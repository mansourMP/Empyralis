import threading
import unittest
from datetime import datetime
import queue
from unittest.mock import patch

from fastapi import HTTPException
from server_modules import machine_lease_service


def _capture_dispatched_operation(operations=None, event=None):
    def _side_effect(awaitable, operation):
        if isinstance(operations, list):
            operations.append(operation)
        if event is not None:
            event.set()
        close_fn = getattr(awaitable, "close", None)
        if callable(close_fn):
            close_fn()
        return None

    return _side_effect


class MachineLeaseServiceTests(unittest.TestCase):
    def test_workspace_scoped_worker_accepts_legacy_default_tenant_run_for_same_workspace(self) -> None:
        allowed = machine_lease_service._worker_runtime_scope_allows_run(
            {
                "tenant_id": "tenant-1",
                "workspace_id": "ws-1",
                "machine_enrollment_scope": "workspace",
            },
            {
                "tenant_id": "default",
                "workspace_id": "ws-1",
            },
        )

        self.assertTrue(allowed)

    def test_workspace_scoped_worker_rejects_legacy_default_tenant_run_for_other_workspace(self) -> None:
        allowed = machine_lease_service._worker_runtime_scope_allows_run(
            {
                "tenant_id": "tenant-1",
                "workspace_id": "ws-1",
                "machine_enrollment_scope": "workspace",
            },
            {
                "tenant_id": "default",
                "workspace_id": "ws-2",
            },
        )

        self.assertFalse(allowed)

    def test_build_machine_presence_record_clears_stale_run_binding_when_worker_is_idle(self) -> None:
        record = machine_lease_service.build_machine_presence_record(
            previous_record={
                "runtime_id": "worker-1",
                "machine_id": "machine-1",
                "current_run_id": "run-stale",
                "status": "busy",
            },
            machine_id="worker-1",
            current_run_id=None,
            status_hint="idle",
            lease_seconds=30,
            now_iso="2026-04-11T00:00:00Z",
            note="idle_poll",
        )

        self.assertIsNone(record["current_run_id"])
        self.assertEqual(record["status"], "idle")

    def test_build_runtime_registration_record_sets_machine_identity(self) -> None:
        record = machine_lease_service.build_runtime_registration_record(
            "runtime-1",
            previous_record={},
            tenant_id="tenant-1",
            workspace_id="ws-1",
            runtime_type="local",
            display_name="My Runtime",
            platform="darwin",
            policy_mode="local_default",
            capabilities=["browser_automation"],
            execution_targets=["local"],
            instance_id="instance-1",
            capability_digest=None,
            prewarm_state="warm",
            warm_pool="hosted-primary",
            lease_seconds=30,
            now_iso="2026-04-06T00:00:00Z",
            normalize_policy_mode_fn=lambda value: str(value or "local_default"),
            capability_digest_fn=lambda capabilities: "digest-1",
        )

        self.assertEqual(record["runtime_id"], "runtime-1")
        self.assertEqual(record["machine_id"], "runtime-1")
        self.assertEqual(record["lease_seconds"], 30)
        self.assertEqual(record["trust_state"], "unverified")
        self.assertEqual(record["prewarm_state"], "warm")
        self.assertEqual(record["warm_pool"], "hosted-primary")
        self.assertEqual(record["queue_shard"], "tenant-1:ws-1:local:local")

    def test_assert_machine_session_verifies_token_and_instance(self) -> None:
        registry = {
            "runtime-1": {
                "runtime_id": "runtime-1",
                "machine_id": "runtime-1",
                "instance_id": "instance-1",
                "session_token_hash": "hash:good",
            }
        }

        verified = machine_lease_service.assert_machine_session(
            "runtime-1",
            "good",
            machine_registry=registry,
            machine_registry_lock=threading.Lock(),
            instance_id="instance-1",
            hash_token_fn=lambda token: f"hash:{token}",
            touch_machine_session_fn=lambda record: record.update(
                {
                    "session_last_authenticated_at": "2026-04-06T00:00:00Z",
                    "trust_state": "verified",
                }
            ),
        )

        self.assertEqual(verified["runtime_id"], "runtime-1")
        self.assertEqual(verified["machine_id"], "runtime-1")
        self.assertEqual(registry["runtime-1"]["trust_state"], "verified")

    def test_assert_machine_session_rejects_runtime_scope_drift(self) -> None:
        registry = {
            "runtime-1": {
                "runtime_id": "runtime-1",
                "machine_id": "runtime-1",
                "instance_id": "instance-1",
                "tenant_id": "tenant-1",
                "workspace_id": "ws-1",
                "runtime_type": "local_companion",
                "runtime_role": "specialist",
                "install_id": "install-a",
                "specialist_key": "install-a",
                "machine_enrollment_scope": "workspace",
            }
        }
        machine_lease_service.issue_machine_session(
            registry["runtime-1"],
            token_factory=lambda _: "good",
            hash_token_fn=lambda token: f"hash:{token}",
            issued_at="2026-04-06T00:00:00Z",
        )
        registry["runtime-1"]["workspace_id"] = "ws-2"

        with self.assertRaises(HTTPException) as exc:
            machine_lease_service.assert_machine_session(
                "runtime-1",
                "good",
                machine_registry=registry,
                machine_registry_lock=threading.Lock(),
                instance_id="instance-1",
                hash_token_fn=lambda token: f"hash:{token}",
                touch_machine_session_fn=lambda record: record.update({"trust_state": "verified"}),
            )

        self.assertEqual(exc.exception.status_code, 409)
        self.assertEqual(exc.exception.detail, "runtime scope changed. Re-register this machine.")

    def test_assert_machine_session_backfills_legacy_scope_binding(self) -> None:
        registry = {
            "runtime-1": {
                "runtime_id": "runtime-1",
                "machine_id": "runtime-1",
                "instance_id": "instance-1",
                "tenant_id": "tenant-1",
                "workspace_id": "ws-1",
                "runtime_type": "local_companion",
                "session_token_hash": "hash:good",
            }
        }

        verified = machine_lease_service.assert_machine_session(
            "runtime-1",
            "good",
            machine_registry=registry,
            machine_registry_lock=threading.Lock(),
            instance_id="instance-1",
            hash_token_fn=lambda token: f"hash:{token}",
            touch_machine_session_fn=lambda record: record.update({"trust_state": "verified"}),
        )

        self.assertEqual(verified["workspace_id"], "ws-1")
        self.assertEqual(registry["runtime-1"]["session_scope"]["workspace_id"], "ws-1")
        self.assertTrue(str(registry["runtime-1"].get("session_scope_key") or "").strip())

    def test_assert_machine_session_accepts_hydrated_durable_record(self) -> None:
        durable_record = {
            "runtime_id": "runtime-1",
            "machine_id": "runtime-1",
            "instance_id": "instance-1",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "runtime_type": "local_companion",
            "runtime_role": "owner",
            "session_token_hash": "hash:good",
            "session_scope": {
                "tenant_id": "tenant-1",
                "workspace_id": "ws-1",
                "runtime_type": "local_companion",
                "runtime_role": "owner",
                "install_id": "",
                "specialist_key": "",
                "instance_id": "instance-1",
                "capability_digest": "",
                "machine_enrollment_scope": "workspace",
            },
        }
        durable_record["session_scope_key"] = "tenant-1|ws-1|local_companion|owner|||instance-1||workspace"
        registry = {"runtime-1": dict(durable_record)}

        verified = machine_lease_service.assert_machine_session(
            "runtime-1",
            "good",
            machine_registry=registry,
            machine_registry_lock=threading.Lock(),
            instance_id="instance-1",
            hash_token_fn=lambda token: f"hash:{token}",
            touch_machine_session_fn=lambda record: record.update({"trust_state": "verified"}),
        )

        self.assertEqual(verified["workspace_id"], "ws-1")
        self.assertEqual(registry["runtime-1"]["trust_state"], "verified")

    def test_assert_machine_session_rejects_revoked_or_stale_record(self) -> None:
        registry = {
            "runtime-1": {
                "runtime_id": "runtime-1",
                "machine_id": "runtime-1",
                "instance_id": "instance-1",
            }
        }

        with self.assertRaises(HTTPException) as exc:
            machine_lease_service.assert_machine_session(
                "runtime-1",
                "good",
                machine_registry=registry,
                machine_registry_lock=threading.Lock(),
                instance_id="instance-1",
                hash_token_fn=lambda token: f"hash:{token}",
                touch_machine_session_fn=lambda record: record.update({"trust_state": "verified"}),
            )

        self.assertEqual(exc.exception.status_code, 409)
        self.assertEqual(exc.exception.detail, "runtime session is not active. Re-register this machine.")

    def test_claim_local_machine_lease_records_machine_and_lease_identity(self) -> None:
        pending = ["run-1"]
        claimed = {}
        persisted = []
        seen = []
        runs = {
            "run-1": {
                "status": "queued_local",
                "context": {
                    "workspace_id": "default",
                    "metadata": {"owner_user_id": "user-1"},
                },
            }
        }
        worker_registry = {
            "worker-1": {
                "runtime_id": "worker-1",
                "machine_id": "machine-1",
                "capabilities": ["browser_automation"],
            }
        }

        repo_claims = []
        repo_claimed = threading.Event()
        with patch.object(
            machine_lease_service.run_state_repository,
            "dispatch_repository_call",
            side_effect=_capture_dispatched_operation(repo_claims, repo_claimed),
        ):
            claimed_run = machine_lease_service.claim_local_machine_lease(
                "worker-1",
                required_capabilities=["browser_automation"],
                local_queue_lock=threading.Lock(),
                pending_run_ids=pending,
                claimed_runs=claimed,
                worker_registry=worker_registry,
                runs_by_id=runs,
                lease_seconds=45,
                cleanup_stale_local_claims_fn=lambda: None,
                ordered_runtime_preferences_for_run_fn=lambda run: [],
                best_online_preferred_runtime_fn=lambda ids: None,
                required_capabilities_for_run_fn=lambda run: ["browser_automation"],
                normalize_capability_ids_fn=lambda items: [str(item).strip().lower() for item in (items or [])],
                persist_local_runtime_state_fn=lambda: persisted.append(True),
                mark_local_worker_seen_fn=lambda worker_id, run_id, status, note=None: seen.append(
                    (worker_id, run_id, status, note)
                ),
                now_iso_fn=lambda: "2026-04-06T00:00:00Z",
                lease_id_factory=lambda: "lease-1",
            )
            repo_claimed.wait(timeout=1.0)

        self.assertEqual(claimed_run, "run-1")
        self.assertEqual(claimed["run-1"]["machine_id"], "machine-1")
        self.assertEqual(claimed["run-1"]["lease_id"], "lease-1")
        self.assertEqual(claimed["run-1"]["workspace_id"], "default")
        self.assertEqual(claimed["run-1"]["actor_id"], "user-1")
        self.assertEqual(persisted, [True])
        self.assertEqual(seen, [("worker-1", "run-1", "busy", "claimed_local_run")])
        self.assertIn("claim_run:run-1", repo_claims)

    def test_claim_local_machine_lease_skips_runs_outside_worker_workspace_scope(self) -> None:
        pending = ["run-foreign", "run-local"]
        claimed = {}
        runs = {
            "run-foreign": {
                "status": "queued_local",
                "context": {
                    "workspace_id": "ws-2",
                    "tenant_id": "tenant-1",
                    "metadata": {"owner_user_id": "user-1"},
                },
            },
            "run-local": {
                "status": "queued_local",
                "context": {
                    "workspace_id": "ws-1",
                    "tenant_id": "tenant-1",
                    "metadata": {"owner_user_id": "user-1"},
                },
            },
        }
        worker_registry = {
            "worker-1": {
                "runtime_id": "worker-1",
                "machine_id": "machine-1",
                "tenant_id": "tenant-1",
                "workspace_id": "ws-1",
                "machine_enrollment_scope": "workspace",
                "capabilities": [],
            }
        }

        with patch.object(
            machine_lease_service.run_state_repository,
            "dispatch_repository_call",
            side_effect=_capture_dispatched_operation(),
        ):
            claimed_run = machine_lease_service.claim_local_machine_lease(
                "worker-1",
                required_capabilities=[],
                local_queue_lock=threading.Lock(),
                pending_run_ids=pending,
                claimed_runs=claimed,
                worker_registry=worker_registry,
                runs_by_id=runs,
                lease_seconds=45,
                cleanup_stale_local_claims_fn=lambda: None,
                ordered_runtime_preferences_for_run_fn=lambda run: [],
                best_online_preferred_runtime_fn=lambda ids: None,
                required_capabilities_for_run_fn=lambda run: [],
                normalize_capability_ids_fn=lambda items: [str(item).strip().lower() for item in (items or [])],
                persist_local_runtime_state_fn=lambda: None,
                mark_local_worker_seen_fn=lambda *args, **kwargs: None,
                now_iso_fn=lambda: "2026-04-06T00:00:00Z",
            )

        self.assertEqual(claimed_run, "run-local")
        self.assertEqual(pending, ["run-foreign"])

    def test_claim_local_machine_lease_balances_workspaces_and_specialists(self) -> None:
        pending = ["run-1", "run-2"]
        claimed = {
            "existing-1": {
                "worker_id": "worker-2",
                "machine_id": "machine-2",
            }
        }
        runs = {
            "existing-1": {
                "status": "running_local",
                "context": {"workspace_id": "ws-1", "metadata": {"active_agent_install_id": "install-a"}},
            },
            "run-1": {
                "status": "queued_local",
                "context": {"workspace_id": "ws-1", "metadata": {"active_agent_install_id": "install-a"}},
            },
            "run-2": {
                "status": "queued_local",
                "context": {"workspace_id": "ws-2", "metadata": {"active_agent_install_id": "install-b"}},
            },
        }
        worker_registry = {
            "worker-1": {
                "runtime_id": "worker-1",
                "machine_id": "machine-1",
                "tenant_id": "default",
                "workspace_id": "ws-1",
                "machine_enrollment_scope": "tenant",
                "capabilities": [],
            }
        }

        with patch.object(
            machine_lease_service.run_state_repository,
            "dispatch_repository_call",
            side_effect=_capture_dispatched_operation(),
        ):
            claimed_run = machine_lease_service.claim_local_machine_lease(
                "worker-1",
                required_capabilities=[],
                local_queue_lock=threading.Lock(),
                pending_run_ids=pending,
                claimed_runs=claimed,
                worker_registry=worker_registry,
                runs_by_id=runs,
                lease_seconds=45,
                cleanup_stale_local_claims_fn=lambda: None,
                ordered_runtime_preferences_for_run_fn=lambda run: [],
                best_online_preferred_runtime_fn=lambda ids: None,
                required_capabilities_for_run_fn=lambda run: [],
                normalize_capability_ids_fn=lambda items: [str(item).strip().lower() for item in (items or [])],
                persist_local_runtime_state_fn=lambda: None,
                mark_local_worker_seen_fn=lambda *args, **kwargs: None,
                now_iso_fn=lambda: "2026-04-06T00:00:00Z",
            )

        self.assertEqual(claimed_run, "run-2")
        self.assertEqual(pending, ["run-1"])
        self.assertEqual(claimed["run-2"]["metadata"]["contention_strategy"], "workspace_specialist_weighted_fifo")
        self.assertEqual(claimed["run-2"]["metadata"]["queue_partition"], "ws-2::install-b")

    def test_claim_local_machine_lease_prioritizes_exact_preferred_runtime_match(self) -> None:
        pending = ["run-generic", "run-pinned"]
        claimed = {}
        runs = {
            "run-generic": {
                "status": "queued_local",
                "context": {"workspace_id": "default", "metadata": {}},
            },
            "run-pinned": {
                "status": "queued_local",
                "context": {"workspace_id": "default", "metadata": {"machine_target": "worker-1"}},
            },
        }
        worker_registry = {"worker-1": {"runtime_id": "worker-1", "machine_id": "machine-1", "capabilities": []}}

        with patch.object(
            machine_lease_service.run_state_repository,
            "dispatch_repository_call",
            side_effect=_capture_dispatched_operation(),
        ):
            claimed_run = machine_lease_service.claim_local_machine_lease(
                "worker-1",
                required_capabilities=[],
                local_queue_lock=threading.Lock(),
                pending_run_ids=pending,
                claimed_runs=claimed,
                worker_registry=worker_registry,
                runs_by_id=runs,
                lease_seconds=45,
                cleanup_stale_local_claims_fn=lambda: None,
                ordered_runtime_preferences_for_run_fn=lambda run: ["worker-1"] if run is runs["run-pinned"] else [],
                best_online_preferred_runtime_fn=lambda ids: ids[0] if ids else None,
                required_capabilities_for_run_fn=lambda run: [],
                normalize_capability_ids_fn=lambda items: [str(item).strip().lower() for item in (items or [])],
                persist_local_runtime_state_fn=lambda: None,
                mark_local_worker_seen_fn=lambda *args, **kwargs: None,
                now_iso_fn=lambda: "2026-04-06T00:00:00Z",
            )

        self.assertEqual(claimed_run, "run-pinned")
        self.assertEqual(pending, ["run-generic"])

    def test_claim_local_machine_lease_uses_in_memory_preferred_runtime_match_without_callback(self) -> None:
        pending = ["run-pinned"]
        claimed = {}
        runs = {
            "run-pinned": {
                "status": "queued_local",
                "context": {"workspace_id": "default", "metadata": {"machine_target": "worker-1"}},
            },
        }
        worker_registry = {
            "worker-1": {
                "runtime_id": "worker-1",
                "machine_id": "machine-1",
                "capabilities": [],
                "status": "idle",
                "last_seen_at": "2026-04-06T00:00:00Z",
                "lease_seconds": 45,
            }
        }

        with patch.object(
            machine_lease_service.run_state_repository,
            "dispatch_repository_call",
            side_effect=_capture_dispatched_operation(),
        ):
            claimed_run = machine_lease_service.claim_local_machine_lease(
                "worker-1",
                required_capabilities=[],
                local_queue_lock=threading.Lock(),
                pending_run_ids=pending,
                claimed_runs=claimed,
                worker_registry=worker_registry,
                runs_by_id=runs,
                lease_seconds=45,
                cleanup_stale_local_claims_fn=lambda: None,
                ordered_runtime_preferences_for_run_fn=lambda run: ["worker-1"],
                best_online_preferred_runtime_fn=lambda ids: (_ for _ in ()).throw(
                    AssertionError("preferred runtime callback should not be called under queue lock")
                ),
                required_capabilities_for_run_fn=lambda run: [],
                normalize_capability_ids_fn=lambda items: [str(item).strip().lower() for item in (items or [])],
                persist_local_runtime_state_fn=lambda: None,
                mark_local_worker_seen_fn=lambda *args, **kwargs: None,
                now_iso_fn=lambda: "2026-04-06T00:00:00Z",
                parse_utc_ts_fn=lambda value: datetime.fromisoformat(str(value).replace("Z", "")) if value else None,
                now_fn=lambda: datetime.fromisoformat("2026-04-06T00:00:05"),
            )

        self.assertEqual(claimed_run, "run-pinned")
        self.assertEqual(pending, [])

    def test_claim_local_machine_lease_prunes_orphaned_queue_ids_and_claims_next_valid_run(self) -> None:
        pending = ["ghost-run", "run-1"]
        claimed = {}
        persisted = []
        seen = []
        runs = {
            "run-1": {
                "status": "queued_local",
                "context": {"workspace_id": "default", "metadata": {"owner_user_id": "user-1"}},
            }
        }
        worker_registry = {"worker-1": {"runtime_id": "worker-1", "machine_id": "machine-1", "capabilities": []}}

        with patch.object(
            machine_lease_service.run_state_repository,
            "dispatch_repository_call",
            side_effect=_capture_dispatched_operation(),
        ):
            claimed_run = machine_lease_service.claim_local_machine_lease(
                "worker-1",
                required_capabilities=[],
                local_queue_lock=threading.Lock(),
                pending_run_ids=pending,
                claimed_runs=claimed,
                worker_registry=worker_registry,
                runs_by_id=runs,
                lease_seconds=45,
                cleanup_stale_local_claims_fn=lambda: None,
                ordered_runtime_preferences_for_run_fn=lambda run: [],
                best_online_preferred_runtime_fn=lambda ids: None,
                required_capabilities_for_run_fn=lambda run: [],
                normalize_capability_ids_fn=lambda items: [str(item).strip().lower() for item in (items or [])],
                persist_local_runtime_state_fn=lambda: persisted.append(True),
                mark_local_worker_seen_fn=lambda worker_id, run_id, status, note=None: seen.append(
                    (worker_id, run_id, status, note)
                ),
                now_iso_fn=lambda: "2026-04-06T00:00:00Z",
            )

        self.assertEqual(claimed_run, "run-1")
        self.assertEqual(pending, [])
        self.assertEqual(claimed["run-1"]["machine_id"], "machine-1")
        self.assertEqual(persisted, [True])
        self.assertEqual(seen, [("worker-1", "run-1", "busy", "claimed_local_run")])

    def test_claim_local_machine_lease_marks_suspended_worker_idle_after_lock_release(self) -> None:
        local_lock = threading.Lock()
        seen = []

        claimed_run = machine_lease_service.claim_local_machine_lease(
            "worker-1",
            required_capabilities=[],
            local_queue_lock=local_lock,
            pending_run_ids=["run-1"],
            claimed_runs={},
            worker_registry={"worker-1": {"runtime_id": "worker-1", "control_state": "suspended"}},
            runs_by_id={"run-1": {"status": "queued_local"}},
            lease_seconds=45,
            cleanup_stale_local_claims_fn=lambda: None,
            ordered_runtime_preferences_for_run_fn=lambda run: [],
            best_online_preferred_runtime_fn=lambda ids: None,
            required_capabilities_for_run_fn=lambda run: [],
            normalize_capability_ids_fn=lambda items: [str(item).strip().lower() for item in (items or [])],
            persist_local_runtime_state_fn=lambda: None,
            mark_local_worker_seen_fn=lambda worker_id, run_id, status, note=None: seen.append(
                (
                    worker_id,
                    run_id,
                    status,
                    note,
                    local_lock.acquire(blocking=False),
                )
            )
            or (local_lock.release() if seen and seen[-1][-1] else None),
            now_iso_fn=lambda: "2026-04-06T00:00:00Z",
        )

        self.assertIsNone(claimed_run)
        self.assertEqual(seen, [("worker-1", None, "idle", "idle_machine_suspended", True)])

    def test_release_machine_lease_claim_releases_and_marks_worker_idle(self) -> None:
        claimed = {
            "run-1": {
                "worker_id": "worker-1",
                "machine_id": "machine-1",
                "lease_id": "lease-1",
            }
        }
        persisted = []
        seen = []

        repo_releases = []
        repo_released = threading.Event()
        with patch.object(
            machine_lease_service.run_state_repository,
            "dispatch_repository_call",
            side_effect=_capture_dispatched_operation(repo_releases, repo_released),
        ):
            result = machine_lease_service.release_machine_lease_claim(
                "run-1",
                worker_id="worker-1",
                local_queue_lock=threading.Lock(),
                claimed_runs=claimed,
                persist_local_runtime_state_fn=lambda: persisted.append(True),
                mark_local_worker_seen_fn=lambda worker_id, run_id, status, note=None: seen.append(
                    (worker_id, run_id, status, note)
                ),
                status_hint="idle",
                note="paused_waiting_for_input",
            )
            repo_released.wait(timeout=1.0)

        self.assertTrue(result["released"])
        self.assertEqual(result["resolved_worker"], "worker-1")
        self.assertEqual(claimed, {})
        self.assertEqual(persisted, [True])
        self.assertEqual(seen, [("worker-1", None, "idle", "paused_waiting_for_input")])
        self.assertIn("release_claim:run-1", repo_releases)

    def test_release_machine_lease_claim_rejects_stale_lease_id(self) -> None:
        claimed = {
            "run-1": {
                "worker_id": "worker-1",
                "machine_id": "machine-1",
                "lease_id": "lease-new",
            }
        }

        with patch.object(
            machine_lease_service.run_state_repository,
            "dispatch_repository_call",
            side_effect=_capture_dispatched_operation(),
        ) as dispatch_mock:
            result = machine_lease_service.release_machine_lease_claim(
                "run-1",
                worker_id="worker-1",
                lease_id="lease-old",
                local_queue_lock=threading.Lock(),
                claimed_runs=claimed,
                persist_local_runtime_state_fn=lambda: None,
                mark_local_worker_seen_fn=lambda *args, **kwargs: None,
                status_hint="idle",
                note="paused_waiting_for_input",
            )

        self.assertFalse(result["released"])
        self.assertTrue(result["lease_mismatch"])
        self.assertEqual(claimed["run-1"]["lease_id"], "lease-new")
        dispatch_mock.assert_not_called()

    def test_cleanup_stale_machine_leases_moves_worker_offline_and_fails_run(self) -> None:
        claimed = {
            "run-1": {
                "worker_id": "worker-1",
                "machine_id": "machine-1",
                "lease_id": "lease-1",
                "claimed_at": "2026-04-06T00:00:00Z",
                "last_heartbeat_at": "2026-04-06T00:00:00Z",
                "lease_seconds": 30,
            }
        }
        worker_registry = {
            "worker-1": {
                "worker_id": "worker-1",
                "runtime_id": "worker-1",
                "machine_id": "machine-1",
                "lease_seconds": 30,
            }
        }
        logs = []
        statuses = []
        persisted = []
        run = {"status": "running_local", "logs": queue.Queue()}

        stale = machine_lease_service.cleanup_stale_machine_leases(
            now=datetime.fromisoformat("2026-04-06T00:01:00"),
            local_queue_lock=threading.Lock(),
            claimed_runs=claimed,
            worker_registry=worker_registry,
            runs_by_id={"run-1": run},
            parse_utc_ts_fn=lambda value: datetime.fromisoformat(str(value).replace("Z", "")) if value else None,
            utc_now_iso_fn=lambda: "2026-04-06T00:01:00Z",
            persist_local_runtime_state_fn=lambda: persisted.append(True),
            emit_log_fn=lambda log_queue, level, message, **kwargs: logs.append((level, message, kwargs)),
            set_run_status_fn=lambda run_id, status: statuses.append((run_id, status)),
            schedule_restored_run_resume_fn=lambda run_id, run: False,
            checkpoint_recovery_max_auto_retries=3,
            checkpoint_recovery_backoff_seconds=[0, 10, 30],
            local_worker_lost_timeout_seconds=30,
            default_lease_seconds=30,
        )

        self.assertEqual(stale, ["run-1"])
        self.assertEqual(claimed, {})
        self.assertEqual(worker_registry["worker-1"]["status"], "offline")
        self.assertIsNone(run["local_worker_id"])
        self.assertIsNone(run["machine_lease_id"])
        self.assertEqual(run["result_data"]["machine_id"], "machine-1")
        self.assertEqual(statuses, [("run-1", "failed")])
        self.assertEqual(persisted, [True])
        self.assertEqual(logs[0][2]["event"], "local_worker_lost")

    def test_cleanup_stale_machine_leases_recovers_checkpoint_run_and_schedules_resume(self) -> None:
        claimed = {
            "run-1": {
                "worker_id": "worker-1",
                "machine_id": "machine-1",
                "lease_id": "lease-1",
                "claimed_at": "2026-04-06T00:00:00Z",
                "last_heartbeat_at": "2026-04-06T00:00:00Z",
                "lease_seconds": 30,
            }
        }
        worker_registry = {
            "worker-1": {
                "worker_id": "worker-1",
                "runtime_id": "worker-1",
                "machine_id": "machine-1",
                "lease_seconds": 30,
            }
        }
        logs = []
        statuses = []
        persisted = []
        scheduled = []
        run = {
            "status": "running_local",
            "logs": queue.Queue(),
            "browser_checkpoint": {"next_action_index": 4, "session_profile": "qa-browser"},
            "context": {"metadata": {"execution_target_selected": "local_companion"}},
        }

        stale = machine_lease_service.cleanup_stale_machine_leases(
            now=datetime.fromisoformat("2026-04-06T00:01:00"),
            local_queue_lock=threading.Lock(),
            claimed_runs=claimed,
            worker_registry=worker_registry,
            runs_by_id={"run-1": run},
            parse_utc_ts_fn=lambda value: datetime.fromisoformat(str(value).replace("Z", "")) if value else None,
            utc_now_iso_fn=lambda: "2026-04-06T00:01:00Z",
            persist_local_runtime_state_fn=lambda: persisted.append(True),
            emit_log_fn=lambda log_queue, level, message, **kwargs: logs.append((level, message, kwargs)),
            set_run_status_fn=lambda run_id, status: statuses.append((run_id, status)) or run.__setitem__("status", status),
            schedule_restored_run_resume_fn=lambda run_id, live_run: scheduled.append((run_id, live_run.get("status"))) or True,
            checkpoint_recovery_max_auto_retries=3,
            checkpoint_recovery_backoff_seconds=[0, 10, 30],
            local_worker_lost_timeout_seconds=30,
            default_lease_seconds=30,
        )

        self.assertEqual(stale, ["run-1"])
        self.assertEqual(claimed, {})
        self.assertEqual(worker_registry["worker-1"]["status"], "offline")
        self.assertEqual(statuses, [("run-1", "waiting_for_input")])
        self.assertEqual(scheduled, [("run-1", "waiting_for_input")])
        self.assertTrue(run["context"]["metadata"]["browser_resume_supported"])
        self.assertEqual(run["result_data"]["error"], "local_worker_lost_recoverable")
        self.assertEqual(logs[0][2]["event"], "local_worker_lost_recoverable")
        self.assertEqual(logs[1][2]["event"], "local_resume_scheduled_after_worker_loss")

    def test_cleanup_stale_machine_leases_applies_backoff_for_repeated_checkpoint_recovery(self) -> None:
        claimed = {
            "run-1": {
                "worker_id": "worker-1",
                "machine_id": "machine-1",
                "lease_id": "lease-1",
                "claimed_at": "2026-04-06T00:00:00Z",
                "last_heartbeat_at": "2026-04-06T00:00:00Z",
                "lease_seconds": 30,
            }
        }
        worker_registry = {"worker-1": {"worker_id": "worker-1", "runtime_id": "worker-1", "machine_id": "machine-1", "lease_seconds": 30}}
        logs = []
        statuses = []
        run = {
            "status": "running_local",
            "logs": queue.Queue(),
            "browser_checkpoint": {"next_action_index": 4, "session_profile": "qa-browser"},
            "context": {
                "metadata": {
                    "execution_target_selected": "local_companion",
                    "local_worker_recovery_attempt_count": 1,
                }
            },
        }

        machine_lease_service.cleanup_stale_machine_leases(
            now=datetime.fromisoformat("2026-04-06T00:01:00"),
            local_queue_lock=threading.Lock(),
            claimed_runs=claimed,
            worker_registry=worker_registry,
            runs_by_id={"run-1": run},
            parse_utc_ts_fn=lambda value: datetime.fromisoformat(str(value).replace("Z", "")) if value else None,
            utc_now_iso_fn=lambda: "2026-04-06T00:01:00Z",
            persist_local_runtime_state_fn=lambda: None,
            emit_log_fn=lambda log_queue, level, message, **kwargs: logs.append((level, message, kwargs)),
            set_run_status_fn=lambda run_id, status: statuses.append((run_id, status)) or run.__setitem__("status", status),
            schedule_restored_run_resume_fn=lambda run_id, live_run: self.fail("backoff attempt should not resume immediately"),
            checkpoint_recovery_max_auto_retries=3,
            checkpoint_recovery_backoff_seconds=[0, 10, 30],
            local_worker_lost_timeout_seconds=30,
            default_lease_seconds=30,
        )

        self.assertEqual(statuses, [("run-1", "waiting_for_input")])
        self.assertEqual(run["result_data"]["retry_backoff_seconds"], 10)
        self.assertEqual(run["context"]["metadata"]["local_worker_recovery_attempt_count"], 2)
        self.assertEqual(logs[1][2]["event"], "local_worker_recovery_backoff")

    def test_cleanup_stale_machine_leases_stops_auto_retry_after_limit(self) -> None:
        claimed = {
            "run-1": {
                "worker_id": "worker-1",
                "machine_id": "machine-1",
                "lease_id": "lease-1",
                "claimed_at": "2026-04-06T00:00:00Z",
                "last_heartbeat_at": "2026-04-06T00:00:00Z",
                "lease_seconds": 30,
            }
        }
        worker_registry = {"worker-1": {"worker_id": "worker-1", "runtime_id": "worker-1", "machine_id": "machine-1", "lease_seconds": 30}}
        logs = []
        statuses = []
        run = {
            "status": "running_local",
            "logs": queue.Queue(),
            "browser_checkpoint": {"next_action_index": 4, "session_profile": "qa-browser"},
            "context": {
                "metadata": {
                    "execution_target_selected": "local_companion",
                    "local_worker_recovery_attempt_count": 3,
                }
            },
        }

        machine_lease_service.cleanup_stale_machine_leases(
            now=datetime.fromisoformat("2026-04-06T00:01:00"),
            local_queue_lock=threading.Lock(),
            claimed_runs=claimed,
            worker_registry=worker_registry,
            runs_by_id={"run-1": run},
            parse_utc_ts_fn=lambda value: datetime.fromisoformat(str(value).replace("Z", "")) if value else None,
            utc_now_iso_fn=lambda: "2026-04-06T00:01:00Z",
            persist_local_runtime_state_fn=lambda: None,
            emit_log_fn=lambda log_queue, level, message, **kwargs: logs.append((level, message, kwargs)),
            set_run_status_fn=lambda run_id, status: statuses.append((run_id, status)) or run.__setitem__("status", status),
            schedule_restored_run_resume_fn=lambda run_id, live_run: self.fail("exhausted recovery should not auto-resume"),
            checkpoint_recovery_max_auto_retries=3,
            checkpoint_recovery_backoff_seconds=[0, 10, 30],
            local_worker_lost_timeout_seconds=30,
            default_lease_seconds=30,
        )

        self.assertEqual(statuses, [("run-1", "waiting_for_input")])
        self.assertEqual(run["result_data"]["error"], "local_worker_recovery_exhausted")
        self.assertTrue(run["result_data"]["manual_confirmation_required"])
        self.assertTrue(run["context"]["metadata"]["local_worker_recovery_auto_retry_exhausted"])
        self.assertTrue(run["context"]["metadata"]["local_worker_recovery_manual_confirmation_required"])
        self.assertEqual(logs[0][2]["event"], "local_worker_recovery_exhausted")

    def test_cleanup_stale_machine_leases_requeues_non_checkpoint_run_before_dead_letter(self) -> None:
        claimed = {
            "run-1": {
                "worker_id": "worker-1",
                "machine_id": "machine-1",
                "lease_id": "lease-1",
                "claimed_at": "2026-04-06T00:00:00Z",
                "last_heartbeat_at": "2026-04-06T00:00:00Z",
                "lease_seconds": 30,
            }
        }
        worker_registry = {"worker-1": {"worker_id": "worker-1", "runtime_id": "worker-1", "machine_id": "machine-1", "lease_seconds": 30}}
        pending = []
        logs = []
        statuses = []
        dead_letters = []
        persisted = []
        run = {
            "status": "running_local",
            "logs": queue.Queue(),
            "context": {"workspace_id": "ws-1", "metadata": {"execution_target_selected": "local_companion", "active_agent_install_id": "install-a"}},
        }

        machine_lease_service.cleanup_stale_machine_leases(
            now=datetime.fromisoformat("2026-04-06T00:01:00"),
            local_queue_lock=threading.Lock(),
            local_pending_run_ids=pending,
            claimed_runs=claimed,
            worker_registry=worker_registry,
            runs_by_id={"run-1": run},
            parse_utc_ts_fn=lambda value: datetime.fromisoformat(str(value).replace("Z", "")) if value else None,
            utc_now_iso_fn=lambda: "2026-04-06T00:01:00Z",
            persist_local_runtime_state_fn=lambda: persisted.append(True),
            emit_log_fn=lambda log_queue, level, message, **kwargs: logs.append((level, message, kwargs)),
            set_run_status_fn=lambda run_id, status: statuses.append((run_id, status)) or run.__setitem__("status", status),
            schedule_restored_run_resume_fn=lambda run_id, live_run: False,
            checkpoint_recovery_max_auto_retries=3,
            checkpoint_recovery_backoff_seconds=[0, 10, 30],
            max_queue_retry_attempts=2,
            queue_retry_backoff_seconds=[0, 10],
            append_dead_letter_fn=lambda **kwargs: dead_letters.append(kwargs),
            local_worker_lost_timeout_seconds=30,
            default_lease_seconds=30,
        )

        self.assertEqual(statuses, [("run-1", "queued_local")])
        self.assertEqual(pending, ["run-1"])
        self.assertEqual(run["result_data"]["error"], "local_worker_requeued")
        self.assertEqual(run["context"]["metadata"]["local_queue_retry_count"], 1)
        self.assertEqual(dead_letters, [])
        self.assertTrue(persisted)
        self.assertEqual(logs[0][2]["event"], "local_worker_requeued")

    def test_cleanup_stale_machine_leases_dead_letters_non_checkpoint_run_after_retry_budget(self) -> None:
        claimed = {
            "run-1": {
                "worker_id": "worker-1",
                "machine_id": "machine-1",
                "lease_id": "lease-1",
                "claimed_at": "2026-04-06T00:00:00Z",
                "last_heartbeat_at": "2026-04-06T00:00:00Z",
                "lease_seconds": 30,
            }
        }
        worker_registry = {"worker-1": {"worker_id": "worker-1", "runtime_id": "worker-1", "machine_id": "machine-1", "lease_seconds": 30}}
        logs = []
        statuses = []
        dead_letters = []
        run = {
            "status": "running_local",
            "logs": queue.Queue(),
            "context": {
                "workspace_id": "ws-1",
                "metadata": {
                    "execution_target_selected": "local_companion",
                    "active_agent_install_id": "install-a",
                    "local_queue_retry_count": 2,
                    "local_queue_max_retries": 2,
                },
            },
        }

        machine_lease_service.cleanup_stale_machine_leases(
            now=datetime.fromisoformat("2026-04-06T00:01:00"),
            local_queue_lock=threading.Lock(),
            local_pending_run_ids=[],
            claimed_runs=claimed,
            worker_registry=worker_registry,
            runs_by_id={"run-1": run},
            parse_utc_ts_fn=lambda value: datetime.fromisoformat(str(value).replace("Z", "")) if value else None,
            utc_now_iso_fn=lambda: "2026-04-06T00:01:00Z",
            persist_local_runtime_state_fn=lambda: None,
            emit_log_fn=lambda log_queue, level, message, **kwargs: logs.append((level, message, kwargs)),
            set_run_status_fn=lambda run_id, status: statuses.append((run_id, status)) or run.__setitem__("status", status),
            schedule_restored_run_resume_fn=lambda run_id, live_run: False,
            checkpoint_recovery_max_auto_retries=3,
            checkpoint_recovery_backoff_seconds=[0, 10, 30],
            max_queue_retry_attempts=2,
            queue_retry_backoff_seconds=[0, 10],
            append_dead_letter_fn=lambda **kwargs: dead_letters.append(kwargs),
            local_worker_lost_timeout_seconds=30,
            default_lease_seconds=30,
        )

        self.assertEqual(statuses, [("run-1", "failed")])
        self.assertEqual(run["result_data"]["error"], "local_worker_lost_connection")
        self.assertEqual(dead_letters[0]["reason"], "worker_lost_retry_exhausted")
        self.assertEqual(dead_letters[0]["specialist_key"], "install-a")
        self.assertEqual(logs[0][2]["event"], "local_worker_lost")


if __name__ == "__main__":
    unittest.main()
