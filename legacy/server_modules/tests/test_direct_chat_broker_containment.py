import types
import unittest
from unittest.mock import Mock, patch

from server_modules import direct_chat_response_service, skills_service


class DirectChatBrokerContainmentTests(unittest.TestCase):
    def _services(self, *, execute_direct_tool_calls) -> direct_chat_response_service.DirectChatResponseServices:
        return direct_chat_response_service.DirectChatResponseServices(
            with_context_used=lambda payload, context: {**payload, "context_used": context},
            build_context_used=lambda **kwargs: kwargs,
            connected_provider_tokens=lambda _workspace_id: ["openai"],
            active_run_count=lambda _workspace_id: 0,
            slash_command_help_text=lambda: "help text",
            execute_direct_tool_calls=execute_direct_tool_calls,
            direct_chat_credentials=lambda _workspace_id, _provider: {"api_key": "sk-test"},
            capture_exception=lambda exc: None,
        )

    def _callbacks(self) -> types.SimpleNamespace:
        return types.SimpleNamespace(
            parse_tool_name=lambda _name: ("llm", "task"),
            tool_arguments_payload=lambda arguments: dict(arguments or {}),
            run_async_tool_call=lambda _coro: {"status_code": 200, "body": "ok"},
            llm_task=Mock(return_value={"ok": True}),
            normalize_reasoning_effort=lambda value: value or "medium",
            search_memory_notebook=lambda *_args, **_kwargs: [],
            safe_positive_int=lambda value, default: int(value or default),
            get_memory_notebook_excerpt=lambda *_args, **_kwargs: {},
            parse_json_object_loose=lambda value: value,
            build_direct_local_tool_config=lambda *_args, **_kwargs: ("shell", {"command": "pwd"}),
            format_direct_local_tool_result=lambda result: str(result),
            build_direct_tool_config=lambda connector_id, action_id, tool_input: {
                "connector": connector_id,
                "action_id": action_id,
                "input": tool_input,
            },
            format_direct_tool_result=lambda result: str(result),
            web_search=lambda _query: [],
            web_fetch=lambda _url: "",
        )

    def test_approval_confirmation_payload_does_not_execute_direct_tool_calls(self):
        execute_direct_tool_calls = Mock(return_value="tool reply")
        payload = direct_chat_response_service.approval_confirmation_payload(
            approved_action_payload={"connector": "email", "action": "send"},
            workspace_id="workspace-1",
            thread_id="thread-1",
            requested_provider="openai",
            requested_model="gpt-5.4",
            reasoning_effort="medium",
            session_ctx=None,
            proactive_suggestions=[],
            connected_systems=[],
            tool_capabilities=[],
            tool_write_action_available_fn=lambda connector, action, capabilities: True,
            approved_action_to_tool_call_fn=lambda approved: {"name": "email__send", "arguments": approved},
            services=self._services(execute_direct_tool_calls=execute_direct_tool_calls),
        )

        execute_direct_tool_calls.assert_not_called()
        self.assertEqual(payload["reply"], "")
        self.assertEqual(payload["error"], "direct_chat_tool_execution_blocked")
        self.assertTrue(payload["interventions"])
        self.assertEqual(payload["interventions"][0]["code"], "direct_chat_tool_execution_blocked")
        self.assertEqual(payload["interventions"][0]["status"], "blocked")

    def test_execute_single_direct_tool_call_blocks_llm_task(self):
        callbacks = self._callbacks()

        with self.assertRaisesRegex(RuntimeError, "direct_chat_tool_execution_blocked"):
            skills_service.execute_single_direct_tool_call(
                tool_call={"name": "llm__task", "arguments": {"prompt": "summarize this"}},
                workspace_id="workspace-1",
                thread_id="thread-1",
                provider="openai",
                model="gpt-5.4",
                credentials={"api_key": "sk-test"},
                callbacks=callbacks,
            )

        callbacks.llm_task.assert_not_called()

    def test_execute_single_direct_tool_call_blocks_http_request(self):
        callbacks = self._callbacks()
        callbacks.parse_tool_name = lambda _name: ("http", "request")

        with patch("server_modules.tools_http.http_request", return_value={"status_code": 200, "body": "ok"}) as request_mock:
            with self.assertRaisesRegex(RuntimeError, "direct_chat_tool_execution_blocked"):
                skills_service.execute_single_direct_tool_call(
                    tool_call={"name": "http__request", "arguments": {"url": "https://example.com"}},
                    workspace_id="workspace-1",
                    thread_id="thread-1",
                    callbacks=callbacks,
                )

        request_mock.assert_not_called()

    def test_execute_single_direct_tool_call_allows_safe_local_shell_read(self):
        callbacks = self._callbacks()
        callbacks.parse_tool_name = lambda _name: ("shell", "exec")

        with patch("server_modules.runs_execution._workflow_execute_local_tool", return_value={"status": "ok"}) as local_tool_mock:
            skills_service.execute_single_direct_tool_call(
                tool_call={"name": "shell__exec", "arguments": {"command": "pwd"}},
                workspace_id="workspace-1",
                thread_id="thread-1",
                callbacks=callbacks,
            )

        local_tool_mock.assert_called_once()

    def test_execute_single_direct_tool_call_blocks_unsafe_local_shell_execution(self):
        callbacks = self._callbacks()
        callbacks.parse_tool_name = lambda _name: ("shell", "exec")

        with patch("server_modules.runs_execution._workflow_execute_local_tool", return_value={"status": "ok"}) as local_tool_mock:
            with self.assertRaisesRegex(RuntimeError, "direct_chat_tool_execution_blocked"):
                skills_service.execute_single_direct_tool_call(
                    tool_call={"name": "shell__exec", "arguments": {"command": "rm -rf /tmp/test"}},
                    workspace_id="workspace-1",
                    thread_id="thread-1",
                    callbacks=callbacks,
                )

        local_tool_mock.assert_not_called()

    def test_execute_single_direct_tool_call_blocks_connector_action_execution(self):
        callbacks = self._callbacks()
        callbacks.parse_tool_name = lambda _name: ("slack", "post_message")

        with patch("server_modules.runs_execution._workflow_execute_connector_action", return_value={"status": "ok"}) as connector_mock:
            with self.assertRaisesRegex(RuntimeError, "direct_chat_tool_execution_blocked"):
                skills_service.execute_single_direct_tool_call(
                    tool_call={"name": "slack__post_message", "arguments": {"input": "hello"}},
                    workspace_id="workspace-1",
                    thread_id="thread-1",
                    callbacks=callbacks,
                )

        connector_mock.assert_not_called()

    def test_workflow_execute_connector_action_rejects_chat_direct_source(self):
        from server_modules import runs_execution

        with self.assertRaisesRegex(RuntimeError, "direct_chat_tool_execution_blocked"):
            runs_execution._workflow_execute_connector_action(
                "run-1",
                "node-1",
                {"workspace_id": "workspace-1", "metadata": {"source": "chat_direct"}},
                {"connector": "slack", "action_id": "post_message"},
                current_text="hello",
            )

    def test_workflow_execute_local_tool_rejects_chat_direct_source(self):
        from server_modules import runs_execution

        with patch("server_modules.runs_execution.run_service.execute_workflow_local_tool", return_value={"status": "ok"}) as local_tool_mock:
            with self.assertRaisesRegex(RuntimeError, "direct_chat_tool_execution_blocked"):
                runs_execution._workflow_execute_local_tool(
                    "run-1",
                    {"workspace_id": "workspace-1", "metadata": {"source": "chat_direct"}},
                    {"command": "pwd"},
                    label="shell__exec",
                    variant="shell",
                    current_text="pwd",
                )

        local_tool_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
