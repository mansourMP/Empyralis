from __future__ import annotations

import asyncio
import os
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple, Union
from urllib.parse import quote_plus

from server_modules.provider_profiles import (
    PROVIDER_CATALOG,
    _build_provider_credential_candidates,
    _openai_bearer_from_credentials,
    _openai_credential_type,
    http_json_request,
    normalize_provider_id,
    resolve_provider_adapter,
)
from server_modules import usage_accounting_service
from server_modules.shared import PROFILES_LOCK, PROVIDER_PROFILES


DEFAULT_MODEL = "gpt-4o"
OPENAI_CHAT_COMPLETIONS_URL = os.getenv("OPENAI_CHAT_COMPLETIONS_URL", "https://api.openai.com/v1/chat/completions")
ANTHROPIC_MESSAGES_URL = os.getenv("ANTHROPIC_MESSAGES_URL", "https://api.anthropic.com/v1/messages")
OPENAI_EMBEDDINGS_URL = os.getenv("OPENAI_EMBEDDINGS_URL", "https://api.openai.com/v1/embeddings")
MODEL_ALIASES = {
    "gpt-4o": "gpt-4o",
    "gpt-4o-mini": "gpt-4o-mini",
    "gpt-4.1": "gpt-4.1",
    "gpt-4.1-mini": "gpt-4.1-mini",
    "claude-sonnet": "anthropic/claude-3-7-sonnet-20250219",
    "claude-haiku": "anthropic/claude-3-haiku-20240307",
    "gemini-flash": "gemini/gemini-2.5-flash",
    "gemini-pro": "gemini/gemini-2.5-pro",
    "vertex-gemini-flash": "vertex_ai/gemini-2.5-flash",
    "vertex-gemini-pro": "vertex_ai/gemini-1.5-pro",
}
ALLOWED_MESSAGE_ROLES = {"system", "user", "assistant", "tool"}
OPENAI_COMPATIBLE_COMPLETION_PROVIDERS = {
    "azure_openai",
    "custom_openai_compatible",
    "deepseek",
    "groq",
    "mistral",
    "ollama",
    "openrouter",
    "qwen",
    "xai",
}


def _profile_record(profile_id: Optional[str]) -> Optional[Dict[str, Any]]:
    token = str(profile_id or "").strip()
    if not token:
        return None
    with PROFILES_LOCK:
        raw = PROVIDER_PROFILES.get(token)
    return dict(raw) if isinstance(raw, dict) else None


def infer_provider(model_name: Optional[str], provider: Optional[str] = None, profile_id: Optional[str] = None) -> str:
    if provider:
        return normalize_provider_id(provider)
    profile = _profile_record(profile_id)
    if profile:
        return normalize_provider_id(profile.get("provider"))
    raw = str(model_name or "").strip().lower()
    if raw.startswith("anthropic/") or raw.startswith("claude"):
        return "anthropic"
    if raw.startswith("gemini/") or raw.startswith("gemini"):
        return "gemini"
    if raw.startswith("vertex_ai/") or raw.startswith("vertex"):
        return "vertex"
    if raw.startswith("qwen/") or raw.startswith("qwen"):
        return "qwen"
    if raw.startswith("groq/"):
        return "groq"
    if raw.startswith("openrouter/"):
        return "openrouter"
    if raw.startswith("xai/") or raw.startswith("grok"):
        return "xai"
    if raw.startswith("deepseek/") or raw.startswith("deepseek"):
        return "deepseek"
    if raw.startswith("mistral/") or raw.startswith("mistral"):
        return "mistral"
    if raw.startswith("ollama/") or raw.startswith("llama") or raw.startswith("gemma") or raw.startswith("phi3"):
        return "ollama"
    return "openai"


def resolve_model(model_name: Optional[str], provider: Optional[str] = None, profile_id: Optional[str] = None) -> str:
    resolved_provider = infer_provider(model_name, provider=provider, profile_id=profile_id)
    raw = str(model_name or "").strip()
    if not raw:
        raw = str(PROVIDER_CATALOG.get(resolved_provider, {}).get("default_model") or DEFAULT_MODEL)
    raw = MODEL_ALIASES.get(raw, raw)
    if raw.startswith("openai/"):
        return raw.split("/", 1)[1]
    if "/" in raw:
        return raw
    if resolved_provider == "anthropic":
        return f"anthropic/{raw}"
    if resolved_provider == "gemini":
        return f"gemini/{raw}"
    if resolved_provider == "vertex":
        return f"vertex_ai/{raw}"
    return raw


