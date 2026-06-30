import unittest
from unittest.mock import patch

from server_modules import gateway_pairing_service


class GatewayPairingServiceRustGateTests(unittest.TestCase):
    def test_pairing_bootstrap_accepts_allow_gateway_service_operation(self) -> None:
        with (
            patch(
                "server_modules.gateway_pairing_service.rust_runtime_kernel_client.gateway_service_decision",
                return_value={
                    "ok": True,
                    "decision": "allow",
                    "reason": "gateway_service_operation_allowed",
                    "operation": "pairing_bootstrap",
                    "next_action": "allow_gateway_service_operation",
                },
            ) as rust_mock,
            patch(
                "server_modules.gateway_pairing_service.gateway_state_repository.create_pairing_intent",
                return_value={"pairing_token": "pair-1"},
            ) as create_mock,
        ):
            result = gateway_pairing_service.create_gateway_pairing_intent(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                user_id="user-1",
                trace_id="trace-1",
            )

        payload = rust_mock.call_args.kwargs
        self.assertEqual(payload["operation"], "pairing_bootstrap")
        self.assertEqual(payload["workspace_id"], "ws-1")
        create_mock.assert_called_once()
        self.assertEqual(result["pairing_token"], "pair-1")

    def test_pairing_bootstrap_blocks_wrong_rust_action_before_repository_write(self) -> None:
        with (
            patch(
                "server_modules.gateway_pairing_service.rust_runtime_kernel_client.gateway_service_decision",
                return_value={
                    "ok": True,
                    "decision": "allow",
                    "reason": "gateway_service_operation_allowed",
                    "operation": "pairing_bootstrap",
                    "next_action": "dispatch_gateway_operation",
                },
            ),
            patch(
                "server_modules.gateway_pairing_service.gateway_state_repository.create_pairing_intent",
                side_effect=AssertionError("should not create pairing intent"),
            ) as create_mock,
        ):
            with self.assertRaises(ValueError) as raised:
                gateway_pairing_service.create_gateway_pairing_intent(
                    tenant_id="tenant-1",
                    workspace_id="ws-1",
                    user_id="user-1",
                    trace_id="trace-1",
                )

        self.assertIn("unexpected next_action", str(raised.exception))
        create_mock.assert_not_called()

    def test_register_gateway_pairing_bootstrap_accepts_allow_gateway_service_operation(self) -> None:
        pairing = {
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "user_id": "user-1",
        }
        with (
            patch(
                "server_modules.gateway_pairing_service.gateway_state_repository.get_pairing_intent_by_token",
                return_value=pairing,
            ),
            patch(
                "server_modules.gateway_pairing_service.rust_runtime_kernel_client.gateway_service_decision",
                return_value={
                    "ok": True,
                    "decision": "allow",
                    "reason": "gateway_service_operation_allowed",
                    "operation": "pairing_bootstrap",
                    "next_action": "allow_gateway_service_operation",
                },
            ) as rust_mock,
            patch(
                "server_modules.gateway_pairing_service.gateway_state_repository.register_gateway_from_pairing",
                return_value={
                    "tenant_id": "tenant-1",
                    "workspace_id": "ws-1",
                    "user_id": "user-1",
                    "gateway_id": "gw-1",
                    "device_id": "device-1",
                    "gateway_token": "gtok-1",
                },
            ),
            patch(
                "server_modules.gateway_pairing_service.auth.ensure_local_gateway_device_link",
                return_value={"device_id": "device-1", "workspace_id": "ws-1", "status": "linked", "trust_state": "verified"},
            ),
            patch(
                "server_modules.gateway_pairing_service.gateway_state_repository.sync_gateway_registration_identity",
                return_value={"gateway_id": "gw-1", "device_id": "device-1", "tenant_id": "tenant-1", "workspace_id": "ws-1", "user_id": "user-1"},
            ),
            patch("server_modules.gateway_pairing_service.activity_ledger_service._run_coro_sync", return_value=None),
        ):
            result = gateway_pairing_service.register_gateway(
                pairing_token="pair-1",
                device_id="device-1",
                gateway_id="gw-1",
                trace_id="trace-1",
            )

        payload = rust_mock.call_args.kwargs
        self.assertEqual(payload["operation"], "pairing_bootstrap")
        self.assertEqual(payload["workspace_id"], "ws-1")
        self.assertEqual(result["gateway_id"], "gw-1")

    def test_register_gateway_blocks_wrong_rust_action_before_repository_write(self) -> None:
        pairing = {
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "user_id": "user-1",
        }
        with (
            patch(
                "server_modules.gateway_pairing_service.gateway_state_repository.get_pairing_intent_by_token",
                return_value=pairing,
            ),
            patch(
                "server_modules.gateway_pairing_service.rust_runtime_kernel_client.gateway_service_decision",
                return_value={
                    "ok": True,
                    "decision": "allow",
                    "reason": "gateway_service_operation_allowed",
                    "operation": "pairing_bootstrap",
                    "next_action": "dispatch_gateway_operation",
                },
            ),
            patch(
                "server_modules.gateway_pairing_service.gateway_state_repository.register_gateway_from_pairing",
                side_effect=AssertionError("should not register gateway"),
            ) as register_mock,
        ):
            with self.assertRaises(ValueError) as raised:
                gateway_pairing_service.register_gateway(
                    pairing_token="pair-1",
                    device_id="device-1",
                    gateway_id="gw-1",
                    trace_id="trace-1",
                )

        self.assertIn("unexpected next_action", str(raised.exception))
        register_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
