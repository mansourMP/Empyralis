from __future__ import annotations

import importlib.util
from pathlib import Path

from server_modules import computer_action_safety, run_service


ROOT = Path(__file__).resolve().parents[2]
WORKER_MODULE_PATH = ROOT / "scripts" / "orion_local_worker_execution.py"
WORKER_SPEC = importlib.util.spec_from_file_location("orion_local_worker_execution_under_test", WORKER_MODULE_PATH)
assert WORKER_SPEC is not None and WORKER_SPEC.loader is not None
worker_module = importlib.util.module_from_spec(WORKER_SPEC)
WORKER_SPEC.loader.exec_module(worker_module)


def test_credential_entry_requires_confirmation_for_owner_mode():
    result = computer_action_safety.evaluate_dangerous_computer_action_policy(
        {
            "action": "type",
            "text": "super-secret-password",
            "target_description": "password field",
        },
        metadata={"owner_role": "owner", "owner_is_admin": True},
        capability_id="computer_control.type",
        policy_mode="local_default",
        target="local_companion",
    )

    assert result["execution_decision"] == "require_confirmation"
    assert "credential_entry" in result["dangerous_action_classes"]


def test_dangerous_computer_action_is_blocked_for_member_mode():
    result = computer_action_safety.evaluate_dangerous_computer_action_policy(
        {
            "action": "launch_app",
            "target": "System Settings",
            "reason": "Open privacy settings",
        },
        metadata={"owner_role": "member", "owner_is_admin": False},
        capability_id="computer_control.launch_app",
        policy_mode="local_default",
        target="local_companion",
    )

    assert result["execution_decision"] == "deny"
    assert "system_settings_change" in result["dangerous_action_classes"]


def test_explicit_allow_bypasses_confirmation_for_owner_mode():
    result = computer_action_safety.evaluate_dangerous_computer_action_policy(
        {
            "action": "type",
            "text": "sk-live-1234567890abcdef",
            "target_description": "API key field",
        },
        metadata={
            "owner_role": "owner",
            "owner_is_admin": True,
            "computer_action_policy": {"allow_dangerous_classes": ["credential_entry"]},
        },
        capability_id="computer_control.type",
        policy_mode="trusted_full_access",
        target="local_companion",
    )

    assert result["execution_decision"] == "allow"
    assert result["decision"] == "allow"


def test_workspace_capability_policy_denial_blocks_dangerous_action():
    result = computer_action_safety.evaluate_dangerous_computer_action_policy(
        {
            "action": "type",
            "text": "super-secret-password",
            "target_description": "password field",
        },
        metadata={
            "owner_role": "owner",
            "owner_is_admin": True,
            "workspace_capability_policy": {"deny": ["computer_control.type"]},
        },
        capability_id="computer_control.type",
        policy_mode="trusted_full_access",
        target="local_companion",
    )

    assert result["execution_decision"] == "deny"
    assert result["reason"] == "workspace_capability_denied"


def test_precheck_human_action_labels_uses_explicit_approval_label():
    labels = run_service.precheck_human_action_labels(
        {
            "items": [
                {
                    "tool_id": "computer_control.type",
                    "execution_decision": "require_confirmation",
                    "approval_label": "Enter credentials",
                }
            ]
        }
    )

    assert labels == ["Enter credentials"]


def test_worker_policy_gate_blocks_dangerous_action_without_precomputed_precheck():
    metadata = {"owner_role": "member", "owner_is_admin": False, "policy_mode": "local_default", "execution_target": "local_companion"}
    operations = [
        {
            "tool": "computer_control",
            "action": "launch_app",
            "target": "System Settings",
            "reason": "Open privacy settings",
        }
    ]

    try:
        worker_module._policy_gate(metadata, operations)
    except RuntimeError as exc:
        assert "Change system settings" in str(exc)
    else:
        raise AssertionError("Expected dangerous action to be blocked by worker policy gate.")
