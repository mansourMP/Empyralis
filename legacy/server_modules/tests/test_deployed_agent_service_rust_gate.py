from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from server_modules import deployed_agent_service


class DeployedAgentServiceRustGateTests(unittest.IsolatedAsyncioTestCase):
    def test_service_gate_uses_owner_role_from_workspace_access(self):
        captured = {}

        def _fake_rust(command: str, payload: dict[str, object]):
            captured["command"] = command
            captured["payload"] = dict(payload)
            return {"next_action": "read_deployed_agent_admin_dashboard"}

        with patch.object(
            deployed_agent_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=_fake_rust,
        ):
            result = deployed_agent_service._enforce_deployed_agent_service_decision(
                operation="admin_dashboard",
                deployed_agent={"id": "dagent_1", "deployment_state": "live", "metadata": {}},
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                current_user={
                    "user_id": "user-1",
                    "workspace_access": {"workspace-1": {"role": "owner"}},
                },
            )

        self.assertEqual(result["next_action"], "read_deployed_agent_admin_dashboard")
        self.assertEqual(captured["command"], "deployed-agent-service-decision")
        self.assertEqual(captured["payload"]["actor_role"], "owner")
        self.assertTrue(captured["payload"]["owner_access"])

    def test_service_gate_uses_member_role_without_owner_access(self):
        captured = {}

        def _fake_rust(command: str, payload: dict[str, object]):
            captured["command"] = command
            captured["payload"] = dict(payload)
            return {"next_action": "read_deployed_agent_analytics"}

        with patch.object(
            deployed_agent_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=_fake_rust,
        ):
            result = deployed_agent_service._enforce_deployed_agent_service_decision(
                operation="analytics_read",
                deployed_agent={"id": "dagent_1", "deployment_state": "live", "metadata": {}},
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                current_user={
                    "user_id": "user-1",
                    "workspace_access": {"workspace-1": {"role": "member"}},
                },
            )

        self.assertEqual(result["next_action"], "read_deployed_agent_analytics")
        self.assertEqual(captured["payload"]["actor_role"], "member")
        self.assertFalse(captured["payload"]["owner_access"])

    def test_service_gate_accepts_canonical_telegram_readiness_action(self):
        captured = {}

        def _fake_rust(command: str, payload: dict[str, object]):
            captured["command"] = command
            captured["payload"] = dict(payload)
            return {"next_action": "read_telegram_readiness"}

        with patch.object(
            deployed_agent_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=_fake_rust,
        ):
            result = deployed_agent_service._enforce_deployed_agent_service_decision(
                "telegram_readiness",
                deployed_agent={},
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                current_user={"user_id": "user-1"},
            )

        self.assertEqual(result["next_action"], "read_telegram_readiness")
        self.assertEqual(captured["command"], "deployed-agent-service-decision")
        self.assertEqual(captured["payload"]["operation"], "telegram_readiness")
        self.assertEqual(captured["payload"]["workspace_id"], "workspace-1")

    async def test_verify_knowledge_wrong_rust_action_blocks_before_retrieval(self):
        with (
            patch(
                "server_modules.deployed_agent_service.auth_module.enforce_workspace_access",
                return_value="workspace-1",
            ),
            patch(
                "server_modules.deployed_agent_service.control_plane_repository.get_workspace_by_id",
                new=AsyncMock(return_value={"id": "workspace-1", "tenant_id": "tenant-1"}),
            ),
            patch(
                "server_modules.deployed_agent_service.control_plane_repository.get_deployed_agent_by_id",
                new=AsyncMock(return_value={"id": "dagent_1", "deployment_state": "live", "metadata": {}}),
            ),
            patch.object(
                deployed_agent_service.rust_runtime_kernel_client,
                "run_runtime_kernel_enforced",
                return_value={"next_action": "upload_deployed_agent_knowledge_reference"},
            ),
            patch.object(
                deployed_agent_service.knowledge_rag_service,
                "retrieve_knowledge",
                new=AsyncMock(),
            ) as retrieve_mock,
        ):
            with self.assertRaises(HTTPException) as raised:
                await deployed_agent_service.verify_deployed_agent_knowledge_retrieval(
                    deployed_agent_id="dagent_1",
                    current_user={"user_id": "user-1"},
                    owner_workspace_id="workspace-1",
                    query="brake pads",
                )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("unexpected next_action", str(raised.exception.detail))
        retrieve_mock.assert_not_awaited()

    async def test_conversation_detail_wrong_rust_action_blocks_before_event_load(self):
        with (
            patch(
                "server_modules.deployed_agent_service.auth_module.enforce_workspace_access",
                return_value="workspace-1",
            ),
            patch(
                "server_modules.deployed_agent_service.control_plane_repository.get_workspace_by_id",
                new=AsyncMock(return_value={"id": "workspace-1", "tenant_id": "tenant-1"}),
            ),
            patch(
                "server_modules.deployed_agent_service.control_plane_repository.get_deployed_agent_by_id",
                new=AsyncMock(return_value={"id": "dagent_1", "deployment_state": "live", "metadata": {}}),
            ),
            patch.object(
                deployed_agent_service.rust_runtime_kernel_client,
                "run_runtime_kernel_enforced",
                return_value={"next_action": "list_deployed_agent_conversations"},
            ),
            patch(
                "server_modules.deployed_agent_service.control_plane_repository.list_deployed_agent_conversation_events",
                new=AsyncMock(),
            ) as events_mock,
        ):
            with self.assertRaises(HTTPException) as raised:
                await deployed_agent_service.get_deployed_agent_conversation_detail(
                    deployed_agent_id="dagent_1",
                    session_id="session-1",
                    current_user={"user_id": "user-1"},
                    owner_workspace_id="workspace-1",
                )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("unexpected next_action", str(raised.exception.detail))
        events_mock.assert_not_awaited()

    async def test_shop_evaluate_wrong_rust_action_blocks_before_evaluator(self):
        with (
            patch(
                "server_modules.deployed_agent_service.auth_module.enforce_workspace_access",
                return_value="workspace-1",
            ),
            patch(
                "server_modules.deployed_agent_service.control_plane_repository.get_workspace_by_id",
                new=AsyncMock(return_value={"id": "workspace-1", "tenant_id": "tenant-1"}),
            ),
            patch(
                "server_modules.deployed_agent_service.control_plane_repository.get_deployed_agent_by_id",
                new=AsyncMock(return_value={"id": "dagent_1", "deployment_state": "live", "metadata": {}}),
            ),
            patch.object(
                deployed_agent_service.rust_runtime_kernel_client,
                "run_runtime_kernel_enforced",
                return_value={"next_action": "read_deployed_agent_analytics"},
            ),
            patch.object(
                deployed_agent_service.shop_assistant_revenue_agent_service,
                "evaluate_shop_assistant_customer_question",
            ) as evaluate_mock,
        ):
            with self.assertRaises(HTTPException) as raised:
                await deployed_agent_service.evaluate_deployed_shop_assistant_customer_question(
                    deployed_agent_id="dagent_1",
                    current_user={"user_id": "user-1"},
                    owner_workspace_id="workspace-1",
                    customer_message="Need brake pads",
                )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("unexpected next_action", str(raised.exception.detail))
        evaluate_mock.assert_not_called()

    async def test_telegram_readiness_wrong_rust_action_blocks_before_status_payload(self):
        with (
            patch(
                "server_modules.deployed_agent_service.auth_module.enforce_workspace_access",
                return_value="workspace-1",
            ),
            patch(
                "server_modules.deployed_agent_service.control_plane_repository.get_workspace_by_id",
                new=AsyncMock(return_value={"id": "workspace-1", "tenant_id": "tenant-1"}),
            ),
            patch.object(
                deployed_agent_service.rust_runtime_kernel_client,
                "run_runtime_kernel_enforced",
                return_value={"next_action": "read_deployed_agent_analytics"},
            ),
            patch(
                "server_modules.deployed_agent_service._workspace_telegram_status_payload",
                return_value={"status": "live", "issues": [], "webhook": {"status": "live"}},
            ) as status_mock,
        ):
            with self.assertRaises(HTTPException) as raised:
                await deployed_agent_service.get_deployed_agent_telegram_readiness(
                    current_user={"user_id": "user-1"},
                    owner_workspace_id="workspace-1",
                )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("unexpected next_action", str(raised.exception.detail))
        status_mock.assert_not_called()

    def test_service_gate_accepts_canonical_knowledge_upload_action(self):
        captured = {}

        def _fake_rust(command: str, payload: dict[str, object]):
            captured["command"] = command
            captured["payload"] = dict(payload)
            return {"next_action": "upload_deployed_agent_knowledge_reference"}

        with patch.object(
            deployed_agent_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=_fake_rust,
        ):
            result = deployed_agent_service._enforce_deployed_agent_service_decision(
                "knowledge_upload",
                deployed_agent={"id": "dagent_1", "deployment_state": "live", "metadata": {}},
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                current_user={"user_id": "user-1"},
                knowledge_reference_only=True,
            )

        self.assertEqual(result["next_action"], "upload_deployed_agent_knowledge_reference")
        self.assertEqual(captured["command"], "deployed-agent-service-decision")
        self.assertEqual(captured["payload"]["operation"], "knowledge_upload")
        self.assertTrue(captured["payload"]["knowledge_reference_only"])

    def test_service_gate_accepts_canonical_external_user_delete_action(self):
        captured = {}

        def _fake_rust(command: str, payload: dict[str, object]):
            captured["command"] = command
            captured["payload"] = dict(payload)
            return {"next_action": "delete_deployed_agent_external_user_data"}

        with patch.object(
            deployed_agent_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=_fake_rust,
        ):
            result = deployed_agent_service._enforce_deployed_agent_service_decision(
                operation="external_user_delete",
                deployed_agent={"id": "dagent_1", "deployment_state": "live", "metadata": {}},
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                current_user={
                    "user_id": "user-1",
                    "workspace_access": {"workspace-1": {"role": "owner"}},
                },
                external_user_id="customer-1",
                session_id="session-1",
                privacy_delete_verified=True,
            )

        self.assertEqual(result["next_action"], "delete_deployed_agent_external_user_data")
        self.assertEqual(captured["payload"]["operation"], "external_user_delete")
        self.assertEqual(captured["payload"]["external_user_id"], "customer-1")
        self.assertEqual(captured["payload"]["session_id"], "session-1")
        self.assertTrue(captured["payload"]["privacy_delete_verified"])

    async def test_audit_export_wrong_rust_action_blocks_before_activity_load(self):
        with (
            patch(
                "server_modules.deployed_agent_service.auth_module.enforce_workspace_access",
                return_value="workspace-1",
            ),
            patch(
                "server_modules.deployed_agent_service.control_plane_repository.get_workspace_by_id",
                new=AsyncMock(return_value={"id": "workspace-1", "tenant_id": "tenant-1"}),
            ),
            patch(
                "server_modules.deployed_agent_service.control_plane_repository.get_deployed_agent_by_id",
                new=AsyncMock(return_value={"id": "dagent_1", "deployment_state": "live", "metadata": {}}),
            ),
            patch.object(
                deployed_agent_service.rust_runtime_kernel_client,
                "run_runtime_kernel_enforced",
                side_effect=lambda command, payload, **_kwargs: (
                    {"next_action": "export_deployed_agent_audit_logs"}
                    if command == "deployed-agent-service-decision"
                    else {"next_action": "list_deployed_agent_activity"}
                ),
            ),
            patch.object(
                deployed_agent_service,
                "list_deployed_agent_activity",
                new=AsyncMock(),
            ) as activity_mock,
        ):
            with self.assertRaises(HTTPException) as raised:
                await deployed_agent_service.export_deployed_agent_audit_logs(
                    deployed_agent_id="dagent_1",
                    current_user={"user_id": "user-1"},
                    owner_workspace_id="workspace-1",
                    limit=25,
                )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("unexpected next_action", str(raised.exception.detail))
        activity_mock.assert_not_awaited()

    def test_data_gate_accepts_canonical_delete_external_user_action(self):
        captured = {}

        def _fake_rust(command: str, payload: dict[str, object]):
            captured["command"] = command
            captured["payload"] = dict(payload)
            return {"next_action": "purge_deployed_agent_external_user_data"}

        with patch.object(
            deployed_agent_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=_fake_rust,
        ):
            result = deployed_agent_service._enforce_deployed_agent_data_decision(
                "delete_external_user_data",
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                deployed_agent_id="dagent_1",
                current_user={
                    "user_id": "user-1",
                    "workspace_access": {"workspace-1": {"role": "owner"}},
                },
                external_user_id="customer-1",
                channel_key="telegram",
            )

        self.assertEqual(result["next_action"], "purge_deployed_agent_external_user_data")
        self.assertEqual(captured["command"], "deployed-data-decision")
        self.assertEqual(captured["payload"]["operation"], "delete_external_user_data")
        self.assertEqual(captured["payload"]["external_user_id"], "customer-1")
        self.assertEqual(captured["payload"]["channel_key"], "telegram")

    async def test_delete_external_user_data_wrong_data_action_blocks_before_purge(self):
        with (
            patch(
                "server_modules.deployed_agent_service.auth_module.enforce_workspace_access",
                return_value="workspace-1",
            ),
            patch(
                "server_modules.deployed_agent_service.control_plane_repository.get_workspace_by_id",
                new=AsyncMock(return_value={"id": "workspace-1", "tenant_id": "tenant-1"}),
            ),
            patch(
                "server_modules.deployed_agent_service.control_plane_repository.get_deployed_agent_by_id",
                new=AsyncMock(return_value={"id": "dagent_1", "deployment_state": "live", "metadata": {}}),
            ),
            patch.object(
                deployed_agent_service.rust_runtime_kernel_client,
                "run_runtime_kernel_enforced",
                side_effect=lambda command, payload, **_kwargs: (
                    {"next_action": "delete_deployed_agent_external_user_data"}
                    if command == "deployed-agent-service-decision"
                    else {"next_action": "verify_privacy_request"}
                ),
            ),
            patch(
                "server_modules.deployed_agent_service.external_user_privacy_service.get_external_user_privacy_service",
            ) as privacy_service_mock,
        ):
            with self.assertRaises(HTTPException) as raised:
                await deployed_agent_service.delete_deployed_agent_external_user_data(
                    deployed_agent_id="dagent_1",
                    external_user_id="customer-1",
                    channel_key="telegram",
                    current_user={"user_id": "user-1"},
                    owner_workspace_id="workspace-1",
                    session_id="session-1",
                )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("unexpected next_action", str(raised.exception.detail))
        privacy_service_mock.return_value.purge_deployed_agent_external_user_data.assert_not_called()
