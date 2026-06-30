from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from server_modules.retention_enforcement_job import (
    RetentionRunResult,
    evaluate_retention,
    run_retention_dry_run,
    run_retention_apply,
    _is_expired,
)


def _run(coro):
    return asyncio.run(coro)


class RetentionEnforcementTests(unittest.TestCase):
    def test_is_expired_returns_false_for_none(self):
        self.assertFalse(_is_expired(None, 30))

    def test_is_expired_returns_false_for_future(self):
        self.assertFalse(_is_expired("2099-01-01T00:00:00Z", 30))

    def test_is_expired_returns_true_for_old_date(self):
        self.assertTrue(_is_expired("2020-01-01T00:00:00Z", 30))

    def test_dry_run_returns_retention_result(self):
        with patch(
            "server_modules.data_retention_service.build_workspace_retention_inventory",
            new=AsyncMock(return_value={"stores": []}),
        ), patch(
            "server_modules.retention_enforcement_job.security_audit_service.emit_security_audit_event",
        ):
            result = _run(run_retention_dry_run(workspace_id="ws-1"))

            self.assertIsInstance(result, RetentionRunResult)
            self.assertTrue(result.dry_run)
            self.assertEqual(result.workspace_id, "ws-1")
            self.assertTrue(len(result.started_at) > 0)
            self.assertTrue(len(result.completed_at) > 0)
            self.assertEqual(result.records_deleted, 0)

    def test_dry_run_does_not_delete(self):
        inventory = {
            "stores": [
                {
                    "store_id": "sage_memory",
                    "retention_policy": {"ttl_days": 90},
                    "row_count": 5,
                    "supports_delete": True,
                },
            ],
        }
        with patch(
            "server_modules.data_retention_service.build_workspace_retention_inventory",
            new=AsyncMock(return_value=inventory),
        ), patch(
            "server_modules.sage_memory_service.list_sage_memory",
            return_value={
                "items": [
                    {"id": "old-1", "created_at": "2020-01-01T00:00:00Z"},
                    {"id": "future-1", "created_at": "2099-01-01T00:00:00Z"},
                ]
            },
        ), patch(
            "server_modules.sage_memory_service.delete_memory_entry"
        ), patch(
            "server_modules.retention_enforcement_job.security_audit_service.emit_security_audit_event",
        ):
            result = _run(run_retention_dry_run(workspace_id="ws-1"))

            self.assertEqual(result.records_deleted, 0)
            self.assertEqual(result.records_eligible, 1)

    def test_apply_deletes_only_expired_sage_memory(self):
        inventory = {
            "stores": [
                {
                    "store_id": "sage_memory",
                    "retention_policy": {"ttl_days": 90},
                    "row_count": 2,
                    "supports_delete": True,
                },
            ],
        }
        with patch(
            "server_modules.data_retention_service.build_workspace_retention_inventory",
            new=AsyncMock(return_value=inventory),
        ), patch(
            "server_modules.sage_memory_service.list_sage_memory",
            return_value={
                "items": [
                    {"id": "old-1", "created_at": "2020-01-01T00:00:00Z"},
                    {"id": "future-1", "created_at": "2099-01-01T00:00:00Z"},
                ]
            },
        ), patch(
            "server_modules.sage_memory_service.delete_memory_entry",
        ) as delete_mock, patch(
            "server_modules.retention_enforcement_job.security_audit_service.emit_security_audit_event",
        ):
            result = _run(run_retention_apply(workspace_id="ws-1"))

            self.assertEqual(result.records_eligible, 1)
            self.assertEqual(result.records_deleted, 1)
            delete_mock.assert_called_once()

    def test_apply_mode_runs_without_error(self):
        with patch(
            "server_modules.data_retention_service.build_workspace_retention_inventory",
            new=AsyncMock(return_value={"stores": []}),
        ), patch(
            "server_modules.retention_enforcement_job.security_audit_service.emit_security_audit_event",
        ):
            result = _run(run_retention_apply(workspace_id="ws-1"))

            self.assertIsInstance(result, RetentionRunResult)
            self.assertFalse(result.dry_run)

    def test_inventory_failure_is_recorded(self):
        with patch(
            "server_modules.data_retention_service.build_workspace_retention_inventory",
            new=AsyncMock(side_effect=RuntimeError("inventory unavailable")),
        ), patch(
            "server_modules.retention_enforcement_job.security_audit_service.emit_security_audit_event",
        ):
            result = _run(evaluate_retention(workspace_id="ws-1", dry_run=True))

            self.assertTrue(len(result.errors) > 0)
            self.assertIn("inventory_failed", result.errors[0])

    def test_audit_event_emitted_on_completion(self):
        with patch(
            "server_modules.data_retention_service.build_workspace_retention_inventory",
            new=AsyncMock(return_value={"stores": []}),
        ), patch(
            "server_modules.retention_enforcement_job.security_audit_service.emit_security_audit_event",
        ) as mock_audit:
            _run(evaluate_retention(workspace_id="ws-1", dry_run=True))

            mock_audit.assert_called()
            kwargs = mock_audit.call_args.kwargs
            self.assertEqual(kwargs["action"], "retention.run_completed")

    def test_sage_memory_retention_supports_legacy_entries_shape(self):
        inventory = {
            "stores": [
                {
                    "store_id": "sage_memory",
                    "retention_policy": {"ttl_days": 90},
                    "row_count": 1,
                    "supports_delete": True,
                },
            ],
        }
        with patch(
            "server_modules.data_retention_service.build_workspace_retention_inventory",
            new=AsyncMock(return_value=inventory),
        ), patch(
            "server_modules.sage_memory_service.list_sage_memory",
            return_value={
                "entries": [
                    {"id": "old-legacy", "created_at": "2020-01-01T00:00:00Z"},
                ]
            },
        ), patch(
            "server_modules.sage_memory_service.delete_memory_entry",
        ) as delete_mock, patch(
            "server_modules.retention_enforcement_job.security_audit_service.emit_security_audit_event",
        ):
            result = _run(run_retention_apply(workspace_id="ws-1"))

            self.assertEqual(result.records_eligible, 1)
            self.assertEqual(result.records_deleted, 1)
            delete_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
