from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import logging
import threading
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from server_modules import agent_registry_repository, authority_mandate_service, control_plane_repository, entitlements_service, rust_runtime_kernel_client, workspace_context
from server_modules.config_loader import config_bool, config_int


LOGGER = logging.getLogger(__name__)

DEFAULT_QUIET_HOURS_START = 23
DEFAULT_QUIET_HOURS_END = 7
# MAN-294: quiet hours are a WALL-CLOCK concept ("don't wake me between
# 11pm and 7am MY time") and have no honest meaning without knowing whose
# wall clock. Before this fix, _is_within_quiet_hours/_next_allowed_wakeup_
# time called bare datetime.astimezone() -- which converts to the SERVER
# process's OS timezone, not any customer's. That silently worked in dev
# (whatever a laptop's local zone happened to be) and silently "worked" in
# prod too, in the sense that it always returned an answer -- just the
# WRONG one for every workspace not physically colocated with the VPS.
# Production runs Etc/UTC, so every workspace's quiet hours were silently
# evaluated as if the workspace were in UTC, regardless of where its owner
# actually is; a UTC+8 owner got their entire morning silenced.
#
# Neither workspaces nor users store a timezone anywhere in this codebase
# today (checked: workspace metadata, workspace settings routes, user
# profile, control_plane_repository -- nothing). Per the ruling on this
# ticket, the fix is NOT to keep guessing via the server's own clock (that
# "looks correct" only by accident of where the VPS happens to run) and NOT
# to invent a guess (geo-IP, Accept-Language, etc.) -- it is to make the
# default an honest, explicit, documented constant that every workspace
# gets until it configures a real one via SchedulerPolicyBounds.timezone_name
# (resolve_scheduler_policy, below -- workspace/install metadata's
# `scheduler.timezone`, mirroring how `scheduler.quiet_hours` is already
# threaded through). UTC is the safest such default: it is nobody's silent
# guess, it is what an unconfigured workspace already defaults to for every
# other timestamp in this product, and it fails toward "quiet hours land at
# a boundary most users will notice and can correct" rather than toward the
# server operator's own timezone leaking into every tenant's evaluation.
DEFAULT_SCHEDULER_TIMEZONE = "UTC"
DEFAULT_MAX_EVENT_TRIGGERS_PER_HOUR = 4
DEFAULT_MAX_SELF_PROPOSED_PER_HOUR = 2
# STEP 6 (agent-identity plan) / numeric backstops on multi-agent chains:
# every framework studied (OpenAI max_turns, Claude Code subagent depth/
# concurrency caps, AutoGen termination conditions, CrewAI iteration/RPM
# limits) backstops agent reasoning with a hard numeric ceiling, never
# reasoning alone -- see docs/design/multi-agent-coordination-research.md.
# task_assigned wakeups (schedule_task_assigned_wakeup, below) were the
# first per-task wake path live in production; task_commented
# (schedule_task_commented_wakeup, the human-comment-channel trigger) is the
# second, and reuses the exact same per-task counter and error shape rather
# than inventing its own. MAN-66's @-mention-driven wake
# (task_mention_service.dispatch_resolved_mentions) is the third live
# caller -- it also reuses schedule_task_commented_wakeup directly (one
# trigger_kind, "task_commented", regardless of whether the wake was caused
# by "any comment on my assigned task" or "a comment that named me
# specifically"), so it draws on this exact same counter with no new code
# path. This constant is the ceiling for ALL of them: a
# single task_id can generate at most this many wake requests in a rolling
# 24h window, regardless of how many distinct triggers (assignment, comments,
# mentions, retries) fire it. Deliberately looser than the
# workspace-wide hourly caps above it (4/hr event-triggers, 2/hr
# self-proposed) -- this exists to stop ONE task from looping/re-triggering
# itself into an unbounded wake storm, not to replace those broader caps.
# Tunable via EMPYRALIS_MAX_WAKES_PER_TASK_PER_DAY without a code change.
DEFAULT_MAX_WAKES_PER_TASK_PER_DAY = 24
# task_commented's OWN, tighter bound on top of the shared daily ceiling
# above: a human posting several comments in a quick back-and-forth (the
# common case -- someone typing a thought across 3-4 short messages) must
# coalesce into ONE wake, not one per comment. Any task_commented wake
# already logged for this task_id inside this trailing window suppresses a
# new one -- the comment itself is never lost (add_task_comment persists it
# regardless), only the extra wake is. Deliberately much shorter than the
# 24h ceiling: that one guards against a task looping/re-triggering itself
# over a whole day; this one guards against a single human's own burst of
# keystrokes. Tunable via EMPYRALIS_TASK_COMMENT_WAKE_DEBOUNCE_SECONDS.
DEFAULT_TASK_COMMENT_WAKE_DEBOUNCE_SECONDS = 120
DEFAULT_MAX_RUNTIME_SECONDS = 20
DEFAULT_MINIMUM_BATTERY_PERCENT = 20
DEFAULT_WAKE_BATCH_LIMIT = 5
EVENT_TRIGGER_PRIORITY_THRESHOLD = 60
IMMEDIATE_TRIGGER_WINDOW_SECONDS = 5
DEFAULT_WAKE_SCAN_POLL_SECONDS = 20
DEFAULT_WAKE_SCAN_SCOPE_LIMIT = 200

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
    # MAN-294: the wall-clock quiet_hours_start/end above are meaningless
    # without this. Defaulted (not required) so every existing construction
    # site -- tests included -- keeps compiling; resolve_scheduler_policy is
    # the one path that should ever leave this at the default on purpose,
    # and only because nothing more specific is configured anywhere yet.
    timezone_name: str = DEFAULT_SCHEDULER_TIMEZONE

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


