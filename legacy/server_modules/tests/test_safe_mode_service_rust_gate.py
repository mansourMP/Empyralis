from __future__ import annotations

import unittest
from unittest import mock

from server_modules import safe_mode_service


_ALLOW_DECISION = {
    "ok": True,
    "decision": "allow",
    "reason": "runtime_state_store_policy_satisfied",
    "operation": "runtime-state-store-decision",
    "next_action": "set_safe_mode_state",
    "approval_required": False,
    "cacheable": False,
    "audit_visibility": "standard",
}


class SafeModeServiceRustGateTests(unittest.TestCase):
    def test_resolve_capability_disable_state_uses_rust_control_metadata(self) -> None:
        state = safe_mode_service.SafeModeState(enabled=True, reason="python fallback")
        rust_block = {
            "ok": False,
            "decision": "block",
            "reason": "capability_blocked_by_safe_mode",
            "next_action": "disable_safe_mode_capability",
            "control_scope": "workspace",
            "control_type": "safe_mode",
            "control_reason": "rust workspace incident",
            "audit_visibility": "security",
        }
        with mock.patch.object(safe_mode_service, "_GLOBAL_SAFE_MODE", state), mock.patch.object(
            safe_mode_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value=rust_block,
        ):
            resolved = safe_mode_service.resolve_capability_disable_state("computer_control.click")

        self.assertTrue(resolved["disabled"])
        self.assertEqual(resolved["scope"], "workspace")
        self.assertEqual(resolved["type"], "safe_mode")
        self.assertEqual(resolved["reason"], "rust workspace incident")

    def test_resolve_capability_disable_state_fails_closed_on_wrong_rust_action(self) -> None:
        state = safe_mode_service.SafeModeState(enabled=True, reason="python fallback")
        rust_allow_wrong_action = {
            "ok": True,
            "decision": "allow",
            "reason": "safe_mode_clear",
            "next_action": "disable_safe_mode_capability",
            "control_scope": "workspace",
            "control_type": "safe_mode",
            "control_reason": "rust workspace incident",
            "audit_visibility": "standard",
        }
        with mock.patch.object(safe_mode_service, "_GLOBAL_SAFE_MODE", state), mock.patch.object(
            safe_mode_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value=rust_allow_wrong_action,
        ):
            resolved = safe_mode_service.resolve_capability_disable_state("computer_control.click")

        self.assertTrue(resolved["disabled"])
        self.assertEqual(resolved["reason"], "unexpected_next_action:allow_safe_mode_capability")
        self.assertEqual(resolved["scope"], "workspace")
        self.assertEqual(resolved["type"], "safe_mode")

    def test_set_safe_mode_calls_rust_before_memory_mutation(self) -> None:
        with mock.patch.object(safe_mode_service, "_GLOBAL_SAFE_MODE", safe_mode_service.SafeModeState(enabled=False)), mock.patch.object(
            safe_mode_service.rust_runtime_kernel_client,
            "runtime_state_store_decision",
            return_value=dict(_ALLOW_DECISION),
        ) as rust_decision:
            result = safe_mode_service.set_safe_mode(enabled=True, reason="incident")

        self.assertEqual(result["scope"], "global")
        self.assertTrue(result["state"]["enabled"])
        payload = rust_decision.call_args.kwargs
        self.assertEqual(payload["operation"], "set_safe_mode_state")
        self.assertEqual(payload["state_class"], "security_control_state")
        self.assertEqual(payload["status"], "active")
        self.assertEqual(payload["payload"]["scope"], "global")
        self.assertEqual(payload["payload"]["control_kind"], "safe_mode")

    def test_set_safe_mode_blocks_before_memory_mutation_when_rust_blocks(self) -> None:
        block_decision = {
            **_ALLOW_DECISION,
            "ok": False,
            "decision": "block",
            "reason": "owner_access_denied",
            "operation": "set_safe_mode_state",
            "audit_visibility": "security",
        }
        state = safe_mode_service.SafeModeState(enabled=False)
        with mock.patch.object(safe_mode_service, "_GLOBAL_SAFE_MODE", state), mock.patch.object(
            safe_mode_service.rust_runtime_kernel_client,
            "runtime_state_store_decision",
            return_value=block_decision,
        ):
            with self.assertRaises(safe_mode_service.SafeModeRustGateError):
                safe_mode_service.set_safe_mode(enabled=True, reason="incident")

        self.assertFalse(state.enabled)

    def test_set_safe_mode_blocks_before_memory_mutation_on_wrong_rust_action(self) -> None:
        wrong_action = {
            **_ALLOW_DECISION,
            "next_action": "write_profile_api_file",
        }
        state = safe_mode_service.SafeModeState(enabled=False)
        with mock.patch.object(safe_mode_service, "_GLOBAL_SAFE_MODE", state), mock.patch.object(
            safe_mode_service.rust_runtime_kernel_client,
            "runtime_state_store_decision",
            return_value=wrong_action,
        ):
            with self.assertRaises(safe_mode_service.SafeModeRustGateError) as raised:
                safe_mode_service.set_safe_mode(enabled=True, reason="incident")

        self.assertEqual(str(raised.exception), "unexpected_next_action")
        self.assertFalse(state.enabled)

    def test_durable_security_control_upsert_requires_rust_before_repository_write(self) -> None:
        with mock.patch.object(
            safe_mode_service.rust_runtime_kernel_client,
            "runtime_state_store_decision",
            return_value={**_ALLOW_DECISION, "next_action": "upsert_security_control_state"},
        ) as rust_decision, mock.patch.object(
            safe_mode_service.control_plane_repository,
            "upsert_security_control_state",
            new=mock.AsyncMock(return_value={"id": "control-1"}),
        ) as repo_write:
            row = safe_mode_service._persist_security_control_state(
                scope="workspace",
                enabled=True,
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                reason="incident",
                metadata={"requested_by_user_id": "owner-1"},
                control_kind="safe_mode",
            )

        self.assertEqual(row, {"id": "control-1"})
        payload = rust_decision.call_args.kwargs
        self.assertEqual(payload["operation"], "upsert_security_control_state")
        self.assertEqual(payload["state_class"], "security_control_state")
        self.assertEqual(payload["tenant_id"], "tenant-1")
        self.assertEqual(payload["workspace_id"], "workspace-1")
        self.assertEqual(payload["actor_id"], "owner-1")
        repo_write.assert_awaited_once()

    def test_durable_security_control_blocks_before_repository_write(self) -> None:
        block_decision = {
            **_ALLOW_DECISION,
            "ok": False,
            "decision": "block",
            "reason": "owner_access_denied",
            "operation": "upsert_security_control_state",
            "audit_visibility": "security",
        }
        with mock.patch.object(
            safe_mode_service.rust_runtime_kernel_client,
            "runtime_state_store_decision",
            return_value=block_decision,
        ), mock.patch.object(
            safe_mode_service.control_plane_repository,
            "upsert_security_control_state",
            new=mock.AsyncMock(return_value={"id": "control-1"}),
        ) as repo_write:
            with self.assertRaises(safe_mode_service.SafeModeRustGateError):
                safe_mode_service._persist_security_control_state(
                    scope="workspace",
                    enabled=True,
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    reason="incident",
                    metadata={"requested_by_user_id": "owner-1"},
                    control_kind="safe_mode",
                )

        repo_write.assert_not_awaited()

    def test_durable_security_control_blocks_before_repository_write_on_wrong_rust_action(self) -> None:
        wrong_action = {
            **_ALLOW_DECISION,
            "next_action": "write_profile_api_file",
        }
        with mock.patch.object(
            safe_mode_service.rust_runtime_kernel_client,
            "runtime_state_store_decision",
            return_value=wrong_action,
        ), mock.patch.object(
            safe_mode_service.control_plane_repository,
            "upsert_security_control_state",
            new=mock.AsyncMock(return_value={"id": "control-1"}),
        ) as repo_write:
            with self.assertRaises(safe_mode_service.SafeModeRustGateError) as raised:
                safe_mode_service._persist_security_control_state(
                    scope="workspace",
                    enabled=True,
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    reason="incident",
                    metadata={"requested_by_user_id": "owner-1"},
                    control_kind="safe_mode",
                )

        self.assertEqual(str(raised.exception), "unexpected_next_action")
        repo_write.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
