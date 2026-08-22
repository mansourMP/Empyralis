"""Natural replies for the LIVE personal-channel path.

server_modules/personal_channels_service.py is the module actually wired to
the gateway inbound path: gateway_protocol_service imports it directly
(gateway_protocol_service.py:28) and routes every "channel.inbound" event
frame through personal_channels_service.handle_gateway_channel_inbound
(gateway_protocol_service.py:2133). Its auto-reply dispatch site used to pass
reply_to_external_message_id=external_message_id unconditionally to
gateway_protocol_service.dispatch_channel_outbound, forcing every automatic
reply to render as a formal "reply-to-THIS-message" quote bubble on
Telegram/WhatsApp (the founder's #1 channel complaint; OpenClaw, the
reference, defaults reply-to threading OFF for these channels).

These tests prove the live auto-reply dispatch site now sends
reply_to_external_message_id=None, and that the explicit "reply to X" send
path is untouched and still forwards a caller-supplied id. The local DB
causality/audit bookkeeping (create_or_get_outbound_message's own
reply_to_external_message_id) is left recording the triggering id --- it does
not affect what is rendered over the wire --- and is deliberately not
asserted on here.

RETARGETED 2026-08-15. This file used to drive THREE auto-reply sites:
`_deliver_whatsapp_personal_reply`, `_handle_telegram_gateway_channel_inbound`
and `_deliver_local_bridge_personal_reply`, plus `send_whatsapp_personal_message`
for the explicit-send case. Commit 6b2baf97e --- the full OpenClaw cutover,
2026-08-14 --- deleted the first three of those four along with the Baileys /
gramjs gateway runtimes they served, so those tests raised AttributeError from
the moment it landed. WhatsApp, Telegram, Signal and iMessage now all deliver
through the SAME `_deliver_local_bridge_personal_reply`, so the three
per-channel tests collapse into one loop over the cut-over channels --- the
coverage survives the cutover and is now stronger than it was, since Signal
and iMessage were never covered here before at all.

Channel keys are DERIVED from `openclaw_channel_registry.OPENCLAW_CUT_OVER_
CHANNEL_IDS` and asserted against `personal_channels_service.LOCAL_BRIDGE_
PERSONAL_CHANNELS`, i.e. the expected set and the actual set come from two
different modules. The deleted version of the local-bridge test hardcoded
`signal_personal` --- a key the cutover removed --- and kept passing while
covering a channel nothing routes any more; a hand-typed key is exactly what
lets this file rot without failing loudly.
"""

import importlib
import tempfile
import unittest
from pathlib import Path
from typing import Tuple
from unittest.mock import AsyncMock, patch

from server_modules import (
    openclaw_channel_registry,
    personal_channels_service,
    personal_channels_repository,
)


def _cut_over_channel_keys() -> Tuple[str, ...]:
    """The channel keys of the five platforms the cutover moved onto the
    transport, built from the registry's own id set rather than typed here."""
    return tuple(
        sorted(
            f"{openclaw_channel_registry.CHANNEL_KEY_PREFIX}{channel_id}"
            for channel_id in openclaw_channel_registry.OPENCLAW_CUT_OVER_CHANNEL_IDS
        )
    )


