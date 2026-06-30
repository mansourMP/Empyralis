import unittest
from unittest import mock

from fastapi import HTTPException

from server_modules import workspace_ai_route_service
from server_modules.rust_runtime_kernel_client import RustKernelDecisionError


class WorkspaceAiRouteControlPlaneRustGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_route_update_calls_rust_before_profile_mutation(self):
        with mock.patch.object(
            workspace_ai_route_service.connectors_core,
            "list_provider_profiles",
            new=mock.AsyncMock(
                return_value={
                    "items": [
                        {
                            "id": "profile-1",
                            "provider": "openai",
                            "label": "OpenAI",
                            "credential_id": "cred-1",
                            "auth_mode": "api_key",
                            "priority": 10,
                            "enabled": True,
                            "model": "gpt-4o",
                            "metadata": {},
                        }
                    ]
                }
            ),
        ), mock.patch.object(
            workspace_ai_route_service.provider_profiles,
            "provider_catalog_entry",
            return_value={
                "label": "OpenAI",
                "default_auth_mode": "api_key",
                "default_model": "gpt-4o",
            },
        ), mock.patch.object(
            workspace_ai_route_service.provider_profiles,
            "provider_requires_credential",
            return_value=True,
        ), mock.patch.object(
            workspace_ai_route_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "control_plane_service_operation_allowed",
                "operation": "workspace_ai_route_update",
                "mutation_plan": {
                    "apply": True,
                    "next_action": "apply_control_plane_write",
                },
            },
        ) as rust_gate, mock.patch.object(
            workspace_ai_route_service.connectors_core,
            "upsert_provider_profile",
            new=mock.AsyncMock(),
        ) as upsert_profile, mock.patch.object(
            workspace_ai_route_service,
            "build_workspace_ai_route_payload",
            new=mock.AsyncMock(return_value={"workspaceId": "ws-1"}),
        ):
            result = await workspace_ai_route_service.update_workspace_default_ai_route(
                workspace_id="ws-1",
                current_user={"user_id": "owner-1", "tenant_id": "tenant-1"},
                provider="openai",
                model_preset="pro",
            )

        rust_gate.assert_called_once()
        command, payload = rust_gate.call_args.args
        self.assertEqual(command, "control-plane-service-decision")
        self.assertEqual(payload["operation"], "workspace_ai_route_update")
        self.assertEqual(payload["record_type"], "workspace_ai_route")
        self.assertEqual(payload["tenant_id"], "tenant-1")
        self.assertEqual(payload["workspace_id"], "ws-1")
        self.assertEqual(payload["actor_id"], "owner-1")
        self.assertEqual(payload["target_status"], "user_api_key")
        self.assertIn("workspace_ai_route:ws-1:user_api_key:openai:pro", payload["idempotency_key"])
        upsert_profile.assert_awaited_once()
        self.assertEqual(result["workspaceId"], "ws-1")

    async def test_rust_denial_blocks_provider_profile_mutation(self):
        denied = RustKernelDecisionError(
            {
                "ok": False,
                "decision": "block",
                "reason": "billing_entitlement_missing",
                "operation": "workspace_ai_route_update",
            },
            command="control-plane-service-decision",
        )
        with mock.patch.object(
            workspace_ai_route_service.connectors_core,
            "list_provider_profiles",
            new=mock.AsyncMock(
                return_value={
                    "items": [
                        {
                            "id": "profile-1",
                            "provider": "openai",
                            "credential_id": "cred-1",
                            "auth_mode": "api_key",
                            "priority": 10,
                            "enabled": True,
                            "model": "gpt-4o",
                            "metadata": {},
                        }
                    ]
                }
            ),
        ), mock.patch.object(
            workspace_ai_route_service.provider_profiles,
            "provider_catalog_entry",
            return_value={"default_auth_mode": "api_key", "default_model": "gpt-4o"},
        ), mock.patch.object(
            workspace_ai_route_service.provider_profiles,
            "provider_requires_credential",
            return_value=True,
        ), mock.patch.object(
            workspace_ai_route_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=denied,
        ), mock.patch.object(
            workspace_ai_route_service.connectors_core,
            "upsert_provider_profile",
            new=mock.AsyncMock(),
        ) as upsert_profile:
            with self.assertRaises(HTTPException) as raised:
                await workspace_ai_route_service.update_workspace_default_ai_route(
                    workspace_id="ws-1",
                    current_user={"user_id": "owner-1", "tenant_id": "tenant-1"},
                    provider="openai",
                    model_preset="pro",
                )

        self.assertEqual(raised.exception.status_code, 423)
        upsert_profile.assert_not_awaited()

    async def test_unexpected_next_action_blocks_provider_profile_mutation(self):
        with mock.patch.object(
            workspace_ai_route_service.connectors_core,
            "list_provider_profiles",
            new=mock.AsyncMock(
                return_value={
                    "items": [
                        {
                            "id": "profile-1",
                            "provider": "openai",
                            "credential_id": "cred-1",
                            "auth_mode": "api_key",
                            "priority": 10,
                            "enabled": True,
                            "model": "gpt-4o",
                            "metadata": {},
                        }
                    ]
                }
            ),
        ), mock.patch.object(
            workspace_ai_route_service.provider_profiles,
            "provider_catalog_entry",
            return_value={"default_auth_mode": "api_key", "default_model": "gpt-4o"},
        ), mock.patch.object(
            workspace_ai_route_service.provider_profiles,
            "provider_requires_credential",
            return_value=True,
        ), mock.patch.object(
            workspace_ai_route_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "control_plane_service_operation_allowed",
                "operation": "workspace_ai_route_update",
                "mutation_plan": {
                    "apply": True,
                    "next_action": "persist_approval_decision",
                },
            },
        ), mock.patch.object(
            workspace_ai_route_service.connectors_core,
            "upsert_provider_profile",
            new=mock.AsyncMock(),
        ) as upsert_profile:
            with self.assertRaises(HTTPException) as raised:
                await workspace_ai_route_service.update_workspace_default_ai_route(
                    workspace_id="ws-1",
                    current_user={"user_id": "owner-1", "tenant_id": "tenant-1"},
                    provider="openai",
                    model_preset="pro",
                )

        self.assertEqual(raised.exception.status_code, 423)
        upsert_profile.assert_not_awaited()

    async def test_missing_mutation_plan_blocks_provider_profile_mutation(self):
        with mock.patch.object(
            workspace_ai_route_service.connectors_core,
            "list_provider_profiles",
            new=mock.AsyncMock(
                return_value={
                    "items": [
                        {
                            "id": "profile-1",
                            "provider": "openai",
                            "credential_id": "cred-1",
                            "auth_mode": "api_key",
                            "priority": 10,
                            "enabled": True,
                            "model": "gpt-4o",
                            "metadata": {},
                        }
                    ]
                }
            ),
        ), mock.patch.object(
            workspace_ai_route_service.provider_profiles,
            "provider_catalog_entry",
            return_value={"default_auth_mode": "api_key", "default_model": "gpt-4o"},
        ), mock.patch.object(
            workspace_ai_route_service.provider_profiles,
            "provider_requires_credential",
            return_value=True,
        ), mock.patch.object(
            workspace_ai_route_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "control_plane_service_operation_allowed",
                "operation": "workspace_ai_route_update",
            },
        ), mock.patch.object(
            workspace_ai_route_service.connectors_core,
            "upsert_provider_profile",
            new=mock.AsyncMock(),
        ) as upsert_profile:
            with self.assertRaises(HTTPException) as raised:
                await workspace_ai_route_service.update_workspace_default_ai_route(
                    workspace_id="ws-1",
                    current_user={"user_id": "owner-1", "tenant_id": "tenant-1"},
                    provider="openai",
                    model_preset="pro",
                )

        self.assertEqual(raised.exception.status_code, 423)
        upsert_profile.assert_not_awaited()
