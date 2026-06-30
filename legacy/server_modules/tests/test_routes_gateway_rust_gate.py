import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from server_modules import routes_gateway


def _route_dependencies(path: str) -> list[str]:
    for route in routes_gateway.router.routes:
        if isinstance(route, APIRoute) and route.path == path:
            return [getattr(dep.call, "__name__", str(dep.call)) for dep in route.dependant.dependencies]
    raise AssertionError(f"Route {path} not found.")


def test_gateway_service_accepts_canonical_dispatch_action():
    with patch.object(
        routes_gateway.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        return_value={
            "ok": True,
            "decision": "allow",
            "reason": "gateway_service_operation_allowed",
            "operation": "tool_execute",
            "next_action": "dispatch_gateway_operation",
        },
    ):
        decision = routes_gateway._enforce_gateway_service_decision(
            operation="tool_execute",
            gateway_id="gw-1",
            workspace_id="ws-1",
            tenant_id="tenant-1",
            actor_id="owner-1",
            quota_profile=routes_gateway.GATEWAY_TOOL_EXECUTION,
            capability_id="computer_control.click",
            run_id="run-1",
            trace_id="trace-1",
            request_id="req-1",
            approval_provided=True,
            approval_memory_hit=True,
            risk_decision="normal",
        )

    assert decision["next_action"] == "dispatch_gateway_operation"


def test_gateway_service_unexpected_next_action_blocks():
    with patch.object(
        routes_gateway.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        return_value={
            "ok": True,
            "decision": "allow",
            "reason": "gateway_service_operation_allowed",
            "operation": "tool_execute",
            "next_action": "allow_gateway_service_operation",
        },
    ):
        with pytest.raises(HTTPException) as raised:
            routes_gateway._enforce_gateway_service_decision(
                operation="tool_execute",
                gateway_id="gw-1",
                workspace_id="ws-1",
                tenant_id="tenant-1",
                actor_id="owner-1",
                quota_profile=routes_gateway.GATEWAY_TOOL_EXECUTION,
                capability_id="computer_control.click",
                run_id="run-1",
                trace_id="trace-1",
                request_id="req-1",
                approval_provided=True,
                approval_memory_hit=True,
                risk_decision="normal",
            )

    assert raised.value.status_code == 423
    assert "unexpected next_action" in str(raised.value.detail)


def test_gateway_service_health_check_accepts_publish_gateway_health():
    with patch.object(
        routes_gateway.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        return_value={
            "ok": True,
            "decision": "allow",
            "reason": "gateway_health_allowed",
            "operation": "health_check",
            "next_action": "publish_gateway_health",
        },
    ):
        decision = routes_gateway._enforce_gateway_service_decision(
            operation="health_check",
            gateway_id="gw-1",
            workspace_id="ws-1",
            tenant_id="tenant-1",
            actor_id="owner-1",
            quota_profile=routes_gateway.GATEWAY_WS_CONNECTION,
            capability_id="gateway.health",
            run_id="gateway-health",
            trace_id="gateway-health:gw-1",
            request_id="gateway-health:gw-1",
            approval_provided=True,
            approval_memory_hit=False,
            risk_decision="normal",
        )

    assert decision["next_action"] == "publish_gateway_health"


def test_gateway_service_tool_interrupt_accepts_dispatch_gateway_operation():
    with patch.object(
        routes_gateway.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        return_value={
            "ok": True,
            "decision": "allow",
            "reason": "gateway_service_operation_allowed",
            "operation": "tool_interrupt",
            "next_action": "dispatch_gateway_operation",
        },
    ):
        decision = routes_gateway._enforce_gateway_service_decision(
            operation="tool_interrupt",
            gateway_id="gw-1",
            workspace_id="ws-1",
            tenant_id="tenant-1",
            actor_id="owner-1",
            quota_profile=routes_gateway.GATEWAY_TOOL_EXECUTION,
            capability_id="tool.interrupt",
            run_id="run-1",
            trace_id="trace-1",
            request_id="req-1",
            approval_provided=True,
            approval_memory_hit=False,
            risk_decision="normal",
        )

    assert decision["next_action"] == "dispatch_gateway_operation"


def test_gateway_service_browser_action_accepts_dispatch_gateway_operation():
    with patch.object(
        routes_gateway.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        return_value={
            "ok": True,
            "decision": "allow",
            "reason": "gateway_service_operation_allowed",
            "operation": "browser_action",
            "next_action": "dispatch_gateway_operation",
        },
    ):
        decision = routes_gateway._enforce_gateway_service_decision(
            operation="browser_action",
            gateway_id="gw-1",
            workspace_id="ws-1",
            tenant_id="tenant-1",
            actor_id="owner-1",
            quota_profile=routes_gateway.GATEWAY_TOOL_EXECUTION,
            capability_id=routes_gateway.gateway_browser_service.BROWSER_SESSION_ACTION_CAPABILITY,
            run_id="run-1",
            trace_id="trace-1",
            request_id="req-1",
            browser_session_id="browser-1",
            approval_provided=True,
            approval_memory_hit=False,
            risk_decision="normal",
        )

    assert decision["next_action"] == "dispatch_gateway_operation"


def test_gateway_service_browser_session_accepts_dispatch_gateway_operation():
    with patch.object(
        routes_gateway.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        return_value={
            "ok": True,
            "decision": "allow",
            "reason": "gateway_browser_session_allowed",
            "operation": "browser_session",
            "next_action": "dispatch_gateway_operation",
        },
    ):
        decision = routes_gateway._enforce_gateway_service_decision(
            operation="browser_session",
            gateway_id="gw-1",
            workspace_id="ws-1",
            tenant_id="tenant-1",
            actor_id="owner-1",
            quota_profile=routes_gateway.GATEWAY_BROWSER_SESSION,
            capability_id=routes_gateway.gateway_browser_service.BROWSER_SESSION_RESUME_CAPABILITY,
            run_id="run-1",
            trace_id="trace-1",
            request_id="req-1",
            approval_provided=True,
            approval_memory_hit=False,
            risk_decision="normal",
        )

    assert decision["next_action"] == "dispatch_gateway_operation"


