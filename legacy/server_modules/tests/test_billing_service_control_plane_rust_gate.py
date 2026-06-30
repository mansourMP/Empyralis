from unittest.mock import Mock, patch

import pytest
from fastapi import HTTPException

from server_modules import billing_service
from server_modules.rust_runtime_kernel_client import RustKernelDecisionError


def _billing_summary(*, customer_id: str | None = None) -> dict[str, object]:
    return {
        "tenant_id": "tenant-1",
        "workspace_id": "workspace-1",
        "account": {
            "provider_customer_id": customer_id,
            "default_currency": "usd",
        },
        "subscription": {
            "plan_id": "pro",
            "status": "active",
            "provider_subscription_id": "sub-1",
            "currency": "usd",
            "metadata": {},
        },
    }


@pytest.mark.asyncio
async def test_workspace_checkout_rust_denial_blocks_stripe_checkout():
    denied = RustKernelDecisionError(
        {
            "ok": False,
            "decision": "block",
            "reason": "billing_entitlement_missing",
            "operation": "workspace_billing_plan_update",
        },
        command="control-plane-service-decision",
    )
    with patch.object(
        billing_service,
        "_stripe_price_map",
        return_value={"pro": "price_1"},
    ), patch.object(
        billing_service,
        "workspace_billing_summary_for_workspace_id",
        return_value=_billing_summary(),
    ), patch.object(
        billing_service.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=denied,
    ) as rust_gate, patch.object(
        billing_service,
        "_stripe_api_request",
        new=Mock(),
    ) as stripe_request:
        with pytest.raises(HTTPException) as raised:
            billing_service.create_workspace_checkout_session(
                workspace_id="workspace-1",
                plan_id="pro",
            )

    assert raised.value.status_code == 423
    command, payload = rust_gate.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "workspace_billing_plan_update"
    assert payload["workspace_id"] == "workspace-1"
    assert payload["tenant_id"] == "tenant-1"
    assert payload["target_status"] == "pro"
    stripe_request.assert_not_called()


@pytest.mark.asyncio
async def test_workspace_checkout_unexpected_rust_next_action_blocks_stripe_checkout():
    with patch.object(
        billing_service,
        "_stripe_price_map",
        return_value={"pro": "price_1"},
    ), patch.object(
        billing_service,
        "workspace_billing_summary_for_workspace_id",
        return_value=_billing_summary(),
    ), patch.object(
        billing_service.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        return_value={
            "ok": True,
            "decision": "allow",
            "reason": "control_plane_service_operation_allowed",
            "operation": "workspace_billing_plan_update",
            "next_action": "allow_control_plane_read",
            "mutation_plan": {
                "apply": True,
                "next_action": "allow_control_plane_read",
            },
        },
    ), patch.object(
        billing_service,
        "_stripe_api_request",
        new=Mock(),
    ) as stripe_request:
        with pytest.raises(HTTPException) as raised:
            billing_service.create_workspace_checkout_session(
                workspace_id="workspace-1",
                plan_id="pro",
            )

    assert raised.value.status_code == 423
    assert "unexpected next_action" in str(raised.value.detail["reason"])
    stripe_request.assert_not_called()


@pytest.mark.asyncio
async def test_workspace_billing_portal_rust_denial_blocks_stripe_portal():
    denied = RustKernelDecisionError(
        {
            "ok": False,
            "decision": "block",
            "reason": "owner_access_required",
            "operation": "billing_update",
        },
        command="control-plane-service-decision",
    )
    with patch.object(
        billing_service,
        "workspace_billing_summary_for_workspace_id",
        return_value=_billing_summary(customer_id="cus_1"),
    ), patch.object(
        billing_service.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=denied,
    ) as rust_gate, patch.object(
        billing_service,
        "_stripe_api_request",
        new=Mock(),
    ) as stripe_request:
        with pytest.raises(HTTPException) as raised:
            billing_service.create_workspace_portal_session(workspace_id="workspace-1")

    assert raised.value.status_code == 423
    command, payload = rust_gate.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "billing_update"
    assert payload["record_type"] == "workspace_billing_portal"
    assert payload["workspace_id"] == "workspace-1"
    stripe_request.assert_not_called()


@pytest.mark.asyncio
async def test_credit_purchase_checkout_rust_denial_blocks_stripe_checkout():
    denied = RustKernelDecisionError(
        {
            "ok": False,
            "decision": "block",
            "reason": "control_plane_kill_switch_enabled",
            "operation": "billing_update",
        },
        command="control-plane-service-decision",
    )
    with patch.object(
        billing_service,
        "workspace_billing_summary_for_workspace_id",
        return_value=_billing_summary(customer_id="cus_1"),
    ), patch.object(
        billing_service.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=denied,
    ) as rust_gate, patch.object(
        billing_service,
        "_stripe_api_request",
        new=Mock(),
    ) as stripe_request:
        with pytest.raises(HTTPException) as raised:
            billing_service.create_credit_purchase_checkout_session(
                workspace_id="workspace-1",
                amount_usd=25.0,
            )

    assert raised.value.status_code == 423
    command, payload = rust_gate.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "billing_update"
    assert payload["record_type"] == "workspace_billing_credit_checkout"
    assert payload["target_status"] == "credit_purchase"
    stripe_request.assert_not_called()
