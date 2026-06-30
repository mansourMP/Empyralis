from __future__ import annotations

from unittest import mock

import pytest

from server_modules.deployed_agent_marketplace_service import DeployedAgentMarketplaceService


@pytest.mark.asyncio
async def test_marketplace_upgrade_click_blocks_before_repository_write():
    service = DeployedAgentMarketplaceService()
    acquisition_service = mock.Mock()
    acquisition_service.decode_attribution_token_payload.return_value = {
        "deployed_agent_id": "dagent-1",
        "channel_key": "telegram",
        "external_user_id": "external-user-1",
        "source": "telegram_limit_hit",
    }
    acquisition_service.resolve_or_create_touch_from_attribution_token.return_value = {
        "id": "touch-1",
        "tenant_id": "tenant-1",
        "workspace_id": "workspace-1",
        "deployed_agent_id": "dagent-1",
        "channel_key": "telegram",
        "external_user_id": "external-user-1",
    }

    with mock.patch(
        "server_modules.deployed_agent_marketplace_service.channel_user_acquisition_service.get_channel_user_acquisition_service",
        return_value=acquisition_service,
    ), mock.patch(
        "server_modules.deployed_agent_marketplace_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel, mock.patch(
        "server_modules.deployed_agent_marketplace_service.control_plane_repository.record_deployed_agent_upgrade_click",
    ) as record_click:
        with pytest.raises(RuntimeError, match="rust denied"):
            await service.record_upgrade_click(
                channel_attribution="token-1",
                source="telegram_limit_hit",
                agent_id="dagent-1",
            )

    record_click.assert_not_called()
    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "deployed_agent_upgrade_click_write"
    assert payload["record_type"] == "deployed_agent_upgrade_click"
    assert payload["tenant_id"] == "tenant-1"
    assert payload["workspace_id"] == "workspace-1"
    assert payload["agent_id"] == "dagent-1"
    assert payload["actor_id"] == "touch-1"


@pytest.mark.asyncio
async def test_marketplace_upgrade_click_unexpected_next_action_blocks_before_repository_write():
    service = DeployedAgentMarketplaceService()
    acquisition_service = mock.Mock()
    acquisition_service.decode_attribution_token_payload.return_value = {
        "deployed_agent_id": "dagent-1",
        "channel_key": "telegram",
        "external_user_id": "external-user-1",
        "source": "telegram_limit_hit",
    }
    acquisition_service.resolve_or_create_touch_from_attribution_token.return_value = {
        "id": "touch-1",
        "tenant_id": "tenant-1",
        "workspace_id": "workspace-1",
        "deployed_agent_id": "dagent-1",
        "channel_key": "telegram",
        "external_user_id": "external-user-1",
    }

    with mock.patch(
        "server_modules.deployed_agent_marketplace_service.channel_user_acquisition_service.get_channel_user_acquisition_service",
        return_value=acquisition_service,
    ), mock.patch(
        "server_modules.deployed_agent_marketplace_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
        return_value={
            "ok": True,
            "decision": "allow",
            "operation": "deployed_agent_upgrade_click_write",
            "next_action": "apply_control_plane_destructive_write",
            "mutation_plan": {
                "apply": True,
                "next_action": "apply_control_plane_destructive_write",
            },
        },
    ), mock.patch(
        "server_modules.deployed_agent_marketplace_service.control_plane_repository.record_deployed_agent_upgrade_click",
    ) as record_click:
        with pytest.raises(RuntimeError, match="unexpected next_action"):
            await service.record_upgrade_click(
                channel_attribution="token-1",
                source="telegram_limit_hit",
                agent_id="dagent-1",
            )

    record_click.assert_not_called()
