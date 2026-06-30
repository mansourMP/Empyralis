import unittest
from unittest import mock

from fastapi import HTTPException

from server_modules import run_service


def _request():
    return run_service.RunStartRequest(
        engine="orion",
        workspace_id="default",
        user_goal="create gated run",
        provider="openai",
        model="gpt-test",
        metadata={"owner_user_id": "user-1", "agent_role": "orchestrator"},
    )


def _prepared():
    return {
        "engine": "orion",
        "metadata": {"owner_user_id": "user-1", "agent_role": "orchestrator"},
        "workflow_snapshot": None,
    }


def _services(create_run):
    return run_service.PreparedRunCreationServices(
        decide_execution_target=lambda metadata, schedule_id=None: {"selected": "cloud"},
        apply_execution_route_metadata=lambda metadata, route: {
            **metadata,
            "execution_target_selected": route["selected"],
        },
        build_doctor_run_gate=lambda **kwargs: {"blocking": False},
        agent_machine_inherited_owner_user_id=lambda owner_user_id: owner_user_id,
        compute_tool_policy_precheck=lambda preview_context: {"blocked_count": 0},
        apply_browser_execution_metadata=lambda metadata: None,
        local_execution_block_prompt=lambda precheck: "blocked",
        resolve_runtime_policy_mode=lambda metadata, selected_target=None: {"policy_mode": "guarded"},
        agent_machine_full_trust_enabled=lambda owner_user_id: False,
        local_execution_requires_start_confirmation=lambda metadata, precheck: False,
        mark_local_execution_tools_approved=lambda metadata: None,
        precheck_human_action_labels=lambda precheck, decision="require_confirmation": [],
        local_execution_confirmation_prompt=lambda precheck: "confirm",
        begin_run_pending_confirmation=lambda *args, **kwargs: {"id": "approval-1"},
        create_run=create_run,
        load_created_run=lambda run_id: {"active_profile_id": "profile-1"},
        now_iso=lambda: "2026-06-01T00:00:00Z",
    )


