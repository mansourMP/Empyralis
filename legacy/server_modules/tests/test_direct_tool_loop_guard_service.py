import unittest

from server_modules import direct_tool_loop_guard_service as service


class DirectToolLoopGuardServiceTests(unittest.TestCase):
    def test_tool_call_signature_normalizes_nested_local_input(self) -> None:
        signature = service.tool_call_signature(
            {
                "name": "computer__click",
                "arguments": {"input": "{\"x\": 12, \"y\": 30}"},
            },
            tool_arguments_payload_fn=lambda value: value if isinstance(value, dict) else {},
            parse_tool_name_fn=lambda name: tuple(name.split("__", 1)),
            parse_json_object_loose_fn=lambda text: {"x": 12, "y": 30} if "\"x\"" in text else {},
        )

        self.assertEqual(signature, 'computer__click:{"x": 12, "y": 30}')

    def test_record_direct_tool_signature_tracks_repeats(self) -> None:
        loop_state = {}

        repeated = service.record_direct_tool_signature(
            "thread-1",
            {"name": "memory_search", "arguments": {"query": "timezone"}},
            loop_state=loop_state,
            repeat_limit=2,
            tool_call_signature_fn=lambda tool_call: f"{tool_call['name']}:{tool_call['arguments']['query']}",
        )
        self.assertFalse(repeated)

        repeated = service.record_direct_tool_signature(
            "thread-1",
            {"name": "memory_search", "arguments": {"query": "timezone"}},
            loop_state=loop_state,
            repeat_limit=2,
            tool_call_signature_fn=lambda tool_call: f"{tool_call['name']}:{tool_call['arguments']['query']}",
        )
        self.assertTrue(repeated)

    def test_clear_direct_tool_loop_state_removes_entry(self) -> None:
        loop_state = {"thread-1": {"signature": "sig", "count": 2}}

        service.clear_direct_tool_loop_state(
            "thread-1",
            loop_state=loop_state,
        )

        self.assertEqual(loop_state, {})


if __name__ == "__main__":
    unittest.main()
