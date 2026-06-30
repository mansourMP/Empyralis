from unittest.mock import patch

import pytest

from server_modules import control_plane_repository


@pytest.mark.asyncio
async def test_knowledge_source_upsert_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await control_plane_repository.upsert_knowledge_source(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                source_id="source-1",
                source_uri="knowledge://source-1",
                source_kind="file",
                agent_id="agent-1",
                status="indexed",
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "knowledge_source_upsert"
    assert payload["record_type"] == "knowledge_source"
    assert payload["tenant_id"] == "tenant-1"
    assert payload["workspace_id"] == "workspace-1"
    assert payload["actor_id"] == "agent-1"
    assert payload["agent_id"] == "agent-1"
    assert payload["idempotency_key"] == "source-1"
    assert payload["target_status"] == "indexed"


@pytest.mark.asyncio
async def test_knowledge_source_chunks_replace_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await control_plane_repository.replace_knowledge_source_chunks(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                source_id="source-1",
                agent_id="agent-1",
                chunks=[{"id": "chunk-1", "chunk_text": "hello"}],
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "knowledge_source_chunks_replace"
    assert payload["record_type"] == "knowledge_chunks"
    assert payload["tenant_id"] == "tenant-1"
    assert payload["workspace_id"] == "workspace-1"
    assert payload["actor_id"] == "agent-1"
    assert payload["agent_id"] == "agent-1"
    assert payload["idempotency_key"] == "source-1:1"


@pytest.mark.asyncio
async def test_knowledge_retrieval_event_write_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await control_plane_repository.record_knowledge_retrieval_event(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                surface="runtime",
                query_hash="hash-1",
                user_id="user-1",
                thread_id="thread-1",
                run_id="run-1",
                agent_id="agent-1",
                event_id="retrieval-1",
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "knowledge_retrieval_event_write"
    assert payload["record_type"] == "knowledge_retrieval_event"
    assert payload["tenant_id"] == "tenant-1"
    assert payload["workspace_id"] == "workspace-1"
    assert payload["actor_id"] == "user-1"
    assert payload["agent_id"] == "agent-1"
    assert payload["run_id"] == "run-1"
    assert payload["thread_id"] == "thread-1"
    assert payload["idempotency_key"] == "retrieval-1"


@pytest.mark.asyncio
async def test_knowledge_source_upsert_unexpected_next_action_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
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
            await control_plane_repository.upsert_knowledge_source(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                source_id="source-1",
                source_uri="knowledge://source-1",
                source_kind="file",
                agent_id="agent-1",
                status="indexed",
            )
