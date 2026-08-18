"""Security review 2026-08-13 (sec/cross-tenant-authz): `GET .../fleet/usage`
had a project-level ACL check for `scope=project` but not for `scope=agent`,
in the same function, right next to a comment explicitly citing MAN-115.
`usage_events_repository.summarize_usage` filters `scope=agent` purely by
`tenant_id + workspace_id + agent_install_id` -- no project predicate
anywhere in the SQL -- so a workspace member with `project_memberships`
access to Project A only could read Project B's agent's full cost/token
rollup (model name, tokens, usd_cost) by id alone.

CONFIRMED live against a seeded two-project workspace with a real Postgres
usage_events row: a project-A-only member's `scope=agent&id=<agent-in-B>`
request returned the full $137.42/750000-token breakdown, while the
equivalent `scope=project&id=<project-B>` request correctly 404'd. Fixed by
adding the same `_enforce_agent_project_access` gate every other agent-scoped
route in this file already uses.

Mirrors test_routes_fleet_member_rbac.py's pattern: real ASGI requests
through the REAL auth_module.enforce_workspace_access/enforce_project_access
(not mocked), with only the install-bundle lookup (agent_registry_repository.
get_workspace_agent_install_bundle) / projects_repository.is_project_member
patched -- so a 403/404 here is the dependency wiring actually rejecting,
never a mock standing in for it.

2026-08-19 (fail-open fix): `_enforce_agent_project_access` was rewritten to
resolve the agent through `agent_reachability_service.
lookup_agent_install_bundle` (the same bundle-based lookup the turn-path
guard uses) instead of `project_tasks_service.agent_project_id`, so it can
tell "project-less specialist" apart from "doesn't exist" -- the earlier
project_tasks_service call conflated both into a bare `None`. These tests
were updated to patch the new lookup accordingly; the scenarios and
assertions are unchanged.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI

from server_modules import routes_fleet


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(routes_fleet.router)
    return app


def _member_user() -> dict:
    return {
        "user_id": "member-1",
        "email": "member@example.com",
        "auth_type": "bearer",
        "workspace_access": {
            "ws-1": {"workspace_id": "ws-1", "tenant_id": "tenant-1", "role": "member", "tenant_role": "member"}
        },
    }


def _owner_user() -> dict:
    return {
        "user_id": "owner-1",
        "email": "owner@example.com",
        "auth_type": "bearer",
        "workspace_access": {
            "ws-1": {"workspace_id": "ws-1", "tenant_id": "tenant-1", "role": "owner", "tenant_role": "owner"}
        },
    }


def _agent_lives_in_project_b():
    return patch(
        "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
        new=AsyncMock(
            return_value={"id": "agent-in-project-b", "agent_kind": "specialist", "project_id": "project-b"}
        ),
    )


def _member_has_project_access(has_access: bool):
    return patch(
        "server_modules.projects_repository.is_project_member",
        new=AsyncMock(return_value=has_access),
    )


_FAKE_USAGE_PAYLOAD = {
    "ok": True,
    "scope": "agent",
    "scope_id": "agent-in-project-b",
    "period": "day",
    "totals": {"events": 1, "total_tokens": 750000, "usd_cost": 137.42},
}


@pytest.mark.anyio
async def test_member_without_project_b_access_cannot_read_project_bs_agent_usage() -> None:
    """THE cross-project boundary this fix has to hold: a member's WORKSPACE
    role alone (viewer floor) must not be enough to read another project's
    agent's usage rollup just by knowing its agent_install_id."""
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _member_user

    with (
        patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
        _agent_lives_in_project_b(),
        _member_has_project_access(False),
        patch(
            "server_modules.usage_events_repository.summarize_usage",
            new=AsyncMock(side_effect=AssertionError("must never reach the repository layer")),
        ),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/api/w/ws-1/fleet/usage", params={"scope": "agent", "id": "agent-in-project-b"},
            )
    # enforce_project_access's own contract: 404, not 403 or a 200 {"ok": ...}
    # -- a non-member must not be able to tell "no such agent" apart from
    # "exists in a project you can't see".
    assert response.status_code == 404


@pytest.mark.anyio
async def test_member_with_project_b_access_can_read_its_agent_usage() -> None:
    """The other half of the boundary: this is a real, usable gate, not a
    blanket lockout -- a member who WAS added to the agent's project still
    gets the data."""
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _member_user

    with (
        patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
        _agent_lives_in_project_b(),
        _member_has_project_access(True),
        patch(
            "server_modules.usage_events_repository.summarize_usage",
            new=AsyncMock(return_value=_FAKE_USAGE_PAYLOAD),
        ) as summarize_mock,
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/api/w/ws-1/fleet/usage", params={"scope": "agent", "id": "agent-in-project-b"},
            )
    assert response.status_code == 200
    assert response.json()["totals"]["usd_cost"] == 137.42
    summarize_mock.assert_awaited_once()


@pytest.mark.anyio
async def test_owner_still_sees_every_agents_usage_without_a_membership_row() -> None:
    """Owners bypass the per-project ACL by design (MAN-70 2026-07-28
    ruling, the same bypass every other _enforce_agent_project_access caller
    in this file relies on) -- this fix must not regress that."""
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _owner_user

    with (
        patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
        _agent_lives_in_project_b(),
        patch(
            "server_modules.usage_events_repository.summarize_usage",
            new=AsyncMock(return_value=_FAKE_USAGE_PAYLOAD),
        ) as summarize_mock,
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/api/w/ws-1/fleet/usage", params={"scope": "agent", "id": "agent-in-project-b"},
            )
    assert response.status_code == 200
    summarize_mock.assert_awaited_once()


@pytest.mark.anyio
async def test_scope_workspace_is_unaffected_by_the_new_gate() -> None:
    """The default scope=workspace path never names an agent or project id
    -- confirm the new check is a no-op there, not a regression."""
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _member_user

    with (
        patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
        patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            new=AsyncMock(side_effect=AssertionError("scope=workspace must never resolve an agent's project")),
        ),
        patch(
            "server_modules.usage_events_repository.summarize_usage",
            new=AsyncMock(return_value={"ok": True, "scope": "workspace"}),
        ) as summarize_mock,
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/w/ws-1/fleet/usage")
    assert response.status_code == 200
    summarize_mock.assert_awaited_once()
