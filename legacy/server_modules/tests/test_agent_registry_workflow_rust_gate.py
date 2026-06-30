from unittest.mock import patch

import pytest

from server_modules import agent_registry_repository


@pytest.mark.asyncio
async def test_compiled_workflow_artifact_create_blocks_before_schema_access():
    with patch.object(
        agent_registry_repository.control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await agent_registry_repository.create_compiled_workflow_artifact(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                name="Compiled Agent",
                description="Compiled workflow",
                definition={"version": "empyralist.workflow.v2", "nodes": [], "edges": []},
                validation={"hasDraftErrors": False},
                created_by_user_id="user-1",
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "compiled_workflow_artifact_create"
    assert payload["record_type"] == "compiled_workflow_artifact"
    assert payload["tenant_id"] == "tenant-1"
    assert payload["workspace_id"] == "workspace-1"
    assert payload["actor_id"] == "user-1"
    assert str(payload["idempotency_key"]).startswith("wf_")


@pytest.mark.asyncio
async def test_install_compiled_artifact_update_blocks_before_schema_access():
    with patch.object(
        agent_registry_repository.control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await agent_registry_repository.update_workspace_agent_install_compiled_artifact(
                "install-1",
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                compiled_workflow_version_id="wfver-1",
                metadata={"compiled_workflow_id": "wf-1"},
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "workspace_agent_install_compiled_artifact_update"
    assert payload["record_type"] == "workspace_agent_install"
    assert payload["tenant_id"] == "tenant-1"
    assert payload["workspace_id"] == "workspace-1"
    assert payload["actor_id"] == "install-1"
    assert payload["idempotency_key"] == "install-1:wfver-1"


@pytest.mark.asyncio
async def test_compiled_workflow_artifact_create_unexpected_next_action_blocks_before_schema_access():
    with patch.object(
        agent_registry_repository.control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        return_value={
            "decision": "allow",
            "next_action": "apply_control_plane_destructive_write",
            "mutation_plan": {
                "apply": True,
                "next_action": "apply_control_plane_destructive_write",
            },
        },
    ):
        with pytest.raises(RuntimeError, match="unexpected next_action"):
            await agent_registry_repository.create_compiled_workflow_artifact(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                name="Compiled Agent",
                description="Compiled workflow",
                definition={"version": "empyralist.workflow.v2", "nodes": [], "edges": []},
                validation={"hasDraftErrors": False},
                created_by_user_id="user-1",
            )
