from unittest.mock import patch

import pytest

from server_modules import control_plane_repository


@pytest.mark.asyncio
async def test_agent_channel_event_write_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await control_plane_repository.append_agent_channel_event(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                channel_key="telegram",
                endpoint_key="bot-1",
                session_key="session-1",
                direction="inbound",
                event_type="message",
                message_id="message-1",
                actor={"id": "external-user-1"},
                deployed_agent_id="dagent-1",
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "agent_channel_event_write"
    assert payload["record_type"] == "agent_channel_event"
    assert payload["tenant_id"] == "tenant-1"
    assert payload["workspace_id"] == "workspace-1"
    assert payload["actor_id"] == "external-user-1"
    assert payload["agent_id"] == "dagent-1"
    assert payload["idempotency_key"] == "message-1"
    assert payload["webhook_id"] == "bot-1"
