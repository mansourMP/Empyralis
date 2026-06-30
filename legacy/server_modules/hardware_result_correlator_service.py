from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from server_modules import (
    agent_computer_surface_service,
    agent_trace_service,
    secret_redaction_service,
    thread_service,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> Dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _safe_activity_detail(value: Any) -> Optional[str]:
    text = " ".join(_text(value).split())
    if not text:
        return None
    if "DSML" in text or "<||" in text or "< | |" in text or "tool_calls" in text:
        return None
    if text.startswith("{") or text.startswith("["):
        return None
    return text[:500]


def _hardware_agent_activity(
    *,
    tool_call_id: str,
    state: str,
    summary: Optional[str] = None,
    started_at: Optional[int] = None,
) -> Dict[str, Any]:
    normalized_state = _text(state).lower() or "running"
    activity_shape = agent_computer_surface_service.agent_computer_activity_shape(normalized_state)
    activity_type = activity_shape["type"]
    label = activity_shape["label"]
    status = activity_shape["status"]
    payload: Dict[str, Any] = {
        "id": _text(tool_call_id) or f"hardware:{_now_ms()}",
        "type": activity_type,
        "label": label,
        "startedAt": int(started_at or _now_ms()),
        "status": status,
    }
    if status != "active":
        payload["completedAt"] = _now_ms()
    detail = _safe_activity_detail(summary)
    if detail:
        payload["detail"] = detail
    return payload


def _append_unique(ids: List[str], candidate: Any) -> None:
    token = _text(candidate)
    if token and token not in ids:
        ids.append(token)


def runtime_artifact_ids_from_response(response: Dict[str, Any]) -> List[str]:
    candidates: List[Any] = []
    artifact = response.get("artifact")
    if isinstance(artifact, dict):
        candidates.append(artifact.get("artifact_id") or artifact.get("id"))
    artifacts = response.get("artifacts")
    if isinstance(artifacts, list):
        for item in artifacts:
            if isinstance(item, dict):
                candidates.append(item.get("artifact_id") or item.get("id"))
    streaming_ui = response.get("streaming_ui")
    if isinstance(streaming_ui, dict):
        live_screenshot = streaming_ui.get("live_screenshot")
        if isinstance(live_screenshot, dict):
            candidates.append(live_screenshot.get("artifact_id") or live_screenshot.get("id"))
        streaming_artifacts = streaming_ui.get("artifacts")
        if isinstance(streaming_artifacts, list):
            for item in streaming_artifacts:
                if isinstance(item, dict):
                    candidates.append(item.get("artifact_id") or item.get("id"))
    ids: List[str] = []
    for candidate in candidates:
        _append_unique(ids, candidate)
    return ids


def artifact_ids_from_execution(execution: Dict[str, Any]) -> List[str]:
    ids: List[str] = []
    for key in ("artifact_id", "screenshot_artifact_id"):
        _append_unique(ids, execution.get(key))
    result = execution.get("result")
    if isinstance(result, dict):
        _append_unique(ids, result.get("artifact_id") or result.get("screenshot_artifact_id"))
        artifacts = result.get("artifacts")
        if isinstance(artifacts, list):
            for item in artifacts:
                if isinstance(item, dict):
                    _append_unique(ids, item.get("artifact_id") or item.get("id"))
                else:
                    _append_unique(ids, item)
    return ids


def artifact_ids_from_artifact_records(items: Any) -> List[str]:
    ids: List[str] = []
    for item in list(items or []):
        if isinstance(item, dict):
            candidate = item.get("artifact_id") or item.get("id")
        else:
            candidate = item
        _append_unique(ids, candidate)
    return ids


async def append_hardware_transcript_event(
    runtime_session: Dict[str, Any],
    *,
    event_type: str,
    data: Optional[Dict[str, Any]] = None,
    tool_call_id: Optional[str] = None,
    artifact_id: Optional[str] = None,
    approval_id: Optional[str] = None,
) -> None:
    session = _dict(runtime_session)
    thread_id = _text(session.get("thread_id"))
    if not thread_id:
        return
    payload: Dict[str, Any] = {
        "event_type": _text(event_type),
        "data": _dict(data),
    }
    if tool_call_id:
        payload["tool_call_id"] = _text(tool_call_id)
    if artifact_id:
        payload["artifact_id"] = _text(artifact_id)
    if approval_id:
        payload["approval_id"] = _text(approval_id)
    try:
        await thread_service.append_assistant_turn_transcript_event(
            tenant_id=_text(session.get("tenant_id")) or "default",
            workspace_id=_text(session.get("workspace_id")) or "default",
            thread_id=thread_id,
            event_name="trace",
            payload=payload,
            request_id=_text(session.get("request_id")) or None,
            trace_id=_text(session.get("trace_id")) or None,
            run_id=_text(session.get("run_id")) or None,
        )
    except Exception:
        return


async def emit_tool_started(
    trace_context: Any,
    *,
    tool_call_id: str,
    capability_id: str,
    arguments: Dict[str, Any],
) -> None:
    activity = _hardware_agent_activity(
        tool_call_id=tool_call_id,
        state="connecting",
        summary="Preparing Agent Computer action.",
    )
    await agent_trace_service.emit_tool_started(
        trace_context,
        item_id=None,
        tool_call_id=tool_call_id,
        tool_name=capability_id,
        capability_id=capability_id,
        connector_id="hardware_runtime",
        args_preview=secret_redaction_service.sanitize_mapping(arguments),
        agent_activity=activity,
    )
    if trace_context and getattr(trace_context, "thread_id", None):
        await append_hardware_transcript_event(
            {
                "tenant_id": getattr(trace_context, "tenant_id", None),
                "workspace_id": getattr(trace_context, "workspace_id", None),
                "thread_id": getattr(trace_context, "thread_id", None),
                "trace_id": getattr(trace_context, "trace_id", None),
                "run_id": getattr(trace_context, "run_id", None),
                "request_id": tool_call_id,
            },
            event_type="tool.started",
            data={
                "tool_name": capability_id,
                "capability_id": capability_id,
                "connector_id": "hardware_runtime",
                "agent_activity": activity,
            },
            tool_call_id=tool_call_id,
        )


async def emit_tool_result(
    trace_context: Any,
    *,
    tool_call_id: str,
    status: str,
    summary: str,
    artifact_ids: Optional[List[str]] = None,
    capability_id: Optional[str] = None,
    arguments: Optional[Dict[str, Any]] = None,
    runtime_session: Optional[Dict[str, Any]] = None,
    runtime_target: Optional[str] = None,
    request_id: Optional[str] = None,
    action_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    session = _dict(runtime_session)
    resolved_capability_id = _text(capability_id) or _text(session.get("capability_id"))
    resolved_runtime_target = (
        _text(runtime_target)
        or _text(session.get("canonical_runtime_target"))
        or _text(session.get("runtime_target"))
    )
    resolved_session_id = _text(session.get("session_id")) or _text(session.get("runtime_session_id"))
    resolved_request_id = _text(request_id) or _text(session.get("request_id")) or _text(tool_call_id)
    resolved_action_id = _text(action_id) or _text(session.get("action_id"))
    result_metadata = {
        "hardware_action": True,
        "runtime_access_mode": _text(session.get("runtime_access_mode")) or None,
        "execution_mode": _text(session.get("execution_mode")) or None,
        "state": _text(session.get("state")) or None,
        "canonical_runtime_target": _text(session.get("canonical_runtime_target")) or None,
        **_dict(metadata),
    }
    result_metadata = {
        key: value
        for key, value in result_metadata.items()
        if value not in (None, "", [], {})
    }
    trace_data = {
        "status": _text(status),
        "summary": _text(summary),
        "artifact_ids": list(artifact_ids or []),
    }
    activity = _hardware_agent_activity(
        tool_call_id=tool_call_id,
        state=(
            _text(session.get("state"))
            or _text(status)
            or "running"
        ),
        summary=summary,
    )
    trace_data["agent_activity"] = activity
    for key, value in {
        "tool_name": resolved_capability_id,
        "capability_id": resolved_capability_id,
        "connector_id": "hardware_runtime",
        "runtime_session_id": resolved_session_id,
        "runtime_target": resolved_runtime_target,
        "request_id": resolved_request_id,
        "action_id": resolved_action_id,
    }.items():
        token = _text(value)
        if token:
            trace_data[key] = token
    if result_metadata:
        trace_data["metadata"] = result_metadata
    await agent_trace_service.emit_tool_result(
        trace_context,
        tool_call_id=tool_call_id,
        status=status,
        summary=summary,
        artifact_ids=artifact_ids or [],
        tool_name=resolved_capability_id or None,
        capability_id=resolved_capability_id or None,
        connector_id="hardware_runtime",
        args_preview=secret_redaction_service.sanitize_mapping(_dict(arguments)),
        runtime_session_id=resolved_session_id or None,
        runtime_target=resolved_runtime_target or None,
        request_id=resolved_request_id or None,
        action_id=resolved_action_id or None,
        metadata=result_metadata,
        agent_activity=activity,
    )
    await append_hardware_transcript_event(
        session,
        event_type="tool.result",
        data=trace_data,
        tool_call_id=tool_call_id,
    )


async def emit_artifacts(
    trace_context: Any,
    artifact_ids: List[str],
    capability_id: str,
    *,
    runtime_session: Optional[Dict[str, Any]] = None,
) -> None:
    for artifact_id in artifact_ids:
        title = f"{capability_id} artifact"
        await agent_trace_service.emit_artifact_created(
            trace_context,
            artifact_id=artifact_id,
            kind="hardware_action",
            title=title,
            mime_type=None,
        )
        if isinstance(runtime_session, dict):
            await append_hardware_transcript_event(
                runtime_session,
                event_type="artifact.created",
                data={
                    "kind": "hardware_action",
                    "title": title,
                    "artifact_id": artifact_id,
                    "capability_id": capability_id,
                },
                artifact_id=artifact_id,
            )


async def emit_approval_resolved(
    trace_context: Any,
    runtime_session: Dict[str, Any],
    *,
    approval_id: str,
    decision: str,
    actor: str,
    note: Optional[str],
) -> None:
    await agent_trace_service.emit_approval_resolved(
        trace_context,
        approval_id=approval_id,
        decision=decision,
        actor=_text(actor) or "user",
        note=note,
    )
    await append_hardware_transcript_event(
        runtime_session,
        event_type="approval.resolved",
        data={
            "approval_id": approval_id,
            "decision": decision,
            "actor": _text(actor) or "user",
        },
        approval_id=approval_id,
    )


async def emit_hardware_stop_transcript_event(
    runtime_session: Dict[str, Any],
    *,
    runtime_target: str,
    target_request_id: Optional[str],
    reason: Optional[str],
) -> None:
    session = _dict(runtime_session)
    activity = _hardware_agent_activity(
        tool_call_id=_text(target_request_id) or _text(session.get("request_id")) or _text(session.get("session_id")),
        state="terminated",
        summary="Hardware action stopped.",
    )
    await append_hardware_transcript_event(
        session,
        event_type="tool.result",
        data={
            "status": "terminated",
            "summary": "Hardware action stopped.",
            "agent_activity": activity,
            "tool_name": _text(session.get("capability_id")) or "tool.interrupt",
            "capability_id": _text(session.get("capability_id")) or "tool.interrupt",
            "connector_id": "hardware_runtime",
            "runtime_session_id": _text(session.get("session_id")) or None,
            "runtime_target": _text(runtime_target),
            "request_id": _text(session.get("request_id")) or None,
            "action_id": _text(session.get("action_id")) or "hardware.stop",
            "target_request_id": _text(target_request_id) or None,
            "reason": _text(reason) or "operator_requested_stop",
        },
        tool_call_id=_text(session.get("request_id")) or None,
    )
