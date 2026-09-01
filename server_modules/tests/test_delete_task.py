"""Removing a single task — DELETE /api/w/{ws}/fleet/tasks/{id} and
project_tasks_service.delete_task.

Before this shipped there was NO way to delete a standalone task at all — it
could only die via its project's own CASCADE (projects_repository.
delete_project). The founder asked for this directly. These tests pin the
things that make the new delete safe rather than merely present, mirroring
test_delete_project.py's structure for the sibling feature:

1. PERMISSION MATCHES PATCH, NOT A NEW SHAPE. `member`, gated on the task's
   own project via _enforce_task_project_access — exactly fleet_patch_task's
   floor, not owner-only. A viewer is rejected; a plain MEMBER (below owner)
   succeeds, proving this isn't quietly stricter than the mutation it
   supersedes. A caller outside the workspace/tenant entirely is rejected by
   the real auth guard.

2. WHAT HAPPENS TO WHAT THE TASK OWNED. Comments (stored on the row itself)
   and label attachments die with it; the label vocabulary survives. Its
   SUB-TASKS are NOT deleted — parent_task_id -> this row is ON DELETE SET
   NULL (migrations/add_task_parent.sql), so they are promoted to top-level
   tasks. Counts are read inside the SAME transaction as the DELETE, before
   the FK actions run, so the summary the caller reports is never a guess.

3. HONESTY ON A RACE. A DELETE that matched zero rows (raced with a
   concurrent delete) must report "not found", never "deleted" — same
   posture delete_project's own test suite pins.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI

from server_modules import project_tasks_service, routes_fleet


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
    """A plain member — below owner. Proves the delete route's floor is the
    same `member` every other task mutation (patch/assign/comment/label)
    already uses, not a stricter, delete-specific tier."""
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


def _intruder_user() -> dict:
    """Authenticated, but the only membership is a different workspace/
    tenant — must not be able to delete ws-1's task just by putting ws-1's
    id in the URL."""
    return {
        "user_id": "intruder-1",
        "email": "intruder@example.com",
        "auth_type": "bearer",
        "workspace_access": {
            "ws-intruder-home": {
                "workspace_id": "ws-intruder-home",
                "tenant_id": "tenant-intruder",
                "role": "owner",
                "tenant_role": "owner",
            }
        },
    }


def _deleted_summary(**overrides) -> dict:
    base = {
        "id": "task-1",
        "title": "Ship the thing",
        "project_id": "proj-1",
        "subtasks_promoted": 2,
        "comments_deleted": 3,
        "labels_detached": 1,
    }
    base.update(overrides)
    return base


# ── 1. Permission ──────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_unauthenticated_delete_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORION_AUTH_REQUIRED", "1")
    monkeypatch.delenv("EMPYRALIS_DEPLOY_ENV", raising=False)
    monkeypatch.delenv("ORION_ENV", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("NODE_ENV", raising=False)

    app = _build_app()
    # No dependency_overrides -- exercises the REAL auth_module.get_current_user.

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.delete("/api/w/ws-victim/fleet/tasks/task-1")
    assert response.status_code == 401


@pytest.mark.anyio
async def test_wrong_workspace_delete_is_rejected() -> None:
    """Exercises the real auth_module.enforce_workspace_access, not a mock."""
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _intruder_user

    with patch(
        "server_modules.project_tasks_service.delete_task",
        new=AsyncMock(side_effect=AssertionError("must never reach the service layer")),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.delete("/api/w/ws-1/fleet/tasks/task-1")
    assert response.status_code == 403


@pytest.mark.anyio
async def test_viewer_cannot_delete() -> None:
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _viewer_user

    with patch(
        "server_modules.project_tasks_service.delete_task",
        new=AsyncMock(side_effect=AssertionError("must never reach the service layer")),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.delete("/api/w/ws-1/fleet/tasks/task-1")
    assert response.status_code == 403


@pytest.mark.anyio
async def test_a_plain_member_can_delete_a_task() -> None:
    """The floor is `member`, exactly fleet_patch_task's own — a member who
    is not an owner must still be able to delete a task they can see."""
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _member_user

    with (
        patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
        patch(
            "server_modules.project_tasks_service.delete_task",
            new=AsyncMock(return_value=_deleted_summary()),
        ) as delete_mock,
        patch("server_modules.activity_ledger_service.append_activity_event", new=AsyncMock(return_value=None)),
        patch(
            "server_modules.control_plane_repository.get_workspace_by_id",
            new=AsyncMock(return_value={"id": "ws-1", "tenant_id": "tenant-1"}),
        ),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.delete("/api/w/ws-1/fleet/tasks/task-1")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    delete_mock.assert_awaited_once()


# ── 2. Route behavior ────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_owner_delete_returns_what_was_removed() -> None:
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _owner_user

    with (
        patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
        patch(
            "server_modules.project_tasks_service.delete_task",
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
            response = await client.delete("/api/w/ws-1/fleet/tasks/task-1")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["deleted"]["subtasks_promoted"] == 2
    assert body["deleted"]["comments_deleted"] == 3
    assert body["deleted"]["labels_detached"] == 1
    delete_mock.assert_awaited_once()
    assert delete_mock.await_args.kwargs["task_id"] == "task-1"
    assert delete_mock.await_args.kwargs["workspace_id"] == "ws-1"
    assert delete_mock.await_args.kwargs["tenant_id"] == "tenant-1"
    ledger_mock.assert_awaited_once()
    assert ledger_mock.await_args.kwargs["action"] == "task_deleted"


@pytest.mark.anyio
async def test_unknown_task_is_reported_not_claimed_as_deleted() -> None:
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _owner_user

    with (
        patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
        patch("server_modules.project_tasks_service.delete_task", new=AsyncMock(return_value=None)),
        patch(
            "server_modules.activity_ledger_service.append_activity_event",
            new=AsyncMock(side_effect=AssertionError("nothing was deleted; nothing to journal")),
        ),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.delete("/api/w/ws-1/fleet/tasks/nope")

    assert response.status_code == 200
    assert response.json() == {"ok": False, "error": "Task not found."}


@pytest.mark.anyio
async def test_service_failure_is_surfaced_as_ok_false_not_500() -> None:
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _owner_user

    with (
        patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
        patch(
            "server_modules.project_tasks_service.delete_task",
            new=AsyncMock(side_effect=RuntimeError("db unavailable")),
        ),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.delete("/api/w/ws-1/fleet/tasks/task-1")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "db unavailable" in body["error"]


# ── 3. The repository: counts, sub-task promotion, and the one transaction ─


def _squash(sql: str) -> str:
    return " ".join(str(sql).split())


class _RecordingConnection:
    """Captures every statement delete_task issues, in order — the count
    SELECT must run before the DELETE, or the tally it produces is a lie
    about rows already gone. Also records commit/rollback."""

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
        if squashed.startswith("DELETE FROM project_tasks"):
            return self.owner.delete_tag
        raise AssertionError(f"delete_task issued an unexpected statement: {squashed}")


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
        self.counts = counts or {"subtasks": 2, "labels": 1, "comments": 3}
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
        project_tasks_service.control_plane_repository,
        "ensure_control_plane_schema",
        AsyncMock(return_value=pool),
    )
    monkeypatch.setattr(
        project_tasks_service.control_plane_repository,
        "apply_connection_scope",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        _RecordingConnection, "transaction", lambda self: _RecordingTransaction(self.owner), raising=False,
    )


def _stub_get_task(monkeypatch, task: dict | None) -> None:
    monkeypatch.setattr(project_tasks_service, "get_task", AsyncMock(return_value=task))


@pytest.mark.anyio
async def test_repository_returns_none_for_a_task_in_another_workspace(monkeypatch) -> None:
    recorder = _RecordingPool()
    _install_recording_pool(monkeypatch, recorder)
    _stub_get_task(monkeypatch, None)

    result = await project_tasks_service.delete_task(
        tenant_id="tenant-1", workspace_id="ws-1", task_id="someone-elses",
    )

    assert result is None
    assert recorder.statements == []


@pytest.mark.anyio
async def test_counts_are_read_before_the_delete_takes_the_rows(monkeypatch) -> None:
    """Sub-tasks are counted here (and reported as PROMOTED, never deleted —
    see the summary field name) precisely because the FK is ON DELETE SET
    NULL, not CASCADE; the count must be read while the parent link still
    resolves them as this task's children."""
    recorder = _RecordingPool(counts={"subtasks": 5, "labels": 4, "comments": 9})
    _install_recording_pool(monkeypatch, recorder)
    _stub_get_task(monkeypatch, {"id": "task-1", "title": "Ship the thing", "project_id": "proj-1"})

    result = await project_tasks_service.delete_task(
        tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1",
    )

    count_at = next(i for i, s in enumerate(recorder.statements) if s.startswith("SELECT"))
    delete_at = next(i for i, s in enumerate(recorder.statements) if s.startswith("DELETE FROM project_tasks"))
    assert count_at < delete_at

    assert result is not None
    assert result["id"] == "task-1"
    assert result["title"] == "Ship the thing"
    assert result["subtasks_promoted"] == 5
    assert result["labels_detached"] == 4
    assert result["comments_deleted"] == 9