def test_gateway_service_approval_request_accepts_request_gateway_owner_approval():
    with patch.object(
        routes_gateway.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        return_value={
            "ok": True,
            "decision": "requires_approval",
            "reason": "gateway_approval_required",
            "operation": "approval_request",
            "next_action": "request_gateway_owner_approval",
        },
    ):
        decision = routes_gateway._enforce_gateway_service_decision(
            operation="approval_request",
            gateway_id="gw-1",
            workspace_id="ws-1",
            tenant_id="tenant-1",
            actor_id="owner-1",
            quota_profile=routes_gateway.GATEWAY_APPROVAL_ACTION,
            capability_id="computer_control.click",
            run_id="run-1",
            trace_id="trace-1",
            request_id="req-1",
            approval_provided=False,
            approval_memory_hit=False,
            risk_decision="requires_approval",
        )

    assert decision["next_action"] == "request_gateway_owner_approval"


def test_gateway_service_approval_resolve_accepts_persist_approval_decision():
    with patch.object(
        routes_gateway.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        return_value={
            "ok": True,
            "decision": "allow",
            "reason": "gateway_approval_resolution_allowed",
            "operation": "approval_resolve",
            "next_action": "persist_approval_decision",
        },
    ):
        decision = routes_gateway._enforce_gateway_service_decision(
            operation="approval_resolve",
            gateway_id="gw-1",
            workspace_id="ws-1",
            tenant_id="tenant-1",
            actor_id="owner-1",
            quota_profile=routes_gateway.GATEWAY_APPROVAL_ACTION,
            capability_id="computer_control.click",
            run_id="run-1",
            trace_id="trace-1",
            request_id="approval-1",
            approval_provided=True,
            approval_memory_hit=False,
            risk_decision="normal",
        )

    assert decision["next_action"] == "persist_approval_decision"


def test_gateway_service_cloud_fallback_accepts_dispatch_gateway_operation():
    with patch.object(
        routes_gateway.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        return_value={
            "ok": True,
            "decision": "allow",
            "reason": "gateway_cloud_fallback_allowed",
            "operation": "cloud_fallback",
            "next_action": "dispatch_gateway_operation",
        },
    ):
        decision = routes_gateway._enforce_gateway_service_decision(
            operation="cloud_fallback",
            gateway_id="gw-1",
            workspace_id="ws-1",
            tenant_id="tenant-1",
            actor_id="owner-1",
            quota_profile=routes_gateway.GATEWAY_BROWSER_SESSION,
            capability_id=routes_gateway.gateway_browser_service.BROWSER_SESSION_START_CAPABILITY,
            run_id="run-1",
            trace_id="trace-1",
            request_id="req-1",
            approval_provided=True,
            approval_memory_hit=False,
            risk_decision="normal",
            cloud_fallback_enabled=True,
            cloud_fallback_approved=True,
        )

    assert decision["next_action"] == "dispatch_gateway_operation"


def test_gateway_service_policy_read_accepts_allow_gateway_service_operation():
    with patch.object(
        routes_gateway.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        return_value={
            "ok": True,
            "decision": "allow",
            "reason": "gateway_service_operation_allowed",
            "operation": "gateway_policy_read",
            "next_action": "allow_gateway_service_operation",
        },
    ):
        decision = routes_gateway._enforce_gateway_service_decision(
            operation="gateway_policy_read",
            gateway_id="gw-1",
            workspace_id="ws-1",
            tenant_id="tenant-1",
            actor_id="owner-1",
            quota_profile=routes_gateway.GATEWAY_WS_CONNECTION,
            capability_id="agent_computer.policy.read",
            run_id="gateway-policy-read",
            trace_id="gateway-policy-read:gw-1",
            request_id="gateway-policy-read:gw-1",
            approval_provided=True,
            approval_memory_hit=False,
            risk_decision="normal",
        )

    assert decision["next_action"] == "allow_gateway_service_operation"


def test_gateway_service_policy_write_accepts_allow_gateway_service_operation():
    with patch.object(
        routes_gateway.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        return_value={
            "ok": True,
            "decision": "allow",
            "reason": "gateway_service_operation_allowed",
            "operation": "gateway_policy_write",
            "next_action": "allow_gateway_service_operation",
        },
    ):
        decision = routes_gateway._enforce_gateway_service_decision(
            operation="gateway_policy_write",
            gateway_id="gw-1",
            workspace_id="ws-1",
            tenant_id="tenant-1",
            actor_id="owner-1",
            quota_profile=routes_gateway.GATEWAY_WS_CONNECTION,
            capability_id="agent_computer.policy.write",
            run_id="gateway-policy-write",
            trace_id="gateway-policy-write:gw-1",
            request_id="gateway-policy-write:gw-1",
            approval_provided=True,
            approval_memory_hit=False,
            risk_decision="normal",
        )

    assert decision["next_action"] == "allow_gateway_service_operation"


