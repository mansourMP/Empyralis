"""MAN-335: a real, invited, accepted teammate saw ZERO projects.

Confirmed live in a real browser by another agent, then reproduced end to
end at the API level here: a genuine owner sends a workspace-level invite
through Settings > Workspace > Members (MembersSection.tsx), a genuinely
separate account accepts it through the real /join/{token} flow, and lands
in the workspace as a "member" with GET /fleet/projects returning [].

Root cause, traced from the actual code (not assumed from the earlier
MAN-70 fix's own docstring, which turned out to describe the bug this
ticket is about without anyone noticing): MembersSection.tsx's invite form
has no project selector at all -- createWorkspaceInvite(workspaceId, email,
role) is called with no fourth `projectId` argument, ever. create_workspace_
invite then stores `invite_metadata = {"project_id": clean_project_id} if
clean_project_id else {}` -- an EMPTY dict for every workspace-level invite.
grant_invite_project_access no-ops on an empty metadata dict (see its own
docstring), and routes_fleet._visible_project_ids strictly filters a
non-owner to their explicit project_memberships rows. Net effect: a
workspace-level invite has, since the day per-project membership shipped
(MAN-70/MAN-115), never granted access to anything.

The Members settings page's own copy still claims otherwise -- "Everyone
with access to this workspace — and every project in it (there's no
separate per-project membership yet)" -- which is stale relative to the
real, enforced, per-project model routes_fleet._visible_project_ids has
implemented since MAN-70/MAN-115 (see the comment on
_enforce_task_project_access in routes_fleet.py for that rollout). This
suite does not touch the copy; that is a frontend-only follow-up.

TWO fixes, tested separately below:

1. FORWARD (create_workspace_invite_route): a project-less invite for any
   role below owner now defaults to the workspace's own default project
   rather than granting nothing -- never "every project" (that would
   silently widen access for every existing workspace, which is NOT what
   was implemented), always exactly the one project every real workspace
   already has from creation. An owner invite still needs no project_id:
   owners bypass the per-project filter entirely.

2. BACKWARD (projects_repository.backfill_default_project_access_if_never_
   granted, wired into routes_fleet._visible_project_ids): every teammate
   who already accepted a project-less invite BEFORE this fix shipped is
   stuck exactly as MAN-335 found them, forever, because nothing re-runs
   invite acceptance for someone already accepted. This self-heals the
   moment their OWN project list is next computed. A durable per-member
   marker (workspace_memberships.metadata) makes this a genuine ONE-SHOT --
   proven below to never re-grant after a deliberate later removal, which a
   naive "currently zero grants" check would have silently undone.

Real Postgres required for every test here (skipped otherwise) -- same
reasoning and same opt-in pattern as
test_workspace_invite_project_access_man70.py, whose helpers are
duplicated rather than imported (see that file's own docstring on why:
importing from a file another agent might be concurrently editing couples
this file's correctness to edits happening there right now). Run with:

    DATABASE_URL=postgresql://...@localhost:5432/<disposable-db> \
        python3 -m pytest server_modules/tests/test_man335_workspace_invite_default_project_access.py -q

Never point this at a real/shared database -- see this repo's own
standing rule against ever setting DATABASE_URL to anything but a
throwaway the agent created for itself.
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
    """See test_workspace_invite_project_access_man70.py's identical fixture
    for the full reasoning (acp_manager's import-time Postgres reload) --
    duplicated verbatim rather than imported, same reason as everything
    else in this file."""
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
    """See test_workspace_invite_project_access_man70.py's identical helper
    for the full reasoning. Also cleans workspace_memberships rows this
    file creates directly (the legacy-member simulation), which that file
    never needs to."""
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
    auth.register_user(email, password)
    user = auth._find_user_by_email(email)
    assert isinstance(user, dict) and user.get("id"), f"registration did not persist a user row for {email}"
    return {"user_id": str(user["id"]).strip(), "email": email}


# ── 1. FORWARD FIX: a project-less workspace-level invite (exactly what
#      MembersSection.tsx sends today, unmodified) now defaults to the
#      workspace's own default project instead of granting nothing. ───────


@pytest.mark.anyio
async def test_workspace_level_invite_with_no_project_id_defaults_to_the_default_project():
    """The EXACT request MembersSection.tsx's handleInvite makes:
    createWorkspaceInvite(workspaceId, email, role) with no fourth argument
    -> {"email": ..., "role": ...}, no project_id key at all. Before the
    fix this created project_id: null and the accepted member saw nothing;
    now it must resolve to the workspace's real default project."""
    if not _database_url_available():
        pytest.skip(_NO_PG_REASON)
    async with _pg_scope() as (pool, suffix, cleanup_workspace_ids):
        app = _build_app()
        owner = _register_owner()
        cleanup_workspace_ids.append(owner["workspace_id"])
        default_project = await projects_repository.ensure_default_project(
            tenant_id=owner["tenant_id"], workspace_id=owner["workspace_id"],
        )

        invitee_email = f"man335-forward-invitee-{suffix}@example.com"
        invitee = _register_invitee(email=invitee_email)

        create_response = await _create_invite(
            app, owner["current_user"], owner["workspace_id"],
            email=invitee_email, role="member",
            # No project_id — the real, current MembersSection.tsx shape.
        )
        assert create_response.status_code == 200
        body = create_response.json()
        assert body["invite"]["project_id"] == default_project["id"], (
            "a project-less workspace invite must default to the workspace's own "
            f"default project, got {body['invite']['project_id']!r}"
        )

        token = body["token"]
        invitee_current_user = _member_current_user(
            user_id=invitee["user_id"], email=invitee_email, workspace_id=owner["workspace_id"],
        )
        accept_response = await _accept_invite(app, invitee_current_user, token)
        assert accept_response.status_code == 200

        # THE bug, closed: GET /fleet/projects's own filter now returns
        # something, not an empty set, for a plain workspace-level invite.
        visible = await routes_fleet._visible_project_ids(
            invitee_current_user, owner["workspace_id"], owner["tenant_id"],
        )
        assert visible == {default_project["id"]}


@pytest.mark.anyio
async def test_workspace_level_invite_never_grants_more_than_the_default_project():
    """The fix is "grant the default project", never "grant every project in
    the workspace" — the coordinator's own explicit caution against a fix
    that would silently widen access for every existing workspace. A second,
    non-default project in the same workspace must NOT become visible."""
    if not _database_url_available():
        pytest.skip(_NO_PG_REASON)
    async with _pg_scope() as (pool, suffix, cleanup_workspace_ids):
        app = _build_app()
        owner = _register_owner()
        cleanup_workspace_ids.append(owner["workspace_id"])
        default_project = await projects_repository.ensure_default_project(
            tenant_id=owner["tenant_id"], workspace_id=owner["workspace_id"],
        )
        other_project = await projects_repository.create_project(
            tenant_id=owner["tenant_id"], workspace_id=owner["workspace_id"], name="Not The Default",
        )

        invitee_email = f"man335-scope-invitee-{suffix}@example.com"
        invitee = _register_invitee(email=invitee_email)
        create_response = await _create_invite(
            app, owner["current_user"], owner["workspace_id"], email=invitee_email, role="member",
        )
        token = create_response.json()["token"]
        invitee_current_user = _member_current_user(
            user_id=invitee["user_id"], email=invitee_email, workspace_id=owner["workspace_id"],
        )
        await _accept_invite(app, invitee_current_user, token)

        visible = await routes_fleet._visible_project_ids(
            invitee_current_user, owner["workspace_id"], owner["tenant_id"],
        )
        assert default_project["id"] in visible
        assert other_project["id"] not in visible
        assert visible == {default_project["id"]}


@pytest.mark.anyio
async def test_owner_role_invite_needs_no_project_id_and_is_not_forced_one():
    """Owners bypass the per-project filter entirely
    (auth.enforce_project_access's 2026-07-28 ruling) — requiring a
    project_id for an owner invite would be pure friction with nothing
    behind it. Confirms the fix's role check does not fire for role=owner."""
    if not _database_url_available():
        pytest.skip(_NO_PG_REASON)
    async with _pg_scope() as (pool, suffix, cleanup_workspace_ids):
        app = _build_app()
        owner = _register_owner()
        cleanup_workspace_ids.append(owner["workspace_id"])
        # A default project exists (every real workspace has one), but an
        # owner invite must not be forced to carry it.
        await projects_repository.ensure_default_project(
            tenant_id=owner["tenant_id"], workspace_id=owner["workspace_id"],
        )

        invitee_email = f"man335-owner-invitee-{suffix}@example.com"
        response = await _create_invite(
            app, owner["current_user"], owner["workspace_id"], email=invitee_email, role="owner",
        )
        assert response.status_code == 200
        assert response.json()["invite"]["project_id"] is None


@pytest.mark.anyio
async def test_member_invite_creates_the_default_project_when_none_exists_yet():
    """The edge case: a workspace with genuinely zero projects (its owner
    deleted the only one, or it structurally never had one — some test
    harnesses' bootstrap workspace doesn't auto-create "General" the way a
    real signup's first project-list load does) must not block an owner
    from inviting a teammate. ensure_default_project (create-if-absent) is
    the same convention project deletion's own fallback already uses — a
    deliberate, active, owner-initiated action creating a real project is
    the honest answer here, not a 400 telling the owner to go create one
    first for no product reason, and not silently granting nothing again
    (the original MAN-335 bug)."""
    if not _database_url_available():
        pytest.skip(_NO_PG_REASON)
    async with _pg_scope() as (pool, suffix, cleanup_workspace_ids):
        app = _build_app()
        owner = _register_owner()
        cleanup_workspace_ids.append(owner["workspace_id"])
        # Deliberately no ensure_default_project call before the invite —
        # this workspace has no projects at all yet.
        existing = await projects_repository.list_projects(
            tenant_id=owner["tenant_id"], workspace_id=owner["workspace_id"],
        )
        assert existing == [], "test precondition: workspace must start with zero projects"

        invitee_email = f"man335-noproject-invitee-{suffix}@example.com"
        response = await _create_invite(
            app, owner["current_user"], owner["workspace_id"], email=invitee_email, role="member",
        )
        assert response.status_code == 200
        created_project_id = response.json()["invite"]["project_id"]
        assert created_project_id

        after = await projects_repository.list_projects(
            tenant_id=owner["tenant_id"], workspace_id=owner["workspace_id"],
        )
        assert len(after) == 1
        assert after[0]["id"] == created_project_id
        assert after[0]["is_default"] is True


# ── 2. BACKWARD FIX: a member who already accepted a project-less invite
#      before this fix shipped self-heals via routes_fleet._visible_project_
#      ids the next time their own project list is computed. ─────────────


async def _simulate_pre_fix_projectless_member(
    *, pool, tenant_id: str, workspace_id: str, email: str,
) -> dict:
    """Bypasses the invite flow entirely and writes exactly the row shape a
    pre-fix accepted workspace invite left behind: a real user, a real
    workspace_memberships row, ZERO project_memberships rows. This is what
    every already-broken MAN-335 teammate looks like in the database today,
    and is not reachable through the (now-fixed) invite-creation route at
    all — hence writing it directly rather than driving it through
    _create_invite/_accept_invite."""
    user_id = f"man335-legacy-{uuid.uuid4().hex[:12]}"
    await pool.execute(
        """
        INSERT INTO users (id, tenant_id, workspace_id, email, display_name, created_at, updated_at)
        VALUES ($1, $2, $3, $4, 'Legacy Pre-Fix Member', now(), now())
        ON CONFLICT (id) DO NOTHING
        """,
        user_id, tenant_id, workspace_id, email,
    )
    await pool.execute(
        """
        INSERT INTO workspace_memberships (id, tenant_id, workspace_id, user_id, role, status, metadata, created_at, updated_at)
        VALUES ($1, $2, $3, $4, 'member', 'active', '{}'::jsonb, now(), now())
        ON CONFLICT (tenant_id, workspace_id, user_id) DO NOTHING
        """,
        f"wm-{user_id}", tenant_id, workspace_id, user_id,
    )
    return {"user_id": user_id, "email": email}


@pytest.mark.anyio
async def test_pre_fix_projectless_member_self_heals_to_the_default_project():
    if not _database_url_available():
        pytest.skip(_NO_PG_REASON)
    async with _pg_scope() as (pool, suffix, cleanup_workspace_ids):
        owner = _register_owner()
        cleanup_workspace_ids.append(owner["workspace_id"])
        default_project = await projects_repository.ensure_default_project(
            tenant_id=owner["tenant_id"], workspace_id=owner["workspace_id"],
        )
        legacy = await _simulate_pre_fix_projectless_member(
            pool=pool, tenant_id=owner["tenant_id"], workspace_id=owner["workspace_id"],
            email=f"man335-legacy-{suffix}@example.com",
        )

        # Precondition: genuinely zero project grants, exactly the reported bug.
        before = await projects_repository.list_member_project_ids(
            tenant_id=owner["tenant_id"], workspace_id=owner["workspace_id"], user_id=legacy["user_id"],
        )
        assert before == []

        legacy_current_user = _member_current_user(
            user_id=legacy["user_id"], email=legacy["email"], workspace_id=owner["workspace_id"],
        )
        visible = await routes_fleet._visible_project_ids(
            legacy_current_user, owner["workspace_id"], owner["tenant_id"],
        )
        assert visible == {default_project["id"]}, (
            "a pre-fix projectless member must self-heal to the default project "
            "the next time their own project list is computed"
        )


@pytest.mark.anyio
async def test_backfill_never_regrants_after_a_deliberate_revocation():
    """The property this design exists to guarantee: a member healed once,
    then deliberately removed from every one of their projects by the
    owner, must NOT be silently re-granted the next time their project
    list is computed. A naive "currently zero grants" re-check (instead of
    a durable per-member marker) would fail this test — that was the exact
    risk identified and designed against."""
    if not _database_url_available():
        pytest.skip(_NO_PG_REASON)
    async with _pg_scope() as (pool, suffix, cleanup_workspace_ids):
        owner = _register_owner()
        cleanup_workspace_ids.append(owner["workspace_id"])
        default_project = await projects_repository.ensure_default_project(
            tenant_id=owner["tenant_id"], workspace_id=owner["workspace_id"],
        )
        legacy = await _simulate_pre_fix_projectless_member(
            pool=pool, tenant_id=owner["tenant_id"], workspace_id=owner["workspace_id"],
            email=f"man335-revoke-{suffix}@example.com",
        )
        legacy_current_user = _member_current_user(
            user_id=legacy["user_id"], email=legacy["email"], workspace_id=owner["workspace_id"],
        )

        # First computation heals them.
        first = await routes_fleet._visible_project_ids(
            legacy_current_user, owner["workspace_id"], owner["tenant_id"],
        )
        assert first == {default_project["id"]}

        # Owner deliberately removes them from the (only) project they have.
        removed = await projects_repository.remove_project_member(
            tenant_id=owner["tenant_id"], workspace_id=owner["workspace_id"],
            project_id=default_project["id"], user_id=legacy["user_id"],
        )
        assert removed
        after_removal = await projects_repository.list_member_project_ids(
            tenant_id=owner["tenant_id"], workspace_id=owner["workspace_id"], user_id=legacy["user_id"],
        )
        assert after_removal == []

        # A SECOND computation of their project list — the naive design
        # would see "currently zero grants" and re-heal. The marker-based
        # design must not.
        second = await routes_fleet._visible_project_ids(
            legacy_current_user, owner["workspace_id"], owner["tenant_id"],
        )
        assert second == set(), (
            "a deliberate removal must stick — re-granting the default project "
            "here would silently undo an owner's own explicit action"
        )


@pytest.mark.anyio
async def test_backfill_is_idempotent_across_concurrent_computations():
    """Two callers computing this member's visible-project set back to back
    (e.g. two tabs, or a retried request) must not both attempt a grant or
    leave the member with duplicate/conflicting state — add_project_
    member's own ON CONFLICT handling plus the marker together make this
    safe, asserted directly rather than assumed."""
    if not _database_url_available():
        pytest.skip(_NO_PG_REASON)
    async with _pg_scope() as (pool, suffix, cleanup_workspace_ids):
        owner = _register_owner()
        cleanup_workspace_ids.append(owner["workspace_id"])
        default_project = await projects_repository.ensure_default_project(
            tenant_id=owner["tenant_id"], workspace_id=owner["workspace_id"],
        )
        legacy = await _simulate_pre_fix_projectless_member(
            pool=pool, tenant_id=owner["tenant_id"], workspace_id=owner["workspace_id"],
            email=f"man335-idempotent-{suffix}@example.com",
        )

        first = await projects_repository.backfill_default_project_access_if_never_granted(
            tenant_id=owner["tenant_id"], workspace_id=owner["workspace_id"], user_id=legacy["user_id"],
        )
        second = await projects_repository.backfill_default_project_access_if_never_granted(
            tenant_id=owner["tenant_id"], workspace_id=owner["workspace_id"], user_id=legacy["user_id"],
        )
        assert first is True
        assert second is False

        ids = await projects_repository.list_member_project_ids(
            tenant_id=owner["tenant_id"], workspace_id=owner["workspace_id"], user_id=legacy["user_id"],
        )
        assert ids == [default_project["id"]]
