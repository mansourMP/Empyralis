"""Removing a project — DELETE /api/w/{ws}/fleet/projects/{id} and
projects_repository.delete_project.

Until this shipped a project could be created and never removed: the repo
had `set_project_archived` with NO caller anywhere in the frontend (the
"built, tested, and never wired" failure mode CLAUDE.md names first) and no
delete function at all. These tests pin the three things that make the new
delete safe rather than merely present:

1. PERMISSION. Owner-on-the-workspace, exactly matching the create/patch
   routes it supersedes. A viewer, a member, and — the case that actually
   matters for multiplayer — an OWNER OF A DIFFERENT WORKSPACE all get 403,
   and the repository is never reached. The workspace's tenant is resolved
   per workspace (control_plane_repository.resolve_tenant_id_for_workspace),
   never off the stale users.tenant_id column; one test drives a user whose
   home tenant disagrees with the workspace's, which is the exact shape of
   the bug CLAUDE.md documents.

2. THE DEFAULT-PROJECT GUARD. `ensure_default_project`'s project is a
   workspace's home for ungrouped agents; deleting it is refused at the
   repository (ValueError) and surfaced by the route as a normal
   {"ok": false, "error": ...}, with no DELETE ever issued.

3. WHAT HAPPENS TO WHAT THE PROJECT OWNED. Tasks/documents/goals/memberships
   go by FK cascade — but agents and project-scoped vault credentials are
   REASSIGNED to the default project FIRST, before the DELETE, precisely so
   the FK's ON DELETE SET NULL never runs on them. A NULL
   workspace_agent_installs.project_id silently revokes an agent's whole
   project_task__*/document__*/goal__* toolset (project membership IS the
   grant), and a NULL vault_credentials.project_id makes a stored secret
   invisible to every surface that could revoke it. The ordering assertion
   is the point of these tests, not decoration.
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
        "agents_moved": 2,
        "credentials_moved": 1,
        "moved_to_project_id": "proj-default",
        "moved_to_project_name": "General",
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
    # The caller is told exactly what went and where the agents landed —
    # "silently drop a user's tasks" is the outcome this response prevents.
    assert body["deleted"]["tasks_deleted"] == 3
    assert body["deleted"]["documents_deleted"] == 1
    assert body["deleted"]["agents_moved"] == 2
    assert body["deleted"]["moved_to_project_name"] == "General"
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


# ── 2. The default-project guard ───────────────────────────────────────────


@pytest.mark.anyio
async def test_route_surfaces_the_default_project_refusal() -> None:
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _owner_user

    with (
        patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
        patch(
            "server_modules.projects_repository.delete_project",
            new=AsyncMock(side_effect=ValueError("The default project cannot be deleted.")),
        ),
        patch(
            "server_modules.activity_ledger_service.append_activity_event",
            new=AsyncMock(side_effect=AssertionError("nothing was deleted; nothing to journal")),
        ),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.delete("/api/w/ws-1/fleet/projects/proj-default")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["error"] == "The default project cannot be deleted."


# ── 3. The repository: ordering, rehoming, and what the cascade takes ──────


def _squash(sql: str) -> str:
    return " ".join(str(sql).split())


class _RecordingConnection:
    """Captures every statement delete_project issues, in order, so the
    tests can assert on sequence — the ordering (reassign, THEN delete) is
    the entire safety property here. Also records whether the whole unit of
    work committed or rolled back, since "all or nothing" is the other."""

    def __init__(self, owner: "_RecordingPool") -> None:
        self.owner = owner

    async def fetchrow(self, sql, *args):
        self.owner.statements.append(_squash(sql))
        self.owner.args.append(args)
        return dict(self.owner.counts)

    async def execute(self, sql, *args):
        squashed = _squash(sql)
        self.owner.statements.append(squashed)
        self.owner.args.append(args)
        if squashed.startswith("DELETE FROM projects"):
            return self.owner.delete_tag
        if "workspace_agent_installs" in squashed:
            return "UPDATE 2"
        if "vault_credentials" in squashed:
            return "UPDATE 1"
        return "UPDATE 0"


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
    def __init__(self, counts: dict | None = None, delete_tag: str = "DELETE 1") -> None:
        self.statements: list[str] = []
        self.args: list[tuple] = []
        self.counts = counts or {"tasks": 3, "documents": 1, "members": 2, "goals": 0}
        self.delete_tag = delete_tag
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


@pytest.mark.anyio
async def test_repository_refuses_the_default_project_and_issues_no_delete(monkeypatch) -> None:
    recorder = _RecordingPool()
    _install_recording_pool(monkeypatch, recorder)
    monkeypatch.setattr(
        projects_repository,
        "get_project",
        AsyncMock(return_value={"id": "proj-default", "name": "General", "is_default": True}),
    )

    with pytest.raises(ValueError, match="default project cannot be deleted"):
        await projects_repository.delete_project(
            tenant_id="tenant-1", workspace_id="ws-1", project_id="proj-default",
        )

    assert not any(s.startswith("DELETE FROM projects") for s in recorder.statements)


@pytest.mark.anyio
async def test_repository_returns_none_for_a_project_in_another_workspace(monkeypatch) -> None:
    """get_project is already tenant+workspace scoped, so "not mine" and
    "doesn't exist" both arrive here as None — and neither may delete."""
    recorder = _RecordingPool()
    _install_recording_pool(monkeypatch, recorder)
    monkeypatch.setattr(projects_repository, "get_project", AsyncMock(return_value=None))

    result = await projects_repository.delete_project(
        tenant_id="tenant-1", workspace_id="ws-1", project_id="someone-elses",
    )

    assert result is None
    assert recorder.statements == []


@pytest.mark.anyio
async def test_agents_and_credentials_are_rehomed_before_the_row_is_deleted(monkeypatch) -> None:
    """The load-bearing ordering test. If the DELETE ran first, the FK's
    ON DELETE SET NULL would win the race and both UPDATEs would match zero
    rows: agents would lose their project-scoped toolset with no error
    anywhere, and the project's vault credentials would become unreachable.
    """
    recorder = _RecordingPool()
    _install_recording_pool(monkeypatch, recorder)
    monkeypatch.setattr(
        projects_repository,
        "get_project",
        AsyncMock(return_value={"id": "proj-1", "name": "Demo takes", "is_default": False}),
    )
    monkeypatch.setattr(
        projects_repository,
        "ensure_default_project",
        AsyncMock(return_value={"id": "proj-default", "name": "General", "is_default": True}),
    )

    result = await projects_repository.delete_project(
        tenant_id="tenant-1", workspace_id="ws-1", project_id="proj-1",
    )

    installs_at = next(i for i, s in enumerate(recorder.statements) if "workspace_agent_installs" in s)
    credentials_at = next(i for i, s in enumerate(recorder.statements) if "vault_credentials" in s)
    delete_at = next(i for i, s in enumerate(recorder.statements) if s.startswith("DELETE FROM projects"))
    assert installs_at < delete_at
    assert credentials_at < delete_at

    # Both reassignments point at the workspace's default project. The
    # ARGUMENT SHAPES DIFFER ON PURPOSE and this asserts the difference:
    # workspace_agent_installs is scoped by tenant + workspace + project,
    # but vault_credentials HAS NO tenant_id COLUMN (one of the tables
    # CLAUDE.md names as carrying only one of the two scope columns), so
    # binding one there is not a stricter filter, it is a hard
    # `column "tenant_id" does not exist` — which is exactly how the first
    # browser run of this delete failed.
    assert recorder.args[installs_at] == ("tenant-1", "ws-1", "proj-1", "proj-default")
    assert recorder.args[credentials_at] == ("ws-1", "proj-1", "proj-default")
    assert "tenant_id" not in recorder.statements[credentials_at]

    assert result is not None
    assert result["agents_moved"] == 2
    assert result["credentials_moved"] == 1
    assert result["moved_to_project_id"] == "proj-default"


@pytest.mark.anyio
async def test_counts_are_read_before_the_cascade_takes_the_rows(monkeypatch) -> None:
    """The tally the confirmation and the audit-ledger entry are built from
    can only be honest if it is read while the rows still exist."""
    recorder = _RecordingPool(counts={"tasks": 7, "documents": 2, "members": 4, "goals": 1})
    _install_recording_pool(monkeypatch, recorder)
    monkeypatch.setattr(
        projects_repository,
        "get_project",
        AsyncMock(return_value={"id": "proj-1", "name": "Demo takes", "is_default": False}),
    )
    monkeypatch.setattr(
        projects_repository,
        "ensure_default_project",
        AsyncMock(return_value={"id": "proj-default", "name": "General", "is_default": True}),
    )

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


@pytest.mark.anyio
async def test_a_delete_that_removed_nothing_rolls_back_and_reports_nothing(monkeypatch) -> None:
    """Raced with a concurrent delete: the DELETE matched zero rows. Saying
    "deleted" here would be exactly the silent-misrouting dishonesty
    CLAUDE.md warns about — real call, real success, wrong bookkeeping. And
    the agent/credential reassignments must not survive either: rehoming a
    user's agents out of a project that still exists, as a side effect of an
    operation that reported failure, is the same class of lie."""
    recorder = _RecordingPool(delete_tag="DELETE 0")
    _install_recording_pool(monkeypatch, recorder)
    monkeypatch.setattr(
        projects_repository,
        "get_project",
        AsyncMock(return_value={"id": "proj-1", "name": "Demo takes", "is_default": False}),
    )
    monkeypatch.setattr(
        projects_repository,
        "ensure_default_project",
        AsyncMock(return_value={"id": "proj-default", "name": "General", "is_default": True}),
    )

    result = await projects_repository.delete_project(
        tenant_id="tenant-1", workspace_id="ws-1", project_id="proj-1",
    )
    assert result is None
    assert recorder.rolled_back is True
    assert recorder.committed is False


@pytest.mark.anyio
async def test_the_whole_removal_is_one_transaction(monkeypatch) -> None:
    """Every statement runs on ONE connection inside ONE transaction. The
    first real browser run of this delete failed on the vault_credentials
    statement, and with a statement-per-transaction helper the agents had
    already been moved out of a project that then didn't get deleted."""
    recorder = _RecordingPool()
    _install_recording_pool(monkeypatch, recorder)
    monkeypatch.setattr(
        projects_repository,
        "get_project",
        AsyncMock(return_value={"id": "proj-1", "name": "Demo takes", "is_default": False}),
    )
    monkeypatch.setattr(
        projects_repository,
        "ensure_default_project",
        AsyncMock(return_value={"id": "proj-default", "name": "General", "is_default": True}),
    )

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
