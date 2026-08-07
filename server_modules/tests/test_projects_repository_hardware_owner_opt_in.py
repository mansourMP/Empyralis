"""'Hardware attaches to its owner, never to the project' (CLAUDE.md).

projects_repository.set_project_default_gateway used to validate a
gateway_id only against fleet_tools.gateway_resolves_in_workspace --
"is this a real, active Gateway registered to this workspace" -- with no
check that the machine's OWNER ever consented to a PROJECT using it. Any
workspace owner-role member could point a project's agents at any other
member's paired Mac, silently.

The fix adds a second gate, gateway_state_repository.
gateway_project_sharing_opted_in, that must ALSO pass. These tests exercise
set_project_default_gateway directly against a mocked control_plane_
repository, DB-free -- same convention
test_projects_repository_member_grant_logging_man299.py already established
for this module (control_plane_repository is monkeypatched rather than
requiring real Postgres, since this suite only needs to prove set_project_
default_gateway's OWN validation-branching behavior, not exercise a real
UPDATE). This is a NEW file (see docs/AGENT-OPERATING-RULES.md's collision
protocol -- most pre-existing test files in this directory belong to other
concurrently-running agents).

The resolution-time re-check (a stored default_gateway_id must not be
grandfathered into access if the flag was never true, or was later
revoked) is covered separately in
test_specialist_runtime_context.py::GatewayOwnerOptInGateTests -- that is
the path that actually hands hardware to a turn, and it re-checks the same
gate live on every resolution rather than trusting whatever passed here at
set time.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from server_modules import control_plane_repository
from server_modules import fleet_tools
from server_modules import gateway_state_repository
from server_modules import projects_repository


class _FakePool:
    """Never actually queried -- every control_plane_repository call this
    suite exercises is monkeypatched below. Just needs to be a non-None
    sentinel so `if pool is None: raise ...` doesn't trip."""


def _fake_project_row(*, gateway_id: str) -> dict:
    return {
        "id": "project_test123",
        "tenant_id": "tenant-x",
        "workspace_id": "ws-x",
        "name": "Test Project",
        "slug": "test-project",
        "description": "",
        "is_default": False,
        "archived": False,
        "metadata": {"default_gateway_id": gateway_id} if gateway_id else {},
        "created_at": "2026-08-07T00:00:00Z",
        "updated_at": "2026-08-07T00:00:00Z",
    }


@pytest.fixture(autouse=True)
def _mock_control_plane(monkeypatch: pytest.MonkeyPatch):
    pool = _FakePool()
    monkeypatch.setattr(
        control_plane_repository, "ensure_control_plane_schema", AsyncMock(return_value=pool)
    )


async def _set_default_gateway(gateway_id: str, *, row_gateway_id: str | None = None):
    """row_gateway_id defaults to gateway_id -- the row the (mocked) UPDATE
    ... RETURNING would produce on success. Only used when the call is
    expected to actually reach the UPDATE."""
    import server_modules.control_plane_repository as cpr

    row = _fake_project_row(gateway_id=row_gateway_id if row_gateway_id is not None else gateway_id)

    async def _fake_rls_fetchrow(*_args, **_kwargs):
        return row

    import unittest.mock as mock
    with mock.patch.object(cpr, "rls_fetchrow", AsyncMock(side_effect=_fake_rls_fetchrow)):
        return await projects_repository.set_project_default_gateway(
            tenant_id="tenant-x", workspace_id="ws-x", project_id="project_test123", gateway_id=gateway_id,
        )


@pytest.mark.anyio
async def test_opted_in_gateway_can_be_set_as_project_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(fleet_tools, "gateway_resolves_in_workspace", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(gateway_state_repository, "gateway_project_sharing_opted_in", lambda *a, **k: True)

    project = await _set_default_gateway("gw-opted-in")

    assert project is not None
    assert project["default_gateway_id"] == "gw-opted-in"


@pytest.mark.anyio
async def test_un_opted_in_gateway_cannot_be_set_as_project_default(monkeypatch: pytest.MonkeyPatch):
    """The core fix: a gateway that resolves fine to the workspace (it's a
    real, active, non-revoked registration) must still be REJECTED here if
    its owner never opted it into project sharing."""
    monkeypatch.setattr(fleet_tools, "gateway_resolves_in_workspace", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(gateway_state_repository, "gateway_project_sharing_opted_in", lambda *a, **k: False)

    with pytest.raises(ValueError, match="hasn't shared it"):
        await _set_default_gateway("gw-not-opted-in")


@pytest.mark.anyio
async def test_non_resolving_gateway_is_still_rejected_before_the_opt_in_check(monkeypatch: pytest.MonkeyPatch):
    """Sanity check: the pre-existing workspace-resolution check still runs,
    and still runs FIRST -- a gateway from another workspace entirely (or a
    revoked one) is rejected on that basis, and the opt-in check is never
    even reached for it."""
    monkeypatch.setattr(
        fleet_tools, "gateway_resolves_in_workspace",
        lambda *a, **k: {"ok": False, "error": "gateway_binding 'gw-x' does not resolve to a Gateway paired to this workspace."},
    )
    opted_in_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(gateway_state_repository, "gateway_project_sharing_opted_in", opted_in_mock)

    with pytest.raises(ValueError, match="does not resolve"):
        await _set_default_gateway("gw-x")

    opted_in_mock.assert_not_called()


@pytest.mark.anyio
async def test_clearing_the_default_never_needs_opt_in(monkeypatch: pytest.MonkeyPatch):
    """Clearing (empty string / None) is always safe, per set_project_
    default_gateway's own pre-existing docstring -- no resolution check, no
    opt-in check, ever, for a clear."""
    opted_in_mock = AsyncMock(return_value=False)
    monkeypatch.setattr(gateway_state_repository, "gateway_project_sharing_opted_in", opted_in_mock)
    resolves_mock = AsyncMock(return_value={"ok": False, "error": "should never be called"})
    monkeypatch.setattr(fleet_tools, "gateway_resolves_in_workspace", resolves_mock)

    project = await _set_default_gateway("", row_gateway_id="")

    assert project is not None
    assert project["default_gateway_id"] is None
    opted_in_mock.assert_not_called()
    resolves_mock.assert_not_called()
