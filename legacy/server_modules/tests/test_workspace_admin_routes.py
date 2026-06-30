from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from server_modules import routes_platform_analytics
from server_modules import routes_workspaces
from server_modules import workspace_admin_service


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(routes_workspaces.router)
    app.include_router(routes_platform_analytics.router)
    return app


def _seed_browser_session(client: httpx.AsyncClient, *, csrf: bool) -> None:
    client.cookies.set(routes_workspaces.auth_module.AUTH_ACCESS_COOKIE_NAME, "access-cookie", path="/")
    if csrf:
        client.cookies.set(routes_workspaces.auth_module.AUTH_CSRF_COOKIE_NAME, "csrf-cookie", path="/")


@pytest.mark.anyio
async def test_workspace_routing_route_delegates_to_admin_service(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_workspaces.get_current_user] = lambda: {"user_id": "user-1"}

    async def fake_build_workspace_routing_payload(*, workspace_id: str, current_user):
        assert workspace_id == "ws-1"
        assert current_user["user_id"] == "user-1"
        return {"workspace": {"id": "ws-1"}, "runtime": {"deploymentMode": "cloud_default"}}

    monkeypatch.setattr(
        routes_workspaces.workspace_admin_service,
        "build_workspace_routing_payload",
        fake_build_workspace_routing_payload,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/workspaces/ws-1/routing")

    assert response.status_code == 200
    assert response.json()["runtime"]["deploymentMode"] == "cloud_default"


@pytest.mark.anyio
async def test_workspace_routing_patch_route_delegates_admin_defaults(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_workspaces.get_current_user] = lambda: {"user_id": "user-1"}
    monkeypatch.setattr(routes_workspaces.auth_module, "validate_csrf", lambda request: None)

    async def fake_update_workspace_routing_payload(*, workspace_id: str, current_user, payload):
        assert workspace_id == "ws-1"
        assert current_user["user_id"] == "user-1"
        assert payload["admin_defaults"]["privacy_policy_url"] == "https://example.com/privacy"
        assert payload["admin_defaults"]["allowed_live_channels"] == ["telegram", "whatsapp"]
        return {
            "workspace": {"id": "ws-1"},
            "admin_defaults": payload["admin_defaults"],
        }

    monkeypatch.setattr(
        routes_workspaces.workspace_admin_service,
        "update_workspace_routing_payload",
        fake_update_workspace_routing_payload,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.patch(
            "/workspaces/ws-1/routing",
            json={
                "admin_defaults": {
                    "privacy_policy_url": "https://example.com/privacy",
                    "allowed_live_channels": ["telegram", "whatsapp"],
                }
            },
        )

    assert response.status_code == 200
    assert response.json()["admin_defaults"]["privacy_policy_url"] == "https://example.com/privacy"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "path", "json_body", "service_name"),
    [
        (
            "POST",
            "/workspaces/ws-1/providers/credentials",
            {"provider": "openai", "api_key": "sk-test"},
            "upsert_workspace_provider_credential",
        ),
        (
            "DELETE",
            "/workspaces/ws-1/providers/credentials",
            {"provider": "openai"},
            "delete_workspace_provider_credential",
        ),
        (
            "POST",
            "/workspaces/ws-1/providers/openai/models/refresh",
            None,
            "refresh_workspace_provider_models",
        ),
    ],
)
async def test_provider_admin_routes_reject_missing_csrf(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    json_body: dict[str, str] | None,
    service_name: str,
) -> None:
    app = _build_app()
    app.dependency_overrides[routes_workspaces.get_current_user] = lambda: {"user_id": "owner-1"}
    service_called = False

    async def fake_service(**kwargs):
        nonlocal service_called
        service_called = True
        return {"ok": True}

    monkeypatch.setattr(routes_workspaces.workspace_admin_service, service_name, fake_service)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        _seed_browser_session(client, csrf=False)
        response = await client.request(method, path, json=json_body)

    assert response.status_code == 403
    assert response.json()["detail"] == "CSRF validation failed."
    assert service_called is False


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "path", "json_body"),
    [
        (
            "POST",
            "/workspaces",
            {
                "name": "Workspace",
                "workspace_type": "personal",
                "preferred_shell_profile": "personal_shell",
                "default_route": "/chat",
            },
        ),
        ("PATCH", "/workspaces/ws-1", {"name": "Updated Workspace"}),
        ("POST", "/workspaces/ws-1/members/invites", {"email": "guest@example.com", "role": "member"}),
        ("DELETE", "/workspaces/ws-1/members/invites/invite-1", None),
        ("PATCH", "/workspaces/ws-1/members/user-2", {"role": "viewer"}),
        ("DELETE", "/workspaces/ws-1/members/user-2", None),
        ("PATCH", "/workspaces/ws-1/policies", {"machine_enrollment_scope": "tenant"}),
        ("PATCH", "/workspaces/ws-1/sage/tool-policy", {"tool": "web_search", "enabled": False}),
    ],
)
async def test_workspace_mutation_routes_reject_cookie_session_without_csrf(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    json_body: dict[str, object] | None,
) -> None:
    app = _build_app()
    routes_workspaces.auth_module.CSRF_FAILURE_RATE_LIMIT_BUCKETS.clear()

    def fail_decode_token(_token: str):
        pytest.fail("cookie auth token should not be decoded before CSRF validation")

    monkeypatch.setattr(routes_workspaces.auth_module, "_decode_token_payload", fail_decode_token)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        _seed_browser_session(client, csrf=False)
        response = await client.request(method, path, json=json_body)

    assert response.status_code == 403
    assert response.json()["detail"] == "CSRF validation failed."


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "path", "json_body", "service_name", "expected"),
    [
        (
            "POST",
            "/workspaces/ws-1/providers/credentials",
            {"provider": "openai", "api_key": "sk-test", "base_url": "https://api.example.com", "model": "gpt-test"},
            "upsert_workspace_provider_credential",
            {
                "workspace_id": "ws-1",
                "provider": "openai",
                "api_key": "sk-test",
                "base_url": "https://api.example.com",
                "model": "gpt-test",
            },
        ),
        (
            "DELETE",
            "/workspaces/ws-1/providers/credentials",
            {"provider": "openai"},
            "delete_workspace_provider_credential",
            {"workspace_id": "ws-1", "provider": "openai"},
        ),
        (
            "POST",
            "/workspaces/ws-1/providers/openai/models/refresh",
            None,
            "refresh_workspace_provider_models",
            {"workspace_id": "ws-1", "provider": "openai"},
        ),
    ],
)
async def test_provider_admin_routes_accept_valid_csrf_and_delegate_owner_scope(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    json_body: dict[str, str] | None,
    service_name: str,
    expected: dict[str, str],
) -> None:
    app = _build_app()
    app.dependency_overrides[routes_workspaces.get_current_user] = lambda: {"user_id": "owner-1"}
    captured: dict[str, object] = {}

    async def fake_service(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "provider": kwargs["provider"]}

    monkeypatch.setattr(routes_workspaces.workspace_admin_service, service_name, fake_service)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        _seed_browser_session(client, csrf=True)
        response = await client.request(
            method,
            path,
            json=json_body,
            headers={routes_workspaces.auth_module.AUTH_CSRF_HEADER_NAME: "csrf-cookie"},
        )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert captured["current_user"] == {"user_id": "owner-1"}
    for key, value in expected.items():
        assert captured[key] == value


