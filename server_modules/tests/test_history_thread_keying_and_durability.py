"""Focused coverage for the HISTORY gap-closing work in
docs/design/audit-history-memory.md (gaps #4, #5, #7):

  1. Thread-key resolution for Studio connectors (Slack, Discord guild,
     GitHub) and the two Discord personal-DM code paths no longer
     collapses every sender/room into the shared "sage-main" SQL thread —
     see agent_channel_router.route_inbound_channel_message's thread-id
     resolution block, and the agent_sender_thread_id() fixes in
     discord_connector.py / connectors_actions.py.
  2. WeChat Official and Telegram-Hosted now read/write through the
     durable agent_conversation_memory JSONL store (belt-and-braces next
     to the SQL thread store), the same fix personal channels already had
     — see sage_reply_dispatcher.dispatch_sage_reply's new
     channel_prior_messages/conversation_memory parameters and their call
     sites in wechat_official_service.py / sage_telegram_hosted_service.py.

This module is additive: it does not replace any existing test file, and
it never touches skills_service.py/tool_registry_service.py/memory tool
files/inbound_envelope.py/sage_turn_adapter.py/channel_adapter.py/
direct_chat_generation_service.py.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock as mock_module
from unittest.mock import AsyncMock, patch

from server_modules import agent_channel_router
from server_modules import agent_conversation_memory
from server_modules import connectors_actions
from server_modules import sage_reply_dispatcher
from server_modules import sage_telegram_hosted_service as hosted
from server_modules import wechat_official_service
from server_modules.connectors import discord_connector
from server_modules.inbound_envelope import (
    EnvelopeChat,
    EnvelopeSender,
    InboundEnvelope,
    SurfaceKind,
)
from server_modules.sage_agent_runtime_contract import SageTurnResult
from server_modules.sage_command_dispatcher import agent_sender_thread_id


class _IsolatedConversationsMixin:
    """Points agent_conversation_memory at a throwaway directory for the
    duration of a test, mirroring test_agent_conversation_memory.py's
    fixture (module attribute, not just the env var, since
    _CONVERSATIONS_ROOT is computed once at import time)."""

    def _isolate_conversations_root(self) -> Path:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / "conversations"
        patcher = patch.object(agent_conversation_memory, "_CONVERSATIONS_ROOT", root)
        patcher.start()
        self.addCleanup(patcher.stop)
        return root


# ─── (a) Slack + Discord-guild: two rooms/senders → two thread ids ─────────


class StudioConnectorThreadKeyingTests(unittest.IsolatedAsyncioTestCase):
    """agent_channel_router.route_inbound_channel_message is the shared
    chokepoint Slack, Discord-guild, and GitHub webhooks all route
    through (connectors_actions.py / discord_bot_runtime_service.py) —
    exercised directly here rather than via each webhook's full
    signature-verification plumbing."""

    async def _route(self, *, channel_key: str, envelope: InboundEnvelope, actor_id: str = "") -> str:
        turn_result = SageTurnResult(message="ok")
        with patch(
            "server_modules.sage_turn_adapter.execute_sage_turn",
            new=AsyncMock(return_value=turn_result),
        ) as mock_turn:
            await agent_channel_router.route_inbound_channel_message(
                workspace_id="ws-1",
                channel_key=channel_key,
                customer_message="hello",
                session_key="session-1",
                actor_id=actor_id,
                envelope=envelope,
            )
        return mock_turn.await_args.kwargs["thread_id"]

    async def test_two_slack_channels_get_two_thread_ids(self):
        env_a = InboundEnvelope(
            platform="slack", surface=SurfaceKind.GROUP,
            sender=EnvelopeSender(id="U1", display_name="U1", is_owner=None),
            chat=EnvelopeChat(id="C-AAA"), addressed=False,
        )
        env_b = InboundEnvelope(
            platform="slack", surface=SurfaceKind.GROUP,
            sender=EnvelopeSender(id="U1", display_name="U1", is_owner=None),
            chat=EnvelopeChat(id="C-BBB"), addressed=False,
        )
        thread_a = await self._route(channel_key="slack", envelope=env_a)
        thread_b = await self._route(channel_key="slack", envelope=env_b)

        self.assertNotEqual(thread_a, thread_b)
        self.assertEqual(thread_a, agent_sender_thread_id("sage", "C-AAA"))
        self.assertEqual(thread_b, agent_sender_thread_id("sage", "C-BBB"))

    async def test_slack_dm_keys_per_sender_not_shared(self):
        env_a = InboundEnvelope(
            platform="slack", surface=SurfaceKind.DM,
            sender=EnvelopeSender(id="U-DM-1", display_name="Dana", is_owner=None),
        )
        env_b = InboundEnvelope(
            platform="slack", surface=SurfaceKind.DM,
            sender=EnvelopeSender(id="U-DM-2", display_name="Sam", is_owner=None),
        )
        thread_a = await self._route(channel_key="slack", envelope=env_a)
        thread_b = await self._route(channel_key="slack", envelope=env_b)

        self.assertNotEqual(thread_a, thread_b)
        self.assertEqual(thread_a, agent_sender_thread_id("sage", "U-DM-1"))
        self.assertEqual(thread_b, agent_sender_thread_id("sage", "U-DM-2"))

    async def test_two_discord_guild_channels_get_two_thread_ids(self):
        env_a = InboundEnvelope(
            platform="discord_guild", surface=SurfaceKind.GROUP,
            sender=EnvelopeSender(id="disc-user-1", display_name="alice", is_owner=None),
            chat=EnvelopeChat(id="channel-111"), addressed=True,
        )
        env_b = InboundEnvelope(
            platform="discord_guild", surface=SurfaceKind.GROUP,
            sender=EnvelopeSender(id="disc-user-1", display_name="alice", is_owner=None),
            chat=EnvelopeChat(id="channel-222"), addressed=True,
        )
        thread_a = await self._route(channel_key="discord", envelope=env_a)
        thread_b = await self._route(channel_key="discord", envelope=env_b)

        self.assertNotEqual(thread_a, thread_b)
        self.assertEqual(thread_a, agent_sender_thread_id("sage", "channel-111"))
        self.assertEqual(thread_b, agent_sender_thread_id("sage", "channel-222"))

    async def test_two_github_repos_get_two_thread_ids_even_with_same_actor(self):
        """GitHub's envelope is surface=API (no GROUP/BROADCAST_CHANNEL —
        a webhook has no member-list concept) but still wants per-repo
        scoping: chat.id presence, not the surface enum, is what selects
        room-over-sender keying."""
        env_a = InboundEnvelope(
            platform="github", surface=SurfaceKind.API,
            sender=EnvelopeSender(id="octocat", display_name="octocat", is_owner=None),
            chat=EnvelopeChat(id="acme/api", title="acme/api"),
        )
        env_b = InboundEnvelope(
            platform="github", surface=SurfaceKind.API,
            sender=EnvelopeSender(id="octocat", display_name="octocat", is_owner=None),
            chat=EnvelopeChat(id="acme/web", title="acme/web"),
        )
        thread_a = await self._route(channel_key="github", envelope=env_a)
        thread_b = await self._route(channel_key="github", envelope=env_b)

        self.assertNotEqual(thread_a, thread_b)
        self.assertEqual(thread_a, agent_sender_thread_id("sage", "acme/api"))
        self.assertEqual(thread_b, agent_sender_thread_id("sage", "acme/web"))

    async def test_envelope_less_caller_keeps_pre_existing_fallback_untouched(self):
        """A caller that never constructs a canonical envelope (WhatsApp/
        Telegram ingress, the direct /agent-registry/channels/inbound API
        route) must see byte-for-byte unchanged behavior: empty thread_id
        in, so execute_sage_turn's own (frozen) fallback still runs."""
        turn_result = SageTurnResult(message="ok")
        with patch(
            "server_modules.sage_turn_adapter.execute_sage_turn",
            new=AsyncMock(return_value=turn_result),
        ) as mock_turn:
            await agent_channel_router.route_inbound_channel_message(
                workspace_id="ws-1",
                channel_key="slack",
                customer_message="hello",
                session_key="session-1",
                actor_id="U1",
                envelope=None,
            )
        self.assertEqual(mock_turn.await_args.kwargs["thread_id"], "")


