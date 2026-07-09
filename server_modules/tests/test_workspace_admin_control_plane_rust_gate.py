import unittest
from unittest import mock

from fastapi import HTTPException

from server_modules import workspace_admin_service
from server_modules.rust_runtime_kernel_client import RustKernelDecisionError


class WorkspaceAdminControlPlaneRustGateTests(unittest.IsolatedAsyncioTestCase):
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
