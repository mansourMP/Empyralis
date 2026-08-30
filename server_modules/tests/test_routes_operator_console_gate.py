"""Operator console routes -- GET /internal/operator/{overview, accounts,
accounts/{workspace_id}, activation-funnel, retention, failures, spend}.

Same convention as test_routes_health_platform_activation_gate.py: every
route must refuse an unauthenticated caller (401) and an authenticated
non-operator (403) WITHOUT the underlying query ever running -- proved with
a mock that raises if called, not just asserted after the fact. These
exercise the routes over a real ASGI request (httpx against the actual
router), so the dependency wiring itself is what refuses the request.
"""

from __future__ import annotations

import importlib
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI


def _routes_module():
    return importlib.import_module("server_modules.routes_operator_console")


def _build_app(routes_module) -> FastAPI:
    app = FastAPI()
    app.include_router(routes_module.router)
    return app


def _operator_user() -> dict:
    # auth_type == "api_key" is the platform's own service-key branch of
    # has_platform_fleet_operator_access -- see auth.py's docstring.
    return {"user_id": "svc", "auth_type": "api_key"}


def _plain_member() -> dict:
    return {"user_id": "member-1", "email": "member@example.com", "auth_type": "bearer", "role": "member"}


ROUTES_UNDER_TEST = (
    ("GET", "/internal/operator/overview", "build_overview"),
    ("GET", "/internal/operator/accounts", "list_accounts"),
    ("GET", "/internal/operator/accounts/ws_1", "get_account_detail"),
    ("GET", "/internal/operator/activation-funnel", "build_activation_funnel"),
    ("GET", "/internal/operator/retention", "build_retention"),
    ("GET", "/internal/operator/failures", "list_failures"),
    ("GET", "/internal/operator/spend", "build_spend"),
)


def test_every_route_is_registered_on_the_router() -> None:
    """'Built and never wired' is this codebase's most common defect
    (CLAUDE.md); this is the grep-shaped guard against it living only in a
    route function nobody registered. server.py mounts this router both
    bare and under /api, mirroring health_router."""
    routes_module = _routes_module()
    paths = {getattr(route, "path", "") for route in routes_module.router.routes}
    assert paths == {
        "/internal/operator/overview",
        "/internal/operator/accounts",
        "/internal/operator/accounts/{workspace_id}",
        "/internal/operator/activation-funnel",
        "/internal/operator/retention",
        "/internal/operator/failures",
        "/internal/operator/spend",
    }


def test_the_router_is_mounted_in_server_py() -> None:
    with open("server.py", "r", encoding="utf-8") as handle:
        source = handle.read()
    assert "from server_modules.routes_operator_console import router as operator_console_router" in source
    assert "app.include_router(operator_console_router)" in source
    assert 'app.include_router(operator_console_router, prefix="/api")' in source


