"""Channel-silence-on-error — regression cover for the 2026-07-18 incident.

The agent was posting hardcoded error/status text ("something went wrong",
"busy", quota/entitlement denials, etc.) into users' personal channels,
including GROUP chats. The founder's rule is ABSOLUTE and non-negotiable:
no hardcoded status/error message may EVER be sent into a channel (DM or
group). On any turn error / quota denial / entitlement block / timeout, the
channel must receive NOTHING — the failure is logged and surfaced on the
dashboard/activity feed only.

This file proves that rule holds at every layer:
  1. platform_event.py — the suppression registry itself.
  2. channel_adapter.filter_channel_outbound_reply — the shared choke point.
  3. agent_reply_dispatcher.dispatch_sage_reply(_safe) — the Telegram-hosted /
     Discord / Slack dispatcher: a turn error, an empty turn, AND a status
     string smuggled in as a "successful" message must all produce ZERO
     transport.send_message calls. A raised exception (dispatch_sage_reply_safe)
     must also produce zero sends.
  4. personal_channel_sage_bridge_service._build_error_reply_dict — the
     personal-channel (WhatsApp/Telegram/Discord/Signal/iMessage/WeChat)
     bridge never returns a sendable "text" on turn failure, and never
     dispatches straight to the channel to get around that. (The specific
     bypass this once guarded, a direct dispatch_cloud_channel_outbound call,
     went with the cloud-session lane on 2026-08-15; the assertion now names
     the gateway dispatch every surviving channel really delivers through.)
  5. personal_channels_service.py delivery layer — defense-in-depth backstop
     even if a reply-dict producer somehow still returns suppressed text.
  6. Web chat (filter_outbound_reply, unmodified) remains permissive — the
     dashboard/API surface may still show the user what went wrong.

Positive controls are included throughout so this file can't pass by
suppressing everything indiscriminately.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import channel_adapter, channel_lane_contract_service, inbound_envelope
from server_modules import openclaw_channel_registry, personal_channels_service, platform_event
from server_modules import agent_reply_dispatcher as srd
from server_modules import personal_channel_sage_bridge_service as bridge
from server_modules.agent_turn_runtime_contract import SageTurnResult


# ─── 1. platform_event registry ─────────────────────────────────────────


class PlatformEventSuppressionRegistryTests(unittest.TestCase):
    def test_generic_error_is_suppressed(self) -> None:
        self.assertTrue(platform_event.is_channel_suppressed_text(platform_event.GENERIC_ERROR.channel_text))

    def test_guaranteed_fallback_is_suppressed(self) -> None:
        self.assertTrue(platform_event.is_channel_suppressed_text(platform_event.GUARANTEED_FALLBACK.channel_text))

    def test_quota_and_busy_events_are_suppressed(self) -> None:
        for event in (
            platform_event.THREAD_BUSY,
            platform_event.AGENT_LIMIT_EXCEEDED,
            platform_event.WORKSPACE_LIMIT_EXCEEDED,
            platform_event.WORKSPACE_RATE_LIMITED,
            platform_event.RUNTIME_CAP_EXCEEDED,
            platform_event.SYSTEM_BUSY_FALLBACK,
            platform_event.AI_LIMIT_REACHED,
            platform_event.SERVICE_RATE_LIMITED,
            platform_event.PROVIDER_UNREACHABLE,
            platform_event.NO_AI_PROVIDER,
            platform_event.SAGE_TURN_NO_REPLY_UNKNOWN,
        ):
            with self.subTest(code=event.code):
                self.assertTrue(platform_event.is_channel_suppressed_text(event.channel_text))

    def test_cli_subscription_failure_events_are_suppressed(self) -> None:
        """Explicitly named in the incident report: cli_subscription failure
        messages must never reach a channel."""
        for event in (
            platform_event.CLI_SUBSCRIPTION_NO_GATEWAY,
            platform_event.CLI_SUBSCRIPTION_GATEWAY_OFFLINE,
            platform_event.CLI_SUBSCRIPTION_TIMEOUT,
            platform_event.CLI_SUBSCRIPTION_CRASH,
        ):
            with self.subTest(code=event.code):
                self.assertTrue(platform_event.is_channel_suppressed_text(event.channel_text))

    def test_command_responses_are_not_suppressed(self) -> None:
        """Direct responses to an explicit user command (/compact, /new,
        /main, /memory, /help) are NOT failure notices — positive control
        proving the registry isn't just suppressing everything."""
        for event in (
            platform_event.SAGE_COMPACTED,
            platform_event.SAGE_COMPACT_NOT_NEEDED,
            platform_event.SAGE_NEW_SESSION,
            platform_event.SAGE_MAIN_RETURN,
            platform_event.SAGE_NO_MEMORIES,
            platform_event.SAGE_HELP,
        ):
            with self.subTest(code=event.code):
                self.assertFalse(platform_event.is_channel_suppressed_text(event.channel_text))

    def test_ordinary_reply_text_is_not_suppressed(self) -> None:
        self.assertFalse(platform_event.is_channel_suppressed_text("Sure, I booked that for 3pm."))

    def test_default_posture_is_deny_not_allow(self) -> None:
        """Every PlatformEvent is suppressed unless explicitly allowlisted —
        a new event added later is silent-by-default, not leak-by-default."""
        for event in platform_event._all_platform_events():
            if event.code in platform_event.CHANNEL_SAFE_CODES:
                continue
            with self.subTest(code=event.code):
                self.assertIn(event.channel_text.strip(), platform_event.CHANNEL_SUPPRESSED_TEXTS)


