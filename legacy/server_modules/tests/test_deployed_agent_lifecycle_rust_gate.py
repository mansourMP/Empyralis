from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
import pytest

from server_modules import deployed_agent_service


def _workspace() -> dict:
    return {
        "workspace_id": "ws-1",
        "tenant_id": "tenant-1",
    }


def _owner_user() -> dict:
    return {
        "user_id": "user-1",
        "role": "owner",
    }


def _deployed_agent(*, state: str = "live", metadata: dict | None = None, channels: dict | None = None) -> dict:
    return {
        "id": "dagent_1",
        "tenant_id": "tenant-1",
        "owner_workspace_id": "ws-1",
        "backing_install_id": "ainstall_1",
        "name": "Agent One",
        "deployment_state": state,
        "runtime_target": "cloud",
        "channels": channels if channels is not None else {"telegram": {"enabled": True, "endpoint_key": "store-bot"}},
        "metadata": metadata if metadata is not None else {},
        "operational_state": {},
    }


@pytest.mark.asyncio
async def test_archive_deployed_agent_uses_rust_mutation_plan() -> None:
    archived_row = _deployed_agent(state="archived", channels={"telegram": {"enabled": True, "endpoint_key": "store-bot"}})
    stop_runs = patch(
        "server_modules.deployed_agent_service.run_state_repository.sync_list_live_runs",
        return_value=[],
    )
    with (
        patch(
            "server_modules.deployed_agent_service.control_plane_repository.get_workspace_by_id",
            new=AsyncMock(return_value=_workspace()),
        ),
        patch(
            "server_modules.deployed_agent_service.control_plane_repository.get_deployed_agent_by_id",
            new=AsyncMock(return_value=_deployed_agent(state="paused")),
        ),
        patch(
            "server_modules.deployed_agent_service._enforce_deployed_agent_service_decision",
            return_value={
                "decision": "allow",
                "mutation_plan": {
                    "target_state": "archived",
                    "disable_channels": False,
                    "stop_active_runs": False,
                },
            },
        ),
        stop_runs as list_runs_mock,
        patch(
            "server_modules.deployed_agent_service.control_plane_repository.update_deployed_agent",
            new=AsyncMock(return_value=archived_row),
        ) as update_mock,
        patch(
            "server_modules.deployed_agent_service._append_deployed_agent_audit_event",
            new=AsyncMock(),
        ),
        patch(
            "server_modules.deployed_agent_service.project_deployed_agent",
            side_effect=lambda payload, include_internal=False: payload,
        ),
    ):
        payload = await deployed_agent_service.archive_deployed_agent(
            deployed_agent_id="dagent_1",
            current_user=_owner_user(),
            owner_workspace_id="ws-1",
            reason="retired",
        )

    assert payload["deployment_state"] == "archived"
    updates = update_mock.await_args.kwargs["updates"]
    assert updates["deployment_state"] == "archived"
    assert updates["channels"]["telegram"]["enabled"] is True
    list_runs_mock.assert_not_called()


@pytest.mark.asyncio
async def test_recover_deployed_agent_uses_rust_clear_kill_switch_flag() -> None:
    metadata = {
        "kill_switch": {
            "active": True,
            "reason": "incident",
        }
    }
    recovered_row = _deployed_agent(state="ready_for_review", metadata=metadata)
    with (
        patch(
            "server_modules.deployed_agent_service.control_plane_repository.get_workspace_by_id",
            new=AsyncMock(return_value=_workspace()),
        ),
        patch(
            "server_modules.deployed_agent_service.control_plane_repository.get_deployed_agent_by_id",
            new=AsyncMock(return_value=_deployed_agent(state="suspended", metadata=metadata)),
        ),
        patch(
            "server_modules.deployed_agent_service._enforce_deployed_agent_service_decision",
            return_value={
                "decision": "allow",
                "mutation_plan": {
                    "target_state": "ready_for_review",
                    "clear_kill_switch": False,
                },
            },
        ),
        patch(
            "server_modules.deployed_agent_service.control_plane_repository.update_deployed_agent",
            new=AsyncMock(return_value=recovered_row),
        ) as update_mock,
        patch(
            "server_modules.deployed_agent_service._append_deployed_agent_audit_event",
            new=AsyncMock(),
        ),
        patch(
            "server_modules.deployed_agent_service.project_deployed_agent",
            side_effect=lambda payload, include_internal=False: payload,
        ),
    ):
        payload = await deployed_agent_service.recover_deployed_agent(
            deployed_agent_id="dagent_1",
            current_user=_owner_user(),
            owner_workspace_id="ws-1",
        )

    assert payload["deployment_state"] == "ready_for_review"
    updates = update_mock.await_args.kwargs["updates"]
    assert updates["deployment_state"] == "ready_for_review"
    assert updates["metadata"]["kill_switch"]["active"] is True
    assert "cleared_at" not in updates["metadata"]["kill_switch"]


@pytest.mark.asyncio
async def test_pause_deployed_agent_blocks_on_unexpected_rust_next_action() -> None:
    with (
        patch(
            "server_modules.deployed_agent_service.require_deployed_agent_admin_access",
            return_value="ws-1",
        ),
        patch(
            "server_modules.deployed_agent_service.control_plane_repository.get_workspace_by_id",
            new=AsyncMock(return_value=_workspace()),
        ),
        patch(
            "server_modules.deployed_agent_service.control_plane_repository.get_deployed_agent_by_id",
            new=AsyncMock(return_value=_deployed_agent(state="live")),
        ),
        patch(
            "server_modules.deployed_agent_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "next_action": "archive_deployed_agent",
                "mutation_plan": {"target_state": "paused"},
            },
        ),
        patch(
            "server_modules.deployed_agent_service.control_plane_repository.set_deployed_agent_state",
            new=AsyncMock(),
        ) as set_state_mock,
    ):
        with pytest.raises(HTTPException) as raised:
            await deployed_agent_service.pause_deployed_agent(
                deployed_agent_id="dagent_1",
                current_user=_owner_user(),
                owner_workspace_id="ws-1",
            )

    assert "unexpected next_action" in str(raised.value.detail)
    set_state_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_archive_deployed_agent_blocks_on_unexpected_rust_next_action() -> None:
    with (
        patch(
            "server_modules.deployed_agent_service.require_deployed_agent_admin_access",
            return_value="ws-1",
        ),
        patch(
            "server_modules.deployed_agent_service.control_plane_repository.get_workspace_by_id",
            new=AsyncMock(return_value=_workspace()),
        ),
        patch(
            "server_modules.deployed_agent_service.control_plane_repository.get_deployed_agent_by_id",
            new=AsyncMock(return_value=_deployed_agent(state="paused")),
        ),
        patch(
            "server_modules.deployed_agent_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "next_action": "recover_deployed_agent",
                "mutation_plan": {"target_state": "archived"},
            },
        ),
        patch(
            "server_modules.deployed_agent_service._stop_deployed_agent_live_runs",
            return_value=[],
        ) as stop_runs_mock,
        patch(
            "server_modules.deployed_agent_service.control_plane_repository.update_deployed_agent",
            new=AsyncMock(),
        ) as update_mock,
    ):
        with pytest.raises(HTTPException) as raised:
            await deployed_agent_service.archive_deployed_agent(
                deployed_agent_id="dagent_1",
                current_user=_owner_user(),
                owner_workspace_id="ws-1",
                reason="retired",
            )

    assert "unexpected next_action" in str(raised.value.detail)
    stop_runs_mock.assert_not_called()
    update_mock.assert_not_awaited()
