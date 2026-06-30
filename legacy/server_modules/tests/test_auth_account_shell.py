from __future__ import annotations

from datetime import datetime, timezone
import httpx
import pytest
from fastapi import FastAPI

from server_modules import account_shell_service
from server_modules import routes_auth


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(routes_auth.router)
    return app


def _entitlement_state(**entitlements):
    return account_shell_service.entitlements_service.WorkspaceEntitlementState(
        plan_id="pro",
        plan_label="Pro",
        source="workspace_metadata",
        entitlements=dict(entitlements),
        usage={},
        non_gated_capabilities={},
    )


@pytest.fixture(autouse=True)
def _reset_account_shell_cache():
    account_shell_service.ACCOUNT_SHELL_CACHE.clear()
    yield
    account_shell_service.ACCOUNT_SHELL_CACHE.clear()


@pytest.mark.anyio
async def test_build_account_shell_payload_uses_membership_truth_and_shell_derivation(
    monkeypatch: pytest.MonkeyPatch,
):
    current_user = {
        "auth_type": "bearer",
        "user_id": "user-1",
        "identity_versions": {"membership_version": 7},
    }
    membership_rows = [
        {
            "workspace_id": "ws-team",
            "tenant_id": "tenant-1",
            "role": "owner",
            "workspace_name": "Acme Deal Room",
            "updated_at": 101,
        },
        {
            "workspace_id": "ws-docs",
            "tenant_id": "tenant-2",
            "role": "member",
            "workspace_name": "Docs Workspace",
            "updated_at": 202,
        },
    ]
    workspaces = {
        "ws-team": {
            "workspace_id": "ws-team",
            "tenant_id": "tenant-1",
            "name": "Acme Deal Room",
            "workspace_type": "team",
            "metadata": {
                "shell": {
                    "preferredProfile": "deal_room_shell",
                    "defaultRoute": "/w/ws-team/runs",
                }
            },
        },
        "ws-docs": {
            "workspace_id": "ws-docs",
            "tenant_id": "tenant-2",
            "name": "Docs Workspace",
            "workspace_type": "personal",
            "metadata": {
                "workspace_traits": {
                    "operatingMode": "document_workstation",
                    "defaultSurface": "workstation",
                    "documentHeavy": True,
                }
            },
        },
    }

    monkeypatch.setattr(
        account_shell_service.auth_module,
        "get_authenticated_user_record",
        lambda current_user: {
            "id": "user-1",
            "email": "user@example.com",
            "name": "User Example",
        },
    )
    monkeypatch.setattr(
        account_shell_service.auth_module,
        "list_authenticated_workspace_memberships",
        lambda current_user: membership_rows,
    )
    monkeypatch.setattr(
        account_shell_service.auth_module,
        "get_authenticated_identity_versions",
        lambda current_user: {"membership_version": 7},
    )

    async def fake_get_workspace_by_id(workspace_id: str):
        return workspaces[workspace_id]

    monkeypatch.setattr(
        account_shell_service.control_plane_repository,
        "get_workspace_by_id",
        fake_get_workspace_by_id,
    )
    monkeypatch.setattr(
        account_shell_service.entitlements_service,
        "resolve_workspace_entitlement_state_for_workspace_id",
        lambda workspace_id, workspace=None: _entitlement_state(
            artifacts_enabled=True,
            approvals_enabled=True,
            mobile_app_enabled=True,
            telegram_channel_enabled=True,
            whatsapp_channel_enabled=False,
        ),
    )

    payload = await account_shell_service.build_account_shell_payload(current_user)

    assert payload["account"] == {
        "id": "user-1",
        "email": "user@example.com",
        "displayName": "User Example",
    }
    assert [item["workspace"]["id"] for item in payload["workspaceMemberships"]] == [
        "ws-team",
        "ws-docs",
    ]

    first = payload["workspaceMemberships"][0]
    assert first["workspace"]["label"] == "Acme Deal Room"
    assert first["workspace"]["kind"] == "team"
    assert first["role"] == "owner"
    assert first["defaultRoute"] == "/w/ws-team/runs"
    assert first["preferredShellProfileId"] == "deal_room_shell"
    assert first["setupCompleted"] is False
    assert first["requiresOnboarding"] is True
    assert first["membershipVersion"] == "7:101"
    assert "workspace.admin" in first["permissions"]

    second = payload["workspaceMemberships"][1]
    assert second["workspace"]["label"] == "Docs Workspace"
    assert second["workspace"]["kind"] == "personal"
    assert second["role"] == "member"
    assert second["defaultRoute"] == "/w/ws-docs/workstation"
    assert second["preferredShellProfileId"] == "document_workstation_shell"
    assert second["setupCompleted"] is False
    assert second["requiresOnboarding"] is True
    assert second["membershipVersion"] == "7:202"
    assert "documents.workstation.use" in second["permissions"]


