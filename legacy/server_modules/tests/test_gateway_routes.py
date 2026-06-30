import importlib
import json
import os
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
import uuid
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from server_modules import (
    agent_computer_profile_service,
    auth,
    gateway_protocol_service,
    kill_switch_gate,
    gateway_state_repository,
    personal_channels_repository,
    routes_gateway,
    routes_personal_channels,
    safe_mode_service,
)


class GatewayRoutesTests(unittest.TestCase):
    def setUp(self) -> None:
        global auth
        global gateway_state_repository
        global personal_channels_repository
        global routes_gateway
        global routes_personal_channels

        auth = importlib.import_module("server_modules.auth")
        gateway_state_repository = importlib.import_module("server_modules.gateway_state_repository")
        agent_computer_profile_service = importlib.import_module("server_modules.agent_computer_profile_service")
        kill_switch_gate = importlib.import_module("server_modules.kill_switch_gate")
        personal_channels_repository = importlib.import_module("server_modules.personal_channels_repository")
        routes_gateway = importlib.import_module("server_modules.routes_gateway")
        routes_personal_channels = importlib.import_module("server_modules.routes_personal_channels")
        safe_mode_service.reset_state_for_tests()

        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "gateway-state.sqlite3"
        self.auth_db_path = Path(self.tmpdir.name) / "auth-users.sqlite3"
        self.runtime_state_db_path = Path(self.tmpdir.name) / "runtime-state.sqlite3"
        self.personal_channels_db_path = Path(self.tmpdir.name) / "personal-channels.sqlite3"
        self.profile_scope_path = Path(self.tmpdir.name) / "workspace-scope"
        gateway_state_repository.init_gateway_state_db(self.db_path)
        personal_channels_repository.init_personal_channels_db(self.personal_channels_db_path)
        self.app = FastAPI()
        self.app.include_router(routes_gateway.router, prefix="/api")
        self.app.include_router(routes_personal_channels.router, prefix="/api")
        self.app.dependency_overrides[routes_gateway.require_api_key] = lambda: self._current_user()
        self.app.dependency_overrides[routes_personal_channels.require_api_key] = lambda: self._current_user()
        self.client = TestClient(self.app)
        self.patchers = [
            patch.object(gateway_state_repository, "GATEWAY_STATE_DB_FILE", self.db_path),
            patch("server_modules.agent_computer_profile_service.workspace_context.workspace_scope_dir", return_value=self.profile_scope_path),
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
        kill_switch_gate.clear_kill_switch(f"{kill_switch_gate.GATEWAY_KILL_PREFIX}gateway_dedicated_test")
        safe_mode_service.reset_state_for_tests()
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

    def test_agent_computer_bootstrap_installer_is_public_plain_text(self) -> None:
        response = self.client.get("/api/hardware/bootstrap/install.sh")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/plain"))
        self.assertIn("#!/usr/bin/env bash", response.text)
        self.assertIn("EMPYRALIS_PAIRING_TOKEN", response.text)

    @staticmethod
    def _other_workspace_user():
        return {
            "auth_type": "bearer",
            "role": "owner",
            "is_admin": False,
            "user_id": "owner-2",
            "workspace_roles": {"other": "owner"},
            "workspace_access": {
                "other": {
                    "workspace_id": "other",
                    "tenant_id": "tenant-other",
                    "role": "owner",
                    "tenant_role": "owner",
                }
            },
        }

    def test_pairing_token_replay_is_rejected(self) -> None:
        pairing_response = self.client.post(
            "/api/gateway/pairings/intents",
            json={"workspace_id": "default", "display_name": "Test Device", "platform": "macos"},
        )
        self.assertEqual(pairing_response.status_code, 200)
        pairing_payload = pairing_response.json()
        pairing_token = pairing_payload["pairing_token"]

        # First registration succeeds
        first_registration = self.client.post(
            "/api/gateway/registrations",
            json={
                "pairing_token": pairing_token,
                "device_id": "device-replay-1",
                "display_name": "Test Device",
                "platform": "macos-arm64",
            },
        )
        self.assertEqual(first_registration.status_code, 200)

        # Second registration with same token fails
        second_registration = self.client.post(
            "/api/gateway/registrations",
            json={
                "pairing_token": pairing_token,
                "device_id": "device-replay-2",
                "display_name": "Test Device 2",
                "platform": "macos-arm64",
            },
        )
        self.assertEqual(second_registration.status_code, 400)
        self.assertIn("no longer active", second_registration.json()["detail"].lower())

    def _register_gateway_with_mode(
        self,
        *,
        workspace_id: str = "default",
        runtime_access_mode: str = "default_guarded",
        warning_ack: bool = False,
        gateway_id: str | None = None,
    ) -> dict:
        pairing_response = self.client.post(
            "/api/gateway/pairings/intents",
            json={
                "workspace_id": workspace_id,
                "display_name": "Test Device",
                "platform": "macos",
                "runtime_access_mode": runtime_access_mode,
                "autonomous_agent_setup_warning_acknowledged": warning_ack,
            },
        )
        self.assertEqual(pairing_response.status_code, 200)
        registration_response = self.client.post(
            "/api/gateway/registrations",
            json={
                "pairing_token": pairing_response.json()["pairing_token"],
                "device_id": f"device-{uuid.uuid4().hex}",
                "gateway_id": gateway_id,
                "display_name": "Test Device",
                "platform": "macos-arm64",
                "capabilities": ["file.write", "browser.read"],
            },
        )
        self.assertEqual(registration_response.status_code, 200)
        return registration_response.json()

    def test_agent_computer_policy_mutations_require_csrf(self) -> None:
        registration_payload = self._register_gateway_with_mode()
        gateway_id = registration_payload["gateway"]["gateway_id"]

        with patch.object(routes_gateway, "validate_csrf", side_effect=HTTPException(status_code=403, detail="CSRF validation failed.")):
            response = self.client.put(
                f"/api/agent-computers/{gateway_id}/policy",
                json={
                    "workspace_id": "default",
                    "policy": {"autonomy_mode": "trusted_workstation"},
                },
            )

        self.assertEqual(response.status_code, 403)
        self.assertIn("csrf", response.text.lower())

    def test_agent_computer_policy_save_and_emergency_stop_routes(self) -> None:
        registration_payload = self._register_gateway_with_mode(gateway_id="gateway-policy-test")
        gateway_id = registration_payload["gateway"]["gateway_id"]

        with patch.object(routes_gateway, "_enforce_gateway_service_decision", return_value={"next_action": "allow_gateway_service_operation"}):
            initial = self.client.get(
                f"/api/agent-computers/{gateway_id}/policy",
                params={"workspace_id": "default"},
            )
            self.assertEqual(initial.status_code, 200)
            self.assertFalse(initial.json()["saved"])
            self.assertEqual(initial.json()["policy"]["autonomy_mode"], "ask_every_time")

            saved = self.client.put(
                f"/api/agent-computers/{gateway_id}/policy",
                json={
                    "workspace_id": "default",
                    "policy": {
                        "autonomy_mode": "trusted_workstation",
                        "filesystem_scope": ["/Users/mansur/Work"],
                        "blocked_filesystem_scope": ["/Users/mansur/Work/secrets"],
                        "domain_allowlist": ["example.com"],
                        "approval_ttl_seconds": 3600,
                    },
                },
            )
        self.assertEqual(saved.status_code, 200)
        payload = saved.json()
        self.assertTrue(payload["saved"])
        self.assertEqual(payload["policy"]["autonomy_mode"], "trusted_workstation")
        self.assertEqual(payload["policy"]["filesystem_scope"], ["/Users/mansur/Work"])

        stopped = self.client.post(
            f"/api/agent-computers/{gateway_id}/emergency-stop",
            json={"workspace_id": "default", "reason": "test stop"},
        )
        self.assertEqual(stopped.status_code, 200)
        self.assertTrue(stopped.json()["emergency_stop"]["active"])
        with self.assertRaises(kill_switch_gate.KillSwitchBlockedError):
            kill_switch_gate.assert_not_killed(gateway_id=gateway_id)

        cleared = self.client.post(
            f"/api/agent-computers/{gateway_id}/clear-emergency-stop",
            json={"workspace_id": "default"},
        )
        self.assertEqual(cleared.status_code, 200)
        self.assertFalse(cleared.json()["emergency_stop"]["active"])
        kill_switch_gate.assert_not_killed(gateway_id=gateway_id)

    def test_full_access_literal_skips_owner_prompt_for_policy_allowed_gateway_action(self) -> None:
        registration_payload = self._register_gateway_with_mode(
            runtime_access_mode="full_access",
            warning_ack=True,
            gateway_id="gateway-full-access-test",
        )
        gateway_id = registration_payload["gateway"]["gateway_id"]

        with patch.object(routes_gateway.gateway_execution_service, "execute_tool_via_gateway", new=AsyncMock(return_value={"status": "completed"})) as execute_mock, patch.object(
            routes_gateway.gateway_approval_service,
            "request_gateway_tool_approval",
            new=AsyncMock(return_value={"approval_id": "approval-1"}),
        ) as approval_mock:
            response = self.client.post(
                f"/api/gateway/registrations/{gateway_id}/tools/execute",
                json={
                    "workspace_id": "default",
                    "capability_id": "file.write",
                    "arguments": {"path": "/Users/mansur/Work/report.md"},
                    "run_id": "run-1",
                    "interactive_approvals": True,
                },
            )

        self.assertEqual(response.status_code, 200)
        execute_mock.assert_awaited_once()
        self.assertEqual(execute_mock.await_args.kwargs["agent_scope"], "sage")
        approval_mock.assert_not_awaited()

    def test_legacy_full_access_registration_requires_new_sage_ack_metadata(self) -> None:
        registration_payload = self._register_gateway_with_mode(
            runtime_access_mode="full_access",
            warning_ack=True,
            gateway_id="gateway-legacy-full-access-test",
        )
        gateway = registration_payload["gateway"]
        gateway_id = gateway["gateway_id"]
        metadata = dict(gateway.get("metadata") or {})
        metadata.pop("agent_scope", None)
        with gateway_state_repository._connect(self.db_path) as conn:
            conn.execute(
                "UPDATE gateway_registrations SET metadata = ? WHERE gateway_id = ?",
                (json.dumps(metadata), gateway_id),
            )
            conn.commit()

        with (
            patch.object(routes_gateway, "_enforce_gateway_service_decision", return_value={"next_action": "allow_gateway_service_operation"}),
            patch.object(routes_gateway.gateway_execution_service, "execute_tool_via_gateway", new=AsyncMock(return_value={"status": "completed"})) as execute_mock,
            patch.object(
                routes_gateway.gateway_approval_service,
                "request_gateway_tool_approval",
                new=AsyncMock(return_value={"approval_id": "approval-legacy"}),
            ) as approval_mock,
        ):
            response = self.client.post(
                f"/api/gateway/registrations/{gateway_id}/tools/execute",
                json={
                    "workspace_id": "default",
                    "capability_id": "file.write",
                    "arguments": {"path": "/Users/mansur/Work/report.md"},
                    "run_id": "run-legacy-full-access",
                    "interactive_approvals": True,
                },
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["error"], "FULL_ACCESS_RECONFIRMATION_REQUIRED")
        approval_mock.assert_not_awaited()
        execute_mock.assert_not_awaited()

    def test_expired_session_token_is_rejected(self) -> None:
        from server_modules import gateway_state_repository

        pairing_response = self.client.post(
            "/api/gateway/pairings/intents",
            json={"workspace_id": "default", "display_name": "Test Device", "platform": "macos"},
        )
        self.assertEqual(pairing_response.status_code, 200)
        pairing_payload = pairing_response.json()

        registration_response = self.client.post(
            "/api/gateway/registrations",
            json={
                "pairing_token": pairing_payload["pairing_token"],
                "device_id": "device-expired-1",
                "display_name": "Test Device",
                "platform": "macos-arm64",
            },
        )
        self.assertEqual(registration_response.status_code, 200)
        registration_payload = registration_response.json()
        gateway_id = registration_payload["gateway"]["gateway_id"]
        gateway_token = registration_payload["gateway_token"]

        session_response = self.client.post(
            "/api/gateway/sessions",
            json={"gateway_id": gateway_id, "gateway_token": gateway_token},
        )
        self.assertEqual(session_response.status_code, 200)
        session_payload = session_response.json()
        session_token = session_payload["session_token"]

        # Manually expire the session in the DB
        with gateway_state_repository._connect(self.db_path) as conn:
            conn.execute(
                "UPDATE gateway_sessions SET expires_at = ? WHERE session_token_hash = ?",
                (
                    (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
                    gateway_state_repository._hash_token(session_token),
                ),
            )
            conn.commit()

        # WS connection with expired session should be rejected
        ws_path = (
            f"/api/gateway/ws?gateway_id={gateway_id}"
            f"&session_token={session_token}"
        )
        with self.assertRaises(WebSocketDisconnect) as raised:
            with self.client.websocket_connect(ws_path) as websocket:
                websocket.receive_text()
        self.assertEqual(raised.exception.code, 4401)
        self.assertIn("expired", raised.exception.reason.lower())

    def test_gateway_websocket_rejects_query_session_token_in_production(self) -> None:
        registration_payload = self._register_gateway()
        gateway_id = registration_payload["gateway"]["gateway_id"]
        session_response = self.client.post(
            "/api/gateway/sessions",
            json={"gateway_id": gateway_id, "gateway_token": registration_payload["gateway_token"]},
        )
        self.assertEqual(session_response.status_code, 200)
        session_payload = session_response.json()
        ws_path = f"/api/gateway/ws?gateway_id={gateway_id}&session_token={session_payload['session_token']}"

        with patch.dict(os.environ, {"ORION_ENV": "production"}, clear=False):
            with self.assertRaises(WebSocketDisconnect) as raised:
                with self.client.websocket_connect(ws_path) as websocket:
                    websocket.receive_text()
        self.assertEqual(raised.exception.code, 4401)
        self.assertIn("query session token", raised.exception.reason.lower())

    def test_gateway_websocket_accepts_session_token_subprotocol_in_production(self) -> None:
        registration_payload = self._register_gateway()
        gateway_id = registration_payload["gateway"]["gateway_id"]
        session_response = self.client.post(
            "/api/gateway/sessions",
            json={"gateway_id": gateway_id, "gateway_token": registration_payload["gateway_token"]},
        )
        self.assertEqual(session_response.status_code, 200)
        session_payload = session_response.json()
        ws_path = f"/api/gateway/ws?gateway_id={gateway_id}"

        with patch.dict(os.environ, {"ORION_ENV": "production"}, clear=False):
            with self.client.websocket_connect(
                ws_path,
                subprotocols=[
                    "empyralis.gateway.v1",
                    f"empyralis.gateway.session.{session_payload['session_token']}",
                ],
            ) as websocket:
                self.assertEqual(websocket.accepted_subprotocol, "empyralis.gateway.v1")

    def _register_gateway(self, *, capabilities: list[str] | None = None) -> dict:
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
                "capabilities": list(capabilities or ["screen.read", "system.presence"]),
            },
        )
        self.assertEqual(registration_response.status_code, 200)
        return registration_response.json()

    def test_pairing_intent_requires_full_access_warning_acknowledgement(self) -> None:
        response = self.client.post(
            "/api/gateway/pairings/intents",
            json={
                "workspace_id": "default",
                "display_name": "Mansur Mac mini",
                "platform": "macos",
                "runtime_access_mode": "full_access",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Full Access setup warning", response.json()["detail"])

    def test_pairing_runtime_access_mode_persists_to_gateway_registration(self) -> None:
        pairing_response = self.client.post(
            "/api/gateway/pairings/intents",
            json={
                "workspace_id": "default",
                "display_name": "Mansur Mac mini",
                "platform": "macos",
                "runtime_access_mode": "full_access",
                "autonomous_agent_setup_warning_acknowledged": True,
            },
        )
        self.assertEqual(pairing_response.status_code, 200)
        pairing_payload = pairing_response.json()
        self.assertEqual(pairing_payload["metadata"]["runtime_access_mode"], "full_access")

        registration_response = self.client.post(
            "/api/gateway/registrations",
            json={
                "pairing_token": pairing_payload["pairing_token"],
                "device_id": "device-dedicated-1",
                "display_name": "Mansur Mac mini",
                "platform": "macos-arm64",
                "capabilities": ["screen.read", "shell.execute"],
                "metadata": {"runtime_access_mode": "default_guarded"},
            },
        )

        self.assertEqual(registration_response.status_code, 200)
        payload = registration_response.json()
        self.assertEqual(payload["gateway"]["runtime_access_mode"], "full_access")
        self.assertEqual(payload["gateway"]["runtime_access_label"], "Full Access")
        self.assertTrue(payload["gateway"]["autonomous_agent_setup_warning_acknowledged"])
        gateway_id = payload["gateway"]["gateway_id"]
        registration = gateway_state_repository.get_gateway_registration(gateway_id)
        self.assertEqual(registration["metadata"]["runtime_access_mode"], "full_access")
        self.assertEqual(registration["metadata"]["agent_scope"], "sage")

    def test_pairing_custom_runtime_access_mode_is_guarded_and_labeled(self) -> None:
        pairing_response = self.client.post(
            "/api/gateway/pairings/intents",
            json={
                "workspace_id": "default",
                "display_name": "Mansur Custom Mac",
                "platform": "macos",
                "runtime_access_mode": "custom",
            },
        )

        self.assertEqual(pairing_response.status_code, 200)
        pairing_payload = pairing_response.json()
        self.assertEqual(pairing_payload["metadata"]["runtime_access_mode"], "custom")
        self.assertEqual(pairing_payload["metadata"]["runtime_access_label"], "Custom")
        self.assertNotIn("autonomous_agent_setup_warning_acknowledged", pairing_payload["metadata"])

    def test_gateway_registration_cannot_escalate_default_pairing_to_full_access(self) -> None:
        pairing_response = self.client.post(
            "/api/gateway/pairings/intents",
            json={
                "workspace_id": "default",
                "display_name": "Mansur Mac",
                "platform": "macos",
                "runtime_access_mode": "default_guarded",
            },
        )
        self.assertEqual(pairing_response.status_code, 200)
        pairing_payload = pairing_response.json()

        registration_response = self.client.post(
            "/api/gateway/registrations",
            json={
                "pairing_token": pairing_payload["pairing_token"],
                "device_id": "device-default-1",
                "display_name": "Mansur Mac",
                "platform": "macos-arm64",
                "capabilities": ["screen.read"],
                "metadata": {
                    "runtime_access_mode": "full_access",
                    "autonomous_agent_setup_warning_acknowledged": True,
                },
            },
        )

        self.assertEqual(registration_response.status_code, 200)
        payload = registration_response.json()
        self.assertEqual(payload["gateway"]["runtime_access_mode"], "default_guarded")
        self.assertEqual(payload["gateway"]["runtime_access_label"], "Default")

    def test_dedicated_workstation_bind_creates_profile_and_readiness(self) -> None:
        registration_payload = self._register_gateway()
        gateway_id = registration_payload["gateway"]["gateway_id"]

        response = self.client.post(
            f"/api/gateway/registrations/{gateway_id}/dedicated-workstation/bind",
            json={
                "workspace_id": "default",
                "policy_id": "policy-dedicated",
                "machine_label": "Mansur Mac mini",
                "trace_id": "trace-dedicated-bind",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["readiness"], "offline")
        self.assertFalse(payload["ready"])
        self.assertIn("connection_offline", payload["blockers"])
        self.assertEqual(payload["profile"]["environment_kind"], "dedicated_workstation")
        self.assertTrue(payload["profile"]["dedicated_to_agent"])
        self.assertEqual(payload["profile"]["gateway_id"], gateway_id)
        self.assertEqual(payload["profile"]["policy_id"], "policy-dedicated")

        registration = gateway_state_repository.get_gateway_registration(gateway_id)
        self.assertEqual(
            registration["metadata"]["dedicated_workstation"]["profile_id"],
            payload["profile"]["profile_id"],
        )

    def test_dedicated_workstation_readiness_reports_ready_for_healthy_session(self) -> None:
        registration_payload = self._register_gateway()
        gateway_id = registration_payload["gateway"]["gateway_id"]
        session_response = self.client.post(
            "/api/gateway/sessions",
            json={
                "gateway_id": gateway_id,
                "gateway_token": registration_payload["gateway_token"],
            },
        )
        self.assertEqual(session_response.status_code, 200)
        session_id = session_response.json()["session_id"]
        gateway_state_repository.mark_gateway_session_connected(session_id, db_path=self.db_path)
        gateway_state_repository.touch_gateway_session(
            session_id=session_id,
            gateway_id=gateway_id,
            health_state="healthy",
            db_path=self.db_path,
        )

        bind_response = self.client.post(
            f"/api/gateway/registrations/{gateway_id}/dedicated-workstation/bind",
            json={"workspace_id": "default", "policy_id": "policy-dedicated"},
        )
        self.assertEqual(bind_response.status_code, 200)

        readiness_response = self.client.get(
            f"/api/gateway/registrations/{gateway_id}/dedicated-workstation/readiness",
            params={"workspace_id": "default", "trace_id": "trace-ready"},
        )

        self.assertEqual(readiness_response.status_code, 200)
        self.assertEqual(readiness_response.json()["readiness"], "ready")
        self.assertTrue(readiness_response.json()["ready"])
        self.assertEqual(readiness_response.json()["blockers"], [])

    def test_dedicated_workstation_bind_rejects_wrong_workspace(self) -> None:
        registration_payload = self._register_gateway()
        gateway_id = registration_payload["gateway"]["gateway_id"]
        self.app.dependency_overrides[routes_gateway.require_api_key] = lambda: self._other_workspace_user()

        response = self.client.post(
            f"/api/gateway/registrations/{gateway_id}/dedicated-workstation/bind",
            json={"workspace_id": "other", "policy_id": "policy-dedicated"},
        )

        self.assertEqual(response.status_code, 403)

    def test_dedicated_workstation_kill_blocks_and_clear_reenables_gateway(self) -> None:
        registration_payload = self._register_gateway()
        gateway_id = registration_payload["gateway"]["gateway_id"]
        bind_response = self.client.post(
            f"/api/gateway/registrations/{gateway_id}/dedicated-workstation/bind",
            json={"workspace_id": "default", "policy_id": "policy-dedicated"},
        )
        self.assertEqual(bind_response.status_code, 200)

        kill_response = self.client.post(
            f"/api/gateway/registrations/{gateway_id}/dedicated-workstation/kill",
            json={
                "workspace_id": "default",
                "reason": "operator stop",
                "trace_id": "trace-dedicated-kill",
            },
        )
        self.assertEqual(kill_response.status_code, 200)
        self.assertEqual(kill_response.json()["readiness"], "killed")

        blocked_response = self.client.post(
            f"/api/gateway/registrations/{gateway_id}/tools/execute",
            json={
                "capability_id": "screen.read",
                "arguments": {},
                "run_id": "run-after-dedicated-kill",
                "trace_id": "trace-after-dedicated-kill",
                "request_id": "req-after-dedicated-kill",
                "interactive_approvals": False,
            },
        )
        self.assertEqual(blocked_response.status_code, 503)
        self.assertEqual(blocked_response.json()["detail"]["error"]["code"], "KILL_SWITCH_ACTIVE")

        clear_response = self.client.post(
            f"/api/gateway/registrations/{gateway_id}/dedicated-workstation/clear-kill",
            json={"workspace_id": "default", "trace_id": "trace-dedicated-clear"},
        )
        self.assertEqual(clear_response.status_code, 200)
        self.assertNotEqual(clear_response.json()["readiness"], "killed")

        not_kill_response = self.client.post(
            f"/api/gateway/registrations/{gateway_id}/tools/execute",
            json={
                "capability_id": "screen.read",
                "arguments": {},
                "run_id": "run-after-dedicated-clear",
                "trace_id": "trace-after-dedicated-clear",
                "request_id": "req-after-dedicated-clear",
                "interactive_approvals": False,
            },
        )
        self.assertNotEqual(not_kill_response.status_code, 503)

    def test_revoke_gateway_marks_dedicated_profile_revoked(self) -> None:
        registration_payload = self._register_gateway()
        gateway_id = registration_payload["gateway"]["gateway_id"]
        bind_response = self.client.post(
            f"/api/gateway/registrations/{gateway_id}/dedicated-workstation/bind",
            json={"workspace_id": "default", "policy_id": "policy-dedicated"},
        )
        self.assertEqual(bind_response.status_code, 200)
        profile_id = bind_response.json()["profile"]["profile_id"]

        revoke_response = self.client.post(
            f"/api/gateway/registrations/{gateway_id}/revoke",
            json={"reason": "retired dedicated machine"},
        )

        self.assertEqual(revoke_response.status_code, 200)
        profile = agent_computer_profile_service.get_agent_computer_profile(
            workspace_id="default",
            profile_id=profile_id,
        )
        self.assertIsNotNone(profile)
        self.assertEqual(profile.health_state, "revoked")

    def test_workspace_kill_switch_blocks_gateway_tool_execution(self) -> None:
        registration_payload = self._register_gateway()
        gateway_id = registration_payload["gateway"]["gateway_id"]
        safe_mode_service.set_kill_switch(
            scope="workspace",
            enabled=True,
            workspace_id="default",
            reason="closed pilot smoke stop",
        )

        response = self.client.post(
            f"/api/gateway/registrations/{gateway_id}/tools/execute",
            json={
                "capability_id": "browser.session.start",
                "arguments": {},
                "run_id": "run-kill-switch",
                "trace_id": "trace-kill-switch",
                "request_id": "req-kill-switch",
            },
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["error"]["code"], "KILL_SWITCH_ACTIVE")

        safe_mode_service.set_kill_switch(
            scope="workspace",
            enabled=False,
            workspace_id="default",
            reason="closed pilot smoke resume",
        )
        response_after_clear = self.client.post(
            f"/api/gateway/registrations/{gateway_id}/tools/execute",
            json={
                "capability_id": "browser.session.start",
                "arguments": {},
                "run_id": "run-kill-switch-clear",
                "trace_id": "trace-kill-switch-clear",
                "request_id": "req-kill-switch-clear",
            },
        )
        self.assertEqual(response_after_clear.status_code, 202)

    def test_pair_register_connect_heartbeat_and_reconnect(self) -> None:
        registration_payload = self._register_gateway()
        gateway_id = registration_payload["gateway"]["gateway_id"]
        gateway_token = registration_payload["gateway_token"]

        session_response = self.client.post(
            "/api/gateway/sessions",
            json={"gateway_id": gateway_id, "gateway_token": gateway_token},
        )
        self.assertEqual(session_response.status_code, 200)
        session_payload = session_response.json()
        self.assertEqual(session_payload["scope"]["gateway_id"], gateway_id)
        self.assertEqual(session_payload["scope"]["device_id"], "device-local-1")
        self.assertIn("/api/gateway/ws", session_payload["ws_url"])

        ws_path = (
            f"/api/gateway/ws?gateway_id={gateway_id}"
            f"&session_token={session_payload['session_token']}"
        )
        with self.client.websocket_connect(ws_path) as websocket:
            websocket.send_json(
                {
                    "kind": "request",
                    "id": "req-connect-1",
                    "type": "gateway.connect",
                    "ts": "2026-04-22T12:00:00Z",
                    "scope": session_payload["scope"],
                    "payload": {
                        "protocol_version": "v1alpha2",
                        "gateway_version": "0.1.0",
                        "device_metadata": {"hostname": "mansur-mac"},
                        "requested_capabilities": ["screen.read"],
                        "journal_cursor": 0,
                        "checkpoint_cursor": 0,
                    },
                }
            )
            connect_ack = websocket.receive_json()
            hello_event = websocket.receive_json()
            presence_event = websocket.receive_json()
            self.assertTrue(connect_ack["ok"])
            self.assertEqual(hello_event["type"], "gateway.hello")
            self.assertEqual(presence_event["type"], "gateway.presence")

            websocket.send_json(
                {
                    "kind": "request",
                    "id": "req-heartbeat-1",
                    "type": "gateway.heartbeat",
                    "ts": "2026-04-22T12:00:01Z",
                    "scope": session_payload["scope"],
                    "payload": {
                        "health_state": "online",
                        "journal_cursor": 5,
                        "checkpoint_cursor": 3,
                        "capability_readiness": {"screen.read": "ready"},
                        "service_inventory": [
                            {
                                "id": "postgres",
                                "label": "Postgres",
                                "kind": "database",
                                "status": "ready",
                                "detected": True,
                                "passive": False,
                                "execution_enabled": True,
                                "check": "pg_isready -q",
                                "summary": "ready",
                                "last_checked_at": "2026-05-29T00:00:00Z",
                            }
                        ],
                        "native_runtime": {
                            "os": "darwin",
                            "arch": "arm64",
                            "release": "25.0.0",
                            "hostname": "mansur-mac",
                            "desktop_session": "system_service",
                            "system_service_mode": True,
                        },
                    },
                }
            )
            heartbeat_ack = websocket.receive_json()
            self.assertTrue(heartbeat_ack["ok"])
            latest_session = gateway_state_repository.get_latest_gateway_session(gateway_id)
            inventory = latest_session["metadata"]["service_inventory"]
            self.assertEqual(inventory[0]["id"], "postgres")
            self.assertTrue(inventory[0]["passive"])
            self.assertFalse(inventory[0]["execution_enabled"])
            native_runtime = latest_session["metadata"]["native_runtime"]
            self.assertEqual(native_runtime["desktop_session"], "system_service")
            self.assertTrue(native_runtime["system_service_mode"])

            websocket.send_json(
                {
                    "kind": "request",
                    "id": "req-state-1",
                    "type": "gateway.state.update",
                    "ts": "2026-04-22T12:00:02Z",
                    "scope": session_payload["scope"],
                    "payload": {
                        "status": "online",
                        "health_state": "online",
                        "journal_cursor": 7,
                        "checkpoint_cursor": 4,
                    },
                }
            )
            state_ack = websocket.receive_json()
            state_presence = websocket.receive_json()
            self.assertTrue(state_ack["ok"])
            self.assertEqual(state_presence["type"], "gateway.presence")

            websocket.send_json(
                {
                    "kind": "request",
                    "id": "req-disconnect-1",
                    "type": "gateway.disconnect",
                    "ts": "2026-04-22T12:00:03Z",
                    "scope": session_payload["scope"],
                    "payload": {"reason": "test_disconnect"},
                }
            )
            disconnect_ack = websocket.receive_json()
            self.assertTrue(disconnect_ack["ok"])

        list_response = self.client.get("/api/gateway/registrations", params={"workspace_id": "default"})
        self.assertEqual(list_response.status_code, 200)
        list_payload = list_response.json()
        self.assertEqual(list_payload["count"], 1)
        self.assertEqual(list_payload["items"][0]["gateway_id"], gateway_id)
        self.assertEqual(list_payload["items"][0]["journal_cursor"], 7)
        self.assertEqual(list_payload["items"][0]["checkpoint_cursor"], 4)
        self.assertEqual(list_payload["items"][0]["device_trust_state"], "verified")

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
                    "id": "req-connect-2",
                    "type": "gateway.connect",
                    "ts": "2026-04-22T12:00:10Z",
                    "scope": reconnect_session["scope"],
                    "payload": {
                        "protocol_version": "v1alpha2",
                        "gateway_version": "0.1.0",
                        "device_metadata": {"hostname": "mansur-mac"},
                        "requested_capabilities": ["screen.read"],
                        "journal_cursor": 7,
                        "checkpoint_cursor": 4,
                    },
                }
            )
            reconnect_ack = websocket.receive_json()
            reconnect_hello = websocket.receive_json()
            self.assertTrue(reconnect_ack["ok"])
            self.assertEqual(reconnect_hello["type"], "gateway.hello")

    def test_gateway_pairing_intents_are_ttl_limited_and_pending_capped(self) -> None:
        over_limit_response = self.client.post(
            "/api/gateway/pairings/intents",
            json={"workspace_id": "default", "ttl_seconds": 7200},
        )
        self.assertEqual(over_limit_response.status_code, 422)

        first_pairing = self.client.post(
            "/api/gateway/pairings/intents",
            json={"workspace_id": "default", "ttl_seconds": 3600},
        )
        self.assertEqual(first_pairing.status_code, 200)
        first_payload = first_pairing.json()
        expires_at = datetime.fromisoformat(first_payload["expires_at"].replace("Z", "+00:00"))
        created_at = datetime.fromisoformat(first_payload["created_at"].replace("Z", "+00:00"))
        ttl_seconds = (expires_at - created_at).total_seconds()
        self.assertLessEqual(ttl_seconds, 3601)
        self.assertGreater(ttl_seconds, 3500)

        for _ in range(routes_gateway.gateway_pairing_service.MAX_PENDING_GATEWAY_PAIRING_INTENTS - 1):
            capped_response = self.client.post(
                "/api/gateway/pairings/intents",
                json={"workspace_id": "default"},
            )
            self.assertEqual(capped_response.status_code, 200)

        blocked_response = self.client.post(
            "/api/gateway/pairings/intents",
            json={"workspace_id": "default"},
        )
        self.assertEqual(blocked_response.status_code, 429)
        self.assertIn("too many pending gateway pairing requests", blocked_response.json()["detail"].lower())

    def test_gateway_pairing_token_is_one_time_use(self) -> None:
        pairing_response = self.client.post(
            "/api/gateway/pairings/intents",
            json={"workspace_id": "default", "display_name": "Mansur Mac", "platform": "macos"},
        )
        self.assertEqual(pairing_response.status_code, 200)
        pairing_token = pairing_response.json()["pairing_token"]
        first_registration = self.client.post(
            "/api/gateway/registrations",
            json={
                "pairing_token": pairing_token,
                "device_id": "device-local-1",
                "display_name": "Mansur Mac",
                "platform": "macos-arm64",
                "capabilities": ["screen.read"],
            },
        )
        self.assertEqual(first_registration.status_code, 200)

        reused_registration = self.client.post(
            "/api/gateway/registrations",
            json={
                "pairing_token": pairing_token,
                "device_id": "device-local-2",
                "display_name": "Mansur Mac 2",
                "platform": "macos-arm64",
                "capabilities": ["screen.read"],
            },
        )
        self.assertEqual(reused_registration.status_code, 400)
        self.assertIn("no longer active", reused_registration.json().get("detail", "").lower())

    def test_gateway_connect_rejects_scope_mismatch(self) -> None:
        registration_payload = self._register_gateway()
        gateway_id = registration_payload["gateway"]["gateway_id"]
        gateway_token = registration_payload["gateway_token"]

        session_response = self.client.post(
            "/api/gateway/sessions",
            json={"gateway_id": gateway_id, "gateway_token": gateway_token},
        )
        self.assertEqual(session_response.status_code, 200)
        session_payload = session_response.json()
        bad_scope = dict(session_payload["scope"])
        bad_scope.pop("gateway_id", None)

        ws_path = f"/api/gateway/ws?gateway_id={gateway_id}&session_token={session_payload['session_token']}"
        with self.client.websocket_connect(ws_path) as websocket:
            websocket.send_json(
                {
                    "kind": "request",
                    "id": "req-connect-bad-scope",
                    "type": "gateway.connect",
                    "scope": bad_scope,
                    "payload": {"protocol_version": "v1alpha2", "gateway_version": "0.1.0"},
                }
            )
            response = websocket.receive_json()
            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "scope_mismatch")

        latest_session = gateway_state_repository.get_latest_gateway_session(gateway_id, include_revoked=True)
        self.assertIsNotNone(latest_session)
        metadata = dict((latest_session or {}).get("metadata") or {})
        self.assertEqual(metadata.get("disconnect_reason"), "scope_mismatch")

    def test_gateway_replay_sequence_is_rejected(self) -> None:
        registration_payload = self._register_gateway()
        gateway_id = registration_payload["gateway"]["gateway_id"]
        gateway_token = registration_payload["gateway_token"]

        session_response = self.client.post(
            "/api/gateway/sessions",
            json={"gateway_id": gateway_id, "gateway_token": gateway_token},
        )
        self.assertEqual(session_response.status_code, 200)
        session_payload = session_response.json()
        ws_path = f"/api/gateway/ws?gateway_id={gateway_id}&session_token={session_payload['session_token']}"
        with self.client.websocket_connect(ws_path) as websocket:
            websocket.send_json(
                {
                    "kind": "request",
                    "id": "req-connect-1",
                    "type": "gateway.connect",
                    "seq": 1,
                    "scope": session_payload["scope"],
                    "payload": {"protocol_version": "v1alpha2", "gateway_version": "0.1.0"},
                }
            )
            connect_ack = websocket.receive_json()
            self.assertTrue(connect_ack["ok"])
            websocket.receive_json()
            websocket.receive_json()

            websocket.send_json(
                {
                    "kind": "request",
                    "id": "hb-1",
                    "type": "gateway.heartbeat",
                    "seq": 2,
                    "scope": session_payload["scope"],
                    "payload": {"health_state": "online"},
                }
            )
            heartbeat_ack = websocket.receive_json()
            self.assertTrue(heartbeat_ack["ok"])

            websocket.send_json(
                {
                    "kind": "request",
                    "id": "hb-replay",
                    "type": "gateway.heartbeat",
                    "seq": 2,
                    "scope": session_payload["scope"],
                    "payload": {"health_state": "online"},
                }
            )
            replay_response = websocket.receive_json()
            self.assertFalse(replay_response["ok"])
            self.assertEqual(replay_response["error"]["code"], "gateway_frame_replayed")

        latest_session = gateway_state_repository.get_latest_gateway_session(gateway_id, include_revoked=True)
        self.assertIsNotNone(latest_session)
        metadata = dict((latest_session or {}).get("metadata") or {})
        self.assertEqual(metadata.get("disconnect_reason"), "gateway_frame_replayed")

    @patch(
        "server_modules.personal_channels_service.gateway_execution_service.execute_tool_via_gateway",
        new_callable=AsyncMock,
    )
    @patch("server_modules.personal_channels_service._enforce_personal_gateway_config_decision")
    @patch("server_modules.routes_personal_channels.security_audit_service.emit_security_audit_event")
    def test_configure_telegram_personal_gateway_emits_credential_audit(
        self,
        audit_mock,
        _gate_mock,
        execute_tool_mock: AsyncMock,
    ) -> None:
        registration_payload = self._register_gateway()
        gateway_id = registration_payload["gateway"]["gateway_id"]
        execute_tool_mock.return_value = {
            "gateway_id": gateway_id,
            "result": {
                "status": "updated",
                "reconnect_requested": True,
                "config": {
                    "has_api_id": True,
                    "has_api_hash": True,
                    "has_phone_number": True,
                },
                "state": {
                    "status": "code_required",
                    "login_hint": "******1234",
                },
            },
        }

        response = self.client.post(
            f"/api/personal-channels/telegram/gateways/{gateway_id}/setup",
            json={
                "api_id": 123456,
                "api_hash": "hash-123",
                "phone_number": "+8618657105303",
            },
        )

        self.assertEqual(response.status_code, 200)
        metadata = audit_mock.call_args.kwargs["metadata"]
        self.assertEqual(metadata["action_class"], "credential_change")
        self.assertEqual(metadata["risk_level"], "high")
        self.assertEqual(metadata["governance_boundary"], "paired_gateway")
        self.assertFalse(metadata["requires_approval"])
        self.assertFalse(metadata["external_side_effect"])

    @patch(
        "server_modules.personal_channels_service.gateway_execution_service.execute_tool_via_gateway",
        new_callable=AsyncMock,
    )
    @patch("server_modules.personal_channels_service._enforce_personal_gateway_config_decision")
    def test_configure_telegram_personal_gateway_dispatches_config_capability(self, _gate_mock, execute_tool_mock: AsyncMock) -> None:
        registration_payload = self._register_gateway()
        gateway_id = registration_payload["gateway"]["gateway_id"]
        execute_tool_mock.return_value = {
            "gateway_id": gateway_id,
            "result": {
                "status": "updated",
                "reconnect_requested": True,
                "config": {
                    "has_api_id": True,
                    "has_api_hash": True,
                    "has_phone_number": True,
                },
                "state": {
                    "status": "code_required",
                    "login_hint": "******1234",
                },
            },
        }

        response = self.client.post(
            f"/api/personal-channels/telegram/gateways/{gateway_id}/setup",
            json={
                "api_id": 123456,
                "api_hash": "hash-123",
                "phone_number": "+8618657105303",
                "login_code": "12345",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["gateway_id"], gateway_id)
        self.assertEqual(payload["channel_key"], "telegram_personal")
        self.assertEqual(payload["status"], "updated")
        self.assertEqual(payload["state"]["status"], "code_required")
        self.assertEqual(
            execute_tool_mock.await_args.kwargs["capability_id"],
            "channel.telegram.personal.configure",
        )
        self.assertEqual(
            execute_tool_mock.await_args.kwargs["arguments"],
            {
                "api_id": 123456,
                "api_hash": "hash-123",
                "phone_number": "+8618657105303",
                "login_code": "12345",
            },
        )

    @patch(
        "server_modules.personal_channels_service.gateway_execution_service.execute_tool_via_gateway",
        new_callable=AsyncMock,
    )
    @patch("server_modules.personal_channels_service._enforce_personal_gateway_config_decision")
    def test_configure_whatsapp_personal_gateway_dispatches_config_capability(self, _gate_mock, execute_tool_mock: AsyncMock) -> None:
        registration_payload = self._register_gateway()
        gateway_id = registration_payload["gateway"]["gateway_id"]
        execute_tool_mock.return_value = {
            "gateway_id": gateway_id,
            "result": {
                "status": "updated",
                "reconnect_requested": True,
                "config": {
                    "has_phone_number": True,
                },
                "state": {
                    "status": "pairing_code_required",
                    "login_hint": "*******5303",
                    "pairing_code": "K6YNWTTP",
                },
            },
        }

        response = self.client.post(
            f"/api/personal-channels/whatsapp/gateways/{gateway_id}/setup",
            json={
                "phone_number": "8618657105303",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["gateway_id"], gateway_id)
        self.assertEqual(payload["channel_key"], "whatsapp_personal")
        self.assertEqual(payload["status"], "updated")
        self.assertEqual(payload["state"]["pairing_code"], "K6YNWTTP")
        self.assertEqual(
            execute_tool_mock.await_args.kwargs["capability_id"],
            "channel.whatsapp.personal.configure",
        )
        self.assertEqual(
            execute_tool_mock.await_args.kwargs["arguments"],
            {
                "phone_number": "8618657105303",
            },
        )

    @patch(
        "server_modules.personal_channels_service.send_whatsapp_personal_message",
        new_callable=AsyncMock,
    )
    @patch("server_modules.routes_personal_channels._enforce_personal_channel_approval_request")
    @patch(
        "server_modules.routes_personal_channels.gateway_approval_service.request_gateway_tool_approval",
        new_callable=AsyncMock,
    )
    @patch("server_modules.routes_personal_channels.security_audit_service.emit_security_audit_event")
    def test_send_whatsapp_personal_message_requires_approval_before_dispatch(
        self,
        audit_mock,
        approval_request_mock: AsyncMock,
        _approval_gate_mock,
        send_message_mock: AsyncMock,
    ) -> None:
        registration_payload = self._register_gateway()
        gateway_id = registration_payload["gateway"]["gateway_id"]
        approval_request_mock.return_value = {
            "approval_id": "approval-wa-1",
            "gateway_id": gateway_id,
            "status": "pending",
        }
        send_message_mock.return_value = {
            "gateway_id": gateway_id,
            "channel_key": "whatsapp_personal",
            "status": "queued",
        }

        response = self.client.post(
            f"/api/personal-channels/whatsapp/gateways/{gateway_id}/messages",
            json={
                "remote_jid": "8618657105303@s.whatsapp.net",
                "text": "hello from sage",
                "idempotency_key": "test-send-1",
            },
        )

        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertEqual(payload["status"], "approval_required")
        self.assertEqual(payload["approval"]["approval_id"], "approval-wa-1")
        self.assertEqual(payload["channel_approval"]["approve_text"], "approve approval-wa-1")
        self.assertEqual(payload["channel_approval"]["deny_text"], "deny approval-wa-1")
        self.assertIn("Reply approve approval-wa-1 or deny approval-wa-1", payload["channel_approval"]["instruction"])
        send_message_mock.assert_not_awaited()
        self.assertEqual(approval_request_mock.await_args.kwargs["capability_id"], "channel.whatsapp.personal.send")
        self.assertEqual(approval_request_mock.await_args.kwargs["arguments"]["text"], "hello from sage")
        metadata = audit_mock.call_args.kwargs["metadata"]
        self.assertEqual(metadata["action_class"], "channel_send")
        self.assertEqual(metadata["risk_level"], "critical")
        self.assertEqual(metadata["governance_boundary"], "paired_gateway")
        self.assertTrue(metadata["requires_approval"])
        self.assertTrue(metadata["external_side_effect"])
        self.assertEqual(metadata["text_length"], len("hello from sage"))
        self.assertEqual(audit_mock.call_args.kwargs["status"], "approval_required")

    @patch(
        "server_modules.personal_channels_service.send_telegram_personal_message",
        new_callable=AsyncMock,
    )
    @patch("server_modules.routes_personal_channels._enforce_personal_channel_approval_request")
    @patch(
        "server_modules.routes_personal_channels.gateway_approval_service.request_gateway_tool_approval",
        new_callable=AsyncMock,
    )
    def test_send_telegram_personal_message_requires_approval_before_dispatch(
        self,
        approval_request_mock: AsyncMock,
        _approval_gate_mock,
        send_message_mock: AsyncMock,
    ) -> None:
        registration_payload = self._register_gateway()
        gateway_id = registration_payload["gateway"]["gateway_id"]
        approval_request_mock.return_value = {
            "approval_id": "approval-tg-1",
            "gateway_id": gateway_id,
            "status": "pending",
        }

        response = self.client.post(
            f"/api/personal-channels/telegram/gateways/{gateway_id}/messages",
            json={
                "remote_jid": "telegram-user-1",
                "text": "hello from sage",
                "idempotency_key": "test-send-tg-1",
            },
        )

        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertEqual(payload["status"], "approval_required")
        self.assertEqual(payload["channel_approval"]["approve_text"], "approve approval-tg-1")
        self.assertEqual(payload["channel_approval"]["deny_text"], "deny approval-tg-1")
        send_message_mock.assert_not_awaited()
        self.assertEqual(approval_request_mock.await_args.kwargs["capability_id"], "channel.telegram.personal.send")

    def test_personal_gateway_channel_surfaces_project_live_and_reserved_channels(self) -> None:
        registration_payload = self._register_gateway_with_mode(gateway_id="gateway-channel-surfaces")
        gateway_id = registration_payload["gateway"]["gateway_id"]
        gateway_state_repository.update_gateway_registration_state(
            gateway_id=gateway_id,
            metadata={
                "personal_channel_manifests": [
                    {
                        "channel_key": "telegram_personal",
                        "label": "Telegram Personal",
                        "provider": "telegram_gramjs",
                        "runtime_lane": "personal_gateway",
                        "stage": "live",
                        "status": "connected",
                        "live_capable": True,
                        "capabilities": ["inbound", "outbound", "text"],
                    },
                    {
                        "channel_key": "signal_personal",
                        "label": "Signal Personal",
                        "provider": "signal_local_bridge",
                        "stage": "live",
                        "status": "not_configured",
                        "live_capable": True,
                        "api_token": "sk-super-secret-token",
                    },
                ],
                "personal_channel_health": [
                    {
                        "channel_key": "telegram_personal",
                        "provider": "telegram_gramjs",
                        "status": "connected",
                        "running": True,
                        "connected": True,
                    },
                    {
                        "channel_key": "signal_personal",
                        "provider": "signal_local_bridge",
                        "status": "not_configured",
                        "running": False,
                        "connected": False,
                    },
                ],
            },
        )
        personal_channels_repository.upsert_telegram_state(
            gateway_id=gateway_id,
            tenant_id="default",
            workspace_id="default",
            user_id="owner-1",
            channel_key="telegram_personal",
            provider="telegram_gramjs",
            status="connected",
            linked_username="mansur",
            connected_at="2026-05-25T00:00:00Z",
        )

        response = self.client.get(f"/api/personal-channels/gateways/{gateway_id}/channels")

        self.assertEqual(response.status_code, 200)
        by_key = {item["channel_key"]: item for item in response.json()["items"]}
        self.assertEqual(
            set(by_key),
            {"telegram_personal", "whatsapp_personal", "signal_personal", "imessage_personal", "wechat_personal"},
        )
        self.assertTrue(by_key["telegram_personal"]["connected"])
        self.assertTrue(by_key["telegram_personal"]["running"])
        self.assertEqual(by_key["telegram_personal"]["connected_identity"], "mansur")
        self.assertEqual(by_key["signal_personal"]["status"], "not_configured")
        self.assertFalse(by_key["signal_personal"]["live_capable"])
        self.assertEqual(by_key["signal_personal"]["manifest"]["api_token"], "[redacted]")
        self.assertEqual(by_key["imessage_personal"]["status"], "agent_computer_bridge")
        self.assertEqual(by_key["wechat_personal"]["status"], "agent_computer_bridge")

    def test_rotate_token_rejects_stale_gateway_token(self) -> None:
        registration_payload = self._register_gateway()
        gateway_id = registration_payload["gateway"]["gateway_id"]
        old_token = registration_payload["gateway_token"]

        rotate_response = self.client.post(f"/api/gateway/registrations/{gateway_id}/rotate-token")
        self.assertEqual(rotate_response.status_code, 200)
        rotated_payload = rotate_response.json()
        new_token = rotated_payload["gateway_token"]
        self.assertNotEqual(new_token, old_token)

        stale_response = self.client.post(
            "/api/gateway/sessions",
            json={"gateway_id": gateway_id, "gateway_token": old_token},
        )
        self.assertEqual(stale_response.status_code, 401)

        fresh_response = self.client.post(
            "/api/gateway/sessions",
            json={"gateway_id": gateway_id, "gateway_token": new_token},
        )
        self.assertEqual(fresh_response.status_code, 200)

    def test_wrong_workspace_user_cannot_rotate_gateway_token(self) -> None:
        registration_payload = self._register_gateway()
        gateway_id = registration_payload["gateway"]["gateway_id"]

        self.app.dependency_overrides[routes_gateway.require_api_key] = lambda: self._other_workspace_user()
        denied_response = self.client.post(f"/api/gateway/registrations/{gateway_id}/rotate-token")
        self.assertEqual(denied_response.status_code, 403)

    def test_revoke_gateway_blocks_future_sessions(self) -> None:
        registration_payload = self._register_gateway()
        gateway_id = registration_payload["gateway"]["gateway_id"]
        gateway_token = registration_payload["gateway_token"]

        initial_session = self.client.post(
            "/api/gateway/sessions",
            json={"gateway_id": gateway_id, "gateway_token": gateway_token},
        )
        self.assertEqual(initial_session.status_code, 200)

        revoke_response = self.client.post(
            f"/api/gateway/registrations/{gateway_id}/revoke",
            json={"reason": "owner_revoked_gateway"},
        )
        self.assertEqual(revoke_response.status_code, 200)
        revoked_payload = revoke_response.json()
        self.assertEqual(revoked_payload["gateway"]["status"], "revoked")
        self.assertEqual(revoked_payload["gateway"]["device_trust_state"], "revoked")

        blocked_response = self.client.post(
            "/api/gateway/sessions",
            json={"gateway_id": gateway_id, "gateway_token": gateway_token},
        )
        self.assertEqual(blocked_response.status_code, 401)

    def test_gateway_connection_status_tracks_online_degraded_reconnecting_offline_and_revoked(self) -> None:
        registration_payload = self._register_gateway()
        gateway_id = registration_payload["gateway"]["gateway_id"]
        gateway_token = registration_payload["gateway_token"]

        pending_session_response = self.client.post(
            "/api/gateway/sessions",
            json={"gateway_id": gateway_id, "gateway_token": gateway_token},
        )
        self.assertEqual(pending_session_response.status_code, 200)
        pending_session_payload = pending_session_response.json()

        pending_list = self.client.get("/api/gateway/registrations", params={"workspace_id": "default"})
        self.assertEqual(pending_list.status_code, 200)
        self.assertEqual(pending_list.json()["items"][0]["connection_status"], "reconnecting")

        ws_path = (
            f"/api/gateway/ws?gateway_id={gateway_id}"
            f"&session_token={pending_session_payload['session_token']}"
        )
        with self.client.websocket_connect(ws_path) as websocket:
            websocket.send_json(
                {
                    "kind": "request",
                    "id": "req-connect-states-1",
                    "type": "gateway.connect",
                    "ts": "2026-04-22T13:00:00Z",
                    "scope": pending_session_payload["scope"],
                    "payload": {
                        "protocol_version": "v1alpha2",
                        "gateway_version": "0.1.0",
                        "device_metadata": {"hostname": "mansur-mac"},
                        "requested_capabilities": ["screen.read"],
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
                    "id": "req-heartbeat-states-1",
                    "type": "gateway.heartbeat",
                    "ts": "2026-04-22T13:00:01Z",
                    "scope": pending_session_payload["scope"],
                    "payload": {
                        "health_state": "online",
                        "journal_cursor": 1,
                        "checkpoint_cursor": 1,
                    },
                }
            )
            self.assertTrue(websocket.receive_json()["ok"])

            online_list = self.client.get("/api/gateway/registrations", params={"workspace_id": "default"})
            self.assertEqual(online_list.status_code, 200)
            self.assertEqual(online_list.json()["items"][0]["connection_status"], "online")

            websocket.send_json(
                {
                    "kind": "request",
                    "id": "req-state-degraded",
                    "type": "gateway.state.update",
                    "ts": "2026-04-22T13:00:02Z",
                    "scope": pending_session_payload["scope"],
                    "payload": {
                        "health_state": "degraded",
                        "journal_cursor": 2,
                        "checkpoint_cursor": 2,
                    },
                }
            )
            self.assertTrue(websocket.receive_json()["ok"])
            websocket.receive_json()

            degraded_list = self.client.get("/api/gateway/registrations", params={"workspace_id": "default"})
            self.assertEqual(degraded_list.status_code, 200)
            degraded_gateway = degraded_list.json()["items"][0]
            self.assertEqual(degraded_gateway["connection_status"], "degraded")
            self.assertEqual(degraded_gateway["reported_health_state"], "degraded")

            websocket.send_json(
                {
                    "kind": "request",
                    "id": "req-state-reconnecting",
                    "type": "gateway.state.update",
                    "ts": "2026-04-22T13:00:03Z",
                    "scope": pending_session_payload["scope"],
                    "payload": {
                        "health_state": "reconnecting",
                        "journal_cursor": 3,
                        "checkpoint_cursor": 3,
                    },
                }
            )
            self.assertTrue(websocket.receive_json()["ok"])
            websocket.receive_json()

            reconnecting_list = self.client.get("/api/gateway/registrations", params={"workspace_id": "default"})
            self.assertEqual(reconnecting_list.status_code, 200)
            reconnecting_gateway = reconnecting_list.json()["items"][0]
            self.assertEqual(reconnecting_gateway["connection_status"], "reconnecting")
            self.assertEqual(reconnecting_gateway["reported_health_state"], "reconnecting")

            websocket.send_json(
                {
                    "kind": "request",
                    "id": "req-state-online-before-disconnect",
                    "type": "gateway.state.update",
                    "ts": "2026-04-22T13:00:03.500Z",
                    "scope": pending_session_payload["scope"],
                    "payload": {
                        "health_state": "online",
                        "journal_cursor": 4,
                        "checkpoint_cursor": 4,
                    },
                }
            )
            self.assertTrue(websocket.receive_json()["ok"])
            websocket.receive_json()

            websocket.send_json(
                {
                    "kind": "request",
                    "id": "req-disconnect-states",
                    "type": "gateway.disconnect",
                    "ts": "2026-04-22T13:00:04Z",
                    "scope": pending_session_payload["scope"],
                    "payload": {"reason": "status_matrix_test"},
                }
            )
            self.assertTrue(websocket.receive_json()["ok"])

        offline_list = self.client.get("/api/gateway/registrations", params={"workspace_id": "default"})
        self.assertEqual(offline_list.status_code, 200)
        self.assertEqual(offline_list.json()["items"][0]["connection_status"], "offline")

        revoke_response = self.client.post(
            f"/api/gateway/registrations/{gateway_id}/revoke",
            json={"reason": "status_matrix_revoke"},
        )
        self.assertEqual(revoke_response.status_code, 200)

        revoked_list = self.client.get("/api/gateway/registrations", params={"workspace_id": "default"})
        self.assertEqual(revoked_list.status_code, 200)
        revoked_gateway = revoked_list.json()["items"][0]
        self.assertEqual(revoked_gateway["status"], "revoked")
        self.assertEqual(revoked_gateway["connection_status"], "revoked")

    def test_gateway_revoke_does_not_revoke_existing_web_auth_session(self) -> None:
        web_session_id = f"web-session-{uuid.uuid4().hex[:10]}"
        auth.create_auth_session(
            "owner-1",
            channel="web",
            session_id=web_session_id,
            session_family_id="web-family-1",
            metadata={"source": "gateway-routes-test"},
            ttl_seconds=3600,
        )
        before = auth.get_auth_session(web_session_id)
        self.assertEqual(str(before.get("status") or "").lower(), "active")

        registration_payload = self._register_gateway()
        gateway_id = registration_payload["gateway"]["gateway_id"]

        revoke_response = self.client.post(
            f"/api/gateway/registrations/{gateway_id}/revoke",
            json={"reason": "owner_revoked_gateway"},
        )
        self.assertEqual(revoke_response.status_code, 200)

        after = auth.get_auth_session(web_session_id)
        self.assertEqual(str(after.get("status") or "").lower(), "active")

    def test_revoke_gateway_shuts_down_live_connection_when_available(self) -> None:
        registration_payload = self._register_gateway()
        gateway_id = registration_payload["gateway"]["gateway_id"]

        class FakeConnection:
            session_id = "gateway-session-live-1"

            class FakeWebSocket:
                close = AsyncMock()

            websocket = FakeWebSocket()

        unregister_mock = patch.object(
            routes_gateway.gateway_protocol_service,
            "_unregister_live_connection",
        )
        with patch.object(
            routes_gateway.gateway_protocol_service,
            "_get_live_connection",
            return_value=FakeConnection(),
        ), unregister_mock as unregister_connection:
            revoke_response = self.client.post(
                f"/api/gateway/registrations/{gateway_id}/revoke",
                json={"reason": "owner_revoked_gateway"},
            )

        self.assertEqual(revoke_response.status_code, 200)
        self.assertEqual(revoke_response.json()["live_connection_shutdown"], True)
        FakeConnection.websocket.close.assert_awaited_once_with(code=4403, reason="registration revoked")
        unregister_connection.assert_called_once_with(
            gateway_id=gateway_id,
            session_id="gateway-session-live-1",
            reason="registration revoked",
        )

    def test_tool_invoke_and_interrupt_flow_through_gateway_with_audit_events(self) -> None:
        registration_payload = self._register_gateway()
        gateway_id = registration_payload["gateway"]["gateway_id"]
        gateway_token = registration_payload["gateway_token"]
        session_response = self.client.post(
            "/api/gateway/sessions",
            json={"gateway_id": gateway_id, "gateway_token": gateway_token},
        )
        self.assertEqual(session_response.status_code, 200)
        session_payload = session_response.json()

        async def _dispatch_tool_invoke(**kwargs):
            request_id = str(kwargs.get("request_id") or "").strip() or "tool-click-1"
            payload = {
                "request_id": request_id,
                "capability_id": str(kwargs.get("capability_id") or "").strip(),
                "run_id": str(kwargs.get("run_id") or "").strip(),
                "result": {"clicked": True, "x": 48, "y": 96},
            }
            gateway_state_repository.record_gateway_event(
                gateway_id=gateway_id,
                session_id=session_payload["session_id"],
                direction="outbound",
                frame_kind="request",
                message_type="tool.invoke",
                payload={"id": request_id, "payload": dict(kwargs)},
            )
            gateway_state_repository.record_gateway_event(
                gateway_id=gateway_id,
                session_id=session_payload["session_id"],
                direction="inbound",
                frame_kind="response",
                message_type="tool.result",
                payload={"id": request_id, "payload": payload},
            )
            return payload

        async def _dispatch_tool_interrupt(**kwargs):
            request_id = str(kwargs.get("request_id") or "").strip() or "tool-interrupt-1"
            payload = {
                "request_id": request_id,
                "run_id": str(kwargs.get("run_id") or "").strip(),
                "target_request_id": str(kwargs.get("target_request_id") or "").strip() or None,
                "interrupted": True,
                "interrupt_count": 1,
            }
            gateway_state_repository.record_gateway_event(
                gateway_id=gateway_id,
                session_id=session_payload["session_id"],
                direction="outbound",
                frame_kind="request",
                message_type="tool.interrupt",
                payload={"id": request_id, "payload": dict(kwargs)},
            )
            gateway_state_repository.record_gateway_event(
                gateway_id=gateway_id,
                session_id=session_payload["session_id"],
                direction="inbound",
                frame_kind="response",
                message_type="tool.interrupt.result",
                payload={"id": request_id, "payload": payload},
            )
            return payload

        with patch(
            "server_modules.gateway_approval_service.capability_requires_owner_approval",
            return_value=False,
        ), patch(
            "server_modules.gateway_execution_service.gateway_protocol_service.gateway_connection_is_live",
            return_value=True,
        ), patch(
            "server_modules.gateway_execution_service.gateway_registry_service.gateway_registration_public_payload",
            return_value={
                "connection_status": "online",
                "heartbeat_fresh": True,
                "reported_health_state": "online",
                "capability_readiness": {"screen.read": "ready"},
            },
        ), patch(
            "server_modules.gateway_execution_service.gateway_protocol_service.dispatch_tool_invoke",
            AsyncMock(side_effect=_dispatch_tool_invoke),
        ), patch(
            "server_modules.gateway_execution_service.gateway_protocol_service.dispatch_tool_interrupt",
            AsyncMock(side_effect=_dispatch_tool_interrupt),
        ):
            execute_response = self.client.post(
                f"/api/gateway/registrations/{gateway_id}/tools/execute",
                json={
                    "capability_id": "screen.read",
                    "arguments": {},
                    "run_id": "run-local-1",
                    "trace_id": "trace-local-1",
                    "request_id": "tool-click-1",
                    "interactive_approvals": False,
                },
            )
            self.assertEqual(execute_response.status_code, 200)
            self.assertIn("result", execute_response.json())

            interrupt_response = self.client.post(
                f"/api/gateway/registrations/{gateway_id}/tools/interrupt",
                json={
                    "run_id": "run-local-1",
                    "trace_id": "trace-local-1",
                    "target_request_id": "tool-click-1",
                    "request_id": "tool-interrupt-1",
                    "reason": "operator_requested_stop",
                },
            )
            self.assertEqual(interrupt_response.status_code, 200)
            self.assertEqual(interrupt_response.json()["interrupted"], True)
            self.assertEqual(interrupt_response.json()["interrupt_count"], 1)

        events = gateway_state_repository.list_gateway_events(
            gateway_id,
            session_id=session_payload["session_id"],
        )
        event_types = [event["message_type"] for event in events]
        self.assertIn("tool.invoke", event_types)
        self.assertIn("tool.result", event_types)
        self.assertIn("tool.interrupt", event_types)
        self.assertIn("tool.interrupt.result", event_types)

    def test_whatsapp_personal_channel_state_reply_reconnect_and_dedupe(self) -> None:
        registration_payload = self._register_gateway(
            capabilities=["screen.read", "system.presence", "channel.whatsapp.personal"]
        )
        gateway_id = registration_payload["gateway"]["gateway_id"]
        gateway_token = registration_payload["gateway_token"]

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

        with patch(
            "server_modules.personal_channel_sage_bridge_service.build_whatsapp_personal_reply",
            return_value={"text": "Sage reply from cloud", "source": "test_bridge"},
        ):
            with self.client.websocket_connect(ws_path) as websocket:
                websocket.send_json(
                    {
                        "kind": "request",
                        "id": "req-connect-wa-1",
                        "type": "gateway.connect",
                        "ts": "2026-04-22T12:30:00Z",
                        "scope": session_payload["scope"],
                        "payload": {
                            "protocol_version": "v1alpha2",
                            "gateway_version": "0.1.0",
                            "device_metadata": {"hostname": "mansur-mac"},
                            "requested_capabilities": ["channel.whatsapp.personal"],
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
                        "id": "req-state-wa-qr",
                        "type": "gateway.state.update",
                        "ts": "2026-04-22T12:30:01Z",
                        "scope": session_payload["scope"],
                        "payload": {
                            "journal_cursor": 1,
                            "checkpoint_cursor": 1,
                            "personal_channels": {
                                "whatsapp_personal": {
                                    "provider": "whatsapp_baileys",
                                    "status": "qr_required",
                                    "qr_code": "qr-test-123",
                                    "retryable": True,
                                }
                            },
                        },
                    }
                )
                self.assertTrue(websocket.receive_json()["ok"])
                websocket.receive_json()

                qr_view = self.client.get(f"/api/personal-channels/whatsapp/gateways/{gateway_id}")
                self.assertEqual(qr_view.status_code, 200)
                qr_payload = qr_view.json()
                self.assertEqual(qr_payload["state"]["status"], "qr_required")
                self.assertEqual(qr_payload["state"]["qr_code"], "qr-test-123")

                websocket.send_json(
                    {
                        "kind": "request",
                        "id": "req-state-wa-pairing",
                        "type": "gateway.state.update",
                        "ts": "2026-04-22T12:30:01.500Z",
                        "scope": session_payload["scope"],
                        "payload": {
                            "journal_cursor": 2,
                            "checkpoint_cursor": 2,
                            "personal_channels": {
                                "whatsapp_personal": {
                                    "provider": "whatsapp_baileys",
                                    "status": "pairing_code_required",
                                    "login_hint": "*******1234",
                                    "pairing_code": "ABCD1234",
                                    "pairing_code_generated_at": "2026-04-22T12:30:01.500Z",
                                    "retryable": True,
                                }
                            },
                        },
                    }
                )
                self.assertTrue(websocket.receive_json()["ok"])
                websocket.receive_json()

                pairing_view = self.client.get(f"/api/personal-channels/whatsapp/gateways/{gateway_id}")
                self.assertEqual(pairing_view.status_code, 200)
                pairing_payload = pairing_view.json()
                self.assertEqual(pairing_payload["state"]["status"], "pairing_code_required")
                self.assertEqual(pairing_payload["state"]["metadata"]["login_hint"], "*******1234")
                self.assertEqual(pairing_payload["state"]["metadata"]["pairing_code"], "ABCD1234")

                websocket.send_json(
                    {
                        "kind": "request",
                        "id": "req-state-wa-connected",
                        "type": "gateway.state.update",
                        "ts": "2026-04-22T12:30:02Z",
                        "scope": session_payload["scope"],
                        "payload": {
                            "journal_cursor": 3,
                            "checkpoint_cursor": 3,
                            "personal_channels": {
                                "whatsapp_personal": {
                                    "provider": "whatsapp_baileys",
                                    "status": "connected",
                                    "linked_jid": "me@s.whatsapp.net",
                                    "linked_name": "Mansur",
                                    "connected_at": "2026-04-22T12:30:02Z",
                                    "retryable": True,
                                }
                            },
                        },
                    }
                )
                self.assertTrue(websocket.receive_json()["ok"])
                websocket.receive_json()

                connected_view = self.client.get(f"/api/personal-channels/whatsapp/gateways/{gateway_id}")
                self.assertEqual(connected_view.status_code, 200)
                connected_payload = connected_view.json()
                self.assertEqual(connected_payload["state"]["status"], "connected")
                self.assertEqual(connected_payload["state"]["linked_jid"], "me@s.whatsapp.net")

                websocket.send_json(
                    {
                        "kind": "event",
                        "type": "channel.inbound",
                        "seq": 7,
                        "ack": 2,
                        "ts": "2026-04-22T12:30:03Z",
                        "scope": session_payload["scope"],
                        "payload": {
                            "channel_key": "whatsapp_personal",
                            "provider": "whatsapp_baileys",
                            "message": {
                                "external_message_id": "wamid.inbound.1",
                                "remote_jid": "user-1@s.whatsapp.net",
                                "sender_jid": "user-1@s.whatsapp.net",
                                "push_name": "User One",
                                "text": "Hello from WhatsApp",
                                "received_at": "2026-04-22T12:30:03Z",
                                "from_me": False,
                            },
                        },
                    }
                )
                pending_payload = None
                for _ in range(20):
                    pending_view = self.client.get(f"/api/personal-channels/whatsapp/gateways/{gateway_id}")
                    self.assertEqual(pending_view.status_code, 200)
                    candidate_payload = pending_view.json()
                    if (
                        candidate_payload["recent_messages"]["outbound"]
                        and candidate_payload["recent_messages"]["outbound"][0]["status"] == "pending"
                    ):
                        pending_payload = candidate_payload
                        break
                    time.sleep(0.1)
                self.assertIsNotNone(
                    pending_payload,
                    [
                        {
                            "type": event["message_type"],
                            "direction": event["direction"],
                            "payload": event.get("payload"),
                        }
                        for event in gateway_state_repository.list_gateway_events(gateway_id)
                        if event["message_type"].startswith("channel.")
                    ],
                )
                self.assertEqual(len(pending_payload["recent_messages"]["inbound"]), 1)
                self.assertEqual(len(pending_payload["recent_messages"]["outbound"]), 1)
                pending_outbound = pending_payload["recent_messages"]["outbound"][0]
                self.assertEqual(pending_outbound["remote_jid"], "user-1@s.whatsapp.net")
                self.assertEqual(pending_outbound["text"], "Sage reply from cloud")
                self.assertEqual(pending_outbound["status"], "pending")

                websocket.send_json(
                    {
                        "kind": "request",
                        "id": "req-disconnect-wa-1",
                        "type": "gateway.disconnect",
                        "ts": "2026-04-22T12:30:05Z",
                        "scope": session_payload["scope"],
                        "payload": {"reason": "reconnect_for_dedupe"},
                    }
                )
                disconnect_ack = websocket.receive_json()
                if not disconnect_ack.get("ok"):
                    disconnect_ack = websocket.receive_json()
                self.assertTrue(disconnect_ack["ok"])

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

        with patch(
            "server_modules.personal_channel_sage_bridge_service.build_whatsapp_personal_reply",
            return_value={"text": "Sage reply from cloud", "source": "test_bridge"},
        ):
            with self.client.websocket_connect(reconnect_path) as websocket:
                websocket.send_json(
                    {
                        "kind": "request",
                        "id": "req-connect-wa-2",
                        "type": "gateway.connect",
                        "ts": "2026-04-22T12:31:00Z",
                        "scope": reconnect_session["scope"],
                        "payload": {
                            "protocol_version": "v1alpha2",
                            "gateway_version": "0.1.0",
                            "device_metadata": {"hostname": "mansur-mac"},
                            "requested_capabilities": ["channel.whatsapp.personal"],
                            "journal_cursor": 2,
                            "checkpoint_cursor": 2,
                        },
                    }
                )
                self.assertTrue(websocket.receive_json()["ok"])
                websocket.receive_json()
                websocket.receive_json()

                websocket.send_json(
                    {
                        "kind": "event",
                        "type": "channel.inbound",
                        "seq": 8,
                        "ack": 2,
                        "ts": "2026-04-22T12:31:01Z",
                        "scope": reconnect_session["scope"],
                        "payload": {
                            "channel_key": "whatsapp_personal",
                            "provider": "whatsapp_baileys",
                            "message": {
                                "external_message_id": "wamid.inbound.1",
                                "remote_jid": "user-1@s.whatsapp.net",
                                "sender_jid": "user-1@s.whatsapp.net",
                                "push_name": "User One",
                                "text": "Hello from WhatsApp",
                                "received_at": "2026-04-22T12:31:01Z",
                                "from_me": False,
                            },
                        },
                    }
                )
                time.sleep(0.15)
                websocket.send_json(
                    {
                        "kind": "request",
                        "id": "req-disconnect-wa-2",
                        "type": "gateway.disconnect",
                        "ts": "2026-04-22T12:31:02Z",
                        "scope": reconnect_session["scope"],
                        "payload": {"reason": "dedupe_verified"},
                    }
                )
                self.assertTrue(websocket.receive_json()["ok"])

        view_after_reconnect = self.client.get(f"/api/personal-channels/whatsapp/gateways/{gateway_id}")
        self.assertEqual(view_after_reconnect.status_code, 200)
        reconnect_payload = view_after_reconnect.json()
        self.assertEqual(len(reconnect_payload["recent_messages"]["inbound"]), 1)
        self.assertEqual(len(reconnect_payload["recent_messages"]["outbound"]), 1)

        all_events = gateway_state_repository.list_gateway_events(gateway_id)
        outbound_request_events = [
            event
            for event in all_events
            if event["message_type"] == "channel.outbound" and event["direction"] == "outbound"
        ]
        outbound_result_events = [
            event
            for event in all_events
            if event["message_type"] == "channel.outbound.result" and event["direction"] == "inbound"
        ]
        self.assertEqual(len(outbound_request_events), 0)
        self.assertEqual(len(outbound_result_events), 0)

    def test_telegram_personal_channel_state_reply_reconnect_and_dedupe(self) -> None:
        registration_payload = self._register_gateway(
            capabilities=["screen.read", "system.presence", "channel.telegram.personal"]
        )
        gateway_id = registration_payload["gateway"]["gateway_id"]
        gateway_token = registration_payload["gateway_token"]

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

        with patch(
            "server_modules.personal_channel_sage_bridge_service.build_telegram_personal_reply",
            return_value={"text": "Sage reply from Telegram cloud", "source": "test_bridge"},
        ):
            with self.client.websocket_connect(ws_path) as websocket:
                websocket.send_json(
                    {
                        "kind": "request",
                        "id": "req-connect-tg-1",
                        "type": "gateway.connect",
                        "ts": "2026-04-22T13:30:00Z",
                        "scope": session_payload["scope"],
                        "payload": {
                            "protocol_version": "v1alpha2",
                            "gateway_version": "0.1.0",
                            "device_metadata": {"hostname": "mansur-mac"},
                            "requested_capabilities": ["channel.telegram.personal"],
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
                        "id": "req-state-tg-code",
                        "type": "gateway.state.update",
                        "ts": "2026-04-22T13:30:01Z",
                        "scope": session_payload["scope"],
                        "payload": {
                            "journal_cursor": 1,
                            "checkpoint_cursor": 1,
                            "personal_channels": {
                                "telegram_personal": {
                                    "provider": "telegram_gramjs",
                                    "status": "code_required",
                                    "login_hint": "******1234",
                                    "retryable": False,
                                }
                            },
                        },
                    }
                )
                self.assertTrue(websocket.receive_json()["ok"])
                websocket.receive_json()

                login_view = self.client.get(f"/api/personal-channels/telegram/gateways/{gateway_id}")
                self.assertEqual(login_view.status_code, 200)
                login_payload = login_view.json()
                self.assertEqual(login_payload["state"]["status"], "code_required")
                self.assertEqual(login_payload["state"]["login_hint"], "******1234")

                websocket.send_json(
                    {
                        "kind": "request",
                        "id": "req-state-tg-connected",
                        "type": "gateway.state.update",
                        "ts": "2026-04-22T13:30:02Z",
                        "scope": session_payload["scope"],
                        "payload": {
                            "journal_cursor": 2,
                            "checkpoint_cursor": 2,
                            "personal_channels": {
                                "telegram_personal": {
                                    "provider": "telegram_gramjs",
                                    "status": "connected",
                                    "linked_user_id": "123456",
                                    "linked_username": "mansur",
                                    "linked_name": "Mansur",
                                    "connected_at": "2026-04-22T13:30:02Z",
                                    "retryable": True,
                                }
                            },
                        },
                    }
                )
                self.assertTrue(websocket.receive_json()["ok"])
                websocket.receive_json()

                connected_view = self.client.get(f"/api/personal-channels/telegram/gateways/{gateway_id}")
                self.assertEqual(connected_view.status_code, 200)
                connected_payload = connected_view.json()
                self.assertEqual(connected_payload["state"]["status"], "connected")
                self.assertEqual(connected_payload["state"]["linked_username"], "mansur")

                websocket.send_json(
                    {
                        "kind": "event",
                        "type": "channel.inbound",
                        "seq": 9,
                        "ack": 2,
                        "ts": "2026-04-22T13:30:03Z",
                        "scope": session_payload["scope"],
                        "payload": {
                            "channel_key": "telegram_personal",
                            "provider": "telegram_gramjs",
                            "message": {
                                "external_message_id": "tg.inbound.1",
                                "remote_jid": "telegram-user-1",
                                "sender_jid": "telegram-user-1",
                                "push_name": "User One",
                                "text": "Hello from Telegram",
                                "received_at": "2026-04-22T13:30:03Z",
                                "from_me": False,
                            },
                        },
                    }
                )
                pending_payload = None
                for _ in range(20):
                    pending_view = self.client.get(f"/api/personal-channels/telegram/gateways/{gateway_id}")
                    self.assertEqual(pending_view.status_code, 200)
                    candidate_payload = pending_view.json()
                    if (
                        candidate_payload["recent_messages"]["outbound"]
                        and candidate_payload["recent_messages"]["outbound"][0]["status"] == "pending"
                    ):
                        pending_payload = candidate_payload
                        break
                    time.sleep(0.1)
                self.assertIsNotNone(
                    pending_payload,
                    [
                        {
                            "type": event["message_type"],
                            "direction": event["direction"],
                            "payload": event.get("payload"),
                        }
                        for event in gateway_state_repository.list_gateway_events(gateway_id)
                        if event["message_type"].startswith("channel.")
                    ],
                )
                self.assertEqual(len(pending_payload["recent_messages"]["inbound"]), 1)
                self.assertEqual(len(pending_payload["recent_messages"]["outbound"]), 1)
                pending_outbound = pending_payload["recent_messages"]["outbound"][0]
                self.assertEqual(pending_outbound["remote_jid"], "telegram-user-1")
                self.assertEqual(pending_outbound["text"], "Sage reply from Telegram cloud")
                self.assertEqual(pending_outbound["status"], "pending")

                websocket.send_json(
                    {
                        "kind": "request",
                        "id": "req-disconnect-tg-1",
                        "type": "gateway.disconnect",
                        "ts": "2026-04-22T13:30:05Z",
                        "scope": session_payload["scope"],
                        "payload": {"reason": "reconnect_for_dedupe"},
                    }
                )
                disconnect_ack = websocket.receive_json()
                if not disconnect_ack.get("ok"):
                    disconnect_ack = websocket.receive_json()
                self.assertTrue(disconnect_ack["ok"])

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

        with patch(
            "server_modules.personal_channel_sage_bridge_service.build_telegram_personal_reply",
            return_value={"text": "Sage reply from Telegram cloud", "source": "test_bridge"},
        ):
            with self.client.websocket_connect(reconnect_path) as websocket:
                websocket.send_json(
                    {
                        "kind": "request",
                        "id": "req-connect-tg-2",
                        "type": "gateway.connect",
                        "ts": "2026-04-22T13:31:00Z",
                        "scope": reconnect_session["scope"],
                        "payload": {
                            "protocol_version": "v1alpha2",
                            "gateway_version": "0.1.0",
                            "device_metadata": {"hostname": "mansur-mac"},
                            "requested_capabilities": ["channel.telegram.personal"],
                            "journal_cursor": 2,
                            "checkpoint_cursor": 2,
                        },
                    }
                )
                self.assertTrue(websocket.receive_json()["ok"])
                websocket.receive_json()
                websocket.receive_json()

                websocket.send_json(
                    {
                        "kind": "event",
                        "type": "channel.inbound",
                        "seq": 10,
                        "ack": 2,
                        "ts": "2026-04-22T13:31:01Z",
                        "scope": reconnect_session["scope"],
                        "payload": {
                            "channel_key": "telegram_personal",
                            "provider": "telegram_gramjs",
                            "message": {
                                "external_message_id": "tg.inbound.1",
                                "remote_jid": "telegram-user-1",
                                "sender_jid": "telegram-user-1",
                                "push_name": "User One",
                                "text": "Hello from Telegram",
                                "received_at": "2026-04-22T13:31:01Z",
                                "from_me": False,
                            },
                        },
                    }
                )
                time.sleep(0.15)
                websocket.send_json(
                    {
                        "kind": "request",
                        "id": "req-disconnect-tg-2",
                        "type": "gateway.disconnect",
                        "ts": "2026-04-22T13:31:02Z",
                        "scope": reconnect_session["scope"],
                        "payload": {"reason": "dedupe_verified"},
                    }
                )
                self.assertTrue(websocket.receive_json()["ok"])

        view_after_reconnect = self.client.get(f"/api/personal-channels/telegram/gateways/{gateway_id}")
        self.assertEqual(view_after_reconnect.status_code, 200)
        reconnect_payload = view_after_reconnect.json()
        self.assertEqual(len(reconnect_payload["recent_messages"]["inbound"]), 1)
        self.assertEqual(len(reconnect_payload["recent_messages"]["outbound"]), 1)

        all_events = gateway_state_repository.list_gateway_events(gateway_id)
        outbound_request_events = [
            event
            for event in all_events
            if event["message_type"] == "channel.outbound" and event["direction"] == "outbound"
            and event["payload"].get("payload", {}).get("channel_key") == "telegram_personal"
        ]
        outbound_result_events = [
            event
            for event in all_events
            if event["message_type"] == "channel.outbound.result" and event["direction"] == "inbound"
            and event["payload"].get("payload", {}).get("channel_key") == "telegram_personal"
        ]
        self.assertEqual(len(outbound_request_events), 0)
        self.assertEqual(len(outbound_result_events), 0)


    def test_gateway_websocket_rejects_oversized_frame(self) -> None:
        """Frames larger than MAX_GATEWAY_FRAME_BYTES must be rejected with gateway_frame_too_large."""
        registration_payload = self._register_gateway()
        gateway_id = registration_payload["gateway"]["gateway_id"]
        gateway_token = registration_payload["gateway_token"]
        session_response = self.client.post(
            "/api/gateway/sessions",
            json={"gateway_id": gateway_id, "gateway_token": gateway_token},
        )
        self.assertEqual(session_response.status_code, 200)
        session_payload = session_response.json()
        ws_path = f"/api/gateway/ws?gateway_id={gateway_id}&session_token={session_payload['session_token']}"

        with self.client.websocket_connect(ws_path) as websocket:
            # First, send a valid connect frame
            websocket.send_json({
                "kind": "request",
                "id": "connect-1",
                "type": "gateway.connect",
                "ts": "2026-04-22T12:00:00Z",
                "scope": session_payload["scope"],
                "payload": {
                    "protocol_version": "v1alpha2",
                    "gateway_version": "0.1.0",
                    "device_metadata": {"hostname": "mansur-mac"},
                    "requested_capabilities": ["screen.read"],
                    "journal_cursor": 0,
                    "checkpoint_cursor": 0,
                },
            })
            connect_ack = websocket.receive_json()
            self.assertTrue(connect_ack["ok"])
            # Consume hello + presence
            websocket.receive_json()
            websocket.receive_json()

            # Now send an oversized frame
            oversized_payload = "x" * (gateway_protocol_service.MAX_GATEWAY_FRAME_BYTES + 1)
            websocket.send_text('{"kind":"request","id":"big-1","type":"gateway.heartbeat","scope":' + json.dumps(session_payload["scope"]) + ',"payload":"' + oversized_payload + '"}')

            error_response = websocket.receive_json()
            self.assertFalse(error_response["ok"])
            self.assertEqual(error_response["error"]["code"], "gateway_frame_too_large")

    def test_gateway_websocket_rejects_excessively_deep_json(self) -> None:
        """Frames with nesting depth > MAX_GATEWAY_JSON_DEPTH must be rejected with gateway_frame_too_deep."""
        registration_payload = self._register_gateway()
        gateway_id = registration_payload["gateway"]["gateway_id"]
        gateway_token = registration_payload["gateway_token"]
        session_response = self.client.post(
            "/api/gateway/sessions",
            json={"gateway_id": gateway_id, "gateway_token": gateway_token},
        )
        self.assertEqual(session_response.status_code, 200)
        session_payload = session_response.json()
        ws_path = f"/api/gateway/ws?gateway_id={gateway_id}&session_token={session_payload['session_token']}"

        with self.client.websocket_connect(ws_path) as websocket:
            # First, send a valid connect frame
            websocket.send_json({
                "kind": "request",
                "id": "connect-1",
                "type": "gateway.connect",
                "ts": "2026-04-22T12:00:00Z",
                "scope": session_payload["scope"],
                "payload": {
                    "protocol_version": "v1alpha2",
                    "gateway_version": "0.1.0",
                    "device_metadata": {"hostname": "mansur-mac"},
                    "requested_capabilities": ["screen.read"],
                    "journal_cursor": 0,
                    "checkpoint_cursor": 0,
                },
            })
            connect_ack = websocket.receive_json()
            self.assertTrue(connect_ack["ok"])
            # Consume hello + presence
            websocket.receive_json()
            websocket.receive_json()

            # Build a deeply nested payload (depth > 32)
            deep_payload = "value"
            for _ in range(40):
                deep_payload = {"nested": deep_payload}

            websocket.send_json({
                "kind": "request",
                "id": "deep-1",
                "type": "gateway.heartbeat",
                "scope": session_payload["scope"],
                "payload": deep_payload,
            })

            error_response = websocket.receive_json()
            self.assertFalse(error_response["ok"])
            self.assertEqual(error_response["error"]["code"], "gateway_frame_too_deep")


if __name__ == "__main__":
    unittest.main()
