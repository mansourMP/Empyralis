import unittest
from unittest import mock

from fastapi import HTTPException

from server_modules import runtime_run_replay_service


class RuntimeRunReplayServiceTests(unittest.TestCase):
    def test_replay_item_response_wraps_payload(self):
        payload = runtime_run_replay_service.replay_item_response(item={"run_id": "run-1"})

        self.assertEqual(payload, {"item": {"run_id": "run-1"}})

    def test_replay_item_response_for_run_loads_payload(self):
        with mock.patch.object(
            runtime_run_replay_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "operation": "get_run",
                "next_action": "get_run",
            },
        ) as rust_mock:
            payload = runtime_run_replay_service.replay_item_response_for_run(
                "run-1",
                get_replay_payload=lambda run_id: {"run_id": run_id, "workspace_id": "ws-1", "tenant_id": "tenant-1"},
            )

        self.assertEqual(payload, {"item": {"run_id": "run-1", "workspace_id": "ws-1", "tenant_id": "tenant-1"}})
        self.assertEqual(rust_mock.call_args.args[0], "run-api-decision")
        self.assertEqual(rust_mock.call_args.args[1]["operation"], "get_run")

    def test_replay_item_response_for_run_request_owner_approval_blocks(self):
        with mock.patch.object(
            runtime_run_replay_service.rust_runtime_kernel_client,
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
                runtime_run_replay_service.replay_item_response_for_run(
                    "run-1",
                    get_replay_payload=lambda run_id: {"run_id": run_id, "workspace_id": "ws-1", "tenant_id": "tenant-1"},
                )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("Rust run-api gate blocked get_run", str(raised.exception.detail))

    def test_replay_run_from_item_requires_replay_request(self):
        with self.assertRaises(HTTPException):
            runtime_run_replay_service.replay_run_from_item(
                item={},
                run_start_request_class=lambda **kwargs: kwargs,
                execute_system_run_start_request_via_turn_runtime=lambda *args, **kwargs: {},
                stamp_request_owner_fn=lambda *args, **kwargs: None,
                run_execution_services=lambda: "services",
            )

    def test_replay_run_from_item_executes_turn_runtime(self):
        calls = {}

        with mock.patch.object(
            runtime_run_replay_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "operation": "start_run",
                "next_action": "start_run",
            },
        ) as rust_mock:
            payload = runtime_run_replay_service.replay_run_from_item(
                item={
                    "run_id": "run-0",
                    "workspace_id": "default",
                    "tenant_id": "tenant-1",
                    "replay_request": {"engine": "orion", "workspace_id": "default", "metadata": {"tenant_id": "tenant-1"}},
                },
                run_start_request_class=lambda **kwargs: {"request": kwargs},
                execute_system_run_start_request_via_turn_runtime=lambda request, **kwargs: calls.update(
                    {"request": request, **kwargs}
                )
                or {"run_id": "run-1"},
                stamp_request_owner_fn=lambda *args, **kwargs: None,
                run_execution_services=lambda: "services",
            )

        self.assertEqual(payload["run_id"], "run-1")
        self.assertEqual(calls["request"]["request"]["engine"], "orion")
        self.assertEqual(calls["services"], "services")
        self.assertEqual(rust_mock.call_args.args[1]["operation"], "start_run")

    def test_replay_run_from_item_confirmation_next_action_blocks_before_runtime(self):
        executed = {"called": False}
        with mock.patch.object(
            runtime_run_replay_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "require_approval",
                "operation": "start_run",
                "reason": "local_execution_requires_confirmation",
                "next_action": "request_local_execution_confirmation",
            },
        ):
            with self.assertRaises(HTTPException) as raised:
                runtime_run_replay_service.replay_run_from_item(
                    item={
                        "run_id": "run-0",
                        "workspace_id": "default",
                        "tenant_id": "tenant-1",
                        "replay_request": {"engine": "orion", "workspace_id": "default", "metadata": {"tenant_id": "tenant-1"}},
                    },
                    run_start_request_class=lambda **kwargs: {"request": kwargs},
                    execute_system_run_start_request_via_turn_runtime=lambda request, **kwargs: executed.update({"called": True}),
                    stamp_request_owner_fn=lambda *args, **kwargs: None,
                    run_execution_services=lambda: "services",
                )

        self.assertEqual(raised.exception.status_code, 423)
        self.assertIn("unexpected next_action", str(raised.exception.detail))
        self.assertFalse(executed["called"])

    def test_replay_run_from_run_id_loads_payload_and_executes(self):
        calls = {}

        with mock.patch.object(
            runtime_run_replay_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "operation": "start_run",
                "next_action": "start_run",
            },
        ):
            payload = runtime_run_replay_service.replay_run_from_run_id(
                "run-1",
                get_replay_payload=lambda run_id: {
                    "run_id": run_id,
                    "workspace_id": "default",
                    "tenant_id": "tenant-1",
                    "replay_request": {
                        "engine": "orion",
                        "workspace_id": "default",
                        "run_id": run_id,
                        "metadata": {"tenant_id": "tenant-1"},
                    },
                },
                run_start_request_class=lambda **kwargs: {"request": kwargs},
                execute_system_run_start_request_via_turn_runtime=lambda request, **kwargs: calls.update(
                    {"request": request, **kwargs}
                )
                or {"run_id": "run-2"},
                stamp_request_owner_fn=lambda *args, **kwargs: None,
                run_execution_services=lambda: "services",
            )

        self.assertEqual(payload["run_id"], "run-2")
        self.assertEqual(calls["request"]["request"]["run_id"], "run-1")


if __name__ == "__main__":
    unittest.main()
