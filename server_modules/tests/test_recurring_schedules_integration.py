"""Recurring schedules ("every morning at 9am") -- real-Postgres integration
cover for the new agent_recurring_schedules table and the bounded_scheduler_
service functions built on top of it (create_recurring_schedule,
list_recurring_schedules, cancel_recurring_schedule, _fire_recurring_schedule,
process_due_recurring_schedules_once).

Real Postgres only, opt-in from an already-exported DATABASE_URL -- same
convention as test_rls_six_tables_isolation_man109.py (no .env reading, no
os.environ mutation, skip rather than fail when DATABASE_URL is unset).
Per CLAUDE.md's MAN-139/MAN-202 rule, DATABASE_URL must be exported
explicitly by whoever runs this file, pointed at a database whose name
contains "test" -- conftest.py's own guard refuses anything else.

Five things this file proves that a mocked unit test cannot:
  1. A created schedule is a real, durable Postgres row -- read back via a
     COMPLETELY FRESH repository call with no shared in-process state
     (restart survival: nothing here is cached anywhere between "create"
     and "read back").
  2. The per-schedule daily wake cap (count_agent_scheduler_wake_requests_
     since's new recurring_schedule_id filter) is enforced against REAL
     rows in agent_scheduler_wake_requests, not a mocked count.
  3. list/cancel work end-to-end through the real RLS-scoped repository
     functions, not through a mock that always says yes.
  4. Two different (tenant_id, workspace_id) scopes cannot see each other's
     recurring schedules -- migrations/enable_rls.sql's policy on this new
     table actually holds at the database level.
  5. A due schedule fired through _fire_recurring_schedule persists exactly
     one new agent_scheduler_wake_requests row and advances next_fire_at,
     using the real _persist_wakeup gate end to end.
"""

from __future__ import annotations

import inspect
import os
import unittest
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch


def _database_url_available() -> bool:
    return bool(os.getenv("DATABASE_URL", "").strip())


# The wake-request path (bounded_scheduler_service._enforce_session_
# scheduler_decision and control_plane_repository._enforce_scheduler_wake_
# repository_decision) gates every wake through the Rust runtime kernel's
# "session-scheduler-decision" command. conftest.py's own autouse fixture
# (_skip_kernel_tests_when_binary_missing) mocks run_runtime_kernel for
# every OTHER command family already exercised against a real DB elsewhere
# in this suite, but has no branch for "session-scheduler-decision" -- no
# existing test hits this command against a live Postgres connection, so it
# was never added there. Rather than edit the shared conftest.py (MAN-139's
# collision protocol assigns it to whichever agent owns it at any given
# moment -- a different file entirely is the safe place for a new,
# narrowly-scoped mock), this local context manager covers exactly this
# command for exactly this file's tests, mirroring conftest's own per-
# operation next_action mapping (session_scheduler.rs's real contract, per
# the comments on _SCHEDULER_WAKE_REPOSITORY_NEXT_ACTIONS /
# _enforce_session_scheduler_decision).
_SESSION_SCHEDULER_ALLOW_NEXT_ACTION = {
    "event_trigger": "schedule_event_trigger",
    "self_proposed_trigger": "schedule_self_proposed_trigger",
    "wake_decision": "trigger_wakeup",
    "claim_wake_requests": "claim_due_wake_requests",
    "finalize_wake_requests": "finalize_wake_requests",
    "schedule_retry": "schedule_retry",
    "failure_decision": "record_scheduler_failure",
}


