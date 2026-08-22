"""Proof for Fix 2: inline channel replies survive a transient send failure.

Regression cover for the audit finding that a generated reply was dropped on a
transient channel 5xx (single send attempt, exception swallowed) while the
webhook still ACKed 200 — silently losing the user's answer.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import agent_reply_dispatcher as srd


class FakeTransport:
    """Minimal ChannelTransport stand-in for _send_one_chunk."""

    def __init__(self, results):
        # results: list of outcomes per send_message call — True/False or an
        # Exception instance to raise (simulating a provider 5xx / dropped socket).
        self._results = list(results)
        self.calls = 0

    def format_text(self, text):
        return text  # identity → single send path per attempt

    async def send_message(self, text, reply_to_id=None):
        self.calls += 1
        outcome = self._results.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class InlineReplyRetryTests(unittest.TestCase):
    def test_transient_5xx_then_retry_delivers(self):
        # Attempt 1 raises (like a Telegram 5xx), attempt 2 succeeds.
        transport = FakeTransport([RuntimeError("Telegram 500"), True])
        with patch.object(srd.asyncio, "sleep", new_callable=AsyncMock):
            delivered = asyncio.run(srd._send_one_chunk(transport, "hello"))
        self.assertTrue(delivered)          # retry delivered the reply
        self.assertEqual(transport.calls, 2)

    def test_transient_falsy_then_retry_delivers(self):
        # A falsy return (provider declined) is also treated as transient.
        transport = FakeTransport([False, True])
        with patch.object(srd.asyncio, "sleep", new_callable=AsyncMock):
            delivered = asyncio.run(srd._send_one_chunk(transport, "hello"))
        self.assertTrue(delivered)
        self.assertEqual(transport.calls, 2)

    def test_total_failure_returns_false_so_webhook_declines_ack(self):
        # All attempts fail → returns False so the webhook returns non-200 and
        # Telegram redelivers, instead of ACKing 200 and dropping the reply.
        transport = FakeTransport([RuntimeError("down")] * srd._SEND_MAX_ATTEMPTS)
        with patch.object(srd.asyncio, "sleep", new_callable=AsyncMock):
            delivered = asyncio.run(srd._send_one_chunk(transport, "hello"))
        self.assertFalse(delivered)
        self.assertEqual(transport.calls, srd._SEND_MAX_ATTEMPTS)

    def test_first_attempt_success_no_retry(self):
        transport = FakeTransport([True])
        with patch.object(srd.asyncio, "sleep", new_callable=AsyncMock) as slept:
            delivered = asyncio.run(srd._send_one_chunk(transport, "hello"))
        self.assertTrue(delivered)
        self.assertEqual(transport.calls, 1)
        slept.assert_not_called()  # no backoff on the happy path


if __name__ == "__main__":
    unittest.main()
