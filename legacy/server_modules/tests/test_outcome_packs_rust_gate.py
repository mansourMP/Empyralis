from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from server_modules import outcome_packs


_ALLOW_DECISION = {
    "ok": True,
    "decision": "allow",
    "reason": "runtime_state_store_policy_satisfied",
    "operation": "runtime-state-store-decision",
    "next_action": "write_outcome_pack_spreadsheet_file",
    "approval_required": False,
    "cacheable": False,
    "audit_visibility": "standard",
}


class OutcomePacksRustGateTests(unittest.TestCase):
    def test_outcome_pack_file_write_calls_rust_before_persisting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sheet.xlsx"
            with mock.patch.object(
                outcome_packs.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=dict(_ALLOW_DECISION),
            ) as rust_decision:
                outcome_packs._enforce_outcome_pack_file_write(
                    operation="write_outcome_pack_spreadsheet_file",
                    pack_id=outcome_packs.SPREADSHEET_OPS_PACK_ID,
                    file_path=path,
                    action="export",
                    storage="local",
                    payload_bytes=128,
                )

            payload = rust_decision.call_args.kwargs
            self.assertEqual(payload["operation"], "write_outcome_pack_spreadsheet_file")
            self.assertEqual(payload["state_class"], "outcome_pack_files")
            self.assertEqual(payload["payload"]["pack_id"], outcome_packs.SPREADSHEET_OPS_PACK_ID)

    def test_outcome_pack_file_write_blocks_when_rust_returns_wrong_action(self) -> None:
        wrong_action = {
            **_ALLOW_DECISION,
            "next_action": "write_outcome_pack_document_file",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sheet.xlsx"
            with mock.patch.object(
                outcome_packs.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=wrong_action,
            ):
                with self.assertRaises(outcome_packs.OutcomePacksRustGateError) as raised:
                    outcome_packs._enforce_outcome_pack_file_write(
                        operation="write_outcome_pack_spreadsheet_file",
                        pack_id=outcome_packs.SPREADSHEET_OPS_PACK_ID,
                        file_path=path,
                        action="export",
                        storage="local",
                        payload_bytes=128,
                    )

        self.assertEqual(str(raised.exception), "unexpected_next_action")


if __name__ == "__main__":
    unittest.main()
