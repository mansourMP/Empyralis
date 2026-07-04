from __future__ import annotations

import os
from dataclasses import dataclass
import logging
from typing import Any, Callable, Optional

from server_modules.agent_turn import (
    AgentTurnRequest,
    bind_agent_turn_request_meta,
    build_agent_turn_session_context,
    resolve_agent_turn_session_identity,
    ensure_direct_chat_turn_request,
)
from server_modules.api_contract import build_turn_chat_body
from server_modules import direct_chat_transport_service, failure_policy_service
from server_modules.error_contracts import OBSERVABILITY_FAILURE, SEVERITY_WARNING


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DirectChatExecutionServices:
    chat_stream_key: Callable[[Any, dict], tuple[str, str, str]]
    session_manager_enabled: Callable[[], bool]
    session_manager_factory: Callable[[], Any]
    build_direct_operator_reply: Callable[..., Any]
    build_chat_turn_event_stream: Callable[..., Any]


def build_direct_chat_execution_services(
    *,
    chat_stream_key: Callable[[Any, dict], tuple[str, str, str]],
    session_manager_enabled: Callable[[], bool],
    session_manager_factory: Callable[[], Any],
    build_direct_operator_reply: Callable[..., Any],
    build_chat_turn_event_stream: Callable[..., Any],
) -> DirectChatExecutionServices:
    return DirectChatExecutionServices(
        chat_stream_key=chat_stream_key,
        session_manager_enabled=session_manager_enabled,
        session_manager_factory=session_manager_factory,
        build_direct_operator_reply=build_direct_operator_reply,
        build_chat_turn_event_stream=build_chat_turn_event_stream,
    )


def direct_chat_request_signature(body: dict) -> str:
    return direct_chat_transport_service.direct_chat_request_signature(body)


def direct_chat_stream_key(current_user: Any, body: dict) -> tuple[str, str, str]:
    return direct_chat_transport_service.direct_chat_stream_key(
        current_user,
        body,
        request_signature_fn=direct_chat_request_signature,
    )


def direct_chat_actor_key(current_user: Any, workspace_id: str, thread_id: str) -> str:
    return direct_chat_transport_service.direct_chat_actor_key(
        current_user,
        workspace_id,
        thread_id,
    )


def direct_chat_actor_key_for_user(user_id: str, workspace_id: str, thread_id: str) -> str:
    return direct_chat_transport_service.direct_chat_actor_key_for_user(
        user_id,
        workspace_id,
        thread_id,
    )


def direct_chat_session_manager_enabled(configured: Any = None) -> bool:
    if isinstance(configured, bool):
        return configured
    return str(os.getenv("ORION_DIRECT_CHAT_SESSION_MANAGER") or "").strip().lower() in {"1", "true", "yes", "on"}


