from __future__ import annotations

import unittest

from scripts.orion_terminal.tui.chat.session_router import (
    _reorder_overlay_options_for_active_channel,
    _session_overlay_options,
)
from scripts.orion_terminal.core import Choice


class SessionRouterTests(unittest.TestCase):
    def test_session_overlay_options_sorts_by_latest_activity(self) -> None:
        options = _session_overlay_options(
            events=[],
            sessions=[
                {
                    "session_id": "telegram:older",
                    "channel": "telegram",
                    "event_count": 10,
                    "last_ts": "2026-02-28T01:00:00Z",
                },
                {
                    "session_id": "telegram:newer",
                    "channel": "telegram",
                    "event_count": 2,
                    "last_ts": "2026-02-28T02:00:00Z",
                },
            ],
        )
        keys = [opt.key for opt in options]
        self.assertIn("session:telegram:newer", keys)
        self.assertIn("session:telegram:older", keys)
        self.assertLess(keys.index("session:telegram:newer"), keys.index("session:telegram:older"))

    def test_session_overlay_options_uses_event_count_as_tiebreaker(self) -> None:
        options = _session_overlay_options(
            events=[],
            sessions=[
                {
                    "session_id": "telegram:low-count",
                    "channel": "telegram",
                    "event_count": 2,
                    "last_ts": "2026-02-28T02:00:00Z",
                },
                {
                    "session_id": "telegram:high-count",
                    "channel": "telegram",
                    "event_count": 9,
                    "last_ts": "2026-02-28T02:00:00Z",
                },
            ],
        )
        keys = [opt.key for opt in options]
        self.assertLess(keys.index("session:telegram:high-count"), keys.index("session:telegram:low-count"))

    def test_overlay_reorders_active_channel_sessions_first(self) -> None:
        options = [
            Choice("__all__", "All channels", ""),
            Choice("__telegram__", "Telegram channel", ""),
            Choice("__whatsapp__", "WhatsApp channel", ""),
            Choice("session:whatsapp:aaa", "whatsapp:aaa", ""),
            Choice("session:telegram:bbb", "telegram:bbb", ""),
            Choice("session:telegram:ccc", "telegram:ccc", ""),
            Choice("session:terminal:ddd", "terminal:ddd", ""),
        ]
        reordered = _reorder_overlay_options_for_active_channel(options, "telegram")
        keys = [opt.key for opt in reordered]
        self.assertEqual(keys[:3], ["__all__", "__telegram__", "__whatsapp__"])
        self.assertEqual(keys[3:5], ["session:telegram:bbb", "session:telegram:ccc"])


if __name__ == "__main__":
    unittest.main()