def test_gateway_acp_turn_accepts_route_acp_turn_action():
    with (
        patch.object(
            routes_gateway.rust_runtime_kernel_client,
            "gateway_action_decision",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "gateway_acp_turn_allowed",
                "operation": "acp_turn",
                "next_action": "route_acp_turn",
            },
        ),
        patch.object(
            routes_gateway.rust_runtime_kernel_client,
            "enforce_kernel_decision",
            side_effect=lambda _command, decision: decision,
        ),
    ):
        decision = routes_gateway._enforce_gateway_acp_turn_decision(
            workspace_id="ws-1",
            request_id="acp-1",
            message="hello",
        )

    assert decision["next_action"] == "route_acp_turn"


def test_acp_turn_endpoint_wrong_rust_action_blocks_before_handle_sage_chat():
    class _FakeRequest:
        async def json(self):
            return {
                "id": "acp-1",
                "type": "agent.turn",
                "payload": {"message": "hello", "workspace_id": "ws-1"},
            }

    async def run_test():
        with (
            patch.object(routes_gateway, "enforce_workspace_access", return_value="ws-1"),
            patch.object(routes_gateway, "workspace_tenant_id", return_value="tenant-1"),
            patch.object(
                routes_gateway.rust_runtime_kernel_client,
                "gateway_action_decision",
                return_value={
                    "ok": True,
                    "decision": "allow",
                    "reason": "gateway_acp_turn_allowed",
                    "operation": "acp_turn",
                    "next_action": "dispatch_tool_invoke",
                },
            ),
            patch.object(
                routes_gateway.rust_runtime_kernel_client,
                "enforce_kernel_decision",
                side_effect=lambda _command, decision: decision,
            ),
            patch(
                "server_modules.sage_agent_runtime_service.handle_sage_chat",
                new=AsyncMock(side_effect=AssertionError("should not execute Sage turn")),
            ) as handle_mock,
        ):
            response = await routes_gateway.acp_turn_endpoint(
                request=_FakeRequest(),
                workspace_id="ws-1",
                current_user={"user_id": "customer-1"},
            )

        assert response.status_code == 500
        assert b"unexpected next_action" in response.body
        handle_mock.assert_not_awaited()

    asyncio.run(run_test())


def test_acp_turn_route_requires_api_key_dependency():
    assert "require_api_key" in _route_dependencies("/gateway/acp/turn")


def test_acp_turn_endpoint_enforces_workspace_access_before_handle_sage_chat():
    class _FakeRequest:
        async def json(self):
            return {
                "id": "acp-1",
                "type": "agent.turn",
                "payload": {"message": "hello", "workspace_id": "ws-denied"},
            }

    async def run_test():
        with (
            patch.object(
                routes_gateway,
                "enforce_workspace_access",
                side_effect=HTTPException(status_code=403, detail="Workspace is not accessible for this user."),
            ) as access_mock,
            patch(
                "server_modules.sage_agent_runtime_service.handle_sage_chat",
                new=AsyncMock(side_effect=AssertionError("should not execute Sage turn")),
            ) as handle_mock,
        ):
            with pytest.raises(HTTPException) as raised:
                await routes_gateway.acp_turn_endpoint(
                    request=_FakeRequest(),
                    workspace_id="ws-denied",
                    current_user={"user_id": "customer-1"},
                )

        assert raised.value.status_code == 403
        access_mock.assert_called_once_with(
            {"user_id": "customer-1"},
            "ws-denied",
            minimum_role="member",
        )
        handle_mock.assert_not_awaited()

    asyncio.run(run_test())


def test_acp_turn_endpoint_rejects_payload_workspace_mismatch_before_handle_sage_chat():
    class _FakeRequest:
        async def json(self):
            return {
                "id": "acp-1",
                "type": "agent.turn",
                "payload": {"message": "hello", "workspace_id": "ws-other"},
            }

    async def run_test():
        with (
            patch.object(routes_gateway, "enforce_workspace_access", return_value="ws-1") as access_mock,
            patch.object(routes_gateway, "workspace_tenant_id", return_value="tenant-1"),
            patch(
                "server_modules.sage_agent_runtime_service.handle_sage_chat",
                new=AsyncMock(side_effect=AssertionError("should not execute Sage turn")),
            ) as handle_mock,
        ):
            response = await routes_gateway.acp_turn_endpoint(
                request=_FakeRequest(),
                workspace_id="ws-1",
                current_user={"user_id": "customer-1"},
            )

        assert response.status_code == 403
        assert b"workspace_mismatch" in response.body
        access_mock.assert_called_once()
        handle_mock.assert_not_awaited()

    asyncio.run(run_test())


def test_gateway_diagnostics_export_accepts_export_bundle_action():
    with (
        patch.object(
            routes_gateway.rust_runtime_kernel_client,
            "gateway_action_decision",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "gateway_diagnostics_export_allowed",
                "operation": "diagnostics_export",
                "next_action": "export_diagnostics_bundle",
            },
        ),
        patch.object(
            routes_gateway.rust_runtime_kernel_client,
            "enforce_kernel_decision",
            side_effect=lambda _command, decision: decision,
        ),
    ):
        decision = routes_gateway._enforce_gateway_diagnostics_export_decision(
            workspace_id="ws-1",
            tenant_id="tenant-1",
            actor_role="viewer",
        )

    assert decision["next_action"] == "export_diagnostics_bundle"


