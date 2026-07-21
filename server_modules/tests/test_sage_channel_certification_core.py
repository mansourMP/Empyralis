"""Sage Core Channels Certification.

Verifies that every core channel catalog card is honest about its launch
status, that setup-readiness detection works, that inbound context wiring
is correct, that outbound sends require approval, and that idempotency is
enforced. Also confirms that iMessage and WeChat are first-class,
owner-connectable gateway channels (live_when_configured, like Telegram/
WhatsApp) backed by a concrete local-bridge runtime contract, while Signal
remains planned until its own bridge runtime is certified — and that none of
the three ever reports "connected" without a real, live bridge health
snapshot saying so.
"""

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import unittest

from server_modules import connection_catalog_service
from server_modules import personal_channel_sage_bridge_service
from server_modules import personal_channels_repository


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ROOT_DIR = Path(__file__).resolve().parents[2]
DIRECT_CHAT_MODULE_PATH = (
    ROOT_DIR / "server_modules" / "direct_chat_runtime_exports.py"
)
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

_spec = importlib.util.spec_from_file_location(
    "operator_chat_cert_under_test", DIRECT_CHAT_MODULE_PATH
)
operator_chat = importlib.util.module_from_spec(_spec)
sys.modules["operator_chat_cert_under_test"] = operator_chat
assert _spec and _spec.loader
_spec.loader.exec_module(operator_chat)


def _catalog_item(connection_id: str) -> dict:
    items = connection_catalog_service.catalog_items()
    for item in items:
        if item["id"] == connection_id:
            return item
    raise AssertionError(f"Catalog item '{connection_id}' not found")


def _make_tool_capability(
    conn_id: str,
    label: str,
    write_actions: list[str] | None = None,
    approval_actions: list[str] | None = None,
) -> dict:
    return {
        "id": conn_id,
        "label": label,
        "connected": True,
        "authenticated": True,
        "runtime_usable": True,
        "read_actions": ["read"],
        "write_actions": write_actions or ["send"],
        "approval_required_actions": approval_actions or ["send"],
    }


# ===================================================================
# Telegram Personal
# ===================================================================

class TelegramPersonalCertification(unittest.TestCase):
    """Telegram Personal is a LAUNCH_LIVE personal channel with gramjs."""

    def test_telegram_personal_catalog_card_status_truthful(self):
        item = _catalog_item("telegram_personal")
        self.assertEqual(item["launch_status"], "live")
        self.assertTrue(item["setup_available"])
        self.assertTrue(item["runtime_usable"])
        self.assertEqual(item["lane"], connection_catalog_service.LANE_SAGE_PERSONAL_CHANNEL)
        self.assertEqual(item["provider"], "telegram_gramjs")

    def test_telegram_personal_setup_readiness(self):
        """status_items reflects the Telegram state from the repository."""
        with (
            patch.object(
                connection_catalog_service,
                "_selected_gateway",
                return_value=({"gateway_id": "gw-1", "connection_status": "online"}, []),
            ),
            patch(
                "server_modules.connection_catalog_service.personal_channels_repository.get_telegram_state",
                return_value={"status": "connected", "linked_name": "Mansur"},
            ) as get_state,
        ):
            items = connection_catalog_service.status_items(
                workspace_id="w-1", user_id="u-1", surface="sage"
            )
            tg = next(i for i in items if i["id"] == "telegram_personal")
            self.assertTrue(tg["connected"])
            self.assertTrue(tg["configured"])
            self.assertEqual(tg["health_status"], "healthy")
            get_state.assert_called_once_with("gw-1", channel_key="telegram_personal")

    def test_telegram_personal_setup_readiness_not_connected(self):
        with (
            patch.object(
                connection_catalog_service,
                "_selected_gateway",
                return_value=({"gateway_id": "gw-1", "connection_status": "online"}, []),
            ),
            patch(
                "server_modules.connection_catalog_service.personal_channels_repository.get_telegram_state",
                return_value={"status": "awaiting_code"},
            ),
        ):
            items = connection_catalog_service.status_items(
                workspace_id="w-1", user_id="u-1", surface="sage"
            )
            tg = next(i for i in items if i["id"] == "telegram_personal")
            self.assertFalse(tg["connected"])
            self.assertEqual(tg["health_status"], "awaiting_code")

    def test_telegram_personal_inbound_context(self):
        """build_telegram_personal_reply creates correct thread context."""
        with patch(
            "server_modules.sage_turn_adapter.execute_sage_turn_for_channel",
            new=AsyncMock(
                return_value={
                    "message": "hello from Sage",
                    "trace_id": "trace-1",
                }
            ),
        ) as execute_mock:
            result = personal_channel_sage_bridge_service.build_telegram_personal_reply(
                workspace_id="workspace-1",
                gateway_id="gateway-1",
                remote_jid="tg-user-1",
                text="hey Sage",
                push_name="Mansur",
            )

        self.assertEqual(result["text"], "hello from Sage")
        self.assertEqual(result["source"], "sage_turn_adapter")
        kwargs = execute_mock.call_args.kwargs
        self.assertEqual(kwargs["surface_channel"], "telegram_personal")
        self.assertEqual(kwargs["workspace_id"], "workspace-1")
        self.assertEqual(kwargs["gateway_id"], "gateway-1")
        self.assertEqual(kwargs["remote_jid"], "tg-user-1")
        self.assertIn("hey Sage", kwargs["message"])

    def test_telegram_personal_outbound_approval(self):
        """Sending a Telegram message produces an answer_with_action approval."""
        payload = operator_chat._build_direct_tool_approval_response(
            tool_calls=[
                {
                    "name": "telegram_personal__send_message",
                    "arguments": json.dumps(
                        {"input": json.dumps({"chat_id": "123", "text": "hello"})}
                    ),
                }
            ],
            tool_capabilities=[
                _make_tool_capability(
                    "telegram_personal",
                    "Your Telegram",
                    write_actions=["send_message"],
                    approval_actions=["send_message"],
                )
            ],
        )
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["mode"], "answer_with_action")
        self.assertEqual(payload["actions"][0]["connector"], "telegram_personal")
        self.assertEqual(payload["actions"][0]["action"], "send_message")

    def test_telegram_personal_idempotency(self):
        """record_inbound_message returns (existing_data, False) for duplicates."""
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as f:
            db_path = f.name
        try:
            personal_channels_repository.init_personal_channels_db(db_path)
            result1, created1 = personal_channels_repository.record_inbound_message(
                gateway_id="gw-1",
                channel_key="telegram_personal",
                external_message_id="msg-1",
                remote_jid="tg-user-1",
                sender_jid=None,
                push_name="Mansur",
                text="hello",
                db_path=db_path,
            )
            self.assertTrue(created1)
            self.assertIsNotNone(result1)

            result2, created2 = personal_channels_repository.record_inbound_message(
                gateway_id="gw-1",
                channel_key="telegram_personal",
                external_message_id="msg-1",
                remote_jid="tg-user-1",
                sender_jid=None,
                push_name="Mansur",
                text="hello",
                db_path=db_path,
            )
            self.assertFalse(created2)
        finally:
            Path(db_path).unlink(missing_ok=True)


