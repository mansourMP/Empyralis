from __future__ import annotations

from typing import Any, Callable, Dict, Iterator, Optional

from server_modules import direct_chat_runtime_service


def build_direct_operator_reply(
    *,
    services: direct_chat_runtime_service.DirectChatRuntimeServices,
    message: str,
    workspace_id: str,
    requested_model: str,
    requested_provider: str,
    thread_id: str = "",
    prior_messages: Optional[list[dict[str, Any]]] = None,
    reasoning_effort: str = "",
    availability: Optional[dict[str, Any]] = None,
    approved_action: Optional[dict[str, Any]] = None,
    max_iterations: Optional[int] = None,
    session_ctx: Optional[dict[str, Any]] = None,
    agent_turn_request: Optional[Any] = None,
    trace_context: Optional[Any] = None,
) -> Iterator[Dict[str, Any]]:
    # Internal delegate. Not an alternate turn engine. Called only from agent_turn().
    yield from direct_chat_runtime_service.build_direct_operator_reply(
        services=services,
        message=message,
        workspace_id=workspace_id,
        requested_model=requested_model,
        requested_provider=requested_provider,
        thread_id=thread_id,
        prior_messages=prior_messages,
        reasoning_effort=reasoning_effort,
        availability=availability,
        approved_action=approved_action,
        max_iterations=max_iterations,
        session_ctx=session_ctx,
        agent_turn_request=agent_turn_request,
        trace_context=trace_context,
    )


def collect_direct_operator_reply(
    *,
    services: direct_chat_runtime_service.DirectChatRuntimeServices,
    **kwargs: Any,
) -> Dict[str, Any]:
    # Internal delegate. Not an alternate turn engine. Called only from agent_turn().
    return direct_chat_runtime_service.collect_direct_operator_reply(
        services=services,
        **kwargs,
    )


def build_chat_turn_event_stream(
    *,
    services: direct_chat_runtime_service.DirectChatRuntimeServices,
    session_ctx: Optional[Dict[str, Any]],
    message: str,
    request_meta: Optional[Dict[str, Any]] = None,
) -> Iterator[Dict[str, Any]]:
    return direct_chat_runtime_service.build_chat_turn_event_stream(
        services=services,
        session_ctx=session_ctx,
        message=message,
        request_meta=request_meta,
    )


def execute_chat_turn(
    *,
    services: direct_chat_runtime_service.DirectChatRuntimeServices,
    session_ctx: Optional[Dict[str, Any]],
    message: str,
    stream_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    request_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return direct_chat_runtime_service.execute_chat_turn(
        services=services,
        session_ctx=session_ctx,
        message=message,
        stream_sink=stream_sink,
        request_meta=request_meta,
    )
