import asyncio
import threading
import time
import unittest
from datetime import datetime
import queue
from types import SimpleNamespace
from unittest.mock import patch

from server_modules import local_queue, safe_mode_service


class LocalQueueMachineControlTests(unittest.TestCase):
    def setUp(self) -> None:
        safe_mode_service.reset_state_for_tests()
        local_queue.reset_runtime_control_stream_state_for_tests()

    def test_sync_durable_fleet_worker_returns_without_waiting_for_repo_write(self) -> None:
        started = threading.Event()
        release = threading.Event()
        calls = []

        def _blocking_sync(record, *, heartbeat_seen=True):
            calls.append((record["runtime_id"], heartbeat_seen))
            started.set()
            release.wait(timeout=1.0)

        started_at = time.monotonic()
        with patch.object(local_queue.run_state_repository, "sync_upsert_fleet_worker", side_effect=_blocking_sync):
            local_queue._sync_durable_fleet_worker({"runtime_id": "worker-1", "machine_id": "machine-1"}, heartbeat_seen=True)
            elapsed = time.monotonic() - started_at
            self.assertLess(elapsed, 0.1)
            self.assertTrue(started.wait(timeout=0.5))
            release.set()

        self.assertEqual(calls, [("worker-1", True)])

    def test_mark_local_worker_seen_throttles_idle_persist_and_durable_sync(self) -> None:
        original_server = local_queue._server
        current_time = {"iso": "2026-04-08T10:00:00Z"}
        local_queue._server = SimpleNamespace(
            LOCAL_QUEUE_LOCK=threading.Lock(),
            LOCAL_WORKER_REGISTRY={
                "worker-1": {
                    "worker_id": "worker-1",
                    "runtime_id": "worker-1",
                    "machine_id": "worker-1",
                    "status": "idle",
                    "current_run_id": None,
                    "last_seen_at": "2026-04-08T10:00:00Z",
                    "note": "idle_poll",
                    "_runtime_state_persisted_at": "2026-04-08T10:00:00Z",
                    "_durable_sync_at": "2026-04-08T10:00:00Z",
                }
            },
            ORION_LOCAL_LEASE_SECONDS=30,
            _utc_now_iso=lambda: current_time["iso"],
            _parse_utc_ts=lambda value: datetime.fromisoformat(str(value).replace("Z", "")) if value else None,
        )
        persisted = []
        synced = []
        try:
            with (
                patch.object(local_queue, "_persist_local_runtime_state", side_effect=lambda: persisted.append(True)),
                patch.object(local_queue, "_sync_durable_fleet_worker", side_effect=lambda *args, **kwargs: synced.append(kwargs)),
            ):
                current_time["iso"] = "2026-04-08T10:00:02Z"
                local_queue._mark_local_worker_seen("worker-1", None, "idle", note="idle_poll")
                self.assertEqual(persisted, [])
                self.assertEqual(synced, [])

                current_time["iso"] = "2026-04-08T10:00:12Z"
                local_queue._mark_local_worker_seen("worker-1", None, "idle", note="idle_poll")
                self.assertEqual(persisted, [True])
                self.assertEqual(synced, [])

                current_time["iso"] = "2026-04-08T10:00:16Z"
                local_queue._mark_local_worker_seen("worker-1", None, "idle", note="idle_poll")
        finally:
            local_queue._server = original_server

        self.assertEqual(persisted, [True])
        self.assertEqual(len(synced), 1)

    def test_handle_heartbeat_local_worker_skips_rewriting_unchanged_permission_probe(self) -> None:
        original_server = local_queue._server
        local_queue._server = SimpleNamespace(
            LOCAL_QUEUE_LOCK=threading.Lock(),
            LOCAL_WORKER_REGISTRY={
                "worker-1": {
                    "worker_id": "worker-1",
                    "runtime_id": "worker-1",
                    "machine_id": "worker-1",
                    "permission_probe": {
                        "screen_recording": {
                            "status": "granted",
                            "source": "probe",
                            "detail": "macOS screen capture permission probe.",
                            "updated_at": "2026-04-08T10:00:00Z",
                        }
                    },
                    "permission_probe_updated_at": "2026-04-08T10:00:00Z",
                }
            },
            runs={},
            LOCAL_CLAIMED_RUNS={},
            _utc_now_iso=lambda: "2026-04-08T10:05:00Z",
        )
        persisted = []
        try:
            with (
                patch.object(local_queue, "_persist_local_runtime_state", side_effect=lambda: persisted.append(True)),
                patch.object(local_queue.worker_dispatch_service, "heartbeat_local_worker", return_value={"status": "ok"}),
            ):
                payload = local_queue.LocalWorkerHeartbeatPayload(
                    permission_probe={
                        "screen_recording": {
                            "status": "granted",
                            "source": "probe",
                            "detail": "macOS screen capture permission probe.",
                            "updated_at": "2026-04-08T10:05:00Z",
                        }
                    }
                )
                result = local_queue.handle_heartbeat_local_worker("worker-1", payload)
        finally:
            local_queue._server = original_server

        self.assertEqual(result["status"], "ok")
        self.assertEqual(persisted, [])

    def test_cleanup_stale_local_claims_is_throttled_between_polls(self) -> None:
        original_server = local_queue._server
        local_queue._server = SimpleNamespace(
            _utc_now=lambda: datetime.fromisoformat("2026-04-08T10:00:00"),
            LOCAL_QUEUE_LOCK=threading.Lock(),
            LOCAL_PENDING_RUN_IDS=[],
            LOCAL_CLAIMED_RUNS={},
            LOCAL_WORKER_REGISTRY={},
            runs={},
            ORION_LOCAL_LEASE_SECONDS=30,
            _parse_utc_ts=lambda value: datetime.fromisoformat(str(value).replace("Z", "")) if value else None,
            _utc_now_iso=lambda: "2026-04-08T10:00:00Z",
            emit_log=lambda *args, **kwargs: None,
            set_run_status=lambda *args, **kwargs: None,
        )
        cleanup_calls = []
        try:
            with (
                patch.object(local_queue.machine_lease_service, "cleanup_stale_machine_leases", side_effect=lambda **kwargs: cleanup_calls.append(kwargs) or []),
                patch.object(local_queue, "_monotonic", side_effect=[100.0, 101.0, 106.0]),
            ):
                self.assertEqual(local_queue._cleanup_stale_local_claims(), [])
                self.assertEqual(local_queue._cleanup_stale_local_claims(), [])
                self.assertEqual(local_queue._cleanup_stale_local_claims(), [])
        finally:
            local_queue._server = original_server

        self.assertEqual(len(cleanup_calls), 2)

    def test_cleanup_stale_local_claims_releases_only_matching_lease(self) -> None:
        original_server = local_queue._server
        local_queue._server = SimpleNamespace(
            _utc_now=lambda: datetime.fromisoformat("2026-04-08T10:00:00"),
            LOCAL_QUEUE_LOCK=threading.Lock(),
            LOCAL_PENDING_RUN_IDS=[],
            LOCAL_CLAIMED_RUNS={"run-1": {"worker_id": "worker-1", "lease_id": "lease-new"}},
            LOCAL_WORKER_REGISTRY={},
            runs={"run-1": {"status": "running_local"}},
            ORION_LOCAL_LEASE_SECONDS=30,
            _parse_utc_ts=lambda value: datetime.fromisoformat(str(value).replace("Z", "")) if value else None,
            _utc_now_iso=lambda: "2026-04-08T10:00:00Z",
            emit_log=lambda *args, **kwargs: None,
            set_run_status=lambda *args, **kwargs: None,
        )
        release_calls = []
        remaining_claims = None
        try:
            with (
                patch.object(local_queue.machine_lease_service, "cleanup_stale_machine_leases", return_value=[]),
                patch.object(
                    local_queue.run_state_repository,
                    "sync_list_expired_local_claims",
                    return_value=[{"run_id": "run-1", "worker_id": "worker-1", "lease_id": "lease-old"}],
                ),
                patch.object(
                    local_queue.run_state_repository,
                    "sync_release_claim",
                    side_effect=lambda run_id, **kwargs: release_calls.append((run_id, kwargs.get("lease_id"))) or False,
                ),
                patch.object(local_queue, "_monotonic", return_value=100.0),
            ):
                recovered = local_queue._cleanup_stale_local_claims()
                remaining_claims = dict(local_queue._server.LOCAL_CLAIMED_RUNS)
        finally:
            local_queue._server = original_server

        self.assertEqual(recovered, [])
        self.assertEqual(release_calls, [("run-1", "lease-old")])
        self.assertEqual(remaining_claims, {"run-1": {"worker_id": "worker-1", "lease_id": "lease-new"}})

    def test_handle_claim_local_run_avoids_full_queue_snapshot_when_idle(self) -> None:
        original_server = local_queue._server
        local_queue._server = SimpleNamespace(
            ORION_LOCAL_COMPANION_ENABLED=True,
            ORION_LOCAL_LEASE_SECONDS=30,
            LOCAL_QUEUE_LOCK=threading.Lock(),
            LOCAL_PENDING_RUN_IDS=[],
            LOCAL_CLAIMED_RUNS={},
            LOCAL_WORKER_REGISTRY={
                "worker-1": {
                    "worker_id": "worker-1",
                    "runtime_id": "worker-1",
                    "machine_id": "worker-1",
                    "status": "idle",
                    "current_run_id": None,
                    "last_seen_at": "2026-04-08T10:00:00Z",
                    "lease_seconds": 30,
                }
            },
            runs={},
            _utc_now=lambda: datetime.fromisoformat("2026-04-08T10:00:05"),
            _parse_utc_ts=lambda value: datetime.fromisoformat(str(value).replace("Z", "")) if value else None,
            set_run_status=lambda *args, **kwargs: None,
            emit_log=lambda *args, **kwargs: None,
            redact_sensitive=lambda payload: payload,
        )
        try:
            with (
                patch.object(local_queue, "_claim_local_run", return_value=None),
                patch.object(local_queue, "handle_get_local_run_queue", side_effect=AssertionError("full queue snapshot should not be used on idle poll")),
            ):
                payload = local_queue.handle_claim_local_run(
                    local_queue.LocalRunClaimRequest(worker_id="worker-1")
                )
        finally:
            local_queue._server = original_server

        self.assertTrue(payload["ok"])
        self.assertIsNone(payload["run"])
        self.assertEqual(payload["backpressure"]["state"], "healthy")
        self.assertEqual(payload["backpressure"]["queued_count"], 0)

    def test_is_worker_online_returns_false_for_recent_offline_worker(self) -> None:
        original_server = local_queue._server
        local_queue._server = SimpleNamespace(
            ORION_LOCAL_LEASE_SECONDS=120,
            _utc_now=lambda: datetime.fromisoformat("2026-04-08T10:00:05"),
            _parse_utc_ts=lambda value: datetime.fromisoformat(str(value).replace("Z", "")) if value else None,
        )
        try:
            online = local_queue._is_worker_online(
                {
                    "runtime_id": "worker-1",
                    "status": "offline",
                    "last_seen_at": "2026-04-08T10:00:00Z",
                    "lease_seconds": 120,
                }
            )
        finally:
            local_queue._server = original_server

        self.assertFalse(online)

    def test_handle_get_local_run_queue_uses_lightweight_pressure_snapshot(self) -> None:
        original_server = local_queue._server
        local_queue._server = SimpleNamespace(
            LOCAL_QUEUE_LOCK=threading.Lock(),
            LOCAL_WORKER_REGISTRY={},
            LOCAL_PENDING_RUN_IDS=["run-1"],
            LOCAL_CLAIMED_RUNS={},
            runs={
                "run-1": {
                    "run_id": "run-1",
                    "status": "queued_local",
                    "created_at": "2026-04-08T10:00:00Z",
                    "updated_at": "2026-04-08T10:00:00Z",
                    "context": {"workspace_id": "default", "metadata": {}},
                }
            },
            ORION_LOCAL_COMPANION_ENABLED=True,
            ORION_LOCAL_LEASE_SECONDS=30,
            ORION_RUNTIME_POLICY_MODE_DEFAULT="local_default",
            _utc_now=lambda: datetime.fromisoformat("2026-04-08T10:00:05"),
            _utc_now_iso=lambda: "2026-04-08T10:00:05Z",
            _parse_utc_ts=lambda value: datetime.fromisoformat(str(value).replace("Z", "")) if value else None,
        )
        try:
            with (
                patch.object(local_queue, "_cleanup_stale_local_claims", return_value=[]),
                patch.object(local_queue.run_state_repository, "sync_list_fleet_workers", side_effect=AssertionError("durable worker merge should not run on queue hot path")),
                patch.object(local_queue, "_build_local_fleet_pressure_snapshot", side_effect=AssertionError("heavy pressure path should not run")),
                patch.object(
                    local_queue,
                    "_build_lightweight_local_fleet_pressure_snapshot",
                    return_value={
                        "state": "healthy",
                        "summary": "Queue progressing.",
                        "queued_count": 1,
                        "claimed_count": 0,
                        "online_workers": 1,
                        "busy_workers": 0,
                        "idle_workers": 1,
                        "backlog_per_online_worker": 1.0,
                        "retry_after_seconds": 0,
                        "workspace_hotspots": [],
                        "specialist_hotspots": [],
                        "dead_letters": None,
                    },
                ) as mock_lightweight,
            ):
                payload = local_queue.handle_get_local_run_queue(workspace_id="default")
        finally:
            local_queue._server = original_server

        self.assertEqual(payload["pressure"]["state"], "healthy")
        mock_lightweight.assert_called_once()

    def test_handle_cleanup_local_run_queue_dry_run_only_matches_stale_unclaimed_workspace_runs(self) -> None:
        original_server = local_queue._server
        test_server = SimpleNamespace(
            LOCAL_QUEUE_LOCK=threading.Lock(),
            LOCAL_WORKER_REGISTRY={},
            LOCAL_PENDING_RUN_IDS=["stale-1", "fresh-1", "claimed-1", "other-ws", "running-1", "missing-1"],
            LOCAL_CLAIMED_RUNS={"claimed-1": {"worker_id": "worker-1", "claimed_at": "2026-04-08T09:40:00Z"}},
            runs={
                "stale-1": {
                    "run_id": "stale-1",
                    "status": "queued_local",
                    "created_at": "2026-04-08T09:40:00Z",
                    "updated_at": "2026-04-08T09:40:00Z",
                    "context": {"workspace_id": "default", "metadata": {}},
                },
                "fresh-1": {
                    "run_id": "fresh-1",
                    "status": "queued_local",
                    "created_at": "2026-04-08T09:58:30Z",
                    "updated_at": "2026-04-08T09:58:30Z",
                    "context": {"workspace_id": "default", "metadata": {}},
                },
                "claimed-1": {
                    "run_id": "claimed-1",
                    "status": "queued_local",
                    "created_at": "2026-04-08T09:40:00Z",
                    "updated_at": "2026-04-08T09:40:00Z",
                    "context": {"workspace_id": "default", "metadata": {}},
                },
                "other-ws": {
                    "run_id": "other-ws",
                    "status": "queued_local",
                    "created_at": "2026-04-08T09:40:00Z",
                    "updated_at": "2026-04-08T09:40:00Z",
                    "context": {"workspace_id": "other", "metadata": {}},
                },
                "running-1": {
                    "run_id": "running-1",
                    "status": "running_local",
                    "created_at": "2026-04-08T09:40:00Z",
                    "updated_at": "2026-04-08T09:40:00Z",
                    "context": {"workspace_id": "default", "metadata": {}},
                },
            },
            ORION_LOCAL_LEASE_SECONDS=30,
            _utc_now=lambda: datetime.fromisoformat("2026-04-08T10:00:30"),
            _utc_now_iso=lambda: "2026-04-08T10:00:30Z",
            _parse_utc_ts=lambda value: datetime.fromisoformat(str(value).replace("Z", "")) if value else None,
        )
        local_queue._server = test_server
        try:
            with patch.object(local_queue, "_cleanup_stale_local_claims", return_value=[]):
                payload = local_queue.handle_cleanup_local_run_queue(
                    "default",
                    older_than_seconds=600,
                    limit=10,
                    dry_run=True,
                )
        finally:
            local_queue._server = original_server

        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["matched_count"], 1)
        self.assertEqual(payload["cleaned_count"], 0)
        self.assertEqual(payload["selected_count"], 1)
        self.assertEqual(payload["runs"][0]["run_id"], "stale-1")
        self.assertEqual(payload["runs"][0]["action"], "preview")
        self.assertEqual(payload["skipped_counts"]["below_threshold"], 1)
        self.assertEqual(payload["skipped_counts"]["claimed_or_active"], 1)
        self.assertEqual(payload["skipped_counts"]["workspace_mismatch"], 1)
        self.assertEqual(payload["skipped_counts"]["status_mismatch"], 1)
        self.assertEqual(payload["skipped_counts"]["missing_run"], 1)
        self.assertEqual(test_server.LOCAL_PENDING_RUN_IDS[0], "stale-1")

    def test_handle_cleanup_local_run_queue_cancels_and_archives_stale_runs_only(self) -> None:
        original_server = local_queue._server
        archived = []
        removed = []
        transition_outbox = []
        persisted = []
        repo_snapshots = []
        repo_transitions = []
        repo_archives = []
        log_queue = queue.Queue()
        runs = {
            "stale-1": {
                "run_id": "stale-1",
                "status": "queued_local",
                "created_at": "2026-04-08T09:40:00Z",
                "updated_at": "2026-04-08T09:40:00Z",
                "logs": log_queue,
                "context": {"workspace_id": "default", "tenant_id": "default", "metadata": {"trace_id": "trace-1"}},
            },
            "claimed-1": {
                "run_id": "claimed-1",
                "status": "queued_local",
                "created_at": "2026-04-08T09:40:00Z",
                "updated_at": "2026-04-08T09:40:00Z",
                "context": {"workspace_id": "default", "tenant_id": "default", "metadata": {}},
            },
            "fresh-1": {
                "run_id": "fresh-1",
                "status": "queued_local",
                "created_at": "2026-04-08T09:59:30Z",
                "updated_at": "2026-04-08T09:59:30Z",
                "context": {"workspace_id": "default", "tenant_id": "default", "metadata": {}},
            },
        }
        test_server = SimpleNamespace(
            LOCAL_QUEUE_LOCK=threading.Lock(),
            LOCAL_WORKER_REGISTRY={},
            LOCAL_PENDING_RUN_IDS=["stale-1", "claimed-1", "fresh-1"],
            LOCAL_CLAIMED_RUNS={"claimed-1": {"worker_id": "worker-1", "claimed_at": "2026-04-08T09:40:00Z"}},
            runs=runs,
            RUN_QUEUE_INDEX={id(log_queue): "stale-1"},
            ORION_LOCAL_LEASE_SECONDS=30,
            _utc_now=lambda: datetime.fromisoformat("2026-04-08T10:00:30"),
            _utc_now_iso=lambda: "2026-04-08T10:00:30Z",
            _parse_utc_ts=lambda value: datetime.fromisoformat(str(value).replace("Z", "")) if value else None,
            emit_log=lambda *args, **kwargs: None,
            _archive_run_if_terminal=lambda run_id, run: (archived.append((run_id, run["status"])), run.__setitem__("_archived", True)),
            _remove_live_run_state=lambda run_id: removed.append(run_id),
        )
        local_queue._server = test_server
        try:
            with (
                patch.object(local_queue, "_cleanup_stale_local_claims", return_value=[]),
                patch.object(local_queue, "_persist_local_runtime_state", side_effect=lambda: persisted.append(True)),
                patch("server_modules.run_service._persist_run_repository_snapshot", side_effect=lambda run_id, run, *, state, trace_id: repo_snapshots.append((run_id, state, trace_id))),
                patch("server_modules.run_service._record_run_repository_transition", side_effect=lambda run_id, *, from_state, to_state, actor, trace_id: repo_transitions.append((run_id, from_state, to_state, actor, trace_id))),
                patch("server_modules.run_service._emit_run_transition_outbox_event", side_effect=lambda run_id, **kwargs: transition_outbox.append((run_id, kwargs["to_state"], kwargs["actor"]))),
                patch("server_modules.run_service._archive_run_repository_payload", side_effect=lambda run_id, run, *, final_state, trace_id: repo_archives.append((run_id, final_state, trace_id))),
            ):
                payload = local_queue.handle_cleanup_local_run_queue(
                    "default",
                    older_than_seconds=600,
                    limit=10,
                    dry_run=False,
                )
        finally:
            local_queue._server = original_server

        self.assertFalse(payload["dry_run"])
        self.assertEqual(payload["matched_count"], 1)
        self.assertEqual(payload["cleaned_count"], 1)
        self.assertEqual(payload["runs"][0]["run_id"], "stale-1")
        self.assertEqual(payload["runs"][0]["action"], "cancelled_and_archived")
        self.assertEqual(payload["runs"][0]["status"], "cancelled")
        self.assertEqual(runs["stale-1"]["status"], "cancelled")
        self.assertEqual(runs["stale-1"]["result_data"]["error"], "stale_local_queue_cancelled")
        self.assertEqual(runs["claimed-1"]["status"], "queued_local")
        self.assertEqual(runs["fresh-1"]["status"], "queued_local")
        self.assertEqual(test_server.LOCAL_PENDING_RUN_IDS, ["claimed-1", "fresh-1"])
        self.assertEqual(test_server.LOCAL_CLAIMED_RUNS["claimed-1"]["worker_id"], "worker-1")
        self.assertEqual(archived, [("stale-1", "cancelled")])
        self.assertEqual(removed, ["stale-1"])
        self.assertEqual(persisted, [True])
        self.assertEqual(repo_snapshots, [("stale-1", "cancelled", "trace-1")])
        self.assertEqual(repo_archives, [("stale-1", "cancelled", "trace-1")])
        self.assertEqual(repo_transitions, [("stale-1", "queued_local", "cancelled", "operator_local_queue_cleanup", "trace-1")])
        self.assertEqual(transition_outbox, [("stale-1", "cancelled", "operator_local_queue_cleanup")])
        self.assertNotIn(id(log_queue), test_server.RUN_QUEUE_INDEX)

    def test_handle_claim_local_run_returns_saturated_backpressure_instead_of_crashing(self) -> None:
        original_server = local_queue._server
        local_queue._server = SimpleNamespace(
            LOCAL_QUEUE_LOCK=threading.Lock(),
            LOCAL_WORKER_REGISTRY={},
            LOCAL_PENDING_RUN_IDS=[f"run-{idx}" for idx in range(8)],
            LOCAL_CLAIMED_RUNS={},
            runs={},
            ORION_LOCAL_COMPANION_ENABLED=True,
            ORION_LOCAL_LEASE_SECONDS=30,
            _utc_now=lambda: datetime.fromisoformat("2026-04-08T10:00:05"),
            _utc_now_iso=lambda: "2026-04-08T10:00:05Z",
            _parse_utc_ts=lambda value: datetime.fromisoformat(str(value).replace("Z", "")) if value else None,
        )
        try:
            with patch.object(local_queue, "_claim_local_run", return_value=None):
                payload = local_queue.handle_claim_local_run(
                    local_queue.LocalRunClaimRequest(worker_id="worker-1")
                )
        finally:
            local_queue._server = original_server

        self.assertTrue(payload["ok"])
        self.assertIsNone(payload["run"])
        self.assertEqual(payload["backpressure"]["state"], "saturated")
        self.assertEqual(payload["backpressure"]["queued_count"], 8)
        self.assertGreaterEqual(payload["backpressure"]["retry_after_seconds"], 15)

    def test_handle_get_local_workers_status_uses_lightweight_pressure_snapshot(self) -> None:
        original_server = local_queue._server
        local_queue._server = SimpleNamespace(
            LOCAL_QUEUE_LOCK=threading.Lock(),
            LOCAL_WORKER_REGISTRY={
                "worker-1": {
                    "worker_id": "worker-1",
                    "runtime_id": "worker-1",
                    "machine_id": "worker-1",
                    "workspace_id": "default",
                    "tenant_id": "default",
                    "status": "idle",
                    "current_run_id": None,
                    "last_seen_at": "2026-04-08T10:00:00Z",
                    "registered_at": "2026-04-08T10:00:00Z",
                    "execution_targets": ["local_companion"],
                }
            },
            LOCAL_PENDING_RUN_IDS=["run-1"],
            LOCAL_CLAIMED_RUNS={},
            runs={
                "run-1": {
                    "run_id": "run-1",
                    "status": "queued_local",
                    "context": {"workspace_id": "default", "metadata": {}},
                }
            },
            ORION_LOCAL_COMPANION_ENABLED=True,
            ORION_LOCAL_LEASE_SECONDS=30,
            ORION_RUNTIME_POLICY_MODE_DEFAULT="local_default",
            _utc_now=lambda: datetime.fromisoformat("2026-04-08T10:00:05"),
            _utc_now_iso=lambda: "2026-04-08T10:00:05Z",
            _parse_utc_ts=lambda value: datetime.fromisoformat(str(value).replace("Z", "")) if value else None,
        )
        try:
            with (
                patch.object(local_queue, "_cleanup_stale_local_claims", return_value=[]),
                patch.object(local_queue, "_mark_ghost_enrollments_failed", return_value=[]),
                patch.object(local_queue.run_state_repository, "sync_list_fleet_workers", side_effect=AssertionError("durable worker merge should not run on worker status hot path")),
                patch.object(local_queue, "_build_local_fleet_pressure_snapshot", side_effect=AssertionError("heavy pressure path should not run")),
                patch.object(
                    local_queue,
                    "_build_lightweight_local_fleet_pressure_snapshot",
                    return_value={
                        "state": "healthy",
                        "summary": "Queue progressing.",
                        "queued_count": 1,
                        "claimed_count": 0,
                        "online_workers": 1,
                        "busy_workers": 0,
                        "idle_workers": 1,
                        "backlog_per_online_worker": 1.0,
                        "retry_after_seconds": 0,
                        "workspace_hotspots": [],
                        "specialist_hotspots": [],
                        "dead_letters": None,
                    },
                ) as mock_lightweight,
            ):
                payload = local_queue.handle_get_local_workers_status()
        finally:
            local_queue._server = original_server

        self.assertEqual(payload["pressure"]["state"], "healthy")
        self.assertEqual(payload["summary"]["online"], 1)
        mock_lightweight.assert_called_once()

    def test_build_local_scale_safety_baseline_includes_dead_letters_and_hotspots(self) -> None:
        original_server = local_queue._server
        local_queue._server = SimpleNamespace(
            LOCAL_QUEUE_LOCK=threading.Lock(),
            LOCAL_WORKER_REGISTRY={
                "worker-1": {
                    "worker_id": "worker-1",
                    "current_run_id": "run-live",
                    "last_seen_at": "2026-04-08T10:00:00Z",
                }
            },
            LOCAL_PENDING_RUN_IDS=["run-1", "run-2", "run-3", "run-4", "run-5"],
            LOCAL_CLAIMED_RUNS={"run-live": {"worker_id": "worker-1"}},
            runs={
                "run-1": {"context": {"workspace_id": "ws-a", "metadata": {"specialist_key": "spec-a"}}},
                "run-2": {"context": {"workspace_id": "ws-a", "metadata": {"specialist_key": "spec-a"}}},
                "run-3": {"context": {"workspace_id": "ws-b", "metadata": {"specialist_key": "spec-b"}}},
                "run-4": {"context": {"workspace_id": "ws-b", "metadata": {"specialist_key": "spec-c"}}},
                "run-5": {"context": {"workspace_id": "ws-c", "metadata": {}}},
                "run-live": {"context": {"workspace_id": "ws-a", "metadata": {"specialist_key": "spec-a"}}},
            },
            ORION_LOCAL_LEASE_SECONDS=30,
            _utc_now=lambda: datetime.fromisoformat("2026-04-08T10:00:05"),
            _utc_now_iso=lambda: "2026-04-08T10:00:05Z",
            _parse_utc_ts=lambda value: datetime.fromisoformat(str(value).replace("Z", "")) if value else None,
        )
        try:
            with (
                patch.object(local_queue, "_cleanup_stale_local_claims", return_value=[]),
                patch.object(local_queue.run_state_repository, "sync_get_local_queue_dead_letter_status", return_value={
                    "dead_letter_count": 2,
                    "workspace_hotspots": [{"workspace_id": "ws-a", "count": 2}],
                    "specialist_hotspots": [{"specialist_key": "spec-a", "count": 3}],
                    "total_failure_count": 4,
                    "last_recorded_at": "2026-04-08T10:00:00Z",
                }),
            ):
                payload = local_queue.build_local_scale_safety_baseline()
        finally:
            local_queue._server = original_server

        self.assertTrue(payload["durable_intake"])
        self.assertEqual(payload["queue_strategy"], "workspace_specialist_weighted_fifo")
        self.assertTrue(payload["queued_state_instead_of_collapse"])
        self.assertEqual(payload["state"], "saturated")
        self.assertEqual(payload["dead_letters"]["dead_letter_count"], 2)
        self.assertEqual(payload["workspace_hotspots"][0]["workspace_id"], "ws-a")
        self.assertIn(
            "spec-a",
            [entry.get("specialist_key") for entry in payload["specialist_hotspots"]],
        )

    def test_handle_get_local_run_queue_marks_stale_machine_pinned_runs(self) -> None:
        original_server = local_queue._server
        local_queue._server = SimpleNamespace(
            LOCAL_QUEUE_LOCK=threading.Lock(),
            LOCAL_WORKER_REGISTRY={},
            LOCAL_PENDING_RUN_IDS=["run-1"],
            LOCAL_CLAIMED_RUNS={},
            runs={
                "run-1": {
                    "run_id": "run-1",
                    "status": "queued_local",
                    "created_at": "2026-04-08T09:40:00Z",
                    "updated_at": "2026-04-08T09:40:00Z",
                    "context": {
                        "workspace_id": "default",
                        "metadata": {
                            "machine_target": "phase49-proof-worker",
                            "runtime_attachment_id": "local_companion:phase49-proof-worker",
                        },
                    },
                }
            },
            ORION_LOCAL_COMPANION_ENABLED=True,
            ORION_LOCAL_LEASE_SECONDS=30,
            _utc_now=lambda: datetime.fromisoformat("2026-04-08T10:00:30"),
            _utc_now_iso=lambda: "2026-04-08T10:00:30Z",
            _parse_utc_ts=lambda value: datetime.fromisoformat(str(value).replace("Z", "")) if value else None,
        )
        try:
            with (
                patch.object(local_queue, "_cleanup_stale_local_claims", return_value=[]),
                patch.object(local_queue.run_state_repository, "sync_list_fleet_workers", return_value=[]),
                patch.object(local_queue.run_state_repository, "sync_get_local_queue_dead_letter_status", return_value={"dead_letter_count": 0, "workspace_hotspots": [], "specialist_hotspots": [], "total_failure_count": 0, "last_recorded_at": None}),
            ):
                payload = local_queue.handle_get_local_run_queue(workspace_id="default")
        finally:
            local_queue._server = original_server

        queued = payload["queued_runs"]
        self.assertEqual(len(queued), 1)
        item = queued[0]
        self.assertEqual(item["preferred_runtime_id"], "phase49-proof-worker")
        self.assertEqual(item["machine_target"], "phase49-proof-worker")
        self.assertEqual(item["queue_state"], "stale_waiting_for_preferred_runtime")
        self.assertIn("phase49-proof-worker", item["queue_reason"])
        self.assertFalse(item["preferred_runtime_online"])
        self.assertEqual(item["online_worker_count"], 0)
        self.assertGreaterEqual(item["queue_age_seconds"], 600)
        self.assertEqual(payload["pressure"]["state"], "saturated")

    def test_handle_get_local_run_queue_marks_stale_unpinned_runs_waiting_for_worker(self) -> None:
        original_server = local_queue._server
        local_queue._server = SimpleNamespace(
            LOCAL_QUEUE_LOCK=threading.Lock(),
            LOCAL_WORKER_REGISTRY={},
            LOCAL_PENDING_RUN_IDS=["run-1"],
            LOCAL_CLAIMED_RUNS={},
            runs={
                "run-1": {
                    "run_id": "run-1",
                    "status": "queued_local",
                    "created_at": "2026-04-08T09:40:00Z",
                    "updated_at": "2026-04-08T09:40:00Z",
                    "context": {
                        "workspace_id": "default",
                        "metadata": {},
                    },
                }
            },
            ORION_LOCAL_COMPANION_ENABLED=True,
            ORION_LOCAL_LEASE_SECONDS=30,
            _utc_now=lambda: datetime.fromisoformat("2026-04-08T10:00:30"),
            _utc_now_iso=lambda: "2026-04-08T10:00:30Z",
            _parse_utc_ts=lambda value: datetime.fromisoformat(str(value).replace("Z", "")) if value else None,
        )
        try:
            with (
                patch.object(local_queue, "_cleanup_stale_local_claims", return_value=[]),
                patch.object(local_queue.run_state_repository, "sync_list_fleet_workers", return_value=[]),
                patch.object(local_queue.run_state_repository, "sync_get_local_queue_dead_letter_status", return_value={"dead_letter_count": 0, "workspace_hotspots": [], "specialist_hotspots": [], "total_failure_count": 0, "last_recorded_at": None}),
            ):
                payload = local_queue.handle_get_local_run_queue(workspace_id="default")
        finally:
            local_queue._server = original_server

        item = payload["queued_runs"][0]
        self.assertIsNone(item["preferred_runtime_id"])
        self.assertEqual(item["queue_state"], "stale_waiting_for_worker")
        self.assertEqual(item["queue_reason"], "No healthy local worker is online. The run has been unclaimed longer than the stale queue threshold.")
        self.assertFalse(item["preferred_runtime_online"])
        self.assertEqual(item["online_worker_count"], 0)

    def test_handle_get_local_run_queue_treats_recent_offline_worker_as_zero_capacity(self) -> None:
        original_server = local_queue._server
        local_queue._server = SimpleNamespace(
            LOCAL_QUEUE_LOCK=threading.Lock(),
            LOCAL_WORKER_REGISTRY={
                "worker-1": {
                    "worker_id": "worker-1",
                    "runtime_id": "worker-1",
                    "machine_id": "worker-1",
                    "status": "offline",
                    "last_seen_at": "2026-04-08T10:00:00Z",
                    "lease_seconds": 120,
                    "execution_targets": ["local"],
                }
            },
            LOCAL_PENDING_RUN_IDS=["run-1"],
            LOCAL_CLAIMED_RUNS={},
            runs={
                "run-1": {
                    "run_id": "run-1",
                    "status": "queued_local",
                    "created_at": "2026-04-08T10:00:00Z",
                    "updated_at": "2026-04-08T10:00:00Z",
                    "context": {
                        "workspace_id": "default",
                        "metadata": {},
                    },
                }
            },
            ORION_LOCAL_COMPANION_ENABLED=True,
            ORION_LOCAL_LEASE_SECONDS=120,
            _utc_now=lambda: datetime.fromisoformat("2026-04-08T10:00:05"),
            _utc_now_iso=lambda: "2026-04-08T10:00:05Z",
            _parse_utc_ts=lambda value: datetime.fromisoformat(str(value).replace("Z", "")) if value else None,
        )
        try:
            with (
                patch.object(local_queue, "_cleanup_stale_local_claims", return_value=[]),
                patch.object(local_queue.run_state_repository, "sync_list_fleet_workers", return_value=[]),
                patch.object(local_queue.run_state_repository, "sync_get_local_queue_dead_letter_status", return_value={"dead_letter_count": 0, "workspace_hotspots": [], "specialist_hotspots": [], "total_failure_count": 0, "last_recorded_at": None}),
            ):
                payload = local_queue.handle_get_local_run_queue(workspace_id="default")
        finally:
            local_queue._server = original_server

        item = payload["queued_runs"][0]
        self.assertEqual(item["queue_state"], "waiting_for_worker")
        self.assertEqual(item["queue_reason"], "No healthy local worker is online.")
        self.assertEqual(item["online_worker_count"], 0)
        self.assertEqual(payload["pressure"]["online_workers"], 0)

    def test_handle_get_local_runtime_status_uses_local_registry_fast_path(self) -> None:
        original_server = local_queue._server
        local_queue._server = SimpleNamespace(
            LOCAL_QUEUE_LOCK=threading.Lock(),
            LOCAL_WORKER_REGISTRY={
                "worker-1": {
                    "worker_id": "worker-1",
                    "runtime_id": "worker-1",
                    "machine_id": "machine-1",
                    "tenant_id": "default",
                    "workspace_id": "default",
                    "runtime_type": "local",
                    "runtime_role": "generic",
                    "display_name": "Local Worker",
                    "policy_mode": "local_default",
                    "capabilities": ["shell.execute"],
                    "execution_targets": ["local"],
                    "trust_state": "verified",
                    "status": "idle",
                    "last_seen_at": "2026-04-08T10:00:00Z",
                    "registered_at": "2026-04-08T09:00:00Z",
                    "lease_seconds": 30,
                }
            },
            ORION_RUNTIME_POLICY_MODE_DEFAULT="local_default",
            ORION_LOCAL_LEASE_SECONDS=30,
            _utc_now=lambda: datetime.fromisoformat("2026-04-08T10:00:10"),
            _parse_utc_ts=lambda value: datetime.fromisoformat(str(value).replace("Z", "")) if value else None,
        )
        try:
            with (
                patch.object(local_queue, "handle_get_local_workers_status", side_effect=AssertionError("slow fleet status path should not be used")),
                patch.object(local_queue, "_hydrate_worker_from_durable_registry", side_effect=AssertionError("durable hydration should not be needed")),
            ):
                payload = local_queue.handle_get_local_runtime_status("worker-1")
        finally:
            local_queue._server = original_server

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["runtime_id"], "worker-1")
        self.assertEqual(payload["machine_id"], "machine-1")
        self.assertEqual(payload["runtime"]["status"], "idle")
        self.assertTrue(payload["runtime"]["online"])

    def test_handle_get_local_workers_status_exposes_permission_and_policy_state(self) -> None:
        original_server = local_queue._server
        local_queue._server = SimpleNamespace(
            LOCAL_QUEUE_LOCK=threading.Lock(),
            LOCAL_WORKER_REGISTRY={
                "machine-1": {
                    "machine_id": "machine-1",
                    "runtime_id": "machine-1",
                    "tenant_id": "tenant-1",
                    "workspace_id": "ws-1",
                    "runtime_type": "local_companion",
                    "display_name": "Desk Mac",
                    "platform": "darwin",
                    "policy_mode": "trusted_full_access",
                    "capabilities": ["screenshot.capture", "browser_automation.interactive", "shell.execute"],
                    "execution_targets": ["local_companion"],
                    "status": "idle",
                    "last_seen_at": "2026-04-08T10:00:00Z",
                    "registered_at": "2026-04-08T09:00:00Z",
                    "trust_state": "verified",
                    "permission_probe": {"screen_recording": {"status": "granted", "source": "probe"}},
                    "permission_probe_updated_at": "2026-04-08T10:00:00Z",
                }
            },
            LOCAL_PENDING_RUN_IDS=[],
            LOCAL_CLAIMED_RUNS={},
            ORION_LOCAL_COMPANION_ENABLED=True,
            ORION_LOCAL_LEASE_SECONDS=30,
            ORION_RUNTIME_POLICY_MODE_DEFAULT="local_default",
            _utc_now=lambda: datetime.fromisoformat("2026-04-08T10:00:10"),
            _utc_now_iso=lambda: "2026-04-08T10:00:10Z",
            _parse_utc_ts=lambda value: datetime.fromisoformat(str(value).replace("Z", "")) if value else None,
        )
        safe_mode_service.set_safe_mode(enabled=True, workspace_id="ws-1", reason="Maintenance window")
        safe_mode_service.set_kill_switch(scope="machine", enabled=True, machine_id="machine-1", reason="Operator stop")
        try:
            with (
                patch.object(local_queue, "_cleanup_stale_local_claims", return_value=[]),
                patch.object(local_queue, "_mark_ghost_enrollments_failed", return_value=[]),
                patch.object(local_queue.run_state_repository, "sync_list_fleet_workers", return_value=[]),
                patch.object(local_queue.run_state_repository, "sync_get_local_queue_dead_letter_status", return_value={"dead_letter_count": 0, "workspace_hotspots": [], "specialist_hotspots": [], "total_failure_count": 0, "last_recorded_at": None}),
            ):
                payload = local_queue.handle_get_local_workers_status()
        finally:
            local_queue._server = original_server
            safe_mode_service.reset_state_for_tests()

        item = payload["items"][0]
        self.assertEqual(item["permission_probe"]["screen_recording"]["status"], "granted")
        self.assertEqual(item["permission_probe"]["accessibility"]["status"], "unknown")
        self.assertTrue(item["safe_mode_status"]["active"])
        self.assertTrue(item["kill_switch_status"]["active"])
        self.assertEqual(payload["summary"]["revoked"], 0)
        self.assertEqual(payload["pressure"]["state"], "healthy")

    def test_handle_delete_local_runtime_marks_machine_revoked(self) -> None:
        original_server = local_queue._server
        local_queue._server = SimpleNamespace(
            LOCAL_QUEUE_LOCK=threading.Lock(),
            LOCAL_WORKER_REGISTRY={
                "machine-1": {
                    "machine_id": "machine-1",
                    "runtime_id": "machine-1",
                    "workspace_id": "default",
                    "tenant_id": "default",
                    "status": "idle",
                    "capabilities": [],
                    "execution_targets": ["local_companion"],
                }
            },
            _utc_now_iso=lambda: "2026-04-08T11:00:00Z",
        )
        try:
            with (
                patch.object(local_queue, "_persist_local_runtime_state", return_value=None),
                patch.object(local_queue, "_emit_machine_outbox_event", return_value=None),
                patch.object(
                    local_queue,
                    "handle_get_local_workers_status",
                    return_value={"items": [{"machine_id": "machine-1", "control_state": "revoked"}]},
                ),
            ):
                payload = local_queue.handle_delete_local_runtime("machine-1")
        finally:
            local_queue._server = original_server

        self.assertTrue(payload["revoked"])
        self.assertFalse(payload["deleted"])
        self.assertEqual(local_queue._server, original_server)

    def test_handle_get_local_run_control_state_pauses_revoked_machine(self) -> None:
        original_server = local_queue._server
        local_queue._server = SimpleNamespace(
            LOCAL_QUEUE_LOCK=threading.Lock(),
            LOCAL_WORKER_REGISTRY={"machine-1": {"machine_id": "machine-1", "control_state": "revoked"}},
            runs={
                "00000000-0000-0000-0000-000000000001": {
                    "status": "running_local",
                    "context": {"metadata": {"machine_id": "machine-1"}},
                }
            },
        )
        try:
            payload = local_queue.handle_get_local_run_control_state(
                local_queue.uuid.UUID("00000000-0000-0000-0000-000000000001"),
                local_queue.LocalRunControlStatePayload(worker_id="machine-1"),
            )
        finally:
            local_queue._server = original_server

        self.assertTrue(payload["pause_requested"])
        self.assertEqual(payload["wait_reason"], "machine_revoked")
        self.assertEqual(payload["machine_control_state"], "revoked")

    def test_handle_request_local_runtime_hard_kill_marks_interrupting_and_streams_event(self) -> None:
        original_server = local_queue._server
        log_events = []
        local_queue._server = SimpleNamespace(
            LOCAL_QUEUE_LOCK=threading.Lock(),
            LOCAL_WORKER_REGISTRY={
                "machine-1": {
                    "machine_id": "machine-1",
                    "runtime_id": "machine-1",
                    "workspace_id": "default",
                    "tenant_id": "default",
                    "status": "busy",
                    "capabilities": [],
                    "execution_targets": ["local_companion"],
                }
            },
            LOCAL_CLAIMED_RUNS={
                "run-1": {"worker_id": "machine-1", "machine_id": "machine-1"},
            },
            runs={
                "run-1": {
                    "status": "running_local",
                    "context": {"metadata": {"machine_id": "machine-1"}},
                    "logs": object(),
                }
            },
            _utc_now_iso=lambda: "2026-04-08T11:05:00Z",
            emit_log=lambda *args, **kwargs: log_events.append((args, kwargs)),
            set_run_status=lambda *_args, **_kwargs: None,
            ORION_LOCAL_COMPANION_ENABLED=True,
            ORION_LOCAL_LEASE_SECONDS=30,
            ORION_RUNTIME_POLICY_MODE_DEFAULT="local_default",
            _utc_now=lambda: datetime.fromisoformat("2026-04-08T11:05:00"),
            _parse_utc_ts=lambda value: datetime.fromisoformat(str(value).replace("Z", "")) if value else None,
        )
        try:
            with (
                patch.object(local_queue, "_persist_local_runtime_state", return_value=None),
                patch.object(local_queue, "_emit_machine_outbox_event", return_value=None),
                patch.object(local_queue, "handle_get_local_workers_status", return_value={"items": [{"machine_id": "machine-1", "control_state": "interrupting"}]}),
            ):
                payload = local_queue.handle_request_local_runtime_hard_kill(
                    "machine-1",
                    reason="Operator stop",
                    requested_by="owner-1",
                )
        finally:
            local_queue._server = original_server

        self.assertEqual(payload["machine_id"], "machine-1")
        self.assertEqual(payload["machine"]["control_state"], "interrupting")
        self.assertEqual(payload["requested_run_ids"], ["run-1"])
        events = local_queue._list_runtime_control_events("machine-1", since_sequence=0)
        self.assertEqual([item["event"] for item in events], ["run_interrupt", "hard_kill"])

    def test_handle_get_local_workers_status_prefers_live_local_registry_over_durable_merge(self) -> None:
        original_server = local_queue._server
        local_queue._server = SimpleNamespace(
            LOCAL_QUEUE_LOCK=threading.Lock(),
            LOCAL_WORKER_REGISTRY={},
            LOCAL_PENDING_RUN_IDS=[],
            LOCAL_CLAIMED_RUNS={},
            ORION_LOCAL_COMPANION_ENABLED=True,
            ORION_LOCAL_LEASE_SECONDS=30,
            ORION_RUNTIME_POLICY_MODE_DEFAULT="local_default",
            _utc_now=lambda: datetime.fromisoformat("2026-04-08T10:00:10"),
            _utc_now_iso=lambda: "2026-04-08T10:00:10Z",
            _parse_utc_ts=lambda value: datetime.fromisoformat(str(value).replace("Z", "")) if value else None,
        )
        try:
            with (
                patch.object(local_queue, "_cleanup_stale_local_claims", return_value=[]),
                patch.object(local_queue, "_mark_ghost_enrollments_failed", return_value=[]),
                patch.object(
                    local_queue.run_state_repository,
                    "sync_list_fleet_workers",
                    side_effect=AssertionError("durable fleet merge should not run on live worker status hot path"),
                ),
                patch.object(
                    local_queue,
                    "_build_lightweight_local_fleet_pressure_snapshot",
                    return_value={
                        "state": "healthy",
                        "summary": "No queued local runs.",
                        "queued_count": 0,
                        "claimed_count": 0,
                        "online_workers": 0,
                        "busy_workers": 0,
                        "idle_workers": 0,
                        "backlog_per_online_worker": None,
                        "retry_after_seconds": 0,
                        "workspace_hotspots": [],
                        "specialist_hotspots": [],
                        "dead_letters": None,
                    },
                ),
            ):
                payload = local_queue.handle_get_local_workers_status()
        finally:
            local_queue._server = original_server

        self.assertEqual(payload["summary"]["prewarmed_ready"], 0)
        self.assertEqual(payload["summary"]["hosted_ready"], 0)
        self.assertEqual(payload["items"], [])

    def test_handle_start_local_runtime_sets_captain_identity_and_surface_hooks(self) -> None:
        original_server = local_queue._server
        local_queue._server = SimpleNamespace(
            LOCAL_QUEUE_LOCK=threading.Lock(),
            LOCAL_WORKER_REGISTRY={
                "machine-1": {
                    "machine_id": "machine-1",
                    "runtime_id": "machine-1",
                    "tenant_id": "tenant-1",
                    "workspace_id": "ws-1",
                    "runtime_type": "local_companion",
                    "display_name": "Desk Mac",
                    "platform": "darwin",
                    "policy_mode": "trusted_full_access",
                    "capabilities": ["shell.execute"],
                    "execution_targets": ["local_companion"],
                    "status": "idle",
                    "registered_at": "2026-04-08T09:00:00Z",
                    "last_seen_at": "2026-04-08T09:59:50Z",
                    "control_state": "active",
                    "lifecycle_state": "registered",
                }
            },
            LOCAL_PENDING_RUN_IDS=[],
            LOCAL_CLAIMED_RUNS={},
            ORION_LOCAL_COMPANION_ENABLED=True,
            ORION_LOCAL_LEASE_SECONDS=30,
            ORION_RUNTIME_POLICY_MODE_DEFAULT="local_default",
            _utc_now=lambda: datetime.fromisoformat("2026-04-08T10:00:10"),
            _utc_now_iso=lambda: "2026-04-08T10:00:10Z",
            _parse_utc_ts=lambda value: datetime.fromisoformat(str(value).replace("Z", "")) if value else None,
            runs={},
        )
        try:
            with (
                patch.object(local_queue, "_hydrate_worker_from_durable_registry", return_value=None),
                patch.object(local_queue, "_persist_local_runtime_state", return_value=None),
                patch.object(local_queue, "_sync_durable_fleet_worker", return_value=None),
                patch.object(local_queue, "_emit_machine_outbox_event", return_value=None),
                patch.object(local_queue, "_cleanup_stale_local_claims", return_value=[]),
                patch.object(local_queue, "_mark_ghost_enrollments_failed", return_value=[]),
                patch.object(local_queue.run_state_repository, "sync_list_fleet_workers", return_value=[]),
                patch.object(local_queue.run_state_repository, "sync_get_local_queue_dead_letter_status", return_value={"dead_letter_count": 0, "workspace_hotspots": [], "specialist_hotspots": [], "total_failure_count": 0, "last_recorded_at": None}),
            ):
                payload = local_queue.handle_start_local_runtime(
                    "machine-1",
                    local_queue.LocalWorkerHeartbeatPayload(
                        note="Captain booted",
                        runtime_role="captain",
                        summary_channel="captain-feed",
                        artifact_channel="captain-artifacts",
                        summary_text="Captain started and ready.",
                        artifacts=[{"name": "report.md", "path": "/tmp/report.md", "review_required": True}],
                        health_state="healthy",
                    ),
                )
        finally:
            local_queue._server = original_server

        runtime = payload["runtime"]
        self.assertEqual(runtime["runtime_role"], "captain")
        self.assertEqual(runtime["lifecycle_state"], "running")
        self.assertEqual(runtime["summary_channel"], "captain-feed")
        self.assertTrue(runtime["local_private_memory_only"])
        self.assertEqual(runtime["last_summary"], "Captain started and ready.")
        self.assertEqual(runtime["last_artifacts"][0]["path"], "/tmp/report.md")

    def test_handle_stop_and_recover_local_runtime_tracks_recovery_ids(self) -> None:
        original_server = local_queue._server
        local_queue._server = SimpleNamespace(
            LOCAL_QUEUE_LOCK=threading.Lock(),
            LOCAL_WORKER_REGISTRY={
                "machine-2": {
                    "machine_id": "machine-2",
                    "runtime_id": "machine-2",
                    "tenant_id": "tenant-1",
                    "workspace_id": "ws-1",
                    "runtime_type": "local_companion",
                    "runtime_role": "specialist",
                    "install_id": "install-1",
                    "specialist_key": "support-bot",
                    "display_name": "Support Specialist",
                    "platform": "darwin",
                    "policy_mode": "trusted_full_access",
                    "capabilities": ["shell.execute"],
                    "execution_targets": ["local_companion"],
                    "status": "busy",
                    "registered_at": "2026-04-08T09:00:00Z",
                    "last_seen_at": "2026-04-08T09:59:50Z",
                    "control_state": "active",
                    "lifecycle_state": "running",
                }
            },
            LOCAL_PENDING_RUN_IDS=[],
            LOCAL_CLAIMED_RUNS={},
            ORION_LOCAL_COMPANION_ENABLED=True,
            ORION_LOCAL_LEASE_SECONDS=30,
            ORION_RUNTIME_POLICY_MODE_DEFAULT="local_default",
            _utc_now=lambda: datetime.fromisoformat("2026-04-08T10:00:10"),
            _utc_now_iso=lambda: "2026-04-08T10:00:10Z",
            _parse_utc_ts=lambda value: datetime.fromisoformat(str(value).replace("Z", "")) if value else None,
            runs={
                "run-1": {"local_worker_id": "machine-2"},
                "run-2": {"context": {"metadata": {"machine_id": "machine-2"}}},
            },
        )
        try:
            with (
                patch.object(local_queue, "_hydrate_worker_from_durable_registry", return_value=None),
                patch.object(local_queue, "_persist_local_runtime_state", return_value=None),
                patch.object(local_queue, "_sync_durable_fleet_worker", return_value=None),
                patch.object(local_queue, "_emit_machine_outbox_event", return_value=None),
                patch.object(local_queue, "_cleanup_stale_local_claims", return_value=[]),
                patch.object(local_queue, "_mark_ghost_enrollments_failed", return_value=[]),
                patch.object(local_queue.run_state_repository, "sync_list_fleet_workers", return_value=[]),
                patch.object(local_queue.run_state_repository, "sync_get_local_queue_dead_letter_status", return_value={"dead_letter_count": 0, "workspace_hotspots": [], "specialist_hotspots": [], "total_failure_count": 0, "last_recorded_at": None}),
                patch.object(local_queue, "recover_expired_worker_leases_on_startup", return_value=["run-1"]),
                patch.object(local_queue, "recover_orphaned_local_runs_on_startup", return_value=[]),
                patch.object(local_queue, "_resume_due_checkpoint_recoveries", return_value=["run-2"]),
            ):
                stopped = local_queue.handle_stop_local_runtime("machine-2", reason="Operator stop")
                recovered = local_queue.handle_recover_local_runtime(
                    "machine-2",
                    local_queue.LocalWorkerHeartbeatPayload(
                        note="Recovered after restart",
                        runtime_role="specialist",
                        install_id="install-1",
                        specialist_key="support-bot",
                    ),
                )
        finally:
            local_queue._server = original_server

        self.assertEqual(stopped["runtime"]["lifecycle_state"], "stopped")
        self.assertFalse(stopped["runtime"]["online"])
        self.assertEqual(recovered["runtime"]["lifecycle_state"], "running")
        self.assertEqual(recovered["runtime"]["last_recovered_run_ids"], ["run-1"])
        self.assertEqual(recovered["runtime"]["last_resumed_run_ids"], ["run-2"])
        self.assertEqual(recovered["runtime"]["runtime_role"], "specialist")

    def test_iter_runtime_control_stream_yields_backlog_event(self) -> None:
        local_queue._append_runtime_control_event(
            "machine-1",
            "hard_kill",
            {
                "machine_id": "machine-1",
                "reason": "Operator stop",
                "scope": "machine",
            },
        )

        iterator = local_queue.iter_runtime_control_stream(
            "machine-1",
            since_sequence=0,
            include_backlog=True,
            heartbeat_seconds=5.0,
            timeout_seconds=1.0,
        )
        payload = next(iterator)

        self.assertEqual(payload["event"], "hard_kill")
        self.assertEqual(payload["data"]["machine_id"], "machine-1")
        self.assertEqual(payload["data"]["reason"], "Operator stop")

    def test_async_runtime_control_stream_yields_without_threadpool_blocking(self) -> None:
        async def _consume_first_event():
            iterator = local_queue.aiter_runtime_control_stream(
                "machine-1",
                since_sequence=0,
                include_backlog=True,
                heartbeat_seconds=5.0,
                timeout_seconds=1.0,
            )

            async def _append_event() -> None:
                await asyncio.sleep(0.01)
                local_queue._append_runtime_control_event(
                    "machine-1",
                    "hard_kill",
                    {
                        "machine_id": "machine-1",
                        "reason": "Operator stop",
                        "scope": "machine",
                    },
                )

            append_task = asyncio.create_task(_append_event())
            try:
                return await iterator.__anext__()
            finally:
                await append_task
                await iterator.aclose()

        payload = asyncio.run(_consume_first_event())

        self.assertEqual(payload["event"], "hard_kill")
        self.assertEqual(payload["data"]["machine_id"], "machine-1")
        self.assertEqual(payload["data"]["reason"], "Operator stop")


if __name__ == "__main__":
    unittest.main()
