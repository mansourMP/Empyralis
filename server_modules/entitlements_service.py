from __future__ import annotations

from dataclasses import asdict, dataclass
import os
import time
from typing import Any, Callable, Dict, Optional

from server_modules import billing_credit_config
from server_modules import billing_service
from server_modules import control_plane_repository
from server_modules import deployed_agent_runtime_contract_service
from server_modules import run_state_repository
from server_modules.direct_tool_config_service import run_async_tool_call
from server_modules.platform_event import AI_LIMIT_REACHED_WEB


DEFAULT_PLAN_ID = "free"
PLAN_ALIASES = {
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
TERMINAL_RUN_STATES = {"completed", "failed", "cancelled", "canceled", "timeout", "aborted"}

NON_GATED_CAPABILITIES: Dict[str, bool] = {
    "core_sage_identity": True,
    "basic_specialist_architecture": True,
    "local_runtime_mode": True,
    "byo_provider_mode": True,
}

HOSTED_SAGE_AI_POLICIES = {"disabled", "owner_opt_in", "enabled_with_cap"}
DEFAULT_HOSTED_SAGE_AI_POLICY = "enabled_with_cap"
HOSTED_SAGE_AI_CREDITS_PER_USD = billing_credit_config.HOSTED_SAGE_AI_CREDITS_PER_USD


DEFAULT_HOSTED_SAGE_AI_MONTHLY_CAP_USD = billing_credit_config.DEFAULT_HOSTED_SAGE_AI_MONTHLY_CAP_USD

PLAN_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "free": {
        "label": "Free",
        "hosted_runtime_enabled": False,
        "hosted_ai_enabled": True,
        "chat_tier_light_enabled": True,
        "chat_tier_pro_enabled": True,
        "chat_tier_max_enabled": False,
        "chat_tier_pro_monthly_credit_cap": 350,
        "chat_tier_max_monthly_credit_cap": 0,
        "virtual_computer_runtime_enabled": False,
        "enterprise_admin_controls_enabled": False,
        "hosted_runtime_minutes_monthly": 0,
        "concurrent_hosted_executions": 0,
        "background_event_triggers_per_hour": 2,
        "background_self_proposed_per_hour": 1,
        "background_runtime_seconds": 15,
        "cloud_memory_storage_mb": 128,
        "cloud_memory_retention_days": 14,
        "sync_depth_days": 7,
        "premium_connectors_enabled": False,
        "team_features_enabled": False,
        "admin_security_features_enabled": False,
        "mobile_push_enabled": False,
        "cloud_services_enabled": True,
        "mobile_app_enabled": True,
        "approvals_enabled": False,
        "artifacts_enabled": False,
        "advanced_features_enabled": False,
        "premium_tools_enabled": False,
        "max_deployed_agents": 1,
        "max_deployed_agents_created": 10,
        "max_deployed_agents_live": 1,
        "priority_sync_enabled": False,
        "mini_apps_unlimited": True,
        "telegram_channel_enabled": True,
        "whatsapp_channel_enabled": True,
        "hosted_sage_ai_policy": "enabled_with_cap",
        "hosted_sage_ai_monthly_cap_usd": DEFAULT_HOSTED_SAGE_AI_MONTHLY_CAP_USD,
        # MAN-132: per-plan ceiling on platform-account Agent Computers
        # (droplets provisioned on EMPYRALIS's own cloud card — see
        # vps_provisioning_service.enforce_platform_vps_capacity's docstring;
        # customer-account provisioning is untouched by this entitlement).
        # 2 matches DEFAULT_VPS_MAX_ACTIVE_PER_WORKSPACE, the flat env-var
        # cap every workspace is already held to today — free stays exactly
        # where it is; this is scaffolding for later tuning, not a new
        # restriction. See enforce_agent_computer_slot_access.
        "max_agent_computers": 2,
    },
    "pilot": {
        "label": "Pilot",
        "hosted_runtime_enabled": True,
        "hosted_ai_enabled": True,
        "chat_tier_light_enabled": True,
        "chat_tier_pro_enabled": True,
        "chat_tier_max_enabled": True,
        "chat_tier_pro_monthly_credit_cap": 50_000,
        "chat_tier_max_monthly_credit_cap": 10_000,
        "virtual_computer_runtime_enabled": True,
        "enterprise_admin_controls_enabled": False,
        "hosted_runtime_minutes_monthly": 3_000,
        "concurrent_hosted_executions": 6,
        "background_event_triggers_per_hour": 12,
        "background_self_proposed_per_hour": 6,
        "background_runtime_seconds": 45,
        "cloud_memory_storage_mb": 4_096,
        "cloud_memory_retention_days": 180,
        "sync_depth_days": 180,
        "premium_connectors_enabled": True,
        "team_features_enabled": True,
        "admin_security_features_enabled": True,
        "mobile_push_enabled": True,
        "cloud_services_enabled": True,
        "mobile_app_enabled": True,
        "approvals_enabled": True,
        "artifacts_enabled": True,
        "advanced_features_enabled": True,
        "premium_tools_enabled": True,
        "max_deployed_agents": 10,
        "max_deployed_agents_created": 50,
        "max_deployed_agents_live": 10,
        "priority_sync_enabled": True,
        "mini_apps_unlimited": True,
        "telegram_channel_enabled": True,
        "whatsapp_channel_enabled": True,
        "hosted_sage_ai_policy": "enabled_with_cap",
        "hosted_sage_ai_monthly_cap_usd": 50.0,
        # See "free" plan's max_agent_computers comment above. 10 mirrors
        # max_deployed_agents for this plan — pilot is the hand-selected
        # internal/beta tier and is deliberately the most generous.
        "max_agent_computers": 10,
    },
    "pro": {
        "label": "Pro",
        "hosted_runtime_enabled": True,
        "hosted_ai_enabled": True,
        "chat_tier_light_enabled": True,
        "chat_tier_pro_enabled": True,
        "chat_tier_max_enabled": True,
        "chat_tier_pro_monthly_credit_cap": 20_000,
        "chat_tier_max_monthly_credit_cap": 5_000,
        "virtual_computer_runtime_enabled": False,
        "enterprise_admin_controls_enabled": False,
        "hosted_runtime_minutes_monthly": 1_500,
        "concurrent_hosted_executions": 4,
        "background_event_triggers_per_hour": 8,
        "background_self_proposed_per_hour": 4,
        "background_runtime_seconds": 30,
        "cloud_memory_storage_mb": 2_048,
        "cloud_memory_retention_days": 90,
        "sync_depth_days": 90,
        "premium_connectors_enabled": True,
        "team_features_enabled": False,
        "admin_security_features_enabled": False,
        "mobile_push_enabled": True,
        "cloud_services_enabled": True,
        "mobile_app_enabled": True,
        "approvals_enabled": True,
        "artifacts_enabled": True,
        "advanced_features_enabled": True,
        "premium_tools_enabled": True,
        "max_deployed_agents": 3,
        "max_deployed_agents_created": 20,
        "max_deployed_agents_live": 3,
        "priority_sync_enabled": True,
        "mini_apps_unlimited": True,
        "telegram_channel_enabled": True,
        "whatsapp_channel_enabled": True,
        # See "free" plan's max_agent_computers comment above. 3 mirrors
        # max_deployed_agents for this plan — above the free-tier ceiling,
        # below pilot's, never below what the flat cap already allows today.
        "max_agent_computers": 3,
    },
}


