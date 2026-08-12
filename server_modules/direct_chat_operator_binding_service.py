from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Optional

from server_modules import direct_chat_availability_service
from server_modules import direct_chat_composition_service
from server_modules import direct_chat_context_service
from server_modules import direct_chat_entry_policy_service
from server_modules import direct_chat_handoff_facade_service
from server_modules import direct_chat_memory_facade_service
from server_modules import direct_chat_metadata_service
from server_modules import direct_chat_operator_support_service
from server_modules import direct_chat_prompt_service
from server_modules import direct_chat_provider_facade_service
from server_modules import direct_chat_routing_service
from server_modules import direct_chat_runtime_entry_facade_service
from server_modules import direct_chat_support_binding_service
from server_modules import direct_chat_tool_catalog_service

from server_modules import direct_tool_config_service
from server_modules import direct_tool_execution_service
from server_modules import skills_service
from server_modules import direct_tool_loop_guard_service
from server_modules import direct_tool_runtime_facade_service


@dataclass(slots=True)
class DirectChatOperatorRuntimeBindings:
    callback_facade_inputs: Any
    generation_services: Any
    runtime_facade_callbacks: Any
    prepare_request: Any
    response_services: Any
    runtime_services: Any


@dataclass(slots=True)
class DirectChatOperatorPolicyBindings:
    routing_policy_callbacks: Any
    tool_policy_callbacks: Any
    direct_tool_execution_callbacks: Any


@dataclass(slots=True)
class DirectChatOperatorToolRuntimeBindings:
    direct_tool_step_payload: Any
    no_provider_execution_services: Any
    build_direct_tool_approval_response: Any
    message_has_obvious_direct_tool_intent: Any
    execute_single_direct_tool_call: Any
    execute_direct_tool_calls: Any


@dataclass(slots=True)
class DirectChatOperatorEntryBindings:
    build_direct_chat_system_prompt: Any
    direct_chat_runtime_available: Any
    resolve_direct_chat_availability: Any
    connected_provider_tokens: Any
    resolve_provider_for_direct_chat_message: Any
    plan_direct_chat_route: Any
    credential_auth_mode: Any
    supports_direct_message_native_chat: Any
    preferred_provider: Any
    provider_unavailable_response: Any
    direct_chat_credentials: Any
    direct_chat_error_reply: Any


@dataclass(slots=True)
class DirectChatOperatorHandoffBindings:
    durable_run_preferred_response: Any
    run_handoff_execution_target: Any
    can_auto_start_run_handoff: Any
    direct_chat_run_handoff_failure_payload: Any
    start_direct_chat_run_handoff: Any
    direct_chat_run_handoff_reply: Any
    direct_chat_run_actions: Any
    direct_chat_run_snapshot: Any
    direct_chat_run_event_to_step: Any
    direct_chat_run_snapshot_to_step: Any
    direct_chat_run_final_payload: Any
    stream_direct_chat_run_handoff: Any


@dataclass(slots=True)
class DirectChatOperatorAvailabilityBindings:
    question_like: Any
    mentions_any: Any
    starts_like_direct_run: Any
    is_obvious_telegram_write_request: Any
    is_obvious_google_write_request: Any
    is_obvious_smtp_write_request: Any
    connector_write_preview_allowed: Any
    is_explicit_workflow_request: Any
    no_ai_chat_response: Any
    suggest_actions: Any
    heartbeat_pending_tasks_for_suggestions: Any
    recent_run_prompts_for_suggestions: Any
    build_proactive_suggestions: Any


@dataclass(slots=True)
class DirectChatOperatorToolRoutingBindings:
    message_requests_http_request_tool: Any
    message_requests_image_generation_tool: Any
    message_requests_browser_tool: Any
    message_can_use_direct_connector_tools: Any
    looks_like_local_path_request: Any
    message_requests_local_file_tool: Any
    message_requests_local_shell_tool: Any
    message_requests_local_screenshot_tool: Any
    message_requests_local_computer_tool: Any
    message_can_use_direct_local_tools: Any
    message_can_use_builtin_direct_tools: Any
    preview_run_response: Any
    prefer_durable_run_handoff: Any


@dataclass(slots=True)
class DirectChatOperatorStateBindings:
    agent_machine_owner_user_id: Any
    agent_machine_full_trust_for_session: Any
    availability_lines: Any
    connected_system_labels: Any
    context_tool_capabilities: Any
    normalize_prior_messages: Any
    direct_tool_session_key: Any
    direct_chat_session_key: Any
    parse_slash_command: Any
    session_model_preference: Any
    set_session_model_preference: Any
    mark_thread_cleared: Any
    consume_thread_cleared: Any
    active_run_count: Any
    slash_command_help_text: Any
    tool_call_signature: Any
    record_direct_tool_signature: Any
    clear_direct_tool_loop_state: Any
    direct_chat_memory_context_message: Any
    direct_chat_workspace_context_text: Any
    build_direct_chat_daily_log_summary: Any
    persist_direct_chat_memory_best_effort: Any
    persist_direct_chat_transcript_best_effort: Any
    persist_direct_chat_hosted_usage_best_effort: Any
    build_context_used: Any
    with_context_used: Any


@dataclass(slots=True)
class DirectChatOperatorBindingBundle:
    runtime_bindings: DirectChatOperatorRuntimeBindings
    policy_bindings: DirectChatOperatorPolicyBindings
    entry_bindings: DirectChatOperatorEntryBindings
    tool_runtime_bindings: DirectChatOperatorToolRuntimeBindings


@dataclass(slots=True)
class DirectChatOperatorEntrypointBindings:
    build_direct_operator_reply: Any
    collect_direct_operator_reply: Any
    build_chat_turn_event_stream: Any
    execute_chat_turn: Any


@dataclass(slots=True)
class DirectChatOperatorToolSupportBindings:
    parse_tool_name: Any
    tool_arguments_payload: Any
    extract_first_email: Any
    extract_subject_text: Any
    extract_body_text: Any
    first_non_empty_line: Any
    build_direct_tool_config: Any
    build_direct_local_tool_config: Any
    tool_write_action_available: Any
    normalize_direct_approved_action: Any
    approved_action_to_tool_call: Any
    run_async_tool_call: Any
    format_direct_tool_result: Any
    format_direct_local_tool_result: Any
    titleize_direct_step_token: Any
    compact_step_detail: Any
    thinking_step_payload: Any
    extract_first_url: Any
    extract_first_path_reference: Any
    resolve_chat_local_path: Any
    direct_tool_followup_message: Any
    provider_display_name: Any
    normalize_reasoning_effort: Any


@dataclass(slots=True)
class DirectChatOperatorShellBindings:
    safe_positive_int: Any
    resolved_chat_iteration_limit: Any
    chat_iteration_limit_reply: Any
    compact_text: Any
    normalize_tool_capabilities: Any
    tool_capability: Any
    tool_connected: Any
    tool_runtime_usable: Any
    local_worker_available: Any
    connect_action: Any
    open_action: Any
    google_repair_action: Any
    run_action: Any
    workflow_action: Any
    action_marker_count: Any
    path_like_reference_count: Any
    provider_supports_direct_tool_calls: Any
    build_local_direct_chat_tools: Any
    build_direct_chat_tools: Any
    build_builtin_direct_chat_tools: Any
    build_always_on_direct_chat_tools: Any
    build_registry_entries: Any
    registered_direct_chat_tool_names_for_logging: Any
    state_bindings: DirectChatOperatorStateBindings
    availability_bindings: DirectChatOperatorAvailabilityBindings
    handoff_bindings: DirectChatOperatorHandoffBindings
    tool_support_bindings: DirectChatOperatorToolSupportBindings
    tool_routing_bindings: Any = None


def _lookup(namespace: Dict[str, Any], name: str) -> Any:
    if name in namespace:
        value = namespace[name]
    else:
        value = namespace.get(f"_{name}")
    if value is None:
        raise KeyError(f"Missing operator binding '{name}'")
    return value


def parse_tool_name(tool_name: str) -> tuple[str, str]:
    token = str(tool_name or "").strip()
    if token == "http_request":
        return "http", "request"
    if token == "generate_image":
        return "image", "generate"
    if token == "send_image":
        return "messaging", "send_image"
    if token == "memory_search":
        return "memory", "search"
    if token == "memory_read":
        return "memory", "read"
    if token == "memory_write":
        return "memory", "write"
    if token == "memory_write_private":
        return "memory", "write_private"
    if token == "memory_get_private":
        return "memory", "get_private"
    if token == "memory_get":
        return "memory", "get"
    if token == "memory_update":
        return "memory", "update"
    if token == "memory_stage_edit":
        return "memory", "stage_edit"
    if token == "memory_apply_edit":
        return "memory", "apply_edit"
    if token == "memory_append_daily_note":
        return "memory", "append_daily_note"
    if token == "memory_stage_consolidation":
        return "memory", "stage_consolidation"
    if token == "memory_consolidate_daily_notes":
        return "memory", "consolidate_daily_notes"
    if token == "memory_list_versions":
        return "memory", "list_versions"
    if token == "memory_rollback_version":
        return "memory", "rollback_version"
    if token == "skill_invoke":
        return "skill", "invoke"
    if token == "skill_write":
        return "skill", "write"
    if "__" not in token:
        raise RuntimeError(f"Unsupported direct chat tool '{token}'.")
    connector_id, action_id = token.split("__", 1)
    connector_id = connector_id.strip().lower()
    action_id = action_id.strip()
    if not connector_id or not action_id:
        raise RuntimeError(f"Unsupported direct chat tool '{token}'.")
    return connector_id, action_id


def tool_arguments_payload(arguments: Any, *, parse_json_object_loose_fn: Any) -> Dict[str, Any]:
    if isinstance(arguments, dict):
        return dict(arguments)
    parsed = parse_json_object_loose_fn(str(arguments or ""))
    return dict(parsed) if isinstance(parsed, dict) else {}


def normalize_direct_approved_action(value: Any) -> Optional[Dict[str, str]]:
    if not isinstance(value, dict):
        return None
    connector_id = str(value.get("connector") or "").strip().lower()
    action_id = str(value.get("action") or "").strip()
    tool_input = str(value.get("input") or "").strip()
    if not connector_id or not action_id or not tool_input:
        return None
    return {
        "connector": connector_id,
        "action": action_id,
        "input": tool_input,
    }


def titleize_direct_step_token(value: str) -> str:
    words = [part for part in str(value or "").strip().replace("-", "_").split("_") if part]
    return " ".join(word.capitalize() for word in words)


def compact_step_detail(value: Any, limit: int = 120) -> Optional[str]:
    normalized = " ".join(str(value or "").split()).strip()
    if not normalized:
        return None
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 1].rstrip()}…"


def build_direct_tool_execution_callbacks(
    *,
    namespace: Dict[str, Any],
    parse_json_object_loose: Any,
    llm_task: Any,
    web_search: Any,
    web_fetch: Any,
    search_memory_notebook: Any,
    get_memory_notebook_excerpt: Any,
):
    return direct_tool_runtime_facade_service.build_direct_tool_execution_callbacks(
        namespace={
            "compact_step_detail": _lookup(namespace, "compact_step_detail"),
            "titleize_direct_step_token": _lookup(namespace, "titleize_direct_step_token"),
            "run_async_tool_call": _lookup(namespace, "run_async_tool_call"),
            "parse_tool_name": _lookup(namespace, "parse_tool_name"),
            "tool_arguments_payload": _lookup(namespace, "tool_arguments_payload"),
            "safe_positive_int": _lookup(namespace, "safe_positive_int"),
            "normalize_reasoning_effort": _lookup(namespace, "normalize_reasoning_effort"),
            "build_direct_local_tool_config": _lookup(namespace, "build_direct_local_tool_config"),
            "format_direct_local_tool_result": _lookup(namespace, "format_direct_local_tool_result"),
            "build_direct_tool_config": _lookup(namespace, "build_direct_tool_config"),
            "format_direct_tool_result": _lookup(namespace, "format_direct_tool_result"),
        },
        parse_json_object_loose=parse_json_object_loose,
        llm_task=llm_task,
        web_search=web_search,
        web_fetch=web_fetch,
        search_memory_notebook=search_memory_notebook,
        get_memory_notebook_excerpt=get_memory_notebook_excerpt,
    )


def build_direct_chat_routing_policy_callbacks(
    *,
    namespace: Dict[str, Any],
    complex_task_sequence_markers: tuple[str, ...] | list[str],
    complex_task_outcome_markers: tuple[str, ...] | list[str],
    execution_markers: tuple[str, ...] | list[str],
    google_workspace_keywords: tuple[str, ...] | list[str],
    telegram_keywords: tuple[str, ...] | list[str],
    slack_keywords: tuple[str, ...] | list[str],
    dropbox_keywords: tuple[str, ...] | list[str],
    s3_keywords: tuple[str, ...] | list[str],
) -> direct_chat_routing_service.DirectChatRoutingPolicyCallbacks:
    return direct_chat_routing_service.DirectChatRoutingPolicyCallbacks(
        compact_text=_lookup(namespace, "compact_text"),
        mentions_any=_lookup(namespace, "mentions_any"),
        question_like=_lookup(namespace, "question_like"),
        is_explicit_workflow_request=_lookup(namespace, "is_explicit_workflow_request"),
        starts_like_direct_run=_lookup(namespace, "starts_like_direct_run"),
        workflow_action=_lookup(namespace, "workflow_action"),
        run_action=_lookup(namespace, "run_action"),
        message_requests_local_file_tool=_lookup(namespace, "message_requests_local_file_tool"),
        message_requests_local_shell_tool=_lookup(namespace, "message_requests_local_shell_tool"),
        message_requests_local_screenshot_tool=_lookup(namespace, "message_requests_local_screenshot_tool"),
        message_requests_local_computer_tool=_lookup(namespace, "message_requests_local_computer_tool"),
        complex_task_sequence_markers=complex_task_sequence_markers,
        complex_task_outcome_markers=complex_task_outcome_markers,
        execution_markers=execution_markers,
        google_workspace_keywords=google_workspace_keywords,
        telegram_keywords=telegram_keywords,
        slack_keywords=slack_keywords,
        dropbox_keywords=dropbox_keywords,
        s3_keywords=s3_keywords,
    )


