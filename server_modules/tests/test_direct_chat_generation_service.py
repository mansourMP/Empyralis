import unittest
from unittest import mock

from server_modules import agent_trace_service
from server_modules import direct_chat_generation_service


class DirectChatGenerationServiceTests(unittest.TestCase):
    def test_extract_assistant_shell_plan_tool_call_does_not_convert_command_blocks(self) -> None:
        reply = (
            "Running the commands now.\n\n"
            "```bash\n"
            "uname -a && sw_vers\n"
            "```\n\n"
            "```bash\n"
            "# apps\n"
            "ls /Applications | head -20\n"
            "```"
        )

        clean_reply, tool_calls = direct_chat_generation_service._extract_assistant_shell_plan_tool_call(
            reply,
            [{"name": "shell__exec", "parameters": {"type": "object"}}],
        )

        self.assertEqual(clean_reply, reply)
        self.assertEqual(tool_calls, [])

    def test_extract_assistant_shell_plan_tool_call_does_not_convert_inline_command_blocks(self) -> None:
        reply = (
            "Looks like the commands ran but the output didn't come through clearly. "
            "Let me re-run them and show you the results directly.\n\n"
            "`uname -a && echo \"===OS===\" && sw_vers 2>/dev/null || cat /etc/os-release 2>/dev/null`\n\n"
            "`echo \"===CPU & RAM===\" && sysctl -n hw.memsize 2>/dev/null && echo \"bytes RAM\"`\n\n"
            "`echo \"===APPLICATIONS===\" && ls /Applications 2>/dev/null | head -80`"
        )

        clean_reply, tool_calls = direct_chat_generation_service._extract_assistant_shell_plan_tool_call(
            reply,
            [{"name": "shell__exec", "parameters": {"type": "object"}}],
        )

        self.assertEqual(clean_reply, reply)
        self.assertEqual(tool_calls, [])

    def test_extract_assistant_shell_plan_tool_call_does_not_normalize_language_prefixed_inline_commands(self) -> None:
        reply = (
            "Let me run them fresh and capture the output properly this time. "
            "`bash uname -a && echo \"---OS---\" && sw_vers 2>/dev/null` "
            "`bash echo \"---DISK---\" && df -h / 2>/dev/null`"
        )

        clean_reply, tool_calls = direct_chat_generation_service._extract_assistant_shell_plan_tool_call(
            reply,
            [{"name": "shell__exec", "parameters": {"type": "object"}}],
        )

        self.assertEqual(clean_reply, reply)
        self.assertEqual(tool_calls, [])

    def test_extract_assistant_shell_plan_tool_call_ignores_without_shell_tool(self) -> None:
        reply = "Running the commands now.\n\n```bash\nuname -a\n```"

        clean_reply, tool_calls = direct_chat_generation_service._extract_assistant_shell_plan_tool_call(reply, [])

        self.assertEqual(clean_reply, reply)
        self.assertEqual(tool_calls, [])

    def _services(self, *, stream_events):
        return direct_chat_generation_service.DirectChatGenerationServices(
            thinking_step_payload=lambda iteration, status, detail=None: {
                "type": "step",
                "iteration": iteration,
                "status": status,
                "detail": detail,
            },
            build_context_used=lambda **kwargs: kwargs,
            build_direct_tool_approval_response=lambda **kwargs: None,
            parse_tool_name=lambda name: tuple(str(name).split("__", 1)) if "__" in str(name) else ("", ""),
            tool_arguments_payload=lambda value: value if isinstance(value, dict) else {},
            parse_page_state=lambda value: {},
            direct_tool_step_payload=lambda connector_id, action_id, arguments, **kwargs: {
                "type": "step",
                "connector": connector_id,
                "action": action_id,
                "arguments": arguments,
                **kwargs,
            },
            execute_single_direct_tool_call=lambda **kwargs: "tool result",
            direct_tool_followup_message=lambda tool_name, result_text: f"{tool_name}: {result_text}",
            suggest_actions=lambda _message, _availability: [{"kind": "open"}],
            clear_direct_tool_loop_state=lambda _session_key: None,
            persist_direct_chat_memory_best_effort=lambda **kwargs: None,
            persist_direct_chat_transcript_best_effort=lambda **kwargs: None,
            persist_direct_chat_hosted_usage_best_effort=lambda **kwargs: None,
            record_direct_tool_signature=lambda _session_key, _tool_call: False,
            direct_chat_error_reply=lambda error: f"Chat failed: {error}",
            capture_exception=lambda exc: None,
            generate_chat_reply_stream_with_provider_fallback=lambda **kwargs: iter(stream_events),
        )

    def test_stream_provider_backed_direct_chat_buffers_shell_plan_chunks(self) -> None:
        command_reply = (
            "Looks like the commands ran but the output didn't come through clearly. "
            "Let me re-run them and show you the results directly.\n\n"
            "```bash\n"
            "uname -a && sw_vers\n"
            "```\n\n"
            "```bash\n"
            "ls /Applications | head -20\n"
            "```"
        )
        stream_rounds = iter(
            [
                [
                    {"type": "chunk", "delta": command_reply[:80]},
                    {"type": "chunk", "delta": command_reply[80:]},
                    {
                        "type": "result",
                        "reply": command_reply,
                        "usage_masked": {"provider": "openai"},
                        "provider": "openai",
                        "model": "gpt-5.4",
                        "attempted_providers": "openai",
                        "error": "",
                        "tool_calls": [],
                    },
                ],
                [
                    {
                        "type": "result",
                        "reply": "The local command finished.",
                        "usage_masked": {"provider": "openai"},
                        "provider": "openai",
                        "model": "gpt-5.4",
                        "attempted_providers": "openai",
                        "error": "",
                        "tool_calls": [],
                    }
                ],
            ]
        )
        services = self._services(stream_events=[])
        services.generate_chat_reply_stream_with_provider_fallback = lambda **kwargs: iter(next(stream_rounds))
        executed: list[str] = []

        def _execute_single_direct_tool_call(**kwargs):
            command = kwargs.get("tool_call", {}).get("arguments", {}).get("command", "")
            executed.append(command)
            return "Darwin\nApplications"

        services.execute_single_direct_tool_call = _execute_single_direct_tool_call

        events = list(
            direct_chat_generation_service.stream_provider_backed_direct_chat(
                services=services,
                context={"provider": "openai"},
                metadata={"provider": "openai", "model": "gpt-5.4"},
                system_prompt="System prompt",
                normalized_workspace_id="default",
                normalized_requested_provider="openai",
                normalized_requested_model="gpt-5.4",
                normalized_reasoning_effort="medium",
                normalized_thread_id="thread-1",
                normalized_message="Check my machine.",
                compacted_prior_messages=[],
                prior_messages_used=False,
                history_mode="none",
                connected_systems=[],
                tool_capabilities=[],
                availability_payload={"ai_ready": True},
                tools=[],
                direct_chat_credentials={},
                proactive_suggestions=[],
                tool_loop_session_key="session-1",
                fallback_reason=None,
                session_ctx=None,
                trace_context=None,
                resolved_chat_max_iterations=3,
                direct_tool_result_summary_system_message="Summarize tool results.",
                assistant_plan_tools=[{"name": "shell__exec"}],
            )
        )

        streamed_text = "".join(str(event.get("delta") or "") for event in events if event.get("type") == "chunk")
        self.assertNotIn("uname -a", streamed_text)
        self.assertEqual(executed, [])
        self.assertIn("Looks like the commands ran", events[-1]["payload"]["reply"])
        self.assertIn("uname -a && sw_vers", events[-1]["payload"]["reply"])

    def test_stream_provider_backed_direct_chat_never_streams_split_fullwidth_dsml(self) -> None:
        dsml_reply = (
            "Let me check.\n"
            "<｜｜DSML｜｜tool_calls>"
            "<｜｜DSML｜｜invoke name=\"bash\">"
            "<｜｜DSML｜｜parameter name=\"command\" string=\"true\">system_profiler</｜｜DSML｜｜parameter>"
            "</｜｜DSML｜｜invoke>"
        )

        events = list(
            direct_chat_generation_service.stream_provider_backed_direct_chat(
                services=self._services(
                    stream_events=[
                        {"type": "chunk", "delta": "Let me check.\n<"},
                        {"type": "chunk", "delta": "｜｜"},
                        {"type": "chunk", "delta": "DSML｜｜tool_calls>"},
                        {
                            "type": "result",
                            "reply": dsml_reply,
                            "usage_masked": {"provider": "deepseek"},
                            "provider": "deepseek",
                            "model": "deepseek-chat",
                            "attempted_providers": "deepseek",
                            "error": "",
                            "tool_calls": [],
                        },
                    ]
                ),
                context={"provider": "deepseek"},
                metadata={"provider": "deepseek", "model": "deepseek-chat"},
                system_prompt="System prompt",
                normalized_workspace_id="default",
                normalized_requested_provider="deepseek",
                normalized_requested_model="deepseek-chat",
                normalized_reasoning_effort="medium",
                normalized_thread_id="thread-1",
                normalized_message="check what hardware I have on my Mac",
                compacted_prior_messages=[],
                prior_messages_used=False,
                history_mode="none",
                connected_systems=[],
                tool_capabilities=[],
                availability_payload={"ai_ready": True},
                tools=[],
                direct_chat_credentials={},
                proactive_suggestions=[],
                tool_loop_session_key="session-1",
                fallback_reason=None,
                session_ctx=None,
                trace_context=None,
                resolved_chat_max_iterations=3,
                direct_tool_result_summary_system_message="Summarize tool results.",
            )
        )

        streamed_text = "".join(str(event.get("delta") or "") for event in events if event.get("type") == "chunk")
        self.assertNotIn("DSML", streamed_text)
        self.assertNotIn("tool_calls", streamed_text)
        self.assertNotIn("system_profiler", streamed_text)
        self.assertEqual(events[-1]["type"], "final")
        self.assertEqual(events[-1]["payload"]["reply"], "Let me check.")
        self.assertTrue(events[-1]["payload"]["response_leak_guard"]["redacted"])

    def test_stream_provider_backed_direct_chat_returns_final_answer(self) -> None:
        events = list(
            direct_chat_generation_service.stream_provider_backed_direct_chat(
                services=self._services(
                    stream_events=[
                        {
                            "type": "result",
                            "reply": "Hello",
                            "usage_masked": {"provider": "openai"},
                            "provider": "openai",
                            "model": "gpt-5.4",
                            "attempted_providers": "openai",
                            "error": "",
                            "tool_calls": [],
                        }
                    ]
                ),
                context={"provider": "openai"},
                metadata={"provider": "openai", "model": "gpt-5.4"},
                system_prompt="System prompt",
                normalized_workspace_id="default",
                normalized_requested_provider="openai",
                normalized_requested_model="gpt-5.4",
                normalized_reasoning_effort="medium",
                normalized_thread_id="thread-1",
                normalized_message="hello",
                compacted_prior_messages=[],
                prior_messages_used=False,
                history_mode="none",
                connected_systems=[],
                tool_capabilities=[],
                availability_payload={"ai_ready": True},
                tools=[],
                direct_chat_credentials={},
                proactive_suggestions=["next"],
                tool_loop_session_key="session-1",
                fallback_reason=None,
                session_ctx=None,
                trace_context=None,
                resolved_chat_max_iterations=3,
                direct_tool_result_summary_system_message="Summarize tool results.",
            )
        )

        self.assertEqual(events[0]["type"], "step")
        self.assertEqual(events[-1]["type"], "final")
        self.assertEqual(events[-1]["payload"]["reply"], "Hello")
        self.assertEqual(events[-1]["payload"]["provider"], "openai")
        self.assertFalse(events[-1]["payload"]["response_leak_guard"]["redacted"])

    def _capture_system_prompt(self, *, model: str, provider: str = "anthropic") -> str:
        """Runs stream_provider_backed_direct_chat with a requested reasoning
        effort and returns the system_prompt that actually reached
        generate_chat_reply_stream_with_provider_fallback — the observable
        signal of whether supports_reasoning treated *model* as reasoning-
        native (system_prompt unchanged) or degraded it (system_prompt gets
        a "[System Instruction: ...]" suffix appended)."""
        captured: dict = {}
        services = self._services(
            stream_events=[
                {
                    "type": "result",
                    "reply": "ok",
                    "usage_masked": {"provider": provider},
                    "provider": provider,
                    "model": model,
                    "attempted_providers": provider,
                    "error": "",
                    "tool_calls": [],
                }
            ]
        )
        _passthrough = services.generate_chat_reply_stream_with_provider_fallback

        def _capture(**kwargs):
            captured.update(kwargs)
            return _passthrough(**kwargs)

        services.generate_chat_reply_stream_with_provider_fallback = _capture

        list(
            direct_chat_generation_service.stream_provider_backed_direct_chat(
                services=services,
                context={"provider": provider},
                metadata={"provider": provider, "model": model},
                system_prompt="Base system prompt",
                normalized_workspace_id="default",
                normalized_requested_provider=provider,
                normalized_requested_model=model,
                normalized_reasoning_effort="high",
                normalized_thread_id="thread-1",
                normalized_message="hello",
                compacted_prior_messages=[],
                prior_messages_used=False,
                history_mode="none",
                connected_systems=[],
                tool_capabilities=[],
                availability_payload={"ai_ready": True},
                tools=[],
                direct_chat_credentials={},
                proactive_suggestions=[],
                tool_loop_session_key="session-1",
                fallback_reason=None,
                session_ctx=None,
                trace_context=None,
                resolved_chat_max_iterations=3,
                direct_tool_result_summary_system_message="Summarize tool results.",
            )
        )
        return str(captured.get("system_prompt") or "")

    def test_supports_reasoning_now_recognizes_claude_models_as_native(self) -> None:
        """Reconciled with the frontend's inferReasoningLevels(), which
        already offers a reasoning picker for Claude models — this used to
        be stale here and silently degraded to a system-prompt instruction
        instead, contradicting what the UI promised."""
        system_prompt = self._capture_system_prompt(model="claude-sonnet-4-6")
        self.assertEqual(system_prompt, "Base system prompt")

    def test_supports_reasoning_now_recognizes_current_gpt5_and_codex_models_as_native(self) -> None:
        for model in ("gpt-5.4", "gpt-5.3-codex", "openai/gpt-5.4"):
            with self.subTest(model=model):
                system_prompt = self._capture_system_prompt(model=model, provider="openai")
                self.assertEqual(system_prompt, "Base system prompt")

    def test_supports_reasoning_still_degrades_an_unrecognized_model_to_a_system_instruction(self) -> None:
        """An honest fallback for a model this detection genuinely doesn't
        know about — proves the fix widened the allowlist without making
        every model look reasoning-native."""
        system_prompt = self._capture_system_prompt(model="llama-3.3-70b-versatile", provider="groq")
        self.assertIn("[System Instruction:", system_prompt)
        self.assertIn("high reasoning effort", system_prompt)

    def test_stream_provider_backed_direct_chat_masks_platform_paid_metadata(self) -> None:
        persisted_usage: dict[str, object] = {}
        services = self._services(
            stream_events=[
                {
                    "type": "result",
                    "reply": "I am running on Empyralis Pro AI.",
                    "usage_masked": {
                        "pricing_source": "https://api-docs.deepseek.com/quick_start/pricing/",
                        "usage_accounting": {
                            "input_tokens": 10,
                            "output_tokens": 4,
                            "total_tokens": 14,
                            "effective_provider": "deepseek",
                            "effective_model": "deepseek-v4-pro",
                            "pricing_source": "https://api-docs.deepseek.com/quick_start/pricing/",
                        }
                    },
                    "provider": "deepseek",
                    "model": "deepseek-v4-pro",
                    "attempted_providers": "deepseek",
                    "error": "",
                    "tool_calls": [],
                }
            ]
        )
        services.persist_direct_chat_hosted_usage_best_effort = lambda **kwargs: persisted_usage.update(kwargs)

        events = list(
            direct_chat_generation_service.stream_provider_backed_direct_chat(
                services=services,
                context={"provider": "deepseek"},
                metadata={
                    "provider": "deepseek",
                    "model": "deepseek-v4-pro",
                    "billing_source": "empyralis_credits",
                    "ai_tier": "pro",
                },
                system_prompt="System prompt",
                normalized_workspace_id="default",
                normalized_requested_provider="deepseek",
                normalized_requested_model="deepseek-v4-pro",
                normalized_reasoning_effort="high",
                normalized_thread_id="thread-1",
                normalized_message="what model are you?",
                compacted_prior_messages=[],
                prior_messages_used=False,
                history_mode="none",
                connected_systems=[],
                tool_capabilities=[],
                availability_payload={"ai_ready": True, "credential_plane": "platform_runtime"},
                tools=[],
                direct_chat_credentials={},
                proactive_suggestions=["Use my saved context: The user expects the model to be DeepSeek."],
                tool_loop_session_key="session-1",
                fallback_reason=None,
                session_ctx=None,
                trace_context=None,
                resolved_chat_max_iterations=3,
                direct_tool_result_summary_system_message="Summarize tool results.",
            )
        )

        payload = events[-1]["payload"]
        self.assertIsNone(payload["provider"])
        self.assertIsNone(payload["model"])
        self.assertIsNone(payload["attempted_providers"])
        self.assertEqual(payload["billing_source"], "empyralis_credits")
        self.assertEqual(payload["ai_tier"], "pro")
        self.assertEqual(payload["ai_label"], "Pro AI")
        self.assertIsNone(payload["context_used"]["effective_provider"])
        self.assertIsNone(payload["context_used"]["effective_model"])
        self.assertNotIn("deepseek", str(payload.get("usage_masked")).lower())
        self.assertNotIn("pricing_source", payload["usage_masked"])
        self.assertNotIn("deepseek", str(payload.get("suggestions")).lower())
        self.assertEqual(persisted_usage["effective_provider"], "deepseek")
        self.assertEqual(persisted_usage["effective_model"], "deepseek-v4-pro")

    def test_stream_provider_backed_direct_chat_redacts_response_leaks(self) -> None:
        events = list(
            direct_chat_generation_service.stream_provider_backed_direct_chat(
                services=self._services(
                    stream_events=[
                        {
                            "type": "result",
                            "reply": "Sensitivity: RED\nAPI key is sk-testsecret123",
                            "usage_masked": {"provider": "openai"},
                            "provider": "openai",
                            "model": "gpt-5.4",
                            "attempted_providers": "openai",
                            "error": "",
                            "tool_calls": [],
                        }
                    ]
                ),
                context={"provider": "openai"},
                metadata={"provider": "openai", "model": "gpt-5.4"},
                system_prompt="System prompt",
                normalized_workspace_id="default",
                normalized_requested_provider="openai",
                normalized_requested_model="gpt-5.4",
                normalized_reasoning_effort="medium",
                normalized_thread_id="thread-1",
                normalized_message="hello",
                compacted_prior_messages=[],
                prior_messages_used=False,
                history_mode="none",
                connected_systems=[],
                tool_capabilities=[],
                availability_payload={"ai_ready": True},
                tools=[],
                direct_chat_credentials={},
                proactive_suggestions=[],
                tool_loop_session_key="session-1",
                fallback_reason=None,
                session_ctx=None,
                trace_context=None,
                resolved_chat_max_iterations=1,
                direct_tool_result_summary_system_message="Summarize tool results.",
            )
        )

        final_payload = events[-1]["payload"]
        self.assertNotIn("sk-testsecret123", final_payload["reply"])
        self.assertTrue(final_payload["response_leak_guard"]["redacted"])
        self.assertIn("red_sensitivity_label", final_payload["response_leak_guard"]["findings"])

    def test_stream_provider_backed_direct_chat_persists_hosted_usage_before_final(self) -> None:
        recorded: list[dict[str, object]] = []
        services = self._services(
            stream_events=[
                {
                    "type": "result",
                    "reply": "Hello",
                    "usage_masked": {
                        "usage_accounting": {
                            "input_tokens": 11,
                            "output_tokens": 7,
                            "total_tokens": 18,
                            "estimated_cost_usd": 0.0012,
                            "effective_provider": "deepseek",
                            "effective_model": "deepseek-chat",
                        }
                    },
                    "provider": "deepseek",
                    "model": "deepseek-chat",
                    "attempted_providers": "deepseek",
                    "error": "",
                    "tool_calls": [],
                }
            ]
        )
        services.persist_direct_chat_hosted_usage_best_effort = lambda **kwargs: recorded.append(dict(kwargs))

        events = list(
            direct_chat_generation_service.stream_provider_backed_direct_chat(
                services=services,
                context={"provider": "deepseek"},
                metadata={"provider": "deepseek", "model": "deepseek-chat"},
                system_prompt="System prompt",
                normalized_workspace_id="ws-1",
                normalized_requested_provider="deepseek",
                normalized_requested_model="deepseek-chat",
                normalized_reasoning_effort="medium",
                normalized_thread_id="thread-1",
                normalized_message="hello",
                compacted_prior_messages=[],
                prior_messages_used=False,
                history_mode="none",
                connected_systems=[],
                tool_capabilities=[],
                availability_payload={"ai_ready": True, "credential_plane": "platform_runtime", "platform_runtime_allowed": True},
                tools=[],
                direct_chat_credentials={},
                proactive_suggestions=["next"],
                tool_loop_session_key="session-1",
                fallback_reason=None,
                session_ctx={"request_id": "req-1"},
                trace_context=None,
                resolved_chat_max_iterations=3,
                direct_tool_result_summary_system_message="Summarize tool results.",
            )
        )

        self.assertEqual(events[-1]["type"], "final")
        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0]["workspace_id"], "ws-1")
        self.assertEqual(recorded[0]["thread_id"], "thread-1")
        self.assertEqual(recorded[0]["effective_provider"], "deepseek")

    def test_stream_provider_backed_direct_chat_marks_reservation_uncertain_on_settlement_failure(self) -> None:
        released: list[dict[str, object]] = []
        cleared: list[str] = []
        services = self._services(
            stream_events=[
                {
                    "type": "result",
                    "reply": "Hello",
                    "usage_masked": {
                        "usage_accounting": {
                            "input_tokens": 11,
                            "output_tokens": 7,
                            "total_tokens": 18,
                            "estimated_cost_usd": 0.0012,
                            "effective_provider": "deepseek",
                            "effective_model": "deepseek-chat",
                        }
                    },
                    "provider": "deepseek",
                    "model": "deepseek-chat",
                    "attempted_providers": "deepseek",
                    "error": "",
                    "tool_calls": [],
                }
            ]
        )
        services.reserve_direct_chat_hosted_usage_best_effort = lambda **kwargs: {
            "reservation_key": "tenant-1:ws-1:req-1:sage_direct_chat",
            "status": "active",
        }
        services.persist_direct_chat_hosted_usage_best_effort = lambda **kwargs: (_ for _ in ()).throw(
            RuntimeError("Hosted AI credit debit failed: insufficient_credits.")
        )
        services.release_direct_chat_hosted_usage_reservation_best_effort = lambda **kwargs: released.append(dict(kwargs))
        services.clear_direct_tool_loop_state = lambda session_key: cleared.append(session_key)

        events = list(
            direct_chat_generation_service.stream_provider_backed_direct_chat(
                services=services,
                context={"provider": "deepseek"},
                metadata={"provider": "deepseek", "model": "deepseek-chat"},
                system_prompt="System prompt",
                normalized_workspace_id="ws-1",
                normalized_requested_provider="deepseek",
                normalized_requested_model="deepseek-chat",
                normalized_reasoning_effort="medium",
                normalized_thread_id="thread-1",
                normalized_message="hello",
                compacted_prior_messages=[],
                prior_messages_used=False,
                history_mode="none",
                connected_systems=[],
                tool_capabilities=[],
                availability_payload={
                    "ai_ready": True,
                    "credential_plane": "platform_runtime",
                    "platform_runtime_allowed": True,
                },
                tools=[],
                direct_chat_credentials={},
                proactive_suggestions=["next"],
                tool_loop_session_key="session-1",
                fallback_reason=None,
                session_ctx={"tenant_id": "tenant-1", "request_id": "req-1"},
                trace_context=None,
                resolved_chat_max_iterations=3,
                direct_tool_result_summary_system_message="Summarize tool results.",
            )
        )

        self.assertEqual(len(released), 1)
        self.assertEqual(released[0]["workspace_id"], "ws-1")
        self.assertEqual(released[0]["thread_id"], "thread-1")
        self.assertEqual(released[0]["session_ctx"], {"tenant_id": "tenant-1", "request_id": "req-1"})
        self.assertEqual(released[0]["status"], "uncertain")
        self.assertEqual(cleared, ["session-1"])

    def test_stream_provider_backed_direct_chat_returns_error_reply_on_failure(self) -> None:
        events = list(
            direct_chat_generation_service.stream_provider_backed_direct_chat(
                services=self._services(
                    stream_events=[
                        {
                            "type": "failure",
                            "attempted_providers": "openai",
                            "error": "temporary backend error",
                        }
                    ]
                ),
                context={"provider": "openai"},
                metadata={"provider": "openai", "model": "gpt-5.4"},
                system_prompt="System prompt",
                normalized_workspace_id="default",
                normalized_requested_provider="openai",
                normalized_requested_model="gpt-5.4",
                normalized_reasoning_effort="medium",
                normalized_thread_id="thread-1",
                normalized_message="hello",
                compacted_prior_messages=[],
                prior_messages_used=False,
                history_mode="none",
                connected_systems=[],
                tool_capabilities=[],
                availability_payload={"ai_ready": True},
                tools=[],
                direct_chat_credentials={},
                proactive_suggestions=[],
                tool_loop_session_key="session-1",
                fallback_reason=None,
                session_ctx=None,
                trace_context=None,
                resolved_chat_max_iterations=1,
                direct_tool_result_summary_system_message="Summarize tool results.",
            )
        )

        self.assertEqual(events[-1]["type"], "final")
        # Error reply populated by _public_generation_error_reply → classify_error
        self.assertIn("provider_generation_failed", events[-1]["payload"]["error"])
        self.assertIn("openai", events[-1]["payload"]["attempted_providers"])

    def test_stream_provider_backed_direct_chat_flags_rate_limit_errors(self) -> None:
        events = list(
            direct_chat_generation_service.stream_provider_backed_direct_chat(
                services=self._services(
                    stream_events=[
                        {
                            "type": "failure",
                            "attempted_providers": "openai,anthropic",
                            "error": "openai generation failed: http_429: rate limit",
                        }
                    ]
                ),
                context={"provider": "openai"},
                metadata={"provider": "openai", "model": "gpt-5.4"},
                system_prompt="System prompt",
                normalized_workspace_id="default",
                normalized_requested_provider="openai",
                normalized_requested_model="gpt-5.4",
                normalized_reasoning_effort="medium",
                normalized_thread_id="thread-1",
                normalized_message="hello",
                compacted_prior_messages=[],
                prior_messages_used=False,
                history_mode="none",
                connected_systems=[],
                tool_capabilities=[],
                availability_payload={"ai_ready": True},
                tools=[],
                direct_chat_credentials={},
                proactive_suggestions=[],
                tool_loop_session_key="session-1",
                fallback_reason=None,
                session_ctx=None,
                trace_context=None,
                resolved_chat_max_iterations=1,
                direct_tool_result_summary_system_message="Summarize tool results.",
            )
        )

        self.assertEqual(events[-1]["type"], "final")
        self.assertEqual(events[-1]["payload"]["error"], "provider_rate_limited")

    def test_stream_provider_backed_direct_chat_flags_payment_required_on_platform_credits(self) -> None:
        """A real-world regression: DeepSeek returning HTTP 402 (insufficient
        balance) on platform-managed credits must classify as a distinct
        payment-required error, in platform voice — never as "verify your
        API key", since the customer never configured one."""
        events = list(
            direct_chat_generation_service.stream_provider_backed_direct_chat(
                services=self._services(
                    stream_events=[
                        {
                            "type": "failure",
                            "attempted_providers": "deepseek",
                            "error": "deepseek generation failed: http_402: Payment Required",
                        }
                    ]
                ),
                context={"provider": "deepseek"},
                metadata={"provider": "deepseek", "model": "deepseek-chat"},
                system_prompt="System prompt",
                normalized_workspace_id="default",
                normalized_requested_provider="deepseek",
                normalized_requested_model="deepseek-chat",
                normalized_reasoning_effort="medium",
                normalized_thread_id="thread-1",
                normalized_message="hello",
                compacted_prior_messages=[],
                prior_messages_used=False,
                history_mode="none",
                connected_systems=[],
                tool_capabilities=[],
                availability_payload={"ai_ready": True, "credential_plane": "platform_runtime"},
                tools=[],
                direct_chat_credentials={},
                proactive_suggestions=[],
                tool_loop_session_key="session-1",
                fallback_reason=None,
                session_ctx=None,
                trace_context=None,
                resolved_chat_max_iterations=1,
                direct_tool_result_summary_system_message="Summarize tool results.",
            )
        )

        self.assertEqual(events[-1]["type"], "final")
        self.assertEqual(events[-1]["payload"]["error"], "provider_payment_required")
        reply = events[-1]["payload"]["reply"]
        self.assertNotIn("api key", reply.lower())
        self.assertNotIn("verify", reply.lower())
        self.assertIn("platform-side issue", reply)

    def test_stream_provider_backed_direct_chat_flags_payment_required_on_byok(self) -> None:
        """The same provider-balance failure on a customer-owned (BYOK) key
        may reference the provider account — the customer owns that key.
        Uses a provider outside PLATFORM_RUNTIME_AUTH_PROVIDERS (openai, not
        deepseek/qwen/mistral/ollama_cloud) since those hosted providers are
        always platform-paid in this system regardless of credential_plane —
        a real BYOK turn runs through a different provider selection."""
        events = list(
            direct_chat_generation_service.stream_provider_backed_direct_chat(
                services=self._services(
                    stream_events=[
                        {
                            "type": "failure",
                            "attempted_providers": "openai",
                            "error": "openai generation failed: http_402: Payment Required",
                        }
                    ]
                ),
                context={"provider": "openai"},
                metadata={"provider": "openai", "model": "gpt-5.4"},
                system_prompt="System prompt",
                normalized_workspace_id="default",
                normalized_requested_provider="openai",
                normalized_requested_model="gpt-5.4",
                normalized_reasoning_effort="medium",
                normalized_thread_id="thread-1",
                normalized_message="hello",
                compacted_prior_messages=[],
                prior_messages_used=False,
                history_mode="none",
                connected_systems=[],
                tool_capabilities=[],
                availability_payload={"ai_ready": True, "credential_plane": "byok"},
                tools=[],
                direct_chat_credentials={},
                proactive_suggestions=[],
                tool_loop_session_key="session-1",
                fallback_reason=None,
                session_ctx=None,
                trace_context=None,
                resolved_chat_max_iterations=1,
                direct_tool_result_summary_system_message="Summarize tool results.",
            )
        )

        self.assertEqual(events[-1]["type"], "final")
        self.assertEqual(events[-1]["payload"]["error"], "provider_payment_required")
        reply = events[-1]["payload"]["reply"]
        self.assertIn("provider account", reply.lower())

    def test_stream_provider_backed_direct_chat_recovers_from_invalid_non_codex_tool_call(self) -> None:
        call_count = {"value": 0}

        def _stream(**kwargs):
            call_count["value"] += 1
            if call_count["value"] == 1:
                return iter(
                    [
                        {
                            "type": "result",
                            "reply": "",
                            "usage_masked": {"provider": "deepseek"},
                            "provider": "deepseek",
                            "model": "deepseek-chat",
                            "attempted_providers": "deepseek",
                            "error": "",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "name": "http_request",
                                    "arguments": {"method": "GET", "url": "file:///Users/test/Desktop"},
                                }
                            ],
                        }
                    ]
                )
            if call_count["value"] == 2:
                return iter(
                    [
                        {
                            "type": "result",
                            "reply": "",
                            "usage_masked": {"provider": "deepseek"},
                            "provider": "deepseek",
                            "model": "deepseek-chat",
                            "attempted_providers": "deepseek",
                            "error": "",
                            "tool_calls": [
                                {
                                    "id": "call-2",
                                    "name": "shell__exec",
                                    "arguments": {"command": "ls -la ~/Desktop"},
                                }
                            ],
                        }
                    ]
                )
            return iter(
                [
                    {
                        "type": "result",
                        "reply": "Desktop files listed.",
                        "usage_masked": {"provider": "deepseek"},
                        "provider": "deepseek",
                        "model": "deepseek-chat",
                        "attempted_providers": "deepseek",
                        "error": "",
                        "tool_calls": [],
                    }
                ]
            )

        services = self._services(stream_events=[])
        services.generate_chat_reply_stream_with_provider_fallback = _stream

        def _execute_single_direct_tool_call(**kwargs):
            tool_call = kwargs.get("tool_call") or {}
            if tool_call.get("name") == "http_request":
                raise RuntimeError("direct_chat_tool_execution_blocked")
            return "alpha.txt\nbeta.txt"

        services.execute_single_direct_tool_call = _execute_single_direct_tool_call

        events = list(
            direct_chat_generation_service.stream_provider_backed_direct_chat(
                services=services,
                context={"provider": "deepseek"},
                metadata={"provider": "deepseek", "model": "deepseek-chat"},
                system_prompt="System prompt",
                normalized_workspace_id="default",
                normalized_requested_provider="deepseek",
                normalized_requested_model="deepseek-chat",
                normalized_reasoning_effort="medium",
                normalized_thread_id="thread-1",
                normalized_message="List the files on my desktop.",
                compacted_prior_messages=[],
                prior_messages_used=False,
                history_mode="none",
                connected_systems=[],
                tool_capabilities=[],
                availability_payload={"ai_ready": True},
                tools=[{"name": "shell__exec"}],
                direct_chat_credentials={},
                proactive_suggestions=[],
                tool_loop_session_key="session-1",
                fallback_reason=None,
                session_ctx=None,
                trace_context=None,
                resolved_chat_max_iterations=4,
                direct_tool_result_summary_system_message="Summarize tool results.",
            )
        )

        self.assertEqual(events[-1]["type"], "final")
        self.assertEqual(events[-1]["payload"]["reply"], "Desktop files listed.")
        self.assertTrue(any(event.get("type") == "step" and event.get("status") == "error" for event in events))
        self.assertGreaterEqual(call_count["value"], 3)

    def test_stream_provider_backed_direct_chat_applies_health_safety_disclaimer_and_citation_trace(self) -> None:
        trace_context = agent_trace_service.TraceContext(
            trace_id="trace-health-1",
            workspace_id="default",
            tenant_id="tenant-1",
            thread_id="thread-1",
            run_id=None,
            root_agent_id="sage",
        )
        emitted: list[dict[str, object]] = []

        async def _emit_with_envelope(trace_ctx, event_type, data, **kwargs):
            seq = trace_ctx.next_seq()
            envelope = {
                "id": f"tevent-{seq}",
                "trace_id": trace_ctx.trace_id,
                "seq": seq,
                "event_type": event_type,
                "persisted": bool(kwargs.get("persisted", True)),
                "agent_id": trace_ctx.root_agent_id,
                "data": dict(data or {}),
            }
            emitted.append(envelope)
            return envelope

        session_ctx = {
            "agent_turn_request": {
                "context_hints": {
                    "metadata": {
                        "health_safety_enabled": True,
                        "health_safety_assistant_name": "HealthGuide",
                    }
                }
            }
        }
        with mock.patch.object(
            direct_chat_generation_service.agent_trace_service,
            "emit_with_envelope",
            side_effect=_emit_with_envelope,
        ), mock.patch.object(
            direct_chat_generation_service.agent_trace_service,
            "finish_trace",
            new=mock.AsyncMock(return_value={}),
        ):
            events = list(
                direct_chat_generation_service.stream_provider_backed_direct_chat(
                    services=self._services(
                        stream_events=[
                            {
                                "type": "result",
                                "reply": "Stay hydrated and monitor your symptoms.",
                                "usage_masked": {"provider": "openai"},
                                "provider": "openai",
                                "model": "gpt-5.4",
                                "attempted_providers": "openai",
                                "error": "",
                                "tool_calls": [],
                                "citation_refs": ["CDC Flu Symptoms (https://www.cdc.gov/flu/symptoms/index.html)"],
                            }
                        ]
                    ),
                    context={"provider": "openai"},
                    metadata={"provider": "openai", "model": "gpt-5.4"},
                    system_prompt="System prompt",
                    normalized_workspace_id="default",
                    normalized_requested_provider="openai",
                    normalized_requested_model="gpt-5.4",
                    normalized_reasoning_effort="medium",
                    normalized_thread_id="thread-1",
                    normalized_message="What are common flu symptoms?",
                    compacted_prior_messages=[],
                    prior_messages_used=False,
                    history_mode="none",
                    connected_systems=[],
                    tool_capabilities=[],
                    availability_payload={"ai_ready": True},
                    tools=[],
                    direct_chat_credentials={},
                    proactive_suggestions=[],
                    tool_loop_session_key="session-1",
                    fallback_reason=None,
                    session_ctx=session_ctx,
                    trace_context=trace_context,
                    resolved_chat_max_iterations=1,
                    direct_tool_result_summary_system_message="Summarize tool results.",
                )
            )

        final_payload = events[-1]["payload"]
        self.assertIn("not a doctor", final_payload["reply"])
        self.assertIn("Sources:", final_payload["reply"])
        completed_events = [
            event["payload"]
            for event in events
            if event.get("type") == "trace" and event.get("payload", {}).get("event_type") == "assistant.message.completed"
        ]
        self.assertTrue(completed_events)
        self.assertEqual(
            completed_events[-1]["data"]["citation_refs"],
            ["CDC Flu Symptoms (https://www.cdc.gov/flu/symptoms/index.html)"],
        )

    def test_stream_provider_backed_direct_chat_emits_trace_plan_tool_search_and_message_events(self) -> None:
        trace_context = agent_trace_service.TraceContext(
            trace_id="trace-1",
            workspace_id="default",
            tenant_id="tenant-1",
            thread_id="thread-1",
            run_id=None,
            root_agent_id="sage",
        )
        emitted: list[dict[str, object]] = []

        async def _emit_with_envelope(trace_ctx, event_type, data, **kwargs):
            seq = trace_ctx.next_seq()
            envelope = {
                "id": f"tevent-{seq}",
                "trace_id": trace_ctx.trace_id,
                "seq": seq,
                "event_type": event_type,
                "persisted": bool(kwargs.get("persisted", True)),
                "agent_id": trace_ctx.root_agent_id,
                "parent_id": kwargs.get("parent_id"),
                "item_id": kwargs.get("item_id"),
                "tool_call_id": kwargs.get("tool_call_id"),
                "child_run_id": kwargs.get("child_run_id"),
                "approval_id": kwargs.get("approval_id"),
                "artifact_id": kwargs.get("artifact_id"),
                "data": dict(data or {}),
            }
            emitted.append(envelope)
            return envelope

        stream_rounds = iter(
            [
                [
                    {
                        "type": "result",
                        "reply": "Here is the final answer",
                        "usage_masked": {"provider": "openai"},
                        "provider": "openai",
                        "model": "gpt-5.4",
                        "attempted_providers": "openai",
                        "error": "",
                        "tool_calls": [
                            {
                                "id": "tool-call-1",
                                "name": "web__search",
                                "arguments": {"query": "latest sage trace ui"},
                            }
                        ],
                    }
                ],
                [
                    {
                        "type": "chunk",
                        "delta": "Here ",
                    },
                    {
                        "type": "chunk",
                        "delta": "you go",
                    },
                    {
                        "type": "result",
                        "reply": "Here you go",
                        "usage_masked": {"provider": "openai"},
                        "provider": "openai",
                        "model": "gpt-5.4",
                        "attempted_providers": "openai",
                        "error": "",
                        "tool_calls": [],
                    },
                ],
            ]
        )

        def _stream_events(**kwargs):
            return iter(next(stream_rounds))

        services = self._services(stream_events=[])
        services.generate_chat_reply_stream_with_provider_fallback = _stream_events

        with mock.patch.object(
            direct_chat_generation_service.agent_trace_service,
            "emit_with_envelope",
            side_effect=_emit_with_envelope,
        ), mock.patch.object(
            direct_chat_generation_service.agent_trace_service,
            "finish_trace",
            new=mock.AsyncMock(return_value={}),
        ):
            events = list(
                direct_chat_generation_service.stream_provider_backed_direct_chat(
                    services=services,
                    context={"provider": "openai"},
                    metadata={"provider": "openai", "model": "gpt-5.4"},
                    system_prompt="System prompt",
                    normalized_workspace_id="default",
                    normalized_requested_provider="openai",
                    normalized_requested_model="gpt-5.4",
                    normalized_reasoning_effort="medium",
                    normalized_thread_id="thread-1",
                    normalized_message="Research the latest Sage trace UI references",
                    compacted_prior_messages=[],
                    prior_messages_used=False,
                    history_mode="none",
                    connected_systems=[],
                    tool_capabilities=[],
                    availability_payload={"ai_ready": True},
                    tools=[],
                    direct_chat_credentials={},
                    proactive_suggestions=["next"],
                    tool_loop_session_key="session-1",
                    fallback_reason=None,
                    session_ctx=None,
                    trace_context=trace_context,
                    resolved_chat_max_iterations=3,
                    direct_tool_result_summary_system_message="Summarize tool results.",
                )
            )

        trace_events = [event["payload"] for event in events if event.get("type") == "trace"]
        event_types = [payload["event_type"] for payload in trace_events]
        self.assertIn("plan.started", event_types)
        self.assertIn("plan.item.created", event_types)
        self.assertIn("tool.started", event_types)
        self.assertIn("tool.result", event_types)
        self.assertIn("search.query", event_types)
        self.assertIn("search.results", event_types)
        self.assertIn("assistant.message.delta", event_types)
        self.assertIn("assistant.message.completed", event_types)
        self.assertLess(event_types.index("plan.started"), event_types.index("tool.started"))
        self.assertEqual(events[-1]["type"], "final")
        self.assertEqual(events[-1]["payload"]["reply"], "Here you go")

    def test_stream_provider_backed_direct_chat_marks_local_gateway_activity(self) -> None:
        trace_context = agent_trace_service.TraceContext(
            trace_id="trace-hardware",
            workspace_id="default",
            tenant_id="tenant-1",
            thread_id="thread-1",
            run_id=None,
            root_agent_id="sage",
        )
        emitted: list[dict[str, object]] = []

        async def _emit_with_envelope(trace_ctx, event_type, data, **kwargs):
            envelope = {
                "id": f"tevent-{trace_ctx.next_seq()}",
                "trace_id": trace_ctx.trace_id,
                "event_type": event_type,
                "data": dict(data or {}),
                "tool_call_id": kwargs.get("tool_call_id"),
            }
            emitted.append(envelope)
            return envelope

        stream_rounds = iter(
            [
                [
                    {
                        "type": "result",
                        "reply": "",
                        "usage_masked": {"provider": "openai"},
                        "provider": "openai",
                        "model": "gpt-5.4",
                        "attempted_providers": "openai",
                        "error": "",
                        "tool_calls": [
                            {
                                "id": "tool-call-hardware",
                                "name": "hardware__action",
                                "arguments": {
                                    "action": "shell",
                                    "command": "echo hello from hardware",
                                    "runtime_target": "user_device_gateway",
                                },
                            }
                        ],
                    }
                ],
                [
                    {
                        "type": "result",
                        "reply": "Done",
                        "usage_masked": {"provider": "openai"},
                        "provider": "openai",
                        "model": "gpt-5.4",
                        "attempted_providers": "openai",
                        "error": "",
                        "tool_calls": [],
                    },
                ],
            ]
        )

        def _stream_events(**kwargs):
            return iter(next(stream_rounds))

        services = self._services(stream_events=[])
        services.generate_chat_reply_stream_with_provider_fallback = _stream_events
        services.execute_single_direct_tool_call = lambda **_kwargs: "shell completed."

        with mock.patch.object(
            direct_chat_generation_service.agent_trace_service,
            "emit_with_envelope",
            side_effect=_emit_with_envelope,
        ), mock.patch.object(
            direct_chat_generation_service.agent_trace_service,
            "finish_trace",
            new=mock.AsyncMock(return_value={}),
        ):
            events = list(
                direct_chat_generation_service.stream_provider_backed_direct_chat(
                    services=services,
                    context={"provider": "openai"},
                    metadata={"provider": "openai", "model": "gpt-5.4"},
                    system_prompt="System prompt",
                    normalized_workspace_id="default",
                    normalized_requested_provider="openai",
                    normalized_requested_model="gpt-5.4",
                    normalized_reasoning_effort="medium",
                    normalized_thread_id="thread-1",
                    normalized_message="run: echo hello from hardware",
                    compacted_prior_messages=[],
                    prior_messages_used=False,
                    history_mode="none",
                    connected_systems=[],
                    tool_capabilities=[],
                    availability_payload={"ai_ready": True},
                    tools=[{"name": "hardware__action"}],
                    direct_chat_credentials={},
                    proactive_suggestions=[],
                    tool_loop_session_key="session-hardware",
                    fallback_reason=None,
                    session_ctx=None,
                    trace_context=trace_context,
                    resolved_chat_max_iterations=3,
                    direct_tool_result_summary_system_message="Summarize tool results.",
                )
            )

        started = next(item for item in emitted if item["event_type"] == "tool.started")
        progress = next(
            item["payload"]
            for item in events
            if item.get("type") == "trace" and item.get("payload", {}).get("event_type") == "tool.progress"
        )
        result = next(item for item in emitted if item["event_type"] == "tool.result")
        self.assertEqual(started["data"]["connector_id"], "hardware_runtime")
        self.assertEqual(started["data"]["execution_environment"], "local_gateway")
        self.assertEqual(started["data"]["agent_activity"]["label"], "Running on your Mac")
        self.assertEqual(started["data"]["agent_activity"]["status"], "active")
        self.assertEqual(progress["data"]["message"], "Running on your Mac")
        self.assertEqual(result["data"]["connector_id"], "hardware_runtime")
        self.assertEqual(result["data"]["agent_activity"]["label"], "hardware__action")
        self.assertEqual(result["data"]["agent_activity"]["status"], "completed")

    def test_stream_provider_backed_direct_chat_sanitizes_tool_result_before_cloud_followup_prompt(self) -> None:
        captured_messages: list[list[dict[str, object]]] = []
        stream_rounds = iter(
            [
                [
                    {
                        "type": "result",
                        "reply": "",
                        "usage_masked": {"provider": "openai"},
                        "provider": "openai",
                        "model": "gpt-5.4",
                        "attempted_providers": "openai",
                        "error": "",
                        "tool_calls": [
                            {
                                "id": "tool-call-red",
                                "name": "file__read",
                                "arguments": {"path": "/Users/mansur/private.txt"},
                            }
                        ],
                    }
                ],
                [
                    {
                        "type": "result",
                        "reply": "Done",
                        "usage_masked": {"provider": "openai"},
                        "provider": "openai",
                        "model": "gpt-5.4",
                        "attempted_providers": "openai",
                        "error": "",
                        "tool_calls": [],
                    }
                ],
            ]
        )

        def _stream_events(**kwargs):
            captured_messages.append(list(kwargs.get("prior_messages") or []))
            return iter(next(stream_rounds))

        services = self._services(stream_events=[])
        services.generate_chat_reply_stream_with_provider_fallback = _stream_events
        services.execute_single_direct_tool_call = lambda **_kwargs: (
            "Visible line\n"
            "[red] API_TOKEN=super-secret-token\n"
            "Another safe line"
        )

        events = list(
            direct_chat_generation_service.stream_provider_backed_direct_chat(
                services=services,
                context={"provider": "openai"},
                metadata={"provider": "openai", "model": "gpt-5.4"},
                system_prompt="System prompt",
                normalized_workspace_id="default",
                normalized_requested_provider="openai",
                normalized_requested_model="gpt-5.4",
                normalized_reasoning_effort="medium",
                normalized_thread_id="thread-1",
                normalized_message="Read the file.",
                compacted_prior_messages=[],
                prior_messages_used=False,
                history_mode="none",
                connected_systems=[],
                tool_capabilities=[],
                availability_payload={"ai_ready": True},
                tools=[{"name": "file__read"}],
                direct_chat_credentials={},
                proactive_suggestions=[],
                tool_loop_session_key="session-red",
                fallback_reason=None,
                session_ctx=None,
                trace_context=None,
                resolved_chat_max_iterations=3,
                direct_tool_result_summary_system_message="Summarize tool results.",
            )
        )

        self.assertEqual(events[-1]["payload"]["reply"], "Done")
        self.assertEqual(len(captured_messages), 2)
        followup_prompt = str(captured_messages[1])
        self.assertIn("Visible line", followup_prompt)
        self.assertIn("Another safe line", followup_prompt)
        self.assertNotIn("super-secret-token", followup_prompt)
        self.assertNotIn("API_TOKEN", followup_prompt)

    def test_stream_provider_backed_direct_chat_caps_codex_tool_result_reinjection(self) -> None:
        # docs/design/audit-context-anatomy.md fix #1: direct_tool_followup_
        # message (the codex_cli re-injection path) used to receive the raw,
        # unbounded tool result while every other provider capped it at
        # 4,000 chars. A huge tool result must now come through truncated the
        # same way for codex_cli too.
        captured_messages: list[list[dict[str, object]]] = []
        huge_result = "A" * 10_000
        stream_rounds = iter(
            [
                [
                    {
                        "type": "result",
                        "reply": "",
                        "usage_masked": {"provider": "codex_cli"},
                        "provider": "codex_cli",
                        "model": "gpt-5-codex",
                        "attempted_providers": "codex_cli",
                        "error": "",
                        "tool_calls": [
                            {
                                "id": "tool-call-1",
                                "name": "file__read",
                                "arguments": {"path": "/tmp/big.txt"},
                            }
                        ],
                    }
                ],
                [
                    {
                        "type": "result",
                        "reply": "Done",
                        "usage_masked": {"provider": "codex_cli"},
                        "provider": "codex_cli",
                        "model": "gpt-5-codex",
                        "attempted_providers": "codex_cli",
                        "error": "",
                        "tool_calls": [],
                    }
                ],
            ]
        )

        def _stream_events(**kwargs):
            captured_messages.append(list(kwargs.get("prior_messages") or []))
            return iter(next(stream_rounds))

        services = self._services(stream_events=[])
        services.generate_chat_reply_stream_with_provider_fallback = _stream_events
        services.execute_single_direct_tool_call = lambda **_kwargs: huge_result

        events = list(
            direct_chat_generation_service.stream_provider_backed_direct_chat(
                services=services,
                context={"provider": "codex_cli"},
                metadata={"provider": "codex_cli", "model": "gpt-5-codex"},
                system_prompt="System prompt",
                normalized_workspace_id="default",
                normalized_requested_provider="codex_cli",
                normalized_requested_model="gpt-5-codex",
                normalized_reasoning_effort="medium",
                normalized_thread_id="thread-1",
                normalized_message="Read the big file.",
                compacted_prior_messages=[],
                prior_messages_used=False,
                history_mode="none",
                connected_systems=[],
                tool_capabilities=[],
                availability_payload={"ai_ready": True},
                tools=[{"name": "file__read"}],
                direct_chat_credentials={},
                proactive_suggestions=[],
                tool_loop_session_key="session-codex",
                fallback_reason=None,
                session_ctx=None,
                trace_context=None,
                resolved_chat_max_iterations=3,
                direct_tool_result_summary_system_message="Summarize tool results.",
            )
        )

        self.assertEqual(events[-1]["payload"]["reply"], "Done")
        self.assertEqual(len(captured_messages), 2)
        # codex_cli appends the tool-followup ("user") message, then a fixed
        # system "summarize tool results" message right after it — the
        # followup (the one carrying the potentially-huge tool result) is
        # second-to-last, not last.
        followup_message = captured_messages[1][-2]
        self.assertEqual(followup_message.get("role"), "user")
        followup_content = str(followup_message.get("content") or "")
        # 4,000-char bound: 3,800 chars of real content plus a short
        # "...[truncated N chars]" marker, tool-name prefix, and the fixed
        # continuation prompt text the fake direct_tool_followup_message and
        # the real one both add — total stays well under the raw 10,000.
        self.assertLess(len(followup_content), 4200)
        self.assertIn("[truncated", followup_content)
        self.assertNotIn("A" * 10_000, followup_content)


if __name__ == "__main__":
    unittest.main()
