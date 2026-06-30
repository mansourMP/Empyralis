from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from server_modules.error_contracts import (
    AUTHORIZATION_ERROR,
    ErrorEnvelopeChannel,
    ErrorEnvelopeHttp,
    ErrorEnvelopeStream,
    IDEMPOTENCY_CONFLICT,
    INTERNAL_ERROR,
    PlatformError,
    RATE_LIMIT,
    USER_INPUT_ERROR,
    EXECUTION_TIMEOUT,
    OBSERVABILITY_FAILURE,
    degraded_notice_payload,
    platform_error_payload,
)


logger = logging.getLogger(__name__)


def request_id_from_request(request: Optional[Request]) -> Optional[str]:
    if request is None:
        return None
    state = getattr(request, "state", None)
    if state is not None:
        for field in ("request_id", "trace_id"):
            token = str(getattr(state, field, "") or "").strip()
            if token:
                return token
    for header_name in ("x-request-id", "x-correlation-id"):
        token = str(request.headers.get(header_name) or "").strip()
        if token:
            return token
    return None


def _http_failure_class(status_code: int, *, code: str, message: str) -> str:
    if status_code == 409 and ("idempotency" in code or "idempotency" in message.lower()):
        return IDEMPOTENCY_CONFLICT
    if status_code in {401, 403}:
        return AUTHORIZATION_ERROR
    if status_code == 429:
        return RATE_LIMIT
    if status_code >= 500:
        return INTERNAL_ERROR
    return USER_INPUT_ERROR


def _http_error_code(status_code: int, detail: Any) -> str:
    if isinstance(detail, dict):
        token = str(detail.get("code") or "").strip()
        if token:
            return token
    if status_code == 400:
        return "invalid_request"
    if status_code == 401:
        return "authentication_required"
    if status_code == 403:
        return "forbidden"
    if status_code == 404:
        return "not_found"
    if status_code == 409:
        return "conflict"
    if status_code == 422:
        return "request_validation_error"
    if status_code == 429:
        return "rate_limit_exceeded"
    if status_code >= 500:
        return "internal_error"
    return f"http_{status_code}"


def _http_error_message(status_code: int, detail: Any) -> str:
    if isinstance(detail, dict):
        for key in ("message", "detail", "error"):
            token = str(detail.get(key) or "").strip()
            if token:
                return token
    if isinstance(detail, list):
        return "Request validation failed."
    token = str(detail or "").strip()
    if token:
        return token
    if status_code >= 500:
        return "Internal server error."
    return "Request failed."


def _http_error_details(detail: Any) -> Dict[str, Any]:
    if isinstance(detail, dict):
        return {
            key: value
            for key, value in dict(detail).items()
            if key not in {"code", "message"}
        }
    if isinstance(detail, list):
        return {"errors": detail}
    return {}


