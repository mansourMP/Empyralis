import unittest
from unittest.mock import patch

from fastapi import HTTPException

from server_modules import runtime_runtime_api


class RuntimeSessionApiRustGateTests(unittest.TestCase):
    @staticmethod
    def _current_user():
        return {
            "auth_type": "api_key",
            "role": "owner",
            "is_admin": False,
            "user_id": "owner-1",
            "workspace_access": {
                "default": {
                    "workspace_id": "default",
                    "tenant_id": "default",
                    "role": "owner",
                    "tenant_role": "owner",
                }
            },
        }

    def test_hardware_execute_gate_calls_rust_session_api(self) -> None:
        calls = []

        def fake_rust(command, payload, **kwargs):
            calls.append((command, payload, kwargs))
            return {
                "ok": True,
                "decision": "allow",
                "reason": "runtime_session_api_policy_satisfied",
                "operation": payload["operation"],
                "next_action": "execute_hardware_action",
                "audit_visibility": "standard",
                "approval_required": False,
                "cacheable": False,
            }

        with patch.object(
            runtime_runtime_api.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=fake_rust,
        ):
            decision = runtime_runtime_api._enforce_runtime_session_api_decision(
                operation="hardware_action_execute",
                tenant_id="default",
                workspace_id="default",
                current_user=self._current_user(),
                action_id="screenshot.capture",
                runtime_target="empyralis_cloud_computer",
                runtime_id="hrs-1",
                session_token="hrs-1",
                timeout_seconds=30,
            )

        self.assertEqual(decision["decision"], "allow")
        self.assertEqual(calls[0][0], "runtime-session-api-decision")
        self.assertEqual(calls[0][1]["operation"], "hardware_action_execute")
        self.assertEqual(calls[0][1]["tenant_id"], "default")
        self.assertEqual(calls[0][1]["workspace_id"], "default")
        self.assertEqual(calls[0][1]["operator_role"], "owner")
        self.assertTrue(calls[0][1]["operator_approved"])
        self.assertEqual(calls[0][1]["action_id"], "screenshot.capture")
        self.assertEqual(calls[0][1]["runtime_target"], "empyralis_cloud_computer")

    def test_hardware_action_gate_translates_rust_block_to_http_403(self) -> None:
        with patch.object(
            runtime_runtime_api.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=runtime_runtime_api.rust_runtime_kernel_client.RustKernelDecisionError(
                {
                    "ok": False,
                    "decision": "block",
                    "reason": "hardware_action_entitlement_missing",
                    "operation": "hardware_action_execute",
                    "next_action": "stop_runtime_api_request",
                    "audit_visibility": "security",
                    "approval_required": False,
                    "cacheable": False,
                },
                command="runtime-session-api-decision",
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                runtime_runtime_api._enforce_runtime_session_api_decision(
                    operation="hardware_action_execute",
                    tenant_id="default",
                    workspace_id="default",
                    current_user=self._current_user(),
                    action_id="screenshot.capture",
                    runtime_target="empyralis_cloud_computer",
                )

        self.assertEqual(raised.exception.status_code, 403)

    def test_runtime_task_claim_gate_calls_rust_without_current_user(self) -> None:
        calls = []

        def fake_rust(command, payload, **kwargs):
            calls.append((command, payload, kwargs))
            return {
                "ok": True,
                "decision": "allow",
                "reason": "runtime_session_api_policy_satisfied",
                "operation": payload["operation"],
                "next_action": "claim_runtime_task",
                "audit_visibility": "standard",
                "approval_required": False,
                "cacheable": False,
            }

        with patch.object(
            runtime_runtime_api.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=fake_rust,
        ):
            decision = runtime_runtime_api._enforce_runtime_session_api_decision(
                operation="runtime_task_claim",
                tenant_id="default",
                workspace_id="default",
                runtime_id="worker-1",
                instance_id="inst",
                session_token="sess",
                runtime_target="local",
                required_capabilities=["shell.execute"],
            )

        self.assertEqual(decision["decision"], "allow")
        self.assertEqual(calls[0][0], "runtime-session-api-decision")
        self.assertEqual(calls[0][1]["operation"], "runtime_task_claim")
        self.assertEqual(calls[0][1]["runtime_id"], "worker-1")
        self.assertEqual(calls[0][1]["instance_id"], "inst")
        self.assertEqual(calls[0][1]["session_token"], "sess")
        self.assertEqual(calls[0][1]["operator_role"], "runtime")
        self.assertEqual(calls[0][1]["required_capabilities"], ["shell.execute"])

    def test_self_hosted_command_enqueue_gate_passes_command_shape(self) -> None:
        calls = []

        def fake_rust(command, payload, **kwargs):
            calls.append((command, payload, kwargs))
            return {
                "ok": True,
                "decision": "allow",
                "reason": "runtime_session_api_policy_satisfied",
                "operation": payload["operation"],
                "next_action": "enqueue_self_hosted_command",
                "audit_visibility": "standard",
                "approval_required": False,
                "cacheable": False,
            }

        with patch.object(
            runtime_runtime_api.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=fake_rust,
        ):
            decision = runtime_runtime_api._enforce_runtime_session_api_decision(
                operation="self_hosted_command_enqueue",
                tenant_id="default",
                workspace_id="default",
                current_user=self._current_user(),
                runtime_id="rprof-1",
                node_id="node-1",
                agent_id="agent-1",
                command_type="runtime_action",
                command_payload_present=True,
            )

        self.assertEqual(decision["decision"], "allow")
        self.assertEqual(calls[0][0], "runtime-session-api-decision")
        self.assertEqual(calls[0][1]["operation"], "self_hosted_command_enqueue")
        self.assertEqual(calls[0][1]["runtime_id"], "rprof-1")
        self.assertEqual(calls[0][1]["node_id"], "node-1")
        self.assertEqual(calls[0][1]["agent_id"], "agent-1")
        self.assertEqual(calls[0][1]["command_type"], "runtime_action")
        self.assertTrue(calls[0][1]["command_payload"])

    def test_runtime_register_gate_passes_runtime_identity_and_capabilities(self) -> None:
        calls = []

        def fake_rust(command, payload, **kwargs):
            calls.append((command, payload, kwargs))
            return {
                "ok": True,
                "decision": "allow",
                "reason": "runtime_session_api_policy_satisfied",
                "operation": payload["operation"],
                "next_action": "register_runtime",
                "audit_visibility": "standard",
                "approval_required": False,
                "cacheable": False,
            }

        with patch.object(
            runtime_runtime_api.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=fake_rust,
        ):
            decision = runtime_runtime_api._enforce_runtime_session_api_decision(
                operation="runtime_register",
                tenant_id="default",
                workspace_id="default",
                runtime_id="worker-1",
                runtime_type="local_companion",
                runtime_role="specialist",
                instance_id="inst",
                required_capabilities=["shell.execute"],
            )

        self.assertEqual(decision["decision"], "allow")
        self.assertEqual(calls[0][0], "runtime-session-api-decision")
        self.assertEqual(calls[0][1]["operation"], "runtime_register")
        self.assertEqual(calls[0][1]["runtime_id"], "worker-1")
        self.assertEqual(calls[0][1]["runtime_type"], "local_companion")
        self.assertEqual(calls[0][1]["runtime_role"], "specialist")
        self.assertEqual(calls[0][1]["capabilities"], ["shell.execute"])

    def test_runtime_lifecycle_gate_passes_verified_session_state(self) -> None:
        calls = []

        def fake_rust(command, payload, **kwargs):
            calls.append((command, payload, kwargs))
            return {
                "ok": True,
                "decision": "allow",
                "reason": "runtime_session_api_policy_satisfied",
                "operation": payload["operation"],
                "next_action": "start_runtime_session",
                "audit_visibility": "standard",
                "approval_required": False,
                "cacheable": False,
            }

        with patch.object(
            runtime_runtime_api.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=fake_rust,
        ):
            runtime_runtime_api._enforce_runtime_session_api_decision(
                operation="runtime_start",
                tenant_id="default",
                workspace_id="default",
                runtime_id="worker-1",
                instance_id="inst",
                session_token="sess",
                runtime_session_valid=True,
                run_id="run-1",
            )

        self.assertEqual(calls[0][1]["operation"], "runtime_start")
        self.assertEqual(calls[0][1]["runtime_id"], "worker-1")
        self.assertEqual(calls[0][1]["session_token"], "sess")
        self.assertTrue(calls[0][1]["runtime_session_valid"])
        self.assertEqual(calls[0][1]["run_id"], "run-1")

    def test_run_hard_kill_gate_passes_run_and_runtime_ids(self) -> None:
        calls = []

        def fake_rust(command, payload, **kwargs):
            calls.append((command, payload, kwargs))
            return {
                "ok": True,
                "decision": "allow",
                "reason": "runtime_session_api_policy_satisfied",
                "operation": payload["operation"],
                "next_action": "hard_kill_runtime_run",
                "audit_visibility": "standard",
                "approval_required": False,
                "cacheable": False,
            }

        with patch.object(
            runtime_runtime_api.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=fake_rust,
        ):
            runtime_runtime_api._enforce_runtime_session_api_decision(
                operation="run_hard_kill",
                tenant_id="default",
                workspace_id="default",
                current_user=self._current_user(),
                run_id="run-1",
                runtime_id="worker-1",
            )

        self.assertEqual(calls[0][0], "runtime-session-api-decision")
        self.assertEqual(calls[0][1]["operation"], "run_hard_kill")
        self.assertEqual(calls[0][1]["run_id"], "run-1")
        self.assertEqual(calls[0][1]["runtime_id"], "worker-1")


if __name__ == "__main__":
    unittest.main()