def build_direct_chat_tool_policy_callbacks(
    *,
    namespace: Dict[str, Any],
    google_workspace_keywords: tuple[str, ...] | list[str],
    smtp_keywords: tuple[str, ...] | list[str],
    telegram_keywords: tuple[str, ...] | list[str],
    slack_keywords: tuple[str, ...] | list[str],
    discord_keywords: tuple[str, ...] | list[str],
    dropbox_keywords: tuple[str, ...] | list[str],
    s3_keywords: tuple[str, ...] | list[str],
    browser_keywords: tuple[str, ...] | list[str],
    local_file_keywords: tuple[str, ...] | list[str],
    local_shell_keywords: tuple[str, ...] | list[str],
    local_screenshot_keywords: tuple[str, ...] | list[str],
    local_computer_control_keywords: tuple[str, ...] | list[str],
    web_lookup_keywords: tuple[str, ...] | list[str],
    http_request_keywords: tuple[str, ...] | list[str],
    image_generation_keywords: tuple[str, ...] | list[str],
    llm_task_keywords: tuple[str, ...] | list[str],
) -> direct_chat_tool_catalog_service.DirectChatToolPolicyCallbacks:
    return direct_chat_tool_catalog_service.DirectChatToolPolicyCallbacks(
        compact_text=_lookup(namespace, "compact_text"),
        question_like=_lookup(namespace, "question_like"),
        mentions_any=_lookup(namespace, "mentions_any"),
        extract_first_path_reference=_lookup(namespace, "extract_first_path_reference"),
        extract_first_url=_lookup(namespace, "extract_first_url"),
        provider_supports_direct_tool_calls=_lookup(namespace, "provider_supports_direct_tool_calls"),
        is_obvious_smtp_write_request=_lookup(namespace, "is_obvious_smtp_write_request"),
        google_workspace_keywords=google_workspace_keywords,
        smtp_keywords=smtp_keywords,
        telegram_keywords=telegram_keywords,
        slack_keywords=slack_keywords,
        discord_keywords=discord_keywords,
        dropbox_keywords=dropbox_keywords,
        s3_keywords=s3_keywords,
        browser_keywords=browser_keywords,
        local_file_keywords=local_file_keywords,
        local_shell_keywords=local_shell_keywords,
        local_screenshot_keywords=local_screenshot_keywords,
        local_computer_control_keywords=local_computer_control_keywords,
        web_lookup_keywords=web_lookup_keywords,
        http_request_keywords=http_request_keywords,
        image_generation_keywords=image_generation_keywords,
        llm_task_keywords=llm_task_keywords,
    )


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
):
    # Approval-era vestige: the approval system was removed in Stage 1.
    # This callback always returns False — the agent acts on its own reasoning.
    namespace.setdefault("approval_required_for_direct_tool", lambda *a, **kw: False)
    callback_namespace = {
        name: _lookup(namespace, name)
        for name in direct_chat_composition_service._CALLBACK_INPUT_NAMES
    }
    return direct_chat_composition_service.build_direct_chat_callback_facade_inputs(
        namespace=callback_namespace,
        parse_page_state=parse_page_state,
        capture_exception=capture_exception,
        generate_chat_reply_stream_with_provider_fallback=generate_chat_reply_stream_with_provider_fallback,
        compact_conversation_history=compact_conversation_history,
        parse_memory_write=parse_memory_write,
        parse_memory_read=parse_memory_read,
        handle_memory_request=handle_memory_request,
        list_memory_entries=list_memory_entries,
        get_memory=get_memory,
        delete_memory=delete_memory,
        no_provider_reasoning_required_response=no_provider_reasoning_required_response,
        supported_providers=supported_providers,
        direct_chat_compaction_token_limit=direct_chat_compaction_token_limit,
    )


def build_direct_chat_runtime_bindings(
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
) -> DirectChatOperatorRuntimeBindings:
    def callback_facade_inputs():
        return build_direct_chat_callback_facade_inputs(
            namespace=namespace,
            parse_page_state=parse_page_state,
            capture_exception=capture_exception,
            generate_chat_reply_stream_with_provider_fallback=generate_chat_reply_stream_with_provider_fallback,
            compact_conversation_history=compact_conversation_history,
            parse_memory_write=parse_memory_write,
            parse_memory_read=parse_memory_read,
            handle_memory_request=handle_memory_request,
            list_memory_entries=list_memory_entries,
            get_memory=get_memory,
            delete_memory=delete_memory,
            no_provider_reasoning_required_response=no_provider_reasoning_required_response,
            supported_providers=supported_providers,
            direct_chat_compaction_token_limit=direct_chat_compaction_token_limit,
        )

    def generation_services():
        return direct_chat_composition_service.build_direct_chat_generation_services(
            callback_facade_inputs()
        )

    def runtime_facade_callbacks():
        return direct_chat_composition_service.build_direct_chat_runtime_facade_callbacks(
            callback_facade_inputs()
        )

    def prepare_request(**kwargs):
        return direct_chat_composition_service.prepare_direct_chat_request(
            callbacks=runtime_facade_callbacks(),
            **kwargs,
        )

    def response_services():
        return direct_chat_composition_service.build_direct_chat_response_services(
            callbacks=runtime_facade_callbacks(),
        )

    def runtime_services():
        return direct_chat_composition_service.build_direct_chat_runtime_services(
            callbacks=runtime_facade_callbacks(),
        )

    return DirectChatOperatorRuntimeBindings(
        callback_facade_inputs=callback_facade_inputs,
        generation_services=generation_services,
        runtime_facade_callbacks=runtime_facade_callbacks,
        prepare_request=prepare_request,
        response_services=response_services,
        runtime_services=runtime_services,
    )


def build_direct_chat_policy_bindings(
    *,
    namespace: Dict[str, Any],
    complex_task_sequence_markers: tuple[str, ...] | list[str],
    complex_task_outcome_markers: tuple[str, ...] | list[str],
    execution_markers: tuple[str, ...] | list[str],
    google_workspace_keywords: tuple[str, ...] | list[str],
    smtp_keywords: tuple[str, ...] | list[str],
    telegram_keywords: tuple[str, ...] | list[str],
    slack_keywords: tuple[str, ...] | list[str],
    discord_keywords: tuple[str, ...] | list[str],
    dropbox_keywords: tuple[str, ...] | list[str],
    s3_keywords: tuple[str, ...] | list[str],
    browser_keywords: tuple[str, ...] | list[str],
    local_file_keywords: tuple[str, ...] | list[str],
    local_shell_keywords: tuple[str, ...] | list[str],
    local_screenshot_keywords: tuple[str, ...] | list[str],
    local_computer_control_keywords: tuple[str, ...] | list[str],
    web_lookup_keywords: tuple[str, ...] | list[str],
    http_request_keywords: tuple[str, ...] | list[str],
    image_generation_keywords: tuple[str, ...] | list[str],
    llm_task_keywords: tuple[str, ...] | list[str],
    parse_json_object_loose: Any,
    llm_task: Any,
    web_search: Any,
    web_fetch: Any,
    search_memory_notebook: Any,
    get_memory_notebook_excerpt: Any,
) -> DirectChatOperatorPolicyBindings:
    def routing_policy_callbacks():
        return build_direct_chat_routing_policy_callbacks(
            namespace=namespace,
            complex_task_sequence_markers=complex_task_sequence_markers,
            complex_task_outcome_markers=complex_task_outcome_markers,
            execution_markers=execution_markers,
            google_workspace_keywords=google_workspace_keywords,
            telegram_keywords=telegram_keywords,
            slack_keywords=slack_keywords,
            dropbox_keywords=dropbox_keywords,
            s3_keywords=s3_keywords,
        )

    def tool_policy_callbacks():
        return build_direct_chat_tool_policy_callbacks(
            namespace=namespace,
            google_workspace_keywords=google_workspace_keywords,
            smtp_keywords=smtp_keywords,
            telegram_keywords=telegram_keywords,
            slack_keywords=slack_keywords,
            discord_keywords=discord_keywords,
            dropbox_keywords=dropbox_keywords,
            s3_keywords=s3_keywords,
            browser_keywords=browser_keywords,
            local_file_keywords=local_file_keywords,
            local_shell_keywords=local_shell_keywords,
            local_screenshot_keywords=local_screenshot_keywords,
            local_computer_control_keywords=local_computer_control_keywords,
            web_lookup_keywords=web_lookup_keywords,
            http_request_keywords=http_request_keywords,
            image_generation_keywords=image_generation_keywords,
            llm_task_keywords=llm_task_keywords,
        )

    def direct_tool_execution_callbacks():
        return build_direct_tool_execution_callbacks(
            namespace=namespace,
            parse_json_object_loose=parse_json_object_loose,
            llm_task=llm_task,
            web_search=web_search,
            web_fetch=web_fetch,
            search_memory_notebook=search_memory_notebook,
            get_memory_notebook_excerpt=get_memory_notebook_excerpt,
        )

    return DirectChatOperatorPolicyBindings(
        routing_policy_callbacks=routing_policy_callbacks,
        tool_policy_callbacks=tool_policy_callbacks,
        direct_tool_execution_callbacks=direct_tool_execution_callbacks,
    )


def build_direct_chat_tool_runtime_bindings(
    *,
    direct_chat_runtime_facade_callbacks: Any,
    direct_tool_execution_callbacks: Any,
    execute_single_direct_tool_call_fn: Any,
) -> DirectChatOperatorToolRuntimeBindings:
    def direct_tool_step_payload(
        connector_id,
        action_id,
        arguments,
        *,
        step_id,
        status,
        detail_override=None,
    ):
        return direct_tool_execution_service.direct_tool_step_payload(
            connector_id,
            action_id,
            arguments,
            step_id=step_id,
            status=status,
            detail_override=detail_override,
            callbacks=direct_tool_execution_callbacks(),
        )

    def no_provider_execution_services():
        return direct_tool_runtime_facade_service.build_no_provider_execution_services(
            callbacks=direct_chat_runtime_facade_callbacks(),
        )

    def build_direct_tool_approval_response(*, tool_calls, tool_capabilities, session_ctx=None):
        return direct_tool_runtime_facade_service.build_direct_tool_approval_response(
            tool_calls=tool_calls,
            tool_capabilities=tool_capabilities,
            session_ctx=session_ctx,
            callbacks=direct_chat_runtime_facade_callbacks(),
        )

    def message_has_obvious_direct_tool_intent(message, tools):
        return direct_tool_runtime_facade_service.message_has_obvious_direct_tool_intent(
            message,
            tools,
            callbacks=direct_chat_runtime_facade_callbacks(),
        )

    def execute_single_direct_tool_call(
        *,
        tool_call,
        workspace_id,
        thread_id,
        index=1,
        provider=None,
        model=None,
        credentials=None,
        reasoning_effort="",
        session_ctx=None,
    ):
        callbacks = direct_tool_execution_callbacks()
        connector_id, action_id = callbacks.parse_tool_name(str(tool_call.get("name") or ""))
        # MCP-namespaced tool calls (mcp__<server_id>__<tool_name>) — Phase A
        # wiring (docs/design/mcp-applications-plan.md). This is THE live
        # dispatch point for structured tool calls in the primary generation
        # loop: this closure is bound into DirectChatGenerationServices.
        # execute_single_direct_tool_call and invoked from
        # direct_chat_generation_service.stream_provider_backed_direct_chat()
        # (via a plain ThreadPoolExecutor — no asyncio event loop running in
        # this thread, hence the SYNC invoke_workspace_mcp_tool(), not the
        # _async twin used by skills_service.execute_single_direct_tool_call_
        # async — that function is currently unreachable from this loop; see
        # docs/design/mcp-current-state.md). Intercepted here, before the
        # "not in {...}" custom-connector fallback below, because "mcp" is
        # not in that whitelist and would otherwise silently mis-route to
        # runs_execution._workflow_execute_connector_action, which has no
        # notion of MCP servers/endpoints. The MCP-specific approval gate
        # (_assert_tool_approved_for_execution) is enforced inside
        # invoke_workspace_mcp_tool() itself, never bypassed here.
        if connector_id == "mcp":
            from server_modules import mcp_registry_service

            if not mcp_registry_service.mcp_tools_enabled():
                raise RuntimeError("MCP tools are disabled for this deployment.")
            parsed_mcp = mcp_registry_service.parse_mcp_tool_name(str(tool_call.get("name") or ""))
            if parsed_mcp is None:
                raise RuntimeError(f"Malformed MCP tool name '{tool_call.get('name')}'.")
            mcp_arguments = callbacks.tool_arguments_payload(tool_call.get("arguments"))
            session_payload = session_ctx if isinstance(session_ctx, dict) else {}
            metadata = skills_service._direct_tool_session_metadata(session_ctx)
            mcp_result = mcp_registry_service.invoke_workspace_mcp_tool(
                workspace_id=workspace_id,
                server_id=parsed_mcp["server_id"],
                tool_name=parsed_mcp["tool_name"],
                arguments=mcp_arguments if isinstance(mcp_arguments, dict) else {},
                agent_label=str(metadata.get("sage_agent_id") or metadata.get("agent_scope") or "Agent"),
                tenant_id=skills_service._tenant_id_from_direct_tool_context(session_ctx),
                thread_id=str(thread_id or "").strip() or None,
                run_id=skills_service._request_id_from_direct_tool_context(session_ctx) or None,
                user_id=str(session_payload.get("sender_id") or "").strip() or None,
                agent_id=str(session_payload.get("active_agent_install_id") or metadata.get("sage_agent_id") or "").strip() or None,
            )
            return mcp_registry_service.format_mcp_tool_result(mcp_result)
        if connector_id not in {"", "http", "llm", "file", "shell", "screenshot", "computer", "hardware", "memory", "web", "browser", "image", "sage_service", "fleet", "skill", "subagent"}:
            from server_modules import runs_execution

            argument_payload = callbacks.tool_arguments_payload(tool_call.get("arguments"))
            if isinstance(argument_payload, dict):
                tool_input = str(argument_payload.get("input") or "").strip()
                if not tool_input:
                    try:
                        tool_input = json.dumps(argument_payload, ensure_ascii=False)
                    except Exception:
                        tool_input = str(argument_payload)
            else:
                tool_input = str(argument_payload or "").strip()
            config = callbacks.build_direct_tool_config(
                connector_id,
                action_id,
                tool_input,
            )
            result = runs_execution._workflow_execute_connector_action(
                "direct-chat-tool-call",
                "direct_chat_tool_call",
                {
                    "workspace_id": workspace_id,
                    "tenant_id": str(
                        (session_ctx or {}).get("tenant_id")
                        or (
                            (session_ctx or {}).get("agent_turn_request", {}).get("tenant_id")
                            if isinstance((session_ctx or {}).get("agent_turn_request"), dict)
                            else ""
                        )
                        or "default"
                    ).strip()
                    or "default",
                    "provider": provider,
                    "model": model,
                    "credentials": credentials if isinstance(credentials, dict) else None,
                    "metadata": {},
                },
                config,
                current_text=str(config.get("text") or tool_input or "").strip(),
            )
            return callbacks.format_direct_tool_result(result)
        return skills_service.execute_single_direct_tool_call(
            tool_call=tool_call,
            workspace_id=workspace_id,
            thread_id=thread_id,
            index=index,
            provider=provider,
            model=model,
            credentials=credentials,
            reasoning_effort=reasoning_effort,
            session_ctx=session_ctx,
            callbacks=callbacks,
        )

    def execute_direct_tool_calls(
        *,
        tool_calls,
        workspace_id,
        thread_id,
        provider=None,
        model=None,
        credentials=None,
        reasoning_effort="",
        session_ctx=None,
    ):
        return direct_tool_execution_service.execute_direct_tool_calls(
            tool_calls=tool_calls,
            workspace_id=workspace_id,
            thread_id=thread_id,
            provider=provider,
            model=model,
            credentials=credentials,
            reasoning_effort=reasoning_effort,
            session_ctx=session_ctx,
            execute_single_tool_call=execute_single_direct_tool_call_fn,
        )

    return DirectChatOperatorToolRuntimeBindings(
        direct_tool_step_payload=direct_tool_step_payload,
        no_provider_execution_services=no_provider_execution_services,
        build_direct_tool_approval_response=build_direct_tool_approval_response,
        message_has_obvious_direct_tool_intent=message_has_obvious_direct_tool_intent,
        execute_single_direct_tool_call=execute_single_direct_tool_call,
        execute_direct_tool_calls=execute_direct_tool_calls,
    )