@pytest.mark.anyio
async def test_auth_account_shell_route_returns_payload(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_auth.get_current_user] = lambda: {
        "auth_type": "bearer",
        "user_id": "user-1",
    }

    async def fake_build_account_shell_payload(current_user):
        assert current_user["user_id"] == "user-1"
        return {
            "account": {
                "id": "user-1",
                "email": "user@example.com",
                "displayName": "User Example",
            },
            "workspaceMemberships": [
                {
                    "workspace": {
                        "id": "ws-1",
                        "tenantId": "tenant-1",
                        "label": "Workspace One",
                        "kind": "personal",
                    },
                    "role": "owner",
                    "permissions": ["workspace.read"],
                    "membershipVersion": "3:4",
                    "defaultRoute": "/w/ws-1/chat",
                    "preferredShellProfileId": "personal_shell",
                    "setupCompleted": True,
                    "requiresOnboarding": False,
                }
            ],
        }

    monkeypatch.setattr(routes_auth, "build_account_shell_payload", fake_build_account_shell_payload)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/auth/account-shell")

    assert response.status_code == 200
    assert response.json()["workspaceMemberships"][0]["defaultRoute"] == "/w/ws-1/chat"


@pytest.mark.anyio
async def test_auth_account_shell_route_requires_auth():
    app = _build_app()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/auth/account-shell")

    assert response.status_code == 401


@pytest.mark.anyio
async def test_build_account_shell_payload_handles_datetime_membership_versions_and_workspace_tenant_fallback(
    monkeypatch: pytest.MonkeyPatch,
):
    current_user = {
        "auth_type": "bearer",
        "user_id": "user-1",
        "identity_versions": {"membership_version": 9},
    }
    updated_at = datetime(2026, 4, 13, 12, 0, tzinfo=timezone.utc)
    membership_rows = [
        {
            "workspace_id": "ws-1",
            "role": "member",
            "workspace_name": "Fallback Tenant Workspace",
            "updated_at": updated_at,
        },
    ]
    workspace_record = {
        "workspace_id": "ws-1",
        "tenant_id": "tenant-1",
        "name": "Fallback Tenant Workspace",
        "workspace_type": "personal",
        "metadata": {
            "workspace_traits": {
                "operatingMode": "personal",
                "defaultSurface": "chat",
            }
        },
    }

    monkeypatch.setattr(
        account_shell_service.auth_module,
        "get_authenticated_user_record",
        lambda current_user: {
            "id": "user-1",
            "email": "user@example.com",
        },
    )
    monkeypatch.setattr(
        account_shell_service.auth_module,
        "list_authenticated_workspace_memberships",
        lambda current_user: membership_rows,
    )
    monkeypatch.setattr(
        account_shell_service.auth_module,
        "get_authenticated_identity_versions",
        lambda current_user: {"membership_version": 9},
    )
    async def fake_get_workspace_by_id(workspace_id: str):
        return workspace_record

    monkeypatch.setattr(
        account_shell_service.control_plane_repository,
        "get_workspace_by_id",
        fake_get_workspace_by_id,
    )
    monkeypatch.setattr(
        account_shell_service.entitlements_service,
        "resolve_workspace_entitlement_state_for_workspace_id",
        lambda workspace_id, workspace=None: _entitlement_state(
            artifacts_enabled=False,
            approvals_enabled=False,
            mobile_app_enabled=True,
        ),
    )

    payload = await account_shell_service.build_account_shell_payload(current_user)

    assert payload["workspaceMemberships"][0]["workspace"]["tenantId"] == "tenant-1"
    assert payload["workspaceMemberships"][0]["membershipVersion"] == f"9:{int(updated_at.timestamp())}"


