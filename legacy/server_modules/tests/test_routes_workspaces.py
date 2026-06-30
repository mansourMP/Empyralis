from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi import HTTPException

from server_modules import routes_workspaces


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(routes_workspaces.router, prefix="/api")
    return app


@pytest.mark.anyio
async def test_workspace_bootstrap_route_returns_payload(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_workspaces.get_current_user] = lambda: {
        "auth_type": "bearer",
        "user_id": "user-1",
    }

    async def fake_build_workspace_bootstrap(*, current_user, workspace_id: str):
        assert current_user["user_id"] == "user-1"
        assert workspace_id == "ws-1"
        return {
            "account": {"id": "user-1", "email": "user@example.com"},
            "workspace": {"id": "ws-1", "tenantId": "tenant-1", "label": "Workspace", "kind": "personal"},
            "membership": {"role": "member", "permissions": ["workspace.read"], "version": "1:1"},
            "capabilities": {"mobile_app_enabled": True},
            "entitlements": {"plan": "personal", "flags": {}, "limits": {}},
            "workspaceTraits": {"operatingMode": "personal", "defaultSurface": "chat"},
            "runtime": {"deploymentMode": "cloud_default", "runtimeTargets": []},
            "shellHints": {"defaultRoute": "/w/ws-1/chat", "preferredProfile": "personal_shell"},
        }

    monkeypatch.setattr(routes_workspaces, "build_workspace_bootstrap", fake_build_workspace_bootstrap)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/workspaces/ws-1/bootstrap")

    assert response.status_code == 200
    assert response.json()["workspace"]["id"] == "ws-1"


@pytest.mark.anyio
async def test_workspace_channel_operations_route_returns_payload(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_workspaces.get_current_user] = lambda: {
        "auth_type": "bearer",
        "user_id": "user-1",
        "role": "admin",
    }

    async def fake_build_workspace_channel_operations(*, current_user, workspace_id: str):
        assert current_user["user_id"] == "user-1"
        assert workspace_id == "ws-1"
        return {
            "ok": True,
            "workspace_id": "ws-1",
            "channels": {
                "telegram": {"workspace_status": "live"},
                "whatsapp": {"workspace_status": "setup_needed"},
            },
            "delivery": {"workspace_summary": {"pending_count": 1}},
            "events": {"recent": [], "pairing_failures": []},
        }

    monkeypatch.setattr(
        routes_workspaces,
        "build_workspace_channel_operations",
        fake_build_workspace_channel_operations,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/workspaces/ws-1/channel-operations")

    assert response.status_code == 200
    assert response.json()["delivery"]["workspace_summary"]["pending_count"] == 1


@pytest.mark.anyio
async def test_workspace_channel_operations_route_rejects_unauthorized_workspace(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_workspaces.get_current_user] = lambda: {
        "auth_type": "bearer",
        "user_id": "user-1",
        "role": "member",
    }

    async def fake_build_workspace_channel_operations(*, current_user, workspace_id: str):
        raise HTTPException(status_code=403, detail=f"Admin role required for workspace '{workspace_id}'.")

    monkeypatch.setattr(
        routes_workspaces,
        "build_workspace_channel_operations",
        fake_build_workspace_channel_operations,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/workspaces/ws-2/channel-operations")

    assert response.status_code == 403
    assert response.json()["detail"] == "Admin role required for workspace 'ws-2'."


@pytest.mark.anyio
async def test_workspace_bootstrap_route_rejects_unauthorized_workspace():
    app = _build_app()
    app.dependency_overrides[routes_workspaces.get_current_user] = lambda: {
        "auth_type": "bearer",
        "user_id": "user-1",
        "email": "user@example.com",
        "role": "member",
        "is_admin": False,
        "identity_versions": {"membership_version": 3},
        "workspace_access": {
            "ws-1": {
                "workspace_id": "ws-1",
                "tenant_id": "tenant-1",
                "tenant_role": "member",
                "role": "member",
                "capabilities": {"allow": [], "deny": []},
                "tenant_capabilities": {"allow": [], "deny": []},
                "workspace_capabilities": {"allow": [], "deny": []},
                "dangerous_action_classes": {"allow": [], "deny": []},
                "tenant_dangerous_action_classes": {"allow": [], "deny": []},
                "workspace_dangerous_action_classes": {"allow": [], "deny": []},
                "connectors": {"allow": [], "deny": []},
                "tenant_connectors": {"allow": [], "deny": []},
                "workspace_connectors": {"allow": [], "deny": []},
                "machine_enrollment_scope": "workspace",
                "trusted_owner_machine_ids": [],
                "owner_user_id": "user-1",
                "owner_email": "user@example.com",
            }
        },
    }

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/workspaces/ws-2/bootstrap")

    assert response.status_code == 403
    assert response.json()["detail"] == "Workspace is not accessible for this user."
