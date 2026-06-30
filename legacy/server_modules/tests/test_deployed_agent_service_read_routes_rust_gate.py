from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from server_modules import routes_deployed_agents


class DeployedAgentServiceReadRoutesRustGateTests(unittest.IsolatedAsyncioTestCase):
    def test_service_route_gate_accepts_canonical_audit_export_action(self):
        captured = {}

        def _fake_rust(command: str, payload: dict[str, object]):
            captured["command"] = command
            captured["payload"] = dict(payload)
            return {"next_action": "export_deployed_agent_audit_logs"}

        with (
            patch.object(routes_deployed_agents, "_deployed_agent_route_actor_role", return_value="owner"),
            patch.object(
                routes_deployed_agents.rust_runtime_kernel_client,
                "run_runtime_kernel_enforced",
                side_effect=_fake_rust,
            ),
        ):
            result = routes_deployed_agents._enforce_deployed_agent_route_decision(
                operation="audit_export",
                workspace_id="workspace-1",
                current_user={"id": "user-1"},
                deployed_agent_id="dagent_1",
                audit_export_approval=True,
            )

        self.assertEqual(result["next_action"], "export_deployed_agent_audit_logs")
        self.assertEqual(captured["command"], "deployed-agent-service-decision")
        self.assertEqual(captured["payload"]["operation"], "audit_export")
        self.assertTrue(captured["payload"]["audit_export_approval"])

    def test_service_route_gate_accepts_canonical_analytics_action(self):
        captured = {}

        def _fake_rust(command: str, payload: dict[str, object]):
            captured["command"] = command
            captured["payload"] = dict(payload)
            return {"next_action": "read_deployed_agent_analytics"}

        with (
            patch.object(routes_deployed_agents, "_deployed_agent_route_actor_role", return_value="owner"),
            patch.object(
                routes_deployed_agents.rust_runtime_kernel_client,
                "run_runtime_kernel_enforced",
                side_effect=_fake_rust,
            ),
        ):
            result = routes_deployed_agents._enforce_deployed_agent_route_decision(
                operation="analytics_read",
                workspace_id="workspace-1",
                current_user={"id": "user-1"},
                deployed_agent_id="dagent_1",
            )

        self.assertEqual(result["next_action"], "read_deployed_agent_analytics")
        self.assertEqual(captured["command"], "deployed-agent-service-decision")
        self.assertEqual(captured["payload"]["operation"], "analytics_read")
        self.assertEqual(captured["payload"]["deployed_agent_id"], "dagent_1")
        self.assertEqual(captured["payload"]["actor_id"], "user-1")

    async def test_admin_dashboard_wrong_rust_action_blocks_before_service(self):
        dashboard_service = type("DashboardService", (), {"get_dashboard": AsyncMock()})()

        with (
            patch.object(routes_deployed_agents, "_deployed_agent_route_actor_role", return_value="owner"),
            patch.object(
                routes_deployed_agents.rust_runtime_kernel_client,
                "run_runtime_kernel_enforced",
                return_value={"next_action": "read_deployed_agent_analytics"},
            ),
            patch.object(
                routes_deployed_agents,
                "get_deployed_agent_admin_dashboard_service",
                return_value=dashboard_service,
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                await routes_deployed_agents.get_deployed_agent_admin_dashboard(
                    deployed_agent_id="dagent_1",
                    workspace_id="workspace-1",
                    current_user={"id": "user-1"},
                )

        self.assertEqual(raised.exception.status_code, 423)
        self.assertEqual(raised.exception.detail["error"], "rust_deployed_agent_invalid_next_action")
        self.assertEqual(raised.exception.detail["operation"], "admin_dashboard")
        dashboard_service.get_dashboard.assert_not_awaited()

    async def test_conversation_detail_wrong_rust_action_blocks_before_service(self):
        with (
            patch.object(routes_deployed_agents, "_deployed_agent_route_actor_role", return_value="owner"),
            patch.object(
                routes_deployed_agents.rust_runtime_kernel_client,
                "run_runtime_kernel_enforced",
                return_value={"next_action": "list_deployed_agent_conversations"},
            ),
            patch.object(
                routes_deployed_agents.deployed_agent_service,
                "get_deployed_agent_conversation_detail",
                new=AsyncMock(),
            ) as detail_mock,
        ):
            with self.assertRaises(HTTPException) as raised:
                await routes_deployed_agents.get_deployed_agent_conversation_detail(
                    deployed_agent_id="dagent_1",
                    session_id="session-1",
                    workspace_id="workspace-1",
                    current_user={"id": "user-1"},
                )

        self.assertEqual(raised.exception.status_code, 423)
        self.assertEqual(raised.exception.detail["error"], "rust_deployed_agent_invalid_next_action")
        self.assertEqual(raised.exception.detail["operation"], "conversation_detail")
        detail_mock.assert_not_awaited()

    def test_service_route_gate_accepts_canonical_test_turn_action(self):
        captured = {}

        def _fake_rust(command: str, payload: dict[str, object]):
            captured["command"] = command
            captured["payload"] = dict(payload)
            return {"next_action": "execute_deployed_agent_test_turn"}

        with (
            patch.object(routes_deployed_agents, "_deployed_agent_route_actor_role", return_value="owner"),
            patch.object(
                routes_deployed_agents.rust_runtime_kernel_client,
                "run_runtime_kernel_enforced",
                side_effect=_fake_rust,
            ),
        ):
            result = routes_deployed_agents._enforce_deployed_agent_route_decision(
                operation="test_turn",
                workspace_id="workspace-1",
                current_user={"id": "user-1"},
                deployed_agent_id="dagent_1",
            )

        self.assertEqual(result["next_action"], "execute_deployed_agent_test_turn")
        self.assertEqual(captured["command"], "deployed-agent-service-decision")
        self.assertEqual(captured["payload"]["operation"], "test_turn")
        self.assertEqual(captured["payload"]["deployed_agent_id"], "dagent_1")

    async def test_business_insight_apply_wrong_rust_action_blocks_before_service(self):
        with (
            patch.object(routes_deployed_agents, "_deployed_agent_route_actor_role", return_value="owner"),
            patch.object(
                routes_deployed_agents.rust_runtime_kernel_client,
                "run_runtime_kernel_enforced",
                return_value={"next_action": "review_deployed_agent_business_insight"},
            ),
            patch.object(
                routes_deployed_agents.deployed_agent_business_insights_service,
                "apply_owner_business_insight",
                new=AsyncMock(),
            ) as apply_mock,
        ):
            with self.assertRaises(HTTPException) as raised:
                await routes_deployed_agents.apply_deployed_agent_business_insight(
                    deployed_agent_id="dagent_1",
                    insight_id="insight-1",
                    workspace_id="workspace-1",
                    body=routes_deployed_agents.DeployedAgentBusinessInsightReviewRequest(note="apply"),
                    request=None,
                    current_user={"id": "user-1"},
                )

        self.assertEqual(raised.exception.status_code, 423)
        self.assertEqual(raised.exception.detail["error"], "rust_deployed_agent_invalid_next_action")
        self.assertEqual(raised.exception.detail["operation"], "business_insight_apply")
        apply_mock.assert_not_awaited()

    async def test_test_turn_wrong_rust_action_blocks_before_execution(self):
        with (
            patch.object(routes_deployed_agents.auth_module, "validate_csrf"),
            patch.object(
                routes_deployed_agents.auth_module,
                "enforce_workspace_access",
                return_value="workspace-1",
            ),
            patch.object(
                routes_deployed_agents.auth_module,
                "workspace_tenant_id",
                return_value="tenant-1",
            ),
            patch.object(routes_deployed_agents, "_deployed_agent_route_actor_role", return_value="owner"),
            patch.object(
                routes_deployed_agents.rust_runtime_kernel_client,
                "run_runtime_kernel_enforced",
                return_value={"next_action": "evaluate_shop_assistant"},
            ),
            patch.object(
                routes_deployed_agents.deployed_agent_test_turn_service,
                "execute_test_turn",
                new=AsyncMock(),
            ) as execute_mock,
        ):
            with self.assertRaises(HTTPException) as raised:
                await routes_deployed_agents.test_turn_deployed_agent(
                    deployed_agent_id="dagent_1",
                    body=routes_deployed_agents.DeployedAgentTestTurnRequest(
                        workspace_id="workspace-1",
                        customer_message="Need brake pads",
                    ),
                    request=None,
                    current_user={"id": "user-1"},
                )

        self.assertEqual(raised.exception.status_code, 423)
        self.assertEqual(raised.exception.detail["error"], "rust_deployed_agent_invalid_next_action")
        self.assertEqual(raised.exception.detail["operation"], "test_turn")
        execute_mock.assert_not_awaited()

    async def test_external_user_delete_wrong_rust_action_blocks_before_service(self):
        with (
            patch.object(routes_deployed_agents.auth_module, "validate_csrf"),
            patch.object(routes_deployed_agents, "_deployed_agent_route_actor_role", return_value="owner"),
            patch.object(
                routes_deployed_agents.rust_runtime_kernel_client,
                "run_runtime_kernel_enforced",
                return_value={"next_action": "list_deployed_agent_memory"},
            ),
            patch.object(
                routes_deployed_agents.deployed_agent_service,
                "delete_deployed_agent_external_user_data",
                new=AsyncMock(),
            ) as delete_mock,
        ):
            with self.assertRaises(HTTPException) as raised:
                await routes_deployed_agents.delete_deployed_agent_external_user_data(
                    deployed_agent_id="dagent_1",
                    external_user_id="customer-1",
                    body=routes_deployed_agents.DeployedAgentExternalUserDeleteRequest(
                        workspace_id="workspace-1",
                        channel="telegram",
                        note="owner request",
                        session_id="session-1",
                    ),
                    request=None,
                    current_user={"id": "user-1"},
                )

        self.assertEqual(raised.exception.status_code, 423)
        self.assertEqual(raised.exception.detail["error"], "rust_deployed_agent_invalid_next_action")
        self.assertEqual(raised.exception.detail["operation"], "external_user_delete")
        delete_mock.assert_not_awaited()
