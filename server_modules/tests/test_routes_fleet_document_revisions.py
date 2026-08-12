"""GET /api/w/{workspace_id}/fleet/documents/{document_id}/revisions -- the
human-facing HTTP route this gap closes.

Before this route existed, project_document_revisions (patch-native diffs,
changed_by_type human/agent/external_agent/system -- feat/document-mcp-
tools-and-revisions) had exactly ONE production caller:
mcp_server.py's empyralis_list_document_revisions, an MCP tool only an
AGENT can call. routes_fleet.py had five document routes (list/get/create/
patch/delete) and none of them read revisions, and `grep -rn "revision"
frontend/` returned zero matches -- CLAUDE.md's own "built, tested, and
never wired" shape: a human editing a document could not see what changed,
who changed it, or when, while an agent editing the same document could.

Mirrors test_routes_fleet_task_comments.py's established patterns: real
ASGI requests through the REAL auth_module.enforce_workspace_access /
enforce_project_access (never mocked) for the authorization cases, and the
"service failure surfaces as ok:false, not a 500" contract every route in
this router shares. `_owner_user` is used for the allowed-caller tests
specifically because auth.enforce_project_access's own docstring documents
that a workspace OWNER bypasses the project_memberships lookup entirely --
so these tests exercise the real dependency chain without needing a fake
database row for project membership.
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


def _owner_user() -> dict:
    return {
        "user_id": "owner-1",
        "email": "owner@example.com",
        "auth_type": "bearer",
        "workspace_access": {
            "ws-1": {
                "workspace_id": "ws-1",
                "tenant_id": "tenant-1",
                "role": "owner",
                "tenant_role": "owner",
            }
        },
    }


def _intruder_user() -> dict:
    """Authenticated, but the only membership is a DIFFERENT workspace/
    tenant -- the mandatory cross-workspace case: this caller must get
    nothing back and must never reach the repository layer just by putting
    ws-1's id in the URL."""
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


_SAMPLE_REVISIONS = [
    {
        "id": "docrev_2",
        "document_id": "doc-1",
        "project_id": "proj-1",
        "title": "Runbook",
        "diff": "--- before\n+++ after\n@@ -1 +1 @@\n-old line\n+new line",
        "changed_by_type": "human",
        "changed_by_id": "owner-1",
        "changed_by_display_name": None,
        "revision_number": 2,
        "created_at": "2026-08-12T10:00:00Z",
    },
    {
        "id": "docrev_1",
        "document_id": "doc-1",
        "project_id": "proj-1",
        "title": "Runbook",
        "diff": "--- before\n+++ after\n@@ -0,0 +1 @@\n+old line",
        "changed_by_type": "agent",
        "changed_by_id": "agent-1",
        "changed_by_display_name": "Ops Agent",
        "revision_number": 1,
        "created_at": "2026-08-11T09:00:00Z",
    },
]


