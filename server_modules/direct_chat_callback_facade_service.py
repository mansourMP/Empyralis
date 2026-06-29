from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List

from server_modules import direct_chat_generation_service, direct_chat_hosted_usage_service
from server_modules import direct_chat_runtime_facade_service


@dataclass(frozen=True)
class DirectChatCallbackFacadeInputs:
    thinking_step_payload: Callable[..., Dict[str, Any]]
    build_context_used: Callable[..., Dict[str, Any]]
    build_direct_tool_approval_response: Callable[..., Dict[str, Any] | None]
    parse_tool_name: Callable[[str], Any]
    tool_arguments_payload: Callable[[Any], Dict[str, Any]]
    parse_page_state: Callable[[str], Any]
    direct_tool_step_payload: Callable[..., Dict[str, Any]]
    execute_single_direct_tool_call: Callable[..., str]
    direct_tool_followup_message: Callable[[str, str], str]
    suggest_actions: Callable[[str, Dict[str, Any]], List[Dict[str, Any]]]
    clear_direct_tool_loop_state: Callable[[str], None]
    persist_direct_chat_memory_best_effort: Callable[..., None]
    persist_direct_chat_transcript_best_effort: Callable[..., None]
    persist_direct_chat_hosted_usage_best_effort: Callable[..., None]
    record_direct_tool_signature: Callable[[str, Dict[str, Any]], bool]
    direct_chat_error_reply: Callable[[str], str]
    capture_exception: Callable[[BaseException], None]
    generate_chat_reply_stream_with_provider_fallback: Callable[..., Any]
    compact_text: Callable[[Any], str]
    safe_positive_int: Callable[[Any, int], int]
    resolve_chat_local_path: Callable[[str], Any]
    extract_first_path_reference: Callable[[str], str]
    extract_first_url: Callable[[str], str]
    parse_memory_write: Callable[[str], Any]
    parse_memory_read: Callable[[str], Any]
    handle_memory_request: Callable[[str, str], Any]
    approval_required_for_direct_tool: Callable[[str, str, Dict[str, Any], List[Dict[str, Any]]], bool]
    agent_machine_full_trust_for_session: Callable[[Dict[str, Any] | None], bool]
    direct_chat_session_key: Callable[[str, str], str]
    resolved_chat_iteration_limit: Callable[[int | None], int]
    session_model_preference: Callable[[str, str], str]
    normalize_reasoning_effort: Callable[[str], str | None]
    parse_slash_command: Callable[[str], Any]
    set_session_model_preference: Callable[[str, str, str], None]
    mark_thread_cleared: Callable[[str, str], None]
    normalize_prior_messages: Callable[[List[Dict[str, Any]] | None], List[Dict[str, Any]]]
    consume_thread_cleared: Callable[[str, str], bool]
    compact_conversation_history: Callable[..., Any]
    build_proactive_suggestions: Callable[[str], List[str]]
    direct_tool_session_key: Callable[[str, str], str]
    resolve_direct_chat_availability: Callable[[Dict[str, Any] | None], Dict[str, Any]]
    connected_system_labels: Callable[[List[Dict[str, Any]]], List[str]]
    context_tool_capabilities: Callable[[str], List[Dict[str, Any]]]
    build_direct_chat_tools: Callable[..., List[Dict[str, Any]]]
    build_local_direct_chat_tools: Callable[..., List[Dict[str, Any]]]
    build_builtin_direct_chat_tools: Callable[..., List[Dict[str, Any]]]
    build_always_on_direct_chat_tools: Callable[[], List[Dict[str, Any]]]
    build_registry_entries: Callable[..., List[Any]]
    normalize_direct_approved_action: Callable[[Any], Dict[str, str] | None]
    direct_chat_compaction_token_limit: int
    with_context_used: Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]]
    connected_provider_tokens: Callable[[str], List[str]]
    list_memory_entries: Callable[[str], List[Any]]
    active_run_count: Callable[[str], int]
    get_memory: Callable[[str], str]
    delete_memory: Callable[[str, str], bool]
    slash_command_help_text: Callable[[], str]
    execute_direct_tool_calls: Callable[..., str]
    direct_chat_credentials: Callable[[str, str], Dict[str, Any]]
    tool_write_action_available: Callable[[str, str, List[Dict[str, Any]]], bool]
    approved_action_to_tool_call: Callable[[Dict[str, str]], Dict[str, Any]]
    resolve_provider_for_direct_chat_message: Callable[[str, str, str], tuple[str, Dict[str, Any]]]
    plan_direct_chat_route: Callable[..., Any]
    start_direct_chat_run_handoff: Callable[..., Dict[str, Any]]
    direct_chat_run_handoff_reply: Callable[[Dict[str, Any]], Dict[str, Any]]
    stream_direct_chat_run_handoff: Callable[..., Any]
    direct_chat_run_handoff_failure_payload: Callable[[str, str], Dict[str, Any]]
    supports_direct_message_native_chat: Callable[[str, Dict[str, Any] | None], bool]
    supported_providers: List[str]
    build_direct_chat_system_prompt: Callable[..., str]
    direct_chat_workspace_context_text: Callable[[str], str]
    no_provider_reasoning_required_response: Callable[[], Dict[str, Any]]