# ─── GitHub webhook: envelope construction ─────────────────────────────────


class GithubWebhookEnvelopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_github_webhook_builds_per_repo_envelope(self):
        connector_row = {
            "id": "cred-github", "provider": "github", "workspace_id": "default",
            "tenant_id": "tenant-default", "metadata": {"username": "acme"},
        }
        route_message = AsyncMock(return_value={"ok": True, "run_id": "run-1", "reply": ""})
        parsed = {
            "repository": "acme/api",
            "sender": "octocat",
            "event_type": "issues",
            "delivery_id": "delivery-1",
        }

        with (
            patch("server_modules.connectors_actions.load_vault", return_value={"credentials": [connector_row]}),
            patch(
                "server_modules.connectors_actions.resolve_vault_credential",
                return_value={"webhook_secret": "secret-1", "username": "acme"},
            ),
            patch("server_modules.connectors_actions.github_parse_inbound_event", return_value=parsed),
            patch("server_modules.connectors_actions.github_event_matches_connector", return_value=True),
            patch("server_modules.connectors_actions.github_should_trigger_agent_run", return_value=True),
            patch("server_modules.connectors_actions.github_build_run_goal_from_event", return_value="issue opened"),
            patch.object(connectors_actions, "_append_channel_event", None, create=True),
            patch("server_modules.agent_channel_router.route_inbound_channel_message", new=route_message),
            patch("server_modules.connectors_actions.github_request_signature_header", return_value="sha256=x"),
            patch("server_modules.connectors_actions.github_verify_request_signature", return_value=True),
        ):
            request = SimpleNamespace(
                body=AsyncMock(return_value=b"{}"),
                headers={"x-github-event": "issues", "x-github-delivery": "delivery-1"},
            )
            await connectors_actions.github_events_webhook(request)

        route_message.assert_awaited_once()
        envelope = route_message.await_args.kwargs["envelope"]
        self.assertIsInstance(envelope, InboundEnvelope)
        self.assertEqual(envelope.platform, "github")
        self.assertEqual(envelope.surface, SurfaceKind.API)
        self.assertEqual(envelope.sender.id, "octocat")
        self.assertIsNone(envelope.sender.is_owner)
        self.assertEqual(envelope.chat.id, "acme/api")


