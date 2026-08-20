"""
Execution switchboard for canonical turns.

Prompt 01 freezes this module as the bridge that decides whether a canonical
`AgentTurnRequest` resolves through direct-chat execution or durable execution.
It is not an alternate engine and should not absorb route, connector, memory,
quota, or channel policy behavior.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, Optional

from server_modules.agent_turn import AgentTurnRequest
from server_modules.direct_chat_service import DirectChatExecutionServices, execute_direct_chat_turn_request
from server_modules import run_service as run_service
from server_modules.run_service import RunExecutionServices, execute_durable_turn_request


@dataclass(slots=True)
class TurnExecutionServices:
    run_execution: RunExecutionServices
    direct_chat: DirectChatExecutionServices


def build_turn_execution_services(
    *,
    run_execution: RunExecutionServices,
    direct_chat: DirectChatExecutionServices,
) -> TurnExecutionServices:
    return TurnExecutionServices(
        run_execution=run_execution,
        direct_chat=direct_chat,
    )


async def execute_agent_turn_request(
    *,
    turn_request: AgentTurnRequest,
    current_user: Any,
    services: TurnExecutionServices,
    chat_body: Optional[dict[str, Any]] = None,
    run_request: Optional[Any] = None,
    trace_context: Optional[Any] = None,
) -> dict[str, Any]:
    # Internal delegate. Not an alternate turn engine. Called only from agent_turn().
    _exec_mode = str(getattr(turn_request, 'execution_mode', '') or '').strip().lower()

    # ── Durable runs are EXPLICIT, never the default ──
    # A conversational message ALWAYS takes the streaming chat loop.
    # Durable/background runs only trigger when explicitly requested.
    if _exec_mode == "durable":
        durable_execution = await run_service.execute_durable_agent_turn_dispatch(
            turn_request=turn_request,
            current_user=current_user,
            services=services.run_execution,
            base_request=run_request,
            trace_context=trace_context,
            execute_durable_turn_request_fn=execute_durable_turn_request,
        )
        if durable_execution is not None:
            return durable_execution

    body = dict(chat_body or {})
    return await execute_direct_chat_turn_request(
        turn_request=turn_request,
        current_user=current_user,
        services=services.direct_chat,
        chat_body=body,
        trace_context=trace_context,
    )


async def execute_run_start_request_via_turn_runtime(
    request: Any,
    *,
    current_user: Any,
    stamp_request_owner_fn: Any,
    services: RunExecutionServices,
) -> Dict[str, Any]:
    from server_modules import turn_ingress_service

    return await turn_ingress_service.start_run_start(
        request,
        current_user=current_user,
        stamp_request_owner_fn=stamp_request_owner_fn,
        services=services,
    )


def execute_system_run_start_request_via_turn_runtime(
    request: Any,
    *,
    stamp_request_owner_fn: Any,
    services: RunExecutionServices,
    current_user: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    from server_modules import turn_ingress_service

    return turn_ingress_service.start_system_run_start(
        request,
        stamp_request_owner_fn=stamp_request_owner_fn,
        services=services,
        current_user=current_user,
    )


def execute_unowned_system_run_start_request_via_turn_runtime(
    request: Any,
    *,
    services: RunExecutionServices,
    current_user: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return run_service.execute_unowned_system_run_start_request_via_turn_runtime(
        request,
        services=services,
        current_user=current_user,
        execute_system_run_start_request_via_turn_runtime_fn=execute_system_run_start_request_via_turn_runtime,
    )


def execute_built_unowned_system_run_start_request_via_turn_runtime(
    request: Any,
    *,
    execute_system_run_start_request_via_turn_runtime_fn: Any,
    build_run_execution_services_fn: Any,
    current_user: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return run_service.execute_built_unowned_system_run_start_request_via_turn_runtime(
        request,
        execute_system_run_start_request_via_turn_runtime_fn=execute_system_run_start_request_via_turn_runtime_fn,
        build_run_execution_services_fn=build_run_execution_services_fn,
        current_user=current_user,
    )


def execute_built_legacy_unowned_system_run_start_request_via_turn_runtime(
    request: Any,
    *,
    execute_system_run_start_request_via_turn_runtime_fn: Any,
    build_run_execution_services_fn: Any,
    create_run_from_request_fn: Any,
    current_user: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return run_service.execute_built_legacy_unowned_system_run_start_request_via_turn_runtime(
        request,
        execute_system_run_start_request_via_turn_runtime_fn=execute_system_run_start_request_via_turn_runtime_fn,
        build_run_execution_services_fn=build_run_execution_services_fn,
        create_run_from_request_fn=create_run_from_request_fn,
        current_user=current_user,
        execute_built_unowned_system_run_start_request_via_turn_runtime_fn=execute_built_unowned_system_run_start_request_via_turn_runtime,
    )


def build_execute_unowned_system_run_start_request_via_turn_runtime(
    *,
    execute_unowned_system_run_start_request_via_turn_runtime_fn: Any = execute_unowned_system_run_start_request_via_turn_runtime,
) -> Any:
    return run_service.build_execute_unowned_system_run_start_request_via_turn_runtime(
        execute_unowned_system_run_start_request_via_turn_runtime_fn=execute_unowned_system_run_start_request_via_turn_runtime_fn,
    )
