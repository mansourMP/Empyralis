"""POST/GET /api/w/{workspace_id}/fleet/bug-reports -- the "report a bug"
rail control's backing routes (MAN-106).

Mirrors the two established patterns in this test suite:
  - test_routes_fleet_auth_guard.py: real ASGI requests through the REAL
    auth_module.enforce_workspace_access (not mocked), proving the
    dependency wiring itself rejects an unauthenticated or wrong-workspace
    caller.
  - test_routes_fleet_delete_agent.py: real ASGI requests with a genuine
    workspace member whose ROLE is below what a route requires.
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
    """A genuine ws-1 member with the lowest RBAC role -- the exact case the
    POST route must still allow through (viewer minimum): reporting a bug
    you ran into isn't a privileged action."""
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
    """Authenticated, but the only membership is a different workspace --
    the cross-tenant shape both routes must reject regardless of role."""
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
async def test_unauthenticated_post_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORION_AUTH_REQUIRED", "1")
    monkeypatch.delenv("EMPYRALIS_DEPLOY_ENV", raising=False)
    monkeypatch.delenv("ORION_ENV", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("NODE_ENV", raising=False)

    app = _build_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/w/ws-victim/fleet/bug-reports", json={"title": "Something broke"}
        )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_wrong_workspace_post_is_rejected() -> None:
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _intruder_user

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/w/ws-1/fleet/bug-reports", json={"title": "Something broke"}
        )
    assert response.status_code == 403


@pytest.mark.anyio
async def test_wrong_workspace_get_is_rejected() -> None:
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _intruder_user

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/w/ws-1/fleet/bug-reports")
    assert response.status_code == 403


@pytest.mark.anyio
async def test_viewer_can_submit_a_report() -> None:
    """The lowest RBAC role must still be able to file a report -- the whole
    point of the entry point is that any workspace member who hits a bug can
    use it, not just owners."""
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _viewer_user

    with (
        patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
        patch(
            "server_modules.bug_report_service.create_report",
            new=AsyncMock(return_value={"id": "bugreport-1", "title": "Something broke"}),
        ) as create_mock,
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/w/ws-1/fleet/bug-reports",
                json={"title": "Something broke", "description": "It broke.", "page_path": "/w/ws-1/agents"},
                headers={"user-agent": "TestClient/1.0"},
            )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["report"]["id"] == "bugreport-1"
    create_mock.assert_awaited_once()
    kwargs = create_mock.await_args.kwargs
    assert kwargs["tenant_id"] == "tenant-1"
    assert kwargs["workspace_id"] == "ws-1"
    assert kwargs["title"] == "Something broke"
    assert kwargs["description"] == "It broke."
    assert kwargs["page_path"] == "/w/ws-1/agents"
    assert kwargs["reported_by_user_id"] == "viewer-1"
    assert kwargs["user_agent"] == "TestClient/1.0"


@pytest.mark.anyio
async def test_blank_title_is_rejected_by_validation() -> None:
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _viewer_user

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/w/ws-1/fleet/bug-reports", json={"title": ""})
    assert response.status_code == 422


@pytest.mark.anyio
async def test_service_failure_is_surfaced_as_ok_false_not_500() -> None:
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _viewer_user

    with (
        patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
        patch(
            "server_modules.bug_report_service.create_report",
            new=AsyncMock(side_effect=RuntimeError("db unavailable")),
        ),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/w/ws-1/fleet/bug-reports", json={"title": "Something broke"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "db unavailable" in body["error"]


@pytest.mark.anyio
async def test_viewer_cannot_list_reports() -> None:
    """Reading the roster back is owner-gated, matching every other
    workspace-wide operational read in this router."""
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _viewer_user

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/w/ws-1/fleet/bug-reports")
    assert response.status_code == 403


@pytest.mark.anyio
async def test_owner_can_list_reports() -> None:
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _owner_user

    with (
        patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
        patch(
            "server_modules.bug_report_service.list_reports",
            new=AsyncMock(return_value=[{"id": "bugreport-1", "title": "Something broke"}]),
        ) as list_mock,
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/w/ws-1/fleet/bug-reports")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["reports"][0]["id"] == "bugreport-1"
    list_mock.assert_awaited_once()
    assert list_mock.await_args.kwargs["tenant_id"] == "tenant-1"
    assert list_mock.await_args.kwargs["workspace_id"] == "ws-1"
