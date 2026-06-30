from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from server_modules import direct_chat_entry_service
from server_modules import direct_chat_generation_service
from server_modules import direct_chat_response_service
from server_modules import direct_chat_runtime_service
from server_modules import no_provider_service


@dataclass(frozen=True)
class DirectChatRuntimeFacadeCallbacks:
    compact_text: Callable[[Any], str]
    safe_positive_int: Callable[[Any, int], int]
    resolve_chat_local_path: Callable[[str], Any]
    extract_first_path_reference: Callable[[str], str]
    extract_first_url: Callable[[str], str]
    parse_page_state: Callable[[str], Any]
    parse_memory_write: Callable[[str], Any]
    parse_memory_read: Callable[[str], Any]
    handle_memory_request: Callable[[str, str], Any]
    parse_tool_name: Callable[[str], Any]
    tool_arguments_payload: Callable[[Any], Dict[str, Any]]
    approval_required_for_direct_tool: Callable[[str, str, Dict[str, Any], List[Dict[str, Any]]], bool]
    agent_machine_full_trust_for_session: Callable[[Optional[Dict[str, Any]]], bool]
    execute_single_direct_tool_call: Callable[..., str]
    direct_chat_session_key: Callable[[str, str], str]
    resolved_chat_iteration_limit: Callable[[Optional[int]], int]
    session_model_preference: Callable[[str, str], str]
    normalize_reasoning_effort: Callable[[str], Optional[str]]
    parse_slash_command: Callable[[str], Any]
    set_session_model_preference: Callable[[str, str, str], None]
    mark_thread_cleared: Callable[[str, str], None]
    normalize_prior_messages: Callable[[Optional[List[Dict[str, Any]]]], List[Dict[str, Any]]]
    consume_thread_cleared: Callable[[str, str], bool]
    compact_conversation_history: Callable[..., Any]
    build_proactive_suggestions: Callable[[str], List[str]]
    direct_tool_session_key: Callable[[str, str], str]
    resolve_direct_chat_availability: Callable[[Optional[Dict[str, Any]]], Dict[str, Any]]
    connected_system_labels: Callable[[List[Dict[str, Any]]], List[str]]
    context_tool_capabilities: Callable[[str], List[Dict[str, Any]]]
    build_direct_chat_tools: Callable[..., List[Dict[str, Any]]]
    build_local_direct_chat_tools: Callable[..., List[Dict[str, Any]]]
    build_builtin_direct_chat_tools: Callable[..., List[Dict[str, Any]]]
    build_always_on_direct_chat_tools: Callable[[], List[Dict[str, Any]]]
    build_registry_entries: Callable[..., List[Any]]
    normalize_direct_approved_action: Callable[[Optional[Dict[str, Any]]], Optional[Dict[str, str]]]
    build_context_used: Callable[..., Dict[str, Any]]
    direct_chat_compaction_token_limit: int
    with_context_used: Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]]
    connected_provider_tokens: Callable[[str], List[str]]
    active_run_count: Callable[[str], int]
    slash_command_help_text: Callable[[], str]
    execute_direct_tool_calls: Callable[..., str]
    direct_chat_credentials: Callable[[str, str], Dict[str, Any]]
    capture_exception: Callable[[BaseException], None]
    tool_write_action_available: Callable[[str, str, List[Dict[str, Any]]], bool]
    approved_action_to_tool_call: Callable[[Dict[str, str]], Dict[str, Any]]
    resolve_provider_for_direct_chat_message: Callable[[str, str, str], tuple[str, Dict[str, Any]]]
    plan_direct_chat_route: Callable[..., Any]
    start_direct_chat_run_handoff: Callable[..., Dict[str, Any]]
    direct_chat_run_handoff_reply: Callable[[Dict[str, Any]], Dict[str, Any]]
    stream_direct_chat_run_handoff: Callable[..., Any]
    direct_chat_run_handoff_failure_payload: Callable[[str, str], Dict[str, Any]]
    supports_direct_message_native_chat: Callable[[str, Optional[Dict[str, Any]]], bool]
    supported_providers: List[str]
    build_direct_chat_system_prompt: Callable[..., str]
    direct_chat_workspace_context_text: Callable[[str], str]
    direct_chat_generation_services: direct_chat_generation_service.DirectChatGenerationServices
    no_provider_reasoning_required_response: Callable[[], Dict[str, Any]]
    # Deprecated compatibility fields retained so older facades/tests can still
    # construct this callback bundle while the implementation reads memory through
    # memory_service directly.
    list_memory_entries: Optional[Callable[[str], Any]] = None
    get_memory: Optional[Callable[[str], Any]] = None
    delete_memory: Optional[Callable[[str, str], Any]] = None


