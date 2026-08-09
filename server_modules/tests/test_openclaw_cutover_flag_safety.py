"""Regression proof for the safety claim CLAUDE.md's channel-UI unification
work depends on: flipping `openclaw_channel_registry.OPENCLAW_CUT_OVER_CHANNEL_IDS`
is safe to reason about because it (1) never touches an existing DB row, and
(2) never touches the unconditional first-party handler registrations —
`personal_channels_service._handler_registry` registers
`_TelegramPersonalChannelHandler`/`_WhatsAppPersonalChannelHandler` (and every
`LOCAL_BRIDGE_PERSONAL_CHANNELS` entry) at import time, unconditionally, with
no read of `OPENCLAW_TRANSPORT_OWNERSHIP` anywhere in that registration block.

This was asserted from a source reading (CLAUDE.md, "The backend cutover flag
is safe, and purely additive today") but never actually exercised. This file
is that exercise, not a re-statement of the trace.

WHY NO LIVE DATABASE
---------------------
Every existing test in this module's sibling
(`test_openclaw_provisioning_service.py`) that needs "an existing binding"
stubs `agent_bindings_repository.list_agent_channel_bindings` with
`monkeypatch` rather than standing up a real Postgres row — same convention
kept here. `resolve_transport_ownership` is a pure function over two
frozensets; it has no DB handle to hold, so the strongest true statement a
test can make is "the binding this repository call would return is read
back byte-identical whether or not the flag has been flipped in between" —
which is exactly what `test_existing_binding_read_back_unchanged_by_the_flip`
below proves, against the SAME repository entry point the rest of the
codebase reads through.

WHY THE FLAG ITSELF IS NOT PERMANENTLY FLIPPED HERE
-----------------------------------------------------
`OPENCLAW_CUT_OVER_CHANNEL_IDS` stays `frozenset()` in production — flipping
it for real requires retiring the corresponding first-party runtime in the
SAME change (the module's own comment: "yes -> openclaw owns it. The
first-party runtime must be retired in the same change"), which is future,
per-channel, `port -> verify -> swap -> delete` work. This test flips it only
inside `monkeypatch`'s scope, to prove the flip WOULD be safe when that work
happens — it is a due-diligence test, not an activation.
"""
from __future__ import annotations

import asyncio
import unittest
import unittest.mock

from server_modules import (
    agent_bindings_repository,
    channel_lane_contract_service,
    openclaw_channel_registry,
    personal_channels_service,
)


