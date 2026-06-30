"""
Canonical transparency event model for Empyralis agents.

Transparency events expose action traces and summarized reasoning to
users, admins, and enterprise auditors without revealing raw model
chain-of-thought, private memory content, or sensitive tool inputs.

Visibility levels control what each audience can see:
  off       — only the final answer and required approval prompts
  minimal   — simple status indicators (checking memory, using tool...)
  standard  — tool / channel names, safety decisions, trace_id
  full      — step timeline, summarized inputs/outputs, policy decisions
  enterprise — full audit trail with runtime session and quota data

Audiences:  owner, admin, operator, customer, system
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Optional

from server_modules import secret_redaction_service

# ── Enums ──────────────────────────────────────────────────────────

VisibilityLevel = Literal["off", "minimal", "standard", "full", "enterprise"]
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


# ── Visibility rules per event type ─────────────────────────────────

_EVENT_MINIMUM_VISIBILITY: dict[TransparencyEventType, VisibilityLevel] = {
    "user_message_received": "minimal",
    "memory_loaded": "standard",
    "memory_excluded": "standard",
    "planning_started": "standard",
    "tool_selected": "standard",
    "tool_started": "standard",
    "tool_completed": "standard",
    "tool_failed": "standard",
    "browser_action": "standard",
    "approval_required": "off",
    "approval_approved": "minimal",
    "approval_denied": "minimal",
    "gateway_action_started": "standard",
    "gateway_action_completed": "standard",
    "channel_message_sent": "minimal",
    "channel_message_received": "minimal",
    "policy_blocked": "standard",
    "quota_blocked": "standard",
    "unsafe_url_blocked": "standard",
    "final_response_started": "minimal",
    "final_response_sent": "minimal",
    "skill_executed": "standard",
}


def minimum_visibility_for_event(event_type: TransparencyEventType) -> VisibilityLevel:
    return _EVENT_MINIMUM_VISIBILITY.get(event_type, "standard")


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
    visibility_level: VisibilityLevel
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

    # ── Visibility helpers ──────────────────────────────────────

    @property
    def effective_visibility(self) -> VisibilityLevel:
        """The actual visibility of this event, respecting its event_type floor."""
        levels: list[VisibilityLevel] = ["off", "minimal", "standard", "full", "enterprise"]
        floor = minimum_visibility_for_event(self.event_type)
        requested = levels.index(self.visibility_level)
        floor_idx = levels.index(floor)
        return levels[max(requested, floor_idx)]

    def is_visible_to(self, audience: Audience) -> bool:
        if audience in ("owner", "admin", "operator"):
            return True
        if audience == "customer":
            return self.effective_visibility in ("off", "minimal")
        if audience == "system":
            return True
        return False

    def to_user_payload(self) -> Dict[str, Any]:
        """Return the subset of fields visible at the current visibility_level."""
        payload: Dict[str, Any] = {
            "event_id": self.event_id,
            "trace_id": self.trace_id,
            "event_type": self.event_type,
            "title": self.title,
            "status": self.status,
            "timestamp": self.timestamp,
        }
        level = self.effective_visibility
        if level in ("standard", "full", "enterprise"):
            payload["summary"] = self.summary
            payload["tool_name"] = self.tool_name
            payload["channel"] = self.channel
        if level in ("full", "enterprise"):
            payload["runtime_mode"] = self.runtime_mode
            payload["memory_scope"] = self.memory_scope
            payload["approval_id"] = self.approval_id
            payload["metadata"] = self.metadata
        if level == "enterprise":
            payload["audit_event_id"] = self.audit_event_id
            payload["workspace_id"] = self.workspace_id
            payload["agent_id"] = self.agent_id
        return payload

    def to_customer_payload(self) -> Dict[str, Any]:
        """Minimal payload safe for customer-facing Studio agents."""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "title": "Agent is working…" if self.status == "running" else self.title,
            "status": self.status,
            "timestamp": self.timestamp,
        }
