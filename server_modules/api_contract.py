from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from server_modules.agent_turn import (
    AgentTurnRequest,
    AgentTurnResponse,
    build_agent_turn_request,
    serialize_turn_attachment,
    serialize_agent_turn_request,
)


class ApiTurnActor(BaseModel):
    type: str = "user"
    id: str = ""
    display_name: str = ""


class ApiTurnAttachment(BaseModel):
    kind: str = "file"
    uri: str
    name: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ApiAgentTurnRequest(BaseModel):
    tenant_id: str = "default"
    workspace_id: str = "default"
    thread_id: Optional[str] = None
    session_id: str
    channel: str = "web"
    actor: ApiTurnActor
    message: str
    provider: Optional[str] = None
    model: Optional[str] = None
    reasoning_effort: Optional[str] = None
    attachments: List[ApiTurnAttachment] = Field(default_factory=list)
    context_hints: Dict[str, Any] = Field(default_factory=dict)
    execution_mode: Literal["sync", "durable"] = "sync"
    response_mode: Literal["stream", "artifact", "channel_reply"] = "stream"
    machine_target: Optional[str] = None
    policy_context: Dict[str, Any] = Field(default_factory=dict)


class ApiAgentTurnResponse(BaseModel):
    ok: bool = True
    status: str
    reply: str = ""
    run_id: Optional[str] = None
    thread_id: Optional[str] = None
    session_id: Optional[str] = None
    artifacts: List[Dict[str, Any]] = Field(default_factory=list)
    approvals: List[Dict[str, Any]] = Field(default_factory=list)
    interventions: List[Dict[str, Any]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ApiRunListItem(BaseModel):
    run_id: Optional[str] = None
    engine: Optional[str] = None
    status: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    completed_at: Optional[str] = None
    workspace_id: Optional[str] = None
    owner_user_id: Optional[str] = None
    owner_email: Optional[str] = None
    workflow_id: Optional[str] = None
    pack_id: Optional[str] = None
    agent_role: Optional[str] = None
    parent_run_id: Optional[str] = None
    execution_target_selected: Optional[str] = None
    result_summary: Optional[str] = None
    source: Optional[Literal["live", "history"]] = None
    raw: Dict[str, Any] = Field(default_factory=dict)


class ApiRunListResponse(BaseModel):
    items: List[Dict[str, Any]] = Field(default_factory=list)
    count: int = 0
    total: int = 0
    limit: int = 50
    offset: int = 0
    next_offset: Optional[int] = None


class ApiNotificationItem(BaseModel):
    id: Optional[str] = None
    ts: Optional[str] = None
    channel: Optional[str] = None
    direction: Optional[str] = None
    event_type: Optional[str] = None
    workspace_id: Optional[str] = None
    session_key: Optional[str] = None
    session_id: Optional[str] = None
    message_id: Optional[str] = None
    parent_id: Optional[str] = None
    run_id: Optional[str] = None
    trace_id: Optional[str] = None
    action: Optional[str] = None
    text: Optional[str] = None
    read_at: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ApiNotificationListResponse(BaseModel):
    items: List[Dict[str, Any]] = Field(default_factory=list)
    count: int = 0
    total: int = 0
    sessions: List[Dict[str, Any]] = Field(default_factory=list)
    session_count: int = 0
    stream: bool = False


class ApiActivityListResponse(BaseModel):
    items: List[Dict[str, Any]] = Field(default_factory=list)
    count: int = 0
    total: int = 0
    summary: Dict[str, Any] = Field(default_factory=dict)


class ApiNotificationReadRequest(BaseModel):
    notification_ids: List[str] = Field(default_factory=list)
    workspace_id: Optional[str] = None
    mark_all: bool = False


class ApiNotificationReadResponse(BaseModel):
    status: str = "ok"
    marked_count: int = 0
    marked_ids: List[str] = Field(default_factory=list)


class ApiArtifactListResponse(BaseModel):
    ok: bool = True
    workspace_id: str = "default"
    updated_at: Optional[str] = None
    summary: Dict[str, Any] = Field(default_factory=dict)
    items: List[Dict[str, Any]] = Field(default_factory=list)


class ApiArtifactPreviewResponse(BaseModel):
    ok: bool = True
    path: str
    kind: str
    media_type: str
    byte_size: Optional[int] = None
    text_preview: Optional[str] = None
    file_url: Optional[str] = None
    download_url: Optional[str] = None
    file_name: Optional[str] = None
    note: Optional[str] = None


class ApiSessionRequest(BaseModel):
    tenant_id: str = "default"
    workspace_id: str = "default"
    channel: str = "web"
    actor: ApiTurnActor = Field(default_factory=ApiTurnActor)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    session_id: Optional[str] = None


class ApiSessionResponse(BaseModel):
    ok: bool = True
    session_id: str
    workspace_id: str = "default"
    tenant_id: str = "default"
    channel: str = "web"
    actor: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[str] = None
    expires_at: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    status: str = "active"


class ApiThreadTurnRecord(BaseModel):
    id: str
    tenant_id: Optional[str] = None
    workspace_id: Optional[str] = None
    thread_id: str
    session_id: Optional[str] = None
    request_id: Optional[str] = None
    role: str
    status: Optional[str] = None
    content: str = ""
    run_id: Optional[str] = None
    actor: Dict[str, Any] = Field(default_factory=dict)
    approvals: List[Dict[str, Any]] = Field(default_factory=list)
    interventions: List[Dict[str, Any]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ApiThreadTurnCreateRequest(BaseModel):
    tenant_id: str = "default"
    workspace_id: str = "default"
    session_id: Optional[str] = None
    channel: str = "web"
    actor: ApiTurnActor = Field(default_factory=ApiTurnActor)
    content: str
    runtime_profile_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    request_id: Optional[str] = None
    client_request_id: Optional[str] = None
    attachments: List[Dict[str, Any]] = Field(default_factory=list)


class ApiThreadRecord(BaseModel):
    id: str
    tenant_id: Optional[str] = None
    workspace_id: Optional[str] = None
    owner_user_id: Optional[str] = None
    master_agent_install_id: Optional[str] = None
    channel: Optional[str] = None
    title: str
    status: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    last_turn_at: Optional[str] = None
    turns: List[Dict[str, Any]] = Field(default_factory=list)


class ApiThreadListResponse(BaseModel):
    items: List[Dict[str, Any]] = Field(default_factory=list)
    count: int = 0
    workspace_id: str = "default"
    tenant_id: Optional[str] = None


class ApiMachineListResponse(BaseModel):
    items: List[Dict[str, Any]] = Field(default_factory=list)
    runtimes: List[Dict[str, Any]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ApiConnectorListResponse(BaseModel):
    items: List[Dict[str, Any]] = Field(default_factory=list)
    connectors: List[Dict[str, Any]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ApiHealthResponse(BaseModel):
    ok: bool = False
    status: Optional[str] = None
    detail: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


CANONICAL_API_ENDPOINTS: Dict[str, Dict[str, Any]] = {
    "turn": {
        "method": "POST",
        "path": "/turn",
        "request_model": "ApiAgentTurnRequest",
        "response_model": "ApiAgentTurnResponse",
        "notes": "Canonical turn entry for web, mobile, and desktop shells.",
    },
    "runs_list": {
        "method": "GET",
        "path": "/runs",
        "response_model": "ApiRunListResponse",
        "notes": "Canonical run listing endpoint; /runs/{run_id} remains the canonical detail endpoint.",
    },
    "runs_detail": {
        "method": "GET",
        "path": "/runs/{run_id}",
        "response_model": "dict",
        "notes": "Canonical durable run detail endpoint.",
    },
    "notifications": {
        "method": "GET/POST",
        "path": "/notifications",
        "response_model": "ApiNotificationListResponse",
        "notes": "Canonical notification feed. `stream=true` returns SSE. POST marks notifications as read.",
    },
    "approvals_list": {
        "method": "GET",
        "path": "/approvals",
        "response_model": "ApiApprovalListResponse",
        "notes": "Canonical approval queue endpoint.",
    },
    "approvals_resolve": {
        "method": "POST",
        "path": "/approvals/{approval_id}/resolve",
        "request_model": "ApiApprovalResolveRequest",
        "response_model": "ApiApprovalResolveResponse",
        "notes": "Canonical approval resolution endpoint with durable approval audit and outbox emission.",
    },
    "artifacts_list": {
        "method": "GET",
        "path": "/artifacts",
        "response_model": "ApiArtifactListResponse",
        "notes": "Canonical artifact listing endpoint; legacy alias /artifacts/workspace remains supported.",
    },
    "artifacts_preview": {
        "method": "GET",
        "path": "/artifacts/preview",
        "response_model": "ApiArtifactPreviewResponse",
        "notes": "Canonical artifact preview endpoint.",
    },
    "artifacts_content": {
        "method": "GET",
        "path": "/artifacts/content",
        "response_model": "binary",
        "notes": "Canonical artifact download endpoint; legacy alias /artifacts/file remains supported.",
    },
    "sessions": {
        "method": "POST/GET/DELETE",
        "path": "/sessions and /sessions/{session_id}",
        "request_model": "ApiSessionRequest",
        "response_model": "ApiSessionResponse",
        "notes": "Canonical runtime session bootstrap and lookup surface.",
    },
    "threads": {
        "method": "GET",
        "path": "/threads and /threads/{thread_id}",
        "response_model": "ApiThreadListResponse / ApiThreadRecord",
        "notes": "Canonical durable master-thread history surface.",
    },
    "machines": {
        "method": "GET",
        "path": "/machines",
        "response_model": "ApiMachineListResponse",
        "notes": "Canonical machine and runtime status surface.",
    },
    "connectors": {
        "method": "GET",
        "path": "/connectors",
        "response_model": "ApiConnectorListResponse",
        "notes": "Canonical connector inventory surface.",
    },
    "health": {
        "method": "GET",
        "path": "/health",
        "response_model": "ApiHealthResponse",
        "notes": "Canonical runtime health probe.",
    },
}


def model_to_dict(model: Any) -> Dict[str, Any]:
    if is_dataclass(model):
        return dict(asdict(model))
    if hasattr(model, "model_dump"):
        return dict(model.model_dump())
    if hasattr(model, "dict"):
        return dict(model.dict())
    return dict(model or {})


def request_body_to_turn_request(body: ApiAgentTurnRequest | AgentTurnRequest | Dict[str, Any]) -> AgentTurnRequest:
    if isinstance(body, AgentTurnRequest):
        return body
    if isinstance(body, ApiAgentTurnRequest):
        payload = model_to_dict(body)
    else:
        payload = dict(body or {})
    return build_agent_turn_request(payload)


def build_turn_chat_body(turn_request: AgentTurnRequest) -> Dict[str, Any]:
    hints = dict(turn_request.context_hints or {})
    return {
        "workspace_id": turn_request.workspace_id,
        "thread_id": turn_request.thread_id or turn_request.session_id,
        "session_id": turn_request.session_id,
        "channel": turn_request.channel,
        "message": turn_request.message,
        "attachments": [serialize_turn_attachment(item) for item in turn_request.attachments],
        "machine_target": turn_request.machine_target,
        "policy_context": dict(turn_request.policy_context or {}),
        "provider": hints.get("provider"),
        "model": hints.get("model"),
        "reasoning_effort": hints.get("reasoning_effort"),
        "approved_action": hints.get("approved_action") if isinstance(hints.get("approved_action"), dict) else None,
        "prior_messages": hints.get("prior_messages") if isinstance(hints.get("prior_messages"), list) else [],
        "max_iterations": hints.get("max_iterations"),
        "metadata": hints.get("metadata") if isinstance(hints.get("metadata"), dict) else {},
    }


def normalize_agent_turn_result(
    result: Dict[str, Any],
    *,
    turn_request: AgentTurnRequest,
) -> ApiAgentTurnResponse:
    result_metadata = dict(result.get("metadata") or {})
    nested = result.get("result") if isinstance(result.get("result"), dict) else {}
    nested_metadata = dict(nested.get("metadata") or {})
    trace_id = (
        str(result_metadata.get("trace_id") or "").strip()
        or str(nested_metadata.get("trace_id") or "").strip()
        or str(result.get("trace_id") or "").strip()
        or None
    )
    kind = str(result.get("kind") or "").strip()
    if kind == "durable_run":
        durable = result.get("result") if isinstance(result.get("result"), dict) else {}
        metadata = {
            "kind": "durable_run",
            "engine": durable.get("engine"),
            "route": durable.get("route"),
            "doctor_preflight": durable.get("doctor_preflight"),
            "created_run": durable.get("created_run"),
            "turn_request": serialize_agent_turn_request(turn_request),
        }
        if trace_id:
            metadata["trace_id"] = trace_id
        return ApiAgentTurnResponse(
            status=str(durable.get("status") or "accepted"),
            run_id=str(durable.get("run_id") or "").strip() or None,
            thread_id=turn_request.thread_id,
            session_id=turn_request.session_id,
            metadata=metadata,
        )
    if kind == "direct_chat_stream":
        metadata = {
            "kind": "direct_chat_stream",
            "workspace_id": result.get("workspace_id"),
            "session_key": result.get("session_key"),
            "thread_id": result.get("thread_id"),
            "client_request_id": result.get("client_request_id"),
            "turn_request": serialize_agent_turn_request(turn_request),
        }
        if trace_id:
            metadata["trace_id"] = trace_id
        return ApiAgentTurnResponse(
            status="stream_ready",
            thread_id=str(result.get("thread_id") or turn_request.thread_id or turn_request.session_id or "").strip() or None,
            session_id=str(result.get("session_id") or turn_request.session_id or "").strip() or None,
            metadata=metadata,
        )
    normalized = AgentTurnResponse(
        status=str(result.get("status") or "ok"),
        reply=str(result.get("reply") or ""),
        run_id=str(result.get("run_id") or "").strip() or None,
        thread_id=str(result.get("thread_id") or turn_request.thread_id or turn_request.session_id or "").strip() or None,
        session_id=str(result.get("session_id") or turn_request.session_id or "").strip() or None,
        artifacts=list(result.get("artifacts") or []),
        interventions=list(result.get("interventions") or []),
        metadata={
            **result_metadata,
            **({"trace_id": trace_id} if trace_id else {}),
        },
    )
    return ApiAgentTurnResponse(**model_to_dict(normalized))


def normalize_session_record(record: Dict[str, Any]) -> ApiSessionResponse:
    payload = dict(record or {})
    return ApiSessionResponse(
        session_id=str(payload.get("session_id") or "").strip(),
        workspace_id=str(payload.get("workspace_id") or "default").strip() or "default",
        tenant_id=str(payload.get("tenant_id") or "default").strip() or "default",
        channel=str(payload.get("channel") or "web").strip() or "web",
        actor=dict(payload.get("actor") or {}),
        created_at=str(payload.get("created_at") or "").strip() or None,
        expires_at=str(payload.get("expires_at") or "").strip() or None,
        metadata=dict(payload.get("metadata") or {}),
        status=str(payload.get("status") or "active").strip() or "active",
    )
