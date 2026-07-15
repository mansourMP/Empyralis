"""DELETE /api/w/{workspace_id}/fleet/agents/{agent_id} — the owner-only,
irreversible agent-delete route added to routes_fleet.py.

Mirrors the two established patterns already used for this file's other
owner-gated routes:
  - test_routes_fleet_auth_guard.py: a real ASGI request through the REAL
    auth_module.enforce_workspace_access (not mocked) to prove the
    dependency wiring itself rejects an insufficiently-privileged caller —
    here, a genuine member of the target workspace whose ROLE is below
    "owner" (viewer), not just a caller from the wrong workspace.
  - test_routes_fleet_slack_channel_binding.py: direct calls to the route
    function with enforce_workspace_access bypassed and fleet_tools.
    fleet_delete_agent mocked, to prove the route passes through the real
    actor_id/workspace_id/tenant_id/agent_id rather than a hardcoded actor.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI

from server_modules import routes_fleet


def _run(coro):
    return asyncio.run(coro)


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(routes_fleet.router)
    return app


def _owner_user() -> dict:
    return {"user_id": "owner-1", "email": "owner@example.com"}


def _viewer_user() -> dict:
    """A real, authenticated member of ws-1 -- just one whose role is below
    "owner". This is the exact shape the owner-only gate exists for: a
    legitimate workspace member (not an intruder from another workspace)
    who still must not be able to delete an agent."""
    return {
        "user_id": "viewer-1",
        "email": "viewer@example.com",
        "auth_type": "bearer",
        "workspace_access": {
            "ws-1": {
                "workspace_id": "ws-1",
                "tenant_id": "tenant-1",
                "role": "viewer",
                "tenant_role": "viewer",
            }
        },
    }


def _member_user() -> dict:
    """Same idea as _viewer_user but role="member" -- the RBAC tier directly
    below "owner" (RBAC_ROLE_ORDER = {viewer: 0, member: 1, owner: 2} in
    auth.py) -- proves the gate is "owner exactly", not "anything but
    viewer"."""
    return {
        "user_id": "member-1",
        "email": "member@example.com",
        "auth_type": "bearer",
        "workspace_access": {
            "ws-1": {
                "workspace_id": "ws-1",
                "tenant_id": "tenant-1",
                "role": "member",
                "tenant_role": "member",
            }
        },
    }


def _bypass_workspace_access():
    return patch.object(
        routes_fleet.auth_module, "enforce_workspace_access",
        lambda current_user, workspace_id, minimum_role="viewer": workspace_id,
    )


@pytest.mark.anyio
async def test_unauthenticated_request_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORION_AUTH_REQUIRED", "1")
    monkeypatch.delenv("EMPYRALIS_DEPLOY_ENV", raising=False)
    monkeypatch.delenv("ORION_ENV", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("NODE_ENV", raising=False)

    app = _build_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.delete("/api/w/ws-victim/fleet/agents/agent-1")

    assert response.status_code == 401


@pytest.mark.anyio
async def test_viewer_role_cannot_delete_an_agent() -> None:
    """A genuine ws-1 member with role="viewer" hits the REAL
    enforce_workspace_access (no bypass, no mocking) and must get a real 403
    -- deleting an agent is destructive and owner-only, same as stop/resume."""
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _viewer_user

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.delete("/api/w/ws-1/fleet/agents/agent-1")

    assert response.status_code == 403


@pytest.mark.anyio
async def test_member_role_cannot_delete_an_agent() -> None:
    """RBAC_ROLE_ORDER puts "member" directly below "owner" -- confirms the
    gate really is minimum_role="owner", not merely "not viewer"."""
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _member_user

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.delete("/api/w/ws-1/fleet/agents/agent-1")

    assert response.status_code == 403


class FleetDeleteAgentRouteWiringTests(unittest.TestCase):
    """Once past the auth gate, the route must call fleet_tools.
    fleet_delete_agent with the REAL signed-in user's id as actor -- never a
    hardcoded "owner" placeholder -- and pass the resolved workspace/tenant/
    agent ids straight through."""

    def test_owner_delete_passes_the_real_actor_id_and_ids_through(self):
        with (
            patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
            patch(
                "server_modules.fleet_tools.fleet_delete_agent",
                new=AsyncMock(return_value={"ok": True, "agent_id": "agent-1", "deleted": True}),
            ) as delete_mock,
            _bypass_workspace_access(),
        ):
            result = _run(routes_fleet.fleet_delete_agent_route(
                request=None, workspace_id="ws-1", agent_id="agent-1", current_user=_owner_user(),
            ))

        self.assertTrue(result["ok"])
        self.assertTrue(result["deleted"])
        delete_mock.assert_awaited_once()
        kwargs = delete_mock.await_args.kwargs
        self.assertEqual(kwargs["workspace_id"], "ws-1")
        self.assertEqual(kwargs["tenant_id"], "tenant-1")
        self.assertEqual(kwargs["agent_id"], "agent-1")
        # The real user_id from current_user, never a hardcoded "owner" id.
        self.assertEqual(kwargs["actor_id"], "owner-1")
        self.assertEqual(kwargs["actor_label"], "owner@example.com")

    def test_actor_id_falls_back_to_owner_only_when_user_id_truly_absent(self):
        with (
            patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
            patch(
                "server_modules.fleet_tools.fleet_delete_agent",
                new=AsyncMock(return_value={"ok": True, "agent_id": "agent-1", "deleted": True}),
            ) as delete_mock,
            _bypass_workspace_access(),
        ):
            _run(routes_fleet.fleet_delete_agent_route(
                request=None, workspace_id="ws-1", agent_id="agent-1", current_user={},
            ))
        self.assertEqual(delete_mock.await_args.kwargs["actor_id"], "owner")

    def test_delete_failure_from_fleet_tools_is_surfaced_as_ok_false(self):
        with (
            patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
            patch(
                "server_modules.fleet_tools.fleet_delete_agent",
                new=AsyncMock(return_value={"ok": False, "error": "Agent agent-404 not found in workspace"}),
            ),
            _bypass_workspace_access(),
        ):
            result = _run(routes_fleet.fleet_delete_agent_route(
                request=None, workspace_id="ws-1", agent_id="agent-404", current_user=_owner_user(),
            ))
        self.assertFalse(result["ok"])
        self.assertIn("not found", result["error"])

    def test_unexpected_exception_is_caught_and_returned_as_ok_false(self):
        """Matches every other route in this file: a raised exception from
        the tools layer must not become an unhandled 500, it becomes
        {ok: False, error: ...}."""
        with (
            patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
            patch(
                "server_modules.fleet_tools.fleet_delete_agent",
                new=AsyncMock(side_effect=RuntimeError("boom")),
            ),
            _bypass_workspace_access(),
        ):
            result = _run(routes_fleet.fleet_delete_agent_route(
                request=None, workspace_id="ws-1", agent_id="agent-1", current_user=_owner_user(),
            ))
        self.assertFalse(result["ok"])
        self.assertIn("boom", result["error"])


if __name__ == "__main__":
    unittest.main()
