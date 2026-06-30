import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server_modules import routes_gateway


class RoutesGatewayRevocationPlanTests(unittest.TestCase):
    def test_revoke_gateway_route_honors_service_mutation_plan(self):
        app = FastAPI()
        app.include_router(routes_gateway.router, prefix="/api")
        app.dependency_overrides[routes_gateway.require_api_key] = lambda: {
            "user_id": "owner-1",
            "workspace_access": {
                "ws-1": {"workspace_id": "ws-1", "tenant_id": "tenant-1", "role": "owner"}
            },
        }
        with mock.patch.object(
            routes_gateway.gateway_state_repository,
            "get_gateway_registration",
            return_value={"gateway_id": "gw-1", "workspace_id": "ws-1"},
        ), mock.patch.object(
            routes_gateway,
            "enforce_workspace_access",
            return_value="ws-1",
        ), mock.patch.object(
            routes_gateway,
            "workspace_tenant_id",
            return_value="tenant-1",
        ), mock.patch.object(
            routes_gateway.gateway_registry_service,
            "revoke_gateway_registration",
            return_value={
                "gateway": {"gateway_id": "gw-1", "status": "revoked"},
                "scope": {"gateway_id": "gw-1"},
                "mutation_plan": {
                    "shutdown_live_connection": False,
                    "mark_dedicated_workstation_revoked": False,
                    "revocation_reason": "owner revoked",
                },
            },
        ), mock.patch.object(
            routes_gateway,
            "_shutdown_revoked_live_gateway_connection",
            new=mock.AsyncMock(return_value=True),
        ) as shutdown_live, mock.patch.object(
            routes_gateway.dedicated_workstation_setup_service,
            "mark_dedicated_workstation_revoked",
        ) as mark_revoked:
            client = TestClient(app)
            response = client.post(
                "/api/gateway/registrations/gw-1/revoke",
                json={"reason": "owner revoked"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["live_connection_shutdown"])
        shutdown_live.assert_not_awaited()
        mark_revoked.assert_not_called()
