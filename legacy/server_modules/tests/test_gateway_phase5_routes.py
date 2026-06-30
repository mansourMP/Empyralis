import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server_modules import (
    auth,
    control_plane_repository,
    gateway_activity_service,
    gateway_state_repository,
    personal_channels_repository,
    routes_gateway,
)


class GatewayPhase5RoutesTests(unittest.TestCase):
    def setUp(self) -> None:
        global auth
        global control_plane_repository
        global gateway_activity_service
        global gateway_state_repository
        global personal_channels_repository
        global routes_gateway

        auth = importlib.import_module("server_modules.auth")
        control_plane_repository = importlib.import_module("server_modules.control_plane_repository")
        gateway_activity_service = importlib.import_module("server_modules.gateway_activity_service")
        gateway_state_repository = importlib.import_module("server_modules.gateway_state_repository")
        personal_channels_repository = importlib.import_module("server_modules.personal_channels_repository")
        routes_gateway = importlib.import_module("server_modules.routes_gateway")

        self.tmpdir = tempfile.TemporaryDirectory()
        self.gateway_db_path = Path(self.tmpdir.name) / "gateway-state.sqlite3"
        self.auth_db_path = Path(self.tmpdir.name) / "auth-users.sqlite3"
        self.runtime_state_db_path = Path(self.tmpdir.name) / "runtime-state.sqlite3"
        self.personal_channels_db_path = Path(self.tmpdir.name) / "personal-channels.sqlite3"
        gateway_state_repository.init_gateway_state_db(self.gateway_db_path)
        personal_channels_repository.init_personal_channels_db(self.personal_channels_db_path)
        self.app = FastAPI()
        self.app.include_router(routes_gateway.router, prefix="/api")
        self.app.dependency_overrides[routes_gateway.require_api_key] = lambda: self._current_user()
        self.client = TestClient(self.app)

        self.activity_append_patcher = patch(
            "server_modules.gateway_activity_service.activity_ledger_service.append_activity_event",
            new=AsyncMock(return_value={"id": "aevt-1"}),
        )
        self.trace_resume_patcher = patch(
            "server_modules.gateway_activity_service.agent_trace_service.resume_trace",
            new=AsyncMock(return_value=None),
        )
        self.trace_approval_requested_patcher = patch(
            "server_modules.gateway_activity_service.agent_trace_service.emit_approval_requested",
            new=AsyncMock(return_value=None),
        )
        self.trace_approval_resolved_patcher = patch(
            "server_modules.gateway_activity_service.agent_trace_service.emit_approval_resolved",
            new=AsyncMock(return_value=None),
        )
        self.patchers = [
            patch.object(gateway_state_repository, "GATEWAY_STATE_DB_FILE", self.gateway_db_path),
            patch.object(auth, "AUTH_DB_FILE", self.auth_db_path),
            patch.object(control_plane_repository, "LOCAL_IDENTITY_DB_FILE", self.auth_db_path),
            patch.object(personal_channels_repository, "PERSONAL_CHANNELS_DB_FILE", self.personal_channels_db_path),
            patch.dict(
                os.environ,
                {
                    "ORION_RUNTIME_STATE_DB": str(self.runtime_state_db_path),
                    "ORION_ALLOW_SERVER_SESSION_SQLITE_FALLBACK": "1",
                },
                clear=False,
            ),
            patch("server_modules.session_service.runtime_db.get_pool", new=AsyncMock(return_value=None)),
        ]
        for patcher in self.patchers:
            patcher.start()
        self.activity_append = self.activity_append_patcher.start()
        self.trace_resume = self.trace_resume_patcher.start()
        self.trace_approval_requested = self.trace_approval_requested_patcher.start()
        self.trace_approval_resolved = self.trace_approval_resolved_patcher.start()

    def tearDown(self) -> None:
        self.client.close()
        self.app.dependency_overrides.clear()
        self.activity_append_patcher.stop()
        self.trace_resume_patcher.stop()
        self.trace_approval_requested_patcher.stop()
        self.trace_approval_resolved_patcher.stop()
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
        pairing_payload = pairing_response.json()
        registration_response = self.client.post(
            "/api/gateway/registrations",
            json={
                "pairing_token": pairing_payload["pairing_token"],
                "device_id": "device-local-1",
                "display_name": "Mansur Mac",
                "platform": "macos-arm64",
                "capabilities": ["computer_control.click", "computer_control.list_apps", "screenshot.capture"],
            },
        )
        self.assertEqual(registration_response.status_code, 200)
        return registration_response.json()

    def _connect_gateway(self, gateway_id: str, gateway_token: str) -> tuple[dict, str]:
        session_response = self.client.post(
            "/api/gateway/sessions",
            json={"gateway_id": gateway_id, "gateway_token": gateway_token},
        )
        self.assertEqual(session_response.status_code, 200)
        session_payload = session_response.json()
        ws_path = (
            f"/api/gateway/ws?gateway_id={gateway_id}"
            f"&session_token={session_payload['session_token']}"
        )
        return session_payload, ws_path

    def test_risky_local_tool_requires_approval_and_can_retry_then_execute(self) -> None:
        registration_payload = self._register_gateway()
        gateway_id = registration_payload["gateway"]["gateway_id"]
        approval_response = self.client.post(
            f"/api/gateway/registrations/{gateway_id}/tools/execute",
            json={
                "capability_id": "computer_control.click",
                "arguments": {"x": 10, "y": 20, "button": "left"},
                "run_id": "run-approval-1",
                "trace_id": "trace-approval-1",
                "request_id": "req-approval-1",
            },
        )
        self.assertEqual(approval_response.status_code, 202)
        approval_payload = approval_response.json()
        self.assertEqual(approval_payload["status"], "approval_required")
        approval_id = approval_payload["approval"]["approval_id"]

        approvals_listing = self.client.get(f"/api/gateway/registrations/{gateway_id}/approvals")
        self.assertEqual(approvals_listing.status_code, 200)
        self.assertEqual(approvals_listing.json()["pending_count"], 1)

        execute_mock = AsyncMock(
            side_effect=[
                RuntimeError("Gateway temporary failure."),
                {
                    "gateway_id": gateway_id,
                    "device_id": "device-local-1",
                    "workspace_id": "default",
                    "request_id": "req-approval-1",
                    "capability_id": "computer_control.click",
                    "run_id": "run-approval-1",
                    "result": {"clicked": True, "x": 10, "y": 20},
                },
            ]
        )
        with patch("server_modules.routes_gateway.gateway_execution_service.execute_tool_via_gateway", execute_mock):
            first_retry_response = self.client.post(
                f"/api/gateway/registrations/{gateway_id}/approvals/{approval_id}/resolve",
                json={"decision": "approved", "note": "Proceed", "timeout_seconds": 1},
            )
            self.assertEqual(first_retry_response.status_code, 409)
            first_retry_payload = first_retry_response.json()
            self.assertEqual(first_retry_payload["status"], "retryable_error")
            self.assertTrue(first_retry_payload["retryable"])
            self.assertEqual(first_retry_payload["approval"]["status"], "approved")
            self.assertEqual(first_retry_payload["approval"]["retry_count"], 1)

            retry_listing = self.client.get(f"/api/gateway/registrations/{gateway_id}/approvals")
            self.assertEqual(retry_listing.status_code, 200)
            self.assertEqual(retry_listing.json()["retryable_count"], 1)

            second_response = self.client.post(
                f"/api/gateway/registrations/{gateway_id}/approvals/{approval_id}/resolve",
                json={"decision": "approved", "note": "Retry", "timeout_seconds": 5},
            )
            self.assertEqual(second_response.status_code, 200)
            second_payload = second_response.json()
            self.assertEqual(second_payload["status"], "executed")
            self.assertEqual(second_payload["approval"]["status"], "executed")
            self.assertTrue(second_payload["execution"]["result"]["clicked"])

        self.assertEqual(execute_mock.await_count, 2)

        events_payload = self.client.get(f"/api/gateway/registrations/{gateway_id}/events")
        self.assertEqual(events_payload.status_code, 200)
        event_types = [item["message_type"] for item in events_payload.json()["items"]]
        self.assertIn("gateway.approval.requested", event_types)
        self.assertIn("gateway.approval.retryable_failure", event_types)
        self.assertIn("gateway.approval.executed", event_types)

        self.assertGreaterEqual(self.activity_append.await_count, 3)

    def test_advertised_inventory_tool_enters_approval_flow(self) -> None:
        registration_payload = self._register_gateway()
        gateway_id = registration_payload["gateway"]["gateway_id"]
        approval_response = self.client.post(
            f"/api/gateway/registrations/{gateway_id}/tools/execute",
            json={
                "capability_id": "computer_control.list_apps",
                "arguments": {},
                "run_id": "run-list-apps-1",
                "trace_id": "trace-list-apps-1",
                "request_id": "req-list-apps-1",
            },
        )
        self.assertEqual(approval_response.status_code, 202)
        approval_payload = approval_response.json()
        self.assertEqual(approval_payload["status"], "approval_required")
        self.assertEqual(approval_payload["approval"]["capability_id"], "computer_control.list_apps")

    def test_gateway_doctor_reports_degraded_offline_and_resume_state(self) -> None:
        registration_payload = self._register_gateway()
        gateway_id = registration_payload["gateway"]["gateway_id"]
        gateway_token = registration_payload["gateway_token"]
        session_payload, ws_path = self._connect_gateway(gateway_id, gateway_token)

        with self.client.websocket_connect(ws_path) as websocket:
            websocket.send_json(
                {
                    "kind": "request",
                    "id": "req-connect-phase5-2",
                    "type": "gateway.connect",
                    "protocolVersion": "v1alpha2",
                    "ts": "2026-04-22T14:00:00Z",
                    "scope": session_payload["scope"],
                    "payload": {
                        "gateway_version": "0.1.0",
                        "device_metadata": {"hostname": "mansur-mac"},
                        "requested_capabilities": ["channel.whatsapp.personal", "screenshot.capture"],
                        "journal_cursor": 0,
                        "checkpoint_cursor": 0,
                    },
                }
            )
            self.assertTrue(websocket.receive_json()["ok"])
            websocket.receive_json()
            websocket.receive_json()

            websocket.send_json(
                {
                    "kind": "request",
                    "id": "req-heartbeat-phase5-2",
                    "type": "gateway.heartbeat",
                    "ts": "2026-04-22T14:00:01Z",
                    "scope": session_payload["scope"],
                    "payload": {
                        "health_state": "online",
                        "journal_cursor": 3,
                        "checkpoint_cursor": 2,
                        "queue_depth_summary": {"total": 1, "pending": 0, "failed": 0, "acknowledged": 1},
                        "capability_readiness": {"requested": ["screenshot.capture"]},
                    },
                }
            )
            self.assertTrue(websocket.receive_json()["ok"])

            websocket.send_json(
                {
                    "kind": "request",
                    "id": "req-state-phase5-2",
                    "type": "gateway.state.update",
                    "ts": "2026-04-22T14:00:02Z",
                    "scope": session_payload["scope"],
                    "payload": {
                        "health_state": "degraded",
                        "journal_cursor": 9,
                        "checkpoint_cursor": 4,
                        "personal_channels": {
                            "whatsapp_personal": {
                                "provider": "whatsapp_baileys",
                                "status": "qr_required",
                                "qr_code": "qr-phase5",
                                "retryable": True,
                            }
                        },
                    },
                }
            )
            self.assertTrue(websocket.receive_json()["ok"])
            websocket.receive_json()

            degraded_doctor = self.client.get(f"/api/gateway/registrations/{gateway_id}/doctor")
            self.assertEqual(degraded_doctor.status_code, 200)
            degraded_payload = degraded_doctor.json()
            self.assertEqual(degraded_payload["status"], "degraded")
            self.assertEqual(degraded_payload["checkpoint"]["journal_cursor"], 9)
            self.assertEqual(degraded_payload["checkpoint"]["checkpoint_cursor"], 4)
            self.assertTrue(degraded_payload["checkpoint"]["resume_ready"])
            self.assertEqual(degraded_payload["whatsapp_personal"]["status"], "qr_required")

            websocket.send_json(
                {
                    "kind": "request",
                    "id": "req-disconnect-phase5-2",
                    "type": "gateway.disconnect",
                    "ts": "2026-04-22T14:00:03Z",
                    "scope": session_payload["scope"],
                    "payload": {"reason": "phase5_offline_check"},
                }
            )
            self.assertTrue(websocket.receive_json()["ok"])

        offline_doctor = self.client.get(f"/api/gateway/registrations/{gateway_id}/doctor")
        self.assertEqual(offline_doctor.status_code, 200)
        offline_payload = offline_doctor.json()
        self.assertEqual(offline_payload["status"], "offline")
        self.assertEqual(offline_payload["checkpoint"]["journal_cursor"], 9)
        self.assertEqual(offline_payload["checkpoint"]["checkpoint_cursor"], 4)

        reconnect_session_response = self.client.post(
            "/api/gateway/sessions",
            json={"gateway_id": gateway_id, "gateway_token": gateway_token},
        )
        self.assertEqual(reconnect_session_response.status_code, 200)
        reconnect_session = reconnect_session_response.json()
        reconnect_path = (
            f"/api/gateway/ws?gateway_id={gateway_id}"
            f"&session_token={reconnect_session['session_token']}"
        )
        with self.client.websocket_connect(reconnect_path) as websocket:
            websocket.send_json(
                {
                    "kind": "request",
                    "id": "req-connect-phase5-3",
                    "type": "gateway.connect",
                    "protocolVersion": "v1alpha2",
                    "ts": "2026-04-22T14:01:00Z",
                    "scope": reconnect_session["scope"],
                    "payload": {
                        "gateway_version": "0.1.0",
                        "device_metadata": {"hostname": "mansur-mac"},
                        "requested_capabilities": ["screenshot.capture"],
                        "journal_cursor": 9,
                        "checkpoint_cursor": 4,
                    },
                }
            )
            self.assertTrue(websocket.receive_json()["ok"])
            websocket.receive_json()
            websocket.receive_json()

            websocket.send_json(
                {
                    "kind": "request",
                    "id": "req-heartbeat-phase5-3",
                    "type": "gateway.heartbeat",
                    "ts": "2026-04-22T14:01:01Z",
                    "scope": reconnect_session["scope"],
                    "payload": {
                        "health_state": "online",
                        "journal_cursor": 9,
                        "checkpoint_cursor": 4,
                        "queue_depth_summary": {"total": 2, "pending": 0, "failed": 0, "acknowledged": 2},
                        "capability_readiness": {"requested": ["screenshot.capture"]},
                    },
                }
            )
            self.assertTrue(websocket.receive_json()["ok"])

            websocket.send_json(
                {
                    "kind": "request",
                    "id": "req-state-phase5-3",
                    "type": "gateway.state.update",
                    "ts": "2026-04-22T14:01:02Z",
                    "scope": reconnect_session["scope"],
                    "payload": {
                        "health_state": "online",
                        "journal_cursor": 9,
                        "checkpoint_cursor": 4,
                    },
                }
            )
            self.assertTrue(websocket.receive_json()["ok"])
            websocket.receive_json()

            resumed_doctor = self.client.get(f"/api/gateway/registrations/{gateway_id}/doctor")
            self.assertEqual(resumed_doctor.status_code, 200)
            resumed_payload = resumed_doctor.json()
            self.assertEqual(resumed_payload["status"], "healthy")
            self.assertTrue(resumed_payload["checkpoint"]["resume_ready"])
            self.assertEqual(resumed_payload["checkpoint"]["journal_cursor"], 9)
            self.assertEqual(resumed_payload["checkpoint"]["checkpoint_cursor"], 4)


if __name__ == "__main__":
    unittest.main()
