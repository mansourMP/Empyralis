import threading
import unittest

from server_modules.connectors.autopilot_event_service import AutopilotEventService


class AutopilotEventServiceTests(unittest.TestCase):
    def _make_service(self, **overrides) -> AutopilotEventService:
        recorded = overrides.pop("recorded", [])
        writes = overrides.pop("writes", [])
        return AutopilotEventService(
            append_channel_event=overrides.pop("append_channel_event", lambda **kwargs: recorded.append(kwargs) or {"ok": True}),
            utc_now_iso=overrides.pop("utc_now_iso", lambda: "2026-04-05T00:00:00Z"),
            normalize_workspace_id=overrides.pop("normalize_workspace_id", lambda value: str(value or "")),
            truncate_one_line=overrides.pop("truncate_one_line", lambda text, limit: str(text or "")[:limit]),
            json_safe=overrides.pop("json_safe", lambda value: value),
            dead_letter_lock=threading.Lock(),
            read_json=overrides.pop("read_json", lambda path, default: {"version": 1, "items": []}),
            write_json=overrides.pop("write_json", lambda path, payload: writes.append((path, payload))),
            dead_letter_file="dead-letter.json",
            dead_letter_limit=overrides.pop("dead_letter_limit", 3),
            collapse_whitespace=overrides.pop("collapse_whitespace", lambda text: " ".join(str(text or "").split())),
        )

    def test_record_event_returns_payload(self) -> None:
        recorded = []
        service = self._make_service(recorded=recorded)
        payload = service.record_event(channel="telegram", direction="outbound", event_type="message", text="hello")
        self.assertEqual(payload, {"ok": True})
        self.assertEqual(recorded[0]["channel"], "telegram")

    def test_append_dead_letter_limits_items(self) -> None:
        writes = []
        service = self._make_service(
            writes=writes,
            read_json=lambda path, default: {"version": 1, "items": [{"id": "old-1"}, {"id": "old-2"}, {"id": "old-3"}]},
            dead_letter_limit=2,
        )
        service.append_dead_letter(channel="whatsapp", direction="outbound", event_type="message", reason="boom")
        self.assertEqual(len(writes), 1)
        payload = writes[0][1]
        self.assertEqual(len(payload["items"]), 2)
        self.assertEqual(payload["items"][0]["reason"], "boom")

    def test_record_event_throttled_suppresses_duplicate(self) -> None:
        recorded = []
        service = self._make_service(recorded=recorded)
        first = service.record_event_throttled(
            channel="telegram",
            direction="system",
            event_type="error",
            text="same text",
            dedupe_seconds=60.0,
        )
        second = service.record_event_throttled(
            channel="telegram",
            direction="system",
            event_type="error",
            text="same text",
            dedupe_seconds=60.0,
        )
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(len(recorded), 1)

    def test_record_event_throttled_uses_override_callback(self) -> None:
        service = self._make_service()
        recorded = []

        result = service.record_event_throttled(
            channel="telegram",
            direction="system",
            event_type="error",
            text="same text",
            dedupe_seconds=0.0,
            record_event_func=lambda **kwargs: recorded.append(kwargs) or {"ok": True},
        )

        self.assertTrue(result)
        self.assertEqual(len(recorded), 1)


if __name__ == "__main__":
    unittest.main()