def build_direct_chat_generation_services(
    inputs: DirectChatCallbackFacadeInputs,
) -> direct_chat_generation_service.DirectChatGenerationServices:
    return direct_chat_generation_service.DirectChatGenerationServices(
        thinking_step_payload=inputs.thinking_step_payload,
        build_context_used=inputs.build_context_used,
        build_direct_tool_approval_response=inputs.build_direct_tool_approval_response,
        parse_tool_name=inputs.parse_tool_name,
        tool_arguments_payload=inputs.tool_arguments_payload,
        parse_page_state=inputs.parse_page_state,
        direct_tool_step_payload=inputs.direct_tool_step_payload,
        execute_single_direct_tool_call=inputs.execute_single_direct_tool_call,
        direct_tool_followup_message=inputs.direct_tool_followup_message,
        suggest_actions=inputs.suggest_actions,
        clear_direct_tool_loop_state=inputs.clear_direct_tool_loop_state,
        persist_direct_chat_memory_best_effort=inputs.persist_direct_chat_memory_best_effort,
        persist_direct_chat_transcript_best_effort=inputs.persist_direct_chat_transcript_best_effort,
        persist_direct_chat_hosted_usage_best_effort=inputs.persist_direct_chat_hosted_usage_best_effort,
        record_direct_tool_signature=inputs.record_direct_tool_signature,
        direct_chat_error_reply=inputs.direct_chat_error_reply,
        capture_exception=inputs.capture_exception,
        generate_chat_reply_stream_with_provider_fallback=inputs.generate_chat_reply_stream_with_provider_fallback,
        reserve_direct_chat_hosted_usage_best_effort=direct_chat_hosted_usage_service.reserve_direct_chat_hosted_usage_best_effort,
        release_direct_chat_hosted_usage_reservation_best_effort=direct_chat_hosted_usage_service.release_direct_chat_hosted_usage_reservation_best_effort,
    )


def build_direct_chat_runtime_facade_callbacks(
    inputs: DirectChatCallbackFacadeInputs,
) -> direct_chat_runtime_facade_service.DirectChatRuntimeFacadeCallbacks:
    return direct_chat_runtime_facade_service.DirectChatRuntimeFacadeCallbacks(
        compact_text=inputs.compact_text,
        safe_positive_int=inputs.safe_positive_int,
        resolve_chat_local_path=inputs.resolve_chat_local_path,
        extract_first_path_reference=inputs.extract_first_path_reference,
        extract_first_url=inputs.extract_first_url,
        parse_page_state=inputs.parse_page_state,
        parse_memory_write=inputs.parse_memory_write,
        parse_memory_read=inputs.parse_memory_read,
        handle_memory_request=inputs.handle_memory_request,
        parse_tool_name=inputs.parse_tool_name,
        tool_arguments_payload=inputs.tool_arguments_payload,
        approval_required_for_direct_tool=inputs.approval_required_for_direct_tool,
        agent_machine_full_trust_for_session=inputs.agent_machine_full_trust_for_session,
        execute_single_direct_tool_call=inputs.execute_single_direct_tool_call,
        direct_chat_session_key=inputs.direct_chat_session_key,
        resolved_chat_iteration_limit=inputs.resolved_chat_iteration_limit,
        session_model_preference=inputs.session_model_preference,
        normalize_reasoning_effort=inputs.normalize_reasoning_effort,
        parse_slash_command=inputs.parse_slash_command,
        set_session_model_preference=inputs.set_session_model_preference,
        mark_thread_cleared=inputs.mark_thread_cleared,
        normalize_prior_messages=inputs.normalize_prior_messages,
        consume_thread_cleared=inputs.consume_thread_cleared,
        compact_conversation_history=inputs.compact_conversation_history,
        build_proactive_suggestions=inputs.build_proactive_suggestions,
        direct_tool_session_key=inputs.direct_tool_session_key,
        resolve_direct_chat_availability=inputs.resolve_direct_chat_availability,
        connected_system_labels=inputs.connected_system_labels,
        context_tool_capabilities=inputs.context_tool_capabilities,
        build_direct_chat_tools=inputs.build_direct_chat_tools,
        build_local_direct_chat_tools=inputs.build_local_direct_chat_tools,
        build_builtin_direct_chat_tools=inputs.build_builtin_direct_chat_tools,
        build_always_on_direct_chat_tools=inputs.build_always_on_direct_chat_tools,
        build_registry_entries=inputs.build_registry_entries,
        normalize_direct_approved_action=inputs.normalize_direct_approved_action,
        build_context_used=inputs.build_context_used,
        direct_chat_compaction_token_limit=inputs.direct_chat_compaction_token_limit,
        with_context_used=inputs.with_context_used,
        connected_provider_tokens=inputs.connected_provider_tokens,
        active_run_count=inputs.active_run_count,
        slash_command_help_text=inputs.slash_command_help_text,
        execute_direct_tool_calls=inputs.execute_direct_tool_calls,
        direct_chat_credentials=inputs.direct_chat_credentials,
        capture_exception=inputs.capture_exception,
        tool_write_action_available=inputs.tool_write_action_available,
        approved_action_to_tool_call=inputs.approved_action_to_tool_call,
        resolve_provider_for_direct_chat_message=inputs.resolve_provider_for_direct_chat_message,
        plan_direct_chat_route=inputs.plan_direct_chat_route,
        start_direct_chat_run_handoff=inputs.start_direct_chat_run_handoff,
        direct_chat_run_handoff_reply=inputs.direct_chat_run_handoff_reply,
        stream_direct_chat_run_handoff=inputs.stream_direct_chat_run_handoff,
        direct_chat_run_handoff_failure_payload=inputs.direct_chat_run_handoff_failure_payload,
        supports_direct_message_native_chat=inputs.supports_direct_message_native_chat,
        supported_providers=inputs.supported_providers,
        build_direct_chat_system_prompt=inputs.build_direct_chat_system_prompt,
        direct_chat_workspace_context_text=inputs.direct_chat_workspace_context_text,
        direct_chat_generation_services=build_direct_chat_generation_services(inputs),
        no_provider_reasoning_required_response=inputs.no_provider_reasoning_required_response,
    )
