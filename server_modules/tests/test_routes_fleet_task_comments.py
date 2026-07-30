"""POST /api/w/{workspace_id}/fleet/tasks/{task_id}/comments -- the
human->agent comment channel's HTTP route (MAN-64 "FOCUS: Tasks -> Agents",
MAN-70 "FOCUS: Multiplayer Projects").

Before this route existed, an agent could post into task.metadata.comments
via project_task__comment / empyralis_comment_on_task, but there was no HTTP
route at all for a human to do the same -- routes_fleet.py had zero mentions
of "comment", and TaskDetailView.tsx rendered the Activity feed read-only
with a note saying exactly that.

Mirrors the two established patterns in this test suite:
  - test_routes_fleet_auth_guard.py: real ASGI requests through the REAL
    auth_module.enforce_workspace_access (not mocked), proving the
    dependency wiring itself rejects an unauthenticated or wrong-workspace/
    wrong-tenant caller -- the mandatory "a user outside the workspace/
    tenant cannot post to that task" coverage.
  - test_routes_fleet_bug_reports.py: real ASGI requests with a genuine
    workspace member whose ROLE is below what the route requires, plus the
    "service failure surfaces as ok:false, not a 500" contract every route
    in this router shares.
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


def _viewer_user() -> dict:
    """A genuine ws-1 member, but below the "owner" minimum every other
    task-mutation route in routes_fleet.py requires (create task, patch
    task, assign, labels attach/detach) -- comments follow that same
    convention rather than inventing a lower bar for themselves."""
    return {
        "user_id": "viewer-1",
        "email": "viewer@example.com",
        "auth_type": "bearer",
        "workspace_access": {
            "ws-1": {
                "workspace_id": "ws-1",
                "tenant_id": "tenant-1",
                "role": "viewer",
                "tenant_role": "viewer",
            }
        },
    }


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
    """Authenticated, but the only membership is a different workspace/
    tenant -- the cross-tenant shape the mandatory authorization test
    exercises: a user outside this task's workspace/tenant must not be able
    to post a comment onto it just by putting ws-1's id in the URL."""
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
async def test_unauthenticated_post_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORION_AUTH_REQUIRED", "1")
    monkeypatch.delenv("EMPYRALIS_DEPLOY_ENV", raising=False)
    monkeypatch.delenv("ORION_ENV", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("NODE_ENV", raising=False)

    app = _build_app()
    # No dependency_overrides -- exercises the REAL auth_module.get_current_user.

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/w/ws-victim/fleet/tasks/task-1/comments", json={"body": "try approach B instead"}
        )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_wrong_workspace_post_is_rejected() -> None:
    """The mandatory authorization case: a caller whose only membership is a
    DIFFERENT workspace/tenant must not be able to comment on ws-1's task by
    changing the workspace_id path segment. Exercises the genuine
    auth_module.enforce_workspace_access, not a mock."""
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _intruder_user

    with patch(
        "server_modules.project_tasks_service.add_human_task_comment",
        new=AsyncMock(side_effect=AssertionError("must never reach the service layer")),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/w/ws-1/fleet/tasks/task-1/comments", json={"body": "try approach B instead"}
            )
    assert response.status_code == 403


@pytest.mark.anyio
async def test_viewer_cannot_comment() -> None:
    """Below the "owner" minimum this router's task-mutation routes share --
    matches create/patch/assign/label, not a new, looser bar."""
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _viewer_user

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/w/ws-1/fleet/tasks/task-1/comments", json={"body": "try approach B instead"}
        )
    assert response.status_code == 403


@pytest.mark.anyio
async def test_blank_body_is_rejected_by_validation() -> None:
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _owner_user

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/w/ws-1/fleet/tasks/task-1/comments", json={"body": ""})
    assert response.status_code == 422


@pytest.mark.anyio
async def test_over_length_body_is_rejected_by_validation() -> None:
    """Mirrors add_task_comment's own 4000-char truncation -- rejecting an
    over-length comment loudly here is more honest than silently truncating
    it later without telling the caller."""
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _owner_user

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/w/ws-1/fleet/tasks/task-1/comments", json={"body": "x" * 4001}
        )
    assert response.status_code == 422


@pytest.mark.anyio
async def test_owner_can_post_a_comment() -> None:
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _owner_user

    with (
        patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
        patch(
            "server_modules.project_tasks_service.add_human_task_comment",
            new=AsyncMock(
                return_value={
                    "task": {"id": "task-1", "assignee_agent_id": "agent-1"},
                    "wake_request": {"id": "wake-1", "status": "pending"},
                    "wake_error": None,
                }
            ),
        ) as comment_mock,
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/w/ws-1/fleet/tasks/task-1/comments",
                json={"body": "try approach B instead"},
            )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["task"]["id"] == "task-1"
    assert body["wake_request"] == {"id": "wake-1", "status": "pending"}
    assert body["wake_error"] is None
    comment_mock.assert_awaited_once()
    kwargs = comment_mock.await_args.kwargs
    assert kwargs["tenant_id"] == "tenant-1"
    assert kwargs["workspace_id"] == "ws-1"
    assert kwargs["task_id"] == "task-1"
    assert kwargs["author_id"] == "owner-1"
    assert kwargs["body"] == "try approach B instead"


@pytest.mark.anyio
async def test_service_failure_is_surfaced_as_ok_false_not_500() -> None:
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _owner_user

    with (
        patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
        patch(
            "server_modules.project_tasks_service.add_human_task_comment",
            new=AsyncMock(side_effect=ValueError("Task task-1 not found in this workspace.")),
        ),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/w/ws-1/fleet/tasks/task-1/comments", json={"body": "try approach B instead"}
            )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "not found" in body["error"]
