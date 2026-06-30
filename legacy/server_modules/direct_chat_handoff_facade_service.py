from __future__ import annotations

from typing import Any, Callable, Dict, Iterator, List, Optional

from server_modules import direct_chat_handoff_service


def _start_run_dependencies() -> tuple[Callable[[Any], Dict[str, Any]], Any]:
    from server_modules.runs_delegation import _delegation_run_execution_services
    from server_modules.runtime_models import RunStartRequest
    from server_modules.turn_runtime import execute_unowned_system_run_start_request_via_turn_runtime

    return (
        lambda request: execute_unowned_system_run_start_request_via_turn_runtime(
            request,
            services=_delegation_run_execution_services(),
        ),
        RunStartRequest,
    )


def _snapshot_dependencies() -> tuple[Dict[str, Any], Callable[[str], Any], Callable[[str, Dict[str, Any]], Dict[str, Any]]]:
    from server_modules.runs_delegation import _lookup_run_snapshot
    from server_modules.runs_output import _serialize_run_snapshot
    from server_modules.shared import runs

    return runs, _lookup_run_snapshot, _serialize_run_snapshot


def durable_run_preferred_response(
    message: str,
    *,
    run_action_fn: Callable[[str], Dict[str, Any]],
) -> Dict[str, Any]:
    return direct_chat_handoff_service.durable_run_preferred_response(
        message,
        run_action_fn=run_action_fn,
    )


def run_handoff_execution_target(availability: Dict[str, Any]) -> str:
    return direct_chat_handoff_service.run_handoff_execution_target(availability)


def can_auto_start_run_handoff(availability: Dict[str, Any]) -> bool:
    return direct_chat_handoff_service.can_auto_start_run_handoff(availability)


def direct_chat_run_handoff_failure_payload(
    message: str,
    error_detail: str,
    *,
    run_action_fn: Callable[[str], Dict[str, Any]],
) -> Dict[str, Any]:
    return direct_chat_handoff_service.direct_chat_run_handoff_failure_payload(
        message,
        error_detail,
        run_action_fn=run_action_fn,
    )


def start_direct_chat_run_handoff(
    *,
    message: str,
    workspace_id: str,
    requested_provider: str,
    requested_model: str,
    thread_id: str,
    availability: Dict[str, Any],
    max_iterations: Optional[int],
    safe_positive_int_fn: Callable[[Any, int], int],
) -> Dict[str, Any]:
    start_run_request_fn, run_start_request_cls = _start_run_dependencies()
    return direct_chat_handoff_service.start_direct_chat_run_handoff(
        message=message,
        workspace_id=workspace_id,
        requested_provider=requested_provider,
        requested_model=requested_model,
        thread_id=thread_id,
        availability=availability,
        max_iterations=max_iterations,
        start_run_request_fn=start_run_request_fn,
        run_start_request_cls=run_start_request_cls,
        safe_positive_int_fn=safe_positive_int_fn,
    )


def direct_chat_run_handoff_reply(
    started: Dict[str, Any],
    *,
    open_action_fn: Callable[..., Dict[str, Any]],
) -> Dict[str, Any]:
    return direct_chat_handoff_service.direct_chat_run_handoff_reply(
        started,
        open_action_fn=open_action_fn,
    )


def direct_chat_run_actions(
    run_id: str,
    *,
    waiting_for_confirmation: bool = False,
    open_action_fn: Callable[..., Dict[str, Any]],
) -> List[Dict[str, Any]]:
    return direct_chat_handoff_service.direct_chat_run_actions(
        run_id,
        waiting_for_confirmation=waiting_for_confirmation,
        open_action_fn=open_action_fn,
    )


def direct_chat_run_snapshot(run_id: str) -> tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    runs_mapping, lookup_run_snapshot_fn, serialize_run_snapshot_fn = _snapshot_dependencies()
    return direct_chat_handoff_service.direct_chat_run_snapshot(
        run_id,
        runs_mapping=runs_mapping,
        lookup_run_snapshot_fn=lookup_run_snapshot_fn,
        serialize_run_snapshot_fn=serialize_run_snapshot_fn,
    )