def test_workspace_diagnostics_endpoint_wrong_rust_action_blocks_before_bundle_export():
    async def run_test():
        current_user = {"role": "viewer"}
        with (
            patch.object(routes_gateway, "enforce_workspace_access", return_value="ws-1"),
            patch.object(routes_gateway, "workspace_tenant_id", return_value="tenant-1"),
            patch.object(
                routes_gateway.rust_runtime_kernel_client,
                "gateway_action_decision",
                return_value={
                    "ok": True,
                    "decision": "allow",
                    "reason": "gateway_diagnostics_export_allowed",
                    "operation": "diagnostics_export",
                    "next_action": "return_redacted_diagnostics",
                },
            ),
            patch.object(
                routes_gateway.rust_runtime_kernel_client,
                "enforce_kernel_decision",
                side_effect=lambda _command, decision: decision,
            ),
            patch(
                "server_modules.session_diagnostics_service.export_diagnostics_bundle",
                new=AsyncMock(side_effect=AssertionError("should not export bundle")),
            ) as export_mock,
        ):
            with pytest.raises(HTTPException) as raised:
                await routes_gateway.export_workspace_diagnostics_endpoint(
                    workspace_id="ws-1",
                    current_user=current_user,
                )

        assert raised.value.status_code == 423
        assert "unexpected next_action" in str(raised.value.detail)
        export_mock.assert_not_awaited()

    asyncio.run(run_test())


def test_gateway_doctor_route_wrong_rust_action_blocks_before_payload_build():
    async def run_test():
        current_user = {
            "user_id": "owner-1",
            "role": "viewer",
            "workspace_access": {"ws-1": {"tenant_id": "tenant-1"}},
        }
        registration = {"gateway_id": "gw-1", "workspace_id": "ws-1"}
        with (
            patch.object(routes_gateway.gateway_state_repository, "get_gateway_registration", return_value=registration),
            patch.object(routes_gateway, "enforce_workspace_access", return_value="ws-1"),
            patch.object(routes_gateway, "workspace_tenant_id", return_value="tenant-1"),
            patch.object(
                routes_gateway.rust_runtime_kernel_client,
                "run_runtime_kernel_enforced",
                return_value={
                    "ok": True,
                    "decision": "allow",
                    "reason": "gateway_health_allowed",
                    "operation": "health_check",
                    "next_action": "dispatch_gateway_operation",
                },
            ),
            patch.object(
                routes_gateway.gateway_health_service,
                "gateway_doctor_payload",
                side_effect=AssertionError("should not build doctor payload"),
            ) as doctor_mock,
        ):
            with pytest.raises(HTTPException) as raised:
                await routes_gateway.get_gateway_registration_doctor(
                    gateway_id="gw-1",
                    force_provider_probe=False,
                    current_user=current_user,
                )

        assert raised.value.status_code == 423
        assert "unexpected next_action" in str(raised.value.detail)
        doctor_mock.assert_not_called()

    asyncio.run(run_test())


def test_interrupt_gateway_tool_route_wrong_rust_action_blocks_before_service_call():
    class _Body:
        workspace_id = "ws-1"
        run_id = "run-1"
        trace_id = "trace-1"
        request_id = "req-1"
        target_request_id = "target-1"
        reason = "operator_requested_stop"
        timeout_seconds = None

    async def run_test():
        current_user = {"user_id": "owner-1"}
        registration = {"gateway_id": "gw-1", "workspace_id": "ws-1"}
        with (
            patch.object(routes_gateway.gateway_state_repository, "get_gateway_registration", return_value=registration),
            patch.object(routes_gateway, "enforce_workspace_access", return_value="ws-1"),
            patch.object(routes_gateway, "workspace_tenant_id", return_value="tenant-1"),
            patch.object(
                routes_gateway.rust_runtime_kernel_client,
                "run_runtime_kernel_enforced",
                return_value={
                    "ok": True,
                    "decision": "allow",
                    "reason": "gateway_service_operation_allowed",
                    "operation": "tool_interrupt",
                    "next_action": "persist_approval_decision",
                },
            ),
            patch.object(
                routes_gateway.gateway_execution_service,
                "interrupt_tool_via_gateway",
                new=AsyncMock(side_effect=AssertionError("should not interrupt tool")),
            ) as interrupt_mock,
        ):
            with pytest.raises(HTTPException) as raised:
                await routes_gateway.interrupt_gateway_tool(
                    gateway_id="gw-1",
                    body=_Body(),
                    current_user=current_user,
                )

        assert raised.value.status_code == 423
        assert "unexpected next_action" in str(raised.value.detail)
        interrupt_mock.assert_not_awaited()

    asyncio.run(run_test())


def test_get_agent_computer_policy_wrong_rust_action_blocks_before_response_build():
    async def run_test():
        current_user = {"user_id": "owner-1"}
        with (
            patch.object(
                routes_gateway,
                "_accessible_agent_computer",
                return_value=("ws-1", {"id": "profile-1"}, {"gateway_id": "gw-1"}, "policy-1"),
            ),
            patch.object(routes_gateway, "workspace_tenant_id", return_value="tenant-1"),
            patch.object(
                routes_gateway.rust_runtime_kernel_client,
                "run_runtime_kernel_enforced",
                return_value={
                    "ok": True,
                    "decision": "allow",
                    "reason": "gateway_service_operation_allowed",
                    "operation": "gateway_policy_read",
                    "next_action": "dispatch_gateway_operation",
                },
            ),
            patch.object(
                routes_gateway,
                "_agent_computer_policy_response",
                side_effect=AssertionError("should not build policy response"),
            ) as response_mock,
        ):
            with pytest.raises(HTTPException) as raised:
                await routes_gateway.get_agent_computer_policy(
                    computer_id="gw-1",
                    workspace_id="ws-1",
                    current_user=current_user,
                )

        assert raised.value.status_code == 423
        assert "unexpected next_action" in str(raised.value.detail)
        response_mock.assert_not_called()

    asyncio.run(run_test())


