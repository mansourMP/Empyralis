from __future__ import annotations

import time
from typing import Any, Callable, Dict, Iterator, List, Optional

from server_modules.direct_chat_intervention_service import build_intervention


def durable_run_preferred_response(
    message: str,
    *,
    run_action_fn: Callable[[str], Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "reply": "",
        "actions": [run_action_fn(message)],
        "mode": "answer_with_action",
        "interventions": [
            build_intervention(
                "run_offer",
                "Durable run available",
                detail="This request is better handled as a durable run so the system can execute it end-to-end.",
                severity="info",
                status="ready",
            )
        ],
    }


def run_handoff_execution_target(availability: Dict[str, Any]) -> str:
    connection_mode = str(availability.get("connection_mode") or "").strip().lower()
    if connection_mode == "local_companion":
        return "local_companion"
    return "auto"


def can_auto_start_run_handoff(availability: Dict[str, Any]) -> bool:
    if not isinstance(availability, dict):
        return False
    if not bool(availability.get("ai_ready")):
        return False
    connection_mode = str(availability.get("connection_mode") or "").strip().lower()
    if connection_mode == "byok":
        return False
    return True


def direct_chat_run_handoff_failure_payload(
    message: str,
    error_detail: str,
    *,
    run_action_fn: Callable[[str], Dict[str, Any]],
) -> Dict[str, Any]:
    detail = str(error_detail or "").strip() or "unknown_error"
    return {
        "reply": "",
        "actions": [run_action_fn(message)],
        "mode": "answer_with_action",
        "error": detail,
        "interventions": [
            build_intervention(
                "system_error",
                "Could not start durable run",
                detail=detail,
                severity="error",
                status="failed",
                code=detail,
            )
        ],
    }


def start_direct_chat_run_handoff(
    *,
    message: str,
    workspace_id: str,
    requested_provider: str,
    requested_model: str,
    thread_id: str,
    availability: Dict[str, Any],
    max_iterations: Optional[int] = None,
    start_run_request_fn: Callable[[Any], Dict[str, Any]],
    run_start_request_cls: Any,
    safe_positive_int_fn: Callable[[Any, int], int],
) -> Dict[str, Any]:
    connection_mode = str(availability.get("connection_mode") or "").strip().lower() or None
    execution_target = run_handoff_execution_target(availability)
    req = run_start_request_cls(
        engine="orion",
        workspace_id=workspace_id or "default",
        user_goal=str(message or "").strip(),
        max_iterations=safe_positive_int_fn(max_iterations, 0) if max_iterations is not None else None,
        provider=str(requested_provider or "").strip() or None,
        model=str(requested_model or "").strip() or None,
        metadata={
            "source": "operator_chat",
            "direct_chat": True,
            "chat_handoff": True,
            "thread_id": str(thread_id or "").strip() or None,
            "execution_target": execution_target,
            "connection_mode": connection_mode,
        },
    )
    return start_run_request_fn(req)


def direct_chat_run_handoff_reply(
    started: Dict[str, Any],
    *,
    open_action_fn: Callable[..., Dict[str, Any]],
) -> Dict[str, Any]:
    run_id = str(started.get("run_id") or "").strip()
    route = started.get("route") if isinstance(started.get("route"), dict) else {}
    selected_target = str(route.get("selected") or "").strip().lower()
    pending = started.get("pending_confirmation") if isinstance(started.get("pending_confirmation"), dict) else {}
    waiting_for_confirmation = bool(pending) or str(started.get("status") or "").strip().lower() == "waiting_for_input"

    if waiting_for_confirmation:
        actions = [
            open_action_fn("Open approvals", "/approvals", variant="primary"),
            open_action_fn("Open run", f"/runs/{run_id}", variant="secondary") if run_id else open_action_fn("Open runs", "/executions", variant="secondary"),
        ]
        detail = "Waiting for confirmation"
        intervention = build_intervention(
            "run_handoff",
            "Durable run is waiting for confirmation",
            detail="Local execution is paused until you approve the sensitive action.",
            severity="warning",
            status="waiting",
            run_id=run_id or None,
            metadata={"route": route, "pending_confirmation": pending if pending else None},
        )
    elif selected_target == "local_companion":
        actions = [
            open_action_fn("Open run", f"/runs/{run_id}", variant="primary") if run_id else open_action_fn("Open runs", "/executions", variant="primary"),
            open_action_fn("Open runs", "/executions", variant="secondary"),
        ]
        detail = "Queued for Gateway"
        intervention = build_intervention(
            "run_handoff",
            "Durable run started on your local machine",
            detail="The run has been handed off to Gateway.",
            severity="info",
            status="active",
            run_id=run_id or None,
            metadata={"route": route},
        )
    else:
        actions = [
            open_action_fn("Open run", f"/runs/{run_id}", variant="primary") if run_id else open_action_fn("Open runs", "/executions", variant="primary"),
            open_action_fn("Open runs", "/executions", variant="secondary"),
        ]
        detail = "Run started"
        intervention = build_intervention(
            "run_handoff",
            "Durable run started",
            detail="The task was handed off to the durable runtime.",
            severity="info",
            status="active",
            run_id=run_id or None,
            metadata={"route": route},
        )

    return {
        "reply": "",
        "actions": actions,
        "mode": "answer_with_action",
        "run_id": run_id or None,
        "detail": detail,
        "interventions": [intervention],
        "route": route if route else None,
        "pending_confirmation": pending if pending else None,
        "status": str(started.get("status") or "").strip() or None,
    }


def direct_chat_run_actions(
    run_id: str,
    *,
    waiting_for_confirmation: bool = False,
    open_action_fn: Callable[..., Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if waiting_for_confirmation:
        actions: List[Dict[str, Any]] = [open_action_fn("Open approvals", "/approvals", variant="primary")]
        if run_id:
            actions.append(open_action_fn("Open run", f"/runs/{run_id}", variant="secondary"))
        else:
            actions.append(open_action_fn("Open runs", "/executions", variant="secondary"))
        return actions
    if run_id:
        return [
            open_action_fn("Open run", f"/runs/{run_id}", variant="primary"),
            open_action_fn("Open runs", "/executions", variant="secondary"),
        ]
    return [open_action_fn("Open runs", "/executions", variant="primary")]


def direct_chat_run_snapshot(
    run_id: str,
    *,
    runs_mapping: Dict[str, Any],
    lookup_run_snapshot_fn: Callable[[str], Any],
    serialize_run_snapshot_fn: Callable[[str, Dict[str, Any]], Dict[str, Any]],
) -> tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    try:
        snapshot = lookup_run_snapshot_fn(run_id)
        if isinstance(snapshot, dict):
            run = runs_mapping.get(run_id)
            return (run if isinstance(run, dict) else None), snapshot
    except Exception:
        pass
    run = runs_mapping.get(run_id)
    if isinstance(run, dict):
        try:
            return run, serialize_run_snapshot_fn(run_id, run)
        except Exception:
            return run, {
                "run_id": run_id,
                "status": str(run.get("status") or "").strip() or "unknown",
                "requested_provider": None,
                "effective_provider": None,
                "requested_model": None,
                "effective_model": None,
                "fallback_used": False,
            }
    try:
        snapshot = lookup_run_snapshot_fn(run_id)
        return None, snapshot if isinstance(snapshot, dict) else {"run_id": run_id}
    except Exception:
        return None, {"run_id": run_id}


def direct_chat_run_event_to_step(run_id: str, event: Dict[str, Any]) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    event_name = str(event.get("event") or "").strip().lower()
    message = str(event.get("message") or "").strip()
    data = event.get("data") if isinstance(event.get("data"), dict) else {}

    if event_name in {"memory_context", "memory_write", "usage_masked", "action_policy_evaluated", "approval_skipped"}:
        return None, None
    if event_name == "local_queued":
        detail = str(data.get("preferred_runtime_label") or "").strip() or message
        return {"type": "step", "id": f"run-handoff:queue:{run_id}", "label": "Queued on your laptop", "detail": detail or None, "status": "done", "kind": "thinking"}, None
    if event_name == "local_claimed":
        detail = str(data.get("worker_id") or "").strip() or message
        return {"type": "step", "id": f"run-handoff:claim:{run_id}", "label": "Local machine picked up the run", "detail": detail or None, "status": "done", "kind": "thinking"}, None
    if event_name in {"local_heartbeat", "local_still_working", "workflow_node_start", "workflow_data_step", "workflow_tool_http", "workflow_tool_connector_action", "pack_phase", "orion_plan", "dag_node_start"}:
        return {"type": "step", "id": f"run-handoff:working:{run_id}", "label": "Working on your laptop", "detail": message or None, "status": "active", "kind": "thinking"}, None
    if event_name == "run_start":
        return {"type": "step", "id": f"run-handoff:started:{run_id}", "label": "Run started", "detail": message or None, "status": "done", "kind": "thinking"}, None
    if event_name in {"approval_requested", "approval_waiting"}:
        prompt = str(data.get("prompt") or "").strip() or message
        return {"type": "step", "id": f"run-handoff:approval:{run_id}", "label": "Waiting for confirmation", "detail": prompt or None, "status": "done", "kind": "thinking"}, None
    if event_name in {"run_error", "timeout", "run_stopped", "local_worker_lost"}:
        label = "Run failed"
        if event_name == "timeout":
            label = "Run timed out"
        elif event_name == "run_stopped":
            label = "Run stopped"
        elif event_name == "local_worker_lost":
            label = "Worker disconnected"
        return {"type": "step", "id": f"run-handoff:error:{run_id}", "label": label, "detail": message or None, "status": "error", "kind": "thinking"}, None
    if event_name in {"local_result", "orion_result", "pack_summary"}:
        reply_text = str(data.get("reply") or data.get("summary") or message).strip() or None
        return {"type": "step", "id": f"run-handoff:working:{run_id}", "label": "Working on your laptop", "detail": "Response ready", "status": "done", "kind": "thinking"}, reply_text
    if event_name == "run_complete":
        return {"type": "step", "id": f"run-handoff:working:{run_id}", "label": "Completed on your laptop", "detail": message or None, "status": "done", "kind": "thinking"}, None
    return None, None


def direct_chat_run_snapshot_to_step(run_id: str, snapshot: Dict[str, Any]) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
    status = str(snapshot.get("status") or "").strip().lower()
    if not status:
        return None, None
    selected_target = str(snapshot.get("execution_target_selected") or "").strip().lower()
    waiting_for_runtime = bool(snapshot.get("execution_target_waiting_for_runtime"))
    waiting_for_capacity = bool(snapshot.get("execution_target_waiting_for_capacity"))
    preferred_runtime_label = str(snapshot.get("execution_target_preferred_runtime_label") or "").strip()
    estimated_wait_band = str(snapshot.get("execution_target_estimated_wait_band") or "").strip()
    pending = (
        snapshot.get("pending_confirmation")
        if isinstance(snapshot.get("pending_confirmation"), dict)
        else snapshot.get("pending_approval")
        if isinstance(snapshot.get("pending_approval"), dict)
        else {}
    )
    prompt = str(pending.get("prompt") or "").strip()

    def _detail(*values: str) -> Optional[str]:
        for value in values:
            token = str(value or "").strip()
            if token:
                return token
        return None

    if status == "waiting_for_input":
        return f"waiting_for_input:{run_id}", {"type": "step", "id": f"run-handoff:approval:{run_id}", "label": "Waiting for confirmation", "detail": _detail(prompt, preferred_runtime_label), "status": "done", "kind": "thinking"}
    if status in {"queued_local", "queued", "starting"}:
        if waiting_for_runtime:
            return f"waiting_for_runtime:{run_id}", {"type": "step", "id": f"run-handoff:waiting-runtime:{run_id}", "label": "Waiting for your laptop", "detail": _detail(preferred_runtime_label, estimated_wait_band, "Local machine not ready yet"), "status": "active", "kind": "thinking"}
        if waiting_for_capacity:
            return f"waiting_for_capacity:{run_id}", {"type": "step", "id": f"run-handoff:waiting-capacity:{run_id}", "label": "Waiting for laptop capacity", "detail": _detail(preferred_runtime_label, estimated_wait_band, "Another task is using the local machine"), "status": "active", "kind": "thinking"}
        if selected_target == "local_companion" or status == "queued_local":
            return f"queued_local:{run_id}", {"type": "step", "id": f"run-handoff:queue:{run_id}", "label": "Queued on your laptop", "detail": _detail(preferred_runtime_label, estimated_wait_band), "status": "active", "kind": "thinking"}
        return f"queued:{run_id}", {"type": "step", "id": f"run-handoff:queue:{run_id}", "label": "Run queued", "detail": _detail(estimated_wait_band), "status": "active", "kind": "thinking"}
    if status in {"running", "running_local"}:
        label = "Working on your laptop" if selected_target == "local_companion" or status == "running_local" else "Working on the run"
        return f"running:{run_id}", {"type": "step", "id": f"run-handoff:working:{run_id}", "label": label, "detail": _detail(preferred_runtime_label), "status": "active", "kind": "thinking"}
    return None, None


def direct_chat_run_final_payload(
    *,
    run_id: str,
    run: Optional[Dict[str, Any]],
    snapshot: Dict[str, Any],
    requested_workspace_id: str,
    requested_provider: str,
    requested_model: str,
    reasoning_effort: Optional[str],
    connected_systems: List[str],
    tool_capabilities: List[Dict[str, Any]],
    fallback_reason: Optional[str],
    reply_override: Optional[str] = None,
    continuing: bool = False,
    build_context_used_fn: Callable[..., Dict[str, Any]],
    open_action_fn: Callable[..., Dict[str, Any]],
) -> Dict[str, Any]:
    status = str(snapshot.get("status") or (run.get("status") if isinstance(run, dict) else "") or "").strip().lower() or "unknown"
    pending = (
        run.get("pending_confirmation")
        if isinstance(run, dict) and isinstance(run.get("pending_confirmation"), dict)
        else snapshot.get("pending_confirmation")
        if isinstance(snapshot.get("pending_confirmation"), dict)
        else snapshot.get("pending_approval")
        if isinstance(snapshot.get("pending_approval"), dict)
        else {}
    )
    result_data = snapshot.get("result_data") if isinstance(snapshot.get("result_data"), dict) else {}
    selected_target = str(snapshot.get("execution_target_selected") or "").strip().lower()
    attempted_providers = str(result_data.get("attempted_providers") or "").strip()
    effective_provider = str(snapshot.get("effective_provider") or "").strip() or None
    effective_model = str(snapshot.get("effective_model") or "").strip() or None
    actual_fallback_reason = str(snapshot.get("fallback_reason") or fallback_reason or "").strip() or None
    base_reply = str(reply_override or snapshot.get("result_summary") or (run.get("result") if isinstance(run, dict) else "") or "").strip()

    interventions: List[Dict[str, Any]] = []
    if status == "completed":
        reply = base_reply
        actions = direct_chat_run_actions(run_id, open_action_fn=open_action_fn)
        error = ""
        if not reply:
            interventions.append(
                build_intervention(
                    "run_handoff",
                    "Durable run completed",
                    detail="The durable run finished successfully.",
                    severity="info",
                    status="completed",
                    run_id=run_id,
                    metadata={"snapshot_status": status},
                )
            )
    elif status == "waiting_for_input":
        prompt = str(pending.get("prompt") or "").strip()
        reply = ""
        actions = direct_chat_run_actions(run_id, waiting_for_confirmation=True, open_action_fn=open_action_fn)
        error = ""
        interventions.append(
            build_intervention(
                "run_handoff",
                "Durable run is waiting for confirmation",
                detail=prompt or "The run is paused until you approve the next sensitive action.",
                severity="warning",
                status="waiting",
                run_id=run_id,
                metadata={"pending_confirmation": pending if pending else None},
            )
        )
    elif continuing:
        reply = ""
        if bool(snapshot.get("execution_target_waiting_for_runtime")):
            detail = "The durable run is waiting for your laptop to become available."
            handoff_status = "waiting"
        elif bool(snapshot.get("execution_target_waiting_for_capacity")):
            detail = "The durable run is waiting for local machine capacity."
            handoff_status = "waiting"
        elif selected_target == "local_companion" and status in {"queued_local", "queued", "starting"}:
            detail = "The durable run is queued for your laptop."
            handoff_status = "waiting"
        elif selected_target == "local_companion":
            detail = "The durable run is still working on your laptop."
            handoff_status = "active"
        else:
            detail = "The durable run is still working."
            handoff_status = "active"
        actions = direct_chat_run_actions(run_id, open_action_fn=open_action_fn)
        error = ""
        interventions.append(
            build_intervention(
                "run_handoff",
                "Durable run in progress",
                detail=detail,
                severity="info",
                status=handoff_status,
                run_id=run_id,
                metadata={"snapshot_status": status, "execution_target_selected": selected_target},
            )
        )
    else:
        reply = base_reply
        actions = direct_chat_run_actions(run_id, open_action_fn=open_action_fn)
        error = status if status not in {"completed", "waiting_for_input"} else ""
        if not reply:
            interventions.append(
                build_intervention(
                    "system_error",
                    "Durable run ended with an unexpected status",
                    detail=f"Status: {status}",
                    severity="error",
                    status="failed",
                    run_id=run_id,
                    code=status,
                    metadata={"snapshot_status": status},
                )
            )

    return {
        "reply": reply,
        "actions": actions,
        "interventions": interventions,
        "mode": "answer_with_action" if actions else "answer",
        "usage_masked": snapshot.get("usage_masked") if isinstance(snapshot.get("usage_masked"), dict) else {},
        "provider": effective_provider,
        "model": effective_model,
        "attempted_providers": attempted_providers,
        "error": error,
        "context_used": build_context_used_fn(
            workspace_id=requested_workspace_id,
            requested_provider=requested_provider,
            effective_provider=effective_provider,
            requested_model=requested_model,
            effective_model=effective_model,
            reasoning_effort=reasoning_effort,
            connected_systems=connected_systems,
            tool_capabilities=tool_capabilities,
            prior_messages_used=False,
            history_mode="none",
            run_created=True,
            fallback_used=bool(snapshot.get("fallback_used")),
            fallback_reason=actual_fallback_reason,
        ),
    }


def stream_direct_chat_run_handoff(
    *,
    started_run: Dict[str, Any],
    requested_workspace_id: str,
    requested_provider: str,
    requested_model: str,
    reasoning_effort: Optional[str],
    connected_systems: List[str],
    tool_capabilities: List[Dict[str, Any]],
    fallback_reason: Optional[str],
    direct_chat_run_snapshot_fn: Callable[[str], tuple[Optional[Dict[str, Any]], Dict[str, Any]]],
    direct_chat_run_event_to_step_fn: Callable[[str, Dict[str, Any]], tuple[Optional[Dict[str, Any]], Optional[str]]],
    direct_chat_run_snapshot_to_step_fn: Callable[[str, Dict[str, Any]], tuple[Optional[str], Optional[Dict[str, Any]]]],
    direct_chat_run_final_payload_fn: Callable[..., Dict[str, Any]],
    open_action_fn: Callable[..., Dict[str, Any]],
    build_context_used_fn: Callable[..., Dict[str, Any]],
    live_window_seconds: float,
    poll_seconds: float,
    monotonic_fn: Callable[[], float] = time.monotonic,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Iterator[Dict[str, Any]]:
    run_id = str(started_run.get("run_id") or "").strip()
    if not run_id:
        yield {
            "type": "final",
            "payload": {
                "reply": "",
                "actions": [open_action_fn("Open runs", "/executions", variant="primary")],
                "interventions": [
                    build_intervention(
                        "system_error",
                        "Durable run did not return an id",
                        detail="The runtime accepted the handoff request but did not return a run identifier.",
                        severity="error",
                        status="failed",
                        code="missing_run_id",
                    )
                ],
                "mode": "answer_with_action",
                "error": "missing_run_id",
                "context_used": build_context_used_fn(
                    workspace_id=requested_workspace_id,
                    requested_provider=requested_provider,
                    effective_provider=None,
                    requested_model=requested_model,
                    effective_model=None,
                    reasoning_effort=reasoning_effort,
                    connected_systems=connected_systems,
                    tool_capabilities=tool_capabilities,
                    prior_messages_used=False,
                    history_mode="none",
                    run_created=False,
                    fallback_used=False,
                    fallback_reason=fallback_reason,
                ),
            },
        }
        return

    deadline = monotonic_fn() + live_window_seconds
    last_seq = 0
    reply_override: Optional[str] = None
    emitted_snapshot_step_keys: set[str] = set()

    while True:
        run, snapshot = direct_chat_run_snapshot_fn(run_id)
        events = run.get("events") if isinstance(run, dict) and isinstance(run.get("events"), list) else []
        for event in events:
            if not isinstance(event, dict):
                continue
            try:
                seq = int(event.get("seq") or 0)
            except Exception:
                seq = 0
            if seq <= last_seq:
                continue
            last_seq = seq
            step_payload, candidate_reply = direct_chat_run_event_to_step_fn(run_id, event)
            if candidate_reply and not reply_override:
                reply_override = candidate_reply
            if step_payload is not None:
                yield step_payload

        snapshot_step_key, snapshot_step_payload = direct_chat_run_snapshot_to_step_fn(run_id, snapshot)
        if snapshot_step_key and snapshot_step_payload is not None and snapshot_step_key not in emitted_snapshot_step_keys:
            emitted_snapshot_step_keys.add(snapshot_step_key)
            yield snapshot_step_payload

        status = str(snapshot.get("status") or "").strip().lower() or "unknown"
        if status in {"completed", "failed", "timeout", "waiting_for_input", "stopped", "cancelled"}:
            yield {
                "type": "final",
                "payload": direct_chat_run_final_payload_fn(
                    run_id=run_id,
                    run=run,
                    snapshot=snapshot,
                    requested_workspace_id=requested_workspace_id,
                    requested_provider=requested_provider,
                    requested_model=requested_model,
                    reasoning_effort=reasoning_effort,
                    connected_systems=connected_systems,
                    tool_capabilities=tool_capabilities,
                    fallback_reason=fallback_reason,
                    reply_override=reply_override,
                    continuing=False,
                ),
            }
            return

        if monotonic_fn() >= deadline:
            yield {
                "type": "final",
                "payload": direct_chat_run_final_payload_fn(
                    run_id=run_id,
                    run=run,
                    snapshot=snapshot,
                    requested_workspace_id=requested_workspace_id,
                    requested_provider=requested_provider,
                    requested_model=requested_model,
                    reasoning_effort=reasoning_effort,
                    connected_systems=connected_systems,
                    tool_capabilities=tool_capabilities,
                    fallback_reason=fallback_reason,
                    reply_override=reply_override,
                    continuing=True,
                ),
            }
            return

        sleep_fn(poll_seconds)
