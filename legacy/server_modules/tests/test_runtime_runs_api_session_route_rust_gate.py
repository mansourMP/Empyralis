import asyncio
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from server_modules import runtime_runs_api


class _FakeApp:
    def __init__(self) -> None:
        self.routes = {}

    def _register(self, method, path, **kwargs):
        def _decorator(fn):
            self.routes[(method, path)] = fn
            return fn

        return _decorator

    def post(self, path, **kwargs):
        return self._register("POST", path, **kwargs)

    def get(self, path, **kwargs):
        return self._register("GET", path, **kwargs)

    def delete(self, path, **kwargs):
        return self._register("DELETE", path, **kwargs)


class RuntimeRunsApiSessionRouteRustGateTests(unittest.TestCase):
    def _current_user(self) -> dict[str, str]:
        return {"user_id": "user-1", "email": "user-1@example.com"}

    def _register_app(self) -> _FakeApp:
        app = _FakeApp()
        runtime_runs_api.register_run_routes(app)
        return app

    def test_create_runtime_session_wrong_next_action_blocks_before_create(self):
        fake_server = types.ModuleType("server")
        fake_server.require_api_key = object()
        fake_server.require_admin_api_key = object()
        fake_server.ORION_SINGLE_AGENT_MODE = False
        fake_server.runs = {}
        fake_server.iter_logs_for_run = lambda run_id: []
        fake_server._get_replay_payload = lambda run_id: {}

        with patch.dict(sys.modules, {"server": fake_server}), patch.object(
            runtime_runs_api.runtime_route_registration_service,
            "register_runtime_run_routes_from_api",
            new=lambda *args, **kwargs: None,
        ), patch.object(
            runtime_runs_api,
            "_refresh_server_exports",
            new=lambda: fake_server,
        ), patch.object(
            runtime_runs_api,
            "enforce_workspace_access",
            new=lambda current_user, workspace_id, tenant_id=None, minimum_role="viewer": "ws-1",
        ), patch.object(
            runtime_runs_api,
            "workspace_tenant_id",
            new=lambda current_user, workspace_id: "tenant-1",
        ), patch.object(
            runtime_runs_api,
            "normalize_direct_chat_session_metadata",
            new=lambda **kwargs: {"source": "test"},
        ), patch.object(
            runtime_runs_api.session_service,
            "get_session",
            new=AsyncMock(return_value=None),
        ), patch.object(
            runtime_runs_api.session_service,
            "create_session",
            new=AsyncMock(return_value="session-created"),
        ) as create_session, patch.object(
            runtime_runs_api.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "session_create_allowed",
                "operation": "create",
                "next_action": "close",
            },
        ):
            app = self._register_app()
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(
                    app.routes[("POST", "/sessions")](
                        runtime_runs_api.ApiSessionRequest(
                            workspace_id="default",
                            tenant_id="default",
                            channel="web",
                            actor={"type": "user", "id": "user-1"},
                            metadata={"source": "test"},
                        ),
                        current_user=self._current_user(),
                    )
                )

        self.assertEqual(raised.exception.status_code, 423)
        self.assertIn("unexpected next_action", str(raised.exception.detail))
        create_session.assert_not_awaited()

    def test_delete_runtime_session_wrong_next_action_blocks_before_terminate(self):
        fake_server = types.ModuleType("server")
        fake_server.require_api_key = object()
        fake_server.require_admin_api_key = object()
        fake_server.ORION_SINGLE_AGENT_MODE = False
        fake_server.runs = {}
        fake_server.iter_logs_for_run = lambda run_id: []
        fake_server._get_replay_payload = lambda run_id: {}

        with patch.dict(sys.modules, {"server": fake_server}), patch.object(
            runtime_runs_api.runtime_route_registration_service,
            "register_runtime_run_routes_from_api",
            new=lambda *args, **kwargs: None,
        ), patch.object(
            runtime_runs_api,
            "_refresh_server_exports",
            new=lambda: fake_server,
        ), patch.object(
            runtime_runs_api,
            "enforce_workspace_access",
            new=lambda current_user, workspace_id, tenant_id=None, minimum_role="viewer": "ws-1",
        ), patch.object(
            runtime_runs_api,
            "workspace_tenant_id",
            new=lambda current_user, workspace_id: "tenant-1",
        ), patch.object(
            runtime_runs_api.session_service,
            "get_session",
            new=AsyncMock(
                return_value={
                    "session_id": "session-created",
                    "workspace_id": "ws-1",
                    "tenant_id": "tenant-1",
                    "status": "active",
                }
            ),
        ), patch.object(
            runtime_runs_api.session_service,
            "terminate_session",
            new=AsyncMock(),
        ) as terminate_session, patch.object(
            runtime_runs_api.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "session_close_allowed",
                "operation": "close",
                "next_action": "create",
            },
        ):
            app = self._register_app()
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(
                    app.routes[("DELETE", "/sessions/{session_id}")](
                        "session-created",
                        current_user=self._current_user(),
                    )
                )

        self.assertEqual(raised.exception.status_code, 423)
        self.assertIn("unexpected next_action", str(raised.exception.detail))
        terminate_session.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
