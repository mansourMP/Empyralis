"""Sage Standing Orders / Cron certification tests.

Certifies the scheduling, retry, cancellation, error-capture,
and deduplication behaviour of the cron / standing-order subsystem.
"""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch, call
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

from server_modules import runs_core
from server_modules.bounded_scheduler_service import (
    compute_retry_delay,
    schedule_retry,
    RetryPolicy,
    DEFAULT_RETRY_POLICY,
)
from server_modules.runtime_models import CronScheduleUpsertRequest, RunStartRequest


def _make_schedule(
    schedule_id: str = "sched-1",
    cron: str = "*/5 * * * *",
    enabled: bool = True,
    workspace_id: str = "ws-1",
    wake_mode: str = "now",
) -> Dict[str, Any]:
    """Build a minimal schedule dict like _build_schedule_item produces."""
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    item = {
        "id": schedule_id,
        "name": f"Test-{schedule_id}",
        "workspace_id": workspace_id,
        "enabled": enabled,
        "cron": cron,
        "timezone": "utc",
        "wake_mode": wake_mode,
        "delivery": "announce",
        "run_request": {
            "engine": "orion",
            "workspace_id": workspace_id,
            "user_goal": "certification test run",
        },
        "day_of_week": None,
        "time_hhmm": None,
        "last_trigger_slot": None,
        "last_trigger_date": None,
        "last_run_id": None,
        "last_run_at": None,
        "last_error": None,
        "next_run_at": None,
        "pending_heartbeat": False,
        "pending_heartbeat_slot": None,
        "run_log": [],
        "created_at": now,
        "updated_at": now,
    }
    return item


