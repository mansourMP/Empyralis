"""Tests for bug_report_service.py -- the backing store for the "report a
bug" rail control (MAN-106, frontend/lib/workspace/fleet/BugReportButton.tsx).

Mirrors test_project_tasks.py's _QueuedFakePool harness: plain
pool.fetch/fetchrow/execute, queued per call.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from server_modules import bug_report_service


class _QueuedFakePool:
    def __init__(self, *, fetchrow_results=None, fetch_results=None):
        self._fetchrow_results = list(fetchrow_results or [])
        self._fetch_results = list(fetch_results or [])
        self.fetchrow_calls: list[tuple] = []
        self.fetch_calls: list[tuple] = []

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query, args))
        if not self._fetchrow_results:
            return None
        return self._fetchrow_results.pop(0)

    async def fetch(self, query, *args):
        self.fetch_calls.append((query, args))
        if not self._fetch_results:
            return []
        return self._fetch_results.pop(0)


def _report_row(**overrides) -> dict:
    row = {
        "id": "bugreport-1",
        "tenant_id": "tenant-1",
        "workspace_id": "ws-1",
        "reported_by_user_id": "user-1",
        "title": "Send button does nothing",
        "description": "Clicked send twice, nothing happened.",
        "page_path": "/w/ws-1/agents",
        "user_agent": "Mozilla/5.0",
        "status": "new",
        "metadata": {},
        "created_at": "2026-07-27T00:00:00Z",
    }
    row.update(overrides)
    return row


class CreateReportTests(unittest.IsolatedAsyncioTestCase):
    async def test_requires_title(self):
        with self.assertRaises(ValueError):
            await bug_report_service.create_report(
                tenant_id="tenant-1", workspace_id="ws-1", title="   ",
            )

    async def test_requires_tenant_and_workspace(self):
        with self.assertRaises(ValueError):
            await bug_report_service.create_report(
                tenant_id="", workspace_id="ws-1", title="Something broke",
            )

    async def test_without_postgres_raises_durable_config_error(self):
        with patch(
            "server_modules.bug_report_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaises(
                bug_report_service.control_plane_repository.runtime_db.DurableRuntimeConfigurationError
            ):
                await bug_report_service.create_report(
                    tenant_id="tenant-1", workspace_id="ws-1", title="Something broke",
                )

    async def test_inserts_and_returns_row(self):
        pool = _QueuedFakePool(fetchrow_results=[_report_row()])
        with patch(
            "server_modules.bug_report_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            report = await bug_report_service.create_report(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                title="Send button does nothing",
                description="Clicked send twice, nothing happened.",
                reported_by_user_id="user-1",
                page_path="/w/ws-1/agents",
                user_agent="Mozilla/5.0",
            )

        self.assertEqual(report["id"], "bugreport-1")
        self.assertEqual(report["status"], "new")
        self.assertEqual(report["title"], "Send button does nothing")
        self.assertEqual(len(pool.fetchrow_calls), 1)
        query, args = pool.fetchrow_calls[0]
        self.assertIn("INSERT INTO bug_reports", query)
        self.assertEqual(args[1], "tenant-1")
        self.assertEqual(args[2], "ws-1")
        self.assertEqual(args[4], "Send button does nothing")

    async def test_title_and_description_are_truncated(self):
        pool = _QueuedFakePool(fetchrow_results=[_report_row()])
        long_title = "x" * 1000
        long_description = "y" * 10000
        with patch(
            "server_modules.bug_report_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            await bug_report_service.create_report(
                tenant_id="tenant-1", workspace_id="ws-1", title=long_title, description=long_description,
            )
        _, args = pool.fetchrow_calls[0]
        self.assertEqual(len(args[4]), bug_report_service.MAX_TITLE_LENGTH)
        self.assertEqual(len(args[5]), bug_report_service.MAX_DESCRIPTION_LENGTH)

    async def test_blank_optional_fields_default_to_none_or_empty(self):
        pool = _QueuedFakePool(fetchrow_results=[_report_row(reported_by_user_id=None, page_path="", user_agent="")])
        with patch(
            "server_modules.bug_report_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            report = await bug_report_service.create_report(
                tenant_id="tenant-1", workspace_id="ws-1", title="Something broke",
            )
        self.assertIsNone(report["reported_by_user_id"])
        _, args = pool.fetchrow_calls[0]
        self.assertIsNone(args[3])  # reported_by_user_id


class ListReportsTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_postgres_returns_empty_list(self):
        with patch(
            "server_modules.bug_report_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=None),
        ):
            reports = await bug_report_service.list_reports(tenant_id="tenant-1", workspace_id="ws-1")
        self.assertEqual(reports, [])

    async def test_scopes_by_tenant_and_workspace_and_orders_newest_first(self):
        pool = _QueuedFakePool(fetch_results=[[_report_row(), _report_row(id="bugreport-2")]])
        with patch(
            "server_modules.bug_report_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            reports = await bug_report_service.list_reports(tenant_id="tenant-1", workspace_id="ws-1")

        self.assertEqual(len(reports), 2)
        query, args = pool.fetch_calls[0]
        self.assertIn("WHERE tenant_id = $1 AND workspace_id = $2", query)
        self.assertIn("ORDER BY created_at DESC", query)
        self.assertEqual(args[:2], ("tenant-1", "ws-1"))

    async def test_limit_is_capped(self):
        pool = _QueuedFakePool(fetch_results=[[]])
        with patch(
            "server_modules.bug_report_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            await bug_report_service.list_reports(tenant_id="tenant-1", workspace_id="ws-1", limit=9999)
        _, args = pool.fetch_calls[0]
        self.assertEqual(args[2], 500)


if __name__ == "__main__":
    unittest.main()