def test_update_agent_computer_policy_wrong_rust_action_blocks_before_upsert():
    class _Body:
        workspace_id = "ws-1"
        policy = {"autonomy_mode": "trusted_workstation"}

    async def run_test():
        current_user = {"user_id": "owner-1"}
        with (
            patch.object(routes_gateway, "validate_csrf", return_value=None),
            patch.object(
                routes_gateway,
                "_accessible_agent_computer",
                return_value=("ws-1", {"id": "profile-1"}, {"gateway_id": "gw-1"}, "policy-1"),
            ),
            patch.object(routes_gateway, "workspace_tenant_id", return_value="tenant-1"),
            patch.object(
                routes_gateway.rust_runtime_kernel_client,
                "run_runtime_kernel_enforced",
                return_value={
                    "ok": True,
                    "decision": "allow",
                    "reason": "gateway_service_operation_allowed",
                    "operation": "gateway_policy_write",
                    "next_action": "publish_gateway_health",
                },
            ),
            patch.object(
                routes_gateway,
                "upsert_agent_computer_policy",
                side_effect=AssertionError("should not upsert policy"),
            ) as upsert_mock,
        ):
            with pytest.raises(HTTPException) as raised:
                await routes_gateway.update_agent_computer_policy(
                    computer_id="gw-1",
                    body=_Body(),
                    request=object(),
                    current_user=current_user,
                )

        assert raised.value.status_code == 423
        assert "unexpected next_action" in str(raised.value.detail)
        upsert_mock.assert_not_called()

    asyncio.run(run_test())


def test_validate_agent_computer_policy_wrong_rust_action_blocks_before_validation():
    class _Body:
        workspace_id = "ws-1"
        policy = {"autonomy_mode": "trusted_workstation"}

    async def run_test():
        current_user = {"user_id": "owner-1"}
        with (
            patch.object(routes_gateway, "validate_csrf", return_value=None),
            patch.object(
                routes_gateway,
                "_accessible_agent_computer",
                return_value=("ws-1", {"id": "profile-1"}, {"gateway_id": "gw-1"}, "policy-1"),
            ),
            patch.object(routes_gateway, "workspace_tenant_id", return_value="tenant-1"),
            patch.object(
                routes_gateway.rust_runtime_kernel_client,
                "run_runtime_kernel_enforced",
                return_value={
                    "ok": True,
                    "decision": "allow",
                    "reason": "gateway_service_operation_allowed",
                    "operation": "gateway_policy_write",
                    "next_action": "dispatch_gateway_operation",
                },
            ),
            patch.object(
                routes_gateway,
                "validate_agent_computer_policy_contract",
                side_effect=AssertionError("should not validate policy"),
            ) as validate_mock,
        ):
            with pytest.raises(HTTPException) as raised:
                await routes_gateway.validate_agent_computer_policy_route(
                    computer_id="gw-1",
                    body=_Body(),
                    request=object(),
                    current_user=current_user,
                )

        assert raised.value.status_code == 423
        assert "unexpected next_action" in str(raised.value.detail)
        validate_mock.assert_not_called()

    asyncio.run(run_test())


def test_execute_gateway_browser_action_wrong_rust_action_blocks_before_dispatch():
    class _Body:
        workspace_id = "ws-1"
        run_id = "run-1"
        request_id = "req-1"
        trace_id = "trace-1"
        action = "click"
        action_args = {"x": 12, "y": 34}
        reviewed_approval_required = None
        allow_cloud_fallback = False
        timeout_seconds = 30

    async def run_test():
        current_user = {"user_id": "member-1"}
        registration = {"gateway_id": "gw-1"}
        browser_session = {
            "gateway_id": "gw-1",
            "session_profile": "default",
            "metadata": {},
            "checkpoint": {},
            "reviewed_approval_required": False,
            "reviewed_approved": False,
        }
        risk_decision = type("RiskDecision", (), {"decision": routes_gateway.DECISION_ALLOW})()
        with (
            patch.object(
                routes_gateway,
                "_accessible_gateway_registration",
                return_value=(registration, "ws-1"),
            ),
            patch.object(routes_gateway, "_enforce_gateway_safety_gates", return_value=None),
            patch.object(
                routes_gateway.gateway_state_repository,
                "get_gateway_browser_session",
                return_value=browser_session,
            ),
            patch.object(routes_gateway, "_latest_gateway_session_id", return_value="session-1"),
            patch.object(
                routes_gateway.gateway_browser_service,
                "normalize_browser_session_mode",
                return_value="live",
            ),
            patch.object(
                routes_gateway.gateway_browser_service,
                "browser_session_requires_reviewed_approval",
                return_value=False,
            ),
            patch.object(
                routes_gateway.gateway_browser_service,
                "build_gateway_browser_metadata",
                return_value={},
            ),
            patch.object(routes_gateway, "workspace_tenant_id", return_value="tenant-1"),
            patch.object(routes_gateway, "_gateway_policy_from_registration", return_value=object()),
            patch.object(
                routes_gateway,
                "classify_gateway_browser_action_risk",
                return_value=risk_decision,
            ),
            patch.object(routes_gateway, "_emit_gateway_risk_decision", return_value=None),
            patch.object(
                routes_gateway.gateway_browser_service,
                "browser_action_requires_owner_approval",
                return_value=False,
            ),
            patch.object(
                routes_gateway.rust_runtime_kernel_client,
                "run_runtime_kernel_enforced",
                return_value={
                    "ok": True,
                    "decision": "allow",
                    "reason": "gateway_service_operation_allowed",
                    "operation": "browser_action",
                    "next_action": "allow_gateway_service_operation",
                },
            ),
            patch.object(
                routes_gateway.gateway_browser_service,
                "execute_browser_capability_via_gateway",
                new=AsyncMock(side_effect=AssertionError("should not dispatch browser action")),
            ) as execute_mock,
        ):
            with pytest.raises(HTTPException) as raised:
                await routes_gateway.execute_gateway_browser_action(
                    gateway_id="gw-1",
                    browser_session_id="browser-1",
                    body=_Body(),
                    current_user=current_user,
                )

        assert raised.value.status_code == 423
        assert "unexpected next_action" in str(raised.value.detail)
        execute_mock.assert_not_awaited()

    asyncio.run(run_test())


