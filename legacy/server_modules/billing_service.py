from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import time
from typing import Any, Dict, Optional
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

from fastapi import HTTPException

from server_modules import billing_credit_config
from server_modules import control_plane_repository
from server_modules import run_state_repository
from server_modules import rust_runtime_kernel_client
from server_modules.direct_tool_config_service import run_async_tool_call


DEFAULT_BILLING_PLAN_ID = "free"
STRIPE_PROVIDER = "stripe"
STRIPE_API_BASE = "https://api.stripe.com/v1"
STRIPE_WEBHOOK_TOLERANCE_SECONDS = 300
HOSTED_SAGE_AI_CREDITS_PER_USD = billing_credit_config.HOSTED_SAGE_AI_CREDITS_PER_USD
DEFAULT_HOSTED_SAGE_AI_MONTHLY_CAP_USD = billing_credit_config.DEFAULT_HOSTED_SAGE_AI_MONTHLY_CAP_USD

PLAN_LABELS: Dict[str, str] = {
    "free": "Free",
    "pilot": "Pilot",
    "pro": "Pro",
}

PLAN_ALIASES: Dict[str, str] = {
    "starter": "free",
    "free_personal": "free",
    "standard": "pro",
    "personal": "pro",
    "business": "pro",
    "power": "pro",
    "team": "pro",
    "enterprise": "pro",
    "enterprise_plus": "pro",
    "beta": "pilot",
    "early_access": "pilot",
    "pilot_program": "pilot",
}

TERMINAL_SUBSCRIPTION_STATUSES = {
    "canceled",
    "cancelled",
    "incomplete_expired",
    "paused",
    "unpaid",
}

ACTIVE_SUBSCRIPTION_STATUSES = {
    "active",
    "trialing",
    "past_due",
}


def normalize_billing_plan_id(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token in PLAN_ALIASES:
        token = PLAN_ALIASES[token]
    return token if token in PLAN_LABELS else DEFAULT_BILLING_PLAN_ID


def billing_plan_label(plan_id: Any) -> str:
    resolved_plan_id = normalize_billing_plan_id(plan_id)
    return PLAN_LABELS.get(resolved_plan_id, resolved_plan_id.title())


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _enforce_billing_control_plane_decision(
    *,
    operation: str,
    record_type: str,
    tenant_id: Optional[str],
    workspace_id: str,
    target_status: str,
    idempotency_key: str,
) -> Dict[str, Any]:
    payload = {
        "operation": operation,
        "record_type": record_type,
        "tenant_id": str(tenant_id or "").strip() or "default",
        "workspace_id": str(workspace_id or "").strip(),
        "actor_id": "billing_service",
        "actor_role": "system",
        "target_status": str(target_status or "").strip() or "active",
        "idempotency_key": str(idempotency_key or "").strip(),
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
                "operation": result.get("operation") or operation,
                "reason": result.get("reason") or str(exc),
            },
        ) from exc
    mutation_plan = _coerce_dict(decision.get("mutation_plan"))
    if mutation_plan.get("apply") is not True:
        raise HTTPException(
            status_code=423,
            detail={
                "error": "rust_control_plane_service_denied",
                "operation": decision.get("operation") or operation,
                "reason": decision.get("reason") or "missing_rust_mutation_plan",
            },
        )
    next_action = str(mutation_plan.get("next_action") or decision.get("next_action") or "").strip()
    if next_action != "apply_control_plane_write":
        raise HTTPException(
            status_code=423,
            detail={
                "error": "rust_control_plane_service_invalid_next_action",
                "operation": decision.get("operation") or operation,
                "reason": (
                    "Rust control-plane service returned unexpected next_action for "
                    f"billing operation {operation}: {next_action or 'missing'}"
                ),
            },
        )
    return decision


