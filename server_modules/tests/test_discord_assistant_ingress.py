"""Discord Sage Ingress — Unit Tests.

Verify that Discord DM messages route through the unified Sage ingress
(execute_sage_turn, Path A) matching the Telegram-hosted pattern, while
guild messages continue to use the specialist connector path.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from server_modules.connectors.discord_connector import parse_inbound_event


def _run(coro):
    return asyncio.run(coro)


# ──────────────────────────────────────────────────────────────────────────────
# 1. Parse: DM vs Guild
# ──────────────────────────────────────────────────────────────────────────────

class DiscordDMParseTests(unittest.TestCase):
    """parse_inbound_event correctly distinguishes DMs from guild messages."""

    def test_dm_has_empty_guild_id(self):
        """DM events have empty guild_id → message_type='direct_message'."""
        parsed = parse_inbound_event({
            "t": "MESSAGE_CREATE",
            "d": {
                "id": "msg-1",
                "channel_id": "123",
                "guild_id": "",
                "content": "hi",
                "author": {"id": "u1", "username": "alice"},
                "mentions": [],
            },
        })
        self.assertEqual(parsed["message_type"], "direct_message")
        self.assertIsNone(parsed["guild_id"])

    def test_guild_message_without_mention_is_ignored_or_message(self):
        """Guild message without bot mention may be 'message' (not DM)."""
        parsed = parse_inbound_event({
            "t": "MESSAGE_CREATE",
            "d": {
                "id": "msg-2",
                "channel_id": "111",
                "guild_id": "222",
                "content": "general chat",
                "author": {"id": "u2", "username": "bob"},
                "mentions": [],
            },
        })
        # Guild messages without @mention are classified as 'message'
        self.assertIn(parsed["message_type"], {"message", "mention", "ignored"})
        self.assertIsNotNone(parsed["guild_id"])

    def test_dm_parse_extracts_all_identity_fields(self):
        """DM parse populates user_id, username, text, message_id, channel_id."""
        parsed = parse_inbound_event({
            "t": "MESSAGE_CREATE",
            "d": {
                "id": "dm-99",
                "channel_id": "456",
                "guild_id": "",
                "content": "what can you do",
                "author": {"id": "user-99", "username": "charlie"},
                "mentions": [],
            },
        })
        self.assertEqual(parsed["user_id"], "user-99")
        self.assertEqual(parsed["username"], "charlie")
        self.assertEqual(parsed["text"], "what can you do")
        self.assertEqual(parsed["message_id"], "dm-99")
        self.assertEqual(parsed["channel_id"], "456")
        self.assertEqual(parsed["message_type"], "direct_message")


# ──────────────────────────────────────────────────────────────────────────────
# 2. Routing: DM → execute_sage_turn
# ──────────────────────────────────────────────────────────────────────────────

class DiscordSageIngressRoutingTests(unittest.TestCase):
    """DiscordBotRuntimeService.handle_parsed_event routes DMs through
    execute_sage_turn (Path A)."""

    def _make_parsed_dm(self, text="hello Sage", user_id="user-1",
                         username="testuser", message_id="dm-1",
                         channel_id="999", guild_id=""):
        return {
            "kind": "event",
            "event_type": "message_create",
            "message_type": "direct_message",
            "channel_id": channel_id,
            "guild_id": guild_id,
            "user_id": user_id,
            "username": username,
            "text": text,
            "message_id": message_id,
            "raw_event": {
                "author": {"id": user_id, "username": username},
            },
        }

    def _make_parsed_guild_mention(self, text="<@999> help", user_id="user-2",
                                    username="guilduser", message_id="gm-1",
                                    channel_id="111", guild_id="222"):
        return {
            "kind": "event",
            "event_type": "message_create",
            "message_type": "mention",
            "channel_id": channel_id,
            "guild_id": guild_id,
            "user_id": user_id,
            "username": username,
            "text": text,
            "message_id": message_id,
            "raw_event": {
                "author": {"id": user_id, "username": username},
            },
        }

    def _make_connector_entry(self, workspace_id="ws-1"):
        return {
            "id": "discord-bot-1",
            "workspace_id": workspace_id,
            "metadata": {},
        }

    def _make_credentials(self):
        return {"bot_token": "mock-bot-token"}

    def test_dm_routes_through_execute_sage_turn(self):
        """A direct_message event calls execute_sage_turn, not the specialist path."""
        from server_modules.connectors import discord_bot_runtime_service

        parsed = self._make_parsed_dm(text="hello Sage")
        connector_entry = self._make_connector_entry()
        credentials = self._make_credentials()

        svc = discord_bot_runtime_service.DiscordBotRuntimeService(
            load_vault=lambda: {},
            resolve_vault_credential=lambda p, ws: credentials,
            append_event=lambda **kw: None,
        )

        # Mock the Sage turn — return a reply that should be sent via DM
        sage_result = MagicMock()
        sage_result.message = "Hey! I'm Sage, your assistant."
        sage_result.trace_id = "trace-dm-1"
        sage_result.provider = "deepseek"

        with (
            patch("server_modules.connectors.discord_bot_runtime_service.event_matches_connector",
                  return_value=True),
            patch("server_modules.connectors.discord_bot_runtime_service.should_trigger_agent_run",
                  return_value=True),
            patch("server_modules.agent_turn_adapter.execute_sage_turn",
                  new=AsyncMock(return_value=sage_result)) as mock_sage_turn,
            patch("server_modules.connectors.discord_connector.send_dm") as mock_send_dm,
            patch("server_modules.agent_command_dispatcher.dispatch_command",
                  new=AsyncMock(return_value=None)),
        ):
            result = _run(svc.handle_parsed_event(
                parsed=parsed,
                connector_entry=connector_entry,
                credentials=credentials,
            ))

        # Should have triggered
        self.assertTrue(result["triggered"], f"Expected triggered=True, got {result}")
        self.assertEqual(result["reason"], "sage_ingress_dm_replied")

        # execute_sage_turn called with correct channel origin
        mock_sage_turn.assert_called_once()
        call_kwargs = mock_sage_turn.call_args.kwargs
        self.assertEqual(call_kwargs["workspace_id"], "ws-1")
        self.assertEqual(call_kwargs["channel_origin"], "discord_personal")
        self.assertEqual(call_kwargs["channel_sender_id"], "user-1")
        self.assertEqual(call_kwargs["channel_sender_name"], "testuser")
        self.assertEqual(call_kwargs["message"], "hello Sage")

        # send_dm called with the filtered reply
        mock_send_dm.assert_called_once()
        dm_kwargs = mock_send_dm.call_args.kwargs
        self.assertEqual(dm_kwargs["user_id"], "user-1")
        self.assertIn("Hey! I'm Sage", dm_kwargs["content"])

    def test_dm_dispatches_command_before_sage_ingress(self):
        """A DM with /help calls dispatch_command first, not execute_sage_turn."""
        from server_modules.connectors import discord_bot_runtime_service

        parsed = self._make_parsed_dm(text="/help")
        connector_entry = self._make_connector_entry()
        credentials = self._make_credentials()

        svc = discord_bot_runtime_service.DiscordBotRuntimeService(
            load_vault=lambda: {},
            resolve_vault_credential=lambda p, ws: credentials,
            append_event=lambda **kw: None,
        )

        with (
            patch("server_modules.connectors.discord_bot_runtime_service.event_matches_connector",
                  return_value=True),
            patch("server_modules.connectors.discord_bot_runtime_service.should_trigger_agent_run",
                  return_value=True),
            patch("server_modules.agent_turn_adapter.execute_sage_turn",
                  new=AsyncMock()) as mock_sage_turn,
            patch("server_modules.connectors.discord_connector.send_dm") as mock_send_dm,
            patch("server_modules.agent_command_dispatcher.dispatch_command",
                  new=AsyncMock(return_value="Available commands: /help, /memory, /status")) as mock_dispatch,
        ):
            result = _run(svc.handle_parsed_event(
                parsed=parsed,
                connector_entry=connector_entry,
                credentials=credentials,
            ))

        self.assertTrue(result["triggered"])
        self.assertEqual(result["reason"], "command_dispatched")
        mock_dispatch.assert_called_once()
        # execute_sage_turn should NOT be called when command is dispatched
        mock_sage_turn.assert_not_called()
        # send_dm called with the command reply
        mock_send_dm.assert_called_once()
        dm_kwargs = mock_send_dm.call_args.kwargs
        self.assertIn("Available commands", dm_kwargs["content"])

    def test_guild_message_uses_specialist_path_not_sage_ingress(self):
        """A guild message with @mention does NOT call execute_sage_turn."""
        from server_modules.connectors import discord_bot_runtime_service

        parsed = self._make_parsed_guild_mention()
        connector_entry = self._make_connector_entry()
        credentials = self._make_credentials()

        svc = discord_bot_runtime_service.DiscordBotRuntimeService(
            load_vault=lambda: {},
            resolve_vault_credential=lambda p, ws: credentials,
            append_event=lambda **kw: None,
        )

        with (
            patch("server_modules.connectors.discord_bot_runtime_service.event_matches_connector",
                  return_value=True),
            patch("server_modules.connectors.discord_bot_runtime_service.should_trigger_agent_run",
                  return_value=True),
            patch("server_modules.agent_turn_adapter.execute_sage_turn",
                  new=AsyncMock()) as mock_sage_turn,
            patch("server_modules.connectors.discord_bot_runtime_service.build_run_goal_from_event",
                  return_value={"goal": "help the user", "target": "specialist"}),
            patch.object(svc, "route_message", new=AsyncMock(
                return_value={"run_id": "run-123", "triggered": True})),
            patch.object(svc, "resolve_tenant", new=AsyncMock(return_value="t-1")),
        ):
            result = _run(svc.handle_parsed_event(
                parsed=parsed,
                connector_entry=connector_entry,
                credentials=credentials,
            ))

        # Specialist path should trigger a run
        self.assertTrue(result["triggered"])
        self.assertEqual(result["run_id"], "run-123")
        # execute_sage_turn should NOT be called for guild messages
        mock_sage_turn.assert_not_called()

    def test_empty_dm_text_returns_without_sage_call(self):
        """Empty DM text returns early without calling execute_sage_turn."""
        from server_modules.connectors import discord_bot_runtime_service

        parsed = self._make_parsed_dm(text="")  # empty text
        connector_entry = self._make_connector_entry()
        credentials = self._make_credentials()

        svc = discord_bot_runtime_service.DiscordBotRuntimeService(
            load_vault=lambda: {},
            resolve_vault_credential=lambda p, ws: credentials,
            append_event=lambda **kw: None,
        )

        with (
            patch("server_modules.connectors.discord_bot_runtime_service.event_matches_connector",
                  return_value=True),
            patch("server_modules.connectors.discord_bot_runtime_service.should_trigger_agent_run",
                  return_value=True),
            patch("server_modules.agent_turn_adapter.execute_sage_turn",
                  new=AsyncMock()) as mock_sage_turn,
        ):
            result = _run(svc.handle_parsed_event(
                parsed=parsed,
                connector_entry=connector_entry,
                credentials=credentials,
            ))

        self.assertFalse(result["triggered"])
        self.assertEqual(result["reason"], "empty_dm")
        mock_sage_turn.assert_not_called()

    def test_dm_error_is_caught_and_reported(self):
        """When execute_sage_turn raises, the error is caught and the listener
        continues (does not crash)."""
        from server_modules.connectors import discord_bot_runtime_service

        parsed = self._make_parsed_dm(text="cause an error")
        connector_entry = self._make_connector_entry()
        credentials = self._make_credentials()

        svc = discord_bot_runtime_service.DiscordBotRuntimeService(
            load_vault=lambda: {},
            resolve_vault_credential=lambda p, ws: credentials,
            append_event=lambda **kw: None,
        )

        with (
            patch("server_modules.connectors.discord_bot_runtime_service.event_matches_connector",
                  return_value=True),
            patch("server_modules.connectors.discord_bot_runtime_service.should_trigger_agent_run",
                  return_value=True),
            patch("server_modules.agent_turn_adapter.execute_sage_turn",
                  new=AsyncMock(side_effect=RuntimeError("simulated failure"))),
            patch("server_modules.agent_command_dispatcher.dispatch_command",
                  new=AsyncMock(return_value=None)),
        ):
            result = _run(svc.handle_parsed_event(
                parsed=parsed,
                connector_entry=connector_entry,
                credentials=credentials,
            ))

        # Should not crash — error is caught and reported
        self.assertFalse(result["triggered"])
        self.assertIn("sage_ingress_dm_error", result["reason"])
        self.assertIn("simulated failure", result["reason"])

    def test_silent_reply_not_sent(self):
        """When execute_sage_turn returns [SILENT], no DM is sent."""
        from server_modules.connectors import discord_bot_runtime_service

        parsed = self._make_parsed_dm(text="ok")
        connector_entry = self._make_connector_entry()
        credentials = self._make_credentials()

        sage_result = MagicMock()
        sage_result.message = "[SILENT]"

        svc = discord_bot_runtime_service.DiscordBotRuntimeService(
            load_vault=lambda: {},
            resolve_vault_credential=lambda p, ws: credentials,
            append_event=lambda **kw: None,
        )

        with (
            patch("server_modules.connectors.discord_bot_runtime_service.event_matches_connector",
                  return_value=True),
            patch("server_modules.connectors.discord_bot_runtime_service.should_trigger_agent_run",
                  return_value=True),
            patch("server_modules.agent_turn_adapter.execute_sage_turn",
                  new=AsyncMock(return_value=sage_result)),
            patch("server_modules.connectors.discord_connector.send_dm") as mock_send_dm,
            patch("server_modules.agent_command_dispatcher.dispatch_command",
                  new=AsyncMock(return_value=None)),
        ):
            result = _run(svc.handle_parsed_event(
                parsed=parsed,
                connector_entry=connector_entry,
                credentials=credentials,
            ))

        # Triggered=True (we processed it) but no DM sent
        self.assertTrue(result["triggered"])
        self.assertEqual(result["reason"], "sage_ingress_dm_no_reply")
        mock_send_dm.assert_not_called()
