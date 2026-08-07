"""feat/agent-goals -- real-Postgres integration cover for the new
agent_goals table and the bounded_scheduler_service functions built on top
of it (create_goal, list_goals, get_goal, update_goal, _fire_goal,
process_due_goals_once).

Modeled directly on test_recurring_schedules_integration.py (duplicated
fixtures rather than imported, same reasoning that file's own docstring
gives: importing from a file a different concurrently-running agent might
own would couple this file's correctness to unrelated edits landing there).

Real Postgres only, opt-in from an already-exported DATABASE_URL -- no
.env reading, no os.environ mutation, skip rather than fail when
DATABASE_URL is unset. Per CLAUDE.md's MAN-139/MAN-202 rule, DATABASE_URL
must be exported explicitly by whoever runs this file, pointed at a
database whose name contains "test" -- conftest.py's own guard refuses
anything else.

What this file proves that a mocked unit test cannot, mapped directly onto
the build's own verification checklist:
  1. A goal wakes its agent -- create_goal persists a REAL, durable
     agent_scheduler_wake_requests row (trigger_kind='goal') through the
     same _persist_wakeup gate every other trigger kind uses.
  2. Attempts increment -- attempt_count is advanced ONLY by _fire_goal,
     proven against real rows, never by anything the test tells the model
     to say.
  3. The ceiling stops it and records why -- max_attempts and expires_at
     both drive a real status='exhausted' transition with a real
     last_outcome_reason, proven for both bounds independently.
  4. An exhausted goal does not wake again -- list_due_agent_goal_scopes
     (the system-level bypass_rls scan) excludes it once terminal.
  5. Per-day wake caps still apply -- goal_id's own daily ceiling, proven
     against real seeded agent_scheduler_wake_requests rows.
  6. A goal survives a simulated restart -- created through one call, read
     back through a completely independent repository call.
  7. RLS isolation -- both at the service layer (this file) and at the
     database level, with a throwaway non-superuser role and an
     unfiltered SELECT (GoalsRlsIsolationTests below), not just
     application-level filtering.
"""

from __future__ import annotations

import inspect
import os
import unittest
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.parse import urlsplit, urlunsplit


def _database_url_available() -> bool:
    return bool(os.getenv("DATABASE_URL", "").strip())


# Same per-operation kernel mock test_recurring_schedules_integration.py
# uses -- goal wakes route through the exact same _persist_wakeup ->
# _enforce_session_scheduler_decision gate every other trigger kind does,
# and conftest.py's own autouse kernel mock has no branch for
# "session-scheduler-decision" (no existing test hit that command against a
# live Postgres connection before the recurring-schedules work landed).
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
            "decision_id": "rkd_mock_agent_goal_test",
            "reason": "mock allow (agent-goal integration test)",
            "next_action": _SESSION_SCHEDULER_ALLOW_NEXT_ACTION.get(operation, ""),
            "payload": dict(payload or {}),
        }

    with patch.object(rk, "run_runtime_kernel", _mocked):
        yield


def _run(coro):
    """Same persistent bridge loop the rest of this suite uses -- asyncpg
    connections (and db.get_pool()'s per-loop pool cache) cannot cross
    event loops, so every async call in this file lands on the same loop
    across setUp/test/tearDown."""
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


# ── 1. Service-layer behavior (create/fire/bound/list/update) ───────────


