from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from server_modules import error_response_service
from server_modules.error_contracts import (
    DegradedOperationNotice,
    PlatformError,
    SEVERITY_WARNING,
    degraded_notice_payload,
)


def degraded_notice(
    *,
    code: str,
    message: str,
    error_class: str,
    degraded_component: str,
    severity: str = SEVERITY_WARNING,
    retryable: bool = False,
    status_code: int = 500,
    request_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    error = error_response_service.platform_error(
        code=code,
        message=message,
        error_class=error_class,
        retryable=retryable,
        status_code=status_code,
        request_id=request_id,
        trace_id=trace_id,
        details=details,
    )
    notice = DegradedOperationNotice(
        error=error,
        degraded_component=str(degraded_component or "").strip() or "unknown",
        severity=str(severity or "").strip().lower() or SEVERITY_WARNING,
        swallowed_intentionally=True,
        metadata=dict(metadata or {}),
    )
    return degraded_notice_payload(notice)


def log_degraded_operation(
    *,
    logger: logging.Logger,
    code: str,
    message: str,
    error_class: str,
    degraded_component: str,
    severity: str = SEVERITY_WARNING,
    retryable: bool = False,
    status_code: int = 500,
    request_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    exc: Optional[Exception] = None,
) -> Dict[str, Any]:
    notice = degraded_notice(
        code=code,
        message=message,
        error_class=error_class,
        degraded_component=degraded_component,
        severity=severity,
        retryable=retryable,
        status_code=status_code,
        request_id=request_id,
        trace_id=trace_id,
        details=details,
        metadata=metadata,
    )
    error_response_service.log_degraded_notice(logger, notice, exc=exc)
    return notice

