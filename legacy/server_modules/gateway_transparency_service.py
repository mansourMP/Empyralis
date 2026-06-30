"""
Gateway transparency event emission.

Produces AgentTransparencyEvent records for real Gateway action stages:
tool execution, approval lifecycle, quota/kill-switch/URL blocks,
channel inbound/outbound events.

Never exposes raw tool arguments, private memory content, or model
internals.  Metadata is always redacted via secret_redaction_service.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from server_modules.agent_transparency_events import (
    AgentTransparencyEvent,
    Audience,
    VisibilityLevel,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


# ── public helpers ──────────────────────────────────────────────────

def emit_gateway_action_event(
    *,
    event_type: str,
    title: str,
    summary: str,
    status: str,
    trace_id: str,
    workspace_id: str,
    gateway_id: str = "",
    capability_id: str = "",
    run_id: str = "",
    audience: Audience = "owner",
    visibility_level: VisibilityLevel = "standard",
    metadata: Optional[Dict[str, Any]] = None,
) -> AgentTransparencyEvent:
    return AgentTransparencyEvent(
        event_id=f"gtevt-{uuid4().hex[:12]}",
        trace_id=trace_id,
        workspace_id=workspace_id,
        agent_id=gateway_id or None,
        actor_type="gateway",
        surface="gateway",
        audience=audience,
        visibility_level=visibility_level,
        event_type=event_type,  # type: ignore[arg-type]
        title=title,
        summary=summary,
        status=status,  # type: ignore[arg-type]
        timestamp=_now(),
        tool_name=capability_id or None,
        channel="gateway",
        runtime_mode="my_computer_agent" if gateway_id else None,
        metadata=dict(metadata or {}),
    )


def emit_approval_event(
    *,
    event_type: str,
    title: str,
    summary: str,
    status: str,
    trace_id: str,
    workspace_id: str,
    gateway_id: str = "",
    approval_id: str = "",
    audience: Audience = "owner",
    metadata: Optional[Dict[str, Any]] = None,
) -> AgentTransparencyEvent:
    return AgentTransparencyEvent(
        event_id=f"gtevt-{uuid4().hex[:12]}",
        trace_id=trace_id,
        workspace_id=workspace_id,
        agent_id=gateway_id or None,
        actor_type="gateway",
        surface="gateway",
        audience=audience,
        visibility_level="standard" if event_type != "approval_required" else "off",
        event_type=event_type,  # type: ignore[arg-type]
        title=title,
        summary=summary,
        status=status,  # type: ignore[arg-type]
        timestamp=_now(),
        channel="gateway",
        approval_id=approval_id or None,
        metadata=dict(metadata or {}),
    )


def emit_safety_block_event(
    *,
    event_type: str,
    title: str,
    summary: str,
    trace_id: str,
    workspace_id: str,
    gateway_id: str = "",
    audience: Audience = "owner",
    metadata: Optional[Dict[str, Any]] = None,
) -> AgentTransparencyEvent:
    return AgentTransparencyEvent(
        event_id=f"gtevt-{uuid4().hex[:12]}",
        trace_id=trace_id,
        workspace_id=workspace_id,
        agent_id=gateway_id or None,
        actor_type="gateway",
        surface="gateway",
        audience=audience,
        visibility_level="standard",
        event_type=event_type,  # type: ignore[arg-type]
        title=title,
        summary=summary,
        status="blocked",
        timestamp=_now(),
        channel="gateway",
        metadata=dict(metadata or {}),
    )


def emit_channel_event(
    *,
    event_type: str,
    title: str,
    summary: str,
    status: str,
    trace_id: str,
    workspace_id: str,
    gateway_id: str = "",
    channel: str = "",
    audience: Audience = "owner",
    metadata: Optional[Dict[str, Any]] = None,
) -> AgentTransparencyEvent:
    return AgentTransparencyEvent(
        event_id=f"gtevt-{uuid4().hex[:12]}",
        trace_id=trace_id,
        workspace_id=workspace_id,
        agent_id=gateway_id or None,
        actor_type="gateway",
        surface="channel" if channel else "gateway",
        audience=audience,
        visibility_level="minimal",
        event_type=event_type,  # type: ignore[arg-type]
        title=title,
        summary=summary,
        status=status,  # type: ignore[arg-type]
        timestamp=_now(),
        channel=channel or None,
        metadata=dict(metadata or {}),
    )