class AgentGoalsIntegrationTests(_BridgeTestCase):
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
        self.tenant_id = f"t_goal_{suffix}"
        self.workspace_id = f"ws_goal_{suffix}"
        self.agent_id = f"agent_goal_{suffix}"
        self.project_id = f"proj_goal_{suffix}"
        self.other_tenant_id = f"t_goal_other_{suffix}"
        self.other_workspace_id = f"ws_goal_other_{suffix}"
        self.other_project_id = f"proj_goal_other_{suffix}"
        # agent_goals.project_id is FK'd to projects(id) -- seed a real
        # project row for both scopes, raw, bypassing RLS the same way the
        # six-table isolation suite's own admin fixture does.
        async with self.cpr._scoped_connection(bypass_rls=True) as connection:
            await connection.execute(
                """
                INSERT INTO projects (id, tenant_id, workspace_id, name, slug)
                VALUES ($1, $2, $3, 'Goal test project', $1), ($4, $5, $6, 'Other goal test project', $4)
                """,
                self.project_id, self.tenant_id, self.workspace_id,
                self.other_project_id, self.other_tenant_id, self.other_workspace_id,
            )

    async def async_teardown(self):
        self._kernel_patch.__exit__(None, None, None)
        try:
            async with self.cpr._scoped_connection(bypass_rls=True) as connection:
                if connection is not None:
                    await connection.execute(
                        "DELETE FROM agent_goals WHERE tenant_id = ANY($1::text[])",
                        [self.tenant_id, self.other_tenant_id],
                    )
                    await connection.execute(
                        "DELETE FROM agent_scheduler_wake_requests WHERE tenant_id = ANY($1::text[])",
                        [self.tenant_id, self.other_tenant_id],
                    )
                    await connection.execute(
                        "DELETE FROM projects WHERE tenant_id = ANY($1::text[])",
                        [self.tenant_id, self.other_tenant_id],
                    )
        except Exception:
            pass

    async def test_create_goal_wakes_the_agent_immediately(self):
        """A goal wakes its agent: create_goal must persist exactly one real
        agent_scheduler_wake_requests row (trigger_kind='goal') through the
        standard _persist_wakeup gate, and the goal itself starts at
        attempt_count=1 (the first attempt was used immediately)."""
        from server_modules import bounded_scheduler_service as bss

        goal = await bss.create_goal(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            agent_id=self.agent_id,
            goal_text="Negotiate a better rate with Acme Supplies.",
            instruction="If rejected, offer a 10% volume discount before giving up.",
        )
        self.assertTrue(goal["id"])
        self.assertEqual(goal["status"], "in_progress")
        self.assertEqual(int(goal["attempt_count"]), 1)
        self.assertEqual(int(goal["max_attempts"]), bss.DEFAULT_GOAL_MAX_ATTEMPTS)

        wake_count = await self.cpr.count_agent_scheduler_wake_requests_since(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            since=datetime.now(timezone.utc) - timedelta(hours=1),
            goal_id=goal["id"],
        )
        self.assertEqual(wake_count, 1)

    async def test_created_goal_survives_a_fresh_read_with_no_shared_state(self):
        """'Restart survival': create through one call, then read back
        through a SEPARATE, independent repository call -- nothing here is
        an in-process cache, so this is exactly what a process restart
        looks like from the table's point of view."""
        from server_modules import bounded_scheduler_service as bss

        goal = await bss.create_goal(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            agent_id=self.agent_id,
            goal_text="Close the Q3 renewal with Beta Corp.",
            instruction="Escalate to a human after 3 failed attempts.",
            max_attempts=7,
            lifetime_days=21,
        )
        goal_id = goal["id"]

        reloaded = await self.cpr.get_agent_goal(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, goal_id=goal_id,
        )
        self.assertIsNotNone(reloaded)
        self.assertEqual(reloaded["goal_text"], "Close the Q3 renewal with Beta Corp.")
        self.assertEqual(reloaded["instruction"], "Escalate to a human after 3 failed attempts.")
        self.assertEqual(int(reloaded["max_attempts"]), 7)
        now = datetime.now(timezone.utc)
        self.assertGreater(reloaded["expires_at"], now + timedelta(days=19))
        self.assertLess(reloaded["expires_at"], now + timedelta(days=23))

    async def test_bounds_are_clamped_to_hard_ceilings(self):
        from server_modules import bounded_scheduler_service as bss

        goal = await bss.create_goal(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            agent_id=self.agent_id,
            goal_text="A goal asking for way more than it should get.",
            max_attempts=99999,
            lifetime_days=99999,
        )
        self.assertEqual(int(goal["max_attempts"]), bss.GOAL_HARD_MAX_ATTEMPTS)
        now = datetime.now(timezone.utc)
        self.assertLess(goal["expires_at"], now + timedelta(days=bss.GOAL_HARD_MAX_LIFETIME_DAYS + 1))

    async def test_attempts_increment_on_each_fire(self):
        """The build's core honesty requirement: attempt_count is real,
        system-recorded data. Force a due goal through _fire_goal twice and
        confirm the counter advances by exactly one each time, with a new
        real wake request each time."""
        from server_modules import bounded_scheduler_service as bss

        goal = await bss.create_goal(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            agent_id=self.agent_id,
            goal_text="Ping the supplier again.",
            max_attempts=5,
        )
        self.assertEqual(int(goal["attempt_count"]), 1)

        due_goal = dict(goal)
        due_goal["next_fire_at"] = datetime.now(timezone.utc) - timedelta(seconds=5)
        outcome = await bss._fire_goal(due_goal)
        self.assertEqual(outcome["action"], "fired")

        reloaded = await self.cpr.get_agent_goal(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, goal_id=goal["id"],
        )
        self.assertEqual(int(reloaded["attempt_count"]), 2)
        self.assertGreater(reloaded["next_fire_at"], datetime.now(timezone.utc))

        due_goal_2 = dict(reloaded)
        due_goal_2["next_fire_at"] = datetime.now(timezone.utc) - timedelta(seconds=5)
        outcome_2 = await bss._fire_goal(due_goal_2)
        self.assertEqual(outcome_2["action"], "fired")

        reloaded_2 = await self.cpr.get_agent_goal(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, goal_id=goal["id"],
        )
        self.assertEqual(int(reloaded_2["attempt_count"]), 3)

        wake_count = await self.cpr.count_agent_scheduler_wake_requests_since(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            since=datetime.now(timezone.utc) - timedelta(hours=1),
            goal_id=goal["id"],
        )
        # attempt 1 (creation) + attempt 2 + attempt 3 = 3 real wake requests.
        self.assertEqual(wake_count, 3)

    async def test_ceiling_stops_it_and_records_why_max_attempts(self):
        """The bounded give-up condition: once attempt_count reaches
        max_attempts, the NEXT fire must mark the goal 'exhausted' with a
        real, system-recorded reason -- never firing a wake beyond the
        ceiling."""
        from server_modules import bounded_scheduler_service as bss

        goal = await bss.create_goal(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            agent_id=self.agent_id,
            goal_text="A goal with exactly one attempt to give.",
            max_attempts=1,
        )
        # create_goal already used attempt #1 (attempt_count == 1 ==
        # max_attempts) -- the goal is at its ceiling from the moment it's
        # created. The next scheduled fire must exhaust it, not fire again.
        self.assertEqual(int(goal["attempt_count"]), 1)
        self.assertEqual(int(goal["max_attempts"]), 1)

        due_goal = dict(goal)
        due_goal["next_fire_at"] = datetime.now(timezone.utc) - timedelta(seconds=5)
        outcome = await bss._fire_goal(due_goal)
        self.assertEqual(outcome["action"], "exhausted")
        self.assertEqual(outcome["reason"], "max_attempts_reached")

        reloaded = await self.cpr.get_agent_goal(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, goal_id=goal["id"],
        )
        self.assertEqual(reloaded["status"], "exhausted")
        self.assertEqual(reloaded["metadata"]["last_outcome_reason"], "max_attempts_reached")
        # No SECOND wake request was created by the exhausting call -- only
        # the one from creation.
        wake_count = await self.cpr.count_agent_scheduler_wake_requests_since(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            since=datetime.now(timezone.utc) - timedelta(hours=1),
            goal_id=goal["id"],
        )
        self.assertEqual(wake_count, 1)

    async def test_ceiling_stops_it_and_records_why_lifetime_expired(self):
        from server_modules import bounded_scheduler_service as bss

        goal = await bss.create_goal(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            agent_id=self.agent_id,
            goal_text="A goal whose lifetime will be force-expired.",
            max_attempts=50,
        )
        due_goal = dict(goal)
        due_goal["next_fire_at"] = datetime.now(timezone.utc) - timedelta(seconds=5)
        due_goal["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
        outcome = await bss._fire_goal(due_goal)
        self.assertEqual(outcome["action"], "exhausted")
        self.assertEqual(outcome["reason"], "lifetime_expired")

        reloaded = await self.cpr.get_agent_goal(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, goal_id=goal["id"],
        )
        self.assertEqual(reloaded["status"], "exhausted")

    async def test_exhausted_goal_does_not_wake_again(self):
        """The system-level due-scope scan (list_due_agent_goal_scopes,
        what process_due_goals_once calls) must exclude a goal the moment
        it turns terminal -- proven directly against the real table, not
        against list_goals' own RLS-scoped view."""
        from server_modules import bounded_scheduler_service as bss

        goal = await bss.create_goal(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            agent_id=self.agent_id,
            goal_text="A goal that will be exhausted then checked for silence.",
            max_attempts=1,
        )
        due_goal = dict(goal)
        due_goal["next_fire_at"] = datetime.now(timezone.utc) - timedelta(seconds=5)
        await bss._fire_goal(due_goal)

        scopes = await self.cpr.list_due_agent_goal_scopes(
            due_before=datetime.now(timezone.utc) + timedelta(days=1),
            non_terminal_statuses=list(bss.NON_TERMINAL_GOAL_STATUSES),
            limit=500,
        )
        matching = [
            s for s in scopes
            if s["tenant_id"] == self.tenant_id and s["workspace_id"] == self.workspace_id
        ]
        self.assertEqual(matching, [], "An exhausted goal's scope must not appear in the due-scan.")

        # Calling _fire_goal on the (now stale, terminal) row again must be
        # a no-op guard, not a second wake -- defends against a race where
        # the scan already queued this row before the previous fire landed.
        stale_due_goal = dict(due_goal)
        stale_due_goal["status"] = "exhausted"
        outcome = await bss._fire_goal(stale_due_goal)
        self.assertEqual(outcome["action"], "skipped_terminal")
        wake_count = await self.cpr.count_agent_scheduler_wake_requests_since(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            since=datetime.now(timezone.utc) - timedelta(hours=1),
            goal_id=goal["id"],
        )
        self.assertEqual(wake_count, 1)

    async def test_process_due_goals_once_fires_a_due_goal_end_to_end(self):
        from server_modules import bounded_scheduler_service as bss

        goal = await bss.create_goal(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            agent_id=self.agent_id,
            goal_text="A goal picked up by the real scan tick.",
            max_attempts=5,
        )
        await self.cpr.update_agent_goal(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            goal_id=goal["id"],
            next_fire_at=datetime.now(timezone.utc) - timedelta(seconds=5),
        )
        result = await bss.process_due_goals_once()
        matching = [r for r in result["results"] if r["result"].get("goal_id") == goal["id"]]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["result"]["action"], "fired")

        reloaded = await self.cpr.get_agent_goal(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, goal_id=goal["id"],
        )
        self.assertEqual(int(reloaded["attempt_count"]), 2)

    async def test_fire_respects_the_per_goal_daily_wake_cap(self):
        """The build's core safety requirement, applied to goals: a fast
        retry cadence must not bypass the per-day wake cap. Seeds the cap's
        worth of real agent_scheduler_wake_requests rows (as _fire_goal
        itself would have, one per prior fire) then proves the NEXT fire is
        skipped rather than creating a (cap+1)th wake, and that
        attempt_count does NOT advance on a skipped occurrence."""
        from server_modules import bounded_scheduler_service as bss

        goal = await bss.create_goal(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            agent_id=self.agent_id,
            goal_text="A goal that will hit its daily wake cap.",
            max_attempts=50,
        )
        cap = bss.max_goal_wakes_per_day()
        for _ in range(cap):
            await self.cpr.append_agent_scheduler_wake_request(
                tenant_id=self.tenant_id,
                workspace_id=self.workspace_id,
                master_agent_install_id=None,
                trigger_kind="goal",
                source="agent_goal",
                requested_by="owner",
                reason="goal_retry",
                summary="seed",
                payload={"goal_id": goal["id"]},
                policy={},
                approval_required=False,
                status="pending",
                denial_reason=None,
                due_at=datetime.now(timezone.utc),
                metadata={"goal_id": goal["id"]},
            )
        before = await self.cpr.get_agent_goal(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, goal_id=goal["id"],
        )
        due_goal = dict(before)
        due_goal["next_fire_at"] = datetime.now(timezone.utc) - timedelta(seconds=5)
        outcome = await bss._fire_goal(due_goal)
        self.assertEqual(outcome["action"], "skipped_daily_cap")

        after = await self.cpr.get_agent_goal(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, goal_id=goal["id"],
        )
        self.assertEqual(int(after["attempt_count"]), int(before["attempt_count"]), "A skipped occurrence must not spend an attempt.")
        self.assertEqual(after["metadata"]["last_skip_reason"], "goal_daily_wake_cap")

    async def test_update_goal_lets_agent_set_status_and_records_outcome(self):
        from server_modules import bounded_scheduler_service as bss

        goal = await bss.create_goal(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            agent_id=self.agent_id,
            goal_text="A goal the agent will mark done itself.",
        )
        result = await bss.update_goal(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            goal_id=goal["id"],
            status="done",
            note="Supplier accepted the counter-offer.",
            actor=f"agent:{self.agent_id}",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["goal"]["status"], "done")
        self.assertTrue(result["goal"]["resolved"])
        self.assertEqual(result["goal"]["last_outcome_reason"], f"done_by_agent:{self.agent_id}")

        # Once terminal, it must never fire again.
        scopes = await self.cpr.list_due_agent_goal_scopes(
            due_before=datetime.now(timezone.utc) + timedelta(days=1),
            non_terminal_statuses=list(bss.NON_TERMINAL_GOAL_STATUSES),
            limit=500,
        )
        matching = [s for s in scopes if s["tenant_id"] == self.tenant_id and s["workspace_id"] == self.workspace_id]
        self.assertEqual(matching, [])

    async def test_update_goal_rejects_exhausted_as_an_agent_settable_status(self):
        """'exhausted' is the SYSTEM's own bounded-give-up signal -- the
        agent-facing update path must reject it loudly, never silently
        accept it as if the model could just declare itself exhausted."""
        from server_modules import bounded_scheduler_service as bss

        goal = await bss.create_goal(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            agent_id=self.agent_id,
            goal_text="A goal that will try to cheat its way to exhausted.",
        )
        result = await bss.update_goal(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            goal_id=goal["id"],
            status="exhausted",
            actor=f"agent:{self.agent_id}",
        )
        self.assertFalse(result["ok"])

        reloaded = await self.cpr.get_agent_goal(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, goal_id=goal["id"],
        )
        self.assertNotEqual(reloaded["status"], "exhausted")

    async def test_update_goal_rejects_updates_on_a_terminal_goal(self):
        from server_modules import bounded_scheduler_service as bss

        goal = await bss.create_goal(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            agent_id=self.agent_id,
            goal_text="A goal that will be cancelled then poked again.",
        )
        first = await bss.update_goal(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, goal_id=goal["id"],
            status="cancelled", note="Dead end.", actor="owner",
        )
        self.assertTrue(first["ok"])
        second = await bss.update_goal(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, goal_id=goal["id"],
            status="in_progress", actor="owner",
        )
        self.assertFalse(second["ok"])

    async def test_list_and_service_layer_isolate_across_tenants(self):
        from server_modules import bounded_scheduler_service as bss

        mine = await bss.create_goal(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, project_id=self.project_id,
            agent_id=self.agent_id, goal_text="Mine.",
        )
        theirs = await bss.create_goal(
            tenant_id=self.other_tenant_id, workspace_id=self.other_workspace_id, project_id=self.other_project_id,
            agent_id=self.agent_id, goal_text="Theirs.",
        )
        mine_list = await bss.list_goals(tenant_id=self.tenant_id, workspace_id=self.workspace_id)
        their_list = await bss.list_goals(tenant_id=self.other_tenant_id, workspace_id=self.other_workspace_id)
        self.assertIn(mine["id"], [g["id"] for g in mine_list])
        self.assertNotIn(theirs["id"], [g["id"] for g in mine_list])
        self.assertIn(theirs["id"], [g["id"] for g in their_list])
        self.assertNotIn(mine["id"], [g["id"] for g in their_list])


