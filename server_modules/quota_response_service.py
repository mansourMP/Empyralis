from __future__ import annotations

import os
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from typing import Any, Dict, Optional

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from server_modules.channel_routing_models import ChannelExecutionResult
from server_modules.channel_user_acquisition_service import CHANNEL_ATTRIBUTION_QUERY_PARAM
from server_modules import error_response_service
from server_modules.error_contracts import EXECUTION_TIMEOUT, RATE_LIMIT
from server_modules.platform_event import (
    AGENT_LIMIT_EXCEEDED,
    QUOTA_REPLY_MAP,
    RUNTIME_CAP_EXCEEDED,
    SYSTEM_BUSY_FALLBACK,
    THREAD_BUSY,
    WORKSPACE_LIMIT_EXCEEDED,
    WORKSPACE_RATE_LIMITED,
)
from server_modules.quota_policy_service import QuotaDecision


_HTTP_DETAIL_BY_REASON = {
    "auth_login_rate_limited": "Too many attempts right now. Please wait a moment and try again.",
    "auth_public_rate_limited": "Too many requests right now. Please wait a moment and try again.",
    "authenticated_api_rate_limited": "Too many requests right now. Please try again shortly.",
    "service_api_rate_limited": "Too many requests right now. Please try again shortly.",
    "control_plane_rate_limited": "Setup services are busy right now. Please try again in a moment.",
    "auth_csrf_rate_limited": "Too many invalid security token attempts. Please wait a moment and try again.",
    "model_invocation_rate_limited": "Too many generation requests right now. Please try again shortly.",
    "mini_app_bridge_rate_limited": "Mini-app bridge traffic is currently limited. Please retry in a moment.",
    "mini_app_invoke_rate_limited": "Mini-app invoke traffic is currently limited. Please retry in a moment.",
}

_CHANNEL_REPLY_BY_REASON = {
    "thread_busy": THREAD_BUSY.channel_text,
    "agent_limit_exceeded": AGENT_LIMIT_EXCEEDED.channel_text,
    "workspace_limit_exceeded": WORKSPACE_LIMIT_EXCEEDED.channel_text,
    "workspace_rate_limited": WORKSPACE_RATE_LIMITED.channel_text,
    "runtime_cap_exceeded": RUNTIME_CAP_EXCEEDED.channel_text,
}

_CHANNEL_STATUS_BY_REASON = {
    "deployed_agent_daily_limit_exceeded": "rate_limited",
    "thread_busy": "thread_busy",
    "agent_limit_exceeded": "agent_busy",
    "workspace_limit_exceeded": "workspace_busy",
    "workspace_rate_limited": "rate_limited",
    "runtime_cap_exceeded": "runtime_capped",
}


def _channel_failure_class_for_reason(reason: Optional[str]) -> str:
    normalized = str(reason or "").strip()
    if normalized == "runtime_cap_exceeded":
        return EXECUTION_TIMEOUT
    return RATE_LIMIT


def http_detail_for_reason(reason: Optional[str]) -> str:
    return _HTTP_DETAIL_BY_REASON.get(
        str(reason or "").strip(),
        "The service is temporarily at capacity. Please try again in a moment.",
    )


def _append_query_params(url: Optional[str], params: Dict[str, Optional[str]]) -> Optional[str]:
    token = str(url or "").strip()
    if not token:
        return None
    parsed = urlparse(token)
    existing = dict(parse_qsl(parsed.query, keep_blank_values=True))
    for key, value in params.items():
        normalized_key = str(key or "").strip()
        normalized_value = str(value or "").strip()
        if normalized_key and normalized_value:
            existing[normalized_key] = normalized_value
    return urlunparse(parsed._replace(query=urlencode(existing)))


