import unittest
from pathlib import Path
from unittest import mock

from server_modules import direct_chat_operator_binding_service as service
from server_modules import direct_chat_routing_service
from server_modules import direct_chat_tool_catalog_service


def _operator_namespace() -> dict:
    return {
        "_compact_text": lambda value: str(value or "").strip().lower(),
        "_mentions_any": lambda message, markers: False,
        "_question_like": lambda message: False,
        "_is_explicit_workflow_request": lambda message: False,
        "_starts_like_direct_run": lambda message: True,
        "_workflow_action": lambda message: {"kind": "workflow"},
        "_run_action": lambda message: {"kind": "run"},
        "_message_requests_local_file_tool": lambda message: False,
        "_message_requests_local_shell_tool": lambda message: False,
        "_message_requests_local_screenshot_tool": lambda message: False,
        "_message_requests_local_computer_tool": lambda message: False,
        "_extract_first_path_reference": lambda value: "",
        "_extract_first_url": lambda value: "",
        "_provider_supports_direct_tool_calls": lambda provider: True,
        "_is_obvious_smtp_write_request": lambda message: False,
        "_thinking_step_payload": lambda *args, **kwargs: {"type": "step"},
        "_build_context_used": lambda **kwargs: kwargs,
        "_build_direct_tool_approval_response": lambda **kwargs: None,
        "_parse_tool_name": lambda name: ("connector", "action"),
        "_tool_arguments_payload": lambda arguments: arguments if isinstance(arguments, dict) else {},
        "_direct_tool_step_payload": lambda *args, **kwargs: {"type": "step"},
        "_execute_single_direct_tool_call": lambda **kwargs: "tool reply",
        "_direct_tool_followup_message": lambda tool_name, result_text: result_text,
        "_suggest_actions": lambda message, availability: [],
        "_clear_direct_tool_loop_state": lambda session_key: None,
        "_persist_direct_chat_memory_best_effort": lambda **kwargs: None,
        "_persist_direct_chat_transcript_best_effort": lambda **kwargs: None,
        "_persist_direct_chat_hosted_usage_best_effort": lambda **kwargs: None,
        "_record_direct_tool_signature": lambda session_key, tool_call: False,
        "_direct_chat_error_reply": lambda error: error,
        "_safe_positive_int": lambda value, default=0: default,
        "_resolve_chat_local_path": lambda path: path,
        "_approval_required_for_direct_tool": lambda connector_id, action_id, arguments, tool_capabilities: False,
        "_agent_machine_full_trust_for_session": lambda session_ctx: False,
        "_direct_chat_session_key": lambda workspace_id, thread_id: f"{workspace_id}:{thread_id}",
        "_resolved_chat_iteration_limit": lambda value=None: 3,
        "_session_model_preference": lambda session_key: {"provider": None, "model": None},
        "_normalize_reasoning_effort": lambda value="": str(value or "").strip().lower() or None,
        "_parse_slash_command": lambda message: {},
        "_set_session_model_preference": lambda session_key, provider=None, model=None: None,
        "_mark_thread_cleared": lambda session_key: None,
        "_normalize_prior_messages": lambda prior_messages: list(prior_messages or []),
        "_consume_thread_cleared": lambda session_key: False,
        "_build_proactive_suggestions": lambda workspace_id: [],
        "_direct_tool_session_key": lambda workspace_id, thread_id: f"tool:{workspace_id}:{thread_id}",
        "_resolve_direct_chat_availability": lambda workspace_id, requested_provider="", availability_override=None: {},
        "_connected_system_labels": lambda availability: [],
        "_context_tool_capabilities": lambda availability: [],
        "_build_direct_chat_tools": lambda tool_capabilities: [],
        "_build_local_direct_chat_tools": lambda availability: [],
        "_build_builtin_direct_chat_tools": lambda: [],
        "_normalize_direct_approved_action": lambda value: value,
        "_with_context_used": lambda payload, context: {**payload, "context_used": context},
        "_connected_provider_tokens": lambda workspace_id: ["openai"],
        "_active_run_count": lambda workspace_id: 0,
        "_slash_command_help_text": lambda: "help text",
        "_execute_direct_tool_calls": lambda **kwargs: "tool reply",
        "_direct_chat_credentials": lambda workspace_id, provider: {"api_key": "sk-test"},
        "_tool_write_action_available": lambda connector, action, capabilities: True,
        "_approved_action_to_tool_call": lambda approved_action: approved_action,
        "_resolve_provider_for_direct_chat_message": lambda workspace_id, requested_provider, message, **kwargs: ("openai", {}),
        "_plan_direct_chat_route": lambda **kwargs: None,
        "_start_direct_chat_run_handoff": lambda **kwargs: {"run_id": "run-1"},
        "_direct_chat_run_handoff_reply": lambda started: {"reply": "handoff"},
        "_stream_direct_chat_run_handoff": lambda **kwargs: iter(()),
        "_direct_chat_run_handoff_failure_payload": lambda message, detail: {"reply": detail},
        "_supports_direct_message_native_chat": lambda provider, credentials: True,
        "_build_direct_chat_system_prompt": lambda **kwargs: "system prompt",
        "_direct_chat_workspace_context_text": lambda workspace_id, memory_query="": "",
        "_compact_step_detail": lambda value, limit=120: "detail",
        "_titleize_direct_step_token": lambda value: "Title",
        "_run_async_tool_call": lambda coro: None,
        "_build_direct_local_tool_config": lambda connector_id, action_id, arguments: ("computer", arguments),
        "_format_direct_local_tool_result": lambda result: "local result",
        "_build_direct_tool_config": lambda connector_id, action_id, tool_input: {"value": tool_input},
        "_format_direct_tool_result": lambda result: "result",
    }


