import asyncio
import types
import unittest
from unittest import mock

from fastapi import HTTPException

from server_modules import turn_ingress_service
from server_modules.agent_turn import AgentTurnRequest, TurnActor


class TurnIngressServiceRustGateTests(unittest.TestCase):
    def _turn_request(self) -> AgentTurnRequest:
        return AgentTurnRequest(
            tenant_id="tenant-1",
            workspace_id="ws-1",
            thread_id="thread-1",
            session_id="session-1",
            channel="web",
            actor=TurnActor(type="user", id="user-1", display_name="User 1"),
            message="hello",
            execution_mode="sync",
            response_mode="message",
            context_hints={"metadata": {"run_id": "run-1"}},
        )

    def test_start_turn_accepts_confirmation_next_action(self):
        turn_request = self._turn_request()
        with mock.patch.object(
            turn_ingress_service,
            "normalize_server_owned_turn_request",
            return_value=turn_request,
        ), mock.patch.object(
            turn_ingress_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "require_approval",
                "reason": "local_execution_requires_confirmation",
                "operation": "start_turn",
                "next_action": "request_local_execution_confirmation",
            },
        ) as rust_mock, mock.patch.object(
            turn_ingress_service,
            "agent_turn",
            new=mock.AsyncMock(return_value={"status": "ok"}),
        ) as agent_turn_mock:
            result = asyncio.run(
                turn_ingress_service.start_turn(
                    turn_request=turn_request,
                    current_user={"user_id": "user-1"},
                    run_execution_services={},
                    return_result_only=True,
                )
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(rust_mock.call_args.args[0], "run-api-decision")
        self.assertEqual(rust_mock.call_args.args[1]["operation"], "start_turn")
        self.assertEqual(rust_mock.call_args.args[1]["run_id"], "run-1")
        agent_turn_mock.assert_awaited_once()

    def test_start_turn_wrong_next_action_blocks_before_agent_turn(self):
        turn_request = self._turn_request()
        with mock.patch.object(
            turn_ingress_service,
            "normalize_server_owned_turn_request",
            return_value=turn_request,
        ), mock.patch.object(
            turn_ingress_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "turn_allowed",
                "operation": "start_turn",
                "next_action": "list_runs",
            },
        ), mock.patch.object(
            turn_ingress_service,
            "agent_turn",
            new=mock.AsyncMock(return_value={"status": "ok"}),
        ) as agent_turn_mock:
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(
                    turn_ingress_service.start_turn(
                        turn_request=turn_request,
                        current_user={"user_id": "user-1"},
                        run_execution_services={},
                        return_result_only=True,
                    )
                )

        self.assertEqual(raised.exception.status_code, 423)
        self.assertIn("unexpected next_action", str(raised.exception.detail))
        agent_turn_mock.assert_not_awaited()

    def test_start_run_start_accepts_selected_start_action(self):
        turn_request = self._turn_request()
        resolution = types.SimpleNamespace(
            request={"workflow_id": "wf-1", "workspace_id": "ws-1"},
            turn_request=turn_request,
        )
        with mock.patch.object(
            turn_ingress_service,
            "resolve_run_start_turn_request",
            return_value=resolution,
        ), mock.patch.object(
            turn_ingress_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "run_start_allowed",
                "operation": "start_run",
                "next_action": "start_run",
            },
        ) as rust_mock, mock.patch(
            "server_modules.run_service.execute_durable_turn_request",
            new=mock.AsyncMock(return_value={"result": {"ok": True}}),
        ) as durable_mock:
            result = asyncio.run(
                turn_ingress_service.start_run_start(
                    {"workflow_id": "wf-1"},
                    current_user={"user_id": "user-1"},
                    stamp_request_owner_fn=lambda request, current_user: request,
                    services={},
                )
            )

        self.assertTrue(result["ok"])
        self.assertEqual(rust_mock.call_args.args[0], "run-api-decision")
        self.assertEqual(rust_mock.call_args.args[1]["operation"], "start_run")
        durable_mock.assert_awaited_once()

    def test_start_run_start_wrong_next_action_blocks_before_durable_execution(self):
        turn_request = self._turn_request()
        resolution = types.SimpleNamespace(
            request={"workflow_id": "wf-1", "workspace_id": "ws-1"},
            turn_request=turn_request,
        )
        with mock.patch.object(
            turn_ingress_service,
            "resolve_run_start_turn_request",
            return_value=resolution,
        ), mock.patch.object(
            turn_ingress_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "run_start_allowed",
                "operation": "start_run",
                "next_action": "get_run",
            },
        ), mock.patch(
            "server_modules.run_service.execute_durable_turn_request",
            new=mock.AsyncMock(return_value={"result": {"ok": True}}),
        ) as durable_mock:
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(
                    turn_ingress_service.start_run_start(
                        {"workflow_id": "wf-1"},
                        current_user={"user_id": "user-1"},
                        stamp_request_owner_fn=lambda request, current_user: request,
                        services={},
                    )
                )

        self.assertEqual(raised.exception.status_code, 423)
        self.assertIn("unexpected next_action", str(raised.exception.detail))
        durable_mock.assert_not_awaited()
