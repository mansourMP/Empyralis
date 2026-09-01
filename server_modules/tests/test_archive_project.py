"""projects_repository.set_project_archived — is_default is archivable, and
archiving no longer force-moves anything.

Companion to test_delete_project.py, same founder ruling (2026-09-01): a
locked-down `is_default` project the founder could not remove or hide was
the bug ("I have no idea how to delete this shit. General project.").
`set_project_archived` had the identical two problems `delete_project` did:

1. It refused `is_default` outright (ValueError, "the default project
   cannot be archived") — the archive equivalent of the same dead-end.

2. Before flipping the flag, it reassigned the project's agents to
   `ensure_default_project()` (create-if-absent) — for a workspace with no
   OTHER project already marked `is_default`, that silently CREATED a
   fresh "General" project as a side effect of archiving a DIFFERENT one.
   Worse than the delete-time version of this bug: archiving is supposed
   to be reversible ("Hidden from your lists. Restorable, nothing is
   lost." — ProjectSettings.tsx), and un-archiving never moved the agents
   back, so the promise was already broken before this fix.

These tests use the same monkeypatch-control_plane-repository convention as
test_projects_repository_hardware_owner_opt_in.py / test_projects_repository_
member_grant_logging_man299.py: DB-free, proving set_project_archived's own
branching, not exercising a real UPDATE.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from server_modules import control_plane_repository, projects_repository


class _FakePool:
    """Never actually queried — every control_plane_repository call this
    suite exercises is monkeypatched below. Just a non-None sentinel so
    `if pool is None: raise/return ...` doesn't trip."""


def _project_row(*, project_id: str = "proj-1", is_default: bool = False, archived: bool = True) -> dict:
    return {
        "id": project_id,
        "tenant_id": "tenant-1",
        "workspace_id": "ws-1",
        "name": "Demo takes",
        "slug": "demo-takes",
        "description": "",
        "is_default": is_default,
        "archived": archived,
        "metadata": {},
        "created_at": "2026-08-07T00:00:00Z",
        "updated_at": "2026-08-07T00:00:00Z",
    }


@pytest.fixture(autouse=True)
def _mock_pool(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        control_plane_repository, "ensure_control_plane_schema", AsyncMock(return_value=_FakePool())
    )


def _never_reassign_agents(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """set_project_archived must never touch workspace_agent_installs (or
    anything else) via rls_execute any more — the only statement it issues
    is the single UPDATE projects ... RETURNING via rls_fetchrow. A call
    here means the old rehoming behavior crept back."""
    mock = AsyncMock(side_effect=AssertionError(
        "set_project_archived must never call rls_execute — that was the "
        "agent-reassignment step this fix removes"
    ))
    monkeypatch.setattr(control_plane_repository, "rls_execute", mock)
    return mock


def _never_ensure_default_project(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Nor may it call ensure_default_project (create-if-absent) — that is
    exactly what could silently invent a fresh "General" project as a side
    effect of archiving a different one."""
    mock = AsyncMock(side_effect=AssertionError(
        "set_project_archived must never call ensure_default_project — that "
        "is the silent-recreate bug this fix removes"
    ))
    monkeypatch.setattr(projects_repository, "ensure_default_project", mock)
    return mock


@pytest.mark.anyio
async def test_is_default_project_can_be_archived(monkeypatch) -> None:
    """The refusal is gone: archiving an is_default project succeeds like
    any other, with no ValueError."""
    _never_reassign_agents(monkeypatch)
    _never_ensure_default_project(monkeypatch)
    monkeypatch.setattr(
        control_plane_repository,
        "rls_fetchrow",
        AsyncMock(return_value=_project_row(project_id="proj-default", is_default=True, archived=True)),
    )

    result = await projects_repository.set_project_archived(
        tenant_id="tenant-1", workspace_id="ws-1", project_id="proj-default", archived=True,
    )

    assert result is not None
    assert result["archived"] is True
    assert result["is_default"] is True


@pytest.mark.anyio
async def test_is_default_project_can_be_unarchived(monkeypatch) -> None:
    """Symmetry check: restoring an is_default project works too — it was
    never blocked by the old guard (which only fired on archived=True), but
    this pins that un-archiving keeps working now that the archive side is
    unlocked as well."""
    _never_reassign_agents(monkeypatch)
    _never_ensure_default_project(monkeypatch)
    monkeypatch.setattr(
        control_plane_repository,
        "rls_fetchrow",
        AsyncMock(return_value=_project_row(project_id="proj-default", is_default=True, archived=False)),
    )

    result = await projects_repository.set_project_archived(
        tenant_id="tenant-1", workspace_id="ws-1", project_id="proj-default", archived=False,
    )

    assert result is not None
    assert result["archived"] is False


@pytest.mark.anyio
async def test_archiving_a_normal_project_does_not_reassign_its_agents(monkeypatch) -> None:
    """The load-bearing regression guard: archiving used to force-move the
    project's agents to a "default" project before flipping the flag. That
    call is gone — _never_reassign_agents/_never_ensure_default_project
    raise if either is attempted, so a passing test here proves neither
    runs any more, for a non-default project too (the bug was not
    is_default-specific — any project's archive could trigger it)."""
    _never_reassign_agents(monkeypatch)
    _never_ensure_default_project(monkeypatch)
    monkeypatch.setattr(
        control_plane_repository,
        "rls_fetchrow",
        AsyncMock(return_value=_project_row(project_id="proj-1", is_default=False, archived=True)),
    )

    result = await projects_repository.set_project_archived(
        tenant_id="tenant-1", workspace_id="ws-1", project_id="proj-1", archived=True,
    )

    assert result is not None
    assert result["archived"] is True


@pytest.mark.anyio
async def test_archiving_an_unknown_project_returns_none(monkeypatch) -> None:
    """No pre-check left to short-circuit on — the single UPDATE ...
    RETURNING matching zero rows is what reports "not found" now."""
    _never_reassign_agents(monkeypatch)
    _never_ensure_default_project(monkeypatch)
    monkeypatch.setattr(control_plane_repository, "rls_fetchrow", AsyncMock(return_value=None))

    result = await projects_repository.set_project_archived(
        tenant_id="tenant-1", workspace_id="ws-1", project_id="nope", archived=True,
    )

    assert result is None