# ── 2. Database-level RLS isolation: a real, non-superuser probe role ────


def _probe_dsn(admin_dsn: str, *, user: str, password: str) -> str:
    parts = urlsplit(admin_dsn)
    netloc = f"{user}:{password}@{parts.hostname or 'localhost'}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


class _RlsProbeRoleFixture(_BridgeTestCase):
    """Same shared plumbing as test_rls_six_tables_isolation_man109.py's
    fixture of the same name, duplicated rather than imported (see this
    file's own module docstring for why)."""

    GRANT_TABLES: tuple[str, ...] = ()

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

    async def async_setup(self):
        import asyncpg

        self.admin_dsn = os.environ["DATABASE_URL"].strip()
        try:
            self.admin_conn = await asyncpg.connect(self.admin_dsn, timeout=10)
        except Exception as exc:  # noqa: BLE001 — unreachable Postgres is a skip
            self.skipTest(f"{_NO_PG_REASON}: {exc}")
            return

        suffix = uuid.uuid4().hex[:10]
        self.tenant_a = f"t_goal_rls_a_{suffix}"
        self.tenant_b = f"t_goal_rls_b_{suffix}"
        self.ws_a = f"ws_goal_rls_a_{suffix}"
        self.ws_b = f"ws_goal_rls_b_{suffix}"
        self.role = f"rls_goal_probe_{suffix}"
        self.role_password = uuid.uuid4().hex

        await self.admin_conn.execute(
            f'CREATE ROLE "{self.role}" LOGIN PASSWORD \'{self.role_password}\''
        )
        if self.GRANT_TABLES:
            table_list = ", ".join(self.GRANT_TABLES)
            await self.admin_conn.execute(
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table_list} TO \"{self.role}\""
            )
        self.probe_dsn = _probe_dsn(self.admin_dsn, user=self.role, password=self.role_password)

    async def async_teardown(self):
        conn = getattr(self, "admin_conn", None)
        if conn is None:
            return
        try:
            await self._cleanup_rows(conn)
        finally:
            if self.GRANT_TABLES:
                table_list = ", ".join(self.GRANT_TABLES)
                await conn.execute(f'REVOKE ALL ON {table_list} FROM "{self.role}"')
            await conn.execute(f'DROP ROLE IF EXISTS "{self.role}"')
            await conn.close()

    async def _cleanup_rows(self, conn) -> None:
        return None

    async def _scoped_probe_read(self, tenant_id: str, workspace_id: str, query: str, *args, bypass: bool = False):
        import asyncpg

        conn = await asyncpg.connect(self.probe_dsn, timeout=10)
        try:
            async with conn.transaction():
                await conn.execute(
                    """
                    SELECT
                        set_config('app.current_tenant_id', $1, true),
                        set_config('app.current_workspace_id', $2, true),
                        set_config('app.rls_bypass', $3, true)
                    """,
                    tenant_id,
                    workspace_id,
                    "on" if bypass else "off",
                )
                return await conn.fetch(query, *args)
        finally:
            await conn.close()


