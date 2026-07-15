"""Per-agent Slack channel binding (routes_fleet.py) -- the write side of
STEP 3's "every Slack workspace answers as Sage" fix. Slack's OAuth
connection is workspace-wide (unlike Discord's dedicated-bot-per-agent
BYO model), so ownership here is per-channel: this agent claims one Slack
channel id, written to the same agent_channel_bindings table Discord's
BYO bot binding already uses, in the shape
agent_channel_router._resolve_agent_for_inbound matches against
(channel_key="slack", binding.endpoint_key=<channel id>).
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import routes_fleet


def _run(coro):
    return asyncio.run(coro)


def _owner_user() -> dict:
    return {"user_id": "owner-1", "email": "owner@example.com"}


def _bypass_workspace_access():
    return patch.object(
        routes_fleet.auth_module, "enforce_workspace_access",
        lambda current_user, workspace_id, minimum_role="viewer": workspace_id,
    )


class FleetSlackChannelBindingTests(unittest.TestCase):
    def test_assign_requires_a_channel_id(self):
        body = routes_fleet.FleetSlackChannelBindRequest(slack_channel_id="   ")
        with _bypass_workspace_access():
            result = _run(routes_fleet.fleet_assign_agent_slack(
                request=None, workspace_id="ws-1", body=body, agent_id="agent-1", current_user=_owner_user(),
            ))
        self.assertFalse(result["ok"])
        self.assertIn("required", result["error"].lower())

    def test_assign_writes_a_channel_binding_shaped_for_the_router(self):
        body = routes_fleet.FleetSlackChannelBindRequest(slack_channel_id="C123456")
        with (
            patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
            patch(
                "server_modules.agent_bindings_repository.upsert_channel_binding",
                new=AsyncMock(return_value={"id": "achbind_1", "enabled": True}),
            ) as upsert_mock,
            _bypass_workspace_access(),
        ):
            result = _run(routes_fleet.fleet_assign_agent_slack(
                request=None, workspace_id="ws-1", body=body, agent_id="agent-1", current_user=_owner_user(),
            ))

        self.assertTrue(result["ok"])
        upsert_mock.assert_awaited_once()
        kwargs = upsert_mock.await_args.kwargs
        self.assertEqual(kwargs["tenant_id"], "tenant-1")
        self.assertEqual(kwargs["workspace_id"], "ws-1")
        self.assertEqual(kwargs["agent_install_id"], "agent-1")
        # This exact shape is what _resolve_agent_for_inbound matches on --
        # channel_key == channel_type, binding.endpoint_key == endpoint_key.
        self.assertEqual(kwargs["channel_key"], "slack")
        self.assertTrue(kwargs["enabled"])
        self.assertEqual(kwargs["binding"]["endpoint_key"], "C123456")

    def test_assign_reports_failure_when_binding_write_returns_none(self):
        body = routes_fleet.FleetSlackChannelBindRequest(slack_channel_id="C123456")
        with (
            patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
            patch(
                "server_modules.agent_bindings_repository.upsert_channel_binding",
                new=AsyncMock(return_value=None),
            ),
            _bypass_workspace_access(),
        ):
            result = _run(routes_fleet.fleet_assign_agent_slack(
                request=None, workspace_id="ws-1", body=body, agent_id="agent-1", current_user=_owner_user(),
            ))
        self.assertFalse(result["ok"])

    def test_release_deletes_the_binding(self):
        with (
            patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
            patch(
                "server_modules.agent_bindings_repository.delete_channel_binding",
                new=AsyncMock(return_value=True),
            ) as delete_mock,
            _bypass_workspace_access(),
        ):
            result = _run(routes_fleet.fleet_release_agent_slack(
                request=None, workspace_id="ws-1", agent_id="agent-1", current_user=_owner_user(),
            ))

        self.assertTrue(result["ok"])
        self.assertTrue(result["deleted"])
        delete_mock.assert_awaited_once()
        kwargs = delete_mock.await_args.kwargs
        self.assertEqual(kwargs["agent_install_id"], "agent-1")
        self.assertEqual(kwargs["channel_key"], "slack")

    def test_two_agents_bind_two_different_channels_independently(self):
        """Mirrors the router-side proof: two POSTs for two different
        agents/channels must each write their own distinct row."""
        with patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")), _bypass_workspace_access():
            with patch(
                "server_modules.agent_bindings_repository.upsert_channel_binding",
                new=AsyncMock(return_value={"id": "achbind_support", "enabled": True}),
            ) as upsert_mock:
                _run(routes_fleet.fleet_assign_agent_slack(
                    request=None, workspace_id="ws-1",
                    body=routes_fleet.FleetSlackChannelBindRequest(slack_channel_id="C_SUPPORT"),
                    agent_id="agent-support-1", current_user=_owner_user(),
                ))
                support_kwargs = upsert_mock.await_args.kwargs

            with patch(
                "server_modules.agent_bindings_repository.upsert_channel_binding",
                new=AsyncMock(return_value={"id": "achbind_sales", "enabled": True}),
            ) as upsert_mock2:
                _run(routes_fleet.fleet_assign_agent_slack(
                    request=None, workspace_id="ws-1",
                    body=routes_fleet.FleetSlackChannelBindRequest(slack_channel_id="C_SALES"),
                    agent_id="agent-sales-1", current_user=_owner_user(),
                ))
                sales_kwargs = upsert_mock2.await_args.kwargs

        self.assertEqual(support_kwargs["agent_install_id"], "agent-support-1")
        self.assertEqual(support_kwargs["binding"]["endpoint_key"], "C_SUPPORT")
        self.assertEqual(sales_kwargs["agent_install_id"], "agent-sales-1")
        self.assertEqual(sales_kwargs["binding"]["endpoint_key"], "C_SALES")


if __name__ == "__main__":
    unittest.main()
