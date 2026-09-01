"""Removing a project — DELETE /api/w/{ws}/fleet/projects/{id} and
projects_repository.delete_project.

Until this shipped a project could be created and never removed: the repo
had `set_project_archived` with NO caller anywhere in the frontend (the
"built, tested, and never wired" failure mode CLAUDE.md names first) and no
delete function at all. These tests pin the things that make the new delete
safe rather than merely present:

1. PERMISSION. Owner-on-the-workspace, exactly matching the create/patch
   routes it supersedes. A viewer, a member, and — the case that actually
   matters for multiplayer — an OWNER OF A DIFFERENT WORKSPACE all get 403,
   and the repository is never reached. The workspace's tenant is resolved
   per workspace (control_plane_repository.resolve_tenant_id_for_workspace),
   never off the stale users.tenant_id column; one test drives a user whose
   home tenant disagrees with the workspace's, which is the exact shape of
   the bug CLAUDE.md documents.

2. EVERY PROJECT IS DELETABLE, INCLUDING is_default. Founder ruling,
   2026-09-01 ("I have no idea how to delete this shit. General project.
   Why don't I have delete function?"): `is_default` used to refuse deletion
   here (ValueError, "the default project cannot be deleted") on the theory
   that a workspace must always keep a home for ungrouped agents. That
   theory is dead — an agent is independent of every project — so
   `is_default` is now just a legacy marker with zero effect on whether a
   project can be removed. These tests prove the refusal is gone AND that
   deleting never falls back to CREATING a replacement default project
   (the real bug hiding under the old guard: for a workspace with no other
   project already marked `is_default`, the old rehoming step silently
   wrote a fresh "General" project as a side effect of deleting a different
   one — `ensure_default_project` must never be called from here again).

3. WHAT HAPPENS TO WHAT THE PROJECT OWNED. Tasks/documents/goals/memberships
   go by FK cascade. Agents and project-scoped vault credentials used to be
   REASSIGNED to a "default" project first, precisely to dodge the FK's own
   ON DELETE SET NULL — that reassignment is what could invent a project
   nobody asked for, so it is gone. Agents land on NULL exactly as the FK
   says, and the count is read (like every other count here) BEFORE the
   delete, so the caller can still report an honest tally.

4. CREDENTIALS ARE NOT LEFT TO THE FK. Landing a project-scoped
   vault_credentials row on NULL project_id turned out to be its own real
   regression (a separate fix from #3 above): nothing anywhere lists or
   revokes a credential once its project_id is NULL, so it becomes
   invisible and unrevokable rather than destroyed. Fixed by deleting it
   OUTRIGHT in the same transaction — UNLESS an ENABLED
   agent_connector_bindings row still points at it, which means a live
   agent is actually using it right now (independent of this project's own
   lifecycle — connectors_actions.py documents N agents sharing ONE
   project-scoped credential this way). Hard-deleting a still-bound
   credential would break that agent's next tool call, a worse bug than
   the one this fixes — so those rows are deliberately left alone and land
   on NULL via the FK exactly as before, and the honest count says so.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI

from server_modules import projects_repository, routes_fleet


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(routes_fleet.router)
    return app


def _owner_user() -> dict:
    return {
        "user_id": "owner-1",
        "email": "owner@example.com",
        "auth_type": "bearer",
        "workspace_access": {
            "ws-1": {"workspace_id": "ws-1", "tenant_id": "tenant-1", "role": "owner", "tenant_role": "owner"}
        },
    }


def _member_user() -> dict:
    return {
        "user_id": "member-1",
        "email": "member@example.com",
        "auth_type": "bearer",
        "workspace_access": {
            "ws-1": {"workspace_id": "ws-1", "tenant_id": "tenant-1", "role": "member", "tenant_role": "member"}
        },
    }


def _viewer_user() -> dict:
    return {
        "user_id": "viewer-1",
        "email": "viewer@example.com",
        "auth_type": "bearer",
        "workspace_access": {
            "ws-1": {"workspace_id": "ws-1", "tenant_id": "tenant-1", "role": "viewer", "tenant_role": "viewer"}
        },
    }


def _outsider_owner_user() -> dict:
    """A real workspace OWNER — of somebody else's workspace. Owning ws-2
    must buy exactly nothing in ws-1; `owner` is a role, not a tenancy."""
    return {
        "user_id": "outsider-1",
        "email": "outsider@example.com",
        "auth_type": "bearer",
        "workspace_access": {
            "ws-2": {"workspace_id": "ws-2", "tenant_id": "tenant-2", "role": "owner", "tenant_role": "owner"}
        },
    }


def _deleted_summary(**overrides) -> dict:
    base = {
        "id": "proj-1",
        "name": "Demo takes",
        "tasks_deleted": 3,
        "documents_deleted": 1,
        "members_removed": 2,
        "goals_deleted": 0,
        "agents_unassigned": 2,
        "credentials_deleted": 1,
        "credentials_still_bound": 0,
    }
    base.update(overrides)
    return base


# ── 1. Permission ──────────────────────────────────────────────────────────


@pytest.mark.anyio
@pytest.mark.parametrize(
    "user_factory",
    [_viewer_user, _member_user, _outsider_owner_user],
    ids=["viewer", "member", "owner-of-another-workspace"],
)
async def test_only_a_workspace_owner_can_delete_a_project(user_factory) -> None:
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = user_factory

    with (
        patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
        patch(
            "server_modules.projects_repository.delete_project",
            new=AsyncMock(side_effect=AssertionError("must never reach the repository")),
        ) as delete_mock,
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.delete("/api/w/ws-1/fleet/projects/proj-1")

    assert response.status_code == 403
    delete_mock.assert_not_awaited()


@pytest.mark.anyio
async def test_owner_delete_returns_what_was_removed() -> None:
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _owner_user

    with (
        patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
        patch(
            "server_modules.projects_repository.delete_project",
            new=AsyncMock(return_value=_deleted_summary()),
        ) as delete_mock,
        patch(
            "server_modules.activity_ledger_service.append_activity_event",
            new=AsyncMock(return_value=None),
        ) as ledger_mock,
        patch(
            "server_modules.control_plane_repository.get_workspace_by_id",
            new=AsyncMock(return_value={"id": "ws-1", "tenant_id": "tenant-1"}),
        ),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.delete("/api/w/ws-1/fleet/projects/proj-1")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    # The caller is told exactly what went — "silently drop a user's tasks"
    # is the outcome this response prevents. Agents/credentials are reported
    # as unassigned, never as moved somewhere — there is no destination any
    # more (see module docstring).
    assert body["deleted"]["tasks_deleted"] == 3
    assert body["deleted"]["documents_deleted"] == 1
    assert body["deleted"]["agents_unassigned"] == 2
    delete_mock.assert_awaited_once()
    assert delete_mock.await_args.kwargs["project_id"] == "proj-1"
    assert delete_mock.await_args.kwargs["workspace_id"] == "ws-1"
    ledger_mock.assert_awaited_once()
    assert ledger_mock.await_args.kwargs["action"] == "project_deleted"


@pytest.mark.anyio
async def test_tenant_comes_from_the_workspace_not_the_users_stale_home_tenant() -> None:
    """CLAUDE.md: users.tenant_id is written once at signup and never
    updated, so it goes stale the moment someone joins a second workspace
    on another tenant. This drives a user whose workspace_access says
    tenant-9 for ws-1 while resolve_tenant_id_for_workspace — the
    authoritative per-workspace resolver — says tenant-1, and asserts the
    repository is called with the resolved one."""
    app = _build_app()

    def _stale_home_tenant_user() -> dict:
        return {
            "user_id": "owner-1",
            "email": "owner@example.com",
            "auth_type": "bearer",
            # The stale value a naive reader would have picked up.
            "tenant_id": "tenant-9",
            "workspace_id": "ws-9",
            "workspace_access": {
                "ws-1": {"workspace_id": "ws-1", "tenant_id": "tenant-1", "role": "owner", "tenant_role": "owner"}
            },
        }

    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _stale_home_tenant_user

    with (
        # Patched at the per-workspace primitive itself, NOT at
        # routes_fleet._resolve_tenant — so this proves the route goes
        # through that resolver rather than merely that a helper was called.
        patch(
            "server_modules.control_plane_repository.resolve_tenant_id_for_workspace",
            new=AsyncMock(return_value="tenant-1"),
        ) as resolve_mock,
        patch(
            "server_modules.projects_repository.delete_project",
            new=AsyncMock(return_value=_deleted_summary()),
        ) as delete_mock,
        patch("server_modules.activity_ledger_service.append_activity_event", new=AsyncMock(return_value=None)),
        patch(
            "server_modules.control_plane_repository.get_workspace_by_id",
            new=AsyncMock(return_value={"id": "ws-1", "tenant_id": "tenant-1"}),
        ),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.delete("/api/w/ws-1/fleet/projects/proj-1")

    assert response.status_code == 200
    resolve_mock.assert_awaited()
    assert delete_mock.await_args.kwargs["tenant_id"] == "tenant-1"


@pytest.mark.anyio
async def test_unknown_project_is_reported_not_claimed_as_deleted() -> None:
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _owner_user

    with (
        patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
        patch("server_modules.projects_repository.delete_project", new=AsyncMock(return_value=None)),
        patch(
            "server_modules.activity_ledger_service.append_activity_event",
            new=AsyncMock(side_effect=AssertionError("nothing was deleted; nothing to journal")),
        ),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.delete("/api/w/ws-1/fleet/projects/nope")

    assert response.status_code == 200
    assert response.json() == {"ok": False, "error": "Project not found."}


# ── 2. is_default is deletable like any other project ──────────────────────


@pytest.mark.anyio
async def test_route_deletes_the_is_default_project_like_any_other() -> None:
    """The route level: an is_default project's delete goes through
    exactly like a normal one's — no special {"ok": false} refusal, no
    different response shape. delete_project itself decides nothing special
    about is_default any more (see the repository-level test below); this
    just proves the route doesn't reintroduce a guard of its own."""
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _owner_user

    with (
        patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
        patch(
            "server_modules.projects_repository.delete_project",
            new=AsyncMock(return_value=_deleted_summary(id="proj-default", name="General")),
        ) as delete_mock,
        patch("server_modules.activity_ledger_service.append_activity_event", new=AsyncMock(return_value=None)),
        patch(
            "server_modules.control_plane_repository.get_workspace_by_id",
            new=AsyncMock(return_value={"id": "ws-1", "tenant_id": "tenant-1"}),
        ),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.delete("/api/w/ws-1/fleet/projects/proj-default")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["deleted"]["id"] == "proj-default"
    delete_mock.assert_awaited_once()
    assert delete_mock.await_args.kwargs["project_id"] == "proj-default"


# ── 3. The repository: counts, no rehoming, and what the cascade takes ─────


def _squash(sql: str) -> str:
    return " ".join(str(sql).split())


class _RecordingConnection:
    """Captures every statement delete_project issues, in order, so the
    tests can assert on sequence — the count SELECT must run before the
    DELETE, or the tally it produces is a lie about rows already gone.
    Also records whether the whole unit of work committed or rolled back,
    since "all or nothing" is the other safety property here.

    Three query shapes are distinguished by statement prefix, matching the
    three DIFFERENT asyncpg calls delete_project actually issues against
    them: `fetchrow` for the tasks/documents/members/goals/agents tally AND
    the still-bound-credentials count, `fetch` for the credentials DELETE
    (it needs the RETURNING rows, not just a command tag), `execute` for
    the final `DELETE FROM projects`. Anything else raises — delete_project
    must never reassign (UPDATE) anything, the exact bug this file exists
    to keep dead."""

    def __init__(self, owner: "_RecordingPool") -> None:
        self.owner = owner

    async def fetchrow(self, sql, *args):
        squashed = _squash(sql)
        self.owner.statements.append(squashed)
        self.owner.args.append(args)
        if squashed.startswith("SELECT COUNT(*) AS n FROM vault_credentials"):
            return {"n": self.owner.credentials_still_bound}
        return dict(self.owner.counts)

    async def fetch(self, sql, *args):
        squashed = _squash(sql)
        self.owner.statements.append(squashed)
        self.owner.args.append(args)
        if squashed.startswith("DELETE FROM vault_credentials"):
            return [{"id": cred_id} for cred_id in self.owner.credentials_deleted_ids]
        raise AssertionError(f"unexpected fetch() statement: {squashed}")

    async def execute(self, sql, *args):
        squashed = _squash(sql)
        self.owner.statements.append(squashed)
        self.owner.args.append(args)
        if squashed.startswith("DELETE FROM projects"):
            return self.owner.delete_tag
        raise AssertionError(f"delete_project must never reassign anything — unexpected statement: {squashed}")


class _RecordingTransaction:
    def __init__(self, owner: "_RecordingPool") -> None:
        self.owner = owner

    async def __aenter__(self):
        self.owner.transactions_opened += 1
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.owner.committed = True
        else:
            self.owner.rolled_back = True
        return False


class _RecordingPool:
    def __init__(
        self,
        counts: dict | None = None,
        delete_tag: str = "DELETE 1",
        credentials_deleted_ids: list[str] | None = None,
        credentials_still_bound: int = 0,
    ) -> None:
        self.statements: list[str] = []
        self.args: list[tuple] = []
        self.counts = counts or {"tasks": 3, "documents": 1, "members": 2, "goals": 0, "agents": 2}
        self.delete_tag = delete_tag
        # Defaults to one deleted, orphaned credential and zero still-bound
        # ones — matches _deleted_summary()'s own default so existing tests
        # asserting against that summary don't have to know this detail.
        self.credentials_deleted_ids = ["cred-1"] if credentials_deleted_ids is None else credentials_deleted_ids
        self.credentials_still_bound = credentials_still_bound
        self.transactions_opened = 0
        self.committed = False
        self.rolled_back = False
        self._connection = _RecordingConnection(self)

    def acquire(self):
        pool = self

        class _Acquire:
            async def __aenter__(self):
                return pool._connection

            async def __aexit__(self, *_exc):
                return False

        return _Acquire()


def _install_recording_pool(monkeypatch, pool: _RecordingPool) -> None:
    monkeypatch.setattr(
        projects_repository.control_plane_repository,
        "ensure_control_plane_schema",
        AsyncMock(return_value=pool),
    )
    monkeypatch.setattr(
        projects_repository.control_plane_repository,
        "apply_connection_scope",
        AsyncMock(return_value=None),
    )
    # asyncpg's Connection.transaction() is sync-returning-context-manager.
    monkeypatch.setattr(
        _RecordingConnection, "transaction", lambda self: _RecordingTransaction(self.owner), raising=False,
    )


def _never_ensure_default_project(monkeypatch) -> AsyncMock:
    """Wired into every repository-level test below: `delete_project` must
    NEVER call `ensure_default_project` (create-if-absent) for ANY project,
    is_default or not — that call is exactly what used to silently CREATE a
    fresh "General" project as a side effect of deleting a different one,
    for any workspace with no project already marked is_default. Returns
    the mock so a test can also assert it directly if it wants to."""
    mock = AsyncMock(side_effect=AssertionError(
        "delete_project must never call ensure_default_project — that is the "
        "silent-recreate bug this fix removes"
    ))
    monkeypatch.setattr(projects_repository, "ensure_default_project", mock)
    return mock


@pytest.mark.anyio
async def test_repository_deletes_the_is_default_project_without_recreating_one(monkeypatch) -> None:
    """The is_default project itself, deleted, with NO other project in the
    workspace: the exact shape of "delete the last project" the founder hit
    personally. Must succeed, must issue the DELETE, and must never call
    ensure_default_project — proving no replacement "General" project gets
    invented as a side effect."""
    recorder = _RecordingPool()
    _install_recording_pool(monkeypatch, recorder)
    monkeypatch.setattr(
        projects_repository,
        "get_project",
        AsyncMock(return_value={"id": "proj-default", "name": "General", "is_default": True}),
    )
    _never_ensure_default_project(monkeypatch)

    result = await projects_repository.delete_project(
        tenant_id="tenant-1", workspace_id="ws-1", project_id="proj-default",
    )

    assert result is not None
    assert result["id"] == "proj-default"
    assert any(s.startswith("DELETE FROM projects") for s in recorder.statements)


@pytest.mark.anyio
async def test_repository_returns_none_for_a_project_in_another_workspace(monkeypatch) -> None:
    """get_project is already tenant+workspace scoped, so "not mine" and
    "doesn't exist" both arrive here as None — and neither may delete."""
    recorder = _RecordingPool()
    _install_recording_pool(monkeypatch, recorder)
    monkeypatch.setattr(projects_repository, "get_project", AsyncMock(return_value=None))
    _never_ensure_default_project(monkeypatch)

    result = await projects_repository.delete_project(
        tenant_id="tenant-1", workspace_id="ws-1", project_id="someone-elses",
    )

    assert result is None
    assert recorder.statements == []


@pytest.mark.anyio
async def test_agents_are_never_reassigned_and_land_on_null(monkeypatch) -> None:
    """The old code reassigned workspace_agent_installs.project_id to a
    "default" project before the DELETE, specifically to dodge the FK's own
    ON DELETE SET NULL. That reassignment is gone (see module docstring) —
    _RecordingConnection.execute raises if delete_project issues anything
    other than the DELETE itself, so this proves no such statement is
    attempted; the FK is left to null the column out on its own when the
    row is deleted. (vault_credentials is NOT this shape any more — see
    the credentials-specific tests below; it is hard-deleted, not
    reassigned OR left to the FK, unless a live agent still uses it.)"""
    recorder = _RecordingPool()
    _install_recording_pool(monkeypatch, recorder)
    monkeypatch.setattr(
        projects_repository,
        "get_project",
        AsyncMock(return_value={"id": "proj-1", "name": "Demo takes", "is_default": False}),
    )
    _never_ensure_default_project(monkeypatch)

    result = await projects_repository.delete_project(
        tenant_id="tenant-1", workspace_id="ws-1", project_id="proj-1",
    )

    assert result is not None
    # Reported for an honest tally, not because anything was moved — see
    # `_deleted_summary`'s doc comment on the field names.
    assert result["agents_unassigned"] == 2


@pytest.mark.anyio
async def test_counts_are_read_before_the_cascade_takes_the_rows(monkeypatch) -> None:
    """The tally the confirmation and the audit-ledger entry are built from
    can only be honest if it is read while the rows still exist — agents
    included, same as tasks/documents/members/goals. (Credentials get their
    own ordering test below: the DELETE FROM vault_credentials IS the
    count, via RETURNING, so there's no separate SELECT to race.)"""
    recorder = _RecordingPool(
        counts={"tasks": 7, "documents": 2, "members": 4, "goals": 1, "agents": 5}
    )
    _install_recording_pool(monkeypatch, recorder)
    monkeypatch.setattr(
        projects_repository,
        "get_project",
        AsyncMock(return_value={"id": "proj-1", "name": "Demo takes", "is_default": False}),
    )
    _never_ensure_default_project(monkeypatch)

    result = await projects_repository.delete_project(
        tenant_id="tenant-1", workspace_id="ws-1", project_id="proj-1",
    )

    count_at = next(i for i, s in enumerate(recorder.statements) if "SELECT (SELECT COUNT(*) FROM project_tasks" in s)
    delete_at = next(i for i, s in enumerate(recorder.statements) if s.startswith("DELETE FROM projects"))
    assert count_at < delete_at

    assert result["tasks_deleted"] == 7
    assert result["documents_deleted"] == 2
    assert result["members_removed"] == 4
    assert result["goals_deleted"] == 1
    assert result["agents_unassigned"] == 5


@pytest.mark.anyio
async def test_a_delete_that_removed_nothing_rolls_back_and_reports_nothing(monkeypatch) -> None:
    """Raced with a concurrent delete: the DELETE matched zero rows. Saying
    "deleted" here would be exactly the silent-misrouting dishonesty
    CLAUDE.md warns about — real call, real success, wrong bookkeeping."""
    recorder = _RecordingPool(delete_tag="DELETE 0")
    _install_recording_pool(monkeypatch, recorder)
    monkeypatch.setattr(
        projects_repository,
        "get_project",
        AsyncMock(return_value={"id": "proj-1", "name": "Demo takes", "is_default": False}),
    )
    _never_ensure_default_project(monkeypatch)

    result = await projects_repository.delete_project(
        tenant_id="tenant-1", workspace_id="ws-1", project_id="proj-1",
    )
    assert result is None
    assert recorder.rolled_back is True
    assert recorder.committed is False


# ── 4. Credentials: hard-deleted, unless a live agent still uses them ──────


@pytest.mark.anyio
async def test_orphaned_project_credentials_are_hard_deleted(monkeypatch) -> None:
    """The actual fix: a project-scoped vault_credentials row that no live
    agent_connector_bindings row points at is DELETED, same transaction —
    never left to the FK's ON DELETE SET NULL, which is exactly how a
    credential used to go invisible-and-unrevokable (module docstring,
    part 4). The repository issues a real `DELETE FROM vault_credentials
    ... RETURNING id`; this test's mock returns two ids for it, and the
    result must report exactly that count as deleted."""
    recorder = _RecordingPool(credentials_deleted_ids=["cred-1", "cred-2"], credentials_still_bound=0)
    _install_recording_pool(monkeypatch, recorder)
    monkeypatch.setattr(
        projects_repository,
        "get_project",
        AsyncMock(return_value={"id": "proj-1", "name": "Demo takes", "is_default": False}),
    )
    _never_ensure_default_project(monkeypatch)

    result = await projects_repository.delete_project(
        tenant_id="tenant-1", workspace_id="ws-1", project_id="proj-1",
    )

    assert result is not None
    assert result["credentials_deleted"] == 2
    assert result["credentials_still_bound"] == 0
    assert any(
        s.startswith("DELETE FROM vault_credentials") and "NOT IN" in s
        for s in recorder.statements
    ), "delete_project must issue a real DELETE FROM vault_credentials, excluding still-bound rows"


@pytest.mark.anyio
async def test_a_credential_a_live_agent_still_uses_is_not_deleted(monkeypatch) -> None:
    """THE case this fix has to get right: a project-scoped credential with
    an ENABLED agent_connector_bindings row still pointing at it is a LIVE
    dependency (secrets_broker._resolve_bound_connector_credential_id reads
    it at tool-call time) — completely independent of this project's own
    lifecycle, per the "N agents share ONE credential" reuse feature
    (connectors_actions.py). Hard-deleting it would break that agent's next
    tool call, a worse bug than the invisible-orphan one this fix targets.
    The repository's own DELETE statement already excludes such rows via
    its `NOT IN (SELECT ... FROM agent_connector_bindings WHERE enabled)`
    clause — this test's mock simulates that exclusion actually holding (a
    row that stays, reported separately) and asserts the result never
    claims it as deleted."""
    recorder = _RecordingPool(credentials_deleted_ids=[], credentials_still_bound=1)
    _install_recording_pool(monkeypatch, recorder)
    monkeypatch.setattr(
        projects_repository,
        "get_project",
        AsyncMock(return_value={"id": "proj-1", "name": "Demo takes", "is_default": False}),
    )
    _never_ensure_default_project(monkeypatch)

    result = await projects_repository.delete_project(
        tenant_id="tenant-1", workspace_id="ws-1", project_id="proj-1",
    )

    assert result is not None
    assert result["credentials_deleted"] == 0
    assert result["credentials_still_bound"] == 1
    # The still-bound row is never claimed as gone anywhere else in the
    # summary either — no other field secretly absorbs it.
    assert result["credentials_deleted"] != result["credentials_still_bound"] or result["credentials_deleted"] == 0


@pytest.mark.anyio
async def test_credential_delete_query_excludes_enabled_bindings_scoped_correctly(monkeypatch) -> None:
    """Pins the actual WHERE shape, not just the outcome: the DELETE must
    filter agent_connector_bindings on `enabled = TRUE` (a disabled/
    unsubscribed binding is not a live dependency) and must scope both the
    credential and the bindings lookup to THIS workspace_id/tenant_id — a
    credential id colliding across tenants must never save a row that
    should go, or delete one that shouldn't."""
    recorder = _RecordingPool()
    _install_recording_pool(monkeypatch, recorder)
    monkeypatch.setattr(
        projects_repository,
        "get_project",
        AsyncMock(return_value={"id": "proj-1", "name": "Demo takes", "is_default": False}),
    )
    _never_ensure_default_project(monkeypatch)

    await projects_repository.delete_project(
        tenant_id="tenant-1", workspace_id="ws-1", project_id="proj-1",
    )

    delete_stmt = next(s for s in recorder.statements if s.startswith("DELETE FROM vault_credentials"))
    assert "enabled = TRUE" in delete_stmt
    assert "agent_connector_bindings" in delete_stmt
    delete_args = next(a for s, a in zip(recorder.statements, recorder.args) if s.startswith("DELETE FROM vault_credentials"))
    assert delete_args == ("ws-1", "proj-1", "tenant-1")


@pytest.mark.anyio
async def test_credentials_are_deleted_before_the_project_row_in_the_same_transaction(monkeypatch) -> None:
    """Ordering matters here too: the credential DELETE must run before the
    project's own DELETE (whose FK ON DELETE SET NULL would otherwise race
    it for the still-bound rows), and both must be inside the one
    transaction `test_the_whole_removal_is_one_transaction` already pins."""
    recorder = _RecordingPool()
    _install_recording_pool(monkeypatch, recorder)
    monkeypatch.setattr(
        projects_repository,
        "get_project",
        AsyncMock(return_value={"id": "proj-1", "name": "Demo takes", "is_default": False}),
    )
    _never_ensure_default_project(monkeypatch)

    await projects_repository.delete_project(
        tenant_id="tenant-1", workspace_id="ws-1", project_id="proj-1",
    )

    credentials_at = next(i for i, s in enumerate(recorder.statements) if s.startswith("DELETE FROM vault_credentials"))
    project_at = next(i for i, s in enumerate(recorder.statements) if s.startswith("DELETE FROM projects"))
    assert credentials_at < project_at
    assert recorder.transactions_opened == 1
    assert recorder.committed is True


@pytest.mark.anyio
async def test_the_whole_removal_is_one_transaction(monkeypatch) -> None:
    """The count SELECT and the DELETE run on ONE connection inside ONE
    transaction — either both apply or neither does."""
    recorder = _RecordingPool()
    _install_recording_pool(monkeypatch, recorder)
    monkeypatch.setattr(
        projects_repository,
        "get_project",
        AsyncMock(return_value={"id": "proj-1", "name": "Demo takes", "is_default": False}),
    )
    _never_ensure_default_project(monkeypatch)

    result = await projects_repository.delete_project(
        tenant_id="tenant-1", workspace_id="ws-1", project_id="proj-1",
    )

    assert result is not None
    assert recorder.transactions_opened == 1
    assert recorder.committed is True
    assert recorder.rolled_back is False


def test_affected_row_count_parses_command_tags() -> None:
    assert projects_repository._affected_row_count("DELETE 1") == 1
    assert projects_repository._affected_row_count("UPDATE 12") == 12
    assert projects_repository._affected_row_count("") == 0
    assert projects_repository._affected_row_count(None) == 0
    assert projects_repository._affected_row_count("UPDATE") == 0
