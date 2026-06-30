import importlib
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server_modules import auth, gateway_state_repository, personal_channels_repository, routes_gateway


class GatewayPhase7RoutesTests(unittest.TestCase):
    def setUp(self) -> None:
        global auth
        global gateway_state_repository
        global personal_channels_repository
        global routes_gateway

        auth = importlib.import_module("server_modules.auth")
        gateway_state_repository = importlib.import_module("server_modules.gateway_state_repository")
        personal_channels_repository = importlib.import_module("server_modules.personal_channels_repository")
        routes_gateway = importlib.import_module("server_modules.routes_gateway")

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

    @staticmethod
    def _member_user():
        return {
            "auth_type": "bearer",
            "role": "member",
            "is_admin": False,
            "user_id": "member-1",
            "workspace_roles": {"default": "member"},
            "workspace_access": {
                "default": {
                    "workspace_id": "default",
                    "tenant_id": "default",
                    "role": "member",
                    "tenant_role": "member",
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
                "capabilities": [
                    "browser.session.start",
                    "browser.session.action",
                    "browser.session.takeover",
                    "browser.session.resume",
                    "browser.session.interrupt",
                ],
            },
        )
        self.assertEqual(registration_response.status_code, 200)
        return registration_response.json()

    def _allow_example_domain(self, gateway_id: str) -> None:
        gateway_state_repository.update_gateway_registration_state(
            gateway_id=gateway_id,
            metadata={
                "agent_computer_policy": {
                    "policy_id": "policy-example-domain",
                    "autonomy_mode": "ask_every_time",
                    "domain_allowlist": ["example.com"],
                }
            },
        )

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

    def test_gateway_browser_routes_cover_start_approval_resume_interrupt_and_fallback(self) -> None:
        registration_payload = self._register_gateway()
        gateway_id = registration_payload["gateway"]["gateway_id"]
        start_execute_payload = {
            "gateway_id": gateway_id,
            "device_id": "device-local-1",
            "workspace_id": "default",
            "request_id": "req-start-1",
            "capability_id": "browser.session.start",
            "run_id": "run-browser-1",
            "result": {
                "browser_session": {
                    "browser_session_id": "gbsess-1",
                    "status": "active",
                    "execution_target": "local_gateway",
                    "session_profile": "qa-browser",
                    "current_url": "https://example.com",
                    "manual_takeover": False,
                    "resume_supported": True,
                    "reviewed_approval_required": False,
                    "reviewed_approved": False,
                    "immutable_plan_hash": "plan-1",
                    "execution_binding": {"cwd": "/tmp/project"},
                    "checkpoint": {
                        "next_action_index": 1,
                        "session_profile": "qa-browser",
                        "current_url": "https://example.com",
                    },
                    "snapshot": {
                        "url": "https://example.com",
                        "tabs": [{"id": 1, "url": "https://example.com"}],
                        "accessibility_snapshot": {"role": "document"},
                    },
                    "metadata": {
                        "browser_resume_supported": True,
                        "browser_execution_binding": {"cwd": "/tmp/project"},
                    },
                },
                "status": "started",
            },
        }
        start_execute_mock = AsyncMock(return_value=start_execute_payload)
        with patch(
            "server_modules.routes_gateway.gateway_browser_service.gateway_execution_service.execute_tool_via_gateway",
            start_execute_mock,
        ):
            start_response = self.client.post(
                f"/api/gateway/registrations/{gateway_id}/browser/sessions",
                json={
                    "url": "https://example.com",
                    "session_profile": "qa-browser",
                    "interactive_actions": ["navigate"],
                    "allow_cloud_fallback": False,
                    "run_id": "run-browser-1",
                    "trace_id": "trace-browser-1",
                },
            )
        self.assertEqual(start_response.status_code, 200)
        start_payload = start_response.json()
        self.assertEqual(start_payload["browser_session"]["browser_session_id"], "gbsess-1")
        self.assertTrue(start_payload["browser_session"]["resume_supported"])
        start_kwargs = start_execute_mock.await_args.kwargs
        self.assertEqual(start_kwargs["capability_id"], "browser.session.start")
        self.assertEqual(start_kwargs["arguments"]["browser_metadata"]["browser_session_profile"], "qa-browser")
        self.assertEqual(start_kwargs["arguments"]["browser_metadata"]["execution_target_selected"], "local_gateway")

        approval_response = self.client.post(
            f"/api/gateway/registrations/{gateway_id}/browser/sessions/gbsess-1/actions",
            json={
                "action": "click",
                "action_args": {"selector": "#login"},
                "allow_cloud_fallback": False,
                "run_id": "run-browser-1",
                "trace_id": "trace-browser-1",
            },
        )
        self.assertEqual(approval_response.status_code, 202)
        approval_payload = approval_response.json()
        self.assertEqual(approval_payload["status"], "approval_required")
        approval_id = approval_payload["approval"]["approval_id"]

        action_execute_payload = {
            "gateway_id": gateway_id,
            "device_id": "device-local-1",
            "workspace_id": "default",
            "request_id": "req-action-1",
            "capability_id": "browser.session.action",
            "run_id": "run-browser-1",
            "result": {
                "browser_session": {
                    "browser_session_id": "gbsess-1",
                    "status": "active",
                    "execution_target": "local_gateway",
                    "session_profile": "qa-browser",
                    "current_url": "https://example.com/app",
                    "manual_takeover": False,
                    "resume_supported": True,
                    "reviewed_approval_required": True,
                    "reviewed_approved": True,
                    "immutable_plan_hash": "plan-2",
                    "execution_binding": {"cwd": "/tmp/project"},
                    "checkpoint": {
                        "next_action_index": 2,
                        "session_profile": "qa-browser",
                        "current_url": "https://example.com/app",
                    },
                    "snapshot": {
                        "url": "https://example.com/app",
                        "tabs": [{"id": 1, "url": "https://example.com/app"}],
                        "accessibility_snapshot": {"role": "application"},
                    },
                    "metadata": {
                        "browser_resume_supported": True,
                        "browser_reviewed_approval_required": True,
                        "browser_reviewed_approved": True,
                    },
                },
                "status": "completed",
                "action_result": {"clicked": True},
            },
        }
        action_execute_mock = AsyncMock(return_value=action_execute_payload)
        with patch(
            "server_modules.routes_gateway.gateway_browser_service.gateway_execution_service.execute_tool_via_gateway",
            action_execute_mock,
        ):
            resolve_response = self.client.post(
                f"/api/gateway/registrations/{gateway_id}/approvals/{approval_id}/resolve",
                json={"decision": "approved", "note": "Proceed", "timeout_seconds": 5},
            )
        self.assertEqual(resolve_response.status_code, 200)
        resolved_payload = resolve_response.json()
        self.assertEqual(resolved_payload["status"], "executed")
        self.assertEqual(
            resolved_payload["execution"]["browser_session"]["checkpoint"]["next_action_index"],
            2,
        )

        takeover_execute_payload = {
            "gateway_id": gateway_id,
            "device_id": "device-local-1",
            "workspace_id": "default",
            "request_id": "req-takeover-1",
            "capability_id": "browser.session.takeover",
            "run_id": "run-browser-1",
            "result": {
                "browser_session": {
                    "browser_session_id": "gbsess-1",
                    "status": "waiting_for_input",
                    "execution_target": "local_gateway",
                    "session_profile": "qa-browser",
                    "current_url": "https://example.com/app",
                    "manual_takeover": True,
                    "resume_supported": True,
                    "reviewed_approval_required": True,
                    "reviewed_approved": True,
                    "immutable_plan_hash": "plan-2",
                    "execution_binding": {"cwd": "/tmp/project"},
                    "checkpoint": {
                        "next_action_index": 2,
                        "session_profile": "qa-browser",
                        "current_url": "https://example.com/app",
                        "manual_takeover": True,
                    },
                    "snapshot": {"url": "https://example.com/app"},
                    "metadata": {"manual_takeover": True, "browser_resume_supported": True},
                },
                "status": "waiting_for_input",
                "manual_takeover": True,
            },
        }
        with patch(
            "server_modules.routes_gateway.gateway_browser_service.gateway_execution_service.execute_tool_via_gateway",
            AsyncMock(return_value=takeover_execute_payload),
        ):
            takeover_response = self.client.post(
                f"/api/gateway/registrations/{gateway_id}/browser/sessions/gbsess-1/takeover",
                json={"run_id": "run-browser-1", "trace_id": "trace-browser-1"},
            )
        self.assertEqual(takeover_response.status_code, 200)
        self.assertTrue(takeover_response.json()["browser_session"]["manual_takeover"])

        resume_execute_payload = {
            "gateway_id": gateway_id,
            "device_id": "device-local-1",
            "workspace_id": "default",
            "request_id": "req-resume-1",
            "capability_id": "browser.session.resume",
            "run_id": "run-browser-1",
            "result": {
                "browser_session": {
                    "browser_session_id": "gbsess-1",
                    "status": "active",
                    "execution_target": "local_gateway",
                    "session_profile": "qa-browser",
                    "current_url": "https://example.com/app",
                    "manual_takeover": False,
                    "resume_supported": True,
                    "reviewed_approval_required": True,
                    "reviewed_approved": True,
                    "immutable_plan_hash": "plan-2",
                    "execution_binding": {"cwd": "/tmp/project"},
                    "checkpoint": {
                        "next_action_index": 2,
                        "session_profile": "qa-browser",
                        "current_url": "https://example.com/app",
                        "manual_takeover": False,
                    },
                    "snapshot": {"url": "https://example.com/app"},
                    "metadata": {"browser_resume_supported": True},
                },
                "status": "resumed",
            },
        }
        with patch(
            "server_modules.routes_gateway.gateway_browser_service.gateway_execution_service.execute_tool_via_gateway",
            AsyncMock(return_value=resume_execute_payload),
        ), patch(
            "server_modules.routes_gateway.gateway_protocol_service.gateway_connection_is_live",
            return_value=True,
        ):
            resume_response = self.client.post(
                f"/api/gateway/registrations/{gateway_id}/browser/sessions/gbsess-1/resume",
                json={"run_id": "run-browser-1", "trace_id": "trace-browser-1"},
            )
        self.assertEqual(resume_response.status_code, 200)
        self.assertFalse(resume_response.json()["browser_session"]["manual_takeover"])

        doctor_response = self.client.get(f"/api/gateway/registrations/{gateway_id}/doctor")
        self.assertEqual(doctor_response.status_code, 200)
        self.assertEqual(doctor_response.json()["browser"]["count"], 1)
        self.assertEqual(doctor_response.json()["browser"]["active_count"], 1)

        interrupt_execute_payload = {
            "gateway_id": gateway_id,
            "device_id": "device-local-1",
            "workspace_id": "default",
            "request_id": "req-interrupt-1",
            "capability_id": "browser.session.interrupt",
            "run_id": "run-browser-1",
            "result": {
                "browser_session": {
                    "browser_session_id": "gbsess-1",
                    "status": "interrupted",
                    "execution_target": "local_gateway",
                    "session_profile": "qa-browser",
                    "current_url": "https://example.com/app",
                    "manual_takeover": False,
                    "resume_supported": True,
                    "reviewed_approval_required": True,
                    "reviewed_approved": True,
                    "immutable_plan_hash": "plan-2",
                    "execution_binding": {"cwd": "/tmp/project"},
                    "checkpoint": {
                        "next_action_index": 2,
                        "session_profile": "qa-browser",
                        "current_url": "https://example.com/app",
                    },
                    "snapshot": {"url": "https://example.com/app"},
                    "metadata": {"browser_resume_supported": True},
                    "interrupted_at": "2026-04-22T15:00:05Z",
                },
                "status": "interrupted",
                "interrupted": True,
                "interrupt_count": 1,
            },
        }
        with patch(
            "server_modules.routes_gateway.gateway_browser_service.gateway_execution_service.execute_tool_via_gateway",
            AsyncMock(return_value=interrupt_execute_payload),
        ):
            interrupt_response = self.client.post(
                f"/api/gateway/registrations/{gateway_id}/browser/sessions/gbsess-1/interrupt",
                json={"run_id": "run-browser-1", "trace_id": "trace-browser-1"},
            )
        self.assertEqual(interrupt_response.status_code, 200)
        self.assertEqual(interrupt_response.json()["result"]["interrupt_count"], 1)

        fallback_response = self.client.post(
            f"/api/gateway/registrations/{gateway_id}/browser/sessions",
            json={
                "url": "https://fallback.example.com",
                "session_profile": "qa-browser",
                "run_id": "run-browser-fallback",
                "trace_id": "trace-browser-fallback",
                "allow_cloud_fallback": True,
            },
        )
        self.assertEqual(fallback_response.status_code, 202)
        fallback_payload = fallback_response.json()
        self.assertEqual(fallback_payload["status"], "fallback_ready")
        self.assertEqual(fallback_payload["execution_target"], "cloud_browser")

        events_payload = self.client.get(f"/api/gateway/registrations/{gateway_id}/events")
        self.assertEqual(events_payload.status_code, 200)
        event_types = [item["message_type"] for item in events_payload.json()["items"]]
        self.assertIn("gateway.browser.fallback_ready", event_types)

    def test_gateway_browser_session_does_not_cloud_fallback_by_default(self) -> None:
        registration_payload = self._register_gateway()
        gateway_id = registration_payload["gateway"]["gateway_id"]
        execute_mock = AsyncMock(side_effect=ValueError("Local gateway is not currently connected."))
        fallback_mock = AsyncMock(return_value={"status": "fallback_ready"})
        with (
            patch(
                "server_modules.routes_gateway.gateway_browser_service.execute_browser_capability_via_gateway",
                execute_mock,
            ),
            patch(
                "server_modules.routes_gateway.gateway_browser_service.build_cloud_browser_fallback_response",
                fallback_mock,
            ),
        ):
            response = self.client.post(
                f"/api/gateway/registrations/{gateway_id}/browser/sessions",
                json={
                    "url": "https://fallback.example.com",
                    "session_profile": "qa-browser",
                    "run_id": "run-browser-no-fallback",
                    "trace_id": "trace-browser-no-fallback",
                },
            )

        self.assertEqual(response.status_code, 409)
        self.assertIn("not currently connected", response.json()["detail"])
        execute_mock.assert_awaited_once()
        fallback_mock.assert_not_awaited()

    def test_gateway_browser_action_does_not_cloud_fallback_by_default(self) -> None:
        registration_payload = self._register_gateway()
        gateway = registration_payload["gateway"]
        gateway_id = gateway["gateway_id"]
        gateway_state_repository.upsert_gateway_browser_session(
            gateway_id=gateway_id,
            device_id=gateway["device_id"],
            tenant_id="default",
            workspace_id="default",
            user_id="owner-1",
            run_id="run-browser-action-no-fallback",
            browser_session_id="gbsess-no-fallback",
            status="active",
            execution_target="local_gateway",
            session_profile="qa-browser",
            metadata={"browser_session_mode": "managed"},
        )
        execute_mock = AsyncMock(side_effect=ValueError("Local gateway is not currently connected."))
        fallback_mock = AsyncMock(return_value={"status": "fallback_ready"})
        with (
            patch(
                "server_modules.routes_gateway.gateway_browser_service.execute_browser_capability_via_gateway",
                execute_mock,
            ),
            patch(
                "server_modules.routes_gateway.gateway_browser_service.build_cloud_browser_fallback_response",
                fallback_mock,
            ),
        ):
            response = self.client.post(
                f"/api/gateway/registrations/{gateway_id}/browser/sessions/gbsess-no-fallback/actions",
                json={
                    "action": "navigate",
                    "action_args": {"url": "https://safe.example"},
                    "run_id": "run-browser-action-no-fallback",
                    "trace_id": "trace-browser-action-no-fallback",
                },
            )

        self.assertEqual(response.status_code, 409)
        self.assertIn("not currently connected", response.json()["detail"])
        execute_mock.assert_awaited_once()
        fallback_mock.assert_not_awaited()

    def test_risky_browser_action_with_interactive_approvals_disabled_returns_pending_approval(self) -> None:
        registration_payload = self._register_gateway()
        gateway = registration_payload["gateway"]
        gateway_id = gateway["gateway_id"]
        gateway_state_repository.upsert_gateway_browser_session(
            gateway_id=gateway_id,
            device_id=gateway["device_id"],
            tenant_id="default",
            workspace_id="default",
            user_id="owner-1",
            run_id="run-browser-risky-blocked",
            browser_session_id="gbsess-risky-blocked",
            status="active",
            execution_target="local_gateway",
            session_profile="qa-browser",
            metadata={"browser_session_mode": "managed"},
        )
        execute_mock = AsyncMock(return_value={"status": "completed"})
        with (
            patch(
                "server_modules.routes_gateway.gateway_browser_service.execute_browser_capability_via_gateway",
                execute_mock,
            ),
            patch(
                "server_modules.routes_gateway.security_audit_service.emit_security_audit_event",
            ) as audit_mock,
        ):
            response = self.client.post(
                f"/api/gateway/registrations/{gateway_id}/browser/sessions/gbsess-risky-blocked/actions",
                json={
                    "action": "click",
                    "action_args": {"selector": "#send"},
                    "run_id": "run-browser-risky-blocked",
                    "trace_id": "trace-browser-risky-blocked",
                    "interactive_approvals": False,
                },
            )

        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertEqual(payload["status"], "approval_required")
        self.assertEqual(payload["normalized_approval"]["channel"], "web")
        self.assertEqual(payload["normalized_approval"]["action"], "browser.session.action")
        execute_mock.assert_not_awaited()
        audit_actions = [call.kwargs["action"] for call in audit_mock.call_args_list]
        self.assertNotIn("gateway.approval_blocked", audit_actions)
        events_payload = self.client.get(f"/api/gateway/registrations/{gateway_id}/events")
        event_types = [item["message_type"] for item in events_payload.json()["items"]]
        self.assertIn("gateway.approval.requested", event_types)

    def test_execute_js_browser_action_requires_approval(self) -> None:
        registration_payload = self._register_gateway()
        gateway = registration_payload["gateway"]
        gateway_id = gateway["gateway_id"]
        gateway_state_repository.upsert_gateway_browser_session(
            gateway_id=gateway_id,
            device_id=gateway["device_id"],
            tenant_id="default",
            workspace_id="default",
            user_id="owner-1",
            run_id="run-browser-js",
            browser_session_id="gbsess-js",
            status="active",
            execution_target="local_gateway",
            session_profile="qa-browser",
            metadata={"browser_session_mode": "managed"},
        )
        execute_mock = AsyncMock(return_value={"status": "completed"})
        with patch(
            "server_modules.routes_gateway.gateway_browser_service.execute_browser_capability_via_gateway",
            execute_mock,
        ):
            response = self.client.post(
                f"/api/gateway/registrations/{gateway_id}/browser/sessions/gbsess-js/actions",
                json={
                    "action": "execute_js",
                    "action_args": {"script": "window.localStorage.clear()"},
                    "run_id": "run-browser-js",
                    "trace_id": "trace-browser-js",
                },
            )

        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertEqual(payload["status"], "approval_required")
        self.assertEqual(payload["normalized_approval"]["action"], "browser.session.action")
        self.assertEqual(payload["approval"]["request_payload"]["capability_id"], "browser.session.action")
        self.assertEqual(payload["approval"]["request_payload"]["arguments"]["action"], "execute_js")
        execute_mock.assert_not_awaited()

    def test_review_required_browser_session_with_interactive_approvals_disabled_returns_pending_approval(self) -> None:
        registration_payload = self._register_gateway()
        gateway_id = registration_payload["gateway"]["gateway_id"]
        execute_mock = AsyncMock(return_value={"status": "completed"})
        with patch(
            "server_modules.routes_gateway.gateway_browser_service.execute_browser_capability_via_gateway",
            execute_mock,
        ):
            response = self.client.post(
                f"/api/gateway/registrations/{gateway_id}/browser/sessions",
                json={
                    "url": "https://safe.example",
                    "session_profile": "qa-browser",
                    "reviewed_approval_required": True,
                    "interactive_approvals": False,
                    "run_id": "run-browser-start-blocked",
                    "trace_id": "trace-browser-start-blocked",
                },
            )

        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertEqual(payload["status"], "approval_required")
        self.assertEqual(payload["normalized_approval"]["channel"], "web")
        self.assertEqual(payload["normalized_approval"]["action"], "browser.session.start")
        execute_mock.assert_not_awaited()

    def test_gateway_browser_attach_mode_reports_attach_required_and_attached_states(self) -> None:
        registration_payload = self._register_gateway()
        gateway_id = registration_payload["gateway"]["gateway_id"]
        attach_required_payload = {
            "status": "attach_required",
            "request_id": "req-browser-attach-required",
            "browser_session": {
                "browser_session_id": "gbsess-attach-required",
                "status": "attach_required",
                "execution_target": "local_gateway",
                "session_profile": "gateway_default",
                "current_url": None,
                "manual_takeover": False,
                "resume_supported": False,
                "reviewed_approval_required": False,
                "reviewed_approved": False,
                "immutable_plan_hash": "plan-attach-required",
                "execution_binding": {"cwd": "/tmp/project"},
                "checkpoint": {
                    "next_action_index": 0,
                    "session_profile": "gateway_default",
                    "session_mode": "existing_session_attach",
                    "attach_state": "attach_required",
                },
                "snapshot": {},
                "metadata": {
                    "browser_session_mode": "existing_session_attach",
                    "browser_attach_state": "attach_required",
                },
            },
        }
        execute_mock = AsyncMock(return_value=attach_required_payload)
        with patch("server_modules.routes_gateway.gateway_browser_service.execute_browser_capability_via_gateway", execute_mock):
            attach_required_response = self.client.post(
                f"/api/gateway/registrations/{gateway_id}/browser/sessions",
                json={
                    "session_mode": "existing_session_attach",
                    "allow_cloud_fallback": False,
                    "run_id": "run-browser-attach-required",
                    "trace_id": "trace-browser-attach-required",
                },
            )
        self.assertEqual(attach_required_response.status_code, 202)
        self.assertEqual(attach_required_response.json()["browser_session"]["status"], "attach_required")
        execute_kwargs = execute_mock.await_args.kwargs
        self.assertEqual(execute_kwargs["capability_id"], "browser.session.start")
        self.assertEqual(execute_kwargs["arguments"]["session_mode"], "existing_session_attach")
        self.assertEqual(
            execute_kwargs["arguments"]["browser_metadata"]["browser_session_mode"],
            "existing_session_attach",
        )
        self.assertIsNone(execute_kwargs["arguments"]["attach_endpoint_url"])

        attach_response = self.client.post(
            f"/api/gateway/registrations/{gateway_id}/browser/sessions",
            json={
                "session_mode": "existing_session_attach",
                "attach_endpoint_url": "http://127.0.0.1:9222",
                "interactive_actions": ["navigate"],
                "allow_cloud_fallback": False,
                "run_id": "run-browser-attach-1",
                "trace_id": "trace-browser-attach-1",
            },
        )
        self.assertEqual(attach_response.status_code, 202)
        attach_approval_payload = attach_response.json()
        self.assertEqual(attach_approval_payload["status"], "approval_required")
        self.assertTrue(str(attach_approval_payload["approval"]["approval_id"]).strip())

    def test_browser_start_blocks_domain_outside_agent_computer_policy(self) -> None:
        registration_payload = self._register_gateway()
        gateway_id = registration_payload["gateway"]["gateway_id"]
        gateway_state_repository.update_gateway_registration_state(
            gateway_id=gateway_id,
            metadata={
                "agent_computer_policy": {
                    "policy_id": "policy-domain",
                    "autonomy_mode": "safe_autopilot",
                    "domain_allowlist": ["example.com"],
                }
            },
        )
        execute_mock = AsyncMock(return_value={"status": "completed"})
        with patch(
            "server_modules.routes_gateway.gateway_browser_service.execute_browser_capability_via_gateway",
            execute_mock,
        ):
            response = self.client.post(
                f"/api/gateway/registrations/{gateway_id}/browser/sessions",
                json={
                    "url": "https://evil.test",
                    "run_id": "run-domain-block",
                    "trace_id": "trace-domain-block",
                },
            )

        self.assertEqual(response.status_code, 403)
        payload = response.json()["detail"]
        self.assertEqual(payload["error"], "GATEWAY_RISK_BLOCKED")
        self.assertEqual(payload["reason"], "domain_not_allowed")
        self.assertEqual(payload["risk_decision"]["decision"], "block")
        execute_mock.assert_not_awaited()

    def test_tool_execute_uses_classifier_approval_even_when_legacy_gate_is_safe(self) -> None:
        registration_payload = self._register_gateway()
        gateway_id = registration_payload["gateway"]["gateway_id"]
        execute_mock = AsyncMock(return_value={"status": "completed"})
        with patch(
            "server_modules.routes_gateway.gateway_approval_service.capability_requires_owner_approval",
            return_value=False,
        ), patch(
            "server_modules.routes_gateway.gateway_execution_service.execute_tool_via_gateway",
            execute_mock,
        ):
            response = self.client.post(
                f"/api/gateway/registrations/{gateway_id}/tools/execute",
                json={
                    "capability_id": "shell.command",
                    "arguments": {"command": "ls -la"},
                    "run_id": "run-classifier-approval",
                    "trace_id": "trace-classifier-approval",
                },
            )

        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertEqual(payload["status"], "approval_required")
        self.assertEqual(payload["risk_decision"]["decision"], "approval_required")
        self.assertEqual(payload["risk_decision"]["capability"], "terminal.command")
        execute_mock.assert_not_awaited()

    def test_gateway_approval_resolution_requires_owner_role(self) -> None:
        registration_payload = self._register_gateway()
        gateway_id = registration_payload["gateway"]["gateway_id"]
        self._allow_example_domain(gateway_id)
        approval_response = self.client.post(
            f"/api/gateway/registrations/{gateway_id}/tools/execute",
            json={
                "capability_id": "computer_control.click",
                "arguments": {"url": "https://example.com/dashboard", "x": 10, "y": 20},
                "run_id": "run-member-approval",
                "trace_id": "trace-member-approval",
                "request_id": "req-member-approval",
            },
        )
        self.assertEqual(approval_response.status_code, 202)
        approval_id = approval_response.json()["approval"]["approval_id"]

        self.app.dependency_overrides[routes_gateway.require_api_key] = lambda: self._member_user()
        execute_mock = AsyncMock(return_value={"status": "completed", "result": {"clicked": True}})
        with patch("server_modules.routes_gateway.gateway_execution_service.execute_tool_via_gateway", execute_mock):
            resolve_response = self.client.post(
                f"/api/gateway/registrations/{gateway_id}/approvals/{approval_id}/resolve",
                json={"decision": "approved", "note": "Member should not be able to approve."},
            )

        self.assertEqual(resolve_response.status_code, 403)
        self.assertEqual(resolve_response.json()["detail"], "Owner role required for workspace 'default'.")
        execute_mock.assert_not_awaited()

    def test_approval_memory_reuses_only_matching_narrow_gateway_scope(self) -> None:
        registration_payload = self._register_gateway()
        gateway_id = registration_payload["gateway"]["gateway_id"]
        self._allow_example_domain(gateway_id)
        approval_response = self.client.post(
            f"/api/gateway/registrations/{gateway_id}/tools/execute",
            json={
                "capability_id": "computer_control.click",
                "arguments": {"url": "https://example.com/dashboard", "x": 10, "y": 20},
                "run_id": "run-remember-request",
                "trace_id": "trace-remember-request",
                "request_id": "req-remember-request",
            },
        )
        self.assertEqual(approval_response.status_code, 202)
        approval_id = approval_response.json()["approval"]["approval_id"]

        execute_mock = AsyncMock(
            return_value={
                "status": "completed",
                "result": {"clicked": True},
                "request_id": "req-remember-request",
            }
        )
        with patch("server_modules.routes_gateway.gateway_execution_service.execute_tool_via_gateway", execute_mock):
            resolve_response = self.client.post(
                f"/api/gateway/registrations/{gateway_id}/approvals/{approval_id}/resolve",
                json={
                    "decision": "approved",
                    "note": "Allow dashboard clicks for this test",
                    "remember_for_seconds": 600,
                    "remember_scope": {"target_domain": "example.com"},
                },
            )

        self.assertEqual(resolve_response.status_code, 200)
        self.assertIn("approval_memory_rule", resolve_response.json())
        self.assertEqual(resolve_response.json()["approval_memory_rule"]["target_domain"], "example.com")

        execute_mock = AsyncMock(return_value={"status": "completed", "result": {"clicked": True}})
        with patch("server_modules.routes_gateway.gateway_execution_service.execute_tool_via_gateway", execute_mock):
            reused_response = self.client.post(
                f"/api/gateway/registrations/{gateway_id}/tools/execute",
                json={
                    "capability_id": "computer_control.click",
                    "arguments": {"url": "https://app.example.com/dashboard", "x": 11, "y": 22},
                    "run_id": "run-remember-reuse",
                    "trace_id": "trace-remember-reuse",
                    "request_id": "req-remember-reuse",
                },
            )

        self.assertEqual(reused_response.status_code, 200)
        execute_mock.assert_awaited_once()
        events_payload = self.client.get(f"/api/gateway/registrations/{gateway_id}/events")
        event_types = [item["message_type"] for item in events_payload.json()["items"]]
        self.assertIn("gateway.approval_memory.used", event_types)

        execute_mock = AsyncMock(return_value={"status": "completed"})
        with patch("server_modules.routes_gateway.gateway_execution_service.execute_tool_via_gateway", execute_mock):
            blocked_by_scope_response = self.client.post(
                f"/api/gateway/registrations/{gateway_id}/tools/execute",
                json={
                    "capability_id": "computer_control.click",
                    "arguments": {"url": "https://evil.test/dashboard", "x": 10, "y": 20},
                    "run_id": "run-remember-miss",
                    "trace_id": "trace-remember-miss",
                    "request_id": "req-remember-miss",
                },
            )

        self.assertEqual(blocked_by_scope_response.status_code, 403)
        blocked_detail = blocked_by_scope_response.json()["detail"]
        self.assertEqual(blocked_detail["error"], "GATEWAY_RISK_BLOCKED")
        self.assertEqual(blocked_detail["reason"], "domain_not_allowed")
        execute_mock.assert_not_awaited()