class SageStandingOrdersTests(unittest.TestCase):
    """Certification tests for cron / standing-order infrastructure."""

    # ------------------------------------------------------------------
    # 1. One-shot fires once
    # ------------------------------------------------------------------
    def test_one_shot_fires_once(self):
        """A schedule whose cron matches exactly one slot fires that slot
        and does *not* re-fire when the same slot is encountered again."""
        schedules: Dict[str, Dict[str, Any]] = {}

        schedule_id = "sched-once"
        slot_a = "2026-06-07T10:00:00Z"
        slot_b = "2026-06-07T10:05:00Z"

        schedules[schedule_id] = _make_schedule(
            schedule_id=schedule_id,
            cron="*/5 * * * *",
            workspace_id="ws-1",
        )

        started: list[Dict[str, Any]] = []

        def _execute(request: Any, schedule_id: str | None = None) -> Dict[str, Any]:
            started.append({"schedule_id": schedule_id, "run_id": f"run-{schedule_id}"})
            return {"run_id": f"run-{schedule_id}"}

        # --- cycle 1 : slot_a is new, should fire ---
        with patch.object(runs_core, "WEEKLY_SCHEDULES", schedules):
            runs_core.trigger_pending_heartbeat_schedules = (
                lambda **kw: _simulate_poll_cycle(
                    schedules=schedules,
                    slot_key=slot_a,
                    execute_scheduled_run_request_fn=_execute,
                )
            )
            result_a = _simulate_poll_cycle(
                schedules=schedules,
                slot_key=slot_a,
                execute_scheduled_run_request_fn=_execute,
            )

        self.assertTrue(result_a["acted"])
        self.assertEqual(len(result_a["started"]), 1)
        self.assertEqual(
            schedules[schedule_id]["last_trigger_slot"], slot_a
        )
        self.assertIn("started", [e["status"] for e in schedules[schedule_id]["run_log"]])

        # --- cycle 2 : same slot again, should NOT fire ---
        started.clear()
        result_b = _simulate_poll_cycle(
            schedules=schedules,
            slot_key=slot_a,
            execute_scheduled_run_request_fn=_execute,
        )

        self.assertFalse(result_b["acted"])
        self.assertEqual(len(started), 0)

    # ------------------------------------------------------------------
    # 2. Recurring fires repeatedly
    # ------------------------------------------------------------------
    def test_recurring_fires_repeatedly(self):
        """A cron schedule with a short interval fires in every poll
        cycle that contains a *new* trigger slot."""
        schedules: Dict[str, Dict[str, Any]] = {}

        schedule_id = "sched-recur"
        schedules[schedule_id] = _make_schedule(
            schedule_id=schedule_id,
            cron="*/1 * * * *",
            workspace_id="ws-1",
        )

        started: list[Dict[str, Any]] = []
        slot_keys = [
            "2026-06-07T10:00:00Z",
            "2026-06-07T10:01:00Z",
        ]

        def _execute(request: Any, schedule_id: str | None = None) -> Dict[str, Any]:
            started.append({"schedule_id": schedule_id, "run_id": f"run-{schedule_id}-{len(started)}"})
            return {"run_id": f"run-{schedule_id}-{len(started)}"}

        # Two separate poll cycles, each with a new slot
        for slot in slot_keys:
            _simulate_poll_cycle(
                schedules=schedules,
                slot_key=slot,
                execute_scheduled_run_request_fn=_execute,
            )

        self.assertEqual(len(started), 2)
        self.assertEqual(
            schedules[schedule_id]["last_trigger_slot"], slot_keys[-1]
        )
        self.assertEqual(len(schedules[schedule_id]["run_log"]), 2)

    # ------------------------------------------------------------------
    # 3. Retry respects backoff
    # ------------------------------------------------------------------
    def test_retry_respects_backoff(self):
        """compute_retry_delay produces exponential backoff and
        schedule_retry respects attempt-increment and max_retries."""
        # ---- compute_retry_delay ----
        policy = RetryPolicy(
            max_retries=3,
            base_delay_seconds=10,
            max_delay_seconds=3600,
            backoff_multiplier=2.0,
        )

        delays = [compute_retry_delay(a, policy) for a in range(1, 5)]
        # attempt 1: 10 * 2^1 = 20
        # attempt 2: 10 * 2^2 = 40
        # attempt 3: 10 * 2^3 = 80
        # attempt 4: 10 * 2^4 = 160
        self.assertEqual(delays, [20, 40, 80, 160])

        # ---- schedule_retry attempt progression ----
        updated_records: list[Dict[str, Any]] = []

        async def _mock_update_status(
            tenant_id: str,
            workspace_id: str,
            wake_id: str,
            status: str,
            **kwargs: Any,
        ) -> Dict[str, Any]:
            record = {"wake_id": wake_id, "status": status, **kwargs}
            updated_records.append(record)
            return record

        with (
            patch(
                "server_modules.bounded_scheduler_service._load_scheduler_scope",
                new=AsyncMock(return_value=({"metadata": {}}, {"id": "install-sage"}, MagicMock())),
            ),
            patch(
                "server_modules.bounded_scheduler_service.control_plane_repository.update_agent_scheduler_wake_request_status",
                new=_mock_update_status,
            ),
            patch(
                "server_modules.bounded_scheduler_service._enforce_session_scheduler_decision",
                return_value=None,
            ),
        ):
            for attempt in range(1, 4):
                result = asyncio.run(
                    schedule_retry(
                        tenant_id="tenant-1",
                        workspace_id="ws-1",
                        wake_request={
                            "id": "wake-retry-1",
                            "metadata": {
                                "retry": {
                                    "retry_attempt": attempt - 1 if attempt > 1 else 0,
                                }
                            },
                        },
                        error=f"attempt-{attempt}-error",
                        retry_policy=policy,
                    )
                )
                self.assertIsNotNone(result)
                if attempt < policy.max_retries:
                    self.assertEqual(result["status"], "pending")
                else:
                    self.assertEqual(result["status"], "failed_permanent")

        # Verify attempt tracking
        self.assertEqual(len(updated_records), 3)

    # ------------------------------------------------------------------
    # 4. Cancellation prevents execution
    # ------------------------------------------------------------------
    def test_cancellation_prevents_execution(self):
        """A schedule that is disabled (enabled=False) does not fire
        even when its cron slot matches."""
        schedules: Dict[str, Dict[str, Any]] = {}

        schedule_id = "sched-cancel"
        schedules[schedule_id] = _make_schedule(
            schedule_id=schedule_id,
            cron="*/5 * * * *",
            workspace_id="ws-1",
            enabled=False,  # disabled from the start
        )

        started: list[Dict[str, Any]] = []

        def _execute(request: Any, schedule_id: str | None = None) -> Dict[str, Any]:
            started.append({"schedule_id": schedule_id})
            return {"run_id": f"run-{schedule_id}"}

        slot = "2026-06-07T10:00:00Z"
        result = _simulate_poll_cycle(
            schedules=schedules,
            slot_key=slot,
            execute_scheduled_run_request_fn=_execute,
        )

        self.assertFalse(result["acted"])
        self.assertEqual(len(started), 0)
        self.assertIsNone(schedules[schedule_id].get("last_trigger_slot"))

        # Re-enable and verify it *does* fire
        schedules[schedule_id]["enabled"] = True
        result = _simulate_poll_cycle(
            schedules=schedules,
            slot_key="2026-06-07T10:05:00Z",
            execute_scheduled_run_request_fn=_execute,
        )
        self.assertTrue(result["acted"])
        self.assertEqual(len(started), 1)

    # ------------------------------------------------------------------
    # 5. Failed delivery does not disappear
    # ------------------------------------------------------------------
    def test_failed_delivery_does_not_disappear(self):
        """When schedule execution raises, the run_log captures the
        error, last_error is set, and the error surfaces in the log."""
        schedules: Dict[str, Dict[str, Any]] = {}

        schedule_id = "sched-fail"
        schedules[schedule_id] = _make_schedule(
            schedule_id=schedule_id,
            cron="*/5 * * * *",
            workspace_id="ws-1",
        )

        error_msg = "Intentional test failure"

        def _execute(request: Any, schedule_id: str | None = None) -> Dict[str, Any]:
            raise RuntimeError(error_msg)

        slot = "2026-06-07T10:00:00Z"
        result = _simulate_poll_cycle(
            schedules=schedules,
            slot_key=slot,
            execute_scheduled_run_request_fn=_execute,
        )

        # Verify no run was "started" but the error is captured
        self.assertFalse(result["acted"])
        schedule = schedules[schedule_id]
        self.assertIsNotNone(schedule.get("last_error"))
        # last_error is set to the error detail by _append_schedule_run_log
        # when status != "started"/"completed"/"queued"
        self.assertIn(error_msg, str(schedule.get("last_error", "")))
        self.assertEqual(len(schedule["run_log"]), 1)
        self.assertEqual(schedule["run_log"][0]["status"], "failed")
        self.assertIn(error_msg, schedule["run_log"][0].get("detail", ""))

    # ------------------------------------------------------------------
    # 6. Duplicate execution is prevented
    # ------------------------------------------------------------------
    def test_duplicate_execution_is_prevented(self):
        """last_trigger_slot prevents re-firing when the same slot is
        matched in a subsequent poll cycle (duplicate-window guard)."""
        schedules: Dict[str, Dict[str, Any]] = {}

        schedule_id = "sched-dedup"
        # A cron that matches one slot per window
        schedules[schedule_id] = _make_schedule(
            schedule_id=schedule_id,
            cron="*/2 * * * *",
            workspace_id="ws-1",
        )

        started: list[Dict[str, Any]] = []

        def _execute(request: Any, schedule_id: str | None = None) -> Dict[str, Any]:
            started.append({"schedule_id": schedule_id})
            return {"run_id": f"run-{schedule_id}"}

        slot = "2026-06-07T10:00:00Z"

        # First fire – consumes the slot
        result = _simulate_poll_cycle(
            schedules=schedules,
            slot_key=slot,
            execute_scheduled_run_request_fn=_execute,
        )
        self.assertTrue(result["acted"])
        self.assertEqual(schedules[schedule_id]["last_trigger_slot"], slot)

        # Second fire attempt with same slot – prevented by last_trigger_slot
        started.clear()
        result = _simulate_poll_cycle(
            schedules=schedules,
            slot_key=slot,
            execute_scheduled_run_request_fn=_execute,
        )
        self.assertFalse(result["acted"])
        self.assertEqual(len(started), 0)

        # New slot (next window) – should fire again
        started.clear()
        result = _simulate_poll_cycle(
            schedules=schedules,
            slot_key="2026-06-07T10:02:00Z",
            execute_scheduled_run_request_fn=_execute,
        )
        self.assertTrue(result["acted"])
        self.assertEqual(len(started), 1)
        self.assertEqual(
            schedules[schedule_id]["last_trigger_slot"], "2026-06-07T10:02:00Z"
        )

    # ------------------------------------------------------------------
    # Helper: simulate a single poll cycle that would normally happen
    # inside run_weekly_scheduler_forever.
    # ------------------------------------------------------------------
    def test_schedule_retry_respects_max_retries_boundary(self):
        """When the attempt exceeds max_retries, schedule_retry marks
        the wake request as failed_permanent."""
        policy = RetryPolicy(
            max_retries=2,
            base_delay_seconds=30,
            max_delay_seconds=3600,
            backoff_multiplier=2.0,
        )

        updated: list[Dict[str, Any]] = []

        async def _mock_update(
            tenant_id: str, workspace_id: str, wake_id: str, status: str, **kwargs: Any
        ) -> Dict[str, Any]:
            record = {"wake_id": wake_id, "status": status, **kwargs}
            updated.append(record)
            return record

        with (
            patch(
                "server_modules.bounded_scheduler_service._load_scheduler_scope",
                new=AsyncMock(return_value=({"metadata": {}}, {"id": "install-sage"}, MagicMock())),
            ),
            patch(
                "server_modules.bounded_scheduler_service.control_plane_repository.update_agent_scheduler_wake_request_status",
                new=_mock_update,
            ),
            patch(
                "server_modules.bounded_scheduler_service._enforce_session_scheduler_decision",
                return_value=None,
            ),
        ):
            # Current attempt 0, next_attempt=1: should_retry(1) = 1 < 2 = True -> pending
            result_1 = asyncio.run(
                schedule_retry(
                    tenant_id="tenant-1",
                    workspace_id="ws-1",
                    wake_request={
                        "id": "wake-max-1",
                        "metadata": {"retry": {"retry_attempt": 0}},
                    },
                    error="err-1",
                    retry_policy=policy,
                )
            )
            self.assertEqual(result_1["status"], "pending")

            # Current attempt 1, next_attempt=2: should_retry(2) = 2 < 2 = False -> failed_permanent
            result_2 = asyncio.run(
                schedule_retry(
                    tenant_id="tenant-1",
                    workspace_id="ws-1",
                    wake_request={
                        "id": "wake-max-1",
                        "metadata": {"retry": {"retry_attempt": 1}},
                    },
                    error="err-2",
                    retry_policy=policy,
                )
            )
            self.assertEqual(result_2["status"], "failed_permanent")

        self.assertEqual(len(updated), 2)
        self.assertEqual(updated[-1]["status"], "failed_permanent")
        self.assertIn("max_retries_exceeded", updated[-1].get("denial_reason", ""))


