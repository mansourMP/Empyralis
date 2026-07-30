from __future__ import annotations

import asyncio
import importlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from server_modules import gateway_state_repository


def _iso(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) - delta).isoformat()


class GatewayStatePruneTests(unittest.TestCase):
    """MAN-140 defense-in-depth: gateway_state_repository.prune_gateway_state()
    is the first DELETE-based cleanup either gateway_sessions or gateway_events
    has ever had (sweep_stale_gateway_sessions only UPDATEs status -> 'expired',
    it never removes a row — see that function's own module for why it alone
    does nothing to bound file size). Neither table is covered by MAN-80's
    retention job either (data_retention_service.DATA_STORE_CATALOG doesn't
    list them; that job is a separate, Postgres/workspace-scoped system).

    These tests exercise prune_gateway_state() against a real temp SQLite DB
    (same schema init path production uses, gateway_state_repository.
    init_gateway_state_db) rather than mocking the DB layer, because the one
    property that actually matters here — a row for a LIVE session is never
    deleted no matter how old it is — is exactly the kind of thing a mock
    would happily let slip past unnoticed.
    """

    def setUp(self) -> None:
        global gateway_state_repository
        gateway_state_repository = importlib.import_module("server_modules.gateway_state_repository")
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "gateway-state.sqlite3"
        gateway_state_repository.init_gateway_state_db(self.db_path)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def _insert_session(self, session_id: str, *, status: str, age: timedelta, gateway_id: str = "gw-1") -> None:
        conn = gateway_state_repository._connect(self.db_path)
        try:
            ts = _iso(age)
            conn.execute(
                """
                INSERT INTO gateway_sessions (
                    session_id, gateway_id, device_id, tenant_id, workspace_id, user_id,
                    session_token_hash, status, metadata, created_at, updated_at, expires_at,
                    last_seq, last_ack
                ) VALUES (?, ?, 'd1', 't1', 'w1', 'u1', ?, ?, '{}', ?, ?, ?, 0, 0)
                """,
                (session_id, gateway_id, f"hash-{session_id}", status, ts, ts, ts),
            )
            conn.commit()
        finally:
            conn.close()

    def _insert_event(self, gateway_id: str, session_id: str, *, age: timedelta) -> None:
        conn = gateway_state_repository._connect(self.db_path)
        try:
            conn.execute(
                """
                INSERT INTO gateway_events (
                    gateway_id, session_id, direction, frame_kind, message_type, payload, created_at
                ) VALUES (?, ?, 'inbound', 'request', 'gateway.heartbeat', '{}', ?)
                """,
                (gateway_id, session_id, _iso(age)),
            )
            conn.commit()
        finally:
            conn.close()

    def _session_ids(self, gateway_id: str = "gw-1") -> set[str]:
        return {
            row["session_id"]
            for row in gateway_state_repository.list_gateway_sessions(
                gateway_id, include_revoked=True, db_path=self.db_path
            )
        }

    def _event_count(self) -> int:
        conn = gateway_state_repository._connect(self.db_path)
        try:
            return int(conn.execute("SELECT COUNT(*) FROM gateway_events").fetchone()[0])
        finally:
            conn.close()

    def test_old_terminal_sessions_are_deleted_recent_ones_survive(self) -> None:
        self._insert_session("sess-old-expired", status="expired", age=timedelta(days=40))
        self._insert_session("sess-old-disconnected", status="disconnected", age=timedelta(days=40))
        self._insert_session("sess-old-revoked", status="revoked", age=timedelta(days=40))
        self._insert_session("sess-recent-expired", status="expired", age=timedelta(days=1))

        result = gateway_state_repository.prune_gateway_state(
            event_retention_days=14, session_retention_days=30, db_path=self.db_path
        )

        remaining = self._session_ids()
        self.assertNotIn("sess-old-expired", remaining)
        self.assertNotIn("sess-old-disconnected", remaining)
        self.assertNotIn("sess-old-revoked", remaining)
        self.assertIn("sess-recent-expired", remaining)
        self.assertEqual(result["gateway_sessions_deleted"], 3)

    def test_live_session_is_never_pruned_regardless_of_age(self) -> None:
        """The critical safety property: a 'connected' (or 'pending') row is
        never a candidate, no matter how stale updated_at looks, because
        gateway_protocol_service._validate_gateway_binding() and is_stale()
        both still key off session_id for an actually-live connection — the
        prune job has no way to know whether a "connected"-status row 90 days
        old is a zombie or a socket that has genuinely been open that long.
        """
        self._insert_session("sess-old-connected", status="connected", age=timedelta(days=90))
        self._insert_session("sess-old-pending", status="pending", age=timedelta(days=90))

        result = gateway_state_repository.prune_gateway_state(
            event_retention_days=14, session_retention_days=30, db_path=self.db_path
        )

        remaining = self._session_ids()
        self.assertIn("sess-old-connected", remaining)
        self.assertIn("sess-old-pending", remaining)
        self.assertEqual(result["gateway_sessions_deleted"], 0)

    def test_old_events_are_deleted_recent_ones_survive_across_gateways(self) -> None:
        self._insert_event("gw-1", "sess-a", age=timedelta(days=30))
        self._insert_event("gw-2", "sess-b", age=timedelta(days=30))
        self._insert_event("gw-1", "sess-a", age=timedelta(hours=1))

        result = gateway_state_repository.prune_gateway_state(
            event_retention_days=14, session_retention_days=30, db_path=self.db_path
        )

        self.assertEqual(result["gateway_events_deleted"], 2)
        self.assertEqual(self._event_count(), 1)

    def test_prune_is_idempotent_and_safe_to_run_with_nothing_to_prune(self) -> None:
        self._insert_session("sess-fresh", status="connected", age=timedelta(minutes=1))
        self._insert_event("gw-1", "sess-fresh", age=timedelta(minutes=1))

        first = gateway_state_repository.prune_gateway_state(
            event_retention_days=14, session_retention_days=30, db_path=self.db_path
        )
        second = gateway_state_repository.prune_gateway_state(
            event_retention_days=14, session_retention_days=30, db_path=self.db_path
        )

        self.assertEqual(first, {"gateway_events_deleted": 0, "gateway_sessions_deleted": 0})
        self.assertEqual(second, {"gateway_events_deleted": 0, "gateway_sessions_deleted": 0})
        self.assertIn("sess-fresh", self._session_ids())
        self.assertEqual(self._event_count(), 1)


