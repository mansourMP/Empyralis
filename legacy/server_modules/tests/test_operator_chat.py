import unittest
import importlib.util
import sys
from pathlib import Path
from unittest.mock import Mock, patch

from server_modules import direct_chat_operator_binding_service

ROOT_DIR = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT_DIR / "server_modules" / "direct_chat_runtime_exports.py"
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
spec = importlib.util.spec_from_file_location("operator_chat_under_test", MODULE_PATH)
operator_chat = importlib.util.module_from_spec(spec)
sys.modules["operator_chat_under_test"] = operator_chat
assert spec and spec.loader
spec.loader.exec_module(operator_chat)


def _legacy_fallback_stream(**kwargs):
    def _iterator():
        metadata = kwargs.get("metadata") if isinstance(kwargs.get("metadata"), dict) else {}
        for attempt in range(2):
            raw = operator_chat.generate_chat_reply_with_provider_fallback(
                context=kwargs.get("context"),
                metadata=kwargs.get("metadata"),
                user_goal=kwargs.get("user_goal"),
                system_prompt=kwargs.get("system_prompt"),
                prior_messages=kwargs.get("prior_messages"),
            )
            if not isinstance(raw, tuple) or len(raw) != 4:
                yield {
                    "type": "failure",
                    "attempted_providers": "",
                    "error": "mocked_generate_chat_reply_with_provider_fallback_missing_return_value",
                }
                return
            reply, usage_masked, attempted_providers, error = raw
            usage_payload = usage_masked if isinstance(usage_masked, dict) else {}
            provider = str(usage_payload.get("provider") or metadata.get("provider") or "").strip() or None
            model = str(usage_payload.get("model") or metadata.get("model") or "").strip() or None
            normalized_error = str(error or "").strip()
            if not str(reply or "").strip() and normalized_error:
                if attempt == 0 and not normalized_error.startswith("direct_chat_transport_unavailable:"):
                    continue
                yield {
                    "type": "failure",
                    "attempted_providers": str(attempted_providers or "").strip(),
                    "error": normalized_error,
                }
                return
            yield {
                "type": "result",
                "reply": str(reply or ""),
                "usage_masked": usage_payload,
                "provider": provider,
                "model": model,
                "attempted_providers": str(attempted_providers or "").strip(),
                "error": normalized_error,
            }
            return

    return _iterator()


