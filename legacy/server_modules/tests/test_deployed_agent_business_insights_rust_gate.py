from unittest.mock import patch

import pytest

from server_modules import control_plane_repository


@pytest.mark.asyncio
async def test_deployed_agent_business_insight_candidate_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await control_plane_repository.upsert_deployed_agent_business_insight_candidate(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                deployed_agent_id="dagent-1",
                pattern_key="pricing.discount",
                insight_type="pricing_intelligence",
                title="Discount pressure",
                summary="Customers ask for discounts.",
                recommendation="Review policy.",
                channel_key="telegram",
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "deployed_agent_record_write"
    assert payload["record_type"] == "deployed_agent_business_insight"
    assert payload["tenant_id"] == "tenant-1"
    assert payload["workspace_id"] == "workspace-1"
    assert payload["actor_id"] == "dagent-1"
    assert payload["agent_id"] == "dagent-1"
    assert payload["idempotency_key"] == "dagent-1:pricing.discount:telegram"


@pytest.mark.asyncio
async def test_deployed_agent_business_insight_review_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await control_plane_repository.update_deployed_agent_business_insight_status(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                deployed_agent_id="dagent-1",
                insight_id="insight-1",
                status="approved",
                actor_user_id="owner-1",
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "deployed_agent_record_write"
    assert payload["record_type"] == "deployed_agent_business_insight"
    assert payload["actor_id"] == "owner-1"
    assert payload["actor_role"] == "admin"
    assert payload["agent_id"] == "dagent-1"
    assert payload["idempotency_key"] == "dagent-1:insight-1:approved"
