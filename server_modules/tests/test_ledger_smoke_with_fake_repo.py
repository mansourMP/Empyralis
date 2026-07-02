"""Smoke test: tool-call ledger write path with in-memory fake repo.

Verifies that the full activity_ledger_service → control_plane_repository
write path completes end-to-end when using an in-memory fake, without
needing a real Postgres connection.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

from server_modules import activity_ledger_service
from server_modules.tests.fake_activity_ledger_repo import FakeActivityLedgerRepo


class ToolCallLedgerSmokeTests(unittest.TestCase):
    """Smoke: inject FakeActivityLedgerRepo, exercise ledger write, inspect record."""

    def setUp(self) -> None:
        self.fake = FakeActivityLedgerRepo()
        # Patch the three ledger functions activity_ledger_service uses.
        self._append_patcher = patch(
            "server_modules.activity_ledger_service.control_plane_repository.append_activity_ledger_event",
            new=self.fake.append_activity_ledger_event,
        )
        self._list_patcher = patch(
            "server_modules.activity_ledger_service.control_plane_repository.list_activity_ledger_events",
            new=self.fake.list_activity_ledger_events,
        )
        self._get_patcher = patch(
            "server_modules.activity_ledger_service.control_plane_repository.get_activity_ledger_event",
            new=self.fake.get_activity_ledger_event,
        )
        self._append_patcher.start()
        self._list_patcher.start()
        self._get_patcher.start()

    def tearDown(self) -> None:
        self._append_patcher.stop()
        self._list_patcher.stop()
        self._get_patcher.stop()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _run(self, coro):
        return asyncio.run(coro)

    def _ledger_dump(self) -> str:
        events = list(self.fake.events.values())
        return json.dumps(events, indent=2, default=str, ensure_ascii=False)

    # ------------------------------------------------------------------
    # Smoke: tool invocation ledger record
    # ------------------------------------------------------------------

    def test_tool_invoke_writes_ledger_record_with_agent_id_tool_and_ts(self):
        """A simulated tool call writes a complete ledger record through the fake.

        Fields visible: agent_id, tool (action), result_status, ts.
        """
        record = self._run(
            activity_ledger_service.append_activity_event(
                tenant_id="tenant-smoke",
                workspace_id="workspace-smoke",
                actor_type="agent",
                actor_id="agent-sage-001",
                event_class="sage_activity",
                detail_level="timeline_detail",
                run_id="run-smoke-42",
                thread_id="thread-smoke-1",
                channel="studio",
                direction="outbound",
                action="shell__exec",
                title="Tool: shell__exec",
                summary="Executed shell command: echo hello",
                status="completed",
                trace_id="trace-smoke-aaa",
                payload={
                    "tool": "shell__exec",
                    "input": {"command": "echo hello"},
                    "result": {"stdout": "hello\n", "exit_code": 0},
                },
            )
        )

        # ------------------------------------------------------------------
        # Assert the record is complete
        # ------------------------------------------------------------------
        self.assertIsNotNone(record)
        assert record is not None  # type narrow
        self.assertEqual(record["actor_id"], "agent-sage-001")
        self.assertEqual(record["actor_type"], "agent")
        self.assertEqual(record["action"], "shell__exec")
        self.assertEqual(record["status"], "completed")
        self.assertEqual(record["run_id"], "run-smoke-42")
        self.assertEqual(record["workspace_id"], "workspace-smoke")
        self.assertIn("created_at", record)
        self.assertIn("id", record)
        self.assertTrue(record["id"].startswith("aevt_"))

        # Verify the record is retrievable via the fake.
        fetched = self._run(
            activity_ledger_service.get_notification_feed_item(
                tenant_id="tenant-smoke",
                workspace_id="workspace-smoke",
                notification_id=record["id"],
            )
        )
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["action"], "shell__exec")

        # Dump the actual ledger record for inspection.
        print("\n--- LEDGER RECORD (tool-call smoke) ---")
        print(self._ledger_dump())
        print("--- END LEDGER RECORD ---\n")

    def test_ledger_denial_recorded_for_blocked_tool(self):
        """A blocked tool call still writes a ledger denial record."""
        record = self._run(
            activity_ledger_service.append_activity_event(
                tenant_id="tenant-smoke",
                workspace_id="workspace-smoke",
                actor_type="agent",
                actor_id="agent-sage-001",
                event_class="blocked_action",
                detail_level="timeline_detail",
                run_id="run-smoke-43",
                channel="studio",
                action="shell__exec",
                title="Blocked: shell__exec",
                summary="Policy denied shell execution",
                status="blocked",
                trace_id="trace-smoke-bbb",
                metadata={"denial_reason": "policy_engine", "policy": "no_shell"},
            )
        )
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record["status"], "blocked")
        self.assertEqual(record["event_class"], "blocked_action")
        self.assertEqual(record["actor_id"], "agent-sage-001")

        print("\n--- LEDGER RECORD (denial) ---")
        print(self._ledger_dump())
        print("--- END LEDGER RECORD ---\n")
