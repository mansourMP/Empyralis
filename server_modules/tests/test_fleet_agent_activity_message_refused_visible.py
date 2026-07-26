"""MAN-71 audit item 4: fleet_get_agent_activity excludes all `fleet_control`
rows from an agent's own Overview feed (2026-07-10 fix, to keep routine
owner-config plumbing off the agent's work log) — but that filter also swept
up `message_agent_refused`, the ledger row `fleet_message_agent` writes every
time a message to this agent can't be delivered (no delivery path exists
yet). The result: a failed inbound message read as "no activity" on the one
page (this agent's own Overview) someone debugging it would actually check,
even though the same row is already visible workspace-wide in the Inbox.

This test locks in the fix: `message_agent_refused` is let through the
fleet_control filter; every other fleet_control action (configure_agent,
create_agent, ...) stays excluded, so routine owner plumbing doesn't flood
back in.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import fleet_tools


def _run(coro):
    return asyncio.run(coro)


class FleetGetAgentActivityMessageRefusedVisibilityTests(unittest.TestCase):
    def _run_query(self):
        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=[])
        with patch(
            "server_modules.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=mock_pool),
        ):
            result = _run(
                fleet_tools.fleet_get_agent_activity(
                    actor_id="owner-1", workspace_id="ws-1", agent_id="ainstall_abc",
                )
            )
        query_sql = mock_pool.fetch.call_args.args[0]
        return result, query_sql

    def test_message_agent_refused_is_let_through_the_fleet_control_filter(self):
        _, query_sql = self._run_query()
        self.assertIn("message_agent_refused", query_sql)

    def test_other_fleet_control_actions_stay_excluded(self):
        """The exception is scoped to this one action, not a blanket
        reopening of fleet_control — the WHERE clause must still exclude
        event_class = 'fleet_control' as its default branch."""
        _, query_sql = self._run_query()
        self.assertIn("event_class != 'fleet_control'", query_sql)

    def test_query_still_ok_with_no_rows(self):
        result, _ = self._run_query()
        self.assertTrue(result["ok"])
        self.assertEqual(result["events"], [])


if __name__ == "__main__":
    unittest.main()
