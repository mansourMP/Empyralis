import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT_DIR / "server_modules" / "direct_chat_runtime_exports.py"
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
spec = importlib.util.spec_from_file_location("operator_chat_no_provider_under_test", MODULE_PATH)
operator_chat = importlib.util.module_from_spec(spec)
sys.modules["operator_chat_no_provider_under_test"] = operator_chat
assert spec and spec.loader
spec.loader.exec_module(operator_chat)


class OperatorChatNoProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._semantic_model_patcher = patch.object(operator_chat.memory_service._workspace_memory_store, "_SEMANTIC_MODEL", False)
        self._semantic_model_patcher.start()
        self._live_runs_patcher = patch(
            "server_modules.direct_chat_operator_support_service.run_state_repository.sync_list_live_runs",
            return_value=[],
        )
        self._live_runs_patcher.start()
        self._workspace_context_patcher = patch(
            "operator_chat_no_provider_under_test._direct_chat_workspace_context_text",
            return_value="",
        )
        self._workspace_context_patcher.start()

    def tearDown(self) -> None:
        self._workspace_context_patcher.stop()
        self._live_runs_patcher.stop()
        self._semantic_model_patcher.stop()

    @patch("operator_chat_no_provider_under_test._preferred_provider", return_value=("openai", {}))
    @patch("operator_chat_no_provider_under_test._supports_direct_message_native_chat", return_value=False)
    @patch("operator_chat_no_provider_under_test._message_has_obvious_direct_tool_intent", return_value=True)
    @patch("operator_chat_no_provider_under_test.resolve_workspace_tool_capabilities", return_value=[])
    def test_file_tool_runs_without_provider_configured(
        self,
        _capabilities,
        _direct_tool_intent,
        _supports_direct_message_native_chat,
        _preferred_provider,
    ):
        with tempfile.TemporaryDirectory(prefix="no-provider-files-") as tmpdir:
            source_dir = Path(tmpdir) / "server_modules"
            source_dir.mkdir(parents=True, exist_ok=True)
            (source_dir / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
            (source_dir / "b.py").write_text("async def b():\n    return 2\n", encoding="utf-8")
            output_path = Path(tmpdir) / "fn_count.txt"

            payload = operator_chat.collect_direct_operator_reply(
                message=(
                    f"Read all .py files in {source_dir} and count functions, "
                    f"write a summary to {output_path}"
                ),
                workspace_id="default",
                requested_model="gpt-5.4",
                requested_provider="openai",
                availability={
                    "ai_ready": False,
                    "runtime_ok": True,
                    "local_gateway_online": True,
                    "connection_mode": "local_companion",
                },
            )

            self.assertEqual(payload["mode"], "answer")
            self.assertTrue(output_path.exists())
            self.assertIn("Counted 2 functions", payload["reply"])
            self.assertIn("Functions found: 2", output_path.read_text(encoding="utf-8"))

    @patch("operator_chat_no_provider_under_test._preferred_provider", return_value=("openai", {}))
    @patch("operator_chat_no_provider_under_test._supports_direct_message_native_chat", return_value=False)
    @patch("operator_chat_no_provider_under_test.resolve_workspace_tool_capabilities", return_value=[])
    def test_browser_request_reuses_session_browser_when_provided(
        self,
        _capabilities,
        _supports_direct_message_native_chat,
        _preferred_provider,
    ):
        class _DummyBrowser:
            __empyralis_browser_adapter__ = True

            def __init__(self) -> None:
                self.calls = []

            def run_sync(self, method_name, *args):
                self.calls.append((method_name, args))
                if method_name == "navigate":
                    return {"url": "https://example.com", "title": "Example Domain", "status_code": 200}
                if method_name == "observe":
                    return {
                        "url": "https://example.com",
                        "title": "Example Domain",
                        "text_preview": "Example Domain",
                        "interactive_elements": [],
                    }
                if method_name == "extract_text":
                    return "Example Domain"
                raise AssertionError(f"Unexpected browser method: {method_name}")

        runtime_handle = type("RuntimeHandle", (), {"browser": _DummyBrowser()})()

        with patch("server_modules.browser_engine.BrowserEngine", side_effect=AssertionError("global browser should not be constructed")):
            payload = operator_chat.collect_direct_operator_reply(
                message="Go to https://example.com and tell me the page title and main heading",
                workspace_id="default",
                requested_model="gpt-5.4",
                requested_provider="openai",
                thread_id="thread-1",
                availability={"ai_ready": False},
                session_ctx={
                    "runtime_handle": runtime_handle,
                    "workspace_id": "default",
                    "thread_id": "thread-1",
                },
            )

        self.assertEqual(payload["mode"], "answer")
        self.assertIn("Page title: Example Domain", payload["reply"])
        self.assertIn("Main heading: Example Domain", payload["reply"])
        self.assertEqual(
            runtime_handle.browser.calls,
            [
                ("navigate", ("https://example.com",)),
                ("observe", ()),
                ("extract_text", ("h1",)),
            ],
        )

    @patch("operator_chat_no_provider_under_test._preferred_provider", return_value=("openai", {}))
    @patch("operator_chat_no_provider_under_test._supports_direct_message_native_chat", return_value=False)
    @patch("operator_chat_no_provider_under_test.resolve_workspace_tool_capabilities", return_value=[])
    @patch(
        "operator_chat_no_provider_under_test._execute_single_direct_tool_call",
        side_effect=[
            '{"url":"https://example.com","title":"Example Domain","status_code":200}',
            '{"url":"https://example.com","title":"Example Domain","text_preview":"Example Domain","interactive_elements":[]}',
            "Example Domain",
        ],
    )
    def test_browser_request_runs_without_provider_configured(
        self,
        _execute_single_direct_tool_call,
        _capabilities,
        _supports_direct_message_native_chat,
        _preferred_provider,
    ):
        payload = operator_chat.collect_direct_operator_reply(
            message="Go to https://example.com and tell me the page title and main heading",
            workspace_id="default",
            requested_model="gpt-5.4",
            requested_provider="openai",
            availability={"ai_ready": False},
        )

        self.assertEqual(payload["mode"], "answer")
        self.assertIn("Page title: Example Domain", payload["reply"])
        self.assertIn("Main heading: Example Domain", payload["reply"])

    @patch("operator_chat_no_provider_under_test._preferred_provider", return_value=("openai", {}))
    @patch("operator_chat_no_provider_under_test._supports_direct_message_native_chat", return_value=False)
    @patch("operator_chat_no_provider_under_test.resolve_workspace_tool_capabilities", return_value=[])
    def test_single_file_function_count_runs_without_provider_configured(
        self,
        _capabilities,
        _supports_direct_message_native_chat,
        _preferred_provider,
    ):
        with tempfile.TemporaryDirectory(prefix="no-provider-single-file-") as tmpdir:
            root = Path(tmpdir)
            source_dir = root / "server_modules"
            source_dir.mkdir(parents=True, exist_ok=True)
            source_path = source_dir / "shared.py"
            source_path.write_text(
                "def alpha():\n    return 1\n\nasync def beta():\n    return 2\n\nclass Example:\n    pass\n",
                encoding="utf-8",
            )
            original_cwd = Path.cwd()
            os.chdir(root)
            try:
                payload = operator_chat.collect_direct_operator_reply(
                    message="Read server_modules/shared.py and count functions",
                    workspace_id="default",
                    requested_model="gpt-5.4",
                    requested_provider="openai",
                    availability={"ai_ready": False},
                )
            finally:
                os.chdir(original_cwd)

        self.assertEqual(payload["mode"], "answer")
        self.assertIn("server_modules/shared.py", payload["reply"])
        self.assertIn("2 functions", payload["reply"])

    @patch("operator_chat_no_provider_under_test._preferred_provider", return_value=("openai", {}))
    @patch("operator_chat_no_provider_under_test._supports_direct_message_native_chat", return_value=False)
    @patch("operator_chat_no_provider_under_test.resolve_workspace_tool_capabilities", return_value=[])
    @patch(
        "operator_chat_no_provider_under_test._execute_single_direct_tool_call",
        return_value="1. Top headline\nURL: https://example.com\nSnippet: Example AI headline",
    )
    def test_web_search_runs_without_provider_configured(
        self,
        _execute_single_direct_tool_call,
        _capabilities,
        _supports_direct_message_native_chat,
        _preferred_provider,
    ):
        payload = operator_chat.collect_direct_operator_reply(
            message="Search for today's top AI news headline",
            workspace_id="default",
            requested_model="gpt-5.4",
            requested_provider="openai",
            availability={"ai_ready": False},
        )

        self.assertEqual(payload["mode"], "answer")
        self.assertIn("Top headline", payload["reply"])

    @patch("operator_chat_no_provider_under_test._preferred_provider", return_value=("openai", {}))
    @patch("operator_chat_no_provider_under_test._supports_direct_message_native_chat", return_value=False)
    @patch("operator_chat_no_provider_under_test.resolve_workspace_tool_capabilities", return_value=[])
    @patch(
        "operator_chat_no_provider_under_test._execute_single_direct_tool_call",
        return_value="Command completed: echo hello world\nhello world",
    )
    def test_shell_command_runs_without_provider_configured(
        self,
        _execute_single_direct_tool_call,
        _capabilities,
        _supports_direct_message_native_chat,
        _preferred_provider,
    ):
        payload = operator_chat.collect_direct_operator_reply(
            message="Run: echo hello world",
            workspace_id="default",
            requested_model="gpt-5.4",
            requested_provider="openai",
            availability={
                "ai_ready": False,
                "runtime_ok": True,
                "local_gateway_online": True,
                "connection_mode": "local_companion",
            },
        )

        self.assertEqual(payload["mode"], "answer")
        self.assertIn("hello world", payload["reply"])

    @patch("operator_chat_no_provider_under_test._preferred_provider", return_value=("openai", {}))
    @patch("operator_chat_no_provider_under_test._supports_direct_message_native_chat", return_value=False)
    @patch("operator_chat_no_provider_under_test.resolve_workspace_tool_capabilities", return_value=[])
    @patch("operator_chat_no_provider_under_test.memory_service.save_memory")
    def test_memory_write_runs_without_provider_configured(
        self,
        save_memory_mock,
        _capabilities,
        _supports_direct_message_native_chat,
        _preferred_provider,
    ):
        payload = operator_chat.collect_direct_operator_reply(
            message="Remember my name is TestUser",
            workspace_id="default",
            requested_model="gpt-5.4",
            requested_provider="openai",
            availability={"ai_ready": False},
        )

        self.assertEqual(payload["mode"], "answer")
        self.assertIn("Stored memory", payload["reply"])
        save_memory_mock.assert_called_once_with("default", "name", "TestUser")

    @patch("operator_chat_no_provider_under_test._preferred_provider", return_value=("openai", {}))
    @patch("operator_chat_no_provider_under_test._supports_direct_message_native_chat", return_value=False)
    @patch("operator_chat_no_provider_under_test.resolve_workspace_tool_capabilities", return_value=[])
    @patch(
        "operator_chat_no_provider_under_test.memory_service.find_workspace_memory_entry",
        return_value={"key": "name", "content": "TestUser"},
    )
    def test_memory_read_runs_without_provider_configured(
        self,
        _find_workspace_memory_entry,
        _capabilities,
        _supports_direct_message_native_chat,
        _preferred_provider,
    ):
        payload = operator_chat.collect_direct_operator_reply(
            message="What is my name",
            workspace_id="default",
            requested_model="gpt-5.4",
            requested_provider="openai",
            availability={"ai_ready": False},
        )

        self.assertEqual(payload["mode"], "answer")
        self.assertEqual(payload["reply"], "name = TestUser")

    @patch("operator_chat_no_provider_under_test._preferred_provider", return_value=("openai", {}))
    @patch("operator_chat_no_provider_under_test._supports_direct_message_native_chat", return_value=False)
    @patch("operator_chat_no_provider_under_test.resolve_workspace_tool_capabilities", return_value=[])
    @patch(
        "operator_chat_no_provider_under_test._execute_single_direct_tool_call",
        return_value='HTTP 200\n\n{"origin":"203.0.113.9"}',
    )
    def test_web_fetch_runs_without_provider_configured(
        self,
        _execute_single_direct_tool_call,
        _capabilities,
        _supports_direct_message_native_chat,
        _preferred_provider,
    ):
        payload = operator_chat.collect_direct_operator_reply(
            message="Fetch https://httpbin.org/get and tell me the origin IP",
            workspace_id="default",
            requested_model="gpt-5.4",
            requested_provider="openai",
            availability={"ai_ready": False},
        )

        self.assertEqual(payload["mode"], "answer")
        self.assertEqual(payload["reply"], "Origin IP: 203.0.113.9")

    @patch("operator_chat_no_provider_under_test._preferred_provider", return_value=("openai", {}))
    @patch("operator_chat_no_provider_under_test._supports_direct_message_native_chat", return_value=False)
    @patch("operator_chat_no_provider_under_test.resolve_workspace_tool_capabilities", return_value=[])
    def test_directory_listing_skips_workspace_context_for_no_provider_tool_path(
        self,
        _capabilities,
        _supports_direct_message_native_chat,
        _preferred_provider,
    ):
        self._workspace_context_patcher.stop()
        with patch(
            "operator_chat_no_provider_under_test._direct_chat_workspace_context_text",
            return_value="",
        ) as workspace_context_mock:
            with tempfile.TemporaryDirectory(prefix="no-provider-list-fast-") as tmpdir:
                root = Path(tmpdir)
                (root / "alpha.txt").write_text("a", encoding="utf-8")
                (root / "beta.txt").write_text("b", encoding="utf-8")

                payload = operator_chat.collect_direct_operator_reply(
                    message=f"List the first 2 files in {root}",
                    workspace_id="default",
                    requested_model="gpt-5.4",
                    requested_provider="openai",
                    availability={
                        "ai_ready": False,
                        "runtime_ok": True,
                        "local_gateway_online": True,
                        "connection_mode": "local_companion",
                    },
                )

        self._workspace_context_patcher.start()
        self.assertEqual(payload["mode"], "answer")
        self.assertIn("First 2 files in ", payload["reply"])
        workspace_context_mock.assert_not_called()

    @patch("operator_chat_no_provider_under_test._preferred_provider", return_value=("openai", {}))
    @patch("operator_chat_no_provider_under_test._supports_direct_message_native_chat", return_value=False)
    @patch("operator_chat_no_provider_under_test.resolve_workspace_tool_capabilities", return_value=[])
    @patch(
        "operator_chat_no_provider_under_test._execute_single_direct_tool_call",
        return_value='HTTP 200\n\n{"utc_datetime":"2026-04-02T00:00:00+00:00"}',
    )
    def test_combined_shell_and_web_request_runs_without_provider_configured(
        self,
        _execute_single_direct_tool_call,
        _capabilities,
        _supports_direct_message_native_chat,
        _preferred_provider,
    ):
        with tempfile.TemporaryDirectory(prefix="no-provider-list-") as tmpdir:
            root = Path(tmpdir)
            (root / "alpha.txt").write_text("a", encoding="utf-8")
            (root / "beta.txt").write_text("b", encoding="utf-8")

            payload = operator_chat.collect_direct_operator_reply(
                message=f"List files in {root} and get current time from worldtimeapi.io/api/timezone/UTC",
                workspace_id="default",
                requested_model="gpt-5.4",
                requested_provider="openai",
                availability={
                    "ai_ready": False,
                    "runtime_ok": True,
                    "local_gateway_online": True,
                    "connection_mode": "local_companion",
                },
            )

        self.assertEqual(payload["mode"], "answer")
        self.assertIn("Files in ", payload["reply"])
        self.assertIn("alpha.txt\nbeta.txt", payload["reply"])
        self.assertIn("Current UTC time: 2026-04-02T00:00:00+00:00", payload["reply"])

    @patch("operator_chat_no_provider_under_test._preferred_provider", return_value=("openai", {}))
    @patch("operator_chat_no_provider_under_test._supports_direct_message_native_chat", return_value=False)
    @patch("operator_chat_no_provider_under_test.resolve_workspace_tool_capabilities", return_value=[])
    def test_directory_listing_with_first_n_runs_without_provider(
        self,
        _capabilities,
        _supports_direct_message_native_chat,
        _preferred_provider,
    ):
        with tempfile.TemporaryDirectory(prefix="no-provider-list-limit-") as tmpdir:
            root = Path(tmpdir)
            (root / "alpha.txt").write_text("a", encoding="utf-8")
            (root / "beta.txt").write_text("b", encoding="utf-8")
            (root / "gamma.txt").write_text("c", encoding="utf-8")

            payload = operator_chat.collect_direct_operator_reply(
                message=f"List the first 2 files in {root}",
                workspace_id="default",
                requested_model="gpt-5.4",
                requested_provider="openai",
                availability={
                    "ai_ready": False,
                    "runtime_ok": True,
                    "local_gateway_online": True,
                    "connection_mode": "local_companion",
                },
            )

        self.assertEqual(payload["mode"], "answer")
        self.assertIn("First 2 files in ", payload["reply"])
        self.assertIn("alpha.txt\nbeta.txt", payload["reply"])
        self.assertNotIn("gamma.txt", payload["reply"])

    @patch("operator_chat_no_provider_under_test._preferred_provider", return_value=("openai", {}))
    @patch("operator_chat_no_provider_under_test._supports_direct_message_native_chat", return_value=False)
    @patch("operator_chat_no_provider_under_test.resolve_workspace_tool_capabilities", return_value=[])
    def test_llm_request_without_provider_returns_clear_error_not_gate(
        self,
        _capabilities,
        _supports_direct_message_native_chat,
        _preferred_provider,
    ):
        payload = operator_chat.collect_direct_operator_reply(
            message="Explain the tradeoffs between SQLite and Postgres for this app.",
            workspace_id="default",
            requested_model="gpt-5.4",
            requested_provider="openai",
            availability={"ai_ready": False},
        )

        self.assertEqual(payload["mode"], "connect")
        self.assertEqual(payload["actions"], [{"label": "Connect", "href": "/connect-ai"}])
        self.assertEqual(payload["reply"], "")
        interventions = payload.get("interventions") or []
        self.assertTrue(interventions)
        self.assertEqual(interventions[0]["kind"], "connect_required")
        self.assertEqual(interventions[0]["title"], "OpenAI is not available")
        self.assertIn("OpenAI", str(interventions[0].get("detail") or ""))
