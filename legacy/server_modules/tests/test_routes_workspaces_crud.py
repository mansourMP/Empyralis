from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from server_modules import routes_workspaces


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(routes_workspaces.router)
    return app


def _workspace_record(
    *,
    workspace_id: str,
    tenant_id: str,
    name: str,
    workspace_type: str,
    preferred_profile: str,
    default_route: str,
    setup_completed: bool,
):
    return {
        "workspace_id": workspace_id,
        "tenant_id": tenant_id,
        "name": name,
        "workspace_type": workspace_type,
        "metadata": {
            "shell": {
                "preferredProfile": preferred_profile,
                "defaultRoute": default_route,
                "setupCompleted": setup_completed,
            }
        },
    }


@pytest.mark.anyio
async def test_list_workspaces_returns_authenticated_workspace_summaries(
    monkeypatch: pytest.MonkeyPatch,
):
    app = _build_app()
    app.dependency_overrides[routes_workspaces.get_current_user] = lambda: {
        "auth_type": "bearer",
        "user_id": "user-1",
    }

    monkeypatch.setattr(
        routes_workspaces.auth_module,
        "get_authenticated_user_record",
        lambda current_user: {"id": "user-1"},
    )
    monkeypatch.setattr(
        routes_workspaces.auth_module,
        "list_authenticated_workspace_memberships",
        lambda current_user: [
            {"workspace_id": "ws-1", "role": "owner"},
            {"workspace_id": "ws-2", "role": "member"},
        ],
    )

    async def fake_list_workspaces_for_user(user_id: str):
        assert user_id == "user-1"
        return [
            _workspace_record(
                workspace_id="ws-1",
                tenant_id="tenant-1",
                name="Primary Workspace",
                workspace_type="personal",
                preferred_profile="personal_shell",
                default_route="/w/ws-1/chat",
                setup_completed=True,
            ),
            _workspace_record(
                workspace_id="ws-2",
                tenant_id="tenant-2",
                name="Deal Team",
                workspace_type="team",
                preferred_profile="operations_admin_shell",
                default_route="/w/ws-2/admin",
                setup_completed=True,
            ),
        ]

    monkeypatch.setattr(
        routes_workspaces.control_plane_repository,
        "list_workspaces_for_user",
        fake_list_workspaces_for_user,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/workspaces")

    assert response.status_code == 200
    payload = response.json()
    assert [item["workspace"]["id"] for item in payload["items"]] == ["ws-1", "ws-2"]
    assert payload["items"][0]["role"] == "owner"
    assert payload["items"][1]["workspace"]["kind"] == "team"


@pytest.mark.anyio
async def test_create_workspace_persists_shell_profile_and_default_route(
    monkeypatch: pytest.MonkeyPatch,
):
    app = _build_app()
    app.dependency_overrides[routes_workspaces.get_current_user] = lambda: {
        "auth_type": "bearer",
        "user_id": "user-1",
    }

    monkeypatch.setattr(
        routes_workspaces.auth_module,
        "get_authenticated_user_record",
        lambda current_user: {"id": "user-1"},
    )

    async def fake_create_workspace_for_user(**kwargs):
        assert kwargs["user_id"] == "user-1"
        assert kwargs["tenant_id"] is None
        assert kwargs["workspace_type"] == "professional"
        assert kwargs["preferred_shell_profile"] == "document_workstation_shell"
        assert kwargs["default_route"] == "/workstation"
        return _workspace_record(
            workspace_id="ws-created",
            tenant_id="tenant-created",
            name=kwargs["name"],
            workspace_type=kwargs["workspace_type"],
            preferred_profile="document_workstation_shell",
            default_route="/w/ws-created/workstation",
            setup_completed=True,
        )

    monkeypatch.setattr(
        routes_workspaces.control_plane_repository,
        "create_workspace_for_user",
        fake_create_workspace_for_user,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/workspaces",
            json={
                "name": "Advisor Workspace",
                "workspace_type": "professional",
                "preferred_shell_profile": "document_workstation_shell",
                "default_route": "/workstation",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["workspace"]["id"] == "ws-created"
    assert payload["preferredShellProfileId"] == "document_workstation_shell"
    assert payload["defaultRoute"] == "/w/ws-created/workstation"
    assert payload["setupCompleted"] is True


@pytest.mark.anyio
async def test_update_workspace_requires_owner_scope_and_returns_updated_summary(
    monkeypatch: pytest.MonkeyPatch,
):
    app = _build_app()
    app.dependency_overrides[routes_workspaces.get_current_user] = lambda: {
        "auth_type": "bearer",
        "user_id": "user-1",
    }

    def fake_enforce_workspace_access(current_user, workspace_id, minimum_role="viewer"):
        assert workspace_id == "ws-1"
        assert minimum_role == "owner"
        return "ws-1"

    monkeypatch.setattr(
        routes_workspaces.auth_module,
        "enforce_workspace_access",
        fake_enforce_workspace_access,
    )

    async def fake_update_workspace_profile(workspace_id: str, updates):
        assert workspace_id == "ws-1"
        assert updates["name"] == "Renamed Workspace"
        assert updates["workspace_type"] == "team"
        assert updates["preferred_shell_profile"] == "operations_admin_shell"
        assert updates["default_route"] == "/admin"
        assert updates["setup_completed"] is True
        return _workspace_record(
            workspace_id="ws-1",
            tenant_id="tenant-1",
            name="Renamed Workspace",
            workspace_type="team",
            preferred_profile="operations_admin_shell",
            default_route="/w/ws-1/admin",
            setup_completed=True,
        )

    monkeypatch.setattr(
        routes_workspaces.control_plane_repository,
        "update_workspace_profile",
        fake_update_workspace_profile,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.patch(
            "/workspaces/ws-1",
            json={
                "name": "Renamed Workspace",
                "workspace_type": "team",
                "preferred_shell_profile": "operations_admin_shell",
                "default_route": "/admin",
                "setup_completed": True,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["workspace"]["label"] == "Renamed Workspace"
    assert payload["workspace"]["kind"] == "team"
    assert payload["defaultRoute"] == "/w/ws-1/admin"
