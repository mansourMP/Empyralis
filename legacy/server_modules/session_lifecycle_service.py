from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from server_modules import rust_runtime_kernel_client, session_service, workspace_context


DEFAULT_MAX_TURNS_PER_SESSION = 100
DEFAULT_IDLE_TIMEOUT_HOURS = 24
DEFAULT_PRUNE_AFTER_IDLE_DAYS = 7
DEFAULT_SHORT_RETENTION_DAYS = 30
DEFAULT_STANDARD_RETENTION_DAYS = 365
DEFAULT_EXTENDED_RETENTION_DAYS = 730


class SessionLifecycleError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SessionLifecyclePolicy:
    max_turns_per_session: int = DEFAULT_MAX_TURNS_PER_SESSION
    idle_timeout_hours: int = DEFAULT_IDLE_TIMEOUT_HOURS
    prune_after_idle_days: int = DEFAULT_PRUNE_AFTER_IDLE_DAYS
    retention_days: int = DEFAULT_STANDARD_RETENTION_DAYS
    auto_reset_enabled: bool = True
    auto_reset_after_turns: int = 0
    auto_reset_after_hours: int = 24
    enforce_max_turns: bool = True
    idle_pruning_enabled: bool = True

    @property
    def idle_timeout(self) -> timedelta:
        return timedelta(hours=max(1, self.idle_timeout_hours))

    @property
    def prune_age(self) -> timedelta:
        return timedelta(days=max(1, self.prune_after_idle_days))

    @property
    def retention_period(self) -> timedelta:
        return timedelta(days=max(1, self.retention_days))

    def as_dict(self) -> Dict[str, Any]:
        return {
            "max_turns_per_session": self.max_turns_per_session,
            "idle_timeout_hours": self.idle_timeout_hours,
            "prune_after_idle_days": self.prune_after_idle_days,
            "retention_days": self.retention_days,
            "auto_reset_enabled": self.auto_reset_enabled,
            "auto_reset_after_turns": self.auto_reset_after_turns,
            "auto_reset_after_hours": self.auto_reset_after_hours,
            "enforce_max_turns": self.enforce_max_turns,
            "idle_pruning_enabled": self.idle_pruning_enabled,
        }


_POLICY_PRESETS: dict[str, SessionLifecyclePolicy] = {
    "short": SessionLifecyclePolicy(
        retention_days=DEFAULT_SHORT_RETENTION_DAYS,
        max_turns_per_session=50,
        idle_timeout_hours=12,
        prune_after_idle_days=3,
    ),
    "standard": SessionLifecyclePolicy(),
    "extended": SessionLifecyclePolicy(
        retention_days=DEFAULT_EXTENDED_RETENTION_DAYS,
        max_turns_per_session=500,
        idle_timeout_hours=72,
        prune_after_idle_days=30,
    ),
    "unlimited": SessionLifecyclePolicy(
        retention_days=DEFAULT_EXTENDED_RETENTION_DAYS,
        max_turns_per_session=0,
        enforce_max_turns=False,
        idle_pruning_enabled=False,
        auto_reset_enabled=False,
    ),
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_int(value: Any, default: int, *, minimum: int = 0, maximum: int = 2_147_483_647) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(parsed, maximum))


