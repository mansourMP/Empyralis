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

from unittest.mock import AsyncMock, patch

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
    (model/BYOK credentials, connectors, channel_bindings). A request with
    no session cookie, Authorization header, or X-API-Key must now get a real
    401 from FastAPI's own dependency resolution, before the handler body
    (or enforce_workspace_access) ever runs. The exact patch body below is
    incidental -- the auth dependency rejects the request before any patch
    key is ever inspected, so its content doesn't need to be a currently
    valid key."""
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


# ---------------------------------------------------------------------------
# MAN-206: cross-WORKSPACE agent_id (IDOR), as opposed to cross-workspace
# MEMBERSHIP (the tests above). Here the caller genuinely belongs to the
# workspace named in the URL -- enforce_workspace_access passes -- but the
# agent_id query param names an agent that actually belongs to a DIFFERENT
# workspace/tenant. Before the fix, hosted_bot_provisioning_service.
# assign_byo_bot / discord_bot_provisioning_service.assign_agent_discord
# never checked that agent_id resolved inside the caller's own
# (tenant_id, workspace_id) -- they just wrote a vault credential and a
# channel binding stamped with the CALLER's tenant/workspace but the
# ATTACKER-CHOSEN agent_install_id, which Postgres RLS's INSERT WITH CHECK
# does not catch (the new row's own tenant_id/workspace_id is correct).
# ---------------------------------------------------------------------------


def _legit_caller_user() -> dict:
    """A real, authenticated user who genuinely owns ws-caller -- passes
    enforce_workspace_access cleanly. The attack is the agent_id they pass
    in the query string, not their session."""
    return {
        "user_id": "caller-1",
        "email": "caller@example.com",
        "auth_type": "bearer",
        "workspace_access": {
            "ws-caller": {
                "workspace_id": "ws-caller",
                "tenant_id": "tenant-caller",
                "role": "owner",
                "tenant_role": "owner",
            }
        },
    }


@pytest.mark.anyio
async def test_foreign_agent_id_on_telegram_assign_route_is_rejected_even_in_the_callers_own_workspace() -> None:
    """This is the actual MAN-206 shape: a legitimate member of ws-caller
    supplies an agent_id belonging to a different tenant's agent. Must be
    rejected end-to-end (route -> service -> ownership check), and must not
    reach Telegram's API or write anything.

    Fails pre-fix: assign_byo_bot had no ownership gate, so it would call
    get_me() (mocked here to prove it's never reached) and go on to write a
    credential + binding stamped with ws-caller/tenant-caller but pointed at
    someone else's agent -- the route would return {"ok": True, ...} instead
    of the rejection asserted below."""
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _legit_caller_user

    with (
        patch.object(routes_fleet, "_resolve_tenant", new=AsyncMock(return_value="tenant-caller")),
        patch(
            "server_modules.agent_bindings_repository.agent_install_in_scope",
            new=AsyncMock(return_value=False),
        ) as scope_mock,
        patch(
            "server_modules.hosted_bot_provisioning_service.get_me",
            new=AsyncMock(side_effect=AssertionError("get_me must not be called when the agent is out of scope")),
        ),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/w/ws-caller/fleet/agent-channels/telegram",
                params={"agent_id": "ainstall_belongs_to_another_tenant"},
                json={"token": "123456:fake-botfather-token"},
            )

    assert response.status_code == 200  # this route reports failures as {"ok": False}, not an HTTP error
    body = response.json()
    assert body["ok"] is False
    assert "workspace" in body["error"].lower()
    scope_mock.assert_awaited_once_with(
        "ainstall_belongs_to_another_tenant", tenant_id="tenant-caller", workspace_id="ws-caller",
    )


@pytest.mark.anyio
async def test_foreign_agent_id_on_discord_assign_route_is_rejected_even_in_the_callers_own_workspace() -> None:
    """Discord counterpart of the Telegram test above."""
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _legit_caller_user

    with (
        patch.object(routes_fleet, "_resolve_tenant", new=AsyncMock(return_value="tenant-caller")),
        patch(
            "server_modules.agent_bindings_repository.agent_install_in_scope",
            new=AsyncMock(return_value=False),
        ) as scope_mock,
        patch(
            "server_modules.discord_bot_provisioning_service.discord_get_me",
            new=AsyncMock(side_effect=AssertionError("discord_get_me must not be called when the agent is out of scope")),
        ),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/w/ws-caller/fleet/agent-channels/discord",
                params={"agent_id": "ainstall_belongs_to_another_tenant"},
                json={"token": "fake-discord-bot-token"},
            )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "workspace" in body["error"].lower()
    scope_mock.assert_awaited_once_with(
        "ainstall_belongs_to_another_tenant", tenant_id="tenant-caller", workspace_id="ws-caller",
    )


@pytest.mark.anyio
async def test_same_workspace_agent_id_on_telegram_assign_route_still_works() -> None:
    """The ownership gate must not block the legitimate path: caller and
    agent genuinely share a workspace."""
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _legit_caller_user

    with (
        patch.object(routes_fleet, "_resolve_tenant", new=AsyncMock(return_value="tenant-caller")),
        patch(
            "server_modules.agent_bindings_repository.agent_install_in_scope",
            new=AsyncMock(return_value=True),
        ) as scope_mock,
        patch(
            "server_modules.hosted_bot_provisioning_service.get_me",
            new=AsyncMock(return_value={"username": "own_agent_bot", "id": "111"}),
        ),
        patch(
            "server_modules.agent_bindings_repository.find_inbound_owner_conflict",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "server_modules.hosted_bot_provisioning_service.store_byo_bot_credential",
            return_value="cred-own",
        ),
        patch(
            "server_modules.hosted_bot_provisioning_service.agent_bot_webhook_url",
            return_value="https://example.com/webhook",
        ),
        patch(
            "server_modules.hosted_bot_provisioning_service.set_webhook",
            new=AsyncMock(return_value={"ok": True}),
        ),
        patch(
            "server_modules.agent_bindings_repository.upsert_channel_binding",
            new=AsyncMock(return_value={"id": "binding-own"}),
        ) as upsert_mock,
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/w/ws-caller/fleet/agent-channels/telegram",
                params={"agent_id": "agent-own"},
                json={"token": "123456:real-botfather-token"},
            )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["channel"]["bot_username"] == "own_agent_bot"
    scope_mock.assert_awaited_once_with("agent-own", tenant_id="tenant-caller", workspace_id="ws-caller")
    upsert_mock.assert_awaited_once()
