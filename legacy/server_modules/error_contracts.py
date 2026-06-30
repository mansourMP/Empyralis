from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


USER_INPUT_ERROR = "user_input_error"
AUTHORIZATION_ERROR = "authorization_error"
POLICY_BLOCK = "policy_block"
RATE_LIMIT = "rate_limit"
IDEMPOTENCY_CONFLICT = "idempotency_conflict"
UPSTREAM_DEPENDENCY_FAILURE = "upstream_dependency_failure"
STORAGE_READ_FAILURE = "storage_read_failure"
STORAGE_WRITE_FAILURE = "storage_write_failure"
OBSERVABILITY_FAILURE = "observability_failure"
EXECUTION_TIMEOUT = "execution_timeout"
INTERNAL_ERROR = "internal_error"

FAILURE_CLASSES = frozenset(
    {
        USER_INPUT_ERROR,
        AUTHORIZATION_ERROR,
        POLICY_BLOCK,
        RATE_LIMIT,
        IDEMPOTENCY_CONFLICT,
        UPSTREAM_DEPENDENCY_FAILURE,
        STORAGE_READ_FAILURE,
        STORAGE_WRITE_FAILURE,
        OBSERVABILITY_FAILURE,
        EXECUTION_TIMEOUT,
        INTERNAL_ERROR,
    }
)

SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_ERROR = "error"
SEVERITY_CRITICAL = "critical"

FAILURE_SEVERITIES = frozenset(
    {
        SEVERITY_INFO,
        SEVERITY_WARNING,
        SEVERITY_ERROR,
        SEVERITY_CRITICAL,
    }
)


def _normalize_details(value: Any) -> Dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


@dataclass(slots=True)
class PlatformError:
    code: str
    message: str
    error_class: str
    retryable: bool = False
    status_code: int = 500
    request_id: Optional[str] = None
    trace_id: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ErrorEnvelopeHttp:
    detail: str
    error: PlatformError


@dataclass(slots=True)
class ErrorEnvelopeChannel:
    status: str
    reply: str
    error: PlatformError
    retry_after_seconds: Optional[int] = None
    audit: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ErrorEnvelopeStream:
    kind: str
    error: PlatformError
    trace_id: Optional[str] = None
    request_id: Optional[str] = None


@dataclass(slots=True)
class DegradedOperationNotice:
    error: PlatformError
    degraded_component: str
    severity: str = SEVERITY_WARNING
    swallowed_intentionally: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)


def platform_error_payload(error: PlatformError) -> Dict[str, Any]:
    return {
        "code": str(error.code or "").strip() or "internal_error",
        "message": str(error.message or "").strip() or "Internal server error.",
        "class": (
            str(error.error_class or "").strip()
            if str(error.error_class or "").strip() in FAILURE_CLASSES
            else INTERNAL_ERROR
        ),
        "retryable": bool(error.retryable),
        "status_code": int(error.status_code or 500),
        "request_id": str(error.request_id or "").strip() or None,
        "trace_id": str(error.trace_id or "").strip() or None,
        "details": _normalize_details(error.details),
    }


def degraded_notice_payload(notice: DegradedOperationNotice) -> Dict[str, Any]:
    severity = str(notice.severity or "").strip().lower()
    if severity not in FAILURE_SEVERITIES:
        severity = SEVERITY_WARNING
    error_payload = platform_error_payload(notice.error)
    return {
        "error": error_payload,
        "error_code": error_payload["code"],
        "error_class": error_payload["class"],
        "degraded_component": str(notice.degraded_component or "").strip() or "unknown",
        "severity": severity,
        "swallowed_intentionally": bool(notice.swallowed_intentionally),
        "metadata": _normalize_details(notice.metadata),
    }