def _parse_session_datetime(value: Any) -> Optional[datetime]:
    token = str(value or "").strip()
    if not token:
        return None
    try:
        parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _session_age_hours(session_record: Dict[str, Any]) -> int:
    created_at = _parse_session_datetime(session_record.get("created_at"))
    if created_at is None:
        return 2_147_483_647
    return max(0, int((_utc_now() - created_at).total_seconds() // 3600))


def _session_idle_seconds(session_record: Dict[str, Any], policy: SessionLifecyclePolicy) -> int:
    expires_at = _parse_session_datetime(session_record.get("expires_at"))
    if expires_at is None:
        return int(policy.idle_timeout.total_seconds())
    last_activity = expires_at - timedelta(seconds=86400)
    return max(0, int((_utc_now() - last_activity).total_seconds()))


def _lifecycle_preset_for_policy(policy: SessionLifecyclePolicy) -> str:
    if not policy.enforce_max_turns and not policy.idle_pruning_enabled:
        return "unlimited"
    if policy.max_turns_per_session <= 50 and policy.idle_timeout_hours <= 12:
        return "short"
    if policy.max_turns_per_session >= 500 or policy.idle_timeout_hours >= 72:
        return "extended"
    return "standard"


def _session_lifecycle_request(
    operation: str,
    *,
    session_record: Dict[str, Any],
    turn_count: int,
    policy: SessionLifecyclePolicy,
    force: bool = False,
) -> Dict[str, Any]:
    max_turns = (
        max(1, int(policy.max_turns_per_session))
        if policy.enforce_max_turns and policy.max_turns_per_session > 0
        else None
    )
    request = {
        "operation": operation,
        "session_id": str(session_record.get("session_id") or "").strip(),
        "status": str(session_record.get("status") or "active").strip() or "active",
        "preset": _lifecycle_preset_for_policy(policy),
        "turn_count": max(0, int(turn_count or 0)),
        "age_hours": _session_age_hours(session_record),
        "idle_seconds": _session_idle_seconds(session_record, policy),
        "max_age_hours": max(1, int(policy.prune_after_idle_days * 24)),
        "idle_timeout_seconds": max(1, int(policy.idle_timeout.total_seconds())),
        "force": bool(force),
    }
    if max_turns is not None:
        request["max_turns"] = max_turns
    return request


def _session_lifecycle_decision(
    operation: str,
    *,
    session_record: Dict[str, Any],
    turn_count: int,
    policy: SessionLifecyclePolicy,
    force: bool = False,
) -> Dict[str, Any]:
    decision = rust_runtime_kernel_client.session_lifecycle_decision(
        **_session_lifecycle_request(
            operation,
            session_record=session_record,
            turn_count=turn_count,
            policy=policy,
            force=force,
        )
    )
    expected_next_actions = {
        "turn": {"continue"},
        "prune": {"prune"},
    }
    expected = expected_next_actions.get(operation)
    if expected and str(decision.get("decision") or "").strip() == "allow":
        next_action = str(decision.get("next_action") or "").strip()
        if next_action not in expected:
            raise RuntimeError(f"unexpected_next_action:{next_action or 'missing'}")
    return decision


def _enforce_session_lifecycle_decision(
    operation: str,
    *,
    session_record: Dict[str, Any],
    turn_count: int,
    policy: SessionLifecyclePolicy,
    force: bool = False,
) -> Dict[str, Any]:
    decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
        "session-lifecycle-decision",
        _session_lifecycle_request(
            operation,
            session_record=session_record,
            turn_count=turn_count,
            policy=policy,
            force=force,
        ),
    )
    expected_next_actions = {
        "prune": {"prune"},
    }
    expected = expected_next_actions.get(operation)
    if expected:
        next_action = str(decision.get("next_action") or "").strip()
        if next_action not in expected:
            raise RuntimeError(f"unexpected_next_action:{next_action or 'missing'}")
    return decision


def resolve_lifecycle_policy(
    workspace_id: str | None = None,
    *,
    preset: str = "standard",
    overrides: Dict[str, Any] | None = None,
) -> SessionLifecyclePolicy:
    base = _POLICY_PRESETS.get(preset.strip().lower(), _POLICY_PRESETS["standard"])
    if overrides:
        return SessionLifecyclePolicy(
            max_turns_per_session=_coerce_int(
                overrides.get("max_turns_per_session", base.max_turns_per_session),
                base.max_turns_per_session,
                minimum=0,
            ),
            idle_timeout_hours=_coerce_int(
                overrides.get("idle_timeout_hours", base.idle_timeout_hours),
                base.idle_timeout_hours,
                minimum=1,
            ),
            prune_after_idle_days=_coerce_int(
                overrides.get("prune_after_idle_days", base.prune_after_idle_days),
                base.prune_after_idle_days,
                minimum=1,
            ),
            retention_days=_coerce_int(
                overrides.get("retention_days", base.retention_days),
                base.retention_days,
                minimum=1,
            ),
            auto_reset_enabled=bool(
                overrides.get("auto_reset_enabled", base.auto_reset_enabled)
            ),
            auto_reset_after_turns=_coerce_int(
                overrides.get("auto_reset_after_turns", base.auto_reset_after_turns),
                base.auto_reset_after_turns,
                minimum=0,
            ),
            auto_reset_after_hours=_coerce_int(
                overrides.get("auto_reset_after_hours", base.auto_reset_after_hours),
                base.auto_reset_after_hours,
                minimum=1,
            ),
            enforce_max_turns=bool(
                overrides.get("enforce_max_turns", base.enforce_max_turns)
            ),
            idle_pruning_enabled=bool(
                overrides.get("idle_pruning_enabled", base.idle_pruning_enabled)
            ),
        )
    return base


def should_auto_reset(
    *,
    session_record: Dict[str, Any],
    turn_count: int,
    policy: SessionLifecyclePolicy,
) -> bool:
    if not policy.auto_reset_enabled:
        return False
    if policy.auto_reset_after_turns > 0 and turn_count >= policy.auto_reset_after_turns:
        return True
    if policy.auto_reset_after_hours > 0:
        created_at_raw = session_record.get("created_at")
        if created_at_raw:
            try:
                created_at = datetime.fromisoformat(
                    str(created_at_raw).replace("Z", "+00:00")
                )
                age = _utc_now() - created_at
                if age >= timedelta(hours=policy.auto_reset_after_hours):
                    return True
            except (ValueError, TypeError):
                pass
    return False


def is_session_idle(
    session_record: Dict[str, Any],
    idle_timeout: timedelta,
) -> bool:
    expires_at_raw = session_record.get("expires_at")
    if not expires_at_raw:
        return True
    try:
        expires_at = datetime.fromisoformat(
            str(expires_at_raw).replace("Z", "+00:00")
        )
    except (ValueError, TypeError):
        return True
    last_activity = expires_at - timedelta(seconds=86400)
    return (_utc_now() - last_activity) >= idle_timeout


def is_session_expired(session_record: Dict[str, Any]) -> bool:
    expires_at_raw = session_record.get("expires_at")
    if not expires_at_raw:
        return True
    try:
        expires_at = datetime.fromisoformat(
            str(expires_at_raw).replace("Z", "+00:00")
        )
    except (ValueError, TypeError):
        return True
    return _utc_now() >= expires_at


def is_session_eligible_for_pruning(
    session_record: Dict[str, Any],
    policy: SessionLifecyclePolicy,
) -> bool:
    if not policy.idle_pruning_enabled:
        return False
    created_at_raw = session_record.get("created_at")
    if not created_at_raw:
        return True
    try:
        created_at = datetime.fromisoformat(
            str(created_at_raw).replace("Z", "+00:00")
        )
    except (ValueError, TypeError):
        return True
    age = _utc_now() - created_at
    if age >= policy.prune_age:
        return True
    return is_session_idle(session_record, policy.idle_timeout)


def check_max_turns(
    turn_count: int,
    policy: SessionLifecyclePolicy,
) -> bool:
    if not policy.enforce_max_turns or policy.max_turns_per_session <= 0:
        return True
    decision = rust_runtime_kernel_client.session_lifecycle_decision(
        operation="turn",
        session_id="turn-check",
        status="active",
        preset=_lifecycle_preset_for_policy(policy),
        turn_count=max(0, int(turn_count or 0)),
        max_turns=max(1, int(policy.max_turns_per_session)),
        max_age_hours=max(1, int(policy.prune_after_idle_days * 24)),
        idle_timeout_seconds=max(1, int(policy.idle_timeout.total_seconds())),
    )
    if str(decision.get("decision") or "").strip() != "allow":
        return False
    next_action = str(decision.get("next_action") or "").strip()
    if next_action != "continue":
        return False
    return True


def max_turns_reached_message(policy: SessionLifecyclePolicy) -> str:
    return (
        f"This session has reached the maximum of {policy.max_turns_per_session} turns. "
        "Start a new session to continue. Previous context has been preserved."
    )


def lifecycle_status(
    *,
    session_record: Dict[str, Any],
    turn_count: int,
    policy: SessionLifecyclePolicy,
) -> Dict[str, Any]:
    return {
        "session_id": session_record.get("session_id", ""),
        "turn_count": turn_count,
        "max_turns": policy.max_turns_per_session if policy.enforce_max_turns else 0,
        "turns_remaining": (
            max(0, policy.max_turns_per_session - turn_count)
            if policy.enforce_max_turns and policy.max_turns_per_session > 0
            else None
        ),
        "auto_reset_eligible": should_auto_reset(
            session_record=session_record,
            turn_count=turn_count,
            policy=policy,
        ),
        "idle": is_session_idle(session_record, policy.idle_timeout),
        "expired": is_session_expired(session_record),
        "prune_eligible": is_session_eligible_for_pruning(session_record, policy),
        "policy": policy.as_dict(),
    }


async def list_prune_candidates(
    workspace_id: str,
    policy: SessionLifecyclePolicy | None = None,
) -> List[Dict[str, Any]]:
    if policy is None:
        policy = resolve_lifecycle_policy(workspace_id)
    try:
        sessions = await session_service.list_workspace_runtime_sessions(workspace_id)
    except Exception:
        return []
    candidates: List[Dict[str, Any]] = []
    for session in sessions:
        decision = _session_lifecycle_decision(
            "prune",
            session_record=session,
            turn_count=0,
            policy=policy,
        )
        if str(decision.get("decision") or "").strip() == "allow":
            candidates.append(session)
    return candidates


async def prune_idle_sessions(
    workspace_id: str,
    policy: SessionLifecyclePolicy | None = None,
    *,
    dry_run: bool = False,
) -> Dict[str, Any]:
    if policy is None:
        policy = resolve_lifecycle_policy(workspace_id)
    candidates = await list_prune_candidates(workspace_id, policy)
    pruned: List[str] = []
    for session in candidates:
        session_id = str(session.get("session_id") or "")
        if not session_id:
            continue
        if not dry_run:
            try:
                _enforce_session_lifecycle_decision(
                    "prune",
                    session_record=session,
                    turn_count=0,
                    policy=policy,
                )
                await session_service.terminate_session(session_id)
            except Exception:
                continue
        pruned.append(session_id)
    return {
        "workspace_id": workspace_id,
        "dry_run": dry_run,
        "candidates_found": len(candidates),
        "pruned_count": len(pruned),
        "pruned_session_ids": pruned,
    }