# ─── Discord DM: live (canonical) path + dormant path ──────────────────────


class DiscordDmThreadKeyingTests(unittest.IsolatedAsyncioTestCase):
    """discord_connector._handle_dm_via_gateway is the CANONICAL, live
    Discord 1:1 DM path (Path C) — previously thread_id="sage-main" for
    EVERY paired user."""

    def setUp(self):
        discord_connector._clear_discord_dedup_cache()

    def _make_message(self, *, text, author_id, message_id):
        async def _send(content):
            pass

        return SimpleNamespace(
            content=text, id=message_id,
            author=SimpleNamespace(id=author_id, name=f"user-{author_id}"),
            channel=SimpleNamespace(send=_send),
        )

    async def test_two_dm_senders_get_two_thread_ids(self):
        sage_result = SimpleNamespace(message="")
        with (
            patch(
                "server_modules.discord_pairing_service.get_workspace_for_discord_user",
                return_value="ws-1",
            ),
            patch(
                "server_modules.sage_command_dispatcher.dispatch_command",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "server_modules.sage_turn_adapter.execute_sage_turn",
                new=AsyncMock(return_value=sage_result),
            ) as mock_turn,
            patch(
                "server_modules.channel_adapter.filter_channel_outbound_reply",
                side_effect=lambda text: text,
            ),
        ):
            await discord_connector._handle_dm_via_gateway(
                self._make_message(text="hi", author_id="alice-1", message_id="m-1")
            )
            await discord_connector._handle_dm_via_gateway(
                self._make_message(text="hi", author_id="bob-2", message_id="m-2")
            )

        self.assertEqual(mock_turn.await_count, 2)
        thread_alice = mock_turn.await_args_list[0].kwargs["thread_id"]
        thread_bob = mock_turn.await_args_list[1].kwargs["thread_id"]
        self.assertNotEqual(thread_alice, "sage-main")
        self.assertNotEqual(thread_bob, "sage-main")
        self.assertNotEqual(thread_alice, thread_bob)
        self.assertEqual(thread_alice, agent_sender_thread_id("sage", "alice-1"))
        self.assertEqual(thread_bob, agent_sender_thread_id("sage", "bob-2"))


