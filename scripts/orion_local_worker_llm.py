from __future__ import annotations

import base64
import http.client
import json
import logging
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterator, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from server_modules.agent_turn import (
    AgentTurnRequest,
    bind_agent_turn_metadata,
    resolve_agent_turn_request,
)
from server_modules import usage_accounting_service
from server_modules import internal_tool_markup_service
from server_modules.usage_reporting import build_usage_record

SUPPORTED_PROVIDERS = (
    "codex_cli",
    "claude_code_cli",
    "openai",
    "anthropic",
    "gemini",
    "ollama",
    "ollama_cloud",
    "qwen",
    "deepseek",
    "mistral",
    "groq",
    "openrouter",
    "azure_openai",
    "custom_openai_compatible",
)

BYOK_FIRST_OPENAI_COMPATIBLE_PROVIDERS = {
    "groq",
    "openrouter",
    "azure_openai",
    "custom_openai_compatible",
}
LOCAL_CLI_AUTH_MODES = {"local_cli", "local_subscription", "subscription_cli", "claude_code_cli"}
AUTH_SCOPE_ERROR_MARKERS = (
    "api.responses.write",
    "missing scopes",
    "missing required scope",
    "insufficient scope",
    "insufficient permissions",
)
DIRECT_CHAT_TRANSPORT_UNAVAILABLE = "direct_chat_transport_unavailable"
OPENAI_API_KEY_MISSING_ERROR = "No OpenAI API key configured. Add one from the AI accounts page."
OPENAI_CODEX_DIRECT_AUTH_ERROR = (
    "This is a Codex OAuth token. Use openai-codex provider or set a direct OpenAI API key."
)
CLAUDE_CODE_CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"
CLAUDE_CODE_KEYCHAIN_SERVICE = "Claude Code-credentials"
LOGGER = logging.getLogger(__name__)
ANTHROPIC_DEFAULT_MODEL_FALLBACK = "claude-3-7-sonnet-20250219"
ANTHROPIC_RETIRED_MODEL_ALIASES = {
    "claude-3-7-sonnet-latest",
    "claude-3-5-sonnet-20241022",
}
_RETRY_AFTER_RE = re.compile(r"(?:retry[-_ ]after)\D*(\d+)", re.IGNORECASE)
_RETRY_SECONDS_RE = re.compile(r"(\d+)\s*(?:s|sec|secs|second|seconds)\b", re.IGNORECASE)
def extract_dsml_tool_calls_from_text(value: Any) -> Tuple[str, List[Dict[str, Any]]]:
    """Convert Codex DSML tool markup in output text into internal tool calls.

    Codex transport should usually emit real function_call items. Some local
    subscription paths can instead place the DSML tool-call envelope in visible
    output text. That text must never be rendered to users.
    """
    return internal_tool_markup_service.extract_trusted_dsml_tool_calls(
        value,
        source="orion_local_worker_llm",
    )


def ensure_trailing_slashless(url: str) -> str:
    return str(url or "").rstrip("/")


def to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _provider_limit_policy(provider: str, model: Optional[str] = None) -> Dict[str, Any]:
    defaults = {
        "provider": str(provider or "").strip().lower(),
        "max_output_tokens": 1200,
        "max_retry_attempts": 1,
        "backoff_base_seconds": 1.0,
        "backoff_max_seconds": 4.0,
        "supports_retry_after": True,
    }
    try:
        from server_modules import provider_profiles as _provider_profiles

        policy = _provider_profiles.provider_limit_policy(provider, model)
        merged = dict(defaults)
        merged.update(policy if isinstance(policy, dict) else {})
        return merged
    except Exception:
        return defaults


def _env_max_tokens_override() -> Optional[int]:
    raw = str(os.getenv("ORION_LOCAL_WORKER_MAX_TOKENS") or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except Exception:
        return None
    return value if value > 0 else None


def _resolved_output_token_cap(provider: str, model: Optional[str]) -> int:
    policy = _provider_limit_policy(provider, model)
    cap = max(256, to_int(policy.get("max_output_tokens"), 1200))
    env_override = _env_max_tokens_override()
    if env_override is not None:
        cap = max(256, min(cap, env_override))
    return cap


def _parse_retry_after_seconds(error_text: Any) -> Optional[float]:
    normalized = " ".join(str(error_text or "").strip().split())
    if not normalized:
        return None
    match = _RETRY_AFTER_RE.search(normalized)
    if match:
        value = to_float(match.group(1), 0.0)
        return value if value > 0 else None
    retry_seconds_match = _RETRY_SECONDS_RE.search(normalized)
    if retry_seconds_match and ("rate limit" in normalized.lower() or "http_429" in normalized.lower()):
        value = to_float(retry_seconds_match.group(1), 0.0)
        return value if value > 0 else None
    return None


def _is_provider_rate_limited(error_text: Any) -> bool:
    normalized = str(error_text or "").strip().lower()
    if not normalized:
        return False
    return (
        "http_429" in normalized
        or "rate limit" in normalized
        or "too many requests" in normalized
    )


def _should_retry_provider_error(
    provider: str,
    error_text: Any,
    *,
    attempt_index: int,
    max_attempts: int,
) -> bool:
    if attempt_index + 1 >= max_attempts:
        return False
    if _is_provider_rate_limited(error_text):
        return True
    return is_retryable_provider_transport_error(error_text)


def _provider_retry_delay_seconds(
    provider: str,
    error_text: Any,
    *,
    attempt_index: int,
    model: Optional[str] = None,
) -> float:
    policy = _provider_limit_policy(provider, model)
    if bool(policy.get("supports_retry_after", True)):
        retry_after = _parse_retry_after_seconds(error_text)
        if retry_after is not None:
            return max(0.0, retry_after)
    base = max(0.1, to_float(policy.get("backoff_base_seconds"), 1.0))
    max_backoff = max(base, to_float(policy.get("backoff_max_seconds"), base))
    return min(max_backoff, base * (2 ** max(0, attempt_index)))


def _sleep_provider_retry_delay(
    provider: str,
    error_text: Any,
    *,
    attempt_index: int,
    model: Optional[str] = None,
) -> None:
    delay_seconds = _provider_retry_delay_seconds(
        provider,
        error_text,
        attempt_index=attempt_index,
        model=model,
    )
    if delay_seconds <= 0:
        return
    LOGGER.info(
        "Provider retry backoff: provider=%s attempt=%s delay_seconds=%.2f",
        provider,
        attempt_index + 1,
        delay_seconds,
    )
    time.sleep(delay_seconds)


def parse_json_object_loose(text: str) -> Optional[Dict[str, Any]]:
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    candidate = raw[start : end + 1]
    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        return None
    return None


def _normalize_prior_messages(
    prior_messages: Any,
    *,
    assistant_role: str = "assistant",
) -> List[Dict[str, str]]:
    normalized: List[Dict[str, str]] = []
    if not isinstance(prior_messages, list):
        return normalized
    allowed_roles = {"user", assistant_role}
    for item in prior_messages:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role == "assistant":
            role = assistant_role
        if role not in allowed_roles:
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        normalized.append({"role": role, "content": content})
    return normalized


def _build_chat_messages(
    user_prompt: str,
    *,
    prior_messages: Any = None,
    assistant_role: str = "assistant",
) -> List[Dict[str, str]]:
    messages = _normalize_prior_messages(prior_messages, assistant_role=assistant_role)
    content = str(user_prompt or "").strip()
    if content:
        messages.append({"role": "user", "content": content})
    return messages


def _build_responses_input(
    user_prompt: str,
    *,
    prior_messages: Any = None,
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for message in _build_chat_messages(user_prompt, prior_messages=prior_messages):
        role = message["role"]
        content_type = "output_text" if role == "assistant" else "input_text"
        items.append(
            {
                "role": role,
                "content": [
                    {
                        "type": content_type,
                        "text": message["content"],
                    }
                ],
            }
        )
    return items


def _tool_arguments_json(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return "{}"
    return "{}"


def _tool_arguments_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        parsed = parse_json_object_loose(value)
        if isinstance(parsed, dict):
            return parsed
    return {}


def _tool_spec_items(tools: Any) -> List[Dict[str, Any]]:
    specs: List[Dict[str, Any]] = []
    if not isinstance(tools, list):
        return specs
    for item in tools:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        parameters = item.get("parameters") if isinstance(item.get("parameters"), dict) else None
        if not name or not parameters:
            continue
        specs.append(
            {
                "name": name,
                "description": str(item.get("description") or "").strip(),
                "parameters": parameters,
            }
        )
    return specs


def _normalize_openai_tool_calls(raw_tool_calls: Any) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    if not isinstance(raw_tool_calls, list):
        return normalized
    for item in raw_tool_calls:
        if not isinstance(item, dict):
            continue
        function_payload = item.get("function") if isinstance(item.get("function"), dict) else {}
        tool_name = str(
            function_payload.get("name")
            or item.get("name")
            or ""
        ).strip()
        if not tool_name:
            continue
        normalized.append(
            {
                "id": str(item.get("id") or "").strip() or None,
                "name": tool_name,
                "arguments": function_payload.get("arguments") if "arguments" in function_payload else item.get("arguments"),
            }
        )
    return normalized


def _normalize_openai_function_call(message: Any) -> List[Dict[str, Any]]:
    if not isinstance(message, dict):
        return []
    tool_calls = _normalize_openai_tool_calls(message.get("tool_calls"))
    if tool_calls:
        return tool_calls
    function_call = message.get("function_call") if isinstance(message.get("function_call"), dict) else None
    if not function_call:
        return []
    tool_name = str(function_call.get("name") or "").strip()
    if not tool_name:
        return []
    return [
        {
            "id": None,
            "name": tool_name,
            "arguments": function_call.get("arguments"),
        }
    ]


def _extract_openai_message_text(message: Any) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if text:
                parts.append(text)
        return "\n".join(parts).strip()
    return ""


def _build_openai_compatible_messages(
    user_prompt: str,
    *,
    prior_messages: Any = None,
) -> List[Dict[str, Any]]:
    messages: List[Dict[str, Any]] = []
    if isinstance(prior_messages, list):
        for item in prior_messages:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip().lower()
            if role == "tool":
                content = str(item.get("content") or "").strip()
                tool_call_id = str(item.get("tool_call_id") or "").strip()
                if content and tool_call_id:
                    tool_message = {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": content,
                    }
                    name = str(item.get("name") or "").strip()
                    if name:
                        tool_message["name"] = name
                    messages.append(tool_message)
                continue
            if role not in {"system", "user", "assistant"}:
                continue
            content = str(item.get("content") or "").strip()
            tool_calls = _normalize_openai_tool_calls(item.get("tool_calls"))
            if role == "assistant" and tool_calls:
                assistant_message: Dict[str, Any] = {"role": "assistant", "tool_calls": []}
                if content:
                    assistant_message["content"] = content
                for tool_call in tool_calls:
                    assistant_message["tool_calls"].append(
                        {
                            "id": str(tool_call.get("id") or "").strip() or f"toolcall_{len(assistant_message['tool_calls']) + 1}",
                            "type": "function",
                            "function": {
                                "name": str(tool_call.get("name") or "").strip(),
                                "arguments": _tool_arguments_json(tool_call.get("arguments")),
                            },
                        }
                    )
                if assistant_message["tool_calls"] or content:
                    messages.append(assistant_message)
                continue
            if content:
                messages.append({"role": role, "content": content})
    content = str(user_prompt or "").strip()
    if content:
        messages.append({"role": "user", "content": content})
    return messages


def _build_anthropic_messages(
    user_prompt: str,
    *,
    prior_messages: Any = None,
) -> List[Dict[str, Any]]:
    messages: List[Dict[str, Any]] = []
    if isinstance(prior_messages, list):
        for item in prior_messages:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip().lower()
            if role == "tool":
                content = str(item.get("content") or "").strip()
                tool_call_id = str(item.get("tool_call_id") or "").strip()
                if content and tool_call_id:
                    messages.append(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": tool_call_id,
                                    "content": content,
                                }
                            ],
                        }
                    )
                continue
            if role not in {"user", "assistant"}:
                continue
            text = str(item.get("content") or "").strip()
            tool_calls = _normalize_openai_tool_calls(item.get("tool_calls"))
            if role == "assistant" and tool_calls:
                blocks: List[Dict[str, Any]] = []
                if text:
                    blocks.append({"type": "text", "text": text})
                for tool_call in tool_calls:
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": str(tool_call.get("id") or "").strip() or f"tooluse_{len(blocks)}",
                            "name": str(tool_call.get("name") or "").strip(),
                            "input": _tool_arguments_dict(tool_call.get("arguments")),
                        }
                    )
                if blocks:
                    messages.append({"role": "assistant", "content": blocks})
                continue
            if text:
                messages.append({"role": role, "content": text})
    content = str(user_prompt or "").strip()
    if content:
        messages.append({"role": "user", "content": content})
    return messages


def _build_gemini_contents(
    user_prompt: str,
    *,
    prior_messages: Any = None,
) -> List[Dict[str, Any]]:
    contents: List[Dict[str, Any]] = []
    if isinstance(prior_messages, list):
        for item in prior_messages:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip().lower()
            if role == "tool":
                content = str(item.get("content") or "").strip()
                tool_name = str(item.get("name") or "").strip()
                if content and tool_name:
                    contents.append(
                        {
                            "role": "user",
                            "parts": [
                                {
                                    "functionResponse": {
                                        "name": tool_name,
                                        "response": {"content": content},
                                    }
                                }
                            ],
                        }
                    )
                continue
            role_token = "model" if role == "assistant" else "user"
            if role_token not in {"model", "user"}:
                continue
            text = str(item.get("content") or "").strip()
            tool_calls = _normalize_openai_tool_calls(item.get("tool_calls"))
            if role_token == "model" and tool_calls:
                parts: List[Dict[str, Any]] = []
                if text:
                    parts.append({"text": text})
                for tool_call in tool_calls:
                    parts.append(
                        {
                            "functionCall": {
                                "name": str(tool_call.get("name") or "").strip(),
                                "args": _tool_arguments_dict(tool_call.get("arguments")),
                            }
                        }
                    )
                if parts:
                    contents.append({"role": "model", "parts": parts})
                continue
            if text:
                contents.append({"role": role_token, "parts": [{"text": text}]})
    content = str(user_prompt or "").strip()
    if content:
        contents.append({"role": "user", "parts": [{"text": content}]})
    return contents


def _normalize_anthropic_tool_calls(content_blocks: Any) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    if not isinstance(content_blocks, list):
        return normalized
    for block in content_blocks:
        if not isinstance(block, dict):
            continue
        if str(block.get("type") or "").strip() != "tool_use":
            continue
        tool_name = str(block.get("name") or "").strip()
        if not tool_name:
            continue
        normalized.append(
            {
                "id": str(block.get("id") or "").strip() or None,
                "name": tool_name,
                "arguments": block.get("input") if isinstance(block.get("input"), dict) else {},
            }
        )
    return normalized


def _extract_anthropic_text(content_blocks: Any) -> str:
    parts: List[str] = []
    if not isinstance(content_blocks, list):
        return ""
    for block in content_blocks:
        if not isinstance(block, dict):
            continue
        if str(block.get("type") or "").strip() != "text":
            continue
        text = str(block.get("text") or "").strip()
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def _normalize_gemini_tool_calls(parts: Any) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    if not isinstance(parts, list):
        return normalized
    for part in parts:
        if not isinstance(part, dict):
            continue
        function_call = part.get("functionCall") if isinstance(part.get("functionCall"), dict) else None
        if not function_call:
            continue
        tool_name = str(function_call.get("name") or "").strip()
        if not tool_name:
            continue
        normalized.append(
            {
                "id": None,
                "name": tool_name,
                "arguments": function_call.get("args") if isinstance(function_call.get("args"), dict) else {},
            }
        )
    return normalized


def _extract_gemini_text(parts: Any) -> str:
    text_parts: List[str] = []
    if not isinstance(parts, list):
        return ""
    for part in parts:
        if not isinstance(part, dict):
            continue
        text = str(part.get("text") or "").strip()
        if text:
            text_parts.append(text)
    return "\n".join(text_parts).strip()


def safe_read_json(path: Path, fallback: Any) -> Any:
    try:
        if not path.exists():
            return fallback
        raw = path.read_text(encoding="utf-8")
        parsed = json.loads(raw)
        return parsed
    except Exception:
        return fallback


def is_auth_scope_error(message: Any) -> bool:
    lowered = str(message or "").strip().lower()
    if not lowered:
        return False
    return any(marker in lowered for marker in AUTH_SCOPE_ERROR_MARKERS)