@contextmanager
def _allow_session_scheduler_kernel_calls():
    from server_modules import rust_runtime_kernel_client as rk

    real_run_runtime_kernel = rk.run_runtime_kernel

    def _mocked(command, payload, timeout_seconds=5):
        if command != "session-scheduler-decision":
            return real_run_runtime_kernel(command, payload, timeout_seconds=timeout_seconds)
        operation = str((payload or {}).get("operation") or "").strip()
        return {
            "ok": True,
            "decision": "allow",
            "command": command,
            "decision_id": "rkd_mock_recurring_schedule_test",
            "reason": "mock allow (recurring-schedule integration test)",
            "next_action": _SESSION_SCHEDULER_ALLOW_NEXT_ACTION.get(operation, ""),
            "payload": dict(payload or {}),
        }

    with patch.object(rk, "run_runtime_kernel", _mocked):
        yield


def _run(coro):
    """Same persistent bridge loop test_rls_six_tables_isolation_man109.py
    uses -- asyncpg connections (and db.get_pool()'s per-loop pool cache)
    cannot cross event loops, so every async call in this file lands on the
    same loop across setUp/test/tearDown."""
    from server_modules import sync_asyncio_bridge

    return sync_asyncio_bridge.run_coro_sync(coro)


_NO_PG_REASON = "No Postgres reachable (DATABASE_URL unset — export it to run this suite)"


class _BridgeTestCase(unittest.TestCase):
    def _maybe_await(self, value):
        return _run(value) if inspect.iscoroutine(value) else value

    def _callTestMethod(self, method):
        self._maybe_await(method())

    def setUp(self):
        if not _database_url_available():
            self.skipTest(_NO_PG_REASON)
            return
        try:
            self._maybe_await(self.async_setup())
        except Exception:
            try:
                self._maybe_await(self.async_teardown())
            except Exception:
                pass
            raise

    def tearDown(self):
        if not _database_url_available():
            return
        self._maybe_await(self.async_teardown())

    async def async_setup(self):
        return None

    async def async_teardown(self):
        return None


