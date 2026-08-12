from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server_modules import workflow_api

# Security review, 2026-08-13: every write route in workflow_api.py
# (create/update/delete/publish) called `enforce_workspace_access(current_user,
# workspace_id)` with NO `minimum_role` argument, which defaults to "viewer"
# (auth.py's RBAC_ROLE_ORDER = {"viewer": 0, "member": 1, "owner": 2}). A
# workspace member invited as read-only viewer could create, overwrite,
# delete, or publish workflows in that workspace -- a role, not tenancy,
# escalation, since `workflow_service` itself performs no role check of its
# own. The reads (list/get) correctly stay at the "viewer" default; only the
# four mutating routes needed `minimum_role="member"` added.


def _viewer_user() -> dict:
    return {
        "user_id": "user-viewer",
        "email": "viewer@example.com",
        "auth_type": "bearer",
        "workspace_access": {
            "ws-1": {"workspace_id": "ws-1", "tenant_id": "tenant-1", "role": "viewer"},
        },
    }


def _member_user() -> dict:
    return {
        "user_id": "user-member",
        "email": "member@example.com",
        "auth_type": "bearer",
        "workspace_access": {
            "ws-1": {"workspace_id": "ws-1", "tenant_id": "tenant-1", "role": "member"},
        },
    }


class WorkflowApiRoleBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = FastAPI()
        router = self.app.router
        workflow_api.register_workflow_routes(router)
        self.current_user = _viewer_user()
        self.app.dependency_overrides[workflow_api.require_api_key] = lambda: self.current_user
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()
        self.app.dependency_overrides.clear()

    def test_viewer_cannot_create_workflow(self) -> None:
        self.current_user = _viewer_user()
        with patch.object(workflow_api.workflow_service, "create_workflow", new=AsyncMock()) as create_mock:
            response = self.client.post(
                "/workflows",
                params={"workspaceId": "ws-1"},
                json={"name": "Escalated workflow"},
            )
        self.assertEqual(response.status_code, 403)
        create_mock.assert_not_awaited()

    def test_member_can_create_workflow(self) -> None:
        self.current_user = _member_user()
        with patch.object(
            workflow_api.workflow_service,
            "create_workflow",
            new=AsyncMock(return_value={"id": "wf-1", "workspaceId": "ws-1"}),
        ) as create_mock:
            response = self.client.post(
                "/workflows",
                params={"workspaceId": "ws-1"},
                json={"name": "Legit workflow"},
            )
        self.assertEqual(response.status_code, 200)
        create_mock.assert_awaited_once()

    def test_viewer_cannot_update_workflow(self) -> None:
        self.current_user = _viewer_user()
        with patch.object(
            workflow_api.workflow_service,
            "get_workflow",
            new=AsyncMock(return_value={"id": "wf-1", "workspaceId": "ws-1"}),
        ), patch.object(workflow_api.workflow_service, "update_workflow", new=AsyncMock()) as update_mock:
            response = self.client.patch("/workflows/wf-1", json={"name": "Renamed"})
        self.assertEqual(response.status_code, 403)
        update_mock.assert_not_awaited()

    def test_viewer_cannot_delete_workflow(self) -> None:
        self.current_user = _viewer_user()
        with patch.object(
            workflow_api.workflow_service,
            "get_workflow",
            new=AsyncMock(return_value={"id": "wf-1", "workspaceId": "ws-1"}),
        ), patch.object(workflow_api.workflow_service, "delete_workflow", new=AsyncMock()) as delete_mock:
            response = self.client.delete("/workflows/wf-1")
        self.assertEqual(response.status_code, 403)
        delete_mock.assert_not_awaited()

    def test_viewer_cannot_publish_workflow(self) -> None:
        self.current_user = _viewer_user()
        with patch.object(
            workflow_api.workflow_service,
            "get_workflow",
            new=AsyncMock(return_value={"id": "wf-1", "workspaceId": "ws-1"}),
        ), patch.object(workflow_api.workflow_service, "publish_workflow", new=AsyncMock()) as publish_mock:
            response = self.client.post("/workflows/wf-1/publish")
        self.assertEqual(response.status_code, 403)
        publish_mock.assert_not_awaited()

    def test_viewer_can_still_list_and_read_workflows(self) -> None:
        # The reads must stay at the viewer default -- this fix must not
        # regress read access for a legitimate read-only member.
        self.current_user = _viewer_user()
        with patch.object(workflow_api.workflow_service, "list_workflows", new=AsyncMock(return_value=[])):
            response = self.client.get("/workflows", params={"workspaceId": "ws-1"})
        self.assertEqual(response.status_code, 200)

        with patch.object(
            workflow_api.workflow_service,
            "get_workflow",
            new=AsyncMock(return_value={"id": "wf-1", "workspaceId": "ws-1"}),
        ):
            response = self.client.get("/workflows/wf-1")
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
