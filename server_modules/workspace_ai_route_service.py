from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from server_modules import auth as auth_module
from server_modules import connectors_core
from server_modules import empyralis_model_tier_contract
from server_modules import empyralis_model_tier_routing_service
from server_modules import provider_catalog_service
from server_modules import provider_profiles
from server_modules import rust_runtime_kernel_client
from server_modules.runtime_models import ProviderProfileUpsertRequest


AI_ROUTE_KINDS = {
    "empyralis_managed",
    "user_api_key",
    "local_model",
    "enterprise_gateway",
}
PUBLIC_MODEL_PRESETS = {"light", "pro"}
LOCAL_PROVIDER_IDS = {"ollama"}
LOCAL_SUBSCRIPTION_PROVIDER_IDS = {"openai-codex", "claude_code_cli"}
ENTERPRISE_PROVIDER_IDS = {
    "azure_openai",
    "bedrock",
    "vertex",
    "litellm",
    "vercel_ai_gateway",
    "custom_openai_compatible",
}
PERSONAL_SUBSCRIPTION_ROUTE_KINDS = {
    "subscription",
    "subscription_passthrough",
    "chatgpt_subscription",
    "claude_subscription",
}


def _read_string(value: Any, default: str = "") -> str:
    return str(value if value is not None else default).strip()


def _read_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _read_list(value: Any) -> List[Any]:
    return list(value) if isinstance(value, list) else []


def _current_user_actor_id(current_user: Any) -> str:
    if isinstance(current_user, dict):
        for key in ("user_id", "id", "sub", "email"):
            token = _read_string(current_user.get(key))
            if token:
                return token
    return "workspace_ai_route_service"


def _current_user_tenant_id(current_user: Any, workspace_id: str) -> str:
    if isinstance(current_user, dict):
        token = _read_string(current_user.get("tenant_id"))
        if token:
            return token
    try:
        token = auth_module.workspace_tenant_id(current_user, workspace_id)
    except Exception:
        token = ""
    return _read_string(token) or _read_string(workspace_id) or "default"


def _enforce_workspace_ai_route_update(
    *,
    workspace_id: str,
    current_user: Any,
    selected_kind: str,
    selected_provider: Optional[str],
    selected_model_preset: Optional[str],
) -> Dict[str, Any]:
    payload = {
        "operation": "workspace_ai_route_update",
        "record_type": "workspace_ai_route",
        "tenant_id": _current_user_tenant_id(current_user, workspace_id),
        "workspace_id": workspace_id,
        "actor_id": _current_user_actor_id(current_user),
        "actor_role": "owner",
        "target_status": selected_kind,
        "idempotency_key": (
            f"workspace_ai_route:{workspace_id}:"
            f"{selected_kind}:{selected_provider or 'managed'}:{selected_model_preset or 'default'}"
        ),
        "owner_access": True,
        "admin_access": True,
        "workspace_access": True,
        "billing_entitled": True,
        "quota_ok": True,
        "approval_provided": True,
        "owner_approval_provided": True,
    }
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "control-plane-service-decision",
            payload,
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        result = getattr(exc, "result", None)
        if not isinstance(result, dict):
            result = {}
        raise HTTPException(
            status_code=423,
            detail={
                "error": "rust_control_plane_service_denied",
                "operation": result.get("operation") or "workspace_ai_route_update",
                "reason": result.get("reason") or str(exc),
            },
        ) from exc
    mutation_plan = _read_dict(decision.get("mutation_plan"))
    next_action = _read_string(mutation_plan.get("next_action") or decision.get("next_action"))
    if mutation_plan.get("apply") is not True:
        raise HTTPException(
            status_code=423,
            detail={
                "error": "rust_control_plane_service_denied",
                "operation": decision.get("operation") or "workspace_ai_route_update",
                "reason": decision.get("reason") or "missing_rust_mutation_plan",
            },
        )
    if next_action != "apply_control_plane_write":
        raise HTTPException(
            status_code=423,
            detail={
                "error": "rust_control_plane_service_denied",
                "operation": decision.get("operation") or "workspace_ai_route_update",
                "reason": f"unexpected_next_action:{next_action or 'missing'}",
            },
        )
    return decision


