import pytest
import threading
from fastapi import HTTPException

from server_modules import machine_lease_service
from server_modules.rust_runtime_kernel_client import RustKernelDecisionError


def test_machine_lease_transition_calls_rust_kernel(monkeypatch):
    calls = []

    def allow(command, payload):
        calls.append((command, payload))
        return {
            "ok": True,
            "decision": "allow",
            "operation": payload["operation"],
            "next_action": "persist_machine_lease_transition",
        }

    monkeypatch.setattr(
        machine_lease_service.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        allow,
    )

    decision = machine_lease_service._enforce_machine_lease_decision(
        operation="acquire",
        holder_id="worker-1",
        active_lease_count=0,
        max_concurrent_leases=1,
        lease_ttl_seconds=120,
    )

    assert decision["decision"] == "allow"
    assert calls[0][0] == "machine-lease-decision"
    assert calls[0][1]["operation"] == "acquire"
    assert calls[0][1]["holder_id"] == "worker-1"
    assert calls[0][1]["requested_holder_id"] == "worker-1"
    assert calls[0][1]["active_lease_count"] == 0
    assert calls[0][1]["max_concurrent_leases"] == 1
    assert calls[0][1]["lease_ttl_seconds"] == 120


def test_machine_lease_transition_blocks_fail_closed(monkeypatch):
    def block(_command, _payload):
        denied = RustKernelDecisionError.__new__(RustKernelDecisionError)
        BaseException.__init__(denied, "lease_holder_mismatch")
        denied.command = "machine-lease-decision"
        denied.result = {
            "ok": False,
            "decision": "block",
            "reason": "lease_holder_mismatch",
        }
        denied.reason = "lease_holder_mismatch"
        raise denied

    monkeypatch.setattr(
        machine_lease_service.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        block,
    )

    with pytest.raises(HTTPException) as excinfo:
        machine_lease_service._enforce_machine_lease_decision(
            operation="release",
            holder_id="worker-2",
            current_holder_id="worker-1",
            lease_id="lease-1",
            lease_ttl_seconds=120,
        )

    assert excinfo.value.status_code == 409
    assert "lease_holder_mismatch" in str(excinfo.value.detail)


def test_machine_lease_transition_wrong_rust_action_blocks(monkeypatch):
    def allow_wrong(_command, payload):
        return {
            "ok": True,
            "decision": "allow",
            "operation": payload["operation"],
            "next_action": "release_machine_lease_transition",
        }

    monkeypatch.setattr(
        machine_lease_service.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        allow_wrong,
    )

    with pytest.raises(HTTPException) as excinfo:
        machine_lease_service._enforce_machine_lease_decision(
            operation="acquire",
            holder_id="worker-1",
            active_lease_count=0,
            max_concurrent_leases=1,
            lease_ttl_seconds=120,
        )

    assert excinfo.value.status_code == 409
    assert "unexpected_next_action:release_machine_lease_transition" in str(excinfo.value.detail)


def test_release_machine_lease_claim_uses_rust_for_lease_mismatch(monkeypatch):
    def fake_kernel(command, payload):
        if command == "queue-transition-decision":
            return {
                "ok": True,
                "decision": "allow",
                "operation": payload["operation"],
                "next_action": "release_claim",
            }
        if command == "machine-lease-decision":
            denied = RustKernelDecisionError.__new__(RustKernelDecisionError)
            BaseException.__init__(denied, "lease_release_id_mismatch")
            denied.command = "machine-lease-decision"
            denied.result = {
                "ok": False,
                "decision": "block",
                "reason": "lease_release_id_mismatch",
                "decision_id": "rkd_lease_mismatch",
            }
            denied.reason = "lease_release_id_mismatch"
            raise denied
        raise AssertionError(f"unexpected command {command}")

    monkeypatch.setattr(
        machine_lease_service.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        fake_kernel,
    )

    released = machine_lease_service.release_machine_lease_claim(
        "run-1",
        worker_id="worker-1",
        lease_id="lease-2",
        local_queue_lock=threading.Lock(),
        claimed_runs={
            "run-1": {
                "worker_id": "worker-1",
                "machine_id": "worker-1",
                "lease_id": "lease-1",
            }
        },
    )

    assert released["released"] is False
    assert released["lease_mismatch"] is True
    assert released["decision_id"] == "rkd_lease_mismatch"
