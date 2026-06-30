from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from server_modules import hardware_runtime_session_service as sessions


class HardwareRuntimeSessionServiceTests(unittest.TestCase):
    def test_session_view_preserves_runtime_contract_metadata(self) -> None:
        view = sessions.session_view(
            "hrs-1",
            {
                "state": "waiting_approval",
                "runtime_target": "local_companion",
                "canonical_runtime_target": "user_device_gateway",
                "runtime_access_mode": "default_guarded",
                "thread_id": "thread-1",
                "request_id": "req-1",
                "approvals": [{"approval_id": "approval-1"}],
                "artifacts": ["artifact-1"],
            },
        )

        self.assertEqual(view["session_id"], "hrs-1")
        self.assertEqual(view["runtime_session_binding"], "sage_hardware_action")
        self.assertEqual(view["state"], "waiting_approval")
        self.assertEqual(view["canonical_runtime_target"], "user_device_gateway")
        self.assertEqual(view["runtime_access_mode"], "default_guarded")
        self.assertEqual(view["request_id"], "req-1")
        self.assertEqual(view["approvals"][0]["approval_id"], "approval-1")
        self.assertEqual(view["artifacts"], ["artifact-1"])

    def test_runtime_session_with_correlation_fills_missing_context(self) -> None:
        session = sessions.runtime_session_with_correlation(
            {"session_id": "hrs-1"},
            payload={"thread_id": "thread-1", "request_id": "req-1"},
            session_record={"trace_id": "trace-1"},
        )

        self.assertEqual(session["thread_id"], "thread-1")
        self.assertEqual(session["request_id"], "req-1")
        self.assertEqual(session["trace_id"], "trace-1")

    def test_create_runtime_session_cloud_default_requires_skip_runtime_binding(self) -> None:
        async def _run():
            with (
                patch(
                    "server_modules.hardware_runtime_session_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                    return_value={
                        "ok": True,
                        "decision": "allow",
                        "operation": "ensure_runtime_session",
                        "next_action": "skip_runtime_binding",
                    },
                ) as rust_mock,
                patch(
                    "server_modules.hardware_runtime_session_service.session_service.create_session",
                    new=AsyncMock(return_value="hrs-1"),
                ) as create_mock,
            ):
                payload = await sessions.create_runtime_session(
                    tenant_id="tenant-1",
                    workspace_id="ws-1",
                    user_id="user-1",
                    runtime_target="cloud_default",
                    canonical_runtime_target="cloud_default",
                    gateway_id=None,
                    device_id=None,
                    node_id=None,
                    action_id="screenshot.capture",
                    capability_id="screen.read",
                    run_id="run-1",
                    trace_id="trace-1",
                    request_id="req-1",
                    thread_id="thread-1",
                    session_id="hrs-1",
                    initial_state="degraded",
                    cost_metadata=None,
                    runtime_access_mode="hosted_secure",
                    execution_mode="default",
                )
            return payload, rust_mock, create_mock

        payload, rust_mock, create_mock = self._run_async(_run())
        self.assertEqual(payload["session_id"], "hrs-1")
        self.assertEqual(payload["state"], "degraded")
        self.assertEqual(rust_mock.call_args.args[0], "runtime-binding-decision")
        self.assertEqual(rust_mock.call_args.args[1]["studio_agent_mode"], "text")
        create_mock.assert_awaited_once()

    def test_create_runtime_session_gateway_wrong_rust_next_action_blocks_before_storage(self) -> None:
        async def _run():
            with (
                patch(
                    "server_modules.hardware_runtime_session_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                    return_value={
                        "ok": True,
                        "decision": "allow",
                        "operation": "ensure_runtime_session",
                        "next_action": "create_cloud_runtime_session",
                    },
                ),
                patch(
                    "server_modules.hardware_runtime_session_service.session_service.create_session",
                    new=AsyncMock(return_value="hrs-1"),
                ) as create_mock,
            ):
                with self.assertRaisesRegex(RuntimeError, "unexpected_next_action:create_cloud_runtime_session"):
                    await sessions.create_runtime_session(
                        tenant_id="tenant-1",
                        workspace_id="ws-1",
                        user_id="user-1",
                        runtime_target="local_companion",
                        canonical_runtime_target="user_device_gateway",
                        gateway_id="gw-1",
                        device_id="device-1",
                        node_id=None,
                        action_id="browser.session.start",
                        capability_id="browser_automation.interactive",
                        run_id="run-1",
                        trace_id="trace-1",
                        request_id="req-1",
                        thread_id="thread-1",
                        session_id="hrs-1",
                        initial_state="running",
                        cost_metadata=None,
                        runtime_access_mode="local_secure",
                        execution_mode="default",
                    )
            return create_mock

        create_mock = self._run_async(_run())
        create_mock.assert_not_awaited()

    def _run_async(self, coroutine):
        import asyncio

        return asyncio.run(coroutine)


if __name__ == "__main__":
    unittest.main()
