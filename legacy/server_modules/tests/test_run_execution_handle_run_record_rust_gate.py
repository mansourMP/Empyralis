import unittest
from unittest import mock

from server_modules import run_execution_handle


class RunExecutionHandleRunRecordRustGateTests(unittest.TestCase):
    def test_build_run_record_calls_rust_before_returning_record(self):
        with mock.patch.object(
            run_execution_handle.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "next_action": "create_live_run_initial",
                "record_patch": {"status": "starting", "state": "starting", "_event_seq": 0, "_archived": False},
            },
        ) as rust_mock:
            record = run_execution_handle.build_run_record(
                run_id="run-1",
                engine="orion",
                context={"tenant_id": "tenant-1", "workspace_id": "ws-1", "trace_id": "trace-1"},
                now_iso="2026-05-31T00:00:00Z",
                memory_enabled=True,
                memory_updated_at="2026-05-31T00:00:00Z",
                active_profile_id=None,
                active_profile_label=None,
                active_provider=None,
                active_model=None,
            )

        self.assertEqual(record.run_id, "run-1")
        self.assertEqual(record.state, "starting")
        rust_mock.assert_called_once()
        self.assertEqual(rust_mock.call_args.args[0], "run-record-decision")
        self.assertEqual(rust_mock.call_args.args[1]["operation"], "register_live_run")
        self.assertEqual(rust_mock.call_args.args[1]["workspace_id"], "ws-1")

    def test_build_run_record_rust_denial_blocks_record_creation(self):
        denied = run_execution_handle.rust_runtime_kernel_client.RustKernelDecisionError(
            {
                "ok": False,
                "decision": "block",
                "reason": "run_record_repository_read_only",
            },
            command="run-record-decision",
        )

        with mock.patch.object(
            run_execution_handle.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=denied,
        ):
            with self.assertRaisesRegex(RuntimeError, "Rust run-record kernel blocked register_live_run"):
                run_execution_handle.build_run_record(
                    run_id="run-1",
                    engine="orion",
                    context={"tenant_id": "tenant-1", "workspace_id": "ws-1"},
                    now_iso="2026-05-31T00:00:00Z",
                    memory_enabled=False,
                    memory_updated_at="2026-05-31T00:00:00Z",
                    active_profile_id=None,
                    active_profile_label=None,
                    active_provider=None,
                    active_model=None,
                )

    def test_build_run_record_wrong_next_action_blocks_record_creation(self):
        with mock.patch.object(
            run_execution_handle.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "next_action": "update_live_run_if_version_matches",
                "record_patch": {"status": "starting", "state": "starting", "_event_seq": 0, "_archived": False},
            },
        ):
            with self.assertRaisesRegex(RuntimeError, "unexpected_next_action:update_live_run_if_version_matches"):
                run_execution_handle.build_run_record(
                    run_id="run-1",
                    engine="orion",
                    context={"tenant_id": "tenant-1", "workspace_id": "ws-1"},
                    now_iso="2026-05-31T00:00:00Z",
                    memory_enabled=False,
                    memory_updated_at="2026-05-31T00:00:00Z",
                    active_profile_id=None,
                    active_profile_label=None,
                    active_provider=None,
                    active_model=None,
                )

    def test_durable_run_payload_gates_snapshot_persistence(self):
        with mock.patch.object(
            run_execution_handle.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "next_action": "update_live_run_if_version_matches",
                "record_patch": {"status": "running", "state": "running", "_event_seq": 7},
            },
        ) as rust_mock:
            payload = run_execution_handle.durable_run_payload(
                "run-1",
                {
                    "run_id": "run-1",
                    "status": "running",
                    "state": "running",
                    "workspace_id": "ws-1",
                    "context": {"tenant_id": "tenant-1", "workspace_id": "ws-1"},
                    "_event_seq": 7,
                },
                json_safe=lambda value: value,
            )

        self.assertEqual(payload["run_id"], "run-1")
        self.assertEqual(payload["state"], "running")
        rust_mock.assert_called_once()
        self.assertEqual(rust_mock.call_args.args[1]["operation"], "persist_snapshot")
        self.assertEqual(rust_mock.call_args.args[1]["version"], 7)

    def test_durable_run_payload_malformed_version_still_reaches_rust(self):
        with mock.patch.object(
            run_execution_handle.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "next_action": "update_live_run_if_version_matches",
                "record_patch": {"status": "running", "state": "running", "_event_seq": 0},
            },
        ) as rust_mock:
            run_execution_handle.durable_run_payload(
                "run-1",
                {
                    "run_id": "run-1",
                    "status": "running",
                    "state": "running",
                    "workspace_id": "ws-1",
                    "context": {"tenant_id": "tenant-1", "workspace_id": "ws-1"},
                    "_event_seq": "not-a-number",
                },
                json_safe=lambda value: value,
            )

        rust_mock.assert_called_once()
        self.assertEqual(rust_mock.call_args.args[1]["operation"], "persist_snapshot")
        self.assertEqual(rust_mock.call_args.args[1]["version"], 0)

    def test_durable_run_payload_applies_rust_record_patch(self):
        with mock.patch.object(
            run_execution_handle.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "next_action": "update_live_run_if_version_matches",
                "record_patch": {
                    "status": "failed",
                    "state": "failed",
                    "_event_seq": 9,
                    "_archived": True,
                    "thread_id": "thread-9",
                },
            },
        ):
            payload = run_execution_handle.durable_run_payload(
                "run-1",
                {
                    "run_id": "run-1",
                    "status": "running",
                    "state": "running",
                    "workspace_id": "ws-1",
                    "context": {"tenant_id": "tenant-1", "workspace_id": "ws-1"},
                    "_event_seq": 9,
                },
                json_safe=lambda value: value,
            )

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["state"], "failed")
        self.assertEqual(payload["_archived"], True)
        self.assertEqual(payload["thread_id"], "thread-9")

    def test_durable_run_payload_wrong_next_action_blocks_snapshot_persistence(self):
        with mock.patch.object(
            run_execution_handle.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "next_action": "create_live_run_initial",
                "record_patch": {"status": "running", "state": "running", "_event_seq": 7},
            },
        ):
            with self.assertRaisesRegex(RuntimeError, "unexpected_next_action:create_live_run_initial"):
                run_execution_handle.durable_run_payload(
                    "run-1",
                    {
                        "run_id": "run-1",
                        "status": "running",
                        "state": "running",
                        "workspace_id": "ws-1",
                        "context": {"tenant_id": "tenant-1", "workspace_id": "ws-1"},
                        "_event_seq": 7,
                    },
                    json_safe=lambda value: value,
                )


if __name__ == "__main__":
    unittest.main()
