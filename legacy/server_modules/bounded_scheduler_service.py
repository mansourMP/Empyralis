from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import threading
from typing import Any, Callable, Dict, List, Optional

from server_modules import agent_registry_repository, control_plane_repository, entitlements_service, rust_runtime_kernel_client, workspace_context


DEFAULT_QUIET_HOURS_START = 23
DEFAULT_QUIET_HOURS_END = 7
DEFAULT_MAX_EVENT_TRIGGERS_PER_HOUR = 4
DEFAULT_MAX_SELF_PROPOSED_PER_HOUR = 2
DEFAULT_MAX_RUNTIME_SECONDS = 20
DEFAULT_MINIMUM_BATTERY_PERCENT = 20
DEFAULT_WAKE_BATCH_LIMIT = 5
EVENT_TRIGGER_PRIORITY_THRESHOLD = 60
IMMEDIATE_TRIGGER_WINDOW_SECONDS = 5

_AMBIENT_MONITOR_REGISTRY_LOCK = threading.Lock()
_AMBIENT_MONITOR_REGISTRY: dict[str, dict[str, Callable[[], Any]]] = {}


class SchedulerPolicyError(Exception):
    pass


@dataclass(frozen=True)
class SchedulerPolicyBounds:
    quiet_hours_start: int
    quiet_hours_end: int
    max_event_triggers_per_hour: int
    max_self_proposed_per_hour: int
    max_runtime_seconds: int
    minimum_battery_percent: int
    require_network_online: bool
    require_owner_approval_for_privileged_wakeups: bool
    plan_tier: str

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_retries: int = 5
    base_delay_seconds: int = 30
    max_delay_seconds: int = 3600
    backoff_multiplier: float = 2.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "max_retries": self.max_retries,
            "base_delay_seconds": self.base_delay_seconds,
            "max_delay_seconds": self.max_delay_seconds,
            "backoff_multiplier": self.backoff_multiplier,
        }


DEFAULT_RETRY_POLICY = RetryPolicy()


def compute_retry_delay(attempt: int, policy: RetryPolicy | None = None) -> int:
    if policy is None:
        policy = DEFAULT_RETRY_POLICY
    if attempt < 1:
        return policy.base_delay_seconds
    delay = int(policy.base_delay_seconds * (policy.backoff_multiplier ** attempt))
    return min(delay, policy.max_delay_seconds)


def build_retry_metadata(
    *,
    attempt: int,
    policy: RetryPolicy | None = None,
    last_error: str = "",
) -> Dict[str, Any]:
    if policy is None:
        policy = DEFAULT_RETRY_POLICY
    return {
        "retry_attempt": attempt,
        "retry_max": policy.max_retries,
        "retry_delay_seconds": compute_retry_delay(attempt, policy),
        "retry_last_error": str(last_error or "")[:500],
        "retry_next_at": (
            datetime.now(timezone.utc) + timedelta(seconds=compute_retry_delay(attempt, policy))
        ).isoformat().replace("+00:00", "Z"),
    }


