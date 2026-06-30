from __future__ import annotations

import importlib
import threading
import unittest
from unittest.mock import patch


class HardwareActivityEventServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = importlib.import_module("server_modules.hardware_activity_event_service")
        self.runtime_events = importlib.import_module("server_modules.runtime_events")

    def test_list_hardware_action_events_filters_diagnostic_probe_rows(self) -> None:
        events = [
            {
                "id": "probe-event",
                "ts": "2026-06-07T07:00:00Z",
                "channel": "hardware",
                "event_type": "hardware_action",
                "workspace_id": "ws-1",
                "run_id": "hardware-capability-probe-gw-1-123",
                "trace_id": "hardware-capability-probe-gw-1",
                "metadata": {
                    "gateway_id": "gw-1",
                    "capability": "shell.execute",
                    "status": "failed",
                    "timestamp": "2026-06-07T07:00:00Z",
                },
            },
            {
                "id": "user-event",
                "ts": "2026-06-07T07:01:00Z",
                "channel": "hardware",
                "event_type": "hardware_action",
                "workspace_id": "ws-1",
                "run_id": "user-request-1",
                "trace_id": "trace-1",
                "metadata": {
                    "gateway_id": "gw-1",
                    "capability": "screenshot.capture",
                    "status": "completed",
                    "timestamp": "2026-06-07T07:01:00Z",
                },
            },
        ]
        with (
            patch.object(self.runtime_events, "CHANNEL_EVENTS", events),
            patch.object(self.runtime_events, "CHANNEL_EVENTS_LOCK", threading.Lock()),
        ):
            result = self.service.list_hardware_action_events(workspace_id="ws-1", limit=10)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], "user-event")
        self.assertEqual(result[0]["capability"], "screenshot.capture")


if __name__ == "__main__":
    unittest.main()
