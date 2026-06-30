from __future__ import annotations

from typing import Any, Dict, Tuple

from server_modules import direct_chat_provider_service
from server_modules.direct_chat_runtime_exports import generate_chat_reply_with_provider_fallback


CLOUD_PROVIDER_IDS = ("anthropic", "deepseek", "openai", "gemini")

INVOKE_APP_SPECS: Dict[str, Dict[str, str]] = {
    "writing": {
        "name": "Writing",
        "description": "Draft, rewrite, and sharpen text.",
        "system_prompt": (
            "You are the Writing mini app inside Empyralis. "
            "Handle only the user's current request. Do not act like a personal agent, "
            "do not mention memory, and do not reference hidden system state. "
            "Return a direct, polished writing result."
        ),
    },
    "reading": {
        "name": "Reading",
        "description": "Summarize, extract, and explain reading material.",
        "system_prompt": (
            "You are the Reading mini app inside Empyralis. "
            "Work only from the current input. Do not use personal memory or act like Sage. "
            "Return a clear summary, extraction, or explanation."
        ),
    },
    "study": {
        "name": "Study",
        "description": "Explain, quiz, and break down topics.",
        "system_prompt": (
            "You are the Study mini app inside Empyralis. "
            "Focus on helping the user learn the current topic. "
            "Do not behave like a personal agent and do not reference memory. "
            "Prefer crisp explanations, examples, and study structure."
        ),
    },
    "notes": {
        "name": "Notes",
        "description": "Turn raw thoughts into clean notes.",
        "system_prompt": (
            "You are the Notes mini app inside Empyralis. "
            "Transform the current input into organized notes. "
            "Do not reference personal memory or hidden context."
        ),
    },
    "planner": {
        "name": "Planner",
        "description": "Turn inputs into clear plans and checklists.",
        "system_prompt": (
            "You are the Planner mini app inside Empyralis. "
            "Turn the current input into a concrete plan, checklist, or schedule. "
            "Do not behave like a personal assistant with long-term memory."
        ),
    },
    "travel": {
        "name": "Travel",
        "description": "Shape simple itineraries and travel options.",
        "system_prompt": (
            "You are the Travel mini app inside Empyralis. "
            "Help with the current trip or itinerary request only. "
            "Do not reference personal memory or act like Sage."
        ),
    },
}


def list_invoke_capable_mini_apps() -> Dict[str, Any]:
    items = []
    for app_id, spec in INVOKE_APP_SPECS.items():
        items.append(
            {
                "id": app_id,
                "name": spec["name"],
                "description": spec["description"],
                "mode": "invoke",
                "memory_scope": "none",
            }
        )
    return {"items": items, "count": len(items)}


def get_invoke_capable_mini_app(app_id: str) -> Dict[str, Any]:
    normalized_app_id = str(app_id or "").strip().lower()
    if not normalized_app_id:
        raise KeyError("Mini app id is required.")
    spec = INVOKE_APP_SPECS.get(normalized_app_id)
    if not spec:
        raise KeyError(f"Mini app '{normalized_app_id}' does not support invoke mode.")
    return {
        "id": normalized_app_id,
        "name": spec["name"],
        "description": spec["description"],
        "mode": "invoke",
        "memory_scope": "none",
    }


def _resolve_cloud_provider_for_workspace(
    workspace_id: str,
    requested_provider: str = "",
) -> Tuple[str, Dict[str, Any]]:
    normalized_workspace_id = str(workspace_id or "default").strip() or "default"
    normalized_requested_provider = str(requested_provider or "").strip().lower()
    if normalized_requested_provider and normalized_requested_provider not in CLOUD_PROVIDER_IDS:
        raise ValueError("Mini apps only support cloud providers.")

    candidates = [normalized_requested_provider] if normalized_requested_provider else list(CLOUD_PROVIDER_IDS)

    for provider in candidates:
        credentials = direct_chat_provider_service.direct_chat_credentials(normalized_workspace_id, provider)
        if provider == "openai":
            credential_type = str(credentials.get("credential_type") or "").strip().lower()
            auth_mode = direct_chat_provider_service.credential_auth_mode("openai", credentials)
            if credential_type == "codex_token" or auth_mode == "oauth_token":
                continue
        if direct_chat_provider_service.supports_direct_message_native_chat(provider, credentials):
            return provider, credentials

    raise RuntimeError("No cloud provider is configured for mini apps.")


def resolve_cloud_provider_for_workspace(
    workspace_id: str,
    requested_provider: str = "",
) -> Tuple[str, Dict[str, Any]]:
    return _resolve_cloud_provider_for_workspace(workspace_id, requested_provider)


def invoke_mini_app(
    workspace_id: str,
    app_id: str,
    user_input: str,
    *,
    requested_provider: str = "",
    requested_model: str = "",
) -> Dict[str, Any]:
    normalized_app_id = str(app_id or "").strip().lower()
    normalized_input = str(user_input or "").strip()
    if not normalized_input:
        raise ValueError("input is required")

    spec = INVOKE_APP_SPECS.get(normalized_app_id)
    if not spec:
        raise KeyError(f"Mini app '{normalized_app_id}' does not support invoke mode.")

    provider, credentials = _resolve_cloud_provider_for_workspace(workspace_id, requested_provider)
    context: Dict[str, Any] = {
        "workspace_id": str(workspace_id or "").strip(),
        "provider": provider,
        "source": "mini_app_invoke",
        "disable_provider_fallback": True,
    }
    metadata: Dict[str, Any] = {
        "workspace_id": str(workspace_id or "").strip(),
        "provider": provider,
        "source": "mini_app_invoke",
        "disable_provider_fallback": True,
        "credentials": credentials,
    }
    if requested_model:
        context["model"] = requested_model
        metadata["model"] = requested_model

    reply, usage_masked, attempted_providers, last_error = generate_chat_reply_with_provider_fallback(
        context,
        metadata,
        normalized_input,
        spec["system_prompt"],
        prior_messages=None,
    )
    if not reply:
        raise RuntimeError(last_error or "Mini app invocation failed.")

    attempted = [item.strip() for item in str(attempted_providers or "").split(",") if item.strip()]
    effective_provider = attempted[-1] if attempted else provider
    effective_model = str((usage_masked or {}).get("model") or requested_model or "").strip()
    return {
        "app_id": normalized_app_id,
        "mode": "invoke",
        "memory_scope": "none",
        "reply": reply,
        "provider": effective_provider,
        "model": effective_model or None,
        "attempted_providers": attempted,
        "usage": usage_masked or None,
    }
