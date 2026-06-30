import importlib
import queue
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from server_modules import outbox_service
from server_modules import runtime_events


class OutboxServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        def fake_rust(command, payload, **_kwargs):
            operation = str((payload or {}).get("operation") or "").strip()
            if command == "run-record-decision":
                return {
                    "ok": True,
                    "decision": "allow",
                    "operation": operation,
                    "next_action": "emit_run_transition_event",
                }
            return {
                "ok": True,
                "decision": "allow",
                "operation": operation,
                "next_action": outbox_service._OUTBOX_DELIVERY_EXPECTED_NEXT_ACTIONS.get(operation, operation),
            }

        self._rust_patch = patch.object(
            outbox_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=fake_rust,
        )
        self._rust_patch.start()
        self.addCleanup(self._rust_patch.stop)

    def test_emit_run_transition_event_calls_run_record_kernel_first(self) -> None:
        persisted = []
        calls = []

        def fake_rust(command, payload, **kwargs):
            calls.append((command, payload, kwargs))
            if command == "run-record-decision":
                return {
                    "ok": True,
                    "decision": "allow",
                    "reason": "run_record_transition_outbox_allowed",
                    "operation": payload["operation"],
                    "next_action": "emit_run_transition_event",
                }
            return {
                "ok": True,
                "decision": "allow",
                "reason": "outbox_persist_allowed",
                "operation": payload["operation"],
                "next_action": "persist_outbox_event",
            }

        with patch.object(
            outbox_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=fake_rust,
        ):
            event = outbox_service.emit_run_transition_event(
                run_id="run-1",
                tenant_id="tenant-1",
                workspace_id="ws-1",
                from_state="queued",
                to_state="running",
                actor="runtime",
                trace_id="trace-1",
                persist_outbox_event_fn=lambda **kwargs: persisted.append(kwargs),
            )

        self.assertEqual(event.event_type, "run_transition")
        self.assertEqual(calls[0][0], "run-record-decision")
        self.assertEqual(calls[0][1]["operation"], "emit_transition_outbox")
        self.assertEqual(calls[0][1]["from_state"], "queued")
        self.assertEqual(calls[0][1]["to_state"], "running")
        self.assertEqual(persisted[0]["payload"]["to_state"], "running")

    def test_emit_run_transition_event_wrong_run_record_action_blocks_persist(self) -> None:
        persisted = []

        with patch.object(
            outbox_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "run_record_transition_outbox_allowed",
                "operation": "emit_transition_outbox",
                "next_action": "emit_artifact_created_events",
            },
        ):
            with self.assertRaisesRegex(RuntimeError, "unexpected next_action"):
                outbox_service.emit_run_transition_event(
                    run_id="run-1",
                    tenant_id="tenant-1",
                    workspace_id="ws-1",
                    from_state="queued",
                    to_state="running",
                    actor="runtime",
                    trace_id="trace-1",
                    persist_outbox_event_fn=lambda **kwargs: persisted.append(kwargs),
                )

        self.assertEqual(persisted, [])

    def test_emit_run_transition_event_noop_skips_persist(self) -> None:
        persisted = []

        with patch.object(
            outbox_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "run_record_transition_outbox_allowed",
                "operation": "emit_transition_outbox",
                "next_action": "noop",
            },
        ):
            event = outbox_service.emit_run_transition_event(
                run_id="run-1",
                tenant_id="tenant-1",
                workspace_id="ws-1",
                from_state="running",
                to_state="running",
                actor="runtime",
                trace_id="trace-1",
                persist_outbox_event_fn=lambda **kwargs: persisted.append(kwargs),
            )

        self.assertIsNone(event)
        self.assertEqual(persisted, [])

    def test_emit_approval_requested_event_persists_browser_payload(self) -> None:
        persisted = []

        event = outbox_service.emit_approval_requested_event(
            approval_id="approval-1",
            run_id="run-1",
            tenant_id="tenant-1",
            workspace_id="ws-1",
            prompt="Review browser run",
            ttl_seconds=60,
            expires_at="2026-04-20T00:01:00Z",
            correlation_id="corr-1",
            metadata={
                "browser_session_profile": "qa-browser",
                "browser_immutable_plan_hash": "hash-1",
                "browser_reviewed_approval_required": True,
                "browser_interactive_actions": ["click"],
            },
            persist_outbox_event_fn=lambda **kwargs: persisted.append(kwargs),
        )

        self.assertEqual(event.event_type, "approval_requested")
        self.assertEqual(
            persisted[0]["payload"]["browser"]["session_profile"],
            "qa-browser",
        )
        self.assertEqual(
            persisted[0]["payload"]["browser"]["immutable_plan_hash"],
            "hash-1",
        )
        self.assertTrue(persisted[0]["payload"]["browser"]["reviewed_approval_required"])

    def test_emit_runtime_event_wrong_outbox_next_action_blocks_persist(self) -> None:
        persisted = []

        with patch.object(
            outbox_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "outbox_persist_allowed",
                "operation": "persist_event",
                "next_action": "claim_due_outbox_events",
            },
        ):
            with self.assertRaisesRegex(RuntimeError, "unexpected next_action"):
                outbox_service.emit_runtime_event(
                    event_type="runtime_event",
                    tenant_id="tenant-1",
                    workspace_id="ws-1",
                    payload={"ok": True},
                    persist_outbox_event_fn=lambda **kwargs: persisted.append(kwargs),
                )

        self.assertEqual(persisted, [])

    def test_emit_approval_resolved_event_persists_outbox_payload(self) -> None:
        persisted = []

        event = outbox_service.emit_approval_resolved_event(
            approval_id="approval-1",
            run_id="run-1",
            tenant_id="tenant-1",
            workspace_id="ws-1",
            resolution="approved",
            actor="user-1",
            reason="ok",
            metadata={
                "browser_session_profile": "qa-browser",
                "browser_immutable_plan_hash": "hash-1",
                "browser_reviewed_approval_required": True,
            },
            trace_id="trace-1",
            persist_outbox_event_fn=lambda **kwargs: persisted.append(kwargs),
        )

        self.assertEqual(event.event_type, "approval_resolved")
        self.assertEqual(persisted[0]["event_id"], event.event_id)
        self.assertEqual(persisted[0]["payload"]["approval_id"], "approval-1")
        self.assertEqual(
            persisted[0]["payload"]["browser"]["session_profile"],
            "qa-browser",
        )

    def test_emit_artifact_created_event_persists_outbox_payload(self) -> None:
        persisted = []
        calls = []

        def fake_rust(command, payload, **kwargs):
            calls.append((command, payload, kwargs))
            if command == "run-record-decision":
                return {
                    "ok": True,
                    "decision": "allow",
                    "reason": "run_record_artifact_outbox_allowed",
                    "operation": payload["operation"],
                    "next_action": "emit_artifact_created_events",
                    "artifact_count": 1,
                }
            return {
                "ok": True,
                "decision": "allow",
                "reason": "outbox_persist_allowed",
                "operation": payload["operation"],
                "next_action": "persist_outbox_event",
            }

        with patch.object(
            outbox_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=fake_rust,
        ):
            event = outbox_service.emit_artifact_created_event(
                run_id="run-1",
                tenant_id="tenant-1",
                workspace_id="ws-1",
                artifact={"kind": "screenshot", "file_path": "/tmp/shot.png"},
                trace_id="trace-1",
                index=0,
                persist_outbox_event_fn=lambda **kwargs: persisted.append(kwargs),
            )

        self.assertEqual(event.event_type, "artifact_created")
        self.assertEqual(calls[0][0], "run-record-decision")
        self.assertEqual(calls[0][1]["operation"], "emit_artifact_outbox")
        self.assertEqual(calls[0][1]["artifact_count"], 1)
        self.assertEqual(persisted[0]["payload"]["artifact_path"], "/tmp/shot.png")

    def test_emit_artifact_created_event_wrong_run_record_action_blocks_persist(self) -> None:
        persisted = []

        with patch.object(
            outbox_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "run_record_artifact_outbox_allowed",
                "operation": "emit_artifact_outbox",
                "next_action": "emit_run_transition_event",
                "artifact_count": 1,
            },
        ):
            with self.assertRaisesRegex(RuntimeError, "unexpected next_action"):
                outbox_service.emit_artifact_created_event(
                    run_id="run-1",
                    tenant_id="tenant-1",
                    workspace_id="ws-1",
                    artifact={"kind": "screenshot", "file_path": "/tmp/shot.png"},
                    trace_id="trace-1",
                    index=0,
                    persist_outbox_event_fn=lambda **kwargs: persisted.append(kwargs),
                )

        self.assertEqual(persisted, [])

    def test_emit_channel_run_delivery_event_persists_outbox_payload(self) -> None:
        persisted = []

        event = outbox_service.emit_channel_run_delivery_event(
            channel="telegram",
            tenant_id="tenant-1",
            workspace_id="ws-1",
            run_id="run-1",
            connector_id="conn-1",
            trace_id="trace-1",
            payload={"chat_id": "chat-1"},
            persist_outbox_event_fn=lambda **kwargs: persisted.append(kwargs),
        )

        self.assertEqual(event.event_type, "channel_run_delivery")
        self.assertEqual(persisted[0]["payload"]["channel"], "telegram")
        self.assertEqual(persisted[0]["payload"]["connector_id"], "conn-1")
        self.assertEqual(persisted[0]["payload"]["delivery"]["provider"], "telegram")
        self.assertEqual(
            persisted[0]["payload"]["delivery"]["provider_idempotency_key"],
            "telegram:conn-1:run-1",
        )

    def test_persist_local_runtime_state_builds_normalized_snapshot(self) -> None:
        captured = {}

        def _replace(db_path, *, pending_run_ids, claimed_runs, runtime_registrations):
            captured["db_path"] = db_path
            captured["pending_run_ids"] = list(pending_run_ids)
            captured["claimed_runs"] = dict(claimed_runs)
            captured["runtime_registrations"] = dict(runtime_registrations)

        persisted = outbox_service.persist_local_runtime_state(
            db_path=Path("/tmp/runtime.sqlite3"),
            pending_run_ids=["run-1"],
            claimed_runs={"run-1": {"worker_id": "worker-1"}, "bad": "skip"},
            runtime_registrations={"worker-1": {"status": "idle"}, "bad": "skip"},
            replace_local_runtime_state_fn=_replace,
        )

        self.assertTrue(persisted)
        self.assertEqual(captured["pending_run_ids"], ["run-1"])
        self.assertEqual(captured["claimed_runs"], {"run-1": {"worker_id": "worker-1"}})
        self.assertEqual(captured["runtime_registrations"], {"worker-1": {"status": "idle"}})

    def test_deliver_pending_outbox_events_wrong_claim_action_blocks_claim(self) -> None:
        claim_calls = []

        def _claim_due(**kwargs):
            claim_calls.append(kwargs)
            return []

        with patch.object(
            outbox_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "outbox_claim_due_allowed",
                "operation": "claim_due",
                "next_action": "persist_outbox_event",
            },
        ):
            with self.assertRaisesRegex(RuntimeError, "unexpected next_action"):
                outbox_service.deliver_due_outbox_events_once(
                    claim_due_outbox_events_fn=_claim_due,
                    mark_outbox_event_delivered_fn=lambda *args, **kwargs: True,
                    record_outbox_delivery_failure_fn=lambda *args, **kwargs: True,
                    deliver_event_fn=lambda event: True,
                    get_outbox_delivery_status_fn=lambda: {"pending": 0},
                )

        self.assertEqual(claim_calls, [])

    def test_enqueue_local_companion_run_updates_queue_persists_and_logs(self) -> None:
        events = []
        statuses = []
        persisted = {}
        run = {
            "logs": queue.Queue(),
            "context": {
                "metadata": {
                    "execution_target_waiting_for_runtime": True,
                    "execution_target_reason": "Waiting for an online machine.",
                    "execution_target_required_capabilities": ["browser_automation"],
                }
            },
        }

        def _replace(db_path, *, pending_run_ids, claimed_runs, runtime_registrations):
            persisted["pending_run_ids"] = list(pending_run_ids)
            persisted["claimed_runs"] = dict(claimed_runs)
            persisted["runtime_registrations"] = dict(runtime_registrations)

        queued = outbox_service.enqueue_local_companion_run(
            "run-1",
            runs_by_id={"run-1": run},
            set_run_status_fn=lambda run_id, status: statuses.append((run_id, status)),
            utc_now_iso_fn=lambda: "2026-04-06T00:00:00Z",
            local_queue_lock=__import__("threading").Lock(),
            pending_run_ids=[],
            claimed_runs={},
            runtime_registrations={"worker-1": {"status": "idle"}},
            db_path=Path("/tmp/runtime.sqlite3"),
            lease_seconds=30,
            emit_log_fn=lambda logs, level, message, **kwargs: events.append(
                {"level": level, "message": message, **kwargs}
            ),
            replace_local_runtime_state_fn=_replace,
        )

        self.assertTrue(queued)
        self.assertEqual(statuses, [("run-1", "queued_local")])
        self.assertEqual(persisted["pending_run_ids"], ["run-1"])
        self.assertEqual(events[0]["message"], "Waiting for an online machine.")
        self.assertEqual(events[0]["data"]["required_capabilities"], ["browser_automation"])

    def test_replay_undelivered_events_on_startup_delivers_and_marks(self) -> None:
        delivered = []
        marked = []

        replayed = outbox_service.replay_undelivered_events_on_startup(
            older_than_seconds=30,
            claim_due_outbox_events_fn=lambda **kwargs: [
                {
                    "event_id": "evt-1",
                    "event_type": "approval_resolved",
                    "tenant_id": "tenant-1",
                    "workspace_id": "ws-1",
                    "run_id": "run-1",
                    "trace_id": "trace-1",
                    "idempotency_key": "approval_resolved:approval-1:approved",
                    "payload": {"approval_id": "approval-1"},
                    "claim_token": "claim-1",
                    "claimed_by": kwargs.get("claimed_by"),
                }
            ],
            mark_outbox_event_delivered_fn=lambda event_id, **kwargs: marked.append((event_id, kwargs.get("claim_token"))),
            deliver_event_fn=lambda event: delivered.append(event.event_id) or True,
        )

        self.assertEqual(replayed, ["evt-1"])
        self.assertEqual(delivered, ["evt-1"])
        self.assertEqual(marked, [("evt-1", "claim-1")])

    def test_deliver_due_outbox_events_once_records_failure_and_poison_isolation(self) -> None:
        failures = []
        marked = []

        result = outbox_service.deliver_due_outbox_events_once(
            claim_due_outbox_events_fn=lambda **kwargs: [
                {
                    "event_id": "evt-bad",
                    "event_type": "approval_resolved",
                    "tenant_id": "tenant-1",
                    "workspace_id": "ws-1",
                    "run_id": "run-1",
                    "trace_id": "trace-1",
                    "idempotency_key": "approval_resolved:approval-1:approved",
                    "payload": {"approval_id": "approval-1"},
                    "retry_count": 4,
                    "claim_token": "claim-bad",
                },
                {
                    "event_id": "evt-good",
                    "event_type": "artifact_created",
                    "tenant_id": "tenant-1",
                    "workspace_id": "ws-1",
                    "run_id": "run-1",
                    "trace_id": "trace-1",
                    "idempotency_key": "artifact_created:run-1:/tmp/shot.png:0",
                    "payload": {"artifact_path": "/tmp/shot.png"},
                    "retry_count": 0,
                    "claim_token": "claim-good",
                },
            ],
            mark_outbox_event_delivered_fn=lambda event_id, **kwargs: marked.append((event_id, kwargs.get("claim_token"))) or True,
            record_outbox_delivery_failure_fn=lambda event_id, **kwargs: failures.append((event_id, kwargs)) or True,
            deliver_event_fn=lambda event: event.event_id == "evt-good",
            get_outbox_delivery_status_fn=lambda: {
                "undelivered_count": 1,
                "poisoned_count": 1,
                "claimed_count": 0,
                "total_retry_count": 5,
                "max_retry_count": 5,
                "last_delivery_error": {"event_id": "evt-bad", "message": "delivery sink returned false"},
            },
        )

        self.assertEqual(marked, [("evt-good", "claim-good")])
        self.assertEqual(failures[0][0], "evt-bad")
        self.assertTrue(failures[0][1]["poison"])
        self.assertEqual(failures[0][1]["claim_token"], "claim-bad")
        self.assertEqual(result["poisoned_ids"], ["evt-bad"])
        self.assertEqual(result["delivered_ids"], ["evt-good"])
        self.assertEqual(result["status"]["last_delivery_error"]["event_id"], "evt-bad")
        self.assertEqual(result["status"]["poisoned_count"], 1)

    def test_deliver_due_outbox_events_once_defers_retry_later_without_increment(self) -> None:
        failures = []
        result = outbox_service.deliver_due_outbox_events_once(
            claim_due_outbox_events_fn=lambda **kwargs: [
                {
                    "event_id": "evt-pending",
                    "event_type": "channel_run_delivery",
                    "tenant_id": "tenant-1",
                    "workspace_id": "ws-1",
                    "run_id": "run-1",
                    "trace_id": "trace-1",
                    "idempotency_key": "channel_run_delivery:telegram:conn-1:run-1",
                    "payload": {"channel": "telegram", "connector_id": "conn-1"},
                    "retry_count": 0,
                    "claim_token": "claim-pending",
                }
            ],
            mark_outbox_event_delivered_fn=lambda event_id, **kwargs: None,
            record_outbox_delivery_failure_fn=lambda event_id, **kwargs: failures.append((event_id, kwargs)) or True,
            deliver_event_fn=lambda event: (_ for _ in ()).throw(
                outbox_service.OutboxRetryLater("pending terminal state", retry_delay_seconds=3)
            ),
            get_outbox_delivery_status_fn=lambda: {"undelivered_count": 1, "poisoned_count": 0, "claimed_count": 0},
        )

        self.assertEqual(result["deferred_ids"], ["evt-pending"])
        self.assertEqual(failures[0][0], "evt-pending")
        self.assertEqual(failures[0][1]["claim_token"], "claim-pending")
        self.assertEqual(failures[0][1]["retry_delay_seconds"], 3)
        self.assertFalse(failures[0][1]["increment_retry"])

    def test_deliver_due_outbox_events_once_skips_items_without_claim(self) -> None:
        delivered = []
        result = outbox_service.deliver_due_outbox_events_once(
            claim_due_outbox_events_fn=lambda **kwargs: [
                {
                    "event_id": "evt-unclaimed",
                    "event_type": "approval_resolved",
                    "tenant_id": "tenant-1",
                    "workspace_id": "ws-1",
                    "run_id": "run-1",
                    "trace_id": "trace-1",
                    "idempotency_key": "approval_resolved:approval-1:approved",
                    "payload": {"approval_id": "approval-1"},
                }
            ],
            mark_outbox_event_delivered_fn=lambda event_id, **kwargs: True,
            record_outbox_delivery_failure_fn=lambda event_id, **kwargs: True,
            deliver_event_fn=lambda event: delivered.append(event.event_id) or True,
            get_outbox_delivery_status_fn=lambda: {"undelivered_count": 1, "poisoned_count": 0, "claimed_count": 0},
        )

        self.assertEqual(delivered, [])
        self.assertEqual(result["attempted"], 0)

    def test_deliver_due_outbox_events_once_prefers_list_fallback_when_claim_fn_missing(self) -> None:
        listed = []
        delivered = []
        marked = []

        result = outbox_service.deliver_due_outbox_events_once(
            list_undelivered_outbox_events_fn=lambda **kwargs: listed.append(dict(kwargs)) or [
                {
                    "event_id": "evt-1",
                    "event_type": "approval_requested",
                    "tenant_id": "tenant-1",
                    "workspace_id": "ws-1",
                    "run_id": "run-1",
                    "trace_id": "trace-1",
                    "idempotency_key": "approval_requested:approval-1",
                    "payload": {"approval_id": "approval-1"},
                }
            ],
            mark_outbox_event_delivered_fn=lambda event_id, **kwargs: marked.append((event_id, kwargs.get("claim_token"))) or True,
            record_outbox_delivery_failure_fn=lambda event_id, **kwargs: True,
            deliver_event_fn=lambda event: delivered.append(event.event_id) or True,
            get_outbox_delivery_status_fn=lambda: {"undelivered_count": 0, "poisoned_count": 0, "claimed_count": 0},
        )

        self.assertEqual(len(listed), 1)
        self.assertEqual(delivered, ["evt-1"])
        self.assertEqual(marked, [("evt-1", "compat-claim:evt-1")])
        self.assertEqual(result["attempted"], 1)

    def test_deliver_due_outbox_events_once_forwards_scope_to_claim(self) -> None:
        claimed = []

        result = outbox_service.deliver_due_outbox_events_once(
            tenant_id="tenant-1",
            workspace_id="ws-1",
            run_id="run-1",
            event_type="channel_run_delivery",
            claim_due_outbox_events_fn=lambda **kwargs: claimed.append(dict(kwargs)) or [],
            mark_outbox_event_delivered_fn=lambda event_id, **kwargs: True,
            record_outbox_delivery_failure_fn=lambda event_id, **kwargs: True,
            deliver_event_fn=lambda event: True,
            get_outbox_delivery_status_fn=lambda: {"undelivered_count": 0, "poisoned_count": 0, "claimed_count": 0},
        )

        self.assertEqual(result["attempted"], 0)
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0]["tenant_id"], "tenant-1")
        self.assertEqual(claimed[0]["workspace_id"], "ws-1")
        self.assertEqual(claimed[0]["run_id"], "run-1")
        self.assertEqual(claimed[0]["event_type"], "channel_run_delivery")

    def test_deliver_due_outbox_events_once_keeps_legacy_list_fallback_kwargs_small(self) -> None:
        listed = []

        result = outbox_service.deliver_due_outbox_events_once(
            list_undelivered_outbox_events_fn=lambda **kwargs: listed.append(dict(kwargs)) or [],
            mark_outbox_event_delivered_fn=lambda event_id, **kwargs: True,
            record_outbox_delivery_failure_fn=lambda event_id, **kwargs: True,
            deliver_event_fn=lambda event: True,
            get_outbox_delivery_status_fn=lambda: {"undelivered_count": 0, "poisoned_count": 0, "claimed_count": 0},
        )

        self.assertEqual(result["attempted"], 0)
        self.assertEqual(listed, [{"older_than_seconds": 0, "limit": 200}])

    def test_deliver_outbox_event_uses_stable_ids_for_duplicate_safety(self) -> None:
        channel_events = []
        try:
            runtime_events.configure_runtime_events(
                channel_events=channel_events,
                channel_events_lock=threading.Lock(),
                channel_events_limit=50,
                channel_sessions_limit=10,
                channel_events_file=Path("/tmp/runtime-events.json"),
                runtime_state_db="/tmp/runtime-state.sqlite3",
                list_channel_events_fn=lambda db_path, limit: [],
                replace_channel_events_fn=lambda db_path, items, limit: None,
                append_channel_event_fn=lambda db_path, item, limit: None,
                utc_now_iso=lambda: "2026-04-07T00:00:00Z",
                parse_utc_ts=lambda value: value,
                normalize_workspace_id=lambda value: str(value or "default"),
                normalize_tenant_id=lambda value: str(value or "default"),
                compact_event_text=lambda value, limit=800: str(value or "")[:limit],
                json_safe=lambda value: dict(value or {}) if isinstance(value, dict) else {},
                safe_read_json=lambda path, default: default,
            )
            event = outbox_service.OutboxEvent(
                event_id="evt-1",
                event_type="approval_resolved",
                tenant_id="tenant-1",
                workspace_id="ws-1",
                run_id="run-1",
                trace_id="trace-1",
                payload={"approval_id": "approval-1", "resolution": "approved"},
                created_at="2026-04-07T00:00:00Z",
            )

            with patch("server_modules.notification_service.deliver_notification_from_outbox_event", return_value=None):
                outbox_service.deliver_outbox_event(event, append_channel_event_item_fn=runtime_events.append_channel_event_item)
                outbox_service.deliver_outbox_event(event, append_channel_event_item_fn=runtime_events.append_channel_event_item)

            self.assertEqual(len(channel_events), 1)
            self.assertEqual(channel_events[0]["id"], "evt-1")
            self.assertEqual(channel_events[0]["tenant_id"], "tenant-1")
            self.assertEqual(channel_events[0]["action"], "approval_resolved")
        finally:
            module = sys.modules.get(runtime_events.__name__)
            if module is None:
                importlib.import_module(runtime_events.__name__)
            else:
                importlib.reload(module)


if __name__ == "__main__":
    unittest.main()