@pytest.mark.anyio
async def test_unauthenticated_request_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORION_AUTH_REQUIRED", "1")
    monkeypatch.delenv("EMPYRALIS_DEPLOY_ENV", raising=False)
    monkeypatch.delenv("ORION_ENV", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("NODE_ENV", raising=False)

    app = _build_app()
    # No dependency_overrides -- exercises the REAL auth_module.get_current_user.

    with (
        patch(
            "server_modules.project_documents_repository.get_document",
            new=AsyncMock(side_effect=AssertionError("must never reach the repository layer")),
        ),
        patch(
            "server_modules.project_documents_repository.list_document_revisions",
            new=AsyncMock(side_effect=AssertionError("must never reach the repository layer")),
        ),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/w/ws-victim/fleet/documents/doc-1/revisions")
    assert response.status_code == 401


@pytest.mark.anyio
async def test_wrong_workspace_request_is_rejected_with_zero_downstream_calls() -> None:
    """The mandatory authorization case: a caller whose only membership is a
    DIFFERENT workspace/tenant must not be able to read ws-1's document
    revisions by changing the workspace_id path segment. Asserts BOTH
    repository calls the route can reach (get_document via
    _enforce_document_project_access, and list_document_revisions) have
    ZERO awaits -- not just that the response is a rejection."""
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _intruder_user

    get_document_mock = AsyncMock(side_effect=AssertionError("must never reach the repository layer"))
    list_revisions_mock = AsyncMock(side_effect=AssertionError("must never reach the repository layer"))
    with (
        patch("server_modules.project_documents_repository.get_document", new=get_document_mock),
        patch("server_modules.project_documents_repository.list_document_revisions", new=list_revisions_mock),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/w/ws-1/fleet/documents/doc-1/revisions")

    assert response.status_code == 403
    assert get_document_mock.await_count == 0
    assert list_revisions_mock.await_count == 0


@pytest.mark.anyio
async def test_document_not_found_returns_ok_false_and_never_lists_revisions() -> None:
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _owner_user

    list_revisions_mock = AsyncMock(side_effect=AssertionError("must never list revisions for a document that was not found"))
    with (
        patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
        patch("server_modules.project_documents_repository.get_document", new=AsyncMock(return_value=None)),
        patch("server_modules.project_documents_repository.list_document_revisions", new=list_revisions_mock),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/w/ws-1/fleet/documents/doc-missing/revisions")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "not found" in body["error"].lower()
    assert list_revisions_mock.await_count == 0


@pytest.mark.anyio
async def test_owner_can_list_revisions_newest_first() -> None:
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _owner_user

    document = {"id": "doc-1", "project_id": "proj-1", "title": "Runbook"}
    with (
        patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
        patch("server_modules.project_documents_repository.get_document", new=AsyncMock(return_value=document)),
        patch(
            "server_modules.project_documents_repository.list_document_revisions",
            new=AsyncMock(return_value=_SAMPLE_REVISIONS),
        ) as list_revisions_mock,
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/w/ws-1/fleet/documents/doc-1/revisions")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert [r["revision_number"] for r in body["revisions"]] == [2, 1]
    assert "body" not in body["revisions"][0]  # include_body was never requested -- no restore surface

    list_revisions_mock.assert_awaited_once()
    kwargs = list_revisions_mock.await_args.kwargs
    assert kwargs["tenant_id"] == "tenant-1"
    assert kwargs["workspace_id"] == "ws-1"
    assert kwargs["document_id"] == "doc-1"
    assert kwargs["limit"] == 50
    assert "include_body" not in kwargs  # default (False) -- never pass True from this route


@pytest.mark.anyio
async def test_limit_query_param_is_forwarded() -> None:
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _owner_user

    document = {"id": "doc-1", "project_id": "proj-1", "title": "Runbook"}
    with (
        patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
        patch("server_modules.project_documents_repository.get_document", new=AsyncMock(return_value=document)),
        patch(
            "server_modules.project_documents_repository.list_document_revisions",
            new=AsyncMock(return_value=[]),
        ) as list_revisions_mock,
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/w/ws-1/fleet/documents/doc-1/revisions?limit=5")

    assert response.status_code == 200
    assert list_revisions_mock.await_args.kwargs["limit"] == 5


@pytest.mark.anyio
async def test_limit_out_of_bounds_is_rejected_by_validation() -> None:
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _owner_user

    with (
        patch(
            "server_modules.project_documents_repository.get_document",
            new=AsyncMock(side_effect=AssertionError("validation must reject before the handler body runs")),
        ),
        patch(
            "server_modules.project_documents_repository.list_document_revisions",
            new=AsyncMock(side_effect=AssertionError("validation must reject before the handler body runs")),
        ),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/w/ws-1/fleet/documents/doc-1/revisions?limit=0")
    assert response.status_code == 422


@pytest.mark.anyio
async def test_service_failure_is_surfaced_as_ok_false_not_500() -> None:
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _owner_user

    document = {"id": "doc-1", "project_id": "proj-1", "title": "Runbook"}
    with (
        patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
        patch("server_modules.project_documents_repository.get_document", new=AsyncMock(return_value=document)),
        patch(
            "server_modules.project_documents_repository.list_document_revisions",
            new=AsyncMock(side_effect=RuntimeError("control plane unavailable")),
        ),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/w/ws-1/fleet/documents/doc-1/revisions")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "control plane unavailable" in body["error"]
