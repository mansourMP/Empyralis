from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import logging
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter

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
# Recurring schedules ("every morning at 9am") -- see
# migrations/add_recurring_schedules.sql and create_recurring_schedule/
# _fire_recurring_schedule below for the full design. These constants mirror
# the STEP 6 per-task backstop above (DEFAULT_MAX_WAKES_PER_TASK_PER_DAY):
# a recurring schedule fires through the exact same _persist_wakeup gate as
# every other wake-up, but a cron that fires every minute must not be
# allowed to bypass the SPIRIT of that per-day cap just because it has no
# task_id -- so it gets its own dedicated per-schedule daily ceiling, keyed
# on metadata->>'recurring_schedule_id' rather than task_id.
DEFAULT_MAX_RECURRING_WAKES_PER_DAY = 24
# Bounded-by-default lifetime (build step 6): a recurring schedule created
# with no explicit expires_at/max_occurrences gets this default lifetime
# rather than running forever. 90 days comfortably covers "remind me every
# morning" use cases while still forcing an explicit renewal decision
# instead of an indefinite job nobody remembers exists -- see CLAUDE.md's
# own agent-worktree-disk-exhaustion note for what "nobody remembers this
# job exists" costs in practice, applied here to wake/turn volume instead
# of disk.
DEFAULT_RECURRING_SCHEDULE_LIFETIME_DAYS = 90
# Hard ceilings applied even when the caller explicitly asks for more --
# create_recurring_schedule clamps to these rather than rejecting the
# request outright, so "give me a year" degrades to "you get a year" instead
# of an error.
RECURRING_SCHEDULE_HARD_MAX_LIFETIME_DAYS = 365
RECURRING_SCHEDULE_HARD_MAX_OCCURRENCES = 3650
# Rows returned only ever get shown to their owning workspace (list_recurring_
# schedules is RLS-scoped like everything else); this only bounds a single
# system-level due-scan tick's batch size, same role DEFAULT_WAKE_SCAN_SCOPE_
# LIMIT plays for the wake-request scanner above.
DEFAULT_RECURRING_SCHEDULE_SCAN_LIMIT = 200

# ── Goals ("agent, go negotiate with this supplier and come back with a
# solution") -- see the "Goals: durable outcomes with bounded retry" section
# far below for the full design. Bounded on BOTH axes from creation, unlike
# recurring schedules' nullable pair -- a goal always gets a real
# max_attempts and a real expires_at, no "forever" branch to reach.
DEFAULT_GOAL_MAX_ATTEMPTS = 5
# Hard ceiling even when a caller explicitly asks for more -- create_goal
# clamps rather than rejects, matching _clamp_recurring_schedule_bounds'
# own "degrades to the ceiling" posture.
GOAL_HARD_MAX_ATTEMPTS = 50
DEFAULT_GOAL_LIFETIME_DAYS = 14
GOAL_HARD_MAX_LIFETIME_DAYS = 90
# A goal's own per-goal daily wake ceiling -- same role DEFAULT_MAX_
# RECURRING_WAKES_PER_DAY plays for recurring schedules, tighter here
# because a goal's PRIMARY bound is max_attempts (typically far below this),
# not cadence; this is the safety net under a misconfigured fast retry
# cadence, not the main bound.
DEFAULT_MAX_GOAL_WAKES_PER_DAY = 12
# The backoff shape BETWEEN attempts, reusing RetryPolicy's existing
# base/max/multiplier encoding rather than inventing a second one (see
# RetryPolicy/compute_retry_delay above). Deliberately much longer than
# DEFAULT_RETRY_POLICY's own 30s/3600s pair, which is sized for a
# scheduler-internal operation retrying within one process's lifetime -- a
# goal's "attempt" is a full agent turn doing real-world work (a
# negotiation, a follow-up), and re-trying every 30 seconds would be
# nonsensical for that shape of work. 1h -> 2h -> 4h ... capped at 24h is a
# reasonable default cadence for "try again, but not immediately"; not
# exposed as a goal__create parameter (kept off the tool surface -- "Best,
# not most") since the model has no principled way to pick a better number
# than this without real-world experience.
DEFAULT_GOAL_RETRY_BASE_DELAY_SECONDS = 3600
DEFAULT_GOAL_RETRY_MAX_DELAY_SECONDS = 86400
DEFAULT_GOAL_RETRY_BACKOFF_MULTIPLIER = 2.0
# The fallback instruction layer (build step 4) when a caller creates a
# goal without authoring its own escalation rule. This is deliberately
# generic -- the whole point of the instruction field is that a human (or
# the model itself, via goal__update) writes the SPECIFIC rule ("retry
# once, offer a different discount tier, escalate after 3 attempts") for
# this goal; this default only keeps the tool usable without one, mirroring
# OpenClaw's own `goal` tool default framing (docs/automation/
# standing-orders.md's reference shape, ported as a default string here
# rather than a second injected-document mechanism -- see the "Goals" build
# section below for why).
DEFAULT_GOAL_INSTRUCTION = (
    "Work this goal each time you wake. If your last approach was rejected "
    "or blocked, try a different, reasonable variation before giving up -- "
    "never repeat an identical request that already failed once. If you "
    "are genuinely stuck and need information or a decision only a human "
    "can give, set status to 'awaiting_input' or 'blocked' and say exactly "
    "what you need. When the goal is achieved, set status to 'done' and "
    "summarize the outcome. If you conclude the goal cannot be achieved, "
    "set status to 'cancelled' and say why -- do not keep retrying a dead "
    "end. You have a limited number of attempts; use them deliberately."
)

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


# ── Recurring schedules: cron parsing and next-fire computation ─────────
# Library, not hand-rolled: croniter (already a dependency -- server_modules/
# runs_core.py has used it in production for its own separate cron-based
# schedule feature since before this change; requirements.txt already pins
# croniter>=2.0.0). Cron edge cases (DST transitions, month rollovers,
# `*/N` on a field that doesn't divide evenly) are exactly the class of bug
# a hand-rolled parser gets wrong in ways that only show up twice a year --
# croniter is a maintained, widely-used library built for precisely this,
# and duplicating its logic here would just be a second, worse
# implementation of the same thing runs_core.py already trusts.
_CRON_FIELD_COUNT = 5


def parse_cron_expression(cron_expr: str) -> str:
    """Validate a standard 5-field cron expression (minute hour day month
    weekday). Returns the normalized (stripped) expression on success.

    FAILS LOUD: raises SchedulerPolicyError with a specific reason on any
    invalid input -- blank, wrong field count, or a field croniter itself
    rejects. This is the fix for the exact bug that motivated this feature:
    fleet_tools._parse_when's docstring claimed a cron string was "passed
    through for cron scheduling," but no branch ever matched one, so it fell
    through to `return None` and a cron expression was silently rejected as
    unparseable with no error at all. That silent-None behavior is now
    reserved for genuinely non-cron, non-datetime, non-relative `when`
    strings in _parse_when -- an input that LOOKS like cron shape but is
    invalid now raises here instead of anywhere silently swallowing it.
    """
    token = str(cron_expr or "").strip()
    if not token:
        raise SchedulerPolicyError("Cron expression is required and cannot be blank.")
    fields = token.split()
    if len(fields) != _CRON_FIELD_COUNT:
        raise SchedulerPolicyError(
            f"Cron expression must have exactly {_CRON_FIELD_COUNT} fields "
            f"(minute hour day month weekday), got {len(fields)}: {token!r}. "
            "Seconds/year extensions are not supported."
        )
    if not croniter.is_valid(token):
        raise SchedulerPolicyError(f"Invalid cron expression: {token!r}.")
    return token


