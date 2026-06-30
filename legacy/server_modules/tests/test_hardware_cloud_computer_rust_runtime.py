import unittest
from unittest.mock import AsyncMock, patch

from server_modules.hardware_runtime_adapters import cloud_computer_adapter


class HardwareCloudComputerRustRuntimeTests(unittest.TestCase):
    def test_virtual_computer_decision_gate_uses_enforced_client(self) -> None:
        calls = []

        def fake_rust(command, payload, **kwargs):
            calls.append((command, payload, kwargs))
            return {
                "ok": True,
                "decision": "allow",
                "reason": "virtual_action_payload_allowed",
                "operation": payload["operation"],
                "next_action": "validate_computer_use_action_payload",
                "approval_required": False,
                "cacheable": False,
                "audit_visibility": "security",
            }

        with patch.object(
            cloud_computer_adapter.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=fake_rust,
        ):
            decision = cloud_computer_adapter.enforce_virtual_computer_decision(
                "action_payload",
                {
                    "runtime_choice": "virtual_browser",
                    "computer_action": "open_url",
                    "url": "https://example.com",
                },
            )

        self.assertEqual(decision["decision"], "allow")
        self.assertEqual(calls[0][0], "virtual-computer-decision")
        self.assertEqual(calls[0][1]["operation"], "action_payload")
        self.assertEqual(calls[0][1]["runtime_choice"], "virtual_browser")
        self.assertEqual(calls[0][1]["computer_action"], "open_url")

    def test_virtual_session_state_gate_accepts_canonical_next_action(self) -> None:
        with patch.object(
            cloud_computer_adapter.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "virtual_session_state_allowed",
                "operation": "session_state",
                "next_action": "assert_virtual_session_active",
                "approval_required": False,
                "cacheable": False,
                "audit_visibility": "standard",
            },
        ):
            decision = cloud_computer_adapter.enforce_virtual_computer_decision(
                "session_state",
                {
                    "runtime_choice": "virtual_browser",
                    "state": "running",
                    "session_operation": "terminate",
                    "session_id": "hrs-cloud",
                },
            )

        self.assertEqual(decision["next_action"], "assert_virtual_session_active")

    def test_virtual_session_state_gate_wrong_next_action_blocks(self) -> None:
        with patch.object(
            cloud_computer_adapter.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "virtual_session_state_allowed",
                "operation": "session_state",
                "next_action": "validate_computer_use_action_payload",
                "approval_required": False,
                "cacheable": False,
                "audit_visibility": "standard",
            },
        ):
            with self.assertRaisesRegex(RuntimeError, "unexpected_next_action:validate_computer_use_action_payload"):
                cloud_computer_adapter.enforce_virtual_computer_decision(
                    "session_state",
                    {
                        "runtime_choice": "virtual_browser",
                        "state": "running",
                        "session_operation": "terminate",
                        "session_id": "hrs-cloud",
                    },
                )

    def test_virtual_action_payload_projects_only_policy_fields(self) -> None:
        payload = cloud_computer_adapter._virtual_action_payload(
            {
                "command": "printf hello",
                "password": "not-forwarded",
                "keys": ["Meta", "L"],
            }
        )

        self.assertEqual(payload["command"], "printf hello")
        self.assertEqual(payload["key_count"], 2)
        self.assertNotIn("password", payload)


class HardwareCloudComputerRustRuntimePathTests(unittest.IsolatedAsyncioTestCase):
    async def test_virtual_computer_block_stops_before_provider_resolution(self) -> None:
        blocked = cloud_computer_adapter.rust_runtime_kernel_client.RustKernelDecisionError(
            {
                "ok": False,
                "decision": "block",
                "reason": "virtual_action_unsupported",
                "operation": "action_payload",
                "next_action": "stop_virtual_computer",
                "approval_required": False,
                "cacheable": False,
                "audit_visibility": "security",
            },
            command="virtual-computer-decision",
        )
        registry = type("_Registry", (), {"resolve": AsyncMock()})()

        with patch.object(
            cloud_computer_adapter.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=blocked,
        ):
            with self.assertRaises(cloud_computer_adapter.rust_runtime_kernel_client.RustKernelDecisionError):
                await cloud_computer_adapter.execute_cloud_computer_action(
                    tenant_id="tenant-1",
                    workspace_id="ws-1",
                    user_id="user-1",
                    action_id="browser.open",
                    capability_id="browser_automation.interactive",
                    arguments={"url": "https://example.com"},
                    runtime_session={"session_id": "hrs-cloud"},
                    run_id="run-1",
                    trace_id="trace-1",
                    thread_id="thread-1",
                    request_id="req-1",
                    trace_context=None,
                    require_approval=False,
                    runtime_access_mode="full_access",
                    cost_metadata=None,
                    tool_call_id="req-1",
                    runtime_registry_getter=lambda: registry,
                )

        registry.resolve.assert_not_called()

    async def test_virtual_identity_context_block_stops_before_provider_resolution(self) -> None:
        registry = type("_Registry", (), {"resolve": AsyncMock()})()

        def fake_rust(command, payload, **kwargs):
            if payload["operation"] == "identity_context":
                raise cloud_computer_adapter.rust_runtime_kernel_client.RustKernelDecisionError(
                    {
                        "ok": False,
                        "decision": "block",
                        "reason": "cookie_reuse_override_blocked",
                        "operation": "identity_context",
                        "next_action": "stop_virtual_computer",
                        "approval_required": False,
                        "cacheable": False,
                        "audit_visibility": "security",
                    },
                    command=command,
                )
            next_action_by_operation = {
                "action_payload": "validate_computer_use_action_payload",
                "network_policy": "assert_network_browser_security",
                "provider_admission": "admit_virtual_computer_provider",
                "isolation_profile": "build_isolation_profile",
                "cost_quota": "build_cost_quota_profile",
            }
            return {
                "ok": True,
                "decision": "allow",
                "reason": "virtual_computer_policy_satisfied",
                "operation": payload["operation"],
                "next_action": next_action_by_operation[payload["operation"]],
                "approval_required": False,
                "cacheable": False,
                "audit_visibility": "security",
            }

        with patch.object(
            cloud_computer_adapter.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=fake_rust,
        ):
            with self.assertRaises(cloud_computer_adapter.rust_runtime_kernel_client.RustKernelDecisionError):
                await cloud_computer_adapter.execute_cloud_computer_action(
                    tenant_id="tenant-1",
                    workspace_id="ws-1",
                    user_id="user-1",
                    action_id="browser.open",
                    capability_id="browser_automation.interactive",
                    arguments={"url": "https://example.com"},
                    runtime_session={"session_id": "hrs-cloud"},
                    run_id="run-1",
                    trace_id="trace-1",
                    thread_id="thread-1",
                    request_id="req-1",
                    trace_context=None,
                    require_approval=False,
                    runtime_access_mode="full_access",
                    cost_metadata={"identity_context": {"allow_cookie_reuse": True}},
                    tool_call_id="req-1",
                    runtime_registry_getter=lambda: registry,
                )

        registry.resolve.assert_not_called()


if __name__ == "__main__":
    unittest.main()
