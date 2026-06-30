from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from server_modules import control_plane_repository
from server_modules import routes_workspaces
from server_modules import workspace_bootstrap_service


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(routes_workspaces.router)
    return app


def test_repository_normalizes_relative_workspace_route() -> None:
    assert (
        control_plane_repository._normalize_workspace_default_route("ws-home", "/admin")
        == "/w/ws-home/admin"
    )


def test_repository_rejects_cross_workspace_default_route() -> None:
    assert (
        control_plane_repository._normalize_workspace_default_route("ws-home", "/w/ws-other/admin")
        == "/w/ws-home/chat"
    )


def test_bootstrap_discards_cross_workspace_shell_route() -> None:
    assert workspace_bootstrap_service._normalize_shell_default_route("ws-home", "/w/ws-other/admin") == ""


@pytest.mark.anyio
async def test_create_workspace_rejects_cross_workspace_absolute_default_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    create_called = False

    async def fake_create_workspace_for_user(**kwargs):
        nonlocal create_called
        create_called = True
        return None

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
                "name": "Unsafe Workspace",
                "workspace_type": "professional",
                "preferred_shell_profile": "document_workstation_shell",
                "default_route": "/w/ws-other/admin",
                "tenant_id": "tenant-attacker",
            },
        )

    assert response.status_code == 400
    assert create_called is False


@pytest.mark.anyio
async def test_update_workspace_rejects_cross_workspace_default_route_before_persisting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _build_app()
    app.dependency_overrides[routes_workspaces.get_current_user] = lambda: {
        "auth_type": "bearer",
        "user_id": "user-1",
    }
    monkeypatch.setattr(
        routes_workspaces.auth_module,
        "enforce_workspace_access",
        lambda current_user, workspace_id, minimum_role="viewer": "ws-home",
    )

    update_called = False

    async def fake_update_workspace_profile(workspace_id: str, updates):
        nonlocal update_called
        update_called = True
        return None

    monkeypatch.setattr(
        routes_workspaces.control_plane_repository,
        "update_workspace_profile",
        fake_update_workspace_profile,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.patch(
            "/workspaces/ws-home",
            json={"default_route": "/w/ws-other/admin"},
        )

    assert response.status_code == 400
    assert update_called is False
