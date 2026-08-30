"""MAN-149 operator route: GET /internal/platform-activation.

CLAUDE.md is explicit that `require_api_key` answers only "is someone
logged in", never "may this caller see every tenant's numbers" -- so this
cross-tenant snapshot must gate on `has_platform_fleet_operator_access`
(the documented gate for exactly this shape of view, matching routes_
health.py's own `scope_internal_health_payload`) and refuse everyone else
outright, since there is no honest narrowed version of "how many users
across the whole platform have ever done anything".

These tests exercise the route over a real ASGI request (httpx against the
actual router), the same approach test_routes_fleet_auth_guard.py uses --
proving the dependency wiring itself refuses the request, not just that a
unit test believes it would.
"""

from __future__ import annotations

import importlib
import os
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI


def _routes_health_module():
    return importlib.import_module("server_modules.routes_health")


def _build_app(routes_health_module) -> FastAPI:
    app = FastAPI()
    app.include_router(routes_health_module.router)
    return app


SNAPSHOT = {
    "generated_at": "2026-08-30T00:00:00+00:00",
    "rls_bypass_verified": True,
    "totals": {
        "users": 105,
        "workspaces": 105,
        "agents": 68,
        "projects": 166,
        "tasks": 36,
        "documents": 5,
    },
    "activation": {
        "users_with_activity": 4,
        "users_with_activity_pct": 3.8,
        "workspaces_with_multiple_members": 0,
        "workspaces_with_multiple_members_pct": 0.0,
    },
    "signups": {"last_7_days": 2, "last_30_days": 9},
    "agents_runtime": {"total": 68, "with_runs": 12, "never_run": 56},
}


def test_the_route_is_registered_on_the_already_mounted_health_router() -> None:
    """server.py mounts health_router twice already (bare and under /api,
    see server.py:383/388) -- this only has to prove the path exists on
    that router, not re-wire a mount. 'built and never wired' is this
    codebase's most common defect; this is the grep-shaped guard against it
    living only in a route function nobody registered."""
    routes_health_module = _routes_health_module()
    paths = {getattr(route, "path", "") for route in routes_health_module.router.routes}
    assert "/internal/platform-activation" in paths


@pytest.mark.anyio
async def test_an_unauthenticated_request_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORION_AUTH_REQUIRED", "1")
    monkeypatch.delenv("EMPYRALIS_DEPLOY_ENV", raising=False)
    monkeypatch.delenv("ORION_ENV", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("NODE_ENV", raising=False)
    monkeypatch.delenv("ORION_API_KEY", raising=False)

    routes_health_module = _routes_health_module()
    app = _build_app(routes_health_module)
    # No dependency_overrides -- exercises the real auth dependency chain.

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/internal/platform-activation")

    assert response.status_code == 401


@pytest.mark.anyio
async def test_an_authenticated_non_operator_is_refused_before_any_query_runs() -> None:
    """A real, logged-in customer -- just not an operator. require_api_key
    alone must never be treated as authorization for a cross-tenant view."""
    routes_health_module = _routes_health_module()
    app = _build_app(routes_health_module)

    def _plain_member() -> dict:
        return {
            "user_id": "member-1",
            "email": "member@example.com",
            "auth_type": "bearer",
            "role": "member",
        }

    app.dependency_overrides[routes_health_module.require_api_key] = _plain_member

    with patch.object(
        routes_health_module.platform_activation_service,
        "build_platform_activation_snapshot",
        new=AsyncMock(side_effect=AssertionError("must never be reached for a non-operator caller")),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/internal/platform-activation")

    assert response.status_code == 403


@pytest.mark.anyio
async def test_an_operator_caller_receives_the_snapshot() -> None:
    routes_health_module = _routes_health_module()
    app = _build_app(routes_health_module)

    def _operator() -> dict:
        # auth_type == "api_key" is the platform's own service-key branch of
        # has_platform_fleet_operator_access -- see auth.py's docstring.
        return {"user_id": "svc", "auth_type": "api_key"}

    app.dependency_overrides[routes_health_module.require_api_key] = _operator

    with (
        patch.object(
            routes_health_module.control_plane_repository,
            "ensure_control_plane_schema",
            new=AsyncMock(return_value=object()),
        ),
        patch.object(
            routes_health_module.platform_activation_service,
            "build_platform_activation_snapshot",
            new=AsyncMock(return_value=SNAPSHOT),
        ) as mock_snapshot,
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/internal/platform-activation")

    assert response.status_code == 200
    assert response.json() == SNAPSHOT
    mock_snapshot.assert_awaited_once()


@pytest.mark.anyio
async def test_a_missing_control_plane_pool_returns_503_never_a_zeroed_payload() -> None:
    """'Empty' and 'could not load' are different facts (CLAUDE.md) -- a
    pool that failed to come up must never render as a platform with 0
    users."""
    routes_health_module = _routes_health_module()
    app = _build_app(routes_health_module)

    def _operator() -> dict:
        return {"user_id": "svc", "auth_type": "api_key"}

    app.dependency_overrides[routes_health_module.require_api_key] = _operator

    with patch.object(
        routes_health_module.control_plane_repository,
        "ensure_control_plane_schema",
        new=AsyncMock(return_value=None),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/internal/platform-activation")

    assert response.status_code == 503
    body = response.json()
    assert body != {"totals": {"users": 0}}
    assert "users" not in str(body.get("detail", "")).lower() or "0" not in str(body.get("detail", ""))


@pytest.mark.anyio
async def test_a_broken_rls_scope_surfaces_as_a_server_error_not_a_silent_zero() -> None:
    routes_health_module = _routes_health_module()
    app = _build_app(routes_health_module)

    def _operator() -> dict:
        return {"user_id": "svc", "auth_type": "api_key"}

    app.dependency_overrides[routes_health_module.require_api_key] = _operator

    with (
        patch.object(
            routes_health_module.control_plane_repository,
            "ensure_control_plane_schema",
            new=AsyncMock(return_value=object()),
        ),
        patch.object(
            routes_health_module.platform_activation_service,
            "build_platform_activation_snapshot",
            new=AsyncMock(
                side_effect=routes_health_module.platform_activation_service.PlatformActivationScopeBroken(
                    "app.rls_bypass was not 'on'"
                )
            ),
        ),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/internal/platform-activation")

    assert response.status_code == 500
