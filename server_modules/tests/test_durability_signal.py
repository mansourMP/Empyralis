"""Proof for Fix 5: durability failures are made loud, not swallowed.

capture_durability_failure must ERROR-log + capture to Sentry + best-effort emit
an activity-ledger dead-letter event, and must never raise from a failure path.
"""

import asyncio
import logging
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import durability_signal


class CaptureDurabilityFailureTests(unittest.TestCase):
    def test_error_logged_and_sentry_captured(self):
        exc = RuntimeError("db gone")
        with patch("sentry_sdk.capture_exception") as sentry_cap, \
             self.assertLogs("empyralis.durability", level="ERROR") as logs:
            durability_signal.capture_durability_failure("terminal persist", exc)
        sentry_cap.assert_called_once_with(exc)
        self.assertTrue(any("terminal persist" in line for line in logs.output))

    def test_never_raises_when_sentry_and_ledger_fail(self):
        with patch("sentry_sdk.capture_exception", side_effect=RuntimeError("sentry down")):
            # Must not propagate — this runs inside failure paths.
            durability_signal.capture_durability_failure("ctx", RuntimeError("x"), workspace_id="ws")

    def test_ledger_dead_letter_emitted_with_workspace(self):
        mock_append = AsyncMock()

        async def scenario():
            with patch("server_modules.activity_ledger_service.append_activity_event", mock_append):
                durability_signal.capture_durability_failure(
                    "channel delivery", RuntimeError("5xx"),
                    workspace_id="ws-9", run_id="r1", channel="telegram_hosted",
                    event_class="channel_delivery_dead_letter",
                )
                await asyncio.sleep(0.02)  # let the scheduled task run

        asyncio.run(scenario())
        mock_append.assert_awaited_once()
        kwargs = mock_append.await_args.kwargs
        self.assertEqual(kwargs["workspace_id"], "ws-9")
        self.assertEqual(kwargs["event_class"], "channel_delivery_dead_letter")
        self.assertEqual(kwargs["channel"], "telegram_hosted")

    def test_no_ledger_without_workspace(self):
        mock_append = AsyncMock()

        async def scenario():
            with patch("server_modules.activity_ledger_service.append_activity_event", mock_append):
                durability_signal.capture_durability_failure("ctx", RuntimeError("x"))
                await asyncio.sleep(0.02)

        asyncio.run(scenario())
        mock_append.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
