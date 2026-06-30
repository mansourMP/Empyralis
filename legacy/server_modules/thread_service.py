from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from server_modules import control_plane_repository, rust_runtime_kernel_client, transcript_events_service


LOGGER = logging.getLogger(__name__)


build_default_thread_title = control_plane_repository.build_default_thread_title


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _request_id_from_metadata(metadata: Optional[Dict[str, Any]]) -> Optional[str]:
    payload = _coerce_dict(metadata)
    for key in ("request_id", "client_request_id"):
        token = str(payload.get(key) or "").strip()
        if token:
            return token
    return None


def _actor_role(actor: Optional[Dict[str, Any]] = None) -> str:
    payload = _coerce_dict(actor)
    role = str(payload.get("role") or payload.get("actor_role") or "").strip().lower()
    if role:
        return role
    actor_type = str(payload.get("type") or "").strip().lower()
    if actor_type in {"system", "service", "runtime"}:
        return "system"
    return "member"


def _enforce_thread_record_decision(**payload: Any) -> Dict[str, Any]:
    operation = str(payload.get("operation") or "thread_record").strip() or "thread_record"
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "thread-record-decision",
            payload,
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        reason = str(getattr(exc, "reason", "") or "thread_record_denied").strip()
        raise RuntimeError(f"Rust thread-record kernel blocked {operation}: {reason}") from exc
    if not isinstance(decision, dict):
        raise RuntimeError(f"Rust thread-record kernel returned invalid decision for {operation}")
    return decision


def _require_thread_next_action(decision: Dict[str, Any], *allowed_actions: str) -> str:
    next_action = str(decision.get("next_action") or "").strip()
    if next_action in allowed_actions:
        return next_action
    operation = str(decision.get("operation") or "thread_record").strip() or "thread_record"
    raise RuntimeError(
        f"Rust thread-record kernel returned unexpected next_action for {operation}: {next_action or 'missing'}"
    )


async def ensure_master_thread(
    *,
    thread_id: str,
    tenant_id: str,
    workspace_id: str,
    owner_user_id: Optional[str],
    master_agent_install_id: Optional[str] = None,
    channel: str,
    title: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    decision = _enforce_thread_record_decision(
        operation="normalize_thread",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        thread_id=thread_id,
        actor_role="system",
        title=title or "New chat",
        status="active",
    )
    _require_thread_next_action(decision, "normalize_thread_record")
    return await control_plane_repository.ensure_agent_thread(
        thread_id=thread_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        owner_user_id=owner_user_id,
        master_agent_install_id=master_agent_install_id,
        channel=channel,
        title=str(decision.get("normalized_title") or title or "New chat").strip() or "New chat",
        metadata=metadata,
    )


async def record_user_turn(
    *,
    thread_id: str,
    tenant_id: str,
    workspace_id: str,
    session_id: Optional[str],
    actor: Dict[str, Any],
    content: str,
    runtime_profile_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    decision = _enforce_thread_record_decision(
        operation="create_turn",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        thread_id=thread_id,
        actor_role=_actor_role(actor),
        content=content,
        client_request_id=_request_id_from_metadata(metadata),
        metadata_only_turn=not bool(str(content or "").strip()),
    )
    _require_thread_next_action(decision, "create_thread_turn")
    return await control_plane_repository.upsert_agent_turn(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        thread_id=thread_id,
        session_id=session_id,
        role="user",
        status="completed",
        content=content,
        actor=actor,
        runtime_profile_id=runtime_profile_id,
        metadata=_coerce_dict(metadata),
        request_id=str(decision.get("request_id") or _request_id_from_metadata(metadata) or "").strip() or None,
        attachments=list(attachments or []),
    )


async def record_assistant_turn(
    *,
    thread_id: str,
    tenant_id: str,
    workspace_id: str,
    session_id: Optional[str],
    actor: Dict[str, Any],
    reply: str,
    status: str,
    run_id: Optional[str] = None,
    active_agent_install_id: Optional[str] = None,
    runtime_profile_id: Optional[str] = None,
    approvals: Optional[List[Dict[str, Any]]] = None,
    interventions: Optional[List[Dict[str, Any]]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    decision = _enforce_thread_record_decision(
        operation="create_turn",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        thread_id=thread_id,
        actor_role=_actor_role(actor),
        content=reply,
        status=str(status or "completed").strip().lower() or "completed",
        run_id=run_id,
        client_request_id=_request_id_from_metadata(metadata),
        metadata_only_turn=not bool(str(reply or "").strip()),
    )
    _require_thread_next_action(decision, "create_thread_turn")
    return await control_plane_repository.upsert_agent_turn(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        thread_id=thread_id,
        session_id=session_id,
        role="assistant",
        status=str(status or "completed").strip().lower() or "completed",
        content=reply,
        run_id=run_id,
        actor=actor,
        active_agent_install_id=active_agent_install_id,
        runtime_profile_id=runtime_profile_id,
        approvals=list(approvals or []),
        interventions=list(interventions or []),
        metadata=_coerce_dict(metadata),
        request_id=str(decision.get("request_id") or _request_id_from_metadata(metadata) or "").strip() or None,
    )


async def append_assistant_turn_transcript_event(
    *,
    thread_id: str,
    tenant_id: str,
    workspace_id: str,
    event_name: str,
    payload: Dict[str, Any],
    request_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    transcript_event = transcript_events_service.build_transcript_event(event_name, payload)
    if transcript_event is None:
        return None
    decision = _enforce_thread_record_decision(
        operation="create_turn",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        thread_id=thread_id,
        actor_role="system",
        content=str(transcript_event.get("name") or event_name or "").strip(),
        run_id=run_id,
        client_request_id=request_id,
        metadata_only_turn=False,
    )
    _require_thread_next_action(decision, "create_thread_turn")
    return await control_plane_repository.append_agent_turn_transcript_event(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        thread_id=thread_id,
        transcript_event=transcript_event,
        request_id=str(decision.get("request_id") or request_id or "").strip() or None,
        trace_id=trace_id,
        run_id=run_id,
    )


async def list_threads(
    *,
    workspace_id: str,
    tenant_id: Optional[str] = None,
    owner_user_id: Optional[str] = None,
    include_turns: bool = False,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    decision = _enforce_thread_record_decision(
        operation="list_threads",
        tenant_id=tenant_id or "default",
        workspace_id=workspace_id,
        actor_role="member",
        include_turns=bool(include_turns),
        limit=int(limit or 50),
    )
    _require_thread_next_action(decision, "list_workspace_threads")
    return await control_plane_repository.list_agent_threads(
        workspace_id=workspace_id,
        tenant_id=tenant_id,
        owner_user_id=owner_user_id,
        include_turns=bool(decision.get("include_turns", include_turns)),
        limit=int(decision.get("limit") or limit or 50),
    )


async def get_thread(
    thread_id: str,
    *,
    tenant_id: str,
    workspace_id: str,
    include_turns: bool = True,
) -> Optional[Dict[str, Any]]:
    decision = _enforce_thread_record_decision(
        operation="get_thread",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        thread_id=thread_id,
        actor_role="member",
        include_turns=bool(include_turns),
    )
    next_action = _require_thread_next_action(decision, "get_thread_with_turns", "return_empty_primary_thread")
    if next_action == "return_empty_primary_thread":
        return {
            "thread_id": thread_id,
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "title": "New chat",
            "status": "active",
            "turns": [] if include_turns else None,
        }
    return await control_plane_repository.get_agent_thread(
        thread_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        include_turns=include_turns,
    )
