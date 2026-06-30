from __future__ import annotations

from unittest.mock import patch

import pytest

from server_modules import agent_memory, workspace_context


def _rust_allow_with_action(next_action: str) -> dict:
    return {
        "decision": "allow",
        "allowed": True,
        "next_action": next_action,
    }


def test_workspace_context_save_blocks_wrong_rust_next_action(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(workspace_context, "_WORKSPACE_DIR", tmp_path / "workspace_context")
    decision = _rust_allow_with_action("wrong_workspace_context_action")

    with patch.object(
        workspace_context.rust_runtime_kernel_client,
        "runtime_state_store_decision",
        return_value=decision,
    ), patch.object(
        workspace_context.rust_runtime_kernel_client,
        "enforce_kernel_decision",
        return_value=None,
    ):
        with pytest.raises(workspace_context.WorkspaceContextRustGateError, match="unexpected_next_action"):
            workspace_context.write_workspace_context_file(
                "MEMORY.md",
                "hello",
                workspace_id="ws-123",
            )

    assert not (workspace_context.agent_workspace_context_dir(workspace_id="ws-123") / "MEMORY.md").exists()


def test_workspace_context_initialize_blocks_wrong_rust_next_action(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(workspace_context, "_WORKSPACE_DIR", tmp_path / "workspace_context")
    decision = _rust_allow_with_action("wrong_workspace_context_action")
    target_filename = next(iter(workspace_context.ALLOWED_CONTEXT_FILENAMES))

    with patch.object(
        workspace_context.rust_runtime_kernel_client,
        "runtime_state_store_decision",
        return_value=decision,
    ), patch.object(
        workspace_context.rust_runtime_kernel_client,
        "enforce_kernel_decision",
        return_value=None,
    ):
        with pytest.raises(workspace_context.WorkspaceContextRustGateError, match="unexpected_next_action"):
            workspace_context.ensure_workspace_context_files(workspace_id="ws-456")

    assert not (workspace_context.agent_workspace_context_dir(workspace_id="ws-456") / target_filename).exists()


def test_agent_memory_daily_log_blocks_wrong_rust_next_action(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(agent_memory, "_MEMORY_DIR", tmp_path / "memory")
    decision = _rust_allow_with_action("wrong_agent_memory_action")

    with patch(
        "server_modules.rust_runtime_kernel_client.runtime_state_store_decision",
        return_value=decision,
    ), patch(
        "server_modules.rust_runtime_kernel_client.enforce_kernel_decision",
        return_value=None,
    ):
        with pytest.raises(agent_memory.AgentMemoryRustGateError, match="unexpected_next_action"):
            agent_memory._save_daily_log("ws-789", "important note")

    assert not list((tmp_path / "memory").rglob("*.md"))
