import unittest

from server_modules import runtime_route_registration_service


class RuntimeRouteBindingServiceTests(unittest.TestCase):
    def test_build_runtime_route_bindings_reuses_expected_callbacks(self):
        markers = {
            "_serialize_run_snapshot": object(),
            "_lookup_run_snapshot": object(),
            "_build_delegated_run_request": object(),
            "_normalize_run_id_token": object(),
            "_refresh_parent_delegation_state": object(),
            "_build_auto_delegation_plan": object(),
            "_emit_auto_delegation_routing_log": object(),
            "_find_run_relationships": object(),
            "_clear_pending_confirmation": object(),
            "_begin_run_pending_confirmation": object(),
        }

        bindings = runtime_route_registration_service.build_runtime_route_bindings(
            late_server_export=lambda name: markers[name],
            enforce_run_owner_access=lambda *args, **kwargs: None,
            normalize_agent_role=lambda value: str(value or ""),
            execute_system_run_start_request_via_turn_runtime=lambda *args, **kwargs: {},
            stamp_request_owner_fn=lambda *args, **kwargs: None,
            run_execution_services=lambda: "services",
            can_view_sensitive_run_payload=lambda *args, **kwargs: True,
            limited_run_context_view=lambda payload: payload,
            limited_result_data_view_fn=lambda payload: payload,
            get_pending_confirmation_fn=lambda run: run.get("pending"),
            build_archived_run_detail_response=lambda *args, **kwargs: {},
            build_live_run_detail_response=lambda *args, **kwargs: {},
            refresh_server_exports=lambda: None,
            run_history_lock=object(),
            run_history=[],
            history_item_matches=lambda *args, **kwargs: True,
            current_user_is_privileged=lambda current_user: False,
            extract_run_owner_user_id=lambda payload: payload.get("user_id"),
            summarize_history_item=lambda *args, **kwargs: {},
            parse_utc_ts=lambda value: value,
            build_retry_child_payload=lambda child, relationship: {},
            approval_correlation_id=lambda *args, **kwargs: "corr",
            append_approval_audit=lambda *args, **kwargs: None,
            resolve_local_execution_start_approval=lambda *args, **kwargs: {},
            set_pending_confirmation=lambda run, pending: None,
            utc_now=lambda: "now",
            utc_now_iso=lambda: "now-iso",
            run_thread_is_alive=lambda run: False,
            emit_log=lambda *args, **kwargs: None,
            schedule_restored_run_resume=lambda run_id, run: True,
        )

        self.assertIs(bindings.serialize_run_snapshot, markers["_serialize_run_snapshot"])
        self.assertIs(
            bindings.delegate_run_children_callbacks["lookup_run_snapshot"],
            markers["_lookup_run_snapshot"],
        )
        self.assertIs(
            bindings.auto_delegate_run_children_callbacks["build_auto_delegation_plan"],
            markers["_build_auto_delegation_plan"],
        )
        self.assertIs(
            bindings.retry_failed_delegation_callbacks["find_run_relationships"],
            markers["_find_run_relationships"],
        )
        self.assertIs(
            bindings.submit_run_decision_callbacks["serialize_run_snapshot"],
            markers["_serialize_run_snapshot"],
        )
        self.assertIs(
            bindings.resolve_run_approval_callbacks["serialize_run_snapshot"],
            markers["_serialize_run_snapshot"],
        )
        self.assertIs(
            bindings.resume_waiting_run_callbacks["serialize_run_snapshot"],
            markers["_serialize_run_snapshot"],
        )
        self.assertIs(
            bindings.resume_waiting_run_callbacks["begin_run_pending_confirmation"],
            markers["_begin_run_pending_confirmation"],
        )


if __name__ == "__main__":
    unittest.main()