class RecurringSchedulesIntegrationTests(_BridgeTestCase):
    async def async_setup(self):
        from server_modules import control_plane_repository as cpr

        self.cpr = cpr
        self._kernel_patch = _allow_session_scheduler_kernel_calls()
        self._kernel_patch.__enter__()
        pool = await cpr.ensure_control_plane_schema()
        if pool is None:
            self.skipTest(f"{_NO_PG_REASON}: pool unavailable")
            return
        suffix = uuid.uuid4().hex[:10]
        self.tenant_id = f"t_recur_{suffix}"
        self.workspace_id = f"ws_recur_{suffix}"
        self.agent_id = f"agent_recur_{suffix}"
        self.other_tenant_id = f"t_recur_other_{suffix}"
        self.other_workspace_id = f"ws_recur_other_{suffix}"
        self._created_schedule_ids: list[str] = []

    async def async_teardown(self):
        self._kernel_patch.__exit__(None, None, None)
        # Best-effort row cleanup -- this is a disposable "test" database
        # (conftest.py refuses anything else), so leaked rows are harmless,
        # but tidy up when we can.
        try:
            async with self.cpr._scoped_connection(bypass_rls=True) as connection:
                if connection is not None:
                    await connection.execute(
                        "DELETE FROM agent_recurring_schedules WHERE tenant_id = ANY($1::text[])",
                        [self.tenant_id, self.other_tenant_id],
                    )
                    await connection.execute(
                        "DELETE FROM agent_scheduler_wake_requests WHERE tenant_id = ANY($1::text[])",
                        [self.tenant_id, self.other_tenant_id],
                    )
        except Exception:
            pass

    async def test_created_schedule_survives_a_fresh_read_with_no_shared_state(self):
        """'Restart survival': create through one call, then read back
        through a SEPARATE, independent repository call -- nothing here is
        an in-process cache, so this is exactly what a process restart
        looks like from the table's point of view."""
        from server_modules import bounded_scheduler_service as bss

        record = await bss.create_recurring_schedule(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            agent_id=self.agent_id,
            cron_expression="0 9 * * *",
            instruction="Check the inbox and summarize anything urgent.",
        )
        schedule_id = record["id"]
        self.assertTrue(schedule_id)
        self.assertEqual(record["status"], "active")
        self.assertEqual(record["cron_expression"], "0 9 * * *")

        # Fresh, independent read -- simulates a process restart: no object
        # from the call above is reused, only the schedule_id string.
        reloaded = await self.cpr.get_agent_recurring_schedule(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, schedule_id=schedule_id,
        )
        self.assertIsNotNone(reloaded)
        self.assertEqual(reloaded["cron_expression"], "0 9 * * *")
        self.assertEqual(reloaded["payload"]["instruction"], "Check the inbox and summarize anything urgent.")
        self.assertIsNotNone(reloaded["expires_at"])
        # Bounded by default: no max_occurrences/expires_at was passed, so
        # this must have defaulted to ~90 days out, not "forever".
        now = datetime.now(timezone.utc)
        expires_at = reloaded["expires_at"]
        self.assertGreater(expires_at, now + timedelta(days=85))
        self.assertLess(expires_at, now + timedelta(days=95))

    async def test_list_shows_active_and_hides_cancelled_by_default(self):
        from server_modules import bounded_scheduler_service as bss

        record = await bss.create_recurring_schedule(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            agent_id=self.agent_id,
            cron_expression="*/15 * * * *",
            instruction="Poll the queue.",
        )
        schedule_id = record["id"]

        active = await bss.list_recurring_schedules(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, agent_id=self.agent_id,
        )
        self.assertIn(schedule_id, [row["id"] for row in active])

        cancel_result = await bss.cancel_recurring_schedule(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            agent_id=self.agent_id,
            schedule_id=schedule_id,
        )
        self.assertTrue(cancel_result["ok"])
        self.assertEqual(cancel_result["schedule"]["status"], "cancelled")

        active_after_cancel = await bss.list_recurring_schedules(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, agent_id=self.agent_id,
        )
        self.assertNotIn(schedule_id, [row["id"] for row in active_after_cancel])

        # A cancelled schedule cannot be cancelled again -- not silently
        # ok:true, an explicit error.
        second_cancel = await bss.cancel_recurring_schedule(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            agent_id=self.agent_id,
            schedule_id=schedule_id,
        )
        self.assertFalse(second_cancel["ok"])

    async def test_cancel_rejects_wrong_agent_and_unknown_id(self):
        from server_modules import bounded_scheduler_service as bss

        record = await bss.create_recurring_schedule(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            agent_id=self.agent_id,
            cron_expression="0 9 * * *",
            instruction="Daily check-in.",
        )
        wrong_agent_cancel = await bss.cancel_recurring_schedule(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            agent_id="some-other-agent",
            schedule_id=record["id"],
        )
        self.assertFalse(wrong_agent_cancel["ok"])

        unknown_cancel = await bss.cancel_recurring_schedule(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            agent_id=self.agent_id,
            schedule_id="recur_does_not_exist",
        )
        self.assertFalse(unknown_cancel["ok"])

    async def test_rls_isolates_recurring_schedules_across_tenants(self):
        """Same proof shape as test_rls_six_tables_isolation_man109.py's
        service-layer regression class: list_recurring_schedules for one
        (tenant_id, workspace_id) must never see a row created under a
        different scope, enforced by migrations/enable_rls.sql's policy on
        agent_recurring_schedules, not by application-level filtering
        alone."""
        from server_modules import bounded_scheduler_service as bss

        mine = await bss.create_recurring_schedule(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            agent_id=self.agent_id,
            cron_expression="0 9 * * *",
            instruction="Mine.",
        )
        theirs = await bss.create_recurring_schedule(
            tenant_id=self.other_tenant_id,
            workspace_id=self.other_workspace_id,
            agent_id=self.agent_id,
            cron_expression="0 9 * * *",
            instruction="Theirs.",
        )
        mine_list = await bss.list_recurring_schedules(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id,
        )
        their_list = await bss.list_recurring_schedules(
            tenant_id=self.other_tenant_id, workspace_id=self.other_workspace_id,
        )
        self.assertIn(mine["id"], [row["id"] for row in mine_list])
        self.assertNotIn(theirs["id"], [row["id"] for row in mine_list])
        self.assertIn(theirs["id"], [row["id"] for row in their_list])
        self.assertNotIn(mine["id"], [row["id"] for row in their_list])

    async def test_fire_creates_exactly_one_wake_request_and_advances_next_fire_at(self):
        from server_modules import bounded_scheduler_service as bss

        record = await bss.create_recurring_schedule(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            agent_id=self.agent_id,
            cron_expression="* * * * *",  # every minute -- always "due"
            instruction="Ping.",
        )
        # Force it due right now (create_recurring_schedule computed a
        # genuine next-minute next_fire_at; back-date it so the fire test
        # doesn't depend on wall-clock timing).
        due_row = dict(record)
        due_row["next_fire_at"] = datetime.now(timezone.utc) - timedelta(seconds=5)

        before = await self.cpr.count_agent_scheduler_wake_requests_since(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            since=datetime.now(timezone.utc) - timedelta(hours=1),
            recurring_schedule_id=record["id"],
        )
        self.assertEqual(before, 0)

        outcome = await bss._fire_recurring_schedule(due_row)
        self.assertEqual(outcome["action"], "fired")

        after = await self.cpr.count_agent_scheduler_wake_requests_since(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            since=datetime.now(timezone.utc) - timedelta(hours=1),
            recurring_schedule_id=record["id"],
        )
        self.assertEqual(after, 1)

        reloaded = await self.cpr.get_agent_recurring_schedule(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, schedule_id=record["id"],
        )
        self.assertEqual(reloaded["occurrence_count"], 1)
        self.assertIsNotNone(reloaded["last_fired_at"])
        # next_fire_at must have advanced to a NEW future slot, not stayed
        # at the (now past) due_at we forced above.
        self.assertGreater(reloaded["next_fire_at"], datetime.now(timezone.utc))

    async def test_fire_respects_the_per_schedule_daily_wake_cap(self):
        """The build's core safety requirement: a cron that fires every
        minute must not bypass the per-day wake cap. Seeds the cap's worth
        of real agent_scheduler_wake_requests rows (as _fire_recurring_
        schedule itself would have, one per prior fire) then proves the
        NEXT fire is skipped rather than creating a (cap+1)th wake."""
        from server_modules import bounded_scheduler_service as bss

        record = await bss.create_recurring_schedule(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            agent_id=self.agent_id,
            cron_expression="* * * * *",
            instruction="Ping every minute.",
        )
        schedule_id = record["id"]
        cap = bss.max_recurring_wakes_per_day()
        for _ in range(cap):
            await self.cpr.append_agent_scheduler_wake_request(
                tenant_id=self.tenant_id,
                workspace_id=self.workspace_id,
                trigger_kind="recurring",
                source="recurring_schedule",
                requested_by="owner",
                reason="recurring_schedule",
                summary="seeded prior fire",
                payload={"agent_id": self.agent_id, "recurring_schedule_id": schedule_id},
                policy={},
                status="pending",
                due_at=datetime.now(timezone.utc),
                metadata={"agent_id": self.agent_id, "recurring_schedule_id": schedule_id},
            )

        due_row = dict(record)
        due_row["next_fire_at"] = datetime.now(timezone.utc) - timedelta(seconds=5)
        outcome = await bss._fire_recurring_schedule(due_row)
        self.assertEqual(outcome["action"], "skipped_daily_cap")

        count_after = await self.cpr.count_agent_scheduler_wake_requests_since(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            since=datetime.now(timezone.utc) - timedelta(hours=1),
            recurring_schedule_id=schedule_id,
        )
        self.assertEqual(count_after, cap)  # unchanged -- no (cap+1)th wake

        reloaded = await self.cpr.get_agent_recurring_schedule(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, schedule_id=schedule_id,
        )
        self.assertEqual(reloaded["metadata"].get("last_skip_reason"), "recurring_daily_wake_cap")
        # Still active -- a capped day is not the same as expired/cancelled;
        # it resumes firing once the rolling 24h window clears.
        self.assertEqual(reloaded["status"], "active")

    async def test_fire_expires_a_schedule_past_its_bound(self):
        from server_modules import bounded_scheduler_service as bss

        record = await bss.create_recurring_schedule(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            agent_id=self.agent_id,
            cron_expression="0 9 * * *",
            instruction="Daily check-in.",
            max_occurrences=1,
        )
        expired_row = dict(record)
        expired_row["occurrence_count"] = 1  # already used its one allowed fire
        expired_row["next_fire_at"] = datetime.now(timezone.utc) - timedelta(seconds=5)

        outcome = await bss._fire_recurring_schedule(expired_row)
        self.assertEqual(outcome["action"], "expired")

        reloaded = await self.cpr.get_agent_recurring_schedule(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, schedule_id=record["id"],
        )
        self.assertEqual(reloaded["status"], "expired")

    async def test_invalid_cron_fails_loud_and_persists_nothing(self):
        from server_modules import bounded_scheduler_service as bss

        with self.assertRaises(bss.SchedulerPolicyError):
            await bss.create_recurring_schedule(
                tenant_id=self.tenant_id,
                workspace_id=self.workspace_id,
                agent_id=self.agent_id,
                cron_expression="not a cron",
                instruction="Should never be created.",
            )
        rows = await bss.list_recurring_schedules(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, agent_id=self.agent_id,
        )
        self.assertEqual(rows, [])


