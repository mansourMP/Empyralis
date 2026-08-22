import importlib
import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from server_modules import (
    agent_channel_router,
    channel_concurrency_service,
    safe_mode_service,
)
from server_modules.agent_manifest import AgentManifest
from server_modules.agent_turn_runtime_contract import SageTurnResult


def _deployed_agent_row(
    *,
    deployment_state: str = "live",
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "id": "dagent_1",
        "tenant_id": "tenant-1",
        "owner_workspace_id": "workspace-1",
        "backing_install_id": "install-specialist",
        "name": "Parts Pro",
        "deployment_state": deployment_state,
        "metadata": dict(metadata or {}),
        "channels": {
            "telegram": {
                "enabled": True,
                "is_inbound_owner": True,
                "endpoint_key": "@partspro_bot",
            }
        },
    }


class AgentChannelRouterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        global agent_channel_router, channel_concurrency_service, safe_mode_service
        agent_channel_router = importlib.import_module("server_modules.agent_channel_router")
        channel_concurrency_service = importlib.import_module("server_modules.channel_concurrency_service")
        safe_mode_service = importlib.import_module("server_modules.safe_mode_service")

    def tearDown(self) -> None:
        safe_mode_service.reset_state_for_tests()






















    # ── Sage-routed studio connector channels ──────────────────────────

    async def test_slack_guild_routes_through_execute_sage_turn(self):
        """Slack Guild webhook calls execute_sage_turn with channel_origin='slack_guild'."""
        with patch(
            "server_modules.sage_turn_adapter.execute_sage_turn",
            new=AsyncMock(return_value=SageTurnResult(
                message="Slack response from Sage.",
                trace_id="trace-slack-1",
                provider="deepseek",
                model="deepseek-chat",
            )),
        ) as execute_mock:
            result = await agent_channel_router.route_inbound_channel_message(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                channel_key="slack",
                endpoint_key="C123456",
                customer_message="Check inventory for brake pads",
                actor_id="U789",
                actor_display_name="Slack User",
                message_id="slack-msg-1",
            )

        self.assertTrue(result["ok"], f"Expected ok=True, got {result}")
        self.assertEqual(result["status"], "completed")
        self.assertIsNotNone(result["run_id"])
        self.assertEqual(result["reply"], "Slack response from Sage.")
        self.assertEqual(result["trace_id"], "trace-slack-1")
        execute_mock.assert_awaited_once()
        _call_kwargs = execute_mock.call_args.kwargs
        self.assertEqual(_call_kwargs["channel_origin"], "slack_guild")
        self.assertEqual(_call_kwargs["message"], "Check inventory for brake pads")
        self.assertEqual(_call_kwargs["channel_sender_id"], "U789")
        self.assertEqual(_call_kwargs["workspace_id"], "workspace-1")

    async def test_discord_guild_routes_through_execute_sage_turn(self):
        """Discord Guild webhook calls execute_sage_turn with channel_origin='discord_guild'."""
        with patch(
            "server_modules.sage_turn_adapter.execute_sage_turn",
            new=AsyncMock(return_value=SageTurnResult(
                message="Discord response from Sage.",
                trace_id="trace-discord-1",
                provider="openai",
                model="gpt-4",
            )),
        ) as execute_mock:
            result = await agent_channel_router.route_inbound_channel_message(
                tenant_id="tenant-2",
                workspace_id="workspace-2",
                channel_key="discord",
                endpoint_key="987654321",
                customer_message="What's the status of order #42?",
                actor_id="discord-user-99",
                actor_display_name="DiscordUser",
                message_id="discord-msg-1",
            )

        self.assertTrue(result["ok"], f"Expected ok=True, got {result}")
        self.assertEqual(result["status"], "completed")
        self.assertIsNotNone(result["run_id"])
        self.assertEqual(result["reply"], "Discord response from Sage.")
        execute_mock.assert_awaited_once()
        _call_kwargs = execute_mock.call_args.kwargs
        self.assertEqual(_call_kwargs["channel_origin"], "discord_guild")
        self.assertEqual(_call_kwargs["message"], "What's the status of order #42?")
        self.assertEqual(_call_kwargs["channel_sender_id"], "discord-user-99")

    async def test_github_routes_through_execute_sage_turn(self):
        """GitHub webhook calls execute_sage_turn with channel_origin='github'."""
        with patch(
            "server_modules.sage_turn_adapter.execute_sage_turn",
            new=AsyncMock(return_value=SageTurnResult(
                message="GitHub response from Sage.",
                trace_id="trace-github-1",
                provider="deepseek",
                model="deepseek-reasoner",
            )),
        ) as execute_mock:
            result = await agent_channel_router.route_inbound_channel_message(
                tenant_id="tenant-3",
                workspace_id="workspace-3",
                channel_key="github",
                endpoint_key="owner/repo",
                customer_message="Review PR #128: fix auth middleware",
                actor_id="github-user-1",
                actor_display_name="GitHubDev",
                message_id="gh-msg-1",
            )

        self.assertTrue(result["ok"], f"Expected ok=True, got {result}")
        self.assertEqual(result["status"], "completed")
        self.assertIsNotNone(result["run_id"])
        self.assertEqual(result["reply"], "GitHub response from Sage.")
        execute_mock.assert_awaited_once()
        _call_kwargs = execute_mock.call_args.kwargs
        self.assertEqual(_call_kwargs["channel_origin"], "github")
        self.assertEqual(_call_kwargs["message"], "Review PR #128: fix auth middleware")
        self.assertEqual(_call_kwargs["channel_sender_id"], "github-user-1")

    async def test_whatsapp_routes_through_execute_sage_turn(self):
        """FIX (channel-audit Defect 2): "whatsapp" was missing from
        _SAGE_CHANNEL_ORIGIN_MAP entirely, so every WhatsApp Business
        "public deployed agent" message (whatsapp_ingress_service.py's
        _dispatch_public_deployed_agent_envelope) fell through to the
        "Unimplemented channels" branch and got channel_unavailable back —
        the turn never reached execute_sage_turn. Mirrors
        test_slack_guild_routes_through_execute_sage_turn /
        test_discord_guild_routes_through_execute_sage_turn /
        test_github_routes_through_execute_sage_turn exactly, proving the
        "authorized" (mapped) case now reaches the model — see
        test_unimplemented_channel_returns_unavailable for the companion
        "unauthorized" (unmapped) case, which must still be refused."""
        with patch(
            "server_modules.sage_turn_adapter.execute_sage_turn",
            new=AsyncMock(return_value=SageTurnResult(
                message="WhatsApp response from Sage.",
                trace_id="trace-whatsapp-1",
                provider="deepseek",
                model="deepseek-chat",
            )),
        ) as execute_mock:
            result = await agent_channel_router.route_inbound_channel_message(
                tenant_id="tenant-4",
                workspace_id="workspace-4",
                channel_key="whatsapp",
                endpoint_key="+15551234567",
                customer_message="What's the status of order #42?",
                actor_id="+15559876543",
                actor_display_name="+15559876543",
                message_id="wa-msg-1",
            )

        self.assertTrue(result["ok"], f"Expected ok=True, got {result}")
        self.assertEqual(result["status"], "completed")
        self.assertIsNotNone(result["run_id"])
        self.assertEqual(result["reply"], "WhatsApp response from Sage.")
        execute_mock.assert_awaited_once()
        _call_kwargs = execute_mock.call_args.kwargs
        self.assertEqual(_call_kwargs["channel_origin"], "whatsapp_twilio")
        self.assertEqual(_call_kwargs["message"], "What's the status of order #42?")
        self.assertEqual(_call_kwargs["channel_sender_id"], "+15559876543")

    async def test_customer_message_dict_extracts_text_field(self):
        """When customer_message is a dict, the 'text' field is extracted."""
        with patch(
            "server_modules.sage_turn_adapter.execute_sage_turn",
            new=AsyncMock(return_value=SageTurnResult(
                message="Dict extracted OK.",
                trace_id="trace-dict-1",
            )),
        ) as execute_mock:
            result = await agent_channel_router.route_inbound_channel_message(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                channel_key="slack",
                customer_message={"text": "plain text from dict", "unused": 42},
                actor_id="user-1",
            )

        self.assertTrue(result["ok"])
        _call_kwargs = execute_mock.call_args.kwargs
        self.assertEqual(_call_kwargs["message"], "plain text from dict")

    async def test_customer_message_dict_falls_back_to_json(self):
        """When customer_message is a dict without text/goal keys, it serialises as JSON."""
        with patch(
            "server_modules.sage_turn_adapter.execute_sage_turn",
            new=AsyncMock(return_value=SageTurnResult(
                message="JSON fallback OK.",
                trace_id="trace-json-1",
            )),
        ) as execute_mock:
            result = await agent_channel_router.route_inbound_channel_message(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                channel_key="github",
                customer_message={"event": "push", "commits": 3},
                actor_id="user-1",
            )

        self.assertTrue(result["ok"])
        _call_kwargs = execute_mock.call_args.kwargs
        self.assertIn('"event"', _call_kwargs["message"])
        self.assertIn('"push"', _call_kwargs["message"])

    # ── FIX (systemic backend-safety task): `metadata` used to be accepted
    # by route_inbound_channel_message and never read again anywhere in
    # this function -- silently dropped, so the model never learned
    # whether a Studio-connector turn (Slack/Discord/GitHub/Telegram-
    # hosted) came from a shared, multi-person channel, or which one. See
    # _studio_channel_context_prefix's docstring for the full fix.

    async def test_group_metadata_is_threaded_into_the_message(self):
        """The core fix: is_group/chat_type/chat_label in metadata reach
        the actual message text execute_sage_turn receives."""
        with patch(
            "server_modules.sage_turn_adapter.execute_sage_turn",
            new=AsyncMock(return_value=SageTurnResult(
                message="Sure thing.",
                trace_id="trace-group-1",
            )),
        ) as execute_mock:
            result = await agent_channel_router.route_inbound_channel_message(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                channel_key="slack",
                endpoint_key="C123456",
                customer_message="what time is the standup",
                actor_id="U789",
                actor_display_name="Slack User",
                message_id="slack-msg-group-1",
                metadata={
                    "connector_id": "conn-1",
                    "slack_channel_id": "C123456",
                    "is_group": True,
                    "chat_type": "slack_channel",
                    "chat_label": "#engineering",
                },
            )

        self.assertTrue(result["ok"])
        sent_message = execute_mock.call_args.kwargs["message"]
        self.assertIn("#engineering", sent_message)
        self.assertIn("slack_channel", sent_message)
        self.assertIn("shared channel", sent_message.lower())
        self.assertIn("what time is the standup", sent_message)

    async def test_non_group_metadata_says_private_not_shared(self):
        """is_group=False must say "private", never "shared channel" --
        proves this isn't a hardcoded always-group assumption."""
        with patch(
            "server_modules.sage_turn_adapter.execute_sage_turn",
            new=AsyncMock(return_value=SageTurnResult(message="ok", trace_id="trace-dm-1")),
        ) as execute_mock:
            await agent_channel_router.route_inbound_channel_message(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                channel_key="slack",
                customer_message="hey",
                actor_id="U789",
                metadata={"is_group": False, "chat_type": "slack_dm"},
            )

        sent_message = execute_mock.call_args.kwargs["message"]
        self.assertIn("private", sent_message.lower())
        self.assertNotIn("shared channel", sent_message.lower())

    async def test_metadata_without_group_context_keys_leaves_message_unchanged(self):
        """Regression guard: today's REAL connectors_actions.py webhook
        metadata shape (connector_id, slack_channel_id, delivery_source,
        etc. -- confirmed by inspection of slack_events_webhook /
        discord_events_webhook / github_events_webhook) never carries
        is_group/chat_type/chat_label. This must stay a safe no-op against
        that real shape -- not just against metadata=None, which the
        other tests in this class already cover."""
        with patch(
            "server_modules.sage_turn_adapter.execute_sage_turn",
            new=AsyncMock(return_value=SageTurnResult(message="ok", trace_id="trace-noop-1")),
        ) as execute_mock:
            await agent_channel_router.route_inbound_channel_message(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                channel_key="slack",
                customer_message="Check inventory for brake pads",
                actor_id="U789",
                metadata={
                    "connector_id": "conn-1",
                    "delivery_source": "webhook",
                    "slack_team_id": "T1",
                    "slack_channel_id": "C123456",
                    "slack_thread_ts": None,
                    "slack_message_ts": "123.456",
                    "source_event_id": "evt-1",
                },
            )

        self.assertEqual(execute_mock.call_args.kwargs["message"], "Check inventory for brake pads")

    async def test_directive_message_is_never_prefixed_even_with_group_metadata(self):
        """A leading "/" message must reach execute_sage_turn byte-for-byte
        unprefixed regardless of metadata -- command_registry's directive
        parser matches on the message literally starting with "/"; a
        context prefix would silently break every Studio-connector
        directive the moment a connector starts sending this metadata."""
        with patch(
            "server_modules.sage_turn_adapter.execute_sage_turn",
            new=AsyncMock(return_value=SageTurnResult(message="ok", trace_id="trace-cmd-1")),
        ) as execute_mock:
            await agent_channel_router.route_inbound_channel_message(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                channel_key="slack",
                customer_message="/model claude",
                actor_id="U789",
                metadata={"is_group": True, "chat_type": "slack_channel", "chat_label": "#engineering"},
            )

        self.assertEqual(execute_mock.call_args.kwargs["message"], "/model claude")

    async def test_empty_customer_message_returns_error(self):
        """Empty or None customer_message returns empty_message status."""
        result = await agent_channel_router.route_inbound_channel_message(
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            channel_key="slack",
            customer_message="",
            actor_id="user-1",
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "empty_message")

    async def test_unimplemented_channel_returns_unavailable(self):
        """Channels not in _SAGE_CHANNEL_ORIGIN_MAP still return channel_unavailable."""
        result = await agent_channel_router.route_inbound_channel_message(
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            channel_key="whatsapp_business",
            customer_message="Hello",
            actor_id="user-1",
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "channel_unavailable")
        self.assertIn("whatsapp_business", result["error"])

    # ── Stage 4B: per-agent channel binding resolution ──────────────────
    #
    # agent_channel_router.py:2251-2298 used to resolve a specialist via
    # _resolve_agent_for_inbound and then discard the result with a literal
    # `pass` — every Slack/Discord-guild message answered as Sage no matter
    # what was bound. That resolver also read a channel_bindings JSONB
    # column with zero writers anywhere in the codebase, so even with the
    # `pass` fixed nothing could ever have matched. These tests cover the
    # real, rewired path: _resolve_agent_for_inbound now queries the same
    # agent_channel_bindings table Discord's BYO bot binding already writes
    # and validates against.

    def _binding_row(self, *, agent_install_id: str, channel_key: str, endpoint_key: str):
        return {
            "id": "achbind_1",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "agent_install_id": agent_install_id,
            "key": channel_key,
            "enabled": True,
            "binding": {"endpoint_key": endpoint_key, "is_inbound_owner": True},
        }

    async def test_resolve_agent_for_inbound_matches_channel_key_and_endpoint(self):
        with patch(
            "server_modules.agent_bindings_repository.list_workspace_channel_bindings",
            new=AsyncMock(return_value=[
                self._binding_row(agent_install_id="agent-slack-1", channel_key="slack", endpoint_key="C123456"),
            ]),
        ):
            resolved = await agent_channel_router._resolve_agent_for_inbound(
                channel_type="slack", endpoint_key="C123456", tenant_id="tenant-1", workspace_id="workspace-1",
            )
        self.assertEqual(resolved, "agent-slack-1")

    async def test_resolve_agent_for_inbound_no_match_returns_empty(self):
        with patch(
            "server_modules.agent_bindings_repository.list_workspace_channel_bindings",
            new=AsyncMock(return_value=[
                self._binding_row(agent_install_id="agent-slack-1", channel_key="slack", endpoint_key="C_OTHER"),
            ]),
        ):
            resolved = await agent_channel_router._resolve_agent_for_inbound(
                channel_type="slack", endpoint_key="C123456", tenant_id="tenant-1", workspace_id="workspace-1",
            )
        self.assertEqual(resolved, "")

    async def test_resolve_agent_for_inbound_ignores_other_channel_keys(self):
        """A discord_bot binding for the same endpoint_key string must not
        leak into a slack lookup -- channel_key is part of the match, not
        just endpoint_key."""
        with patch(
            "server_modules.agent_bindings_repository.list_workspace_channel_bindings",
            new=AsyncMock(return_value=[
                self._binding_row(agent_install_id="agent-discord-1", channel_key="discord_bot", endpoint_key="C123456"),
            ]),
        ):
            resolved = await agent_channel_router._resolve_agent_for_inbound(
                channel_type="slack", endpoint_key="C123456", tenant_id="tenant-1", workspace_id="workspace-1",
            )
        self.assertEqual(resolved, "")

    async def test_resolve_agent_for_inbound_lookup_failure_fails_safe_to_empty(self):
        """A DB hiccup must never block or crash the turn -- it falls
        through to Sage exactly like a genuine no-match, never an error."""
        with patch(
            "server_modules.agent_bindings_repository.list_workspace_channel_bindings",
            new=AsyncMock(side_effect=RuntimeError("db unavailable")),
        ):
            resolved = await agent_channel_router._resolve_agent_for_inbound(
                channel_type="slack", endpoint_key="C123456", tenant_id="tenant-1", workspace_id="workspace-1",
            )
        self.assertEqual(resolved, "")

    async def test_bound_specialist_runs_the_turn_not_sage(self):
        """End-to-end: a Slack message in a bound channel must run AS that
        specialist (specialist_context threaded into execute_sage_turn),
        not as Sage with the reply merely attributed to it -- the exact
        'every Slack workspace answers as Sage' bug this step fixes."""
        specialist_ctx = object()  # identity is all that matters here
        with (
            patch(
                "server_modules.agent_bindings_repository.list_workspace_channel_bindings",
                new=AsyncMock(return_value=[
                    self._binding_row(agent_install_id="agent-slack-1", channel_key="slack", endpoint_key="C123456"),
                ]),
            ),
            patch(
                "server_modules.specialist_runtime_context.resolve_specialist_runtime_context",
                new=AsyncMock(return_value=specialist_ctx),
            ) as resolve_mock,
            patch(
                "server_modules.sage_turn_adapter.execute_sage_turn",
                new=AsyncMock(return_value=SageTurnResult(
                    message="Reply as the specialist.",
                    trace_id="trace-1",
                    provider="anthropic",
                    model="claude-sonnet-4-6",
                )),
            ) as execute_mock,
        ):
            result = await agent_channel_router.route_inbound_channel_message(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                channel_key="slack",
                endpoint_key="C123456",
                customer_message="Where's my order?",
                actor_id="U789",
                message_id="slack-msg-2",
            )

        self.assertTrue(result["ok"])
        resolve_mock.assert_awaited_once()
        self.assertEqual(resolve_mock.await_args.kwargs["active_agent_install_id"], "agent-slack-1")
        execute_mock.assert_awaited_once()
        self.assertIs(execute_mock.call_args.kwargs["specialist_context"], specialist_ctx)

    async def test_unbound_channel_runs_as_sage_specialist_context_none(self):
        """A channel with no matching binding is unaffected -- runs as Sage
        with specialist_context=None, exactly like before this step."""
        with (
            patch(
                "server_modules.agent_bindings_repository.list_workspace_channel_bindings",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "server_modules.sage_turn_adapter.execute_sage_turn",
                new=AsyncMock(return_value=SageTurnResult(
                    message="Reply as Sage.",
                    trace_id="trace-2",
                    provider="deepseek",
                    model="deepseek-chat",
                )),
            ) as execute_mock,
        ):
            result = await agent_channel_router.route_inbound_channel_message(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                channel_key="slack",
                endpoint_key="C_UNBOUND",
                customer_message="Hello",
                actor_id="U789",
            )

        self.assertTrue(result["ok"])
        execute_mock.assert_awaited_once()
        self.assertIsNone(execute_mock.call_args.kwargs["specialist_context"])

    async def test_two_agents_two_channels_each_gets_its_own(self):
        """The task's literal spirit for this step: two specialists bound to
        two different Slack channels in the SAME workspace, proven to each
        receive their own specialist_context -- no cross-agent leakage."""
        rows = [
            self._binding_row(agent_install_id="agent-support-1", channel_key="slack", endpoint_key="C_SUPPORT"),
            self._binding_row(agent_install_id="agent-sales-1", channel_key="slack", endpoint_key="C_SALES"),
        ]
        with patch(
            "server_modules.agent_bindings_repository.list_workspace_channel_bindings",
            new=AsyncMock(return_value=rows),
        ):
            resolved_support = await agent_channel_router._resolve_agent_for_inbound(
                channel_type="slack", endpoint_key="C_SUPPORT", tenant_id="tenant-1", workspace_id="workspace-1",
            )
            resolved_sales = await agent_channel_router._resolve_agent_for_inbound(
                channel_type="slack", endpoint_key="C_SALES", tenant_id="tenant-1", workspace_id="workspace-1",
            )

        self.assertEqual(resolved_support, "agent-support-1")
        self.assertEqual(resolved_sales, "agent-sales-1")
        self.assertNotEqual(resolved_support, resolved_sales)


# ── Natural replies: personal-channel auto-replies must not force a
# Telegram/WhatsApp quote-reply bubble ──────────────────────────────────
#
# This class used to duplicate personal_channels_service.py's own
# PersonalChannelsServiceNaturalReplyTests (test_personal_channels_service_
# natural_reply.py) byte-for-byte, testing agent_channel_router's dead
# duplicate handlers (_deliver_whatsapp_personal_reply,
# _handle_telegram_gateway_channel_inbound,
# _deliver_local_bridge_personal_reply, send_whatsapp_personal_message)
# instead of the live personal_channels_service.py functions the Gateway
# actually calls (gateway_protocol_service.py -> personal_channels_service.
# handle_gateway_channel_inbound; agent_channel_router.py's copies had zero
# external callers -- confirmed by a full-repo grep before deletion, see
# the FIX 4 backend-safety task commit this comment was added in). Removed
# alongside that dead handler family in agent_channel_router.py itself;
# test_personal_channels_service_natural_reply.py already covers the exact
# same four behaviors (identical test names) against the real, live code
# path -- no coverage was lost.


if __name__ == "__main__":
    unittest.main()
