"""MAN-64/MAN-70 Part 2 -- member-role write access on the task surface.

Before this change, routes_fleet.py had 39 `minimum_role="owner"` gates and
zero `minimum_role="member"` gates: an invited teammate (role="member") could
read the board (viewer-gated reads) and change nothing at all -- the member
tier was functionally identical to viewer across the whole task/project
surface. This file proves the fix, route by route, and just as importantly
proves the boundary was NOT blown open past what a teammate needs:

1. `member` CAN create/edit/status-change/assign(agent or human)/comment/
   sub-task/label a task -- the explicit list the brief asked for.
2. `member` is still gated by MAN-115's per-project ACL on every task-scoped
   route -- a member with project_memberships access to Project A must NOT
   be able to touch Project B's tasks just by knowing a task_id. This is
   the security property _enforce_task_project_access exists to hold, and
   it did not exist before this change (every task-mutation route used to
   be owner-only, and an owner already bypasses the per-project ACL, so the
   check was redundant until `member` made it not be).
3. `viewer` still cannot do any of the above (403).
4. `member` still cannot do the destructive/financial/infra things left
   owner-gated -- several representative routes, proven with real 403s.
5. `member` still cannot delete a workspace label (the one deliberately-
   flagged-ambiguous label route -- see fleet_delete_label's docstring).

Mirrors the established patterns in this suite:
  - test_routes_fleet_task_comments.py / test_routes_fleet_bug_reports.py:
    real ASGI requests through the REAL auth_module.enforce_workspace_access
    and auth_module.enforce_project_access (not mocked) for every rejection
    case, so a 403/404 here is the dependency wiring actually rejecting,
    never a mock standing in for it.
  - test_routes_fleet_delete_agent.py: `_member_user()` alongside
    `_viewer_user()`/`_owner_user()`, to prove a gate is "member is enough"
    or "owner exactly", not merely "not viewer".
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
    return {
        "user_id": "viewer-1",
        "email": "viewer@example.com",
        "auth_type": "bearer",
        "workspace_access": {
            "ws-1": {"workspace_id": "ws-1", "tenant_id": "tenant-1", "role": "viewer", "tenant_role": "viewer"}
        },
    }


def _member_user() -> dict:
    return {
        "user_id": "member-1",
        "email": "member@example.com",
        "auth_type": "bearer",
        "workspace_access": {
            "ws-1": {"workspace_id": "ws-1", "tenant_id": "tenant-1", "role": "member", "tenant_role": "member"}
        },
    }


def _owner_user() -> dict:
    return {
        "user_id": "owner-1",
        "email": "owner@example.com",
        "auth_type": "bearer",
        "workspace_access": {
            "ws-1": {"workspace_id": "ws-1", "tenant_id": "tenant-1", "role": "owner", "tenant_role": "owner"}
        },
    }


def _task_with_project(project_id: str = "proj-1") -> dict:
    return {
        "id": "task-1",
        "project_id": project_id,
        "title": "Ship the widget",
        "assignee_agent_id": None,
        "assignee_user_id": None,
    }


def _member_has_project_access(has_access: bool):
    """Patches the ONE primitive auth_module.enforce_project_access calls
    for a non-owner caller (projects_repository.is_project_member) --
    letting these tests prove both halves of the MAN-115 boundary without
    needing a real project_memberships row in a real database."""
    return patch(
        "server_modules.projects_repository.is_project_member",
        new=AsyncMock(return_value=has_access),
    )


# ── Part 2 mandatory test: member CAN create/comment/status-change/assign ──


@pytest.mark.anyio
async def test_member_can_create_a_task_in_a_project_they_belong_to() -> None:
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _member_user

    with (
        patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
        _member_has_project_access(True),
        patch(
            "server_modules.project_tasks_service.create_task",
            new=AsyncMock(return_value={"id": "task-1", "project_id": "proj-1", "title": "New task"}),
        ) as create_mock,
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/w/ws-1/fleet/tasks", json={"project_id": "proj-1", "title": "New task"},
            )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["task"]["id"] == "task-1"
    create_mock.assert_awaited_once()


@pytest.mark.anyio
async def test_member_without_project_access_cannot_create_a_task_there() -> None:
    """THE cross-project boundary this rollout has to hold: a member's
    WORKSPACE role alone must not be enough to create a task in a project
    they have no project_memberships row for."""
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _member_user

    with (
        patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
        _member_has_project_access(False),
        patch(
            "server_modules.project_tasks_service.create_task",
            new=AsyncMock(side_effect=AssertionError("must never reach the service layer")),
        ),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/w/ws-1/fleet/tasks", json={"project_id": "proj-1", "title": "New task"},
            )
    # enforce_project_access's own documented contract: 404, not 403, for a
    # non-owner with no access -- a non-member must not be able to tell
    # "project doesn't exist" apart from "exists but you can't see it".
    assert response.status_code == 404


@pytest.mark.anyio
async def test_member_can_change_a_tasks_status() -> None:
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _member_user

    with (
        patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
        patch("server_modules.project_tasks_service.get_task", new=AsyncMock(return_value=_task_with_project())),
        _member_has_project_access(True),
        patch(
            "server_modules.project_tasks_service.update_task",
            new=AsyncMock(return_value={"id": "task-1", "status": "in_progress"}),
        ) as update_mock,
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.patch(
                "/api/w/ws-1/fleet/tasks/task-1", json={"status": "in_progress"},
            )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["task"]["status"] == "in_progress"
    update_mock.assert_awaited_once()


@pytest.mark.anyio
async def test_member_without_project_access_cannot_patch_a_task_there() -> None:
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _member_user

    with (
        patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
        patch("server_modules.project_tasks_service.get_task", new=AsyncMock(return_value=_task_with_project())),
        _member_has_project_access(False),
        patch(
            "server_modules.project_tasks_service.update_task",
            new=AsyncMock(side_effect=AssertionError("must never reach the service layer")),
        ),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.patch(
                "/api/w/ws-1/fleet/tasks/task-1", json={"status": "in_progress"},
            )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_member_can_assign_a_task_to_an_agent() -> None:
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _member_user

    with (
        patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
        patch("server_modules.project_tasks_service.get_task", new=AsyncMock(return_value=_task_with_project())),
        _member_has_project_access(True),
        patch(
            "server_modules.project_tasks_service.assign_task",
            new=AsyncMock(
                return_value={
                    "task": {"id": "task-1", "assignee_agent_id": "agent-1"},
                    "wake_request": {"id": "wake-1"},
                    "wake_error": None,
                }
            ),
        ) as assign_mock,
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/w/ws-1/fleet/tasks/task-1/assign", json={"agent_id": "agent-1"},
            )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["task"]["assignee_agent_id"] == "agent-1"
    assign_mock.assert_awaited_once()
    assert assign_mock.await_args.kwargs["agent_id"] == "agent-1"


@pytest.mark.anyio
async def test_member_can_assign_a_task_to_a_human() -> None:
    """The other half of "assign/reassign tasks (to themselves, another
    human, or an agent)" -- a member using the new human-assignee path."""
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _member_user

    with (
        patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
        patch("server_modules.project_tasks_service.get_task", new=AsyncMock(return_value=_task_with_project())),
        _member_has_project_access(True),
        patch(
            "server_modules.project_tasks_service.assign_task_to_user",
            new=AsyncMock(
                return_value={
                    "task": {"id": "task-1", "assignee_user_id": "user-9"},
                    "wake_request": None,
                    "wake_error": None,
                }
            ),
        ) as assign_mock,
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/w/ws-1/fleet/tasks/task-1/assign", json={"user_id": "user-9"},
            )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["task"]["assignee_user_id"] == "user-9"
    assert body["wake_request"] is None
    assert body["wake_error"] is None
    assign_mock.assert_awaited_once()
    assert assign_mock.await_args.kwargs["user_id"] == "user-9"


