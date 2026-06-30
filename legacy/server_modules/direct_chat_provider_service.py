from __future__ import annotations

from datetime import datetime, timezone
import os
from typing import Any, Callable, Dict, List, Optional

from server_modules.direct_chat_intervention_service import build_intervention

from scripts.orion_local_worker_llm import (
    SUPPORTED_PROVIDERS,
    get_claude_code_session_token,
    provider_has_key,
)
from server_modules import entitlements_service
from server_modules import provider_profiles
from server_modules.provider_profiles import _build_provider_credential_candidates, normalize_auth_mode
from server_modules import skills_service
from server_modules import runtime_config


CHANNEL_CAPABILITY_IDS = {
    "telegram_bot",
    "whatsapp",
    "slack",
    "discord_bot",
    "google_workspace",
    "microsoft_365",
    "smtp",
}

PLATFORM_RUNTIME_AUTH_PROVIDERS = {"deepseek"}


def _normalize_capability_entries(tool_capabilities: Any) -> List[Dict[str, Any]]:
    if not isinstance(tool_capabilities, list):
        return []
    normalized: List[Dict[str, Any]] = []
    for item in tool_capabilities:
        if not isinstance(item, dict):
            continue
        capability_id = str(item.get("id") or "").strip().lower()
        if not capability_id:
            continue
        normalized.append(
            {
                "id": capability_id,
                "label": str(item.get("label") or capability_id).strip() or capability_id,
                "connected": bool(item.get("connected")),
                "runtime_usable": item.get("runtime_usable")
                if isinstance(item.get("runtime_usable"), bool)
                else None,
                "read_actions": list(item.get("read_actions") or [])
                if isinstance(item.get("read_actions"), list)
                else [],
                "write_actions": list(item.get("write_actions") or [])
                if isinstance(item.get("write_actions"), list)
                else [],
                "approval_required_actions": list(item.get("approval_required_actions") or [])
                if isinstance(item.get("approval_required_actions"), list)
                else [],
            }
        )
    return normalized


def _append_setup_action(
    actions: List[Dict[str, str]],
    *,
    action_id: str,
    label: str,
    href: str,
    reason: str,
) -> None:
    for item in actions:
        if str(item.get("id") or "").strip() == action_id:
            return
    actions.append(
        {
            "id": action_id,
            "label": label,
            "href": href,
            "reason": reason,
        }
    )


def _byok_provider_options() -> List[Dict[str, Any]]:
    options: List[Dict[str, Any]] = []
    for provider_id in ("deepseek", "gemini", "openai", "ollama_cloud", "anthropic"):
        entry = provider_profiles.PROVIDER_CATALOG.get(provider_id)
        if not isinstance(entry, dict):
            continue
        options.append(
            {
                "provider_id": provider_id,
                "label": str(entry.get("label") or provider_id).strip() or provider_id,
                "default_model": str(entry.get("default_model") or "").strip() or None,
                "models": list(entry.get("models") or []),
            }
        )
    return options


_LOCAL_WORKER_FALLBACK_ENVS = {"dev", "development", "local", "test", "testing"}


def _local_worker_fallback_enabled() -> bool:
    token = str(os.getenv("ORION_ENV") or os.getenv("APP_ENV") or "").strip().lower()
    return token in _LOCAL_WORKER_FALLBACK_ENVS


def _workspace_local_worker_online_exact(workspace_id: str) -> bool:
    normalized_workspace_id = str(workspace_id or "default").strip() or "default"
    try:
        from server_modules import local_queue

        payload = local_queue.handle_get_local_workers_status()
    except Exception:
        return False
    items = payload.get("items") if isinstance(payload, dict) else []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not bool(item.get("online")):
            continue
        worker_workspace = str(item.get("workspace_id") or "default").strip() or "default"
        if worker_workspace != normalized_workspace_id:
            continue
        capabilities = {
            str(capability or "").strip().lower()
            for capability in (item.get("capabilities") if isinstance(item.get("capabilities"), list) else [])
            if str(capability or "").strip()
        }
        if "shell.execute" in capabilities or "local.worker" in capabilities:
            return True
    return False


def _workspace_local_worker_online(workspace_id: str) -> bool:
    normalized_workspace_id = str(workspace_id or "default").strip() or "default"
    if _workspace_local_worker_online_exact(normalized_workspace_id):
        return True
    if normalized_workspace_id != "default" and _local_worker_fallback_enabled():
        return _workspace_local_worker_online_exact("default")
    return False


