"""Tests for kill-switch enforcement on personal channel paths.

RETARGETED 2026-08-15. Four tests here drove
`_handle_{whatsapp,telegram}_gateway_channel_inbound` and
`send_{whatsapp,telegram}_personal_message`, all four deleted by commit
6b2baf97e (the full OpenClaw cutover, 2026-08-14). Every one of those
channels now enters through `_handle_local_bridge_gateway_channel_inbound`
and leaves through `send_local_bridge_personal_message`, both of which carry
the same `assert_not_killed` first line, so the coverage moves onto the live
functions and --- driven off the registry rather than one hand-picked channel
--- covers all five cut-over channels instead of two.

FINDING, reported rather than papered over: two further tests here,
`test_kill_switch_blocks_configure_{whatsapp,telegram}_personal_gateway`,
are DELETED with no replacement, because the capability they guarded no
longer has one. Their own comment records why they were written: "an active
kill switch blocked sending through an already-paired channel but did nothing
to stop pairing a brand new one", fixed by adding assert_not_killed() as
configure_*_personal_gateway's first line. The cutover deleted both those
functions, and the paths that replaced them --- provision_openclaw_gateway and
the openclaw channel-credential setup service reached through
POST /personal-channels/openclaw/gateways/{id}/provision and
PUT  /personal-channels/openclaw/gateways/{id}/channels/{key}/credential ---
contain no kill_switch_gate reference at all. So the exact gap those tests
were written to close is open again on the replacement path. Fixing it is a
production change in modules outside this pass's scope; it is named here so
the next person finds it rather than re-discovering it.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from server_modules import (
    kill_switch_gate,
    openclaw_channel_registry,
    personal_channels_service,
    rust_runtime_kernel_client,
    safe_mode_service,
)


def _cut_over_channel_keys():
    """The five platforms the cutover moved onto the transport, from the
    registry's own id set rather than typed here."""
    return tuple(
        sorted(
            f"{openclaw_channel_registry.CHANNEL_KEY_PREFIX}{channel_id}"
            for channel_id in openclaw_channel_registry.OPENCLAW_CUT_OVER_CHANNEL_IDS
        )
    )


def _rust_kernel_allow(command, payload):
    """Real gateway.connect handshakes on production run against the real
    compiled Rust kernel binary, which returns next_action == operation for
    runtime-state-store-decision (proven live: the /emergency-stop route's
    set_kill_switch()/clear_kill_switch() calls work correctly in
    production). Locally there's no binary, so run_runtime_kernel fails
    closed for every command by default -- without this mock, EVERY test
    in this file fails in setUp() itself (clear_kill_switch raising
    KillSwitchRustGateError), which means a real regression in the
    kill-switch mechanism would be silently invisible here."""
    return {
        "ok": True,
        "decision": "allow",
        "next_action": payload.get("operation") if isinstance(payload, dict) else command,
    }


