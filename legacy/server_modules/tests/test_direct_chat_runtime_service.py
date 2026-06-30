import unittest
from types import SimpleNamespace
from unittest import mock

from server_modules import agent_trace_service
from server_modules import direct_chat_generation_service
from server_modules import direct_chat_response_service
from server_modules import direct_chat_runtime_service
from server_modules import no_provider_service


class DirectChatRuntimeServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._rust_run_api_patch = mock.patch.object(
            direct_chat_runtime_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "test_run_api_allowed",
                "next_action": "start_chat_stream",
            },
        )
        self._rust_run_api_patch.start()

    def tearDown(self) -> None:
        self._rust_run_api_patch.stop()

    def _response_services(self) -> direct_chat_response_service.DirectChatResponseServices:
        return direct_chat_response_service.DirectChatResponseServices(
            with_context_used=lambda payload, context: {**payload, "context_used": context},
            build_context_used=lambda **kwargs: kwargs,
            connected_provider_tokens=lambda _workspace_id: ["openai"],
            active_run_count=lambda _workspace_id: 0,
            slash_command_help_text=lambda: "help text",
            execute_direct_tool_calls=lambda **kwargs: "tool reply",
            direct_chat_credentials=lambda _workspace_id, _provider: {"api_key": "sk-test"},
            capture_exception=lambda exc: None,
        )

    def _runtime_services(self, prepared: SimpleNamespace) -> direct_chat_runtime_service.DirectChatRuntimeServices:
        return direct_chat_runtime_service.DirectChatRuntimeServices(
            prepare_direct_chat_request=lambda **kwargs: prepared,
            direct_chat_response_services=self._response_services(),
            with_context_used=lambda payload, context: {**payload, "context_used": context},
            tool_write_action_available=lambda connector, action, capabilities: True,
            approved_action_to_tool_call=lambda approved_action: {},
            message_has_obvious_direct_tool_intent=lambda message, tools: False,
            no_provider_execution_services=no_provider_service.NoProviderExecutionServices(
                compact_text=lambda value: str(value or "").strip().lower(),
                safe_positive_int=lambda value, default=0: default,
                resolve_local_path=lambda path: mock.MagicMock(),
                extract_first_path_reference=lambda message: "",
                extract_first_url=lambda message: "",
                parse_page_state=lambda payload: payload,
                parse_memory_write=lambda message: None,
                parse_memory_read=lambda message: None,
                handle_memory_request=lambda workspace_id, message: None,
                parse_tool_name=lambda name: ("", ""),
                tool_arguments_payload=lambda arguments: {},
                approval_required_for_tool=lambda connector, action, payload, capabilities: False,
                agent_machine_full_trust_for_session=lambda session_ctx: False,
                execute_single_tool_call=lambda **kwargs: "tool reply",
            ),
            build_context_used=lambda **kwargs: kwargs,
            resolve_provider_for_direct_chat_message=lambda workspace_id, requested_provider, message, tools_present=False: ("openai", {}),
            plan_direct_chat_route=lambda **kwargs: SimpleNamespace(
                allow_direct_tool_calls=True,
                preview=None,
                should_auto_start_run=False,
            ),
            start_direct_chat_run_handoff=lambda **kwargs: {"run_id": "run-1"},
            direct_chat_run_handoff_reply=lambda started: {"detail": "Run started"},
            stream_direct_chat_run_handoff=lambda **kwargs: iter(()),
            direct_chat_run_handoff_failure_payload=lambda message, detail: {"reply": detail},
            supports_direct_message_native_chat=lambda provider, credentials: True,
            supported_providers=["openai"],
            build_direct_chat_system_prompt=lambda **kwargs: "system prompt",
            direct_chat_workspace_context_text=lambda workspace_id, memory_query="": "",
            direct_chat_generation_services=direct_chat_generation_service.DirectChatGenerationServices(
                thinking_step_payload=lambda iteration, status, detail=None: {"type": "step", "label": "Thinking"},
                build_context_used=lambda **kwargs: kwargs,
                build_direct_tool_approval_response=lambda **kwargs: None,
                parse_tool_name=lambda name: ("", ""),
                tool_arguments_payload=lambda arguments: {},
                parse_page_state=lambda payload: payload,
                direct_tool_step_payload=lambda *args, **kwargs: {"type": "step", "label": "Tool"},
                execute_single_direct_tool_call=lambda **kwargs: "done",
                direct_tool_followup_message=lambda tool_name, result: result,
                suggest_actions=lambda reply, availability: [],
                clear_direct_tool_loop_state=lambda session_key: None,
                persist_direct_chat_memory_best_effort=lambda **kwargs: None,
                persist_direct_chat_transcript_best_effort=lambda **kwargs: None,
                persist_direct_chat_hosted_usage_best_effort=lambda **kwargs: None,
                record_direct_tool_signature=lambda session_key, tool_call: False,
                direct_chat_error_reply=lambda error: error,
                capture_exception=lambda exc: None,
                generate_chat_reply_stream_with_provider_fallback=lambda **kwargs: iter(()),
            ),
            no_provider_reasoning_required_response=lambda: {
                "reply": "",
                "actions": [],
                "mode": "error",
                "error": "no_provider",
            },
            capture_exception=lambda exc: None,
        )

    def test_build_direct_operator_reply_returns_empty_message_payload(self) -> None:
        prepared = SimpleNamespace(
            normalized_message="",
            normalized_workspace_id="default",
            normalized_thread_id="thread-1",
            normalized_requested_provider="openai",
            normalized_requested_model="gpt-5.4",
            normalized_reasoning_effort="medium",
            compaction={},
            compacted_prior_messages=[],
            proactive_suggestions=["next"],
            tool_loop_session_key="loop",
            availability_payload={"ai_ready": True},
            connected_systems=[],
            tool_capabilities=[],
            tools=[],
            approved_action_payload=None,
            base_context_used={"workspace_id": "default"},
            slash_command_name="",
            slash_remainder="",
            resolved_chat_max_iterations=3,
        )

        events = list(
            direct_chat_runtime_service.build_direct_operator_reply(
                services=self._runtime_services(prepared),
                message="",
                workspace_id="default",
                requested_model="gpt-5.4",
                requested_provider="openai",
            )
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "final")
        self.assertEqual(events[0]["payload"]["reply"], "")
        self.assertEqual(events[0]["payload"]["interventions"][0]["kind"], "system_notice")
        self.assertEqual(events[0]["payload"]["interventions"][0]["title"], "Describe the outcome you want")
        self.assertIn("Tell the system what you want done", events[0]["payload"]["interventions"][0]["detail"])
        self.assertEqual(events[0]["payload"]["suggestions"], ["next"])

    def test_hosted_tier_forces_platform_runtime_credentials(self) -> None:
        prepared = SimpleNamespace(
            normalized_message="hello",
            normalized_workspace_id="ws-1",
            normalized_thread_id="thread-1",
            normalized_requested_provider="deepseek",
            normalized_requested_model="deepseek-v4-pro",
            normalized_reasoning_effort="high",
            compaction={},
            compacted_prior_messages=[],
            proactive_suggestions=[],
            tool_loop_session_key="loop",
            availability_payload={
                "ai_ready": False,
                "force_platform_runtime": True,
                "credential_plane": "platform_runtime",
                "billing_source": "empyralis_credits",
                "ai_tier": "pro",
            },
            connected_systems=[],
            tool_capabilities=[],
            tools=[],
            approved_action_payload=None,
            base_context_used={"workspace_id": "ws-1"},
            slash_command_name="",
            slash_remainder="",
            resolved_chat_max_iterations=3,
        )
        services = self._runtime_services(prepared)
        services.supported_providers = ["deepseek"]
        services.resolve_provider_for_direct_chat_message = (
            lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("generic provider selector should not run"))
        )
        observed: dict[str, object] = {}

        def _generate(**kwargs):
            observed["metadata"] = kwargs.get("metadata")
            yield {"type": "chunk", "delta": "ok"}
            yield {"type": "result", "reply": "ok", "provider": "deepseek", "model": "deepseek-v4-pro"}

        services.direct_chat_generation_services.generate_chat_reply_stream_with_provider_fallback = _generate

        with mock.patch(
            "server_modules.direct_chat_runtime_service.direct_chat_provider_service.platform_runtime_credentials",
            return_value={"api_key": "sk-platform", "auth_mode": "platform_runtime"},
        ):
            events = list(
                direct_chat_runtime_service.build_direct_operator_reply(
                    services=services,
                    message=prepared.normalized_message,
                    workspace_id="ws-1",
                    requested_model="deepseek-v4-pro",
                    requested_provider="deepseek",
                )
            )

        self.assertEqual(events[-1]["type"], "final")
        self.assertEqual(events[-1]["payload"]["reply"], "ok")
        metadata = observed.get("metadata")
        self.assertIsInstance(metadata, dict)
        self.assertEqual(metadata["billing_source"], "empyralis_credits")
        self.assertEqual(metadata["ai_tier"], "pro")
        self.assertEqual(metadata["credentials"]["api_key"], "sk-platform")

    def test_collect_direct_operator_reply_accumulates_chunks(self) -> None:
        services = self._runtime_services(SimpleNamespace())

        with mock.patch.object(
            direct_chat_runtime_service,
            "build_direct_operator_reply",
            return_value=iter(
                [
                    {"type": "chunk", "delta": "Hello "},
                    {"type": "chunk", "delta": "world"},
                    {"type": "final", "payload": {"actions": [], "mode": "answer"}},
                ]
            ),
        ):
            payload = direct_chat_runtime_service.collect_direct_operator_reply(
                services=services,
                message="hi",
                workspace_id="default",
                requested_model="gpt-5.4",
                requested_provider="openai",
            )

        self.assertEqual(payload["reply"], "Hello world")
        self.assertEqual(payload["mode"], "answer")

    def test_build_direct_operator_reply_uses_session_turn_request_fallback(self) -> None:
        captured: dict[str, object] = {}
        prepared = SimpleNamespace(
            normalized_message="hello from turn",
            normalized_workspace_id="default",
            normalized_thread_id="thread-1",
            normalized_requested_provider="openai",
            normalized_requested_model="gpt-5.4",
            normalized_reasoning_effort="medium",
            compaction={},
            compacted_prior_messages=[],
            proactive_suggestions=[],
            tool_loop_session_key="loop",
            availability_payload={"ai_ready": True},
            connected_systems=[],
            tool_capabilities=[],
            tools=[],
            approved_action_payload=None,
            base_context_used={"workspace_id": "default"},
            slash_command_name="help",
            slash_remainder="",
            resolved_chat_max_iterations=3,
        )
        services = self._runtime_services(prepared)
        def _prepare_direct_chat_request(**kwargs):
            captured["kwargs"] = kwargs
            return prepared
        services.prepare_direct_chat_request = _prepare_direct_chat_request

        turn_request = {
            "workspace_id": "default",
            "session_id": "thread-1",
            "channel": "web",
            "actor": {"type": "user", "id": "user-1", "display_name": "user-1"},
            "message": "hello from turn",
        }

        list(
            direct_chat_runtime_service.build_direct_operator_reply(
                services=services,
                message="fallback",
                workspace_id="default",
                requested_model="gpt-5.4",
                requested_provider="openai",
                session_ctx={"agent_turn_request": turn_request},
            )
        )

        resolved_turn_request = captured["kwargs"]["resolved_turn_request"]
        self.assertEqual(resolved_turn_request.workspace_id, "default")
        self.assertEqual(resolved_turn_request.message, "hello from turn")

    def test_build_direct_operator_reply_hydrates_prior_messages_from_thread_store(self) -> None:
        captured: dict[str, object] = {}
        prepared = SimpleNamespace(
            normalized_message="hello from turn",
            normalized_workspace_id="default",
            normalized_thread_id="thread-1",
            normalized_requested_provider="openai",
            normalized_requested_model="gpt-5.4",
            normalized_reasoning_effort="medium",
            compaction={},
            compacted_prior_messages=[{"role": "user", "content": "Earlier instruction"}],
            proactive_suggestions=[],
            tool_loop_session_key="loop",
            availability_payload={"ai_ready": True},
            connected_systems=[],
            tool_capabilities=[],
            tools=[],
            approved_action_payload=None,
            base_context_used={"workspace_id": "default"},
            slash_command_name="help",
            slash_remainder="",
            resolved_chat_max_iterations=3,
        )
        services = self._runtime_services(prepared)

        def _prepare_direct_chat_request(**kwargs):
            captured["kwargs"] = kwargs
            return prepared

        services.prepare_direct_chat_request = _prepare_direct_chat_request
        turn_request = {
            "tenant_id": "tenant-1",
            "workspace_id": "default",
            "thread_id": "thread-1",
            "session_id": "thread-1",
            "channel": "web",
            "actor": {"type": "user", "id": "user-1", "display_name": "user-1"},
            "message": "hello from turn",
        }

        with mock.patch.object(
            direct_chat_runtime_service,
            "_hydrate_prior_messages_from_thread_store",
            return_value=[
                {"role": "user", "content": "Earlier instruction"},
                {"role": "assistant", "content": "Earlier reply"},
            ],
        ) as hydrate_mock:
            list(
                direct_chat_runtime_service.build_direct_operator_reply(
                    services=services,
                    message="fallback",
                    workspace_id="default",
                    thread_id="thread-1",
                    requested_model="gpt-5.4",
                    requested_provider="openai",
                    prior_messages=[],
                    session_ctx={"agent_turn_request": turn_request},
                )
            )

        hydrate_mock.assert_called_once_with(
            workspace_id="default",
            tenant_id="tenant-1",
            thread_id="thread-1",
            current_message="hello from turn",
        )
        self.assertEqual(
            captured["kwargs"]["prior_messages"],
            [
                {"role": "user", "content": "Earlier instruction"},
                {"role": "assistant", "content": "Earlier reply"},
            ],
        )

    def test_build_chat_turn_event_stream_prefers_request_meta_turn_request(self) -> None:
        captured: dict[str, object] = {}
        services = self._runtime_services(SimpleNamespace())

        def _build_direct_operator_reply(**kwargs):
            captured.update(kwargs)
            return iter(())

        with mock.patch.object(
            direct_chat_runtime_service,
            "build_direct_operator_reply",
            side_effect=_build_direct_operator_reply,
        ):
            list(
                direct_chat_runtime_service.build_chat_turn_event_stream(
                    services=services,
                    session_ctx={
                        "workspace_id": "context-workspace",
                        "thread_id": "context-thread",
                        "agent_turn_request": {
                            "workspace_id": "context-workspace",
                            "session_id": "context-thread",
                            "channel": "web",
                            "actor": {"type": "user", "id": "context-user", "display_name": "context-user"},
                            "message": "context message",
                        },
                    },
                    message="fallback",
                    request_meta={
                        "workspace_id": "meta-workspace",
                        "thread_id": "meta-thread",
                        "request_id": "req-meta-1",
                        "client_request_id": "req-meta-1",
                        "agent_turn_request": {
                            "workspace_id": "meta-workspace",
                            "session_id": "meta-thread",
                            "channel": "web",
                            "actor": {"type": "user", "id": "meta-user", "display_name": "meta-user"},
                            "message": "meta message",
                        },
                    },
                )
            )

        self.assertEqual(captured["message"], "meta message")
        self.assertEqual(captured["workspace_id"], "meta-workspace")
        self.assertEqual(captured["thread_id"], "meta-thread")
        self.assertEqual(captured["agent_turn_request"].workspace_id, "meta-workspace")
        self.assertEqual(captured["session_ctx"]["request_id"], "req-meta-1")
        self.assertEqual(captured["session_ctx"]["client_request_id"], "req-meta-1")

    def test_build_chat_turn_event_stream_resumes_trace_context_from_request_meta(self) -> None:
        captured: dict[str, object] = {}
        services = self._runtime_services(SimpleNamespace())
        trace_context = agent_trace_service.TraceContext(
            trace_id="trace-1",
            workspace_id="meta-workspace",
            tenant_id="tenant-1",
            thread_id="meta-thread",
            run_id=None,
            root_agent_id="sage",
        )

        def _build_direct_operator_reply(**kwargs):
            captured.update(kwargs)
            return iter(())

        with mock.patch.object(
            direct_chat_runtime_service,
            "build_direct_operator_reply",
            side_effect=_build_direct_operator_reply,
        ), mock.patch.object(
            direct_chat_runtime_service,
            "run_async_tool_call",
            side_effect=lambda awaitable: (awaitable.close(), trace_context)[1],
        ):
            list(
                direct_chat_runtime_service.build_chat_turn_event_stream(
                    services=services,
                    session_ctx={"workspace_id": "meta-workspace", "thread_id": "meta-thread"},
                    message="fallback",
                    request_meta={
                        "workspace_id": "meta-workspace",
                        "thread_id": "meta-thread",
                        "trace": {
                            "trace_id": "trace-1",
                            "workspace_id": "meta-workspace",
                            "tenant_id": "tenant-1",
                            "thread_id": "meta-thread",
                            "root_agent_id": "sage",
                        },
                    },
                )
            )

        self.assertIs(captured["trace_context"], trace_context)

    def test_build_direct_operator_reply_returns_explicit_provider_unavailable_when_provider_changes(self) -> None:
        prepared = SimpleNamespace(
            normalized_message="hello",
            normalized_workspace_id="default",
            normalized_thread_id="thread-1",
            normalized_requested_provider="anthropic",
            normalized_requested_model="claude-3-7-sonnet",
            normalized_reasoning_effort="medium",
            compaction={},
            compacted_prior_messages=[],
            proactive_suggestions=["Connect Anthropic"],
            tool_loop_session_key="loop",
            availability_payload={"ai_ready": True},
            connected_systems=[],
            tool_capabilities=[],
            tools=[],
            approved_action_payload=None,
            base_context_used={"workspace_id": "default"},
            slash_command_name="",
            slash_remainder="",
            resolved_chat_max_iterations=3,
        )
        services = self._runtime_services(prepared)
        services.resolve_provider_for_direct_chat_message = lambda workspace_id, requested_provider, message, tools_present=False: ("openai", {"api_key": "sk-test"})

        events = list(
            direct_chat_runtime_service.build_direct_operator_reply(
                services=services,
                message="hello",
                workspace_id="default",
                requested_model="claude-3-7-sonnet",
                requested_provider="anthropic",
            )
        )

        final_events = [event for event in events if event.get("type") == "final"]
        self.assertEqual(len(final_events), 1)
        payload = final_events[0]["payload"]
        self.assertEqual(payload["mode"], "connect")
        self.assertEqual(payload["interventions"][0]["title"], "Anthropic is not available")
        self.assertEqual(payload["suggestions"], ["Connect Anthropic"])

    def test_build_direct_operator_reply_does_not_keyword_block_offline_my_computer_text(self) -> None:
        prepared = SimpleNamespace(
            normalized_message="find this file on my laptop",
            normalized_workspace_id="default",
            normalized_thread_id="thread-1",
            normalized_requested_provider="openai",
            normalized_requested_model="gpt-5.4",
            normalized_reasoning_effort="medium",
            compaction={},
            compacted_prior_messages=[],
            proactive_suggestions=[],
            tool_loop_session_key="loop",
            availability_payload={
                "ai_ready": True,
                "runtime_ok": False,
                "local_gateway_online": False,
                "capability_truth": {
                    "my_computer": {
                        "online": False,
                        "runtime_ok": False,
                        "local_tools_available": False,
                        "state": "offline",
                    },
                    "required_setup_actions": [
                        {
                            "id": "connect_my_computer",
                            "label": "Connect My Computer",
                            "href": "/integrations",
                            "reason": "My Computer is offline.",
                        }
                    ],
                },
            },
            connected_systems=[],
            tool_capabilities=[],
            tools=[],
            approved_action_payload=None,
            base_context_used={"workspace_id": "default"},
            slash_command_name="",
            slash_remainder="",
            resolved_chat_max_iterations=3,
        )
        services = self._runtime_services(prepared)
        services.resolve_provider_for_direct_chat_message = (
            lambda workspace_id, requested_provider, message, tools_present=False: ("openai", {"api_key": "sk-test"})
        )

        def _mock_provider_stream(**kwargs):
            yield {
                "type": "result",
                "reply": "Mock provider reply.",
                "provider": "openai",
                "model": "gpt-5.4",
                "attempted_providers": "openai",
                "error": "",
                "usage_masked": {},
            }

        services.direct_chat_generation_services.generate_chat_reply_stream_with_provider_fallback = _mock_provider_stream

        events = list(
            direct_chat_runtime_service.build_direct_operator_reply(
                services=services,
                message="find this file on my laptop",
                workspace_id="default",
                requested_model="gpt-5.4",
                requested_provider="openai",
            )
        )

        final_events = [event for event in events if event.get("type") == "final"]
        self.assertEqual(len(final_events), 1)
        payload = final_events[0]["payload"]
        self.assertEqual(payload["mode"], "answer")
        self.assertEqual(payload["reply"], "Mock provider reply.")
        self.assertFalse(payload.get("interventions"))

    def test_collect_direct_operator_reply_keeps_generic_capability_question_as_sage_chat(self) -> None:
        prepared = SimpleNamespace(
            normalized_message="what can you do here right now?",
            normalized_workspace_id="default",
            normalized_thread_id="thread-1",
            normalized_requested_provider="openai",
            normalized_requested_model="gpt-5.4",
            normalized_reasoning_effort="medium",
            compaction={},
            compacted_prior_messages=[],
            proactive_suggestions=[],
            tool_loop_session_key="loop",
            availability_payload={
                "ai_ready": True,
                "runtime_ok": False,
                "local_gateway_online": False,
                "capability_truth": {
                    "provider": {"label": "DeepSeek", "ai_ready": True},
                    "credits": {"hosted_enabled": True, "reason": ""},
                    "my_computer": {
                        "online": False,
                        "runtime_ok": False,
                        "local_tools_available": False,
                        "state": "offline",
                    },
                    "connected_apps": [
                        {"label": "Google Workspace", "connected": True, "runtime_usable": True},
                    ],
                    "channels": [
                        {"label": "Telegram", "connected": True, "runtime_usable": True},
                    ],
                    "byok_providers": [{"label": "DeepSeek"}, {"label": "Gemini"}, {"label": "OpenAI"}],
                    "required_setup_actions": [{"label": "Connect My Computer", "href": "/integrations"}],
                },
            },
            connected_systems=["Google Workspace", "Telegram"],
            tool_capabilities=[],
            tools=[
                {"name": "web__search", "description": "Search the web"},
                {"name": "telegram_bot__send_message", "description": "Send a Telegram message"},
            ],
            approved_action_payload=None,
            base_context_used={"workspace_id": "default"},
            slash_command_name="",
            slash_remainder="",
            resolved_chat_max_iterations=3,
        )
        services = self._runtime_services(prepared)
        captured: dict = {}

        def _provider_stream(**kwargs):
            captured.update(kwargs)
            return iter(
                [
                    {
                        "type": "result",
                        "reply": "I can help with connected apps, channels, and available tools from this workspace.",
                        "provider": "openai",
                        "model": "gpt-5.4",
                        "attempted_providers": "openai",
                        "error": "",
                        "usage_masked": {},
                    }
                ]
            )

        services.direct_chat_generation_services.generate_chat_reply_stream_with_provider_fallback = _provider_stream

        payload = direct_chat_runtime_service.collect_direct_operator_reply(
            services=services,
            message="what can you do here right now?",
            workspace_id="default",
            requested_model="gpt-5.4",
            requested_provider="openai",
        )

        self.assertEqual(payload["mode"], "answer")
        self.assertEqual(payload["reply"], "I can help with connected apps, channels, and available tools from this workspace.")
        observed_message = captured.get("normalized_message") or captured.get("message")
        if observed_message is not None:
            self.assertEqual(observed_message, "what can you do here right now?")

    def test_build_direct_operator_reply_returns_explicit_provider_unavailable_when_not_ready(self) -> None:
        prepared = SimpleNamespace(
            normalized_message="hello",
            normalized_workspace_id="default",
            normalized_thread_id="thread-1",
            normalized_requested_provider="anthropic",
            normalized_requested_model="claude-3-7-sonnet",
            normalized_reasoning_effort="medium",
            compaction={},
            compacted_prior_messages=[],
            proactive_suggestions=["Connect Anthropic"],
            tool_loop_session_key="loop",
            availability_payload={"ai_ready": True},
            connected_systems=[],
            tool_capabilities=[],
            tools=[],
            approved_action_payload=None,
            base_context_used={"workspace_id": "default"},
            slash_command_name="",
            slash_remainder="",
            resolved_chat_max_iterations=3,
        )
        services = self._runtime_services(prepared)
        services.resolve_provider_for_direct_chat_message = lambda workspace_id, requested_provider, message, tools_present=False: ("anthropic", {})
        services.supports_direct_message_native_chat = lambda provider, credentials: provider == "openai" and bool(credentials)
        services.supported_providers = ["openai", "anthropic"]

        events = list(
            direct_chat_runtime_service.build_direct_operator_reply(
                services=services,
                message="hello",
                workspace_id="default",
                requested_model="claude-3-7-sonnet",
                requested_provider="anthropic",
            )
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "final")
        payload = events[0]["payload"]
        self.assertEqual(payload["mode"], "connect")
        self.assertEqual(payload["interventions"][0]["title"], "Anthropic is not available")
        self.assertEqual(payload["suggestions"], ["Connect Anthropic"])

    def test_build_direct_operator_reply_locks_default_hosted_provider(self) -> None:
        prepared = SimpleNamespace(
            normalized_message="hello",
            normalized_workspace_id="default",
            normalized_thread_id="thread-1",
            normalized_requested_provider="",
            normalized_requested_model="",
            normalized_reasoning_effort="medium",
            compaction={},
            compacted_prior_messages=[],
            proactive_suggestions=[],
            tool_loop_session_key="loop",
            availability_payload={
                "ai_ready": True,
                "credential_plane": "platform_runtime",
                "platform_runtime_allowed": True,
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
            },
            connected_systems=[],
            tool_capabilities=[],
            tools=[],
            approved_action_payload=None,
            base_context_used={"workspace_id": "default"},
            slash_command_name="",
            slash_remainder="",
            resolved_chat_max_iterations=3,
        )
        services = self._runtime_services(prepared)
        services.supported_providers = ["openai", "deepseek"]
        services.resolve_provider_for_direct_chat_message = (
            lambda workspace_id, requested_provider, message, tools_present=False: ("deepseek", {"api_key": "sk-test"})
        )
        services.supports_direct_message_native_chat = lambda provider, credentials: provider == "deepseek" and bool(credentials)
        captured: dict[str, object] = {}

        def _provider_stream(**kwargs):
            captured["context"] = kwargs.get("context")
            captured["metadata"] = kwargs.get("metadata")
            yield {
                "type": "result",
                "reply": "ok",
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "tool_calls": [],
                "attempted_providers": "deepseek",
                "error": "",
                "usage_masked": {},
            }

        services.direct_chat_generation_services.generate_chat_reply_stream_with_provider_fallback = _provider_stream

        list(
            direct_chat_runtime_service.build_direct_operator_reply(
                services=services,
                message="hello",
                workspace_id="default",
                requested_model="",
                requested_provider="",
            )
        )

        self.assertTrue((captured["context"] or {}).get("disable_provider_fallback"))
        self.assertTrue((captured["metadata"] or {}).get("disable_provider_fallback"))
        self.assertEqual((captured["context"] or {}).get("provider"), "deepseek")
        self.assertEqual((captured["context"] or {}).get("model"), "deepseek-v4-flash")

    def test_build_direct_operator_reply_keeps_obvious_tool_intent_provider_backed_when_provider_ready(self) -> None:
        prepared = SimpleNamespace(
            normalized_message="List the files on my desktop.",
            normalized_workspace_id="default",
            normalized_thread_id="thread-1",
            normalized_requested_provider="deepseek",
            normalized_requested_model="deepseek-chat",
            normalized_reasoning_effort="medium",
            compaction={},
            compacted_prior_messages=[],
            proactive_suggestions=[],
            tool_loop_session_key="loop",
            availability_payload={"ai_ready": True},
            connected_systems=[],
            tool_capabilities=[],
            tools=[{"name": "file__read"}, {"name": "memory_search"}],
            approved_action_payload=None,
            base_context_used={"workspace_id": "default"},
            slash_command_name="",
            slash_remainder="",
            resolved_chat_max_iterations=3,
        )
        services = self._runtime_services(prepared)
        services.supported_providers = ["openai", "deepseek"]
        services.message_has_obvious_direct_tool_intent = lambda message, tools: True
        services.resolve_provider_for_direct_chat_message = (
            lambda workspace_id, requested_provider, message, tools_present=False: ("deepseek", {"api_key": "sk-test"})
        )
        services.supports_direct_message_native_chat = lambda provider, credentials: provider == "deepseek" and bool(credentials)
        captured: dict[str, object] = {}

        def _provider_stream(**kwargs):
            captured["context_tools"] = (kwargs.get("context") or {}).get("tools")
            captured["metadata_tools"] = (kwargs.get("metadata") or {}).get("tools")
            yield {
                "type": "result",
                "reply": "",
                "provider": "deepseek",
                "model": "deepseek-chat",
                "tool_calls": [{"id": "call-1", "name": "file__read", "arguments": {"path": "~/Desktop"}}],
                "attempted_providers": "deepseek",
                "error": "",
                "usage_masked": {},
            }

        services.direct_chat_generation_services.generate_chat_reply_stream_with_provider_fallback = _provider_stream

        events = list(
            direct_chat_runtime_service.build_direct_operator_reply(
                services=services,
                message="List the files on my desktop.",
                workspace_id="default",
                requested_model="deepseek-chat",
                requested_provider="deepseek",
            )
        )

        self.assertEqual(captured["context_tools"], [{"name": "file__read"}, {"name": "memory_search"}])
        self.assertEqual(captured["metadata_tools"], [{"name": "file__read"}, {"name": "memory_search"}])
        self.assertFalse(any(event.get("detail") == "Completed" and event.get("id") == "direct-tools:auto" for event in events))

    def test_build_direct_operator_reply_keeps_local_tools_for_assistant_plan_interception(self) -> None:
        prepared = SimpleNamespace(
            normalized_message="what about now?",
            normalized_workspace_id="ws-1",
            normalized_thread_id="thread-1",
            normalized_requested_provider="deepseek",
            normalized_requested_model="deepseek-chat",
            normalized_reasoning_effort="medium",
            compaction={},
            compacted_prior_messages=[],
            proactive_suggestions=[],
            tool_loop_session_key="loop",
            availability_payload={"ai_ready": True},
            connected_systems=[],
            tool_capabilities=[],
            tools=[{"name": "shell__exec"}],
            approved_action_payload=None,
            base_context_used={"workspace_id": "ws-1"},
            slash_command_name="",
            slash_remainder="",
            resolved_chat_max_iterations=3,
        )
        services = self._runtime_services(prepared)
        services.supported_providers = ["deepseek"]
        services.message_has_obvious_direct_tool_intent = lambda message, tools: False
        services.resolve_provider_for_direct_chat_message = (
            lambda workspace_id, requested_provider, message, tools_present=False: ("deepseek", {"api_key": "sk-test"})
        )
        services.supports_direct_message_native_chat = lambda provider, credentials: provider == "deepseek" and bool(credentials)
        captured: dict[str, object] = {}

        def _stream_provider_backed_direct_chat(**kwargs):
            captured["tools"] = kwargs.get("tools")
            captured["assistant_plan_tools"] = kwargs.get("assistant_plan_tools")
            return iter(
                [
                    {
                        "type": "final",
                        "payload": {"reply": "ok", "context_used": {}},
                    }
                ]
            )

        with mock.patch.object(
            direct_chat_runtime_service.direct_chat_generation_service,
            "stream_provider_backed_direct_chat",
            side_effect=_stream_provider_backed_direct_chat,
        ):
            list(
                direct_chat_runtime_service.build_direct_operator_reply(
                    services=services,
                    message=prepared.normalized_message,
                    workspace_id="ws-1",
                    requested_model="deepseek-chat",
                    requested_provider="deepseek",
                )
            )

        self.assertEqual(captured["tools"], [])
        self.assertEqual(captured["assistant_plan_tools"], [{"name": "shell__exec"}])

    def test_build_direct_operator_reply_exposes_web_tools_for_web_lookup_when_provider_ready(self) -> None:
        prepared = SimpleNamespace(
            normalized_message="do some web search!",
            normalized_workspace_id="ws-1",
            normalized_thread_id="thread-1",
            normalized_requested_provider="deepseek",
            normalized_requested_model="deepseek-chat",
            normalized_reasoning_effort="medium",
            compaction={},
            compacted_prior_messages=[],
            proactive_suggestions=[],
            tool_loop_session_key="loop",
            availability_payload={"ai_ready": True},
            connected_systems=[],
            tool_capabilities=[],
            tools=[
                {"name": "web__search", "description": "Search the web"},
                {"name": "web__fetch", "description": "Fetch a URL"},
                {"name": "shell__exec", "description": "Run shell"},
            ],
            approved_action_payload=None,
            base_context_used={"workspace_id": "ws-1"},
            slash_command_name="",
            slash_remainder="",
            resolved_chat_max_iterations=3,
        )
        services = self._runtime_services(prepared)
        services.supported_providers = ["deepseek"]
        services.message_has_obvious_direct_tool_intent = lambda message, tools: False
        services.resolve_provider_for_direct_chat_message = (
            lambda workspace_id, requested_provider, message, tools_present=False: ("deepseek", {"api_key": "sk-test"})
        )
        services.supports_direct_message_native_chat = lambda provider, credentials: provider == "deepseek" and bool(credentials)
        captured: dict[str, object] = {}

        def _stream_provider_backed_direct_chat(**kwargs):
            captured["tools"] = kwargs.get("tools")
            captured["assistant_plan_tools"] = kwargs.get("assistant_plan_tools")
            return iter(
                [
                    {
                        "type": "final",
                        "payload": {"reply": "ok", "context_used": {}},
                    }
                ]
            )

        with mock.patch.object(
            direct_chat_runtime_service.direct_chat_generation_service,
            "stream_provider_backed_direct_chat",
            side_effect=_stream_provider_backed_direct_chat,
        ):
            list(
                direct_chat_runtime_service.build_direct_operator_reply(
                    services=services,
                    message=prepared.normalized_message,
                    workspace_id="ws-1",
                    requested_model="deepseek-chat",
                    requested_provider="deepseek",
                )
            )

        self.assertEqual(
            captured["tools"],
            [
                {"name": "web__search", "description": "Search the web"},
                {"name": "web__fetch", "description": "Fetch a URL"},
            ],
        )
        self.assertEqual(captured["assistant_plan_tools"], prepared.tools)

    def test_build_direct_operator_reply_requires_agent_computer_for_browser_request(self) -> None:
        prepared = SimpleNamespace(
            normalized_message="open https://example.com in the browser",
            normalized_workspace_id="ws-1",
            normalized_thread_id="thread-1",
            normalized_requested_provider="deepseek",
            normalized_requested_model="deepseek-chat",
            normalized_reasoning_effort="medium",
            compaction={},
            compacted_prior_messages=[],
            proactive_suggestions=[],
            tool_loop_session_key="loop",
            availability_payload={
                "ai_ready": True,
                "runtime_ok": False,
                "local_gateway_online": False,
                "capability_truth": {"my_computer": {"state": "offline"}},
            },
            connected_systems=[],
            tool_capabilities=[],
            tools=[{"name": "browser__navigate", "description": "Navigate"}],
            approved_action_payload=None,
            base_context_used={"workspace_id": "ws-1"},
            slash_command_name="",
            slash_remainder="",
            resolved_chat_max_iterations=3,
        )
        services = self._runtime_services(prepared)
        services.supported_providers = ["deepseek"]
        services.resolve_provider_for_direct_chat_message = (
            lambda workspace_id, requested_provider, message, tools_present=False: ("deepseek", {"api_key": "sk-test"})
        )
        services.supports_direct_message_native_chat = lambda provider, credentials: provider == "deepseek" and bool(credentials)

        with mock.patch.object(
            direct_chat_runtime_service.direct_chat_generation_service,
            "stream_provider_backed_direct_chat",
            side_effect=AssertionError("provider should not be called"),
        ):
            events = list(
                direct_chat_runtime_service.build_direct_operator_reply(
                    services=services,
                    message=prepared.normalized_message,
                    workspace_id="ws-1",
                    requested_model="deepseek-chat",
                    requested_provider="deepseek",
                )
            )

        self.assertEqual(events[-1]["type"], "final")
        self.assertEqual(events[-1]["payload"]["reply"], "Choose Sage Agent Computer.")

    def test_build_direct_operator_reply_blocks_browser_request_when_selected_agent_computer_offline(self) -> None:
        prepared = SimpleNamespace(
            normalized_message="open https://example.com in the browser",
            normalized_workspace_id="ws-1",
            normalized_thread_id="thread-1",
            normalized_requested_provider="deepseek",
            normalized_requested_model="deepseek-chat",
            normalized_reasoning_effort="medium",
            compaction={},
            compacted_prior_messages=[],
            proactive_suggestions=[],
            tool_loop_session_key="loop",
            availability_payload={
                "ai_ready": True,
                "runtime_ok": False,
                "local_gateway_online": False,
                "selected_gateway_id": "gw-1",
                "capability_truth": {"my_computer": {"state": "offline"}},
            },
            connected_systems=[],
            tool_capabilities=[],
            tools=[{"name": "browser__navigate", "description": "Navigate"}],
            approved_action_payload=None,
            base_context_used={"workspace_id": "ws-1"},
            slash_command_name="",
            slash_remainder="",
            resolved_chat_max_iterations=3,
        )
        services = self._runtime_services(prepared)
        services.supported_providers = ["deepseek"]
        services.resolve_provider_for_direct_chat_message = (
            lambda workspace_id, requested_provider, message, tools_present=False: ("deepseek", {"api_key": "sk-test"})
        )
        services.supports_direct_message_native_chat = lambda provider, credentials: provider == "deepseek" and bool(credentials)

        events = list(
            direct_chat_runtime_service.build_direct_operator_reply(
                services=services,
                message=prepared.normalized_message,
                workspace_id="ws-1",
                requested_model="deepseek-chat",
                requested_provider="deepseek",
            )
        )

        self.assertEqual(events[-1]["type"], "final")
        self.assertEqual(events[-1]["payload"]["reply"], "Selected Sage Agent Computer is offline.")

    def test_build_direct_operator_reply_forces_explicit_tool_request_even_when_provider_ready(self) -> None:
        prepared = SimpleNamespace(
            normalized_message="Use the local shell tool to run ls ~/Desktop and return the result exactly.",
            normalized_workspace_id="default",
            normalized_thread_id="thread-1",
            normalized_requested_provider="ollama",
            normalized_requested_model="qwen2.5:1.5b",
            normalized_reasoning_effort="medium",
            compaction={},
            compacted_prior_messages=[],
            proactive_suggestions=[],
            tool_loop_session_key="loop",
            availability_payload={"ai_ready": True},
            connected_systems=[],
            tool_capabilities=[],
            tools=[{"name": "shell__exec"}],
            approved_action_payload=None,
            base_context_used={"workspace_id": "default"},
            slash_command_name="",
            slash_remainder="",
            resolved_chat_max_iterations=3,
        )
        services = self._runtime_services(prepared)
        services.supported_providers = ["ollama"]
        services.message_has_obvious_direct_tool_intent = lambda message, tools: True
        services.resolve_provider_for_direct_chat_message = (
            lambda workspace_id, requested_provider, message, tools_present=False: ("ollama", {})
        )
        services.supports_direct_message_native_chat = lambda provider, credentials: provider == "ollama"
        services.no_provider_execution_services.parse_tool_name = lambda name: tuple(str(name).split("__", 1))
        services.no_provider_execution_services.tool_arguments_payload = (
            lambda arguments: arguments if isinstance(arguments, dict) else {}
        )
        executed: dict[str, object] = {}

        def _execute_single_tool_call(**kwargs):
            executed.update(kwargs)
            return "Desktop file A\nDesktop file B"

        services.no_provider_execution_services.execute_single_tool_call = _execute_single_tool_call
        services.direct_chat_generation_services.generate_chat_reply_stream_with_provider_fallback = (
            lambda **kwargs: (_ for _ in ()).throw(AssertionError("provider generation should not run"))
        )

        events = list(
            direct_chat_runtime_service.build_direct_operator_reply(
                services=services,
                message=prepared.normalized_message,
                workspace_id="default",
                requested_model="qwen2.5:1.5b",
                requested_provider="ollama",
            )
        )

        self.assertEqual(executed["tool_call"]["name"], "shell__exec")
        self.assertEqual(executed["tool_call"]["arguments"]["command"], "ls ~/Desktop")
        self.assertEqual(events[-1]["type"], "final")
        self.assertIn("Desktop file A", events[-1]["payload"]["reply"])
        self.assertEqual(events[-1]["payload"]["provider"], "ollama")
        self.assertEqual(events[-1]["payload"]["context_used"]["effective_provider"], "ollama")

    def test_build_direct_operator_reply_forces_working_directory_request_even_when_provider_ready(self) -> None:
        prepared = SimpleNamespace(
            normalized_message="what folder are you in ?",
            normalized_workspace_id="ws-1",
            normalized_thread_id="thread-1",
            normalized_requested_provider="deepseek",
            normalized_requested_model="deepseek-chat",
            normalized_reasoning_effort="medium",
            compaction={},
            compacted_prior_messages=[],
            proactive_suggestions=[],
            tool_loop_session_key="loop",
            availability_payload={"ai_ready": True},
            connected_systems=[],
            tool_capabilities=[],
            tools=[{"name": "shell__exec"}],
            approved_action_payload=None,
            base_context_used={"workspace_id": "ws-1"},
            slash_command_name="",
            slash_remainder="",
            resolved_chat_max_iterations=3,
        )
        services = self._runtime_services(prepared)
        services.supported_providers = ["deepseek"]
        services.message_has_obvious_direct_tool_intent = lambda message, tools: True
        services.resolve_provider_for_direct_chat_message = (
            lambda workspace_id, requested_provider, message, tools_present=False: ("deepseek", {"api_key": "sk-test"})
        )
        services.supports_direct_message_native_chat = lambda provider, credentials: provider == "deepseek" and bool(credentials)
        services.no_provider_execution_services.parse_tool_name = lambda name: tuple(str(name).split("__", 1))
        services.no_provider_execution_services.tool_arguments_payload = (
            lambda arguments: arguments if isinstance(arguments, dict) else {}
        )
        executed: dict[str, object] = {}

        def _execute_single_tool_call(**kwargs):
            executed.update(kwargs)
            return "/Users/mansur/Multi_Agent_Orchestrator_Project"

        services.no_provider_execution_services.execute_single_tool_call = _execute_single_tool_call
        services.direct_chat_generation_services.generate_chat_reply_stream_with_provider_fallback = (
            lambda **kwargs: (_ for _ in ()).throw(AssertionError("provider generation should not run"))
        )

        events = list(
            direct_chat_runtime_service.build_direct_operator_reply(
                services=services,
                message=prepared.normalized_message,
                workspace_id="ws-1",
                requested_model="deepseek-chat",
                requested_provider="deepseek",
            )
        )

        self.assertEqual(executed["tool_call"]["name"], "shell__exec")
        self.assertEqual(executed["tool_call"]["arguments"]["command"], "pwd")
        self.assertEqual(events[-1]["type"], "final")
        self.assertIn("/Users/mansur/Multi_Agent_Orchestrator_Project", events[-1]["payload"]["reply"])

    def test_build_direct_operator_reply_orders_system_identity_and_workspace_context(self) -> None:
        prepared = SimpleNamespace(
            normalized_message="hello",
            normalized_workspace_id="default",
            normalized_thread_id="thread-1",
            normalized_requested_provider="openai",
            normalized_requested_model="gpt-5.4",
            normalized_reasoning_effort="medium",
            compaction={},
            compacted_prior_messages=[],
            proactive_suggestions=[],
            tool_loop_session_key="loop",
            availability_payload={"ai_ready": True},
            connected_systems=[],
            tool_capabilities=[],
            tools=[],
            approved_action_payload=None,
            base_context_used={"workspace_id": "default"},
            slash_command_name="",
            slash_remainder="",
            resolved_chat_max_iterations=1,
        )
        services = self._runtime_services(prepared)
        services.direct_chat_workspace_context_text = (
            lambda workspace_id, memory_query="": "Stable Memory Summary\nStable block\n\nRetrieved Relevant Memory\nDynamic block"
        )
        captured: dict[str, object] = {}

        def _provider_stream(**kwargs):
            captured["system_prompt"] = kwargs.get("system_prompt")
            yield {
                "type": "result",
                "reply": "ok",
                "provider": "openai",
                "model": "gpt-5.4",
                "tool_calls": [],
                "attempted_providers": "openai",
                "error": "",
                "usage_masked": {},
            }

        services.direct_chat_generation_services.generate_chat_reply_stream_with_provider_fallback = _provider_stream

        list(
            direct_chat_runtime_service.build_direct_operator_reply(
                services=services,
                message="hello",
                workspace_id="default",
                requested_model="gpt-5.4",
                requested_provider="openai",
            )
        )

        assembled = str(captured.get("system_prompt") or "")
        system_index = assembled.index("system prompt")
        identity_index = assembled.index("## Runtime Environment")
        workspace_index = assembled.index("## Workspace Context")
        stable_index = assembled.index("Stable Memory Summary")
        retrieved_index = assembled.index("Retrieved Relevant Memory")
        self.assertLess(system_index, identity_index)
        self.assertLess(identity_index, workspace_index)
        self.assertLess(stable_index, retrieved_index)

    def test_build_direct_operator_reply_calls_rust_stream_chat_gate_before_preview(self) -> None:
        prepared = SimpleNamespace(
            normalized_message="hello",
            normalized_workspace_id="default",
            normalized_thread_id="thread-1",
            normalized_requested_provider="openai",
            normalized_requested_model="gpt-5.4",
            normalized_reasoning_effort="medium",
            compaction={},
            compacted_prior_messages=[],
            proactive_suggestions=["next"],
            tool_loop_session_key="loop",
            availability_payload={"ai_ready": False},
            connected_systems=[],
            tool_capabilities=[],
            tools=[],
            approved_action_payload=None,
            base_context_used={"workspace_id": "default"},
            slash_command_name="",
            slash_remainder="",
            resolved_chat_max_iterations=3,
        )
        services = self._runtime_services(prepared)
        services.plan_direct_chat_route = lambda **kwargs: SimpleNamespace(
            allow_direct_tool_calls=False,
            preview={"reply": "preview", "actions": [], "mode": "answer", "error": ""},
            should_auto_start_run=False,
        )

        with mock.patch.object(
            direct_chat_runtime_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={"next_action": "start_chat_stream"},
        ) as rust_mock:
            events = list(
                direct_chat_runtime_service.build_direct_operator_reply(
                    services=services,
                    message="hello",
                    workspace_id="default",
                    requested_model="gpt-5.4",
                    requested_provider="openai",
                )
            )

        self.assertEqual(events[-1]["payload"]["reply"], "preview")
        self.assertEqual(rust_mock.call_args.args[0], "run-api-decision")
        self.assertEqual(rust_mock.call_args.args[1]["operation"], "stream_chat")
        self.assertEqual(rust_mock.call_args.args[1]["workspace_id"], "default")

    def test_build_direct_operator_reply_wrong_rust_action_blocks_before_route_planning(self) -> None:
        prepared = SimpleNamespace(
            normalized_message="hello",
            normalized_workspace_id="default",
            normalized_thread_id="thread-1",
            normalized_requested_provider="openai",
            normalized_requested_model="gpt-5.4",
            normalized_reasoning_effort="medium",
            compaction={},
            compacted_prior_messages=[],
            proactive_suggestions=["next"],
            tool_loop_session_key="loop",
            availability_payload={"ai_ready": False},
            connected_systems=[],
            tool_capabilities=[],
            tools=[],
            approved_action_payload=None,
            base_context_used={"workspace_id": "default"},
            slash_command_name="",
            slash_remainder="",
            resolved_chat_max_iterations=3,
        )
        services = self._runtime_services(prepared)
        route_called = {"value": False}

        def _plan_direct_chat_route(**kwargs):
            route_called["value"] = True
            return SimpleNamespace(
                allow_direct_tool_calls=False,
                preview={"reply": "preview", "actions": [], "mode": "answer", "error": ""},
                should_auto_start_run=False,
            )

        services.plan_direct_chat_route = _plan_direct_chat_route

        with mock.patch.object(
            direct_chat_runtime_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={"next_action": "start_run"},
        ):
            events = list(
                direct_chat_runtime_service.build_direct_operator_reply(
                    services=services,
                    message="hello",
                    workspace_id="default",
                    requested_model="gpt-5.4",
                    requested_provider="openai",
                )
            )

        self.assertFalse(route_called["value"])
        self.assertEqual(events[-1]["payload"]["error"], "unexpected_next_action:start_run")


if __name__ == "__main__":
    unittest.main()
