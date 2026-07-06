"""Proof for the workspace-tenant binding fix.

An unbound workspace (no tenant binding) is exactly the condition that makes
tenant_id_for_workspace return None → /auth/me 403. These tests exercise the
real control-plane functions against the isolated tmp store (conftest autouse
fixture) to show: unbound resolves to None, ensure_workspace_tenant_binding
binds it, resolution then succeeds, and an already-bound workspace is untouched.
"""

import asyncio

import pytest

from server_modules import control_plane_repository as cpr


@pytest.fixture(autouse=True)
def _bypass_control_plane_kernel_gate(monkeypatch):
    # The workspace_tenant_binding_ensure kernel decision isn't stubbed by the
    # shared conftest kernel mock. These tests cover the binding + resolution
    # logic, not the kernel policy, so no-op that one gate.
    monkeypatch.setattr(cpr, "_enforce_control_plane_service_decision", lambda *a, **k: None)


def test_unbound_workspace_resolves_none_then_binds_and_resolves():
    async def scenario():
        # Unbound → None. This is the /auth/me 403 condition.
        assert await cpr.tenant_id_for_workspace("ws_unbound_alpha") is None

        # Bind via the shared, fixed binding function.
        result = await cpr.ensure_workspace_tenant_binding(
            workspace_id="ws_unbound_alpha", tenant_id="tenant_alpha",
        )
        assert isinstance(result, dict)
        assert result.get("tenant_id") == "tenant_alpha"

        # Now it resolves — /auth/me would load 200.
        assert await cpr.tenant_id_for_workspace("ws_unbound_alpha") == "tenant_alpha"

    asyncio.run(scenario())


def test_existing_binding_unaffected_by_binding_another_workspace():
    async def scenario():
        await cpr.ensure_workspace_tenant_binding(workspace_id="ws-1", tenant_id="tenant_ws1")
        # Binding a different, previously-unbound workspace must not disturb ws-1.
        await cpr.ensure_workspace_tenant_binding(workspace_id="ws_unbound_beta", tenant_id="tenant_beta")

        assert await cpr.tenant_id_for_workspace("ws-1") == "tenant_ws1"          # unaffected
        assert await cpr.tenant_id_for_workspace("ws_unbound_beta") == "tenant_beta"

    asyncio.run(scenario())


def test_rebinding_same_workspace_is_idempotent():
    async def scenario():
        await cpr.ensure_workspace_tenant_binding(workspace_id="ws_idem", tenant_id="tenant_idem")
        # Re-running the migration over an already-bound workspace is safe.
        again = await cpr.ensure_workspace_tenant_binding(workspace_id="ws_idem", tenant_id="tenant_idem")
        assert again.get("tenant_id") == "tenant_idem"
        assert await cpr.tenant_id_for_workspace("ws_idem") == "tenant_idem"

    asyncio.run(scenario())
