import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from server_modules import shared_operational_board_service, memory_service, workspace_context


class SharedOperationalBoardServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="shared-board-service-")
        self.addCleanup(self._tmpdir.cleanup)
        tmp_root = Path(self._tmpdir.name)
        self._workspace_root = tmp_root / "workspace"
        self._workspace_root.mkdir(parents=True, exist_ok=True)
        self._memory_root = tmp_root / "runtime-memory"
        self._workspace_patch = patch.object(workspace_context, "_WORKSPACE_DIR", self._workspace_root)
        self._memory_patch = patch.object(memory_service._workspace_memory_store, "_MEMORY_DIR", self._memory_root)
        self._semantic_model_patch = patch.object(memory_service._workspace_memory_store, "_SEMANTIC_MODEL", False)
        self._workspace_patch.start()
        self._memory_patch.start()
        self._semantic_model_patch.start()
        self.addCleanup(self._workspace_patch.stop)
        self.addCleanup(self._memory_patch.stop)
        self.addCleanup(self._semantic_model_patch.stop)

    def test_write_shared_board_entry_rejects_publish_without_permission(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(
                shared_operational_board_service.write_shared_operational_board_entry(
                    "default",
                    tenant_id="tenant-1",
                    actor_permission="propose_update",
                    action="publish_update",
                    actor={"type": "user", "id": "user-1", "role": "member"},
                    entry_kind="shared_instruction",
                    title="Escalation rule",
                    body="Always escalate urgent billing tickets.",
                )
            )

        self.assertEqual(ctx.exception.status_code, 403)

    def test_propose_then_publish_keeps_published_reads_clean_and_history_versioned(self) -> None:
        with patch(
            "server_modules.shared_operational_board_service.activity_ledger_service.append_activity_event",
            new=AsyncMock(side_effect=[{"id": "aevt-1"}, {"id": "aevt-2"}]),
        ) as append_mock:
            proposed = asyncio.run(
                shared_operational_board_service.write_shared_operational_board_entry(
                    "default",
                    tenant_id="tenant-1",
                    actor_permission="propose_update",
                    action="propose_update",
                    actor={"type": "user", "id": "user-1", "role": "member"},
                    entry_kind="shared_instruction",
                    title="Escalation rule",
                    body="Always escalate urgent billing tickets.",
                )
            )
            published = asyncio.run(
                shared_operational_board_service.write_shared_operational_board_entry(
                    "default",
                    tenant_id="tenant-1",
                    actor_permission="publish_update",
                    action="publish_update",
                    actor={"type": "user", "id": "owner-1", "role": "owner"},
                    entry_id=proposed["entry"]["entry_id"],
                    entry_kind="shared_instruction",
                    title="Escalation rule",
                    body="Escalate urgent billing tickets within 10 minutes.",
                )
            )

        published_only = shared_operational_board_service.list_shared_operational_board_entries(
            "default",
            actor_permission="read",
        )
        history = shared_operational_board_service.get_shared_operational_board_history(
            "default",
            proposed["entry"]["entry_id"],
            actor_permission="publish_update",
            include_proposed=True,
        )

        self.assertEqual(len(published_only), 1)
        self.assertEqual(published_only[0]["revision_number"], 2)
        self.assertEqual(published_only[0]["status"], "published")
        self.assertIn("10 minutes", published_only[0]["body"])
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["status"], "published")
        self.assertEqual(history[1]["status"], "proposed")
        self.assertEqual(proposed["activity_event_id"], "aevt-1")
        self.assertEqual(published["activity_event_id"], "aevt-2")
        self.assertEqual(append_mock.await_count, 2)

    def test_shared_board_rejects_private_memory_keys(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(
                shared_operational_board_service.write_shared_operational_board_entry(
                    "default",
                    tenant_id="tenant-1",
                    actor_permission="publish_update",
                    action="publish_update",
                    actor={"type": "user", "id": "owner-1", "role": "owner"},
                    entry_kind="handoff_note",
                    title="Do not leak",
                    body="No raw memory here.",
                    metadata={"captain_context": {"raw": True}},
                )
            )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("captain_context", str(ctx.exception.detail))


if __name__ == "__main__":
    unittest.main()
