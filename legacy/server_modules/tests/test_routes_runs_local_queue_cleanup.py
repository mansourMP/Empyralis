import unittest
from unittest.mock import AsyncMock, patch

from server_modules import routes_runs
from server_modules.runtime_models import LocalQueueCleanupRequest


class RoutesRunsLocalQueueCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_cleanup_local_run_queue_routes_workspace_scoped_request(self) -> None:
        body = LocalQueueCleanupRequest(
            workspace_id="default",
            older_than_seconds=900,
            limit=25,
            dry_run=False,
            reason="demo reset",
        )

        with (
            patch.object(routes_runs, "enforce_workspace_access", return_value="default") as workspace_mock,
            patch.object(routes_runs.core, "cleanup_local_run_queue", AsyncMock(return_value={"ok": True})) as cleanup_mock,
        ):
            payload = await routes_runs.cleanup_local_run_queue(
                body,
                current_user={"user_id": "user-1"},
            )

        self.assertEqual(payload, {"ok": True})
        workspace_mock.assert_called_once_with(
            {"user_id": "user-1"},
            "default",
            minimum_role="member",
        )
        cleanup_mock.assert_awaited_once_with(
            workspace_id="default",
            older_than_seconds=900,
            limit=25,
            dry_run=False,
            reason="demo reset",
        )