# ── True database-level RLS proof ───────────────────────────────────────
# RecurringSchedulesIntegrationTests.test_rls_isolates_recurring_schedules_
# across_tenants above proves list_recurring_schedules never RETURNS another
# scope's row -- but every one of this file's repository calls already
# includes its own "WHERE tenant_id = $1 AND workspace_id = $2" (see
# list_agent_recurring_schedules in control_plane_repository.py), so that
# test alone cannot tell "the WHERE clause filtered it" apart from "the
# database-level POLICY filtered it" -- an application-code regression that
# dropped the WHERE clause would still pass it if the policy silently
# covered the gap, or vice versa. This class is the other half: same
# methodology as test_rls_six_tables_isolation_man109.py's
# _RlsProbeRoleFixture (duplicated here rather than imported, per that
# file's own stated collision-avoidance reasoning), a throwaway, ORDINARY
# (non-superuser, non-BYPASSRLS) Postgres role running a RAW SELECT with NO
# tenant_id/workspace_id filter anywhere in it. If migrations/enable_rls.sql
# is ever not applied to agent_recurring_schedules -- or its policy is ever
# dropped -- this test starts seeing rows across scopes and fails, even
# though every application-layer call above would still pass.
class RecurringSchedulesDatabaseLevelRlsTests(_BridgeTestCase):
    async def async_setup(self):
        import asyncpg

        from server_modules import control_plane_repository as cpr

        self.cpr = cpr
        self._kernel_patch = _allow_session_scheduler_kernel_calls()
        self._kernel_patch.__enter__()
        pool = await cpr.ensure_control_plane_schema()
        if pool is None:
            self.skipTest(f"{_NO_PG_REASON}: pool unavailable")
            return

        self.admin_dsn = os.environ["DATABASE_URL"].strip()
        try:
            self.admin_conn = await asyncpg.connect(self.admin_dsn, timeout=10)
        except Exception as exc:
            self.skipTest(f"{_NO_PG_REASON}: {exc}")
            return

        suffix = uuid.uuid4().hex[:10]
        self.tenant_a = f"t_recur_rls_a_{suffix}"
        self.tenant_b = f"t_recur_rls_b_{suffix}"
        self.ws_a = f"ws_recur_rls_a_{suffix}"
        self.ws_b = f"ws_recur_rls_b_{suffix}"
        self.agent_id = f"agent_recur_rls_{suffix}"
        self.role = f"rls_recur_probe_{suffix}"
        self.role_password = uuid.uuid4().hex

        await self.admin_conn.execute(
            f'CREATE ROLE "{self.role}" LOGIN PASSWORD \'{self.role_password}\''
        )
        await self.admin_conn.execute(
            f'GRANT SELECT ON agent_recurring_schedules TO "{self.role}"'
        )

        from urllib.parse import urlsplit, urlunsplit

        parts = urlsplit(self.admin_dsn)
        netloc = f"{self.role}:{self.role_password}@{parts.hostname or 'localhost'}"
        if parts.port:
            netloc += f":{parts.port}"
        self.probe_dsn = urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))

        from server_modules import bounded_scheduler_service as bss

        self.row_a = await bss.create_recurring_schedule(
            tenant_id=self.tenant_a,
            workspace_id=self.ws_a,
            agent_id=self.agent_id,
            cron_expression="0 9 * * *",
            instruction="Tenant A's schedule.",
        )
        self.row_b = await bss.create_recurring_schedule(
            tenant_id=self.tenant_b,
            workspace_id=self.ws_b,
            agent_id=self.agent_id,
            cron_expression="0 9 * * *",
            instruction="Tenant B's schedule.",
        )

    async def async_teardown(self):
        self._kernel_patch.__exit__(None, None, None)
        admin_conn = getattr(self, "admin_conn", None)
        if admin_conn is None:
            return
        try:
            await admin_conn.execute(
                "DELETE FROM agent_recurring_schedules WHERE tenant_id = ANY($1::text[])",
                [getattr(self, "tenant_a", ""), getattr(self, "tenant_b", "")],
            )
        finally:
            if hasattr(self, "role"):
                await admin_conn.execute(f'REVOKE ALL ON agent_recurring_schedules FROM "{self.role}"')
                await admin_conn.execute(f'DROP ROLE IF EXISTS "{self.role}"')
            await admin_conn.close()

    async def test_raw_unfiltered_select_as_ordinary_role_only_sees_the_scoped_tenant(self):
        import asyncpg

        conn = await asyncpg.connect(self.probe_dsn, timeout=10)
        try:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.current_tenant_id', $1, true), "
                    "set_config('app.current_workspace_id', $2, true)",
                    self.tenant_a,
                    self.ws_a,
                )
                # Deliberately NO tenant_id/workspace_id in this WHERE clause
                # at all -- if the database-level policy is doing its job,
                # scoping to tenant_a happens via the session GUCs above,
                # not this query's own filtering.
                rows = await conn.fetch("SELECT id, tenant_id FROM agent_recurring_schedules")
        finally:
            await conn.close()

        seen_ids = {row["id"] for row in rows}
        self.assertIn(self.row_a["id"], seen_ids)
        self.assertNotIn(self.row_b["id"], seen_ids)
        self.assertTrue(all(row["tenant_id"] == self.tenant_a for row in rows))

    async def test_raw_unfiltered_select_with_no_scope_set_sees_nothing(self):
        """No app.current_tenant_id/workspace_id GUCs set at all -- the
        default-deny posture: an ordinary role with no scope configured
        must see zero rows, not every workspace's."""
        import asyncpg

        conn = await asyncpg.connect(self.probe_dsn, timeout=10)
        try:
            rows = await conn.fetch("SELECT id FROM agent_recurring_schedules")
        finally:
            await conn.close()
        seen_ids = {row["id"] for row in rows}
        self.assertNotIn(self.row_a["id"], seen_ids)
        self.assertNotIn(self.row_b["id"], seen_ids)


if __name__ == "__main__":
    unittest.main()
