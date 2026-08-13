"""Regression coverage for the invite-flow tenant-resolution bug reported
live: inviting a brand-new email (someone with no account yet) directly into
a project failed with "Project not found in this workspace."

Root cause, confirmed against a real database: routes_workspaces.py's
_control_plane_tenant_id() prioritized a stale `tenant_id` field stored on
the USER record over the authoritative per-workspace lookup
(auth_module.workspace_tenant_id / control_plane_repository.
resolve_tenant_id_for_workspace, the same helper routes_fleet.py's
_resolve_tenant() already used correctly). users.tenant_id is written once
at signup -- the tenant of the user's first/"home" workspace -- and never
updated as that user is invited into OTHER workspaces bound to a different
tenant. In production this was observed as:

    users.tenant_id            = "tenant-1"              (stale)
    workspace's real tenant_id = "tenant_f6a63cf2c367"    (authoritative)

so create_workspace_invite_route's up-front project_id validation
(projects_repository.get_project(tenant_id=..., workspace_id=..., ...))
ran against the WRONG tenant and always returned None, producing the 400
"Project not found in this workspace." error for a project that genuinely
existed in that workspace.

The fix makes _control_plane_tenant_id() resolve straight from
control_plane_repository.resolve_tenant_id_for_workspace(workspace_id) --
never reading tenant_id off the user/current_user record at all -- mirroring
routes_fleet.py's _resolve_tenant() exactly, so the two cannot drift apart
again.

This is a NEW file (not an addition to test_routes_workspaces_invites.py or
test_workspace_invite_project_access_man70.py) for the same reason
test_workspace_invite_project_access_man70.py's own docstring gives:
server_modules/tests/conftest.py and every pre-existing test file may be
owned by a different, concurrently-running agent per the collision protocol
(docs/AGENT-OPERATING-RULES.md) -- helpers below are intentionally
duplicated from those files rather than imported.

Real Postgres required for every test here (skipped otherwise): the bug is
specifically in the Postgres `users.tenant_id` column (the SQLite local
auth fallback's `users` table has no tenant_id column at all -- tenant is
always joined fresh from workspace_registry there, so this exact defect
cannot occur on that path). Point DATABASE_URL at a disposable database
whose name contains "test" (server_modules/tests/conftest.py's
pytest_configure hard-refuses anything else -- MAN-139/MAN-202), e.g.:

    DATABASE_URL=postgresql://mansur@localhost:5432/empyralis_test_workspace_tenant \\
        python3 -m pytest server_modules/tests/test_workspace_invite_tenant_resolution.py -q

DATABASE_URL is read directly from the process environment only -- this
suite does NOT fall back to a repo-root .env the way some other suites do,
per this task's explicit instruction to never inherit it implicitly.
"""

from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Tuple

import httpx
import pytest
from fastapi import FastAPI

from server_modules import auth
from server_modules import control_plane_repository
from server_modules import projects_repository
from server_modules import routes_workspaces

_NO_PG_REASON = "No Postgres reachable (export DATABASE_URL, pointed at a *_test* database, to run this suite)"


def _database_url_available() -> bool:
    return bool(os.environ.get("DATABASE_URL", "").strip())


@asynccontextmanager
async def _pg_scope() -> AsyncIterator[Tuple[Any, list]]:
    """Yields (pool, cleanup_workspace_ids). Every test appends the real
    workspace_id(s) it creates to cleanup_workspace_ids; teardown deletes
    every row this suite could have written, scoped by those workspace_ids,
    even if a test raises partway through. Mirrors
    test_workspace_invite_project_access_man70.py's own _pg_scope exactly."""
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        pytest.skip(_NO_PG_REASON)
        return
    cleanup_workspace_ids: list = []
    try:
        yield pool, cleanup_workspace_ids
    finally:
        for workspace_id in cleanup_workspace_ids:
            if not workspace_id:
                continue
            try:
                await pool.execute("DELETE FROM project_memberships WHERE workspace_id = $1", workspace_id)
                await pool.execute("DELETE FROM projects WHERE workspace_id = $1", workspace_id)
                await pool.execute("DELETE FROM workspace_member_invites WHERE workspace_id = $1", workspace_id)
            except Exception:
                pass


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(routes_workspaces.router)
    return app


def _register_owner(*, email: str | None = None, password: str = "Own3r-Secret-Pass!") -> dict:
    """Real registration against Postgres (control_plane_repository.
    create_local_password_account, via auth.register_user) -- the owner's
    own bootstrap workspace/tenant binding becomes the target for the test,
    same as a real first user. Duplicated from
    test_workspace_invite_project_access_man70.py's helper of the same name
    -- see module docstring for why."""
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
    # The AUTHORITATIVE tenant_id for this workspace, read back from the
    # real (workspace_id -> tenant_id) binding register_user's bootstrap
    # created -- NOT assumed equal to workspace_id or to anything this file
    # controls. Any Postgres row this test writes directly (projects) must
    # use THIS value to match what the fixed route/repository resolve.
    entry = workspace_access.get(workspace_id) or {}
    real_tenant_id = str(entry.get("tenant_id") or "").strip() or workspace_id
    return {
        "user_id": user_id,
        "email": clean_email,
        "workspace_id": workspace_id,
        "tenant_id": real_tenant_id,
        "current_user": current_user,
    }


