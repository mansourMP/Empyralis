import unittest

from server_modules import direct_chat_generation_service
from server_modules import direct_chat_runtime_facade_service as service


def _callbacks() -> service.DirectChatRuntimeFacadeCallbacks:
    return service.DirectChatRuntimeFacadeCallbacks(
        compact_text=lambda value: str(value or "").strip().lower(),
        safe_positive_int=lambda value, default=0: default,
        resolve_chat_local_path=lambda path: path,
        extract_first_path_reference=lambda message: "/tmp/demo.txt" if "/tmp/demo.txt" in str(message or "") else "",
        extract_first_url=lambda message: "https://example.com" if "example.com" in str(message or "") else "",
        parse_page_state=lambda payload: payload,
        parse_memory_write=lambda message: None,
        parse_memory_read=lambda message: None,
        handle_memory_request=lambda workspace_id, message: None,
        parse_tool_name=lambda name: tuple(str(name or "").split("__", 1)) if "__" in str(name or "") else ("", ""),
        tool_arguments_payload=lambda arguments: arguments if isinstance(arguments, dict) else {},
        approval_required_for_direct_tool=lambda connector, action, arguments, capabilities: connector == "telegram_bot",
        agent_machine_full_trust_for_session=lambda session_ctx: False,
        execute_single_direct_tool_call=lambda **kwargs: "tool reply",
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
        build_proactive_suggestions=lambda message: [],
        direct_tool_session_key=lambda workspace_id, thread_id: f"tool:{workspace_id}:{thread_id}",
        resolve_direct_chat_availability=lambda availability: dict(availability or {}),
        connected_system_labels=lambda capabilities: [],
        context_tool_capabilities=lambda workspace_id: [],
        build_direct_chat_tools=lambda **kwargs: [],
        build_local_direct_chat_tools=lambda **kwargs: [],
        build_builtin_direct_chat_tools=lambda **kwargs: [],
        build_always_on_direct_chat_tools=lambda: [],
        build_registry_entries=lambda tool_capabilities, availability_payload: [],
        normalize_direct_approved_action=lambda action: action,
        build_context_used=lambda **kwargs: kwargs,
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
        capture_exception=lambda exc: None,
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
        direct_chat_generation_services=direct_chat_generation_service.DirectChatGenerationServices(
            thinking_step_payload=lambda iteration, status, detail=None: {"type": "step"},
            build_context_used=lambda **kwargs: kwargs,
            build_direct_tool_approval_response=lambda **kwargs: None,
            parse_tool_name=lambda name: ("", ""),
            tool_arguments_payload=lambda arguments: {},
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
        ),
        no_provider_reasoning_required_response=lambda: {"reply": "reasoning required", "actions": [], "mode": "answer"},
    )


class DirectChatRuntimeFacadeServiceTests(unittest.TestCase):
    def test_build_no_provider_execution_services_preserves_operator_callbacks(self) -> None:
        callbacks = _callbacks()

        services = service.build_no_provider_execution_services(callbacks)

        self.assertIs(services.execute_single_tool_call, callbacks.execute_single_direct_tool_call)
        self.assertIs(services.extract_first_url, callbacks.extract_first_url)
        self.assertIs(services.handle_memory_request, callbacks.handle_memory_request)

    def test_build_direct_tool_approval_response_uses_no_provider_bundle(self) -> None:
        payload = service.build_direct_tool_approval_response(
            tool_calls=[{"name": "telegram_bot__send_message", "arguments": {"input": "hello"}}],
            tool_capabilities=[{"id": "telegram_bot", "approval_required_actions": ["send_message"]}],
            callbacks=_callbacks(),
        )

        self.assertIsNotNone(payload)
        self.assertEqual(payload["mode"], "answer_with_action")
        self.assertEqual(payload["actions"][0]["connector"], "telegram_bot")

    def test_build_direct_chat_runtime_services_preserves_message_and_tool_callbacks(self) -> None:
        callbacks = _callbacks()

        runtime_services = service.build_direct_chat_runtime_services(callbacks)

        self.assertIs(runtime_services.direct_chat_response_services.execute_direct_tool_calls, callbacks.execute_direct_tool_calls)
        self.assertIs(runtime_services.no_provider_execution_services.execute_single_tool_call, callbacks.execute_single_direct_tool_call)
        self.assertTrue(runtime_services.message_has_obvious_direct_tool_intent("Open /tmp/demo.txt", []))


if __name__ == "__main__":
    unittest.main()
