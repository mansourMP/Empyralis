from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional

from fastapi import HTTPException
from server_modules import agent_action_metering_service, browser_checkpoint_service
from server_modules import machine_lease_service, usage_accounting_service
from server_modules import rust_runtime_kernel_client


@dataclass(slots=True)
class WorkerLease:
    worker_id: str
    run_id: str
    lease_id: str
    ttl_seconds: int
    note: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DispatchEnvelope:
    run_id: str
    worker_id: Optional[str]
    payload: Dict[str, Any] = field(default_factory=dict)


def _run_context(run: Mapping[str, Any]) -> Dict[str, Any]:
    context = run.get("context") if isinstance(run, Mapping) and isinstance(run.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    return {
        "tenant_id": str(context.get("tenant_id") or metadata.get("tenant_id") or "default").strip() or "default",
        "workspace_id": str(context.get("workspace_id") or metadata.get("workspace_id") or "default").strip() or "default",
    }


def _enforce_local_worker_decision(**payload: Any) -> Dict[str, Any]:
    operation = str(payload.get("operation") or "local_worker").strip() or "local_worker"
    allow_block_result = bool(payload.pop("allow_block_result", False))
    queue_empty = bool(payload.get("queue_empty"))
    expected_next_action = None
    if operation == "claim_run":
        expected_next_action = "return_backpressure" if queue_empty else "claim_local_run"
    elif operation == "worker_heartbeat":
        expected_next_action = "record_worker_heartbeat"
    elif operation == "run_heartbeat":
        expected_next_action = "record_run_heartbeat"
    elif operation == "complete_run":
        expected_next_action = "complete_local_run"
    elif operation == "pause_run":
        expected_next_action = "pause_local_run"
    elif operation == "fail_run":
        expected_next_action = "fail_local_run"
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "local-worker-decision",
            payload,
        )
        next_action = str(decision.get("next_action") or "").strip()
        if expected_next_action and next_action != expected_next_action:
            raise HTTPException(
                status_code=423,
                detail={
                    "error": "rust_local_worker_denied",
                    "operation": operation,
                    "reason": f"unexpected_next_action:{next_action or 'missing'}",
                },
            )
        return decision
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        if allow_block_result and isinstance(exc.result, dict):
            return dict(exc.result)
        reason = str(getattr(exc, "reason", "") or "local_worker_denied").strip()
        raise HTTPException(
            status_code=423,
            detail={
                "error": "rust_local_worker_denied",
                "operation": operation,
                "reason": reason,
            },
        ) from exc


def _raise_local_worker_gate_block(operation: str, reason: str) -> None:
    normalized_reason = str(reason or "local_worker_denied").strip()
    if normalized_reason == "local_run_not_owned_by_worker":
        raise HTTPException(status_code=403, detail="Worker does not own this local run.")
    if normalized_reason == "local_run_claim_missing":
        raise HTTPException(status_code=409, detail="Run is not claimed by Gateway.")
    raise HTTPException(
        status_code=423,
        detail={
            "error": "rust_local_worker_denied",
            "operation": operation,
            "reason": normalized_reason,
        },
    )


