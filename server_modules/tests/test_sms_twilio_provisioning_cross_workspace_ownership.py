"""MAN-301 regression cover: sms_twilio_provisioning_service.assign_agent_sms_number
took a caller-supplied agent_install_id and, before writing anything, first
PURCHASED A REAL TWILIO PHONE NUMBER (a financial side effect, billed to the
platform's master Twilio account) -- then stored an agent-scoped vault
credential and wrote a channel binding against it, all with NO check that
the install belongs to the caller's own (tenant_id, workspace_id).

Same MAN-206 shape: the binding row carries the CALLER's own tenant_id/
workspace_id, so RLS's INSERT WITH CHECK is satisfied even though
agent_install_id points at another tenant's agent, and
agent_channel_router._resolve_agent_for_inbound (keyed by (tenant_id,
workspace_id) + channel_key + endpoint_key -- see that module's own
docstring) would then route real inbound SMS traffic to it.

There is no live route wired to this function yet (confirmed by a repo-wide
grep — only server_modules/tests/test_sms_twilio_channel.py calls it), so
this is pre-emptive hardening, not an active-exploit closure. Still held to
the same bar: the ownership check runs BEFORE search_available_numbers/
purchase_number (the two calls that actually talk to Twilio and would spend
real money), not just before the credential/binding write.

EVERY test in this file mocks search_available_numbers and purchase_number
(or, for the rejection tests, asserts they are never called) -- no real
Twilio API call is made by this suite, ever.

Two tiers of coverage, matching MAN-300's bar:

  * mocked-scope wiring tests -- prove the service calls agent_install_in_scope
    and reacts correctly to True/False, before the Twilio purchase call.
  * real-ownership-check tests -- exercise the ACTUAL agent_install_in_scope
    SQL logic through assign_agent_sms_number's real call path (only the DB
    layer beneath it is faked). This is the test that must fail against
    pre-fix code because purchase_number gets CALLED (a real number would
    have been bought), not because a symbol is missing.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from server_modules import sms_twilio_provisioning_service as sms


def _assign_kwargs(**overrides) -> dict:
    base = dict(
        agent_install_id="agent-2",
        workspace_id="ws-1",
        tenant_id="tenant-1",
    )
    base.update(overrides)
    return base


class SmsAssignCrossWorkspaceOwnershipMockedScopeTests(unittest.IsolatedAsyncioTestCase):
    """Wiring-level cover: does assign_agent_sms_number call the ownership
    guard, and honor True/False, BEFORE the Twilio purchase call or any
    write? Mirrors MAN-206's AssignByoBotCrossWorkspaceOwnershipTests."""

    async def test_rejects_when_agent_install_id_is_outside_the_callers_workspace(self) -> None:
        with (
            patch.object(sms, "platform_twilio_credentials", return_value={"account_sid": "AC", "auth_token": "tok"}),
            patch.object(sms, "webhook_base_url", return_value="https://hook.test"),
            patch.object(sms.bindings, "agent_install_in_scope", new=AsyncMock(return_value=False)) as scope_mock,
            patch.object(sms, "search_available_numbers", new=AsyncMock()) as search_mock,
            patch.object(sms, "purchase_number", new=AsyncMock()) as purchase_mock,
            patch.object(sms, "store_sms_credential") as store_mock,
            patch.object(sms.bindings, "upsert_channel_binding", new=AsyncMock()) as upsert_mock,
        ):
            with self.assertRaises(sms.bindings.AgentInstallNotInScopeError):
                await sms.assign_agent_sms_number(**_assign_kwargs(
                    agent_install_id="ainstall_belongs_to_other_tenant",
                    workspace_id="ws-attacker",
                    tenant_id="tenant-attacker",
                ))
        scope_mock.assert_awaited_once_with(
            "ainstall_belongs_to_other_tenant", tenant_id="tenant-attacker", workspace_id="ws-attacker",
        )
        # The rejection must happen BEFORE Twilio is ever contacted -- no
        # number search, no purchase (no money spent), no credential, no
        # binding.
        search_mock.assert_not_awaited()
        purchase_mock.assert_not_awaited()
        store_mock.assert_not_called()
        upsert_mock.assert_not_awaited()

    async def test_legitimate_same_workspace_assignment_still_works(self) -> None:
        """The ownership gate must not block the ordinary path: an agent
        that genuinely belongs to the caller's own workspace."""
        with (
            patch.object(sms, "platform_twilio_credentials", return_value={"account_sid": "AC", "auth_token": "tok"}),
            patch.object(sms, "webhook_base_url", return_value="https://hook.test"),
            patch.object(sms.bindings, "agent_install_in_scope", new=AsyncMock(return_value=True)) as scope_mock,
            patch.object(sms, "search_available_numbers", new=AsyncMock(return_value=[{"phone_number": "+15550001111"}])),
            patch.object(sms, "purchase_number", new=AsyncMock(return_value={"phone_number": "+15550001111", "sid": "PN123"})),
            patch.object(sms, "store_sms_credential", return_value="cred-1"),
            patch.object(sms.bindings, "upsert_channel_binding", new=AsyncMock(return_value={"id": "x"})) as upsert_mock,
        ):
            result = await sms.assign_agent_sms_number(**_assign_kwargs())
        scope_mock.assert_awaited_once_with("agent-2", tenant_id="tenant-1", workspace_id="ws-1")
        self.assertEqual(result["phone_number"], "+15550001111")
        upsert_mock.assert_awaited_once()