def build_no_provider_execution_services(
    callbacks: DirectChatRuntimeFacadeCallbacks,
) -> no_provider_service.NoProviderExecutionServices:
    return no_provider_service.NoProviderExecutionServices(
        compact_text=callbacks.compact_text,
        safe_positive_int=callbacks.safe_positive_int,
        resolve_local_path=callbacks.resolve_chat_local_path,
        extract_first_path_reference=callbacks.extract_first_path_reference,
        extract_first_url=callbacks.extract_first_url,
        parse_page_state=callbacks.parse_page_state,
        parse_memory_write=callbacks.parse_memory_write,
        parse_memory_read=callbacks.parse_memory_read,
        handle_memory_request=callbacks.handle_memory_request,
        parse_tool_name=callbacks.parse_tool_name,
        tool_arguments_payload=callbacks.tool_arguments_payload,
        approval_required_for_tool=callbacks.approval_required_for_direct_tool,
        agent_machine_full_trust_for_session=callbacks.agent_machine_full_trust_for_session,
        execute_single_tool_call=callbacks.execute_single_direct_tool_call,
    )


def build_direct_tool_approval_response(
    *,
    tool_calls: List[Dict[str, Any]],
    tool_capabilities: List[Dict[str, Any]],
    session_ctx: Optional[Dict[str, Any]] = None,
    callbacks: DirectChatRuntimeFacadeCallbacks,
) -> Optional[Dict[str, Any]]:
    return no_provider_service.build_direct_tool_approval_response(
        tool_calls=tool_calls,
        tool_capabilities=tool_capabilities,
        services=build_no_provider_execution_services(callbacks),
        session_ctx=session_ctx,
    )


def message_has_obvious_direct_tool_intent(
    message: str,
    tools: List[Dict[str, Any]],
    callbacks: DirectChatRuntimeFacadeCallbacks,
) -> bool:
    return no_provider_service.has_obvious_direct_tool_intent(
        message,
        tools,
        compact_text=callbacks.compact_text,
        extract_first_path_reference=callbacks.extract_first_path_reference,
        extract_first_url=callbacks.extract_first_url,
        parse_memory_write=callbacks.parse_memory_write,
        parse_memory_read=callbacks.parse_memory_read,
    )


