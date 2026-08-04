"""MAN-300 regression cover: wechat_official_service.assign_wechat_official
took a caller-supplied agent_install_id and wrote an agent-scoped vault
credential PLUS a channel binding against it with NO check that the install
belongs to the caller's own (tenant_id, workspace_id) -- full exploitability
parity with MAN-206 (hosted_bot_provisioning_service.assign_byo_bot /
discord_bot_provisioning_service.assign_agent_discord). RLS's
``INSERT ... WITH CHECK`` does not catch this: the new binding/credential row
correctly carries the CALLER's own tenant_id/workspace_id, so the policy is
satisfied even though agent_install_id points at a different tenant's agent.
The inbound WeChat/WeCom webhook (_resolve_binding_and_credential ->
get_channel_binding_by_agent_unscoped, keyed ONLY on agent_install_id, with
bypass_rls=True) would then route real inbound traffic -- and reply using
the planted credential -- to that other tenant's agent.

Two tiers of coverage, per the higher bar this fix is held to:

  * WeChatAssignCrossWorkspaceOwnershipMockedScopeTests -- wiring tests that
    mock agent_install_in_scope directly (mirrors MAN-206's own
    AssignByoBotCrossWorkspaceOwnershipTests in
    test_hosted_bot_provisioning_service.py). These prove the SERVICE calls
    the guard and reacts correctly to True/False, but do not by themselves
    prove the guard's own SQL actually rejects a foreign id.

  * WeChatAssignRealOwnershipCheckExploitTests -- exercises the REAL
    agent_install_in_scope implementation (only the DB layer underneath it --
    control_plane_repository.ensure_control_plane_schema / rls_fetchrow -- is
    faked), so the full ownership-check logic actually runs on the path
    assign_wechat_official takes. This is the test that must fail against
    pre-fix code because the write SUCCEEDS (not because a symbol is
    missing) -- see each test's docstring for exactly what pre-fix behavior
    it catches.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from server_modules import wechat_official_service as wechat


def _assign_kwargs(**overrides) -> dict:
    base = dict(
        agent_install_id="agent-2",
        workspace_id="ws-1",
        tenant_id="tenant-1",
        account_kind=wechat.ACCOUNT_KIND_OFFICIAL,
        app_id="wx-app-id",
        app_secret="wx-app-secret",
        verify_token="verify-tok",
    )
    base.update(overrides)
    return base


class WeChatAssignCrossWorkspaceOwnershipMockedScopeTests(unittest.IsolatedAsyncioTestCase):
    """Wiring-level cover: does assign_wechat_official call the ownership
    guard, and does it honor True/False, BEFORE any credential validation or
    write? Mirrors MAN-206's AssignByoBotCrossWorkspaceOwnershipTests."""

    async def test_rejects_when_agent_install_id_is_outside_the_callers_workspace(self) -> None:
        with (
            patch.object(wechat.bindings, "agent_install_in_scope", new=AsyncMock(return_value=False)) as scope_mock,
            patch.object(wechat.WeChatAccessTokenManager, "get_access_token", new=AsyncMock()) as token_mock,
            patch.object(wechat, "store_wechat_credential") as store_mock,
            patch.object(wechat.bindings, "upsert_channel_binding", new=AsyncMock()) as upsert_mock,
        ):
            with self.assertRaises(wechat.bindings.AgentInstallNotInScopeError):
                await wechat.assign_wechat_official(**_assign_kwargs(
                    agent_install_id="ainstall_belongs_to_other_tenant",
                    workspace_id="ws-attacker",
                    tenant_id="tenant-attacker",
                ))
        scope_mock.assert_awaited_once_with(
            "ainstall_belongs_to_other_tenant", tenant_id="tenant-attacker", workspace_id="ws-attacker",
        )
        # The rejection must happen BEFORE any credential validation or
        # write -- no live access_token fetch, no credential, no binding.
        token_mock.assert_not_awaited()
        store_mock.assert_not_called()
        upsert_mock.assert_not_awaited()

    async def test_legitimate_same_workspace_assignment_still_works(self) -> None:
        """The ownership gate must not block the ordinary path: an agent
        that genuinely belongs to the caller's own workspace."""
        with (
            patch.object(wechat.bindings, "agent_install_in_scope", new=AsyncMock(return_value=True)) as scope_mock,
            patch.object(wechat.WeChatAccessTokenManager, "get_access_token", new=AsyncMock(return_value="tok-abc")),
            patch.object(wechat.bindings, "find_inbound_owner_conflict", new=AsyncMock(return_value=None)),
            patch.object(wechat, "store_wechat_credential", return_value="cred-123") as store_mock,
            patch.object(wechat.bindings, "upsert_channel_binding", new=AsyncMock(return_value={"id": "x"})) as upsert_mock,
        ):
            result = await wechat.assign_wechat_official(**_assign_kwargs())
        scope_mock.assert_awaited_once_with("agent-2", tenant_id="tenant-1", workspace_id="ws-1")
        self.assertEqual(result["credential_id"], "cred-123")
        store_mock.assert_called_once()
        upsert_mock.assert_awaited_once()


