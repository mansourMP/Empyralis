import unittest
from unittest.mock import patch

from server_modules import runs_execution


class RunsExecutionRustRuntimeTests(unittest.TestCase):
    def test_execution_outcome_helper_calls_rust_with_normalized_payload(self) -> None:
        calls = []

        def fake_rust(command, payload, **kwargs):
            calls.append((command, payload, kwargs))
            return {
                "ok": True,
                "decision": "allow",
                "reason": "execution_outcome_normalized",
                "status": "failed",
                "final_status": "failed",
                "summary": "temporary failure: connection reset",
                "event": "run_error",
                "log_level": "error",
                "record_patch": {
                    "result": "temporary failure: connection reset",
                    "status": "failed",
                    "execution_outcome": {
                        "final_status": "failed",
                        "retryable": True,
                    },
                },
                "retryable": True,
                "audit_visibility": "security",
                "cacheable": False,
            }

        with patch.object(
            runs_execution.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=fake_rust,
        ):
            outcome = runs_execution._normalize_execution_outcome_with_rust(
                exit_code=1,
                stderr="temporary failure: connection reset",
                artifacts=[{"id": "artifact-1"}],
            )

        self.assertEqual(outcome["retryable"], True)
        self.assertEqual(calls[0][0], "execution-outcome")
        self.assertEqual(calls[0][1]["exit_code"], 1)
        self.assertEqual(calls[0][1]["stderr"], "temporary failure: connection reset")
        self.assertEqual(calls[0][1]["artifacts"], [{"id": "artifact-1"}])
        self.assertEqual(outcome["record_patch"]["status"], "failed")

    def test_runtime_final_status_prefers_rust_outcome(self) -> None:
        self.assertEqual(
            runs_execution._runtime_final_status_from_outcome({"final_status": "cancelled"}, "failed"),
            "cancelled",
        )
        self.assertEqual(
            runs_execution._runtime_final_status_from_outcome({}, "failed"),
            "failed",
        )

    def test_runtime_outcome_summary_prefers_rust_outcome(self) -> None:
        self.assertEqual(
            runs_execution._runtime_outcome_summary({"summary": "Execution timed out."}, "fallback"),
            "Execution timed out.",
        )
        self.assertEqual(
            runs_execution._runtime_outcome_summary({}, "fallback"),
            "fallback",
        )

    def test_runtime_outcome_event_and_log_level_prefer_rust_outcome(self) -> None:
        self.assertEqual(
            runs_execution._runtime_outcome_event({"event": "timeout"}, "run_error"),
            "timeout",
        )
        self.assertEqual(
            runs_execution._runtime_outcome_log_level({"log_level": "warn"}, "error"),
            "warn",
        )

    def test_apply_execution_outcome_record_patch_preserves_existing_success_result(self) -> None:
        run = {"result": "Workflow completed.", "result_data": {"summary": "Workflow completed."}}
        outcome = {
            "record_patch": {
                "result": "Execution succeeded.",
                "status": "completed",
                "execution_outcome": {"final_status": "completed", "retryable": False},
            }
        }

        runs_execution._apply_execution_outcome_record_patch(run, outcome, preserve_existing_result=True)

        self.assertEqual(run["result"], "Workflow completed.")
        self.assertEqual(run["result_data"]["execution_outcome"]["final_status"], "completed")

    def test_apply_execution_outcome_record_patch_sets_failure_result(self) -> None:
        run = {}
        outcome = {
            "record_patch": {
                "result": "Execution failed.",
                "status": "failed",
                "execution_outcome": {"final_status": "failed", "retryable": False},
            }
        }

        runs_execution._apply_execution_outcome_record_patch(run, outcome)

        self.assertEqual(run["result"], "Execution failed.")
        self.assertEqual(run["result_data"]["execution_outcome"]["final_status"], "failed")

    def test_execution_runtime_helper_calls_rust_with_normalized_payload(self) -> None:
        calls = []

        def fake_rust(command, payload, **kwargs):
            calls.append((command, payload, kwargs))
            return {
                "ok": True,
                "decision": "allow",
                "reason": "execution_runtime_policy_satisfied",
                "operation": payload["operation"],
                "next_action": "execute_runtime_run",
                "audit_visibility": "standard",
                "approval_required": False,
                "cacheable": False,
            }

        run = {
            "status": "queued",
            "engine": "orion",
            "workspace_id": "ws-1",
            "context": {
                "metadata": {
                    "owner_user_id": "user-1",
                    "execution_target_selected": runs_execution.EXECUTION_TARGET_CLOUD,
                }
            },
        }

        with patch.object(
            runs_execution.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=fake_rust,
        ):
            decision = runs_execution._enforce_execution_runtime_decision(
                "run-1",
                operation="execute_run",
                run=run,
                timeout_seconds=60,
            )

        self.assertEqual(decision["decision"], "allow")
        self.assertEqual(calls[0][0], "execution-runtime-decision")
        self.assertEqual(calls[0][1]["run_id"], "run-1")
        self.assertEqual(calls[0][1]["workspace_id"], "ws-1")
        self.assertEqual(calls[0][1]["actor_id"], "user-1")
        self.assertEqual(calls[0][1]["operation"], "execute_run")
        self.assertEqual(calls[0][1]["engine_id"], "orion")
        self.assertEqual(calls[0][1]["execution_target"], runs_execution.EXECUTION_TARGET_CLOUD)
        self.assertEqual(calls[0][1]["timeout_seconds"], 60)

    def test_execution_runtime_helper_wrong_next_action_blocks(self) -> None:
        def fake_rust(_command, payload, **_kwargs):
            return {
                "ok": True,
                "decision": "allow",
                "reason": "execution_runtime_policy_satisfied",
                "operation": payload["operation"],
                "next_action": "dispatch_runtime_run",
                "audit_visibility": "standard",
                "approval_required": False,
                "cacheable": False,
            }

        run = {
            "status": "queued",
            "engine": "orion",
            "workspace_id": "ws-1",
            "context": {"metadata": {"owner_user_id": "user-1"}},
        }

        with patch.object(
            runs_execution.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=fake_rust,
        ):
            with self.assertRaisesRegex(RuntimeError, "unexpected_next_action:dispatch_runtime_run"):
                runs_execution._enforce_execution_runtime_decision(
                    "run-1",
                    operation="execute_run",
                    run=run,
                    timeout_seconds=60,
                )

    def test_terminal_status_helper_calls_rust_before_status_mutation(self) -> None:
        calls = []

        def fake_rust(command, payload, **kwargs):
            calls.append((command, payload, kwargs))
            return {
                "ok": True,
                "decision": "allow",
                "reason": "execution_runtime_policy_satisfied",
                "operation": payload["operation"],
                "next_action": "finalize_runtime_run",
                "normalized_status": "completed",
                "audit_visibility": "standard",
                "approval_required": False,
                "cacheable": False,
            }

        statuses = []

        with patch.object(
            runs_execution.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=fake_rust,
        ), patch.object(runs_execution, "set_run_status", side_effect=lambda run_id, status: statuses.append((run_id, status))):
            runs_execution._set_run_status_after_execution_runtime_decision(
                "run-1",
                "completed",
                run={
                    "status": "running",
                    "engine": "orion",
                    "workspace_id": "ws-1",
                    "context": {"metadata": {"actor_id": "user-1"}},
                },
            )

        self.assertEqual(calls[0][0], "execution-runtime-decision")
        self.assertEqual(calls[0][1]["operation"], "finalize_run")
        self.assertEqual(calls[0][1]["status"], "completed")
        self.assertEqual(statuses, [("run-1", "completed")])

    def test_terminal_status_helper_wrong_next_action_blocks_status_mutation(self) -> None:
        statuses = []

        def fake_rust(_command, payload, **_kwargs):
            return {
                "ok": True,
                "decision": "allow",
                "reason": "execution_runtime_policy_satisfied",
                "operation": payload["operation"],
                "next_action": "execute_runtime_run",
                "normalized_status": "completed",
                "audit_visibility": "standard",
                "approval_required": False,
                "cacheable": False,
            }

        with patch.object(
            runs_execution.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=fake_rust,
        ), patch.object(runs_execution, "set_run_status", side_effect=lambda run_id, status: statuses.append((run_id, status))):
            with self.assertRaisesRegex(RuntimeError, "unexpected_next_action:execute_runtime_run"):
                runs_execution._set_run_status_after_execution_runtime_decision(
                    "run-1",
                    "completed",
                    run={
                        "status": "running",
                        "engine": "orion",
                        "workspace_id": "ws-1",
                        "context": {"metadata": {"actor_id": "user-1"}},
                    },
                )

        self.assertEqual(statuses, [])

    def test_terminal_status_helper_uses_rust_normalized_status(self) -> None:
        statuses = []

        def fake_rust(_command, payload, **_kwargs):
            return {
                "ok": True,
                "decision": "allow",
                "reason": "execution_runtime_policy_satisfied",
                "operation": payload["operation"],
                "next_action": "finalize_runtime_run",
                "normalized_status": "timeout",
                "audit_visibility": "standard",
                "approval_required": False,
                "cacheable": False,
            }

        with patch.object(
            runs_execution.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=fake_rust,
        ), patch.object(runs_execution, "set_run_status", side_effect=lambda run_id, status: statuses.append((run_id, status))):
            runs_execution._set_run_status_after_execution_runtime_decision(
                "run-1",
                "timed_out",
                run={
                    "status": "running",
                    "engine": "orion",
                    "workspace_id": "ws-1",
                    "context": {"metadata": {"actor_id": "user-1"}},
                },
            )

        self.assertEqual(statuses, [("run-1", "timeout")])

    def test_execution_runtime_helper_passes_connector_and_metering_fields(self) -> None:
        calls = []

        def fake_rust(command, payload, **kwargs):
            calls.append((command, payload, kwargs))
            return {
                "ok": True,
                "decision": "allow",
                "reason": "execution_runtime_policy_satisfied",
                "operation": payload["operation"],
                "next_action": "execute_connector_action",
                "audit_visibility": "standard",
                "approval_required": False,
                "cacheable": False,
            }

        with patch.object(
            runs_execution.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=fake_rust,
        ):
            runs_execution._enforce_execution_runtime_decision(
                "run-1",
                operation="connector_action",
                context={"workspace_id": "ws-1", "metadata": {"actor_id": "user-1"}},
                workflow_node_id="node-1",
                connector_id="google_workspace",
                connector_action="send_email",
                idempotency_key="run-1:node-1:google_workspace:send_email",
                policy_decision="allow",
                external_write_requested=True,
            )

        self.assertEqual(calls[0][0], "execution-runtime-decision")
        self.assertEqual(calls[0][1]["operation"], "connector_action")
        self.assertEqual(calls[0][1]["workflow_node_id"], "node-1")
        self.assertEqual(calls[0][1]["execution_target"], runs_execution.EXECUTION_TARGET_CLOUD)
        self.assertEqual(calls[0][1]["connector_id"], "google_workspace")
        self.assertEqual(calls[0][1]["connector_action"], "send_email")
        self.assertEqual(calls[0][1]["idempotency_key"], "run-1:node-1:google_workspace:send_email")
        self.assertTrue(calls[0][1]["external_write_requested"])

    def test_execution_runtime_helper_preserves_explicit_local_connector_target(self) -> None:
        calls = []

        def fake_rust(command, payload, **kwargs):
            calls.append((command, payload, kwargs))
            return {
                "ok": True,
                "decision": "allow",
                "reason": "execution_runtime_policy_satisfied",
                "operation": payload["operation"],
                "next_action": "execute_connector_action",
                "audit_visibility": "standard",
                "approval_required": False,
                "cacheable": False,
            }

        with patch.object(
            runs_execution.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=fake_rust,
        ):
            runs_execution._enforce_execution_runtime_decision(
                "run-1",
                operation="connector_action",
                context={
                    "workspace_id": "ws-1",
                    "metadata": {
                        "actor_id": "user-1",
                        "execution_target_selected": runs_execution.EXECUTION_TARGET_LOCAL_COMPANION,
                    },
                },
                workflow_node_id="node-1",
                connector_id="telegram_personal",
                connector_action="send_message",
                idempotency_key="run-1:node-1:telegram_personal:send_message",
                policy_decision="allow",
                external_write_requested=True,
            )

        self.assertEqual(calls[0][1]["execution_target"], runs_execution.EXECUTION_TARGET_LOCAL_COMPANION)

    def test_execution_runtime_helper_passes_usage_metering_fields(self) -> None:
        calls = []

        def fake_rust(command, payload, **kwargs):
            calls.append((command, payload, kwargs))
            return {
                "ok": True,
                "decision": "allow",
                "reason": "execution_runtime_policy_satisfied",
                "operation": payload["operation"],
                "next_action": "record_usage_event",
                "audit_visibility": "standard",
                "approval_required": False,
                "cacheable": False,
            }

        with patch.object(
            runs_execution.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=fake_rust,
        ):
            runs_execution._enforce_execution_runtime_decision(
                "run-1",
                operation="usage_metering",
                context={"workspace_id": "ws-1", "metadata": {"actor_id": "user-1"}},
                workflow_node_id="node-1",
                connector_id="google_workspace",
                connector_action="send_email",
                usage_unit_count=1,
                metering_enabled=True,
            )

        self.assertEqual(calls[0][0], "execution-runtime-decision")
        self.assertEqual(calls[0][1]["operation"], "usage_metering")
        self.assertEqual(calls[0][1]["execution_target"], runs_execution.EXECUTION_TARGET_CLOUD)
        self.assertEqual(calls[0][1]["usage_unit_count"], 1)
        self.assertTrue(calls[0][1]["metering_enabled"])


if __name__ == "__main__":
    unittest.main()
