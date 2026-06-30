import threading
import unittest
from unittest import mock

from server_modules import run_service


class _FakeTimer:
    created = []

    def __init__(self, delay, callback):
        self.delay = delay
        self.callback = callback
        self.daemon = False
        self.started = False
        _FakeTimer.created.append(self)

    def start(self):
        self.started = True


def _schedule_kwargs():
    return {
        "runs_by_id": {
            "parent-1": {
                "run_id": "parent-1",
                "context": {"metadata": {}},
                "logs": None,
            }
        },
        "parse_utc_ts": lambda value: None,
        "child_lineage_key": lambda child: "lineage-1",
        "failure_status": lambda status: str(status or "").lower() == "failed",
        "child_retry_count": lambda child: int(child.get("retry_count") or 0),
        "safe_int": lambda value, default: int(value) if value not in (None, "") else default,
        "auto_retry_pending": set(),
        "auto_retry_attempts": {},
        "auto_retry_pending_lock": threading.Lock(),
        "auto_retry_max_retries": 3,
        "auto_retry_delay_seconds": 5.0,
        "emit_log": lambda *args, **kwargs: None,
        "build_retry_child_payload_fn": lambda *args, **kwargs: {},
        "build_delegated_child_run_request_fn": lambda *args, **kwargs: {},
        "execute_delegated_run_request_fn": lambda request: {},
        "lookup_run_snapshot_fn": lambda run_id: {"run_id": run_id},
        "find_run_relationships_fn": lambda run_id, snapshot: (snapshot, []),
        "refresh_parent_delegation_state_fn": lambda *args, **kwargs: None,
        "timer_factory": _FakeTimer,
    }


class RunServiceSchedulerRustGateTests(unittest.TestCase):
    def setUp(self):
        _FakeTimer.created = []

    def test_auto_retry_uses_rust_scheduler_delay_before_timer_start(self):
        kwargs = _schedule_kwargs()
        with mock.patch.object(
            run_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={"ok": True, "decision": "allow", "next_action": "schedule_retry", "delay_seconds": 42},
        ) as rust_mock:
            scheduled = run_service.schedule_auto_retry_for_failed_children(
                {"run_id": "parent-1"},
                [{"run_id": "child-1", "status": "failed", "retry_count": 0}],
                **kwargs,
            )

        self.assertEqual(scheduled, {"lineage-1"})
        rust_mock.assert_called_once()
        self.assertEqual(rust_mock.call_args.args[0], "scheduler-decision")
        self.assertEqual(rust_mock.call_args.args[1]["operation"], "retry")
        self.assertEqual(rust_mock.call_args.args[1]["retry_count"], 0)
        self.assertEqual(_FakeTimer.created[0].delay, 42)
        self.assertTrue(_FakeTimer.created[0].started)

    def test_auto_retry_rust_denial_blocks_pending_state_and_timer(self):
        kwargs = _schedule_kwargs()
        denied = run_service.rust_runtime_kernel_client.RustKernelDecisionError(
            {
                "ok": False,
                "decision": "block",
                "reason": "retry_attempts_exhausted",
            },
            command="scheduler-decision",
        )
        with mock.patch.object(
            run_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=denied,
        ) as rust_mock:
            scheduled = run_service.schedule_auto_retry_for_failed_children(
                {"run_id": "parent-1"},
                [{"run_id": "child-1", "status": "failed", "retry_count": 3}],
                **kwargs,
            )

        self.assertEqual(scheduled, set())
        rust_mock.assert_called_once()
        self.assertEqual(_FakeTimer.created, [])
        self.assertEqual(kwargs["auto_retry_pending"], set())
        self.assertEqual(kwargs["auto_retry_attempts"], {})

    def test_auto_retry_wrong_rust_action_blocks_pending_state_and_timer(self):
        kwargs = _schedule_kwargs()
        with mock.patch.object(
            run_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={"ok": True, "decision": "allow", "next_action": "defer_session_scheduler_operation", "delay_seconds": 42},
        ) as rust_mock:
            scheduled = run_service.schedule_auto_retry_for_failed_children(
                {"run_id": "parent-1"},
                [{"run_id": "child-1", "status": "failed", "retry_count": 0}],
                **kwargs,
            )

        self.assertEqual(scheduled, set())
        rust_mock.assert_called_once()
        self.assertEqual(_FakeTimer.created, [])
        self.assertEqual(kwargs["auto_retry_pending"], set())
        self.assertEqual(kwargs["auto_retry_attempts"], {})


if __name__ == "__main__":
    unittest.main()
