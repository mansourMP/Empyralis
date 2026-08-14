"""
Canonical transparency event model for Empyralis agents.

Transparency events expose action traces and summarized reasoning to
users without revealing raw model chain-of-thought, private memory
content, or sensitive tool inputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Optional

from server_modules import secret_redaction_service

# ── Enums ──────────────────────────────────────────────────────────

Audience = Literal["owner", "admin", "operator", "customer", "system"]
TransparencyEventType = Literal[
    "user_message_received",
    "memory_loaded",
    "memory_excluded",
    "planning_started",
    "tool_selected",
    "tool_started",
    "tool_completed",
    "tool_failed",
    "browser_action",
    "approval_required",
    "approval_approved",
    "approval_denied",
    "gateway_action_started",
    "gateway_action_completed",
    "channel_message_sent",
    "channel_message_received",
    "policy_blocked",
    "turn_failed",
    "quota_blocked",
    "unsafe_url_blocked",
    "final_response_started",
    "final_response_sent",
    "skill_executed",
]

# ── Forbidden fields (raw chain-of-thought / LLM internals) ─────────

_FORBIDDEN_METADATA_KEYS = frozenset({
    "raw_chain_of_thought",
    "raw_cot",
    "model_internals",
    "internal_reasoning",
    "completion_tokens",
    "logprobs",
})


def _strip_forbidden_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    for key in _FORBIDDEN_METADATA_KEYS:
        metadata.pop(key, None)
    return metadata


# ── Event model ─────────────────────────────────────────────────────

@dataclass
class AgentTransparencyEvent:
    """One transparency event produced during an agent turn.

    The `metadata` dict is always redacted via secret_redaction_service
    and stripped of raw chain-of-thought keys before construction.
    """

    event_id: str
    trace_id: str
    workspace_id: str
    actor_type: Literal["sage", "studio_agent", "gateway", "system"]
    surface: Literal["chat", "channel", "gateway", "studio_test", "admin"]
    audience: Audience
    event_type: TransparencyEventType
    title: str
    summary: str
    status: Literal["running", "completed", "failed", "blocked", "denied"]
    timestamp: str

    agent_id: Optional[str] = None
    tool_name: Optional[str] = None
    channel: Optional[str] = None
    runtime_mode: Optional[str] = None
    memory_scope: Optional[str] = None
    approval_id: Optional[str] = None
    audit_event_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Always redact secrets
        self.metadata = secret_redaction_service.sanitize_mapping(dict(self.metadata))
        # Never allow raw chain-of-thought
        self.metadata = _strip_forbidden_metadata(self.metadata)

    # ── Payload helper ──────────────────────────────────────────

    def to_user_payload(self) -> Dict[str, Any]:
        """Return the user-facing fields for this event."""
        return {
            "event_id": self.event_id,
            "trace_id": self.trace_id,
            "event_type": self.event_type,
            "title": self.title,
            "status": self.status,
            "timestamp": self.timestamp,
            "summary": self.summary,
            "tool_name": self.tool_name,
            "channel": self.channel,
        }

    def to_customer_payload(self) -> Dict[str, Any]:
        """Minimal payload safe for customer-facing Studio agents."""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "title": "Agent is working…" if self.status == "running" else self.title,
            "status": self.status,
            "timestamp": self.timestamp,
        }
