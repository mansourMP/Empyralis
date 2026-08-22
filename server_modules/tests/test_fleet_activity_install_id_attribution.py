"""2026-07-09 attribution fix (friction #4/#5): fleet_get_agent_activity and
fleet_get_project_activity must query activity_ledger_events by install_id —
the acting agent's own identity, stamped by every ledger writer — not
actor_id, which tracks the human sender and never matches an agent_id."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import fleet_tools
from server_modules import agent_turn_runtime_service


def _run(coro):
    return asyncio.run(coro)


class FleetGetAgentActivityQueryTests(unittest.TestCase):
    def test_queries_by_install_id_not_actor_id(self):
        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=[])
        with patch(
            "server_modules.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=mock_pool),
        ):
            result = _run(fleet_tools.fleet_get_agent_activity(
                actor_id="owner-1", workspace_id="ws-1", agent_id="ainstall_abc",
            ))

        self.assertTrue(result["ok"])
        query_sql = mock_pool.fetch.call_args.args[0]
        self.assertIn("install_id = $2", query_sql)
        self.assertNotIn("actor_id = $2", query_sql)
        # agent_id is passed as the install_id filter value
        self.assertEqual(mock_pool.fetch.call_args.args[2], "ainstall_abc")


class FleetGetProjectActivityQueryTests(unittest.TestCase):
    def test_queries_by_install_id_not_actor_id(self):
        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=[])
        with (
            patch(
                "server_modules.agent_registry_repository.list_workspace_agent_installs",
                new=AsyncMock(return_value=[{"id": "ainstall_abc", "project_id": "proj-1"}]),
            ),
            patch(
                "server_modules.control_plane_repository.ensure_control_plane_schema",
                new=AsyncMock(return_value=mock_pool),
            ),
        ):
            result = _run(fleet_tools.fleet_get_project_activity(
                workspace_id="ws-1", project_id="proj-1",
            ))

        self.assertTrue(result["ok"])
        query_sql = mock_pool.fetch.call_args.args[0]
        self.assertIn("install_id = ANY", query_sql)
        self.assertNotIn("actor_id = ANY", query_sql)


class SageChatLedgerFieldsTests(unittest.TestCase):
    """Direct coverage of the pure helper that decides ledger identity +
    honesty — the acting agent's own event_class/title vs Sage's, and
    status=error on failure regardless of which."""

    def test_sage_turn_success(self):
        event_class, action, title, status = agent_turn_runtime_service._sage_chat_ledger_fields(
            spec_install_id="", agent_label="", failed=False,
        )
        self.assertEqual(event_class, "sage_activity")
        self.assertEqual(action, "sage_chat.completed")
        self.assertEqual(title, "Agent chat completed")
        self.assertEqual(status, "logged")

    def test_sage_turn_failure(self):
        event_class, action, title, status = agent_turn_runtime_service._sage_chat_ledger_fields(
            spec_install_id="", agent_label="", failed=True,
        )
        self.assertEqual(event_class, "sage_activity")
        self.assertEqual(action, "sage_chat.failed")
        self.assertEqual(title, "Agent chat failed")
        self.assertEqual(status, "error")

    def test_specialist_turn_success_carries_its_own_label(self):
        event_class, action, title, status = agent_turn_runtime_service._sage_chat_ledger_fields(
            spec_install_id="ainstall_xyz", agent_label="Repo Watch", failed=False,
        )
        self.assertEqual(event_class, "specialist_activity")
        self.assertEqual(action, "agent_chat.completed")
        self.assertEqual(title, "Repo Watch chat completed")
        self.assertEqual(status, "logged")
        self.assertNotIn("Sage", title)

    def test_specialist_turn_failure_carries_its_own_label(self):
        event_class, action, title, status = agent_turn_runtime_service._sage_chat_ledger_fields(
            spec_install_id="ainstall_xyz", agent_label="Repo Watch", failed=True,
        )
        self.assertEqual(event_class, "specialist_activity")
        self.assertEqual(action, "agent_chat.failed")
        self.assertEqual(title, "Repo Watch chat failed")
        self.assertEqual(status, "error")

    def test_specialist_turn_missing_label_falls_back_honestly(self):
        _, _, title, _ = agent_turn_runtime_service._sage_chat_ledger_fields(
            spec_install_id="ainstall_xyz", agent_label="", failed=False,
        )
        self.assertEqual(title, "Agent chat completed")


if __name__ == "__main__":
    unittest.main()