def should_retry(attempt: int, policy: RetryPolicy | None = None) -> bool:
    if policy is None:
        policy = DEFAULT_RETRY_POLICY
    return attempt < policy.max_retries


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _coerce_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        resolved = int(value)
    except (TypeError, ValueError):
        resolved = default
    return max(minimum, min(maximum, resolved))


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    token = str(value or "").strip().lower()
    if token in {"1", "true", "yes", "on"}:
        return True
    if token in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _parse_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    token = str(value or "").strip()
    if not token:
        return None
    try:
        parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _enforce_session_scheduler_decision(
    operation: str,
    *,
    workspace_id: str,
    policy: SchedulerPolicyBounds,
    trigger_id: str = "",
    trigger_kind: str = "",
    wake_mode: str = "",
    priority: int = 0,
    attempt: int = 0,
    max_retries: int = DEFAULT_RETRY_POLICY.max_retries,
    base_delay_seconds: int = DEFAULT_RETRY_POLICY.base_delay_seconds,
    max_delay_seconds: int = DEFAULT_RETRY_POLICY.max_delay_seconds,
    status: str = "",
    candidate_count: int = 0,
    owner_approval_provided: bool = False,
    scheduler_enabled: bool = True,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    device_state = _coerce_dict(payload).get("device_state")
    if not isinstance(device_state, dict):
        device_state = {}
    request = {
        "operation": operation,
        "workspace_id": str(workspace_id or "").strip(),
        "trigger_id": str(trigger_id or "").strip(),
        "trigger_kind": str(trigger_kind or "").strip(),
        "wake_mode": str(wake_mode or trigger_kind or "").strip(),
        "priority": int(priority or 0),
        "attempt": int(attempt or 0),
        "max_retries": int(max_retries or 0),
        "base_delay_seconds": int(base_delay_seconds or 1),
        "max_delay_seconds": int(max_delay_seconds or 1),
        "status": str(status or "").strip(),
        "candidate_count": int(candidate_count or 0),
        "scheduler_enabled": bool(scheduler_enabled),
        "quiet_hours_start": policy.quiet_hours_start,
        "quiet_hours_end": policy.quiet_hours_end,
        "current_hour": _utc_now().hour,
        "quiet_hours_override": bool(_coerce_dict(payload).get("quiet_hours_override")),
        "max_event_triggers_per_hour": policy.max_event_triggers_per_hour,
        "max_self_proposed_per_hour": policy.max_self_proposed_per_hour,
        "max_runtime_seconds": policy.max_runtime_seconds,
        "battery_percent": int(device_state.get("battery_percent") or 100),
        "minimum_battery_percent": policy.minimum_battery_percent,
        "network_online": bool(device_state.get("network_online", True)),
        "require_network_online": policy.require_network_online,
        "require_owner_approval_for_privileged_wakeups": policy.require_owner_approval_for_privileged_wakeups,
        "owner_approval_provided": bool(owner_approval_provided),
        "plan_tier": policy.plan_tier,
    }
    decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
        "session-scheduler-decision",
        request,
    )
    expected_next_actions = {
        "event_trigger": {
            "schedule_event_trigger",
            "defer_session_scheduler_operation",
        },
        "self_proposed_trigger": {
            "schedule_self_proposed_trigger",
            "request_session_scheduler_approval",
            "defer_session_scheduler_operation",
        },
        "wake_decision": {
            "trigger_wakeup",
            "request_session_scheduler_approval",
            "defer_session_scheduler_operation",
        },
        "claim_wake_requests": {"claim_due_wake_requests"},
        "finalize_wake_requests": {"finalize_wake_requests"},
        "schedule_retry": {"schedule_retry"},
        "failure_decision": {"record_scheduler_failure"},
    }
    expected = expected_next_actions.get(operation)
    if expected:
        next_action = str(decision.get("next_action") or "").strip()
        if next_action not in expected:
            raise RuntimeError(
                "Rust session scheduler returned unexpected next_action for "
                f"{operation}: {next_action or 'missing'}"
            )
    return decision