def _profile_metadata(profile: Any) -> Dict[str, Any]:
    if not isinstance(profile, dict):
        return {}
    return _read_dict(profile.get("metadata"))


def _profile_sort_key(profile: Dict[str, Any]) -> tuple[int, str, str]:
    try:
        priority = int(profile.get("priority") or 100)
    except Exception:
        priority = 100
    return (
        priority,
        _read_string(profile.get("provider")).lower(),
        _read_string(profile.get("label")).lower(),
    )


def _catalog_provider_by_id(catalog_payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    providers = {}
    for item in _read_list(catalog_payload.get("providers")):
        if not isinstance(item, dict):
            continue
        provider_id = provider_profiles.normalize_provider_id(item.get("id") or item.get("provider"))
        if provider_id:
            providers[provider_id] = dict(item)
    return providers


def _provider_route_kind(provider_id: str, provider_record: Optional[Dict[str, Any]] = None) -> str:
    record = provider_record or {}
    scopes = {
        _read_string(scope).lower()
        for scope in _read_list(record.get("provider_scopes"))
        if _read_string(scope)
    }
    credential_plane = _read_string(record.get("credential_plane")).lower()
    if (
        provider_id in LOCAL_PROVIDER_IDS
        or bool(record.get("local_only"))
        or bool(record.get("machine_bound"))
        or "local_only" in scopes
    ):
        return "local_model"
    if provider_id in ENTERPRISE_PROVIDER_IDS or "enterprise" in scopes or "gateway" in scopes:
        return "enterprise_gateway"
    return "user_api_key"


def _is_personal_subscription_passthrough(provider_id: str, provider_record: Optional[Dict[str, Any]] = None) -> bool:
    record = provider_record or {}
    scopes = {
        _read_string(scope).lower()
        for scope in _read_list(record.get("provider_scopes"))
        if _read_string(scope)
    }
    credential_plane = _read_string(record.get("credential_plane")).lower()
    auth_mode = _read_string(record.get("auth_mode") or record.get("default_auth_mode")).lower()
    active_source = _read_string(record.get("active_source") or record.get("runtime_active_source")).lower()
    state_detail = _read_string(record.get("state_detail") or record.get("runtime_state_detail")).lower()
    if provider_id in LOCAL_SUBSCRIPTION_PROVIDER_IDS:
        return True
    if "claude-cli" in active_source or "codex" in active_source:
        return True
    if "subscription" in state_detail and provider_id not in LOCAL_PROVIDER_IDS:
        return True
    return (
        credential_plane == "local_runtime"
        and provider_id not in LOCAL_PROVIDER_IDS
        and not bool(record.get("local_only"))
        and not bool(record.get("machine_bound"))
        and (
            auth_mode in {"local_cli", "local_subscription"}
            or active_source.endswith("cli")
            or "subscription" in scopes
        )
    )


def _billing_source_for_kind(kind: str, provider_record: Optional[Dict[str, Any]] = None) -> str:
    if kind == "empyralis_managed":
        return "empyralis_credits"
    if kind == "local_model":
        return "local_runtime"
    if kind == "enterprise_gateway":
        return "enterprise_gateway"
    record = provider_record or {}
    credential_plane = _read_string(record.get("credential_plane")).lower()
    if credential_plane == "workspace_connection":
        return "workspace_api_key"
    return "provider_api_key"


def _runtime_target_for_kind(kind: str, provider_record: Optional[Dict[str, Any]] = None) -> str:
    record = provider_record or {}
    if kind == "local_model":
        if _read_string(record.get("machine_label")):
            return _read_string(record.get("machine_label"))
        return "This Device"
    if kind == "enterprise_gateway":
        return "Enterprise gateway"
    if kind == "empyralis_managed":
        return "Cloud worker"
    return "Workspace provider"


def _public_tier_label(model_preset: Optional[str]) -> str:
    token = empyralis_model_tier_contract.normalize_model_tier(model_preset or "light", fallback="light")
    if token not in PUBLIC_MODEL_PRESETS:
        token = "light"
    return token.capitalize()


def _hosted_route(
    *,
    preset: str = "light",
    enabled: bool = True,
    status: str = "available",
    description: Optional[str] = None,
) -> Dict[str, Any]:
    public_label = _public_tier_label(preset)
    return {
        "id": f"empyralis_managed:{preset}",
        "kind": "empyralis_managed",
        "label": f"{public_label} · Workspace AI",
        "publicLabel": public_label,
        "description": description or "Managed by Empyralis credits through the workspace default route.",
        "providerId": None,
        "modelId": None,
        "modelPreset": preset,
        "billingSource": "empyralis_credits",
        "runtimeTarget": "Cloud worker",
        "enabled": bool(enabled),
        "status": status,
    }


def _provider_route(
    provider_id: str,
    provider_record: Dict[str, Any],
    *,
    explicit_profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    kind = _provider_route_kind(provider_id, provider_record)
    label = _read_string(provider_record.get("label")) or _read_string(explicit_profile.get("label") if explicit_profile else None) or provider_id
    default_model = (
        _read_string(explicit_profile.get("model") if explicit_profile else None)
        or _read_string(provider_record.get("default_model"))
        or None
    )
    enabled = bool(provider_record.get("usable") or provider_record.get("active") or provider_record.get("configured"))
    status = _read_string(provider_record.get("state")) or ("connected" if enabled else "setup_required")
    suffix = "API key" if kind == "user_api_key" else "route"
    if kind == "local_model":
        suffix = "local model"
    elif kind == "enterprise_gateway":
        suffix = "gateway"
    return {
        "id": f"provider:{provider_id}",
        "kind": kind,
        "label": f"{label} {suffix}",
        "publicLabel": label,
        "description": _read_string(provider_record.get("state_detail")) or "Workspace provider route.",
        "providerId": provider_id,
        "modelId": default_model,
        "modelPreset": None,
        "billingSource": _billing_source_for_kind(kind, provider_record),
        "runtimeTarget": _runtime_target_for_kind(kind, provider_record),
        "enabled": enabled,
        "status": status,
    }


def _build_used_by() -> List[Dict[str, Any]]:
    return [
        {
            "surface": "sage",
            "label": "Agent",
            "scope": "workspace_default",
            "detail": "Workspace default",
            "override": False,
        },
        {
            "surface": "studio_agents",
            "label": "Studio agents",
            "scope": "workspace_default",
            "detail": "Workspace default unless an agent overrides inside setup.",
            "override": False,
        },
        {
            "surface": "mini_apps",
            "label": "Mini-apps",
            "scope": "workspace_default",
            "detail": "Workspace default unless an app policy requires otherwise.",
            "override": False,
        },
    ]


def _build_budgets(catalog_payload: Dict[str, Any]) -> Dict[str, Any]:
    hosted = _read_dict(catalog_payload.get("hosted_sage_ai"))
    monthly_cap = hosted.get("monthly_cap_usd")
    monthly_remaining = hosted.get("monthly_remaining_usd")
    monthly_cost = hosted.get("monthly_cost_usd")
    return {
        "workspaceMonthlyCapUsd": monthly_cap,
        "workspaceMonthlyRemainingUsd": monthly_remaining,
        "workspaceMonthlyCostUsd": monthly_cost,
        "remainingCredits": hosted.get("remaining_credits") or hosted.get("credits_remaining"),
        "fallbackAllowed": bool(catalog_payload.get("hosted_ai_enabled")),
        "perAgentCap": None,
        "perAppCap": None,
    }


def _explicit_workspace_profile(profiles: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for profile in sorted(profiles, key=_profile_sort_key):
        metadata = _profile_metadata(profile)
        if _read_string(metadata.get("chat_model_selection")).lower() == "explicit":
            return profile
    return None


def _resolve_workspace_default(
    *,
    catalog_payload: Dict[str, Any],
    providers_by_id: Dict[str, Dict[str, Any]],
    profiles: List[Dict[str, Any]],
) -> Dict[str, Any]:
    explicit_profile = _explicit_workspace_profile(profiles)
    if explicit_profile:
        metadata = _profile_metadata(explicit_profile)
        provider_id = provider_profiles.normalize_provider_id(explicit_profile.get("provider"))
        provider_record = providers_by_id.get(provider_id) or {
            "id": provider_id,
            "label": _read_string(explicit_profile.get("label")) or provider_id,
            "default_model": explicit_profile.get("model"),
            "configured": True,
            "usable": bool(explicit_profile.get("enabled", True)),
            "state": "connected",
        }
        if _is_personal_subscription_passthrough(provider_id, provider_record):
            return _hosted_route(
                preset="light",
                enabled=bool(catalog_payload.get("hosted_ai_enabled")),
                status="available" if bool(catalog_payload.get("hosted_ai_enabled")) else "setup_required",
                description="Personal AI subscriptions are not workspace AI routes. Use Empyralis credits, a provider API key, or a true local model.",
            )
        model_preset = empyralis_model_tier_contract.normalize_model_tier(
            metadata.get("chat_model_tier"),
            fallback="",
        )
        if model_preset in PUBLIC_MODEL_PRESETS:
            route = _hosted_route(preset=model_preset, enabled=True, status="connected")
            route["providerId"] = provider_id or None
            route["modelId"] = _read_string(explicit_profile.get("model")) or None
            return route
        route = _provider_route(provider_id, provider_record, explicit_profile=explicit_profile)
        route["enabled"] = bool(explicit_profile.get("enabled", True))
        route["status"] = "connected" if route["enabled"] else "disabled"
        return route

    hosted_enabled = bool(catalog_payload.get("hosted_ai_enabled"))
    if hosted_enabled:
        return _hosted_route(preset="light", enabled=True, status="available")

    return {
        "id": "setup_required",
        "kind": "empyralis_managed",
        "label": "Set up AI route",
        "publicLabel": "Setup required",
        "description": "Connect Empyralis credits, a provider API key, or a local model.",
        "providerId": None,
        "modelId": None,
        "modelPreset": None,
        "billingSource": None,
        "runtimeTarget": None,
        "enabled": False,
        "status": "setup_required",
    }


async def build_workspace_ai_route_payload(workspace_id: str) -> Dict[str, Any]:
    normalized_workspace_id = _read_string(workspace_id) or "default"
    catalog_payload = await provider_catalog_service.list_workspace_provider_catalog(
        workspace_id=normalized_workspace_id,
    )
    profiles_payload = await connectors_core.list_provider_profiles(workspace_id=normalized_workspace_id)
    profiles = [
        dict(item)
        for item in _read_list(_read_dict(profiles_payload).get("items"))
        if isinstance(item, dict)
    ]
    providers_by_id = _catalog_provider_by_id(_read_dict(catalog_payload))

    available_routes = [
        _hosted_route(
            preset=preset,
            enabled=bool(catalog_payload.get("hosted_ai_enabled")),
            status="available" if bool(catalog_payload.get("hosted_ai_enabled")) else "setup_required",
        )
        for preset in ("light", "pro")
    ]
    for provider_id, provider_record in sorted(providers_by_id.items()):
        if _is_personal_subscription_passthrough(provider_id, provider_record):
            continue
        route = _provider_route(provider_id, provider_record)
        if route["kind"] in PERSONAL_SUBSCRIPTION_ROUTE_KINDS:
            continue
        available_routes.append(route)

    workspace_default = _resolve_workspace_default(
        catalog_payload=_read_dict(catalog_payload),
        providers_by_id=providers_by_id,
        profiles=profiles,
    )

    return {
        "workspaceId": normalized_workspace_id,
        "workspaceDefault": workspace_default,
        "availableRoutes": available_routes,
        "usedBy": _build_used_by(),
        "budgets": _build_budgets(_read_dict(catalog_payload)),
        "unsupportedRoutes": [
            {
                "kind": "subscription_passthrough",
                "label": "Personal ChatGPT or Claude subscriptions",
                "reason": "Personal web subscriptions are browser/session automation, not stable backend AI routes.",
            }
        ],
    }


def _resolve_update_selection(
    *,
    route_id: Optional[str],
    kind: Optional[str],
    provider: Optional[str],
    model_preset: Optional[str],
) -> Dict[str, Optional[str]]:
    route_token = _read_string(route_id)
    next_kind = _read_string(kind).lower() or None
    next_provider = provider_profiles.normalize_provider_id(provider) if _read_string(provider) else None
    next_preset = _read_string(model_preset).lower() or None

    if route_token:
        route_kind, _, route_value = route_token.partition(":")
        if route_kind in PERSONAL_SUBSCRIPTION_ROUTE_KINDS:
            raise HTTPException(status_code=400, detail="Personal AI subscriptions are not workspace AI routes.")
        if route_kind == "provider":
            next_provider = provider_profiles.normalize_provider_id(route_value)
        elif route_kind == "empyralis_managed":
            next_kind = "empyralis_managed"
            normalized_route_value = empyralis_model_tier_contract.normalize_model_tier(route_value, fallback="")
            if normalized_route_value in PUBLIC_MODEL_PRESETS:
                next_preset = normalized_route_value
        elif route_kind:
            next_kind = route_kind

    if next_kind in PERSONAL_SUBSCRIPTION_ROUTE_KINDS:
        raise HTTPException(status_code=400, detail="Personal AI subscriptions are not workspace AI routes.")
    if next_kind and next_kind not in AI_ROUTE_KINDS:
        raise HTTPException(status_code=400, detail=f"Unsupported AI route kind '{next_kind}'.")
    if next_preset:
        next_preset = empyralis_model_tier_contract.normalize_model_tier(next_preset, fallback="")
    if next_preset and next_preset not in PUBLIC_MODEL_PRESETS:
        raise HTTPException(status_code=400, detail="model_preset must be light or pro.")
    if not next_kind and next_provider:
        next_kind = None
    if not next_kind:
        next_kind = "empyralis_managed" if not next_provider else "user_api_key"
    return {
        "kind": next_kind,
        "provider": next_provider,
        "model_preset": next_preset,
    }


def _existing_profile_for_provider(profiles: List[Dict[str, Any]], provider_id: str) -> Optional[Dict[str, Any]]:
    for profile in sorted(profiles, key=_profile_sort_key):
        if provider_profiles.normalize_provider_id(profile.get("provider")) == provider_id:
            return profile
    return None


def _existing_platform_profile_for_provider(profiles: List[Dict[str, Any]], provider_id: str) -> Optional[Dict[str, Any]]:
    normalized_provider_id = provider_profiles.normalize_provider_id(provider_id)
    for profile in sorted(profiles, key=_profile_sort_key):
        if provider_profiles.normalize_provider_id(profile.get("provider")) != normalized_provider_id:
            continue
        metadata = _profile_metadata(profile)
        auth_mode = _read_string(profile.get("auth_mode")).lower()
        billing_source = _read_string(metadata.get("billing_source")).lower()
        if auth_mode == "platform_runtime" or billing_source == "empyralis_credits":
            return profile
    return None


def _update_existing_provider_profile_route_metadata(
    profile: Dict[str, Any],
    *,
    workspace_id: str,
    priority: int,
    enabled: bool,
    model: Optional[str],
    metadata: Dict[str, Any],
) -> bool:
    profile_id = _read_string(profile.get("id"))
    if not profile_id:
        return False

    store = getattr(connectors_core, "PROVIDER_PROFILES", None)
    lock = getattr(connectors_core, "PROFILES_LOCK", None)
    persist = getattr(connectors_core, "_persist_provider_profiles", None)
    now_iso = getattr(connectors_core, "_utc_now_iso", None)
    if store is None or lock is None or not callable(persist):
        return False

    with lock:
        existing = store.get(profile_id)
        if not isinstance(existing, dict):
            return False
        provider_id = provider_profiles.normalize_provider_id(existing.get("provider") or profile.get("provider"))
        next_profile = dict(existing)
        next_profile["workspace_id"] = workspace_id
        next_profile["priority"] = int(priority)
        next_profile["enabled"] = bool(enabled)
        if model is not None:
            next_profile["model"] = provider_profiles.normalize_provider_model_id(
                provider_id,
                model,
                fallback_to_default=bool(model),
            ) or None
        next_profile["metadata"] = dict(metadata)
        if callable(now_iso):
            next_profile["updated_at"] = now_iso()
        store[profile_id] = next_profile

    persist()
    return True


async def update_workspace_default_ai_route(
    *,
    workspace_id: str,
    current_user: Any = None,
    route_id: Optional[str] = None,
    kind: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    model_preset: Optional[str] = None,
) -> Dict[str, Any]:
    normalized_workspace_id = _read_string(workspace_id) or "default"
    selection = _resolve_update_selection(
        route_id=route_id,
        kind=kind,
        provider=provider,
        model_preset=model_preset,
    )
    selected_kind = selection["kind"] or "empyralis_managed"
    selected_provider = selection["provider"]
    selected_model_preset = selection["model_preset"]
    selected_model_override = _read_string(model) or None
    target_profile: Optional[Dict[str, Any]] = None

    if selected_kind == "empyralis_managed" and selected_model_preset:
        try:
            tier_route = empyralis_model_tier_routing_service.resolve_backend_provider_model_for_tier(
                selected_model_preset
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        selected_provider = provider_profiles.normalize_provider_id(tier_route.get("provider"))
        selected_model_override = _read_string(tier_route.get("model")) or selected_model_override

    profiles_payload = await connectors_core.list_provider_profiles(workspace_id=normalized_workspace_id)
    profiles = [
        dict(item)
        for item in _read_list(_read_dict(profiles_payload).get("items"))
        if isinstance(item, dict)
    ]

    if selected_provider:
        catalog_entry = provider_profiles.provider_catalog_entry(selected_provider)
        if not catalog_entry:
            raise HTTPException(status_code=400, detail=f"Unsupported provider '{selected_provider}'.")
        managed_platform_profile = selected_kind == "empyralis_managed" and bool(selected_model_preset)
        target_profile = (
            _existing_platform_profile_for_provider(profiles, selected_provider)
            if managed_platform_profile
            else _existing_profile_for_provider(profiles, selected_provider)
        )
        auth_mode = (
            "platform_runtime"
            if managed_platform_profile
            else (
                _read_string(target_profile.get("auth_mode") if target_profile else None)
                or _read_string(catalog_entry.get("default_auth_mode"))
                or None
            )
        )
        credential_id = (
            ""
            if managed_platform_profile
            else _read_string(target_profile.get("credential_id") if target_profile else None) or None
        )
        if provider_profiles.provider_requires_credential(selected_provider, auth_mode) and not credential_id:
            raise HTTPException(
                status_code=400,
                detail="Provider profile must be connected before it can become the workspace AI route.",
            )
        if target_profile is None:
            target_profile = {
                "id": None,
                "provider": selected_provider,
                "label": _read_string(catalog_entry.get("label")) or selected_provider,
                "credential_id": credential_id,
                "auth_mode": auth_mode,
                "priority": 10,
                "enabled": True,
                "model": selected_model_override or catalog_entry.get("default_model"),
                "metadata": {"billing_source": "empyralis_credits"} if managed_platform_profile else {},
            }
            profiles.append(target_profile)

    _enforce_workspace_ai_route_update(
        workspace_id=normalized_workspace_id,
        current_user=current_user,
        selected_kind=selected_kind,
        selected_provider=selected_provider,
        selected_model_preset=selected_model_preset,
    )

    for profile in sorted(profiles, key=_profile_sort_key):
        provider_id = provider_profiles.normalize_provider_id(profile.get("provider"))
        if not provider_id:
            continue
        target_profile_id = _read_string(target_profile.get("id") if target_profile else None)
        is_selected = bool(
            selected_provider
            and (
                profile is target_profile
                or (target_profile_id and _read_string(profile.get("id")) == target_profile_id)
                or (target_profile is None and provider_id == selected_provider)
            )
        )
        metadata = {
            **_profile_metadata(profile),
            "chat_model_selection": "explicit" if is_selected else "default",
        }
        if is_selected and selected_model_preset:
            metadata["chat_model_tier"] = selected_model_preset
            if selected_kind == "empyralis_managed":
                metadata["billing_source"] = "empyralis_credits"
        elif is_selected:
            metadata.pop("chat_model_tier", None)

        selected_model = selected_model_override if is_selected and selected_model_override else _read_string(profile.get("model")) or None
        if is_selected and not selected_model:
            selected_model = _read_string(provider_profiles.provider_catalog_entry(provider_id).get("default_model")) or None
        selected_managed_profile = bool(is_selected and selected_kind == "empyralis_managed" and selected_model_preset)
        profile_priority = int(profile.get("priority") or (10 if is_selected else 100))
        profile_enabled = bool(profile.get("enabled", True))

        if not is_selected and _update_existing_provider_profile_route_metadata(
            profile,
            workspace_id=normalized_workspace_id,
            priority=profile_priority,
            enabled=profile_enabled,
            model=selected_model,
            metadata=metadata,
        ):
            continue

        await connectors_core.upsert_provider_profile(
            ProviderProfileUpsertRequest(
                id=_read_string(profile.get("id")) or None,
                provider=provider_id,
                label=_read_string(profile.get("label")) or provider_id,
                credential_id=None if selected_managed_profile else _read_string(profile.get("credential_id")) or None,
                auth_mode="platform_runtime" if selected_managed_profile else _read_string(profile.get("auth_mode")) or None,
                workspace_id=normalized_workspace_id,
                priority=profile_priority,
                enabled=profile_enabled,
                model=selected_model,
                metadata=metadata,
            )
        )

    if selected_kind == "empyralis_managed" and selected_provider is None:
        # No provider profile should be explicitly selected when the workspace uses
        # the managed default route. The read model falls back to Light/Workspace AI.
        pass

    # ── Persist the active provider to admin_defaults so the runtime resolver
    #     (_resolve_cloud_provider) sees the same selection.  "empyralis_managed"
    #     tiers clear the field (= use the credit-gated platform default); BYOK
    #     providers store the provider id.
    from server_modules import control_plane_repository as _cp_repo
    from server_modules import workspace_config_schema as _ws_config

    _next_provider = (
        ""
        if selected_kind == "empyralis_managed"
        else str(selected_provider or "").strip().lower()
    )
    _ws_record = await _cp_repo.get_workspace_by_id(normalized_workspace_id)
    if isinstance(_ws_record, dict):
        _ws_meta = dict(_ws_record.get("metadata") or {})
        _existing_defaults = _ws_config.workspace_admin_defaults_from_metadata(_ws_meta)
        _next_defaults = _ws_config.WorkspaceAdminDefaultsConfig.model_validate(
            {
                **_existing_defaults.model_dump(),
                "sage_ai_provider": _next_provider,
            }
        )
        await _cp_repo.update_workspace_admin_defaults_metadata(
            normalized_workspace_id,
            _ws_config.workspace_admin_defaults_envelope(_next_defaults),
        )

    return await build_workspace_ai_route_payload(normalized_workspace_id)
