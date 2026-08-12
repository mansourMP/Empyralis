"""GET /api/w/{workspace_id}/fleet/agents/suggested-name -- the create-agent
wizard's Placement step Name field pre-fill (item 5 of the chat-first UX
review: the wizard used to never ask for a name at all). Same auth-guard
pattern as test_routes_fleet_bug_reports.py: real ASGI requests through the
REAL auth_module.enforce_workspace_access dependency.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI

from server_modules import routes_fleet


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(routes_fleet.router)
    return app


def _viewer_user() -> dict:
    return {
        "user_id": "viewer-1",
        "email": "viewer@example.com",
        "auth_type": "bearer",
        "workspace_access": {
            "ws-1": {
                "workspace_id": "ws-1",
                "tenant_id": "tenant-1",
                "role": "viewer",
                "tenant_role": "viewer",
            }
        },
    }


def _owner_user() -> dict:
    return {
        "user_id": "owner-1",
        "email": "owner@example.com",
        "auth_type": "bearer",
        "workspace_access": {
            "ws-1": {
                "workspace_id": "ws-1",
                "tenant_id": "tenant-1",
                "role": "owner",
                "tenant_role": "owner",
            }
        },
    }


def _intruder_user() -> dict:
    return {
        "user_id": "intruder-1",
        "email": "intruder@example.com",
        "auth_type": "bearer",
        "workspace_access": {
            "ws-intruder-home": {
                "workspace_id": "ws-intruder-home",
                "tenant_id": "tenant-intruder",
                "role": "owner",
                "tenant_role": "owner",
            }
        },
    }


@pytest.mark.anyio
async def test_unauthenticated_request_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORION_AUTH_REQUIRED", "1")
    monkeypatch.delenv("EMPYRALIS_DEPLOY_ENV", raising=False)
    monkeypatch.delenv("ORION_ENV", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("NODE_ENV", raising=False)

    app = _build_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/w/ws-victim/fleet/agents/suggested-name")
    assert response.status_code == 401


@pytest.mark.anyio
async def test_wrong_workspace_request_is_rejected() -> None:
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _intruder_user

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/w/ws-1/fleet/agents/suggested-name")
    assert response.status_code == 403


@pytest.mark.anyio
async def test_viewer_cannot_fetch_a_suggested_name() -> None:
    """Same minimum_role="owner" bar as the create route itself
    (fleet_create_agent_route) -- a pre-fill for a control a viewer can't
    use anyway must not be reachable at a looser role."""
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _viewer_user

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/w/ws-1/fleet/agents/suggested-name")
    assert response.status_code == 403


@pytest.mark.anyio
async def test_owner_gets_a_real_suggested_name() -> None:
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _owner_user

    with (
        patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
        patch(
            "server_modules.fleet_tools.suggest_agent_name",
            new=AsyncMock(return_value="Atlas"),
        ) as suggest_mock,
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/w/ws-1/fleet/agents/suggested-name")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["name"] == "Atlas"
    suggest_mock.assert_awaited_once_with(tenant_id="tenant-1", workspace_id="ws-1")


@pytest.mark.anyio
async def test_service_failure_is_surfaced_as_ok_false_not_500() -> None:
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _owner_user

    with (
        patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
        patch(
            "server_modules.fleet_tools.suggest_agent_name",
            new=AsyncMock(side_effect=RuntimeError("db unavailable")),
        ),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/w/ws-1/fleet/agents/suggested-name")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "db unavailable" in body["error"]
