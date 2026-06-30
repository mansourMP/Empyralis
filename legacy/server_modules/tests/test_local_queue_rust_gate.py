from unittest.mock import patch

import pytest

from server_modules import local_queue


def test_local_queue_cleanup_cancel_uses_rust_queue_transition_gate() -> None:
    with patch.object(
        local_queue.machine_lease_service,
        "enforce_queue_transition_decision",
        side_effect=RuntimeError("rust denied"),
    ) as gate:
        with pytest.raises(RuntimeError, match="rust denied"):
            local_queue._enforce_local_queue_cancel_transition(
                run_id="run-1",
                previous_status="queued_local",
            )

    assert gate.call_args.kwargs["operation"] == "cancel"
    assert gate.call_args.kwargs["run_id"] == "run-1"
    assert gate.call_args.kwargs["status"] == "queued_local"
    assert gate.call_args.kwargs["force"] is True