class DormantDiscordDmWebhookBranchTests(unittest.IsolatedAsyncioTestCase):
    """connectors_actions.discord_webhook's forward-compatible
    message_type == "direct_message" branch (currently unreachable via
    the standard Discord Interactions endpoint, per its own comment) — no
    longer hardcodes thread_id="sage-main" nor omits the envelope."""

    def setUp(self):
        discord_connector._clear_discord_dedup_cache()

    async def _invoke(self, *, user_id: str, message_id: str):
        connector_row = {
            "id": "cred-discord", "provider": "discord_bot", "workspace_id": "default",
            "tenant_id": "default", "metadata": {},
        }
        parsed = {
            "kind": "event", "message_type": "direct_message",
            "user_id": user_id, "username": f"user-{user_id}",
            "text": "hello bot", "message_id": message_id,
        }
        sage_result = SimpleNamespace(message="")
        with (
            patch("server_modules.connectors_actions.load_vault", return_value={"credentials": [connector_row]}),
            patch(
                "server_modules.connectors_actions.resolve_vault_credential",
                return_value={"application_public_key": "key", "bot_token": "tok"},
            ),
            patch("server_modules.connectors_actions.discord_interaction_signature_headers",
                  return_value={"signature": "sig", "timestamp": "1700000000"}),
            patch("server_modules.connectors_actions.discord_verify_interaction_signature", return_value=True),
            patch("server_modules.connectors_actions.discord_parse_inbound_event", return_value=parsed),
            patch("server_modules.connectors_actions.discord_event_matches_connector", return_value=True),
            patch("server_modules.connectors_actions.discord_should_trigger_agent_run", return_value=True),
            patch("server_modules.connectors_actions.discord_build_run_goal_from_event", return_value="goal"),
            patch.object(connectors_actions, "_append_channel_event", None, create=True),
            patch(
                "server_modules.sage_command_dispatcher.dispatch_command",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "server_modules.sage_turn_adapter.execute_sage_turn",
                new=AsyncMock(return_value=sage_result),
            ) as mock_turn,
        ):
            request = SimpleNamespace(body=AsyncMock(return_value=b"{}"), headers={})
            await connectors_actions.discord_webhook(request)
        return mock_turn

    async def test_dormant_branch_no_longer_hardcodes_sage_main(self):
        mock_turn = await self._invoke(user_id="dm-user-1", message_id="evt-dm-1")
        mock_turn.assert_awaited_once()
        kwargs = mock_turn.await_args.kwargs
        self.assertNotEqual(kwargs["thread_id"], "sage-main")
        self.assertEqual(kwargs["thread_id"], agent_sender_thread_id("sage", "dm-user-1"))
        self.assertIsInstance(kwargs["envelope"], InboundEnvelope)
        self.assertEqual(kwargs["envelope"].sender.id, "dm-user-1")

    async def test_two_dormant_branch_senders_get_two_thread_ids(self):
        mock_turn_a = await self._invoke(user_id="dm-user-A", message_id="evt-dm-a")
        mock_turn_b = await self._invoke(user_id="dm-user-B", message_id="evt-dm-b")
        self.assertNotEqual(
            mock_turn_a.await_args.kwargs["thread_id"],
            mock_turn_b.await_args.kwargs["thread_id"],
        )


