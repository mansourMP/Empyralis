import unittest
from unittest import mock

from server_modules import deployed_agent_service


class DeployedAgentEmergencyStopRustGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_workspace_emergency_stop_uses_rust_mutation_plan(self):
        deployed_agent = {
            "id": "dagent-1",
            "deployment_state": "live",
            "metadata": {},
        }
        with mock.patch.object(
            deployed_agent_service,
            "require_deployed_agent_admin_access",
            return_value="ws-1",
        ), mock.patch.object(
            deployed_agent_service.control_plane_repository,
            "get_workspace_by_id",
            new=mock.AsyncMock(return_value={"workspace_id": "ws-1", "tenant_id": "tenant-1"}),
        ), mock.patch.object(
            deployed_agent_service.control_plane_repository,
            "list_deployed_agents_for_workspace",
            new=mock.AsyncMock(return_value=[deployed_agent]),
        ), mock.patch.object(
            deployed_agent_service,
            "_enforce_deployed_agent_service_decision",
            return_value={
                "ok": True,
                "decision": "allow",
                "operation": "emergency_stop",
                "mutation_plan": {
                    "target_state": "paused",
                    "stop_active_runs": False,
                    "activate_workspace_emergency_stop": False,
                    "set_last_paused_at": False,
                    "stop_reason": "custom_stop",
                },
            },
        ), mock.patch.object(
            deployed_agent_service,
            "_stop_deployed_agent_live_runs",
            return_value=["run-1"],
        ) as stop_runs, mock.patch.object(
            deployed_agent_service.control_plane_repository,
            "update_deployed_agent",
            new=mock.AsyncMock(return_value={"id": "dagent-1", "deployment_state": "paused", "metadata": {}}),
        ) as update_deployed_agent, mock.patch.object(
            deployed_agent_service,
            "_append_deployed_agent_audit_event",
            new=mock.AsyncMock(return_value={"ok": True}),
        ), mock.patch.object(
            deployed_agent_service,
            "_utc_now_iso",
            return_value="2026-06-01T00:00:00Z",
        ):
            payload = await deployed_agent_service.emergency_stop_workspace_deployed_agents(
                current_user={"user_id": "owner-1"},
                owner_workspace_id="ws-1",
                reason="incident",
            )

        stop_runs.assert_not_called()
        update_deployed_agent.assert_awaited_once()
        update_kwargs = update_deployed_agent.await_args.kwargs
        self.assertEqual(update_kwargs["updates"]["deployment_state"], "paused")
        self.assertNotIn("last_paused_at", update_kwargs["updates"])
        self.assertNotIn("workspace_emergency_stop", update_kwargs["updates"]["metadata"])
        self.assertEqual(payload["status"], "emergency_stopped")
        self.assertEqual(payload["count"], 1)
