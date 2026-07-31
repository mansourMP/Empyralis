"""Regression tests for the RustKernelDecisionError -> allow_block_result path.

Bug: `_enforce_local_worker_decision` (server_modules/worker_dispatch_service.py)
read `exc.result` off a caught `RustKernelDecisionError`, but the exception
class (server_modules/rust_runtime_kernel_client.py) only ever sets
`.decision`, `.command`, and `.reason` -- there is no `.result` attribute.
Every kernel "block" decision on the four call sites that pass
`allow_block_result=True` (heartbeat_local_run, complete_local_run,
pause_local_run, fail_local_run) crashed with an AttributeError instead of
surfacing the intended, precise HTTPException (403 for
"local_run_not_owned_by_worker", 409 for "local_run_claim_missing", 423 for
anything else -- see `_raise_local_worker_gate_block`).

These tests patch `run_runtime_kernel_enforced` directly (the same pattern
used by test_worker_dispatch_local_worker_rust_gate.py) so they exercise the
exact except-clause in `_enforce_local_worker_decision`, independent of the
autouse Rust-kernel mock in conftest.py or whether the compiled kernel
binary is present.
"""

from __future__ import annotations

import queue
import threading
import unittest
from unittest import mock

from fastapi import HTTPException

from server_modules import rust_runtime_kernel_client, worker_dispatch_service


def _block_error(
    reason: str, *, command: str = "local-worker-decision"
) -> rust_runtime_kernel_client.RustKernelDecisionError:
    return rust_runtime_kernel_client.RustKernelDecisionError(
        {"ok": False, "decision": "block", "reason": reason, "operation": command},
        command=command,
    )


class RustKernelDecisionErrorAttributesTests(unittest.TestCase):
    """Pin down the exception's real attribute surface, since the whole bug
    was code assuming an attribute (`.result`) that was never set."""

    def test_exception_exposes_decision_command_reason(self) -> None:
        exc = _block_error("local_run_not_owned_by_worker")

        self.assertEqual(exc.reason, "local_run_not_owned_by_worker")
        self.assertEqual(exc.command, "local-worker-decision")
        self.assertIsInstance(exc.decision, dict)
        self.assertEqual(exc.decision["decision"], "block")
        self.assertEqual(exc.decision["reason"], "local_run_not_owned_by_worker")

    def test_exception_has_no_result_attribute(self) -> None:
        exc = _block_error("local_run_claim_missing")

        self.assertFalse(hasattr(exc, "result"))
        with self.assertRaises(AttributeError):
            exc.result  # noqa: B018 - intentional attribute probe, mirrors the bug

    def test_decision_attribute_is_always_a_dict_copy(self) -> None:
        source = {"ok": False, "decision": "block", "reason": "x"}
        exc = rust_runtime_kernel_client.RustKernelDecisionError(source, command="c")

        self.assertIsNot(exc.decision, source)
        self.assertEqual(exc.decision, source)


def _run(status: str = "claimed") -> dict:
    return {
        "status": status,
        "context": {"workspace_id": "ws-1", "tenant_id": "tenant-1", "metadata": {}},
        "logs": queue.Queue(),
    }


