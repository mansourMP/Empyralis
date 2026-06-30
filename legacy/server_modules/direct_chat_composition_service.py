from __future__ import annotations

from typing import Any, Dict

from server_modules import direct_chat_callback_facade_service
from server_modules import direct_chat_runtime_facade_service


_CALLBACK_INPUT_NAMES = (
    "thinking_step_payload",
    "build_context_used",
    "build_direct_tool_approval_response",
    "parse_tool_name",
    "tool_arguments_payload",
    "direct_tool_step_payload",
    "execute_single_direct_tool_call",
    "direct_tool_followup_message",
    "suggest_actions",
    "clear_direct_tool_loop_state",
    "persist_direct_chat_memory_best_effort",
    "persist_direct_chat_transcript_best_effort",
    "persist_direct_chat_hosted_usage_best_effort",
    "record_direct_tool_signature",
    "direct_chat_error_reply",
    "compact_text",
    "safe_positive_int",
    "resolve_chat_local_path",
    "extract_first_path_reference",
    "extract_first_url",
    "approval_required_for_direct_tool",
    "agent_machine_full_trust_for_session",
    "direct_chat_session_key",
    "resolved_chat_iteration_limit",
    "session_model_preference",
    "normalize_reasoning_effort",
    "parse_slash_command",
    "set_session_model_preference",
    "mark_thread_cleared",
    "normalize_prior_messages",
    "consume_thread_cleared",
    "build_proactive_suggestions",
    "direct_tool_session_key",
    "resolve_direct_chat_availability",
    "connected_system_labels",
    "context_tool_capabilities",
    "build_direct_chat_tools",
    "build_local_direct_chat_tools",
    "build_builtin_direct_chat_tools",
    "build_always_on_direct_chat_tools",
    "build_registry_entries",
    "normalize_direct_approved_action",
    "with_context_used",
    "connected_provider_tokens",
    "active_run_count",
    "slash_command_help_text",
    "execute_direct_tool_calls",
    "direct_chat_credentials",
    "tool_write_action_available",
    "approved_action_to_tool_call",
    "resolve_provider_for_direct_chat_message",
    "plan_direct_chat_route",
    "start_direct_chat_run_handoff",
    "direct_chat_run_handoff_reply",
    "stream_direct_chat_run_handoff",
    "direct_chat_run_handoff_failure_payload",
    "supports_direct_message_native_chat",
    "build_direct_chat_system_prompt",
    "direct_chat_workspace_context_text",
)


def _lookup(namespace: Dict[str, Any], name: str) -> Any:
    value = namespace.get(name)
    if value is None:
        raise KeyError(f"Missing callback dependency '{name}'")
    return value


