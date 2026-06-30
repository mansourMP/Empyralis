import pytest
from fastapi import HTTPException

from server_modules import connection_verify_service as service


def test_verify_telegram_bot_uses_latest_saved_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service.runtime_common,
        "list_vault_credentials",
        lambda workspace_id=None: [
            {
                "id": "cred-old",
                "provider": "telegram_bot",
                "workspace_id": workspace_id,
                "updated_at": "2026-06-09T00:00:00Z",
                "metadata": {"channel_account": True, "connection_id": "telegram_bot"},
            },
            {
                "id": "cred-new",
                "provider": "telegram_bot",
                "workspace_id": workspace_id,
                "updated_at": "2026-06-10T00:00:00Z",
                "metadata": {"channel_account": True, "connection_id": "telegram_bot"},
            },
        ],
    )
    resolved_ids: list[str] = []

    def fake_resolve(credential_id, **_kwargs):
        resolved_ids.append(credential_id)
        return {"bot_token": "123:abc"}

    monkeypatch.setattr(service.runtime_common, "resolve_vault_credential", fake_resolve)
    monkeypatch.setattr(
        service,
        "_telegram_get_me",
        lambda bot_token: {"id": 123, "username": "empyralis_bot", "first_name": "Empyralis"},
    )

    payload = service.verify_connection(
        connection_id="telegram_bot",
        provider="telegram_bot",
        workspace_id="ws-1",
        tenant_id="tenant-1",
    )

    assert resolved_ids == ["cred-new"]
    assert payload == {
        "valid": True,
        "account_name": "@empyralis_bot",
        "account_id": "123",
    }


def test_verify_discord_bot_returns_bot_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service.runtime_common,
        "list_vault_credentials",
        lambda workspace_id=None: [
            {
                "id": "cred-discord",
                "provider": "discord_bot",
                "workspace_id": workspace_id,
                "updated_at": "2026-06-10T00:00:00Z",
                "metadata": {"channel_account": True, "connection_id": "discord_bot"},
            },
        ],
    )
    monkeypatch.setattr(
        service.runtime_common,
        "resolve_vault_credential",
        lambda credential_id, **_kwargs: {"bot_token": "discord-token"},
    )
    monkeypatch.setattr(
        service.discord_connector,
        "_discord_api_call",
        lambda path, **_kwargs: {"id": "999", "username": "EmpyralisBot", "global_name": "Empyralis"},
    )

    payload = service.verify_connection(
        connection_id="discord_bot",
        provider="discord_bot",
        workspace_id="ws-1",
        tenant_id="tenant-1",
    )

    assert payload == {
        "valid": True,
        "account_name": "Empyralis",
        "account_id": "999",
    }


def test_verify_unsupported_connection_returns_conflict() -> None:
    with pytest.raises(HTTPException) as raised:
        service.verify_connection(
            connection_id="slack",
            provider="slack",
            workspace_id="ws-1",
            tenant_id="tenant-1",
        )

    assert raised.value.status_code == 409
