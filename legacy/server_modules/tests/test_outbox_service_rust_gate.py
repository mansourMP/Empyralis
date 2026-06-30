from unittest.mock import Mock, patch

import pytest

from server_modules import outbox_service


def test_emit_runtime_event_blocks_before_outbox_persist_when_rust_denies() -> None:
    persist = Mock()
    with patch.object(
        outbox_service.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            outbox_service.emit_runtime_event(
                event_type="run_transition",
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                run_id="run-1",
                idempotency_key="transition-1",
                payload={"to_state": "completed"},
                persist_outbox_event_fn=persist,
            )

    command, payload = kernel.call_args.args
    assert command == "outbox-delivery-decision"
    assert payload["operation"] == "persist_event"
    assert payload["event_type"] == "run_transition"
    assert payload["tenant_id"] == "tenant-1"
    assert payload["workspace_id"] == "workspace-1"
    persist.assert_not_called()


def test_deliver_due_outbox_events_blocks_before_claim_when_rust_denies() -> None:
    claim_due = Mock(return_value=[])
    with patch.object(
        outbox_service.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            outbox_service.deliver_due_outbox_events_once(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                claim_due_outbox_events_fn=claim_due,
                mark_outbox_event_delivered_fn=Mock(),
                record_outbox_delivery_failure_fn=Mock(),
                get_outbox_delivery_status_fn=Mock(return_value={}),
            )

    command, payload = kernel.call_args.args
    assert command == "outbox-delivery-decision"
    assert payload["operation"] == "claim_due"
    assert payload["tenant_id"] == "tenant-1"
    assert payload["workspace_id"] == "workspace-1"
    claim_due.assert_not_called()
