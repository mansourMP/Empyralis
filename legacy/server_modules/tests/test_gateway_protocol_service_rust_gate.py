import json

import pytest

def _install_allow_gate(monkeypatch, rust_client):
    calls = []

    def allow_decision(**request):
        calls.append(request)
        return {
            "ok": True,
            "decision": "allow",
            "operation": request.get("operation"),
            "next_action": request.get("operation"),
        }

    def enforce(_command, decision):
        return decision

    monkeypatch.setattr(rust_client, "runtime_state_store_decision", allow_decision)
    monkeypatch.setattr(rust_client, "enforce_kernel_decision", enforce)
    return calls


def _install_gateway_frame_allow_gate(monkeypatch, rust_client):
    calls = []

    def allow_decision(**request):
        calls.append(request)
        frame_kind = str(request.get("kind") or "").strip()
        message_type = str(request.get("type") or "").strip()
        if frame_kind == "request" and message_type == "gateway.connect":
            next_action = "accept_gateway_connect"
        elif frame_kind == "request":
            next_action = "route_gateway_request"
        elif frame_kind == "response":
            next_action = "resolve_gateway_response"
        elif frame_kind == "event":
            next_action = "handle_gateway_event"
        else:
            next_action = "record_gateway_frame"
        return {
            "ok": True,
            "decision": "allow",
            "operation": request.get("operation"),
            "next_action": next_action,
        }

    def enforce(_command, decision):
        return decision

    monkeypatch.setattr(rust_client, "gateway_frame_decision", allow_decision)
    monkeypatch.setattr(rust_client, "enforce_kernel_decision", enforce)
    return calls


def _install_gateway_state_allow_gate(monkeypatch, rust_client):
    calls = []

    def allow_decision(**request):
        calls.append(request)
        return {
            "ok": True,
            "decision": "allow",
            "operation": request.get("operation"),
            "next_action": request.get("operation"),
        }

    def enforce(_command, decision):
        return decision

    monkeypatch.setattr(rust_client, "gateway_state_decision", allow_decision)
    monkeypatch.setattr(rust_client, "enforce_kernel_decision", enforce)
    return calls


def test_gateway_protocol_request_frame_calls_rust_gate(monkeypatch):
    from server_modules import gateway_protocol_service

    calls = _install_allow_gate(monkeypatch, gateway_protocol_service.rust_runtime_kernel_client)

    gateway_protocol_service._enforce_gateway_protocol_request_frame(
        gateway_id="gateway-1",
        session_id="session-1",
        message_type="tool.invoke",
        frame={
            "id": "request-1",
            "scope": {"workspace_id": "workspace-1"},
            "payload": {"run_id": "run-1", "capability_id": "browser.click"},
        },
    )

    assert calls[0]["operation"] == "send_gateway_protocol_request_frame"
    assert calls[0]["state_class"] == "gateway_protocol_frames"
    assert calls[0]["workspace_id"] == "workspace-1"
    assert calls[0]["run_id"] == "run-1"
    assert calls[0]["payload"]["message_type"] == "tool.invoke"


def test_gateway_inbound_frame_calls_rust_gate(monkeypatch):
    from server_modules import gateway_protocol_service

    calls = _install_gateway_frame_allow_gate(
        monkeypatch,
        gateway_protocol_service.rust_runtime_kernel_client,
    )

    raw = json.dumps(
        {
            "kind": "request",
            "protocolVersion": "v1alpha2",
            "id": "connect",
            "type": "gateway.connect",
            "payload": {"gateway_version": "0.1.0"},
        }
    )
    frame = json.loads(raw)
    gateway_protocol_service._enforce_gateway_inbound_frame_decision(
        raw_text=raw,
        frame=frame,
        seq=7,
        ack=3,
    )

    assert calls[0]["operation"] == "validate_frame"
    assert calls[0]["kind"] == "request"
    assert calls[0]["type"] == "gateway.connect"
    assert calls[0]["protocol_version"] == "v1alpha2"
    assert calls[0]["raw_size_bytes"] == len(raw.encode("utf-8"))
    assert calls[0]["seq"] == 7
    assert calls[0]["ack"] == 3


