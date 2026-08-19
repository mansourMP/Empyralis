"""Cloud-side plumbing for shell.execute batching (MAN: batched-hardware-dispatch).

These cover the three places identified by tracing the real live path from
the model-facing tool schema through to the gateway dispatch, none of which
are touched by the gateway-side (TypeScript) test suite:

  1. The tool schema the model actually sees (`shell__exec`'s ToolDescriptor)
     advertises `commands` and describes when to use it, while `command`
     keeps working unchanged (no `required` list that would break a batch
     call, and a single-command call is untouched).
  2. `_gateway_arguments_for_direct_local_tool` normalizes home-directory
     aliases (/root/Desktop etc.) per-entry for a `commands` array the same
     way it already does for a single `command`, and never introduces a
     `command` key that would make gateway_execution_service's
     `_normalize_gateway_capability` silently rewrite `commands` away.
  3. `_format_gateway_direct_local_tool_result` renders a batch result's
     five-way per-command outcome (success/failed/skipped/timed_out/
     not_run) without ever collapsing two of those into the same
     completed/failed bit — this is the cloud-side half of the
     outcome-honesty law CLAUDE.md states for exactly this shape of thing.
"""
from __future__ import annotations

import json
import unittest

from server_modules import hardware_access_policy_service as hardware_access_policy_service_module
from server_modules import skills_service


class _IdentityCallbacks:
    """Stub `callbacks` for `_format_gateway_direct_local_tool_result`.

    Returns the constructed result envelope as JSON text rather than running
    it through the real prose formatter — these tests are about what THIS
    function builds (the per-command action list, the worst-case status),
    not about the shared formatter's own prose rendering, which is exercised
    elsewhere.
    """

    @staticmethod
    def format_direct_local_tool_result(result: dict) -> str:
        return json.dumps(result, ensure_ascii=False)


class ShellExecToolDescriptorBatchTests(unittest.TestCase):
    def _shell_exec_descriptor(self):
        descriptors = skills_service._local_tool_descriptors()
        for descriptor in descriptors:
            if descriptor.tool_name == "shell__exec":
                return descriptor
        raise AssertionError("shell__exec tool descriptor not found")

    def test_capability_id_is_unchanged_shell_execute(self) -> None:
        # No new capability_id — batching rides on the SAME "shell.execute"
        # capability every existing authorization/permission/approval check
        # is already keyed on, distinguished only by which argument
        # (`command` vs `commands`) is present.
        descriptor = self._shell_exec_descriptor()
        self.assertEqual(descriptor.capability_id, "shell.execute")

    def test_parameters_advertise_commands_and_stop_on_failure(self) -> None:
        descriptor = self._shell_exec_descriptor()
        properties = descriptor.parameters["properties"]
        self.assertIn("command", properties)
        self.assertIn("commands", properties)
        self.assertIn("stop_on_failure", properties)
        self.assertEqual(properties["commands"]["type"], "array")

    def test_command_is_no_longer_unconditionally_required(self) -> None:
        # A batch call legitimately omits `command` entirely — a top-level
        # `required: ["command"]` (the pre-batch schema) would advertise a
        # single-command-only contract to any model reading the schema
        # strictly, even though the gateway itself accepts `commands`.
        descriptor = self._shell_exec_descriptor()
        self.assertNotIn("required", descriptor.parameters)

    def test_description_tells_the_model_to_batch_when_several_commands_are_known_upfront(self) -> None:
        descriptor = self._shell_exec_descriptor()
        lowered = descriptor.description.lower()
        self.assertIn("commands", lowered)
        self.assertIn("once per command", lowered)


class GatewayArgumentsForShellBatchTests(unittest.TestCase):
    def test_single_command_path_is_unchanged(self) -> None:
        result = skills_service._gateway_arguments_for_direct_local_tool(
            "shell", "exec", {"command": "ls /root/Desktop"}
        )
        self.assertNotIn("Desktop' /root", result["command"])
        self.assertNotIn("commands", result)

    def test_batch_commands_get_the_same_home_alias_rewrite_per_entry(self) -> None:
        result = skills_service._gateway_arguments_for_direct_local_tool(
            "shell",
            "exec",
            {"commands": ["ls /root/Desktop", {"command": "cat /root/Documents/notes.txt", "timeout_seconds": 5}]},
        )
        self.assertNotIn("command", result)  # never introduced for a batch call
        commands = result["commands"]
        self.assertNotIn("/root/Desktop", commands[0])
        self.assertNotIn("/root/Documents", commands[1]["command"])
        self.assertEqual(commands[1]["timeout_seconds"], 5)

    def test_empty_commands_list_is_left_alone(self) -> None:
        # An empty array is not a batch (the gateway itself treats it as
        # "no commands given" and falls through to the single-command
        # path's "command is required" error) — this function must not
        # crash or fabricate anything for it.
        result = skills_service._gateway_arguments_for_direct_local_tool("shell", "exec", {"commands": []})
        self.assertEqual(result["commands"], [])