def compute_next_cron_fire_at(
    cron_expr: str,
    policy: SchedulerPolicyBounds,
    *,
    after: Optional[datetime] = None,
) -> datetime:
    """Resolve a validated cron expression's next fire time, in the
    WORKSPACE's timezone (policy.timezone_name, same resolve_scheduler_
    policy/_scheduler_zone this module already uses for quiet hours -- see
    the MAN-294 comment on DEFAULT_SCHEDULER_TIMEZONE above for why that
    must never be the server process's own zone). "Every morning at 9am"
    means 9am in the workspace's timezone, not UTC and not wherever the VPS
    happens to run.

    DST correctness: croniter is handed a timezone-AWARE reference datetime
    (via ZoneInfo, not a fixed UTC offset), which is what lets it compute
    the next WALL-CLOCK match correctly across a DST transition -- a fixed-
    offset "9am" would silently become 8am or 10am local time the day the
    clock changes. Reference: runs_core._compute_schedule_next_run_at, which
    already does exactly this for its own separate cron feature; the same
    call shape is used deliberately rather than inventing a second one.
    """
    validated = parse_cron_expression(cron_expr)
    zone = _scheduler_zone(policy)
    reference = (after or _utc_now()).astimezone(zone)
    iterator = croniter(validated, reference)
    candidate = iterator.get_next(datetime)
    if candidate.tzinfo is None:
        candidate = candidate.replace(tzinfo=zone)
    return candidate.astimezone(timezone.utc)


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