class PersonalChannelsServiceNaturalReplyTests(unittest.IsolatedAsyncioTestCase):
    _ALLOW_DISPATCH_DECISION = {
        "ok": True,
        "decision": "allow",
        "reason": "gateway_service_operation_allowed",
        "operation": "protocol_route",
        "next_action": "dispatch_gateway_operation",
    }

    def setUp(self) -> None:
        global personal_channels_service, personal_channels_repository
        personal_channels_service = importlib.import_module("server_modules.personal_channels_service")
        personal_channels_repository = importlib.import_module("server_modules.personal_channels_repository")

        self.tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tmpdir.name) / "personal-channels.sqlite3"
        personal_channels_repository.init_personal_channels_db(db_path)
        self.db_patcher = patch.object(personal_channels_repository, "PERSONAL_CHANNELS_DB_FILE", db_path)
        self.db_patcher.start()

        # workspace_id="default" so the delivery path skips its
        # control_plane_repository.get_workspace_by_id validation round-trip
        # (only runs for a non-"default" workspace); irrelevant to reply-to.
        self.registration = {
            "gateway_id": "gw-live-1",
            "workspace_id": "default",
            "tenant_id": "tenant-1",
            "device_trust_state": "trusted",
            "active_session_id": "sess-1",
        }

    def tearDown(self) -> None:
        self.db_patcher.stop()
        self.tmpdir.cleanup()

    def test_cut_over_channel_keys_are_all_live(self) -> None:
        """The keys the loops below drive must exist in the live registry.

        Two independent sources: the ids come from openclaw_channel_registry,
        the routing table from personal_channels_service. A rename on either
        side fails HERE rather than leaving every test below exercising a
        channel key nothing routes.
        """
        registry = personal_channels_service.LOCAL_BRIDGE_PERSONAL_CHANNELS
        keys = _cut_over_channel_keys()
        self.assertTrue(keys, "OPENCLAW_CUT_OVER_CHANNEL_IDS resolved to nothing")
        for channel_key in keys:
            self.assertIn(channel_key, registry, f"{channel_key} is not a live personal channel")

    async def test_auto_reply_dispatches_without_reply_to_id(self) -> None:
        """The one live automatic-reply site, across every cut-over channel.

        WhatsApp and Telegram used to have their own delivery functions and
        their own copy of this test; they are covered here through the single
        path that now actually serves them, alongside Signal/iMessage/Weixin,
        which this property was never asserted for before.
        """
        for channel_key in _cut_over_channel_keys():
            with self.subTest(channel_key=channel_key):
                spec = personal_channels_service.LOCAL_BRIDGE_PERSONAL_CHANNELS[channel_key]
                with (
                    patch(
                        "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                        return_value=self._ALLOW_DISPATCH_DECISION,
                    ),
                    # The shared slash-command waist runs first and must fall
                    # through (a normal message is not a command) so the
                    # ordinary reply path this test is about is what executes.
                    patch(
                        "server_modules.agent_command_dispatcher.dispatch_command",
                        new=AsyncMock(return_value=None),
                    ),
                    patch(
                        "server_modules.personal_channels_service.personal_channel_sage_bridge_service"
                        ".build_personal_channel_reply_async",
                        new=AsyncMock(return_value={"text": "Sure, on it.", "source": "sage"}),
                    ),
                    patch(
                        "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                        new=AsyncMock(return_value={"external_message_id": f"{channel_key}-out-1"}),
                        create=True,
                    ) as dispatch_mock,
                    patch("server_modules.personal_channels_service.security_audit_service.emit_security_audit_event"),
                ):
                    result = await personal_channels_service._deliver_local_bridge_personal_reply(
                        gateway_id="gw-live-1",
                        registration=self.registration,
                        inbound={
                            "external_message_id": f"{channel_key}-in-1",
                            "remote_jid": "contact-1",
                        },
                        remote_jid="contact-1",
                        external_message_id=f"{channel_key}-in-1",
                        text="hey are you around?",
                        push_name="Mansur",
                        duplicate=False,
                        channel_key=channel_key,
                        provider=str(spec["provider"]),
                        label=str(spec["label"]),
                        agent_id="agent-natural-reply",
                    )

                dispatch_mock.assert_awaited_once()
                self.assertIsNone(dispatch_mock.call_args.kwargs["reply_to_external_message_id"])
                self.assertEqual(result["outbound"]["status"], "delivered")

    async def test_explicit_send_still_forwards_caller_supplied_reply_to_id(self) -> None:
        """Scope guard-rail: send_local_bridge_personal_message is the explicit
        "reply to X" path (e.g. an approved agent tool call), not an
        auto-reply -- it must keep forwarding whatever id the caller passes.
        This fix only drops the *forced* id on the automatic-reply path.

        This is the surviving equivalent of the deleted
        send_whatsapp_personal_message case, and it is a strictly better place
        for the assertion: unlike that function, this one runs
        channel_lane_contract_service.assert_personal_gateway_channel on the
        key it is handed, so a dead channel key fails here instead of passing
        quietly.
        """
        for channel_key in _cut_over_channel_keys():
            with self.subTest(channel_key=channel_key):
                spec = personal_channels_service.LOCAL_BRIDGE_PERSONAL_CHANNELS[channel_key]
                with (
                    patch(
                        "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                        return_value=self._ALLOW_DISPATCH_DECISION,
                    ),
                    patch(
                        "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                        new=AsyncMock(return_value={"external_message_id": f"{channel_key}-out-2"}),
                        create=True,
                    ) as dispatch_mock,
                ):
                    await personal_channels_service.send_local_bridge_personal_message(
                        gateway_id="gw-live-1",
                        registration=self.registration,
                        channel_key=channel_key,
                        provider=str(spec["provider"]),
                        remote_jid="contact-1",
                        text="Replying to your earlier question",
                        idempotency_key=f"explicit-send-{channel_key}",
                        reply_to_external_message_id=f"{channel_key}-in-earlier",
                    )

                dispatch_mock.assert_awaited_once()
                self.assertEqual(
                    dispatch_mock.call_args.kwargs["reply_to_external_message_id"],
                    f"{channel_key}-in-earlier",
                )


if __name__ == "__main__":
    unittest.main()
