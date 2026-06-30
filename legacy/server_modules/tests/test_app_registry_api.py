import asyncio
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from server_modules import app_registry_api
from server_modules.schemas import (
    AppCaptainBridgeRequest,
    AppRuntimeBridgeRequest,
    AppSpecialistBridgeRequest,
    SageAppBridgeRequest,
)


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


class AppRegistryApiRouteTests(unittest.TestCase):
    def test_register_app_registry_routes_adds_bridge_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            registry_path = Path(tempdir) / "apps.json"
            fake_server = types.ModuleType("server")
            fake_server.Depends = lambda dependency: dependency
            fake_server.require_api_key = object()
            fake_server.HTTPException = HTTPException
            fake_server.ORION_APP_REGISTRY_FILE = registry_path
            fake_server._safe_read_json = lambda path, fallback: fallback
            fake_server._safe_write_json = lambda path, value: path.write_text("{}", encoding="utf-8")
            fake_server._utc_now_iso = lambda: "2026-04-10T00:00:00Z"

            previous_server = sys.modules.get("server")
            sys.modules["server"] = fake_server
            try:
                app = _FakeApp()
                app_registry_api.register_app_registry_routes(app)
                self.assertIn(("POST", "/apps/bridge/captain"), app.routes)
                self.assertIn(("POST", "/apps/bridge/specialist"), app.routes)
                self.assertIn(("POST", "/apps/bridge/runtime-action"), app.routes)
                self.assertIn(("POST", "/apps/bridge/handoff"), app.routes)
            finally:
                if previous_server is None:
                    sys.modules.pop("server", None)
                else:
                    sys.modules["server"] = previous_server

    def test_bridge_routes_validate_and_audit_requests(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            registry_path = Path(tempdir) / "apps.json"
            fake_server = types.ModuleType("server")
            fake_server.Depends = lambda dependency: dependency
            fake_server.require_api_key = object()
            fake_server.HTTPException = HTTPException
            fake_server.ORION_APP_REGISTRY_FILE = registry_path
            fake_server._safe_read_json = lambda path, fallback: fallback
            fake_server._safe_write_json = lambda path, value: path.write_text("{}", encoding="utf-8")
            fake_server._utc_now_iso = lambda: "2026-04-10T00:00:00Z"

            previous_server = sys.modules.get("server")
            sys.modules["server"] = fake_server
            try:
                app = _FakeApp()
                app_registry_api.register_app_registry_routes(app)
                current_user = {
                    "auth_type": "bearer",
                    "user_id": "user-1",
                    "role": "owner",
                    "workspace_access": {
                        "workspace-1": {"tenant_id": "tenant-1", "role": "owner"},
                    },
                }
                bridge_payload = {
                    "app_id": "study",
                    "bridge_kind": "app_to_sage",
                    "bridge_type": "summary_request",
                    "target": {},
                    "context_envelope": {"classes": ["user_selected_inputs"], "payload": {"user_selected_inputs": {"topic": "bio"}}},
                }

                with (
                    patch("server_modules.auth.enforce_workspace_access", return_value="workspace-1"),
                    patch("server_modules.auth.workspace_tenant_id", return_value="tenant-1"),
                    patch(
                        "server_modules.app_bridge_service.normalize_bridge_contract",
                        return_value=bridge_payload,
                    ) as normalize_mock,
                    patch(
                        "server_modules.app_bridge_service.record_app_bridge_audit",
                        new=AsyncMock(return_value={"id": "audit-1"}),
                    ) as audit_mock,
                ):
                    captain_result = asyncio.run(
                        app.routes[("POST", "/apps/bridge/captain")](
                            AppCaptainBridgeRequest(
                                workspace_id="workspace-1",
                                app_id="study",
                                bridge_type="summary_request",
                                context_envelope={"user_selected_inputs": {"topic": "bio"}},
                            ),
                            current_user=current_user,
                        )
                    )
                    specialist_result = asyncio.run(
                        app.routes[("POST", "/apps/bridge/specialist")](
                            AppSpecialistBridgeRequest(
                                workspace_id="workspace-1",
                                app_id="study",
                                bridge_type="status_request",
                                target_install_id="install-1",
                            ),
                            current_user=current_user,
                        )
                    )
                    runtime_result = asyncio.run(
                        app.routes[("POST", "/apps/bridge/runtime-action")](
                            AppRuntimeBridgeRequest(
                                workspace_id="workspace-1",
                                app_id="study",
                                bridge_type="brokered_structured_backend_route",
                                route_key="study.generate-quiz",
                            ),
                            current_user=current_user,
                        )
                    )
                    handoff_result = asyncio.run(
                        app.routes[("POST", "/apps/bridge/handoff")](
                            SageAppBridgeRequest(
                                workspace_id="workspace-1",
                                app_id="study",
                                bridge_type="handoff_to_app",
                            ),
                            current_user=current_user,
                        )
                    )

                self.assertEqual(captain_result["audit"]["activity_event_id"], "audit-1")
                self.assertEqual(specialist_result["workspace_id"], "workspace-1")
                self.assertEqual(runtime_result["tenant_id"], "tenant-1")
                self.assertEqual(handoff_result["bridge"]["bridge_kind"], "app_to_sage")
                self.assertEqual(normalize_mock.call_count, 4)
                self.assertEqual(audit_mock.await_count, 4)
            finally:
                if previous_server is None:
                    sys.modules.pop("server", None)
                else:
                    sys.modules["server"] = previous_server


if __name__ == "__main__":
    unittest.main()