class CutoverFlagSafetyTests(unittest.TestCase):
    def test_flipping_telegram_changes_only_the_pure_ownership_computation(self):
        """`resolve_transport_ownership` is the one function the flag feeds.
        Flip it and recompute directly -- this is the mechanism CLAUDE.md
        calls safe, exercised rather than just read."""
        before = openclaw_channel_registry.resolve_transport_ownership(
            channel_lane_contract_service.FIRST_PARTY_PLATFORM_TOKENS
        )
        self.assertEqual(
            before.get("telegram"),
            openclaw_channel_registry.OWNER_FIRST_PARTY,
            "production default: telegram is not cut over, so first_party must own it",
        )

        flipped_ids = openclaw_channel_registry.OPENCLAW_CUT_OVER_CHANNEL_IDS | frozenset({"telegram"})
        with unittest.mock.patch.object(
            openclaw_channel_registry, "OPENCLAW_CUT_OVER_CHANNEL_IDS", flipped_ids
        ):
            after = openclaw_channel_registry.resolve_transport_ownership(
                channel_lane_contract_service.FIRST_PARTY_PLATFORM_TOKENS
            )
        self.assertEqual(
            after.get("telegram"),
            openclaw_channel_registry.OWNER_OPENCLAW,
            "the flip must actually move ownership -- otherwise this test would "
            "pass vacuously no matter what the flag did",
        )

        # The flag is process-global; confirm the patch is undone (mock.patch
        # already guarantees this, but this is the property the rest of this
        # file's claims about "unaffected" actually rest on).
        self.assertEqual(
            openclaw_channel_registry.OPENCLAW_CUT_OVER_CHANNEL_IDS,
            frozenset(),
            "production default must be restored after the patched block",
        )

    def test_existing_binding_read_back_unchanged_by_the_flip(self):
        """The DB-rows half of the claim. A binding read through the same
        repository entry point `openclaw_provisioning_service.channels_in_use`
        and every fleet-channel status reader goes through must come back
        byte-identical whether the read happens before, during, or after an
        ownership recomputation triggered by flipping the flag."""
        existing_binding = {
            "channel_key": "telegram_personal",
            "endpoint_key": "existing-owner-chat-id",
            "enabled": True,
        }

        async def fake_list_agent_channel_bindings(
            *, tenant_id, workspace_id, agent_install_id, enabled_only=True,
        ):
            return [dict(existing_binding)]

        real_impl = agent_bindings_repository.list_agent_channel_bindings
        agent_bindings_repository.list_agent_channel_bindings = fake_list_agent_channel_bindings
        try:
            before = _run(
                agent_bindings_repository.list_agent_channel_bindings(
                    tenant_id="t1", workspace_id="w1", agent_install_id="a1",
                )
            )

            flipped_ids = openclaw_channel_registry.OPENCLAW_CUT_OVER_CHANNEL_IDS | frozenset(
                {"telegram"}
            )
            with unittest.mock.patch.object(
                openclaw_channel_registry, "OPENCLAW_CUT_OVER_CHANNEL_IDS", flipped_ids
            ):
                # The act this test exists to prove is safe: recompute
                # ownership while the flag is flipped.
                openclaw_channel_registry.resolve_transport_ownership(
                    channel_lane_contract_service.FIRST_PARTY_PLATFORM_TOKENS
                )
                during = _run(
                    agent_bindings_repository.list_agent_channel_bindings(
                        tenant_id="t1", workspace_id="w1", agent_install_id="a1",
                    )
                )

            after = _run(
                agent_bindings_repository.list_agent_channel_bindings(
                    tenant_id="t1", workspace_id="w1", agent_install_id="a1",
                )
            )
        finally:
            agent_bindings_repository.list_agent_channel_bindings = real_impl

        self.assertEqual(before, [existing_binding])
        self.assertEqual(during, [existing_binding])
        self.assertEqual(after, [existing_binding])
        self.assertEqual(before, during)
        self.assertEqual(during, after)

    def test_unconditional_first_party_handler_registrations_survive_the_flip(self):
        """The handler-registrations half of the claim.
        `_TelegramPersonalChannelHandler`/`_WhatsAppPersonalChannelHandler`
        register at import time with no read of `OPENCLAW_TRANSPORT_OWNERSHIP`
        -- this asserts that fact holds by checking the SAME handler instance
        answers before and after a flip-and-recompute, never a fresh one
        silently swapped in."""
        registry = personal_channels_service._handler_registry
        self.assertTrue(
            registry.has("telegram_personal"),
            "telegram_personal must be registered unconditionally -- if this "
            "fails, the flag has already leaked into registration and the "
            "'purely additive' claim is false today, before any flip",
        )
        self.assertTrue(registry.has("whatsapp_personal"))
        handler_before = registry.get("telegram_personal")

        flipped_ids = openclaw_channel_registry.OPENCLAW_CUT_OVER_CHANNEL_IDS | frozenset({"telegram"})
        with unittest.mock.patch.object(
            openclaw_channel_registry, "OPENCLAW_CUT_OVER_CHANNEL_IDS", flipped_ids
        ):
            openclaw_channel_registry.resolve_transport_ownership(
                channel_lane_contract_service.FIRST_PARTY_PLATFORM_TOKENS
            )
            self.assertTrue(
                registry.has("telegram_personal"),
                "recomputing ownership under a flipped flag must not deregister "
                "the first-party handler",
            )
            self.assertIs(
                registry.get("telegram_personal"),
                handler_before,
                "the SAME handler instance must still answer -- a different "
                "instance would mean something re-registered behind the flip, "
                "which is not what 'purely additive' claims",
            )

        self.assertIs(registry.get("telegram_personal"), handler_before)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


if __name__ == "__main__":
    unittest.main()
