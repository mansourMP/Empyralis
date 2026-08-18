"""MAN-201: a teammate could not open Ask AI at all.

Confirmed live in a real browser against a disposable stack before this
suite was written: a genuinely separate account, invited into a workspace
through Settings > Workspace > Members and accepted through the real
/join/{token} flow, landed in the workspace as a "member" with
GET /api/w/{ws}/fleet/agents returning ZERO agents -- so
fleet-presentation.ts's findSageAgent returned null and SageLauncher's
`if (!sageAgent) return null` rendered no console, no button, nothing.

FOUNDER'S DECISION (2026-08-01), which is what makes this a bug rather than
an open product question: Ask AI is a PERSONAL surface every workspace
member gets, each person's conversations their own. Not owner-only, not
shared.

ROOT CAUSE, traced rather than assumed. The prior commit on this ticket
(1643af209) states the Sage install is filtered "server-side" by
`audience: "owner"`. That is not what happens -- there is no `audience`
filter anywhere in the codebase; `audience` is computed in fleet_tools and
read by no gate at all. The real cause is collateral damage from the
MAN-115 per-project ACL filter in routes_fleet.fleet_agents:

    result["agents"] = [
        a for a in result["agents"]
        if str(a.get("project_id") or "").strip() in visible_ids
    ]

The workspace-level system agent is workspace-scoped BY DESIGN (seeded once
per workspace by agent_registry_repository's master-install INSERT, whose
column list has no project_id at all), so its project_id is "" -- and ""
is never a member of a real visible-project set. An owner is unaffected
because _visible_project_ids returns None for them and the branch is
skipped entirely. That asymmetry is the whole bug.

WHY THE FIX KEYS ON agent_kind AND NOT ON AN EMPTY project_id -- this is
the assertion that matters most in this file, and it is deliberately
tested as a negative below. "Has no project" must NEVER be what grants
visibility: a plain specialist that somehow ends up project-less would
then become visible to every member of every project, which is a
widening of exactly the boundary MAN-115 exists to enforce. The exemption
is keyed on the producer's own authoritative `agent_kind == "master"`.

Note the pre-existing asymmetry this fix closes: the per-AGENT routes have
always exempted a project-less agent (_enforce_agent_project_access's
`if not project_id: return`), so the detail routes and the list route
disagreed about this one agent, and only the list dropped it.

These tests drive the REAL ASGI route (not a direct handler call), the
same approach as test_routes_fleet_auth_guard.py, so the dependency wiring
and the filter body are both exercised. No database is required: the
non-owner situation is expressed by patching _visible_project_ids to a
concrete set, which is exactly what it returns for a member, and the real
filtering code then runs against it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI

from server_modules import routes_fleet


WORKSPACE_ID = "ws-team"
TENANT_ID = "tenant-team"
MEMBER_PROJECT_ID = "project_member_belongs_to"
FOREIGN_PROJECT_ID = "project_member_was_never_added_to"


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(routes_fleet.router)
    return app


def _member_user() -> dict:
    """A real, authenticated teammate whose workspace role is `member` --
    the exact role a /join/{token} acceptance grants."""
    return {
        "user_id": "teammate-1",
        "email": "teammate@example.com",
        "auth_type": "bearer",
        "workspace_access": {
            WORKSPACE_ID: {
                "workspace_id": WORKSPACE_ID,
                "tenant_id": TENANT_ID,
                "role": "member",
                "tenant_role": "member",
            }
        },
    }


def _fleet_agents_payload() -> dict:
    """Three agents covering the three cases that must be decided
    differently. The master row mirrors what fleet_list_agents really
    emits for a seeded workspace: project_id "" and agent_kind "master".
    Its `role` is deliberately "specialist" because that is what
    resolve_agent_role actually returns for the seeded master install --
    its metadata carries no `role` key -- which is precisely why the fix
    must not key on role either."""
    return {
        "ok": True,
        "agents": [
            {
                "agent_id": "ainstall_ws-team_sage",
                "label": "Sage",
                "role": "specialist",
                "agent_kind": "master",
                "project_id": "",
            },
            {
                "agent_id": "agent-in-my-project",
                "label": "Support Bot",
                "role": "specialist",
                "agent_kind": "specialist",
                "project_id": MEMBER_PROJECT_ID,
            },
            {
                "agent_id": "agent-in-someone-elses-project",
                "label": "Finance Bot",
                "role": "specialist",
                "agent_kind": "specialist",
                "project_id": FOREIGN_PROJECT_ID,
            },
        ],
    }


async def _get_agents_as_member() -> dict:
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _member_user
    with patch.object(
        routes_fleet, "_resolve_tenant", AsyncMock(return_value=TENANT_ID)
    ), patch.object(
        routes_fleet,
        "_visible_project_ids",
        AsyncMock(return_value={MEMBER_PROJECT_ID}),
    ), patch(
        "server_modules.fleet_tools.fleet_list_agents",
        AsyncMock(return_value=_fleet_agents_payload()),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/w/{WORKSPACE_ID}/fleet/agents")
    assert response.status_code == 200
    return response.json()


@pytest.mark.anyio
async def test_member_receives_the_workspace_level_agent() -> None:
    """THE regression. Before the fix this list came back without the master
    install, so findSageAgent returned null and the teammate had no Ask AI
    console at all -- not an empty one, not a permission message, simply no
    control."""
    payload = await _get_agents_as_member()
    ids = {a["agent_id"] for a in payload["agents"]}
    assert "ainstall_ws-team_sage" in ids


@pytest.mark.anyio
async def test_member_still_cannot_see_an_agent_in_a_project_they_are_not_in() -> None:
    """MAN-115 must be untouched by the MAN-201 exemption: the per-project
    boundary is the whole reason this filter exists."""
    payload = await _get_agents_as_member()
    ids = {a["agent_id"] for a in payload["agents"]}
    assert "agent-in-someone-elses-project" not in ids
    assert "agent-in-my-project" in ids


@pytest.mark.anyio
async def test_a_project_less_specialist_is_NOT_exempted() -> None:
    """The negative that pins the fix to `agent_kind` rather than to an
    empty project_id. A naive `or not project_id` exemption would pass every
    other assertion in this file and silently widen MAN-115's boundary for
    any specialist that ends up project-less."""
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _member_user
    payload = {
        "ok": True,
        "agents": [
            {
                "agent_id": "orphan-specialist",
                "label": "Orphan",
                "role": "specialist",
                "agent_kind": "specialist",
                "project_id": "",
            },
        ],
    }
    with patch.object(
        routes_fleet, "_resolve_tenant", AsyncMock(return_value=TENANT_ID)
    ), patch.object(
        routes_fleet,
        "_visible_project_ids",
        AsyncMock(return_value={MEMBER_PROJECT_ID}),
    ), patch(
        "server_modules.fleet_tools.fleet_list_agents",
        AsyncMock(return_value=payload),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/w/{WORKSPACE_ID}/fleet/agents")

    assert response.status_code == 200
    assert response.json()["agents"] == []


@pytest.mark.anyio
async def test_owner_is_unaffected_and_still_sees_every_agent() -> None:
    """_visible_project_ids returns None for an owner, so the filter branch
    is skipped entirely. Asserted so a future change to the exemption can
    never quietly start filtering an owner."""
    app = _build_app()
    owner = dict(_member_user())
    owner["workspace_access"] = {
        WORKSPACE_ID: {
            "workspace_id": WORKSPACE_ID,
            "tenant_id": TENANT_ID,
            "role": "owner",
            "tenant_role": "owner",
        }
    }
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = lambda: owner
    with patch.object(
        routes_fleet, "_resolve_tenant", AsyncMock(return_value=TENANT_ID)
    ), patch.object(
        routes_fleet, "_visible_project_ids", AsyncMock(return_value=None)
    ), patch(
        "server_modules.fleet_tools.fleet_list_agents",
        AsyncMock(return_value=_fleet_agents_payload()),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/w/{WORKSPACE_ID}/fleet/agents")

    assert response.status_code == 200
    assert len(response.json()["agents"]) == 3


def test_predicate_keys_on_agent_kind_only() -> None:
    """Direct coverage of the predicate, including the two shapes a reader
    is most likely to get wrong when editing it later."""
    assert routes_fleet._is_workspace_scoped_agent({"agent_kind": "master"}) is True
    assert routes_fleet._is_workspace_scoped_agent({"agent_kind": "MASTER"}) is True
    assert routes_fleet._is_workspace_scoped_agent({"agent_kind": "specialist"}) is False
    # An empty project_id alone must never be enough.
    assert routes_fleet._is_workspace_scoped_agent({"project_id": ""}) is False
    # A missing field must fail CLOSED, not open.
    assert routes_fleet._is_workspace_scoped_agent({}) is False


def test_install_summary_emits_agent_kind() -> None:
    """The chain that makes the filter above possible, guarded at its
    source. Both listing queries (Postgres and the SQLite fallback) already
    SELECT ad.agent_kind and both map rows through _row_to_install_summary;
    it resolved the value for project_install_contract_fields but never
    returned it, so no caller could see it. If this stops being emitted the
    route filter silently stops finding any master agent and MAN-201
    regresses with no error anywhere -- exactly the failure mode that made
    the original bug invisible."""
    from server_modules import agent_registry_repository as repo

    summary = repo._row_to_install_summary(
        {
            "id": "ainstall_ws_sage",
            "tenant_id": TENANT_ID,
            "workspace_id": WORKSPACE_ID,
            "label": "Sage",
            "agent_kind": "master",
        }
    )
    assert summary is not None
    assert summary["agent_kind"] == "master"

    # And the default is the specialist kind, never an empty string, so the
    # predicate above always has a real token to compare.
    specialist = repo._row_to_install_summary(
        {"id": "ainstall_x", "tenant_id": TENANT_ID, "workspace_id": WORKSPACE_ID}
    )
    assert specialist is not None
    assert specialist["agent_kind"] == repo.SPECIALIST_AGENT_KIND