def list_model_aliases() -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    default_model_resolved = resolve_model(DEFAULT_MODEL)
    for alias, target in sorted(MODEL_ALIASES.items(), key=lambda item: item[0]):
        provider = infer_provider(target)
        provider_default_raw = str(PROVIDER_CATALOG.get(provider, {}).get("default_model") or "").strip() or None
        provider_default_resolved = resolve_model(provider_default_raw, provider=provider) if provider_default_raw else None
        resolved_target = resolve_model(alias, provider=provider)
        items.append(
            {
                "alias": alias,
                "provider": provider,
                "model": target,
                "resolved_model": resolved_target,
                "is_global_default": resolved_target == default_model_resolved,
                "is_provider_default": bool(provider_default_resolved and resolved_target == provider_default_resolved),
            }
        )
    return items


def normalize_messages(messages: List[Any]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "user").strip().lower()
        if role not in ALLOWED_MESSAGE_ROLES:
            role = "user"
        content = message.get("content", "")
        if content is None:
            content = ""
        elif not isinstance(content, (str, list)):
            content = str(content)
        normalized.append({"role": role, "content": content})
    return normalized


def resolve_call_credentials(
    *,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    workspace_id: Optional[str] = None,
    profile_id: Optional[str] = None,
    credential_id: Optional[str] = None,
) -> Dict[str, Any]:
    resolved_provider = infer_provider(model, provider=provider, profile_id=profile_id)
    profile = _profile_record(profile_id)
    candidates = _build_provider_credential_candidates(
        {
            "workspace_id": str(workspace_id or "default").strip() or "default",
            "credential_id": credential_id,
            "model": str(model or "").strip() or None,
        },
        {
            "profile_id": str(profile_id or "").strip() or None,
            "model": str(model or "").strip() or None,
        },
        resolved_provider,
    )
    candidate = candidates[0] if candidates else None
    preferred_model = (
        str(model or "").strip()
        or str((candidate or {}).get("model") or "").strip()
        or str((profile or {}).get("model") or "").strip()
        or str(PROVIDER_CATALOG.get(resolved_provider, {}).get("default_model") or DEFAULT_MODEL)
    )
    return {
        "provider": resolved_provider,
        "model": resolve_model(preferred_model, provider=resolved_provider, profile_id=profile_id),
        "credentials": (candidate or {}).get("credentials"),
        "candidate": candidate,
    }


def _response_field(payload: Any, key: str, default: Any = None) -> Any:
    if isinstance(payload, dict):
        return payload.get(key, default)
    return getattr(payload, key, default)


def _normalize_text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
        return "".join(parts)
    return ""


def _extract_text(response: Any) -> str:
    choices = _response_field(response, "choices", []) or []
    if isinstance(choices, list) and choices:
        first = choices[0]
        message = _response_field(first, "message", {}) or {}
        content = _response_field(message, "content")
        text = _normalize_text_content(content)
        if text:
            return text
        delta = _response_field(first, "delta", {}) or {}
        delta_text = _normalize_text_content(_response_field(delta, "content"))
        if delta_text:
            return delta_text
        direct_text = _response_field(first, "text", None)
        if isinstance(direct_text, str) and direct_text:
            return direct_text
    return ""


