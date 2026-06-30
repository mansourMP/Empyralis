import unittest

from server_modules import runtime_run_detail_service


class RuntimeRunDetailServiceTests(unittest.TestCase):
    def test_can_view_sensitive_run_payload_accepts_api_key_and_admin(self):
        self.assertTrue(runtime_run_detail_service.can_view_sensitive_run_payload({"auth_type": "api_key"}))
        self.assertTrue(runtime_run_detail_service.can_view_sensitive_run_payload({"is_admin": True}))
        self.assertFalse(runtime_run_detail_service.can_view_sensitive_run_payload({"auth_type": "session"}))

    def test_limited_result_data_view_trims_execution_summary(self):
        payload = runtime_run_detail_service.limited_result_data_view(
            {
                "summary": "done",
                "pack_id": "pack-1",
                "execution_summary": {
                    "risk_level": "medium",
                    "next_action": "approve",
                    "approval_required": True,
                    "approval_reason": "writes data",
                    "estimated_time_saved_minutes": 12,
                    "ignored": "value",
                },
            }
        )

        self.assertEqual(payload["summary"], "done")
        self.assertNotIn("ignored", payload["execution_summary"])

    def test_build_archived_run_detail_response_sets_archived_and_route(self):
        payload = runtime_run_detail_service.build_archived_run_detail_response(
            run_id="run-1",
            snapshot={
                "status": "completed",
                "execution_target_requested": "cloud",
                "execution_target_selected": "cloud",
                "fallback_reason": "provider_unavailable",
                "events": [],
            },
            metadata={"agent_role": "builder"},
            include_sensitive=False,
            safe_context={"workspace_id": "default"},
            parent_run=None,
            child_runs=[],
            delegation_summary={},
            connector_binding={},
            limited_result_data_view_fn=lambda value: {"summary": "trimmed"},
            limited_node_states_view_fn=lambda value: {"trimmed": True},
        )

        self.assertTrue(payload["archived"])
        self.assertEqual(payload["route"]["requested"], "cloud")
        self.assertEqual(payload["fallback_reason"], "provider_unavailable")
        self.assertEqual(payload["diagnostics"]["category"], "completed")

    def test_build_live_run_detail_response_uses_trimmed_memory_and_pending_confirmation(self):
        payload = runtime_run_detail_service.build_live_run_detail_response(
            run_id="run-1",
            run={
                "status": "running",
                "machine_id": "machine-7",
                "result_data": {"secret": True},
                "memory_trace": {"raw": True},
                "interrupt_requested": True,
                "interrupt_reason": "Operator stop",
                "interrupt_scope": "machine",
                "interrupt_requested_at": "2026-04-08T12:00:00Z",
                "tool_policy_audit": [{"event": "audit"}],
            },
            snapshot={},
            metadata={"owner_user_id": "user-1", "execution_target_requested": "local", "runtime_id": "runtime-7"},
            include_sensitive=True,
            safe_context={"workspace_id": "default"},
            parent_run=None,
            child_runs=[],
            delegation_summary={},
            connector_binding={},
            limited_result_data_view_fn=lambda value: {"summary": "trimmed"},
            limited_node_states_view_fn=lambda value: {"trimmed": True},
            trim_memory_trace_fn=lambda value: {"trimmed": bool(value)},
            get_pending_confirmation_fn=lambda run: {"approval_id": "approval-1"},
        )

        self.assertFalse(payload["archived"])
        self.assertEqual(payload["memory_trace"], {"trimmed": True})
        self.assertEqual(payload["pending_confirmation"]["approval_id"], "approval-1")
        self.assertEqual(payload["machine_id"], "machine-7")
        self.assertEqual(payload["runtime_id"], "runtime-7")
        self.assertTrue(payload["interrupt_requested"])
        self.assertEqual(payload["interrupt_reason"], "Operator stop")
        self.assertEqual(payload["interrupt_scope"], "machine")
        self.assertEqual(payload["interrupt_requested_at"], "2026-04-08T12:00:00Z")
        self.assertEqual(payload["diagnostics"]["category"], "approval_wait")

    def test_build_live_run_detail_response_sets_local_waiting_diagnostics(self):
        payload = runtime_run_detail_service.build_live_run_detail_response(
            run_id="run-local-1",
            run={
                "status": "queued_local",
                "local_last_heartbeat_at": "2026-04-06T09:00:00Z",
                "tool_policy_audit": [],
            },
            snapshot={
                "status": "queued_local",
                "events": [],
                "retry_sequence": 2,
                "retry_of_run_id": "run-root-1",
            },
            metadata={
                "owner_user_id": "user-1",
                "execution_target_selected": "local_companion",
                "execution_target_waiting_for_capacity": True,
                "execution_target_reason": "Local worker is online but busy.",
                "scheduled": True,
                "schedule_id": "schedule-1",
            },
            include_sensitive=True,
            safe_context={"workspace_id": "default"},
            parent_run=None,
            child_runs=[],
            delegation_summary={},
            connector_binding={},
            limited_result_data_view_fn=lambda value: {"summary": "trimmed"},
            limited_node_states_view_fn=lambda value: {"trimmed": True},
            trim_memory_trace_fn=lambda value: {"trimmed": bool(value)},
            get_pending_confirmation_fn=lambda run: None,
        )

        self.assertEqual(payload["diagnostics"]["category"], "local_capacity_wait")
        self.assertEqual(payload["diagnostics"]["blocked_on"], "local_capacity")
        self.assertTrue(payload["diagnostics"]["scheduled"])
        self.assertEqual(payload["diagnostics"]["schedule_id"], "schedule-1")
        self.assertEqual(payload["diagnostics"]["retry_sequence"], 2)
        self.assertEqual(payload["diagnostics"]["local_last_heartbeat_at"], "2026-04-06T09:00:00Z")

    def test_build_archived_run_detail_response_sets_failure_diagnostics(self):
        payload = runtime_run_detail_service.build_archived_run_detail_response(
            run_id="run-failed-1",
            snapshot={
                "status": "failed",
                "result_summary": "Provider authentication failed.",
                "scheduled": True,
                "schedule_id": "schedule-nightly",
                "events": [
                    {"event": "run_error", "message": "Provider authentication failed."},
                ],
            },
            metadata={"agent_role": "builder"},
            include_sensitive=False,
            safe_context={"workspace_id": "default"},
            parent_run=None,
            child_runs=[],
            delegation_summary={},
            connector_binding={},
            limited_result_data_view_fn=lambda value: {"summary": "trimmed"},
            limited_node_states_view_fn=lambda value: {"trimmed": True},
        )

        self.assertEqual(payload["diagnostics"]["category"], "failure")
        self.assertEqual(payload["diagnostics"]["failure_event"], "run_error")
        self.assertEqual(payload["diagnostics"]["failure_message"], "Provider authentication failed.")
        self.assertTrue(payload["diagnostics"]["scheduled"])

    def test_build_archived_run_detail_response_surfaces_machine_interrupt_fields(self):
        payload = runtime_run_detail_service.build_archived_run_detail_response(
            run_id="run-archived-1",
            snapshot={
                "status": "failed",
                "context": {
                    "metadata": {
                        "runtime_id": "runtime-archived-1",
                        "machine_id": "machine-archived-1",
                        "interrupt_requested": True,
                        "interrupt_reason": "Machine kill",
                        "interrupt_scope": "machine",
                    }
                },
                "events": [],
            },
            metadata={"agent_role": "builder"},
            include_sensitive=False,
            safe_context={"workspace_id": "default"},
            parent_run=None,
            child_runs=[],
            delegation_summary={},
            connector_binding={},
            limited_result_data_view_fn=lambda value: {"summary": "trimmed"},
            limited_node_states_view_fn=lambda value: {"trimmed": True},
        )

        self.assertEqual(payload["machine_id"], "machine-archived-1")
        self.assertEqual(payload["runtime_id"], "runtime-archived-1")
        self.assertTrue(payload["interrupt_requested"])
        self.assertEqual(payload["interrupt_reason"], "Machine kill")
        self.assertEqual(payload["interrupt_scope"], "machine")

    def test_build_run_browser_checkpoint_payload_hides_raw_checkpoint_for_non_sensitive_view(self):
        payload = runtime_run_detail_service.build_run_browser_checkpoint_payload(
            run_id="run-browser-1",
            source={
                "browser_checkpoint": {
                    "next_action_index": 5,
                    "session_profile": "qa-browser",
                    "tabs": [{"id": 1}],
                }
            },
            metadata={
                "browser_reviewed_approval_required": True,
                "browser_immutable_plan_hash": "hash-1",
            },
            include_sensitive=False,
            archived=False,
        )

        self.assertTrue(payload["available"])
        self.assertTrue(payload["browser_resume_supported"])
        self.assertEqual(payload["checkpoint_summary"]["next_action_index"], 5)
        self.assertEqual(payload["session_profile"], "qa-browser")
        self.assertIsNone(payload["checkpoint"])
        self.assertTrue(payload["browser_reviewed_approval_required"])
        self.assertEqual(payload["browser_immutable_plan_hash"], "hash-1")

    def test_build_run_browser_session_payload_prefers_introspection_snapshot(self):
        payload = runtime_run_detail_service.build_run_browser_session_payload(
            run_id="run-browser-2",
            source={
                "browser_checkpoint": {
                    "next_action_index": 2,
                    "session_profile": "qa-browser",
                },
                "browser_introspection": {
                    "tabs": [{"id": 7}],
                    "console_entries": [{"message": "warn"}],
                },
                "runtime_id": "runtime-2",
                "machine_id": "machine-2",
            },
            metadata={
                "browser_execution_binding": {"cwd": "/tmp/project"},
                "browser_reviewed_approval_required": True,
            },
            include_sensitive=True,
            archived=True,
        )

        self.assertTrue(payload["available"])
        self.assertEqual(payload["checkpoint_summary"]["next_action_index"], 2)
        self.assertEqual(payload["session_profile"], "qa-browser")
        self.assertEqual(payload["browser_introspection"]["tabs"][0]["id"], 7)
        self.assertEqual(payload["browser_execution_binding"]["cwd"], "/tmp/project")
        self.assertEqual(payload["machine_id"], "machine-2")
        self.assertEqual(payload["runtime_id"], "runtime-2")


if __name__ == "__main__":
    unittest.main()
