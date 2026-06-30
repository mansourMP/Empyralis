import unittest

from server_modules import direct_tool_runtime_facade_service as service
from server_modules.tests.test_direct_chat_runtime_facade_service import _callbacks


def _namespace() -> dict:
    return {
        "compact_step_detail": lambda value: " ".join(str(value or "").split()).strip() or None,
        "titleize_direct_step_token": lambda value: " ".join(word.capitalize() for word in str(value or "").split("_")),
        "run_async_tool_call": lambda awaitable: awaitable,
        "parse_tool_name": lambda name: tuple(str(name or "").split("__", 1)) if "__" in str(name or "") else ("", ""),
        "tool_arguments_payload": lambda arguments: arguments if isinstance(arguments, dict) else {},
        "safe_positive_int": lambda value, default=0: default,
        "normalize_reasoning_effort": lambda value: str(value or "").strip().lower() or None,
        "build_direct_local_tool_config": lambda connector_id, action_id, arguments: ("read", {"connector": connector_id, "action": action_id}),
        "format_direct_local_tool_result": lambda result: str(result),
        "build_direct_tool_config": lambda connector_id, action_id, tool_input: {"connector": connector_id, "action": action_id, "input": tool_input},
        "format_direct_tool_result": lambda result: str(result),
    }


class DirectToolRuntimeFacadeServiceTests(unittest.TestCase):
    def test_build_direct_tool_execution_callbacks_preserves_operator_callbacks(self) -> None:
        namespace = _namespace()

        callbacks = service.build_direct_tool_execution_callbacks(
            namespace=namespace,
            parse_json_object_loose=lambda value: {},
            llm_task=lambda *args, **kwargs: {"ok": True},
            web_search=lambda query: [],
            web_fetch=lambda url: url,
            search_memory_notebook=lambda **kwargs: [],
            get_memory_notebook_excerpt=lambda **kwargs: {},
        )

        self.assertIs(callbacks.tool_arguments_payload, namespace["tool_arguments_payload"])
        self.assertIs(callbacks.build_direct_tool_config, namespace["build_direct_tool_config"])

    def test_build_no_provider_execution_services_delegates(self) -> None:
        callbacks = _callbacks()
        services = service.build_no_provider_execution_services(callbacks=callbacks)

        self.assertIs(services.execute_single_tool_call, callbacks.execute_single_direct_tool_call)

    def test_build_direct_tool_approval_response_delegates(self) -> None:
        payload = service.build_direct_tool_approval_response(
            tool_calls=[{"name": "telegram_bot__send_message", "arguments": {"input": "hello"}}],
            tool_capabilities=[{"id": "telegram_bot", "approval_required_actions": ["send_message"]}],
            session_ctx=None,
            callbacks=_callbacks(),
        )

        self.assertIsNotNone(payload)
        self.assertEqual(payload["mode"], "answer_with_action")


if __name__ == "__main__":
    unittest.main()
