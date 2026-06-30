from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from server_modules.hardware_runtime_adapters import gateway_adapter


def _registration(**overrides):
    base = {
        "gateway_id": "gw-1",
        "device_id": "device-1",
        "workspace_id": "ws-1",
        "status": "active",
        "device_trust_state": "trusted",
    }
    base.update(overrides)
    return base


class HardwareGatewayAdapterTests(unittest.TestCase):
    def test_find_gateway_registration_prefers_live_active_gateway(self) -> None:
        with (
            patch(
                "server_modules.hardware_runtime_adapters.gateway_adapter.gateway_state_repository.list_workspace_gateway_registrations",
                return_value=[
                    _registration(gateway_id="gw-offline"),
                    _registration(gateway_id="gw-live"),
                    _registration(gateway_id="gw-revoked", device_trust_state="revoked"),
                ],
            ),
            patch(
                "server_modules.hardware_runtime_adapters.gateway_adapter.gateway_protocol_service.gateway_connection_is_live",
                side_effect=lambda gateway_id: gateway_id == "gw-live",
            ),
        ):
            registration = gateway_adapter.find_gateway_registration(gateway_id=None, workspace_id="ws-1")

        self.assertEqual(registration["gateway_id"], "gw-live")

    def test_registration_usability_keeps_hard_boundaries(self) -> None:
        self.assertEqual(
            gateway_adapter.registration_is_usable(_registration(status="inactive"), workspace_id="ws-1"),
            (False, "gateway_registration_inactive"),
        )
        self.assertEqual(
            gateway_adapter.registration_is_usable(_registration(device_trust_state="revoked"), workspace_id="ws-1"),
            (False, "gateway_device_revoked"),
        )
        self.assertEqual(
            gateway_adapter.registration_is_usable(_registration(workspace_id="other"), workspace_id="ws-1"),
            (False, "gateway_workspace_mismatch"),
        )
        self.assertEqual(
            gateway_adapter.registration_is_usable(_registration(), workspace_id="ws-1"),
            (True, ""),
        )

    def test_execution_readiness_fails_closed_for_stale_heartbeat(self) -> None:
        with (
            patch(
                "server_modules.gateway_execution_service.gateway_protocol_service.gateway_connection_is_live",
                return_value=True,
            ),
            patch(
                "server_modules.gateway_execution_service.gateway_registry_service.gateway_registration_public_payload",
                return_value={
                    "connection_status": "degraded",
                    "heartbeat_fresh": False,
                    "reported_health_state": "online",
                },
            ),
        ):
            self.assertEqual(
                gateway_adapter.gateway_execution_service.gateway_registration_execution_readiness(
                    _registration(capabilities=["shell.execute"]),
                    workspace_id="ws-1",
                    capability_id="shell.execute",
                ),
                (False, "gateway_heartbeat_stale"),
            )

    def test_execution_readiness_fails_closed_for_missing_capability(self) -> None:
        self.assertEqual(
            gateway_adapter.gateway_execution_service.gateway_registration_execution_readiness(
                _registration(capabilities=["screenshot.capture"]),
                workspace_id="ws-1",
                capability_id="shell.execute",
            ),
            (False, "gateway_capability_missing"),
        )

    def test_execution_readiness_allows_fresh_healthy_registered_capability(self) -> None:
        with (
            patch(
                "server_modules.gateway_execution_service.gateway_protocol_service.gateway_connection_is_live",
                return_value=True,
            ),
            patch(
                "server_modules.gateway_execution_service.gateway_registry_service.gateway_registration_public_payload",
                return_value={
                    "connection_status": "online",
                    "heartbeat_fresh": True,
                    "reported_health_state": "online",
                    "capability_readiness": {"ready": ["shell.execute"]},
                },
            ),
        ):
            self.assertEqual(
                gateway_adapter.gateway_execution_service.gateway_registration_execution_readiness(
                    _registration(
                        capabilities=["shell.execute"],
                        metadata={"capability_readiness": {"ready": ["shell.execute"]}},
                    ),
                    workspace_id="ws-1",
                    capability_id="shell.execute",
                ),
                (True, ""),
            )

    def test_execution_readiness_requires_heartbeat_capability_ready(self) -> None:
        with (
            patch(
                "server_modules.gateway_execution_service.gateway_protocol_service.gateway_connection_is_live",
                return_value=True,
            ),
            patch(
                "server_modules.gateway_execution_service.gateway_registry_service.gateway_registration_public_payload",
                return_value={
                    "connection_status": "online",
                    "heartbeat_fresh": True,
                    "reported_health_state": "online",
                    "metadata": {
                        "service_inventory": [
                            {
                                "id": "postgres",
                                "status": "ready",
                                "passive": True,
                                "execution_enabled": False,
                            }
                        ],
                        "capability_readiness": {
                            "passive_services": ["postgres"],
                            "service_statuses": {"postgres": "ready"},
                        },
                    },
                },
            ),
        ):
            self.assertEqual(
                gateway_adapter.gateway_execution_service.gateway_registration_execution_readiness(
                    _registration(
                        capabilities=["shell.execute"],
                        metadata={
                            "service_inventory": [
                                {
                                    "id": "postgres",
                                    "status": "ready",
                                    "passive": True,
                                    "execution_enabled": False,
                                }
                            ],
                            "capability_readiness": {
                                "passive_services": ["postgres"],
                                "service_statuses": {"postgres": "ready"},
                            },
                        },
                    ),
                    workspace_id="ws-1",
                    capability_id="shell.execute",
                ),
                (False, "gateway_capability_not_ready"),
            )

    def test_execution_summary_uses_user_visible_result_text(self) -> None:
        self.assertEqual(
            gateway_adapter.execution_summary(
                "shell.execute",
                {"result": {"command": "pwd", "exit_code": 0}},
            ),
            "Ran command: pwd",
        )
        self.assertEqual(
            gateway_adapter.execution_summary(
                "filesystem.read",
                {"result": {"path": "/tmp/demo.txt", "mode": "read"}},
            ),
            "Read file action completed: /tmp/demo.txt",
        )

    def test_gateway_action_decision_requires_dispatch_action_for_live_tool_execute(self) -> None:
        with (
            patch(
                "server_modules.hardware_runtime_adapters.gateway_adapter.rust_runtime_kernel_client.gateway_action_decision",
                return_value={
                    "ok": True,
                    "decision": "allow",
                    "operation": "tool_execute",
                    "next_action": "dispatch_tool_invoke",
                },
            ) as decision_mock,
            patch(
                "server_modules.hardware_runtime_adapters.gateway_adapter.rust_runtime_kernel_client.enforce_kernel_decision",
                side_effect=lambda _command, decision, **_kwargs: decision,
            ),
        ):
            gateway_adapter._enforce_gateway_action_decision(
                operation="tool_execute",
                tenant_id="tenant-1",
                workspace_id="ws-1",
                gateway_id="gw-1",
                run_id="run-1",
                request_id="req-1",
                capability_id="shell.execute",
                user_id="user-1",
                runtime_access_mode="guarded",
                risk_decision="allow",
                approval_provided=False,
            )

        request = decision_mock.call_args.kwargs
        self.assertEqual(request["operation"], "tool_execute")
        self.assertEqual(request["gateway_id"], "gw-1")
        self.assertEqual(request["actor_role"], "member")
        self.assertEqual(request["risk_decision"], "allow")

    def test_gateway_action_decision_requires_stop_action_for_browser_session_stop(self) -> None:
        with (
            patch(
                "server_modules.hardware_runtime_adapters.gateway_adapter.rust_runtime_kernel_client.gateway_action_decision",
                return_value={
                    "ok": True,
                    "decision": "allow",
                    "operation": "browser_session_stop",
                    "next_action": "stop_browser_session",
                },
            ) as decision_mock,
            patch(
                "server_modules.hardware_runtime_adapters.gateway_adapter.rust_runtime_kernel_client.enforce_kernel_decision",
                side_effect=lambda _command, decision, **_kwargs: decision,
            ),
        ):
            gateway_adapter._enforce_gateway_action_decision(
                operation="browser_session_stop",
                tenant_id="tenant-1",
                workspace_id="ws-1",
                gateway_id="gw-1",
                run_id="run-1",
                request_id="req-1",
                capability_id="browser.session.interrupt",
                user_id="user-1",
                runtime_access_mode="guarded",
                risk_decision="allow",
                approval_provided=True,
            )

        request = decision_mock.call_args.kwargs
        self.assertEqual(request["operation"], "browser_session_stop")
        self.assertEqual(request["capability_id"], "browser.session.interrupt")

    def test_execute_gateway_action_defaults_to_agent_computer_timeout(self) -> None:
        async def run_test() -> None:
            runtime_session = {
                "session_id": "session-1",
                "state": "ready",
                "canonical_runtime_target": "user_device_gateway",
            }
            execution_mock = unittest.mock.AsyncMock(
                return_value={
                    "request_id": "req-1",
                    "capability_id": "shell.execute",
                    "run_id": "run-1",
                    "result": {"command": "echo ok", "exit_code": 0, "stdout": "ok"},
                }
            )
            with (
                patch(
                    "server_modules.hardware_runtime_adapters.gateway_adapter.find_gateway_registration",
                    return_value=_registration(),
                ),
                patch(
                    "server_modules.hardware_runtime_adapters.gateway_adapter.registration_is_usable",
                    return_value=(True, ""),
                ),
                patch(
                    "server_modules.hardware_runtime_adapters.gateway_adapter.gateway_execution_service.gateway_registration_execution_readiness",
                    return_value=(True, ""),
                ),
                patch(
                    "server_modules.hardware_runtime_adapters.gateway_adapter.hardware_runtime_session_service.update_runtime_session",
                    side_effect=lambda session, **kwargs: {**session, **({"state": kwargs["state"]} if "state" in kwargs else {})},
                ),
                patch(
                    "server_modules.hardware_runtime_adapters.gateway_adapter.hardware_access_policy_service.hardware_action_requires_software_approval",
                    return_value=False,
                ),
                patch(
                    "server_modules.hardware_runtime_adapters.gateway_adapter.rust_runtime_kernel_client.gateway_action_decision",
                    return_value={
                        "ok": True,
                        "decision": "allow",
                        "operation": "tool_execute",
                        "next_action": "dispatch_tool_invoke",
                    },
                ),
                patch(
                    "server_modules.hardware_runtime_adapters.gateway_adapter.rust_runtime_kernel_client.enforce_kernel_decision",
                    side_effect=lambda _command, decision, **_kwargs: decision,
                ),
                patch(
                    "server_modules.hardware_runtime_adapters.gateway_adapter.gateway_execution_service.execute_tool_via_gateway",
                    execution_mock,
                ),
                patch(
                    "server_modules.hardware_runtime_adapters.gateway_adapter.hardware_result_correlator_service.emit_artifacts",
                    new_callable=unittest.mock.AsyncMock,
                ),
                patch(
                    "server_modules.hardware_runtime_adapters.gateway_adapter.hardware_result_correlator_service.emit_tool_result",
                    new_callable=unittest.mock.AsyncMock,
                ),
            ):
                result = await gateway_adapter.execute_gateway_action(
                    tenant_id="tenant-1",
                    workspace_id="ws-1",
                    user_id="user-1",
                    gateway_id="gw-1",
                    device_id="device-1",
                    action_id="shell.execute",
                    capability_id="shell.execute",
                    arguments={"command": "echo ok"},
                    runtime_session=runtime_session,
                    run_id="run-1",
                    trace_id="trace-1",
                    thread_id=None,
                    request_id="req-1",
                    trace_context=None,
                    require_approval=None,
                    runtime_access_mode="guarded",
                    timeout_seconds=None,
                    tool_call_id="tool-1",
                )

            self.assertEqual(result["status"], "completed")
            self.assertEqual(
                execution_mock.await_args.kwargs["timeout_seconds"],
                gateway_adapter.DEFAULT_AGENT_COMPUTER_TOOL_TIMEOUT_SECONDS,
            )

        asyncio.run(run_test())

    def test_execute_gateway_action_blocks_wrong_rust_action_before_dispatch(self) -> None:
        async def run_test() -> None:
            runtime_session = {"session_id": "session-1", "state": "ready"}
            with (
                patch(
                    "server_modules.hardware_runtime_adapters.gateway_adapter.find_gateway_registration",
                    return_value=_registration(),
                ),
                patch(
                    "server_modules.hardware_runtime_adapters.gateway_adapter.registration_is_usable",
                    return_value=(True, ""),
                ),
                patch(
                    "server_modules.hardware_runtime_adapters.gateway_adapter.gateway_execution_service.gateway_registration_execution_readiness",
                    return_value=(True, ""),
                ),
                patch(
                    "server_modules.hardware_runtime_adapters.gateway_adapter.hardware_runtime_session_service.update_runtime_session",
                    side_effect=lambda session, **kwargs: {**session, **({"state": kwargs["state"]} if "state" in kwargs else {})},
                ),
                patch(
                    "server_modules.hardware_runtime_adapters.gateway_adapter.hardware_access_policy_service.hardware_action_requires_software_approval",
                    return_value=False,
                ),
                patch(
                    "server_modules.hardware_runtime_adapters.gateway_adapter.rust_runtime_kernel_client.gateway_action_decision",
                    return_value={
                        "ok": True,
                        "decision": "allow",
                        "operation": "tool_execute",
                        "next_action": "request_gateway_tool_approval",
                    },
                ),
                patch(
                    "server_modules.hardware_runtime_adapters.gateway_adapter.rust_runtime_kernel_client.enforce_kernel_decision",
                    side_effect=lambda _command, decision, **_kwargs: decision,
                ),
                patch(
                    "server_modules.hardware_runtime_adapters.gateway_adapter.gateway_execution_service.execute_tool_via_gateway",
                    side_effect=AssertionError("should not dispatch"),
                ) as execute_mock,
                patch(
                    "server_modules.hardware_runtime_adapters.gateway_adapter.hardware_result_correlator_service.emit_tool_result",
                    new_callable=unittest.mock.AsyncMock,
                ),
            ):
                with self.assertRaises(RuntimeError) as raised:
                    await gateway_adapter.execute_gateway_action(
                        tenant_id="tenant-1",
                        workspace_id="ws-1",
                        user_id="user-1",
                        gateway_id="gw-1",
                        device_id="device-1",
                        action_id="act-1",
                        capability_id="shell.execute",
                        arguments={"command": "pwd"},
                        runtime_session=runtime_session,
                        run_id="run-1",
                        trace_id="trace-1",
                        thread_id=None,
                        request_id="req-1",
                        trace_context=None,
                        require_approval=None,
                        runtime_access_mode="guarded",
                        timeout_seconds=5,
                        tool_call_id="tool-1",
                    )

            self.assertIn("unexpected next_action", str(raised.exception))
            execute_mock.assert_not_called()

        asyncio.run(run_test())

    def test_stop_gateway_action_blocks_wrong_rust_action_before_interrupt(self) -> None:
        async def run_test() -> None:
            with (
                patch(
                    "server_modules.hardware_runtime_adapters.gateway_adapter.find_gateway_registration",
                    return_value=_registration(user_id="user-1"),
                ),
                patch(
                    "server_modules.hardware_runtime_adapters.gateway_adapter.registration_is_usable",
                    return_value=(True, ""),
                ),
                patch(
                    "server_modules.hardware_runtime_adapters.gateway_adapter.gateway_protocol_service.gateway_connection_is_live",
                    return_value=True,
                ),
                patch(
                    "server_modules.hardware_runtime_adapters.gateway_adapter.rust_runtime_kernel_client.gateway_action_decision",
                    return_value={
                        "ok": True,
                        "decision": "allow",
                        "operation": "browser_session_stop",
                        "next_action": "dispatch_tool_invoke",
                    },
                ),
                patch(
                    "server_modules.hardware_runtime_adapters.gateway_adapter.rust_runtime_kernel_client.enforce_kernel_decision",
                    side_effect=lambda _command, decision, **_kwargs: decision,
                ),
                patch(
                    "server_modules.hardware_runtime_adapters.gateway_adapter.gateway_execution_service.interrupt_tool_via_gateway",
                    new=AsyncMock(side_effect=AssertionError("should not interrupt")),
                ) as interrupt_mock,
            ):
                with self.assertRaises(RuntimeError) as raised:
                    await gateway_adapter.stop_gateway_action(
                        tenant_id="tenant-1",
                        workspace_id="ws-1",
                        run_id="run-1",
                        target_ids={
                            "canonical_runtime_target": "user_device_gateway",
                            "runtime_target": "user_device_gateway",
                        },
                        gateway_id="gw-1",
                        trace_id="trace-1",
                        target_request_id="target-1",
                        request_id="req-1",
                        thread_id=None,
                        reason="operator_requested_stop",
                        session_id=None,
                        timeout_seconds=5,
                    )

            self.assertIn("unexpected next_action", str(raised.exception))
            interrupt_mock.assert_not_awaited()

        asyncio.run(run_test())
