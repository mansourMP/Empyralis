from __future__ import annotations

from typing import Any, Dict, Optional

from server_modules import empyralis_model_tier_contract, provider_profiles


EMPYRALIS_PROVIDER_ALIAS = "empyralis"
LEGACY_LIGHT_MODEL_MARKERS = ("flash", "lite", "mini", "nano", "haiku", "small")
LEGACY_MAX_MODEL_MARKERS = ("reasoner", "reasoning", "max", "o1", "o3", "opus", "gpt-5.5")


def is_hosted_empyralis_tier(value: Any) -> bool:
    tier = empyralis_model_tier_contract.normalize_model_tier(value, fallback="")
    return tier in empyralis_model_tier_contract.EMPYRALIS_HOSTED_TIERS


def _catalog_model_ids(provider: str) -> set[str]:
    return {
        str(item.get("id") or "").strip()
        for item in provider_profiles.provider_model_catalog(provider)
        if str(item.get("id") or "").strip()
    }


def _assert_route_supported(provider: Optional[str], model: Optional[str]) -> None:
    provider_token = str(provider or "").strip()
    model_token = str(model or "").strip()
    if not provider_token or not model_token:
        raise ValueError("Empyralis hosted tiers must resolve to an internal provider/model.")
    if not provider_profiles.provider_catalog_entry(provider_token):
        raise ValueError(f"Unsupported Empyralis tier provider '{provider_token}'.")
    if model_token not in _catalog_model_ids(provider_token):
        raise ValueError(
            f"Empyralis tier model '{model_token}' is not registered for provider '{provider_token}'."
        )


def resolve_model_tier_route(
    tier: Any,
    *,
    include_internal_route: bool = False,
) -> Dict[str, Any]:
    contract = empyralis_model_tier_contract.model_tier_contract(tier)
    if contract.public_tier in empyralis_model_tier_contract.EMPYRALIS_HOSTED_TIERS:
        _assert_route_supported(contract.internal_provider, contract.internal_model)
    payload = contract.to_dict(include_internal_route=include_internal_route)
    fallback_contract = (
        empyralis_model_tier_contract.model_tier_contract(contract.fallback_tier)
        if contract.fallback_tier
        else None
    )
    if fallback_contract and fallback_contract.public_tier in empyralis_model_tier_contract.EMPYRALIS_HOSTED_TIERS:
        _assert_route_supported(fallback_contract.internal_provider, fallback_contract.internal_model)
    payload["fallback"] = (
        fallback_contract.to_dict(include_internal_route=include_internal_route)
        if fallback_contract
        else None
    )
    payload["admin_debug"] = (
        {
            "internal_provider": contract.internal_provider,
            "internal_model": contract.internal_model,
            "fallback_provider": fallback_contract.internal_provider if fallback_contract else None,
            "fallback_model": fallback_contract.internal_model if fallback_contract else None,
        }
        if include_internal_route
        else None
    )
    return payload


def resolve_backend_provider_model_for_tier(tier: Any) -> Dict[str, Any]:
    contract = empyralis_model_tier_contract.model_tier_contract(tier)
    _assert_route_supported(contract.internal_provider, contract.internal_model)
    fallback_contract = (
        empyralis_model_tier_contract.model_tier_contract(contract.fallback_tier)
        if contract.fallback_tier
        else None
    )
    if fallback_contract and fallback_contract.public_tier in empyralis_model_tier_contract.EMPYRALIS_HOSTED_TIERS:
        _assert_route_supported(fallback_contract.internal_provider, fallback_contract.internal_model)
    return {
        "public_tier": contract.public_tier,
        "public_label": contract.public_label,
        "provider": contract.internal_provider,
        "model": contract.internal_model,
        "reasoning_effort": contract.reasoning_effort,
        "thinking_mode": contract.thinking_mode,
        "billing_source": contract.billing_source,
        "credit_multiplier": contract.credit_multiplier,
        "fallback_tier": contract.fallback_tier,
        "fallback_provider": fallback_contract.internal_provider if fallback_contract else None,
        "fallback_model": fallback_contract.internal_model if fallback_contract else None,
        "fallback_reasoning_effort": fallback_contract.reasoning_effort if fallback_contract else None,
    }


