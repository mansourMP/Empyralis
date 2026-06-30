import unittest

from server_modules.connectors.autopilot_event_bridge_service import AutopilotEventBridgeService


class AutopilotEventBridgeServiceTests(unittest.TestCase):
    def test_bridge_initializes_runtime_before_record(self) -> None:
        calls = []

        class _EventService:
            def record_event(self, **kwargs):
                calls.append(("record", kwargs))
                return {"ok": True}

        service = AutopilotEventBridgeService(
            init_runtime=lambda: calls.append(("init", None)),
            event_service=lambda: _EventService(),
        )

        payload = service.record_channel_event(
            channel="telegram",
            direction="outbound",
            event_type="message",
            text="hello",
        )

        self.assertEqual(payload, {"ok": True})
        self.assertEqual(calls[0][0], "init")
        self.assertEqual(calls[1][0], "record")

    def test_bridge_forwards_throttled_record_callback(self) -> None:
        captured = {}

        class _EventService:
            def record_event_throttled(self, **kwargs):
                captured.update(kwargs)
                return True

        callback = lambda **kwargs: {"ok": True}
        service = AutopilotEventBridgeService(
            init_runtime=lambda: None,
            event_service=lambda: _EventService(),
        )

        result = service.record_channel_event_throttled(
            channel="telegram",
            direction="system",
            event_type="error",
            dedupe_seconds=15.0,
            record_event_func=callback,
        )

        self.assertTrue(result)
        self.assertIs(captured["record_event_func"], callback)


if __name__ == "__main__":
    unittest.main()
