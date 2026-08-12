"""Owner-visible invite status for the panel where project members are
managed (ProjectMemberAdd.tsx): 'From the project page an owner cannot tell
whether an invite was accepted, is still pending, or failed.'

GET /workspaces/{workspace_id}/projects/{project_id}/invites
(routes_workspaces.list_project_invite_status_route) is the new,
project-scoped, every-status counterpart to the existing workspace-wide,
pending-only GET /workspaces/{id}/invites. It also surfaces
`email_delivery_status`, persisted by
control_plane_repository.record_workspace_invite_email_delivery at send
time (wired from create_workspace_invite_route) so a delivery failure stays
visible after the one-time creation toast is gone -- pending / accepted /
failed-to-send are three different facts, never collapsed to one.

The `projects` table itself is Postgres-only (projects_repository.create_project
/ get_project both return early with no SQLite fallback) -- this repo's own
test_workspace_invite_project_access_man70.py skips its whole file without a
live DATABASE_URL for exactly that reason. These tests must run under
`DATABASE_URL=` per this repo's standing instruction, so project_id here is
a plain string and projects_repository.get_project is monkeypatched to
"resolve" it -- the thing under test (invite status filtering by
metadata.project_id, stored entirely in the SQLite-backed
workspace_member_invites table) never touches the `projects` table itself.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from server_modules import auth
from server_modules import control_plane_repository
from server_modules import projects_repository
from server_modules import routes_workspaces


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(routes_workspaces.router)
    return app


def _register_owner(*, email: str | None = None, password: str = "Own3r-Secret-Pass!") -> dict:
    clean_email = (email or f"owner-{uuid.uuid4().hex[:10]}@example.com").strip().lower()
    auth.register_user(clean_email, password)
    user = auth._find_user_by_email(clean_email)
    assert isinstance(user, dict) and user.get("id")
    user_id = str(user["id"]).strip()

    memberships = auth._list_workspace_memberships(user_id)
    assert memberships, "registration did not create a bootstrap workspace membership"
    workspace_id = str(memberships[0]["workspace_id"]).strip()

    workspace_access = auth._effective_workspace_access(
        user_id=user_id,
        email=clean_email,
        role="owner",
        auth_type="bearer",
        is_admin=False,
        workspace_ids=[workspace_id],
    )
    current_user = {
        "user_id": user_id,
        "auth_type": "bearer",
        "email": clean_email,
        "workspace_ids": list(workspace_access.keys()),
        "workspace_access": workspace_access,
        "role": "owner",
        "is_admin": False,
        "auth_admin": False,
    }
    return {
        "user_id": user_id,
        "email": clean_email,
        "workspace_id": workspace_id,
        "current_user": current_user,
    }


@pytest.fixture(autouse=True)
def _fake_projects_resolve_to_real(monkeypatch: pytest.MonkeyPatch):
    """Make every project_id used below "exist" for the invite-creation
    route's own validation (create_workspace_invite_route calls
    projects_repository.get_project and 400s on None) without touching the
    Postgres-only `projects` table. Scoped to (workspace_id, project_id)
    exactly like the real function's WHERE clause, so a mismatched pair
    still resolves to None -- same "no accidental grant across tenants"
    property the real function has.
    """
    known: set[tuple[str, str]] = set()

    async def _fake_get_project(*, tenant_id, workspace_id, project_id):
        key = (str(workspace_id or "").strip(), str(project_id or "").strip())
        if key in known:
            return {"id": project_id, "tenant_id": tenant_id, "workspace_id": workspace_id, "name": "Test project"}
        return None

    monkeypatch.setattr(projects_repository, "get_project", _fake_get_project)
    return known


def _register_fake_project(known: set[tuple[str, str]], workspace_id: str) -> str:
    project_id = f"proj_{uuid.uuid4().hex[:12]}"
    known.add((workspace_id, project_id))
    return project_id


async def _create_invite(
    app: FastAPI,
    caller_current_user: dict,
    workspace_id: str,
    *,
    email: str,
    role: str = "member",
    project_id: str | None = None,
) -> httpx.Response:
    app.dependency_overrides[routes_workspaces.get_current_user] = lambda: caller_current_user
    body: dict = {"email": email, "role": role}
    if project_id:
        body["project_id"] = project_id
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(f"/workspaces/{workspace_id}/invites", json=body)


async def _list_project_invite_status(app: FastAPI, current_user: dict, workspace_id: str, project_id: str) -> httpx.Response:
    app.dependency_overrides[routes_workspaces.get_current_user] = lambda: current_user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get(f"/workspaces/{workspace_id}/projects/{project_id}/invites")


@pytest.mark.anyio
async def test_pending_project_invite_is_visible_with_delivery_status(monkeypatch: pytest.MonkeyPatch, _fake_projects_resolve_to_real):
    from server_modules import email_provider_service
    from server_modules import routes_workspaces as _routes_workspaces

    # No provider configured in this test env -> deliver_workspace_invite_email
    # reports "not_configured", which must be recorded and read back.
    monkeypatch.setattr(email_provider_service, "email_provider_configured", lambda: False)

    # This test is about the MAILER's outcome, so the inviter has to reach
    # the mailer at all. create_workspace_invite_route now withholds the send
    # entirely when the inviter has not verified their own address, and
    # `_register_owner` registers for real -- register_user writes the
    # verification code row before attempting a send that cannot succeed
    # here, leaving every freshly registered owner `pending`. Without this
    # the assertion below would read `withheld_unverified_sender` and the
    # not_configured path would never run. See
    # test_workspace_invite_email.py's own gate tests for that branch.
    async def _verified(_user_id: str) -> bool:
        return True

    monkeypatch.setattr(
        _routes_workspaces.email_verification_service, "is_verified", _verified
    )

    app = _build_app()
    owner = _register_owner()
    project_id = _register_fake_project(_fake_projects_resolve_to_real, owner["workspace_id"])

    create_response = await _create_invite(
        app, owner["current_user"], owner["workspace_id"], email="pending-status@example.com", role="member", project_id=project_id
    )
    assert create_response.status_code == 200
    invite_id = create_response.json()["invite"]["id"]

    status_response = await _list_project_invite_status(app, owner["current_user"], owner["workspace_id"], project_id)
    assert status_response.status_code == 200
    items = status_response.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == invite_id
    assert items[0]["status"] == "pending"
    assert items[0]["email_delivery_status"] == "not_configured"


@pytest.mark.anyio
async def test_accepted_invite_status_flips_from_pending_to_accepted(second_real_user_in_workspace, _fake_projects_resolve_to_real):
    app = _build_app()
    owner = _register_owner()
    project_id = _register_fake_project(_fake_projects_resolve_to_real, owner["workspace_id"])

    invitee_email = "accepts-and-shows-up@example.com"
    invitee = second_real_user_in_workspace(owner["workspace_id"], email=invitee_email, join_workspace=False)

    create_response = await _create_invite(
        app, owner["current_user"], owner["workspace_id"], email=invitee_email, role="member", project_id=project_id
    )
    invite_id = create_response.json()["invite"]["id"]

    before = await _list_project_invite_status(app, owner["current_user"], owner["workspace_id"], project_id)
    assert before.json()["items"][0]["status"] == "pending"

    # Accept via the in-app join route (no token needed).
    app.dependency_overrides[routes_workspaces.get_current_user] = lambda: invitee["current_user"]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        join_response = await client.post(f"/workspaces/invites/{invite_id}/join")
    assert join_response.status_code == 200

    after = await _list_project_invite_status(app, owner["current_user"], owner["workspace_id"], project_id)
    after_items = after.json()["items"]
    assert len(after_items) == 1
    assert after_items[0]["id"] == invite_id
    assert after_items[0]["status"] == "accepted"


@pytest.mark.anyio
async def test_invite_status_is_scoped_to_its_own_project_not_the_whole_workspace(_fake_projects_resolve_to_real):
    app = _build_app()
    owner = _register_owner()
    project_a = _register_fake_project(_fake_projects_resolve_to_real, owner["workspace_id"])
    project_b = _register_fake_project(_fake_projects_resolve_to_real, owner["workspace_id"])

    await _create_invite(app, owner["current_user"], owner["workspace_id"], email="for-a@example.com", role="member", project_id=project_a)
    await _create_invite(app, owner["current_user"], owner["workspace_id"], email="for-b@example.com", role="member", project_id=project_b)
    # A plain workspace invite with no project at all.
    await _create_invite(app, owner["current_user"], owner["workspace_id"], email="workspace-only@example.com", role="member")

    response_a = await _list_project_invite_status(app, owner["current_user"], owner["workspace_id"], project_a)
    items_a = response_a.json()["items"]
    assert [item["email"] for item in items_a] == ["for-a@example.com"]

    response_b = await _list_project_invite_status(app, owner["current_user"], owner["workspace_id"], project_b)
    items_b = response_b.json()["items"]
    assert [item["email"] for item in items_b] == ["for-b@example.com"]


@pytest.mark.anyio
async def test_member_without_project_access_cannot_see_that_projects_invites(
    monkeypatch: pytest.MonkeyPatch, second_real_user_in_workspace, _fake_projects_resolve_to_real,
):
    """Security review 2026-08-13 (sec/cross-tenant-authz): this route only
    checked workspace-viewer access, unlike its sibling
    routes_fleet.fleet_list_project_members which gates the identical
    "one project's roster" read through enforce_project_access. A workspace
    member with no project_memberships row for project_b could read
    project_b's invited emails/roles/invited-by ids just by knowing its
    project_id -- confirmed live (2026-08-12) against a real two-project
    seeded workspace: the request reached the same repository call an
    authorized project member would, with no 403/404 in between.

    is_project_member is monkeypatched the same way get_project already is
    in this file (Postgres-only project_memberships table, SQLite-backed
    test env) -- the caller here is deliberately never added to it.
    """
    from server_modules import projects_repository

    monkeypatch.setattr(
        projects_repository, "is_project_member",
        AsyncMock(return_value=False),
    )

    app = _build_app()
    owner = _register_owner()
    project_id = _register_fake_project(_fake_projects_resolve_to_real, owner["workspace_id"])

    await _create_invite(
        app, owner["current_user"], owner["workspace_id"],
        email="secret-invite@example.com", role="member", project_id=project_id,
    )

    outsider = second_real_user_in_workspace(owner["workspace_id"], role="member")

    response = await _list_project_invite_status(app, outsider["current_user"], owner["workspace_id"], project_id)
    # enforce_project_access's own contract: 404, not 403 and never a 200
    # carrying the other project's invite list.
    assert response.status_code == 404


@pytest.mark.anyio
async def test_member_with_project_access_can_still_see_that_projects_invites(
    monkeypatch: pytest.MonkeyPatch, second_real_user_in_workspace, _fake_projects_resolve_to_real,
):
    """The other half of the boundary: a real project member (not just an
    owner) can still use this panel -- the fix is a real gate, not a
    lockout."""
    from server_modules import projects_repository

    monkeypatch.setattr(
        projects_repository, "is_project_member",
        __import__("unittest.mock", fromlist=["AsyncMock"]).AsyncMock(return_value=True),
    )

    app = _build_app()
    owner = _register_owner()
    project_id = _register_fake_project(_fake_projects_resolve_to_real, owner["workspace_id"])

    create_response = await _create_invite(
        app, owner["current_user"], owner["workspace_id"],
        email="visible-invite@example.com", role="member", project_id=project_id,
    )
    invite_id = create_response.json()["invite"]["id"]

    teammate = second_real_user_in_workspace(owner["workspace_id"], role="member")

    response = await _list_project_invite_status(app, teammate["current_user"], owner["workspace_id"], project_id)
    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["id"] for item in items] == [invite_id]


@pytest.mark.asyncio
async def test_record_email_delivery_never_touches_invite_status():
    owner = _register_owner()
    invite = await control_plane_repository.create_workspace_invite(
        workspace_id=owner["workspace_id"],
        tenant_id="tenant-1",
        email="delivery-only@example.com",
        role="member",
        invited_by_user_id=owner["user_id"],
        invited_by_role="owner",
    )
    invite_id = invite["id"]

    await control_plane_repository.record_workspace_invite_email_delivery(
        invite_id=invite_id,
        delivery_status="failed",
    )

    stored = await control_plane_repository.get_workspace_member_invite(invite_id)
    assert stored["status"] == "pending"
    assert stored["metadata"]["email_delivery_status"] == "failed"
    assert stored["accepted_at"] is None
    assert stored["revoked_at"] is None
