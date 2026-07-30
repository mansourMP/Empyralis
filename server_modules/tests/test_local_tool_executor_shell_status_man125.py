"""MAN-125: local_tool_executor.shell_execute must report its own honest
status.

Before this fix, shell_execute's return dict hardcoded "status": "completed"
on every non-timeout, non-exception path -- regardless of result.returncode
-- while the SAME function, three lines earlier, already computed the
correct value ("completed" if result.returncode == 0 else "failed") to feed
ledger_audit.record_shell_exec. The audit ledger got the truth; the caller
(and, downstream, anything reading `result["status"]` instead of/in addition
to `result["exit_code"]`) got told "completed" for a command that failed.

This is a real internal inconsistency independent of any downstream
classifier: a single dict claiming both "exit_code": 1 and "status":
"completed" is self-contradictory on its face. Real Postgres/network access
is not needed -- shell_execute takes no pool/connection, so this is a pure
subprocess-level unit test, no mocking of the executor itself required.
"""

from __future__ import annotations

import unittest

from server_modules import local_tool_executor


class ShellExecuteStatusTests(unittest.TestCase):
    def test_zero_exit_code_reports_completed(self) -> None:
        result = local_tool_executor.shell_execute("exit 0")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["status"], "completed")

    def test_nonzero_exit_code_reports_failed(self) -> None:
        result = local_tool_executor.shell_execute("exit 7")
        self.assertEqual(result["exit_code"], 7)
        self.assertEqual(result["status"], "failed")

    def test_command_writing_to_stderr_and_failing_reports_failed(self) -> None:
        result = local_tool_executor.shell_execute("echo boom 1>&2; exit 1")
        self.assertEqual(result["exit_code"], 1)
        self.assertEqual(result["status"], "failed")
        self.assertIn("boom", result["stderr"])

    def test_status_and_exit_code_never_disagree(self) -> None:
        """The specific self-contradiction this fix removes: status must
        never claim "completed" while exit_code is nonzero, for any exit
        code the shell returns."""
        for code in (0, 1, 2, 127, 255):
            with self.subTest(code=code):
                result = local_tool_executor.shell_execute(f"exit {code}")
                if code == 0:
                    self.assertEqual(result["status"], "completed")
                else:
                    self.assertEqual(result["status"], "failed")

    def test_missing_command_is_unaffected_by_this_fix(self) -> None:
        """Regression guard: the empty-command short-circuit (a distinct
        branch, above the subprocess.run call this fix touches) is untouched
        -- still exit_code 1 with no "status" key at all, exactly as before."""
        result = local_tool_executor.shell_execute("   ")
        self.assertEqual(result["exit_code"], 1)
        self.assertNotIn("status", result)

    def test_timeout_path_is_unaffected_by_this_fix(self) -> None:
        """Regression guard: the TimeoutExpired branch already reported its
        own honest "status": "timeout" before this fix and still does."""
        result = local_tool_executor.shell_execute("sleep 5", timeout=1)
        self.assertEqual(result["status"], "timeout")
        self.assertEqual(result["exit_code"], -1)


if __name__ == "__main__":
    unittest.main()
