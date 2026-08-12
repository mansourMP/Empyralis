"""migrations/add_task_sequence_numbers.sql built the projects.task_key /
task_seq columns and wrote an extensive comment describing exactly how they
should be populated -- "every future INSERT assigns one -- see
projects_repository.create_project" -- but that Python was never actually
written. Every project created after the migration landed still got a NULL
task_key, so every task rendered a random hex slice of its own id
(task-status.taskShortId's confessed placeholder) instead of Linear's
"GEN-12" shape. This is the "built, tested, and never wired" failure mode
CLAUDE.md documents repeatedly, one level up: only the SCHEMA was built.

Deliberately DB-free: control_plane_repository is monkeypatched, following
test_projects_repository_member_grant_logging_man299.py's own pattern in
this same directory (a NEW file, per docs/AGENT-OPERATING-RULES.md's
collision protocol -- most pre-existing test files belong to other
concurrently-running agents).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from server_modules import control_plane_repository
from server_modules import projects_repository


class _FakePool:
    """Never actually queried -- every control_plane_repository call
    create_project makes is monkeypatched below."""


def _fake_row(*, project_id: str, task_key: str) -> dict:
    return {
        "id": project_id,
        "tenant_id": "tenant-x",
        "workspace_id": "ws-x",
        "name": "General",
        "slug": "general",
        "description": "",
        "is_default": False,
        "archived": False,
        "metadata": {},
        "created_at": "2026-08-13T00:00:00Z",
        "updated_at": "2026-08-13T00:00:00Z",
        "task_key": task_key,
    }


def _install_fetch(monkeypatch: pytest.MonkeyPatch, *, existing_task_keys: list[str]) -> AsyncMock:
    """Dispatches on the query text: `_unique_slug`'s SELECT sees no
    existing slugs (this suite is not testing slug collision), and
    `_unique_task_key`'s SELECT sees `existing_task_keys` -- the two calls
    share one mocked function the same way they share one real one in
    production, so the query text is what tells them apart, not call
    order."""

    async def _fetch(pool: Any, query: str, *args: Any, **kwargs: Any) -> list[dict]:
        if "SELECT task_key FROM projects" in query:
            return [{"task_key": key} for key in existing_task_keys]
        if "SELECT slug FROM projects" in query:
            return []
        raise AssertionError(f"unexpected rls_fetch query in this test: {query}")

    mock = AsyncMock(side_effect=_fetch)
    monkeypatch.setattr(control_plane_repository, "rls_fetch", mock)
    return mock


@pytest.fixture(autouse=True)
def _mock_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        control_plane_repository, "ensure_control_plane_schema", AsyncMock(return_value=_FakePool())
    )


@pytest.mark.anyio
async def test_create_project_assigns_a_task_key_derived_from_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fetch(monkeypatch, existing_task_keys=[])
    insert_mock = AsyncMock(return_value=_fake_row(project_id="project_1", task_key="GEN"))
    monkeypatch.setattr(control_plane_repository, "rls_fetchrow", insert_mock)

    project = await projects_repository.create_project(
        tenant_id="tenant-x", workspace_id="ws-x", name="General", slug="general",
    )

    # The INSERT was handed a task_key, not left to a column default --
    # same "written explicitly" posture project_tasks_service.create_task's
    # own priority column takes.
    insert_mock.assert_awaited_once()
    insert_query = insert_mock.await_args.args[1]
    assert "task_key" in insert_query
    # The task_key positional argument is the 9th bound value (id, tenant,
    # workspace, name, slug, description, is_default, metadata, task_key).
    bound_args = insert_mock.await_args.args[2:]
    assert bound_args[8] == "GEN"

    assert project is not None
    assert project["task_key"] == "GEN"


@pytest.mark.anyio
async def test_create_project_dedupes_a_colliding_task_key_with_a_numeric_suffix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two projects that both reduce to the same 3-letter key ("General"
    and "General Ops" both give GEN) must not collide -- the later one gets
    GEN2, exactly as migrations/add_task_sequence_numbers.sql's own
    backfill loop does it."""
    _install_fetch(monkeypatch, existing_task_keys=["GEN"])
    insert_mock = AsyncMock(return_value=_fake_row(project_id="project_2", task_key="GEN2"))
    monkeypatch.setattr(control_plane_repository, "rls_fetchrow", insert_mock)

    project = await projects_repository.create_project(
        tenant_id="tenant-x", workspace_id="ws-x", name="General Ops", slug="general-ops",
    )

    bound_args = insert_mock.await_args.args[2:]
    assert bound_args[8] == "GEN2"
    assert project["task_key"] == "GEN2"


@pytest.mark.anyio
async def test_task_key_falls_back_to_tsk_for_a_slug_with_no_letters(monkeypatch: pytest.MonkeyPatch) -> None:
    """A slug that strips to nothing alphanumeric must not produce an empty
    task_key -- migrations/add_task_sequence_numbers.sql's own backfill
    falls back to 'TSK' for exactly this case. Calls _unique_task_key
    directly rather than through create_project: _slugify's own
    fallback="project" already prevents create_project from ever handing
    it a slug that reduces to empty (every _slugify output is either
    non-empty alnum-and-hyphens, or "project"), so this branch is reachable
    only by exercising the helper itself -- e.g. against a slug column
    written before slugs were normalized this strictly, or a future caller
    that skips _slugify."""
    _install_fetch(monkeypatch, existing_task_keys=[])
    pool = _FakePool()

    task_key = await projects_repository._unique_task_key(pool, "tenant-x", "ws-x", "---")

    assert task_key == "TSK"


@pytest.mark.anyio
async def test_row_to_project_reads_a_missing_task_key_as_none_not_a_raise() -> None:
    """A project created before create_project started assigning task_key,
    or on a database predating the migration, must read as absent rather
    than raising -- the same deploy-before-migrate posture every sibling
    column on this row already takes."""
    row = _fake_row(project_id="project_old", task_key=None)
    project = projects_repository._row_to_project(row)
    assert project is not None
    assert project["task_key"] is None