def test_gateway_approval_required_response_wrong_rust_action_blocks_before_approval_creation():
    class _RiskDecision:
        decision = "requires_approval"

        def as_dict(self):
            return {"decision": self.decision}

    async def run_test():
        registration = {"gateway_id": "gw-1", "workspace_id": "ws-1"}
        with (
            patch.object(routes_gateway, "_latest_gateway_session_id", return_value="session-1"),
            patch.object(
                routes_gateway.rust_runtime_kernel_client,
                "run_runtime_kernel_enforced",
                return_value={
                    "ok": True,
                    "decision": "requires_approval",
                    "reason": "gateway_approval_required",
                    "operation": "approval_request",
                    "next_action": "allow_gateway_service_operation",
                },
            ),
            patch.object(
                routes_gateway.gateway_approval_service,
                "request_gateway_tool_approval",
                new=AsyncMock(side_effect=AssertionError("should not create approval")),
            ) as approval_mock,
        ):
            with pytest.raises(HTTPException) as raised:
                await routes_gateway._gateway_approval_required_response(
                    registration=registration,
                    gateway_id="gw-1",
                    tenant_id="tenant-1",
                    actor_id="owner-1",
                    capability_id="computer_control.click",
                    arguments={"x": 1, "y": 2},
                    run_id="run-1",
                    trace_id="trace-1",
                    request_id="req-1",
                    risk_decision=_RiskDecision(),
                )

        assert raised.value.status_code == 423
        assert "unexpected next_action" in str(raised.value.detail)
        approval_mock.assert_not_awaited()

    asyncio.run(run_test())


def test_resolve_gateway_registration_approval_wrong_rust_action_blocks_before_resolution():
    class _Body:
        decision = "approved"
        note = "looks safe"
        timeout_seconds = 30
        remember_for_seconds = None

    async def run_test():
        current_user = {"user_id": "owner-1"}
        registration = {"gateway_id": "gw-1", "tenant_id": "tenant-1"}
        approval = {
            "approval_id": "approval-1",
            "capability_id": "computer_control.click",
            "run_id": "run-1",
            "trace_id": "trace-1",
            "request_id": "req-1",
        }
        with (
            patch.object(
                routes_gateway,
                "_accessible_gateway_registration",
                return_value=(registration, "ws-1"),
            ),
            patch.object(routes_gateway, "_enforce_gateway_safety_gates", return_value=None),
            patch.object(
                routes_gateway.gateway_state_repository,
                "get_gateway_action_approval",
                return_value=approval,
            ),
            patch.object(routes_gateway, "_latest_gateway_session_id", return_value="session-1"),
            patch.object(
                routes_gateway.rust_runtime_kernel_client,
                "run_runtime_kernel_enforced",
                return_value={
                    "ok": True,
                    "decision": "allow",
                    "reason": "gateway_approval_resolution_allowed",
                    "operation": "approval_resolve",
                    "next_action": "allow_gateway_service_operation",
                },
            ),
            patch.object(
                routes_gateway.gateway_approval_service,
                "resolve_gateway_tool_approval",
                new=AsyncMock(side_effect=AssertionError("should not resolve approval")),
            ) as resolve_mock,
        ):
            with pytest.raises(HTTPException) as raised:
                await routes_gateway.resolve_gateway_registration_approval(
                    gateway_id="gw-1",
                    approval_id="approval-1",
                    body=_Body(),
                    current_user=current_user,
                )

        assert raised.value.status_code == 423
        assert "unexpected next_action" in str(raised.value.detail)
        resolve_mock.assert_not_awaited()

    asyncio.run(run_test())


def test_start_gateway_browser_session_cloud_fallback_wrong_rust_action_blocks_before_builder():
    class _Body:
        workspace_id = "ws-1"
        run_id = "run-1"
        request_id = "req-1"
        trace_id = "trace-1"
        url = "https://example.com"
        session_profile = "default"
        session_mode = "live"
        attach_endpoint_url = None
        interactive_actions = []
        reviewed_approval_required = False
        allow_cloud_fallback = True
        timeout_seconds = 30

    async def run_test():
        current_user = {"user_id": "member-1"}
        registration = {"gateway_id": "gw-1"}
        gateway_policy = object()
        risk_decision = type("RiskDecision", (), {"decision": routes_gateway.DECISION_ALLOW})()
        with (
            patch.object(
                routes_gateway,
                "_accessible_gateway_registration",
                return_value=(registration, "ws-1"),
            ),
            patch.object(routes_gateway, "_enforce_gateway_safety_gates", return_value=None),
            patch.object(
                routes_gateway.gateway_browser_service,
                "normalize_browser_session_mode",
                return_value="live",
            ),
            patch.object(
                routes_gateway.gateway_browser_service,
                "browser_session_requires_reviewed_approval",
                return_value=False,
            ),
            patch.object(
                routes_gateway.gateway_browser_service,
                "build_gateway_browser_metadata",
                return_value={},
            ),
            patch.object(routes_gateway, "workspace_tenant_id", return_value="tenant-1"),
            patch.object(routes_gateway, "_gateway_policy_from_registration", return_value=gateway_policy),
            patch.object(
                routes_gateway,
                "classify_gateway_browser_action_risk",
                return_value=risk_decision,
            ),
            patch.object(routes_gateway, "_emit_gateway_risk_decision", return_value=None),
            patch.object(
                routes_gateway.gateway_browser_service,
                "browser_action_requires_owner_approval",
                return_value=False,
            ),
            patch.object(
                routes_gateway.gateway_protocol_service,
                "gateway_connection_is_live",
                return_value=False,
            ),
            patch.object(routes_gateway, "_latest_gateway_session_id", return_value="session-1"),
            patch.object(
                routes_gateway.rust_runtime_kernel_client,
                "run_runtime_kernel_enforced",
                return_value={
                    "ok": True,
                    "decision": "allow",
                    "reason": "gateway_cloud_fallback_allowed",
                    "operation": "cloud_fallback",
                    "next_action": "allow_gateway_service_operation",
                },
            ),
            patch.object(
                routes_gateway.gateway_browser_service,
                "build_cloud_browser_fallback_response",
                new=AsyncMock(side_effect=AssertionError("should not build fallback")),
            ) as fallback_mock,
        ):
            with pytest.raises(HTTPException) as raised:
                await routes_gateway.start_gateway_browser_session(
                    gateway_id="gw-1",
                    body=_Body(),
                    current_user=current_user,
                )

        assert raised.value.status_code == 423
        assert "unexpected next_action" in str(raised.value.detail)
        fallback_mock.assert_not_awaited()

    asyncio.run(run_test())