@pytest.mark.anyio
async def test_build_account_shell_payload_skips_invalid_memberships_without_failing_shell(
    monkeypatch: pytest.MonkeyPatch,
):
    current_user = {
        "auth_type": "bearer",
        "user_id": "user-1",
        "identity_versions": {"membership_version": 11},
    }
    membership_rows = [
        {
            "workspace_id": "",
            "tenant_id": "tenant-missing-id",
            "role": "member",
            "workspace_name": "Broken Membership",
            "updated_at": 10,
        },
        {
            "workspace_id": "ws-bad-tenant",
            "role": "member",
            "workspace_name": "Broken Tenant Membership",
            "updated_at": 11,
        },
        {
            "workspace_id": "ws-good",
            "tenant_id": "tenant-1",
            "role": "owner",
            "workspace_name": "Healthy Workspace",
            "updated_at": 12,
        },
    ]
    workspaces = {
        "ws-bad-tenant": {
            "workspace_id": "ws-bad-tenant",
            "name": "Broken Tenant Membership",
            "workspace_type": "personal",
        },
        "ws-good": {
            "workspace_id": "ws-good",
            "tenant_id": "tenant-1",
            "name": "Healthy Workspace",
            "workspace_type": "personal",
            "metadata": {
                "workspace_traits": {
                    "operatingMode": "personal",
                    "defaultSurface": "chat",
                }
            },
        },
    }

    monkeypatch.setattr(
        account_shell_service.auth_module,
        "get_authenticated_user_record",
        lambda current_user: {
            "id": "user-1",
            "email": "user@example.com",
        },
    )
    monkeypatch.setattr(
        account_shell_service.auth_module,
        "list_authenticated_workspace_memberships",
        lambda current_user: membership_rows,
    )
    monkeypatch.setattr(
        account_shell_service.auth_module,
        "get_authenticated_identity_versions",
        lambda current_user: {"membership_version": 11},
    )

    async def fake_get_workspace_by_id(workspace_id: str):
        return workspaces.get(workspace_id)

    monkeypatch.setattr(
        account_shell_service.control_plane_repository,
        "get_workspace_by_id",
        fake_get_workspace_by_id,
    )
    monkeypatch.setattr(
        account_shell_service.entitlements_service,
        "resolve_workspace_entitlement_state_for_workspace_id",
        lambda workspace_id, workspace=None: _entitlement_state(
            artifacts_enabled=False,
            approvals_enabled=False,
            mobile_app_enabled=True,
        ),
    )

    payload = await account_shell_service.build_account_shell_payload(current_user)

    assert payload["account"]["id"] == "user-1"
    assert [item["workspace"]["id"] for item in payload["workspaceMemberships"]] == ["ws-good"]
    assert payload["workspaceMemberships"][0]["membershipVersion"] == "11:12"


@pytest.mark.anyio
async def test_build_account_shell_payload_does_not_call_workspace_role_when_membership_role_present(
    monkeypatch: pytest.MonkeyPatch,
):
    current_user = {
        "auth_type": "bearer",
        "user_id": "user-1",
        "identity_versions": {"membership_version": 13},
    }
    membership_rows = [
        {
            "workspace_id": "ws-1",
            "tenant_id": "tenant-1",
            "role": "owner",
            "workspace_name": "Primary Workspace",
            "updated_at": 14,
        }
    ]

    monkeypatch.setattr(
        account_shell_service.auth_module,
        "get_authenticated_user_record",
        lambda current_user: {
            "id": "user-1",
            "email": "user@example.com",
        },
    )
    monkeypatch.setattr(
        account_shell_service.auth_module,
        "list_authenticated_workspace_memberships",
        lambda current_user: membership_rows,
    )
    monkeypatch.setattr(
        account_shell_service.auth_module,
        "get_authenticated_identity_versions",
        lambda current_user: {"membership_version": 13, "auth_version": 1, "provider_scope_version": 1},
    )
    monkeypatch.setattr(
        account_shell_service.auth_module,
        "workspace_role",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("workspace_role should not be called")),
    )

    async def fake_get_workspace_by_id(_workspace_id: str):
        return {
            "workspace_id": "ws-1",
            "tenant_id": "tenant-1",
            "name": "Primary Workspace",
            "workspace_type": "personal",
            "metadata": {},
        }

    monkeypatch.setattr(
        account_shell_service.control_plane_repository,
        "get_workspace_by_id",
        fake_get_workspace_by_id,
    )
    monkeypatch.setattr(
        account_shell_service.entitlements_service,
        "resolve_workspace_entitlement_state_for_workspace_id",
        lambda workspace_id, workspace=None: _entitlement_state(mobile_app_enabled=True),
    )

    payload = await account_shell_service.build_account_shell_payload(current_user)

    assert payload["workspaceMemberships"][0]["role"] == "owner"