class WeChatAssignRealOwnershipCheckExploitTests(unittest.IsolatedAsyncioTestCase):
    """Exercises the REAL agent_install_in_scope logic (not mocked away) on
    assign_wechat_official's actual call path -- only the DB layer beneath
    it is faked. This is the stronger proof: it shows the ownership SQL
    itself, reached through the real service function, actually refuses a
    foreign agent_install_id -- not just that the service calls *some*
    function named agent_install_in_scope.
    """

    async def test_foreign_agent_install_id_is_refused_end_to_end(self) -> None:
        """Simulates the real exploit shape: agent-victim belongs to
        tenant-victim/ws-victim. tenant-attacker/ws-attacker calls
        assign_wechat_official with that foreign agent_install_id. The
        control-plane pool is reachable, but the RLS-scoped ownership query
        -- run with the CALLER's own (tenant-attacker, ws-attacker) GUC
        scope -- finds no matching row for agent-victim (it belongs to a
        different tenant/workspace), exactly like a real RLS-protected
        Postgres would behave. agent_install_in_scope must therefore return
        False for real, and assign_wechat_official must refuse the write.

        Against PRE-FIX code (before the ownership check was added to
        assign_wechat_official), this test fails because the write
        SUCCEEDS: no AgentInstallNotInScopeError is raised, and
        token_mock/store_mock/upsert_mock all get called -- proving an
        actual credential + binding would have been planted against
        agent-victim, not merely that a helper symbol was missing.
        find_inbound_owner_conflict (an unrelated pre-existing soft check
        further down the same function, nothing to do with ownership) is
        stubbed out here so pre-fix code can run the WHOLE way through to a
        real write instead of tripping over a second, unrelated unmocked DB
        call first -- otherwise a pre-fix failure could be misread as
        support for the fix when it is really just an unrelated mock gap.
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
            patch.object(wechat.bindings, "find_inbound_owner_conflict", new=AsyncMock(return_value=None)),
            patch.object(wechat.WeChatAccessTokenManager, "get_access_token", new=AsyncMock(return_value="tok-abc")) as token_mock,
            patch.object(wechat, "store_wechat_credential", return_value="cred-planted") as store_mock,
            patch.object(wechat.bindings, "upsert_channel_binding", new=AsyncMock(return_value={"id": "x"})) as upsert_mock,
        ):
            with self.assertRaises(wechat.bindings.AgentInstallNotInScopeError):
                await wechat.assign_wechat_official(**_assign_kwargs(
                    agent_install_id="agent-victim",
                    workspace_id="ws-attacker",
                    tenant_id="tenant-attacker",
                ))

        # The ownership query really ran, scoped to the CALLER's own tenant/
        # workspace -- this is what makes the query prove ownership rather
        # than just "does this id exist somewhere".
        fetch_mock.assert_awaited_once()
        _, kwargs = fetch_mock.call_args
        self.assertEqual(kwargs.get("tenant_id"), "tenant-attacker")
        self.assertEqual(kwargs.get("workspace_id"), "ws-attacker")

        # No credential validation, no vault write, no binding write.
        token_mock.assert_not_awaited()
        store_mock.assert_not_called()
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
            patch.object(wechat.WeChatAccessTokenManager, "get_access_token", new=AsyncMock(return_value="tok-abc")),
            patch.object(wechat.bindings, "find_inbound_owner_conflict", new=AsyncMock(return_value=None)),
            patch.object(wechat, "store_wechat_credential", return_value="cred-123") as store_mock,
            patch.object(wechat.bindings, "upsert_channel_binding", new=AsyncMock(return_value={"id": "x"})) as upsert_mock,
        ):
            result = await wechat.assign_wechat_official(**_assign_kwargs(
                agent_install_id="agent-own", workspace_id="ws-1", tenant_id="tenant-1",
            ))

        fetch_mock.assert_awaited_once()
        self.assertEqual(result["credential_id"], "cred-123")
        store_mock.assert_called_once()
        upsert_mock.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