class DirectChatOperatorBindingServiceTests(unittest.TestCase):
    def test_parse_tool_name_handles_builtin_and_connector_tools(self) -> None:
        self.assertEqual(service.parse_tool_name("memory_search"), ("memory", "search"))
        self.assertEqual(service.parse_tool_name("memory_update"), ("memory", "update"))
        self.assertEqual(service.parse_tool_name("memory_stage_edit"), ("memory", "stage_edit"))
        self.assertEqual(service.parse_tool_name("memory_apply_edit"), ("memory", "apply_edit"))
        self.assertEqual(service.parse_tool_name("memory_append_daily_note"), ("memory", "append_daily_note"))
        self.assertEqual(service.parse_tool_name("memory_stage_consolidation"), ("memory", "stage_consolidation"))
        self.assertEqual(service.parse_tool_name("memory_consolidate_daily_notes"), ("memory", "consolidate_daily_notes"))
        self.assertEqual(service.parse_tool_name("memory_list_versions"), ("memory", "list_versions"))
        self.assertEqual(service.parse_tool_name("memory_rollback_version"), ("memory", "rollback_version"))
        self.assertEqual(service.parse_tool_name("telegram__send"), ("telegram", "send"))

    def test_build_direct_tool_execution_callbacks_reads_underscored_namespace(self) -> None:
        callbacks = service.build_direct_tool_execution_callbacks(
            namespace=_operator_namespace(),
            parse_json_object_loose=lambda value: {},
            llm_task=lambda **kwargs: None,
            web_search=lambda **kwargs: None,
            web_fetch=lambda **kwargs: None,
            search_memory_notebook=lambda *args, **kwargs: [],
            get_memory_notebook_excerpt=lambda *args, **kwargs: "",
        )

        self.assertEqual(callbacks.titleize_direct_step_token("send"), "Title")
        self.assertEqual(callbacks.compact_step_detail("value"), "detail")

    def test_build_direct_chat_callback_facade_inputs_reads_underscored_namespace(self) -> None:
        namespace = _operator_namespace()

        inputs = service.build_direct_chat_callback_facade_inputs(
            namespace=namespace,
            parse_page_state=lambda payload: payload,
            capture_exception=lambda exc: None,
            generate_chat_reply_stream_with_provider_fallback=lambda **kwargs: iter(()),
            compact_conversation_history=lambda **kwargs: {"messages": [], "history_mode": "none", "prior_messages_used": False},
            parse_memory_write=lambda value: None,
            parse_memory_read=lambda value: None,
            handle_memory_request=lambda workspace_id, message: None,
            list_memory_entries=lambda workspace_id: [],
            get_memory=lambda workspace_id: "",
            delete_memory=lambda workspace_id, key: False,
            no_provider_reasoning_required_response=lambda: {"reply": "fallback", "actions": [], "mode": "answer"},
            supported_providers=["openai"],
            direct_chat_compaction_token_limit=12000,
        )

        self.assertIs(inputs.execute_single_direct_tool_call, namespace["_execute_single_direct_tool_call"])
        self.assertIs(inputs.build_context_used, namespace["_build_context_used"])
        self.assertEqual(inputs.supported_providers, ["openai"])

    def test_build_direct_chat_runtime_bindings_reads_underscored_namespace(self) -> None:
        namespace = _operator_namespace()

        bindings = service.build_direct_chat_runtime_bindings(
            namespace=namespace,
            parse_page_state=lambda payload: payload,
            capture_exception=lambda exc: None,
            generate_chat_reply_stream_with_provider_fallback=lambda **kwargs: iter(()),
            compact_conversation_history=lambda **kwargs: {"messages": [], "history_mode": "none", "prior_messages_used": False},
            parse_memory_write=lambda value: None,
            parse_memory_read=lambda value: None,
            handle_memory_request=lambda workspace_id, message: None,
            list_memory_entries=lambda workspace_id: [],
            get_memory=lambda workspace_id: "",
            delete_memory=lambda workspace_id, key: False,
            no_provider_reasoning_required_response=lambda: {"reply": "fallback", "actions": [], "mode": "answer"},
            supported_providers=["openai"],
            direct_chat_compaction_token_limit=12000,
        )

        inputs = bindings.callback_facade_inputs()
        callbacks = bindings.runtime_facade_callbacks()
        services_payload = bindings.response_services()

        self.assertIs(inputs.build_context_used, namespace["_build_context_used"])
        self.assertIs(callbacks.resolve_provider_for_direct_chat_message, namespace["_resolve_provider_for_direct_chat_message"])
        self.assertIsNotNone(services_payload)

    def test_build_direct_chat_policy_bindings_reads_underscored_namespace(self) -> None:
        namespace = _operator_namespace()

        bindings = service.build_direct_chat_policy_bindings(
            namespace=namespace,
            complex_task_sequence_markers=("first",),
            complex_task_outcome_markers=("done",),
            execution_markers=("run",),
            google_workspace_keywords=("gmail",),
            smtp_keywords=("smtp",),
            telegram_keywords=("telegram",),
            slack_keywords=("slack",),
            discord_keywords=("discord",),
            dropbox_keywords=("dropbox",),
            s3_keywords=("s3",),
            browser_keywords=("browser",),
            local_file_keywords=("file",),
            local_shell_keywords=("shell",),
            local_screenshot_keywords=("screenshot",),
            local_computer_control_keywords=("click",),
            web_lookup_keywords=("latest",),
            http_request_keywords=("api",),
            image_generation_keywords=("image",),
            llm_task_keywords=("reason",),
            parse_json_object_loose=lambda value: {},
            llm_task=lambda **kwargs: None,
            web_search=lambda **kwargs: None,
            web_fetch=lambda **kwargs: None,
            search_memory_notebook=lambda *args, **kwargs: [],
            get_memory_notebook_excerpt=lambda *args, **kwargs: "",
        )

        routing_callbacks = bindings.routing_policy_callbacks()
        tool_callbacks = bindings.tool_policy_callbacks()
        execution_callbacks = bindings.direct_tool_execution_callbacks()

        self.assertEqual(routing_callbacks.compact_text(" Hello "), "hello")
        self.assertEqual(tool_callbacks.extract_first_url("value"), "")
        self.assertEqual(execution_callbacks.compact_step_detail("value"), "detail")

    def test_build_direct_chat_tool_runtime_bindings_preserves_late_bound_single_tool_call(self) -> None:
        calls = []

        def _single_tool_call(**kwargs):
            calls.append(kwargs)
            return "ok"

        bindings = service.build_direct_chat_tool_runtime_bindings(
            direct_chat_runtime_facade_callbacks=lambda: object(),
            direct_tool_execution_callbacks=lambda: object(),
            execute_single_direct_tool_call_fn=_single_tool_call,
        )

        result = bindings.execute_direct_tool_calls(
            tool_calls=[{"name": "memory_search", "arguments": {}}],
            workspace_id="default",
            thread_id="thread-1",
        )

        self.assertIsInstance(result, str)
        self.assertTrue(calls)

    def test_build_direct_chat_entry_bindings_preserves_provider_and_error_helpers(self) -> None:
        bindings = service.build_direct_chat_entry_bindings(
            availability_lines=lambda workspace_id, availability: ["AI ready"],
            build_operator_system_prompt=lambda **kwargs: "base prompt",
            memory_tool_names=("memory_search",),
            local_worker_registry={},
            is_worker_online_fn=lambda *_args, **_kwargs: True,
            preferred_provider_fn=lambda workspace_id, requested_provider="": ("openai", {"api_key": "sk-test"}),
            supports_direct_message_native_chat_fn=lambda provider, credentials: True,
            resolve_workspace_tool_capabilities_fn=lambda workspace_id: [],
            supported_providers=("openai", "anthropic"),
            direct_chat_credentials_fn=lambda workspace_id, provider: {"api_key": "sk-test"} if provider == "openai" else {},
            build_provider_credential_candidates_fn=lambda workspace_id, provider: [{"api_key": "sk-test"}] if provider == "openai" else [],
            compact_text_fn=lambda value: str(value or "").strip().lower(),
            mentions_any_fn=lambda message, markers: False,
            message_requests_local_file_tool_fn=lambda message: False,
            message_requests_local_shell_tool_fn=lambda message: False,
            message_requests_local_screenshot_tool_fn=lambda message: False,
            message_requests_local_computer_tool_fn=lambda message: False,
            is_obvious_smtp_write_request_fn=lambda message: False,
            preview_run_response_fn=lambda message, availability: None,
            prefer_durable_run_handoff_fn=lambda message, availability: False,
            durable_run_preferred_response_fn=lambda message: {"mode": "run"},
            message_can_use_direct_connector_tools_fn=lambda message, *, provider, tools: False,
            message_can_use_direct_local_tools_fn=lambda message, *, provider, tools: False,
            message_can_use_builtin_direct_tools_fn=lambda message, *, tools: False,
            can_auto_start_run_handoff_fn=lambda availability: False,
            credential_auth_mode_fn=lambda provider, credentials: "api_key",
            normalize_auth_mode_fn=lambda value="": str(value or "").strip().lower(),
            get_claude_code_session_token_fn=lambda: "",
            provider_has_key_fn=lambda provider: provider == "openai",
            connect_action_fn=lambda label, href: {"label": label, "href": href},
            chat_iteration_limit_reply_fn=lambda limit: f"limit:{limit}",
            safe_positive_int_fn=lambda value, default: int(value) if str(value or "").isdigit() else default,
            chat_max_iterations_default=30,
            google_workspace_keywords=("gmail",),
            telegram_keywords=("telegram",),
            slack_keywords=("slack",),
            discord_keywords=("discord",),
            dropbox_keywords=("dropbox",),
            s3_keywords=("s3",),
        )

        provider, credentials = bindings.preferred_provider("default", "openai")

        self.assertEqual(provider, "openai")
        self.assertEqual(credentials["api_key"], "sk-test")
        self.assertEqual(bindings.direct_chat_error_reply("max_tool_iterations_reached:7"), "limit:7")

    def test_build_direct_chat_handoff_bindings_preserves_handoff_helpers(self) -> None:
        calls = []

        def _run_action(message):
            return {"kind": "run", "message": message}

        def _open_action(label, href, **kwargs):
            return {"label": label, "href": href, **kwargs}

        def _build_context_used(**kwargs):
            calls.append(kwargs)
            return kwargs

        bindings = service.build_direct_chat_handoff_bindings(
            run_action_fn=_run_action,
            safe_positive_int_fn=lambda value, default: default,
            open_action_fn=_open_action,
            build_context_used_fn=_build_context_used,
            live_window_seconds=1.0,
            poll_seconds=0.0,
            monotonic_fn=lambda: 10.0,
            sleep_fn=lambda _seconds: None,
        )

        payload = bindings.direct_chat_run_final_payload(
            run_id="run-1",
            run={"id": "run-1"},
            snapshot={"status": "completed", "usage_masked": {}, "fallback_used": False},
            requested_workspace_id="default",
            requested_provider="openai",
            requested_model="gpt-5.4",
            reasoning_effort=None,
            connected_systems=[],
            tool_capabilities=[],
            fallback_reason=None,
        )

        preferred = bindings.durable_run_preferred_response("do it")

        self.assertEqual(preferred["mode"], "answer_with_action")
        self.assertEqual(preferred["actions"][0]["kind"], "run")
        self.assertEqual(bindings.direct_chat_run_actions("run-1")[0]["label"], "Open run")
        self.assertEqual(payload["context_used"]["run_created"], True)
        self.assertTrue(calls)

    def test_build_direct_chat_availability_bindings_preserves_availability_helpers(self) -> None:
        bindings = service.build_direct_chat_availability_bindings(
            compact_text_fn=lambda value: str(value or "").strip().lower(),
            normalize_tool_capabilities_fn=lambda availability: availability.get("tools") or [],
            tool_connected_fn=lambda availability, tool_id: False,
            tool_runtime_usable_fn=lambda availability, tool_id: True if tool_id == "google_workspace" else None,
            connect_action_fn=lambda label, href: {"kind": "connect", "label": label, "href": href},
            google_repair_action_fn=lambda: {"kind": "repair"},
            workflow_action_fn=lambda message: {"kind": "workflow", "message": message},
            run_action_fn=lambda message: {"kind": "run", "message": message},
            workspace_context_dir_fn=lambda: Path("/tmp/context"),
            memory_suggestion_prompts_fn=lambda workspace_id: [f"memory:{workspace_id}"],
            question_openers=("what", "how"),
            direct_run_openers=("run", "execute"),
            workflow_request_markers=("workflow",),
            execution_markers=("run", "execute"),
            google_workspace_keywords=("gmail",),
            smtp_keywords=("smtp",),
            telegram_keywords=("telegram",),
            slack_keywords=("slack",),
            dropbox_keywords=("dropbox",),
            s3_keywords=("s3",),
        )

        suggestions = bindings.build_proactive_suggestions("default")

        self.assertTrue(bindings.question_like("what can you do"))
        self.assertIsInstance(suggestions, list)
        self.assertTrue(suggestions)

    def test_build_direct_chat_tool_routing_bindings_preserves_tool_and_preview_helpers(self) -> None:
        tool_callbacks = direct_chat_tool_catalog_service.DirectChatToolPolicyCallbacks(
            compact_text=lambda value: " ".join(str(value or "").strip().lower().split()),
            question_like=lambda compact: compact.startswith(("what ", "why ", "how ", "can ")),
            mentions_any=lambda compact, keywords: any(token in compact for token in keywords),
            extract_first_path_reference=lambda message: "/tmp/demo.txt" if "/tmp/demo.txt" in str(message or "") else "",
            extract_first_url=lambda message: "https://example.com" if "example.com" in str(message or "") else "",
            provider_supports_direct_tool_calls=service.direct_chat_tool_catalog_service.provider_supports_direct_tool_calls,
            is_obvious_smtp_write_request=lambda compact: "email" in compact and "send" in compact,
            google_workspace_keywords=("gmail", "calendar", "drive"),
            smtp_keywords=("smtp",),
            telegram_keywords=("telegram",),
            slack_keywords=("slack",),
            discord_keywords=("discord",),
            dropbox_keywords=("dropbox",),
            s3_keywords=("s3",),
            browser_keywords=("browser", "go to", "page title"),
            local_file_keywords=("read file", "open file"),
            local_shell_keywords=("run command", "shell"),
            local_screenshot_keywords=("screenshot",),
            local_computer_control_keywords=("click screen", "ocr screen"),
            web_lookup_keywords=("search web", "look up"),
            http_request_keywords=("http", "api request"),
            image_generation_keywords=("generate image",),
            llm_task_keywords=("think deeply",),
        )
        routing_callbacks = direct_chat_routing_service.DirectChatRoutingPolicyCallbacks(
            compact_text=lambda value: str(value or "").strip().lower(),
            mentions_any=lambda text, keywords: any(keyword in text for keyword in keywords),
            question_like=lambda compact: compact.startswith(("what ", "why ", "how ", "can ")),
            is_explicit_workflow_request=lambda message: "workflow" in str(message or "").lower(),
            starts_like_direct_run=lambda compact: compact.startswith(("run ", "execute ", "send ")),
            workflow_action=lambda message: {"kind": "workflow", "message": message},
            run_action=lambda message: {"kind": "run", "message": message},
            message_requests_local_file_tool=lambda message: "/tmp/" in str(message or ""),
            message_requests_local_shell_tool=lambda message: "shell" in str(message or "").lower(),
            message_requests_local_screenshot_tool=lambda message: "screenshot" in str(message or "").lower(),
            message_requests_local_computer_tool=lambda message: "computer" in str(message or "").lower(),
            complex_task_sequence_markers=("then", "after that"),
            complex_task_outcome_markers=("end to end", "done"),
            execution_markers=("run", "execute", "send"),
            google_workspace_keywords=("gmail",),
            telegram_keywords=("telegram",),
            slack_keywords=("slack",),
            dropbox_keywords=("dropbox",),
            s3_keywords=("s3",),
        )

        bindings = service.build_direct_chat_tool_routing_bindings(
            direct_chat_tool_policy_callbacks=lambda: tool_callbacks,
            direct_chat_routing_policy_callbacks=lambda: routing_callbacks,
            compact_text_fn=lambda value: str(value or "").strip().lower(),
        )

        self.assertTrue(
            bindings.message_can_use_direct_connector_tools(
                "Send a telegram message",
                provider="codex_cli",
                tools=[{"name": "telegram_bot__send_message"}],
            )
        )
        preview = bindings.preview_run_response("Run the deployment check", {"ai_ready": True})
        self.assertIsNotNone(preview)
        self.assertEqual(preview["actions"][0]["kind"], "run")
        self.assertTrue(
            bindings.approval_required_for_direct_tool(
                "computer",
                "click",
                {},
                [],
            )
        )

    def test_build_direct_chat_state_bindings_preserves_session_and_loop_helpers(self) -> None:
        model_preferences = {}
        clear_markers = set()
        loop_state = {}

        bindings = service.build_direct_chat_state_bindings(
            agent_machine_full_trust_enabled_fn=lambda owner_user_id: owner_user_id == "owner-1",
            normalize_tool_capabilities_fn=lambda availability: availability.get("tools") or [],
            max_context_tool_actions=2,
            max_context_tool_capabilities=2,
            max_direct_chat_prior_message_chars=100,
            max_direct_chat_prior_messages=5,
            direct_chat_model_preferences=model_preferences,
            direct_chat_clear_markers=clear_markers,
            direct_tool_loop_state=loop_state,
            direct_chat_loop_repeat_limit=2,
            parse_json_object_loose_fn=lambda value: {},
            generate_reply_fn=lambda **kwargs: ("", {}, "", ""),
            extraction_prompt="prompt",
            extraction_system_prompt="system",
            save_session_transcript_fn=lambda *args, **kwargs: None,
            system_prefix="Memory",
        )

        session_key = bindings.direct_chat_session_key("default", "thread-1")
        bindings.set_session_model_preference(session_key, provider="openai", model="gpt-5.4")
        loop_repeat = bindings.record_direct_tool_signature(
            "tool:default:thread-1",
            {"name": "memory_search", "arguments": {}},
        )
        context_used = bindings.build_context_used(
            workspace_id="default",
            requested_provider="openai",
            effective_provider="openai",
            requested_model="gpt-5.4",
            effective_model="gpt-5.4",
            reasoning_effort=None,
            connected_systems=[],
            tool_capabilities=[],
            prior_messages_used=False,
            history_mode="none",
            run_created=False,
        )

        self.assertFalse(loop_repeat)
        self.assertEqual(bindings.session_model_preference(session_key)["provider"], "openai")
        self.assertTrue(bindings.agent_machine_full_trust_for_session({"meta": {"owner_user_id": "owner-1"}}))
        self.assertEqual(context_used["workspace"], "default")

    def test_build_direct_chat_shell_bindings_preserves_bootstrap_groups(self) -> None:
        shell = service.build_direct_chat_shell_bindings(
            agent_machine_full_trust_enabled_fn=lambda owner_user_id: owner_user_id == "owner-1",
            chat_max_iterations_default=30,
            chat_max_iterations_ceiling=100,
            compact_text_fn=lambda value: str(value or "").strip().lower(),
            direct_chat_compaction_token_limit=8000,
            execution_markers=("run",),
            question_openers=("what",),
            direct_run_openers=("summarize",),
            workflow_request_markers=("workflow",),
            google_workspace_keywords=("gmail",),
            smtp_keywords=("smtp",),
            telegram_keywords=("telegram",),
            slack_keywords=("slack",),
            dropbox_keywords=("dropbox",),
            s3_keywords=("s3",),
            max_context_tool_actions=2,
            max_context_tool_capabilities=2,
            max_direct_chat_prior_message_chars=100,
            max_direct_chat_prior_messages=5,
            direct_chat_model_preferences={},
            direct_chat_clear_markers=set(),
            direct_tool_loop_state={},
            direct_chat_loop_repeat_limit=2,
            parse_json_object_loose_fn=lambda value: {},
            generate_reply_fn=lambda **kwargs: ("", {}, "", ""),
            extraction_prompt="prompt",
            extraction_system_prompt="system",
            save_session_transcript_fn=lambda *args, **kwargs: None,
            system_prefix="Memory",
            workspace_context_dir_fn=lambda workspace_id: Path("/tmp"),
            memory_suggestion_prompts_fn=lambda workspace_id: [],
            run_handoff_execution_target_fn=lambda run_id: {"run_id": run_id},
            direct_chat_run_snapshot_fn=lambda run_id: None,
            direct_chat_run_event_to_step_fn=lambda run_id, event: None,
            direct_chat_run_snapshot_to_step_fn=lambda run_id, snapshot: None,
            direct_chat_run_final_payload_fn=lambda **kwargs: kwargs,
            live_window_seconds=12.0,
            poll_seconds=0.25,
            monotonic_fn=lambda: 1.0,
            sleep_fn=lambda seconds: None,
            parse_json_object_loose_support_fn=lambda value: {},
            direct_chat_tool_policy_callbacks_fn=lambda: service.DirectChatToolPolicyCallbacks(
                extract_first_url=lambda value: "",
                extract_first_path_reference=lambda value: "",
                message_requests_http_request_tool=lambda compact: False,
                message_requests_image_generation_tool=lambda compact: False,
                message_requests_browser_tool=lambda compact: False,
                message_requests_local_file_tool=lambda compact: False,
                message_requests_local_shell_tool=lambda compact: False,
                message_requests_local_screenshot_tool=lambda compact: False,
                message_requests_local_computer_tool=lambda compact: False,
                approval_required_for_direct_tool=lambda connector_id, action_id, arguments, tool_capabilities: False,
            ),
            direct_chat_routing_policy_callbacks_fn=lambda: service.DirectChatRoutingPolicyCallbacks(
                compact_text=lambda value: str(value or "").strip().lower(),
                question_like=lambda compact: compact.startswith("what "),
                mentions_any=lambda compact, markers: False,
                starts_like_direct_run=lambda compact: compact.startswith("run "),
                is_obvious_smtp_write_request=lambda compact: False,
                is_explicit_workflow_request=lambda compact: False,
            ),
        )

        self.assertEqual(shell.safe_positive_int("7", 1), 7)
        self.assertIsNotNone(shell.state_bindings)
        self.assertIsNotNone(shell.availability_bindings)
        self.assertIsNotNone(shell.handoff_bindings)
        self.assertIsNotNone(shell.tool_support_bindings)
        self.assertIsNotNone(shell.tool_routing_bindings)
        self.assertTrue(callable(shell.build_local_direct_chat_tools))

    def test_build_direct_chat_operator_binding_bundle_preserves_sub_bindings(self) -> None:
        bundle = service.build_direct_chat_operator_binding_bundle(
            namespace=_operator_namespace(),
            parse_page_state=lambda payload: payload,
            capture_exception=lambda exc: None,
            generate_chat_reply_stream_with_provider_fallback=lambda **kwargs: iter(()),
            compact_conversation_history=lambda **kwargs: {"messages": [], "history_mode": "none", "prior_messages_used": False},
            parse_memory_write=lambda value: None,
            parse_memory_read=lambda value: None,
            handle_memory_request=lambda workspace_id, message: None,
            list_memory_entries=lambda workspace_id: [],
            get_memory=lambda workspace_id: "",
            delete_memory=lambda workspace_id, key: False,
            no_provider_reasoning_required_response=lambda: {"reply": "fallback", "actions": [], "mode": "answer"},
            supported_providers=["openai"],
            direct_chat_compaction_token_limit=12000,
            complex_task_sequence_markers=("first",),
            complex_task_outcome_markers=("done",),
            execution_markers=("run",),
            google_workspace_keywords=("gmail",),
            smtp_keywords=("smtp",),
            telegram_keywords=("telegram",),
            slack_keywords=("slack",),
            discord_keywords=("discord",),
            dropbox_keywords=("dropbox",),
            s3_keywords=("s3",),
            browser_keywords=("browser",),
            local_file_keywords=("file",),
            local_shell_keywords=("shell",),
            local_screenshot_keywords=("screenshot",),
            local_computer_control_keywords=("click",),
            web_lookup_keywords=("latest",),
            http_request_keywords=("api",),
            image_generation_keywords=("image",),
            llm_task_keywords=("reason",),
            parse_json_object_loose=lambda value: {},
            llm_task=lambda **kwargs: None,
            web_search=lambda **kwargs: None,
            web_fetch=lambda **kwargs: None,
            search_memory_notebook=lambda *args, **kwargs: [],
            get_memory_notebook_excerpt=lambda *args, **kwargs: "",
            availability_lines=lambda workspace_id, availability: ["AI ready"],
            build_operator_system_prompt=lambda **kwargs: "base prompt",
            memory_tool_names=("memory_search",),
            local_worker_registry={},
            is_worker_online_fn=lambda *_args, **_kwargs: True,
            preferred_provider_fn=lambda workspace_id, requested_provider="": ("openai", {"api_key": "sk-test"}),
            supports_direct_message_native_chat_fn=lambda provider, credentials: True,
            resolve_workspace_tool_capabilities_fn=lambda workspace_id: [],
            direct_chat_credentials_fn=lambda workspace_id, provider: {"api_key": "sk-test"},
            build_provider_credential_candidates_fn=lambda workspace_id, provider: [{"api_key": "sk-test"}],
            compact_text_fn=lambda value: str(value or "").strip().lower(),
            mentions_any_fn=lambda message, markers: False,
            message_requests_local_file_tool_fn=lambda message: False,
            message_requests_local_shell_tool_fn=lambda message: False,
            message_requests_local_screenshot_tool_fn=lambda message: False,
            message_requests_local_computer_tool_fn=lambda message: False,
            is_obvious_smtp_write_request_fn=lambda message: False,
            preview_run_response_fn=lambda message, availability: None,
            prefer_durable_run_handoff_fn=lambda message, availability: False,
            durable_run_preferred_response_fn=lambda message: {"mode": "answer_with_action"},
            message_can_use_direct_connector_tools_fn=lambda message, *, provider, tools: False,
            message_can_use_direct_local_tools_fn=lambda message, *, provider, tools: False,
            message_can_use_builtin_direct_tools_fn=lambda message, *, tools: False,
            can_auto_start_run_handoff_fn=lambda availability: False,
            credential_auth_mode_fn=lambda provider, credentials: "api_key",
            normalize_auth_mode_fn=lambda value="": str(value or "").strip().lower(),
            get_claude_code_session_token_fn=lambda: "",
            provider_has_key_fn=lambda provider: provider == "openai",
            connect_action_fn=lambda label, href: {"label": label, "href": href},
            chat_iteration_limit_reply_fn=lambda limit: f"limit:{limit}",
            safe_positive_int_fn=lambda value, default: default,
            chat_max_iterations_default=30,
            execute_single_direct_tool_call_fn=lambda **kwargs: "ok",
        )

        self.assertIsNotNone(bundle.runtime_bindings)
        self.assertIsNotNone(bundle.policy_bindings)
        self.assertIsNotNone(bundle.entry_bindings)
        self.assertIsNotNone(bundle.tool_runtime_bindings)

    def test_build_direct_chat_operator_binding_bundle_from_namespace_resolves_wrappers(self) -> None:
        namespace = _operator_namespace()
        namespace.update(
            {
                "_availability_lines": lambda workspace_id, availability: ["AI ready"],
                "_preferred_provider": lambda workspace_id, requested_provider="": ("openai", {"api_key": "sk-test"}),
                "_supports_direct_message_native_chat": lambda provider, credentials: True,
                "_preview_run_response": lambda message, availability: None,
                "_prefer_durable_run_handoff": lambda message, availability: False,
                "_durable_run_preferred_response": lambda message: {"mode": "answer_with_action"},
                "_can_auto_start_run_handoff": lambda availability: False,
                "_credential_auth_mode": lambda provider, credentials: "api_key",
                "_message_requests_local_computer_tool": lambda message: False,
                "_message_can_use_direct_connector_tools": lambda message, *, provider, tools: False,
                "_message_can_use_direct_local_tools": lambda message, *, provider, tools: False,
                "_message_can_use_builtin_direct_tools": lambda message, *, tools: False,
            }
        )

        bundle = service.build_direct_chat_operator_binding_bundle_from_namespace(
            namespace=namespace,
            parse_json_object_loose_fn=lambda value: {},
            capture_exception_fn=lambda exc: None,
            generate_chat_reply_stream_with_provider_fallback_fn=lambda **kwargs: iter(()),
            compact_conversation_history_fn=lambda **kwargs: {"messages": [], "history_mode": "none", "prior_messages_used": False},
            parse_memory_write_fn=lambda value: None,
            parse_memory_read_fn=lambda value: None,
            handle_memory_request_fn=lambda workspace_id, message: None,
            list_memory_entries_fn=lambda workspace_id: [],
            get_memory_fn=lambda workspace_id: "",
            delete_memory_fn=lambda workspace_id, key: False,
            no_provider_reasoning_required_response_fn=lambda: {"reply": "fallback", "actions": [], "mode": "answer"},
            supported_providers=["openai"],
            direct_chat_compaction_token_limit=12000,
            complex_task_sequence_markers=("first",),
            complex_task_outcome_markers=("done",),
            execution_markers=("run",),
            google_workspace_keywords=("gmail",),
            smtp_keywords=("smtp",),
            telegram_keywords=("telegram",),
            slack_keywords=("slack",),
            discord_keywords=("discord",),
            dropbox_keywords=("dropbox",),
            s3_keywords=("s3",),
            browser_keywords=("browser",),
            local_file_keywords=("file",),
            local_shell_keywords=("shell",),
            local_screenshot_keywords=("screenshot",),
            local_computer_control_keywords=("click",),
            web_lookup_keywords=("latest",),
            http_request_keywords=("api",),
            image_generation_keywords=("image",),
            llm_task_keywords=("reason",),
            llm_task_fn=lambda **kwargs: None,
            web_search_fn=lambda **kwargs: None,
            web_fetch_fn=lambda **kwargs: None,
            search_memory_notebook_fn=lambda *args, **kwargs: [],
            get_memory_notebook_excerpt_fn=lambda *args, **kwargs: "",
            build_operator_system_prompt_fn=lambda **kwargs: "base prompt",
            memory_tool_names=("memory_search",),
            local_worker_registry={},
            is_worker_online_fn=lambda *_args, **_kwargs: True,
            resolve_workspace_tool_capabilities_fn=lambda workspace_id: [],
            build_provider_credential_candidates_fn=lambda workspace_id, provider: [{"api_key": "sk-test"}],
            normalize_auth_mode_fn=lambda value="": str(value or "").strip().lower(),
            get_claude_code_session_token_fn=lambda: "",
            provider_has_key_fn=lambda provider: provider == "openai",
            connect_action_fn=lambda label, href: {"label": label, "href": href},
            chat_iteration_limit_reply_fn=lambda limit: f"limit:{limit}",
            safe_positive_int_fn=lambda value, default: default,
            chat_max_iterations_default=30,
        )

        self.assertIsNotNone(bundle.runtime_bindings)
        self.assertIsNotNone(bundle.policy_bindings)
        self.assertIsNotNone(bundle.entry_bindings)
        self.assertIsNotNone(bundle.tool_runtime_bindings)

    def test_build_direct_chat_entrypoint_bindings_delegates_runtime_entry_facade(self) -> None:
        calls = []

        def _runtime_services():
            calls.append("runtime")
            return object()

        bindings = service.build_direct_chat_entrypoint_bindings(
            direct_chat_runtime_services_fn=_runtime_services,
        )

        with mock.patch.object(
            service.direct_chat_runtime_entry_facade_service,
            "collect_direct_operator_reply",
            return_value={"reply": "ok"},
        ) as collect_reply:
            payload = bindings.collect_direct_operator_reply(message="hi")

        self.assertEqual(payload["reply"], "ok")
        self.assertEqual(calls, ["runtime"])
        collect_reply.assert_called_once()

    def test_build_direct_chat_runtime_export_map_exposes_runtime_and_entrypoint_exports(self) -> None:
        bundle = service.build_direct_chat_operator_binding_bundle(
            namespace=_operator_namespace(),
            parse_page_state=lambda payload: payload,
            capture_exception=lambda exc: None,
            generate_chat_reply_stream_with_provider_fallback=lambda **kwargs: iter(()),
            compact_conversation_history=lambda **kwargs: {"messages": [], "history_mode": "none", "prior_messages_used": False},
            parse_memory_write=lambda value: None,
            parse_memory_read=lambda value: None,
            handle_memory_request=lambda workspace_id, message: None,
            list_memory_entries=lambda workspace_id: [],
            get_memory=lambda workspace_id: "",
            delete_memory=lambda workspace_id, key: False,
            no_provider_reasoning_required_response=lambda: {"reply": "fallback", "actions": [], "mode": "answer"},
            supported_providers=["openai"],
            direct_chat_compaction_token_limit=12000,
            complex_task_sequence_markers=("first",),
            complex_task_outcome_markers=("done",),
            execution_markers=("run",),
            google_workspace_keywords=("gmail",),
            smtp_keywords=("smtp",),
            telegram_keywords=("telegram",),
            slack_keywords=("slack",),
            discord_keywords=("discord",),
            dropbox_keywords=("dropbox",),
            s3_keywords=("s3",),
            browser_keywords=("browser",),
            local_file_keywords=("file",),
            local_shell_keywords=("shell",),
            local_screenshot_keywords=("screenshot",),
            local_computer_control_keywords=("click",),
            web_lookup_keywords=("latest",),
            http_request_keywords=("api",),
            image_generation_keywords=("image",),
            llm_task_keywords=("reason",),
            parse_json_object_loose=lambda value: {},
            llm_task=lambda **kwargs: None,
            web_search=lambda **kwargs: None,
            web_fetch=lambda **kwargs: None,
            search_memory_notebook=lambda *args, **kwargs: [],
            get_memory_notebook_excerpt=lambda *args, **kwargs: "",
            availability_lines=lambda workspace_id, availability: ["AI ready"],
            build_operator_system_prompt=lambda **kwargs: "base prompt",
            memory_tool_names=("memory_search",),
            local_worker_registry={},
            is_worker_online_fn=lambda *_args, **_kwargs: True,
            preferred_provider_fn=lambda workspace_id, requested_provider="": ("openai", {"api_key": "sk-test"}),
            supports_direct_message_native_chat_fn=lambda provider, credentials: True,
            resolve_workspace_tool_capabilities_fn=lambda workspace_id: [],
            direct_chat_credentials_fn=lambda workspace_id, provider: {"api_key": "sk-test"},
            build_provider_credential_candidates_fn=lambda workspace_id, provider: [{"api_key": "sk-test"}],
            compact_text_fn=lambda value: str(value or "").strip().lower(),
            mentions_any_fn=lambda message, markers: False,
            message_requests_local_file_tool_fn=lambda message: False,
            message_requests_local_shell_tool_fn=lambda message: False,
            message_requests_local_screenshot_tool_fn=lambda message: False,
            message_requests_local_computer_tool_fn=lambda message: False,
            is_obvious_smtp_write_request_fn=lambda message: False,
            preview_run_response_fn=lambda message, availability: None,
            prefer_durable_run_handoff_fn=lambda message, availability: False,
            durable_run_preferred_response_fn=lambda message: {"mode": "answer_with_action"},
            message_can_use_direct_connector_tools_fn=lambda message, *, provider, tools: False,
            message_can_use_direct_local_tools_fn=lambda message, *, provider, tools: False,
            message_can_use_builtin_direct_tools_fn=lambda message, *, tools: False,
            can_auto_start_run_handoff_fn=lambda availability: False,
            credential_auth_mode_fn=lambda provider, credentials: "api_key",
            normalize_auth_mode_fn=lambda value="": str(value or "").strip().lower(),
            get_claude_code_session_token_fn=lambda: "",
            provider_has_key_fn=lambda provider: provider == "openai",
            connect_action_fn=lambda label, href: {"label": label, "href": href},
            chat_iteration_limit_reply_fn=lambda limit: f"limit:{limit}",
            safe_positive_int_fn=lambda value, default: default,
            chat_max_iterations_default=30,
            execute_single_direct_tool_call_fn=lambda **kwargs: "ok",
        )

        export_map = service.build_direct_chat_runtime_export_map(
            binding_bundle=bundle,
            preview_run_response_fn=lambda message, availability: {"mode": "preview"},
            prefer_durable_run_handoff_fn=lambda message, availability: True,
        )

        self.assertIn("_direct_chat_runtime_services", export_map)
        self.assertIn("_preview_run_response", export_map)
        self.assertIn("collect_direct_operator_reply", export_map)
        self.assertTrue(callable(export_map["collect_direct_operator_reply"]))
        self.assertTrue(callable(export_map["build_direct_operator_reply"]))

    def test_build_direct_chat_shell_export_map_exposes_shell_aliases(self) -> None:
        shell = service.build_direct_chat_shell_bindings(
            agent_machine_full_trust_enabled_fn=lambda owner_user_id: owner_user_id == "owner-1",
            chat_max_iterations_default=30,
            chat_max_iterations_ceiling=100,
            compact_text_fn=lambda value: str(value or "").strip().lower(),
            direct_chat_compaction_token_limit=8000,
            execution_markers=("run",),
            question_openers=("what",),
            direct_run_openers=("summarize",),
            workflow_request_markers=("workflow",),
            google_workspace_keywords=("gmail",),
            smtp_keywords=("smtp",),
            telegram_keywords=("telegram",),
            slack_keywords=("slack",),
            dropbox_keywords=("dropbox",),
            s3_keywords=("s3",),
            max_context_tool_actions=2,
            max_context_tool_capabilities=2,
            max_direct_chat_prior_message_chars=100,
            max_direct_chat_prior_messages=5,
            direct_chat_model_preferences={},
            direct_chat_clear_markers=set(),
            direct_tool_loop_state={},
            direct_chat_loop_repeat_limit=2,
            parse_json_object_loose_fn=lambda value: {},
            generate_reply_fn=lambda **kwargs: ("", {}, "", ""),
            extraction_prompt="prompt",
            extraction_system_prompt="system",
            save_session_transcript_fn=lambda *args, **kwargs: None,
            system_prefix="Memory",
            workspace_context_dir_fn=lambda workspace_id: Path("/tmp"),
            memory_suggestion_prompts_fn=lambda workspace_id: [],
            run_handoff_execution_target_fn=lambda run_id: {"run_id": run_id},
            direct_chat_run_snapshot_fn=lambda run_id: None,
            direct_chat_run_event_to_step_fn=lambda run_id, event: None,
            direct_chat_run_snapshot_to_step_fn=lambda run_id, snapshot: None,
            direct_chat_run_final_payload_fn=lambda **kwargs: kwargs,
            live_window_seconds=12.0,
            poll_seconds=0.25,
            monotonic_fn=lambda: 1.0,
            sleep_fn=lambda seconds: None,
            parse_json_object_loose_support_fn=lambda value: {},
            direct_chat_tool_policy_callbacks_fn=lambda: service.DirectChatToolPolicyCallbacks(
                extract_first_url=lambda value: "",
                extract_first_path_reference=lambda value: "",
                message_requests_http_request_tool=lambda compact: False,
                message_requests_image_generation_tool=lambda compact: False,
                message_requests_browser_tool=lambda compact: False,
                message_requests_local_file_tool=lambda compact: False,
                message_requests_local_shell_tool=lambda compact: False,
                message_requests_local_screenshot_tool=lambda compact: False,
                message_requests_local_computer_tool=lambda compact: False,
                approval_required_for_direct_tool=lambda connector_id, action_id, arguments, tool_capabilities: False,
            ),
            direct_chat_routing_policy_callbacks_fn=lambda: service.DirectChatRoutingPolicyCallbacks(
                compact_text=lambda value: str(value or "").strip().lower(),
                question_like=lambda compact: compact.startswith("what "),
                mentions_any=lambda compact, markers: False,
                starts_like_direct_run=lambda compact: compact.startswith("run "),
                is_obvious_smtp_write_request=lambda compact: False,
                is_explicit_workflow_request=lambda compact: False,
            ),
        )

        export_map = service.build_direct_chat_shell_export_map(shell_bindings=shell)

        self.assertIn("_compact_text", export_map)
        self.assertIn("_direct_chat_state_bindings", export_map)
        self.assertIn("_direct_chat_tool_routing_bindings", export_map)
        self.assertIn("_direct_chat_tool_support_bindings", export_map)
        self.assertIn("_provider_display_name", export_map)

    def test_build_direct_chat_module_export_map_from_namespace_merges_shell_and_runtime_exports(self) -> None:
        namespace = _operator_namespace()
        namespace.update(
            {
                "_direct_chat_run_snapshot": lambda run_id: None,
                "_direct_chat_run_event_to_step": lambda run_id, event: None,
                "_direct_chat_run_snapshot_to_step": lambda run_id, snapshot: None,
                "_direct_chat_run_final_payload": lambda **kwargs: kwargs,
            }
        )

        export_map = service.build_direct_chat_module_export_map_from_namespace(
            namespace=namespace,
            agent_machine_full_trust_enabled_fn=lambda owner_user_id: owner_user_id == "owner-1",
            chat_max_iterations_default=30,
            chat_max_iterations_ceiling=100,
            compact_text_fn=lambda value: str(value or "").strip().lower(),
            direct_chat_compaction_token_limit=8000,
            execution_markers=("run",),
            question_openers=("what",),
            direct_run_openers=("summarize",),
            workflow_request_markers=("workflow",),
            google_workspace_keywords=("gmail",),
            smtp_keywords=("smtp",),
            telegram_keywords=("telegram",),
            slack_keywords=("slack",),
            dropbox_keywords=("dropbox",),
            s3_keywords=("s3",),
            max_context_tool_actions=2,
            max_context_tool_capabilities=2,
            max_direct_chat_prior_message_chars=100,
            max_direct_chat_prior_messages=5,
            direct_chat_model_preferences={},
            direct_chat_clear_markers=set(),
            direct_tool_loop_state={},
            direct_chat_loop_repeat_limit=2,
            parse_json_object_loose_fn=lambda value: {},
            generate_reply_fn=lambda **kwargs: ("", {}, "", ""),
            extraction_prompt="prompt",
            extraction_system_prompt="system",
            save_session_transcript_fn=lambda *args, **kwargs: None,
            system_prefix="Memory",
            workspace_context_dir_fn=lambda workspace_id: Path("/tmp"),
            memory_suggestion_prompts_fn=lambda workspace_id: [],
            direct_chat_run_snapshot_fn=lambda run_id: None,
            direct_chat_run_event_to_step_fn=lambda run_id, event: None,
            direct_chat_run_snapshot_to_step_fn=lambda run_id, snapshot: None,
            direct_chat_run_final_payload_fn=lambda **kwargs: kwargs,
            live_window_seconds=12.0,
            poll_seconds=0.25,
            monotonic_fn=lambda: 1.0,
            sleep_fn=lambda seconds: None,
            parse_json_object_loose_support_fn=lambda value: {},
            direct_chat_tool_policy_callbacks_fn=lambda: service.DirectChatToolPolicyCallbacks(
                extract_first_url=lambda value: "",
                extract_first_path_reference=lambda value: "",
                message_requests_http_request_tool=lambda compact: False,
                message_requests_image_generation_tool=lambda compact: False,
                message_requests_browser_tool=lambda compact: False,
                message_requests_local_file_tool=lambda compact: False,
                message_requests_local_shell_tool=lambda compact: False,
                message_requests_local_screenshot_tool=lambda compact: False,
                message_requests_local_computer_tool=lambda compact: False,
                approval_required_for_direct_tool=lambda connector_id, action_id, arguments, tool_capabilities: False,
            ),
            direct_chat_routing_policy_callbacks_fn=lambda: service.DirectChatRoutingPolicyCallbacks(
                compact_text=lambda value: str(value or "").strip().lower(),
                question_like=lambda compact: compact.startswith("what "),
                mentions_any=lambda compact, markers: False,
                starts_like_direct_run=lambda compact: compact.startswith("run "),
                is_obvious_smtp_write_request=lambda compact: False,
                is_explicit_workflow_request=lambda compact: False,
            ),
            capture_exception_fn=lambda exc: None,
            generate_chat_reply_stream_with_provider_fallback_fn=lambda **kwargs: iter(()),
            compact_conversation_history_fn=lambda **kwargs: {"messages": [], "history_mode": "none", "prior_messages_used": False},
            parse_memory_write_fn=lambda value: None,
            parse_memory_read_fn=lambda value: None,
            handle_memory_request_fn=lambda workspace_id, message: None,
            list_memory_entries_fn=lambda workspace_id: [],
            get_memory_fn=lambda workspace_id: "",
            delete_memory_fn=lambda workspace_id, key: False,
            no_provider_reasoning_required_response_fn=lambda: {"reply": "fallback", "actions": [], "mode": "answer"},
            supported_providers=["openai"],
            complex_task_sequence_markers=("first",),
            complex_task_outcome_markers=("done",),
            discord_keywords=("discord",),
            browser_keywords=("browser",),
            local_file_keywords=("file",),
            local_shell_keywords=("shell",),
            local_screenshot_keywords=("screenshot",),
            local_computer_control_keywords=("click",),
            web_lookup_keywords=("latest",),
            http_request_keywords=("api",),
            image_generation_keywords=("image",),
            llm_task_keywords=("reason",),
            llm_task_fn=lambda **kwargs: None,
            web_search_fn=lambda **kwargs: None,
            web_fetch_fn=lambda **kwargs: None,
            search_memory_notebook_fn=lambda *args, **kwargs: [],
            get_memory_notebook_excerpt_fn=lambda *args, **kwargs: "",
            build_operator_system_prompt_fn=lambda **kwargs: "base prompt",
            memory_tool_names=("memory_search",),
            local_worker_registry={},
            is_worker_online_fn=lambda *_args, **_kwargs: True,
            resolve_workspace_tool_capabilities_fn=lambda workspace_id: [],
            build_provider_credential_candidates_fn=lambda workspace_id, provider: [{"api_key": "sk-test"}],
            normalize_auth_mode_fn=lambda value="": str(value or "").strip().lower(),
            get_claude_code_session_token_fn=lambda: "",
            provider_has_key_fn=lambda provider: provider == "openai",
            chat_iteration_limit_reply_fn=lambda limit: f"limit:{limit}",
            safe_positive_int_fn=lambda value, default: default,
        )

        self.assertIn("_direct_chat_shell_bindings", export_map)
        self.assertIn("_direct_chat_binding_bundle", export_map)
        self.assertIn("_direct_chat_runtime_services", export_map)
        self.assertIn("_direct_chat_tool_routing_bindings", export_map)
        self.assertIn("build_direct_operator_reply", export_map)
        self.assertTrue(callable(export_map["collect_direct_operator_reply"]))

    def test_build_direct_chat_tool_support_bindings_preserves_config_and_support_helpers(self) -> None:
        bindings = service.build_direct_chat_tool_support_bindings(
            parse_json_object_loose_fn=lambda value: {"url": "https://example.com"} if value else {},
        )

        tool_config = bindings.build_direct_tool_config(
            "google_workspace",
            "send_email",
            "to john@example.com subject Demo body Hello there",
        )
        approved_tool = bindings.approved_action_to_tool_call(
            {"connector": "http", "action": "request", "input": '{"url":"https://example.com"}'}
        )

        self.assertEqual(bindings.parse_tool_name("memory_search"), ("memory", "search"))
        self.assertEqual(bindings.parse_tool_name("memory_stage_edit"), ("memory", "stage_edit"))
        self.assertEqual(bindings.parse_tool_name("memory_apply_edit"), ("memory", "apply_edit"))
        self.assertEqual(bindings.parse_tool_name("memory_append_daily_note"), ("memory", "append_daily_note"))
        self.assertEqual(bindings.parse_tool_name("memory_stage_consolidation"), ("memory", "stage_consolidation"))
        self.assertEqual(bindings.parse_tool_name("memory_consolidate_daily_notes"), ("memory", "consolidate_daily_notes"))
        self.assertEqual(bindings.parse_tool_name("memory_list_versions"), ("memory", "list_versions"))
        self.assertEqual(bindings.parse_tool_name("memory_rollback_version"), ("memory", "rollback_version"))
        self.assertEqual(bindings.tool_arguments_payload({"x": 1}), {"x": 1})
        self.assertEqual(tool_config["to_email"], "john@example.com")
        self.assertEqual(approved_tool["name"], "http_request")
        self.assertIn("https://example.com", approved_tool["arguments"])
        self.assertEqual(bindings.provider_display_name("codex_cli"), "Codex/OpenAI")

    def test_build_direct_chat_operator_binding_bundle_from_namespace_preserves_positional_tool_callbacks(self) -> None:
        namespace = _operator_namespace()
        calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

        def _record(name: str):
            def _inner(*args, **kwargs):
                calls.append((name, args, kwargs))
                if name == "web_search":
                    return [{"title": "Result", "url": "https://example.com", "snippet": "demo"}]
                if name == "web_fetch":
                    return "fetched"
                return {"ok": True}

            return _inner

        namespace.update(
            {
                "availability_lines": lambda workspace_id, availability: [],
                "llm_task": _record("llm_task"),
                "preferred_provider": lambda workspace_id, requested_provider="": ("openai", {"api_key": "sk-test"}),
                "supports_direct_message_native_chat": lambda provider, credentials: True,
                "direct_chat_credentials": lambda workspace_id, provider: {"api_key": "sk-test"},
                "preview_run_response": lambda message, availability: None,
                "prefer_durable_run_handoff": lambda message, availability: False,
                "durable_run_preferred_response": lambda message: {"mode": "answer_with_action"},
                "message_can_use_direct_connector_tools": lambda message, *, provider, tools: False,
                "message_can_use_direct_local_tools": lambda message, *, provider, tools: False,
                "message_can_use_builtin_direct_tools": lambda message, *, tools: False,
                "can_auto_start_run_handoff": lambda availability: False,
                "credential_auth_mode": lambda provider, credentials: "api_key",
                "execute_single_direct_tool_call": lambda **kwargs: "ok",
                "web_search": _record("web_search"),
                "web_fetch": _record("web_fetch"),
            }
        )

        bundle = service.build_direct_chat_operator_binding_bundle_from_namespace(
            namespace=namespace,
            parse_json_object_loose_fn=lambda value: {},
            capture_exception_fn=lambda exc: None,
            generate_chat_reply_stream_with_provider_fallback_fn=lambda **kwargs: iter(()),
            compact_conversation_history_fn=lambda **kwargs: {"messages": [], "history_mode": "none", "prior_messages_used": False},
            parse_memory_write_fn=lambda value: None,
            parse_memory_read_fn=lambda value: None,
            handle_memory_request_fn=lambda workspace_id, message: None,
            list_memory_entries_fn=lambda workspace_id: [],
            get_memory_fn=lambda workspace_id: "",
            delete_memory_fn=lambda workspace_id, key: False,
            no_provider_reasoning_required_response_fn=lambda: {"reply": "fallback", "actions": [], "mode": "answer"},
            supported_providers=["openai"],
            direct_chat_compaction_token_limit=12000,
            complex_task_sequence_markers=("first",),
            complex_task_outcome_markers=("done",),
            execution_markers=("run",),
            google_workspace_keywords=("gmail",),
            smtp_keywords=("smtp",),
            telegram_keywords=("telegram",),
            slack_keywords=("slack",),
            discord_keywords=("discord",),
            dropbox_keywords=("dropbox",),
            s3_keywords=("s3",),
            browser_keywords=("browser",),
            local_file_keywords=("file",),
            local_shell_keywords=("shell",),
            local_screenshot_keywords=("screenshot",),
            local_computer_control_keywords=("click",),
            web_lookup_keywords=("latest",),
            http_request_keywords=("api",),
            image_generation_keywords=("image",),
            llm_task_keywords=("reason",),
            llm_task_fn=lambda **kwargs: None,
            web_search_fn=lambda **kwargs: [],
            web_fetch_fn=lambda **kwargs: "",
            search_memory_notebook_fn=lambda *args, **kwargs: [],
            get_memory_notebook_excerpt_fn=lambda *args, **kwargs: "",
            build_operator_system_prompt_fn=lambda **kwargs: "base prompt",
            memory_tool_names=("memory_search",),
            local_worker_registry={},
            is_worker_online_fn=lambda *_args, **_kwargs: True,
            resolve_workspace_tool_capabilities_fn=lambda workspace_id: [],
            build_provider_credential_candidates_fn=lambda workspace_id, provider: [{"api_key": "sk-test"}],
            normalize_auth_mode_fn=lambda value="": str(value or "").strip().lower(),
            get_claude_code_session_token_fn=lambda: "",
            provider_has_key_fn=lambda provider: provider == "openai",
            connect_action_fn=lambda label, href: {"label": label, "href": href},
            chat_iteration_limit_reply_fn=lambda limit: f"limit:{limit}",
            safe_positive_int_fn=lambda value, default: default,
            chat_max_iterations_default=30,
        )

        callbacks = bundle.policy_bindings.direct_tool_execution_callbacks()
        self.assertEqual(callbacks.web_search("cats")[0]["title"], "Result")
        self.assertEqual(callbacks.web_fetch("https://example.com"), "fetched")
        self.assertEqual(
            callbacks.llm_task(
                "Summarize cats",
                schema={"type": "object"},
                context={"workspace_id": "default"},
                metadata={"provider": "openai"},
            ),
            {"ok": True},
        )
        self.assertEqual(calls[0], ("web_search", ("cats",), {}))
        self.assertEqual(calls[1], ("web_fetch", ("https://example.com",), {}))
        self.assertEqual(calls[2][0], "llm_task")
        self.assertEqual(calls[2][1], ("Summarize cats",))
        self.assertEqual(calls[2][2]["schema"], {"type": "object"})


if __name__ == "__main__":
    unittest.main()