def resolve_requested_empyralis_tier(
    *,
    requested_provider: Any = None,
    requested_model: Any = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    metadata_payload = metadata if isinstance(metadata, dict) else {}
    metadata_tier = (
        metadata_payload.get("public_tier")
        or metadata_payload.get("model_tier")
        or metadata_payload.get("empyralis_model_tier")
    )
    provider_token = str(requested_provider or "").strip().lower()
    model_token = str(requested_model or "").strip().lower().replace("-", "_")
    tier_token = str(metadata_tier or "").strip().lower().replace("-", "_")
    hosted_tiers = set(empyralis_model_tier_contract.EMPYRALIS_HOSTED_TIERS)
    legacy_hosted_tiers = set(getattr(empyralis_model_tier_contract, "LEGACY_EMPYRALIS_HOSTED_TIERS", ()))
    if tier_token in hosted_tiers or tier_token in legacy_hosted_tiers:
        return resolve_backend_provider_model_for_tier(tier_token)
    if provider_token == EMPYRALIS_PROVIDER_ALIAS and (model_token in hosted_tiers or model_token in legacy_hosted_tiers):
        return resolve_backend_provider_model_for_tier(model_token)
    if not provider_token and (model_token in hosted_tiers or model_token in legacy_hosted_tiers):
        return resolve_backend_provider_model_for_tier(model_token)
    return None


def _metadata_text(metadata: Dict[str, Any], key: str) -> str:
    return str(metadata.get(key) or "").strip().lower()


def _model_matches_any(model_token: str, markers: tuple[str, ...]) -> bool:
    if not model_token:
        return False
    return any(marker in model_token for marker in markers)


def _is_local_route(provider_token: str, metadata_payload: Dict[str, Any]) -> bool:
    if provider_token == "ollama":
        return True
    credential_plane = _metadata_text(metadata_payload, "credential_plane")
    credential_owner_kind = _metadata_text(metadata_payload, "credential_owner_kind")
    auth_mode = _metadata_text(metadata_payload, "auth_mode")
    source = _metadata_text(metadata_payload, "source")
    return (
        credential_plane == "local_runtime"
        or credential_owner_kind in {"local_machine", "machine_owner"}
        or auth_mode in {"local_cli", "local_runtime"}
        or source.startswith("local")
    )


def _is_user_api_key_route(provider_token: str, metadata_payload: Dict[str, Any]) -> bool:
    billing_source = _metadata_text(metadata_payload, "billing_source")
    credential_plane = _metadata_text(metadata_payload, "credential_plane")
    credential_owner_kind = _metadata_text(metadata_payload, "credential_owner_kind")
    if billing_source in {"user_api_key", "user_ai_account"}:
        return True
    if credential_plane == "workspace_connection":
        return True
    if credential_owner_kind == "workspace_byok":
        return True
    return provider_token not in {"", EMPYRALIS_PROVIDER_ALIAS, "deepseek", "ollama"}


def infer_migrated_public_tier_from_legacy_selection(
    *,
    requested_provider: Any = None,
    requested_model: Any = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    metadata_payload = metadata if isinstance(metadata, dict) else {}
    explicit_tier = (
        metadata_payload.get("public_tier")
        or metadata_payload.get("model_tier")
        or metadata_payload.get("empyralis_model_tier")
    )
    if str(explicit_tier or "").strip():
        return empyralis_model_tier_contract.normalize_model_tier(explicit_tier, fallback="pro")

    provider_token = str(requested_provider or "").strip().lower().replace("-", "_")
    model_token = str(requested_model or "").strip().lower().replace("-", "_")

    if _is_local_route(provider_token, metadata_payload):
        return "local_ai"

    if _is_user_api_key_route(provider_token, metadata_payload):
        return "my_api_key"

    if provider_token in {"", EMPYRALIS_PROVIDER_ALIAS, "deepseek"}:
        if model_token in {"light", "pro", "max"}:
            return empyralis_model_tier_contract.normalize_model_tier(model_token, fallback="pro")
        if not model_token or model_token == "deepseek_chat":
            return "pro"
        if model_token == "deepseek_v4_flash" or _model_matches_any(model_token, LEGACY_LIGHT_MODEL_MARKERS):
            return "light"
        if model_token == "deepseek_reasoner" or _model_matches_any(model_token, LEGACY_MAX_MODEL_MARKERS):
            return "pro"
        if model_token in {"deepseek_v4_pro"}:
            return "pro"
        return "pro"
    return None