def build_direct_chat_callback_facade_inputs(
    *,
    namespace: Dict[str, Any],
    parse_page_state: Any,
    capture_exception: Any,
    generate_chat_reply_stream_with_provider_fallback: Any,
    compact_conversation_history: Any,
    parse_memory_write: Any,
    parse_memory_read: Any,
    handle_memory_request: Any,
    list_memory_entries: Any,
    get_memory: Any,
    delete_memory: Any,
    no_provider_reasoning_required_response: Any,
    supported_providers: list[str],
    direct_chat_compaction_token_limit: int,
) -> direct_chat_callback_facade_service.DirectChatCallbackFacadeInputs:
    base = {name: _lookup(namespace, name) for name in _CALLBACK_INPUT_NAMES}
    return direct_chat_callback_facade_service.DirectChatCallbackFacadeInputs(
        thinking_step_payload=base["thinking_step_payload"],
        build_context_used=base["build_context_used"],
        build_direct_tool_approval_response=base["build_direct_tool_approval_response"],
        parse_tool_name=base["parse_tool_name"],
        tool_arguments_payload=base["tool_arguments_payload"],
        parse_page_state=parse_page_state,
        direct_tool_step_payload=base["direct_tool_step_payload"],
        execute_single_direct_tool_call=base["execute_single_direct_tool_call"],
        direct_tool_followup_message=base["direct_tool_followup_message"],
        suggest_actions=base["suggest_actions"],
        clear_direct_tool_loop_state=base["clear_direct_tool_loop_state"],
        persist_direct_chat_memory_best_effort=base["persist_direct_chat_memory_best_effort"],
        persist_direct_chat_transcript_best_effort=base["persist_direct_chat_transcript_best_effort"],
        persist_direct_chat_hosted_usage_best_effort=base["persist_direct_chat_hosted_usage_best_effort"],
        record_direct_tool_signature=base["record_direct_tool_signature"],
        direct_chat_error_reply=base["direct_chat_error_reply"],
        capture_exception=capture_exception,
        generate_chat_reply_stream_with_provider_fallback=generate_chat_reply_stream_with_provider_fallback,
        compact_text=base["compact_text"],
        safe_positive_int=base["safe_positive_int"],
        resolve_chat_local_path=base["resolve_chat_local_path"],
        extract_first_path_reference=base["extract_first_path_reference"],
        extract_first_url=base["extract_first_url"],
        parse_memory_write=parse_memory_write,
        parse_memory_read=parse_memory_read,
        handle_memory_request=handle_memory_request,
        approval_required_for_direct_tool=base["approval_required_for_direct_tool"],
        agent_machine_full_trust_for_session=base["agent_machine_full_trust_for_session"],
        direct_chat_session_key=base["direct_chat_session_key"],
        resolved_chat_iteration_limit=base["resolved_chat_iteration_limit"],
        session_model_preference=base["session_model_preference"],
        normalize_reasoning_effort=base["normalize_reasoning_effort"],
        parse_slash_command=base["parse_slash_command"],
        set_session_model_preference=base["set_session_model_preference"],
        mark_thread_cleared=base["mark_thread_cleared"],
        normalize_prior_messages=base["normalize_prior_messages"],
        consume_thread_cleared=base["consume_thread_cleared"],
        compact_conversation_history=compact_conversation_history,
        build_proactive_suggestions=base["build_proactive_suggestions"],
        direct_tool_session_key=base["direct_tool_session_key"],
        resolve_direct_chat_availability=base["resolve_direct_chat_availability"],
        connected_system_labels=base["connected_system_labels"],
        context_tool_capabilities=base["context_tool_capabilities"],
        build_direct_chat_tools=base["build_direct_chat_tools"],
        build_local_direct_chat_tools=base["build_local_direct_chat_tools"],
        build_builtin_direct_chat_tools=base["build_builtin_direct_chat_tools"],
        build_always_on_direct_chat_tools=base["build_always_on_direct_chat_tools"],
        build_registry_entries=base["build_registry_entries"],
        normalize_direct_approved_action=base["normalize_direct_approved_action"],
        direct_chat_compaction_token_limit=direct_chat_compaction_token_limit,
        with_context_used=base["with_context_used"],
        connected_provider_tokens=base["connected_provider_tokens"],
        list_memory_entries=list_memory_entries,
        active_run_count=base["active_run_count"],
        get_memory=get_memory,
        delete_memory=delete_memory,
        slash_command_help_text=base["slash_command_help_text"],
        execute_direct_tool_calls=base["execute_direct_tool_calls"],
        direct_chat_credentials=base["direct_chat_credentials"],
        tool_write_action_available=base["tool_write_action_available"],
        approved_action_to_tool_call=base["approved_action_to_tool_call"],
        resolve_provider_for_direct_chat_message=base["resolve_provider_for_direct_chat_message"],
        plan_direct_chat_route=base["plan_direct_chat_route"],
        start_direct_chat_run_handoff=base["start_direct_chat_run_handoff"],
        direct_chat_run_handoff_reply=base["direct_chat_run_handoff_reply"],
        stream_direct_chat_run_handoff=base["stream_direct_chat_run_handoff"],
        direct_chat_run_handoff_failure_payload=base["direct_chat_run_handoff_failure_payload"],
        supports_direct_message_native_chat=base["supports_direct_message_native_chat"],
        supported_providers=list(supported_providers),
        build_direct_chat_system_prompt=base["build_direct_chat_system_prompt"],
        direct_chat_workspace_context_text=base["direct_chat_workspace_context_text"],
        no_provider_reasoning_required_response=no_provider_reasoning_required_response,
    )


def build_direct_chat_generation_services(
    inputs: direct_chat_callback_facade_service.DirectChatCallbackFacadeInputs,
):
    return direct_chat_callback_facade_service.build_direct_chat_generation_services(inputs)


def build_direct_chat_runtime_facade_callbacks(
    inputs: direct_chat_callback_facade_service.DirectChatCallbackFacadeInputs,
):
    return direct_chat_callback_facade_service.build_direct_chat_runtime_facade_callbacks(inputs)


def prepare_direct_chat_request(*, callbacks, **kwargs):
    return direct_chat_runtime_facade_service.prepare_direct_chat_request(
        callbacks=callbacks,
        **kwargs,
    )


def build_direct_chat_response_services(*, callbacks):
    return direct_chat_runtime_facade_service.build_direct_chat_response_services(callbacks)


def build_direct_chat_runtime_services(*, callbacks):
    return direct_chat_runtime_facade_service.build_direct_chat_runtime_services(callbacks)