def build_capability_truth(
    *,
    workspace_id: str,
    provider: str,
    provider_truth: Dict[str, Any],
    tool_capabilities: Any,
    runtime_ok: bool,
    local_gateway_online: bool,
    local_worker_online: bool = False,
    ai_ready: bool,
    connection_mode: str,
) -> Dict[str, Any]:
    normalized_workspace_id = str(workspace_id or "default").strip() or "default"
    provider_id = "openai-codex" if str(provider or "").strip().lower() == "codex_cli" else str(provider or "").strip().lower()
    provider_catalog_entry = provider_profiles.PROVIDER_CATALOG.get(provider_id) or {}
    capability_entries = _normalize_capability_entries(tool_capabilities)

    connected_apps: List[Dict[str, Any]] = []
    channels: List[Dict[str, Any]] = []
    for item in capability_entries:
        connected_apps.append(
            {
                "id": item["id"],
                "label": item["label"],
                "connected": bool(item.get("connected")),
                "runtime_usable": item.get("runtime_usable")
                if isinstance(item.get("runtime_usable"), bool)
                else None,
            }
        )
        if item["id"] in CHANNEL_CAPABILITY_IDS:
            channels.append(
                {
                    "id": item["id"],
                    "label": item["label"],
                    "connected": bool(item.get("connected")),
                    "runtime_usable": item.get("runtime_usable")
                    if isinstance(item.get("runtime_usable"), bool)
                    else None,
                }
            )

    local_tools_online = bool((runtime_ok and (local_gateway_online or local_worker_online)) or local_gateway_online)
    setup_actions: List[Dict[str, str]] = []
    credential_plane = str(provider_truth.get("credential_plane") or "").strip().lower()
    hosted_enabled = bool(provider_truth.get("hosted_ai_enabled"))
    hosted_reason = str(provider_truth.get("hosted_sage_ai_reason") or "").strip().lower()
    provider_label = str(
        provider_truth.get("provider_label")
        or provider_catalog_entry.get("label")
        or provider_display_name(provider)
    ).strip() or provider_display_name(provider)

    if credential_plane == "local_runtime" and not local_gateway_online:
        _append_setup_action(
            setup_actions,
            action_id="connect_my_computer",
            label="Connect My Computer",
            href="/integrations",
            reason="My Computer is offline.",
        )
    if credential_plane == "local_runtime" and local_gateway_online and not runtime_ok:
        _append_setup_action(
            setup_actions,
            action_id="restart_local_runtime",
            label="Open My Computer",
            href="/integrations",
            reason="My Computer is connected, but local runtime is not healthy.",
        )
    if not ai_ready and credential_plane != "local_runtime":
        if hosted_enabled:
            _append_setup_action(
                setup_actions,
                action_id="connect_or_switch_model",
                label="Choose AI Model",
                href="/integrations",
                reason=f"{provider_label} is selected but not ready in this workspace.",
            )
        else:
            if hosted_reason in {"owner_approval_required", "cap_reached", "policy_disabled"}:
                _append_setup_action(
                    setup_actions,
                    action_id="view_workspace_ai",
                    label="View Workspace AI",
                    href="/integrations",
                    reason="Workspace AI is currently blocked for this workspace.",
                )
            _append_setup_action(
                setup_actions,
                action_id="add_provider_key",
                label="Add API key",
                href="/integrations",
                reason=f"Add a BYOK key to use {provider_label}.",
            )

    for channel_item in channels:
        if not channel_item.get("connected"):
            _append_setup_action(
                setup_actions,
                action_id=f"connect_{channel_item['id']}",
                label=f"Connect {channel_item['label']}",
                href="/integrations",
                reason=f"{channel_item['label']} is not connected.",
            )

    return {
        "workspace_id": normalized_workspace_id,
        "provider": {
            "id": provider_id,
            "label": provider_label,
            "connection_mode": str(connection_mode or "").strip() or None,
            "ai_ready": bool(ai_ready),
            "credential_plane": credential_plane or None,
            "models": list(provider_catalog_entry.get("models") or []),
            "default_model": str(provider_catalog_entry.get("default_model") or "").strip() or None,
        },
        "credits": {
            "hosted_enabled": hosted_enabled,
            "policy": str(provider_truth.get("hosted_sage_ai_policy") or "").strip().lower() or None,
            "reason": hosted_reason or None,
            "monthly_cap_usd": float(provider_truth.get("hosted_sage_ai_monthly_cap_usd") or 0.0),
            "monthly_cost_usd": float(provider_truth.get("hosted_sage_ai_monthly_cost_usd") or 0.0),
            "monthly_remaining_usd": float(provider_truth.get("hosted_sage_ai_monthly_remaining_usd") or 0.0),
        },
        "byok_providers": _byok_provider_options(),
        "my_computer": {
            "online": bool(local_gateway_online or local_worker_online),
            "runtime_ok": bool(runtime_ok),
            "local_worker_online": bool(local_worker_online),
            "local_gateway_online": bool(local_gateway_online),
            "local_tools_available": local_tools_online,
            "state": (
                "online"
                if local_tools_online or local_gateway_online
                else "offline"
            ),
        },
        "local_tools": {
            "available": [
                "file read/list",
                "shell",
                "browser",
                "screenshot",
                "clipboard",
                "computer control",
            ]
            if local_tools_online
            else [],
            "required_state": "My Computer online",
        },
        "connected_apps": connected_apps,
        "channels": channels,
        "permissions": {
            "requested_session_mode": None,
            "effective_session_mode": None,
        },
        "required_setup_actions": setup_actions,
    }


