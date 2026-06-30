import asyncio
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import sage_services_api


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

    def put(self, path, **kwargs):
        return self._register("PUT", path, **kwargs)

    def patch(self, path, **kwargs):
        return self._register("PATCH", path, **kwargs)

    def delete(self, path, **kwargs):
        return self._register("DELETE", path, **kwargs)


class SageServicesApiTests(unittest.TestCase):
    def test_list_routes_enforce_workspace_and_return_payload(self):
        fake_server = types.ModuleType("server")
        fake_server.Depends = lambda dependency: dependency
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            sage_services_api.register_sage_services_routes(app)
            route = app.routes[("GET", "/api/sage-services")]
            with (
                patch("server_modules.sage_services_api.enforce_workspace_access", return_value="workspace-1"),
                patch("server_modules.sage_services_api.workspace_tenant_id", return_value="tenant-1"),
                patch(
                    "server_modules.sage_services_api.list_sage_services",
                    return_value={"items": [{"id": "flashcards"}], "updated_at": "2026-04-15T00:00:00Z", "memory_context": "Sage services state"},
                ) as list_mock,
            ):
                payload = asyncio.run(
                    route(
                        workspace_id="workspace-1",
                        current_user={"user_id": "user-1", "role": "owner"},
                    )
                )
            self.assertEqual(payload["workspace_id"], "workspace-1")
            self.assertEqual(payload["tenant_id"], "tenant-1")
            self.assertEqual(payload["items"][0]["id"], "flashcards")
            self.assertEqual(list_mock.call_args.kwargs["workspace_id"], "workspace-1")
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_create_entry_routes_to_service_layer(self):
        fake_server = types.ModuleType("server")
        fake_server.Depends = lambda dependency: dependency
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            sage_services_api.register_sage_services_routes(app)
            route = app.routes[("POST", "/api/sage-services/{service_id}/entries")]
            with (
                patch("server_modules.sage_services_api.enforce_workspace_access", return_value="workspace-1"),
                patch("server_modules.sage_services_api.workspace_tenant_id", return_value="tenant-1"),
                patch(
                    "server_modules.sage_services_api.create_service_entry",
                    new=AsyncMock(return_value={"service": {"id": "flashcards"}}),
                ) as create_mock,
            ):
                payload = asyncio.run(
                    route(
                        "flashcards",
                        sage_services_api.SageServiceEntryCreateRequest(
                            workspace_id="workspace-1",
                            entry={"front": "SPA", "back": "Share Purchase Agreement"},
                        ),
                        current_user={"user_id": "user-1", "role": "owner"},
                    )
                )
            self.assertEqual(payload["workspace_id"], "workspace-1")
            self.assertEqual(create_mock.await_args.kwargs["service_id"], "flashcards")
            self.assertEqual(create_mock.await_args.kwargs["actor_user_id"], "user-1")
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server


if __name__ == "__main__":
    unittest.main()
