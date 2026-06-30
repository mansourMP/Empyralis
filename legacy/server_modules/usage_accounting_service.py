from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
import uuid

from server_modules import billing_credit_config
from server_modules import pricing_registry_service


@dataclass(frozen=True)
class UsageAccountingRecord:
    usage_record_id: str
    run_id: Optional[str]
    tenant_id: Optional[str]
    workspace_id: Optional[str]
    deployed_agent_id: Optional[str]
    surface: str
    source_surface: str
    requested_provider: Optional[str]
    effective_provider: Optional[str]
    requested_model: Optional[str]
    effective_model: Optional[str]
    input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated_cost_usd: Optional[float]
    pricing_known: bool
    pricing_registry_version: str
    pricing_source: Optional[str]
    estimation_mode: str
    completed_at: str
    metadata: Dict[str, Any]
    user_id: Optional[str] = None
    app_id: Optional[str] = None
    thread_id: Optional[str] = None
    agent_id: Optional[str] = None
    payer: Optional[str] = None
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    visible_output_tokens: int = 0
    reasoning_tokens: int = 0
    thinking_tokens: int = 0
    provider_cost_usd: Optional[float] = None
    retail_credits_charged: Optional[float] = None
    success: bool = True
    error_code: Optional[str] = None
    fallback_attempt: bool = False
    created_at: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_optional_text(value: Any) -> Optional[str]:
    token = _normalize_text(value)
    return token or None


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _safe_float(value: Any) -> Optional[float]:
    try:
        return round(float(value), 6)
    except Exception:
        return None


def _nonnegative_int(value: Any) -> int:
    return max(0, _safe_int(value, 0))


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


def masked_cost_band(cost_usd: Optional[float]) -> str:
    if cost_usd is None:
        return "Unknown"
    if cost_usd < 0.001:
        return "< $0.001"
    if cost_usd < 0.01:
        return "$0.001 - $0.01"
    if cost_usd < 0.05:
        return "$0.01 - $0.05"
    if cost_usd < 0.10:
        return "$0.05 - $0.10"
    return ">= $0.10"


