from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from server_modules import auth as auth_module
from server_modules import control_plane_repository


router = APIRouter()
get_current_user = auth_module.get_current_user


def _resolve_trace_scope(current_user: Any, workspace_id: str) -> tuple[str, str]:
    resolved_workspace_id = auth_module.enforce_workspace_access(
        current_user,
        workspace_id,
        minimum_role="viewer",
    )
    resolved_tenant_id = auth_module.workspace_tenant_id(current_user, resolved_workspace_id)
    return resolved_workspace_id, resolved_tenant_id


def _canonical_trace_event(event: Dict[str, Any]) -> Dict[str, Any]:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    return {
        "id": str(event.get("id") or "").strip(),
        "trace_id": str(event.get("trace_id") or "").strip() or None,
        "seq": int(event.get("seq") or 0),
        "ts": event.get("ts"),
        "event_type": str(event.get("event_type") or "").strip() or None,
        "persisted": bool(event.get("persisted")),
        "agent_id": str(event.get("agent_id") or "").strip() or None,
        "parent_id": str(event.get("parent_id") or "").strip() or None,
        "item_id": str(event.get("item_id") or "").strip() or None,
        "tool_call_id": str(event.get("tool_call_id") or "").strip() or None,
        "child_run_id": str(event.get("child_run_id") or "").strip() or None,
        "approval_id": str(event.get("approval_id") or "").strip() or None,
        "artifact_id": str(event.get("artifact_id") or "").strip() or None,
        "data": dict(payload),
    }


def _terminal_trace_event(event: Dict[str, Any]) -> bool:
    event_type = str(event.get("event_type") or "").strip().lower()
    return event_type in {"trace.completed", "trace.failed"}


async def _load_trace_detail(
    *,
    trace_id: str,
    tenant_id: str,
    workspace_id: str,
    persisted_only: bool = True,
    limit: int = 5000,
) -> tuple[Optional[Dict[str, Any]], list[Dict[str, Any]]]:
    trace = await control_plane_repository.get_agent_trace_by_id(
        trace_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    if not isinstance(trace, dict):
        return None, []
    events = await control_plane_repository.get_agent_trace_events(
        trace_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        persisted_only=persisted_only,
        limit=limit,
    )
    canonical_events = [
        _canonical_trace_event(item)
        for item in (events or [])
        if isinstance(item, dict)
    ]
    return trace, canonical_events


async def _iter_trace_stream(
    *,
    request: Request,
    trace_id: str,
    tenant_id: str,
    workspace_id: str,
    poll_seconds: float,
    event_limit: int,
) -> AsyncIterator[Dict[str, str]]:
    _, initial_events = await _load_trace_detail(
        trace_id=trace_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        persisted_only=True,
        limit=event_limit,
    )
    last_seq = 0
    for event in initial_events:
        last_seq = max(last_seq, int(event.get("seq") or 0))
        yield {
            "event": "trace",
            "data": json.dumps(event, ensure_ascii=True),
        }
        if _terminal_trace_event(event):
            return

    safe_poll_seconds = max(0.05, min(float(poll_seconds or 0.5), 5.0))
    while True:
        if await request.is_disconnected():
            return
        await asyncio.sleep(safe_poll_seconds)
        _, current_events = await _load_trace_detail(
            trace_id=trace_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            persisted_only=True,
            limit=event_limit,
        )
        fresh_events = [
            item for item in current_events
            if int(item.get("seq") or 0) > last_seq
        ]
        for event in fresh_events:
            last_seq = max(last_seq, int(event.get("seq") or 0))
            yield {
                "event": "trace",
                "data": json.dumps(event, ensure_ascii=True),
            }
            if _terminal_trace_event(event):
                return


@router.get("/agent-traces")
async def list_agent_traces(
    workspace_id: str,
    thread_id: Optional[str] = None,
    run_id: Optional[str] = None,
    surface: Optional[str] = None,
    outcome: Optional[str] = None,
    root_agent_id: Optional[str] = None,
    limit: int = 100,
    current_user=Depends(get_current_user),
):
    resolved_workspace_id, tenant_id = _resolve_trace_scope(current_user, workspace_id)
    safe_limit = max(1, min(int(limit or 100), 200))
    items = await control_plane_repository.list_agent_traces(
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        thread_id=thread_id,
        run_id=run_id,
        surface=surface,
        outcome=outcome,
        root_agent_id=root_agent_id,
        limit=safe_limit,
    )
    return {
        "workspace_id": resolved_workspace_id,
        "items": list(items or []),
        "count": len(items or []),
        "limit": safe_limit,
        "filters": {
            "thread_id": str(thread_id or "").strip() or None,
            "run_id": str(run_id or "").strip() or None,
            "surface": str(surface or "").strip().lower() or None,
            "outcome": str(outcome or "").strip().lower() or None,
            "root_agent_id": str(root_agent_id or "").strip() or None,
        },
    }


@router.get("/agent-traces/{trace_id}")
async def get_agent_trace_detail(
    trace_id: str,
    workspace_id: str,
    current_user=Depends(get_current_user),
):
    resolved_workspace_id, tenant_id = _resolve_trace_scope(current_user, workspace_id)
    trace, events = await _load_trace_detail(
        trace_id=trace_id,
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        persisted_only=True,
    )
    if not isinstance(trace, dict):
        raise HTTPException(status_code=404, detail="Trace not found.")
    return {
        "trace": trace,
        "events": events,
    }


@router.get("/agent-traces/{trace_id}/stream")
async def stream_agent_trace(
    trace_id: str,
    request: Request,
    workspace_id: str,
    poll_seconds: float = 0.35,
    event_limit: int = 5000,
    current_user=Depends(get_current_user),
):
    resolved_workspace_id, tenant_id = _resolve_trace_scope(current_user, workspace_id)
    trace = await control_plane_repository.get_agent_trace_by_id(
        trace_id,
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
    )
    if not isinstance(trace, dict):
        raise HTTPException(status_code=404, detail="Trace not found.")
    return EventSourceResponse(
        _iter_trace_stream(
            request=request,
            trace_id=trace_id,
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            poll_seconds=poll_seconds,
            event_limit=max(1, min(int(event_limit or 5000), 10000)),
        ),
        ping=15,
    )
