import unittest
from unittest.mock import patch

from server_modules import runtime_attachment_service


class RuntimeAttachmentRustGateTests(unittest.TestCase):
    @staticmethod
    def _allow(operation: str, **extra):
        next_action_map = {
            "normalize_target": "normalize_runtime_target_id",
            "build_targets": "build_workspace_runtime_targets",
            "select_attachment": "select_runtime_attachment",
            "self_hosted_gate": "ensure_self_hosted_node_gate",
            "local_companion_gate": "select_local_companion_attachment",
            "usage_credit_event": "build_runtime_usage_credit_event",
        }
        return {
            "ok": True,
            "decision": "allow",
            "reason": f"{operation}_allowed",
            "operation": operation,
            "next_action": next_action_map.get(operation, operation),
            "audit_visibility": "standard",
            "approval_required": False,
            "cacheable": False,
            **extra,
        }

    def test_runtime_usage_credit_event_normalizes_target_through_rust_before_metering(self) -> None:
        operations = []

        def fake_rust(command: str, payload: dict, **kwargs):
            self.assertEqual(command, "runtime-attachment-decision")
            operations.append(payload["operation"])
            if payload["operation"] == "normalize_target":
                self.assertEqual(payload["runtime_target"], "cloud_computer")
                return self._allow(
                    "normalize_target",
                    runtime_target="sage_cloud_computer",
                    canonical_runtime_target="empyralis_cloud_computer",
                )
            if payload["operation"] == "usage_credit_event":
                self.assertEqual(payload["runtime_target"], "sage_cloud_computer")
                return self._allow("usage_credit_event")
            raise AssertionError(f"unexpected operation {payload['operation']}")

        with patch.object(
            runtime_attachment_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=fake_rust,
        ):
            event = runtime_attachment_service.build_runtime_usage_credit_event(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                surface="sage",
                runtime_target="cloud_computer",
                session_id="session-1",
                started_at="2026-06-01T00:00:00Z",
                ended_at="2026-06-01T00:05:00Z",
                active_seconds=300,
                billable_seconds=300,
            )

        self.assertEqual(operations, ["normalize_target", "usage_credit_event"])
        self.assertEqual(event["runtime_target"], "sage_cloud_computer")
        self.assertEqual(event["canonical_runtime_target"], "empyralis_cloud_computer")

    def test_runtime_usage_credit_event_fails_closed_when_rust_rejects_target_normalization(self) -> None:
        operations = []

        def fake_rust(command: str, payload: dict, **kwargs):
            operations.append(payload["operation"])
            if payload["operation"] == "normalize_target":
                raise runtime_attachment_service.rust_runtime_kernel_client.RustKernelDecisionError(
                    {
                        "ok": False,
                        "decision": "block",
                        "reason": "runtime_target_unsupported",
                    },
                    command=command,
                )
            raise AssertionError("usage metering should not run after normalization denial")

        with patch.object(
            runtime_attachment_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=fake_rust,
        ):
            with self.assertRaises(ValueError):
                runtime_attachment_service.build_runtime_usage_credit_event(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    surface="sage",
                    runtime_target="unknown_runtime",
                    session_id="session-1",
                    started_at="2026-06-01T00:00:00Z",
                    ended_at="2026-06-01T00:05:00Z",
                    active_seconds=300,
                    billable_seconds=300,
                )

        self.assertEqual(operations, ["normalize_target"])

    def test_runtime_usage_credit_event_fails_closed_when_rust_returns_wrong_normalize_action(self) -> None:
        operations = []

        def fake_rust(command: str, payload: dict, **kwargs):
            operations.append(payload["operation"])
            if payload["operation"] == "normalize_target":
                return self._allow(
                    "normalize_target",
                    next_action="build_workspace_runtime_targets",
                    runtime_target="sage_cloud_computer",
                    canonical_runtime_target="empyralis_cloud_computer",
                )
            raise AssertionError("usage metering should not run after wrong normalize next_action")

        with patch.object(
            runtime_attachment_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=fake_rust,
        ):
            with self.assertRaises(ValueError) as raised:
                runtime_attachment_service.build_runtime_usage_credit_event(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    surface="sage",
                    runtime_target="cloud_computer",
                    session_id="session-1",
                    started_at="2026-06-01T00:00:00Z",
                    ended_at="2026-06-01T00:05:00Z",
                    active_seconds=300,
                    billable_seconds=300,
                )

        self.assertIn("unexpected_next_action:build_workspace_runtime_targets", str(raised.exception))
        self.assertEqual(operations, ["normalize_target"])

    def test_runtime_usage_credit_event_fails_closed_when_rust_returns_wrong_usage_action(self) -> None:
        operations = []

        def fake_rust(command: str, payload: dict, **kwargs):
            operations.append(payload["operation"])
            if payload["operation"] == "normalize_target":
                return self._allow(
                    "normalize_target",
                    runtime_target="sage_cloud_computer",
                    canonical_runtime_target="empyralis_cloud_computer",
                )
            if payload["operation"] == "usage_credit_event":
                return self._allow("usage_credit_event", next_action="select_runtime_attachment")
            raise AssertionError(f"unexpected operation {payload['operation']}")

        with patch.object(
            runtime_attachment_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=fake_rust,
        ):
            with self.assertRaises(RuntimeError) as raised:
                runtime_attachment_service.build_runtime_usage_credit_event(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    surface="sage",
                    runtime_target="cloud_computer",
                    session_id="session-1",
                    started_at="2026-06-01T00:00:00Z",
                    ended_at="2026-06-01T00:05:00Z",
                    active_seconds=300,
                    billable_seconds=300,
                )

        self.assertIn("unexpected_next_action:select_runtime_attachment", str(raised.exception))
        self.assertEqual(operations, ["normalize_target", "usage_credit_event"])


if __name__ == "__main__":
    unittest.main()