class GatewayStatePruneLoopTests(unittest.IsolatedAsyncioTestCase):
    """server_modules.shared._gateway_state_prune_loop is the piece that
    actually wires prune_gateway_state() up to run — the MAN-80 framing
    ("retention job built but never called") is exactly the failure mode
    this guards against: a correct prune function nobody ever invokes still
    leaves the disk filling up. This asserts the loop calls it (via
    asyncio.to_thread, off the event loop — see the loop's own comment for
    why that specifically matters on a route that shares an event loop with
    every live gateway websocket) and shuts down cleanly on cancellation,
    without needing to wait out a real multi-hour sleep interval.
    """

    async def test_loop_invokes_prune_and_cancels_cleanly(self) -> None:
        from server_modules import shared

        call_count = 0

        def _fake_prune(*, event_retention_days, session_retention_days):
            nonlocal call_count
            call_count += 1
            return {"gateway_events_deleted": 0, "gateway_sessions_deleted": 0}

        with patch.object(shared, "_GATEWAY_STATE_PRUNE_INTERVAL_SECONDS", 60), patch(
            "server_modules.gateway_state_repository.prune_gateway_state",
            side_effect=_fake_prune,
        ):
            task = asyncio.create_task(shared._gateway_state_prune_loop())
            for _ in range(100):
                if call_count >= 1:
                    break
                await asyncio.sleep(0.01)
            self.assertGreaterEqual(call_count, 1, "prune loop should call prune_gateway_state on its first tick")

            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_loop_survives_prune_failure_and_keeps_running(self) -> None:
        """A single bad prune (e.g. a locked DB file) must not kill the
        background loop forever — same "never let one failure take down a
        long-running loop" posture as HeartbeatLoop.start on the gateway
        client side (empyralis-gateway/src/cloud/heartbeat.ts)."""
        from server_modules import shared

        call_count = 0

        def _fake_prune_raises(*, event_retention_days, session_retention_days):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("simulated prune failure")

        with patch.object(shared, "_GATEWAY_STATE_PRUNE_INTERVAL_SECONDS", 60), patch(
            "server_modules.gateway_state_repository.prune_gateway_state",
            side_effect=_fake_prune_raises,
        ):
            task = asyncio.create_task(shared._gateway_state_prune_loop())
            for _ in range(100):
                if call_count >= 1:
                    break
                await asyncio.sleep(0.01)
            self.assertGreaterEqual(call_count, 1)
            self.assertFalse(task.done(), "the loop must still be alive after a failed prune attempt")

            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task


if __name__ == "__main__":
    unittest.main()