class EntitlementError(RuntimeError):
    def __init__(
        self,
        *,
        reason: str,
        message: str,
        entitlement_state: Optional[Dict[str, Any]] = None,
        retry_after_seconds: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.reason = str(reason or "entitlement_denied").strip() or "entitlement_denied"
        self.message = str(message or "This feature is not available for this workspace.").strip()
        self.entitlement_state = dict(entitlement_state or {})
        self.retry_after_seconds = int(retry_after_seconds or 0) or None


class EntitlementDeniedError(EntitlementError):
    pass


class EntitlementQuotaExceededError(EntitlementError):
    pass


@dataclass(frozen=True)
class WorkspaceEntitlementState:
    plan_id: str
    plan_label: str
    source: str
    entitlements: Dict[str, Any]
    usage: Dict[str, Any]
    non_gated_capabilities: Dict[str, bool]
    # workspace_id the state was resolved for (may be "" for ad-hoc/test
    # states built without a real workspace record). Carried through so
    # hosted_sage_ai_access_state can check the founder/demo unlimited-
    # credit allowlist below without an extra DB lookup.
    workspace_id: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _coerce_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    token = str(value).strip().lower()
    if token in {"1", "true", "yes", "on", "y"}:
        return True
    if token in {"0", "false", "no", "off", "n"}:
        return False
    return bool(default)


def _coerce_non_negative_float(value: Any, default: float) -> float:
    parsed = _coerce_float(value)
    if parsed is None:
        parsed = float(default)
    if parsed < 0:
        return 0.0
    return float(parsed)


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


def _normalize_hosted_sage_ai_policy(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token in HOSTED_SAGE_AI_POLICIES:
        return token
    return DEFAULT_HOSTED_SAGE_AI_POLICY


def normalize_plan_id(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token in PLAN_ALIASES:
        token = PLAN_ALIASES[token]
    return token if token in PLAN_DEFINITIONS else DEFAULT_PLAN_ID


def _workspace_entitlement_metadata(workspace: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    metadata = _coerce_dict(_coerce_dict(workspace).get("metadata"))
    admin_defaults = _coerce_dict(metadata.get("admin_defaults"))
    if isinstance(admin_defaults.get("payload"), dict):
        admin_defaults = _coerce_dict(admin_defaults.get("payload"))
    return {
        **_coerce_dict(metadata.get("entitlements")),
        **_coerce_dict(metadata.get("billing")),
        **admin_defaults,
    }


def _install_entitlement_metadata(install: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    metadata = _coerce_dict(_coerce_dict(install).get("metadata"))
    admin_defaults = _coerce_dict(metadata.get("admin_defaults"))
    if isinstance(admin_defaults.get("payload"), dict):
        admin_defaults = _coerce_dict(admin_defaults.get("payload"))
    return {
        **_coerce_dict(metadata.get("entitlements")),
        **_coerce_dict(metadata.get("billing")),
        **admin_defaults,
    }


def _merged_usage(
    workspace: Optional[Dict[str, Any]],
    install: Optional[Dict[str, Any]],
    *,
    billing_usage: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    workspace_meta = _workspace_entitlement_metadata(workspace)
    install_meta = _install_entitlement_metadata(install)
    return {
        **_coerce_dict(workspace_meta.get("usage")),
        **_coerce_dict(workspace_meta.get("entitlement_usage")),
        **_coerce_dict(billing_usage),
        **_coerce_dict(install_meta.get("usage")),
        **_coerce_dict(install_meta.get("entitlement_usage")),
    }


def _truthy_env(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _unlimited_credit_workspace_ids() -> frozenset[str]:
    """Founder/demo unlimited-credit workspace allowlist
    (EMPYRALIS_UNLIMITED_CREDIT_WORKSPACE_IDS).

    Comma-separated workspace_ids, read from the environment only — never
    hardcoded here, never committed. Same comma-split / trimmed /
    case-insensitive convention as agent_turn_runtime_service.py's
    EMPYRALIS_PRIMARY_COMPACTION_ENABLED / EMPYRALIS_FORCE_LEGACY_ENGINE env
    vars. Defaults to empty (disabled) when unset, so this is a no-op for
    every workspace until someone deliberately sets the var on the server.
    """
    raw = os.environ.get("EMPYRALIS_UNLIMITED_CREDIT_WORKSPACE_IDS", "")
    return frozenset(
        token.strip().lower()
        for token in raw.split(",")
        if token.strip()
    )


def unlimited_credit_workspace_bypass_active(workspace_id: str) -> bool:
    """Public wrapper over ``_unlimited_credit_workspace_ids`` for callers
    outside this module (MAN-132's VPS credit-balance gate — see
    ``vps_provisioning_service.enforce_platform_vps_credit_balance``) that
    need the SAME founder/demo allowlist ``hosted_sage_ai_access_state``
    already honors, rather than re-parsing
    EMPYRALIS_UNLIMITED_CREDIT_WORKSPACE_IDS a second time in a different
    module — "a channel list copied into a third place" is a documented
    failure mode here even for a three-line env-var split.
    """
    return str(workspace_id or "").strip().lower() in _unlimited_credit_workspace_ids()


def _mobile_beta_override_enabled(*, workspace: Optional[Dict[str, Any]], install: Optional[Dict[str, Any]]) -> bool:
    if _truthy_env(os.getenv("ORION_MOBILE_BETA_ENABLED")):
        return True

    workspace_meta = _workspace_entitlement_metadata(workspace)
    install_meta = _install_entitlement_metadata(install)
    workspace_root_meta = _coerce_dict(_coerce_dict(workspace).get("metadata"))
    install_root_meta = _coerce_dict(_coerce_dict(install).get("metadata"))

    candidates = (
        workspace_meta.get("mobile_beta_enabled"),
        workspace_meta.get("mobile_beta"),
        install_meta.get("mobile_beta_enabled"),
        install_meta.get("mobile_beta"),
        workspace_root_meta.get("mobile_beta_enabled"),
        workspace_root_meta.get("mobile_beta"),
        install_root_meta.get("mobile_beta_enabled"),
        install_root_meta.get("mobile_beta"),
    )
    return any(_coerce_bool(candidate, default=False) for candidate in candidates)


def resolve_workspace_entitlement_state(
    *,
    workspace: Optional[Dict[str, Any]],
    install: Optional[Dict[str, Any]] = None,
) -> WorkspaceEntitlementState:
    workspace_meta = _workspace_entitlement_metadata(workspace)
    install_meta = _install_entitlement_metadata(install)
    workspace_id = str(_coerce_dict(workspace).get("workspace_id") or "").strip()
    explicit_plan = (
        install_meta.get("plan")
        or install_meta.get("plan_id")
        or install_meta.get("plan_tier")
        or install_meta.get("billing_plan")
        or workspace_meta.get("plan")
        or workspace_meta.get("plan_id")
        or workspace_meta.get("plan_tier")
        or workspace_meta.get("billing_plan")
    )
    billing_summary = (
        billing_service.workspace_billing_summary_for_workspace_id(workspace_id, workspace=_coerce_dict(workspace))
        if workspace_id and not str(explicit_plan or "").strip()
        else None
    )
    billing_plan_id = None
    billing_source = None
    billing_metadata: Dict[str, Any] = {}
    billing_usage: Dict[str, Any] = {}
    if isinstance(billing_summary, dict):
        subscription = _coerce_dict(billing_summary.get("subscription"))
        billing_usage = _coerce_dict(billing_summary.get("usage"))
        hosted_billing_state = _coerce_dict(billing_summary.get("hosted_sage_ai"))
        if hosted_billing_state:
            billing_usage["hosted_sage_credit_balance_usd"] = hosted_billing_state.get("credit_balance_usd")
            billing_usage["hosted_sage_total_available_usd"] = hosted_billing_state.get("total_available_usd")
        billing_plan_id = str(
            subscription.get("effective_plan_id")
            or subscription.get("plan_id")
            or ""
        ).strip()
        billing_source = "workspace_billing"
        billing_metadata = _coerce_dict(subscription.get("metadata"))
    # ── Fallback: when the billing summary lacks credit_balance_usd (e.g.
    #     explicit-plan path or local dev without a billing ledger), read it
    #     directly from workspace metadata so the signup grant always counts.
    if "hosted_sage_credit_balance_usd" not in billing_usage:
        fallback_balance = _coerce_non_negative_float(
            workspace_meta.get("credit_balance_usd"), 0.0
        )
        if fallback_balance > 0:
            billing_usage["hosted_sage_credit_balance_usd"] = fallback_balance
    workspace_plan_id = explicit_plan
    if (
        billing_plan_id == DEFAULT_PLAN_ID
        and str(billing_metadata.get("source") or "").strip().lower() == "workspace_default"
        and str(workspace_plan_id or "").strip()
    ):
        billing_plan_id = None
    raw_plan = (
        install_meta.get("plan")
        or install_meta.get("plan_id")
        or billing_plan_id
        or workspace_meta.get("plan")
        or workspace_meta.get("plan_id")
        or install_meta.get("plan_tier")
        or workspace_meta.get("plan_tier")
        or install_meta.get("billing_plan")
        or workspace_meta.get("billing_plan")
    )
    plan_id = normalize_plan_id(raw_plan)
    entitlements = dict(PLAN_DEFINITIONS[plan_id])
    entitlements.update(_coerce_dict(workspace_meta.get("overrides")))
    entitlements.update(_coerce_dict(install_meta.get("overrides")))
    entitlements["hosted_sage_ai_policy"] = _normalize_hosted_sage_ai_policy(
        install_meta.get("hosted_sage_ai_policy")
        or workspace_meta.get("hosted_sage_ai_policy")
        or entitlements.get("hosted_sage_ai_policy")
        or DEFAULT_HOSTED_SAGE_AI_POLICY
    )
    entitlements["hosted_sage_ai_monthly_cap_usd"] = _coerce_non_negative_float(
        install_meta.get("hosted_sage_ai_monthly_cap_usd")
        if install_meta.get("hosted_sage_ai_monthly_cap_usd") is not None
        else workspace_meta.get("hosted_sage_ai_monthly_cap_usd")
        if workspace_meta.get("hosted_sage_ai_monthly_cap_usd") is not None
        else entitlements.get("hosted_sage_ai_monthly_cap_usd")
        if entitlements.get("hosted_sage_ai_monthly_cap_usd") is not None
        else DEFAULT_HOSTED_SAGE_AI_MONTHLY_CAP_USD,
        DEFAULT_HOSTED_SAGE_AI_MONTHLY_CAP_USD,
    )
    if _mobile_beta_override_enabled(workspace=workspace, install=install):
        entitlements["mobile_app_enabled"] = True
    source = "workspace_metadata"
    if install_meta.get("plan") or install_meta.get("plan_id") or install_meta.get("plan_tier"):
        source = "install_metadata"
    elif billing_plan_id:
        source = str(billing_source or "workspace_billing")
    elif not raw_plan:
        source = "default"
    return WorkspaceEntitlementState(
        plan_id=plan_id,
        plan_label=str(entitlements.get("label") or plan_id.title()).strip() or plan_id.title(),
        source=source,
        entitlements=entitlements,
        usage=_merged_usage(workspace, install, billing_usage=billing_usage),
        non_gated_capabilities=dict(NON_GATED_CAPABILITIES),
        workspace_id=workspace_id,
    )


def _load_workspace_record(workspace_id: str) -> Optional[Dict[str, Any]]:
    token = str(workspace_id or "").strip()
    if not token:
        return None
    try:
        payload = run_async_tool_call(control_plane_repository.get_workspace_by_id(token))
    except Exception:
        return None
    return dict(payload) if isinstance(payload, dict) else None


def resolve_workspace_entitlement_state_for_workspace_id(
    *,
    workspace_id: str,
    workspace: Optional[Dict[str, Any]] = None,
    install: Optional[Dict[str, Any]] = None,
) -> WorkspaceEntitlementState:
    resolved_workspace = dict(workspace) if isinstance(workspace, dict) else _load_workspace_record(workspace_id)
    return resolve_workspace_entitlement_state(workspace=resolved_workspace, install=install)


def hosted_sage_ai_access_state(
    *,
    workspace: Optional[Dict[str, Any]] = None,
    install: Optional[Dict[str, Any]] = None,
    state: Optional[WorkspaceEntitlementState] = None,
) -> Dict[str, Any]:
    resolved_state = state or resolve_workspace_entitlement_state(workspace=workspace, install=install)
    entitlements = resolved_state.entitlements
    usage = resolved_state.usage

    plan_allows_hosted_ai = bool(entitlements.get("hosted_ai_enabled"))
    policy = _normalize_hosted_sage_ai_policy(entitlements.get("hosted_sage_ai_policy"))
    monthly_cap_usd = _coerce_non_negative_float(
        entitlements.get("hosted_sage_ai_monthly_cap_usd"),
        DEFAULT_HOSTED_SAGE_AI_MONTHLY_CAP_USD,
    )
    monthly_cost_usd = _coerce_non_negative_float(usage.get("hosted_sage_cost_usd_monthly"), 0.0)
    remaining_usd = max(0.0, round(monthly_cap_usd - monthly_cost_usd, 6))
    credit_balance_usd = _coerce_non_negative_float(usage.get("hosted_sage_credit_balance_usd"), 0.0)
    total_available_usd = max(0.0, round(remaining_usd + credit_balance_usd, 6))
    credit_fields = _hosted_sage_ai_credit_fields(
        monthly_cap_usd=monthly_cap_usd,
        monthly_cost_usd=monthly_cost_usd,
        monthly_remaining_usd=remaining_usd,
    )

    # ── Founder/demo unlimited-credit bypass (deliberate, env-gated) ──
    # NOT a general product feature. Lets a narrow, explicit allowlist of
    # workspace_ids skip the credit-balance/cap gate entirely, so the
    # founder's own workspace(s) are never blocked by credit exhaustion
    # while demoing, testing, or dogfooding the platform. Gated purely by
    # EMPYRALIS_UNLIMITED_CREDIT_WORKSPACE_IDS (see
    # _unlimited_credit_workspace_ids above) — no workspace_id is
    # hardcoded in source, and every other workspace's balance/cap check
    # is completely unaffected: this branch only triggers when that env
    # var is set on the server AND lists this exact workspace_id.
    if resolved_state.workspace_id.strip().lower() in _unlimited_credit_workspace_ids():
        return {
            "allowed": True,
            "plan_allows_hosted_ai": plan_allows_hosted_ai,
            "policy": policy,
            "monthly_cap_usd": monthly_cap_usd,
            "monthly_cost_usd": monthly_cost_usd,
            "monthly_remaining_usd": remaining_usd,
            "credit_balance_usd": credit_balance_usd,
            "credit_balance_credits": int(round(credit_balance_usd * HOSTED_SAGE_AI_CREDITS_PER_USD)),
            "total_available_usd": total_available_usd,
            "total_available_credits": int(round(total_available_usd * HOSTED_SAGE_AI_CREDITS_PER_USD)),
            **credit_fields,
            "reason": None,
            "message": None,
            "unlimited_credit_bypass": True,
        }

    if not plan_allows_hosted_ai:
        return {
            "allowed": False,
            "plan_allows_hosted_ai": False,
            "policy": "disabled",
            "monthly_cap_usd": monthly_cap_usd,
            "monthly_cost_usd": monthly_cost_usd,
            "monthly_remaining_usd": remaining_usd,
            "credit_balance_usd": credit_balance_usd,
            "credit_balance_credits": int(round(credit_balance_usd * HOSTED_SAGE_AI_CREDITS_PER_USD)),
            "total_available_usd": total_available_usd,
            "total_available_credits": int(round(total_available_usd * HOSTED_SAGE_AI_CREDITS_PER_USD)),
            **credit_fields,
            "reason": "policy_disabled",
            "message": "Credits are not active yet. Add credits or use your own API key.",
        }
    if policy == "disabled":
        return {
            "allowed": False,
            "plan_allows_hosted_ai": True,
            "policy": policy,
            "monthly_cap_usd": monthly_cap_usd,
            "monthly_cost_usd": monthly_cost_usd,
            "monthly_remaining_usd": remaining_usd,
            "credit_balance_usd": credit_balance_usd,
            "credit_balance_credits": int(round(credit_balance_usd * HOSTED_SAGE_AI_CREDITS_PER_USD)),
            "total_available_usd": total_available_usd,
            "total_available_credits": int(round(total_available_usd * HOSTED_SAGE_AI_CREDITS_PER_USD)),
            **credit_fields,
            "reason": "policy_disabled",
            "message": "Hosted AI is disabled for this workspace.",
        }
    if policy == "owner_opt_in":
        return {
            "allowed": False,
            "plan_allows_hosted_ai": True,
            "policy": policy,
            "monthly_cap_usd": monthly_cap_usd,
            "monthly_cost_usd": monthly_cost_usd,
            "monthly_remaining_usd": remaining_usd,
            "credit_balance_usd": credit_balance_usd,
            "credit_balance_credits": int(round(credit_balance_usd * HOSTED_SAGE_AI_CREDITS_PER_USD)),
            "total_available_usd": total_available_usd,
            "total_available_credits": int(round(total_available_usd * HOSTED_SAGE_AI_CREDITS_PER_USD)),
            **credit_fields,
            "reason": "owner_approval_required",
            "message": "Hosted AI is not enabled for this workspace. Connect your own provider key or switch to a local runtime.",
        }
    if total_available_usd <= 0:
        credit_balance_credits = int(round(credit_balance_usd * HOSTED_SAGE_AI_CREDITS_PER_USD))
        total_available_credits = int(round(total_available_usd * HOSTED_SAGE_AI_CREDITS_PER_USD))
        # If the user had a credit balance that is now depleted, give a
        # friendlier message directing them to add their own key.
        if credit_balance_credits <= 0 and remaining_usd <= 0:
            # The canonical constant, not a hand-retyped copy of it —
            # HardStopMessageTests.test_entitlement_exhaustion_message_is_friendly
            # asserts this by identity precisely because this literal used
            # to drift from platform_event.AI_LIMIT_REACHED_WEB: rewording
            # the shared message (2026-08-19, dropping a "→" this surface
            # renders as a dead click affordance) fixed sage_command_
            # dispatcher's copy and silently left this one behind.
            message = AI_LIMIT_REACHED_WEB.detail
        else:
            message = "Hosted AI monthly cap is reached for this workspace."
        return {
            "allowed": False,
            "plan_allows_hosted_ai": True,
            "policy": policy,
            "monthly_cap_usd": monthly_cap_usd,
            "monthly_cost_usd": monthly_cost_usd,
            "monthly_remaining_usd": remaining_usd,
            "credit_balance_usd": credit_balance_usd,
            "credit_balance_credits": credit_balance_credits,
            "total_available_usd": total_available_usd,
            "total_available_credits": total_available_credits,
            **credit_fields,
            "reason": "cap_reached",
            "message": message,
        }
    return {
        "allowed": True,
        "plan_allows_hosted_ai": True,
        "policy": policy,
        "monthly_cap_usd": monthly_cap_usd,
        "monthly_cost_usd": monthly_cost_usd,
        "monthly_remaining_usd": remaining_usd,
        "credit_balance_usd": credit_balance_usd,
        "credit_balance_credits": int(round(credit_balance_usd * HOSTED_SAGE_AI_CREDITS_PER_USD)),
        "total_available_usd": total_available_usd,
        "total_available_credits": int(round(total_available_usd * HOSTED_SAGE_AI_CREDITS_PER_USD)),
        **credit_fields,
        "reason": None,
        "message": None,
    }


def hosted_sage_ai_access_state_for_workspace_id(
    *,
    workspace_id: str,
    workspace: Optional[Dict[str, Any]] = None,
    install: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    resolved_workspace = dict(workspace) if isinstance(workspace, dict) else _load_workspace_record(workspace_id)
    state = resolve_workspace_entitlement_state(workspace=resolved_workspace, install=install)
    return hosted_sage_ai_access_state(state=state)


def _coerce_non_negative_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed < 0:
        return 0
    return parsed


def _tier_enabled_with_credit_cap(
    *,
    enabled: bool,
    monthly_credit_cap: Optional[int],
    monthly_credits_used: int,
) -> tuple[bool, Optional[str]]:
    if not enabled:
        return False, "plan_disabled"
    if monthly_credit_cap is None:
        return True, None
    if monthly_credit_cap <= 0:
        return False, "monthly_credit_cap_reached"
    if monthly_credits_used >= monthly_credit_cap:
        return False, "monthly_credit_cap_reached"
    return True, None


def chat_model_tier_policy(
    *,
    workspace: Optional[Dict[str, Any]] = None,
    install: Optional[Dict[str, Any]] = None,
    state: Optional[WorkspaceEntitlementState] = None,
) -> Dict[str, Any]:
    resolved_state = state or resolve_workspace_entitlement_state(workspace=workspace, install=install)
    entitlements = resolved_state.entitlements
    hosted_state = hosted_sage_ai_access_state(state=resolved_state)
    hosted_enabled = bool(hosted_state.get("allowed"))
    monthly_credits_used = int(hosted_state.get("monthly_credits_used") or 0)

    light_enabled = _coerce_bool(entitlements.get("chat_tier_light_enabled"), True)
    pro_enabled = _coerce_bool(entitlements.get("chat_tier_pro_enabled"), True)
    max_enabled = _coerce_bool(
        entitlements.get("chat_tier_max_enabled"),
        resolved_state.plan_id != "free",
    )
    pro_monthly_credit_cap = _coerce_non_negative_int(
        entitlements.get("chat_tier_pro_monthly_credit_cap"),
        None,
    )
    max_monthly_credit_cap = _coerce_non_negative_int(
        entitlements.get("chat_tier_max_monthly_credit_cap"),
        None,
    )

    light_allowed, light_reason = _tier_enabled_with_credit_cap(
        enabled=hosted_enabled and light_enabled,
        monthly_credit_cap=None,
        monthly_credits_used=monthly_credits_used,
    )
    pro_allowed, pro_reason = _tier_enabled_with_credit_cap(
        enabled=hosted_enabled and pro_enabled,
        monthly_credit_cap=pro_monthly_credit_cap,
        monthly_credits_used=monthly_credits_used,
    )
    max_allowed, max_reason = _tier_enabled_with_credit_cap(
        enabled=hosted_enabled and max_enabled,
        monthly_credit_cap=max_monthly_credit_cap,
        monthly_credits_used=monthly_credits_used,
    )

    return {
        "plan_id": resolved_state.plan_id,
        "hosted_ai_enabled": hosted_enabled,
        "monthly_credits_used": monthly_credits_used,
        "tiers": {
            "light": {
                "enabled": bool(light_allowed),
                "monthly_credit_cap": None,
                "reason": light_reason,
            },
            "pro": {
                "enabled": bool(pro_allowed),
                "monthly_credit_cap": pro_monthly_credit_cap,
                "reason": pro_reason,
            },
            "max": {
                "enabled": bool(max_allowed),
                "monthly_credit_cap": max_monthly_credit_cap,
                "reason": max_reason,
            },
        },
        "user_owned": {
            "local_ai": bool(
                _coerce_bool(
                    entitlements.get("local_runtime_mode"),
                    bool(NON_GATED_CAPABILITIES.get("local_runtime_mode", True)),
                )
            ),
            "my_api_key": bool(
                _coerce_bool(
                    entitlements.get("byo_provider_mode"),
                    bool(NON_GATED_CAPABILITIES.get("byo_provider_mode", True)),
                )
            ),
            "my_ai_account": True,
        },
        "virtual_computer_runtime_enabled": bool(
            _coerce_bool(entitlements.get("virtual_computer_runtime_enabled"), False)
        ),
        "enterprise_admin_controls_enabled": bool(
            _coerce_bool(entitlements.get("enterprise_admin_controls_enabled"), False)
            or _coerce_bool(entitlements.get("admin_security_features_enabled"), False)
        ),
    }


def chat_model_tier_policy_for_workspace_id(
    *,
    workspace_id: str,
    workspace: Optional[Dict[str, Any]] = None,
    install: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    resolved_workspace = dict(workspace) if isinstance(workspace, dict) else _load_workspace_record(workspace_id)
    resolved_state = resolve_workspace_entitlement_state(workspace=resolved_workspace, install=install)
    return chat_model_tier_policy(state=resolved_state)


def workspace_capability_flags(
    *,
    workspace: Optional[Dict[str, Any]] = None,
    install: Optional[Dict[str, Any]] = None,
    state: Optional[WorkspaceEntitlementState] = None,
) -> Dict[str, Any]:
    resolved_state = state or resolve_workspace_entitlement_state(workspace=workspace, install=install)
    entitlements = resolved_state.entitlements
    hosted_state = hosted_sage_ai_access_state(state=resolved_state)
    history_window_days = max(1, _coerce_int(entitlements.get("sync_depth_days"), 30))
    deployed_agent_quotas = deployed_agent_quota_defaults(state=resolved_state)
    return {
        "hosted_ai_enabled": bool(hosted_state.get("allowed")),
        "hosted_sage_ai_policy": str(hosted_state.get("policy") or DEFAULT_HOSTED_SAGE_AI_POLICY),
        "hosted_sage_ai_monthly_cap_usd": float(hosted_state.get("monthly_cap_usd") or 0.0),
        "hosted_sage_ai_monthly_cost_usd": float(hosted_state.get("monthly_cost_usd") or 0.0),
        "hosted_sage_ai_monthly_remaining_usd": float(hosted_state.get("monthly_remaining_usd") or 0.0),
        "hosted_sage_ai_monthly_credit_cap": int(hosted_state.get("monthly_credit_cap") or 0),
        "hosted_sage_ai_monthly_credits_used": int(hosted_state.get("monthly_credits_used") or 0),
        "hosted_sage_ai_monthly_credits_remaining": int(hosted_state.get("monthly_credits_remaining") or 0),
        "hosted_sage_ai_reason": hosted_state.get("reason"),
        "mobile_app_enabled": bool(entitlements.get("mobile_app_enabled")),
        "mobile_push_enabled": bool(entitlements.get("mobile_push_enabled")),
        "approvals_enabled": bool(entitlements.get("approvals_enabled")),
        "artifacts_enabled": bool(entitlements.get("artifacts_enabled")),
        "advanced_features_enabled": bool(entitlements.get("advanced_features_enabled")),
        "premium_tools_enabled": bool(entitlements.get("premium_tools_enabled")),
        "priority_sync_enabled": bool(entitlements.get("priority_sync_enabled")),
        "mini_apps_unlimited": bool(entitlements.get("mini_apps_unlimited")),
        "max_specialists": _coerce_int(entitlements.get("max_deployed_agents"), 1),
        "deployed_agent_quotas": deployed_agent_quotas,
        "history_window_days": history_window_days,
        "telegram_channel_enabled": bool(entitlements.get("telegram_channel_enabled")),
        "whatsapp_channel_enabled": bool(entitlements.get("whatsapp_channel_enabled")),
        "channels": {
            "telegram": bool(entitlements.get("telegram_channel_enabled")),
            "whatsapp": bool(entitlements.get("whatsapp_channel_enabled")),
        },
    }


def _runtime_target_payload_by_id(runtime_targets: Any, target_id: str) -> Dict[str, Any]:
    payload = runtime_targets if isinstance(runtime_targets, dict) else {}
    targets = payload.get("targets") if isinstance(payload.get("targets"), list) else []
    token = str(target_id or "").strip().lower()
    for item in targets:
        if not isinstance(item, dict):
            continue
        if str(item.get("target_id") or "").strip().lower() == token:
            return dict(item)
    return {}


def deployed_agent_quota_defaults(
    *,
    workspace: Optional[Dict[str, Any]] = None,
    install: Optional[Dict[str, Any]] = None,
    state: Optional[WorkspaceEntitlementState] = None,
    runtime_targets: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    resolved_state = state or resolve_workspace_entitlement_state(workspace=workspace, install=install)
    limits = resolved_state.entitlements
    max_specialists = max(1, _coerce_int(limits.get("max_deployed_agents"), 1))
    created_agents_max = max(1, _coerce_int(limits.get("max_deployed_agents_created"), max_specialists))
    live_agents_max = max(1, _coerce_int(limits.get("max_deployed_agents_live"), max_specialists))
    concurrent_running_agents_max = max(1, _coerce_int(limits.get("max_concurrent_running_agents"), _coerce_int(limits.get("concurrent_hosted_executions"), 1)))
    computer_sessions_max = max(1, _coerce_int(limits.get("max_computer_sessions"), 1))
    background_jobs_max = max(1, _coerce_int(limits.get("max_background_jobs"), _coerce_int(limits.get("background_event_triggers_per_hour"), 2)))
    messages_per_day_max = max(1, _coerce_int(limits.get("max_messages_per_day"), 500))
    tool_calls_per_day_max = max(1, _coerce_int(limits.get("max_tool_calls_per_day"), 300))
    runtime_minutes_monthly_max = max(0, _coerce_int(limits.get("max_runtime_minutes_monthly"), _coerce_int(limits.get("hosted_runtime_minutes_monthly"), 0)))
    monthly_spend_usd_max = _coerce_non_negative_float(
        limits.get("max_monthly_spend_usd"),
        0.0,
    )
    storage_memory_size_mb_max = max(0, _coerce_int(limits.get("max_storage_memory_mb"), _coerce_int(limits.get("cloud_memory_storage_mb"), 0)))

    local_target = _runtime_target_payload_by_id(runtime_targets, "local_companion")
    self_hosted_target = _runtime_target_payload_by_id(runtime_targets, "self_host_runtime")
    local_capacity = max(1, int(local_target.get("attachment_count") or 1)) if local_target else 1
    self_hosted_capacity = max(1, int(self_hosted_target.get("attachment_count") or 1)) if self_hosted_target else 1

    text_created = max(1, _coerce_int(limits.get("max_text_agent_created"), created_agents_max))
    text_live = max(1, _coerce_int(limits.get("max_text_agent_live"), min(live_agents_max, text_created)))
    cloud_created = max(1, _coerce_int(limits.get("max_cloud_computer_agent_created"), min(created_agents_max, 2)))
    cloud_live = max(1, _coerce_int(limits.get("max_cloud_computer_agent_live"), min(live_agents_max, 1)))
    cloud_concurrent = max(1, _coerce_int(limits.get("max_cloud_computer_agent_concurrent_running"), min(concurrent_running_agents_max, 1)))
    cloud_sessions = max(1, _coerce_int(limits.get("max_cloud_computer_sessions"), min(computer_sessions_max, 1)))
    my_created = max(1, _coerce_int(limits.get("max_my_computer_agent_created"), created_agents_max))
    my_live_requested = max(1, _coerce_int(limits.get("max_my_computer_agent_live"), live_agents_max))
    my_live = min(local_capacity, my_live_requested)
    my_concurrent_requested = max(1, _coerce_int(limits.get("max_my_computer_agent_concurrent_running"), concurrent_running_agents_max))
    my_concurrent = min(local_capacity, my_concurrent_requested)
    my_sessions_requested = max(1, _coerce_int(limits.get("max_my_computer_sessions"), computer_sessions_max))
    my_sessions = min(local_capacity, my_sessions_requested)
    self_created = max(1, _coerce_int(limits.get("max_self_hosted_agent_created"), created_agents_max))
    self_live_requested = max(1, _coerce_int(limits.get("max_self_hosted_agent_live"), live_agents_max))
    self_live = min(self_hosted_capacity, self_live_requested)
    self_concurrent_requested = max(1, _coerce_int(limits.get("max_self_hosted_agent_concurrent_running"), concurrent_running_agents_max))
    self_concurrent = min(self_hosted_capacity, self_concurrent_requested)
    self_sessions_requested = max(1, _coerce_int(limits.get("max_self_hosted_agent_computer_sessions"), computer_sessions_max))
    self_sessions = min(self_hosted_capacity, self_sessions_requested)

    return {
        "plan_tier": resolved_state.plan_id,
        "created_agents_max": created_agents_max,
        "live_agents_max": live_agents_max,
        "concurrent_running_agents_max": concurrent_running_agents_max,
        "computer_sessions_max": computer_sessions_max,
        "background_jobs_max": background_jobs_max,
        "messages_per_day_max": messages_per_day_max,
        "tool_calls_per_day_max": tool_calls_per_day_max,
        "runtime_minutes_monthly_max": runtime_minutes_monthly_max,
        "monthly_spend_usd_max": monthly_spend_usd_max,
        "storage_memory_size_mb_max": storage_memory_size_mb_max,
        "mode_limits": {
            deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_TEXT: {
                "created_agents_max": text_created,
                "live_agents_max": text_live,
                "concurrent_running_agents_max": concurrent_running_agents_max,
                "computer_sessions_max": 0,
            },
            deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_CLOUD_COMPUTER: {
                "created_agents_max": cloud_created,
                "live_agents_max": cloud_live,
                "concurrent_running_agents_max": cloud_concurrent,
                "computer_sessions_max": cloud_sessions,
            },
            deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_MY_COMPUTER: {
                "created_agents_max": my_created,
                "live_agents_max": my_live,
                "concurrent_running_agents_max": my_concurrent,
                "computer_sessions_max": my_sessions,
            },
            deployed_agent_runtime_contract_service.STUDIO_AGENT_MODE_SELF_HOSTED: {
                "created_agents_max": self_created,
                "live_agents_max": self_live,
                "concurrent_running_agents_max": self_concurrent,
                "computer_sessions_max": self_sessions,
            },
        },
    }


def hosted_ai_enabled(
    *,
    workspace: Optional[Dict[str, Any]] = None,
    install: Optional[Dict[str, Any]] = None,
    state: Optional[WorkspaceEntitlementState] = None,
) -> bool:
    hosted_state = hosted_sage_ai_access_state(
        workspace=workspace,
        install=install,
        state=state,
    )
    return bool(hosted_state.get("allowed"))


def hosted_ai_enabled_for_workspace_id(
    *,
    workspace_id: str,
    workspace: Optional[Dict[str, Any]] = None,
    install: Optional[Dict[str, Any]] = None,
) -> bool:
    hosted_state = hosted_sage_ai_access_state_for_workspace_id(
        workspace_id=workspace_id,
        workspace=workspace,
        install=install,
    )
    return bool(hosted_state.get("allowed"))


def workspace_entitlement_payload(
    *,
    workspace: Optional[Dict[str, Any]] = None,
    install: Optional[Dict[str, Any]] = None,
    state: Optional[WorkspaceEntitlementState] = None,
) -> Dict[str, Any]:
    resolved_state = state or resolve_workspace_entitlement_state(workspace=workspace, install=install)
    hosted_state = hosted_sage_ai_access_state(state=resolved_state)
    return {
        **resolved_state.as_dict(),
        "capabilities": workspace_capability_flags(state=resolved_state),
        "hosted_sage_ai": hosted_state,
    }


def workspace_entitlement_payload_for_workspace_id(
    *,
    workspace_id: str,
    workspace: Optional[Dict[str, Any]] = None,
    install: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    state = resolve_workspace_entitlement_state_for_workspace_id(
        workspace_id=workspace_id,
        workspace=workspace,
        install=install,
    )
    return workspace_entitlement_payload(state=state)


def enforce_specialist_slot_access(
    *,
    workspace: Optional[Dict[str, Any]],
    install: Optional[Dict[str, Any]] = None,
    current_specialist_count: int,
) -> Dict[str, Any]:
    state = resolve_workspace_entitlement_state(workspace=workspace, install=install)
    max_specialists = max(1, _coerce_int(
        state.entitlements.get("max_deployed_agents_created"),
        _coerce_int(state.entitlements.get("max_deployed_agents"), 1),
    ))
    if int(current_specialist_count or 0) >= max_specialists:
        raise EntitlementQuotaExceededError(
            reason="created_agent_limit_exceeded",
            message=(
                "This workspace has reached its draft agent limit for the current plan. "
                "Archive an unused draft or upgrade before creating another agent."
            ),
            entitlement_state=workspace_entitlement_payload(state=state),
            retry_after_seconds=3600,
        )
    return workspace_entitlement_payload(state=state)


def enforce_agent_computer_slot_access(
    *,
    workspace: Optional[Dict[str, Any]],
    install: Optional[Dict[str, Any]] = None,
    current_agent_computer_count: int,
) -> Dict[str, Any]:
    """MAN-132: per-plan ceiling on platform-account Agent Computers (droplets
    provisioned on EMPYRALIS's own cloud card). Same count-then-403 shape as
    ``enforce_specialist_slot_access`` above and ``_enforce_phase8_quota_controls``
    in ``deployed_agent_service.py``.

    Callers are expected to count with
    ``vps_provisioning_service.count_active_workspace_vps`` — see
    ``vps_provisioning_service.enforce_platform_vps_plan_capacity``, the thin
    fetch-and-translate wrapper that calls this from the provisioning route.
    This function itself takes no dependency on vps_provisioning_service (the
    same "entitlements never fetches its own data" shape
    ``enforce_specialist_slot_access`` already uses) so it stays a pure,
    unit-testable policy decision over a caller-supplied workspace + count.

    Deliberately layered ALONGSIDE ``vps_provisioning_service.
    enforce_platform_vps_capacity`` (the flat, env-var-tuned cap), not a
    replacement for it: every PLAN_DEFINITIONS default here is chosen to be
    >= DEFAULT_VPS_MAX_ACTIVE_PER_WORKSPACE, so wiring this in changes
    nothing for any workspace already within today's flat cap — the flat cap
    keeps binding until an operator deliberately raises
    EMPYRALIS_VPS_MAX_ACTIVE_PER_WORKSPACE, at which point this becomes the
    real, differentiated ceiling per plan without another code change.
    """
    state = resolve_workspace_entitlement_state(workspace=workspace, install=install)
    max_agent_computers = max(1, _coerce_int(state.entitlements.get("max_agent_computers"), 1))
    if int(current_agent_computer_count or 0) >= max_agent_computers:
        raise EntitlementQuotaExceededError(
            reason="agent_computer_limit_exceeded",
            message=(
                f"This workspace has reached its Agent Computer limit for the current plan "
                f"({max_agent_computers}). Delete one you no longer need, or upgrade, then try again."
            ),
            entitlement_state=workspace_entitlement_payload(state=state),
            retry_after_seconds=3600,
        )
    return workspace_entitlement_payload(state=state)


def history_window_cutoff_ts(
    *,
    workspace: Optional[Dict[str, Any]] = None,
    install: Optional[Dict[str, Any]] = None,
    state: Optional[WorkspaceEntitlementState] = None,
    now_ts: Optional[float] = None,
) -> Optional[float]:
    resolved_state = state or resolve_workspace_entitlement_state(workspace=workspace, install=install)
    history_window_days = max(1, _coerce_int(resolved_state.entitlements.get("sync_depth_days"), 30))
    return float(now_ts if now_ts is not None else time.time()) - float(history_window_days * 86400)


def history_window_cutoff_ts_for_workspace_id(
    *,
    workspace_id: str,
    workspace: Optional[Dict[str, Any]] = None,
    install: Optional[Dict[str, Any]] = None,
    now_ts: Optional[float] = None,
) -> Optional[float]:
    state = resolve_workspace_entitlement_state_for_workspace_id(
        workspace_id=workspace_id,
        workspace=workspace,
        install=install,
    )
    return history_window_cutoff_ts(state=state, now_ts=now_ts)


def scheduler_policy_defaults(
    *,
    workspace: Optional[Dict[str, Any]],
    install: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    state = resolve_workspace_entitlement_state(workspace=workspace, install=install)
    limits = state.entitlements
    return {
        "plan_tier": state.plan_id,
        "max_event_triggers_per_hour": _coerce_int(limits.get("background_event_triggers_per_hour"), 4),
        "max_self_proposed_per_hour": _coerce_int(limits.get("background_self_proposed_per_hour"), 2),
        "max_runtime_seconds": _coerce_int(limits.get("background_runtime_seconds"), 20),
    }


def memory_policy_defaults(
    *,
    workspace: Optional[Dict[str, Any]],
    install: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    state = resolve_workspace_entitlement_state(workspace=workspace, install=install)
    limits = state.entitlements
    return {
        "plan_tier": state.plan_id,
        "cloud_memory_storage_mb": _coerce_int(limits.get("cloud_memory_storage_mb"), 512),
        "cloud_memory_retention_days": _coerce_int(limits.get("cloud_memory_retention_days"), 30),
        "sync_depth_days": _coerce_int(limits.get("sync_depth_days"), 30),
    }


def connector_policy_defaults(
    *,
    workspace: Optional[Dict[str, Any]],
    install: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    state = resolve_workspace_entitlement_state(workspace=workspace, install=install)
    limits = state.entitlements
    return {
        "plan_tier": state.plan_id,
        "premium_connectors_enabled": bool(limits.get("premium_connectors_enabled")),
        "team_features_enabled": bool(limits.get("team_features_enabled")),
        "admin_security_features_enabled": bool(limits.get("admin_security_features_enabled")),
    }


def _enforce_workspace_capability(
    *,
    workspace: Optional[Dict[str, Any]],
    install: Optional[Dict[str, Any]],
    entitlement_key: str,
    reason: str,
    message: str,
) -> Dict[str, Any]:
    state = resolve_workspace_entitlement_state(workspace=workspace, install=install)
    if not bool(state.entitlements.get(entitlement_key)):
        raise EntitlementDeniedError(
            reason=reason,
            message=message,
            entitlement_state=workspace_entitlement_payload(state=state),
        )
    return workspace_entitlement_payload(state=state)


def _workspace_hosted_execution_count(
    workspace_id: str,
    *,
    live_runs_fn: Optional[Callable[[], list[Dict[str, Any]]]] = None,
    count_hosted_live_runs_fn: Optional[Callable[[str], int]] = None,
) -> int:
    resolved_workspace_id = str(workspace_id or "").strip()
    if not resolved_workspace_id:
        return 0
    if callable(count_hosted_live_runs_fn):
        try:
            return max(0, int(count_hosted_live_runs_fn(resolved_workspace_id)))
        except Exception:
            return 0
    if live_runs_fn is None:
        try:
            return max(0, int(run_state_repository.sync_count_hosted_live_runs(resolved_workspace_id)))
        except Exception:
            return 0
    try:
        live_runs = live_runs_fn() if callable(live_runs_fn) else run_state_repository.sync_list_live_runs()
    except Exception:
        live_runs = []
    total = 0
    for item in list(live_runs or []):
        if not isinstance(item, dict):
            continue
        context = _coerce_dict(item.get("context"))
        if str(context.get("workspace_id") or item.get("workspace_id") or "").strip() != resolved_workspace_id:
            continue
        status = str(item.get("status") or "").strip().lower()
        if status in TERMINAL_RUN_STATES:
            continue
        metadata = _coerce_dict(context.get("metadata"))
        if str(metadata.get("runtime_attachment_kind") or "").strip() == "self_hosted_business_node":
            continue
        execution_target = str(metadata.get("execution_target_selected") or "").strip().lower()
        runtime_mode = str(metadata.get("runtime_mode") or item.get("runtime_mode") or "").strip().lower()
        if execution_target == "cloud" or runtime_mode == "hosted_secure":
            total += 1
    return total


def enforce_hosted_runtime_access(
    *,
    workspace: Optional[Dict[str, Any]],
    install: Optional[Dict[str, Any]] = None,
    workspace_id: str,
    selected_attachment: Optional[Dict[str, Any]] = None,
    live_runs_fn: Optional[Callable[[], list[Dict[str, Any]]]] = None,
    count_hosted_live_runs_fn: Optional[Callable[[str], int]] = None,
) -> Dict[str, Any]:
    state = resolve_workspace_entitlement_state(workspace=workspace, install=install)
    attachment = _coerce_dict(selected_attachment)
    attachment_kind = str(attachment.get("attachment_kind") or "").strip()
    if attachment_kind == "self_hosted_business_node":
        return {
            **state.as_dict(),
            "usage_snapshot": {
                "hosted_runtime_minutes_monthly": _coerce_float(state.usage.get("hosted_runtime_minutes_monthly")) or 0.0,
                "concurrent_hosted_executions": 0,
            },
            "enforcement_target": "self_hosted",
        }
    entitlements = state.entitlements
    if not bool(entitlements.get("hosted_runtime_enabled")):
        raise EntitlementDeniedError(
            reason="hosted_runtime_unavailable",
            message="Hosted cloud execution is not included in the current plan. Local and self-hosted runtimes remain available.",
            entitlement_state=state.as_dict(),
        )
    monthly_minutes_limit = _coerce_float(entitlements.get("hosted_runtime_minutes_monthly"))
    used_minutes = _coerce_float(state.usage.get("hosted_runtime_minutes_monthly")) or 0.0
    if monthly_minutes_limit is not None and used_minutes >= monthly_minutes_limit:
        raise EntitlementQuotaExceededError(
            reason="hosted_runtime_minutes_exhausted",
            message="Hosted runtime minutes are exhausted for this workspace right now. Local and self-hosted runtimes still work.",
            entitlement_state=state.as_dict(),
            retry_after_seconds=3600,
        )
    concurrent_limit = _coerce_int(entitlements.get("concurrent_hosted_executions"), 1)
    active_hosted = _workspace_hosted_execution_count(
        workspace_id,
        live_runs_fn=live_runs_fn,
        count_hosted_live_runs_fn=count_hosted_live_runs_fn,
    )
    if active_hosted >= concurrent_limit:
        raise EntitlementQuotaExceededError(
            reason="hosted_runtime_concurrency_exhausted",
            message="Hosted execution is busy for this workspace right now. Please retry shortly, or use a local/self-hosted runtime if attached.",
            entitlement_state=state.as_dict(),
            retry_after_seconds=30,
        )
    return {
        **state.as_dict(),
        "usage_snapshot": {
            "hosted_runtime_minutes_monthly": used_minutes,
            "concurrent_hosted_executions": active_hosted,
        },
        "enforcement_target": "cloud_computer" if attachment_kind == "cloud_computer" else "managed_cloud",
        "metered_cloud_computer": attachment_kind == "cloud_computer",
    }


def enforce_hosted_ai_access(
    *,
    workspace: Optional[Dict[str, Any]],
    install: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    state = resolve_workspace_entitlement_state(workspace=workspace, install=install)
    hosted_state = hosted_sage_ai_access_state(state=state)
    if not bool(hosted_state.get("allowed")):
        reason = str(hosted_state.get("reason") or "hosted_ai_unavailable").strip() or "hosted_ai_unavailable"
        reason_code = {
            "policy_disabled": "hosted_ai_policy_disabled",
            "owner_approval_required": "hosted_ai_owner_approval_required",
            "cap_reached": "hosted_ai_cap_reached",
        }.get(reason, "hosted_ai_unavailable")
        raise EntitlementDeniedError(
            reason=reason_code,
            message=str(hosted_state.get("message") or "Empyralis-hosted AI is not enabled for this workspace."),
            entitlement_state=workspace_entitlement_payload(state=state),
        )
    return {
        **workspace_entitlement_payload(state=state),
        "hosted_sage_ai": hosted_state,
    }


def enforce_mobile_push_access(
    *,
    workspace: Optional[Dict[str, Any]],
    install: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    state = resolve_workspace_entitlement_state(workspace=workspace, install=install)
    entitlements = state.entitlements
    if not bool(entitlements.get("mobile_push_enabled")) or not bool(entitlements.get("cloud_services_enabled")):
        raise EntitlementDeniedError(
            reason="mobile_push_unavailable",
            message="Mobile push delivery is not included in the current plan. The in-app notification feed still works.",
            entitlement_state=workspace_entitlement_payload(state=state),
        )
    return workspace_entitlement_payload(state=state)


def enforce_mobile_app_access(
    *,
    workspace: Optional[Dict[str, Any]] = None,
    install: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return _enforce_workspace_capability(
        workspace=workspace,
        install=install,
        entitlement_key="mobile_app_enabled",
        reason="mobile_app_unavailable",
        message="Mobile app access is not enabled for this workspace yet.",
    )


def enforce_approvals_access(
    *,
    workspace: Optional[Dict[str, Any]] = None,
    install: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return _enforce_workspace_capability(
        workspace=workspace,
        install=install,
        entitlement_key="approvals_enabled",
        reason="approvals_unavailable",
        message="Approvals are available on paid plans only. Free workspaces can still chat through Telegram or WhatsApp.",
    )


def enforce_artifacts_access(
    *,
    workspace: Optional[Dict[str, Any]] = None,
    install: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return _enforce_workspace_capability(
        workspace=workspace,
        install=install,
        entitlement_key="artifacts_enabled",
        reason="artifacts_unavailable",
        message="Artifacts are available on paid plans only. Upgrade the workspace to inspect generated files and previews.",
    )


def enforce_advanced_features_access(
    *,
    workspace: Optional[Dict[str, Any]] = None,
    install: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return _enforce_workspace_capability(
        workspace=workspace,
        install=install,
        entitlement_key="advanced_features_enabled",
        reason="advanced_features_unavailable",
        message="Advanced computer controls and Gateway management are available on paid plans only.",
    )


def enforce_channel_surface_access(
    provider: str,
    *,
    workspace: Optional[Dict[str, Any]] = None,
    install: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    provider_token = str(provider or "").strip().lower()
    key = {
        "telegram": "telegram_channel_enabled",
        "whatsapp": "whatsapp_channel_enabled",
    }.get(provider_token)
    if not key:
        raise EntitlementDeniedError(
            reason="channel_surface_unknown",
            message=f"Unknown channel provider '{provider_token}'.",
            entitlement_state={},
        )
    return _enforce_workspace_capability(
        workspace=workspace,
        install=install,
        entitlement_key=key,
        reason=f"{provider_token}_channel_unavailable",
        message=f"{provider_token.title()} access is not included in the current workspace plan.",
    )


def enforce_channel_surface_access_for_workspace_id(
    provider: str,
    *,
    workspace_id: str,
    workspace: Optional[Dict[str, Any]] = None,
    install: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    provider_token = str(provider or "").strip().lower()
    key = {
        "telegram": "telegram_channel_enabled",
        "whatsapp": "whatsapp_channel_enabled",
    }.get(provider_token)
    if not key:
        raise EntitlementDeniedError(
            reason="channel_surface_unknown",
            message=f"Unknown channel provider '{provider_token}'.",
            entitlement_state={},
        )
    state = resolve_workspace_entitlement_state_for_workspace_id(
        workspace_id=workspace_id,
        workspace=workspace,
        install=install,
    )
    if not bool(state.entitlements.get(key)):
        raise EntitlementDeniedError(
            reason=f"{provider_token}_channel_unavailable",
            message=f"{provider_token.title()} access is not included in the current workspace plan.",
            entitlement_state=workspace_entitlement_payload(state=state),
        )
    return workspace_entitlement_payload(state=state)
