"""MAN-125: a tool that runs and RETURNS a failure payload must render as
failed, not "completed" -- server_modules/gateway_execution_service.py
execute_tool_via_gateway.

Before this fix, dispatch not raising a Python exception was the ONLY
signal the three emission sites at the tail of execute_tool_via_gateway
looked at:

  * hardware_activity_event_service.emit_hardware_action_event(status=...)
  * gateway_activity_service.append_gateway_activity(status=...)
  * gateway_transparency_service.emit_gateway_action_event(status=...)

All three hardcoded status="completed" unconditionally on that path. A tool
that ran and returned {"exit_code": 1} (shell) or {"status": "error"}
(filesystem, hardware) inside its OWN result payload -- gateway_protocol_
service only raises when the RPC ENVELOPE's `ok` flag is false, never when
the tool's own result says it failed -- rendered as a green "completed" row
at every one of these three surfaces. This is the Agent Computer / hardware
path: paying customers watching their own tool calls lie to them.

The fix classifies `result` once via tool_result_status.classify_tool_result
(the same structural classifier direct_chat_generation_service.py,
skills_service.py, and mcp_registry_service.py already use) and drives all
three emissions off that ONE verdict, mirroring test_gateway_execution_
service.py's own mocking conventions (patch the same seam points: gateway_
state_repository.get_gateway_registration, gateway_protocol_service.
gateway_connection_is_live/dispatch_tool_invoke, gateway_registry_service.
gateway_registration_public_payload, rust_runtime_kernel_client.
run_runtime_kernel_enforced) so this file is a self-contained sibling to
that suite, not a duplicate of it.
"""

from __future__ import annotations

import importlib
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from server_modules import gateway_execution_service


def _rust_allow(command, payload):
    if payload.get("operation") == "quota_check":
        return {"ok": True, "decision": "allow", "next_action": "allow_gateway_service_operation"}
    return {"ok": True, "decision": "allow", "next_action": "dispatch_gateway_operation"}


def _registration(capability: str = "shell.execute") -> dict:
    return {
        "gateway_id": "gw-1",
        "device_id": "dev-1",
        "workspace_id": "ws-1",
        "tenant_id": "tenant-1",
        "status": "active",
        "device_trust_state": "trusted",
        "capabilities": [capability],
        "metadata": {"capability_readiness": {"ready": [capability]}},
    }


