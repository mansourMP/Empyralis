from __future__ import annotations

import tempfile
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from server_modules import mini_apps_service, routes_discovery, workspace_context


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(routes_discovery.router, prefix="/api")
    return app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _allow_mini_apps_rust_gate(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        mini_apps_service,
        "_enforce_mini_apps_state_decision",
        lambda **_kwargs: {"decision": "allow"},
    )


def _seed_browser_session(client: httpx.AsyncClient, *, csrf: bool) -> None:
    client.cookies.set(routes_discovery.auth_module.AUTH_ACCESS_COOKIE_NAME, "access-cookie", path="/")
    if csrf:
        client.cookies.set(routes_discovery.auth_module.AUTH_CSRF_COOKIE_NAME, "csrf-cookie", path="/")


@pytest.mark.anyio
async def test_discovery_adopt_rejects_cookie_session_without_csrf(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _build_app()
    routes_discovery.auth_module.CSRF_FAILURE_RATE_LIMIT_BUCKETS.clear()

    def fail_decode_token(_token: str):
        pytest.fail("cookie auth token should not be decoded before CSRF validation")

    monkeypatch.setattr(routes_discovery.auth_module, "_decode_token_payload", fail_decode_token)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        _seed_browser_session(client, csrf=False)
        response = await client.post("/api/workspaces/ws-1/discovery/items/disc_missing/adopt")

    assert response.status_code == 403
    assert response.json()["detail"] == "CSRF validation failed."


@pytest.mark.anyio
async def test_discovery_feed_and_adopt_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _build_app()
    app.dependency_overrides[routes_discovery.get_current_user] = lambda: {
        "user_id": "user-2",
        "email": "mike@example.com",
    }
    monkeypatch.setattr(
        routes_discovery.auth_module,
        "enforce_workspace_access",
        lambda current_user, workspace_id, minimum_role="viewer": workspace_id,
    )

    with tempfile.TemporaryDirectory(prefix="discovery-routes-") as tmpdir:
        monkeypatch.setattr(workspace_context, "_WORKSPACE_DIR", Path(tmpdir) / "workspace")
        mini_apps_service.publish_app(
            "source-ws",
            name="Public Budget",
            description="Track daily spending.",
            source_url="https://apps.example.com/budget",
            visibility="public",
            creator_label="@sarah",
        )
        mini_apps_service.approve_app_publication("source-ws", "public_budget")

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            apps_response = await client.get("/api/workspaces/target-ws/discovery/feed?filter=apps")
            assert apps_response.status_code == 200
            app_item = apps_response.json()["items"][0]
            assert app_item["title"] == "Public Budget"
            assert app_item["action"]["label"] == "Clone Blueprint"
            assert "public_app_id" not in app_item
            assert "package_id" not in app_item

            clone_response = await client.post(
                f"/api/workspaces/target-ws/discovery/items/{app_item['id']}/adopt"
            )
            assert clone_response.status_code == 200
            assert clone_response.json()["open_url"] == "/w/target-ws/applications/public_budget"

            agents_response = await client.get("/api/workspaces/target-ws/discovery/feed?filter=agents")
            assert agents_response.status_code == 200
            template_item = next(item for item in agents_response.json()["items"] if item["title"] == "Shop Assistant")
            assert template_item["action"]["label"] == "Copy to Studio"
            assert "runtime_truth" not in template_item

            template_response = await client.post(
                f"/api/workspaces/target-ws/discovery/items/{template_item['id']}/adopt"
            )
            assert template_response.status_code == 200
            assert template_response.json()["open_url"] == "/w/target-ws/studio?proof_agent=shop-assistant"