def _rebuild_operator_chat_exports() -> None:
    operator_chat.globals = globals
    operator_chat.__dict__.update(
        direct_chat_operator_binding_service.build_direct_chat_module_export_map_from_namespace(
            namespace=operator_chat.__dict__,
            agent_machine_full_trust_enabled_fn=operator_chat.runtime_config.agent_machine_full_trust_enabled,
            chat_max_iterations_default=operator_chat.CHAT_MAX_ITERATIONS_DEFAULT,
            chat_max_iterations_ceiling=operator_chat.CHAT_MAX_ITERATIONS_CEILING,
            compact_text_fn=lambda value: " ".join(str(value or "").split()).strip().lower(),
            direct_chat_compaction_token_limit=operator_chat.DIRECT_CHAT_COMPACTION_TOKEN_LIMIT,
            execution_markers=operator_chat.EXECUTION_MARKERS,
            question_openers=operator_chat.QUESTION_OPENERS,
            direct_run_openers=operator_chat.DIRECT_RUN_OPENERS,
            workflow_request_markers=operator_chat.WORKFLOW_REQUEST_MARKERS,
            google_workspace_keywords=operator_chat.GOOGLE_WORKSPACE_KEYWORDS,
            smtp_keywords=operator_chat.SMTP_KEYWORDS,
            telegram_keywords=operator_chat.TELEGRAM_KEYWORDS,
            slack_keywords=operator_chat.SLACK_KEYWORDS,
            dropbox_keywords=operator_chat.DROPBOX_KEYWORDS,
            s3_keywords=operator_chat.S3_KEYWORDS,
            max_context_tool_actions=operator_chat.MAX_CONTEXT_TOOL_ACTIONS,
            max_context_tool_capabilities=operator_chat.MAX_CONTEXT_TOOL_CAPABILITIES,
            max_direct_chat_prior_message_chars=operator_chat.MAX_DIRECT_CHAT_PRIOR_MESSAGE_CHARS,
            max_direct_chat_prior_messages=operator_chat.MAX_DIRECT_CHAT_PRIOR_MESSAGES,
            direct_chat_model_preferences=operator_chat._DIRECT_CHAT_MODEL_PREFERENCES,
            direct_chat_clear_markers=operator_chat._DIRECT_CHAT_CLEAR_MARKERS,
            direct_tool_loop_state=operator_chat._DIRECT_TOOL_LOOP_STATE,
            direct_chat_loop_repeat_limit=operator_chat.DIRECT_CHAT_LOOP_REPEAT_LIMIT,
            parse_json_object_loose_fn=operator_chat.parse_json_object_loose,
            generate_reply_fn=operator_chat.generate_chat_reply_with_provider_fallback,
            extraction_prompt=operator_chat._DIRECT_CHAT_MEMORY_EXTRACTION_PROMPT,
            extraction_system_prompt=operator_chat._DIRECT_CHAT_MEMORY_EXTRACTION_SYSTEM_PROMPT,
            save_session_transcript_fn=operator_chat.save_session_transcript,
            system_prefix=operator_chat._DIRECT_CHAT_MEMORY_SYSTEM_PREFIX,
            workspace_context_dir_fn=operator_chat.workspace_context_dir,
            memory_suggestion_prompts_fn=lambda workspace_id: operator_chat.memory_service.memory_suggestion_prompts(
                workspace_id,
                limit=2,
            ),
            direct_chat_run_snapshot_fn=lambda run_id: (
                getattr(operator_chat, "direct_chat_run_snapshot", operator_chat._direct_chat_run_snapshot)(run_id)
            ),
            direct_chat_run_event_to_step_fn=lambda run_id, event: (
                getattr(operator_chat, "direct_chat_run_event_to_step", operator_chat._direct_chat_run_event_to_step)(run_id, event)
            ),
            direct_chat_run_snapshot_to_step_fn=lambda run_id, snapshot: (
                getattr(operator_chat, "direct_chat_run_snapshot_to_step", operator_chat._direct_chat_run_snapshot_to_step)(run_id, snapshot)
            ),
            direct_chat_run_final_payload_fn=lambda **kwargs: (
                getattr(operator_chat, "direct_chat_run_final_payload", operator_chat._direct_chat_run_final_payload)(**kwargs)
            ),
            live_window_seconds=operator_chat.DIRECT_CHAT_RUN_HANDOFF_LIVE_WINDOW_SECONDS,
            poll_seconds=operator_chat.DIRECT_CHAT_RUN_HANDOFF_POLL_SECONDS,
            monotonic_fn=operator_chat.time.monotonic,
            sleep_fn=operator_chat.time.sleep,
            parse_json_object_loose_support_fn=operator_chat.parse_json_object_loose,
            direct_chat_tool_policy_callbacks_fn=lambda: operator_chat._direct_chat_tool_policy_callbacks(),
            direct_chat_routing_policy_callbacks_fn=lambda: operator_chat._direct_chat_routing_policy_callbacks(),
            capture_exception_fn=operator_chat.sentry_sdk.capture_exception,
            generate_chat_reply_stream_with_provider_fallback_fn=operator_chat.generate_chat_reply_stream_with_provider_fallback,
            compact_conversation_history_fn=operator_chat.compact_conversation_history,
            parse_memory_write_fn=operator_chat.memory_service.parse_no_provider_memory_write,
            parse_memory_read_fn=operator_chat.memory_service.parse_no_provider_memory_read,
            handle_memory_request_fn=operator_chat.memory_service.handle_no_provider_memory_request,
            list_memory_entries_fn=operator_chat.list_memory_entries,
            get_memory_fn=operator_chat.get_memory,
            delete_memory_fn=operator_chat.delete_memory,
            no_provider_reasoning_required_response_fn=operator_chat.no_provider_service.no_provider_reasoning_required_response,
            supported_providers=list(operator_chat.SUPPORTED_PROVIDERS),
            complex_task_sequence_markers=operator_chat.COMPLEX_TASK_SEQUENCE_MARKERS,
            complex_task_outcome_markers=operator_chat.COMPLEX_TASK_OUTCOME_MARKERS,
            discord_keywords=operator_chat.DISCORD_KEYWORDS,
            browser_keywords=operator_chat.BROWSER_KEYWORDS,
            local_file_keywords=operator_chat.LOCAL_FILE_KEYWORDS,
            local_shell_keywords=operator_chat.LOCAL_SHELL_KEYWORDS,
            local_screenshot_keywords=operator_chat.LOCAL_SCREENSHOT_KEYWORDS,
            local_computer_control_keywords=operator_chat.LOCAL_COMPUTER_CONTROL_KEYWORDS,
            web_lookup_keywords=operator_chat.WEB_LOOKUP_KEYWORDS,
            http_request_keywords=operator_chat.HTTP_REQUEST_KEYWORDS,
            image_generation_keywords=operator_chat.IMAGE_GENERATION_KEYWORDS,
            llm_task_keywords=operator_chat.LLM_TASK_KEYWORDS,
            llm_task_fn=operator_chat.llm_task,
            web_search_fn=operator_chat.web_search,
            web_fetch_fn=operator_chat.web_fetch,
            search_memory_notebook_fn=operator_chat.search_memory_notebook,
            get_memory_notebook_excerpt_fn=operator_chat.get_memory_notebook_excerpt,
            build_operator_system_prompt_fn=operator_chat.build_operator_system_prompt,
            memory_tool_names=operator_chat._MEMORY_NOTEBOOK_TOOL_NAMES,
            local_worker_registry=operator_chat.LOCAL_WORKER_REGISTRY,
            is_worker_online_fn=operator_chat._is_worker_online,
            resolve_workspace_tool_capabilities_fn=operator_chat.resolve_workspace_tool_capabilities,
            build_provider_credential_candidates_fn=operator_chat._build_provider_credential_candidates,
            normalize_auth_mode_fn=operator_chat.normalize_auth_mode,
            get_claude_code_session_token_fn=operator_chat.get_claude_code_session_token,
            provider_has_key_fn=operator_chat.provider_has_key,
            chat_iteration_limit_reply_fn=operator_chat._chat_iteration_limit_reply,
            safe_positive_int_fn=operator_chat._safe_positive_int,
        )
    )
    public_aliases = {
        "direct_chat_credentials": "_direct_chat_credentials",
        "supports_direct_message_native_chat": "_supports_direct_message_native_chat",
        "execute_single_direct_tool_call": "_execute_single_direct_tool_call",
        "can_auto_start_run_handoff": "_can_auto_start_run_handoff",
        "persist_direct_chat_memory_best_effort": "_persist_direct_chat_memory_best_effort",
        "preferred_provider": "_preferred_provider",
        "start_direct_chat_run_handoff": "_start_direct_chat_run_handoff",
        "direct_chat_run_snapshot": "_direct_chat_run_snapshot",
        "message_can_use_direct_connector_tools": "_message_can_use_direct_connector_tools",
    }
    for public_name, private_name in public_aliases.items():
        current_public = operator_chat.__dict__.get(public_name)
        if (
            private_name in operator_chat.__dict__
            and not isinstance(current_public, Mock)
        ):
            operator_chat.__dict__[public_name] = operator_chat.__dict__[private_name]


operator_chat.generate_chat_reply_stream_with_provider_fallback = _legacy_fallback_stream
_rebuild_operator_chat_exports()

def build_direct_operator_reply(**kwargs):
    _rebuild_operator_chat_exports()
    return operator_chat.collect_direct_operator_reply(**kwargs)


def stream_direct_operator_reply(**kwargs):
    _rebuild_operator_chat_exports()
    return operator_chat.build_direct_operator_reply(**kwargs)