def direct_chat_run_event_to_step(run_id: str, event: Dict[str, Any]) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    return direct_chat_handoff_service.direct_chat_run_event_to_step(run_id, event)


def direct_chat_run_snapshot_to_step(run_id: str, snapshot: Dict[str, Any]) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
    return direct_chat_handoff_service.direct_chat_run_snapshot_to_step(run_id, snapshot)


def direct_chat_run_final_payload(
    *,
    run_id: str,
    run: Optional[Dict[str, Any]],
    snapshot: Dict[str, Any],
    requested_workspace_id: str,
    requested_provider: str,
    requested_model: str,
    reasoning_effort: Optional[str],
    connected_systems: List[str],
    tool_capabilities: List[Dict[str, Any]],
    fallback_reason: Optional[str],
    reply_override: Optional[str] = None,
    continuing: bool = False,
    build_context_used_fn: Callable[..., Dict[str, Any]],
    open_action_fn: Callable[..., Dict[str, Any]],
) -> Dict[str, Any]:
    return direct_chat_handoff_service.direct_chat_run_final_payload(
        run_id=run_id,
        run=run,
        snapshot=snapshot,
        requested_workspace_id=requested_workspace_id,
        requested_provider=requested_provider,
        requested_model=requested_model,
        reasoning_effort=reasoning_effort,
        connected_systems=connected_systems,
        tool_capabilities=tool_capabilities,
        fallback_reason=fallback_reason,
        reply_override=reply_override,
        continuing=continuing,
        build_context_used_fn=build_context_used_fn,
        open_action_fn=open_action_fn,
    )


def stream_direct_chat_run_handoff(
    *,
    started_run: Dict[str, Any],
    requested_workspace_id: str,
    requested_provider: str,
    requested_model: str,
    reasoning_effort: Optional[str],
    connected_systems: List[str],
    tool_capabilities: List[Dict[str, Any]],
    fallback_reason: Optional[str],
    direct_chat_run_snapshot_fn: Callable[[str], tuple[Optional[Dict[str, Any]], Dict[str, Any]]],
    direct_chat_run_event_to_step_fn: Callable[[str, Dict[str, Any]], tuple[Optional[Dict[str, Any]], Optional[str]]],
    direct_chat_run_snapshot_to_step_fn: Callable[[str, Dict[str, Any]], tuple[Optional[str], Optional[Dict[str, Any]]]],
    direct_chat_run_final_payload_fn: Callable[..., Dict[str, Any]],
    open_action_fn: Callable[..., Dict[str, Any]],
    build_context_used_fn: Callable[..., Dict[str, Any]],
    live_window_seconds: float,
    poll_seconds: float,
    monotonic_fn: Callable[[], float],
    sleep_fn: Callable[[float], None],
) -> Iterator[Dict[str, Any]]:
    yield from direct_chat_handoff_service.stream_direct_chat_run_handoff(
        started_run=started_run,
        requested_workspace_id=requested_workspace_id,
        requested_provider=requested_provider,
        requested_model=requested_model,
        reasoning_effort=reasoning_effort,
        connected_systems=connected_systems,
        tool_capabilities=tool_capabilities,
        fallback_reason=fallback_reason,
        direct_chat_run_snapshot_fn=direct_chat_run_snapshot_fn,
        direct_chat_run_event_to_step_fn=direct_chat_run_event_to_step_fn,
        direct_chat_run_snapshot_to_step_fn=direct_chat_run_snapshot_to_step_fn,
        direct_chat_run_final_payload_fn=direct_chat_run_final_payload_fn,
        open_action_fn=open_action_fn,
        build_context_used_fn=build_context_used_fn,
        live_window_seconds=live_window_seconds,
        poll_seconds=poll_seconds,
        monotonic_fn=monotonic_fn,
        sleep_fn=sleep_fn,
    )
