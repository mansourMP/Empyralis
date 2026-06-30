import unittest
from unittest.mock import AsyncMock, patch

from server_modules import hardware_action_broker_service as broker


class HardwareActionBrokerRustRuntimeTests(unittest.TestCase):
    def test_cloud_runtime_action_gate_calls_rust_with_canonical_action(self) -> None:
        calls = []

        def fake_rust(command, payload, **kwargs):
            calls.append((command, payload, kwargs))
            return {
                "ok": True,
                "decision": "allow",
                "reason": "runtime_action_cloud_allowed",
                "operation": "execute_runtime_action",
                "next_action": "execute_cloud_runtime_action",
                "runtime_action": "open_url",
                "approval_required": False,
                "cacheable": False,
                "audit_visibility": "security",
            }

        with patch.object(
            broker.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=fake_rust,
        ):
            decision = broker._enforce_cloud_runtime_action_decision(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                user_id="user-1",
                action_id="browser.open",
                arguments={"url": "https://example.com", "password": "not-forwarded"},
                run_id="run-1",
                thread_id="thread-1",
                request_id="req-1",
                runtime_session_id="hrs-1",
            )

        self.assertEqual(decision["decision"], "allow")
        self.assertEqual(calls[0][0], "runtime-action-decision")
        self.assertEqual(calls[0][1]["runtime_session_binding"], "cloud_computer_agent")
        self.assertEqual(calls[0][1]["studio_agent_mode"], "cloud_computer")
        self.assertEqual(calls[0][1]["connector_id"], "browser")
        self.assertEqual(calls[0][1]["action_id"], "navigate")
        self.assertEqual(calls[0][1]["url"], "https://example.com")
        self.assertNotIn("password", calls[0][1])

    def test_cloud_runtime_action_gate_wrong_next_action_blocks(self) -> None:
        with patch.object(
            broker.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "runtime_action_cloud_allowed",
                "operation": "execute_runtime_action",
                "next_action": "execute_runtime_action",
                "runtime_action": "open_url",
                "approval_required": False,
                "cacheable": False,
                "audit_visibility": "security",
            },
        ):
            with self.assertRaises(RuntimeError) as raised:
                broker._enforce_cloud_runtime_action_decision(
                    tenant_id="tenant-1",
                    workspace_id="ws-1",
                    user_id="user-1",
                    action_id="browser.open",
                    arguments={"url": "https://example.com"},
                    run_id="run-1",
                    thread_id="thread-1",
                    request_id="req-1",
                    runtime_session_id="hrs-1",
                )

        self.assertEqual(str(raised.exception), "unexpected_next_action")


class HardwareActionBrokerRustRuntimePathTests(unittest.IsolatedAsyncioTestCase):
    async def test_cloud_runtime_action_block_stops_before_session_creation(self) -> None:
        blocked = broker.rust_runtime_kernel_client.RustKernelDecisionError(
            {
                "ok": False,
                "decision": "block",
                "reason": "runtime_action_not_supported",
                "operation": "execute_runtime_action",
                "next_action": "stop_runtime_action",
                "approval_required": False,
                "cacheable": False,
                "audit_visibility": "security",
            },
            command="runtime-action-decision",
        )
        create_session = AsyncMock()

        with patch.object(
            broker.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=blocked,
        ), patch.object(broker, "_create_runtime_session", create_session):
            with self.assertRaises(broker.rust_runtime_kernel_client.RustKernelDecisionError):
                await broker.execute_hardware_action(
                    tenant_id="tenant-1",
                    workspace_id="ws-1",
                    action_id="browser.open",
                    arguments={"url": "https://example.com"},
                    runtime_target="empyralis_cloud_computer",
                    run_id="run-1",
                    request_id="req-1",
                )

        create_session.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