@pytest.mark.anyio
async def test_build_account_shell_payload_reuses_short_ttl_cache_for_identical_context(
    monkeypatch: pytest.MonkeyPatch,
):
    current_user = {
        "auth_type": "bearer",
        "user_id": "user-1",
        "identity_versions": {"membership_version": 15, "auth_version": 2, "provider_scope_version": 3},
    }
    membership_rows = [
        {
            "workspace_id": "ws-1",
            "tenant_id": "tenant-1",
            "role": "member",
            "workspace_name": "Cached Workspace",
            "updated_at": 100,
        }
    ]
    workspace_fetch_count = {"count": 0}

    monkeypatch.setattr(
        account_shell_service.auth_module,
        "get_authenticated_user_record",
        lambda current_user: {
            "id": "user-1",
            "email": "user@example.com",
        },
    )
    monkeypatch.setattr(
        account_shell_service.auth_module,
        "list_authenticated_workspace_memberships",
        lambda current_user: membership_rows,
    )
    monkeypatch.setattr(
        account_shell_service.auth_module,
        "get_authenticated_identity_versions",
        lambda current_user: {"membership_version": 15, "auth_version": 2, "provider_scope_version": 3},
    )

    async def fake_get_workspace_by_id(_workspace_id: str):
        workspace_fetch_count["count"] += 1
        return {
            "workspace_id": "ws-1",
            "tenant_id": "tenant-1",
            "name": "Cached Workspace",
            "workspace_type": "personal",
            "metadata": {},
        }

    monkeypatch.setattr(
        account_shell_service.control_plane_repository,
        "get_workspace_by_id",
        fake_get_workspace_by_id,
    )
    monkeypatch.setattr(
        account_shell_service.entitlements_service,
        "resolve_workspace_entitlement_state_for_workspace_id",
        lambda workspace_id, workspace=None: _entitlement_state(mobile_app_enabled=True),
    )

    first_payload = await account_shell_service.build_account_shell_payload(current_user)
    second_payload = await account_shell_service.build_account_shell_payload(current_user)

    assert workspace_fetch_count["count"] == 1
    assert first_payload == second_payload


@pytest.mark.anyio
async def test_build_account_shell_payload_cache_is_user_scoped(
    monkeypatch: pytest.MonkeyPatch,
):
    account_shell_service.ACCOUNT_SHELL_CACHE.clear()
    memberships_by_user = {
        "user-1": [
            {
                "workspace_id": "ws-u1",
                "tenant_id": "tenant-u1",
                "role": "owner",
                "workspace_name": "Workspace One",
                "updated_at": 1,
            }
        ],
        "user-2": [
            {
                "workspace_id": "ws-u2",
                "tenant_id": "tenant-u2",
                "role": "member",
                "workspace_name": "Workspace Two",
                "updated_at": 1,
            }
        ],
    }

    monkeypatch.setattr(
        account_shell_service.auth_module,
        "get_authenticated_user_record",
        lambda current_user: {
            "id": current_user["user_id"],
            "email": f"{current_user['user_id']}@example.com",
        },
    )
    monkeypatch.setattr(
        account_shell_service.auth_module,
        "list_authenticated_workspace_memberships",
        lambda current_user: memberships_by_user[current_user["user_id"]],
    )
    monkeypatch.setattr(
        account_shell_service.auth_module,
        "get_authenticated_identity_versions",
        lambda current_user: {"membership_version": 16},
    )

    async def fake_get_workspace_by_id(workspace_id: str):
        if workspace_id == "ws-u1":
            return {
                "workspace_id": "ws-u1",
                "tenant_id": "tenant-u1",
                "name": "Workspace One",
                "workspace_type": "personal",
                "metadata": {},
            }
        return {
            "workspace_id": "ws-u2",
            "tenant_id": "tenant-u2",
            "name": "Workspace Two",
            "workspace_type": "personal",
            "metadata": {},
        }

    monkeypatch.setattr(
        account_shell_service.control_plane_repository,
        "get_workspace_by_id",
        fake_get_workspace_by_id,
    )
    monkeypatch.setattr(
        account_shell_service.entitlements_service,
        "resolve_workspace_entitlement_state_for_workspace_id",
        lambda workspace_id, workspace=None: _entitlement_state(mobile_app_enabled=True),
    )

    payload_user_1 = await account_shell_service.build_account_shell_payload(
        {"auth_type": "bearer", "user_id": "user-1", "identity_versions": {"membership_version": 16}},
    )
    payload_user_2 = await account_shell_service.build_account_shell_payload(
        {"auth_type": "bearer", "user_id": "user-2", "identity_versions": {"membership_version": 16}},
    )

    assert payload_user_1["workspaceMemberships"][0]["workspace"]["id"] == "ws-u1"
    assert payload_user_2["workspaceMemberships"][0]["workspace"]["id"] == "ws-u2"
