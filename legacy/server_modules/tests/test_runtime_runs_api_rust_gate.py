import unittest
from unittest import mock

from fastapi import HTTPException

from server_modules import runtime_runs_api


class RuntimeRunsApiRustGateTests(unittest.TestCase):
    def test_list_runs_accepts_rust_selected_list_action(self):
        with mock.patch.object(
            runtime_runs_api.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "run_api_list_allowed",
                "operation": "list_runs",
                "next_action": "list_runs",
            },
        ):
            decision = runtime_runs_api._enforce_run_api_decision(
                operation="list_runs",
                workspace_id="ws-1",
                tenant_id="tenant-1",
                user_role="viewer",
                body={"limit": 50},
            )

        self.assertEqual(decision["next_action"], "list_runs")

    def test_list_runs_unexpected_next_action_blocks(self):
        with mock.patch.object(
            runtime_runs_api.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "run_api_list_allowed",
                "operation": "list_runs",
                "next_action": "get_run",
            },
        ):
            with self.assertRaises(HTTPException) as raised:
                runtime_runs_api._enforce_run_api_decision(
                    operation="list_runs",
                    workspace_id="ws-1",
                    tenant_id="tenant-1",
                    user_role="viewer",
                    body={"limit": 50},
                )

        self.assertEqual(raised.exception.status_code, 423)
        self.assertIn("unexpected next_action", str(raised.exception.detail))

    def test_start_turn_accepts_rust_selected_confirmation_action(self):
        with mock.patch.object(
            runtime_runs_api.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "require_approval",
                "reason": "local_execution_requires_confirmation",
                "operation": "start_turn",
                "next_action": "request_local_execution_confirmation",
            },
        ):
            decision = runtime_runs_api._enforce_run_api_decision(
                operation="start_turn",
                workspace_id="ws-1",
                tenant_id="tenant-1",
                user_role="member",
                body={"input": "run this locally"},
            )

        self.assertEqual(decision["next_action"], "request_local_execution_confirmation")

    def test_start_turn_unexpected_next_action_blocks(self):
        with mock.patch.object(
            runtime_runs_api.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "run_api_start_allowed",
                "operation": "start_turn",
                "next_action": "start_run",
            },
        ):
            with self.assertRaises(HTTPException) as raised:
                runtime_runs_api._enforce_run_api_decision(
                    operation="start_turn",
                    workspace_id="ws-1",
                    tenant_id="tenant-1",
                    user_role="member",
                    body={"input": "hello"},
                )

        self.assertEqual(raised.exception.status_code, 423)
        self.assertIn("unexpected next_action", str(raised.exception.detail))

    def test_session_create_accepts_rust_selected_create_action(self):
        with mock.patch.object(
            runtime_runs_api.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "session_create_allowed",
                "operation": "create",
                "next_action": "create",
            },
        ):
            decision = runtime_runs_api._enforce_session_lifecycle_route_decision(
                operation="create",
                session_record={"session_id": "", "status": "new"},
            )

        self.assertEqual(decision["next_action"], "create")

    def test_session_close_unexpected_next_action_blocks(self):
        with mock.patch.object(
            runtime_runs_api.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "session_close_allowed",
                "operation": "close",
                "next_action": "continue",
            },
        ):
            with self.assertRaises(HTTPException) as raised:
                runtime_runs_api._enforce_session_lifecycle_route_decision(
                    operation="close",
                    session_record={"session_id": "session-1", "status": "active"},
                )

        self.assertEqual(raised.exception.status_code, 423)
        self.assertIn("unexpected next_action", str(raised.exception.detail))
