"""MAN-299: create_project's auto-add-creator membership grant used to
swallow every failure with a bare ``except Exception: pass`` -- the project
came back looking like a full success while the creator's
project_memberships row silently never landed. No log, no signal to the
caller, nothing. "Projects hold members directly. There is no Teams layer"
is a standing product law (CLAUDE.md) -- project membership *is* the
multiplayer model here, and MAN-114 shipped a real add-member UI on top of
this exact write path.

Whether there is a genuinely benign failure to special-case: no.
add_project_member's INSERT is `ON CONFLICT (project_id, user_id) DO
UPDATE` (see projects_repository.py), so "the creator is already a member"
is idempotent at the SQL level and never raises in the first place --
there is no narrow, expected exception type for this call site to catch
differently. Anything that reaches the except block here is a genuine,
unexpected failure (dropped connection, constraint violation, bad pool),
so the fix keeps the broad `except Exception` (project creation itself must
not be undone by a failed secondary write -- it already committed) but
replaces the silent `pass` with LOGGER.error(..., exc_info=True) carrying
the creator user_id, project_id, tenant_id, and workspace_id.

This also turns server_modules/tests/test_exception_and_task_lint.py's
test_no_new_files_with_bare_except_pass green for this file:
projects_repository.py is not (and must not become) an entry in
BARE_EXCEPT_PASS_BASELINE_FILES.

Deliberately DB-free: control_plane_repository is monkeypatched rather than
requiring real Postgres, since this suite only needs to prove
create_project's *own* exception-handling behavior around
add_project_member, not exercise a real INSERT. This is a NEW file (see
docs/AGENT-OPERATING-RULES.md's collision protocol -- server_modules/tests/
conftest.py and most pre-existing test files belong to other
concurrently-running agents).
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

from server_modules import control_plane_repository
from server_modules import projects_repository


class _FakePool:
    """Never actually queried -- every control_plane_repository call
    create_project makes is monkeypatched below. Just needs to be a
    non-None sentinel so `if pool is None: raise ...` doesn't trip."""


def _fake_row(*, project_id: str, tenant_id: str, workspace_id: str, name: str) -> dict:
    return {
        "id": project_id,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "name": name,
        "slug": "test-project",
        "description": "",
        "is_default": False,
        "archived": False,
        "metadata": {},
        "created_at": "2026-08-04T00:00:00Z",
        "updated_at": "2026-08-04T00:00:00Z",
    }


@pytest.fixture(autouse=True)
def _mock_control_plane(monkeypatch: pytest.MonkeyPatch) -> dict:
    pool = _FakePool()
    row = _fake_row(
        project_id="project_test123",
        tenant_id="tenant-x",
        workspace_id="ws-x",
        name="Test Project",
    )
    monkeypatch.setattr(
        control_plane_repository, "ensure_control_plane_schema", AsyncMock(return_value=pool)
    )
    # _unique_slug's SELECT -- no existing rows, so the requested slug is free.
    monkeypatch.setattr(control_plane_repository, "rls_fetch", AsyncMock(return_value=[]))
    # The project INSERT ... RETURNING.
    monkeypatch.setattr(control_plane_repository, "rls_fetchrow", AsyncMock(return_value=row))
    return row


@pytest.mark.anyio
async def test_member_grant_failure_is_logged_not_silent(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    failure = RuntimeError("simulated DB failure: connection reset")
    add_member_mock = AsyncMock(side_effect=failure)
    monkeypatch.setattr(projects_repository, "add_project_member", add_member_mock)

    with caplog.at_level(logging.ERROR, logger="server_modules.projects_repository"):
        project = await projects_repository.create_project(
            tenant_id="tenant-x",
            workspace_id="ws-x",
            name="Test Project",
            created_by_user_id="user-123",
        )

    # Design intent preserved: a failed membership grant must not undo (or
    # appear to undo) an already-committed project create.
    assert project is not None
    assert project["id"] == "project_test123"

    add_member_mock.assert_awaited_once()
    _, kwargs = add_member_mock.await_args
    assert kwargs["user_id"] == "user-123"
    assert kwargs["project_id"] == "project_test123"

    # The actual bug: this used to be a bare `except Exception: pass`, so
    # NOTHING below was ever true on unmodified main -- caplog.text was
    # empty and this assertion is exactly what fails pre-fix.
    assert "create_project_member_grant_failed" in caplog.text
    assert "user-123" in caplog.text
    assert "project_test123" in caplog.text
    assert "tenant-x" in caplog.text
    assert "ws-x" in caplog.text
    # exc_info=True must carry the real exception through, not just a
    # generic "something failed" line.
    assert "simulated DB failure: connection reset" in caplog.text


@pytest.mark.anyio
async def test_member_grant_success_is_not_logged_as_a_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Sanity counterpart: the happy path (no exception at all) must stay
    silent at ERROR level -- this fix must not turn ordinary success into
    log noise."""
    add_member_mock = AsyncMock(return_value={"id": "projmember_1", "role": "owner"})
    monkeypatch.setattr(projects_repository, "add_project_member", add_member_mock)

    with caplog.at_level(logging.ERROR, logger="server_modules.projects_repository"):
        project = await projects_repository.create_project(
            tenant_id="tenant-x",
            workspace_id="ws-x",
            name="Test Project",
            created_by_user_id="user-123",
        )

    assert project is not None
    add_member_mock.assert_awaited_once()
    assert "create_project_member_grant_failed" not in caplog.text