def _coerce_timezone_name(value: Any, default: str) -> str:
    """Validate an IANA zone name against the system tzdata, same posture as
    runs_core.py's own ZoneInfo(timezone_name) try/except -- reject rather
    than silently coerce, and fall back to the passed-in `default` (always
    DEFAULT_SCHEDULER_TIMEZONE at every call site today) rather than the
    server's own zone. A misconfigured/garbled value in workspace or install
    metadata must not quietly become "whatever this process happens to run
    on", which is the exact bug this whole fix removes."""
    token = str(value or "").strip()
    if not token:
        return default
    try:
        ZoneInfo(token)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return default
    return token


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
    # MAN-294: threaded through exactly like quiet_hours above -- install
    # metadata wins over workspace metadata, both read from the same
    # `scheduler`/`scheduler_policy`/`plan_limits` metadata keys
    # _workspace_scheduler_metadata/_install_scheduler_metadata already
    # merge. No workspace or install has ever set this key (nothing in this
    # codebase writes `scheduler.timezone` yet), so today every policy
    # resolves to DEFAULT_SCHEDULER_TIMEZONE -- but the field exists and is
    # read now, so the day a workspace-settings surface starts writing it,
    # quiet hours start respecting it with no further change here.
    timezone_name = _coerce_timezone_name(
        install_meta.get("timezone") or workspace_meta.get("timezone"),
        DEFAULT_SCHEDULER_TIMEZONE,
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
        timezone_name=timezone_name,
    )


def _scheduler_zone(policy: SchedulerPolicyBounds) -> ZoneInfo:
    """Resolve policy.timezone_name to a real tzdata zone. policy.timezone_
    name is already validated by _coerce_timezone_name at construction time
    (resolve_scheduler_policy), so this only needs a defensive fallback for
    a SchedulerPolicyBounds built by hand (tests, or a future caller) with a
    bad string -- same DEFAULT_SCHEDULER_TIMEZONE fallback, never the
    server's own zone."""
    try:
        return ZoneInfo(policy.timezone_name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return ZoneInfo(DEFAULT_SCHEDULER_TIMEZONE)


def _is_within_quiet_hours(now_utc: datetime, policy: SchedulerPolicyBounds) -> bool:
    # MAN-294: was bare now_utc.astimezone(), which converts to the SERVER
    # PROCESS's OS timezone -- see the DEFAULT_SCHEDULER_TIMEZONE comment
    # above for why that is wrong for every workspace not colocated with
    # the VPS. Now explicit: the workspace/install-configured zone, or the
    # documented UTC default when none is configured.
    hour = now_utc.astimezone(_scheduler_zone(policy)).hour
    start = int(policy.quiet_hours_start)
    end = int(policy.quiet_hours_end)
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def _next_allowed_wakeup_time(now_utc: datetime, policy: SchedulerPolicyBounds) -> datetime:
    zone = _scheduler_zone(policy)
    local_now = now_utc.astimezone(zone)
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
            # MAN-294: was bare next_allowed_at.astimezone() -- same server-
            # timezone leak as _is_within_quiet_hours/_next_allowed_wakeup_
            # time, just in the human-readable label rather than the gate
            # itself. The owner reading "until 07:00" only trusts that
            # number if it is THEIR 07:00, not the VPS's.
            f"Quiet hours active until {next_allowed_at.astimezone(_scheduler_zone(policy)).strftime('%H:%M')}"
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
    skip_quiet_hours: bool = False,
) -> tuple[datetime, Optional[str]]:
    """MAN-294: `skip_quiet_hours` exists for exactly two callers --
    schedule_task_assigned_wakeup and schedule_task_commented_wakeup -- and
    for one reason: quiet hours exist to stop an AMBIENT trigger (a context-
    engine event, a self-proposed idea) from waking a sleeping device at an
    hour nobody asked for. A human clicking "Assign" or posting a comment in
    a browser is demonstrably awake and took an explicit action right now;
    silently deferring that up to 8 hours is not a quiet-hours protection,
    it is the assignment/comment quietly not happening, which is the whole
    bug this fix closes. Battery and network stay gated regardless -- those
    describe the TARGET DEVICE's ability to do work at all, which an
    assigning human's own wakefulness has no bearing on."""
    adjusted_due_at = due_at
    reason: Optional[str] = None
    if not skip_quiet_hours and _is_within_quiet_hours(adjusted_due_at, policy):
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
            # Context-engine events have no live sender to classify and no
            # parent turn to inherit from — a "system turn with no traceable
            # parent" per authority_mandate_service's model, which resolves
            # directly to audience. Stamped explicitly (not left absent) so
            # consumption never has to guess.
            "authority_tier": authority_mandate_service.TIER_AUDIENCE,
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