def build_direct_chat_entry_bindings(
    *,
    availability_lines: Any,
    build_operator_system_prompt: Any,
    memory_tool_names: Any,
    local_worker_registry: Dict[str, Any],
    is_worker_online_fn: Any,
    preferred_provider_fn: Any,
    supports_direct_message_native_chat_fn: Any,
    resolve_workspace_tool_capabilities_fn: Any,
    supported_providers: list[str] | tuple[str, ...],
    direct_chat_credentials_fn: Any,
    build_provider_credential_candidates_fn: Any,
    compact_text_fn: Any,
    mentions_any_fn: Any,
    message_requests_local_file_tool_fn: Any,
    message_requests_local_shell_tool_fn: Any,
    message_requests_local_screenshot_tool_fn: Any,
    message_requests_local_computer_tool_fn: Any,
    is_obvious_smtp_write_request_fn: Any,
    preview_run_response_fn: Any,
    prefer_durable_run_handoff_fn: Any,
    durable_run_preferred_response_fn: Any,
    message_can_use_direct_connector_tools_fn: Any,
    message_can_use_direct_local_tools_fn: Any,
    message_can_use_builtin_direct_tools_fn: Any,
    can_auto_start_run_handoff_fn: Any,
    credential_auth_mode_fn: Any,
    normalize_auth_mode_fn: Any,
    get_claude_code_session_token_fn: Any,
    provider_has_key_fn: Any,
    connect_action_fn: Any,
    chat_iteration_limit_reply_fn: Any,
    safe_positive_int_fn: Any,
    chat_max_iterations_default: int,
    google_workspace_keywords: tuple[str, ...] | list[str],
    telegram_keywords: tuple[str, ...] | list[str],
    slack_keywords: tuple[str, ...] | list[str],
    discord_keywords: tuple[str, ...] | list[str],
    dropbox_keywords: tuple[str, ...] | list[str],
    s3_keywords: tuple[str, ...] | list[str],
) -> DirectChatOperatorEntryBindings:
    def build_direct_chat_system_prompt(*, workspace_id, availability, tools):
        return direct_chat_prompt_service.build_system_prompt(
            workspace_id=workspace_id,
            availability=availability,
            tools=tools,
            availability_lines=availability_lines,
            build_operator_system_prompt=build_operator_system_prompt,
            memory_tool_names=memory_tool_names,
        )

    def direct_chat_runtime_available():
        return direct_chat_entry_policy_service.direct_chat_runtime_available(
            local_worker_registry,
            is_worker_online_fn=is_worker_online_fn,
        )

    def resolve_direct_chat_availability(workspace_id, requested_provider="", availability_override=None):
        return direct_chat_entry_policy_service.resolve_direct_chat_availability(
            workspace_id,
            requested_provider,
            direct_chat_runtime_available_fn=direct_chat_runtime_available,
            preferred_provider_fn=preferred_provider_fn,
            supports_direct_message_native_chat_fn=supports_direct_message_native_chat_fn,
            resolve_workspace_tool_capabilities_fn=resolve_workspace_tool_capabilities_fn,
            availability_override=availability_override,
        )

    def connected_provider_tokens(workspace_id):
        return direct_chat_entry_policy_service.connected_provider_tokens(
            workspace_id,
            supported_providers=supported_providers,
            direct_chat_credentials_fn=direct_chat_credentials_fn,
        )

    def resolve_provider_for_direct_chat_message(workspace_id, requested_provider, message, *, tools_present):
        return direct_chat_entry_policy_service.resolve_provider_for_direct_chat_message(
            workspace_id,
            requested_provider,
            message,
            tools_present=tools_present,
            preferred_provider_fn=preferred_provider_fn,
            direct_chat_credentials_fn=direct_chat_credentials_fn,
            supports_direct_message_native_chat_fn=supports_direct_message_native_chat_fn,
            compact_text_fn=compact_text_fn,
            mentions_any_fn=mentions_any_fn,
            message_requests_local_file_tool_fn=message_requests_local_file_tool_fn,
            message_requests_local_shell_tool_fn=message_requests_local_shell_tool_fn,
            message_requests_local_screenshot_tool_fn=message_requests_local_screenshot_tool_fn,
            message_requests_local_computer_tool_fn=message_requests_local_computer_tool_fn,
            google_workspace_keywords=google_workspace_keywords,
            telegram_keywords=telegram_keywords,
            slack_keywords=slack_keywords,
            dropbox_keywords=dropbox_keywords,
            s3_keywords=s3_keywords,
        )

    def plan_direct_chat_route(*, message, availability, provider, tools):
        return direct_chat_entry_policy_service.plan_direct_chat_route(
            message=message,
            availability=availability,
            provider=provider,
            tools=tools,
            compact_text_fn=compact_text_fn,
            mentions_any_fn=mentions_any_fn,
            is_obvious_smtp_write_request_fn=is_obvious_smtp_write_request_fn,
            preview_run_response_fn=preview_run_response_fn,
            prefer_durable_run_handoff_fn=prefer_durable_run_handoff_fn,
            durable_run_preferred_response_fn=durable_run_preferred_response_fn,
            message_can_use_direct_connector_tools_fn=lambda inner_message: message_can_use_direct_connector_tools_fn(
                inner_message,
                provider=provider,
                tools=tools,
            ),
            message_can_use_direct_local_tools_fn=lambda inner_message: message_can_use_direct_local_tools_fn(
                inner_message,
                provider=provider,
                tools=tools,
            ),
            message_can_use_builtin_direct_tools_fn=lambda inner_message: message_can_use_builtin_direct_tools_fn(
                inner_message,
                tools=tools,
            ),
            can_auto_start_run_handoff_fn=can_auto_start_run_handoff_fn,
            google_workspace_keywords=google_workspace_keywords,
            telegram_keywords=telegram_keywords,
            slack_keywords=slack_keywords,
            discord_keywords=discord_keywords,
            dropbox_keywords=dropbox_keywords,
            s3_keywords=s3_keywords,
        )

    def credential_auth_mode(provider, credentials):
        return direct_chat_provider_facade_service.credential_auth_mode(
            provider,
            credentials,
            normalize_auth_mode_fn=normalize_auth_mode_fn,
        )

    def supports_direct_message_native_chat(provider, credentials):
        return direct_chat_provider_facade_service.supports_direct_message_native_chat(
            provider,
            credentials,
            credential_auth_mode_fn=credential_auth_mode_fn,
            get_claude_code_session_token_fn=get_claude_code_session_token_fn,
            provider_has_key_fn=provider_has_key_fn,
        )

    def preferred_provider(workspace_id, requested_provider=""):
        return direct_chat_provider_facade_service.preferred_provider(
            workspace_id,
            requested_provider,
            supported_providers=supported_providers,
            direct_chat_credentials_fn=direct_chat_credentials_fn,
            supports_direct_message_native_chat_fn=supports_direct_message_native_chat_fn,
            credential_auth_mode_fn=credential_auth_mode_fn,
        )

    def provider_unavailable_response(provider, issue_code=None, issue_detail=None):
        return direct_chat_provider_facade_service.provider_unavailable_response(
            provider,
            connect_action_fn=connect_action_fn,
            issue_code=issue_code,
            issue_detail=issue_detail,
        )

    def direct_chat_credentials(workspace_id, provider):
        return direct_chat_provider_facade_service.direct_chat_credentials(
            workspace_id,
            provider,
            build_provider_credential_candidates_fn=build_provider_credential_candidates_fn,
        )

    def direct_chat_error_reply(llm_error):
        detail = str(llm_error or "").strip()
        if detail.startswith("max_tool_iterations_reached:"):
            raw_limit = detail.split(":", 1)[1]
            limit = safe_positive_int_fn(raw_limit, chat_max_iterations_default)
            return chat_iteration_limit_reply_fn(limit)
        return direct_chat_provider_facade_service.direct_chat_error_reply(
            llm_error,
            chat_iteration_limit_reply_fn=chat_iteration_limit_reply_fn,
            safe_positive_int_fn=safe_positive_int_fn,
            chat_max_iterations_default=chat_max_iterations_default,
        )

    return DirectChatOperatorEntryBindings(
        build_direct_chat_system_prompt=build_direct_chat_system_prompt,
        direct_chat_runtime_available=direct_chat_runtime_available,
        resolve_direct_chat_availability=resolve_direct_chat_availability,
        connected_provider_tokens=connected_provider_tokens,
        resolve_provider_for_direct_chat_message=resolve_provider_for_direct_chat_message,
        plan_direct_chat_route=plan_direct_chat_route,
        credential_auth_mode=credential_auth_mode,
        supports_direct_message_native_chat=supports_direct_message_native_chat,
        preferred_provider=preferred_provider,
        provider_unavailable_response=provider_unavailable_response,
        direct_chat_credentials=direct_chat_credentials,
        direct_chat_error_reply=direct_chat_error_reply,
    )


def build_direct_chat_handoff_bindings(
    *,
    run_action_fn: Any,
    safe_positive_int_fn: Any,
    open_action_fn: Any,
    build_context_used_fn: Any,
    direct_chat_run_snapshot_fn: Any | None = None,
    direct_chat_run_event_to_step_fn: Any | None = None,
    direct_chat_run_snapshot_to_step_fn: Any | None = None,
    direct_chat_run_final_payload_fn: Any | None = None,
    live_window_seconds: float,
    poll_seconds: float,
    monotonic_fn: Any,
    sleep_fn: Any,
) -> DirectChatOperatorHandoffBindings:
    def durable_run_preferred_response(message: str) -> Dict[str, Any]:
        return direct_chat_handoff_facade_service.durable_run_preferred_response(
            message,
            run_action_fn=run_action_fn,
        )

    def direct_chat_run_handoff_failure_payload(message: str, error_detail: str) -> Dict[str, Any]:
        return direct_chat_handoff_facade_service.direct_chat_run_handoff_failure_payload(
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
    ) -> Dict[str, Any]:
        return direct_chat_handoff_facade_service.start_direct_chat_run_handoff(
            message=message,
            workspace_id=workspace_id,
            requested_provider=requested_provider,
            requested_model=requested_model,
            thread_id=thread_id,
            availability=availability,
            max_iterations=max_iterations,
            safe_positive_int_fn=safe_positive_int_fn,
        )

    def direct_chat_run_handoff_reply(started: Dict[str, Any]) -> Dict[str, Any]:
        return direct_chat_handoff_facade_service.direct_chat_run_handoff_reply(
            started,
            open_action_fn=open_action_fn,
        )

    def direct_chat_run_actions(
        run_id: str,
        *,
        waiting_for_confirmation: bool = False,
    ) -> list[Dict[str, Any]]:
        return direct_chat_handoff_facade_service.direct_chat_run_actions(
            run_id,
            waiting_for_confirmation=waiting_for_confirmation,
            open_action_fn=open_action_fn,
        )

    def direct_chat_run_final_payload(
        *,
        run_id: str,
        run: Optional[Dict[str, Any]],
        snapshot: Dict[str, Any],
        requested_workspace_id: str,
        requested_provider: str,
        requested_model: str,
        reasoning_effort: Optional[str],
        connected_systems: list[str],
        tool_capabilities: list[Dict[str, Any]],
        fallback_reason: Optional[str],
        reply_override: Optional[str] = None,
        continuing: bool = False,
    ) -> Dict[str, Any]:
        return direct_chat_handoff_facade_service.direct_chat_run_final_payload(
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
        connected_systems: list[str],
        tool_capabilities: list[Dict[str, Any]],
        fallback_reason: Optional[str],
    ):
        return direct_chat_handoff_facade_service.stream_direct_chat_run_handoff(
            started_run=started_run,
            requested_workspace_id=requested_workspace_id,
            requested_provider=requested_provider,
            requested_model=requested_model,
            reasoning_effort=reasoning_effort,
            connected_systems=connected_systems,
            tool_capabilities=tool_capabilities,
            fallback_reason=fallback_reason,
            direct_chat_run_snapshot_fn=direct_chat_run_snapshot_fn
            or direct_chat_handoff_facade_service.direct_chat_run_snapshot,
            direct_chat_run_event_to_step_fn=direct_chat_run_event_to_step_fn
            or direct_chat_handoff_facade_service.direct_chat_run_event_to_step,
            direct_chat_run_snapshot_to_step_fn=direct_chat_run_snapshot_to_step_fn
            or direct_chat_handoff_facade_service.direct_chat_run_snapshot_to_step,
            direct_chat_run_final_payload_fn=direct_chat_run_final_payload_fn or direct_chat_run_final_payload,
            open_action_fn=open_action_fn,
            build_context_used_fn=build_context_used_fn,
            live_window_seconds=live_window_seconds,
            poll_seconds=poll_seconds,
            monotonic_fn=monotonic_fn,
            sleep_fn=sleep_fn,
        )

    return DirectChatOperatorHandoffBindings(
        durable_run_preferred_response=durable_run_preferred_response,
        run_handoff_execution_target=direct_chat_handoff_facade_service.run_handoff_execution_target,
        can_auto_start_run_handoff=direct_chat_handoff_facade_service.can_auto_start_run_handoff,
        direct_chat_run_handoff_failure_payload=direct_chat_run_handoff_failure_payload,
        start_direct_chat_run_handoff=start_direct_chat_run_handoff,
        direct_chat_run_handoff_reply=direct_chat_run_handoff_reply,
        direct_chat_run_actions=direct_chat_run_actions,
        direct_chat_run_snapshot=direct_chat_handoff_facade_service.direct_chat_run_snapshot,
        direct_chat_run_event_to_step=direct_chat_handoff_facade_service.direct_chat_run_event_to_step,
        direct_chat_run_snapshot_to_step=direct_chat_handoff_facade_service.direct_chat_run_snapshot_to_step,
        direct_chat_run_final_payload=direct_chat_run_final_payload,
        stream_direct_chat_run_handoff=stream_direct_chat_run_handoff,
    )


