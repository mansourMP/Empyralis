import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from server_modules import control_plane_repository


def test_scheduler_wake_append_blocks_before_database_access() -> None:
    async def run() -> None:
        with patch.object(
            control_plane_repository.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=RuntimeError("rust denied"),
        ) as kernel:
            with pytest.raises(RuntimeError, match="rust denied"):
                await control_plane_repository.append_agent_scheduler_wake_request(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    trigger_kind="event_trigger",
                    source="context",
                    requested_by="system",
                    due_at=datetime.now(timezone.utc),
                    payload={"event_id": "event-1", "priority": 80},
                    policy={"scheduler_enabled": True},
                )

        command, payload = kernel.call_args.args
        assert command == "session-scheduler-decision"
        assert payload["operation"] == "event_trigger"
        assert payload["workspace_id"] == "workspace-1"
        assert payload["trigger_kind"] == "event_trigger"

    asyncio.run(run())


def test_scheduler_wake_claim_blocks_before_database_access() -> None:
    async def run() -> None:
        with patch.object(
            control_plane_repository.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=RuntimeError("rust denied"),
        ) as kernel:
            with pytest.raises(RuntimeError, match="rust denied"):
                await control_plane_repository.claim_due_agent_scheduler_wake_requests(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    due_before=datetime.now(timezone.utc),
                    limit=5,
                )

        command, payload = kernel.call_args.args
        assert command == "session-scheduler-decision"
        assert payload["operation"] == "claim_wake_requests"
        assert payload["workspace_id"] == "workspace-1"
        assert payload["candidate_count"] == 5

    asyncio.run(run())


def test_scheduler_wake_append_blocks_on_unexpected_next_action() -> None:
    async def run() -> None:
        with patch.object(
            control_plane_repository.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={"ok": True, "decision": "allow", "next_action": "claim_due_wake_requests"},
        ), patch.object(control_plane_repository, "_scoped_connection") as scoped_connection:
            with pytest.raises(RuntimeError, match="unexpected next_action"):
                await control_plane_repository.append_agent_scheduler_wake_request(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    trigger_kind="event_trigger",
                    source="context",
                    requested_by="system",
                    due_at=datetime.now(timezone.utc),
                    payload={"event_id": "event-1", "priority": 80},
                    policy={"scheduler_enabled": True},
                )

        scoped_connection.assert_not_called()

    asyncio.run(run())


def test_scheduler_wake_claim_blocks_on_unexpected_next_action() -> None:
    async def run() -> None:
        with patch.object(
            control_plane_repository.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={"ok": True, "decision": "allow", "next_action": "schedule_retry"},
        ), patch.object(control_plane_repository, "_scoped_connection") as scoped_connection:
            with pytest.raises(RuntimeError, match="unexpected next_action"):
                await control_plane_repository.claim_due_agent_scheduler_wake_requests(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    due_before=datetime.now(timezone.utc),
                    limit=5,
                )

        scoped_connection.assert_not_called()

    asyncio.run(run())


def test_scheduler_wake_update_blocks_on_unexpected_next_action_before_execute() -> None:
    async def run() -> None:
        connection = AsyncMock()
        connection.fetchrow = AsyncMock(
            side_effect=[
                {
                    "id": "wake-1",
                    "trigger_kind": "event_trigger",
                    "policy": {"scheduler_enabled": True},
                    "payload": {"priority": 80},
                    "metadata": {"retry": {"retry_attempt": 0}},
                }
            ]
        )
        connection.execute = AsyncMock()

        @asynccontextmanager
        async def fake_scoped_connection(**kwargs):
            yield connection

        with patch.object(
            control_plane_repository.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={"ok": True, "decision": "allow", "next_action": "claim_due_wake_requests"},
        ), patch.object(
            control_plane_repository,
            "_scoped_connection",
            side_effect=fake_scoped_connection,
        ):
            with pytest.raises(RuntimeError, match="unexpected next_action"):
                await control_plane_repository.update_agent_scheduler_wake_request_status(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    wake_id="wake-1",
                    status="retry_scheduled",
                    metadata_patch={"retry": {"retry_attempt": 1}},
                )

        connection.execute.assert_not_awaited()

    asyncio.run(run())


def test_scheduler_wake_append_decodes_jsonb_fields_from_the_round_trip() -> None:
    """MAN-294: no jsonb codec is registered on this pool, so a bare
    `dict(row)` off asyncpg hands back payload/policy/metadata as raw JSON
    TEXT, not dicts -- append_agent_scheduler_wake_request's return value
    goes straight through bounded_scheduler_service._persist_wakeup to
    schedule_task_assigned_wakeup/schedule_task_commented_wakeup and from
    there straight into the HTTP response as `wake_request`, so a caller
    reading wake_request["metadata"]["policy_delay_reason"] needs a real
    dict, not a string it has to remember to json.loads() itself. This
    fixture's fetchrow response deliberately returns JSON TEXT for all
    three fields -- exactly what an uncodec'd asyncpg connection actually
    returns -- to prove the decode happens, not just that a dict passed
    straight through unchanged."""

    async def run() -> None:
        connection = AsyncMock()
        connection.execute = AsyncMock()
        connection.fetchrow = AsyncMock(
            return_value={
                "id": "wake-1",
                "tenant_id": "tenant-1",
                "workspace_id": "workspace-1",
                "trigger_kind": "task_assigned",
                "source": "project_tasks",
                "status": "pending",
                # Raw JSON text, matching what an uncodec'd asyncpg
                # connection actually hands back for a jsonb column --
                # NOT a dict.
                "payload": '{"agent_id": "agent-1", "task_id": "task-1"}',
                "policy": '{"quiet_hours_start": 22, "quiet_hours_end": 7}',
                "metadata": '{"agent_id": "agent-1", "task_id": "task-1", "policy_delay_reason": "quiet_hours"}',
            }
        )

        @asynccontextmanager
        async def fake_scoped_connection(**kwargs):
            yield connection

        with patch.object(
            control_plane_repository.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            # task_assigned -> _scheduler_wake_operation() -> "wake_decision",
            # whose accepted next_actions are trigger_wakeup/request_session_
            # scheduler_approval/defer_session_scheduler_operation (NOT the
            # operation name itself) -- see _SCHEDULER_WAKE_REPOSITORY_NEXT_
            # ACTIONS above.
            return_value={"ok": True, "decision": "allow", "next_action": "trigger_wakeup"},
        ), patch.object(
            control_plane_repository,
            "_scoped_connection",
            side_effect=fake_scoped_connection,
        ):
            record = await control_plane_repository.append_agent_scheduler_wake_request(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                trigger_kind="task_assigned",
                source="project_tasks",
                requested_by="owner",
                due_at=datetime.now(timezone.utc),
                payload={"agent_id": "agent-1", "task_id": "task-1"},
                policy={"quiet_hours_start": 22, "quiet_hours_end": 7},
                metadata={"agent_id": "agent-1", "task_id": "task-1", "policy_delay_reason": "quiet_hours"},
            )

        assert isinstance(record["payload"], dict)
        assert record["payload"]["task_id"] == "task-1"
        assert isinstance(record["policy"], dict)
        assert record["policy"]["quiet_hours_start"] == 22
        assert isinstance(record["metadata"], dict)
        assert record["metadata"]["policy_delay_reason"] == "quiet_hours"

    asyncio.run(run())

    asyncio.run(run())
