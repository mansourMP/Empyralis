from unittest.mock import patch

import pytest

from server_modules import control_plane_repository


@pytest.mark.asyncio
async def test_deployed_agent_conversation_memory_upsert_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await control_plane_repository.upsert_deployed_agent_conversation_memory(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                deployed_agent_id="dagent-1",
                channel_key="telegram",
                external_user_id="external-user-1",
                session_key="session-1",
                summary_text="summary",
                recent_message_count=2,
                source_message_count=5,
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "deployed_agent_record_write"
    assert payload["record_type"] == "deployed_agent_conversation_memory"
    assert payload["tenant_id"] == "tenant-1"
    assert payload["workspace_id"] == "workspace-1"
    assert payload["actor_id"] == "dagent-1"
    assert payload["actor_role"] == "system"
    assert payload["agent_id"] == "dagent-1"
    assert payload["idempotency_key"] == "dagent-1:telegram:external-user-1"


@pytest.mark.asyncio
async def test_deployed_agent_record_write_unexpected_next_action_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        return_value={
            "ok": True,
            "decision": "allow",
            "reason": "control_plane_service_operation_allowed",
            "operation": "deployed_agent_record_write",
            "next_action": "apply_control_plane_destructive_write",
            "mutation_plan": {
                "apply": True,
                "next_action": "apply_control_plane_destructive_write",
            },
        },
    ), patch.object(
        control_plane_repository,
        "_scoped_connection",
        new=pytest.fail,
    ):
        with pytest.raises(RuntimeError, match="unexpected next_action"):
            await control_plane_repository.upsert_deployed_agent_conversation_memory(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                deployed_agent_id="dagent-1",
                channel_key="telegram",
                external_user_id="external-user-1",
                session_key="session-1",
                summary_text="summary",
                recent_message_count=2,
                source_message_count=5,
            )
