from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from server_modules import hosted_secure_worker, marketplace_distribution_service


_ALLOW_DECISION = {
    "ok": True,
    "decision": "allow",
    "reason": "runtime_state_store_policy_satisfied",
    "operation": "runtime-state-store-decision",
    "next_action": "write_hosted_worker_output",
    "approval_required": False,
    "cacheable": False,
    "audit_visibility": "standard",
}


class WorkerMarketplaceStateRustGateTests(unittest.TestCase):
    def test_hosted_worker_output_calls_rust_before_persisting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "turn-result.json"
            with mock.patch.dict(os.environ, {"EMPYRALIS_SANDBOX_OUTPUT_FILE": str(output_path)}, clear=False), mock.patch.object(
                hosted_secure_worker.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=dict(_ALLOW_DECISION),
            ) as rust_decision:
                hosted_secure_worker._write_workspace_output({"status": "completed", "message": "ok"})

            self.assertTrue(output_path.exists())
            payload = rust_decision.call_args.kwargs
            self.assertEqual(payload["operation"], "write_hosted_worker_output")
            self.assertEqual(payload["state_class"], "hosted_worker_output")
            self.assertEqual(payload["status"], "completed")
            self.assertEqual(payload["payload"]["output_path"], str(output_path.resolve()))

    def test_hosted_worker_output_blocks_before_file_write_when_rust_blocks(self) -> None:
        block_decision = {
            **_ALLOW_DECISION,
            "ok": False,
            "decision": "block",
            "reason": "owner_access_denied",
            "operation": "write_hosted_worker_output",
            "audit_visibility": "security",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "turn-result.json"
            with mock.patch.dict(os.environ, {"EMPYRALIS_SANDBOX_OUTPUT_FILE": str(output_path)}, clear=False), mock.patch.object(
                hosted_secure_worker.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=block_decision,
            ):
                with self.assertRaises(hosted_secure_worker.HostedWorkerRustGateError):
                    hosted_secure_worker._write_workspace_output({"status": "completed"})

            self.assertFalse(output_path.exists())

    def test_hosted_worker_output_blocks_before_file_write_on_wrong_rust_action(self) -> None:
        wrong_action = {
            **_ALLOW_DECISION,
            "next_action": "write_profile_api_file",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "turn-result.json"
            with mock.patch.dict(os.environ, {"EMPYRALIS_SANDBOX_OUTPUT_FILE": str(output_path)}, clear=False), mock.patch.object(
                hosted_secure_worker.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=wrong_action,
            ):
                with self.assertRaises(hosted_secure_worker.HostedWorkerRustGateError) as raised:
                    hosted_secure_worker._write_workspace_output({"status": "completed"})

            self.assertEqual(str(raised.exception), "unexpected_next_action")
            self.assertFalse(output_path.exists())

    def test_marketplace_distribution_save_calls_rust_before_persisting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "marketplace_distribution.json"
            with mock.patch.object(
                marketplace_distribution_service,
                "_distribution_state_path",
                return_value=state_path,
            ), mock.patch.object(
                marketplace_distribution_service.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value={**_ALLOW_DECISION, "next_action": "save_marketplace_distribution_state"},
            ) as rust_decision:
                result = marketplace_distribution_service._save_state(
                    "workspace-1",
                    {"packages": {"pkg-1": {"id": "pkg-1"}}, "installs": {}},
                )

            self.assertTrue(state_path.exists())
            self.assertIn("pkg-1", result["packages"])
            payload = rust_decision.call_args.kwargs
            self.assertEqual(payload["operation"], "save_marketplace_distribution_state")
            self.assertEqual(payload["state_class"], "marketplace_distribution")
            self.assertEqual(payload["workspace_id"], "workspace-1")

    def test_marketplace_distribution_blocks_before_file_write_on_wrong_rust_action(self) -> None:
        wrong_action = {
            **_ALLOW_DECISION,
            "next_action": "write_profile_api_file",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "marketplace_distribution.json"
            with mock.patch.object(
                marketplace_distribution_service,
                "_distribution_state_path",
                return_value=state_path,
            ), mock.patch.object(
                marketplace_distribution_service.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=wrong_action,
            ):
                with self.assertRaises(marketplace_distribution_service.MarketplaceDistributionRustGateError) as raised:
                    marketplace_distribution_service._save_state(
                        "workspace-1",
                        {"packages": {"pkg-1": {"id": "pkg-1"}}, "installs": {}},
                    )

            self.assertEqual(str(raised.exception), "unexpected_next_action")
            self.assertFalse(state_path.exists())

    def test_marketplace_distribution_read_missing_file_is_not_a_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "marketplace_distribution.json"
            with mock.patch.object(
                marketplace_distribution_service,
                "_distribution_state_path",
                return_value=state_path,
            ):
                result = marketplace_distribution_service._safe_read_state("workspace-1")

            self.assertEqual(result["packages"], {})
            self.assertFalse(state_path.exists())


if __name__ == "__main__":
    unittest.main()
