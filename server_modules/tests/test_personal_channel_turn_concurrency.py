"""Two messages arriving close together on the SAME personal-channel thread
used to start two concurrent turns reading torn thread history.
dispatch_sage_reply already prevented this for hosted-bot/WeChat-official
channels via sage_reply_dispatcher._CHANNEL_TURN_LOCKS; personal channels
(WhatsApp, Telegram-personal, Discord DMs, and the whole local-bridge/
OpenClaw family) never had it — personal_channel_sage_bridge_service.py's
_execute_channel_turn_with_envelope called execute_sage_turn directly, with
no lock anywhere in the file.

_execute_channel_turn_with_envelope is the one chokepoint every personal-
channel reply crosses before calling execute_sage_turn (build_discord_
personal_reply_async and build_personal_channel_reply_async both funnel
through _build_unified_sage_personal_reply_async into it — the per-platform
WhatsApp and Telegram builders that used to sit beside them are deleted, see
the retarget notes below), so these tests prove the
fix at that one seam rather than per public entry point.

Deliberately per-thread, not global: MAN-318 measured 8 fully concurrent
turns running cleanly on a 1vCPU box, and the founder's own instruction on
this fix was NOT to serialize tool dispatch across a whole box/workspace —
only to close the specific torn-history bug on one thread. A global lock
here would silently undo that.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import openclaw_channel_registry, personal_channel_sage_bridge_service
from server_modules.agent_turn_runtime_contract import SageTurnResult

# RETARGETED 2026-08-15 from build_whatsapp_personal_reply_async, deleted that
# day: it hardcoded `whatsapp_personal`, a key the OpenClaw cutover removed and
# nothing can produce. WhatsApp's live key comes from the registry, so a
# rename fails here instead of leaving this serialization proof pointed at a
# channel nothing routes.
_WHATSAPP_CHANNEL_KEY = f"{openclaw_channel_registry.CHANNEL_KEY_PREFIX}whatsapp"

# SAME RETARGET, SAME DAY, for build_telegram_personal_reply_async — deleted
# with the cloud-session lane, the only thing that still produced
# `telegram_personal`. The serialization proof itself is unchanged: it is a
# property of _execute_channel_turn_with_envelope's per-thread lock, not of
# any one channel, so it is now asserted on the key a real Telegram message
# can actually arrive under.
_TELEGRAM_CHANNEL_KEY = f"{openclaw_channel_registry.CHANNEL_KEY_PREFIX}telegram"


def _telegram_reply(**kwargs):
    """The deleted per-platform builder's shape, on the live generic one."""
    kwargs.setdefault("fallback_label", "Telegram")
    return personal_channel_sage_bridge_service.build_personal_channel_reply_async(
        surface_channel=_TELEGRAM_CHANNEL_KEY, **kwargs
    )


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
                _telegram_reply(
                    workspace_id="workspace-1",
                    gateway_id="gateway-1",
                    remote_jid="tg-user-1",
                    text="first",
                    push_name="User",
                ),
                _telegram_reply(
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
                _telegram_reply(
                    workspace_id="workspace-1",
                    gateway_id="gateway-1",
                    remote_jid="tg-user-1",
                    text="a",
                    push_name="User",
                ),
                _telegram_reply(
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

    def test_two_entry_points_share_the_serialization_seam(self) -> None:
        # Proves the fix is at the shared chokepoint, not duplicated
        # per-channel. surface_channel IS part of the lock key, so two
        # different channels could never collide here anyway — what has to
        # be shown is that two turns on the SAME channel + workspace +
        # remote_jid funnel through one lock no matter which entry point
        # started them. It used to show that by driving WhatsApp's own sync
        # and async builders; both were deleted 2026-08-15 (see
        # _WHATSAPP_CHANNEL_KEY above), so it drives the generic builder
        # twice instead — the entry point production actually uses.
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
                personal_channel_sage_bridge_service.build_personal_channel_reply_async(
                    surface_channel=_WHATSAPP_CHANNEL_KEY,
                    workspace_id="workspace-1",
                    gateway_id="gateway-1",
                    remote_jid="15551234567",
                    text="a",
                    push_name="User",
                    fallback_label="WhatsApp",
                ),
                personal_channel_sage_bridge_service.build_personal_channel_reply_async(
                    surface_channel=_WHATSAPP_CHANNEL_KEY,
                    workspace_id="workspace-1",
                    gateway_id="gateway-1",
                    remote_jid="15551234567",
                    text="b",
                    push_name="User",
                    fallback_label="WhatsApp",
                ),
            )

        with patch("server_modules.sage_turn_adapter.execute_sage_turn", new=AsyncMock(side_effect=fake_turn)):
            asyncio.run(run_case())

        self.assertEqual(max_observed_concurrency, 1, "two WhatsApp turns for the same number ran concurrently")


if __name__ == "__main__":
    unittest.main()
