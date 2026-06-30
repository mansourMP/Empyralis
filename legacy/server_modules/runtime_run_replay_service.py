from __future__ import annotations

from typing import Any, Callable

from fastapi import HTTPException
from server_modules import rust_runtime_kernel_client


def _replay_scope(item: dict[str, Any]) -> dict[str, str]:
    context = item.get("context") if isinstance(item.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    return {
        "workspace_id": str(
            item.get("workspace_id")
            or context.get("workspace_id")
            or metadata.get("workspace_id")
            or ""
        ).strip(),
        "tenant_id": str(
            item.get("tenant_id")
            or context.get("tenant_id")
            or metadata.get("tenant_id")
            or ""
        ).strip(),
        "run_status": str(
            item.get("status")
            or item.get("state")
            or context.get("status")
            or metadata.get("status")
            or ""
        ).strip(),
    }


def _enforce_replay_run_api_decision(
    *,
    operation: str,
    run_id: str,
    item: dict[str, Any],
    body: dict[str, Any] | None = None,
    user_role: str = "admin",
) -> dict[str, Any]:
    scope = _replay_scope(item)
    rust_payload = {
        "operation": str(operation or "").strip(),
        "workspace_id": scope["workspace_id"],
        "tenant_id": scope["tenant_id"],
        "run_id": str(run_id or "").strip(),
        "run_status": scope["run_status"],
        "user_role": str(user_role or "admin").strip() or "admin",
        "workspace_access_denied": False,
        "owner_access_denied": False,
        "kill_switch_active": False,
        "history_window_allowed": True,
        "body": {"user_id": "system", **dict(body or {})},
    }
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "run-api-decision",
            rust_payload,
            allow_approval_required=True,
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise HTTPException(status_code=409, detail=f"Rust run-api gate blocked {operation}: {exc.reason}") from exc
    next_action = str(decision.get("next_action") or "").strip()
    if operation == "get_run" and next_action == "request_owner_approval":
        raise HTTPException(
            status_code=409,
            detail=f"Rust run-api gate blocked {operation}: {decision.get('reason') or 'owner_approval_required'}",
        )
    allowed_next_actions = {
        "get_run": {"get_run"},
        "start_run": {"start_run"},
    }.get(operation, set())
    if next_action not in allowed_next_actions:
        raise HTTPException(
            status_code=423,
            detail=f"Rust run-api gate returned unexpected next_action for {operation}: {next_action or 'missing'}",
        )
    return dict(decision)


def replay_item_response(*, item: dict[str, Any]) -> dict[str, Any]:
    return {"item": item}


def replay_item_response_for_run(
    run_id: str,
    *,
    get_replay_payload: Callable[[str], dict[str, Any]],
) -> dict[str, Any]:
    item = get_replay_payload(run_id)
    _enforce_replay_run_api_decision(
        operation="get_run",
        run_id=run_id,
        item=item,
        body={"replay_requested": True, "sensitive_payload_requested": True},
    )
    return replay_item_response(item=item)


def replay_run_from_item(
    *,
    item: dict[str, Any],
    run_start_request_class: Callable[..., Any],
    execute_system_run_start_request_via_turn_runtime: Callable[..., Any],
    stamp_request_owner_fn: Callable[..., Any],
    run_execution_services: Callable[[], Any],
) -> Any:
    replay_payload = item.get("replay_request")
    if not isinstance(replay_payload, dict):
        raise HTTPException(status_code=400, detail="Replay request is not available for this run.")
    _enforce_replay_run_api_decision(
        operation="start_run",
        run_id=str(item.get("run_id") or item.get("id") or "").strip(),
        item=item,
        body={"replay_requested": True, **dict(replay_payload)},
    )
    try:
        request = run_start_request_class(**replay_payload)
        return execute_system_run_start_request_via_turn_runtime(
            request,
            stamp_request_owner_fn=stamp_request_owner_fn,
            services=run_execution_services(),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def replay_run_from_run_id(
    run_id: str,
    *,
    get_replay_payload: Callable[[str], dict[str, Any]],
    run_start_request_class: Callable[..., Any],
    execute_system_run_start_request_via_turn_runtime: Callable[..., Any],
    stamp_request_owner_fn: Callable[..., Any],
    run_execution_services: Callable[[], Any],
) -> Any:
    return replay_run_from_item(
        item=get_replay_payload(run_id),
        run_start_request_class=run_start_request_class,
        execute_system_run_start_request_via_turn_runtime=execute_system_run_start_request_via_turn_runtime,
        stamp_request_owner_fn=stamp_request_owner_fn,
        run_execution_services=run_execution_services,
    )