def _workspace_scheduler_metadata(workspace: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    metadata = _coerce_dict(_coerce_dict(workspace).get("metadata"))
    return {
        **_coerce_dict(metadata.get("scheduler")),
        **_coerce_dict(metadata.get("scheduler_policy")),
        **_coerce_dict(metadata.get("plan_limits")),
    }


def _install_scheduler_metadata(install: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    metadata = _coerce_dict(_coerce_dict(install).get("metadata"))
    return {
        **_coerce_dict(metadata.get("scheduler")),
        **_coerce_dict(metadata.get("scheduler_policy")),
        **_coerce_dict(metadata.get("plan_limits")),
    }


def resolve_scheduler_policy(
    *,
    workspace: Optional[Dict[str, Any]],
    master_install: Optional[Dict[str, Any]],
) -> SchedulerPolicyBounds:
    workspace_meta = _workspace_scheduler_metadata(workspace)
    install_meta = _install_scheduler_metadata(master_install)
    plan_defaults = entitlements_service.scheduler_policy_defaults(
        workspace=workspace,
        install=master_install,
    )
    plan_tier = str(plan_defaults.get("plan_tier") or entitlements_service.DEFAULT_PLAN_ID).strip() or entitlements_service.DEFAULT_PLAN_ID
    quiet_hours = {
        **_coerce_dict(workspace_meta.get("quiet_hours")),
        **_coerce_dict(install_meta.get("quiet_hours")),
    }
    quiet_start = _coerce_int(
        quiet_hours.get("start"),
        DEFAULT_QUIET_HOURS_START,
        minimum=0,
        maximum=23,
    )
    quiet_end = _coerce_int(
        quiet_hours.get("end"),
        DEFAULT_QUIET_HOURS_END,
        minimum=0,
        maximum=23,
    )
    return SchedulerPolicyBounds(
        quiet_hours_start=quiet_start,
        quiet_hours_end=quiet_end,
        max_event_triggers_per_hour=_coerce_int(
            install_meta.get("max_event_triggers_per_hour")
            or workspace_meta.get("max_event_triggers_per_hour"),
            int(plan_defaults.get("max_event_triggers_per_hour") or DEFAULT_MAX_EVENT_TRIGGERS_PER_HOUR),
            minimum=1,
            maximum=100,
        ),
        max_self_proposed_per_hour=_coerce_int(
            install_meta.get("max_self_proposed_per_hour")
            or workspace_meta.get("max_self_proposed_per_hour"),
            int(plan_defaults.get("max_self_proposed_per_hour") or DEFAULT_MAX_SELF_PROPOSED_PER_HOUR),
            minimum=1,
            maximum=100,
        ),
        max_runtime_seconds=_coerce_int(
            install_meta.get("max_runtime_seconds")
            or workspace_meta.get("max_customer_runtime_seconds")
            or workspace_meta.get("max_runtime_seconds"),
            int(plan_defaults.get("max_runtime_seconds") or DEFAULT_MAX_RUNTIME_SECONDS),
            minimum=5,
            maximum=300,
        ),
        minimum_battery_percent=_coerce_int(
            install_meta.get("minimum_battery_percent")
            or workspace_meta.get("minimum_battery_percent"),
            DEFAULT_MINIMUM_BATTERY_PERCENT,
            minimum=0,
            maximum=100,
        ),
        require_network_online=_coerce_bool(
            install_meta.get("require_network_online")
            if "require_network_online" in install_meta
            else workspace_meta.get("require_network_online"),
            False,
        ),
        require_owner_approval_for_privileged_wakeups=_coerce_bool(
            install_meta.get("require_owner_approval_for_privileged_wakeups")
            if "require_owner_approval_for_privileged_wakeups" in install_meta
            else workspace_meta.get("require_owner_approval_for_privileged_wakeups"),
            True,
        ),
        plan_tier=plan_tier,
    )


def _is_within_quiet_hours(now_utc: datetime, policy: SchedulerPolicyBounds) -> bool:
    hour = now_utc.astimezone().hour
    start = int(policy.quiet_hours_start)
    end = int(policy.quiet_hours_end)
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def _next_allowed_wakeup_time(now_utc: datetime, policy: SchedulerPolicyBounds) -> datetime:
    local_now = now_utc.astimezone()
    if not _is_within_quiet_hours(now_utc, policy):
        return now_utc
    end = int(policy.quiet_hours_end)
    candidate = local_now.replace(hour=end, minute=0, second=0, microsecond=0)
    if candidate <= local_now:
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def quiet_hours_status_snapshot(
    *,
    policy: SchedulerPolicyBounds,
    now_utc: Optional[datetime] = None,
) -> Dict[str, Any]:
    current = now_utc or _utc_now()
    active = _is_within_quiet_hours(current, policy)
    next_allowed_at = _next_allowed_wakeup_time(current, policy)
    return {
        "active": active,
        "label": (
            f"Quiet hours active until {next_allowed_at.astimezone().strftime('%H:%M')}"
            if active
            else "Background work can run now"
        ),
        "next_allowed_at": next_allowed_at.isoformat().replace("+00:00", "Z"),
    }


def register_ambient_monitor(
    *,
    workspace_id: str,
    trigger_now: Callable[[], Any],
    status: Optional[Callable[[], Any]] = None,
) -> None:
    token = str(workspace_id or "").strip()
    if not token:
        return
    with _AMBIENT_MONITOR_REGISTRY_LOCK:
        _AMBIENT_MONITOR_REGISTRY[token] = {
            "trigger_now": trigger_now,
            "status": status or (lambda: {}),
        }


def ambient_monitor_status(workspace_id: str) -> Dict[str, Any]:
    token = str(workspace_id or "").strip()
    with _AMBIENT_MONITOR_REGISTRY_LOCK:
        entry = _AMBIENT_MONITOR_REGISTRY.get(token)
    if not isinstance(entry, dict):
        return {"registered": False}
    status_callback = entry.get("status")
    try:
        snapshot = status_callback() if callable(status_callback) else {}
    except Exception as exc:
        snapshot = {"ok": False, "detail": str(exc)}
    return {
        "registered": True,
        "heartbeat": snapshot if isinstance(snapshot, dict) else {"detail": str(snapshot)},
    }


def _trigger_ambient_monitor(workspace_id: str) -> Optional[Dict[str, Any]]:
    token = str(workspace_id or "").strip()
    with _AMBIENT_MONITOR_REGISTRY_LOCK:
        entry = _AMBIENT_MONITOR_REGISTRY.get(token)
    if not isinstance(entry, dict):
        return None
    callback = entry.get("trigger_now")
    if not callable(callback):
        return None
    try:
        result = callback()
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}
    return result if isinstance(result, dict) else {"ok": True}


async def _load_scheduler_scope(
    *,
    tenant_id: str,
    workspace_id: str,
) -> tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], SchedulerPolicyBounds]:
    workspace = await control_plane_repository.get_workspace_by_id(workspace_id)
    master_install = await agent_registry_repository.get_workspace_master_agent_install(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    policy = resolve_scheduler_policy(workspace=workspace, master_install=master_install)
    return workspace, master_install, policy


def _device_state(policy_context: Optional[Dict[str, Any]], workspace: Optional[Dict[str, Any]], master_install: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    payload = {
        **_coerce_dict(_workspace_scheduler_metadata(workspace).get("device_state")),
        **_coerce_dict(_install_scheduler_metadata(master_install).get("device_state")),
        **_coerce_dict(_coerce_dict(policy_context).get("device_state")),
    }
    return payload


def _apply_policy_to_due_at(
    *,
    due_at: datetime,
    policy: SchedulerPolicyBounds,
    device_state: Dict[str, Any],
) -> tuple[datetime, Optional[str]]:
    adjusted_due_at = due_at
    reason: Optional[str] = None
    if _is_within_quiet_hours(adjusted_due_at, policy):
        adjusted_due_at = _next_allowed_wakeup_time(adjusted_due_at, policy)
        reason = "quiet_hours"
    battery_percent = device_state.get("battery_percent")
    if battery_percent is not None:
        try:
            battery_value = int(battery_percent)
        except (TypeError, ValueError):
            battery_value = None
        if battery_value is not None and battery_value < policy.minimum_battery_percent:
            candidate = _utc_now() + timedelta(minutes=30)
            adjusted_due_at = max(adjusted_due_at, candidate)
            reason = reason or "battery_low"
    if policy.require_network_online and "network_online" in device_state and not _coerce_bool(device_state.get("network_online"), True):
        candidate = _utc_now() + timedelta(minutes=15)
        adjusted_due_at = max(adjusted_due_at, candidate)
        reason = reason or "network_offline"
    return adjusted_due_at, reason


async def _persist_wakeup(
    *,
    tenant_id: str,
    workspace_id: str,
    master_install: Optional[Dict[str, Any]],
    trigger_kind: str,
    source: str,
    requested_by: str,
    reason: str,
    summary: str,
    payload: Optional[Dict[str, Any]],
    policy: SchedulerPolicyBounds,
    due_at: datetime,
    approval_required: bool,
    status: str,
    denial_reason: Optional[str],
    metadata: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    payload_dict = _coerce_dict(payload)
    metadata_dict = _coerce_dict(metadata)
    trigger_id = (
        str(metadata_dict.get("event_id") or "").strip()
        or str(payload_dict.get("run_id") or "").strip()
        or str(payload_dict.get("event_type") or "").strip()
        or str(summary or reason or trigger_kind or "").strip()
    )
    operation = (
        "event_trigger"
        if str(trigger_kind or "").strip() == "event_trigger"
        else "self_proposed_trigger"
        if str(trigger_kind or "").strip() == "self_proposed"
        else "wake_decision"
    )
    _enforce_session_scheduler_decision(
        operation,
        workspace_id=workspace_id,
        policy=policy,
        trigger_id=trigger_id,
        trigger_kind=trigger_kind,
        wake_mode=trigger_kind,
        priority=int(payload_dict.get("priority") or 0),
        status=status,
        owner_approval_provided=not approval_required or _coerce_bool(metadata_dict.get("approval_granted"), False),
        payload={**payload_dict, "device_state": _coerce_dict(metadata_dict.get("device_state"))},
    )
    record = await control_plane_repository.append_agent_scheduler_wake_request(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        master_agent_install_id=str(_coerce_dict(master_install).get("id") or "").strip() or None,
        trigger_kind=trigger_kind,
        source=source,
        requested_by=requested_by,
        reason=reason,
        summary=summary,
        payload=payload_dict,
        policy=policy.as_dict(),
        approval_required=approval_required,
        status=status,
        denial_reason=denial_reason,
        due_at=due_at,
        metadata=metadata_dict,
    )
    if not isinstance(record, dict):
        raise SchedulerPolicyError("Failed to persist scheduler wake request.")
    try:
        from server_modules import activity_ledger_service

        master_install_id = str(_coerce_dict(master_install).get("id") or "").strip() or None
        actor_type = "sage" if master_install_id else "system"
        await activity_ledger_service.append_activity_event(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_type=actor_type,
            actor_id=master_install_id or "scheduler",
            install_id=master_install_id,
            event_class="delegation",
            detail_level="timeline_detail",
            action=trigger_kind,
            run_id=str(_coerce_dict(payload).get("run_id") or "").strip() or None,
            title="Delegated wake request scheduled",
            summary=summary or reason or f"Scheduled {trigger_kind} wake request.",
            status=str(status or "pending").strip().lower() or "pending",
            review_required=bool(approval_required),
            payload={
                "trigger_kind": trigger_kind,
                "source": source,
                "requested_by": requested_by,
                "due_at": due_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            },
            metadata={
                "wake_request_id": str(record.get("id") or "").strip() or None,
                "approval_required": bool(approval_required),
                "denial_reason": str(denial_reason or "").strip() or None,
            },
        )
    except Exception:
        pass
    return record


async def maybe_schedule_event_trigger(
    *,
    tenant_id: str,
    workspace_id: str,
    event: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    event_scope = _coerce_dict(event.get("scope"))
    audience = {str(item or "").strip().lower() for item in list(event_scope.get("audience") or []) if str(item or "").strip()}
    if audience and not audience.intersection({"sage", "workspace", "all"}):
        return None
    priority = int(event.get("priority") or 0)
    if priority < EVENT_TRIGGER_PRIORITY_THRESHOLD:
        return None
    workspace, master_install, policy = await _load_scheduler_scope(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    recent_count = await control_plane_repository.count_agent_scheduler_wake_requests_since(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        since=_utc_now() - timedelta(hours=1),
        trigger_kind="event_trigger",
    )
    due_at = _utc_now()
    status = "pending"
    denial_reason = None
    metadata: Dict[str, Any] = {"event_id": str(event.get("id") or "").strip() or None}
    if recent_count >= policy.max_event_triggers_per_hour:
        due_at = max(due_at, _utc_now() + timedelta(hours=1))
        metadata["deferred_reason"] = "event_rate_limit"
    due_at, due_reason = _apply_policy_to_due_at(
        due_at=due_at,
        policy=policy,
        device_state=_device_state({}, workspace, master_install),
    )
    if due_reason:
        metadata["policy_delay_reason"] = due_reason
    record = await _persist_wakeup(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        master_install=master_install,
        trigger_kind="event_trigger",
        source=str(event.get("source_app") or "context").strip().lower() or "context",
        requested_by="context_engine",
        reason=str(event.get("event_type") or "").strip(),
        summary=str(event.get("summary") or "").strip(),
        payload={
            "context_event_ids": [str(event.get("id") or "").strip()] if str(event.get("id") or "").strip() else [],
            "event_type": str(event.get("event_type") or "").strip(),
            "source_app": str(event.get("source_app") or "").strip(),
            "priority": priority,
        },
        policy=policy,
        due_at=due_at,
        approval_required=False,
        status=status,
        denial_reason=denial_reason,
        metadata=metadata,
    )
    if due_at <= _utc_now() + timedelta(seconds=IMMEDIATE_TRIGGER_WINDOW_SECONDS):
        _trigger_ambient_monitor(workspace_id)
    return record


async def propose_self_wakeup(
    *,
    tenant_id: str,
    workspace_id: str,
    summary: str,
    reason: str,
    due_at: Optional[Any] = None,
    payload: Optional[Dict[str, Any]] = None,
    policy_context: Optional[Dict[str, Any]] = None,
    requested_by: str = "sage",
) -> Dict[str, Any]:
    resolved_summary = str(summary or "").strip()
    resolved_reason = str(reason or "").strip()
    if not resolved_summary:
        raise SchedulerPolicyError("summary is required for self-proposed wakeups.")
    workspace, master_install, policy = await _load_scheduler_scope(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    now_utc = _utc_now()
    requested_due_at = _parse_datetime(due_at) or now_utc
    device_state = _device_state(policy_context, workspace, master_install)
    metadata = _coerce_dict(policy_context)
    approval_required = _coerce_bool(metadata.get("approval_required"), False)
    privileged = _coerce_bool(metadata.get("requires_privileged_runtime"), False)
    if privileged and policy.require_owner_approval_for_privileged_wakeups and not _coerce_bool(metadata.get("approval_granted"), False):
        record = await _persist_wakeup(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            master_install=master_install,
            trigger_kind="self_proposed",
            source="sage",
            requested_by=requested_by,
            reason=resolved_reason,
            summary=resolved_summary,
            payload=payload,
            policy=policy,
            due_at=requested_due_at,
            approval_required=True,
            status="denied",
            denial_reason="approval_required",
            metadata={**metadata, "policy_decision": "denied"},
        )
        return {"wake_request": record, "policy": policy.as_dict(), "accepted": False}
    recent_count = await control_plane_repository.count_agent_scheduler_wake_requests_since(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        since=now_utc - timedelta(hours=1),
        trigger_kind="self_proposed",
    )
    adjusted_due_at = requested_due_at
    if recent_count >= policy.max_self_proposed_per_hour:
        adjusted_due_at = max(adjusted_due_at, now_utc + timedelta(hours=1))
        metadata["deferred_reason"] = "self_proposed_rate_limit"
    adjusted_due_at, due_reason = _apply_policy_to_due_at(
        due_at=adjusted_due_at,
        policy=policy,
        device_state=device_state,
    )
    if due_reason:
        metadata["policy_delay_reason"] = due_reason
    record = await _persist_wakeup(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        master_install=master_install,
        trigger_kind="self_proposed",
        source="sage",
        requested_by=requested_by,
        reason=resolved_reason,
        summary=resolved_summary,
        payload=payload,
        policy=policy,
        due_at=adjusted_due_at,
        approval_required=approval_required,
        status="pending",
        denial_reason=None,
        metadata={**metadata, "policy_decision": "accepted"},
    )
    if adjusted_due_at <= now_utc + timedelta(seconds=IMMEDIATE_TRIGGER_WINDOW_SECONDS):
        _trigger_ambient_monitor(workspace_id)
    return {"wake_request": record, "policy": policy.as_dict(), "accepted": True}


async def claim_due_wake_requests(
    *,
    tenant_id: str,
    workspace_id: str,
    limit: int = DEFAULT_WAKE_BATCH_LIMIT,
) -> Dict[str, Any]:
    workspace, master_install, policy = await _load_scheduler_scope(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    _enforce_session_scheduler_decision(
        "claim_wake_requests",
        workspace_id=workspace_id,
        policy=policy,
        trigger_id="claim_due_wake_requests",
        candidate_count=max(1, int(limit or DEFAULT_WAKE_BATCH_LIMIT)),
    )
    claimed = await control_plane_repository.claim_due_agent_scheduler_wake_requests(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        due_before=_utc_now(),
        limit=max(1, int(limit or DEFAULT_WAKE_BATCH_LIMIT)),
    )
    return {
        "items": claimed,
        "policy": policy.as_dict(),
        "workspace": workspace or {},
        "master_install": master_install or {},
    }


def _extract_context_event_ids(wake_requests: List[Dict[str, Any]]) -> List[str]:
    ids: list[str] = []
    seen = set()
    for item in wake_requests:
        payload = _coerce_dict(item.get("payload"))
        for raw_id in list(payload.get("context_event_ids") or []):
            token = str(raw_id or "").strip()
            if not token or token in seen:
                continue
            seen.add(token)
            ids.append(token)
    return ids


async def finalize_wake_requests(
    *,
    tenant_id: str,
    workspace_id: str,
    wake_requests: List[Dict[str, Any]],
    status: str,
    denial_reason: Optional[str] = None,
    mark_context_seen: bool = False,
    metadata_patch: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    _, _, policy = await _load_scheduler_scope(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    updated: list[dict[str, Any]] = []
    for item in wake_requests:
        wake_id = str(_coerce_dict(item).get("id") or "").strip()
        if not wake_id:
            continue
        _enforce_session_scheduler_decision(
            "finalize_wake_requests",
            workspace_id=workspace_id,
            policy=policy,
            trigger_id=wake_id,
            status=status,
            candidate_count=len(wake_requests),
            payload=_coerce_dict(item),
        )
        row = await control_plane_repository.update_agent_scheduler_wake_request_status(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            wake_id=wake_id,
            status=status,
            denial_reason=denial_reason,
            metadata_patch=metadata_patch,
        )
        if isinstance(row, dict):
            updated.append(row)
    if mark_context_seen:
        event_ids = _extract_context_event_ids(wake_requests)
        if event_ids:
            from server_modules import personal_context_engine

            await personal_context_engine.mark_seen_by_sage(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                event_ids=event_ids,
                mark_all=False,
            )
    return updated


async def build_wakeup_execution_bundle(
    *,
    tenant_id: str,
    workspace_id: str,
    heartbeat_tasks: List[str],
    wake_requests: List[Dict[str, Any]],
) -> Dict[str, Any]:
    workspace, master_install, policy = await _load_scheduler_scope(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    from server_modules import personal_context_engine

    recent_changes = await personal_context_engine.list_events(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        audience="sage",
        limit=8,
        unseen_only=False,
    )
    user_preferences = workspace_context.read_workspace_context_file(
        "USER.md",
        workspace_id=workspace_id,
    ).strip()
    workspace_meta = _coerce_dict(_coerce_dict(workspace).get("metadata"))
    master_meta = _coerce_dict(_coerce_dict(master_install).get("metadata"))
    goals = list(workspace_meta.get("goals") or master_meta.get("goals") or master_meta.get("scheduler_goals") or [])
    goal_lines = [str(item or "").strip() for item in goals if str(item or "").strip()]

    sections: list[str] = []
    if heartbeat_tasks:
        sections.append(
            "Heartbeat checklist tasks:\n" + "\n".join(f"- {task}" for task in heartbeat_tasks)
        )
    if wake_requests:
        wake_lines = []
        for item in wake_requests:
            trigger_kind = str(item.get("trigger_kind") or "wake").strip()
            summary = str(item.get("summary") or item.get("reason") or "").strip()
            if summary:
                wake_lines.append(f"- [{trigger_kind}] {summary}")
        if wake_lines:
            sections.append("Wake reasons:\n" + "\n".join(wake_lines))
    if recent_changes:
        sections.append(
            "Recent context changes:\n"
            + "\n".join(
                f"- [{str(item.get('source_app') or 'context').strip()}] {str(item.get('summary') or '').strip()}"
                for item in recent_changes
                if str(item.get("summary") or "").strip()
            )
        )
    if goal_lines:
        sections.append("Current goals:\n" + "\n".join(f"- {line}" for line in goal_lines))
    if user_preferences:
        sections.append("User preferences:\n" + user_preferences[:2000])
    sections.append(
        "Scheduler policy bounds:\n"
        f"- quiet hours: {policy.quiet_hours_start:02d}:00 to {policy.quiet_hours_end:02d}:00\n"
        f"- max runtime seconds: {policy.max_runtime_seconds}\n"
        f"- plan tier: {policy.plan_tier}"
    )
    sections.append(
        "Review the queued wake reasons and recent structured changes. Decide whether a follow-up is needed now. "
        "If no follow-up is needed, explain briefly and stop. If action is needed, stay inside policy and approval limits."
    )

    wake_request_ids = [str(item.get("id") or "").strip() for item in wake_requests if str(item.get("id") or "").strip()]
    context_event_ids = _extract_context_event_ids(wake_requests)
    scheduler_mode = "mixed"
    if wake_requests and not heartbeat_tasks:
        scheduler_mode = "wakeup"
    elif heartbeat_tasks and not wake_requests:
        scheduler_mode = "heartbeat"
    elif not wake_requests and not heartbeat_tasks:
        scheduler_mode = "idle"
    return {
        "message": "\n\n".join(section for section in sections if section.strip()),
        "metadata": {
            "source": "bounded_scheduler",
            "scheduler_mode": scheduler_mode,
            "wake_request_ids": wake_request_ids,
            "context_event_ids": context_event_ids,
            "scheduler_policy": policy.as_dict(),
            "scheduler_goals": goal_lines,
            "recent_context_change_count": len(recent_changes),
        },
        "recent_changes": recent_changes,
        "scheduler_goals": goal_lines,
        "user_preferences": user_preferences,
        "summary": (
            f"Scheduler triggered {len(wake_requests)} wake request(s) and {len(heartbeat_tasks)} heartbeat task(s)."
            if wake_requests or heartbeat_tasks
            else "No due scheduler work."
        ),
        "policy": policy.as_dict(),
    }


async def scheduler_status_snapshot(
    *,
    tenant_id: str,
    workspace_id: str,
    limit: int = 20,
) -> Dict[str, Any]:
    workspace, master_install, policy = await _load_scheduler_scope(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    pending = await control_plane_repository.list_agent_scheduler_wake_requests(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        status="pending",
        limit=max(1, int(limit or 20)),
    )
    claimed = await control_plane_repository.list_agent_scheduler_wake_requests(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        status="claimed",
        limit=max(1, int(limit or 20)),
    )
    from server_modules import runs_core

    exact_jobs = await runs_core.list_schedules(workspace_id=workspace_id)
    items = list(exact_jobs.get("items") or []) if isinstance(exact_jobs, dict) else []
    return {
        "policy": policy.as_dict(),
        "ambient_monitor": ambient_monitor_status(workspace_id),
        "exact_jobs": {
            "count": len(items),
            "items": items[: min(8, len(items))],
        },
        "wake_queue": {
            "pending_count": len(pending),
            "claimed_count": len(claimed),
            "pending": pending,
            "claimed": claimed,
        },
        "workspace": {
            "workspace_id": workspace_id,
            "tenant_id": tenant_id,
            "master_agent_install_id": str(_coerce_dict(master_install).get("id") or "").strip() or None,
        },
    }


async def schedule_retry(
    *,
    tenant_id: str,
    workspace_id: str,
    wake_request: Dict[str, Any],
    error: str = "",
    retry_policy: RetryPolicy | None = None,
) -> Optional[Dict[str, Any]]:
    if retry_policy is None:
        retry_policy = DEFAULT_RETRY_POLICY
    payload = _coerce_dict(wake_request)
    wake_id = str(payload.get("id") or "").strip()
    if not wake_id:
        return None
    _, _, scheduler_policy = await _load_scheduler_scope(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    existing_meta = _coerce_dict(
        _coerce_dict(payload.get("metadata")).get("retry", payload.get("retry"))
    )
    current_attempt = int(
        existing_meta.get("retry_attempt", existing_meta.get("attempt", 0))
    )
    next_attempt = current_attempt + 1
    if not should_retry(next_attempt, retry_policy):
        _enforce_session_scheduler_decision(
            "failure_decision",
            workspace_id=workspace_id,
            policy=scheduler_policy,
            trigger_id=wake_id,
            attempt=next_attempt,
            max_retries=retry_policy.max_retries,
            base_delay_seconds=retry_policy.base_delay_seconds,
            max_delay_seconds=retry_policy.max_delay_seconds,
            status="failed_permanent",
            payload=payload,
        )
        return await control_plane_repository.update_agent_scheduler_wake_request_status(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            wake_id=wake_id,
            status="failed_permanent",
            denial_reason=f"max_retries_exceeded:{next_attempt}",
        )
    retry_meta = build_retry_metadata(
        attempt=next_attempt,
        policy=retry_policy,
        last_error=str(error or "")[:500],
    )
    delay_seconds = compute_retry_delay(next_attempt, retry_policy)
    due_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
    _enforce_session_scheduler_decision(
        "schedule_retry",
        workspace_id=workspace_id,
        policy=scheduler_policy,
        trigger_id=wake_id,
        attempt=next_attempt,
        max_retries=retry_policy.max_retries,
        base_delay_seconds=retry_policy.base_delay_seconds,
        max_delay_seconds=retry_policy.max_delay_seconds,
        status="pending",
        payload=payload,
    )
    return await control_plane_repository.update_agent_scheduler_wake_request_status(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        wake_id=wake_id,
        status="pending",
        denial_reason=None,
        metadata_patch={
            "retry": retry_meta,
            "due_at": due_at.isoformat().replace("+00:00", "Z"),
        },
    )


def retry_queue_status(workspace_id: str) -> Dict[str, Any]:
    return {
        "workspace_id": str(workspace_id or "").strip(),
        "retry_policy": DEFAULT_RETRY_POLICY.as_dict(),
    }
