from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from server_modules import routes_studio


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(routes_studio.router, prefix="/api")
    return app


def _owner_user() -> dict[str, object]:
    return {
        "user_id": "owner-1",
        "tenant_id": "tenant-1",
        "workspace_access": {
            "ws-1": {
                "workspace_id": "ws-1",
                "tenant_id": "tenant-1",
                "role": "owner",
                "tenant_role": "owner",
                "capabilities": {"allow": [], "deny": []},
                "tenant_capabilities": {"allow": [], "deny": []},
                "workspace_capabilities": {"allow": [], "deny": []},
            }
        },
    }


@pytest.mark.anyio
async def test_channel_catalog_lists_platform_surfaces(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _build_app()
    app.dependency_overrides[routes_studio.get_current_user] = _owner_user
    monkeypatch.setattr(
        routes_studio.auth_module,
        "enforce_workspace_access",
        lambda current_user, workspace_id, minimum_role="viewer": workspace_id,
    )
    monkeypatch.setattr(routes_studio.auth_module, "workspace_tenant_id", lambda current_user, workspace_id: "tenant-1")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/studio/channel-catalog", params={"workspace_id": "ws-1"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 24
    assert any(item["channel_key"] == "telegram_personal" for item in payload["items"])
    assert any(item["channel_key"] == "signal_personal" for item in payload["items"])
    assert any(item["channel_key"] == "wechat_personal" for item in payload["items"])
    assert any(item["channel_key"] == "apple_messages_business" for item in payload["items"])
    assert not any(item["channel_key"] == "imessage_personal" for item in payload["reserved"])


@pytest.mark.anyio
async def test_channel_binding_create_requires_csrf(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _build_app()
    app.dependency_overrides[routes_studio.get_current_user] = _owner_user

    def reject_csrf(_request):
        raise HTTPException(status_code=403, detail="CSRF validation failed.")

    monkeypatch.setattr(routes_studio.auth_module, "validate_csrf", reject_csrf)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/studio/agents/dagent-1/channel-bindings",
            json={"workspace_id": "ws-1", "catalog_id": "telegram_bot", "account_ref": "vault:cred-1"},
        )

    assert response.status_code == 403
    assert "csrf" in response.text.lower()


@pytest.mark.anyio
async def test_channel_account_create_requires_csrf(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _build_app()
    app.dependency_overrides[routes_studio.get_current_user] = _owner_user

    def reject_csrf(_request):
        raise HTTPException(status_code=403, detail="CSRF validation failed.")

    monkeypatch.setattr(routes_studio.auth_module, "validate_csrf", reject_csrf)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/studio/channel-accounts",
            json={
                "workspace_id": "ws-1",
                "provider": "slack",
                "label": "Support Slack",
                "credentials": {"bot_token": "xoxb-test"},
                "skip_validation": True,
            },
        )

    assert response.status_code == 403
    assert "csrf" in response.text.lower()


@pytest.mark.anyio
async def test_channel_binding_routes_use_owner_scope_and_service(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _build_app()
    app.dependency_overrides[routes_studio.get_current_user] = _owner_user

    scopes: list[tuple[str, str]] = []
    service_calls: list[str] = []

    def fake_enforce_workspace_access(current_user, workspace_id, minimum_role="viewer"):
        scopes.append((workspace_id, minimum_role))
        return workspace_id

    async def fake_upsert_agent_channel_binding(**kwargs: Any) -> dict[str, Any]:
        service_calls.append("upsert")
        assert kwargs["deployed_agent_id"] == "dagent-1"
        assert kwargs["catalog_id"] == "telegram_bot"
        return {"binding": {"channel_key": "telegram", "enabled": True}}

    async def fake_test_agent_channel_binding(**kwargs: Any) -> dict[str, Any]:
        service_calls.append("test")
        assert kwargs["dry_run"] is True
        return {"status": "dry_run_ready", "live_send_performed": False}

    async def fake_set_agent_channel_binding_state(**kwargs: Any) -> dict[str, Any]:
        service_calls.append(kwargs["action"])
        return {"binding": {"channel_key": kwargs["channel_key"], "status": kwargs["action"]}}

    async def fake_create_workspace_channel_account(**kwargs: Any) -> dict[str, Any]:
        service_calls.append("create_account")
        assert kwargs["provider"] == "slack"
        return {"account": {"account_ref": "vault:cred-slack"}}

    monkeypatch.setattr(routes_studio.auth_module, "validate_csrf", lambda request: None)
    monkeypatch.setattr(routes_studio.auth_module, "enforce_workspace_access", fake_enforce_workspace_access)
    monkeypatch.setattr(routes_studio.auth_module, "workspace_tenant_id", lambda current_user, workspace_id: "tenant-1")
    monkeypatch.setattr(routes_studio.channel_platform_service, "upsert_agent_channel_binding", fake_upsert_agent_channel_binding)
    monkeypatch.setattr(routes_studio.channel_platform_service, "test_agent_channel_binding", fake_test_agent_channel_binding)
    monkeypatch.setattr(routes_studio.channel_platform_service, "set_agent_channel_binding_state", fake_set_agent_channel_binding_state)
    monkeypatch.setattr(routes_studio.channel_platform_service, "create_workspace_channel_account", fake_create_workspace_channel_account)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        account_response = await client.post(
            "/api/studio/channel-accounts",
            json={
                "workspace_id": "ws-1",
                "provider": "slack",
                "label": "Support Slack",
                "credentials": {"bot_token": "xoxb-test"},
                "skip_validation": True,
            },
        )
        create_response = await client.post(
            "/api/studio/agents/dagent-1/channel-bindings",
            json={"workspace_id": "ws-1", "catalog_id": "telegram_bot", "account_ref": "vault:cred-1"},
        )
        test_response = await client.post(
            "/api/studio/agents/dagent-1/channel-bindings/telegram/test",
            json={"workspace_id": "ws-1", "message": "hello", "dry_run": True},
        )
        pause_response = await client.post(
            "/api/studio/agents/dagent-1/channel-bindings/telegram/pause",
            json={"workspace_id": "ws-1"},
        )
        resume_response = await client.post(
            "/api/studio/agents/dagent-1/channel-bindings/telegram/resume",
            json={"workspace_id": "ws-1"},
        )
        revoke_response = await client.post(
            "/api/studio/agents/dagent-1/channel-bindings/telegram/revoke",
            json={"workspace_id": "ws-1"},
        )

    assert account_response.status_code == 200
    assert create_response.status_code == 200
    assert test_response.status_code == 200
    assert pause_response.status_code == 200
    assert resume_response.status_code == 200
    assert revoke_response.status_code == 200
    assert service_calls == ["create_account", "upsert", "test", "pause", "resume", "revoke"]
    assert all(scope == ("ws-1", "owner") for scope in scopes)


@pytest.mark.anyio
async def test_channel_binding_service_error_maps_to_status(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _build_app()
    app.dependency_overrides[routes_studio.get_current_user] = _owner_user
    monkeypatch.setattr(routes_studio.auth_module, "validate_csrf", lambda request: None)
    monkeypatch.setattr(
        routes_studio.auth_module,
        "enforce_workspace_access",
        lambda current_user, workspace_id, minimum_role="viewer": workspace_id,
    )
    monkeypatch.setattr(routes_studio.auth_module, "workspace_tenant_id", lambda current_user, workspace_id: "tenant-1")

    async def fake_upsert_agent_channel_binding(**_kwargs: Any) -> dict[str, Any]:
        raise routes_studio.channel_platform_service.ChannelPlatformError(
            403,
            "Personal channels are Agent Computer/Sage runtime channels.",
        )

    monkeypatch.setattr(routes_studio.channel_platform_service, "upsert_agent_channel_binding", fake_upsert_agent_channel_binding)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/studio/agents/dagent-1/channel-bindings",
            json={"workspace_id": "ws-1", "catalog_id": "telegram_personal"},
        )

    assert response.status_code == 403
    assert "Agent Computer" in response.text
