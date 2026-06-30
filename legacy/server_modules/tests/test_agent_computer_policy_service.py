import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from server_modules import agent_computer_policy_service as policy


class AgentComputerPolicyServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._kernel_patch = patch.object(
            policy.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=self._fake_rust_kernel,
        )
        self._kernel_mock = self._kernel_patch.start()

    def tearDown(self) -> None:
        self._kernel_patch.stop()

    def _fake_rust_kernel(self, command: str, payload: dict, **kwargs):
        if command == "check-path-containment":
            path = str(payload.get("path") or "").rstrip("/")
            blocked_roots = [str(item or "").rstrip("/") for item in list(payload.get("blocked_roots") or [])]
            if any(path == root or path.startswith(f"{root}/") for root in blocked_roots if root):
                self._raise_rust_block("path_blocked_by_root", command=command)
            allowed_roots = [str(item or "").rstrip("/") for item in list(payload.get("allowed_roots") or [])]
            if any(path == root or path.startswith(f"{root}/") for root in allowed_roots if root):
                return {
                    "ok": True,
                    "decision": "allow",
                    "reason": "path_within_allowed_root",
                    "next_action": "allow_path_access",
                    "approval_required": False,
                }
            self._raise_rust_block("path_outside_allowed_roots", command=command)
        if command != "validate-policy":
            raise AssertionError(f"unexpected Rust command {command}")
        self.assertTrue(kwargs.get("allow_approval_required"))
        contract = dict(payload.get("policy") or {})
        capability = str(payload.get("capability") or "").strip()
        requested_domain = str(payload.get("requested_domain") or "").strip().lower()
        requested_path = str(payload.get("requested_path") or "").strip()
        allowed_domains = [str(item or "").strip().lower() for item in list(contract.get("domain_allowlist") or [])]
        if requested_domain and allowed_domains and not any(
            requested_domain == domain or requested_domain.endswith(f".{domain}")
            for domain in allowed_domains
            if domain
        ):
            self._raise_policy_block("domain_outside_policy_scope")
        blocked_scopes = [str(item or "").rstrip("/") for item in list(contract.get("blocked_filesystem_scope") or [])]
        if requested_path and any(
            requested_path == scope or requested_path.startswith(f"{scope}/")
            for scope in blocked_scopes
            if scope
        ):
            self._raise_policy_block("path_blocked_by_policy_scope")
        allowed_scopes = [
            str(item or "").rstrip("/")
            for item in list(contract.get("filesystem_scope") or [])
            if str(item or "").startswith(("/", "~"))
        ]
        if requested_path and allowed_scopes and not any(
            requested_path == scope or requested_path.startswith(f"{scope}/")
            for scope in allowed_scopes
            if scope
        ):
            self._raise_policy_block("path_outside_policy_scope")
        if capability in set(contract.get("blocked_capabilities") or []):
            self._raise_policy_block("capability_blocked_by_policy")
        if capability in set(contract.get("approval_required_capabilities") or []):
            return {
                "ok": True,
                "decision": "require_approval",
                "reason": "capability_requires_approval",
                "next_action": "request_agent_computer_approval",
                "approval_required": True,
            }
        if capability in set(contract.get("allowed_capabilities") or []):
            return {
                "ok": True,
                "decision": "allow",
                "reason": "capability_allowed_by_policy",
                "next_action": "allow_agent_computer_request",
                "approval_required": False,
            }
        self._raise_policy_block("default_deny")

    def _raise_policy_block(self, reason: str) -> None:
        self._raise_rust_block(reason, command="validate-policy")

    def _raise_rust_block(self, reason: str, *, command: str) -> None:
        raise policy.rust_runtime_kernel_client.RustKernelDecisionError(
            {
                "ok": False,
                "decision": "block",
                "reason": reason,
                "approval_required": False,
            },
            command=command,
        )

    def test_policy_owns_autonomy_mode_and_profile_should_reference_policy(self) -> None:
        contract = policy.build_default_agent_computer_policy(
            autonomy_mode="safe_autopilot",
            policy_id="policy-safe",
        )

        payload = contract.as_dict()
        self.assertEqual(payload["autonomy_mode"], "safe_autopilot")
        self.assertIn("policy_id", payload)
        self.assertNotIn("profile_mode", payload)

    def test_read_only_allows_reads_and_blocks_writes(self) -> None:
        contract = policy.build_default_agent_computer_policy(autonomy_mode="read_only")

        self.assertEqual(policy.decision_for_capability(contract, "browser.read"), "allow")
        self.assertEqual(policy.decision_for_capability(contract, "file.metadata"), "allow")
        self.assertEqual(policy.decision_for_capability(contract, "communication.send"), "block")
        self.assertEqual(policy.decision_for_capability(contract, "terminal.command"), "block")

    def test_ask_every_time_allows_safe_reads_and_requires_approval_for_mutation(self) -> None:
        contract = policy.build_default_agent_computer_policy(autonomy_mode="ask_every_time")

        self.assertEqual(policy.decision_for_capability(contract, "screen.read"), "allow")
        self.assertEqual(policy.decision_for_capability(contract, "browser.click"), "approval_required")
        self.assertEqual(policy.decision_for_capability(contract, "file.write"), "approval_required")
        self.assertEqual(policy.decision_for_capability(contract, "credential.access"), "block")

    def test_safe_autopilot_requires_approval_for_external_send_and_cloud_storage(self) -> None:
        contract = policy.build_default_agent_computer_policy(autonomy_mode="safe_autopilot")

        self.assertEqual(policy.decision_for_capability(contract, "communication.draft"), "allow")
        self.assertEqual(policy.decision_for_capability(contract, "communication.send"), "approval_required")
        self.assertEqual(policy.decision_for_capability(contract, "cloud_storage.access"), "approval_required")
        self.assertEqual(policy.decision_for_capability(contract, "install.software"), "block")

    def test_trusted_workstation_still_blocks_installs_and_asks_for_money(self) -> None:
        contract = policy.build_default_agent_computer_policy(autonomy_mode="trusted_workstation")

        self.assertEqual(policy.decision_for_capability(contract, "file.write"), "allow")
        self.assertEqual(policy.decision_for_capability(contract, "browser.click"), "allow")
        self.assertEqual(policy.decision_for_capability(contract, "app.control"), "allow")
        self.assertEqual(policy.decision_for_capability(contract, "terminal.command"), "allow")
        self.assertEqual(policy.decision_for_capability(contract, "terminal.approved_script"), "allow")
        self.assertEqual(policy.decision_for_capability(contract, "commerce.payment"), "approval_required")
        self.assertEqual(policy.decision_for_capability(contract, "install.extension"), "block")
        self.assertEqual(contract.terminal_policy, "allow")
        self.assertEqual(contract.network_policy, "allow")
        self.assertEqual(contract.browser_access_policy, "allow")
        self.assertEqual(contract.app_access_policy, "allow")

    def test_emergency_stop_blocks_everything(self) -> None:
        contract = policy.build_default_agent_computer_policy(autonomy_mode="emergency_stop")

        for capability in ("browser.read", "screen.read", "memory.read", "notification.send"):
            self.assertEqual(policy.decision_for_capability(contract, capability), "block")

    def test_decision_for_capability_uses_rust_validate_policy_path(self) -> None:
        contract = policy.build_default_agent_computer_policy(autonomy_mode="safe_autopilot")

        decision = policy.decision_for_capability(contract, "browser.click")

        self.assertEqual(decision, "approval_required")
        self.assertEqual(self._kernel_mock.call_args.args[0], "validate-policy")

    def test_normalize_rejects_unknown_capability(self) -> None:
        with self.assertRaises(policy.AgentComputerPolicyError):
            policy.normalize_agent_computer_policy({"allowed_capabilities": ["unknown.capability"]})

    def test_normalize_rejects_allowed_and_blocked_overlap(self) -> None:
        with self.assertRaises(policy.AgentComputerPolicyError):
            policy.normalize_agent_computer_policy(
                {
                    "allowed_capabilities": ["file.write"],
                    "blocked_capabilities": ["file.write"],
                }
            )

    def test_normalize_preserves_scopes_and_policy_version(self) -> None:
        contract = policy.normalize_agent_computer_policy(
            {
                "policy_id": "policy-1",
                "policy_version": 3,
                "autonomy_mode": "safe",
                "domain_allowlist": ["example.com", "example.com", "docs.example.com"],
                "filesystem_scope": ["/Users/mansur/Work", "/Users/mansur/Work"],
                "approval_ttl_seconds": 30,
            }
        )

        self.assertEqual(contract.policy_id, "policy-1")
        self.assertEqual(contract.policy_version, 3)
        self.assertEqual(contract.autonomy_mode, "safe_autopilot")
        self.assertEqual(contract.domain_allowlist, ("example.com", "docs.example.com"))
        self.assertEqual(contract.filesystem_scope, ("/Users/mansur/Work",))
        self.assertEqual(contract.approval_ttl_seconds, 60)

    def test_evaluate_blocks_domain_outside_allowlist(self) -> None:
        contract = policy.build_default_agent_computer_policy(
            autonomy_mode="safe_autopilot",
            domain_allowlist=["example.com"],
        )

        denied = policy.evaluate_agent_computer_request(
            contract,
            capability="browser.read",
            requested_domain="evil.test",
        )
        allowed = policy.evaluate_agent_computer_request(
            contract,
            capability="browser.read",
            requested_domain="docs.example.com",
        )

        self.assertEqual(denied.decision, "block")
        self.assertEqual(denied.reason, "domain_not_allowed")
        self.assertEqual(allowed.decision, "allow")

    def test_evaluate_blocks_path_outside_custom_filesystem_scope(self) -> None:
        contract = policy.normalize_agent_computer_policy(
            {
                "autonomy_mode": "trusted_workstation",
                "filesystem_scope": ["/Users/mansur/Allowed"],
                "blocked_filesystem_scope": ["/Users/mansur/Allowed/secrets"],
            }
        )

        allowed = policy.evaluate_agent_computer_request(
            contract,
            capability="file.write",
            requested_path="/Users/mansur/Allowed/report.md",
        )
        blocked_nested = policy.evaluate_agent_computer_request(
            contract,
            capability="file.write",
            requested_path="/Users/mansur/Allowed/secrets/token.txt",
        )
        blocked_outside = policy.evaluate_agent_computer_request(
            contract,
            capability="file.write",
            requested_path="/Users/mansur/Other/file.txt",
        )

        self.assertEqual(allowed.decision, "allow")
        self.assertEqual(blocked_nested.reason, "filesystem_scope_not_allowed")
        self.assertEqual(blocked_outside.reason, "filesystem_scope_not_allowed")

    def test_evaluate_blocks_when_path_containment_returns_wrong_action(self) -> None:
        contract = policy.normalize_agent_computer_policy(
            {
                "autonomy_mode": "trusted_workstation",
                "filesystem_scope": ["/Users/mansur/Allowed"],
            }
        )
        with patch.object(
            policy.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "path_within_allowed_root",
                "next_action": "request_agent_computer_approval",
            },
        ):
            decision = policy.evaluate_agent_computer_request(
                contract,
                capability="file.write",
                requested_path="/Users/mansur/Allowed/report.md",
            )

        self.assertEqual(decision.decision, "block")
        self.assertEqual(decision.reason, "unexpected_next_action:allow_path_access")

    def test_evaluate_blocks_when_validate_policy_returns_wrong_action(self) -> None:
        contract = policy.build_default_agent_computer_policy(autonomy_mode="safe_autopilot")
        with patch.object(
            policy.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "capability_allowed_by_policy",
                "next_action": "request_agent_computer_approval",
                "approval_required": False,
            },
        ):
            decision = policy.evaluate_agent_computer_request(
                contract,
                capability="communication.draft",
            )

        self.assertEqual(decision.decision, "block")
        self.assertEqual(decision.reason, "unexpected_next_action:allow_agent_computer_request")

    def test_evaluate_returns_approval_scope_for_risky_action(self) -> None:
        contract = policy.build_default_agent_computer_policy(autonomy_mode="ask_every_time")

        decision = policy.evaluate_agent_computer_request(contract, capability="browser.click")

        self.assertEqual(decision.decision, "approval_required")
        self.assertTrue(decision.approval_required)
        self.assertEqual(decision.approval_scope, "browser.click")

    def test_validate_text_agent_rejects_computer_policy(self) -> None:
        contract = policy.build_default_agent_computer_policy(autonomy_mode="safe_autopilot")

        with self.assertRaises(policy.AgentComputerPolicyError):
            policy.validate_agent_computer_policy(contract, studio_agent_mode="text_agent")

    def test_validate_rejects_implicit_relative_filesystem_scope(self) -> None:
        with self.assertRaises(policy.AgentComputerPolicyError):
            policy.validate_agent_computer_policy(
                {
                    "filesystem_scope": ["Desktop"],
                }
            )

    def test_validate_rejects_implicit_blocked_filesystem_scope(self) -> None:
        with self.assertRaises(policy.AgentComputerPolicyError):
            policy.validate_agent_computer_policy(
                {
                    "blocked_filesystem_scope": ["Secrets"],
                }
            )

    def test_persisted_policy_is_workspace_scoped_and_custom_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir) / "ws-1"
            with patch("server_modules.agent_computer_policy_service.workspace_context.workspace_scope_dir", return_value=root):
                saved = policy.upsert_agent_computer_policy(
                    workspace_id="ws-1",
                    policy={
                        "policy_id": "agent-computer:gw-1",
                        "autonomy_mode": "trusted_workstation",
                        "filesystem_scope": ["/Users/mansur/Work"],
                        "domain_allowlist": ["example.com"],
                    },
                )
                loaded = policy.get_saved_agent_computer_policy(
                    workspace_id="ws-1",
                    policy_id="agent-computer:gw-1",
                )
                fallback, fallback_saved = policy.effective_agent_computer_policy(
                    workspace_id="ws-1",
                    policy_id="missing",
                    runtime_access_mode="custom",
                )

        self.assertEqual(saved.policy_id, "agent-computer:gw-1")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.autonomy_mode, "trusted_workstation")
        self.assertFalse(fallback_saved)
        self.assertEqual(fallback.autonomy_mode, "ask_every_time")

    def test_deployed_agent_record_uses_config_policy_over_metadata(self) -> None:
        contract = policy.agent_computer_policy_from_deployed_agent_record(
            {
                "id": "agent-1",
                "config": {
                    "agent_computer_policy": {
                        "policy_id": "config-policy",
                        "autonomy_mode": "read_only",
                    },
                },
                "metadata": {
                    "agent_computer_policy": {
                        "policy_id": "metadata-policy",
                        "autonomy_mode": "trusted_workstation",
                    },
                },
            }
        )

        self.assertEqual(contract.policy_id, "config-policy")
        self.assertEqual(contract.autonomy_mode, "read_only")

    def test_deployed_agent_record_falls_back_to_computer_automation_allowlist(self) -> None:
        contract = policy.agent_computer_policy_from_deployed_agent_record(
            {
                "deployed_agent_id": "agent-2",
                "config": {
                    "computer_automation": {
                        "autonomy_mode": "safe_autopilot",
                        "allowed_domains": ["example.com"],
                        "filesystem_scope": ["workspace_scoped"],
                    },
                },
            }
        )

        self.assertEqual(contract.policy_id, "deployed-agent:agent-2")
        self.assertEqual(contract.autonomy_mode, "safe_autopilot")
        self.assertEqual(contract.domain_allowlist, ("example.com",))
        self.assertEqual(contract.filesystem_scope, ("workspace_scoped",))


if __name__ == "__main__":
    unittest.main()
