import unittest
from unittest.mock import patch

from server_modules.hardware_runtime_adapters import self_hosted_node_adapter


class HardwareSelfHostedRustRuntimeTests(unittest.TestCase):
    def test_self_hosted_runtime_action_gate_uses_real_attachment_facts(self) -> None:
        calls = []

        def fake_rust(command, payload, **kwargs):
            calls.append((command, payload, kwargs))
            return {
                "ok": True,
                "decision": "allow",
                "reason": "runtime_action_self_hosted_allowed",
                "operation": "execute_runtime_action",
                "next_action": "execute_self_hosted_runtime_action",
                "runtime_action": "run_command",
                "approval_required": False,
                "cacheable": False,
                "audit_visibility": "security",
            }

        with patch.object(
            self_hosted_node_adapter.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=fake_rust,
        ):
            decision = self_hosted_node_adapter.enforce_self_hosted_runtime_action_decision(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                user_id="user-1",
                action_id="shell.exec",
                arguments={"command": "printf hello", "password": "not-forwarded"},
                runtime_session={"session_id": "hrs-self"},
                run_id="run-1",
                thread_id="thread-1",
                attachment={
                    "runtime_node_id": "node-1",
                    "active_session_count": 1,
                    "max_concurrent_sessions": 4,
                },
            )

        self.assertEqual(decision["decision"], "allow")
        self.assertEqual(calls[0][0], "runtime-action-decision")
        self.assertEqual(calls[0][1]["runtime_session_binding"], "self_hosted_agent")
        self.assertEqual(calls[0][1]["studio_agent_mode"], "self_hosted")
        self.assertEqual(calls[0][1]["connector_id"], "shell")
        self.assertEqual(calls[0][1]["action_id"], "exec")
        self.assertEqual(calls[0][1]["command"], "printf hello")
        self.assertTrue(calls[0][1]["self_hosted_node_gate_passed"])
        self.assertFalse(calls[0][1]["self_hosted_node_concurrency_exceeded"])
        self.assertNotIn("password", calls[0][1])

    def test_self_hosted_runtime_action_gate_wrong_next_action_blocks(self) -> None:
        def fake_rust(_command, _payload, **_kwargs):
            return {
                "ok": True,
                "decision": "allow",
                "reason": "runtime_action_self_hosted_allowed",
                "operation": "execute_runtime_action",
                "next_action": "execute_cloud_runtime_action",
                "runtime_action": "run_command",
                "approval_required": False,
                "cacheable": False,
                "audit_visibility": "security",
            }

        with patch.object(
            self_hosted_node_adapter.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=fake_rust,
        ):
            with self.assertRaisesRegex(RuntimeError, "unexpected_next_action:execute_cloud_runtime_action"):
                self_hosted_node_adapter.enforce_self_hosted_runtime_action_decision(
                    tenant_id="tenant-1",
                    workspace_id="ws-1",
                    user_id="user-1",
                    action_id="shell.exec",
                    arguments={"command": "printf hello"},
                    runtime_session={"session_id": "hrs-self"},
                    run_id="run-1",
                    thread_id="thread-1",
                    attachment={
                        "runtime_node_id": "node-1",
                        "active_session_count": 1,
                        "max_concurrent_sessions": 4,
                    },
                )

    def test_self_hosted_concurrency_fact_blocks_when_attachment_at_capacity(self) -> None:
        self.assertTrue(
            self_hosted_node_adapter.self_hosted_concurrency_exceeded(
                {"active_session_count": 2, "max_concurrent_sessions": 2}
            )
        )


if __name__ == "__main__":
    unittest.main()
