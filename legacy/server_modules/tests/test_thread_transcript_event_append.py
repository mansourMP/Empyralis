from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from server_modules import thread_service


class ThreadTranscriptEventAppendTests(unittest.IsolatedAsyncioTestCase):
    async def test_append_assistant_turn_transcript_event_sanitizes_before_persisting(self) -> None:
        with patch(
            "server_modules.thread_service.control_plane_repository.append_agent_turn_transcript_event",
            new=AsyncMock(return_value={"turn": {"id": "turn-assistant"}}),
        ) as append_event:
            result = await thread_service.append_assistant_turn_transcript_event(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                thread_id="thread-1",
                request_id="req-1",
                trace_id="trace-1",
                run_id="run-1",
                event_name="trace",
                payload={
                    "event_type": "tool.result",
                    "tool_call_id": "tool-1",
                    "data": {
                        "status": "completed",
                        "summary": "Ran command.",
                        "runtime_session_id": "hrs-1",
                        "args_preview": {"command": "secret command"},
                        "raw_output": "private output",
                    },
                },
            )

        self.assertEqual(result, {"turn": {"id": "turn-assistant"}})
        append_event.assert_awaited_once()
        event = append_event.await_args.kwargs["transcript_event"]
        self.assertEqual(event["event"], "trace")
        self.assertEqual(event["schema_version"], 1)
        payload = event["payload"]
        self.assertEqual(payload["event_type"], "tool.result")
        self.assertEqual(payload["data"]["status"], "completed")
        self.assertEqual(payload["data"]["runtime_session_id"], "hrs-1")
        self.assertNotIn("args_preview", payload["data"])
        self.assertNotIn("raw_output", payload["data"])


if __name__ == "__main__":
    unittest.main()
