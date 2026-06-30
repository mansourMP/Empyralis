"""
Canonical dataclasses for Empyralis channel events, safety context,
health payloads, and capability contracts.

Used by personal_channels_service, channel_lane_contract_service,
gateway_health_service, and the handler registry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Optional


@dataclass(frozen=True)
class ChannelSafetyContext:
    """Safety-gate results for a single channel event."""

    kill_switch_checked: bool = True
    quota_checked: bool = True
    approval_status: str = "none"  # "none" | "auto" | "pending" | "approved" | "denied"
    control_command_blocked: bool = False
    content_guard_passed: bool = True
    dedupe_key: str = ""
    trace_id: str = ""


@dataclass(frozen=True)
class CanonicalChannelEvent:
    """Normalized representation of one inbound or outbound channel message."""

    gateway_id: str
    channel_key: str
    external_message_id: str
    remote_jid: str
    text: str
    direction: Literal["inbound", "outbound"]
    sender_jid: Optional[str] = None
    push_name: Optional[str] = None
    reply_idempotency_key: Optional[str] = None
    processed_at: Optional[str] = None
    trace_id: str = ""
    safety_context: ChannelSafetyContext = field(default_factory=ChannelSafetyContext)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class ChannelHealthPayload:
    """Single health-check entry for a gateway channel."""

    id: str
    status: str  # "pass" | "warn" | "fail"
    summary: str
    detail: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class ChannelCapabilityContract:
    """Runtime contract for a channel from PERSONAL_CHANNEL_SPECS."""

    channel_key: str
    provider: str
    runtime_lane: str
    memory_surface: str
    stage: str  # "live" | "next" | "later"
    live_capable: bool