def _coerce_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _workspace_billing_metadata(workspace: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    metadata = _coerce_dict(_coerce_dict(workspace).get("metadata"))
    admin_defaults = _coerce_dict(metadata.get("admin_defaults"))
    if isinstance(admin_defaults.get("payload"), dict):
        admin_defaults = _coerce_dict(admin_defaults.get("payload"))
    return {
        **_coerce_dict(metadata.get("entitlements")),
        **_coerce_dict(metadata.get("billing")),
        **admin_defaults,
    }


def _explicit_workspace_billing_plan_id(workspace: Optional[Dict[str, Any]]) -> Optional[str]:
    metadata = _workspace_billing_metadata(workspace)
    hosted_policy = _hosted_sage_ai_policy(metadata.get("hosted_sage_ai_policy"))
    if hosted_policy != "enabled_with_cap":
        return None
    for key in ("billing_plan", "plan", "plan_id", "plan_tier"):
        raw = metadata.get(key)
        if str(raw or "").strip():
            return normalize_billing_plan_id(raw)
    return None


def _hosted_sage_ai_policy(value: Any) -> str:
    token = str(value or "").strip().lower()
    return token if token in {"disabled", "owner_opt_in", "enabled_with_cap"} else "owner_opt_in"


def _plan_allows_hosted_ai(effective_plan_id: str) -> bool:
    plan_id = normalize_billing_plan_id(effective_plan_id)
    return plan_id in {"free", "pilot", "pro"}


def _hosted_sage_ai_credit_fields(
    *,
    monthly_cap_usd: float,
    monthly_cost_usd: float,
    monthly_remaining_usd: float,
) -> Dict[str, Any]:
    credits_per_usd = HOSTED_SAGE_AI_CREDITS_PER_USD
    return {
        "credit_unit": "credits",
        "credits_per_usd": credits_per_usd,
        "monthly_credit_cap": int(round(float(monthly_cap_usd or 0.0) * credits_per_usd)),
        "monthly_credits_used": int(round(float(monthly_cost_usd or 0.0) * credits_per_usd)),
        "monthly_credits_remaining": int(round(float(monthly_remaining_usd or 0.0) * credits_per_usd)),
    }


def _hosted_sage_ai_credit_state(
    *,
    effective_plan_id: str,
    usage: Dict[str, Any],
    workspace: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    metadata = _workspace_billing_metadata(workspace)
    plan_allows_hosted_ai = _plan_allows_hosted_ai(effective_plan_id)
    policy = _hosted_sage_ai_policy(metadata.get("hosted_sage_ai_policy"))
    monthly_cap_usd = _coerce_float(metadata.get("hosted_sage_ai_monthly_cap_usd"))
    if monthly_cap_usd is None:
        monthly_cap_usd = DEFAULT_HOSTED_SAGE_AI_MONTHLY_CAP_USD
    monthly_cap_usd = max(0.0, round(float(monthly_cap_usd), 6))
    monthly_cost_usd = max(0.0, round(float(usage.get("hosted_sage_cost_usd_monthly") or 0.0), 6))
    monthly_remaining_usd = max(0.0, round(monthly_cap_usd - monthly_cost_usd, 6))
    credit_balance_usd = _credit_balance_from_metadata(metadata)
    total_available_usd = round(monthly_remaining_usd + credit_balance_usd, 6)
    credit_fields = _hosted_sage_ai_credit_fields(
        monthly_cap_usd=monthly_cap_usd,
        monthly_cost_usd=monthly_cost_usd,
        monthly_remaining_usd=monthly_remaining_usd,
    )

    if not plan_allows_hosted_ai:
        reason = "policy_disabled"
        message = "Credits are not active yet. Add credits or use your own API key."
        allowed = False
        resolved_policy = "disabled"
    elif policy == "disabled":
        reason = "policy_disabled"
        message = "Hosted Sage AI is disabled for this workspace."
        allowed = False
        resolved_policy = policy
    elif policy == "owner_opt_in":
        reason = "owner_approval_required"
        message = "Hosted Sage AI needs owner approval before this workspace can use it."
        allowed = False
        resolved_policy = policy
    elif total_available_usd <= 0:
        reason = "cap_reached"
        message = "Hosted Sage AI monthly cap is reached for this workspace."
        allowed = False
        resolved_policy = policy
    else:
        reason = None
        message = None
        allowed = True
        resolved_policy = policy

    return {
        "allowed": allowed,
        "plan_allows_hosted_ai": plan_allows_hosted_ai,
        "policy": resolved_policy,
        "monthly_cap_usd": monthly_cap_usd,
        "monthly_cost_usd": monthly_cost_usd,
        "monthly_remaining_usd": monthly_remaining_usd,
        "credit_balance_usd": credit_balance_usd,
        "credit_balance_credits": int(round(credit_balance_usd * HOSTED_SAGE_AI_CREDITS_PER_USD)),
        "total_available_usd": total_available_usd,
        "total_available_credits": int(round(total_available_usd * HOSTED_SAGE_AI_CREDITS_PER_USD)),
        **credit_fields,
        "reason": reason,
        "message": message,
    }


def _stripe_secret_key() -> str:
    return str(
        os.getenv("EMPYRALIS_STRIPE_SECRET_KEY")
        or os.getenv("STRIPE_SECRET_KEY")
        or ""
    ).strip()


def _stripe_webhook_secret() -> str:
    return str(os.getenv("EMPYRALIS_STRIPE_WEBHOOK_SECRET") or "").strip()


def _stripe_price_map() -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    raw_json = str(os.getenv("EMPYRALIS_STRIPE_PRICE_IDS") or "").strip()
    if raw_json:
        try:
            parsed = json.loads(raw_json)
        except Exception:
            parsed = {}
        if isinstance(parsed, dict):
            for raw_plan, raw_price_id in parsed.items():
                price_id = str(raw_price_id or "").strip()
                if price_id:
                    mapping[normalize_billing_plan_id(raw_plan)] = price_id
    for plan_id in PLAN_LABELS:
        env_key = f"EMPYRALIS_STRIPE_PRICE_{plan_id.upper()}"
        price_id = str(os.getenv(env_key) or "").strip()
        if price_id:
            mapping[plan_id] = price_id
    return mapping


def _price_to_plan_map() -> Dict[str, str]:
    return {price_id: plan_id for plan_id, price_id in _stripe_price_map().items() if price_id}


def _frontend_origin() -> str:
    configured = str(os.getenv("EMPYRALIS_BILLING_FRONTEND_ORIGIN") or "").strip()
    if configured:
        return configured.rstrip("/")
    origins = str(os.getenv("FRONTEND_ORIGINS") or "http://127.0.0.1:3000").split(",")
    for origin in origins:
        token = str(origin or "").strip()
        if token:
            return token.rstrip("/")
    return "http://127.0.0.1:3000"


def _billing_success_url(workspace_id: str) -> str:
    configured = str(os.getenv("EMPYRALIS_BILLING_SUCCESS_URL") or "").strip()
    if configured:
        return configured
    return f"{_frontend_origin()}/w/{workspace_id}/admin/billing?checkout=success"


def _billing_cancel_url(workspace_id: str) -> str:
    configured = str(os.getenv("EMPYRALIS_BILLING_CANCEL_URL") or "").strip()
    if configured:
        return configured
    return f"{_frontend_origin()}/w/{workspace_id}/admin/billing?checkout=cancelled"


def _billing_credit_success_url(workspace_id: str) -> str:
    configured = str(os.getenv("EMPYRALIS_BILLING_CREDIT_SUCCESS_URL") or "").strip()
    if configured:
        return configured
    return f"{_frontend_origin()}/w/{workspace_id}/admin/billing?credit_purchase=success"


def _billing_credit_cancel_url(workspace_id: str) -> str:
    configured = str(os.getenv("EMPYRALIS_BILLING_CREDIT_CANCEL_URL") or "").strip()
    if configured:
        return configured
    return f"{_frontend_origin()}/w/{workspace_id}/admin/billing?credit_purchase=cancelled"


def _billing_portal_return_url(workspace_id: str) -> str:
    configured = str(os.getenv("EMPYRALIS_BILLING_PORTAL_RETURN_URL") or "").strip()
    if configured:
        return configured
    return f"{_frontend_origin()}/w/{workspace_id}/admin/billing"


def _stripe_configured() -> bool:
    return bool(_stripe_secret_key()) and any(_stripe_price_map().values())


_MIN_CREDIT_PURCHASE_USD = 1.0
_MAX_CREDIT_PURCHASE_USD = 500.0


def _credit_balance_from_metadata(metadata: Dict[str, Any]) -> float:
    raw = _coerce_float(_coerce_dict(metadata).get("credit_balance_usd"))
    if raw is None:
        return 0.0
    return max(0.0, round(float(raw), 6))


def _credit_transactions_from_metadata(metadata: Dict[str, Any]) -> list[Dict[str, Any]]:
    transactions = _coerce_dict(metadata).get("credit_transactions")
    if isinstance(transactions, list):
        return [dict(t) for t in transactions if isinstance(t, dict)]
    return []


def _credit_debits_for_month(metadata: Dict[str, Any], usage_month: str) -> float:
    total = 0.0
    for transaction in _credit_transactions_from_metadata(metadata):
        if str(transaction.get("kind") or "").strip().lower() != "usage_debit":
            continue
        if str(transaction.get("usage_month") or "").strip() != usage_month:
            continue
        amount = _coerce_float(transaction.get("amount_usd"))
        if amount is None:
            continue
        total += abs(float(amount))
    return round(total, 6)


def _normalize_credit_purchase_amount_usd(value: Any) -> float:
    parsed = _coerce_float(value)
    if parsed is None:
        return 0.0
    return max(0.0, min(_MAX_CREDIT_PURCHASE_USD, round(float(parsed), 2)))


def debit_workspace_credit_balance_for_hosted_usage(
    *,
    workspace_id: str,
    tenant_id: str,
    request_id: str,
) -> Dict[str, Any]:
    clean_workspace_id = str(workspace_id or "").strip()
    clean_tenant_id = str(tenant_id or "").strip()
    clean_request_id = str(request_id or "").strip()
    if not clean_workspace_id or not clean_tenant_id or not clean_request_id:
        return {"ok": False, "debited_usd": 0.0, "reason": "missing_scope"}

    workspace = run_async_tool_call(control_plane_repository.get_workspace_by_id(clean_workspace_id)) or {}
    metadata = _workspace_billing_metadata(workspace)
    usage = run_async_tool_call(
        control_plane_repository.summarize_workspace_billing_usage(
            tenant_id=clean_tenant_id,
            workspace_id=clean_workspace_id,
        )
    ) or {}
    usage_month = str(usage.get("usage_month") or _utc_month_start().isoformat()).strip()
    monthly_cost_usd = max(0.0, round(float(usage.get("hosted_sage_cost_usd_monthly") or 0.0), 6))
    monthly_cap_usd = _coerce_float(metadata.get("hosted_sage_ai_monthly_cap_usd"))
    if monthly_cap_usd is None:
        monthly_cap_usd = DEFAULT_HOSTED_SAGE_AI_MONTHLY_CAP_USD
    monthly_cap_usd = max(0.0, round(float(monthly_cap_usd), 6))
    return run_async_tool_call(
        control_plane_repository.debit_workspace_credit_balance_for_hosted_usage_atomic(
            workspace_id=clean_workspace_id,
            tenant_id=clean_tenant_id,
            request_id=clean_request_id,
            usage_month=usage_month,
            monthly_cost_usd=monthly_cost_usd,
            monthly_cap_usd=monthly_cap_usd,
            credits_per_usd=HOSTED_SAGE_AI_CREDITS_PER_USD,
        )
    ) or {
        "ok": False,
        "debited_usd": 0.0,
        "reason": "credit_debit_unavailable",
    }


def _stripe_api_request(path: str, form_fields: Dict[str, Any]) -> Dict[str, Any]:
    secret_key = _stripe_secret_key()
    if not secret_key:
        raise HTTPException(status_code=503, detail="Stripe billing is not configured.")
    encoded = urlparse.urlencode(
        [
            (str(key), str(value))
            for key, value in form_fields.items()
            if value is not None and str(value) != ""
        ]
    ).encode("utf-8")
    request = urlrequest.Request(
        f"{STRIPE_API_BASE}{path}",
        data=encoded,
        method="POST",
        headers={
            "Authorization": f"Bearer {secret_key}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with urlrequest.urlopen(request, timeout=15) as response:
            payload = response.read().decode("utf-8")
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail=f"Stripe request failed: {detail or exc.reason}") from exc
    except urlerror.URLError as exc:
        raise HTTPException(status_code=502, detail=f"Stripe request failed: {exc.reason}") from exc
    try:
        parsed = json.loads(payload)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Stripe returned an invalid response.") from exc
    return dict(parsed) if isinstance(parsed, dict) else {}


def _subscription_effective_plan(subscription: Optional[Dict[str, Any]]) -> str:
    payload = _coerce_dict(subscription)
    if not payload:
        return DEFAULT_BILLING_PLAN_ID
    status = str(payload.get("status") or "").strip().lower()
    if status in TERMINAL_SUBSCRIPTION_STATUSES or status in {"checkout_pending", "checkout_completed"}:
        return DEFAULT_BILLING_PLAN_ID
    return normalize_billing_plan_id(payload.get("plan_id"))


def _subscription_active(subscription: Optional[Dict[str, Any]]) -> bool:
    payload = _coerce_dict(subscription)
    return str(payload.get("status") or "").strip().lower() in ACTIVE_SUBSCRIPTION_STATUSES


def _subscription_terminal(subscription: Optional[Dict[str, Any]]) -> bool:
    payload = _coerce_dict(subscription)
    return str(payload.get("status") or "").strip().lower() in TERMINAL_SUBSCRIPTION_STATUSES


def _plan_catalog_summary(current_plan_id: str) -> list[Dict[str, Any]]:
    price_map = _stripe_price_map()
    plans: list[Dict[str, Any]] = []
    for plan_id, label in PLAN_LABELS.items():
        plans.append(
            {
                "plan_id": plan_id,
                "label": label,
                "checkout_enabled": plan_id != DEFAULT_BILLING_PLAN_ID and bool(price_map.get(plan_id)),
                "price_configured": bool(price_map.get(plan_id)),
                "current": plan_id == current_plan_id,
            }
        )
    return plans


def _utc_month_start(now: Optional[datetime] = None) -> datetime.date:
    reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return reference.date().replace(day=1)


def _parse_run_timestamp(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return datetime.fromisoformat(raw).astimezone(timezone.utc)
    except Exception:
        return None


def _run_is_managed_cloud(run: Dict[str, Any]) -> bool:
    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    if str(metadata.get("runtime_attachment_kind") or "").strip().lower() == "self_hosted_business_node":
        return False
    execution_target = str(metadata.get("execution_target_selected") or "").strip().lower()
    runtime_mode = str(metadata.get("runtime_mode") or run.get("runtime_mode") or "").strip().lower()
    return execution_target == "cloud" or runtime_mode == "hosted_secure"


def _run_duration_minutes(run: Dict[str, Any], *, browser_only: bool = False) -> float:
    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    if browser_only:
        has_browser_signal = bool(
            metadata.get("browser_execution_binding")
            or metadata.get("browser_automation_policy")
            or run.get("browser_checkpoint")
        )
        if not has_browser_signal:
            return 0.0
    started_at = _parse_run_timestamp(run.get("started_at"))
    completed_at = _parse_run_timestamp(run.get("completed_at")) or _parse_run_timestamp(run.get("updated_at"))
    if started_at is None or completed_at is None or completed_at < started_at:
        return 0.0
    return round(max((completed_at - started_at).total_seconds(), 0.0) / 60.0, 6)


def _workspace_runtime_usage_summary(*, workspace_id: str, tenant_id: Optional[str]) -> Dict[str, float]:
    month_start = _utc_month_start()
    runs_by_id: Dict[str, Dict[str, Any]] = {}
    for item in list(run_state_repository.sync_list_run_archive(limit=1000) or []) + list(run_state_repository.sync_list_live_runs() or []):
        if not isinstance(item, dict):
            continue
        run_id = str(item.get("run_id") or "").strip()
        if not run_id:
            continue
        context = item.get("context") if isinstance(item.get("context"), dict) else {}
        run_workspace_id = str(item.get("workspace_id") or context.get("workspace_id") or "").strip()
        run_tenant_id = str(item.get("tenant_id") or context.get("tenant_id") or "").strip()
        if run_workspace_id != str(workspace_id or "").strip():
            continue
        if tenant_id and run_tenant_id and run_tenant_id != str(tenant_id or "").strip():
            continue
        completed_at = _parse_run_timestamp(item.get("completed_at")) or _parse_run_timestamp(item.get("updated_at"))
        if completed_at is None or completed_at.date() < month_start:
            continue
        if not _run_is_managed_cloud(item):
            continue
        runs_by_id[run_id] = item
    hosted_minutes = 0.0
    browser_minutes = 0.0
    for item in runs_by_id.values():
        hosted_minutes += _run_duration_minutes(item)
        browser_minutes += _run_duration_minutes(item, browser_only=True)
    return {
        "hosted_runtime_minutes_monthly": round(hosted_minutes, 6),
        "browser_compute_minutes_monthly": round(browser_minutes, 6),
    }


def billing_proxy_from_summary(summary: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    payload = dict(summary or {}) if isinstance(summary, dict) else {}
    account = _coerce_dict(payload.get("account"))
    subscription = _coerce_dict(payload.get("subscription"))
    effective_plan_id = _subscription_effective_plan(subscription)
    return {
        "workspace_id": str(payload.get("workspace_id") or "").strip() or None,
        "workspace_name": str(payload.get("workspace_name") or "").strip() or None,
        "provider": str(payload.get("provider") or STRIPE_PROVIDER).strip().lower() or STRIPE_PROVIDER,
        "effective_plan_id": effective_plan_id,
        "effective_plan_label": billing_plan_label(effective_plan_id),
        "subscription_status": str(subscription.get("status") or "inactive").strip().lower() or "inactive",
        "billing_email": str(account.get("billing_email") or "").strip().lower() or None,
        "provider_customer_id": str(account.get("provider_customer_id") or "").strip() or None,
        "current_period_end": str(subscription.get("current_period_end") or "").strip() or None,
    }


def workspace_billing_summary_for_workspace_id(
    workspace_id: str,
    *,
    workspace: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    resolved_workspace = dict(workspace) if isinstance(workspace, dict) else {}
    if not resolved_workspace:
        resolved_workspace = run_async_tool_call(control_plane_repository.get_workspace_by_id(workspace_id)) or {}
    if not isinstance(resolved_workspace, dict) or not str(resolved_workspace.get("workspace_id") or "").strip():
        raise HTTPException(status_code=404, detail="Workspace not found.")
    summary = run_async_tool_call(
        control_plane_repository.ensure_workspace_billing_defaults(
            str(resolved_workspace.get("workspace_id") or "").strip(),
            tenant_id=str(resolved_workspace.get("tenant_id") or "").strip() or None,
        )
    )
    payload = dict(summary or {})
    account = _coerce_dict(payload.get("account"))
    subscription = _coerce_dict(payload.get("subscription"))
    effective_plan_id = _subscription_effective_plan(subscription)
    workspace_plan_id = _explicit_workspace_billing_plan_id(resolved_workspace)
    if workspace_plan_id and effective_plan_id == DEFAULT_BILLING_PLAN_ID:
        effective_plan_id = workspace_plan_id
    tenant_id = str(payload.get("tenant_id") or resolved_workspace.get("tenant_id") or "").strip() or None
    usage_summary = run_async_tool_call(
        control_plane_repository.summarize_workspace_billing_usage(
            tenant_id=str(tenant_id or "").strip() or "default",
            workspace_id=str(payload.get("workspace_id") or resolved_workspace.get("workspace_id") or "").strip(),
        )
    ) if tenant_id else {}
    runtime_usage = _workspace_runtime_usage_summary(
        workspace_id=str(payload.get("workspace_id") or resolved_workspace.get("workspace_id") or "").strip(),
        tenant_id=tenant_id,
    )
    limits = {
        "max_specialists": 1 if effective_plan_id == "free" else 3,
        "hosted_runtime_minutes_monthly": 0 if effective_plan_id == "free" else 1500,
        "hosted_ai_enabled": _plan_allows_hosted_ai(effective_plan_id),
        "priority_sync_enabled": effective_plan_id == "pro",
        "local_gateway_enabled": True,
        "mini_apps_unlimited": True,
    }
    usage = {
        **_coerce_dict(usage_summary),
        **runtime_usage,
        "max_specialists": limits["max_specialists"],
        "specialists_in_use": len(
            run_async_tool_call(
                control_plane_repository.list_deployed_agents_for_workspace(
                    str(payload.get("workspace_id") or resolved_workspace.get("workspace_id") or "").strip(),
                    tenant_id=str(tenant_id or "").strip() or None,
                )
            ) or []
        ),
    }
    hosted_sage_ai = _hosted_sage_ai_credit_state(
        effective_plan_id=effective_plan_id,
        usage=usage,
        workspace=resolved_workspace,
    )
    return {
        "ok": True,
        "workspace_id": str(payload.get("workspace_id") or resolved_workspace.get("workspace_id") or "").strip(),
        "tenant_id": tenant_id,
        "workspace_name": str(payload.get("workspace_name") or resolved_workspace.get("name") or "").strip()
        or str(resolved_workspace.get("workspace_id") or "").strip(),
        "provider": STRIPE_PROVIDER,
        "configured": _stripe_configured(),
        "account": {
            "billing_email": str(account.get("billing_email") or "").strip().lower() or None,
            "provider_customer_id": str(account.get("provider_customer_id") or "").strip() or None,
            "default_currency": str(account.get("default_currency") or "usd").strip().lower() or "usd",
            "status": str(account.get("status") or "active").strip().lower() or "active",
            "metadata": _coerce_dict(account.get("metadata")),
        },
        "subscription": {
            "plan_id": normalize_billing_plan_id(subscription.get("plan_id")),
            "effective_plan_id": effective_plan_id,
            "label": PLAN_LABELS.get(effective_plan_id, effective_plan_id.title()),
            "status": str(subscription.get("status") or "active").strip().lower() or "active",
            "provider_subscription_id": str(subscription.get("provider_subscription_id") or "").strip() or None,
            "provider_price_id": str(subscription.get("provider_price_id") or "").strip() or None,
            "provider_product_id": str(subscription.get("provider_product_id") or "").strip() or None,
            "checkout_session_id": str(subscription.get("checkout_session_id") or "").strip() or None,
            "checkout_url": str(subscription.get("checkout_url") or "").strip() or None,
            "portal_url": str(subscription.get("portal_url") or "").strip() or None,
            "currency": str(subscription.get("currency") or "usd").strip().lower() or "usd",
            "billing_interval": str(subscription.get("billing_interval") or "").strip().lower() or None,
            "current_period_start": _coerce_int(subscription.get("current_period_start")),
            "current_period_end": _coerce_int(subscription.get("current_period_end")),
            "cancel_at_period_end": bool(subscription.get("cancel_at_period_end")),
            "canceled_at": _coerce_int(subscription.get("canceled_at")),
            "trial_ends_at": _coerce_int(subscription.get("trial_ends_at")),
            "metadata": _coerce_dict(subscription.get("metadata")),
            "active": _subscription_active(subscription),
            "terminal": _subscription_terminal(subscription),
        },
        "portal_available": bool(account.get("provider_customer_id")) and _stripe_configured(),
        "plans": _plan_catalog_summary(effective_plan_id),
        "limits": limits,
        "usage": usage,
        "hosted_sage_ai": hosted_sage_ai,
    }


def resolve_workspace_billing_plan_id(
    *,
    workspace_id: str,
    workspace: Optional[Dict[str, Any]] = None,
) -> str:
    summary = workspace_billing_summary_for_workspace_id(workspace_id, workspace=workspace)
    subscription = _coerce_dict(summary.get("subscription"))
    return normalize_billing_plan_id(subscription.get("effective_plan_id"))


def create_workspace_checkout_session(
    *,
    workspace_id: str,
    plan_id: str,
    success_url: Optional[str] = None,
    cancel_url: Optional[str] = None,
    billing_email: Optional[str] = None,
) -> Dict[str, Any]:
    normalized_plan_id = normalize_billing_plan_id(plan_id)
    if normalized_plan_id == DEFAULT_BILLING_PLAN_ID:
        raise HTTPException(status_code=400, detail="Free does not require checkout.")
    if normalized_plan_id != "pro":
        raise HTTPException(status_code=400, detail="Only the Pro plan is available for checkout.")
    price_id = _stripe_price_map().get(normalized_plan_id)
    if not price_id:
        raise HTTPException(status_code=400, detail=f"Billing plan '{normalized_plan_id}' is not configured.")
    summary = workspace_billing_summary_for_workspace_id(workspace_id)
    account = _coerce_dict(summary.get("account"))
    _enforce_billing_control_plane_decision(
        operation="workspace_billing_plan_update",
        record_type="workspace_billing_subscription",
        tenant_id=str(summary.get("tenant_id") or "").strip() or None,
        workspace_id=workspace_id,
        target_status=normalized_plan_id,
        idempotency_key=f"{workspace_id}:checkout:{normalized_plan_id}",
    )
    form_fields: Dict[str, Any] = {
        "mode": "subscription",
        "success_url": str(success_url or _billing_success_url(workspace_id)).strip(),
        "cancel_url": str(cancel_url or _billing_cancel_url(workspace_id)).strip(),
        "line_items[0][price]": price_id,
        "line_items[0][quantity]": 1,
        "client_reference_id": workspace_id,
        "metadata[workspace_id]": workspace_id,
        "metadata[plan_id]": normalized_plan_id,
        "subscription_data[metadata][workspace_id]": workspace_id,
        "subscription_data[metadata][plan_id]": normalized_plan_id,
    }
    customer_id = str(account.get("provider_customer_id") or "").strip()
    if customer_id:
        form_fields["customer"] = customer_id
    elif billing_email:
        form_fields["customer_email"] = str(billing_email or "").strip().lower()
    response = _stripe_api_request("/checkout/sessions", form_fields)
    checkout_session_id = str(response.get("id") or "").strip()
    checkout_url = str(response.get("url") or "").strip()
    if not checkout_session_id or not checkout_url:
        raise HTTPException(status_code=502, detail="Stripe checkout session did not include a usable URL.")
    run_async_tool_call(
        control_plane_repository.upsert_workspace_billing_subscription(
            workspace_id,
            plan_id=normalized_plan_id,
            status="checkout_pending",
            provider_customer_id=str(response.get("customer") or "").strip() or None,
            checkout_session_id=checkout_session_id,
            checkout_url=checkout_url,
            currency=str(response.get("currency") or account.get("default_currency") or "usd").strip().lower() or "usd",
            metadata={"source": "stripe_checkout"},
        )
    )
    return {
        "ok": True,
        "provider": STRIPE_PROVIDER,
        "plan_id": normalized_plan_id,
        "checkout_session_id": checkout_session_id,
        "checkout_url": checkout_url,
    }


def create_workspace_portal_session(
    *,
    workspace_id: str,
    return_url: Optional[str] = None,
) -> Dict[str, Any]:
    summary = workspace_billing_summary_for_workspace_id(workspace_id)
    account = _coerce_dict(summary.get("account"))
    customer_id = str(account.get("provider_customer_id") or "").strip()
    if not customer_id:
        raise HTTPException(status_code=400, detail="Workspace does not have a billable customer account yet.")
    subscription = _coerce_dict(summary.get("subscription"))
    _enforce_billing_control_plane_decision(
        operation="billing_update",
        record_type="workspace_billing_portal",
        tenant_id=str(summary.get("tenant_id") or "").strip() or None,
        workspace_id=workspace_id,
        target_status=str(subscription.get("status") or "active").strip().lower() or "active",
        idempotency_key=f"{workspace_id}:billing_portal:{customer_id}",
    )
    response = _stripe_api_request(
        "/billing_portal/sessions",
        {
            "customer": customer_id,
            "return_url": str(return_url or _billing_portal_return_url(workspace_id)).strip(),
        },
    )
    portal_url = str(response.get("url") or "").strip()
    if not portal_url:
        raise HTTPException(status_code=502, detail="Stripe portal session did not include a usable URL.")
    run_async_tool_call(
        control_plane_repository.upsert_workspace_billing_subscription(
            workspace_id,
            plan_id=normalize_billing_plan_id(subscription.get("plan_id")),
            status=str(subscription.get("status") or "active").strip().lower() or "active",
            provider_subscription_id=str(subscription.get("provider_subscription_id") or "").strip() or None,
            provider_price_id=str(subscription.get("provider_price_id") or "").strip() or None,
            provider_product_id=str(subscription.get("provider_product_id") or "").strip() or None,
            provider_customer_id=customer_id,
            checkout_session_id=str(subscription.get("checkout_session_id") or "").strip() or None,
            checkout_url=str(subscription.get("checkout_url") or "").strip() or None,
            portal_url=portal_url,
            currency=str(subscription.get("currency") or "usd").strip().lower() or "usd",
            billing_interval=str(subscription.get("billing_interval") or "").strip().lower() or None,
            current_period_start=_coerce_int(subscription.get("current_period_start")),
            current_period_end=_coerce_int(subscription.get("current_period_end")),
            cancel_at_period_end=bool(subscription.get("cancel_at_period_end")),
            canceled_at=_coerce_int(subscription.get("canceled_at")),
            trial_ends_at=_coerce_int(subscription.get("trial_ends_at")),
            metadata=_coerce_dict(subscription.get("metadata")),
        )
    )
    return {
        "ok": True,
        "provider": STRIPE_PROVIDER,
        "portal_url": portal_url,
    }


def create_credit_purchase_checkout_session(
    *,
    workspace_id: str,
    amount_usd: float,
    success_url: Optional[str] = None,
    cancel_url: Optional[str] = None,
    billing_email: Optional[str] = None,
) -> Dict[str, Any]:
    parsed = _coerce_float(amount_usd)
    if parsed is None or parsed < _MIN_CREDIT_PURCHASE_USD:
        raise HTTPException(status_code=400, detail="Credit purchase amount must be at least $1.")
    amount_usd = min(_MAX_CREDIT_PURCHASE_USD, round(float(parsed), 2))
    unit_amount_cents = int(round(amount_usd * 100))
    summary = workspace_billing_summary_for_workspace_id(workspace_id)
    account = _coerce_dict(summary.get("account"))
    _enforce_billing_control_plane_decision(
        operation="billing_update",
        record_type="workspace_billing_credit_checkout",
        tenant_id=str(summary.get("tenant_id") or "").strip() or None,
        workspace_id=workspace_id,
        target_status="credit_purchase",
        idempotency_key=f"{workspace_id}:credit_checkout:{unit_amount_cents}",
    )
    form_fields: Dict[str, Any] = {
        "mode": "payment",
        "success_url": str(success_url or _billing_credit_success_url(workspace_id)).strip(),
        "cancel_url": str(cancel_url or _billing_credit_cancel_url(workspace_id)).strip(),
        "client_reference_id": workspace_id,
        "metadata[workspace_id]": workspace_id,
        "metadata[purchase_kind]": "credits",
        "metadata[amount_usd]": str(amount_usd),
        "line_items[0][price_data][currency]": str(account.get("default_currency") or "usd").strip().lower() or "usd",
        "line_items[0][price_data][product_data][name]": "Hosted Sage AI Credits",
        "line_items[0][price_data][product_data][description]": f"{int(round(amount_usd * HOSTED_SAGE_AI_CREDITS_PER_USD))} credits for hosted Sage AI usage",
        "line_items[0][price_data][unit_amount]": unit_amount_cents,
        "line_items[0][quantity]": 1,
    }
    customer_id = str(account.get("provider_customer_id") or "").strip()
    if customer_id:
        form_fields["customer"] = customer_id
    elif billing_email:
        form_fields["customer_email"] = str(billing_email or "").strip().lower()
    response = _stripe_api_request("/checkout/sessions", form_fields)
    checkout_session_id = str(response.get("id") or "").strip()
    checkout_url = str(response.get("url") or "").strip()
    if not checkout_session_id or not checkout_url:
        raise HTTPException(status_code=502, detail="Stripe checkout session did not include a usable URL.")
    run_async_tool_call(
        control_plane_repository.upsert_workspace_billing_subscription(
            workspace_id,
            plan_id=normalize_billing_plan_id(summary.get("subscription", {}).get("plan_id")),
            status="checkout_pending",
            provider_customer_id=str(response.get("customer") or "").strip() or customer_id or None,
            checkout_session_id=checkout_session_id,
            checkout_url=checkout_url,
            currency=str(response.get("currency") or account.get("default_currency") or "usd").strip().lower() or "usd",
            metadata={"source": "stripe_credit_checkout", "purchase_kind": "credits", "amount_usd": amount_usd},
        )
    )
    return {
        "ok": True,
        "provider": STRIPE_PROVIDER,
        "purchase_kind": "credits",
        "amount_usd": amount_usd,
        "credits": int(round(amount_usd * HOSTED_SAGE_AI_CREDITS_PER_USD)),
        "checkout_session_id": checkout_session_id,
        "checkout_url": checkout_url,
    }


def credit_balance_for_workspace(workspace_id: str) -> Dict[str, Any]:
    workspace = run_async_tool_call(control_plane_repository.get_workspace_by_id(workspace_id)) or {}
    metadata = _workspace_billing_metadata(workspace)
    balance_usd = _credit_balance_from_metadata(metadata)
    transactions = _credit_transactions_from_metadata(metadata)
    return {
        "ok": True,
        "workspace_id": workspace_id,
        "credit_balance_usd": balance_usd,
        "credit_balance_credits": int(round(balance_usd * HOSTED_SAGE_AI_CREDITS_PER_USD)),
        "transactions": transactions,
    }


def _credit_history_timestamp(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat().replace("+00:00", "Z")
        except Exception:
            return None
    text = str(value or "").strip()
    return text or None


def _credit_history_usage_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    metadata = _coerce_dict(entry.get("metadata"))
    thread_id = str(entry.get("thread_id") or metadata.get("thread_id") or "").strip()
    request_id = str(entry.get("request_id") or metadata.get("request_id") or "").strip()
    estimated_cost_usd = max(0.0, round(float(entry.get("estimated_cost_usd") or 0.0), 6))
    credits = -int(round(estimated_cost_usd * HOSTED_SAGE_AI_CREDITS_PER_USD))
    public_tier = str(metadata.get("public_tier") or "").strip()
    label = str(metadata.get("chat_title") or metadata.get("thread_title") or "").strip()
    if not label:
        label = "Sage" if thread_id in {"", "primary"} else f"Sage chat {thread_id[:8]}"
    return {
        "id": str(entry.get("id") or request_id or thread_id or f"usage-{time.time()}").strip(),
        "kind": "usage",
        "source": "sage_direct_chat",
        "label": label,
        "thread_id": thread_id or None,
        "request_id": request_id or None,
        "credits": credits,
        "amount_usd": -estimated_cost_usd,
        "provider": str(entry.get("provider") or "").strip().lower() or None,
        "model": str(entry.get("model") or "").strip() or None,
        "public_tier": public_tier or None,
        "total_tokens": int(entry.get("total_tokens") or 0),
        "created_at": _credit_history_timestamp(entry.get("completed_at") or entry.get("updated_at") or entry.get("created_at")),
    }


def _credit_history_transaction_entry(transaction: Dict[str, Any], index: int) -> Dict[str, Any]:
    kind = str(transaction.get("kind") or "").strip().lower() or "transaction"
    credits = int(round(float(transaction.get("credits") or 0)))
    amount_usd = _coerce_float(transaction.get("amount_usd"))
    if amount_usd is None:
        amount_usd = round(credits / HOSTED_SAGE_AI_CREDITS_PER_USD, 6) if credits else 0.0
    label = "Bonus for new users" if kind == "bonus" else "Credit purchase" if kind == "purchase" else "Credit adjustment"
    if kind == "usage_debit":
        label = "Hosted Sage overage"
    return {
        "id": str(transaction.get("id") or transaction.get("request_id") or f"transaction-{index}").strip(),
        "kind": kind,
        "source": str(transaction.get("source") or "credits").strip() or "credits",
        "label": label,
        "thread_id": None,
        "request_id": str(transaction.get("request_id") or "").strip() or None,
        "credits": credits,
        "amount_usd": round(float(amount_usd or 0.0), 6),
        "provider": None,
        "model": None,
        "public_tier": None,
        "total_tokens": 0,
        "created_at": _credit_history_timestamp(transaction.get("created_at")),
    }


def credit_usage_history_for_workspace(workspace_id: str, *, limit: int = 50) -> Dict[str, Any]:
    clean_workspace_id = str(workspace_id or "").strip()
    safe_limit = max(1, min(int(limit or 50), 200))
    workspace = run_async_tool_call(control_plane_repository.get_workspace_by_id(clean_workspace_id)) or {}
    if not isinstance(workspace, dict) or not str(workspace.get("workspace_id") or "").strip():
        raise HTTPException(status_code=404, detail="Workspace not found.")
    tenant_id = str(workspace.get("tenant_id") or "").strip()
    if not tenant_id:
        raise HTTPException(status_code=404, detail="Workspace tenant not found.")
    summary = workspace_billing_summary_for_workspace_id(clean_workspace_id, workspace=workspace)
    metadata = _workspace_billing_metadata(workspace)
    usage_entries = run_async_tool_call(
        control_plane_repository.list_workspace_hosted_ai_monthly_cost_ledger_entries(
            tenant_id=tenant_id,
            workspace_id=clean_workspace_id,
            limit=safe_limit,
        )
    ) or []
    rows = [
        _credit_history_usage_entry(entry)
        for entry in usage_entries
        if isinstance(entry, dict)
    ]
    rows.extend(
        _credit_history_transaction_entry(transaction, index)
        for index, transaction in enumerate(_credit_transactions_from_metadata(metadata))
        if isinstance(transaction, dict)
    )
    rows.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return {
        "ok": True,
        "workspace_id": clean_workspace_id,
        "tenant_id": tenant_id,
        "plan": _coerce_dict(summary.get("subscription")),
        "hosted_sage_ai": _coerce_dict(summary.get("hosted_sage_ai")),
        "items": rows[:safe_limit],
        "history_source": "workspace_hosted_ai_monthly_cost_ledger",
        "per_chat_available": bool(usage_entries),
    }


def _usage_api_timestamp(value: Any) -> Optional[str]:
    return _credit_history_timestamp(value)


def _usage_api_label(event: Dict[str, Any], *, fallback: Optional[str] = None) -> str:
    metadata = _coerce_dict(event.get("metadata"))
    label = str(
        metadata.get("label")
        or metadata.get("thread_title")
        or metadata.get("chat_title")
        or fallback
        or ""
    ).strip()
    if label:
        return label
    surface = str(event.get("surface") or event.get("source_surface") or "").strip().lower()
    credit_type = str(event.get("credit_type") or "").strip().lower()
    if credit_type == "computer_runtime":
        return "Cloud Computer session"
    if surface == "studio":
        agent_id = str(event.get("agent_id") or "").strip()
        return f"Studio agent {agent_id[:8]}" if agent_id else "Studio agent"
    if surface == "mini_app":
        app_id = str(event.get("app_id") or "").strip()
        return f"Mini-app {app_id[:8]}" if app_id else "Mini-app"
    return "Sage"


def _usage_api_item_from_event(
    event: Dict[str, Any],
    *,
    item_id: Any,
    created_at: Any = None,
    fallback_label: Optional[str] = None,
    ledger_source: str,
) -> Dict[str, Any]:
    provider_usage = _coerce_dict(event.get("provider_usage"))
    payer = str(event.get("payer") or "").strip() or "platform_credits"
    credit_type = str(event.get("credit_type") or "").strip().lower() or "ai_tokens"
    platform_cost_usd = _coerce_float(event.get("platform_cost_usd")) or 0.0
    provider_reported_cost = _coerce_float(event.get("provider_reported_cost"))
    credits_debited = _coerce_float(event.get("credits_debited")) or 0.0
    return {
        "id": str(item_id or event.get("id") or f"usage-{time.time()}").strip(),
        "surface": str(event.get("surface") or "").strip().lower() or None,
        "source_surface": str(event.get("source_surface") or event.get("surface") or "").strip().lower() or None,
        "payer": payer,
        "credit_type": credit_type,
        "label": _usage_api_label(event, fallback=fallback_label),
        "provider": str(event.get("provider") or "").strip().lower() or None,
        "model": str(event.get("model") or "").strip() or None,
        "runtime_target": str(event.get("runtime_target") or "").strip() or None,
        "workspace_id": str(event.get("workspace_id") or "").strip() or None,
        "user_id": str(event.get("user_id") or "").strip() or None,
        "thread_id": str(event.get("thread_id") or "").strip() or None,
        "run_id": str(event.get("run_id") or "").strip() or None,
        "agent_id": str(event.get("agent_id") or "").strip() or None,
        "app_id": str(event.get("app_id") or "").strip() or None,
        "provider_usage": provider_usage,
        "platform_cost_usd": round(float(platform_cost_usd), 6),
        "provider_reported_cost": round(float(provider_reported_cost), 6) if provider_reported_cost is not None else None,
        "provider_reported_currency": str(event.get("provider_reported_currency") or "").strip() or None,
        "credits_debited": round(float(credits_debited), 6),
        "estimation_mode": str(event.get("estimation_mode") or "").strip() or None,
        "empyralis_credits_used": payer == "platform_credits" and credits_debited > 0,
        "ledger_source": ledger_source,
        "created_at": _usage_api_timestamp(created_at or event.get("created_at")),
    }


def _usage_api_event_from_hosted_ledger_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    metadata = _coerce_dict(entry.get("metadata"))
    unified = _coerce_dict(metadata.get("unified_credit_ledger_event"))
    if unified:
        return {
            **unified,
            "metadata": metadata,
            "provider": unified.get("provider") or entry.get("provider"),
            "model": unified.get("model") or entry.get("model"),
            "thread_id": unified.get("thread_id") or entry.get("thread_id"),
            "created_at": unified.get("created_at") or entry.get("completed_at") or entry.get("updated_at") or entry.get("created_at"),
        }
    source_surface = str(entry.get("source_surface") or metadata.get("source_surface") or "sage_direct_chat").strip().lower()
    surface = str(metadata.get("surface") or source_surface).strip().lower()
    if surface.startswith("sage"):
        surface = "sage"
    elif surface.startswith("studio") or surface in {"deployed_agent", "deployed_agent_channel"}:
        surface = "studio"
    elif surface.startswith("mini"):
        surface = "mini_app"
    estimated_cost = _coerce_float(entry.get("estimated_cost_usd")) or 0.0
    return {
        "surface": surface or "sage",
        "source_surface": source_surface,
        "payer": "platform_credits",
        "credit_type": "ai_tokens",
        "provider": entry.get("provider"),
        "model": entry.get("model"),
        "workspace_id": entry.get("workspace_id"),
        "thread_id": entry.get("thread_id"),
        "run_id": metadata.get("run_id"),
        "agent_id": metadata.get("deployed_agent_id") or metadata.get("agent_id"),
        "app_id": metadata.get("app_id"),
        "provider_usage": {
            "prompt_tokens": int(entry.get("prompt_tokens") or 0),
            "completion_tokens": int(entry.get("completion_tokens") or 0),
            "total_tokens": int(entry.get("total_tokens") or 0),
        },
        "platform_cost_usd": estimated_cost,
        "provider_reported_cost": estimated_cost,
        "provider_reported_currency": "USD",
        "credits_debited": int(round(estimated_cost * HOSTED_SAGE_AI_CREDITS_PER_USD)),
        "estimation_mode": metadata.get("estimation_mode") or "provider_usage_exact",
        "created_at": entry.get("completed_at") or entry.get("updated_at") or entry.get("created_at"),
        "metadata": metadata,
    }


def _usage_api_summary(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_surface: Dict[str, Dict[str, Any]] = {}
    by_credit_type: Dict[str, Dict[str, Any]] = {}
    total_platform_cost_usd = 0.0
    total_credits_debited = 0.0
    for item in items:
        platform_cost = float(item.get("platform_cost_usd") or 0.0)
        credits = float(item.get("credits_debited") or 0.0)
        total_platform_cost_usd += platform_cost
        total_credits_debited += credits
        for bucket, key in ((by_surface, str(item.get("surface") or "unknown")), (by_credit_type, str(item.get("credit_type") or "unknown"))):
            current = bucket.setdefault(key, {"count": 0, "platform_cost_usd": 0.0, "credits_debited": 0.0})
            current["count"] += 1
            current["platform_cost_usd"] = round(float(current["platform_cost_usd"]) + platform_cost, 6)
            current["credits_debited"] = round(float(current["credits_debited"]) + credits, 6)
    return {
        "count": len(items),
        "total_platform_cost_usd": round(total_platform_cost_usd, 6),
        "total_credits_debited": round(total_credits_debited, 6),
        "by_surface": by_surface,
        "by_credit_type": by_credit_type,
    }


def _usage_api_item_matches_filters(item: Dict[str, Any], filters: Dict[str, Optional[str]]) -> bool:
    for key, raw_value in filters.items():
        value = str(raw_value or "").strip()
        if not value:
            continue
        item_value = str(item.get(key) or "").strip()
        if key in {"surface", "credit_type", "payer"}:
            item_value = item_value.lower()
            value = value.lower()
        if item_value != value:
            return False
    return True


def unified_credit_usage_for_workspace(
    workspace_id: str,
    *,
    limit: int = 50,
    surface: Optional[str] = None,
    credit_type: Optional[str] = None,
    payer: Optional[str] = None,
    agent_id: Optional[str] = None,
    app_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    clean_workspace_id = str(workspace_id or "").strip()
    safe_limit = max(1, min(int(limit or 50), 200))
    workspace = run_async_tool_call(control_plane_repository.get_workspace_by_id(clean_workspace_id)) or {}
    if not isinstance(workspace, dict) or not str(workspace.get("workspace_id") or "").strip():
        raise HTTPException(status_code=404, detail="Workspace not found.")
    tenant_id = str(workspace.get("tenant_id") or "").strip()
    if not tenant_id:
        raise HTTPException(status_code=404, detail="Workspace tenant not found.")
    credit_ledger_entries = run_async_tool_call(
        control_plane_repository.list_credit_ledger_events(
            tenant_id=tenant_id,
            workspace_id=clean_workspace_id,
            surface=surface,
            credit_type=credit_type,
            payer=payer,
            agent_id=agent_id,
            app_id=app_id,
            thread_id=thread_id,
            run_id=run_id,
            limit=safe_limit,
        )
    ) or []
    usage_entries = run_async_tool_call(
        control_plane_repository.list_workspace_hosted_ai_monthly_cost_ledger_entries(
            tenant_id=tenant_id,
            workspace_id=clean_workspace_id,
            limit=safe_limit,
        )
    ) or []
    activity_entries = run_async_tool_call(
        control_plane_repository.list_activity_ledger_events(
            tenant_id=tenant_id,
            workspace_id=clean_workspace_id,
            limit=safe_limit,
        )
    ) or []
    items: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_sources: set[tuple[str, str]] = set()
    active_filters = {
        "surface": str(surface or "").strip().lower() or None,
        "credit_type": str(credit_type or "").strip().lower() or None,
        "payer": str(payer or "").strip() or None,
        "agent_id": str(agent_id or "").strip() or None,
        "app_id": str(app_id or "").strip() or None,
        "thread_id": str(thread_id or "").strip() or None,
        "run_id": str(run_id or "").strip() or None,
    }
    for row in credit_ledger_entries:
        if not isinstance(row, dict):
            continue
        item = _usage_api_item_from_event(
            row,
            item_id=row.get("id"),
            created_at=row.get("created_at"),
            fallback_label=str(_coerce_dict(row.get("metadata")).get("label") or "").strip() or None,
            ledger_source="credit_ledger_events",
        )
        if not _usage_api_item_matches_filters(item, active_filters):
            continue
        source_key = (str(row.get("source_table") or "").strip(), str(row.get("source_event_id") or "").strip())
        if all(source_key):
            seen_sources.add(source_key)
        seen_ids.add(str(item.get("id") or ""))
        items.append(item)
    for entry in usage_entries:
        if not isinstance(entry, dict):
            continue
        source_key = ("workspace_hosted_ai_monthly_cost_ledger", str(entry.get("id") or entry.get("request_id") or "").strip())
        if all(source_key) and source_key in seen_sources:
            continue
        event = _usage_api_event_from_hosted_ledger_entry(entry)
        item = _usage_api_item_from_event(
            event,
            item_id=entry.get("id") or entry.get("request_id"),
            created_at=entry.get("completed_at") or entry.get("updated_at") or entry.get("created_at"),
            fallback_label=str(_coerce_dict(entry.get("metadata")).get("thread_title") or "").strip() or None,
            ledger_source="workspace_hosted_ai_monthly_cost_ledger",
        )
        if not _usage_api_item_matches_filters(item, active_filters):
            continue
        seen_ids.add(str(item.get("id") or ""))
        if all(source_key):
            seen_sources.add(source_key)
        items.append(item)
    for row in activity_entries:
        if not isinstance(row, dict):
            continue
        source_key = ("activity_ledger_events", str(row.get("id") or "").strip())
        if all(source_key) and source_key in seen_sources:
            continue
        metadata = _coerce_dict(row.get("metadata"))
        event = _coerce_dict(metadata.get("unified_credit_ledger_event"))
        if not event:
            continue
        if str(event.get("credit_type") or "").strip().lower() == "ai_tokens" and str(event.get("payer") or "").strip() == "platform_credits":
            continue
        item = _usage_api_item_from_event(
            {**event, "metadata": metadata},
            item_id=row.get("id"),
            created_at=row.get("created_at"),
            fallback_label=str(row.get("title") or row.get("summary") or "").strip() or None,
            ledger_source="activity_ledger_events",
        )
        if not _usage_api_item_matches_filters(item, active_filters):
            continue
        item_id = str(item.get("id") or "")
        if item_id in seen_ids:
            continue
        if all(source_key):
            seen_sources.add(source_key)
        seen_ids.add(item_id)
        items.append(item)
    items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    items = items[:safe_limit]
    return {
        "ok": True,
        "workspace_id": clean_workspace_id,
        "tenant_id": tenant_id,
        "items": items,
        "summary": _usage_api_summary(items),
        "filters": active_filters,
        "history_sources": ["credit_ledger_events", "workspace_hosted_ai_monthly_cost_ledger", "activity_ledger_events"],
    }


def _stripe_signature_payload(timestamp: str, body: bytes) -> bytes:
    return f"{timestamp}.{body.decode('utf-8')}".encode("utf-8")


def verify_stripe_webhook_signature(body: bytes, signature_header: str) -> None:
    secret = _stripe_webhook_secret()
    if not secret:
        raise HTTPException(status_code=503, detail="Stripe webhook secret is not configured.")
    parts: Dict[str, str] = {}
    for item in str(signature_header or "").split(","):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        parts[key.strip()] = value.strip()
    timestamp = parts.get("t")
    signature = parts.get("v1")
    if not timestamp or not signature:
        raise HTTPException(status_code=400, detail="Stripe signature is invalid.")
    try:
        timestamp_value = int(timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Stripe signature timestamp is invalid.") from exc
    if abs(int(time.time()) - timestamp_value) > STRIPE_WEBHOOK_TOLERANCE_SECONDS:
        raise HTTPException(status_code=400, detail="Stripe signature timestamp is outside the tolerance window.")
    expected = hmac.new(
        secret.encode("utf-8"),
        _stripe_signature_payload(timestamp, body),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=400, detail="Stripe signature verification failed.")


def _workspace_id_from_webhook_object(payload: Dict[str, Any]) -> Optional[str]:
    metadata = _coerce_dict(payload.get("metadata"))
    token = (
        str(metadata.get("workspace_id") or "").strip()
        or str(payload.get("client_reference_id") or "").strip()
    )
    return token or None


def _plan_id_from_subscription_object(payload: Dict[str, Any]) -> str:
    metadata = _coerce_dict(payload.get("metadata"))
    metadata_plan = str(metadata.get("plan_id") or metadata.get("target_plan") or "").strip()
    if metadata_plan:
        return normalize_billing_plan_id(metadata_plan)
    price_to_plan = _price_to_plan_map()
    items = (((payload.get("items") or {}).get("data")) if isinstance(payload.get("items"), dict) else None) or []
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            price = _coerce_dict(item.get("price"))
            price_id = str(price.get("id") or "").strip()
            if price_id and price_id in price_to_plan:
                return price_to_plan[price_id]
    return DEFAULT_BILLING_PLAN_ID


def apply_stripe_webhook_event(event: Dict[str, Any]) -> Dict[str, Any]:
    event_type = str(event.get("type") or "").strip()
    data = _coerce_dict(event.get("data"))
    obj = _coerce_dict(data.get("object"))
    if not event_type or not obj:
        raise HTTPException(status_code=400, detail="Stripe webhook payload is invalid.")

    if event_type == "checkout.session.completed":
        workspace_id = _workspace_id_from_webhook_object(obj)
        if not workspace_id:
            return {"ok": True, "ignored": True, "reason": "missing_workspace_id"}
        customer_details = _coerce_dict(obj.get("customer_details"))
        run_async_tool_call(
            control_plane_repository.upsert_workspace_billing_account(
                workspace_id,
                billing_email=str(customer_details.get("email") or "").strip().lower() or None,
                provider_customer_id=str(obj.get("customer") or "").strip() or None,
                metadata={"source": "stripe_webhook"},
            )
        )
        session_mode = str(obj.get("mode") or "").strip().lower()
        metadata = _coerce_dict(obj.get("metadata"))
        purchase_kind = str(metadata.get("purchase_kind") or "").strip().lower()

        if session_mode == "payment" and purchase_kind == "credits":
            amount_usd = _coerce_float(metadata.get("amount_usd"))
            if amount_usd is not None and amount_usd > 0:
                workspace = run_async_tool_call(
                    control_plane_repository.get_workspace_by_id(workspace_id)
                ) or {}
                ws_metadata = _workspace_billing_metadata(workspace)
                current_balance = _credit_balance_from_metadata(ws_metadata)
                new_balance = round(current_balance + amount_usd, 6)
                transactions = _credit_transactions_from_metadata(ws_metadata)
                transactions.append(
                    {
                        "kind": "purchase",
                        "amount_usd": amount_usd,
                        "credits": int(round(amount_usd * HOSTED_SAGE_AI_CREDITS_PER_USD)),
                        "checkout_session_id": str(obj.get("id") or "").strip() or None,
                        "provider": STRIPE_PROVIDER,
                        "created_at": int(time.time()),
                    }
                )
                run_async_tool_call(
                    control_plane_repository.update_workspace_admin_defaults_metadata(
                        workspace_id,
                        metadata={
                            **_coerce_dict(ws_metadata),
                            "credit_balance_usd": new_balance,
                            "credit_transactions": transactions,
                        },
                    )
                )
            run_async_tool_call(
                control_plane_repository.upsert_workspace_billing_subscription(
                    workspace_id,
                    plan_id=normalize_billing_plan_id(
                        _coerce_dict(
                            run_async_tool_call(
                                control_plane_repository.get_workspace_billing_summary(workspace_id)
                            ) or {}
                        ).get("subscription", {}).get("plan_id")
                    ),
                    status="checkout_completed",
                    provider_customer_id=str(obj.get("customer") or "").strip() or None,
                    checkout_session_id=str(obj.get("id") or "").strip() or None,
                    metadata={"source": "stripe_webhook", "purchase_kind": "credits", "amount_usd": amount_usd},
                )
            )
            return {
                "ok": True,
                "event_type": event_type,
                "workspace_id": workspace_id,
                "purchase_kind": "credits",
                "amount_usd": amount_usd,
            }

        target_plan = normalize_billing_plan_id(
            metadata.get("plan_id") or metadata.get("target_plan")
        )
        run_async_tool_call(
            control_plane_repository.upsert_workspace_billing_subscription(
                workspace_id,
                plan_id=target_plan,
                status="checkout_completed",
                provider_customer_id=str(obj.get("customer") or "").strip() or None,
                checkout_session_id=str(obj.get("id") or "").strip() or None,
                metadata={"source": "stripe_webhook"},
            )
        )
        return {"ok": True, "event_type": event_type, "workspace_id": workspace_id}

    if event_type.startswith("customer.subscription."):
        workspace_id = _workspace_id_from_webhook_object(obj)
        if not workspace_id:
            return {"ok": True, "ignored": True, "reason": "missing_workspace_id"}
        customer_id = str(obj.get("customer") or "").strip() or None
        billing_email = str(_coerce_dict(obj.get("customer_details")).get("email") or "").strip().lower() or None
        if customer_id or billing_email:
            run_async_tool_call(
                control_plane_repository.upsert_workspace_billing_account(
                    workspace_id,
                    billing_email=billing_email,
                    provider_customer_id=customer_id,
                    metadata={"source": "stripe_webhook"},
                )
            )
        status = str(obj.get("status") or "").strip().lower() or "active"
        items = (((obj.get("items") or {}).get("data")) if isinstance(obj.get("items"), dict) else None) or []
        first_item = items[0] if isinstance(items, list) and items else {}
        price = _coerce_dict(_coerce_dict(first_item).get("price"))
        run_async_tool_call(
            control_plane_repository.upsert_workspace_billing_subscription(
                workspace_id,
                plan_id=_plan_id_from_subscription_object(obj),
                status=status,
                provider_subscription_id=str(obj.get("id") or "").strip() or None,
                provider_price_id=str(price.get("id") or "").strip() or None,
                provider_product_id=str(price.get("product") or "").strip() or None,
                provider_customer_id=customer_id,
                currency=str(obj.get("currency") or price.get("currency") or "usd").strip().lower() or "usd",
                billing_interval=str(price.get("recurring", {}).get("interval") if isinstance(price.get("recurring"), dict) else "" or "").strip().lower() or None,
                current_period_start=_coerce_int(obj.get("current_period_start")),
                current_period_end=_coerce_int(obj.get("current_period_end")),
                cancel_at_period_end=bool(obj.get("cancel_at_period_end")),
                canceled_at=_coerce_int(obj.get("canceled_at")),
                trial_ends_at=_coerce_int(obj.get("trial_end")),
                metadata={"source": "stripe_webhook"},
            )
        )
        if status in TERMINAL_SUBSCRIPTION_STATUSES:
            workspace = run_async_tool_call(
                control_plane_repository.get_workspace_by_id(workspace_id)
            ) or {}
            ws_metadata = _coerce_dict(_coerce_dict(workspace).get("metadata"))
            billing_metadata = _coerce_dict(ws_metadata.get("billing"))
            next_billing = {k: v for k, v in billing_metadata.items() if k not in {"billing_plan", "plan", "plan_id", "plan_tier"}}
            next_metadata = {
                **ws_metadata,
                "billing": next_billing,
            }
            run_async_tool_call(
                control_plane_repository.update_workspace_profile(
                    workspace_id,
                    updates={
                        "name": str(workspace.get("name") or "").strip() or workspace_id,
                        "workspace_type": str(workspace.get("workspace_type") or workspace.get("kind") or "personal"),
                        "metadata": next_metadata,
                    },
                )
            )
        return {"ok": True, "event_type": event_type, "workspace_id": workspace_id}

    return {"ok": True, "ignored": True, "reason": "unsupported_event_type", "event_type": event_type}


def handle_stripe_webhook(body: bytes, signature_header: str) -> Dict[str, Any]:
    verify_stripe_webhook_signature(body, signature_header)
    try:
        event = json.loads(body.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Stripe webhook payload is invalid JSON.") from exc
    if not isinstance(event, dict):
        raise HTTPException(status_code=400, detail="Stripe webhook payload is invalid.")
    return apply_stripe_webhook_event(event)