@pytest.mark.anyio
@pytest.mark.parametrize("method,path,service_fn", ROUTES_UNDER_TEST)
async def test_an_unauthenticated_request_is_rejected(
    method: str, path: str, service_fn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ORION_AUTH_REQUIRED", "1")
    monkeypatch.delenv("EMPYRALIS_DEPLOY_ENV", raising=False)
    monkeypatch.delenv("ORION_ENV", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("NODE_ENV", raising=False)
    monkeypatch.delenv("ORION_API_KEY", raising=False)

    routes_module = _routes_module()
    app = _build_app(routes_module)
    # No dependency_overrides -- exercises the real auth dependency chain.

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.request(method, path)

    assert response.status_code == 401


@pytest.mark.anyio
@pytest.mark.parametrize("method,path,service_fn", ROUTES_UNDER_TEST)
async def test_an_authenticated_non_operator_is_refused_before_any_query_runs(
    method: str, path: str, service_fn: str
) -> None:
    routes_module = _routes_module()
    app = _build_app(routes_module)
    app.dependency_overrides[routes_module.require_api_key] = _plain_member

    with patch.object(
        routes_module.operator_console_service,
        service_fn,
        new=AsyncMock(side_effect=AssertionError(f"{service_fn} must never be reached for a non-operator caller")),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.request(method, path)

    assert response.status_code == 403


@pytest.mark.anyio
async def test_a_missing_control_plane_pool_returns_503_never_a_zeroed_payload() -> None:
    routes_module = _routes_module()
    app = _build_app(routes_module)
    app.dependency_overrides[routes_module.require_api_key] = _operator_user

    with (
        patch.object(
            routes_module.control_plane_repository, "ensure_control_plane_schema", new=AsyncMock(return_value=None)
        ),
        patch.object(
            routes_module.operator_console_service,
            "build_overview",
            new=AsyncMock(side_effect=AssertionError("must never be reached when the pool is unavailable")),
        ),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/internal/operator/overview")

    assert response.status_code == 503
    body = response.json()
    assert body != {"totals": {"users": 0}}


@pytest.mark.anyio
async def test_a_broken_rls_scope_surfaces_as_500_not_a_silent_zero() -> None:
    routes_module = _routes_module()
    app = _build_app(routes_module)
    app.dependency_overrides[routes_module.require_api_key] = _operator_user

    with (
        patch.object(
            routes_module.control_plane_repository, "ensure_control_plane_schema", new=AsyncMock(return_value=object())
        ),
        patch.object(
            routes_module.operator_console_service,
            "build_overview",
            new=AsyncMock(
                side_effect=routes_module.operator_console_service.OperatorConsoleScopeBroken("app.rls_bypass was not 'on'")
            ),
        ),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/internal/operator/overview")

    assert response.status_code == 500


@pytest.mark.anyio
async def test_operator_overview_happy_path() -> None:
    routes_module = _routes_module()
    app = _build_app(routes_module)
    app.dependency_overrides[routes_module.require_api_key] = _operator_user

    snapshot = {"generated_at": "now", "rls_bypass_verified": True, "totals": {"users": 105}}
    with (
        patch.object(
            routes_module.control_plane_repository, "ensure_control_plane_schema", new=AsyncMock(return_value=object())
        ),
        patch.object(
            routes_module.operator_console_service, "build_overview", new=AsyncMock(return_value=snapshot)
        ) as mock_build,
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/internal/operator/overview")

    assert response.status_code == 200
    assert response.json() == snapshot
    mock_build.assert_awaited_once()


@pytest.mark.anyio
async def test_operator_accounts_happy_path_forwards_query_params() -> None:
    routes_module = _routes_module()
    app = _build_app(routes_module)
    app.dependency_overrides[routes_module.require_api_key] = _operator_user

    result = {"total_matching": 0, "accounts": []}
    with (
        patch.object(
            routes_module.control_plane_repository, "ensure_control_plane_schema", new=AsyncMock(return_value=object())
        ),
        patch.object(
            routes_module.operator_console_service, "list_accounts", new=AsyncMock(return_value=result)
        ) as mock_list,
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/internal/operator/accounts",
                params={"search": "acme", "sort_by": "total_platform_cost_usd", "sort_dir": "desc", "limit": 10},
            )

    assert response.status_code == 200
    assert response.json() == result
    mock_list.assert_awaited_once()
    kwargs = mock_list.await_args.kwargs
    assert kwargs["search"] == "acme"
    assert kwargs["sort_by"] == "total_platform_cost_usd"
    assert kwargs["sort_dir"] == "desc"
    assert kwargs["limit"] == 10


@pytest.mark.anyio
async def test_operator_accounts_rejects_bad_sort_by_with_400() -> None:
    routes_module = _routes_module()
    app = _build_app(routes_module)
    app.dependency_overrides[routes_module.require_api_key] = _operator_user

    with (
        patch.object(
            routes_module.control_plane_repository, "ensure_control_plane_schema", new=AsyncMock(return_value=object())
        ),
        patch.object(
            routes_module.operator_console_service,
            "list_accounts",
            new=AsyncMock(side_effect=ValueError("sort_by must be one of [...]")),
        ),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/internal/operator/accounts", params={"sort_by": "not_a_column"})

    assert response.status_code == 400


@pytest.mark.anyio
async def test_operator_account_detail_404s_when_workspace_not_found() -> None:
    routes_module = _routes_module()
    app = _build_app(routes_module)
    app.dependency_overrides[routes_module.require_api_key] = _operator_user

    with (
        patch.object(
            routes_module.control_plane_repository, "ensure_control_plane_schema", new=AsyncMock(return_value=object())
        ),
        patch.object(
            routes_module.operator_console_service, "get_account_detail", new=AsyncMock(return_value=None)
        ),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/internal/operator/accounts/does-not-exist")

    assert response.status_code == 404


@pytest.mark.anyio
async def test_operator_account_detail_happy_path() -> None:
    routes_module = _routes_module()
    app = _build_app(routes_module)
    app.dependency_overrides[routes_module.require_api_key] = _operator_user

    detail = {"workspace_id": "ws_1", "name": "Acme"}
    with (
        patch.object(
            routes_module.control_plane_repository, "ensure_control_plane_schema", new=AsyncMock(return_value=object())
        ),
        patch.object(
            routes_module.operator_console_service, "get_account_detail", new=AsyncMock(return_value=detail)
        ) as mock_detail,
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/internal/operator/accounts/ws_1")

    assert response.status_code == 200
    assert response.json() == detail
    mock_detail.assert_awaited_once()
    assert mock_detail.await_args.args[1] == "ws_1"


@pytest.mark.anyio
async def test_operator_failures_forwards_days_and_limit() -> None:
    routes_module = _routes_module()
    app = _build_app(routes_module)
    app.dependency_overrides[routes_module.require_api_key] = _operator_user

    result = {"total_failures": 0, "grouped": [], "recent": []}
    with (
        patch.object(
            routes_module.control_plane_repository, "ensure_control_plane_schema", new=AsyncMock(return_value=object())
        ),
        patch.object(
            routes_module.operator_console_service, "list_failures", new=AsyncMock(return_value=result)
        ) as mock_failures,
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/internal/operator/failures", params={"days": 14, "limit": 25})

    assert response.status_code == 200
    mock_failures.assert_awaited_once()
    assert mock_failures.await_args.kwargs["days"] == 14
    assert mock_failures.await_args.kwargs["limit"] == 25


@pytest.mark.anyio
async def test_operator_spend_forwards_days() -> None:
    routes_module = _routes_module()
    app = _build_app(routes_module)
    app.dependency_overrides[routes_module.require_api_key] = _operator_user

    result = {"total_platform_cost_usd": 0, "by_workspace": [], "by_provider_model": [], "over_time": []}
    with (
        patch.object(
            routes_module.control_plane_repository, "ensure_control_plane_schema", new=AsyncMock(return_value=object())
        ),
        patch.object(
            routes_module.operator_console_service, "build_spend", new=AsyncMock(return_value=result)
        ) as mock_spend,
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/internal/operator/spend", params={"days": 60})

    assert response.status_code == 200
    mock_spend.assert_awaited_once()
    assert mock_spend.await_args.kwargs["days"] == 60


@pytest.mark.anyio
async def test_operator_retention_and_activation_funnel_happy_paths() -> None:
    routes_module = _routes_module()
    app = _build_app(routes_module)
    app.dependency_overrides[routes_module.require_api_key] = _operator_user

    with (
        patch.object(
            routes_module.control_plane_repository, "ensure_control_plane_schema", new=AsyncMock(return_value=object())
        ),
        patch.object(
            routes_module.operator_console_service, "build_retention", new=AsyncMock(return_value={"total_users": 1})
        ),
        patch.object(
            routes_module.operator_console_service, "build_activation_funnel", new=AsyncMock(return_value={"steps": []})
        ),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            retention_response = await client.get("/internal/operator/retention")
            funnel_response = await client.get("/internal/operator/activation-funnel")

    assert retention_response.status_code == 200
    assert retention_response.json() == {"total_users": 1}
    assert funnel_response.status_code == 200
    assert funnel_response.json() == {"steps": []}
