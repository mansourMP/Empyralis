from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from server_modules import hardware_action_broker_service as broker
from server_modules.agent_trace_service import TraceContext


def _trace() -> TraceContext:
    return TraceContext(
        trace_id="trace-1",
        workspace_id="ws-1",
        tenant_id="tenant-1",
        thread_id="thread-1",
        run_id="run-1",
        root_agent_id="sage",
    )


def _registration() -> dict:
    capabilities = [
        "shell.execute",
        "screenshot.capture",
        "computer_control.click",
        "browser_automation.interactive",
        "filesystem.read",
        "filesystem.write",
        "filesystem.read_write",
    ]
    return {
        "gateway_id": "gw-1",
        "device_id": "device-1",
        "tenant_id": "tenant-1",
        "workspace_id": "ws-1",
        "user_id": "user-1",
        "status": "active",
        "device_trust_state": "trusted",
        "capabilities": capabilities,
        "metadata": {"capability_readiness": {"ready": capabilities}},
    }


def _self_hosted_inventory(*, status: str = "online", online: bool = True, healthy: bool = True) -> dict:
    return {
        "tenant_id": "tenant-1",
        "workspace_id": "ws-1",
        "attachments": [
            {
                "attachment_id": "self_hosted_business_node:rprof-self",
                "attachment_kind": "self_hosted_business_node",
                "tenant_id": "tenant-1",
                "workspace_id": "ws-1",
                "runtime_profile_id": "rprof-self",
                "runtime_profile_slug": "secure-node",
                "runtime_id": "node-1",
                "runtime_node_id": "node-1",
                "machine_id": "machine-1",
                "node_kind": "linux_server",
                "self_hosted_node_status": status,
                "owner_approved": True,
                "max_concurrent_sessions": 2,
                "capabilities": ["shell.execute", "file_access"],
                "online": online,
                "healthy": healthy,
            }
        ],
    }


def _hardware_approval_session_record(*, runtime_target: str, approval_id: str, request_payload: dict) -> dict:
    return {
        "session_id": request_payload.get("runtime_session_id") or "hrs-approval",
        "tenant_id": "tenant-1",
        "workspace_id": "ws-1",
        "metadata": {
            "runtime_session_binding": broker.HARDWARE_RUNTIME_SESSION_BINDING,
            "runtime_session_id": request_payload.get("runtime_session_id") or "hrs-approval",
            "runtime_target": runtime_target,
            "canonical_runtime_target": runtime_target,
            "runtime_access_mode": "default_guarded",
            "execution_mode": "default",
            "state": "waiting_approval",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "thread_id": "thread-1",
            "user_id": "user-1",
            "run_id": request_payload.get("run_id") or "run-1",
            "trace_id": request_payload.get("trace_id") or "trace-1",
            "request_id": request_payload.get("request_id") or "req-approval",
            "capability_id": request_payload.get("capability_id"),
            "action_id": request_payload.get("action_id"),
            "approvals": [
                {
                    "approval_id": approval_id,
                    "status": "pending",
                    "kind": "hardware_action",
                    "request_payload": {
                        **request_payload,
                        "arguments": {"command": "[redacted]"},
                    },
                }
            ],
            "approval_execution_payloads": {approval_id: request_payload},
            "artifacts": [],
            "audit_events": [],
            "billing": {},
        },
    }


class _FakeCloudRuntime:
    def __init__(self) -> None:
        self.create_session = AsyncMock(
            return_value={
                "session_id": "hrs-cloud-computer",
                "status": "started",
                "runtime_kind": "virtual_computer_runtime",
                "cost_usage": {"estimated_cost": 0.01, "cost_unit": "runtime_unit"},
            }
        )
        self.execute_action = AsyncMock(
            return_value={
                "session_id": "hrs-cloud-computer",
                "status": "completed",
                "runtime_kind": "virtual_computer_runtime",
                "session": {"ui_current_url": "https://example.com"},
                "action_result": {
                    "ok": True,
                    "action": "open_url",
                    "cost_estimate": {"estimated_cost": 0.01, "cost_unit": "runtime_unit"},
                },
                "cost_usage": {"estimated_cost": 0.02, "cost_unit": "runtime_unit"},
            }
        )
        self.stream_screenshot = AsyncMock(
            return_value={
                "session_id": "hrs-cloud-computer",
                "status": "streaming",
                "runtime_kind": "virtual_computer_runtime",
                "artifact": {"artifact_id": "artifact-cloud-shot", "type": "screenshot"},
                "streaming_ui": {"live_screenshot": {"artifact_id": "artifact-cloud-shot"}},
                "cost_usage": {"estimated_cost": 0.03, "cost_unit": "runtime_unit"},
            }
        )
        self.terminate_session = AsyncMock(
            return_value={
                "session_id": "hrs-cloud-computer",
                "status": "terminated",
                "runtime_kind": "virtual_computer_runtime",
            }
        )


class _FakeCloudRuntimeRegistry:
    def __init__(self, runtime: _FakeCloudRuntime) -> None:
        self.runtime = runtime
        self.resolve_calls: list[dict] = []

    def resolve(self, runtime_choice, *, preferred_provider_id=None):
        self.resolve_calls.append(
            {
                "runtime_choice": runtime_choice,
                "preferred_provider_id": preferred_provider_id,
            }
        )
        return self.runtime


class HardwareActionBrokerServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        def rust_runtime_decision(command: str, payload: dict, **_kwargs):
            operation = str((payload or {}).get("operation") or "").strip()
            if command == "runtime-binding-decision":
                studio_agent_mode = str((payload or {}).get("studio_agent_mode") or "").strip()
                next_action = {
                    "ensure_cloud_runtime_session": "create_cloud_runtime_session",
                    "ensure_runtime_session": (
                        "create_local_gateway_runtime_session"
                        if studio_agent_mode == "my_computer"
                        else "skip_runtime_binding"
                    ),
                }.get(operation, "skip_runtime_binding")
                return {
                    "ok": True,
                    "decision": "allow",
                    "operation": operation,
                    "next_action": next_action,
                    "cacheable": False,
                    "audit_visibility": "security",
                }
            if command == "virtual-computer-decision":
                next_action = {
                    "provider_admission": "admit_virtual_computer_provider",
                    "isolation_profile": "build_isolation_profile",
                    "identity_context": "build_identity_context",
                    "cost_quota": "build_cost_quota_profile",
                    "action_payload": "validate_computer_use_action_payload",
                    "network_policy": "assert_network_browser_security",
                    "artifact_export": "collect_virtual_computer_artifact",
                    "session_state": "assert_virtual_session_active",
                }.get(operation, "validate_computer_use_action_payload")
                return {
                    "ok": True,
                    "decision": "allow",
                    "operation": operation,
                    "next_action": next_action,
                    "approval_required": False,
                    "cacheable": False,
                    "audit_visibility": "security",
                }
            if command == "runtime-action-decision":
                next_action = (
                    "execute_cloud_runtime_action"
                    if str((payload or {}).get("studio_agent_mode") or "") == "cloud_computer"
                    else "execute_self_hosted_runtime_action"
                )
                return {
                    "ok": True,
                    "decision": "allow",
                    "reason": "runtime_action_allowed",
                    "operation": operation or "execute_runtime_action",
                    "next_action": next_action,
                    "approval_required": False,
                    "cacheable": False,
                    "audit_visibility": "security",
                }
            if command == "runtime-attachment-decision":
                next_action = {
                    "self_hosted_gate": "ensure_self_hosted_node_gate",
                    "local_companion_gate": "select_local_companion_attachment",
                    "select_attachment": "select_runtime_attachment",
                    "normalize_target": "normalize_runtime_target_id",
                    "build_targets": "build_workspace_runtime_targets",
                }.get(operation, "ensure_self_hosted_node_gate")
                return {
                    "ok": True,
                    "decision": "allow",
                    "operation": operation,
                    "next_action": next_action,
                    "cacheable": False,
                    "audit_visibility": "security",
                }
            return {
                "ok": True,
                "decision": "allow",
                "operation": operation,
                "next_action": "allow",
                "cacheable": False,
                "audit_visibility": "security",
            }

        def gateway_action_decision(**_payload):
            operation = str(_payload.get("operation") or "tool_execute")
            risk_decision = str(_payload.get("risk_decision") or "allow")
            if operation == "browser_session_stop":
                next_action = "stop_browser_session"
            elif risk_decision == "require_approval":
                next_action = "request_gateway_tool_approval"
            else:
                next_action = "dispatch_tool_invoke"
            return {
                "ok": True,
                "decision": "require_approval" if risk_decision == "require_approval" else "allow",
                "operation": operation,
                "next_action": next_action,
                "approval_required": False,
                "cacheable": False,
                "audit_visibility": "security",
            }

        self._rust_runtime_action_patch = patch(
            "server_modules.hardware_action_broker_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
            side_effect=rust_runtime_decision,
        )
        self._gateway_action_patch = patch(
            "server_modules.hardware_action_broker_service.rust_runtime_kernel_client.gateway_action_decision",
            side_effect=gateway_action_decision,
        )
        self._gateway_public_payload_patch = patch(
            "server_modules.gateway_execution_service.gateway_registry_service.gateway_registration_public_payload",
            return_value={
                "connection_status": "online",
                "heartbeat_fresh": True,
                "reported_health_state": "online",
                "capability_readiness": {
                    "ready": [
                        "shell.execute",
                        "screenshot.capture",
                        "computer_control.click",
                        "browser_automation.interactive",
                        "filesystem.read",
                        "filesystem.write",
                        "filesystem.read_write",
                    ]
                },
            },
        )
        self.mock_rust_runtime_action = self._rust_runtime_action_patch.start()
        self.mock_gateway_action_decision = self._gateway_action_patch.start()
        self.mock_gateway_public_payload = self._gateway_public_payload_patch.start()

    async def asyncTearDown(self) -> None:
        self._gateway_public_payload_patch.stop()
        self._gateway_action_patch.stop()
        self._rust_runtime_action_patch.stop()

    def _session_patches(self):
        return (
            patch(
                "server_modules.hardware_action_broker_service.session_service.create_session",
                new=AsyncMock(return_value="hrs-1"),
            ),
            patch(
                "server_modules.hardware_action_broker_service.session_service.extend_session",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "server_modules.hardware_action_broker_service.agent_trace_service.emit_tool_started",
                new=AsyncMock(return_value="evt-started"),
            ),
            patch(
                "server_modules.hardware_action_broker_service.agent_trace_service.emit_tool_result",
                new=AsyncMock(return_value="evt-result"),
            ),
            patch(
                "server_modules.hardware_action_broker_service.agent_trace_service.emit_artifact_created",
                new=AsyncMock(return_value="evt-artifact"),
            ),
            patch(
                "server_modules.hardware_action_broker_service.agent_trace_service.emit_approval_requested",
                new=AsyncMock(return_value="evt-approval"),
            ),
        )

    async def test_cloud_default_hardware_action_returns_degraded_without_gateway_execution(self) -> None:
        create_patch, extend_patch, started_patch, result_patch, artifact_patch, approval_patch = self._session_patches()
        execute_mock = AsyncMock()
        with (
            create_patch as create_session,
            extend_patch,
            started_patch,
            result_patch as emit_result,
            artifact_patch,
            approval_patch,
            patch(
                "server_modules.hardware_action_broker_service.gateway_execution_service.execute_tool_via_gateway",
                execute_mock,
            ),
        ):
            payload = await broker.execute_hardware_action(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                action_id="screenshot.capture",
                runtime_target="cloud_default",
                run_id="run-1",
                trace_id="trace-1",
                thread_id="thread-1",
                session_id="hrs-cloud",
                trace_context=_trace(),
            )

        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["reason"], "hardware_action_requires_optional_runtime_target")
        self.assertEqual(payload["runtime_session"]["state"], "degraded")
        self.assertEqual(payload["runtime_session"]["canonical_runtime_target"], "cloud_default")
        execute_mock.assert_not_awaited()
        emit_result.assert_awaited()
        create_kwargs = create_session.await_args.kwargs
        self.assertEqual(create_kwargs["channel"], broker.HARDWARE_RUNTIME_CHANNEL)
        self.assertEqual(create_kwargs["metadata"]["runtime_session_binding"], broker.HARDWARE_RUNTIME_SESSION_BINDING)
        self.assertEqual(create_kwargs["metadata"]["state"], "degraded")

    async def test_gateway_connected_action_routes_through_gateway_with_runtime_session_and_redacted_trace(self) -> None:
        create_patch, extend_patch, started_patch, result_patch, artifact_patch, approval_patch = self._session_patches()
        execute_mock = AsyncMock(
            return_value={
                "gateway_id": "gw-1",
                "device_id": "device-1",
                "workspace_id": "ws-1",
                "request_id": "req-1",
                "capability_id": "browser_automation.interactive",
                "run_id": "run-1",
                "result": {
                    "summary": "Used browser.",
                    "artifacts": [{"id": "artifact-1"}],
                },
            }
        )
        with (
            create_patch,
            extend_patch,
            started_patch as emit_started,
            result_patch as emit_result,
            artifact_patch as emit_artifact,
            approval_patch,
            patch(
                "server_modules.hardware_action_broker_service.gateway_state_repository.get_gateway_registration",
                return_value=_registration(),
            ),
            patch(
                "server_modules.hardware_action_broker_service.gateway_protocol_service.gateway_connection_is_live",
                return_value=True,
            ),
            patch(
                "server_modules.hardware_action_broker_service.gateway_approval_service.capability_requires_owner_approval",
                return_value=False,
            ),
            patch(
                "server_modules.hardware_action_broker_service.gateway_execution_service.execute_tool_via_gateway",
                execute_mock,
            ),
        ):
            payload = await broker.execute_hardware_action(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                action_id="browser.open",
                arguments={"url": "https://example.com", "api_key": "sk-secret-token"},
                runtime_target="user_device_gateway",
                gateway_id="gw-1",
                run_id="run-1",
                trace_id="trace-1",
                request_id="req-1",
                session_id="hrs-gateway",
                trace_context=_trace(),
            )

        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["runtime_session"]["state"], "ready")
        self.assertEqual(payload["runtime_session"]["gateway_id"], "gw-1")
        self.assertIn("artifact-1", payload["artifacts"])
        execute_mock.assert_awaited_once()
        execute_kwargs = execute_mock.await_args.kwargs
        self.assertEqual(execute_kwargs["gateway_id"], "gw-1")
        self.assertEqual(execute_kwargs["capability_id"], "browser_automation.interactive")
        self.assertEqual(execute_kwargs["request_id"], "req-1")
        emit_started.assert_awaited()
        self.assertEqual(emit_started.await_args.kwargs["args_preview"]["api_key"], "[redacted]")
        emit_artifact.assert_awaited_once()
        emit_result.assert_awaited()
        result_kwargs = emit_result.await_args.kwargs
        self.assertEqual(result_kwargs["tool_name"], "browser_automation.interactive")
        self.assertEqual(result_kwargs["capability_id"], "browser_automation.interactive")
        self.assertEqual(result_kwargs["connector_id"], "hardware_runtime")
        self.assertEqual(result_kwargs["runtime_session_id"], "hrs-gateway")
        self.assertEqual(result_kwargs["runtime_target"], "user_device_gateway")
        self.assertEqual(result_kwargs["request_id"], "req-1")
        self.assertEqual(result_kwargs["action_id"], "browser.open")
        self.assertEqual(result_kwargs["args_preview"]["api_key"], "[redacted]")

    async def test_gateway_offline_returns_offline_without_dispatch(self) -> None:
        create_patch, extend_patch, started_patch, result_patch, artifact_patch, approval_patch = self._session_patches()
        execute_mock = AsyncMock()
        with (
            create_patch,
            extend_patch,
            started_patch,
            result_patch,
            artifact_patch,
            approval_patch,
            patch(
                "server_modules.hardware_action_broker_service.gateway_state_repository.get_gateway_registration",
                return_value=_registration(),
            ),
            patch(
                "server_modules.hardware_action_broker_service.gateway_protocol_service.gateway_connection_is_live",
                return_value=False,
            ),
            patch(
                "server_modules.hardware_action_broker_service.gateway_execution_service.execute_tool_via_gateway",
                execute_mock,
            ),
        ):
            payload = await broker.execute_hardware_action(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                action_id="shell.exec",
                arguments={"command": "pwd"},
                runtime_target="local_companion",
                gateway_id="gw-1",
                run_id="run-1",
                trace_id="trace-1",
                session_id="hrs-offline",
                trace_context=_trace(),
            )

        self.assertEqual(payload["status"], "offline")
        self.assertEqual(payload["reason"], "gateway_offline")
        self.assertEqual(payload["runtime_session"]["state"], "offline")
        execute_mock.assert_not_awaited()

    async def test_gateway_risky_action_requests_approval_without_execution(self) -> None:
        create_patch, extend_patch, started_patch, result_patch, artifact_patch, approval_patch = self._session_patches()
        approval = {
                "approval_id": "gapproval-1",
                "status": "pending",
                "request_payload": {
                    "capability_id": "shell.execute",
                    "arguments": {"command": "rm -rf /tmp/demo", "password": "secret"},
                },
            }
        request_approval_mock = AsyncMock(return_value=approval)
        execute_mock = AsyncMock()
        with (
            create_patch,
            extend_patch,
            started_patch,
            result_patch,
            artifact_patch,
            approval_patch as emit_approval,
            patch(
                "server_modules.hardware_action_broker_service.gateway_state_repository.get_gateway_registration",
                return_value=_registration(),
            ),
            patch(
                "server_modules.hardware_action_broker_service.gateway_protocol_service.gateway_connection_is_live",
                return_value=True,
            ),
            patch(
                "server_modules.hardware_action_broker_service.gateway_approval_service.capability_requires_owner_approval",
                return_value=True,
            ),
            patch(
                "server_modules.hardware_action_broker_service.gateway_approval_service.request_gateway_tool_approval",
                request_approval_mock,
            ),
            patch(
                "server_modules.hardware_action_broker_service.gateway_execution_service.execute_tool_via_gateway",
                execute_mock,
            ),
        ):
            payload = await broker.execute_hardware_action(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                action_id="shell.exec",
                arguments={"command": "rm -rf /tmp/demo", "password": "secret"},
                runtime_target="user_device_gateway",
                gateway_id="gw-1",
                run_id="run-1",
                trace_id="trace-1",
                request_id="req-1",
                session_id="hrs-approval",
                trace_context=_trace(),
            )

        self.assertEqual(payload["status"], "waiting_approval")
        self.assertEqual(payload["runtime_session"]["state"], "waiting_approval")
        self.assertEqual(
            payload["runtime_session"]["approvals"][0]["request_payload"]["arguments"]["password"],
            "[redacted]",
        )
        request_approval_mock.assert_awaited_once()
        execute_mock.assert_not_awaited()
        emit_approval.assert_awaited_once()

    async def test_gateway_full_access_risky_action_executes_without_approval(self) -> None:
        create_patch, extend_patch, started_patch, result_patch, artifact_patch, approval_patch = self._session_patches()
        request_approval_mock = AsyncMock()
        execute_mock = AsyncMock(
            return_value={
                "gateway_id": "gw-1",
                "device_id": "device-1",
                "workspace_id": "ws-1",
                "request_id": "req-full",
                "capability_id": "shell.execute",
                "run_id": "run-1",
                "result": {"summary": "Ran command.", "command": "rm -rf /tmp/demo", "exit_code": 0},
            }
        )
        with (
            create_patch,
            extend_patch,
            started_patch,
            result_patch,
            artifact_patch,
            approval_patch as emit_approval,
            patch(
                "server_modules.hardware_action_broker_service.gateway_state_repository.get_gateway_registration",
                return_value=_registration(),
            ),
            patch(
                "server_modules.hardware_action_broker_service.gateway_protocol_service.gateway_connection_is_live",
                return_value=True,
            ),
            patch(
                "server_modules.hardware_action_broker_service.gateway_approval_service.request_gateway_tool_approval",
                request_approval_mock,
            ),
            patch(
                "server_modules.hardware_action_broker_service.gateway_execution_service.execute_tool_via_gateway",
                execute_mock,
            ),
        ):
            payload = await broker.execute_hardware_action(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                action_id="shell.exec",
                arguments={"command": "rm -rf /tmp/demo", "password": "secret"},
                runtime_target="user_device_gateway",
                runtime_access_mode="full_access",
                gateway_id="gw-1",
                run_id="run-1",
                trace_id="trace-1",
                request_id="req-full",
                session_id="hrs-full",
                trace_context=_trace(),
                agent_scope="sage",
            )

        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["runtime_session"]["runtime_access_mode"], "full_access")
        self.assertFalse(payload["runtime_session"]["empyralis_action_approvals_enabled"])
        request_approval_mock.assert_not_awaited()
        emit_approval.assert_not_awaited()
        execute_mock.assert_awaited_once()
        execute_kwargs = execute_mock.await_args.kwargs
        self.assertEqual(execute_kwargs["runtime_access_mode"], "full_access")
        self.assertTrue(execute_kwargs["empyralis_approved"])
        self.assertEqual(execute_kwargs["agent_scope"], "sage")

    async def test_cloud_computer_browser_action_uses_virtual_runtime_adapter(self) -> None:
        create_patch, extend_patch, started_patch, result_patch, artifact_patch, approval_patch = self._session_patches()
        cloud_runtime = _FakeCloudRuntime()
        cloud_registry = _FakeCloudRuntimeRegistry(cloud_runtime)
        with (
            create_patch,
            extend_patch,
            started_patch,
            result_patch as emit_result,
            artifact_patch as emit_artifact,
            approval_patch,
            patch(
                "server_modules.hardware_action_broker_service.get_cloud_computer_runtime_registry",
                return_value=cloud_registry,
            ),
        ):
            payload = await broker.execute_hardware_action(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                action_id="browser.open",
                arguments={"url": "https://example.com"},
                runtime_target="empyralis_cloud_computer",
                run_id="run-1",
                trace_id="trace-1",
                request_id="req-cloud",
                session_id="hrs-cloud-computer",
                trace_context=_trace(),
            )

        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["runtime_session"]["state"], "ready")
        self.assertEqual(payload["runtime_session"]["canonical_runtime_target"], "empyralis_cloud_computer")
        self.assertEqual(payload["runtime_session"]["runtime_choice"], "virtual_browser")
        self.assertEqual(payload["execution"]["action"], "open_url")
        self.assertEqual(cloud_registry.resolve_calls[0]["runtime_choice"], "virtual_browser")
        cloud_runtime.create_session.assert_awaited_once()
        create_payload = cloud_runtime.create_session.await_args.args[0]
        self.assertTrue(create_payload["ephemeral_task"])
        self.assertEqual(create_payload["session_persistence"]["session_mode"], "ephemeral")
        self.assertTrue(create_payload["session_persistence"]["auto_terminate_on_task_complete"])
        cloud_runtime.execute_action.assert_awaited_once()
        action_payload = cloud_runtime.execute_action.await_args.args[0]
        self.assertEqual(action_payload["action"], "open_url")
        self.assertEqual(action_payload["action_args"]["url"], "https://example.com")
        cloud_runtime.stream_screenshot.assert_not_awaited()
        emit_artifact.assert_not_awaited()
        emit_result.assert_awaited()

    async def test_cloud_computer_passes_certification_control_metadata_to_runtime(self) -> None:
        create_patch, extend_patch, started_patch, result_patch, artifact_patch, approval_patch = self._session_patches()
        cloud_runtime = _FakeCloudRuntime()
        cloud_registry = _FakeCloudRuntimeRegistry(cloud_runtime)
        with (
            create_patch,
            extend_patch,
            started_patch,
            result_patch,
            artifact_patch,
            approval_patch,
            patch(
                "server_modules.hardware_action_broker_service.get_cloud_computer_runtime_registry",
                return_value=cloud_registry,
            ),
        ):
            payload = await broker.execute_hardware_action(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                action_id="browser.open",
                arguments={"url": "https://example.com"},
                runtime_target="empyralis_cloud_computer",
                runtime_access_mode="full_access",
                run_id="run-1",
                trace_id="trace-1",
                request_id="req-cloud-quota",
                session_id="hrs-cloud-computer",
                cost_metadata={
                    "runtime_quota": {"provider_concurrency_limit": 1},
                    "cost_quota": {"max_estimated_cost": 1.25},
                    "computer_automation": {"provider_health": "healthy"},
                    "ignored_user_field": {"raw": "should not reach runtime create"},
                },
                trace_context=_trace(),
            )

        self.assertEqual(payload["status"], "completed")
        cloud_runtime.create_session.assert_awaited_once()
        create_payload = cloud_runtime.create_session.await_args.args[0]
        self.assertEqual(create_payload["runtime_quota"], {"provider_concurrency_limit": 1})
        self.assertEqual(create_payload["cost_quota"], {"max_estimated_cost": 1.25})
        self.assertEqual(create_payload["computer_automation"], {"provider_health": "healthy"})
        self.assertNotIn("ignored_user_field", create_payload)

    async def test_cloud_computer_screenshot_streams_artifact_without_gateway(self) -> None:
        create_patch, extend_patch, started_patch, result_patch, artifact_patch, approval_patch = self._session_patches()
        cloud_runtime = _FakeCloudRuntime()
        cloud_registry = _FakeCloudRuntimeRegistry(cloud_runtime)
        with (
            create_patch,
            extend_patch,
            started_patch,
            result_patch as emit_result,
            artifact_patch as emit_artifact,
            approval_patch,
            patch(
                "server_modules.hardware_action_broker_service.get_cloud_computer_runtime_registry",
                return_value=cloud_registry,
            ),
        ):
            payload = await broker.execute_hardware_action(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                action_id="screenshot.capture",
                runtime_target="empyralis_cloud_computer",
                run_id="run-1",
                trace_id="trace-1",
                request_id="req-cloud-shot",
                session_id="hrs-cloud-computer",
                trace_context=_trace(),
            )

        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["artifacts"], ["artifact-cloud-shot"])
        self.assertEqual(payload["runtime_session"]["artifacts"], ["artifact-cloud-shot"])
        self.assertEqual(cloud_registry.resolve_calls[0]["runtime_choice"], "virtual_browser")
        cloud_runtime.create_session.assert_awaited_once()
        create_payload = cloud_runtime.create_session.await_args.args[0]
        self.assertTrue(create_payload["ephemeral_task"])
        self.assertEqual(create_payload["session_persistence"]["session_mode"], "ephemeral")
        self.assertTrue(create_payload["session_persistence"]["auto_terminate_on_task_complete"])
        cloud_runtime.stream_screenshot.assert_awaited_once()
        cloud_runtime.execute_action.assert_not_awaited()
        emit_artifact.assert_awaited_once()
        self.assertEqual(emit_result.await_args.kwargs["artifact_ids"], ["artifact-cloud-shot"])
        self.assertEqual(emit_result.await_args.kwargs["tool_name"], "screenshot.capture")
        self.assertEqual(emit_result.await_args.kwargs["runtime_session_id"], "hrs-cloud-computer")
        self.assertEqual(emit_result.await_args.kwargs["runtime_target"], "empyralis_cloud_computer")

    async def test_cloud_computer_risky_shell_waits_for_approval_without_execution(self) -> None:
        create_patch, extend_patch, started_patch, result_patch, artifact_patch, approval_patch = self._session_patches()
        cloud_runtime = _FakeCloudRuntime()
        cloud_registry = _FakeCloudRuntimeRegistry(cloud_runtime)
        with (
            create_patch,
            extend_patch,
            started_patch,
            result_patch,
            artifact_patch,
            approval_patch as emit_approval,
            patch(
                "server_modules.hardware_action_broker_service.get_cloud_computer_runtime_registry",
                return_value=cloud_registry,
            ),
        ):
            payload = await broker.execute_hardware_action(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                action_id="shell.exec",
                arguments={"command": "rm -rf /tmp/demo", "password": "secret"},
                runtime_target="empyralis_cloud_computer",
                run_id="run-1",
                trace_id="trace-1",
                request_id="req-cloud-shell",
                session_id="hrs-cloud-computer",
                trace_context=_trace(),
            )

        self.assertEqual(payload["status"], "waiting_approval")
        self.assertEqual(payload["runtime_session"]["state"], "waiting_approval")
        approval_args = payload["runtime_session"]["approvals"][0]["request_payload"]["arguments"]
        self.assertNotIn("password", approval_args)
        self.assertEqual(approval_args["command"], "rm -rf /tmp/demo")
        self.assertEqual(cloud_registry.resolve_calls, [])
        cloud_runtime.create_session.assert_not_awaited()
        cloud_runtime.execute_action.assert_not_awaited()
        emit_approval.assert_awaited_once()

    async def test_cloud_computer_full_access_shell_executes_without_approval(self) -> None:
        create_patch, extend_patch, started_patch, result_patch, artifact_patch, approval_patch = self._session_patches()
        cloud_runtime = _FakeCloudRuntime()
        cloud_registry = _FakeCloudRuntimeRegistry(cloud_runtime)
        with (
            create_patch,
            extend_patch,
            started_patch,
            result_patch,
            artifact_patch,
            approval_patch as emit_approval,
            patch(
                "server_modules.hardware_action_broker_service.get_cloud_computer_runtime_registry",
                return_value=cloud_registry,
            ),
        ):
            payload = await broker.execute_hardware_action(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                action_id="shell.exec",
                arguments={"command": "rm -rf /tmp/demo"},
                runtime_target="empyralis_cloud_computer",
                runtime_access_mode="full_access",
                run_id="run-1",
                trace_id="trace-1",
                request_id="req-cloud-full",
                session_id="hrs-cloud-computer",
                trace_context=_trace(),
            )

        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["runtime_session"]["runtime_access_mode"], "full_access")
        self.assertEqual(cloud_registry.resolve_calls[0]["runtime_choice"], "virtual_code_sandbox")
        cloud_runtime.create_session.assert_awaited_once()
        cloud_runtime.execute_action.assert_awaited_once()
        emit_approval.assert_not_awaited()

    async def test_cloud_computer_unsupported_file_action_returns_degraded(self) -> None:
        create_patch, extend_patch, started_patch, result_patch, artifact_patch, approval_patch = self._session_patches()
        cloud_runtime = _FakeCloudRuntime()
        cloud_registry = _FakeCloudRuntimeRegistry(cloud_runtime)
        with (
            create_patch,
            extend_patch,
            started_patch,
            result_patch,
            artifact_patch,
            approval_patch,
            patch(
                "server_modules.hardware_action_broker_service.get_cloud_computer_runtime_registry",
                return_value=cloud_registry,
            ),
        ):
            payload = await broker.execute_hardware_action(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                action_id="file.read",
                arguments={"path": "/tmp/demo.txt"},
                runtime_target="empyralis_cloud_computer",
                run_id="run-1",
                trace_id="trace-1",
                request_id="req-cloud-file",
                session_id="hrs-cloud-computer",
                trace_context=_trace(),
            )

        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["reason"], "cloud_computer_action_not_supported")
        self.assertEqual(payload["runtime_session"]["state"], "degraded")
        self.assertEqual(cloud_registry.resolve_calls, [])
        cloud_runtime.create_session.assert_not_awaited()

    async def test_self_hosted_node_action_enqueues_command_after_gate(self) -> None:
        create_patch, extend_patch, started_patch, result_patch, artifact_patch, approval_patch = self._session_patches()
        enqueue_mock = AsyncMock(
            return_value={
                "ok": True,
                "runtime_profile_id": "rprof-self",
                "runtime_node_id": "node-1",
                "workspace_id": "ws-1",
                "command": {
                    "id": "shcmd-1",
                    "state": "queued",
                    "command_type": "hardware_action",
                },
            }
        )
        with (
            create_patch,
            extend_patch,
            started_patch,
            result_patch as emit_result,
            artifact_patch,
            approval_patch,
            patch(
                "server_modules.hardware_action_broker_service.runtime_attachment_service.list_workspace_runtime_attachments",
                new=AsyncMock(return_value=_self_hosted_inventory()),
            ),
            patch(
                "server_modules.hardware_action_broker_service.agent_registry_repository.enqueue_self_hosted_runtime_command",
                enqueue_mock,
            ),
        ):
            payload = await broker.execute_hardware_action(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                action_id="shell.exec",
                arguments={"command": "printf hello"},
                runtime_target="self_hosted_node",
                node_id="node-1",
                run_id="run-1",
                trace_id="trace-1",
                request_id="req-self-host",
                session_id="hrs-self-host",
                trace_context=_trace(),
                require_approval=False,
            )

        self.assertEqual(payload["status"], "running")
        self.assertEqual(payload["runtime_session"]["state"], "running")
        self.assertEqual(payload["runtime_session"]["runtime_node_id"], "node-1")
        self.assertEqual(payload["runtime_session"]["runtime_profile_id"], "rprof-self")
        self.assertEqual(payload["runtime_session"]["self_hosted_command_id"], "shcmd-1")
        enqueue_mock.assert_awaited_once()
        enqueue_kwargs = enqueue_mock.await_args.kwargs
        self.assertEqual(enqueue_kwargs["runtime_profile_id"], "rprof-self")
        self.assertEqual(enqueue_kwargs["runtime_node_id"], "node-1")
        self.assertEqual(enqueue_kwargs["command_type"], "hardware_action")
        self.assertEqual(enqueue_kwargs["command_payload"]["arguments"]["command"], "printf hello")
        emit_result.assert_awaited()
        self.assertEqual(emit_result.await_args.kwargs["status"], "running")
        self.assertEqual(emit_result.await_args.kwargs["tool_name"], "shell.execute")
        self.assertEqual(emit_result.await_args.kwargs["runtime_session_id"], "hrs-self-host")
        self.assertEqual(emit_result.await_args.kwargs["runtime_target"], "self_hosted_node")
        self.assertEqual(emit_result.await_args.kwargs["args_preview"]["command"], "printf hello")

    async def test_self_hosted_node_risky_action_waits_for_approval_without_enqueue(self) -> None:
        create_patch, extend_patch, started_patch, result_patch, artifact_patch, approval_patch = self._session_patches()
        enqueue_mock = AsyncMock()
        with (
            create_patch,
            extend_patch,
            started_patch,
            result_patch,
            artifact_patch,
            approval_patch as emit_approval,
            patch(
                "server_modules.hardware_action_broker_service.runtime_attachment_service.list_workspace_runtime_attachments",
                new=AsyncMock(return_value=_self_hosted_inventory()),
            ),
            patch(
                "server_modules.hardware_action_broker_service.agent_registry_repository.enqueue_self_hosted_runtime_command",
                enqueue_mock,
            ),
        ):
            payload = await broker.execute_hardware_action(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                action_id="shell.exec",
                arguments={"command": "rm -rf /tmp/demo", "password": "secret"},
                runtime_target="self_hosted_node",
                node_id="node-1",
                run_id="run-1",
                trace_id="trace-1",
                request_id="req-self-approval",
                session_id="hrs-self-host",
                trace_context=_trace(),
            )

        self.assertEqual(payload["status"], "waiting_approval")
        self.assertEqual(payload["runtime_session"]["state"], "waiting_approval")
        self.assertEqual(
            payload["runtime_session"]["approvals"][0]["request_payload"]["arguments"]["password"],
            "[redacted]",
        )
        enqueue_mock.assert_not_awaited()
        emit_approval.assert_awaited_once()

    async def test_self_hosted_node_full_access_risky_action_enqueues_without_approval(self) -> None:
        create_patch, extend_patch, started_patch, result_patch, artifact_patch, approval_patch = self._session_patches()
        enqueue_mock = AsyncMock(
            return_value={
                "ok": True,
                "runtime_profile_id": "rprof-self",
                "runtime_node_id": "node-1",
                "workspace_id": "ws-1",
                "command": {
                    "id": "shcmd-full",
                    "state": "queued",
                    "command_type": "hardware_action",
                },
            }
        )
        with (
            create_patch,
            extend_patch,
            started_patch,
            result_patch,
            artifact_patch,
            approval_patch as emit_approval,
            patch(
                "server_modules.hardware_action_broker_service.runtime_attachment_service.list_workspace_runtime_attachments",
                new=AsyncMock(return_value=_self_hosted_inventory()),
            ),
            patch(
                "server_modules.hardware_action_broker_service.agent_registry_repository.enqueue_self_hosted_runtime_command",
                enqueue_mock,
            ),
        ):
            payload = await broker.execute_hardware_action(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                action_id="shell.exec",
                arguments={"command": "rm -rf /tmp/demo", "password": "secret"},
                runtime_target="self_hosted_node",
                runtime_access_mode="full_access",
                node_id="node-1",
                run_id="run-1",
                trace_id="trace-1",
                request_id="req-self-full",
                session_id="hrs-self-host",
                trace_context=_trace(),
            )

        self.assertEqual(payload["status"], "running")
        self.assertEqual(payload["runtime_session"]["runtime_access_mode"], "full_access")
        enqueue_mock.assert_awaited_once()
        command_payload = enqueue_mock.await_args.kwargs["command_payload"]
        self.assertEqual(command_payload["runtime_access_mode"], "full_access")
        self.assertEqual(command_payload["runtime_session_id"], "hrs-self-host")
        emit_approval.assert_not_awaited()

    async def test_self_hosted_node_offline_returns_offline_without_enqueue(self) -> None:
        create_patch, extend_patch, started_patch, result_patch, artifact_patch, approval_patch = self._session_patches()
        enqueue_mock = AsyncMock()
        with (
            create_patch,
            extend_patch,
            started_patch,
            result_patch,
            artifact_patch,
            approval_patch,
            patch(
                "server_modules.hardware_action_broker_service.runtime_attachment_service.list_workspace_runtime_attachments",
                new=AsyncMock(return_value=_self_hosted_inventory(status="offline", online=False, healthy=False)),
            ),
            patch(
                "server_modules.hardware_action_broker_service.agent_registry_repository.enqueue_self_hosted_runtime_command",
                enqueue_mock,
            ),
        ):
            payload = await broker.execute_hardware_action(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                action_id="shell.exec",
                arguments={"command": "printf hello"},
                runtime_target="self_hosted_node",
                node_id="node-1",
                run_id="run-1",
                trace_id="trace-1",
                request_id="req-self-offline",
                session_id="hrs-self-host",
                trace_context=_trace(),
                require_approval=False,
            )

        self.assertEqual(payload["status"], "offline")
        self.assertEqual(payload["reason"], "self_hosted_node_offline")
        enqueue_mock.assert_not_awaited()

    async def test_self_hosted_command_completion_updates_original_runtime_session(self) -> None:
        _create_patch, extend_patch, _started_patch, result_patch, artifact_patch, _approval_patch = self._session_patches()
        completion = {
            "ok": True,
            "status": "completed",
            "command_id": "shcmd-1",
            "command": {
                "id": "shcmd-1",
                "command_payload": {
                    "runtime_session_binding": broker.HARDWARE_RUNTIME_SESSION_BINDING,
                    "runtime_session_id": "hrs-self-host",
                    "runtime_target": "self_hosted_node",
                    "runtime_access_mode": "full_access",
                    "tenant_id": "tenant-1",
                    "workspace_id": "ws-1",
                    "thread_id": "thread-1",
                    "run_id": "run-1",
                    "trace_id": "trace-1",
                    "request_id": "req-self-host",
                    "capability_id": "shell.execute",
                    "action_id": "shell.exec",
                    "arguments": {"command": "uptime"},
                    "runtime_node_id": "node-1",
                    "runtime_profile_id": "rprof-self",
                },
            },
            "result_payload": {"summary": "Command completed."},
            "artifacts": [{"artifact_id": "artifact-self"}],
        }
        with (
            extend_patch as extend_session,
            result_patch as emit_result,
            artifact_patch as emit_artifact,
            patch(
                "server_modules.hardware_action_broker_service.session_service.get_session",
                new=AsyncMock(
                    return_value={
                        "session_id": "hrs-self-host",
                        "tenant_id": "tenant-1",
                        "workspace_id": "ws-1",
                        "metadata": {
                            "runtime_session_binding": broker.HARDWARE_RUNTIME_SESSION_BINDING,
                            "runtime_session_id": "hrs-self-host",
                            "runtime_target": "self_hosted_node",
                            "canonical_runtime_target": "self_hosted_node",
                            "runtime_access_mode": "full_access",
                            "state": "running",
                            "run_id": "run-1",
                            "trace_id": "trace-1",
                            "request_id": "req-self-host",
                            "capability_id": "shell.execute",
                        },
                    }
                ),
            ),
            patch(
                "server_modules.hardware_action_broker_service.agent_trace_service.resume_trace",
                new=AsyncMock(return_value=_trace()),
            ),
            patch(
                "server_modules.hardware_action_broker_service.thread_service.append_assistant_turn_transcript_event",
                new=AsyncMock(return_value={"turn": {"id": "turn-assistant"}}),
            ) as append_transcript,
        ):
            payload = await broker.record_self_hosted_command_completion(completion)

        self.assertEqual(payload["runtime_session"]["state"], "ready")
        self.assertEqual(payload["runtime_session"]["self_hosted_command_id"], "shcmd-1")
        self.assertEqual(payload["runtime_session"]["artifacts"], ["artifact-self"])
        extend_session.assert_awaited_once()
        emit_artifact.assert_awaited_once()
        emit_result.assert_awaited_once()
        self.assertEqual(emit_result.await_args.kwargs["status"], "completed")
        self.assertEqual(emit_result.await_args.kwargs["tool_name"], "shell.execute")
        self.assertEqual(emit_result.await_args.kwargs["runtime_session_id"], "hrs-self-host")
        self.assertEqual(emit_result.await_args.kwargs["runtime_target"], "self_hosted_node")
        self.assertEqual(emit_result.await_args.kwargs["request_id"], "req-self-host")
        self.assertEqual(emit_result.await_args.kwargs["action_id"], "shell.exec")
        self.assertEqual(emit_result.await_args.kwargs["args_preview"]["command"], "uptime")
        self.assertEqual(append_transcript.await_count, 2)
        transcript_payloads = [call.kwargs["payload"] for call in append_transcript.await_args_list]
        self.assertEqual(transcript_payloads[0]["event_type"], "artifact.created")
        self.assertEqual(transcript_payloads[0]["artifact_id"], "artifact-self")
        self.assertEqual(transcript_payloads[1]["event_type"], "tool.result")
        self.assertEqual(transcript_payloads[1]["data"]["status"], "completed")
        self.assertEqual(transcript_payloads[1]["data"]["runtime_session_id"], "hrs-self-host")
        self.assertNotIn("arguments", transcript_payloads[1]["data"])

    async def test_gateway_approval_execution_updates_original_runtime_session(self) -> None:
        _create_patch, extend_patch, _started_patch, result_patch, artifact_patch, _approval_patch = self._session_patches()
        result = {
            "status": "executed",
            "approval": {
                "approval_id": "gapproval-1",
                "decision": "approved",
                "request_payload": {
                    "runtime_session_binding": broker.HARDWARE_RUNTIME_SESSION_BINDING,
                    "runtime_session_id": "hrs-gateway",
                    "runtime_target": "user_device_gateway",
                    "runtime_access_mode": "default_guarded",
                    "tenant_id": "tenant-1",
                    "workspace_id": "ws-1",
                    "thread_id": "thread-1",
                    "run_id": "run-1",
                    "trace_id": "trace-1",
                    "request_id": "req-gateway",
                    "capability_id": "shell.execute",
                    "action_id": "shell.exec",
                    "arguments": {"command": "whoami"},
                },
            },
            "execution": {
                "request_id": "req-gateway",
                "result": {
                    "summary": "Ran command.",
                    "artifacts": [{"id": "artifact-gateway"}],
                },
            },
        }
        with (
            extend_patch as extend_session,
            result_patch as emit_result,
            artifact_patch as emit_artifact,
            patch(
                "server_modules.hardware_action_broker_service.session_service.get_session",
                new=AsyncMock(
                    return_value={
                        "session_id": "hrs-gateway",
                        "tenant_id": "tenant-1",
                        "workspace_id": "ws-1",
                        "metadata": {
                            "runtime_session_binding": broker.HARDWARE_RUNTIME_SESSION_BINDING,
                            "runtime_session_id": "hrs-gateway",
                            "runtime_target": "user_device_gateway",
                            "canonical_runtime_target": "user_device_gateway",
                            "runtime_access_mode": "default_guarded",
                            "state": "waiting_approval",
                            "run_id": "run-1",
                            "trace_id": "trace-1",
                            "request_id": "req-gateway",
                            "capability_id": "shell.execute",
                        },
                    }
                ),
            ),
            patch(
                "server_modules.hardware_action_broker_service.agent_trace_service.resume_trace",
                new=AsyncMock(return_value=_trace()),
            ),
            patch(
                "server_modules.hardware_action_broker_service.agent_trace_service.emit_approval_resolved",
                new=AsyncMock(return_value="evt-approval-resolved"),
            ) as emit_resolved,
            patch(
                "server_modules.hardware_action_broker_service.thread_service.append_assistant_turn_transcript_event",
                new=AsyncMock(return_value={"turn": {"id": "turn-assistant"}}),
            ) as append_transcript,
        ):
            payload = await broker.record_gateway_approval_resolution(result, actor="user-1")

        self.assertEqual(payload["runtime_session"]["state"], "ready")
        self.assertEqual(payload["runtime_session"]["artifacts"], ["artifact-gateway"])
        extend_session.assert_awaited_once()
        emit_resolved.assert_awaited_once()
        emit_artifact.assert_awaited_once()
        emit_result.assert_awaited_once()
        self.assertEqual(emit_result.await_args.kwargs["status"], "completed")
        self.assertEqual(emit_result.await_args.kwargs["tool_name"], "shell.execute")
        self.assertEqual(emit_result.await_args.kwargs["runtime_session_id"], "hrs-gateway")
        self.assertEqual(emit_result.await_args.kwargs["runtime_target"], "user_device_gateway")
        self.assertEqual(emit_result.await_args.kwargs["request_id"], "req-gateway")
        self.assertEqual(emit_result.await_args.kwargs["action_id"], "shell.exec")
        self.assertEqual(emit_result.await_args.kwargs["args_preview"]["command"], "whoami")
        self.assertEqual(append_transcript.await_count, 3)
        transcript_payloads = [call.kwargs["payload"] for call in append_transcript.await_args_list]
        self.assertEqual(transcript_payloads[0]["event_type"], "approval.resolved")
        self.assertEqual(transcript_payloads[1]["event_type"], "artifact.created")
        self.assertEqual(transcript_payloads[2]["event_type"], "tool.result")
        self.assertEqual(transcript_payloads[2]["data"]["status"], "completed")
        self.assertEqual(transcript_payloads[2]["data"]["runtime_target"], "user_device_gateway")

    async def test_cloud_computer_runtime_approval_resume_executes_stored_request(self) -> None:
        request_payload = {
            "runtime_session_binding": broker.HARDWARE_RUNTIME_SESSION_BINDING,
            "runtime_session_id": "hrs-cloud-approval",
            "runtime_target": "empyralis_cloud_computer",
            "runtime_access_mode": "default_guarded",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "thread_id": "thread-1",
            "run_id": "run-1",
            "trace_id": "trace-1",
            "request_id": "req-cloud-approval",
            "capability_id": "shell.execute",
            "action_id": "shell.exec",
            "arguments": {"command": "rm -rf /tmp/demo"},
        }
        cloud_runtime = _FakeCloudRuntime()
        cloud_registry = _FakeCloudRuntimeRegistry(cloud_runtime)
        with (
            patch(
                "server_modules.hardware_action_broker_service.session_service.find_runtime_session_by_approval_id",
                new=AsyncMock(
                    return_value=_hardware_approval_session_record(
                        runtime_target="empyralis_cloud_computer",
                        approval_id="cloudapproval-1",
                        request_payload=request_payload,
                    )
                ),
            ) as find_session,
            patch(
                "server_modules.hardware_action_broker_service.session_service.extend_session",
                new=AsyncMock(return_value=None),
            ) as extend_session,
            patch(
                "server_modules.hardware_action_broker_service.agent_trace_service.resume_trace",
                new=AsyncMock(return_value=_trace()),
            ),
            patch(
                "server_modules.hardware_action_broker_service.agent_trace_service.emit_approval_resolved",
                new=AsyncMock(return_value="evt-approval-resolved"),
            ) as emit_resolved,
            patch(
                "server_modules.hardware_action_broker_service.agent_trace_service.emit_tool_result",
                new=AsyncMock(return_value="evt-result"),
            ) as emit_result,
            patch(
                "server_modules.hardware_action_broker_service.agent_trace_service.emit_artifact_created",
                new=AsyncMock(return_value="evt-artifact"),
            ),
            patch(
                "server_modules.hardware_action_broker_service.get_cloud_computer_runtime_registry",
                return_value=cloud_registry,
            ),
        ):
            payload = await broker.resolve_runtime_hardware_approval(
                "cloudapproval-1",
                decision="approved",
                actor="user-1",
                note="ok",
                workspace_id="ws-1",
                approval_scope="session",
            )

        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["runtime_session"]["state"], "ready")
        self.assertEqual(payload["approval"]["status"], "executed")
        self.assertEqual(payload["approval"]["approval_scope"], "session")
        find_session.assert_awaited_once()
        self.assertGreaterEqual(extend_session.await_count, 3)
        emit_resolved.assert_awaited_once()
        cloud_runtime.execute_action.assert_awaited_once()
        action_payload = cloud_runtime.execute_action.await_args.args[0]
        self.assertEqual(action_payload["action"], broker.virtual_computer_runtime.ACTION_RUN_COMMAND)
        self.assertEqual(action_payload["action_args"]["command"], "rm -rf /tmp/demo")
        emit_result.assert_awaited()
        self.assertEqual(emit_result.await_args.kwargs["runtime_session_id"], "hrs-cloud-approval")

    async def test_self_hosted_runtime_approval_resume_enqueues_stored_request(self) -> None:
        request_payload = {
            "runtime_session_binding": broker.HARDWARE_RUNTIME_SESSION_BINDING,
            "runtime_session_id": "hrs-self-approval",
            "runtime_target": "self_hosted_node",
            "runtime_access_mode": "default_guarded",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "thread_id": "thread-1",
            "run_id": "run-1",
            "trace_id": "trace-1",
            "request_id": "req-self-approval",
            "capability_id": "shell.execute",
            "action_id": "shell.exec",
            "runtime_node_id": "node-1",
            "runtime_profile_id": "rprof-self",
            "arguments": {"command": "rm -rf /tmp/demo"},
        }
        enqueue_mock = AsyncMock(
            return_value={
                "ok": True,
                "runtime_profile_id": "rprof-self",
                "runtime_node_id": "node-1",
                "workspace_id": "ws-1",
                "command": {
                    "id": "shcmd-resumed",
                    "state": "queued",
                    "command_type": "hardware_action",
                },
            }
        )
        with (
            patch(
                "server_modules.hardware_action_broker_service.session_service.find_runtime_session_by_approval_id",
                new=AsyncMock(
                    return_value=_hardware_approval_session_record(
                        runtime_target="self_hosted_node",
                        approval_id="selfhostapproval-1",
                        request_payload=request_payload,
                    )
                ),
            ),
            patch(
                "server_modules.hardware_action_broker_service.session_service.extend_session",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "server_modules.hardware_action_broker_service.agent_trace_service.resume_trace",
                new=AsyncMock(return_value=_trace()),
            ),
            patch(
                "server_modules.hardware_action_broker_service.agent_trace_service.emit_approval_resolved",
                new=AsyncMock(return_value="evt-approval-resolved"),
            ) as emit_resolved,
            patch(
                "server_modules.hardware_action_broker_service.agent_trace_service.emit_tool_result",
                new=AsyncMock(return_value="evt-result"),
            ),
            patch(
                "server_modules.hardware_action_broker_service.runtime_attachment_service.list_workspace_runtime_attachments",
                new=AsyncMock(return_value=_self_hosted_inventory()),
            ),
            patch(
                "server_modules.hardware_action_broker_service.agent_registry_repository.enqueue_self_hosted_runtime_command",
                enqueue_mock,
            ),
        ):
            payload = await broker.resolve_runtime_hardware_approval(
                "selfhostapproval-1",
                decision="approved",
                actor="user-1",
                note="ok",
                workspace_id="ws-1",
            )

        self.assertEqual(payload["status"], "running")
        self.assertEqual(payload["runtime_session"]["state"], "running")
        self.assertEqual(payload["approval"]["status"], "executed")
        emit_resolved.assert_awaited_once()
        enqueue_mock.assert_awaited_once()
        command_payload = enqueue_mock.await_args.kwargs["command_payload"]
        self.assertEqual(command_payload["runtime_session_id"], "hrs-self-approval")
        self.assertEqual(command_payload["arguments"]["command"], "rm -rf /tmp/demo")

    async def test_runtime_approval_deny_terminates_without_execution(self) -> None:
        request_payload = {
            "runtime_session_binding": broker.HARDWARE_RUNTIME_SESSION_BINDING,
            "runtime_session_id": "hrs-cloud-deny",
            "runtime_target": "empyralis_cloud_computer",
            "runtime_access_mode": "default_guarded",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "thread_id": "thread-1",
            "run_id": "run-1",
            "trace_id": "trace-1",
            "request_id": "req-cloud-deny",
            "capability_id": "shell.execute",
            "action_id": "shell.exec",
            "arguments": {"command": "rm -rf /tmp/demo"},
        }
        cloud_runtime = _FakeCloudRuntime()
        with (
            patch(
                "server_modules.hardware_action_broker_service.session_service.find_runtime_session_by_approval_id",
                new=AsyncMock(
                    return_value=_hardware_approval_session_record(
                        runtime_target="empyralis_cloud_computer",
                        approval_id="cloudapproval-deny",
                        request_payload=request_payload,
                    )
                ),
            ),
            patch(
                "server_modules.hardware_action_broker_service.session_service.extend_session",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "server_modules.hardware_action_broker_service.agent_trace_service.resume_trace",
                new=AsyncMock(return_value=_trace()),
            ),
            patch(
                "server_modules.hardware_action_broker_service.agent_trace_service.emit_approval_resolved",
                new=AsyncMock(return_value="evt-approval-resolved"),
            ) as emit_resolved,
            patch(
                "server_modules.hardware_action_broker_service.agent_trace_service.emit_tool_result",
                new=AsyncMock(return_value="evt-result"),
            ) as emit_result,
            patch(
                "server_modules.hardware_action_broker_service.get_cloud_computer_runtime_registry",
                return_value=_FakeCloudRuntimeRegistry(cloud_runtime),
            ),
        ):
            payload = await broker.resolve_runtime_hardware_approval(
                "cloudapproval-deny",
                decision="rejected",
                actor="user-1",
                note="no",
                workspace_id="ws-1",
            )

        self.assertEqual(payload["status"], "rejected")
        self.assertEqual(payload["runtime_session"]["state"], "terminated")
        self.assertEqual(payload["approval"]["status"], "rejected")
        emit_resolved.assert_awaited_once()
        emit_result.assert_awaited_once()
        self.assertEqual(emit_result.await_args.kwargs["status"], "terminated")
        cloud_runtime.execute_action.assert_not_awaited()

    async def test_stop_gateway_action_interrupts_and_marks_session_terminated(self) -> None:
        interrupt_mock = AsyncMock(
            return_value={
                "gateway_id": "gw-1",
                "device_id": "device-1",
                "workspace_id": "ws-1",
                "request_id": "stop-1",
                "run_id": "run-1",
                "interrupted": True,
                "interrupt_count": 1,
            }
        )
        with (
            patch(
                "server_modules.hardware_action_broker_service.session_service.extend_session",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "server_modules.hardware_action_broker_service.gateway_state_repository.get_gateway_registration",
                return_value=_registration(),
            ),
            patch(
                "server_modules.hardware_action_broker_service.gateway_protocol_service.gateway_connection_is_live",
                return_value=True,
            ),
            patch(
                "server_modules.hardware_action_broker_service.gateway_execution_service.interrupt_tool_via_gateway",
                interrupt_mock,
            ),
        ):
            payload = await broker.stop_hardware_action(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                runtime_target="user_device_gateway",
                gateway_id="gw-1",
                run_id="run-1",
                trace_id="trace-1",
                request_id="stop-1",
                target_request_id="req-1",
                reason="operator_requested_stop",
                session_id="hrs-gateway",
            )

        self.assertEqual(payload["status"], "terminated")
        self.assertEqual(payload["runtime_session"]["state"], "terminated")
        interrupt_mock.assert_awaited_once()
        interrupt_kwargs = interrupt_mock.await_args.kwargs
        self.assertEqual(interrupt_kwargs["gateway_id"], "gw-1")
        self.assertEqual(interrupt_kwargs["target_request_id"], "req-1")

    async def test_stop_cloud_computer_action_terminates_virtual_runtime_session(self) -> None:
        cloud_runtime = _FakeCloudRuntime()
        cloud_registry = _FakeCloudRuntimeRegistry(cloud_runtime)
        with (
            patch(
                "server_modules.hardware_action_broker_service.session_service.extend_session",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "server_modules.hardware_runtime_adapters.cloud_computer_adapter.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value={
                    "ok": True,
                    "decision": "allow",
                    "reason": "virtual_session_state_allowed",
                    "operation": "session_state",
                    "next_action": "assert_virtual_session_active",
                    "approval_required": False,
                    "cacheable": False,
                    "audit_visibility": "standard",
                },
            ),
            patch(
                "server_modules.hardware_action_broker_service.get_cloud_computer_runtime_registry",
                return_value=cloud_registry,
            ),
        ):
            payload = await broker.stop_hardware_action(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                runtime_target="empyralis_cloud_computer",
                run_id="run-1",
                trace_id="trace-1",
                request_id="stop-cloud",
                reason="operator_requested_stop",
                session_id="hrs-cloud-computer",
            )

        self.assertEqual(payload["status"], "terminated")
        self.assertEqual(payload["runtime_session"]["state"], "terminated")
        cloud_runtime.terminate_session.assert_awaited_once()
        terminate_payload = cloud_runtime.terminate_session.await_args.args[0]
        self.assertEqual(terminate_payload["session_id"], "hrs-cloud-computer")
        self.assertTrue(terminate_payload["manual_terminate"])

    async def test_stop_self_hosted_node_action_enqueues_cancel_command(self) -> None:
        enqueue_mock = AsyncMock(
            return_value={
                "ok": True,
                "runtime_profile_id": "rprof-self",
                "runtime_node_id": "node-1",
                "workspace_id": "ws-1",
                "command": {
                    "id": "shcmd-stop",
                    "state": "queued",
                    "command_type": "cancel_runtime_action",
                },
            }
        )
        with (
            patch(
                "server_modules.hardware_action_broker_service.session_service.extend_session",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "server_modules.hardware_action_broker_service.runtime_attachment_service.list_workspace_runtime_attachments",
                new=AsyncMock(return_value=_self_hosted_inventory()),
            ),
            patch(
                "server_modules.hardware_action_broker_service.agent_registry_repository.enqueue_self_hosted_runtime_command",
                enqueue_mock,
            ),
        ):
            payload = await broker.stop_hardware_action(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                runtime_target="self_hosted_node",
                node_id="node-1",
                run_id="run-1",
                trace_id="trace-1",
                request_id="stop-self",
                target_request_id="req-self-host",
                reason="operator_requested_stop",
                session_id="hrs-self-host",
            )

        self.assertEqual(payload["status"], "terminated")
        self.assertEqual(payload["runtime_session"]["state"], "terminated")
        enqueue_mock.assert_awaited_once()
        enqueue_kwargs = enqueue_mock.await_args.kwargs
        self.assertEqual(enqueue_kwargs["command_type"], "cancel_runtime_action")
        self.assertEqual(enqueue_kwargs["command_payload"]["target_request_id"], "req-self-host")

    async def test_hardware_action_emits_tool_progress_start_for_gateway_target(self) -> None:
        """F4: Verify tool.progress is emitted when a hardware action starts on a Mac gateway."""
        create_patch, extend_patch, started_patch, result_patch, artifact_patch, approval_patch = self._session_patches()
        execute_mock = AsyncMock(
            return_value={
                "gateway_id": "gw-1",
                "device_id": "device-1",
                "workspace_id": "ws-1",
                "request_id": "req-1",
                "capability_id": "shell.execute",
                "run_id": "run-1",
                "result": {"summary": "Command ran.", "artifacts": []},
            }
        )
        progress_mock = AsyncMock(return_value="evt-progress")
        with (
            create_patch,
            extend_patch,
            started_patch,
            result_patch,
            artifact_patch,
            approval_patch,
            patch(
                "server_modules.hardware_action_broker_service.gateway_state_repository.get_gateway_registration",
                return_value=_registration(),
            ),
            patch(
                "server_modules.hardware_action_broker_service.gateway_protocol_service.gateway_connection_is_live",
                return_value=True,
            ),
            patch(
                "server_modules.hardware_action_broker_service.gateway_approval_service.capability_requires_owner_approval",
                return_value=False,
            ),
            patch(
                "server_modules.hardware_action_broker_service.gateway_execution_service.execute_tool_via_gateway",
                execute_mock,
            ),
            patch(
                "server_modules.hardware_action_broker_service.agent_trace_service.emit_tool_progress",
                progress_mock,
            ),
        ):
            await broker.execute_hardware_action(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                action_id="shell.exec",
                arguments={"command": "echo hello"},
                runtime_target="user_device_gateway",
                gateway_id="gw-1",
                run_id="run-1",
                trace_id="trace-1",
                request_id="req-1",
                session_id="hrs-gateway",
                trace_context=_trace(),
            )

        # The broker emits start progress; the gateway adapter emits completion.
        progress_mock.assert_awaited()
        # First call: start progress from the broker
        start_call = progress_mock.await_args_list[0].kwargs
        self.assertEqual(start_call["message"], "Running shell.execute on your Agent Computer…")
        self.assertEqual(start_call["percent"], 0)
        # Second call: completion progress from the gateway adapter
        completion_call = progress_mock.await_args_list[-1].kwargs
        self.assertIn("Completed", completion_call["message"])
        self.assertEqual(completion_call["percent"], 100)

    async def test_hardware_action_emits_tool_progress_start_for_self_hosted_target(self) -> None:
        """F4: Verify tool.progress is emitted when a hardware action starts on a VPS node."""
        create_patch, extend_patch, started_patch, result_patch, artifact_patch, approval_patch = self._session_patches()
        enqueue_mock = AsyncMock(
            return_value={
                "ok": True,
                "runtime_profile_id": "rprof-self",
                "runtime_node_id": "node-1",
                "workspace_id": "ws-1",
                "command": {"id": "shcmd-1", "state": "queued", "command_type": "hardware_action"},
            }
        )
        progress_mock = AsyncMock(return_value="evt-progress")
        with (
            create_patch,
            extend_patch,
            started_patch,
            result_patch as emit_result,
            artifact_patch,
            approval_patch,
            patch(
                "server_modules.hardware_action_broker_service.runtime_attachment_service.list_workspace_runtime_attachments",
                new=AsyncMock(return_value=_self_hosted_inventory()),
            ),
            patch(
                "server_modules.hardware_action_broker_service.agent_registry_repository.enqueue_self_hosted_runtime_command",
                enqueue_mock,
            ),
            patch(
                "server_modules.hardware_action_broker_service.agent_trace_service.emit_tool_progress",
                progress_mock,
            ),
        ):
            await broker.execute_hardware_action(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                action_id="shell.exec",
                arguments={"command": "printf hello"},
                runtime_target="self_hosted_node",
                node_id="node-1",
                run_id="run-self-1",
                trace_id="trace-self-1",
                request_id="req-self",
                session_id="hrs-self-host",
                trace_context=_trace(),
                require_approval=False,
            )

        # The broker emits start progress; the VPS adapter also emits its own.
        progress_mock.assert_awaited()
        # First call: start progress from the broker
        first_call_kwargs = progress_mock.await_args_list[0].kwargs
        self.assertEqual(first_call_kwargs["message"], "Running shell.execute on your Server…")
        self.assertEqual(first_call_kwargs["percent"], 0)
        # The tool result should show status "running" (enqueued)
        emit_result.assert_awaited()
        self.assertEqual(emit_result.await_args.kwargs["status"], "running")

    async def _run_gateway_filesystem_write(self, *, file_mount_grants):
        """Drives execute_hardware_action for a filesystem.write to
        project/notes.txt with the given file_mount_grants, through the REAL
        (unmocked) gateway_adapter.execute_gateway_action code — including its
        real _resolve_file_mount_for_gateway_action call. Only
        gateway_execution_service.execute_tool_via_gateway is mocked (matching
        this file's own established convention, e.g. the
        test_gateway_connected_action_... test above), so mount resolution
        and its allow/deny outcome are exercised for real, while the deeper
        WSS/quota/kernel plumbing inside execute_tool_via_gateway itself is
        not (that plumbing is unrelated to file-mount grants and already has
        its own test coverage elsewhere in this file)."""
        create_patch, extend_patch, started_patch, result_patch, artifact_patch, approval_patch = self._session_patches()
        execute_mock = AsyncMock(
            return_value={
                "gateway_id": "gw-1",
                "device_id": "device-1",
                "workspace_id": "ws-1",
                "request_id": "req-1",
                "capability_id": "filesystem.read_write",
                "run_id": "run-1",
                "result": {"summary": "Wrote file.", "path": "project/notes.txt", "mode": "write"},
            }
        )
        with (
            create_patch,
            extend_patch,
            started_patch,
            result_patch,
            artifact_patch,
            approval_patch,
            patch(
                "server_modules.hardware_action_broker_service.gateway_state_repository.get_gateway_registration",
                return_value=_registration(),
            ),
            patch(
                "server_modules.hardware_action_broker_service.gateway_protocol_service.gateway_connection_is_live",
                return_value=True,
            ),
            patch(
                "server_modules.hardware_action_broker_service.gateway_approval_service.capability_requires_owner_approval",
                return_value=False,
            ),
            patch(
                "server_modules.hardware_action_broker_service.gateway_execution_service.execute_tool_via_gateway",
                execute_mock,
            ),
        ):
            payload = await broker.execute_hardware_action(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                action_id="filesystem.write",
                capability_id="filesystem.write",
                arguments={"path": "project/notes.txt", "content": "hi"},
                runtime_target="user_device_gateway",
                gateway_id="gw-1",
                run_id="run-1",
                trace_id="trace-1",
                request_id="req-1",
                session_id="hrs-gateway",
                trace_context=_trace(),
                require_approval=False,
                file_mount_grants=file_mount_grants,
            )
        return payload, execute_mock

    async def test_file_mount_grants_default_denies_write_to_project_mount(self) -> None:
        # No per-agent grants threaded at all — resolves against
        # file_mount_security.py's built-in defaults, where "project" is
        # read-only. This is the "old default" behavior being compared against.
        payload, execute_mock = await self._run_gateway_filesystem_write(file_mount_grants=None)
        self.assertEqual(payload["status"], "failed")
        execute_mock.assert_not_awaited()

    async def test_file_mount_grants_explicit_read_only_still_denies_write(self) -> None:
        # A real, explicitly-threaded grant of "read" for this agent on the
        # project mount — distinct from the generic default (this IS the
        # agent's actual configured grant, not a fallback) — still correctly
        # denies write.
        payload, execute_mock = await self._run_gateway_filesystem_write(
            file_mount_grants=[{"mount": "project", "grant": "read"}]
        )
        self.assertEqual(payload["status"], "failed")
        execute_mock.assert_not_awaited()

    async def test_file_mount_grants_real_per_agent_read_write_grant_allows_write(self) -> None:
        # This agent's real configured grant is read_write on project —
        # distinct from the generic default above, and the outcome changes
        # accordingly: the write is allowed and dispatched.
        payload, execute_mock = await self._run_gateway_filesystem_write(
            file_mount_grants=[{"mount": "project", "grant": "read_write"}]
        )
        self.assertEqual(payload["status"], "completed")
        execute_mock.assert_awaited_once()
        dispatched_arguments = execute_mock.await_args.kwargs["arguments"]
        self.assertEqual(dispatched_arguments["mount"], "project")
        self.assertEqual(dispatched_arguments["path"], "project/notes.txt")


if __name__ == "__main__":
    unittest.main()
