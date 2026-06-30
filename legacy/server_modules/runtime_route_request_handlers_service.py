from __future__ import annotations

from typing import Any, Callable

from fastapi import HTTPException


async def respond_chat_response(
    *,
    request: Any,
    current_user: Any,
    read_json_object_payload: Callable[..., Any],
    require_authenticated_user: Callable[[Any], dict[str, Any]],
    build_direct_chat_stream_response: Callable[..., Any],
    direct_chat_stream_response_services: Callable[[], Any],
) -> Any:
    body = await read_json_object_payload(
        request,
        invalid_detail="Invalid chat payload",
    )
    return await build_direct_chat_stream_response(
        current_user=require_authenticated_user(current_user),
        body=body,
        last_event_id=request.headers.get("last-event-id") or body.get("last_event_id"),
        services=direct_chat_stream_response_services(),
    )


async def update_workspace_context_file_response(
    filename: str,
    *,
    request: Any,
    read_json_payload: Callable[..., Any],
    update_workspace_context_file_payload: Callable[..., dict[str, Any]],
    write_workspace_context_file: Callable[[str, str], dict[str, str]],
) -> dict[str, Any]:
    body = await read_json_payload(
        request,
        invalid_detail="Invalid context file payload",
    )
    return update_workspace_context_file_payload(
        filename,
        body,
        write_workspace_context_file=write_workspace_context_file,
    )


def trigger_heartbeat_response(
    *,
    heartbeat_scheduler: Callable[[], Any],
    trigger_heartbeat_payload: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    try:
        return trigger_heartbeat_payload(
            scheduler=heartbeat_scheduler(),
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Heartbeat scheduler is not configured.") from exc


async def register_webhook_trigger_response(
    *,
    request: Any,
    current_user: Any,
    load_webhook_triggers: Callable[[], None],
    read_json_payload: Callable[..., Any],
    enforce_workspace_access: Callable[..., str],
    register_webhook_trigger_payload: Callable[..., dict[str, Any]],
    uuid_factory: Callable[[], Any],
    build_webhook_trigger_fn: Callable[..., dict[str, Any]],
    triggers: dict[str, dict[str, Any]],
    lock: Any,
    persist_webhook_triggers_locked: Callable[[], None],
) -> dict[str, Any]:
    load_webhook_triggers()
    body = await read_json_payload(
        request,
        invalid_detail="Invalid webhook trigger payload",
    )
    if isinstance(body, dict):
        body = dict(body)
        body["workspace_id"] = enforce_workspace_access(
            current_user,
            body.get("workspace_id"),
            minimum_role="member",
        )
    return register_webhook_trigger_payload(
        body,
        uuid_factory=uuid_factory,
        build_webhook_trigger_fn=build_webhook_trigger_fn,
        triggers=triggers,
        lock=lock,
        persist_webhook_triggers_locked=persist_webhook_triggers_locked,
    )


async def ingest_webhook_response(
    workspace_id: str,
    *,
    request: Any,
    current_user: Any,
    enforce_workspace_access: Callable[..., str],
    read_json_payload: Callable[..., Any],
    ingest_webhook_payload: Callable[..., Any],
    match_webhook_trigger_fn: Callable[[str, str], Any],
    run_start_request_class: Callable[..., Any],
    execute_run_start_request_via_turn_runtime: Callable[..., Any],
    stamp_request_owner_fn: Callable[..., Any],
    run_execution_services: Callable[[], Any],
) -> Any:
    payload = await read_json_payload(
        request,
        invalid_detail="Invalid webhook payload",
    )
    resolved_workspace_id = enforce_workspace_access(
        current_user,
        workspace_id,
        minimum_role="member",
    )
    return await ingest_webhook_payload(
        workspace_id=resolved_workspace_id,
        request_url=str(request.url),
        payload=payload,
        current_user=current_user,
        match_webhook_trigger_fn=match_webhook_trigger_fn,
        run_start_request_class=run_start_request_class,
        execute_run_start_request_via_turn_runtime=execute_run_start_request_via_turn_runtime,
        stamp_request_owner_fn=stamp_request_owner_fn,
        run_execution_services=run_execution_services,
    )
