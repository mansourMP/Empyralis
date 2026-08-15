"""Canonical InboundEnvelope wiring — PERSONAL-channel family.

Covers the backend-safety task wiring server_modules/inbound_envelope.py's
canonical envelope into the Telegram-personal / WhatsApp / Signal / iMessage
inbound paths (personal_channel_sage_bridge_service.py,
personal_channels_service.py). The envelope core itself
(InboundEnvelope/render_envelope_header/envelope_allows_owner_commands) is
frozen and covered by its own tests — this file proves PERSONAL-CHANNEL
construction and threading, end to end where practical:

  (a) owner self-chat -> OWNER_SELF_CHAT + is_owner True
  (b) group message from a non-owner -> GROUP + is_owner False + chat title
  (c) the rendered header lands at the START of the message handed to
      handle_sage_chat
  (d) a "/" command from a group is NOT dispatched as a command, while the
      same "/" from owner self-chat IS
  (e) iMessage owner detection: the imsg gateway runtime never populates
      is_self_chat, so a False there is "unverified," not "verified not the
      owner" — rendered as tri-state None, never guessed True.

See docs/design/inbound-envelope-design.md and
docs/design/inbound-attribution-audit.md for the full picture this file
only tests one slice of.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from server_modules import agent_conversation_memory
from server_modules import personal_channel_sage_bridge_service as bridge
from server_modules.command_registry import ProcessedMessage
from server_modules.inbound_envelope import SurfaceKind


def _run(coro):
    return asyncio.run(coro)


# ─── (a) / (b): envelope construction from already-computed signals ──────


class PersonalChannelEnvelopeConstructionTests(unittest.TestCase):
    """_build_personal_channel_envelope builds the canonical envelope purely
    from signals the bridge already receives (is_owner/is_group/chat_label/
    push_name/remote_jid) — no new/guessed signal."""

    def test_owner_self_chat_is_owner_self_chat_surface(self) -> None:
        """(a) owner self-chat -> OWNER_SELF_CHAT + is_owner True."""
        envelope = bridge._build_personal_channel_envelope(
            surface_channel="telegram_personal",
            remote_jid="owner-tg-1",
            sender_id="owner-tg-1",
            push_name="Mansur",
            is_owner=True,
            is_group=False,
            chat_label=None,
        )
        self.assertEqual(envelope.surface, SurfaceKind.OWNER_SELF_CHAT)
        self.assertIs(envelope.sender.is_owner, True)
        self.assertEqual(envelope.sender.display_name, "Mansur")
        self.assertEqual(envelope.sender.id, "owner-tg-1")
        self.assertEqual(envelope.platform, "telegram_personal")
        self.assertIsNone(envelope.addressed)

    def test_group_message_from_non_owner_is_group_surface(self) -> None:
        """(b) group message from a non-owner -> GROUP + is_owner False +
        chat title. was_addressed=True here is the CALLER's real, resolved
        fact (personal_channels_service._enforce_group_policy's own result
        — see that function's docstring) — explicitly passed, not a
        hardcode this function makes on its own (see the next test)."""
        envelope = bridge._build_personal_channel_envelope(
            surface_channel="telegram_personal",
            remote_jid="-100555777",
            sender_id="aunt-nadia-id",
            push_name="Aunt Nadia",
            is_owner=False,
            is_group=True,
            chat_label="Family",
            was_addressed=True,
        )
        self.assertEqual(envelope.surface, SurfaceKind.GROUP)
        self.assertIs(envelope.sender.is_owner, False)
        self.assertEqual(envelope.sender.display_name, "Aunt Nadia")
        self.assertEqual(envelope.chat.title, "Family")
        self.assertEqual(envelope.chat.id, "-100555777")
        self.assertTrue(envelope.addressed)

    def test_group_message_addressed_is_the_callers_honest_fact_not_a_hardcode(self) -> None:
        """UPDATED 2026-07-23 (group_policy build): addressed used to be
        hardcoded True for every group message — safe ONLY because every
        group message that reached this function had already passed a
        hard, non-configurable mention/reply-to-Sage gate upstream (see
        _build_personal_channel_envelope's own docstring for the full
        history). That invariant no longer holds: requireMention now
        defaults OFF, so an unaddressed group message routinely reaches
        this function too. addressed must now be exactly whatever the
        caller passes as was_addressed — False when the caller resolved
        "not addressed", and None (not a false True) when the caller
        didn't resolve/pass a real fact at all, so the model is never told
        "you were addressed directly" for a message that was not."""
        not_addressed = bridge._build_personal_channel_envelope(
            surface_channel="telegram_personal",
            remote_jid="-100555777",
            sender_id="aunt-nadia-id",
            push_name="Aunt Nadia",
            is_owner=False,
            is_group=True,
            chat_label="Family",
            was_addressed=False,
        )
        self.assertIs(not_addressed.addressed, False)

        unresolved = bridge._build_personal_channel_envelope(
            surface_channel="telegram_personal",
            remote_jid="-100555777",
            sender_id="aunt-nadia-id",
            push_name="Aunt Nadia",
            is_owner=False,
            is_group=True,
            chat_label="Family",
            # was_addressed intentionally omitted — must default to None,
            # never True.
        )
        self.assertIsNone(unresolved.addressed)

    def test_owner_posting_inside_a_group_is_still_group_not_self_chat(self) -> None:
        """The owner being a member of a group must never collapse the
        surface back to OWNER_SELF_CHAT — a group is a shared space with
        other, non-owner participants (the "family group" bug's whole
        point)."""
        envelope = bridge._build_personal_channel_envelope(
            surface_channel="whatsapp_personal",
            remote_jid="120363-group@g.us",
            sender_id="owner-wa-1",
            push_name="Mansur",
            is_owner=True,
            is_group=True,
            chat_label="Family Group",
        )
        self.assertEqual(envelope.surface, SurfaceKind.GROUP)
        self.assertIs(envelope.sender.is_owner, True)
        self.assertEqual(envelope.chat.title, "Family Group")

    def test_stranger_dm_is_dm_surface(self) -> None:
        envelope = bridge._build_personal_channel_envelope(
            surface_channel="whatsapp_personal",
            remote_jid="15559990000",
            sender_id="",
            push_name="Rando",
            is_owner=False,
            is_group=False,
            chat_label=None,
        )
        self.assertEqual(envelope.surface, SurfaceKind.DM)
        self.assertIs(envelope.sender.is_owner, False)
        # sender_id omitted -> falls back to remote_jid (the 1:1 case, same JID).
        self.assertEqual(envelope.sender.id, "15559990000")


# ─── (e): iMessage owner detection ────────────────────────────────────────


class IMessageOwnerDetectionTests(unittest.TestCase):
    """The imsg gateway runtime (empyralis-gateway/src/channels/
    imsg-imessage-runtime.ts, handleInboundNotification) forwards only
    external_message_id, remote_jid, sender_jid, push_name, text,
    received_at, from_me, is_group, is_mentioned, is_reply_to_sage — it
    never sets is_self_chat. personal_channels_service._is_owner_message's
    ONLY owner signal for a local-bridge channel is
    message.get("is_self_chat") (there is no per-agent linked-identity
    table for iMessage the way there is for WhatsApp/Telegram), so
    dm_decision["is_owner"] is always False for iMessage today — and that
    False is not a verified "not the owner," it is "this channel could not
    tell." This is a TS-gateway-side gap a Python-only backend change
    cannot close; the fix here is honest tri-state rendering: is_owner=None
    (unverified) instead of a guessed/flattened False, scoped ONLY to
    imessage_personal so Signal/WhatsApp/Telegram (whose bridges DO compute
    is_self_chat correctly) keep their real verified False untouched.
    """

    def test_imessage_false_is_owner_renders_as_unverified_not_false(self) -> None:
        envelope = bridge._build_personal_channel_envelope(
            surface_channel="imessage_personal",
            remote_jid="+15551230000",
            sender_id="+15551230000",
            push_name="Mansur",
            is_owner=False,  # the only value dm_decision can produce today
            is_group=False,
            chat_label=None,
        )
        self.assertIsNone(envelope.sender.is_owner)
        # Never assumed to be a self-chat just because verification failed.
        self.assertEqual(envelope.surface, SurfaceKind.DM)

    def test_imessage_verified_self_chat_still_becomes_owner_self_chat(self) -> None:
        """Forward-compatible: _is_owner_message already returns True the
        moment message.get("is_self_chat") is True, unchanged by this task
        — the day the imsg bridge starts setting it for a genuine iMessage
        self-chat, this renders exactly like Signal/WhatsApp/Telegram
        already do today, with no further change needed here."""
        envelope = bridge._build_personal_channel_envelope(
            surface_channel="imessage_personal",
            remote_jid="+15551230000",
            sender_id="+15551230000",
            push_name="Mansur",
            is_owner=True,
            is_group=False,
            chat_label=None,
        )
        self.assertIs(envelope.sender.is_owner, True)
        self.assertEqual(envelope.surface, SurfaceKind.OWNER_SELF_CHAT)

    def test_signal_false_is_owner_stays_verified_false_not_none(self) -> None:
        """Contrast case: Signal's bridge (signal-cli-bridge.ts's
        mapSignalCliReceiveNotification, per the inbound-attribution audit)
        DOES compute is_self_chat correctly, so a Signal False is a real,
        verified "not the owner" signal and must stay False — the
        unverified-None softening is scoped to iMessage only."""
        envelope = bridge._build_personal_channel_envelope(
            surface_channel="signal_personal",
            remote_jid="+15559990000",
            sender_id="+15559990000",
            push_name="Stranger",
            is_owner=False,
            is_group=False,
            chat_label=None,
        )
        self.assertIs(envelope.sender.is_owner, False)
        self.assertEqual(envelope.surface, SurfaceKind.DM)


# ─── (c) / (d): end-to-end through execute_sage_turn ──────────────────────


class _EnvelopeEndToEndTestCase(unittest.TestCase):
    """Shared setup for tests that run the REAL execute_sage_turn (not
    execute_sage_turn_for_channel — see
    personal_channel_sage_bridge_service._execute_channel_turn_with_envelope's
    own docstring for why) through the actual public bridge entry points,
    proving the envelope this bridge constructs really reaches the frozen
    chokepoint and its gates — not just that the envelope module works in
    isolation."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._mem_patch = patch.object(
            agent_conversation_memory, "_CONVERSATIONS_ROOT", Path(self._tmpdir.name)
        )
        self._mem_patch.start()
        # De-risk the outer parts of execute_sage_turn (triage gate, tenant
        # resolution, thread resolution) from any real DB/network
        # dependency — none of this is what these tests are about, and all
        # three are already best-effort/fail-soft in execute_sage_turn
        # itself; pinning them here just keeps the test fast and
        # deterministic instead of exercising that fail-soft path for real.
        self._patches = [
            patch(
                "server_modules.agent_registry_repository.get_workspace_master_agent_install",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "server_modules.control_plane_repository.resolve_tenant_id_for_workspace",
                new=AsyncMock(return_value="default"),
            ),
            patch(
                "server_modules.sage_command_dispatcher.get_active_thread",
                new=AsyncMock(return_value="test-thread"),
            ),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        self._mem_patch.stop()
        self._tmpdir.cleanup()


_CLOUD_TELEGRAM_LANE_REGRESSION = """WAS RED 2026-08-15, FIXED THE SAME DAY. These two are the cloud-session
lane's only regression witnesses on the NON-OWNER branch — keep them
distinguishable from the owner-branch cases beside them.

`personal_channel_sage_bridge_service.build_telegram_personal_reply_async`
hands its hardcoded `telegram_personal` to
channel_lane_contract_service.guard_personal_gateway_inbound_message on its
non-owner branch only. The 2026-08-14 OpenClaw cutover removed that key from
PERSONAL_CHANNEL_SPECS while `cloud-session-manager/` went on sending it, so
the guard raised on every call. That builder is the only one
personal_channels_service.handle_cloud_channel_inbound calls, and that
handler's own comment records that is_owner "still always evaluates to False
today" on the cloud wire — so the broken branch was the one EVERY cloud
Telegram message took: turn failed, reply suppressed to empty, person got
silence with nothing anywhere saying why.

The owner-branch tests in this file passed throughout, which is exactly why
it was invisible. Fixed by declaring the cloud-session lane in the lane
contract (it is served by a live runtime, just not the deleted on-box one) —
see PERSONAL_CHANNEL_SPECS's own comment for why NOT `openclaw_telegram`.
"""


class EnvelopeHeaderReachesHandleSageChatTests(_EnvelopeEndToEndTestCase):
    def test_owner_self_chat_header_prepended_before_handle_sage_chat(self) -> None:
        """(c) the rendered header actually lands at the START of the
        message handed to handle_sage_chat."""
        with patch(
            "server_modules.sage_agent_runtime_service.handle_sage_chat",
            new=AsyncMock(return_value={"message": "sure thing"}),
        ) as handle_mock:
            result = _run(
                bridge.build_telegram_personal_reply_async(
                    workspace_id="workspace-envelope-1",
                    gateway_id="gateway-1",
                    remote_jid="owner-tg-1",
                    text="remind me to call mom",
                    push_name="Mansur",
                    sender_id="owner-tg-1",
                    is_owner=True,
                )
            )

        self.assertEqual(result["text"], "sure thing")
        handle_mock.assert_called_once()
        sent_message = handle_mock.call_args.kwargs["message"]
        self.assertTrue(sent_message.startswith("[Telegram"), sent_message)
        self.assertIn("your owner Mansur", sent_message)
        self.assertIn("talking to you directly", sent_message)
        self.assertIn("remind me to call mom", sent_message)
        # No more duplicate ad-hoc "From: ... (owner) · Telegram · direct
        # message" prose prefix underneath the header — the envelope header
        # is now the ONLY place this fact is stated.
        self.assertNotIn("(owner) ·", sent_message)

    def test_group_message_header_states_group_and_not_owner(self) -> None:
        __doc__ = _CLOUD_TELEGRAM_LANE_REGRESSION  # noqa: F841
        with patch(
            "server_modules.sage_agent_runtime_service.handle_sage_chat",
            new=AsyncMock(return_value={"message": "'Posle' means 'later'."}),
        ) as handle_mock:
            result = _run(
                bridge.build_telegram_personal_reply_async(
                    workspace_id="workspace-envelope-2",
                    gateway_id="gateway-1",
                    remote_jid="-100555777",
                    text="Posle",
                    push_name="Aunt Nadia",
                    sender_id="aunt-nadia-id",
                    is_owner=False,
                    is_group=True,
                    chat_label="Family",
                )
            )

        self.assertEqual(result["text"], "'Posle' means 'later'.")
        sent_message = handle_mock.call_args.kwargs["message"]
        self.assertTrue(sent_message.startswith("[Telegram"), sent_message)
        self.assertIn('group "Family"', sent_message)
        self.assertIn("NOT your owner", sent_message)
        self.assertIn("Posle", sent_message)


class EnvelopeCommandGateTests(_EnvelopeEndToEndTestCase):
    """(d) a "/" command from a group is NOT dispatched as a command, while
    the same "/" from owner self-chat IS — enforced in CODE
    (envelope_allows_owner_commands), not model reasoning."""

    def test_group_slash_command_is_not_dispatched_as_a_command(self) -> None:
        __doc__ = _CLOUD_TELEGRAM_LANE_REGRESSION  # noqa: F841
        with (
            patch(
                "server_modules.command_registry.process_message", new=AsyncMock()
            ) as proc_mock,
            patch(
                "server_modules.command_registry.dispatch", new=AsyncMock()
            ) as dispatch_mock,
            patch(
                "server_modules.sage_agent_runtime_service.handle_sage_chat",
                new=AsyncMock(return_value={"message": "noted"}),
            ) as handle_mock,
        ):
            _run(
                bridge.build_telegram_personal_reply_async(
                    workspace_id="workspace-envelope-3",
                    gateway_id="gateway-1",
                    remote_jid="-100555777",
                    text="/status",
                    push_name="Aunt Nadia",
                    sender_id="aunt-nadia-id",
                    is_owner=False,
                    is_group=True,
                    chat_label="Family",
                )
            )

        proc_mock.assert_not_called()
        dispatch_mock.assert_not_called()
        # A group can never be an owner-command surface — the "/" is passed
        # to the model as ordinary text, with its group header, like any
        # other message.
        handle_mock.assert_called_once()
        sent_message = handle_mock.call_args.kwargs["message"]
        self.assertIn("/status", sent_message)

    def test_owner_self_chat_slash_command_is_dispatched_as_a_command(self) -> None:
        with patch(
            "server_modules.command_registry.process_message",
            new=AsyncMock(
                return_value=ProcessedMessage(text="", replies=["OK."], is_command_only=True)
            ),
        ) as proc_mock:
            result = _run(
                bridge.build_telegram_personal_reply_async(
                    workspace_id="workspace-envelope-4",
                    gateway_id="gateway-1",
                    remote_jid="owner-tg-1",
                    text="/status",
                    push_name="Mansur",
                    sender_id="owner-tg-1",
                    is_owner=True,
                )
            )

        proc_mock.assert_called_once()
        self.assertEqual(result["text"], "OK.")


if __name__ == "__main__":
    unittest.main()
