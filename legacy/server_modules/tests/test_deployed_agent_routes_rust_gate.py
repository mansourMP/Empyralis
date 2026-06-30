from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from server_modules import routes_deployed_agents


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(routes_deployed_agents.router)
    return app


def _owner_user() -> dict[str, object]:
    return {
        "user_id": "user-owner",
        "auth_type": "bearer",
        "workspace_access": {
            "ws-1": {
                "workspace_id": "ws-1",
                "tenant_id": "tenant-1",
                "role": "owner",
            }
        },
    }


def test_deployed_agent_route_gate_accepts_create_draft_action(monkeypatch: pytest.MonkeyPatch):
    captured = {}

    def _fake_rust(command: str, payload: dict[str, object]):
        captured["command"] = command
        captured["payload"] = dict(payload)
        return {"next_action": "create_draft"}

    monkeypatch.setattr(
        routes_deployed_agents.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        _fake_rust,
    )

    result = routes_deployed_agents._enforce_deployed_agent_route_decision(
        operation="create_draft",
        workspace_id="ws-1",
        current_user=_owner_user(),
        deployment_state="draft",
        runtime_target="cloud",
    )

    assert result["next_action"] == "create_draft"
    assert captured["command"] == "deployed-agent-decision"
    assert captured["payload"]["operation"] == "create_draft"
    assert captured["payload"]["workspace_id"] == "ws-1"
    assert captured["payload"]["actor_role"] == "owner"


@pytest.mark.anyio
async def test_create_deployed_agent_route_unexpected_next_action_blocks_before_service(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_deployed_agents.get_current_user] = _owner_user
    monkeypatch.setattr(routes_deployed_agents.auth_module, "validate_csrf", lambda request: None)
    monkeypatch.setattr(
        routes_deployed_agents,
        "_enforce_deployed_agent_route_decision",
        lambda **kwargs: (_ for _ in ()).throw(
            HTTPException(
                status_code=423,
                detail={
                    "error": "rust_deployed_agent_invalid_next_action",
                    "operation": "create_draft",
                    "reason": "unexpected_next_action:read_agent",
                },
            )
        ),
    )

    async def _should_not_run(**kwargs):
        raise AssertionError("service should not run")

    monkeypatch.setattr(routes_deployed_agents.deployed_agent_service, "create_draft_deployed_agent", _should_not_run)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/deployed-agents",
            json={
                "workspace_id": "ws-1",
                "name": "Store Assistant",
                "persona": "Helpful retail agent",
            },
        )

    assert response.status_code == 423
    assert response.json()["detail"]["reason"] == "unexpected_next_action:read_agent"


@pytest.mark.anyio
async def test_get_deployed_agent_route_unexpected_next_action_blocks_before_service(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_deployed_agents.get_current_user] = _owner_user
    monkeypatch.setattr(
        routes_deployed_agents,
        "_enforce_deployed_agent_route_decision",
        lambda **kwargs: (_ for _ in ()).throw(
            HTTPException(
                status_code=423,
                detail={
                    "error": "rust_deployed_agent_invalid_next_action",
                    "operation": "read",
                    "reason": "unexpected_next_action:create_draft",
                },
            )
        ),
    )

    async def _should_not_run(**kwargs):
        raise AssertionError("service should not run")

    monkeypatch.setattr(routes_deployed_agents.deployed_agent_service, "get_deployed_agent_detail", _should_not_run)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/deployed-agents/dagent_1", params={"workspace_id": "ws-1"})

    assert response.status_code == 423
    assert response.json()["detail"]["reason"] == "unexpected_next_action:create_draft"
