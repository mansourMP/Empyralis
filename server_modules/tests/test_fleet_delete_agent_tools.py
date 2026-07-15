"""fleet_tools.fleet_delete_agent — the owner-only, irreversible per-agent
teardown behind DELETE /api/w/{workspace_id}/fleet/agents/{agent_id}.

Every external call fleet_delete_agent makes is mocked at its SOURCE module
(server_modules.<x>.<fn>), not at fleet_tools.<fn> -- fleet_tools.py imports
each of these lazily, inside the function body, so patching the source
module is what actually takes effect (same approach test_fleet_tools.py
already uses for bounded_scheduler_service.propose_self_wakeup).

Tests:
  - missing agent_id / agent not found -> ok:false, no side effects
  - the workspace's operator/master install can never be deleted (the guard
    against workspace_context.agent_workspace_context_dir's empty-id ->
    workspace-root fallback) -> ok:false, no side effects
  - control-plane unavailable -> ok:false BEFORE any teardown side effect runs
  - happy path: Discord/Telegram/Slack channel release, schedule
    cancellation, memory wipe, and the hard DELETE all run with the correct
    agent_id/tenant_id/workspace_id, and the audit ledger event omits
    install_id (the row is already gone -- a real FK constraint, not just a
    naming nuance -- see fleet_delete_agent's own comment on this)
  - the memory wipe refuses to rmtree anything not shaped like
    .../agents/<install_token>, even if agent_workspace_context_dir ever
    returned something else
  - a channel-release failure (e.g. a dead Discord token) doesn't abort the
    rest of the teardown
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from server_modules import fleet_tools


def _run(coro):
    return asyncio.run(coro)


def _bundle(label: str = "Test Agent") -> dict:
    return {"id": "agent-1", "label": label, "install_metadata": {}, "metadata": {}}


class FleetDeleteAgentGuardTests(unittest.TestCase):

    def test_missing_agent_id_is_rejected(self):
        result = _run(fleet_tools.fleet_delete_agent(actor_id="owner-1", workspace_id="ws-1", agent_id="   "))
        self.assertFalse(result["ok"])
        self.assertIn("agent_id", result["error"])

    def test_agent_not_found_returns_ok_false(self):
        with patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            AsyncMock(return_value=None),
        ):
            result = _run(fleet_tools.fleet_delete_agent(
                actor_id="owner-1", workspace_id="ws-1", agent_id="agent-404",
            ))
        self.assertFalse(result["ok"])
        self.assertIn("not found", result["error"].lower())

    def test_cannot_delete_the_workspace_operator_agent(self):
        """The install this workspace's get_workspace_master_agent_install
        resolves to (Sage) must never be deletable -- both because losing it
        strands the workspace with no operator, and because
        agent_workspace_context_dir silently resolves an EMPTY
        agent_install_id to the bare workspace memory root."""
        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                AsyncMock(return_value=_bundle()),
            ),
            patch(
                "server_modules.agent_registry_repository.get_workspace_master_agent_install",
                AsyncMock(return_value={"id": "agent-1"}),
            ),
            patch("server_modules.control_plane_repository.rls_execute", AsyncMock()) as rls_mock,
            patch(
                "server_modules.discord_bot_provisioning_service.release_agent_discord", AsyncMock(),
            ) as discord_mock,
        ):
            result = _run(fleet_tools.fleet_delete_agent(
                actor_id="owner-1", workspace_id="ws-1", agent_id="agent-1",
            ))
        self.assertFalse(result["ok"])
        self.assertIn("operator", result["error"].lower())
        rls_mock.assert_not_awaited()
        discord_mock.assert_not_awaited()

    def test_control_plane_unavailable_fails_before_any_teardown_side_effect(self):
        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                AsyncMock(return_value=_bundle()),
            ),
            patch(
                "server_modules.agent_registry_repository.get_workspace_master_agent_install",
                AsyncMock(return_value=None),
            ),
            patch(
                "server_modules.control_plane_repository.ensure_control_plane_schema",
                AsyncMock(return_value=None),
            ),
            patch(
                "server_modules.discord_bot_provisioning_service.release_agent_discord", AsyncMock(),
            ) as discord_mock,
        ):
            result = _run(fleet_tools.fleet_delete_agent(
                actor_id="owner-1", workspace_id="ws-1", agent_id="agent-1",
            ))
        self.assertFalse(result["ok"])
        self.assertIn("unavailable", result["error"].lower())
        # No channel release, schedule cancel, or memory wipe must have been
        # attempted -- the DB-unavailable check runs before any of it.
        discord_mock.assert_not_awaited()


class FleetDeleteAgentHappyPathTests(unittest.TestCase):

    def test_happy_path_releases_channels_cancels_schedules_wipes_memory_and_hard_deletes(self):
        agent_dir = MagicMock()
        agent_dir.parent.name = "agents"
        agent_dir.exists.return_value = True

        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                AsyncMock(return_value=_bundle(label="Sales Bot")),
            ),
            patch(
                "server_modules.agent_registry_repository.get_workspace_master_agent_install",
                AsyncMock(return_value={"id": "agent-OTHER"}),
            ),
            patch("server_modules.control_plane_repository.ensure_control_plane_schema", AsyncMock(return_value=object())),
            patch("server_modules.control_plane_repository.rls_execute", AsyncMock(return_value="DELETE 1")) as rls_mock,
            patch(
                "server_modules.discord_bot_provisioning_service.release_agent_discord",
                AsyncMock(return_value={"released": True, "credential_deleted": True}),
            ) as discord_mock,
            patch(
                "server_modules.hosted_bot_provisioning_service.release_agent_telegram",
                AsyncMock(return_value={"released": True, "credential_deleted": True}),
            ) as telegram_mock,
            patch(
                "server_modules.agent_bindings_repository.delete_channel_binding",
                AsyncMock(return_value=True),
            ) as slack_mock,
            patch(
                "server_modules.bounded_scheduler_service.list_wake_requests_for_agent",
                AsyncMock(return_value=[{"id": "wake-1"}, {"id": "wake-2"}]),
            ),
            patch(
                "server_modules.bounded_scheduler_service.cancel_wake_request",
                AsyncMock(return_value={"ok": True}),
            ) as cancel_mock,
            patch("server_modules.workspace_context.agent_workspace_context_dir", MagicMock(return_value=agent_dir)),
            patch("server_modules.kill_switch_gate.clear_kill_switch") as kill_mock,
            patch(
                "server_modules.control_plane_repository.get_workspace_by_id",
                AsyncMock(return_value={"tenant_id": "tenant-1"}),
            ),
            patch("server_modules.activity_ledger_service.append_activity_event", AsyncMock()) as ledger_mock,
            patch("shutil.rmtree") as rmtree_mock,
        ):
            result = _run(fleet_tools.fleet_delete_agent(
                actor_id="owner-1", actor_label="owner@x.com", workspace_id="ws-1",
                tenant_id="tenant-1", agent_id="agent-1",
            ))

        self.assertTrue(result["ok"])
        self.assertTrue(result["deleted"])
        self.assertEqual(result["cancelled_schedules"], 2)
        self.assertTrue(result["memory_wiped"])

        discord_mock.assert_awaited_once_with(agent_install_id="agent-1", workspace_id="ws-1", tenant_id="tenant-1")
        telegram_mock.assert_awaited_once_with(agent_install_id="agent-1", workspace_id="ws-1", tenant_id="tenant-1")
        slack_mock.assert_awaited_once()
        self.assertEqual(slack_mock.await_args.kwargs["channel_key"], "slack")
        self.assertEqual(slack_mock.await_args.kwargs["agent_install_id"], "agent-1")
        self.assertEqual(cancel_mock.await_count, 2)

        rls_mock.assert_awaited_once()
        query = rls_mock.await_args.args[1]
        self.assertIn("DELETE FROM workspace_agent_installs", query)
        self.assertEqual(rls_mock.await_args.args[2:], ("agent-1", "tenant-1", "ws-1"))
        self.assertEqual(rls_mock.await_args.kwargs["tenant_id"], "tenant-1")
        self.assertEqual(rls_mock.await_args.kwargs["workspace_id"], "ws-1")

        rmtree_mock.assert_called_once_with(agent_dir, ignore_errors=True)
        kill_mock.assert_called_once_with("agent:agent-1")

        ledger_mock.assert_awaited_once()
        ledger_kwargs = ledger_mock.await_args.kwargs
        # install_id must be OMITTED (defaults to None) -- the row is already
        # gone, and activity_ledger_events.install_id is a real FK
        # (ON DELETE SET NULL); inserting a new row pointed at a
        # now-nonexistent id would violate that constraint outright.
        self.assertNotIn("install_id", ledger_kwargs)
        self.assertEqual(ledger_kwargs["action"], "agent_deleted")
        self.assertEqual(ledger_kwargs["metadata"]["agent_id"], "agent-1")
        self.assertEqual(ledger_kwargs["metadata"]["cancelled_schedules"], 2)
        self.assertTrue(ledger_kwargs["metadata"]["memory_wiped"])

    def test_memory_wipe_refuses_a_path_not_shaped_like_agents_slash_install_token(self):
        """Defense in depth: if agent_workspace_context_dir ever resolved to
        something NOT shaped like .../agents/<token> (e.g. a future bug
        collapses it to the bare workspace scope root -- see that function's
        own note on the 2026-07-14 cross-agent memory leak), this must
        refuse to rmtree it rather than trust the path blindly."""
        unsafe_dir = MagicMock()
        unsafe_dir.parent.name = "workspaces"  # NOT "agents" -> must refuse
        unsafe_dir.exists.return_value = True

        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                AsyncMock(return_value=_bundle()),
            ),
            patch(
                "server_modules.agent_registry_repository.get_workspace_master_agent_install",
                AsyncMock(return_value=None),
            ),
            patch("server_modules.control_plane_repository.ensure_control_plane_schema", AsyncMock(return_value=object())),
            patch("server_modules.control_plane_repository.rls_execute", AsyncMock(return_value="DELETE 1")),
            patch(
                "server_modules.discord_bot_provisioning_service.release_agent_discord",
                AsyncMock(return_value={"released": False}),
            ),
            patch(
                "server_modules.hosted_bot_provisioning_service.release_agent_telegram",
                AsyncMock(return_value={"released": False}),
            ),
            patch("server_modules.agent_bindings_repository.delete_channel_binding", AsyncMock(return_value=False)),
            patch("server_modules.bounded_scheduler_service.list_wake_requests_for_agent", AsyncMock(return_value=[])),
            patch("server_modules.workspace_context.agent_workspace_context_dir", MagicMock(return_value=unsafe_dir)),
            patch("server_modules.kill_switch_gate.clear_kill_switch"),
            patch(
                "server_modules.control_plane_repository.get_workspace_by_id",
                AsyncMock(return_value={"tenant_id": "tenant-1"}),
            ),
            patch("server_modules.activity_ledger_service.append_activity_event", AsyncMock()),
            patch("shutil.rmtree") as rmtree_mock,
        ):
            result = _run(fleet_tools.fleet_delete_agent(
                actor_id="owner-1", workspace_id="ws-1", tenant_id="tenant-1", agent_id="agent-1",
            ))

        self.assertTrue(result["ok"])
        self.assertFalse(result["memory_wiped"])
        rmtree_mock.assert_not_called()

    def test_channel_release_failure_does_not_abort_the_delete(self):
        """A Discord API hiccup (e.g. token already revoked) must not block
        the rest of the teardown -- the install row still needs to go away.
        Errors are captured in the returned channel_release detail instead."""
        agent_dir = MagicMock()
        agent_dir.parent.name = "agents"
        agent_dir.exists.return_value = True

        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                AsyncMock(return_value=_bundle()),
            ),
            patch(
                "server_modules.agent_registry_repository.get_workspace_master_agent_install",
                AsyncMock(return_value=None),
            ),
            patch("server_modules.control_plane_repository.ensure_control_plane_schema", AsyncMock(return_value=object())),
            patch("server_modules.control_plane_repository.rls_execute", AsyncMock(return_value="DELETE 1")) as rls_mock,
            patch(
                "server_modules.discord_bot_provisioning_service.release_agent_discord",
                AsyncMock(side_effect=RuntimeError("discord api down")),
            ),
            patch(
                "server_modules.hosted_bot_provisioning_service.release_agent_telegram",
                AsyncMock(return_value={"released": False, "reason": "no telegram binding for this agent"}),
            ),
            patch("server_modules.agent_bindings_repository.delete_channel_binding", AsyncMock(return_value=False)),
            patch("server_modules.bounded_scheduler_service.list_wake_requests_for_agent", AsyncMock(return_value=[])),
            patch("server_modules.workspace_context.agent_workspace_context_dir", MagicMock(return_value=agent_dir)),
            patch("server_modules.kill_switch_gate.clear_kill_switch"),
            patch(
                "server_modules.control_plane_repository.get_workspace_by_id",
                AsyncMock(return_value={"tenant_id": "tenant-1"}),
            ),
            patch("server_modules.activity_ledger_service.append_activity_event", AsyncMock()),
            patch("shutil.rmtree"),
        ):
            result = _run(fleet_tools.fleet_delete_agent(
                actor_id="owner-1", workspace_id="ws-1", tenant_id="tenant-1", agent_id="agent-1",
            ))

        self.assertTrue(result["ok"])
        self.assertTrue(result["deleted"])
        self.assertFalse(result["channel_release"]["discord"]["released"])
        self.assertIn("discord api down", result["channel_release"]["discord"]["error"])
        rls_mock.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
