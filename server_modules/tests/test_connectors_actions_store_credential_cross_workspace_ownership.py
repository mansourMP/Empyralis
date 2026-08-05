"""MAN-301 regression cover: connectors_actions.store_agent_connector_credential
took a caller-supplied agent_install_id and wrote a vault credential PLUS an
agent_connector_bindings row against it with NO ownership rejection.

_resolve_agent_project_id (called just above the write) IS already scoped by
(tenant_id, workspace_id) -- but pre-fix, its empty-string result for a
foreign agent was only ever used to label the credential's project_id
(project_id=None, i.e. "unscoped"), never to refuse the write. The sibling
subscribe_agent_to_project_credential (same file) already raises
HTTPException(403) when an agent can't be resolved into a project; this fix
makes store_agent_connector_credential do the equivalent -- but via the
agent_install_in_scope helper (MAN-206) directly, not by overloading
_resolve_agent_project_id's empty-string signal, since an agent that
genuinely belongs to the caller's workspace but simply has no project
assigned ALSO resolves an empty project id (that ambiguity is exactly why
the pre-fix code couldn't safely reject on it).

Two tiers of coverage, matching the bar test_wechat_official_service_cross_
workspace_ownership.py set for MAN-300:

  * mocked-scope wiring tests -- prove the service calls agent_install_in_scope
    and reacts correctly to True/False, before any write.
  * real-ownership-check tests -- exercise the ACTUAL agent_install_in_scope
    SQL logic through store_agent_connector_credential's real call path (only
    the DB layer beneath it -- control_plane_repository.ensure_control_plane_
    schema / rls_fetchrow -- is faked). This is the test that must fail
    against pre-fix code because the write SUCCEEDS, not because a symbol is
    missing.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from server_modules import connectors_actions


def _store_kwargs(**overrides) -> dict:
    base = dict(
        workspace_id="ws-1",
        agent_install_id="agent-2",
        provider="stripe",
        label="Stripe",
        credentials={"api_key": "sk_live_x"},
        tenant_id="tenant-1",
    )
    base.update(overrides)
    return base


class StoreAgentConnectorCredentialMockedScopeTests(unittest.IsolatedAsyncioTestCase):
    """Wiring-level cover: does store_agent_connector_credential call the
    ownership guard, and honor True/False, BEFORE any credential/binding
    write?"""

    async def test_rejects_when_agent_install_id_is_outside_the_callers_workspace(self) -> None:
        with (
            patch(
                "server_modules.agent_bindings_repository.agent_install_in_scope",
                new=AsyncMock(return_value=False),
            ) as scope_mock,
            patch.object(connectors_actions, "add_credential") as add_cred_mock,
            patch(
                "server_modules.agent_bindings_repository.upsert_connector_binding",
                new=AsyncMock(),
            ) as upsert_mock,
            patch.object(connectors_actions, "_resolve_agent_project_id", new=AsyncMock()) as resolve_proj_mock,
        ):
            with self.assertRaises(HTTPException) as ctx:
                await connectors_actions.store_agent_connector_credential(**_store_kwargs(
                    agent_install_id="ainstall_belongs_to_other_tenant",
                    workspace_id="ws-attacker",
                    tenant_id="tenant-attacker",
                ))
        self.assertEqual(ctx.exception.status_code, 403)
        scope_mock.assert_awaited_once_with(
            "ainstall_belongs_to_other_tenant", tenant_id="tenant-attacker", workspace_id="ws-attacker",
        )
        # The rejection must happen BEFORE any write -- no vault credential,
        # no binding, not even the (harmless) project-id lookup.
        add_cred_mock.assert_not_called()
        upsert_mock.assert_not_awaited()
        resolve_proj_mock.assert_not_awaited()

    async def test_legitimate_same_workspace_assignment_still_works(self) -> None:
        with (
            patch(
                "server_modules.agent_bindings_repository.agent_install_in_scope",
                new=AsyncMock(return_value=True),
            ) as scope_mock,
            patch.object(connectors_actions, "_resolve_agent_project_id", new=AsyncMock(return_value="proj-1")),
            patch.object(connectors_actions, "add_credential") as add_cred_mock,
            patch(
                "server_modules.agent_bindings_repository.upsert_connector_binding",
                new=AsyncMock(return_value={"enabled": True}),
            ) as upsert_mock,
        ):
            result = await connectors_actions.store_agent_connector_credential(**_store_kwargs())
        scope_mock.assert_awaited_once_with("agent-2", tenant_id="tenant-1", workspace_id="ws-1")
        self.assertEqual(result["provider"], "stripe")
        self.assertEqual(result["project_id"], "proj-1")
        self.assertTrue(result["binding_enabled"])
        add_cred_mock.assert_called_once()
        upsert_mock.assert_awaited_once()

    async def test_agent_with_no_project_is_not_rejected_by_the_ownership_gate(self) -> None:
        """The gate checks OWNERSHIP, not "does this agent have a project" --
        a legitimately-owned agent with no project must still succeed, with
        project_id ending up None (unscoped credential), exactly like
        pre-fix behavior for that case. This is what rules out overloading
        _resolve_agent_project_id's empty-string result as the ownership
        signal: that would have rejected this legitimate case too."""
        with (
            patch(
                "server_modules.agent_bindings_repository.agent_install_in_scope",
                new=AsyncMock(return_value=True),
            ),
            patch.object(connectors_actions, "_resolve_agent_project_id", new=AsyncMock(return_value="")),
            patch.object(connectors_actions, "add_credential") as add_cred_mock,
            patch(
                "server_modules.agent_bindings_repository.upsert_connector_binding",
                new=AsyncMock(return_value={"enabled": True}),
            ) as upsert_mock,
        ):
            result = await connectors_actions.store_agent_connector_credential(**_store_kwargs(
                agent_install_id="agent-no-project",
            ))
        self.assertIsNone(result["project_id"])
        add_cred_mock.assert_called_once()
        upsert_mock.assert_awaited_once()


class StoreAgentConnectorCredentialRealOwnershipCheckExploitTests(unittest.IsolatedAsyncioTestCase):
    """Exercises the REAL agent_install_in_scope logic (not mocked away) on
    store_agent_connector_credential's actual call path -- only the DB layer
    beneath it is faked."""

    async def test_foreign_agent_install_id_is_refused_end_to_end(self) -> None:
        """Simulates the real exploit shape: agent-victim belongs to
        tenant-victim/ws-victim. tenant-attacker/ws-attacker calls
        store_agent_connector_credential with that foreign agent_install_id.
        The control-plane pool is reachable, but the RLS-scoped ownership
        query -- run with the CALLER's own (tenant-attacker, ws-attacker)
        GUC scope -- finds no matching row for agent-victim. agent_install_
        in_scope must therefore return False for real, and the write must
        be refused.

        Against PRE-FIX code (before the ownership check was added), this
        test fails because the write SUCCEEDS: no HTTPException is raised,
        and add_credential/upsert_connector_binding both get called --
        proving an actual credential + binding would have been planted
        against agent-victim, not merely that a helper symbol was missing.
        _resolve_agent_project_id is stubbed to avoid a second, unrelated
        DB round-trip so pre-fix code can run the WHOLE way through to a
        real write.
        """
        with (
            patch(
                "server_modules.control_plane_repository.ensure_control_plane_schema",
                new=AsyncMock(return_value=object()),  # pool "reachable"
            ),
            patch(
                "server_modules.control_plane_repository.rls_fetchrow",
                new=AsyncMock(return_value=None),  # no row visible under the caller's own scope
            ) as fetch_mock,
            patch.object(connectors_actions, "_resolve_agent_project_id", new=AsyncMock(return_value="")),
            patch.object(connectors_actions, "add_credential") as add_cred_mock,
            patch(
                "server_modules.agent_bindings_repository.upsert_connector_binding",
                new=AsyncMock(return_value={"enabled": True}),
            ) as upsert_mock,
        ):
            with self.assertRaises(HTTPException) as ctx:
                await connectors_actions.store_agent_connector_credential(**_store_kwargs(
                    agent_install_id="agent-victim",
                    workspace_id="ws-attacker",
                    tenant_id="tenant-attacker",
                ))
        self.assertEqual(ctx.exception.status_code, 403)

        # The ownership query really ran, scoped to the CALLER's own tenant/
        # workspace -- this is what makes the query prove ownership rather
        # than just "does this id exist somewhere".
        fetch_mock.assert_awaited_once()
        _, kwargs = fetch_mock.call_args
        self.assertEqual(kwargs.get("tenant_id"), "tenant-attacker")
        self.assertEqual(kwargs.get("workspace_id"), "ws-attacker")

        # No vault write, no binding write.
        add_cred_mock.assert_not_called()
        upsert_mock.assert_not_awaited()

    async def test_own_agent_install_id_is_accepted_end_to_end(self) -> None:
        """Same real ownership-check path, but the row DOES resolve under
        the caller's own (tenant_id, workspace_id) -- the ordinary,
        legitimate case. Confirms the fix does not reject everything."""
        with (
            patch(
                "server_modules.control_plane_repository.ensure_control_plane_schema",
                new=AsyncMock(return_value=object()),
            ),
            patch(
                "server_modules.control_plane_repository.rls_fetchrow",
                new=AsyncMock(return_value={"?column?": 1}),  # row found under the caller's own scope
            ) as fetch_mock,
            patch.object(connectors_actions, "_resolve_agent_project_id", new=AsyncMock(return_value="proj-1")),
            patch.object(connectors_actions, "add_credential") as add_cred_mock,
            patch(
                "server_modules.agent_bindings_repository.upsert_connector_binding",
                new=AsyncMock(return_value={"enabled": True}),
            ) as upsert_mock,
        ):
            result = await connectors_actions.store_agent_connector_credential(**_store_kwargs(
                agent_install_id="agent-own", workspace_id="ws-1", tenant_id="tenant-1",
            ))

        fetch_mock.assert_awaited_once()
        self.assertEqual(result["project_id"], "proj-1")
        add_cred_mock.assert_called_once()
        upsert_mock.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
