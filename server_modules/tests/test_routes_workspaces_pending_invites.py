"""In-app pending-invite visibility for the INVITEE (MAN: 'the founder invited
someone and nothing appeared anywhere for them to see it').

GET /workspaces/{id}/invites (routes_workspaces.list_workspace_pending_invites_route)
already existed but requires the caller to already be a member of the target
workspace -- structurally useless to the person being invited, who is by
definition not a member yet. This file covers the three new invitee-facing
routes:

  - GET  /workspaces/invites/pending           -- "what am I invited to"
  - POST /workspaces/invites/{invite_id}/join  -- accept without a token
  - POST /workspaces/invites/{invite_id}/decline -- a real, recorded state

Uses the same real-registration pattern as test_routes_workspaces_invites.py
(auth.register_user against the per-test SQLite DB, not synthetic dicts).
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from fastapi import FastAPI

from server_modules import auth
from server_modules import control_plane_repository
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


async def _create_invite(app: FastAPI, caller_current_user: dict, workspace_id: str, *, email: str, role: str = "member") -> httpx.Response:
    app.dependency_overrides[routes_workspaces.get_current_user] = lambda: caller_current_user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(f"/workspaces/{workspace_id}/invites", json={"email": email, "role": role})


async def _list_my_pending_invites(app: FastAPI, current_user: dict) -> httpx.Response:
    app.dependency_overrides[routes_workspaces.get_current_user] = lambda: current_user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get("/workspaces/invites/pending")


async def _join_invite(app: FastAPI, current_user: dict, invite_id: str) -> httpx.Response:
    app.dependency_overrides[routes_workspaces.get_current_user] = lambda: current_user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(f"/workspaces/invites/{invite_id}/join")


async def _decline_invite(app: FastAPI, current_user: dict, invite_id: str) -> httpx.Response:
    app.dependency_overrides[routes_workspaces.get_current_user] = lambda: current_user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(f"/workspaces/invites/{invite_id}/decline")


@pytest.mark.anyio
async def test_invitee_sees_pending_invite_with_workspace_name(second_real_user_in_workspace):
    app = _build_app()
    owner = _register_owner()
    invitee_email = "sees-invite@example.com"

    invitee = second_real_user_in_workspace(owner["workspace_id"], email=invitee_email, join_workspace=False)

    create_response = await _create_invite(app, owner["current_user"], owner["workspace_id"], email=invitee_email, role="member")
    assert create_response.status_code == 200
    invite_id = create_response.json()["invite"]["id"]

    owner_workspace = await control_plane_repository.get_workspace_by_id(owner["workspace_id"])
    expected_name = owner_workspace["name"]
    # The whole point of MAN-A: a real workspace never carries its own id as
    # its display name.
    assert expected_name != owner["workspace_id"]

    list_response = await _list_my_pending_invites(app, invitee["current_user"])
    assert list_response.status_code == 200
    items = list_response.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == invite_id
    assert items[0]["workspace_id"] == owner["workspace_id"]
    assert items[0]["workspace_name"] == expected_name
    assert items[0]["role"] == "member"


@pytest.mark.anyio
async def test_user_with_no_invites_sees_an_empty_list(second_real_user_in_workspace):
    app = _build_app()
    owner = _register_owner()
    bystander = second_real_user_in_workspace(owner["workspace_id"], email="bystander@example.com", join_workspace=False)

    response = await _list_my_pending_invites(app, bystander["current_user"])
    assert response.status_code == 200
    assert response.json()["items"] == []


@pytest.mark.anyio
async def test_join_without_a_token_grants_real_membership(second_real_user_in_workspace):
    app = _build_app()
    owner = _register_owner()
    invitee_email = "joins-in-app@example.com"
    invitee = second_real_user_in_workspace(owner["workspace_id"], email=invitee_email, join_workspace=False)

    create_response = await _create_invite(app, owner["current_user"], owner["workspace_id"], email=invitee_email, role="member")
    invite_id = create_response.json()["invite"]["id"]

    join_response = await _join_invite(app, invitee["current_user"], invite_id)
    assert join_response.status_code == 200
    payload = join_response.json()
    assert payload["workspace_id"] == owner["workspace_id"]
    assert payload["role"] == "member"
    assert payload["status"] == "accepted"

    real_memberships = auth._list_workspace_memberships(invitee["user_id"])
    assert any(
        str(item.get("workspace_id")) == owner["workspace_id"] and item.get("role") == "member"
        for item in real_memberships
    )

    # No longer pending, from either angle.
    assert await _list_my_pending_invites(app, invitee["current_user"])
    remaining = (await _list_my_pending_invites(app, invitee["current_user"])).json()["items"]
    assert remaining == []


@pytest.mark.anyio
async def test_join_rejects_an_invite_issued_to_a_different_email(second_real_user_in_workspace):
    app = _build_app()
    owner = _register_owner()

    create_response = await _create_invite(app, owner["current_user"], owner["workspace_id"], email="intended@example.com", role="member")
    invite_id = create_response.json()["invite"]["id"]

    wrong_user = second_real_user_in_workspace(owner["workspace_id"], email="not-the-invitee@example.com", join_workspace=False)

    response = await _join_invite(app, wrong_user["current_user"], invite_id)
    assert response.status_code == 403
    real_memberships = auth._list_workspace_memberships(wrong_user["user_id"])
    assert not any(str(item.get("workspace_id")) == owner["workspace_id"] for item in real_memberships)


@pytest.mark.anyio
async def test_decline_marks_the_invite_declined_and_grants_no_membership(second_real_user_in_workspace):
    app = _build_app()
    owner = _register_owner()
    invitee_email = "declines@example.com"
    invitee = second_real_user_in_workspace(owner["workspace_id"], email=invitee_email, join_workspace=False)

    create_response = await _create_invite(app, owner["current_user"], owner["workspace_id"], email=invitee_email, role="member")
    invite_id = create_response.json()["invite"]["id"]

    decline_response = await _decline_invite(app, invitee["current_user"], invite_id)
    assert decline_response.status_code == 200
    payload = decline_response.json()
    assert payload["workspace_id"] == owner["workspace_id"]
    assert payload["status"] == "declined"

    # A real, recorded state: fetching the invite row directly shows
    # 'declined', not 'pending' and not silently gone.
    invite_row = await control_plane_repository.get_workspace_member_invite(invite_id)
    assert invite_row["status"] == "declined"

    # No membership was granted.
    real_memberships = auth._list_workspace_memberships(invitee["user_id"])
    assert not any(str(item.get("workspace_id")) == owner["workspace_id"] for item in real_memberships)

    # It also drops off the invitee's own pending list.
    remaining = (await _list_my_pending_invites(app, invitee["current_user"])).json()["items"]
    assert remaining == []


@pytest.mark.anyio
async def test_declined_invite_cannot_then_be_joined(second_real_user_in_workspace):
    app = _build_app()
    owner = _register_owner()
    invitee_email = "declines-then-tries-join@example.com"
    invitee = second_real_user_in_workspace(owner["workspace_id"], email=invitee_email, join_workspace=False)

    create_response = await _create_invite(app, owner["current_user"], owner["workspace_id"], email=invitee_email, role="member")
    invite_id = create_response.json()["invite"]["id"]

    decline_response = await _decline_invite(app, invitee["current_user"], invite_id)
    assert decline_response.status_code == 200

    join_response = await _join_invite(app, invitee["current_user"], invite_id)
    assert join_response.status_code == 404


@pytest.mark.anyio
async def test_decline_rejects_an_invite_issued_to_a_different_email(second_real_user_in_workspace):
    app = _build_app()
    owner = _register_owner()

    create_response = await _create_invite(app, owner["current_user"], owner["workspace_id"], email="intended-2@example.com", role="member")
    invite_id = create_response.json()["invite"]["id"]

    wrong_user = second_real_user_in_workspace(owner["workspace_id"], email="also-not-the-invitee@example.com", join_workspace=False)

    response = await _decline_invite(app, wrong_user["current_user"], invite_id)
    assert response.status_code == 403

    invite_row = await control_plane_repository.get_workspace_member_invite(invite_id)
    assert invite_row["status"] == "pending"
