"""Tests for server_modules/telemetry_retention_service.py and its five
prune_* repository functions (control_plane_repository.py x4,
run_state_repository.py x1, session_service.py x1).

Requires a REAL, disposable Postgres database -- these tests exercise the
actual batched-DELETE SQL against the real schema
(control_plane_repository.ensure_control_plane_schema(), same init path
production uses), not a mock, because the properties that actually matter
here -- "a live/undelivered/unfinished row is never deleted no matter how
old", "a batch-bounded run never deletes more than batch_size * max_batches
rows", "conversation content and financial records are never touched" --
are exactly the kind of thing a mock would happily let slip past unnoticed
(same reasoning server_modules/tests/test_gateway_state_repository_prune.py
states for its own real-SQLite-DB choice).

Run against a disposable database ONLY. conftest.py's pytest_configure hook
hard-aborts the whole session if DATABASE_URL doesn't look like an obvious
test database (MAN-139) -- see that file. Typical invocation:

    createdb empyralis_test_retention
    DATABASE_URL=postgresql://$(whoami)@localhost:5432/empyralis_test_retention \\
        python3 -m pytest server_modules/tests/test_telemetry_retention_service.py -v
    dropdb empyralis_test_retention

Marked blackbox_db (same marker test_gateway_state_repository_prune-adjacent
Postgres tests use) so it can be selected/excluded the same way other
Postgres-backed tests are.
"""

from __future__ import annotations

import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import pytest

if not os.environ.get("DATABASE_URL", "").strip():
    pytest.skip(
        "DATABASE_URL not set -- these tests require a real, disposable "
        "Postgres database (see this file's module docstring).",
        allow_module_level=True,
    )
if "test" not in os.environ.get("DATABASE_URL", "").lower():
    pytest.skip(
        "DATABASE_URL does not look like a test database (no 'test' in the "
        "name) -- refusing to run destructive DELETE tests against it.",
        allow_module_level=True,
    )

from server_modules import control_plane_repository  # noqa: E402
from server_modules import db as runtime_db  # noqa: E402
from server_modules import run_state_repository  # noqa: E402
from server_modules import session_service  # noqa: E402
from server_modules import telemetry_retention_service  # noqa: E402


pytestmark = pytest.mark.blackbox_db


