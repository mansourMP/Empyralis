from __future__ import annotations

from typing import Any, Callable

from fastapi import HTTPException


def build_local_execution_approval_callbacks(
    *,
    get_pending_confirmation: Callable[[dict[str, Any]], Any],
    approval_correlation_id: Callable[..., str],
    parse_utc_ts: Callable[[Any], Any],
    utc_now: Callable[[], Any],
    utc_now_iso: Callable[[], str],
    set_pending_confirmation: Callable[[dict[str, Any], dict[str, Any]], None],
    emit_log: Callable[..., None],
    append_approval_audit: Callable[..., None],
    browser_plan_hash_from_inputs: Callable[[Any], str],
    clear_pending_confirmation: Callable[[dict[str, Any]], None],
    set_run_status: Callable[[str, str], None],
    mark_local_execution_tools_approved: Callable[[dict[str, Any]], None],
    build_browser_execution_binding: Callable[[str, str, str], Any],
    root_dir: str,
    enqueue_local_companion_run: Callable[..., None],
) -> dict[str, Any]:
    return {
        "get_pending_confirmation": get_pending_confirmation,
        "approval_correlation_id": approval_correlation_id,
        "parse_utc_ts": parse_utc_ts,
        "utc_now": utc_now,
        "utc_now_iso": utc_now_iso,
        "set_pending_confirmation": set_pending_confirmation,
        "emit_log": emit_log,
        "append_approval_audit": append_approval_audit,
        "browser_plan_hash_from_inputs": browser_plan_hash_from_inputs,
        "clear_pending_confirmation": clear_pending_confirmation,
        "set_run_status": set_run_status,
        "mark_local_execution_tools_approved": mark_local_execution_tools_approved,
        "build_browser_execution_binding": build_browser_execution_binding,
        "root_dir": root_dir,
        "enqueue_local_companion_run": enqueue_local_companion_run,
    }


def build_resolve_local_execution_start_approval_fn(
    *,
    get_pending_confirmation: Callable[[dict[str, Any]], Any],
    approval_correlation_id: Callable[..., str],
    parse_utc_ts: Callable[[Any], Any],
    utc_now: Callable[[], Any],
    utc_now_iso: Callable[[], str],
    set_pending_confirmation: Callable[[dict[str, Any], dict[str, Any]], None],
    emit_log: Callable[..., None],
    append_approval_audit: Callable[..., None],
    browser_plan_hash_from_inputs: Callable[[Any], str],
    clear_pending_confirmation: Callable[[dict[str, Any]], None],
    set_run_status: Callable[[str, str], None],
    mark_local_execution_tools_approved: Callable[[dict[str, Any]], None],
    build_browser_execution_binding: Callable[[str, str, str], Any],
    root_dir: Callable[[], str] | str,
    enqueue_local_companion_run: Callable[..., None],
) -> Callable[..., dict[str, Any]]:
    def _resolve(
        run_id: str,
        run: dict[str, Any],
        approval_id: str,
        decision_text: str,
        note: str = "",
    ) -> dict[str, Any]:
        resolved_root_dir = root_dir() if callable(root_dir) else root_dir
        return resolve_local_execution_start_approval(
            run_id,
            run,
            approval_id,
            decision_text,
            note=note,
            **build_local_execution_approval_callbacks(
                get_pending_confirmation=get_pending_confirmation,
                approval_correlation_id=approval_correlation_id,
                parse_utc_ts=parse_utc_ts,
                utc_now=utc_now,
                utc_now_iso=utc_now_iso,
                set_pending_confirmation=set_pending_confirmation,
                emit_log=emit_log,
                append_approval_audit=append_approval_audit,
                browser_plan_hash_from_inputs=browser_plan_hash_from_inputs,
                clear_pending_confirmation=clear_pending_confirmation,
                set_run_status=set_run_status,
                mark_local_execution_tools_approved=mark_local_execution_tools_approved,
                build_browser_execution_binding=build_browser_execution_binding,
                root_dir=resolved_root_dir,
                enqueue_local_companion_run=enqueue_local_companion_run,
            ),
        )

    return _resolve


