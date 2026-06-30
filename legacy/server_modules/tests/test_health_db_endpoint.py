import importlib
from pathlib import Path
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


@pytest.mark.anyio
async def test_health_db_endpoint_reports_backend_state():
    routes_health_module = _routes_health_module()
    with patch.object(routes_health_module.runtime_db, "postgres_health_status", new=AsyncMock(return_value="connected")) as mock_postgres, patch.object(
        routes_health_module.runtime_db,
        "sqlite_health_status",
        return_value="active",
    ) as mock_sqlite, patch.dict(os.environ, {"ORION_API_KEY": "secret"}, clear=False):
        transport = httpx.ASGITransport(app=_build_app(routes_health_module))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/health/db", headers={"X-API-Key": "secret"})

    assert response.status_code == 200
    assert response.json() == {
        "postgres": "connected",
        "sqlite": "active",
        "durable_required": False,
        "durability_mode": "best_effort_local_dev",
        "sqlite_fallback_allowed": True,
    }
    mock_postgres.assert_awaited_once()
    mock_sqlite.assert_called_once()


@pytest.mark.anyio
async def test_public_health_endpoint_redacts_sensitive_runtime_fields():
    routes_health_module = _routes_health_module()
    sensitive_payload = {
        "ok": True,
        "workspace_ids": ["workspace-1"],
        "queue_backlog": 42,
        "state_file": "/tmp/runtime-state.sqlite3",
        "provider_profile_health": {"providers_by_id": {"openai": {"state": "active"}}},
    }
    with patch.object(routes_health_module.core, "health", new=AsyncMock(return_value=sensitive_payload)):
        transport = httpx.ASGITransport(app=_build_app(routes_health_module))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


@pytest.mark.anyio
async def test_internal_health_endpoint_returns_full_payload():
    routes_health_module = _routes_health_module()
    with patch.object(
        routes_health_module.core,
        "health",
        new=AsyncMock(return_value={"ok": True, "workspace_ids": ["workspace-1"]}),
    ) as mock_health, patch.dict(os.environ, {"ORION_API_KEY": "secret"}, clear=False):
        transport = httpx.ASGITransport(app=_build_app(routes_health_module))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get(
                "/health/internal",
                headers={"X-API-Key": "secret"},
            )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "workspace_ids": ["workspace-1"]}
    mock_health.assert_awaited_once()


def test_sqlite_health_status_reflects_runtime_state_file_presence(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "runtime.db"
    monkeypatch.setenv("ORION_RUNTIME_STATE_DB", str(db_path))
    from server_modules import db as runtime_db

    assert runtime_db.sqlite_health_status() == "inactive"
    db_path.touch()
    assert runtime_db.sqlite_health_status() == "active"
