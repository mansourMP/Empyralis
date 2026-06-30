from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


ERROR_CODE_KILL_SWITCH = "KILL_SWITCH_ACTIVE"
ERROR_CODE_QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
ERROR_CODE_APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
ERROR_CODE_APPROVAL_DENIED = "APPROVAL_DENIED"
ERROR_CODE_WORKSPACE_ISOLATION = "WORKSPACE_ISOLATION_VIOLATION"
ERROR_CODE_RATE_LIMITED = "RATE_LIMITED"
ERROR_CODE_BLOCKED_ACTION = "BLOCKED_ACTION"


@dataclass(frozen=True, slots=True)
class SafetyError:
    code: str
    message: str
    scope: str = ""
    trace_id: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    retryable: bool = False
    retry_after_seconds: int = 0


def kill_switch_error(*, scope: str, detail: str, trace_id: str = "") -> SafetyError:
    return SafetyError(
        code=ERROR_CODE_KILL_SWITCH,
        message=detail,
        scope=scope,
        trace_id=trace_id,
        details={"scope": scope},
        retryable=False,
    )


def quota_exceeded_error(
    *, profile: str, retry_after_seconds: int, trace_id: str = ""
) -> SafetyError:
    return SafetyError(
        code=ERROR_CODE_QUOTA_EXCEEDED,
        message=f"Quota exceeded for {profile}. Retry after {retry_after_seconds}s.",
        scope=profile,
        trace_id=trace_id,
        details={"profile": profile},
        retryable=True,
        retry_after_seconds=retry_after_seconds,
    )


def approval_required_error(
    *, action: str, approval_token: str = "", trace_id: str = ""
) -> SafetyError:
    return SafetyError(
        code=ERROR_CODE_APPROVAL_REQUIRED,
        message=f"Action '{action}' requires owner approval.",
        scope="approval",
        trace_id=trace_id,
        details={"action": action, "approval_token": approval_token},
        retryable=False,
    )


def blocked_action_error(
    *, action: str, reason: str, trace_id: str = ""
) -> SafetyError:
    return SafetyError(
        code=ERROR_CODE_BLOCKED_ACTION,
        message=f"Action '{action}' blocked: {reason}",
        scope="safety",
        trace_id=trace_id,
        details={"action": action, "reason": reason},
        retryable=False,
    )


def workspace_isolation_error(
    *, expected: str, actual: str, trace_id: str = ""
) -> SafetyError:
    return SafetyError(
        code=ERROR_CODE_WORKSPACE_ISOLATION,
        message="Cross-workspace access is not permitted.",
        scope="isolation",
        trace_id=trace_id,
        details={"expected_workspace": expected, "actual_workspace": actual},
        retryable=False,
    )


def to_http_body(error: SafetyError) -> dict:
    return {
        "code": error.code,
        "message": error.message,
        "scope": error.scope,
        "trace_id": error.trace_id,
        "retryable": error.retryable,
        "retry_after_seconds": error.retry_after_seconds,
        "details": error.details,
        "error": {
            "code": error.code,
            "message": error.message,
            "scope": error.scope,
            "trace_id": error.trace_id,
            "retryable": error.retryable,
            "retry_after_seconds": error.retry_after_seconds,
            "details": error.details,
        }
    }


def to_http_status(error: SafetyError) -> int:
    if error.code == ERROR_CODE_KILL_SWITCH:
        return 503
    if error.code in (ERROR_CODE_QUOTA_EXCEEDED, ERROR_CODE_RATE_LIMITED):
        return 429
    if error.code in (ERROR_CODE_APPROVAL_REQUIRED, ERROR_CODE_APPROVAL_DENIED):
        return 403
    if error.code == ERROR_CODE_WORKSPACE_ISOLATION:
        return 403
    return 400
