from __future__ import annotations

import json
import tempfile
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from server_modules import app_registry_api
from server_modules import marketplace_distribution_service
from server_modules import mini_apps_service
from server_modules import routes_marketplace
from server_modules import workspace_context


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(routes_marketplace.router, prefix="/api")
    return app


def _seed_browser_session(client: httpx.AsyncClient, *, csrf: bool) -> None:
    client.cookies.set(routes_marketplace.auth_module.AUTH_ACCESS_COOKIE_NAME, "access-cookie", path="/")
    if csrf:
        client.cookies.set(routes_marketplace.auth_module.AUTH_CSRF_COOKIE_NAME, "csrf-cookie", path="/")


def _patch_app_registry(monkeypatch: pytest.MonkeyPatch, temp_root: Path) -> None:
    registry_path = temp_root / "apps.json"

    def _safe_read_json(path, fallback):
        if not path.exists():
            return fallback
        return json.loads(path.read_text(encoding="utf-8"))

    def _safe_write_json(path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    monkeypatch.setattr(app_registry_api, "ORION_APP_REGISTRY_FILE", registry_path, raising=False)
    monkeypatch.setattr(app_registry_api, "_safe_read_json", _safe_read_json, raising=False)
    monkeypatch.setattr(app_registry_api, "_safe_write_json", _safe_write_json, raising=False)
    monkeypatch.setattr(app_registry_api, "_utc_now_iso", lambda: "2026-04-22T00:00:00Z", raising=False)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _allow_marketplace_rust_gate(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        marketplace_distribution_service,
        "_enforce_marketplace_distribution_state_decision",
        lambda **_kwargs: {"decision": "allow"},
    )
    monkeypatch.setattr(
        mini_apps_service,
        "_enforce_mini_apps_state_decision",
        lambda **_kwargs: {"decision": "allow"},
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("path", "json_body"),
    [
        (
            "/api/workspaces/ws-1/marketplace/providers",
            {"label": "Provider", "description": "Provider package."},
        ),
        (
            "/api/workspaces/ws-1/marketplace/apps",
            {"label": "App", "description": "App package."},
        ),
        ("/api/workspaces/ws-1/marketplace/packages/pkg-1/install", None),
        (
            "/api/workspaces/ws-1/marketplace/packages/pkg-1/review",
            {"approved": True},
        ),
        (
            "/api/workspaces/ws-1/marketplace/packages/pkg-1/runtime-events",
            {"event_type": "runtime_ok"},
        ),
    ],
)
async def test_workspace_marketplace_mutations_reject_cookie_session_without_csrf(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    json_body: dict[str, object] | None,
) -> None:
    app = _build_app()
    routes_marketplace.auth_module.CSRF_FAILURE_RATE_LIMIT_BUCKETS.clear()

    def fail_decode_token(_token: str):
        pytest.fail("cookie auth token should not be decoded before CSRF validation")

    monkeypatch.setattr(routes_marketplace.auth_module, "_decode_token_payload", fail_decode_token)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        _seed_browser_session(client, csrf=False)
        response = await client.post(path, json=json_body)

    assert response.status_code == 403
    assert response.json()["detail"] == "CSRF validation failed."


@pytest.mark.anyio
async def test_marketplace_routes_register_install_and_track_runtime(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_marketplace.get_current_user] = lambda: {
        "user_id": "user-1",
        "workspace_access": {"ws-1": {"tenant_id": "tenant-1", "role": "owner"}},
    }
    monkeypatch.setattr(
        routes_marketplace.auth_module,
        "enforce_workspace_access",
        lambda current_user, workspace_id, minimum_role="viewer": workspace_id,
    )

    with tempfile.TemporaryDirectory(prefix="marketplace-routes-") as tmpdir:
        temp_root = Path(tmpdir)
        monkeypatch.setattr(workspace_context, "_WORKSPACE_DIR", temp_root / "workspace")
        _patch_app_registry(monkeypatch, temp_root)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            templates = await client.get("/api/workspaces/ws-1/studio/templates")
            assert templates.status_code == 200
            templates_payload = templates.json()
            assert templates_payload["count"] == 3
            assert {item["slug"] for item in templates_payload["items"]} == {
                "shop-assistant",
                "dental-receptionist",
                "restaurant-order-taker",
            }

            create_provider = await client.post(
                "/api/workspaces/ws-1/marketplace/providers",
                json={
                    "label": "Signal Forge Models",
                    "description": "Provider package for governed third-party models.",
                    "verification_status": "partner",
                    "review_state": "approved",
                    "health_state": "healthy",
                    "billing": {
                        "monetization_kind": "metered",
                        "revenue_share_bps": 900,
                        "accounting_hook": {"ledger_key": "signal-forge.install"},
                    },
                    "provider": {
                        "provider_id": "signalforge",
                        "models": [
                            {
                                "id": "signalforge-chat",
                                "label": "Signal Forge Chat",
                            }
                        ],
                    },
                },
            )
            assert create_provider.status_code == 200
            provider_package_id = create_provider.json()["package_id"]

            create_app = await client.post(
                "/api/workspaces/ws-1/marketplace/apps",
                json={
                    "label": "Signal Forge Console",
                    "description": "Hosted app package distributed through the Empyralis shell.",
                    "category": "Operations",
                    "publisher": {"label": "Signal Forge"},
                    "app": {
                        "app_id": "signalforge_console",
                        "hosted_url": "https://apps.signalforge.example/embed",
                        "icon_url": "https://apps.signalforge.example/icon.png",
                        "allowed_origins": ["https://apps.signalforge.example"],
                        "permissions": [],
                    },
                },
            )
            assert create_app.status_code == 200
            app_package_id = create_app.json()["package_id"]
            assert create_app.json()["review_state"] == "pending"

            queue = await client.get("/api/workspaces/ws-1/marketplace/app-submissions")
            assert queue.status_code == 200
            assert queue.json()["items"][0]["package_id"] == app_package_id

            public_apps_before_review = await client.get("/api/workspaces/ws-1/marketplace/packages?kind=app&runtime_type=community")
            assert public_apps_before_review.status_code == 200
            assert public_apps_before_review.json()["count"] == 0

            review_app = await client.post(
                f"/api/workspaces/ws-1/marketplace/packages/{app_package_id}/review",
                json={"approved": True, "verification_status": "partner", "reason": "Looks good."},
            )
            assert review_app.status_code == 200
            assert review_app.json()["review_state"] == "approved"

            install_provider = await client.post(
                f"/api/workspaces/ws-1/marketplace/packages/{provider_package_id}/install"
            )
            assert install_provider.status_code == 200
            assert install_provider.json()["installed"] is True

            install_app = await client.post(
                f"/api/workspaces/ws-1/marketplace/packages/{app_package_id}/install"
            )
            assert install_app.status_code == 200
            assert install_app.json()["runtime_truth"]["open_href"] == "/w/ws-1/applications/signalforge_console"

            runtime_event = await client.post(
                f"/api/workspaces/ws-1/marketplace/packages/{provider_package_id}/runtime-events",
                json={"event_type": "runtime_ok", "health_state": "healthy", "metadata": {"probe": "smoke"}},
            )
            assert runtime_event.status_code == 200
            assert runtime_event.json()["analytics"]["runtime_event_count"] == 1

            list_response = await client.get("/api/workspaces/ws-1/marketplace/packages")
            assert list_response.status_code == 200
            payload = list_response.json()
            assert payload["count"] == 2
            installed_items = {item["package_id"]: item for item in payload["items"]}
            assert installed_items[provider_package_id]["installed"] is True
            assert installed_items[provider_package_id]["billing"]["revenue_share_bps"] == 900
            assert installed_items[app_package_id]["runtime_truth"]["surface"] == "app_registry"


@pytest.mark.anyio
async def test_marketplace_route_lists_backend_seed_agent_templates(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_marketplace.get_current_user] = lambda: {
        "user_id": "user-1",
        "workspace_access": {"ws-1": {"tenant_id": "tenant-1", "role": "owner"}},
    }
    monkeypatch.setattr(
        routes_marketplace.auth_module,
        "enforce_workspace_access",
        lambda current_user, workspace_id, minimum_role="viewer": workspace_id,
    )

    with tempfile.TemporaryDirectory(prefix="marketplace-seed-routes-") as tmpdir:
        monkeypatch.setattr(workspace_context, "_WORKSPACE_DIR", Path(tmpdir) / "workspace")
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            list_response = await client.get("/api/workspaces/ws-1/marketplace/packages?kind=agent_template")
            assert list_response.status_code == 200
            payload = list_response.json()
            assert payload["count"] == 6
            items = {item["package"]["template_id"]: item for item in payload["items"]}
            assert set(items) == {
                "shop-assistant",
                "dental-receptionist",
                "restaurant-order-taker",
                "restaurant_orders",
                "auto_parts_sales",
                "spreadsheet_catalog",
            }
            assert items["shop-assistant"]["kind"] == "agent_template"
            assert items["shop-assistant"]["runtime_truth"]["open_href"] == "/w/ws-1/studio?proof_agent=shop-assistant"


@pytest.mark.anyio
async def test_marketplace_route_returns_shop_assistant_revenue_proof(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_marketplace.get_current_user] = lambda: {
        "user_id": "user-1",
        "workspace_access": {"ws-1": {"tenant_id": "tenant-1", "role": "owner"}},
    }
    monkeypatch.setattr(
        routes_marketplace.auth_module,
        "enforce_workspace_access",
        lambda current_user, workspace_id, minimum_role="viewer": workspace_id,
    )

    with tempfile.TemporaryDirectory(prefix="marketplace-revenue-proof-") as tmpdir:
        monkeypatch.setattr(workspace_context, "_WORKSPACE_DIR", Path(tmpdir) / "workspace")
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get(
                "/api/workspaces/ws-1/studio/templates/shop-assistant/revenue-proof",
                params={
                    "conversations_handled": 100,
                    "qualified_purchase_intent": 20,
                    "owner_handoffs": 5,
                    "avg_order_value_usd": 90,
                    "close_rate_from_intent": 0.4,
                    "gross_margin_rate": 0.5,
                    "monthly_subscription_cost_usd": 120,
                },
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["template_slug"] == "shop-assistant"
            assert payload["revenue_proof"]["vertical"] == "shop_assistant"
            assert payload["roi_snapshot"]["event_counts"]["qualified_purchase_intent"] == 20
            assert payload["roi_snapshot"]["results"]["estimated_orders"] == 8.0
