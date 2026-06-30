from __future__ import annotations

from typing import Any

import pytest

from server_modules import channel_platform_service as service


def _agent_record(agent_id: str = "dagent-1") -> dict[str, Any]:
    return {
        "id": agent_id,
        "tenant_id": "tenant-1",
        "owner_workspace_id": "ws-1",
        "backing_install_id": f"install-{agent_id}",
        "channels": {},
    }


def _vault_connectors() -> list[dict[str, Any]]:
    return [
        {
            "id": "cred-telegram",
            "label": "Telegram bot",
            "connector": "telegram_bot",
            "workspace_id": "ws-1",
            "metadata": {
                "bot_username": "empyralis_bot",
                "bot_token": "123456:must-not-leak",
                "safe_note": "workspace bot",
            },
        }
    ]


def test_channel_platform_catalog_has_launch_surfaces_and_reserved_private_runtime_items() -> None:
    payload = service.list_channel_catalog()
    keys = [item["channel_key"] for item in payload["items"]]

    assert len(keys) == 24
    assert "web_chat" in keys
    assert "telegram_bot" in keys
    assert "telegram_personal" in keys
    assert "whatsapp_personal" in keys
    assert "signal_personal" in keys
    assert "imessage_personal" in keys
    assert "wechat_personal" in keys
    assert "apple_messages_business" in keys
    assert "dropbox" in keys
    assert "s3" in keys
    assert "smtp" in keys
    assert "wechat_work" in keys
    assert "instagram_business" in keys
    by_key = {item["channel_key"]: item for item in payload["items"]}
    assert by_key["github"]["surface_kind"] == "connected_app"
    assert by_key["dropbox"]["surface_kind"] == "connected_app"
    assert by_key["s3"]["launch_allowed"] is True
    assert by_key["smtp"]["status"] == "working_when_configured"
    assert by_key["wechat_work"]["product_surface"] == "connected_app"
    assert by_key["instagram_business"]["live_capable"] is True
    assert by_key["telegram_bot"]["surface_kind"] == "messaging_channel"
    assert by_key["signal_personal"]["product_surface"] == "personal_messaging"
    assert by_key["apple_messages_business"]["product_surface"] == "business_channel"
    assert by_key["apple_messages_business"]["requires_agent_computer"] is False
    assert by_key["apple_messages_business"]["live_capable"] is False
    assert "human_handoff_required" in by_key["apple_messages_business"]["capabilities"]
    assert payload["groups"]["business_channels"] >= 1
    assert payload["groups"]["connected_apps"] >= 1
    assert payload["groups"]["personal_messaging"] >= 1
    assert payload["concepts"]["messaging_channel"]
    assert payload["concepts"]["connected_app"]
    assert payload["concepts"]["extension"]
    assert payload["concepts"]["agent_channel_binding"]
    assert not (
        {item["channel_key"] for item in payload["reserved"]}
        & {"imessage_personal", "signal_personal", "wechat_personal"}
    )


def test_channel_accounts_project_secret_refs_without_raw_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service.runtime_common, "list_vault_connectors", lambda workspace_id: _vault_connectors())
    monkeypatch.setattr(service.gateway_state_repository, "list_workspace_gateway_registrations", lambda *args, **kwargs: [])

    payload = service.list_workspace_channel_accounts(
        tenant_id="tenant-1",
        workspace_id="ws-1",
        current_user={"user_id": "owner-1"},
    )

    assert payload["items"][0]["account_ref"] == "vault:cred-telegram"
    assert payload["items"][0]["secret_ref"] == "vault://credential/cred-telegram"
    assert "bot_token" not in payload["items"][0]["metadata"]
    assert "must-not-leak" not in str(payload)


@pytest.mark.anyio
async def test_create_channel_account_uses_vault_and_returns_projection(monkeypatch: pytest.MonkeyPatch) -> None:
    created_requests: list[Any] = []

    async def fake_create_vault_credential(body):
        created_requests.append(body)
        return {
            "id": "cred-slack",
            "label": body.label,
            "provider": body.provider,
            "workspace_id": body.workspace_id,
            "metadata": {"workspace": "support", "bot_token": "must-not-leak"},
            "created_at": "2026-05-25T00:00:00Z",
            "updated_at": "2026-05-25T00:00:00Z",
        }

    monkeypatch.setattr(service.connectors_actions, "create_vault_credential", fake_create_vault_credential)
    monkeypatch.setattr(service.security_audit_service, "emit_security_audit_event", lambda **_kwargs: None)

    payload = await service.create_workspace_channel_account(
        tenant_id="tenant-1",
        workspace_id="ws-1",
        provider="slack",
        label="Support Slack",
        credentials={"bot_token": "xoxb-must-not-leak"},
        skip_validation=True,
        current_user={"user_id": "owner-1"},
    )

    assert created_requests[0].provider == "slack"
    assert created_requests[0].credentials["bot_token"] == "xoxb-must-not-leak"
    assert payload["account"]["account_ref"] == "vault:cred-slack"
    assert payload["account"]["secret_ref"] == "vault://credential/cred-slack"
    assert "must-not-leak" not in str(payload)