def test_gateway_session_mutation_calls_rust_gate(monkeypatch):
    from server_modules import gateway_protocol_service

    calls = _install_gateway_state_allow_gate(
        monkeypatch,
        gateway_protocol_service.rust_runtime_kernel_client,
    )

    gateway_protocol_service._enforce_gateway_session_mutation(
        registration={
            "gateway_id": "gateway-1",
            "workspace_id": "workspace-1",
            "tenant_id": "tenant-1",
            "user_id": "user-1",
            "device_id": "device-1",
            "status": "active",
        },
        session={
            "session_id": "session-1",
            "gateway_id": "gateway-1",
            "status": "connected",
        },
        operation="mark_session_connected",
    )

    assert calls[0]["operation"] == "mark_session_connected"
    assert calls[0]["gateway_id"] == "gateway-1"
    assert calls[0]["session_id"] == "session-1"
    assert calls[0]["workspace_id"] == "workspace-1"
    assert calls[0]["tenant_id"] == "tenant-1"
    assert calls[0]["user_id"] == "user-1"
    assert calls[0]["device_id"] == "device-1"
    assert calls[0]["status"] == "connected"
    assert calls[0]["is_service"] is True


def test_gateway_websocket_connect_calls_rust_gate(monkeypatch):
    from server_modules import gateway_protocol_service

    calls = []

    def allow_decision(command, payload):
        assert command == "gateway-service-decision"
        calls.append(payload)
        return {
            "ok": True,
            "decision": "allow",
            "operation": payload.get("operation"),
            "next_action": "allow_gateway_service_operation",
        }

    monkeypatch.setattr(
        gateway_protocol_service.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        allow_decision,
    )

    gateway_protocol_service._enforce_gateway_websocket_connect_decision(
        gateway_id="gateway-1",
        session_id="session-1",
        workspace_id="workspace-1",
        tenant_id="tenant-1",
        device_id="device-1",
        request_id="session-1",
        actor_id="user-1",
        device_trust_state="trusted",
    )

    assert calls[0]["operation"] == "websocket_connect"
    assert calls[0]["gateway_id"] == "gateway-1"
    assert calls[0]["session_id"] == "session-1"
    assert calls[0]["workspace_id"] == "workspace-1"
    assert calls[0]["tenant_id"] == "tenant-1"
    assert calls[0]["device_trust_state"] == "trusted"
    assert calls[0]["websocket_token_present"] is True


def test_gateway_quota_check_calls_rust_gate(monkeypatch):
    from server_modules import gateway_protocol_service

    calls = []

    def allow_decision(command, payload):
        assert command == "gateway-service-decision"
        calls.append(payload)
        return {
            "ok": True,
            "decision": "allow",
            "operation": payload.get("operation"),
            "next_action": "allow_gateway_service_operation",
        }

    monkeypatch.setattr(
        gateway_protocol_service.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        allow_decision,
    )

    gateway_protocol_service._enforce_gateway_quota_check(
        gateway_id="gateway-1",
        session_id="session-1",
        workspace_id="workspace-1",
        tenant_id="tenant-1",
        device_id="device-1",
        request_id="req-1",
        quota_profile="gateway_channel_outbound",
    )

    assert calls[0]["operation"] == "quota_check"
    assert calls[0]["gateway_id"] == "gateway-1"
    assert calls[0]["session_id"] == "session-1"
    assert calls[0]["workspace_id"] == "workspace-1"
    assert calls[0]["tenant_id"] == "tenant-1"
    assert calls[0]["quota_profile"] == "gateway_channel_outbound"
    assert calls[0]["quota_ok"] is True


def test_gateway_protocol_request_frame_blocks_wrong_rust_action(monkeypatch):
    from server_modules import gateway_protocol_service

    def allow_decision(**request):
        return {
            "ok": True,
            "decision": "allow",
            "operation": request.get("operation"),
            "next_action": "write_notification",
        }

    def enforce(_command, decision):
        return decision

    monkeypatch.setattr(gateway_protocol_service.rust_runtime_kernel_client, "runtime_state_store_decision", allow_decision)
    monkeypatch.setattr(gateway_protocol_service.rust_runtime_kernel_client, "enforce_kernel_decision", enforce)

    with pytest.raises(gateway_protocol_service.GatewayProtocolRustGateError, match="unexpected next_action"):
        gateway_protocol_service._enforce_gateway_protocol_request_frame(
            gateway_id="gateway-1",
            session_id="session-1",
            message_type="tool.invoke",
            frame={
                "id": "request-1",
                "scope": {"workspace_id": "workspace-1"},
                "payload": {"run_id": "run-1"},
            },
        )


