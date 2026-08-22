"""§1.3 (Multiplayer Projects plan) — MCP registry silent cross-account
overwrite.

Verified gap: mcp_registry_service._workspace_bucket/_save_workspace_bucket
key MCP server rows by (workspace_id, server_id) with a STATIC server_id per
provider (APP_MCP_SERVER_MAP, e.g. "google-gmail" —
connection_oauth_service.py). upsert_workspace_mcp_server{,_async} upserted
that row unconditionally: a second agent connecting its OWN account of the
same provider in the same workspace silently overwrote the credential_id
every agent's tool calls resolve through (_resolve_mcp_credential reads only
that one row) — the next MCP tool call for ANY agent in the workspace would
silently start hitting a DIFFERENT mailbox, with no error.

Fix (interim containment, not a schema change — see the module banner above
mcp_registry_service._workspace_bucket): upsert_workspace_mcp_server{,_async}
now calls _assert_no_cross_agent_credential_collision before writing. It
refuses (McpServerCredentialCollisionError) exactly when an existing row's
credential is owned by one specific agent and the incoming credential is
owned by a DIFFERENT one — preserving migration safety for every other case
(first assignment, same-agent reconnect/re-auth, and legacy/workspace-shared
rows, which stay silently overwritable exactly as before).

connection_oauth_service._register_mcp_servers_for_provider (the real OAuth
completion call site) must not let that refusal get swallowed into the same
generic warning-log-and-continue as an ordinary failure — it now reports it
distinctly under "collisions" in the returned payload.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from server_modules import connection_oauth_service, mcp_registry_service
from server_modules import agent_turn_runtime_service as sage


def _run(coro):
    return asyncio.run(coro)


class McpRegistryCrossAccountCollisionTests(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.registry_path = Path(self.temp_dir.name) / "mcp_servers.json"
        self.registry_patcher = patch.object(mcp_registry_service, "MCP_SERVER_REGISTRY_FILE", self.registry_path)
        self.registry_patcher.start()
        # credential_owner_agent_install_id does a real vault_store lookup;
        # stub it with a plain dict so these tests never touch Postgres/the
        # vault and can freely control who "owns" each credential id.
        self._owners = {}
        self.owner_patcher = patch.object(
            mcp_registry_service,
            "credential_owner_agent_install_id",
            side_effect=lambda credential_id: self._owners.get(str(credential_id or "").strip()),
        )
        self.owner_patcher.start()

    def tearDown(self) -> None:
        self.owner_patcher.stop()
        self.registry_patcher.stop()
        self.temp_dir.cleanup()
        super().tearDown()

    def _upsert(self, *, credential_id: str):
        return mcp_registry_service.upsert_workspace_mcp_server(
            workspace_id="ws-1",
            server_id="google-gmail",
            label="Google Gmail (MCP)",
            transport="streamable_http",
            endpoint="https://gmailmcp.googleapis.com/mcp/v1",
            enabled=True,
            credential_id=credential_id,
            discover_tools=False,
        )

    def test_second_agents_own_account_is_refused_not_silently_overwritten(self) -> None:
        """Agent A connects first. Agent B connecting its OWN Gmail account
        for the SAME provider in the SAME workspace must be refused loudly —
        never silently take over the row agent A's tool calls resolve
        through."""
        self._owners["cred-agent-a"] = "agent-a"
        self._owners["cred-agent-b"] = "agent-b"

        first = self._upsert(credential_id="cred-agent-a")
        self.assertEqual(first["credential_id"], "cred-agent-a")

        with self.assertRaises(mcp_registry_service.McpServerCredentialCollisionError) as ctx:
            self._upsert(credential_id="cred-agent-b")
        self.assertIn("agent-a", str(ctx.exception))

        # The critical assertion: agent A's row was NOT silently swapped.
        # Every agent's next MCP tool call for this provider still resolves
        # agent A's own credential, not agent B's.
        still_stored = mcp_registry_service.get_workspace_mcp_server("ws-1", "google-gmail")
        self.assertEqual(still_stored["credential_id"], "cred-agent-a")

    def test_same_agent_reconnecting_is_allowed(self) -> None:
        """Migration/UX safety: the SAME agent re-authing its own account
        (a brand new vault row every time — vault_store.add_credential is an
        INSERT, never an update-in-place) must not be mistaken for a
        collision."""
        self._owners["cred-agent-a-v1"] = "agent-a"
        self._owners["cred-agent-a-v2"] = "agent-a"

        self._upsert(credential_id="cred-agent-a-v1")
        updated = self._upsert(credential_id="cred-agent-a-v2")  # must not raise
        self.assertEqual(updated["credential_id"], "cred-agent-a-v2")

    def test_legacy_workspace_shared_row_stays_overwritable(self) -> None:
        """Migration safety for existing single-account/no-fleet-agent
        setups: a row whose credential carries no agent_install_id at all
        (workspace-shared/legacy — today's default) keeps today's silent-
        overwrite behavior unchanged, even when the new connect IS
        agent-scoped."""
        self._owners["cred-shared-old"] = None  # unassigned / legacy
        self._owners["cred-agent-x"] = "agent-x"

        self._upsert(credential_id="cred-shared-old")
        updated = self._upsert(credential_id="cred-agent-x")  # must not raise
        self.assertEqual(updated["credential_id"], "cred-agent-x")

    def test_first_assignment_to_a_fresh_server_slot_is_unaffected(self) -> None:
        """No prior row at all -- the common case (first-ever connect) must
        be completely untouched by this guard."""
        self._owners["cred-agent-a"] = "agent-a"
        created = self._upsert(credential_id="cred-agent-a")
        self.assertEqual(created["credential_id"], "cred-agent-a")

    def test_async_upsert_path_enforces_the_same_guard(self) -> None:
        """upsert_workspace_mcp_server_async (the path OAuth connect actually
        calls via _register_mcp_servers_for_provider) must not be a bypass."""
        self._owners["cred-agent-a"] = "agent-a"
        self._owners["cred-agent-b"] = "agent-b"

        _run(mcp_registry_service.upsert_workspace_mcp_server_async(
            workspace_id="ws-1", server_id="google-gmail", label="Gmail",
            transport="streamable_http", endpoint="https://gmailmcp.googleapis.com/mcp/v1",
            enabled=True, credential_id="cred-agent-a", discover_tools=False,
        ))
        with self.assertRaises(mcp_registry_service.McpServerCredentialCollisionError):
            _run(mcp_registry_service.upsert_workspace_mcp_server_async(
                workspace_id="ws-1", server_id="google-gmail", label="Gmail",
                transport="streamable_http", endpoint="https://gmailmcp.googleapis.com/mcp/v1",
                enabled=True, credential_id="cred-agent-b", discover_tools=False,
            ))


class RegisterMcpServersForProviderCollisionReportingTests(unittest.IsolatedAsyncioTestCase):
    """connection_oauth_service._register_mcp_servers_for_provider must not
    swallow a collision into the same generic warning-and-continue as any
    other failure -- it must surface it distinctly so a human can see it
    (the OAuth connect itself still succeeds; only MCP tool registration for
    that server_id is withheld).

    This class's own concern is collision reporting, not Google scope
    availability -- google_workspace's gmail/calendar/drive MCP entries are
    each capability-gated (connection_oauth_service.google_workspace_
    capability_available, see fix/google-connectors-honest-when-scopes-
    unavailable) and only "drive" is enabled by default, so every test here
    forces all three capabilities available to keep exercising exactly the
    three entries these tests were written against."""

    def setUp(self) -> None:
        super().setUp()
        self.capability_patcher = patch.object(
            connection_oauth_service, "google_workspace_capability_available", return_value=True,
        )
        self.capability_patcher.start()

    def tearDown(self) -> None:
        self.capability_patcher.stop()
        super().tearDown()

    async def test_collision_is_reported_not_silently_swallowed(self) -> None:
        with patch.object(
            mcp_registry_service,
            "upsert_workspace_mcp_server_async",
            new=AsyncMock(
                side_effect=mcp_registry_service.McpServerCredentialCollisionError(
                    "google-gmail", "ws-1", "agent-a", "agent-b",
                )
            ),
        ):
            result = await connection_oauth_service._register_mcp_servers_for_provider(
                workspace_id="ws-1",
                normalized_provider="google_workspace",
                credential_id="cred-agent-b",
            )
        self.assertEqual(result["registered"], 0)
        self.assertEqual(len(result["collisions"]), 3)  # gmail + calendar + drive entries
        self.assertTrue(all("agent-a" in c["detail"] for c in result["collisions"]))

    async def test_non_collision_failure_still_swallowed_as_before(self) -> None:
        """Ordinary failures (network errors, invalid endpoint, etc.) keep
        their pre-existing best-effort behavior -- logged, not surfaced as a
        collision, and never block the OAuth flow."""
        with patch.object(
            mcp_registry_service,
            "upsert_workspace_mcp_server_async",
            new=AsyncMock(side_effect=RuntimeError("transient network error")),
        ):
            result = await connection_oauth_service._register_mcp_servers_for_provider(
                workspace_id="ws-1",
                normalized_provider="google_workspace",
                credential_id="cred-agent-a",
            )
        self.assertEqual(result["registered"], 0)
        self.assertEqual(result["collisions"], [])


class _RegistryEntry:
    def __init__(self, name: str, connector: str) -> None:
        self.tool_name = name
        self.connector_id = connector


class SpecialistMcpRegistryOwnershipFilterTests(unittest.TestCase):
    """Read side of §1.3: agent_turn_runtime_service._filter_registry_for_
    specialist previously gated MCP tools purely on connector-id/tool-name
    membership, never on WHICH credential the workspace's MCP registry row
    currently resolves to. A specialist explicitly toggled onto (or bound to
    the connector of) an mcp__<server>__<tool> entry must not be handed
    another agent's connected mailbox just because the toggle matches."""

    def setUp(self) -> None:
        super().setUp()
        self.get_server_patcher = patch.object(sage.mcp_registry_service, "get_workspace_mcp_server")
        self.mock_get_server = self.get_server_patcher.start()
        self.owner_patcher = patch.object(sage.mcp_registry_service, "credential_owner_agent_install_id")
        self.mock_owner = self.owner_patcher.start()

    def tearDown(self) -> None:
        self.owner_patcher.stop()
        self.get_server_patcher.stop()
        super().tearDown()

    def test_mcp_tool_owned_by_a_different_agent_is_dropped(self) -> None:
        self.mock_get_server.return_value = {"credential_id": "cred-agent-a"}
        self.mock_owner.return_value = "agent-a"
        registry = [_RegistryEntry("mcp__google-gmail__send_email", "mcp")]
        toolset = {
            "core": set(), "tools": {"mcp__google-gmail__send_email"}, "connectors": set(),
            "agent_install_id": "agent-b",
        }
        kept = sage._filter_registry_for_specialist(registry, toolset, workspace_id="ws-1")
        self.assertEqual(kept, [])

    def test_mcp_tool_owned_by_the_same_agent_is_kept(self) -> None:
        self.mock_get_server.return_value = {"credential_id": "cred-agent-a"}
        self.mock_owner.return_value = "agent-a"
        registry = [_RegistryEntry("mcp__google-gmail__send_email", "mcp")]
        toolset = {
            "core": set(), "tools": {"mcp__google-gmail__send_email"}, "connectors": set(),
            "agent_install_id": "agent-a",
        }
        kept = sage._filter_registry_for_specialist(registry, toolset, workspace_id="ws-1")
        self.assertEqual([e.tool_name for e in kept], ["mcp__google-gmail__send_email"])

    def test_workspace_shared_unassigned_mcp_credential_is_kept_for_any_agent(self) -> None:
        self.mock_get_server.return_value = {"credential_id": "cred-shared"}
        self.mock_owner.return_value = None  # unassigned/workspace-shared
        registry = [_RegistryEntry("mcp__google-gmail__send_email", "mcp")]
        toolset = {
            "core": set(), "tools": {"mcp__google-gmail__send_email"}, "connectors": set(),
            "agent_install_id": "agent-anyone",
        }
        kept = sage._filter_registry_for_specialist(registry, toolset, workspace_id="ws-1")
        self.assertEqual([e.tool_name for e in kept], ["mcp__google-gmail__send_email"])

    def test_missing_workspace_id_skips_the_new_gate_for_backward_compatibility(self) -> None:
        """workspace_id is optional precisely so pre-existing callers/tests
        that never touch MCP tools are unaffected by this fix."""
        registry = [_RegistryEntry("slack__post", "slack")]
        toolset = {"core": set(), "tools": set(), "connectors": {"slack"}}
        kept = sage._filter_registry_for_specialist(registry, toolset)
        self.assertEqual([e.tool_name for e in kept], ["slack__post"])
        self.mock_get_server.assert_not_called()

    def test_lookup_error_fails_closed_drops_entry(self) -> None:
        self.mock_get_server.side_effect = RuntimeError("registry file unavailable")
        registry = [_RegistryEntry("mcp__google-gmail__send_email", "mcp")]
        toolset = {
            "core": set(), "tools": {"mcp__google-gmail__send_email"}, "connectors": set(),
            "agent_install_id": "agent-a",
        }
        kept = sage._filter_registry_for_specialist(registry, toolset, workspace_id="ws-1")
        self.assertEqual(kept, [])


if __name__ == "__main__":
    unittest.main()