def credential_auth_mode(
    provider: str,
    credentials: Optional[Dict[str, Any]],
    *,
    normalize_auth_mode_fn: Callable[..., str] = normalize_auth_mode,
) -> str:
    payload = credentials if isinstance(credentials, dict) else {}
    return normalize_auth_mode_fn(provider, credentials=payload)


def _openai_explicit_env_api_key_credentials() -> Dict[str, Any]:
    disabled = str(os.getenv("ORION_DISABLE_OPENAI_API_KEY", "1") or "").strip().lower()
    if disabled not in {"0", "false", "no", "off"}:
        return {}
    api_key = str(os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return {}
    return {
        "api_key": api_key,
        "auth_mode": "api_key",
        "credential_type": "api_key",
    }


def supports_direct_message_native_chat(
    provider: str,
    credentials: Optional[Dict[str, Any]],
    *,
    credential_auth_mode_fn: Callable[[str, Optional[Dict[str, Any]]], str] = credential_auth_mode,
    get_claude_code_session_token_fn: Callable[[], Any] = get_claude_code_session_token,
    provider_has_key_fn: Callable[[str], bool] = provider_has_key,
) -> bool:
    payload = credentials if isinstance(credentials, dict) else {}
    auth_mode = credential_auth_mode_fn(provider, payload)
    normalized_provider = str(provider or "").strip().lower()
    credential_type = str(payload.get("credential_type") or "").strip().lower()
    bearer_token = str(payload.get("access_token") or payload.get("oauth_token") or "").strip()
    if normalized_provider == "anthropic":
        if auth_mode == "local_cli":
            return bool(get_claude_code_session_token_fn())
        return bool(str(payload.get("api_key") or "").strip())
    if normalized_provider == "openai":
        if credential_type == "codex_token":
            return False
        return (
            bool(str(payload.get("api_key") or "").strip())
            or (auth_mode in {"oauth_token", "access_token"} and bool(bearer_token))
        )
    if normalized_provider == "gemini":
        return bool(str(payload.get("api_key") or "").strip())
    if normalized_provider in {
        "qwen",
        "deepseek",
        "mistral",
        "vertex",
        "ollama_cloud",
        "groq",
        "openrouter",
        "azure_openai",
        "custom_openai_compatible",
    }:
        return bool(str(payload.get("api_key") or "").strip())
    if normalized_provider in {"openai-codex", "codex_cli"}:
        return bool(payload) or provider_has_key_fn("codex_cli")
    return provider_has_key_fn(normalized_provider)


def direct_chat_credentials(
    workspace_id: str,
    provider: str,
    *,
    build_provider_credential_candidates_fn: Callable[..., list[dict[str, Any]]] = _build_provider_credential_candidates,
    hosted_sage_ai_access_state_for_workspace_id_fn: Callable[..., Dict[str, Any]] = entitlements_service.hosted_sage_ai_access_state_for_workspace_id,
) -> Dict[str, Any]:
    normalized_workspace_id = str(workspace_id or "default").strip() or "default"
    normalized_provider = str(provider or "").strip().lower()
    candidate_provider = "openai-codex" if normalized_provider == "codex_cli" else normalized_provider
    hosted_access_state = hosted_sage_ai_access_state_for_workspace_id_fn(workspace_id=normalized_workspace_id)
    hosted_ai_enabled = bool(hosted_access_state.get("allowed"))
    candidates = build_provider_credential_candidates_fn(
        {"workspace_id": normalized_workspace_id},
        {"source": "chat_direct", "workspace_only": True},
        candidate_provider,
    )
    workspace_candidates_are_usable = any(
        isinstance(item, dict)
        and isinstance(item.get("credentials"), dict)
        and supports_direct_message_native_chat(normalized_provider, item.get("credentials"))
        for item in candidates
    )
    if not candidates or not workspace_candidates_are_usable:
        candidates = build_provider_credential_candidates_fn(
            {"workspace_id": normalized_workspace_id},
            {"source": "chat_direct"},
            candidate_provider,
        )
    if normalized_provider == "codex_cli" and not candidates:
        openai_candidates = build_provider_credential_candidates_fn(
            {"workspace_id": normalized_workspace_id},
            {"source": "chat_direct"},
            "openai",
        )
        candidates = [
            item
            for item in openai_candidates
            if isinstance(item.get("credentials"), dict)
            and str((item.get("credentials") or {}).get("auth_mode") or "").strip().lower() == "oauth_token"
        ]
    if not hosted_ai_enabled:
        candidates = [
            item
            for item in candidates
            if _credential_plane_metadata(source=item.get("source"), identity_owner=None).get("credential_plane") != "platform_runtime"
        ]
    for item in candidates:
        credentials = item.get("credentials") if isinstance(item, dict) else {}
        if not isinstance(credentials, dict):
            continue
        if supports_direct_message_native_chat(normalized_provider, credentials):
            return dict(credentials)
    if normalized_provider == "openai":
        explicit_env_credentials = _openai_explicit_env_api_key_credentials()
        if explicit_env_credentials:
            return explicit_env_credentials
    first = candidates[0].get("credentials") if candidates else {}
    return dict(first) if isinstance(first, dict) else {}


def platform_runtime_credentials(
    workspace_id: str,
    provider: str,
    *,
    tenant_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    normalized_workspace_id = str(workspace_id or "default").strip() or "default"
    normalized_provider = str(provider or "").strip().lower()
    if normalized_provider not in PLATFORM_RUNTIME_AUTH_PROVIDERS:
        return {}
    env_key, _ = provider_profiles._resolve_hosted_provider_api_key(
        provider_id=normalized_provider,
        tenant_id=tenant_id,
        workspace_id=normalized_workspace_id,
        run_id=run_id,
        purpose="platform_runtime_direct_chat",
    )
    if not env_key:
        return {}
    return {
        "api_key": env_key,
        "auth_mode": "platform_runtime",
        "credential_plane": "platform_runtime",
    }


def provider_runtime_usable(
    workspace_id: str,
    provider: str,
    *,
    build_provider_runtime_truth_fn: Callable[[str], Dict[str, Any]] = provider_profiles.build_provider_runtime_truth,
) -> bool:
    normalized_workspace_id = str(workspace_id or "default").strip() or "default"
    normalized_provider = str(provider or "").strip().lower()
    provider_id = "openai-codex" if normalized_provider == "codex_cli" else normalized_provider
    truth = build_provider_runtime_truth_fn(normalized_workspace_id)
    providers_by_id = truth.get("providers_by_id") if isinstance(truth, dict) else {}
    entry = providers_by_id.get(provider_id) if isinstance(providers_by_id, dict) else None
    if not isinstance(entry, dict):
        return True
    if bool(entry.get("backpressure")):
        return False
    if entry.get("usable") is False:
        return False
    return True


def _credential_plane_metadata(
    *,
    source: Any,
    identity_owner: Any,
) -> Dict[str, str]:
    source_token = str(source or "").strip().lower()
    identity_owner_token = str(identity_owner or "").strip().lower()

    if source_token in {"profile", "workspace_profile", "credential_id", "vault_default"} or source_token.startswith("profile:") or source_token.startswith("vault-default"):
        return {
            "credential_owner_kind": "workspace_byok",
            "credential_owner_label": "Workspace BYOK",
            "credential_plane": "workspace_connection",
            "credential_plane_label": "Connected by workspace owner",
        }

    if source_token.startswith("env") or identity_owner_token == "platform_account":
        return {
            "credential_owner_kind": "platform_hosted",
            "credential_owner_label": "Empyralis hosted runtime",
            "credential_plane": "platform_runtime",
            "credential_plane_label": "Provided by Empyralis",
        }

    if source_token.startswith("local") or source_token.endswith("cli") or identity_owner_token in {"local_machine", "machine_owner"}:
        return {
            "credential_owner_kind": "local_machine",
            "credential_owner_label": "Local machine",
            "credential_plane": "local_runtime",
            "credential_plane_label": "Available on this machine",
        }

    return {
        "credential_owner_kind": "unknown",
        "credential_owner_label": "Unknown source",
        "credential_plane": "unknown",
        "credential_plane_label": "Unknown source",
    }


def direct_chat_provider_truth(
    workspace_id: str,
    provider: str,
    *,
    build_workspace_provider_connection_truth_fn: Callable[[str], Dict[str, Any]] = provider_profiles.build_workspace_provider_connection_truth,
    build_provider_runtime_truth_fn: Callable[[str], Dict[str, Any]] = provider_profiles.build_provider_runtime_truth,
    hosted_sage_ai_access_state_for_workspace_id_fn: Callable[..., Dict[str, Any]] = entitlements_service.hosted_sage_ai_access_state_for_workspace_id,
) -> Dict[str, Any]:
    normalized_workspace_id = str(workspace_id or "default").strip() or "default"
    normalized_provider = str(provider or "").strip().lower()
    provider_id = "openai-codex" if normalized_provider == "codex_cli" else normalized_provider

    connection_truth = build_workspace_provider_connection_truth_fn(normalized_workspace_id)
    runtime_truth = build_provider_runtime_truth_fn(normalized_workspace_id)

    connection_entry = (
        (connection_truth.get("providers_by_id") or {}).get(provider_id)
        if isinstance(connection_truth, dict)
        else None
    )
    runtime_entry = (
        (runtime_truth.get("providers_by_id") or {}).get(provider_id)
        if isinstance(runtime_truth, dict)
        else None
    )

    if not isinstance(connection_entry, dict):
        connection_entry = {}
    if not isinstance(runtime_entry, dict):
        runtime_entry = {}

    connection_state = str(connection_entry.get("state") or "").strip() or None
    connection_state_detail = connection_entry.get("state_detail")
    connection_active_source = connection_entry.get("active_source")
    connection_credential_sources = (
        list(connection_entry.get("credential_sources") or [])
        if isinstance(connection_entry.get("credential_sources"), list)
        else []
    )
    workspace_connected = connection_state in {"active", "configured", "degraded"}

    runtime_state = str(runtime_entry.get("state") or connection_state or "").strip() or None
    runtime_state_detail = runtime_entry.get("state_detail") if runtime_entry else connection_state_detail
    runtime_active_source = runtime_entry.get("active_source") if runtime_entry else connection_active_source
    runtime_credential_sources = (
        list(runtime_entry.get("credential_sources") or [])
        if isinstance(runtime_entry.get("credential_sources"), list)
        else connection_credential_sources
    )

    identity_owner = connection_entry.get("identity_owner")
    runtime_identity_source = str(runtime_active_source or "").strip().lower()
    if runtime_identity_source and runtime_identity_source not in {"profile", "workspace_profile"}:
        identity_owner = runtime_entry.get("identity_owner") or identity_owner

    payload: Dict[str, Any] = {
        "provider_id": provider_id,
        "provider_label": runtime_entry.get("label") or connection_entry.get("label") or provider_display_name(normalized_provider),
        "workspace_connected": workspace_connected,
        "connection_state": connection_state,
        "connection_state_detail": connection_state_detail,
        "connection_active_source": connection_active_source,
        "connection_credential_sources": connection_credential_sources,
        "runtime_state": runtime_state,
        "runtime_state_detail": runtime_state_detail,
        "runtime_active_source": runtime_active_source,
        "runtime_credential_sources": runtime_credential_sources,
    }
    payload.update(
        _credential_plane_metadata(
            source=runtime_active_source or connection_active_source,
            identity_owner=identity_owner,
        )
    )
    hosted_access_state = hosted_sage_ai_access_state_for_workspace_id_fn(workspace_id=normalized_workspace_id)
    hosted_ai_enabled = bool(hosted_access_state.get("allowed"))
    hosted_reason = str(hosted_access_state.get("reason") or "").strip().lower() or None
    payload["hosted_ai_enabled"] = hosted_ai_enabled
    payload["hosted_sage_ai_policy"] = str(hosted_access_state.get("policy") or "owner_opt_in").strip().lower() or "owner_opt_in"
    payload["hosted_sage_ai_monthly_cap_usd"] = float(hosted_access_state.get("monthly_cap_usd") or 0.0)
    payload["hosted_sage_ai_monthly_cost_usd"] = float(hosted_access_state.get("monthly_cost_usd") or 0.0)
    payload["hosted_sage_ai_monthly_remaining_usd"] = float(hosted_access_state.get("monthly_remaining_usd") or 0.0)
    payload["hosted_sage_ai_reason"] = hosted_reason
    requires_hosted_lane = (
        str(payload.get("credential_plane") or "").strip().lower() == "platform_runtime"
        and not bool(payload.get("workspace_connected"))
    )
    payload["platform_runtime_allowed"] = bool(
        hosted_ai_enabled or not requires_hosted_lane
    )
    if not payload["platform_runtime_allowed"]:
        if hosted_reason == "owner_approval_required":
            issue_code = "hosted_ai_owner_approval_required"
            detail = "Hosted Sage AI requires workspace owner approval before this provider can run on Empyralis runtime."
        elif hosted_reason == "cap_reached":
            issue_code = "hosted_ai_cap_reached"
            detail = (
                "Hosted Sage AI monthly cap is reached for this workspace. "
                "Switch to your own key or local runtime, or raise the cap."
            )
        else:
            issue_code = "hosted_ai_policy_disabled"
            detail = (
                "Hosted Sage AI is disabled for this workspace. "
                "Connect your own provider key or use a local runtime instead."
            )
        payload.update(
            {
                "runtime_state": "restricted",
                "runtime_state_detail": detail,
                "runtime_active_source": None,
                "runtime_credential_sources": [],
                "issue_code": issue_code,
                "issue": detail,
            }
        )
    return payload


def preferred_provider(
    workspace_id: str,
    requested_provider: str = "",
    *,
    supported_providers: List[str] | tuple[str, ...] = SUPPORTED_PROVIDERS,
    direct_chat_credentials_fn: Callable[[str, str], Dict[str, Any]] = direct_chat_credentials,
    supports_direct_message_native_chat_fn: Callable[[str, Optional[Dict[str, Any]]], bool] = supports_direct_message_native_chat,
    credential_auth_mode_fn: Callable[[str, Optional[Dict[str, Any]]], str] = credential_auth_mode,
    provider_runtime_usable_fn: Callable[[str, str], bool] = provider_runtime_usable,
) -> tuple[str, Dict[str, Any]]:
    normalized_workspace_id = str(workspace_id or "default").strip() or "default"
    requested = str(requested_provider or "").strip().lower()
    normalized_requested = "codex_cli" if requested == "openai-codex" else requested
    if normalized_requested in supported_providers:
        if normalized_requested == "codex_cli" and not provider_runtime_usable_fn(normalized_workspace_id, "codex_cli"):
            return normalized_requested, {}
        requested_credentials = direct_chat_credentials_fn(normalized_workspace_id, normalized_requested)
        if supports_direct_message_native_chat_fn(normalized_requested, requested_credentials):
            return normalized_requested, requested_credentials
        return normalized_requested, requested_credentials
    # Launch preference: keep Anthropic optional and prefer DeepSeek/Gemini/OpenAI first.
    for provider in ("deepseek", "gemini", "openai", "ollama_cloud", "anthropic"):
        credentials = direct_chat_credentials_fn(normalized_workspace_id, provider)
        if provider == "openai":
            credential_type = str(credentials.get("credential_type") or "").strip().lower()
            if credential_type == "codex_token" or credential_auth_mode_fn("openai", credentials) == "oauth_token":
                codex_credentials = direct_chat_credentials_fn(normalized_workspace_id, "codex_cli")
                if provider_runtime_usable_fn(normalized_workspace_id, "codex_cli") and supports_direct_message_native_chat_fn("codex_cli", codex_credentials):
                    return "codex_cli", codex_credentials
                continue
        if supports_direct_message_native_chat_fn(provider, credentials):
            return provider, credentials
    codex_credentials = direct_chat_credentials_fn(normalized_workspace_id, "codex_cli")
    if provider_runtime_usable_fn(normalized_workspace_id, "codex_cli") and supports_direct_message_native_chat_fn("codex_cli", codex_credentials):
        return "codex_cli", codex_credentials
    fallback_provider = requested if requested in supported_providers else "openai"
    fallback_credentials = direct_chat_credentials_fn(normalized_workspace_id, fallback_provider)
    return fallback_provider, fallback_credentials


def provider_display_name(provider: str) -> str:
    normalized = str(provider or "").strip().lower()
    if normalized == "codex_cli":
        return "Codex/OpenAI"
    if normalized == "claude_code_cli":
        return "Claude Code"
    if normalized == "openai":
        return "OpenAI"
    if normalized == "anthropic":
        return "Anthropic"
    if normalized == "gemini":
        return "Gemini"
    if normalized == "ollama":
        return "Ollama"
    if normalized == "ollama_cloud":
        return "Ollama Cloud"
    return normalized or "AI"


def provider_unavailable_response(
    provider: str,
    *,
    connect_action: Callable[[str, str], Dict[str, Any]],
    issue_code: Optional[str] = None,
    issue_detail: Optional[str] = None,
) -> Dict[str, Any]:
    label = provider_display_name(provider)
    normalized_issue = str(issue_code or "").strip().lower()
    detail = str(issue_detail or "").strip()
    if normalized_issue in {"hosted_ai_policy_disabled", "hosted_ai_owner_approval_required", "hosted_ai_cap_reached"}:
        summary = detail or "Hosted Sage AI is currently blocked for this workspace."
        return {
            "reply": "",
            "actions": [connect_action("Connect", "/connect-ai")],
            "mode": "connect",
            "interventions": [
                build_intervention(
                    "connect_required",
                    "Hosted Sage AI is blocked",
                    detail=summary,
                    severity="warning",
                    status="waiting",
                    code=normalized_issue,
                    metadata={"provider": provider},
                )
            ],
        }
    if provider == "codex_cli":
        return {
            "reply": "",
            "actions": [connect_action("Connect", "/connect-ai")],
            "mode": "connect",
            "interventions": [
                build_intervention(
                    "connect_required",
                    "Workspace AI account is not ready",
                    detail="Connect the workspace AI account to use model-backed chat in this workspace.",
                    severity="warning",
                    status="waiting",
                    code="provider_unavailable",
                    metadata={"provider": provider},
                )
            ],
        }
    return {
        "reply": "",
        "actions": [connect_action("Connect", "/connect-ai")],
        "mode": "connect",
        "interventions": [
            build_intervention(
                "connect_required",
                f"{label} is not available",
                detail=(
                    f"{label} is selected for chat but is not available right now. "
                    "Connect this computer, switch to Workspace AI, or add your own API key."
                ),
                severity="warning",
                status="waiting",
                code="provider_unavailable",
                metadata={"provider": provider},
            )
        ],
    }


def direct_chat_runtime_available(
    local_worker_registry: Dict[str, Any],
    *,
    is_worker_online_fn: Callable[[Any, datetime], bool],
    now: Optional[datetime] = None,
) -> bool:
    try:
        current_time = now if isinstance(now, datetime) else datetime.now(timezone.utc)
        return any(
            isinstance(record, dict) and is_worker_online_fn(record, current_time)
            for record in local_worker_registry.values()
        )
    except Exception:
        return False


def resolve_direct_chat_availability(
    workspace_id: str,
    requested_provider: str = "",
    *,
    direct_chat_runtime_available_fn: Callable[[], bool],
    preferred_provider_fn: Callable[[str, str], tuple[str, Dict[str, Any]]],
    supports_direct_message_native_chat_fn: Callable[[str, Optional[Dict[str, Any]]], bool],
    resolve_workspace_tool_capabilities_fn: Callable[[str], List[Dict[str, Any]]],
    direct_chat_provider_truth_fn: Callable[[str, str], Dict[str, Any]] = direct_chat_provider_truth,
    workspace_live_gateway_available_fn: Callable[[str], bool] = provider_profiles.workspace_live_gateway_available,
    availability_override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    normalized_workspace_id = str(workspace_id or "default").strip() or "default"
    runtime_ok = direct_chat_runtime_available_fn()
    provider, credentials = preferred_provider_fn(normalized_workspace_id, requested_provider)
    tool_capabilities = skills_service.resolve_workspace_capability_payloads(
        normalized_workspace_id,
        resolve_workspace_tool_capabilities_fn=resolve_workspace_tool_capabilities_fn,
    )
    override_payload = availability_override if isinstance(availability_override, dict) else {}
    source_channel = str(override_payload.get("surface_channel") or override_payload.get("channel") or "").strip().lower()
    source_token = str(override_payload.get("source") or override_payload.get("source_surface") or "").strip().lower()
    mobile_server_first = (
        bool(override_payload.get("mobile_server_first"))
        or source_channel == "mobile"
        or source_token in {"mobile_chat", "mobile_workspace_chat_surface"}
    )
    provider_truth = direct_chat_provider_truth_fn(normalized_workspace_id, provider)
    ai_ready = supports_direct_message_native_chat_fn(provider, credentials)
    credential_plane = str(provider_truth.get("credential_plane") or "").strip().lower()
    runtime_state = str(provider_truth.get("runtime_state") or "").strip().lower()
    local_gateway_online = workspace_live_gateway_available_fn(normalized_workspace_id)

    local_worker_online = _workspace_local_worker_online(normalized_workspace_id)
    # Agent machine mode: override ALL availability flags so the LLM knows
    # local tools (shell, file, screenshot, computer) are usable.
    if runtime_config.AGENT_MACHINE_MODE == "agent":
        try:
            from server_modules.supervisor_client import _assert_direct_supervisor_allowed
            _assert_direct_supervisor_allowed()
            local_gateway_online = True
            local_worker_online = True
            runtime_ok = True
            ai_ready = True
        except Exception:
            pass
    if credential_plane == "local_runtime" and (runtime_state != "active" or not local_gateway_online) and runtime_config.AGENT_MACHINE_MODE != "agent":
        ai_ready = False
    connection_mode = ""
    if ai_ready:
        if credential_plane == "local_runtime":
            connection_mode = "local_companion" if runtime_ok else "byok"
        elif credential_plane == "workspace_connection":
            connection_mode = "byok"
        elif credential_plane == "platform_runtime":
            connection_mode = "platform"
        else:
            connection_mode = "platform" if mobile_server_first else "local_companion" if runtime_ok else "byok"

    resolved: Dict[str, Any] = {
        "ai_ready": ai_ready,
        "runtime_ok": runtime_ok,
        "local_gateway_online": local_gateway_online,
        "local_worker_online": local_worker_online,
        "connection_mode": connection_mode,
        "provider": provider,
        **provider_truth,
        "tool_capabilities": tool_capabilities,
    }
    resolved["capability_truth"] = build_capability_truth(
        workspace_id=normalized_workspace_id,
        provider=provider,
        provider_truth=provider_truth,
        tool_capabilities=tool_capabilities,
        runtime_ok=runtime_ok,
        local_gateway_online=local_gateway_online,
        local_worker_online=local_worker_online,
        ai_ready=ai_ready,
        connection_mode=connection_mode,
    )
    if override_payload:
        resolved.update(override_payload)
        if "tool_capabilities" not in override_payload:
            resolved["tool_capabilities"] = tool_capabilities
        if "connection_mode" not in override_payload:
            resolved["connection_mode"] = connection_mode
        if "capability_truth" not in override_payload:
            resolved["capability_truth"] = build_capability_truth(
                workspace_id=normalized_workspace_id,
                provider=provider,
                provider_truth=provider_truth,
                tool_capabilities=resolved.get("tool_capabilities"),
                runtime_ok=bool(resolved.get("runtime_ok")),
                local_gateway_online=bool(resolved.get("local_gateway_online")),
                local_worker_online=bool(resolved.get("local_worker_online")),
                ai_ready=bool(resolved.get("ai_ready")),
                connection_mode=str(resolved.get("connection_mode") or ""),
            )
    return resolved


def connected_provider_tokens(
    workspace_id: str,
    *,
    supported_providers: List[str] | tuple[str, ...] = SUPPORTED_PROVIDERS,
    direct_chat_credentials_fn: Callable[[str, str], Dict[str, Any]] = direct_chat_credentials,
) -> List[str]:
    connected: List[str] = []
    for provider in supported_providers:
        try:
            credentials = direct_chat_credentials_fn(workspace_id, provider)
        except Exception:
            credentials = {}
        if isinstance(credentials, dict) and credentials:
            connected.append(provider)
    return connected


def message_prefers_codex_for_direct_chat(
    message: str,
    *,
    tools_present: bool,
    compact_text_fn: Callable[[Any], str],
    mentions_any_fn: Callable[[str, tuple[str, ...] | list[str]], bool],
    message_requests_local_file_tool_fn: Callable[[str], bool],
    message_requests_local_shell_tool_fn: Callable[[str], bool],
    message_requests_local_screenshot_tool_fn: Callable[[str], bool],
    message_requests_local_computer_tool_fn: Callable[[str], bool],
    google_workspace_keywords: tuple[str, ...] | list[str],
    telegram_keywords: tuple[str, ...] | list[str],
    slack_keywords: tuple[str, ...] | list[str],
    dropbox_keywords: tuple[str, ...] | list[str],
    s3_keywords: tuple[str, ...] | list[str],
) -> bool:
    compact_message = compact_text_fn(message)
    connector_heavy = bool(tools_present) and (
        mentions_any_fn(compact_message, google_workspace_keywords)
        or mentions_any_fn(compact_message, telegram_keywords)
        or mentions_any_fn(compact_message, slack_keywords)
        or mentions_any_fn(compact_message, dropbox_keywords)
        or mentions_any_fn(compact_message, s3_keywords)
    )
    local_machine_request = (
        message_requests_local_file_tool_fn(message)
        or message_requests_local_shell_tool_fn(message)
        or message_requests_local_screenshot_tool_fn(message)
        or message_requests_local_computer_tool_fn(message)
    )
    return connector_heavy or local_machine_request


def resolve_provider_for_direct_chat_message(
    workspace_id: str,
    requested_provider: str,
    message: str,
    *,
    tools_present: bool,
    preferred_provider_fn: Callable[[str, str], tuple[str, Dict[str, Any]]],
    direct_chat_credentials_fn: Callable[[str, str], Dict[str, Any]],
    supports_direct_message_native_chat_fn: Callable[[str, Optional[Dict[str, Any]]], bool],
    provider_runtime_usable_fn: Callable[[str, str], bool] = provider_runtime_usable,
    compact_text_fn: Callable[[Any], str],
    mentions_any_fn: Callable[[str, tuple[str, ...] | list[str]], bool],
    message_requests_local_file_tool_fn: Callable[[str], bool],
    message_requests_local_shell_tool_fn: Callable[[str], bool],
    message_requests_local_screenshot_tool_fn: Callable[[str], bool],
    message_requests_local_computer_tool_fn: Callable[[str], bool],
    google_workspace_keywords: tuple[str, ...] | list[str],
    telegram_keywords: tuple[str, ...] | list[str],
    slack_keywords: tuple[str, ...] | list[str],
    dropbox_keywords: tuple[str, ...] | list[str],
    s3_keywords: tuple[str, ...] | list[str],
) -> tuple[str, Dict[str, Any]]:
    provider, credentials = preferred_provider_fn(workspace_id, requested_provider)
    explicit_provider = bool(str(requested_provider or "").strip())
    if not message_prefers_codex_for_direct_chat(
        message,
        tools_present=tools_present,
        compact_text_fn=compact_text_fn,
        mentions_any_fn=mentions_any_fn,
        message_requests_local_file_tool_fn=message_requests_local_file_tool_fn,
        message_requests_local_shell_tool_fn=message_requests_local_shell_tool_fn,
        message_requests_local_screenshot_tool_fn=message_requests_local_screenshot_tool_fn,
        message_requests_local_computer_tool_fn=message_requests_local_computer_tool_fn,
        google_workspace_keywords=google_workspace_keywords,
        telegram_keywords=telegram_keywords,
        slack_keywords=slack_keywords,
        dropbox_keywords=dropbox_keywords,
        s3_keywords=s3_keywords,
    ):
        return provider, credentials
    if explicit_provider:
        return provider, credentials
    codex_credentials = direct_chat_credentials_fn(workspace_id, "codex_cli")
    if provider_runtime_usable_fn(workspace_id, "codex_cli") and supports_direct_message_native_chat_fn("codex_cli", codex_credentials):
        return "codex_cli", codex_credentials
    return provider, credentials