def test_resume_gateway_browser_session_cloud_fallback_wrong_rust_action_blocks_before_builder():
    class _Body:
        workspace_id = "ws-1"
        run_id = "run-1"
        request_id = "req-1"
        trace_id = "trace-1"
        note = None
        timeout_seconds = 30

    async def run_test():
        current_user = {"user_id": "member-1"}
        registration = {"gateway_id": "gw-1"}
        browser_session = {
            "gateway_id": "gw-1",
            "session_profile": "default",
            "metadata": {},
            "checkpoint": {},
        }
        with (
            patch.object(
                routes_gateway,
                "_accessible_gateway_registration",
                return_value=(registration, "ws-1"),
            ),
            patch.object(routes_gateway, "_enforce_gateway_safety_gates", return_value=None),
            patch.object(
                routes_gateway.gateway_state_repository,
                "get_gateway_browser_session",
                return_value=browser_session,
            ),
            patch.object(
                routes_gateway.gateway_browser_service,
                "normalize_browser_session_mode",
                return_value="live",
            ),
            patch.object(
                routes_gateway.gateway_protocol_service,
                "gateway_connection_is_live",
                return_value=False,
            ),
            patch.object(routes_gateway, "workspace_tenant_id", return_value="tenant-1"),
            patch.object(routes_gateway, "_latest_gateway_session_id", return_value="session-1"),
            patch.object(
                routes_gateway.rust_runtime_kernel_client,
                "run_runtime_kernel_enforced",
                return_value={
                    "ok": True,
                    "decision": "allow",
                    "reason": "gateway_cloud_fallback_allowed",
                    "operation": "cloud_fallback",
                    "next_action": "allow_gateway_service_operation",
                },
            ),
            patch.object(
                routes_gateway.gateway_browser_service,
                "build_cloud_browser_fallback_response",
                new=AsyncMock(side_effect=AssertionError("should not build fallback")),
            ) as fallback_mock,
        ):
            with pytest.raises(HTTPException) as raised:
                await routes_gateway.resume_gateway_browser_session(
                    gateway_id="gw-1",
                    browser_session_id="browser-1",
                    body=_Body(),
                    current_user=current_user,
                )

        assert raised.value.status_code == 423
        assert "unexpected next_action" in str(raised.value.detail)
        fallback_mock.assert_not_awaited()

    asyncio.run(run_test())


def test_resume_gateway_browser_session_wrong_rust_action_blocks_before_dispatch():
    class _Body:
        workspace_id = "ws-1"
        run_id = "run-1"
        request_id = "req-1"
        trace_id = "trace-1"
        note = None
        timeout_seconds = 30

    async def run_test():
        current_user = {"user_id": "member-1"}
        registration = {"gateway_id": "gw-1"}
        browser_session = {
            "gateway_id": "gw-1",
            "session_profile": "default",
            "metadata": {},
            "checkpoint": {},
        }
        with (
            patch.object(
                routes_gateway,
                "_accessible_gateway_registration",
                return_value=(registration, "ws-1"),
            ),
            patch.object(routes_gateway, "_enforce_gateway_safety_gates", return_value=None),
            patch.object(
                routes_gateway.gateway_state_repository,
                "get_gateway_browser_session",
                return_value=browser_session,
            ),
            patch.object(
                routes_gateway.gateway_browser_service,
                "normalize_browser_session_mode",
                return_value="live",
            ),
            patch.object(
                routes_gateway.gateway_protocol_service,
                "gateway_connection_is_live",
                return_value=True,
            ),
            patch.object(routes_gateway, "workspace_tenant_id", return_value="tenant-1"),
            patch.object(routes_gateway, "_latest_gateway_session_id", return_value="session-1"),
            patch.object(
                routes_gateway.rust_runtime_kernel_client,
                "run_runtime_kernel_enforced",
                return_value={
                    "ok": True,
                    "decision": "allow",
                    "reason": "gateway_browser_session_allowed",
                    "operation": "browser_session",
                    "next_action": "allow_gateway_service_operation",
                },
            ),
            patch.object(
                routes_gateway.gateway_browser_service,
                "execute_browser_capability_via_gateway",
                new=AsyncMock(side_effect=AssertionError("should not dispatch resume")),
            ) as execute_mock,
        ):
            with pytest.raises(HTTPException) as raised:
                await routes_gateway.resume_gateway_browser_session(
                    gateway_id="gw-1",
                    browser_session_id="browser-1",
                    body=_Body(),
                    current_user=current_user,
                )

        assert raised.value.status_code == 423
        assert "unexpected next_action" in str(raised.value.detail)
        execute_mock.assert_not_awaited()

    asyncio.run(run_test())


