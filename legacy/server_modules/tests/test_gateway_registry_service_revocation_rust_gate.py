import unittest
from unittest import mock

from server_modules import gateway_registry_service


class GatewayRegistryServiceRevocationRustGateTests(unittest.TestCase):
    def test_revoke_gateway_registration_revokes_auth_link_after_repository_revoke(self):
        order: list[str] = []
        registration = {
            "gateway_id": "gw-1",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "user_id": "user-1",
            "device_id": "device-1",
        }
        revoked = {
            **registration,
            "status": "revoked",
            "device_trust_state": "revoked",
        }
        with mock.patch.object(
            gateway_registry_service.gateway_state_repository,
            "get_gateway_registration",
            return_value=registration,
        ), mock.patch.object(
            gateway_registry_service.gateway_state_repository,
            "revoke_gateway_registration",
            side_effect=lambda **kwargs: order.append("repository_revoke") or revoked,
        ), mock.patch.object(
            gateway_registry_service.auth,
            "revoke_local_gateway_device_link",
            side_effect=lambda **kwargs: order.append("auth_revoke") or {"ok": True},
        ), mock.patch.object(
            gateway_registry_service,
            "gateway_registration_public_payload",
            return_value={"gateway_id": "gw-1", "status": "revoked"},
        ), mock.patch.object(
            gateway_registry_service,
            "gateway_scope_payload",
            return_value={"gateway_id": "gw-1"},
        ):
            payload = gateway_registry_service.revoke_gateway_registration(
                gateway_id="gw-1",
                tenant_id="tenant-1",
                workspace_id="ws-1",
                user_id="user-1",
                reason="owner revoked",
            )

        self.assertEqual(order, ["repository_revoke", "auth_revoke"])
        self.assertTrue(payload["mutation_plan"]["shutdown_live_connection"])
        self.assertTrue(payload["mutation_plan"]["mark_dedicated_workstation_revoked"])
        self.assertEqual(payload["mutation_plan"]["revocation_reason"], "owner revoked")