class GoalsRlsIsolationTests(_RlsProbeRoleFixture):
    """The database-level mechanism, exactly the methodology
    test_rls_six_tables_isolation_man109.py established: a throwaway,
    ORDINARY (non-superuser, non-BYPASSRLS) Postgres role, opened fresh,
    scoped via the same set_config(...) calls _apply_connection_scope uses,
    running a query with NO tenant_id/workspace_id filter anywhere in its
    WHERE clause. No service-layer code anywhere in this class -- this is
    what proves the POLICY filters, not "the app remembered to filter."""

    GRANT_TABLES = ("agent_goals", "projects")

    async def async_setup(self):
        await super().async_setup()
        if not hasattr(self, "admin_conn"):
            return

        self.project_a_id = f"proj_a_{self.tenant_a}"
        self.project_b_id = f"proj_b_{self.tenant_b}"
        await self.admin_conn.execute(
            """
            INSERT INTO projects (id, tenant_id, workspace_id, name, slug)
            VALUES ($1, $2, $3, 'Tenant A project', $1), ($4, $5, $6, 'Tenant B project', $4)
            """,
            self.project_a_id, self.tenant_a, self.ws_a,
            self.project_b_id, self.tenant_b, self.ws_b,
        )

        now = datetime.now(timezone.utc)
        self.goal_a_id = f"goal_a_{self.tenant_a}"
        self.goal_b_id = f"goal_b_{self.tenant_b}"
        await self.admin_conn.execute(
            """
            INSERT INTO agent_goals (
                id, tenant_id, workspace_id, project_id, agent_id,
                goal_text, status, max_attempts, next_fire_at, expires_at
            )
            VALUES
                ($1, $2, $3, $4, 'agent-a', 'Tenant A goal', 'todo', 5, $9, $10),
                ($5, $6, $7, $8, 'agent-b', 'Tenant B goal', 'todo', 5, $9, $10)
            """,
            self.goal_a_id, self.tenant_a, self.ws_a, self.project_a_id,
            self.goal_b_id, self.tenant_b, self.ws_b, self.project_b_id,
            now, now + timedelta(days=14),
        )

    async def _cleanup_rows(self, conn) -> None:
        await conn.execute("DELETE FROM agent_goals WHERE tenant_id IN ($1, $2)", self.tenant_a, self.tenant_b)
        await conn.execute("DELETE FROM projects WHERE tenant_id IN ($1, $2)", self.tenant_a, self.tenant_b)

    async def test_agent_goals_isolated_at_the_database_level(self) -> None:
        rows = await self._scoped_probe_read(
            self.tenant_a, self.ws_a,
            "SELECT id, tenant_id FROM agent_goals WHERE id = ANY($1)",
            [self.goal_a_id, self.goal_b_id],
        )
        seen = {row["id"] for row in rows}
        self.assertIn(self.goal_a_id, seen, "agent_goals: tenant A's own row went missing under its own session scope.")
        self.assertNotIn(self.goal_b_id, seen, "agent_goals: RLS failed to hide tenant B's row from tenant A's session.")
        self.assertEqual(len(rows), 1, "agent_goals: expected exactly tenant A's one row.")

    async def test_regression_guard_tenant_b_sees_its_own_row_too(self) -> None:
        """Isolation is not 'nobody sees anything' -- scoped to tenant B,
        the SAME query sees tenant B's row instead."""
        rows = await self._scoped_probe_read(
            self.tenant_b, self.ws_b,
            "SELECT id, tenant_id FROM agent_goals WHERE id = ANY($1)",
            [self.goal_a_id, self.goal_b_id],
        )
        seen = {row["id"] for row in rows}
        self.assertIn(self.goal_b_id, seen)
        self.assertNotIn(self.goal_a_id, seen)
        self.assertEqual(len(rows), 1)

    async def test_catalog_confirms_agent_goals_rls_enabled_and_forced(self) -> None:
        rows = await self.admin_conn.fetch(
            """
            SELECT c.relname AS table_name, c.relrowsecurity AS rls_enabled, c.relforcerowsecurity AS rls_forced,
                   COALESCE(p.policy_count, 0) AS policy_count
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            LEFT JOIN (
                SELECT schemaname, tablename, COUNT(*) AS policy_count
                FROM pg_policies GROUP BY schemaname, tablename
            ) p ON p.schemaname = n.nspname AND p.tablename = c.relname
            WHERE n.nspname = 'public' AND c.relname = 'agent_goals'
            """
        )
        self.assertEqual(len(rows), 1, "agent_goals: not found in pg_class.")
        row = rows[0]
        self.assertTrue(row["rls_enabled"], "agent_goals: RLS not enabled.")
        self.assertTrue(row["rls_forced"], "agent_goals: RLS not FORCEd.")
        self.assertGreaterEqual(row["policy_count"], 1, "agent_goals: no RLS policy.")


if __name__ == "__main__":
    unittest.main()
