from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from fastapi import HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from server_modules.agent_turn import AgentTurnRequest
from server_modules import error_response_service
from server_modules.error_contracts import INTERNAL_ERROR, POLICY_BLOCK, USER_INPUT_ERROR
from server_modules import rust_runtime_kernel_client


@dataclass(slots=True)
class DirectChatStreamResponseServices:
    resolve_direct_chat_turn_request: Callable[..., Any]
    chat_stream_request_signature: Callable[..., str]
    execute_agent_turn_request: Callable[..., Any]
    build_turn_execution_services: Callable[..., Any]
    run_execution_services: Callable[[], Any]
    direct_chat_execution_services: Callable[[], Any]
    get_chat_stream_state: Callable[[Any, str], Optional[dict[str, Any]]]
    chat_stream_state_db_path: Callable[[], Any]
    get_or_create_chat_stream_session: Callable[..., dict[str, Any]]
    extract_direct_chat_error_response: Callable[[Any], Optional[dict[str, str]]]
    start_chat_stream_producer: Callable[[dict[str, Any], Any], None]
    iter_chat_stream_events: Callable[[dict[str, Any], Any], Any]


def _turn_request_request_id(turn_request: AgentTurnRequest) -> str:
    context_hints = _turn_request_context_hints(turn_request)
    return str(context_hints.get("request_id") or "").strip()


def _metadata_dict(value: Any) -> dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _turn_request_context_hints(turn_request: Any) -> dict[str, Any]:
    if isinstance(turn_request, AgentTurnRequest):
        return turn_request.context_hints if isinstance(turn_request.context_hints, dict) else {}
    if isinstance(turn_request, dict):
        return turn_request.get("context_hints") if isinstance(turn_request.get("context_hints"), dict) else {}
    context_hints = getattr(turn_request, "context_hints", None)
    return context_hints if isinstance(context_hints, dict) else {}


def _turn_request_field(turn_request: Any, field_name: str) -> str:
    if isinstance(turn_request, dict):
        return str(turn_request.get(field_name) or "").strip()
    return str(getattr(turn_request, field_name, "") or "").strip()


def _current_user_role(current_user: dict[str, Any]) -> str:
    if bool(current_user.get("is_admin")):
        return "admin"
    auth_type = str(current_user.get("auth_type") or "").strip().lower()
    if auth_type == "api_key":
        return "admin"
    return str(current_user.get("role") or "member").strip() or "member"


