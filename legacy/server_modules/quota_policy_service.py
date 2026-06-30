from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from server_modules import deployed_agent_daily_quota_adapter, request_window_quota_adapter


@dataclass(frozen=True)
class QuotaSubject:
    tenant_id: Optional[str] = None
    workspace_id: Optional[str] = None
    surface_kind: str = ""
    channel_key: Optional[str] = None
    deployed_agent_id: Optional[str] = None
    external_user_id: Optional[str] = None
    responder_install_id: Optional[str] = None
    thread_id: Optional[str] = None
    session_key: Optional[str] = None
    actor_id: Optional[str] = None
    request_path: Optional[str] = None
    client_ip: Optional[str] = None


@dataclass(frozen=True)
class QuotaPolicyProfile:
    name: str
    surface_kind: str
    deny_reason: Optional[str] = None


@dataclass(frozen=True)
class QuotaDecision:
    allowed: bool
    reason: Optional[str]
    scope: Optional[str]
    surface_kind: str
    retry_after_seconds: Optional[int] = None
    quota_snapshot: Optional[Dict[str, Any]] = None
    response_class: Optional[str] = None
    http_status: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


AUTH_LOGIN_PROFILE = QuotaPolicyProfile(
    name="auth_login",
    surface_kind="auth_login",
    deny_reason="auth_login_rate_limited",
)
AUTH_PUBLIC_REGISTRATION_PROFILE = QuotaPolicyProfile(
    name="auth_public_registration",
    surface_kind="auth_public",
    deny_reason="auth_public_rate_limited",
)
AUTHENTICATED_API_PROFILE = QuotaPolicyProfile(
    name="authenticated_api",
    surface_kind="authenticated_api",
    deny_reason="authenticated_api_rate_limited",
)
SERVICE_API_PROFILE = QuotaPolicyProfile(
    name="service_api",
    surface_kind="service_api",
    deny_reason="service_api_rate_limited",
)
CONTROL_PLANE_MUTATION_PROFILE = QuotaPolicyProfile(
    name="control_plane_mutation",
    surface_kind="control_plane_mutation",
    deny_reason="control_plane_rate_limited",
)
AUTH_CSRF_FAILURE_PROFILE = QuotaPolicyProfile(
    name="auth_csrf_failure",
    surface_kind="auth_csrf",
    deny_reason="auth_csrf_rate_limited",
)
AUTH_REFRESH_PROFILE = QuotaPolicyProfile(
    name="auth_refresh",
    surface_kind="auth_refresh",
    deny_reason="auth_refresh_rate_limited",
)
MODEL_INVOCATION_PROFILE = QuotaPolicyProfile(
    name="model_invocation",
    surface_kind="model_invocation",
    deny_reason="model_invocation_rate_limited",
)
MINI_APP_BRIDGE_PROFILE = QuotaPolicyProfile(
    name="mini_app_bridge",
    surface_kind="mini_app_bridge",
    deny_reason="mini_app_bridge_rate_limited",
)
MINI_APP_INVOKE_PROFILE = QuotaPolicyProfile(
    name="mini_app_invoke",
    surface_kind="mini_app_invoke",
    deny_reason="mini_app_invoke_rate_limited",
)
DEPLOYED_AGENT_PUBLIC_TURN_PROFILE = QuotaPolicyProfile(
    name="deployed_agent_public_turn",
    surface_kind="deployed_agent_channel",
)
DEPLOYED_AGENT_INTERNAL_TRANSPORT_PROFILE = QuotaPolicyProfile(
    name="deployed_agent_internal_transport",
    surface_kind="deployed_agent_channel",
)

_PROFILES = {
    AUTH_LOGIN_PROFILE.name: AUTH_LOGIN_PROFILE,
    AUTH_PUBLIC_REGISTRATION_PROFILE.name: AUTH_PUBLIC_REGISTRATION_PROFILE,
    AUTHENTICATED_API_PROFILE.name: AUTHENTICATED_API_PROFILE,
    SERVICE_API_PROFILE.name: SERVICE_API_PROFILE,
    CONTROL_PLANE_MUTATION_PROFILE.name: CONTROL_PLANE_MUTATION_PROFILE,
    AUTH_CSRF_FAILURE_PROFILE.name: AUTH_CSRF_FAILURE_PROFILE,
    AUTH_REFRESH_PROFILE.name: AUTH_REFRESH_PROFILE,
    MODEL_INVOCATION_PROFILE.name: MODEL_INVOCATION_PROFILE,
    MINI_APP_BRIDGE_PROFILE.name: MINI_APP_BRIDGE_PROFILE,
    MINI_APP_INVOKE_PROFILE.name: MINI_APP_INVOKE_PROFILE,
    DEPLOYED_AGENT_PUBLIC_TURN_PROFILE.name: DEPLOYED_AGENT_PUBLIC_TURN_PROFILE,
    DEPLOYED_AGENT_INTERNAL_TRANSPORT_PROFILE.name: DEPLOYED_AGENT_INTERNAL_TRANSPORT_PROFILE,
}

