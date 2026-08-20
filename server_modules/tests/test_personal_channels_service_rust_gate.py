"""Rust-gate coverage for every personal-channel path that dispatches outbound.

The gate is `_enforce_personal_channel_dispatch_decision` ->
`rust_runtime_kernel_client.run_runtime_kernel_enforced("gateway-service-decision")`.
It must raise BEFORE `gateway_protocol_service.dispatch_channel_outbound` is
awaited whenever the kernel answers with anything other than
`dispatch_gateway_operation`. Every test below proves exactly that shape:
the gate raises ValueError, and the dispatch mock is never awaited.

REWRITTEN 2026-08-15. This file previously carried seven tests against the
first-party WhatsApp/Telegram surface (`configure_*_personal_gateway`,
`send_*_personal_message`, `_deliver_whatsapp_personal_reply`) which commit
6b2baf97e — the full OpenClaw cutover, 2026-08-14 — deleted along with the
Baileys/gramjs gateway runtimes they served. Those tests failed with
AttributeError from the moment that commit landed; they were not fixable,
because the functions they named are gone and are not coming back. One of
them, `_deliver_telegram_personal_reply`, had NEVER existed at all, so
Telegram's automatic-reply gate had zero working coverage for its whole life.

WhatsApp/Telegram/Signal/iMessage/Weixin inbound now all flow through the SAME
`_deliver_local_bridge_personal_reply`, so the per-channel tests collapse into
one loop over the cut-over channel keys — this is the honest replacement for
the deleted WhatsApp case AND the first working coverage the Telegram case has
ever had. Channel keys come from the live
`LOCAL_BRIDGE_PERSONAL_CHANNELS` registry rather than being typed here: the
old tests hardcoded `signal_personal`, which the cutover deleted, and a
hand-written key is exactly what let this file rot without failing loudly on
the paths that do still exist.
"""

import asyncio
import unittest
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, patch

from server_modules import personal_channels_service


# The platforms OPENCLAW_CUT_OVER_CHANNEL_IDS moved onto the transport.
# Resolved against the live registry so a rename upstream fails HERE rather
# than silently testing a channel key nothing routes any more.
#
# telegram REMOVED 2026-08-20 (feat/seamless-telegram-setup): it is no
# longer cut over — see openclaw_channel_registry.py's own "CORRECTION,
# 2026-08-20" comment. openclaw_telegram is declared but no longer an
# active personal channel, so LOCAL_BRIDGE_PERSONAL_CHANNELS no longer
# carries it.
_CUT_OVER_CHANNEL_KEYS = (
    "openclaw_whatsapp",
    "openclaw_signal",
    "openclaw_imessage",
)

_REGISTRATION: Dict[str, Any] = {
    "gateway_id": "gw-1",
    "workspace_id": "ws-1",
    "tenant_id": "tenant-1",
    "device_trust_state": "trusted",
    "active_session_id": "sess-1",
}


def _wrong_action_decision(next_action: str) -> Dict[str, Any]:
    """A kernel answer that is `ok`/`allow` but NOT dispatch_gateway_operation.

    Deliberately a permissive-looking response: the point of the gate is that
    `decision == "allow"` is not enough on its own — only the exact
    `next_action` may dispatch.
    """
    return {
        "ok": True,
        "decision": "allow",
        "reason": "gateway_service_operation_allowed",
        "operation": "protocol_route",
        "next_action": next_action,
    }


