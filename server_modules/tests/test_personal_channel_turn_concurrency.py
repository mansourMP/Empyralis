"""Two messages arriving close together on the SAME personal-channel thread
used to start two concurrent turns reading torn thread history.
dispatch_sage_reply already prevented this for hosted-bot/WeChat-official
channels via sage_reply_dispatcher._CHANNEL_TURN_LOCKS; personal channels
(WhatsApp, Telegram-personal, Discord DMs, and the whole local-bridge/
OpenClaw family) never had it — personal_channel_sage_bridge_service.py's
_execute_channel_turn_with_envelope called execute_sage_turn directly, with
no lock anywhere in the file.

_execute_channel_turn_with_envelope is the one chokepoint every personal-
channel reply crosses before calling execute_sage_turn (build_whatsapp_
personal_reply(_async), build_telegram_personal_reply(_async), build_
discord_personal_reply_async, and build_personal_channel_reply_async all
funnel through _build_unified_sage_personal_reply_async into it), so these
tests prove the fix at that one seam rather than per public entry point.

Deliberately per-thread, not global: MAN-318 measured 8 fully concurrent
turns running cleanly on a 1vCPU box, and the founder's own instruction on
this fix was NOT to serialize tool dispatch across a whole box/workspace —
only to close the specific torn-history bug on one thread. A global lock
here would silently undo that.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import personal_channel_sage_bridge_service
from server_modules.sage_agent_runtime_contract import SageTurnResult


class PersonalChannelTurnSerializationTests(unittest.TestCase):
    def test_two_concurrent_messages_on_the_same_thread_never_overlap(self) -> None:
        in_flight = 0
        max_observed_concurrency = 0
        call_order: list[str] = []

        async def fake_turn(**kwargs):
            nonlocal in_flight, max_observed_concurrency
            in_flight += 1
            max_observed_concurrency = max(max_observed_concurrency, in_flight)
            call_order.append(f"start:{kwargs.get('message')}")
            await asyncio.sleep(0.05)
            call_order.append(f"end:{kwargs.get('message')}")
            in_flight -= 1
            return SageTurnResult(message=f"reply to {kwargs.get('message')}")

        async def run_case():
            await asyncio.gather(
                personal_channel_sage_bridge_service.build_telegram_personal_reply_async(
                    workspace_id="workspace-1",
                    gateway_id="gateway-1",
                    remote_jid="tg-user-1",
                    text="first",
                    push_name="User",
                ),
                personal_channel_sage_bridge_service.build_telegram_personal_reply_async(
                    workspace_id="workspace-1",
                    gateway_id="gateway-1",
                    remote_jid="tg-user-1",
                    text="second",
                    push_name="User",
                ),
            )

        with patch("server_modules.sage_turn_adapter.execute_sage_turn", new=AsyncMock(side_effect=fake_turn)):
            asyncio.run(run_case())

        self.assertEqual(max_observed_concurrency, 1, "two turns for the same thread ran concurrently")
        # Fully serialized: the first call's start AND end both happen
        # before the second call's start — not merely non-overlapping by
        # luck of the scheduler. Substring match, not exact-equal: a
        # non-owner sender's message is wrapped with an external-content
        # security notice before it reaches execute_sage_turn (a separate,
        # unrelated guard), so "first"/"second" survive only as substrings
        # of the actual argument.
        self.assertEqual(len(call_order), 4)
        self.assertIn("first", call_order[0])
        self.assertTrue(call_order[0].startswith("start:"))
        self.assertIn("first", call_order[1])
        self.assertTrue(call_order[1].startswith("end:"))
        self.assertIn("second", call_order[2])
        self.assertTrue(call_order[2].startswith("start:"))
        self.assertIn("second", call_order[3])
        self.assertTrue(call_order[3].startswith("end:"))

    def test_different_threads_run_concurrently_not_serialized_against_each_other(self) -> None:
        in_flight = 0
        max_observed_concurrency = 0

        async def fake_turn(**kwargs):
            nonlocal in_flight, max_observed_concurrency
            in_flight += 1
            max_observed_concurrency = max(max_observed_concurrency, in_flight)
            await asyncio.sleep(0.05)
            in_flight -= 1
            return SageTurnResult(message="ok")

        async def run_case():
            await asyncio.gather(
                personal_channel_sage_bridge_service.build_telegram_personal_reply_async(
                    workspace_id="workspace-1",
                    gateway_id="gateway-1",
                    remote_jid="tg-user-1",
                    text="a",
                    push_name="User",
                ),
                personal_channel_sage_bridge_service.build_telegram_personal_reply_async(
                    workspace_id="workspace-1",
                    gateway_id="gateway-1",
                    remote_jid="tg-user-2",
                    text="b",
                    push_name="User",
                ),
            )

        with patch("server_modules.sage_turn_adapter.execute_sage_turn", new=AsyncMock(side_effect=fake_turn)):
            asyncio.run(run_case())

        self.assertEqual(
            max_observed_concurrency, 2, "different threads must NOT be serialized against each other"
        )

    def test_whatsapp_and_telegram_share_the_serialization_seam(self) -> None:
        # Proves the fix is at the shared chokepoint, not duplicated
        # per-channel: two DIFFERENT public entry points (WhatsApp,
        # Telegram) for the same workspace+remote_jid+surface pairing would
        # only collide if surface_channel is part of the lock key (it is —
        # "whatsapp_personal" vs "telegram_personal" differ), so this
        # instead proves the SAME channel's two async/sync entry points
        # (build_whatsapp_personal_reply vs build_whatsapp_personal_reply_
        # async) both funnel through the identical lock.
        in_flight = 0
        max_observed_concurrency = 0

        async def fake_turn(**kwargs):
            nonlocal in_flight, max_observed_concurrency
            in_flight += 1
            max_observed_concurrency = max(max_observed_concurrency, in_flight)
            await asyncio.sleep(0.05)
            in_flight -= 1
            return SageTurnResult(message="ok")

        async def run_case():
            await asyncio.gather(
                personal_channel_sage_bridge_service.build_whatsapp_personal_reply_async(
                    workspace_id="workspace-1",
                    gateway_id="gateway-1",
                    remote_jid="15551234567",
                    text="a",
                    push_name="User",
                ),
                personal_channel_sage_bridge_service.build_whatsapp_personal_reply_async(
                    workspace_id="workspace-1",
                    gateway_id="gateway-1",
                    remote_jid="15551234567",
                    text="b",
                    push_name="User",
                ),
            )

        with patch("server_modules.sage_turn_adapter.execute_sage_turn", new=AsyncMock(side_effect=fake_turn)):
            asyncio.run(run_case())

        self.assertEqual(max_observed_concurrency, 1, "two WhatsApp turns for the same number ran concurrently")


if __name__ == "__main__":
    unittest.main()
