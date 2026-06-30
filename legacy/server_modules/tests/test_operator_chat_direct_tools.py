import json
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


ROOT_DIR = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT_DIR / "server_modules" / "direct_chat_runtime_exports.py"
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
spec = importlib.util.spec_from_file_location("operator_chat_direct_tools_under_test", MODULE_PATH)
operator_chat = importlib.util.module_from_spec(spec)
sys.modules["operator_chat_direct_tools_under_test"] = operator_chat
assert spec and spec.loader
spec.loader.exec_module(operator_chat)

_PUBLIC_ALIASES = {
    "direct_chat_workspace_context_text": "_direct_chat_workspace_context_text",
    "preferred_provider": "_preferred_provider",
    "supports_direct_message_native_chat": "_supports_direct_message_native_chat",
    "start_direct_chat_run_handoff": "_start_direct_chat_run_handoff",
    "execute_single_direct_tool_call": "_execute_single_direct_tool_call",
}
for public_name, private_name in _PUBLIC_ALIASES.items():
    if not hasattr(operator_chat, public_name) and hasattr(operator_chat, private_name):
        setattr(operator_chat, public_name, getattr(operator_chat, private_name))


class OperatorChatDirectToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self._semantic_model_patcher = patch.object(operator_chat.memory_service._workspace_memory_store, "_SEMANTIC_MODEL", False)
        self._semantic_model_patcher.start()
        self._workspace_context_patcher = patch(
            "operator_chat_direct_tools_under_test.direct_chat_workspace_context_text",
            return_value="",
        )
        self._workspace_context_patcher.start()

    def tearDown(self) -> None:
        self._workspace_context_patcher.stop()
        self._semantic_model_patcher.stop()

    def test_memory_tools_are_exposed_in_direct_chat_prompt(self):
        captured = {}

        def _fake_stream(**kwargs):
            captured["system_prompt"] = kwargs.get("system_prompt")
            captured["tools"] = kwargs.get("metadata", {}).get("tools")
            return iter(
                [
                    {
                        "type": "result",
                        "reply": "Checked memory.",
                        "usage_masked": {"provider": "codex_cli", "model": "gpt-5.4"},
                        "provider": "codex_cli",
                        "model": "gpt-5.4",
                        "attempted_providers": "codex_cli",
                        "error": "",
                    }
                ]
            )

        with patch(
            "operator_chat_direct_tools_under_test.resolve_workspace_tool_capabilities",
            return_value=[],
        ):
            with patch(
                "operator_chat_direct_tools_under_test.preferred_provider",
                return_value=("codex_cli", {}),
            ):
                with patch(
                    "operator_chat_direct_tools_under_test.supports_direct_message_native_chat",
                    return_value=True,
                ):
                    with patch(
                        "operator_chat_direct_tools_under_test.generate_chat_reply_stream_with_provider_fallback",
                        side_effect=_fake_stream,
                    ):
                        with patch(
                            "server_modules.direct_chat_runtime_facade_service.message_has_obvious_direct_tool_intent",
                            return_value=True,
                        ):
                            payload = operator_chat.collect_direct_operator_reply(
                                message="Summarize my memory preferences.",
                                workspace_id="default",
                                requested_model="gpt-5.4",
                                requested_provider="codex_cli",
                                availability={"ai_ready": True},
                            )

        self.assertEqual(payload["reply"], "Checked memory.")
        tool_names = {item["name"] for item in captured["tools"]}
        self.assertIn("memory_search", tool_names)
        self.assertIn("memory_get", tool_names)
        self.assertIn("sage_service__list_state", tool_names)
        self.assertIn("sage_service__update_profile", tool_names)
        self.assertIn("sage_service__create_entry", tool_names)
        self.assertIn("## Memory Recall", str(captured["system_prompt"] or ""))
        self.assertIn("run memory_search", str(captured["system_prompt"] or ""))

    def test_memory_direct_tools_execute_notebook_lookup(self):
        with patch(
            "operator_chat_direct_tools_under_test.search_memory_notebook",
            return_value=[
                {
                    "path": "MEMORY.md",
                    "start_line": 2,
                    "end_line": 3,
                    "score": 11,
                    "snippet": "- timezone: Asia/Shanghai",
                }
            ],
        ):
            raw = operator_chat._execute_single_direct_tool_call(
                tool_call={"name": "memory_search", "arguments": {"query": "timezone"}},
                workspace_id="default",
                thread_id="thread-1",
            )
        parsed = json.loads(raw)
        self.assertEqual(parsed["results"][0]["path"], "MEMORY.md")

        with patch(
            "operator_chat_direct_tools_under_test.get_memory_notebook_excerpt",
            return_value={
                "path": "MEMORY.md",
                "from_line": 2,
                "to_line": 3,
                "text": "- timezone: Asia/Shanghai",
                "total_lines": 4,
            },
        ):
            raw = operator_chat._execute_single_direct_tool_call(
                tool_call={"name": "memory_get", "arguments": {"path": "MEMORY.md", "from": 2, "lines": 2}},
                workspace_id="default",
                thread_id="thread-1",
            )
        parsed = json.loads(raw)
        self.assertEqual(parsed["path"], "MEMORY.md")
        self.assertEqual(parsed["from_line"], 2)
        self.assertIn("Asia/Shanghai", parsed["text"])

    def test_sage_service_direct_tools_execute_inside_operator_chat_runtime(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir) / "workspace-1"
            with (
                patch(
                    "server_modules.sage_services_service.workspace_context.workspace_scope_dir",
                    return_value=root,
                ),
                patch(
                    "server_modules.sage_services_service.personal_context_engine.publish_event",
                    new=AsyncMock(return_value={"id": "evt-1"}),
                ),
            ):
                created_raw = operator_chat._execute_single_direct_tool_call(
                    tool_call={
                        "name": "sage_service__create_entry",
                        "arguments": {
                            "service_id": "flashcards",
                            "entry": {
                                "deck": "Legal",
                                "front": "SPA",
                                "back": "Share Purchase Agreement",
                            },
                            "explicit_user_intent": True,
                        },
                    },
                    workspace_id="workspace-1",
                    thread_id="thread-1",
                    session_ctx={"tenant_id": "tenant-1"},
                )
                listed_raw = operator_chat._execute_single_direct_tool_call(
                    tool_call={
                        "name": "sage_service__list_state",
                        "arguments": {"service_id": "flashcards"},
                    },
                    workspace_id="workspace-1",
                    thread_id="thread-1",
                    session_ctx={"tenant_id": "tenant-1"},
                )

        created = json.loads(created_raw)
        listed = json.loads(listed_raw)
        self.assertEqual(created["entries"][0]["front"], "SPA")
        self.assertEqual(listed["entries"][0]["deck"], "Legal")

    @patch(
        "operator_chat_direct_tools_under_test.resolve_workspace_tool_capabilities",
        return_value=[
            {
                "id": "telegram_bot",
                "label": "Telegram Bot LIVE",
                "connected": True,
                "authenticated": True,
                "runtime_usable": True,
                "read_actions": ["chats.read"],
                "write_actions": ["send_message"],
                "approval_required_actions": [],
            }
        ],
    )
    @patch("operator_chat_direct_tools_under_test.preferred_provider", return_value=("codex_cli", {}))
    @patch("operator_chat_direct_tools_under_test.supports_direct_message_native_chat", return_value=True)
    @patch("operator_chat_direct_tools_under_test.generate_chat_reply_stream_with_provider_fallback")
    @patch("server_modules.runs_execution._workflow_execute_connector_action")
    def test_direct_chat_executes_tool_call_result(
        self,
        execute_tool_mock,
        stream_mock,
        _supports_direct_message_native_chat,
        _preferred_provider,
        _resolve_workspace_tool_capabilities,
    ):
        stream_mock.return_value = iter(
            []
        )
        stream_mock.side_effect = [
            iter(
                [
                    {
                        "type": "result",
                        "reply": "",
                        "usage_masked": {"provider": "codex_cli", "model": "gpt-5.4"},
                        "provider": "codex_cli",
                        "model": "gpt-5.4",
                        "attempted_providers": "codex_cli",
                        "error": "",
                        "tool_calls": [
                            {
                                "name": "telegram_bot__send_message",
                                "arguments": "{\"input\":\"hello from direct chat\"}",
                            }
                        ],
                    }
                ]
            ),
            iter(
                [
                    {
                        "type": "result",
                        "reply": "The Telegram message was sent.",
                        "usage_masked": {"provider": "codex_cli", "model": "gpt-5.4"},
                        "provider": "codex_cli",
                        "model": "gpt-5.4",
                        "attempted_providers": "codex_cli",
                        "error": "",
                    }
                ]
            ),
        ]
        execute_tool_mock.return_value = {
            "summary": "Connector action completed: telegram_bot.send_message.",
            "result_data": {
                "connector_action": {
                    "connector": "telegram_bot",
                    "action_id": "send_message",
                    "chat_id": "default",
                }
            },
        }

        payload = operator_chat.collect_direct_operator_reply(
            message="Send a Telegram message saying hello from direct chat.",
            workspace_id="default",
            requested_model="gpt-5.4",
            requested_provider="codex_cli",
            availability={"ai_ready": True},
        )

        self.assertEqual(payload["mode"], "answer")
        self.assertEqual(payload["actions"], [])
        self.assertEqual(payload["reply"], "The Telegram message was sent.")
        execute_tool_mock.assert_called_once()

    @patch(
        "operator_chat_direct_tools_under_test.resolve_workspace_tool_capabilities",
        return_value=[
            {
                "id": "telegram_bot",
                "label": "Telegram Bot LIVE",
                "connected": True,
                "authenticated": True,
                "runtime_usable": True,
                "read_actions": ["chats.read"],
                "write_actions": ["send_message"],
                "approval_required_actions": ["send_message"],
            }
        ],
    )
    @patch("operator_chat_direct_tools_under_test.preferred_provider", return_value=("codex_cli", {}))
    @patch("operator_chat_direct_tools_under_test.supports_direct_message_native_chat", return_value=True)
    @patch("operator_chat_direct_tools_under_test.generate_chat_reply_stream_with_provider_fallback")
    @patch("server_modules.direct_chat_runtime_facade_service.message_has_obvious_direct_tool_intent", return_value=True)
    def test_direct_chat_passes_tool_schemas_to_worker(
        self,
        _obvious_tool_intent,
        stream_mock,
        _supports_direct_message_native_chat,
        _preferred_provider,
        _resolve_workspace_tool_capabilities,
    ):
        captured = {}

        def _fake_stream(**kwargs):
            captured["tools"] = kwargs["metadata"].get("tools")
            return iter(
                [
                    {
                        "type": "result",
                        "reply": "Handled without tools.",
                        "usage_masked": {},
                        "provider": "codex_cli",
                        "model": "gpt-5.4",
                        "attempted_providers": "codex_cli",
                        "error": "",
                    }
                ]
            )

        stream_mock.side_effect = _fake_stream

        payload = operator_chat.collect_direct_operator_reply(
            message="Send a Telegram message saying hi.",
            workspace_id="default",
            requested_model="gpt-5.4",
            requested_provider="codex_cli",
            availability={"ai_ready": True},
        )

        self.assertEqual(payload["reply"], "Handled without tools.")
        self.assertIsInstance(captured.get("tools"), list)
        tool_names = {str(item.get("name") or "").strip() for item in captured["tools"]}
        self.assertIn("telegram_bot__send_message", tool_names)

    @patch(
        "operator_chat_direct_tools_under_test.resolve_workspace_tool_capabilities",
        return_value=[
            {
                "id": "telegram_bot",
                "label": "Telegram Bot LIVE",
                "connected": True,
                "authenticated": True,
                "runtime_usable": True,
                "read_actions": ["chats.read"],
                "write_actions": ["send_message"],
                "approval_required_actions": ["send_message"],
            }
        ],
    )
    @patch("operator_chat_direct_tools_under_test.preferred_provider", return_value=("codex_cli", {}))
    @patch("operator_chat_direct_tools_under_test.supports_direct_message_native_chat", return_value=True)
    @patch("operator_chat_direct_tools_under_test.generate_chat_reply_stream_with_provider_fallback")
    @patch("server_modules.runs_execution._workflow_execute_connector_action")
    def test_direct_chat_requires_approval_before_executing_tool_call(
        self,
        execute_tool_mock,
        stream_mock,
        _supports_direct_message_native_chat,
        _preferred_provider,
        _resolve_workspace_tool_capabilities,
    ):
        stream_mock.return_value = iter(
            [
                {
                    "type": "result",
                    "reply": "",
                    "usage_masked": {"provider": "codex_cli", "model": "gpt-5.4"},
                    "provider": "codex_cli",
                    "model": "gpt-5.4",
                    "attempted_providers": "codex_cli",
                    "error": "",
                    "tool_calls": [
                        {
                            "name": "telegram_bot__send_message",
                            "arguments": "{\"input\":\"hello from direct chat\"}",
                        }
                    ],
                }
            ]
        )

        payload = operator_chat.collect_direct_operator_reply(
            message="Send a Telegram message saying hello from direct chat.",
            workspace_id="default",
            requested_model="gpt-5.4",
            requested_provider="codex_cli",
            availability={"ai_ready": True},
        )

        self.assertEqual(payload["mode"], "answer_with_action")
        self.assertEqual(payload["reply"], "")
        self.assertEqual(payload["approvals"][0]["prompt"], "Approve Telegram Bot to send message before continuing.")
        self.assertEqual(payload["actions"][0]["type"], "approval_required")
        self.assertEqual(payload["actions"][0]["kind"], "approval_required")
        self.assertEqual(payload["actions"][0]["connector"], "telegram_bot")
        self.assertEqual(payload["actions"][0]["action"], "send_message")
        self.assertEqual(payload["actions"][0]["input"], "hello from direct chat")
        execute_tool_mock.assert_not_called()

    @patch(
        "operator_chat_direct_tools_under_test.resolve_workspace_tool_capabilities",
        return_value=[
            {
                "id": "telegram_bot",
                "label": "Telegram Bot LIVE",
                "connected": True,
                "authenticated": True,
                "runtime_usable": True,
                "read_actions": ["chats.read"],
                "write_actions": ["send_message"],
                "approval_required_actions": ["send_message"],
            }
        ],
    )
    @patch("server_modules.runs_execution._workflow_execute_connector_action")
    def test_direct_chat_executes_approved_action_confirmation(
        self,
        execute_tool_mock,
        _resolve_workspace_tool_capabilities,
    ):
        execute_tool_mock.return_value = {
            "summary": "Connector action completed: telegram_bot.send_message.",
            "result_data": {
                "connector_action": {
                    "connector": "telegram_bot",
                    "action_id": "send_message",
                    "chat_id": "default",
                }
            },
        }

        payload = operator_chat.collect_direct_operator_reply(
            message="__approval_confirmed__",
            workspace_id="default",
            requested_model="gpt-5.4",
            requested_provider="openai",
            availability={"ai_ready": True},
            approved_action={
                "connector": "telegram_bot",
                "action": "send_message",
                "input": "hello from direct chat",
            },
        )

        self.assertEqual(payload["mode"], "answer")
        self.assertIn("telegram_bot.send_message", payload["reply"])
        execute_tool_mock.assert_called_once()

    @patch("operator_chat_direct_tools_under_test.start_direct_chat_run_handoff")
    @patch("operator_chat_direct_tools_under_test.resolve_workspace_tool_capabilities", return_value=[])
    @patch("operator_chat_direct_tools_under_test.preferred_provider", return_value=("codex_cli", {}))
    @patch("operator_chat_direct_tools_under_test.supports_direct_message_native_chat", return_value=True)
    @patch("operator_chat_direct_tools_under_test.generate_chat_reply_stream_with_provider_fallback")
    @patch("operator_chat_direct_tools_under_test.execute_single_direct_tool_call")
    def test_simple_local_file_request_stays_on_ai_first_chat_path(
        self,
        execute_direct_tool_mock,
        stream_mock,
        _supports_direct_message_native_chat,
        _preferred_provider,
        _resolve_workspace_tool_capabilities,
        start_run_mock,
    ):
        execute_direct_tool_mock.return_value = "Example file contents"
        start_run_mock.return_value = {
            "run_id": "run-local-file-1",
            "status": "queued_local",
            "route": {"selected": "local_companion"},
        }
        stream_mock.return_value = iter(
            [
                {
                    "type": "result",
                    "reply": "I can help inspect that file if an enabled file tool is available.",
                    "usage_masked": {"provider": "codex_cli", "model": "gpt-5.4"},
                    "provider": "codex_cli",
                    "model": "gpt-5.4",
                    "attempted_providers": "codex_cli",
                    "error": "",
                    "tool_calls": [],
                }
            ]
        )

        payload = operator_chat.collect_direct_operator_reply(
            message="Open file /tmp/README.md",
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

        self.assertEqual(payload["mode"], "answer")
        self.assertIn("enabled file tool", payload["reply"])
        stream_mock.assert_called_once()
        execute_direct_tool_mock.assert_not_called()
        start_run_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