def test_takeover_gateway_browser_session_wrong_rust_action_blocks_before_dispatch():
    class _Body:
        workspace_id = "ws-1"
        run_id = "run-1"
        request_id = "req-1"
        trace_id = "trace-1"
        note = "manual"
        timeout_seconds = 30

    async def run_test():
        current_user = {"user_id": "member-1"}
        browser_session = {"gateway_id": "gw-1"}
        registration = {"gateway_id": "gw-1"}
        with (
            patch.object(
                routes_gateway.gateway_state_repository,
                "get_gateway_browser_session",
                return_value=browser_session,
            ),
            patch.object(
                routes_gateway,
                "_accessible_gateway_registration",
                return_value=(registration, "ws-1"),
            ),
            patch.object(routes_gateway, "_enforce_gateway_safety_gates", return_value=None),
            patch.object(routes_gateway, "workspace_tenant_id", return_value="tenant-1"),
            patch.object(routes_gateway, "_latest_gateway_session_id", return_value="session-1"),
            patch.object(
                routes_gateway.rust_runtime_kernel_client,
                "run_runtime_kernel_enforced",
                return_value={
                    "ok": True,
                    "decision": "allow",
                    "reason": "gateway_browser_action_allowed",
                    "operation": "browser_action",
                    "next_action": "allow_gateway_service_operation",
                },
            ),
            patch.object(
                routes_gateway.gateway_browser_service,
                "execute_browser_capability_via_gateway",
                new=AsyncMock(side_effect=AssertionError("should not dispatch takeover")),
            ) as execute_mock,
        ):
            with pytest.raises(HTTPException) as raised:
                await routes_gateway.takeover_gateway_browser_session(
                    gateway_id="gw-1",
                    browser_session_id="browser-1",
                    body=_Body(),
                    current_user=current_user,
                )

        assert raised.value.status_code == 423
        assert "unexpected next_action" in str(raised.value.detail)
        execute_mock.assert_not_awaited()

    asyncio.run(run_test())


def test_gateway_approval_memory_consume_accepts_allow_gateway_service_operation():
    class _RiskDecision:
        capability = "computer_control.click"
        decision = "requires_approval"

    with (
        patch.object(
            routes_gateway.agent_approval_memory_service,
            "find_matching_approval_memory_rule",
            return_value=object(),
        ),
        patch.object(
            routes_gateway.agent_approval_memory_service,
            "consume_matching_approval_memory_rule",
            return_value="rule-1",
        ) as consume_mock,
        patch.object(routes_gateway, "_emit_gateway_approval_memory_used", return_value=None),
        patch.object(routes_gateway, "_latest_gateway_session_id", return_value="session-1"),
        patch.object(
            routes_gateway.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "gateway_approval_memory_allowed",
                "operation": "approval_memory_consume",
                "next_action": "allow_gateway_service_operation",
            },
        ) as rust_mock,
    ):
        rule = routes_gateway._consume_gateway_approval_memory(
            registration={"gateway_id": "gw-1"},
            workspace_id="ws-1",
            tenant_id="tenant-1",
            actor_user_id="owner-1",
            policy_id="policy-1",
            risk_decision=_RiskDecision(),
            payload={"x": 1},
            run_id="run-1",
            trace_id="trace-1",
            request_id="req-1",
        )

    assert rule == "rule-1"
    assert rust_mock.call_args.args[0] == "gateway-service-decision"
    assert rust_mock.call_args.args[1]["operation"] == "approval_memory_consume"
    assert rust_mock.call_args.args[1]["approval_provided"] is True
    assert rust_mock.call_args.args[1]["approval_memory_hit"] is True
    consume_mock.assert_called_once()


def test_gateway_approval_memory_consume_wrong_rust_action_blocks_before_consume():
    class _RiskDecision:
        capability = "computer_control.click"
        decision = "requires_approval"

    with (
        patch.object(
            routes_gateway.agent_approval_memory_service,
            "find_matching_approval_memory_rule",
            return_value=object(),
        ),
        patch.object(
            routes_gateway.agent_approval_memory_service,
            "consume_matching_approval_memory_rule",
            side_effect=AssertionError("should not consume approval memory"),
        ) as consume_mock,
        patch.object(
            routes_gateway,
            "_emit_gateway_approval_memory_used",
            side_effect=AssertionError("should not emit approval memory usage"),
        ) as emit_mock,
        patch.object(routes_gateway, "_latest_gateway_session_id", return_value="session-1"),
        patch.object(
            routes_gateway.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "gateway_approval_memory_allowed",
                "operation": "approval_memory_consume",
                "next_action": "dispatch_gateway_operation",
            },
        ),
    ):
        with pytest.raises(HTTPException) as raised:
            routes_gateway._consume_gateway_approval_memory(
                registration={"gateway_id": "gw-1"},
                workspace_id="ws-1",
                tenant_id="tenant-1",
                actor_user_id="owner-1",
                policy_id="policy-1",
                risk_decision=_RiskDecision(),
                payload={"x": 1},
                run_id="run-1",
                trace_id="trace-1",
                request_id="req-1",
            )

    assert raised.value.status_code == 423
    assert "unexpected next_action" in str(raised.value.detail)
    consume_mock.assert_not_called()
    emit_mock.assert_not_called()
