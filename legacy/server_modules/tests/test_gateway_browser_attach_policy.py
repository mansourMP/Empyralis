from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server_modules import auth, gateway_execution_service, gateway_state_repository, personal_channels_repository, routes_gateway


class GatewayBrowserAttachPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.gateway_db_path = Path(self.tmpdir.name) / "gateway-state.sqlite3"
        self.auth_db_path = Path(self.tmpdir.name) / "auth-users.sqlite3"
        self.personal_channels_db_path = Path(self.tmpdir.name) / "personal-channels.sqlite3"
        self.runtime_state_db_path = Path(self.tmpdir.name) / "runtime-state.sqlite3"
        gateway_state_repository.init_gateway_state_db(self.gateway_db_path)
        personal_channels_repository.init_personal_channels_db(self.personal_channels_db_path)
        self.app = FastAPI()
        self.app.include_router(routes_gateway.router, prefix="/api")
        self.app.dependency_overrides[routes_gateway.require_api_key] = lambda: self._current_user()
        self.client = TestClient(self.app)

        self.patchers = [
            patch.object(gateway_state_repository, "GATEWAY_STATE_DB_FILE", self.gateway_db_path),
            patch.object(auth, "AUTH_DB_FILE", self.auth_db_path),
            patch.object(personal_channels_repository, "PERSONAL_CHANNELS_DB_FILE", self.personal_channels_db_path),
            patch.dict(os.environ, {"ORION_RUNTIME_STATE_DB": str(self.runtime_state_db_path)}, clear=False),
            patch("server_modules.session_service.runtime_db.get_pool", new=AsyncMock(return_value=None)),
            patch(
                "server_modules.gateway_activity_service.activity_ledger_service.append_activity_event",
                new=AsyncMock(return_value={"id": "aevt-1"}),
            ),
            patch(
                "server_modules.gateway_activity_service.agent_trace_service.resume_trace",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "server_modules.gateway_activity_service.agent_trace_service.emit_approval_requested",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "server_modules.gateway_activity_service.agent_trace_service.emit_approval_resolved",
                new=AsyncMock(return_value=None),
            ),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self) -> None:
        self.client.close()
        self.app.dependency_overrides.clear()
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.tmpdir.cleanup()

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

    def _register_gateway(self) -> dict:
        pairing_response = self.client.post(
            "/api/gateway/pairings/intents",
            json={"workspace_id": "default", "display_name": "Mansur Mac", "platform": "macos"},
        )
        self.assertEqual(pairing_response.status_code, 200)
        registration_response = self.client.post(
            "/api/gateway/registrations",
            json={
                "pairing_token": pairing_response.json()["pairing_token"],
                "device_id": "device-local-1",
                "display_name": "Mansur Mac",
                "platform": "macos-arm64",
                "capabilities": ["browser.session.start"],
            },
        )
        self.assertEqual(registration_response.status_code, 200)
        return registration_response.json()

    def test_existing_session_attach_requires_reviewed_approval_before_start(self) -> None:
        registration_payload = self._register_gateway()
        gateway_id = registration_payload["gateway"]["gateway_id"]

        response = self.client.post(
            f"/api/gateway/registrations/{gateway_id}/browser/sessions",
            json={
                "session_mode": "existing_session_attach",
                "attach_endpoint_url": "http://127.0.0.1:9222",
                "interactive_actions": ["navigate"],
                "allow_cloud_fallback": False,
                "run_id": "run-browser-attach-policy-1",
                "trace_id": "trace-browser-attach-policy-1",
            },
        )

        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertEqual(payload["status"], "approval_required")
        self.assertEqual(payload["gateway_id"], gateway_id)
        self.assertEqual(payload["approval"]["capability_id"], "browser.session.start")

    def test_managed_browser_can_open_url_without_owner_approval(self) -> None:
        registration_payload = self._register_gateway()
        gateway_id = registration_payload["gateway"]["gateway_id"]
        execute_mock = AsyncMock(
            return_value={
                "status": "active",
                "browser_session": {
                    "browser_session_id": "gbsess-open-1",
                    "status": "active",
                    "current_url": "https://example.com",
                    "metadata": {},
                },
            }
        )

        with patch(
            "server_modules.routes_gateway.gateway_browser_service.execute_browser_capability_via_gateway",
            execute_mock,
        ):
            response = self.client.post(
                f"/api/gateway/registrations/{gateway_id}/browser/sessions",
                json={
                    "url": "https://example.com",
                    "session_mode": "managed_profile",
                    "interactive_actions": ["navigate"],
                    "run_id": "run-browser-open-1",
                    "trace_id": "trace-browser-open-1",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["browser_session"]["current_url"], "https://example.com")
        execute_mock.assert_awaited_once()

    def test_browser_click_requires_approval_in_guarded_mode(self) -> None:
        registration_payload = self._register_gateway()
        gateway_id = registration_payload["gateway"]["gateway_id"]
        gateway_state_repository.upsert_gateway_browser_session(
            browser_session_id="gbsess-click-1",
            gateway_id=gateway_id,
            device_id="device-local-1",
            tenant_id="default",
            workspace_id="default",
            user_id="owner-1",
            run_id="run-browser-seed-1",
            status="active",
            execution_target="local_gateway",
            current_url="https://example.com",
            metadata={"browser_session_mode": "managed_profile"},
        )

        response = self.client.post(
            f"/api/gateway/registrations/{gateway_id}/browser/sessions/gbsess-click-1/actions",
            json={
                "action": "click",
                "action_args": {"selector": "#pay"},
                "run_id": "run-browser-click-1",
                "trace_id": "trace-browser-click-1",
            },
        )

        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertEqual(payload["status"], "approval_required")
        self.assertEqual(payload["approval"]["capability_id"], "browser.session.action")

    def test_browser_click_dispatches_in_full_access_even_with_stale_approval_policy(self) -> None:
        registration_payload = self._register_gateway()
        gateway_id = registration_payload["gateway"]["gateway_id"]
        gateway_state_repository.update_gateway_registration_state(
            gateway_id=gateway_id,
            metadata={
                "runtime_access_mode": "full_access",
                "autonomous_agent_setup_warning_acknowledged": True,
                "agent_computer_policy": {"autonomy_mode": "ask_every_time"},
            },
        )
        gateway_state_repository.upsert_gateway_browser_session(
            browser_session_id="gbsess-click-full-1",
            gateway_id=gateway_id,
            device_id="device-local-1",
            tenant_id="default",
            workspace_id="default",
            user_id="owner-1",
            run_id="run-browser-seed-full-1",
            status="active",
            execution_target="local_gateway",
            current_url="https://example.com",
            metadata={"browser_session_mode": "managed_profile"},
        )
        execute_mock = AsyncMock(
            return_value={
                "status": "active",
                "browser_session": {
                    "browser_session_id": "gbsess-click-full-1",
                    "status": "active",
                    "current_url": "https://example.com/after-click",
                    "metadata": {},
                },
            }
        )

        with patch(
            "server_modules.routes_gateway.gateway_browser_service.execute_browser_capability_via_gateway",
            execute_mock,
        ):
            response = self.client.post(
                f"/api/gateway/registrations/{gateway_id}/browser/sessions/gbsess-click-full-1/actions",
                json={
                    "action": "click",
                    "action_args": {"selector": "#pay"},
                    "run_id": "run-browser-click-full-1",
                    "trace_id": "trace-browser-click-full-1",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["browser_session"]["current_url"], "https://example.com/after-click")
        execute_mock.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
