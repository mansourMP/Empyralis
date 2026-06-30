from unittest.mock import patch

import pytest
from fastapi import HTTPException

from server_modules import machine_lease_service


def test_queue_claim_accepts_canonical_rust_action() -> None:
    with patch.object(
        machine_lease_service.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        return_value={
            "ok": True,
            "decision": "allow",
            "next_action": "claim_queue_item",
            "next_status": "running",
            "retryable": False,
        },
    ):
        decision = machine_lease_service.enforce_queue_transition_decision(
            operation="claim",
            run_id="run-1",
            requester_id="worker-1",
            lease_holder_id="worker-1",
            status="claimed",
        )

    assert decision["next_action"] == "claim_queue_item"


def test_queue_fail_retry_blocks_unexpected_rust_action() -> None:
    with patch.object(
        machine_lease_service.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        return_value={
            "ok": True,
            "decision": "allow",
            "next_action": "fail_queue_item",
            "next_status": "retry_scheduled",
            "retryable": True,
        },
    ):
        with pytest.raises(HTTPException) as raised:
            machine_lease_service.enforce_queue_transition_decision(
                operation="fail",
                run_id="run-1",
                requester_id="worker-1",
                lease_holder_id="worker-1",
                status="running",
                attempts=0,
                max_attempts=3,
            )

    assert raised.value.status_code == 409
    assert "unexpected next_action" in str(raised.value.detail)