def _resolve_delegation_actor(
    *,
    master_install: Optional[Dict[str, Any]],
    attributed_agent_install_id: Optional[str] = None,
) -> Tuple[str, str, Optional[str]]:
    """Every ledger write in this module used to unconditionally stamp the
    workspace's own Sage/Operator install (master_install) as the actor of a
    delegated wake/goal/recurring-schedule event -- even for trigger kinds
    that already have a REAL specialist agent's install_id in hand
    (task_assigned, task_commented, a goal's own agent_id). MAN-304 (3): a
    brand-new user assigning their own agent a task saw the Inbox attribute
    "Delegated wake request scheduled" to "Sage" -- an entity they never
    created and could not find anywhere, because agentNameByInstall
    (frontend/app/(account)/w/[workspaceId]/inbox/page.tsx) resolves the
    sender purely from install_id, and this module always sent Sage's.

    One resolver, called everywhere this module writes an actor onto a
    ledger event, so a future trigger kind cannot reintroduce the mistake at
    its own call site: pass the real per-agent install_id whenever the
    caller already has one (it always does for task/goal triggers), and it
    wins; only genuinely Sage-owned triggers (self-proposed wakeups,
    context-engine event triggers) have no such id and fall back to
    Sage/system exactly as before.
    """
    resolved_agent_install_id = str(attributed_agent_install_id or "").strip()
    if resolved_agent_install_id:
        return "agent", resolved_agent_install_id, resolved_agent_install_id
    master_install_id = str(_coerce_dict(master_install).get("id") or "").strip() or None
    actor_type = "sage" if master_install_id else "system"
    return actor_type, master_install_id or "scheduler", master_install_id


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
    attributed_agent_install_id: Optional[str] = None,
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

        actor_type, actor_id, install_id = _resolve_delegation_actor(
            master_install=master_install,
            attributed_agent_install_id=attributed_agent_install_id,
        )
        await activity_ledger_service.append_activity_event(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_type=actor_type,
            actor_id=actor_id,
            install_id=install_id,
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
    authority_tier: Optional[str] = None,
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
    natively.

    AUTHORITY TIER (found and fixed 2026-08-13): this function used to omit
    `authority_tier` from the persisted payload entirely, unlike its sibling
    maybe_schedule_event_trigger above (which explicitly stamps TIER_AUDIENCE
    for its own genuinely-senderless context-engine events). The consuming
    scan (`_wake_request_tier`) treats an absent tier from an unrecognized
    producer as a REAL gap, not a documented default, and fails safe to
    audience — so every task-assigned wake ran as audience tier, and at the
    time an audience-tier turn could only call `audience_safe` tools.
    `project_task.list` was not one, so the resulting turn could not even
    read the task it had just been handed (`mandate_blocked`, observed live
    2026-08-13). That tier is gone as of 2026-08-21 (see
    authority_mandate_service) and this specific symptom can no longer recur,
    but the tier is still what a wake carries as its recorded principal and
    is still what decides the machine-administration floor, so stamping it
    correctly still matters. Two
    distinct callers, two distinct correct tiers, so this takes an explicit
    `authority_tier` rather than hardcoding one value the way
    maybe_schedule_event_trigger safely can:
      - A human assigning via the HTTP route (routes_fleet.fleet_assign_task
        -> project_tasks_service.assign_task) passes no turn to inherit a
        tier from at all — but the route itself already required at least
        `member` role to reach this point (MAN-64/MAN-70), so it is never a
        bare/audience trigger. Leaving `authority_tier` at its default
        (None) resolves to TIER_OWNER below. The mandate model has no tier
        between "owner" and "audience" today (a documented, separate gap —
        see CLAUDE.md's authority-mandate note), so this is the closest
        correct value, not a redesign of the tier taxonomy; a future
        project-teammate tier should replace it here too.
      - An agent delegating via the project_task__assign TOOL (a turn
        already running under its own resolved tier) must INHERIT that
        tier, never be upgraded to owner just because it happened to call
        this function — an audience-tier turn delegating a task must not be
        able to mint an owner-tier wake for itself. skills_service.py's
        dispatcher passes its own `session_ctx["authority_tier"]` through
        assign_task -> here explicitly for this reason."""
    resolved_tier = (
        authority_mandate_service.inherit_tier(authority_tier)
        if authority_tier is not None
        else authority_mandate_service.TIER_OWNER
    )
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
            "authority_tier": resolved_tier,
        },
        policy=policy,
        due_at=due_at,
        approval_required=False,
        status="pending",
        denial_reason=None,
        metadata=metadata,
        attributed_agent_install_id=resolved_agent_id,
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
        attributed_agent_install_id=resolved_agent_id,
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


# ── Recurring schedules: create / list / cancel / fire ──────────────────
# Built ON TOP OF the wake-request machinery above, not beside it: a
# recurring schedule row is only ever a generator of ordinary wake requests.
# Every fire goes through _persist_wakeup (via propose_self_wakeup's own
# gate, reused directly below) -- same quiet-hours, battery/network, and
# rate-cap enforcement as task_assigned/task_commented/self_proposed. No
# parallel scheduler thread: _fire_recurring_schedule is called from the
# SAME daemon tick as the existing wake-request scanner (see
# scan_due_wake_requests_once below).

RECURRING_SCHEDULE_NON_TERMINAL_STATUSES = {"active"}


def max_recurring_wakes_per_day() -> int:
    """Recurring schedules' own daily wake ceiling -- see
    DEFAULT_MAX_RECURRING_WAKES_PER_DAY above. Env-overridable, floored at 1
    for the same reason every other cap in this module is: a misconfigured
    0/negative override can never mean "unlimited"."""
    return max(
        1,
        config_int("EMPYRALIS_MAX_RECURRING_WAKES_PER_DAY", DEFAULT_MAX_RECURRING_WAKES_PER_DAY),
    )


def _clamp_recurring_schedule_bounds(
    *,
    now_utc: datetime,
    max_occurrences: Optional[int],
    expires_at: Optional[datetime],
) -> tuple[Optional[int], datetime]:
    """Build step 6 (bounded by default): every recurring schedule gets a
    lifetime bound. If the caller supplied neither max_occurrences nor
    expires_at, default to DEFAULT_RECURRING_SCHEDULE_LIFETIME_DAYS out.
    Whatever ends up in play -- caller-supplied or defaulted -- is clamped to
    the hard ceilings so "give me a year of every-5-minutes wakes" degrades
    to the ceiling rather than either erroring or actually running for a
    year unattended."""
    resolved_max_occurrences = None
    if max_occurrences is not None:
        try:
            resolved_max_occurrences = max(1, min(int(max_occurrences), RECURRING_SCHEDULE_HARD_MAX_OCCURRENCES))
        except (TypeError, ValueError):
            resolved_max_occurrences = None
    hard_ceiling_at = now_utc + timedelta(days=RECURRING_SCHEDULE_HARD_MAX_LIFETIME_DAYS)
    if expires_at is not None:
        resolved_expires_at = min(expires_at, hard_ceiling_at)
    elif resolved_max_occurrences is not None:
        # An explicit occurrence bound with no explicit expiry still gets the
        # hard lifetime ceiling as a backstop -- an occurrence cap alone
        # doesn't protect against a schedule that fires so rarely it would
        # otherwise still be "active" a decade from now.
        resolved_expires_at = hard_ceiling_at
    else:
        resolved_expires_at = min(
            now_utc + timedelta(days=DEFAULT_RECURRING_SCHEDULE_LIFETIME_DAYS),
            hard_ceiling_at,
        )
    return resolved_max_occurrences, resolved_expires_at


def recurring_schedule_view(row: Dict[str, Any]) -> Dict[str, Any]:
    payload = _coerce_dict(row.get("payload"))
    metadata = _coerce_dict(row.get("metadata"))
    next_fire_at = row.get("next_fire_at")
    last_fired_at = row.get("last_fired_at")
    expires_at = row.get("expires_at")
    created_at = row.get("created_at")
    return {
        "id": str(row.get("id") or ""),
        "agent_id": str(row.get("agent_id") or ""),
        "cron_expression": str(row.get("cron_expression") or ""),
        "summary": str(row.get("summary") or "").strip() or str(payload.get("instruction") or "")[:200],
        "instruction": str(payload.get("instruction") or "").strip(),
        "status": str(row.get("status") or "active").strip().lower(),
        "requested_by": str(row.get("requested_by") or "owner").strip().lower(),
        "next_fire_at": next_fire_at.isoformat() if hasattr(next_fire_at, "isoformat") else str(next_fire_at or ""),
        "last_fired_at": last_fired_at.isoformat() if hasattr(last_fired_at, "isoformat") else (str(last_fired_at) if last_fired_at else None),
        "occurrence_count": int(row.get("occurrence_count") or 0),
        "max_occurrences": row.get("max_occurrences"),
        "expires_at": expires_at.isoformat() if hasattr(expires_at, "isoformat") else (str(expires_at) if expires_at else None),
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at or ""),
        "last_skip_reason": metadata.get("last_skip_reason"),
    }


async def create_recurring_schedule(
    *,
    tenant_id: str,
    workspace_id: str,
    agent_id: str,
    cron_expression: str,
    summary: str = "",
    instruction: str = "",
    authority_tier: Optional[str] = None,
    requested_by: str = "owner",
    max_occurrences: Optional[int] = None,
    expires_at: Optional[Any] = None,
) -> Dict[str, Any]:
    """Create a recurring wake schedule for *agent_id*. FAILS LOUD
    (SchedulerPolicyError) on an invalid cron expression or a missing
    agent_id/instruction -- never silently drops the request, matching this
    feature's own hard constraint."""
    resolved_agent_id = str(agent_id or "").strip()
    resolved_instruction = str(instruction or "").strip()
    if not resolved_agent_id:
        raise SchedulerPolicyError("agent_id is required to create a recurring schedule.")
    if not resolved_instruction:
        raise SchedulerPolicyError("instruction is required — what should the agent do each time it wakes?")
    validated_cron = parse_cron_expression(cron_expression)
    workspace, master_install, policy = await _load_scheduler_scope(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    now_utc = _utc_now()
    resolved_max_occurrences, resolved_expires_at = _clamp_recurring_schedule_bounds(
        now_utc=now_utc,
        max_occurrences=max_occurrences,
        expires_at=_parse_datetime(expires_at),
    )
    next_fire_at = compute_next_cron_fire_at(validated_cron, policy, after=now_utc)
    resolved_tier = authority_mandate_service.inherit_tier(authority_tier)
    record = await control_plane_repository.append_agent_recurring_schedule(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        master_agent_install_id=str(_coerce_dict(master_install).get("id") or "").strip() or None,
        agent_id=resolved_agent_id,
        cron_expression=validated_cron,
        summary=str(summary or "").strip() or resolved_instruction[:200],
        payload={
            "instruction": resolved_instruction,
            "agent_id": resolved_agent_id,
            "authority_tier": resolved_tier,
        },
        requested_by=requested_by,
        next_fire_at=next_fire_at,
        max_occurrences=resolved_max_occurrences,
        expires_at=resolved_expires_at,
        metadata={},
    )
    if not isinstance(record, dict):
        raise SchedulerPolicyError("Failed to persist recurring schedule.")
    try:
        from server_modules import activity_ledger_service

        actor_type, actor_id, install_id = _resolve_delegation_actor(
            master_install=master_install,
            attributed_agent_install_id=resolved_agent_id,
        )
        await activity_ledger_service.append_activity_event(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_type=actor_type,
            actor_id=actor_id,
            install_id=install_id,
            event_class="delegation",
            detail_level="timeline_detail",
            action="recurring_schedule_created",
            title="Recurring wake schedule created",
            summary=f"{resolved_instruction[:150]} ({validated_cron})",
            status="active",
            metadata={
                "schedule_id": str(record.get("id") or "").strip() or None,
                "agent_id": resolved_agent_id,
                "cron_expression": validated_cron,
            },
        )
    except Exception:
        pass
    return record


async def list_recurring_schedules(
    *,
    tenant_id: str,
    workspace_id: str,
    agent_id: Optional[str] = None,
    include_terminal: bool = False,
) -> List[Dict[str, Any]]:
    rows = await control_plane_repository.list_agent_recurring_schedules(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        agent_id=agent_id,
    )
    if include_terminal:
        return rows
    return [
        row for row in rows
        if str(row.get("status") or "").strip().lower() in RECURRING_SCHEDULE_NON_TERMINAL_STATUSES
    ]


async def cancel_recurring_schedule(
    *,
    tenant_id: str,
    workspace_id: str,
    agent_id: str,
    schedule_id: str,
    cancelled_by: str = "owner",
) -> Dict[str, Any]:
    """Owner- or agent-initiated cancel. Status transition, not a hard
    delete -- matches cancel_wake_request's own append-only-history
    convention above."""
    existing = await control_plane_repository.get_agent_recurring_schedule(
        tenant_id=tenant_id, workspace_id=workspace_id, schedule_id=schedule_id,
    )
    not_found = {"ok": False, "error": f"Recurring schedule {schedule_id} not found."}
    if existing is None:
        return not_found
    if str(existing.get("agent_id") or "").strip() != str(agent_id or "").strip():
        return not_found
    current_status = str(existing.get("status") or "").strip().lower()
    if current_status not in RECURRING_SCHEDULE_NON_TERMINAL_STATUSES:
        return {"ok": False, "error": f"Can't cancel a recurring schedule that's already {current_status}."}
    row = await control_plane_repository.update_agent_recurring_schedule(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        schedule_id=schedule_id,
        status="cancelled",
        metadata_patch={"cancelled_by": cancelled_by, "cancelled_at": _utc_now().isoformat().replace("+00:00", "Z")},
    )
    if row is None:
        return {"ok": False, "error": f"Could not cancel recurring schedule {schedule_id}."}
    return {"ok": True, "schedule": row}


async def _fire_recurring_schedule(schedule: Dict[str, Any]) -> Dict[str, Any]:
    """Process one due recurring schedule: either propose exactly one
    ordinary wake request through the standard _persist_wakeup gate, or skip
    this occurrence (daily cap, expired), then always advance next_fire_at.

    A single misbehaving schedule must never take down the scan tick -- the
    caller (scan_due_wake_requests_once) wraps this per-schedule, same
    posture as its existing per-workspace heartbeat try/except below.

    Missed-occurrence handling: next_fire_at is always recomputed from NOW,
    never from the missed slot. If the process was down for two days, a
    "every hour" schedule fires ONCE on the next tick (not 48 queued
    catch-up wakes) and resumes its normal cadence from there -- the daily
    cap above exists to bound a MISCONFIGURED cron, this is what bounds an
    ordinary OUTAGE.
    """
    tenant_id = str(schedule.get("tenant_id") or "").strip()
    workspace_id = str(schedule.get("workspace_id") or "").strip()
    schedule_id = str(schedule.get("id") or "").strip()
    agent_id = str(schedule.get("agent_id") or "").strip()
    cron_expression = str(schedule.get("cron_expression") or "").strip()
    payload = _coerce_dict(schedule.get("payload"))
    instruction = str(payload.get("instruction") or "").strip()
    now_utc = _utc_now()

    workspace, master_install, policy = await _load_scheduler_scope(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )

    expires_at = _parse_datetime(schedule.get("expires_at"))
    max_occurrences = schedule.get("max_occurrences")
    occurrence_count = int(schedule.get("occurrence_count") or 0)
    if (expires_at is not None and now_utc >= expires_at) or (
        max_occurrences is not None and occurrence_count >= int(max_occurrences)
    ):
        await control_plane_repository.update_agent_recurring_schedule(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            schedule_id=schedule_id,
            status="expired",
        )
        return {"schedule_id": schedule_id, "action": "expired"}

    recent_count = await control_plane_repository.count_agent_scheduler_wake_requests_since(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        since=now_utc - timedelta(hours=24),
        recurring_schedule_id=schedule_id,
    )
    daily_cap = max_recurring_wakes_per_day()
    next_fire_at = compute_next_cron_fire_at(cron_expression, policy, after=now_utc)
    if recent_count >= daily_cap:
        await control_plane_repository.update_agent_recurring_schedule(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            schedule_id=schedule_id,
            next_fire_at=next_fire_at,
            metadata_patch={
                "last_skip_reason": "recurring_daily_wake_cap",
                "last_skip_at": now_utc.isoformat().replace("+00:00", "Z"),
            },
        )
        return {"schedule_id": schedule_id, "action": "skipped_daily_cap"}

    due_at, due_reason = _apply_policy_to_due_at(
        due_at=now_utc,
        policy=policy,
        device_state=_device_state({}, workspace, master_install),
        # Recurring fires are ambient triggers, not a live human action
        # taken right now -- unlike schedule_task_assigned_wakeup/schedule_
        # task_commented_wakeup, quiet hours are NOT skipped here. A 2am
        # cron in an 11pm-7am quiet window is exactly the case quiet hours
        # exist to defer.
        skip_quiet_hours=False,
    )
    metadata: Dict[str, Any] = {
        "agent_id": agent_id,
        "recurring_schedule_id": schedule_id,
        "authority_tier": authority_mandate_service.normalize_tier(payload.get("authority_tier")),
    }
    if due_reason:
        metadata["policy_delay_reason"] = due_reason
    record = await _persist_wakeup(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        master_install=master_install,
        trigger_kind="recurring",
        source="recurring_schedule",
        requested_by=str(schedule.get("requested_by") or "owner").strip().lower() or "owner",
        reason="recurring_schedule",
        summary=str(schedule.get("summary") or instruction[:200]).strip(),
        payload={
            "instruction": instruction,
            "agent_id": agent_id,
            "recurring_schedule_id": schedule_id,
            "authority_tier": metadata["authority_tier"],
        },
        policy=policy,
        due_at=due_at,
        approval_required=False,
        status="pending",
        denial_reason=None,
        metadata=metadata,
        attributed_agent_install_id=agent_id,
    )
    if due_at <= now_utc + timedelta(seconds=IMMEDIATE_TRIGGER_WINDOW_SECONDS):
        _trigger_ambient_monitor(workspace_id)
    await control_plane_repository.update_agent_recurring_schedule(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        schedule_id=schedule_id,
        next_fire_at=next_fire_at,
        last_fired_at=now_utc,
        occurrence_count=occurrence_count + 1,
        metadata_patch={"last_skip_reason": None, "last_wake_request_id": str(_coerce_dict(record).get("id") or "") or None},
    )
    return {"schedule_id": schedule_id, "action": "fired", "wake_request_id": str(_coerce_dict(record).get("id") or "")}


async def process_due_recurring_schedules_once(
    *,
    limit: int = DEFAULT_RECURRING_SCHEDULE_SCAN_LIMIT,
) -> Dict[str, Any]:
    """Cross-workspace tick for due recurring schedules -- structurally
    schedule_due_wake_requests_once's twin: a system-level bypass_rls scan
    finds which (tenant_id, workspace_id) scopes have due work, then this
    reloads each due schedule through the normal RLS-scoped path and fires
    it. Called from the SAME daemon tick as the wake-request scan (see
    run_wake_request_scan_forever), not a second thread."""
    now_utc = _utc_now()
    scopes = await control_plane_repository.list_due_agent_recurring_schedule_scopes(
        due_before=now_utc,
        limit=limit,
    )
    results: List[Dict[str, Any]] = []
    for scope in scopes:
        tenant_id = str(scope.get("tenant_id") or "").strip()
        workspace_id = str(scope.get("workspace_id") or "").strip()
        if not tenant_id or not workspace_id:
            continue
        due_schedules = await control_plane_repository.list_agent_recurring_schedules(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            status="active",
        )
        for schedule in due_schedules:
            next_fire_at = _parse_datetime(schedule.get("next_fire_at"))
            if next_fire_at is None or next_fire_at > now_utc:
                continue
            try:
                outcome = await _fire_recurring_schedule(schedule)
            except Exception:
                LOGGER.exception(
                    "recurring-schedule-scan: fire failed for tenant=%s workspace=%s schedule=%s",
                    tenant_id, workspace_id, schedule.get("id"),
                )
                outcome = {"schedule_id": str(schedule.get("id") or ""), "action": "error"}
            results.append({"tenant_id": tenant_id, "workspace_id": workspace_id, "result": outcome})
    return {"scanned": len(results), "results": results}


# ── Goals: durable outcomes with bounded retry ───────────────────────────
# "agent, go to this person and negotiate ... and if the person says no, it
# would either try again, or offer something different" (the founder's own
# framing). Built ON TOP OF the exact same wake-request machinery every
# other trigger kind above uses -- a goal row is only ever a generator of
# ordinary wake requests, never a second execution path. Every fire goes
# through _persist_wakeup, same quiet-hours/battery/network/rate-cap
# enforcement as task_assigned/task_commented/self_proposed/recurring.
#
# STRUCTURALLY this is agent_recurring_schedules' twin -- same "a row is
# bookkeeping about WHEN to next ask for a wake-up" shape, fired from the
# SAME scan tick (process_due_goals_once, called from scan_due_wake_
# requests_once below, right alongside process_due_recurring_schedules_
# once) -- no second scheduler thread. Two things make a goal more than a
# renamed recurring schedule:
#   1. a STATUS VOCABULARY (below) a plain reminder has no use for --
#      todo/in_progress/awaiting_input/blocked/in_review/done are lifted
#      verbatim from project_tasks_service.TASK_STATUS_ORDER (same words,
#      same meaning: a goal being worked by an agent moves through the
#      exact states a task does), plus two goal-specific terminal states
#      (`exhausted`, `cancelled`) that vocabulary has no way to express --
#      see GOAL_STATUS_ORDER's own comment.
#   2. a bounded ATTEMPT COUNTER the system alone advances (_fire_goal,
#      below, bumps it exactly once per wake it actually persists -- never
#      the model narrating "I tried again"), with an exponential backoff
#      between attempts (RetryPolicy/compute_retry_delay, already defined
#      above for an unrelated scheduler-internal use -- reused here rather
#      than inventing a second backoff shape) instead of a fixed cron.
#
# THE INSTRUCTION LAYER (build step 4): `instruction` is a plain-text
# column on this row, threaded into the wake turn's message every time the
# goal fires (see runtime_heartbeat_service.build_heartbeat_turn_request's
# "Goal:" section) -- this is where a human (or the model itself, via
# goal__update) writes "retry once, adjust the offer, escalate after 3
# attempts." Three existing mechanisms were considered and rejected before
# landing here:
#   - agent_memory.py: durable but workspace/agent-scoped free text meant
#     for standing facts and preferences, not a single goal's own
#     escalation rule -- every goal would need to invent its own naming
#     convention inside one shared memory file, and nothing would ever
#     prune it when the goal finished.
#   - the per-agent skills system (SKILL.md): SDK-engine only (does not
#     exist on the other engine this codebase still runs), and a skill is
#     a reusable CAPABILITY the agent chooses to invoke, not a specific
#     goal's own state -- wrong shape and wrong lifecycle entirely.
#   - sage_instruction_compiler_service.py: this is Sage's own (the
#     workspace MASTER agent) system-prompt compiler, with its own budget
#     and its own char-limit machinery -- docs/design/context-engineering-
#     plan.md item 10 notes the SPECIALIST branch (the one that actually
#     works project tasks/goals) has no compiler budget of its own at all
#     and never routes through this file. Wiring a goal's instruction
#     through Sage's compiler would mean either growing Sage's own prompt
#     with every workspace's every active goal, or building a second,
#     parallel per-specialist compiler -- a fourth concept, not reuse.
# What already exists and fits exactly: the wake-turn message assembly in
# runtime_heartbeat_service.build_heartbeat_turn_request, which ALREADY
# threads a persistent per-trigger instruction into a turn's message for
# task_assigned wakeups (its "Assigned task:" section, task_description
# verbatim) -- goals get the same treatment, a new "Goal:" section,
# reusing the identical mechanism rather than adding a new one.
GOAL_STATUS_ORDER = (
    # Lifted verbatim from project_tasks_service.TASK_STATUS_ORDER (copied,
    # not imported -- project_tasks_service already imports THIS module
    # lazily inside assign_task, so a module-level import back would be a
    # cycle; NON_TERMINAL_WAKE_STATUSES/task_commented's own trigger-kind
    # list above are copied the same way for the same reason). `backlog` is
    # deliberately not carried over -- a goal is created with explicit
    # intent to work it immediately, never triaged out of a queue the way
    # an untouched task can be.
    "todo",
    "in_progress",
    "awaiting_input",
    "blocked",
    "in_review",
    "done",
    # Goal-specific: the task vocabulary has no way to express either of
    # these two facts.
    "cancelled",   # deliberately abandoned before succeeding or exhausting
                    # the attempt/lifetime budget -- by the agent (it
                    # concluded the goal is unreachable) or the owner.
    "exhausted",   # the bounded attempt/lifetime ceiling was hit WITHOUT
                    # the model ever reporting success or giving up -- a
                    # system-recorded fact, never model narration. See
                    # _fire_goal below, the ONLY place this status is ever
                    # written.
)
VALID_GOAL_STATUSES = set(GOAL_STATUS_ORDER)
DEFAULT_GOAL_STATUS = "todo"
# Statuses that keep a goal alive -- the scan below only ever wakes a goal
# sitting in one of these. `blocked`/`awaiting_input` are included
# DELIBERATELY: "if the person says no, it would either try again, or offer
# something different" is exactly the blocked-then-retry loop this build
# exists for, so a blocked goal must keep waking its agent, never go quiet.
NON_TERMINAL_GOAL_STATUSES = {"todo", "in_progress", "awaiting_input", "blocked", "in_review"}
TERMINAL_GOAL_STATUSES = {"done", "cancelled", "exhausted"}
# What the agent-facing goal__update tool may set. `exhausted` is excluded
# on purpose -- it is the SYSTEM's bounded-give-up signal (_fire_goal
# alone writes it), kept structurally distinct from the model deciding to
# stop (which is `done`, having succeeded, or `cancelled`, having concluded
# the goal is unreachable -- both are honest, attributable outcomes the
# model chooses; `exhausted` is what happened when nobody decided anything
# and the ceiling did the deciding instead).
AGENT_SETTABLE_GOAL_STATUSES = NON_TERMINAL_GOAL_STATUSES | {"done", "cancelled"}


def max_goal_wakes_per_day() -> int:
    """A goal's own daily wake ceiling -- see DEFAULT_MAX_GOAL_WAKES_PER_DAY
    above. Env-overridable, floored at 1 for the same reason every other cap
    in this module is."""
    return max(1, config_int("EMPYRALIS_MAX_GOAL_WAKES_PER_DAY", DEFAULT_MAX_GOAL_WAKES_PER_DAY))


def _goal_retry_policy(goal: Dict[str, Any]) -> RetryPolicy:
    stored = _coerce_dict(goal.get("retry_policy"))
    max_attempts = _coerce_int(goal.get("max_attempts"), DEFAULT_GOAL_MAX_ATTEMPTS, minimum=1, maximum=GOAL_HARD_MAX_ATTEMPTS)
    return RetryPolicy(
        max_retries=max_attempts,
        base_delay_seconds=_coerce_int(
            stored.get("base_delay_seconds"), DEFAULT_GOAL_RETRY_BASE_DELAY_SECONDS, minimum=1, maximum=86400,
        ),
        max_delay_seconds=_coerce_int(
            stored.get("max_delay_seconds"), DEFAULT_GOAL_RETRY_MAX_DELAY_SECONDS, minimum=1, maximum=604800,
        ),
        backoff_multiplier=float(stored.get("backoff_multiplier") or DEFAULT_GOAL_RETRY_BACKOFF_MULTIPLIER),
    )


def _clamp_goal_bounds(
    *,
    now_utc: datetime,
    max_attempts: Optional[int],
    lifetime_days: Optional[int],
) -> tuple[int, datetime]:
    """Build step 3 (bounded by construction): unlike recurring schedules'
    nullable max_occurrences/expires_at pair, a goal ALWAYS gets a real
    max_attempts and a real expires_at -- no unbounded branch exists to
    reach. Whatever the caller supplies (or omits, landing on the default)
    is clamped to the hard ceilings so "give me 500 attempts over a year"
    degrades to the ceiling rather than either erroring or actually running
    that long unattended."""
    resolved_max_attempts = _coerce_int(
        max_attempts, DEFAULT_GOAL_MAX_ATTEMPTS, minimum=1, maximum=GOAL_HARD_MAX_ATTEMPTS,
    )
    resolved_lifetime_days = _coerce_int(
        lifetime_days, DEFAULT_GOAL_LIFETIME_DAYS, minimum=1, maximum=GOAL_HARD_MAX_LIFETIME_DAYS,
    )
    resolved_expires_at = now_utc + timedelta(days=resolved_lifetime_days)
    return resolved_max_attempts, resolved_expires_at


def goal_view(row: Dict[str, Any]) -> Dict[str, Any]:
    """Honest-reporting read shape (build step 6): every field here is real,
    system-recorded data -- attempt_count is advanced ONLY by _fire_goal,
    status is either an agent's own explicit tool call or _fire_goal's own
    exhaustion transition, last_outcome_reason is stamped by the system at
    the moment a goal turns terminal. Nothing here is the model's own
    narration of its progress."""
    metadata = _coerce_dict(row.get("metadata"))
    retry_policy = _coerce_dict(row.get("retry_policy"))
    next_fire_at = row.get("next_fire_at")
    last_fired_at = row.get("last_fired_at")
    expires_at = row.get("expires_at")
    created_at = row.get("created_at")
    status = str(row.get("status") or DEFAULT_GOAL_STATUS).strip().lower()
    return {
        "id": str(row.get("id") or ""),
        "project_id": str(row.get("project_id") or ""),
        "agent_id": str(row.get("agent_id") or ""),
        "title": str(row.get("title") or "").strip(),
        "goal": str(row.get("goal_text") or "").strip(),
        "instruction": str(row.get("instruction") or "").strip(),
        "status": status,
        "resolved": status in TERMINAL_GOAL_STATUSES,
        "requested_by": str(row.get("requested_by") or "owner").strip().lower(),
        "attempt_count": int(row.get("attempt_count") or 0),
        "max_attempts": int(row.get("max_attempts") or DEFAULT_GOAL_MAX_ATTEMPTS),
        "retry_policy": retry_policy,
        "next_fire_at": (
            next_fire_at.isoformat() if hasattr(next_fire_at, "isoformat") else (str(next_fire_at) if next_fire_at else None)
        ) if status in NON_TERMINAL_GOAL_STATUSES else None,
        "last_fired_at": last_fired_at.isoformat() if hasattr(last_fired_at, "isoformat") else (str(last_fired_at) if last_fired_at else None),
        "expires_at": expires_at.isoformat() if hasattr(expires_at, "isoformat") else str(expires_at or ""),
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at or ""),
        # Why it stopped (build step 3/6) -- present only once the goal is
        # terminal; None on a live goal, never a guess.
        "last_outcome_reason": metadata.get("last_outcome_reason"),
        "last_skip_reason": metadata.get("last_skip_reason"),
    }


async def create_goal(
    *,
    tenant_id: str,
    workspace_id: str,
    project_id: str,
    agent_id: str,
    goal_text: str,
    title: str = "",
    instruction: str = "",
    authority_tier: Optional[str] = None,
    requested_by: str = "owner",
    max_attempts: Optional[int] = None,
    lifetime_days: Optional[int] = None,
) -> Dict[str, Any]:
    """Create a goal for *agent_id* inside *project_id* and fire its FIRST
    attempt immediately -- same reasoning as schedule_task_assigned_
    wakeup's own skip_quiet_hours=True: creating a goal is itself an
    explicit action (an owner saying "go do this now", or an agent deciding
    to pursue one), not an ambient trigger, so it must not sit deferred by a
    policy meant to protect a sleeping device from a trigger nobody asked
    for right now. Every RETRY after this first attempt is ambient (see
    _fire_goal below) and does respect quiet hours, exactly like a
    recurring schedule's own fires do.

    FAILS LOUD (SchedulerPolicyError) on a missing agent_id/project_id/
    goal_text -- never silently drops the request."""
    resolved_agent_id = str(agent_id or "").strip()
    resolved_project_id = str(project_id or "").strip()
    resolved_goal_text = str(goal_text or "").strip()
    if not resolved_agent_id:
        raise SchedulerPolicyError("agent_id is required to create a goal.")
    if not resolved_project_id:
        raise SchedulerPolicyError("project_id is required to create a goal.")
    if not resolved_goal_text:
        raise SchedulerPolicyError("goal_text is required — what outcome should the agent work toward?")
    workspace, master_install, policy = await _load_scheduler_scope(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    now_utc = _utc_now()
    resolved_max_attempts, resolved_expires_at = _clamp_goal_bounds(
        now_utc=now_utc, max_attempts=max_attempts, lifetime_days=lifetime_days,
    )
    resolved_instruction = str(instruction or "").strip() or DEFAULT_GOAL_INSTRUCTION
    retry_policy = RetryPolicy(
        max_retries=resolved_max_attempts,
        base_delay_seconds=DEFAULT_GOAL_RETRY_BASE_DELAY_SECONDS,
        max_delay_seconds=DEFAULT_GOAL_RETRY_MAX_DELAY_SECONDS,
        backoff_multiplier=DEFAULT_GOAL_RETRY_BACKOFF_MULTIPLIER,
    )
    # The first attempt is used immediately (fired below), so next_fire_at
    # already reflects the delay before attempt #2 -- mirrors _fire_goal's
    # own post-fire bookkeeping so creation and every subsequent fire follow
    # the exact same arithmetic.
    next_fire_at = now_utc + timedelta(seconds=compute_retry_delay(1, retry_policy))
    resolved_tier = authority_mandate_service.inherit_tier(authority_tier)
    resolved_title = str(title or "").strip() or resolved_goal_text[:200]
    record = await control_plane_repository.append_agent_goal(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        project_id=resolved_project_id,
        agent_id=resolved_agent_id,
        master_agent_install_id=str(_coerce_dict(master_install).get("id") or "").strip() or None,
        title=resolved_title,
        goal_text=resolved_goal_text,
        instruction=resolved_instruction,
        status="in_progress",
        requested_by=requested_by,
        attempt_count=1,
        max_attempts=resolved_max_attempts,
        retry_policy=retry_policy.as_dict(),
        next_fire_at=next_fire_at,
        last_fired_at=now_utc,
        expires_at=resolved_expires_at,
        metadata={},
    )
    if not isinstance(record, dict):
        raise SchedulerPolicyError("Failed to persist goal.")
    goal_id = str(record.get("id") or "")
    due_at, due_reason = _apply_policy_to_due_at(
        due_at=now_utc,
        policy=policy,
        device_state=_device_state({}, workspace, master_install),
        # MAN-294 reasoning, applied here: creating a goal is an explicit
        # action taken right now, not an ambient trigger.
        skip_quiet_hours=True,
    )
    wake_metadata: Dict[str, Any] = {
        "agent_id": resolved_agent_id,
        "project_id": resolved_project_id,
        "goal_id": goal_id,
        "authority_tier": resolved_tier,
    }
    if due_reason:
        wake_metadata["policy_delay_reason"] = due_reason
    wake_record = await _persist_wakeup(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        master_install=master_install,
        trigger_kind="goal",
        source="agent_goal",
        requested_by=str(requested_by or "owner").strip().lower() or "owner",
        reason="goal_created",
        summary=f"Goal: {resolved_title}",
        payload={
            "goal_id": goal_id,
            "goal_text": resolved_goal_text,
            "instruction": resolved_instruction,
            "agent_id": resolved_agent_id,
            "project_id": resolved_project_id,
            "attempt_number": 1,
            "max_attempts": resolved_max_attempts,
            "status": "in_progress",
            "authority_tier": resolved_tier,
        },
        policy=policy,
        due_at=due_at,
        approval_required=False,
        status="pending",
        denial_reason=None,
        metadata=wake_metadata,
        attributed_agent_install_id=resolved_agent_id,
    )
    await control_plane_repository.update_agent_goal(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        goal_id=goal_id,
        metadata_patch={"last_wake_request_id": str(_coerce_dict(wake_record).get("id") or "") or None},
    )
    if due_at <= now_utc + timedelta(seconds=IMMEDIATE_TRIGGER_WINDOW_SECONDS):
        _trigger_ambient_monitor(workspace_id)
    try:
        from server_modules import activity_ledger_service

        actor_type, actor_id, install_id = _resolve_delegation_actor(
            master_install=master_install,
            attributed_agent_install_id=resolved_agent_id,
        )
        await activity_ledger_service.append_activity_event(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_type=actor_type,
            actor_id=actor_id,
            install_id=install_id,
            event_class="delegation",
            detail_level="timeline_detail",
            action="goal_created",
            title="Goal created",
            summary=resolved_title,
            status="in_progress",
            metadata={"goal_id": goal_id, "agent_id": resolved_agent_id, "project_id": resolved_project_id},
        )
    except Exception:
        pass
    reloaded = await control_plane_repository.get_agent_goal(
        tenant_id=tenant_id, workspace_id=workspace_id, goal_id=goal_id,
    )
    return reloaded or record


async def list_goals(
    *,
    tenant_id: str,
    workspace_id: str,
    project_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    status: Optional[str] = None,
    include_terminal: bool = True,
) -> List[Dict[str, Any]]:
    if status:
        rows = await control_plane_repository.list_agent_goals(
            tenant_id=tenant_id, workspace_id=workspace_id, project_id=project_id, agent_id=agent_id, status=status,
        )
        return rows
    if include_terminal:
        return await control_plane_repository.list_agent_goals(
            tenant_id=tenant_id, workspace_id=workspace_id, project_id=project_id, agent_id=agent_id,
        )
    return await control_plane_repository.list_agent_goals(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        project_id=project_id,
        agent_id=agent_id,
        statuses=list(NON_TERMINAL_GOAL_STATUSES),
    )


async def get_goal(
    *,
    tenant_id: str,
    workspace_id: str,
    goal_id: str,
) -> Optional[Dict[str, Any]]:
    return await control_plane_repository.get_agent_goal(
        tenant_id=tenant_id, workspace_id=workspace_id, goal_id=goal_id,
    )


async def update_goal(
    *,
    tenant_id: str,
    workspace_id: str,
    goal_id: str,
    status: Optional[str] = None,
    title: Optional[str] = None,
    goal_text: Optional[str] = None,
    instruction: Optional[str] = None,
    note: str = "",
    actor: str = "agent",
) -> Dict[str, Any]:
    """The validated, agent-facing update path (goal__update). Rejects a
    status outside AGENT_SETTABLE_GOAL_STATUSES loudly rather than silently
    coercing it -- in particular 'exhausted' can never be set through this
    function, matching that status's own "system fact, not a choice"
    contract (see AGENT_SETTABLE_GOAL_STATUSES' docstring above).
    control_plane_repository.update_agent_goal itself performs no such
    validation -- it is the generic field-patch primitive _fire_goal also
    uses to write 'exhausted', so the validation has to live at THIS layer,
    not the repository's."""
    existing = await control_plane_repository.get_agent_goal(
        tenant_id=tenant_id, workspace_id=workspace_id, goal_id=goal_id,
    )
    if existing is None:
        return {"ok": False, "error": f"Goal {goal_id} not found."}
    current_status = str(existing.get("status") or DEFAULT_GOAL_STATUS).strip().lower()
    if current_status in TERMINAL_GOAL_STATUSES:
        return {"ok": False, "error": f"Can't update a goal that's already {current_status}."}
    resolved_status = None
    if status is not None:
        candidate = str(status or "").strip().lower()
        if candidate not in AGENT_SETTABLE_GOAL_STATUSES:
            return {
                "ok": False,
                "error": (
                    f"Invalid goal status '{status}'. Must be one of "
                    f"{sorted(AGENT_SETTABLE_GOAL_STATUSES)}."
                ),
            }
        resolved_status = candidate
    metadata_patch: Dict[str, Any] = {}
    if resolved_status in {"done", "cancelled"}:
        metadata_patch["last_outcome_reason"] = f"{resolved_status}_by_{actor}"
    if note:
        metadata_patch["last_note"] = str(note or "").strip()[:2000]
        metadata_patch["last_note_by"] = actor
        metadata_patch["last_note_at"] = _utc_now().isoformat().replace("+00:00", "Z")
    row = await control_plane_repository.update_agent_goal(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        goal_id=goal_id,
        status=resolved_status,
        title=title,
        goal_text=goal_text,
        instruction=instruction,
        metadata_patch=metadata_patch or None,
    )
    if row is None:
        return {"ok": False, "error": f"Could not update goal {goal_id}."}
    return {"ok": True, "goal": goal_view(row)}


async def _fire_goal(goal: Dict[str, Any]) -> Dict[str, Any]:
    """Process one due goal: either propose exactly one ordinary wake
    request through the standard _persist_wakeup gate, or stop the goal for
    good (expired/attempts exhausted), or skip this occurrence (daily cap),
    then advance next_fire_at. A single misbehaving goal must never take
    down the scan tick -- the caller (process_due_goals_once) wraps this
    per-goal, same posture as _fire_recurring_schedule's own caller.

    Bounding, mirrored from _fire_recurring_schedule's own shape:
      1. expires_at reached, OR attempt_count already at max_attempts ->
         status flips to 'exhausted' (the ONLY place this status is ever
         written) and the goal never fires again. Checked BEFORE firing, so
         the Nth attempt (attempt_count going N-1 -> N) is the last real
         wake; the tick after that finds attempt_count >= max_attempts and
         stops without ever firing an (N+1)th time.
      2. the per-goal daily wake cap (max_goal_wakes_per_day) -- skip this
         occurrence, recompute next_fire_at, do NOT advance attempt_count
         (a skipped occurrence used no attempt budget).
      3. otherwise: persist one wake request (skip_quiet_hours=False -- a
         retry fire is ambient, not a live action taken right now, same
         reasoning _fire_recurring_schedule already documents), advance
         attempt_count, compute the NEXT next_fire_at via the goal's own
         backoff policy, and flip status 'todo' -> 'in_progress' on first
         real use (mirrors assign_task's own unstarted -> in_progress
         flip) without touching any other in-flight status (blocked/
         awaiting_input/in_review all keep waking as themselves -- the
         model, not this function, decides when those change).
    """
    tenant_id = str(goal.get("tenant_id") or "").strip()
    workspace_id = str(goal.get("workspace_id") or "").strip()
    goal_id = str(goal.get("id") or "").strip()
    project_id = str(goal.get("project_id") or "").strip()
    agent_id = str(goal.get("agent_id") or "").strip()
    goal_text = str(goal.get("goal_text") or "").strip()
    instruction = str(goal.get("instruction") or "").strip()
    title = str(goal.get("title") or "").strip() or goal_text[:200]
    current_status = str(goal.get("status") or DEFAULT_GOAL_STATUS).strip().lower()
    now_utc = _utc_now()

    if current_status not in NON_TERMINAL_GOAL_STATUSES:
        # Defensive only -- process_due_goals_once already filters to
        # non-terminal statuses, but a race (the model itself resolved the
        # goal between the scan query and this call) is possible.
        return {"goal_id": goal_id, "action": "skipped_terminal"}

    workspace, master_install, policy = await _load_scheduler_scope(
        tenant_id=tenant_id, workspace_id=workspace_id,
    )

    expires_at = _parse_datetime(goal.get("expires_at"))
    max_attempts = int(goal.get("max_attempts") or DEFAULT_GOAL_MAX_ATTEMPTS)
    attempt_count = int(goal.get("attempt_count") or 0)
    if (expires_at is not None and now_utc >= expires_at) or attempt_count >= max_attempts:
        outcome_reason = "lifetime_expired" if (expires_at is not None and now_utc >= expires_at) else "max_attempts_reached"
        await control_plane_repository.update_agent_goal(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            goal_id=goal_id,
            status="exhausted",
            metadata_patch={"last_outcome_reason": outcome_reason, "exhausted_at": now_utc.isoformat().replace("+00:00", "Z")},
        )
        try:
            from server_modules import activity_ledger_service

            await activity_ledger_service.append_activity_event(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                actor_type="system",
                actor_id="scheduler",
                install_id=None,
                event_class="delegation",
                detail_level="timeline_detail",
                action="goal_exhausted",
                title="Goal exhausted",
                summary=f"{title} — {outcome_reason} after {attempt_count} attempt(s)",
                status="exhausted",
                metadata={"goal_id": goal_id, "agent_id": agent_id, "project_id": project_id, "reason": outcome_reason},
            )
        except Exception:
            pass
        return {"goal_id": goal_id, "action": "exhausted", "reason": outcome_reason}

    recent_count = await control_plane_repository.count_agent_scheduler_wake_requests_since(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        since=now_utc - timedelta(hours=24),
        goal_id=goal_id,
    )
    daily_cap = max_goal_wakes_per_day()
    retry_policy = _goal_retry_policy(goal)
    if recent_count >= daily_cap:
        skip_next_fire_at = now_utc + timedelta(hours=1)
        await control_plane_repository.update_agent_goal(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            goal_id=goal_id,
            next_fire_at=skip_next_fire_at,
            metadata_patch={
                "last_skip_reason": "goal_daily_wake_cap",
                "last_skip_at": now_utc.isoformat().replace("+00:00", "Z"),
            },
        )
        return {"goal_id": goal_id, "action": "skipped_daily_cap"}

    due_at, due_reason = _apply_policy_to_due_at(
        due_at=now_utc,
        policy=policy,
        device_state=_device_state({}, workspace, master_install),
        # A retry fire is ambient, not a live human/agent action taken
        # right now -- unlike create_goal's own first fire, quiet hours are
        # NOT skipped here. Same reasoning as _fire_recurring_schedule.
        skip_quiet_hours=False,
    )
    new_attempt_count = attempt_count + 1
    wake_metadata: Dict[str, Any] = {
        "agent_id": agent_id,
        "project_id": project_id,
        "goal_id": goal_id,
        "authority_tier": authority_mandate_service.normalize_tier(_coerce_dict(goal.get("metadata")).get("authority_tier")),
    }
    if due_reason:
        wake_metadata["policy_delay_reason"] = due_reason
    record = await _persist_wakeup(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        master_install=master_install,
        trigger_kind="goal",
        source="agent_goal",
        requested_by=str(goal.get("requested_by") or "owner").strip().lower() or "owner",
        reason="goal_retry",
        summary=f"Goal: {title} (attempt {new_attempt_count}/{max_attempts})",
        payload={
            "goal_id": goal_id,
            "goal_text": goal_text,
            "instruction": instruction,
            "agent_id": agent_id,
            "project_id": project_id,
            "attempt_number": new_attempt_count,
            "max_attempts": max_attempts,
            "status": current_status,
            "authority_tier": wake_metadata["authority_tier"],
        },
        policy=policy,
        due_at=due_at,
        approval_required=False,
        status="pending",
        denial_reason=None,
        metadata=wake_metadata,
        attributed_agent_install_id=agent_id,
    )
    if due_at <= now_utc + timedelta(seconds=IMMEDIATE_TRIGGER_WINDOW_SECONDS):
        _trigger_ambient_monitor(workspace_id)
    next_fire_at = now_utc + timedelta(seconds=compute_retry_delay(new_attempt_count, retry_policy))
    next_status = "in_progress" if current_status == "todo" else current_status
    await control_plane_repository.update_agent_goal(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        goal_id=goal_id,
        status=next_status,
        next_fire_at=next_fire_at,
        last_fired_at=now_utc,
        attempt_count=new_attempt_count,
        metadata_patch={"last_skip_reason": None, "last_wake_request_id": str(_coerce_dict(record).get("id") or "") or None},
    )
    return {"goal_id": goal_id, "action": "fired", "wake_request_id": str(_coerce_dict(record).get("id") or "")}


async def process_due_goals_once(
    *,
    limit: int = DEFAULT_RECURRING_SCHEDULE_SCAN_LIMIT,
) -> Dict[str, Any]:
    """Cross-workspace tick for due goals -- structurally process_due_
    recurring_schedules_once's twin: a system-level bypass_rls scan finds
    which (tenant_id, workspace_id) scopes have due work, then this reloads
    each due goal through the normal RLS-scoped path and fires it. Called
    from the SAME daemon tick as the wake-request scan and the recurring-
    schedule scan (see scan_due_wake_requests_once), not a second thread."""
    now_utc = _utc_now()
    non_terminal = list(NON_TERMINAL_GOAL_STATUSES)
    scopes = await control_plane_repository.list_due_agent_goal_scopes(
        due_before=now_utc, non_terminal_statuses=non_terminal, limit=limit,
    )
    results: List[Dict[str, Any]] = []
    for scope in scopes:
        tenant_id = str(scope.get("tenant_id") or "").strip()
        workspace_id = str(scope.get("workspace_id") or "").strip()
        if not tenant_id or not workspace_id:
            continue
        due_goals = await control_plane_repository.list_agent_goals(
            tenant_id=tenant_id, workspace_id=workspace_id, statuses=non_terminal,
        )
        for goal in due_goals:
            next_fire_at = _parse_datetime(goal.get("next_fire_at"))
            if next_fire_at is None or next_fire_at > now_utc:
                continue
            try:
                outcome = await _fire_goal(goal)
            except Exception:
                LOGGER.exception(
                    "goal-scan: fire failed for tenant=%s workspace=%s goal=%s",
                    tenant_id, workspace_id, goal.get("id"),
                )
                outcome = {"goal_id": str(goal.get("id") or ""), "action": "error"}
            results.append({"tenant_id": tenant_id, "workspace_id": workspace_id, "result": outcome})
    return {"scanned": len(results), "results": results}


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

    Also processes due RECURRING schedules and due GOALS first, on this
    exact same tick -- no second (or third) daemon thread (see process_due_
    recurring_schedules_once's and process_due_goals_once's own
    docstrings). Firing either only ever creates one ordinary agent_
    scheduler_wake_requests row; running these steps before the
    wake-request scope scan just below means a schedule or goal that fires
    THIS tick is picked up by THIS tick's wake-request scan too, not left
    to wait a full poll interval. A failure in either must never block the
    wake-request scan that already existed, or each other -- each is
    independently caught and logged, never re-raised.
    """
    try:
        await process_due_recurring_schedules_once()
    except Exception:
        LOGGER.exception("recurring-schedule-scan: tick failed")
    try:
        await process_due_goals_once()
    except Exception:
        LOGGER.exception("goal-scan: tick failed")
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
    # 2026-08-13: task_assigned rows now always stamp authority_tier (see
    # schedule_task_assigned_wakeup's own docstring) and so never reach this
    # line going forward. A task_assigned row that DOES land here is a
    # legacy row persisted before that fix -- correctly unattributed, since
    # this function genuinely cannot recover which tier it should have had.
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
    recurring = await list_recurring_schedules(tenant_id=tenant_id, workspace_id=workspace_id)
    return {
        "policy": policy.as_dict(),
        "ambient_monitor": ambient_monitor_status(workspace_id),
        "exact_jobs": {
            "count": len(items),
            "items": items[: min(8, len(items))],
        },
        "recurring_schedules": {
            "count": len(recurring),
            "items": [recurring_schedule_view(row) for row in recurring[: min(8, len(recurring))]],
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
