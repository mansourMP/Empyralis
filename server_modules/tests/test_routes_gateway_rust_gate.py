import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from server_modules import routes_gateway
# routes_gateway.py only imports DECISION_BLOCK from
# capability_risk_classifier_service (it never needed DECISION_ALLOW at
# module scope), so `routes_gateway.DECISION_ALLOW` was never a real
# attribute -- pull the constant from its actual source instead.
from server_modules.agent_computer_policy_service import DECISION_ALLOW


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
        risk_decision = type("RiskDecision", (), {"decision": DECISION_ALLOW})()
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
            # execute_gateway_browser_action reads gateway_policy.policy_id
            # when consuming approval memory (see routes_gateway.py's
            # _consume_gateway_approval_memory call) -- a plain object()
            # doesn't have one.
            patch.object(
                routes_gateway,
                "_gateway_policy_from_registration",
                return_value=type("GatewayPolicy", (), {"policy_id": "policy-1"})(),
            ),
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


# test_gateway_approval_required_response_wrong_rust_action_blocks_before_
# approval_creation and
# test_resolve_gateway_registration_approval_wrong_rust_action_blocks_
# before_resolution deleted: both called routes_gateway.
# _gateway_approval_required_response and routes_gateway.
# resolve_gateway_registration_approval, neither of which exists anymore --
# 0820a732c ("Remove approval system — agent now acts on reasoning, not
# approval gates") removed both route handlers along with the rest of the
# approval-gate system. Not a mock gap to patch; the functions under test
# are gone.


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
        # start_gateway_browser_session reads gateway_policy.policy_id when
        # consuming approval memory (see routes_gateway.py's
        # _consume_gateway_approval_memory call) -- a plain object()
        # doesn't have one.
        gateway_policy = type("GatewayPolicy", (), {"policy_id": "policy-1"})()
        risk_decision = type("RiskDecision", (), {"decision": DECISION_ALLOW})()
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


# test_gateway_approval_memory_consume_accepts_allow_gateway_service_operation
# and test_gateway_approval_memory_consume_wrong_rust_action_blocks_before_
# consume deleted: both patched routes_gateway.agent_approval_memory_service,
# a module reference that no longer exists on routes_gateway (0820a732c,
# "Remove approval system", stripped the import), and both asserted
# behavior for routes_gateway._consume_gateway_approval_memory that
# contradicts what the function actually does now -- it's a hardcoded
# `return None` stub ("# Phase 4: approval memory system removed", see its
# one-line body in routes_gateway.py). The first test asserted a real rule
# gets returned and consumed; the second asserted the function raises
# HTTPException on a bad kernel decision. Neither is true of a function
# that unconditionally returns None before ever calling the kernel gate it
# mocks.