def _ago(days: float = 0, hours: float = 0) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days, hours=hours)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class TelemetryRetentionServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        os.environ.pop("EMPYRALIS_TELEMETRY_RETENTION_ENABLED", None)
        os.environ.pop("EMPYRALIS_ACTIVITY_LEDGER_RETENTION_DAYS", None)
        os.environ.pop("EMPYRALIS_AGENT_TRACE_RETENTION_DAYS", None)
        os.environ.pop("EMPYRALIS_RUNTIME_OUTBOX_RETENTION_DAYS", None)
        os.environ.pop("EMPYRALIS_AGENT_SESSION_RETENTION_DAYS", None)
        os.environ.pop("EMPYRALIS_SECRET_ACCESS_AUDIT_RETENTION_DAYS", None)
        os.environ.pop("EMPYRALIS_TELEMETRY_RETENTION_BATCH_SIZE", None)
        os.environ.pop("EMPYRALIS_TELEMETRY_RETENTION_MAX_BATCHES_PER_TABLE", None)
        os.environ.pop("EMPYRALIS_TELEMETRY_RETENTION_INTERVAL_SECONDS", None)

        pool = await control_plane_repository.ensure_control_plane_schema()
        assert pool is not None, "test requires a live Postgres pool -- check DATABASE_URL"
        self.pool = pool
        # Force-create the two lazily-initialized tables so TRUNCATE below
        # doesn't fail on a first run against a brand-new database.
        await run_state_repository._ensure_runtime_outbox_table(pool)
        await session_service._ensure_runtime_sessions_table()

        self.tenant_id = _new_id("tenant")
        self.workspace_id = _new_id("ws")
        await pool.execute(
            "TRUNCATE activity_ledger_events, agent_secret_access_events, agent_sessions, "
            "agent_traces, agent_trace_events, runtime_outbox, runtime_sessions, "
            "agent_turns, credit_ledger_events"
        )

    async def _insert_activity_ledger_event(self, *, created_at: datetime) -> str:
        row_id = _new_id("aevt")
        await self.pool.execute(
            """
            INSERT INTO activity_ledger_events (
                id, tenant_id, workspace_id, actor_type, actor_id, event_class, created_at, updated_at
            ) VALUES ($1, $2, $3, 'agent', 'sage', 'system_activity', $4, $4)
            """,
            row_id, self.tenant_id, self.workspace_id, created_at,
        )
        return row_id

    async def _insert_secret_access_event(self, *, created_at: datetime) -> str:
        row_id = _new_id("sevt")
        await self.pool.execute(
            """
            INSERT INTO agent_secret_access_events (
                id, tenant_id, workspace_id, secret_kind, status, created_at, updated_at
            ) VALUES ($1, $2, $3, 'provider_api_key', 'allowed', $4, $4)
            """,
            row_id, self.tenant_id, self.workspace_id, created_at,
        )
        return row_id

    async def _insert_agent_session(self, *, expires_at: Optional[datetime]) -> str:
        row_id = _new_id("asess")
        await self.pool.execute(
            """
            INSERT INTO agent_sessions (
                id, tenant_id, workspace_id, thread_id, channel, status, expires_at
            ) VALUES ($1, $2, $3, $1, 'web', 'active', $4)
            """,
            row_id, self.tenant_id, self.workspace_id, expires_at,
        )
        return row_id

    async def _insert_agent_trace(self, *, started_at: datetime, finished_at: Optional[datetime]) -> str:
        row_id = _new_id("trace")
        await self.pool.execute(
            """
            INSERT INTO agent_traces (
                id, tenant_id, workspace_id, root_agent_id, surface, started_at, finished_at, outcome
            ) VALUES ($1, $2, $3, 'sage', 'console', $4, $5, $6)
            """,
            row_id, self.tenant_id, self.workspace_id, started_at, finished_at,
            "completed" if finished_at else None,
        )
        return row_id

    async def _insert_agent_trace_event(self, trace_id: str, *, seq: int = 1) -> str:
        row_id = _new_id("tevt")
        await self.pool.execute(
            """
            INSERT INTO agent_trace_events (
                id, trace_id, seq, event_type, agent_id, ts
            ) VALUES ($1, $2, $3, 'tool_call', 'sage', now())
            """,
            row_id, trace_id, seq,
        )
        return row_id

    async def _insert_outbox_event(
        self, *, created_at: datetime, delivered_at: Optional[datetime], poisoned_at: Optional[datetime] = None
    ) -> str:
        row_id = _new_id("obx")
        await self.pool.execute(
            """
            INSERT INTO runtime_outbox (
                event_id, event_type, tenant_id, workspace_id, payload,
                created_at, delivered_at, poisoned_at
            ) VALUES ($1, 'runtime_event', $2, $3, '{}'::jsonb, $4, $5, $6)
            """,
            row_id, self.tenant_id, self.workspace_id, created_at, delivered_at, poisoned_at,
        )
        return row_id

    async def _insert_runtime_session(self, *, expires_at: datetime, created_at: Optional[datetime] = None) -> str:
        row_id = _new_id("rtsess")
        await self.pool.execute(
            """
            INSERT INTO runtime_sessions (
                session_id, workspace_id, tenant_id, channel, actor, created_at, expires_at, metadata
            ) VALUES ($1, $2, $3, 'web', '{}'::jsonb, $4, $5, '{}'::jsonb)
            """,
            row_id, self.workspace_id, self.tenant_id, created_at or _ago(), expires_at,
        )
        return row_id

    async def _insert_agent_turn(self, *, created_at: datetime, content: str = "hello") -> str:
        row_id = _new_id("turn")
        await self.pool.execute(
            """
            INSERT INTO agent_turns (
                id, tenant_id, workspace_id, thread_id, role, content, created_at, updated_at
            ) VALUES ($1, $2, $3, $1, 'user', $4, $5, $5)
            """,
            row_id, self.tenant_id, self.workspace_id, content, created_at,
        )
        return row_id

    async def _insert_credit_ledger_event(self, *, created_at: datetime) -> str:
        row_id = _new_id("cevt")
        await self.pool.execute(
            """
            INSERT INTO credit_ledger_events (
                id, tenant_id, workspace_id, surface, payer, credit_type, created_at
            ) VALUES ($1, $2, $3, 'sage', 'workspace', 'usage', $4)
            """,
            row_id, self.tenant_id, self.workspace_id, created_at,
        )
        return row_id

    async def _table_ids(self, table: str, *, id_column: str = "id") -> set[str]:
        rows = await self.pool.fetch(f"SELECT {id_column} FROM {table}")
        return {str(row[id_column]) for row in rows}

    # -- activity_ledger_events -------------------------------------------

    async def test_activity_ledger_events_old_deleted_recent_survives(self) -> None:
        old_id = await self._insert_activity_ledger_event(created_at=_ago(days=40))
        recent_id = await self._insert_activity_ledger_event(created_at=_ago(days=1))

        deleted = await control_plane_repository.prune_activity_ledger_events(
            cutoff=_ago(days=30), batch_size=500, max_batches=50
        )

        self.assertEqual(deleted, 1)
        remaining = await self._table_ids("activity_ledger_events")
        self.assertNotIn(old_id, remaining)
        self.assertIn(recent_id, remaining)

    # -- agent_secret_access_events (security audit trail) ----------------

    async def test_secret_access_events_respects_its_own_longer_window(self) -> None:
        # 90 days old: past the general 30-day telemetry window but well
        # inside the 180-day default audit-trail window -- must survive.
        mid_age_id = await self._insert_secret_access_event(created_at=_ago(days=90))
        very_old_id = await self._insert_secret_access_event(created_at=_ago(days=200))

        deleted = await control_plane_repository.prune_agent_secret_access_events(
            cutoff=_ago(days=telemetry_retention_service.DEFAULT_SECRET_ACCESS_AUDIT_RETENTION_DAYS),
            batch_size=500,
            max_batches=50,
        )

        self.assertEqual(deleted, 1)
        remaining = await self._table_ids("agent_secret_access_events")
        self.assertIn(mid_age_id, remaining, "90-day-old audit row must survive the 180-day default window")
        self.assertNotIn(very_old_id, remaining)

    # -- agent_sessions -----------------------------------------------------

    async def test_agent_sessions_only_deletes_already_expired_past_window(self) -> None:
        expired_long_ago = await self._insert_agent_session(expires_at=_ago(days=40))
        expired_recently = await self._insert_agent_session(expires_at=_ago(hours=1))
        not_yet_expired = await self._insert_agent_session(
            expires_at=datetime.now(timezone.utc) + timedelta(days=1)
        )
        null_expiry = await self._insert_agent_session(expires_at=None)

        deleted = await control_plane_repository.prune_expired_agent_sessions(
            cutoff=_ago(days=30), batch_size=500, max_batches=50
        )

        self.assertEqual(deleted, 1)
        remaining = await self._table_ids("agent_sessions")
        self.assertNotIn(expired_long_ago, remaining)
        self.assertIn(expired_recently, remaining, "expired-but-within-window session must survive")
        self.assertIn(not_yet_expired, remaining, "a session that has not expired yet must never be deleted")
        self.assertIn(null_expiry, remaining, "a session with no expires_at must never be guessed at / deleted")

    # -- agent_traces / agent_trace_events (cascade) -------------------------

    async def test_finished_old_traces_deleted_and_cascade_their_events(self) -> None:
        finished_old = await self._insert_agent_trace(started_at=_ago(days=40), finished_at=_ago(days=35))
        await self._insert_agent_trace_event(finished_old, seq=1)
        await self._insert_agent_trace_event(finished_old, seq=2)

        finished_recent = await self._insert_agent_trace(started_at=_ago(days=2), finished_at=_ago(days=1))
        await self._insert_agent_trace_event(finished_recent, seq=1)

        unfinished_old = await self._insert_agent_trace(started_at=_ago(days=90), finished_at=None)
        await self._insert_agent_trace_event(unfinished_old, seq=1)

        deleted = await control_plane_repository.prune_finished_agent_traces(
            cutoff=_ago(days=14), batch_size=500, max_batches=50
        )

        self.assertEqual(deleted, 1)
        remaining_traces = await self._table_ids("agent_traces")
        self.assertNotIn(finished_old, remaining_traces)
        self.assertIn(finished_recent, remaining_traces)
        self.assertIn(unfinished_old, remaining_traces, "an unfinished trace must never be deleted regardless of age")

        remaining_events = await self._table_ids("agent_trace_events", id_column="id")
        remaining_trace_ids = await self.pool.fetch("SELECT trace_id FROM agent_trace_events")
        remaining_trace_id_set = {str(r["trace_id"]) for r in remaining_trace_ids}
        self.assertNotIn(finished_old, remaining_trace_id_set, "cascade must remove the deleted trace's events")
        self.assertIn(finished_recent, remaining_trace_id_set)
        self.assertIn(unfinished_old, remaining_trace_id_set)
        self.assertEqual(len(remaining_events), 2)  # finished_recent's 1 + unfinished_old's 1

    # -- runtime_outbox -----------------------------------------------------

    async def test_outbox_only_deletes_delivered_past_window(self) -> None:
        delivered_old = await self._insert_outbox_event(created_at=_ago(days=20), delivered_at=_ago(days=10))
        delivered_recent = await self._insert_outbox_event(created_at=_ago(days=1), delivered_at=_ago(hours=1))
        undelivered_old = await self._insert_outbox_event(created_at=_ago(days=30), delivered_at=None)
        poisoned_old = await self._insert_outbox_event(
            created_at=_ago(days=30), delivered_at=None, poisoned_at=_ago(days=20)
        )

        deleted = await run_state_repository.prune_delivered_outbox_events(
            cutoff=_ago(days=7), batch_size=500, max_batches=50
        )

        self.assertEqual(deleted, 1)
        remaining = await self._table_ids("runtime_outbox", id_column="event_id")
        self.assertNotIn(delivered_old, remaining)
        self.assertIn(delivered_recent, remaining)
        self.assertIn(undelivered_old, remaining, "an undelivered event must never be deleted, no matter how old")
        self.assertIn(poisoned_old, remaining, "a poisoned (dead-lettered) event is out of this job's scope")

    # -- runtime_sessions -----------------------------------------------------

    async def test_runtime_sessions_only_deletes_already_expired_past_window(self) -> None:
        expired_long_ago = await self._insert_runtime_session(expires_at=_ago(days=40))
        not_yet_expired = await self._insert_runtime_session(
            expires_at=datetime.now(timezone.utc) + timedelta(days=1)
        )

        deleted = await session_service.prune_expired_runtime_sessions(
            cutoff=_ago(days=30), batch_size=500, max_batches=50
        )

        self.assertEqual(deleted, 1)
        remaining = await self._table_ids("runtime_sessions", id_column="session_id")
        self.assertNotIn(expired_long_ago, remaining)
        self.assertIn(not_yet_expired, remaining)

    # -- batching bound -------------------------------------------------------

    async def test_batching_bounds_rows_deleted_per_call(self) -> None:
        for _ in range(23):
            await self._insert_activity_ledger_event(created_at=_ago(days=40))

        deleted = await control_plane_repository.prune_activity_ledger_events(
            cutoff=_ago(days=30), batch_size=5, max_batches=3
        )

        self.assertEqual(deleted, 15, "must stop at batch_size * max_batches, not drain the whole backlog in one call")
        remaining = await self.pool.fetchval("SELECT COUNT(*) FROM activity_ledger_events")
        self.assertEqual(remaining, 8)

        # A second call picks up where the first left off.
        deleted_second = await control_plane_repository.prune_activity_ledger_events(
            cutoff=_ago(days=30), batch_size=5, max_batches=3
        )
        self.assertEqual(deleted_second, 8)
        remaining_after = await self.pool.fetchval("SELECT COUNT(*) FROM activity_ledger_events")
        self.assertEqual(remaining_after, 0)

    # -- full sweep: protected data is never touched -------------------------

    async def test_full_sweep_never_touches_conversation_or_financial_data(self) -> None:
        turn_id = await self._insert_agent_turn(created_at=_ago(days=400), content="a real conversation turn")
        credit_id = await self._insert_credit_ledger_event(created_at=_ago(days=400))
        old_activity_id = await self._insert_activity_ledger_event(created_at=_ago(days=400))

        os.environ["EMPYRALIS_TELEMETRY_RETENTION_ENABLED"] = "1"
        os.environ["EMPYRALIS_ACTIVITY_LEDGER_RETENTION_DAYS"] = "30"
        os.environ["EMPYRALIS_TELEMETRY_RETENTION_BATCH_SIZE"] = "500"
        os.environ["EMPYRALIS_TELEMETRY_RETENTION_MAX_BATCHES_PER_TABLE"] = "50"
        try:
            result = await telemetry_retention_service.run_telemetry_retention_sweep()
        finally:
            for key in (
                "EMPYRALIS_TELEMETRY_RETENTION_ENABLED",
                "EMPYRALIS_ACTIVITY_LEDGER_RETENTION_DAYS",
                "EMPYRALIS_TELEMETRY_RETENTION_BATCH_SIZE",
                "EMPYRALIS_TELEMETRY_RETENTION_MAX_BATCHES_PER_TABLE",
            ):
                os.environ.pop(key, None)

        self.assertTrue(result.enabled)
        self.assertEqual(result.deleted.get("activity_ledger_events"), 1)
        self.assertEqual(result.errors, {})

        turn_row = await self.pool.fetchrow("SELECT content FROM agent_turns WHERE id = $1", turn_id)
        self.assertIsNotNone(turn_row, "a conversation turn must survive the sweep regardless of age")
        self.assertEqual(turn_row["content"], "a real conversation turn")

        credit_row = await self.pool.fetchrow("SELECT id FROM credit_ledger_events WHERE id = $1", credit_id)
        self.assertIsNotNone(credit_row, "a financial ledger event must survive the sweep regardless of age")

        remaining_activity = await self._table_ids("activity_ledger_events")
        self.assertNotIn(old_activity_id, remaining_activity)

    async def test_full_sweep_disabled_flag_deletes_nothing(self) -> None:
        old_id = await self._insert_activity_ledger_event(created_at=_ago(days=400))

        os.environ["EMPYRALIS_TELEMETRY_RETENTION_ENABLED"] = "0"
        try:
            result = await telemetry_retention_service.run_telemetry_retention_sweep()
        finally:
            os.environ.pop("EMPYRALIS_TELEMETRY_RETENTION_ENABLED", None)

        self.assertFalse(result.enabled)
        self.assertEqual(result.deleted, {})
        remaining = await self._table_ids("activity_ledger_events")
        self.assertIn(old_id, remaining, "disabled sweep must not delete anything")

    # -- env var configuration -------------------------------------------

    async def test_env_var_overrides_and_defaults(self) -> None:
        self.assertEqual(
            telemetry_retention_service.activity_ledger_retention_days(),
            telemetry_retention_service.DEFAULT_ACTIVITY_LEDGER_RETENTION_DAYS,
        )
        os.environ["EMPYRALIS_ACTIVITY_LEDGER_RETENTION_DAYS"] = "7"
        try:
            self.assertEqual(telemetry_retention_service.activity_ledger_retention_days(), 7)
        finally:
            os.environ.pop("EMPYRALIS_ACTIVITY_LEDGER_RETENTION_DAYS", None)
        self.assertEqual(
            telemetry_retention_service.activity_ledger_retention_days(),
            telemetry_retention_service.DEFAULT_ACTIVITY_LEDGER_RETENTION_DAYS,
        )

        # Secret-access audit window is independent of the general window.
        self.assertGreater(
            telemetry_retention_service.secret_access_audit_retention_days(),
            telemetry_retention_service.activity_ledger_retention_days(),
        )

        # Garbage input falls back to the default rather than crashing the sweep.
        os.environ["EMPYRALIS_TELEMETRY_RETENTION_BATCH_SIZE"] = "not-a-number"
        try:
            self.assertEqual(
                telemetry_retention_service.retention_batch_size(),
                telemetry_retention_service.DEFAULT_BATCH_SIZE,
            )
        finally:
            os.environ.pop("EMPYRALIS_TELEMETRY_RETENTION_BATCH_SIZE", None)

        self.assertTrue(telemetry_retention_service.telemetry_retention_enabled())
        os.environ["EMPYRALIS_TELEMETRY_RETENTION_ENABLED"] = "0"
        try:
            self.assertFalse(telemetry_retention_service.telemetry_retention_enabled())
        finally:
            os.environ.pop("EMPYRALIS_TELEMETRY_RETENTION_ENABLED", None)


