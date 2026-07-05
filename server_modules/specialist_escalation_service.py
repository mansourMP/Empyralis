"""Specialist escalation (Phase 4).

Specialists never take operator/out-of-scope actions themselves. When a
specialist hits a decision outside its scope — an operator/fleet tool, or any
request it judges beyond its remit — it must ESCALATE to the operator (Sage) /
workspace owner rather than act.

An escalation is two durable signals, reusing existing primitives:
  1. an activity-ledger event (append_activity_event, event_class="fleet_control",
     action="escalation_requested", review_required=True) — the audit record;
  2. a notification to the owner (outbox_service.emit_notification_event) — the
     nudge that reaches the owner/Sage.

Both are best-effort: an escalation failing to record must never crash the
specialist's turn.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


async def escalate_out_of_scope(
    *,
    tenant_id: str,
    workspace_id: str,
    agent_install_id: str,
    agent_label: str = "",
    reason: str,
    detail: str = "",
    thread_id: Optional[str] = None,
    trace_id: str = "",
    channel: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Record an escalation from a specialist to the operator/owner.

    Returns a small dict describing what was emitted. Never raises.
    """
    label = str(agent_label or agent_install_id or "specialist").strip()
    reason_text = str(reason or "out-of-scope request").strip()
    title = f"Escalation from {label}"
    summary = (
        f"Specialist {label} ({agent_install_id}) hit a decision outside its scope "
        f"and escalated instead of acting: {reason_text}"
    )
    if detail:
        summary = f"{summary} — {detail}"

    emitted = {"ledger": False, "notification": False, "reason": reason_text}
    payload = {
        "escalation": True,
        "agent_install_id": agent_install_id,
        "agent_label": label,
        "reason": reason_text,
        "detail": detail,
        **(dict(metadata or {})),
    }

    # 1. Activity-ledger audit record (review_required flags it for the owner feed).
    try:
        from server_modules import activity_ledger_service

        await activity_ledger_service.append_activity_event(
            tenant_id=str(tenant_id or "system").strip() or "system",
            workspace_id=str(workspace_id or "unknown").strip() or "unknown",
            actor_type="agent",
            actor_id=str(agent_install_id or "").strip() or "specialist",
            install_id=str(agent_install_id or "").strip() or None,
            event_class="fleet_control",
            detail_level="timeline_detail",
            action="escalation_requested",
            title=title,
            summary=summary,
            status="escalated",
            review_required=True,
            thread_id=thread_id,
            trace_id=trace_id,
            channel=channel,
            metadata=payload,
        )
        emitted["ledger"] = True
    except Exception:
        logger.exception("specialist escalation: failed to append ledger event")

    # 2. Notification to the owner / operator (outbox → notification pipeline).
    try:
        from server_modules import outbox_service

        outbox_service.emit_notification_event(
            tenant_id=str(tenant_id or "system").strip() or "system",
            workspace_id=str(workspace_id or "unknown").strip() or "unknown",
            action="specialist_escalation",
            text=summary,
            trace_id=trace_id,
            metadata=payload,
        )
        emitted["notification"] = True
    except Exception:
        logger.exception("specialist escalation: failed to emit notification event")

    logger.warning("[specialist-escalation] %s", summary)
    return emitted
