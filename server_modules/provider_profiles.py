"""
Provider profile management, credential candidate resolution, and LLM provider adapters.

Extracted from server.py to reduce hotspot size.
All function signatures and behaviour are unchanged.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import base64
import hashlib
import sys
from functools import lru_cache
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.parse import quote, quote_plus

from server_modules import platform_config_schema, pricing_registry_service, secrets_broker, usage_accounting_service

# ---------------------------------------------------------------------------
# Imports from server.py globals – these must be supplied by the caller or
# looked-up at import time through the parent module.
# ---------------------------------------------------------------------------
# We import lazily from the parent server module to avoid circular imports.
# At module load time we cache the references we need.

_server = None  # populated by _init()


def _init():
    """Late-bind references to server.py globals.  Called once from __init__.py."""
    global _server
    if _server is not None:
        return
    import server as _s
    _server = _s


def _utc_now():
    _init()
    return _server._utc_now()


def _utc_now_iso():
    _init()
    return _server._utc_now_iso()


def _parse_utc_ts(raw):
    _init()
    return _server._parse_utc_ts(raw)


def _safe_write_json(path, payload):
    _init()
    return _server._safe_write_json(path, payload)


def _safe_read_json(path, fallback):
    _init()
    return _server._safe_read_json(path, fallback)


def _openai_bearer_from_credentials(credentials):
    _init()
    return _server._openai_bearer_from_credentials(credentials)


def _openai_env_bearer_with_source():
    _init()
    return _server._openai_env_bearer_with_source()


def http_json_request(url, **kwargs):
    _init()
    return _server.http_json_request(url, **kwargs)


def resolve_vault_credential(credential_id, workspace_id=None, **scope):
    _init()
    return _server.resolve_vault_credential(credential_id, workspace_id, **scope)


def resolve_default_vault_credential(provider, workspace_id=None, **scope):
    _init()
    return _server.resolve_default_vault_credential(provider, workspace_id, **scope)


def _validation_message(provider_label: str, response: Dict[str, Any]) -> str:
    status = int(response.get("status") or 500)
    payload = response.get("json")
    detail = ""
    if isinstance(payload, dict):
        error_obj = payload.get("error")
        if isinstance(error_obj, dict):
            detail = str(
                error_obj.get("message")
                or error_obj.get("detail")
                or error_obj.get("error")
                or ""
            ).strip()
        elif isinstance(error_obj, str):
            detail = error_obj.strip()
        if not detail:
            detail = str(
                payload.get("message")
                or payload.get("detail")
                or payload.get("error_description")
                or payload.get("title")
                or ""
            ).strip()
    if not detail:
        detail = str(response.get("text") or "").strip()
    if status == 200:
        return f"{provider_label} credential is valid."
    return detail or f"{provider_label} credential could not be verified (status {status})."


def _validation_result(provider_label: str, response: Dict[str, Any]) -> Dict[str, Any]:
    status = int(response.get("status") or 500)
    return {
        "ok": status == 200,
        "status": status,
        "message": _validation_message(provider_label, response),
    }


def _curl_json_request(
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    payload: Optional[Any] = None,
    method: Optional[str] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    command = [
        "curl",
        "-sS",
        "-L",
        "-X",
        (method or ("POST" if payload is not None else "GET")).upper(),
        "--max-time",
        str(max(1, int(timeout))),
        "-w",
        "\n__CODEX_STATUS__:%{http_code}",
    ]
    for key, value in dict(headers or {}).items():
        command.extend(["-H", f"{key}: {value}"])
    body: Optional[bytes] = None
    if payload is not None:
        if isinstance(payload, (bytes, bytearray)):
            body = bytes(payload)
        elif dict(headers or {}).get("Content-Type") == "application/x-www-form-urlencoded":
            if isinstance(payload, str):
                body = payload.encode("utf-8")
            else:
                from urllib.parse import urlencode as _urlencode

                body = _urlencode(payload, doseq=True).encode("utf-8")
        else:
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
    parsed: Any = None
    try:
        parsed = json.loads(body_text) if body_text else None
    except Exception:
        parsed = None
    return {
        "status": status_code,
        "text": body_text,
        "json": parsed,
        "headers": {},
    }


def _anthropic_models_request(credentials: Dict[str, Any], *, timeout: int = 30) -> Dict[str, Any]:
    url = "https://api.anthropic.com/v1/models"
    headers = {
        "x-api-key": str(credentials.get("api_key") or ""),
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    try:
        return http_json_request(url, headers=headers, timeout=timeout)
    except Exception:
        if not shutil.which("curl"):
            raise
        return _curl_json_request(url, headers=headers, timeout=timeout)


def ollama_local_status(*, timeout: int = 5) -> Dict[str, Any]:
    base_url = str(
        os.getenv("ORION_LOCAL_WORKER_OLLAMA_URL")
        or "http://127.0.0.1:11434"
    ).rstrip("/")
    url = f"{base_url}/api/tags"
    try:
        response = http_json_request(url, timeout=timeout)
    except Exception:
        if not shutil.which("curl"):
            return {"reachable": False, "has_models": False, "models": [], "error": "service_unreachable"}
        try:
            response = _curl_json_request(url, timeout=timeout)
        except Exception:
            return {"reachable": False, "has_models": False, "models": [], "error": "service_unreachable"}
    payload = response.get("json") if isinstance(response.get("json"), dict) else {}
    items = payload.get("models") if isinstance(payload.get("models"), list) else []
    models: List[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("model") or "").strip()
        if name:
            models.append(name)
    return {
        "reachable": int(response.get("status") or 0) == 200,
        "has_models": bool(models),
        "models": sorted(set(models)),
        "error": "",
    }


_LOCAL_GATEWAY_FALLBACK_ENVS = {"dev", "development", "local", "test", "testing"}


def _local_gateway_workspace_fallback_enabled() -> bool:
    token = (
        os.getenv("ORION_ENV")
        or os.getenv("ENV")
        or os.getenv("NODE_ENV")
        or ""
    ).strip().lower()
    return token in _LOCAL_GATEWAY_FALLBACK_ENVS


def _workspace_live_gateway_available_for_id(
    workspace_id: str,
    *,
    gateway_protocol_service: Any,
    gateway_state_repository: Any,
) -> bool:
    normalized_workspace_id = str(workspace_id or "default").strip() or "default"
    try:
        registrations = gateway_state_repository.list_workspace_gateway_registrations(
            normalized_workspace_id,
            include_revoked=False,
        )
    except Exception:
        return False

    for registration in registrations:
        if not isinstance(registration, dict):
            continue
        gateway_id = str(registration.get("gateway_id") or "").strip()
        if not gateway_id:
            continue
        if str(registration.get("status") or "").strip().lower() != "active":
            continue
        if gateway_protocol_service.gateway_connection_is_live(gateway_id):
            return True
    return False


def workspace_live_gateway_available(workspace_id: str) -> bool:
    try:
        from server_modules import gateway_protocol_service, gateway_state_repository
    except Exception:
        return False

    normalized_workspace_id = str(workspace_id or "default").strip() or "default"
    if _workspace_live_gateway_available_for_id(
        normalized_workspace_id,
        gateway_protocol_service=gateway_protocol_service,
        gateway_state_repository=gateway_state_repository,
    ):
        return True
    if normalized_workspace_id != "default" and _local_gateway_workspace_fallback_enabled():
        return _workspace_live_gateway_available_for_id(
            "default",
            gateway_protocol_service=gateway_protocol_service,
            gateway_state_repository=gateway_state_repository,
        )
    return False


def _openai_credential_type(credentials: Optional[Dict[str, Any]]) -> str:
    payload = credentials if isinstance(credentials, dict) else {}
    explicit = str(payload.get("credential_type") or "").strip().lower()
    if explicit in {"api_key", "oauth_token", "codex_token"}:
        return explicit
    token = str(payload.get("oauth_token") or payload.get("access_token") or "").strip()
    if str(payload.get("account_id") or "").strip():
        return "codex_token"
    if token and codex_account_id_from_token(token):
        return "codex_token"
    if str(payload.get("api_key") or "").strip():
        return "api_key"
    if str(payload.get("oauth_token") or "").strip():
        return "oauth_token"
    if str(payload.get("access_token") or "").strip():
        return "oauth_token"
    return ""


def _credential_type_from_openai_env_source(source: Any) -> str:
    normalized = str(source or "").strip().lower()
    if normalized in {"env_codex_oauth_token", "codex_token_vault"}:
        return "codex_token"
    if normalized == "env_api_key":
        return "api_key"
    if normalized in {"env_oauth_token", "env_access_token"}:
        return "oauth_token"
    return "oauth_token"


def _openai_env_credentials(token: str, source: Any) -> Dict[str, Any]:
    sanitized = str(token or "").strip()
    if not sanitized:
        return {}
    credential_type = _credential_type_from_openai_env_source(source)
    credentials: Dict[str, Any] = {
        "credential_type": credential_type,
        "org_id": OPENAI_ORG_ID,
        "project_id": OPENAI_PROJECT_ID,
    }
    if credential_type == "api_key":
        credentials["api_key"] = sanitized
        credentials["auth_mode"] = "api_key"
    elif credential_type == "codex_token":
        credentials["oauth_token"] = sanitized
        credentials["auth_mode"] = "oauth_token"
    else:
        credentials["access_token"] = sanitized
        credentials["auth_mode"] = "access_token"
    return credentials


def _resolve_hosted_provider_api_key(
    *,
    provider_id: str,
    tenant_id: Optional[str],
    workspace_id: Optional[str],
    run_id: Optional[str],
    purpose: str,
) -> tuple[str, str]:
    resolution = secrets_broker.resolve_hosted_provider_secret(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        provider_id=provider_id,
        field="api_key",
        tool_name="provider_profiles",
        run_id=run_id,
        actor_type="provider_profile",
        purpose=purpose,
    )
    if provider_id == "ollama_cloud" and not str(resolution.value or "").strip():
        env_key = str(
            os.getenv("ORION_LOCAL_WORKER_OLLAMA_CLOUD_API_KEY")
            or os.getenv("OLLAMA_API_KEY")
            or ""
        ).strip()
        if env_key:
            return env_key, "env-ollama_cloud"
    source = f"env-{provider_id}"
    if provider_id in {"qwen", "deepseek", "mistral", "ollama_cloud"} and str(resolution.source or "").startswith("bundle-"):
        source = f"env-{provider_id}"
    return str(resolution.value or "").strip(), source


def _resolve_hosted_openai_bearer(
    *,
    tenant_id: Optional[str],
    workspace_id: Optional[str],
    run_id: Optional[str],
    purpose: str,
) -> tuple[str, str]:
    fallback_token, fallback_source = _openai_env_bearer_with_source()
    resolution = secrets_broker.resolve_hosted_openai_bearer(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        fallback_token=fallback_token,
        fallback_source=fallback_source,
        tool_name="provider_profiles",
        run_id=run_id,
        actor_type="provider_profile",
        purpose=purpose,
    )
    return str(resolution.value or "").strip(), str(resolution.source or "").strip().lower() or "none"


# ---------------------------------------------------------------------------
# Provider catalog (moved from server.py)
# ---------------------------------------------------------------------------

LEGACY_PROVIDER_ALIASES = {
    "claude_code_cli": "anthropic",
    "openai_codex": "openai-codex",
}

PROVIDER_MODEL_ALIASES: Dict[str, Dict[str, str]] = {
    "anthropic": {
        "claude-3-7-sonnet-latest": "claude-3-7-sonnet-20250219",
        "claude-3-5-sonnet-20241022": "claude-3-7-sonnet-20250219",
    },
}

LOCAL_CLI_AUTH_MODES = {
    "local_cli",
    "local_subscription",
    "subscription_cli",
    "claude_code_cli",
}

PLATFORM_RUNTIME_AUTH_PROVIDERS = {"deepseek"}

WORKSPACE_USER_FACING_AI_PROVIDERS = {
    "openai",
    "openai-codex",
    "anthropic",
    "gemini",
    "vertex",
    "groq",
    "openrouter",
    "xai",
    "azure_openai",
    "bedrock",
    "qwen",
    "deepseek",
    "mistral",
    "custom_openai_compatible",
    "ollama_cloud",
}


PROVIDER_CATALOG = {
    "openai": {
        "label": "OpenAI",
        "auth": ["oauth_token", "access_token", "api_key"],
        "auth_modes": [
            {"id": "oauth_token", "label": "Saved OpenAI / Codex token", "secret_required": True},
            {"id": "access_token", "label": "OpenAI access token", "secret_required": True},
            {"id": "api_key", "label": "API Key", "secret_required": True},
        ],
        "default_auth_mode": "api_key",
        "default_model": "gpt-5.4",
        "models": ["gpt-5.5", "gpt-5.5-pro", "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-5.2", "gpt-5-mini", "gpt-5-nano", "gpt-4.1", "gpt-4.1-mini", "gpt-4o", "gpt-4o-mini"],
        "provider_scopes": ["sage_personal", "workspace_api", "studio_safe"],
        "note": "Direct OpenAI credentials only. Empyralis does not provide an in-product ChatGPT or Codex sign-in flow yet.",
    },
    "openai-codex": {
        "label": "OpenAI Codex",
        "auth": ["oauth_token"],
        "auth_modes": [
            {"id": "oauth_token", "label": "ChatGPT / Codex OAuth", "secret_required": True},
        ],
        "default_auth_mode": "oauth_token",
        "default_model": "gpt-5.4",
        "models": ["gpt-5.4", "gpt-5.3-codex", "gpt-5.2"],
        "provider_scopes": ["sage_personal"],
        "note": "ChatGPT / Codex subscription session through the user's Agent Computer.",
    },
    "anthropic": {
        "label": "Anthropic",
        "auth": ["api_key", "local_cli"],
        "auth_modes": [
            {"id": "api_key", "label": "API Key", "secret_required": True},
            {"id": "local_cli", "label": "Claude Subscription", "secret_required": False},
        ],
        "default_auth_mode": "api_key",
        "default_model": "claude-sonnet-4-6",
        "models": [
            "claude-opus-4-7",
            "claude-sonnet-4-6",
            "claude-haiku-4-5-20251001",
            "claude-sonnet-4-20250514",
            "claude-opus-4-1-20250805",
            "claude-3-7-sonnet-20250219",
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022",
        ],
        "provider_scopes": ["sage_personal", "workspace_api", "studio_safe"],
        "note": "Use a direct Anthropic API key or the local Claude subscription already signed into the Claude CLI on this machine.",
    },
    "claude_code_cli": {
        "label": "Claude Code (Subscription)",
        "auth": ["local_subscription"],
        "auth_modes": [
            {"id": "local_cli", "label": "Claude Subscription", "secret_required": False},
        ],
        "default_auth_mode": "local_cli",
        "default_model": "sonnet",
        "alias_for": "anthropic",
        "hidden": True,
        "provider_scopes": ["sage_personal", "local_only", "hidden"],
    },
    "gemini": {
        "label": "Google Gemini",
        "auth": ["api_key", "gemini_cli_oauth"],
        "auth_modes": [
            {"id": "api_key", "label": "API Key", "secret_required": True},
            {"id": "gemini_cli_oauth", "label": "Gemini CLI OAuth", "secret_required": False},
        ],
        "default_auth_mode": "api_key",
        "default_model": "gemini-2.5-flash",
        "models": ["gemini-3-pro-preview", "gemini-3-flash-preview", "gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro", "gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-1.5-flash", "gemini-1.5-pro"],
        "provider_scopes": ["sage_personal", "workspace_api", "studio_safe"],
        "note": "Direct Gemini API key or Gemini CLI OAuth.",
    },
    "vertex": {
        "label": "Google Vertex AI",
        "auth": ["access_token", "project_id", "location"],
        "auth_modes": [
            {"id": "access_token", "label": "Access Token", "secret_required": True},
        ],
        "default_auth_mode": "access_token",
        "default_model": "gemini-2.5-flash",
        "models": ["gemini-3-pro-preview", "gemini-3-flash-preview", "gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro", "gemini-1.5-pro", "gemini-1.5-flash"],
        "provider_scopes": ["sage_personal", "workspace_api", "studio_safe"],
        "note": "Direct Vertex AI access token with project and region.",
    },
    "groq": {
        "label": "Groq",
        "auth": ["api_key"],
        "auth_modes": [
            {"id": "api_key", "label": "API Key", "secret_required": True},
        ],
        "default_auth_mode": "api_key",
        "default_model": "llama-3.3-70b-versatile",
        "base_url": "https://api.groq.com/openai/v1",
        "models": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"],
        "provider_scopes": ["sage_personal", "workspace_api", "studio_safe"],
        "note": "Direct Groq API key using Groq's OpenAI-compatible endpoint.",
    },
    "openrouter": {
        "label": "OpenRouter",
        "auth": ["api_key"],
        "auth_modes": [
            {"id": "api_key", "label": "API Key", "secret_required": True},
        ],
        "default_auth_mode": "api_key",
        "default_model": "openai/gpt-5.2",
        "base_url": "https://openrouter.ai/api/v1",
        "models": [
            "openai/gpt-5.5",
            "openai/gpt-5.5-pro",
            "openai/gpt-5.4",
            "openai/gpt-5.4-mini",
            "anthropic/claude-opus-4.7",
            "anthropic/claude-sonnet-4.6",
            "google/gemini-3-pro-preview",
            "google/gemini-3-flash-preview",
            "google/gemini-2.5-flash",
            "x-ai/grok-4",
            "deepseek/deepseek-chat",
            "mistralai/mistral-large-latest",
        ],
        "provider_scopes": ["sage_personal", "workspace_api", "studio_safe"],
        "note": "OpenRouter gives one API key for a broad model marketplace. Model pricing comes from OpenRouter when available.",
    },
    "xai": {
        "label": "xAI",
        "auth": ["api_key"],
        "auth_modes": [
            {"id": "api_key", "label": "API Key", "secret_required": True},
        ],
        "default_auth_mode": "api_key",
        "default_model": "grok-4",
        "base_url": "https://api.x.ai/v1",
        "models": ["grok-4", "grok-4-0709", "grok-4-latest", "grok-3"],
        "provider_scopes": ["sage_personal", "workspace_api", "studio_safe"],
        "note": "Direct xAI API key using xAI's OpenAI-compatible endpoint.",
    },
    "azure_openai": {
        "label": "Azure OpenAI",
        "auth": ["api_key"],
        "auth_modes": [
            {"id": "api_key", "label": "API Key", "secret_required": True},
        ],
        "default_auth_mode": "api_key",
        "default_model": "deployment-name",
        "models": ["deployment-name"],
        "provider_scopes": ["sage_personal", "workspace_api", "studio_safe"],
        "note": "Azure OpenAI is deployment-scoped. Connect with an Azure endpoint/base URL and API key.",
    },
    "bedrock": {
        "label": "Amazon Bedrock",
        "auth": ["api_key"],
        "auth_modes": [
            {"id": "api_key", "label": "AWS Access Key", "secret_required": True},
        ],
        "default_auth_mode": "api_key",
        "default_model": "anthropic.claude-3-5-sonnet-20241022-v2:0",
        "models": ["anthropic.claude-3-5-sonnet-20241022-v2:0", "amazon.nova-pro-v1:0"],
        "provider_scopes": ["sage_personal", "workspace_api"],
        "note": "Amazon Bedrock model availability depends on AWS region and account access.",
    },
    "qwen": {
        "label": "Qwen",
        "auth": ["api_key"],
        "auth_modes": [
            {"id": "api_key", "label": "API Key", "secret_required": True},
        ],
        "default_auth_mode": "api_key",
        "default_model": "qwen-plus",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen-plus", "qwen-turbo", "qwen-max"],
        "provider_scopes": ["sage_personal", "workspace_api", "studio_safe"],
        "note": "Direct Qwen API key using Alibaba DashScope's OpenAI-compatible endpoint.",
    },
    "deepseek": {
        "label": "DeepSeek",
        "auth": ["api_key"],
        "auth_modes": [
            {"id": "api_key", "label": "API Key", "secret_required": True},
        ],
        "default_auth_mode": "api_key",
        "default_model": "deepseek-chat",
        "base_url": "https://api.deepseek.com/v1",
        "models": ["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-chat", "deepseek-reasoner"],
        "provider_scopes": ["sage_personal", "workspace_api", "studio_safe"],
        "note": "Direct DeepSeek API key using the OpenAI-compatible endpoint.",
    },
    "mistral": {
        "label": "Mistral",
        "auth": ["api_key"],
        "auth_modes": [
            {"id": "api_key", "label": "API Key", "secret_required": True},
        ],
        "default_auth_mode": "api_key",
        "default_model": "mistral-large-latest",
        "base_url": "https://api.mistral.ai/v1",
        "models": ["mistral-large-latest", "mistral-medium-latest", "mistral-small-latest"],
        "provider_scopes": ["sage_personal", "workspace_api", "studio_safe"],
        "note": "Direct Mistral API key using the OpenAI-compatible endpoint.",
    },
    "ollama": {
        "label": "Ollama",
        "auth": ["none"],
        "auth_modes": [
            {"id": "none", "label": "No auth required", "secret_required": False},
        ],
        "default_auth_mode": "none",
        "default_model": "llama3.2",
        "base_url": "http://localhost:11434/v1",
        "models": ["llama3.2", "llama3", "mistral", "gemma", "phi3"],
        "provider_scopes": ["sage_personal", "studio_safe", "local_only"],
        "note": "Local Ollama endpoint on this machine. No credential is required.",
    },
    "ollama_cloud": {
        "label": "Ollama Cloud",
        "auth": ["api_key"],
        "auth_modes": [
            {"id": "api_key", "label": "API Key", "secret_required": True},
        ],
        "default_auth_mode": "api_key",
        "default_model": "gpt-oss:120b",
        "base_url": "https://ollama.com",
        "models": ["gpt-oss:120b", "gpt-oss:20b"],
        "provider_scopes": ["sage_personal", "workspace_api", "studio_safe"],
        "note": "Direct Ollama cloud API key. This is separate from local Ollama running through the paired computer.",
    },
    "custom_openai_compatible": {
        "label": "Custom OpenAI-compatible",
        "auth": ["api_key"],
        "auth_modes": [
            {"id": "api_key", "label": "API Key", "secret_required": True},
        ],
        "default_auth_mode": "api_key",
        "default_model": "",
        "models": [],
        "provider_scopes": ["sage_personal", "workspace_api", "studio_safe"],
        "note": "Use any OpenAI-compatible API by providing a base URL and API key.",
    },
}

PROVIDER_GOVERNANCE_CATALOG = {
    "openai": {
        "privacy_posture": "Managed API with vendor-hosted processing.",
        "jurisdiction": "United States",
        "residency": "Provider-managed cloud regions.",
        "enterprise_risk_note": "Review regional residency and vendor retention requirements before sending regulated data.",
        "capability_labels": ["Tools", "Fast", "Hosted API"],
        "local_self_hosted_compatible": False,
    },
    "openai-codex": {
        "privacy_posture": "Codex transport tied to a ChatGPT / Codex session on the user's Agent Computer.",
        "jurisdiction": "United States",
        "residency": "Provider-managed cloud regions.",
        "enterprise_risk_note": "Saved-session transports are machine-bound by default and require gateway presence, revocation, and audit controls.",
        "capability_labels": ["Reasoning", "Codex transport", "Agent Computer"],
        "local_self_hosted_compatible": False,
    },
    "anthropic": {
        "privacy_posture": "Managed API or local Claude subscription transport.",
        "jurisdiction": "United States",
        "residency": "Provider-managed cloud regions or local CLI session.",
        "enterprise_risk_note": "Validate whether tenant contracts require dedicated data controls beyond the shared hosted API.",
        "capability_labels": ["Reasoning", "Long context", "Tools"],
        "local_self_hosted_compatible": False,
    },
    "gemini": {
        "privacy_posture": "Managed Google-hosted API.",
        "jurisdiction": "United States",
        "residency": "Google-managed cloud regions.",
        "enterprise_risk_note": "Enterprise deployments should align Gemini usage with Google Cloud governance and data-processing terms.",
        "capability_labels": ["Fast", "Tools", "Multimodal"],
        "local_self_hosted_compatible": False,
    },
    "vertex": {
        "privacy_posture": "Managed Google Cloud API with project-scoped access.",
        "jurisdiction": "Google Cloud project region",
        "residency": "Selected Vertex project region.",
        "enterprise_risk_note": "Region pinning helps, but operators still need to align project policy, logging, and retention controls.",
        "capability_labels": ["Enterprise", "Tools", "Project-scoped"],
        "local_self_hosted_compatible": False,
    },
    "xai": {
        "privacy_posture": "Managed API with vendor-hosted processing.",
        "jurisdiction": "United States",
        "residency": "Provider-managed cloud regions.",
        "enterprise_risk_note": "Review regional residency and vendor retention requirements before sending regulated data.",
        "capability_labels": ["Reasoning", "Tools", "Hosted API"],
        "local_self_hosted_compatible": False,
    },
    "qwen": {
        "privacy_posture": "Managed third-party API.",
        "jurisdiction": "Alibaba Cloud regions",
        "residency": "Provider-managed cloud regions.",
        "enterprise_risk_note": "Review provider-region and sector-specific compliance requirements before routing regulated user data.",
        "capability_labels": ["Fast", "Hosted API"],
        "local_self_hosted_compatible": False,
    },
    "deepseek": {
        "privacy_posture": "Managed third-party API. DeepSeek's privacy policy says prompts, chat history, and uploaded content may be collected and stored by the provider.",
        "jurisdiction": "People's Republic of China",
        "residency": "DeepSeek states personal information it collects is stored on secure servers located in the PRC, with policy-governed cross-border transfers.",
        "enterprise_risk_note": "Enterprise and regulated buyers should review PRC data residency, prompt retention, and sector-specific compliance exposure before sending user conversations.",
        "capability_labels": ["Reasoning", "Cost-efficient", "Hosted API"],
        "local_self_hosted_compatible": False,
    },
    "mistral": {
        "privacy_posture": "Managed third-party API.",
        "jurisdiction": "European Union",
        "residency": "Provider-managed cloud regions.",
        "enterprise_risk_note": "Validate chosen hosting region and contractual controls if workloads require strict residency guarantees.",
        "capability_labels": ["Fast", "Long context", "Hosted API"],
        "local_self_hosted_compatible": False,
    },
    "ollama": {
        "privacy_posture": "Local or self-hosted inference on your own machine.",
        "jurisdiction": "Self-hosted / operator-controlled",
        "residency": "Local machine or self-hosted runtime.",
        "enterprise_risk_note": "Operational risk shifts to the operator because patching, logging, and perimeter controls are self-managed.",
        "capability_labels": ["Local", "Self-hosted", "Offline-capable", "Tools"],
        "local_self_hosted_compatible": True,
    },
    "ollama_cloud": {
        "privacy_posture": "Managed Ollama-hosted API using a workspace or runtime API key.",
        "jurisdiction": "Provider-managed cloud regions",
        "residency": "Provider-managed cloud regions.",
        "enterprise_risk_note": "Treat as a managed third-party inference API; keep local-only data on the gateway unless the user explicitly selects this cloud provider.",
        "capability_labels": ["Hosted API", "Tools", "Ollama"],
        "local_self_hosted_compatible": False,
    },
}

PROVIDER_MODEL_CATALOG = {
    "openai": {
        "gpt-5.5": {
            "label": "GPT-5.5",
            "context_window_tokens": 1050000,
            "supports_tools": True,
            "supports_reasoning": True,
            "reasoning_levels": ["none", "low", "medium", "high", "xhigh"],
            "capability_labels": ["Frontier", "Reasoning", "Tools", "Long context"],
        },
        "gpt-5.5-pro": {
            "label": "GPT-5.5 Pro",
            "context_window_tokens": 1050000,
            "supports_tools": True,
            "supports_reasoning": True,
            "reasoning_levels": ["none", "low", "medium", "high", "xhigh"],
            "capability_labels": ["Highest quality", "Reasoning", "Tools", "Long context"],
        },
        "gpt-5.4": {
            "label": "GPT-5.4",
            "context_window_tokens": 1050000,
            "supports_tools": True,
            "supports_reasoning": True,
            "reasoning_levels": ["none", "low", "medium", "high", "xhigh"],
            "capability_labels": ["Frontier", "Reasoning", "Tools", "Long context"],
        },
        "gpt-5.4-mini": {
            "label": "GPT-5.4 Mini",
            "context_window_tokens": 400000,
            "supports_tools": True,
            "supports_reasoning": True,
            "reasoning_levels": ["none", "low", "medium", "high", "xhigh"],
            "capability_labels": ["Balanced", "Fast", "Tools", "Computer use"],
        },
        "gpt-5.4-nano": {
            "label": "GPT-5.4 Nano",
            "context_window_tokens": 400000,
            "supports_tools": True,
            "supports_reasoning": True,
            "reasoning_levels": ["none", "low", "medium"],
            "capability_labels": ["Lowest cost", "Fast", "Tools"],
        },
        "gpt-5.2": {
            "label": "GPT-5.2",
            "context_window_tokens": 400000,
            "input_cost_per_1k_usd": 0.00175,
            "output_cost_per_1k_usd": 0.014,
            "supports_tools": True,
            "supports_reasoning": True,
            "reasoning_levels": ["none", "low", "medium", "high"],
            "capability_labels": ["Reasoning", "High quality", "Tools"],
        },
        "gpt-5-mini": {
            "label": "GPT-5 Mini",
            "context_window_tokens": 400000,
            "input_cost_per_1k_usd": 0.00025,
            "output_cost_per_1k_usd": 0.002,
            "supports_tools": True,
            "supports_reasoning": True,
            "reasoning_levels": ["none", "low", "medium"],
            "capability_labels": ["Low cost", "Fast", "Tools"],
        },
        "gpt-5-nano": {
            "label": "GPT-5 Nano",
            "context_window_tokens": 400000,
            "input_cost_per_1k_usd": 0.00005,
            "output_cost_per_1k_usd": 0.0004,
            "supports_tools": True,
            "supports_reasoning": True,
            "reasoning_levels": ["none", "low"],
            "capability_labels": ["Lowest cost", "Fast", "Tools"],
        },
        "gpt-4o": {
            "label": "GPT-4o",
            "context_window_tokens": 128000,
            "input_cost_per_1k_usd": 0.005,
            "output_cost_per_1k_usd": 0.015,
            "supports_tools": True,
            "supports_reasoning": True,
            "capability_labels": ["Balanced", "Tools", "Multimodal"],
        },
        "gpt-4o-mini": {
            "label": "GPT-4o Mini",
            "context_window_tokens": 128000,
            "input_cost_per_1k_usd": 0.00015,
            "output_cost_per_1k_usd": 0.0006,
            "supports_tools": True,
            "supports_reasoning": False,
            "capability_labels": ["Low cost", "Fast", "Tools"],
        },
        "gpt-4.1": {
            "label": "GPT-4.1",
            "context_window_tokens": 1000000,
            "input_cost_per_1k_usd": 0.002,
            "output_cost_per_1k_usd": 0.008,
            "supports_tools": True,
            "supports_reasoning": True,
            "capability_labels": ["Long context", "Tools", "High quality"],
        },
        "gpt-4.1-mini": {
            "label": "GPT-4.1 Mini",
            "context_window_tokens": 1000000,
            "input_cost_per_1k_usd": 0.0004,
            "output_cost_per_1k_usd": 0.0016,
            "supports_tools": True,
            "supports_reasoning": True,
            "capability_labels": ["Long context", "Fast", "Tools"],
        },
    },
    "openai-codex": {
        "gpt-5.4": {
            "label": "GPT-5.4",
            "context_window_tokens": 400000,
            "input_cost_per_1k_usd": 0.0,
            "output_cost_per_1k_usd": 0.0,
            "supports_tools": True,
            "supports_reasoning": True,
            "reasoning_levels": ["low", "medium", "high", "xhigh"],
            "capability_labels": ["Reasoning", "Codex", "High quality"],
        },
        "gpt-5.3-codex": {
            "label": "GPT-5.3 Codex",
            "context_window_tokens": 400000,
            "input_cost_per_1k_usd": 0.0,
            "output_cost_per_1k_usd": 0.0,
            "supports_tools": True,
            "supports_reasoning": True,
            "reasoning_levels": ["low", "medium", "high", "xhigh"],
            "capability_labels": ["Reasoning", "Codex", "Engineering"],
        },
        "gpt-5.2": {
            "label": "GPT-5.2",
            "context_window_tokens": 400000,
            "input_cost_per_1k_usd": 0.0,
            "output_cost_per_1k_usd": 0.0,
            "supports_tools": True,
            "supports_reasoning": True,
            "reasoning_levels": ["none", "low", "medium", "high", "xhigh"],
            "capability_labels": ["Reasoning", "Codex", "Balanced"],
        },
    },
    "anthropic": {
        "claude-opus-4-7": {
            "label": "Claude Opus 4.7",
            "context_window_tokens": 1000000,
            "supports_tools": True,
            "supports_vision": True,
            "supports_reasoning": True,
            "capability_labels": ["Highest quality", "Reasoning", "Tools", "Long context"],
        },
        "claude-sonnet-4-6": {
            "label": "Claude Sonnet 4.6",
            "context_window_tokens": 1000000,
            "supports_tools": True,
            "supports_vision": True,
            "supports_reasoning": True,
            "capability_labels": ["Balanced", "Reasoning", "Tools", "Long context"],
        },
        "claude-haiku-4-5-20251001": {
            "label": "Claude Haiku 4.5",
            "context_window_tokens": 200000,
            "supports_tools": True,
            "supports_vision": True,
            "supports_reasoning": True,
            "capability_labels": ["Fast", "Low cost", "Tools", "Vision"],
        },
        "claude-opus-4-1-20250805": {
            "label": "Claude Opus 4.1",
            "context_window_tokens": 200000,
            "input_cost_per_1k_usd": 0.015,
            "output_cost_per_1k_usd": 0.075,
            "supports_tools": True,
            "supports_reasoning": True,
            "capability_labels": ["Highest quality", "Reasoning", "Tools"],
        },
        "claude-sonnet-4-20250514": {
            "label": "Claude Sonnet 4",
            "context_window_tokens": 200000,
            "input_cost_per_1k_usd": 0.003,
            "output_cost_per_1k_usd": 0.015,
            "supports_tools": True,
            "supports_reasoning": True,
            "capability_labels": ["Reasoning", "Long context", "Tools"],
        },
        "claude-3-5-sonnet-20241022": {
            "label": "Claude 3.5 Sonnet",
            "context_window_tokens": 200000,
            "input_cost_per_1k_usd": 0.003,
            "output_cost_per_1k_usd": 0.015,
            "supports_tools": True,
            "supports_reasoning": True,
            "capability_labels": ["Reasoning", "Long context", "Tools"],
        },
        "claude-3-5-haiku-20241022": {
            "label": "Claude 3.5 Haiku",
            "context_window_tokens": 200000,
            "input_cost_per_1k_usd": 0.0008,
            "output_cost_per_1k_usd": 0.004,
            "supports_tools": True,
            "supports_reasoning": False,
            "capability_labels": ["Low cost", "Fast", "Tools"],
        },
        "claude-3-7-sonnet-20250219": {
            "label": "Claude 3.7 Sonnet",
            "context_window_tokens": 200000,
            "input_cost_per_1k_usd": 0.003,
            "output_cost_per_1k_usd": 0.015,
            "supports_tools": True,
            "supports_reasoning": True,
            "capability_labels": ["Reasoning", "Long context", "Tools"],
        },
    },
    "gemini": {
        "gemini-3-pro-preview": {
            "label": "Gemini 3 Pro Preview",
            "context_window_tokens": 1048576,
            "supports_tools": True,
            "supports_vision": True,
            "supports_json": True,
            "supports_reasoning": True,
            "capability_labels": ["Preview", "Reasoning", "Tools", "Multimodal"],
        },
        "gemini-3-flash-preview": {
            "label": "Gemini 3 Flash Preview",
            "context_window_tokens": 1048576,
            "supports_tools": True,
            "supports_vision": True,
            "supports_json": True,
            "supports_reasoning": True,
            "capability_labels": ["Preview", "Fast", "Reasoning", "Multimodal"],
        },
        "gemini-2.5-flash": {
            "label": "Gemini 2.5 Flash",
            "context_window_tokens": 1000000,
            "input_cost_per_1k_usd": 0.0003,
            "output_cost_per_1k_usd": 0.0025,
            "supports_tools": True,
            "supports_reasoning": True,
            "capability_labels": ["Fast", "Reasoning", "Tools", "Multimodal"],
        },
        "gemini-2.5-flash-lite": {
            "label": "Gemini 2.5 Flash-Lite",
            "context_window_tokens": 1048576,
            "supports_tools": True,
            "supports_vision": True,
            "supports_json": True,
            "supports_reasoning": True,
            "capability_labels": ["Lowest cost", "Fast", "Tools", "Multimodal"],
        },
        "gemini-2.5-pro": {
            "label": "Gemini 2.5 Pro",
            "context_window_tokens": 1000000,
            "input_cost_per_1k_usd": 0.00125,
            "output_cost_per_1k_usd": 0.01,
            "supports_tools": True,
            "supports_reasoning": True,
            "capability_labels": ["Reasoning", "High quality", "Tools", "Multimodal"],
        },
        "gemini-1.5-flash": {
            "label": "Gemini 1.5 Flash",
            "context_window_tokens": 1000000,
            "input_cost_per_1k_usd": 0.000075,
            "output_cost_per_1k_usd": 0.0003,
            "supports_tools": True,
            "supports_reasoning": False,
            "capability_labels": ["Low cost", "Fast", "Long context"],
        },
        "gemini-1.5-pro": {
            "label": "Gemini 1.5 Pro",
            "context_window_tokens": 2000000,
            "input_cost_per_1k_usd": 0.00125,
            "output_cost_per_1k_usd": 0.005,
            "supports_tools": True,
            "supports_reasoning": True,
            "capability_labels": ["Long context", "Multimodal", "Tools"],
        },
        "gemini-2.0-flash": {
            "label": "Gemini 2.0 Flash",
            "context_window_tokens": 1000000,
            "input_cost_per_1k_usd": 0.0001,
            "output_cost_per_1k_usd": 0.0004,
            "supports_tools": True,
            "supports_reasoning": True,
            "capability_labels": ["Fast", "Reasoning", "Tools"],
        },
        "gemini-2.0-flash-lite": {
            "label": "Gemini 2.0 Flash-Lite",
            "context_window_tokens": 1048576,
            "supports_tools": True,
            "supports_reasoning": False,
            "supports_vision": True,
            "supports_json": True,
            "capability_labels": ["Low cost", "Fast", "Multimodal"],
        },
    },
    "vertex": {
        "gemini-3-pro-preview": {
            "label": "Vertex Gemini 3 Pro Preview",
            "context_window_tokens": 1048576,
            "supports_tools": True,
            "supports_vision": True,
            "supports_json": True,
            "supports_reasoning": True,
            "capability_labels": ["Enterprise", "Preview", "Reasoning", "Tools"],
        },
        "gemini-3-flash-preview": {
            "label": "Vertex Gemini 3 Flash Preview",
            "context_window_tokens": 1048576,
            "supports_tools": True,
            "supports_vision": True,
            "supports_json": True,
            "supports_reasoning": True,
            "capability_labels": ["Enterprise", "Preview", "Fast", "Tools"],
        },
        "gemini-2.5-flash": {
            "label": "Vertex Gemini 2.5 Flash",
            "context_window_tokens": 1000000,
            "input_cost_per_1k_usd": 0.0003,
            "output_cost_per_1k_usd": 0.0025,
            "supports_tools": True,
            "supports_reasoning": True,
            "capability_labels": ["Enterprise", "Fast", "Tools"],
        },
        "gemini-2.5-flash-lite": {
            "label": "Vertex Gemini 2.5 Flash-Lite",
            "context_window_tokens": 1048576,
            "supports_tools": True,
            "supports_vision": True,
            "supports_json": True,
            "supports_reasoning": True,
            "capability_labels": ["Enterprise", "Lowest cost", "Fast", "Tools"],
        },
        "gemini-2.5-pro": {
            "label": "Vertex Gemini 2.5 Pro",
            "context_window_tokens": 1000000,
            "input_cost_per_1k_usd": 0.00125,
            "output_cost_per_1k_usd": 0.01,
            "supports_tools": True,
            "supports_reasoning": True,
            "capability_labels": ["Enterprise", "High quality", "Tools"],
        },
        "gemini-1.5-pro": {
            "label": "Vertex Gemini 1.5 Pro",
            "context_window_tokens": 2000000,
            "input_cost_per_1k_usd": 0.00125,
            "output_cost_per_1k_usd": 0.005,
            "supports_tools": True,
            "supports_reasoning": True,
            "capability_labels": ["Enterprise", "Long context", "Tools"],
        },
        "gemini-1.5-flash": {
            "label": "Vertex Gemini 1.5 Flash",
            "context_window_tokens": 1000000,
            "input_cost_per_1k_usd": 0.000075,
            "output_cost_per_1k_usd": 0.0003,
            "supports_tools": True,
            "supports_reasoning": False,
            "capability_labels": ["Enterprise", "Low cost", "Fast"],
        },
    },
    "groq": {
        "llama-3.3-70b-versatile": {
            "label": "Llama 3.3 70B Versatile",
            "context_window_tokens": 131072,
            "supports_tools": True,
            "supports_reasoning": False,
            "capability_labels": ["Fast", "Low latency", "Tools"],
        },
        "llama-3.1-8b-instant": {
            "label": "Llama 3.1 8B Instant",
            "context_window_tokens": 131072,
            "supports_tools": True,
            "supports_reasoning": False,
            "capability_labels": ["Lowest latency", "Low cost", "Tools"],
        },
    },
    "openrouter": {
        "openai/gpt-5.5": {
            "label": "GPT-5.5 via OpenRouter",
            "context_window_tokens": 1050000,
            "supports_tools": True,
            "supports_reasoning": True,
            "capability_labels": ["Frontier", "Reasoning", "OpenRouter"],
        },
        "openai/gpt-5.5-pro": {
            "label": "GPT-5.5 Pro via OpenRouter",
            "context_window_tokens": 1050000,
            "supports_tools": True,
            "supports_reasoning": True,
            "capability_labels": ["Highest quality", "Reasoning", "OpenRouter"],
        },
        "openai/gpt-5.4": {
            "label": "GPT-5.4 via OpenRouter",
            "context_window_tokens": 1050000,
            "supports_tools": True,
            "supports_reasoning": True,
            "capability_labels": ["Frontier", "Reasoning", "OpenRouter"],
        },
        "openai/gpt-5.4-mini": {
            "label": "GPT-5.4 Mini via OpenRouter",
            "context_window_tokens": 400000,
            "supports_tools": True,
            "supports_reasoning": True,
            "capability_labels": ["Balanced", "Fast", "OpenRouter"],
        },
        "anthropic/claude-opus-4.7": {
            "label": "Claude Opus 4.7 via OpenRouter",
            "context_window_tokens": 1000000,
            "supports_tools": True,
            "supports_reasoning": True,
            "capability_labels": ["Highest quality", "Reasoning", "OpenRouter"],
        },
        "anthropic/claude-sonnet-4.6": {
            "label": "Claude Sonnet 4.6 via OpenRouter",
            "context_window_tokens": 1000000,
            "supports_tools": True,
            "supports_reasoning": True,
            "capability_labels": ["Balanced", "Reasoning", "OpenRouter"],
        },
        "google/gemini-3-pro-preview": {
            "label": "Gemini 3 Pro Preview via OpenRouter",
            "context_window_tokens": 1048576,
            "supports_tools": True,
            "supports_reasoning": True,
            "capability_labels": ["Preview", "Multimodal", "OpenRouter"],
        },
        "google/gemini-3-flash-preview": {
            "label": "Gemini 3 Flash Preview via OpenRouter",
            "context_window_tokens": 1048576,
            "supports_tools": True,
            "supports_reasoning": True,
            "capability_labels": ["Preview", "Fast", "OpenRouter"],
        },
        "google/gemini-2.5-flash": {
            "label": "Gemini 2.5 Flash via OpenRouter",
            "context_window_tokens": 1000000,
            "supports_tools": True,
            "supports_reasoning": True,
            "capability_labels": ["Fast", "Multimodal", "OpenRouter"],
        },
        "x-ai/grok-4": {
            "label": "Grok 4 via OpenRouter",
            "context_window_tokens": 256000,
            "supports_tools": True,
            "supports_reasoning": True,
            "capability_labels": ["Reasoning", "OpenRouter"],
        },
        "deepseek/deepseek-chat": {
            "label": "DeepSeek Chat via OpenRouter",
            "context_window_tokens": 128000,
            "supports_tools": True,
            "supports_reasoning": False,
            "capability_labels": ["Low cost", "OpenRouter"],
        },
        "mistralai/mistral-large-latest": {
            "label": "Mistral Large via OpenRouter",
            "context_window_tokens": 131072,
            "supports_tools": True,
            "supports_reasoning": True,
            "capability_labels": ["Reasoning", "OpenRouter"],
        },
    },
    "xai": {
        "grok-4": {
            "label": "Grok 4",
            "context_window_tokens": 256000,
            "supports_tools": True,
            "supports_json": True,
            "supports_reasoning": True,
            "capability_labels": ["Reasoning", "Tools", "Structured outputs"],
        },
        "grok-4-0709": {
            "label": "Grok 4 0709",
            "context_window_tokens": 256000,
            "supports_tools": True,
            "supports_json": True,
            "supports_reasoning": True,
            "capability_labels": ["Reasoning", "Tools", "Structured outputs"],
        },
        "grok-4-latest": {
            "label": "Grok 4 Latest",
            "context_window_tokens": 256000,
            "supports_tools": True,
            "supports_json": True,
            "supports_reasoning": True,
            "capability_labels": ["Reasoning", "Tools", "Structured outputs"],
        },
        "grok-3": {
            "label": "Grok 3",
            "context_window_tokens": 131072,
            "supports_tools": True,
            "supports_json": True,
            "supports_reasoning": True,
            "capability_labels": ["Reasoning", "Tools"],
        },
    },
    "qwen": {
        "qwen-plus": {
            "label": "Qwen Plus",
            "context_window_tokens": 131072,
            "input_cost_per_1k_usd": 0.0,
            "output_cost_per_1k_usd": 0.0,
            "supports_tools": True,
            "supports_reasoning": True,
            "capability_labels": ["Reasoning", "Hosted API"],
        },
        "qwen-turbo": {
            "label": "Qwen Turbo",
            "context_window_tokens": 1000000,
            "input_cost_per_1k_usd": 0.0,
            "output_cost_per_1k_usd": 0.0,
            "supports_tools": True,
            "supports_reasoning": False,
            "capability_labels": ["Fast", "Long context"],
        },
        "qwen-max": {
            "label": "Qwen Max",
            "context_window_tokens": 32768,
            "input_cost_per_1k_usd": 0.0,
            "output_cost_per_1k_usd": 0.0,
            "supports_tools": True,
            "supports_reasoning": True,
            "capability_labels": ["High quality", "Reasoning"],
        },
    },
    "deepseek": {
        "deepseek-v4-flash": {
            "label": "DeepSeek V4 Flash",
            "context_window_tokens": 1000000,
            "input_cost_per_1k_usd": 0.00014,
            "output_cost_per_1k_usd": 0.00028,
            "supports_tools": True,
            "supports_reasoning": False,
            "capability_labels": ["Low cost", "Fast", "Long context", "Hosted API"],
        },
        "deepseek-v4-pro": {
            "label": "DeepSeek V4 Pro",
            "context_window_tokens": 1000000,
            "input_cost_per_1k_usd": 0.000435,
            "output_cost_per_1k_usd": 0.00087,
            "supports_tools": True,
            "supports_reasoning": True,
            "reasoning_levels": ["high", "max"],
            "capability_labels": ["Reasoning", "Long context", "High quality", "Hosted API"],
        },
        "deepseek-chat": {
            "label": "DeepSeek Chat",
            "context_window_tokens": 1000000,
            "input_cost_per_1k_usd": 0.00014,
            "output_cost_per_1k_usd": 0.00028,
            "supports_tools": True,
            "supports_reasoning": False,
            "capability_labels": ["Low cost", "Fast", "Hosted API"],
        },
        "deepseek-reasoner": {
            "label": "DeepSeek Reasoner",
            "context_window_tokens": 128000,
            "input_cost_per_1k_usd": 0.00014,
            "output_cost_per_1k_usd": 0.00028,
            "supports_tools": True,
            "supports_reasoning": True,
            "capability_labels": ["Reasoning", "Low cost", "Hosted API"],
        },
    },
    "mistral": {
        "mistral-large-latest": {
            "label": "Mistral Large",
            "context_window_tokens": 131072,
            "input_cost_per_1k_usd": 0.0,
            "output_cost_per_1k_usd": 0.0,
            "supports_tools": True,
            "supports_reasoning": True,
            "capability_labels": ["Reasoning", "Long context", "Hosted API"],
        },
        "mistral-medium-latest": {
            "label": "Mistral Medium",
            "context_window_tokens": 32768,
            "input_cost_per_1k_usd": 0.0,
            "output_cost_per_1k_usd": 0.0,
            "supports_tools": True,
            "supports_reasoning": False,
            "capability_labels": ["Balanced", "Hosted API"],
        },
        "mistral-small-latest": {
            "label": "Mistral Small",
            "context_window_tokens": 32768,
            "input_cost_per_1k_usd": 0.0,
            "output_cost_per_1k_usd": 0.0,
            "supports_tools": True,
            "supports_reasoning": False,
            "capability_labels": ["Low cost", "Fast", "Hosted API"],
        },
    },
    "ollama": {
        "llama3.2": {
            "label": "Llama 3.2",
            "context_window_tokens": 128000,
            "input_cost_per_1k_usd": 0.0,
            "output_cost_per_1k_usd": 0.0,
            "supports_tools": True,
            "supports_reasoning": False,
            "local_self_hosted_compatible": True,
            "capability_labels": ["Local", "Self-hosted", "Offline-capable", "Tools"],
        },
        "llama3": {
            "label": "Llama 3",
            "context_window_tokens": 8192,
            "input_cost_per_1k_usd": 0.0,
            "output_cost_per_1k_usd": 0.0,
            "supports_tools": True,
            "supports_reasoning": False,
            "local_self_hosted_compatible": True,
            "capability_labels": ["Local", "Self-hosted", "Tools"],
        },
        "mistral": {
            "label": "Mistral (Ollama)",
            "context_window_tokens": 32768,
            "input_cost_per_1k_usd": 0.0,
            "output_cost_per_1k_usd": 0.0,
            "supports_tools": True,
            "supports_reasoning": False,
            "local_self_hosted_compatible": True,
            "capability_labels": ["Local", "Self-hosted", "Tools"],
        },
        "gemma": {
            "label": "Gemma (Ollama)",
            "context_window_tokens": 8192,
            "input_cost_per_1k_usd": 0.0,
            "output_cost_per_1k_usd": 0.0,
            "supports_tools": True,
            "supports_reasoning": False,
            "local_self_hosted_compatible": True,
            "capability_labels": ["Local", "Self-hosted", "Tools"],
        },
        "phi3": {
            "label": "Phi-3 (Ollama)",
            "context_window_tokens": 128000,
            "input_cost_per_1k_usd": 0.0,
            "output_cost_per_1k_usd": 0.0,
            "supports_tools": True,
            "supports_reasoning": False,
            "local_self_hosted_compatible": True,
            "capability_labels": ["Local", "Self-hosted", "Tools"],
        },
    },
    "ollama_cloud": {
        "gpt-oss:120b": {
            "label": "GPT OSS 120B (Ollama Cloud)",
            "context_window_tokens": 128000,
            "input_cost_per_1k_usd": 0.0,
            "output_cost_per_1k_usd": 0.0,
            "supports_tools": True,
            "supports_reasoning": True,
            "capability_labels": ["Hosted API", "Reasoning", "Tools"],
        },
        "gpt-oss:20b": {
            "label": "GPT OSS 20B (Ollama Cloud)",
            "context_window_tokens": 128000,
            "input_cost_per_1k_usd": 0.0,
            "output_cost_per_1k_usd": 0.0,
            "supports_tools": True,
            "supports_reasoning": False,
            "capability_labels": ["Hosted API", "Fast", "Tools"],
        },
    },
}

PROVIDER_LIMIT_POLICY = {
    "openai": {
        "max_output_tokens": 1600,
        "max_retry_attempts": 2,
        "backoff_base_seconds": 1.0,
        "backoff_max_seconds": 8.0,
        "supports_retry_after": True,
    },
    "openai-codex": {
        "max_output_tokens": 1800,
        "max_retry_attempts": 2,
        "backoff_base_seconds": 1.0,
        "backoff_max_seconds": 8.0,
        "supports_retry_after": True,
    },
    "anthropic": {
        "max_output_tokens": 1600,
        "max_retry_attempts": 2,
        "backoff_base_seconds": 1.0,
        "backoff_max_seconds": 8.0,
        "supports_retry_after": True,
    },
    "gemini": {
        "max_output_tokens": 1700,
        "max_retry_attempts": 2,
        "backoff_base_seconds": 1.0,
        "backoff_max_seconds": 8.0,
        "supports_retry_after": True,
    },
    "deepseek": {
        "max_output_tokens": 2400,
        "max_retry_attempts": 3,
        "backoff_base_seconds": 1.0,
        "backoff_max_seconds": 10.0,
        "supports_retry_after": True,
    },
    "qwen": {
        "max_output_tokens": 1400,
        "max_retry_attempts": 2,
        "backoff_base_seconds": 1.0,
        "backoff_max_seconds": 8.0,
        "supports_retry_after": True,
    },
    "mistral": {
        "max_output_tokens": 1400,
        "max_retry_attempts": 2,
        "backoff_base_seconds": 1.0,
        "backoff_max_seconds": 8.0,
        "supports_retry_after": True,
    },
    "ollama": {
        "max_output_tokens": 900,
        "max_retry_attempts": 1,
        "backoff_base_seconds": 0.5,
        "backoff_max_seconds": 2.0,
        "supports_retry_after": False,
    },
    "ollama_cloud": {
        "max_output_tokens": 1400,
        "max_retry_attempts": 2,
        "backoff_base_seconds": 1.0,
        "backoff_max_seconds": 8.0,
        "supports_retry_after": True,
    },
}

PROVIDER_LIMIT_ALIASES = {
    "codex_cli": "openai-codex",
    "claude_code_cli": "anthropic",
}


@lru_cache(maxsize=1)
def _platform_provider_catalog_config() -> platform_config_schema.PlatformProviderCatalogConfig:
    return platform_config_schema.build_platform_provider_catalog_config(
        provider_catalog=PROVIDER_CATALOG,
        governance_catalog=PROVIDER_GOVERNANCE_CATALOG,
        model_catalog=PROVIDER_MODEL_CATALOG,
        model_policy_overrides=platform_config_schema.load_model_catalog_policy_overrides(),
    )


def provider_governance_entry(provider: Any) -> Dict[str, Any]:
    provider_id = normalize_provider_id(provider)
    entry = _platform_provider_catalog_config().provider_governance(provider_id)
    return entry.model_dump(exclude_none=True) if entry is not None else {}


def provider_model_policy(provider: Any, model: Any) -> Dict[str, Any]:
    provider_id = normalize_provider_id(provider)
    model_id = normalize_provider_model_id(provider_id, model, fallback_to_default=False)
    entry = _platform_provider_catalog_config().provider_model_policy(provider_id, model_id)
    return entry.model_dump(exclude_none=True) if entry is not None else {}


def _default_model_choice_label(model_id: str) -> str:
    return " ".join(part for part in str(model_id or "").replace("_", "-").split("-") if part).strip() or str(model_id or "")


def _float_or_none(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _int_or_none(value: Any) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        number = int(float(value))
    except Exception:
        return None
    return number if number > 0 else None


def _model_cost_per_1k_from_raw(value: Any, *, value_is_per_token: bool = False) -> Optional[float]:
    number = _float_or_none(value)
    if number is None:
        return None
    return number * 1000 if value_is_per_token else number


def normalize_live_model_record(
    provider: Any,
    raw: Any,
    *,
    source: str = "provider_adapter",
    fetched_at: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    provider_id = normalize_provider_id(provider)
    if isinstance(raw, str):
        raw_record: Dict[str, Any] = {"id": raw}
    elif isinstance(raw, dict):
        raw_record = dict(raw)
    else:
        return None
    model_id = str(
        raw_record.get("id")
        or raw_record.get("name")
        or raw_record.get("model")
        or ""
    ).strip()
    if model_id.startswith("models/"):
        model_id = model_id.split("/", 1)[1]
    if not model_id:
        return None
    pricing = raw_record.get("pricing") if isinstance(raw_record.get("pricing"), dict) else {}
    architecture = raw_record.get("architecture") if isinstance(raw_record.get("architecture"), dict) else {}
    supported_parameters = raw_record.get("supported_parameters") if isinstance(raw_record.get("supported_parameters"), list) else []
    input_per_1k = (
        _model_cost_per_1k_from_raw(raw_record.get("input_cost_per_1k_usd"))
        if raw_record.get("input_cost_per_1k_usd") is not None
        else _model_cost_per_1k_from_raw(raw_record.get("input_cost_per_million"), value_is_per_token=False) / 1000
        if _float_or_none(raw_record.get("input_cost_per_million")) is not None
        else _model_cost_per_1k_from_raw(pricing.get("prompt"), value_is_per_token=True)
    )
    output_per_1k = (
        _model_cost_per_1k_from_raw(raw_record.get("output_cost_per_1k_usd"))
        if raw_record.get("output_cost_per_1k_usd") is not None
        else _model_cost_per_1k_from_raw(raw_record.get("output_cost_per_million"), value_is_per_token=False) / 1000
        if _float_or_none(raw_record.get("output_cost_per_million")) is not None
        else _model_cost_per_1k_from_raw(pricing.get("completion"), value_is_per_token=True)
    )
    context_window = (
        _int_or_none(raw_record.get("context_window_tokens"))
        or _int_or_none(raw_record.get("context_length"))
        or _int_or_none(raw_record.get("context_window"))
        or _int_or_none(raw_record.get("input_token_limit"))
        or _int_or_none(raw_record.get("inputTokenLimit"))
    )
    supports_tools = bool(
        raw_record.get("supports_tools")
        or "tools" in supported_parameters
        or "tool_choice" in supported_parameters
    )
    modalities = [str(item).lower() for item in raw_record.get("modalities", [])] if isinstance(raw_record.get("modalities"), list) else []
    supports_vision = bool(
        raw_record.get("supports_vision")
        or "image" in str(architecture.get("modality") or "").lower()
        or "image" in modalities
    )
    supports_json = bool(raw_record.get("supports_json") or "response_format" in supported_parameters)
    supports_reasoning = bool(
        raw_record.get("supports_reasoning")
        or "reasoning" in supported_parameters
        or any(token in model_id.lower() for token in ("reason", "o1", "o3", "o4", "gpt-5", "grok-4", "opus"))
    )
    return {
        "id": model_id,
        "label": str(raw_record.get("label") or raw_record.get("display_name") or raw_record.get("name") or _default_model_choice_label(model_id)),
        "provider": provider_id,
        "provider_id": provider_id,
        "context_window_tokens": context_window,
        "input_cost_per_1k_usd": input_per_1k,
        "output_cost_per_1k_usd": output_per_1k,
        "input_cost_per_million": input_per_1k * 1000 if input_per_1k is not None else None,
        "output_cost_per_million": output_per_1k * 1000 if output_per_1k is not None else None,
        "pricing_known": input_per_1k is not None or output_per_1k is not None,
        "supports_tools": supports_tools,
        "supports_vision": supports_vision,
        "supports_json": supports_json,
        "supports_reasoning": supports_reasoning,
        "lifecycle": str(raw_record.get("lifecycle") or raw_record.get("stage") or "").strip() or None,
        "source": source,
        "fetched_at": fetched_at,
        "raw": raw_record,
    }


def _provider_model_identifier_list(provider: Any) -> List[str]:
    provider_id = normalize_provider_id(provider)
    entry = provider_catalog_entry(provider_id)
    explicit = [str(item).strip() for item in entry.get("models", []) if str(item).strip()]
    policy_models = [
        str(model_id or "").strip()
        for model_id, policy in (_platform_provider_catalog_config().model_policies.get(provider_id) or {}).items()
        if str(model_id or "").strip() and policy.enabled is not False
    ]
    if explicit:
        return list(dict.fromkeys(explicit + policy_models))
    default_model = str(entry.get("default_model") or "").strip()
    return list(dict.fromkeys(([default_model] if default_model else []) + policy_models))


def provider_model_catalog(provider: Any) -> List[Dict[str, Any]]:
    provider_id = normalize_provider_id(provider)
    governance = provider_governance_entry(provider_id)
    model_entries = {
        item.id: item.model_dump(exclude_none=True)
        for item in _platform_provider_catalog_config().provider_models(provider_id)
    }
    items: List[Dict[str, Any]] = []
    for model_id in _provider_model_identifier_list(provider_id):
        metadata = dict(model_entries.get(model_id) or {})
        policy = provider_model_policy(provider_id, model_id)
        if policy.get("enabled") is False:
            continue
        if policy.get("context_window_tokens") is not None:
            metadata["context_window_tokens"] = policy.get("context_window_tokens")
        for flag in ("supports_tools", "supports_vision", "supports_reasoning"):
            if policy.get(flag) is not None:
                metadata[flag] = bool(policy.get(flag))
        pricing_projection = pricing_registry_service.catalog_price_projection(provider_id, model_id)
        pricing_known = bool(
            policy.get("pricing_known")
            if policy.get("pricing_known") is not None
            else pricing_projection.get("pricing_known")
        )
        supports_tools = bool(metadata.get("supports_tools"))
        supports_reasoning = bool(metadata.get("supports_reasoning"))
        capability_labels = [
            str(label).strip()
            for label in metadata.get("capability_labels", [])
            if str(label).strip()
        ]
        reasoning_levels = [
            str(level).strip().lower()
            for level in metadata.get("reasoning_levels", [])
            if str(level).strip()
        ]
        if supports_tools and "Tools" not in capability_labels:
            capability_labels.append("Tools")
        if supports_reasoning and "Reasoning" not in capability_labels:
            capability_labels.append("Reasoning")
        if bool(metadata.get("local_self_hosted_compatible") or governance.get("local_self_hosted_compatible")) and "Local / self-hosted" not in capability_labels:
            capability_labels.append("Local / self-hosted")
        items.append(
            {
                "id": model_id,
                "label": str(metadata.get("label") or _default_model_choice_label(model_id)),
                "provider": provider_id,
                "context_window_tokens": int(metadata.get("context_window_tokens") or 0) or None,
                "input_cost_per_1k_usd": float(pricing_projection.get("input_cost_per_1k_usd") or 0.0),
                "output_cost_per_1k_usd": float(pricing_projection.get("output_cost_per_1k_usd") or 0.0),
                "pricing_known": pricing_known,
                "pricing_source": policy.get("pricing_source") or pricing_projection.get("pricing_source"),
                "pricing_registry_version": policy.get("pricing_version") or pricing_projection.get("pricing_registry_version"),
                "supports_tools": supports_tools,
                "supports_vision": bool(metadata.get("supports_vision")),
                "supports_reasoning": supports_reasoning,
                "reasoning_levels": reasoning_levels,
                "local_self_hosted_compatible": bool(metadata.get("local_self_hosted_compatible") or governance.get("local_self_hosted_compatible")),
                "capability_labels": capability_labels,
                "enabled": policy.get("enabled", True),
                "allowed_surfaces": list(policy.get("allowed_surfaces") or []),
                "allowed_payers": list(policy.get("allowed_payers") or []),
                "platform_paid_allowed": bool(policy.get("platform_paid_allowed", pricing_known)),
                "fallback_allowed": bool(policy.get("fallback_allowed", False)),
            }
        )
    return items


def normalize_provider_model_id(
    provider: Any,
    model_id: Any,
    *,
    fallback_to_default: bool = False,
) -> str:
    provider_id = normalize_provider_id(provider)
    token = str(model_id or "").strip()
    if not token:
        return str(provider_catalog_entry(provider_id).get("default_model") or "").strip() if fallback_to_default else ""
    if provider_id == "anthropic" and token.startswith("anthropic/"):
        token = token.split("/", 1)[1]
    elif provider_id == "gemini" and token.startswith("gemini/"):
        token = token.split("/", 1)[1]
    elif provider_id == "vertex" and token.startswith("vertex_ai/"):
        token = token.split("/", 1)[1]
    elif provider_id in {"qwen", "deepseek", "mistral", "ollama", "ollama_cloud", "groq", "xai", "azure_openai", "custom_openai_compatible"} and "/" in token:
        provider_token, model_token = token.split("/", 1)
        if normalize_provider_id(provider_token) == provider_id:
            token = model_token.strip()
    token = PROVIDER_MODEL_ALIASES.get(provider_id, {}).get(token, token)
    supported_models = set(_provider_model_identifier_list(provider_id))
    if token and supported_models and token not in supported_models and fallback_to_default:
        return str(provider_catalog_entry(provider_id).get("default_model") or "").strip()
    return token


def context_window_for_model(provider: str | None, model: str | None) -> int | None:
    provider_id = normalize_provider_id(provider)
    if not provider_id:
        return None
    try:
        catalog_entry = provider_catalog_entry(provider_id)
    except Exception:
        catalog_entry = {}

    model_token = normalize_provider_model_id(
        provider_id,
        model,
        fallback_to_default=False,
    )
    if not model_token:
        model_token = str(catalog_entry.get("default_model") or "").strip()
    if not model_token:
        return None

    model_catalog = PROVIDER_MODEL_CATALOG.get(provider_id, {})
    metadata = model_catalog.get(model_token) if isinstance(model_catalog, dict) else None
    if not isinstance(metadata, dict):
        return None
    try:
        resolved = int(metadata.get("context_window_tokens") or 0)
    except Exception:
        return None
    return resolved if resolved > 0 else None


def provider_limit_policy(provider: str | None, model: str | None = None) -> Dict[str, Any]:
    provider_token = str(provider or "").strip().lower()
    provider_id = PROVIDER_LIMIT_ALIASES.get(provider_token, normalize_provider_id(provider_token))
    defaults = {
        "max_output_tokens": 1200,
        "max_retry_attempts": 1,
        "backoff_base_seconds": 1.0,
        "backoff_max_seconds": 4.0,
        "supports_retry_after": True,
    }
    policy = dict(defaults)
    policy.update(dict(PROVIDER_LIMIT_POLICY.get(provider_id) or {}))
    context_window = context_window_for_model(provider_id, model)
    if context_window and context_window > 0:
        model_cap = max(512, min(12000, int(context_window * 0.15)))
        policy["max_output_tokens"] = max(
            256,
            min(
                int(policy.get("max_output_tokens") or defaults["max_output_tokens"]),
                model_cap,
            ),
        )
    policy["max_retry_attempts"] = max(1, int(policy.get("max_retry_attempts") or 1))
    policy["backoff_base_seconds"] = max(0.1, float(policy.get("backoff_base_seconds") or 1.0))
    policy["backoff_max_seconds"] = max(
        policy["backoff_base_seconds"],
        float(policy.get("backoff_max_seconds") or policy["backoff_base_seconds"]),
    )
    policy["supports_retry_after"] = bool(policy.get("supports_retry_after", True))
    policy["provider"] = provider_id
    return policy


def normalize_provider_id(provider: Any) -> str:
    provider_id = str(provider or "").strip().lower()
    return LEGACY_PROVIDER_ALIASES.get(provider_id, provider_id)


def provider_catalog_entry(provider: Any) -> Dict[str, Any]:
    provider_id = normalize_provider_id(provider)
    entry = _platform_provider_catalog_config().provider(provider_id)
    return entry.model_dump(exclude_none=True) if entry is not None else {}


def normalize_auth_mode(provider: Any, auth_mode: Any = None, credentials: Optional[Dict[str, Any]] = None) -> str:
    provider_id = str(provider or "").strip().lower()
    raw_auth_mode = str(
        auth_mode
        or (credentials or {}).get("auth_mode")
        or (credentials or {}).get("authMode")
        or ""
    ).strip().lower()
    if provider_id == "claude_code_cli":
        return "local_cli"
    if raw_auth_mode in LOCAL_CLI_AUTH_MODES:
        return "local_cli"
    if raw_auth_mode:
        return raw_auth_mode
    entry = provider_catalog_entry(provider_id)
    default_auth_mode = str(entry.get("default_auth_mode") or "").strip().lower()
    return default_auth_mode or ""


def provider_supports_auth_mode(provider: Any, auth_mode: Any) -> bool:
    entry = provider_catalog_entry(provider)
    supported = {str(mode).strip().lower() for mode in entry.get("auth", []) if str(mode).strip()}
    normalized = normalize_auth_mode(provider, auth_mode)
    if normalized == "platform_runtime":
        return normalize_provider_id(provider) in PLATFORM_RUNTIME_AUTH_PROVIDERS
    if normalized == "local_cli":
        return bool({"local_cli", "local_subscription"} & supported) or str(provider).strip().lower() == "claude_code_cli"
    return normalized in supported if normalized else not supported


def provider_requires_credential(provider: Any, auth_mode: Any) -> bool:
    normalized = normalize_auth_mode(provider, auth_mode)
    if normalized == "platform_runtime":
        return normalize_provider_id(provider) not in PLATFORM_RUNTIME_AUTH_PROVIDERS
    return normalized not in {"local_cli", "none"}


def secretless_provider_credentials(provider: Any, auth_mode: Any) -> Dict[str, Any]:
    provider_id = normalize_provider_id(provider)
    normalized_auth_mode = normalize_auth_mode(provider, auth_mode)
    credentials: Dict[str, Any] = {"auth_mode": normalized_auth_mode}
    if provider_id == "anthropic" and normalized_auth_mode == "local_cli":
        credentials["_provider"] = "anthropic"
    return credentials


def resolve_provider_adapter(provider: Any, credentials: Optional[Dict[str, Any]] = None) -> tuple[str, str, ProviderAdapter]:
    provider_id = normalize_provider_id(provider)
    auth_mode = normalize_auth_mode(provider, credentials=credentials)
    resolved_provider_id = provider_id
    adapter_key = provider_id
    if provider_id == "anthropic" and auth_mode == "local_cli":
        adapter_key = "claude_code_cli"
    if provider_id == "openai-codex":
        adapter_key = "openai-codex"
    adapter = PROVIDER_ADAPTERS.get(adapter_key)
    if adapter is None:
        raise RuntimeError(f"Unsupported provider '{provider}'.")
    return resolved_provider_id, adapter_key, adapter

# ---------------------------------------------------------------------------
# Provider adapters (moved from server.py)
# ---------------------------------------------------------------------------

OPENAI_RESPONSES_URL = os.getenv("OPENAI_RESPONSES_URL", "https://api.openai.com/v1/responses")
OPENAI_ORG_ID = os.getenv("OPENAI_ORG_ID")
OPENAI_PROJECT_ID = os.getenv("OPENAI_PROJECT_ID")
OPENAI_CODEX_OAUTH_SOURCES = {"env_codex_oauth_token", "codex_token_vault"}
OPENAI_CODEX_MODEL_CATALOG = [
    "gpt-5.4",
    "gpt-5.3-codex",
    "gpt-5.3-codex-spark",
    "gpt-5.2",
    "gpt-5.2-codex",
    "gpt-5.1-codex",
]
OPENAI_CODEX_DIRECT_AUTH_ERROR = (
    "This is a Codex OAuth token. Use openai-codex provider or set a direct OpenAI API key."
)
PROVIDER_LIVE_PROBE_PROMPT = "Reply with OK. Do not use tools."
PROVIDER_STATUS_PROBE_TIMEOUT_SECONDS = 2
LOCAL_SERVICE_STATUS_TIMEOUT_SECONDS = 1


def _load_openai_codex_transport():
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    try:
        import orion_local_worker_llm as worker_llm  # type: ignore
    except Exception as exc:
        raise RuntimeError(f"Could not load Codex transport: {exc}") from exc
    return worker_llm


class ProviderAdapter:
    provider_id = ""

    def validate(self, credentials: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    def list_models(self, credentials: Dict[str, Any]) -> List[str]:
        raise NotImplementedError

    def list_model_records(self, credentials: Dict[str, Any]) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        fetched_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        for model_id in self.list_models(credentials):
            record = normalize_live_model_record(
                self.provider_id,
                model_id,
                source="provider_adapter",
                fetched_at=fetched_at,
            )
            if record:
                records.append(record)
        return records

    def generate(self, system_prompt: str, user_input: str, model: str, credentials: Dict[str, Any]) -> str:
        raise NotImplementedError

    def probe_model(self, credentials: Dict[str, Any]) -> str:
        _ = credentials
        entry = provider_catalog_entry(self.provider_id)
        return str(entry.get("default_model") or "").strip()

    def probe(self, credentials: Dict[str, Any]) -> Dict[str, Any]:
        selected_model = self.probe_model(credentials)
        if not selected_model:
            models = self.list_models(credentials)
            selected_model = str(models[0] if models else "").strip()
        if not selected_model:
            raise RuntimeError(f"No probe model available for provider '{self.provider_id}'.")
        reply = self.generate("", PROVIDER_LIVE_PROBE_PROMPT, selected_model, credentials)
        normalized_reply = str(reply or "").strip()
        if not normalized_reply:
            raise RuntimeError(f"{self.provider_id} probe returned empty output.")
        return {
            "ok": True,
            "status": 200,
            "message": "Live probe succeeded.",
            "model": selected_model,
            "reply": normalized_reply,
        }


def claude_code_cli_available() -> bool:
    return bool(shutil.which("claude"))


def gemini_cli_available() -> bool:
    return bool(shutil.which("gemini"))


def claude_code_cli_status(timeout: int = 15) -> Dict[str, Any]:
    if not claude_code_cli_available():
        return {
            "available": False,
            "logged_in": False,
            "message": "Claude Code CLI is not installed.",
        }
    try:
        result = subprocess.run(
            ["claude", "auth", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=max(5, int(timeout)),
            check=False,
        )
    except Exception as exc:
        return {
            "available": True,
            "logged_in": False,
            "message": str(exc),
        }

    raw = str(result.stdout or result.stderr or "").strip()
    if result.returncode != 0:
        return {
            "available": True,
            "logged_in": False,
            "message": raw or f"Claude auth status failed with exit code {result.returncode}.",
        }
    try:
        payload = json.loads(raw) if raw else {}
    except Exception:
        payload = {}
    logged_in = bool(payload.get("loggedIn"))
    return {
        "available": True,
        "logged_in": logged_in,
        "auth_method": str(payload.get("authMethod") or "").strip(),
        "api_provider": str(payload.get("apiProvider") or "").strip(),
        "message": "Claude subscription is signed in on this machine." if logged_in else "Claude subscription is not signed in yet.",
    }


def run_claude_code_cli(system_prompt: str, user_input: str, model: str, timeout: int = 120) -> str:
    if not claude_code_cli_available():
        raise RuntimeError("Claude Code CLI is not installed.")

    selected_model = str(model or "sonnet").strip() or "sonnet"
    cmd = [
        "claude",
        "-p",
        "--output-format",
        "text",
        "--model",
        selected_model,
        "--system-prompt",
        system_prompt,
        user_input,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=max(15, int(timeout)),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Claude Code CLI timed out after {int(timeout)}s.") from exc
    except Exception as exc:
        raise RuntimeError(f"Claude Code CLI failed to start: {exc}") from exc

    if result.returncode != 0:
        detail = str(result.stderr or result.stdout or "").strip() or f"exit_{result.returncode}"
        if len(detail) > 500:
            detail = detail[:500] + "..."
        raise RuntimeError(detail)

    text = str(result.stdout or "").strip()
    if not text:
        raise RuntimeError("Claude Code CLI returned empty output.")
    return text


class OpenAIAdapter(ProviderAdapter):
    provider_id = "openai"

    def _uses_codex_token(self, credentials: Dict[str, Any]) -> bool:
        return _openai_credential_type(credentials) == "codex_token"

    def _headers(self, credentials: Dict[str, Any]) -> Dict[str, str]:
        self._ensure_direct_api_credentials(credentials)
        token = _openai_bearer_from_credentials(credentials)
        if not token:
            raise RuntimeError("OpenAI credential requires api_key, access_token, or oauth_token.")
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        org_id = credentials.get("org_id") or OPENAI_ORG_ID
        project_id = credentials.get("project_id") or OPENAI_PROJECT_ID
        if org_id:
            headers["OpenAI-Organization"] = str(org_id)
        if project_id:
            headers["OpenAI-Project"] = str(project_id)
        return headers

    def _ensure_direct_api_credentials(self, credentials: Dict[str, Any]) -> None:
        if self._uses_codex_token(credentials):
            raise RuntimeError(OPENAI_CODEX_DIRECT_AUTH_ERROR)

    def validate(self, credentials: Dict[str, Any]) -> Dict[str, Any]:
        self._ensure_direct_api_credentials(credentials)
        res = http_json_request("https://api.openai.com/v1/models", headers=self._headers(credentials))
        return _validation_result("OpenAI", res)

    def list_models(self, credentials: Dict[str, Any]) -> List[str]:
        self._ensure_direct_api_credentials(credentials)
        res = http_json_request("https://api.openai.com/v1/models", headers=self._headers(credentials))
        data = res.get("json", {}) or {}
        models = []
        for item in data.get("data", []) if isinstance(data.get("data"), list) else []:
            if isinstance(item, dict):
                model_id = item.get("id")
                if isinstance(model_id, str):
                    models.append(model_id)
        return sorted(set(models))

    def list_model_records(self, credentials: Dict[str, Any]) -> List[Dict[str, Any]]:
        self._ensure_direct_api_credentials(credentials)
        res = http_json_request("https://api.openai.com/v1/models", headers=self._headers(credentials))
        body = res.get("json", {}) or {}
        fetched_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        records: List[Dict[str, Any]] = []
        seen: Set[str] = set()
        for item in body.get("data", []) if isinstance(body.get("data"), list) else []:
            record = normalize_live_model_record(
                self.provider_id,
                item,
                source="openai_models_api",
                fetched_at=fetched_at,
            )
            if not record or record["id"] in seen:
                continue
            seen.add(record["id"])
            records.append(record)
        return sorted(records, key=lambda item: str(item.get("id") or ""))

    def generate(self, system_prompt: str, user_input: str, model: str, credentials: Dict[str, Any]) -> str:
        from server_modules.model_router import call_model_sync

        self._ensure_direct_api_credentials(credentials)
        result = call_model_sync(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_input},
            ],
            model=model,
            provider=self.provider_id,
            credentials=credentials,
        )
        return str(result.get("content") or "").strip()


class OpenAICompatibleAdapter(ProviderAdapter):
    def __init__(self, provider_id: str, provider_label: str, *, requires_auth: bool = True) -> None:
        self.provider_id = provider_id
        self.provider_label = provider_label
        self.requires_auth = requires_auth

    def _model_or_deployment(self, credentials: Dict[str, Any], model: Optional[str] = None) -> str:
        return str(
            credentials.get("deployment")
            or credentials.get("deployment_name")
            or credentials.get("azure_deployment")
            or credentials.get("model")
            or credentials.get("model_id")
            or model
            or ""
        ).strip()

    def _base_url(self, credentials: Dict[str, Any], *, model: Optional[str] = None) -> str:
        entry = provider_catalog_entry(self.provider_id)
        base_url = str(credentials.get("base_url") or entry.get("base_url") or "").strip().rstrip("/")
        if self.provider_id == "azure_openai":
            if base_url and "/openai/deployments/" in base_url:
                return base_url.rsplit("/chat/completions", 1)[0].rstrip("/")
            endpoint = str(credentials.get("endpoint") or credentials.get("azure_endpoint") or "").strip().rstrip("/")
            deployment = self._model_or_deployment(credentials, model=model)
            if endpoint and deployment:
                return f"{endpoint}/openai/deployments/{quote(deployment, safe='')}"
        if not base_url:
            raise RuntimeError(f"{self.provider_label} base URL is not configured.")
        return base_url

    def _query_params(self, credentials: Dict[str, Any]) -> Dict[str, str]:
        if self.provider_id != "azure_openai":
            return {}
        api_version = str(credentials.get("api_version") or credentials.get("azure_api_version") or "").strip()
        if not api_version:
            raise RuntimeError("Azure OpenAI api_version is required.")
        return {"api-version": api_version}

    def _chat_completions_url(self, credentials: Dict[str, Any], *, model: str) -> str:
        base_url = self._base_url(credentials, model=model)
        if self.provider_id == "azure_openai":
            params = self._query_params(credentials)
            return f"{base_url}/chat/completions?api-version={quote_plus(params['api-version'])}"
        return f"{base_url}/chat/completions"

    def _headers(self, credentials: Dict[str, Any], *, include_content_type: bool = False) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        if include_content_type:
            headers["Content-Type"] = "application/json"
        if not self.requires_auth:
            return headers
        token = str(
            credentials.get("api_key")
            or credentials.get("access_token")
            or credentials.get("oauth_token")
            or credentials.get("token")
            or ""
        ).strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        if not token:
            raise RuntimeError(f"{self.provider_label} api_key is required.")
        headers["Authorization"] = f"Bearer {token}"
        return headers

    def validate(self, credentials: Dict[str, Any]) -> Dict[str, Any]:
        base_url = self._base_url(credentials, model=self._model_or_deployment(credentials))
        params = self._query_params(credentials)
        url = f"{base_url}/models"
        if params:
            url = f"{url}?api-version={quote_plus(params['api-version'])}"
        try:
            res = http_json_request(url, headers=self._headers(credentials))
        except Exception as exc:
            if self.provider_id == "ollama":
                raise RuntimeError(f"Ollama is not running at {base_url}") from exc
            raise
        return _validation_result(self.provider_label, res)

    def list_models(self, credentials: Dict[str, Any]) -> List[str]:
        base_url = self._base_url(credentials, model=self._model_or_deployment(credentials))
        params = self._query_params(credentials)
        url = f"{base_url}/models"
        if params:
            url = f"{url}?api-version={quote_plus(params['api-version'])}"
        res = http_json_request(url, headers=self._headers(credentials))
        body = res.get("json") or {}
        models: List[str] = []
        for item in body.get("data", []) if isinstance(body.get("data"), list) else []:
            if not isinstance(item, dict):
                continue
            model_id = item.get("id")
            if isinstance(model_id, str) and model_id.strip():
                models.append(model_id.strip())
        if models:
            return sorted(set(models))
        entry = provider_catalog_entry(self.provider_id)
        fallback_models = [str(item).strip() for item in entry.get("models", []) if str(item).strip()]
        return fallback_models

    def list_model_records(self, credentials: Dict[str, Any]) -> List[Dict[str, Any]]:
        base_url = self._base_url(credentials, model=self._model_or_deployment(credentials))
        params = self._query_params(credentials)
        url = f"{base_url}/models"
        if params:
            url = f"{url}?api-version={quote_plus(params['api-version'])}"
        res = http_json_request(url, headers=self._headers(credentials))
        body = res.get("json") or {}
        fetched_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        records: List[Dict[str, Any]] = []
        seen: Set[str] = set()
        for item in body.get("data", []) if isinstance(body.get("data"), list) else []:
            record = normalize_live_model_record(
                self.provider_id,
                item,
                source="openai_compatible_models_api",
                fetched_at=fetched_at,
            )
            if not record or record["id"] in seen:
                continue
            seen.add(record["id"])
            records.append(record)
        if records:
            return sorted(records, key=lambda item: str(item.get("id") or ""))
        return super().list_model_records(credentials)

    def generate(self, system_prompt: str, user_input: str, model: str, credentials: Dict[str, Any]) -> str:
        payload: Dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_input},
            ],
            "temperature": 0.2,
        }
        if not str(system_prompt or "").strip():
            payload["messages"] = [{"role": "user", "content": user_input}]
        res = http_json_request(
            self._chat_completions_url(credentials, model=model),
            method="POST",
            headers=self._headers(credentials, include_content_type=True),
            payload=payload,
            timeout=60,
        )
        body = res.get("json")
        if not isinstance(body, dict):
            raise RuntimeError(f"{self.provider_label} returned invalid response.")
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError(f"{self.provider_label} returned no choices.")
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
        if isinstance(content, str) and content.strip():
            return content.strip()
        raise RuntimeError(f"{self.provider_label} response did not include text content.")


class OllamaCloudAdapter(ProviderAdapter):
    provider_id = "ollama_cloud"

    def _base_url(self, credentials: Dict[str, Any]) -> str:
        entry = provider_catalog_entry(self.provider_id)
        base_url = str(credentials.get("base_url") or entry.get("base_url") or "https://ollama.com").strip().rstrip("/")
        if not base_url:
            raise RuntimeError("Ollama Cloud base URL is not configured.")
        return base_url

    def _headers(self, credentials: Dict[str, Any], *, include_content_type: bool = False) -> Dict[str, str]:
        token = str(
            credentials.get("api_key")
            or credentials.get("access_token")
            or credentials.get("oauth_token")
            or credentials.get("token")
            or ""
        ).strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        if not token:
            raise RuntimeError("Ollama Cloud api_key is required.")
        headers = {"Authorization": f"Bearer {token}"}
        if include_content_type:
            headers["Content-Type"] = "application/json"
        return headers

    @staticmethod
    def _message_text(payload: Dict[str, Any]) -> str:
        message = payload.get("message") if isinstance(payload, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        return str(content or "").strip()

    def validate(self, credentials: Dict[str, Any]) -> Dict[str, Any]:
        res = http_json_request(f"{self._base_url(credentials)}/api/tags", headers=self._headers(credentials))
        return _validation_result("Ollama Cloud", res)

    def list_models(self, credentials: Dict[str, Any]) -> List[str]:
        res = http_json_request(f"{self._base_url(credentials)}/api/tags", headers=self._headers(credentials))
        body = res.get("json") if isinstance(res.get("json"), dict) else {}
        items = body.get("models") if isinstance(body.get("models"), list) else []
        models: List[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("name") or item.get("model") or "").strip()
            if model_id:
                models.append(model_id)
        if models:
            return sorted(set(models))
        entry = provider_catalog_entry(self.provider_id)
        return [str(item).strip() for item in entry.get("models", []) if str(item).strip()]

    def generate(self, system_prompt: str, user_input: str, model: str, credentials: Dict[str, Any]) -> str:
        messages: List[Dict[str, str]] = []
        if str(system_prompt or "").strip():
            messages.append({"role": "system", "content": str(system_prompt).strip()})
        messages.append({"role": "user", "content": str(user_input or "").strip()})
        payload: Dict[str, Any] = {
            "model": str(model or "").strip() or str(provider_catalog_entry(self.provider_id).get("default_model") or "gpt-oss:120b"),
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0.2},
        }
        res = http_json_request(
            f"{self._base_url(credentials)}/api/chat",
            method="POST",
            headers=self._headers(credentials, include_content_type=True),
            payload=payload,
            timeout=60,
        )
        body = res.get("json")
        if not isinstance(body, dict):
            raise RuntimeError("Ollama Cloud returned invalid response.")
        text = self._message_text(body)
        if text:
            return text
        raise RuntimeError("Ollama Cloud response did not include text content.")


def _urlsafe_b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("utf-8"))


def codex_account_id_from_token(token: Any) -> str:
    raw = str(token or "").strip()
    if not raw:
        return ""
    parts = raw.split(".")
    if len(parts) < 2:
        return ""
    try:
        payload = json.loads(_urlsafe_b64decode(parts[1]).decode("utf-8", "ignore"))
    except Exception:
        return ""
    auth_payload = payload.get("https://api.openai.com/auth")
    if isinstance(auth_payload, dict):
        account_id = str(auth_payload.get("chatgpt_account_id") or "").strip()
        if account_id:
            return account_id
    return str(payload.get("sub") or "").strip()


class OpenAICodexAdapter(ProviderAdapter):
    provider_id = "openai-codex"

    def _oauth_token(self, credentials: Dict[str, Any]) -> str:
        token = str(credentials.get("oauth_token") or credentials.get("access_token") or "").strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        if not token:
            raise RuntimeError("OpenAI Codex OAuth token is required.")
        return token

    def validate(self, credentials: Dict[str, Any]) -> Dict[str, Any]:
        token = self._oauth_token(credentials)
        account_id = str(credentials.get("account_id") or "").strip() or codex_account_id_from_token(token)
        if not account_id:
            return {
                "ok": False,
                "status": 401,
                "message": "Codex session expired — re-authenticate",
            }
        try:
            result = self.probe(credentials)
        except Exception as exc:
            detail = str(exc or "").strip().lower()
            if (
                "http_401" in detail
                or "http_403" in detail
                or "missing_oauth_token" in detail
                or "missing_chatgpt_account_id" in detail
                or "expired" in detail
                or "unauthorized" in detail
                or "forbidden" in detail
            ):
                return {
                    "ok": False,
                    "status": 401,
                    "message": "Codex session expired — re-authenticate",
                }
            raise
        return {
            "ok": True,
            "status": 200,
            "message": "Codex session is valid.",
            "model": result.get("model"),
        }

    def list_models(self, credentials: Dict[str, Any]) -> List[str]:
        self._oauth_token(credentials)
        return list(OPENAI_CODEX_MODEL_CATALOG)

    def _credential_override(self, credentials: Dict[str, Any], token: str) -> Dict[str, str]:
        return {
            "oauth_token": token,
            "account_id": str(credentials.get("account_id") or "").strip() or codex_account_id_from_token(token),
            "email": str(credentials.get("email") or "").strip(),
            "profile_name": str(credentials.get("profile_name") or "").strip(),
        }

    def _transport_text(
        self,
        system_prompt: str,
        user_input: str,
        model: str,
        credentials: Dict[str, Any],
    ) -> tuple[str, Optional[Dict[str, Any]], str]:
        token = self._oauth_token(credentials)
        selected_model = str(model or self.probe_model(credentials) or "gpt-5.4").strip() or "gpt-5.4"
        worker_llm = _load_openai_codex_transport()
        text, usage, used_model, error = worker_llm.openai_codex_backend_text(
            system_prompt,
            user_input,
            model_override=selected_model,
            credential_override=self._credential_override(credentials, token),
        )
        if error:
            raise RuntimeError(str(error))
        reply = str(text or "").strip()
        if not reply:
            raise RuntimeError("openai-codex returned empty output.")
        return reply, usage if isinstance(usage, dict) else None, str(used_model or selected_model)

    def generate(self, system_prompt: str, user_input: str, model: str, credentials: Dict[str, Any]) -> str:
        reply, _usage, _used_model = self._transport_text(system_prompt, user_input, model, credentials)
        return reply

    def probe(self, credentials: Dict[str, Any]) -> Dict[str, Any]:
        reply, _usage, used_model = self._transport_text("", PROVIDER_LIVE_PROBE_PROMPT, self.probe_model(credentials) or "gpt-5.4", credentials)
        return {
            "ok": True,
            "status": 200,
            "message": "Live probe succeeded.",
            "model": used_model,
            "reply": reply,
        }


class AnthropicAdapter(ProviderAdapter):
    provider_id = "anthropic"

    def _headers(self, credentials: Dict[str, Any]) -> Dict[str, str]:
        key = credentials.get("api_key") or ""
        if not key:
            raise RuntimeError("Anthropic api_key is required.")
        return {
            "x-api-key": str(key),
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

    def validate(self, credentials: Dict[str, Any]) -> Dict[str, Any]:
        res = _anthropic_models_request(credentials)
        result = _validation_result("Anthropic", res)
        if int(result.get("status") or 500) == 401:
            result["message"] = "Anthropic API key is invalid."
        return result

    def list_models(self, credentials: Dict[str, Any]) -> List[str]:
        res = _anthropic_models_request(credentials)
        body = res.get("json") or {}
        out = []
        for item in body.get("data", []) if isinstance(body.get("data"), list) else []:
            if isinstance(item, dict):
                model_id = item.get("id")
                if isinstance(model_id, str):
                    out.append(model_id)
        return sorted(set(out))

    def list_model_records(self, credentials: Dict[str, Any]) -> List[Dict[str, Any]]:
        res = _anthropic_models_request(credentials)
        body = res.get("json") or {}
        fetched_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        records: List[Dict[str, Any]] = []
        seen: Set[str] = set()
        for item in body.get("data", []) if isinstance(body.get("data"), list) else []:
            record = normalize_live_model_record(
                self.provider_id,
                item,
                source="anthropic_models_api",
                fetched_at=fetched_at,
            )
            if not record or record["id"] in seen:
                continue
            seen.add(record["id"])
            records.append(record)
        return sorted(records, key=lambda item: str(item.get("id") or ""))

    def generate(self, system_prompt: str, user_input: str, model: str, credentials: Dict[str, Any]) -> str:
        from server_modules.model_router import call_model_sync

        result = call_model_sync(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_input},
            ],
            model=model,
            provider=self.provider_id,
            credentials=credentials,
            max_tokens=1024,
        )
        return str(result.get("content") or "").strip()


class ClaudeCodeCLIAdapter(ProviderAdapter):
    provider_id = "claude_code_cli"

    def validate(self, credentials: Dict[str, Any]) -> Dict[str, Any]:
        _ = credentials  # Stored record exists for routing/selection; auth lives in the local Claude CLI session.
        status = claude_code_cli_status()
        if not status.get("available"):
            raise RuntimeError(status.get("message") or "Claude Code CLI is not installed.")
        if not status.get("logged_in"):
            return {
                "ok": False,
                "status": 401,
                "message": status.get("message") or "Claude subscription is not signed in yet.",
            }
        return {
            "ok": True,
            "status": 200,
            "message": status.get("message") or "Claude subscription is signed in on this machine.",
        }

    def list_models(self, credentials: Dict[str, Any]) -> List[str]:
        _ = credentials
        return ["sonnet", "opus"]

    def generate(self, system_prompt: str, user_input: str, model: str, credentials: Dict[str, Any]) -> str:
        _ = credentials
        return run_claude_code_cli(system_prompt, user_input, model or "sonnet", timeout=120)


class GeminiAdapter(ProviderAdapter):
    provider_id = "gemini"

    def _auth_params(self, credentials: Dict[str, Any]) -> Dict[str, str]:
        auth_mode = normalize_auth_mode(self.provider_id, credentials=credentials)
        access_token = str(credentials.get("access_token") or "").strip()
        if access_token.lower().startswith("bearer "):
            access_token = access_token[7:].strip()
        project_id = str(credentials.get("project_id") or "").strip()
        if auth_mode == "gemini_cli_oauth" or access_token:
            if not access_token:
                raise RuntimeError("Gemini CLI OAuth credential requires access_token.")
            if not project_id:
                raise RuntimeError("Gemini CLI OAuth credential requires project_id.")
            return {
                "mode": "oauth",
                "access_token": access_token,
                "project_id": project_id,
            }
        key = str(credentials.get("api_key") or "").strip()
        if not key:
            raise RuntimeError("Gemini api_key is required.")
        return {"mode": "api_key", "api_key": key}

    def validate(self, credentials: Dict[str, Any]) -> Dict[str, Any]:
        auth = self._auth_params(credentials)
        if auth["mode"] == "oauth":
            res = http_json_request(
                "https://generativelanguage.googleapis.com/v1beta/models",
                headers={
                    "Authorization": f"Bearer {auth['access_token']}",
                    "x-goog-user-project": auth["project_id"],
                },
            )
        else:
            res = http_json_request(f"https://generativelanguage.googleapis.com/v1beta/models?key={quote_plus(auth['api_key'])}")
        return _validation_result("Gemini", res)

    def list_models(self, credentials: Dict[str, Any]) -> List[str]:
        auth = self._auth_params(credentials)
        if auth["mode"] == "oauth":
            res = http_json_request(
                "https://generativelanguage.googleapis.com/v1beta/models",
                headers={
                    "Authorization": f"Bearer {auth['access_token']}",
                    "x-goog-user-project": auth["project_id"],
                },
            )
        else:
            res = http_json_request(f"https://generativelanguage.googleapis.com/v1beta/models?key={quote_plus(auth['api_key'])}")
        body = res.get("json") or {}
        out = []
        for item in body.get("models", []) if isinstance(body.get("models"), list) else []:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            methods = item.get("supportedGenerationMethods", [])
            if not isinstance(name, str):
                continue
            if isinstance(methods, list) and "generateContent" not in methods:
                continue
            out.append(name.split("/")[-1])
        return sorted(set(out))

    def list_model_records(self, credentials: Dict[str, Any]) -> List[Dict[str, Any]]:
        auth = self._auth_params(credentials)
        if auth["mode"] == "oauth":
            res = http_json_request(
                "https://generativelanguage.googleapis.com/v1beta/models",
                headers={
                    "Authorization": f"Bearer {auth['access_token']}",
                    "x-goog-user-project": auth["project_id"],
                },
            )
        else:
            res = http_json_request(f"https://generativelanguage.googleapis.com/v1beta/models?key={quote_plus(auth['api_key'])}")
        body = res.get("json") or {}
        fetched_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        records: List[Dict[str, Any]] = []
        seen: Set[str] = set()
        for item in body.get("models", []) if isinstance(body.get("models"), list) else []:
            if not isinstance(item, dict):
                continue
            methods = item.get("supportedGenerationMethods", [])
            if isinstance(methods, list) and "generateContent" not in methods:
                continue
            record = normalize_live_model_record(
                self.provider_id,
                item,
                source="gemini_models_api",
                fetched_at=fetched_at,
            )
            if not record or record["id"] in seen:
                continue
            seen.add(record["id"])
            records.append(record)
        return sorted(records, key=lambda item: str(item.get("id") or ""))

    def generate(self, system_prompt: str, user_input: str, model: str, credentials: Dict[str, Any]) -> str:
        from server_modules.model_router import call_model_sync

        result = call_model_sync(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_input},
            ],
            model=model,
            provider=self.provider_id,
            credentials=credentials,
        )
        return str(result.get("content") or "").strip()


class VertexAdapter(ProviderAdapter):
    provider_id = "vertex"

    def _params(self, credentials: Dict[str, Any]):
        token = credentials.get("access_token") or ""
        project = credentials.get("project_id") or ""
        location = credentials.get("location") or "us-central1"
        if not token:
            raise RuntimeError("Vertex access_token is required.")
        if not project:
            raise RuntimeError("Vertex requires project_id and location")
        return str(token), str(project), str(location)

    def validate(self, credentials: Dict[str, Any]) -> Dict[str, Any]:
        token, project, location = self._params(credentials)
        url = f"https://{location}-aiplatform.googleapis.com/v1/projects/{project}/locations/{location}/publishers/google/models"
        res = http_json_request(url, headers={"Authorization": f"Bearer {token}"})
        return _validation_result("Vertex", res)

    def list_models(self, credentials: Dict[str, Any]) -> List[str]:
        token, project, location = self._params(credentials)
        url = f"https://{location}-aiplatform.googleapis.com/v1/projects/{project}/locations/{location}/publishers/google/models"
        res = http_json_request(url, headers={"Authorization": f"Bearer {token}"})
        body = res.get("json") or {}
        out = []
        for item in body.get("models", []) if isinstance(body.get("models"), list) else []:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if isinstance(name, str):
                out.append(name.split("/")[-1])
        return sorted(set(out))

    def list_model_records(self, credentials: Dict[str, Any]) -> List[Dict[str, Any]]:
        token, project, location = self._params(credentials)
        url = f"https://{location}-aiplatform.googleapis.com/v1/projects/{project}/locations/{location}/publishers/google/models"
        res = http_json_request(url, headers={"Authorization": f"Bearer {token}"})
        body = res.get("json") or {}
        fetched_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        records: List[Dict[str, Any]] = []
        seen: Set[str] = set()
        for item in body.get("models", []) if isinstance(body.get("models"), list) else []:
            record = normalize_live_model_record(
                self.provider_id,
                item,
                source="vertex_models_api",
                fetched_at=fetched_at,
            )
            if not record or record["id"] in seen:
                continue
            seen.add(record["id"])
            records.append(record)
        return sorted(records, key=lambda item: str(item.get("id") or ""))

    def generate(self, system_prompt: str, user_input: str, model: str, credentials: Dict[str, Any]) -> str:
        token, project, location = self._params(credentials)
        url = (
            f"https://{location}-aiplatform.googleapis.com/v1/projects/{project}/locations/{location}"
            f"/publishers/google/models/{quote_plus(model)}:generateContent"
        )
        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_input}]}],
        }
        res = http_json_request(
            url,
            method="POST",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            payload=payload,
            timeout=60,
        )
        body = res.get("json")
        if not isinstance(body, dict):
            raise RuntimeError("Vertex returned invalid response.")
        candidates = body.get("candidates")
        texts = []
        if isinstance(candidates, list):
            for cand in candidates:
                if not isinstance(cand, dict):
                    continue
                content = cand.get("content", {})
                if not isinstance(content, dict):
                    continue
                for part in content.get("parts", []) if isinstance(content.get("parts"), list) else []:
                    if isinstance(part, dict):
                        text = part.get("text")
                        if isinstance(text, str) and text.strip():
                            texts.append(text.strip())
        if texts:
            return "\n".join(texts)
        raise RuntimeError("Vertex response did not include text content.")


class BedrockAdapter(ProviderAdapter):
    provider_id = "bedrock"

    def _client(self, credentials: Dict[str, Any]):
        try:
            import boto3  # type: ignore
        except Exception as exc:
            raise RuntimeError("Amazon Bedrock model sync requires boto3 to be installed.") from exc
        access_key = str(credentials.get("api_key") or credentials.get("aws_access_key_id") or "").strip()
        secret_key = str(credentials.get("aws_secret_access_key") or credentials.get("secret_key") or "").strip()
        region = str(credentials.get("region") or credentials.get("aws_region") or "us-east-1").strip()
        kwargs: Dict[str, Any] = {"region_name": region}
        if access_key and secret_key:
            kwargs["aws_access_key_id"] = access_key
            kwargs["aws_secret_access_key"] = secret_key
        session_token = str(credentials.get("aws_session_token") or "").strip()
        if session_token:
            kwargs["aws_session_token"] = session_token
        return boto3.client("bedrock", **kwargs)

    def validate(self, credentials: Dict[str, Any]) -> Dict[str, Any]:
        models = self.list_models(credentials)
        return {
            "ok": True,
            "status": 200,
            "message": "Amazon Bedrock credential is valid.",
            "models_preview": models[:25],
        }

    def list_models(self, credentials: Dict[str, Any]) -> List[str]:
        response = self._client(credentials).list_foundation_models()
        models: List[str] = []
        for item in response.get("modelSummaries", []) if isinstance(response.get("modelSummaries"), list) else []:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("modelId") or "").strip()
            if model_id:
                models.append(model_id)
        return sorted(set(models))

    def list_model_records(self, credentials: Dict[str, Any]) -> List[Dict[str, Any]]:
        response = self._client(credentials).list_foundation_models()
        fetched_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        records: List[Dict[str, Any]] = []
        seen: Set[str] = set()
        for item in response.get("modelSummaries", []) if isinstance(response.get("modelSummaries"), list) else []:
            if not isinstance(item, dict):
                continue
            raw = {
                "id": item.get("modelId"),
                "label": item.get("modelName") or item.get("modelId"),
                "supports_tools": True,
                "raw_bedrock": item,
            }
            record = normalize_live_model_record(
                self.provider_id,
                raw,
                source="bedrock_list_foundation_models",
                fetched_at=fetched_at,
            )
            if not record or record["id"] in seen:
                continue
            seen.add(record["id"])
            records.append(record)
        return sorted(records, key=lambda item: str(item.get("id") or ""))

    def generate(self, system_prompt: str, user_input: str, model: str, credentials: Dict[str, Any]) -> str:
        raise RuntimeError("Amazon Bedrock generation is not wired for Studio agents yet.")


PROVIDER_ADAPTERS: Dict[str, ProviderAdapter] = {
    "openai": OpenAIAdapter(),
    "openai-codex": OpenAICodexAdapter(),
    "anthropic": AnthropicAdapter(),
    "claude_code_cli": ClaudeCodeCLIAdapter(),
    "gemini": GeminiAdapter(),
    "vertex": VertexAdapter(),
    "groq": OpenAICompatibleAdapter("groq", "Groq"),
    "openrouter": OpenAICompatibleAdapter("openrouter", "OpenRouter"),
    "xai": OpenAICompatibleAdapter("xai", "xAI"),
    "azure_openai": OpenAICompatibleAdapter("azure_openai", "Azure OpenAI"),
    "bedrock": BedrockAdapter(),
    "qwen": OpenAICompatibleAdapter("qwen", "Qwen"),
    "deepseek": OpenAICompatibleAdapter("deepseek", "DeepSeek"),
    "mistral": OpenAICompatibleAdapter("mistral", "Mistral"),
    "custom_openai_compatible": OpenAICompatibleAdapter("custom_openai_compatible", "Custom OpenAI-compatible"),
    "ollama": OpenAICompatibleAdapter("ollama", "Ollama", requires_auth=False),
    "ollama_cloud": OllamaCloudAdapter(),
}

# Approximate token pricing per 1K tokens; used only for masked telemetry.
# For DeepSeek, the official public pricing page currently lists cache-hit, cache-miss,
# and output token rates. The masked telemetry fallback uses the cache-miss input rate
# plus standard output rate so estimates do not understate spend.
PROVIDER_COST_PER_1K = {
    provider: {
        "input": float(payload.get("input") or 0.0),
        "output": float(payload.get("output") or 0.0),
    }
    for provider, payload in pricing_registry_service.provider_fallback_rates_per_1k().items()
}


def estimate_tokens(text: str) -> int:
    return usage_accounting_service.estimate_tokens(text)


def masked_cost_band(cost_usd: float) -> str:
    return usage_accounting_service.masked_cost_band(cost_usd)


def build_masked_usage(provider: str, model: str, input_text: str, output_text: str) -> Dict[str, Any]:
    return usage_accounting_service.build_masked_usage(
        provider,
        model,
        input_text,
        output_text,
    )


# ---------------------------------------------------------------------------
# Profile helpers (moved from server.py)
# ---------------------------------------------------------------------------

def _persist_provider_profiles():
    _init()
    _server.sync_acp_manager_paths(provider_profiles_path=_server.ORION_PROVIDER_PROFILES_FILE)
    _server.ACP_MANAGER._persist_provider_profiles()


def _load_provider_profiles():
    _init()
    with _server.PROFILES_LOCK:
        _server.sync_acp_manager_paths(provider_profiles_path=_server.ORION_PROVIDER_PROFILES_FILE)
        _server.ACP_MANAGER.reload_secondary_state()


def classify_profile_failure(raw_error: str) -> Dict[str, Any]:
    _init()
    lowered = str(raw_error or "").lower()
    if "401" in lowered or "403" in lowered or "api_key" in lowered or "unauthorized" in lowered:
        return {
            "failure_class": "auth",
            "retryable": False,
            "backpressure": False,
            "cooldown_seconds": max(60, _server.ORION_PROFILE_COOLDOWN_AUTH_SECONDS),
        }
    if "429" in lowered or "rate limit" in lowered:
        return {
            "failure_class": "rate_limited",
            "retryable": True,
            "backpressure": True,
            "cooldown_seconds": max(30, _server.ORION_PROFILE_COOLDOWN_RATE_LIMIT_SECONDS),
        }
    return {
        "failure_class": "transient",
        "retryable": True,
        "backpressure": False,
        "cooldown_seconds": max(15, _server.ORION_PROFILE_COOLDOWN_TRANSIENT_SECONDS),
    }


def _profile_cooldown_seconds_for_error(raw_error: str) -> int:
    return int(classify_profile_failure(raw_error).get("cooldown_seconds") or 15)


def _profile_ready(profile: Dict[str, Any], ref: Optional[datetime] = None) -> bool:
    if not bool(profile.get("enabled", True)):
        return False
    now = ref or _utc_now()
    cooldown_until = _parse_utc_ts(profile.get("cooldown_until"))
    if cooldown_until is None:
        return True
    return cooldown_until <= now


def _mark_profile_success(profile_id: str):
    _init()
    with _server.PROFILES_LOCK:
        profile = _server.PROVIDER_PROFILES.get(profile_id)
        if not isinstance(profile, dict):
            return
        profile["last_used_at"] = _utc_now_iso()
        profile["last_success_at"] = _utc_now_iso()
        profile["last_error"] = None
        profile["cooldown_until"] = None
        profile["success_count"] = int(profile.get("success_count", 0)) + 1
        profile["updated_at"] = _utc_now_iso()
        _server.PROVIDER_PROFILES[profile_id] = profile
    _persist_provider_profiles()


def _mark_profile_failure(profile_id: str, error_text: str):
    _init()
    cooldown_seconds = _profile_cooldown_seconds_for_error(error_text)
    cooldown_until = (_utc_now() + timedelta(seconds=cooldown_seconds)).isoformat().replace("+00:00", "Z")
    with _server.PROFILES_LOCK:
        profile = _server.PROVIDER_PROFILES.get(profile_id)
        if not isinstance(profile, dict):
            return
        profile["last_error"] = error_text[:1200]
        profile["last_failure_at"] = _utc_now_iso()
        profile["failure_count"] = int(profile.get("failure_count", 0)) + 1
        profile["cooldown_until"] = cooldown_until
        profile["updated_at"] = _utc_now_iso()
        _server.PROVIDER_PROFILES[profile_id] = profile
    _persist_provider_profiles()


def _sorted_profiles(provider: str, workspace_id: Optional[str], preferred_profile_id: Optional[str] = None) -> List[Dict[str, Any]]:
    _init()
    requested_ws = str(workspace_id or "default").strip() or "default"
    provider_id = normalize_provider_id(provider)
    preferred = str(preferred_profile_id or "").strip()
    with _server.PROFILES_LOCK:
        values = [dict(item) for item in _server.PROVIDER_PROFILES.values() if isinstance(item, dict)]
    filtered: List[Dict[str, Any]] = []
    now = _utc_now()
    for profile in values:
        if normalize_provider_id(profile.get("provider")) != provider_id:
            continue
        profile_ws = str(profile.get("workspace_id") or "default").strip() or "default"
        if profile_ws != requested_ws:
            continue
        if not _profile_ready(profile, now):
            continue
        filtered.append(profile)
    filtered.sort(key=lambda p: (0 if str(p.get("id") or "") == preferred else 1, int(p.get("priority", 100)), str(p.get("created_at") or "")))
    return filtered


_OPENAI_COMPATIBLE_CONFIG_KEYS = {
    "base_url",
    "endpoint",
    "azure_endpoint",
    "api_version",
    "azure_api_version",
    "deployment",
    "deployment_name",
    "azure_deployment",
    "model",
    "model_id",
}

BYOK_FIRST_OPENAI_COMPATIBLE_PROVIDER_IDS = {
    "groq",
    "openrouter",
    "azure_openai",
    "custom_openai_compatible",
}


def _merge_profile_config_into_credentials(
    credentials: Dict[str, Any],
    profile: Dict[str, Any],
) -> Dict[str, Any]:
    merged = dict(credentials or {})
    metadata = profile.get("metadata") if isinstance(profile.get("metadata"), dict) else {}
    for key in _OPENAI_COMPATIBLE_CONFIG_KEYS:
        if str(merged.get(key) or "").strip():
            continue
        value = metadata.get(key)
        if value in {None, ""}:
            continue
        merged[key] = value
    if not str(merged.get("model") or "").strip() and str(profile.get("model") or "").strip():
        merged["model"] = str(profile.get("model") or "").strip()
    return merged


def openai_compatible_provider_config_error(
    provider: Any,
    credentials: Optional[Dict[str, Any]],
    *,
    model: Any = None,
) -> str:
    provider_id = normalize_provider_id(provider)
    if provider_id not in BYOK_FIRST_OPENAI_COMPATIBLE_PROVIDER_IDS:
        return ""
    payload = credentials if isinstance(credentials, dict) else {}
    secret = str(
        payload.get("api_key")
        or payload.get("access_token")
        or payload.get("oauth_token")
        or payload.get("token")
        or ""
    ).strip()
    if not secret:
        return f"{provider_id} requires a workspace vault credential."
    base_url = str(payload.get("base_url") or "").strip()
    if provider_id == "azure_openai":
        deployment = str(
            payload.get("deployment")
            or payload.get("deployment_name")
            or payload.get("azure_deployment")
            or payload.get("model")
            or payload.get("model_id")
            or model
            or ""
        ).strip()
        endpoint = str(payload.get("endpoint") or payload.get("azure_endpoint") or "").strip()
        api_version = str(payload.get("api_version") or payload.get("azure_api_version") or "").strip()
        if not api_version:
            return "azure_openai requires api_version."
        if not ((base_url and "/openai/deployments/" in base_url) or (endpoint and deployment)):
            return "azure_openai requires endpoint/base_url and deployment/model."
    if provider_id == "custom_openai_compatible":
        model_id = str(payload.get("model") or payload.get("model_id") or model or "").strip()
        if not base_url:
            return "custom_openai_compatible requires base_url."
        if not model_id:
            return "custom_openai_compatible requires model_id."
    return ""


def _build_provider_credential_candidates(context: Dict[str, Any], metadata: Dict[str, Any], provider: str) -> List[Dict[str, Any]]:
    _init()
    workspace_id = str(context.get("workspace_id") or metadata.get("workspace_id") or "default").strip() or "default"
    tenant_id = str(context.get("tenant_id") or metadata.get("tenant_id") or "").strip() or None
    run_id = str(context.get("run_id") or metadata.get("run_id") or "").strip() or None
    credential_id = context.get("credential_id") or metadata.get("credential_id")
    canonical_provider = normalize_provider_id(provider)
    workspace_only = bool(metadata.get("workspace_only"))
    candidates: List[Dict[str, Any]] = []
    seen_labels: Set[str] = set()

    if credential_id:
        credentials = resolve_vault_credential(
            str(credential_id),
            workspace_id,
            tenant_id=tenant_id,
            provider=canonical_provider,
            tool_name="provider_candidate_resolution",
            run_id=run_id,
            purpose="provider_candidate_resolution",
            actor_type="provider_profile",
        )
        config_error = openai_compatible_provider_config_error(
            canonical_provider,
            credentials,
            model=context.get("model") or metadata.get("model"),
        )
        if config_error:
            return []
        candidates.append(
            {
                "source": "credential_id",
                "credentials": credentials,
                "profile_id": None,
                "label": f"credential:{credential_id}",
            }
        )
        seen_labels.add(f"credential:{credential_id}")

    if isinstance(metadata.get("credentials"), dict) and metadata.get("credentials"):
        config_error = openai_compatible_provider_config_error(
            canonical_provider,
            metadata.get("credentials"),
            model=context.get("model") or metadata.get("model"),
        )
        if config_error:
            return []
        candidates.append(
            {
                "source": "inline",
                "credentials": metadata.get("credentials"),
                "profile_id": None,
                "label": "inline",
            }
        )
        seen_labels.add("inline")

    profile_id = str(metadata.get("profile_id") or "").strip()
    profiles = _sorted_profiles(canonical_provider, workspace_id, profile_id if profile_id else None)
    for profile in profiles:
        pid = str(profile.get("id") or "").strip()
        cid = str(profile.get("credential_id") or "").strip()
        profile_auth_mode = normalize_auth_mode(profile.get("provider") or canonical_provider, profile.get("auth_mode"))
        if not pid or not cid:
            if not pid or provider_requires_credential(profile.get("provider") or canonical_provider, profile_auth_mode):
                continue
        label = f"profile:{pid}"
        if label in seen_labels:
            continue
        if cid:
            try:
                credentials = resolve_vault_credential(
                    cid,
                    workspace_id,
                    tenant_id=tenant_id,
                    provider=canonical_provider,
                    tool_name="provider_candidate_resolution",
                    run_id=run_id,
                    purpose="provider_candidate_resolution",
                    actor_type="provider_profile",
                )
            except Exception as exc:
                _mark_profile_failure(pid, f"Credential resolution failed: {exc}")
                continue
        else:
            credentials = secretless_provider_credentials(profile.get("provider") or canonical_provider, profile_auth_mode)
        credentials = _merge_profile_config_into_credentials(credentials, profile)
        config_error = openai_compatible_provider_config_error(
            canonical_provider,
            credentials,
            model=profile.get("model") or context.get("model") or metadata.get("model"),
        )
        if config_error:
            _mark_profile_failure(pid, config_error)
            continue
        candidates.append(
            {
                "source": "profile",
                "credentials": credentials,
                "profile_id": pid,
                "label": label,
                "model": normalize_provider_model_id(canonical_provider, profile.get("model"), fallback_to_default=True),
            }
        )
        seen_labels.add(label)

    if canonical_provider == "openai":
        try:
            fallback = resolve_default_vault_credential(
                "openai",
                workspace_id,
                tenant_id=tenant_id,
                tool_name="provider_candidate_resolution",
                run_id=run_id,
                purpose="provider_default_resolution",
                actor_type="provider_profile",
            )
            if "vault-default" not in seen_labels:
                candidates.append(
                    {
                        "source": "vault_default",
                        "credentials": fallback,
                        "profile_id": None,
                        "label": "vault-default",
                    }
                )
                seen_labels.add("vault-default")
        except Exception:
            pass
        if not workspace_only:
            env_key, env_source = _resolve_hosted_openai_bearer(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                run_id=run_id,
                purpose="provider_candidate_resolution",
            )
            if env_key and "env-openai" not in seen_labels:
                candidates.append(
                    {
                        "source": "env",
                        "credentials": _openai_env_credentials(env_key, env_source),
                        "profile_id": None,
                        "label": "env-openai",
                    }
                )
                seen_labels.add("env-openai")

    if canonical_provider == "openai-codex":
        try:
            fallback = resolve_default_vault_credential(
                "openai-codex",
                workspace_id,
                tenant_id=tenant_id,
                tool_name="provider_candidate_resolution",
                run_id=run_id,
                purpose="provider_default_resolution",
                actor_type="provider_profile",
            )
            if "vault-default-codex" not in seen_labels:
                candidates.append(
                    {
                        "source": "vault_default",
                        "credentials": fallback,
                        "profile_id": None,
                        "label": "vault-default-codex",
                    }
                )
                seen_labels.add("vault-default-codex")
        except Exception:
            pass
        if not workspace_only:
            env_key, env_source = _resolve_hosted_openai_bearer(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                run_id=run_id,
                purpose="provider_candidate_resolution",
            )
            normalized_env_source = str(env_source or "").strip().lower()
            if env_key and normalized_env_source in OPENAI_CODEX_OAUTH_SOURCES and "env-openai-codex" not in seen_labels:
                candidates.append(
                    {
                        "source": "env",
                        "credentials": _openai_env_credentials(env_key, env_source),
                        "profile_id": None,
                        "label": "env-openai-codex",
                    }
                )
                seen_labels.add("env-openai-codex")

    if canonical_provider == "anthropic" and not workspace_only:
        env_key, _ = _resolve_hosted_provider_api_key(
            provider_id="anthropic",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            run_id=run_id,
            purpose="provider_candidate_resolution",
        )
        if env_key and "env-anthropic" not in seen_labels:
            candidates.append(
                {
                    "source": "env",
                    "credentials": {
                        "api_key": env_key,
                    },
                    "profile_id": None,
                    "label": "env-anthropic",
                }
            )
            seen_labels.add("env-anthropic")

    if canonical_provider == "anthropic" and not workspace_only and claude_code_cli_available() and "local-claude-cli" not in seen_labels:
        candidates.append(
            {
                "source": "local_cli",
                "credentials": secretless_provider_credentials("anthropic", "local_cli"),
                "profile_id": None,
                "label": "local-claude-cli",
            }
        )
        seen_labels.add("local-claude-cli")

    if canonical_provider == "gemini" and not workspace_only:
        env_key, _ = _resolve_hosted_provider_api_key(
            provider_id="gemini",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            run_id=run_id,
            purpose="provider_candidate_resolution",
        )
        if env_key and "env-gemini" not in seen_labels:
            candidates.append(
                {
                    "source": "env",
                    "credentials": {
                        "api_key": env_key,
                    },
                    "profile_id": None,
                    "label": "env-gemini",
                }
            )
            seen_labels.add("env-gemini")

    if canonical_provider == "qwen":
        env_key, _ = _resolve_hosted_provider_api_key(
            provider_id="qwen",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            run_id=run_id,
            purpose="provider_candidate_resolution",
        )
        if env_key and "env-qwen" not in seen_labels:
            candidates.append(
                {
                    "source": "env",
                    "credentials": {"api_key": env_key},
                    "profile_id": None,
                    "label": "env-qwen",
                }
            )
            seen_labels.add("env-qwen")

    if canonical_provider == "deepseek":
        env_key, _ = _resolve_hosted_provider_api_key(
            provider_id="deepseek",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            run_id=run_id,
            purpose="provider_candidate_resolution",
        )
        if env_key and "env-deepseek" not in seen_labels:
            candidates.append(
                {
                    "source": "env",
                    "credentials": {"api_key": env_key},
                    "profile_id": None,
                    "label": "env-deepseek",
                }
            )
            seen_labels.add("env-deepseek")

    if canonical_provider == "mistral":
        env_key, _ = _resolve_hosted_provider_api_key(
            provider_id="mistral",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            run_id=run_id,
            purpose="provider_candidate_resolution",
        )
        if env_key and "env-mistral" not in seen_labels:
            candidates.append(
                {
                    "source": "env",
                    "credentials": {"api_key": env_key},
                    "profile_id": None,
                    "label": "env-mistral",
                }
            )
            seen_labels.add("env-mistral")

    if canonical_provider == "ollama_cloud":
        env_key = str(
            os.getenv("ORION_LOCAL_WORKER_OLLAMA_CLOUD_API_KEY")
            or os.getenv("OLLAMA_API_KEY")
            or ""
        ).strip()
        if env_key and "env-ollama-cloud" not in seen_labels:
            candidates.append(
                {
                    "source": "env",
                    "credentials": {"api_key": env_key},
                    "profile_id": None,
                    "label": "env-ollama-cloud",
                }
            )
            seen_labels.add("env-ollama-cloud")

    if canonical_provider == "ollama" and "local-ollama" not in seen_labels:
        candidates.append(
            {
                "source": "local",
                "credentials": secretless_provider_credentials("ollama", "none"),
                "profile_id": None,
                "label": "local-ollama",
            }
        )
        seen_labels.add("local-ollama")

    return candidates


def _workspace_user_facing_auth_modes(provider_id: str, catalog_entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    auth_modes = [
        dict(item)
        for item in list(catalog_entry.get("auth_modes") or [])
        if isinstance(item, dict)
    ]
    if provider_id not in WORKSPACE_USER_FACING_AI_PROVIDERS:
        return auth_modes
    api_key_modes = [item for item in auth_modes if str(item.get("id") or "").strip() == "api_key"]
    return api_key_modes or auth_modes


def _profile_health_value(profile: Dict[str, Any], ref: Optional[datetime] = None) -> str:
    if not bool(profile.get("enabled", True)):
        return "disabled"
    now = ref or _utc_now()
    cooldown_until = _parse_utc_ts(profile.get("cooldown_until"))
    if cooldown_until is not None and cooldown_until > now:
        return "cooldown"
    return "healthy"


def _profile_has_unresolved_failure(profile: Dict[str, Any]) -> bool:
    last_error = str(profile.get("last_error") or "").strip()
    if not last_error:
        return False
    last_failure_at = _parse_utc_ts(profile.get("last_failure_at"))
    last_success_at = _parse_utc_ts(profile.get("last_success_at"))
    if last_success_at is not None and last_failure_at is not None and last_success_at >= last_failure_at:
        return False
    if last_success_at is not None and last_failure_at is None:
        return False
    return True


def _default_vault_credential_present(provider_id: str, workspace_id: str, tenant_id: Optional[str]) -> bool:
    try:
        resolve_default_vault_credential(
            provider_id,
            workspace_id,
            tenant_id=tenant_id,
            tool_name="provider_catalog_truth",
            run_id=None,
            purpose="provider_catalog_truth",
            actor_type="provider_profile",
        )
        return True
    except Exception:
        return False


def _push_unique_issue(target: List[Dict[str, Any]], code: str, message: str) -> None:
    normalized_code = str(code or "").strip()
    normalized_message = str(message or "").strip()
    if not normalized_code or not normalized_message:
        return
    for item in target:
        if not isinstance(item, dict):
            continue
        if str(item.get("code") or "").strip() == normalized_code:
            return
    target.append({"code": normalized_code, "message": normalized_message})


def _register_provider_path(
    entry: Dict[str, Any],
    *,
    state: str,
    source: str,
    detail: str,
    issue_code: Optional[str] = None,
    failure_class: Optional[str] = None,
    retry_after_seconds: Optional[int] = None,
    backpressure: bool = False,
    connection_kind: Optional[str] = None,
    connection_scope: Optional[str] = None,
    connection_label: Optional[str] = None,
) -> None:
    paths = entry.setdefault("_paths", [])
    if not isinstance(paths, list):
        paths = []
        entry["_paths"] = paths
    source_token = str(source or "").strip()
    detail_text = str(detail or "").strip()
    payload: Dict[str, Any] = {
        "state": str(state or "").strip() or "setup_required",
        "source": source_token,
        "detail": detail_text,
    }
    if connection_kind:
        payload["connection_kind"] = str(connection_kind).strip()
    if connection_scope:
        payload["connection_scope"] = str(connection_scope).strip()
    if connection_label:
        payload["connection_label"] = str(connection_label).strip()
    if issue_code:
        payload["issue_code"] = str(issue_code).strip()
    if failure_class:
        payload["failure_class"] = str(failure_class).strip()
    if retry_after_seconds is not None:
        payload["retry_after_seconds"] = int(max(0, retry_after_seconds))
    if backpressure:
        payload["backpressure"] = True
    paths.append(payload)
    if source_token:
        sources = entry.setdefault("credential_sources", [])
        if isinstance(sources, list) and source_token not in sources:
            sources.append(source_token)
    if issue_code and detail_text:
        _push_unique_issue(entry.setdefault("issues", []), issue_code, detail_text)
    if failure_class and not entry.get("failure_class"):
        entry["failure_class"] = str(failure_class).strip()
    if backpressure:
        entry["backpressure"] = True
    if retry_after_seconds is not None:
        current_retry = entry.get("retry_after_seconds")
        if current_retry is None or int(retry_after_seconds) < int(current_retry):
            entry["retry_after_seconds"] = int(retry_after_seconds)


def _provider_state_rank(state: str) -> int:
    normalized = str(state or "").strip().lower()
    if normalized == "active":
        return 0
    if normalized == "degraded":
        return 1
    if normalized == "configured":
        return 2
    if normalized == "unavailable":
        return 3
    return 4


def _provider_path_boundary(source: str) -> tuple[str, str, str]:
    normalized = str(source or "").strip().lower()
    if (
        normalized == "codex_token_vault"
        or normalized.startswith("profile:local_cli")
        or normalized.startswith("local-")
        or normalized.endswith("-cli")
    ):
        return ("machine_local_capability", "machine", "This machine only")
    if normalized.startswith("env-") or normalized.startswith("env_") or normalized.startswith("env"):
        return ("runtime_environment", "runtime", "Runtime environment")
    if normalized.startswith("vault") or normalized.startswith("profile"):
        return ("workspace_provider_connection", "workspace", "Workspace provider")
    return ("workspace_provider_connection", "workspace", "Workspace provider")


def _finalize_provider_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    paths = entry.pop("_paths", [])
    if not isinstance(paths, list):
        paths = []
    chosen_state = "setup_required"
    chosen_issue_code = "setup_required"
    chosen_issue = "Provider setup is still required."
    chosen_source = None
    chosen_detail = None
    chosen_connection_kind = "workspace_provider_connection"
    chosen_connection_scope = "workspace"
    chosen_connection_label = "Workspace provider"
    for candidate_state in ("active", "degraded", "configured", "unavailable", "setup_required"):
        match = next((item for item in paths if isinstance(item, dict) and str(item.get("state") or "").strip() == candidate_state), None)
        if match is None:
            continue
        chosen_state = candidate_state
        chosen_source = str(match.get("source") or "").strip() or None
        boundary_kind, boundary_scope, boundary_label = _provider_path_boundary(chosen_source or "")
        chosen_connection_kind = str(match.get("connection_kind") or "").strip() or boundary_kind
        chosen_connection_scope = str(match.get("connection_scope") or "").strip() or boundary_scope
        chosen_connection_label = str(match.get("connection_label") or "").strip() or boundary_label
        chosen_detail = str(match.get("detail") or "").strip() or None
        if candidate_state == "active":
            chosen_issue_code = None
            chosen_issue = None
        else:
            chosen_issue_code = str(match.get("issue_code") or candidate_state).strip() or candidate_state
            chosen_issue = str(match.get("detail") or "").strip() or None
        break
    issues = entry.get("issues")
    if not isinstance(issues, list):
        issues = []
        entry["issues"] = issues
    if chosen_issue_code and chosen_issue:
        _push_unique_issue(issues, chosen_issue_code, chosen_issue)
    entry["state"] = chosen_state
    entry["usable"] = chosen_state == "active"
    entry["configured"] = chosen_state in {"active", "configured", "degraded", "unavailable"}
    entry["active"] = chosen_state == "active"
    entry["issue_code"] = chosen_issue_code
    entry["issue"] = chosen_issue
    entry["state_detail"] = chosen_detail
    entry["active_source"] = chosen_source
    entry["connection_kind"] = chosen_connection_kind
    entry["connection_scope"] = chosen_connection_scope
    entry["connection_label"] = chosen_connection_label
    entry["machine_bound"] = chosen_connection_scope == "machine"
    entry["credential_sources"] = sorted({str(item).strip() for item in entry.get("credential_sources", []) if str(item).strip()})
    return entry


def build_provider_runtime_truth(
    workspace_id: Optional[str] = None,
    *,
    openai_probe: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    _init()
    requested_workspace_id = str(workspace_id or "default").strip() or "default"
    tenant_id: Optional[str] = None
    now = _utc_now()
    with _server.PROFILES_LOCK:
        profiles = [dict(item) for item in _server.PROVIDER_PROFILES.values() if isinstance(item, dict)]

    items: List[Dict[str, Any]] = []
    profile_summary = {"healthy": 0, "cooldown": 0, "disabled": 0, "total": 0}
    provider_entries: Dict[str, Dict[str, Any]] = {}

    def _entry(provider_id: str) -> Dict[str, Any]:
        existing = provider_entries.get(provider_id)
        if existing is not None:
            return existing
        catalog_entry = provider_catalog_entry(provider_id)
        provider_scopes = [
            str(scope).strip()
            for scope in list(catalog_entry.get("provider_scopes") or [])
            if str(scope).strip()
        ]
        local_only_provider = "local_only" in provider_scopes
        created: Dict[str, Any] = {
            "id": provider_id,
            "kind": "provider",
            "label": catalog_entry.get("label", provider_id),
            "identity_owner": "local_machine" if local_only_provider else "platform_account",
            "identity_owner_label": "Local machine" if local_only_provider else "Empyralis account",
            "identity_boundary_note": (
                "This provider is bound to the local machine runtime and does not require a workspace credential."
                if local_only_provider
                else "Platform sign-in stays separate from provider capabilities and machine-local sessions."
            ),
            "auth": list(catalog_entry.get("auth", [])),
            "auth_modes": list(catalog_entry.get("auth_modes", [])),
            "default_auth_mode": catalog_entry.get("default_auth_mode"),
            "default_model": catalog_entry.get("default_model"),
            "note": catalog_entry.get("note"),
            "profile_count": 0,
            "enabled_profile_count": 0,
            "disabled_profile_count": 0,
            "healthy_profile_count": 0,
            "cooldown_profile_count": 0,
            "credential_sources": [],
            "issues": [],
            "backpressure": False,
            "retry_after_seconds": None,
            "failure_class": None,
        }
        provider_entries[provider_id] = created
        return created

    for provider_id, info in PROVIDER_CATALOG.items():
        if bool(info.get("hidden")):
            continue
        _entry(provider_id)

    for profile in profiles:
        workspace = str(profile.get("workspace_id") or "default").strip() or "default"
        if workspace != requested_workspace_id:
            continue
        provider_id = normalize_provider_id(profile.get("provider"))
        if not provider_id or bool(provider_catalog_entry(provider_id).get("hidden")):
            continue
        health = _profile_health_value(profile, now)
        item = {
            "id": profile.get("id"),
            "provider": provider_id,
            "label": profile.get("label"),
            "credential_id": profile.get("credential_id"),
            "auth_mode": normalize_auth_mode(provider_id, profile.get("auth_mode")),
            "workspace_id": workspace,
            "priority": profile.get("priority", 100),
            "enabled": bool(profile.get("enabled", True)),
            "model": normalize_provider_model_id(provider_id, profile.get("model"), fallback_to_default=True),
            "health": health,
            "cooldown_until": profile.get("cooldown_until"),
            "last_error": profile.get("last_error"),
            "last_used_at": profile.get("last_used_at"),
            "last_success_at": profile.get("last_success_at"),
            "last_failure_at": profile.get("last_failure_at"),
            "success_count": int(profile.get("success_count", 0)),
            "failure_count": int(profile.get("failure_count", 0)),
            "created_at": profile.get("created_at"),
            "updated_at": profile.get("updated_at"),
            "metadata": dict(profile.get("metadata") or {}) if isinstance(profile.get("metadata"), dict) else {},
        }
        items.append(item)
        profile_summary[health] += 1
        profile_summary["total"] += 1

        entry = _entry(provider_id)
        entry["profile_count"] += 1
        current_priority = int(item.get("priority") or 100)
        existing_priority = int(entry.get("selected_profile_priority") or 1000000)
        if "selected_profile_priority" not in entry or current_priority <= existing_priority:
            entry["profile_metadata"] = dict(item.get("metadata") or {}) if isinstance(item.get("metadata"), dict) else {}
            entry["selected_profile_priority"] = current_priority
        if item["enabled"]:
            entry["enabled_profile_count"] += 1
        else:
            entry["disabled_profile_count"] += 1
        if health == "healthy":
            entry["healthy_profile_count"] += 1
        elif health == "cooldown":
            entry["cooldown_profile_count"] += 1

        auth_mode = normalize_auth_mode(provider_id, item.get("auth_mode"))
        unresolved_failure = _profile_has_unresolved_failure(profile)
        failure_info = classify_profile_failure(str(profile.get("last_error") or "")) if unresolved_failure else {}
        retry_after_seconds = None
        cooldown_until = _parse_utc_ts(profile.get("cooldown_until"))
        if cooldown_until is not None and cooldown_until > now:
            retry_after_seconds = max(0, int((cooldown_until - now).total_seconds()))

        if auth_mode == "local_cli" and provider_id == "anthropic":
            cli_status = claude_code_cli_status(timeout=PROVIDER_STATUS_PROBE_TIMEOUT_SECONDS)
            if not cli_status.get("available"):
                _register_provider_path(
                    entry,
                    state="unavailable",
                    source="profile:local_cli",
                    detail="Claude subscription is configured for this workspace, but the Claude CLI is not installed on this machine.",
                    issue_code="local_cli_missing",
                )
                continue
            if not cli_status.get("logged_in"):
                _register_provider_path(
                    entry,
                    state="setup_required",
                    source="profile:local_cli",
                    detail="Claude CLI is installed, but the Claude subscription is not signed in on this machine yet.",
                    issue_code="local_cli_not_signed_in",
                )
                continue

        if health == "disabled":
            _register_provider_path(
                entry,
                state="configured",
                source="profile",
                detail="A provider profile exists in this workspace but is currently disabled.",
                issue_code="profile_disabled",
            )
            continue

        if health == "cooldown" or unresolved_failure:
            detail = str(profile.get("last_error") or "").strip() or "Provider profile is cooling down after a runtime failure."
            _register_provider_path(
                entry,
                state="degraded",
                source="profile",
                detail=detail,
                issue_code=str(failure_info.get("failure_class") or "profile_degraded"),
                failure_class=str(failure_info.get("failure_class") or "").strip() or None,
                retry_after_seconds=retry_after_seconds,
                backpressure=bool(failure_info.get("backpressure")),
            )
            continue

        _register_provider_path(
            entry,
            state="active",
            source="profile",
            detail="An enabled provider profile is ready for this workspace.",
        )

    openai_env_token, openai_env_source = _resolve_hosted_openai_bearer(
        tenant_id=tenant_id,
        workspace_id=requested_workspace_id,
        run_id=None,
        purpose="provider_catalog_truth",
    )
    openai_env_source = str(openai_env_source or "").strip().lower()
    openai_env_present = bool(str(openai_env_token or "").strip())

    openai_entry = _entry("openai")
    if openai_probe:
        source = str(openai_probe.get("openai_credential_source") or "").strip().lower()
        source_label = source or "direct_openai"
        if bool(openai_probe.get("openai_key_valid")) and source in {"env_api_key", "env_access_token", "vault"}:
            _register_provider_path(
                openai_entry,
                state="active",
                source=source_label,
                detail="A direct OpenAI credential is verified and available to the runtime.",
            )
        elif bool(openai_probe.get("openai_key_present")) or bool(openai_probe.get("openai_vault_present")):
            error_text = str(openai_probe.get("openai_error") or "").strip() or "Direct OpenAI credential is present but not currently usable."
            failure_info = classify_profile_failure(error_text)
            degraded = str(source or openai_env_source) in {"env_api_key", "env_access_token", "vault"}
            _register_provider_path(
                openai_entry,
                state="degraded" if degraded else "setup_required",
                source=source_label,
                detail=error_text,
                issue_code=str(failure_info.get("failure_class") or "openai_unusable"),
                failure_class=str(failure_info.get("failure_class") or "").strip() or None,
                backpressure=bool(failure_info.get("backpressure")),
            )
        elif source in {"env_oauth_token", "env_codex_oauth_token", "codex_token_vault"}:
            _register_provider_path(
                openai_entry,
                state="setup_required",
                source=source,
                detail="A Codex/OpenAI token is present, but a direct OpenAI API credential is still required for the direct OpenAI provider.",
                issue_code="direct_openai_credential_required",
            )
    elif openai_env_source in {"env_api_key", "env_access_token"}:
        _register_provider_path(
            openai_entry,
            state="active",
            source=openai_env_source,
            detail="A direct OpenAI credential is available in the runtime environment.",
        )
    elif _default_vault_credential_present("openai", requested_workspace_id, tenant_id):
        _register_provider_path(
            openai_entry,
            state="configured",
            source="vault",
            detail="A saved direct OpenAI credential exists for this workspace.",
        )
    elif openai_env_source in {"env_oauth_token", "env_codex_oauth_token", "codex_token_vault"}:
        _register_provider_path(
            openai_entry,
            state="setup_required",
            source=openai_env_source,
            detail="A Codex/OpenAI token is present, but a direct OpenAI API credential is still required for the direct OpenAI provider.",
            issue_code="direct_openai_credential_required",
        )
    elif openai_env_present:
        _register_provider_path(
            openai_entry,
            state="configured",
            source=openai_env_source or "env",
            detail="An OpenAI credential is present in the runtime environment.",
        )

    codex_entry = _entry("openai-codex")
    if openai_env_source == "codex_token_vault":
        codex_gateway_live = workspace_live_gateway_available(requested_workspace_id)
        if codex_gateway_live:
            _register_provider_path(
                codex_entry,
                state="active",
                source=openai_env_source,
                detail="A ChatGPT / Codex subscription session is available through this Agent Computer.",
            )
        else:
            _register_provider_path(
                codex_entry,
                state="configured",
                source=openai_env_source,
                detail="A ChatGPT / Codex session exists on this machine, but the paired Agent Computer gateway is offline.",
                issue_code="local_gateway_required",
            )
    elif openai_env_source in OPENAI_CODEX_OAUTH_SOURCES:
        _register_provider_path(
            codex_entry,
            state="active",
            source=openai_env_source,
            detail="An OpenAI / Codex OAuth token is available in the runtime environment.",
        )
    elif _default_vault_credential_present("openai-codex", requested_workspace_id, tenant_id):
        _register_provider_path(
            codex_entry,
            state="active",
            source="vault-default-codex",
            detail="A saved OpenAI / Codex OAuth credential exists for this workspace.",
        )

    anthropic_entry = _entry("anthropic")
    anthropic_env_key, _ = _resolve_hosted_provider_api_key(
        provider_id="anthropic",
        tenant_id=tenant_id,
        workspace_id=requested_workspace_id,
        run_id=None,
        purpose="provider_catalog_truth",
    )
    if anthropic_env_key:
        _register_provider_path(
            anthropic_entry,
            state="active",
            source="env-anthropic",
            detail="An Anthropic API key is available in the runtime environment.",
        )
    elif _default_vault_credential_present("anthropic", requested_workspace_id, tenant_id):
        _register_provider_path(
            anthropic_entry,
            state="configured",
            source="vault-default-anthropic",
            detail="A saved Anthropic credential exists for this workspace.",
        )
    claude_status = claude_code_cli_status(timeout=PROVIDER_STATUS_PROBE_TIMEOUT_SECONDS)
    if claude_status.get("available") and claude_status.get("logged_in"):
        _register_provider_path(
            anthropic_entry,
            state="active",
            source="local-claude-cli",
            detail="The Claude subscription signed into the local Claude CLI is available on this machine.",
        )
    elif claude_status.get("available"):
        _register_provider_path(
            anthropic_entry,
            state="setup_required",
            source="local-claude-cli",
            detail="Claude CLI is installed, but the Claude subscription is not signed in on this machine yet.",
            issue_code="local_cli_not_signed_in",
        )

    gemini_entry = _entry("gemini")
    gemini_env_key, _ = _resolve_hosted_provider_api_key(
        provider_id="gemini",
        tenant_id=tenant_id,
        workspace_id=requested_workspace_id,
        run_id=None,
        purpose="provider_catalog_truth",
    )
    if gemini_env_key:
        _register_provider_path(
            gemini_entry,
            state="active",
            source="env-gemini",
            detail="A Gemini API key is available in the runtime environment.",
        )
    elif _default_vault_credential_present("gemini", requested_workspace_id, tenant_id):
        _register_provider_path(
            gemini_entry,
            state="configured",
            source="vault-default-gemini",
            detail="A saved Gemini credential exists for this workspace.",
        )
    if gemini_cli_available():
        _register_provider_path(
            gemini_entry,
            state="setup_required",
            source="gemini-cli",
            detail="Gemini CLI is installed on this machine, but Gemini CLI OAuth still needs an access token and project configuration before it can be used.",
            issue_code="gemini_cli_oauth_incomplete",
        )

    for provider_id in ("vertex", "qwen", "deepseek", "mistral", "ollama_cloud"):
        entry = _entry(provider_id)
        if provider_id == "vertex":
            env_var = ""
        else:
            env_var, _ = _resolve_hosted_provider_api_key(
                provider_id=provider_id,
                tenant_id=tenant_id,
                workspace_id=requested_workspace_id,
                run_id=None,
                purpose="provider_catalog_truth",
            )
        if env_var:
            _register_provider_path(
                entry,
                state="active",
                source=f"env-{provider_id}",
                detail=f"A {entry['label']} credential is available in the runtime environment.",
            )
            continue
        if _default_vault_credential_present(provider_id, requested_workspace_id, tenant_id):
            _register_provider_path(
                entry,
                state="configured",
                source=f"vault-default-{provider_id}",
                detail=f"A saved {entry['label']} credential exists for this workspace.",
            )

    ollama_entry = _entry("ollama")
    ollama_status = ollama_local_status(timeout=LOCAL_SERVICE_STATUS_TIMEOUT_SECONDS)
    ollama_gateway_live = workspace_live_gateway_available(requested_workspace_id)
    if ollama_status.get("reachable") and ollama_status.get("has_models") and ollama_gateway_live:
        _register_provider_path(
            ollama_entry,
            state="active",
            source="local-ollama",
            detail="Ollama is running locally on this machine, has at least one model available, and the paired gateway is online.",
        )
    elif ollama_status.get("reachable") and ollama_status.get("has_models"):
        _register_provider_path(
            ollama_entry,
            state="configured",
            source="local-ollama",
            detail="Ollama is running locally and has a model installed, but the paired gateway is offline.",
            issue_code="local_gateway_required",
        )
    elif ollama_status.get("reachable"):
        _register_provider_path(
            ollama_entry,
            state="configured",
            source="local-ollama",
            detail=(
                "Ollama is running locally on this machine, but no local model is installed yet."
                if ollama_gateway_live
                else "Ollama is running locally, but the paired gateway is offline and no local model is installed yet."
            ),
            issue_code="local_model_required" if ollama_gateway_live else "local_gateway_required",
        )
    else:
        _register_provider_path(
            ollama_entry,
            state="configured",
            source="local-ollama",
            detail=(
                "Ollama is a local-only provider on this machine and needs the local Ollama service running before it becomes usable."
                if ollama_gateway_live
                else "Ollama is a local-only provider on this machine and requires the paired gateway to be online before it becomes usable."
            ),
            issue_code="local_service_required" if ollama_gateway_live else "local_gateway_required",
        )

    providers: List[Dict[str, Any]] = []
    state_summary = {"active": 0, "configured": 0, "setup_required": 0, "unavailable": 0, "degraded": 0}
    for provider_id, entry in provider_entries.items():
        if bool(provider_catalog_entry(provider_id).get("hidden")):
            continue
        finalized = _finalize_provider_entry(entry)
        state_summary[finalized["state"]] += 1
        providers.append(finalized)
    providers.sort(key=lambda item: (_provider_state_rank(str(item.get("state") or "")), str(item.get("label") or "")))
    providers_by_id = {str(item.get("id") or ""): item for item in providers}

    summary = {
        **profile_summary,
        **state_summary,
        "provider_total": len(providers),
    }
    return {
        "workspace_id": requested_workspace_id,
        "summary": summary,
        "items": items,
        "providers": providers,
        "providers_by_id": providers_by_id,
        "openai_profile_ready": str(providers_by_id.get("openai", {}).get("state") or "") == "active",
        "codex_profile_ready": str(providers_by_id.get("openai-codex", {}).get("state") or "") == "active",
    }


def _workspace_profile_sort_key(profile: Dict[str, Any]) -> tuple[int, str, str]:
    return (
        int(profile.get("priority", 100)),
        str(profile.get("created_at") or ""),
        str(profile.get("id") or ""),
    )


def build_workspace_provider_connection_truth(
    workspace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Return user-facing provider truth based only on saved workspace connections."""

    _init()
    requested_workspace_id = str(workspace_id or "default").strip() or "default"
    now = _utc_now()
    with _server.PROFILES_LOCK:
        profiles = [dict(item) for item in _server.PROVIDER_PROFILES.values() if isinstance(item, dict)]

    providers: List[Dict[str, Any]] = []
    providers_by_id: Dict[str, Dict[str, Any]] = {}
    state_summary = {"active": 0, "configured": 0, "setup_required": 0, "unavailable": 0, "degraded": 0}

    for provider_id, catalog_entry in PROVIDER_CATALOG.items():
        if bool(catalog_entry.get("hidden")):
            continue
        auth_modes = _workspace_user_facing_auth_modes(provider_id, catalog_entry)
        auth_mode_ids = [str(item.get("id") or "").strip() for item in auth_modes if str(item.get("id") or "").strip()]
        default_auth_mode = str(catalog_entry.get("default_auth_mode") or "").strip()
        if default_auth_mode and default_auth_mode not in auth_mode_ids and auth_mode_ids:
            default_auth_mode = auth_mode_ids[0]

        workspace_profiles = [
            profile
            for profile in profiles
            if normalize_provider_id(profile.get("provider")) == provider_id
            and (str(profile.get("workspace_id") or "default").strip() or "default") == requested_workspace_id
            and str(profile.get("credential_id") or "").strip()
        ]
        workspace_profiles.sort(key=_workspace_profile_sort_key)

        enabled_profiles = [profile for profile in workspace_profiles if bool(profile.get("enabled", True))]
        healthy_profiles = [
            profile
            for profile in enabled_profiles
            if _profile_health_value(profile, now) == "healthy" and not _profile_has_unresolved_failure(profile)
        ]
        degraded_profiles = [
            profile
            for profile in enabled_profiles
            if profile not in healthy_profiles
        ]
        selected_profile = healthy_profiles[0] if healthy_profiles else (enabled_profiles[0] if enabled_profiles else (workspace_profiles[0] if workspace_profiles else None))
        selected_profile_metadata = (
            dict(selected_profile.get("metadata") or {})
            if isinstance(selected_profile, dict) and isinstance(selected_profile.get("metadata"), dict)
            else {}
        )

        provider_scopes = [
            str(item).strip().lower()
            for item in list(catalog_entry.get("provider_scopes") or [])
            if str(item).strip()
        ]
        local_only_provider = "local_only" in provider_scopes

        if healthy_profiles:
            state = "active"
            state_detail = "A provider key is connected for this workspace."
            issue_code = None
            issue = None
        elif degraded_profiles:
            state = "degraded"
            state_detail = str(degraded_profiles[0].get("last_error") or "").strip() or "The saved provider key is not currently healthy."
            failure = classify_profile_failure(state_detail)
            issue_code = str(failure.get("failure_class") or "provider_degraded")
            issue = state_detail
        elif workspace_profiles:
            state = "configured"
            state_detail = "A provider key is saved for this workspace but is currently disabled."
            issue_code = "profile_disabled"
            issue = state_detail
        elif local_only_provider:
            state = "configured"
            state_detail = "This provider runs on the local machine and does not require a workspace credential."
            issue_code = None
            issue = None
        else:
            state = "setup_required"
            state_detail = None
            issue_code = "setup_required"
            issue = "Provider setup is still required."

        default_model = ""
        if selected_profile is not None:
            default_model = normalize_provider_model_id(
                provider_id,
                selected_profile.get("model"),
                fallback_to_default=True,
            ) or ""
        if not default_model:
            default_model = str(catalog_entry.get("default_model") or "").strip()

        entry: Dict[str, Any] = {
            "id": provider_id,
            "kind": "provider",
            "label": catalog_entry.get("label", provider_id),
            "identity_owner": "local_machine" if local_only_provider else "workspace",
            "identity_owner_label": "Local machine" if local_only_provider else "Workspace connection",
            "identity_boundary_note": (
                "This provider is bound to the local machine runtime and does not require a workspace credential."
                if local_only_provider
                else "This provider becomes available only after a workspace owner connects an API key."
            ),
            "auth": auth_mode_ids,
            "auth_modes": auth_modes,
            "default_auth_mode": default_auth_mode or None,
            "default_model": default_model or None,
            "provider_scopes": list(catalog_entry.get("provider_scopes") or []),
            "note": catalog_entry.get("note"),
            "profile_count": len(workspace_profiles),
            "enabled_profile_count": len(enabled_profiles),
            "disabled_profile_count": max(0, len(workspace_profiles) - len(enabled_profiles)),
            "healthy_profile_count": len(healthy_profiles),
            "cooldown_profile_count": len([profile for profile in workspace_profiles if _profile_health_value(profile, now) == "cooldown"]),
            "credential_sources": (
                ["workspace_profile"]
                if workspace_profiles
                else ["local_runtime"]
                if local_only_provider
                else []
            ),
            "issues": ([{"code": str(issue_code), "message": str(issue)}] if issue_code and issue else []),
            "backpressure": state == "degraded" and bool(classify_profile_failure(state_detail or "").get("backpressure")),
            "retry_after_seconds": None,
            "failure_class": str(classify_profile_failure(state_detail or "").get("failure_class") or "").strip() or None,
            "state": state,
            "usable": state == "active",
            "configured": state in {"active", "configured", "degraded"},
            "active": state == "active",
            "issue_code": issue_code,
            "issue": issue,
            "state_detail": state_detail,
            "active_source": (
                "workspace_profile"
                if workspace_profiles
                else "local_runtime"
                if local_only_provider
                else None
            ),
            "connection_kind": "machine_local_capability" if local_only_provider else "workspace_provider_connection",
            "connection_scope": "machine" if local_only_provider else "workspace",
            "connection_label": "This machine only" if local_only_provider else "Workspace provider",
            "machine_bound": local_only_provider,
            "profile_metadata": selected_profile_metadata,
        }
        state_summary[state] += 1
        providers.append(entry)
        providers_by_id[provider_id] = entry

    providers.sort(key=lambda item: (_provider_state_rank(str(item.get("state") or "")), str(item.get("label") or "")))
    summary = {
        **state_summary,
        "provider_total": len(providers),
    }
    return {
        "workspace_id": requested_workspace_id,
        "summary": summary,
        "providers": providers,
        "providers_by_id": providers_by_id,
    }
