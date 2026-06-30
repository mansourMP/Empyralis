import queue
import unittest

from fastapi import HTTPException

from server_modules import runtime_local_execution_approval_service


class RuntimeLocalExecutionApprovalServiceTests(unittest.TestCase):
    def test_build_local_execution_approval_callbacks_preserves_entries(self):
        callbacks = runtime_local_execution_approval_service.build_local_execution_approval_callbacks(
            get_pending_confirmation=lambda run: None,
            approval_correlation_id=lambda approval_id, run_id=None: "corr-1",
            parse_utc_ts=lambda value: None,
            utc_now=lambda: None,
            utc_now_iso=lambda: "2026-04-05T00:00:00Z",
            set_pending_confirmation=lambda run, pending: None,
            emit_log=lambda *args, **kwargs: None,
            append_approval_audit=lambda **kwargs: None,
            browser_plan_hash_from_inputs=lambda inputs: "",
            clear_pending_confirmation=lambda run: None,
            set_run_status=lambda run_id, status: None,
            mark_local_execution_tools_approved=lambda metadata: None,
            build_browser_execution_binding=lambda root_dir, plan_hash, profile: {},
            root_dir="/tmp/root",
            enqueue_local_companion_run=lambda run_id, **kwargs: None,
        )

        self.assertIn("approval_correlation_id", callbacks)
        self.assertIn("enqueue_local_companion_run", callbacks)

    def test_approved_local_execution_queues_local_companion_run(self):
        run = {
            "logs": queue.Queue(),
            "context": {"metadata": {"local_execution_waiting_confirmation": True}},
        }
        pending_updates = []
        enqueued = []

        payload = runtime_local_execution_approval_service.resolve_local_execution_start_approval(
            "run-1",
            run,
            "approval-1",
            "approve",
            get_pending_confirmation=lambda run: {"approval_id": "approval-1", "correlation_id": "corr-1"},
            approval_correlation_id=lambda approval_id, run_id=None: "corr-1",
            parse_utc_ts=lambda value: None,
            utc_now=lambda: None,
            utc_now_iso=lambda: "2026-04-05T00:00:00Z",
            set_pending_confirmation=lambda run, pending: pending_updates.append(dict(pending)),
            emit_log=lambda *args, **kwargs: None,
            append_approval_audit=lambda **kwargs: None,
            browser_plan_hash_from_inputs=lambda inputs: "",
            clear_pending_confirmation=lambda run: run.setdefault("cleared", True),
            set_run_status=lambda run_id, status: run.setdefault("status", status),
            mark_local_execution_tools_approved=lambda metadata: metadata.setdefault("approved", True),
            build_browser_execution_binding=lambda root_dir, plan_hash, profile: {"binding": True},
            root_dir="/tmp/root",
            enqueue_local_companion_run=lambda run_id, **kwargs: enqueued.append((run_id, kwargs)),
        )

        self.assertEqual(payload["decision_kind"], "approved")
        self.assertEqual(enqueued[0][0], "run-1")
        self.assertEqual(pending_updates[0]["status"], "resolved")

    def test_rejected_local_execution_fails_run(self):
        run = {
            "logs": queue.Queue(),
            "context": {"metadata": {"local_execution_waiting_confirmation": True}},
        }
        statuses = []

        payload = runtime_local_execution_approval_service.resolve_local_execution_start_approval(
            "run-1",
            run,
            "approval-1",
            "reject",
            get_pending_confirmation=lambda run: {"approval_id": "approval-1", "correlation_id": "corr-1"},
            approval_correlation_id=lambda approval_id, run_id=None: "corr-1",
            parse_utc_ts=lambda value: None,
            utc_now=lambda: None,
            utc_now_iso=lambda: "2026-04-05T00:00:00Z",
            set_pending_confirmation=lambda run, pending: None,
            emit_log=lambda *args, **kwargs: None,
            append_approval_audit=lambda **kwargs: None,
            browser_plan_hash_from_inputs=lambda inputs: "",
            clear_pending_confirmation=lambda run: None,
            set_run_status=lambda run_id, status: statuses.append(status),
            mark_local_execution_tools_approved=lambda metadata: None,
            build_browser_execution_binding=lambda root_dir, plan_hash, profile: {},
            root_dir="/tmp/root",
            enqueue_local_companion_run=lambda run_id, **kwargs: None,
        )

        self.assertEqual(payload["decision_kind"], "rejected")
        self.assertEqual(statuses, ["failed"])

    def test_mismatched_browser_plan_hash_raises_conflict(self):
        run = {
            "logs": queue.Queue(),
            "context": {
                "metadata": {
                    "local_execution_waiting_confirmation": True,
                    "browser_immutable_plan_hash": "expected",
                    "pack_inputs": {"x": 1},
                }
            },
        }

        with self.assertRaises(HTTPException):
            runtime_local_execution_approval_service.resolve_local_execution_start_approval(
                "run-1",
                run,
                "approval-1",
                "approve",
                get_pending_confirmation=lambda run: {"approval_id": "approval-1", "correlation_id": "corr-1"},
                approval_correlation_id=lambda approval_id, run_id=None: "corr-1",
                parse_utc_ts=lambda value: None,
                utc_now=lambda: None,
                utc_now_iso=lambda: "2026-04-05T00:00:00Z",
                set_pending_confirmation=lambda run, pending: None,
                emit_log=lambda *args, **kwargs: None,
                append_approval_audit=lambda **kwargs: None,
                browser_plan_hash_from_inputs=lambda inputs: "current",
                clear_pending_confirmation=lambda run: None,
                set_run_status=lambda run_id, status: None,
                mark_local_execution_tools_approved=lambda metadata: None,
                build_browser_execution_binding=lambda root_dir, plan_hash, profile: {},
                root_dir="/tmp/root",
                enqueue_local_companion_run=lambda run_id, **kwargs: None,
            )

    def test_build_resolve_local_execution_start_approval_fn_resolves_with_late_root_dir(self):
        run = {
            "logs": queue.Queue(),
            "context": {"metadata": {"local_execution_waiting_confirmation": True}},
        }
        enqueued = []

        resolver = runtime_local_execution_approval_service.build_resolve_local_execution_start_approval_fn(
            get_pending_confirmation=lambda run: {"approval_id": "approval-1", "correlation_id": "corr-1"},
            approval_correlation_id=lambda approval_id, run_id=None: "corr-1",
            parse_utc_ts=lambda value: None,
            utc_now=lambda: None,
            utc_now_iso=lambda: "2026-04-05T00:00:00Z",
            set_pending_confirmation=lambda run, pending: None,
            emit_log=lambda *args, **kwargs: None,
            append_approval_audit=lambda **kwargs: None,
            browser_plan_hash_from_inputs=lambda inputs: "",
            clear_pending_confirmation=lambda run: None,
            set_run_status=lambda run_id, status: None,
            mark_local_execution_tools_approved=lambda metadata: None,
            build_browser_execution_binding=lambda root_dir, plan_hash, profile: {"root_dir": root_dir},
            root_dir=lambda: "/tmp/root",
            enqueue_local_companion_run=lambda run_id, **kwargs: enqueued.append((run_id, kwargs)),
        )

        payload = resolver("run-1", run, "approval-1", "approve")

        self.assertEqual(payload["decision_kind"], "approved")
        self.assertEqual(enqueued[0][0], "run-1")


if __name__ == "__main__":
    unittest.main()
