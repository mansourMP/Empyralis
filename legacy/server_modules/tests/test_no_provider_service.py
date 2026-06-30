import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from server_modules import no_provider_service


def _compact_text(value):
    import re

    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def _extract_first_path_reference(value: str) -> str:
    import re

    match = re.search(r"([A-Za-z0-9_./-]+\.[A-Za-z0-9_./-]+)", str(value or ""))
    return str(match.group(1) or "").strip() if match else ""


def _extract_first_url(value: str) -> str:
    import re

    match = re.search(r"https?://[^\s)]+", str(value or ""))
    return str(match.group(0) or "").strip() if match else ""


def _safe_positive_int(value, default: int) -> int:
    try:
        parsed = int(str(value or "").strip())
    except Exception:
        return default
    return parsed if parsed > 0 else default


class NoProviderServiceTests(unittest.TestCase):
    def test_extract_shell_command_supports_short_run_prefix(self) -> None:
        command = no_provider_service.extract_shell_command(
            "Run: echo hello world",
            compact_text=_compact_text,
            extract_first_url=_extract_first_url,
        )
        self.assertEqual(command, "echo hello world")

    def test_extract_web_query_supports_search_prompt(self) -> None:
        query = no_provider_service.extract_web_query("Search for today's top AI news headline")
        self.assertEqual(query, "today's top AI news headline")

    def test_parse_http_tool_output_returns_json_payload(self) -> None:
        parsed = no_provider_service.parse_http_tool_output('HTTP 200\n\n{"origin":"203.0.113.9"}')
        self.assertEqual(parsed, {"origin": "203.0.113.9"})

    def test_count_definitions_in_file_handles_function_and_class_counts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="no-provider-service-file-") as tmpdir:
            root = Path(tmpdir)
            source_path = root / "example.py"
            source_path.write_text(
                "def alpha():\n    return 1\n\nclass Example:\n    pass\n",
                encoding="utf-8",
            )

            reply = no_provider_service.count_definitions_in_file(
                f"Read {source_path} and count functions and classes",
                compact_text=_compact_text,
                extract_first_path_reference=_extract_first_path_reference,
                resolve_local_path=Path,
            )

        self.assertIn("1 functions", str(reply))
        self.assertIn("1 classes", str(reply))

    def test_count_functions_and_write_summary_writes_output_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="no-provider-service-summary-") as tmpdir:
            root = Path(tmpdir)
            source_dir = root / "src"
            source_dir.mkdir(parents=True, exist_ok=True)
            output_path = root / "summary.txt"
            (source_dir / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
            (source_dir / "b.py").write_text("async def b():\n    return 2\n", encoding="utf-8")

            with (
                patch(
                    "server_modules.rust_runtime_kernel_client.runtime_state_store_decision",
                    side_effect=lambda **kwargs: {
                        "ok": True,
                        "decision": "allow",
                        "next_action": kwargs.get("operation"),
                    },
                ),
                patch(
                    "server_modules.rust_runtime_kernel_client.enforce_kernel_decision",
                    side_effect=lambda _command, decision: decision,
                ),
            ):
                reply = no_provider_service.count_functions_and_write_summary(
                    f"Read all .py files in {source_dir} and count functions, write a summary to {output_path}",
                    compact_text=_compact_text,
                    resolve_local_path=Path,
                )

            written = output_path.read_text(encoding="utf-8")

        self.assertIn("Counted 2 functions", str(reply))
        self.assertIn("Functions found: 2", written)

    def test_list_directory_respects_first_n(self) -> None:
        with tempfile.TemporaryDirectory(prefix="no-provider-service-list-") as tmpdir:
            root = Path(tmpdir)
            (root / "alpha.txt").write_text("a", encoding="utf-8")
            (root / "beta.txt").write_text("b", encoding="utf-8")
            (root / "gamma.txt").write_text("c", encoding="utf-8")

            result = no_provider_service.list_directory(
                f"List the first 2 files in {root}",
                safe_positive_int=_safe_positive_int,
                resolve_local_path=Path,
            )

        self.assertEqual(result["limit"], 2)
        self.assertEqual(result["listing"], "alpha.txt\nbeta.txt")

    def test_looks_like_directory_listing_request_is_syntactic_only(self) -> None:
        self.assertTrue(
            no_provider_service.looks_like_directory_listing_request("List the first 2 files in /tmp/does-not-need-to-exist")
        )
        self.assertTrue(no_provider_service.looks_like_directory_listing_request("List the files on my desktop"))
        self.assertFalse(no_provider_service.looks_like_directory_listing_request("Explain the tradeoffs between SQLite and Postgres"))

    def test_no_provider_reasoning_required_response_is_stable(self) -> None:
        payload = no_provider_service.no_provider_reasoning_required_response()
        self.assertEqual(payload["mode"], "error")
        self.assertEqual(payload["error"], "no_provider")
        self.assertEqual(payload["reply"], "")
        self.assertEqual(payload["interventions"][0]["code"], "no_provider")

    def test_execute_no_provider_request_handles_memory_reply_before_tool_path(self) -> None:
        services = no_provider_service.NoProviderExecutionServices(
            compact_text=_compact_text,
            safe_positive_int=_safe_positive_int,
            resolve_local_path=Path,
            extract_first_path_reference=_extract_first_path_reference,
            extract_first_url=_extract_first_url,
            parse_page_state=lambda value: value,
            parse_memory_write=lambda message: None,
            parse_memory_read=lambda message: "name",
            handle_memory_request=lambda workspace_id, message: "name = TestUser",
            parse_tool_name=lambda tool_name: ("telegram_bot", "send_message"),
            tool_arguments_payload=lambda arguments: {},
            approval_required_for_tool=lambda connector_id, action_id, arguments, tool_capabilities: False,
            agent_machine_full_trust_for_session=lambda session_ctx: False,
            execute_single_tool_call=lambda **kwargs: "unused",
        )

        payload = no_provider_service.execute_no_provider_request(
            message="What is my name",
            workspace_id="default",
            thread_id="thread-1",
            tools=[],
            tool_capabilities=[],
            reasoning_effort="medium",
            services=services,
        )

        self.assertEqual(payload["reply"], "name = TestUser")
        self.assertEqual(payload["mode"], "answer")

    def test_execute_no_provider_request_handles_tool_execution_and_origin_ip_format(self) -> None:
        execute_single_tool_call = Mock(return_value='HTTP 200\n\n{"origin":"203.0.113.9"}')
        services = no_provider_service.NoProviderExecutionServices(
            compact_text=_compact_text,
            safe_positive_int=_safe_positive_int,
            resolve_local_path=Path,
            extract_first_path_reference=_extract_first_path_reference,
            extract_first_url=_extract_first_url,
            parse_page_state=lambda value: value,
            parse_memory_write=lambda message: None,
            parse_memory_read=lambda message: None,
            handle_memory_request=lambda workspace_id, message: None,
            parse_tool_name=lambda tool_name: ("http", "request"),
            tool_arguments_payload=lambda arguments: {"url": "https://httpbin.org/get"},
            approval_required_for_tool=lambda connector_id, action_id, arguments, tool_capabilities: False,
            agent_machine_full_trust_for_session=lambda session_ctx: False,
            execute_single_tool_call=execute_single_tool_call,
        )

        payload = no_provider_service.execute_no_provider_request(
            message="Fetch https://httpbin.org/get and tell me the origin IP",
            workspace_id="default",
            thread_id="thread-1",
            tools=[{"name": "http_request"}],
            tool_capabilities=[],
            reasoning_effort="medium",
            services=services,
        )

        self.assertEqual(payload["reply"], "Origin IP: 203.0.113.9")
        execute_single_tool_call.assert_called_once()

    def test_plan_tool_calls_builds_browser_and_shell_actions(self) -> None:
        tool_calls = no_provider_service.plan_tool_calls(
            "Go to https://example.com and tell me the page title and main heading",
            [
                {"name": "browser__navigate"},
                {"name": "browser__observe"},
                {"name": "browser__extract_text"},
                {"name": "http_request"},
            ],
            compact_text=_compact_text,
            extract_first_path_reference=_extract_first_path_reference,
            extract_first_url=_extract_first_url,
        )

        self.assertEqual(
            tool_calls,
            [
                {"name": "browser__navigate", "arguments": {"url": "https://example.com"}},
                {"name": "browser__observe", "arguments": {}},
                {"name": "browser__extract_text", "arguments": {"selector": "h1"}},
            ],
        )

    def test_plan_tool_calls_uses_hardware_action_when_specific_local_tool_is_absent(self) -> None:
        tool_calls = no_provider_service.plan_tool_calls(
            "Run `pwd`",
            [{"name": "hardware__action"}],
            compact_text=_compact_text,
            extract_first_path_reference=_extract_first_path_reference,
            extract_first_url=_extract_first_url,
        )

        self.assertEqual(
            tool_calls,
            [
                {
                    "name": "hardware__action",
                    "arguments": {
                        "runtime_target": "user_device_gateway",
                        "action": "shell.execute",
                        "arguments": {"command": "pwd"},
                    },
                }
            ],
        )

    def test_plan_tool_calls_builds_read_only_local_system_info_shell_action(self) -> None:
        tool_calls = no_provider_service.plan_tool_calls(
            "check what i have in this hardware !",
            [{"name": "shell__exec"}],
            compact_text=_compact_text,
            extract_first_path_reference=_extract_first_path_reference,
            extract_first_url=_extract_first_url,
        )

        self.assertEqual(len(tool_calls), 1)
        self.assertEqual(tool_calls[0]["name"], "shell__exec")
        command = tool_calls[0]["arguments"]["command"]
        self.assertIn("sw_vers", command)
        self.assertIn("hw.memsize", command)
        self.assertIn("df -h /", command)

    def test_plan_tool_calls_uses_hardware_action_for_local_system_info_fallback(self) -> None:
        tool_calls = no_provider_service.plan_tool_calls(
            "What hardware specs does this machine have?",
            [{"name": "hardware__action"}],
            compact_text=_compact_text,
            extract_first_path_reference=_extract_first_path_reference,
            extract_first_url=_extract_first_url,
        )

        self.assertEqual(tool_calls[0]["name"], "hardware__action")
        self.assertEqual(tool_calls[0]["arguments"]["action"], "shell.execute")
        self.assertIn("sw_vers", tool_calls[0]["arguments"]["arguments"]["command"])

    def test_plan_tool_calls_builds_working_directory_shell_action(self) -> None:
        tool_calls = no_provider_service.plan_tool_calls(
            "what folder are you in ?",
            [{"name": "shell__exec"}],
            compact_text=_compact_text,
            extract_first_path_reference=_extract_first_path_reference,
            extract_first_url=_extract_first_url,
        )

        self.assertEqual(tool_calls, [{"name": "shell__exec", "arguments": {"command": "pwd"}}])

    def test_plan_tool_calls_uses_hardware_action_for_working_directory_fallback(self) -> None:
        tool_calls = no_provider_service.plan_tool_calls(
            "where is the runtime running from?",
            [{"name": "hardware__action"}],
            compact_text=_compact_text,
            extract_first_path_reference=_extract_first_path_reference,
            extract_first_url=_extract_first_url,
        )

        self.assertEqual(
            tool_calls,
            [
                {
                    "name": "hardware__action",
                    "arguments": {
                        "runtime_target": "user_device_gateway",
                        "action": "shell.execute",
                        "arguments": {"command": "pwd"},
                    },
                }
            ],
        )

    def test_has_obvious_direct_tool_intent_detects_directory_and_memory_requests(self) -> None:
        self.assertTrue(
            no_provider_service.has_obvious_direct_tool_intent(
                "List the first 2 files in /tmp/example",
                [],
                compact_text=_compact_text,
                extract_first_path_reference=_extract_first_path_reference,
                extract_first_url=_extract_first_url,
                parse_memory_write=lambda message: None,
                parse_memory_read=lambda message: None,
            )
        )
        self.assertTrue(
            no_provider_service.has_obvious_direct_tool_intent(
                "List the files on my desktop.",
                [],
                compact_text=_compact_text,
                extract_first_path_reference=_extract_first_path_reference,
                extract_first_url=_extract_first_url,
                parse_memory_write=lambda message: None,
                parse_memory_read=lambda message: None,
            )
        )
        self.assertTrue(
            no_provider_service.has_obvious_direct_tool_intent(
                "check what i have in this hardware !",
                [{"name": "shell__exec"}],
                compact_text=_compact_text,
                extract_first_path_reference=_extract_first_path_reference,
                extract_first_url=_extract_first_url,
                parse_memory_write=lambda message: None,
                parse_memory_read=lambda message: None,
            )
        )
        self.assertTrue(
            no_provider_service.has_obvious_direct_tool_intent(
                "what folder are you in ?",
                [{"name": "shell__exec"}],
                compact_text=_compact_text,
                extract_first_path_reference=_extract_first_path_reference,
                extract_first_url=_extract_first_url,
                parse_memory_write=lambda message: None,
                parse_memory_read=lambda message: None,
            )
        )
        self.assertTrue(
            no_provider_service.has_obvious_direct_tool_intent(
                "What is my name",
                [],
                compact_text=_compact_text,
                extract_first_path_reference=_extract_first_path_reference,
                extract_first_url=_extract_first_url,
                parse_memory_write=lambda message: None,
                parse_memory_read=lambda message: "name",
            )
        )
        self.assertTrue(
            no_provider_service.has_obvious_direct_tool_intent(
                "take a screenshot and see what you see",
                [{"name": "hardware__action"}],
                compact_text=_compact_text,
                extract_first_path_reference=_extract_first_path_reference,
                extract_first_url=_extract_first_url,
                parse_memory_write=lambda message: None,
                parse_memory_read=lambda message: None,
            )
        )
        self.assertFalse(
            no_provider_service.has_obvious_direct_tool_intent(
                "Explain the tradeoffs between SQLite and Postgres",
                [],
                compact_text=_compact_text,
                extract_first_path_reference=_extract_first_path_reference,
                extract_first_url=_extract_first_url,
                parse_memory_write=lambda message: None,
                parse_memory_read=lambda message: None,
            )
        )
        self.assertFalse(
            no_provider_service.has_obvious_direct_tool_intent(
                "Discuss hardware acceleration.",
                [{"name": "shell__exec"}],
                compact_text=_compact_text,
                extract_first_path_reference=_extract_first_path_reference,
                extract_first_url=_extract_first_url,
                parse_memory_write=lambda message: None,
                parse_memory_read=lambda message: None,
            )
        )

    def test_has_obvious_direct_tool_intent_ignores_pasted_terminal_context(self) -> None:
        pasted_context = """
        Ran command: `git status`
        ✓ ReadFile server_modules/model_router.py
        > Type your message or @path/to/file
        workspace (/directory)      branch   sandbox    /model
        quota   memory   tokens
        ~/Multi_Agent_Orchestrator_Project main untrusted gemini-3.1-pro-preview
        """

        self.assertFalse(
            no_provider_service.has_obvious_direct_tool_intent(
                pasted_context,
                [],
                compact_text=_compact_text,
                extract_first_path_reference=_extract_first_path_reference,
                extract_first_url=_extract_first_url,
                parse_memory_write=lambda message: None,
                parse_memory_read=lambda message: None,
            )
        )

    def test_build_direct_tool_approval_response_returns_approval_action(self) -> None:
        services = no_provider_service.NoProviderExecutionServices(
            compact_text=_compact_text,
            safe_positive_int=_safe_positive_int,
            resolve_local_path=Path,
            extract_first_path_reference=_extract_first_path_reference,
            extract_first_url=_extract_first_url,
            parse_page_state=lambda value: value,
            parse_memory_write=lambda message: None,
            parse_memory_read=lambda message: None,
            handle_memory_request=lambda workspace_id, message: None,
            parse_tool_name=lambda tool_name: ("telegram_bot", "send_message"),
            tool_arguments_payload=lambda arguments: {"input": "hello from direct chat"},
            approval_required_for_tool=lambda connector_id, action_id, arguments, tool_capabilities: True,
            agent_machine_full_trust_for_session=lambda session_ctx: False,
            execute_single_tool_call=lambda **kwargs: "unused",
        )

        payload = no_provider_service.build_direct_tool_approval_response(
            tool_calls=[{"name": "telegram_bot__send_message", "arguments": "{\"input\":\"hello from direct chat\"}"}],
            tool_capabilities=[],
            services=services,
        )

        self.assertEqual(payload["mode"], "answer_with_action")
        self.assertEqual(payload["reply"], "")
        self.assertEqual(payload["approvals"][0]["prompt"], "Approve Telegram Bot to send message before continuing.")
        self.assertEqual(payload["actions"][0]["connector"], "telegram_bot")
        self.assertEqual(payload["actions"][0]["action"], "send_message")
        self.assertEqual(payload["actions"][0]["input"], "hello from direct chat")


if __name__ == "__main__":
    unittest.main()