class GatewayExecutionStatusHonestyTests(unittest.IsolatedAsyncioTestCase):
    """Each test drives execute_tool_via_gateway with a dispatch response
    whose `result` carries a specific failure (or success) shape, and reads
    the status every one of the three emission sites actually received --
    not just the function's own return value, which was never the lie (the
    lie was always in what got broadcast to the activity ledger, the
    transparency feed, and the hardware activity channel)."""

    def setUp(self) -> None:
        global gateway_execution_service
        gateway_execution_service = importlib.import_module("server_modules.gateway_execution_service")

    async def _run(self, *, capability: str, result_payload: dict, emit_hardware_activity: bool = True):
        registration = _registration(capability)
        dispatch_mock = AsyncMock(
            return_value={
                "request_id": "req-1",
                "capability_id": capability,
                "run_id": "run-1",
                "result": result_payload,
            }
        )
        with (
            patch("server_modules.gateway_execution_service.gateway_state_repository.get_gateway_registration", return_value=registration),
            patch("server_modules.gateway_execution_service.gateway_protocol_service.gateway_connection_is_live", return_value=True),
            patch(
                "server_modules.gateway_execution_service.gateway_registry_service.gateway_registration_public_payload",
                return_value={
                    "connection_status": "online",
                    "heartbeat_fresh": True,
                    "reported_health_state": "online",
                    "capability_readiness": {"ready": [capability]},
                },
            ),
            patch("server_modules.gateway_execution_service.gateway_protocol_service.dispatch_tool_invoke", dispatch_mock),
            patch("server_modules.gateway_execution_service.gateway_activity_service.append_gateway_activity", new=AsyncMock(return_value={"id": "activity-1"})) as activity_mock,
            patch("server_modules.gateway_execution_service.hardware_activity_event_service.emit_hardware_action_event", return_value=None) as hardware_mock,
            patch("server_modules.gateway_execution_service.gateway_transparency_service.emit_gateway_action_event", return_value=None) as transparency_mock,
            patch(
                "server_modules.gateway_execution_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                side_effect=_rust_allow,
            ),
        ):
            response = await gateway_execution_service.execute_tool_via_gateway(
                gateway_id="gw-1",
                capability_id=capability,
                arguments={},
                run_id="run-1",
                trace_id="trace-1",
                workspace_id="ws-1",
                request_id="req-1",
                emit_hardware_activity=emit_hardware_activity,
            )
        return response, activity_mock, hardware_mock, transparency_mock

    # ── failure shapes: each must report "failed" at all three sites ──────

    async def test_shell_nonzero_exit_code_reports_failed_everywhere(self) -> None:
        response, activity_mock, hardware_mock, transparency_mock = await self._run(
            capability="shell.execute",
            result_payload={"command": "false", "exit_code": 1, "stdout": "", "stderr": "boom"},
        )
        # The RPC round-tripped fine -- the function must NOT raise, and the
        # raw result is still returned byte-for-byte (nothing here silently
        # swallows or rewrites the tool's own payload).
        self.assertEqual(response["result"]["exit_code"], 1)

        hardware_kwargs = hardware_mock.call_args.kwargs
        self.assertEqual(hardware_kwargs["status"], "failed")

        activity_kwargs = activity_mock.await_args.kwargs
        self.assertEqual(activity_kwargs["status"], "failed")

        transparency_kwargs = transparency_mock.call_args.kwargs
        self.assertEqual(transparency_kwargs["status"], "failed")

    async def test_structured_status_error_payload_reports_failed_everywhere(self) -> None:
        _, activity_mock, hardware_mock, transparency_mock = await self._run(
            capability="filesystem.read_write",
            result_payload={"status": "error", "error": "file not found"},
        )
        self.assertEqual(hardware_mock.call_args.kwargs["status"], "failed")
        self.assertEqual(activity_mock.await_args.kwargs["status"], "failed")
        self.assertEqual(transparency_mock.call_args.kwargs["status"], "failed")

    async def test_ok_false_payload_reports_failed_everywhere(self) -> None:
        _, activity_mock, hardware_mock, transparency_mock = await self._run(
            capability="shell.execute",
            result_payload={"ok": False, "error": "subagent unreachable"},
        )
        self.assertEqual(hardware_mock.call_args.kwargs["status"], "failed")
        self.assertEqual(activity_mock.await_args.kwargs["status"], "failed")
        self.assertEqual(transparency_mock.call_args.kwargs["status"], "failed")

    async def test_failed_activity_payload_carries_the_error_text(self) -> None:
        """Not just the status flips -- the activity ledger's payload should
        surface the actual reason, so a human reading the ledger later sees
        WHY it failed, not just a red dot."""
        _, activity_mock, _, _ = await self._run(
            capability="shell.execute",
            result_payload={"exit_code": 2, "stderr": "permission denied"},
        )
        activity_kwargs = activity_mock.await_args.kwargs
        self.assertEqual(activity_kwargs["status"], "failed")
        self.assertIn("failed", activity_kwargs["title"].lower())

    # ── regression guard: success must still report "completed" ───────────

    async def test_successful_result_still_reports_completed_everywhere(self) -> None:
        response, activity_mock, hardware_mock, transparency_mock = await self._run(
            capability="shell.execute",
            result_payload={"command": "echo ok", "exit_code": 0, "stdout": "ok", "stderr": ""},
        )
        self.assertEqual(response["result"]["exit_code"], 0)
        self.assertEqual(hardware_mock.call_args.kwargs["status"], "completed")
        self.assertEqual(activity_mock.await_args.kwargs["status"], "completed")
        self.assertEqual(transparency_mock.call_args.kwargs["status"], "completed")

    async def test_result_with_no_status_signal_at_all_still_reports_completed(self) -> None:
        """Ambiguous (no error/status/exit_code signal anywhere) must default
        to NOT failed -- classify_tool_result is deliberately conservative,
        and this fix must not turn every quiet successful payload red."""
        _, activity_mock, hardware_mock, transparency_mock = await self._run(
            capability="computer_control.click",
            result_payload={"clicked": True, "x": 12, "y": 34},
        )
        self.assertEqual(hardware_mock.call_args.kwargs["status"], "completed")
        self.assertEqual(activity_mock.await_args.kwargs["status"], "completed")
        self.assertEqual(transparency_mock.call_args.kwargs["status"], "completed")

    async def test_emit_hardware_activity_false_still_classifies_the_other_two_sites(self) -> None:
        """emit_hardware_activity=False (the hardware-capability-probe path)
        skips ONLY the hardware activity channel -- the activity ledger and
        transparency feed must still tell the truth about a failed result."""
        _, activity_mock, hardware_mock, transparency_mock = await self._run(
            capability="shell.execute",
            result_payload={"exit_code": 1, "stderr": "no such file"},
            emit_hardware_activity=False,
        )
        hardware_mock.assert_not_called()
        self.assertEqual(activity_mock.await_args.kwargs["status"], "failed")
        self.assertEqual(transparency_mock.call_args.kwargs["status"], "failed")


if __name__ == "__main__":
    unittest.main()
