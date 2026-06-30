import unittest
from unittest import mock

from fastapi import HTTPException

from server_modules import routes_workspaces
from server_modules.rust_runtime_kernel_client import RustKernelDecisionError


class RoutesWorkspacesControlPlaneRustGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_workspace_unexpected_next_action_blocks_repository_mutation(self):
        with mock.patch.object(
            routes_workspaces.auth_module,
            "get_authenticated_user_record",
            return_value={"id": "owner-1", "tenant_id": "tenant-1"},
        ), mock.patch.object(
            routes_workspaces.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "control_plane_service_operation_allowed",
                "operation": "workspace_create",
                "next_action": "apply_control_plane_destructive_write",
                "mutation_plan": {
                    "apply": True,
                    "next_action": "apply_control_plane_destructive_write",
                },
            },
        ), mock.patch.object(
            routes_workspaces.control_plane_repository,
            "create_workspace_for_user",
            new=mock.AsyncMock(),
        ) as create_workspace:
            with self.assertRaises(HTTPException) as raised:
                await routes_workspaces.create_workspace(
                    routes_workspaces.WorkspaceCreateRequest(
                        name="Acme",
                        workspace_type="team",
                        preferred_shell_profile="personal_shell",
                        default_route="/chat",
                    ),
                    current_user={"user_id": "owner-1", "tenant_id": "tenant-1"},
                )

        self.assertEqual(raised.exception.status_code, 423)
        self.assertIn("unexpected next_action", str(raised.exception.detail["reason"]))
        create_workspace.assert_not_awaited()

    async def test_create_workspace_calls_rust_before_repository_mutation(self):
        with mock.patch.object(
            routes_workspaces.auth_module,
            "get_authenticated_user_record",
            return_value={"id": "owner-1", "tenant_id": "tenant-1"},
        ), mock.patch.object(
            routes_workspaces.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "control_plane_service_operation_allowed",
                "operation": "workspace_create",
                "next_action": "apply_control_plane_write",
                "mutation_plan": {"apply": True, "next_action": "apply_control_plane_write"},
            },
        ) as rust_gate, mock.patch.object(
            routes_workspaces.control_plane_repository,
            "create_workspace_for_user",
            new=mock.AsyncMock(
                return_value={
                    "workspace_id": "ws-1",
                    "tenant_id": "tenant-1",
                    "name": "Acme",
                    "workspace_type": "team",
                    "metadata": {
                        "shell": {
                            "preferredProfile": "personal_shell",
                            "defaultRoute": "/w/ws-1/chat",
                            "setupCompleted": False,
                        }
                    },
                }
            ),
        ) as create_workspace:
            result = await routes_workspaces.create_workspace(
                routes_workspaces.WorkspaceCreateRequest(
                    name="Acme",
                    workspace_type="team",
                    preferred_shell_profile="personal_shell",
                    default_route="/chat",
                ),
                current_user={"user_id": "owner-1", "tenant_id": "tenant-1"},
            )

        rust_gate.assert_called_once()
        command, payload = rust_gate.call_args.args
        self.assertEqual(command, "control-plane-service-decision")
        self.assertEqual(payload["operation"], "workspace_create")
        self.assertEqual(payload["record_type"], "workspace")
        self.assertEqual(payload["tenant_id"], "tenant-1")
        self.assertEqual(payload["actor_id"], "owner-1")
        self.assertEqual(payload["target_status"], "team")
        create_workspace.assert_awaited_once()
        self.assertEqual(result["workspace"]["id"], "ws-1")

    async def test_update_workspace_rust_denial_blocks_repository_mutation(self):
        denied = RustKernelDecisionError(
            {
                "ok": False,
                "decision": "block",
                "reason": "workspace_access_denied",
                "operation": "workspace_update",
            },
            command="control-plane-service-decision",
        )
        with mock.patch.object(
            routes_workspaces.auth_module,
            "enforce_workspace_access",
            return_value="ws-1",
        ), mock.patch.object(
            routes_workspaces.auth_module,
            "workspace_tenant_id",
            return_value="tenant-1",
        ), mock.patch.object(
            routes_workspaces.auth_module,
            "get_authenticated_user_record",
            return_value={"id": "owner-1", "tenant_id": "tenant-1"},
        ), mock.patch.object(
            routes_workspaces.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=denied,
        ), mock.patch.object(
            routes_workspaces.control_plane_repository,
            "update_workspace_profile",
            new=mock.AsyncMock(),
        ) as update_workspace:
            with self.assertRaises(HTTPException) as raised:
                await routes_workspaces.update_workspace(
                    "ws-1",
                    routes_workspaces.WorkspaceUpdateRequest(name="New name"),
                    current_user={"user_id": "owner-1", "tenant_id": "tenant-1"},
                )

        self.assertEqual(raised.exception.status_code, 423)
        update_workspace.assert_not_awaited()

    async def test_update_workspace_without_rust_mutation_plan_blocks_repository_mutation(self):
        with mock.patch.object(
            routes_workspaces.auth_module,
            "enforce_workspace_access",
            return_value="ws-1",
        ), mock.patch.object(
            routes_workspaces.auth_module,
            "workspace_tenant_id",
            return_value="tenant-1",
        ), mock.patch.object(
            routes_workspaces.auth_module,
            "get_authenticated_user_record",
            return_value={"id": "owner-1", "tenant_id": "tenant-1"},
        ), mock.patch.object(
            routes_workspaces.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "control_plane_service_operation_allowed",
                "operation": "workspace_update",
            },
        ), mock.patch.object(
            routes_workspaces.control_plane_repository,
            "update_workspace_profile",
            new=mock.AsyncMock(),
        ) as update_workspace:
            with self.assertRaises(HTTPException) as raised:
                await routes_workspaces.update_workspace(
                    "ws-1",
                    routes_workspaces.WorkspaceUpdateRequest(name="New name"),
                    current_user={"user_id": "owner-1", "tenant_id": "tenant-1"},
                )

        self.assertEqual(raised.exception.status_code, 423)
        update_workspace.assert_not_awaited()

    async def test_transparency_settings_rust_denial_blocks_persistence(self):
        denied = RustKernelDecisionError(
            {
                "ok": False,
                "decision": "block",
                "reason": "owner_access_required",
                "operation": "transparency_settings_update",
            },
            command="control-plane-service-decision",
        )
        current_settings = mock.Mock()
        current_settings.to_dict.return_value = {"workspace_id": "ws-1", "default_mode": "compact"}
        with mock.patch.object(
            routes_workspaces.auth_module,
            "validate_csrf",
            return_value=None,
        ), mock.patch.object(
            routes_workspaces.auth_module,
            "enforce_workspace_access",
            return_value="ws-1",
        ), mock.patch.object(
            routes_workspaces.auth_module,
            "workspace_tenant_id",
            return_value="tenant-1",
        ), mock.patch.object(
            routes_workspaces.auth_module,
            "get_authenticated_user_record",
            return_value={"id": "owner-1", "tenant_id": "tenant-1"},
        ), mock.patch.object(
            routes_workspaces,
            "get_transparency_settings",
            return_value=current_settings,
        ), mock.patch.object(
            routes_workspaces.TransparencySettings,
            "from_dict",
            return_value=mock.Mock(),
        ), mock.patch.object(
            routes_workspaces.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=denied,
        ), mock.patch.object(
            routes_workspaces,
            "put_transparency_settings",
        ) as put_settings:
            with self.assertRaises(HTTPException) as raised:
                await routes_workspaces.workspace_transparency_settings_update(
                    "ws-1",
                    routes_workspaces.WorkspaceTransparencySettingsUpdateRequest(default_mode="expanded"),
                    request=mock.Mock(),
                    current_user={"user_id": "owner-1", "tenant_id": "tenant-1"},
                )

        self.assertEqual(raised.exception.status_code, 423)
        put_settings.assert_not_called()

    async def test_provider_credential_delete_rust_denial_blocks_service_delete(self):
        denied = RustKernelDecisionError(
            {
                "ok": False,
                "decision": "block",
                "reason": "secret_reference_write_requires_approval",
                "operation": "secret_reference_write",
            },
            command="control-plane-service-decision",
        )
        with mock.patch.object(
            routes_workspaces.auth_module,
            "validate_csrf",
            return_value=None,
        ), mock.patch.object(
            routes_workspaces.auth_module,
            "enforce_workspace_access",
            return_value="ws-1",
        ), mock.patch.object(
            routes_workspaces.auth_module,
            "workspace_tenant_id",
            return_value="tenant-1",
        ), mock.patch.object(
            routes_workspaces.auth_module,
            "get_authenticated_user_record",
            return_value={"id": "owner-1", "tenant_id": "tenant-1"},
        ), mock.patch.object(
            routes_workspaces.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=denied,
        ), mock.patch.object(
            routes_workspaces.workspace_admin_service,
            "delete_workspace_provider_credential",
            new=mock.AsyncMock(),
        ) as delete_credential:
            with self.assertRaises(HTTPException) as raised:
                await routes_workspaces.workspace_provider_credential_delete(
                    "ws-1",
                    routes_workspaces.WorkspaceProviderCredentialDeleteRequest(provider="anthropic"),
                    request=mock.Mock(),
                    current_user={"user_id": "owner-1", "tenant_id": "tenant-1"},
                )

        self.assertEqual(raised.exception.status_code, 423)
        delete_credential.assert_not_awaited()

    async def test_invite_create_rust_denial_blocks_invite_service(self):
        denied = RustKernelDecisionError(
            {
                "ok": False,
                "decision": "block",
                "reason": "owner_access_required",
                "operation": "invite_create",
            },
            command="control-plane-service-decision",
        )
        with mock.patch.object(
            routes_workspaces.auth_module,
            "enforce_workspace_access",
            return_value="ws-1",
        ), mock.patch.object(
            routes_workspaces.auth_module,
            "workspace_tenant_id",
            return_value="tenant-1",
        ), mock.patch.object(
            routes_workspaces.auth_module,
            "get_authenticated_user_record",
            return_value={"id": "owner-1", "tenant_id": "tenant-1"},
        ), mock.patch.object(
            routes_workspaces.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=denied,
        ), mock.patch.object(
            routes_workspaces.workspace_admin_service,
            "invite_workspace_member",
            new=mock.AsyncMock(),
        ) as invite_member:
            with self.assertRaises(HTTPException) as raised:
                await routes_workspaces.workspace_member_invites(
                    "ws-1",
                    routes_workspaces.WorkspaceInviteRequest(email="new@example.com", role="member"),
                    current_user={"user_id": "owner-1", "tenant_id": "tenant-1"},
                )

        self.assertEqual(raised.exception.status_code, 423)
        invite_member.assert_not_awaited()

    async def test_invite_create_unexpected_next_action_blocks_invite_service(self):
        with mock.patch.object(
            routes_workspaces.auth_module,
            "enforce_workspace_access",
            return_value="ws-1",
        ), mock.patch.object(
            routes_workspaces.auth_module,
            "workspace_tenant_id",
            return_value="tenant-1",
        ), mock.patch.object(
            routes_workspaces.auth_module,
            "get_authenticated_user_record",
            return_value={"id": "owner-1", "tenant_id": "tenant-1"},
        ), mock.patch.object(
            routes_workspaces.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "control_plane_service_operation_allowed",
                "operation": "invite_create",
                "next_action": "apply_control_plane_destructive_write",
                "mutation_plan": {
                    "apply": True,
                    "next_action": "apply_control_plane_destructive_write",
                },
            },
        ), mock.patch.object(
            routes_workspaces.workspace_admin_service,
            "invite_workspace_member",
            new=mock.AsyncMock(),
        ) as invite_member:
            with self.assertRaises(HTTPException) as raised:
                await routes_workspaces.workspace_member_invites(
                    "ws-1",
                    routes_workspaces.WorkspaceInviteRequest(email="new@example.com", role="member"),
                    current_user={"user_id": "owner-1", "tenant_id": "tenant-1"},
                )

        self.assertEqual(raised.exception.status_code, 423)
        self.assertIn("unexpected next_action", str(raised.exception.detail["reason"]))
        invite_member.assert_not_awaited()

    async def test_workspace_policy_update_rust_denial_blocks_policy_service(self):
        denied = RustKernelDecisionError(
            {
                "ok": False,
                "decision": "block",
                "reason": "workspace_access_denied",
                "operation": "workspace_policy_update",
            },
            command="control-plane-service-decision",
        )
        with mock.patch.object(
            routes_workspaces.auth_module,
            "enforce_workspace_access",
            return_value="ws-1",
        ), mock.patch.object(
            routes_workspaces.auth_module,
            "workspace_tenant_id",
            return_value="tenant-1",
        ), mock.patch.object(
            routes_workspaces.auth_module,
            "get_authenticated_user_record",
            return_value={"id": "owner-1", "tenant_id": "tenant-1"},
        ), mock.patch.object(
            routes_workspaces.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=denied,
        ), mock.patch.object(
            routes_workspaces.workspace_admin_service,
            "update_workspace_policies_payload",
            new=mock.AsyncMock(),
        ) as update_policies:
            with self.assertRaises(HTTPException) as raised:
                await routes_workspaces.workspace_policies_update(
                    "ws-1",
                    routes_workspaces.WorkspacePoliciesUpdateRequest(
                        capabilities={"shell": ["execute"]},
                    ),
                    current_user={"user_id": "owner-1", "tenant_id": "tenant-1"},
                )

        self.assertEqual(raised.exception.status_code, 423)
        update_policies.assert_not_awaited()

    async def test_provider_models_refresh_rust_denial_blocks_refresh_service(self):
        denied = RustKernelDecisionError(
            {
                "ok": False,
                "decision": "block",
                "reason": "quota_exceeded",
                "operation": "provider_models_refresh",
            },
            command="control-plane-service-decision",
        )
        with mock.patch.object(
            routes_workspaces.auth_module,
            "validate_csrf",
            return_value=None,
        ), mock.patch.object(
            routes_workspaces.auth_module,
            "enforce_workspace_access",
            return_value="ws-1",
        ), mock.patch.object(
            routes_workspaces.auth_module,
            "workspace_tenant_id",
            return_value="tenant-1",
        ), mock.patch.object(
            routes_workspaces.auth_module,
            "get_authenticated_user_record",
            return_value={"id": "owner-1", "tenant_id": "tenant-1"},
        ), mock.patch.object(
            routes_workspaces.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=denied,
        ), mock.patch.object(
            routes_workspaces.workspace_admin_service,
            "refresh_workspace_provider_models",
            new=mock.AsyncMock(),
        ) as refresh_models:
            with self.assertRaises(HTTPException) as raised:
                await routes_workspaces.workspace_provider_models_refresh(
                    "ws-1",
                    "anthropic",
                    request=mock.Mock(),
                    current_user={"user_id": "owner-1", "tenant_id": "tenant-1"},
                )

        self.assertEqual(raised.exception.status_code, 423)
        refresh_models.assert_not_awaited()
