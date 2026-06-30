from __future__ import annotations

import unittest
from unittest import mock

from server_modules import capability_risk_classifier_service as classifier
from server_modules import agent_computer_policy_service as policies
from server_modules import agent_computer_profile_service as profiles


class CapabilityRiskClassifierServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._kernel_patch = mock.patch.object(
            classifier.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=self._fake_rust_classifier,
        )
        self._kernel_patch.start()

    def tearDown(self) -> None:
        self._kernel_patch.stop()

    def _fake_rust_classifier(self, command: str, payload: dict, **kwargs):
        self.assertEqual(command, "classify-risk")
        self.assertTrue(kwargs.get("allow_approval_required"))
        capability = str(payload.get("capability") or "").strip()
        action_class = str(payload.get("action_class") or "read").strip() or "read"
        policy = dict(payload.get("policy") or {})
        current_kill_state = str(payload.get("current_kill_state") or "").strip().lower()
        if current_kill_state:
            self._raise_block(
                reason=f"kill_state:{current_kill_state}",
                risk_class="critical",
                capability=capability,
                action_class=action_class,
            )
        profile = payload.get("computer_profile") if isinstance(payload.get("computer_profile"), dict) else {}
        if str(profile.get("health_state") or "").strip().lower() in {"offline", "unhealthy", "revoked"}:
            self._raise_block(
                reason=f"profile_{str(profile.get('health_state') or '').strip().lower()}",
                risk_class="critical",
                capability=capability,
                action_class=action_class,
            )
        requested_domain = str(payload.get("requested_domain") or "").strip().lower()
        allowed_domains = [str(item or "").strip().lower() for item in list(policy.get("domain_allowlist") or [])]
        if requested_domain and allowed_domains and not any(
            requested_domain == domain or requested_domain.endswith(f".{domain}")
            for domain in allowed_domains
            if domain
        ):
            self._raise_block(
                reason="domain_not_allowed",
                risk_class="critical",
                capability=capability,
                action_class=action_class,
            )
        if capability in {"install.software", "credential.access"}:
            self._raise_block(
                reason="capability_blocked_by_policy",
                risk_class="critical",
                capability=capability,
                action_class=action_class,
            )
        if capability in {"browser.form_submit", "terminal.command", "file.delete", "communication.send"}:
            return self._rust_response(
                decision="require_approval",
                risk_class="high",
                capability=capability,
                action_class=action_class,
            )
        return self._rust_response(
            decision="allow",
            risk_class="low",
            capability=capability,
            action_class=action_class,
        )

    def _rust_response(self, *, decision: str, risk_class: str, capability: str, action_class: str) -> dict:
        return {
            "ok": decision != "block",
            "decision": decision,
            "reason": "risk_allowed" if decision == "allow" else "risk_requires_approval",
            "next_action": (
                "allow_capability_execution"
                if decision == "allow"
                else "request_capability_risk_approval"
                if decision == "require_approval"
                else "block_capability_execution"
            ),
            "approval_required": decision == "require_approval",
            "decision_id": "crd_test",
            "risk_level": risk_class,
            "risk_class": risk_class,
            "action_class": action_class,
            "capability": capability,
            "approval_scopes_required": [],
            "audit_visibility": "security" if risk_class in {"high", "critical"} else "standard",
            "recording_required": risk_class in {"high", "critical"},
            "retention_class": "extended" if risk_class == "critical" else "standard" if risk_class == "high" else "short",
            "cacheable": False,
        }

    def _raise_block(self, *, reason: str, risk_class: str, capability: str, action_class: str) -> None:
        decision = self._rust_response(
            decision="block",
            risk_class=risk_class,
            capability=capability,
            action_class=action_class,
        )
        decision["reason"] = reason
        decision["blocked_reason"] = reason
        raise classifier.rust_runtime_kernel_client.RustKernelDecisionError(
            decision,
            command="classify-risk",
        )

    def test_safe_read_is_allowed_with_complete_contract_shape(self) -> None:
        policy = policies.build_default_agent_computer_policy(autonomy_mode="safe_autopilot")

        decision = classifier.classify_capability_risk(
            policy=policy,
            capability="browser.read",
            action_class="read",
            target_url="https://example.com/docs",
        )
        payload = decision.as_dict()

        self.assertEqual(payload["decision"], "allow")
        self.assertEqual(payload["risk_class"], "low")
        self.assertEqual(payload["risk_level"], 1)
        self.assertEqual(payload["capability"], "browser.read")
        self.assertEqual(payload["policy_version"], policy.policy_version)
        self.assertFalse(payload["cacheable"])
        self.assertIn("decision_id", payload)

    def test_browser_fill_requires_approval(self) -> None:
        policy = policies.build_default_agent_computer_policy(autonomy_mode="safe_autopilot")

        decision = classifier.classify_gateway_browser_action_risk(
            policy=policy,
            browser_action="fill",
            payload={"url": "https://example.com/form"},
        )

        self.assertEqual(decision.decision, "approval_required")
        self.assertEqual(decision.capability, "browser.form_submit")
        self.assertEqual(decision.risk_class, "high")
        self.assertEqual(decision.approval_scopes_required, ("browser.form_submit",))
        self.assertTrue(decision.recording_required)

    def test_terminal_command_requires_approval_in_ask_mode(self) -> None:
        policy = policies.build_default_agent_computer_policy(autonomy_mode="ask_every_time")

        decision = classifier.classify_gateway_tool_risk(
            policy=policy,
            capability_id="shell.command",
            arguments={"command": "ls -la"},
        )

        self.assertEqual(decision.decision, "approval_required")
        self.assertEqual(decision.capability, "terminal.command")
        self.assertEqual(decision.risk_class, "high")
        self.assertEqual(decision.audit_visibility, "payload_redacted")

    def test_file_delete_is_high_risk_and_approval_required(self) -> None:
        policy = policies.build_default_agent_computer_policy(autonomy_mode="ask_every_time")

        decision = classifier.classify_capability_risk(
            policy=policy,
            capability="file.delete",
            action_class="write",
            target_path="/Users/mansur/Agent/report.md",
        )

        self.assertEqual(decision.risk_class, "high")
        self.assertEqual(decision.decision, "approval_required")
        self.assertTrue(decision.recording_required)

    def test_install_software_is_critical_and_blocked(self) -> None:
        policy = policies.build_default_agent_computer_policy(autonomy_mode="trusted_workstation")

        decision = classifier.classify_capability_risk(
            policy=policy,
            capability="install.software",
            action_class="execute",
            payload={"package": "unknown.pkg"},
        )

        self.assertEqual(decision.risk_class, "critical")
        self.assertEqual(decision.decision, "block")
        self.assertEqual(decision.retention_class, "security")
        self.assertEqual(decision.audit_visibility, "security_event")

    def test_domain_outside_policy_is_blocked(self) -> None:
        policy = policies.build_default_agent_computer_policy(
            autonomy_mode="safe_autopilot",
            domain_allowlist=["example.com"],
        )

        decision = classifier.classify_capability_risk(
            policy=policy,
            capability="browser.read",
            target_url="https://evil.test",
        )

        self.assertEqual(decision.decision, "block")
        self.assertEqual(decision.blocked_reason, "domain_not_allowed")

    def test_kill_state_blocks_even_safe_read(self) -> None:
        policy = policies.build_default_agent_computer_policy(autonomy_mode="safe_autopilot")

        decision = classifier.classify_capability_risk(
            policy=policy,
            capability="browser.read",
            target_url="https://example.com",
            current_kill_state="workspace_emergency_stop",
        )

        self.assertEqual(decision.decision, "block")
        self.assertEqual(decision.blocked_reason, "kill_state:workspace_emergency_stop")
        self.assertEqual(decision.audit_visibility, "security_event")

    def test_unhealthy_profile_blocks_action(self) -> None:
        policy = policies.build_default_agent_computer_policy(autonomy_mode="safe_autopilot")
        profile = profiles.normalize_agent_computer_profile(
            {
                "workspace_id": "ws-1",
                "policy_id": "policy-1",
                "environment_kind": "personal_computer",
                "gateway_id": "gw-1",
                "health_state": "offline",
            }
        )

        decision = classifier.classify_capability_risk(
            policy=policy,
            capability="browser.read",
            computer_profile=profile,
        )

        self.assertEqual(decision.decision, "block")
        self.assertEqual(decision.blocked_reason, "profile_offline")

    def test_target_summary_redacts_secrets(self) -> None:
        policy = policies.build_default_agent_computer_policy(autonomy_mode="ask_every_time")

        decision = classifier.classify_capability_risk(
            policy=policy,
            capability="communication.send",
            action_class="write",
            target_channel="telegram",
            payload={"recipient": "+15555551212", "message": "token=sk-secret123456789"},
        )

        self.assertEqual(decision.decision, "approval_required")
        self.assertIn("[redacted-phone]", decision.target_summary)
        self.assertNotIn("+15555551212", decision.target_summary)

    def test_credential_access_is_critical_and_blocked(self) -> None:
        policy = policies.build_default_agent_computer_policy(autonomy_mode="safe_autopilot")

        decision = classifier.classify_capability_risk(
            policy=policy,
            capability="credential.access",
            action_class="read",
        )

        self.assertEqual(decision.risk_class, "critical")
        self.assertEqual(decision.decision, "block")

    def test_unknown_capability_fails_closed(self) -> None:
        with self.assertRaises(classifier.CapabilityRiskClassifierError):
            classifier.classify_capability_risk(
                policy=policies.build_default_agent_computer_policy(),
                capability="unknown.capability",
            )

    def test_unexpected_rust_next_action_fails_closed(self) -> None:
        with mock.patch.object(
            classifier.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "risk_allowed",
                "next_action": "request_capability_risk_approval",
                "approval_required": False,
                "decision_id": "crd_wrong_action",
                "risk_level": "low",
                "risk_class": "low",
                "action_class": "read",
                "capability": "browser.read",
                "approval_scopes_required": [],
                "audit_visibility": "standard",
                "recording_required": False,
                "retention_class": "short",
                "cacheable": False,
            },
        ):
            with self.assertRaisesRegex(classifier.CapabilityRiskClassifierError, "unexpected_next_action:request_capability_risk_approval"):
                classifier.classify_capability_risk(
                    policy=policies.build_default_agent_computer_policy(autonomy_mode="safe_autopilot"),
                    capability="browser.read",
                    action_class="read",
                    target_url="https://example.com/docs",
                )


if __name__ == "__main__":
    unittest.main()