# ─── (b) Durable conversation memory: generic dispatch_sage_reply wiring ───


class _FakeTransport:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.supports_typing_indicator = False
        self.max_message_length = 4096

    def format_text(self, text: str) -> str:
        return text

    async def send_message(self, text: str, reply_to_id=None) -> bool:
        self.sent.append(text)
        return True

    async def start_typing(self) -> None:  # pragma: no cover
        pass

    async def stop_typing(self) -> None:  # pragma: no cover
        pass


def _no_op_directives(**kwargs):
    class _Proc:
        replies: list[str] = []
        is_command_only = False
        text = kwargs.get("text", "")

    return _Proc()


class DispatchSageReplyConversationMemoryTests(_IsolatedConversationsMixin, unittest.IsolatedAsyncioTestCase):
    """sage_reply_dispatcher.dispatch_sage_reply's new channel_prior_messages
    (read) / conversation_memory (write) parameters — the shared mechanism
    both wechat_official_service.py and sage_telegram_hosted_service.py
    now route through."""

    def setUp(self):
        self._isolate_conversations_root()

    async def test_round_trips_user_and_assistant_turns_with_envelope_metadata(self):
        transport = _FakeTransport()
        envelope = InboundEnvelope(
            platform="wechat_official", surface=SurfaceKind.DM,
            sender=EnvelopeSender(id="openid-1", display_name="", is_owner=False),
        )
        turn_result = SageTurnResult(message="Hi there, customer!")

        with (
            patch("server_modules.command_registry.process_message", new=AsyncMock(side_effect=_no_op_directives)),
            patch("server_modules.sage_turn_adapter.execute_sage_turn", new=AsyncMock(return_value=turn_result)),
        ):
            delivered = await sage_reply_dispatcher.dispatch_sage_reply(
                transport=transport,
                workspace_id="ws-wc",
                message="Hello agent",
                channel_origin="wechat_official",
                sender_id="openid-1",
                envelope=envelope,
                conversation_memory={
                    "workspace_id": "ws-wc", "agent_id": "agent-1",
                    "conversation_key": "wechat_official:openid-1",
                },
            )

        self.assertTrue(delivered)
        turns = agent_conversation_memory.load_recent_turns(
            workspace_id="ws-wc", agent_id="agent-1", conversation_key="wechat_official:openid-1",
        )
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[0]["role"], "user")
        self.assertEqual(turns[0]["content"], "Hello agent")
        self.assertEqual(turns[0]["metadata"]["platform"], "wechat_official")
        self.assertEqual(turns[0]["metadata"]["sender_id"], "openid-1")
        self.assertIs(turns[0]["metadata"]["sender_is_owner"], False)
        self.assertEqual(turns[1]["role"], "assistant")
        self.assertEqual(turns[1]["content"], "Hi there, customer!")
        # The assistant's own reply is never tagged with the sender's
        # envelope — only the inbound user turn is (see
        # sage_reply_dispatcher.dispatch_sage_reply's docstring).
        self.assertNotIn("metadata", turns[1])

    async def test_channel_prior_messages_forwarded_to_execute_sage_turn(self):
        transport = _FakeTransport()
        prior = [{"role": "user", "content": "earlier message"}]
        turn_result = SageTurnResult(message="ok")

        with (
            patch("server_modules.command_registry.process_message", new=AsyncMock(side_effect=_no_op_directives)),
            patch(
                "server_modules.sage_turn_adapter.execute_sage_turn", new=AsyncMock(return_value=turn_result),
            ) as mock_turn,
        ):
            await sage_reply_dispatcher.dispatch_sage_reply(
                transport=transport,
                workspace_id="ws-wc",
                message="second message",
                channel_origin="wechat_official",
                sender_id="openid-1",
                channel_prior_messages=prior,
            )

        self.assertEqual(mock_turn.await_args.kwargs["channel_prior_messages"], prior)

    async def test_no_conversation_memory_kwarg_writes_nothing(self):
        """Unwired callers (e.g. hosted_bot_provisioning_service.py's BYO
        Telegram bot path) keep zero behavior change — no conversation_key
        means no write, matching pre-existing behavior exactly."""
        transport = _FakeTransport()
        turn_result = SageTurnResult(message="ok")
        with (
            patch("server_modules.command_registry.process_message", new=AsyncMock(side_effect=_no_op_directives)),
            patch("server_modules.sage_turn_adapter.execute_sage_turn", new=AsyncMock(return_value=turn_result)),
        ):
            await sage_reply_dispatcher.dispatch_sage_reply(
                transport=transport, workspace_id="ws-x", message="hi", channel_origin="telegram_hosted",
            )
        turns = agent_conversation_memory.load_recent_turns(
            workspace_id="ws-x", agent_id="", conversation_key="anything",
        )
        self.assertEqual(turns, [])


