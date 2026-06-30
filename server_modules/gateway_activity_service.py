from __future__ import annotations

from typing import Any, Dict, Optional

from server_modules import activity_ledger_service, agent_trace_service, secret_redaction_service


def _gateway_actor_id(gateway_id: str) -> str:
    token = str(gateway_id or "").strip()
    return f"gateway:{token}" if token else "gateway:unknown"


async def append_gateway_activity(
    registration: Dict[str, Any],
    *,
    action: str,
    title: str,
    summary: str,
    status: str = "logged",
    event_class: str = "system_activity",
    review_required: bool = False,
    payload: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    trace_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    gateway_id = str(registration.get("gateway_id") or "").strip()
    workspace_id = str(registration.get("workspace_id") or "").strip() or "default"
    tenant_id = str(registration.get("tenant_id") or "").strip() or "default"
    if not gateway_id:
        return None
    # Auto-redact secrets from payload before persisting to audit/activity log
    safe_payload = secret_redaction_service.sanitize_mapping(payload) if payload else None
    return await activity_ledger_service.append_activity_event(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor_type="system",
        actor_id=_gateway_actor_id(gateway_id),
        event_class=event_class,
        detail_level="timeline_detail",
        session_key=f"gateway:{gateway_id}",
        channel="gateway",
        direction="system",
        action=str(action or "").strip().lower() or "gateway_event",
        trace_id=str(trace_id or "").strip() or None,
        title=title,
        summary=summary,
        status=str(status or "logged").strip().lower() or "logged",
        review_required=bool(review_required),
        payload={
            "gateway_id": gateway_id,
            "device_id": str(registration.get("device_id") or "").strip() or None,
            **dict(safe_payload or {}),
        },
        metadata=dict(metadata or {}),
    )


async def emit_gateway_presence_activity(
    registration: Dict[str, Any],
    *,
    action: str,
    title: str,
    summary: str,
    status: str,
    payload: Optional[Dict[str, Any]] = None,
) -> None:
    await append_gateway_activity(
        registration,
        action=action,
        title=title,
        summary=summary,
        status=status,
        event_class="system_activity",
        payload=payload,
    )
