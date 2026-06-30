import unittest
from unittest.mock import AsyncMock, patch

from server_modules import run_state_repository


class RunStateRepositoryRustGateTests(unittest.TestCase):
    def test_live_run_state_gate_calls_rust_store_decision(self) -> None:
        calls = []

        def fake_rust(command, payload, **kwargs):
            calls.append((command, payload, kwargs))
            return {
                "ok": True,
                "decision": "allow",
                "reason": "runtime_state_store_policy_satisfied",
                "operation": payload["operation"],
                "next_action": "write_live_run_state",
                "approval_required": False,
                "cacheable": False,
                "audit_visibility": "standard",
            }

        with patch.object(
            run_state_repository.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=fake_rust,
        ):
            decision = run_state_repository._enforce_runtime_state_store_decision(
                operation="upsert_live_run",
                run_id="run-1",
                workspace_id="ws-1",
                tenant_id="tenant-1",
                status="running",
                trace_id="trace-1",
                payload={"run_id": "run-1", "status": "running"},
                expected_version=3,
            )

        self.assertEqual(decision["decision"], "allow")
        self.assertEqual(calls[0][0], "runtime-state-store-decision")
        self.assertEqual(calls[0][1]["operation"], "upsert_live_run")
        self.assertEqual(calls[0][1]["state_class"], "live_runs")
        self.assertEqual(calls[0][1]["run_id"], "run-1")
        self.assertEqual(calls[0][1]["workspace_id"], "ws-1")
        self.assertEqual(calls[0][1]["tenant_id"], "tenant-1")
        self.assertEqual(calls[0][1]["expected_version"], 3)
        self.assertTrue(calls[0][1]["payload"])

    def test_archive_run_state_gate_uses_archive_class(self) -> None:
        calls = []

        with patch.object(
            run_state_repository.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=lambda command, payload, **kwargs: calls.append((command, payload, kwargs))
            or {
                "ok": True,
                "decision": "allow",
                "reason": "runtime_state_store_policy_satisfied",
                "operation": payload["operation"],
                "next_action": "write_run_archive",
                "approval_required": False,
                "cacheable": False,
                "audit_visibility": "standard",
            },
        ):
            run_state_repository._enforce_runtime_state_store_decision(
                operation="archive_run",
                run_id="run-1",
                workspace_id="ws-1",
                tenant_id="tenant-1",
                status="completed",
                payload={"run_id": "run-1", "status": "completed"},
            )

        self.assertEqual(calls[0][0], "runtime-state-store-decision")
        self.assertEqual(calls[0][1]["operation"], "archive_run")
        self.assertEqual(calls[0][1]["state_class"], "run_archive")

    def test_runtime_registration_state_gate_uses_runtime_registration_class(self) -> None:
        calls = []

        with patch.object(
            run_state_repository.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=lambda command, payload, **kwargs: calls.append((command, payload, kwargs))
            or {
                "ok": True,
                "decision": "allow",
                "reason": "runtime_state_store_policy_satisfied",
                "operation": payload["operation"],
                "next_action": "write_runtime_registration",
                "approval_required": False,
                "cacheable": False,
                "audit_visibility": "standard",
            },
        ):
            run_state_repository._enforce_runtime_state_store_decision(
                operation="upsert_runtime_registration",
                runtime_id="worker-1",
                workspace_id="ws-1",
                tenant_id="tenant-1",
                status="idle",
                payload={"worker_id": "worker-1", "status": "idle"},
            )

        self.assertEqual(calls[0][0], "runtime-state-store-decision")
        self.assertEqual(calls[0][1]["operation"], "upsert_runtime_registration")
        self.assertEqual(calls[0][1]["state_class"], "runtime_registrations")
        self.assertEqual(calls[0][1]["runtime_id"], "worker-1")

    def test_fleet_queue_partition_state_gate_uses_partition_class(self) -> None:
        calls = []

        with patch.object(
            run_state_repository.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=lambda command, payload, **kwargs: calls.append((command, payload, kwargs))
            or {
                "ok": True,
                "decision": "allow",
                "reason": "runtime_state_store_policy_satisfied",
                "operation": payload["operation"],
                "next_action": "write_fleet_queue_partition",
                "approval_required": False,
                "cacheable": False,
                "audit_visibility": "standard",
            },
        ):
            run_state_repository._enforce_runtime_state_store_decision(
                operation="upsert_fleet_queue_partition",
                workspace_id="ws-1",
                tenant_id="tenant-1",
                status="strained",
                payload={"summary": "Partition backlog is elevated but progressing."},
                state_class="fleet_queue_partitions",
            )

        self.assertEqual(calls[0][0], "runtime-state-store-decision")
        self.assertEqual(calls[0][1]["operation"], "upsert_fleet_queue_partition")
        self.assertEqual(calls[0][1]["state_class"], "fleet_queue_partitions")

    def test_delete_live_run_state_gate_uses_live_runs_class(self) -> None:
        calls = []

        with patch.object(
            run_state_repository.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=lambda command, payload, **kwargs: calls.append((command, payload, kwargs))
            or {
                "ok": True,
                "decision": "allow",
                "reason": "runtime_state_store_policy_satisfied",
                "operation": payload["operation"],
                "next_action": "delete_live_run_state",
                "approval_required": False,
                "cacheable": False,
                "audit_visibility": "standard",
            },
        ):
            run_state_repository._enforce_runtime_state_store_decision(
                operation="delete_live_run",
                run_id="run-1",
                workspace_id="ws-1",
                tenant_id="tenant-1",
                status="failed",
                state_class="live_runs",
            )

        self.assertEqual(calls[0][0], "runtime-state-store-decision")
        self.assertEqual(calls[0][1]["operation"], "delete_live_run")
        self.assertEqual(calls[0][1]["state_class"], "live_runs")

    def test_runtime_state_store_gate_blocks_on_wrong_rust_action(self) -> None:
        with patch.object(
            run_state_repository.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "runtime_state_store_policy_satisfied",
                "operation": "upsert_live_run",
                "next_action": "write_notification",
                "approval_required": False,
                "cacheable": False,
                "audit_visibility": "standard",
            },
        ):
            with self.assertRaisesRegex(RuntimeError, "unexpected next_action"):
                run_state_repository._enforce_runtime_state_store_decision(
                    operation="upsert_live_run",
                    run_id="run-1",
                    workspace_id="ws-1",
                    tenant_id="tenant-1",
                    status="running",
                    payload={"run_id": "run-1", "status": "running"},
                )


class RunStateRepositoryArchiveRunRecordRustGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_archive_run_calls_run_record_kernel_before_persisting(self) -> None:
        calls = []

        def fake_rust(command, payload, **kwargs):
            calls.append((command, payload, kwargs))
            if command == "run-record-decision":
                return {
                    "ok": True,
                    "decision": "allow",
                    "reason": "run_record_archive_allowed",
                    "operation": payload["operation"],
                    "next_action": "archive_run",
                    "record_patch": {
                        "status": "completed",
                        "state": "completed",
                        "_archived": True,
                        "_event_seq": 7,
                    },
                    "approval_required": False,
                    "cacheable": False,
                    "audit_visibility": "security",
                }
            return {
                "ok": True,
                "decision": "allow",
                "reason": "runtime_state_store_policy_satisfied",
                "operation": payload["operation"],
                "next_action": "write_run_archive",
                "approval_required": False,
                "cacheable": False,
                "audit_visibility": "standard",
            }

        pool = AsyncMock()
        pool.execute.return_value = None

        with patch.object(
            run_state_repository.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=fake_rust,
        ), patch.object(
            run_state_repository,
            "_require_pool",
            AsyncMock(return_value=pool),
        ), patch.object(
            run_state_repository,
            "_ensure_run_archive_table",
            AsyncMock(return_value=None),
        ):
            await run_state_repository.archive_run(
                "run-1",
                "completed",
                {
                    "run_id": "run-1",
                    "status": "running",
                    "state": "running",
                    "workspace_id": "ws-1",
                    "tenant_id": "tenant-1",
                    "_event_seq": 7,
                },
                "trace-1",
            )

        self.assertEqual(calls[0][0], "run-record-decision")
        self.assertEqual(calls[0][1]["operation"], "archive_payload")
        self.assertEqual(calls[1][0], "runtime-state-store-decision")
        self.assertEqual(calls[1][1]["status"], "completed")
        self.assertTrue(calls[1][1]["payload"])

    async def test_archive_run_malformed_version_uses_zero_for_run_record(self) -> None:
        calls = []

        def fake_rust(command, payload, **kwargs):
            calls.append((command, payload, kwargs))
            if command == "run-record-decision":
                return {
                    "ok": True,
                    "decision": "allow",
                    "reason": "run_record_archive_allowed",
                    "operation": payload["operation"],
                    "next_action": "archive_run",
                    "record_patch": {"status": "completed", "state": "completed", "_archived": True},
                }
            return {
                "ok": True,
                "decision": "allow",
                "reason": "runtime_state_store_policy_satisfied",
                "operation": payload["operation"],
                "next_action": "write_run_archive",
            }

        pool = AsyncMock()
        pool.execute.return_value = None

        with patch.object(
            run_state_repository.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=fake_rust,
        ), patch.object(
            run_state_repository,
            "_require_pool",
            AsyncMock(return_value=pool),
        ), patch.object(
            run_state_repository,
            "_ensure_run_archive_table",
            AsyncMock(return_value=None),
        ):
            await run_state_repository.archive_run(
                "run-1",
                "completed",
                {
                    "run_id": "run-1",
                    "workspace_id": "ws-1",
                    "tenant_id": "tenant-1",
                    "status": "completed",
                    "_event_seq": "bad-version",
                },
                "trace-1",
            )

        self.assertEqual(calls[0][0], "run-record-decision")
        self.assertEqual(calls[0][1]["version"], 0)

    async def test_record_transition_calls_run_record_kernel_before_insert(self) -> None:
        calls = []

        def fake_rust(command, payload, **kwargs):
            calls.append((command, payload, kwargs))
            return {
                "ok": True,
                "decision": "allow",
                "reason": "run_record_transition_allowed",
                "operation": payload["operation"],
                "next_action": "record_transition",
                "record_patch": {"status": "running", "state": "running", "_event_seq": 4},
            }

        pool = AsyncMock()
        pool.execute.return_value = None

        with patch.object(
            run_state_repository.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=fake_rust,
        ), patch.object(
            run_state_repository,
            "get_live_run",
            AsyncMock(
                return_value={
                    "run_id": "run-1",
                    "workspace_id": "ws-1",
                    "tenant_id": "tenant-1",
                    "status": "running",
                    "_event_seq": 4,
                }
            ),
        ), patch.object(
            run_state_repository,
            "_require_pool",
            AsyncMock(return_value=pool),
        ), patch.object(
            run_state_repository,
            "_ensure_live_run_tables",
            AsyncMock(return_value=None),
        ):
            await run_state_repository.record_transition(
                "run-1",
                "queued",
                "running",
                "runtime",
                "trace-1",
            )

        self.assertEqual(calls[0][0], "run-record-decision")
        self.assertEqual(calls[0][1]["operation"], "record_transition")
        self.assertEqual(calls[0][1]["from_state"], "queued")
        self.assertEqual(calls[0][1]["to_state"], "running")
        self.assertEqual(calls[0][1]["version"], 4)
        pool.execute.assert_awaited()

    async def test_record_transition_blocks_on_wrong_run_record_action_before_pool(self) -> None:
        pool = AsyncMock()
        pool.execute.return_value = None

        with patch.object(
            run_state_repository.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "run_record_transition_allowed",
                "operation": "record_transition",
                "next_action": "archive_run",
                "record_patch": {"status": "running", "state": "running", "_event_seq": 4},
            },
        ), patch.object(
            run_state_repository,
            "get_live_run",
            AsyncMock(
                return_value={
                    "run_id": "run-1",
                    "workspace_id": "ws-1",
                    "tenant_id": "tenant-1",
                    "status": "running",
                    "_event_seq": 4,
                }
            ),
        ), patch.object(
            run_state_repository,
            "_require_pool",
            AsyncMock(return_value=pool),
        ):
            with self.assertRaisesRegex(RuntimeError, "unexpected next_action"):
                await run_state_repository.record_transition(
                    "run-1",
                    "queued",
                    "running",
                    "runtime",
                    "trace-1",
                )

        run_state_repository._require_pool.assert_not_awaited()

    async def test_archive_run_blocks_on_wrong_run_record_action_before_pool(self) -> None:
        pool = AsyncMock()
        pool.execute.return_value = None

        with patch.object(
            run_state_repository.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "run_record_archive_allowed",
                "operation": "archive_payload",
                "next_action": "record_transition",
                "record_patch": {
                    "status": "completed",
                    "state": "completed",
                    "_archived": True,
                    "_event_seq": 7,
                },
            },
        ), patch.object(
            run_state_repository,
            "_require_pool",
            AsyncMock(return_value=pool),
        ):
            with self.assertRaisesRegex(RuntimeError, "unexpected next_action"):
                await run_state_repository.archive_run(
                    "run-1",
                    "completed",
                    {
                        "run_id": "run-1",
                        "status": "running",
                        "state": "running",
                        "workspace_id": "ws-1",
                        "tenant_id": "tenant-1",
                        "_event_seq": 7,
                    },
                    "trace-1",
                )

        run_state_repository._require_pool.assert_not_awaited()

    async def test_append_local_queue_dead_letter_calls_queue_kernel_first(self) -> None:
        calls = []

        def fake_rust(command, payload, **kwargs):
            calls.append((command, payload, kwargs))
            return {
                "ok": True,
                "decision": "allow",
                "reason": "queue_dead_letter_allowed",
                "operation": payload["operation"],
                "next_status": "failed",
                "terminal": True,
            }

        pool = AsyncMock()
        pool.execute.return_value = None

        with patch.object(
            run_state_repository.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=fake_rust,
        ), patch.object(
            run_state_repository,
            "_require_pool",
            AsyncMock(return_value=pool),
        ), patch.object(
            run_state_repository,
            "_ensure_local_queue_tables",
            AsyncMock(return_value=None),
        ):
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

        self.assertEqual(calls[0][0], "queue-transition-decision")
        self.assertEqual(calls[0][1]["operation"], "dead_letter")
        self.assertEqual(calls[0][1]["reason"], "worker_lost_retry_exhausted")
        self.assertEqual(calls[0][1]["attempts"], 3)
        pool.execute.assert_awaited()

    async def test_upsert_fleet_worker_calls_runtime_state_store_kernel_first(self) -> None:
        calls = []

        def fake_rust(command, payload, **kwargs):
            calls.append((command, payload, kwargs))
            return {
                "ok": True,
                "decision": "allow",
                "reason": "runtime_state_store_policy_satisfied",
                "operation": payload["operation"],
                "next_action": "write_runtime_registration",
                "approval_required": False,
                "cacheable": False,
                "audit_visibility": "standard",
            }

        pool = AsyncMock()
        pool.execute.return_value = None

        with patch.object(
            run_state_repository.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=fake_rust,
        ), patch.object(
            run_state_repository,
            "_require_pool",
            AsyncMock(return_value=pool),
        ), patch.object(
            run_state_repository,
            "_ensure_fleet_runtime_tables",
            AsyncMock(return_value=None),
        ):
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
                    "lease_seconds": 45,
                },
                heartbeat_seen=True,
            )

        self.assertEqual(calls[0][0], "runtime-state-store-decision")
        self.assertEqual(calls[0][1]["operation"], "upsert_runtime_registration")
        self.assertEqual(calls[0][1]["state_class"], "runtime_registrations")
        self.assertEqual(calls[0][1]["runtime_id"], "worker-1")
        self.assertEqual(calls[0][1]["workspace_id"], "ws-1")
        pool.execute.assert_awaited()

    async def test_upsert_fleet_queue_partition_calls_runtime_state_store_kernel_first(self) -> None:
        calls = []

        def fake_rust(command, payload, **kwargs):
            calls.append((command, payload, kwargs))
            return {
                "ok": True,
                "decision": "allow",
                "reason": "runtime_state_store_policy_satisfied",
                "operation": payload["operation"],
                "next_action": "write_fleet_queue_partition",
                "approval_required": False,
                "cacheable": False,
                "audit_visibility": "standard",
            }

        pool = AsyncMock()
        pool.execute.return_value = None

        with patch.object(
            run_state_repository.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=fake_rust,
        ), patch.object(
            run_state_repository,
            "_require_pool",
            AsyncMock(return_value=pool),
        ), patch.object(
            run_state_repository,
            "_ensure_fleet_runtime_tables",
            AsyncMock(return_value=None),
        ):
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

        self.assertEqual(calls[0][0], "runtime-state-store-decision")
        self.assertEqual(calls[0][1]["operation"], "upsert_fleet_queue_partition")
        self.assertEqual(calls[0][1]["state_class"], "fleet_queue_partitions")
        self.assertEqual(calls[0][1]["workspace_id"], "ws-1")
        pool.execute.assert_awaited()

    async def test_delete_live_run_calls_runtime_state_store_kernel_first(self) -> None:
        calls = []

        def fake_rust(command, payload, **kwargs):
            calls.append((command, payload, kwargs))
            return {
                "ok": True,
                "decision": "allow",
                "reason": "runtime_state_store_policy_satisfied",
                "operation": payload["operation"],
                "next_action": "delete_live_run_state",
                "approval_required": False,
                "cacheable": False,
                "audit_visibility": "standard",
            }

        pool = AsyncMock()
        pool.execute.return_value = None

        with patch.object(
            run_state_repository.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=fake_rust,
        ), patch.object(
            run_state_repository,
            "_require_pool",
            AsyncMock(return_value=pool),
        ), patch.object(
            run_state_repository,
            "_ensure_live_run_tables",
            AsyncMock(return_value=None),
        ), patch.object(
            run_state_repository,
            "get_live_run",
            AsyncMock(
                return_value={
                    "run_id": "run-1",
                    "workspace_id": "ws-1",
                    "tenant_id": "tenant-1",
                    "status": "failed",
                    "trace_id": "trace-1",
                }
            ),
        ):
            await run_state_repository.delete_live_run("run-1")

        self.assertEqual(calls[0][0], "runtime-state-store-decision")
        self.assertEqual(calls[0][1]["operation"], "delete_live_run")
        self.assertEqual(calls[0][1]["state_class"], "live_runs")
        self.assertEqual(calls[0][1]["status"], "failed")
        pool.execute.assert_awaited()

    async def test_create_or_update_approval_request_blocks_on_wrong_runtime_state_action(self) -> None:
        pool = AsyncMock()
        pool.fetchrow.return_value = None

        with patch.object(
            run_state_repository.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "runtime_state_store_policy_satisfied",
                "operation": "create_or_update_approval_request",
                "next_action": "write_runtime_registration",
                "approval_required": False,
                "cacheable": False,
                "audit_visibility": "standard",
            },
        ), patch.object(
            run_state_repository,
            "_require_pool",
            AsyncMock(return_value=pool),
        ):
            with self.assertRaisesRegex(RuntimeError, "unexpected next_action"):
                await run_state_repository.create_or_update_approval_request(
                    "run-1",
                    "approval-1",
                    {"workspace_id": "ws-1", "tenant_id": "tenant-1"},
                    "runtime",
                    "trace-1",
                )

        run_state_repository._require_pool.assert_not_awaited()

    async def test_persist_outbox_event_blocks_on_wrong_outbox_action(self) -> None:
        pool = AsyncMock()

        with patch.object(
            run_state_repository.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "outbox_delivery_policy_satisfied",
                "operation": "persist_event",
                "next_action": "claim_due_outbox_events",
                "approval_required": False,
                "cacheable": False,
                "audit_visibility": "standard",
            },
        ), patch.object(
            run_state_repository,
            "_require_pool",
            AsyncMock(return_value=pool),
        ):
            with self.assertRaisesRegex(RuntimeError, "unexpected next_action"):
                await run_state_repository.persist_outbox_event(
                    event_id="evt-1",
                    event_type="runtime_event",
                    tenant_id="tenant-1",
                    workspace_id="ws-1",
                    run_id="run-1",
                    machine_id="machine-1",
                    trace_id="trace-1",
                    idempotency_key="idem-1",
                    payload={"hello": "world"},
                )

        run_state_repository._require_pool.assert_not_awaited()

    async def test_claim_run_blocks_on_wrong_queue_claim_action(self) -> None:
        pool = AsyncMock()

        with patch.object(
            run_state_repository.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "queue_transition_allowed",
                "operation": "claim",
                "next_action": "release_queue_item",
                "approval_required": False,
                "cacheable": False,
                "audit_visibility": "standard",
            },
        ), patch.object(
            run_state_repository,
            "_require_pool",
            AsyncMock(return_value=pool),
        ):
            with self.assertRaisesRegex(RuntimeError, "unexpected next_action"):
                await run_state_repository.claim_run(
                    "run-1",
                    "worker-1",
                    30,
                    "trace-1",
                    lease_id="lease-1",
                )

        run_state_repository._require_pool.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
