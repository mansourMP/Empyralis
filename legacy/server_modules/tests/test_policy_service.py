import unittest
from unittest.mock import patch

from server_modules import no_provider_service
from server_modules import policy_service
from server_modules import runtime_policy


class PolicyServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._rust_patch = patch.object(
            policy_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=self._fake_rust_authorize,
        )
        self._rust_patch.start()

    def tearDown(self) -> None:
        self._rust_patch.stop()

    def _fake_rust_authorize(self, command: str, payload: dict, **kwargs):
        self.assertIn(command, {"authorize-request", "authorize-execution", "execution-plan"})
        self.assertTrue(kwargs.get("allow_approval_required"))
        policy = dict(payload.get("policy") or {})
        capability = str(payload.get("capability") or "").strip()
        raw_command = str(payload.get("command") or "").strip()
        policy_context = dict((payload.get("payload") or {}).get("policy_context") or {})
        runtime_policy_context = dict((payload.get("payload") or {}).get("runtime_policy_context") or {})
        reason = str((payload.get("payload") or {}).get("reason") or "").strip()
        if command == "execution-plan":
            compact = raw_command.lower()
            if any(marker in compact for marker in ("rm -rf /", "shutdown", "reboot")):
                raise policy_service.rust_runtime_kernel_client.RustKernelDecisionError(
                    {
                        "ok": False,
                        "decision": "block",
                        "reason": "destructive_command_blocked",
                        "approval_required": False,
                        "next_action": "deny_tool_execution",
                        "decision_id": "rkd_execution_plan_blocked",
                    },
                    command=command,
                )
            if any(marker in compact for marker in ("curl ", "wget ", "git clone", "npm install", "pip install", "gh ", "brew install")):
                return {
                    "ok": True,
                    "decision": "require_approval",
                    "reason": "network_execution_requires_approval",
                    "approval_required": True,
                    "next_action": "request_tool_execution_approval",
                    "decision_id": "rkd_execution_plan_approval",
                }
            return {
                "ok": True,
                "decision": "allow",
                "reason": "execution_plan_ready",
                "approval_required": False,
                "next_action": "allow_tool_execution",
                "decision_id": "rkd_execution_plan_allow",
            }
        requested_capabilities = {
            str(item or "").strip().lower()
            for item in list(policy_context.get("requested_capability_ids") or [])
            if str(item or "").strip()
        }
        denied_capabilities = {
            str(item or "").strip().lower()
            for item in list(policy_context.get("workspace_denied_capabilities") or [])
            if str(item or "").strip()
        }
        allowed_capabilities = {
            str(item or "").strip().lower()
            for item in list(policy_context.get("workspace_allowed_capabilities") or [])
            if str(item or "").strip()
        }
        workspace_role = str(policy_context.get("workspace_role") or "").strip().lower()
        if policy_context.get("disabled_capability"):
            raise policy_service.rust_runtime_kernel_client.RustKernelDecisionError(
                {
                    "ok": False,
                    "decision": "block",
                    "reason": "capability_disabled_by_policy_scope",
                    "approval_required": False,
                    "decision_id": "rkd_disabled",
                },
                command="authorize-request",
            )
        if "*" in denied_capabilities or requested_capabilities.intersection(denied_capabilities):
            raise policy_service.rust_runtime_kernel_client.RustKernelDecisionError(
                {
                    "ok": False,
                    "decision": "block",
                    "reason": "workspace_capability_denied",
                    "approval_required": False,
                    "decision_id": "rkd_workspace_denied",
                },
                command="authorize-request",
            )
        if allowed_capabilities and "*" not in allowed_capabilities and workspace_role != "owner" and any(
            capability_id not in allowed_capabilities for capability_id in requested_capabilities
        ):
            raise policy_service.rust_runtime_kernel_client.RustKernelDecisionError(
                {
                    "ok": False,
                    "decision": "block",
                    "reason": "workspace_capability_not_granted",
                    "approval_required": False,
                    "decision_id": "rkd_workspace_not_granted",
                },
                command="authorize-request",
            )
        if policy_context.get("tool_disabled"):
            raise policy_service.rust_runtime_kernel_client.RustKernelDecisionError(
                {
                    "ok": False,
                    "decision": "block",
                    "reason": "tool_disabled",
                    "approval_required": False,
                    "decision_id": "rkd_tool_disabled",
                },
                command="authorize-request",
            )
        if policy_context.get("unsupported_capability"):
            raise policy_service.rust_runtime_kernel_client.RustKernelDecisionError(
                {
                    "ok": False,
                    "decision": "block",
                    "reason": "blocked_unsupported_capability",
                    "approval_required": False,
                    "decision_id": "rkd_unsupported",
                },
                command="authorize-request",
            )
        if policy_context.get("blocked_raw_shell_command"):
            raise policy_service.rust_runtime_kernel_client.RustKernelDecisionError(
                {
                    "ok": False,
                    "decision": "block",
                    "reason": "blocked_raw_shell_command",
                    "approval_required": False,
                    "next_action": "deny_tool_execution",
                    "decision_id": "rkd_raw_shell_blocked",
                },
                command=command,
            )
        if policy_context.get("action_blocked") and not policy_context.get("uses_capability_path") and not policy_context.get("safe_raw_shell_command"):
            raise policy_service.rust_runtime_kernel_client.RustKernelDecisionError(
                {
                    "ok": False,
                    "decision": "block",
                    "reason": "blocked_by_action_policy",
                    "approval_required": False,
                    "next_action": "deny_tool_execution",
                    "decision_id": "rkd_action_blocked",
                },
                command=command,
            )
        if policy_context.get("cloud_target") and policy_context.get("critical") and policy_context.get("block_cloud_critical"):
            raise policy_service.rust_runtime_kernel_client.RustKernelDecisionError(
                {
                    "ok": False,
                    "decision": "block",
                    "reason": "blocked_cloud_critical",
                    "approval_required": False,
                    "next_action": "deny_tool_execution",
                    "decision_id": "rkd_cloud_critical",
                },
                command=command,
            )
        if str(runtime_policy_context.get("execution_decision") or "").strip().lower() == "deny":
            raise policy_service.rust_runtime_kernel_client.RustKernelDecisionError(
                {
                    "ok": False,
                    "decision": "block",
                    "reason": str(runtime_policy_context.get("reason") or "runtime_policy_blocked"),
                    "approval_required": False,
                    "next_action": "deny_tool_execution",
                    "decision_id": "rkd_runtime_policy_blocked",
                },
                command=command,
            )
        if command == "authorize-execution" and any(marker in raw_command.lower() for marker in ("rm -rf /", "shutdown", "reboot")):
            raise policy_service.rust_runtime_kernel_client.RustKernelDecisionError(
                {
                    "ok": False,
                    "decision": "block",
                    "reason": "destructive_command_blocked",
                    "approval_required": False,
                    "next_action": "deny_tool_execution",
                    "decision_id": "rkd_execution_plan_blocked",
                },
                command=command,
            )
        if policy_context.get("action_approval_required") and not policy_context.get("safe_raw_shell_command"):
            return {
                "ok": True,
                "decision": "require_approval",
                "reason": "approval_required_by_action_policy",
                "approval_required": True,
                "next_action": "request_tool_execution_approval",
                "decision_id": "rkd_action_requires_approval",
            }
        if str(runtime_policy_context.get("execution_decision") or "").strip().lower() == "require_confirmation":
            return {
                "ok": True,
                "decision": "require_approval",
                "reason": str(runtime_policy_context.get("reason") or "runtime_policy_requires_approval"),
                "approval_required": True,
                "next_action": "request_tool_execution_approval",
                "decision_id": "rkd_runtime_policy_requires_approval",
            }
        if capability in set(policy.get("blocked_capabilities") or []):
            raise policy_service.rust_runtime_kernel_client.RustKernelDecisionError(
                {
                    "ok": False,
                    "decision": "block",
                    "reason": reason or "capability_blocked_by_policy",
                    "approval_required": False,
                    "decision_id": "rkd_policy_block",
                },
                command="authorize-request",
            )
        if capability in set(policy.get("approval_required_capabilities") or []):
            return {
                "ok": True,
                "decision": "require_approval",
                "reason": reason or "approval_required",
                "approval_required": True,
                "next_action": "request_tool_execution_approval",
                "decision_id": "rkd_policy_approval",
            }
        return {
            "ok": True,
            "decision": "allow",
            "reason": reason or "authorization_allowed",
            "approval_required": False,
            "next_action": "allow_tool_execution",
            "decision_id": "rkd_policy_allow",
        }

    def test_resolve_runtime_policy_mode_prefers_registered_runtime_policy(self) -> None:
        registry = runtime_policy.LOCAL_WORKER_REGISTRY
        previous = dict(registry)
        try:
            registry.clear()
            registry["runtime-1"] = {"policy_mode": "trusted_full_access"}
            resolved = policy_service.resolve_runtime_policy_mode(
                {
                    "execution_target_matching_runtime_ids": ["runtime-1"],
                },
                selected_target="local_companion",
            )
        finally:
            registry.clear()
            registry.update(previous)

        self.assertEqual(resolved["policy_mode"], "trusted_full_access")
        self.assertEqual(resolved["runtime_id"], "runtime-1")
        self.assertEqual(resolved["source"], "runtime_registration")

    def test_evaluate_tool_policy_decision_requires_confirmation_for_safe_raw_shell_command(self) -> None:
        evaluation = policy_service.evaluate_tool_policy_decision(
            tool_id="shell.execute",
            trust_mode="guarded",
            target="local_companion",
            metadata={
                "pack_inputs": {
                    "operations": [
                        {
                            "tool": "shell.execute",
                            "command": "echo hello world",
                        }
                    ]
                }
            },
            capability_ids=[],
        )

        self.assertEqual(evaluation["execution_decision"], "require_confirmation")
        self.assertEqual(evaluation["reason"], "Capability contract requires approval before execution.")
        self.assertTrue(evaluation["safe_raw_shell_command"])
        self.assertEqual(evaluation["rust_authorization_command"], "authorize-execution")
        self.assertEqual(evaluation["rust_execution_plan_command"], "execution-plan")

    def test_evaluate_tool_policy_decision_allows_known_read_only_system_probe(self) -> None:
        evaluation = policy_service.evaluate_tool_policy_decision(
            tool_id="shell.execute",
            trust_mode="guarded",
            target="local_companion",
            metadata={
                "pack_inputs": {
                    "operations": [
                        {
                            "tool": "shell.execute",
                            "command": no_provider_service.local_system_info_shell_command(),
                        }
                    ]
                }
            },
            capability_ids=[],
        )

        self.assertEqual(evaluation["execution_decision"], "allow")
        self.assertTrue(evaluation["safe_raw_shell_command"])
        self.assertTrue(evaluation["known_read_only_system_probe"])
        self.assertEqual(evaluation["classification"]["action_type"], "read")
        self.assertEqual(evaluation["decision_id"], "rkd_policy_allow")
        self.assertEqual(evaluation["rust_authorization_command"], "authorize-execution")
        self.assertEqual(evaluation["rust_execution_plan_command"], "execution-plan")

    def test_evaluate_tool_policy_decision_capability_backed_shell_command_keeps_authorize_request(self) -> None:
        evaluation = policy_service.evaluate_tool_policy_decision(
            tool_id="shell.execute",
            trust_mode="guarded",
            target="local_companion",
            metadata={},
            capability_ids=["stack.status"],
        )

        self.assertEqual(evaluation["rust_authorization_command"], "authorize-request")
        self.assertIsNone(evaluation["rust_execution_plan_command"])

    def test_compute_tool_policy_precheck_allows_known_read_only_system_probe(self) -> None:
        precheck = policy_service.compute_tool_policy_precheck(
            {
                "metadata": {
                    "execution_target_selected": "local_companion",
                    "pack_inputs": {
                        "operations": [
                            {
                                "tool": "shell.execute",
                                "command": no_provider_service.local_system_info_shell_command(),
                            }
                        ]
                    },
                }
            },
            derive_browser_automation_policy_fn=lambda context: {},
            predict_tool_ids_for_context_fn=lambda context: ["shell.execute"],
            build_skill_contract_from_metadata_fn=lambda metadata, tool_ids, policy_mode, target: {
                "policy_mode": "observe",
                "undeclared_tools": [],
                "declared_runtime_tools": ["shell.execute"],
            },
            predict_capability_ids_for_context_fn=lambda context: [],
            apply_agent_machine_bypass_to_tool_policy_evaluation_fn=lambda evaluation: evaluation,
        )

        self.assertEqual(precheck["blocked_count"], 0)
        self.assertEqual(precheck["approval_required_count"], 0)
        self.assertEqual(precheck["allow_count"], 1)
        self.assertEqual(precheck["allowed"], ["shell.execute"])

    def test_compute_tool_policy_precheck_aggregates_items_and_counts(self) -> None:
        precheck = policy_service.compute_tool_policy_precheck(
            {"metadata": {"execution_target_selected": "local_companion"}},
            derive_browser_automation_policy_fn=lambda context: {
                "profile": "authenticated_interactive",
                "requires_approval": True,
                "session_profiles": ["default"],
                "session_profile_count": 1,
                "interactive_actions": ["click"],
                "privileged_actions": [],
            },
            predict_tool_ids_for_context_fn=lambda context: ["browser_automation.interactive", "filesystem.read_write"],
            build_skill_contract_from_metadata_fn=lambda metadata, tool_ids, policy_mode, target: {
                "policy_mode": "observe",
                "undeclared_tools": [],
                "declared_runtime_tools": ["browser_automation.interactive", "filesystem.read_write"],
            },
            predict_capability_ids_for_context_fn=lambda context: [],
            apply_agent_machine_bypass_to_tool_policy_evaluation_fn=lambda evaluation: evaluation,
        )

        self.assertEqual(precheck["blocked_count"], 0)
        self.assertEqual(precheck["approval_required_count"], 2)
        self.assertEqual(precheck["allow_count"], 0)
        self.assertIn("browser_automation.interactive", precheck["approval_required"])
        self.assertIn("filesystem.read_write", precheck["approval_required"])

    def test_approval_required_for_direct_tool_uses_capability_policy(self) -> None:
        requires_approval = policy_service.approval_required_for_direct_tool(
            "slack",
            "post_message",
            {},
            [{"id": "slack", "approval_required_actions": ["post_message"]}],
            compact_text=lambda value: str(value or "").strip().lower(),
            http_request_requires_approval=lambda method, url: False,
        )

        self.assertTrue(requires_approval)

    def test_hardware_shell_action_skips_approval_for_known_read_only_probe(self) -> None:
        requires_approval = policy_service.approval_required_for_direct_tool(
            "hardware",
            "action",
            {
                "runtime_target": "user_device_gateway",
                "action": "shell.execute",
                "arguments": {"command": no_provider_service.local_system_info_shell_command()},
            },
            [],
            compact_text=lambda value: str(value or "").strip().lower(),
            http_request_requires_approval=lambda method, url: False,
        )

        self.assertFalse(requires_approval)

    def test_hardware_shell_action_skips_approval_for_simple_echo_input_payload(self) -> None:
        requires_approval = policy_service.approval_required_for_direct_tool(
            "hardware",
            "action",
            {"input": '{"action": "shell", "command": "echo hello from hardware"}'},
            [],
            compact_text=lambda value: str(value or "").strip().lower(),
            http_request_requires_approval=lambda method, url: False,
        )

        self.assertFalse(requires_approval)

    def test_hardware_run_action_skips_approval_for_simple_echo_input_payload(self) -> None:
        requires_approval = policy_service.approval_required_for_direct_tool(
            "hardware",
            "action",
            {"input": '{"action": "run", "runtime_t": "cloud", "command": "echo hello from hardware"}'},
            [],
            compact_text=lambda value: str(value or "").strip().lower(),
            http_request_requires_approval=lambda method, url: False,
        )

        self.assertFalse(requires_approval)

    def test_hardware_run_action_skips_approval_for_simple_echo_payload_field(self) -> None:
        requires_approval = policy_service.approval_required_for_direct_tool(
            "hardware",
            "action",
            {"input": '{"action": "run", "runtime_t": "cloud", "payload": "echo hello from hardware"}'},
            [],
            compact_text=lambda value: str(value or "").strip().lower(),
            http_request_requires_approval=lambda method, url: False,
        )

        self.assertFalse(requires_approval)

    def test_hardware_shell_action_skips_approval_for_simple_echo_action_data_payload(self) -> None:
        requires_approval = policy_service.approval_required_for_direct_tool(
            "hardware",
            "action",
            {"input": '{"action": "shell", "action_data": "{\\"command\\": \\"echo hello from hardware\\"}"}'},
            [],
            compact_text=lambda value: str(value or "").strip().lower(),
            http_request_requires_approval=lambda method, url: False,
        )

        self.assertFalse(requires_approval)

    def test_hardware_shell_action_skips_approval_for_simple_echo_input_params_payload(self) -> None:
        requires_approval = policy_service.approval_required_for_direct_tool(
            "hardware",
            "action",
            {"input": '{"action": "shell", "params": {"command": "echo hello from hardware"}}'},
            [],
            compact_text=lambda value: str(value or "").strip().lower(),
            http_request_requires_approval=lambda method, url: False,
        )

        self.assertFalse(requires_approval)

    def test_hardware_shell_action_still_requires_approval_for_unknown_command(self) -> None:
        requires_approval = policy_service.approval_required_for_direct_tool(
            "hardware",
            "action",
            {
                "runtime_target": "user_device_gateway",
                "action": "shell.execute",
                "arguments": {"command": "uname -a && whoami"},
            },
            [],
            compact_text=lambda value: str(value or "").strip().lower(),
            http_request_requires_approval=lambda method, url: False,
        )

        self.assertTrue(requires_approval)

    def test_evaluate_tool_policy_decision_blocks_shell_execute_by_default(self) -> None:
        evaluation = policy_service.evaluate_tool_policy_decision(
            tool_id="shell.execute",
            trust_mode="guarded",
            target="local_companion",
            metadata={},
            capability_ids=[],
        )
        self.assertEqual(evaluation["execution_decision"], "deny")

    def test_evaluate_tool_policy_decision_requires_confirmation_for_filesystem_write(self) -> None:
        evaluation = policy_service.evaluate_tool_policy_decision(
            tool_id="filesystem.read_write",
            trust_mode="guarded",
            target="local_companion",
            metadata={},
            capability_ids=[],
        )
        self.assertEqual(evaluation["execution_decision"], "require_confirmation")
        self.assertEqual(evaluation["reason"], "approval_required_by_action_policy")

    def test_evaluate_tool_policy_decision_requires_confirmation_for_browser_automation(self) -> None:
        evaluation = policy_service.evaluate_tool_policy_decision(
            tool_id="browser_automation.interactive",
            trust_mode="guarded",
            target="local_companion",
            metadata={},
            capability_ids=[],
        )
        self.assertEqual(evaluation["execution_decision"], "require_confirmation")

    def test_evaluate_tool_policy_decision_requires_confirmation_for_channel_send(self) -> None:
        evaluation = policy_service.evaluate_tool_policy_decision(
            tool_id="send_message",
            trust_mode="guarded",
            target="local_companion",
            metadata={},
            capability_ids=[],
        )
        self.assertEqual(evaluation["execution_decision"], "require_confirmation")

    def test_evaluate_tool_policy_decision_denies_payment_transfer_without_approval(self) -> None:
        evaluation = policy_service.evaluate_tool_policy_decision(
            tool_id="transfer_funds",
            trust_mode="guarded",
            target="local_companion",
            metadata={},
            capability_ids=[],
        )
        self.assertEqual(evaluation["execution_decision"], "deny")

    def test_evaluate_tool_policy_decision_allows_read_only_connector_action(self) -> None:
        evaluation = policy_service.evaluate_tool_policy_decision(
            tool_id="connector.action.read",
            trust_mode="guarded",
            target="local_companion",
            metadata={},
            capability_ids=[],
        )
        self.assertEqual(evaluation["execution_decision"], "allow")

    def test_workflow_app_tool_aliases_map_to_rust_capability_classes(self) -> None:
        self.assertEqual(policy_service._rust_capability_for_tool_policy("http_request"), "network_access")
        self.assertEqual(policy_service._rust_capability_for_tool_policy("draft_email"), "communication_draft")
        self.assertEqual(policy_service._rust_capability_for_tool_policy("document_create"), "external_write")
        self.assertEqual(policy_service._rust_capability_for_tool_policy("spreadsheet_read"), "read_files")
        self.assertEqual(policy_service._rust_capability_for_tool_policy("connector.action.write"), "external_write")

    def test_evaluate_tool_policy_decision_emits_rejected_action_audit_event(self) -> None:
        with patch(
            "server_modules.security_audit_service.emit_security_audit_event"
        ) as emit_audit:
            evaluation = policy_service.evaluate_tool_policy_decision(
                tool_id="shell.execute",
                trust_mode="guarded",
                target="local_companion",
                metadata={"tenant_id": "tenant-1", "workspace_id": "ws-1", "run_id": "run-1"},
                capability_ids=[],
            )

        self.assertEqual(evaluation["execution_decision"], "deny")
        emit_audit.assert_called_once()
        self.assertEqual(emit_audit.call_args.kwargs.get("action"), "tool_policy.rejected")
        self.assertEqual(emit_audit.call_args.kwargs.get("status"), "blocked")

    def test_evaluate_tool_policy_decision_blocks_unexpected_rust_next_action(self) -> None:
        with patch.object(
            policy_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "authorization_allowed",
                "approval_required": False,
                "next_action": "request_tool_execution_approval",
                "decision_id": "rkd_wrong_action",
            },
        ):
            evaluation = policy_service.evaluate_tool_policy_decision(
                tool_id="connector.action.read",
                trust_mode="guarded",
                target="local_companion",
                metadata={},
                capability_ids=[],
            )

        self.assertEqual(evaluation["execution_decision"], "deny")
        self.assertEqual(evaluation["decision"], "blocked")
        self.assertEqual(evaluation["reason"], "unexpected_next_action:request_tool_execution_approval")


if __name__ == "__main__":
    unittest.main()
