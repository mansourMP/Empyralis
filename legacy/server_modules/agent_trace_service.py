from __future__ import annotations

from dataclasses import dataclass, field
import logging
from threading import Lock
from typing import Any, Dict, List, Optional
import uuid

from server_modules import control_plane_repository, failure_policy_service
from server_modules.error_contracts import OBSERVABILITY_FAILURE, SEVERITY_WARNING


LOGGER = logging.getLogger(__name__)

PERSISTED_TRACE_EVENT_TYPES = frozenset(
    {
        "trace.started",
        "trace.routed",
        "plan.started",
        "plan.item.created",
        "plan.item.updated",
        "plan.replanned",
        "tool.started",
        "tool.result",
        "search.query",
        "search.results",
        "browser.action",
        "browser.screenshot",
        "delegation.started",
        "delegation.finished",
        "approval.requested",
        "approval.resolved",
        "artifact.created",
        "assistant.message.completed",
        "trace.completed",
        "trace.failed",
    }
)

EPHEMERAL_TRACE_EVENT_TYPES = frozenset(
    {
        "reasoning.summary.delta",
        "tool.progress",
        "assistant.message.delta",
    }
)


@dataclass(slots=True)
class TraceContext:
    trace_id: str
    workspace_id: str
    tenant_id: str
    thread_id: Optional[str]
    run_id: Optional[str]
    root_agent_id: str
    _seq: int = 0
    _lock: Lock = field(default_factory=Lock, repr=False)

    def next_seq(self) -> int:
        with self._lock:
            self._seq += 1
            return self._seq


def _normalized_payload(value: Any) -> Dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _event_id() -> str:
    return f"tevent_{uuid.uuid4().hex[:24]}"


def _normalized_ref(value: Any) -> Optional[str]:
    token = str(value or "").strip()
    return token or None


def _build_event_envelope(
    trace_context: TraceContext,
    *,
    event_id: str,
    seq: int,
    event_type: str,
    persisted: bool,
    data: Optional[Dict[str, Any]],
    parent_id: Optional[str] = None,
    item_id: Optional[str] = None,
    tool_call_id: Optional[str] = None,
    child_run_id: Optional[str] = None,
    approval_id: Optional[str] = None,
    artifact_id: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "id": event_id,
        "trace_id": trace_context.trace_id,
        "seq": seq,
        "event_type": str(event_type or "").strip(),
        "persisted": bool(persisted),
        "agent_id": trace_context.root_agent_id,
        "parent_id": _normalized_ref(parent_id),
        "item_id": _normalized_ref(item_id),
        "tool_call_id": _normalized_ref(tool_call_id),
        "child_run_id": _normalized_ref(child_run_id),
        "approval_id": _normalized_ref(approval_id),
        "artifact_id": _normalized_ref(artifact_id),
        "data": _normalized_payload(data),
    }


def _log_failure(operation: str, exc: Exception) -> None:
    failure_policy_service.log_degraded_operation(
        logger=LOGGER,
        code=f"agent_trace_{str(operation or 'operation').strip().lower()}_failed",
        message=f"Agent trace service failed during {operation}.",
        error_class=OBSERVABILITY_FAILURE,
        degraded_component="agent_trace_service",
        severity=SEVERITY_WARNING,
        retryable=False,
        status_code=500,
        metadata={"operation": str(operation or "").strip() or None},
        exc=exc,
    )


async def _publish_live_event(_context: TraceContext, _event: Dict[str, Any]) -> None:
    return None