# ─── 2. channel_adapter — the shared choke point ────────────────────────


class FilterChannelOutboundReplyTests(unittest.TestCase):
    def test_silent_marker_suppressed(self) -> None:
        self.assertIsNone(channel_adapter.filter_channel_outbound_reply("[SILENT]"))
        self.assertIsNone(channel_adapter.filter_channel_outbound_reply("NO_REPLY"))

    def test_hardcoded_error_text_suppressed(self) -> None:
        self.assertIsNone(
            channel_adapter.filter_channel_outbound_reply(platform_event.GENERIC_ERROR.channel_text)
        )
        self.assertIsNone(
            channel_adapter.filter_channel_outbound_reply(platform_event.AI_LIMIT_REACHED.channel_text)
        )

    def test_none_and_empty_suppressed(self) -> None:
        self.assertIsNone(channel_adapter.filter_channel_outbound_reply(None))
        self.assertIsNone(channel_adapter.filter_channel_outbound_reply("   "))

    def test_real_reply_passes_through(self) -> None:
        self.assertEqual(
            channel_adapter.filter_channel_outbound_reply("Booked your 3pm."),
            "Booked your 3pm.",
        )

    def test_command_response_passes_through(self) -> None:
        self.assertEqual(
            channel_adapter.filter_channel_outbound_reply(platform_event.SAGE_HELP.channel_text),
            platform_event.SAGE_HELP.channel_text,
        )

    def test_web_chat_filter_is_unaffected_and_stays_permissive(self) -> None:
        """filter_outbound_reply() (used by web chat / dashboard) must keep
        letting error text through — only the channel-specific filter adds
        suppression. Web chat is explicitly exempt from the absolute rule."""
        self.assertEqual(
            channel_adapter.filter_outbound_reply(platform_event.GENERIC_ERROR.channel_text),
            platform_event.GENERIC_ERROR.channel_text,
        )


# ─── 3. agent_reply_dispatcher — Telegram-hosted / Discord / Slack ───────