async def schedule_task_assigned_wakeup(
    *,
    tenant_id: str,
    workspace_id: str,
    agent_id: str,
    task_id: str,
    title: str,
    description: str = "",
    triggered_by: str = "owner",
) -> Dict[str, Any]:
    """docs/design/tasks-to-agents-research.md Section 4.6 step 3: a new
    trigger reason ("task_assigned"), not a new execution engine. Called by
    project_tasks_service.assign_task (the ONE code path shared by the
    assignment API and a future @-mention resolver) whenever a task's
    assignee_agent_id is set. Reuses maybe_schedule_event_trigger's exact
    persist shape above (_persist_wakeup) -- same claim_due_wake_requests /
    finalize_wake_requests machinery picks this row up on the next scan,
    same as every other wake request kind.

    payload carries `agent_id` (the same field list_wake_requests_for_agent/
    cancel_wake_request already filter on) plus `task_id`/`task_title`/
    `task_description`, so runtime_heartbeat_service.build_heartbeat_turn_
    request can thread task_id into the resulting turn's trace metadata --
    that's the seam direct_chat_generation_service reads at turn start/end
    to seed and persist update_plan's current_plan per-task (Section 4.4).

    MAN-294: quiet hours are SKIPPED here (_apply_policy_to_due_at's
    skip_quiet_hours=True) -- battery and network are still respected. This
    used to run through the full gate including quiet hours, which meant an
    assignment made at 3am silently sat until quiet hours ended (up to 8h
    later) with the UI still saying "In progress" the whole time -- the
    confirmed production bug this fixed. Quiet hours protect a sleeping
    DEVICE from an ambient trigger it never asked for; a human clicking
    "assign" in a browser is, by construction, awake right now, and their
    explicit action must not be silently deferred by a policy meant for
    something else. No approval gate here either (unlike propose_self_
    wakeup's privileged-runtime branch): assigning a task is itself the
    explicit human action, matching the hard constraint that this feature
    adds no approval system beyond what the scheduler already has
    natively."""
    resolved_agent_id = str(agent_id or "").strip()
    resolved_task_id = str(task_id or "").strip()
    resolved_title = str(title or "").strip()
    if not resolved_agent_id or not resolved_task_id or not resolved_title:
        raise SchedulerPolicyError(
            "agent_id, task_id, and title are required to schedule a task-assigned wakeup."
        )
    # STEP 6 numeric backstop: a single task_id may not generate more than
    # max_wakes_per_task_per_day() wake requests in a rolling 24h window --
    # loud and explicit (SchedulerPolicyError), never a silent clamp/drop.
    # This is the enforcement point the future wake-on-mention trigger reuses
    # rather than inventing its own per-task cap; task_assigned is simply the
    # first live trigger kind that can fire repeatedly for the same task_id
    # (re-assignment, re-triggering) today.
    _daily_wake_cap = max_wakes_per_task_per_day()
    _recent_task_wake_count = await control_plane_repository.count_agent_scheduler_wake_requests_since(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        since=_utc_now() - timedelta(hours=24),
        task_id=resolved_task_id,
    )
    if _recent_task_wake_count >= _daily_wake_cap:
        raise SchedulerPolicyError(
            f"Task {resolved_task_id} has already reached its wake ceiling of "
            f"{_daily_wake_cap} wake requests in the last 24 hours. Wait for the "
            "window to roll over, or reduce how often this task re-triggers, "
            "before requesting another wakeup."
        )
    workspace, master_install, policy = await _load_scheduler_scope(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    due_at, due_reason = _apply_policy_to_due_at(
        due_at=_utc_now(),
        policy=policy,
        device_state=_device_state({}, workspace, master_install),
        # MAN-294: an explicit human action (clicking "assign") is not an
        # ambient trigger -- see _apply_policy_to_due_at's own docstring.
        skip_quiet_hours=True,
    )
    metadata: Dict[str, Any] = {"agent_id": resolved_agent_id, "task_id": resolved_task_id}
    if due_reason:
        metadata["policy_delay_reason"] = due_reason
    record = await _persist_wakeup(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        master_install=master_install,
        trigger_kind="task_assigned",
        source="project_tasks",
        requested_by=str(triggered_by or "owner").strip().lower() or "owner",
        reason="task_assigned",
        summary=f"Task assigned: {resolved_title}",
        payload={
            "agent_id": resolved_agent_id,
            "task_id": resolved_task_id,
            "task_title": resolved_title,
            "task_description": str(description or "").strip(),
        },
        policy=policy,
        due_at=due_at,
        approval_required=False,
        status="pending",
        denial_reason=None,
        metadata=metadata,
    )
    if due_at <= _utc_now() + timedelta(seconds=IMMEDIATE_TRIGGER_WINDOW_SECONDS):
        _trigger_ambient_monitor(workspace_id)
    return record


async def schedule_task_commented_wakeup(
    *,
    tenant_id: str,
    workspace_id: str,
    agent_id: str,
    task_id: str,
    title: str,
    comment_body: str = "",
    triggered_by: str = "owner",
) -> Optional[Dict[str, Any]]:
    """The human->agent comment channel's wake trigger (docs/design/
    tasks-to-agents-research.md Section 4.5) -- structurally
    schedule_task_assigned_wakeup's twin, one new trigger_kind
    ("task_commented"), not a new execution engine or a new gate. Called by
    project_tasks_service.add_human_task_comment right after the comment
    itself is durably appended via add_task_comment, and ONLY when the task
    already has an assignee -- an unassigned task has no one to wake, and
    the caller must not reach this function for one (enforced there, not
    re-checked here, since by the time a caller has an agent_id in hand the
    question is already answered).

    No approval gate here, matching schedule_task_assigned_wakeup and this
    feature's own hard constraint ("No approval system"): a comment is an
    inline message an agent picks up on its next turn, never something that
    blocks or requires sign-off. Returns None (not an error) when a wake is
    deliberately not scheduled -- a debounced burst is the expected, healthy
    case, not a failure the caller needs to react to.

    MAN-294: quiet hours are SKIPPED here too (_apply_policy_to_due_at's
    skip_quiet_hours=True), same reasoning as schedule_task_assigned_
    wakeup's own note -- a human posting a comment is an explicit awake
    action, not an ambient trigger, and must not be silently deferred by a
    policy meant to protect a sleeping device from triggers it never asked
    for. Battery and network are still respected.

    Bounding is two separate, stacked backstops:
    1. DEBOUNCE (this function's own, short window): if a task_commented
       wake was already logged for this task_id inside
       max_task_comment_wake_debounce_seconds(), skip -- the comment is
       already durably saved by the time this runs, so nothing is lost, and
       the agent reads the full thread fresh whenever it does wake. This is
       the piece that stops "five comments in a row" from becoming five
       wakes.
    2. DAILY CEILING (shared with schedule_task_assigned_wakeup, STEP 6):
       the same max_wakes_per_task_per_day() rolling-24h cap on this
       task_id, counting every trigger_kind together -- raised loudly as
       SchedulerPolicyError, never a silent drop, exactly like the assigned
       path. A human who keeps commenting across the whole day still can't
       turn one task into an unbounded wake storm.
    """
    resolved_agent_id = str(agent_id or "").strip()
    resolved_task_id = str(task_id or "").strip()
    resolved_title = str(title or "").strip()
    if not resolved_agent_id or not resolved_task_id or not resolved_title:
        raise SchedulerPolicyError(
            "agent_id, task_id, and title are required to schedule a task-commented wakeup."
        )
    # Backstop 1: debounce. Checked before the daily ceiling since a
    # debounced call never persists a row and so must never count against
    # it either -- the two backstops compose, they don't share bookkeeping.
    #
    # Scoped to (task_id, agent_id), not task_id alone -- MAN-66 (mention-
    # driven wakes): a comment mentioning several DIFFERENT agents calls
    # this function once per mentioned agent, back to back, inside the same
    # request. Debouncing on task_id alone would let the FIRST agent's
    # freshly-persisted wake row suppress every other mentioned agent's
    # wake in the same comment -- an accidental collision, not the
    # intentional per-comment bound (see task_mention_service.py's own
    # max_mentioned_agent_wakes_per_comment). Scoping by agent_id also fixes
    # a pre-existing quirk for the single-assignee case: reassigning a task
    # then commenting again inside the debounce window no longer gets
    # suppressed by the OLD assignee's still-recent wake row.
    _debounce_window = max_task_comment_wake_debounce_seconds()
    _recent_comment_wake_count = await control_plane_repository.count_agent_scheduler_wake_requests_since(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        since=_utc_now() - timedelta(seconds=_debounce_window),
        trigger_kind="task_commented",
        task_id=resolved_task_id,
        agent_id=resolved_agent_id,
    )
    if _recent_comment_wake_count >= 1:
        return None
    # Backstop 2: the shared per-task daily ceiling (STEP 6) -- identical
    # check to schedule_task_assigned_wakeup's, deliberately not filtered to
    # trigger_kind so assignment and comment wakes draw from one shared
    # budget per task.
    _daily_wake_cap = max_wakes_per_task_per_day()
    _recent_task_wake_count = await control_plane_repository.count_agent_scheduler_wake_requests_since(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        since=_utc_now() - timedelta(hours=24),
        task_id=resolved_task_id,
    )
    if _recent_task_wake_count >= _daily_wake_cap:
        raise SchedulerPolicyError(
            f"Task {resolved_task_id} has already reached its wake ceiling of "
            f"{_daily_wake_cap} wake requests in the last 24 hours. Wait for the "
            "window to roll over, or reduce how often this task re-triggers, "
            "before requesting another wakeup."
        )
    workspace, master_install, policy = await _load_scheduler_scope(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    due_at, due_reason = _apply_policy_to_due_at(
        due_at=_utc_now(),
        policy=policy,
        device_state=_device_state({}, workspace, master_install),
        # MAN-294: an explicit human action (posting a comment) is not an
        # ambient trigger -- see _apply_policy_to_due_at's own docstring.
        skip_quiet_hours=True,
    )
    metadata: Dict[str, Any] = {"agent_id": resolved_agent_id, "task_id": resolved_task_id}
    if due_reason:
        metadata["policy_delay_reason"] = due_reason
    record = await _persist_wakeup(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        master_install=master_install,
        trigger_kind="task_commented",
        source="project_tasks",
        requested_by=str(triggered_by or "owner").strip().lower() or "owner",
        reason="task_commented",
        summary=f"New comment on: {resolved_title}",
        payload={
            "agent_id": resolved_agent_id,
            "task_id": resolved_task_id,
            "task_title": resolved_title,
            "comment_body": str(comment_body or "").strip()[:500],
        },
        policy=policy,
        due_at=due_at,
        approval_required=False,
        status="pending",
        denial_reason=None,
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


def wake_request_scan_enabled() -> bool:
    return config_bool("EMPYRALIS_WAKE_SCAN_ENABLED", True)


def wake_request_scan_poll_seconds() -> int:
    return max(5, config_int("EMPYRALIS_WAKE_SCAN_POLL_SECONDS", DEFAULT_WAKE_SCAN_POLL_SECONDS))


def max_wakes_per_task_per_day() -> int:
    """The numeric backstop for STEP 6 -- see DEFAULT_MAX_WAKES_PER_TASK_PER_DAY
    above. Env-overridable, floored at 1 so a misconfigured 0/negative value
    can never mean "unlimited"."""
    return max(1, config_int("EMPYRALIS_MAX_WAKES_PER_TASK_PER_DAY", DEFAULT_MAX_WAKES_PER_TASK_PER_DAY))


def max_task_comment_wake_debounce_seconds() -> int:
    """schedule_task_commented_wakeup's own debounce window -- see
    DEFAULT_TASK_COMMENT_WAKE_DEBOUNCE_SECONDS above. Env-overridable,
    floored at 1 so a misconfigured 0/negative value can never mean "no
    debounce" (which would silently re-introduce the one-wake-per-comment
    problem this exists to prevent)."""
    return max(
        1,
        config_int(
            "EMPYRALIS_TASK_COMMENT_WAKE_DEBOUNCE_SECONDS",
            DEFAULT_TASK_COMMENT_WAKE_DEBOUNCE_SECONDS,
        ),
    )


async def scan_due_wake_requests_once(
    *,
    run_workspace_heartbeat: Callable[[List[str], Dict[str, Any]], Any],
    limit: int = DEFAULT_WAKE_SCAN_SCOPE_LIMIT,
) -> Dict[str, Any]:
    """Cross-workspace tick for the wake-request scanner daemon.

    claim_due_wake_requests (above) is intentionally RLS-scoped to one
    (tenant_id, workspace_id) at a time -- there is no single query that can
    claim due wake requests across tenants, by design. This finds which
    scopes currently have due work via a system-level, bypass_rls scan
    (control_plane_repository.list_due_agent_scheduler_wake_request_scopes),
    then runs the existing, already-correct per-workspace claim -> tier-
    grouped execute -> finalize pipeline (run_workspace_heartbeat, i.e.
    runtime_heartbeat_service's heartbeat run callback) once per scope found.
    No new turn-dispatch or authority-tier logic here -- this only adds the
    "which workspaces have work" step RLS otherwise makes invisible to a
    single global query, and reuses everything already proven correct for a
    single workspace (including authority-tier grouping and the pending ->
    executed status transition on success).
    """
    scopes = await control_plane_repository.list_due_agent_scheduler_wake_request_scopes(
        due_before=_utc_now(),
        limit=limit,
    )
    results: List[Dict[str, Any]] = []
    for scope in scopes:
        tenant_id = str(scope.get("tenant_id") or "").strip()
        workspace_id = str(scope.get("workspace_id") or "").strip()
        if not tenant_id or not workspace_id:
            continue
        try:
            outcome = run_workspace_heartbeat(
                [],
                {"workspace_id": workspace_id, "tenant_id": tenant_id, "trigger": "schedule"},
            )
        except Exception as exc:
            # MAN-292: this used to be swallowed into `outcome` with no
            # logging, and the caller (run_wake_request_scan_forever)
            # discards the returned `results` list entirely -- so a
            # per-scope heartbeat failure was invisible everywhere.
            # LOGGER.exception here (full traceback + scope context) is the
            # only place this failure is ever recorded now.
            LOGGER.exception(
                "wake-request-scanner: workspace heartbeat failed for tenant=%s workspace=%s",
                tenant_id, workspace_id,
            )
            outcome = {"acted": False, "summary": f"wake scan failed: {exc}"}
        results.append({"tenant_id": tenant_id, "workspace_id": workspace_id, "result": outcome})
    return {"scanned": len(scopes), "results": results}


def run_wake_request_scan_forever(
    *,
    run_workspace_heartbeat: Callable[[List[str], Dict[str, Any]], Any],
    stop_event: threading.Event,
    poll_seconds: Optional[int] = None,
) -> None:
    """Daemon-thread entry point -- same shape as run_service's
    run_weekly_scheduler_forever (plain while-not-stopped/sleep loop, started
    once at boot). Not asyncio-native since it's started from a sync
    bootstrap context.

    MAN-265 fix: runs on ONE event loop for this thread's entire lifetime
    instead of the old _run_sync helper, which did
    asyncio.new_event_loop() / run_until_complete() / loop.close() fresh on
    EVERY tick. server_modules/db.py's get_pool() caches the Postgres pool
    keyed by id(current_loop) (db.py:150) precisely so a long-lived worker
    reuses one pool -- but a brand-new loop object every
    wake_request_scan_poll_seconds() (default 20s) made every tick look
    like a new caller to that cache, so it tore down the "stale" pool and
    paid for a fresh asyncpg.create_pool(...) every single tick, forever.
    Confirmed live on production: "Postgres pool initialized -- run state
    will be durable" in ~/.pm2/logs/empyralis-error.log at exact 20-second
    intervals. One persistent loop here means db.py's per-loop cache
    actually caches, as designed -- see
    WakeRequestScannerPersistentLoopTests.test_pool_created_once_across_
    multiple_ticks in
    server_modules/tests/test_bounded_scheduler_service.py, which proves
    the pool is created once across multiple ticks.

    A single tick failure must never be allowed to kill this loop (and
    therefore the scanner) for good -- that would turn a transient error
    into a permanent outage, strictly worse than the old wasteful-but-
    resilient per-tick-loop behavior. So each tick's run_until_complete is
    individually try/excepted (MAN-292: and now logged, see
    scan_due_wake_requests_once above and the except below); only
    stop_event controls whether the loop keeps going.
    """
    interval = int(poll_seconds) if poll_seconds is not None else wake_request_scan_poll_seconds()
    interval = max(5, interval)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        while not stop_event.wait(interval):
            try:
                loop.run_until_complete(
                    scan_due_wake_requests_once(run_workspace_heartbeat=run_workspace_heartbeat)
                )
            except Exception:
                LOGGER.exception("wake-request-scanner: tick failed")
                continue
    finally:
        try:
            pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        except Exception:
            LOGGER.exception("wake-request-scanner: error cleaning up pending tasks during shutdown")
        finally:
            try:
                asyncio.set_event_loop(None)
            except Exception:
                LOGGER.debug("wake-request-scanner: asyncio.set_event_loop(None) failed during shutdown", exc_info=True)
            loop.close()


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


# Wake requests that haven't resolved yet — what a "scheduled wake-ups" list
# should show. Terminal states (executed/completed/failed/failed_permanent/
# denied/cancelled/skipped) are history, not something still "scheduled".
NON_TERMINAL_WAKE_STATUSES = {"pending", "claimed", "retry_scheduled"}


async def list_wake_requests_for_agent(
    *,
    tenant_id: str,
    workspace_id: str,
    agent_id: str,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Owner-facing schedule list for one agent. Self-proposed wake-ups only —
    event_trigger rows carry no agent_id in their payload at all (see
    maybe_schedule_event_trigger), so payload->>'agent_id' filtering excludes
    them naturally; this list is specifically "what did/could this agent
    schedule for itself", not the workspace's ambient context-engine
    triggers."""
    rows = await control_plane_repository.list_agent_scheduler_wake_requests(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        agent_id=agent_id,
        limit=max(1, int(limit or 50)),
    )
    return [
        row for row in rows
        if str(row.get("status") or "").strip().lower() in NON_TERMINAL_WAKE_STATUSES
    ]


async def cancel_wake_request(
    *,
    tenant_id: str,
    workspace_id: str,
    agent_id: str,
    wake_id: str,
    cancelled_by: str = "owner",
) -> Dict[str, Any]:
    """Owner-initiated cancel. A status transition, not a hard delete — this
    table has no DELETE statement anywhere in the codebase; every other
    resolution (executed/failed/denied/...) is also just a status update, so
    cancellation follows the same append-only-history convention rather than
    introducing a new one."""
    existing = await control_plane_repository.get_agent_scheduler_wake_request(
        tenant_id=tenant_id, workspace_id=workspace_id, wake_id=wake_id,
    )
    not_found = {"ok": False, "error": f"Scheduled wake-up {wake_id} not found."}
    if existing is None:
        return not_found
    # A row read straight back from the DB (unlike payloads built in-process
    # elsewhere in this module) carries `payload` as a raw JSON string — no
    # jsonb codec is registered on this connection. _coerce_dict only accepts
    # real dicts, so it would silently see {} here and reject every agent_id.
    raw_payload = existing.get("payload")
    if isinstance(raw_payload, str):
        import json as _json
        try:
            raw_payload = _json.loads(raw_payload)
        except Exception:
            raw_payload = {}
    existing_payload = _coerce_dict(raw_payload)
    if str(existing_payload.get("agent_id") or "").strip() != str(agent_id or "").strip():
        return not_found
    current_status = str(existing.get("status") or "").strip().lower()
    if current_status not in NON_TERMINAL_WAKE_STATUSES:
        return {"ok": False, "error": f"Can't cancel a wake-up that's already {current_status}."}
    row = await control_plane_repository.update_agent_scheduler_wake_request_status(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        wake_id=wake_id,
        status="cancelled",
        denial_reason=f"cancelled_by_{cancelled_by}",
    )
    if row is None:
        return {"ok": False, "error": f"Could not cancel wake-up {wake_id}."}
    return {"ok": True, "wake_request": row}


def _wake_request_tier(item: Dict[str, Any]) -> "tuple[str, bool]":
    """Resolve a claimed wake request's authority tier.

    Returns (tier, unattributed). unattributed=True means the row carried no
    tier AND the producing mechanism isn't a recognized no-tier-by-design
    case — a real gap (a legacy row, or an unrecognized producer), not a
    documented "system, no parent" default. Always fails toward audience,
    never owner.
    """
    payload = _coerce_dict(item.get("payload"))
    raw = payload.get("authority_tier")
    if raw is not None and str(raw).strip():
        return authority_mandate_service.normalize_tier(raw), False
    # event_trigger rows are provably context-engine-originated with no live
    # sender and no parent turn to inherit from — the model's own rule for a
    # "system turn with no traceable parent" resolves directly to audience.
    # Known by design, not unattributed (maybe_schedule_event_trigger also
    # stamps this explicitly now; this branch covers pre-existing rows).
    if str(item.get("trigger_kind") or "").strip() == "event_trigger":
        return authority_mandate_service.TIER_AUDIENCE, False
    return authority_mandate_service.TIER_AUDIENCE, True


def _wake_group_message(
    *,
    heartbeat_tasks: List[str],
    wake_requests: List[Dict[str, Any]],
    recent_changes: List[Dict[str, Any]],
    goal_lines: List[str],
    user_preferences: str,
    policy: SchedulerPolicyBounds,
) -> str:
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
    return "\n\n".join(section for section in sections if section.strip())


async def _ledger_unattributed_wake_request(
    *, tenant_id: str, workspace_id: str, item: Dict[str, Any],
) -> None:
    try:
        from server_modules import activity_ledger_service

        wake_id = str(item.get("id") or "").strip()
        await activity_ledger_service.append_activity_event(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_type="system",
            actor_id="bounded_scheduler",
            event_class=authority_mandate_service.MANDATE_UNATTRIBUTED_EVENT_CLASS,
            detail_level="audit_reference",
            action="wake_request_tier_unattributed",
            title="Wake request had no derivable authority tier",
            summary=(
                f"Wake request {wake_id or '(no id)'} (trigger_kind="
                f"{str(item.get('trigger_kind') or '').strip() or 'unknown'}) carried no authority_tier "
                "and its producer isn't a recognized no-tier mechanism. Defaulted to audience."
            ),
            status="logged",
            metadata={
                "wake_request_id": wake_id or None,
                "trigger_kind": str(item.get("trigger_kind") or "").strip() or None,
                "source": str(item.get("source") or "").strip() or None,
            },
        )
    except Exception:
        pass


async def build_wakeup_execution_bundle(
    *,
    tenant_id: str,
    workspace_id: str,
    heartbeat_tasks: List[str],
    wake_requests: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Group due wake requests BY AUTHORITY TIER and build one execution
    bundle per tier — never a single composite that blends them.

    This is the anti-laundering fix: previously every claimed wake request
    (regardless of who/what scheduled it) was merged into one message and
    executed as one turn with no tier at all, which — once a caller reads
    tier from the resulting turn — would otherwise let an audience-scheduled
    instruction (fleet_tools.schedule_task, called mid-conversation with an
    end customer) execute alongside, and be indistinguishable from,
    owner-configured work.

    heartbeat_tasks (the HEARTBEAT.md checklist) are always owner tier — that
    file is workspace-level configuration only the owner edits, never
    audience-writable. Each wake request keeps (or is safely defaulted to)
    its own tier via _wake_request_tier.
    """
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
    # Founder ruling (2026-07-23, final): USER.md is removed from the
    # root-file taxonomy -- onboarding now projects the owner profile into
    # the memory/files/profile.md topic file instead (see
    # sage_profile_service.SAGE_PROFILE_MEMORY_TOPIC_FILE). Read that first;
    # fall back to the raw legacy USER.md file for workspaces that had real
    # content written before this migration (never auto-created or written
    # to anymore, but never deleted either -- see workspace_context.
    # read_legacy_root_file), so the scheduler keeps working for both new
    # and pre-migration workspaces.
    user_preferences = workspace_context.read_workspace_context_file(
        "memory/files/profile.md",
        workspace_id=workspace_id,
    ).strip()
    if not user_preferences:
        user_preferences = workspace_context.read_legacy_root_file(
            "USER.md",
            workspace_id=workspace_id,
        ).strip()
    workspace_meta = _coerce_dict(_coerce_dict(workspace).get("metadata"))
    master_meta = _coerce_dict(_coerce_dict(master_install).get("metadata"))
    goals = list(workspace_meta.get("goals") or master_meta.get("goals") or master_meta.get("scheduler_goals") or [])
    goal_lines = [str(item or "").strip() for item in goals if str(item or "").strip()]

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    unattributed_ids: List[str] = []
    for item in wake_requests:
        tier, unattributed = _wake_request_tier(item)
        grouped.setdefault(tier, []).append(item)
        if unattributed:
            wake_id = str(item.get("id") or "").strip()
            if wake_id:
                unattributed_ids.append(wake_id)
            await _ledger_unattributed_wake_request(tenant_id=tenant_id, workspace_id=workspace_id, item=item)
    if heartbeat_tasks and authority_mandate_service.TIER_OWNER not in grouped:
        grouped[authority_mandate_service.TIER_OWNER] = []

    groups: List[Dict[str, Any]] = []
    for tier, items in grouped.items():
        group_tasks = list(heartbeat_tasks) if tier == authority_mandate_service.TIER_OWNER else []
        message = _wake_group_message(
            heartbeat_tasks=group_tasks,
            wake_requests=items,
            recent_changes=recent_changes,
            goal_lines=goal_lines,
            user_preferences=user_preferences,
            policy=policy,
        )
        wake_request_ids = [str(i.get("id") or "").strip() for i in items if str(i.get("id") or "").strip()]
        context_event_ids = _extract_context_event_ids(items)
        scheduler_mode = "mixed" if (items and group_tasks) else ("wakeup" if items else "heartbeat")
        groups.append({
            "authority_tier": tier,
            "message": message,
            "heartbeat_tasks": group_tasks,
            "wake_requests": items,
            "wake_request_ids": wake_request_ids,
            "context_event_ids": context_event_ids,
            "scheduler_mode": scheduler_mode,
            "summary": (
                f"Scheduler triggered {len(items)} wake request(s) and {len(group_tasks)} heartbeat task(s) as {tier}."
            ),
        })

    return {
        "groups": groups,
        "unattributed_wake_request_ids": unattributed_ids,
        "recent_changes": recent_changes,
        "scheduler_goals": goal_lines,
        "user_preferences": user_preferences,
        "policy": policy.as_dict(),
        "metadata": {
            "source": "bounded_scheduler",
            "scheduler_policy": policy.as_dict(),
            "scheduler_goals": goal_lines,
            "recent_context_change_count": len(recent_changes),
            "group_count": len(groups),
            "group_tiers": [g["authority_tier"] for g in groups],
        },
        "summary": (
            f"Scheduler triggered {len(wake_requests)} wake request(s) across {len(groups)} tier group(s) "
            f"and {len(heartbeat_tasks)} heartbeat task(s)."
            if wake_requests or heartbeat_tasks
            else "No due scheduler work."
        ),
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