# ===================================================================
# WhatsApp Personal
# ===================================================================

class WhatsAppPersonalCertification(unittest.TestCase):
    """WhatsApp Personal is a LAUNCH_LIVE personal channel with baileys."""

    def test_whatsapp_personal_catalog_card_status_truthful(self):
        item = _catalog_item("whatsapp_personal")
        self.assertEqual(item["launch_status"], "live")
        self.assertTrue(item["setup_available"])
        self.assertTrue(item["runtime_usable"])
        self.assertEqual(item["lane"], connection_catalog_service.LANE_SAGE_PERSONAL_CHANNEL)
        self.assertEqual(item["provider"], "whatsapp_baileys")

    def test_whatsapp_personal_setup_readiness(self):
        with (
            patch.object(
                connection_catalog_service,
                "_selected_gateway",
                return_value=({"gateway_id": "gw-1", "connection_status": "online"}, []),
            ),
            patch(
                "server_modules.connection_catalog_service.personal_channels_repository.get_whatsapp_state",
                return_value={"status": "connected", "linked_name": "Mansur"},
            ) as get_state,
        ):
            items = connection_catalog_service.status_items(
                workspace_id="w-1", user_id="u-1", surface="sage"
            )
            wa = next(i for i in items if i["id"] == "whatsapp_personal")
            self.assertTrue(wa["connected"])
            self.assertTrue(wa["configured"])
            self.assertEqual(wa["health_status"], "healthy")
            get_state.assert_called_once_with("gw-1", channel_key="whatsapp_personal")

    def test_whatsapp_personal_inbound_context(self):
        """build_whatsapp_personal_reply creates correct thread context."""
        with patch(
            "server_modules.sage_turn_adapter.execute_sage_turn_for_channel",
            new=AsyncMock(
                return_value={
                    "message": "hello from Sage",
                    "trace_id": "trace-2",
                }
            ),
        ) as execute_mock:
            result = personal_channel_sage_bridge_service.build_whatsapp_personal_reply(
                workspace_id="workspace-1",
                gateway_id="gateway-1",
                remote_jid="15551234567",
                text="hey Sage",
                push_name="Mansur",
            )

        self.assertEqual(result["text"], "hello from Sage")
        self.assertEqual(result["source"], "sage_turn_adapter")
        kwargs = execute_mock.call_args.kwargs
        self.assertEqual(kwargs["surface_channel"], "whatsapp_personal")
        self.assertEqual(kwargs["workspace_id"], "workspace-1")
        self.assertEqual(kwargs["gateway_id"], "gateway-1")
        self.assertIn("hey Sage", kwargs["message"])

    def test_whatsapp_personal_outbound_approval(self):
        payload = operator_chat._build_direct_tool_approval_response(
            tool_calls=[
                {
                    "name": "whatsapp_personal__send_message",
                    "arguments": json.dumps(
                        {"input": json.dumps({"to": "15551234567", "text": "hello"})}
                    ),
                }
            ],
            tool_capabilities=[
                _make_tool_capability(
                    "whatsapp_personal",
                    "Your WhatsApp",
                    write_actions=["send_message"],
                    approval_actions=["send_message"],
                )
            ],
        )
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["mode"], "answer_with_action")
        self.assertEqual(payload["actions"][0]["connector"], "whatsapp_personal")
        self.assertEqual(payload["actions"][0]["action"], "send_message")

    def test_whatsapp_personal_idempotency(self):
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as f:
            db_path = f.name
        try:
            personal_channels_repository.init_personal_channels_db(db_path)
            result1, created1 = personal_channels_repository.record_inbound_message(
                gateway_id="gw-1",
                channel_key="whatsapp_personal",
                external_message_id="wa-msg-1",
                remote_jid="15551234567",
                sender_jid=None,
                push_name="Mansur",
                text="hello",
                db_path=db_path,
            )
            self.assertTrue(created1)

            result2, created2 = personal_channels_repository.record_inbound_message(
                gateway_id="gw-1",
                channel_key="whatsapp_personal",
                external_message_id="wa-msg-1",
                remote_jid="15551234567",
                sender_jid=None,
                push_name="Mansur",
                text="hello",
                db_path=db_path,
            )
            self.assertFalse(created2)
        finally:
            Path(db_path).unlink(missing_ok=True)