class RunServiceCreateRustGateTests(unittest.TestCase):
    def test_create_run_execution_boundary_unexpected_next_action_blocks_before_persistence(self):
        create_run = mock.Mock(return_value="run-1")

        def _rust(command, payload, **kwargs):
            if command == "run-routing-decision":
                return {
                    "ok": True,
                    "decision": "allow",
                    "operation": "execution_boundary",
                    "next_action": "build_routing_preview",
                }
            raise AssertionError("run-service gate should not be reached")

        with mock.patch.object(
            run_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=_rust,
        ):
            with self.assertRaises(HTTPException) as raised:
                run_service.create_run_from_prepared_request(
                    _request(),
                    prepared=_prepared(),
                    services=_services(create_run),
                )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("unexpected_next_action:build_routing_preview", str(raised.exception.detail))
        create_run.assert_not_called()

    def test_create_run_denied_by_rust_before_persistence(self):
        create_run = mock.Mock(return_value="run-1")
        denied = run_service.rust_runtime_kernel_client.RustKernelDecisionError(
            {
                "ok": False,
                "decision": "block",
                "reason": "quota_exhausted",
            },
            command="run-service-decision",
        )

        with mock.patch.object(
            run_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=denied,
        ) as rust_mock:
            with self.assertRaises(HTTPException) as raised:
                run_service.create_run_from_prepared_request(
                    _request(),
                    prepared=_prepared(),
                    services=_services(create_run),
                )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("Rust run-service gate blocked create", str(raised.exception.detail))
        create_run.assert_not_called()
        rust_mock.assert_called_once()
        self.assertEqual(rust_mock.call_args.args[0], "run-service-decision")
        self.assertEqual(rust_mock.call_args.args[1]["operation"], "create")
        self.assertEqual(rust_mock.call_args.args[1]["actor_id"], "user-1")
        self.assertEqual(rust_mock.call_args.args[1]["execution_target"], "cloud")

    def test_create_run_requires_approval_before_enqueue_when_rust_demands_it(self):
        create_run = mock.Mock(return_value="run-1")
        services = _services(create_run)
        services.begin_run_pending_confirmation = mock.Mock(return_value={"id": "approval-1"})

        with mock.patch.object(
            run_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "requires_approval",
                "reason": "safe_mode_run_mutation",
                "next_action": "request_run_service_approval",
                "approval_required": True,
            },
        ):
            result = run_service.create_run_from_prepared_request(
                _request(),
                prepared=_prepared(),
                services=services,
            )

        self.assertEqual(result["run_id"], "run-1")
        self.assertEqual(result["status"], "waiting_for_input")
        self.assertEqual(result["pending_confirmation"], {"id": "approval-1"})
        self.assertTrue(result["metadata"]["run_service_waiting_approval"])
        create_run.assert_called_once()
        self.assertTrue(create_run.call_args.kwargs["defer_local_enqueue"])
        services.begin_run_pending_confirmation.assert_called_once()
        self.assertEqual(
            services.begin_run_pending_confirmation.call_args.kwargs["source"],
            "run_service_approval",
        )

    def test_create_run_unexpected_next_action_blocks_before_persistence(self):
        create_run = mock.Mock(return_value="run-1")

        with mock.patch.object(
            run_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "run_service_operation_allowed",
                "next_action": "dispatch_run_to_runtime",
                "approval_required": False,
            },
        ):
            with self.assertRaises(HTTPException) as raised:
                run_service.create_run_from_prepared_request(
                    _request(),
                    prepared=_prepared(),
                    services=_services(create_run),
                )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("unexpected_next_action:dispatch_run_to_runtime", str(raised.exception.detail))
        create_run.assert_not_called()

    def test_create_run_local_confirmation_unexpected_next_action_blocks_before_persistence(self):
        create_run = mock.Mock(return_value="run-1")
        prepared = {
            "engine": "orion",
            "metadata": {
                "owner_user_id": "user-1",
                "agent_role": "orchestrator",
                "outcome_pack": "local_execution",
            },
            "workflow_snapshot": None,
        }
        services = run_service.PreparedRunCreationServices(
            decide_execution_target=lambda metadata, schedule_id=None: {"selected": "local_companion"},
            apply_execution_route_metadata=lambda metadata, route: {
                **metadata,
                "execution_target_selected": route["selected"],
                "runtime_mode": "local_secure",
            },
            build_doctor_run_gate=lambda **kwargs: {"blocking": False},
            agent_machine_inherited_owner_user_id=lambda owner_user_id: owner_user_id,
            compute_tool_policy_precheck=lambda preview_context: {
                "blocked_count": 0,
                "require_confirmation_count": 1,
                "approval_required_count": 0,
            },
            apply_browser_execution_metadata=lambda metadata: None,
            local_execution_block_prompt=lambda precheck: "blocked",
            resolve_runtime_policy_mode=lambda metadata, selected_target=None: {"policy_mode": "guarded"},
            agent_machine_full_trust_enabled=lambda owner_user_id: False,
            local_execution_requires_start_confirmation=lambda metadata, precheck: True,
            mark_local_execution_tools_approved=lambda metadata: None,
            precheck_human_action_labels=lambda precheck, decision="require_confirmation": [],
            local_execution_confirmation_prompt=lambda precheck: "confirm",
            begin_run_pending_confirmation=lambda *args, **kwargs: {"id": "approval-1"},
            create_run=create_run,
            load_created_run=lambda run_id: {"active_profile_id": "profile-1"},
            now_iso=lambda: "2026-06-01T00:00:00Z",
        )

        def _rust(command, payload, **kwargs):
            if command == "run-routing-decision" and payload.get("operation") == "execution_boundary":
                return {
                    "ok": True,
                    "decision": "allow",
                    "operation": "execution_boundary",
                    "next_action": "write_execution_boundary_metadata",
                }
            if command == "run-routing-decision" and payload.get("operation") == "local_confirmation":
                return {
                    "ok": True,
                    "decision": "require_approval",
                    "operation": "local_confirmation",
                    "next_action": "merge_child_results",
                    "approval_required": True,
                }
            raise AssertionError("run-service gate should not be reached")

        with mock.patch.object(
            run_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=_rust,
        ):
            with self.assertRaises(HTTPException) as raised:
                run_service.create_run_from_prepared_request(
                    _request(),
                    prepared=prepared,
                    services=services,
                )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("unexpected_next_action:merge_child_results", str(raised.exception.detail))
        create_run.assert_not_called()

    def test_create_run_local_confirmation_rust_denial_blocks_before_persistence(self):
        create_run = mock.Mock(return_value="run-1")
        prepared = {
            "engine": "orion",
            "metadata": {
                "owner_user_id": "user-1",
                "agent_role": "orchestrator",
                "outcome_pack": "local_execution",
            },
            "workflow_snapshot": None,
        }
        services = run_service.PreparedRunCreationServices(
            decide_execution_target=lambda metadata, schedule_id=None: {"selected": "local_companion"},
            apply_execution_route_metadata=lambda metadata, route: {
                **metadata,
                "execution_target_selected": route["selected"],
                "runtime_mode": "local_secure",
            },
            build_doctor_run_gate=lambda **kwargs: {"blocking": False},
            agent_machine_inherited_owner_user_id=lambda owner_user_id: owner_user_id,
            compute_tool_policy_precheck=lambda preview_context: {
                "blocked_count": 0,
                "require_confirmation_count": 1,
                "approval_required_count": 0,
            },
            apply_browser_execution_metadata=lambda metadata: None,
            local_execution_block_prompt=lambda precheck: "blocked",
            resolve_runtime_policy_mode=lambda metadata, selected_target=None: {"policy_mode": "guarded"},
            agent_machine_full_trust_enabled=lambda owner_user_id: False,
            local_execution_requires_start_confirmation=lambda metadata, precheck: True,
            mark_local_execution_tools_approved=lambda metadata: None,
            precheck_human_action_labels=lambda precheck, decision="require_confirmation": [],
            local_execution_confirmation_prompt=lambda precheck: "confirm",
            begin_run_pending_confirmation=lambda *args, **kwargs: {"id": "approval-1"},
            create_run=create_run,
            load_created_run=lambda run_id: {"active_profile_id": "profile-1"},
            now_iso=lambda: "2026-06-01T00:00:00Z",
        )

        denied = run_service.rust_runtime_kernel_client.RustKernelDecisionError(
            {
                "ok": False,
                "decision": "block",
                "reason": "local_execution_precheck_blocked",
            },
            command="run-routing-decision",
        )

        def _rust(command, payload, **kwargs):
            if command == "run-routing-decision" and payload.get("operation") == "execution_boundary":
                return {
                    "ok": True,
                    "decision": "allow",
                    "operation": "execution_boundary",
                    "next_action": "write_execution_boundary_metadata",
                }
            if command == "run-routing-decision" and payload.get("operation") == "local_confirmation":
                raise denied
            raise AssertionError("run-service gate should not be reached")

        with mock.patch.object(
            run_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=_rust,
        ):
            with self.assertRaises(HTTPException) as raised:
                run_service.create_run_from_prepared_request(
                    _request(),
                    prepared=prepared,
                    services=services,
                )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("Rust run-routing gate blocked local_confirmation: local_execution_precheck_blocked", str(raised.exception.detail))
        create_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
