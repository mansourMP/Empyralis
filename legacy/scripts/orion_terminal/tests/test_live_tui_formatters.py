from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone

from scripts.orion_terminal.services.live_tui_formatters import (
    collect_chat_rows,
    format_channel_health_rollup,
    format_chat_rows,
    normalize_chat_row,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class LiveTuiFormattersTests(unittest.TestCase):
    def setUp(self) -> None:
        self.prev_meta = os.environ.get("ORION_CLI_CHAT_SHOW_META")
        self.prev_compact = os.environ.get("ORION_CLI_CHAT_COMPACT")
        os.environ["ORION_CLI_CHAT_SHOW_META"] = "0"
        os.environ["ORION_CLI_CHAT_COMPACT"] = "1"

    def tearDown(self) -> None:
        if self.prev_meta is None:
            os.environ.pop("ORION_CLI_CHAT_SHOW_META", None)
        else:
            os.environ["ORION_CLI_CHAT_SHOW_META"] = self.prev_meta
        if self.prev_compact is None:
            os.environ.pop("ORION_CLI_CHAT_COMPACT", None)
        else:
            os.environ["ORION_CLI_CHAT_COMPACT"] = self.prev_compact

    def test_channel_health_ignores_timeout_noise_errors(self) -> None:
        items = [
            {
                "ts": _now_iso(),
                "direction": "system",
                "channel": "telegram",
                "event_type": "error",
                "text": "<urlopen error timed out>",
            },
            {
                "ts": _now_iso(),
                "direction": "inbound",
                "channel": "telegram",
                "event_type": "message",
                "text": "hello",
            },
        ]
        rendered = format_channel_health_rollup(items, window_seconds=300)
        self.assertIn("telegram:", rendered)
        self.assertIn("connected", rendered)
        self.assertNotIn("degraded", rendered)

    def test_channel_health_keeps_non_timeout_errors(self) -> None:
        items = [
            {
                "ts": _now_iso(),
                "direction": "system",
                "channel": "telegram",
                "event_type": "error",
                "text": "invalid bot token",
            },
            {
                "ts": _now_iso(),
                "direction": "inbound",
                "channel": "telegram",
                "event_type": "message",
                "text": "hello",
            },
        ]
        rendered = format_channel_health_rollup(items, window_seconds=300)
        self.assertIn("telegram:", rendered)
        self.assertIn("degraded", rendered)

    def test_format_chat_rows_shows_skill_replay_metadata(self) -> None:
        rows = collect_chat_rows(
            items=[
                {
                    "ts": _now_iso(),
                    "direction": "outbound",
                    "channel": "telegram",
                    "event_type": "message",
                    "run_id": "run-12345678",
                    "text": "Here is the answer.",
                    "metadata": {
                        "skill_replay": {
                            "applied": True,
                            "confidence": 0.91,
                            "signature": "run.status.short",
                            "reason": "high_confidence",
                        }
                    },
                }
            ],
            local_entries=[],
            limit=10,
        )
        rendered = "\n".join(format_chat_rows(rows))
        self.assertIn("Here is the answer.", rendered)
        self.assertIn("replay: applied conf=0.91 sig=run.status.short reason=high_confidence", rendered)

    def test_format_chat_rows_hides_replay_line_when_absent(self) -> None:
        rows = collect_chat_rows(
            items=[
                {
                    "ts": _now_iso(),
                    "direction": "outbound",
                    "channel": "telegram",
                    "event_type": "message",
                    "run_id": "run-12345678",
                    "text": "Here is the answer.",
                }
            ],
            local_entries=[],
            limit=10,
        )
        rendered = "\n".join(format_chat_rows(rows))
        self.assertIn("Here is the answer.", rendered)
        self.assertNotIn("replay:", rendered)

    def test_normalize_chat_row_prefers_explicit_role(self) -> None:
        row = normalize_chat_row(
            {
                "id": "evt-1",
                "ts": _now_iso(),
                "channel": "telegram",
                "direction": "inbound",
                "event_type": "message",
                "role": "orion",
                "text": "hello",
            },
            source="inbox",
        )
        self.assertEqual(row["role"], "orion")
        self.assertEqual(row["channel"], "telegram")
        self.assertEqual(row["source"], "inbox")

    def test_collect_chat_rows_preserves_local_roles(self) -> None:
        rows = collect_chat_rows(
            items=[],
            local_entries=[
                {
                    "ts": _now_iso(),
                    "role": "you",
                    "text": "hi",
                    "channel": "terminal",
                },
                {
                    "ts": _now_iso(),
                    "role": "orion",
                    "text": "hello",
                    "channel": "terminal",
                },
            ],
            limit=10,
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["role"], "you")
        self.assertEqual(rows[1]["role"], "orion")

    def test_collect_chat_rows_keeps_repeated_user_messages(self) -> None:
        ts = _now_iso()
        rows = collect_chat_rows(
            items=[
                {
                    "id": "evt-1",
                    "ts": ts,
                    "direction": "inbound",
                    "channel": "telegram",
                    "event_type": "message",
                    "text": "hello",
                },
                {
                    "id": "evt-2",
                    "ts": ts,
                    "direction": "inbound",
                    "channel": "telegram",
                    "event_type": "message",
                    "text": "hello",
                },
            ],
            local_entries=[],
            limit=10,
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["text"], "hello")
        self.assertEqual(rows[1]["text"], "hello")

    def test_collect_chat_rows_dedupes_repeated_approval_banners(self) -> None:
        ts = _now_iso()
        rows = collect_chat_rows(
            items=[
                {
                    "id": "evt-1",
                    "ts": ts,
                    "direction": "outbound",
                    "channel": "telegram",
                    "event_type": "message",
                    "text": "⚠️ Approval required. Pending approvals: - 76786a5e ... event_id: 76786a5e-4be2-4fe3-a0ed-83745eb61ead",
                },
                {
                    "id": "evt-2",
                    "ts": ts,
                    "direction": "outbound",
                    "channel": "telegram",
                    "event_type": "message",
                    "text": "Objective approval required [76786a5e] ... Use /ok 76786a5e",
                },
            ],
            local_entries=[],
            limit=10,
        )
        self.assertEqual(len(rows), 1)

    def test_collect_chat_rows_hides_telegram_command_card(self) -> None:
        ts = _now_iso()
        rows = collect_chat_rows(
            items=[
                {
                    "id": "evt-1",
                    "ts": ts,
                    "direction": "outbound",
                    "channel": "telegram",
                    "event_type": "message",
                    "text": "Empyralis Telegram Commands - /orion run <goal> - /orion status",
                },
                {
                    "id": "evt-2",
                    "ts": ts,
                    "direction": "inbound",
                    "channel": "telegram",
                    "event_type": "message",
                    "text": "hello",
                },
            ],
            local_entries=[],
            limit=10,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["text"], "hello")

    def test_collect_chat_rows_hides_runtime_boilerplate_lines(self) -> None:
        ts = _now_iso()
        rows = collect_chat_rows(
            items=[
                {
                    "id": "evt-run-summary",
                    "ts": ts,
                    "direction": "inbound",
                    "channel": "terminal",
                    "event_type": "message",
                    "text": "Local worker completed Empyralis run for goal: hello.",
                },
                {
                    "id": "evt-run-id",
                    "ts": ts,
                    "direction": "inbound",
                    "channel": "terminal",
                    "event_type": "message",
                    "text": "run_id: abc12345-dead-beef-cafe-111122223333",
                },
                {
                    "id": "evt-keep",
                    "ts": ts,
                    "direction": "inbound",
                    "channel": "terminal",
                    "event_type": "message",
                    "text": "Actual assistant message.",
                },
            ],
            local_entries=[],
            limit=10,
        )
        text_blob = "\n".join(str(row.get("text") or "") for row in rows)
        self.assertNotIn("Local worker completed Empyralis run for goal", text_blob)
        self.assertNotIn("run_id:", text_blob)
        self.assertIn("Actual assistant message.", text_blob)


if __name__ == "__main__":
    unittest.main()