def _enforce_direct_chat_stream_run_api_decision(
    *,
    current_user: dict[str, Any],
    turn_request: Any,
    workspace_id: str,
    thread_id: str,
    request_id: str,
) -> dict[str, Any]:
    payload = {
        "operation": "stream_chat",
        "workspace_id": str(workspace_id or "").strip() or "default",
        "tenant_id": _turn_request_field(turn_request, "tenant_id") or "default",
        "user_role": _current_user_role(current_user),
        "run_id": None,
        "run_status": None,
        "body": {
            "thread_id": str(thread_id or "").strip() or None,
            "request_id": str(request_id or "").strip() or None,
        },
        "workspace_access_denied": False,
        "owner_access_denied": False,
        "kill_switch_active": False,
        "entitlement_blocked": False,
        "budget_exhausted": False,
        "history_window_allowed": True,
    }
    try:
        decision = dict(
            rust_runtime_kernel_client.run_runtime_kernel_enforced(
                "run-api-decision",
                payload,
            )
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        reason = str(exc.reason or "rust_run_api_denied").strip()
        raise HTTPException(status_code=409, detail=f"Rust run-api gate blocked stream_chat: {reason}") from exc
    next_action = str(decision.get("next_action") or "").strip()
    if next_action != "start_chat_stream":
        raise HTTPException(
            status_code=423,
            detail=f"Rust run-api gate returned unexpected next_action for stream_chat: {next_action or 'missing'}",
        )
    return decision


def _assistant_turn_session_metadata(
    *,
    turn_request: Any,
    workspace_id: str,
    thread_id: str,
    request_id: str,
) -> dict[str, Any]:
    context_hints = _turn_request_context_hints(turn_request)
    binding_metadata = _metadata_dict(context_hints.get("metadata"))
    active_agent_install_id = (
        str(binding_metadata.get("active_agent_install_id") or binding_metadata.get("workspace_agent_install_id") or "").strip()
        or None
    )
    runtime_profile_id = str(binding_metadata.get("runtime_profile_id") or "").strip() or None
    session_id = _turn_request_field(turn_request, "session_id") or thread_id
    return {
        "tenant_id": _turn_request_field(turn_request, "tenant_id") or "default",
        "workspace_id": str(workspace_id or "").strip() or "default",
        "thread_id": str(thread_id or "").strip() or "direct-chat",
        "session_id": session_id,
        "request_id": str(request_id or "").strip() or None,
        "active_agent_install_id": active_agent_install_id,
        "runtime_profile_id": runtime_profile_id,
    }


async def build_agent_turn_stream_response(
    *,
    current_user: dict[str, Any],
    turn_request: AgentTurnRequest,
    last_event_id: Any,
    services: DirectChatStreamResponseServices,
    chat_body: Optional[dict[str, Any]] = None,
    fallback_workspace_id: Optional[str] = None,
    fallback_thread_id: Optional[str] = None,
    fallback_client_request_id: Optional[str] = None,
) -> JSONResponse | StreamingResponse:
    run_execution_services = services.run_execution_services()
    direct_chat_execution_services = services.direct_chat_execution_services()
    try:
        execution = await services.execute_agent_turn_request(
            turn_request=turn_request,
            current_user=current_user,
            run_execution_services=run_execution_services,
            direct_chat_services=direct_chat_execution_services,
            chat_body=chat_body,
        )
    except TypeError as exc:
        if "unexpected keyword argument 'run_execution_services'" not in str(exc):
            raise
        execution = await services.execute_agent_turn_request(
            turn_request=turn_request,
            current_user=current_user,
            services=services.build_turn_execution_services(
                run_execution=run_execution_services,
                direct_chat=direct_chat_execution_services,
            ),
            chat_body=chat_body,
        )
    workspace_id = (
        str(execution.get("workspace_id") or "").strip()
        or str(fallback_workspace_id or "").strip()
        or str(turn_request.workspace_id or "").strip()
        or "default"
    )
    session_key = str(execution.get("session_key") or "").strip()
    thread_id = (
        str(execution.get("thread_id") or "").strip()
        or str(fallback_thread_id or "").strip()
        or str(turn_request.session_id or "").strip()
        or "direct-chat"
    )
    client_request_id = (
        str(execution.get("client_request_id") or "").strip()
        or str(fallback_client_request_id or "").strip()
        or _turn_request_request_id(turn_request)
    )
    _enforce_direct_chat_stream_run_api_decision(
        current_user=current_user,
        turn_request=turn_request,
        workspace_id=workspace_id,
        thread_id=thread_id,
        request_id=client_request_id,
    )
    producer = execution["producer"]

    # Pre-resolve image descriptions (Gemini vision) before the sync generator starts
    if turn_request.attachments:
        from server_modules.attachment_utils import preprocess_image_attachments
        await preprocess_image_attachments(workspace_id, turn_request.attachments)

    session = services.get_or_create_chat_stream_session(
        session_key,
        thread_id=thread_id,
        request_id=client_request_id,
        workspace_id=workspace_id,
    )
    session_metadata = session.get("metadata") if isinstance(session.get("metadata"), dict) else {}
    session_metadata["assistant_turn"] = _assistant_turn_session_metadata(
        turn_request=turn_request,
        workspace_id=workspace_id,
        thread_id=thread_id,
        request_id=client_request_id,
    )
    session["metadata"] = session_metadata
    if not bool(session.get("producer_started")):
        producer_iter = producer()
        try:
            first_event = next(producer_iter)
        except StopIteration:
            error = error_response_service.platform_error(
                code="chat_unavailable",
                message="Chat ended before producing a response.",
                error_class=INTERNAL_ERROR,
                retryable=True,
                status_code=500,
                request_id=client_request_id,
            )
            content = error_response_service.serialize_http_error_envelope(
                error_response_service.build_http_error_envelope(error)
            )
            content.update(
                {
                    "error_code": error.code,
                    "message": error.message,
                }
            )
            return JSONResponse(status_code=500, content=content)
        immediate_error = services.extract_direct_chat_error_response(first_event)
        if isinstance(immediate_error, dict):
            error = error_response_service.platform_error(
                code=str(immediate_error.get("error") or "direct_chat_conflict").strip() or "direct_chat_conflict",
                message=str(immediate_error.get("message") or "Direct chat could not start.").strip()
                or "Direct chat could not start.",
                error_class=POLICY_BLOCK,
                retryable=True,
                status_code=409,
                request_id=client_request_id,
            )
            content = error_response_service.serialize_http_error_envelope(
                error_response_service.build_http_error_envelope(error)
            )
            content.update(immediate_error)
            return JSONResponse(status_code=409, content=content)

        def replaying_producer():
            yield first_event
            for item in producer_iter:
                yield item

        services.start_chat_stream_producer(session, replaying_producer)
    else:
        services.start_chat_stream_producer(session, producer)

    return StreamingResponse(
        services.iter_chat_stream_events(session, last_event_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def build_direct_chat_stream_response(
    *,
    current_user: dict[str, Any],
    body: dict[str, Any],
    last_event_id: Any,
    services: DirectChatStreamResponseServices,
) -> JSONResponse | StreamingResponse:
    try:
        direct_resolution = services.resolve_direct_chat_turn_request(
            current_user=current_user,
            body=body,
            request_signature_fn=services.chat_stream_request_signature,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_direct_chat_request",
                "message": str(exc),
                "class": USER_INPUT_ERROR,
            },
        ) from exc

    return await build_agent_turn_stream_response(
        current_user=current_user,
        turn_request=direct_resolution.turn_request,
        last_event_id=last_event_id,
        services=services,
        chat_body=body,
        fallback_workspace_id=direct_resolution.workspace_id,
        fallback_thread_id=direct_resolution.thread_id,
        fallback_client_request_id=direct_resolution.client_request_id,
    )
