from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ChannelRoutingContext:
    tenant_id: str
    workspace_id: str
    channel_key: str
    endpoint_key: str
    actor_id: str
    actor_display_name: str
    session_key: str
    thread_id: str
    message: str
    owner_type: str
    install: Dict[str, Any]
    manifest: Any
    responder_install_id: Optional[str]
    responder_label: str
    runtime_mode: str
    runtime_profile_id: Optional[str]
    master_install_id: Optional[str]
    shared_metadata: Dict[str, Any]
    turn_request: Any
    execution_owner: Dict[str, Any]
    connector_id: Optional[str] = None
    deployed_agent: Optional[Dict[str, Any]] = None
    deployed_agent_id: Optional[str] = None
    deployed_agent_state: Optional[str] = None
    inbound_event: Optional[Dict[str, Any]] = None
    prior_messages: Optional[List[Dict[str, Any]]] = None
    business_plan: Optional[str] = None
    health_safety_context: Dict[str, Any] = field(default_factory=lambda: {"enabled": False})
    allow_master_fallback: bool = False


@dataclass
class ChannelExecutionResult:
    status: str
    reply: str
    run_id: Optional[str] = None
    artifact: Any = None
    steps: List[Any] = field(default_factory=list)
    critic: Any = None
    limit_reason: Optional[str] = None
    retry_after_seconds: Optional[int] = None
    quota_snapshot: Any = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    event_type: str = "response"
    incident_scope: Optional[str] = None
    incident_mode: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)
    event_metadata: Dict[str, Any] = field(default_factory=dict)
    health_safety: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None
    degraded_operations: List[Dict[str, Any]] = field(default_factory=list)
