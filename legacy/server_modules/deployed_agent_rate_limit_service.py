from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from server_modules import channel_user_acquisition_service
from server_modules import control_plane_repository
from server_modules import deployed_agent_config_schema


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _normalize_optional_text(value: Any) -> Optional[str]:
    token = str(value or "").strip()
    return token or None


def _normalize_optional_positive_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    token = str(value).strip()
    if not token:
        return None
    try:
        parsed = int(token)
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return parsed


def _usage_day_token(now: Optional[datetime] = None) -> str:
    current = now.astimezone(timezone.utc) if isinstance(now, datetime) else datetime.now(timezone.utc)
    return current.date().isoformat()


def _next_utc_midnight(now: Optional[datetime] = None) -> datetime:
    current = now.astimezone(timezone.utc) if isinstance(now, datetime) else datetime.now(timezone.utc)
    midnight = datetime(current.year, current.month, current.day, tzinfo=timezone.utc)
    return midnight + timedelta(days=1)


def deployed_agent_daily_limit_settings(
    *,
    deployed_agent: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    config = deployed_agent_config_schema.deployed_agent_config_from_record(deployed_agent)
    return {
        "deployed_agent_id": _normalize_optional_text((deployed_agent or {}).get("id")),
        "daily_message_limit": _normalize_optional_positive_int(
            config.customer_policy.daily_message_limit
        ),
        "upgrade_cta_url": _normalize_optional_text(config.customer_policy.upgrade_cta_url),
        "upgrade_cta_label": _normalize_optional_text(config.customer_policy.upgrade_cta_label),
    }


async def enforce_deployed_agent_daily_message_limit(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent: Optional[Dict[str, Any]],
    channel_key: str,
    external_user_id: Optional[str],
    message_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    settings = deployed_agent_daily_limit_settings(deployed_agent=deployed_agent)
    limit = settings.get("daily_message_limit")
    deployed_agent_id = settings.get("deployed_agent_id")
    resolved_external_user_id = _normalize_optional_text(external_user_id)
    if not deployed_agent_id or not resolved_external_user_id or limit is None:
        return {
            **settings,
            "applied": False,
            "allowed": True,
            "usage_day": _usage_day_token(now),
            "message_count": 0,
            "remaining": None,
            "warning_sent": False,
            "retry_after_seconds": None,
            "reset_at": _next_utc_midnight(now).isoformat().replace("+00:00", "Z"),
        }
    usage_day = _usage_day_token(now)
    usage = await control_plane_repository.consume_deployed_agent_daily_message_quota(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        deployed_agent_id=deployed_agent_id,
        channel_key=str(channel_key or "").strip().lower(),
        external_user_id=resolved_external_user_id,
        usage_day=usage_day,
        limit=int(limit),
        metadata={
            "message_id": _normalize_optional_text(message_id),
        },
    )
    usage_payload = _coerce_dict(usage)
    message_count = int(usage_payload.get("message_count") or 0)
    allowed = bool(usage_payload.get("quota_consumed"))
    remaining = max(int(limit) - message_count, 0)
    reset_at = _next_utc_midnight(now)
    channel_attribution: Optional[str] = None
    normalized_channel_key = str(channel_key or "").strip().lower() or "telegram"
    if not allowed and resolved_external_user_id:
        deployed_agent_payload = dict(deployed_agent or {})
        channel_bindings = _coerce_dict(deployed_agent_payload.get("channels"))
        selected_binding = _coerce_dict(channel_bindings.get(normalized_channel_key))
        acquisition = channel_user_acquisition_service.get_channel_user_acquisition_service()
        try:
            attribution_payload = acquisition.ensure_touch_attribution_token(
                tenant_id=str(tenant_id or "").strip(),
                workspace_id=str(workspace_id or "").strip(),
                deployed_agent_id=str(deployed_agent_id or "").strip(),
                channel_key=normalized_channel_key,
                external_user_id=resolved_external_user_id,
                endpoint_key=_normalize_optional_text(selected_binding.get("endpoint_key")),
                source=f"{normalized_channel_key}_limit_hit",
                metadata={
                    "daily_limit_triggered": True,
                    "message_id": _normalize_optional_text(message_id),
                },
            )
            channel_attribution = _normalize_optional_text(attribution_payload.get("attribution_token"))
        except Exception:
            channel_attribution = None
    return {
        **settings,
        "applied": True,
        "allowed": allowed,
        "usage_day": usage_day,
        "message_count": message_count,
        "remaining": remaining,
        "warning_sent": bool(usage_payload.get("warning_sent")),
        "retry_after_seconds": max(int((reset_at - (now.astimezone(timezone.utc) if isinstance(now, datetime) else datetime.now(timezone.utc))).total_seconds()), 1),
        "reset_at": reset_at.isoformat().replace("+00:00", "Z"),
        "channel_attribution": channel_attribution,
    }
