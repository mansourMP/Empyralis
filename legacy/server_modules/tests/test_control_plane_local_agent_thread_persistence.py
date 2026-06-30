from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest

from server_modules import control_plane_repository


@asynccontextmanager
async def _no_control_plane_connection(**_kwargs):
    yield None


def _allow_control_plane_write(_command: str, _payload: dict):
    return {
        "decision": "allow",
        "next_action": "apply_control_plane_write",
        "mutation_plan": {"next_action": "apply_control_plane_write"},
    }


@pytest.mark.asyncio
async def test_local_agent_threads_persist_across_memory_reset(monkeypatch: pytest.MonkeyPatch, tmp_path):
    local_store = tmp_path / "control-plane" / "agent-threads.json"
    monkeypatch.setattr(control_plane_repository, "LOCAL_AGENT_THREADS_FILE", local_store)
    monkeypatch.setattr(control_plane_repository, "_scoped_connection", _no_control_plane_connection)
    control_plane_repository._LOCAL_AGENT_THREADS.clear()
    control_plane_repository._LOCAL_AGENT_TURNS.clear()
    control_plane_repository._LOCAL_AGENT_THREADS_LOADED = False

    try:
        with patch.object(
            control_plane_repository.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=_allow_control_plane_write,
        ):
            await control_plane_repository.ensure_agent_thread(
                thread_id="thread-local-1",
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                owner_user_id="user-1",
                master_agent_install_id="install-1",
                channel="web",
                title="New chat",
                metadata={"source": "test"},
            )
            await control_plane_repository.upsert_agent_turn(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                thread_id="thread-local-1",
                session_id="session-1",
                role="user",
                content="Create my morning brief",
                actor={"id": "user-1"},
                request_id="request-user-1",
                turn_id="turn-user-1",
            )
            await control_plane_repository.upsert_agent_turn(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                thread_id="thread-local-1",
                session_id="session-1",
                role="assistant",
                content="Working on it.",
                actor={"id": "sage"},
                run_id="run-1",
                request_id="request-assistant-1",
                turn_id="turn-assistant-1",
                metadata={"trace_id": "trace-1"},
            )
            await control_plane_repository.append_agent_turn_transcript_event(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                thread_id="thread-local-1",
                transcript_event={"type": "agent_activity", "label": "Running on your Mac"},
                request_id="request-assistant-1",
                trace_id="trace-1",
                run_id="run-1",
            )

        assert local_store.exists()

        control_plane_repository._LOCAL_AGENT_THREADS.clear()
        control_plane_repository._LOCAL_AGENT_TURNS.clear()
        control_plane_repository._LOCAL_AGENT_THREADS_LOADED = False

        restored = await control_plane_repository.get_agent_thread(
            "thread-local-1",
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            include_turns=True,
        )
        assert restored is not None
        assert restored["id"] == "thread-local-1"
        assert restored["title"] == "Create my morning brief"
        assert [turn["content"] for turn in restored["turns"]] == [
            "Create my morning brief",
            "Working on it.",
        ]
        assert restored["turns"][1]["metadata"]["transcript_events"] == [
            {"type": "agent_activity", "label": "Running on your Mac"}
        ]

        listed = await control_plane_repository.list_agent_threads(
            workspace_id="workspace-1",
            tenant_id="tenant-1",
            owner_user_id="user-1",
            include_turns=True,
        )
        assert [thread["id"] for thread in listed] == ["thread-local-1"]
        assert listed[0]["turns"][0]["content"] == "Create my morning brief"
    finally:
        control_plane_repository._LOCAL_AGENT_THREADS.clear()
        control_plane_repository._LOCAL_AGENT_TURNS.clear()
        control_plane_repository._LOCAL_AGENT_THREADS_LOADED = False
