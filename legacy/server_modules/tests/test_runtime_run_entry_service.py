import unittest
from unittest import mock

from fastapi import HTTPException

from server_modules import runtime_run_entry_service


class RuntimeRunEntryServiceTests(unittest.TestCase):
    def test_start_run_response_calls_rust_before_execution(self):
        calls = []
        captured = {}

        async def _execute(request_payload, **kwargs):
            calls.append("execute")
            captured["request_payload"] = request_payload
            return {"run_id": "run-1"}

        with mock.patch.object(
            runtime_run_entry_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=lambda command, payload, **kwargs: calls.append(command)
            or {
                "ok": True,
                "decision": "allow",
                "operation": "prepare_run_start",
                "next_action": "prepare_run_request",
                "engine": "workflow",
                "workflow_id": "wf-1",
                "agent_role": "researcher",
                "trust_mode": "strict",
            },
        ) as rust_mock:
            result = self._run_async(
                runtime_run_entry_service.start_run_response(
                    {
                        "engine": "orion",
                        "workspace_id": "ws-1",
                        "tenant_id": "tenant-1",
                        "metadata": {"trust_mode": "guarded"},
                    },
                    current_user={"user_id": "user-1"},
                    execute_run_start_request_via_turn_runtime=_execute,
                    stamp_request_owner_fn=lambda payload, current_user: payload,
                    run_execution_services=lambda: object(),
                )
            )

        self.assertEqual(result, {"run_id": "run-1"})
        self.assertEqual(calls, ["run-preparation-decision", "execute"])
        rust_mock.assert_called_once()
        self.assertEqual(rust_mock.call_args.args[1]["operation"], "prepare_run_start")
        self.assertEqual(rust_mock.call_args.args[1]["workspace_id"], "ws-1")
        self.assertEqual(captured["request_payload"]["engine"], "workflow")
        self.assertEqual(captured["request_payload"]["workflow_id"], "wf-1")
        self.assertEqual(captured["request_payload"]["metadata"]["agent_role"], "researcher")
        self.assertEqual(captured["request_payload"]["metadata"]["trust_mode"], "strict")

    def test_start_run_response_rust_denial_blocks_execution(self):
        denied = runtime_run_entry_service.rust_runtime_kernel_client.RustKernelDecisionError(
            {
                "ok": False,
                "decision": "block",
                "reason": "metadata_pack_inputs_must_be_object",
            },
            command="run-preparation-decision",
        )
        execute = mock.AsyncMock(return_value={"run_id": "run-1"})

        with mock.patch.object(
            runtime_run_entry_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=denied,
        ):
            with self.assertRaises(HTTPException) as raised:
                self._run_async(
                    runtime_run_entry_service.start_run_response(
                        {
                            "engine": "orion",
                            "workspace_id": "ws-1",
                            "tenant_id": "tenant-1",
                            "metadata": {"pack_inputs": ["bad"]},
                        },
                        current_user={"user_id": "user-1"},
                        execute_run_start_request_via_turn_runtime=execute,
                        stamp_request_owner_fn=lambda payload, current_user: payload,
                        run_execution_services=lambda: object(),
                    )
                )

        self.assertEqual(raised.exception.status_code, 423)
        self.assertEqual(raised.exception.detail["reason"], "metadata_pack_inputs_must_be_object")
        execute.assert_not_awaited()

    def test_start_run_response_unexpected_rust_next_action_blocks_execution(self):
        execute = mock.AsyncMock(return_value={"run_id": "run-1"})

        with mock.patch.object(
            runtime_run_entry_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "operation": "prepare_run_start",
                "next_action": "request_elevated_mode_approval",
            },
        ):
            with self.assertRaises(HTTPException) as raised:
                self._run_async(
                    runtime_run_entry_service.start_run_response(
                        {
                            "engine": "orion",
                            "workspace_id": "ws-1",
                            "tenant_id": "tenant-1",
                            "metadata": {},
                        },
                        current_user={"user_id": "user-1"},
                        execute_run_start_request_via_turn_runtime=execute,
                        stamp_request_owner_fn=lambda payload, current_user: payload,
                        run_execution_services=lambda: object(),
                    )
                )

        self.assertEqual(raised.exception.status_code, 423)
        self.assertEqual(raised.exception.detail["reason"], "unexpected_next_action:request_elevated_mode_approval")
        execute.assert_not_awaited()

    def test_preview_routing_response_shapes_preview_payload(self):
        payload = runtime_run_entry_service.preview_routing_response(
            {"engine": "orion"},
            build_run_routing_preview=lambda request_payload, services=None: {
                "engine": "orion",
                "metadata": {"agent_role": "researcher", "agent_role_source": "explicit"},
                "route": {"selected": "cloud"},
                "tool_policy_precheck": {"requires_approval": False},
            },
            run_routing_preview_services=lambda: object(),
        )

        self.assertEqual(payload["engine"], "orion")
        self.assertEqual(payload["agent_role"], "researcher")
        self.assertEqual(payload["route"], {"selected": "cloud"})

    def test_preview_routing_response_calls_rust_before_build(self):
        calls = []

        with mock.patch.object(
            runtime_run_entry_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=lambda command, payload, **kwargs: calls.append(command)
            or {
                "ok": True,
                "decision": "allow",
                "operation": "routing_preview",
                "next_action": "build_routing_preview",
            },
        ):
            payload = runtime_run_entry_service.preview_routing_response(
                {"engine": "orion", "workspace_id": "ws-1"},
                build_run_routing_preview=lambda request_payload, services=None: calls.append("preview")
                or {
                    "engine": "orion",
                    "metadata": {"agent_role": "researcher", "agent_role_source": "explicit"},
                    "route": {"selected": "cloud"},
                    "tool_policy_precheck": {"requires_approval": False},
                },
                run_routing_preview_services=lambda: object(),
            )

        self.assertEqual(calls, ["run-routing-decision", "preview"])
        self.assertEqual(payload["route"], {"selected": "cloud"})

    def test_precheck_run_response_includes_doctor_preflight(self):
        async def _build(request_payload, services=None):
            return {
                "engine": "orion",
                "metadata": {"agent_role": "researcher", "agent_role_source": "explicit"},
                "route": {"selected": "cloud"},
                "tool_policy_precheck": {"requires_approval": False},
                "doctor_preflight": {"ok": True},
            }

        payload = self._run_async(
            runtime_run_entry_service.precheck_run_response(
                {"engine": "orion"},
                build_run_precheck_result=_build,
                run_routing_preview_services=lambda: object(),
            )
        )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["doctor_preflight"], {"ok": True})

    def test_precheck_run_response_unexpected_rust_next_action_blocks_build(self):
        build = mock.AsyncMock(
            return_value={
                "engine": "orion",
                "metadata": {"agent_role": "researcher", "agent_role_source": "explicit"},
                "route": {"selected": "cloud"},
                "tool_policy_precheck": {"requires_approval": False},
                "doctor_preflight": {"ok": True},
            }
        )

        with mock.patch.object(
            runtime_run_entry_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "operation": "routing_preview",
                "next_action": "write_execution_boundary_metadata",
            },
        ):
            with self.assertRaises(HTTPException) as raised:
                self._run_async(
                    runtime_run_entry_service.precheck_run_response(
                        {"engine": "orion", "workspace_id": "ws-1"},
                        build_run_precheck_result=build,
                        run_routing_preview_services=lambda: object(),
                    )
                )

        self.assertEqual(raised.exception.status_code, 423)
        self.assertEqual(
            raised.exception.detail["reason"],
            "unexpected_next_action:write_execution_boundary_metadata",
        )
        build.assert_not_awaited()

    def test_stream_run_response_requires_existing_run(self):
        with mock.patch.object(
            runtime_run_entry_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={"ok": True, "decision": "allow", "operation": "get_run", "next_action": "get_run"},
        ):
            with self.assertRaises(HTTPException):
                runtime_run_entry_service.stream_run_response(
                    "run-1",
                    current_user={"user_id": "user-1"},
                    runs={},
                    get_live_run_fn=lambda run_id: None,
                    serialize_run_snapshot=lambda run_id, run: {},
                    enforce_run_owner_access=lambda current_user, snapshot: None,
                    event_source_response_class=lambda events: events,
                    iter_logs_for_run=lambda run_id: [],
                )

    def test_stream_run_response_enforces_access_and_wraps_event_source(self):
        seen = {}

        with mock.patch.object(
            runtime_run_entry_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={"ok": True, "decision": "allow", "operation": "get_run", "next_action": "get_run"},
        ) as rust_mock:
            payload = runtime_run_entry_service.stream_run_response(
                "run-1",
                current_user={"user_id": "user-1"},
                runs={"run-1": {"status": "running"}},
                get_live_run_fn=lambda run_id: {"run_id": run_id, "status": "running"},
                serialize_run_snapshot=lambda run_id, run: {"run_id": run_id},
                enforce_run_owner_access=lambda current_user, snapshot: seen.setdefault("snapshot", snapshot),
                event_source_response_class=lambda events: {"events": events},
                iter_logs_for_run=lambda run_id: [f"log:{run_id}"],
            )

        self.assertEqual(seen["snapshot"], {"run_id": "run-1"})
        self.assertEqual(payload, {"events": ["log:run-1"]})
        self.assertEqual(rust_mock.call_args.args[0], "run-api-decision")
        self.assertEqual(rust_mock.call_args.args[1]["operation"], "get_run")

    def test_stream_run_response_request_owner_approval_blocks_before_event_source(self):
        with mock.patch.object(
            runtime_run_entry_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "require_approval",
                "operation": "get_run",
                "reason": "owner_approval_required",
                "next_action": "request_owner_approval",
            },
        ):
            with self.assertRaises(HTTPException) as raised:
                runtime_run_entry_service.stream_run_response(
                    "run-1",
                    current_user={"user_id": "user-1"},
                    runs={"run-1": {"status": "running"}},
                    get_live_run_fn=lambda run_id: {"run_id": run_id, "status": "running"},
                    serialize_run_snapshot=lambda run_id, run: {"run_id": run_id},
                    enforce_run_owner_access=lambda current_user, snapshot: None,
                    event_source_response_class=lambda events: self.fail("event source should not be built"),
                    iter_logs_for_run=lambda run_id: [f"log:{run_id}"],
                )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("Rust run-api gate blocked get_run", str(raised.exception.detail))

    def test_stream_run_response_unexpected_next_action_blocks_before_event_source(self):
        with mock.patch.object(
            runtime_run_entry_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "operation": "get_run",
                "next_action": "pause_run",
            },
        ):
            with self.assertRaises(HTTPException) as raised:
                runtime_run_entry_service.stream_run_response(
                    "run-1",
                    current_user={"user_id": "user-1"},
                    runs={"run-1": {"status": "running"}},
                    get_live_run_fn=lambda run_id: {"run_id": run_id, "status": "running"},
                    serialize_run_snapshot=lambda run_id, run: {"run_id": run_id},
                    enforce_run_owner_access=lambda current_user, snapshot: None,
                    event_source_response_class=lambda events: self.fail("event source should not be built"),
                    iter_logs_for_run=lambda run_id: [f"log:{run_id}"],
                )

        self.assertEqual(raised.exception.status_code, 423)
        self.assertIn("unexpected next_action", str(raised.exception.detail))

    def _run_async(self, coroutine):
        import asyncio

        return asyncio.run(coroutine)


if __name__ == "__main__":
    unittest.main()