def sanitize_bearer_token(value: Any) -> str:
    token = str(value or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token


def _jwt_payload(token: str) -> Optional[Dict[str, Any]]:
    raw = sanitize_bearer_token(token)
    parts = raw.split(".")
    if len(parts) != 3:
        return None
    payload = parts[1].strip()
    if not payload:
        return None
    padding = "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode((payload + padding).encode("utf-8")).decode("utf-8")
        parsed = json.loads(decoded)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def codex_account_id_from_token(token: str) -> str:
    payload = _jwt_payload(token)
    if not isinstance(payload, dict):
        return ""
    auth_payload = payload.get("https://api.openai.com/auth")
    if not isinstance(auth_payload, dict):
        return ""
    account_id = str(auth_payload.get("chatgpt_account_id") or "").strip()
    return account_id


def codex_token_from_vault(codex_auth_file: Path) -> str:
    try:
        payload = safe_read_json(codex_auth_file, {})
        if not isinstance(payload, dict):
            return ""
        tokens = payload.get("tokens")
        if not isinstance(tokens, dict):
            return ""
        return sanitize_bearer_token(tokens.get("access_token"))
    except Exception:
        return ""


def codex_cli_available() -> bool:
    return bool(shutil.which("codex"))


def claude_code_cli_available() -> bool:
    return bool(shutil.which("claude"))


def _coerce_epoch_ms(value: Any) -> int:
    try:
        return int(float(value))
    except Exception:
        return 0


def _parse_claude_code_session_token(payload: Any) -> str:
    source = payload.get("claudeAiOauth") if isinstance(payload, dict) else payload
    if not isinstance(source, dict):
        return ""
    token = sanitize_bearer_token(
        source.get("accessToken")
        or source.get("access_token")
        or source.get("token")
        or ""
    )
    if not token:
        return ""
    expires_at = _coerce_epoch_ms(
        source.get("expiresAt")
        or source.get("expires_at")
        or source.get("expires")
        or 0
    )
    if expires_at and expires_at <= int(time.time() * 1000):
        return ""
    return token


def read_claude_code_keychain_token() -> str:
    if not shutil.which("security"):
        return ""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", CLAUDE_CODE_KEYCHAIN_SERVICE, "-w"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return ""
    if result.returncode != 0:
        return ""
    raw = str(result.stdout or "").strip()
    if not raw:
        return ""
    try:
        parsed = json.loads(raw)
    except Exception:
        return ""
    return _parse_claude_code_session_token(parsed)


def read_claude_code_file_token() -> str:
    parsed = safe_read_json(CLAUDE_CODE_CREDENTIALS_PATH, {})
    return _parse_claude_code_session_token(parsed)


def get_claude_code_session_token() -> str:
    return read_claude_code_keychain_token() or read_claude_code_file_token()


def _openai_api_key_disabled() -> bool:
    return str(os.getenv("ORION_DISABLE_OPENAI_API_KEY", "0")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _openai_auth_mode() -> str:
    return str(os.getenv("ORION_AUTH_MODE", "")).strip().lower()


def _first_valid_token(candidates: list[Any]) -> str:
    for raw in candidates:
        token = sanitize_bearer_token(raw)
        if token:
            return token
    return ""


def _openai_oauth_candidates() -> list[Any]:
    return [
        os.getenv("ORION_LOCAL_WORKER_OPENAI_TOKEN"),
        os.getenv("CODEX_OAUTH_TOKEN"),
        os.getenv("OPENAI_OAUTH_TOKEN"),
        os.getenv("OPENAI_ACCESS_TOKEN"),
    ]


def _codex_oauth_candidates() -> list[Any]:
    auth_file = Path(
        os.getenv("CODEX_AUTH_FILE", str(Path.home() / ".codex" / "auth.json"))
    ).expanduser()
    return _openai_oauth_candidates() + [codex_token_from_vault(auth_file)]


def _openai_api_key_candidates() -> list[Any]:
    if _openai_api_key_disabled():
        return []
    return [
        os.getenv("ORION_LOCAL_WORKER_OPENAI_API_KEY"),
        os.getenv("OPENAI_API_KEY"),
    ]


def get_openai_bearer_token() -> str:
    return _first_valid_token(_openai_oauth_candidates())


def get_codex_oauth_token() -> str:
    return _first_valid_token(_codex_oauth_candidates())


def get_openai_api_key() -> str:
    return _first_valid_token(_openai_api_key_candidates())


def get_anthropic_api_key() -> str:
    return (
        os.getenv("ORION_LOCAL_WORKER_ANTHROPIC_API_KEY")
        or os.getenv("ANTHROPIC_API_KEY")
        or ""
    ).strip()


def anthropic_default_model() -> str:
    override = str(os.getenv("ORION_LOCAL_WORKER_ANTHROPIC_MODEL") or "").strip()
    if override:
        return override
    try:
        from server_modules import provider_profiles as _provider_profiles

        catalog_entry = _provider_profiles.provider_catalog_entry("anthropic")
        candidate = str((catalog_entry or {}).get("default_model") or "").strip()
        if candidate:
            return candidate
    except Exception:
        pass
    return ANTHROPIC_DEFAULT_MODEL_FALLBACK


def normalize_anthropic_model(model: Any) -> str:
    token = str(model or "").strip()
    if not token:
        return anthropic_default_model()
    normalized = token.lower()
    if normalized in {"default", "sonnet", "claude", "claude-sonnet"}:
        return anthropic_default_model()
    if token in ANTHROPIC_RETIRED_MODEL_ALIASES:
        return anthropic_default_model()
    return token


def get_gemini_api_key() -> str:
    return (
        os.getenv("ORION_LOCAL_WORKER_GEMINI_API_KEY")
        or os.getenv("GEMINI_API_KEY")
        or os.getenv("GOOGLE_API_KEY")
        or ""
    ).strip()


def get_qwen_api_key() -> str:
    return (
        os.getenv("ORION_LOCAL_WORKER_QWEN_API_KEY")
        or os.getenv("QWEN_API_KEY")
        or os.getenv("DASHSCOPE_API_KEY")
        or ""
    ).strip()


def get_deepseek_api_key() -> str:
    return (
        os.getenv("ORION_LOCAL_WORKER_DEEPSEEK_API_KEY")
        or os.getenv("DEEPSEEK_API_KEY")
        or ""
    ).strip()


def get_mistral_api_key() -> str:
    return (
        os.getenv("ORION_LOCAL_WORKER_MISTRAL_API_KEY")
        or os.getenv("MISTRAL_API_KEY")
        or ""
    ).strip()


def get_groq_api_key() -> str:
    return (
        os.getenv("ORION_LOCAL_WORKER_GROQ_API_KEY")
        or os.getenv("GROQ_API_KEY")
        or ""
    ).strip()


def get_openrouter_api_key() -> str:
    return (
        os.getenv("ORION_LOCAL_WORKER_OPENROUTER_API_KEY")
        or os.getenv("OPENROUTER_API_KEY")
        or ""
    ).strip()


def get_azure_openai_api_key() -> str:
    return (
        os.getenv("ORION_LOCAL_WORKER_AZURE_OPENAI_API_KEY")
        or os.getenv("AZURE_OPENAI_API_KEY")
        or ""
    ).strip()


def get_custom_openai_compatible_api_key() -> str:
    return (
        os.getenv("ORION_LOCAL_WORKER_CUSTOM_OPENAI_COMPATIBLE_API_KEY")
        or os.getenv("CUSTOM_OPENAI_COMPATIBLE_API_KEY")
        or ""
    ).strip()


def get_ollama_cloud_api_key() -> str:
    return (
        os.getenv("ORION_LOCAL_WORKER_OLLAMA_CLOUD_API_KEY")
        or os.getenv("OLLAMA_API_KEY")
        or ""
    ).strip()


def ollama_service_status() -> Dict[str, Any]:
    base_url = ensure_trailing_slashless(os.getenv("ORION_LOCAL_WORKER_OLLAMA_URL") or "http://127.0.0.1:11434")
    req = urllib.request.Request(
        url=f"{base_url}/api/tags",
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except Exception:
        return {"reachable": False, "has_models": False, "models": []}
    parsed = parse_json_object_loose(raw) or {}
    items = parsed.get("models") if isinstance(parsed.get("models"), list) else []
    models: List[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("model") or "").strip()
        if name:
            models.append(name)
    return {
        "reachable": True,
        "has_models": bool(models),
        "models": sorted(set(models)),
    }


def ollama_enabled() -> bool:
    raw = str(os.getenv("ORION_LOCAL_WORKER_OLLAMA_ENABLED", "0")).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    status = ollama_service_status()
    return bool(status.get("reachable") and status.get("has_models"))


def provider_has_key(provider: str) -> bool:
    pid = str(provider or "").strip().lower()
    if pid == "codex_cli":
        return codex_cli_available()
    if pid == "claude_code_cli":
        return claude_code_cli_available()
    if pid == "openai":
        return bool(get_openai_api_key())
    if pid == "anthropic":
        return bool(get_anthropic_api_key())
    if pid == "gemini":
        return bool(get_gemini_api_key())
    if pid == "ollama":
        return ollama_enabled()
    if pid == "ollama_cloud":
        return bool(get_ollama_cloud_api_key())
    if pid == "qwen":
        return bool(get_qwen_api_key())
    if pid == "deepseek":
        return bool(get_deepseek_api_key())
    if pid == "mistral":
        return bool(get_mistral_api_key())
    return False


def requested_auth_mode(context: Dict[str, Any], metadata: Dict[str, Any]) -> str:
    inline_credentials = metadata.get("credentials") if isinstance(metadata.get("credentials"), dict) else {}
    return str(
        inline_credentials.get("auth_mode")
        or inline_credentials.get("authMode")
        or metadata.get("auth_mode")
        or context.get("auth_mode")
        or ""
    ).strip().lower()


def openai_direct_auth_error(context: Dict[str, Any], metadata: Dict[str, Any]) -> str:
    inline_credentials = metadata.get("credentials") if isinstance(metadata.get("credentials"), dict) else {}
    if requested_auth_mode(context, metadata) == "oauth_token":
        return OPENAI_CODEX_DIRECT_AUTH_ERROR
    oauth_token = sanitize_bearer_token(
        inline_credentials.get("oauth_token")
        or inline_credentials.get("oauthToken")
        or metadata.get("oauth_token")
    )
    if oauth_token:
        return OPENAI_CODEX_DIRECT_AUTH_ERROR
    return ""


def should_use_openai_chat_completions(context: Dict[str, Any], metadata: Dict[str, Any]) -> bool:
    requested_mode = requested_auth_mode(context, metadata)
    if requested_mode == "oauth_token":
        return True
    run_source = str(metadata.get("source") or context.get("source") or "").strip().lower()
    if run_source == "chat_direct":
        return False
    return _openai_auth_mode() == "codex"


def resolve_requested_provider(context: Dict[str, Any], metadata: Dict[str, Any]) -> str:
    raw_provider = str(context.get("provider") or metadata.get("provider") or "").strip().lower()
    auth_mode = requested_auth_mode(context, metadata)
    if raw_provider == "claude_code_cli":
        return "claude_code_cli"
    if raw_provider == "openai-codex":
        return "codex_cli"
    if raw_provider == "anthropic" and auth_mode in LOCAL_CLI_AUTH_MODES:
        if provider_has_usable_credentials("anthropic", context, metadata):
            return "anthropic"
        if provider_has_usable_credentials("claude_code_cli", context, metadata):
            return "claude_code_cli"
    return raw_provider


def _deepseek_default_model() -> str:
    """The one place this file decides "what DeepSeek model, if nobody
    asked for a specific one" — provider_profiles.py's own "deepseek"
    catalog entry (default_model) is the single source of truth, never a
    second hardcoded copy. Three separate copies of this fallback existed
    here before this fix, ALL still returning DeepSeek's retired
    "deepseek-chat" id (retired 2026-07-24) — discovered by a full-path
    integration test that ran the real resolution chain rather than
    mocking it (test_default_engine_credit_debit.py's
    FullPathRealDefaultEngineIntegrationTest). "deepseek-v4-flash" is the
    final fallback only if the catalog lookup itself fails (should not
    happen — defensive, not a second source of truth)."""
    try:
        from server_modules import provider_profiles as _provider_profiles

        catalog_default = str(
            _provider_profiles.provider_catalog_entry("deepseek").get("default_model") or ""
        ).strip()
    except Exception:
        catalog_default = ""
    return (
        os.getenv("ORION_LOCAL_WORKER_DEEPSEEK_MODEL") or catalog_default or "deepseek-v4-flash"
    ).strip() or "deepseek-v4-flash"


def _deepseek_model_is_known(model: str) -> bool:
    """Is `model` one of DeepSeek's real, current, selectable model ids?
    Delegates to provider_profiles.model_is_known_for_provider — the SAME
    save-time gate fleet_tools.configure_agent uses — rather than a second,
    hand-kept name set. Also see that function's own docstring on why it
    deliberately does NOT resolve "deepseek-chat"/"deepseek-reasoner"
    forward: recognizing the retired names here as still-valid would put
    them right back into circulation the moment any caller checked
    membership before choosing whether to send a model string as-is."""
    try:
        from server_modules import provider_profiles as _provider_profiles

        return _provider_profiles.model_is_known_for_provider("deepseek", model)
    except Exception:
        return False


def resolve_requested_model(context: Dict[str, Any], metadata: Dict[str, Any], provider: str = "") -> str:
    requested = str(
        context.get("model")
        or metadata.get("model")
        or metadata.get("requested_model")
        or ""
    ).strip()
    if requested:
        return requested
    pid = str(provider or resolve_requested_provider(context, metadata) or "").strip().lower()
    if pid == "codex_cli":
        return (
            os.getenv("ORION_LOCAL_WORKER_CODEX_MODEL")
            or os.getenv("CODEX_MODEL")
            or "gpt-5.4"
        ).strip() or "gpt-5.4"
    if pid == "openai":
        return (os.getenv("ORION_LOCAL_WORKER_OPENAI_MODEL") or os.getenv("CODEX_MODEL") or "gpt-4.1").strip() or "gpt-4.1"
    if pid == "claude_code_cli":
        return (os.getenv("ORION_LOCAL_WORKER_CLAUDE_CODE_MODEL") or "sonnet").strip() or "sonnet"
    if pid == "anthropic":
        return anthropic_default_model()
    if pid == "gemini":
        return (os.getenv("ORION_LOCAL_WORKER_GEMINI_MODEL") or "gemini-2.0-flash").strip() or "gemini-2.0-flash"
    if pid == "ollama":
        return (os.getenv("ORION_LOCAL_WORKER_OLLAMA_MODEL") or "llama3.1:8b").strip() or "llama3.1:8b"
    if pid == "ollama_cloud":
        return (os.getenv("ORION_LOCAL_WORKER_OLLAMA_CLOUD_MODEL") or "gpt-oss:120b").strip() or "gpt-oss:120b"
    if pid == "qwen":
        return (os.getenv("ORION_LOCAL_WORKER_QWEN_MODEL") or "qwen-turbo").strip() or "qwen-turbo"
    if pid == "deepseek":
        return _deepseek_default_model()
    if pid == "mistral":
        return (os.getenv("ORION_LOCAL_WORKER_MISTRAL_MODEL") or "mistral-small-latest").strip() or "mistral-small-latest"
    return ""


def default_codex_model() -> str:
    return (
        os.getenv("ORION_LOCAL_WORKER_CODEX_MODEL")
        or os.getenv("CODEX_MODEL")
        or "gpt-5.4"
    ).strip() or "gpt-5.4"


def codex_cli_supports_model(model: Any) -> bool:
    normalized = str(model or "").strip().lower()
    if not normalized:
        return False
    return normalized.startswith("gpt-5") or "codex" in normalized


def coerce_requested_model_for_provider(requested_model: Any, provider: str) -> str:
    model = str(requested_model or "").strip()
    pid = str(provider or "").strip().lower()
    if pid == "codex_cli":
        return model if codex_cli_supports_model(model) else default_codex_model()
    if pid == "claude_code_cli":
        return normalize_anthropic_model(model)
    if pid == "anthropic":
        return normalize_anthropic_model(model)
    if pid == "deepseek":
        # BEFORE this fix: backwards. The old whitelist was exactly the
        # two RETIRED names ({"deepseek-chat", "deepseek-reasoner"}), so a
        # caller explicitly requesting a real current id ("deepseek-v4-
        # flash"/"deepseek-v4-pro") had it silently DISCARDED and replaced
        # with the default, while a caller carrying the dead name had it
        # preserved untouched. _deepseek_model_is_known checks against the
        # real, current catalog (provider_profiles.py), so a valid
        # explicit choice now survives and a retired one is coerced to the
        # real default instead of being passed through.
        return model if _deepseek_model_is_known(model) else default_openai_compatible_model("deepseek")
    if pid == "ollama_cloud":
        return model or ((os.getenv("ORION_LOCAL_WORKER_OLLAMA_CLOUD_MODEL") or "gpt-oss:120b").strip() or "gpt-oss:120b")
    return model


def _inline_credentials(metadata: Dict[str, Any]) -> Dict[str, Any]:
    payload = metadata.get("credentials") if isinstance(metadata.get("credentials"), dict) else {}
    return payload if isinstance(payload, dict) else {}


def resolve_anthropic_api_key(credential_override: Optional[Dict[str, Any]] = None) -> str:
    override = credential_override if isinstance(credential_override, dict) else {}
    auth_mode = str(
        override.get("auth_mode")
        or override.get("authMode")
        or ""
    ).strip().lower()
    direct_token = sanitize_bearer_token(
        override.get("api_key")
        or override.get("access_token")
        or override.get("oauth_token")
        or override.get("token")
        or ""
    )
    if direct_token:
        return direct_token
    if auth_mode in LOCAL_CLI_AUTH_MODES:
        return get_claude_code_session_token()
    return get_anthropic_api_key()


def resolve_gemini_api_key(credential_override: Optional[Dict[str, Any]] = None) -> str:
    override = credential_override if isinstance(credential_override, dict) else {}
    inline_key = sanitize_bearer_token(
        override.get("api_key")
        or override.get("access_token")
        or override.get("oauth_token")
        or override.get("token")
        or ""
    )
    if inline_key:
        return inline_key
    return get_gemini_api_key()


def provider_has_usable_credentials(provider: str, context: Dict[str, Any], metadata: Dict[str, Any]) -> bool:
    pid = str(provider or "").strip().lower()
    inline_credentials = _inline_credentials(metadata)
    auth_mode = requested_auth_mode(context, metadata)
    if pid == "codex_cli":
        inline_token = sanitize_bearer_token(
            inline_credentials.get("oauth_token")
            or inline_credentials.get("access_token")
            or ""
        )
        return bool(inline_token) or provider_has_key("codex_cli")
    if pid == "claude_code_cli":
        return bool(get_claude_code_session_token())
    if pid == "openai":
        inline_key = sanitize_bearer_token(inline_credentials.get("api_key") or "")
        return bool(inline_key) or provider_has_key("openai")
    if pid == "anthropic":
        if auth_mode in LOCAL_CLI_AUTH_MODES:
            return bool(get_claude_code_session_token())
        inline_key = sanitize_bearer_token(
            inline_credentials.get("api_key")
            or inline_credentials.get("access_token")
            or inline_credentials.get("oauth_token")
            or inline_credentials.get("token")
            or ""
        )
        return bool(inline_key) or provider_has_key("anthropic")
    if pid == "gemini":
        inline_key = sanitize_bearer_token(inline_credentials.get("api_key") or "")
        return bool(inline_key) or provider_has_key("gemini")
    if pid in {"qwen", "deepseek", "mistral", "ollama_cloud"}:
        inline_key = sanitize_bearer_token(
            inline_credentials.get("api_key")
            or inline_credentials.get("access_token")
            or inline_credentials.get("oauth_token")
            or inline_credentials.get("token")
            or ""
        )
        return bool(inline_key) or provider_has_key(pid)
    return provider_has_key(pid)


def resolve_requested_reasoning_effort(context: Dict[str, Any], metadata: Dict[str, Any]) -> str:
    requested = str(
        context.get("reasoning_effort")
        or metadata.get("reasoning_effort")
        or metadata.get("requested_reasoning_effort")
        or ""
    ).strip().lower()
    if requested in {"low", "medium", "high", "xhigh"}:
        return requested
    return ""


def resolve_requested_tools(context: Dict[str, Any], metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw_tools = metadata.get("tools")
    if not isinstance(raw_tools, list):
        raw_tools = context.get("tools")
    resolved: List[Dict[str, Any]] = []
    if not isinstance(raw_tools, list):
        return resolved
    for item in raw_tools:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        description = str(item.get("description") or "").strip()
        parameters = item.get("parameters") if isinstance(item.get("parameters"), dict) else None
        if not name or not parameters:
            continue
        resolved.append(
            {
                "name": name,
                "description": description,
                "parameters": parameters,
            }
        )
    return resolved


def format_provider_error(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        status = getattr(exc, "code", None)
        body = ""
        try:
            body = exc.read().decode("utf-8", "ignore")
        except Exception:
            body = ""
        body = " ".join(body.strip().split()) if body else ""
        if len(body) > 320:
            body = body[:320] + "..."
        if status is not None:
            return f"http_{status}: {body or str(exc.reason)}"
        return body or str(exc)
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", None)
        return f"url_error: {reason or str(exc)}"
    return str(exc)


def _curl_json_request(
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    payload: Optional[Dict[str, Any]] = None,
    timeout_seconds: int = 45,
) -> Dict[str, Any]:
    command = [
        "curl",
        "-sS",
        "-L",
        "-X",
        "POST" if payload is not None else "GET",
        "--max-time",
        str(max(1, int(timeout_seconds))),
        "-w",
        "\n__CODEX_STATUS__:%{http_code}",
    ]
    for key, value in dict(headers or {}).items():
        command.extend(["-H", f"{key}: {value}"])
    body: Optional[bytes] = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        command.extend(["--data-binary", "@-"])
    command.append(url)
    completed = subprocess.run(
        command,
        input=body,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(stderr or f"curl request failed for {url}")
    raw_output = completed.stdout.decode("utf-8", errors="replace")
    marker = "\n__CODEX_STATUS__:"
    body_text, _, status_text = raw_output.rpartition(marker)
    if not status_text:
        body_text = raw_output
        status_code = 200
    else:
        try:
            status_code = int(status_text.strip() or "0")
        except Exception:
            status_code = 0
    parsed = parse_json_object_loose(body_text)
    return {
        "status": status_code,
        "raw": body_text,
        "json": parsed if isinstance(parsed, dict) else {},
    }


def _openai_compatible_chat_completion_request(
    *,
    base_url: str,
    api_key: str,
    payload: Dict[str, Any],
    timeout_seconds: int,
    query_params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    url = f"{ensure_trailing_slashless(base_url)}/chat/completions"
    query = {
        str(key): str(value)
        for key, value in dict(query_params or {}).items()
        if str(key or "").strip() and str(value or "").strip()
    }
    if query:
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}{urllib.parse.urlencode(query)}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        "Connection": "close",
    }
    req = urllib.request.Request(
        url=url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
            try:
                raw_body = response.read()
            except http.client.IncompleteRead as exc:
                # A partial chat-completions JSON document is unsafe to parse.
                # Fall through to the curl transport fallback below.
                raise exc
            raw = raw_body.decode("utf-8")
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception as exc:
        error_text = format_provider_error(exc)
        if not is_retryable_provider_transport_error(error_text) or not shutil.which("curl"):
            raise
        response = _curl_json_request(
            url,
            headers=headers,
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
        status = int(response.get("status") or 0)
        if status >= 400:
            raw = " ".join(str(response.get("raw") or "").strip().split())
            if len(raw) > 320:
                raw = raw[:320] + "..."
            raise RuntimeError(f"http_{status}: {raw or 'request_failed'}")
        parsed = response.get("json") if isinstance(response.get("json"), dict) else {}
        if not parsed:
            raw = str(response.get("raw") or "").strip()
            parsed = parse_json_object_loose(raw) or {}
        return parsed


def _anthropic_messages_request(
    *,
    api_url: str,
    api_key: str,
    payload: Dict[str, Any],
    timeout_seconds: int,
) -> Dict[str, Any]:
    url = f"{api_url}/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(
        url=url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
            return {
                "status": int(getattr(response, "status", response.getcode())),
                "raw": raw,
                "json": json.loads(raw),
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
        return {
            "status": int(getattr(exc, "code", 500)),
            "raw": raw,
            "json": parse_json_object_loose(raw) or {},
        }
    except Exception:
        if not shutil.which("curl"):
            raise
        return _curl_json_request(url, headers=headers, payload=payload, timeout_seconds=timeout_seconds)


def _anthropic_response_error(response: Dict[str, Any]) -> str:
    parsed = response.get("json") if isinstance(response.get("json"), dict) else {}
    error_payload = parsed.get("error") if isinstance(parsed, dict) else None
    if isinstance(error_payload, dict):
        message = str(error_payload.get("message") or "").strip()
        if message:
            return message
    raw = str(response.get("raw") or "").strip()
    if raw:
        return raw
    return f"anthropic_http_{response.get('status') or 0}"


def is_retryable_provider_transport_error(error_text: Any) -> bool:
    normalized = str(error_text or "").strip().lower()
    if not normalized:
        return False
    return (
        "incomplete read" in normalized
        or "incompleteread" in normalized
        or "connection reset" in normalized
        or "remote end closed" in normalized
        or "service unavailable" in normalized
        or "http_429" in normalized
        or "http_500" in normalized
        or "http_502" in normalized
        or "http_503" in normalized
        or "http_504" in normalized
    )


def _gemini_generate_content_request(
    *,
    api_url: str,
    model: str,
    api_key: str,
    payload: Dict[str, Any],
    timeout_seconds: int,
) -> Dict[str, Any]:
    url = f"{api_url}/models/{urllib.parse.quote_plus(model)}:generateContent?key={urllib.parse.quote_plus(api_key)}"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        "Connection": "close",
    }
    req = urllib.request.Request(
        url=url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception as exc:
        error_text = format_provider_error(exc)
        if not is_retryable_provider_transport_error(error_text) or not shutil.which("curl"):
            raise
        response = _curl_json_request(
            url,
            headers=headers,
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
        status = int(response.get("status") or 0)
        if status >= 400:
            raw = " ".join(str(response.get("raw") or "").strip().split())
            if len(raw) > 320:
                raw = raw[:320] + "..."
            raise RuntimeError(f"http_{status}: {raw or 'request_failed'}")
        parsed = response.get("json") if isinstance(response.get("json"), dict) else {}
        if not parsed:
            raw = str(response.get("raw") or "").strip()
            parsed = parse_json_object_loose(raw) or {}
        return parsed if isinstance(parsed, dict) else {}


def compact_tool_mode_system_prompt(system_prompt: Optional[str]) -> Optional[str]:
    prompt = str(system_prompt or "").strip()
    runtime_identity = ""
    if "## Runtime Identity" in prompt:
        runtime_start = prompt.find("## Runtime Identity")
        runtime_identity = prompt[runtime_start:].strip()

    sections = [
        (
            "You are Sage.\n"
            "Use the provided API tool definitions when they match the request.\n"
            "Do not claim you lack filesystem, browser, desktop, clipboard, shell, or web access if a matching tool is available.\n"
            "For local machine requests like listing desktop files, reading local files, taking screenshots, or running terminal commands, call the matching tool before answering."
        )
    ]
    if runtime_identity:
        sections.append(runtime_identity)
    compacted = "\n\n".join(section.strip() for section in sections if str(section or "").strip()).strip()
    return compacted or system_prompt


def is_invalid_tool_capable_final_answer(final_text: Any, tool_calls: Any) -> bool:
    if isinstance(tool_calls, list) and tool_calls:
        return False
    normalized = " ".join(str(final_text or "").strip().lower().split())
    if not normalized:
        return False
    denial_markers = (
        "i don't have direct access",
        "i do not have direct access",
        "i can't access your",
        "i cannot access your",
        "i can't see or interact with files",
        "i cannot see or interact with files",
        "unless you upload",
        "through file uploads",
    )
    return any(marker in normalized for marker in denial_markers)


def collect_tool_capable_provider_response(events: Iterator[Dict[str, Any]]) -> Dict[str, Any]:
    streamed_parts: List[str] = []
    final_usage: Optional[Dict[str, Any]] = None
    final_model = ""
    tool_calls: List[Dict[str, Any]] = []
    provider_error = "unknown_error"

    for event in events:
        event_type = str(event.get("type") or "").strip().lower()
        if event_type == "delta":
            delta = str(event.get("delta") or "")
            if delta:
                streamed_parts.append(delta)
            final_model = str(event.get("model") or final_model or "").strip() or final_model
            continue
        if event_type == "done":
            final_usage = event.get("usage") if isinstance(event.get("usage"), dict) else None
            final_model = str(event.get("model") or final_model or "").strip() or final_model
            tool_calls = event.get("tool_calls") if isinstance(event.get("tool_calls"), list) else []
            final_text = str(event.get("text") or "").strip() or "".join(streamed_parts).strip()
            if is_invalid_tool_capable_final_answer(final_text, tool_calls):
                provider_error = "tool_capable_provider_refused_available_tools"
                break
            if final_text or tool_calls:
                return {
                    "ok": True,
                    "text": final_text,
                    "deltas": streamed_parts,
                    "usage": final_usage,
                    "model": final_model,
                    "tool_calls": tool_calls,
                    "error": "",
                }
            provider_error = "empty_content"
            break
        if event_type == "error":
            final_model = str(event.get("model") or final_model or "").strip() or final_model
            provider_error = str(event.get("error") or "unknown_error").strip() or "unknown_error"
            break

    print(f"[TOOL_CAPABLE_FAIL] error={provider_error!r} deltas_count={len(streamed_parts)} model={final_model!r}", flush=True)
    return {
        "ok": False,
        "text": "",
        "deltas": streamed_parts,
        "usage": final_usage,
        "model": final_model,
        "tool_calls": [],
        "error": provider_error,
    }


def run_tool_capable_provider_with_prompt_fallback(
    *,
    system_prompt: Optional[str],
    stream_factory: Any,
) -> Dict[str, Any]:
    prompt_variants: List[Optional[str]] = [system_prompt]
    compacted_prompt = compact_tool_mode_system_prompt(system_prompt)
    if compacted_prompt != system_prompt:
        prompt_variants.append(compacted_prompt)

    last_result: Dict[str, Any] = {
        "ok": False,
        "text": "",
        "deltas": [],
        "usage": None,
        "model": "",
        "tool_calls": [],
        "error": "unknown_error",
    }
    for attempt_index, prompt_variant in enumerate(prompt_variants):
        last_result = collect_tool_capable_provider_response(stream_factory(prompt_variant))
        if last_result.get("ok"):
            return last_result
        if attempt_index + 1 >= len(prompt_variants):
            break
        if last_result.get("deltas"):
            if str(last_result.get("error") or "").strip() != "tool_capable_provider_refused_available_tools":
                break
        if not is_retryable_provider_transport_error(last_result.get("error")):
            if str(last_result.get("error") or "").strip() != "tool_capable_provider_refused_available_tools":
                break
    return last_result


def codex_instructions(system_prompt: Optional[str]) -> str:
    return str(system_prompt or "")


def resolve_openai_compatible_api_key(
    provider: str,
    credential_override: Optional[Dict[str, Any]] = None,
) -> str:
    override = credential_override if isinstance(credential_override, dict) else {}
    inline_key = sanitize_bearer_token(
        override.get("api_key")
        or override.get("access_token")
        or override.get("oauth_token")
        or override.get("token")
        or ""
    )
    if inline_key:
        return inline_key
    pid = str(provider or "openai").strip().lower()
    if pid in BYOK_FIRST_OPENAI_COMPATIBLE_PROVIDERS:
        return ""
    if pid == "openai":
        return get_openai_api_key()
    if pid == "qwen":
        return get_qwen_api_key()
    if pid == "deepseek":
        return get_deepseek_api_key()
    if pid == "mistral":
        return get_mistral_api_key()
    if pid == "groq":
        return get_groq_api_key()
    if pid == "openrouter":
        return get_openrouter_api_key()
    if pid == "azure_openai":
        return get_azure_openai_api_key()
    if pid == "custom_openai_compatible":
        return get_custom_openai_compatible_api_key()
    return ""


def resolve_openai_compatible_base_url(
    provider: str,
    credential_override: Optional[Dict[str, Any]] = None,
    model: Any = None,
) -> str:
    override = credential_override if isinstance(credential_override, dict) else {}
    override_base_url = str(override.get("base_url") or "").strip()
    pid = str(provider or "openai").strip().lower()
    if pid == "azure_openai":
        deployment = _azure_openai_deployment_from_override(override, model=model)
        endpoint = str(
            override.get("endpoint")
            or override.get("azure_endpoint")
            or ""
        ).strip()
        if override_base_url and "/openai/deployments/" in override_base_url:
            return ensure_trailing_slashless(override_base_url.rsplit("/chat/completions", 1)[0])
        if endpoint and deployment:
            return ensure_trailing_slashless(
                f"{ensure_trailing_slashless(endpoint)}/openai/deployments/{urllib.parse.quote(deployment, safe='')}"
            )
        if override_base_url:
            return ensure_trailing_slashless(override_base_url)
        return ""
    if override_base_url:
        return ensure_trailing_slashless(override_base_url)
    if pid == "qwen":
        return ensure_trailing_slashless(
            os.getenv("ORION_LOCAL_WORKER_QWEN_URL") or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
    if pid == "deepseek":
        return ensure_trailing_slashless(
            os.getenv("ORION_LOCAL_WORKER_DEEPSEEK_URL") or "https://api.deepseek.com/v1"
        )
    if pid == "mistral":
        return ensure_trailing_slashless(
            os.getenv("ORION_LOCAL_WORKER_MISTRAL_URL") or "https://api.mistral.ai/v1"
        )
    if pid == "groq":
        return ensure_trailing_slashless(
            os.getenv("ORION_LOCAL_WORKER_GROQ_URL") or "https://api.groq.com/openai/v1"
        )
    if pid == "openrouter":
        return ensure_trailing_slashless(
            os.getenv("ORION_LOCAL_WORKER_OPENROUTER_URL") or "https://openrouter.ai/api/v1"
        )
    if pid == "custom_openai_compatible":
        return ""
    return ensure_trailing_slashless(os.getenv("ORION_LOCAL_WORKER_OPENAI_URL") or "https://api.openai.com/v1")


def default_openai_compatible_model(provider: str) -> str:
    pid = str(provider or "openai").strip().lower()
    if pid == "qwen":
        return (os.getenv("ORION_LOCAL_WORKER_QWEN_MODEL") or "qwen-turbo").strip() or "qwen-turbo"
    if pid == "deepseek":
        # This is the resolver openai_compatible_model_from_request (and
        # therefore iter_openai_compatible_chat_events — the function that
        # actually sends a request to DeepSeek's wire) falls back to when
        # nobody supplied an explicit model. See _deepseek_default_model's
        # own docstring for why this used to be "deepseek-chat".
        return _deepseek_default_model()
    if pid == "mistral":
        return (os.getenv("ORION_LOCAL_WORKER_MISTRAL_MODEL") or "mistral-small-latest").strip() or "mistral-small-latest"
    if pid == "groq":
        return (os.getenv("ORION_LOCAL_WORKER_GROQ_MODEL") or "llama-3.3-70b-versatile").strip() or "llama-3.3-70b-versatile"
    if pid == "openrouter":
        return (os.getenv("ORION_LOCAL_WORKER_OPENROUTER_MODEL") or "openai/gpt-5.2").strip() or "openai/gpt-5.2"
    if pid == "azure_openai":
        return ""
    if pid == "custom_openai_compatible":
        return ""
    return (os.getenv("ORION_LOCAL_WORKER_OPENAI_MODEL") or "gpt-4.1").strip() or "gpt-4.1"


def openai_compatible_model_from_request(
    provider: str,
    model_override: Optional[str] = None,
    credential_override: Optional[Dict[str, Any]] = None,
) -> str:
    model = str(model_override or "").strip()
    override = credential_override if isinstance(credential_override, dict) else {}
    pid = str(provider or "openai").strip().lower()
    if not model and pid == "azure_openai":
        model = str(
            override.get("deployment")
            or override.get("deployment_name")
            or override.get("azure_deployment")
            or ""
        ).strip()
    if not model and pid == "custom_openai_compatible":
        model = str(override.get("model") or override.get("model_id") or "").strip()
    return (model or default_openai_compatible_model(provider)).strip()


def openai_compatible_missing_key_error(provider: str) -> str:
    pid = str(provider or "openai").strip().lower()
    if pid == "qwen":
        return "No Qwen API key configured. Add one from the AI accounts page."
    if pid == "deepseek":
        return "No DeepSeek API key configured. Add one from the AI accounts page."
    if pid == "mistral":
        return "No Mistral API key configured. Add one from the AI accounts page."
    if pid == "groq":
        return "No Groq API key configured. Add one from the AI accounts page."
    if pid == "openrouter":
        return "No OpenRouter API key configured. Add one from the AI accounts page."
    if pid == "azure_openai":
        return "No Azure OpenAI API key configured. Add one from the AI accounts page."
    if pid == "custom_openai_compatible":
        return "No custom OpenAI-compatible API key configured. Add one from the AI accounts page."
    return OPENAI_API_KEY_MISSING_ERROR


def openai_compatible_missing_model_error(provider: str) -> str:
    pid = str(provider or "openai").strip().lower()
    if pid == "azure_openai":
        return "Azure OpenAI requires a workspace deployment/model id."
    if pid == "custom_openai_compatible":
        return "Custom OpenAI-compatible providers require an explicit model id."
    return "Provider model is not configured."


def openai_compatible_missing_base_url_error(provider: str) -> str:
    pid = str(provider or "openai").strip().lower()
    if pid == "azure_openai":
        return "Azure OpenAI requires a workspace endpoint/base URL and API version."
    if pid == "custom_openai_compatible":
        return "Custom OpenAI-compatible providers require a workspace base URL."
    return "Provider base URL is not configured."


def _azure_openai_deployment_from_override(
    override: Dict[str, Any],
    *,
    model: Any = None,
) -> str:
    return str(
        override.get("deployment")
        or override.get("deployment_name")
        or override.get("azure_deployment")
        or override.get("model")
        or override.get("model_id")
        or model
        or ""
    ).strip()


def openai_compatible_query_params(
    provider: str,
    credential_override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    pid = str(provider or "openai").strip().lower()
    if pid != "azure_openai":
        return {}
    override = credential_override if isinstance(credential_override, dict) else {}
    api_version = str(
        override.get("api_version")
        or override.get("azure_api_version")
        or ""
    ).strip()
    return {"api-version": api_version} if api_version else {}


def openai_chat_json(
    system_prompt: Optional[str],
    user_prompt: str,
    model_override: Optional[str] = None,
    prior_messages: Any = None,
    *,
    provider: str = "openai",
    credential_override: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], str, str]:
    api_key = resolve_openai_compatible_api_key(provider, credential_override=credential_override)
    if not api_key:
        return None, None, "", openai_compatible_missing_key_error(provider)

    model = openai_compatible_model_from_request(
        provider,
        model_override=model_override,
        credential_override=credential_override,
    )
    if not model:
        return None, None, "", openai_compatible_missing_model_error(provider)
    temperature = to_float(os.getenv("ORION_LOCAL_WORKER_TEMPERATURE"), 0.2)
    timeout_seconds = max(10, to_int(os.getenv("ORION_LOCAL_WORKER_LLM_TIMEOUT_SECONDS"), 45))
    base_url = resolve_openai_compatible_base_url(provider, credential_override=credential_override, model=model)
    if not base_url:
        return None, None, model, openai_compatible_missing_base_url_error(provider)
    query_params = openai_compatible_query_params(provider, credential_override=credential_override)
    if str(provider or "").strip().lower() == "azure_openai" and not query_params.get("api-version"):
        return None, None, model, openai_compatible_missing_base_url_error(provider)
    max_tokens = _resolved_output_token_cap(provider, model)

    messages: List[Dict[str, str]] = []
    if str(system_prompt or "").strip():
        messages.append({"role": "system", "content": str(system_prompt).strip()})
    messages.extend(_build_chat_messages(user_prompt, prior_messages=prior_messages))
    payload = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "messages": messages,
    }
    try:
        parsed = _openai_compatible_chat_completion_request(
            base_url=base_url,
            api_key=api_key,
            payload=payload,
            timeout_seconds=timeout_seconds,
            query_params=query_params,
        )
        choices = parsed.get("choices") if isinstance(parsed, dict) else None
        if not isinstance(choices, list) or not choices:
            return None, None, model, "empty_choices"
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            return None, None, model, "empty_content"
        result = parse_json_object_loose(content)
        if not isinstance(result, dict):
            return None, None, model, "invalid_json_content"
        usage_raw = parsed.get("usage") if isinstance(parsed, dict) else None
        usage = usage_raw if isinstance(usage_raw, dict) else None
        return result, usage, model, ""
    except Exception as exc:
        return None, None, model, format_provider_error(exc)


def openai_chat_text(
    system_prompt: Optional[str],
    user_prompt: str,
    model_override: Optional[str] = None,
    prior_messages: Any = None,
    *,
    provider: str = "openai",
    credential_override: Optional[Dict[str, Any]] = None,
) -> Tuple[str, Optional[Dict[str, Any]], str, str]:
    api_key = resolve_openai_compatible_api_key(provider, credential_override=credential_override)
    if not api_key:
        return "", None, "", openai_compatible_missing_key_error(provider)

    model = openai_compatible_model_from_request(
        provider,
        model_override=model_override,
        credential_override=credential_override,
    )
    if not model:
        return "", None, "", openai_compatible_missing_model_error(provider)
    temperature = to_float(os.getenv("ORION_LOCAL_WORKER_TEMPERATURE"), 0.2)
    timeout_seconds = max(10, to_int(os.getenv("ORION_LOCAL_WORKER_LLM_TIMEOUT_SECONDS"), 45))
    base_url = resolve_openai_compatible_base_url(provider, credential_override=credential_override, model=model)
    if not base_url:
        return "", None, model, openai_compatible_missing_base_url_error(provider)
    query_params = openai_compatible_query_params(provider, credential_override=credential_override)
    if str(provider or "").strip().lower() == "azure_openai" and not query_params.get("api-version"):
        return "", None, model, openai_compatible_missing_base_url_error(provider)
    max_tokens = _resolved_output_token_cap(provider, model)

    messages: List[Dict[str, str]] = []
    if str(system_prompt or "").strip():
        messages.append({"role": "system", "content": str(system_prompt).strip()})
    messages.extend(_build_chat_messages(user_prompt, prior_messages=prior_messages))
    payload = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    try:
        parsed = _openai_compatible_chat_completion_request(
            base_url=base_url,
            api_key=api_key,
            payload=payload,
            timeout_seconds=timeout_seconds,
            query_params=query_params,
        )
        choices = parsed.get("choices") if isinstance(parsed, dict) else None
        if not isinstance(choices, list) or not choices:
            return "", None, model, "empty_choices"
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                text = str(item.get("text") or "").strip()
                if text:
                    parts.append(text)
            content = "\n".join(parts).strip()
        if not isinstance(content, str) or not content.strip():
            return "", None, model, "empty_content"
        usage_raw = parsed.get("usage") if isinstance(parsed, dict) else None
        usage = usage_raw if isinstance(usage_raw, dict) else None
        return content.strip(), usage, model, ""
    except Exception as exc:
        return "", None, model, format_provider_error(exc)


def openai_chat_text_with_retries(
    system_prompt: Optional[str],
    user_prompt: str,
    model_override: Optional[str] = None,
    prior_messages: Any = None,
    *,
    provider: str = "openai",
    credential_override: Optional[Dict[str, Any]] = None,
) -> Tuple[str, Optional[Dict[str, Any]], str, str]:
    normalized_provider = str(provider or "openai").strip().lower() or "openai"
    max_attempts = 3 if normalized_provider == "deepseek" else 1
    last_result: Tuple[str, Optional[Dict[str, Any]], str, str] = ("", None, "", "unknown_error")
    for _attempt in range(max_attempts):
        last_result = openai_chat_text(
            system_prompt,
            user_prompt,
            model_override=model_override,
            prior_messages=prior_messages,
            provider=normalized_provider,
            credential_override=credential_override,
        )
        text, _usage, _model, provider_error = last_result
        if text:
            return last_result
        if normalized_provider != "deepseek":
            return last_result
        normalized_error = str(provider_error or "").strip().lower()
        if not normalized_error:
            continue
        if "incomplete read" not in normalized_error and "connection reset" not in normalized_error and "remote end closed" not in normalized_error:
            return last_result
    return last_result


def iter_openai_compatible_chat_events(
    system_prompt: Optional[str],
    user_prompt: str,
    model_override: Optional[str] = None,
    prior_messages: Any = None,
    *,
    provider: str = "openai",
    credential_override: Optional[Dict[str, Any]] = None,
    tools: Any = None,
) -> Iterator[Dict[str, Any]]:
    api_key = resolve_openai_compatible_api_key(provider, credential_override=credential_override)
    if not api_key:
        yield {"type": "error", "error": openai_compatible_missing_key_error(provider), "model": ""}
        return

    model = openai_compatible_model_from_request(
        provider,
        model_override=model_override,
        credential_override=credential_override,
    )
    if not model:
        yield {"type": "error", "error": openai_compatible_missing_model_error(provider), "model": ""}
        return
    temperature = to_float(os.getenv("ORION_LOCAL_WORKER_TEMPERATURE"), 0.2)
    timeout_seconds = max(10, to_int(os.getenv("ORION_LOCAL_WORKER_LLM_TIMEOUT_SECONDS"), 45))
    base_url = resolve_openai_compatible_base_url(provider, credential_override=credential_override, model=model)
    if not base_url:
        yield {"type": "error", "error": openai_compatible_missing_base_url_error(provider), "model": model}
        return
    query_params = openai_compatible_query_params(provider, credential_override=credential_override)
    if str(provider or "").strip().lower() == "azure_openai" and not query_params.get("api-version"):
        yield {"type": "error", "error": openai_compatible_missing_base_url_error(provider), "model": model}
        return
    max_tokens = _resolved_output_token_cap(provider, model)

    messages = _build_openai_compatible_messages(user_prompt, prior_messages=prior_messages)
    if str(system_prompt or "").strip():
        messages = [{"role": "system", "content": str(system_prompt).strip()}, *messages]
    payload: Dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    tool_specs = _tool_spec_items(tools)
    if tool_specs:
        payload["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": item["name"],
                    "description": item["description"],
                    "parameters": item["parameters"],
                },
            }
            for item in tool_specs
        ]
        payload["tool_choice"] = "auto"
        payload["parallel_tool_calls"] = True
    max_attempts = 3 if str(provider or "").strip().lower() == "deepseek" else 1
    last_error = "unknown_error"
    for attempt in range(max_attempts):
        try:
            parsed = _openai_compatible_chat_completion_request(
                base_url=base_url,
                api_key=api_key,
                payload=payload,
                timeout_seconds=timeout_seconds,
                query_params=query_params,
            )
            choices = parsed.get("choices") if isinstance(parsed, dict) else None
            if not isinstance(choices, list) or not choices:
                yield {"type": "error", "error": "empty_choices", "model": model}
                return
            message = choices[0].get("message") if isinstance(choices[0], dict) else None
            tool_calls = _normalize_openai_function_call(message)
            final_text = _extract_openai_message_text(message)
            # MAN-308 shape, generalized across the whole DeepSeek path (not
            # a per-model fix): a model can emit a tool call as plain/fenced
            # JSON text in `content` instead of populating the structured
            # `tool_calls` field. This is the SAME failure class
            # extract_dsml_tool_calls_from_text already recovers for Codex's
            # DSML envelope — DeepSeek does not emit that envelope, so that
            # recovery never fires for it, and the model's own upstream
            # issue (deepseek-ai/DeepSeek-V3#1244, filed 2026-04-24, still
            # open, measured ~11% of completions) has the identical shape:
            # a real tool call the model intended, written as prose instead
            # of dispatched. Wired generically across every model this
            # function serves (not keyed to a specific model id) so
            # whichever model the "pro"/"light" tiers point at today or
            # later is covered — CLAUDE.md's own rule that a fix belongs on
            # the provider/path, never hand-tied to one model string that
            # rots the moment a tier's mapping changes.
            #
            # Reuses internal_tool_markup_service.extract_textual_tool_call_
            # mentions (the MAN-263 detector) rather than reimplementing the
            # JSON-scan/name-normalization it already has. That function's
            # own docstring says it is "never wired to any executor" — true
            # of its EXISTING caller (tool_honesty_guard's synthesis-turn
            # dishonesty check, which must never double-execute a call that
            # already ran for real this turn). This call site is safe for
            # exactly the reason that one is not: it only runs when the
            # structured `tool_calls` came back EMPTY, i.e. nothing else
            # fired this turn to double up against — this IS the invocation
            # turn, not a synthesis turn describing a past success.
            # Restricted to tool names this turn actually OFFERED
            # (tool_specs): a recovered mention naming anything else is a
            # false positive (quoted JSON, an unrelated example) and is
            # dropped rather than guessed into a dispatched call — the same
            # "never trust an unregistered name" posture claude_agent_sdk_
            # bridge.is_registered_empyralis_tool already applies on the SDK
            # engine's own tool-call path.
            if not tool_calls and final_text and tool_specs:
                _offered_tool_names = {item["name"] for item in tool_specs}
                _recovered_mentions = [
                    mention
                    for mention in internal_tool_markup_service.extract_textual_tool_call_mentions(final_text)
                    if mention.get("name") in _offered_tool_names
                ]
                if _recovered_mentions:
                    print(
                        f"[DS_TEXTUAL_TOOLCALL_RECOVERED] provider={provider} model={model} "
                        f"recovered={len(_recovered_mentions)} names={[m['name'] for m in _recovered_mentions]}",
                        flush=True,
                    )
                    tool_calls = [
                        {
                            "id": None,
                            "name": mention["name"],
                            "arguments": json.dumps(mention.get("arguments") or {}),
                        }
                        for mention in _recovered_mentions
                    ]
                    # The recovered text DESCRIBED a call, it did not ANSWER
                    # the user — never stream or return it as the reply
                    # alongside the recovered call, the same "a tool-shaped
                    # message is not automatically the agent speaking"
                    # principle claude_agent_sdk_bridge.translate_sdk_
                    # message applies to a provider-error AssistantMessage.
                    final_text = ""
            if final_text:
                yield {"type": "delta", "delta": final_text, "model": model}
            usage = parsed.get("usage") if isinstance(parsed.get("usage"), dict) else None
            if final_text or tool_calls:
                yield {
                    "type": "done",
                    "text": final_text,
                    "usage": usage,
                    "model": model,
                    "tool_calls": tool_calls,
                }
                return
            # DeepSeek known bug: returns empty content when tool messages are in context.
            # Retry without tools to force a plain-text synthesis response.
            if (
                str(provider or "").strip().lower() == "deepseek"
                and "tools" in payload
                and attempt + 1 < max_attempts
            ):
                print(f"[DS_SYNTH_RETRY] attempt={attempt} stripping tools (had {len(payload.get('tools', []))} tools) final_text={final_text!r} tool_calls_count={len(tool_calls)}", flush=True)
                payload = {k: v for k, v in payload.items() if k not in ("tools", "tool_choice", "parallel_tool_calls")}
                last_error = "empty_content"
                continue
            print(f"[DS_EMPTY_CONTENT] attempt={attempt} tools_in_payload={'tools' in payload} final_text={final_text!r} tool_calls_count={len(tool_calls)}", flush=True)
            yield {"type": "error", "error": "empty_content", "model": model}
            return
        except Exception as exc:
            last_error = format_provider_error(exc)
            print(f"[DS_HTTP_ERROR] attempt={attempt}/{max_attempts} error={last_error!r}", flush=True)
            normalized_error = str(last_error or "").strip().lower()
            if attempt + 1 >= max_attempts:
                break
            if (
                "incomplete read" not in normalized_error
                and "connection reset" not in normalized_error
                and "remote end closed" not in normalized_error
            ):
                break
    yield {"type": "error", "error": last_error, "model": model}


def iter_anthropic_chat_events(
    system_prompt: Optional[str],
    user_prompt: str,
    model_override: Optional[str] = None,
    prior_messages: Any = None,
    *,
    credential_override: Optional[Dict[str, Any]] = None,
    tools: Any = None,
) -> Iterator[Dict[str, Any]]:
    api_key = resolve_anthropic_api_key(credential_override=credential_override)
    if not api_key:
        yield {"type": "error", "error": "missing_api_key", "model": ""}
        return

    model = normalize_anthropic_model(model_override)
    timeout_seconds = max(10, to_int(os.getenv("ORION_LOCAL_WORKER_LLM_TIMEOUT_SECONDS"), 45))
    max_tokens = _resolved_output_token_cap("anthropic", model)
    api_url = ensure_trailing_slashless(os.getenv("ORION_LOCAL_WORKER_ANTHROPIC_URL") or "https://api.anthropic.com")
    payload: Dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": _build_anthropic_messages(user_prompt, prior_messages=prior_messages),
    }
    if str(system_prompt or "").strip():
        payload["system"] = str(system_prompt).strip()
    tool_specs = _tool_spec_items(tools)
    if tool_specs:
        payload["tools"] = [
            {
                "name": item["name"],
                "description": item["description"],
                "input_schema": item["parameters"],
            }
            for item in tool_specs
        ]
        payload["tool_choice"] = {"type": "auto"}
    try:
        response = _anthropic_messages_request(
            api_url=api_url,
            api_key=api_key,
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
        if int(response.get("status") or 0) >= 400:
            yield {"type": "error", "error": _anthropic_response_error(response), "model": model}
            return
        parsed = response.get("json") if isinstance(response.get("json"), dict) else {}
        content_blocks = parsed.get("content") if isinstance(parsed, dict) else None
        tool_calls = _normalize_anthropic_tool_calls(content_blocks)
        final_text = _extract_anthropic_text(content_blocks)
        if final_text:
            yield {"type": "delta", "delta": final_text, "model": model}
        usage = parsed.get("usage") if isinstance(parsed.get("usage"), dict) else None
        if final_text or tool_calls:
            yield {
                "type": "done",
                "text": final_text,
                "usage": usage,
                "model": model,
                "tool_calls": tool_calls,
            }
            return
        yield {"type": "error", "error": "empty_content", "model": model}
    except Exception as exc:
        yield {"type": "error", "error": format_provider_error(exc), "model": model}


def iter_gemini_chat_events(
    system_prompt: Optional[str],
    user_prompt: str,
    model_override: Optional[str] = None,
    prior_messages: Any = None,
    *,
    tools: Any = None,
    credential_override: Optional[Dict[str, Any]] = None,
) -> Iterator[Dict[str, Any]]:
    api_key = resolve_gemini_api_key(credential_override=credential_override)
    if not api_key:
        yield {"type": "error", "error": "missing_api_key", "model": ""}
        return

    model = (str(model_override or "").strip() or os.getenv("ORION_LOCAL_WORKER_GEMINI_MODEL") or "gemini-2.0-flash").strip() or "gemini-2.0-flash"
    timeout_seconds = max(10, to_int(os.getenv("ORION_LOCAL_WORKER_LLM_TIMEOUT_SECONDS"), 45))
    api_url = ensure_trailing_slashless(os.getenv("ORION_LOCAL_WORKER_GEMINI_URL") or "https://generativelanguage.googleapis.com/v1beta")
    payload: Dict[str, Any] = {
        "contents": _build_gemini_contents(user_prompt, prior_messages=prior_messages),
        "generationConfig": {"maxOutputTokens": _resolved_output_token_cap("gemini", model)},
    }
    if str(system_prompt or "").strip():
        payload["system_instruction"] = {"parts": [{"text": str(system_prompt).strip()}]}
    tool_specs = _tool_spec_items(tools)
    if tool_specs:
        payload["tools"] = [
            {
                "functionDeclarations": [
                    {
                        "name": item["name"],
                        "description": item["description"],
                        "parameters": item["parameters"],
                    }
                    for item in tool_specs
                ]
            }
        ]
        payload["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}
    try:
        parsed = _gemini_generate_content_request(
            api_url=api_url,
            model=model,
            api_key=api_key,
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
        candidates = parsed.get("candidates") if isinstance(parsed, dict) else None
        first_candidate = candidates[0] if isinstance(candidates, list) and candidates else {}
        content = first_candidate.get("content") if isinstance(first_candidate, dict) else {}
        parts = content.get("parts") if isinstance(content, dict) else None
        tool_calls = _normalize_gemini_tool_calls(parts)
        final_text = _extract_gemini_text(parts)
        if final_text:
            yield {"type": "delta", "delta": final_text, "model": model}
        usage = parsed.get("usageMetadata") if isinstance(parsed.get("usageMetadata"), dict) else None
        if final_text or tool_calls:
            yield {
                "type": "done",
                "text": final_text,
                "usage": usage,
                "model": model,
                "tool_calls": tool_calls,
            }
            return
        yield {"type": "error", "error": "empty_content", "model": model}
    except Exception as exc:
        yield {"type": "error", "error": format_provider_error(exc), "model": model}


def iter_ollama_chat_events(
    system_prompt: Optional[str],
    user_prompt: str,
    model_override: Optional[str] = None,
    prior_messages: Any = None,
    *,
    tools: Any = None,
) -> Iterator[Dict[str, Any]]:
    if not ollama_enabled():
        yield {"type": "error", "error": "ollama_disabled", "model": ""}
        return

    model = (str(model_override or "").strip() or os.getenv("ORION_LOCAL_WORKER_OLLAMA_MODEL") or "llama3.1:8b").strip() or "llama3.1:8b"
    temperature = to_float(os.getenv("ORION_LOCAL_WORKER_TEMPERATURE"), 0.2)
    base_timeout = max(10, to_int(os.getenv("ORION_LOCAL_WORKER_LLM_TIMEOUT_SECONDS"), 45))
    timeout_seconds = max(10, to_int(os.getenv("ORION_LOCAL_WORKER_OLLAMA_TIMEOUT_SECONDS"), base_timeout))
    api_url = ensure_trailing_slashless(os.getenv("ORION_LOCAL_WORKER_OLLAMA_URL") or "http://127.0.0.1:11434")
    num_predict = max(
        128,
        to_int(
            os.getenv("ORION_LOCAL_WORKER_OLLAMA_NUM_PREDICT"),
            to_int(os.getenv("ORION_LOCAL_WORKER_MAX_TOKENS"), 700),
        ),
    )
    payload: Dict[str, Any] = {
        "model": model,
        "stream": False,
        "messages": _build_openai_compatible_messages(user_prompt, prior_messages=prior_messages),
        "options": {"temperature": temperature, "num_predict": num_predict},
    }
    if str(system_prompt or "").strip():
        payload["system"] = str(system_prompt).strip()
    tool_specs = _tool_spec_items(tools)
    if tool_specs:
        payload["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": item["name"],
                    "description": item["description"],
                    "parameters": item["parameters"],
                },
            }
            for item in tool_specs
        ]
    req = urllib.request.Request(
        url=f"{api_url}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
        parsed = json.loads(raw)
        message = parsed.get("message") if isinstance(parsed, dict) else None
        tool_calls = _normalize_openai_function_call(message)
        final_text = _extract_openai_message_text(message)
        if final_text:
            yield {"type": "delta", "delta": final_text, "model": model}
        usage = {
            "prompt_eval_count": to_int(parsed.get("prompt_eval_count"), 0),
            "eval_count": to_int(parsed.get("eval_count"), 0),
        }
        if final_text or tool_calls:
            yield {
                "type": "done",
                "text": final_text,
                "usage": usage,
                "model": model,
                "tool_calls": tool_calls,
            }
            return
        yield {"type": "error", "error": "empty_content", "model": model}
    except Exception as exc:
        yield {"type": "error", "error": format_provider_error(exc), "model": model}


def extract_openai_text(payload: Dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str) and payload.get("output_text"):
        return payload["output_text"]

    output = payload.get("output") or []
    text_parts: list[str] = []
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content") or []
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    block_text = block.get("text")
                    if isinstance(block_text, str) and block_text.strip():
                        text_parts.append(block_text.strip())
            item_text = item.get("text")
            if isinstance(item_text, str) and item_text.strip():
                text_parts.append(item_text.strip())
    if text_parts:
        return "\n".join(text_parts)

    choices = payload.get("choices") or []
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message") or {}
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    return content
    return ""


def openai_codex_backend_text(
    system_prompt: Optional[str],
    user_prompt: str,
    model_override: Optional[str] = None,
    reasoning_effort_override: Optional[str] = None,
    prior_messages: Any = None,
    credential_override: Optional[Dict[str, Any]] = None,
) -> Tuple[str, Optional[Dict[str, Any]], str, str]:
    text_parts: list[str] = []
    usage: Optional[Dict[str, Any]] = None
    model = (
        str(model_override or "").strip()
        or os.getenv("ORION_LOCAL_WORKER_CODEX_MODEL")
        or os.getenv("CODEX_MODEL")
        or "gpt-5.4"
    ).strip() or "gpt-5.4"
    for event in iter_openai_codex_backend_events(
        system_prompt,
        user_prompt,
        model_override=model_override,
        reasoning_effort_override=reasoning_effort_override,
        prior_messages=prior_messages,
        credential_override=credential_override,
    ):
        event_type = str(event.get("type") or "").strip().lower()
        if event_type == "delta":
            delta = str(event.get("delta") or "")
            if delta:
                text_parts.append(delta)
            continue
        if event_type == "done":
            usage = event.get("usage") if isinstance(event.get("usage"), dict) else None
            resolved_model = str(event.get("model") or model or "").strip() or model
            text = str(event.get("text") or "").strip() or "".join(text_parts).strip()
            if text:
                return text, usage, resolved_model, ""
            return "", usage, resolved_model, "codex_empty_output"
        if event_type == "error":
            resolved_model = str(event.get("model") or model or "").strip() or model
            return "", None, resolved_model, str(event.get("error") or "codex_response_failed").strip() or "codex_response_failed"
    final_text = "".join(text_parts).strip()
    if final_text:
        return final_text, usage, model, ""
    return "", usage, model, "codex_empty_output"


def iter_openai_codex_backend_events(
    system_prompt: Optional[str],
    user_prompt: str,
    model_override: Optional[str] = None,
    reasoning_effort_override: Optional[str] = None,
    prior_messages: Any = None,
    credential_override: Optional[Dict[str, Any]] = None,
    tools: Any = None,
) -> Iterator[Dict[str, Any]]:
    override = credential_override if isinstance(credential_override, dict) else {}
    token = sanitize_bearer_token(
        override.get("oauth_token")
        or override.get("access_token")
        or ""
    ) or get_codex_oauth_token()
    if not token:
        yield {"type": "error", "error": "missing_oauth_token", "model": ""}
        return

    account_id = str(override.get("account_id") or "").strip() or codex_account_id_from_token(token)
    if not account_id:
        yield {"type": "error", "error": "missing_chatgpt_account_id", "model": ""}
        return

    model = (
        str(model_override or "").strip()
        or os.getenv("ORION_LOCAL_WORKER_CODEX_MODEL")
        or os.getenv("CODEX_MODEL")
        or "gpt-5.4"
    ).strip() or "gpt-5.4"
    timeout_seconds = max(20, to_int(os.getenv("ORION_LOCAL_WORKER_CODEX_TIMEOUT_SECONDS"), 90))
    reasoning_effort = (
        str(reasoning_effort_override or "").strip().lower()
        or str(os.getenv("ORION_LOCAL_WORKER_CODEX_REASONING_EFFORT") or "low").strip().lower()
        or "low"
    )
    api_url = ensure_trailing_slashless(
        os.getenv("ORION_LOCAL_WORKER_CODEX_RESPONSES_URL")
        or "https://chatgpt.com/backend-api/codex/responses"
    )

    payload = {
        "model": model,
        "store": False,
        "stream": True,
        "max_output_tokens": _resolved_output_token_cap("codex_cli", model),
        "input": _build_responses_input(user_prompt, prior_messages=prior_messages),
        "instructions": codex_instructions(system_prompt),
        "text": {"verbosity": "low"},
        "tool_choice": "auto",
        "parallel_tool_calls": True,
    }
    if isinstance(tools, list) and tools:
        payload["tools"] = [
            {
                "type": "function",
                "name": str(item.get("name") or "").strip(),
                "description": str(item.get("description") or "").strip(),
                "parameters": item.get("parameters"),
            }
            for item in tools
            if isinstance(item, dict)
            and str(item.get("name") or "").strip()
            and isinstance(item.get("parameters"), dict)
        ]
        if not payload["tools"]:
            payload.pop("tools", None)
    if reasoning_effort:
        payload["reasoning"] = {"effort": reasoning_effort, "summary": "auto"}
    req = urllib.request.Request(
        url=api_url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "chatgpt-account-id": account_id,
            "originator": "pi",
            "OpenAI-Beta": "responses=experimental",
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
            "User-Agent": "Empyralis Local Worker",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
            buffer = ""
            text_parts: list[str] = []
            usage: Optional[Dict[str, Any]] = None
            completed_message_text = ""
            tool_calls: list[Dict[str, Any]] = []

            while True:
                chunk = response.read(8192)
                if not chunk:
                    break
                buffer += chunk.decode("utf-8", "ignore")
                while "\n\n" in buffer:
                    raw_event, buffer = buffer.split("\n\n", 1)
                    data_lines = []
                    for line in raw_event.splitlines():
                        if line.startswith("data:"):
                            data_lines.append(line[5:].strip())
                    if not data_lines:
                        continue
                    payload_text = "\n".join(data_lines).strip()
                    if not payload_text or payload_text == "[DONE]":
                        continue
                    try:
                        event = json.loads(payload_text)
                    except Exception:
                        continue
                    if not isinstance(event, dict):
                        continue
                    event_type = str(event.get("type") or "").strip()
                    if event_type == "response.output_text.delta":
                        delta = str(event.get("delta") or "")
                        if delta:
                            text_parts.append(delta)
                        continue
                    if event_type == "response.output_item.done":
                        item = event.get("item")
                        if isinstance(item, dict) and str(item.get("type") or "").strip() == "function_call":
                            tool_name = str(item.get("name") or "").strip()
                            if tool_name:
                                tool_calls.append(
                                    {
                                        "name": tool_name,
                                        "arguments": item.get("arguments"),
                                    }
                                )
                            continue
                        if isinstance(item, dict) and str(item.get("type") or "").strip() == "message":
                            content = item.get("content")
                            done_parts: list[str] = []
                            if isinstance(content, list):
                                for block in content:
                                    if not isinstance(block, dict):
                                        continue
                                    if str(block.get("type") or "").strip() == "output_text":
                                        block_text = str(block.get("text") or "")
                                        if block_text:
                                            done_parts.append(block_text)
                            completed_message_text = "\n".join(part for part in done_parts if part).strip()
                        continue
                    if event_type in {"response.completed", "response.done", "response.incomplete"}:
                        response_payload = event.get("response")
                        if isinstance(response_payload, dict) and isinstance(response_payload.get("usage"), dict):
                            usage = response_payload.get("usage")
                        final_text = completed_message_text or "\n".join(part for part in text_parts if part).strip()
                        if not final_text and isinstance(response_payload, dict):
                            final_text = extract_openai_text(response_payload).strip()
                        final_text, dsml_tool_calls = extract_dsml_tool_calls_from_text(final_text)
                        if dsml_tool_calls:
                            tool_calls.extend(dsml_tool_calls)
                        if final_text or tool_calls:
                            yield {
                                "type": "done",
                                "text": final_text,
                                "usage": usage,
                                "model": model,
                                "tool_calls": tool_calls,
                            }
                            return
                        yield {"type": "error", "error": "codex_empty_output", "model": model}
                        return
                    if event_type in {"response.failed", "error"}:
                        message = ""
                        response_payload = event.get("response")
                        if isinstance(response_payload, dict):
                            response_error = response_payload.get("error")
                            if isinstance(response_error, dict):
                                message = str(response_error.get("message") or response_error.get("code") or "").strip()
                        if not message:
                            message = str(event.get("message") or event.get("code") or "codex_response_failed").strip()
                        yield {"type": "error", "error": message or "codex_response_failed", "model": model}
                        return

            final_text = "\n".join(part for part in text_parts if part).strip()
            final_text, dsml_tool_calls = extract_dsml_tool_calls_from_text(final_text)
            if dsml_tool_calls:
                tool_calls.extend(dsml_tool_calls)
            if final_text or tool_calls:
                yield {
                    "type": "done",
                    "text": final_text,
                    "usage": usage,
                    "model": model,
                    "tool_calls": tool_calls,
                }
                return
            yield {"type": "error", "error": "codex_empty_output", "model": model}
            return
    except Exception as exc:
        yield {"type": "error", "error": format_provider_error(exc), "model": model}
        return


def openai_responses_text(
    system_prompt: Optional[str],
    user_prompt: str,
    model_override: Optional[str] = None,
    prior_messages: Any = None,
) -> Tuple[str, Optional[Dict[str, Any]], str, str]:
    api_key = get_openai_api_key()
    if not api_key:
        return "", None, "", OPENAI_API_KEY_MISSING_ERROR

    model = (
        str(model_override or "").strip()
        or os.getenv("ORION_LOCAL_WORKER_OPENAI_MODEL")
        or os.getenv("CODEX_MODEL")
        or "gpt-4.1"
    ).strip() or "gpt-4.1"
    timeout_seconds = max(10, to_int(os.getenv("ORION_LOCAL_WORKER_LLM_TIMEOUT_SECONDS"), 45))
    api_url = ensure_trailing_slashless(
        os.getenv("ORION_LOCAL_WORKER_OPENAI_RESPONSES_URL")
        or os.getenv("OPENAI_RESPONSES_URL")
        or "https://api.openai.com/v1/responses"
    )
    payload = {
        "model": model,
        "input": _build_responses_input(user_prompt, prior_messages=prior_messages),
        "max_output_tokens": _resolved_output_token_cap("openai", model),
    }
    if str(system_prompt or "").strip():
        payload["instructions"] = str(system_prompt).strip()
    req = urllib.request.Request(
        url=api_url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return "", None, model, "invalid_response"
        text = extract_openai_text(parsed).strip()
        if not text:
            return "", parsed.get("usage") if isinstance(parsed.get("usage"), dict) else None, model, "empty_content"
        usage = parsed.get("usage") if isinstance(parsed.get("usage"), dict) else None
        return text, usage, model, ""
    except Exception as exc:
        return "", None, model, format_provider_error(exc)


def codex_exec_text(
    system_prompt: Optional[str],
    user_prompt: str,
    model_override: Optional[str] = None,
    reasoning_effort_override: Optional[str] = None,
    prior_messages: Any = None,
    credential_override: Optional[Dict[str, Any]] = None,
) -> Tuple[str, Optional[Dict[str, Any]], str, str]:
    direct_text, direct_usage, direct_model, direct_error = openai_codex_backend_text(
        system_prompt,
        user_prompt,
        model_override=model_override,
        reasoning_effort_override=reasoning_effort_override,
        prior_messages=prior_messages,
        credential_override=credential_override,
    )
    if direct_text:
        return direct_text, direct_usage, direct_model, ""
    if not codex_cli_available():
        return "", None, direct_model or "codex", direct_error or "codex_cli_not_found"

    timeout_seconds = max(15, to_int(os.getenv("ORION_LOCAL_WORKER_CODEX_TIMEOUT_SECONDS"), 90))
    model = (str(model_override or "").strip() or os.getenv("ORION_LOCAL_WORKER_CODEX_MODEL") or "").strip()

    prompt_parts: List[str] = []
    if str(system_prompt or "").strip():
        prompt_parts.append(str(system_prompt).strip())
    prompt_parts.append(str(user_prompt or "").strip())
    prompt = "\n\n".join(prompt_parts)

    with tempfile.NamedTemporaryFile(prefix="orion-codex-", suffix=".txt", delete=False) as tmp:
        out_path = tmp.name

    cmd = [
        "codex",
        "exec",
        "--ephemeral",
        "--skip-git-repo-check",
        "--sandbox",
        "workspace-write",
        "--output-last-message",
        out_path,
    ]
    if model:
        cmd.extend(["--model", model])
    cmd.append(prompt)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        if result.returncode != 0:
            stderr = str(result.stderr or "").strip()
            stdout = str(result.stdout or "").strip()
            detail = stderr or stdout or f"exit_{result.returncode}"
            if len(detail) > 320:
                detail = detail[:320] + "..."
            return "", None, model or "codex", f"codex_exec_failed: {detail}"
        try:
            text = Path(out_path).read_text(encoding="utf-8").strip()
        except Exception:
            text = ""
        if not text:
            return "", None, model or "codex", "codex_empty_output"
        return text, None, model or "codex", ""
    except subprocess.TimeoutExpired:
        if direct_error:
            return "", None, model or "codex", direct_error
        return "", None, model or "codex", "codex_timeout"
    except Exception as exc:
        if direct_error:
            return "", None, model or "codex", direct_error
        return "", None, model or "codex", f"codex_exec_error: {exc}"
    finally:
        try:
            Path(out_path).unlink(missing_ok=True)
        except Exception:
            pass


def codex_exec_json(
    system_prompt: str,
    user_prompt: str,
    model_override: Optional[str] = None,
    credential_override: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], str, str]:
    prompt = (
        f"{user_prompt}\n\n"
        'Return strictly valid JSON only with keys: "summary", "content_plan", "next_steps".'
    )
    text, usage, model, err = codex_exec_text(
        system_prompt,
        prompt,
        model_override=model_override,
        credential_override=credential_override,
    )
    if not text:
        return None, usage, model, err or "codex_empty_output"
    parsed = parse_json_object_loose(text)
    if not isinstance(parsed, dict):
        return None, usage, model, "codex_invalid_json_content"
    return parsed, usage, model, ""


def claude_code_exec_text(
    system_prompt: Optional[str],
    user_prompt: str,
    model_override: Optional[str] = None,
    prior_messages: Any = None,
) -> Tuple[str, Optional[Dict[str, Any]], str, str]:
    if not claude_code_cli_available():
        return "", None, "sonnet", "claude_code_cli_not_found"

    timeout_seconds = max(15, to_int(os.getenv("ORION_LOCAL_WORKER_CLAUDE_CODE_TIMEOUT_SECONDS"), 120))
    model = (str(model_override or "").strip() or os.getenv("ORION_LOCAL_WORKER_CLAUDE_CODE_MODEL") or "sonnet").strip() or "sonnet"
    cmd = [
        "claude",
        "-p",
        "--output-format",
        "text",
        "--model",
        model,
    ]
    if str(system_prompt or "").strip():
        cmd.extend(["--system-prompt", str(system_prompt).strip()])
    cmd.append(user_prompt)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        if result.returncode != 0:
            stderr = str(result.stderr or "").strip()
            stdout = str(result.stdout or "").strip()
            detail = stderr or stdout or f"exit_{result.returncode}"
            if len(detail) > 320:
                detail = detail[:320] + "..."
            return "", None, model, f"claude_code_exec_failed: {detail}"
        text = str(result.stdout or "").strip()
        if not text:
            return "", None, model, "claude_code_empty_output"
        return text, None, model, ""
    except subprocess.TimeoutExpired:
        return "", None, model, "claude_code_timeout"
    except Exception as exc:
        return "", None, model, f"claude_code_exec_error: {exc}"


def claude_code_exec_json(system_prompt: str, user_prompt: str, model_override: Optional[str] = None) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], str, str]:
    prompt = (
        f"{user_prompt}\n\n"
        'Return strictly valid JSON only with keys: "summary", "content_plan", "next_steps".'
    )
    text, usage, model, err = claude_code_exec_text(system_prompt, prompt, model_override=model_override)
    if not text:
        return None, usage, model, err or "claude_code_empty_output"
    parsed = parse_json_object_loose(text)
    if not isinstance(parsed, dict):
        return None, usage, model, "claude_code_invalid_json_content"
    return parsed, usage, model, ""


def anthropic_chat_text(
    system_prompt: Optional[str],
    user_prompt: str,
    model_override: Optional[str] = None,
    prior_messages: Any = None,
    credential_override: Optional[Dict[str, Any]] = None,
) -> Tuple[str, Optional[Dict[str, Any]], str, str]:
    api_key = resolve_anthropic_api_key(credential_override=credential_override)
    if not api_key:
        return "", None, "", "missing_api_key"

    model = normalize_anthropic_model(model_override)
    timeout_seconds = max(10, to_int(os.getenv("ORION_LOCAL_WORKER_LLM_TIMEOUT_SECONDS"), 45))
    max_tokens = _resolved_output_token_cap("anthropic", model)
    api_url = ensure_trailing_slashless(os.getenv("ORION_LOCAL_WORKER_ANTHROPIC_URL") or "https://api.anthropic.com")
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": _build_chat_messages(user_prompt, prior_messages=prior_messages),
    }
    if str(system_prompt or "").strip():
        payload["system"] = str(system_prompt).strip()
    try:
        response = _anthropic_messages_request(
            api_url=api_url,
            api_key=api_key,
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
        if int(response.get("status") or 0) >= 400:
            return "", None, model, _anthropic_response_error(response)
        parsed = response.get("json") if isinstance(response.get("json"), dict) else {}
        content = parsed.get("content") if isinstance(parsed, dict) else None
        parts: list[str] = []
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        parts.append(text.strip())
        combined = "\n".join(parts).strip()
        if not combined:
            return "", None, model, "empty_content"
        usage_raw = parsed.get("usage") if isinstance(parsed, dict) else None
        usage = usage_raw if isinstance(usage_raw, dict) else None
        return combined, usage, model, ""
    except Exception as exc:
        return "", None, model, format_provider_error(exc)


def anthropic_chat_json(
    system_prompt: Optional[str],
    user_prompt: str,
    model_override: Optional[str] = None,
    prior_messages: Any = None,
    credential_override: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], str, str]:
    api_key = resolve_anthropic_api_key(credential_override=credential_override)
    if not api_key:
        return None, None, "", "missing_api_key"

    model = normalize_anthropic_model(model_override)
    timeout_seconds = max(10, to_int(os.getenv("ORION_LOCAL_WORKER_LLM_TIMEOUT_SECONDS"), 45))
    max_tokens = _resolved_output_token_cap("anthropic", model)
    api_url = ensure_trailing_slashless(os.getenv("ORION_LOCAL_WORKER_ANTHROPIC_URL") or "https://api.anthropic.com")
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": _build_chat_messages(user_prompt, prior_messages=prior_messages),
    }
    if str(system_prompt or "").strip():
        payload["system"] = str(system_prompt).strip()
    try:
        response = _anthropic_messages_request(
            api_url=api_url,
            api_key=api_key,
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
        if int(response.get("status") or 0) >= 400:
            return None, None, model, _anthropic_response_error(response)
        parsed = response.get("json") if isinstance(response.get("json"), dict) else {}
        content = parsed.get("content") if isinstance(parsed, dict) else None
        parts: list[str] = []
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        parts.append(text.strip())
        combined = "\n".join(parts).strip()
        if not combined:
            return None, None, model, "empty_content"
        result = parse_json_object_loose(combined)
        if not isinstance(result, dict):
            return None, None, model, "invalid_json_content"
        usage_raw = parsed.get("usage") if isinstance(parsed, dict) else None
        usage = usage_raw if isinstance(usage_raw, dict) else None
        return result, usage, model, ""
    except Exception as exc:
        return None, None, model, format_provider_error(exc)


def gemini_chat_text(
    system_prompt: Optional[str],
    user_prompt: str,
    model_override: Optional[str] = None,
    prior_messages: Any = None,
    credential_override: Optional[Dict[str, Any]] = None,
) -> Tuple[str, Optional[Dict[str, Any]], str, str]:
    api_key = resolve_gemini_api_key(credential_override=credential_override)
    if not api_key:
        return "", None, "", "missing_api_key"

    model = (str(model_override or "").strip() or os.getenv("ORION_LOCAL_WORKER_GEMINI_MODEL") or "gemini-2.0-flash").strip() or "gemini-2.0-flash"
    timeout_seconds = max(10, to_int(os.getenv("ORION_LOCAL_WORKER_LLM_TIMEOUT_SECONDS"), 45))
    api_url = ensure_trailing_slashless(os.getenv("ORION_LOCAL_WORKER_GEMINI_URL") or "https://generativelanguage.googleapis.com/v1beta")
    payload = {
        "contents": [
            {"role": item["role"], "parts": [{"text": item["content"]}]}
            for item in _build_chat_messages(user_prompt, prior_messages=prior_messages, assistant_role="model")
        ],
        "generationConfig": {"maxOutputTokens": _resolved_output_token_cap("gemini", model)},
    }
    if str(system_prompt or "").strip():
        payload["system_instruction"] = {"parts": [{"text": str(system_prompt).strip()}]}
    try:
        parsed = _gemini_generate_content_request(
            api_url=api_url,
            model=model,
            api_key=api_key,
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
        candidates = parsed.get("candidates") if isinstance(parsed, dict) else None
        texts: list[str] = []
        if isinstance(candidates, list):
            for cand in candidates:
                if not isinstance(cand, dict):
                    continue
                content = cand.get("content")
                if not isinstance(content, dict):
                    continue
                for part in content.get("parts", []) if isinstance(content.get("parts"), list) else []:
                    if isinstance(part, dict):
                        text = part.get("text")
                        if isinstance(text, str) and text.strip():
                            texts.append(text.strip())
        combined = "\n".join(texts).strip()
        if not combined:
            return "", None, model, "empty_content"
        usage_raw = parsed.get("usageMetadata") if isinstance(parsed, dict) else None
        usage = usage_raw if isinstance(usage_raw, dict) else None
        return combined, usage, model, ""
    except Exception as exc:
        return "", None, model, format_provider_error(exc)


def gemini_chat_json(
    system_prompt: Optional[str],
    user_prompt: str,
    model_override: Optional[str] = None,
    prior_messages: Any = None,
    credential_override: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], str, str]:
    api_key = resolve_gemini_api_key(credential_override=credential_override)
    if not api_key:
        return None, None, "", "missing_api_key"

    model = (str(model_override or "").strip() or os.getenv("ORION_LOCAL_WORKER_GEMINI_MODEL") or "gemini-2.0-flash").strip() or "gemini-2.0-flash"
    timeout_seconds = max(10, to_int(os.getenv("ORION_LOCAL_WORKER_LLM_TIMEOUT_SECONDS"), 45))
    api_url = ensure_trailing_slashless(os.getenv("ORION_LOCAL_WORKER_GEMINI_URL") or "https://generativelanguage.googleapis.com/v1beta")
    payload = {
        "contents": [
            {"role": item["role"], "parts": [{"text": item["content"]}]}
            for item in _build_chat_messages(user_prompt, prior_messages=prior_messages, assistant_role="model")
        ],
        "generationConfig": {"maxOutputTokens": _resolved_output_token_cap("gemini", model)},
    }
    if str(system_prompt or "").strip():
        payload["system_instruction"] = {"parts": [{"text": str(system_prompt).strip()}]}
    try:
        parsed = _gemini_generate_content_request(
            api_url=api_url,
            model=model,
            api_key=api_key,
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
        candidates = parsed.get("candidates") if isinstance(parsed, dict) else None
        texts: list[str] = []
        if isinstance(candidates, list):
            for cand in candidates:
                if not isinstance(cand, dict):
                    continue
                content = cand.get("content")
                if not isinstance(content, dict):
                    continue
                for part in content.get("parts", []) if isinstance(content.get("parts"), list) else []:
                    if isinstance(part, dict):
                        text = part.get("text")
                        if isinstance(text, str) and text.strip():
                            texts.append(text.strip())
        combined = "\n".join(texts).strip()
        if not combined:
            return None, None, model, "empty_content"
        result = parse_json_object_loose(combined)
        if not isinstance(result, dict):
            return None, None, model, "invalid_json_content"
        usage_raw = parsed.get("usageMetadata") if isinstance(parsed, dict) else None
        usage = usage_raw if isinstance(usage_raw, dict) else None
        return result, usage, model, ""
    except Exception as exc:
        return None, None, model, format_provider_error(exc)


def ollama_chat_text(
    system_prompt: Optional[str],
    user_prompt: str,
    model_override: Optional[str] = None,
    prior_messages: Any = None,
) -> Tuple[str, Optional[Dict[str, Any]], str, str]:
    if not ollama_enabled():
        return "", None, "", "ollama_disabled"

    model = (str(model_override or "").strip() or os.getenv("ORION_LOCAL_WORKER_OLLAMA_MODEL") or "llama3.1:8b").strip() or "llama3.1:8b"
    temperature = to_float(os.getenv("ORION_LOCAL_WORKER_TEMPERATURE"), 0.2)
    base_timeout = max(10, to_int(os.getenv("ORION_LOCAL_WORKER_LLM_TIMEOUT_SECONDS"), 45))
    timeout_seconds = max(10, to_int(os.getenv("ORION_LOCAL_WORKER_OLLAMA_TIMEOUT_SECONDS"), base_timeout))
    api_url = ensure_trailing_slashless(os.getenv("ORION_LOCAL_WORKER_OLLAMA_URL") or "http://127.0.0.1:11434")
    num_predict = max(
        128,
        to_int(
            os.getenv("ORION_LOCAL_WORKER_OLLAMA_NUM_PREDICT"),
            _resolved_output_token_cap("ollama", model),
        ),
    )

    payload = {
        "model": model,
        "stream": False,
        "messages": _build_chat_messages(user_prompt, prior_messages=prior_messages),
        "options": {"temperature": temperature, "num_predict": num_predict},
    }
    if str(system_prompt or "").strip():
        payload["system"] = str(system_prompt).strip()
    req = urllib.request.Request(
        url=f"{api_url}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
        parsed = json.loads(raw)
        message = parsed.get("message") if isinstance(parsed, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            return "", None, model, "empty_content"
        usage = {
            "prompt_eval_count": to_int(parsed.get("prompt_eval_count"), 0),
            "eval_count": to_int(parsed.get("eval_count"), 0),
        }
        return content.strip(), usage, model, ""
    except Exception as exc:
        return "", None, model, format_provider_error(exc)


def ollama_chat_json(
    system_prompt: Optional[str],
    user_prompt: str,
    model_override: Optional[str] = None,
    prior_messages: Any = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], str, str]:
    if not ollama_enabled():
        return None, None, "", "ollama_disabled"

    model = (str(model_override or "").strip() or os.getenv("ORION_LOCAL_WORKER_OLLAMA_MODEL") or "llama3.1:8b").strip() or "llama3.1:8b"
    temperature = to_float(os.getenv("ORION_LOCAL_WORKER_TEMPERATURE"), 0.2)
    base_timeout = max(10, to_int(os.getenv("ORION_LOCAL_WORKER_LLM_TIMEOUT_SECONDS"), 45))
    timeout_seconds = max(10, to_int(os.getenv("ORION_LOCAL_WORKER_OLLAMA_TIMEOUT_SECONDS"), base_timeout))
    api_url = ensure_trailing_slashless(os.getenv("ORION_LOCAL_WORKER_OLLAMA_URL") or "http://127.0.0.1:11434")
    num_predict = max(
        128,
        to_int(
            os.getenv("ORION_LOCAL_WORKER_OLLAMA_NUM_PREDICT"),
            _resolved_output_token_cap("ollama", model),
        ),
    )

    payload = {
        "model": model,
        "stream": False,
        "format": "json",
        "messages": _build_chat_messages(user_prompt, prior_messages=prior_messages),
        "options": {"temperature": temperature, "num_predict": num_predict},
    }
    if str(system_prompt or "").strip():
        payload["system"] = str(system_prompt).strip()
    req = urllib.request.Request(
        url=f"{api_url}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
        parsed = json.loads(raw)
        message = parsed.get("message") if isinstance(parsed, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            return None, None, model, "empty_content"
        result = parse_json_object_loose(content)
        if not isinstance(result, dict):
            return None, None, model, "invalid_json_content"
        usage = {
            "prompt_eval_count": to_int(parsed.get("prompt_eval_count"), 0),
            "eval_count": to_int(parsed.get("eval_count"), 0),
        }
        return result, usage, model, ""
    except Exception as exc:
        return None, None, model, format_provider_error(exc)


def resolve_ollama_cloud_api_key(credential_override: Optional[Dict[str, Any]] = None) -> str:
    override = credential_override if isinstance(credential_override, dict) else {}
    inline_key = sanitize_bearer_token(
        override.get("api_key")
        or override.get("access_token")
        or override.get("oauth_token")
        or override.get("token")
        or ""
    )
    if inline_key:
        return inline_key
    return get_ollama_cloud_api_key()


def resolve_ollama_cloud_base_url(credential_override: Optional[Dict[str, Any]] = None) -> str:
    override = credential_override if isinstance(credential_override, dict) else {}
    return ensure_trailing_slashless(
        override.get("base_url")
        or os.getenv("ORION_LOCAL_WORKER_OLLAMA_CLOUD_URL")
        or "https://ollama.com"
    )


def default_ollama_cloud_model() -> str:
    return (os.getenv("ORION_LOCAL_WORKER_OLLAMA_CLOUD_MODEL") or "gpt-oss:120b").strip() or "gpt-oss:120b"


def ollama_cloud_missing_key_error() -> str:
    return "No Ollama Cloud API key configured. Add one from the AI accounts page."


def ollama_cloud_headers(api_key: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {sanitize_bearer_token(api_key)}",
        "Content-Type": "application/json",
    }


def _ollama_cloud_chat_payload(
    system_prompt: Optional[str],
    user_prompt: str,
    *,
    model: str,
    prior_messages: Any = None,
    response_format: Optional[str] = None,
    tools: Any = None,
) -> Dict[str, Any]:
    temperature = to_float(os.getenv("ORION_LOCAL_WORKER_TEMPERATURE"), 0.2)
    num_predict = max(
        128,
        to_int(
            os.getenv("ORION_LOCAL_WORKER_OLLAMA_CLOUD_NUM_PREDICT"),
            to_int(os.getenv("ORION_LOCAL_WORKER_MAX_TOKENS"), 700),
        ),
    )
    payload: Dict[str, Any] = {
        "model": model,
        "stream": False,
        "messages": _build_openai_compatible_messages(user_prompt, prior_messages=prior_messages),
        "options": {"temperature": temperature, "num_predict": num_predict},
    }
    if str(system_prompt or "").strip():
        payload["system"] = str(system_prompt).strip()
    if response_format:
        payload["format"] = response_format
    tool_specs = _tool_spec_items(tools)
    if tool_specs:
        payload["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": item["name"],
                    "description": item["description"],
                    "parameters": item["parameters"],
                },
            }
            for item in tool_specs
        ]
    return payload


def _ollama_cloud_chat_request(
    payload: Dict[str, Any],
    *,
    api_key: str,
    base_url: str,
    timeout_seconds: int,
) -> Dict[str, Any]:
    req = urllib.request.Request(
        url=f"{base_url}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers=ollama_cloud_headers(api_key),
    )
    with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
        raw = response.read().decode("utf-8")
    parsed = json.loads(raw)
    return parsed if isinstance(parsed, dict) else {}


def ollama_cloud_chat_text(
    system_prompt: Optional[str],
    user_prompt: str,
    model_override: Optional[str] = None,
    prior_messages: Any = None,
    *,
    credential_override: Optional[Dict[str, Any]] = None,
) -> Tuple[str, Optional[Dict[str, Any]], str, str]:
    api_key = resolve_ollama_cloud_api_key(credential_override)
    if not api_key:
        return "", None, "", ollama_cloud_missing_key_error()

    model = (str(model_override or "").strip() or default_ollama_cloud_model()).strip() or default_ollama_cloud_model()
    base_timeout = max(10, to_int(os.getenv("ORION_LOCAL_WORKER_LLM_TIMEOUT_SECONDS"), 45))
    timeout_seconds = max(10, to_int(os.getenv("ORION_LOCAL_WORKER_OLLAMA_CLOUD_TIMEOUT_SECONDS"), base_timeout))
    base_url = resolve_ollama_cloud_base_url(credential_override)
    payload = _ollama_cloud_chat_payload(system_prompt, user_prompt, model=model, prior_messages=prior_messages)
    try:
        parsed = _ollama_cloud_chat_request(
            payload,
            api_key=api_key,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
        )
        message = parsed.get("message") if isinstance(parsed, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            return "", None, model, "empty_content"
        usage = {
            "prompt_eval_count": to_int(parsed.get("prompt_eval_count"), 0),
            "eval_count": to_int(parsed.get("eval_count"), 0),
        }
        return content.strip(), usage, model, ""
    except Exception as exc:
        return "", None, model, format_provider_error(exc)


def ollama_cloud_chat_json(
    system_prompt: Optional[str],
    user_prompt: str,
    model_override: Optional[str] = None,
    prior_messages: Any = None,
    *,
    credential_override: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], str, str]:
    api_key = resolve_ollama_cloud_api_key(credential_override)
    if not api_key:
        return None, None, "", ollama_cloud_missing_key_error()

    model = (str(model_override or "").strip() or default_ollama_cloud_model()).strip() or default_ollama_cloud_model()
    base_timeout = max(10, to_int(os.getenv("ORION_LOCAL_WORKER_LLM_TIMEOUT_SECONDS"), 45))
    timeout_seconds = max(10, to_int(os.getenv("ORION_LOCAL_WORKER_OLLAMA_CLOUD_TIMEOUT_SECONDS"), base_timeout))
    base_url = resolve_ollama_cloud_base_url(credential_override)
    payload = _ollama_cloud_chat_payload(system_prompt, user_prompt, model=model, prior_messages=prior_messages, response_format="json")
    try:
        parsed = _ollama_cloud_chat_request(
            payload,
            api_key=api_key,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
        )
        message = parsed.get("message") if isinstance(parsed, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            return None, None, model, "empty_content"
        result = parse_json_object_loose(content)
        if not isinstance(result, dict):
            return None, None, model, "invalid_json_content"
        usage = {
            "prompt_eval_count": to_int(parsed.get("prompt_eval_count"), 0),
            "eval_count": to_int(parsed.get("eval_count"), 0),
        }
        return result, usage, model, ""
    except Exception as exc:
        return None, None, model, format_provider_error(exc)


def iter_ollama_cloud_chat_events(
    system_prompt: Optional[str],
    user_prompt: str,
    model_override: Optional[str] = None,
    prior_messages: Any = None,
    *,
    credential_override: Optional[Dict[str, Any]] = None,
    tools: Any = None,
) -> Iterator[Dict[str, Any]]:
    api_key = resolve_ollama_cloud_api_key(credential_override)
    if not api_key:
        yield {"type": "error", "error": ollama_cloud_missing_key_error(), "model": ""}
        return

    model = (str(model_override or "").strip() or default_ollama_cloud_model()).strip() or default_ollama_cloud_model()
    base_timeout = max(10, to_int(os.getenv("ORION_LOCAL_WORKER_LLM_TIMEOUT_SECONDS"), 45))
    timeout_seconds = max(10, to_int(os.getenv("ORION_LOCAL_WORKER_OLLAMA_CLOUD_TIMEOUT_SECONDS"), base_timeout))
    base_url = resolve_ollama_cloud_base_url(credential_override)
    payload = _ollama_cloud_chat_payload(system_prompt, user_prompt, model=model, prior_messages=prior_messages, tools=tools)
    try:
        parsed = _ollama_cloud_chat_request(
            payload,
            api_key=api_key,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
        )
        message = parsed.get("message") if isinstance(parsed, dict) else None
        tool_calls = _normalize_openai_function_call(message)
        final_text = _extract_openai_message_text(message)
        if final_text:
            yield {"type": "delta", "delta": final_text, "model": model}
        usage = {
            "prompt_eval_count": to_int(parsed.get("prompt_eval_count"), 0),
            "eval_count": to_int(parsed.get("eval_count"), 0),
        }
        if final_text or tool_calls:
            yield {
                "type": "done",
                "text": final_text,
                "usage": usage,
                "model": model,
                "tool_calls": tool_calls,
            }
            return
        yield {"type": "error", "error": "empty_content", "model": model}
    except Exception as exc:
        yield {"type": "error", "error": format_provider_error(exc), "model": model}


def build_usage_masked(provider: str, model: str, input_tokens: int, output_tokens: int, total_tokens: int) -> Dict[str, Any]:
    return build_usage_record(
        provider,
        model,
        max(0, input_tokens),
        max(0, output_tokens),
        max(0, total_tokens),
    )


def build_usage_masked_from_provider(provider: str, usage: Optional[Dict[str, Any]], model: str) -> Dict[str, Any]:
    pid = str(provider or "").strip().lower()
    source = usage or {}
    if pid not in {"claude_code_cli"}:
        try:
            record = usage_accounting_service.normalize_provider_usage(
                provider=pid or "local_companion",
                model=model or resolve_requested_model({}, {}, pid),
                usage=source,
            )
            return usage_accounting_service.usage_projection_from_record(record)
        except Exception:
            pass
    if pid == "codex_cli":
        prompt_tokens = to_int(source.get("prompt_tokens"), 0)
        completion_tokens = to_int(source.get("completion_tokens"), 0)
        total_tokens = to_int(source.get("total_tokens"), prompt_tokens + completion_tokens)
        return build_usage_masked(pid, model or "codex", prompt_tokens, completion_tokens, total_tokens)
    if pid == "claude_code_cli":
        return build_usage_masked(pid, model or "sonnet", 0, 0, 0)
    if pid == "openai":
        prompt_tokens = to_int(source.get("prompt_tokens"), 0)
        completion_tokens = to_int(source.get("completion_tokens"), 0)
        total_tokens = to_int(source.get("total_tokens"), prompt_tokens + completion_tokens)
        return build_usage_masked(pid, model or "gpt-4.1", prompt_tokens, completion_tokens, total_tokens)
    if pid in {"qwen", "deepseek", "mistral"}:
        prompt_tokens = to_int(source.get("prompt_tokens"), 0)
        completion_tokens = to_int(source.get("completion_tokens"), 0)
        total_tokens = to_int(source.get("total_tokens"), prompt_tokens + completion_tokens)
        return build_usage_masked(pid, model or resolve_requested_model({}, {}, pid), prompt_tokens, completion_tokens, total_tokens)
    if pid == "anthropic":
        prompt_tokens = to_int(source.get("input_tokens"), 0)
        completion_tokens = to_int(source.get("output_tokens"), 0)
        total_tokens = prompt_tokens + completion_tokens
        return build_usage_masked(pid, normalize_anthropic_model(model), prompt_tokens, completion_tokens, total_tokens)
    if pid == "gemini":
        prompt_tokens = to_int(source.get("promptTokenCount"), 0)
        completion_tokens = to_int(source.get("candidatesTokenCount"), 0)
        total_tokens = to_int(source.get("totalTokenCount"), prompt_tokens + completion_tokens)
        return build_usage_masked(pid, model or "gemini-2.0-flash", prompt_tokens, completion_tokens, total_tokens)
    if pid in {"ollama", "ollama_cloud"}:
        prompt_tokens = to_int(source.get("prompt_eval_count"), 0)
        completion_tokens = to_int(source.get("eval_count"), 0)
        total_tokens = prompt_tokens + completion_tokens
        fallback_model = "llama3.1:8b" if pid == "ollama" else default_ollama_cloud_model()
        return build_usage_masked(pid, model or fallback_model, prompt_tokens, completion_tokens, total_tokens)
    return build_usage_masked(pid or "local_companion", model or "unknown-model", 0, 0, 0)


def provider_order_for_run(context: Dict[str, Any], metadata: Dict[str, Any]) -> list[str]:
    requested_order = [item.strip().lower() for item in str(os.getenv("ORION_LOCAL_WORKER_PROVIDER_ORDER") or "").split(",") if item.strip()]
    requested_order = [item for item in requested_order if item in SUPPORTED_PROVIDERS]

    provider_hint = str(os.getenv("ORION_LOCAL_WORKER_PROVIDER") or "").strip().lower()
    auth_mode = _openai_auth_mode()
    use_codex_cli = str(os.getenv("ORION_LOCAL_WORKER_USE_CODEX_CLI", "1")).strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    prefer_direct_openai = str(os.getenv("ORION_LOCAL_WORKER_PREFER_DIRECT_OPENAI", "1")).strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    context_provider = resolve_requested_provider(context, metadata)
    explicit_requested_provider = context_provider if context_provider in SUPPORTED_PROVIDERS else ""
    explicit_requested_provider_has_credentials = bool(
        explicit_requested_provider
        and provider_has_usable_credentials(explicit_requested_provider, context, metadata)
    )
    run_source = str(metadata.get("source") or context.get("source") or "").strip().lower()
    # ── No-fallback is the DEFAULT (ONE AI ROAD. NO FALLBACK. EVER.) ──
    # To enable provider fallback, the caller must explicitly set
    # enable_provider_fallback: True in context or metadata, OR set the
    # env var ORION_LOCAL_WORKER_PROVIDER_FALLBACK=1.
    disable_fallback = str(
        metadata.get("disable_provider_fallback")
        or context.get("disable_provider_fallback")
        or "true"  # <-- DEFAULT: no fallback
    ).strip().lower() in {"1", "true", "yes", "on"}
    configured_fallback_provider = str(
        metadata.get("fallback_provider")
        or context.get("fallback_provider")
        or os.getenv("ORION_LOCAL_WORKER_FALLBACK_PROVIDER")
        or ""
    ).strip().lower()

    base = list(requested_order)
    if provider_hint and provider_hint != "auto" and provider_hint in SUPPORTED_PROVIDERS and provider_hint not in base:
        base.insert(0, provider_hint)
    if context_provider in SUPPORTED_PROVIDERS and context_provider not in base:
        base.insert(0, context_provider)
    if explicit_requested_provider_has_credentials:
        base = [explicit_requested_provider] + [pid for pid in base if pid != explicit_requested_provider]

    for pid in SUPPORTED_PROVIDERS:
        if pid not in base:
            base.append(pid)
    if (
        auth_mode == "codex"
        and not explicit_requested_provider_has_credentials
        and run_source not in {"chat_direct"}
        and use_codex_cli
        and "codex_cli" in base
        and provider_has_usable_credentials("codex_cli", context, metadata)
    ):
        if run_source in {"telegram_autopilot", "whatsapp_autopilot"}:
            return ["codex_cli"]
        base = ["codex_cli"] + [pid for pid in base if pid != "codex_cli"]
        if prefer_direct_openai and "openai" in base and provider_has_usable_credentials("openai", context, metadata):
            openai_index = base.index("openai")
            if openai_index > 1:
                base.pop(openai_index)
                base.insert(1, "openai")

    fallback_enabled = str(os.getenv("ORION_LOCAL_WORKER_PROVIDER_FALLBACK", "0")).strip().lower() not in {"0", "false", "no", "off"}
    if disable_fallback and context_provider in SUPPORTED_PROVIDERS:
        return [context_provider] if provider_has_usable_credentials(context_provider, context, metadata) else []
    if provider_hint and provider_hint != "auto" and provider_hint in SUPPORTED_PROVIDERS and not fallback_enabled:
        return [provider_hint] if provider_has_usable_credentials(provider_hint, context, metadata) else []

    available = [pid for pid in base if provider_has_usable_credentials(pid, context, metadata)]
    if fallback_enabled and configured_fallback_provider in available and configured_fallback_provider in SUPPORTED_PROVIDERS:
        available = [pid for pid in available if pid != configured_fallback_provider]
        insert_index = 1 if available else 0
        if context_provider and context_provider in available:
            insert_index = available.index(context_provider) + 1
        available.insert(insert_index, configured_fallback_provider)

    # ── NO-FALLBACK GUARD (DEFAULT) ──
    # When fallback is disabled, return at most ONE provider.  This is
    # the core enforcement of "ONE AI ROAD. NO FALLBACK. EVER." — the
    # calling code iterates over the list and tries each provider, so
    # a single-element list means no switching on failure.
    if not fallback_enabled or disable_fallback:
        return available[:1]

    return available


def _log_provider_override(
    requested_provider: str,
    effective_provider: str,
    *,
    attempted: List[str],
    reason: str,
) -> None:
    requested = str(requested_provider or "").strip().lower()
    effective = str(effective_provider or "").strip().lower()
    if not requested or not effective or requested == effective:
        return
    LOGGER.warning(
        "Requested provider overridden: requested=%s effective=%s reason=%s attempted=%s",
        requested,
        effective,
        reason,
        ",".join(str(item or "").strip().lower() for item in attempted if str(item or "").strip()),
    )


def generate_pack_with_provider_fallback(
    context: Dict[str, Any],
    metadata: Dict[str, Any],
    system_prompt: str,
    user_prompt: str,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], str, str]:
    attempted: list[str] = []
    last_error = "no provider credentials available"
    requested_provider = resolve_requested_provider(context, metadata)
    requested_model = resolve_requested_model(context, metadata)
    credential_override = metadata.get("credentials") if isinstance(metadata.get("credentials"), dict) else None

    def _run_json_with_limits(
        provider_id: str,
        provider_model: Optional[str],
        call_fn: Any,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], str, str]:
        policy = _provider_limit_policy(provider_id, provider_model)
        max_attempts = max(1, to_int(policy.get("max_retry_attempts"), 1))
        last_result: Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], str, str] = (None, None, provider_model or "", "unknown_error")
        for attempt_index in range(max_attempts):
            result = call_fn()
            payload, usage, model, provider_error = result
            if isinstance(payload, dict):
                return payload, usage, model, ""
            last_result = result
            if not _should_retry_provider_error(
                provider_id,
                provider_error,
                attempt_index=attempt_index,
                max_attempts=max_attempts,
            ):
                return last_result
            _sleep_provider_retry_delay(
                provider_id,
                provider_error,
                attempt_index=attempt_index,
                model=model or provider_model,
            )
        return last_result

    for provider in provider_order_for_run(context, metadata):
        attempted.append(provider)
        _log_provider_override(
            requested_provider,
            provider,
            attempted=attempted,
            reason="provider_order_for_run",
        )
        provider_model = coerce_requested_model_for_provider(requested_model, provider)
        if provider == "codex_cli":
            result, usage, model, provider_error = _run_json_with_limits(
                provider,
                provider_model,
                lambda: codex_exec_json(
                    system_prompt,
                    user_prompt,
                    model_override=provider_model,
                    credential_override=credential_override,
                ),
            )
        elif provider == "claude_code_cli":
            result, usage, model, provider_error = _run_json_with_limits(
                provider,
                provider_model,
                lambda: anthropic_chat_json(
                    system_prompt,
                    user_prompt,
                    model_override=provider_model,
                    credential_override=credential_override,
                ),
            )
        elif provider == "openai":
            direct_auth_error = openai_direct_auth_error(context, metadata)
            if direct_auth_error:
                last_error = direct_auth_error
                continue
            result, usage, model, provider_error = _run_json_with_limits(
                provider,
                provider_model,
                lambda: openai_chat_json(
                    system_prompt,
                    user_prompt,
                    model_override=provider_model,
                    provider="openai",
                    credential_override=credential_override,
                ),
            )
        elif provider in {"qwen", "deepseek", "mistral"}:
            result, usage, model, provider_error = _run_json_with_limits(
                provider,
                provider_model,
                lambda: openai_chat_json(
                    system_prompt,
                    user_prompt,
                    model_override=provider_model,
                    provider=provider,
                    credential_override=credential_override,
                ),
            )
        elif provider == "anthropic":
            result, usage, model, provider_error = _run_json_with_limits(
                provider,
                provider_model,
                lambda: anthropic_chat_json(
                    system_prompt,
                    user_prompt,
                    model_override=provider_model,
                    credential_override=credential_override,
                ),
            )
        elif provider == "gemini":
            result, usage, model, provider_error = _run_json_with_limits(
                provider,
                provider_model,
                lambda: gemini_chat_json(
                    system_prompt,
                    user_prompt,
                    model_override=provider_model,
                    credential_override=credential_override,
                ),
            )
        elif provider == "ollama":
            result, usage, model, provider_error = _run_json_with_limits(
                provider,
                provider_model,
                lambda: ollama_chat_json(system_prompt, user_prompt, model_override=provider_model),
            )
        elif provider == "ollama_cloud":
            result, usage, model, provider_error = _run_json_with_limits(
                provider,
                provider_model,
                lambda: ollama_cloud_chat_json(
                    system_prompt,
                    user_prompt,
                    model_override=provider_model,
                    credential_override=credential_override,
                ),
            )
        else:
            continue
        if isinstance(result, dict):
            return result, build_usage_masked_from_provider(provider, usage, model), ",".join(attempted), ""
        if provider_error:
            last_error = f"{provider} generation failed: {provider_error}"
        else:
            last_error = f"{provider} generation failed"
    attempted_str = ",".join(attempted)
    return None, None, attempted_str, last_error


def generate_chat_reply_with_provider_fallback(
    context: Dict[str, Any],
    metadata: Dict[str, Any],
    user_goal: str,
    system_prompt: Optional[str],
    prior_messages: Any = None,
) -> Tuple[str, Optional[Dict[str, Any]], str, str]:
    attempted: list[str] = []
    last_error = "no provider credentials available"
    prefer_openai_chat = should_use_openai_chat_completions(context, metadata)
    requested_provider = resolve_requested_provider(context, metadata)
    requested_model = resolve_requested_model(context, metadata)
    requested_reasoning_effort = resolve_requested_reasoning_effort(context, metadata)
    credential_override = metadata.get("credentials") if isinstance(metadata.get("credentials"), dict) else None

    def _run_text_with_limits(
        provider_id: str,
        provider_model: Optional[str],
        call_fn: Any,
    ) -> Tuple[str, Optional[Dict[str, Any]], str, str]:
        policy = _provider_limit_policy(provider_id, provider_model)
        max_attempts = max(1, to_int(policy.get("max_retry_attempts"), 1))
        last_result: Tuple[str, Optional[Dict[str, Any]], str, str] = ("", None, provider_model or "", "unknown_error")
        for attempt_index in range(max_attempts):
            result = call_fn()
            text, usage, model, provider_error = result
            if text:
                return text, usage, model, ""
            last_result = result
            if not _should_retry_provider_error(
                provider_id,
                provider_error,
                attempt_index=attempt_index,
                max_attempts=max_attempts,
            ):
                return last_result
            _sleep_provider_retry_delay(
                provider_id,
                provider_error,
                attempt_index=attempt_index,
                model=model or provider_model,
            )
        return last_result

    for provider in provider_order_for_run(context, metadata):
        attempted.append(provider)
        _log_provider_override(
            requested_provider,
            provider,
            attempted=attempted,
            reason="provider_order_for_run",
        )
        provider_model = coerce_requested_model_for_provider(requested_model, provider)
        if provider == "codex_cli":
            text, usage, model, provider_error = _run_text_with_limits(
                provider,
                provider_model,
                lambda: openai_codex_backend_text(
                    system_prompt,
                    user_goal,
                    model_override=provider_model,
                    reasoning_effort_override=requested_reasoning_effort or None,
                    prior_messages=prior_messages,
                    credential_override=credential_override,
                ),
            )
            if text:
                return (
                    text,
                    build_usage_masked_from_provider("codex_cli", usage, model),
                    ",".join(attempted),
                    "",
                )
            last_error = (
                f"{DIRECT_CHAT_TRANSPORT_UNAVAILABLE}: codex_cli_backend_unavailable: "
                f"{provider_error or 'unknown_error'}"
            )
            continue
        if provider == "claude_code_cli":
            text, usage, model, provider_error = _run_text_with_limits(
                provider,
                provider_model,
                lambda: anthropic_chat_text(
                    system_prompt,
                    user_goal,
                    model_override=provider_model,
                    prior_messages=prior_messages,
                    credential_override=credential_override,
                ),
            )
            if text:
                return (
                    text,
                    build_usage_masked_from_provider("claude_code_cli", usage, model),
                    ",".join(attempted),
                    "",
                )
            last_error = f"claude_code_cli generation failed: {provider_error or 'unknown_error'}"
            continue
        if provider == "openai":
            direct_auth_error = openai_direct_auth_error(context, metadata)
            if direct_auth_error:
                last_error = f"openai generation failed: {direct_auth_error}"
                continue
            if not prefer_openai_chat:
                text, usage, model, provider_error = openai_responses_text(
                    system_prompt,
                    user_goal,
                    model_override=provider_model,
                    prior_messages=prior_messages,
                )
                if text:
                    return (
                        text,
                        build_usage_masked_from_provider("openai", usage, model),
                        ",".join(attempted),
                        "",
                    )
            else:
                provider_error = ""
            text, usage_chat, model_chat, provider_error_chat = _run_text_with_limits(
                provider,
                provider_model,
                lambda: openai_chat_text(
                    system_prompt,
                    user_goal,
                    model_override=provider_model,
                    prior_messages=prior_messages,
                    provider="openai",
                    credential_override=credential_override,
                ),
            )
            if text:
                return (
                    text,
                    build_usage_masked_from_provider("openai", usage_chat, model_chat),
                    ",".join(attempted),
                    "",
                )
            last_error = (
                f"openai generation failed: "
                f"{provider_error or provider_error_chat or 'unknown_error'}"
            )
            continue
        if provider in {"qwen", "deepseek", "mistral", "groq", "openrouter", "azure_openai", "custom_openai_compatible"}:
            text, usage, model, provider_error = _run_text_with_limits(
                provider,
                provider_model,
                lambda: openai_chat_text(
                    system_prompt,
                    user_goal,
                    model_override=provider_model,
                    prior_messages=prior_messages,
                    provider=provider,
                    credential_override=credential_override,
                ),
            )
            if text:
                return text, build_usage_masked_from_provider(provider, usage, model), ",".join(attempted), ""
            last_error = f"{provider} generation failed: {provider_error or 'unknown_error'}"
            continue
        if provider == "anthropic":
            text, usage, model, provider_error = _run_text_with_limits(
                provider,
                provider_model,
                lambda: anthropic_chat_text(
                    system_prompt,
                    user_goal,
                    model_override=provider_model,
                    prior_messages=prior_messages,
                    credential_override=credential_override,
                ),
            )
        elif provider == "gemini":
            text, usage, model, provider_error = _run_text_with_limits(
                provider,
                provider_model,
                lambda: gemini_chat_text(
                    system_prompt,
                    user_goal,
                    model_override=provider_model,
                    prior_messages=prior_messages,
                    credential_override=credential_override,
                ),
            )
        elif provider == "ollama":
            text, usage, model, provider_error = _run_text_with_limits(
                provider,
                provider_model,
                lambda: ollama_chat_text(
                    system_prompt,
                    user_goal,
                    model_override=provider_model,
                    prior_messages=prior_messages,
                ),
            )
        elif provider == "ollama_cloud":
            text, usage, model, provider_error = _run_text_with_limits(
                provider,
                provider_model,
                lambda: ollama_cloud_chat_text(
                    system_prompt,
                    user_goal,
                    model_override=provider_model,
                    prior_messages=prior_messages,
                    credential_override=credential_override,
                ),
            )
        else:
            continue
        if text:
            return text, build_usage_masked_from_provider(provider, usage, model), ",".join(attempted), ""
        if provider_error:
            last_error = f"{provider} generation failed: {provider_error}"
        else:
            last_error = f"{provider} generation failed"
    return "", None, ",".join(attempted), last_error


def generate_chat_reply_for_turn_request(
    *,
    turn_request: Any,
    context: Dict[str, Any],
    metadata: Dict[str, Any],
    system_prompt: Optional[str],
    prior_messages: Any = None,
) -> Tuple[str, Optional[Dict[str, Any]], str, str]:
    resolved = resolve_agent_turn_request(turn_request)
    if not isinstance(resolved, AgentTurnRequest):
        raise ValueError("A valid AgentTurnRequest is required for local worker chat dispatch.")

    next_context = dict(context or {})
    next_metadata = bind_agent_turn_metadata(
        dict(metadata or {}),
        resolved,
        source="local_worker",
    )
    next_context["metadata"] = next_metadata
    return generate_chat_reply_with_provider_fallback(
        context=next_context,
        metadata=next_metadata,
        user_goal=resolved.message,
        system_prompt=system_prompt,
        prior_messages=prior_messages,
    )


def generate_chat_reply_stream_with_provider_fallback(
    context: Dict[str, Any],
    metadata: Dict[str, Any],
    user_goal: str,
    system_prompt: Optional[str],
    prior_messages: Any = None,
) -> Iterator[Dict[str, Any]]:
    attempted: list[str] = []
    last_error = "no provider credentials available"
    prefer_openai_chat = should_use_openai_chat_completions(context, metadata)
    requested_provider = resolve_requested_provider(context, metadata)
    requested_model = resolve_requested_model(context, metadata)
    requested_reasoning_effort = resolve_requested_reasoning_effort(context, metadata)
    credential_override = metadata.get("credentials") if isinstance(metadata.get("credentials"), dict) else None
    requested_tools = resolve_requested_tools(context, metadata)

    def _run_text_with_limits(
        provider_id: str,
        provider_model: Optional[str],
        call_fn: Any,
    ) -> Tuple[str, Optional[Dict[str, Any]], str, str]:
        policy = _provider_limit_policy(provider_id, provider_model)
        max_attempts = max(1, to_int(policy.get("max_retry_attempts"), 1))
        last_result: Tuple[str, Optional[Dict[str, Any]], str, str] = ("", None, provider_model or "", "unknown_error")
        for attempt_index in range(max_attempts):
            result = call_fn()
            text, usage, model, provider_error = result
            if text:
                return text, usage, model, ""
            last_result = result
            if not _should_retry_provider_error(
                provider_id,
                provider_error,
                attempt_index=attempt_index,
                max_attempts=max_attempts,
            ):
                return last_result
            _sleep_provider_retry_delay(
                provider_id,
                provider_error,
                attempt_index=attempt_index,
                model=model or provider_model,
            )
        return last_result

    def _run_structured_with_limits(
        provider_id: str,
        provider_model: Optional[str],
        call_fn: Any,
    ) -> Dict[str, Any]:
        policy = _provider_limit_policy(provider_id, provider_model)
        max_attempts = max(1, to_int(policy.get("max_retry_attempts"), 1))
        last_result: Dict[str, Any] = {
            "ok": False,
            "text": "",
            "deltas": [],
            "usage": None,
            "model": provider_model or "",
            "tool_calls": [],
            "error": "unknown_error",
        }
        for attempt_index in range(max_attempts):
            result = call_fn()
            if bool(result.get("ok")):
                return result
            last_result = dict(result or {})
            provider_error = str(last_result.get("error") or "").strip()
            if not _should_retry_provider_error(
                provider_id,
                provider_error,
                attempt_index=attempt_index,
                max_attempts=max_attempts,
            ):
                return last_result
            _sleep_provider_retry_delay(
                provider_id,
                provider_error,
                attempt_index=attempt_index,
                model=str(last_result.get("model") or provider_model or "").strip() or provider_model,
            )
        return last_result

    for provider in provider_order_for_run(context, metadata):
        attempted.append(provider)
        attempted_str = ",".join(attempted)
        _log_provider_override(
            requested_provider,
            provider,
            attempted=attempted,
            reason="provider_order_for_run",
        )
        provider_model = coerce_requested_model_for_provider(requested_model, provider)

        if provider == "codex_cli":
            policy = _provider_limit_policy("codex_cli", provider_model)
            max_attempts = max(1, to_int(policy.get("max_retry_attempts"), 1))
            for attempt_index in range(max_attempts):
                streamed_parts: list[str] = []
                final_usage: Optional[Dict[str, Any]] = None
                final_model = provider_model
                provider_error = ""
                for event in iter_openai_codex_backend_events(
                    system_prompt,
                    user_goal,
                    model_override=provider_model,
                    reasoning_effort_override=requested_reasoning_effort or None,
                    prior_messages=prior_messages,
                    credential_override=credential_override,
                    tools=requested_tools,
                ):
                    event_type = str(event.get("type") or "").strip().lower()
                    if event_type == "delta":
                        delta = str(event.get("delta") or "")
                        if delta:
                            streamed_parts.append(delta)
                            yield {"type": "chunk", "delta": delta}
                        continue
                    if event_type == "done":
                        final_usage = event.get("usage") if isinstance(event.get("usage"), dict) else None
                        final_model = str(event.get("model") or final_model or "").strip() or final_model
                        tool_calls = event.get("tool_calls") if isinstance(event.get("tool_calls"), list) else []
                        final_text = str(event.get("text") or "").strip() or "".join(streamed_parts).strip()
                        if final_text or tool_calls:
                            yield {
                                "type": "result",
                                "reply": final_text,
                                "usage_masked": build_usage_masked_from_provider("codex_cli", final_usage, final_model),
                                "provider": "codex_cli",
                                "model": final_model,
                                "attempted_providers": attempted_str,
                                "error": "",
                                "tool_calls": tool_calls,
                            }
                            return
                        provider_error = "codex_empty_output"
                        break
                    if event_type == "error":
                        final_model = str(event.get("model") or final_model or "").strip() or final_model
                        provider_error = str(event.get("error") or "unknown_error").strip() or "unknown_error"
                        if streamed_parts:
                            yield {
                                "type": "result",
                                "reply": "".join(streamed_parts).strip(),
                                "usage_masked": build_usage_masked_from_provider("codex_cli", final_usage, final_model),
                                "provider": "codex_cli",
                                "model": final_model,
                                "attempted_providers": attempted_str,
                                "error": provider_error,
                            }
                            return
                        break
                if not provider_error:
                    provider_error = "unknown_error"
                last_error = f"{DIRECT_CHAT_TRANSPORT_UNAVAILABLE}: codex_cli_backend_unavailable: {provider_error}"
                if _should_retry_provider_error(
                    "codex_cli",
                    provider_error,
                    attempt_index=attempt_index,
                    max_attempts=max_attempts,
                ):
                    _sleep_provider_retry_delay(
                        "codex_cli",
                        provider_error,
                        attempt_index=attempt_index,
                        model=provider_model,
                    )
                    continue
                break
            continue

        if provider == "claude_code_cli":
            text, usage, model, provider_error = _run_text_with_limits(
                provider,
                provider_model,
                lambda: anthropic_chat_text(
                    system_prompt,
                    user_goal,
                    model_override=provider_model,
                    prior_messages=prior_messages,
                    credential_override=credential_override,
                ),
            )
            if text:
                yield {"type": "chunk", "delta": text}
                yield {
                    "type": "result",
                    "reply": text,
                    "usage_masked": build_usage_masked_from_provider("claude_code_cli", usage, model),
                    "provider": "claude_code_cli",
                    "model": model,
                    "attempted_providers": attempted_str,
                    "error": "",
                }
                return
            last_error = f"claude_code_cli generation failed: {provider_error or 'unknown_error'}"
            continue

        if provider == "openai":
            direct_auth_error = openai_direct_auth_error(context, metadata)
            if direct_auth_error:
                last_error = f"openai generation failed: {direct_auth_error}"
                continue
            if requested_tools:
                provider_result = _run_structured_with_limits(
                    "openai",
                    provider_model,
                    lambda: run_tool_capable_provider_with_prompt_fallback(
                        system_prompt=system_prompt,
                        stream_factory=lambda prompt_variant: iter_openai_compatible_chat_events(
                            prompt_variant,
                            user_goal,
                            model_override=provider_model,
                            prior_messages=prior_messages,
                            provider="openai",
                            credential_override=credential_override,
                            tools=requested_tools,
                        ),
                    ),
                )
                if provider_result.get("ok"):
                    for delta in list(provider_result.get("deltas") or []):
                        yield {"type": "chunk", "delta": str(delta)}
                    yield {
                        "type": "result",
                        "reply": str(provider_result.get("text") or ""),
                        "usage_masked": build_usage_masked_from_provider("openai", provider_result.get("usage"), str(provider_result.get("model") or provider_model or "").strip() or provider_model),
                        "provider": "openai",
                        "model": str(provider_result.get("model") or provider_model or "").strip() or provider_model,
                        "attempted_providers": attempted_str,
                        "error": "",
                        "tool_calls": provider_result.get("tool_calls") if isinstance(provider_result.get("tool_calls"), list) else [],
                    }
                    return
                last_error = f"openai generation failed: {str(provider_result.get('error') or 'unknown_error').strip() or 'unknown_error'}"
                continue
            text = ""
            usage: Optional[Dict[str, Any]] = None
            model = provider_model
            provider_error = ""
            if not prefer_openai_chat:
                text, usage, model, provider_error = _run_text_with_limits(
                    "openai",
                    provider_model,
                    lambda: openai_responses_text(
                        system_prompt,
                        user_goal,
                        model_override=provider_model,
                        prior_messages=prior_messages,
                    ),
                )
            if not text:
                text, usage, model, provider_error = _run_text_with_limits(
                    "openai",
                    provider_model,
                    lambda: openai_chat_text(
                        system_prompt,
                        user_goal,
                        model_override=provider_model,
                        prior_messages=prior_messages,
                        provider="openai",
                        credential_override=credential_override,
                    ),
                )
            if text:
                yield {"type": "chunk", "delta": text}
                yield {
                    "type": "result",
                    "reply": text,
                    "usage_masked": build_usage_masked_from_provider("openai", usage, model),
                    "provider": "openai",
                    "model": model,
                    "attempted_providers": attempted_str,
                    "error": "",
                }
                return
            last_error = f"openai generation failed: {provider_error or 'unknown_error'}"
            continue

        if provider in {"qwen", "deepseek", "mistral"}:
            if requested_tools:
                provider_result = _run_structured_with_limits(
                    provider,
                    provider_model,
                    lambda: run_tool_capable_provider_with_prompt_fallback(
                        system_prompt=system_prompt,
                        stream_factory=lambda prompt_variant: iter_openai_compatible_chat_events(
                            prompt_variant,
                            user_goal,
                            model_override=provider_model,
                            prior_messages=prior_messages,
                            provider=provider,
                            credential_override=credential_override,
                            tools=requested_tools,
                        ),
                    ),
                )
                if provider_result.get("ok"):
                    for delta in list(provider_result.get("deltas") or []):
                        yield {"type": "chunk", "delta": str(delta)}
                    yield {
                        "type": "result",
                        "reply": str(provider_result.get("text") or ""),
                        "usage_masked": build_usage_masked_from_provider(provider, provider_result.get("usage"), str(provider_result.get("model") or provider_model or "").strip() or provider_model),
                        "provider": provider,
                        "model": str(provider_result.get("model") or provider_model or "").strip() or provider_model,
                        "attempted_providers": attempted_str,
                        "error": "",
                        "tool_calls": provider_result.get("tool_calls") if isinstance(provider_result.get("tool_calls"), list) else [],
                    }
                    return
                last_error = f"{provider} generation failed: {str(provider_result.get('error') or 'unknown_error').strip() or 'unknown_error'}"
                continue
            text, usage, model, provider_error = _run_text_with_limits(
                provider,
                provider_model,
                lambda: openai_chat_text(
                    system_prompt,
                    user_goal,
                    model_override=provider_model,
                    prior_messages=prior_messages,
                    provider=provider,
                    credential_override=credential_override,
                ),
            )
            if text:
                yield {"type": "chunk", "delta": text}
                yield {
                    "type": "result",
                    "reply": text,
                    "usage_masked": build_usage_masked_from_provider(provider, usage, model),
                    "provider": provider,
                    "model": model,
                    "attempted_providers": attempted_str,
                    "error": "",
                }
                return
            last_error = f"{provider} generation failed: {provider_error or 'unknown_error'}"
            continue

        if provider == "anthropic":
            if requested_tools:
                provider_result = _run_structured_with_limits(
                    "anthropic",
                    provider_model,
                    lambda: run_tool_capable_provider_with_prompt_fallback(
                        system_prompt=system_prompt,
                        stream_factory=lambda prompt_variant: iter_anthropic_chat_events(
                            prompt_variant,
                            user_goal,
                            model_override=provider_model,
                            prior_messages=prior_messages,
                            credential_override=credential_override,
                            tools=requested_tools,
                        ),
                    ),
                )
                if provider_result.get("ok"):
                    for delta in list(provider_result.get("deltas") or []):
                        yield {"type": "chunk", "delta": str(delta)}
                    yield {
                        "type": "result",
                        "reply": str(provider_result.get("text") or ""),
                        "usage_masked": build_usage_masked_from_provider("anthropic", provider_result.get("usage"), str(provider_result.get("model") or provider_model or "").strip() or provider_model),
                        "provider": "anthropic",
                        "model": str(provider_result.get("model") or provider_model or "").strip() or provider_model,
                        "attempted_providers": attempted_str,
                        "error": "",
                        "tool_calls": provider_result.get("tool_calls") if isinstance(provider_result.get("tool_calls"), list) else [],
                    }
                    return
                last_error = f"anthropic generation failed: {str(provider_result.get('error') or 'unknown_error').strip() or 'unknown_error'}"
                continue
            text, usage, model, provider_error = _run_text_with_limits(
                "anthropic",
                provider_model,
                lambda: anthropic_chat_text(
                    system_prompt,
                    user_goal,
                    model_override=provider_model,
                    prior_messages=prior_messages,
                    credential_override=credential_override,
                ),
            )
        elif provider == "gemini":
            if requested_tools:
                provider_result = _run_structured_with_limits(
                    "gemini",
                    provider_model,
                    lambda: run_tool_capable_provider_with_prompt_fallback(
                        system_prompt=system_prompt,
                        stream_factory=lambda prompt_variant: iter_gemini_chat_events(
                            prompt_variant,
                            user_goal,
                            model_override=provider_model,
                            prior_messages=prior_messages,
                            tools=requested_tools,
                            credential_override=credential_override,
                        ),
                    ),
                )
                if provider_result.get("ok"):
                    for delta in list(provider_result.get("deltas") or []):
                        yield {"type": "chunk", "delta": str(delta)}
                    yield {
                        "type": "result",
                        "reply": str(provider_result.get("text") or ""),
                        "usage_masked": build_usage_masked_from_provider("gemini", provider_result.get("usage"), str(provider_result.get("model") or provider_model or "").strip() or provider_model),
                        "provider": "gemini",
                        "model": str(provider_result.get("model") or provider_model or "").strip() or provider_model,
                        "attempted_providers": attempted_str,
                        "error": "",
                        "tool_calls": provider_result.get("tool_calls") if isinstance(provider_result.get("tool_calls"), list) else [],
                    }
                    return
                last_error = f"gemini generation failed: {str(provider_result.get('error') or 'unknown_error').strip() or 'unknown_error'}"
                continue
            text, usage, model, provider_error = _run_text_with_limits(
                "gemini",
                provider_model,
                lambda: gemini_chat_text(
                    system_prompt,
                    user_goal,
                    model_override=provider_model,
                    prior_messages=prior_messages,
                    credential_override=credential_override,
                ),
            )
        elif provider == "ollama":
            if requested_tools:
                provider_result = _run_structured_with_limits(
                    "ollama",
                    provider_model,
                    lambda: run_tool_capable_provider_with_prompt_fallback(
                        system_prompt=system_prompt,
                        stream_factory=lambda prompt_variant: iter_ollama_chat_events(
                            prompt_variant,
                            user_goal,
                            model_override=provider_model,
                            prior_messages=prior_messages,
                            tools=requested_tools,
                        ),
                    ),
                )
                if provider_result.get("ok"):
                    for delta in list(provider_result.get("deltas") or []):
                        yield {"type": "chunk", "delta": str(delta)}
                    yield {
                        "type": "result",
                        "reply": str(provider_result.get("text") or ""),
                        "usage_masked": build_usage_masked_from_provider("ollama", provider_result.get("usage"), str(provider_result.get("model") or provider_model or "").strip() or provider_model),
                        "provider": "ollama",
                        "model": str(provider_result.get("model") or provider_model or "").strip() or provider_model,
                        "attempted_providers": attempted_str,
                        "error": "",
                        "tool_calls": provider_result.get("tool_calls") if isinstance(provider_result.get("tool_calls"), list) else [],
                    }
                    return
                last_error = f"ollama generation failed: {str(provider_result.get('error') or 'unknown_error').strip() or 'unknown_error'}"
                continue
            text, usage, model, provider_error = _run_text_with_limits(
                "ollama",
                provider_model,
                lambda: ollama_chat_text(
                    system_prompt,
                    user_goal,
                    model_override=provider_model,
                    prior_messages=prior_messages,
                ),
            )
        elif provider == "ollama_cloud":
            if requested_tools:
                provider_result = _run_structured_with_limits(
                    "ollama_cloud",
                    provider_model,
                    lambda: run_tool_capable_provider_with_prompt_fallback(
                        system_prompt=system_prompt,
                        stream_factory=lambda prompt_variant: iter_ollama_cloud_chat_events(
                            prompt_variant,
                            user_goal,
                            model_override=provider_model,
                            prior_messages=prior_messages,
                            credential_override=credential_override,
                            tools=requested_tools,
                        ),
                    ),
                )
                if provider_result.get("ok"):
                    for delta in list(provider_result.get("deltas") or []):
                        yield {"type": "chunk", "delta": str(delta)}
                    yield {
                        "type": "result",
                        "reply": str(provider_result.get("text") or ""),
                        "usage_masked": build_usage_masked_from_provider("ollama_cloud", provider_result.get("usage"), str(provider_result.get("model") or provider_model or "").strip() or provider_model),
                        "provider": "ollama_cloud",
                        "model": str(provider_result.get("model") or provider_model or "").strip() or provider_model,
                        "attempted_providers": attempted_str,
                        "error": "",
                        "tool_calls": provider_result.get("tool_calls") if isinstance(provider_result.get("tool_calls"), list) else [],
                    }
                    return
                last_error = f"ollama_cloud generation failed: {str(provider_result.get('error') or 'unknown_error').strip() or 'unknown_error'}"
                continue
            text, usage, model, provider_error = _run_text_with_limits(
                "ollama_cloud",
                provider_model,
                lambda: ollama_cloud_chat_text(
                    system_prompt,
                    user_goal,
                    model_override=provider_model,
                    prior_messages=prior_messages,
                    credential_override=credential_override,
                ),
            )
        else:
            continue
        if text:
            yield {"type": "chunk", "delta": text}
            yield {
                "type": "result",
                "reply": text,
                "usage_masked": build_usage_masked_from_provider(provider, usage, model),
                "provider": provider,
                "model": model,
                "attempted_providers": attempted_str,
                "error": "",
            }
            return
        if provider_error:
            last_error = f"{provider} generation failed: {provider_error}"
        else:
            last_error = f"{provider} generation failed"
    yield {
        "type": "failure",
        "attempted_providers": ",".join(attempted),
        "error": last_error,
    }