def _derive_source_surface(
    *,
    explicit: Optional[str],
    context: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    token = _normalize_text(explicit).lower()
    if token:
        return token
    payload = _coerce_dict(context)
    meta = _coerce_dict(metadata) or _coerce_dict(payload.get("metadata"))
    if _normalize_optional_text(meta.get("deployed_agent_id")):
        return "deployed_agent_channel"
    if _normalize_optional_text(payload.get("workflow_id")):
        return "workflow_graph"
    return "durable_run"


def _derive_surface(value: Any) -> str:
    token = _normalize_text(value).lower()
    if token in {"sage", "studio", "mini_app"}:
        return token
    if token in {"sage_direct_chat", "direct_chat", "chat_direct"}:
        return "sage"
    if token in {"deployed_agent_channel", "studio_agent", "studio_test", "workflow_graph"}:
        return "studio"
    if token in {"mini_app_invoke", "application_activity"}:
        return "mini_app"
    return token or "durable_run"


def build_usage_accounting_record(
    *,
    run_id: Optional[str],
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    deployed_agent_id: Optional[str] = None,
    surface: Optional[str] = None,
    source_surface: Optional[str] = None,
    requested_provider: Optional[str] = None,
    effective_provider: Optional[str] = None,
    requested_model: Optional[str] = None,
    effective_model: Optional[str] = None,
    input_tokens: int,
    output_tokens: int,
    total_tokens: Optional[int] = None,
    cached_input_tokens: int = 0,
    cache_write_tokens: int = 0,
    cache_read_tokens: int = 0,
    visible_output_tokens: Optional[int] = None,
    reasoning_tokens: int = 0,
    thinking_tokens: int = 0,
    estimated_cost_usd: Optional[float] = None,
    provider_cost_usd: Optional[float] = None,
    retail_credits_charged: Optional[float] = None,
    payer: Optional[str] = None,
    user_id: Optional[str] = None,
    app_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    success: bool = True,
    error_code: Optional[str] = None,
    fallback_attempt: bool = False,
    estimation_mode: str = "reconstructed",
    completed_at: Optional[str] = None,
    created_at: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    allow_provider_fallback: bool = False,
    require_known_pricing: bool = False,
) -> Dict[str, Any]:
    provider = pricing_registry_service.normalize_provider_id(
        effective_provider or requested_provider or "local_companion"
    ) or "local_companion"
    model = pricing_registry_service.normalize_model_id(
        provider,
        effective_model or requested_model or "unknown-model",
    ) or "unknown-model"
    normalized_requested_provider = pricing_registry_service.normalize_provider_id(
        requested_provider or provider
    ) or None
    normalized_requested_model = _normalize_optional_text(requested_model or model)
    input_value = _nonnegative_int(input_tokens)
    output_value = _nonnegative_int(output_tokens)
    cached_value = _nonnegative_int(cached_input_tokens)
    cache_write_value = _nonnegative_int(cache_write_tokens)
    cache_read_value = _nonnegative_int(cache_read_tokens)
    visible_output_value = _nonnegative_int(output_value if visible_output_tokens is None else visible_output_tokens)
    reasoning_value = _nonnegative_int(reasoning_tokens)
    thinking_value = _nonnegative_int(thinking_tokens)
    cached_charge_value = cache_read_value or cached_value
    token_floor = input_value + cache_write_value + cached_charge_value + visible_output_value + reasoning_value + thinking_value
    total_value = max(token_floor, _safe_int(total_tokens, token_floor))
    pricing_entry = pricing_registry_service.lookup_pricing_entry(provider, model)
    pricing_source = None
    pricing_known = False
    resolved_cost = _safe_float(provider_cost_usd)
    if resolved_cost is None:
        resolved_cost = _safe_float(estimated_cost_usd)
    if pricing_entry is not None:
        pricing_source = pricing_entry.rate.source
        if pricing_entry.rate.input_usd_per_million is not None and pricing_entry.rate.output_usd_per_million is not None:
            pricing_known = True
            if resolved_cost is None:
                resolved_cost = pricing_registry_service.estimate_cost_usd(
                    provider,
                    model,
                    input_value,
                    visible_output_value,
                    cached_input_tokens=0 if cache_read_value else cached_value,
                    cache_write_tokens=cache_write_value,
                    cache_read_tokens=cache_read_value,
                    reasoning_tokens=reasoning_value,
                    thinking_tokens=thinking_value,
                )
    if require_known_pricing and (pricing_entry is None or resolved_cost is None or not pricing_known):
        raise ValueError(f"Known pricing is required for platform-paid usage: {provider}:{model}.")
    if resolved_cost is None and allow_provider_fallback:
        fallback = pricing_registry_service.provider_fallback_rate_per_1k(provider)
        fallback_source = str(fallback.get("source") or "").strip()
        fallback_known = fallback_source and fallback_source != "pricing_registry_fallback"
        if fallback_known:
            resolved_cost = round(
                ((input_value / 1000.0) * float(fallback.get("input") or 0.0))
                + ((output_value / 1000.0) * float(fallback.get("output") or 0.0)),
                6,
            )
            pricing_source = fallback_source or pricing_source
            pricing_known = False
    resolved_source_surface = _derive_source_surface(explicit=source_surface or surface)
    resolved_surface = _derive_surface(surface or resolved_source_surface)
    metadata_payload = _coerce_dict(metadata)
    payer_token = _normalize_optional_text(payer)
    resolved_retail_credits = _safe_float(retail_credits_charged)
    if resolved_retail_credits is None and resolved_cost is not None:
        billing_source = _normalize_text(metadata_payload.get("billing_source")).lower()
        credential_plane = _normalize_text(metadata_payload.get("credential_plane")).lower()
        if (
            is_platform_paid_usage_value(payer_token)
            or is_platform_paid_usage_value(billing_source)
            or credential_plane == "platform_runtime"
        ):
            resolved_retail_credits = billing_credit_config.display_credit_float_for_usd(resolved_cost)
    record = UsageAccountingRecord(
        usage_record_id=f"uacct_{_normalize_text(run_id) or uuid.uuid4().hex[:16]}",
        run_id=_normalize_optional_text(run_id),
        tenant_id=_normalize_optional_text(tenant_id),
        workspace_id=_normalize_optional_text(workspace_id),
        deployed_agent_id=_normalize_optional_text(deployed_agent_id),
        surface=resolved_surface,
        source_surface=resolved_source_surface,
        requested_provider=normalized_requested_provider,
        effective_provider=provider,
        requested_model=normalized_requested_model,
        effective_model=model,
        input_tokens=input_value,
        output_tokens=output_value,
        total_tokens=total_value,
        estimated_cost_usd=resolved_cost,
        pricing_known=bool(pricing_known),
        pricing_registry_version=pricing_registry_service.pricing_registry_version(),
        pricing_source=_normalize_optional_text(pricing_source),
        estimation_mode=_normalize_text(estimation_mode).lower() or "reconstructed",
        completed_at=_normalize_optional_text(completed_at) or _utc_now_iso(),
        metadata=metadata_payload,
        user_id=_normalize_optional_text(user_id),
        app_id=_normalize_optional_text(app_id),
        thread_id=_normalize_optional_text(thread_id),
        agent_id=_normalize_optional_text(agent_id or deployed_agent_id),
        payer=payer_token,
        cached_input_tokens=cached_value,
        cache_write_tokens=cache_write_value,
        cache_read_tokens=cache_read_value,
        visible_output_tokens=visible_output_value,
        reasoning_tokens=reasoning_value,
        thinking_tokens=thinking_value,
        provider_cost_usd=resolved_cost,
        retail_credits_charged=resolved_retail_credits,
        success=bool(success),
        error_code=_normalize_optional_text(error_code),
        fallback_attempt=bool(fallback_attempt),
        created_at=_normalize_optional_text(created_at) or _normalize_optional_text(completed_at) or _utc_now_iso(),
    )
    return record.as_dict()


def usage_projection_from_record(record: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(record or {})
    cost = _safe_float(payload.get("estimated_cost_usd"))
    timestamp = _normalize_optional_text(payload.get("completed_at")) or _utc_now_iso()
    source_surface = _normalize_optional_text(payload.get("source_surface")) or _derive_source_surface(
        explicit=_normalize_optional_text(payload.get("surface"))
    )
    surface = _derive_surface(payload.get("surface") or source_surface)
    return {
        "provider": _normalize_optional_text(payload.get("effective_provider")),
        "model": _normalize_optional_text(payload.get("effective_model")),
        "prompt_tokens": max(0, _safe_int(payload.get("input_tokens"), 0)),
        "completion_tokens": max(0, _safe_int(payload.get("output_tokens"), 0)),
        "total_tokens": max(0, _safe_int(payload.get("total_tokens"), 0)),
        "cached_input_tokens": max(0, _safe_int(payload.get("cached_input_tokens"), 0)),
        "cache_write_tokens": max(0, _safe_int(payload.get("cache_write_tokens"), 0)),
        "cache_read_tokens": max(0, _safe_int(payload.get("cache_read_tokens"), 0)),
        "visible_output_tokens": max(0, _safe_int(payload.get("visible_output_tokens"), payload.get("output_tokens") or 0)),
        "reasoning_tokens": max(0, _safe_int(payload.get("reasoning_tokens"), 0)),
        "thinking_tokens": max(0, _safe_int(payload.get("thinking_tokens"), 0)),
        "input_tokens_est": max(0, _safe_int(payload.get("input_tokens"), 0)),
        "output_tokens_est": max(0, _safe_int(payload.get("output_tokens"), 0)),
        "total_tokens_est": max(0, _safe_int(payload.get("total_tokens"), 0)),
        "estimated_cost_usd": cost,
        "provider_cost_usd": _safe_float(payload.get("provider_cost_usd", payload.get("estimated_cost_usd"))),
        "retail_credits_charged": _safe_float(payload.get("retail_credits_charged")),
        "payer": _normalize_optional_text(payload.get("payer")),
        "cost_est_usd": cost,
        "cost_band": masked_cost_band(cost),
        "pricing_known": bool(payload.get("pricing_known")),
        "pricing_source": _normalize_optional_text(payload.get("pricing_source")),
        "pricing_registry_version": _normalize_optional_text(payload.get("pricing_registry_version")),
        "estimation_mode": _normalize_optional_text(payload.get("estimation_mode")) or "reconstructed",
        "surface": surface,
        "source_surface": source_surface,
        "usage_record_id": _normalize_optional_text(payload.get("usage_record_id")),
        "run_id": _normalize_optional_text(payload.get("run_id")),
        "workspace_id": _normalize_optional_text(payload.get("workspace_id")),
        "user_id": _normalize_optional_text(payload.get("user_id")),
        "app_id": _normalize_optional_text(payload.get("app_id")),
        "agent_id": _normalize_optional_text(payload.get("agent_id") or payload.get("deployed_agent_id")),
        "thread_id": _normalize_optional_text(payload.get("thread_id")),
        "success": bool(payload.get("success", True)),
        "error_code": _normalize_optional_text(payload.get("error_code")),
        "fallback_attempt": bool(payload.get("fallback_attempt")),
        "timestamp": timestamp,
        "usage_accounting": payload,
    }


PLATFORM_PAID_USAGE_VALUES = {"platform_credits", "empyralis_credits", "empyralis"}
_NON_EXACT_ESTIMATION_MODES = {
    "provider_usage_missing",
    "masked_text_estimate",
    "heuristic_blackbox",
    "heuristic",
    "heuristic_estimate",
}


def is_platform_paid_usage_value(value: Any) -> bool:
    return _normalize_text(value).lower() in PLATFORM_PAID_USAGE_VALUES


def require_known_payer_value(value: Any, *, field_name: str = "payer") -> str:
    token = _normalize_text(value).lower()
    if token in PLATFORM_PAID_USAGE_VALUES:
        return "platform_credits"
    if token in {"byok", "workspace_api_key", "workspace_connection"}:
        return "BYOK"
    if token in {"local", "local_model", "local_companion"}:
        return "local"
    if token in {"subscription_passthrough", "subscription", "codex_cli", "claude_code_cli"}:
        return "subscription_passthrough"
    raise ValueError(f"Unknown or missing {field_name}.")


def platform_paid_usage_validation_error(row_or_record: Dict[str, Any]) -> Optional[str]:
    payload = dict(row_or_record or {}) if isinstance(row_or_record, dict) else {}
    provider = _normalize_optional_text(payload.get("provider") or payload.get("effective_provider"))
    model = _normalize_optional_text(payload.get("model") or payload.get("effective_model"))
    total_tokens = max(0, _safe_int(payload.get("total_tokens") or payload.get("total_tokens_est"), 0))
    provider_cost = _safe_float(
        payload.get("provider_cost_usd", payload.get("estimated_cost_usd", payload.get("cost_est_usd")))
    )
    estimation_mode = _normalize_text(payload.get("estimation_mode")).lower()
    if not provider:
        return "missing_provider"
    if not model:
        return "missing_model"
    if total_tokens <= 0:
        return "missing_provider_token_usage"
    if not bool(payload.get("pricing_known")):
        return "unknown_pricing"
    if provider_cost is None:
        return "missing_provider_cost"
    if estimation_mode in _NON_EXACT_ESTIMATION_MODES or estimation_mode.startswith("heuristic"):
        return f"non_exact_usage:{estimation_mode or 'unknown'}"
    if bool(payload.get("fallback_attempt")):
        return "fallback_attempt"
    if payload.get("success") is False:
        return f"unsuccessful_usage:{_normalize_text(payload.get('error_code')) or 'unknown'}"
    return None


def assert_platform_paid_usage(row_or_record: Dict[str, Any], *, label: str = "Platform-paid AI usage") -> Dict[str, Any]:
    payload = dict(row_or_record or {}) if isinstance(row_or_record, dict) else {}
    error = platform_paid_usage_validation_error(payload)
    if error:
        raise ValueError(f"{label} requires exact priced provider usage ({error}).")
    return payload


def build_masked_usage(
    provider: str,
    model: str,
    input_text: str,
    output_text: str,
    *,
    run_id: Optional[str] = None,
    source_surface: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    input_tokens = estimate_tokens(input_text)
    output_tokens = estimate_tokens(output_text)
    record = build_usage_accounting_record(
        run_id=run_id,
        requested_provider=provider,
        effective_provider=provider,
        requested_model=model,
        effective_model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        estimation_mode="masked_text_estimate",
        source_surface=source_surface or "durable_run",
        metadata=metadata,
        allow_provider_fallback=True,
    )
    return usage_projection_from_record(record)


def accounting_record_from_snapshot(snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(snapshot, dict):
        return None
    direct = snapshot.get("usage_accounting") if isinstance(snapshot.get("usage_accounting"), dict) else None
    if isinstance(direct, dict) and direct:
        return build_usage_accounting_record(
            run_id=_normalize_optional_text(direct.get("run_id") or snapshot.get("run_id")),
            tenant_id=_normalize_optional_text(direct.get("tenant_id") or _coerce_dict(snapshot.get("context")).get("tenant_id")),
            workspace_id=_normalize_optional_text(direct.get("workspace_id") or _coerce_dict(snapshot.get("context")).get("workspace_id")),
            deployed_agent_id=_normalize_optional_text(direct.get("deployed_agent_id") or _coerce_dict(_coerce_dict(snapshot.get("context")).get("metadata")).get("deployed_agent_id")),
            surface=_normalize_optional_text(direct.get("surface")),
            source_surface=_normalize_optional_text(direct.get("source_surface") or direct.get("surface")),
            requested_provider=direct.get("requested_provider"),
            effective_provider=direct.get("effective_provider"),
            requested_model=direct.get("requested_model"),
            effective_model=direct.get("effective_model"),
            input_tokens=_safe_int(direct.get("input_tokens"), 0),
            output_tokens=_safe_int(direct.get("output_tokens"), 0),
            total_tokens=_safe_int(direct.get("total_tokens"), 0),
            cached_input_tokens=_safe_int(direct.get("cached_input_tokens"), 0),
            cache_write_tokens=_safe_int(direct.get("cache_write_tokens"), 0),
            cache_read_tokens=_safe_int(direct.get("cache_read_tokens"), 0),
            visible_output_tokens=_safe_int(direct.get("visible_output_tokens"), direct.get("output_tokens") or 0),
            reasoning_tokens=_safe_int(direct.get("reasoning_tokens"), 0),
            thinking_tokens=_safe_int(direct.get("thinking_tokens"), 0),
            estimated_cost_usd=_safe_float(direct.get("estimated_cost_usd")),
            provider_cost_usd=_safe_float(direct.get("provider_cost_usd")),
            retail_credits_charged=_safe_float(direct.get("retail_credits_charged")),
            payer=_normalize_optional_text(direct.get("payer")),
            user_id=_normalize_optional_text(direct.get("user_id") or snapshot.get("owner_user_id")),
            app_id=_normalize_optional_text(direct.get("app_id") or snapshot.get("app_id")),
            thread_id=_normalize_optional_text(direct.get("thread_id") or snapshot.get("thread_id")),
            agent_id=_normalize_optional_text(direct.get("agent_id") or direct.get("deployed_agent_id")),
            success=bool(direct.get("success", True)),
            error_code=_normalize_optional_text(direct.get("error_code")),
            fallback_attempt=bool(direct.get("fallback_attempt")),
            estimation_mode=_normalize_optional_text(direct.get("estimation_mode")) or "reconstructed",
            completed_at=_normalize_optional_text(direct.get("completed_at") or snapshot.get("completed_at") or snapshot.get("updated_at") or snapshot.get("created_at")),
            created_at=_normalize_optional_text(direct.get("created_at") or snapshot.get("created_at")),
            metadata=_coerce_dict(direct.get("metadata")),
        )
    usage = snapshot.get("usage_masked") if isinstance(snapshot.get("usage_masked"), dict) else {}
    if isinstance(usage.get("usage_accounting"), dict):
        return accounting_record_from_snapshot(
            {
                **snapshot,
                "usage_accounting": usage.get("usage_accounting"),
            }
        )
    provider = usage.get("provider") or snapshot.get("usage_provider")
    model = usage.get("model") or snapshot.get("usage_model")
    if not provider or not model:
        return None
    context = _coerce_dict(snapshot.get("context"))
    metadata = _coerce_dict(context.get("metadata"))
    return build_usage_accounting_record(
        run_id=_normalize_optional_text(snapshot.get("run_id") or usage.get("run_id")),
        tenant_id=_normalize_optional_text(context.get("tenant_id") or snapshot.get("tenant_id")),
        workspace_id=_normalize_optional_text(context.get("workspace_id") or snapshot.get("workspace_id")),
        deployed_agent_id=_normalize_optional_text(metadata.get("deployed_agent_id") or snapshot.get("deployed_agent_id")),
        surface=_normalize_optional_text(usage.get("surface") or snapshot.get("surface") or metadata.get("surface")),
        source_surface=_derive_source_surface(
            explicit=_normalize_optional_text(
                usage.get("source_surface")
                or usage.get("surface")
                or snapshot.get("source_surface")
                or snapshot.get("surface")
            ),
            context=context,
            metadata=metadata,
        ),
        requested_provider=usage.get("requested_provider") or provider,
        effective_provider=provider,
        requested_model=usage.get("requested_model") or model,
        effective_model=model,
        input_tokens=_safe_int(usage.get("prompt_tokens", usage.get("input_tokens_est", 0)), 0),
        output_tokens=_safe_int(usage.get("completion_tokens", usage.get("output_tokens_est", 0)), 0),
        total_tokens=_safe_int(usage.get("total_tokens", usage.get("total_tokens_est", 0)), 0),
        cached_input_tokens=_safe_int(usage.get("cached_input_tokens"), 0),
        cache_write_tokens=_safe_int(usage.get("cache_write_tokens"), 0),
        cache_read_tokens=_safe_int(usage.get("cache_read_tokens"), 0),
        visible_output_tokens=_safe_int(usage.get("visible_output_tokens", usage.get("completion_tokens", usage.get("output_tokens_est", 0))), 0),
        reasoning_tokens=_safe_int(usage.get("reasoning_tokens"), 0),
        thinking_tokens=_safe_int(usage.get("thinking_tokens"), 0),
        estimated_cost_usd=_safe_float(usage.get("estimated_cost_usd", usage.get("cost_est_usd"))),
        provider_cost_usd=_safe_float(usage.get("provider_cost_usd")),
        retail_credits_charged=_safe_float(usage.get("retail_credits_charged")),
        payer=_normalize_optional_text(usage.get("payer")),
        user_id=_normalize_optional_text(usage.get("user_id") or snapshot.get("owner_user_id")),
        app_id=_normalize_optional_text(usage.get("app_id") or snapshot.get("app_id")),
        thread_id=_normalize_optional_text(usage.get("thread_id") or snapshot.get("thread_id")),
        agent_id=_normalize_optional_text(usage.get("agent_id") or snapshot.get("deployed_agent_id")),
        success=bool(usage.get("success", True)),
        error_code=_normalize_optional_text(usage.get("error_code")),
        fallback_attempt=bool(usage.get("fallback_attempt")),
        estimation_mode=_normalize_optional_text(usage.get("estimation_mode")) or "reconstructed",
        completed_at=_normalize_optional_text(usage.get("timestamp") or snapshot.get("completed_at") or snapshot.get("updated_at") or snapshot.get("created_at")),
        created_at=_normalize_optional_text(snapshot.get("created_at")),
        metadata={
            **_coerce_dict(usage.get("metadata")),
            "pricing_registry_version": usage.get("pricing_registry_version"),
            "pricing_source": usage.get("pricing_source"),
            "pricing_known": usage.get("pricing_known"),
        },
    )


def usage_row_from_record(record: Dict[str, Any], *, snapshot: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = dict(record or {})
    origin = _coerce_dict(snapshot)
    source_surface = _normalize_optional_text(payload.get("source_surface")) or _derive_source_surface(
        explicit=_normalize_optional_text(payload.get("surface"))
    )
    surface = _derive_surface(payload.get("surface") or source_surface)
    row = {
        "usage_record_id": _normalize_optional_text(payload.get("usage_record_id")),
        "run_id": _normalize_optional_text(payload.get("run_id") or origin.get("run_id")),
        "tenant_id": _normalize_optional_text(payload.get("tenant_id") or origin.get("tenant_id")),
        "workspace_id": _normalize_optional_text(payload.get("workspace_id") or origin.get("workspace_id")),
        "deployed_agent_id": _normalize_optional_text(payload.get("deployed_agent_id") or origin.get("deployed_agent_id")),
        "surface": surface,
        "source_surface": source_surface,
        "requested_provider": _normalize_optional_text(payload.get("requested_provider")),
        "provider": _normalize_optional_text(payload.get("effective_provider")),
        "requested_model": _normalize_optional_text(payload.get("requested_model")),
        "model": _normalize_optional_text(payload.get("effective_model")),
        "prompt_tokens": max(0, _safe_int(payload.get("input_tokens"), 0)),
        "completion_tokens": max(0, _safe_int(payload.get("output_tokens"), 0)),
        "total_tokens": max(0, _safe_int(payload.get("total_tokens"), 0)),
        "cached_input_tokens": max(0, _safe_int(payload.get("cached_input_tokens"), 0)),
        "cache_write_tokens": max(0, _safe_int(payload.get("cache_write_tokens"), 0)),
        "cache_read_tokens": max(0, _safe_int(payload.get("cache_read_tokens"), 0)),
        "visible_output_tokens": max(0, _safe_int(payload.get("visible_output_tokens"), payload.get("output_tokens") or 0)),
        "reasoning_tokens": max(0, _safe_int(payload.get("reasoning_tokens"), 0)),
        "thinking_tokens": max(0, _safe_int(payload.get("thinking_tokens"), 0)),
        "estimated_cost_usd": _safe_float(payload.get("estimated_cost_usd")),
        "provider_cost_usd": _safe_float(payload.get("provider_cost_usd", payload.get("estimated_cost_usd"))),
        "retail_credits_charged": _safe_float(payload.get("retail_credits_charged")),
        "payer": _normalize_optional_text(payload.get("payer")),
        "cost_est_usd": _safe_float(payload.get("estimated_cost_usd")),
        "cost_band": masked_cost_band(_safe_float(payload.get("estimated_cost_usd"))),
        "pricing_known": bool(payload.get("pricing_known")),
        "pricing_registry_version": _normalize_optional_text(payload.get("pricing_registry_version")),
        "pricing_source": _normalize_optional_text(payload.get("pricing_source")),
        "estimation_mode": _normalize_optional_text(payload.get("estimation_mode")) or "reconstructed",
        "timestamp": _normalize_optional_text(payload.get("completed_at")) or _utc_now_iso(),
        "created_at": _normalize_optional_text(origin.get("created_at")),
        "completed_at": _normalize_optional_text(origin.get("completed_at") or payload.get("completed_at")),
        "status": _normalize_optional_text(origin.get("status")),
        "owner_user_id": _normalize_optional_text(origin.get("owner_user_id")),
        "user_id": _normalize_optional_text(payload.get("user_id") or origin.get("owner_user_id")),
        "app_id": _normalize_optional_text(payload.get("app_id") or origin.get("app_id")),
        "agent_id": _normalize_optional_text(payload.get("agent_id") or payload.get("deployed_agent_id") or origin.get("deployed_agent_id")),
        "thread_id": _normalize_optional_text(payload.get("thread_id") or origin.get("thread_id")),
        "run_name": _normalize_optional_text(origin.get("user_goal") or origin.get("result_summary")) or _normalize_optional_text(payload.get("run_id")),
        "metadata": _coerce_dict(payload.get("metadata")),
    }
    return row


def normalize_provider_usage(
    *,
    provider: Any,
    model: Any,
    usage: Optional[Dict[str, Any]],
    run_id: Optional[str] = None,
    surface: Optional[str] = None,
    source_surface: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    payer: Optional[str] = None,
    require_known_pricing: bool = False,
) -> Dict[str, Any]:
    """Normalize provider-specific token usage into the canonical accounting shape."""
    pid = pricing_registry_service.normalize_provider_id(provider)
    model_id = pricing_registry_service.normalize_model_id(pid, model)
    raw = _coerce_dict(usage)

    if pid in {"anthropic", "claude_code_cli"}:
        input_tokens = _nonnegative_int(raw.get("input_tokens"))
        cache_read_tokens = _nonnegative_int(raw.get("cache_read_input_tokens"))
        cache_write_tokens = _nonnegative_int(raw.get("cache_creation_input_tokens"))
        visible_output_tokens = _nonnegative_int(raw.get("output_tokens"))
        reasoning_tokens = _nonnegative_int(raw.get("reasoning_output_tokens") or raw.get("thinking_tokens"))
        thinking_tokens = _nonnegative_int(raw.get("thinking_output_tokens"))
    elif pid in {"gemini", "vertex"}:
        prompt_tokens = _nonnegative_int(raw.get("promptTokenCount") or raw.get("prompt_token_count"))
        cache_read_tokens = _nonnegative_int(raw.get("cachedContentTokenCount") or raw.get("cacheReadTokenCount"))
        input_tokens = max(0, prompt_tokens - cache_read_tokens)
        cache_write_tokens = _nonnegative_int(raw.get("cacheWriteTokenCount"))
        visible_output_tokens = _nonnegative_int(raw.get("candidatesTokenCount") or raw.get("completionTokenCount"))
        thinking_tokens = _nonnegative_int(raw.get("thoughtsTokenCount") or raw.get("thinkingTokenCount"))
        reasoning_tokens = _nonnegative_int(raw.get("reasoningTokenCount"))
    elif pid in {"ollama", "ollama_cloud"}:
        input_tokens = _nonnegative_int(raw.get("prompt_eval_count"))
        cache_read_tokens = 0
        cache_write_tokens = 0
        visible_output_tokens = _nonnegative_int(raw.get("eval_count"))
        reasoning_tokens = 0
        thinking_tokens = 0
    else:
        prompt_tokens = _nonnegative_int(raw.get("prompt_tokens") or raw.get("input_tokens"))
        prompt_details = _coerce_dict(raw.get("prompt_tokens_details") or raw.get("input_tokens_details"))
        completion_details = _coerce_dict(raw.get("completion_tokens_details"))
        prompt_cache_hit_tokens = _nonnegative_int(
            raw.get("prompt_cache_hit_tokens")
            or raw.get("prompt_cache_read_tokens")
            or prompt_details.get("prompt_cache_hit_tokens")
        )
        cache_read_tokens = _nonnegative_int(
            raw.get("cached_input_tokens")
            or prompt_cache_hit_tokens
            or prompt_details.get("cached_tokens")
            or prompt_details.get("cache_read_tokens")
            or prompt_details.get("cache_read_input_tokens")
            or raw.get("cache_read_input_tokens")
            or raw.get("cache_read_tokens")
        )
        cache_miss_present = (
            "prompt_cache_miss_tokens" in raw
            or "cache_miss_tokens" in raw
            or "prompt_cache_miss_tokens" in prompt_details
            or "cache_miss_tokens" in prompt_details
        )
        cache_miss_tokens = _nonnegative_int(
            raw.get("prompt_cache_miss_tokens")
            or raw.get("cache_miss_tokens")
            or prompt_details.get("prompt_cache_miss_tokens")
            or prompt_details.get("cache_miss_tokens")
        )
        input_tokens = cache_miss_tokens if cache_miss_present else max(0, prompt_tokens - cache_read_tokens)
        cache_write_tokens = _nonnegative_int(
            raw.get("cache_write_input_tokens")
            or raw.get("cache_creation_input_tokens")
            or raw.get("cache_write_tokens")
            or prompt_details.get("cache_write_tokens")
            or prompt_details.get("cache_creation_tokens")
        )
        reasoning_tokens = _nonnegative_int(
            raw.get("reasoning_tokens")
            or completion_details.get("reasoning_tokens")
            or raw.get("reasoning_output_tokens")
        )
        thinking_tokens = _nonnegative_int(raw.get("thinking_tokens") or raw.get("thoughts_tokens"))
        completion_tokens = _nonnegative_int(raw.get("completion_tokens") or raw.get("output_tokens"))
        visible_output_tokens = max(0, completion_tokens - reasoning_tokens - thinking_tokens)

    output_tokens = visible_output_tokens + reasoning_tokens + thinking_tokens
    total_tokens = _nonnegative_int(raw.get("total_tokens") or raw.get("totalTokenCount"))
    if total_tokens <= 0:
        total_tokens = input_tokens + cache_read_tokens + cache_write_tokens + output_tokens
    provider_reported_cost = _safe_float(
        raw.get("cost")
        or raw.get("provider_reported_cost")
        or raw.get("provider_cost_usd")
        or raw.get("estimated_cost_usd")
    )
    provider_reported_currency = _normalize_optional_text(
        raw.get("currency") or raw.get("provider_reported_currency")
    )
    return build_usage_accounting_record(
        run_id=run_id,
        surface=surface,
        source_surface=source_surface,
        requested_provider=provider,
        effective_provider=pid,
        requested_model=model,
        effective_model=model_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cached_input_tokens=cache_read_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        visible_output_tokens=visible_output_tokens,
        reasoning_tokens=reasoning_tokens,
        thinking_tokens=thinking_tokens,
        provider_cost_usd=provider_reported_cost,
        estimation_mode="provider_usage_exact" if raw else "provider_usage_missing",
        metadata={
            **_coerce_dict(metadata),
            "raw_usage_keys": sorted(str(key) for key in raw.keys()),
            "provider_reported_cost": provider_reported_cost,
            "provider_reported_currency": provider_reported_currency,
        },
        payer=payer,
        require_known_pricing=require_known_pricing,
    )


def usage_row_from_snapshot(snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    record = accounting_record_from_snapshot(snapshot)
    if record is None:
        return None
    row = usage_row_from_record(record, snapshot=snapshot)
    return row if row.get("run_id") else None


def summarize_provider_model_cost_rows(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[Tuple[str, str], Dict[str, Any]] = {}
    total_cost = 0.0
    for row in list(rows or []):
        if not isinstance(row, dict):
            continue
        provider = _normalize_text(row.get("provider") or row.get("effective_provider") or "unknown").lower() or "unknown"
        model = _normalize_text(row.get("model") or row.get("effective_model") or "unknown") or "unknown"
        key = (provider, model)
        if key not in grouped:
            grouped[key] = {
                "provider": provider,
                "model": model,
                "runs_count": 0,
                "total_tokens": 0,
                "total_cost_usd": 0.0,
            }
        cost_value = float(row.get("estimated_cost_usd") or 0.0)
        grouped[key]["runs_count"] = int(grouped[key]["runs_count"] or 0) + int(row.get("runs_count") or 1)
        grouped[key]["total_tokens"] = int(grouped[key]["total_tokens"] or 0) + int(row.get("total_tokens") or 0)
        grouped[key]["total_cost_usd"] = round(float(grouped[key]["total_cost_usd"] or 0.0) + cost_value, 6)
        total_cost += cost_value
    items = list(grouped.values())
    items.sort(
        key=lambda item: (
            -float(item.get("total_cost_usd") or 0.0),
            -int(item.get("runs_count") or 0),
            str(item.get("provider") or ""),
            str(item.get("model") or ""),
        )
    )
    denominator = total_cost if total_cost > 0 else 0.0
    for item in items:
        cost_value = float(item.get("total_cost_usd") or 0.0)
        item["share_percent"] = round((cost_value / denominator) * 100.0, 2) if denominator > 0 else 0.0
    return items