def build_direct_chat_request_meta(
    *,
    body: dict,
    workspace_id: str,
    thread_id: str,
    client_request_id: str,
    agent_turn_request: Optional[Any] = None,
    trace_context: Optional[Any] = None,
) -> dict[str, Any]:
    turn_context_hints: dict[str, Any] = {}
    if isinstance(agent_turn_request, AgentTurnRequest):
        turn_context_hints = dict(agent_turn_request.context_hints or {})
    elif isinstance(agent_turn_request, dict):
        raw_hints = agent_turn_request.get("context_hints")
        if isinstance(raw_hints, dict):
            turn_context_hints = dict(raw_hints)
    turn_metadata = turn_context_hints.get("metadata") if isinstance(turn_context_hints.get("metadata"), dict) else {}
    provider = str(body.get("provider") or turn_context_hints.get("provider") or turn_metadata.get("provider") or "").strip()
    model = str(body.get("model") or turn_context_hints.get("model") or turn_metadata.get("model") or "").strip()
    reasoning_effort = str(
        body.get("reasoning_effort")
        or turn_context_hints.get("reasoning_effort")
        or turn_metadata.get("reasoning_effort")
        or ""
    ).strip()
    prior_messages = (
        body.get("prior_messages")
        if isinstance(body.get("prior_messages"), list)
        else turn_context_hints.get("prior_messages")
        if isinstance(turn_context_hints.get("prior_messages"), list)
        else []
    )
    approved_action = (
        body.get("approved_action")
        if isinstance(body.get("approved_action"), dict)
        else turn_context_hints.get("approved_action")
        if isinstance(turn_context_hints.get("approved_action"), dict)
        else None
    )
    max_iterations = body.get("max_iterations")
    if max_iterations is None:
        max_iterations = turn_context_hints.get("max_iterations")
    request_meta = {
        "request_id": client_request_id,
        "client_request_id": client_request_id,
        "workspace_id": workspace_id,
        "thread_id": thread_id,
        "provider": provider,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "prior_messages": prior_messages,
        "approved_action": approved_action,
        "max_iterations": max_iterations,
        "runtime_options": {
            "cwd": str(body.get("cwd") or "").strip(),
            "provider": provider,
            "model": model,
            "reasoning_effort": reasoning_effort,
            "thread_id": thread_id,
        },
    }
    if trace_context is not None:
        trace_id = str(getattr(trace_context, "trace_id", "") or "").strip()
        if trace_id:
            request_meta["trace"] = {
                "trace_id": trace_id,
                "workspace_id": str(getattr(trace_context, "workspace_id", "") or workspace_id or "").strip() or workspace_id,
                "tenant_id": str(getattr(trace_context, "tenant_id", "") or "").strip(),
                "thread_id": str(getattr(trace_context, "thread_id", "") or thread_id or "").strip() or thread_id,
                "run_id": str(getattr(trace_context, "run_id", "") or "").strip() or None,
                "root_agent_id": str(getattr(trace_context, "root_agent_id", "") or "").strip(),
            }
    return bind_agent_turn_request_meta(request_meta, agent_turn_request)


def build_direct_chat_event_producer(
    *,
    current_user: Any,
    body: dict,
    message: str,
    workspace_id: str,
    session_key: str,
    thread_id: str,
    client_request_id: str,
    services: DirectChatExecutionServices,
    agent_turn_request: Optional[AgentTurnRequest] = None,
    trace_context: Optional[Any] = None,
):
    turn_request = ensure_direct_chat_turn_request(
        current_user=current_user,
        body=body,
        workspace_id=workspace_id,
        thread_id=thread_id,
        client_request_id=client_request_id,
        message=message,
        agent_turn_request=agent_turn_request,
    )
    fallback_user_id = (
        str((current_user or {}).get("user_id") or "").strip()
        or str((current_user or {}).get("email") or "").strip().lower()
        or str((current_user or {}).get("auth_type") or "").strip()
    )
    identity = resolve_agent_turn_session_identity(
        turn_request,
        workspace_id=workspace_id,
        session_id=thread_id,
        user_id=fallback_user_id,
    )
    normalized_workspace_id = identity["workspace_id"]
    normalized_thread_id = identity["thread_id"]
    user_id = identity["user_id"]
    actor_key = direct_chat_actor_key_for_user(user_id, normalized_workspace_id, normalized_thread_id)
    direct_session_ctx = build_agent_turn_session_context(
        turn_request,
        workspace_id=normalized_workspace_id,
        session_id=normalized_thread_id,
        user_id=user_id,
    )
    if isinstance(direct_session_ctx, dict) and client_request_id:
        direct_session_ctx["request_id"] = client_request_id
        direct_session_ctx["client_request_id"] = client_request_id
    trace_id = str(getattr(trace_context, "trace_id", "") or "").strip()
    if trace_context is not None and isinstance(direct_session_ctx, dict):
        direct_session_ctx["trace_context"] = trace_context
        if trace_id:
            direct_session_ctx["trace_id"] = trace_id
            direct_session_ctx["trace"] = {
                "trace_id": trace_id,
                "workspace_id": str(getattr(trace_context, "workspace_id", "") or normalized_workspace_id or "").strip()
                or normalized_workspace_id,
                "tenant_id": str(getattr(trace_context, "tenant_id", "") or "").strip(),
                "thread_id": str(getattr(trace_context, "thread_id", "") or normalized_thread_id or "").strip()
                or normalized_thread_id,
                "run_id": str(getattr(trace_context, "run_id", "") or "").strip() or None,
                "root_agent_id": str(getattr(trace_context, "root_agent_id", "") or "").strip(),
            }

    if not services.session_manager_enabled():
        return services.build_direct_operator_reply(
            message=turn_request.message,
            workspace_id=normalized_workspace_id,
            requested_model=str(body.get("model") or "").strip(),
            requested_provider=str(body.get("provider") or "").strip(),
            thread_id=normalized_thread_id,
            prior_messages=body.get("prior_messages") if isinstance(body.get("prior_messages"), list) else [],
            reasoning_effort=str(body.get("reasoning_effort") or "").strip(),
            approved_action=body.get("approved_action") if isinstance(body.get("approved_action"), dict) else None,
            max_iterations=body.get("max_iterations"),
            session_ctx=direct_session_ctx,
            agent_turn_request=turn_request,
            trace_context=trace_context,
        )

    manager = services.session_manager_factory()
    try:
        manager.evict_idle_handles()
    except Exception as exc:
        failure_policy_service.log_degraded_operation(
            logger=logger,
            code="direct_chat_session_cleanup_failed",
            message="Failed to evict idle direct-chat session handles.",
            error_class=OBSERVABILITY_FAILURE,
            degraded_component="direct_chat_session_manager_cleanup",
            severity=SEVERITY_WARNING,
            retryable=False,
            status_code=500,
            request_id=client_request_id,
            trace_id=trace_id or None,
            metadata={
                "workspace_id": normalized_workspace_id,
                "thread_id": normalized_thread_id,
                "session_key": actor_key,
            },
            exc=exc,
        )
    request_meta = build_direct_chat_request_meta(
        body=body,
        workspace_id=normalized_workspace_id,
        thread_id=normalized_thread_id,
        client_request_id=client_request_id,
        agent_turn_request=turn_request,
        trace_context=trace_context,
    )
    return manager.iter_turn_events(
        session_id=actor_key,
        actor_key=actor_key,
        workspace_id=normalized_workspace_id,
        user_id=user_id,
        message=turn_request.message,
        request_meta=request_meta,
        turn_executor=services.build_chat_turn_event_stream,
    )


