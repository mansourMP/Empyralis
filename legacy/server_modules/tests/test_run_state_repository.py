import asyncio
import time
import threading
import unittest
from datetime import datetime
from unittest.mock import patch

from server_modules import run_state_repository


class _FakePool:
    def __init__(self, *, fetchrow_result=None, fetchrow_results=None, fetch_result=None, fetch_results=None, execute_error: Exception | None = None):
        self.fetchrow_result = fetchrow_result
        self.fetchrow_results = list(fetchrow_results or [])
        self.fetch_result = list(fetch_result or [])
        self.fetch_results = list(fetch_results or [])
        self.execute_error = execute_error
        self.execute_calls = []
        self.fetchrow_calls = []
        self.fetch_calls = []

    async def execute(self, query, *args):
        self.execute_calls.append((query, args))
        if self.execute_error is not None:
            raise self.execute_error
        return "OK"

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query, args))
        if self.execute_error is not None:
            raise self.execute_error
        if self.fetchrow_results:
            return self.fetchrow_results.pop(0)
        return self.fetchrow_result

    async def fetch(self, query, *args):
        self.fetch_calls.append((query, args))
        if self.execute_error is not None:
            raise self.execute_error
        if self.fetch_results:
            return list(self.fetch_results.pop(0))
        return list(self.fetch_result)


class RunStateRepositoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._rust_gate = patch.object(
            run_state_repository.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={"decision": "allow"},
        )
        self._rust_gate.start()
        self.addCleanup(self._rust_gate.stop)

    async def test_create_live_run_initial_returns_version_zero_registration(self):
        pool = _FakePool(fetchrow_result={"version": 0, "registered_at": "2026-04-12T00:00:00Z"})
        with patch("server_modules.run_state_repository.runtime_db.get_pool", return_value=pool):
            result = await run_state_repository.create_live_run_initial(
                "run-1",
                "workspace-1",
                "tenant-1",
                "starting",
                {"run_id": "run-1", "status": "starting"},
                "trace-1",
            )

        self.assertEqual(result["version"], 0)
        self.assertEqual(result["registered_at"], "2026-04-12T00:00:00Z")
        self.assertEqual(len(pool.fetchrow_calls), 1)

    async def test_upsert_live_run_writes_to_postgres_pool(self):
        pool = _FakePool()
        with patch("server_modules.run_state_repository.runtime_db.get_pool", return_value=pool):
            result = await run_state_repository.upsert_live_run(
                "run-1",
                "workspace-1",
                "tenant-1",
                "queued",
                {"run_id": "run-1", "status": "queued"},
                "trace-1",
            )

        self.assertIsNone(result)
        self.assertGreaterEqual(len(pool.execute_calls), 1)
        _, args = pool.execute_calls[-1]
        self.assertEqual(args[0], "run-1")
        self.assertEqual(args[1], "workspace-1")
        self.assertEqual(args[2], "tenant-1")
        self.assertEqual(args[3], "queued")

    async def test_update_live_run_if_version_matches_advances_version(self):
        pool = _FakePool(fetchrow_results=[{"version": 1}])
        with patch("server_modules.run_state_repository.runtime_db.get_pool", return_value=pool):
            result = await run_state_repository.update_live_run_if_version_matches(
                "run-1",
                "workspace-1",
                "tenant-1",
                "queued_local",
                {"run_id": "run-1", "status": "queued_local", "_durable_version": 0},
                "trace-1",
                expected_version=0,
            )

        self.assertEqual(result, 1)
        self.assertEqual(len(pool.fetchrow_calls), 1)
        self.assertIn("UPDATE live_runs", pool.fetchrow_calls[0][0])

    async def test_update_live_run_if_version_matches_rejects_stale_snapshot(self):
        pool = _FakePool(
            fetchrow_results=[
                None,
                {
                    "run_id": "run-1",
                    "workspace_id": "workspace-1",
                    "tenant_id": "tenant-1",
                    "state": "executing",
                    "payload": {"run_id": "run-1", "status": "executing", "_durable_version": 2},
                    "trace_id": "trace-1",
                    "version": 2,
                    "registered_at": "2026-04-12T00:00:00Z",
                },
            ]
        )
        with patch("server_modules.run_state_repository.runtime_db.get_pool", return_value=pool):
            result = await run_state_repository.update_live_run_if_version_matches(
                "run-1",
                "workspace-1",
                "tenant-1",
                "queued_local",
                {"run_id": "run-1", "status": "queued_local", "_durable_version": 1},
                "trace-1",
                expected_version=1,
            )

        self.assertIsNone(result)
        self.assertEqual(len(pool.fetchrow_calls), 2)

    async def test_get_live_run_prefers_postgres_payload(self):
        pool = _FakePool(
            fetchrow_result={
                "run_id": "run-1",
                "workspace_id": "workspace-1",
                "tenant_id": "tenant-1",
                "state": "executing",
                "payload": {"run_id": "run-1", "status": "executing"},
                "trace_id": "trace-1",
                "version": 3,
                "registered_at": "2026-04-12T00:00:00Z",
            }
        )
        with patch("server_modules.run_state_repository.runtime_db.get_pool", return_value=pool):
            result = await run_state_repository.get_live_run("run-1")

        self.assertEqual(result["run_id"], "run-1")
        self.assertEqual(result["status"], "executing")
        self.assertEqual(result["_durable_version"], 3)
        self.assertEqual(len(pool.fetchrow_calls), 1)

    async def test_get_live_run_returns_none_when_postgres_unavailable(self):
        with patch("server_modules.run_state_repository.runtime_db.get_pool", return_value=None):
            result = await run_state_repository.get_live_run("run-sqlite")

        self.assertIsNone(result)

    async def test_get_live_run_raises_when_durable_runtime_is_required(self):
        with (
            patch("server_modules.run_state_repository.runtime_db.durable_runtime_required", return_value=True),
            patch(
                "server_modules.run_state_repository.runtime_db.require_durable_pool",
                side_effect=run_state_repository.runtime_db.DurableRuntimeConfigurationError(
                    "Postgres is required for durable run state during get_live_run"
                ),
            ),
        ):
            with self.assertRaises(run_state_repository.RunStatePersistenceError):
                await run_state_repository.get_live_run("run-sqlite")

    async def test_create_or_update_approval_request_returns_durable_row(self):
        pool = _FakePool(
            fetchrow_result={
                "run_id": "run-1",
                "step_id": "approval-1",
                "approval_id": "approval-1",
                "status": "requested",
                "requested_at": "2026-04-12T10:00:00Z",
                "resolved_at": None,
                "resolution": None,
                "actor": "system",
                "trace_id": "trace-1",
                "request_payload": {"prompt": "Approve deploy", "workspace_id": "workspace-1", "tenant_id": "tenant-1"},
                "decision_payload": {},
                "metadata": {"owner_user_id": "user-1"},
                "expires_at": "2026-04-12T10:05:00Z",
                "updated_at": "2026-04-12T10:00:00Z",
                "version": 0,
            }
        )
        with patch("server_modules.run_state_repository.runtime_db.get_pool", return_value=pool):
            result = await run_state_repository.create_or_update_approval_request(
                "run-1",
                "approval-1",
                {"prompt": "Approve deploy", "workspace_id": "workspace-1", "tenant_id": "tenant-1"},
                "system",
                "trace-1",
                metadata={"owner_user_id": "user-1"},
                expires_at="2026-04-12T10:05:00Z",
            )

        self.assertEqual(result["approval_id"], "approval-1")
        self.assertEqual(result["status"], "requested")
        self.assertEqual(result["workspace_id"], "workspace-1")
        self.assertEqual(result["owner_user_id"], "user-1")

    async def test_create_or_update_approval_request_coerces_iso_timestamps_for_postgres(self):
        pool = _FakePool(
            fetchrow_result={
                "run_id": "run-1",
                "step_id": "approval-1",
                "approval_id": "approval-1",
                "status": "requested",
                "requested_at": "2026-04-12T10:00:00Z",
                "resolved_at": None,
                "resolution": None,
                "actor": "system",
                "trace_id": "trace-1",
                "request_payload": {"prompt": "Approve deploy", "workspace_id": "workspace-1", "tenant_id": "tenant-1"},
                "decision_payload": {},
                "metadata": {},
                "expires_at": "2026-04-12T10:05:00Z",
                "updated_at": "2026-04-12T10:00:00Z",
                "version": 0,
            }
        )
        with patch("server_modules.run_state_repository.runtime_db.require_durable_pool", return_value=pool):
            await run_state_repository.create_or_update_approval_request(
                "run-1",
                "approval-1",
                {
                    "prompt": "Approve deploy",
                    "workspace_id": "workspace-1",
                    "tenant_id": "tenant-1",
                    "requested_at": "2026-04-12T10:00:00Z",
                    "expires_at": "2026-04-12T10:05:00Z",
                },
                "system",
                "trace-1",
            )

        _, args = pool.fetchrow_calls[-1]
        self.assertIsInstance(args[2], datetime)
        self.assertIsInstance(args[7], datetime)

    async def test_resolve_approval_if_pending_blocks_duplicate_resolution(self):
        pool = _FakePool(
            fetchrow_results=[
                None,
                {
                    "run_id": "run-1",
                    "step_id": "approval-1",
                    "approval_id": "approval-1",
                    "status": "resolved",
                    "requested_at": "2026-04-12T10:00:00Z",
                    "resolved_at": "2026-04-12T10:01:00Z",
                    "resolution": "approved",
                    "actor": "user-1",
                    "trace_id": "trace-1",
                    "request_payload": {"prompt": "Approve deploy"},
                    "decision_payload": {"decision": "approved"},
                    "metadata": {},
                    "expires_at": None,
                    "updated_at": "2026-04-12T10:01:00Z",
                    "version": 1,
                },
            ]
        )
        with patch("server_modules.run_state_repository.runtime_db.get_pool", return_value=pool):
            result = await run_state_repository.resolve_approval_if_pending(
                "run-1",
                "approval-1",
                "approved",
                "user-1",
                "trace-1",
            )

        self.assertIsNone(result)
        self.assertEqual(len(pool.fetchrow_calls), 2)

    async def test_record_approval_resolution_uses_valid_jsonb_default(self):
        pool = _FakePool(
            fetchrow_result={
                "run_id": "run-1",
                "step_id": "approval-1",
                "approval_id": "approval-1",
                "status": "approved",
                "requested_at": "2026-04-12T10:00:00Z",
                "resolved_at": "2026-04-12T10:01:00Z",
                "resolution": "approved",
                "actor": "user-1",
                "trace_id": "trace-1",
                "request_payload": {"prompt": "Approve local action"},
                "decision_payload": {"decision": "approved"},
                "metadata": {},
                "expires_at": None,
                "updated_at": "2026-04-12T10:01:00Z",
                "version": 1,
            }
        )
        with patch("server_modules.run_state_repository.runtime_db.require_durable_pool", return_value=pool):
            result = await run_state_repository.record_approval_resolution(
                "run-1",
                "approval-1",
                "approved",
                "user-1",
                "trace-1",
                note="ok",
            )

        self.assertEqual(result["approval_id"], "approval-1")
        query, args = pool.fetchrow_calls[-1]
        self.assertIn("'{}'::jsonb", query)
        self.assertNotIn("'{{}}'::jsonb", query)
        self.assertIn('"decision":"approved"', args[-1])

    async def test_list_pending_approvals_returns_requested_rows(self):
        pool = _FakePool(
            fetch_result=[
                {
                    "run_id": "run-1",
                    "step_id": "approval-1",
                    "approval_id": "approval-1",
                    "status": "requested",
                    "requested_at": "2026-04-12T10:00:00Z",
                    "resolved_at": None,
                    "resolution": None,
                    "actor": "system",
                    "trace_id": "trace-1",
                    "request_payload": {"prompt": "Approve deploy", "workspace_id": "workspace-1"},
                    "decision_payload": {},
                    "metadata": {"owner_user_id": "user-1"},
                    "expires_at": None,
                    "updated_at": "2026-04-12T10:00:00Z",
                    "version": 0,
                }
            ]
        )
        with patch("server_modules.run_state_repository.runtime_db.get_pool", return_value=pool):
            items = await run_state_repository.list_pending_approvals(limit=10)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["approval_id"], "approval-1")
        self.assertEqual(items[0]["status"], "requested")

    async def test_list_live_runs_page_applies_workspace_state_limit_and_offset(self):
        pool = _FakePool(
            fetch_result=[
                {
                    "run_id": "run-1",
                    "workspace_id": "workspace-1",
                    "tenant_id": "tenant-1",
                    "state": "running",
                    "payload": {"run_id": "run-1", "status": "running"},
                    "trace_id": "trace-1",
                    "version": 1,
                    "registered_at": "2026-04-12T00:00:00Z",
                }
            ]
        )
        with patch("server_modules.run_state_repository.runtime_db.get_pool", return_value=pool):
            items = await run_state_repository.list_live_runs_page(
                limit=25,
                offset=50,
                workspace_id="workspace-1",
                states=["running"],
            )

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["run_id"], "run-1")
        query, args = pool.fetch_calls[-1]
        self.assertIn("LIMIT $3", query)
        self.assertEqual(args[0], "workspace-1")
        self.assertEqual(args[1], ["running"])
        self.assertEqual(args[2], 25)
        self.assertEqual(args[3], 50)

    async def test_count_hosted_live_runs_uses_bounded_workspace_count_query(self):
        pool = _FakePool(fetchrow_result={"count": 2})
        with patch("server_modules.run_state_repository.runtime_db.get_pool", return_value=pool):
            count = await run_state_repository.count_hosted_live_runs("workspace-1")

        self.assertEqual(count, 2)
        query, args = pool.fetchrow_calls[-1]
        self.assertIn("COUNT(*)::int AS count", query)
        self.assertEqual(args[0], "workspace-1")
        self.assertIn("completed", args[1])

    async def test_list_pending_approvals_page_filters_by_workspace(self):
        pool = _FakePool(
            fetch_result=[
                {
                    "run_id": "run-1",
                    "step_id": "approval-1",
                    "approval_id": "approval-1",
                    "status": "requested",
                    "requested_at": "2026-04-12T10:00:00Z",
                    "resolved_at": None,
                    "resolution": None,
                    "actor": "system",
                    "trace_id": "trace-1",
                    "request_payload": {"prompt": "Approve deploy", "workspace_id": "workspace-1"},
                    "decision_payload": {},
                    "metadata": {"owner_user_id": "user-1"},
                    "expires_at": None,
                    "updated_at": "2026-04-12T10:00:00Z",
                    "version": 0,
                }
            ]
        )
        with patch("server_modules.run_state_repository.runtime_db.get_pool", return_value=pool):
            items = await run_state_repository.list_pending_approvals_page(
                limit=20,
                offset=10,
                workspace_id="workspace-1",
            )

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["workspace_id"], "workspace-1")
        query, args = pool.fetch_calls[-1]
        self.assertIn("workspace_id", query)
        self.assertEqual(args[0], "workspace-1")
        self.assertEqual(args[1], 20)
        self.assertEqual(args[2], 10)

    async def test_record_transition_raises_when_postgres_fails(self):
        pool = _FakePool(execute_error=RuntimeError("db down"))
        with patch("server_modules.run_state_repository.runtime_db.get_pool", return_value=pool):
            with self.assertRaises(run_state_repository.RunStatePersistenceError):
                await run_state_repository.record_transition(
                    "run-1",
                    "queued",
                    "executing",
                    "runtime",
                    "trace-1",
                )
        self.assertGreaterEqual(len(pool.execute_calls), 1)

    async def test_archive_run_ensures_archive_table_before_insert(self):
        pool = _FakePool()
        with patch("server_modules.run_state_repository.runtime_db.require_durable_pool", return_value=pool):
            await run_state_repository.archive_run(
                "run-1",
                "completed",
                {
                    "run_id": "run-1",
                    "workspace_id": "workspace-1",
                    "tenant_id": "tenant-1",
                    "status": "completed",
                },
                "trace-1",
            )

        queries = [query for query, _args in pool.execute_calls]
        self.assertTrue(any("CREATE TABLE IF NOT EXISTS run_archive" in query for query in queries))
        self.assertIn("INSERT INTO run_archive", queries[-1])

    async def test_archive_run_claim_and_release_are_idempotent_calls(self):
        pool = _FakePool(fetchrow_results=[{"run_id": "run-1"}, {"run_id": "run-1"}])
        with patch("server_modules.run_state_repository.runtime_db.get_pool", return_value=pool):
            await run_state_repository.archive_run(
                "run-1",
                "completed",
                {
                    "run_id": "run-1",
                    "workspace_id": "workspace-1",
                    "tenant_id": "tenant-1",
                    "status": "completed",
                    "context": {"workspace_id": "workspace-1", "tenant_id": "tenant-1"},
                },
                "trace-1",
            )
            await run_state_repository.claim_run("run-1", "worker-1", 30, "trace-2", lease_id="lease-1")
            released = await run_state_repository.release_claim("run-1", lease_id="lease-1")

        self.assertTrue(released)
        self.assertTrue(any("run_archive" in query for query, _args in pool.execute_calls))
        self.assertTrue(any("local_queue_claims" in query for query, _args in pool.fetchrow_calls))
        self.assertTrue(any("DELETE FROM local_queue_claims" in query for query, _args in pool.fetchrow_calls))

    async def test_claim_run_rejects_overwriting_an_active_claim(self):
        pool = _FakePool(fetchrow_result=None)
        with patch("server_modules.run_state_repository.runtime_db.get_pool", return_value=pool):
            with self.assertRaises(run_state_repository.RunClaimConflictError):
                await run_state_repository.claim_run("run-1", "worker-2", 30, "trace-2", lease_id="lease-2")

        self.assertEqual(len(pool.fetchrow_calls), 1)

    def test_sync_upsert_live_run_raises_when_postgres_write_fails(self):
        pool = _FakePool(execute_error=RuntimeError("db down"))
        with patch("server_modules.run_state_repository.runtime_db.get_pool", return_value=pool):
            with self.assertRaises(run_state_repository.RunStatePersistenceError):
                run_state_repository.sync_upsert_live_run(
                    "run-1",
                    "workspace-1",
                    "tenant-1",
                    "queued",
                    {"run_id": "run-1", "status": "queued"},
                    "trace-1",
                )

    def test_sync_list_live_runs_raises_when_durable_runtime_is_required(self):
        with (
            patch("server_modules.run_state_repository.runtime_db.durable_runtime_required", return_value=True),
            patch(
                "server_modules.run_state_repository.runtime_db.require_durable_pool",
                side_effect=run_state_repository.runtime_db.DurableRuntimeConfigurationError(
                    "Postgres is required for durable run state during list_live_runs"
                ),
            ),
        ):
            with self.assertRaises(run_state_repository.RunStatePersistenceError):
                run_state_repository.sync_list_live_runs()

    async def test_persist_and_claim_outbox_events_round_trip_through_postgres(self):
        pool = _FakePool(
            fetch_result=[
                {
                    "event_id": "evt-1",
                    "event_type": "approval_resolved",
                    "tenant_id": "tenant-1",
                    "workspace_id": "ws-1",
                    "run_id": "run-1",
                    "machine_id": None,
                    "trace_id": "trace-1",
                    "idempotency_key": "approval_resolved:approval-1:approved",
                    "payload": {"approval_id": "approval-1"},
                    "created_at": "2026-04-07T00:00:00Z",
                    "delivered_at": None,
                    "last_replayed_at": None,
                    "retry_count": 0,
                    "last_delivery_error": None,
                    "last_attempted_at": None,
                    "next_attempt_at": None,
                    "poisoned_at": None,
                    "claim_token": "claim-1",
                    "claimed_by": "poller-1",
                    "claimed_at": "2026-04-07T00:00:10Z",
                    "claim_expires_at": "2026-04-07T00:00:40Z",
                }
            ],
            fetchrow_result={"event_id": "evt-1"},
        )
        with patch("server_modules.run_state_repository.runtime_db.get_pool", return_value=pool):
            await run_state_repository.persist_outbox_event(
                event_id="evt-1",
                event_type="approval_resolved",
                tenant_id="tenant-1",
                workspace_id="ws-1",
                run_id="run-1",
                machine_id=None,
                trace_id="trace-1",
                idempotency_key="approval_resolved:approval-1:approved",
                payload={"approval_id": "approval-1"},
            )
            items = await run_state_repository.claim_due_outbox_events(
                older_than_seconds=30,
                limit=10,
                claimed_by="poller-1",
                claim_ttl_seconds=30,
            )
            delivered = await run_state_repository.mark_outbox_event_delivered("evt-1", claim_token="claim-1")

        self.assertEqual(items[0]["event_id"], "evt-1")
        self.assertEqual(items[0]["claim_token"], "claim-1")
        self.assertTrue(delivered)
        self.assertGreaterEqual(len(pool.execute_calls), 3)
        self.assertIn("runtime_outbox", pool.execute_calls[0][0])
        self.assertIn("runtime_outbox", pool.execute_calls[1][0])
        self.assertEqual(len(pool.fetch_calls), 1)
        self.assertIn("FOR UPDATE SKIP LOCKED", pool.fetch_calls[0][0])
        self.assertIn("claim_token = $2", pool.fetchrow_calls[-1][0])

    async def test_record_outbox_delivery_failure_and_status_snapshot_include_retry_fields(self):
        pool = _FakePool(
            fetchrow_results=[
                {"event_id": "evt-1"},
                {
                    "undelivered_count": 2,
                    "poisoned_count": 1,
                    "claimed_count": 1,
                    "repeated_failure_count": 1,
                    "stuck_count": 1,
                    "total_retry_count": 5,
                    "max_retry_count": 3,
                },
                {
                    "event_id": "evt-1",
                    "last_delivery_error": "boom",
                    "last_attempted_at": "2026-04-07T00:01:00Z",
                    "retry_count": 3,
                },
            ]
        )
        with patch("server_modules.run_state_repository.runtime_db.get_pool", return_value=pool):
            recorded = await run_state_repository.record_outbox_delivery_failure(
                "evt-1",
                claim_token="claim-1",
                error_text="boom",
                retry_delay_seconds=15,
                poison=False,
            )
            status = await run_state_repository.get_outbox_delivery_status()

        self.assertTrue(recorded)
        self.assertTrue(any("UPDATE runtime_outbox" in query for query, _args in pool.execute_calls))
        self.assertEqual(status["undelivered_count"], 2)
        self.assertEqual(status["poisoned_count"], 1)
        self.assertEqual(status["claimed_count"], 1)
        self.assertEqual(status["repeated_failure_count"], 1)
        self.assertEqual(status["stuck_count"], 1)
        self.assertEqual(status["last_delivery_error"]["event_id"], "evt-1")

    async def test_claim_due_outbox_events_returns_only_claimed_rows_with_expiry(self):
        pool = _FakePool(
            fetch_result=[
                {
                    "event_id": "evt-claim",
                    "event_type": "artifact_created",
                    "tenant_id": "tenant-1",
                    "workspace_id": "ws-1",
                    "run_id": "run-1",
                    "machine_id": None,
                    "trace_id": "trace-1",
                    "idempotency_key": "artifact_created:run-1:/tmp/file:0",
                    "payload": {"artifact_path": "/tmp/file"},
                    "created_at": "2026-04-07T00:00:00Z",
                    "delivered_at": None,
                    "last_replayed_at": None,
                    "retry_count": 0,
                    "last_delivery_error": None,
                    "last_attempted_at": None,
                    "next_attempt_at": None,
                    "poisoned_at": None,
                    "claim_token": "claim-evt",
                    "claimed_by": "poller-1",
                    "claimed_at": "2026-04-07T00:00:10Z",
                    "claim_expires_at": "2026-04-07T00:00:40Z",
                }
            ]
        )
        with patch("server_modules.run_state_repository.runtime_db.get_pool", return_value=pool):
            items = await run_state_repository.claim_due_outbox_events(
                older_than_seconds=0,
                limit=5,
                claimed_by="poller-1",
                claim_ttl_seconds=30,
            )

        self.assertEqual(items[0]["claim_token"], "claim-evt")
        self.assertEqual(items[0]["claimed_by"], "poller-1")
        self.assertEqual(items[0]["claim_expires_at"], "2026-04-07T00:00:40Z")
        self.assertIn("FOR UPDATE SKIP LOCKED", pool.fetch_calls[0][0])

    async def test_claim_due_outbox_events_accepts_scope_filters(self):
        pool = _FakePool(fetch_result=[])
        with patch("server_modules.run_state_repository.runtime_db.get_pool", return_value=pool):
            items = await run_state_repository.claim_due_outbox_events(
                older_than_seconds=5,
                limit=3,
                claimed_by="poller-1",
                claim_ttl_seconds=45,
                tenant_id="tenant-1",
                workspace_id="ws-1",
                run_id="run-1",
                event_type="channel_run_delivery",
            )

        self.assertEqual(items, [])
        query, args = pool.fetch_calls[0]
        self.assertIn("tenant_id = $5", query)
        self.assertIn("workspace_id = $6", query)
        self.assertIn("run_id = $7", query)
        self.assertIn("event_type = $8", query)
        self.assertEqual(args[:4], (5, 3, "poller-1", 45))
        self.assertEqual(args[4:], ("tenant-1", "ws-1", "run-1", "channel_run_delivery"))

    async def test_list_undelivered_outbox_events_accepts_scope_filters(self):
        pool = _FakePool(fetch_result=[])
        with patch("server_modules.run_state_repository.runtime_db.get_pool", return_value=pool):
            items = await run_state_repository.list_undelivered_outbox_events(
                older_than_seconds=5,
                limit=3,
                tenant_id="tenant-1",
                workspace_id="ws-1",
                run_id="run-1",
                event_type="channel_run_delivery",
            )

        self.assertEqual(items, [])
        query, args = pool.fetch_calls[0]
        self.assertIn("tenant_id = $3", query)
        self.assertIn("workspace_id = $4", query)
        self.assertIn("run_id = $5", query)
        self.assertIn("event_type = $6", query)
        self.assertEqual(args, (5, 3, "tenant-1", "ws-1", "run-1", "channel_run_delivery"))

    async def test_fenced_outbox_writes_reject_wrong_claim_token(self):
        pool = _FakePool(fetchrow_result=None)
        with patch("server_modules.run_state_repository.runtime_db.get_pool", return_value=pool):
            delivered = await run_state_repository.mark_outbox_event_delivered("evt-1", claim_token="wrong-claim")
            failed = await run_state_repository.record_outbox_delivery_failure(
                "evt-1",
                claim_token="wrong-claim",
                error_text="boom",
                retry_delay_seconds=5,
                poison=False,
            )

        self.assertFalse(delivered)
        self.assertFalse(failed)

    async def test_patch_outbox_event_payload_and_list_poisoned_events(self):
        pool = _FakePool(
            fetch_result=[
                {
                    "event_id": "evt-poison",
                    "event_type": "channel_run_delivery",
                    "tenant_id": "tenant-1",
                    "workspace_id": "ws-1",
                    "run_id": "run-1",
                    "machine_id": None,
                    "trace_id": "trace-1",
                    "idempotency_key": "channel_run_delivery:telegram:conn-1:run-1",
                    "payload": {"channel": "telegram", "delivery": {"status": "failed"}},
                    "created_at": "2026-04-07T00:00:00Z",
                    "delivered_at": None,
                    "last_replayed_at": None,
                    "retry_count": 5,
                    "last_delivery_error": "boom",
                    "last_attempted_at": "2026-04-07T00:05:00Z",
                    "next_attempt_at": None,
                    "poisoned_at": "2026-04-07T00:05:30Z",
                }
            ]
        )
        with patch("server_modules.run_state_repository.runtime_db.get_pool", return_value=pool):
            await run_state_repository.patch_outbox_event_payload(
                "evt-poison",
                {"delivery": {"status": "sent", "receipt": {"provider_message_id": "msg-1"}}},
            )
            items = await run_state_repository.list_poisoned_outbox_events(limit=50)

        self.assertTrue(any("payload = COALESCE(runtime_outbox.payload" in query for query, _args in pool.execute_calls))
        self.assertEqual(items[0]["event_id"], "evt-poison")
        self.assertEqual(items[0]["payload"]["delivery"]["status"], "failed")

    async def test_list_expired_local_claims_returns_joined_run_payloads(self):
        pool = _FakePool(
            fetch_result=[
                {
                    "run_id": "run-1",
                    "worker_id": "worker-1",
                    "lease_id": "lease-1",
                    "claimed_at": "2026-04-07T00:00:00Z",
                    "last_heartbeat_at": "2026-04-07T00:00:10Z",
                    "last_progress_at": "2026-04-07T00:00:12Z",
                    "ttl": 30,
                    "trace_id": "trace-1",
                    "run_payload": {"run_id": "run-1", "status": "running_local"},
                }
            ]
        )
        with patch("server_modules.run_state_repository.runtime_db.get_pool", return_value=pool):
            items = await run_state_repository.list_expired_local_claims()

        self.assertEqual(items[0]["run_id"], "run-1")
        self.assertEqual(items[0]["worker_id"], "worker-1")
        self.assertEqual(items[0]["lease_id"], "lease-1")
        self.assertEqual(items[0]["last_heartbeat_at"], "2026-04-07T00:00:10Z")
        self.assertEqual(items[0]["run_payload"]["status"], "running_local")

    async def test_touch_claim_heartbeat_updates_durable_claim_timestamps(self):
        pool = _FakePool(fetchrow_result={"run_id": "run-1"})
        with patch("server_modules.run_state_repository.runtime_db.get_pool", return_value=pool):
            updated = await run_state_repository.touch_claim_heartbeat(
                "run-1",
                "worker-1",
                lease_id="lease-1",
                note="still working",
                progress=True,
            )

        self.assertTrue(updated)
        self.assertTrue(any("UPDATE local_queue_claims" in query for query, _args in pool.fetchrow_calls))

    async def test_release_claim_rejects_stale_lease_id(self):
        pool = _FakePool(fetchrow_result=None)
        with patch("server_modules.run_state_repository.runtime_db.get_pool", return_value=pool):
            released = await run_state_repository.release_claim("run-1", lease_id="stale-lease")

        self.assertFalse(released)
        self.assertTrue(any("lease_id = $2" in query for query, _args in pool.fetchrow_calls))

    async def test_touch_claim_heartbeat_rejects_stale_lease_id(self):
        pool = _FakePool(fetchrow_result=None)
        with patch("server_modules.run_state_repository.runtime_db.get_pool", return_value=pool):
            touched = await run_state_repository.touch_claim_heartbeat(
                "run-1",
                "worker-1",
                lease_id="stale-lease",
                note="still working",
                progress=True,
            )

        self.assertFalse(touched)
        self.assertTrue(any("lease_id = $5" in query for query, _args in pool.fetchrow_calls))

    async def test_append_dead_letter_and_status_snapshot_round_trip(self):
        pool = _FakePool(
            fetchrow_results=[
                {
                    "dead_letter_count": 2,
                    "total_failure_count": 5,
                    "last_recorded_at": "2026-04-07T00:02:00Z",
                }
            ],
            fetch_results=[
                [{"workspace_id": "ws-1", "count": 2}],
                [{"specialist_key": "install-1", "count": 2}],
            ],
        )
        with patch("server_modules.run_state_repository.runtime_db.get_pool", return_value=pool):
            await run_state_repository.append_local_queue_dead_letter(
                run_id="run-1",
                tenant_id="tenant-1",
                workspace_id="ws-1",
                specialist_key="install-1",
                reason="worker_lost_retry_exhausted",
                trace_id="trace-1",
                failure_count=3,
                payload={"run_id": "run-1"},
            )
            status = await run_state_repository.get_local_queue_dead_letter_status()

        self.assertTrue(any("local_queue_dead_letters" in query for query, _args in pool.execute_calls))
        self.assertEqual(status["dead_letter_count"], 2)
        self.assertEqual(status["workspace_hotspots"][0]["workspace_id"], "ws-1")
        self.assertEqual(status["specialist_hotspots"][0]["specialist_key"], "install-1")

    async def test_upsert_and_list_fleet_workers_round_trip(self):
        pool = _FakePool(
            fetch_result=[
                {
                    "worker_id": "worker-1",
                    "tenant_id": "tenant-1",
                    "workspace_id": "ws-1",
                    "machine_id": "machine-1",
                    "runtime_type": "hosted_secure",
                    "status": "idle",
                    "control_state": "active",
                    "current_run_id": None,
                    "instance_id": "instance-1",
                    "shard_key": "tenant-1:ws-1:hosted_secure:hosted_secure",
                    "prewarm_state": "warm",
                    "warm_pool": "primary",
                    "lease_seconds": 45,
                    "registered_at": "2026-04-10T00:00:00Z",
                    "last_registered_at": "2026-04-10T00:00:10Z",
                    "last_heartbeat_at": "2026-04-10T00:00:12Z",
                    "updated_at": "2026-04-10T00:00:12Z",
                    "payload": {"display_name": "Hosted Worker"},
                }
            ]
        )
        with patch("server_modules.run_state_repository.runtime_db.get_pool", return_value=pool):
            await run_state_repository.upsert_fleet_worker(
                {
                    "worker_id": "worker-1",
                    "tenant_id": "tenant-1",
                    "workspace_id": "ws-1",
                    "machine_id": "machine-1",
                    "runtime_type": "hosted_secure",
                    "status": "idle",
                    "control_state": "active",
                    "execution_targets": ["hosted_secure"],
                    "prewarm_state": "warm",
                    "warm_pool": "primary",
                    "lease_seconds": 45,
                },
                heartbeat_seen=True,
            )
            items = await run_state_repository.list_fleet_workers(workspace_id="ws-1")

        self.assertTrue(any("fleet_worker_registrations" in query for query, _args in pool.execute_calls))
        self.assertEqual(items[0]["worker_id"], "worker-1")
        self.assertEqual(items[0]["prewarm_state"], "warm")
        self.assertEqual(items[0]["queue_shard"], "tenant-1:ws-1:hosted_secure:hosted_secure")

    async def test_upsert_and_list_fleet_queue_partitions_round_trip(self):
        pool = _FakePool(
            fetch_result=[
                {
                    "partition_id": "ws-1::install-1",
                    "tenant_id": "tenant-1",
                    "workspace_id": "ws-1",
                    "specialist_key": "install-1",
                    "pending_count": 3,
                    "claimed_count": 1,
                    "online_workers": 2,
                    "busy_workers": 1,
                    "idle_workers": 1,
                    "prewarmed_workers": 1,
                    "state": "strained",
                    "retry_after_seconds": 10,
                    "updated_at": "2026-04-10T00:00:30Z",
                    "payload": {"summary": "Partition backlog is elevated but progressing."},
                }
            ]
        )
        with patch("server_modules.run_state_repository.runtime_db.get_pool", return_value=pool):
            await run_state_repository.upsert_fleet_queue_partition(
                partition_id="ws-1::install-1",
                tenant_id="tenant-1",
                workspace_id="ws-1",
                specialist_key="install-1",
                pending_count=3,
                claimed_count=1,
                online_workers=2,
                busy_workers=1,
                idle_workers=1,
                prewarmed_workers=1,
                state="strained",
                retry_after_seconds=10,
                payload={"summary": "Partition backlog is elevated but progressing."},
            )
            items = await run_state_repository.list_fleet_queue_partitions(workspace_id="ws-1")

        self.assertTrue(any("fleet_queue_partitions" in query for query, _args in pool.execute_calls))
        self.assertEqual(items[0]["partition_id"], "ws-1::install-1")
        self.assertEqual(items[0]["pending_count"], 3)
        self.assertEqual(items[0]["prewarmed_workers"], 1)


class RunStateRepositorySyncDispatchTests(unittest.TestCase):
    def test_dispatch_repository_call_returns_without_waiting_for_completion(self):
        started = threading.Event()
        release = threading.Event()

        async def _blocking() -> None:
            started.set()
            await asyncio.to_thread(release.wait, 1.0)

        started_at = time.monotonic()
        run_state_repository.dispatch_repository_call(_blocking(), operation="test_dispatch_repository_call")
        elapsed = time.monotonic() - started_at

        self.assertLess(elapsed, 0.1)
        self.assertTrue(started.wait(timeout=1.0))
        release.set()
