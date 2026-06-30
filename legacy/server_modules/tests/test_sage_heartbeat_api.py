import asyncio
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import sage_heartbeat_api


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


class SageHeartbeatApiTests(unittest.TestCase):
    def test_get_route_returns_snapshot(self) -> None:
        fake_server = types.ModuleType("server")
        fake_server.Depends = lambda dependency: dependency
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            sage_heartbeat_api.register_sage_heartbeat_routes(app)
            route = app.routes[("GET", "/api/sage-heartbeat")]
            with (
                patch("server_modules.sage_heartbeat_api.enforce_workspace_access", return_value="workspace-1"),
                patch("server_modules.sage_heartbeat_api.workspace_tenant_id", return_value="tenant-1"),
                patch(
                    "server_modules.sage_heartbeat_api.build_sage_heartbeat_snapshot",
                    new=AsyncMock(return_value={"profile": {"recurring_responsibility": "Inbox"}}),
                ) as snapshot_mock,
            ):
                payload = asyncio.run(
                    route(
                        workspace_id="workspace-1",
                        current_user={"user_id": "user-1", "name": "Mansur", "email": "m@example.com"},
                    )
                )
            self.assertEqual(payload["workspace_id"], "workspace-1")
            self.assertEqual(payload["tenant_id"], "tenant-1")
            self.assertEqual(snapshot_mock.await_args.kwargs["tenant_id"], "tenant-1")
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server


if __name__ == "__main__":
    unittest.main()
