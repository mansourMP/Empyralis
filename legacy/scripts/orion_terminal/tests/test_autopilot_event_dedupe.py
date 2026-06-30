import unittest
from unittest import mock

try:
    from server_modules import autopilot_connectors as ac
    _IMPORT_ERROR = None
except ModuleNotFoundError as exc:
    ac = None
    _IMPORT_ERROR = exc


class AutopilotEventDedupeTests(unittest.TestCase):
    def setUp(self) -> None:
        if ac is None:
            self.skipTest(f"server_modules dependencies unavailable: {_IMPORT_ERROR}")
        ac._AUTOPILOT_EVENT_DEDUP.clear()

    def test_throttled_event_records_once_within_window(self):
        with mock.patch.object(ac, "_init", return_value=None), mock.patch.object(
            ac, "_record_channel_event"
        ) as record_mock:
            first = ac._record_channel_event_throttled(
                channel="telegram",
                direction="system",
                event_type="error",
                text="<urlopen error timed out>",
                workspace_id="default",
                action="connector",
                dedupe_seconds=30.0,
            )
            second = ac._record_channel_event_throttled(
                channel="telegram",
                direction="system",
                event_type="error",
                text="<urlopen error timed out>",
                workspace_id="default",
                action="connector",
                dedupe_seconds=30.0,
            )

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(record_mock.call_count, 1)

    def test_throttled_event_with_zero_window_does_not_dedupe(self):
        with mock.patch.object(ac, "_init", return_value=None), mock.patch.object(
            ac, "_record_channel_event"
        ) as record_mock:
            first = ac._record_channel_event_throttled(
                channel="telegram",
                direction="system",
                event_type="error",
                text="connection reset by peer",
                workspace_id="default",
                action="connector",
                dedupe_seconds=0.0,
            )
            second = ac._record_channel_event_throttled(
                channel="telegram",
                direction="system",
                event_type="error",
                text="connection reset by peer",
                workspace_id="default",
                action="connector",
                dedupe_seconds=0.0,
            )

        self.assertTrue(first)
        self.assertTrue(second)
        self.assertEqual(record_mock.call_count, 2)


if __name__ == "__main__":
    unittest.main()