class SmsAssignRealOwnershipCheckExploitTests(unittest.IsolatedAsyncioTestCase):
    """Exercises the REAL agent_install_in_scope logic (not mocked away) on
    assign_agent_sms_number's actual call path -- only the DB layer beneath
    it is faked. purchase_number is ALWAYS mocked in this class: no real
    Twilio call is ever made, regardless of pre-fix/post-fix outcome.
    """

    async def test_foreign_agent_install_id_is_refused_end_to_end(self) -> None:
        """Simulates the real shape: agent-victim belongs to tenant-victim/
        ws-victim. tenant-attacker/ws-attacker calls assign_agent_sms_number
        with that foreign agent_install_id. The control-plane pool is
        reachable, but the RLS-scoped ownership query -- run with the
        CALLER's own (tenant-attacker, ws-attacker) GUC scope -- finds no
        matching row for agent-victim. agent_install_in_scope must
        therefore return False for real, and the number must never be
        purchased.

        Against PRE-FIX code (before the ownership check was added), this
        test fails because purchase_number GETS CALLED -- proving a real
        Twilio number would have been bought and billed, and the
        credential/binding would have been planted against agent-victim --
        not because a symbol was missing.
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
            patch.object(sms, "platform_twilio_credentials", return_value={"account_sid": "AC", "auth_token": "tok"}),
            patch.object(sms, "webhook_base_url", return_value="https://hook.test"),
            patch.object(sms, "search_available_numbers", new=AsyncMock(return_value=[{"phone_number": "+15550001111"}])) as search_mock,
            patch.object(sms, "purchase_number", new=AsyncMock(return_value={"phone_number": "+15550001111", "sid": "PN123"})) as purchase_mock,
            patch.object(sms, "store_sms_credential") as store_mock,
            patch.object(sms.bindings, "upsert_channel_binding", new=AsyncMock()) as upsert_mock,
        ):
            with self.assertRaises(sms.bindings.AgentInstallNotInScopeError):
                await sms.assign_agent_sms_number(**_assign_kwargs(
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

        # No Twilio purchase, no credential, no binding.
        search_mock.assert_not_awaited()
        purchase_mock.assert_not_awaited()
        store_mock.assert_not_called()
        upsert_mock.assert_not_awaited()

    async def test_own_agent_install_id_is_accepted_end_to_end(self) -> None:
        """Same real ownership-check path, but the row DOES resolve under
        the caller's own (tenant_id, workspace_id) -- the ordinary,
        legitimate case. Confirms the fix does not reject everything.
        purchase_number is mocked -- still no real Twilio call."""
        with (
            patch(
                "server_modules.control_plane_repository.ensure_control_plane_schema",
                new=AsyncMock(return_value=object()),
            ),
            patch(
                "server_modules.control_plane_repository.rls_fetchrow",
                new=AsyncMock(return_value={"?column?": 1}),  # row found under the caller's own scope
            ) as fetch_mock,
            patch.object(sms, "platform_twilio_credentials", return_value={"account_sid": "AC", "auth_token": "tok"}),
            patch.object(sms, "webhook_base_url", return_value="https://hook.test"),
            patch.object(sms, "search_available_numbers", new=AsyncMock(return_value=[{"phone_number": "+15550001111"}])),
            patch.object(sms, "purchase_number", new=AsyncMock(return_value={"phone_number": "+15550001111", "sid": "PN123"})) as purchase_mock,
            patch.object(sms, "store_sms_credential", return_value="cred-1"),
            patch.object(sms.bindings, "upsert_channel_binding", new=AsyncMock(return_value={"id": "x"})) as upsert_mock,
        ):
            result = await sms.assign_agent_sms_number(**_assign_kwargs(
                agent_install_id="agent-own", workspace_id="ws-1", tenant_id="tenant-1",
            ))

        fetch_mock.assert_awaited_once()
        self.assertEqual(result["phone_number"], "+15550001111")
        purchase_mock.assert_awaited_once()
        upsert_mock.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