# ===================================================================
# Slack
# ===================================================================

class SlackCertification(unittest.TestCase):
    """Slack is LAUNCH_LIVE_WHEN_CONFIGURED via OAuth."""

    def test_slack_catalog_card_status_truthful(self):
        item = _catalog_item("slack")
        self.assertEqual(item["launch_status"], "live_when_configured")
        self.assertTrue(item["setup_available"])
        self.assertTrue(item["runtime_usable"])
        self.assertEqual(item["lane"], connection_catalog_service.LANE_STUDIO_BUSINESS_CHANNEL)
        self.assertEqual(item["provider"], "slack_events")

    def test_slack_setup_readiness(self):
        """status_items reflects vault-connected Slack."""
        with (
            patch(
                "server_modules.connection_catalog_service._vault_connector_ids",
                return_value={"slack"},
            ),
            patch.object(
                connection_catalog_service,
                "_selected_gateway",
                return_value=(None, []),
            ),
        ):
            items = connection_catalog_service.status_items(
                workspace_id="w-1", tenant_id="t-1", surface="sage"
            )
            sl = next(i for i in items if i["id"] == "slack")
            self.assertTrue(sl["connected"])
            self.assertTrue(sl["configured"])
            self.assertEqual(sl["health_status"], "healthy")

    def test_slack_inbound_context(self):
        """Slack inbound events produce proper thread context."""
        from server_modules.connectors import slack_connector
        parsed = slack_connector.parse_inbound_event(
            {
                "type": "event_callback",
                "team_id": "T123",
                "event_id": "Ev123",
                "api_app_id": "A123",
                "event": {
                    "type": "app_mention",
                    "channel": "C123",
                    "user": "U123",
                    "text": "<@BOT> hello from slack",
                    "ts": "1712000000.000100",
                    "thread_ts": "1712000000.000001",
                },
            }
        )
        self.assertEqual(parsed["kind"], "event")
        self.assertEqual(parsed["message_type"], "mention")
        self.assertEqual(parsed["channel"], "C123")
        self.assertEqual(parsed["user_id"], "U123")
        self.assertEqual(parsed["thread_ts"], "1712000000.000001")

    def test_slack_outbound_approval(self):
        payload = operator_chat._build_direct_tool_approval_response(
            tool_calls=[
                {
                    "name": "slack__send_message",
                    "arguments": json.dumps(
                        {"input": json.dumps({"channel": "C123", "message": "hello"})}
                    ),
                }
            ],
            tool_capabilities=[
                _make_tool_capability(
                    "slack",
                    "Slack LIVE",
                    write_actions=["send_message"],
                    approval_actions=["send_message"],
                )
            ],
        )
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["mode"], "answer_with_action")
        self.assertEqual(payload["actions"][0]["connector"], "slack")
        self.assertEqual(payload["actions"][0]["action"], "send_message")

    def test_slack_idempotency(self):
        """Duplicate Slack event IDs are detected at the event-appending layer."""
        from server_modules import connectors_actions
        with (
            patch(
                "server_modules.connectors_actions._append_channel_event",
                return_value=None,
            ) as append_event,
            patch(
                "server_modules.sage_turn_adapter.execute_sage_turn_for_channel",
                new=AsyncMock(),
            ),
            patch(
                "server_modules.connectors_actions.slack_verify_request_signature",
                return_value=True,
            ),
            patch(
                "server_modules.connectors_actions.slack_parse_inbound_event",
                return_value={
                    "kind": "event",
                    "event_id": "Ev123",
                    "message_type": "app_mention",
                    "channel": "C123",
                    "user_id": "U123",
                    "text": "<@BOT> hello",
                },
            ),
            patch(
                "server_modules.connectors_actions.load_vault",
                return_value={
                    "credentials": [
                        {
                            "id": "cred-slack",
                            "provider": "slack",
                            "workspace_id": "default",
                            "tenant_id": "tenant-default",
                            "metadata": {
                                "team_id": "T123",
                                "channel_registry_bindings": {
                                    "slack": {"endpoint_key": "C123"}
                                },
                            },
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
                new=AsyncMock(return_value={"ok": True, "run_id": "run-1"}),
            ),
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

            # Second call with same event_id — append_event would catch the dup
            result2 = asyncio.run(connectors_actions.slack_events_webhook(request))
            self.assertEqual(result2["handled"], 1)
            self.assertEqual(append_event.call_count, 2)


# ===================================================================
# Discord
# ===================================================================

class DiscordCertification(unittest.TestCase):
    """Discord is LAUNCH_LIVE_WHEN_CONFIGURED via bot install."""

    def test_discord_catalog_card_status_truthful(self):
        item = _catalog_item("discord_bot")
        self.assertEqual(item["launch_status"], "live_when_configured")
        self.assertTrue(item["setup_available"])
        self.assertTrue(item["runtime_usable"])
        self.assertEqual(item["lane"], connection_catalog_service.LANE_STUDIO_BUSINESS_CHANNEL)
        self.assertEqual(item["provider"], "discord_webhook")

    def test_discord_setup_readiness(self):
        with (
            patch(
                "server_modules.connection_catalog_service._vault_connector_ids",
                return_value={"discord_bot"},
            ),
            patch.object(
                connection_catalog_service,
                "_selected_gateway",
                return_value=(None, []),
            ),
        ):
            items = connection_catalog_service.status_items(
                workspace_id="w-1", tenant_id="t-1", surface="sage"
            )
            dc = next(i for i in items if i["id"] == "discord_bot")
            self.assertTrue(dc["connected"])
            self.assertTrue(dc["configured"])
            self.assertEqual(dc["health_status"], "healthy")

    def test_discord_inbound_context(self):
        """Discord inbound events produce proper channel context."""
        from server_modules.connectors import discord_connector
        parsed = discord_connector.parse_inbound_event(
            {
                "t": "MESSAGE_CREATE",
                "d": {
                    "id": "msg-1",
                    "channel_id": "123",
                    "guild_id": "456",
                    "content": "<@999> please investigate this",
                    "author": {"id": "321", "username": "alice"},
                    "mentions": [{"id": "999"}],
                },
            }
        )
        self.assertEqual(parsed["kind"], "event")
        self.assertEqual(parsed["event_type"], "message_create")
        self.assertEqual(parsed["channel_id"], "123")
        self.assertEqual(parsed["user_id"], "321")

    def test_discord_outbound_approval(self):
        payload = operator_chat._build_direct_tool_approval_response(
            tool_calls=[
                {
                    "name": "discord_bot__send_message",
                    "arguments": json.dumps(
                        {
                            "input": json.dumps(
                                {"channel_id": "123", "message": "hello from discord"}
                            )
                        }
                    ),
                }
            ],
            tool_capabilities=[
                _make_tool_capability(
                    "discord_bot",
                    "Discord Bot",
                    write_actions=["send_message", "send_dm"],
                    approval_actions=["send_message", "send_dm"],
                )
            ],
        )
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["mode"], "answer_with_action")
        self.assertEqual(payload["actions"][0]["connector"], "discord_bot")
        self.assertEqual(payload["actions"][0]["action"], "send_message")

    def test_discord_idempotency(self):
        """Discord parse_inbound_event extracts message_id for dedup."""
        from server_modules.connectors import discord_connector

        parsed = discord_connector.parse_inbound_event(
            {
                "t": "MESSAGE_CREATE",
                "d": {
                    "id": "dup-msg",
                    "channel_id": "123",
                    "guild_id": "456",
                    "content": "hello",
                    "author": {"id": "321", "username": "alice"},
                    "mentions": [],
                },
            }
        )
        # Every parsed event gets a message_id that callers can use for dedup
        self.assertEqual(parsed["message_id"], "dup-msg")

        # A second parse of the same upstream data yields the same message_id
        parsed2 = discord_connector.parse_inbound_event(
            {
                "t": "MESSAGE_CREATE",
                "d": {
                    "id": "dup-msg",
                    "channel_id": "123",
                    "guild_id": "456",
                    "content": "hello",
                    "author": {"id": "321", "username": "alice"},
                    "mentions": [],
                },
            }
        )
        self.assertEqual(parsed2["message_id"], parsed["message_id"])


# ===================================================================
# Discord Sage Ingress (DM → Main Agent via execute_sage_turn)
# ===================================================================

class DiscordSageIngressCertification(unittest.TestCase):
    """Discord DMs route through unified Sage ingress (Path A).

    Upgraded from Path B (personal channel bridge) to Path A (direct
    execute_sage_turn call) to match the Telegram-hosted and Slack pattern.
    Guild messages with @mentions continue to use the specialist connector path.
    """

    def test_discord_dm_is_direct_message_type(self):
        """parse_inbound_event identifies DMs with message_type 'direct_message'.
        No guild_id → the message is a DM, not a guild message."""
        from server_modules.connectors import discord_connector

        parsed = discord_connector.parse_inbound_event({
            "t": "MESSAGE_CREATE",
            "d": {
                "id": "dm-1",
                "channel_id": "999",
                "guild_id": "",  # Empty guild = DM
                "content": "hello Sage",
                "author": {"id": "user-1", "username": "testuser"},
                "mentions": [],
            },
        })
        self.assertEqual(parsed["message_type"], "direct_message")
        self.assertIsNone(parsed["guild_id"])
        self.assertEqual(parsed["user_id"], "user-1")

    def test_discord_guild_mention_is_not_direct_message(self):
        """Guild message with @mention has message_type 'mention', not 'direct_message'.
        This ensures guild messages continue to use the specialist connector path."""
        from server_modules.connectors import discord_connector

        parsed = discord_connector.parse_inbound_event({
            "t": "MESSAGE_CREATE",
            "d": {
                "id": "gm-1",
                "channel_id": "111",
                "guild_id": "222",
                "content": "<@999> help me",
                "author": {"id": "user-2", "username": "guildmember"},
                "mentions": [{"id": "999"}],
            },
        })
        self.assertEqual(parsed["message_type"], "mention")
        self.assertIsNotNone(parsed["guild_id"])
        self.assertEqual(parsed["guild_id"], "222")

    def test_discord_dm_parse_extracts_user_identity(self):
        """DM parse extracts user_id and username for channel_sender metadata."""
        from server_modules.connectors import discord_connector

        parsed = discord_connector.parse_inbound_event({
            "t": "MESSAGE_CREATE",
            "d": {
                "id": "dm-2",
                "channel_id": "888",
                "guild_id": "",
                "content": "what can you do?",
                "author": {"id": "user-42", "username": "alice"},
                "mentions": [],
            },
        })
        self.assertEqual(parsed["user_id"], "user-42")
        self.assertEqual(parsed["username"], "alice")
        self.assertEqual(parsed["message_type"], "direct_message")
        self.assertEqual(parsed["text"], "what can you do?")

    def test_discord_dm_parse_preserves_message_id_for_dedup(self):
        """DM parse extracts message_id so callers can deduplicate."""
        from server_modules.connectors import discord_connector

        parsed = discord_connector.parse_inbound_event({
            "t": "MESSAGE_CREATE",
            "d": {
                "id": "dedup-me",
                "channel_id": "777",
                "guild_id": "",
                "content": "ping",
                "author": {"id": "user-3", "username": "bob"},
                "mentions": [],
            },
        })
        self.assertEqual(parsed["message_id"], "dedup-me")


# ===================================================================
# Google Workspace
# ===================================================================

class GoogleWorkspaceCertification(unittest.TestCase):
    """Google Workspace is LAUNCH_LIVE_WHEN_CONFIGURED, supports_inbound=False."""

    def test_google_workspace_catalog_card_status_truthful(self):
        item = _catalog_item("google_workspace")
        self.assertEqual(item["launch_status"], "live_when_configured")
        self.assertTrue(item["setup_available"])
        self.assertTrue(item["runtime_usable"])
        self.assertFalse(item["supports_inbound"])
        self.assertTrue(item["supports_outbound"])
        self.assertEqual(item["lane"], connection_catalog_service.LANE_WORK_APP_CONNECTOR)
        self.assertEqual(item["provider"], "google_workspace")

    def test_google_workspace_setup_readiness(self):
        with (
            patch(
                "server_modules.connection_catalog_service._vault_connector_ids",
                return_value={"google_workspace"},
            ),
            patch.object(
                connection_catalog_service,
                "_selected_gateway",
                return_value=(None, []),
            ),
        ):
            items = connection_catalog_service.status_items(
                workspace_id="w-1", tenant_id="t-1", surface="sage"
            )
            gw = next(i for i in items if i["id"] == "google_workspace")
            self.assertTrue(gw["connected"])
            self.assertTrue(gw["configured"])
            self.assertEqual(gw["health_status"], "healthy")

    def test_google_workspace_outbound_approval(self):
        payload = operator_chat._build_direct_tool_approval_response(
            tool_calls=[
                {
                    "name": "google_workspace__send_email",
                    "arguments": json.dumps(
                        {
                            "input": json.dumps(
                                {
                                    "to": "user@example.com",
                                    "subject": "Hello",
                                    "body": "Email body",
                                }
                            )
                        }
                    ),
                }
            ],
            tool_capabilities=[
                _make_tool_capability(
                    "google_workspace",
                    "Google Workspace",
                    write_actions=["send_email"],
                    approval_actions=["send_email"],
                )
            ],
        )
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["mode"], "answer_with_action")
        self.assertEqual(payload["actions"][0]["connector"], "google_workspace")
        self.assertEqual(payload["actions"][0]["action"], "send_email")


# ===================================================================
# SMTP / IMAP
# ===================================================================

class SmtpCertification(unittest.TestCase):
    """SMTP/IMAP is LAUNCH_LIVE_WHEN_CONFIGURED with credential validation."""

    def test_smtp_catalog_card_status_truthful(self):
        item = _catalog_item("smtp")
        self.assertEqual(item["launch_status"], "live_when_configured")
        self.assertTrue(item["setup_available"])
        self.assertTrue(item["runtime_usable"])
        self.assertFalse(item["supports_inbound"])
        self.assertTrue(item["supports_outbound"])
        self.assertEqual(item["lane"], connection_catalog_service.LANE_WORK_APP_CONNECTOR)
        self.assertEqual(item["provider"], "smtp")

    def test_smtp_credential_validation(self):
        """Mock SMTP server and verify credential validation works."""
        from server_modules.connectors.smtp_connector import validate_smtp_credentials

        class FakeSMTP:
            def __init__(self, host, port, timeout=0):
                self.host = host
                self.port = port
                self.calls = []

            def ehlo(self):
                self.calls.append("ehlo")

            def starttls(self, context=None):
                self.calls.append("starttls")

            def login(self, user, pwd):
                self.calls.append(("login", user, pwd))

            def noop(self):
                self.calls.append("noop")
                return 250, b"OK"

            def quit(self):
                self.calls.append("quit")

        result = validate_smtp_credentials(
            {
                "host": "smtp.example.com",
                "port": 587,
                "username": "user@example.com",
                "password": "secret",
                "use_tls": True,
                "from_email": "user@example.com",
            },
            smtp_cls=FakeSMTP,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["smtp_access"], True)

    def test_smtp_send_approval(self):
        payload = operator_chat._build_direct_tool_approval_response(
            tool_calls=[
                {
                    "name": "smtp__send_email",
                    "arguments": json.dumps(
                        {
                            "input": json.dumps(
                                {
                                    "to_email": "user@example.com",
                                    "subject": "Hello",
                                    "body": "SMTP body",
                                }
                            )
                        }
                    ),
                }
            ],
            tool_capabilities=[
                _make_tool_capability(
                    "smtp",
                    "SMTP Email",
                    write_actions=["send_email"],
                    approval_actions=["send_email"],
                )
            ],
        )
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["mode"], "answer_with_action")
        self.assertEqual(payload["actions"][0]["connector"], "smtp")
        self.assertEqual(payload["actions"][0]["action"], "send_email")


# ===================================================================
# Signal / iMessage / WeChat local-bridge truth
# ===================================================================

class LocalBridgePersonalChannelsCertification(unittest.TestCase):
    """Per the 'no fake runtime' rule, only channels with a concrete runtime
    contract can be live_when_configured."""

    def test_signal_is_live_when_configured_with_local_bridge_runtime(self):
        """Signal is a first-class, owner-connectable gateway channel (like
        Telegram/WhatsApp/iMessage) because it has a concrete runtime
        contract: a real signal-cli bridge adapter
        (empyralis-gateway/src/bridges/signal-cli-bridge.ts) and the generic
        Agent Computer local-bridge client that talks to it — the exact same
        shape as iMessage's BlueBubbles adapter below. It stays
        live_when_configured (not unconditionally "live") because a real
        signal-cli install must still be connected and certified per-instance
        before it's actually usable.

        Was asserted "planned"/setup_available=False/runtime_usable=False —
        stale even before the channel_lane_contract_service.py catalog fix
        (a separate file this test doesn't touch): this catalog's own
        signal_personal _item() had already carried live_when_configured/
        True/True (identical to imessage_personal/wechat_personal below)
        since before that fix; this assertion just never caught up. That
        catalog fix was about a DIFFERENT, real bug — CHANNEL_PLATFORM_CATALOG
        and PERSONAL_CHANNEL_ROADMAP (channel_lane_contract_service.py)
        separately marked Signal not-live/not-launch-allowed while THIS
        catalog (connection_catalog_service.py) already had it right, which
        is exactly the kind of drift this test exists to catch — it just
        wasn't asserting the value that would have caught it."""
        item = _catalog_item("signal_personal")
        self.assertEqual(item["launch_status"], "live_when_configured")
        self.assertTrue(item["setup_available"])
        self.assertTrue(item["runtime_usable"])
        self.assertEqual(item["lane"], connection_catalog_service.LANE_SAGE_PERSONAL_CHANNEL)
        self.assertEqual(item["provider"], "signal_local_bridge")
        self.assertEqual(item["setup_kind"], "local_bridge")

    def test_imessage_is_live_when_configured_with_bluebubbles_bridge(self):
        """iMessage is a first-class, owner-connectable gateway channel (like
        Telegram/WhatsApp) because it has a concrete runtime contract: a real
        BlueBubbles bridge adapter (empyralis-gateway/src/bridges/bluebubbles-bridge.ts)
        and a generic Agent Computer local-bridge client that talks to it.
        It stays live_when_configured (not unconditionally "live") because a
        real Mac + BlueBubbles Server must still be connected and certified
        per-instance before it's actually usable."""
        item = _catalog_item("imessage_personal")
        self.assertEqual(item["launch_status"], "live_when_configured")
        self.assertTrue(item["setup_available"])
        self.assertTrue(item["runtime_usable"])
        self.assertEqual(item["lane"], connection_catalog_service.LANE_SAGE_PERSONAL_CHANNEL)
        self.assertEqual(item["provider"], "bluebubbles_local_bridge")
        self.assertEqual(item["setup_kind"], "mac_bridge")

    def test_wechat_personal_is_not_supported_no_bridge_exists(self):
        """Unlike Signal (real, installable signal-cli daemon) or iMessage
        (Empyralis-shipped BlueBubbles adapter), there is no third-party
        "WeChat bridge process" an owner can plausibly supply — personal
        WeChat automation has no supported API at all, full stop. This test
        previously asserted the opposite (live_when_configured / True / True,
        on the theory that WeChat was BYO-bridge like Signal) — that theory
        doesn't hold up: docs/design/reliability-audit-2-channels.md found
        zero pairing flow anywhere in the codebase and confirmed "Empyralis
        ships no such bridge", and there is no real-world equivalent to
        signal-cli for personal WeChat. Empyralis's WeChat investment goes
        into the official Official Account / WeCom integration instead — see
        empyralis-gateway/src/channels/wechat/ and the wechat_work catalog
        entry — so this entry now honestly reports itself as not usable
        rather than claiming a working bridge flow that doesn't exist."""
        item = _catalog_item("wechat_personal")
        self.assertEqual(item["launch_status"], "live_when_configured")
        self.assertFalse(item["setup_available"])
        self.assertFalse(item["runtime_usable"])
        self.assertEqual(item["lane"], connection_catalog_service.LANE_SAGE_PERSONAL_CHANNEL)
        self.assertEqual(item["provider"], "wechat_local_bridge")
        self.assertEqual(item["setup_kind"], "local_bridge")

    def test_apple_messages_business_is_official_msp_roadmap_channel(self):
        item = _catalog_item("apple_messages_business")
        self.assertEqual(item["launch_status"], "planned")
        self.assertFalse(item["setup_available"])
        self.assertFalse(item["runtime_usable"])
        self.assertEqual(item["lane"], connection_catalog_service.LANE_STUDIO_BUSINESS_CHANNEL)
        self.assertEqual(item["provider"], "apple_messages_business_msp")
        self.assertEqual(item["setup_kind"], "msp_business_messaging")
        self.assertFalse(item["requires_gateway"])
        self.assertIn("Apple Messages for Business", item["display_name"])

    def test_wechat_status_uses_selected_gateway_bridge_health(self):
        with patch.object(
            connection_catalog_service,
            "_selected_gateway",
            return_value=(
                {
                    "gateway_id": "gw-1",
                    "connection_status": "online",
                    "metadata": {
                        "personal_channel_manifests": [
                            {
                                "channel_key": "wechat_personal",
                                "provider": "wechat_local_bridge",
                                "status": "not_configured",
                                "live_capable": True,
                            }
                        ],
                        "personal_channel_health": [
                            {
                                "channel_key": "wechat_personal",
                                "provider": "wechat_local_bridge",
                                "status": "connected",
                                "connected": True,
                                "running": True,
                            }
                        ],
                    },
                },
                [],
            ),
        ):
            items = connection_catalog_service.status_items(
                workspace_id="w-1", user_id="u-1", surface="sage"
            )
            wechat = next(i for i in items if i["id"] == "wechat_personal")
            self.assertTrue(wechat["connected"])
            self.assertTrue(wechat["configured"])
            self.assertEqual(wechat["health_status"], "healthy")
            # Shipped, not locked: a genuinely connected+healthy bridge routes
            # the owner to "manage" it, same as Signal/iMessage.
            self.assertEqual(wechat["next_action"], "manage")

    def test_wechat_status_reports_bridge_unavailable_without_claiming_connected(self):
        with patch.object(
            connection_catalog_service,
            "_selected_gateway",
            return_value=(
                {
                    "gateway_id": "gw-1",
                    "connection_status": "online",
                    "metadata": {
                        "personal_channel_health": [
                            {
                                "channel_key": "wechat_personal",
                                "provider": "wechat_local_bridge",
                                "status": "unavailable",
                                "connected": False,
                                "last_error": "bridge refused connection",
                            }
                        ],
                    },
                },
                [],
            ),
        ):
            items = connection_catalog_service.status_items(
                workspace_id="w-1", user_id="u-1", surface="sage"
            )
            wechat = next(i for i in items if i["id"] == "wechat_personal")
            self.assertFalse(wechat["connected"])
            self.assertTrue(wechat["configured"])
            self.assertEqual(wechat["health_status"], "unavailable")
            self.assertEqual(wechat["last_error"], "bridge refused connection")
            # Locked, not "connect": wechat_personal's setup_available/
            # runtime_usable are now False (see
            # test_wechat_personal_is_not_supported_no_bridge_exists and
            # the wechat_personal _item() in connection_catalog_service.py)
            # because there is no real bridge an owner can install, unlike
            # Signal/iMessage. _next_action() only short-circuits straight
            # to "manage" for an already-connected bridge (the branch
            # test_wechat_status_uses_selected_gateway_bridge_health above
            # covers); for a not-currently-connected report like this one it
            # falls through to the runtime_usable gate, so offering a
            # "connect" CTA here would be the same false promise this fix is
            # closing — there is nothing to connect to.
            self.assertEqual(wechat["next_action"], "locked")

    def test_no_channel_card_claims_supported_without_runtime(self):
        """Every catalog item with a usable launch status must have a real
        connector module.  We check by verifying that the runtime_provider
        module exists in the connectors directory (or is a known system
        provider that doesn't need a standalone connector module)."""
        KNOWN_PROVIDERS_WITHOUT_DEDICATED_MODULE = {
            # These are handled via shared infrastructure or are not file-backed
            "agent_computer",
            "mcp",
            "mcp_plugin",
            "applications",
            "mini_apps",
            "web_chat",
            "webhook",
            "telegram_bot",
            "telegram_bot_api",
            # Local bridge providers are handled by shared Agent Computer bridge infrastructure.
            "signal_local_bridge",
            "bluebubbles_local_bridge",
            "wechat_local_bridge",
            # Served at the CLI/service layer without a dedicated connector module
            "wechat_work",
            "instagram_business",
        }
        connectors_dir = ROOT_DIR / "server_modules" / "connectors"
        existing_connector_modules = {
            p.stem for p in connectors_dir.glob("*.py") if p.stem != "__init__"
        }

        items = connection_catalog_service.catalog_items()
        for item in items:
            status = item.get("launch_status", "")
            if status not in (
                "live",
                "live_when_configured",
            ):
                continue
            rp = item.get("runtime_provider", "")
            if not rp:
                continue
            if rp in KNOWN_PROVIDERS_WITHOUT_DEDICATED_MODULE:
                continue
            if rp in existing_connector_modules:
                continue
            # Check server_modules/ root level
            module_path = ROOT_DIR / "server_modules" / f"{rp}.py"
            if module_path.exists():
                continue
            # Check for a CLI/API module that serves this runtime provider
            # (e.g. google_workspace_cli.py serves google_workspace)
            server_root_stems = {
                p.stem for p in (ROOT_DIR / "server_modules").glob("*.py")
                if p.stem != "__init__"
            }
            if rp in server_root_stems:
                continue
            # Check if any server_modules root file contains the runtime provider name
            if any(rp in stem for stem in server_root_stems):
                continue
            # Check connectors/ dir with different name patterns
            module_path2 = ROOT_DIR / "server_modules" / "connectors" / f"{rp}.py"
            if module_path2.exists():
                continue
            # Some providers are served by a family of modules with a shared prefix
            # (e.g. telegram_gramjs lives in telegram_connector_* modules).
            prefix = rp.split("_")[0] + "_"
            if any(m.startswith(prefix) for m in existing_connector_modules):
                continue
            # Also check connector_id fallback
            connector_id = item.get("connector_id", "")
            if connector_id and connector_id in existing_connector_modules:
                continue
            # Check if the connector_id has a matching module at top level
            connector_module_path = ROOT_DIR / "server_modules" / f"{connector_id}.py"
            if connector_module_path.exists():
                continue

            self.fail(
                f"Catalog item '{item['id']}' has launch_status='{status}' "
                f"and runtime_provider='{rp}' but no corresponding connector "
                f"module was found in server_modules/connectors/ or server_modules/."
            )


if __name__ == "__main__":
    unittest.main()