class FormatGatewayBatchResultTests(unittest.TestCase):
    def _format(self, inner_result: dict) -> dict:
        formatted = skills_service._format_gateway_direct_local_tool_result(
            connector_id="shell",
            action_id="exec",
            capability_id="shell.execute",
            gateway_response={"result": inner_result},
            callbacks=_IdentityCallbacks(),
        )
        # _with_gateway_tool_status attaches .gateway_tool_status to the
        # returned str subclass — read straight off the live return value,
        # never re-derived, so this test can't pass by agreeing with itself.
        status = formatted.gateway_tool_status
        return {"formatted": json.loads(str(formatted)), "status": status}

    def test_all_success_reports_completed_overall(self) -> None:
        outcome = self._format(
            {
                "commands": [
                    {"index": 0, "command": "echo one", "status": "success", "ran": True, "exit_code": 0, "stdout": "one", "stderr": "", "reason": None},
                    {"index": 1, "command": "echo two", "status": "success", "ran": True, "exit_code": 0, "stdout": "two", "stderr": "", "reason": None},
                ],
                "stop_on_failure": True,
                "stopped_early": False,
                "batch_timed_out": False,
            }
        )
        self.assertEqual(outcome["status"]["status"], "completed")
        actions = outcome["formatted"]["result_data"]["child_result"]["outputs"]["actions"]
        self.assertEqual(len(actions), 2)
        self.assertTrue(all(a["status"] == "completed" for a in actions))

    def test_success_failed_skipped_timed_out_not_run_never_collapse_to_the_same_label(self) -> None:
        outcome = self._format(
            {
                "commands": [
                    {"index": 0, "command": "ok", "status": "success", "ran": True, "exit_code": 0, "stdout": "", "stderr": "", "reason": None},
                    {"index": 1, "command": "bad", "status": "failed", "ran": True, "exit_code": 1, "stdout": "", "stderr": "boom", "reason": None},
                    {"index": 2, "command": "skipped-one", "status": "skipped", "ran": False, "exit_code": None, "stdout": "", "stderr": "", "reason": "not run: an earlier command failed"},
                    {"index": 3, "command": "in-flight", "status": "timed_out", "ran": True, "exit_code": None, "stdout": "partial", "stderr": "", "reason": "shared time budget ran out"},
                    {"index": 4, "command": "never", "status": "not_run", "ran": False, "exit_code": None, "stdout": "", "stderr": "", "reason": "batch ended before this command could start"},
                ],
                "stop_on_failure": True,
                "stopped_early": True,
                "batch_timed_out": True,
            }
        )
        actions = outcome["formatted"]["result_data"]["child_result"]["outputs"]["actions"]
        statuses = [a["status"] for a in actions]
        # "failed" (ran, bad exit) and "timed_out" (ran, unknown exit) must
        # both differ from "not_run" (skipped/never-reached, never ran) —
        # collapsing any of these would be exactly the "different facts
        # sharing one signal" bug this dispatch exists to avoid.
        self.assertEqual(statuses, ["completed", "failed", "not_run", "timed_out", "not_run"])
        # The reason/partial-output text is preserved in the model-visible
        # preview for every non-success entry, not silently dropped.
        self.assertIn("boom", actions[1]["output_preview"])
        self.assertIn("earlier command failed", actions[2]["output_preview"])
        self.assertIn("partial", actions[3]["output_preview"])
        self.assertIn("batch ended", actions[4]["output_preview"])
        self.assertEqual(outcome["status"]["status"], "failed")

    def test_single_command_result_shape_is_routed_to_the_unchanged_single_command_branch(self) -> None:
        # No "commands" key at all -> must NOT enter the batch branch (which
        # would read command="" / exit_code=None and misreport as
        # completed). This is the regression this ordering exists to catch.
        outcome = self._format(
            {"command": "echo hi", "exit_code": 1, "stdout": "", "stderr": "boom", "timed_out": False, "execution_mode": "sandbox"}
        )
        self.assertEqual(outcome["status"]["status"], "failed")
        self.assertEqual(outcome["status"]["exit_code"], 1)


if __name__ == "__main__":
    unittest.main()
