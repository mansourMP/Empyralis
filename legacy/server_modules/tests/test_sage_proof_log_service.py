from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server_modules import sage_proof_log_service


class SageProofLogServiceTests(unittest.TestCase):
    def test_append_list_get_summary_and_redaction(self):
        calls = []

        def _kernel(command, payload):
            calls.append((command, payload))
            return {
                "decision": "allow",
                "operation": payload["operation"],
                "next_action": payload["operation"],
            }

        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "server_modules.sage_proof_log_service.workspace_context.workspace_scope_dir",
            return_value=Path(tmpdir) / "ws-1",
        ), patch(
            "server_modules.sage_proof_log_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
            side_effect=_kernel,
        ):
            proof_log = {
                "version": "daily_operator_proof_v1",
                "recipe_id": "morning_brief",
                "title": "Morning brief",
                "status": "completed",
                "checked": [{"tool": "gmail", "status": "completed"}],
                "changes": [],
                "blocked": [],
                "approvals": [],
                "metadata": {"api_key": "sk-test-secret-value"},
            }
            record = sage_proof_log_service.append_proof_log(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                actor_user_id="user-1",
                trace_id="trace-1",
                surface="chat",
                proof_log=proof_log,
                status="completed",
                title="Morning brief",
                source="sage_chat",
            )
            duplicate = sage_proof_log_service.append_proof_log(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                actor_user_id="user-1",
                trace_id="trace-1",
                surface="chat",
                proof_log={**proof_log, "checked": [{"tool": "calendar", "status": "completed"}]},
                status="completed",
                title="Morning brief",
                source="sage_chat",
            )

            self.assertEqual(record["proof_id"], duplicate["proof_id"])
            self.assertEqual(calls[0][0], "runtime-state-store-decision")
            self.assertEqual(calls[0][1]["state_class"], "sage_proof_logs")
            self.assertEqual(calls[0][1]["operation"], "append_sage_proof_log")

            payload = sage_proof_log_service.list_proof_logs(
                workspace_id="ws-1",
                tenant_id="tenant-1",
            )
            self.assertEqual(payload["total_count"], 1)
            self.assertEqual(payload["items"][0]["checked"][0]["tool"], "calendar")
            self.assertEqual(payload["items"][0]["proof_log"]["metadata"]["api_key"], "[redacted]")

            detail = sage_proof_log_service.get_proof_log(
                workspace_id="ws-1",
                tenant_id="tenant-1",
                proof_id=record["proof_id"],
            )
            self.assertIsNotNone(detail)
            self.assertEqual(detail["summary"]["checked_count"], 1)

            summary = sage_proof_log_service.summarize_proof_logs(
                workspace_id="ws-1",
                tenant_id="tenant-1",
            )
            self.assertEqual(summary["total_count"], 1)
            self.assertEqual(summary["status_counts"], {"completed": 1})
            self.assertEqual(summary["checked_count"], 1)


if __name__ == "__main__":
    unittest.main()
