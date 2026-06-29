import unittest
from types import SimpleNamespace
from unittest.mock import patch

from server_modules import direct_chat_entry_service


class DirectChatEntryServiceTests(unittest.TestCase):
    def test_prepare_direct_chat_request_resolves_empyralis_pro_tier(self) -> None:
        with patch.object(
            direct_chat_entry_service.session_transcript_store,
            "load_latest_session_transcript_messages",
            return_value=[],
        ):
            prepared = direct_chat_entry_service.prepare_direct_chat_request(
                resolved_turn_request=None,
                session_ctx=None,
                message="hello",
                workspace_id="default",
                thread_id="thread-1",
                requested_model="pro",
                requested_provider="empyralis",
                prior_messages=[],
                reasoning_effort="",
                availability={"ai_ready": True},
                approved_action=None,
                max_iterations=4,
                direct_chat_session_key_fn=lambda workspace_id, thread_id: f"{workspace_id}:{thread_id}",
                resolved_chat_iteration_limit_fn=lambda value: int(value or 30),
                session_model_preference_fn=lambda _session_key: {},
                normalize_reasoning_effort_fn=lambda value: str(value or "").strip().lower() or None,
                parse_slash_command_fn=lambda _message: {"command": "", "remainder": ""},
                set_session_model_preference_fn=lambda session_key, provider=None, model=None: None,
                mark_thread_cleared_fn=lambda _session_key: None,
                normalize_prior_messages_fn=lambda value: list(value or []),
                consume_thread_cleared_fn=lambda _session_key: False,
                compact_conversation_history_fn=lambda messages, **kwargs: {"messages": list(messages), "compacted": False},
                build_proactive_suggestions_fn=lambda _workspace_id: [],
                direct_tool_session_key_fn=lambda workspace_id, thread_id: f"tool:{workspace_id}:{thread_id}",
                resolve_direct_chat_availability_fn=lambda workspace_id, requested_provider, availability_override=None: {"ai_ready": True, "provider": requested_provider},
                connected_system_labels_fn=lambda _availability: [],
                context_tool_capabilities_fn=lambda _availability: [],
                build_direct_chat_tools_fn=lambda _tool_capabilities: [],
                build_local_direct_chat_tools_fn=lambda _availability: [],
                build_builtin_direct_chat_tools_fn=lambda: [],
                build_always_on_direct_chat_tools_fn=lambda: [],
                build_registry_entries_fn=lambda tool_capabilities, availability_payload: [],
                normalize_direct_approved_action_fn=lambda _value: None,
                build_context_used_fn=lambda **kwargs: kwargs,
                direct_chat_compaction_token_limit=2000,
            )

        self.assertEqual(prepared.normalized_requested_provider, "deepseek")
        self.assertEqual(prepared.normalized_requested_model, "deepseek-v4-pro")
        self.assertEqual(prepared.normalized_reasoning_effort, "high")
        self.assertEqual(prepared.availability_payload["provider"], "deepseek")
        self.assertEqual(prepared.base_context_used["requested_provider"], "deepseek")
        self.assertEqual(prepared.base_context_used["requested_model"], "deepseek-v4-pro")

    def test_prepare_direct_chat_request_applies_model_command_and_builds_tools(self) -> None:
        with patch.object(
            direct_chat_entry_service.session_transcript_store,
            "load_latest_session_transcript_messages",
            return_value=[],
        ):
            prepared = direct_chat_entry_service.prepare_direct_chat_request(
                resolved_turn_request=None,
                session_ctx=None,
                message="/model openai:gpt-5.4 Continue with the task",
                workspace_id="default",
                thread_id="thread-1",
                requested_model="",
                requested_provider="",
                prior_messages=[],
                reasoning_effort="medium",
                availability={"ai_ready": True},
                approved_action=None,
                max_iterations=12,
                direct_chat_session_key_fn=lambda workspace_id, thread_id: f"{workspace_id}:{thread_id}",
                resolved_chat_iteration_limit_fn=lambda value: int(value or 30),
                session_model_preference_fn=lambda _session_key: {},
                normalize_reasoning_effort_fn=lambda value: value,
                parse_slash_command_fn=lambda message: {"command": "model", "remainder": "openai:gpt-5.4 Continue with the task"} if str(message).startswith("/model") else {"command": "", "remainder": ""},
                set_session_model_preference_fn=lambda session_key, provider=None, model=None: None,
                mark_thread_cleared_fn=lambda _session_key: None,
                normalize_prior_messages_fn=lambda value: list(value or []),
                consume_thread_cleared_fn=lambda _session_key: False,
                compact_conversation_history_fn=lambda messages, **kwargs: {"messages": list(messages), "compacted": False},
                build_proactive_suggestions_fn=lambda _workspace_id: ["next"],
                direct_tool_session_key_fn=lambda workspace_id, thread_id: f"tool:{workspace_id}:{thread_id}",
                resolve_direct_chat_availability_fn=lambda workspace_id, requested_provider, availability_override=None: {"ai_ready": True, "provider": requested_provider or "openai"},
                connected_system_labels_fn=lambda _availability: ["Memory"],
                context_tool_capabilities_fn=lambda _availability: [{"id": "memory", "connected": True}],
                build_direct_chat_tools_fn=lambda _tool_capabilities: [{"name": "app_tool"}],
                build_local_direct_chat_tools_fn=lambda _availability: [{"name": "local_tool"}],
                build_builtin_direct_chat_tools_fn=lambda: [{"name": "builtin_tool"}],
                build_always_on_direct_chat_tools_fn=lambda: [{"name": "always_on_core"}],
                build_registry_entries_fn=lambda tool_capabilities, availability_payload: [{"name": "registry_tool"}],
                normalize_direct_approved_action_fn=lambda _value: None,
                build_context_used_fn=lambda **kwargs: kwargs,
                direct_chat_compaction_token_limit=2000,
            )

        self.assertEqual(prepared.normalized_requested_provider, "openai")
        self.assertEqual(prepared.normalized_requested_model, "gpt-5.4")
        self.assertEqual(prepared.normalized_message, "Continue with the task")
        # Two-tier assembly: tools = always-on only
        self.assertEqual(len(prepared.tools), 1)
        self.assertEqual(prepared.tools[0]["name"], "always_on_core")
        # Registry contains everything else
        self.assertEqual(len(prepared.tool_registry), 1)
        self.assertEqual(prepared.tool_registry[0]["name"], "registry_tool")
        self.assertEqual(prepared.proactive_suggestions, ["next"])

    def test_prepare_direct_chat_request_respects_thread_clear_and_compaction(self) -> None:
        with patch.object(
            direct_chat_entry_service.session_transcript_store,
            "load_latest_session_transcript_messages",
            return_value=[],
        ):
            prepared = direct_chat_entry_service.prepare_direct_chat_request(
                resolved_turn_request=None,
                session_ctx=None,
                message="/clear Keep going",
                workspace_id="default",
                thread_id="thread-1",
                requested_model="gpt-5.4",
                requested_provider="openai",
                prior_messages=[{"role": "user", "content": "old"}],
                reasoning_effort="medium",
                availability={"ai_ready": True},
                approved_action=None,
                max_iterations=4,
                direct_chat_session_key_fn=lambda workspace_id, thread_id: f"{workspace_id}:{thread_id}",
                resolved_chat_iteration_limit_fn=lambda value: int(value or 30),
                session_model_preference_fn=lambda _session_key: {},
                normalize_reasoning_effort_fn=lambda value: value,
                parse_slash_command_fn=lambda message: {"command": "clear", "remainder": "Keep going"} if str(message).startswith("/clear") else {"command": "", "remainder": ""},
                set_session_model_preference_fn=lambda session_key, provider=None, model=None: None,
                mark_thread_cleared_fn=lambda _session_key: None,
                normalize_prior_messages_fn=lambda value: list(value or []),
                consume_thread_cleared_fn=lambda _session_key: True,
                compact_conversation_history_fn=lambda messages, **kwargs: {"messages": list(messages), "compacted": False},
                build_proactive_suggestions_fn=lambda _workspace_id: ["next"],
                direct_tool_session_key_fn=lambda workspace_id, thread_id: f"tool:{workspace_id}:{thread_id}",
                resolve_direct_chat_availability_fn=lambda workspace_id, requested_provider, availability_override=None: {"ai_ready": True, "provider": requested_provider},
                connected_system_labels_fn=lambda _availability: [],
                context_tool_capabilities_fn=lambda _availability: [],
                build_direct_chat_tools_fn=lambda _tool_capabilities: [],
                build_local_direct_chat_tools_fn=lambda _availability: [],
                build_builtin_direct_chat_tools_fn=lambda: [],
                build_always_on_direct_chat_tools_fn=lambda: [],
                build_registry_entries_fn=lambda tool_capabilities, availability_payload: [],
                normalize_direct_approved_action_fn=lambda _value: None,
                build_context_used_fn=lambda **kwargs: kwargs,
                direct_chat_compaction_token_limit=2000,
            )

        self.assertEqual(prepared.normalized_message, "Keep going")
        self.assertEqual(prepared.compacted_prior_messages, [])
        self.assertEqual(prepared.base_context_used["workspace_id"], "default")

    def test_prepare_direct_chat_request_marks_mobile_turns_server_first(self) -> None:
        captured = {}

        def resolve_availability(workspace_id, requested_provider, availability_override=None):
            captured["override"] = dict(availability_override or {})
            return {"ai_ready": True, "provider": requested_provider or "openai"}

        with patch.object(
            direct_chat_entry_service.session_transcript_store,
            "load_latest_session_transcript_messages",
            return_value=[],
        ):
            prepared = direct_chat_entry_service.prepare_direct_chat_request(
                resolved_turn_request=SimpleNamespace(
                    message="hello",
                    workspace_id="default",
                    session_id="thread-1",
                    channel="mobile",
                    context_hints={"source": "mobile_chat", "metadata": {"source": "mobile_chat"}},
                ),
                session_ctx=None,
                message="ignored",
                workspace_id="ignored",
                thread_id="ignored",
                requested_model="",
                requested_provider="",
                prior_messages=[],
                reasoning_effort="medium",
                availability={"ai_ready": True},
                approved_action=None,
                max_iterations=4,
                direct_chat_session_key_fn=lambda workspace_id, thread_id: f"{workspace_id}:{thread_id}",
                resolved_chat_iteration_limit_fn=lambda value: int(value or 30),
                session_model_preference_fn=lambda _session_key: {},
                normalize_reasoning_effort_fn=lambda value: value,
                parse_slash_command_fn=lambda _message: {"command": "", "remainder": ""},
                set_session_model_preference_fn=lambda session_key, provider=None, model=None: None,
                mark_thread_cleared_fn=lambda _session_key: None,
                normalize_prior_messages_fn=lambda value: list(value or []),
                consume_thread_cleared_fn=lambda _session_key: False,
                compact_conversation_history_fn=lambda messages, **kwargs: {"messages": list(messages), "compacted": False},
                build_proactive_suggestions_fn=lambda _workspace_id: [],
                direct_tool_session_key_fn=lambda workspace_id, thread_id: f"tool:{workspace_id}:{thread_id}",
                resolve_direct_chat_availability_fn=resolve_availability,
                connected_system_labels_fn=lambda _availability: [],
                context_tool_capabilities_fn=lambda _availability: [],
                build_direct_chat_tools_fn=lambda _tool_capabilities: [],
                build_local_direct_chat_tools_fn=lambda _availability: [],
                build_builtin_direct_chat_tools_fn=lambda: [],
                build_always_on_direct_chat_tools_fn=lambda: [],
                build_registry_entries_fn=lambda tool_capabilities, availability_payload: [],
                normalize_direct_approved_action_fn=lambda _value: None,
                build_context_used_fn=lambda **kwargs: kwargs,
                direct_chat_compaction_token_limit=2000,
            )

        self.assertEqual(prepared.normalized_thread_id, "thread-1")
        self.assertTrue(captured["override"]["mobile_server_first"])
        self.assertEqual(captured["override"]["surface_channel"], "mobile")
        self.assertEqual(captured["override"]["source"], "mobile_chat")

    def test_prepare_direct_chat_request_uses_latest_transcript_when_prior_messages_missing(self) -> None:
        with patch.object(
            direct_chat_entry_service.session_transcript_store,
            "load_latest_session_transcript_messages",
            return_value=[
                {"role": "user", "content": "Earlier question"},
                {"role": "assistant", "content": "Earlier answer"},
            ],
        ) as load_transcript:
            prepared = direct_chat_entry_service.prepare_direct_chat_request(
                resolved_turn_request=None,
                session_ctx=None,
                message="Continue",
                workspace_id="default",
                thread_id="thread-1",
                requested_model="gpt-5.4",
                requested_provider="openai",
                prior_messages=[],
                reasoning_effort="medium",
                availability={"ai_ready": True},
                approved_action=None,
                max_iterations=4,
                direct_chat_session_key_fn=lambda workspace_id, thread_id: f"{workspace_id}:{thread_id}",
                resolved_chat_iteration_limit_fn=lambda value: int(value or 30),
                session_model_preference_fn=lambda _session_key: {},
                normalize_reasoning_effort_fn=lambda value: value,
                parse_slash_command_fn=lambda _message: {"command": "", "remainder": ""},
                set_session_model_preference_fn=lambda session_key, provider=None, model=None: None,
                mark_thread_cleared_fn=lambda _session_key: None,
                normalize_prior_messages_fn=lambda value: list(value or []),
                consume_thread_cleared_fn=lambda _session_key: False,
                compact_conversation_history_fn=lambda messages, **kwargs: {"messages": list(messages), "compacted": False},
                build_proactive_suggestions_fn=lambda _workspace_id: [],
                direct_tool_session_key_fn=lambda workspace_id, thread_id: f"tool:{workspace_id}:{thread_id}",
                resolve_direct_chat_availability_fn=lambda workspace_id, requested_provider, availability_override=None: {"ai_ready": True, "provider": requested_provider},
                connected_system_labels_fn=lambda _availability: [],
                context_tool_capabilities_fn=lambda _availability: [],
                build_direct_chat_tools_fn=lambda _tool_capabilities: [],
                build_local_direct_chat_tools_fn=lambda _availability: [],
                build_builtin_direct_chat_tools_fn=lambda: [],
                build_always_on_direct_chat_tools_fn=lambda: [],
                build_registry_entries_fn=lambda tool_capabilities, availability_payload: [],
                normalize_direct_approved_action_fn=lambda _value: None,
                build_context_used_fn=lambda **kwargs: kwargs,
                direct_chat_compaction_token_limit=2000,
            )

        load_transcript.assert_called_once_with(
            workspace_id="default",
            thread_id="thread-1",
            limit=22,
        )
        self.assertEqual(
            prepared.compacted_prior_messages,
            [
                {"role": "user", "content": "Earlier question"},
                {"role": "assistant", "content": "Earlier answer"},
            ],
        )

    def test_prepare_direct_chat_request_uses_model_aware_compaction_for_known_model(self) -> None:
        captured_kwargs = {}

        def _compact(messages, **kwargs):
            captured_kwargs.update(kwargs)
            return {"messages": list(messages), "compacted": False}

        with patch.object(
            direct_chat_entry_service.session_transcript_store,
            "load_latest_session_transcript_messages",
            return_value=[],
        ):
            direct_chat_entry_service.prepare_direct_chat_request(
                resolved_turn_request=None,
                session_ctx=None,
                message="Continue",
                workspace_id="default",
                thread_id="thread-1",
                requested_model="gpt-4.1",
                requested_provider="openai",
                prior_messages=[],
                reasoning_effort="medium",
                availability={"ai_ready": True},
                approved_action=None,
                max_iterations=4,
                direct_chat_session_key_fn=lambda workspace_id, thread_id: f"{workspace_id}:{thread_id}",
                resolved_chat_iteration_limit_fn=lambda value: int(value or 30),
                session_model_preference_fn=lambda _session_key: {},
                normalize_reasoning_effort_fn=lambda value: value,
                parse_slash_command_fn=lambda _message: {"command": "", "remainder": ""},
                set_session_model_preference_fn=lambda session_key, provider=None, model=None: None,
                mark_thread_cleared_fn=lambda _session_key: None,
                normalize_prior_messages_fn=lambda value: list(value or []),
                consume_thread_cleared_fn=lambda _session_key: False,
                compact_conversation_history_fn=_compact,
                build_proactive_suggestions_fn=lambda _workspace_id: [],
                direct_tool_session_key_fn=lambda workspace_id, thread_id: f"tool:{workspace_id}:{thread_id}",
                resolve_direct_chat_availability_fn=lambda workspace_id, requested_provider, availability_override=None: {"ai_ready": True, "provider": requested_provider},
                connected_system_labels_fn=lambda _availability: [],
                context_tool_capabilities_fn=lambda _availability: [],
                build_direct_chat_tools_fn=lambda _tool_capabilities: [],
                build_local_direct_chat_tools_fn=lambda _availability: [],
                build_builtin_direct_chat_tools_fn=lambda: [],
                build_always_on_direct_chat_tools_fn=lambda: [],
                build_registry_entries_fn=lambda tool_capabilities, availability_payload: [],
                normalize_direct_approved_action_fn=lambda _value: None,
                build_context_used_fn=lambda **kwargs: kwargs,
                direct_chat_compaction_token_limit=8_000,
            )

        self.assertGreater(captured_kwargs.get("max_tokens", 0), 8_000)
        self.assertGreater(captured_kwargs.get("recent_message_budget_tokens", 0), 0)
        self.assertLessEqual(captured_kwargs.get("summary_max_chars", 0), 8_000)

    def test_prepare_direct_chat_request_downgrades_disallowed_hosted_tier(self) -> None:
        with patch.object(
            direct_chat_entry_service.session_transcript_store,
            "load_latest_session_transcript_messages",
            return_value=[],
        ), patch.object(
            direct_chat_entry_service.entitlements_service,
            "chat_model_tier_policy_for_workspace_id",
            return_value={
                "tiers": {
                    "light": {"enabled": True},
                    "pro": {"enabled": True},
                    "max": {"enabled": False},
                }
            },
        ):
            prepared = direct_chat_entry_service.prepare_direct_chat_request(
                resolved_turn_request=None,
                session_ctx=None,
                message="hello",
                workspace_id="default",
                thread_id="thread-1",
                requested_model="max",
                requested_provider="empyralis",
                prior_messages=[],
                reasoning_effort="",
                availability={"ai_ready": True},
                approved_action=None,
                max_iterations=4,
                direct_chat_session_key_fn=lambda workspace_id, thread_id: f"{workspace_id}:{thread_id}",
                resolved_chat_iteration_limit_fn=lambda value: int(value or 30),
                session_model_preference_fn=lambda _session_key: {},
                normalize_reasoning_effort_fn=lambda value: str(value or "").strip().lower() or None,
                parse_slash_command_fn=lambda _message: {"command": "", "remainder": ""},
                set_session_model_preference_fn=lambda session_key, provider=None, model=None: None,
                mark_thread_cleared_fn=lambda _session_key: None,
                normalize_prior_messages_fn=lambda value: list(value or []),
                consume_thread_cleared_fn=lambda _session_key: False,
                compact_conversation_history_fn=lambda messages, **kwargs: {"messages": list(messages), "compacted": False},
                build_proactive_suggestions_fn=lambda _workspace_id: [],
                direct_tool_session_key_fn=lambda workspace_id, thread_id: f"tool:{workspace_id}:{thread_id}",
                resolve_direct_chat_availability_fn=lambda workspace_id, requested_provider, availability_override=None: {"ai_ready": True, "provider": requested_provider},
                connected_system_labels_fn=lambda _availability: [],
                context_tool_capabilities_fn=lambda _availability: [],
                build_direct_chat_tools_fn=lambda _tool_capabilities: [],
                build_local_direct_chat_tools_fn=lambda _availability: [],
                build_builtin_direct_chat_tools_fn=lambda: [],
                build_always_on_direct_chat_tools_fn=lambda: [],
                build_registry_entries_fn=lambda tool_capabilities, availability_payload: [],
                normalize_direct_approved_action_fn=lambda _value: None,
                build_context_used_fn=lambda **kwargs: kwargs,
                direct_chat_compaction_token_limit=2000,
            )

        self.assertEqual(prepared.normalized_requested_provider, "deepseek")
        self.assertEqual(prepared.normalized_requested_model, "deepseek-v4-pro")
        self.assertEqual(prepared.normalized_reasoning_effort, "high")

    def test_prepare_direct_chat_request_falls_back_to_default_budget_when_model_unknown(self) -> None:
        captured_kwargs = {}

        def _compact(messages, **kwargs):
            captured_kwargs.update(kwargs)
            return {"messages": list(messages), "compacted": False}

        with patch.object(
            direct_chat_entry_service.session_transcript_store,
            "load_latest_session_transcript_messages",
            return_value=[],
        ):
            direct_chat_entry_service.prepare_direct_chat_request(
                resolved_turn_request=None,
                session_ctx=None,
                message="Continue",
                workspace_id="default",
                thread_id="thread-1",
                requested_model="unknown-model",
                requested_provider="openai",
                prior_messages=[],
                reasoning_effort="medium",
                availability={"ai_ready": True},
                approved_action=None,
                max_iterations=4,
                direct_chat_session_key_fn=lambda workspace_id, thread_id: f"{workspace_id}:{thread_id}",
                resolved_chat_iteration_limit_fn=lambda value: int(value or 30),
                session_model_preference_fn=lambda _session_key: {},
                normalize_reasoning_effort_fn=lambda value: value,
                parse_slash_command_fn=lambda _message: {"command": "", "remainder": ""},
                set_session_model_preference_fn=lambda session_key, provider=None, model=None: None,
                mark_thread_cleared_fn=lambda _session_key: None,
                normalize_prior_messages_fn=lambda value: list(value or []),
                consume_thread_cleared_fn=lambda _session_key: False,
                compact_conversation_history_fn=_compact,
                build_proactive_suggestions_fn=lambda _workspace_id: [],
                direct_tool_session_key_fn=lambda workspace_id, thread_id: f"tool:{workspace_id}:{thread_id}",
                resolve_direct_chat_availability_fn=lambda workspace_id, requested_provider, availability_override=None: {"ai_ready": True, "provider": requested_provider},
                connected_system_labels_fn=lambda _availability: [],
                context_tool_capabilities_fn=lambda _availability: [],
                build_direct_chat_tools_fn=lambda _tool_capabilities: [],
                build_local_direct_chat_tools_fn=lambda _availability: [],
                build_builtin_direct_chat_tools_fn=lambda: [],
                build_always_on_direct_chat_tools_fn=lambda: [],
                build_registry_entries_fn=lambda tool_capabilities, availability_payload: [],
                normalize_direct_approved_action_fn=lambda _value: None,
                build_context_used_fn=lambda **kwargs: kwargs,
                direct_chat_compaction_token_limit=8_000,
            )

        self.assertEqual(captured_kwargs.get("max_tokens"), 8_000)
        self.assertEqual(captured_kwargs.get("preserve_last_messages"), 10)

    def test_prepare_direct_chat_request_migrates_legacy_deepseek_default_to_pro_route(self) -> None:
        with patch.object(
            direct_chat_entry_service.session_transcript_store,
            "load_latest_session_transcript_messages",
            return_value=[],
        ):
            prepared = direct_chat_entry_service.prepare_direct_chat_request(
                resolved_turn_request=None,
                session_ctx=None,
                message="hello",
                workspace_id="default",
                thread_id="thread-1",
                requested_model="deepseek-chat",
                requested_provider="deepseek",
                prior_messages=[],
                reasoning_effort="",
                availability={"ai_ready": True},
                approved_action=None,
                max_iterations=4,
                direct_chat_session_key_fn=lambda workspace_id, thread_id: f"{workspace_id}:{thread_id}",
                resolved_chat_iteration_limit_fn=lambda value: int(value or 30),
                session_model_preference_fn=lambda _session_key: {},
                normalize_reasoning_effort_fn=lambda value: str(value or "").strip().lower() or None,
                parse_slash_command_fn=lambda _message: {"command": "", "remainder": ""},
                set_session_model_preference_fn=lambda session_key, provider=None, model=None: None,
                mark_thread_cleared_fn=lambda _session_key: None,
                normalize_prior_messages_fn=lambda value: list(value or []),
                consume_thread_cleared_fn=lambda _session_key: False,
                compact_conversation_history_fn=lambda messages, **kwargs: {"messages": list(messages), "compacted": False},
                build_proactive_suggestions_fn=lambda _workspace_id: [],
                direct_tool_session_key_fn=lambda workspace_id, thread_id: f"tool:{workspace_id}:{thread_id}",
                resolve_direct_chat_availability_fn=lambda workspace_id, requested_provider, availability_override=None: {"ai_ready": True, "provider": requested_provider},
                connected_system_labels_fn=lambda _availability: [],
                context_tool_capabilities_fn=lambda _availability: [],
                build_direct_chat_tools_fn=lambda _tool_capabilities: [],
                build_local_direct_chat_tools_fn=lambda _availability: [],
                build_builtin_direct_chat_tools_fn=lambda: [],
                build_always_on_direct_chat_tools_fn=lambda: [],
                build_registry_entries_fn=lambda tool_capabilities, availability_payload: [],
                normalize_direct_approved_action_fn=lambda _value: None,
                build_context_used_fn=lambda **kwargs: kwargs,
                direct_chat_compaction_token_limit=2_000,
            )

        self.assertEqual(prepared.normalized_requested_provider, "deepseek")
        self.assertEqual(prepared.normalized_requested_model, "deepseek-v4-pro")
        self.assertEqual(prepared.normalized_reasoning_effort, "high")

    def test_prepare_direct_chat_request_migrates_legacy_ollama_to_local_ai_without_reroute(self) -> None:
        with patch.object(
            direct_chat_entry_service.session_transcript_store,
            "load_latest_session_transcript_messages",
            return_value=[],
        ):
            prepared = direct_chat_entry_service.prepare_direct_chat_request(
                resolved_turn_request=None,
                session_ctx=None,
                message="hello",
                workspace_id="default",
                thread_id="thread-1",
                requested_model="llama3.2",
                requested_provider="ollama",
                prior_messages=[],
                reasoning_effort="",
                availability={"ai_ready": True},
                approved_action=None,
                max_iterations=4,
                direct_chat_session_key_fn=lambda workspace_id, thread_id: f"{workspace_id}:{thread_id}",
                resolved_chat_iteration_limit_fn=lambda value: int(value or 30),
                session_model_preference_fn=lambda _session_key: {},
                normalize_reasoning_effort_fn=lambda value: str(value or "").strip().lower() or None,
                parse_slash_command_fn=lambda _message: {"command": "", "remainder": ""},
                set_session_model_preference_fn=lambda session_key, provider=None, model=None: None,
                mark_thread_cleared_fn=lambda _session_key: None,
                normalize_prior_messages_fn=lambda value: list(value or []),
                consume_thread_cleared_fn=lambda _session_key: False,
                compact_conversation_history_fn=lambda messages, **kwargs: {"messages": list(messages), "compacted": False},
                build_proactive_suggestions_fn=lambda _workspace_id: [],
                direct_tool_session_key_fn=lambda workspace_id, thread_id: f"tool:{workspace_id}:{thread_id}",
                resolve_direct_chat_availability_fn=lambda workspace_id, requested_provider, availability_override=None: {"ai_ready": True, "provider": requested_provider},
                connected_system_labels_fn=lambda _availability: [],
                context_tool_capabilities_fn=lambda _availability: [],
                build_direct_chat_tools_fn=lambda _tool_capabilities: [],
                build_local_direct_chat_tools_fn=lambda _availability: [],
                build_builtin_direct_chat_tools_fn=lambda: [],
                build_always_on_direct_chat_tools_fn=lambda: [],
                build_registry_entries_fn=lambda tool_capabilities, availability_payload: [],
                normalize_direct_approved_action_fn=lambda _value: None,
                build_context_used_fn=lambda **kwargs: kwargs,
                direct_chat_compaction_token_limit=2_000,
            )

        self.assertEqual(prepared.normalized_requested_provider, "ollama")
        self.assertEqual(prepared.normalized_requested_model, "llama3.2")
        self.assertIsNone(prepared.normalized_reasoning_effort)

    def test_prepare_direct_chat_request_migrates_workspace_byok_to_my_api_key_without_hosted_reroute(self) -> None:
        with patch.object(
            direct_chat_entry_service.session_transcript_store,
            "load_latest_session_transcript_messages",
            return_value=[],
        ):
            prepared = direct_chat_entry_service.prepare_direct_chat_request(
                resolved_turn_request=None,
                session_ctx={"agent_turn_request": {"context_hints": {"metadata": {"credential_owner_kind": "workspace_byok"}}}},
                message="hello",
                workspace_id="default",
                thread_id="thread-1",
                requested_model="gpt-4o",
                requested_provider="openai",
                prior_messages=[],
                reasoning_effort="",
                availability={"ai_ready": True},
                approved_action=None,
                max_iterations=4,
                direct_chat_session_key_fn=lambda workspace_id, thread_id: f"{workspace_id}:{thread_id}",
                resolved_chat_iteration_limit_fn=lambda value: int(value or 30),
                session_model_preference_fn=lambda _session_key: {},
                normalize_reasoning_effort_fn=lambda value: str(value or "").strip().lower() or None,
                parse_slash_command_fn=lambda _message: {"command": "", "remainder": ""},
                set_session_model_preference_fn=lambda session_key, provider=None, model=None: None,
                mark_thread_cleared_fn=lambda _session_key: None,
                normalize_prior_messages_fn=lambda value: list(value or []),
                consume_thread_cleared_fn=lambda _session_key: False,
                compact_conversation_history_fn=lambda messages, **kwargs: {"messages": list(messages), "compacted": False},
                build_proactive_suggestions_fn=lambda _workspace_id: [],
                direct_tool_session_key_fn=lambda workspace_id, thread_id: f"tool:{workspace_id}:{thread_id}",
                resolve_direct_chat_availability_fn=lambda workspace_id, requested_provider, availability_override=None: {"ai_ready": True, "provider": requested_provider},
                connected_system_labels_fn=lambda _availability: [],
                context_tool_capabilities_fn=lambda _availability: [],
                build_direct_chat_tools_fn=lambda _tool_capabilities: [],
                build_local_direct_chat_tools_fn=lambda _availability: [],
                build_builtin_direct_chat_tools_fn=lambda: [],
                build_always_on_direct_chat_tools_fn=lambda: [],
                build_registry_entries_fn=lambda tool_capabilities, availability_payload: [],
                normalize_direct_approved_action_fn=lambda _value: None,
                build_context_used_fn=lambda **kwargs: kwargs,
                direct_chat_compaction_token_limit=2_000,
            )

        self.assertEqual(prepared.normalized_requested_provider, "openai")
        self.assertEqual(prepared.normalized_requested_model, "gpt-4o")


if __name__ == "__main__":
    unittest.main()
