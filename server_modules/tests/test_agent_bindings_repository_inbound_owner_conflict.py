"""Shared inbound-owner-conflict helpers (agent_bindings_repository.py) --
used by every channel-bind path (Slack/Discord/Telegram/...) to translate
uq_agent_channel_bindings_inbound_owner_v2's DB-level guarantee
(control_plane_repository.py) into a specific, human-readable error instead
of leaking a raw asyncpg constraint-violation string to the frontend. Added
alongside FIX 2 (surfacing the "channel already owned" rejection in the UI)
-- before this, routes_fleet.py's Slack path and hosted_bot_provisioning_
service.assign_byo_bot (Telegram BYO) had NO conflict translation at all,
so a real conflict (now reachable now that Slack/GitHub are covered by the
unique index) would have dumped a raw Postgres error string straight into
the "Fleet -> Channels" banner.

See test_routes_fleet_slack_channel_binding.py and
test_hosted_bot_provisioning_service.py for the call-site-level proof that
these helpers are actually wired in.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import agent_bindings_repository as bindings


def _run(coro):
    return asyncio.run(coro)


class _FakeConstraintError(Exception):
    def __init__(self, message: str, constraint_name: str = ""):
        super().__init__(message)
        self.constraint_name = constraint_name


class IsInboundOwnerConflictTests(unittest.TestCase):
    def test_matches_by_constraint_name_attribute(self):
        exc = _FakeConstraintError("duplicate key", constraint_name="uq_agent_channel_bindings_inbound_owner_v2")
        self.assertTrue(bindings.is_inbound_owner_conflict(exc))

    def test_matches_by_message_substring_when_no_constraint_name_attr(self):
        exc = RuntimeError(
            'duplicate key value violates unique constraint '
            '"uq_agent_channel_bindings_inbound_owner_v2"'
        )
        self.assertTrue(bindings.is_inbound_owner_conflict(exc))

    def test_does_not_match_unrelated_errors(self):
        exc = RuntimeError("connection refused")
        self.assertFalse(bindings.is_inbound_owner_conflict(exc))

    def test_survives_index_name_revisions(self):
        # Matches by substring, not the full literal name, so a future
        # rebuild (e.g. a hypothetical _v3) still matches -- same rationale
        # as discord_bot_provisioning_service._is_inbound_owner_conflict and
        # agent_specialist_repository._is_channel_ownership_unique_violation.
        exc = _FakeConstraintError("x", constraint_name="uq_agent_channel_bindings_inbound_owner_v3")
        self.assertTrue(bindings.is_inbound_owner_conflict(exc))


class FindInboundOwnerConflictTests(unittest.TestCase):
    def test_returns_none_when_no_rows_exist(self):
        with patch.object(bindings, "list_workspace_channel_bindings", new=AsyncMock(return_value=[])):
            result = _run(bindings.find_inbound_owner_conflict(
                tenant_id="t1", workspace_id="w1", channel_key="slack",
                endpoint_key="C123", exclude_agent_install_id="agent-a",
            ))
        self.assertIsNone(result)

    def test_finds_a_conflicting_row_owned_by_a_different_agent(self):
        rows = [{
            "key": "slack", "agent_install_id": "agent-b",
            "binding": {"endpoint_key": "C123", "is_inbound_owner": "true"},
        }]
        with patch.object(bindings, "list_workspace_channel_bindings", new=AsyncMock(return_value=rows)):
            result = _run(bindings.find_inbound_owner_conflict(
                tenant_id="t1", workspace_id="w1", channel_key="slack",
                endpoint_key="C123", exclude_agent_install_id="agent-a",
            ))
        self.assertIsNotNone(result)
        self.assertEqual(result["agent_install_id"], "agent-b")

    def test_ignores_the_excluded_agents_own_row(self):
        """The agent re-saving its OWN existing binding must not be flagged
        as a conflict with itself."""
        rows = [{
            "key": "slack", "agent_install_id": "agent-a",
            "binding": {"endpoint_key": "C123", "is_inbound_owner": "true"},
        }]
        with patch.object(bindings, "list_workspace_channel_bindings", new=AsyncMock(return_value=rows)):
            result = _run(bindings.find_inbound_owner_conflict(
                tenant_id="t1", workspace_id="w1", channel_key="slack",
                endpoint_key="C123", exclude_agent_install_id="agent-a",
            ))
        self.assertIsNone(result)

    def test_ignores_bindings_that_are_not_inbound_owner(self):
        rows = [{
            "key": "slack", "agent_install_id": "agent-b",
            "binding": {"endpoint_key": "C123", "is_inbound_owner": "false"},
        }]
        with patch.object(bindings, "list_workspace_channel_bindings", new=AsyncMock(return_value=rows)):
            result = _run(bindings.find_inbound_owner_conflict(
                tenant_id="t1", workspace_id="w1", channel_key="slack",
                endpoint_key="C123", exclude_agent_install_id="agent-a",
            ))
        self.assertIsNone(result)

    def test_ignores_a_different_channel_key(self):
        rows = [{
            "key": "discord", "agent_install_id": "agent-b",
            "binding": {"endpoint_key": "C123", "is_inbound_owner": "true"},
        }]
        with patch.object(bindings, "list_workspace_channel_bindings", new=AsyncMock(return_value=rows)):
            result = _run(bindings.find_inbound_owner_conflict(
                tenant_id="t1", workspace_id="w1", channel_key="slack",
                endpoint_key="C123", exclude_agent_install_id="agent-a",
            ))
        self.assertIsNone(result)

    def test_endpoint_key_match_is_case_insensitive(self):
        rows = [{
            "key": "slack", "agent_install_id": "agent-b",
            "binding": {"endpoint_key": "C123", "is_inbound_owner": "true"},
        }]
        with patch.object(bindings, "list_workspace_channel_bindings", new=AsyncMock(return_value=rows)):
            result = _run(bindings.find_inbound_owner_conflict(
                tenant_id="t1", workspace_id="w1", channel_key="slack",
                endpoint_key="c123", exclude_agent_install_id="agent-a",
            ))
        self.assertIsNotNone(result)


class GetAgentInstallLabelTests(unittest.TestCase):
    def test_returns_none_when_the_control_plane_pool_is_unavailable(self):
        with patch(
            "server_modules.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=None),
        ):
            result = _run(bindings.get_agent_install_label("agent-a", tenant_id="t1", workspace_id="w1"))
        self.assertIsNone(result)

    def test_returns_the_label_when_resolvable(self):
        with (
            patch(
                "server_modules.control_plane_repository.ensure_control_plane_schema",
                new=AsyncMock(return_value=object()),
            ),
            patch(
                "server_modules.control_plane_repository.rls_fetchrow",
                new=AsyncMock(return_value={"label": "Support Bot"}),
            ),
        ):
            result = _run(bindings.get_agent_install_label("agent-a", tenant_id="t1", workspace_id="w1"))
        self.assertEqual(result, "Support Bot")

    def test_returns_none_rather_than_raising_on_a_lookup_failure(self):
        """A label-lookup hiccup must never block the conflict error itself
        from being raised -- best-effort only."""
        with patch(
            "server_modules.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(side_effect=RuntimeError("db down")),
        ):
            result = _run(bindings.get_agent_install_label("agent-a", tenant_id="t1", workspace_id="w1"))
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