class _FakeTransport:
    """Records every send_message call so tests can assert zero sends."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.supports_typing_indicator = False
        self.max_message_length = 4096

    def format_text(self, text: str) -> str:
        return text

    async def send_message(self, text: str, reply_to_id=None) -> bool:
        self.sent.append(text)
        return True

    async def start_typing(self) -> None:  # pragma: no cover - not exercised
        pass

    async def stop_typing(self) -> None:  # pragma: no cover - not exercised
        pass


def _no_op_directives(**kwargs):
    """Stand-in for command_registry.process_message: no directives, pass
    the message through untouched, nothing sent yet."""

    class _Proc:
        replies: list[str] = []
        is_command_only = False
        text = kwargs.get("text", "")

    return _Proc()


class DispatchSageReplyChannelSilenceTests(unittest.TestCase):
    """Test point 4 from the incident report: a failed/erroring turn in a
    CHANNEL context must produce ZERO channel send (assert no outbound)."""

    def _run(self, turn_result: SageTurnResult) -> _FakeTransport:
        transport = _FakeTransport()
        with (
            patch("server_modules.command_registry.process_message", new=AsyncMock(side_effect=_no_op_directives)),
            patch("server_modules.agent_turn_adapter.execute_sage_turn", new=AsyncMock(return_value=turn_result)),
        ):
            asyncio.run(
                srd.dispatch_sage_reply(
                    transport=transport,
                    workspace_id="ws-1",
                    message="hello",
                    channel_origin="telegram_hosted",
                )
            )
        return transport

    def test_turn_error_produces_zero_channel_send(self) -> None:
        """result.error set, result.message empty — the classic quota/
        provider-failure shape. No hardcoded error text may reach the
        channel."""
        transport = self._run(SageTurnResult(message="", error="provider_rate_limited: 429 too many requests"))
        self.assertEqual(transport.sent, [], "channel must receive nothing on a turn error")

    def test_status_text_smuggled_as_successful_message_produces_zero_channel_send(self) -> None:
        """The actual live bug: handle_sage_chat's final return always sets
        error=None, so a cli_subscription failure / SAGE_TURN_NO_REPLY_UNKNOWN /
        GENERIC_ERROR string can arrive as a "successful" message with no
        error attached. Must still be suppressed."""
        transport = self._run(
            SageTurnResult(message=platform_event.CLI_SUBSCRIPTION_TIMEOUT.channel_text, error=None)
        )
        self.assertEqual(transport.sent, [], "a status string in .message must never reach the channel")

    def test_empty_turn_with_no_error_stays_silent_no_guaranteed_fallback_text(self) -> None:
        """Old behavior: an empty/no-op turn sent GUARANTEED_FALLBACK
        ("no response could be produced...") into the channel. Must now be
        pure silence."""
        transport = self._run(SageTurnResult(message="", error=None))
        self.assertEqual(transport.sent, [], "an empty turn must not announce itself in the channel")

    def test_legitimate_reply_still_delivers(self) -> None:
        """Positive control: this file must not silence real replies too."""
        transport = self._run(SageTurnResult(message="Sure, done!", error=None))
        self.assertEqual(transport.sent, ["Sure, done!"])

    def test_dispatch_sage_reply_safe_swallows_exception_with_zero_channel_send(self) -> None:
        """dispatch_sage_reply_safe used to have a 'last resort' that sent
        classify_error(exc) straight into the channel on ANY unhandled
        exception. Must now send nothing and never raise."""
        transport = _FakeTransport()
        with (
            patch("server_modules.command_registry.process_message", new=AsyncMock(side_effect=_no_op_directives)),
            patch(
                "server_modules.agent_turn_adapter.execute_sage_turn",
                new=AsyncMock(side_effect=RuntimeError("cli_subscription generation timed out")),
            ),
        ):
            delivered = asyncio.run(
                srd.dispatch_sage_reply_safe(
                    transport=transport,
                    workspace_id="ws-1",
                    message="hello",
                    channel_origin="telegram_hosted",
                )
            )
        self.assertEqual(transport.sent, [], "an unhandled exception must never reach the channel")
        self.assertFalse(delivered)

    def test_group_channel_origin_gets_same_silence_as_dm(self) -> None:
        """The dispatcher does not special-case DM vs group — silence must
        hold for a group-originated turn exactly like a DM."""
        transport = self._run(SageTurnResult(message="", error="workspace_rate_limited"))
        self.assertEqual(transport.sent, [])


# ─── 4. personal_channel_sage_bridge_service — WhatsApp/Telegram/Discord/local-bridge ──


class BridgeServiceNeverReturnsSendableErrorTextTests(unittest.TestCase):
    def test_build_error_reply_dict_leaves_text_empty(self) -> None:
        result = bridge._build_error_reply_dict(RuntimeError("HTTP 429 rate limit"), "ws-1")
        self.assertEqual(result["text"], "", "text is the ONLY field every delivery call site treats as sendable")
        self.assertIn("rate limited", result["error_text"].lower())

    def test_generic_builder_exception_never_bypasses_by_dispatching_directly(self) -> None:
        """RETARGETED TWICE, and both retargets are the same lesson.

        2026-08-15 (first): from build_whatsapp_personal_reply_async, deleted
        that day for hardcoding `whatsapp_personal` — a key the OpenClaw
        cutover removed and nothing can produce, so the assertion was being
        made about a channel no message can reach. Moved to
        build_personal_channel_reply_async with WhatsApp's live registry key.

        2026-08-15 (second): a SIBLING test drove
        build_telegram_personal_reply_async and patched
        personal_channels_service.dispatch_cloud_channel_outbound as the
        bypass it must never take. Both of those are gone with the
        cloud-session lane, so that test is folded into this one rather than
        left pointing at deleted symbols. The bypass surface is now the
        gateway dispatch every surviving personal channel actually delivers
        through, which is a strictly wider net than the cloud one was.

        Two assertions, both load-bearing:
          - "text" stays EMPTY, so a classified error can never be sent.
          - the result is NOT None. resolve_channel_reply_outcome(None) can
            only classify None as SILENT, so returning None on a crash
            records "the agent chose not to answer" for a turn that could not
            run. Never weaken assertIsNotNone to assertIsNone.
        """

        async def run_case():
            with (
                patch(
                    "server_modules.agent_turn_adapter.execute_sage_turn",
                    new=AsyncMock(side_effect=RuntimeError("boom")),
                ),
                patch(
                    "server_modules.gateway_protocol_service.dispatch_channel_outbound",
                    new=AsyncMock(side_effect=AssertionError("must never dispatch directly to the channel")),
                ) as direct_dispatch,
            ):
                result = await bridge.build_personal_channel_reply_async(
                    surface_channel=(
                        f"{openclaw_channel_registry.CHANNEL_KEY_PREFIX}whatsapp"
                    ),
                    workspace_id="ws-1",
                    gateway_id="gw-1",
                    remote_jid="15551234567",
                    text="hey",
                    fallback_label="WhatsApp",
                )
                direct_dispatch.assert_not_called()
                return result

        result = asyncio.run(run_case())
        self.assertIsNotNone(result)
        self.assertEqual(result["text"], "")

    def test_telegram_builder_exception_never_bypasses_by_dispatching_directly(self) -> None:
        """The Telegram half of the case above, on the live key.

        Its ancestor drove build_telegram_personal_reply_async against
        `telegram_personal`, the cloud-session lane's key. Both are deleted;
        `openclaw_telegram` is the whole Telegram surface now, so the same
        assertion is made where a real Telegram message can actually land."""

        async def run_case():
            with (
                patch(
                    "server_modules.agent_turn_adapter.execute_sage_turn",
                    new=AsyncMock(side_effect=RuntimeError("boom")),
                ),
                patch(
                    "server_modules.gateway_protocol_service.dispatch_channel_outbound",
                    new=AsyncMock(side_effect=AssertionError("must never dispatch directly to the channel")),
                ) as direct_dispatch,
            ):
                result = await bridge.build_personal_channel_reply_async(
                    surface_channel=(
                        f"{openclaw_channel_registry.CHANNEL_KEY_PREFIX}telegram"
                    ),
                    workspace_id="ws-1",
                    gateway_id="gw-1",
                    remote_jid="tg-user-1",
                    text="hey",
                    fallback_label="Telegram",
                )
                direct_dispatch.assert_not_called()
                return result

        result = asyncio.run(run_case())
        self.assertIsNotNone(result)
        self.assertEqual(result["text"], "")


# ─── 5. personal_channels_service — defense-in-depth delivery backstop ──


class PersonalChannelsServiceDeliveryBackstopTests(unittest.TestCase):
    """Even if a reply-dict producer regresses and returns a hardcoded
    status string under "text", the delivery layer itself must refuse to
    dispatch it — filter_channel_outbound_reply is applied a second time
    right before the outbound message is created."""

    def _allow_decision(self, operation: str):
        return {
            "ok": True,
            "decision": "allow",
            "reason": "gateway_service_operation_allowed",
            "operation": operation,
            "next_action": "dispatch_gateway_operation",
        }

    def test_delivery_refuses_suppressed_status_text(self) -> None:
        """RETARGETED 2026-08-15 from _deliver_whatsapp_personal_reply, which
        commit 6b2baf97e (the full OpenClaw cutover, 2026-08-14) deleted, onto
        _deliver_local_bridge_personal_reply -- the delivery function WhatsApp
        and every other personal channel now share. Driven for all five
        cut-over channels rather than WhatsApp alone; the channel keys come
        from the registry so a rename fails here instead of leaving this
        backstop asserted against a channel nothing routes."""
        from server_modules import personal_channels_service as pcs

        registration = {
            "gateway_id": "gw-1", "workspace_id": "default", "tenant_id": "tenant-1",
            "device_trust_state": "trusted", "active_session_id": "sess-1",
        }

        cut_over_keys = sorted(
            f"{openclaw_channel_registry.CHANNEL_KEY_PREFIX}{channel_id}"
            for channel_id in openclaw_channel_registry.OPENCLAW_CUT_OVER_CHANNEL_IDS
        )
        self.assertTrue(cut_over_keys)

        for channel_key in cut_over_keys:
            with self.subTest(channel_key=channel_key):
                spec = pcs.LOCAL_BRIDGE_PERSONAL_CHANNELS[channel_key]
                inbound = {"external_message_id": f"{channel_key}-msg-1", "remote_jid": "15551234567"}

                async def run_case():
                    with (
                        patch.object(
                            pcs.rust_runtime_kernel_client, "run_runtime_kernel_enforced",
                            return_value=self._allow_decision("protocol_route"),
                        ),
                        patch(
                            "server_modules.agent_command_dispatcher.dispatch_command",
                            new=AsyncMock(return_value=None),
                        ),
                        patch.object(
                            pcs.personal_channel_sage_bridge_service, "build_personal_channel_reply_async",
                            # Simulates a regression upstream: a hardcoded
                            # status string leaking into "text" as if it were
                            # a real reply.
                            new=AsyncMock(
                                return_value={"text": platform_event.GENERIC_ERROR.channel_text, "source": "test"}
                            ),
                        ),
                        patch.object(
                            pcs.personal_channels_repository, "mark_inbound_processed",
                            return_value=inbound,
                        ) as mark_processed,
                        patch.object(
                            pcs.personal_channels_repository, "create_or_get_outbound_message",
                            side_effect=AssertionError("must never queue a suppressed status string for delivery"),
                        ) as create_outbound,
                        patch.object(pcs.security_audit_service, "emit_security_audit_event"),
                    ):
                        result = await pcs._deliver_local_bridge_personal_reply(
                            gateway_id="gw-1",
                            registration=registration,
                            inbound=inbound,
                            remote_jid="15551234567",
                            external_message_id=inbound["external_message_id"],
                            text="hi",
                            push_name=None,
                            duplicate=False,
                            channel_key=channel_key,
                            provider=str(spec["provider"]),
                            label=str(spec["label"]),
                            agent_id="agent-backstop-1",
                        )
                    create_outbound.assert_not_called()
                    # STRENGTHENED (fix/silent-message-drop). This used to
                    # assert mark_inbound_processed WAS called -- i.e. that a
                    # status string smuggled in as a reply got recorded under
                    # the `<channel>:noreply:` marker, the durable "the agent
                    # was asked and DELIBERATELY said nothing" record and the
                    # only thing the redelivery guard reads. The turn plainly
                    # did not choose silence, so writing that marker recorded
                    # a message the platform failed to answer as answered AND
                    # cancelled the at-least-once retry that was its last
                    # chance. Suppression is unchanged (the text still never
                    # leaves); what changed is that the message is no longer
                    # thrown away with it. See
                    # test_channel_undelivered_not_silence.py.
                    mark_processed.assert_not_called()
                    return result

                result = asyncio.run(run_case())
                self.assertIsNone(result["outbound"])



# ─── 6. the cloud-session lane — DELETED 2026-08-15 ─────────────────────
#
# Two classes lived here, driving personal_channels_service.handle_cloud_
# channel_inbound end to end: that a CRASHED cloud turn reports `undelivered`
# rather than `no_reply`, and that a channel_key the lane does not serve is
# refused with a structured status instead of running a turn.
#
# The whole lane is gone (a second, cloud-hosted gramjs Telegram ACCOUNT
# runtime the 2026-08-14 cutover missed — see channel_lane_contract_service.
# PERSONAL_CHANNEL_SPECS), so there is no handler left to drive. Neither
# subject is dropped, both simply already live where the surviving handlers
# are:
#   undelivered-vs-silence  test_channel_undelivered_not_silence.py, at the
#                           resolver AND at the real local-bridge seam every
#                           OpenClaw channel delivers through.
#   suppression backstop    section 5 above, driven for every cut-over
#                           channel key including openclaw_telegram.


class OpenClawChannelsRenderAHumanPlatformLabelTests(unittest.TestCase):
    """The cutover moved the whole personal family onto `openclaw_*` keys and
    inbound_envelope._PLATFORM_LABELS had an entry for none of them, so the
    one-line header every model reads opened with "[openclaw_whatsapp ...]".
    Driven off the live registry, so a channel OpenClaw adds tomorrow is
    covered without an edit here — the expected set and the actual set come
    from different places."""

    def test_every_openclaw_channel_has_a_human_label(self) -> None:
        self.assertTrue(openclaw_channel_registry.CHANNELS)
        for channel in openclaw_channel_registry.CHANNELS:
            with self.subTest(channel_key=channel.channel_key):
                label = inbound_envelope.platform_label(channel.channel_key)
                self.assertNotEqual(
                    label, channel.channel_key,
                    "the raw transport key must never be what the model is shown",
                )
                self.assertEqual(label, channel.label)


if __name__ == "__main__":
    unittest.main()