def prepare_direct_chat_request(
    *,
    resolved_turn_request: Optional[Any],
    session_ctx: Optional[Dict[str, Any]],
    message: str,
    workspace_id: str,
    thread_id: str,
    requested_model: str,
    requested_provider: str,
    prior_messages: Optional[List[Dict[str, Any]]],
    reasoning_effort: str,
    availability: Optional[Dict[str, Any]],
    approved_action: Optional[Dict[str, Any]],
    max_iterations: Optional[int],
    callbacks: DirectChatRuntimeFacadeCallbacks,
) -> direct_chat_entry_service.PreparedDirectChatRequest:
    return direct_chat_entry_service.prepare_direct_chat_request(
        resolved_turn_request=resolved_turn_request,
        session_ctx=session_ctx,
        message=message,
        workspace_id=workspace_id,
        thread_id=thread_id,
        requested_model=requested_model,
        requested_provider=requested_provider,
        prior_messages=prior_messages,
        reasoning_effort=reasoning_effort,
        availability=availability,
        approved_action=approved_action,
        max_iterations=max_iterations,
        direct_chat_session_key_fn=callbacks.direct_chat_session_key,
        resolved_chat_iteration_limit_fn=callbacks.resolved_chat_iteration_limit,
        session_model_preference_fn=callbacks.session_model_preference,
        normalize_reasoning_effort_fn=callbacks.normalize_reasoning_effort,
        parse_slash_command_fn=callbacks.parse_slash_command,
        set_session_model_preference_fn=callbacks.set_session_model_preference,
        mark_thread_cleared_fn=callbacks.mark_thread_cleared,
        normalize_prior_messages_fn=callbacks.normalize_prior_messages,
        consume_thread_cleared_fn=callbacks.consume_thread_cleared,
        compact_conversation_history_fn=callbacks.compact_conversation_history,
        build_proactive_suggestions_fn=callbacks.build_proactive_suggestions,
        direct_tool_session_key_fn=callbacks.direct_tool_session_key,
        resolve_direct_chat_availability_fn=callbacks.resolve_direct_chat_availability,
        connected_system_labels_fn=callbacks.connected_system_labels,
        context_tool_capabilities_fn=callbacks.context_tool_capabilities,
        build_direct_chat_tools_fn=callbacks.build_direct_chat_tools,
        build_local_direct_chat_tools_fn=callbacks.build_local_direct_chat_tools,
        build_builtin_direct_chat_tools_fn=callbacks.build_builtin_direct_chat_tools,
        build_always_on_direct_chat_tools_fn=callbacks.build_always_on_direct_chat_tools,
        build_registry_entries_fn=callbacks.build_registry_entries,
        normalize_direct_approved_action_fn=callbacks.normalize_direct_approved_action,
        build_context_used_fn=callbacks.build_context_used,
        direct_chat_compaction_token_limit=callbacks.direct_chat_compaction_token_limit,
    )


def build_direct_chat_response_services(
    callbacks: DirectChatRuntimeFacadeCallbacks,
) -> direct_chat_response_service.DirectChatResponseServices:
    return direct_chat_response_service.DirectChatResponseServices(
        with_context_used=callbacks.with_context_used,
        build_context_used=callbacks.build_context_used,
        connected_provider_tokens=callbacks.connected_provider_tokens,
        active_run_count=callbacks.active_run_count,
        slash_command_help_text=callbacks.slash_command_help_text,
        execute_direct_tool_calls=callbacks.execute_direct_tool_calls,
        direct_chat_credentials=callbacks.direct_chat_credentials,
        capture_exception=callbacks.capture_exception,
    )


def build_direct_chat_runtime_services(
    callbacks: DirectChatRuntimeFacadeCallbacks,
) -> direct_chat_runtime_service.DirectChatRuntimeServices:
    return direct_chat_runtime_service.DirectChatRuntimeServices(
        prepare_direct_chat_request=lambda **kwargs: prepare_direct_chat_request(callbacks=callbacks, **kwargs),
        direct_chat_response_services=build_direct_chat_response_services(callbacks),
        with_context_used=callbacks.with_context_used,
        tool_write_action_available=callbacks.tool_write_action_available,
        approved_action_to_tool_call=callbacks.approved_action_to_tool_call,
        message_has_obvious_direct_tool_intent=lambda message, tools: message_has_obvious_direct_tool_intent(
            message,
            tools,
            callbacks,
        ),
        no_provider_execution_services=build_no_provider_execution_services(callbacks),
        build_context_used=callbacks.build_context_used,
        resolve_provider_for_direct_chat_message=callbacks.resolve_provider_for_direct_chat_message,
        plan_direct_chat_route=callbacks.plan_direct_chat_route,
        start_direct_chat_run_handoff=callbacks.start_direct_chat_run_handoff,
        direct_chat_run_handoff_reply=callbacks.direct_chat_run_handoff_reply,
        stream_direct_chat_run_handoff=callbacks.stream_direct_chat_run_handoff,
        direct_chat_run_handoff_failure_payload=callbacks.direct_chat_run_handoff_failure_payload,
        supports_direct_message_native_chat=callbacks.supports_direct_message_native_chat,
        supported_providers=callbacks.supported_providers,
        build_direct_chat_system_prompt=callbacks.build_direct_chat_system_prompt,
        direct_chat_workspace_context_text=callbacks.direct_chat_workspace_context_text,
        direct_chat_generation_services=callbacks.direct_chat_generation_services,
        no_provider_reasoning_required_response=callbacks.no_provider_reasoning_required_response,
        capture_exception=callbacks.capture_exception,
    )