_RESPONSE_CLASS_BY_REASON = {
    "auth_login_rate_limited": "http_rate_limited",
    "auth_public_rate_limited": "http_rate_limited",
    "authenticated_api_rate_limited": "http_rate_limited",
    "service_api_rate_limited": "http_rate_limited",
    "control_plane_rate_limited": "http_rate_limited",
    "auth_csrf_rate_limited": "http_rate_limited",
    "auth_refresh_rate_limited": "http_rate_limited",
    "model_invocation_rate_limited": "http_rate_limited",
    "mini_app_bridge_rate_limited": "http_rate_limited",
    "mini_app_invoke_rate_limited": "http_rate_limited",
    "deployed_agent_daily_limit_exceeded": "upgrade_required",
    "thread_busy": "thread_busy",
    "agent_limit_exceeded": "specialist_busy",
    "workspace_limit_exceeded": "workspace_busy",
    "workspace_rate_limited": "traffic_surge",
    "runtime_cap_exceeded": "service_window_exceeded",
}

_SCOPE_BY_REASON = {
    "auth_login_rate_limited": "auth_login_ip_window",
    "auth_public_rate_limited": "auth_public_ip_window",
    "authenticated_api_rate_limited": "authenticated_api_actor_window",
    "service_api_rate_limited": "service_api_actor_window",
    "control_plane_rate_limited": "control_plane_identity_window",
    "auth_csrf_rate_limited": "auth_csrf_failure_ip_window",
    "auth_refresh_rate_limited": "auth_refresh_ip_window",
    "model_invocation_rate_limited": "model_invocation_actor_window",
    "mini_app_bridge_rate_limited": "mini_app_bridge_actor_window",
    "mini_app_invoke_rate_limited": "mini_app_invoke_actor_window",
    "deployed_agent_daily_limit_exceeded": "deployed_agent_external_user_day",
    "thread_busy": "conversation_thread",
    "agent_limit_exceeded": "agent_active_threads",
    "workspace_limit_exceeded": "workspace_active_threads",
    "workspace_rate_limited": "workspace_turn_rate",
    "runtime_cap_exceeded": "runtime_service_window",
}


def _quota_snapshot_as_dict(snapshot: Any) -> Optional[Dict[str, Any]]:
    if snapshot is None:
        return None
    if hasattr(snapshot, "as_dict"):
        return snapshot.as_dict()
    if isinstance(snapshot, dict):
        return dict(snapshot)
    return None


def get_quota_policy_profile(name: str) -> QuotaPolicyProfile:
    profile = _PROFILES.get(str(name or "").strip())
    if profile is None:
        raise KeyError(f"Unknown quota policy profile: {name}")
    return profile


def allow_quota_decision(
    *,
    profile: QuotaPolicyProfile,
    metadata: Optional[Dict[str, Any]] = None,
) -> QuotaDecision:
    return QuotaDecision(
        allowed=True,
        reason=None,
        scope=None,
        surface_kind=profile.surface_kind,
        http_status=200,
        metadata=dict(metadata or {}),
    )


def deny_quota_decision(
    *,
    profile: QuotaPolicyProfile,
    reason: str,
    retry_after_seconds: Optional[int] = None,
    quota_snapshot: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    scope: Optional[str] = None,
    response_class: Optional[str] = None,
    http_status: int = 429,
) -> QuotaDecision:
    normalized_reason = str(reason or "").strip() or (profile.deny_reason or "rate_limited")
    return QuotaDecision(
        allowed=False,
        reason=normalized_reason,
        scope=scope or _SCOPE_BY_REASON.get(normalized_reason),
        surface_kind=profile.surface_kind,
        retry_after_seconds=max(int(retry_after_seconds or 0), 1) if retry_after_seconds else None,
        quota_snapshot=dict(quota_snapshot or {}) or None,
        response_class=response_class or _RESPONSE_CLASS_BY_REASON.get(normalized_reason),
        http_status=max(int(http_status or 429), 400),
        metadata=dict(metadata or {}),
    )


