"""MAN-149 ("Activation, not acquisition") -- the cross-tenant activation
snapshot behind the new operator route.

The founder had to SSH into production Postgres to learn there are 105
users and 5 documents. This module builds that same snapshot as a plain
`SELECT count(*)`-shaped query, and the ONE thing that must never happen is
the trap CLAUDE.md names for this exact class of bug: `workspace_agent_
installs`/`project_tasks`/etc. all carry FORCE ROW LEVEL SECURITY, so a
query run without `SET LOCAL app.rls_bypass = 'on'` does not error -- it
silently matches the empty set and returns a confident, wrong 0 for every
table. `control_plane_repository.rls_fetchrow(..., bypass_rls=True)` is the
documented fix, and the query itself reads `current_setting('app.rls_
bypass', true)` back as its own canary, so a future edit that drops
`bypass_rls=True` fails LOUDLY (an exception) instead of silently reporting
zero users.
"""

from __future__ import annotations

import inspect
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import platform_activation_service


def _row(**overrides):
    base = {
        "rls_bypass_scope": "on",
        "total_users": 105,
        "total_workspaces": 105,
        "total_agents": 68,
        "total_projects": 166,
        "total_tasks": 36,
        "total_documents": 5,
        "users_with_activity": 4,
        "workspaces_with_multiple_members": 0,
        "signups_last_7_days": 2,
        "signups_last_30_days": 9,
        "agents_with_runs": 12,
    }
    base.update(overrides)
    return base


def _patched_fetchrow(return_value):
    return patch.object(
        platform_activation_service.control_plane_repository,
        "rls_fetchrow",
        new=AsyncMock(return_value=return_value),
    )


class PlatformActivationSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_reports_totals_and_derived_activation_numbers(self) -> None:
        with _patched_fetchrow(_row()) as mock_fetch:
            snapshot = await platform_activation_service.build_platform_activation_snapshot(
                pool=object()
            )

        mock_fetch.assert_awaited_once()
        assert mock_fetch.await_args.kwargs.get("bypass_rls") is True

        assert snapshot["rls_bypass_verified"] is True
        assert snapshot["totals"] == {
            "users": 105,
            "workspaces": 105,
            "agents": 68,
            "projects": 166,
            "tasks": 36,
            "documents": 5,
        }
        assert snapshot["activation"]["users_with_activity"] == 4
        assert snapshot["activation"]["users_with_activity_pct"] == round(4 / 105 * 100, 1)
        assert snapshot["activation"]["workspaces_with_multiple_members"] == 0
        assert snapshot["activation"]["workspaces_with_multiple_members_pct"] == 0.0
        assert snapshot["signups"] == {"last_7_days": 2, "last_30_days": 9}
        assert snapshot["agents_runtime"] == {"total": 68, "with_runs": 12, "never_run": 56}

    async def test_raises_instead_of_returning_zeros_when_the_bypass_canary_is_off(self) -> None:
        """The exact regression this module exists to prevent: an RLS-
        scoped connection returning honest-looking zeros for every table.
        The canary must turn that into a loud failure, never a 200 full of
        0s."""
        broken_row = _row(
            rls_bypass_scope="off",
            total_users=0,
            total_workspaces=0,
            total_agents=0,
            total_projects=0,
            total_tasks=0,
            total_documents=0,
        )
        with _patched_fetchrow(broken_row):
            with self.assertRaises(platform_activation_service.PlatformActivationScopeBroken):
                await platform_activation_service.build_platform_activation_snapshot(pool=object())

    async def test_raises_when_the_canary_setting_is_entirely_unset(self) -> None:
        with _patched_fetchrow(_row(rls_bypass_scope=None)):
            with self.assertRaises(platform_activation_service.PlatformActivationScopeBroken):
                await platform_activation_service.build_platform_activation_snapshot(pool=object())

    async def test_raises_rather_than_fabricating_a_payload_when_no_row_comes_back(self) -> None:
        with _patched_fetchrow(None):
            with self.assertRaises(platform_activation_service.PlatformActivationScopeBroken):
                await platform_activation_service.build_platform_activation_snapshot(pool=object())

    async def test_never_run_agent_count_cannot_go_negative(self) -> None:
        with _patched_fetchrow(_row(total_agents=5, agents_with_runs=9)):
            snapshot = await platform_activation_service.build_platform_activation_snapshot(
                pool=object()
            )
        assert snapshot["agents_runtime"]["never_run"] == 0

    def test_the_query_bypasses_rls_exactly_once_and_reads_the_canary_back(self) -> None:
        """Structural guard, same convention as test_master_agent_isolation_
        backfill.py's own cross-tenant-read test: the ONE read that spans
        every tenant must carry bypass_rls=True, and the SQL itself must
        read the GUC back so a future refactor that silently drops the
        kwarg still gets caught at run time, not just by this grep."""
        source = inspect.getsource(platform_activation_service.build_platform_activation_snapshot)
        assert source.count("bypass_rls=True") == 1
        assert "current_setting('app.rls_bypass'" in platform_activation_service.PLATFORM_ACTIVATION_SNAPSHOT_SQL


if __name__ == "__main__":
    unittest.main()