def build_direct_chat_availability_bindings(
    *,
    compact_text_fn: Any,
    normalize_tool_capabilities_fn: Any,
    tool_connected_fn: Any,
    tool_runtime_usable_fn: Any,
    connect_action_fn: Any,
    google_repair_action_fn: Any,
    workflow_action_fn: Any,
    run_action_fn: Any,
    workspace_context_dir_fn: Any,
    memory_suggestion_prompts_fn: Any,
    question_openers: tuple[str, ...] | list[str],
    direct_run_openers: tuple[str, ...] | list[str],
    workflow_request_markers: tuple[str, ...] | list[str],
    execution_markers: tuple[str, ...] | list[str],
    google_workspace_keywords: tuple[str, ...] | list[str],
    smtp_keywords: tuple[str, ...] | list[str],
    telegram_keywords: tuple[str, ...] | list[str],
    slack_keywords: tuple[str, ...] | list[str],
    dropbox_keywords: tuple[str, ...] | list[str],
    s3_keywords: tuple[str, ...] | list[str],
) -> DirectChatOperatorAvailabilityBindings:
    def question_like(compact_message: str) -> bool:
        return direct_chat_availability_service.question_like(
            compact_message,
            question_openers=question_openers,
        )

    def mentions_any(compact_message: str, markers) -> bool:
        return direct_chat_availability_service.mentions_any(
            compact_message,
            markers=markers,
        )

    def starts_like_direct_run(compact_message: str) -> bool:
        return direct_chat_availability_service.starts_like_direct_run(
            compact_message,
            direct_run_openers=direct_run_openers,
        )

    def is_obvious_telegram_write_request(compact_message: str) -> bool:
        return direct_chat_availability_service.is_obvious_telegram_write_request(
            compact_message,
            question_like_fn=question_like,
            mentions_any_fn=mentions_any,
            starts_like_direct_run_fn=starts_like_direct_run,
            telegram_keywords=telegram_keywords,
        )

    def is_obvious_google_write_request(compact_message: str) -> bool:
        return direct_chat_availability_service.is_obvious_google_write_request(
            compact_message,
            question_like_fn=question_like,
            starts_like_direct_run_fn=starts_like_direct_run,
        )

    def is_obvious_smtp_write_request(compact_message: str) -> bool:
        return direct_chat_availability_service.is_obvious_smtp_write_request(
            compact_message,
            question_like_fn=question_like,
            mentions_any_fn=mentions_any,
            starts_like_direct_run_fn=starts_like_direct_run,
            smtp_keywords=smtp_keywords,
        )

    def connector_write_preview_allowed(message: str, availability: Dict[str, Any]) -> bool:
        return direct_chat_availability_service.connector_write_preview_allowed(
            message,
            availability,
            compact_text_fn=compact_text_fn,
            is_obvious_telegram_write_request_fn=is_obvious_telegram_write_request,
            is_obvious_google_write_request_fn=is_obvious_google_write_request,
            is_obvious_smtp_write_request_fn=is_obvious_smtp_write_request,
            tool_runtime_usable_fn=tool_runtime_usable_fn,
        )

    def is_explicit_workflow_request(message: str) -> bool:
        return direct_chat_availability_service.is_explicit_workflow_request(
            message,
            compact_text_fn=compact_text_fn,
            mentions_any_fn=mentions_any,
            workflow_request_markers=workflow_request_markers,
        )

    def no_ai_chat_response(availability: Dict[str, Any]) -> Dict[str, Any]:
        return direct_chat_availability_service.no_ai_chat_response(
            availability,
            normalize_tool_capabilities_fn=normalize_tool_capabilities_fn,
            connect_action_fn=connect_action_fn,
        )

    def suggest_actions(message: str, availability: Dict[str, Any]) -> list[Dict[str, Any]]:
        return direct_chat_availability_service.suggest_actions(
            message,
            availability,
            compact_text_fn=compact_text_fn,
            mentions_any_fn=mentions_any,
            question_like_fn=question_like,
            is_explicit_workflow_request_fn=is_explicit_workflow_request,
            is_obvious_smtp_write_request_fn=is_obvious_smtp_write_request,
            tool_runtime_usable_fn=tool_runtime_usable_fn,
            workflow_action_fn=workflow_action_fn,
            run_action_fn=run_action_fn,
            google_workspace_keywords=google_workspace_keywords,
            telegram_keywords=telegram_keywords,
            slack_keywords=slack_keywords,
            dropbox_keywords=dropbox_keywords,
            s3_keywords=s3_keywords,
            execution_markers=execution_markers,
        )

    def heartbeat_pending_tasks_for_suggestions() -> list[str]:
        return direct_chat_support_binding_service.heartbeat_pending_tasks_for_suggestions(
            workspace_context_dir_fn=workspace_context_dir_fn,
        )

    def recent_run_prompts_for_suggestions(workspace_id: str) -> list[str]:
        return direct_chat_operator_support_service.recent_run_prompts_for_suggestions(workspace_id)

    def build_proactive_suggestions(workspace_id: str) -> list[str]:
        return direct_chat_support_binding_service.build_proactive_suggestions(
            workspace_id,
            heartbeat_tasks=heartbeat_pending_tasks_for_suggestions,
            recent_run_prompts=recent_run_prompts_for_suggestions,
            memory_suggestion_prompts=memory_suggestion_prompts_fn,
        )

    return DirectChatOperatorAvailabilityBindings(
        question_like=question_like,
        mentions_any=mentions_any,
        starts_like_direct_run=starts_like_direct_run,
        is_obvious_telegram_write_request=is_obvious_telegram_write_request,
        is_obvious_google_write_request=is_obvious_google_write_request,
        is_obvious_smtp_write_request=is_obvious_smtp_write_request,
        connector_write_preview_allowed=connector_write_preview_allowed,
        is_explicit_workflow_request=is_explicit_workflow_request,
        no_ai_chat_response=no_ai_chat_response,
        suggest_actions=suggest_actions,
        heartbeat_pending_tasks_for_suggestions=heartbeat_pending_tasks_for_suggestions,
        recent_run_prompts_for_suggestions=recent_run_prompts_for_suggestions,
        build_proactive_suggestions=build_proactive_suggestions,
    )


