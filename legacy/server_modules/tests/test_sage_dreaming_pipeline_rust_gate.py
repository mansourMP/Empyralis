from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from server_modules import sage_dreaming_pipeline


_ALLOW_DECISION = {
    "ok": True,
    "decision": "allow",
    "reason": "runtime_state_store_policy_satisfied",
    "operation": "runtime-state-store-decision",
    "next_action": "write_sage_dreaming_staging_file",
    "approval_required": False,
    "cacheable": False,
    "audit_visibility": "standard",
}


class SageDreamingPipelineRustGateTests(unittest.TestCase):
    def test_dreaming_json_write_calls_rust_before_persisting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "dream.json"
            with mock.patch.object(
                sage_dreaming_pipeline.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=dict(_ALLOW_DECISION),
            ) as rust_decision:
                sage_dreaming_pipeline._write_dreaming_json(
                    operation="write_sage_dreaming_staging_file",
                    workspace_id="workspace-1",
                    path=path,
                    payload={"stage": "light_sleep", "merged": 0},
                )

            self.assertTrue(path.exists())
            payload = rust_decision.call_args.kwargs
            self.assertEqual(payload["operation"], "write_sage_dreaming_staging_file")
            self.assertEqual(payload["state_class"], "sage_dreaming")
            self.assertEqual(payload["workspace_id"], "workspace-1")
            self.assertEqual(payload["status"], "light_sleep")

    def test_dreaming_json_write_blocks_before_file_write_when_rust_returns_wrong_action(self) -> None:
        wrong_action = {
            **_ALLOW_DECISION,
            "next_action": "write_profile_api_file",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "dream.json"
            with mock.patch.object(
                sage_dreaming_pipeline.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=wrong_action,
            ):
                with self.assertRaises(sage_dreaming_pipeline.SageDreamingRustGateError) as raised:
                    sage_dreaming_pipeline._write_dreaming_json(
                        operation="write_sage_dreaming_staging_file",
                        workspace_id="workspace-1",
                        path=path,
                        payload={"stage": "light_sleep"},
                    )

            self.assertFalse(path.exists())
            self.assertEqual(str(raised.exception), "unexpected_next_action")

    def test_dreaming_json_write_blocks_before_file_write_when_rust_blocks(self) -> None:
        block_decision = {
            **_ALLOW_DECISION,
            "ok": False,
            "decision": "block",
            "reason": "owner_access_denied",
            "operation": "write_sage_dreaming_staging_file",
            "audit_visibility": "security",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "dream.json"
            with mock.patch.object(
                sage_dreaming_pipeline.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=block_decision,
            ):
                with self.assertRaises(sage_dreaming_pipeline.SageDreamingRustGateError):
                    sage_dreaming_pipeline._write_dreaming_json(
                        operation="write_sage_dreaming_staging_file",
                        workspace_id="workspace-1",
                        path=path,
                        payload={"stage": "light_sleep"},
                    )

            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
