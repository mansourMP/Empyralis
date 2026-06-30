import unittest

from server_modules import direct_chat_callback_facade_service as service


def _inputs() -> service.DirectChatCallbackFacadeInputs:
    return service.DirectChatCallbackFacadeInputs(
        thinking_step_payload=lambda *args, **kwargs: {"type": "step"},
        build_context_used=lambda **kwargs: kwargs,
        build_direct_tool_approval_response=lambda **kwargs: None,
        parse_tool_name=lambda name: ("connector", "action"),
        tool_arguments_payload=lambda arguments: arguments if isinstance(arguments, dict) else {},
        parse_page_state=lambda payload: payload,
        direct_tool_step_payload=lambda *args, **kwargs: {"type": "step"},
        execute_single_direct_tool_call=lambda **kwargs: "tool reply",
        direct_tool_followup_message=lambda tool_name, result_text: result_text,
        suggest_actions=lambda message, availability: [],
        clear_direct_tool_loop_state=lambda session_key: None,
        persist_direct_chat_memory_best_effort=lambda **kwargs: None,
        persist_direct_chat_transcript_best_effort=lambda **kwargs: None,
        persist_direct_chat_hosted_usage_best_effort=lambda **kwargs: None,
        record_direct_tool_signature=lambda session_key, tool_call: False,
        direct_chat_error_reply=lambda error: error,
        capture_exception=lambda exc: None,
        generate_chat_reply_stream_with_provider_fallback=lambda **kwargs: iter(()),
        compact_text=lambda value: str(value or "").strip().lower(),
        safe_positive_int=lambda value, default=0: default,
        resolve_chat_local_path=lambda path: path,
        extract_first_path_reference=lambda value: "",
        extract_first_url=lambda value: "",
        parse_memory_write=lambda value: None,
        parse_memory_read=lambda value: None,
        handle_memory_request=lambda workspace_id, message: None,
        approval_required_for_direct_tool=lambda connector_id, action_id, arguments, tool_capabilities: False,
        agent_machine_full_trust_for_session=lambda session_ctx: False,
        direct_chat_session_key=lambda workspace_id, thread_id: f"{workspace_id}:{thread_id}",
        resolved_chat_iteration_limit=lambda value: 3,
        session_model_preference=lambda workspace_id, thread_id: "",
        normalize_reasoning_effort=lambda value: str(value or "").strip().lower() or None,
        parse_slash_command=lambda message: ("", ""),
        set_session_model_preference=lambda workspace_id, thread_id, model: None,
        mark_thread_cleared=lambda workspace_id, thread_id: None,
        normalize_prior_messages=lambda prior_messages: list(prior_messages or []),
        consume_thread_cleared=lambda workspace_id, thread_id: False,
        compact_conversation_history=lambda **kwargs: {"messages": [], "history_mode": "none", "prior_messages_used": False},
        build_proactive_suggestions=lambda workspace_id: [],
        direct_tool_session_key=lambda workspace_id, thread_id: f"tool:{workspace_id}:{thread_id}",
        resolve_direct_chat_availability=lambda availability: dict(availability or {}),
        connected_system_labels=lambda capabilities: [],
        context_tool_capabilities=lambda workspace_id: [],
        build_direct_chat_tools=lambda **kwargs: [],
        build_local_direct_chat_tools=lambda **kwargs: [],
        build_builtin_direct_chat_tools=lambda **kwargs: [],
        build_always_on_direct_chat_tools=lambda: [],
        build_registry_entries=lambda tool_capabilities, availability_payload: [],
        normalize_direct_approved_action=lambda value: value,
        direct_chat_compaction_token_limit=12000,
        with_context_used=lambda payload, context: {**payload, "context_used": context},
        connected_provider_tokens=lambda workspace_id: ["openai"],
        list_memory_entries=lambda workspace_id: [],
        active_run_count=lambda workspace_id: 0,
        get_memory=lambda workspace_id: "",
        delete_memory=lambda workspace_id, key: False,
        slash_command_help_text=lambda: "help text",
        execute_direct_tool_calls=lambda **kwargs: "tool reply",
        direct_chat_credentials=lambda workspace_id, provider: {"api_key": "sk-test"},
        tool_write_action_available=lambda connector, action, capabilities: True,
        approved_action_to_tool_call=lambda approved_action: approved_action,
        resolve_provider_for_direct_chat_message=lambda workspace_id, requested_provider, message: ("openai", {}),
        plan_direct_chat_route=lambda **kwargs: None,
        start_direct_chat_run_handoff=lambda **kwargs: {"run_id": "run-1"},
        direct_chat_run_handoff_reply=lambda started: {"reply": "handoff"},
        stream_direct_chat_run_handoff=lambda **kwargs: iter(()),
        direct_chat_run_handoff_failure_payload=lambda message, detail: {"reply": detail},
        supports_direct_message_native_chat=lambda provider, credentials: True,
        supported_providers=["openai"],
        build_direct_chat_system_prompt=lambda **kwargs: "system prompt",
        direct_chat_workspace_context_text=lambda workspace_id: "",
        no_provider_reasoning_required_response=lambda: {"reply": "fallback", "actions": [], "mode": "answer"},
    )


class DirectChatCallbackFacadeServiceTests(unittest.TestCase):
    def test_build_direct_chat_generation_services_preserves_generation_callbacks(self) -> None:
        inputs = _inputs()

        services = service.build_direct_chat_generation_services(inputs)

        self.assertIs(services.execute_single_direct_tool_call, inputs.execute_single_direct_tool_call)
        self.assertIs(services.build_direct_tool_approval_response, inputs.build_direct_tool_approval_response)
        self.assertIs(services.generate_chat_reply_stream_with_provider_fallback, inputs.generate_chat_reply_stream_with_provider_fallback)
        self.assertIs(services.persist_direct_chat_hosted_usage_best_effort, inputs.persist_direct_chat_hosted_usage_best_effort)

    def test_build_direct_chat_runtime_facade_callbacks_embeds_generation_services(self) -> None:
        inputs = _inputs()

        callbacks = service.build_direct_chat_runtime_facade_callbacks(inputs)

        self.assertIs(callbacks.execute_single_direct_tool_call, inputs.execute_single_direct_tool_call)
        self.assertIs(callbacks.direct_chat_credentials, inputs.direct_chat_credentials)
        self.assertIs(callbacks.direct_chat_generation_services.execute_single_direct_tool_call, inputs.execute_single_direct_tool_call)
        self.assertIs(callbacks.direct_chat_generation_services.suggest_actions, inputs.suggest_actions)


if __name__ == "__main__":
    unittest.main()
