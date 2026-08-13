"""MAN-70/MAN-114 follow-up: accepting a workspace invite grants zero project
access.

Verified bug: `projects_repository.add_project_member` had exactly two
callers -- create_project (auto-adding the creator) and the UI-less
POST /api/w/{workspace_id}/fleet/projects/{project_id}/members route.
Neither `auth.py` nor `routes_workspaces.py` ever called it, and
`routes_fleet._visible_project_ids` (the filter every non-owner list
endpoint applies) returns only the explicit `project_memberships` rows a
non-owner has. Net effect: invite a teammate, they accept, they log in --
they see no projects at all, unless someone separately makes them a
workspace owner.

The fix threads an optional `project_id` through invite creation (stored on
the invite's existing `metadata` jsonb column) and grants it via
`projects_repository.grant_invite_project_access` from BOTH acceptance
paths:
  - routes_workspaces.accept_workspace_invite_route (the /join/{token} link)
  - auth.accept_workspace_invites_for_user (the auto-accept-at-login path)

This is a NEW file -- server_modules/tests/conftest.py and every
pre-existing test file are owned by a different, concurrently-running agent
per the collision protocol (docs/AGENT-OPERATING-RULES.md). Helpers below
(`_build_app`, `_register_owner`, `_create_invite`, `_accept_invite`) are
intentionally duplicated from test_routes_workspaces_invites.py rather than
imported from it, for the same reason test_rls_six_tables_isolation_man109.py
gives for duplicating its own probe-role fixture: importing from a file
someone else is actively editing would couple this file's correctness to
edits happening there right now.

Real Postgres required for every test here (skipped otherwise): the whole
point is proving real `project_memberships` rows get written and read back
through `projects_repository`/`routes_fleet._visible_project_ids`, and that
module is Postgres-only (no SQLite fallback, "control-plane concept from the
Postgres-first era" -- see migrations/add_project_memberships.sql). Run with
DATABASE_URL pointed at a local/dev Postgres, e.g.:

    DATABASE_URL=postgresql://postgres:...@localhost:5432/empyralis \
        python3 -m pytest server_modules/tests/test_workspace_invite_project_access_man70.py -q

This suite does not isolate DATABASE_URL (MAN-139) -- every test below
generates its own uuid-suffixed tenant_id/workspace_id/email and cleans up
its own rows in `async_teardown`-style try/finally blocks, exactly like
ServiceLayerRegressionTests in test_rls_six_tables_isolation_man109.py, so
concurrent runs (this suite, other agents' local Postgres use) don't
collide or leave garbage behind.
"""

from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, Optional, Tuple

import httpx
import pytest
from fastapi import FastAPI

from server_modules import auth
from server_modules import control_plane_repository
from server_modules import projects_repository
from server_modules import routes_fleet
from server_modules import routes_workspaces

_NO_PG_REASON = "No Postgres reachable (DATABASE_URL unset — export it to run this suite)"


def _database_url_available() -> bool:
    return bool(os.getenv("DATABASE_URL", "").strip())