def platform_error(
    *,
    code: str,
    message: str,
    error_class: str,
    retryable: bool = False,
    status_code: int = 500,
    request_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> PlatformError:
    return PlatformError(
        code=str(code or "").strip() or "internal_error",
        message=str(message or "").strip() or "Internal server error.",
        error_class=str(error_class or "").strip() or INTERNAL_ERROR,
        retryable=bool(retryable),
        status_code=int(status_code or 500),
        request_id=str(request_id or "").strip() or None,
        trace_id=str(trace_id or "").strip() or None,
        details=dict(details or {}),
    )


def http_platform_error_from_exception(
    exc: HTTPException | StarletteHTTPException,
    *,
    request_id: Optional[str] = None,
) -> PlatformError:
    status_code = int(getattr(exc, "status_code", 500) or 500)
    detail = getattr(exc, "detail", None)
    code = _http_error_code(status_code, detail)
    message = _http_error_message(status_code, detail)
    details = _http_error_details(detail)
    return platform_error(
        code=code,
        message=message,
        error_class=_http_failure_class(status_code, code=code, message=message),
        retryable=status_code in {409, 429, 503},
        status_code=status_code,
        request_id=request_id,
        details=details,
    )


def build_http_error_envelope(error: PlatformError) -> ErrorEnvelopeHttp:
    return ErrorEnvelopeHttp(
        detail=str(error.message or "").strip() or "Request failed.",
        error=error,
    )


def serialize_http_error_envelope(envelope: ErrorEnvelopeHttp) -> Dict[str, Any]:
    return {
        "detail": envelope.detail,
        "error": platform_error_payload(envelope.error),
    }


def json_response_from_platform_error(
    error: PlatformError,
    *,
    headers: Optional[Dict[str, str]] = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=int(error.status_code or 500),
        content=serialize_http_error_envelope(build_http_error_envelope(error)),
        headers=headers or None,
    )


def channel_error_payload(error: PlatformError) -> Dict[str, Any]:
    return platform_error_payload(error)


def build_channel_error_envelope(
    *,
    status: str,
    reply: str,
    error: PlatformError,
    retry_after_seconds: Optional[int] = None,
    audit: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> ErrorEnvelopeChannel:
    return ErrorEnvelopeChannel(
        status=str(status or "").strip() or "error",
        reply=str(reply or "").strip(),
        error=error,
        retry_after_seconds=int(retry_after_seconds) if retry_after_seconds is not None else None,
        audit=dict(audit or {}),
        metadata=dict(metadata or {}),
    )


def serialize_channel_error_envelope(envelope: ErrorEnvelopeChannel) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "status": envelope.status,
        "reply": envelope.reply,
        "error": channel_error_payload(envelope.error),
        "audit": dict(envelope.audit or {}),
        "metadata": dict(envelope.metadata or {}),
    }
    if envelope.retry_after_seconds is not None:
        payload["retry_after_seconds"] = envelope.retry_after_seconds
    return payload


def build_stream_error_envelope(
    *,
    error: PlatformError,
    request_id: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> ErrorEnvelopeStream:
    return ErrorEnvelopeStream(
        kind="terminal_error",
        error=PlatformError(
            code=error.code,
            message=error.message,
            error_class=error.error_class,
            retryable=error.retryable,
            status_code=error.status_code,
            request_id=str(request_id or error.request_id or "").strip() or None,
            trace_id=str(trace_id or error.trace_id or "").strip() or None,
            details=dict(error.details or {}),
        ),
        trace_id=str(trace_id or error.trace_id or "").strip() or None,
        request_id=str(request_id or error.request_id or "").strip() or None,
    )


def serialize_stream_error_envelope(envelope: ErrorEnvelopeStream) -> Dict[str, Any]:
    return {
        "kind": envelope.kind,
        "error": platform_error_payload(envelope.error),
        "trace_id": str(envelope.trace_id or "").strip() or None,
        "request_id": str(envelope.request_id or "").strip() or None,
    }


def http_exception_response(request: Request, exc: HTTPException | StarletteHTTPException) -> JSONResponse:
    request_id = request_id_from_request(request)
    error = http_platform_error_from_exception(exc, request_id=request_id)
    headers = dict(getattr(exc, "headers", None) or {})
    return json_response_from_platform_error(error, headers=headers)


def request_validation_error_response(request: Request, exc: RequestValidationError) -> JSONResponse:
    error = platform_error(
        code="request_validation_error",
        message="Request validation failed.",
        error_class=USER_INPUT_ERROR,
        retryable=False,
        status_code=422,
        request_id=request_id_from_request(request),
        details={"errors": exc.errors()},
    )
    return json_response_from_platform_error(error)


def unhandled_exception_response(request: Request, exc: Exception) -> JSONResponse:
    request_id = request_id_from_request(request)
    logger.exception(
        "Unhandled API exception",
        extra={
            "error_code": "internal_error",
            "error_class": INTERNAL_ERROR,
            "request_id": request_id,
            "path": str(request.url.path or "").strip() or "/",
            "method": str(request.method or "GET").upper(),
        },
    )
    error = platform_error(
        code="internal_error",
        message="Internal server error.",
        error_class=INTERNAL_ERROR,
        retryable=False,
        status_code=500,
        request_id=request_id,
    )
    return json_response_from_platform_error(error)


def log_degraded_notice(logger_obj: logging.Logger, notice: Dict[str, Any], *, exc: Optional[Exception] = None) -> None:
    message = str(
        (
            (notice.get("error") or {}).get("message")
            if isinstance(notice.get("error"), dict)
            else ""
        )
        or "Degraded operation notice."
    ).strip()
    extra = {
        "error_code": notice.get("error_code"),
        "error_class": notice.get("error_class"),
        "degraded_component": notice.get("degraded_component"),
        "severity": notice.get("severity"),
        "request_id": ((notice.get("error") or {}).get("request_id") if isinstance(notice.get("error"), dict) else None),
        "trace_id": ((notice.get("error") or {}).get("trace_id") if isinstance(notice.get("error"), dict) else None),
        **dict(notice.get("metadata") or {}),
    }
    severity = str(notice.get("severity") or "").strip().lower()
    log_method = logger_obj.error if severity in {"error", "critical"} else logger_obj.warning
    if exc is None:
        log_method(message, extra=extra)
    else:
        log_method(message, exc_info=exc, extra=extra)