class PersonalChannelKillSwitchTests(unittest.TestCase):
    def setUp(self):
        self._rust_patcher = patch.object(
            rust_runtime_kernel_client, "run_runtime_kernel", side_effect=_rust_kernel_allow
        )
        self._rust_patcher.start()
        safe_mode_service.reset_state_for_tests()
        kill_switch_gate.clear_kill_switch(kill_switch_gate.GLOBAL_KILL_KEY)
        for gw in ("gw-kill-inbound", "gw-kill-bridge-inbound",
                   "gw-kill-bridge-send", "gw-kill-test", "any-gateway"):
            kill_switch_gate.clear_kill_switch(
                f"{kill_switch_gate.GATEWAY_KILL_PREFIX}{gw}"
            )
        # A real key, resolved from the registry. The kill-switch assertion is
        # the FIRST line of the function under test so it fires before the
        # lane contract is even consulted -- but a dead key here would still
        # be a test naming a channel nothing routes, which is precisely how
        # this file's WhatsApp/Telegram cases rotted.
        self.live_channel_key = _cut_over_channel_keys()[0]

    def tearDown(self):
        safe_mode_service.reset_state_for_tests()
        self._rust_patcher.stop()

    def _set_gateway_kill(self, gateway_id: str) -> None:
        kill_switch_gate.set_kill_switch(
            f"{kill_switch_gate.GATEWAY_KILL_PREFIX}{gateway_id}"
        )

    # ------------------------------------------------------------------
    # handle_gateway_channel_inbound (async)
    # ------------------------------------------------------------------

    def test_kill_switch_blocks_handle_gateway_channel_inbound(self):
        self._set_gateway_kill("gw-kill-inbound")
        with self.assertRaises(kill_switch_gate.KillSwitchBlockedError):
            asyncio.run(
                personal_channels_service.handle_gateway_channel_inbound(
                    gateway_id="gw-kill-inbound",
                    registration={},
                    payload={"channel_key": self.live_channel_key},
                )
            )

    def test_safe_mode_machine_kill_blocks_handle_gateway_channel_inbound(self):
        safe_mode_service.set_kill_switch(
            scope="machine",
            enabled=True,
            machine_id="gw-safe-mode-inbound",
            reason="machine incident",
        )
        with self.assertRaises(kill_switch_gate.KillSwitchBlockedError) as ctx:
            asyncio.run(
                personal_channels_service.handle_gateway_channel_inbound(
                    gateway_id="gw-safe-mode-inbound",
                    registration={},
                    payload={"channel_key": self.live_channel_key},
                )
            )
        self.assertEqual(ctx.exception.decision.reason, "safe_mode_machine_kill_active")

    # ------------------------------------------------------------------
    # _handle_local_bridge_gateway_channel_inbound (async) -- the ONE inbound
    # handler every cut-over channel (WhatsApp/Telegram/Signal/iMessage/
    # Weixin) now shares, replacing the two deleted per-channel handlers.
    # ------------------------------------------------------------------

    def test_kill_switch_blocks_local_bridge_gateway_inbound(self):
        self._set_gateway_kill("gw-kill-bridge-inbound")
        for channel_key in _cut_over_channel_keys():
            with self.subTest(channel_key=channel_key):
                spec = personal_channels_service.LOCAL_BRIDGE_PERSONAL_CHANNELS[channel_key]
                with self.assertRaises(kill_switch_gate.KillSwitchBlockedError):
                    asyncio.run(
                        personal_channels_service._handle_local_bridge_gateway_channel_inbound(
                            gateway_id="gw-kill-bridge-inbound",
                            registration={},
                            payload={"channel_key": channel_key},
                            channel_key=channel_key,
                            provider=str(spec["provider"]),
                            label=str(spec["label"]),
                        )
                    )

    # ------------------------------------------------------------------
    # send_local_bridge_personal_message (async) -- the ONE explicit send
    # path, replacing the two deleted per-channel senders.
    # ------------------------------------------------------------------

    def test_kill_switch_blocks_send_local_bridge_personal_message(self):
        self._set_gateway_kill("gw-kill-bridge-send")
        for channel_key in _cut_over_channel_keys():
            with self.subTest(channel_key=channel_key):
                spec = personal_channels_service.LOCAL_BRIDGE_PERSONAL_CHANNELS[channel_key]
                with self.assertRaises(kill_switch_gate.KillSwitchBlockedError):
                    asyncio.run(
                        personal_channels_service.send_local_bridge_personal_message(
                            gateway_id="gw-kill-bridge-send",
                            registration={},
                            channel_key=channel_key,
                            provider=str(spec["provider"]),
                            remote_jid="123456789",
                            text="hello",
                            idempotency_key=f"ik-{channel_key}",
                        )
                    )

    # test_kill_switch_blocks_configure_whatsapp_personal_gateway and
    # test_kill_switch_blocks_configure_telegram_personal_gateway DELETED
    # 2026-08-15 with NO replacement -- see this module's docstring for the
    # finding that goes with them: the channel-setup path that replaced
    # configure_*_personal_gateway carries no kill-switch gate at all, so
    # there is currently nothing to assert.

    # ------------------------------------------------------------------
    # Inactive kill switch allows normal path
    # ------------------------------------------------------------------

    def test_inactive_kill_switch_allows_handle_gateway_channel_inbound(self):
        decision = kill_switch_gate.evaluate_kill_switch(gateway_id="gw-kill-test")
        self.assertFalse(decision.blocked)

    def test_inactive_kill_switch_allows_assert_not_killed(self):
        kill_switch_gate.assert_not_killed(gateway_id="gw-kill-test")

    # ------------------------------------------------------------------
    # Global kill also works
    # ------------------------------------------------------------------

    def test_global_kill_blocks_personal_channels(self):
        kill_switch_gate.set_kill_switch(kill_switch_gate.GLOBAL_KILL_KEY)
        try:
            with self.assertRaises(kill_switch_gate.KillSwitchBlockedError):
                kill_switch_gate.assert_not_killed(gateway_id="any-gateway")
        finally:
            kill_switch_gate.clear_kill_switch(kill_switch_gate.GLOBAL_KILL_KEY)

    # ------------------------------------------------------------------
    # trace_id preserved
    # ------------------------------------------------------------------

    def test_kill_switch_blocked_decision_has_trace_id(self):
        decision = kill_switch_gate.evaluate_kill_switch(
            gateway_id="gw-kill-test",
            trace_id="trace-abc-123",
        )
        self.assertEqual(decision.trace_id, "trace-abc-123")


if __name__ == "__main__":
    unittest.main()