@pytest.fixture(autouse=True)
def _database_url_from_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set DATABASE_URL for THIS TEST'S execution only -- never in the shell
    before pytest starts, and never at module-import/collection time.

    Why it can't just be exported in the shell like the rest of this
    codebase's DB-backed tests: server_modules/acp_manager.py's module-level
    DEFAULT_ACP_MANAGER singleton eagerly reloads persisted run state from
    Postgres AT IMPORT TIME whenever DATABASE_URL is already present in the
    process environment when routes_workspaces (-> ... -> shared ->
    acp_manager) is first imported. This suite's shared local Postgres has
    real in-flight run state from other concurrently-running agents, and the
    Rust kernel binary that would validate reloading it isn't built in this
    environment, so an already-exported DATABASE_URL turns a plain `import
    routes_workspaces` into a hard collection-time crash having nothing to
    do with this file's own tests.

    Setting it here instead -- inside an autouse fixture, which pytest runs
    per-test, strictly after every module's import-time code has already
    executed once during collection -- sidesteps that: DATABASE_URL simply
    isn't in the process environment yet when acp_manager.py's singleton is
    constructed, and IS present by the time any test body (or
    projects_repository/control_plane_repository call, which read it fresh
    per-call via db.configured_database_url()) runs.

    Reads the value via dotenv_values() (read-only -- never mutates the
    .env file, never calls load_dotenv() which would set unrelated vars
    too) using find_dotenv()'s own upward search from CWD, exactly the
    "a .env at the repo root is found by dotenv's upward search from any
    worktree" mechanism this suite is expected to run under. A DATABASE_URL
    already present in the process (e.g. a developer who genuinely did
    export it and therefore already ate the collection-time cost above) is
    left as-is.
    """
    if os.getenv("DATABASE_URL", "").strip():
        return
    try:
        from dotenv import dotenv_values, find_dotenv
    except Exception:
        return
    env_path = find_dotenv(usecwd=True)
    if not env_path:
        return
    database_url = str(dotenv_values(env_path).get("DATABASE_URL") or "").strip()
    if database_url:
        monkeypatch.setenv("DATABASE_URL", database_url)


@asynccontextmanager
async def _pg_scope() -> AsyncIterator[Tuple[Any, str, list]]:
    """Yields (pool, suffix, cleanup_workspace_ids). Every test appends the
    real workspace_id(s) it creates (via _register_owner, which bootstraps a
    genuine (workspace_id -> tenant_id) binding through the real
    register_user path) to `cleanup_workspace_ids`; teardown deletes every
    row this suite could have written in project_memberships/projects/
    workspace_member_invites filtered by THOSE workspace_ids, even if the
    test raises partway through.

    Deliberately workspace_id-scoped, not tenant_id-scoped: a bootstrap
    workspace's tenant_id is a real, auto-generated binding from
    register_user (NOT equal to workspace_id, and not something this file
    controls or can predict up front), whereas workspace_id itself is
    exactly the fresh uuid-based value _register_owner hands back --
    reliable to collect and filter on regardless of what tenant_id turned
    out to be.

    Does NOT delete the `users`/`workspaces`/`workspace_registry` rows
    register_user itself creates -- those are small, uuid-random-email,
    dev-only rows, no different in kind from what every OTHER test in this
    suite that calls register_user already leaves behind whether or not
    DATABASE_URL happens to be configured (this suite does not isolate
    DATABASE_URL -- MAN-139). Only project_memberships/projects/
    workspace_member_invites are this feature's own tables and are cleaned
    up so repeated runs don't accumulate rows that could shadow future
    assertions.
    """
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        pytest.skip(_NO_PG_REASON)
        return
    suffix = uuid.uuid4().hex[:12]
    cleanup_workspace_ids: list = []
    try:
        yield pool, suffix, cleanup_workspace_ids
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


def _register_owner(*, email: Optional[str] = None, password: str = "Own3r-Secret-Pass!") -> dict:
    """Real registration (SQLite-backed local auth DB, isolated per test by
    conftest.py's autouse _isolate_empyralis_state fixture) -- the owner's
    own bootstrap workspace becomes the target workspace_id, same as a real
    first user. Duplicated from test_routes_workspaces_invites.py's helper
    of the same name -- see module docstring."""
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
    # The AUTHORITATIVE tenant_id for this workspace -- register_user's own
    # bootstrap binds a real (workspace_id -> tenant_id) row (workspaces /
    # workspace_registry table), and that tenant_id is generally NOT equal
    # to workspace_id itself. _control_plane_tenant_id (routes_workspaces.py)
    # resolves to this same value via auth.workspace_tenant_id ->
    # tenant_id_for_workspace when a real binding exists (as it does here,
    # since this is a real registered user, not a synthetic current_user
    # dict) -- tests that create Postgres rows directly (projects, invites)
    # must use THIS tenant_id, not workspace_id, or they will not match what
    # the real route/repository resolves.
    entry = workspace_access.get(workspace_id) or {}
    tenant_id = str(entry.get("tenant_id") or "").strip() or workspace_id
    return {
        "user_id": user_id,
        "email": clean_email,
        "workspace_id": workspace_id,
        "tenant_id": tenant_id,
        "current_user": current_user,
    }


def _member_current_user(*, user_id: str, email: str, workspace_id: str, role: str = "member") -> dict:
    """A non-owner current_user dict shaped exactly like
    auth._effective_workspace_access would build one for a real member --
    used to drive routes_fleet._visible_project_ids directly, the same
    function every non-owner fleet list endpoint filters through."""
    return {
        "user_id": user_id,
        "auth_type": "bearer",
        "email": email,
        "workspace_ids": [workspace_id],
        "workspace_access": {workspace_id: {"workspace_id": workspace_id, "role": role, "tenant_id": workspace_id}},
        "role": role,
        "is_admin": False,
        "auth_admin": False,
    }


async def _create_invite(
    app: FastAPI,
    caller_current_user: dict,
    workspace_id: str,
    *,
    email: str,
    role: str = "member",
    project_id: Optional[str] = None,
) -> httpx.Response:
    app.dependency_overrides[routes_workspaces.get_current_user] = lambda: caller_current_user
    transport = httpx.ASGITransport(app=app)
    payload: Dict[str, Any] = {"email": email, "role": role}
    if project_id is not None:
        payload["project_id"] = project_id
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(f"/workspaces/{workspace_id}/invites", json=payload)


async def _accept_invite(app: FastAPI, invitee_current_user: dict, token: str) -> httpx.Response:
    app.dependency_overrides[routes_workspaces.get_current_user] = lambda: invitee_current_user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post("/workspaces/invites/accept", json={"token": token})


def _register_invitee(*, email: str, password: str = "Inv1tee-Secret-Pass!") -> dict:
    """A real, distinct second user, NOT attached to any workspace -- used
    for both acceptance-path tests so each drives its own real user through
    auth.register_user's actual persistence path."""
    auth.register_user(email, password)
    user = auth._find_user_by_email(email)
    assert isinstance(user, dict) and user.get("id"), f"registration did not persist a user row for {email}"
    return {"user_id": str(user["id"]).strip(), "email": email}


# ── 1. Creation-time validation: project_id must belong to the inviting
#      workspace, or the invite is rejected outright (first line of
#      defense; grant_invite_project_access is the second, at accept time).
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_create_invite_rejects_project_id_from_a_different_workspace():
    if not _database_url_available():
        pytest.skip(_NO_PG_REASON)
    async with _pg_scope() as (pool, suffix, cleanup_workspace_ids):
        app = _build_app()
        owner_a = _register_owner()
        owner_b = _register_owner()
        cleanup_workspace_ids += [owner_a["workspace_id"], owner_b["workspace_id"]]

        # A real project that belongs to owner_b's workspace/tenant, not
        # owner_a's -- created directly through projects_repository so this
        # test does not depend on a project-creation ROUTE existing.
        project_b = await projects_repository.create_project(
            tenant_id=owner_b["tenant_id"], workspace_id=owner_b["workspace_id"], name="Beta Project"
        )

        response = await _create_invite(
            app, owner_a["current_user"], owner_a["workspace_id"],
            email="cross-tenant-invitee@example.com", role="member",
            project_id=project_b["id"],
        )

        assert response.status_code == 400
        assert "Project not found" in response.json()["detail"]


@pytest.mark.anyio
async def test_create_invite_accepts_project_id_from_the_same_workspace():
    if not _database_url_available():
        pytest.skip(_NO_PG_REASON)
    async with _pg_scope() as (pool, suffix, cleanup_workspace_ids):
        app = _build_app()
        owner = _register_owner()
        cleanup_workspace_ids.append(owner["workspace_id"])
        # Create the project under the SAME tenant_id the route itself will
        # resolve for this workspace (owner["tenant_id"], the real binding
        # register_user's bootstrap created) -- not an assumption, the
        # authoritative value _register_owner read back from
        # workspace_access[workspace_id]["tenant_id"].
        project = await projects_repository.create_project(
            tenant_id=owner["tenant_id"], workspace_id=owner["workspace_id"], name="Alpha Project"
        )

        response = await _create_invite(
            app, owner["current_user"], owner["workspace_id"],
            email="same-workspace-invitee@example.com", role="member",
            project_id=project["id"],
        )

        assert response.status_code == 200
        assert response.json()["invite"]["project_id"] == project["id"]


# ── 2. Token-route acceptance grants project access, proven end to end
#      through routes_fleet._visible_project_ids (not just a raw row check).
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_invite_with_project_accepted_via_token_route_grants_visible_project_access():
    if not _database_url_available():
        pytest.skip(_NO_PG_REASON)
    async with _pg_scope() as (pool, suffix, cleanup_workspace_ids):
        app = _build_app()
        owner = _register_owner()
        cleanup_workspace_ids.append(owner["workspace_id"])
        tenant_id = owner["tenant_id"]
        project = await projects_repository.create_project(
            tenant_id=tenant_id, workspace_id=owner["workspace_id"], name="Token Route Project"
        )
        invitee_email = f"token-route-invitee-{suffix}@example.com"
        invitee = _register_invitee(email=invitee_email)

        create_response = await _create_invite(
            app, owner["current_user"], owner["workspace_id"],
            email=invitee_email, role="member", project_id=project["id"],
        )
        assert create_response.status_code == 200
        token = create_response.json()["token"]

        invitee_current_user = _member_current_user(
            user_id=invitee["user_id"], email=invitee_email, workspace_id=owner["workspace_id"],
        )
        accept_response = await _accept_invite(app, invitee_current_user, token)
        assert accept_response.status_code == 200
        assert accept_response.json()["status"] == "accepted"

        # Raw row proof.
        assert await projects_repository.is_project_member(
            tenant_id=tenant_id, workspace_id=owner["workspace_id"],
            project_id=project["id"], user_id=invitee["user_id"],
        )

        # THE bug, closed: a non-owner who accepted this invite can now
        # actually see the project through the exact function every
        # non-owner fleet list endpoint filters through.
        visible = await routes_fleet._visible_project_ids(
            invitee_current_user, owner["workspace_id"], tenant_id,
        )
        assert visible is not None, "non-owner current_user should be filtered, not bypassed"
        assert project["id"] in visible


# ── 3. Login auto-accept grants project access too -- the path most likely
#      to be missed if only the token route were patched.
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_invite_with_project_accepted_via_login_auto_accept_grants_visible_project_access():
    if not _database_url_available():
        pytest.skip(_NO_PG_REASON)
    async with _pg_scope() as (pool, suffix, cleanup_workspace_ids):
        owner = _register_owner()
        cleanup_workspace_ids.append(owner["workspace_id"])
        tenant_id = owner["tenant_id"]
        project = await projects_repository.create_project(
            tenant_id=tenant_id, workspace_id=owner["workspace_id"], name="Auto Accept Project"
        )
        invitee_email = f"auto-accept-invitee-{suffix}@example.com"

        # Invitee registers FIRST, with no pending invite yet -- register_user
        # itself calls accept_workspace_invites_for_user internally (that is
        # exactly how the auto-accept-at-login path normally fires, on
        # registration too, not just login), and if the invite already
        # existed at registration time it would be consumed right there,
        # leaving nothing for the explicit call below to do and making this
        # test pass without actually isolating the function under test. This
        # ordering -- register first, invite second -- mirrors
        # test_accept_invite_succeeds_when_invitee_signup_already_auto_accepted_it's
        # own comment on why order matters, just inverted: THAT test wants
        # the invite consumed at registration; THIS test wants it NOT
        # consumed at registration, so a later call cleanly simulates "the
        # invite arrived after signup, and the invitee eventually logs back
        # in" -- the accept_workspace_invites_for_user call auth.login_user
        # makes on every sign-in.
        invitee = _register_invitee(email=invitee_email)

        # Invite created AFTER the invitee already has an account --
        # create_workspace_invite directly (repository level), not the
        # route, to isolate this test to auth.accept_workspace_invites_for_user
        # alone.
        invite = await control_plane_repository.create_workspace_invite(
            workspace_id=owner["workspace_id"],
            tenant_id=tenant_id,
            email=invitee_email,
            role="member",
            invited_by_user_id=owner["user_id"],
            invited_by_role="owner",
            project_id=project["id"],
        )
        assert invite["project_id"] == project["id"]

        # THE call under test: auth.accept_workspace_invites_for_user is what
        # auth.login_user/register_user invoke on every sign-in -- calling it
        # directly here (simulating the invitee logging back in after the
        # invite arrived) isolates the auto-accept-at-login path from the
        # /join/{token} route entirely, so this test cannot pass by accident
        # via the other acceptance path.
        accepted = auth.accept_workspace_invites_for_user(invitee["user_id"], invitee_email)
        assert any(item["workspace_id"] == owner["workspace_id"] for item in accepted)

        # Real DB proof: workspace membership landed.
        real_memberships = auth._list_workspace_memberships(invitee["user_id"])
        assert any(
            str(item.get("workspace_id")) == owner["workspace_id"] and item.get("role") == "member"
            for item in real_memberships
        )

        # Raw row proof: project membership landed too -- this is the row
        # that did NOT exist before this fix, for this exact path.
        assert await projects_repository.is_project_member(
            tenant_id=tenant_id, workspace_id=owner["workspace_id"],
            project_id=project["id"], user_id=invitee["user_id"],
        )

        # End-to-end proof through the real visibility filter.
        invitee_current_user = _member_current_user(
            user_id=invitee["user_id"], email=invitee_email, workspace_id=owner["workspace_id"],
        )
        visible = await routes_fleet._visible_project_ids(
            invitee_current_user, owner["workspace_id"], tenant_id,
        )
        assert visible is not None
        assert project["id"] in visible


# ── 4. No project_id on the invite: SUPERSEDED by MAN-335.
#
#      This test used to assert that a project-less invite grants no project
#      access at all, calling it "the deliberate default... not a gap" —
#      that was wrong. It was the exact MAN-335 bug: a real, invited,
#      accepted teammate saw ZERO projects, because MembersSection.tsx's
#      own invite form (the only UI surface for this exact request shape)
#      never sends a project_id, and grant_invite_project_access no-ops on
#      empty metadata. See
#      test_man335_workspace_invite_default_project_access.py for the full
#      fix and its test surface (forward default + the pre-fix-member
#      self-heal + the revocation-safety property) — this one test is kept
#      here, corrected, because section 4's own no-project_id request shape
#      belongs with the rest of this file's creation-time coverage.
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_invite_with_no_project_id_defaults_to_the_workspace_default_project():
    if not _database_url_available():
        pytest.skip(_NO_PG_REASON)
    async with _pg_scope() as (pool, suffix, cleanup_workspace_ids):
        app = _build_app()
        owner = _register_owner()
        cleanup_workspace_ids.append(owner["workspace_id"])
        tenant_id = owner["tenant_id"]
        default_project = await projects_repository.ensure_default_project(
            tenant_id=tenant_id, workspace_id=owner["workspace_id"],
        )
        invitee_email = f"no-project-invitee-{suffix}@example.com"
        invitee = _register_invitee(email=invitee_email)

        create_response = await _create_invite(
            app, owner["current_user"], owner["workspace_id"], email=invitee_email, role="member",
        )
        assert create_response.status_code == 200
        assert create_response.json()["invite"]["project_id"] == default_project["id"]
        token = create_response.json()["token"]

        invitee_current_user = _member_current_user(
            user_id=invitee["user_id"], email=invitee_email, workspace_id=owner["workspace_id"],
        )
        accept_response = await _accept_invite(app, invitee_current_user, token)
        assert accept_response.status_code == 200

        member_project_ids = await projects_repository.list_member_project_ids(
            tenant_id=tenant_id, workspace_id=owner["workspace_id"], user_id=invitee["user_id"],
        )
        assert member_project_ids == [default_project["id"]]

        visible = await routes_fleet._visible_project_ids(
            invitee_current_user, owner["workspace_id"], tenant_id,
        )
        assert visible == {default_project["id"]}


# ── 5. Idempotency: accepting/granting twice must not error or duplicate
#      rows. add_project_member's own ON CONFLICT (project_id, user_id) DO
#      UPDATE already guarantees this at the SQL level -- this test proves
#      it holds across the exact scenario the two-acceptance-paths design
#      creates: BOTH paths' grant call firing for the same invite/user.
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_double_grant_across_both_acceptance_paths_is_idempotent():
    if not _database_url_available():
        pytest.skip(_NO_PG_REASON)
    async with _pg_scope() as (pool, suffix, cleanup_workspace_ids):
        owner = _register_owner()
        cleanup_workspace_ids.append(owner["workspace_id"])
        tenant_id = owner["tenant_id"]
        project = await projects_repository.create_project(
            tenant_id=tenant_id, workspace_id=owner["workspace_id"], name="Idempotency Project"
        )
        invitee_email = f"idempotent-invitee-{suffix}@example.com"
        invitee = _register_invitee(email=invitee_email)
        metadata = {"project_id": project["id"]}

        # Simulates accept_workspace_invite_route's grant call.
        first = await projects_repository.grant_invite_project_access(
            tenant_id=tenant_id, workspace_id=owner["workspace_id"],
            user_id=invitee["user_id"], metadata=metadata, added_by=owner["user_id"],
        )
        assert first is not None
        assert first["role"] == "member"

        # Simulates auth.accept_workspace_invites_for_user's grant call for
        # the SAME invite -- the exact race the two-path design creates.
        second = await projects_repository.grant_invite_project_access(
            tenant_id=tenant_id, workspace_id=owner["workspace_id"],
            user_id=invitee["user_id"], metadata=metadata, added_by=owner["user_id"],
        )
        assert second is not None
        assert second["id"] == first["id"], "a second grant should update the same row, not insert a new one"

        rows = await pool.fetch(
            "SELECT id FROM project_memberships WHERE tenant_id = $1 AND workspace_id = $2 AND project_id = $3 AND user_id = $4",
            tenant_id, owner["workspace_id"], project["id"], invitee["user_id"],
        )
        assert len(rows) == 1, "double grant duplicated the project_memberships row"


# ── 6. Cross-tenant safety: even if a project_id somehow reaches the grant
#      step referencing a project in a DIFFERENT tenant/workspace than the
#      invite's own (bypassing create_workspace_invite_route's own check --
#      defense in depth for exactly that), it must not grant membership.
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_cross_tenant_project_id_never_grants_membership():
    if not _database_url_available():
        pytest.skip(_NO_PG_REASON)
    async with _pg_scope() as (pool, suffix, cleanup_workspace_ids):
        owner_a = _register_owner()
        owner_b = _register_owner()
        cleanup_workspace_ids += [owner_a["workspace_id"], owner_b["workspace_id"]]
        tenant_a = owner_a["tenant_id"]
        tenant_b = owner_b["tenant_id"]

        project_b = await projects_repository.create_project(
            tenant_id=tenant_b, workspace_id=owner_b["workspace_id"], name="Tenant B Project"
        )

        attacker_email = f"cross-tenant-attacker-{suffix}@example.com"
        attacker = _register_invitee(email=attacker_email)

        # metadata crafted (or stale) as if this were an invite in tenant_a's
        # workspace, but pointing at a project that actually lives in tenant_b.
        result = await projects_repository.grant_invite_project_access(
            tenant_id=tenant_a, workspace_id=owner_a["workspace_id"],
            user_id=attacker["user_id"], metadata={"project_id": project_b["id"]},
            added_by=owner_a["user_id"],
        )

        assert result is None, "grant_invite_project_access must not resolve a cross-tenant project_id"

        # No row in EITHER tenant's scope.
        assert not await projects_repository.is_project_member(
            tenant_id=tenant_a, workspace_id=owner_a["workspace_id"],
            project_id=project_b["id"], user_id=attacker["user_id"],
        )
        assert not await projects_repository.is_project_member(
            tenant_id=tenant_b, workspace_id=owner_b["workspace_id"],
            project_id=project_b["id"], user_id=attacker["user_id"],
        )

        visible = await routes_fleet._visible_project_ids(
            _member_current_user(
                user_id=attacker["user_id"], email=attacker_email, workspace_id=owner_a["workspace_id"],
            ),
            owner_a["workspace_id"], tenant_a,
        )
        assert visible == set()