def _authorized_wechat_pairing_service():
    """Gate 1 (channel_pairing_service.authorize_channel_message) mocked as
    already-linked — this module tests durable conversation memory/thread
    keying DOWNSTREAM of Gate 1, not the gate itself (that's covered by
    WeChatOfficialGate1Tests in test_inbound_envelope_hosted_channels.py)."""
    service = mock_module.MagicMock()
    service.authorize_channel_message.return_value = {
        "authorized": True,
        "status": "linked",
        "workspace_id": "ws-wechat",
    }
    return service


# ─── WeChat: real end-to-end durable round trip ────────────────────────────


class WeChatDurableMemoryTests(_IsolatedConversationsMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._isolate_conversations_root()

    @staticmethod
    def _xml_body(*, from_user: str, content: str, msg_id: str) -> str:
        return (
            "<xml>"
            "<ToUserName><![CDATA[gh_agent]]></ToUserName>"
            f"<FromUserName><![CDATA[{from_user}]]></FromUserName>"
            "<CreateTime>1700000000</CreateTime>"
            "<MsgType><![CDATA[text]]></MsgType>"
            f"<Content><![CDATA[{content}]]></Content>"
            f"<MsgId>{msg_id}</MsgId>"
            "</xml>"
        )

    async def test_inbound_callback_round_trips_through_agent_conversation_memory(self):
        binding = {"workspace_id": "ws-wechat"}
        binding_meta = {"account_kind": "official", "app_id": "appid-1"}
        creds = {"verify_token": "tok", "app_secret": "sec"}

        first_turn = SageTurnResult(message="Hi customer, how can I help?")
        second_turn = SageTurnResult(message="Sure, following up.")
        turn_mock = AsyncMock(side_effect=[first_turn, second_turn])

        with (
            patch(
                "server_modules.wechat_official_service._resolve_binding_and_credential",
                new=AsyncMock(return_value=(binding, binding_meta, "cred-1", creds)),
            ),
            patch("server_modules.wechat_official_service.verify_wechat_server_signature", return_value=True),
            patch(
                "server_modules.wechat_official_service.send_wechat_text_message",
                new=AsyncMock(return_value={"ok": True}),
            ),
            patch("server_modules.sage_turn_adapter.execute_sage_turn", new=turn_mock),
            patch(
                "server_modules.channel_pairing_service.get_channel_pairing_service",
                return_value=_authorized_wechat_pairing_service(),
            ),
        ):
            result_1 = await wechat_official_service.handle_inbound_callback(
                agent_install_id="agent-wc-1", timestamp="t", nonce="n", signature="s",
                raw_body=self._xml_body(from_user="ext-openid-999", content="Hello agent", msg_id="1"),
            )
            self.assertTrue(result_1["routed"] and result_1["processed"])

            result_2 = await wechat_official_service.handle_inbound_callback(
                agent_install_id="agent-wc-1", timestamp="t", nonce="n", signature="s",
                raw_body=self._xml_body(from_user="ext-openid-999", content="Follow-up question", msg_id="2"),
            )
            self.assertTrue(result_2["routed"] and result_2["processed"])

        # write side: both exchanges durably persisted, keyed by
        # (agent, "wechat_official:<sender OpenID>").
        turns = agent_conversation_memory.load_recent_turns(
            workspace_id="ws-wechat", agent_id="agent-wc-1",
            conversation_key="wechat_official:ext-openid-999",
        )
        self.assertEqual(len(turns), 4)
        self.assertEqual(turns[0]["content"], "Hello agent")
        self.assertEqual(turns[0]["metadata"]["platform"], "wechat_official")
        self.assertEqual(turns[0]["metadata"]["sender_id"], "ext-openid-999")
        self.assertIs(turns[0]["metadata"]["sender_is_owner"], False)
        self.assertEqual(turns[1]["content"], "Hi customer, how can I help?")
        self.assertEqual(turns[2]["content"], "Follow-up question")
        self.assertEqual(turns[3]["content"], "Sure, following up.")

        # read side: the SECOND turn's execute_sage_turn call received the
        # FIRST exchange as channel_prior_messages (the actual "recall").
        second_call_kwargs = turn_mock.await_args_list[1].kwargs
        prior = second_call_kwargs["channel_prior_messages"]
        self.assertTrue(any(t.get("content") == "Hello agent" for t in prior))
        self.assertTrue(any(t.get("content") == "Hi customer, how can I help?" for t in prior))

    async def test_different_customers_get_different_conversation_keys(self):
        binding = {"workspace_id": "ws-wechat"}
        binding_meta = {"account_kind": "official", "app_id": "appid-1"}
        creds = {"verify_token": "tok", "app_secret": "sec"}
        turn_result = SageTurnResult(message="ok")

        with (
            patch(
                "server_modules.wechat_official_service._resolve_binding_and_credential",
                new=AsyncMock(return_value=(binding, binding_meta, "cred-1", creds)),
            ),
            patch("server_modules.wechat_official_service.verify_wechat_server_signature", return_value=True),
            patch(
                "server_modules.wechat_official_service.send_wechat_text_message",
                new=AsyncMock(return_value={"ok": True}),
            ),
            patch("server_modules.sage_turn_adapter.execute_sage_turn", new=AsyncMock(return_value=turn_result)),
            patch(
                "server_modules.channel_pairing_service.get_channel_pairing_service",
                return_value=_authorized_wechat_pairing_service(),
            ),
        ):
            await wechat_official_service.handle_inbound_callback(
                agent_install_id="agent-wc-1", timestamp="t", nonce="n", signature="s",
                raw_body=self._xml_body(from_user="customer-A", content="hi from A", msg_id="a1"),
            )
            await wechat_official_service.handle_inbound_callback(
                agent_install_id="agent-wc-1", timestamp="t", nonce="n", signature="s",
                raw_body=self._xml_body(from_user="customer-B", content="hi from B", msg_id="b1"),
            )

        turns_a = agent_conversation_memory.load_recent_turns(
            workspace_id="ws-wechat", agent_id="agent-wc-1", conversation_key="wechat_official:customer-A",
        )
        turns_b = agent_conversation_memory.load_recent_turns(
            workspace_id="ws-wechat", agent_id="agent-wc-1", conversation_key="wechat_official:customer-B",
        )
        self.assertEqual([t["content"] for t in turns_a if t["role"] == "user"], ["hi from A"])
        self.assertEqual([t["content"] for t in turns_b if t["role"] == "user"], ["hi from B"])


# ─── Telegram-Hosted: real end-to-end durable round trip ───────────────────


class TelegramHostedDurableMemoryTests(_IsolatedConversationsMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._isolate_conversations_root()
        self._paired_chat_ids: list[str] = []

    def tearDown(self):
        for chat_id in self._paired_chat_ids:
            hosted._SAGE_HOSTED_PAIRS.pop(chat_id, None)

    def _pair(self, chat_id: str, workspace_id: str) -> None:
        hosted._SAGE_HOSTED_PAIRS[str(chat_id)] = {"workspace_id": workspace_id, "paired_at": "2026-01-01T00:00:00Z"}
        self._paired_chat_ids.append(str(chat_id))

    @staticmethod
    def _update(*, chat_id: str, sender_id: str, text: str, message_id: int) -> dict:
        return {
            "message": {
                "message_id": message_id,
                "chat": {"id": chat_id, "type": "private"},
                "from": {"id": sender_id, "first_name": "Alice"},
                "text": text,
            }
        }

    async def test_process_update_round_trips_through_agent_conversation_memory(self):
        self._pair("chat-42", "ws-tg")
        first_turn = SageTurnResult(message="Hello, owner!")
        second_turn = SageTurnResult(message="Still here.")
        turn_mock = AsyncMock(side_effect=[first_turn, second_turn])

        with (
            patch("server_modules.sage_telegram_hosted_service._telegram_api", new=AsyncMock(return_value={"ok": True, "result": {}})),
            patch("server_modules.sage_command_dispatcher.dispatch_command", new=AsyncMock(return_value=None)),
            patch("server_modules.sage_turn_adapter.execute_sage_turn", new=turn_mock),
        ):
            handled_1 = await hosted._process_update(
                self._update(chat_id="chat-42", sender_id="tg-user-1", text="Hi agent", message_id=1)
            )
            self.assertTrue(handled_1)
            handled_2 = await hosted._process_update(
                self._update(chat_id="chat-42", sender_id="tg-user-1", text="Still there?", message_id=2)
            )
            self.assertTrue(handled_2)

        turns = agent_conversation_memory.load_recent_turns(
            workspace_id="ws-tg", agent_id="", conversation_key="telegram_hosted:chat-42",
        )
        self.assertEqual(len(turns), 4)
        self.assertEqual(turns[0]["content"], "Hi agent")
        self.assertEqual(turns[0]["metadata"]["platform"], "telegram_hosted")
        self.assertIs(turns[0]["metadata"]["sender_is_owner"], True)
        self.assertEqual(turns[2]["content"], "Still there?")

        second_call_kwargs = turn_mock.await_args_list[1].kwargs
        prior = second_call_kwargs["channel_prior_messages"]
        self.assertTrue(any(t.get("content") == "Hi agent" for t in prior))

    async def test_two_paired_chats_get_two_conversation_keys(self):
        self._pair("chat-A", "ws-tg-a")
        self._pair("chat-B", "ws-tg-b")
        turn_result = SageTurnResult(message="ok")

        with (
            patch("server_modules.sage_telegram_hosted_service._telegram_api", new=AsyncMock(return_value={"ok": True, "result": {}})),
            patch("server_modules.sage_command_dispatcher.dispatch_command", new=AsyncMock(return_value=None)),
            patch("server_modules.sage_turn_adapter.execute_sage_turn", new=AsyncMock(return_value=turn_result)),
        ):
            await hosted._process_update(
                self._update(chat_id="chat-A", sender_id="u-1", text="from chat A", message_id=1)
            )
            await hosted._process_update(
                self._update(chat_id="chat-B", sender_id="u-2", text="from chat B", message_id=2)
            )

        turns_a = agent_conversation_memory.load_recent_turns(
            workspace_id="ws-tg-a", agent_id="", conversation_key="telegram_hosted:chat-A",
        )
        turns_b = agent_conversation_memory.load_recent_turns(
            workspace_id="ws-tg-b", agent_id="", conversation_key="telegram_hosted:chat-B",
        )
        self.assertEqual([t["content"] for t in turns_a if t["role"] == "user"], ["from chat A"])
        self.assertEqual([t["content"] for t in turns_b if t["role"] == "user"], ["from chat B"])


if __name__ == "__main__":
    unittest.main()