class TelemetryRetentionLoopTests(unittest.IsolatedAsyncioTestCase):
    """telemetry_retention_service.telemetry_retention_loop() is the piece
    that actually wires run_telemetry_retention_sweep() up to run on a
    schedule -- mirrors server_modules/tests/test_gateway_state_repository_
    prune.py's GatewayStatePruneLoopTests for the sibling MAN-140 loop. Uses
    a mocked sweep function, so unlike the rest of this file it does not
    need a live Postgres connection."""

    async def test_loop_invokes_sweep_and_cancels_cleanly(self) -> None:
        import asyncio
        from unittest.mock import patch

        call_count = 0

        async def _fake_sweep():
            nonlocal call_count
            call_count += 1
            return telemetry_retention_service.TelemetryRetentionResult(started_at="now")

        with patch.object(telemetry_retention_service, "retention_interval_seconds", return_value=60), patch.object(
            telemetry_retention_service, "run_telemetry_retention_sweep", side_effect=_fake_sweep
        ):
            task = asyncio.create_task(telemetry_retention_service.telemetry_retention_loop())
            for _ in range(100):
                if call_count >= 1:
                    break
                await asyncio.sleep(0.01)
            self.assertGreaterEqual(call_count, 1, "loop should call run_telemetry_retention_sweep on its first tick")

            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_loop_survives_sweep_failure_and_keeps_running(self) -> None:
        import asyncio
        from unittest.mock import patch

        call_count = 0

        async def _fake_sweep_raises():
            nonlocal call_count
            call_count += 1
            raise RuntimeError("simulated sweep failure")

        with patch.object(telemetry_retention_service, "retention_interval_seconds", return_value=60), patch.object(
            telemetry_retention_service, "run_telemetry_retention_sweep", side_effect=_fake_sweep_raises
        ):
            task = asyncio.create_task(telemetry_retention_service.telemetry_retention_loop())
            for _ in range(100):
                if call_count >= 1:
                    break
                await asyncio.sleep(0.01)
            self.assertGreaterEqual(call_count, 1)
            self.assertFalse(task.done(), "the loop must still be alive after a failed sweep attempt")

            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task


if __name__ == "__main__":
    unittest.main()
