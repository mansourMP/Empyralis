from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ErrorContext:
    method: str = "GET"
    path: str = ""
    status_code: Optional[int] = None
    raw: str = ""


class OrionTerminalError(RuntimeError):
    code = "terminal_error"
    retryable = False

    def __init__(self, message: str, *, context: Optional[ErrorContext] = None):
        super().__init__(message)
        self.context = context or ErrorContext()


class RuntimeUnavailable(OrionTerminalError):
    code = "runtime_unavailable"
    retryable = True


class AuthExpired(OrionTerminalError):
    code = "auth_expired"
    retryable = False


class PermissionDenied(OrionTerminalError):
    code = "permission_denied"
    retryable = False


class ValidationFailed(OrionTerminalError):
    code = "validation_failed"
    retryable = False


class ConflictState(OrionTerminalError):
    code = "conflict_state"
    retryable = False


class TimeoutExceeded(OrionTerminalError):
    code = "timeout_exceeded"
    retryable = True


class TransientUpstreamError(OrionTerminalError):
    code = "transient_upstream_error"
    retryable = True


class CompatibilityError(OrionTerminalError):
    code = "compatibility_error"
    retryable = False


class UnknownRuntimeError(OrionTerminalError):
    code = "unknown_runtime_error"
    retryable = False


_HTTP_STATUS_RE = re.compile(r"\bHTTP\s+(\d{3})\b", re.IGNORECASE)
_URLERROR_STATUS_RE = re.compile(r"\bstatus\s*=\s*(\d{3})\b", re.IGNORECASE)


def extract_status_code(error_text: str) -> Optional[int]:
    text = str(error_text or "")
    for pattern in (_HTTP_STATUS_RE, _URLERROR_STATUS_RE):
        match = pattern.search(text)
        if match:
            try:
                return int(match.group(1))
            except Exception:
                return None
    return None


def map_runtime_exception(exc: Exception, *, method: str = "GET", path: str = "") -> OrionTerminalError:
    if isinstance(exc, OrionTerminalError):
        return exc

    message = str(exc or "").strip() or exc.__class__.__name__
    lowered = message.lower()
    status = extract_status_code(message)
    context = ErrorContext(method=method.upper().strip() or "GET", path=path, status_code=status, raw=message)

    if "runtime_api_min_cli_version" in lowered or "cli_version" in lowered or "compatib" in lowered:
        return CompatibilityError(message, context=context)

    if (
        "network error" in lowered
        or "connection refused" in lowered
        or "connection reset" in lowered
        or "temporarily unavailable" in lowered
        or "name or service not known" in lowered
        or "nodename nor servname provided" in lowered
    ):
        return RuntimeUnavailable(message, context=context)

    if "timed out" in lowered or "timeout" in lowered:
        return TimeoutExceeded(message, context=context)

    if status in {401} or "unauthorized" in lowered:
        return AuthExpired(message, context=context)

    if status in {403} or "forbidden" in lowered:
        return PermissionDenied(message, context=context)

    if status in {400, 404, 405, 410, 422}:
        return ValidationFailed(message, context=context)

    if status in {409}:
        return ConflictState(message, context=context)

    if status in {429, 500, 502, 503, 504}:
        return TransientUpstreamError(message, context=context)

    return UnknownRuntimeError(message, context=context)


def should_retry_error(error: OrionTerminalError, *, allow_unsafe_retry: bool = False) -> bool:
    if not error.retryable:
        return False
    method = (error.context.method or "GET").upper()
    if method in {"GET", "HEAD", "OPTIONS"}:
        return True
    return bool(allow_unsafe_retry)

