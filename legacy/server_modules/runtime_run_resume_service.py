from __future__ import annotations

from typing import Any, Callable

from fastapi import HTTPException
from server_modules import browser_checkpoint_service, rust_runtime_kernel_client


def run_thread_is_alive(
    run: dict,
    *,
    enumerate_threads: Callable[[], list[Any]],
) -> bool:
    thread_id = run.get("thread_id")
    if not isinstance(thread_id, int) or thread_id <= 0:
        return False
    for worker in enumerate_threads():
        try:
            if worker.ident == thread_id and worker.is_alive():
                return True
        except Exception:
            continue
    return False


def build_run_thread_is_alive_fn(
    *,
    enumerate_threads: Callable[[], list[Any]],
) -> Callable[[dict], bool]:
    return lambda run: run_thread_is_alive(run, enumerate_threads=enumerate_threads)


def _enforce_restored_run_resume_decision(
    *,
    run_id: str,
    run: dict[str, Any],
) -> dict[str, Any]:
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
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "run-api-decision",
            {
                "operation": "resume_run",
                "workspace_id": workspace_id,
                "tenant_id": tenant_id,
                "run_id": str(run_id or "").strip(),
                "run_status": run_status,
                "user_role": "system",
                "workspace_access_denied": False,
                "owner_access_denied": False,
                "kill_switch_active": False,
                "history_window_allowed": True,
                "body": {
                    "user_id": "system",
                    "resume_requested": True,
                    "restored_runtime_resume": True,
                },
            },
            allow_approval_required=True,
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise HTTPException(status_code=409, detail=f"Rust run-api gate blocked resume_run: {exc.reason}") from exc
    next_action = str(decision.get("next_action") or "").strip()
    if next_action != "resume_run":
        raise HTTPException(
            status_code=423,
            detail=f"Rust run-api gate returned unexpected next_action for resume_run: {next_action or 'missing'}",
        )
    return dict(decision)


def schedule_restored_run_resume(
    run_id: str,
    run: dict,
    *,
    run_thread_is_alive_fn: Callable[[dict], bool],
    utc_now_iso: Callable[[], str],
    late_server_export: Callable[[str], Any],
    thread_class: Callable[..., Any],
) -> bool:
    if not isinstance(run, dict):
        return False
    if run_thread_is_alive_fn(run):
        return False
    if str(run.get("status") or "").strip().lower() != "waiting_for_input":
        return False
    if bool(run.get("_resume_after_confirmation_scheduled")):
        return True
    _enforce_restored_run_resume_decision(
        run_id=run_id,
        run=run,
    )
    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    selected_target = str(
        metadata.get("execution_target_selected")
        or metadata.get("execution_target")
        or ""
    ).strip().lower()
    if selected_target == "local_companion":
        run["_resume_after_confirmation_scheduled"] = True
        run["thread_id"] = None
        run["updated_at"] = utc_now_iso()
        checkpoint = browser_checkpoint_service.browser_checkpoint_from_run(run)
        metadata["browser_resume_supported"] = browser_checkpoint_service.browser_resume_supported(
            run,
            metadata,
        )
        context["metadata"] = metadata
        run["context"] = context
        late_server_export("_enqueue_local_companion_run")(
            run_id,
            message=(
                "Resuming Gateway run from saved browser checkpoint."
                if checkpoint
                else "Resuming Gateway run."
            ),
            event=("local_resumed_from_checkpoint" if checkpoint else "local_resumed"),
        )
        return True
    run["_resume_after_confirmation_scheduled"] = True
    run["thread_id"] = None
    run["updated_at"] = utc_now_iso()
    late_server_export("_persist_live_run_state")(run_id, run)
    worker = thread_class(
        target=late_server_export("run_mission"),
        args=(run_id,),
        daemon=True,
        name=f"run-resume-{run_id[:8]}",
    )
    worker.start()
    return True


def build_schedule_restored_run_resume_fn(
    *,
    run_thread_is_alive_fn: Callable[[dict], bool],
    utc_now_iso: Callable[[], str],
    late_server_export: Callable[[str], Any],
    thread_class: Callable[..., Any],
) -> Callable[[str, dict], bool]:
    return lambda run_id, run: schedule_restored_run_resume(
        run_id,
        run,
        run_thread_is_alive_fn=run_thread_is_alive_fn,
        utc_now_iso=utc_now_iso,
        late_server_export=late_server_export,
        thread_class=thread_class,
    )
