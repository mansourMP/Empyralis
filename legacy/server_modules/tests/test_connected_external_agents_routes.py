from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from server_modules import routes_studio


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(routes_studio.router, prefix="/api")
    return app


def _fake_scoped_connection(connection):
    @asynccontextmanager
    async def _scoped_connection(**_kwargs):
        yield connection

    return _scoped_connection


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


def _member_user() -> dict[str, object]:
    return {
        **_owner_user(),
        "workspace_access": {
            "ws-1": {
                **_owner_user()["workspace_access"]["ws-1"],  # type: ignore[index]
                "role": "member",
                "tenant_role": "member",
            }
        },
    }


@pytest.mark.anyio
async def test_agent_surfaces_combines_native_external_and_computers(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _build_app()
    app.dependency_overrides[routes_studio.get_current_user] = _owner_user

    monkeypatch.setattr(
        routes_studio.auth_module,
        "enforce_workspace_access",
        lambda current_user, workspace_id, minimum_role="viewer": workspace_id,
    )
    monkeypatch.setattr(routes_studio.auth_module, "workspace_tenant_id", lambda current_user, workspace_id: "tenant-1")

    async def fake_list_deployed_agents(**_kwargs):
        return {"items": [{"id": "dagent-1", "name": "Native agent", "deployment_state": "draft"}]}

    async def fake_list_runtime_attachments(**_kwargs):
        return {"items": [{"id": "computer-1", "label": "Mansur MacBook", "status": "online"}]}

    async def fake_build_agent_surfaces_payload(**kwargs):
        assert kwargs["workspace_id"] == "ws-1"
        assert kwargs["native_agents"][0]["id"] == "dagent-1"
        assert kwargs["agent_computers"][0]["id"] == "computer-1"
        return {
            "workspace_id": "ws-1",
            "tenant_id": "tenant-1",
            "native_studio_agents": [{"id": "dagent-1", "surface_kind": "native_studio_agent"}],
            "connected_external_agents": [{"id": "extagent-1", "surface_kind": "connected_external_agent"}],
            "agent_computers": [{"id": "computer-1", "surface_kind": "agent_computer"}],
            "items": [],
        }

    monkeypatch.setattr(routes_studio.deployed_agent_service, "list_deployed_agents", fake_list_deployed_agents)
    monkeypatch.setattr(routes_studio.runtime_attachment_service, "list_workspace_runtime_attachments", fake_list_runtime_attachments)
    monkeypatch.setattr(routes_studio.connected_external_agent_service, "build_agent_surfaces_payload", fake_build_agent_surfaces_payload)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/studio/agent-surfaces", params={"workspace_id": "ws-1"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["connected_external_agents"][0]["id"] == "extagent-1"
    assert payload["agent_computers"][0]["surface_kind"] == "agent_computer"


@pytest.mark.anyio
async def test_agent_surfaces_local_mode_without_database_returns_empty_external_surfaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _build_app()
    app.dependency_overrides[routes_studio.get_current_user] = _owner_user

    monkeypatch.setenv("ORION_ENV", "local")
    monkeypatch.delenv("EMPYRALIS_DEPLOY_ENV", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("NODE_ENV", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("EMPYRALIS_EXTERNAL_AGENT_LOCAL_STORE", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    monkeypatch.setattr(
        routes_studio.auth_module,
        "enforce_workspace_access",
        lambda current_user, workspace_id, minimum_role="viewer": workspace_id,
    )
    monkeypatch.setattr(routes_studio.auth_module, "workspace_tenant_id", lambda current_user, workspace_id: "tenant-1")
    monkeypatch.setattr(
        "server_modules.control_plane_repository._scoped_connection",
        _fake_scoped_connection(None),
    )

    async def fake_list_deployed_agents(**_kwargs):
        return {"items": []}

    async def fake_list_runtime_attachments(**_kwargs):
        return {"items": []}

    monkeypatch.setattr(routes_studio.deployed_agent_service, "list_deployed_agents", fake_list_deployed_agents)
    monkeypatch.setattr(routes_studio.runtime_attachment_service, "list_workspace_runtime_attachments", fake_list_runtime_attachments)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/studio/agent-surfaces", params={"workspace_id": "ws-1"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["connected_external_agents"] == []
    assert payload["agent_computers"] == []
    assert payload["items"] == []


@pytest.mark.anyio
async def test_external_agent_create_requires_csrf(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _build_app()
    app.dependency_overrides[routes_studio.get_current_user] = _owner_user

    def reject_csrf(_request):
        raise HTTPException(status_code=403, detail="CSRF validation failed.")

    monkeypatch.setattr(routes_studio.auth_module, "validate_csrf", reject_csrf)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/studio/external-agents",
            json={"workspace_id": "ws-1", "name": "OpenClaw", "provider_kind": "openclaw"},
        )

    assert response.status_code == 403
    assert "csrf" in response.text.lower()


@pytest.mark.anyio
async def test_external_agent_mutations_use_owner_scope_and_service(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _build_app()
    app.dependency_overrides[routes_studio.get_current_user] = _owner_user

    calls: list[tuple[str, str]] = []

    def fake_enforce_workspace_access(current_user, workspace_id, minimum_role="viewer"):
        calls.append((workspace_id, minimum_role))
        return workspace_id

    async def fake_create_connected_external_agent(**kwargs):
        return {"id": "extagent-1", "workspace_id": kwargs["workspace_id"], "provider_kind": kwargs["provider_kind"]}

    async def fake_update_connected_external_agent(**kwargs):
        return {"id": kwargs["external_agent_id"], "name": kwargs["name"]}

    async def fake_refresh_connected_external_agent_manifest(**kwargs):
        return {"id": kwargs["external_agent_id"], "connection_state": "verified"}

    async def fake_disconnect_connected_external_agent(**kwargs):
        return {"id": kwargs["external_agent_id"], "connection_state": "revoked"}

    monkeypatch.setattr(routes_studio.auth_module, "validate_csrf", lambda request: None)
    monkeypatch.setattr(routes_studio.auth_module, "enforce_workspace_access", fake_enforce_workspace_access)
    monkeypatch.setattr(routes_studio.auth_module, "workspace_tenant_id", lambda current_user, workspace_id: "tenant-1")
    monkeypatch.setattr(routes_studio.connected_external_agent_service, "create_connected_external_agent", fake_create_connected_external_agent)
    monkeypatch.setattr(routes_studio.connected_external_agent_service, "update_connected_external_agent", fake_update_connected_external_agent)
    monkeypatch.setattr(routes_studio.connected_external_agent_service, "refresh_connected_external_agent_manifest", fake_refresh_connected_external_agent_manifest)
    monkeypatch.setattr(routes_studio.connected_external_agent_service, "disconnect_connected_external_agent", fake_disconnect_connected_external_agent)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        create = await client.post(
            "/api/studio/external-agents",
            json={"workspace_id": "ws-1", "name": "Hermes", "provider_kind": "hermes"},
        )
        update = await client.patch(
            "/api/studio/external-agents/extagent-1",
            json={"workspace_id": "ws-1", "name": "Hermes updated"},
        )
        refresh = await client.post(
            "/api/studio/external-agents/extagent-1/refresh-manifest",
            json={"workspace_id": "ws-1"},
        )
        disconnect = await client.post(
            "/api/studio/external-agents/extagent-1/disconnect",
            json={"workspace_id": "ws-1"},
        )

    assert create.status_code == 200
    assert update.json()["name"] == "Hermes updated"
    assert refresh.json()["connection_state"] == "verified"
    assert disconnect.json()["connection_state"] == "revoked"
    assert calls == [("ws-1", "owner"), ("ws-1", "owner"), ("ws-1", "owner"), ("ws-1", "owner")]


@pytest.mark.anyio
async def test_external_agent_chat_requires_owner_scope_and_uses_proxy_service(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _build_app()
    app.dependency_overrides[routes_studio.get_current_user] = _owner_user
    captured: dict[str, object] = {}

    def fake_enforce_workspace_access(current_user, workspace_id, minimum_role="viewer"):
        captured["minimum_role"] = minimum_role
        return workspace_id

    async def fake_chat_with_connected_external_agent(**kwargs):
        captured.update(kwargs)
        return {"reply": "hello from external", "run_details": {"surface_kind": "connected_external_agent"}}

    monkeypatch.setattr(routes_studio.auth_module, "validate_csrf", lambda request: None)
    monkeypatch.setattr(routes_studio.auth_module, "enforce_workspace_access", fake_enforce_workspace_access)
    monkeypatch.setattr(routes_studio.auth_module, "workspace_tenant_id", lambda current_user, workspace_id: "tenant-1")
    monkeypatch.setattr(routes_studio.connected_external_agent_service, "chat_with_connected_external_agent", fake_chat_with_connected_external_agent)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/studio/external-agents/extagent-1/chat-turn",
            json={"workspace_id": "ws-1", "message": "hello", "recent_messages": []},
        )

    assert response.status_code == 200
    assert response.json()["reply"] == "hello from external"
    assert captured["minimum_role"] == "owner"
    assert captured["external_agent_id"] == "extagent-1"


@pytest.mark.anyio
async def test_external_agent_section_data_requires_owner_scope_and_uses_proxy_service(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _build_app()
    app.dependency_overrides[routes_studio.get_current_user] = _owner_user
    captured: dict[str, object] = {}

    def fake_enforce_workspace_access(current_user, workspace_id, minimum_role="viewer"):
        captured["minimum_role"] = minimum_role
        return workspace_id

    async def fake_get_connected_external_agent_section_data(**kwargs):
        captured.update(kwargs)
        return {
            "external_agent_id": kwargs["external_agent_id"],
            "section": {"id": kwargs["section_id"], "title": "Agent Logs"},
            "display_kind": "logs",
            "items": [],
        }

    monkeypatch.setattr(routes_studio.auth_module, "enforce_workspace_access", fake_enforce_workspace_access)
    monkeypatch.setattr(routes_studio.auth_module, "workspace_tenant_id", lambda current_user, workspace_id: "tenant-1")
    monkeypatch.setattr(routes_studio.connected_external_agent_service, "get_connected_external_agent_section_data", fake_get_connected_external_agent_section_data)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/studio/external-agents/extagent-1/sections/agent_logs",
            params={"workspace_id": "ws-1"},
        )

    assert response.status_code == 200
    assert response.json()["section"]["id"] == "agent_logs"
    assert captured["minimum_role"] == "owner"
    assert captured["external_agent_id"] == "extagent-1"
    assert captured["section_id"] == "agent_logs"