def _daily_limit_cta_payload(
    *,
    deployed_agent: Optional[Dict[str, Any]],
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Optional[str]]:
    details = dict(metadata or {})
    resolved_label = str(details.get("upgrade_cta_label") or "").strip() or "Continue in app"
    resolved_agent_id = str(
        details.get("deployed_agent_id")
        or (deployed_agent or {}).get("id")
        or ""
    ).strip() or None
    fallback_public_url = str(os.getenv("EMPYRALIS_PUBLIC_URL") or "").strip().rstrip("/")
    fallback_continue_url = f"{fallback_public_url}/continue" if fallback_public_url else None
    resolved_url = _append_query_params(
        str(details.get("upgrade_cta_url") or "").strip() or fallback_continue_url,
        {
            "source": "telegram_limit_hit",
            "agent": resolved_agent_id,
            CHANNEL_ATTRIBUTION_QUERY_PARAM: str(details.get("channel_attribution") or "").strip() or None,
        },
    )
    return {
        "label": resolved_label,
        "url": resolved_url,
        "agent_id": resolved_agent_id,
    }


def channel_reply_for_reason(
    reason: Optional[str],
    *,
    deployed_agent: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    normalized_reason = str(reason or "").strip()
    details = dict(metadata or {})
    if normalized_reason == "deployed_agent_daily_limit_exceeded":
        business_agent_name = str((deployed_agent or {}).get("name") or "").strip() or "this Business Agent"
        cta = _daily_limit_cta_payload(
            deployed_agent=deployed_agent,
            metadata=details,
        )
        reply = f"{business_agent_name} has reached today's free message limit."
        if cta.get("url") and cta.get("label"):
            reply = f"{reply} {cta['label']}: {cta['url']}"
        elif cta.get("url"):
            reply = f"{reply} Continue here: {cta['url']}"
        else:
            reply = f"{reply} Please come back tomorrow."
        from server_modules.external_user_privacy_service import get_external_user_privacy_service

        return get_external_user_privacy_service().append_privacy_policy_line(
            reply,
            workspace_id=str((deployed_agent or {}).get("owner_workspace_id") or "").strip() or None,
        )
    return _CHANNEL_REPLY_BY_REASON.get(normalized_reason, SYSTEM_BUSY_FALLBACK.channel_text)


def http_exception_from_quota_decision(decision: QuotaDecision) -> HTTPException:
    headers: Dict[str, str] = {}
    if decision.retry_after_seconds:
        headers["Retry-After"] = str(decision.retry_after_seconds)
    return HTTPException(
        status_code=int(decision.http_status or 429),
        detail={
            "code": str(decision.reason or "").strip() or "rate_limit_exceeded",
            "message": http_detail_for_reason(decision.reason),
            "class": RATE_LIMIT,
            "retryable": True,
            "details": {
                "response_class": decision.response_class,
                **{key: value for key, value in dict(decision.metadata or {}).items() if value is not None},
            },
        },
        headers=headers or None,
    )


def json_response_from_quota_decision(decision: QuotaDecision) -> JSONResponse:
    headers: Dict[str, str] = {}
    if decision.retry_after_seconds:
        headers["Retry-After"] = str(decision.retry_after_seconds)
    error = error_response_service.platform_error(
        code=str(decision.reason or "").strip() or "rate_limit_exceeded",
        message=http_detail_for_reason(decision.reason),
        error_class=RATE_LIMIT,
        retryable=True,
        status_code=int(decision.http_status or 429),
        details={
            "response_class": decision.response_class,
            **{key: value for key, value in dict(decision.metadata or {}).items() if value is not None},
        },
    )
    content = error_response_service.serialize_http_error_envelope(
        error_response_service.build_http_error_envelope(error)
    )
    if decision.retry_after_seconds:
        content["retry_after_seconds"] = decision.retry_after_seconds
    return JSONResponse(status_code=int(decision.http_status or 429), content=content, headers=headers or None)


def channel_result_from_quota_decision(
    *,
    decision: QuotaDecision,
    deployed_agent: Optional[Dict[str, Any]] = None,
    shared_metadata: Optional[Dict[str, Any]] = None,
    deployed_agent_id: Optional[str] = None,
    deployed_agent_state: Optional[str] = None,
) -> ChannelExecutionResult:
    details = dict(decision.metadata or {})
    status = _CHANNEL_STATUS_BY_REASON.get(str(decision.reason or "").strip(), "rate_limited")
    reply = channel_reply_for_reason(
        decision.reason,
        deployed_agent=deployed_agent,
        metadata=details,
    )
    error = error_response_service.platform_error(
        code=str(decision.reason or "").strip() or "rate_limit_exceeded",
        message=reply,
        error_class=_channel_failure_class_for_reason(decision.reason),
        retryable=True,
        status_code=int(decision.http_status or 429),
        details={
            "response_class": decision.response_class,
            "scope": decision.scope,
            **{key: value for key, value in details.items() if value is not None},
        },
    )
    payload: Dict[str, Any] = {
        "status": status,
        "limit_reason": decision.reason,
        "response_class": decision.response_class,
        "error": error_response_service.channel_error_payload(error),
    }
    if str(decision.reason or "").strip() == "deployed_agent_daily_limit_exceeded":
        cta = _daily_limit_cta_payload(
            deployed_agent=deployed_agent,
            metadata=details,
        )
        if cta.get("url"):
            payload["reply_markup"] = {
                "inline_keyboard": [[{
                    "text": str(cta.get("label") or "Continue in app"),
                    "url": str(cta.get("url")),
                }]],
            }
    payload.update({key: value for key, value in details.items() if value is not None})
    event_metadata: Dict[str, Any] = dict(shared_metadata or {})
    event_metadata.update({key: value for key, value in details.items() if value is not None})
    if decision.reason:
        event_metadata["limit_reason"] = decision.reason
    if decision.scope:
        event_metadata["rate_limit_scope"] = decision.scope
    metadata: Dict[str, Any] = {
        "response_class": decision.response_class,
        **{key: value for key, value in details.items() if value is not None},
    }
    if deployed_agent_id:
        metadata["deployed_agent_id"] = deployed_agent_id
    if deployed_agent_state:
        metadata["deployment_state"] = deployed_agent_state
    return ChannelExecutionResult(
        status=status,
        reply=reply,
        limit_reason=decision.reason,
        retry_after_seconds=decision.retry_after_seconds,
        quota_snapshot=dict(decision.quota_snapshot or {}) or None,
        metadata=metadata,
        payload=payload,
        event_metadata=event_metadata,
        error=error_response_service.channel_error_payload(error),
    )


def channel_payload_from_quota_decision(
    *,
    decision: QuotaDecision,
    deployed_agent: Optional[Dict[str, Any]] = None,
    shared_metadata: Optional[Dict[str, Any]] = None,
    deployed_agent_id: Optional[str] = None,
    deployed_agent_state: Optional[str] = None,
) -> Dict[str, Any]:
    result = channel_result_from_quota_decision(
        decision=decision,
        deployed_agent=deployed_agent,
        shared_metadata=shared_metadata,
        deployed_agent_id=deployed_agent_id,
        deployed_agent_state=deployed_agent_state,
    )
    return {
        "status": result.status,
        "reply": result.reply,
        "reply_markup": result.payload.get("reply_markup") if isinstance(result.payload.get("reply_markup"), dict) else None,
        "error": result.error,
        "artifact": result.artifact,
        "steps": result.steps,
        "critic": result.critic,
        "quota_snapshot": result.quota_snapshot,
        "retry_after_seconds": result.retry_after_seconds,
        "limit_reason": result.limit_reason,
        "metadata": result.metadata,
        "event_type": result.event_type,
        "incident_scope": result.incident_scope,
        "incident_mode": result.incident_mode,
        "payload": result.payload,
        "event_metadata": result.event_metadata,
        "health_safety": result.health_safety,
        "degraded_operations": result.degraded_operations,
    }
