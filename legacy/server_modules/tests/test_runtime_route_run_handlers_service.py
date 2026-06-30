import unittest
from unittest import mock

from fastapi import HTTPException

from server_modules import runtime_route_run_handlers_service


class _Payload:
    def __init__(self) -> None:
        self.validated = False

    def validate_fields(self) -> None:
        self.validated = True


class RuntimeRouteRunHandlersServiceTests(unittest.TestCase):
    def test_delegate_run_route_response_rejects_single_agent_mode(self):
        with self.assertRaises(HTTPException):
            runtime_route_run_handlers_service.delegate_run_route_response(
                "run-1",
                body=object(),
                current_user={"user_id": "user-1"},
                single_agent_mode=True,
                delegate_run_children_fn=lambda **kwargs: {},
                callbacks={},
            )

    def test_start_run_route_response_uses_default_request_class_when_body_missing(self):
        async def _start_response(request_payload, **kwargs):
            return request_payload

        async def _run():
            with mock.patch.object(
                runtime_route_run_handlers_service.rust_runtime_kernel_client,
                "run_runtime_kernel_enforced",
                return_value={"ok": True, "decision": "allow", "next_action": "create_or_route_run"},
            ) as rust_mock:
                payload = await runtime_route_run_handlers_service.start_run_route_response(
                    None,
                    current_user={"user_id": "user-1"},
                    run_start_request_class=lambda: {"default": True},
                    start_run_response_fn=_start_response,
                    execute_run_start_request_via_turn_runtime=lambda *args, **kwargs: None,
                    stamp_request_owner_fn=lambda *args, **kwargs: None,
                    run_execution_services=lambda: object(),
                )
            return payload, rust_mock

        payload, rust_mock = self._run_async(_run())
        self.assertEqual(payload, {"default": True})
        rust_mock.assert_called_once()
        self.assertEqual(rust_mock.call_args.args[0], "platform-orchestration-decision")
        self.assertEqual(rust_mock.call_args.args[1]["operation"], "run_create")

    def test_start_run_route_response_rust_denial_blocks_start_response(self):
        start_response = mock.AsyncMock(return_value={"run_id": "run-1"})
        denied = runtime_route_run_handlers_service.rust_runtime_kernel_client.RustKernelDecisionError(
            {
                "ok": False,
                "decision": "block",
                "reason": "actor_id_missing_for_mutation",
            },
            command="platform-orchestration-decision",
        )

        async def _run():
            with mock.patch.object(
                runtime_route_run_handlers_service.rust_runtime_kernel_client,
                "run_runtime_kernel_enforced",
                side_effect=denied,
            ):
                with self.assertRaises(HTTPException) as raised:
                    await runtime_route_run_handlers_service.start_run_route_response(
                        {"workspace_id": "ws-1"},
                        current_user={},
                        run_start_request_class=lambda: {"default": True},
                        start_run_response_fn=start_response,
                        execute_run_start_request_via_turn_runtime=lambda *args, **kwargs: None,
                        stamp_request_owner_fn=lambda *args, **kwargs: None,
                        run_execution_services=lambda: object(),
                    )
            return raised.exception

        exc = self._run_async(_run())
        self.assertEqual(exc.status_code, 423)
        self.assertEqual(exc.detail["reason"], "actor_id_missing_for_mutation")
        start_response.assert_not_awaited()

    def test_start_run_route_response_unexpected_next_action_blocks_start_response(self):
        start_response = mock.AsyncMock(return_value={"run_id": "run-1"})

        async def _run():
            with mock.patch.object(
                runtime_route_run_handlers_service.rust_runtime_kernel_client,
                "run_runtime_kernel_enforced",
                return_value={
                    "ok": True,
                    "decision": "allow",
                    "reason": "platform_orchestration_policy_satisfied",
                    "operation": "run_create",
                    "next_action": "allow_platform_read",
                },
            ):
                with self.assertRaises(HTTPException) as raised:
                    await runtime_route_run_handlers_service.start_run_route_response(
                        {"workspace_id": "ws-1"},
                        current_user={"user_id": "user-1"},
                        run_start_request_class=lambda: {"default": True},
                        start_run_response_fn=start_response,
                        execute_run_start_request_via_turn_runtime=lambda *args, **kwargs: None,
                        stamp_request_owner_fn=lambda *args, **kwargs: None,
                        run_execution_services=lambda: object(),
                    )
            return raised.exception

        exc = self._run_async(_run())
        self.assertEqual(exc.status_code, 423)
        self.assertEqual(exc.detail["reason"], "unexpected_next_action:allow_platform_read")
        start_response.assert_not_awaited()

    def test_submit_run_decision_route_response_validates_payload(self):
        payload = _Payload()
        result = runtime_route_run_handlers_service.submit_run_decision_route_response(
            "run-1",
            payload=payload,
            current_user={"user_id": "user-1"},
            submit_run_decision_fn=lambda run_id_str, **kwargs: {"run_id": run_id_str},
            run={},
            run_record={},
            callbacks={},
        )

        self.assertTrue(payload.validated)
        self.assertEqual(result, {"run_id": "run-1"})

    def test_resolve_run_approval_route_response_validates_payload(self):
        payload = _Payload()
        result = runtime_route_run_handlers_service.resolve_run_approval_route_response(
            "run-1",
            "approval-1",
            payload=payload,
            current_user={"user_id": "user-1"},
            resolve_run_approval_fn=lambda run_id_str, approval_id, **kwargs: {
                "run_id": run_id_str,
                "approval_id": approval_id,
            },
            run={},
            run_record={},
            callbacks={},
        )

        self.assertTrue(payload.validated)
        self.assertEqual(result, {"run_id": "run-1", "approval_id": "approval-1"})

    def test_replay_run_route_response_uses_run_id_string(self):
        result = runtime_route_run_handlers_service.replay_run_route_response(
            "run-1",
            replay_run_from_run_id_fn=lambda run_id, **kwargs: {
                "run_id": run_id,
                "has_payload": callable(kwargs["get_replay_payload"]),
            },
            get_replay_payload=lambda run_id: {"run_id": run_id},
            run_start_request_class=lambda **kwargs: kwargs,
            execute_system_run_start_request_via_turn_runtime=lambda *args, **kwargs: {},
            stamp_request_owner_fn=lambda *args, **kwargs: None,
            run_execution_services=lambda: object(),
        )

        self.assertEqual(result, {"run_id": "run-1", "has_payload": True})

    def _run_async(self, coroutine):
        import asyncio

        return asyncio.run(coroutine)


if __name__ == "__main__":
    unittest.main()
