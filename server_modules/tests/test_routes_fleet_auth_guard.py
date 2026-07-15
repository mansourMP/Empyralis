"""Regression coverage for the routes_fleet.py cross-tenant authorization fix.

Before this fix, only the owner-gated stop/resume/schedule routes carried
`Depends(auth_module.get_current_user)` + `auth_module.enforce_workspace_access(...)`.
Every other route in routes_fleet.py -- including ones that create agents,
rewrite an agent's model/BYOK config, read or write an agent's memory tree,
and store BYO Telegram/Discord bot tokens -- had NO auth dependency at all.
Since there is no global auth middleware in this codebase (confirmed: zero
BaseHTTPMiddleware in server_modules), any caller who could reach the
FastAPI process directly (bypassing the authenticated Next.js frontend
proxy) could pass an arbitrary workspace_id in the URL and read or mutate
ANY workspace's agents.

These tests exercise the routes over a real ASGI request (not a direct
Python call) to prove the dependency wiring itself -- not just the
handler body -- now rejects the request, mirroring the approach in
test_connected_external_agents_routes.py.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from server_modules import routes_fleet


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(routes_fleet.router)
    return app


def _intruder_user() -> dict:
    """A real, authenticated user -- but one whose only membership is a
    DIFFERENT workspace than the one being requested. This is the exact
    cross-tenant shape the vulnerability allowed: any authenticated (or,
    before this fix, even unauthenticated) caller could put someone else's
    workspace_id in the URL and reach their agents."""
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
async def test_unauthenticated_request_to_formerly_open_write_route_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fleet_configure_agent_route (PATCH .../fleet/agents/{agent_id}) used to
    have no Depends(get_current_user) at all -- any request, session or not,
    reached fleet_configure_agent and could rewrite ANY agent's config
    (model/BYOK credentials, enabled_tools, channel_bindings). A request with
    no session cookie, Authorization header, or X-API-Key must now get a real
    401 from FastAPI's own dependency resolution, before the handler body
    (or enforce_workspace_access) ever runs."""
    monkeypatch.setenv("ORION_AUTH_REQUIRED", "1")
    monkeypatch.delenv("EMPYRALIS_DEPLOY_ENV", raising=False)
    monkeypatch.delenv("ORION_ENV", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("NODE_ENV", raising=False)

    app = _build_app()
    # No dependency_overrides at all -- this exercises the REAL
    # auth_module.get_current_user, not a mock.

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(
            "/api/w/ws-victim/fleet/agents/agent-1",
            json={"patch": {"enabled_tools": ["shell"]}},
        )

    assert response.status_code == 401


@pytest.mark.anyio
async def test_wrong_workspace_request_to_formerly_open_write_route_is_rejected() -> None:
    """Same route, now with a real authenticated session -- just one that
    belongs to a different workspace. Exercises the genuine
    auth_module.enforce_workspace_access (not mocked): proves a valid
    session can no longer reach another tenant's agent just by changing the
    workspace_id path segment."""
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _intruder_user

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(
            "/api/w/ws-victim/fleet/agents/agent-1",
            json={"patch": {"enabled_tools": ["shell"]}},
        )

    assert response.status_code == 403


@pytest.mark.anyio
async def test_wrong_workspace_request_to_formerly_open_read_route_is_rejected() -> None:
    """A formerly-open READ route (fleet_agents) must reject cross-workspace
    reads too -- this hole leaked agent rosters/config, not just mutation."""
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _intruder_user

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/w/ws-victim/fleet/agents")

    assert response.status_code == 403


@pytest.mark.anyio
async def test_wrong_workspace_request_to_bot_token_route_is_rejected() -> None:
    """Top-priority case called out in the fix: fleet_assign_agent_telegram /
    fleet_assign_agent_discord store the user's BYO bot token. A caller
    authenticated into a different workspace must not be able to bind (or
    silently overwrite) another workspace's agent's bot token."""
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _intruder_user

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/w/ws-victim/fleet/agent-channels/telegram",
            params={"agent_id": "agent-1"},
            json={"token": "123456:fake-botfather-token"},
        )

    assert response.status_code == 403
