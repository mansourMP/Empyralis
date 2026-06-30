import unittest
from unittest import mock

from fastapi import HTTPException

from server_modules import workspace_admin_service
from server_modules.rust_runtime_kernel_client import RustKernelDecisionError


class WorkspaceAdminControlPlaneRustGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_invite_create_calls_rust_before_repository_mutation(self):
        with mock.patch.object(
            workspace_admin_service,
            "_enforce_owner_scope",
            return_value="ws-1",
        ), mock.patch.object(
            workspace_admin_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "control_plane_service_operation_allowed",
                "operation": "invite_create",
                "next_action": "apply_control_plane_write",
                "mutation_plan": {
                    "apply": True,
                    "next_action": "apply_control_plane_write",
                },
            },
        ) as rust_gate, mock.patch.object(
            workspace_admin_service.control_plane_repository,
            "create_workspace_invite",
            new=mock.AsyncMock(return_value={"id": "invite-1", "status": "pending"}),
        ) as create_invite:
            result = await workspace_admin_service.invite_workspace_member(
                "ws-1",
                {"user_id": "owner-1", "tenant_id": "tenant-1"},
                email="person@example.com",
                role="member",
            )

        rust_gate.assert_called_once()
        command, payload = rust_gate.call_args.args
        self.assertEqual(command, "control-plane-service-decision")
        self.assertEqual(payload["operation"], "invite_create")
        self.assertEqual(payload["record_type"], "workspace_member_invite")
        self.assertEqual(payload["workspace_id"], "ws-1")
        self.assertEqual(payload["tenant_id"], "tenant-1")
        self.assertEqual(payload["actor_id"], "owner-1")
        self.assertEqual(payload["target_status"], "pending")
        create_invite.assert_awaited_once_with(
            workspace_id="ws-1",
            email="person@example.com",
            role="member",
            invited_by_user_id="owner-1",
            metadata={"source": "workspace_admin_service"},
        )
        self.assertEqual(result["status"], "pending")

    async def test_invite_create_unexpected_next_action_blocks_repository_mutation(self):
        with mock.patch.object(
            workspace_admin_service,
            "_enforce_owner_scope",
            return_value="ws-1",
        ), mock.patch.object(
            workspace_admin_service.rust_runtime_kernel_client,
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
            workspace_admin_service.control_plane_repository,
            "create_workspace_invite",
            new=mock.AsyncMock(),
        ) as create_invite:
            with self.assertRaises(HTTPException) as raised:
                await workspace_admin_service.invite_workspace_member(
                    "ws-1",
                    {"user_id": "owner-1", "tenant_id": "tenant-1"},
                    email="person@example.com",
                    role="member",
                )

        self.assertEqual(raised.exception.status_code, 423)
        self.assertIn("unexpected next_action", str(raised.exception.detail["reason"]))
        create_invite.assert_not_awaited()

    async def test_member_role_update_unexpected_next_action_blocks_auth_mutation(self):
        with mock.patch.object(
            workspace_admin_service,
            "_enforce_owner_scope",
            return_value="ws-1",
        ), mock.patch.object(
            workspace_admin_service.control_plane_repository,
            "list_workspace_members",
            new=mock.AsyncMock(return_value=[]),
        ), mock.patch.object(
            workspace_admin_service,
            "_find_member",
            return_value={"user_id": "target-1", "role": "member"},
        ), mock.patch.object(
            workspace_admin_service,
            "_guard_last_owner",
            return_value=None,
        ), mock.patch.object(
            workspace_admin_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "control_plane_service_operation_allowed",
                "operation": "membership_update",
                "next_action": "apply_control_plane_destructive_write",
                "mutation_plan": {
                    "apply": True,
                    "next_action": "apply_control_plane_destructive_write",
                },
            },
        ), mock.patch.object(
            workspace_admin_service.auth_module,
            "upsert_workspace_membership",
            new=mock.AsyncMock(),
        ) as upsert_membership:
            with self.assertRaises(HTTPException) as raised:
                await workspace_admin_service.update_workspace_member_role(
                    "ws-1",
                    {"user_id": "owner-1", "tenant_id": "tenant-1"},
                    user_id="target-1",
                    role="viewer",
                )

        self.assertEqual(raised.exception.status_code, 423)
        self.assertIn("unexpected next_action", str(raised.exception.detail["reason"]))
        upsert_membership.assert_not_awaited()

    async def test_member_role_update_calls_rust_before_auth_mutation(self):
        with mock.patch.object(
            workspace_admin_service,
            "_enforce_owner_scope",
            return_value="ws-1",
        ), mock.patch.object(
            workspace_admin_service.control_plane_repository,
            "list_workspace_members",
            new=mock.AsyncMock(return_value=[]),
        ), mock.patch.object(
            workspace_admin_service,
            "_find_member",
            return_value={"user_id": "target-1", "role": "member"},
        ), mock.patch.object(
            workspace_admin_service,
            "_guard_last_owner",
            return_value=None,
        ), mock.patch.object(
            workspace_admin_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "membership_update_allowed",
                "operation": "membership_update",
                "mutation_plan": {"apply": True},
            },
        ) as rust_gate, mock.patch.object(
            workspace_admin_service.auth_module,
            "upsert_workspace_membership",
            new=mock.AsyncMock(
                return_value={
                    "workspace_id": "ws-1",
                    "user_id": "target-1",
                    "role": "viewer",
                }
            ),
        ) as upsert_membership:
            result = await workspace_admin_service.update_workspace_member_role(
                "ws-1",
                {"user_id": "owner-1", "tenant_id": "tenant-1"},
                user_id="target-1",
                role="viewer",
            )

        rust_gate.assert_called_once()
        command, payload = rust_gate.call_args.args
        self.assertEqual(command, "control-plane-service-decision")
        self.assertEqual(payload["operation"], "membership_update")
        self.assertEqual(payload["workspace_id"], "ws-1")
        self.assertEqual(payload["tenant_id"], "tenant-1")
        self.assertEqual(payload["actor_id"], "owner-1")
        self.assertEqual(payload["target_actor_id"], "target-1")
        self.assertEqual(payload["target_status"], "viewer")
        upsert_membership.assert_awaited_once()
        self.assertEqual(result["role"], "viewer")

    async def test_member_remove_rust_denial_blocks_auth_mutation(self):
        denied = RustKernelDecisionError(
            {
                "ok": False,
                "decision": "block",
                "reason": "cannot_remove_self_membership",
                "operation": "membership_remove",
            },
            command="control-plane-service-decision",
        )
        with mock.patch.object(
            workspace_admin_service,
            "_enforce_owner_scope",
            return_value="ws-1",
        ), mock.patch.object(
            workspace_admin_service.control_plane_repository,
            "list_workspace_members",
            new=mock.AsyncMock(return_value=[]),
        ), mock.patch.object(
            workspace_admin_service,
            "_find_member",
            return_value={"user_id": "target-1", "role": "member"},
        ), mock.patch.object(
            workspace_admin_service,
            "_guard_last_owner",
            return_value=None,
        ), mock.patch.object(
            workspace_admin_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=denied,
        ), mock.patch.object(
            workspace_admin_service.auth_module,
            "remove_workspace_membership",
            new=mock.AsyncMock(),
        ) as remove_membership:
            with self.assertRaises(HTTPException) as raised:
                await workspace_admin_service.remove_workspace_member(
                    "ws-1",
                    {"user_id": "owner-1", "tenant_id": "tenant-1"},
                    user_id="target-1",
                )

        self.assertEqual(raised.exception.status_code, 423)
        remove_membership.assert_not_awaited()

    async def test_member_role_update_without_mutation_plan_blocks_auth_mutation(self):
        with mock.patch.object(
            workspace_admin_service,
            "_enforce_owner_scope",
            return_value="ws-1",
        ), mock.patch.object(
            workspace_admin_service.control_plane_repository,
            "list_workspace_members",
            new=mock.AsyncMock(return_value=[]),
        ), mock.patch.object(
            workspace_admin_service,
            "_find_member",
            return_value={"user_id": "target-1", "role": "member"},
        ), mock.patch.object(
            workspace_admin_service,
            "_guard_last_owner",
            return_value=None,
        ), mock.patch.object(
            workspace_admin_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "control_plane_service_operation_allowed",
                "operation": "membership_update",
            },
        ), mock.patch.object(
            workspace_admin_service.auth_module,
            "upsert_workspace_membership",
            new=mock.AsyncMock(),
        ) as upsert_membership:
            with self.assertRaises(HTTPException) as raised:
                await workspace_admin_service.update_workspace_member_role(
                    "ws-1",
                    {"user_id": "owner-1", "tenant_id": "tenant-1"},
                    user_id="target-1",
                    role="viewer",
                )

        self.assertEqual(raised.exception.status_code, 423)
        upsert_membership.assert_not_awaited()

    async def test_member_remove_unexpected_next_action_blocks_auth_mutation(self):
        with mock.patch.object(
            workspace_admin_service,
            "_enforce_owner_scope",
            return_value="ws-1",
        ), mock.patch.object(
            workspace_admin_service.control_plane_repository,
            "list_workspace_members",
            new=mock.AsyncMock(return_value=[]),
        ), mock.patch.object(
            workspace_admin_service,
            "_find_member",
            return_value={"user_id": "target-1", "role": "member"},
        ), mock.patch.object(
            workspace_admin_service,
            "_guard_last_owner",
            return_value=None,
        ), mock.patch.object(
            workspace_admin_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "control_plane_service_operation_allowed",
                "operation": "membership_remove",
                "next_action": "apply_control_plane_write",
                "mutation_plan": {
                    "apply": True,
                    "next_action": "apply_control_plane_write",
                },
            },
        ), mock.patch.object(
            workspace_admin_service.auth_module,
            "remove_workspace_membership",
            new=mock.AsyncMock(),
        ) as remove_membership:
            with self.assertRaises(HTTPException) as raised:
                await workspace_admin_service.remove_workspace_member(
                    "ws-1",
                    {"user_id": "owner-1", "tenant_id": "tenant-1"},
                    user_id="target-1",
                )

        self.assertEqual(raised.exception.status_code, 423)
        self.assertIn("unexpected next_action", str(raised.exception.detail["reason"]))
        remove_membership.assert_not_awaited()

    async def test_invite_revoke_calls_rust_before_repository_mutation(self):
        invite_record = {"id": "invite-1", "status": "pending"}
        with mock.patch.object(
            workspace_admin_service,
            "_enforce_owner_scope",
            return_value="ws-1",
        ), mock.patch.object(
            workspace_admin_service.control_plane_repository,
            "get_workspace_invite",
            new=mock.AsyncMock(return_value=invite_record),
        ), mock.patch.object(
            workspace_admin_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "control_plane_service_operation_allowed",
                "operation": "invite_revoke",
                "next_action": "apply_control_plane_destructive_write",
                "mutation_plan": {
                    "apply": True,
                    "next_action": "apply_control_plane_destructive_write",
                },
            },
        ) as rust_gate, mock.patch.object(
            workspace_admin_service.control_plane_repository,
            "revoke_workspace_invite",
            new=mock.AsyncMock(return_value={"id": "invite-1", "status": "revoked"}),
        ) as revoke_invite:
            result = await workspace_admin_service.revoke_workspace_invite(
                "ws-1",
                {"user_id": "owner-1", "tenant_id": "tenant-1"},
                invite_id="invite-1",
            )

        rust_gate.assert_called_once()
        command, payload = rust_gate.call_args.args
        self.assertEqual(command, "control-plane-service-decision")
        self.assertEqual(payload["operation"], "invite_revoke")
        self.assertEqual(payload["workspace_id"], "ws-1")
        self.assertEqual(payload["tenant_id"], "tenant-1")
        self.assertEqual(payload["actor_id"], "owner-1")
        self.assertEqual(payload["target_status"], "pending")
        revoke_invite.assert_awaited_once_with("invite-1")
        self.assertEqual(result["status"], "revoked")

    async def test_invite_revoke_rust_denial_for_accepted_invite_blocks_repository_mutation(self):
        denied = RustKernelDecisionError(
            {
                "ok": False,
                "decision": "block",
                "reason": "invite_already_accepted",
                "operation": "invite_revoke",
            },
            command="control-plane-service-decision",
        )
        with mock.patch.object(
            workspace_admin_service,
            "_enforce_owner_scope",
            return_value="ws-1",
        ), mock.patch.object(
            workspace_admin_service.control_plane_repository,
            "get_workspace_invite",
            new=mock.AsyncMock(return_value={"id": "invite-1", "status": "accepted"}),
        ), mock.patch.object(
            workspace_admin_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=denied,
        ), mock.patch.object(
            workspace_admin_service.control_plane_repository,
            "revoke_workspace_invite",
            new=mock.AsyncMock(),
        ) as revoke_invite:
            with self.assertRaises(HTTPException) as raised:
                await workspace_admin_service.revoke_workspace_invite(
                    "ws-1",
                    {"user_id": "owner-1", "tenant_id": "tenant-1"},
                    invite_id="invite-1",
                )

        self.assertEqual(raised.exception.status_code, 409)
        revoke_invite.assert_not_awaited()

    async def test_invite_revoke_existing_revoked_record_skips_repository_mutation(self):
        invite_record = {"id": "invite-1", "status": "revoked"}
        with mock.patch.object(
            workspace_admin_service,
            "_enforce_owner_scope",
            return_value="ws-1",
        ), mock.patch.object(
            workspace_admin_service.control_plane_repository,
            "get_workspace_invite",
            new=mock.AsyncMock(return_value=invite_record),
        ), mock.patch.object(
            workspace_admin_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "control_plane_service_operation_allowed",
                "operation": "invite_revoke",
                "next_action": "return_existing_control_plane_record",
                "mutation_plan": {
                    "apply": False,
                    "next_action": "return_existing_control_plane_record",
                },
            },
        ), mock.patch.object(
            workspace_admin_service.control_plane_repository,
            "revoke_workspace_invite",
            new=mock.AsyncMock(),
        ) as revoke_invite:
            result = await workspace_admin_service.revoke_workspace_invite(
                "ws-1",
                {"user_id": "owner-1", "tenant_id": "tenant-1"},
                invite_id="invite-1",
            )

        revoke_invite.assert_not_awaited()
        self.assertEqual(result["status"], "revoked")

    async def test_provider_credential_rust_denial_blocks_vault_write(self):
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
            workspace_admin_service,
            "_enforce_owner_scope",
            return_value="ws-1",
        ), mock.patch.object(
            workspace_admin_service,
            "_normalize_provider_id",
            return_value="anthropic",
        ), mock.patch.object(
            workspace_admin_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=denied,
        ), mock.patch.object(
            workspace_admin_service.connectors_actions,
            "create_vault_credential",
            new=mock.AsyncMock(),
        ) as create_credential:
            with self.assertRaises(HTTPException) as raised:
                await workspace_admin_service.upsert_workspace_provider_credential(
                    "ws-1",
                    {"user_id": "owner-1", "tenant_id": "tenant-1"},
                    provider="anthropic",
                    api_key="sk-test",
                )

        self.assertEqual(raised.exception.status_code, 423)
        create_credential.assert_not_awaited()
