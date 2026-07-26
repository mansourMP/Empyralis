from __future__ import annotations

from typing import Any, Dict, List, Optional

from server_modules import entitlements_service
from server_modules import marketplace_distribution_service
from server_modules import model_router
from server_modules import empyralis_model_tier_routing_service, provider_profiles

ANTHROPIC_MODEL_ALIASES = {
    "claude-3-7-sonnet-latest": "claude-3-7-sonnet-20250219",
}

MANUAL_MODEL_ID_PROVIDERS = {
    "azure_openai",
    "custom_openai_compatible",
    "groq",
    "openrouter",
    "xai",
}
BYOK_FIRST_PROVIDERS = {
    "azure_openai",
    "custom_openai_compatible",
    "groq",
    "openrouter",
}
LOCAL_OR_SUBSCRIPTION_PROVIDERS = {"ollama", "openai-codex", "claude_code_cli"}
PLATFORM_CREDIT_MODEL_ALLOWLIST = {
    ("deepseek", "deepseek-chat"),
    ("deepseek", "deepseek-v4-pro"),
    # Default platform-credits model for new fleet specialists as of this
    # session (deny-a-successful-tool rate: deepseek-chat 5/5, reasoner 1/5).
    ("deepseek", "deepseek-reasoner"),
}


def _canonical_surface(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token in {"sage", "main", "main_agent", "sage_direct_chat"}:
        return "sage"
    if token in {"studio", "specialist", "deployed_agent", "deployed_agent_channel"}:
        return "studio"
    if token in {"mini_app", "mini-app", "app", "mini_app_invoke"}:
        return "mini_app"
    return token


def _canonical_payer(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token in {"empyralis_credits", "empyralis", "platform_credits"}:
        return "platform_credits"
    if token in {"byok", "workspace_api_key", "workspace_connection"}:
        return "BYOK"
    if token in {"local", "local_model", "local_companion"}:
        return "local"
    if token in {"subscription", "subscription_passthrough", "codex_cli", "claude_code_cli"}:
        return "subscription_passthrough"
    return token


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

def _cached_model_records(provider_id: str, metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
    cached = metadata.get("cached_models")
    if not isinstance(cached, list):
        return []
    known_by_id = {
        str(item.get("id") or "").strip(): dict(item)
        for item in provider_profiles.provider_model_catalog(provider_id)
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    items: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for raw in cached:
        raw_record = dict(raw) if isinstance(raw, dict) else {"id": raw}
        model_id = str(raw_record.get("id") or "").strip()
        if not model_id or model_id in seen:
            continue
        if provider_id == "deepseek" and model_id not in known_by_id:
            # DeepSeek's direct API only accepts its first-party model IDs.
            # Stale workspace-cached aliases must not replace the static catalog.
            continue
        seen.add(model_id)
        record = dict(known_by_id.get(model_id) or {})
        pricing_projection = provider_profiles.pricing_registry_service.catalog_price_projection(provider_id, model_id)
        input_cost = raw_record.get("input_cost_per_1k_usd")
        if input_cost is None and raw_record.get("input_cost_per_million") is not None:
            try:
                input_cost = float(raw_record.get("input_cost_per_million") or 0.0) / 1000
            except Exception:
                input_cost = None
        output_cost = raw_record.get("output_cost_per_1k_usd")
        if output_cost is None and raw_record.get("output_cost_per_million") is not None:
            try:
                output_cost = float(raw_record.get("output_cost_per_million") or 0.0) / 1000
            except Exception:
                output_cost = None
        merged_capabilities = [
            str(label).strip()
            for label in list(record.get("capability_labels") or []) + list(raw_record.get("capability_labels") or [])
            if str(label).strip()
        ]
        if raw_record.get("supports_tools") and "Tools" not in merged_capabilities:
            merged_capabilities.append("Tools")
        if raw_record.get("supports_reasoning") and "Reasoning" not in merged_capabilities:
            merged_capabilities.append("Reasoning")
        record["id"] = model_id
        record["label"] = str(raw_record.get("label") or record.get("label") or model_id)
        record["provider"] = provider_id
        record["provider_id"] = provider_id
        record["context_window_tokens"] = raw_record.get("context_window_tokens") or record.get("context_window_tokens")
        record["input_cost_per_1k_usd"] = input_cost if input_cost is not None else record.get("input_cost_per_1k_usd", pricing_projection.get("input_cost_per_1k_usd"))
        record["output_cost_per_1k_usd"] = output_cost if output_cost is not None else record.get("output_cost_per_1k_usd", pricing_projection.get("output_cost_per_1k_usd"))
        try:
            record["input_cost_per_million"] = float(record.get("input_cost_per_1k_usd") or 0.0) * 1000
            record["output_cost_per_million"] = float(record.get("output_cost_per_1k_usd") or 0.0) * 1000
        except Exception:
            pass
        record["pricing_known"] = bool(raw_record.get("pricing_known") or record.get("pricing_known") or pricing_projection.get("pricing_known"))
        record["pricing_source"] = raw_record.get("pricing_source") or record.get("pricing_source") or pricing_projection.get("pricing_source")
        record["supports_tools"] = bool(raw_record.get("supports_tools") or record.get("supports_tools"))
        record["supports_vision"] = bool(raw_record.get("supports_vision") or record.get("supports_vision"))
        record["supports_json"] = bool(raw_record.get("supports_json") or record.get("supports_json"))
        record["supports_reasoning"] = bool(raw_record.get("supports_reasoning") or record.get("supports_reasoning"))
        record["capability_labels"] = list(dict.fromkeys(merged_capabilities))
        record["lifecycle"] = raw_record.get("lifecycle") or record.get("lifecycle")
        record["source"] = raw_record.get("source") or "workspace_cached_models"
        record["fetched_at"] = raw_record.get("fetched_at") or metadata.get("cached_models_synced_at")
        if "raw" in raw_record:
            record["raw"] = raw_record.get("raw")
        items.append(record)
    return items


def cached_provider_model_ids(*, workspace_id: Any = None, provider: Any = None) -> List[str]:
    provider_id = provider_profiles.normalize_provider_id(provider)
    if not provider_id:
        return []
    try:
        connection_truth = provider_profiles.build_workspace_provider_connection_truth(workspace_id)
    except Exception:
        return []
    for item in connection_truth.get("providers", []):
        if not isinstance(item, dict):
            continue
        if provider_profiles.normalize_provider_id(item.get("id")) != provider_id:
            continue
        metadata = dict(item.get("profile_metadata") or {}) if isinstance(item.get("profile_metadata"), dict) else {}
        return [
            record["id"]
            for record in _cached_model_records(provider_id, metadata)
            if str(record.get("id") or "").strip()
        ]
    return []


def model_route_policy(
    *,
    provider: Any,
    model: Any,
) -> Dict[str, Any]:
    provider_id = provider_profiles.normalize_provider_id(provider)
    model_id = _normalize_model_token(provider_id, model)
    policy = provider_profiles.provider_model_policy(provider_id, model_id)
    pricing = provider_profiles.pricing_registry_service.catalog_price_projection(provider_id, model_id)
    return {
        "provider": provider_id,
        "model": model_id,
        "enabled": policy.get("enabled", True),
        "allowed_surfaces": list(policy.get("allowed_surfaces") or []),
        "allowed_payers": list(policy.get("allowed_payers") or []),
        "platform_paid_allowed": bool(
            policy.get(
                "platform_paid_allowed",
                provider_id not in BYOK_FIRST_PROVIDERS
                and provider_id not in LOCAL_OR_SUBSCRIPTION_PROVIDERS
                and bool(pricing.get("pricing_known")),
            )
        ),
        "pricing_known": bool(
            policy.get("pricing_known")
            if policy.get("pricing_known") is not None
            else pricing.get("pricing_known")
        ),
        "pricing_source": policy.get("pricing_source") or pricing.get("pricing_source"),
        "pricing_version": policy.get("pricing_version") or pricing.get("pricing_registry_version"),
        "fallback_allowed": bool(policy.get("fallback_allowed", False)),
    }


def assert_model_route_policy(
    *,
    provider: Any,
    model: Any,
    surface: Any = None,
    payer: Any = None,
) -> Dict[str, Any]:
    policy = model_route_policy(provider=provider, model=model)
    provider_id = policy["provider"]
    model_id = policy["model"]
    if policy.get("enabled") is False:
        raise ValueError(f"Model '{model_id}' is disabled for provider '{provider_id}'.")
    surface_token = _canonical_surface(surface)
    if surface_token:
        allowed_surfaces = {_canonical_surface(item) for item in policy.get("allowed_surfaces") or [] if str(item or "").strip()}
        if allowed_surfaces and surface_token not in allowed_surfaces:
            raise ValueError(f"Model '{provider_id}:{model_id}' is not allowed for surface '{surface_token}'.")
    payer_token = _canonical_payer(payer)
    if payer_token:
        allowed_payers = {_canonical_payer(item) for item in policy.get("allowed_payers") or [] if str(item or "").strip()}
        if allowed_payers and payer_token not in allowed_payers:
            raise ValueError(f"Model '{provider_id}:{model_id}' is not allowed for payer '{payer_token}'.")
        if payer_token == "platform_credits":
            if provider_id in BYOK_FIRST_PROVIDERS:
                raise ValueError(f"{provider_id} is BYOK/workspace-key only and cannot use Empyralis credits.")
            if (provider_id, model_id) not in PLATFORM_CREDIT_MODEL_ALLOWLIST:
                raise ValueError(
                    "Empyralis credits only support Light (DeepSeek Chat), Pro (DeepSeek V4 Pro), "
                    "and DeepSeek Reasoner."
                )
            if not policy.get("platform_paid_allowed"):
                raise ValueError(f"Model '{provider_id}:{model_id}' is not approved for Empyralis credits.")
            if not policy.get("pricing_known"):
                raise ValueError(f"Empyralis-credit usage requires known pricing for {provider_id}:{model_id}.")
    return policy


def _normalize_model_token(provider_id: str, model_id: Any) -> str:
    token = str(model_id or "").strip()
    if not token:
        return ""
    if provider_id == "anthropic" and token.startswith("anthropic/"):
        token = token.split("/", 1)[1]
    if provider_id == "anthropic":
        return ANTHROPIC_MODEL_ALIASES.get(token, token)
    if provider_id == "gemini" and token.startswith("gemini/"):
        return token.split("/", 1)[1]
    if provider_id == "vertex" and token.startswith("vertex_ai/"):
        return token.split("/", 1)[1]
    if provider_id in {"qwen", "deepseek", "mistral", "ollama", "ollama_cloud", "groq", "xai", "azure_openai", "custom_openai_compatible"} and "/" in token:
        provider_token, model_token = token.split("/", 1)
        if provider_profiles.normalize_provider_id(provider_token) == provider_id:
            return model_token.strip()
    return token


def resolve_provider_model_selection(
    *,
    provider: Any = None,
    model: Any = None,
    existing_provider: Any = None,
    existing_model: Any = None,
    cached_models: Any = None,
    surface: Any = None,
    payer: Any = None,
) -> Dict[str, Optional[str]]:
    raw_provider = provider_profiles.normalize_provider_id(provider) if str(provider or "").strip() else ""
    raw_model = str(model or "").strip()
    normalized_existing_provider = (
        provider_profiles.normalize_provider_id(existing_provider)
        if str(existing_provider or "").strip()
        else ""
    )
    normalized_existing_model = str(existing_model or "").strip()

    if not raw_provider and raw_model:
        raw_provider = provider_profiles.normalize_provider_id(model_router.infer_provider(raw_model))
    if not raw_provider:
        raw_provider = normalized_existing_provider
    if not raw_provider:
        return {"provider": None, "model": None}

    provider_entry = provider_profiles.provider_catalog_entry(raw_provider)
    if not provider_entry or bool(provider_entry.get("hidden")):
        raise ValueError(f"Unsupported provider '{provider}'.")

    normalized_model = _normalize_model_token(raw_provider, raw_model)
    if not normalized_model:
        if raw_provider == normalized_existing_provider:
            normalized_model = _normalize_model_token(raw_provider, normalized_existing_model)
        if not normalized_model:
            normalized_model = str(provider_entry.get("default_model") or "").strip()

    model_catalog = provider_profiles.provider_model_catalog(raw_provider)
    supported_models = {str(item.get("id") or "").strip() for item in model_catalog if str(item.get("id") or "").strip()}
    if isinstance(cached_models, list) and raw_provider != "deepseek":
        supported_models.update(
            _normalize_model_token(raw_provider, cached_model.get("id") if isinstance(cached_model, dict) else cached_model)
            for cached_model in cached_models
            if _normalize_model_token(raw_provider, cached_model.get("id") if isinstance(cached_model, dict) else cached_model)
        )
    if normalized_model:
        route_policy = model_route_policy(provider=raw_provider, model=normalized_model)
        if route_policy.get("enabled") is False:
            raise ValueError(f"Model '{normalized_model}' is disabled for provider '{raw_provider}'.")
    if normalized_model and supported_models and normalized_model not in supported_models and raw_provider not in MANUAL_MODEL_ID_PROVIDERS:
        raise ValueError(f"Model '{normalized_model}' is not supported for provider '{raw_provider}'.")
    if normalized_model:
        assert_model_route_policy(
            provider=raw_provider,
            model=normalized_model,
            surface=surface,
            payer=payer,
        )

    return {
        "provider": raw_provider or None,
        "model": normalized_model or None,
    }


def resolve_empyralis_model_tier_selection(
    *,
    public_tier: Any,
    include_internal_route: bool = False,
) -> Dict[str, Any]:
    return empyralis_model_tier_routing_service.resolve_model_tier_route(
        public_tier,
        include_internal_route=include_internal_route,
    )


def _provider_catalog_projection(item: Dict[str, Any]) -> Dict[str, Any]:
    provider_id = str(item.get("id") or "").strip()
    governance = provider_profiles.provider_governance_entry(provider_id)
    catalog_entry = provider_profiles.provider_catalog_entry(provider_id)
    profile_metadata = dict(item.get("profile_metadata") or {}) if isinstance(item.get("profile_metadata"), dict) else {}
    cached_models = _cached_model_records(provider_id, profile_metadata)
    models = cached_models or provider_profiles.provider_model_catalog(provider_id)
    provider_scopes = [
        str(scope).strip()
        for scope in list(catalog_entry.get("provider_scopes") or [])
        if str(scope).strip()
    ]
    return {
        **dict(item),
        "hidden": bool(catalog_entry.get("hidden")),
        "provider_scopes": provider_scopes,
        "sage_visible": "sage_personal" in provider_scopes and not bool(catalog_entry.get("hidden")),
        "studio_visible": "studio_safe" in provider_scopes and not bool(catalog_entry.get("hidden")),
        "local_only": "local_only" in provider_scopes,
        "privacy_posture": governance.get("privacy_posture"),
        "privacy_posture_summary": governance.get("privacy_posture"),
        "jurisdiction": governance.get("jurisdiction"),
        "residency": governance.get("residency"),
        "residency_caveat": governance.get("residency"),
        "enterprise_risk_note": governance.get("enterprise_risk_note"),
        "capability_labels": list(governance.get("capability_labels") or []),
        "local_self_hosted_compatible": bool(governance.get("local_self_hosted_compatible")),
        "models": models,
        "models_source": "workspace_cached_models" if cached_models else "static_catalog",
        "models_synced_at": profile_metadata.get("cached_models_synced_at"),
        "models_expires_at": profile_metadata.get("cached_models_expires_at"),
        "models_error": profile_metadata.get("cached_models_error"),
    }


def _apply_hosted_ai_policy(item: Dict[str, Any], *, hosted_access_state: Dict[str, Any]) -> Dict[str, Any]:
    projected = dict(item)
    policy = str(hosted_access_state.get("policy") or "owner_opt_in").strip().lower() or "owner_opt_in"
    reason = str(hosted_access_state.get("reason") or "").strip().lower() or None
    hosted_allowed = bool(hosted_access_state.get("allowed"))
    workspace_connected = bool(projected.get("workspace_connected"))
    requires_hosted_lane = (
        str(projected.get("credential_plane") or "").strip().lower() == "platform_runtime"
        and not workspace_connected
    )
    projected["hosted_ai_enabled"] = hosted_allowed
    projected["hosted_sage_ai_policy"] = policy
    projected["hosted_sage_ai_monthly_cap_usd"] = float(hosted_access_state.get("monthly_cap_usd") or 0.0)
    projected["hosted_sage_ai_monthly_cost_usd"] = float(hosted_access_state.get("monthly_cost_usd") or 0.0)
    projected["hosted_sage_ai_monthly_remaining_usd"] = float(hosted_access_state.get("monthly_remaining_usd") or 0.0)
    projected["hosted_sage_ai_reason"] = reason
    projected["platform_runtime_allowed"] = bool(hosted_allowed or not requires_hosted_lane)
    if projected["platform_runtime_allowed"]:
        return projected

    if reason == "owner_approval_required":
        issue_code = "hosted_ai_owner_approval_required"
        detail = "Hosted AI is not enabled for this workspace. Connect your own provider key or switch to a local runtime."
    elif reason == "cap_reached":
        issue_code = "hosted_ai_cap_reached"
        detail = (
            "Hosted AI monthly cap is reached for this workspace. "
            "Connect your own provider key, switch to local runtime, or raise the cap."
        )
    else:
        issue_code = "hosted_ai_policy_disabled"
        detail = (
            "Hosted AI is disabled for this workspace. "
            "Connect your own provider key or use a local runtime instead."
        )
    connection_state = str(projected.get("connection_state") or "").strip().lower()
    connection_credential_sources = (
        list(projected.get("connection_credential_sources") or [])
        if isinstance(projected.get("connection_credential_sources"), list)
        else []
    )
    issues = list(projected.get("issues") or []) if isinstance(projected.get("issues"), list) else []
    issues.append({"code": issue_code, "detail": detail})
    projected.update(
        {
            "state": connection_state or "setup_required",
            "usable": False,
            "configured": connection_state in {"active", "configured", "degraded", "unavailable"},
            "active": False,
            "issues": issues,
            "issue_code": issue_code,
            "issue": detail,
            "state_detail": detail,
            "active_source": projected.get("connection_active_source"),
            "credential_sources": connection_credential_sources,
            "runtime_state": "restricted",
            "runtime_state_detail": detail,
            "runtime_active_source": None,
            "runtime_credential_sources": [],
        }
    )
    return projected


def _merge_runtime_truth(
    connection_item: Dict[str, Any],
    runtime_item: Dict[str, Any] | None,
) -> Dict[str, Any]:
    connection_state = str(connection_item.get("state") or "").strip() or None
    connection_state_detail = connection_item.get("state_detail")
    connection_active_source = connection_item.get("active_source")
    connection_credential_sources = list(connection_item.get("credential_sources") or []) if isinstance(connection_item.get("credential_sources"), list) else []
    workspace_connected = connection_state in {"active", "configured", "degraded"}
    if not isinstance(runtime_item, dict):
        merged = {
            **dict(connection_item),
            "connection_state": connection_state,
            "connection_state_detail": connection_state_detail,
            "connection_active_source": connection_active_source,
            "connection_credential_sources": connection_credential_sources,
            "workspace_connected": workspace_connected,
            "runtime_state": connection_state,
            "runtime_state_detail": connection_state_detail,
            "runtime_active_source": connection_active_source,
            "runtime_credential_sources": connection_credential_sources,
        }
        merged.update(
            _credential_plane_metadata(
                source=connection_active_source,
                identity_owner=connection_item.get("identity_owner"),
            )
        )
        return merged
    merged = {
        **dict(connection_item),
        "state": runtime_item.get("state"),
        "usable": bool(runtime_item.get("usable")),
        "configured": bool(runtime_item.get("configured")),
        "active": bool(runtime_item.get("active")),
        "issues": list(runtime_item.get("issues") or []) if isinstance(runtime_item.get("issues"), list) else [],
        "credential_sources": list(runtime_item.get("credential_sources") or []) if isinstance(runtime_item.get("credential_sources"), list) else [],
        "backpressure": bool(runtime_item.get("backpressure")),
        "retry_after_seconds": runtime_item.get("retry_after_seconds"),
        "failure_class": runtime_item.get("failure_class"),
        "issue_code": runtime_item.get("issue_code"),
        "issue": runtime_item.get("issue"),
        "state_detail": runtime_item.get("state_detail"),
        "active_source": runtime_item.get("active_source"),
        "connection_state": connection_state,
        "connection_state_detail": connection_state_detail,
        "connection_active_source": connection_active_source,
        "connection_credential_sources": connection_credential_sources,
        "workspace_connected": workspace_connected,
        "runtime_state": runtime_item.get("state"),
        "runtime_state_detail": runtime_item.get("state_detail"),
        "runtime_active_source": runtime_item.get("active_source"),
        "runtime_credential_sources": list(runtime_item.get("credential_sources") or []) if isinstance(runtime_item.get("credential_sources"), list) else [],
        "profile_metadata": (
            dict(runtime_item.get("profile_metadata"))
            if isinstance(runtime_item.get("profile_metadata"), dict)
            else dict(connection_item.get("profile_metadata") or {})
            if isinstance(connection_item.get("profile_metadata"), dict)
            else {}
        ),
    }
    runtime_identity_source = str(runtime_item.get("active_source") or "").strip().lower()
    if runtime_identity_source and runtime_identity_source not in {"profile", "workspace_profile"}:
        for key in ("identity_owner", "identity_owner_label", "identity_boundary_note", "machine_bound"):
            if key in runtime_item:
                merged[key] = runtime_item.get(key)
    if runtime_item.get("default_model"):
        merged["default_model"] = runtime_item.get("default_model")
    merged.update(
        _credential_plane_metadata(
            source=runtime_item.get("active_source") or connection_active_source,
            identity_owner=merged.get("identity_owner"),
        )
    )
    return merged


def _marketplace_provider_catalog_projection(item: Dict[str, Any]) -> Dict[str, Any]:
    package = item.get("package") if isinstance(item.get("package"), dict) else {}
    publisher = item.get("publisher") if isinstance(item.get("publisher"), dict) else {}
    billing = item.get("billing") if isinstance(item.get("billing"), dict) else {}
    runtime_truth = item.get("runtime_truth") if isinstance(item.get("runtime_truth"), dict) else {}
    analytics = item.get("analytics") if isinstance(item.get("analytics"), dict) else {}
    models = package.get("models") if isinstance(package.get("models"), list) else []
    return {
        "id": str(package.get("provider_id") or item.get("package_id") or "").strip(),
        "label": str(item.get("label") or package.get("provider_id") or item.get("package_id") or "").strip(),
        "state": str(runtime_truth.get("health_state") or item.get("health_state") or "setup_required").strip() or "setup_required",
        "usable": False,
        "active": False,
        "configured": False,
        "default_model": str(package.get("default_model") or "").strip() or None,
        "privacy_posture": str(package.get("privacy_posture") or "").strip() or None,
        "privacy_posture_summary": str(package.get("privacy_posture") or "").strip() or None,
        "jurisdiction": str(package.get("jurisdiction") or "").strip() or None,
        "residency": str(package.get("residency") or "").strip() or None,
        "residency_caveat": str(package.get("residency") or "").strip() or None,
        "enterprise_risk_note": str(package.get("enterprise_risk_note") or "").strip() or None,
        "capability_labels": [
            *[str(label).strip() for label in package.get("capability_labels", []) if str(label).strip()],
            "Marketplace provider",
        ],
        "local_self_hosted_compatible": False,
        "models": models,
        "distribution_origin": "third_party_marketplace",
        "verification_status": str(item.get("verification_status") or "unverified").strip() or "unverified",
        "review_state": str(item.get("review_state") or "pending").strip() or "pending",
        "health_state": str(item.get("health_state") or "setup_required").strip() or "setup_required",
        "publisher": {
            "publisher_id": str(publisher.get("publisher_id") or "").strip() or None,
            "label": str(publisher.get("label") or "").strip() or None,
            "website": str(publisher.get("website") or "").strip() or None,
        },
        "billing_hooks": {
            "monetization_kind": str(billing.get("monetization_kind") or "free").strip() or "free",
            "revenue_share_bps": int(billing.get("revenue_share_bps") or 0),
            "billing_product_id": str(billing.get("billing_product_id") or "").strip() or None,
            "accounting_hook": billing.get("accounting_hook") if isinstance(billing.get("accounting_hook"), dict) else {},
        },
        "analytics": {
            "install_count": int(analytics.get("install_count") or 0),
            "runtime_event_count": int(analytics.get("runtime_event_count") or 0),
            "last_install_at": analytics.get("last_install_at"),
            "last_runtime_at": analytics.get("last_runtime_at"),
        },
    }


async def list_workspace_provider_catalog(
    *,
    workspace_id: Optional[str],
) -> Dict[str, Any]:
    connection_truth = provider_profiles.build_workspace_provider_connection_truth(workspace_id)
    runtime_truth = provider_profiles.build_provider_runtime_truth(workspace_id)
    runtime_by_id = {
        str(item.get("id") or "").strip(): item
        for item in runtime_truth.get("providers", [])
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    normalized_workspace_id = str(connection_truth.get("workspace_id") or workspace_id or "default").strip() or "default"
    hosted_access_state = entitlements_service.hosted_sage_ai_access_state_for_workspace_id(
        workspace_id=normalized_workspace_id,
    )
    model_tier_policy = entitlements_service.chat_model_tier_policy_for_workspace_id(
        workspace_id=normalized_workspace_id,
    )
    providers = [
        _provider_catalog_projection(
            _apply_hosted_ai_policy(
                _merge_runtime_truth(item, runtime_by_id.get(str(item.get("id") or "").strip())),
                hosted_access_state=hosted_access_state,
            )
        )
        for item in connection_truth.get("providers", [])
        if isinstance(item, dict)
    ]
    for item in marketplace_distribution_service.installed_provider_marketplace_packages(normalized_workspace_id):
        providers.append(_marketplace_provider_catalog_projection(item))
    summary = dict(runtime_truth.get("summary") or {}) if isinstance(runtime_truth.get("summary"), dict) else {}
    summary["provider_total"] = len(providers)
    return {
        "workspace_id": normalized_workspace_id,
        "hosted_ai_enabled": bool(hosted_access_state.get("allowed")),
        "hosted_sage_ai": hosted_access_state,
        "model_tier_policy": model_tier_policy,
        "summary": summary,
        "providers": providers,
    }