class OperatorChatTests(unittest.TestCase):
    def assertIntervention(self, payload, kind: str, *, title: str | None = None, detail_contains: str | None = None) -> dict:
        if kind == "system_error":
            self.assertTrue(str(payload.get("reply") or "").strip())
        else:
            self.assertEqual(payload["reply"], "")
        interventions = payload.get("interventions")
        self.assertTrue(interventions)
        intervention = interventions[0]
        self.assertEqual(intervention["kind"], kind)
        if title is not None:
            self.assertEqual(intervention["title"], title)
        if detail_contains is not None:
            self.assertIn(detail_contains, str(intervention.get("detail") or ""))
        return intervention

    def setUp(self) -> None:
        self._semantic_model_patch = patch.object(operator_chat.memory_service._workspace_memory_store, "_SEMANTIC_MODEL", False)
        self._semantic_model_patch.start()
        self._supports_direct_message_patch = patch.object(
            operator_chat,
            "supports_direct_message_native_chat",
            side_effect=lambda provider, credentials: bool(credentials) or bool(operator_chat.provider_has_key(provider)),
        )
        self._supports_direct_message_patch.start()

    def tearDown(self) -> None:
        self._supports_direct_message_patch.stop()
        self._semantic_model_patch.stop()

    def test_preferred_provider_keeps_explicit_openai_oauth_selection(self):
        def fake_credentials(_workspace_id, provider):
            if provider == "openai":
                return {"auth_mode": "oauth_token", "oauth_token": "token"}
            if provider == "codex_cli":
                return {"auth_mode": "oauth_token", "oauth_token": "token"}
            return {}

        def fake_support(provider, credentials):
            return provider == "codex_cli" and bool(credentials)

        with patch("operator_chat_under_test.direct_chat_credentials", side_effect=fake_credentials):
            with patch("operator_chat_under_test.supports_direct_message_native_chat", side_effect=fake_support):
                provider, credentials = operator_chat._preferred_provider("default", "openai")

        self.assertEqual(provider, "openai")
        self.assertEqual(credentials.get("auth_mode"), "oauth_token")

    def test_preferred_provider_keeps_explicit_openai_codex_token_selection(self):
        def fake_credentials(_workspace_id, provider):
            if provider == "openai":
                return {"credential_type": "codex_token", "access_token": "token"}
            if provider == "codex_cli":
                return {"auth_mode": "oauth_token", "oauth_token": "token"}
            return {}

        def fake_support(provider, credentials):
            return provider == "codex_cli" and bool(credentials)

        with patch("operator_chat_under_test.direct_chat_credentials", side_effect=fake_credentials):
            with patch("operator_chat_under_test.supports_direct_message_native_chat", side_effect=fake_support):
                provider, credentials = operator_chat._preferred_provider("default", "openai")

        self.assertEqual(provider, "openai")
        self.assertEqual(credentials.get("credential_type"), "codex_token")

    @patch("operator_chat_under_test.resolve_workspace_tool_capabilities", return_value=[])
    @patch(
        "operator_chat_under_test.execute_single_direct_tool_call",
        return_value="1. Top headline\nURL: https://example.com\nSnippet: Example AI headline",
    )
    @patch(
        "operator_chat_under_test.generate_chat_reply_stream_with_provider_fallback",
        side_effect=AssertionError("generation path should not run for obvious direct tool prompts"),
    )
    @patch("operator_chat_under_test.supports_direct_message_native_chat", return_value=False)
    @patch("operator_chat_under_test.provider_has_key", return_value=False)
    def test_obvious_direct_tool_prompt_bypasses_generation(
        self,
        _provider_has_key,
        _supports_direct_message_native_chat,
        _generate_chat_reply_stream_with_provider_fallback,
        _execute_single_direct_tool_call,
        _capabilities,
    ):
        payload = build_direct_operator_reply(
            message="Search for today's top AI news headline",
            workspace_id="default",
            requested_model="gpt-5.4",
            requested_provider="openai",
            availability={"ai_ready": True},
        )

        self.assertEqual(payload["mode"], "answer")
        self.assertIn("Top headline", payload["reply"])
        self.assertEqual(payload["context_used"]["fallback_reason"], "obvious_direct_tool_execution")

    @patch("operator_chat_under_test.can_auto_start_run_handoff", return_value=False)
    @patch("operator_chat_under_test.resolve_workspace_tool_capabilities", return_value=[])
    @patch("operator_chat_under_test.persist_direct_chat_memory_best_effort", return_value=None)
    @patch("operator_chat_under_test.preferred_provider", return_value=("openai", {"api_key": "sk-test"}))
    def test_direct_chat_locks_model_call_to_resolved_provider(
        self,
        _preferred_provider,
        _persist_memory,
        _capabilities,
        _handoff,
    ):
        def fake_stream(**kwargs):
            self.assertTrue(kwargs["context"].get("disable_provider_fallback"))
            self.assertTrue(kwargs["metadata"].get("disable_provider_fallback"))
            self.assertEqual(kwargs["context"].get("provider"), "openai")
            self.assertEqual(kwargs["metadata"].get("provider"), "openai")

            def _iterator():
                yield {
                    "type": "result",
                    "reply": "Hello",
                    "usage_masked": {
                        "provider": "openai",
                        "model": "gpt-5.4",
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                        "estimation_mode": "provider_usage_exact",
                    },
                    "provider": "openai",
                    "model": "gpt-5.4",
                    "attempted_providers": "openai",
                    "error": "",
                    "tool_calls": [],
                }
            return _iterator()

        with (
            patch("operator_chat_under_test.generate_chat_reply_stream_with_provider_fallback", side_effect=fake_stream),
            patch("operator_chat_under_test._persist_direct_chat_hosted_usage_best_effort", lambda **_kwargs: None),
            patch("server_modules.direct_chat_hosted_usage_service.persist_direct_chat_hosted_usage_best_effort", lambda **_kwargs: None),
        ):
            payload = build_direct_operator_reply(
                message="hello",
                workspace_id="default",
                requested_model="gpt-5.4",
                requested_provider="openai",
                availability={
                    "ai_ready": True,
                    "credential_plane": "workspace_connection",
                    "billing_source": "user_api_key",
                },
            )

        self.assertEqual(payload["reply"], "Hello")
        self.assertEqual(payload["provider"], "openai")

    @patch("operator_chat_under_test.can_auto_start_run_handoff", return_value=False)
    @patch("operator_chat_under_test.preferred_provider", return_value=("openai", {"api_key": "sk-test"}))
    @patch("operator_chat_under_test.provider_has_key", return_value=True)
    @patch("operator_chat_under_test.resolve_workspace_tool_capabilities", return_value=[])
    def test_streaming_reply_does_not_duplicate_final_chunk(self, _capabilities, _provider_has_key, _preferred_provider, _handoff):
        def fake_stream(**kwargs):
            def _iterator():
                yield {"type": "chunk", "delta": "Hel"}
                yield {"type": "chunk", "delta": "lo"}
                yield {
                    "type": "result",
                    "reply": "Hello",
                    "usage_masked": {
                        "provider": "openai",
                        "model": "gpt-5.4",
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                        "estimation_mode": "provider_usage_exact",
                    },
                    "provider": "openai",
                    "model": "gpt-5.4",
                    "attempted_providers": "openai",
                    "error": "",
                    "tool_calls": [],
                }
            return _iterator()

        with (
            patch("operator_chat_under_test.generate_chat_reply_stream_with_provider_fallback", side_effect=fake_stream),
            patch("operator_chat_under_test._persist_direct_chat_hosted_usage_best_effort", lambda **_kwargs: None),
            patch("server_modules.direct_chat_hosted_usage_service.persist_direct_chat_hosted_usage_best_effort", lambda **_kwargs: None),
        ):
            events = list(stream_direct_operator_reply(
                message="hello",
                workspace_id="default",
                requested_model="gpt-5.4",
                requested_provider="openai",
                availability={
                    "ai_ready": True,
                    "credential_plane": "workspace_connection",
                    "billing_source": "user_api_key",
                    "local_worker_online": False,
                },
            ))

        chunk_deltas = [str(event.get("delta") or "") for event in events if str(event.get("type") or "") == "chunk"]
        final_payloads = [event.get("payload") for event in events if str(event.get("type") or "") == "final"]
        self.assertEqual(chunk_deltas, ["Hel", "lo"])
        self.assertEqual(len(final_payloads), 1)
        self.assertEqual(final_payloads[0]["reply"], "Hello")

    @patch(
        "operator_chat_under_test.generate_chat_reply_with_provider_fallback",
        return_value=("Email can be discussed normally.", {"provider": "openai", "model": "gpt-5.4"}, "openai", ""),
    )
    @patch("operator_chat_under_test.resolve_workspace_tool_capabilities", return_value=[])
    def test_missing_google_workspace_does_not_block_generation(self, _capabilities, generate_reply):
        payload = build_direct_operator_reply(
            message="Could you summarize my emails?",
            workspace_id="default",
            requested_model="gpt-5.4",
            requested_provider="openai",
            availability={
                "ai_ready": True,
                "credential_plane": "workspace_connection",
                "billing_source": "user_api_key",
            },
        )

        self.assertEqual(payload["reply"], "Email can be discussed normally.")
        self.assertEqual(payload["actions"], [])
        self.assertEqual(payload.get("interventions") or [], [])
        generate_reply.assert_called()

    @patch("operator_chat_under_test.generate_chat_reply_with_provider_fallback")
    @patch("operator_chat_under_test.can_auto_start_run_handoff", return_value=False)
    @patch("operator_chat_under_test.message_can_use_direct_connector_tools", return_value=False)
    @patch("operator_chat_under_test.provider_has_key", return_value=True)
    @patch(
        "operator_chat_under_test.resolve_workspace_tool_capabilities",
        return_value=[{
            "id": "google_workspace",
            "label": "Google Workspace",
            "connected": True,
            "authenticated": True,
            "runtime_usable": True,
            "read_actions": ["gmail_threads.read"],
            "write_actions": ["draft_email"],
            "approval_required_actions": ["draft_email"],
        }],
    )
    def test_connected_google_workspace_returns_generic_run_preview(self, _capabilities, _provider_has_key, _message_can_use_direct_connector_tools, _can_auto_start_run_handoff, generate_reply):
        payload = build_direct_operator_reply(
            message="Summarize my Gmail inbox.",
            workspace_id="default",
            requested_model="gpt-5.4",
            requested_provider="openai",
            availability={"ai_ready": True},
        )

        self.assertEqual(payload["mode"], "answer_with_action")
        self.assertIntervention(payload, "run_offer", title="Ready to run this task")
        self.assertTrue(any(action["kind"] == "run" for action in payload["actions"]))
        generate_reply.assert_not_called()

    @patch("operator_chat_under_test.generate_chat_reply_with_provider_fallback")
    @patch("operator_chat_under_test.provider_has_key", return_value=False)
    @patch(
        "operator_chat_under_test.resolve_workspace_tool_capabilities",
        return_value=[{
            "id": "telegram_bot",
            "label": "Telegram Bot LIVE",
            "connected": True,
            "authenticated": True,
            "runtime_usable": True,
            "read_actions": ["chats.read"],
            "write_actions": ["send_message"],
            "approval_required_actions": ["send_message"],
        }],
    )
    def test_obvious_telegram_write_preview_bypasses_ai_ready_gate(self, _capabilities, _provider_has_key, generate_reply):
        with patch("operator_chat_under_test.message_can_use_direct_connector_tools", return_value=False):
            payload = build_direct_operator_reply(
                message="Send a Telegram message to my test chat saying certification probe one.",
                workspace_id="default",
                requested_model="gpt-5.4",
                requested_provider="openai",
                availability={"ai_ready": False},
            )

        self.assertEqual(payload["mode"], "answer_with_action")
        self.assertIntervention(payload, "run_offer", title="Ready to run this task")
        self.assertTrue(any(action["kind"] == "run" for action in payload["actions"]))
        generate_reply.assert_not_called()

    @patch("operator_chat_under_test.generate_chat_reply_with_provider_fallback")
    @patch("operator_chat_under_test.provider_has_key", return_value=False)
    @patch(
        "operator_chat_under_test.resolve_workspace_tool_capabilities",
        return_value=[{
            "id": "google_workspace",
            "label": "Google Workspace",
            "connected": True,
            "authenticated": True,
            "runtime_usable": True,
            "read_actions": ["gmail_threads.read"],
            "write_actions": ["draft_email", "create_calendar_event"],
            "approval_required_actions": ["draft_email", "create_calendar_event"],
        }],
    )
    def test_obvious_google_draft_preview_bypasses_ai_ready_gate(self, _capabilities, _provider_has_key, generate_reply):
        with patch("operator_chat_under_test.message_can_use_direct_connector_tools", return_value=False):
            payload = build_direct_operator_reply(
                message="Draft an email to myself summarizing today's certification results.",
                workspace_id="default",
                requested_model="gpt-5.4",
                requested_provider="openai",
                availability={"ai_ready": False},
            )

        self.assertEqual(payload["mode"], "answer_with_action")
        self.assertIntervention(payload, "run_offer", title="Ready to run this task")
        self.assertTrue(any(action["kind"] == "run" for action in payload["actions"]))
        generate_reply.assert_not_called()

    @patch(
        "operator_chat_under_test.generate_chat_reply_with_provider_fallback",
        return_value=("Hello again.", {"provider": "openai", "model": "gpt-5.4"}, "openai", ""),
    )
    @patch("operator_chat_under_test.provider_has_key", return_value=True)
    @patch("operator_chat_under_test.resolve_workspace_tool_capabilities", return_value=[])
    def test_context_used_marks_prior_messages_when_history_is_passed(self, _capabilities, _provider_has_key, _generate_reply):
        payload = build_direct_operator_reply(
            message="Reply with context.",
            workspace_id="default",
            requested_model="gpt-5.4",
            requested_provider="openai",
            prior_messages=[
                {"role": "user", "content": "First question"},
                {"role": "assistant", "content": "First answer"},
            ],
            availability={"ai_ready": True},
        )

        self.assertTrue(payload["context_used"]["prior_messages_used"])
        self.assertEqual(payload["context_used"]["history_mode"], "raw_messages")

    @patch(
        "operator_chat_under_test.generate_chat_reply_with_provider_fallback",
        return_value=("Hello.", {"provider": "openai", "model": "gpt-5.4"}, "openai", ""),
    )
    @patch("operator_chat_under_test.provider_has_key", return_value=True)
    @patch("operator_chat_under_test.resolve_workspace_tool_capabilities", return_value=[])
    def test_context_used_uses_none_history_mode_without_prior_messages(self, _capabilities, _provider_has_key, _generate_reply):
        payload = build_direct_operator_reply(
            message="hello",
            workspace_id="default",
            requested_model="gpt-5.4",
            requested_provider="openai",
            availability={"ai_ready": True},
        )

        self.assertFalse(payload["context_used"]["prior_messages_used"])
        self.assertEqual(payload["context_used"]["history_mode"], "none")

    @patch.dict("operator_chat_under_test.os.environ", {"ORION_AUTH_MODE": "codex"}, clear=False)
    @patch("operator_chat_under_test.direct_chat_credentials", return_value={})
    @patch("operator_chat_under_test.provider_has_key", return_value=False)
    @patch("operator_chat_under_test.resolve_workspace_tool_capabilities", return_value=[])
    def test_codex_chat_unavailable_fails_closed(self, _capabilities, _provider_has_key, _direct_chat_credentials):
        payload = build_direct_operator_reply(
            message="hello",
            workspace_id="default",
            requested_model="gpt-5.4",
            requested_provider="openai",
            availability={"ai_ready": True},
        )

        self.assertEqual(payload["mode"], "connect")
        self.assertEqual(payload["reply"], "")
        self.assertEqual(payload["actions"], [{"label": "Connect", "href": "/connect-ai"}])
        self.assertIntervention(
            payload,
            "connect_required",
            title="OpenAI is not available",
            detail_contains="OpenAI",
        )
        self.assertFalse(payload["context_used"]["provider_overridden"])
        self.assertFalse(payload["context_used"]["fallback_used"])

    @patch(
        "operator_chat_under_test.generate_chat_reply_with_provider_fallback",
        return_value=("Hello from Gemini.", {"provider": "gemini", "model": "gemini-2.0-flash"}, "gemini", ""),
    )
    @patch("operator_chat_under_test.direct_chat_credentials", return_value={})
    @patch("operator_chat_under_test.provider_has_key", side_effect=lambda provider: provider == "gemini")
    @patch("operator_chat_under_test.resolve_workspace_tool_capabilities", return_value=[])
    def test_selected_provider_unavailable_fails_closed(self, _capabilities, _provider_has_key, _direct_chat_credentials, _generate_reply):
        payload = build_direct_operator_reply(
            message="hello",
            workspace_id="default",
            requested_model="gpt-5.4",
            requested_provider="openai",
            availability={"ai_ready": True},
        )

        self.assertEqual(payload["mode"], "connect")
        self.assertEqual(payload["actions"], [{"label": "Connect", "href": "/connect-ai"}])
        self.assertIntervention(
            payload,
            "connect_required",
            title="OpenAI is not available",
            detail_contains="OpenAI",
        )
        self.assertIsNone(payload["context_used"]["effective_provider"])
        self.assertFalse(payload["context_used"]["provider_overridden"])

    @patch("operator_chat_under_test.generate_chat_reply_with_provider_fallback")
    @patch("operator_chat_under_test.provider_has_key", return_value=True)
    @patch("operator_chat_under_test.resolve_workspace_tool_capabilities", return_value=[])
    def test_explicit_workflow_request_returns_workflow_action(self, _capabilities, _provider_has_key, generate_reply):
        payload = build_direct_operator_reply(
            message="Turn this into a workflow for me.",
            workspace_id="default",
            requested_model="gpt-5.4",
            requested_provider="openai",
            availability={"ai_ready": True},
        )

        self.assertEqual(payload["mode"], "answer_with_action")
        self.assertTrue(any(action["kind"] == "workflow" for action in payload["actions"]))
        self.assertIntervention(payload, "workflow_offer", title="Ready to turn this into a workflow")
        generate_reply.assert_not_called()

    @patch(
        "operator_chat_under_test.generate_chat_reply_with_provider_fallback",
        return_value=("Workflows help structure repeated tasks.", {"provider": "openai", "model": "gpt-5.4"}, "openai", ""),
    )
    @patch("operator_chat_under_test.provider_has_key", return_value=True)
    @patch("operator_chat_under_test.resolve_workspace_tool_capabilities", return_value=[])
    def test_casual_workflow_question_does_not_emit_workflow_action(self, _capabilities, _provider_has_key, _generate_reply):
        payload = build_direct_operator_reply(
            message="What are workflows in this product?",
            workspace_id="default",
            requested_model="gpt-5.4",
            requested_provider="openai",
            availability={"ai_ready": True},
        )

        self.assertEqual(payload["mode"], "answer")
        self.assertEqual(payload["actions"], [])

    @patch.dict("operator_chat_under_test.os.environ", {"ORION_AUTH_MODE": "codex"}, clear=False)
    @patch(
        "operator_chat_under_test.generate_chat_reply_with_provider_fallback",
        return_value=("Hello.", {"provider": "codex_cli", "model": "gpt-5.4"}, "codex_cli", ""),
    )
    @patch("operator_chat_under_test.preferred_provider", return_value=("openai", {"api_key": "sk-test"}))
    @patch("operator_chat_under_test.provider_has_key", return_value=True)
    @patch("operator_chat_under_test.resolve_workspace_tool_capabilities", return_value=[])
    def test_context_used_exposes_requested_vs_effective_provider(self, _capabilities, _provider_has_key, _preferred_provider, _generate_reply):
        payload = build_direct_operator_reply(
            message="hello",
            workspace_id="default",
            requested_model="gpt-5.4",
            requested_provider="openai",
            availability={
                "ai_ready": True,
                "credential_plane": "workspace_connection",
                "billing_source": "user_api_key",
            },
        )

        self.assertEqual(payload["context_used"]["requested_provider"], "openai")
        self.assertEqual(payload["context_used"]["effective_provider"], "codex_cli")
        self.assertTrue(payload["context_used"]["provider_overridden"])
        self.assertFalse(payload["context_used"]["fallback_used"])

    @patch("operator_chat_under_test.generate_chat_reply_with_provider_fallback", return_value=("placeholder", {"provider": "codex_cli", "model": "gpt-5.4"}, "codex_cli", ""))
    @patch("operator_chat_under_test.provider_has_key", return_value=True)
    @patch("operator_chat_under_test.resolve_workspace_tool_capabilities", return_value=[])
    def test_model_identity_question_is_not_overridden(self, _capabilities, _provider_has_key, _generate_reply):
        payload = build_direct_operator_reply(
            message="What model are you using right now?",
            workspace_id="default",
            requested_model="gpt-5.4",
            requested_provider="openai",
            availability={"ai_ready": True},
        )

        self.assertEqual(payload["reply"], "placeholder")

    @patch(
        "operator_chat_under_test.generate_chat_reply_with_provider_fallback",
        return_value=("", None, "codex_cli", "direct_chat_transport_unavailable: codex_cli_backend_unavailable"),
    )
    @patch("operator_chat_under_test.provider_has_key", return_value=True)
    @patch("operator_chat_under_test.resolve_workspace_tool_capabilities", return_value=[])
    def test_transport_unavailable_reply_is_explicit(self, _capabilities, _provider_has_key, _generate_reply):
        payload = build_direct_operator_reply(
            message="Write a short answer about project status.",
            workspace_id="default",
            requested_model="gpt-5.4",
            requested_provider="openai",
            availability={"ai_ready": True},
        )

        self.assertIn("temporary error", payload["reply"])
        self.assertEqual(payload.get("interventions") or [], [])

    @patch(
        "operator_chat_under_test.generate_chat_reply_with_provider_fallback",
        return_value=("I can access Google Workspace here.", {"provider": "openai", "model": "gpt-5.4"}, "openai", ""),
    )
    @patch("operator_chat_under_test.provider_has_key", return_value=True)
    @patch(
        "operator_chat_under_test.resolve_workspace_tool_capabilities",
        return_value=[{
            "id": "google_workspace",
            "label": "Google Workspace",
            "connected": True,
            "authenticated": None,
            "runtime_usable": None,
            "read_actions": [],
            "write_actions": [],
            "approval_required_actions": [],
        }],
    )
    def test_capability_question_uses_tool_catalog_reply(self, _capabilities, _provider_has_key, generate_reply):
        payload = build_direct_operator_reply(
            message="What can you do in this environment?",
            workspace_id="default",
            requested_model="gpt-5.4",
            requested_provider="openai",
            availability={"ai_ready": True},
        )

        self.assertIn("Here is what Sage can do", payload["reply"])
        self.assertIn("Google Workspace", payload["reply"])
        self.assertEqual(payload["mode"], "answer")
        generate_reply.assert_not_called()

    @patch("operator_chat_under_test.generate_chat_reply_with_provider_fallback")
    @patch(
        "operator_chat_under_test.resolve_workspace_tool_capabilities",
        return_value=[{
            "id": "telegram_bot",
            "label": "Telegram Bot LIVE",
            "connected": True,
            "authenticated": True,
            "runtime_usable": True,
            "read_actions": ["chats.read"],
            "write_actions": ["send_message"],
            "approval_required_actions": ["send_message"],
        }],
    )
    def test_no_ai_account_plain_chat_and_capability_question_share_one_contract(self, _capabilities, generate_reply):
        capability_payload = build_direct_operator_reply(
            message="What can you help me with in this environment right now?",
            workspace_id="default",
            requested_model="gpt-5.4",
            requested_provider="openai",
            availability={"ai_ready": False},
        )
        plain_payload = build_direct_operator_reply(
            message="My favorite color is blue.",
            workspace_id="default",
            requested_model="gpt-5.4",
            requested_provider="openai",
            availability={"ai_ready": False},
        )

        self.assertEqual(capability_payload["reply"], plain_payload["reply"])
        self.assertEqual(capability_payload["mode"], "connect")
        self.assertEqual(plain_payload["mode"], "connect")
        self.assertEqual(capability_payload["actions"], [{"label": "Connect", "href": "/connect-ai"}])
        self.assertEqual(plain_payload["actions"], [{"label": "Connect", "href": "/connect-ai"}])
        self.assertIntervention(
            capability_payload,
            "connect_required",
            title="OpenAI is not available",
            detail_contains="OpenAI",
        )
        self.assertIntervention(
            plain_payload,
            "connect_required",
            title="OpenAI is not available",
            detail_contains="OpenAI",
        )
        generate_reply.assert_not_called()

    @patch(
        "operator_chat_under_test.generate_chat_reply_with_provider_fallback",
        return_value=("Gmail can be discussed normally.", {"provider": "openai", "model": "gpt-5.4"}, "openai", ""),
    )
    @patch(
        "operator_chat_under_test.resolve_workspace_tool_capabilities",
        return_value=[{
            "id": "google_workspace",
            "label": "Google Workspace",
            "connected": True,
            "authenticated": None,
            "runtime_usable": None,
            "read_actions": [],
            "write_actions": [],
            "approval_required_actions": [],
        }],
    )
    def test_connected_but_unverified_google_workspace_does_not_block_generation(self, _capabilities, generate_reply):
        payload = build_direct_operator_reply(
            message="What does a Gmail inbox label mean?",
            workspace_id="default",
            requested_model="gpt-5.4",
            requested_provider="openai",
            availability={"ai_ready": True},
        )

        self.assertEqual(payload["reply"], "Gmail can be discussed normally.")
        self.assertEqual(payload["actions"], [])
        self.assertEqual(payload.get("interventions") or [], [])
        generate_reply.assert_called()

    @patch(
        "operator_chat_under_test.generate_chat_reply_with_provider_fallback",
        return_value=("Gmail can be discussed normally.", {"provider": "openai", "model": "gpt-5.4"}, "openai", ""),
    )
    @patch(
        "operator_chat_under_test.resolve_workspace_tool_capabilities",
        return_value=[{
            "id": "google_workspace",
            "label": "Google Workspace",
            "connected": True,
            "authenticated": True,
            "runtime_usable": False,
            "read_actions": [],
            "write_actions": [],
            "approval_required_actions": [],
        }],
    )
    def test_connected_but_not_usable_google_workspace_does_not_block_generation(self, _capabilities, generate_reply):
        payload = build_direct_operator_reply(
            message="What does a Gmail inbox label mean?",
            workspace_id="default",
            requested_model="gpt-5.4",
            requested_provider="openai",
            availability={"ai_ready": True},
        )

        self.assertEqual(payload["reply"], "Gmail can be discussed normally.")
        self.assertEqual(payload["actions"], [])
        self.assertEqual(payload.get("interventions") or [], [])
        generate_reply.assert_called()

    @patch(
        "operator_chat_under_test.generate_chat_reply_with_provider_fallback",
        side_effect=[
            ("", {}, "codex_cli", "temporary backend error"),
            ("Hello.", {"provider": "codex_cli", "model": "gpt-5.4"}, "codex_cli", ""),
        ],
    )
    @patch("operator_chat_under_test.provider_has_key", return_value=True)
    @patch("operator_chat_under_test.resolve_workspace_tool_capabilities", return_value=[])
    def test_direct_chat_retries_same_provider_once(self, _capabilities, _provider_has_key, generate_reply):
        payload = build_direct_operator_reply(
            message="hello",
            workspace_id="default",
            requested_model="gpt-5.4",
            requested_provider="openai",
            availability={"ai_ready": True},
        )

        self.assertEqual(payload["reply"], "Hello.")
        self.assertEqual(generate_reply.call_count, 2)

    @patch(
        "operator_chat_under_test.generate_chat_reply_with_provider_fallback",
        return_value=("", {}, "codex_cli", "temporary backend error"),
    )
    @patch("operator_chat_under_test.provider_has_key", return_value=True)
    @patch("operator_chat_under_test.resolve_workspace_tool_capabilities", return_value=[])
    def test_simple_greeting_uses_honest_transport_fallback(self, _capabilities, _provider_has_key, _generate_reply):
        payload = build_direct_operator_reply(
            message="hello",
            workspace_id="default",
            requested_model="gpt-5.4",
            requested_provider="openai",
            availability={"ai_ready": True},
        )

        self.assertIn("temporary error", payload["reply"])
        self.assertEqual(payload.get("interventions") or [], [])

    @patch("operator_chat_under_test.direct_chat_run_snapshot")
    @patch("operator_chat_under_test.start_direct_chat_run_handoff")
    @patch("operator_chat_under_test.preferred_provider", return_value=("openai", {"api_key": "sk-test"}))
    @patch("operator_chat_under_test.resolve_workspace_tool_capabilities", return_value=[])
    def test_execution_preview_auto_starts_durable_run_handoff(
        self,
        _capabilities,
        _preferred_provider,
        start_run_mock,
        snapshot_mock,
    ):
        start_run_mock.return_value = {
            "run_id": "run-handoff-1",
            "route": {"selected": "local_companion"},
            "status": "queued_local",
        }
        snapshot_mock.return_value = (
            {"status": "completed", "result": "Finished on the laptop.", "events": []},
            {
                "run_id": "run-handoff-1",
                "status": "completed",
                "result_summary": "Finished on the laptop.",
                "effective_provider": "openai",
                "effective_model": "gpt-5.4",
                "fallback_used": False,
                "usage_masked": {},
            },
        )

        events = list(
            stream_direct_operator_reply(
                message="Summarize the project docs and prepare next steps.",
                workspace_id="default",
                requested_model="gpt-5.4",
                requested_provider="openai",
                availability={"ai_ready": True, "connection_mode": "local_companion"},
            )
        )

        self.assertEqual(events[0]["type"], "step")
        self.assertEqual(events[0]["label"], "Starting durable run")
        self.assertEqual(events[1]["type"], "step")
        self.assertEqual(events[1]["label"], "Durable run started")
        self.assertEqual(events[-1]["type"], "final")
        self.assertEqual(events[-1]["payload"]["reply"], "Finished on the laptop.")
        self.assertTrue(events[-1]["payload"]["context_used"]["run_created"])
        start_run_mock.assert_called_once()

    @patch("operator_chat_under_test.start_direct_chat_run_handoff")
    @patch("operator_chat_under_test.preferred_provider", return_value=("openai", {"api_key": "sk-test"}))
    @patch("operator_chat_under_test.resolve_workspace_tool_capabilities", return_value=[])
    def test_execution_preview_does_not_auto_start_durable_run_for_byok(
        self,
        _capabilities,
        _preferred_provider,
        start_run_mock,
    ):
        payload = build_direct_operator_reply(
            message="Summarize the project docs and prepare next steps.",
            workspace_id="default",
            requested_model="gpt-5.4",
            requested_provider="openai",
            availability={"ai_ready": True, "connection_mode": "byok"},
        )

        self.assertIntervention(payload, "run_offer", title="Ready to run this task")
        self.assertEqual(payload["mode"], "answer_with_action")
        self.assertFalse(payload["context_used"]["run_created"])
        start_run_mock.assert_not_called()

    @patch("operator_chat_under_test.direct_chat_run_snapshot")
    @patch("operator_chat_under_test.start_direct_chat_run_handoff")
    @patch("operator_chat_under_test.preferred_provider", return_value=("codex_cli", {"auth_mode": "oauth_token", "oauth_token": "token"}))
    @patch("operator_chat_under_test.resolve_workspace_tool_capabilities", return_value=[])
    def test_complex_local_task_prefers_durable_run_handoff(
        self,
        _capabilities,
        _preferred_provider,
        start_run_mock,
        snapshot_mock,
    ):
        start_run_mock.return_value = {
            "run_id": "run-handoff-local-1",
            "route": {"selected": "local_companion"},
            "status": "queued_local",
        }
        snapshot_mock.return_value = (
            {"status": "completed", "result": "Finished the local agent task.", "events": []},
            {
                "run_id": "run-handoff-local-1",
                "status": "completed",
                "result_summary": "Finished the local agent task.",
                "effective_provider": "codex_cli",
                "effective_model": "gpt-5.4",
                "fallback_used": False,
                "usage_masked": {},
            },
        )

        events = list(
            stream_direct_operator_reply(
                message="Open file /tmp/README.md, then inspect the logs and prepare next steps.",
                workspace_id="default",
                requested_model="gpt-5.4",
                requested_provider="codex_cli",
                availability={
                    "ai_ready": True,
                    "runtime_ok": True,
                    "local_gateway_online": True,
                    "connection_mode": "local_companion",
                },
            )
        )

        self.assertEqual(events[0]["type"], "step")
        self.assertEqual(events[0]["label"], "Starting durable run")
        self.assertEqual(events[-1]["type"], "final")
        self.assertEqual(events[-1]["payload"]["reply"], "Finished the local agent task.")
        self.assertTrue(events[-1]["payload"]["context_used"]["run_created"])
        start_run_mock.assert_called_once()

    @patch("operator_chat_under_test.time.monotonic", side_effect=[0.0, 13.0])
    @patch("operator_chat_under_test.direct_chat_run_snapshot")
    def test_handoff_stream_emits_snapshot_waiting_for_runtime_step(
        self,
        snapshot_mock,
        _monotonic,
    ):
        snapshot_mock.return_value = (
            None,
            {
                "run_id": "run-handoff-wait-1",
                "status": "queued_local",
                "execution_target_selected": "local_companion",
                "execution_target_waiting_for_runtime": True,
                "execution_target_preferred_runtime_label": "Mansur's MacBook Air",
                "execution_target_estimated_wait_band": "soon",
                "usage_masked": {},
                "fallback_used": False,
            },
        )

        events = list(
            operator_chat._stream_direct_chat_run_handoff(
                started_run={"run_id": "run-handoff-wait-1"},
                requested_workspace_id="default",
                requested_provider="openai",
                requested_model="gpt-5.4",
                reasoning_effort=None,
                connected_systems=[],
                tool_capabilities=[],
                fallback_reason=None,
            )
        )

        self.assertEqual(events[0]["type"], "step")
        self.assertEqual(events[0]["label"], "Waiting for your laptop")
        self.assertEqual(events[0]["detail"], "Mansur's MacBook Air")
        self.assertEqual(events[-1]["type"], "final")
        self.assertIntervention(
            events[-1]["payload"],
            "run_handoff",
            title="Durable run in progress",
            detail_contains="waiting for your laptop to become available",
        )

    @patch("operator_chat_under_test.direct_chat_run_snapshot")
    def test_handoff_stream_emits_snapshot_waiting_for_confirmation_step(
        self,
        snapshot_mock,
    ):
        snapshot_mock.return_value = (
            None,
            {
                "run_id": "run-handoff-approval-1",
                "status": "waiting_for_input",
                "execution_target_selected": "local_companion",
                "pending_confirmation": {
                    "approval_id": "approval-1",
                    "prompt": "Confirm local execution before continuing.",
                },
                "usage_masked": {},
                "fallback_used": False,
            },
        )

        events = list(
            operator_chat._stream_direct_chat_run_handoff(
                started_run={"run_id": "run-handoff-approval-1"},
                requested_workspace_id="default",
                requested_provider="openai",
                requested_model="gpt-5.4",
                reasoning_effort=None,
                connected_systems=[],
                tool_capabilities=[],
                fallback_reason=None,
            )
        )

        self.assertEqual(events[0]["type"], "step")
        self.assertEqual(events[0]["label"], "Waiting for confirmation")
        self.assertEqual(events[0]["detail"], "Confirm local execution before continuing.")
        self.assertEqual(events[-1]["type"], "final")
        self.assertIntervention(
            events[-1]["payload"],
            "run_handoff",
            title="Durable run is waiting for confirmation",
            detail_contains="Confirm local execution before continuing.",
        )


if __name__ == "__main__":
    unittest.main()