def build_direct_chat_tool_routing_bindings(
    *,
    direct_chat_tool_policy_callbacks: Any,
    direct_chat_routing_policy_callbacks: Any,
    compact_text_fn: Any,
) -> DirectChatOperatorToolRoutingBindings:
    def message_requests_http_request_tool(message: str) -> bool:
        return direct_chat_tool_catalog_service.message_requests_http_request_tool(
            message,
            direct_chat_tool_policy_callbacks(),
        )

    def message_requests_image_generation_tool(message: str) -> bool:
        return direct_chat_tool_catalog_service.message_requests_image_generation_tool(
            message,
            direct_chat_tool_policy_callbacks(),
        )

    def message_requests_browser_tool(message: str) -> bool:
        return direct_chat_tool_catalog_service.message_requests_browser_tool(
            message,
            direct_chat_tool_policy_callbacks(),
        )

    def message_can_use_direct_connector_tools(message: str, *, provider: str, tools) -> bool:
        return direct_chat_tool_catalog_service.message_can_use_direct_connector_tools(
            message,
            provider=provider,
            tools=tools,
            callbacks=direct_chat_tool_policy_callbacks(),
        )

    def message_requests_local_file_tool(message: str) -> bool:
        return direct_chat_tool_catalog_service.message_requests_local_file_tool(
            message,
            direct_chat_tool_policy_callbacks(),
        )

    def message_requests_local_shell_tool(message: str) -> bool:
        return direct_chat_tool_catalog_service.message_requests_local_shell_tool(
            message,
            direct_chat_tool_policy_callbacks(),
        )

    def message_requests_local_screenshot_tool(message: str) -> bool:
        return direct_chat_tool_catalog_service.message_requests_local_screenshot_tool(
            message,
            direct_chat_tool_policy_callbacks(),
        )

    def message_requests_local_computer_tool(message: str) -> bool:
        return direct_chat_tool_catalog_service.message_requests_local_computer_tool(
            message,
            direct_chat_tool_policy_callbacks(),
        )

    def message_can_use_direct_local_tools(message: str, *, provider: str, tools) -> bool:
        return direct_chat_tool_catalog_service.message_can_use_direct_local_tools(
            message,
            provider=provider,
            tools=tools,
            callbacks=direct_chat_tool_policy_callbacks(),
        )

    def message_can_use_builtin_direct_tools(message: str, *, tools) -> bool:
        return direct_chat_tool_catalog_service.message_can_use_builtin_direct_tools(
            message,
            tools=tools,
            callbacks=direct_chat_tool_policy_callbacks(),
        )

    def preview_run_response(message: str, availability: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return direct_chat_routing_service.preview_run_response(
            message,
            availability,
            direct_chat_routing_policy_callbacks(),
        )

    def prefer_durable_run_handoff(message: str, availability: Dict[str, Any]) -> bool:
        return direct_chat_routing_service.prefer_durable_run_handoff(
            message,
            availability,
            direct_chat_routing_policy_callbacks(),
        )

    return DirectChatOperatorToolRoutingBindings(
        message_requests_http_request_tool=message_requests_http_request_tool,
        message_requests_image_generation_tool=message_requests_image_generation_tool,
        message_requests_browser_tool=message_requests_browser_tool,
        message_can_use_direct_connector_tools=message_can_use_direct_connector_tools,
        looks_like_local_path_request=direct_chat_tool_catalog_service.looks_like_local_path_request,
        message_requests_local_file_tool=message_requests_local_file_tool,
        message_requests_local_shell_tool=message_requests_local_shell_tool,
        message_requests_local_screenshot_tool=message_requests_local_screenshot_tool,
        message_requests_local_computer_tool=message_requests_local_computer_tool,
        message_can_use_direct_local_tools=message_can_use_direct_local_tools,
        message_can_use_builtin_direct_tools=message_can_use_builtin_direct_tools,
        preview_run_response=preview_run_response,
        prefer_durable_run_handoff=prefer_durable_run_handoff,
    )


def build_direct_chat_state_bindings(
    *,
    agent_machine_full_trust_enabled_fn: Any,
    normalize_tool_capabilities_fn: Any,
    max_context_tool_actions: int,
    max_context_tool_capabilities: int,
    max_direct_chat_prior_message_chars: int,
    max_direct_chat_prior_messages: int,
    direct_chat_model_preferences: dict[str, Any],
    direct_chat_clear_markers: set[str],
    direct_tool_loop_state: dict[str, Any],
    direct_chat_loop_repeat_limit: int,
    parse_json_object_loose_fn: Any,
    generate_reply_fn: Any,
    extraction_prompt: str,
    extraction_system_prompt: str,
    save_session_transcript_fn: Any,
    system_prefix: str,
) -> DirectChatOperatorStateBindings:
    def agent_machine_full_trust_for_session(session_ctx):
        effective_session_mode = direct_chat_context_service.session_mode_effective(session_ctx)
        if effective_session_mode:
            if effective_session_mode != "agent":
                return False
        return agent_machine_full_trust_enabled_fn(
            direct_chat_context_service.agent_machine_owner_user_id(session_ctx),
        )

    def availability_lines(workspace_id, availability):
        return direct_chat_entry_policy_service.availability_lines(
            workspace_id,
            availability,
            normalize_tool_capabilities=normalize_tool_capabilities_fn,
        )

    def connected_system_labels(availability):
        return direct_chat_entry_policy_service.connected_system_labels(
            availability,
            normalize_tool_capabilities=normalize_tool_capabilities_fn,
        )

    def context_tool_capabilities(availability):
        return direct_chat_entry_policy_service.context_tool_capabilities(
            availability,
            normalize_tool_capabilities=normalize_tool_capabilities_fn,
            max_context_tool_actions=max_context_tool_actions,
            max_context_tool_capabilities=max_context_tool_capabilities,
        )

    def normalize_prior_messages(prior_messages):
        return direct_chat_entry_policy_service.normalize_prior_messages(
            prior_messages,
            max_direct_chat_prior_message_chars=max_direct_chat_prior_message_chars,
            max_direct_chat_prior_messages=max_direct_chat_prior_messages,
        )

    def session_model_preference(session_key):
        return direct_chat_entry_policy_service.session_model_preference(
            session_key,
            store=direct_chat_model_preferences,
        )

    def set_session_model_preference(session_key, provider=None, model=None):
        return direct_chat_entry_policy_service.set_session_model_preference(
            session_key,
            provider=provider,
            model=model,
            store=direct_chat_model_preferences,
        )

    def mark_thread_cleared(session_key):
        return direct_chat_entry_policy_service.mark_thread_cleared(
            session_key,
            clear_markers=direct_chat_clear_markers,
        )

    def consume_thread_cleared(session_key):
        return direct_chat_entry_policy_service.consume_thread_cleared(
            session_key,
            clear_markers=direct_chat_clear_markers,
        )

    def tool_call_signature(tool_call):
        return direct_tool_loop_guard_service.tool_call_signature(
            tool_call,
            tool_arguments_payload_fn=lambda arguments: tool_arguments_payload(arguments, parse_json_object_loose_fn=parse_json_object_loose_fn),
            parse_tool_name_fn=parse_tool_name,
            parse_json_object_loose_fn=parse_json_object_loose_fn,
        )

    def record_direct_tool_signature(session_key, tool_call):
        return direct_tool_loop_guard_service.record_direct_tool_signature(
            session_key,
            tool_call,
            loop_state=direct_tool_loop_state,
            repeat_limit=direct_chat_loop_repeat_limit,
            tool_call_signature_fn=tool_call_signature,
        )

    def clear_direct_tool_loop_state(session_key):
        return direct_tool_loop_guard_service.clear_direct_tool_loop_state(
            session_key,
            loop_state=direct_tool_loop_state,
        )

    def direct_chat_memory_context_message(message):
        return direct_chat_memory_facade_service.direct_chat_memory_context_message(
            message,
            system_prefix=system_prefix,
        )

    def persist_direct_chat_memory_best_effort(**kwargs):
        return direct_chat_support_binding_service.persist_direct_chat_memory_best_effort(
            generate_reply=generate_reply_fn,
            extraction_prompt=extraction_prompt,
            extraction_system_prompt=extraction_system_prompt,
            **kwargs,
        )

    def persist_direct_chat_transcript_best_effort(**kwargs):
        return direct_chat_support_binding_service.persist_direct_chat_transcript_best_effort(
            save_session_transcript_fn=save_session_transcript_fn,
            **kwargs,
        )

    def persist_direct_chat_hosted_usage_best_effort(**kwargs):
        return direct_chat_support_binding_service.persist_direct_chat_hosted_usage_best_effort(
            **kwargs,
        )

    return DirectChatOperatorStateBindings(
        agent_machine_owner_user_id=direct_chat_context_service.agent_machine_owner_user_id,
        agent_machine_full_trust_for_session=agent_machine_full_trust_for_session,
        availability_lines=availability_lines,
        connected_system_labels=connected_system_labels,
        context_tool_capabilities=context_tool_capabilities,
        normalize_prior_messages=normalize_prior_messages,
        direct_tool_session_key=direct_chat_entry_policy_service.direct_tool_session_key,
        direct_chat_session_key=direct_chat_entry_policy_service.direct_chat_session_key,
        parse_slash_command=direct_chat_entry_policy_service.parse_slash_command,
        session_model_preference=session_model_preference,
        set_session_model_preference=set_session_model_preference,
        mark_thread_cleared=mark_thread_cleared,
        consume_thread_cleared=consume_thread_cleared,
        active_run_count=direct_chat_operator_support_service.active_run_count,
        slash_command_help_text=direct_chat_entry_policy_service.slash_command_help_text,
        tool_call_signature=tool_call_signature,
        record_direct_tool_signature=record_direct_tool_signature,
        clear_direct_tool_loop_state=clear_direct_tool_loop_state,
        direct_chat_memory_context_message=direct_chat_memory_context_message,
        direct_chat_workspace_context_text=direct_chat_memory_facade_service.direct_chat_workspace_context_text,
        build_direct_chat_daily_log_summary=direct_chat_memory_facade_service.build_direct_chat_daily_log_summary,
        persist_direct_chat_memory_best_effort=persist_direct_chat_memory_best_effort,
        persist_direct_chat_transcript_best_effort=persist_direct_chat_transcript_best_effort,
        persist_direct_chat_hosted_usage_best_effort=persist_direct_chat_hosted_usage_best_effort,
        build_context_used=direct_chat_support_binding_service.build_context_used,
        with_context_used=direct_chat_metadata_service.with_context_used,
    )


def build_direct_chat_shell_bindings(
    *,
    agent_machine_full_trust_enabled_fn: Any,
    chat_max_iterations_default: int,
    chat_max_iterations_ceiling: int,
    compact_text_fn: Any,
    direct_chat_compaction_token_limit: int,
    execution_markers: tuple[str, ...] | list[str],
    question_openers: tuple[str, ...] | list[str],
    direct_run_openers: tuple[str, ...] | list[str],
    workflow_request_markers: tuple[str, ...] | list[str],
    google_workspace_keywords: tuple[str, ...] | list[str],
    smtp_keywords: tuple[str, ...] | list[str],
    telegram_keywords: tuple[str, ...] | list[str],
    slack_keywords: tuple[str, ...] | list[str],
    dropbox_keywords: tuple[str, ...] | list[str],
    s3_keywords: tuple[str, ...] | list[str],
    max_context_tool_actions: int,
    max_context_tool_capabilities: int,
    max_direct_chat_prior_message_chars: int,
    max_direct_chat_prior_messages: int,
    direct_chat_model_preferences: Dict[str, Dict[str, Optional[str]]],
    direct_chat_clear_markers: set[str],
    direct_tool_loop_state: Dict[str, Dict[str, Any]],
    direct_chat_loop_repeat_limit: int,
    parse_json_object_loose_fn: Any,
    generate_reply_fn: Any,
    extraction_prompt: str,
    extraction_system_prompt: str,
    save_session_transcript_fn: Any,
    system_prefix: str,
    workspace_context_dir_fn: Any,
    memory_suggestion_prompts_fn: Any,
    run_handoff_execution_target_fn: Any,
    direct_chat_run_snapshot_fn: Any,
    direct_chat_run_event_to_step_fn: Any,
    direct_chat_run_snapshot_to_step_fn: Any,
    direct_chat_run_final_payload_fn: Any,
    live_window_seconds: float,
    poll_seconds: float,
    monotonic_fn: Any,
    sleep_fn: Any,
    parse_json_object_loose_support_fn: Any,
    direct_chat_tool_policy_callbacks_fn: Any,
    direct_chat_routing_policy_callbacks_fn: Any,
) -> DirectChatOperatorShellBindings:
    safe_positive_int = direct_chat_entry_policy_service.safe_positive_int
    resolved_chat_iteration_limit = lambda value=None: direct_chat_entry_policy_service.resolved_chat_iteration_limit(
        value,
        default_limit=chat_max_iterations_default,
        ceiling=chat_max_iterations_ceiling,
        env_var_name="ORION_MAX_CHAT_ITERATIONS",
        safe_positive_int_fn=safe_positive_int,
    )
    normalize_tool_capabilities = direct_chat_operator_support_service.normalize_tool_capabilities
    tool_capability = direct_chat_operator_support_service.tool_capability
    tool_connected = direct_chat_operator_support_service.tool_connected
    tool_runtime_usable = direct_chat_operator_support_service.tool_runtime_usable
    local_worker_available = direct_chat_operator_support_service.local_worker_available
    connect_action = direct_chat_availability_service.connect_action
    open_action = direct_chat_availability_service.open_action
    google_repair_action = lambda: direct_chat_availability_service.google_repair_action(
        connect_action_fn=connect_action,
    )
    run_action = direct_chat_availability_service.run_action
    workflow_action = direct_chat_availability_service.workflow_action

    state_bindings = build_direct_chat_state_bindings(
        agent_machine_full_trust_enabled_fn=agent_machine_full_trust_enabled_fn,
        normalize_tool_capabilities_fn=normalize_tool_capabilities,
        max_context_tool_actions=max_context_tool_actions,
        max_context_tool_capabilities=max_context_tool_capabilities,
        max_direct_chat_prior_message_chars=max_direct_chat_prior_message_chars,
        max_direct_chat_prior_messages=max_direct_chat_prior_messages,
        direct_chat_model_preferences=direct_chat_model_preferences,
        direct_chat_clear_markers=direct_chat_clear_markers,
        direct_tool_loop_state=direct_tool_loop_state,
        direct_chat_loop_repeat_limit=direct_chat_loop_repeat_limit,
        parse_json_object_loose_fn=parse_json_object_loose_fn,
        generate_reply_fn=generate_reply_fn,
        extraction_prompt=extraction_prompt,
        extraction_system_prompt=extraction_system_prompt,
        save_session_transcript_fn=save_session_transcript_fn,
        system_prefix=system_prefix,
    )
    availability_bindings = build_direct_chat_availability_bindings(
        compact_text_fn=compact_text_fn,
        normalize_tool_capabilities_fn=normalize_tool_capabilities,
        tool_connected_fn=tool_connected,
        tool_runtime_usable_fn=tool_runtime_usable,
        connect_action_fn=connect_action,
        google_repair_action_fn=google_repair_action,
        workflow_action_fn=workflow_action,
        run_action_fn=run_action,
        workspace_context_dir_fn=workspace_context_dir_fn,
        memory_suggestion_prompts_fn=memory_suggestion_prompts_fn,
        question_openers=question_openers,
        direct_run_openers=direct_run_openers,
        workflow_request_markers=workflow_request_markers,
        execution_markers=execution_markers,
        google_workspace_keywords=google_workspace_keywords,
        smtp_keywords=smtp_keywords,
        telegram_keywords=telegram_keywords,
        slack_keywords=slack_keywords,
        dropbox_keywords=dropbox_keywords,
        s3_keywords=s3_keywords,
    )
    handoff_bindings = build_direct_chat_handoff_bindings(
        run_action_fn=run_action,
        safe_positive_int_fn=safe_positive_int,
        open_action_fn=open_action,
        build_context_used_fn=state_bindings.build_context_used,
        direct_chat_run_snapshot_fn=direct_chat_run_snapshot_fn,
        direct_chat_run_event_to_step_fn=direct_chat_run_event_to_step_fn,
        direct_chat_run_snapshot_to_step_fn=direct_chat_run_snapshot_to_step_fn,
        direct_chat_run_final_payload_fn=direct_chat_run_final_payload_fn,
        live_window_seconds=live_window_seconds,
        poll_seconds=poll_seconds,
        monotonic_fn=monotonic_fn,
        sleep_fn=sleep_fn,
    )
    action_marker_count = lambda compact: direct_chat_routing_service.action_marker_count(
        compact,
        execution_markers=execution_markers,
    )
    path_like_reference_count = direct_chat_routing_service.path_like_reference_count
    tool_routing_bindings = build_direct_chat_tool_routing_bindings(
        direct_chat_tool_policy_callbacks=direct_chat_tool_policy_callbacks_fn,
        direct_chat_routing_policy_callbacks=direct_chat_routing_policy_callbacks_fn,
        compact_text_fn=compact_text_fn,
    )
    tool_support_bindings = build_direct_chat_tool_support_bindings(
        parse_json_object_loose_fn=parse_json_object_loose_support_fn,
    )

    return DirectChatOperatorShellBindings(
        safe_positive_int=safe_positive_int,
        resolved_chat_iteration_limit=resolved_chat_iteration_limit,
        chat_iteration_limit_reply=direct_chat_entry_policy_service.chat_iteration_limit_reply,
        compact_text=compact_text_fn,
        normalize_tool_capabilities=normalize_tool_capabilities,
        tool_capability=tool_capability,
        tool_connected=tool_connected,
        tool_runtime_usable=tool_runtime_usable,
        local_worker_available=local_worker_available,
        connect_action=connect_action,
        open_action=open_action,
        google_repair_action=google_repair_action,
        run_action=run_action,
        workflow_action=workflow_action,
        action_marker_count=action_marker_count,
        path_like_reference_count=path_like_reference_count,
        provider_supports_direct_tool_calls=direct_chat_tool_catalog_service.provider_supports_direct_tool_calls,
        build_local_direct_chat_tools=lambda availability: direct_chat_tool_catalog_service.build_local_direct_chat_tools(
            availability,
            local_worker_available=local_worker_available,
        ),
        build_direct_chat_tools=direct_chat_tool_catalog_service.build_direct_chat_tools,
        build_builtin_direct_chat_tools=direct_chat_tool_catalog_service.build_builtin_direct_chat_tools,
        build_always_on_direct_chat_tools=direct_chat_tool_catalog_service.build_always_on_direct_chat_tools,
        build_registry_entries=direct_chat_tool_catalog_service.build_registry_entries,
        registered_direct_chat_tool_names_for_logging=direct_chat_tool_catalog_service.registered_direct_chat_tool_names_for_logging,
        state_bindings=state_bindings,
        availability_bindings=availability_bindings,
        handoff_bindings=handoff_bindings,
        tool_support_bindings=tool_support_bindings,
        tool_routing_bindings=tool_routing_bindings,
    )


def build_direct_chat_operator_binding_bundle(
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
    complex_task_sequence_markers: tuple[str, ...] | list[str],
    complex_task_outcome_markers: tuple[str, ...] | list[str],
    execution_markers: tuple[str, ...] | list[str],
    google_workspace_keywords: tuple[str, ...] | list[str],
    smtp_keywords: tuple[str, ...] | list[str],
    telegram_keywords: tuple[str, ...] | list[str],
    slack_keywords: tuple[str, ...] | list[str],
    discord_keywords: tuple[str, ...] | list[str],
    dropbox_keywords: tuple[str, ...] | list[str],
    s3_keywords: tuple[str, ...] | list[str],
    browser_keywords: tuple[str, ...] | list[str],
    local_file_keywords: tuple[str, ...] | list[str],
    local_shell_keywords: tuple[str, ...] | list[str],
    local_screenshot_keywords: tuple[str, ...] | list[str],
    local_computer_control_keywords: tuple[str, ...] | list[str],
    web_lookup_keywords: tuple[str, ...] | list[str],
    http_request_keywords: tuple[str, ...] | list[str],
    image_generation_keywords: tuple[str, ...] | list[str],
    llm_task_keywords: tuple[str, ...] | list[str],
    parse_json_object_loose: Any,
    llm_task: Any,
    web_search: Any,
    web_fetch: Any,
    search_memory_notebook: Any,
    get_memory_notebook_excerpt: Any,
    availability_lines: Any,
    build_operator_system_prompt: Any,
    memory_tool_names: Any,
    local_worker_registry: Dict[str, Any],
    is_worker_online_fn: Any,
    preferred_provider_fn: Any,
    supports_direct_message_native_chat_fn: Any,
    resolve_workspace_tool_capabilities_fn: Any,
    direct_chat_credentials_fn: Any,
    build_provider_credential_candidates_fn: Any,
    compact_text_fn: Any,
    mentions_any_fn: Any,
    message_requests_local_file_tool_fn: Any,
    message_requests_local_shell_tool_fn: Any,
    message_requests_local_screenshot_tool_fn: Any,
    message_requests_local_computer_tool_fn: Any,
    is_obvious_smtp_write_request_fn: Any,
    preview_run_response_fn: Any,
    prefer_durable_run_handoff_fn: Any,
    durable_run_preferred_response_fn: Any,
    message_can_use_direct_connector_tools_fn: Any,
    message_can_use_direct_local_tools_fn: Any,
    message_can_use_builtin_direct_tools_fn: Any,
    can_auto_start_run_handoff_fn: Any,
    credential_auth_mode_fn: Any,
    normalize_auth_mode_fn: Any,
    get_claude_code_session_token_fn: Any,
    provider_has_key_fn: Any,
    connect_action_fn: Any,
    chat_iteration_limit_reply_fn: Any,
    safe_positive_int_fn: Any,
    chat_max_iterations_default: int,
    execute_single_direct_tool_call_fn: Any,
) -> DirectChatOperatorBindingBundle:
    runtime_bindings = build_direct_chat_runtime_bindings(
        namespace=namespace,
        parse_page_state=parse_page_state,
        capture_exception=capture_exception,
        generate_chat_reply_stream_with_provider_fallback=generate_chat_reply_stream_with_provider_fallback,
        compact_conversation_history=compact_conversation_history,
        parse_memory_write=parse_memory_write,
        parse_memory_read=parse_memory_read,
        handle_memory_request=handle_memory_request,
        list_memory_entries=list_memory_entries,
        get_memory=get_memory,
        delete_memory=delete_memory,
        no_provider_reasoning_required_response=no_provider_reasoning_required_response,
        supported_providers=supported_providers,
        direct_chat_compaction_token_limit=direct_chat_compaction_token_limit,
    )
    policy_bindings = build_direct_chat_policy_bindings(
        namespace=namespace,
        complex_task_sequence_markers=complex_task_sequence_markers,
        complex_task_outcome_markers=complex_task_outcome_markers,
        execution_markers=execution_markers,
        google_workspace_keywords=google_workspace_keywords,
        smtp_keywords=smtp_keywords,
        telegram_keywords=telegram_keywords,
        slack_keywords=slack_keywords,
        discord_keywords=discord_keywords,
        dropbox_keywords=dropbox_keywords,
        s3_keywords=s3_keywords,
        browser_keywords=browser_keywords,
        local_file_keywords=local_file_keywords,
        local_shell_keywords=local_shell_keywords,
        local_screenshot_keywords=local_screenshot_keywords,
        local_computer_control_keywords=local_computer_control_keywords,
        web_lookup_keywords=web_lookup_keywords,
        http_request_keywords=http_request_keywords,
        image_generation_keywords=image_generation_keywords,
        llm_task_keywords=llm_task_keywords,
        parse_json_object_loose=parse_json_object_loose,
        llm_task=llm_task,
        web_search=web_search,
        web_fetch=web_fetch,
        search_memory_notebook=search_memory_notebook,
        get_memory_notebook_excerpt=get_memory_notebook_excerpt,
    )
    entry_bindings = build_direct_chat_entry_bindings(
        availability_lines=availability_lines,
        build_operator_system_prompt=build_operator_system_prompt,
        memory_tool_names=memory_tool_names,
        local_worker_registry=local_worker_registry,
        is_worker_online_fn=is_worker_online_fn,
        preferred_provider_fn=preferred_provider_fn,
        supports_direct_message_native_chat_fn=supports_direct_message_native_chat_fn,
        resolve_workspace_tool_capabilities_fn=resolve_workspace_tool_capabilities_fn,
        supported_providers=supported_providers,
        direct_chat_credentials_fn=direct_chat_credentials_fn,
        build_provider_credential_candidates_fn=build_provider_credential_candidates_fn,
        compact_text_fn=compact_text_fn,
        mentions_any_fn=mentions_any_fn,
        message_requests_local_file_tool_fn=message_requests_local_file_tool_fn,
        message_requests_local_shell_tool_fn=message_requests_local_shell_tool_fn,
        message_requests_local_screenshot_tool_fn=message_requests_local_screenshot_tool_fn,
        message_requests_local_computer_tool_fn=message_requests_local_computer_tool_fn,
        is_obvious_smtp_write_request_fn=is_obvious_smtp_write_request_fn,
        preview_run_response_fn=preview_run_response_fn,
        prefer_durable_run_handoff_fn=prefer_durable_run_handoff_fn,
        durable_run_preferred_response_fn=durable_run_preferred_response_fn,
        message_can_use_direct_connector_tools_fn=message_can_use_direct_connector_tools_fn,
        message_can_use_direct_local_tools_fn=message_can_use_direct_local_tools_fn,
        message_can_use_builtin_direct_tools_fn=message_can_use_builtin_direct_tools_fn,
        can_auto_start_run_handoff_fn=can_auto_start_run_handoff_fn,
        credential_auth_mode_fn=credential_auth_mode_fn,
        normalize_auth_mode_fn=normalize_auth_mode_fn,
        get_claude_code_session_token_fn=get_claude_code_session_token_fn,
        provider_has_key_fn=provider_has_key_fn,
        connect_action_fn=connect_action_fn,
        chat_iteration_limit_reply_fn=chat_iteration_limit_reply_fn,
        safe_positive_int_fn=safe_positive_int_fn,
        chat_max_iterations_default=chat_max_iterations_default,
        google_workspace_keywords=google_workspace_keywords,
        telegram_keywords=telegram_keywords,
        slack_keywords=slack_keywords,
        discord_keywords=discord_keywords,
        dropbox_keywords=dropbox_keywords,
        s3_keywords=s3_keywords,
    )
    tool_runtime_bindings = build_direct_chat_tool_runtime_bindings(
        direct_chat_runtime_facade_callbacks=lambda: runtime_bindings.runtime_facade_callbacks(),
        direct_tool_execution_callbacks=lambda: policy_bindings.direct_tool_execution_callbacks(),
        execute_single_direct_tool_call_fn=execute_single_direct_tool_call_fn,
    )
    return DirectChatOperatorBindingBundle(
        runtime_bindings=runtime_bindings,
        policy_bindings=policy_bindings,
        entry_bindings=entry_bindings,
        tool_runtime_bindings=tool_runtime_bindings,
    )


def build_direct_chat_operator_binding_bundle_from_namespace(
    *,
    namespace: Dict[str, Any],
    parse_json_object_loose_fn: Any,
    capture_exception_fn: Any,
    generate_chat_reply_stream_with_provider_fallback_fn: Any,
    compact_conversation_history_fn: Any,
    parse_memory_write_fn: Any,
    parse_memory_read_fn: Any,
    handle_memory_request_fn: Any,
    list_memory_entries_fn: Any,
    get_memory_fn: Any,
    delete_memory_fn: Any,
    no_provider_reasoning_required_response_fn: Any,
    supported_providers: list[str],
    direct_chat_compaction_token_limit: int,
    complex_task_sequence_markers: tuple[str, ...] | list[str],
    complex_task_outcome_markers: tuple[str, ...] | list[str],
    execution_markers: tuple[str, ...] | list[str],
    google_workspace_keywords: tuple[str, ...] | list[str],
    smtp_keywords: tuple[str, ...] | list[str],
    telegram_keywords: tuple[str, ...] | list[str],
    slack_keywords: tuple[str, ...] | list[str],
    discord_keywords: tuple[str, ...] | list[str],
    dropbox_keywords: tuple[str, ...] | list[str],
    s3_keywords: tuple[str, ...] | list[str],
    browser_keywords: tuple[str, ...] | list[str],
    local_file_keywords: tuple[str, ...] | list[str],
    local_shell_keywords: tuple[str, ...] | list[str],
    local_screenshot_keywords: tuple[str, ...] | list[str],
    local_computer_control_keywords: tuple[str, ...] | list[str],
    web_lookup_keywords: tuple[str, ...] | list[str],
    http_request_keywords: tuple[str, ...] | list[str],
    image_generation_keywords: tuple[str, ...] | list[str],
    llm_task_keywords: tuple[str, ...] | list[str],
    llm_task_fn: Any,
    web_search_fn: Any,
    web_fetch_fn: Any,
    search_memory_notebook_fn: Any,
    get_memory_notebook_excerpt_fn: Any,
    build_operator_system_prompt_fn: Any,
    memory_tool_names: Any,
    local_worker_registry: Dict[str, Any],
    is_worker_online_fn: Any,
    resolve_workspace_tool_capabilities_fn: Any,
    build_provider_credential_candidates_fn: Any,
    normalize_auth_mode_fn: Any,
    get_claude_code_session_token_fn: Any,
    provider_has_key_fn: Any,
    connect_action_fn: Any,
    chat_iteration_limit_reply_fn: Any,
    safe_positive_int_fn: Any,
    chat_max_iterations_default: int,
) -> DirectChatOperatorBindingBundle:
    availability_lines_fn = _lookup(namespace, "availability_lines")
    compact_text_fn = _lookup(namespace, "compact_text")
    mentions_any_fn = _lookup(namespace, "mentions_any")
    message_requests_local_file_tool_fn = _lookup(namespace, "message_requests_local_file_tool")
    message_requests_local_shell_tool_fn = _lookup(namespace, "message_requests_local_shell_tool")
    message_requests_local_screenshot_tool_fn = _lookup(namespace, "message_requests_local_screenshot_tool")
    message_requests_local_computer_tool_fn = _lookup(namespace, "message_requests_local_computer_tool")
    is_obvious_smtp_write_request_fn = _lookup(namespace, "is_obvious_smtp_write_request")

    def _namespace_value(name: str, fallback: Any) -> Any:
        return namespace.get(name, fallback)

    return build_direct_chat_operator_binding_bundle(
        namespace=namespace,
        parse_page_state=lambda payload: _namespace_value("parse_json_object_loose", parse_json_object_loose_fn)(payload),
        capture_exception=capture_exception_fn,
        generate_chat_reply_stream_with_provider_fallback=lambda **kwargs: _namespace_value(
            "generate_chat_reply_stream_with_provider_fallback",
            generate_chat_reply_stream_with_provider_fallback_fn,
        )(**kwargs),
        compact_conversation_history=compact_conversation_history_fn,
        parse_memory_write=parse_memory_write_fn,
        parse_memory_read=parse_memory_read_fn,
        handle_memory_request=handle_memory_request_fn,
        list_memory_entries=list_memory_entries_fn,
        get_memory=get_memory_fn,
        delete_memory=delete_memory_fn,
        no_provider_reasoning_required_response=no_provider_reasoning_required_response_fn,
        supported_providers=supported_providers,
        direct_chat_compaction_token_limit=direct_chat_compaction_token_limit,
        complex_task_sequence_markers=complex_task_sequence_markers,
        complex_task_outcome_markers=complex_task_outcome_markers,
        execution_markers=execution_markers,
        google_workspace_keywords=google_workspace_keywords,
        smtp_keywords=smtp_keywords,
        telegram_keywords=telegram_keywords,
        slack_keywords=slack_keywords,
        discord_keywords=discord_keywords,
        dropbox_keywords=dropbox_keywords,
        s3_keywords=s3_keywords,
        browser_keywords=browser_keywords,
        local_file_keywords=local_file_keywords,
        local_shell_keywords=local_shell_keywords,
        local_screenshot_keywords=local_screenshot_keywords,
        local_computer_control_keywords=local_computer_control_keywords,
        web_lookup_keywords=web_lookup_keywords,
        http_request_keywords=http_request_keywords,
        image_generation_keywords=image_generation_keywords,
        llm_task_keywords=llm_task_keywords,
        parse_json_object_loose=lambda value: _namespace_value("parse_json_object_loose", parse_json_object_loose_fn)(value),
        llm_task=lambda *args, **kwargs: _namespace_value("llm_task", llm_task_fn)(*args, **kwargs),
        web_search=lambda *args, **kwargs: _namespace_value("web_search", web_search_fn)(*args, **kwargs),
        web_fetch=lambda *args, **kwargs: _namespace_value("web_fetch", web_fetch_fn)(*args, **kwargs),
        search_memory_notebook=lambda *args, **kwargs: _namespace_value(
            "search_memory_notebook",
            search_memory_notebook_fn,
        )(*args, **kwargs),
        get_memory_notebook_excerpt=lambda *args, **kwargs: _namespace_value(
            "get_memory_notebook_excerpt",
            get_memory_notebook_excerpt_fn,
        )(*args, **kwargs),
        availability_lines=availability_lines_fn,
        build_operator_system_prompt=build_operator_system_prompt_fn,
        memory_tool_names=memory_tool_names,
        local_worker_registry=local_worker_registry,
        is_worker_online_fn=is_worker_online_fn,
        preferred_provider_fn=lambda workspace_id, requested_provider="": _lookup(namespace, "preferred_provider")(workspace_id, requested_provider),
        supports_direct_message_native_chat_fn=lambda provider, credentials: _lookup(namespace, "supports_direct_message_native_chat")(provider, credentials),
        resolve_workspace_tool_capabilities_fn=lambda workspace_id: _namespace_value(
            "resolve_workspace_tool_capabilities",
            resolve_workspace_tool_capabilities_fn,
        )(workspace_id),
        direct_chat_credentials_fn=lambda workspace_id, provider: _lookup(namespace, "direct_chat_credentials")(workspace_id, provider),
        build_provider_credential_candidates_fn=lambda *args, **kwargs: _namespace_value(
            "_build_provider_credential_candidates",
            build_provider_credential_candidates_fn,
        )(*args, **kwargs),
        compact_text_fn=compact_text_fn,
        mentions_any_fn=mentions_any_fn,
        message_requests_local_file_tool_fn=lambda message: message_requests_local_file_tool_fn(message),
        message_requests_local_shell_tool_fn=lambda message: message_requests_local_shell_tool_fn(message),
        message_requests_local_screenshot_tool_fn=lambda message: message_requests_local_screenshot_tool_fn(message),
        message_requests_local_computer_tool_fn=lambda message: message_requests_local_computer_tool_fn(message),
        is_obvious_smtp_write_request_fn=lambda message: is_obvious_smtp_write_request_fn(message),
        preview_run_response_fn=lambda message, availability: _lookup(namespace, "preview_run_response")(message, availability),
        prefer_durable_run_handoff_fn=lambda message, availability: _lookup(namespace, "prefer_durable_run_handoff")(message, availability),
        durable_run_preferred_response_fn=lambda message: _lookup(namespace, "durable_run_preferred_response")(message),
        message_can_use_direct_connector_tools_fn=lambda message, *, provider, tools: _lookup(namespace, "message_can_use_direct_connector_tools")(message, provider=provider, tools=tools),
        message_can_use_direct_local_tools_fn=lambda message, *, provider, tools: _lookup(namespace, "message_can_use_direct_local_tools")(message, provider=provider, tools=tools),
        message_can_use_builtin_direct_tools_fn=lambda message, *, tools: _lookup(namespace, "message_can_use_builtin_direct_tools")(message, tools=tools),
        can_auto_start_run_handoff_fn=lambda availability: _lookup(namespace, "can_auto_start_run_handoff")(availability),
        credential_auth_mode_fn=lambda provider, credentials: _lookup(namespace, "credential_auth_mode")(provider, credentials),
        normalize_auth_mode_fn=normalize_auth_mode_fn,
        get_claude_code_session_token_fn=lambda: _namespace_value(
            "get_claude_code_session_token",
            get_claude_code_session_token_fn,
        )(),
        provider_has_key_fn=lambda provider: _namespace_value("provider_has_key", provider_has_key_fn)(provider),
        connect_action_fn=connect_action_fn,
        chat_iteration_limit_reply_fn=chat_iteration_limit_reply_fn,
        safe_positive_int_fn=safe_positive_int_fn,
        chat_max_iterations_default=chat_max_iterations_default,
        execute_single_direct_tool_call_fn=lambda **kwargs: _lookup(namespace, "execute_single_direct_tool_call")(**kwargs),
    )


def build_direct_chat_entrypoint_bindings(
    *,
    direct_chat_runtime_services_fn: Any,
) -> DirectChatOperatorEntrypointBindings:
    def build_direct_operator_reply(
        *,
        message,
        workspace_id,
        requested_model,
        requested_provider,
        thread_id="",
        prior_messages=None,
        reasoning_effort="",
        availability=None,
        approved_action=None,
        max_iterations=None,
        session_ctx=None,
        agent_turn_request=None,
        trace_context=None,
    ):
        return direct_chat_runtime_entry_facade_service.build_direct_operator_reply(
            services=direct_chat_runtime_services_fn(),
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

    def collect_direct_operator_reply(**kwargs):
        return direct_chat_runtime_entry_facade_service.collect_direct_operator_reply(
            services=direct_chat_runtime_services_fn(),
            **kwargs,
        )

    def build_chat_turn_event_stream(*, session_ctx, message, request_meta=None):
        return direct_chat_runtime_entry_facade_service.build_chat_turn_event_stream(
            services=direct_chat_runtime_services_fn(),
            session_ctx=session_ctx,
            message=message,
            request_meta=request_meta,
        )

    def execute_chat_turn(session_ctx, message, stream_sink=None, request_meta=None):
        return direct_chat_runtime_entry_facade_service.execute_chat_turn(
            services=direct_chat_runtime_services_fn(),
            session_ctx=session_ctx,
            message=message,
            stream_sink=stream_sink,
            request_meta=request_meta,
        )

    return DirectChatOperatorEntrypointBindings(
        build_direct_operator_reply=build_direct_operator_reply,
        collect_direct_operator_reply=collect_direct_operator_reply,
        build_chat_turn_event_stream=build_chat_turn_event_stream,
        execute_chat_turn=execute_chat_turn,
    )


def build_direct_chat_runtime_export_map(
    *,
    binding_bundle: DirectChatOperatorBindingBundle,
    preview_run_response_fn: Any,
    prefer_durable_run_handoff_fn: Any,
) -> Dict[str, Any]:
    runtime_bindings = binding_bundle.runtime_bindings
    policy_bindings = binding_bundle.policy_bindings
    entry_bindings = binding_bundle.entry_bindings
    tool_runtime_bindings = binding_bundle.tool_runtime_bindings
    entrypoint_bindings = build_direct_chat_entrypoint_bindings(
        direct_chat_runtime_services_fn=lambda: runtime_bindings.runtime_services(),
    )
    return {
        "_direct_chat_routing_policy_callbacks": policy_bindings.routing_policy_callbacks,
        "_direct_chat_tool_policy_callbacks": policy_bindings.tool_policy_callbacks,
        "_direct_tool_execution_callbacks": policy_bindings.direct_tool_execution_callbacks,
        "_build_direct_chat_system_prompt": entry_bindings.build_direct_chat_system_prompt,
        "_direct_chat_runtime_available": entry_bindings.direct_chat_runtime_available,
        "_resolve_direct_chat_availability": entry_bindings.resolve_direct_chat_availability,
        "_connected_provider_tokens": entry_bindings.connected_provider_tokens,
        "_resolve_provider_for_direct_chat_message": entry_bindings.resolve_provider_for_direct_chat_message,
        "_plan_direct_chat_route": entry_bindings.plan_direct_chat_route,
        "_credential_auth_mode": entry_bindings.credential_auth_mode,
        "_supports_direct_message_native_chat": entry_bindings.supports_direct_message_native_chat,
        "_preferred_provider": entry_bindings.preferred_provider,
        "_provider_unavailable_response": entry_bindings.provider_unavailable_response,
        "_direct_chat_credentials": entry_bindings.direct_chat_credentials,
        "_direct_chat_error_reply": entry_bindings.direct_chat_error_reply,
        "_direct_tool_step_payload": tool_runtime_bindings.direct_tool_step_payload,
        "_no_provider_execution_services": tool_runtime_bindings.no_provider_execution_services,
        "_build_direct_tool_approval_response": tool_runtime_bindings.build_direct_tool_approval_response,
        "_message_has_obvious_direct_tool_intent": tool_runtime_bindings.message_has_obvious_direct_tool_intent,
        "_execute_single_direct_tool_call": tool_runtime_bindings.execute_single_direct_tool_call,
        "_execute_direct_tool_calls": tool_runtime_bindings.execute_direct_tool_calls,
        "_direct_chat_callback_facade_inputs": runtime_bindings.callback_facade_inputs,
        "_direct_chat_generation_services": runtime_bindings.generation_services,
        "_direct_chat_runtime_facade_callbacks": runtime_bindings.runtime_facade_callbacks,
        "_prepare_direct_chat_request": runtime_bindings.prepare_request,
        "_direct_chat_response_services": runtime_bindings.response_services,
        "_direct_chat_runtime_services": runtime_bindings.runtime_services,
        "_preview_run_response": preview_run_response_fn,
        "_prefer_durable_run_handoff": prefer_durable_run_handoff_fn,
        "build_direct_operator_reply": entrypoint_bindings.build_direct_operator_reply,
        "collect_direct_operator_reply": entrypoint_bindings.collect_direct_operator_reply,
        "build_chat_turn_event_stream": entrypoint_bindings.build_chat_turn_event_stream,
        "execute_chat_turn": entrypoint_bindings.execute_chat_turn,
    }


def build_direct_chat_shell_export_map(
    *,
    shell_bindings: DirectChatOperatorShellBindings,
) -> Dict[str, Any]:
    state_bindings = shell_bindings.state_bindings
    availability_bindings = shell_bindings.availability_bindings
    handoff_bindings = shell_bindings.handoff_bindings
    tool_routing_bindings = shell_bindings.tool_routing_bindings
    tool_support_bindings = shell_bindings.tool_support_bindings
    return {
        "_compact_text": shell_bindings.compact_text,
        "_normalize_tool_capabilities": shell_bindings.normalize_tool_capabilities,
        "_tool_capability": shell_bindings.tool_capability,
        "_tool_connected": shell_bindings.tool_connected,
        "_tool_runtime_usable": shell_bindings.tool_runtime_usable,
        "_local_worker_available": shell_bindings.local_worker_available,
        "_direct_chat_state_bindings": state_bindings,
        "_agent_machine_owner_user_id": state_bindings.agent_machine_owner_user_id,
        "_agent_machine_full_trust_for_session": state_bindings.agent_machine_full_trust_for_session,
        "_availability_lines": state_bindings.availability_lines,
        "_connected_system_labels": state_bindings.connected_system_labels,
        "_context_tool_capabilities": state_bindings.context_tool_capabilities,
        "_normalize_prior_messages": state_bindings.normalize_prior_messages,
        "_direct_tool_session_key": state_bindings.direct_tool_session_key,
        "_direct_chat_session_key": state_bindings.direct_chat_session_key,
        "_parse_slash_command": state_bindings.parse_slash_command,
        "_session_model_preference": state_bindings.session_model_preference,
        "_set_session_model_preference": state_bindings.set_session_model_preference,
        "_mark_thread_cleared": state_bindings.mark_thread_cleared,
        "_consume_thread_cleared": state_bindings.consume_thread_cleared,
        "_active_run_count": state_bindings.active_run_count,
        "_slash_command_help_text": state_bindings.slash_command_help_text,
        "_tool_call_signature": state_bindings.tool_call_signature,
        "_record_direct_tool_signature": state_bindings.record_direct_tool_signature,
        "_clear_direct_tool_loop_state": state_bindings.clear_direct_tool_loop_state,
        "_direct_chat_memory_context_message": state_bindings.direct_chat_memory_context_message,
        "_direct_chat_workspace_context_text": state_bindings.direct_chat_workspace_context_text,
        "_build_direct_chat_daily_log_summary": state_bindings.build_direct_chat_daily_log_summary,
        "_persist_direct_chat_memory_best_effort": state_bindings.persist_direct_chat_memory_best_effort,
        "_persist_direct_chat_transcript_best_effort": state_bindings.persist_direct_chat_transcript_best_effort,
        "_persist_direct_chat_hosted_usage_best_effort": state_bindings.persist_direct_chat_hosted_usage_best_effort,
        "_build_context_used": state_bindings.build_context_used,
        "_with_context_used": state_bindings.with_context_used,
        "_connect_action": shell_bindings.connect_action,
        "_open_action": shell_bindings.open_action,
        "_google_repair_action": shell_bindings.google_repair_action,
        "_run_action": shell_bindings.run_action,
        "_workflow_action": shell_bindings.workflow_action,
        "_direct_chat_availability_bindings": availability_bindings,
        "_question_like": availability_bindings.question_like,
        "_mentions_any": availability_bindings.mentions_any,
        "_starts_like_direct_run": availability_bindings.starts_like_direct_run,
        "_is_obvious_telegram_write_request": availability_bindings.is_obvious_telegram_write_request,
        "_is_obvious_google_write_request": availability_bindings.is_obvious_google_write_request,
        "_is_obvious_smtp_write_request": availability_bindings.is_obvious_smtp_write_request,
        "_connector_write_preview_allowed": availability_bindings.connector_write_preview_allowed,
        "_is_explicit_workflow_request": availability_bindings.is_explicit_workflow_request,
        "_no_ai_chat_response": availability_bindings.no_ai_chat_response,
        "_suggest_actions": availability_bindings.suggest_actions,
        "_heartbeat_pending_tasks_for_suggestions": availability_bindings.heartbeat_pending_tasks_for_suggestions,
        "_recent_run_prompts_for_suggestions": availability_bindings.recent_run_prompts_for_suggestions,
        "_build_proactive_suggestions": availability_bindings.build_proactive_suggestions,
        "_action_marker_count": shell_bindings.action_marker_count,
        "_path_like_reference_count": shell_bindings.path_like_reference_count,
        "_direct_chat_handoff_bindings": handoff_bindings,
        "_durable_run_preferred_response": handoff_bindings.durable_run_preferred_response,
        "_run_handoff_execution_target": handoff_bindings.run_handoff_execution_target,
        "_can_auto_start_run_handoff": handoff_bindings.can_auto_start_run_handoff,
        "_direct_chat_run_handoff_failure_payload": handoff_bindings.direct_chat_run_handoff_failure_payload,
        "_start_direct_chat_run_handoff": handoff_bindings.start_direct_chat_run_handoff,
        "_direct_chat_run_handoff_reply": handoff_bindings.direct_chat_run_handoff_reply,
        "_direct_chat_run_actions": handoff_bindings.direct_chat_run_actions,
        "_direct_chat_run_snapshot": handoff_bindings.direct_chat_run_snapshot,
        "_direct_chat_run_event_to_step": handoff_bindings.direct_chat_run_event_to_step,
        "_direct_chat_run_snapshot_to_step": handoff_bindings.direct_chat_run_snapshot_to_step,
        "_direct_chat_run_final_payload": handoff_bindings.direct_chat_run_final_payload,
        "_stream_direct_chat_run_handoff": handoff_bindings.stream_direct_chat_run_handoff,
        "_provider_supports_direct_tool_calls": shell_bindings.provider_supports_direct_tool_calls,
        "_build_local_direct_chat_tools": shell_bindings.build_local_direct_chat_tools,
        "_build_direct_chat_tools": shell_bindings.build_direct_chat_tools,
        "_build_builtin_direct_chat_tools": shell_bindings.build_builtin_direct_chat_tools,
        "_build_always_on_direct_chat_tools": shell_bindings.build_always_on_direct_chat_tools,
        "_build_registry_entries": shell_bindings.build_registry_entries,
        "registered_direct_chat_tool_names_for_logging": shell_bindings.registered_direct_chat_tool_names_for_logging,
        "_direct_chat_tool_routing_bindings": tool_routing_bindings,
        "_message_requests_http_request_tool": tool_routing_bindings.message_requests_http_request_tool,
        "_message_requests_image_generation_tool": tool_routing_bindings.message_requests_image_generation_tool,
        "_message_requests_browser_tool": tool_routing_bindings.message_requests_browser_tool,
        "_message_can_use_direct_connector_tools": tool_routing_bindings.message_can_use_direct_connector_tools,
        "_looks_like_local_path_request": tool_routing_bindings.looks_like_local_path_request,
        "_message_requests_local_file_tool": tool_routing_bindings.message_requests_local_file_tool,
        "_message_requests_local_shell_tool": tool_routing_bindings.message_requests_local_shell_tool,
        "_message_requests_local_screenshot_tool": tool_routing_bindings.message_requests_local_screenshot_tool,
        "_message_requests_local_computer_tool": tool_routing_bindings.message_requests_local_computer_tool,
        "_message_can_use_direct_local_tools": tool_routing_bindings.message_can_use_direct_local_tools,
        "_message_can_use_builtin_direct_tools": tool_routing_bindings.message_can_use_builtin_direct_tools,

        "_direct_chat_tool_support_bindings": tool_support_bindings,
        "_parse_tool_name": tool_support_bindings.parse_tool_name,
        "_tool_arguments_payload": tool_support_bindings.tool_arguments_payload,
        "_extract_first_email": tool_support_bindings.extract_first_email,
        "_extract_subject_text": tool_support_bindings.extract_subject_text,
        "_extract_body_text": tool_support_bindings.extract_body_text,
        "_first_non_empty_line": tool_support_bindings.first_non_empty_line,
        "_build_direct_tool_config": tool_support_bindings.build_direct_tool_config,
        "_build_direct_local_tool_config": tool_support_bindings.build_direct_local_tool_config,
        "_tool_write_action_available": tool_support_bindings.tool_write_action_available,
        "_normalize_direct_approved_action": tool_support_bindings.normalize_direct_approved_action,
        "_approved_action_to_tool_call": tool_support_bindings.approved_action_to_tool_call,
        "_run_async_tool_call": tool_support_bindings.run_async_tool_call,
        "_format_direct_tool_result": tool_support_bindings.format_direct_tool_result,
        "_format_direct_local_tool_result": tool_support_bindings.format_direct_local_tool_result,
        "_titleize_direct_step_token": tool_support_bindings.titleize_direct_step_token,
        "_compact_step_detail": tool_support_bindings.compact_step_detail,
        "_thinking_step_payload": tool_support_bindings.thinking_step_payload,
        "_extract_first_url": tool_support_bindings.extract_first_url,
        "_extract_first_path_reference": tool_support_bindings.extract_first_path_reference,
        "_resolve_chat_local_path": tool_support_bindings.resolve_chat_local_path,
        "_direct_tool_followup_message": tool_support_bindings.direct_tool_followup_message,
        "_provider_display_name": tool_support_bindings.provider_display_name,
        "_normalize_reasoning_effort": tool_support_bindings.normalize_reasoning_effort,
    }


def build_direct_chat_module_export_map_from_namespace(
    *,
    namespace: Dict[str, Any],
    agent_machine_full_trust_enabled_fn: Any,
    chat_max_iterations_default: int,
    chat_max_iterations_ceiling: int,
    compact_text_fn: Any,
    direct_chat_compaction_token_limit: int,
    execution_markers: tuple[str, ...] | list[str],
    question_openers: tuple[str, ...] | list[str],
    direct_run_openers: tuple[str, ...] | list[str],
    workflow_request_markers: tuple[str, ...] | list[str],
    google_workspace_keywords: tuple[str, ...] | list[str],
    smtp_keywords: tuple[str, ...] | list[str],
    telegram_keywords: tuple[str, ...] | list[str],
    slack_keywords: tuple[str, ...] | list[str],
    dropbox_keywords: tuple[str, ...] | list[str],
    s3_keywords: tuple[str, ...] | list[str],
    max_context_tool_actions: int,
    max_context_tool_capabilities: int,
    max_direct_chat_prior_message_chars: int,
    max_direct_chat_prior_messages: int,
    direct_chat_model_preferences: Dict[str, Dict[str, Optional[str]]],
    direct_chat_clear_markers: set[str],
    direct_tool_loop_state: Dict[str, Dict[str, Any]],
    direct_chat_loop_repeat_limit: int,
    parse_json_object_loose_fn: Any,
    generate_reply_fn: Any,
    extraction_prompt: str,
    extraction_system_prompt: str,
    save_session_transcript_fn: Any,
    system_prefix: str,
    workspace_context_dir_fn: Any,
    memory_suggestion_prompts_fn: Any,
    direct_chat_run_snapshot_fn: Any,
    direct_chat_run_event_to_step_fn: Any,
    direct_chat_run_snapshot_to_step_fn: Any,
    direct_chat_run_final_payload_fn: Any,
    live_window_seconds: float,
    poll_seconds: float,
    monotonic_fn: Any,
    sleep_fn: Any,
    parse_json_object_loose_support_fn: Any,
    direct_chat_tool_policy_callbacks_fn: Any,
    direct_chat_routing_policy_callbacks_fn: Any,
    capture_exception_fn: Any,
    generate_chat_reply_stream_with_provider_fallback_fn: Any,
    compact_conversation_history_fn: Any,
    parse_memory_write_fn: Any,
    parse_memory_read_fn: Any,
    handle_memory_request_fn: Any,
    list_memory_entries_fn: Any,
    get_memory_fn: Any,
    delete_memory_fn: Any,
    no_provider_reasoning_required_response_fn: Any,
    supported_providers: list[str],
    complex_task_sequence_markers: tuple[str, ...] | list[str],
    complex_task_outcome_markers: tuple[str, ...] | list[str],
    discord_keywords: tuple[str, ...] | list[str],
    browser_keywords: tuple[str, ...] | list[str],
    local_file_keywords: tuple[str, ...] | list[str],
    local_shell_keywords: tuple[str, ...] | list[str],
    local_screenshot_keywords: tuple[str, ...] | list[str],
    local_computer_control_keywords: tuple[str, ...] | list[str],
    web_lookup_keywords: tuple[str, ...] | list[str],
    http_request_keywords: tuple[str, ...] | list[str],
    image_generation_keywords: tuple[str, ...] | list[str],
    llm_task_keywords: tuple[str, ...] | list[str],
    llm_task_fn: Any,
    web_search_fn: Any,
    web_fetch_fn: Any,
    search_memory_notebook_fn: Any,
    get_memory_notebook_excerpt_fn: Any,
    build_operator_system_prompt_fn: Any,
    memory_tool_names: Any,
    local_worker_registry: Dict[str, Any],
    is_worker_online_fn: Any,
    resolve_workspace_tool_capabilities_fn: Any,
    build_provider_credential_candidates_fn: Any,
    normalize_auth_mode_fn: Any,
    get_claude_code_session_token_fn: Any,
    provider_has_key_fn: Any,
    chat_iteration_limit_reply_fn: Any,
    safe_positive_int_fn: Any,
) -> Dict[str, Any]:
    shell_bindings = build_direct_chat_shell_bindings(
        agent_machine_full_trust_enabled_fn=agent_machine_full_trust_enabled_fn,
        chat_max_iterations_default=chat_max_iterations_default,
        chat_max_iterations_ceiling=chat_max_iterations_ceiling,
        compact_text_fn=compact_text_fn,
        direct_chat_compaction_token_limit=direct_chat_compaction_token_limit,
        execution_markers=execution_markers,
        question_openers=question_openers,
        direct_run_openers=direct_run_openers,
        workflow_request_markers=workflow_request_markers,
        google_workspace_keywords=google_workspace_keywords,
        smtp_keywords=smtp_keywords,
        telegram_keywords=telegram_keywords,
        slack_keywords=slack_keywords,
        dropbox_keywords=dropbox_keywords,
        s3_keywords=s3_keywords,
        max_context_tool_actions=max_context_tool_actions,
        max_context_tool_capabilities=max_context_tool_capabilities,
        max_direct_chat_prior_message_chars=max_direct_chat_prior_message_chars,
        max_direct_chat_prior_messages=max_direct_chat_prior_messages,
        direct_chat_model_preferences=direct_chat_model_preferences,
        direct_chat_clear_markers=direct_chat_clear_markers,
        direct_tool_loop_state=direct_tool_loop_state,
        direct_chat_loop_repeat_limit=direct_chat_loop_repeat_limit,
        parse_json_object_loose_fn=parse_json_object_loose_fn,
        generate_reply_fn=generate_reply_fn,
        extraction_prompt=extraction_prompt,
        extraction_system_prompt=extraction_system_prompt,
        save_session_transcript_fn=save_session_transcript_fn,
        system_prefix=system_prefix,
        workspace_context_dir_fn=workspace_context_dir_fn,
        memory_suggestion_prompts_fn=memory_suggestion_prompts_fn,
        run_handoff_execution_target_fn=lambda run_id: None,
        direct_chat_run_snapshot_fn=direct_chat_run_snapshot_fn,
        direct_chat_run_event_to_step_fn=direct_chat_run_event_to_step_fn,
        direct_chat_run_snapshot_to_step_fn=direct_chat_run_snapshot_to_step_fn,
        direct_chat_run_final_payload_fn=direct_chat_run_final_payload_fn,
        live_window_seconds=live_window_seconds,
        poll_seconds=poll_seconds,
        monotonic_fn=monotonic_fn,
        sleep_fn=sleep_fn,
        parse_json_object_loose_support_fn=parse_json_object_loose_support_fn,
        direct_chat_tool_policy_callbacks_fn=direct_chat_tool_policy_callbacks_fn,
        direct_chat_routing_policy_callbacks_fn=direct_chat_routing_policy_callbacks_fn,
    )
    shell_export_map = build_direct_chat_shell_export_map(shell_bindings=shell_bindings)
    namespace.update(shell_export_map)
    binding_bundle = build_direct_chat_operator_binding_bundle_from_namespace(
        namespace=namespace,
        parse_json_object_loose_fn=parse_json_object_loose_fn,
        capture_exception_fn=capture_exception_fn,
        generate_chat_reply_stream_with_provider_fallback_fn=generate_chat_reply_stream_with_provider_fallback_fn,
        compact_conversation_history_fn=compact_conversation_history_fn,
        parse_memory_write_fn=parse_memory_write_fn,
        parse_memory_read_fn=parse_memory_read_fn,
        handle_memory_request_fn=handle_memory_request_fn,
        list_memory_entries_fn=list_memory_entries_fn,
        get_memory_fn=get_memory_fn,
        delete_memory_fn=delete_memory_fn,
        no_provider_reasoning_required_response_fn=no_provider_reasoning_required_response_fn,
        supported_providers=supported_providers,
        direct_chat_compaction_token_limit=direct_chat_compaction_token_limit,
        complex_task_sequence_markers=complex_task_sequence_markers,
        complex_task_outcome_markers=complex_task_outcome_markers,
        execution_markers=execution_markers,
        google_workspace_keywords=google_workspace_keywords,
        smtp_keywords=smtp_keywords,
        telegram_keywords=telegram_keywords,
        slack_keywords=slack_keywords,
        discord_keywords=discord_keywords,
        dropbox_keywords=dropbox_keywords,
        s3_keywords=s3_keywords,
        browser_keywords=browser_keywords,
        local_file_keywords=local_file_keywords,
        local_shell_keywords=local_shell_keywords,
        local_screenshot_keywords=local_screenshot_keywords,
        local_computer_control_keywords=local_computer_control_keywords,
        web_lookup_keywords=web_lookup_keywords,
        http_request_keywords=http_request_keywords,
        image_generation_keywords=image_generation_keywords,
        llm_task_keywords=llm_task_keywords,
        llm_task_fn=llm_task_fn,
        web_search_fn=web_search_fn,
        web_fetch_fn=web_fetch_fn,
        search_memory_notebook_fn=search_memory_notebook_fn,
        get_memory_notebook_excerpt_fn=get_memory_notebook_excerpt_fn,
        build_operator_system_prompt_fn=build_operator_system_prompt_fn,
        memory_tool_names=memory_tool_names,
        local_worker_registry=local_worker_registry,
        is_worker_online_fn=is_worker_online_fn,
        resolve_workspace_tool_capabilities_fn=resolve_workspace_tool_capabilities_fn,
        build_provider_credential_candidates_fn=build_provider_credential_candidates_fn,
        normalize_auth_mode_fn=normalize_auth_mode_fn,
        get_claude_code_session_token_fn=get_claude_code_session_token_fn,
        provider_has_key_fn=provider_has_key_fn,
        connect_action_fn=shell_bindings.connect_action,
        chat_iteration_limit_reply_fn=chat_iteration_limit_reply_fn,
        safe_positive_int_fn=safe_positive_int_fn,
        chat_max_iterations_default=chat_max_iterations_default,
    )
    runtime_export_map = build_direct_chat_runtime_export_map(
        binding_bundle=binding_bundle,
        preview_run_response_fn=shell_bindings.tool_routing_bindings.preview_run_response,
        prefer_durable_run_handoff_fn=shell_bindings.tool_routing_bindings.prefer_durable_run_handoff,
    )
    namespace.update(runtime_export_map)
    return {
        "_direct_chat_shell_bindings": shell_bindings,
        "_direct_chat_binding_bundle": binding_bundle,
        **shell_export_map,
        **runtime_export_map,
    }


def build_direct_chat_tool_support_bindings(
    *,
    parse_json_object_loose_fn: Any,
) -> DirectChatOperatorToolSupportBindings:
    def build_direct_tool_config(connector_id, action_id, tool_input):
        return skills_service.build_direct_tool_config(
            connector_id,
            action_id,
            tool_input,
            parse_json_object_loose=parse_json_object_loose_fn,
        )

    def approved_action_to_tool_call(approved_action):
        return skills_service.approved_action_to_tool_call(
            approved_action,
            parse_json_object_loose=parse_json_object_loose_fn,
        )

    def tool_arguments_payload_wrapper(arguments):
        return tool_arguments_payload(
            arguments,
            parse_json_object_loose_fn=parse_json_object_loose_fn,
        )

    return DirectChatOperatorToolSupportBindings(
        parse_tool_name=parse_tool_name,
        tool_arguments_payload=tool_arguments_payload_wrapper,
        extract_first_email=skills_service.extract_first_email,
        extract_subject_text=skills_service.extract_subject_text,
        extract_body_text=skills_service.extract_body_text,
        first_non_empty_line=skills_service.first_non_empty_line,
        build_direct_tool_config=build_direct_tool_config,
        build_direct_local_tool_config=skills_service.build_direct_local_tool_config,
        tool_write_action_available=skills_service.tool_write_action_available,
        normalize_direct_approved_action=normalize_direct_approved_action,
        approved_action_to_tool_call=approved_action_to_tool_call,
        run_async_tool_call=direct_tool_config_service.run_async_tool_call,
        format_direct_tool_result=direct_tool_config_service.format_direct_tool_result,
        format_direct_local_tool_result=direct_tool_config_service.format_direct_local_tool_result,
        titleize_direct_step_token=titleize_direct_step_token,
        compact_step_detail=compact_step_detail,
        thinking_step_payload=direct_tool_execution_service.thinking_step_payload,
        extract_first_url=direct_tool_execution_service.extract_first_url,
        extract_first_path_reference=direct_tool_execution_service.extract_first_path_reference,
        resolve_chat_local_path=direct_tool_execution_service.resolve_chat_local_path,
        direct_tool_followup_message=direct_tool_execution_service.direct_tool_followup_message,
        provider_display_name=direct_chat_provider_facade_service.provider_display_name,
        normalize_reasoning_effort=direct_chat_provider_facade_service.normalize_reasoning_effort,
    )