@pytest.mark.anyio
async def test_upsert_agent_channel_binding_uses_per_agent_endpoint_and_secret_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, Any]] = []

    async def fake_get_deployed_agent_by_id(deployed_agent_id: str, **_kwargs: Any) -> dict[str, Any]:
        return _agent_record(deployed_agent_id)

    async def fake_update_deployed_agent(deployed_agent_id: str, **kwargs: Any) -> dict[str, Any]:
        channels = kwargs["updates"]["channels"]
        captured.append({"deployed_agent_id": deployed_agent_id, "channels": channels})
        return {**_agent_record(deployed_agent_id), "channels": channels}

    async def fake_save_workspace_specialist_channel_bindings(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"ok": True}

    monkeypatch.setattr(service.runtime_common, "list_vault_connectors", lambda workspace_id: _vault_connectors())
    monkeypatch.setattr(service.gateway_state_repository, "list_workspace_gateway_registrations", lambda *args, **kwargs: [])
    monkeypatch.setattr(service.control_plane_repository, "get_deployed_agent_by_id", fake_get_deployed_agent_by_id)
    monkeypatch.setattr(service.control_plane_repository, "update_deployed_agent", fake_update_deployed_agent)
    monkeypatch.setattr(
        service.agent_specialist_repository,
        "save_workspace_specialist_channel_bindings",
        fake_save_workspace_specialist_channel_bindings,
    )
    monkeypatch.setattr(service.security_audit_service, "emit_security_audit_event", lambda **_kwargs: None)

    first = await service.upsert_agent_channel_binding(
        tenant_id="tenant-1",
        workspace_id="ws-1",
        deployed_agent_id="dagent-a",
        catalog_id="telegram_bot",
        account_ref="vault:cred-telegram",
        current_user={"user_id": "owner-1"},
    )
    second = await service.upsert_agent_channel_binding(
        tenant_id="tenant-1",
        workspace_id="ws-1",
        deployed_agent_id="dagent-b",
        catalog_id="telegram_bot",
        account_ref="vault:cred-telegram",
        current_user={"user_id": "owner-1"},
    )

    assert first["binding"]["channel_key"] == "telegram"
    assert second["binding"]["endpoint_key"] == "agent:dagent-b:telegram"
    assert captured[0]["channels"]["telegram"]["endpoint_key"] == "agent:dagent-a:telegram"
    assert captured[1]["channels"]["telegram"]["endpoint_key"] == "agent:dagent-b:telegram"
    assert captured[0]["channels"]["telegram"]["secret_ref"] == "vault://credential/cred-telegram"
    assert "must-not-leak" not in str(captured)


@pytest.mark.anyio
async def test_studio_binding_rejects_personal_agent_computer_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_deployed_agent_by_id(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return _agent_record()

    monkeypatch.setattr(service.control_plane_repository, "get_deployed_agent_by_id", fake_get_deployed_agent_by_id)
    monkeypatch.setattr(service.runtime_common, "list_vault_connectors", lambda workspace_id: [])
    monkeypatch.setattr(service.gateway_state_repository, "list_workspace_gateway_registrations", lambda *args, **kwargs: [])

    with pytest.raises(service.ChannelPlatformError, match="Personal channels"):
        await service.upsert_agent_channel_binding(
            tenant_id="tenant-1",
            workspace_id="ws-1",
            deployed_agent_id="dagent-1",
            catalog_id="telegram_personal",
            account_ref=None,
            current_user={"user_id": "owner-1"},
        )


@pytest.mark.anyio
async def test_studio_binding_rejects_not_launch_ready_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_deployed_agent_by_id(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return _agent_record()

    monkeypatch.setattr(service.control_plane_repository, "get_deployed_agent_by_id", fake_get_deployed_agent_by_id)
    monkeypatch.setattr(service.runtime_common, "list_vault_connectors", lambda workspace_id: [])
    monkeypatch.setattr(service.gateway_state_repository, "list_workspace_gateway_registrations", lambda *args, **kwargs: [])

    with pytest.raises(service.ChannelPlatformError, match="not launch-ready"):
        await service.upsert_agent_channel_binding(
            tenant_id="tenant-1",
            workspace_id="ws-1",
            deployed_agent_id="dagent-1",
            catalog_id="whatsapp_business",
            account_ref=None,
            current_user={"user_id": "owner-1"},
        )


@pytest.mark.anyio
async def test_channel_test_send_is_dry_run_only(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_deployed_agent_by_id(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            **_agent_record(),
            "channels": {
                "telegram": {
                    "enabled": True,
                    "catalog_id": "telegram_bot",
                    "runtime_lane": "studio_business_connector",
                    "endpoint_key": "agent:dagent-1:telegram",
                }
            },
        }

    monkeypatch.setattr(service.control_plane_repository, "get_deployed_agent_by_id", fake_get_deployed_agent_by_id)
    monkeypatch.setattr(service.security_audit_service, "emit_security_audit_event", lambda **_kwargs: None)

    dry_run = await service.test_agent_channel_binding(
        tenant_id="tenant-1",
        workspace_id="ws-1",
        deployed_agent_id="dagent-1",
        channel_key="telegram",
        message="hello",
        dry_run=True,
        current_user={"user_id": "owner-1"},
    )

    assert dry_run["status"] == "dry_run_ready"
    assert dry_run["live_send_performed"] is False

    with pytest.raises(service.ChannelPlatformError, match="Live channel test sends"):
        await service.test_agent_channel_binding(
            tenant_id="tenant-1",
            workspace_id="ws-1",
            deployed_agent_id="dagent-1",
            channel_key="telegram",
            message="hello",
            dry_run=False,
            current_user={"user_id": "owner-1"},
        )