def _canonical_run_state_for_rust(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token in {"running", "running_local", "executing", "claimed", "in_progress"}:
        return "running"
    if token in {"queued", "queued_local", "starting", "planning", "machine_allocating"}:
        return "queued"
    if token in {"completed", "succeeded", "success"}:
        return "completed"
    if token in {"cancelled", "canceled"}:
        return "cancelled"
    if token in {"timeout", "timed_out"}:
        return "failed"
    return token or "queued"


def _enforce_run_state_transition_decision(
    *,
    operation: str,
    run_id: str,
    run: Mapping[str, Any],
    actor_id: Optional[str],
) -> Dict[str, Any]:
    context_scope = _run_context(run)
    expected_next_action = {
        "complete": "complete_state_transition",
        "fail": "fail_state_transition",
    }.get(str(operation or "").strip())
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "state-transition-decision",
            {
                "operation": operation,
                "entity_type": "run",
                "entity_id": run_id,
                "run_id": run_id,
                "actor_id": actor_id,
                "workspace_id": context_scope["workspace_id"],
                "tenant_id": context_scope["tenant_id"],
                "current_state": _canonical_run_state_for_rust(run.get("status") or run.get("state")),
            },
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        reason = str(getattr(exc, "reason", "") or "state_transition_denied").strip()
        raise HTTPException(
            status_code=423,
            detail={
                "error": "rust_state_transition_denied",
                "operation": operation,
                "reason": reason,
            },
        ) from exc
    next_action = str(decision.get("next_action") or "").strip()
    if expected_next_action and next_action != expected_next_action:
        raise HTTPException(
            status_code=423,
            detail={
                "error": "rust_state_transition_invalid_next_action",
                "operation": operation,
                "reason": f"unexpected_next_action:{next_action or 'missing'}",
            },
        )
    return decision


def claim_local_run(
    worker_id: str,
    *,
    required_capabilities: Optional[List[str]],
    local_queue_lock: Any,
    pending_run_ids: List[str],
    claimed_runs: Dict[str, Dict[str, Any]],
    worker_registry: Mapping[str, Any],
    runs_by_id: Mapping[str, Any],
    lease_seconds: int,
    cleanup_stale_local_claims_fn: Callable[[], Any],
    ordered_runtime_preferences_for_run_fn: Callable[[Dict[str, Any]], List[str]],
    best_online_preferred_runtime_fn: Callable[[List[str]], Optional[str]],
    required_capabilities_for_run_fn: Callable[[Dict[str, Any]], List[str]],
    normalize_capability_ids_fn: Callable[[Any], List[str]],
    persist_local_runtime_state_fn: Callable[[], Any],
    mark_local_worker_seen_fn: Callable[..., Any],
    now_iso_fn: Callable[[], str],
    parse_utc_ts_fn: Optional[Callable[[Any], Any]] = None,
    now_fn: Optional[Callable[[], Any]] = None,
) -> Optional[str]:
    # ── Stage 4A: resolve workspace from pending runs ──────────────────
    resolved_ws = ""
    if pending_run_ids and runs_by_id:
        first_run_id = pending_run_ids[0] if pending_run_ids else None
        if first_run_id and first_run_id in runs_by_id:
            first_run = runs_by_id[first_run_id]
            if isinstance(first_run, dict):
                resolved_ws = str(
                    first_run.get("workspace_id")
                    or (first_run.get("context") or {}).get("workspace_id")
                    or ""
                ).strip()

    if not resolved_ws:
        from server_modules import workspace_scope as _ws
        resolved_ws = _ws.resolve_workspace(
            None, site="worker_dispatch_service:claim_run"
        )
        # If still unscoped, refuse the job
        if resolved_ws.startswith("_unscoped_"):
            return None  # job refused — no workspace scope

    _enforce_local_worker_decision(
        operation="claim_run",
        workspace_id=resolved_ws,
        worker_id=worker_id,
        actor_role="worker",
        required_capabilities=list(required_capabilities or []),
        queue_empty=not bool(pending_run_ids),
        local_companion_enabled=True,
    )
    return machine_lease_service.claim_local_machine_lease(
        worker_id,
        required_capabilities=required_capabilities,
        local_queue_lock=local_queue_lock,
        pending_run_ids=pending_run_ids,
        claimed_runs=claimed_runs,
        worker_registry=worker_registry,
        runs_by_id=runs_by_id,
        lease_seconds=lease_seconds,
        cleanup_stale_local_claims_fn=cleanup_stale_local_claims_fn,
        ordered_runtime_preferences_for_run_fn=ordered_runtime_preferences_for_run_fn,
        best_online_preferred_runtime_fn=best_online_preferred_runtime_fn,
        required_capabilities_for_run_fn=required_capabilities_for_run_fn,
        normalize_capability_ids_fn=normalize_capability_ids_fn,
        persist_local_runtime_state_fn=persist_local_runtime_state_fn,
        mark_local_worker_seen_fn=mark_local_worker_seen_fn,
        now_iso_fn=now_iso_fn,
        parse_utc_ts_fn=parse_utc_ts_fn,
        now_fn=now_fn,
    )


def heartbeat_local_worker(
    worker_id: str,
    *,
    current_run_id: Optional[str],
    note: Optional[str],
    runs_by_id: Mapping[str, Any],
    local_queue_lock: Any,
    claimed_runs: Dict[str, Dict[str, Any]],
    mark_local_worker_seen_fn: Callable[..., Any],
    maybe_emit_local_still_working_fn: Callable[..., bool],
    persist_local_runtime_state_fn: Callable[[], Any],
    utc_now_iso_fn: Callable[[], str],
    touch_claim_heartbeat_fn: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    worker = str(worker_id or "").strip()
    if not worker:
        raise HTTPException(status_code=400, detail="worker_id is required.")

    current_run = str(current_run_id or "").strip()
    note_text = str(note or "").strip()
    workspace_id = "default"
    if current_run:
        run = runs_by_id.get(current_run)
        if isinstance(run, Mapping):
            workspace_id = _run_context(run)["workspace_id"]
    _enforce_local_worker_decision(
        operation="worker_heartbeat",
        workspace_id=workspace_id,
        worker_id=worker,
        run_id=current_run or None,
        actor_role="worker",
        local_companion_enabled=True,
    )
    mark_local_worker_seen_fn(worker, current_run or None, "busy" if current_run else "idle", note=note_text or None)

    if current_run:
        run = runs_by_id.get(current_run)
        if isinstance(run, dict):
            should_persist = False
            with local_queue_lock:
                claim = claimed_runs.get(current_run)
                if isinstance(claim, dict) and str(claim.get("worker_id") or "") == worker:
                    now_iso = utc_now_iso_fn()
                    claim["last_heartbeat_at"] = now_iso
                    claimed_runs[current_run] = claim
                    run["local_last_heartbeat_at"] = now_iso
                    maybe_emit_local_still_working_fn(current_run, run, claim, note=note_text or None)
                    should_persist = True
            if should_persist:
                persist_local_runtime_state_fn()
            if callable(touch_claim_heartbeat_fn):
                touch_claim_heartbeat_fn(
                    current_run,
                    worker,
                    note=note_text or None,
                    progress=bool(note_text),
                )

    return {
        "status": "ok",
        "worker_id": worker,
        "current_run_id": current_run or None,
        "last_seen_at": utc_now_iso_fn(),
    }


def heartbeat_local_run(
    run_id: str,
    *,
    worker_id: Optional[str],
    note: Optional[str],
    runs_by_id: Mapping[str, Any],
    local_queue_lock: Any,
    claimed_runs: Dict[str, Dict[str, Any]],
    maybe_emit_local_still_working_fn: Callable[..., bool],
    mark_local_worker_seen_fn: Callable[..., Any],
    utc_now_iso_fn: Callable[[], str],
    touch_claim_heartbeat_fn: Optional[Callable[..., Any]] = None,
    progress_event: bool = False,
) -> Dict[str, Any]:
    run = runs_by_id.get(run_id)
    if not isinstance(run, dict):
        raise HTTPException(status_code=404, detail="Run ID not found")

    note_text = str(note or "").strip()
    incoming_worker = str(worker_id or "").strip()
    context_scope = _run_context(run)
    with local_queue_lock:
        claim = claimed_runs.get(run_id)
        resolved_worker = incoming_worker or (
            str(claim.get("worker_id") or "").strip() if isinstance(claim, dict) else ""
        )
        local_worker_decision = _enforce_local_worker_decision(
            operation="run_heartbeat",
            workspace_id=context_scope["workspace_id"],
            worker_id=incoming_worker or resolved_worker,
            current_worker_id=str(claim.get("worker_id") or "").strip() if isinstance(claim, dict) else "",
            run_id=run_id,
            run_status=str(run.get("status") or "claimed").strip() or "claimed",
            actor_role="worker",
            local_companion_enabled=True,
            claim_missing=not isinstance(claim, dict),
            allow_block_result=True,
        )
        if str(local_worker_decision.get("decision") or "").strip().lower() == "block":
            _raise_local_worker_gate_block("run_heartbeat", str(local_worker_decision.get("reason") or ""))
        now_iso = utc_now_iso_fn()
        claim["last_heartbeat_at"] = now_iso
        maybe_emit_local_still_working_fn(run_id, run, claim, note=note_text or None)
        claimed_runs[run_id] = claim

    run["local_last_heartbeat_at"] = now_iso
    if resolved_worker:
        mark_local_worker_seen_fn(resolved_worker, run_id, "busy", note=note_text or None)
        if callable(touch_claim_heartbeat_fn):
            touch_claim_heartbeat_fn(
                run_id,
                resolved_worker,
                note=note_text or None,
                progress=bool(note_text or progress_event),
            )
    return {"status": "ok", "run_id": run_id, "last_heartbeat_at": now_iso}


def _enforce_local_queue_terminal_transition(
    *,
    operation: str,
    run_id: str,
    run: Mapping[str, Any],
    worker_id: Optional[str],
    claim: Any,
) -> Dict[str, Any]:
    requester = str(worker_id or "").strip()
    holder = ""
    if isinstance(claim, Mapping):
        holder = str(claim.get("worker_id") or claim.get("machine_id") or "").strip()
    return machine_lease_service.enforce_queue_transition_decision(
        operation=operation,
        run_id=run_id,
        requester_id=requester or holder,
        lease_holder_id=holder,
        status=str(run.get("status") or "claimed").strip() or "claimed",
    )


def complete_local_run(
    run_id: str,
    *,
    worker_id: Optional[str],
    result_text: Optional[str],
    result_data: Optional[Dict[str, Any]],
    usage_masked: Optional[Dict[str, Any]],
    runs_by_id: Mapping[str, Any],
    local_queue_lock: Any,
    claimed_runs: Dict[str, Dict[str, Any]],
    emit_log_fn: Callable[..., Any],
    mark_local_worker_seen_fn: Callable[..., Any],
    set_run_status_fn: Callable[[str, str], Any],
    persist_run_memory_fn: Callable[[str, Dict[str, Any]], Any],
    persist_local_runtime_state_fn: Optional[Callable[[], Any]] = None,
) -> Dict[str, Any]:
    run = runs_by_id.get(run_id)
    if not isinstance(run, dict):
        raise HTTPException(status_code=404, detail="Run ID not found")

    with local_queue_lock:
        claim = claimed_runs.get(run_id)
        incoming_worker = str(worker_id or "").strip()
        resolved_worker = incoming_worker or (
            str(claim.get("worker_id") or "").strip() if isinstance(claim, dict) else ""
        )

    status = str(run.get("status") or "").strip().lower()
    context_scope = _run_context(run)
    local_worker_decision = _enforce_local_worker_decision(
        operation="complete_run",
        workspace_id=context_scope["workspace_id"],
        worker_id=incoming_worker or resolved_worker or worker_id,
        current_worker_id=str((claim or {}).get("worker_id") or "").strip() if isinstance(claim, dict) else "",
        run_id=run_id,
        run_status=status or "claimed",
        actor_role="worker",
        local_companion_enabled=True,
        claim_missing=not isinstance(claim, dict),
        allow_block_result=True,
    )
    if str(local_worker_decision.get("decision") or "").strip().lower() == "block":
        _raise_local_worker_gate_block("complete_run", str(local_worker_decision.get("reason") or ""))
    state_decision = _enforce_run_state_transition_decision(
        operation="complete",
        run_id=run_id,
        run=run,
        actor_id=resolved_worker or worker_id,
    )
    queue_decision = _enforce_local_queue_terminal_transition(
        operation="complete",
        run_id=run_id,
        run=run,
        worker_id=resolved_worker or worker_id,
        claim=claim,
    )
    if (
        str(state_decision.get("reason") or "").strip().lower() == "state_already_terminal"
        or str(queue_decision.get("reason") or "").strip().lower() == "queue_item_already_terminal"
    ):
        return {"status": "ok", "run_id": run_id, "already_terminal": True}

    if isinstance(result_data, dict):
        run["result_data"] = result_data
    run.pop("local_execution_checkpoint", None)
    run.pop("wait_reason", None)
    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    metadata.pop("local_execution_checkpoint", None)
    metadata.pop("local_execution_resume_supported", None)
    metadata.pop("manual_takeover", None)
    context["metadata"] = metadata
    run["context"] = context
    if isinstance(result_data, dict):
        outputs = result_data.get("outputs") if isinstance(result_data.get("outputs"), dict) else {}
        actions = outputs.get("actions") if isinstance(outputs.get("actions"), list) else []
        tenant_id = str(context.get("tenant_id") or metadata.get("tenant_id") or "default").strip() or "default"
        workspace_id = str(context.get("workspace_id") or metadata.get("workspace_id") or "default").strip() or "default"
        for index, action in enumerate(actions):
            if not isinstance(action, dict):
                continue
            tool_id = str(action.get("tool") or action.get("action") or "local_worker").strip() or "local_worker"
            status_token = str(action.get("status") or "completed").strip().lower() or "completed"
            if status_token not in {"completed", "failed", "blocked"}:
                status_token = "completed"
            record_fn = (
                agent_action_metering_service.record_failed_sync
                if status_token == "failed"
                else agent_action_metering_service.record_blocked_sync
                if status_token == "blocked"
                else agent_action_metering_service.record_completed_sync
            )
            record_fn(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                surface=str(metadata.get("surface") or "sage").strip().lower() or "sage",
                source_surface="local_worker",
                action_domain="runtime",
                action_type="execute",
                action_name=tool_id,
                tool_kind="local_worker_tool",
                tool_id=tool_id,
                runtime_target="local_companion",
                capability_id=str(action.get("capability_id") or tool_id).strip() or tool_id,
                risk_level=str(action.get("risk_level") or "").strip() or None,
                payer="local",
                billing_mode="transparency",
                thread_id=str(context.get("thread_id") or metadata.get("thread_id") or "").strip() or None,
                run_id=run_id,
                agent_id=str(context.get("agent_id") or metadata.get("agent_id") or "").strip() or None,
                input_summary=str(action.get("summary") or action.get("label") or tool_id).strip(),
                output_summary=str(action.get("summary") or action.get("status") or "").strip(),
                error_code=str(action.get("error_code") or "").strip() or None,
                source_table="local_worker_actions",
                source_event_id=agent_action_metering_service.build_source_event_id(
                    source_surface="local_worker",
                    run_id=run_id,
                    node_id=index,
                    action_name=tool_id,
                ),
                metadata={"worker_id": resolved_worker, "step_index": index},
            )
    if isinstance(usage_masked, dict):
        run["usage_masked"] = usage_masked
        usage_accounting = usage_accounting_service.accounting_record_from_snapshot(
            {
                "run_id": run_id,
                "status": "completed",
                "created_at": run.get("created_at"),
                "updated_at": run.get("updated_at"),
                "completed_at": run.get("completed_at"),
                "context": context,
                "usage_masked": usage_masked,
            }
        )
        if isinstance(usage_accounting, dict):
            run["usage_accounting"] = usage_accounting

    resolved_text = str(result_text or "").strip()
    if not resolved_text and isinstance(result_data, dict):
        resolved_text = str(result_data.get("summary") or "").strip()
    if not resolved_text:
        resolved_text = "Gateway run completed."
    run["result"] = resolved_text
    machine_lease_service.clear_active_machine_lease_binding(run)

    emit_log_fn(run["logs"], "info", resolved_text, event="local_result", data=result_data if isinstance(result_data, dict) else None)
    emit_log_fn(run["logs"], "info", "Run completed by Local Companion.", event="run_complete")
    release = machine_lease_service.release_machine_lease_claim(
        run_id,
        worker_id=resolved_worker or worker_id,
        local_queue_lock=local_queue_lock,
        claimed_runs=claimed_runs,
        persist_local_runtime_state_fn=persist_local_runtime_state_fn,
        mark_local_worker_seen_fn=mark_local_worker_seen_fn,
        status_hint="idle",
        note="completed_run",
    )
    if not bool(release.get("released")) and resolved_worker:
        mark_local_worker_seen_fn(resolved_worker, None, "idle", note="completed_run")
    set_run_status_fn(run_id, "completed")
    try:
        persist_run_memory_fn(run_id, run)
    except Exception:
        pass
    run["logs"].put(None)
    return {"status": "ok", "run_id": run_id}


def pause_local_run(
    run_id: str,
    *,
    worker_id: Optional[str],
    result_text: Optional[str],
    result_data: Optional[Dict[str, Any]],
    browser_checkpoint: Optional[Dict[str, Any]],
    wait_reason: Optional[str],
    runs_by_id: Mapping[str, Any],
    local_queue_lock: Any,
    claimed_runs: Dict[str, Dict[str, Any]],
    emit_log_fn: Callable[..., Any],
    mark_local_worker_seen_fn: Callable[..., Any],
    set_run_status_fn: Callable[[str, str], Any],
    persist_local_runtime_state_fn: Callable[[], Any],
) -> Dict[str, Any]:
    run = runs_by_id.get(run_id)
    if not isinstance(run, dict):
        raise HTTPException(status_code=404, detail="Run ID not found")

    context_scope = _run_context(run)
    with local_queue_lock:
        claim = claimed_runs.get(run_id)
        incoming_worker = str(worker_id or "").strip()
        resolved_worker = incoming_worker or (
            str(claim.get("worker_id") or "").strip() if isinstance(claim, dict) else ""
        )
    local_worker_decision = _enforce_local_worker_decision(
        operation="pause_run",
        workspace_id=context_scope["workspace_id"],
        worker_id=incoming_worker or resolved_worker or worker_id,
        current_worker_id=str((claim or {}).get("worker_id") or "").strip() if isinstance(claim, dict) else "",
        run_id=run_id,
        run_status=str(run.get("status") or "claimed").strip() or "claimed",
        actor_role="worker",
        local_companion_enabled=True,
        claim_missing=not isinstance(claim, dict),
        allow_block_result=True,
    )
    if str(local_worker_decision.get("decision") or "").strip().lower() == "block":
        _raise_local_worker_gate_block("pause_run", str(local_worker_decision.get("reason") or ""))
    release = machine_lease_service.release_machine_lease_claim(
        run_id,
        worker_id=worker_id,
        local_queue_lock=local_queue_lock,
        claimed_runs=claimed_runs,
        persist_local_runtime_state_fn=persist_local_runtime_state_fn,
        mark_local_worker_seen_fn=mark_local_worker_seen_fn,
        status_hint="idle",
        note="paused_waiting_for_input",
    )
    released_worker = str(release.get("resolved_worker") or "").strip()
    if released_worker:
        resolved_worker = released_worker

    pause_data = dict(result_data) if isinstance(result_data, dict) else {}
    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    local_execution_checkpoint = run.get("local_execution_checkpoint") if isinstance(run.get("local_execution_checkpoint"), dict) else None
    if not isinstance(local_execution_checkpoint, dict):
        metadata_checkpoint = metadata.get("local_execution_checkpoint")
        if isinstance(metadata_checkpoint, dict) and metadata_checkpoint:
            local_execution_checkpoint = dict(metadata_checkpoint)
    if not isinstance(local_execution_checkpoint, dict) or not local_execution_checkpoint:
        pack_inputs = metadata.get("pack_inputs") if isinstance(metadata.get("pack_inputs"), dict) else {}
        operations = pack_inputs.get("operations") if isinstance(pack_inputs.get("operations"), list) else []
        if operations:
            local_execution_checkpoint = {
                "kind": "local_execution_v1",
                "next_operation_index": 0,
                "total_operations": len(operations),
                "phase": "planned",
                "mode": "observing",
            }
            run["local_execution_checkpoint"] = local_execution_checkpoint
    if isinstance(local_execution_checkpoint, dict) and local_execution_checkpoint:
        pause_data.setdefault("local_execution_checkpoint", dict(local_execution_checkpoint))
        pause_data.setdefault("resume_available", True)
        metadata["local_execution_checkpoint"] = dict(local_execution_checkpoint)
        metadata["local_execution_resume_supported"] = True
    checkpoint = browser_checkpoint_service.attach_browser_checkpoint(
        run,
        browser_checkpoint,
        metadata=metadata,
    )
    if checkpoint:
        pause_data.setdefault("resume_available", True)

    resolved_text = str(result_text or "").strip()
    if not resolved_text and pause_data:
        resolved_text = str(pause_data.get("summary") or "").strip()
    if not resolved_text:
        resolved_text = "Gateway paused and is waiting for human input."
    run["result"] = resolved_text
    machine_lease_service.clear_active_machine_lease_binding(run)
    run.pop("_resume_after_confirmation_scheduled", None)

    resolved_wait_reason = str(wait_reason or "").strip() or "human_unblock_required"
    manual_takeover = bool(pause_data.get("manual_takeover")) or resolved_wait_reason == "manual_takeover_requested"
    if manual_takeover:
        pause_data["manual_takeover"] = True
    if pause_data:
        pause_data.setdefault("summary", resolved_text)
        pause_data.setdefault("pause_reason", resolved_wait_reason)
        run["result_data"] = pause_data
    run["wait_reason"] = resolved_wait_reason
    metadata["manual_takeover"] = manual_takeover
    context["metadata"] = metadata
    run["context"] = context
    emit_log_fn(
        run["logs"],
        "warn",
        resolved_text,
        event="local_pause_required",
        data={
            "run_id": run_id,
            "wait_reason": resolved_wait_reason,
            **browser_checkpoint_service.checkpoint_summary(checkpoint),
        },
    )
    if manual_takeover:
        checkpoint = pause_data.get("local_execution_checkpoint") if isinstance(pause_data.get("local_execution_checkpoint"), dict) else {}
        emit_log_fn(
            run["logs"],
            "warn",
            "Human now has control. AI execution is paused.",
            event="computer_action",
            data={
                "schema": "empyralis.computer_action.v1",
                "mode": "takeover",
                "phase": "paused",
                "tool": "computer_control",
                "action_type": "manual_takeover",
                "label": "Human now has control",
                "reason": resolved_text,
                "detail": "Pause requested by the operator. No further computer-control actions will execute until resume.",
                "status": "waiting_for_input",
                "success": True,
                "step_number": (int(checkpoint.get("next_operation_index") or 0) + 1) if isinstance(checkpoint.get("next_operation_index"), int) else None,
                "step_total": int(checkpoint.get("total_operations") or 0) or None,
            },
        )
    set_run_status_fn(run_id, "waiting_for_input")
    return {"status": "ok", "run_id": run_id, "waiting_for_input": True}


def fail_local_run(
    run_id: str,
    *,
    worker_id: Optional[str],
    error: Optional[str],
    runs_by_id: Mapping[str, Any],
    local_queue_lock: Any,
    claimed_runs: Dict[str, Dict[str, Any]],
    emit_log_fn: Callable[..., Any],
    mark_local_worker_seen_fn: Callable[..., Any],
    set_run_status_fn: Callable[[str, str], Any],
    persist_local_runtime_state_fn: Optional[Callable[[], Any]] = None,
) -> Dict[str, Any]:
    run = runs_by_id.get(run_id)
    if not isinstance(run, dict):
        raise HTTPException(status_code=404, detail="Run ID not found")

    with local_queue_lock:
        claim = claimed_runs.get(run_id)
        incoming_worker = str(worker_id or "").strip()
        resolved_worker = incoming_worker or (
            str(claim.get("worker_id") or "").strip() if isinstance(claim, dict) else ""
        )

    status = str(run.get("status") or "").strip().lower()
    context_scope = _run_context(run)
    local_worker_decision = _enforce_local_worker_decision(
        operation="fail_run",
        workspace_id=context_scope["workspace_id"],
        worker_id=incoming_worker or resolved_worker or worker_id,
        current_worker_id=str((claim or {}).get("worker_id") or "").strip() if isinstance(claim, dict) else "",
        run_id=run_id,
        run_status=status or "claimed",
        actor_role="worker",
        local_companion_enabled=True,
        claim_missing=not isinstance(claim, dict),
        allow_block_result=True,
    )
    if str(local_worker_decision.get("decision") or "").strip().lower() == "block":
        _raise_local_worker_gate_block("fail_run", str(local_worker_decision.get("reason") or ""))
    state_decision = _enforce_run_state_transition_decision(
        operation="fail",
        run_id=run_id,
        run=run,
        actor_id=resolved_worker or worker_id,
    )
    queue_decision = _enforce_local_queue_terminal_transition(
        operation="fail",
        run_id=run_id,
        run=run,
        worker_id=resolved_worker or worker_id,
        claim=claim,
    )
    if (
        str(state_decision.get("reason") or "").strip().lower() == "state_already_terminal"
        or str(queue_decision.get("reason") or "").strip().lower() == "queue_item_already_terminal"
    ):
        return {"status": "ok", "run_id": run_id, "already_terminal": True}

    message = (
        str(error or "").strip()
        or str(run.get("interrupt_reason") or "").strip()
        or "Gateway run failed."
    )
    run["result"] = message
    run.pop("wait_reason", None)
    machine_lease_service.clear_active_machine_lease_binding(run)
    interrupt_requested = bool(run.get("interrupt_requested"))
    emit_log_fn(
        run["logs"],
        "error",
        message[:1200],
        event="run_interrupted" if interrupt_requested else "run_error",
        data={
            "interrupt_requested": interrupt_requested,
            "interrupt_scope": str(run.get("interrupt_scope") or "").strip().lower() or None,
            "machine_id": str(run.get("interrupt_machine_id") or run.get("machine_id") or "").strip() or None,
            "runtime_id": str(run.get("interrupt_runtime_id") or run.get("local_worker_id") or "").strip() or None,
        }
        if interrupt_requested
        else None,
    )
    if resolved_worker:
        release = machine_lease_service.release_machine_lease_claim(
            run_id,
            worker_id=resolved_worker,
            local_queue_lock=local_queue_lock,
            claimed_runs=claimed_runs,
            persist_local_runtime_state_fn=persist_local_runtime_state_fn,
            mark_local_worker_seen_fn=mark_local_worker_seen_fn,
            status_hint="idle",
            note="failed_run",
        )
        if not bool(release.get("released")):
            mark_local_worker_seen_fn(resolved_worker, None, "idle", note="failed_run")
    set_run_status_fn(run_id, "failed")
    run["logs"].put(None)
    return {"status": "ok", "run_id": run_id}
