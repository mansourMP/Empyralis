import pytest
from fastapi import HTTPException

from server_modules import connection_certification_service as service


def test_doctor_reports_planned_requirements_for_apple_messages(monkeypatch):
    monkeypatch.setattr(
        service.connection_catalog_service,
        "status_items",
        lambda **_kwargs: [],
    )

    payload = service.doctor_connection(
        connection_id="apple_messages_business",
        workspace_id="ws-1",
        tenant_id="tenant-1",
        user_id="user-1",
        surface="sage",
    )

    assert payload["readiness_status"] == "planned"
    assert payload["launch_certified"] is False
    assert payload["certification_required"] is True
    assert payload["certification_requirements"]
    assert any(check["status"] == "block" for check in payload["doctor_checks"])


def test_certify_provider_channel_delegates_to_verify_service(monkeypatch):
    calls = []

    monkeypatch.setattr(
        service.connection_catalog_service,
        "status_items",
        lambda **_kwargs: [
            {
                "id": "telegram_bot",
                "display_name": "Telegram Bot",
                "launch_status": "live_when_configured",
                "setup_available": True,
                "runtime_usable": True,
                "launchable": True,
                "requires_gateway": False,
                "requires_external_account": True,
                "connected": False,
                "health_status": "not_configured",
                "readiness_status": "implementation_ready",
                "readiness_reason": "runtime_available_needs_account_certification",
                "certification_required": True,
                "certification_requirements": [],
                "vault_provider": "telegram_bot",
                "next_action": "connect",
            }
        ],
    )

    def fake_verify_connection(**kwargs):
        calls.append(kwargs)
        return {"valid": True, "account_name": "@sage", "account_id": "42"}

    monkeypatch.setattr(service.connection_verify_service, "verify_connection", fake_verify_connection)

    payload = service.certify_connection(
        connection_id="telegram_bot",
        workspace_id="ws-1",
        tenant_id="tenant-1",
        user_id="user-1",
        surface="sage",
    )

    assert payload["certified"] is True
    assert payload["readiness_status"] == "launch_certified"
    assert payload["account_name"] == "@sage"
    assert calls[0]["connection_id"] == "telegram_bot"
    assert calls[0]["provider"] == "telegram_bot"


def test_certify_local_bridge_requires_healthy_selected_gateway(monkeypatch):
    monkeypatch.setattr(
        service.connection_catalog_service,
        "status_items",
        lambda **_kwargs: [
            {
                "id": "wechat_personal",
                "display_name": "WeChat",
                "launch_status": "planned",
                "setup_available": False,
                "runtime_usable": False,
                "launchable": False,
                "requires_gateway": True,
                "requires_external_account": False,
                "selected_gateway_id": "gateway-1",
                "selected_gateway_missing": False,
                "connected": True,
                "health_status": "healthy",
                "readiness_status": "launch_certified",
                "readiness_reason": "connected_runtime_certified",
                "certification_required": False,
                "certification_requirements": [],
                "next_action": "locked",
            }
        ],
    )

    payload = service.certify_connection(
        connection_id="wechat_personal",
        workspace_id="ws-1",
        tenant_id="tenant-1",
        user_id="user-1",
        surface="sage",
        selected_gateway_id="gateway-1",
    )

    assert payload["certified"] is True
    assert payload["certification_kind"] == "agent_computer_bridge_health"
    assert payload["readiness_status"] == "launch_certified"


def test_certify_planned_cloud_channel_returns_actionable_requirements(monkeypatch):
    monkeypatch.setattr(
        service.connection_catalog_service,
        "status_items",
        lambda **_kwargs: [],
    )

    with pytest.raises(HTTPException) as raised:
        service.certify_connection(
            connection_id="whatsapp_twilio",
            workspace_id="ws-1",
            tenant_id="tenant-1",
            user_id="user-1",
            surface="studio",
        )

    assert raised.value.status_code == 409
    assert raised.value.detail["requirements"]
