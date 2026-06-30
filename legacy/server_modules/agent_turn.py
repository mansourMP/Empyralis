"""
Canonical turn contract for the platform.

Prompt 01 freezes this module as the owner of `AgentTurnRequest` and the request
builders that normalize user-facing and system-facing work into one turn shape
before handing execution to `turn_runtime`.

Connectors, route handlers, and UI surfaces should converge on these builders
instead of inventing parallel turn contracts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import re
from typing import Any, Dict, List, Literal, Optional, Protocol

from server_modules import agent_trace_service
from server_modules import agent_registry_repository
from server_modules import deployed_agent_virtual_runtime_service
from server_modules import healthguide_safety_service
from server_modules import session_service
from server_modules import thread_service
from server_modules import transcript_events_service
from server_modules.telemetry import get_tracer, set_span_attributes


ExecutionMode = Literal["sync", "durable"]
ResponseMode = Literal["stream", "artifact", "channel_reply"]
SessionMode = Literal["copilot", "agent"]

LOGGER = logging.getLogger(__name__)
VALID_SESSION_MODES = {"copilot", "agent"}
LIGHTWEIGHT_DIRECT_CHAT_PREFIXES = (
    "what ",
    "what's ",
    "whats ",
    "why ",
    "how ",
    "who ",
    "when ",
    "where ",
    "which ",
    "is ",
    "are ",
    "can ",
    "could ",
    "would ",
    "will ",
    "should ",
    "do ",
    "does ",
    "did ",
    "tell me ",
    "explain ",
)
SERIOUS_TASK_MARKERS = (
    "research",
    "draft",
    "prepare",
    "create",
    "generate",
    "build",
    "write",
    "review",
    "summarize",
    "summarise",
    "analyze",
    "analyse",
    "investigate",
    "inspect",
    "check",
    "organize",
    "compile",
    "open",
    "run",
    "send",
)
SERIOUS_SEQUENCE_MARKERS = (
    " and ",
    " then ",
    " after ",
    " before ",
)
SERIOUS_OUTCOME_MARKERS = (
    " summary",
    " report",
    " plan",
    " draft",
    " checklist",
    " artifact",
    " file",
    " slides",
    " presentation",
    " email",
    " notes",
)
SERVER_OWNED_DIRECT_CHAT_CHANNELS = {"web", "mobile"}
MASTER_DIRECT_CHAT_AGENT_IDS = {"assistant", "sage"}


@dataclass(slots=True)
class TurnActor:
    type: str
    id: str = ""
    display_name: str = ""


@dataclass(slots=True)
class TurnAttachment:
    kind: str
    uri: str
    name: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentTurnRequest:
    tenant_id: str
    workspace_id: str
    thread_id: str
    session_id: str
    channel: str
    actor: TurnActor
    message: str
    attachments: List[TurnAttachment] = field(default_factory=list)
    context_hints: Dict[str, Any] = field(default_factory=dict)
    execution_mode: ExecutionMode = "sync"
    response_mode: ResponseMode = "stream"
    machine_target: Optional[str] = None
    policy_context: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentTurnResponse:
    status: str
    reply: str = ""
    run_id: Optional[str] = None
    thread_id: Optional[str] = None
    session_id: Optional[str] = None
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    approvals: List[Dict[str, Any]] = field(default_factory=list)
    interventions: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DirectChatTurnResolution:
    turn_request: AgentTurnRequest
    workspace_id: str
    thread_id: str
    client_request_id: str
    message: str


@dataclass(slots=True)
class RunStartTurnResolution:
    request: Any
    turn_request: AgentTurnRequest


class AgentTurnRuntime(Protocol):
    def handle_turn(self, request: AgentTurnRequest) -> AgentTurnResponse: ...


def normalize_channel(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text or "web"


def normalize_session_mode(value: Any) -> SessionMode:
    text = str(value or "").strip().lower()
    return text if text in VALID_SESSION_MODES else "copilot"


def _metadata_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


async def _bind_cloud_runtime_session_if_needed(
    *,
    turn_request: AgentTurnRequest,
    session_record: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not isinstance(session_record, dict):
        return session_record
    context_hints = dict(turn_request.context_hints or {})
    turn_metadata = _metadata_dict(context_hints.get("metadata"))
    session_metadata = _metadata_dict(session_record.get("metadata"))
    binding = await deployed_agent_virtual_runtime_service.ensure_runtime_session_binding(
        deployed_agent_id=turn_metadata.get("deployed_agent_id") or session_metadata.get("deployed_agent_id"),
        tenant_id=turn_request.tenant_id,
        workspace_id=turn_request.workspace_id,
        session_id=session_record.get("session_id") or turn_request.session_id,
        thread_id=turn_request.thread_id,
        channel=turn_request.channel,
        actor=serialize_turn_actor(turn_request.actor),
        session_metadata=session_metadata,
        turn_metadata=turn_metadata,
        machine_target=turn_request.machine_target,
    )
    if not isinstance(binding, dict):
        return session_record
    metadata_updates = _metadata_dict(binding.get("metadata_updates"))
    if not metadata_updates:
        return session_record
    merged_session_metadata = dict(session_metadata)
    merged_session_metadata.update(metadata_updates)
    merged_turn_metadata = dict(turn_metadata)
    merged_turn_metadata.update(metadata_updates)
    session_record["metadata"] = merged_session_metadata
    context_hints["metadata"] = merged_turn_metadata
    context_hints["session"] = dict(session_record)
    turn_request.context_hints = context_hints
    runtime_session_id = str(metadata_updates.get("runtime_session_id") or "").strip()
    if runtime_session_id and str(turn_request.machine_target or "").strip().lower() not in {"cloud", "virtual"}:
        turn_request.machine_target = "virtual"
    await session_service.extend_session(
        str(session_record.get("session_id") or turn_request.session_id).strip(),
        metadata_updates=metadata_updates,
    )
    return session_record


def _trace_root_agent_id(binding_metadata: Dict[str, Any]) -> str:
    install_id = (
        str(binding_metadata.get("active_agent_install_id") or "").strip()
        or str(binding_metadata.get("master_agent_install_id") or "").strip()
        or str(binding_metadata.get("workspace_agent_install_id") or "").strip()
    )
    if install_id:
        return f"specialist:{install_id}"
    return "sage"


def _trace_surface(channel: Any) -> str:
    normalized = normalize_channel(channel)
    if normalized in {"web", "mobile", "desktop", "api"}:
        return normalized
    return "channel"


def _trace_runtime_target(turn_request: AgentTurnRequest) -> str:
    hints = _metadata_dict(turn_request.context_hints.get("metadata"))
    return (
        str(turn_request.machine_target or "").strip()
        or str(turn_request.policy_context.get("execution_target") or "").strip()
        or str(hints.get("execution_target_selected") or "").strip()
        or str(hints.get("execution_target") or "").strip()
        or "cloud"
    )


def _trace_provider(turn_request: AgentTurnRequest) -> str:
    return str(turn_request.context_hints.get("provider") or "").strip()


def _trace_model(turn_request: AgentTurnRequest) -> str:
    return str(turn_request.context_hints.get("model") or "").strip()


def _trace_routing_reason(turn_request: AgentTurnRequest) -> str:
    metadata = _metadata_dict(turn_request.context_hints.get("metadata"))
    return (
        str(metadata.get("primary_engine_reason") or "").strip()
        or str(metadata.get("routing_reason") or "").strip()
        or (
            "execution_mode:durable"
            if str(turn_request.execution_mode or "").strip().lower() == "durable"
            else "execution_mode:sync"
        )
    )


def _result_trace_id(result: Dict[str, Any]) -> Optional[str]:
    if not isinstance(result, dict):
        return None
    metadata = _metadata_dict(result.get("metadata"))
    trace_id = str(metadata.get("trace_id") or result.get("trace_id") or "").strip()
    if trace_id:
        return trace_id
    nested = result.get("result") if isinstance(result.get("result"), dict) else {}
    nested_metadata = _metadata_dict(nested.get("metadata"))
    nested_trace_id = str(nested_metadata.get("trace_id") or "").strip()
    return nested_trace_id or None


def _bind_trace_id_to_turn_result(result: Any, trace_id: Optional[str]) -> Any:
    if not trace_id or not isinstance(result, dict):
        return result
    payload = dict(result)
    metadata = _metadata_dict(payload.get("metadata"))
    metadata["trace_id"] = trace_id
    payload["metadata"] = metadata
    payload["trace_id"] = trace_id
    nested = payload.get("result") if isinstance(payload.get("result"), dict) else None
    if isinstance(nested, dict):
        nested_payload = dict(nested)
        nested_metadata = _metadata_dict(nested_payload.get("metadata"))
        nested_metadata["trace_id"] = trace_id
        nested_payload["metadata"] = nested_metadata
        payload["result"] = nested_payload
    return payload


def _should_persist_direct_chat_result(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    metadata = _metadata_dict(result.get("metadata"))
    kind = str(result.get("kind") or metadata.get("kind") or "").strip().lower()
    if kind != "direct_chat_stream":
        return True
    reply = str(result.get("reply") or "").strip()
    interventions = result.get("interventions") if isinstance(result.get("interventions"), list) else []
    return bool(reply or interventions)


_THINKING_METADATA_ALLOWLIST = {"reasoning_effort", "reasoning_tokens", "supports_reasoning"}
_THINKING_METADATA_BLOCKLIST = {
    "thinking",
    "thinking_text",
    "reasoning",
    "reasoning_text",
    "reasoning_summary",
    "reasoning_output",
    "chain_of_thought",
    "raw_chain_of_thought",
    "internal_reasoning",
    "private_reasoning",
}


def _is_thinking_metadata_key(raw_key: Any) -> bool:
    key = str(raw_key or "").strip().lower()
    if not key:
        return False
    if key in _THINKING_METADATA_ALLOWLIST:
        return False
    if key in _THINKING_METADATA_BLOCKLIST:
        return True
    if "chain_of_thought" in key or "raw_chain_of_thought" in key or "private_reasoning" in key:
        return True
    if key in {"thought", "thoughts"}:
        return True
    if "thinking" in key and not key.endswith("_mode"):
        return True
    return False


def _strip_thinking_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: Dict[str, Any] = {}
        for key, nested_value in value.items():
            if _is_thinking_metadata_key(key):
                continue
            sanitized[str(key)] = _strip_thinking_metadata(nested_value)
        return sanitized
    if isinstance(value, list):
        return [_strip_thinking_metadata(item) for item in value]
    return value


def _assistant_turn_metadata_from_result(
    result: Dict[str, Any],
    *,
    request_id: Optional[str],
) -> Dict[str, Any]:
    metadata = _strip_thinking_metadata(_metadata_dict(result.get("metadata")))
    assistant_metadata: Dict[str, Any] = {
        "request_id": request_id,
        "result_metadata": metadata,
    }
    trace_id = _result_trace_id(result)
    if trace_id:
        assistant_metadata["trace_id"] = trace_id
    stream_events = result.get("stream_events") if isinstance(result.get("stream_events"), list) else []
    transcript_events = transcript_events_service.transcript_events_from_stream_events(stream_events)
    if transcript_events:
        assistant_metadata["transcript_events"] = transcript_events
    return assistant_metadata


def _direct_chat_request_metadata(body: Dict[str, Any]) -> Dict[str, Any]:
    context_hints = body.get("context_hints") if isinstance(body.get("context_hints"), dict) else {}
    metadata = context_hints.get("metadata") if isinstance(context_hints.get("metadata"), dict) else {}
    if metadata:
        return dict(metadata)
    return _metadata_dict(body.get("metadata"))


def _current_user_is_owner(current_user: Any) -> bool:
    if not isinstance(current_user, dict):
        return False
    if bool(current_user.get("is_admin")):
        return True
    auth_type = str(current_user.get("auth_type") or "").strip().lower()
    if auth_type == "api_key":
        return True
    return str(current_user.get("role") or "").strip().lower() == "owner"


def normalize_turn_policy_context(
    policy_context: Optional[Dict[str, Any]],
    *,
    current_user: Any,
    workspace_id: str = "",
    session_id: str = "",
    logger: Optional[logging.Logger] = None,
) -> Dict[str, Any]:
    normalized = _metadata_dict(policy_context)
    if "session_mode" not in normalized:
        if "runtime_trust_zone" in normalized or "elevated_mode" in normalized or "elevated" in normalized:
            from server_modules import runtime_policy as runtime_policy_module

            execution_target = str(normalized.get("execution_target") or "").strip()
            normalized["runtime_trust_zone"] = runtime_policy_module.normalize_runtime_trust_zone(
                normalized.get("runtime_trust_zone"),
                target=execution_target,
            )
            if "elevated_mode" in normalized:
                normalized["elevated_mode"] = runtime_policy_module.normalize_elevated_mode(
                    normalized.get("elevated_mode"),
                    default=runtime_policy_module.ELEVATED_MODE_ASK,
                )
            if "elevated" in normalized:
                raw_elevated = normalized.get("elevated")
                if isinstance(raw_elevated, str):
                    normalized["elevated"] = runtime_policy_module.normalize_elevated_mode(
                        raw_elevated,
                        default=runtime_policy_module.ELEVATED_MODE_ASK,
                    )
                elif isinstance(raw_elevated, dict):
                    next_elevated = dict(raw_elevated)
                    next_elevated["mode"] = runtime_policy_module.normalize_elevated_mode(
                        next_elevated.get("mode"),
                        default=runtime_policy_module.ELEVATED_MODE_ASK,
                    )
                    next_elevated["runtime_trust_zone"] = runtime_policy_module.normalize_runtime_trust_zone(
                        next_elevated.get("runtime_trust_zone") or normalized.get("runtime_trust_zone"),
                        target=execution_target,
                    )
                    normalized["elevated"] = next_elevated
        return {key: value for key, value in normalized.items() if value not in (None, "", [], {})}

    requested_session_mode = normalize_session_mode(normalized.get("session_mode"))
    user_is_owner = _current_user_is_owner(current_user)
    current_user_id = str((current_user or {}).get("user_id") or "").strip()
    effective_session_mode = requested_session_mode
    downgrade_reason = ""

    full_trust_allowed = False
    if user_is_owner:
        try:
            from server_modules.runtime_config import agent_machine_full_trust_enabled

            full_trust_allowed = bool(agent_machine_full_trust_enabled(current_user_id))
        except Exception:
            full_trust_allowed = False

    if requested_session_mode == "agent":
        if not user_is_owner:
            effective_session_mode = "copilot"
            downgrade_reason = "owner_required"
        elif not full_trust_allowed:
            effective_session_mode = "copilot"
            downgrade_reason = "full_trust_disabled"

    if downgrade_reason:
        audit_logger = logger or LOGGER
        audit_logger.warning(
            "Downgraded requested agent session mode to copilot.",
            extra={
                "workspace_id": str(workspace_id or "").strip() or "default",
                "session_id": str(session_id or "").strip() or "agent-turn",
                "user_id": current_user_id,
                "email": str((current_user or {}).get("email") or "").strip().lower(),
                "requested_session_mode": requested_session_mode,
                "effective_session_mode": effective_session_mode,
                "reason": downgrade_reason,
            },
        )

    from server_modules import runtime_policy as runtime_policy_module

    raw_requested_trust_mode = str(normalized.get("trust_mode") or "").strip().lower()
    requested_trust_mode = (
        runtime_policy_module.normalize_trust_mode(raw_requested_trust_mode)
        if raw_requested_trust_mode
        else ""
    )
    requested_permission_mode = str(normalized.get("permission_mode") or "").strip().lower()
    if requested_permission_mode not in {"default", "auto_review", "autopilot", "device_access"}:
        requested_permission_mode = ""

    requested_execution_target = str(normalized.get("execution_target") or "").strip()
    normalized_target = runtime_policy_module.normalize_execution_target(requested_execution_target)
    machine_trust_declaration = str(normalized.get("machine_trust_declaration") or "").strip().lower()
    if (
        effective_session_mode == "agent"
        and machine_trust_declaration == "agent"
        and normalized_target == runtime_policy_module.EXECUTION_TARGET_LOCAL_COMPANION
    ):
        normalized_runtime_trust_zone = runtime_policy_module.RUNTIME_TRUST_ZONE_OWNED_DEDICATED_RUNTIME
    else:
        normalized_runtime_trust_zone = runtime_policy_module.normalize_runtime_trust_zone(
            normalized.get("runtime_trust_zone"),
            target=normalized_target,
        )
        if (
            effective_session_mode != "agent"
            and normalized_runtime_trust_zone == runtime_policy_module.RUNTIME_TRUST_ZONE_OWNED_DEDICATED_RUNTIME
        ):
            normalized_runtime_trust_zone = runtime_policy_module.normalize_runtime_trust_zone(
                None,
                target=normalized_target,
            )

    if requested_permission_mode == "autopilot" and (
        normalized_runtime_trust_zone == runtime_policy_module.RUNTIME_TRUST_ZONE_USER_OWNED_LOCAL
    ):
        requested_permission_mode = "device_access"
        normalized["permission_mode"] = requested_permission_mode
    elif requested_permission_mode:
        normalized["permission_mode"] = requested_permission_mode

    if effective_session_mode == "agent":
        if requested_permission_mode == "auto_review":
            effective_trust_mode = (
                requested_trust_mode
                if requested_trust_mode in {"guarded", "strict", "cost_guard", "sensitive_guard"}
                else "sensitive_guard"
            )
        elif requested_permission_mode == "device_access":
            effective_trust_mode = (
                requested_trust_mode
                if requested_trust_mode in {"guarded", "strict", "cost_guard", "sensitive_guard"}
                else "sensitive_guard"
            )
        else:
            effective_trust_mode = "auto"
    elif requested_trust_mode == "auto":
        effective_trust_mode = "guarded"
    else:
        effective_trust_mode = requested_trust_mode or None

    raw_elevated = normalized.get("elevated")
    elevated_mode = runtime_policy_module.normalize_elevated_mode(
        normalized.get("elevated_mode")
        or (raw_elevated.get("mode") if isinstance(raw_elevated, dict) else raw_elevated),
        default=runtime_policy_module.ELEVATED_MODE_ASK,
    )
    if effective_session_mode != "agent":
        elevated_mode = runtime_policy_module.ELEVATED_MODE_ASK
    normalized["runtime_trust_zone"] = normalized_runtime_trust_zone
    normalized["elevated_mode"] = elevated_mode
    if isinstance(raw_elevated, dict):
        next_elevated = dict(raw_elevated)
        next_elevated["mode"] = elevated_mode
        next_elevated["runtime_trust_zone"] = normalized_runtime_trust_zone
        normalized["elevated"] = next_elevated
    elif isinstance(raw_elevated, str):
        normalized["elevated"] = {
            "mode": elevated_mode,
            "runtime_trust_zone": normalized_runtime_trust_zone,
        }

    normalized["requested_session_mode"] = requested_session_mode
    normalized["session_mode"] = effective_session_mode
    normalized["effective_session_mode"] = effective_session_mode
    requested_interactive_approvals = normalized.get("interactive_approvals")
    if effective_session_mode != "agent":
        normalized["interactive_approvals"] = True
    elif normalized_runtime_trust_zone == runtime_policy_module.RUNTIME_TRUST_ZONE_USER_OWNED_LOCAL:
        normalized["interactive_approvals"] = True
    elif requested_permission_mode == "auto_review":
        normalized["interactive_approvals"] = True
    elif isinstance(requested_interactive_approvals, bool):
        normalized["interactive_approvals"] = bool(requested_interactive_approvals)
    else:
        normalized["interactive_approvals"] = False
    normalized["approval_ui"] = str(normalized.get("approval_ui") or "card").strip() or "card"
    if effective_trust_mode:
        normalized["trust_mode"] = effective_trust_mode
    elif "trust_mode" in normalized:
        normalized.pop("trust_mode", None)
    if downgrade_reason:
        normalized["session_mode_downgraded"] = True
        normalized["session_mode_downgrade_reason"] = downgrade_reason
    else:
        normalized.pop("session_mode_downgraded", None)
        normalized.pop("session_mode_downgrade_reason", None)

    return {key: value for key, value in normalized.items() if value not in (None, "", [], {})}


def _attachments_from_payload(items: Any) -> List[TurnAttachment]:
    if not isinstance(items, list):
        return []
    attachments: List[TurnAttachment] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        # Accept both "url" (SageChatAttachment from frontend) and "uri" (ApiTurnAttachment)
        uri = str(item.get("uri") or item.get("url") or "").strip()
        if not uri:
            continue
        name = str(item.get("name") or item.get("filename") or "").strip()
        metadata = _metadata_dict(item.get("metadata"))
        # Copy flat SageChatAttachment fields into metadata for downstream use
        if "safe_filename" in item and "safe_filename" not in metadata:
            metadata["safe_filename"] = str(item["safe_filename"]).strip() or name
        if "content_type" in item and "content_type" not in metadata:
            metadata["content_type"] = str(item["content_type"]).strip()
        if "size" in item and "size" not in metadata:
            try:
                metadata["size"] = int(item["size"])
            except (ValueError, TypeError):
                pass
        if "file_id" in item and "file_id" not in metadata:
            metadata["file_id"] = str(item["file_id"]).strip()
        attachments.append(
            TurnAttachment(
                kind=str(item.get("kind") or "file").strip() or "file",
                uri=uri,
                name=name,
                metadata=metadata,
            )
        )
    return attachments


def _request_actor_id(current_user: Any, metadata: Optional[Dict[str, Any]] = None) -> str:
    metadata = metadata if isinstance(metadata, dict) else {}
    return (
        str((current_user or {}).get("user_id") or "").strip()
        or str((current_user or {}).get("email") or "").strip().lower()
        or str(metadata.get("owner_user_id") or "").strip()
        or str(metadata.get("owner_email") or "").strip().lower()
        or str((current_user or {}).get("auth_type") or "").strip()
        or "anonymous"
    )


def _request_actor_display_name(current_user: Any, actor_id: str) -> str:
    email = str((current_user or {}).get("email") or "").strip()
    if email:
        return email
    return actor_id


def _should_server_own_direct_chat_thread(
    *,
    channel: Any,
    execution_mode: Any = "sync",
    response_mode: Any = "stream",
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    if normalize_channel(channel) not in SERVER_OWNED_DIRECT_CHAT_CHANNELS:
        return False
    if str(execution_mode or "").strip().lower() != "sync":
        return False
    if str(response_mode or "").strip().lower() not in {"stream", "artifact"}:
        return False
    payload = _metadata_dict(metadata)
    agent_id = str(payload.get("agent_id") or "").strip().lower()
    if agent_id and agent_id not in MASTER_DIRECT_CHAT_AGENT_IDS:
        return False
    if str(payload.get("master_agent_install_id") or payload.get("workspace_agent_install_id") or "").strip():
        return True
    if agent_id in MASTER_DIRECT_CHAT_AGENT_IDS:
        return True
    source = str(payload.get("source") or "").strip().lower()
    return source in {"", "direct_chat", "mobile_chat"}


def _canonical_direct_chat_thread_id(
    *,
    current_user: Any,
    workspace_id: str,
    fallback_thread_id: str = "",
    fallback_actor_id: str = "",
) -> str:
    owner_user_id = (
        str((current_user or {}).get("user_id") or "").strip()
        or str(fallback_actor_id or "").strip()
    )
    if owner_user_id:
        return agent_registry_repository.build_master_thread_id(
            workspace_id=str(workspace_id or "default").strip() or "default",
            owner_user_id=owner_user_id,
        )
    return str(fallback_thread_id or "direct-chat").strip() or "direct-chat"


def normalize_direct_chat_session_metadata(
    *,
    current_user: Any,
    workspace_id: str,
    channel: Any,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = _metadata_dict(metadata)
    requested_thread_id = str(payload.get("thread_id") or "").strip()
    if not _should_server_own_direct_chat_thread(
        channel=channel,
        metadata=payload,
    ):
        return payload
    canonical_thread_id = _canonical_direct_chat_thread_id(
        current_user=current_user,
        workspace_id=workspace_id,
        fallback_thread_id=requested_thread_id,
        fallback_actor_id=_request_actor_id(current_user, payload),
    )
    if canonical_thread_id:
        if requested_thread_id and requested_thread_id != canonical_thread_id:
            payload.setdefault("client_thread_id", requested_thread_id)
        payload["thread_id"] = canonical_thread_id
    return payload


def serialize_turn_actor(actor: TurnActor) -> Dict[str, Any]:
    return {
        "type": str(actor.type or "").strip() or "user",
        "id": str(actor.id or "").strip(),
        "display_name": str(actor.display_name or "").strip(),
    }


def serialize_turn_attachment(attachment: TurnAttachment) -> Dict[str, Any]:
    return {
        "kind": str(attachment.kind or "").strip() or "file",
        "uri": str(attachment.uri or "").strip(),
        "name": str(attachment.name or "").strip(),
        "metadata": dict(attachment.metadata or {}),
    }


def serialize_agent_turn_request(request: AgentTurnRequest) -> Dict[str, Any]:
    return {
        "tenant_id": str(request.tenant_id or "").strip(),
        "workspace_id": str(request.workspace_id or "").strip() or "default",
        "thread_id": str(request.thread_id or "").strip() or str(request.session_id or "").strip(),
        "session_id": str(request.session_id or "").strip(),
        "channel": normalize_channel(request.channel),
        "actor": serialize_turn_actor(request.actor),
        "message": str(request.message or ""),
        "attachments": [serialize_turn_attachment(item) for item in request.attachments],
        "context_hints": dict(request.context_hints or {}),
        "execution_mode": request.execution_mode,
        "response_mode": request.response_mode,
        "machine_target": str(request.machine_target or "").strip() or None,
        "policy_context": dict(request.policy_context or {}),
    }


def resolve_agent_turn_request(value: Any) -> Optional[AgentTurnRequest]:
    if isinstance(value, AgentTurnRequest):
        return value
    if isinstance(value, dict):
        return build_agent_turn_request(value)
    return None


def resolve_agent_turn_request_with_fallback(
    value: Any,
    fallback: Any = None,
) -> Optional[AgentTurnRequest]:
    resolved = resolve_agent_turn_request(value)
    if isinstance(resolved, AgentTurnRequest):
        return resolved
    return resolve_agent_turn_request(fallback)


def resolve_agent_turn_request_from_runtime_context(
    *,
    request_meta: Any = None,
    session_ctx: Any = None,
) -> Optional[AgentTurnRequest]:
    meta = request_meta if isinstance(request_meta, dict) else {}
    context = session_ctx if isinstance(session_ctx, dict) else {}
    return resolve_agent_turn_request_with_fallback(
        meta.get("agent_turn_request"),
        context.get("agent_turn_request"),
    )


def _compact_turn_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _looks_like_lightweight_direct_chat(compact_message: str) -> bool:
    if not compact_message:
        return True
    if compact_message.endswith("?"):
        return True
    return any(compact_message.startswith(prefix) for prefix in LIGHTWEIGHT_DIRECT_CHAT_PREFIXES)


def _serious_task_marker_count(compact_message: str) -> int:
    if not compact_message:
        return 0
    count = 0
    for marker in SERIOUS_TASK_MARKERS:
        if re.search(rf"\b{re.escape(marker)}\b", compact_message):
            count += 1
    return count


def _promote_turn_request_to_primary_engine_path(request: AgentTurnRequest) -> AgentTurnRequest:
    if request.execution_mode != "sync" or request.response_mode != "stream":
        return request
    compact_message = _compact_turn_text(request.message)
    if not compact_message:
        return request
    context_hints = dict(request.context_hints or {})
    metadata = _metadata_dict(context_hints.get("metadata"))
    if bool(context_hints.get("force_direct_chat")) or bool(metadata.get("force_direct_chat")):
        return request

    promotion_reason = ""
    if bool(context_hints.get("force_durable_run")) or bool(metadata.get("force_durable_run")):
        promotion_reason = "explicit_override"
    elif request.attachments:
        promotion_reason = "attachments_present"
    else:
        marker_count = _serious_task_marker_count(compact_message)
        sequence_requested = any(marker in compact_message for marker in SERIOUS_SEQUENCE_MARKERS)
        outcome_requested = any(marker in compact_message for marker in SERIOUS_OUTCOME_MARKERS)
        if not _looks_like_lightweight_direct_chat(compact_message):
            if marker_count >= 2:
                promotion_reason = "task_markers"
            elif marker_count >= 1 and (sequence_requested or outcome_requested or len(compact_message) >= 48):
                promotion_reason = "task_markers"

    if not promotion_reason:
        return request

    next_metadata = dict(metadata)
    next_metadata["primary_engine_path"] = "durable_run"
    next_metadata["primary_engine_reason"] = promotion_reason
    context_hints["metadata"] = next_metadata
    return AgentTurnRequest(
        tenant_id=request.tenant_id,
        workspace_id=request.workspace_id,
        thread_id=request.thread_id,
        session_id=request.session_id,
        channel=request.channel,
        actor=request.actor,
        message=request.message,
        attachments=list(request.attachments or []),
        context_hints=context_hints,
        execution_mode="durable",
        response_mode="artifact",
        machine_target=request.machine_target,
        policy_context=dict(request.policy_context or {}),
    )


def build_agent_turn_request(payload: Dict[str, Any]) -> AgentTurnRequest:
    actor_payload = payload.get("actor") if isinstance(payload.get("actor"), dict) else {}
    context_hints = payload.get("context_hints") if isinstance(payload.get("context_hints"), dict) else {}
    request_id = (
        str(payload.get("client_request_id") or "").strip()
        or str(payload.get("request_id") or "").strip()
        or str(context_hints.get("request_id") or "").strip()
    )
    next_context_hints = dict(context_hints)
    if request_id:
        next_context_hints.setdefault("request_id", request_id)
    provider = str(payload.get("provider") or "").strip()
    model = str(payload.get("model") or "").strip()
    reasoning_effort = str(payload.get("reasoning_effort") or "").strip()
    if provider:
        next_context_hints.setdefault("provider", provider)
    if model:
        next_context_hints.setdefault("model", model)
    if reasoning_effort:
        next_context_hints.setdefault("reasoning_effort", reasoning_effort)
    request = AgentTurnRequest(
        tenant_id=str(payload.get("tenant_id") or "").strip(),
        workspace_id=str(payload.get("workspace_id") or "").strip(),
        thread_id=(
            str(payload.get("thread_id") or "").strip()
            or str(payload.get("session_id") or "").strip()
        ),
        session_id=str(payload.get("session_id") or "").strip(),
        channel=normalize_channel(payload.get("channel")),
        actor=TurnActor(
            type=str(actor_payload.get("type") or "user").strip() or "user",
            id=str(actor_payload.get("id") or "").strip(),
            display_name=str(actor_payload.get("display_name") or "").strip(),
        ),
        message=str(payload.get("message") or ""),
        attachments=_attachments_from_payload(payload.get("attachments")),
        context_hints=next_context_hints,
        execution_mode="durable" if str(payload.get("execution_mode") or "").strip().lower() == "durable" else "sync",
        response_mode=(
            str(payload.get("response_mode") or "stream").strip().lower()
            if str(payload.get("response_mode") or "").strip().lower() in {"stream", "artifact", "channel_reply"}
            else "stream"
        ),
        machine_target=str(payload.get("machine_target") or "").strip() or None,
        policy_context=payload.get("policy_context") if isinstance(payload.get("policy_context"), dict) else {},
    )
    return _promote_turn_request_to_primary_engine_path(request)


def build_inbound_agent_turn_request(
    *,
    tenant_id: str = "",
    workspace_id: str,
    session_id: str,
    thread_id: Optional[str] = None,
    channel: str,
    actor_type: str,
    actor_id: str,
    actor_display_name: str = "",
    message: str,
    attachments: Optional[List[Dict[str, Any]]] = None,
    prior_messages: Optional[List[Dict[str, Any]]] = None,
    business_plan: Optional[str] = None,
    context_hints: Optional[Dict[str, Any]] = None,
    execution_mode: ExecutionMode = "sync",
    response_mode: ResponseMode = "stream",
    machine_target: Optional[str] = None,
    policy_context: Optional[Dict[str, Any]] = None,
) -> AgentTurnRequest:
    resolved_session_id = str(session_id or "agent-turn").strip() or "agent-turn"
    resolved_thread_id = str(thread_id or resolved_session_id).strip() or resolved_session_id
    next_context_hints = dict(context_hints or {})
    if isinstance(prior_messages, list) and prior_messages:
        next_context_hints["prior_messages"] = [dict(item) for item in prior_messages if isinstance(item, dict)]
    resolved_business_plan = str(business_plan or "").strip()
    if resolved_business_plan:
        next_context_hints["business_plan"] = resolved_business_plan
    return AgentTurnRequest(
        tenant_id=str(tenant_id or "default").strip() or "default",
        workspace_id=str(workspace_id or "default").strip() or "default",
        thread_id=resolved_thread_id,
        session_id=resolved_session_id,
        channel=normalize_channel(channel),
        actor=TurnActor(
            type=str(actor_type or "user").strip() or "user",
            id=str(actor_id or "").strip(),
            display_name=str(actor_display_name or actor_id or "").strip(),
        ),
        message=str(message or ""),
        attachments=_attachments_from_payload(attachments or []),
        context_hints=next_context_hints,
        execution_mode=execution_mode,
        response_mode=response_mode,
        machine_target=str(machine_target or "").strip() or None,
        policy_context=dict(policy_context or {}),
    )


def build_direct_chat_turn_request(
    *,
    current_user: Any,
    body: Dict[str, Any],
    workspace_id: str,
    thread_id: str,
    client_request_id: str,
    message: str,
) -> AgentTurnRequest:
    actor_id = _request_actor_id(current_user)
    body_metadata = _metadata_dict(body.get("metadata"))
    policy_context = _metadata_dict(body.get("policy_context"))
    runtime_hints = {
        "provider": str(body.get("provider") or "").strip() or None,
        "model": str(body.get("model") or "").strip() or None,
        "reasoning_effort": str(body.get("reasoning_effort") or "").strip() or None,
        "request_id": str(client_request_id or "").strip() or None,
        "approved_action": body.get("approved_action") if isinstance(body.get("approved_action"), dict) else None,
        "prior_messages": body.get("prior_messages") if isinstance(body.get("prior_messages"), list) else [],
        "max_iterations": body.get("max_iterations"),
        "metadata": body_metadata,
    }
    request = AgentTurnRequest(
        tenant_id=str(body.get("tenant_id") or actor_id or "default").strip() or "default",
        workspace_id=str(workspace_id or "default").strip() or "default",
        thread_id=str(thread_id or client_request_id or "direct-chat").strip() or "direct-chat",
        session_id=(
            str(body.get("session_id") or "").strip()
            or str(thread_id or client_request_id or "direct-chat").strip()
            or "direct-chat"
        ),
        channel=normalize_channel(body.get("channel") or "web"),
        actor=TurnActor(
            type="user",
            id=actor_id,
            display_name=_request_actor_display_name(current_user, actor_id),
        ),
        message=str(message or ""),
        attachments=_attachments_from_payload(body.get("attachments")),
        context_hints={key: value for key, value in runtime_hints.items() if value not in (None, "", [], {})},
        execution_mode="sync",
        response_mode="stream",
        machine_target=str(body.get("machine_target") or "").strip() or None,
        policy_context=policy_context,
    )
    return _promote_turn_request_to_primary_engine_path(request)


def normalize_server_owned_turn_request(
    *,
    current_user: Any,
    turn_request: AgentTurnRequest,
) -> AgentTurnRequest:
    metadata = _metadata_dict(turn_request.context_hints.get("metadata"))
    source = str(turn_request.context_hints.get("source") or "").strip()
    if source and not str(metadata.get("source") or "").strip():
        metadata["source"] = source
    if not _should_server_own_direct_chat_thread(
        channel=turn_request.channel,
        execution_mode=turn_request.execution_mode,
        response_mode=turn_request.response_mode,
        metadata=metadata,
    ):
        return turn_request

    resolved_thread_id = _canonical_direct_chat_thread_id(
        current_user=current_user,
        workspace_id=turn_request.workspace_id,
        fallback_thread_id=turn_request.thread_id or turn_request.session_id,
        fallback_actor_id=turn_request.actor.id,
    )
    if resolved_thread_id == str(turn_request.thread_id or "").strip():
        return turn_request

    next_metadata = dict(metadata)
    client_thread_id = str(turn_request.thread_id or "").strip()
    if client_thread_id:
        next_metadata["client_thread_id"] = client_thread_id
    next_metadata["server_owned_thread"] = True

    next_context_hints = dict(turn_request.context_hints or {})
    next_context_hints["thread_id"] = resolved_thread_id
    next_context_hints["metadata"] = next_metadata
    return AgentTurnRequest(
        tenant_id=turn_request.tenant_id,
        workspace_id=turn_request.workspace_id,
        thread_id=resolved_thread_id,
        session_id=turn_request.session_id,
        channel=turn_request.channel,
        actor=turn_request.actor,
        message=turn_request.message,
        attachments=list(turn_request.attachments or []),
        context_hints=next_context_hints,
        execution_mode=turn_request.execution_mode,
        response_mode=turn_request.response_mode,
        machine_target=turn_request.machine_target,
        policy_context=dict(turn_request.policy_context or {}),
    )


def build_local_worker_turn_request(
    *,
    worker_id: str,
    run: Optional[Dict[str, Any]],
    context: Optional[Dict[str, Any]],
    metadata: Optional[Dict[str, Any]],
    goal: str,
) -> AgentTurnRequest:
    run_payload = run if isinstance(run, dict) else {}
    context_payload = context if isinstance(context, dict) else {}
    metadata_payload = metadata if isinstance(metadata, dict) else {}
    run_id = str(run_payload.get("id") or "").strip()
    machine_target = (
        str(metadata_payload.get("machine_target") or "").strip()
        or str(metadata_payload.get("execution_target_selected") or "").strip()
        or str(metadata_payload.get("execution_target") or "").strip()
        or None
    )
    return build_inbound_agent_turn_request(
        tenant_id=str(metadata_payload.get("tenant_id") or worker_id or "default").strip(),
        workspace_id=str(context_payload.get("workspace_id") or metadata_payload.get("workspace_id") or "default").strip() or "default",
        session_id=(
            str(metadata_payload.get("session_id") or "").strip()
            or str(metadata_payload.get("thread_id") or "").strip()
            or run_id
            or str(worker_id or "local-worker").strip()
        ),
        channel=str(metadata_payload.get("channel") or "local_worker").strip() or "local_worker",
        actor_type="worker",
        actor_id=str(worker_id or "").strip(),
        actor_display_name=str(worker_id or "local-worker").strip(),
        message=str(goal or ""),
        context_hints={
            "run_id": run_id or None,
            "metadata": metadata_payload,
        },
        execution_mode="sync",
        response_mode="artifact",
        machine_target=machine_target,
        policy_context={
            "execution_target": str(metadata_payload.get("execution_target") or "").strip() or None,
            "trust_mode": str(metadata_payload.get("trust_mode") or "").strip() or None,
        },
    )


def build_discord_turn_request(
    *,
    parsed: Dict[str, Any],
    connector_entry: Dict[str, Any],
    goal: str,
    trace_id: str,
) -> AgentTurnRequest:
    metadata = connector_entry.get("metadata") if isinstance(connector_entry.get("metadata"), dict) else {}
    workspace_id = str(connector_entry.get("workspace_id") or metadata.get("workspace_id") or "default").strip() or "default"
    session_id = (
        str(parsed.get("channel_id") or "").strip()
        or str(parsed.get("guild_id") or "").strip()
        or "discord"
    )
    return build_inbound_agent_turn_request(
        tenant_id=str(metadata.get("tenant_id") or "default").strip() or "default",
        workspace_id=workspace_id,
        session_id=session_id,
        channel="discord",
        actor_type="user",
        actor_id=str(parsed.get("user_id") or "").strip() or "discord-user",
        actor_display_name=str(parsed.get("username") or parsed.get("user_id") or "discord-user").strip(),
        message=str(goal or ""),
        context_hints={
            "trace_id": f"discord:{trace_id}",
            "source": "discord_connector",
            "discord": {
                "channel_id": str(parsed.get("channel_id") or "").strip() or None,
                "guild_id": str(parsed.get("guild_id") or "").strip() or None,
                "message_id": str(parsed.get("message_id") or "").strip() or None,
                "interaction_id": str(parsed.get("interaction_id") or "").strip() or None,
                "event_type": str(parsed.get("event_type") or "").strip() or None,
            },
            "metadata": metadata,
        },
        execution_mode="durable",
        response_mode="artifact",
        machine_target=str(metadata.get("machine_target") or "").strip() or None,
        policy_context={
            "execution_target": str(metadata.get("execution_target") or "").strip() or None,
            "trust_mode": str(metadata.get("trust_mode") or "").strip() or None,
        },
    )


def build_telegram_turn_request(
    *,
    workspace_id: str,
    connector_entry: Optional[Dict[str, Any]],
    goal: str,
    chat_id: str,
    sender_id: str,
    update_id: int,
    message_id: Optional[str] = None,
    trace_id: str = "",
    source_event_id: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> AgentTurnRequest:
    connector_metadata = connector_entry.get("metadata") if isinstance((connector_entry or {}).get("metadata"), dict) else {}
    merged_metadata = dict(connector_metadata)
    if isinstance(metadata, dict):
        merged_metadata.update(metadata)
    resolved_workspace_id = str(workspace_id or connector_entry.get("workspace_id") or merged_metadata.get("workspace_id") or "default").strip() or "default"
    return build_inbound_agent_turn_request(
        tenant_id=str(merged_metadata.get("tenant_id") or "default").strip() or "default",
        workspace_id=resolved_workspace_id,
        session_id=str(chat_id or "telegram").strip() or "telegram",
        channel="telegram",
        actor_type="user",
        actor_id=str(sender_id or chat_id or "telegram-user").strip() or "telegram-user",
        actor_display_name=str(sender_id or chat_id or "telegram-user").strip() or "telegram-user",
        message=str(goal or ""),
        context_hints={
            "trace_id": str(trace_id or "").strip() or None,
            "source": "telegram_connector",
            "telegram": {
                "chat_id": str(chat_id or "").strip() or None,
                "sender_id": str(sender_id or "").strip() or None,
                "update_id": int(update_id or 0),
                "message_id": str(message_id or "").strip() or None,
                "source_event_id": str(source_event_id or "").strip() or None,
            },
            "metadata": merged_metadata,
        },
        execution_mode="durable",
        response_mode="artifact",
        machine_target=str(merged_metadata.get("machine_target") or "").strip() or None,
        policy_context={
            "execution_target": str(merged_metadata.get("execution_target") or "").strip() or None,
            "trust_mode": str(merged_metadata.get("trust_mode") or "").strip() or None,
        },
    )


def build_whatsapp_turn_request(
    *,
    workspace_id: str,
    connector_entry: Optional[Dict[str, Any]],
    goal: str,
    from_number: str,
    to_number: str,
    message_sid: str,
    account_sid: str,
    session_id: str,
    trace_id: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> AgentTurnRequest:
    connector_metadata = connector_entry.get("metadata") if isinstance((connector_entry or {}).get("metadata"), dict) else {}
    merged_metadata = dict(connector_metadata)
    if isinstance(metadata, dict):
        merged_metadata.update(metadata)
    resolved_workspace_id = str(workspace_id or connector_entry.get("workspace_id") or merged_metadata.get("workspace_id") or "default").strip() or "default"
    return build_inbound_agent_turn_request(
        tenant_id=str(merged_metadata.get("tenant_id") or "default").strip() or "default",
        workspace_id=resolved_workspace_id,
        session_id=str(session_id or "whatsapp").strip() or "whatsapp",
        channel="whatsapp",
        actor_type="user",
        actor_id=str(from_number or "whatsapp-user").strip() or "whatsapp-user",
        actor_display_name=str(from_number or "whatsapp-user").strip() or "whatsapp-user",
        message=str(goal or ""),
        context_hints={
            "trace_id": str(trace_id or "").strip() or None,
            "source": "whatsapp_connector",
            "whatsapp": {
                "from": str(from_number or "").strip() or None,
                "to": str(to_number or "").strip() or None,
                "message_sid": str(message_sid or "").strip() or None,
                "account_sid": str(account_sid or "").strip() or None,
            },
            "metadata": merged_metadata,
        },
        execution_mode="durable",
        response_mode="artifact",
        machine_target=str(merged_metadata.get("machine_target") or "").strip() or None,
        policy_context={
            "execution_target": str(merged_metadata.get("execution_target") or "").strip() or None,
            "trust_mode": str(merged_metadata.get("trust_mode") or "").strip() or None,
        },
    )


def resolve_direct_chat_turn_request(
    *,
    current_user: Any,
    body: Dict[str, Any],
    request_signature_fn: Any,
) -> DirectChatTurnResolution:
    if not isinstance(body, dict):
        raise ValueError("Invalid chat payload.")
    message = str(body.get("message") or "").strip()
    if not message:
        raise ValueError("Chat message is required.")
    workspace_id = str(body.get("workspace_id") or "default").strip() or "default"
    metadata = _direct_chat_request_metadata(body)
    requested_thread_id = str(body.get("thread_id") or "").strip()
    request_body = dict(body)
    if _should_server_own_direct_chat_thread(
        channel=body.get("channel") or "web",
        metadata=metadata,
    ):
        thread_id = _canonical_direct_chat_thread_id(
            current_user=current_user,
            workspace_id=workspace_id,
            fallback_thread_id=requested_thread_id,
            fallback_actor_id=_request_actor_id(current_user, metadata),
        )
        if requested_thread_id and not str(request_body.get("session_id") or "").strip():
            request_body["session_id"] = requested_thread_id
    else:
        thread_id = requested_thread_id or "direct-chat"
    client_request_id = (
        str(body.get("client_request_id") or "").strip()
        or str(request_signature_fn(body) if callable(request_signature_fn) else "").strip()
        or "direct-chat-request"
    )
    turn_request = build_direct_chat_turn_request(
        current_user=current_user,
        body=request_body,
        workspace_id=workspace_id,
        thread_id=thread_id,
        client_request_id=client_request_id,
        message=message,
    )
    return DirectChatTurnResolution(
        turn_request=turn_request,
        workspace_id=workspace_id,
        thread_id=thread_id,
        client_request_id=client_request_id,
        message=message,
    )


def ensure_direct_chat_turn_request(
    *,
    current_user: Any,
    body: Dict[str, Any],
    workspace_id: str,
    thread_id: str,
    client_request_id: str,
    message: str,
    agent_turn_request: Any = None,
) -> AgentTurnRequest:
    resolved = resolve_agent_turn_request(agent_turn_request)
    if isinstance(resolved, AgentTurnRequest):
        return resolved
    return build_direct_chat_turn_request(
        current_user=current_user,
        body=body,
        workspace_id=workspace_id,
        thread_id=thread_id,
        client_request_id=client_request_id,
        message=message,
    )


def resolve_run_start_turn_request(
    *,
    current_user: Any,
    body: Any,
    stamp_request_owner_fn: Any,
) -> RunStartTurnResolution:
    from server_modules.runtime_models import RunStartRequest

    request = body if body is not None else RunStartRequest()
    if callable(stamp_request_owner_fn):
        request = stamp_request_owner_fn(request, current_user)
    return RunStartTurnResolution(
        request=request,
        turn_request=build_run_start_turn_request(request),
    )


def build_run_start_turn_request(req: Any) -> AgentTurnRequest:
    metadata = _metadata_dict(getattr(req, "metadata", None))
    actor_id = _request_actor_id(None, metadata)
    workspace_id = str(getattr(req, "workspace_id", None) or metadata.get("workspace_id") or "default").strip() or "default"
    session_id = (
        str(metadata.get("session_id") or "").strip()
        or str(metadata.get("thread_id") or "").strip()
        or str(metadata.get("parent_run_id") or "").strip()
        or str(getattr(req, "workflow_id", None) or "").strip()
        or "run-start"
    )
    message = (
        str(getattr(req, "user_goal", None) or "").strip()
        or str(getattr(req, "business_plan", None) or "").strip()
        or str(metadata.get("summary") or "").strip()
    )
    policy_context = {
        "trust_mode": str(metadata.get("trust_mode") or "").strip() or None,
        "outcome_pack": str(metadata.get("outcome_pack") or "").strip() or None,
        "execution_target": str(metadata.get("execution_target") or "").strip() or None,
        "action_policy": metadata.get("action_policy") if isinstance(metadata.get("action_policy"), dict) else None,
    }
    context_hints = {
        "engine": str(getattr(req, "engine", None) or "orion").strip().lower() or "orion",
        "workflow_id": str(getattr(req, "workflow_id", None) or "").strip() or None,
        "provider": str(getattr(req, "provider", None) or "").strip() or None,
        "model": str(getattr(req, "model", None) or "").strip() or None,
        "credential_id": str(getattr(req, "credential_id", None) or "").strip() or None,
        "agent_role": str(getattr(req, "agent_role", None) or "").strip() or None,
        "max_iterations": getattr(req, "max_iterations", None),
        "metadata": metadata,
    }
    return AgentTurnRequest(
        tenant_id=str(metadata.get("tenant_id") or actor_id or "default").strip() or "default",
        workspace_id=workspace_id,
        thread_id=session_id,
        session_id=session_id,
        channel=normalize_channel(metadata.get("channel") or "web"),
        actor=TurnActor(
            type="user",
            id=actor_id,
            display_name=str(metadata.get("owner_email") or actor_id).strip(),
        ),
        message=message,
        attachments=[],
        context_hints={key: value for key, value in context_hints.items() if value not in (None, "", [], {})},
        execution_mode="durable",
        response_mode="artifact",
        machine_target=(
            str(metadata.get("machine_target") or "").strip()
            or None
        ),
        policy_context={key: value for key, value in policy_context.items() if value not in (None, "", [], {})},
    )


def bind_agent_turn_metadata(
    metadata: Optional[Dict[str, Any]],
    request: AgentTurnRequest,
    *,
    source: str,
) -> Dict[str, Any]:
    bound = _metadata_dict(metadata)
    bound["agent_turn_request"] = serialize_agent_turn_request(request)
    bound["agent_turn_contract_version"] = 1
    if source and not str(bound.get("source") or "").strip():
        bound["source"] = str(source).strip()
    if request.thread_id and not str(bound.get("thread_id") or "").strip():
        bound["thread_id"] = str(request.thread_id).strip()
    if request.session_id and not str(bound.get("session_id") or "").strip():
        bound["session_id"] = str(request.session_id).strip()
    if request.channel and not str(bound.get("channel") or "").strip():
        bound["channel"] = normalize_channel(request.channel)
    if request.machine_target and not str(bound.get("machine_target") or "").strip():
        bound["machine_target"] = str(request.machine_target).strip()
    if request.actor.id and not str(bound.get("request_actor_id") or "").strip():
        bound["request_actor_id"] = str(request.actor.id).strip()
    return bound


def _build_noop_direct_chat_execution_services():
    from server_modules.direct_chat_service import build_direct_chat_execution_services

    def _unreachable(*args: Any, **kwargs: Any):
        raise RuntimeError("Direct chat services are unavailable for this agent_turn dispatch.")

    return build_direct_chat_execution_services(
        chat_stream_key=_unreachable,
        session_manager_enabled=lambda: False,
        session_manager_factory=_unreachable,
        build_direct_operator_reply=_unreachable,
        build_chat_turn_event_stream=_unreachable,
    )


def execute_system_agent_turn(
    *,
    turn_request: AgentTurnRequest,
    run_execution_services: Any,
    direct_chat_services: Any = None,
    chat_body: Optional[Dict[str, Any]] = None,
    run_request: Optional[Any] = None,
    current_user: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    from server_modules.direct_tool_config_service import run_async_tool_call

    system_user = (
        dict(current_user)
        if isinstance(current_user, dict)
        else {"auth_type": "api_key", "user_id": "", "email": ""}
    )
    return run_async_tool_call(
        agent_turn(
            turn_request=turn_request,
            current_user=system_user,
            run_execution_services=run_execution_services,
            direct_chat_services=direct_chat_services,
            chat_body=chat_body,
            run_request=run_request,
        )
    )


async def agent_turn(
    *,
    turn_request: AgentTurnRequest,
    current_user: Any,
    run_execution_services: Any,
    direct_chat_services: Any = None,
    chat_body: Optional[Dict[str, Any]] = None,
    run_request: Optional[Any] = None,
) -> Dict[str, Any]:
    from server_modules.turn_runtime import (
        build_turn_execution_services,
        execute_agent_turn_request,
    )

    tracer = get_tracer("server_modules.agent_turn")
    resolved_turn_request = turn_request
    resolved_turn_request.policy_context = normalize_turn_policy_context(
        resolved_turn_request.policy_context,
        current_user=current_user,
        workspace_id=resolved_turn_request.workspace_id,
        session_id=resolved_turn_request.session_id,
    )
    resolved_turn_request = healthguide_safety_service.apply_health_safety_to_turn_request(resolved_turn_request)
    preferred_session_id = str(turn_request.session_id or "").strip()
    resolved_thread_id = str(turn_request.thread_id or preferred_session_id or "").strip()
    if not resolved_thread_id:
        raise ValueError("AgentTurnRequest.thread_id is required.")
    resolved_turn_request.thread_id = resolved_thread_id
    binding_metadata = _metadata_dict(resolved_turn_request.context_hints.get("metadata"))
    trace_context = None
    session_record = None
    if preferred_session_id:
        session_record = await session_service.get_session_scoped(
            preferred_session_id,
            workspace_id=turn_request.workspace_id,
            tenant_id=turn_request.tenant_id,
            channel=turn_request.channel,
            thread_id=resolved_thread_id,
        )
        scope_mismatch = isinstance(session_record, dict) and isinstance(session_record.get("_scope_mismatch"), dict)
        if (
            isinstance(session_record, dict)
            and not scope_mismatch
            and str(session_record.get("status") or "").strip().lower() != "expired"
        ):
            session_metadata = session_record.get("metadata") if isinstance(session_record.get("metadata"), dict) else {}
            metadata_updates = (
                {"thread_id": resolved_thread_id}
                if str(session_metadata.get("thread_id") or "").strip()
                else None
            )
            if isinstance(metadata_updates, dict):
                await session_service.extend_session(preferred_session_id, metadata_updates=metadata_updates)
            else:
                await session_service.extend_session(preferred_session_id)
        else:
            resolved_turn_request.session_id = await session_service.create_session(
                workspace_id=turn_request.workspace_id,
                tenant_id=turn_request.tenant_id,
                actor=serialize_turn_actor(turn_request.actor),
                channel=turn_request.channel,
                metadata={
                    "source": "agent_turn",
                    "thread_id": resolved_thread_id,
                    "machine_target": turn_request.machine_target,
                    "master_agent_install_id": str(binding_metadata.get("master_agent_install_id") or binding_metadata.get("workspace_agent_install_id") or "").strip() or None,
                    "runtime_profile_id": str(binding_metadata.get("runtime_profile_id") or "").strip() or None,
                },
                session_id=None if scope_mismatch else preferred_session_id,
            )
            session_record = await session_service.get_session(resolved_turn_request.session_id)
    else:
        resolved_turn_request.session_id = await session_service.create_session(
            workspace_id=turn_request.workspace_id,
            tenant_id=turn_request.tenant_id,
            actor=serialize_turn_actor(turn_request.actor),
            channel=turn_request.channel,
            metadata={
                "source": "agent_turn",
                "thread_id": resolved_thread_id,
                "machine_target": turn_request.machine_target,
                "master_agent_install_id": str(binding_metadata.get("master_agent_install_id") or binding_metadata.get("workspace_agent_install_id") or "").strip() or None,
                "runtime_profile_id": str(binding_metadata.get("runtime_profile_id") or "").strip() or None,
            },
        )
        session_record = await session_service.get_session(resolved_turn_request.session_id)
    session_record = await _bind_cloud_runtime_session_if_needed(
        turn_request=resolved_turn_request,
        session_record=session_record,
    )
    if isinstance(session_record, dict):
        context_hints = dict(resolved_turn_request.context_hints or {})
        if not isinstance(context_hints.get("session"), dict):
            context_hints["session"] = dict(session_record)
        context_hints.setdefault("thread_id", resolved_thread_id)
        resolved_turn_request.context_hints = context_hints
    binding_metadata = _metadata_dict(resolved_turn_request.context_hints.get("metadata"))
    master_agent_install_id = (
        str(binding_metadata.get("master_agent_install_id") or binding_metadata.get("workspace_agent_install_id") or "").strip()
        or None
    )
    active_agent_install_id = (
        str(binding_metadata.get("active_agent_install_id") or binding_metadata.get("workspace_agent_install_id") or "").strip()
        or None
    )
    runtime_profile_id = str(binding_metadata.get("runtime_profile_id") or "").strip() or None
    await thread_service.ensure_master_thread(
        thread_id=resolved_thread_id,
        tenant_id=resolved_turn_request.tenant_id,
        workspace_id=resolved_turn_request.workspace_id,
        owner_user_id=str((current_user or {}).get("user_id") or resolved_turn_request.actor.id or "").strip() or None,
        master_agent_install_id=master_agent_install_id,
        channel=resolved_turn_request.channel,
        title=thread_service.build_default_thread_title(resolved_turn_request.message),
        metadata={
            "source": "agent_turn",
            "session_id": resolved_turn_request.session_id,
        },
    )
    try:
        trace_context = await agent_trace_service.start_trace(
            workspace_id=resolved_turn_request.workspace_id,
            tenant_id=resolved_turn_request.tenant_id,
            thread_id=resolved_thread_id,
            run_id=(
                str(getattr(run_request, "run_id", None) or "").strip()
                or str((chat_body or {}).get("run_id") or "").strip()
                or None
            ),
            surface=_trace_surface(resolved_turn_request.channel),
            runtime_target=_trace_runtime_target(resolved_turn_request),
            provider=_trace_provider(resolved_turn_request),
            model=_trace_model(resolved_turn_request),
            root_agent_id=_trace_root_agent_id(binding_metadata),
        )
        if trace_context is not None:
            try:
                await agent_trace_service.emit_trace_started(trace_context, "text")
            except Exception:
                pass
    except Exception:
        trace_context = None
    await thread_service.record_user_turn(
        thread_id=resolved_thread_id,
        tenant_id=resolved_turn_request.tenant_id,
        workspace_id=resolved_turn_request.workspace_id,
        session_id=resolved_turn_request.session_id,
        actor=serialize_turn_actor(resolved_turn_request.actor),
        content=resolved_turn_request.message,
        runtime_profile_id=runtime_profile_id,
        metadata={
            **{
                **{
                    key: value
                    for key, value in _metadata_dict(resolved_turn_request.context_hints).items()
                    if key not in {"prior_messages", "business_plan"}
                },
                "prior_messages_count": (
                    len(resolved_turn_request.context_hints.get("prior_messages"))
                    if isinstance(resolved_turn_request.context_hints.get("prior_messages"), list)
                    else 0
                ),
                "business_plan_present": bool(str(resolved_turn_request.context_hints.get("business_plan") or "").strip()),
                "business_plan_preview": (
                    str(resolved_turn_request.context_hints.get("business_plan") or "").strip()[:240].rstrip() or None
                ),
            },
            "request_id": str(resolved_turn_request.context_hints.get("request_id") or "").strip() or None,
        },
    )
    with tracer.start_as_current_span("agent_turn.handle") as span:
        set_span_attributes(
            span,
            {
                "workspace_id": str(resolved_turn_request.workspace_id or "").strip() or "default",
                "thread_id": resolved_thread_id,
                "session_id": str(resolved_turn_request.session_id or "").strip() or "agent-turn",
                "channel": normalize_channel(resolved_turn_request.channel),
                "tenant_id": str(resolved_turn_request.tenant_id or "").strip() or "default",
                "actor_type": str(resolved_turn_request.actor.type or "").strip() or "user",
                "run_id": (
                    str(getattr(run_request, "run_id", None) or "").strip()
                    or str((chat_body or {}).get("run_id") or "").strip()
                    or None
                ),
                "execution_mode": str(resolved_turn_request.execution_mode or "").strip() or "sync",
                "response_mode": str(resolved_turn_request.response_mode or "").strip() or "stream",
                "requested_session_mode": str(resolved_turn_request.policy_context.get("requested_session_mode") or "").strip() or None,
                "effective_session_mode": (
                    str(resolved_turn_request.policy_context.get("effective_session_mode") or "").strip()
                    or str(resolved_turn_request.policy_context.get("session_mode") or "").strip()
                    or None
                ),
            },
        )
        try:
            try:
                await agent_trace_service.emit_trace_routed(
                    trace_context,
                    str(resolved_turn_request.execution_mode or "").strip().lower() or "sync",
                    _trace_runtime_target(resolved_turn_request),
                    _trace_provider(resolved_turn_request),
                    _trace_model(resolved_turn_request),
                    _trace_routing_reason(resolved_turn_request),
                )
            except Exception:
                pass
            services = build_turn_execution_services(
                run_execution=run_execution_services,
                direct_chat=direct_chat_services or _build_noop_direct_chat_execution_services(),
            )
            result = await execute_agent_turn_request(
                turn_request=resolved_turn_request,
                current_user=current_user,
                services=services,
                chat_body=chat_body,
                run_request=run_request,
                trace_context=trace_context,
            )
            trace_id = trace_context.trace_id if trace_context is not None else None
            result = _bind_trace_id_to_turn_result(result, trace_id)
            if _should_persist_direct_chat_result(result):
                assistant_request_id = (
                    str(resolved_turn_request.context_hints.get("request_id") or "").strip()
                    or str(result.get("client_request_id") or "").strip()
                    or None
                )
                await thread_service.record_assistant_turn(
                    thread_id=resolved_thread_id,
                    tenant_id=resolved_turn_request.tenant_id,
                    workspace_id=resolved_turn_request.workspace_id,
                    session_id=resolved_turn_request.session_id,
                    actor={
                        "type": "assistant",
                        "id": str((result.get("metadata") or {}).get("agent_role") or "master-agent").strip() or "master-agent",
                        "display_name": "Empyralis",
                    },
                    reply=str(result.get("reply") or ""),
                    status=str(result.get("status") or "completed").strip() or "completed",
                    run_id=str(result.get("run_id") or "").strip() or None,
                    active_agent_install_id=active_agent_install_id,
                    runtime_profile_id=runtime_profile_id,
                    approvals=list(result.get("approvals") or []) if isinstance(result.get("approvals"), list) else [],
                    interventions=list(result.get("interventions") or []) if isinstance(result.get("interventions"), list) else [],
                    metadata=_assistant_turn_metadata_from_result(
                        result,
                        request_id=assistant_request_id,
                    ),
                )
                set_span_attributes(
                    span,
                    {
                        "run_id": str(result.get("run_id") or "").strip() or None,
                        "turn_status": str(result.get("status") or "").strip() or None,
                        "trace_id": _result_trace_id(result),
                    },
                )
            return result
        except Exception as exc:
            try:
                span.record_exception(exc)
            except Exception:
                pass
            try:
                await agent_trace_service.emit_trace_failed(
                    trace_context,
                    code=exc.__class__.__name__,
                    message=str(exc),
                    retryable=False,
                    failed_item_id=None,
                )
            except Exception:
                pass
            raise


def bind_agent_turn_request_meta(
    request_meta: Optional[Dict[str, Any]],
    request: Any,
) -> Dict[str, Any]:
    bound = _metadata_dict(request_meta)
    resolved = resolve_agent_turn_request(request)
    if not isinstance(resolved, AgentTurnRequest):
        return bound
    bound["agent_turn_request"] = serialize_agent_turn_request(resolved)
    if resolved.workspace_id and not str(bound.get("workspace_id") or "").strip():
        bound["workspace_id"] = str(resolved.workspace_id).strip()
    if resolved.thread_id and not str(bound.get("thread_id") or "").strip():
        bound["thread_id"] = str(resolved.thread_id).strip()
    return bound


def resolve_agent_turn_session_identity(
    request: Any,
    *,
    workspace_id: str = "",
    session_id: str = "",
    user_id: str = "",
) -> Dict[str, str]:
    resolved = resolve_agent_turn_request(request)
    if not isinstance(resolved, AgentTurnRequest):
        return {
            "workspace_id": str(workspace_id or "default").strip() or "default",
            "thread_id": str(session_id or "direct-chat").strip() or "direct-chat",
            "user_id": str(user_id or "").strip(),
        }
    return {
        "workspace_id": str(resolved.workspace_id or workspace_id or "default").strip() or "default",
        "thread_id": str(resolved.thread_id or resolved.session_id or session_id or "direct-chat").strip() or "direct-chat",
        "user_id": str(user_id or resolved.actor.id or "").strip(),
    }


def build_agent_turn_session_context(
    request: Any,
    *,
    workspace_id: str = "",
    session_id: str = "",
    user_id: str = "",
) -> Dict[str, Any]:
    resolved = resolve_agent_turn_request(request)
    identity = resolve_agent_turn_session_identity(
        request,
        workspace_id=workspace_id,
        session_id=session_id,
        user_id=user_id,
    )
    if not isinstance(resolved, AgentTurnRequest):
        return identity
    policy_context = dict(resolved.policy_context or {})
    return {
        **identity,
        "session_mode": str(policy_context.get("session_mode") or "").strip() or None,
        "effective_session_mode": str(policy_context.get("effective_session_mode") or "").strip() or None,
        "interactive_approvals": (
            policy_context.get("interactive_approvals")
            if isinstance(policy_context.get("interactive_approvals"), bool)
            else None
        ),
        "agent_turn_request": serialize_agent_turn_request(resolved),
    }
