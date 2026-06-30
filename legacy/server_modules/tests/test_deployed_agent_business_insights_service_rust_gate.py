from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from server_modules import deployed_agent_business_insights_service as service


@pytest.mark.anyio
async def test_review_owner_business_insight_wrong_rust_action_blocks_before_repository_write() -> None:
    scope = {
        "tenant_id": "tenant-1",
        "workspace_id": "ws-1",
        "deployed_agent": {"id": "dagent-1", "deployment_state": "live", "metadata": {}},
    }

    with (
        patch.object(service, "_resolve_owner_deployed_agent_scope", new=AsyncMock(return_value=scope)),
        patch.object(
            service.deployed_agent_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={"next_action": "apply_deployed_agent_business_insight"},
        ),
        patch.object(
            service.control_plane_repository,
            "update_deployed_agent_business_insight_status",
            new=AsyncMock(),
        ) as update_mock,
    ):
        with pytest.raises(HTTPException) as exc:
            await service.review_owner_business_insight(
                current_user={"user_id": "user-1"},
                workspace_id="ws-1",
                deployed_agent_id="dagent-1",
                insight_id="insight-1",
                status="approved",
            )

    assert exc.value.status_code == 409
    assert "unexpected next_action" in str(exc.value.detail)
    update_mock.assert_not_awaited()


@pytest.mark.anyio
async def test_apply_owner_business_insight_wrong_rust_action_blocks_before_insight_read() -> None:
    scope = {
        "tenant_id": "tenant-1",
        "workspace_id": "ws-1",
        "deployed_agent": {"id": "dagent-1", "deployment_state": "live", "metadata": {}},
    }

    with (
        patch.object(service, "_resolve_owner_deployed_agent_scope", new=AsyncMock(return_value=scope)),
        patch.object(
            service.deployed_agent_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={"next_action": "review_deployed_agent_business_insight"},
        ),
        patch.object(
            service.control_plane_repository,
            "get_deployed_agent_business_insight",
            new=AsyncMock(),
        ) as get_mock,
    ):
        with pytest.raises(HTTPException) as exc:
            await service.apply_owner_business_insight(
                current_user={"user_id": "user-1"},
                workspace_id="ws-1",
                deployed_agent_id="dagent-1",
                insight_id="insight-1",
            )

    assert exc.value.status_code == 409
    assert "unexpected next_action" in str(exc.value.detail)
    get_mock.assert_not_awaited()


def test_business_insight_service_gate_accepts_canonical_apply_action() -> None:
    captured = {}

    def _fake_rust(command: str, payload: dict[str, object]):
        captured["command"] = command
        captured["payload"] = dict(payload)
        return {"next_action": "apply_deployed_agent_business_insight"}

    with patch.object(
        service.deployed_agent_service.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=_fake_rust,
    ):
        result = service.deployed_agent_service._enforce_deployed_agent_service_decision(
            "business_insight_apply",
            deployed_agent={"id": "dagent-1", "deployment_state": "live", "metadata": {}},
            tenant_id="tenant-1",
            workspace_id="ws-1",
            current_user={"user_id": "user-1"},
            insight_id="insight-1",
        )

    assert result["next_action"] == "apply_deployed_agent_business_insight"
    assert captured["command"] == "deployed-agent-service-decision"
    assert captured["payload"]["operation"] == "business_insight_apply"
    assert captured["payload"]["insight_id"] == "insight-1"