def _normalize_usage(response: Any) -> Dict[str, int]:
    usage = _response_field(response, "usage", {}) or {}
    prompt_tokens = int(_response_field(usage, "prompt_tokens", 0) or 0)
    completion_tokens = int(_response_field(usage, "completion_tokens", 0) or 0)
    total_tokens = int(_response_field(usage, "total_tokens", prompt_tokens + completion_tokens) or 0)
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def _message_text(message: Dict[str, Any]) -> str:
    content = message.get("content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        return "\n".join(parts).strip()
    return str(content).strip() if content is not None else ""


def _legacy_adapter_payload(messages: List[Any]) -> Tuple[str, str]:
    normalized = normalize_messages(messages)
    system_parts: List[str] = []
    user_parts: List[str] = []
    for message in normalized:
        text = _message_text(message)
        if not text:
            continue
        if message.get("role") == "system":
            system_parts.append(text)
        else:
            user_parts.append(text)
    return "\n\n".join(system_parts).strip(), "\n\n".join(user_parts).strip()


def _use_adapter_compat_fallback(provider: str, credentials: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(credentials, dict):
        return False
    if provider == "vertex":
        return bool(str(credentials.get("access_token") or "").strip())
    return provider == "ollama_cloud"


def _legacy_adapter_call(
    *,
    provider: str,
    messages: List[Any],
    model: str,
    credentials: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if not isinstance(credentials, dict):
        raise RuntimeError(f"No credential available for provider '{provider}'.")
    _, _, adapter = resolve_provider_adapter(provider, credentials=credentials)
    system_prompt, user_input = _legacy_adapter_payload(messages)
    adapter_model = model.split("/", 1)[1] if provider == "vertex" and "/" in model else model
    content = adapter.generate(system_prompt, user_input, adapter_model, credentials)
    usage = usage_accounting_service.usage_projection_from_record(
        usage_accounting_service.build_usage_accounting_record(
            run_id=None,
            requested_provider=provider,
            effective_provider=provider,
            requested_model=model,
            effective_model=model,
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            estimation_mode="provider_usage_missing",
            metadata={"legacy_adapter_fallback": True},
        )
    )
    return {
        "content": str(content or "").strip(),
        "model": model,
        "provider": provider,
        "usage": usage,
    }


def _provider_kwargs(provider: str, credentials: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not credentials:
        return {}
    if provider == "openai":
        if _openai_credential_type(credentials) == "codex_token":
            raise RuntimeError("This is a Codex OAuth token. Use openai-codex provider or set a direct OpenAI API key.")
        token = _openai_bearer_from_credentials(credentials)
        if not token:
            raise RuntimeError("OpenAI credential requires api_key or access_token.")
        extra_headers: Dict[str, str] = {}
        org_id = str(credentials.get("org_id") or "").strip()
        project_id = str(credentials.get("project_id") or "").strip()
        if org_id:
            extra_headers["OpenAI-Organization"] = org_id
        if project_id:
            extra_headers["OpenAI-Project"] = project_id
        kwargs: Dict[str, Any] = {"api_key": token}
        if extra_headers:
            kwargs["extra_headers"] = extra_headers
        return kwargs
    if provider == "anthropic":
        key = str(credentials.get("api_key") or "").strip()
        if not key:
            raise RuntimeError("Anthropic credential requires api_key.")
        return {"api_key": key}
    if provider == "gemini":
        auth_mode = str(credentials.get("auth_mode") or "").strip().lower()
        access_token = str(credentials.get("access_token") or "").strip()
        if access_token.lower().startswith("bearer "):
            access_token = access_token[7:].strip()
        project_id = str(credentials.get("project_id") or "").strip()
        if auth_mode == "gemini_cli_oauth" or access_token:
            if not access_token:
                raise RuntimeError("Gemini credential requires access_token for gemini_cli_oauth.")
            if not project_id:
                raise RuntimeError("Gemini credential requires project_id for gemini_cli_oauth.")
            return {"access_token": access_token, "project_id": project_id}
        key = str(credentials.get("api_key") or "").strip()
        if not key:
            raise RuntimeError("Gemini credential requires api_key.")
        return {"api_key": key}
    if provider == "vertex":
        raise RuntimeError("Vertex model routing requires a direct Vertex credential payload.")
    raise RuntimeError(f"Unsupported provider '{provider}'.")


def _error_detail(payload: Dict[str, Any]) -> str:
    body = payload.get("json")
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
          message = error.get("message")
          if isinstance(message, str) and message.strip():
              return message.strip()
        detail = body.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
    text = str(payload.get("text") or "").strip()
    return text[:500] if text else ""


def _raise_provider_error(provider: str, model: str, response: Dict[str, Any]) -> None:
    status = int(response.get("status") or 500)
    detail = _error_detail(response)
    if status in {401, 403}:
        raise ValueError(f"Invalid or missing credential for provider '{provider}'. {detail}".strip())
    if status == 429:
        raise ValueError(f"Rate limit reached for '{model}'. Try again shortly or switch models.")
    if status == 400:
        raise ValueError(f"Bad request to '{model}'. {detail}".strip())
    raise RuntimeError(f"Model call failed [{model}] ({status}). {detail}".strip())


def _normalize_usage_from_dict(
    usage: Optional[Dict[str, Any]],
    *,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    source_surface: str = "direct_model_router",
) -> Dict[str, Any]:
    if provider and model:
        record = usage_accounting_service.normalize_provider_usage(
            provider=provider,
            model=model,
            usage=usage,
            source_surface=source_surface,
        )
        return usage_accounting_service.usage_projection_from_record(record)
    usage = usage or {}
    prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or usage.get("promptTokenCount") or usage.get("inputTokenCount") or 0)
    completion_tokens = int(
        usage.get("completion_tokens")
        or usage.get("output_tokens")
        or usage.get("candidatesTokenCount")
        or usage.get("completionTokenCount")
        or 0
    )
    total_tokens = int(
        usage.get("total_tokens")
        or usage.get("totalTokenCount")
        or prompt_tokens + completion_tokens
        or 0
    )
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def _anthropic_text_from_body(body: Dict[str, Any]) -> str:
    parts: List[str] = []
    for item in body.get("content", []) if isinstance(body.get("content"), list) else []:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "").strip().lower() != "text":
            continue
        text = item.get("text")
        if isinstance(text, str) and text:
            parts.append(text)
    return "".join(parts).strip()


def _gemini_text_from_body(body: Dict[str, Any]) -> str:
    candidates = body.get("candidates")
    parts: List[str] = []
    if isinstance(candidates, list):
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            content = candidate.get("content")
            if not isinstance(content, dict):
                continue
            for part in content.get("parts", []) if isinstance(content.get("parts"), list) else []:
                if not isinstance(part, dict):
                    continue
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
    return "\n".join(parts).strip()


def _provider_messages_for_openai(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return normalize_messages(messages)


def _adapter_model_id(provider: str, model: str) -> str:
    token = str(model or "").strip()
    prefix = f"{provider}/"
    if token.lower().startswith(prefix):
        return token.split("/", 1)[1]
    return token


def _provider_messages_for_anthropic(messages: List[Dict[str, Any]]) -> Tuple[str, List[Dict[str, Any]]]:
    normalized = normalize_messages(messages)
    system_parts: List[str] = []
    provider_messages: List[Dict[str, Any]] = []
    for message in normalized:
        role = str(message.get("role") or "user").strip().lower()
        content = message.get("content", "")
        if role == "system":
            text = _normalize_text_content(content)
            if text:
                system_parts.append(text)
            continue
        anthropic_role = "assistant" if role == "assistant" else "user"
        provider_messages.append({"role": anthropic_role, "content": content})
    if not provider_messages:
        provider_messages.append({"role": "user", "content": ""})
    return "\n\n".join(system_parts).strip(), provider_messages


def _provider_messages_for_gemini(messages: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    normalized = normalize_messages(messages)
    system_parts: List[str] = []
    contents: List[Dict[str, Any]] = []
    for message in normalized:
        role = str(message.get("role") or "user").strip().lower()
        text = _normalize_text_content(message.get("content"))
        if role == "system":
            if text:
                system_parts.append(text)
            continue
        parts = []
        if text:
            parts.append({"text": text})
        contents.append({"role": "model" if role == "assistant" else "user", "parts": parts or [{"text": ""}]})
    system_instruction = None
    if system_parts:
        system_instruction = {"parts": [{"text": "\n\n".join(system_parts).strip()}]}
    if not contents:
        contents.append({"role": "user", "parts": [{"text": ""}]})
    return system_instruction, contents


def _sync_provider_completion(
    *,
    resolved_provider: str,
    resolved_model: str,
    messages: List[Any],
    credentials: Optional[Dict[str, Any]],
    max_tokens: int,
    temperature: float,
    source_surface: str = "direct_model_router",
) -> Dict[str, Any]:
    normalized = normalize_messages(messages)
    if resolved_provider == "openai":
        kwargs = _provider_kwargs(resolved_provider, credentials)
        headers = {
            "Authorization": f"Bearer {kwargs['api_key']}",
            "Content-Type": "application/json",
        }
        for key, value in (kwargs.get("extra_headers") or {}).items():
            headers[str(key)] = str(value)
        payload = {
            "model": resolved_model,
            "messages": _provider_messages_for_openai(normalized),
            "max_tokens": int(max_tokens),
            "temperature": float(temperature),
        }
        response = http_json_request(OPENAI_CHAT_COMPLETIONS_URL, headers=headers, payload=payload, timeout=60)
        if int(response.get("status") or 500) >= 400:
            _raise_provider_error(resolved_provider, resolved_model, response)
        body = response.get("json")
        if not isinstance(body, dict):
            raise RuntimeError(f"Model call failed [{resolved_model}]: OpenAI returned invalid JSON.")
        return {
            "content": _extract_text(body),
            "model": resolved_model,
            "provider": resolved_provider,
            "usage": _normalize_usage_from_dict(
                body.get("usage") if isinstance(body.get("usage"), dict) else {},
                provider=resolved_provider,
                model=resolved_model,
                source_surface=source_surface,
            ),
        }

    if resolved_provider == "anthropic":
        kwargs = _provider_kwargs(resolved_provider, credentials)
        system_prompt, provider_messages = _provider_messages_for_anthropic(normalized)
        payload = {
            "model": resolved_model.split("/", 1)[1] if "/" in resolved_model else resolved_model,
            "messages": provider_messages,
            "max_tokens": int(max_tokens),
            "temperature": float(temperature),
        }
        if system_prompt:
            payload["system"] = system_prompt
        response = http_json_request(
            ANTHROPIC_MESSAGES_URL,
            headers={
                "x-api-key": kwargs["api_key"],
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            payload=payload,
            timeout=60,
        )
        if int(response.get("status") or 500) >= 400:
            _raise_provider_error(resolved_provider, resolved_model, response)
        body = response.get("json")
        if not isinstance(body, dict):
            raise RuntimeError(f"Model call failed [{resolved_model}]: Anthropic returned invalid JSON.")
        return {
            "content": _anthropic_text_from_body(body),
            "model": resolved_model,
            "provider": resolved_provider,
            "usage": _normalize_usage_from_dict(
                body.get("usage") if isinstance(body.get("usage"), dict) else {},
                provider=resolved_provider,
                model=resolved_model,
                source_surface=source_surface,
            ),
        }

    if resolved_provider == "gemini":
        kwargs = _provider_kwargs(resolved_provider, credentials)
        model_id = resolved_model.split("/", 1)[1] if "/" in resolved_model else resolved_model
        system_instruction, contents = _provider_messages_for_gemini(normalized)
        payload: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": float(temperature),
                "maxOutputTokens": int(max_tokens),
            },
        }
        if system_instruction:
            payload["systemInstruction"] = system_instruction
        if "api_key" in kwargs:
            response = http_json_request(
                f"https://generativelanguage.googleapis.com/v1beta/models/{quote_plus(model_id)}:generateContent?key={quote_plus(kwargs['api_key'])}",
                headers={"Content-Type": "application/json"},
                payload=payload,
                timeout=60,
            )
        else:
            response = http_json_request(
                f"https://generativelanguage.googleapis.com/v1beta/models/{quote_plus(model_id)}:generateContent",
                headers={
                    "Authorization": f"Bearer {kwargs['access_token']}",
                    "x-goog-user-project": str(kwargs["project_id"]),
                    "Content-Type": "application/json",
                },
                payload=payload,
                timeout=60,
            )
        if int(response.get("status") or 500) >= 400:
            _raise_provider_error(resolved_provider, resolved_model, response)
        body = response.get("json")
        if not isinstance(body, dict):
            raise RuntimeError(f"Model call failed [{resolved_model}]: Gemini returned invalid JSON.")
        return {
            "content": _gemini_text_from_body(body),
            "model": resolved_model,
            "provider": resolved_provider,
            "usage": _normalize_usage_from_dict(
                body.get("usageMetadata") if isinstance(body.get("usageMetadata"), dict) else {},
                provider=resolved_provider,
                model=resolved_model,
                source_surface=source_surface,
            ),
        }

    if resolved_provider in OPENAI_COMPATIBLE_COMPLETION_PROVIDERS:
        if not isinstance(credentials, dict):
            raise RuntimeError(f"No credential available for provider '{resolved_provider}'.")
        _, _, adapter = resolve_provider_adapter(resolved_provider, credentials=credentials)
        adapter_model = _adapter_model_id(resolved_provider, resolved_model)
        payload = {
            "model": adapter_model,
            "messages": _provider_messages_for_openai(normalized),
            "max_tokens": int(max_tokens),
            "temperature": float(temperature),
        }
        response = http_json_request(
            adapter._chat_completions_url(credentials, model=adapter_model),
            method="POST",
            headers=adapter._headers(credentials, include_content_type=True),
            payload=payload,
            timeout=60,
        )
        if int(response.get("status") or 500) >= 400:
            _raise_provider_error(resolved_provider, resolved_model, response)
        body = response.get("json")
        if not isinstance(body, dict):
            raise RuntimeError(f"Model call failed [{resolved_model}]: {resolved_provider} returned invalid JSON.")
        return {
            "content": _extract_text(body),
            "model": resolved_model,
            "provider": resolved_provider,
            "usage": _normalize_usage_from_dict(
                body.get("usage") if isinstance(body.get("usage"), dict) else {},
                provider=resolved_provider,
                model=resolved_model,
                source_surface=source_surface,
            ),
        }

    raise RuntimeError(f"Unsupported provider '{resolved_provider}'.")


def _base_completion_kwargs(
    *,
    messages: List[Any],
    model: Optional[str],
    provider: Optional[str],
    profile_id: Optional[str],
    credentials: Optional[Dict[str, Any]],
    max_tokens: int,
    temperature: float,
) -> Tuple[str, str, Dict[str, Any]]:
    resolved_provider = infer_provider(model, provider=provider, profile_id=profile_id)
    resolved_model = resolve_model(model, provider=resolved_provider, profile_id=profile_id)
    kwargs: Dict[str, Any] = {
        "model": resolved_model,
        "messages": normalize_messages(messages),
        "max_tokens": int(max_tokens),
        "temperature": float(temperature),
    }
    kwargs.update(_provider_kwargs(resolved_provider, credentials))
    return resolved_provider, resolved_model, kwargs


async def _stream_text(stream: Any) -> AsyncGenerator[str, None]:
    async for chunk in stream:
        text = _extract_text(chunk)
        if text:
            yield text


async def _yield_text_once(text: str) -> AsyncGenerator[str, None]:
    if text:
        yield text


async def call_model(
    messages: List[Any],
    *,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    profile_id: Optional[str] = None,
    credentials: Optional[Dict[str, Any]] = None,
    max_tokens: int = 2000,
    temperature: float = 0.7,
    stream: bool = False,
    source_surface: str = "direct_model_router",
) -> Union[Dict[str, Any], AsyncGenerator[str, None]]:
    resolved_provider = infer_provider(model, provider=provider, profile_id=profile_id)
    resolved_model = resolve_model(model, provider=resolved_provider, profile_id=profile_id)
    if _use_adapter_compat_fallback(resolved_provider, credentials):
        if stream:
            raise RuntimeError(f"Streaming is not supported for provider '{resolved_provider}' with the current credential format.")
        return await asyncio.to_thread(
            _legacy_adapter_call,
            provider=resolved_provider,
            messages=messages,
            model=resolved_model,
            credentials=credentials,
        )
    result = await asyncio.to_thread(
        call_model_sync,
        messages=messages,
        model=model,
        provider=provider,
        profile_id=profile_id,
        credentials=credentials,
        max_tokens=max_tokens,
        temperature=temperature,
        source_surface=source_surface,
    )
    if stream:
        return _yield_text_once(str(result.get("content") or ""))
    return result


def call_model_sync(
    messages: List[Any],
    *,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    profile_id: Optional[str] = None,
    credentials: Optional[Dict[str, Any]] = None,
    max_tokens: int = 2000,
    temperature: float = 0.7,
    source_surface: str = "direct_model_router",
) -> Dict[str, Any]:
    resolved_provider = infer_provider(model, provider=provider, profile_id=profile_id)
    resolved_model = resolve_model(model, provider=resolved_provider, profile_id=profile_id)
    if _use_adapter_compat_fallback(resolved_provider, credentials):
        return _legacy_adapter_call(
            provider=resolved_provider,
            messages=messages,
            model=resolved_model,
            credentials=credentials,
        )
    return _sync_provider_completion(
        resolved_provider=resolved_provider,
        resolved_model=resolved_model,
        messages=messages,
        credentials=credentials,
        max_tokens=max_tokens,
        temperature=temperature,
        source_surface=source_surface,
    )