async def _corrupt_users_tenant_id(pool: Any, *, user_id: str, stale_tenant_id: str) -> None:
    """Directly overwrites the users.tenant_id column with an arbitrary,
    WRONG value -- reproducing the exact production observation
    (users.tenant_id="tenant-1" while the workspace's real tenant_id was
    "tenant_f6a63cf2c367"). This is the regression guard: every test that
    calls this must still resolve the workspace's real tenant, proving the
    fix ignores this column for request-scoped resolution."""
    await pool.execute("UPDATE users SET tenant_id = $1 WHERE id = $2", stale_tenant_id, user_id)
    row = await pool.fetchrow("SELECT tenant_id FROM users WHERE id = $1", user_id)
    assert row is not None and str(row["tenant_id"]) == stale_tenant_id, (
        "test setup failed to actually corrupt users.tenant_id -- the assertions below would not "
        "be testing what this file claims"
    )


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
    transport = httpx.ASGITransport(app=app)
    payload: dict = {"email": email, "role": role}
    if project_id is not None:
        payload["project_id"] = project_id
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(f"/workspaces/{workspace_id}/invites", json=payload)


def _register_invitee(*, email: str, password: str = "Inv1tee-Secret-Pass!") -> dict:
    """A real, distinct second user with NO prior relationship to the
    inviting workspace -- used for the "invite an existing workspace
    member" test."""
    auth.register_user(email, password)
    user = auth._find_user_by_email(email)
    assert isinstance(user, dict) and user.get("id"), f"registration did not persist a user row for {email}"
    return {"user_id": str(user["id"]).strip(), "email": email}


async def _invite_row_tenant_id(pool: Any, *, workspace_id: str, email: str) -> str:
    row = await pool.fetchrow(
        "SELECT tenant_id FROM workspace_member_invites WHERE workspace_id = $1 AND email = $2 "
        "ORDER BY created_at DESC LIMIT 1",
        workspace_id,
        email,
    )
    assert row is not None, "no invite row was persisted"
    return str(row["tenant_id"])


# ── 1. THE regression guard: a brand-new email (no account yet) invited into
#      a real project succeeds even though the inviting owner's OWN
#      users.tenant_id row is stale/wrong -- this is the exact production
#      failure ("Project not found in this workspace.") reproduced and
#      closed.
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_invite_new_email_succeeds_despite_stale_users_tenant_id():
    if not _database_url_available():
        pytest.skip(_NO_PG_REASON)
    async with _pg_scope() as (pool, cleanup_workspace_ids):
        app = _build_app()
        owner = _register_owner()
        cleanup_workspace_ids.append(owner["workspace_id"])
        real_tenant_id = owner["tenant_id"]

        # Corrupt the owner's OWN users.tenant_id to a different, plausible-
        # looking tenant token -- reproducing "tenant-1" vs
        # "tenant_f6a63cf2c367" from the real incident.
        stale_tenant_id = "tenant-1"
        assert stale_tenant_id != real_tenant_id, "test fixture collision: pick a stale value that differs from the real one"
        await _corrupt_users_tenant_id(pool, user_id=owner["user_id"], stale_tenant_id=stale_tenant_id)

        # A real project, created under the workspace's AUTHORITATIVE
        # tenant -- exactly what get_project's tenant_id AND workspace_id
        # AND id WHERE clause requires to find it.
        project = await projects_repository.create_project(
            tenant_id=real_tenant_id, workspace_id=owner["workspace_id"], name="Regression Project"
        )

        brand_new_email = f"brand-new-{uuid.uuid4().hex[:10]}@example.com"
        assert auth._find_user_by_email(brand_new_email) is None, "fixture email must belong to nobody"

        response = await _create_invite(
            app, owner["current_user"], owner["workspace_id"],
            email=brand_new_email, role="member", project_id=project["id"],
        )

        assert response.status_code == 200, (
            f"invite failed with stale users.tenant_id in play: {response.status_code} {response.text}"
        )
        body = response.json()
        assert body["invite"]["project_id"] == project["id"]
        assert body["invite"]["email"] == brand_new_email

        # The invite must be stored under the workspace's REAL tenant, not
        # the stale value that was sitting on the owner's user row.
        persisted_tenant_id = await _invite_row_tenant_id(
            pool, workspace_id=owner["workspace_id"], email=brand_new_email
        )
        assert persisted_tenant_id == real_tenant_id
        assert persisted_tenant_id != stale_tenant_id