class BlockedKernelDecisionGracefulResponseTests(unittest.TestCase):
    """Each `allow_block_result=True` call site must translate a kernel
    'block' decision into the caller's intended HTTPException -- never an
    AttributeError."""

    def _patched(self, reason: str):
        return mock.patch.object(
            worker_dispatch_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=_block_error(reason),
        )

    # -- heartbeat_local_run (operation="run_heartbeat") --------------------

    def test_heartbeat_local_run_not_owned_denial_maps_to_403(self) -> None:
        run = _run()
        with self._patched("local_run_not_owned_by_worker"):
            with self.assertRaises(HTTPException) as raised:
                worker_dispatch_service.heartbeat_local_run(
                    "run-1",
                    worker_id="worker-2",
                    note=None,
                    runs_by_id={"run-1": run},
                    local_queue_lock=threading.Lock(),
                    claimed_runs={"run-1": {"worker_id": "worker-1"}},
                    maybe_emit_local_still_working_fn=lambda *a, **k: False,
                    mark_local_worker_seen_fn=lambda *a, **k: None,
                    utc_now_iso_fn=lambda: "2026-07-31T00:00:00Z",
                )

        self.assertEqual(raised.exception.status_code, 403)

    def test_heartbeat_local_run_claim_missing_denial_maps_to_409(self) -> None:
        run = _run()
        with self._patched("local_run_claim_missing"):
            with self.assertRaises(HTTPException) as raised:
                worker_dispatch_service.heartbeat_local_run(
                    "run-1",
                    worker_id="worker-1",
                    note=None,
                    runs_by_id={"run-1": run},
                    local_queue_lock=threading.Lock(),
                    claimed_runs={},
                    maybe_emit_local_still_working_fn=lambda *a, **k: False,
                    mark_local_worker_seen_fn=lambda *a, **k: None,
                    utc_now_iso_fn=lambda: "2026-07-31T00:00:00Z",
                )

        self.assertEqual(raised.exception.status_code, 409)

    # -- complete_local_run (operation="complete_run") -----------------------

    def test_complete_local_run_not_owned_denial_maps_to_403(self) -> None:
        run = _run()
        with self._patched("local_run_not_owned_by_worker"):
            with self.assertRaises(HTTPException) as raised:
                worker_dispatch_service.complete_local_run(
                    "run-1",
                    worker_id="worker-2",
                    result_text="done",
                    result_data=None,
                    usage_masked=None,
                    runs_by_id={"run-1": run},
                    local_queue_lock=threading.Lock(),
                    claimed_runs={"run-1": {"worker_id": "worker-1"}},
                    emit_log_fn=lambda *a, **k: None,
                    mark_local_worker_seen_fn=lambda *a, **k: None,
                    set_run_status_fn=lambda *a, **k: None,
                    persist_run_memory_fn=lambda *a, **k: None,
                    persist_local_runtime_state_fn=lambda: None,
                )

        self.assertEqual(raised.exception.status_code, 403)

    def test_complete_local_run_claim_missing_denial_maps_to_409(self) -> None:
        run = _run()
        with self._patched("local_run_claim_missing"):
            with self.assertRaises(HTTPException) as raised:
                worker_dispatch_service.complete_local_run(
                    "run-1",
                    worker_id=None,
                    result_text="done",
                    result_data=None,
                    usage_masked=None,
                    runs_by_id={"run-1": run},
                    local_queue_lock=threading.Lock(),
                    claimed_runs={},
                    emit_log_fn=lambda *a, **k: None,
                    mark_local_worker_seen_fn=lambda *a, **k: None,
                    set_run_status_fn=lambda *a, **k: None,
                    persist_run_memory_fn=lambda *a, **k: None,
                    persist_local_runtime_state_fn=lambda: None,
                )

        self.assertEqual(raised.exception.status_code, 409)

    # -- pause_local_run (operation="pause_run") -----------------------------

    def test_pause_local_run_not_owned_denial_maps_to_403(self) -> None:
        run = _run()
        with self._patched("local_run_not_owned_by_worker"):
            with self.assertRaises(HTTPException) as raised:
                worker_dispatch_service.pause_local_run(
                    "run-1",
                    worker_id="worker-2",
                    result_text="waiting",
                    result_data=None,
                    browser_checkpoint=None,
                    wait_reason=None,
                    runs_by_id={"run-1": run},
                    local_queue_lock=threading.Lock(),
                    claimed_runs={"run-1": {"worker_id": "worker-1"}},
                    emit_log_fn=lambda *a, **k: None,
                    mark_local_worker_seen_fn=lambda *a, **k: None,
                    set_run_status_fn=lambda *a, **k: None,
                    persist_local_runtime_state_fn=lambda: None,
                )

        self.assertEqual(raised.exception.status_code, 403)

    def test_pause_local_run_claim_missing_denial_maps_to_409(self) -> None:
        run = _run()
        with self._patched("local_run_claim_missing"):
            with self.assertRaises(HTTPException) as raised:
                worker_dispatch_service.pause_local_run(
                    "run-1",
                    worker_id=None,
                    result_text="waiting",
                    result_data=None,
                    browser_checkpoint=None,
                    wait_reason=None,
                    runs_by_id={"run-1": run},
                    local_queue_lock=threading.Lock(),
                    claimed_runs={},
                    emit_log_fn=lambda *a, **k: None,
                    mark_local_worker_seen_fn=lambda *a, **k: None,
                    set_run_status_fn=lambda *a, **k: None,
                    persist_local_runtime_state_fn=lambda: None,
                )

        self.assertEqual(raised.exception.status_code, 409)

    # -- fail_local_run (operation="fail_run") -------------------------------

    def test_fail_local_run_not_owned_denial_maps_to_403(self) -> None:
        run = _run()
        with self._patched("local_run_not_owned_by_worker"):
            with self.assertRaises(HTTPException) as raised:
                worker_dispatch_service.fail_local_run(
                    "run-1",
                    worker_id="worker-2",
                    error="boom",
                    runs_by_id={"run-1": run},
                    local_queue_lock=threading.Lock(),
                    claimed_runs={"run-1": {"worker_id": "worker-1"}},
                    emit_log_fn=lambda *a, **k: None,
                    mark_local_worker_seen_fn=lambda *a, **k: None,
                    set_run_status_fn=lambda *a, **k: None,
                    persist_local_runtime_state_fn=lambda: None,
                )

        self.assertEqual(raised.exception.status_code, 403)

    def test_fail_local_run_claim_missing_denial_maps_to_409(self) -> None:
        run = _run()
        with self._patched("local_run_claim_missing"):
            with self.assertRaises(HTTPException) as raised:
                worker_dispatch_service.fail_local_run(
                    "run-1",
                    worker_id=None,
                    error="boom",
                    runs_by_id={"run-1": run},
                    local_queue_lock=threading.Lock(),
                    claimed_runs={},
                    emit_log_fn=lambda *a, **k: None,
                    mark_local_worker_seen_fn=lambda *a, **k: None,
                    set_run_status_fn=lambda *a, **k: None,
                    persist_local_runtime_state_fn=lambda: None,
                )

        self.assertEqual(raised.exception.status_code, 409)

    # -- generic reason: still graceful, falls back to 423 -------------------

    def test_generic_block_reason_falls_back_to_423_without_crashing(self) -> None:
        run = _run()
        with self._patched("local_worker_kill_switch_active"):
            with self.assertRaises(HTTPException) as raised:
                worker_dispatch_service.fail_local_run(
                    "run-1",
                    worker_id="worker-1",
                    error="boom",
                    runs_by_id={"run-1": run},
                    local_queue_lock=threading.Lock(),
                    claimed_runs={"run-1": {"worker_id": "worker-1"}},
                    emit_log_fn=lambda *a, **k: None,
                    mark_local_worker_seen_fn=lambda *a, **k: None,
                    set_run_status_fn=lambda *a, **k: None,
                    persist_local_runtime_state_fn=lambda: None,
                )

        self.assertEqual(raised.exception.status_code, 423)
        self.assertEqual(
            raised.exception.detail["reason"], "local_worker_kill_switch_active"
        )


if __name__ == "__main__":
    unittest.main()
