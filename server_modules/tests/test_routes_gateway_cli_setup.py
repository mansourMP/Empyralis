from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server_modules import cli_setup_service, routes_gateway


_FAKE_GATEWAY_ID = "gw-cli-setup-test"

_FAKE_REGISTRATION = {
    "gateway_id": _FAKE_GATEWAY_ID,
    "device_id": "device-1",
    "tenant_id": "default",
    "workspace_id": "default",
    "user_id": "owner-1",
    "status": "active",
    "device_trust_state": "trusted",
    "capabilities": ["cli.install", "cli.login.start", "cli.login.input"],
    "metadata": {},
}


def _fake_get_registration(gateway_id: str):
    return dict(_FAKE_REGISTRATION) if gateway_id == _FAKE_GATEWAY_ID else None


class RoutesGatewayCliSetupTests(unittest.TestCase):
    """HTTP-wiring tests for the cli.install / cli.login.* routes. These seed
    a registration by patching gateway_state_repository.get_gateway_registration
    directly (mirroring test_gateway_execution_service.py's own boundary)
    rather than driving the real pairing/registration HTTP flow, which
    requires a live Rust runtime-kernel binary this environment doesn't have
    (confirmed pre-existing: test_gateway_routes.py's own pairing tests fail
    the same way here, unrelated to this change). cli_setup_service's own
    dispatch functions are mocked too — execute_tool_via_gateway's readiness
    logic has dedicated coverage in test_gateway_execution_service.py, and
    cli_setup_service's translation logic is covered in
    test_cli_setup_service.py. This file only proves the HTTP boundary: auth,
    workspace access, path/body wiring, and CliSetupError -> HTTPException
    translation."""

    def setUp(self) -> None:
        self.app = FastAPI()
        self.app.include_router(routes_gateway.router, prefix="/api")
        self.app.dependency_overrides[routes_gateway.require_api_key] = lambda: self._current_user()
        self.client = TestClient(self.app)
        self.patchers = [
            patch.object(routes_gateway.gateway_state_repository, "get_gateway_registration", side_effect=_fake_get_registration),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self) -> None:
        self.client.close()
        self.app.dependency_overrides.clear()
        for patcher in reversed(self.patchers):
            patcher.stop()

    @staticmethod
    def _current_user():
        return {
            "auth_type": "api_key",
            "role": "owner",
            "is_admin": True,
            "user_id": "owner-1",
            "workspace_roles": {"default": "owner"},
            "workspace_access": {
                "default": {
                    "workspace_id": "default",
                    "tenant_id": "default",
                    "role": "owner",
                    "tenant_role": "owner",
                }
            },
        }

    # ---- 404 / unknown gateway --------------------------------------------

    def test_install_unknown_gateway_returns_404(self) -> None:
        response = self.client.post(
            "/api/gateway/registrations/no-such-gateway/cli/install",
            json={"runtime": "claude_code", "run_id": "run-1"},
        )
        self.assertEqual(response.status_code, 404)

    def test_login_start_unknown_gateway_returns_404(self) -> None:
        response = self.client.post(
            "/api/gateway/registrations/no-such-gateway/cli/login/start",
            json={"runtime": "codex", "run_id": "run-1"},
        )
        self.assertEqual(response.status_code, 404)

    def test_login_events_unknown_gateway_returns_404(self) -> None:
        response = self.client.get("/api/gateway/registrations/no-such-gateway/cli/login/run-1/events")
        self.assertEqual(response.status_code, 404)

    # ---- workspace access ---------------------------------------------------

    def test_install_wrong_workspace_is_forbidden(self) -> None:
        def _other_workspace_user():
            return {
                "auth_type": "bearer",
                "role": "owner",
                "is_admin": False,
                "user_id": "owner-2",
                "workspace_roles": {"other": "owner"},
                "workspace_access": {
                    "other": {"workspace_id": "other", "tenant_id": "tenant-other", "role": "owner", "tenant_role": "owner"},
                },
            }

        self.app.dependency_overrides[routes_gateway.require_api_key] = lambda: _other_workspace_user()
        response = self.client.post(
            f"/api/gateway/registrations/{_FAKE_GATEWAY_ID}/cli/install",
            json={"runtime": "claude_code", "run_id": "run-1", "workspace_id": "other"},
        )
        self.assertEqual(response.status_code, 403)

    # ---- request body validation -------------------------------------------

    def test_install_missing_runtime_is_a_422(self) -> None:
        response = self.client.post(
            f"/api/gateway/registrations/{_FAKE_GATEWAY_ID}/cli/install",
            json={"run_id": "run-1"},
        )
        self.assertEqual(response.status_code, 422)

    def test_install_missing_run_id_is_a_422(self) -> None:
        response = self.client.post(
            f"/api/gateway/registrations/{_FAKE_GATEWAY_ID}/cli/install",
            json={"runtime": "claude_code"},
        )
        self.assertEqual(response.status_code, 422)

    def test_login_input_blank_code_is_a_422(self) -> None:
        response = self.client.post(
            f"/api/gateway/registrations/{_FAKE_GATEWAY_ID}/cli/login/run-1/input",
            json={"code": ""},
        )
        self.assertEqual(response.status_code, 422)

    # ---- success + error wiring through cli_setup_service ------------------

    def test_install_success_wires_body_into_service_and_returns_its_result(self) -> None:
        install_mock = AsyncMock(return_value={"gateway_id": _FAKE_GATEWAY_ID, "result": {"installed": True, "version": "2.1.205"}})
        with patch.object(routes_gateway.cli_setup_service, "install_cli_runtime", install_mock):
            response = self.client.post(
                f"/api/gateway/registrations/{_FAKE_GATEWAY_ID}/cli/install",
                json={"runtime": "claude_code", "run_id": "run-1", "trace_id": "trace-1"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["result"]["installed"])
        kwargs = install_mock.await_args.kwargs
        self.assertEqual(kwargs["gateway_id"], _FAKE_GATEWAY_ID)
        self.assertEqual(kwargs["workspace_id"], "default")
        self.assertEqual(kwargs["runtime"], "claude_code")
        self.assertEqual(kwargs["run_id"], "run-1")
        self.assertEqual(kwargs["trace_id"], "trace-1")

    def test_install_cli_setup_error_becomes_http_exception_with_its_status_code(self) -> None:
        install_mock = AsyncMock(side_effect=cli_setup_service.CliSetupError("Heads up: npm isn't on the Gateway's PATH.", status_code=400))
        with patch.object(routes_gateway.cli_setup_service, "install_cli_runtime", install_mock):
            response = self.client.post(
                f"/api/gateway/registrations/{_FAKE_GATEWAY_ID}/cli/install",
                json={"runtime": "claude_code", "run_id": "run-1"},
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn("npm", response.json()["detail"])

    def test_install_cli_setup_error_409_for_offline_gateway(self) -> None:
        install_mock = AsyncMock(side_effect=cli_setup_service.CliSetupError("Heads up: the Gateway is offline.", status_code=409))
        with patch.object(routes_gateway.cli_setup_service, "install_cli_runtime", install_mock):
            response = self.client.post(
                f"/api/gateway/registrations/{_FAKE_GATEWAY_ID}/cli/install",
                json={"runtime": "claude_code", "run_id": "run-1"},
            )
        self.assertEqual(response.status_code, 409)

    def test_login_start_success_wires_body_into_service(self) -> None:
        start_mock = AsyncMock(return_value={"gateway_id": _FAKE_GATEWAY_ID, "result": {"run_id": "run-2", "status": "started"}})
        with patch.object(routes_gateway.cli_setup_service, "start_cli_login", start_mock):
            response = self.client.post(
                f"/api/gateway/registrations/{_FAKE_GATEWAY_ID}/cli/login/start",
                json={"runtime": "codex", "run_id": "run-2"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"]["status"], "started")
        self.assertEqual(start_mock.await_args.kwargs["runtime"], "codex")

    def test_login_input_passes_path_run_id_and_body_code_through(self) -> None:
        input_mock = AsyncMock(return_value={"gateway_id": _FAKE_GATEWAY_ID, "result": {"ok": True}})
        with patch.object(routes_gateway.cli_setup_service, "submit_cli_login_input", input_mock):
            response = self.client.post(
                f"/api/gateway/registrations/{_FAKE_GATEWAY_ID}/cli/login/run-42/input",
                json={"code": "ABCD-1234"},
            )
        self.assertEqual(response.status_code, 200)
        kwargs = input_mock.await_args.kwargs
        self.assertEqual(kwargs["run_id"], "run-42")
        self.assertEqual(kwargs["code"], "ABCD-1234")

    def test_login_cancel_with_no_body_defaults_cleanly(self) -> None:
        cancel_mock = AsyncMock(return_value={"interrupted": True, "run_id": "run-9"})
        with patch.object(routes_gateway.cli_setup_service, "cancel_cli_login", cancel_mock):
            response = self.client.post(f"/api/gateway/registrations/{_FAKE_GATEWAY_ID}/cli/login/run-9/cancel")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["interrupted"])
        self.assertEqual(cancel_mock.await_args.kwargs["run_id"], "run-9")

    def test_login_cancel_forwards_explicit_reason(self) -> None:
        cancel_mock = AsyncMock(return_value={"interrupted": True, "run_id": "run-9"})
        with patch.object(routes_gateway.cli_setup_service, "cancel_cli_login", cancel_mock):
            self.client.post(
                f"/api/gateway/registrations/{_FAKE_GATEWAY_ID}/cli/login/run-9/cancel",
                json={"reason": "navigated away"},
            )
        self.assertEqual(cancel_mock.await_args.kwargs["reason"], "navigated away")

    def test_login_events_returns_service_items_for_the_path_run_id(self) -> None:
        fake_items = [{"payload": {"run_id": "run-7", "event": "output", "kind": "url", "text": "https://x"}}]
        with patch.object(routes_gateway.cli_setup_service, "list_cli_login_events", return_value=fake_items) as events_mock:
            response = self.client.get(f"/api/gateway/registrations/{_FAKE_GATEWAY_ID}/cli/login/run-7/events")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["run_id"], "run-7")
        self.assertEqual(body["count"], 1)
        self.assertEqual(events_mock.call_args.kwargs["run_id"], "run-7")

    def test_login_events_viewer_role_is_sufficient(self) -> None:
        def _viewer_user():
            return {
                "auth_type": "bearer",
                "role": "viewer",
                "is_admin": False,
                "user_id": "viewer-1",
                "workspace_roles": {"default": "viewer"},
                "workspace_access": {
                    "default": {"workspace_id": "default", "tenant_id": "default", "role": "viewer", "tenant_role": "viewer"},
                },
            }

        self.app.dependency_overrides[routes_gateway.require_api_key] = lambda: _viewer_user()
        with patch.object(routes_gateway.cli_setup_service, "list_cli_login_events", return_value=[]):
            response = self.client.get(f"/api/gateway/registrations/{_FAKE_GATEWAY_ID}/cli/login/run-1/events")
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