class PersonalChannelsServiceRustGateTests(unittest.TestCase):
    def test_cut_over_channel_keys_are_all_live(self) -> None:
        """The keys this file drives must exist in the live registry.

        Without this, a cutover/rename makes every test below exercise a
        channel key nothing routes — which is precisely how the deleted
        `signal_personal` tests kept passing while covering nothing.
        """
        registry = personal_channels_service.LOCAL_BRIDGE_PERSONAL_CHANNELS
        for channel_key in _CUT_OVER_CHANNEL_KEYS:
            self.assertIn(channel_key, registry, f"{channel_key} is not a live personal channel")

    def test_personal_channel_dispatch_accepts_dispatch_gateway_operation(self) -> None:
        with patch(
            "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "gateway_service_operation_allowed",
                "operation": "protocol_route",
                "next_action": "dispatch_gateway_operation",
            },
        ) as rust_mock:
            decision = personal_channels_service._enforce_personal_channel_dispatch_decision(
                gateway_id="gw-1",
                registration=_REGISTRATION,
                capability_id="openclaw_whatsapp.send",
                request_id="send-1",
            )

        payload = rust_mock.call_args.args[1]
        self.assertEqual(payload["operation"], "protocol_route")
        self.assertEqual(payload["session_id"], "sess-1")
        self.assertEqual(payload["capability_id"], "openclaw_whatsapp.send")
        self.assertEqual(decision["next_action"], "dispatch_gateway_operation")

    def _assert_automatic_reply_blocked(self, *, channel_key: str, next_action: str) -> None:
        """Drive `_deliver_local_bridge_personal_reply` to its gate and stop there."""
        label = str(
            personal_channels_service.LOCAL_BRIDGE_PERSONAL_CHANNELS[channel_key].get("label") or channel_key
        )
        provider = str(
            personal_channels_service.LOCAL_BRIDGE_PERSONAL_CHANNELS[channel_key].get("provider") or "openclaw"
        )
        inbound = {"external_message_id": "msg-1", "remote_jid": "user-1"}
        outbound = {
            "status": "queued",
            "remote_jid": "user-1",
            "text": "auto-reply",
            "reply_to_external_message_id": "msg-1",
        }

        async def run_test() -> None:
            with (
                patch(
                    "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                    return_value=_wrong_action_decision(next_action),
                ),
                patch(
                    "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                    new=AsyncMock(side_effect=AssertionError("should not dispatch automatic reply")),
                    create=True,
                ) as dispatch_mock,
                patch(
                    "server_modules.personal_channels_service.personal_channels_repository.create_or_get_outbound_message",
                    return_value=(outbound, True),
                ),
                # Without this the call reaches the real reply builder, which
                # returns nothing under the test-suite provider guard; the
                # function then takes its "agent said nothing" early return
                # and never gets as far as the Rust gate this test is about.
                patch(
                    "server_modules.personal_channels_service.personal_channel_sage_bridge_service"
                    ".build_personal_channel_reply_async",
                    new=AsyncMock(return_value={"text": "auto-reply", "source": "test"}),
                ),
                # The shared slash-command waist runs first and must fall
                # through (a normal message is not a command) so the ordinary
                # reply path — the one carrying the gate under test — is what
                # actually executes.
                patch(
                    "server_modules.sage_command_dispatcher.dispatch_command",
                    new=AsyncMock(return_value=None),
                ),
            ):
                with self.assertRaises(ValueError) as raised:
                    await personal_channels_service._deliver_local_bridge_personal_reply(
                        gateway_id="gw-1",
                        registration=_REGISTRATION,
                        inbound=inbound,
                        channel_key=channel_key,
                        provider=provider,
                        label=label,
                        agent_id="agent-rust-gate",
                        remote_jid="user-1",
                        external_message_id="msg-1",
                        text="hello",
                        push_name=None,
                        duplicate=False,
                    )

            self.assertIn("unexpected next_action", str(raised.exception))
            dispatch_mock.assert_not_awaited()

        asyncio.run(run_test())

    def test_automatic_reply_blocks_wrong_rust_action_before_dispatch(self) -> None:
        """Every cut-over channel's automatic reply is gated, not just one.

        WhatsApp and Telegram are the two that used to have their own
        (deleted / never-written) delivery functions; they are covered here
        through the single path that now actually serves them.
        """
        # Both non-dispatch actions the kernel can legitimately return, so a
        # future gate that special-cased one of them cannot pass this.
        for next_action in ("allow_gateway_service_operation", "request_gateway_owner_approval"):
            for channel_key in _CUT_OVER_CHANNEL_KEYS:
                with self.subTest(channel_key=channel_key, next_action=next_action):
                    self._assert_automatic_reply_blocked(
                        channel_key=channel_key,
                        next_action=next_action,
                    )

    def test_local_bridge_personal_send_blocks_wrong_rust_action_before_dispatch(self) -> None:
        async def run_test() -> None:
            with (
                patch(
                    "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                    return_value=_wrong_action_decision("allow_gateway_service_operation"),
                ),
                patch(
                    "server_modules.personal_channels_service.personal_channels_repository.create_or_get_outbound_message",
                    return_value=({"status": "queued", "remote_jid": "jid", "text": "hello"}, True),
                ),
                patch(
                    "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                    new=AsyncMock(side_effect=AssertionError("should not dispatch local bridge send")),
                    create=True,
                ) as dispatch_mock,
            ):
                with self.assertRaises(ValueError) as raised:
                    await personal_channels_service.send_local_bridge_personal_message(
                        gateway_id="gw-1",
                        registration=_REGISTRATION,
                        channel_key="openclaw_signal",
                        provider="openclaw",
                        remote_jid="signal-user-1",
                        text="hello",
                        idempotency_key="send-1",
                    )

            self.assertIn("unexpected next_action", str(raised.exception))
            dispatch_mock.assert_not_awaited()

        asyncio.run(run_test())

    def test_shared_command_dispatch_blocks_wrong_rust_action_before_dispatch(self) -> None:
        """The slash-command waist is a dispatch path too, and had no coverage.

        `_dispatch_personal_channel_command` is the ONE place a recognised
        /command's reply is sent on every personal channel. It carries its own
        `_enforce_personal_channel_dispatch_decision` call, entirely separate
        from the ordinary-reply one above, and nothing asserted that gate held.
        """
        inbound = {"external_message_id": "msg-1", "remote_jid": "user-1"}

        async def run_test() -> None:
            with (
                patch(
                    "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                    return_value=_wrong_action_decision("request_gateway_owner_approval"),
                ),
                patch(
                    "server_modules.sage_command_dispatcher.dispatch_command",
                    new=AsyncMock(return_value="compacted."),
                ),
                patch(
                    "server_modules.personal_channels_service.personal_channels_repository.create_or_get_outbound_message",
                    return_value=({"status": "queued", "remote_jid": "user-1", "text": "compacted."}, True),
                ),
                patch(
                    "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                    new=AsyncMock(side_effect=AssertionError("should not dispatch command reply")),
                    create=True,
                ) as dispatch_mock,
            ):
                with self.assertRaises(ValueError) as raised:
                    await personal_channels_service._dispatch_personal_channel_command(
                        gateway_id="gw-1",
                        registration=_REGISTRATION,
                        inbound=inbound,
                        channel_key="openclaw_telegram",
                        provider="openclaw",
                        capability_id="openclaw_telegram.send",
                        agent_id="agent-rust-gate",
                        external_message_id="msg-1",
                        remote_jid="user-1",
                        text="/compact",
                        duplicate=False,
                    )

            self.assertIn("unexpected next_action", str(raised.exception))
            dispatch_mock.assert_not_awaited()

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
