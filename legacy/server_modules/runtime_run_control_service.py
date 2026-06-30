from __future__ import annotations

from typing import Any, Callable

from fastapi import HTTPException
from server_modules import browser_checkpoint_service, rust_runtime_kernel_client


def _run_control_scope(run: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str, str, str]:
    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    workspace_id = str(
        run.get("workspace_id")
        or context.get("workspace_id")
        or metadata.get("workspace_id")
        or "default"
    ).strip() or "default"
    tenant_id = str(
        run.get("tenant_id")
        or context.get("tenant_id")
        or metadata.get("tenant_id")
        or "default"
    ).strip() or "default"
    run_status = str(
        run.get("status")
        or run.get("state")
        or context.get("status")
        or metadata.get("status")
        or ""
    ).strip()
    return context, metadata, workspace_id, tenant_id, run_status


def _run_control_actor_role(current_user: Any) -> str:
    if isinstance(current_user, dict):
        role = str(current_user.get("workspace_role") or current_user.get("role") or "member").strip()
        return role or "member"
    return "member"


def _enforce_run_control_run_api_decision(
    *,
    operation: str,
    run_id: str,
    snapshot_run: dict[str, Any],
    current_user: Any,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _context, _metadata, workspace_id, tenant_id, run_status = _run_control_scope(snapshot_run)
    rust_payload = {
        "operation": str(operation or "").strip(),
        "workspace_id": workspace_id,
        "tenant_id": tenant_id,
        "run_id": str(run_id or "").strip(),
        "run_status": run_status,
        "user_role": _run_control_actor_role(current_user),
        "workspace_access_denied": False,
        "owner_access_denied": False,
        "kill_switch_active": False,
        "history_window_allowed": True,
        "body": {"user_id": str((current_user or {}).get("user_id") or "").strip() or "user", **dict(body or {})},
    }
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "run-api-decision",
            rust_payload,
            allow_approval_required=True,
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise HTTPException(status_code=409, detail=f"Rust run-api gate blocked {operation}: {exc.reason}") from exc
    allowed_next_actions = {
        "resume_run": {"resume_run"},
        "pause_run": {"pause_run"},
    }.get(operation, set())
    next_action = str(decision.get("next_action") or "").strip()
    if next_action not in allowed_next_actions:
        raise HTTPException(
            status_code=423,
            detail=f"Rust run-api gate returned unexpected next_action for {operation}: {next_action or 'missing'}",
        )
    return dict(decision)


def local_worker_recovery_confirmation_required(run: dict[str, Any]) -> bool:
    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    return bool(metadata.get("local_worker_recovery_manual_confirmation_required"))


def local_worker_recovery_confirmation_prompt(run: dict[str, Any]) -> str:
    checkpoint = browser_checkpoint_service.browser_checkpoint_from_run(run)
    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    attempt_count = int(metadata.get("local_worker_recovery_attempt_count") or 0)
    max_auto_retries = int(metadata.get("local_worker_recovery_max_auto_retries") or attempt_count or 1)
    checkpoint_details = browser_checkpoint_service.checkpoint_summary(checkpoint)
    next_action_index = checkpoint_details.get("next_action_index")
    session_profile = str(checkpoint_details.get("session_profile") or "").strip()
    details = [
        "Gateway recovery exhausted its automatic retry budget after repeated worker loss.",
        "Approve only if you want to try resuming this unstable local checkpoint again.",
    ]
    if attempt_count > 0:
        details.append(f"Recovery attempts used: {attempt_count}/{max_auto_retries}.")
    if next_action_index is not None:
        details.append(f"Checkpoint next action index: {next_action_index}.")
    if session_profile:
        details.append(f"Session profile: {session_profile}.")
    return " ".join(details)


def _local_execution_checkpoint(run: dict[str, Any]) -> dict[str, Any]:
    checkpoint = run.get("local_execution_checkpoint")
    if isinstance(checkpoint, dict) and checkpoint:
        return dict(checkpoint)
    result_data = run.get("result_data") if isinstance(run.get("result_data"), dict) else {}
    result_checkpoint = result_data.get("local_execution_checkpoint")
    if isinstance(result_checkpoint, dict) and result_checkpoint:
        return dict(result_checkpoint)
    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    metadata_checkpoint = metadata.get("local_execution_checkpoint")
    if isinstance(metadata_checkpoint, dict) and metadata_checkpoint:
        return dict(metadata_checkpoint)
    pack_inputs = metadata.get("pack_inputs") if isinstance(metadata.get("pack_inputs"), dict) else {}
    operations = pack_inputs.get("operations") if isinstance(pack_inputs.get("operations"), list) else []
    if operations:
        return {
            "kind": "local_execution_v1",
            "next_operation_index": 0,
            "total_operations": len(operations),
            "phase": "planned",
            "mode": "observing",
        }
    return {}


def _resume_capability(snapshot_run: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    browser_checkpoint = browser_checkpoint_service.browser_checkpoint_from_run(snapshot_run)
    if browser_checkpoint:
        return "browser_checkpoint", dict(browser_checkpoint)
    local_execution_checkpoint = _local_execution_checkpoint(snapshot_run)
    if local_execution_checkpoint:
        return "local_execution_checkpoint", dict(local_execution_checkpoint)
    return "", {}


def build_resume_waiting_run_callbacks(
    *,
    serialize_run_snapshot: Callable[[str, dict[str, Any]], dict[str, Any]],
    enforce_run_owner_access: Callable[[Any, Any], None],
    get_pending_confirmation: Callable[[dict[str, Any]], Any],
    begin_run_pending_confirmation: Callable[..., dict[str, Any]],
    emit_log: Callable[..., None],
    schedule_restored_run_resume: Callable[[str, dict[str, Any]], bool],
) -> dict[str, Any]:
    return {
        "serialize_run_snapshot": serialize_run_snapshot,
        "enforce_run_owner_access": enforce_run_owner_access,
        "get_pending_confirmation": get_pending_confirmation,
        "begin_run_pending_confirmation": begin_run_pending_confirmation,
        "emit_log": emit_log,
        "schedule_restored_run_resume": schedule_restored_run_resume,
    }


def build_pause_run_callbacks(
    *,
    serialize_run_snapshot: Callable[[str, dict[str, Any]], dict[str, Any]],
    enforce_run_owner_access: Callable[[Any, Any], None],
    pause_local_run: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    return {
        "serialize_run_snapshot": serialize_run_snapshot,
        "enforce_run_owner_access": enforce_run_owner_access,
        "pause_local_run": pause_local_run,
    }


def build_local_worker_recovery_approval_callbacks(
    *,
    get_pending_confirmation: Callable[[dict[str, Any]], Any],
    clear_pending_confirmation: Callable[[dict[str, Any]], None],
    approval_correlation_id: Callable[..., str],
    utc_now_iso: Callable[[], str],
    emit_log: Callable[..., None],
    append_approval_audit: Callable[..., None],
    schedule_restored_run_resume: Callable[[str, dict[str, Any]], bool],
) -> dict[str, Any]:
    return {
        "get_pending_confirmation": get_pending_confirmation,
        "clear_pending_confirmation": clear_pending_confirmation,
        "approval_correlation_id": approval_correlation_id,
        "utc_now_iso": utc_now_iso,
        "emit_log": emit_log,
        "append_approval_audit": append_approval_audit,
        "schedule_restored_run_resume": schedule_restored_run_resume,
    }


def pause_run_for_takeover(
    run_id: str,
    *,
    run: dict[str, Any] | None,
    run_record: dict[str, Any] | None = None,
    current_user: Any,
    serialize_run_snapshot: Callable[[str, dict[str, Any]], dict[str, Any]],
    enforce_run_owner_access: Callable[[Any, Any], None],
    pause_local_run: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    active_run = run if isinstance(run, dict) else None
    snapshot_run = active_run if isinstance(active_run, dict) else run_record if isinstance(run_record, dict) else None
    if not isinstance(snapshot_run, dict):
        raise HTTPException(status_code=404, detail="Run ID not found")
    snapshot = serialize_run_snapshot(run_id, snapshot_run)
    enforce_run_owner_access(current_user, snapshot)
    _enforce_run_control_run_api_decision(
        operation="pause_run",
        run_id=run_id,
        snapshot_run=snapshot_run,
        current_user=current_user,
        body={"manual_takeover_requested": True},
    )

    status = str(snapshot_run.get("status") or "").strip().lower()
    if status in {"completed", "failed", "timeout", "cancelled", "canceled", "stopped"}:
        raise HTTPException(status_code=409, detail="Run is already terminal.")
    if not isinstance(active_run, dict):
        raise HTTPException(status_code=409, detail="Run is not active in this process for takeover.")

    local_worker_id = str(active_run.get("local_worker_id") or "").strip()
    machine_id = str(active_run.get("machine_id") or "").strip()
    if not local_worker_id and not machine_id:
        raise HTTPException(status_code=409, detail="Run is not currently attached to a local machine.")

    result = pause_local_run(
        run_id,
        worker_id=None,
        result_text="Control handed back to the user. Run paused for manual takeover.",
        result_data={
            "manual_takeover": True,
            "resume_available": True,
            "summary": "Control handed back to the user. Run paused for manual takeover.",
        },
        browser_checkpoint=(
            dict(active_run.get("browser_checkpoint"))
            if isinstance(active_run.get("browser_checkpoint"), dict)
            else None
        ),
        wait_reason="manual_takeover_requested",
    )
    return {
        "status": "ok",
        "run_id": run_id,
        "paused": True,
        "takeover": True,
        "machine_id": machine_id or None,
        "worker_id": local_worker_id or None,
        **(result if isinstance(result, dict) else {}),
    }


def resolve_local_worker_recovery_approval(
    run_id: str,
    run: dict[str, Any],
    approval_id: str,
    decision_text: str,
    *,
    note: str = "",
    get_pending_confirmation: Callable[[dict[str, Any]], Any],
    clear_pending_confirmation: Callable[[dict[str, Any]], None],
    approval_correlation_id: Callable[..., str],
    utc_now_iso: Callable[[], str],
    emit_log: Callable[..., None],
    append_approval_audit: Callable[..., None],
    schedule_restored_run_resume: Callable[[str, dict[str, Any]], bool],
) -> dict[str, Any]:
    pending = get_pending_confirmation(run)
    correlation_id = str(pending.get("correlation_id") or "").strip() or approval_correlation_id(approval_id, run_id=run_id)
    approve_tokens = {"proceed", "approve", "yes", "y", "continue", "ok"}
    reject_tokens = {"hold", "reject", "no", "n", "abort", "stop", "cancel"}
    escalate_tokens = {"escalate", "escalated"}
    approved = decision_text in approve_tokens
    escalated = decision_text in escalate_tokens
    rejected = decision_text in reject_tokens or (not approved and not escalated)
    scope_value = "once"
    consequence = "This confirmation applies only to this pending step in this run. Later runs or later confirmation points will ask again."

    append_approval_audit(
        approval_id=approval_id,
        stage="received",
        decision=decision_text,
        actor="user",
        source="local_worker_recovery_resume",
        run_id=run_id,
        note=note,
        correlation_id=correlation_id,
        metadata={"scope": scope_value, "reusable": False},
    )

    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    metadata["local_worker_recovery_manual_confirmation_resolved_at"] = utc_now_iso()
    context["metadata"] = metadata
    run["context"] = context
    clear_pending_confirmation(run)

    checkpoint = browser_checkpoint_service.browser_checkpoint_from_run(run)

    if approved:
        emit_log(
            run["logs"],
            "warn",
            "Manual recovery confirmation received after repeated local worker instability. Resuming checkpoint.",
            event="local_worker_recovery_manual_resume_approved",
            data={
                "run_id": run_id,
                "approval_id": approval_id,
                "correlation_id": correlation_id,
                "attempt_count": int(metadata.get("local_worker_recovery_attempt_count") or 0),
                **browser_checkpoint_service.checkpoint_summary(checkpoint),
            },
        )
        append_approval_audit(
            approval_id=approval_id,
            stage="resolved",
            decision="approved",
            actor="runtime",
            source="local_worker_recovery_resume",
            run_id=run_id,
            correlation_id=correlation_id,
            metadata={"scope": scope_value, "reusable": False},
        )
        metadata["local_worker_recovery_manual_confirmation_required"] = True
        context["metadata"] = metadata
        run["context"] = context
        if not schedule_restored_run_resume(run_id, run):
            raise HTTPException(status_code=409, detail="Run could not be resumed.")
        metadata["local_worker_recovery_manual_confirmation_required"] = False
        context["metadata"] = metadata
        run["context"] = context
        return {
            "status": "ok",
            "run_id": run_id,
            "approval_id": approval_id,
            "correlation_id": correlation_id,
            "decision_kind": "approved",
            "scope": scope_value,
            "reusable": False,
            "consequence": consequence,
        }

    run["result"] = (
        "Manual checkpoint recovery was not approved after repeated local worker instability."
    )
    run["result_data"] = {
        "summary": run["result"],
        "error": "local_worker_recovery_manual_resume_declined",
        "resume_available": True,
        "manual_confirmation_required": True,
        "decision": "escalated" if escalated else "rejected",
        "attempt_count": int(metadata.get("local_worker_recovery_attempt_count") or 0),
        **browser_checkpoint_service.checkpoint_summary(checkpoint),
    }
    metadata["local_worker_recovery_manual_confirmation_required"] = True
    context["metadata"] = metadata
    run["context"] = context
    emit_log(
        run["logs"],
        "warn",
        "Manual checkpoint recovery was not approved after repeated local worker instability.",
        event="local_worker_recovery_manual_resume_declined",
        data={
            "run_id": run_id,
            "approval_id": approval_id,
            "correlation_id": correlation_id,
            "decision": "escalated" if escalated else "rejected",
            "attempt_count": int(metadata.get("local_worker_recovery_attempt_count") or 0),
        },
    )
    append_approval_audit(
        approval_id=approval_id,
        stage="resolved",
        decision="escalated" if escalated else "rejected",
        actor="runtime",
        source="local_worker_recovery_resume",
        run_id=run_id,
        correlation_id=correlation_id,
        metadata={"scope": scope_value, "reusable": False},
    )
    return {
        "status": "ok",
        "run_id": run_id,
        "approval_id": approval_id,
        "correlation_id": correlation_id,
        "decision_kind": "escalated" if escalated else "rejected",
        "scope": scope_value,
        "reusable": False,
        "consequence": consequence,
    }


def resume_waiting_run(
    run_id: str,
    *,
    run: dict[str, Any] | None,
    run_record: dict[str, Any] | None = None,
    current_user: Any,
    serialize_run_snapshot: Callable[[str, dict[str, Any]], dict[str, Any]],
    enforce_run_owner_access: Callable[[Any, Any], None],
    get_pending_confirmation: Callable[[dict[str, Any]], Any],
    begin_run_pending_confirmation: Callable[..., dict[str, Any]],
    emit_log: Callable[..., None],
    schedule_restored_run_resume: Callable[[str, dict[str, Any]], bool],
) -> dict[str, Any]:
    snapshot_run = run if isinstance(run, dict) else run_record if isinstance(run_record, dict) else None
    if not isinstance(snapshot_run, dict):
        raise HTTPException(status_code=404, detail="Run ID not found")
    snapshot = serialize_run_snapshot(run_id, snapshot_run)
    enforce_run_owner_access(current_user, snapshot)
    _enforce_run_control_run_api_decision(
        operation="resume_run",
        run_id=run_id,
        snapshot_run=snapshot_run,
        current_user=current_user,
        body={"resume_requested": True},
    )
    if str(snapshot_run.get("status") or "").strip().lower() != "waiting_for_input":
        raise HTTPException(status_code=409, detail="Run is not waiting for input.")
    pending_confirmation = get_pending_confirmation(snapshot_run)
    if isinstance(pending_confirmation, dict) and pending_confirmation:
        raise HTTPException(status_code=409, detail="Run requires confirmation resolution, not direct resume.")
    resume_kind, checkpoint = _resume_capability(snapshot_run)
    if not resume_kind:
        raise HTTPException(status_code=409, detail="Run does not have a resumable local execution checkpoint.")
    if local_worker_recovery_confirmation_required(snapshot_run):
        if not isinstance(run, dict):
            raise HTTPException(status_code=409, detail="Run is not active in this process.")
        pending = begin_run_pending_confirmation(
            run_id,
            local_worker_recovery_confirmation_prompt(snapshot_run),
            source="local_worker_recovery_resume",
            metadata={
                "kind": "local_worker_recovery_resume",
                "reason": "local_worker_recovery_exhausted",
                **browser_checkpoint_service.checkpoint_summary(checkpoint),
            },
        )
        return {
            "status": "confirmation_required",
            "run_id": run_id,
            "approval_id": pending.get("approval_id"),
            "correlation_id": pending.get("correlation_id"),
            "resume_kind": "browser_checkpoint",
            "degradation_kind": "local_worker_recovery_exhausted",
        }
    if not isinstance(run, dict):
        raise HTTPException(status_code=409, detail="Run is not active in this process.")
    run_result_data = run.get("result_data") if isinstance(run.get("result_data"), dict) else {}
    if isinstance(run_result_data, dict) and run_result_data:
        run_result_data.pop("manual_takeover", None)
        run_result_data["resume_available"] = True
        run["result_data"] = run_result_data
    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    metadata["manual_takeover"] = False
    if resume_kind == "local_execution_checkpoint":
        metadata["local_execution_resume_supported"] = True
    context["metadata"] = metadata
    run["context"] = context
    run.pop("wait_reason", None)
    emit_log(
        run["logs"],
        "info",
        (
            "Resume requested for paused browser operator run."
            if resume_kind == "browser_checkpoint"
            else "Resume requested for paused local computer-control run."
        ),
        event=("browser_resume_requested" if resume_kind == "browser_checkpoint" else "local_execution_resume_requested"),
        data={
            "run_id": run_id,
            **browser_checkpoint_service.checkpoint_summary(checkpoint),
        },
    )
    emit_log(
        run["logs"],
        "info",
        "AI control is resuming from the saved checkpoint.",
        event="computer_action",
        data={
            "schema": "empyralis.computer_action.v1",
            "mode": "acting",
            "phase": "planned",
            "tool": "computer_control",
            "action_type": "resume",
            "label": "AI control is resuming",
            "reason": (
                "Resuming from the saved browser checkpoint."
                if resume_kind == "browser_checkpoint"
                else "Resuming from the saved local execution checkpoint."
            ),
            "status": "queued_local",
            "success": True,
            "step_number": (int(checkpoint.get("next_action_index") or 0) + 1) if resume_kind == "browser_checkpoint" and checkpoint else (int(checkpoint.get("next_operation_index") or 0) + 1) if checkpoint else None,
            "step_total": int(checkpoint.get("total_operations") or 0) or None,
        },
    )
    if not schedule_restored_run_resume(run_id, run):
        raise HTTPException(status_code=409, detail="Run could not be resumed.")
    return {
        "status": "ok",
        "run_id": run_id,
        "resume_kind": resume_kind,
        "next_action_index": browser_checkpoint_service.checkpoint_next_action_index(checkpoint),
        "next_operation_index": checkpoint.get("next_operation_index"),
    }