@pytest.mark.anyio
async def test_a_delete_that_removed_nothing_rolls_back_and_reports_nothing(monkeypatch) -> None:
    """Raced with a concurrent delete: the DELETE matched zero rows. Saying
    "deleted" here would be exactly the silent-misrouting dishonesty
    CLAUDE.md warns about."""
    recorder = _RecordingPool(delete_tag="DELETE 0")
    _install_recording_pool(monkeypatch, recorder)
    _stub_get_task(monkeypatch, {"id": "task-1", "title": "Ship the thing", "project_id": "proj-1"})

    result = await project_tasks_service.delete_task(
        tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1",
    )
    assert result is None
    assert recorder.rolled_back is True
    assert recorder.committed is False


@pytest.mark.anyio
async def test_the_whole_removal_is_one_transaction(monkeypatch) -> None:
    recorder = _RecordingPool()
    _install_recording_pool(monkeypatch, recorder)
    _stub_get_task(monkeypatch, {"id": "task-1", "title": "Ship the thing", "project_id": "proj-1"})

    result = await project_tasks_service.delete_task(
        tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1",
    )

    assert result is not None
    assert recorder.transactions_opened == 1
    assert recorder.committed is True
    assert recorder.rolled_back is False


def test_task_delete_affected_row_count_parses_command_tags() -> None:
    assert project_tasks_service._task_delete_affected_row_count("DELETE 1") == 1
    assert project_tasks_service._task_delete_affected_row_count("DELETE 12") == 12
    assert project_tasks_service._task_delete_affected_row_count("") == 0
    assert project_tasks_service._task_delete_affected_row_count(None) == 0
    assert project_tasks_service._task_delete_affected_row_count("DELETE") == 0