def resolve_local_execution_start_approval(
    run_id: str,
    run: dict[str, Any],
    approval_id: str,
    decision_text: str,
    *,
    note: str = "",
    get_pending_confirmation: Callable[[dict[str, Any]], Any],
    approval_correlation_id: Callable[..., str],
    parse_utc_ts: Callable[[Any], Any],
    utc_now: Callable[[], Any],
    utc_now_iso: Callable[[], str],
    set_pending_confirmation: Callable[[dict[str, Any], dict[str, Any]], None],
    emit_log: Callable[..., None],
    append_approval_audit: Callable[..., None],
    browser_plan_hash_from_inputs: Callable[[Any], str],
    clear_pending_confirmation: Callable[[dict[str, Any]], None],
    set_run_status: Callable[[str, str], None],
    mark_local_execution_tools_approved: Callable[[dict[str, Any]], None],
    build_browser_execution_binding: Callable[[str, str, str], Any],
    root_dir: str,
    enqueue_local_companion_run: Callable[..., None],
) -> dict[str, Any]:
    scope_value = "once"
    consequence = "This confirmation applies only to this pending step in this run. Later runs or later confirmation points will ask again."
    pending = get_pending_confirmation(run)
    correlation_id = str(pending.get("correlation_id") or "").strip() or approval_correlation_id(approval_id, run_id=run_id)
    expires_at = parse_utc_ts(pending.get("expires_at"))
    if expires_at is not None and utc_now() > expires_at:
        pending["status"] = "expired"
        pending["expired_at"] = utc_now_iso()
        set_pending_confirmation(run, pending)
        raise HTTPException(status_code=409, detail="Confirmation request has already expired.")

    approve_tokens = {"proceed", "approve", "yes", "y", "continue", "ok"}
    reject_tokens = {"hold", "reject", "no", "n", "abort", "stop", "cancel"}
    escalate_tokens = {"escalate", "escalated"}
    approved = decision_text in approve_tokens
    escalated = decision_text in escalate_tokens
    rejected = decision_text in reject_tokens or (not approved and not escalated)

    pending["status"] = "resolved"
    pending["resolved_at"] = utc_now_iso()
    pending["decision"] = decision_text
    set_pending_confirmation(run, pending)
    emit_log(
        run["logs"],
        "info" if approved else "warn",
        f"Decision received: {decision_text}",
        event="approval_received",
        data={"approval_id": approval_id, "correlation_id": correlation_id, "decision": decision_text, "scope": scope_value, "reusable": False},
    )
    append_approval_audit(
        approval_id=approval_id,
        stage="received",
        decision=decision_text,
        actor="user",
        source="local_execution_start",
        run_id=run_id,
        note=note,
        correlation_id=correlation_id,
        metadata={"scope": scope_value, "reusable": False},
    )

    if approved:
        context = run.get("context")
        metadata = context.get("metadata") if isinstance(context, dict) and isinstance(context.get("metadata"), dict) else {}
        precheck = metadata.get("tool_policy_precheck") if isinstance(metadata.get("tool_policy_precheck"), dict) else {}
        browser_policy = precheck.get("browser_automation_policy") if isinstance(precheck.get("browser_automation_policy"), dict) else {}
        expected_plan_hash = (
            str(metadata.get("browser_immutable_plan_hash") or "").strip()
            or str(browser_policy.get("immutable_plan_hash") or "").strip()
        )
        current_plan_hash = browser_plan_hash_from_inputs(metadata.get("pack_inputs")) if isinstance(metadata, dict) else ""
        if expected_plan_hash and current_plan_hash and expected_plan_hash != current_plan_hash:
            clear_pending_confirmation(run)
            run["result"] = "Local browser execution plan changed after approval."
            run["result_data"] = {
                "summary": "Local browser execution plan changed after approval.",
                "error": "browser_plan_hash_mismatch",
                "expected_plan_hash": expected_plan_hash,
                "current_plan_hash": current_plan_hash,
            }
            emit_log(
                run["logs"],
                "error",
                "Approved browser plan changed before execution. Run failed.",
                event="approval_plan_hash_mismatch",
                data={
                    "approval_id": approval_id,
                    "correlation_id": correlation_id,
                    "expected_plan_hash": expected_plan_hash,
                    "current_plan_hash": current_plan_hash,
                },
            )
            set_run_status(run_id, "failed")
            run["logs"].put(None)
            raise HTTPException(status_code=409, detail="Approved browser execution plan changed before execution.")
        clear_pending_confirmation(run)
        if isinstance(context, dict) and isinstance(metadata, dict):
            metadata.pop("local_execution_waiting_confirmation", None)
            metadata.pop("local_execution_waiting_approval", None)
            mark_local_execution_tools_approved(metadata)
            if expected_plan_hash or current_plan_hash:
                metadata["browser_immutable_plan_hash"] = current_plan_hash or expected_plan_hash
                try:
                    metadata["browser_execution_binding"] = build_browser_execution_binding(
                        root_dir,
                        current_plan_hash or expected_plan_hash,
                        str(metadata.get("browser_session_profile") or "").strip(),
                    )
                except Exception:
                    metadata.pop("browser_execution_binding", None)
            if bool(browser_policy.get("reviewed_approval_required")):
                metadata["browser_reviewed_approved"] = True
                metadata["browser_reviewed_approved_at"] = utc_now_iso()
            context["metadata"] = metadata
        emit_log(
            run["logs"],
            "info",
            "Confirmation received. Run queued for Local Companion execution.",
            event="approval_resolved",
            data={"approval_id": approval_id, "correlation_id": correlation_id, "decision": decision_text, "approved": True, "scope": scope_value, "reusable": False},
        )
        append_approval_audit(
            approval_id=approval_id,
            stage="resolved",
            decision="approved",
            actor="runtime",
            source="local_execution_start",
            run_id=run_id,
            correlation_id=correlation_id,
            metadata={"scope": scope_value, "reusable": False},
        )
        enqueue_local_companion_run(
            run_id,
            message="Confirmation received. Run queued for Local Companion execution.",
            event="local_queued_after_approval",
        )
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

    clear_pending_confirmation(run)
    context = run.get("context")
    if isinstance(context, dict):
        metadata = context.get("metadata")
        if isinstance(metadata, dict):
            metadata.pop("local_execution_waiting_confirmation", None)
            metadata.pop("local_execution_waiting_approval", None)
            context["metadata"] = metadata
    emit_log(
        run["logs"],
        "warn",
        "Gateway execution was not started because confirmation was not granted.",
        event="approval_resolved",
        data={
            "approval_id": approval_id,
            "correlation_id": correlation_id,
            "decision": decision_text,
            "approved": False,
            "rejected": bool(rejected),
            "escalated": bool(escalated),
            "scope": scope_value,
            "reusable": False,
        },
    )
    append_approval_audit(
        approval_id=approval_id,
        stage="resolved",
        decision=("escalated" if escalated else "rejected"),
        actor="runtime",
        source="local_execution_start",
        run_id=run_id,
        correlation_id=correlation_id,
        metadata={
            "raw_decision": decision_text,
            "approved": False,
            "rejected": bool(rejected),
            "escalated": bool(escalated),
            "scope": scope_value,
            "reusable": False,
        },
    )
    run["result"] = "Gateway execution was not started because confirmation was not granted."
    set_run_status(run_id, "failed")
    run["logs"].put(None)
    return {
        "status": "ok",
        "run_id": run_id,
        "approval_id": approval_id,
        "correlation_id": correlation_id,
        "decision_kind": ("escalated" if escalated else "rejected"),
        "scope": scope_value,
        "reusable": False,
        "consequence": consequence,
    }