def build_ephemeral_envelope(
    trace_context: Optional[TraceContext],
    event_type: str,
    data: Optional[Dict[str, Any]],
    *,
    parent_id: Optional[str] = None,
    item_id: Optional[str] = None,
    tool_call_id: Optional[str] = None,
    child_run_id: Optional[str] = None,
    approval_id: Optional[str] = None,
    artifact_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    try:
        if trace_context is None:
            return None
        resolved_event_type = str(event_type or "").strip()
        if not resolved_event_type:
            return None
        seq = trace_context.next_seq()
        return _build_event_envelope(
            trace_context,
            event_id=_event_id(),
            seq=seq,
            event_type=resolved_event_type,
            persisted=False,
            data=data,
            parent_id=parent_id,
            item_id=item_id,
            tool_call_id=tool_call_id,
            child_run_id=child_run_id,
            approval_id=approval_id,
            artifact_id=artifact_id,
        )
    except Exception as exc:
        _log_failure("build_ephemeral_envelope", exc)
        return None


async def start_trace(
    *,
    workspace_id: str,
    tenant_id: str,
    thread_id: Optional[str],
    run_id: Optional[str],
    surface: str,
    runtime_target: Optional[str],
    provider: Optional[str],
    model: Optional[str],
    root_agent_id: str,
) -> Optional[TraceContext]:
    try:
        trace = await control_plane_repository.create_agent_trace(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            thread_id=thread_id,
            run_id=run_id,
            root_agent_id=root_agent_id,
            surface=surface,
            runtime_target=runtime_target,
            provider=provider,
            model=model,
        )
        if not trace:
            return None
        return TraceContext(
            trace_id=str(trace.get("id") or "").strip(),
            workspace_id=str(trace.get("workspace_id") or workspace_id or "").strip(),
            tenant_id=str(trace.get("tenant_id") or tenant_id or "").strip(),
            thread_id=str(trace.get("thread_id") or "").strip() or None,
            run_id=str(trace.get("run_id") or "").strip() or None,
            root_agent_id=str(trace.get("root_agent_id") or root_agent_id or "").strip(),
        )
    except Exception as exc:
        _log_failure("start_trace", exc)
        return None


async def resume_trace(
    *,
    trace_id: str,
    tenant_id: str,
    workspace_id: str,
    thread_id: Optional[str] = None,
    run_id: Optional[str] = None,
    root_agent_id: Optional[str] = None,
) -> Optional[TraceContext]:
    try:
        normalized_trace_id = str(trace_id or "").strip()
        normalized_tenant_id = str(tenant_id or "").strip()
        normalized_workspace_id = str(workspace_id or "").strip()
        if not normalized_trace_id or not normalized_tenant_id or not normalized_workspace_id:
            return None
        trace = await control_plane_repository.get_agent_trace_by_id(
            normalized_trace_id,
            tenant_id=normalized_tenant_id,
            workspace_id=normalized_workspace_id,
        )
        if not isinstance(trace, dict):
            return None
        events = await control_plane_repository.get_agent_trace_events(
            normalized_trace_id,
            tenant_id=normalized_tenant_id,
            workspace_id=normalized_workspace_id,
            persisted_only=False,
        )
        max_seq = max((int((item or {}).get("seq") or 0) for item in (events or []) if isinstance(item, dict)), default=0)
        return TraceContext(
            trace_id=normalized_trace_id,
            workspace_id=str(trace.get("workspace_id") or normalized_workspace_id or "").strip(),
            tenant_id=str(trace.get("tenant_id") or normalized_tenant_id or "").strip(),
            thread_id=str(trace.get("thread_id") or thread_id or "").strip() or None,
            run_id=str(trace.get("run_id") or run_id or "").strip() or None,
            root_agent_id=str(trace.get("root_agent_id") or root_agent_id or "").strip(),
            _seq=max_seq,
        )
    except Exception as exc:
        _log_failure("resume_trace", exc)
        return None


async def emit_with_envelope(
    trace_context: Optional[TraceContext],
    event_type: str,
    data: Optional[Dict[str, Any]],
    *,
    persisted: bool = True,
    parent_id: Optional[str] = None,
    item_id: Optional[str] = None,
    tool_call_id: Optional[str] = None,
    child_run_id: Optional[str] = None,
    approval_id: Optional[str] = None,
    artifact_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    try:
        if trace_context is None:
            return None
        resolved_event_type = str(event_type or "").strip()
        if not resolved_event_type:
            return None
        event_id = _event_id()
        seq = trace_context.next_seq()
        envelope = _build_event_envelope(
            trace_context,
            event_id=event_id,
            seq=seq,
            event_type=resolved_event_type,
            persisted=persisted,
            data=data,
            parent_id=parent_id,
            item_id=item_id,
            tool_call_id=tool_call_id,
            child_run_id=child_run_id,
            approval_id=approval_id,
            artifact_id=artifact_id,
        )
        if persisted:
            await control_plane_repository.append_agent_trace_event(
                trace_id=trace_context.trace_id,
                tenant_id=trace_context.tenant_id,
                workspace_id=trace_context.workspace_id,
                seq=seq,
                event_type=resolved_event_type,
                persisted=True,
                agent_id=trace_context.root_agent_id,
                payload=envelope["data"],
                parent_id=envelope["parent_id"],
                item_id=envelope["item_id"],
                tool_call_id=envelope["tool_call_id"],
                child_run_id=envelope["child_run_id"],
                approval_id=envelope["approval_id"],
                artifact_id=envelope["artifact_id"],
                event_id=event_id,
            )
        await _publish_live_event(trace_context, envelope)
        return envelope
    except Exception as exc:
        _log_failure("emit_with_envelope", exc)
        return None


async def emit(
    trace_context: Optional[TraceContext],
    event_type: str,
    data: Optional[Dict[str, Any]],
    *,
    persisted: bool = True,
    parent_id: Optional[str] = None,
    item_id: Optional[str] = None,
    tool_call_id: Optional[str] = None,
    child_run_id: Optional[str] = None,
    approval_id: Optional[str] = None,
    artifact_id: Optional[str] = None,
) -> Optional[str]:
    envelope = await emit_with_envelope(
        trace_context,
        event_type,
        data,
        persisted=persisted,
        parent_id=parent_id,
        item_id=item_id,
        tool_call_id=tool_call_id,
        child_run_id=child_run_id,
        approval_id=approval_id,
        artifact_id=artifact_id,
    )
    return str((envelope or {}).get("id") or "").strip() or None


async def finish_trace(
    trace_context: Optional[TraceContext],
    *,
    outcome: str,
    final_message_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    try:
        if trace_context is None:
            return None
        return await control_plane_repository.finish_agent_trace(
            trace_context.trace_id,
            tenant_id=trace_context.tenant_id,
            workspace_id=trace_context.workspace_id,
            outcome=outcome,
            final_message_id=final_message_id,
        )
    except Exception as exc:
        _log_failure("finish_trace", exc)
        return None


async def get_trace_replay(trace_id: str, workspace_id: str) -> Dict[str, Any]:
    try:
        workspace = await control_plane_repository.get_workspace_by_id(workspace_id)
        tenant_id = str((workspace or {}).get("tenant_id") or "").strip()
        if not tenant_id:
            return {"trace": None, "events": []}
        trace = await control_plane_repository.get_agent_trace_by_id(
            trace_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
        if trace is None:
            return {"trace": None, "events": []}
        events = await control_plane_repository.get_agent_trace_events(
            trace_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            persisted_only=True,
        )
        return {
            "trace": trace,
            "events": events,
        }
    except Exception as exc:
        _log_failure("get_trace_replay", exc)
        return {"trace": None, "events": []}


async def emit_trace_started(trace_context: Optional[TraceContext], input_mode: str) -> Optional[str]:
    return await emit(
        trace_context,
        "trace.started",
        {
            "input_mode": str(input_mode or "").strip() or "text",
        },
        persisted=True,
    )


async def emit_trace_routed(
    trace_context: Optional[TraceContext],
    execution_mode: str,
    runtime_target: Optional[str],
    provider: Optional[str],
    model: Optional[str],
    reason_summary: Optional[str],
) -> Optional[str]:
    return await emit(
        trace_context,
        "trace.routed",
        {
            "execution_mode": str(execution_mode or "").strip(),
            "runtime_target": str(runtime_target or "").strip(),
            "provider": str(provider or "").strip(),
            "model": str(model or "").strip(),
            "reason_summary": str(reason_summary or "").strip(),
        },
        persisted=True,
    )


async def emit_plan_started(
    trace_context: Optional[TraceContext],
    plan_id: str,
    title: str,
    summary: Optional[str],
) -> Optional[str]:
    return await emit(
        trace_context,
        "plan.started",
        {
            "plan_id": str(plan_id or "").strip(),
            "title": str(title or "").strip(),
            "summary": str(summary or "").strip(),
        },
        persisted=True,
    )


async def emit_plan_item(
    trace_context: Optional[TraceContext],
    plan_id: str,
    item_id: str,
    index: int,
    title: str,
    kind: str,
    owner: str,
    depends_on: Optional[List[str]],
    rationale_summary: Optional[str],
) -> Optional[str]:
    return await emit(
        trace_context,
        "plan.item.created",
        {
            "plan_id": str(plan_id or "").strip(),
            "item_id": str(item_id or "").strip(),
            "index": int(index),
            "title": str(title or "").strip(),
            "kind": str(kind or "").strip(),
            "owner": str(owner or "").strip(),
            "depends_on": list(depends_on or []),
            "rationale_summary": str(rationale_summary or "").strip(),
        },
        persisted=True,
        item_id=item_id,
    )


async def emit_plan_item_updated(
    trace_context: Optional[TraceContext],
    item_id: str,
    status: str,
    summary: Optional[str],
) -> Optional[str]:
    return await emit(
        trace_context,
        "plan.item.updated",
        {
            "item_id": str(item_id or "").strip(),
            "status": str(status or "").strip(),
            "summary": str(summary or "").strip(),
        },
        persisted=True,
        item_id=item_id,
    )


async def emit_plan_replanned(
    trace_context: Optional[TraceContext],
    new_plan_id: str,
    supersedes_plan_id: str,
    reason_summary: Optional[str],
) -> Optional[str]:
    return await emit(
        trace_context,
        "plan.replanned",
        {
            "new_plan_id": str(new_plan_id or "").strip(),
            "supersedes_plan_id": str(supersedes_plan_id or "").strip(),
            "reason_summary": str(reason_summary or "").strip(),
        },
        persisted=True,
    )


async def emit_tool_started(
    trace_context: Optional[TraceContext],
    item_id: Optional[str],
    tool_call_id: str,
    tool_name: str,
    capability_id: Optional[str],
    connector_id: Optional[str],
    args_preview: Optional[Dict[str, Any]],
    agent_activity: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    data = {
        "tool_name": str(tool_name or "").strip(),
        "capability_id": str(capability_id or "").strip() or None,
        "connector_id": str(connector_id or "").strip() or None,
        "args_preview": _normalized_payload(args_preview),
    }
    if isinstance(agent_activity, dict) and agent_activity:
        data["agent_activity"] = _normalized_payload(agent_activity)
    return await emit(
        trace_context,
        "tool.started",
        data,
        persisted=True,
        item_id=item_id,
        tool_call_id=tool_call_id,
    )


async def emit_tool_progress(
    trace_context: Optional[TraceContext],
    tool_call_id: str,
    message: Optional[str],
    percent: Optional[int],
) -> Optional[str]:
    return await emit(
        trace_context,
        "tool.progress",
        {
            "message": str(message or "").strip(),
            "percent": int(percent or 0),
        },
        persisted=False,
        tool_call_id=tool_call_id,
    )


async def emit_tool_result(
    trace_context: Optional[TraceContext],
    tool_call_id: str,
    status: str,
    summary: Optional[str],
    artifact_ids: Optional[List[str]],
    *,
    tool_name: Optional[str] = None,
    capability_id: Optional[str] = None,
    connector_id: Optional[str] = None,
    args_preview: Optional[Dict[str, Any]] = None,
    runtime_session_id: Optional[str] = None,
    runtime_target: Optional[str] = None,
    request_id: Optional[str] = None,
    action_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    agent_activity: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    data: Dict[str, Any] = {
        "status": str(status or "").strip(),
        "summary": str(summary or "").strip(),
        "artifact_ids": list(artifact_ids or []),
    }
    for key, value in {
        "tool_name": tool_name,
        "capability_id": capability_id,
        "connector_id": connector_id,
        "runtime_session_id": runtime_session_id,
        "runtime_target": runtime_target,
        "request_id": request_id,
        "action_id": action_id,
    }.items():
        token = str(value or "").strip()
        if token:
            data[key] = token
    if isinstance(args_preview, dict) and args_preview:
        data["args_preview"] = _normalized_payload(args_preview)
    if isinstance(metadata, dict) and metadata:
        data["metadata"] = _normalized_payload(metadata)
    if isinstance(agent_activity, dict) and agent_activity:
        data["agent_activity"] = _normalized_payload(agent_activity)
    return await emit(
        trace_context,
        "tool.result",
        data,
        persisted=True,
        tool_call_id=tool_call_id,
    )


async def emit_search_query(
    trace_context: Optional[TraceContext],
    tool_call_id: str,
    provider: str,
    query: str,
    filters: Optional[Dict[str, Any]],
) -> Optional[str]:
    return await emit(
        trace_context,
        "search.query",
        {
            "provider": str(provider or "").strip(),
            "query": str(query or "").strip(),
            "filters": _normalized_payload(filters),
        },
        persisted=True,
        tool_call_id=tool_call_id,
    )


async def emit_search_results(
    trace_context: Optional[TraceContext],
    tool_call_id: str,
    results: Optional[List[Dict[str, Any]]],
) -> Optional[str]:
    return await emit(
        trace_context,
        "search.results",
        {
            "results": list(results or []),
        },
        persisted=True,
        tool_call_id=tool_call_id,
    )


async def emit_browser_action(
    trace_context: Optional[TraceContext],
    tool_call_id: str,
    action: str,
    target_summary: Optional[str],
    url: Optional[str],
) -> Optional[str]:
    return await emit(
        trace_context,
        "browser.action",
        {
            "action": str(action or "").strip(),
            "target_summary": str(target_summary or "").strip(),
            "url": str(url or "").strip() or None,
        },
        persisted=True,
        tool_call_id=tool_call_id,
    )


async def emit_browser_screenshot(
    trace_context: Optional[TraceContext],
    tool_call_id: str,
    artifact_id: str,
    caption: Optional[str],
    width: Optional[int],
    height: Optional[int],
) -> Optional[str]:
    return await emit(
        trace_context,
        "browser.screenshot",
        {
            "caption": str(caption or "").strip(),
            "width": int(width or 0),
            "height": int(height or 0),
        },
        persisted=True,
        tool_call_id=tool_call_id,
        artifact_id=artifact_id,
    )


async def emit_delegation_started(
    trace_context: Optional[TraceContext],
    item_id: Optional[str],
    child_run_id: str,
    specialist_id: str,
    specialist_name: Optional[str],
    task_summary: Optional[str],
) -> Optional[str]:
    return await emit(
        trace_context,
        "delegation.started",
        {
            "specialist_id": str(specialist_id or "").strip(),
            "specialist_name": str(specialist_name or "").strip(),
            "task_summary": str(task_summary or "").strip(),
        },
        persisted=True,
        item_id=item_id,
        child_run_id=child_run_id,
    )


async def emit_delegation_finished(
    trace_context: Optional[TraceContext],
    item_id: Optional[str],
    child_run_id: str,
    status: str,
    result_summary: Optional[str],
) -> Optional[str]:
    return await emit(
        trace_context,
        "delegation.finished",
        {
            "status": str(status or "").strip(),
            "result_summary": str(result_summary or "").strip(),
        },
        persisted=True,
        item_id=item_id,
        child_run_id=child_run_id,
    )


async def emit_approval_requested(
    trace_context: Optional[TraceContext],
    approval_id: str,
    kind: str,
    title: str,
    description: Optional[str],
    blocking_item_id: Optional[str],
) -> Optional[str]:
    return await emit(
        trace_context,
        "approval.requested",
        {
            "kind": str(kind or "").strip(),
            "title": str(title or "").strip(),
            "description": str(description or "").strip(),
            "blocking_item_id": str(blocking_item_id or "").strip() or None,
        },
        persisted=True,
        item_id=blocking_item_id,
        approval_id=approval_id,
    )


async def emit_approval_resolved(
    trace_context: Optional[TraceContext],
    approval_id: str,
    decision: str,
    actor: str,
    note: Optional[str],
) -> Optional[str]:
    return await emit(
        trace_context,
        "approval.resolved",
        {
            "decision": str(decision or "").strip(),
            "actor": str(actor or "").strip(),
            "note": str(note or "").strip(),
        },
        persisted=True,
        approval_id=approval_id,
    )


async def emit_artifact_created(
    trace_context: Optional[TraceContext],
    artifact_id: str,
    kind: str,
    title: str,
    mime_type: Optional[str],
) -> Optional[str]:
    return await emit(
        trace_context,
        "artifact.created",
        {
            "kind": str(kind or "").strip(),
            "title": str(title or "").strip(),
            "mime_type": str(mime_type or "").strip(),
        },
        persisted=True,
        artifact_id=artifact_id,
    )


async def emit_reasoning_summary_delta(
    trace_context: Optional[TraceContext],
    item_id: Optional[str],
    delta: Optional[str],
) -> Optional[str]:
    return await emit(
        trace_context,
        "reasoning.summary.delta",
        {
            "delta": str(delta or "").strip(),
        },
        persisted=False,
        item_id=item_id,
    )


async def emit_assistant_message_delta(
    trace_context: Optional[TraceContext],
    message_id: str,
    delta: Optional[str],
) -> Optional[str]:
    return await emit(
        trace_context,
        "assistant.message.delta",
        {
            "message_id": str(message_id or "").strip(),
            "delta": str(delta or "").strip(),
        },
        persisted=False,
    )


async def emit_assistant_message_completed(
    trace_context: Optional[TraceContext],
    message_id: str,
    text: Optional[str],
    citation_refs: Optional[List[str]],
    artifact_ids: Optional[List[str]],
) -> Optional[str]:
    return await emit(
        trace_context,
        "assistant.message.completed",
        {
            "message_id": str(message_id or "").strip(),
            "text": str(text or "").strip(),
            "citation_refs": list(citation_refs or []),
            "artifact_ids": list(artifact_ids or []),
        },
        persisted=True,
    )


async def emit_trace_completed(
    trace_context: Optional[TraceContext],
    duration_ms: int,
    final_message_id: Optional[str],
) -> Optional[str]:
    return await emit(
        trace_context,
        "trace.completed",
        {
            "duration_ms": int(duration_ms or 0),
            "final_message_id": str(final_message_id or "").strip() or None,
        },
        persisted=True,
    )


async def emit_trace_failed(
    trace_context: Optional[TraceContext],
    code: str,
    message: str,
    retryable: bool,
    failed_item_id: Optional[str],
) -> Optional[str]:
    return await emit(
        trace_context,
        "trace.failed",
        {
            "code": str(code or "").strip(),
            "message": str(message or "").strip(),
            "retryable": bool(retryable),
            "failed_item_id": str(failed_item_id or "").strip() or None,
        },
        persisted=True,
        item_id=failed_item_id,
    )