# ── 2. An invite with no EXPLICIT project_id (the common case -- what
#      MembersSection.tsx actually sends) must also still succeed under the
#      same stale-tenant-id condition -- proving the fix isn't scoped
#      narrowly to the project-lookup branch alone.
#
#      MAN-335 superseded this test's original assertion
#      (`project_id is None`) -- that was the exact bug: a project-less
#      invite used to grant literally nothing, and an accepted teammate saw
#      zero projects forever. It now defaults to the workspace's own
#      default project (never "every project" -- see
#      test_man335_workspace_invite_default_project_access.py for the full
#      fix). This test's OWN subject -- tenant resolution staying correct
#      under a stale users.tenant_id -- is unchanged; only the shape of
#      "succeeds" is corrected here.
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_invite_new_email_with_no_project_succeeds_despite_stale_users_tenant_id():
    if not _database_url_available():
        pytest.skip(_NO_PG_REASON)
    async with _pg_scope() as (pool, cleanup_workspace_ids):
        app = _build_app()
        owner = _register_owner()
        cleanup_workspace_ids.append(owner["workspace_id"])
        real_tenant_id = owner["tenant_id"]

        await _corrupt_users_tenant_id(pool, user_id=owner["user_id"], stale_tenant_id="tenant-1")

        brand_new_email = f"brand-new-noproj-{uuid.uuid4().hex[:10]}@example.com"
        response = await _create_invite(
            app, owner["current_user"], owner["workspace_id"], email=brand_new_email, role="member",
        )

        assert response.status_code == 200, response.text
        # MAN-335: defaults to the workspace's default project rather than
        # granting nothing. The important thing for THIS test is that the
        # lookup ensure_default_project needed (tenant_id + workspace_id)
        # resolved correctly despite the stale users.tenant_id, proven by
        # the invite succeeding at all and the persisted tenant_id check
        # below -- not any particular project id.
        assert response.json()["invite"]["project_id"] is not None

        persisted_tenant_id = await _invite_row_tenant_id(
            pool, workspace_id=owner["workspace_id"], email=brand_new_email
        )
        assert persisted_tenant_id == real_tenant_id


# ── 3. Inviting an EXISTING workspace member (a real, already-registered
#      user, not just an email string) into a project still works, under
#      the same stale-users.tenant_id condition.
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_invite_existing_registered_user_into_project_succeeds_despite_stale_tenant_id():
    if not _database_url_available():
        pytest.skip(_NO_PG_REASON)
    async with _pg_scope() as (pool, cleanup_workspace_ids):
        app = _build_app()
        owner = _register_owner()
        cleanup_workspace_ids.append(owner["workspace_id"])
        real_tenant_id = owner["tenant_id"]

        await _corrupt_users_tenant_id(pool, user_id=owner["user_id"], stale_tenant_id="tenant-1")

        # A real, already-registered second user (their OWN users row has
        # its own -- correct, for their own home workspace -- tenant_id;
        # irrelevant here, since this invite is scoped to the OWNER's
        # workspace/tenant, not the invitee's).
        existing_email = f"existing-member-{uuid.uuid4().hex[:10]}@example.com"
        existing_user = _register_invitee(email=existing_email)
        assert existing_user["user_id"]

        project = await projects_repository.create_project(
            tenant_id=real_tenant_id, workspace_id=owner["workspace_id"], name="Existing Member Project"
        )

        response = await _create_invite(
            app, owner["current_user"], owner["workspace_id"],
            email=existing_email, role="member", project_id=project["id"],
        )

        assert response.status_code == 200, response.text
        assert response.json()["invite"]["project_id"] == project["id"]

        persisted_tenant_id = await _invite_row_tenant_id(
            pool, workspace_id=owner["workspace_id"], email=existing_email
        )
        assert persisted_tenant_id == real_tenant_id


# ── 4. Direct unit coverage of the fixed resolver itself: given a
#      current_user/user pair carrying a wrong tenant_id, it must resolve
#      to the workspace's real tenant -- not the value on either dict. This
#      is the tightest possible regression guard for _control_plane_
#      tenant_id() specifically (as opposed to the end-to-end route tests
#      above).
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_control_plane_tenant_id_ignores_wrong_tenant_id_on_user_records():
    if not _database_url_available():
        pytest.skip(_NO_PG_REASON)
    async with _pg_scope() as (pool, cleanup_workspace_ids):
        owner = _register_owner()
        cleanup_workspace_ids.append(owner["workspace_id"])
        real_tenant_id = owner["tenant_id"]

        wrong_current_user = {**owner["current_user"], "tenant_id": "not-the-real-tenant"}
        wrong_user_record = {"id": owner["user_id"], "tenant_id": "also-not-the-real-tenant"}

        resolved = await routes_workspaces._control_plane_tenant_id(
            wrong_current_user, owner["workspace_id"], wrong_user_record
        )
        assert resolved == real_tenant_id
        assert resolved not in {"not-the-real-tenant", "also-not-the-real-tenant"}

        # Same guarantee with no `user` argument at all (the shape every
        # other call site in routes_workspaces.py uses).
        resolved_no_user_arg = await routes_workspaces._control_plane_tenant_id(
            wrong_current_user, owner["workspace_id"]
        )
        assert resolved_no_user_arg == real_tenant_id
