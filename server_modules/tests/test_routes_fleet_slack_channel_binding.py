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
                "server_modules.agent_bindings_repository.agent_install_in_scope",
                new=AsyncMock(return_value=True),
            ),
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
                "server_modules.agent_bindings_repository.agent_install_in_scope",
                new=AsyncMock(return_value=True),
            ),
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

    def test_assign_rejects_a_channel_already_owned_by_another_agent(self):
        """FIX 2: the soft pre-check must reject before ever calling
        upsert_channel_binding, and the error text must be specific (not a
        raw DB string) -- see agent_bindings_repository.
        find_inbound_owner_conflict."""
        body = routes_fleet.FleetSlackChannelBindRequest(slack_channel_id="C123456")
        conflict_row = {"agent_install_id": "agent-owner", "key": "slack"}
        with (
            patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
            patch(
                "server_modules.agent_bindings_repository.agent_install_in_scope",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "server_modules.agent_bindings_repository.find_inbound_owner_conflict",
                new=AsyncMock(return_value=conflict_row),
            ),
            patch(
                "server_modules.agent_bindings_repository.get_agent_install_label",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "server_modules.agent_bindings_repository.upsert_channel_binding",
                new=AsyncMock(),
            ) as upsert_mock,
            _bypass_workspace_access(),
        ):
            result = _run(routes_fleet.fleet_assign_agent_slack(
                request=None, workspace_id="ws-1", body=body, agent_id="agent-2", current_user=_owner_user(),
            ))

        self.assertFalse(result["ok"])
        self.assertEqual(result.get("reason"), "already_bound")
        self.assertIn("already connected", result["error"].lower())
        self.assertIn("one agent at a time", result["error"].lower())
        upsert_mock.assert_not_awaited()

    def test_assign_conflict_message_names_the_owning_agent_when_resolvable(self):
        body = routes_fleet.FleetSlackChannelBindRequest(slack_channel_id="C123456")
        conflict_row = {"agent_install_id": "agent-owner", "key": "slack"}
        with (
            patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
            patch(
                "server_modules.agent_bindings_repository.agent_install_in_scope",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "server_modules.agent_bindings_repository.find_inbound_owner_conflict",
                new=AsyncMock(return_value=conflict_row),
            ),
            patch(
                "server_modules.agent_bindings_repository.get_agent_install_label",
                new=AsyncMock(return_value="Support Bot"),
            ),
            _bypass_workspace_access(),
        ):
            result = _run(routes_fleet.fleet_assign_agent_slack(
                request=None, workspace_id="ws-1", body=body, agent_id="agent-2", current_user=_owner_user(),
            ))

        self.assertFalse(result["ok"])
        self.assertIn("Support Bot", result["error"])

    def test_assign_translates_a_race_condition_db_violation_into_friendly_copy(self):
        """The soft pre-check is advisory only -- if two binds land
        concurrently, upsert_channel_binding itself can raise the raw
        unique-index violation. That must be translated too, not leaked to
        the frontend as a raw asyncpg/Postgres string."""
        body = routes_fleet.FleetSlackChannelBindRequest(slack_channel_id="C123456")
        db_error = RuntimeError(
            'duplicate key value violates unique constraint '
            '"uq_agent_channel_bindings_inbound_owner_v2"'
        )
        with (
            patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
            patch(
                "server_modules.agent_bindings_repository.agent_install_in_scope",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "server_modules.agent_bindings_repository.find_inbound_owner_conflict",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "server_modules.agent_bindings_repository.upsert_channel_binding",
                new=AsyncMock(side_effect=db_error),
            ),
            _bypass_workspace_access(),
        ):
            result = _run(routes_fleet.fleet_assign_agent_slack(
                request=None, workspace_id="ws-1", body=body, agent_id="agent-2", current_user=_owner_user(),
            ))

        self.assertFalse(result["ok"])
        self.assertEqual(result.get("reason"), "already_bound")
        self.assertNotIn("constraint", result["error"].lower())
        self.assertNotIn("duplicate key", result["error"].lower())
        self.assertIn("already connected", result["error"].lower())

    def test_assign_rejects_an_agent_install_id_outside_the_caller_workspace(self):
        """Security review 2026-08-13 (sec/cross-tenant-authz), MAN-206:
        every sibling channel-bind route (Telegram assign_byo_bot, Discord
        assign_agent_discord, WeChat, SMS) calls
        agent_bindings_repository.agent_install_in_scope BEFORE writing a
        binding -- this route was the one gap. Without this guard, a caller
        supplies agent_id in the query string, and a fresh
        upsert_channel_binding INSERT succeeds because Postgres RLS's
        WITH CHECK only verifies the NEW row's own tenant_id/workspace_id
        (the caller's), never that agent_install_id itself belongs to that
        scope -- confirmed live: an attacker in a wholly separate tenant
        bound their own workspace's Slack channel to another tenant's real
        agent_install_id, and the DB row landed with the attacker's
        tenant_id/workspace_id and the victim's agent_install_id. A second
        live run showed the write also permanently blocks the victim's own
        legitimate bind afterward (RLS makes the poisoned row invisible to
        their session, so the ON CONFLICT UPDATE raises instead of
        succeeding) -- a cross-tenant DoS, not just a poisoned row.

        Must reject BEFORE find_inbound_owner_conflict/upsert_channel_binding
        are ever called -- this proves the check runs first, not as an
        afterthought."""
        body = routes_fleet.FleetSlackChannelBindRequest(slack_channel_id="C123456")
        with (
            patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
            patch(
                "server_modules.agent_bindings_repository.agent_install_in_scope",
                new=AsyncMock(return_value=False),
            ) as scope_mock,
            patch(
                "server_modules.agent_bindings_repository.find_inbound_owner_conflict",
                new=AsyncMock(side_effect=AssertionError("must never reach the conflict check")),
            ),
            patch(
                "server_modules.agent_bindings_repository.upsert_channel_binding",
                new=AsyncMock(side_effect=AssertionError("must never write a binding for a foreign agent")),
            ),
            _bypass_workspace_access(),
        ):
            result = _run(routes_fleet.fleet_assign_agent_slack(
                request=None, workspace_id="ws-1", body=body,
                agent_id="agent-belonging-to-another-tenant", current_user=_owner_user(),
            ))

        self.assertFalse(result["ok"])
        self.assertIn("does not belong", result["error"].lower())
        scope_mock.assert_awaited_once()
        kwargs = scope_mock.await_args.kwargs
        self.assertEqual(kwargs["tenant_id"], "tenant-1")
        self.assertEqual(kwargs["workspace_id"], "ws-1")

    def test_assign_still_succeeds_for_an_agent_install_id_in_scope(self):
        """The other half of the boundary: this is a real, usable gate, not
        a blanket lockout -- an agent that DOES belong to the caller's own
        (tenant, workspace) still binds normally."""
        body = routes_fleet.FleetSlackChannelBindRequest(slack_channel_id="C123456")
        with (
            patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
            patch(
                "server_modules.agent_bindings_repository.agent_install_in_scope",
                new=AsyncMock(return_value=True),
            ),
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

    def test_two_agents_bind_two_different_channels_independently(self):
        """Mirrors the router-side proof: two POSTs for two different
        agents/channels must each write their own distinct row."""
        with (
            patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
            patch(
                "server_modules.agent_bindings_repository.agent_install_in_scope",
                new=AsyncMock(return_value=True),
            ),
            _bypass_workspace_access(),
        ):
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