@pytest.mark.anyio
async def test_platform_analytics_route_delegates_to_admin_service(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_platform_analytics.get_current_user] = lambda: {
        "user_id": "admin-1",
        "auth_type": "bearer",
        "auth_admin": True,
        "is_admin": True,
    }

    async def fake_build_platform_analytics_payload(current_user):
        assert current_user["user_id"] == "admin-1"
        return {
            "scope": "platform",
            "summary": {
                "total_active_external_users_30d": 12,
                "total_messages": 58,
            },
            "deployments": [],
            "model_cost_breakdown": [],
        }

    monkeypatch.setattr(
        routes_platform_analytics.workspace_admin_service,
        "build_platform_analytics_payload",
        fake_build_platform_analytics_payload,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/platform-analytics")

    assert response.status_code == 200
    assert response.json()["scope"] == "platform"
    assert response.json()["summary"]["total_messages"] == 58


@pytest.mark.anyio
async def test_platform_analytics_route_rejects_non_admin() -> None:
    app = _build_app()

    def _forbidden():
        raise HTTPException(status_code=403, detail="Admin role required.")

    app.dependency_overrides[routes_platform_analytics.get_current_user] = _forbidden

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/platform-analytics")

    assert response.status_code == 403
    assert response.json()["detail"] == "Admin role required."


@pytest.mark.anyio
async def test_workspace_members_route_returns_members_and_invites(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_workspaces.get_current_user] = lambda: {"user_id": "user-1"}

    async def fake_build_workspace_members_payload(*, workspace_id: str, current_user):
        assert workspace_id == "ws-1"
        return {
            "workspace": {"id": "ws-1"},
            "members": [{"user_id": "user-1", "role": "owner"}],
            "invites": [{"id": "invite-1", "email": "guest@example.com", "role": "member"}],
        }

    monkeypatch.setattr(
        routes_workspaces.workspace_admin_service,
        "build_workspace_members_payload",
        fake_build_workspace_members_payload,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/workspaces/ws-1/members")

    assert response.status_code == 200
    payload = response.json()
    assert payload["members"][0]["role"] == "owner"
    assert payload["invites"][0]["email"] == "guest@example.com"


@pytest.mark.anyio
async def test_workspace_invite_route_passes_email_and_role(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("EMPYRALIS_ENABLE_WORKSPACE_INVITES", "1")
    app = _build_app()
    app.dependency_overrides[routes_workspaces.get_current_user] = lambda: {"user_id": "user-1"}

    async def fake_invite_workspace_member(*, workspace_id: str, current_user, email: str, role: str):
        assert workspace_id == "ws-1"
        assert email == "invitee@example.com"
        assert role == "member"
        return {"id": "invite-1", "email": email, "role": role, "status": "pending"}

    monkeypatch.setattr(
        routes_workspaces.workspace_admin_service,
        "invite_workspace_member",
        fake_invite_workspace_member,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/workspaces/ws-1/members/invites",
            json={"email": "invitee@example.com", "role": "member"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "pending"


@pytest.mark.anyio
async def test_workspace_invite_revoke_route_delegates_to_admin_service(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("EMPYRALIS_ENABLE_WORKSPACE_INVITES", "1")
    app = _build_app()
    app.dependency_overrides[routes_workspaces.get_current_user] = lambda: {"user_id": "user-1"}

    async def fake_revoke_workspace_invite(*, workspace_id: str, current_user, invite_id: str):
        assert workspace_id == "ws-1"
        assert current_user["user_id"] == "user-1"
        assert invite_id == "invite-1"
        return {"id": invite_id, "status": "revoked"}

    monkeypatch.setattr(
        routes_workspaces.workspace_admin_service,
        "revoke_workspace_invite",
        fake_revoke_workspace_invite,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.delete("/workspaces/ws-1/members/invites/invite-1")

    assert response.status_code == 200
    assert response.json()["status"] == "revoked"


@pytest.mark.anyio
async def test_workspace_policies_patch_delegates_payload(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_workspaces.get_current_user] = lambda: {"user_id": "user-1"}

    async def fake_update_workspace_policies_payload(*, workspace_id: str, current_user, payload):
        assert workspace_id == "ws-1"
        assert payload["machine_enrollment_scope"] == "tenant"
        return {"workspace_id": "ws-1", "policy": payload}

    monkeypatch.setattr(
        routes_workspaces.workspace_admin_service,
        "update_workspace_policies_payload",
        fake_update_workspace_policies_payload,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.patch(
            "/workspaces/ws-1/policies",
            json={"machine_enrollment_scope": "tenant"},
        )

    assert response.status_code == 200
    assert response.json()["policy"]["machine_enrollment_scope"] == "tenant"


@pytest.mark.anyio
async def test_update_workspace_member_role_blocks_demoting_last_owner(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        workspace_admin_service.auth_module,
        "enforce_workspace_access",
        lambda current_user, workspace_id, minimum_role='owner': workspace_id,
    )

    async def fake_list_workspace_members(workspace_id: str):
        assert workspace_id == "ws-1"
        return [
            {
                "user_id": "owner-1",
                "workspace_id": "ws-1",
                "role": "owner",
                "status": "active",
            },
        ]

    monkeypatch.setattr(
        workspace_admin_service.control_plane_repository,
        "list_workspace_members",
        fake_list_workspace_members,
    )

    with pytest.raises(HTTPException) as exc_info:
        await workspace_admin_service.update_workspace_member_role(
            workspace_id="ws-1",
            current_user={"user_id": "owner-1"},
            user_id="owner-1",
            role="member",
        )

    error = exc_info.value
    assert getattr(error, "status_code", None) == 409
    assert "last workspace owner" in str(getattr(error, "detail", ""))


@pytest.mark.anyio
async def test_remove_workspace_member_blocks_removing_last_owner(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        workspace_admin_service.auth_module,
        "enforce_workspace_access",
        lambda current_user, workspace_id, minimum_role='owner': workspace_id,
    )

    async def fake_list_workspace_members(workspace_id: str):
        assert workspace_id == "ws-1"
        return [
            {
                "user_id": "owner-1",
                "workspace_id": "ws-1",
                "role": "owner",
                "status": "active",
            },
        ]

    monkeypatch.setattr(
        workspace_admin_service.control_plane_repository,
        "list_workspace_members",
        fake_list_workspace_members,
    )

    with pytest.raises(HTTPException) as exc_info:
        await workspace_admin_service.remove_workspace_member(
            workspace_id="ws-1",
            current_user={"user_id": "owner-1"},
            user_id="owner-1",
        )

    error = exc_info.value
    assert getattr(error, "status_code", None) == 409
    assert "last workspace owner" in str(getattr(error, "detail", ""))
