import asyncio
import sys
import types
import unittest
from unittest.mock import patch

from server_modules import sage_memory_api


class _FakeApp:
    def __init__(self) -> None:
        self.routes = {}

    def _register(self, method, path, **kwargs):
        def _decorator(fn):
            self.routes[(method, path)] = fn
            return fn

        return _decorator

    def get(self, path, **kwargs):
        return self._register("GET", path, **kwargs)

    def post(self, path, **kwargs):
        return self._register("POST", path, **kwargs)

    def patch(self, path, **kwargs):
        return self._register("PATCH", path, **kwargs)

    def delete(self, path, **kwargs):
        return self._register("DELETE", path, **kwargs)


class SageMemoryApiTests(unittest.TestCase):
    def test_list_route_enforces_workspace_and_returns_payload(self):
        fake_server = types.ModuleType("server")
        fake_server.Depends = lambda dependency: dependency
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            sage_memory_api.register_sage_memory_routes(app)
            route = app.routes[("GET", "/api/sage-memory")]
            with (
                patch("server_modules.sage_memory_api.enforce_workspace_access", return_value="workspace-1"),
                patch("server_modules.sage_memory_api.workspace_tenant_id", return_value="tenant-1"),
                patch(
                    "server_modules.sage_memory_api.list_sage_memory",
                    return_value={"items": [{"id": "memory-1"}], "categories": [], "summary": {}, "updated_at": "2026-04-15T00:00:00Z"},
                ),
            ):
                payload = asyncio.run(route(workspace_id="workspace-1", current_user={"user_id": "user-1"}))
            self.assertEqual(payload["workspace_id"], "workspace-1")
            self.assertEqual(payload["tenant_id"], "tenant-1")
            self.assertEqual(payload["items"][0]["id"], "memory-1")
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_create_route_passes_category_and_actor(self):
        fake_server = types.ModuleType("server")
        fake_server.Depends = lambda dependency: dependency
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            sage_memory_api.register_sage_memory_routes(app)
            route = app.routes[("POST", "/api/sage-memory/entries")]
            with (
                patch("server_modules.sage_memory_api.enforce_workspace_access", return_value="workspace-1"),
                patch("server_modules.sage_memory_api.workspace_tenant_id", return_value="tenant-1"),
                patch(
                    "server_modules.sage_memory_api.upsert_memory_entry",
                    return_value={"entry": {"id": "memory-1"}, "items": [], "categories": [], "summary": {}},
                ) as upsert_mock,
            ):
                payload = asyncio.run(
                    route(
                        sage_memory_api.SageMemoryEntryCreateRequest(
                            workspace_id="workspace-1",
                            category="personal_context",
                            title="Timezone",
                            content="Uses Asia/Shanghai.",
                            pinned=True,
                        ),
                        current_user={"user_id": "user-1"},
                    )
                )
            self.assertEqual(payload["workspace_id"], "workspace-1")
            self.assertEqual(upsert_mock.call_args.kwargs["category"], "personal_context")
            self.assertEqual(upsert_mock.call_args.kwargs["actor_user_id"], "user-1")
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_storage_policy_route_returns_governance_payload(self):
        fake_server = types.ModuleType("server")
        fake_server.Depends = lambda dependency: dependency
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            sage_memory_api.register_sage_memory_routes(app)
            route = app.routes[("GET", "/api/sage-memory/storage-policy")]
            with (
                patch("server_modules.sage_memory_api.enforce_workspace_access", return_value="workspace-1") as enforce_mock,
                patch("server_modules.sage_memory_api.workspace_tenant_id", return_value="tenant-1"),
                patch(
                    "server_modules.sage_memory_api.sage_memory_storage_policy",
                    return_value={"authority": "cloud_canonical", "max_entries": 50},
                ),
            ):
                payload = asyncio.run(route(workspace_id="workspace-1", current_user={"user_id": "user-1"}))
            self.assertEqual(payload["authority"], "cloud_canonical")
            self.assertEqual(enforce_mock.call_args.kwargs["minimum_role"], "viewer")
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_export_route_audits_counts_without_memory_content(self):
        fake_server = types.ModuleType("server")
        fake_server.Depends = lambda dependency: dependency
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            sage_memory_api.register_sage_memory_routes(app)
            route = app.routes[("GET", "/api/sage-memory/export")]
            export_payload = {
                "summary": {"total_count": 1, "category_counts": {"private": 1}},
                "items": [{"id": "memory-1", "content": "private content"}],
                "markdown": "private content",
            }
            with (
                patch("server_modules.sage_memory_api.enforce_workspace_access", return_value="workspace-1") as enforce_mock,
                patch("server_modules.sage_memory_api.workspace_tenant_id", return_value="tenant-1"),
                patch("server_modules.sage_memory_api.export_sage_memory", return_value=export_payload),
                patch("server_modules.sage_memory_api.security_audit_service.emit_security_audit_event") as audit_mock,
            ):
                payload = asyncio.run(route(workspace_id="workspace-1", current_user={"user_id": "user-1"}))
            self.assertEqual(payload["items"][0]["id"], "memory-1")
            self.assertEqual(enforce_mock.call_args.kwargs["minimum_role"], "member")
            audit_metadata = audit_mock.call_args.kwargs["metadata"]
            self.assertEqual(audit_metadata["entry_count"], 1)
            self.assertNotIn("private content", str(audit_metadata))
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_wipe_route_requires_owner_and_audits_deleted_count(self):
        fake_server = types.ModuleType("server")
        fake_server.Depends = lambda dependency: dependency
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            sage_memory_api.register_sage_memory_routes(app)
            route = app.routes[("POST", "/api/sage-memory/wipe")]
            with (
                patch("server_modules.sage_memory_api.enforce_workspace_access", return_value="workspace-1") as enforce_mock,
                patch("server_modules.sage_memory_api.workspace_tenant_id", return_value="tenant-1"),
                patch(
                    "server_modules.sage_memory_api.wipe_sage_memory",
                    return_value={"deleted_count": 3, "previous_updated_at": "2026-05-02T00:00:00Z"},
                ) as wipe_mock,
                patch("server_modules.sage_memory_api.security_audit_service.emit_security_audit_event") as audit_mock,
            ):
                payload = asyncio.run(
                    route(
                        sage_memory_api.SageMemoryWipeRequest(
                            workspace_id="workspace-1",
                            confirm="WIPE SAGE MEMORY",
                        ),
                        current_user={"user_id": "user-1"},
                    )
                )
            self.assertEqual(payload["deleted_count"], 3)
            self.assertEqual(enforce_mock.call_args.kwargs["minimum_role"], "owner")
            self.assertEqual(wipe_mock.call_args.kwargs["actor_user_id"], "user-1")
            self.assertEqual(audit_mock.call_args.kwargs["metadata"]["deleted_count"], 3)
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server


if __name__ == "__main__":
    unittest.main()
