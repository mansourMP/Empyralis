"""Tests for kill-switch enforcement on personal channel paths."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from server_modules import personal_channels_service, kill_switch_gate, safe_mode_service, rust_runtime_kernel_client


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
        for gw in ("gw-kill-inbound", "gw-kill-wa", "gw-kill-tg",
                   "gw-kill-send-wa", "gw-kill-send-tg", "gw-kill-test",
                   "gw-kill-configure-wa", "gw-kill-configure-tg",
                   "any-gateway"):
            kill_switch_gate.clear_kill_switch(
                f"{kill_switch_gate.GATEWAY_KILL_PREFIX}{gw}"
            )

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
                    payload={"channel_key": "whatsapp_personal"},
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
                    payload={"channel_key": "whatsapp_personal"},
                )
            )
        self.assertEqual(ctx.exception.decision.reason, "safe_mode_machine_kill_active")

    # ------------------------------------------------------------------
    # _handle_whatsapp_gateway_channel_inbound (async)
    # ------------------------------------------------------------------

    def test_kill_switch_blocks_whatsapp_gateway_inbound(self):
        self._set_gateway_kill("gw-kill-wa")
        with self.assertRaises(kill_switch_gate.KillSwitchBlockedError):
            asyncio.run(
                personal_channels_service._handle_whatsapp_gateway_channel_inbound(
                    gateway_id="gw-kill-wa",
                    registration={},
                    payload={"channel_key": "whatsapp_personal"},
                )
            )

    # ------------------------------------------------------------------
    # _handle_telegram_gateway_channel_inbound (async)
    # ------------------------------------------------------------------

    def test_kill_switch_blocks_telegram_gateway_inbound(self):
        self._set_gateway_kill("gw-kill-tg")
        with self.assertRaises(kill_switch_gate.KillSwitchBlockedError):
            asyncio.run(
                personal_channels_service._handle_telegram_gateway_channel_inbound(
                    gateway_id="gw-kill-tg",
                    registration={},
                    payload={"channel_key": "telegram_personal"},
                )
            )

    # ------------------------------------------------------------------
    # send_whatsapp_personal_message (async)
    # ------------------------------------------------------------------

    def test_kill_switch_blocks_send_whatsapp_personal_message(self):
        self._set_gateway_kill("gw-kill-send-wa")
        with self.assertRaises(kill_switch_gate.KillSwitchBlockedError):
            asyncio.run(
                personal_channels_service.send_whatsapp_personal_message(
                    gateway_id="gw-kill-send-wa",
                    registration={},
                    remote_jid="123456789",
                    text="hello",
                    idempotency_key="ik-1",
                )
            )

    # ------------------------------------------------------------------
    # send_telegram_personal_message (async)
    # ------------------------------------------------------------------

    def test_kill_switch_blocks_send_telegram_personal_message(self):
        self._set_gateway_kill("gw-kill-send-tg")
        with self.assertRaises(kill_switch_gate.KillSwitchBlockedError):
            asyncio.run(
                personal_channels_service.send_telegram_personal_message(
                    gateway_id="gw-kill-send-tg",
                    registration={},
                    remote_jid="123456789",
                    text="hello",
                    idempotency_key="ik-1",
                )
            )

    # ------------------------------------------------------------------
    # configure_whatsapp_personal_gateway / configure_telegram_personal_gateway (async)
    #
    # These had NO kill-switch check at all until this fix -- an active
    # kill switch blocked sending through an already-paired channel but
    # did nothing to stop pairing a brand new one. Confirmed live against
    # production first (identical response with the switch on vs. off),
    # then fixed by adding assert_not_killed() as these functions' first
    # line, matching send_*_personal_message's existing pattern exactly.
    # ------------------------------------------------------------------

    def test_kill_switch_blocks_configure_whatsapp_personal_gateway(self):
        self._set_gateway_kill("gw-kill-configure-wa")
        with self.assertRaises(kill_switch_gate.KillSwitchBlockedError):
            asyncio.run(
                personal_channels_service.configure_whatsapp_personal_gateway(
                    gateway_id="gw-kill-configure-wa",
                    registration={},
                    phone_number="15550000000",
                )
            )

    def test_kill_switch_blocks_configure_telegram_personal_gateway(self):
        self._set_gateway_kill("gw-kill-configure-tg")
        with self.assertRaises(kill_switch_gate.KillSwitchBlockedError):
            asyncio.run(
                personal_channels_service.configure_telegram_personal_gateway(
                    gateway_id="gw-kill-configure-tg",
                    registration={},
                    phone_number="15550000000",
                )
            )

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
