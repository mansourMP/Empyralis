"""Multiplayer Projects Phase 1 -- workspace invite links.

Covers the full real path: an owner creates an invite (POST .../invites),
a second, genuinely-registered user accepts it (POST /workspaces/invites/
accept) and becomes a real workspace member, list_workspace_members shows
both, a non-owner cannot create an invite, a repository-level guarantee
blocks a role above the inviter's own, and a tampered/expired token is
rejected.

Uses the `second_real_user_in_workspace` fixture from conftest.py -- REAL
auth.register_user() + auth.upsert_workspace_membership() against the same
per-test SQLite DB the app itself would use, not the synthetic
current_user dict pattern used in test_routes_fleet_delete_agent.py.
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
    """Real registration for the workspace owner -- auth.register_user's own
    bootstrap workspace (created_local_password_account) becomes the target
    workspace_id for the test, exactly as it would for a real first user.
    """
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


async def _create_invite(
    app: FastAPI,
    caller_current_user: dict,
    workspace_id: str,
    *,
    email: str,
    role: str = "member",
) -> httpx.Response:
    app.dependency_overrides[routes_workspaces.get_current_user] = lambda: caller_current_user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(
            f"/workspaces/{workspace_id}/invites",
            json={"email": email, "role": role},
        )


async def _accept_invite(app: FastAPI, invitee_current_user: dict, token: str) -> httpx.Response:
    app.dependency_overrides[routes_workspaces.get_current_user] = lambda: invitee_current_user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post("/workspaces/invites/accept", json={"token": token})


@pytest.mark.anyio
async def test_owner_creates_invite_and_returns_signed_link():
    app = _build_app()
    owner = _register_owner()

    response = await _create_invite(app, owner["current_user"], owner["workspace_id"], email="invitee@example.com", role="member")

    assert response.status_code == 200
    payload = response.json()
    assert payload["token"] and isinstance(payload["token"], str)
    assert payload["token"].count(".") == 2, "expected a header.payload.signature shaped token"
    assert payload["invite"]["email"] == "invitee@example.com"
    assert payload["invite"]["role"] == "member"
    assert payload["invite"]["status"] == "pending"
    assert payload["invite"]["workspace_id"] == owner["workspace_id"]
    assert isinstance(payload["expires_at"], int) and payload["expires_at"] > 0

    pending = await control_plane_repository.list_pending_workspace_invites(owner["workspace_id"])
    assert [item["email"] for item in pending] == ["invitee@example.com"]


@pytest.mark.anyio
async def test_second_real_user_accepts_invite_and_becomes_member(second_real_user_in_workspace):
    app = _build_app()
    owner = _register_owner()
    invitee_email = "second-user@example.com"

    # A REAL, distinct second user -- registered for real, NOT yet a member
    # of owner['workspace_id'] (join_workspace=False). Registered BEFORE the
    # invite exists: auth.register_user() already auto-accepts any pending
    # invite matching the new user's email (auth.accept_workspace_invites_
    # for_user, called from both register and login) -- registering first
    # keeps that pre-existing email-match path from firing here, so this
    # test cleanly isolates the NEW explicit token-accept endpoint as the
    # thing that creates the membership.
    invitee = second_real_user_in_workspace(
        owner["workspace_id"],
        email=invitee_email,
        join_workspace=False,
    )
    assert owner["workspace_id"] not in invitee["current_user"]["workspace_access"]

    create_response = await _create_invite(app, owner["current_user"], owner["workspace_id"], email=invitee_email, role="member")
    assert create_response.status_code == 200
    token = create_response.json()["token"]

    accept_response = await _accept_invite(app, invitee["current_user"], token)
    assert accept_response.status_code == 200
    accept_payload = accept_response.json()
    assert accept_payload["workspace_id"] == owner["workspace_id"]
    assert accept_payload["role"] == "member"
    assert accept_payload["status"] == "accepted"

    # Real DB proof: the invitee is now genuinely a member of the workspace.
    real_memberships = auth._list_workspace_memberships(invitee["user_id"])
    assert any(
        str(item.get("workspace_id")) == owner["workspace_id"] and item.get("role") == "member"
        for item in real_memberships
    )

    pending = await control_plane_repository.list_pending_workspace_invites(owner["workspace_id"])
    assert pending == []


@pytest.mark.anyio
async def test_accept_invite_succeeds_when_invitee_signup_already_auto_accepted_it(second_real_user_in_workspace):
    """Regression test for the real-world ordering the previous test
    deliberately avoids (see its comment): an owner sends an invite link to
    someone who does NOT have an account yet, so the invite is created
    BEFORE the invitee registers. When that invitee signs up (via
    /join/{token} -> /signup?next=/join/{token} -> back to /join/{token}),
    auth.register_user's own accept_workspace_invites_for_user call already
    auto-accepts this exact invite (it matches their email) before the
    /join/{token} page ever calls POST /workspaces/invites/accept with the
    token. The explicit accept call must still report success (the user did
    get the right membership -- just via the login side effect), not the
    false-negative 404 the endpoint used to return here. And a genuine
    replay of the same token afterward must still be rejected as invalid.
    """
    app = _build_app()
    owner = _register_owner()
    invitee_email = "invited-before-signup@example.com"

    create_response = await _create_invite(app, owner["current_user"], owner["workspace_id"], email=invitee_email, role="member")
    assert create_response.status_code == 200
    token = create_response.json()["token"]

    # NOW the invitee registers -- this is the real /signup flow order, and
    # it silently auto-accepts the invite above as a side effect of
    # register_user() before the token is ever presented to the accept route.
    invitee = second_real_user_in_workspace(
        owner["workspace_id"],
        email=invitee_email,
        join_workspace=False,
    )
    # Proof the race already happened: the invitee is a real member even
    # though the explicit accept endpoint hasn't been called yet.
    real_memberships = auth._list_workspace_memberships(invitee["user_id"])
    assert any(
        str(item.get("workspace_id")) == owner["workspace_id"] and item.get("role") == "member"
        for item in real_memberships
    )
    pending_before = await control_plane_repository.list_pending_workspace_invites(owner["workspace_id"])
    assert pending_before == []

    # The /join/{token} page's call: must succeed, not 404.
    first_accept_response = await _accept_invite(app, invitee["current_user"], token)
    assert first_accept_response.status_code == 200
    first_payload = first_accept_response.json()
    assert first_payload["workspace_id"] == owner["workspace_id"]
    assert first_payload["role"] == "member"
    assert first_payload["status"] == "accepted"

    # A genuine second call with the same token (replay) must still be
    # rejected -- the fix must not turn this into a permanently-idempotent
    # endpoint.
    second_accept_response = await _accept_invite(app, invitee["current_user"], token)
    assert second_accept_response.status_code == 404
    assert second_accept_response.json()["detail"] == "Invite is no longer valid."


@pytest.mark.anyio
async def test_list_workspace_members_shows_owner_and_accepted_member(second_real_user_in_workspace):
    app = _build_app()
    owner = _register_owner()
    invitee_email = "roster-check@example.com"

    # Register the invitee before the invite exists (see the comment in
    # test_second_real_user_accepts_invite_and_becomes_member for why order
    # matters here).
    invitee = second_real_user_in_workspace(
        owner["workspace_id"],
        email=invitee_email,
        join_workspace=False,
    )

    create_response = await _create_invite(app, owner["current_user"], owner["workspace_id"], email=invitee_email, role="viewer")
    token = create_response.json()["token"]

    accept_response = await _accept_invite(app, invitee["current_user"], token)
    assert accept_response.status_code == 200

    app.dependency_overrides[routes_workspaces.get_current_user] = lambda: owner["current_user"]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(f"/workspaces/{owner['workspace_id']}/members")

    assert response.status_code == 200
    members = response.json()["items"]
    emails_and_roles = {(item["email"], item["role"]) for item in members}
    assert (owner["email"], "owner") in emails_and_roles
    assert (invitee_email, "viewer") in emails_and_roles
    assert len(members) == 2


@pytest.mark.anyio
async def test_non_owner_cannot_create_invite(second_real_user_in_workspace):
    app = _build_app()
    owner = _register_owner()
    member = second_real_user_in_workspace(owner["workspace_id"], role="member")

    response = await _create_invite(
        app, member["current_user"], owner["workspace_id"], email="someone-else@example.com", role="viewer"
    )

    assert response.status_code == 403


@pytest.mark.anyio
async def test_accept_invite_rejects_email_mismatch(second_real_user_in_workspace):
    app = _build_app()
    owner = _register_owner()

    create_response = await _create_invite(app, owner["current_user"], owner["workspace_id"], email="intended@example.com", role="member")
    token = create_response.json()["token"]

    # A real, authenticated user whose email does NOT match the invite.
    wrong_user = second_real_user_in_workspace(
        owner["workspace_id"],
        email="not-the-invitee@example.com",
        join_workspace=False,
    )

    response = await _accept_invite(app, wrong_user["current_user"], token)

    assert response.status_code == 403
    real_memberships = auth._list_workspace_memberships(wrong_user["user_id"])
    assert not any(str(item.get("workspace_id")) == owner["workspace_id"] for item in real_memberships)


@pytest.mark.anyio
async def test_repository_rejects_invite_role_above_inviters_own_role():
    owner = _register_owner()

    with pytest.raises(ValueError, match="above the inviter's own role"):
        await control_plane_repository.create_workspace_invite(
            workspace_id=owner["workspace_id"],
            tenant_id="tenant-1",
            email="escalation@example.com",
            role="owner",
            invited_by_user_id=owner["user_id"],
            invited_by_role="member",
        )


@pytest.mark.anyio
async def test_tampered_invite_token_is_rejected():
    app = _build_app()
    owner = _register_owner()

    create_response = await _create_invite(app, owner["current_user"], owner["workspace_id"], email="tamper@example.com", role="member")
    token = create_response.json()["token"]

    header_segment, payload_segment, signature_segment = token.split(".")
    corrupted_signature = ("A" if signature_segment[0] != "A" else "B") + signature_segment[1:]
    tampered_token = f"{header_segment}.{payload_segment}.{corrupted_signature}"

    with pytest.raises(ValueError):
        control_plane_repository.verify_workspace_invite_token(tampered_token)

    accept_response = await _accept_invite(app, owner["current_user"], tampered_token)
    assert accept_response.status_code == 400


@pytest.mark.anyio
async def test_expired_invite_token_is_rejected(monkeypatch: pytest.MonkeyPatch):
    owner = _register_owner()

    invite = await control_plane_repository.create_workspace_invite(
        workspace_id=owner["workspace_id"],
        tenant_id="tenant-1",
        email="expired@example.com",
        role="member",
        invited_by_user_id=owner["user_id"],
        invited_by_role="owner",
        ttl_seconds=60,
    )
    token = invite["token"]

    # Fast-forward past the token's own "exp" claim.
    monkeypatch.setattr(control_plane_repository.time, "time", lambda: invite["expires_at"] + 10)

    with pytest.raises(ValueError, match="expired"):
        control_plane_repository.verify_workspace_invite_token(token)
