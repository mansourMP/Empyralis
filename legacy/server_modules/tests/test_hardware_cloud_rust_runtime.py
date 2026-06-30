import unittest
from unittest.mock import patch

from server_modules.hardware_runtime_adapters import cloud_computer_adapter


class CloudRuntimeActionRustGateTests(unittest.TestCase):
    def test_cloud_runtime_action_gate_accepts_canonical_next_action(self) -> None:
        calls = []

        def fake_rust(command, payload, **kwargs):
            calls.append((command, payload, kwargs))
            return {
                "ok": True,
                "decision": "allow",
                "operation": "execute_runtime_action",
                "next_action": "execute_cloud_runtime_action",
            }

        with patch.object(
            cloud_computer_adapter.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=fake_rust,
        ):
            decision = cloud_computer_adapter.enforce_virtual_computer_decision(
                "action_payload",
                {
                    "studio_agent_mode": "cloud_computer",
                    "runtime_session_binding": "cloud_computer_agent",
                    "connector_id": "browser",
                    "action_id": "navigate",
                    "deployed_agent_id": "agent-1",
                    "tenant_id": "tenant-1",
                    "workspace_id": "workspace-1",
                    "runtime_session_id": "session-1",
                    "run_id": "run-1",
                    "thread_id": "thread-1",
                    "url": "https://example.com",
                },
            )

        self.assertEqual(decision["next_action"], "execute_cloud_runtime_action")
        self.assertEqual(calls[0][0], "virtual-computer-decision")
        self.assertEqual(calls[0][1]["operation"], "action_payload")

    def test_cloud_runtime_action_gate_wrong_next_action_blocks(self) -> None:
        with patch.object(
            cloud_computer_adapter.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "operation": "execute_runtime_action",
                "next_action": "execute_self_hosted_runtime_action",
            },
        ):
            with self.assertRaisesRegex(RuntimeError, "unexpected_next_action:execute_self_hosted_runtime_action"):
                cloud_computer_adapter.enforce_virtual_computer_decision(
                    "action_payload",
                    {
                        "studio_agent_mode": "cloud_computer",
                        "runtime_session_binding": "cloud_computer_agent",
                        "connector_id": "browser",
                        "action_id": "navigate",
                        "deployed_agent_id": "agent-1",
                        "tenant_id": "tenant-1",
                        "workspace_id": "workspace-1",
                        "runtime_session_id": "session-1",
                        "run_id": "run-1",
                        "thread_id": "thread-1",
                        "url": "https://example.com",
                    },
                )


if __name__ == "__main__":
    unittest.main()
