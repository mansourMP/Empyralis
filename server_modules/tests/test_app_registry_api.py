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


class AppRegistryInstallMutationAuthorizationTests(unittest.TestCase):
    """Security review, 2026-08-13: /apps/install, /apps/uninstall, and
    /apps/update mutate ORION_APP_REGISTRY_FILE, ONE process-wide store with
    no workspace_id/tenant_id column at all (confirmed: WorkflowCreate/
    Update/Delete carry no such field). They were gated by a bare
    `require_api_key` -- any authenticated user of ANY tenant -- so any
    signed-up customer could install/uninstall/update an app for the whole
    platform, visible in every other tenant's own /apps/installed. Fixed to
    require real platform-operator access
    (auth.current_user_has_auth_admin_access), matching the same-shaped fix
    applied to update_tool_contract/rotate_vault_key_route in
    routes_connectors.py."""

    def setUp(self) -> None:
        self._tempdir_ctx = tempfile.TemporaryDirectory()
        tempdir = self._tempdir_ctx.__enter__()
        self.addCleanup(self._tempdir_ctx.__exit__, None, None, None)
        registry_path = Path(tempdir) / "apps.json"
        seeded = {
            "apps": [{"id": "study", "status": "available", "latest_version": "1.1"}],
            "updated_at": "2026-04-10T00:00:00Z",
        }
        registry_path.write_text(__import__("json").dumps(seeded), encoding="utf-8")

        fake_server = types.ModuleType("server")
        fake_server.Depends = lambda dependency: dependency
        fake_server.require_api_key = object()
        fake_server.HTTPException = HTTPException
        fake_server.ORION_APP_REGISTRY_FILE = registry_path
        fake_server._safe_read_json = lambda path, fallback: __import__("json").loads(path.read_text(encoding="utf-8")) if path.exists() else fallback
        fake_server._safe_write_json = lambda path, value: path.write_text(__import__("json").dumps(value), encoding="utf-8")
        fake_server._utc_now_iso = lambda: "2026-04-10T00:00:00Z"

        self._previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        self.addCleanup(self._restore_server)

        self.app = _FakeApp()
        app_registry_api.register_app_registry_routes(self.app)
        # register_app_registry_routes only copies names from the fake
        # `server` module into app_registry_api's OWN globals the FIRST
        # time (`if key not in module_globals`), so a later test's fresh
        # tempdir would otherwise be shadowed by whichever path the first
        # test in this process happened to register. Set it directly so
        # each test's own registry file is the one actually read/written.
        app_registry_api.ORION_APP_REGISTRY_FILE = registry_path
        app_registry_api._safe_read_json = fake_server._safe_read_json
        app_registry_api._safe_write_json = fake_server._safe_write_json

    def _restore_server(self) -> None:
        if self._previous_server is None:
            sys.modules.pop("server", None)
        else:
            sys.modules["server"] = self._previous_server

    @staticmethod
    def _ordinary_customer() -> dict:
        # An ordinary signed-in owner of their OWN workspace -- exactly what
        # `require_api_key` alone let through -- must not be an operator.
        return {"auth_type": "bearer", "role": "owner", "auth_admin": False, "user_id": "customer-1", "email": "customer@example.com"}

    @staticmethod
    def _operator() -> dict:
        return {"auth_type": "bearer", "role": "owner", "auth_admin": True, "user_id": "operator-1", "email": "operator@example.com"}

    def test_ordinary_customer_cannot_install_an_app_for_the_whole_platform(self) -> None:
        from server_modules.schemas import WorkflowCreate

        with self.assertRaises(HTTPException) as exc_info:
            asyncio.run(
                self.app.routes[("POST", "/apps/install")](
                    WorkflowCreate(app_id="study"),
                    current_user=self._ordinary_customer(),
                )
            )
        self.assertEqual(exc_info.exception.status_code, 403)

    def test_ordinary_customer_cannot_uninstall_an_app_for_the_whole_platform(self) -> None:
        from server_modules.schemas import WorkflowDelete

        with self.assertRaises(HTTPException) as exc_info:
            asyncio.run(
                self.app.routes[("POST", "/apps/uninstall")](
                    WorkflowDelete(app_id="study"),
                    current_user=self._ordinary_customer(),
                )
            )
        self.assertEqual(exc_info.exception.status_code, 403)

    def test_ordinary_customer_cannot_update_an_app_for_the_whole_platform(self) -> None:
        from server_modules.schemas import WorkflowUpdate

        with self.assertRaises(HTTPException) as exc_info:
            asyncio.run(
                self.app.routes[("POST", "/apps/update")](
                    WorkflowUpdate(app_id="study"),
                    current_user=self._ordinary_customer(),
                )
            )
        self.assertEqual(exc_info.exception.status_code, 403)

    def test_operator_can_still_install_an_app(self) -> None:
        from server_modules.schemas import WorkflowCreate

        result = asyncio.run(
            self.app.routes[("POST", "/apps/install")](
                WorkflowCreate(app_id="study"),
                current_user=self._operator(),
            )
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["app"]["status"], "installed")


if __name__ == "__main__":
    unittest.main()
