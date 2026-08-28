"""Regression tests for the canonical InboundEnvelope wiring into the
HOSTED/BOT + CONSOLE channel family: Telegram hosted, WeChat Official,
Slack, and the web console.

Scope matches the wiring task: this module does NOT touch (and does not
test) the personal-channel bridge (personal_channel_sage_bridge_service.py /
personal_channels_service.py) — that is a separate agent's territory.

Five scenarios, one per test class:
  (a) WeChat: two distinct customers get two distinct, deterministic
      thread_ids — regression-proof for the cross-customer SQL-thread /
      turn-lock collapse the audit found (thread_id="sage-main" hardcoded
      for every sender).
  (b) Telegram hosted: envelope surface=DM, sender.is_owner=True (the
      pairing IS the owner check for this channel).
  (c) Slack: a non-DM channel message gets surface=GROUP, sender.is_owner
      is None (Slack has no owner concept — unverified fails closed), and
      the previously-computed-then-discarded channel_type signal now
      actually reaches execute_sage_turn.
  (d) Console: the live web-chat chokepoint (direct_chat_service's
      "UNIFIED ENTRY" producer, which agent_turn.py's /api/turn path
      ultimately calls) reconstructs the envelope agent_turn.py stashed in
      context_hints and forwards it to execute_sage_turn — surface=CONSOLE,
      sender.is_owner=True.
  (e) WeChat: a "/" message from a customer is never command-dispatched —
      envelope_allows_owner_commands is structurally False for an
      is_owner=False sender, so dispatch_command is never even called.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest import mock as mock_module
from unittest.mock import AsyncMock, patch

from server_modules.inbound_envelope import SurfaceKind
from server_modules.agent_command_dispatcher import agent_sender_thread_id


# ═══════════════════════════ WeChat Official ═══════════════════════════

_WECHAT_XML_TEMPLATE = (
    "<xml>"
    "<ToUserName><![CDATA[gh_official]]></ToUserName>"
    "<FromUserName><![CDATA[{openid}]]></FromUserName>"
    "<CreateTime>1700000000</CreateTime>"
    "<MsgType><![CDATA[text]]></MsgType>"
    "<Content><![CDATA[{content}]]></Content>"
    "<MsgId>{msg_id}</MsgId>"
    "</xml>"
)


def _wechat_body(*, openid: str, content: str, msg_id: str) -> str:
    return _WECHAT_XML_TEMPLATE.format(openid=openid, content=content, msg_id=msg_id)


def _authorized_pairing_service():
    """A channel_pairing_service stand-in whose authorize_channel_message
    always reports an existing link — used by tests below that exercise
    behavior DOWNSTREAM of Gate 1 (thread keying, envelope shape, command
    dispatch) and are not themselves testing the gate. Gate 1 itself (an
    unpaired sender never reaching the turn) is covered by
    test_sms_twilio_channel.py, test_wechat_official_service_cross_workspace_ownership.py,
    and the dedicated Slack/WeChat/SMS gate tests."""
    service = mock_module.MagicMock()
    service.authorize_channel_message.return_value = {
        "authorized": True,
        "status": "linked",
        "workspace_id": "ws-1",
    }
    return service


class WeChatHostedEnvelopeTests(unittest.IsolatedAsyncioTestCase):
    """(a) + envelope-shape coverage for wechat_official_service.py."""

    def setUp(self) -> None:
        import server_modules.wechat_official_service as wechat

        self.wechat = wechat
        self._binding = {"workspace_id": "ws-1"}
        self._binding_meta = {"account_kind": "official_account", "app_id": "wx-app-1", "agent_id": None}
        self._creds = {"app_secret": "s3cr3t", "verify_token": "tok"}
        self._resolved = (self._binding, self._binding_meta, "cred-1", self._creds)

    async def test_two_distinct_customers_get_two_distinct_deterministic_threads(self) -> None:
        """FIX regression: every WeChat customer used to collapse onto the
        literal shared thread_id="sage-main" (and its serialization lock).
        Each distinct OpenID must now get its own deterministic thread."""
        wechat = self.wechat
        captured_thread_ids: list[str] = []

        async def _fake_dispatch_sage_reply_safe(**kwargs):
            captured_thread_ids.append(kwargs.get("thread_id"))
            return True

        with patch.object(wechat, "_resolve_binding_and_credential", new=AsyncMock(return_value=self._resolved)), \
             patch.object(wechat, "verify_wechat_server_signature", return_value=True), \
             patch("server_modules.agent_command_dispatcher.dispatch_command", new=AsyncMock(return_value=None)), \
             patch("server_modules.agent_reply_dispatcher.dispatch_sage_reply_safe", new=_fake_dispatch_sage_reply_safe), \
             patch(
                 "server_modules.channel_pairing_service.get_channel_pairing_service",
                 return_value=_authorized_pairing_service(),
             ):
            await wechat.handle_inbound_callback(
                agent_install_id="agent-1", timestamp="1", nonce="n1", signature="s1",
                raw_body=_wechat_body(openid="cust-A", content="hi there", msg_id="m1"),
            )
            await wechat.handle_inbound_callback(
                agent_install_id="agent-1", timestamp="1", nonce="n2", signature="s2",
                raw_body=_wechat_body(openid="cust-B", content="hi there", msg_id="m2"),
            )

        self.assertEqual(len(captured_thread_ids), 2)
        thread_a, thread_b = captured_thread_ids
        self.assertNotEqual(thread_a, thread_b)
        # Neither customer collapses onto the old shared pointer.
        self.assertNotEqual(thread_a, "sage-main")
        self.assertNotEqual(thread_b, "sage-main")
        # Deterministic and matches the reused agent_sender_thread_id helper.
        self.assertEqual(thread_a, agent_sender_thread_id("agent-1", "cust-A"))
        self.assertEqual(thread_b, agent_sender_thread_id("agent-1", "cust-B"))

    async def test_same_customer_reuses_the_same_thread_across_messages(self) -> None:
        """The fix must not fragment ONE customer's own conversation either
        — same OpenID, same thread, every time."""
        wechat = self.wechat
        captured_thread_ids: list[str] = []

        async def _fake_dispatch_sage_reply_safe(**kwargs):
            captured_thread_ids.append(kwargs.get("thread_id"))
            return True

        with patch.object(wechat, "_resolve_binding_and_credential", new=AsyncMock(return_value=self._resolved)), \
             patch.object(wechat, "verify_wechat_server_signature", return_value=True), \
             patch("server_modules.agent_command_dispatcher.dispatch_command", new=AsyncMock(return_value=None)), \
             patch("server_modules.agent_reply_dispatcher.dispatch_sage_reply_safe", new=_fake_dispatch_sage_reply_safe), \
             patch(
                 "server_modules.channel_pairing_service.get_channel_pairing_service",
                 return_value=_authorized_pairing_service(),
             ):
            for i in range(3):
                await wechat.handle_inbound_callback(
                    agent_install_id="agent-1", timestamp="1", nonce=f"n{i}", signature="s",
                    raw_body=_wechat_body(openid="cust-A", content=f"message {i}", msg_id=f"m{i}"),
                )

        self.assertEqual(len(set(captured_thread_ids)), 1)

    async def test_envelope_shape_is_dm_customer_never_owner(self) -> None:
        wechat = self.wechat
        captured: dict = {}

        async def _fake_dispatch_sage_reply_safe(**kwargs):
            captured.update(kwargs)
            return True

        with patch.object(wechat, "_resolve_binding_and_credential", new=AsyncMock(return_value=self._resolved)), \
             patch.object(wechat, "verify_wechat_server_signature", return_value=True), \
             patch("server_modules.agent_command_dispatcher.dispatch_command", new=AsyncMock(return_value=None)), \
             patch("server_modules.agent_reply_dispatcher.dispatch_sage_reply_safe", new=_fake_dispatch_sage_reply_safe), \
             patch(
                 "server_modules.channel_pairing_service.get_channel_pairing_service",
                 return_value=_authorized_pairing_service(),
             ):
            await wechat.handle_inbound_callback(
                agent_install_id="agent-1", timestamp="1", nonce="n1", signature="s1",
                raw_body=_wechat_body(openid="cust-A", content="hello", msg_id="m1"),
            )

        envelope = captured.get("envelope")
        self.assertIsNotNone(envelope)
        self.assertEqual(envelope.platform, "wechat_official")
        self.assertEqual(envelope.surface, SurfaceKind.DM)
        self.assertEqual(envelope.sender.id, "cust-A")
        self.assertIs(envelope.sender.is_owner, False)

    async def test_slash_message_from_customer_is_not_command_dispatched(self) -> None:
        """(e) A WeChat customer typing "/new" must reach Sage as plain
        text, never the command registry — envelope_allows_owner_commands
        is structurally False for an unverified/non-owner sender, so this
        holds even though "/new" itself has access="all" in the registry."""
        wechat = self.wechat
        cmd_mock = AsyncMock(return_value="should never be used")
        reply_mock = AsyncMock(return_value=True)

        with patch.object(wechat, "_resolve_binding_and_credential", new=AsyncMock(return_value=self._resolved)), \
             patch.object(wechat, "verify_wechat_server_signature", return_value=True), \
             patch("server_modules.agent_command_dispatcher.dispatch_command", new=cmd_mock), \
             patch("server_modules.agent_reply_dispatcher.dispatch_sage_reply_safe", new=reply_mock), \
             patch(
                 "server_modules.channel_pairing_service.get_channel_pairing_service",
                 return_value=_authorized_pairing_service(),
             ):
            result = await wechat.handle_inbound_callback(
                agent_install_id="agent-1", timestamp="1", nonce="n1", signature="s1",
                raw_body=_wechat_body(openid="cust-A", content="/new", msg_id="m1"),
            )

        self.assertTrue(result.get("processed"))
        cmd_mock.assert_not_called()
        reply_mock.assert_awaited_once()
        self.assertEqual(reply_mock.await_args.kwargs.get("message"), "/new")


class WeChatOfficialGate1Tests(unittest.IsolatedAsyncioTestCase):
    """Gate 1 (THE ACTUAL FIX): handle_inbound_callback used to route every
    signature-verified sender straight to dispatch (the channel-gateway hardening
    §5a) — Tencent's callback contract is 1:1 with no groups, so signature
    verification alone was the entire authorization story. An unpaired
    OpenID must now get a pairing prompt back over WeChat and must NEVER
    reach dispatch_sage_reply_safe or dispatch_command (this channel's
    chokepoints into execute_sage_turn)."""

    def setUp(self) -> None:
        import server_modules.wechat_official_service as wechat

        self.wechat = wechat
        self._binding = {"workspace_id": "ws-1"}
        self._binding_meta = {"account_kind": "official_account", "app_id": "wx-app-1", "agent_id": None}
        self._creds = {"app_secret": "s3cr3t", "verify_token": "tok"}
        self._resolved = (self._binding, self._binding_meta, "cred-1", self._creds)

    async def test_unpaired_sender_never_reaches_dispatch(self) -> None:
        wechat = self.wechat
        cmd_mock = AsyncMock(return_value="should never be used")
        reply_mock = AsyncMock(return_value=True)
        send_mock = AsyncMock(return_value=True)

        pairing_service = mock_module.MagicMock()
        pairing_service.authorize_channel_message.return_value = {
            "authorized": False,
            "status": "pairing_required",
            "connect_url": "https://app.empyralis.test/continue?source=channel_connect&channel=wechat_official",
            "reply_text": "This WeChat identity is not linked to Empyralis yet. Open this link to connect it: https://app.empyralis.test/continue?source=channel_connect&channel=wechat_official",
        }

        with patch.object(wechat, "_resolve_binding_and_credential", new=AsyncMock(return_value=self._resolved)), \
             patch.object(wechat, "verify_wechat_server_signature", return_value=True), \
             patch("server_modules.agent_command_dispatcher.dispatch_command", new=cmd_mock), \
             patch("server_modules.agent_reply_dispatcher.dispatch_sage_reply_safe", new=reply_mock), \
             patch.object(wechat.WeChatOfficialTransport, "send_message", new=send_mock), \
             patch(
                 "server_modules.channel_pairing_service.get_channel_pairing_service",
                 return_value=pairing_service,
             ):
            result = await wechat.handle_inbound_callback(
                agent_install_id="agent-1", timestamp="1", nonce="n1", signature="s1",
                raw_body=_wechat_body(openid="stranger-1", content="hi who is this", msg_id="m1"),
            )

        self.assertFalse(result.get("processed"))
        self.assertEqual(result.get("reason"), "pairing_required")
        cmd_mock.assert_not_called()
        reply_mock.assert_not_awaited()
        send_mock.assert_awaited_once()
        self.assertIn("not linked to Empyralis", send_mock.await_args.args[0])
        pairing_service.authorize_channel_message.assert_called_once_with(
            provider="wechat_official",
            external_subject="stranger-1",
            workspace_id="ws-1",
            message_text="hi who is this",
        )

    async def test_paired_sender_reaches_dispatch(self) -> None:
        wechat = self.wechat
        reply_mock = AsyncMock(return_value=True)

        pairing_service = mock_module.MagicMock()
        pairing_service.authorize_channel_message.return_value = {
            "authorized": True,
            "status": "linked",
            "workspace_id": "ws-1",
        }

        with patch.object(wechat, "_resolve_binding_and_credential", new=AsyncMock(return_value=self._resolved)), \
             patch.object(wechat, "verify_wechat_server_signature", return_value=True), \
             patch("server_modules.agent_command_dispatcher.dispatch_command", new=AsyncMock(return_value=None)), \
             patch("server_modules.agent_reply_dispatcher.dispatch_sage_reply_safe", new=reply_mock), \
             patch(
                 "server_modules.channel_pairing_service.get_channel_pairing_service",
                 return_value=pairing_service,
             ):
            result = await wechat.handle_inbound_callback(
                agent_install_id="agent-1", timestamp="1", nonce="n1", signature="s1",
                raw_body=_wechat_body(openid="cust-known", content="hi again", msg_id="m1"),
            )

        self.assertTrue(result.get("processed"))
        reply_mock.assert_awaited_once()
        pairing_service.authorize_channel_message.assert_called_once_with(
            provider="wechat_official",
            external_subject="cust-known",
            workspace_id="ws-1",
            message_text="hi again",
        )


# ═══════════════════════════ Telegram hosted ═══════════════════════════


def _telegram_update(*, update_id: int, chat_id: int, chat_type: str, text: str, from_id: int,
                      message_id: int = 1, first_name: str = "Mansur") -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": message_id,
            "date": 1700000000,
            "chat": {"id": chat_id, "type": chat_type},
            "from": {"id": from_id, "first_name": first_name, "is_bot": False},
            "text": text,
        },
    }


class TelegramHostedEnvelopeTests(unittest.IsolatedAsyncioTestCase):
    """(b) hosted Telegram: envelope surface=DM, sender.is_owner=True."""

    def setUp(self) -> None:
        import server_modules.sage_telegram_hosted_service as hosted

        self.hosted = hosted
        self._pairs_backup = dict(hosted._SAGE_HOSTED_PAIRS)
        hosted._SAGE_HOSTED_PAIRS.clear()
        hosted._SAGE_HOSTED_PAIRS["555444"] = {
            "workspace_id": "ws-1", "paired_at": "2020-01-01T00:00:00+00:00",
        }
        self._persist_patch = patch.object(hosted, "_persist_after_mutation", lambda: None)
        self._persist_patch.start()

    def tearDown(self) -> None:
        self._persist_patch.stop()
        self.hosted._SAGE_HOSTED_PAIRS.clear()
        self.hosted._SAGE_HOSTED_PAIRS.update(self._pairs_backup)

    async def test_paired_dm_sender_is_verified_owner(self) -> None:
        hosted = self.hosted
        captured: dict = {}

        async def _fake_dispatch_sage_reply_safe(**kwargs):
            captured.update(kwargs)
            return True

        with patch("server_modules.agent_command_dispatcher.dispatch_command", new=AsyncMock(return_value=None)), \
             patch("server_modules.agent_reply_dispatcher.dispatch_sage_reply_safe", new=_fake_dispatch_sage_reply_safe):
            await hosted._process_update(
                _telegram_update(update_id=1, chat_id=555444, chat_type="private", text="hello", from_id=555444)
            )

        envelope = captured.get("envelope")
        self.assertIsNotNone(envelope)
        self.assertEqual(envelope.platform, "telegram_hosted")
        self.assertEqual(envelope.surface, SurfaceKind.DM)
        self.assertIs(envelope.sender.is_owner, True)
        self.assertEqual(envelope.sender.id, "555444")


# ═══════════════════════════ Slack ═══════════════════════════


class SlackEnvelopeTests(unittest.TestCase):
    """(c) Slack: channel_type/message_type, previously computed then
    discarded (agent_channel_router.py's own docstring names this bug),
    now actually reach execute_sage_turn as a real envelope."""

    def test_channel_message_is_group_surface_with_unverified_owner(self) -> None:
        from server_modules import connectors_actions

        with (
            patch("server_modules.connectors_actions._append_channel_event", return_value=None),
            patch("server_modules.connectors_actions.slack_verify_request_signature", return_value=True),
            patch(
                "server_modules.connectors_actions.slack_parse_inbound_event",
                return_value={
                    "kind": "event",
                    "event_id": "Ev999",
                    "message_type": "mention",
                    "channel_type": "channel",
                    "channel": "C123",
                    "user_id": "U456",
                    "text": "<@BOT> what's the inventory count",
                },
            ),
            patch(
                "server_modules.connectors_actions.load_vault",
                return_value={
                    "credentials": [
                        {
                            "id": "cred-slack",
                            "provider": "slack",
                            "workspace_id": "ws-1",
                            "tenant_id": "tenant-1",
                            "metadata": {"team_id": "T123"},
                        }
                    ]
                },
            ),
            patch(
                "server_modules.connectors_actions.resolve_vault_credential",
                return_value={"team_id": "T123", "bot_user_id": "BOT"},
            ),
            patch(
                "server_modules.agent_channel_router.route_inbound_channel_message",
                new=AsyncMock(return_value={"ok": True, "run_id": "run-1", "reply": ""}),
            ) as route_mock,
        ):
            from starlette.requests import Request

            async def _receive():
                return {"type": "http.request", "body": b"{}", "more_body": False}

            scope = {
                "type": "http",
                "http_version": "1.1",
                "method": "POST",
                "path": "/channels/slack/events",
                "raw_path": b"/channels/slack/events",
                "query_string": b"",
                "headers": [],
                "client": ("127.0.0.1", 54321),
                "server": ("127.0.0.1", 8001),
            }
            request = Request(scope, _receive)

            result = asyncio.run(connectors_actions.slack_events_webhook(request))

        self.assertEqual(result["handled"], 1)
        route_mock.assert_awaited_once()
        envelope = route_mock.await_args.kwargs.get("envelope")
        self.assertIsNotNone(envelope)
        self.assertEqual(envelope.platform, "slack")
        self.assertEqual(envelope.surface, SurfaceKind.GROUP)
        self.assertIsNone(envelope.sender.is_owner)
        self.assertEqual(envelope.sender.id, "U456")
        # Slack has no display-name resolution — the id is passed through,
        # never invented, per the audit's own finding.
        self.assertEqual(envelope.sender.display_name, "U456")
        self.assertTrue(envelope.addressed)

    def test_dm_channel_type_is_dm_surface(self) -> None:
        """Envelope-shape coverage for an ALREADY-PAIRED Slack DM sender —
        Gate 1 (channel_pairing_service.authorize_channel_message) is mocked
        authorized here so this test can keep asserting what it always
        asserted (surface/owner shape). Gate 1 itself — an unpaired Slack DM
        never reaching route_inbound_channel_message — is covered by
        SlackDmGate1Tests below."""
        from server_modules import connectors_actions

        pairing_service = mock_module.MagicMock()
        pairing_service.authorize_channel_message.return_value = {
            "authorized": True,
            "status": "linked",
            "workspace_id": "ws-1",
        }

        with (
            patch("server_modules.connectors_actions._append_channel_event", return_value=None),
            patch("server_modules.connectors_actions.slack_verify_request_signature", return_value=True),
            patch(
                "server_modules.connectors_actions.slack_parse_inbound_event",
                return_value={
                    "kind": "event",
                    "event_id": "Ev1000",
                    "message_type": "message",
                    "channel_type": "im",
                    "channel": "D999",
                    "user_id": "U789",
                    "text": "hey, quick question",
                },
            ),
            patch(
                "server_modules.connectors_actions.load_vault",
                return_value={
                    "credentials": [
                        {
                            "id": "cred-slack-2",
                            "provider": "slack",
                            "workspace_id": "ws-1",
                            "tenant_id": "tenant-1",
                            "metadata": {"team_id": "T999", "trigger_on_all_messages": True},
                        }
                    ]
                },
            ),
            patch(
                "server_modules.connectors_actions.resolve_vault_credential",
                return_value={"team_id": "T999", "bot_user_id": "BOT"},
            ),
            patch(
                "server_modules.channel_pairing_service.get_channel_pairing_service",
                return_value=pairing_service,
            ),
            patch(
                "server_modules.agent_channel_router.route_inbound_channel_message",
                new=AsyncMock(return_value={"ok": True, "run_id": "run-2", "reply": ""}),
            ) as route_mock,
        ):
            from starlette.requests import Request

            async def _receive():
                return {"type": "http.request", "body": b"{}", "more_body": False}

            scope = {
                "type": "http",
                "http_version": "1.1",
                "method": "POST",
                "path": "/channels/slack/events",
                "raw_path": b"/channels/slack/events",
                "query_string": b"",
                "headers": [],
                "client": ("127.0.0.1", 54321),
                "server": ("127.0.0.1", 8001),
            }
            request = Request(scope, _receive)

            asyncio.run(connectors_actions.slack_events_webhook(request))

        route_mock.assert_awaited_once()
        envelope = route_mock.await_args.kwargs.get("envelope")
        self.assertIsNotNone(envelope)
        self.assertEqual(envelope.surface, SurfaceKind.DM)
        self.assertIsNone(envelope.sender.is_owner)


class SlackDmGate1Tests(unittest.TestCase):
    """Gate 1 (THE ACTUAL FIX): slack_connector.should_trigger_agent_run's
    own comment says a DM "always triggers" — that was true structurally but
    never checked WHO was DMing the app. Before this change, any Slack user
    who opened a DM with the installed app got a full agent turn, no pairing,
    no allowlist (the channel-gateway hardening §5a). An unpaired DM sender must
    now get a pairing prompt back in the DM and must NEVER reach
    route_inbound_channel_message (Slack's chokepoint into
    execute_sage_turn). A non-DM (channel/group) message is untouched by
    this gate — see test_channel_message_is_group_surface_with_unverified_owner
    above, which exercises no pairing mock and still passes."""

    def _slack_webhook_request(self):
        from starlette.requests import Request

        async def _receive():
            return {"type": "http.request", "body": b"{}", "more_body": False}

        scope = {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "path": "/channels/slack/events",
            "raw_path": b"/channels/slack/events",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 54321),
            "server": ("127.0.0.1", 8001),
        }
        return Request(scope, _receive)

    def test_unpaired_dm_sender_never_reaches_route_inbound_channel_message(self) -> None:
        from server_modules import connectors_actions

        pairing_service = mock_module.MagicMock()
        pairing_service.authorize_channel_message.return_value = {
            "authorized": False,
            "status": "pairing_required",
            "connect_url": "https://app.empyralis.test/continue?source=channel_connect&channel=slack",
            "reply_text": "This Slack identity is not linked to Empyralis yet. Open this link to connect it: https://app.empyralis.test/continue?source=channel_connect&channel=slack",
        }
        send_message_mock = mock_module.MagicMock(return_value={"ok": True})

        with (
            patch("server_modules.connectors_actions._append_channel_event", return_value=None),
            patch("server_modules.connectors_actions.slack_verify_request_signature", return_value=True),
            patch(
                "server_modules.connectors_actions.slack_parse_inbound_event",
                return_value={
                    "kind": "event",
                    "event_id": "Ev2000",
                    "message_type": "message",
                    "channel_type": "im",
                    "channel": "D111",
                    "user_id": "U-STRANGER",
                    "text": "hi who is this",
                },
            ),
            patch(
                "server_modules.connectors_actions.load_vault",
                return_value={
                    "credentials": [
                        {
                            "id": "cred-slack-3",
                            "provider": "slack",
                            "workspace_id": "ws-1",
                            "tenant_id": "tenant-1",
                            "metadata": {"team_id": "T222", "trigger_on_all_messages": True},
                        }
                    ]
                },
            ),
            patch(
                "server_modules.connectors_actions.resolve_vault_credential",
                return_value={"team_id": "T222", "bot_user_id": "BOT"},
            ),
            patch(
                "server_modules.channel_pairing_service.get_channel_pairing_service",
                return_value=pairing_service,
            ),
            patch(
                "server_modules.connectors_actions.slack_send_channel_message",
                new=send_message_mock,
            ),
            patch(
                "server_modules.agent_channel_router.route_inbound_channel_message",
                new=AsyncMock(return_value={"ok": True, "run_id": "should-never-happen", "reply": ""}),
            ) as route_mock,
        ):
            asyncio.run(connectors_actions.slack_events_webhook(self._slack_webhook_request()))

        route_mock.assert_not_awaited()
        pairing_service.authorize_channel_message.assert_called_once_with(
            provider="slack",
            external_subject="U-STRANGER",
            workspace_id="ws-1",
            message_text="hi who is this",
        )
        send_message_mock.assert_called_once()
        sent_channel, sent_text = send_message_mock.call_args.args[1], send_message_mock.call_args.args[2]
        self.assertEqual(sent_channel, "D111")
        self.assertIn("not linked to Empyralis", sent_text)

    def test_paired_dm_sender_reaches_route_inbound_channel_message(self) -> None:
        from server_modules import connectors_actions

        pairing_service = mock_module.MagicMock()
        pairing_service.authorize_channel_message.return_value = {
            "authorized": True,
            "status": "linked",
            "workspace_id": "ws-1",
        }

        with (
            patch("server_modules.connectors_actions._append_channel_event", return_value=None),
            patch("server_modules.connectors_actions.slack_verify_request_signature", return_value=True),
            patch(
                "server_modules.connectors_actions.slack_parse_inbound_event",
                return_value={
                    "kind": "event",
                    "event_id": "Ev2001",
                    "message_type": "message",
                    "channel_type": "im",
                    "channel": "D111",
                    "user_id": "U-KNOWN",
                    "text": "hi again",
                },
            ),
            patch(
                "server_modules.connectors_actions.load_vault",
                return_value={
                    "credentials": [
                        {
                            "id": "cred-slack-4",
                            "provider": "slack",
                            "workspace_id": "ws-1",
                            "tenant_id": "tenant-1",
                            "metadata": {"team_id": "T333", "trigger_on_all_messages": True},
                        }
                    ]
                },
            ),
            patch(
                "server_modules.connectors_actions.resolve_vault_credential",
                return_value={"team_id": "T333", "bot_user_id": "BOT"},
            ),
            patch(
                "server_modules.channel_pairing_service.get_channel_pairing_service",
                return_value=pairing_service,
            ),
            patch(
                "server_modules.agent_channel_router.route_inbound_channel_message",
                new=AsyncMock(return_value={"ok": True, "run_id": "run-known", "reply": ""}),
            ) as route_mock,
        ):
            asyncio.run(connectors_actions.slack_events_webhook(self._slack_webhook_request()))

        route_mock.assert_awaited_once()
        pairing_service.authorize_channel_message.assert_called_once_with(
            provider="slack",
            external_subject="U-KNOWN",
            workspace_id="ws-1",
            message_text="hi again",
        )


# ═══════════════════════════ Console (web) ═══════════════════════════


class ConsoleDirectChatEnvelopeTests(unittest.TestCase):
    """(d) console: the live web-chat chokepoint. agent_turn.py's /api/turn
    path stashes a serialized envelope into context_hints["envelope"];
    direct_chat_service.execute_direct_chat_turn_request (the "UNIFIED
    ENTRY" producer — the actual function that calls execute_sage_turn for
    every real web-chat turn) must reconstruct it and forward it through."""

    def test_console_envelope_reaches_execute_sage_turn(self) -> None:
        from server_modules import agent_turn, direct_chat_service
        from server_modules.inbound_envelope import InboundEnvelope, EnvelopeSender, SurfaceKind
        from server_modules.agent_turn_runtime_contract import SageTurnResult

        console_envelope = InboundEnvelope(
            platform="console",
            surface=SurfaceKind.CONSOLE,
            sender=EnvelopeSender(id="user-1", display_name="Mansur", is_owner=True),
        )
        turn_request = agent_turn.AgentTurnRequest(
            tenant_id="tenant-1",
            workspace_id="ws-1",
            thread_id="thread-1",
            session_id="session-1",
            channel="web",
            actor=agent_turn.TurnActor(type="user", id="user-1", display_name="Mansur"),
            message="what's my calendar look like today",
            execution_mode="sync",
            context_hints={
                "envelope": console_envelope.to_metadata(),
                "request_id": "req-1",
            },
        )
        services = direct_chat_service.build_direct_chat_execution_services(
            chat_stream_key=lambda current_user, body: ("session-1", "thread-1", "req-1"),
            session_manager_enabled=lambda: False,
            session_manager_factory=lambda: None,
            build_direct_operator_reply=lambda **kwargs: None,
            build_chat_turn_event_stream=lambda **kwargs: None,
        )

        captured: dict = {}

        async def _fake_execute_sage_turn(**kwargs):
            captured.update(kwargs)
            return SageTurnResult(message="Nothing on your calendar.", trace_id="t1", provider="deepseek", model="deepseek-chat")

        with patch("server_modules.agent_turn_adapter.execute_sage_turn", new=_fake_execute_sage_turn):
            result = asyncio.run(direct_chat_service.execute_direct_chat_turn_request(
                turn_request=turn_request,
                current_user={"user_id": "user-1", "email": "mansur@example.com"},
                services=services,
            ))
            # Drive the streaming producer to completion — this is what
            # actually starts the background thread that calls
            # execute_sage_turn.
            list(result["producer"]())

        envelope = captured.get("envelope")
        self.assertIsNotNone(envelope, "execute_sage_turn must receive the reconstructed console envelope")
        self.assertEqual(envelope.platform, "console")
        self.assertEqual(envelope.surface, SurfaceKind.CONSOLE)
        self.assertIs(envelope.sender.is_owner, True)
        self.assertEqual(envelope.sender.id, "user-1")

    def test_missing_envelope_in_context_hints_is_unwired_fallback(self) -> None:
        """A caller that never populated context_hints["envelope"] (any
        non-console/mobile or non-sync agent_turn() path) must reach
        execute_sage_turn with envelope=None — unchanged legacy behavior."""
        from server_modules import agent_turn, direct_chat_service
        from server_modules.agent_turn_runtime_contract import SageTurnResult

        turn_request = agent_turn.AgentTurnRequest(
            tenant_id="tenant-1",
            workspace_id="ws-1",
            thread_id="thread-2",
            session_id="session-2",
            channel="web",
            actor=agent_turn.TurnActor(type="user", id="user-2", display_name="Someone"),
            message="hello",
            execution_mode="sync",
            context_hints={"request_id": "req-2"},
        )
        services = direct_chat_service.build_direct_chat_execution_services(
            chat_stream_key=lambda current_user, body: ("session-2", "thread-2", "req-2"),
            session_manager_enabled=lambda: False,
            session_manager_factory=lambda: None,
            build_direct_operator_reply=lambda **kwargs: None,
            build_chat_turn_event_stream=lambda **kwargs: None,
        )

        captured: dict = {}

        async def _fake_execute_sage_turn(**kwargs):
            captured.update(kwargs)
            return SageTurnResult(message="hi", trace_id="t2", provider="deepseek", model="deepseek-chat")

        with patch("server_modules.agent_turn_adapter.execute_sage_turn", new=_fake_execute_sage_turn):
            result = asyncio.run(direct_chat_service.execute_direct_chat_turn_request(
                turn_request=turn_request,
                current_user={"user_id": "user-2"},
                services=services,
            ))
            list(result["producer"]())

        self.assertIsNone(captured.get("envelope"))


# ═══════════════════════════ Discord ═══════════════════════════


class _FakeDiscordAuthor:
    def __init__(self, id_: str, name: str) -> None:
        self.id = id_
        self.name = name


class _FakeDiscordChannel:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, text: str) -> None:
        self.sent.append(text)


class _FakeDiscordMessage:
    def __init__(self, *, content: str, author_id: str, author_name: str, msg_id: str) -> None:
        self.content = content
        self.author = _FakeDiscordAuthor(author_id, author_name)
        self.channel = _FakeDiscordChannel()
        self.id = msg_id


class DiscordTrueDmEnvelopeTests(unittest.IsolatedAsyncioTestCase):
    """The canonical Discord 1:1 DM path (_handle_dm_via_gateway) is the
    ONE Discord path with a real, verified owner check — a prior /pair.
    Per the task's ruling, this earns is_owner=True; no other Discord path
    gets that."""

    def setUp(self) -> None:
        from server_modules.connectors import discord_connector

        self.discord_connector = discord_connector
        discord_connector._clear_discord_dedup_cache()

    async def test_paired_dm_sender_is_verified_owner(self) -> None:
        from server_modules.connectors import discord_connector

        captured: dict = {}

        async def _fake_execute_sage_turn(**kwargs):
            captured.update(kwargs)

            class _Result:
                message = "hi there"

            return _Result()

        message = _FakeDiscordMessage(content="what's up", author_id="discord-user-1", author_name="Mansur", msg_id="dm-msg-1")

        with patch(
            "server_modules.discord_pairing_service.get_workspace_for_discord_user",
            return_value="ws-1",
        ), patch(
            "server_modules.agent_command_dispatcher.dispatch_command",
            new=AsyncMock(return_value=None),
        ), patch(
            "server_modules.agent_turn_adapter.execute_sage_turn",
            new=_fake_execute_sage_turn,
        ):
            await discord_connector._handle_dm_via_gateway(message)

        envelope = captured.get("envelope")
        self.assertIsNotNone(envelope)
        self.assertEqual(envelope.platform, "discord_personal")
        self.assertEqual(envelope.surface, SurfaceKind.DM)
        self.assertIs(envelope.sender.is_owner, True)
        self.assertEqual(envelope.sender.id, "discord-user-1")
        self.assertEqual(message.channel.sent, ["hi there"])


class DiscordGuildEnvelopeTests(unittest.IsolatedAsyncioTestCase):
    """The guild / Group-DM path (handle_parsed_event) never runs an
    owner-linkage check at all — is_owner must always be None there, never
    inherited from the unrelated 1:1-DM /pair concept."""

    async def test_guild_mention_gets_group_surface_unverified_owner(self) -> None:
        from server_modules.connectors.discord_bot_runtime_service import DiscordBotRuntimeService

        captured: dict = {}

        async def _fake_route_message(**kwargs):
            captured.update(kwargs)
            return {"ok": True, "triggered": True, "run_id": "run-1"}

        service = DiscordBotRuntimeService(
            load_vault=lambda: {"credentials": []},
            resolve_vault_credential=lambda *a, **k: {},
            append_event=lambda **kwargs: kwargs,
            route_message=_fake_route_message,
            resolve_tenant=AsyncMock(return_value="tenant-1"),
        )

        result = await service.handle_parsed_event(
            {
                "kind": "event",
                "event_type": "message_create",
                "message_type": "mention",
                "channel_id": "123",
                "guild_id": "456",
                "message_id": "msg-1",
                "user_id": "user-1",
                "username": "Mansur",
                "text": "hello",
                "mention_ids": ["999"],
            },
            connector_entry={
                "id": "cred-discord",
                "provider": "discord_bot",
                "workspace_id": "workspace-1",
                "metadata": {"bot_id": "999"},
            },
            credentials={"bot_token": "token", "channel_id": "123"},
        )

        self.assertTrue(result["triggered"])
        envelope = captured.get("envelope")
        self.assertIsNotNone(envelope)
        self.assertEqual(envelope.platform, "discord_guild")
        self.assertEqual(envelope.surface, SurfaceKind.GROUP)
        self.assertIsNone(envelope.sender.is_owner)
        self.assertTrue(envelope.addressed)


if __name__ == "__main__":
    unittest.main()