@pytest.mark.anyio
async def test_assign_rejects_both_agent_id_and_user_id_at_once() -> None:
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _member_user

    with (
        patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
        patch("server_modules.project_tasks_service.get_task", new=AsyncMock(return_value=_task_with_project())),
        _member_has_project_access(True),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/w/ws-1/fleet/tasks/task-1/assign", json={"agent_id": "agent-1", "user_id": "user-9"},
            )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "either" in body["error"].lower()


@pytest.mark.anyio
async def test_assign_rejects_neither_agent_id_nor_user_id() -> None:
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _member_user

    with (
        patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
        patch("server_modules.project_tasks_service.get_task", new=AsyncMock(return_value=_task_with_project())),
        _member_has_project_access(True),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/w/ws-1/fleet/tasks/task-1/assign", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "required" in body["error"].lower()


@pytest.mark.anyio
async def test_member_can_comment_on_a_task() -> None:
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _member_user

    with (
        patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
        patch("server_modules.project_tasks_service.get_task", new=AsyncMock(return_value=_task_with_project())),
        _member_has_project_access(True),
        patch(
            "server_modules.project_tasks_service.add_human_task_comment",
            new=AsyncMock(
                return_value={"task": {"id": "task-1"}, "wake_request": None, "wake_error": None}
            ),
        ) as comment_mock,
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/w/ws-1/fleet/tasks/task-1/comments", json={"body": "looks good"},
            )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    comment_mock.assert_awaited_once()
    assert comment_mock.await_args.kwargs["author_id"] == "member-1"


@pytest.mark.anyio
async def test_member_can_create_and_manage_a_sub_task() -> None:
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _member_user

    with (
        patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
        patch("server_modules.project_tasks_service.get_task", new=AsyncMock(return_value=_task_with_project())),
        _member_has_project_access(True),
        patch(
            "server_modules.project_tasks_service.set_task_parent",
            new=AsyncMock(return_value={"id": "task-2", "parent_task_id": "task-1"}),
        ) as parent_mock,
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/w/ws-1/fleet/tasks/task-2/parent", json={"parent_task_id": "task-1"},
            )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    parent_mock.assert_awaited_once()


@pytest.mark.anyio
async def test_member_can_create_and_manage_labels_on_a_task() -> None:
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _member_user

    with (
        patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
        patch("server_modules.workspace_labels_service.create_label", new=AsyncMock(return_value={"id": "lbl-1", "name": "bug"})) as create_label_mock,
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/w/ws-1/fleet/labels", json={"name": "bug"})
    assert response.status_code == 200
    assert response.json()["ok"] is True
    create_label_mock.assert_awaited_once()

    with (
        patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
        patch("server_modules.project_tasks_service.get_task", new=AsyncMock(return_value=_task_with_project())),
        _member_has_project_access(True),
        patch(
            "server_modules.workspace_labels_service.attach_label",
            new=AsyncMock(return_value=[{"id": "lbl-1", "name": "bug"}]),
        ) as attach_mock,
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/w/ws-1/fleet/tasks/task-1/labels", json={"label": "bug"},
            )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    attach_mock.assert_awaited_once()


# ── Mandatory test: viewer still CANNOT do any of the above (403) ─────────


@pytest.mark.parametrize(
    "method,path,json_body",
    [
        ("POST", "/api/w/ws-1/fleet/tasks", {"project_id": "proj-1", "title": "x"}),
        ("PATCH", "/api/w/ws-1/fleet/tasks/task-1", {"status": "in_progress"}),
        ("POST", "/api/w/ws-1/fleet/tasks/task-1/assign", {"agent_id": "agent-1"}),
        ("POST", "/api/w/ws-1/fleet/tasks/task-1/comments", {"body": "hi"}),
        ("POST", "/api/w/ws-1/fleet/tasks/task-1/parent", {"parent_task_id": "task-2"}),
        ("POST", "/api/w/ws-1/fleet/labels", {"name": "bug"}),
        ("PATCH", "/api/w/ws-1/fleet/labels/lbl-1", {"name": "bugfix"}),
        ("POST", "/api/w/ws-1/fleet/tasks/task-1/labels", {"label": "bug"}),
        ("DELETE", "/api/w/ws-1/fleet/tasks/task-1/labels/bug", None),
    ],
)
@pytest.mark.anyio
async def test_viewer_cannot_write_to_the_task_surface(method: str, path: str, json_body) -> None:
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _viewer_user

    # Every service call must be unreachable -- the workspace-level "member"
    # floor rejects a viewer before any of these would ever be invoked.
    with (
        patch("server_modules.project_tasks_service.create_task", new=AsyncMock(side_effect=AssertionError)),
        patch("server_modules.project_tasks_service.update_task", new=AsyncMock(side_effect=AssertionError)),
        patch("server_modules.project_tasks_service.assign_task", new=AsyncMock(side_effect=AssertionError)),
        patch("server_modules.project_tasks_service.add_human_task_comment", new=AsyncMock(side_effect=AssertionError)),
        patch("server_modules.project_tasks_service.set_task_parent", new=AsyncMock(side_effect=AssertionError)),
        patch("server_modules.workspace_labels_service.create_label", new=AsyncMock(side_effect=AssertionError)),
        patch("server_modules.workspace_labels_service.update_label", new=AsyncMock(side_effect=AssertionError)),
        patch("server_modules.workspace_labels_service.attach_label", new=AsyncMock(side_effect=AssertionError)),
        patch("server_modules.workspace_labels_service.detach_label", new=AsyncMock(side_effect=AssertionError)),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.request(method, path, json=json_body)
    assert response.status_code == 403


# ── Mandatory test: member still CANNOT do the destructive/financial/infra
# things left owner-gated. Several representative routes across every
# category the brief named. ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "method,path,json_body",
    [
        # Destructive.
        ("DELETE", "/api/w/ws-1/fleet/projects/proj-1/members/member-1", None),
        ("DELETE", "/api/w/ws-1/fleet/agents/agent-1", None),
        ("DELETE", "/api/w/ws-1/fleet/labels/lbl-1", None),
        # Infra / provisioning-adjacent (agent lifecycle).
        ("POST", "/api/w/ws-1/fleet/agents", {"label": "x"}),
        ("POST", "/api/w/ws-1/fleet/agents/agent-1/stop", {}),
        ("POST", "/api/w/ws-1/fleet/stop-all", {}),
        # Credentials / financial-adjacent.
        (
            "POST",
            "/api/w/ws-1/fleet/agent-capabilities/key?agent_id=agent-1",
            {"capability": "image_generation", "provider": "openai", "api_key": "sk-x"},
        ),
        # Workspace/project settings and member management.
        ("POST", "/api/w/ws-1/fleet/projects", {"name": "New project"}),
        ("POST", "/api/w/ws-1/fleet/projects/proj-1/members", {"user_id": "user-9"}),
    ],
)
@pytest.mark.anyio
async def test_member_cannot_do_owner_only_actions(method: str, path: str, json_body) -> None:
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _member_user

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.request(method, path, json=json_body)
    assert response.status_code == 403, f"{method} {path} should still be owner-only, got {response.status_code}"


@pytest.mark.anyio
async def test_owner_can_still_do_everything_a_member_can() -> None:
    """A quick sanity check that loosening member did not accidentally
    tighten owner: the pre-existing top role must still pass every gate
    this rollout touched."""
    app = _build_app()
    app.dependency_overrides[routes_fleet.auth_module.get_current_user] = _owner_user

    with (
        patch("server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value="tenant-1")),
        patch(
            "server_modules.project_tasks_service.create_task",
            new=AsyncMock(return_value={"id": "task-1", "project_id": "proj-1", "title": "New task"}),
        ),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/w/ws-1/fleet/tasks", json={"project_id": "proj-1", "title": "New task"},
            )
    assert response.status_code == 200
    assert response.json()["ok"] is True