def test_gateway_inbound_frame_blocks_wrong_rust_action(monkeypatch):
    from server_modules import gateway_protocol_service

    def allow_decision(**request):
        return {
            "ok": True,
            "decision": "allow",
            "operation": request.get("operation"),
            "next_action": "record_gateway_frame",
        }

    def enforce(_command, decision):
        return decision

    monkeypatch.setattr(gateway_protocol_service.rust_runtime_kernel_client, "gateway_frame_decision", allow_decision)
    monkeypatch.setattr(gateway_protocol_service.rust_runtime_kernel_client, "enforce_kernel_decision", enforce)

    raw = json.dumps(
        {
            "kind": "request",
            "protocolVersion": "v1alpha2",
            "id": "connect",
            "type": "gateway.connect",
            "payload": {"gateway_version": "0.1.0"},
        }
    )
    frame = json.loads(raw)

    with pytest.raises(gateway_protocol_service.GatewayFrameValidationError, match="unexpected next_action"):
        gateway_protocol_service._enforce_gateway_inbound_frame_decision(
            raw_text=raw,
            frame=frame,
            seq=None,
            ack=None,
        )


def test_gateway_session_mutation_blocks_wrong_rust_action(monkeypatch):
    from server_modules import gateway_protocol_service

    def allow_decision(**request):
        return {
            "ok": True,
            "decision": "allow",
            "operation": request.get("operation"),
            "next_action": "issue_gateway_session",
        }

    def enforce(_command, decision):
        return decision

    monkeypatch.setattr(gateway_protocol_service.rust_runtime_kernel_client, "gateway_state_decision", allow_decision)
    monkeypatch.setattr(gateway_protocol_service.rust_runtime_kernel_client, "enforce_kernel_decision", enforce)

    with pytest.raises(gateway_protocol_service.GatewayProtocolRustGateError, match="unexpected next_action"):
        gateway_protocol_service._enforce_gateway_session_mutation(
            registration={
                "gateway_id": "gateway-1",
                "workspace_id": "workspace-1",
                "tenant_id": "tenant-1",
                "user_id": "user-1",
                "device_id": "device-1",
                "status": "active",
            },
            session={
                "session_id": "session-1",
                "gateway_id": "gateway-1",
                "status": "connected",
            },
            operation="mark_session_connected",
        )


@pytest.mark.asyncio
async def test_dispatch_channel_outbound_wrong_quota_action_blocks_before_send(monkeypatch):
    from server_modules import gateway_protocol_service

    class _Connection:
        session_id = "session-1"
        scope = {"workspace_id": "workspace-1", "tenant_id": "tenant-1"}

        async def send_request(self, **kwargs):
            raise AssertionError("should not send outbound frame")

    monkeypatch.setattr(
        gateway_protocol_service,
        "evaluate_gateway_quota",
        lambda profile, gateway_id: type("Decision", (), {"allowed": True, "retry_after_seconds": 0})(),
    )
    monkeypatch.setattr(
        gateway_protocol_service,
        "_get_live_connection",
        lambda gateway_id: _Connection(),
    )
    monkeypatch.setattr(
        gateway_protocol_service.gateway_state_repository,
        "get_gateway_registration",
        lambda gateway_id: {"workspace_id": "workspace-1", "tenant_id": "tenant-1", "device_id": "device-1"},
    )

    def wrong_quota_action(command, payload):
        assert command == "gateway-service-decision"
        return {
            "ok": True,
            "decision": "allow",
            "operation": payload.get("operation"),
            "next_action": "dispatch_gateway_operation",
        }

    monkeypatch.setattr(
        gateway_protocol_service.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        wrong_quota_action,
    )

    with pytest.raises(gateway_protocol_service.GatewayProtocolRustGateError, match="unexpected next_action"):
        await gateway_protocol_service.dispatch_channel_outbound(
            gateway_id="gateway-1",
            channel_key="telegram_personal",
            provider="telegram",
            remote_jid="user-1",
            text="hello",
            idempotency_key="idem-1",
            request_id="req-1",
        )