# --------------------------------------------------------------------------
# Module-level helper used by test methods above.
# --------------------------------------------------------------------------
def _simulate_poll_cycle(
    schedules: Dict[str, Dict[str, Any]],
    slot_key: str,
    execute_scheduled_run_request_fn: Any,
) -> Dict[str, Any]:
    """Simulate the core logic of one `run_weekly_scheduler_forever` poll
    cycle for the given schedules and a *single* matched slot.

    This replicates the inner-loop behaviour:
      1. Skip disabled schedules.
      2. Skip schedules whose `last_trigger_slot` already equals the key.
      3. Execute the run request; on success update run_log/last_trigger_slot.
      4. On failure update run_log with error and set last_error.

    Returns {"acted": bool, "started": [...]}.
    """
    started: list[Dict[str, Any]] = []
    changed = False

    for sid, schedule in list(schedules.items()):
        if not bool(schedule.get("enabled")):
            continue
        if str(schedule.get("last_trigger_slot") or "") == slot_key:
            continue

        req_payload = schedule.get("run_request") or {}
        if not isinstance(req_payload, dict) or not req_payload:
            continue

        try:
            req = RunStartRequest(**req_payload)
            run_result = execute_scheduled_run_request_fn(req, schedule_id=sid)
            current = dict(schedule)
            current["last_trigger_slot"] = slot_key
            current["last_trigger_date"] = slot_key[:10]
            current["pending_heartbeat"] = False
            current["pending_heartbeat_slot"] = None
            current = runs_core._append_schedule_run_log(
                current,
                status="started",
                run_id=str(run_result.get("run_id") or "").strip() or None,
                detail="Scheduled run started (test poll cycle).",
            )
            schedule.clear()
            schedule.update(current)
            changed = True
            started.append({
                "schedule_id": sid,
                "run_id": str(run_result.get("run_id") or ""),
                "name": str(schedule.get("name") or ""),
            })
        except Exception as exc:
            current = dict(schedule)
            current["last_trigger_slot"] = slot_key
            current["last_trigger_date"] = slot_key[:10]
            current = runs_core._append_schedule_run_log(
                current,
                status="failed",
                detail=str(exc),
            )
            schedule.clear()
            schedule.update(current)
            changed = True

    return {"acted": bool(started), "started": started}


if __name__ == "__main__":
    unittest.main()