def evaluate_request_window_quota(
    *,
    profile_name: str,
    subject: QuotaSubject,
    buckets: Dict[str, list[float]],
    lock: Any,
    key: str,
    limit: int,
    now: Optional[float] = None,
) -> QuotaDecision:
    profile = get_quota_policy_profile(profile_name)
    window = request_window_quota_adapter.evaluate_request_window(
        buckets=buckets,
        lock=lock,
        key=key,
        limit=limit,
        now=now,
    )
    metadata = {
        "quota_key": str(key or "").strip() or None,
        "window_seconds": 60,
        "limit": int(window.get("limit") or max(int(limit or 0), 1)),
        "observed": int(window.get("observed") or 0),
        "request_path": subject.request_path,
        "client_ip": subject.client_ip,
        "actor_id": subject.actor_id,
    }
    if window.get("allowed", False):
        return allow_quota_decision(profile=profile, metadata=metadata)
    return deny_quota_decision(
        profile=profile,
        reason=profile.deny_reason or "rate_limited",
        retry_after_seconds=int(window.get("retry_after_seconds") or 1),
        metadata=metadata,
    )


async def evaluate_channel_quota(
    *,
    profile_name: str,
    subject: QuotaSubject,
    deployed_agent: Optional[Dict[str, Any]],
    message_id: Optional[str] = None,
) -> QuotaDecision:
    profile = get_quota_policy_profile(profile_name)
    quota_verdict = await deployed_agent_daily_quota_adapter.evaluate_daily_quota(
        tenant_id=str(subject.tenant_id or "").strip(),
        workspace_id=str(subject.workspace_id or "").strip(),
        deployed_agent=deployed_agent,
        channel_key=str(subject.channel_key or "").strip(),
        external_user_id=str(subject.external_user_id or "").strip() or None,
        message_id=str(message_id or "").strip() or None,
    )
    if not quota_verdict.get("applied") or quota_verdict.get("allowed", True):
        return allow_quota_decision(
            profile=profile,
            metadata={
                "applied": bool(quota_verdict.get("applied")),
                "deployed_agent_id": subject.deployed_agent_id,
                "daily_message_limit": quota_verdict.get("daily_message_limit"),
                "message_count": quota_verdict.get("message_count"),
                "remaining": quota_verdict.get("remaining"),
                "usage_day": quota_verdict.get("usage_day"),
                "warning_sent": quota_verdict.get("warning_sent"),
            },
        )
    return deny_quota_decision(
        profile=profile,
        reason="deployed_agent_daily_limit_exceeded",
        retry_after_seconds=int(quota_verdict.get("retry_after_seconds") or 1),
        metadata={
            "deployed_agent_id": subject.deployed_agent_id,
            "daily_message_limit": quota_verdict.get("daily_message_limit"),
            "message_count": quota_verdict.get("message_count"),
            "remaining": quota_verdict.get("remaining"),
            "usage_day": quota_verdict.get("usage_day"),
            "upgrade_cta_url": quota_verdict.get("upgrade_cta_url"),
            "upgrade_cta_label": quota_verdict.get("upgrade_cta_label"),
            "channel_attribution": quota_verdict.get("channel_attribution"),
        },
    )


def channel_limit_error_decision(
    *,
    subject: QuotaSubject,
    error: Any,
) -> QuotaDecision:
    return deny_quota_decision(
        profile=DEPLOYED_AGENT_PUBLIC_TURN_PROFILE,
        reason=str(getattr(error, "reason", "") or "workspace_limit_exceeded"),
        retry_after_seconds=int(getattr(error, "retry_after_seconds", 1) or 1),
        quota_snapshot=_quota_snapshot_as_dict(getattr(error, "quota_snapshot", None)),
        metadata={
            "deployed_agent_id": subject.deployed_agent_id,
            "responder_install_id": subject.responder_install_id,
            "thread_id": subject.thread_id,
            "session_key": subject.session_key,
        },
    )


def runtime_cap_quota_decision(
    *,
    subject: QuotaSubject,
    quota_snapshot: Any,
) -> QuotaDecision:
    snapshot_dict = _quota_snapshot_as_dict(quota_snapshot) or {}
    max_runtime_seconds = int(snapshot_dict.get("max_runtime_seconds") or 0)
    retry_after_seconds = max(min(max_runtime_seconds // 2, 30), 5) if max_runtime_seconds else 5
    return deny_quota_decision(
        profile=DEPLOYED_AGENT_PUBLIC_TURN_PROFILE,
        reason="runtime_cap_exceeded",
        retry_after_seconds=retry_after_seconds,
        quota_snapshot=snapshot_dict,
        metadata={
            "deployed_agent_id": subject.deployed_agent_id,
            "responder_install_id": subject.responder_install_id,
            "thread_id": subject.thread_id,
            "session_key": subject.session_key,
        },
    )