@pytest.mark.asyncio
async def test_handle_gateway_websocket_wrong_connect_action_blocks_before_accept(monkeypatch):
    from server_modules import gateway_protocol_service

    class _FakeWebSocket:
        def __init__(self):
            self.accepted = False

        async def accept(self, subprotocol=None):
            self.accepted = True

    websocket = _FakeWebSocket()

    monkeypatch.setattr(
        gateway_protocol_service.gateway_state_repository,
        "validate_gateway_session",
        lambda gateway_id, session_token: {"session_id": "session-1", "gateway_id": gateway_id},
    )
    monkeypatch.setattr(
        gateway_protocol_service.gateway_state_repository,
        "get_gateway_registration",
        lambda gateway_id: {
            "gateway_id": gateway_id,
            "workspace_id": "workspace-1",
            "tenant_id": "tenant-1",
            "user_id": "user-1",
            "device_id": "device-1",
            "device_trust_state": "trusted",
        },
    )

    async def _binding_ok(*, session, registration):
        return {"binding": "ok"}

    monkeypatch.setattr(gateway_protocol_service, "_validate_gateway_binding", _binding_ok)
    monkeypatch.setattr(
        gateway_protocol_service.gateway_registry_service,
        "gateway_scope_payload",
        lambda registration: {"workspace_id": registration.get("workspace_id")},
    )

    def wrong_action(command, payload):
        assert command == "gateway-service-decision"
        return {
            "ok": True,
            "decision": "allow",
            "operation": payload.get("operation"),
            "next_action": "dispatch_gateway_operation",
        }

    monkeypatch.setattr(
        gateway_protocol_service.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        wrong_action,
    )

    with pytest.raises(gateway_protocol_service.GatewayProtocolRustGateError, match="unexpected next_action"):
        await gateway_protocol_service.handle_gateway_websocket(
            websocket,
            gateway_id="gateway-1",
            session_token="token-1",
            accept_subprotocol=None,
        )

    assert websocket.accepted is False


@pytest.mark.asyncio
async def test_handle_gateway_websocket_wrong_quota_action_blocks_before_accept(monkeypatch):
    from server_modules import gateway_protocol_service

    class _FakeWebSocket:
        def __init__(self):
            self.accepted = False

        async def accept(self, subprotocol=None):
            self.accepted = True

    websocket = _FakeWebSocket()

    monkeypatch.setattr(
        gateway_protocol_service.gateway_state_repository,
        "validate_gateway_session",
        lambda gateway_id, session_token: {"session_id": "session-1", "gateway_id": gateway_id},
    )
    monkeypatch.setattr(
        gateway_protocol_service.gateway_state_repository,
        "get_gateway_registration",
        lambda gateway_id: {
            "gateway_id": gateway_id,
            "workspace_id": "workspace-1",
            "tenant_id": "tenant-1",
            "user_id": "user-1",
            "device_id": "device-1",
            "device_trust_state": "trusted",
        },
    )

    async def _binding_ok(*, session, registration):
        return {"binding": "ok"}

    monkeypatch.setattr(gateway_protocol_service, "_validate_gateway_binding", _binding_ok)
    monkeypatch.setattr(
        gateway_protocol_service.gateway_registry_service,
        "gateway_scope_payload",
        lambda registration: {"workspace_id": registration.get("workspace_id")},
    )

    def wrong_quota_action(command, payload):
        assert command == "gateway-service-decision"
        if payload.get("operation") == "quota_check":
            return {
                "ok": True,
                "decision": "allow",
                "operation": payload.get("operation"),
                "next_action": "dispatch_gateway_operation",
            }
        return {
            "ok": True,
            "decision": "allow",
            "operation": payload.get("operation"),
            "next_action": "allow_gateway_service_operation",
        }

    monkeypatch.setattr(
        gateway_protocol_service.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        wrong_quota_action,
    )

    with pytest.raises(gateway_protocol_service.GatewayProtocolRustGateError, match="unexpected next_action"):
        await gateway_protocol_service.handle_gateway_websocket(
            websocket,
            gateway_id="gateway-1",
            session_token="token-1",
            accept_subprotocol=None,
        )

    assert websocket.accepted is False
