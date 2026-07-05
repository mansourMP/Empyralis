import importlib
import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from server_modules import agent_channel_router, channel_concurrency_service, safe_mode_service
from server_modules.agent_manifest import AgentManifest
from server_modules.sage_agent_runtime_contract import SageTurnResult


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


if __name__ == "__main__":
    unittest.main()