async def execute_direct_chat_turn_request(
    *,
    turn_request: AgentTurnRequest,
    current_user: Any,
    services: DirectChatExecutionServices,
    chat_body: Optional[dict[str, Any]] = None,
    trace_context: Optional[Any] = None,
) -> dict[str, Any]:
    body = build_turn_chat_body(turn_request)
    if isinstance(chat_body, dict):
        for key, value in chat_body.items():
            if value is not None:
                body[key] = value
    body["workspace_id"] = str(body.get("workspace_id") or turn_request.workspace_id or "default").strip() or "default"
    body["thread_id"] = str(body.get("thread_id") or turn_request.session_id or "direct-chat").strip() or "direct-chat"
    body["message"] = str(body.get("message") or turn_request.message or "")
    request_id = str(body.get("client_request_id") or turn_request.context_hints.get("request_id") or "").strip()
    if request_id:
        body["client_request_id"] = request_id
    workspace_id = str(turn_request.workspace_id or body.get("workspace_id") or "default").strip() or "default"
    session_key, thread_id, client_request_id = services.chat_stream_key(current_user, body)

    # ── UNIFIED ENTRY: route web chat through the SAME handle_sage_chat() that channels use ──
    import sys as _sys_turn
    print(f"[TRACE_UNIFIED_ENTRY] ws={workspace_id} channel={turn_request.channel} routing through handle_sage_chat (unified entry)", flush=True, file=_sys_turn.stderr)

    def producer():
        """Producer that routes through the unified Sage entry, forwarding
        generation events as they arrive via a thread-safe sink so the reply
        streams in real time instead of landing in one dump.

        The sink is a contextvar — it is inherited by the thread-pool thread
        that executes the generation loop.  When the sink is NOT set
        (Telegram / API / background paths) nothing changes.
        """
        import asyncio as _asyncio
        import queue as _queue
        import threading as _threading

        from server_modules.direct_chat_generation_service import (
            _GENERATION_EVENT_SINK,
        )

        # Resolve actor info from turn_request
        actor = getattr(turn_request, 'actor', None)
        if isinstance(actor, dict):
            sender_id = str(actor.get('id') or '').strip()
            sender_name = str(actor.get('display_name') or '').strip()
        else:
            sender_id = ''
            sender_name = ''

        # Thread-safe queue so the generation thread (Thread 3 in the pool)
        # can push events and the SSE thread (this one) can pop them.
        event_queue: _queue.Queue = _queue.Queue()

        def _sink(event: dict):
            event_queue.put(event)

        # Set BEFORE spawning the thread so the child thread inherits it.
        _GENERATION_EVENT_SINK.set(_sink)

        result_container: dict = {}
        error_container: dict = {}

        def _run_sage():
            try:
                _loop = _asyncio.new_event_loop()
                _asyncio.set_event_loop(_loop)
                from server_modules.sage_turn_adapter import execute_sage_turn
                sage_result = _loop.run_until_complete(execute_sage_turn(
                    workspace_id=workspace_id,
                    message=str(turn_request.message or ''),
                    surface='web',
                    mode='owner_sage',
                    current_user=current_user if isinstance(current_user, dict) else None,
                    channel_origin=str(turn_request.channel or 'web'),
                    channel_sender_id=sender_id,
                    channel_sender_name=sender_name,
                    attachments=list(turn_request.attachments) if getattr(turn_request, 'attachments', None) else None,
                    thread_id=thread_id,
                    request_id=client_request_id,
                ))
                _loop.close()
                result_container['value'] = sage_result
            except BaseException as _err:
                error_container['error'] = _err
            finally:
                # Ensure the consumer always wakes up — even if
                # execute_sage_turn raises before pushing a single event.
                event_queue.put(_SENTINEL)

        _SENTINEL = object()
        _thread = _threading.Thread(target=_run_sage, daemon=True)
        _thread.start()

        # Drain the queue as events arrive, yielding each one to the SSE
        # transport.  The thread is still running; we keep yielding until
        # the sentinel arrives.
        while True:
            try:
                event = event_queue.get(timeout=0.15)
            except _queue.Empty:
                if not _thread.is_alive():
                    break
                continue
            if event is _SENTINEL:
                break
            if isinstance(event, dict):
                yield event

        _thread.join()

        if 'error' in error_container:
            # Classify rather than re-raise: an unhandled exception here
            # propagates as a raw 500, which the frontend renders as
            # "Sage hit a temporary server issue" — masking known, fixable
            # states (no provider configured, auth failed, etc.) behind a
            # generic message. Route through the same classifier the
            # Telegram/Discord channel dispatcher uses so the reply is
            # honest and actionable instead.
            from server_modules.sage_command_dispatcher import classify_error as _classify_stream_error
            _raw_err = str(error_container['error'])
            _classified = _classify_stream_error(_raw_err, raw_error=_raw_err)
            yield {
                "type": "final",
                "payload": {
                    "reply": _classified,
                    "actions": [],
                    "mode": "error",
                    "error": _classified,
                },
            }
            return

        sage_result = result_container.get('value')
        if not isinstance(sage_result, dict):
            sage_result = {
                'message': getattr(sage_result, 'message', '') or '',
                'error': getattr(sage_result, 'error', None),
                'tool_calls': list(getattr(sage_result, 'tool_calls', None) or []),
                'provider': getattr(sage_result, 'provider', '') or '',
                'model': getattr(sage_result, 'model', None),
            }

        # Convert sage result to a single final SSE event.
        reply_text = str((sage_result or {}).get('message') or '').strip()
        error_text = str((sage_result or {}).get('error') or '').strip()

        # Never surface the internal [SILENT] sentinel as user-visible text.
        if reply_text == '[SILENT]' or reply_text.startswith('[SILENT]'):
            reply_text = ''

        # If the generator already streamed a full reply via chunks, the
        # frontend used the streamed text as visual feedback but will
        # replace it with this final payload.  That is correct — the final
        # payload is the canonical answer, the stream was live progress.
        if error_text and not reply_text:
            yield {
                "type": "final",
                "payload": {
                    "reply": error_text,
                    "actions": [],
                    "mode": "error",
                    "error": error_text,
                },
            }
        else:
            yield {
                "type": "final",
                "payload": {
                    "reply": reply_text,
                    "actions": [],
                    "mode": "answer",
                    "error": "",
                },
            }

    return {
        "kind": "direct_chat_stream",
        "workspace_id": workspace_id,
        "session_key": session_key,
        "thread_id": thread_id,
        "client_request_id": client_request_id,
        "producer": producer,
        "turn_request": turn_request,
    }
