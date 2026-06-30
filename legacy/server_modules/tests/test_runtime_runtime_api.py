import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from server_modules import runtime_runtime_api


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


class _FakeEventSourceResponse:
    def __init__(self, iterator, ping):
        self.iterator = iterator
        self.ping = ping


class RuntimeRuntimeApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._rust_runtime_session_patch = patch(
            "server_modules.runtime_runtime_api.rust_runtime_kernel_client.run_runtime_kernel_enforced",
            side_effect=self._rust_allow_side_effect,
        )
        self.mock_rust_runtime_session = self._rust_runtime_session_patch.start()

    def tearDown(self) -> None:
        self._rust_runtime_session_patch.stop()

    @staticmethod
    def _current_user(**overrides):
        current_user = {
            "auth_type": "api_key",
            "role": "owner",
            "is_admin": True,
            "user_id": "owner-1",
            "workspace_access": {
                "default": {
                    "workspace_id": "default",
                    "tenant_id": "default",
                    "role": "owner",
                    "tenant_role": "owner",
                }
            },
        }
        current_user.update(overrides)
        return current_user

    @staticmethod
    def _rust_allow_decision(operation: str = "runtime_api_test"):
        next_action_map = {
            "stt_request": "transcribe_audio",
            "tts_request": "synthesize_tts",
            "machine_control": "apply_machine_control",
            "run_hard_kill": "hard_kill_runtime_run",
            "runtime_register": "register_runtime",
            "runtime_bootstrap": "bootstrap_runtime_companion",
            "self_hosted_command_enqueue": "enqueue_self_hosted_command",
            "self_hosted_command_claim": "claim_self_hosted_command",
            "self_hosted_command_result": "record_self_hosted_command_result",
            "hardware_action_execute": "execute_hardware_action",
            "hardware_action_stop": "stop_hardware_action",
            "runtime_start": "start_runtime_session",
            "runtime_heartbeat": "touch_runtime_session",
            "runtime_stop": "stop_runtime_session",
            "runtime_revoke": "revoke_runtime_session",
            "runtime_recover": "recover_runtime_session",
            "runtime_control_stream": "stream_runtime_control",
            "runtime_task_claim": "claim_runtime_task",
            "runtime_task_heartbeat": "record_runtime_task_heartbeat",
            "runtime_task_control_state": "read_runtime_task_control_state",
            "runtime_task_complete": "complete_runtime_task",
            "runtime_task_pause": "pause_runtime_task",
            "runtime_task_fail": "fail_runtime_task",
        }
        return {
            "ok": True,
            "decision": "allow",
            "reason": "runtime_session_api_policy_satisfied",
            "operation": operation,
            "next_action": next_action_map.get(operation, "review_runtime_api_request"),
            "audit_visibility": "standard",
            "approval_required": False,
            "cacheable": False,
        }

    def _rust_allow_side_effect(self, _command, payload):
        return self._rust_allow_decision(str((payload or {}).get("operation") or "runtime_api_test"))

    @patch("server_modules.local_queue.recover_orphaned_local_runs_on_startup")
    @patch("server_modules.outbox_service.get_outbox_delivery_status")
    @patch("server_modules.local_queue.handle_get_local_workers_status")
    def test_runtime_status_payload_maps_worker_summary(self, mock_status, mock_outbox_status, mock_recover):
        mock_status.return_value = {
            "summary": {"known": 1, "online": 1, "idle": 1, "busy": 0, "offline": 0},
            "capability_queue": {"read_write_files": ["empyralis-tauri-local"]},
            "items": [
                {
                    "machine_id": "empyralis-tauri-local",
                    "runtime_id": "empyralis-tauri-local",
                    "worker_id": "empyralis-tauri-local",
                    "runtime_type": "local_companion",
                    "display_name": "Empyralis Local Worker",
                    "platform": "macos",
                    "policy_mode": "trusted_full_access",
                    "capabilities": ["read_write_files"],
                    "execution_targets": ["local_companion"],
                    "status": "idle",
                    "online": True,
                    "current_run_id": None,
                    "last_seen_at": "2026-03-27T00:00:00Z",
                    "registered_at": "2026-03-27T00:00:00Z",
                    "session_issued_at": "2026-03-27T00:00:00Z",
                    "trust_state": "verified",
                    "permission_probe": {"screen_recording": {"status": "granted", "source": "probe"}},
                    "control_state": "active",
                    "safe_mode_status": {"active": False},
                    "kill_switch_status": {"active": False},
                }
            ],
        }
        mock_outbox_status.return_value = {"undelivered_count": 2, "total_retry_count": 1}

        payload = runtime_runtime_api.runtime_status_payload()

        self.assertEqual(payload["scope"], "local_companion_bridge")
        self.assertEqual(payload["summary"]["online"], 1)
        self.assertEqual(payload["outbox"]["undelivered_count"], 2)
        self.assertEqual(payload["items"][0]["machine_id"], "empyralis-tauri-local")
        self.assertEqual(payload["items"][0]["runtime_id"], "empyralis-tauri-local")
        self.assertEqual(payload["items"][0]["status"], "idle")
        self.assertEqual(payload["items"][0]["policy_mode"], "trusted_full_access")
        self.assertIsNone(payload["items"][0]["current_lease_holder"])
        self.assertEqual(payload["items"][0]["permission_probe"]["screen_recording"]["status"], "granted")
        self.assertEqual(payload["items"][0]["control_state"], "active")
        mock_recover.assert_called_once()

    @patch("server_modules.runtime_runtime_api.runtime_status_payload")
    def test_legacy_local_workers_status_payload_preserves_counts(self, mock_status_payload):
        mock_status_payload.return_value = {
            "scope": "local_companion_bridge",
            "summary": {"known": 2, "online": 1, "idle": 1, "busy": 0, "offline": 1},
            "capability_queue": {},
            "items": [{"runtime_id": "empyralis-tauri-local"}],
        }

        payload = runtime_runtime_api.legacy_local_workers_status_payload()

        self.assertTrue(payload["enabled"])
        self.assertEqual(payload["known"], 2)
        self.assertEqual(payload["online_workers"], 1)
        self.assertEqual(payload["offline"], 1)

    @patch("server_modules.runtime_runtime_api.telemetry.get_reliability_snapshot")
    @patch("server_modules.runtime_runtime_api.outbox_service.get_outbox_delivery_status")
    @patch("server_modules.runtime_runtime_api._recent_failed_run_snapshots")
    def test_runtime_reliability_payload_combines_snapshot_and_outbox(self, mock_failed_runs, mock_outbox_status, mock_snapshot):
        mock_failed_runs.return_value = [{"run_id": "run-1", "status": "failed"}]
        mock_outbox_status.return_value = {"undelivered_count": 1}
        mock_snapshot.return_value = {"generated_at": "2026-04-08T00:00:00Z", "control_plane_api": {"request_count": 1}}

        payload = runtime_runtime_api.runtime_reliability_payload()

        self.assertEqual(payload["outbox"]["undelivered_count"], 1)
        self.assertEqual(payload["control_plane_api"]["request_count"], 1)
        mock_snapshot.assert_called_once()

    @patch("server_modules.local_queue.handle_enroll_local_runtime")
    @patch("server_modules.runtime_runtime_api.grant_workspace_owner_machine_trust")
    @patch("server_modules.runtime_runtime_api.security_audit_service.emit_security_audit_event")
    def test_register_runtime_routes_exposes_machine_enroll(self, mock_audit, mock_grant_trust, mock_enroll):
        app = _FakeApp()
        runtime_runtime_api.register_runtime_routes(app)
        mock_enroll.return_value = {"ok": True, "machine_id": "machine-1"}
        handler = app.routes[("POST", "/machines/enroll")]

        result = self._run_async(
            handler(
                runtime_runtime_api.MachineEnrollPayload(display_name="Machine 1", workspace_id="default"),
                current_user=self._current_user(),
            )
        )

        self.assertEqual(result["machine_id"], "machine-1")
        self.assertEqual(mock_enroll.call_args.kwargs["tenant_id"], "default")
        self.assertEqual(mock_enroll.call_args.kwargs["workspace_id"], "default")
        self.assertEqual(mock_enroll.call_args.kwargs["machine_enrollment_scope"], "workspace")
        mock_grant_trust.assert_called_once_with("default", "machine-1")
        mock_audit.assert_called_once()

    @patch("server_modules.runtime_runtime_api.runtime_reliability_payload")
    def test_register_runtime_routes_exposes_runtime_reliability(self, mock_reliability_payload):
        app = _FakeApp()
        runtime_runtime_api.register_runtime_routes(app)
        mock_reliability_payload.return_value = {"generated_at": "2026-04-08T00:00:00Z", "control_plane_api": {"request_count": 2}}
        handler = app.routes[("GET", "/runtime/runtimes/reliability")]

        result = self._run_async(handler())

        self.assertEqual(result["control_plane_api"]["request_count"], 2)
        mock_reliability_payload.assert_called_once()

    @patch("server_modules.local_queue.create_machine_enrollment_intent")
    @patch("server_modules.runtime_runtime_api.grant_workspace_owner_machine_trust")
    @patch("server_modules.runtime_runtime_api.security_audit_service.emit_security_audit_event")
    def test_register_runtime_routes_exposes_machine_enrollment_intent(self, mock_audit, mock_grant_trust, mock_create_intent):
        app = _FakeApp()
        runtime_runtime_api.register_runtime_routes(app)
        mock_create_intent.return_value = {"ok": True, "machine_id": "machine-1", "token": "tok"}
        handler = app.routes[("POST", "/machines/enrollment-intents")]

        result = self._run_async(
            handler(
                runtime_runtime_api.MachineEnrollPayload(display_name="Machine 1", workspace_id="default"),
                current_user=self._current_user(),
            )
        )

        self.assertEqual(result["machine_id"], "machine-1")
        self.assertEqual(mock_create_intent.call_args.kwargs["tenant_id"], "default")
        self.assertEqual(mock_create_intent.call_args.kwargs["workspace_id"], "default")
        self.assertEqual(mock_create_intent.call_args.kwargs["machine_enrollment_scope"], "workspace")
        mock_grant_trust.assert_called_once_with("default", "machine-1")
        mock_audit.assert_called_once()

    @patch("server_modules.runtime_runtime_api.entitlements_service.workspace_entitlement_payload_for_workspace_id")
    def test_machine_enroll_rejects_free_workspace_plan(self, mock_entitlements):
        app = _FakeApp()
        runtime_runtime_api.register_runtime_routes(app)
        mock_entitlements.return_value = {
            "capabilities": {"advanced_features_enabled": False},
        }
        handler = app.routes[("POST", "/machines/enroll")]

        with self.assertRaises(HTTPException) as exc:
            self._run_async(
                handler(
                    runtime_runtime_api.MachineEnrollPayload(display_name="Machine 1", workspace_id="default"),
                    current_user=self._current_user(),
                )
            )

        self.assertEqual(exc.exception.status_code, 403)
        self.assertEqual(exc.exception.detail, "Advanced runtime controls are not included in this workspace plan.")

    @patch("server_modules.local_queue.complete_machine_bootstrap")
    def test_register_runtime_routes_exposes_machine_bootstrap_complete(self, mock_complete):
        app = _FakeApp()
        runtime_runtime_api.register_runtime_routes(app)
        mock_complete.return_value = {"ok": True, "machine_id": "machine-1"}
        handler = app.routes[("POST", "/machines/{machine_id}/bootstrap-complete")]

        result = self._run_async(handler("machine-1", runtime_runtime_api.MachineBootstrapCompletePayload(enrollment_token="tok")))

        self.assertEqual(result["machine_id"], "machine-1")
        mock_complete.assert_called_once_with("machine-1", enrollment_token="tok")

    @patch("server_modules.runtime_runtime_api.agent_registry_repository.enroll_self_hosted_runtime_profile")
    def test_self_hosted_node_enroll_route_accepts_one_time_token(self, mock_enroll):
        app = _FakeApp()
        runtime_runtime_api.register_runtime_routes(app)
        handler = app.routes[("POST", "/runtime/self-hosted-nodes/{runtime_profile_id}/enroll")]
        mock_enroll.return_value = {
            "runtime_profile_id": "rprof-1",
            "runtime_node_id": "node-1",
            "workspace_id": "ws-1",
            "node_session_token": "sess-node-1",
            "runtime_profile": {"id": "rprof-1"},
        }

        result = self._run_async(
            handler(
                "rprof-1",
                runtime_runtime_api.SelfHostedNodeEnrollPayload(
                    enrollment_token="tok-1",
                    public_key="pubkey-xyz",
                    node_kind="mac_mini",
                    capabilities=["shell.execute"],
                ),
            )
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["node_session_token"], "sess-node-1")
        self.assertEqual(mock_enroll.await_args.kwargs["runtime_profile_id"], "rprof-1")
        self.assertEqual(mock_enroll.await_args.kwargs["enrollment_token"], "tok-1")

    @patch("server_modules.runtime_runtime_api.agent_registry_repository.resolve_self_hosted_runtime_heartbeat")
    def test_self_hosted_node_heartbeat_route_requires_node_session_token(self, mock_heartbeat):
        app = _FakeApp()
        runtime_runtime_api.register_runtime_routes(app)
        handler = app.routes[("POST", "/runtime/self-hosted-nodes/{runtime_profile_id}/heartbeat")]
        mock_heartbeat.return_value = {
            "ok": True,
            "runtime_profile_id": "rprof-1",
            "runtime_node_id": "node-1",
            "workspace_id": "ws-1",
            "heartbeat_at": "2026-05-11T00:12:00Z",
        }

        result = self._run_async(
            handler(
                "rprof-1",
                runtime_runtime_api.SelfHostedNodeHeartbeatPayload(
                    node_session_token="sess-node-1",
                    status="idle",
                    note="alive",
                    capabilities=["shell.execute"],
                    health_state="healthy",
                ),
            )
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["runtime_node_id"], "node-1")
        self.assertEqual(mock_heartbeat.await_args.kwargs["runtime_profile_id"], "rprof-1")
        self.assertEqual(mock_heartbeat.await_args.kwargs["node_session_token"], "sess-node-1")

    @patch("server_modules.runtime_runtime_api._ensure_advanced_features_access")
    @patch("server_modules.runtime_runtime_api.agent_registry_repository.enqueue_self_hosted_runtime_command")
    def test_self_hosted_node_command_enqueue_route_enforces_workspace_scope(self, mock_enqueue, mock_features_gate):
        app = _FakeApp()
        runtime_runtime_api.register_runtime_routes(app)
        handler = app.routes[("POST", "/runtime/self-hosted-nodes/{runtime_profile_id}/commands")]
        mock_enqueue.return_value = {"ok": True, "command": {"id": "shcmd_1"}}

        result = self._run_async(
            handler(
                "rprof-1",
                runtime_runtime_api.SelfHostedNodeCommandEnqueuePayload(
                    workspace_id="default",
                    runtime_node_id="node-1",
                    agent_id="agent-1",
                    command_type="runtime_action",
                    command_payload={"action": "open_url"},
                ),
                current_user=self._current_user(),
            )
        )

        self.assertTrue(result["ok"])
        self.assertEqual(mock_enqueue.await_args.kwargs["runtime_profile_id"], "rprof-1")
        self.assertEqual(mock_enqueue.await_args.kwargs["workspace_id"], "default")
        self.assertEqual(mock_enqueue.await_args.kwargs["runtime_node_id"], "node-1")
        self.assertEqual(mock_enqueue.await_args.kwargs["agent_id"], "agent-1")
        self.assertEqual(mock_enqueue.await_args.kwargs["command_payload"]["action"], "open_url")
        mock_features_gate.assert_called_once_with("default")

    @patch("server_modules.runtime_runtime_api._ensure_advanced_features_access")
    @patch("server_modules.runtime_runtime_api.hardware_action_broker_service.execute_hardware_action")
    def test_runtime_hardware_action_route_executes_for_workspace_operator(self, mock_execute, mock_features_gate):
        app = _FakeApp()
        runtime_runtime_api.register_runtime_routes(app)
        handler = app.routes[("POST", "/runtime/hardware/actions/execute")]
        mock_execute.return_value = {"status": "completed", "artifacts": ["artifact-1"]}

        with patch.object(
            runtime_runtime_api.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=self._rust_allow_side_effect,
        ) as mock_rust:
            result = self._run_async(
                handler(
                    runtime_runtime_api.RuntimeHardwareActionExecutePayload(
                        workspace_id="default",
                        action_id="screenshot.capture",
                        runtime_target="empyralis_cloud_computer",
                        runtime_access_mode="full_access",
                        thread_id="thread-1",
                        request_id="req-1",
                    ),
                    current_user=self._current_user(),
                )
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(mock_rust.call_args.args[0], "runtime-session-api-decision")
        self.assertEqual(mock_rust.call_args.args[1]["operation"], "hardware_action_execute")
        mock_features_gate.assert_called_once_with("default")
        self.assertEqual(mock_execute.await_args.kwargs["tenant_id"], "default")
        self.assertEqual(mock_execute.await_args.kwargs["workspace_id"], "default")
        self.assertEqual(mock_execute.await_args.kwargs["user_id"], "owner-1")
        self.assertEqual(mock_execute.await_args.kwargs["action_id"], "screenshot.capture")
        self.assertEqual(mock_execute.await_args.kwargs["runtime_target"], "empyralis_cloud_computer")
        self.assertEqual(mock_execute.await_args.kwargs["runtime_access_mode"], "full_access")
        self.assertEqual(mock_execute.await_args.kwargs["thread_id"], "thread-1")
        self.assertEqual(mock_execute.await_args.kwargs["request_id"], "req-1")

    @patch("server_modules.runtime_runtime_api._ensure_advanced_features_access")
    @patch("server_modules.runtime_runtime_api.hardware_action_broker_service.stop_hardware_action")
    def test_runtime_hardware_action_stop_route_stops_for_workspace_operator(self, mock_stop, mock_features_gate):
        app = _FakeApp()
        runtime_runtime_api.register_runtime_routes(app)
        handler = app.routes[("POST", "/runtime/hardware/actions/stop")]
        mock_stop.return_value = {"status": "terminated", "runtime_session": {"session_id": "hrs-1"}}

        with patch.object(
            runtime_runtime_api.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=self._rust_allow_side_effect,
        ) as mock_rust:
            result = self._run_async(
                handler(
                    runtime_runtime_api.RuntimeHardwareActionStopPayload(
                        workspace_id="default",
                        run_id="run-1",
                        runtime_target="empyralis_cloud_computer",
                        thread_id="thread-1",
                        request_id="req-1",
                        target_request_id="req-tool-1",
                        session_id="hrs-1",
                        reason="certification_stop",
                    ),
                    current_user=self._current_user(),
                )
            )

        self.assertEqual(result["status"], "terminated")
        self.assertEqual(mock_rust.call_args.args[0], "runtime-session-api-decision")
        self.assertEqual(mock_rust.call_args.args[1]["operation"], "hardware_action_stop")
        mock_features_gate.assert_called_once_with("default")
        self.assertEqual(mock_stop.await_args.kwargs["tenant_id"], "default")
        self.assertEqual(mock_stop.await_args.kwargs["workspace_id"], "default")
        self.assertEqual(mock_stop.await_args.kwargs["runtime_target"], "empyralis_cloud_computer")
        self.assertEqual(mock_stop.await_args.kwargs["thread_id"], "thread-1")
        self.assertEqual(mock_stop.await_args.kwargs["request_id"], "req-1")
        self.assertEqual(mock_stop.await_args.kwargs["target_request_id"], "req-tool-1")
        self.assertEqual(mock_stop.await_args.kwargs["session_id"], "hrs-1")

    @patch("server_modules.runtime_runtime_api._ensure_advanced_features_access")
    @patch("server_modules.runtime_runtime_api.hardware_action_broker_service.execute_hardware_action")
    def test_runtime_hardware_action_route_blocks_non_operator(self, mock_execute, mock_features_gate):
        app = _FakeApp()
        runtime_runtime_api.register_runtime_routes(app)
        handler = app.routes[("POST", "/runtime/hardware/actions/execute")]

        with self.assertRaises(HTTPException) as exc:
            self._run_async(
                handler(
                    runtime_runtime_api.RuntimeHardwareActionExecutePayload(
                        workspace_id="default",
                        action_id="screenshot.capture",
                        runtime_target="empyralis_cloud_computer",
                    ),
                    current_user=self._current_user(
                        is_admin=False,
                        role="member",
                        workspace_access={
                            "default": {
                                "workspace_id": "default",
                                "tenant_id": "default",
                                "role": "member",
                                "tenant_role": "member",
                            }
                        },
                    ),
                )
            )

        self.assertEqual(exc.exception.status_code, 403)
        mock_features_gate.assert_not_called()
        mock_execute.assert_not_called()

    @patch("server_modules.runtime_runtime_api._ensure_advanced_features_access")
    @patch("server_modules.runtime_runtime_api.agent_registry_repository.enqueue_self_hosted_runtime_command")
    def test_self_hosted_node_command_enqueue_route_blocks_non_admin_role(self, mock_enqueue, mock_features_gate):
        app = _FakeApp()
        runtime_runtime_api.register_runtime_routes(app)
        handler = app.routes[("POST", "/runtime/self-hosted-nodes/{runtime_profile_id}/commands")]

        with self.assertRaises(HTTPException) as exc:
            self._run_async(
                handler(
                    "rprof-1",
                    runtime_runtime_api.SelfHostedNodeCommandEnqueuePayload(
                        workspace_id="default",
                        runtime_node_id="node-1",
                        agent_id="agent-1",
                        command_type="runtime_action",
                        command_payload={"action": "open_url"},
                    ),
                    current_user=self._current_user(
                        auth_type="bearer",
                        is_admin=False,
                        role="member",
                        workspace_access={
                            "default": {
                                "workspace_id": "default",
                                "tenant_id": "default",
                                "role": "member",
                                "tenant_role": "member",
                            }
                        },
                    ),
                )
            )
        self.assertEqual(exc.exception.status_code, 403)
        mock_enqueue.assert_not_called()
        mock_features_gate.assert_not_called()

    @patch("server_modules.runtime_runtime_api.agent_registry_repository.claim_self_hosted_runtime_commands")
    def test_self_hosted_node_command_claim_route_uses_node_session_token(self, mock_claim):
        app = _FakeApp()
        runtime_runtime_api.register_runtime_routes(app)
        handler = app.routes[("POST", "/runtime/self-hosted-nodes/{runtime_profile_id}/commands/claim")]
        mock_claim.return_value = {"ok": True, "claimed_count": 1, "commands": [{"id": "shcmd_1"}]}

        result = self._run_async(
            handler(
                "rprof-1",
                runtime_runtime_api.SelfHostedNodeCommandClaimPayload(
                    node_session_token="sess-node-1",
                    max_commands=2,
                    lease_seconds=90,
                ),
            )
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["claimed_count"], 1)
        self.assertEqual(mock_claim.await_args.kwargs["runtime_profile_id"], "rprof-1")
        self.assertEqual(mock_claim.await_args.kwargs["node_session_token"], "sess-node-1")
        self.assertEqual(mock_claim.await_args.kwargs["max_commands"], 2)
        self.assertEqual(mock_claim.await_args.kwargs["lease_seconds"], 90)

    @patch("server_modules.runtime_runtime_api.agent_registry_repository.complete_self_hosted_runtime_command")
    def test_self_hosted_node_command_result_route_reports_completion(self, mock_complete):
        app = _FakeApp()
        runtime_runtime_api.register_runtime_routes(app)
        handler = app.routes[("POST", "/runtime/self-hosted-nodes/{runtime_profile_id}/commands/{command_id}/result")]
        mock_complete.return_value = {"ok": True, "command_id": "shcmd_1", "status": "completed"}

        result = self._run_async(
            handler(
                "rprof-1",
                "shcmd_1",
                runtime_runtime_api.SelfHostedNodeCommandResultPayload(
                    node_session_token="sess-node-1",
                    status="completed",
                    result_payload={"ok": True},
                    artifacts=[{"artifact_id": "a1"}],
                    audit_references=[{"event_id": "ev1"}],
                ),
            )
        )

        self.assertTrue(result["ok"])
        self.assertEqual(mock_complete.await_args.kwargs["runtime_profile_id"], "rprof-1")
        self.assertEqual(mock_complete.await_args.kwargs["command_id"], "shcmd_1")
        self.assertEqual(mock_complete.await_args.kwargs["node_session_token"], "sess-node-1")
        self.assertEqual(mock_complete.await_args.kwargs["status"], "completed")

    @patch("server_modules.runtime_runtime_api.run_in_threadpool", side_effect=lambda fn, *args, **kwargs: fn(*args, **kwargs))
    @patch("server_modules.local_queue.handle_bootstrap_enrolled_local_companion_runtime")
    def test_companion_bootstrap_route_accepts_enrollment_token_without_api_key(self, mock_bootstrap, mock_run_in_threadpool):
        app = FastAPI()
        runtime_runtime_api.register_runtime_routes(app)
        mock_bootstrap.return_value = {
            "ok": True,
            "runtime_id": "worker-1",
            "machine_id": "worker-1",
            "session_token": "session-1",
            "instance_id": "instance-1",
            "capability_digest": "abcd1234",
            "session_issued_at": "2026-04-11T00:00:00Z",
            "connection_mode": "platform_relay",
            "runtime": {
                "runtime_id": "worker-1",
                "machine_id": "worker-1",
                "runtime_type": "local_companion",
                "connection_mode": "platform_relay",
                "display_name": "Remote Worker",
                "online": True,
            },
        }

        with TestClient(app) as client:
            response = client.post(
                "/runtime/companions/worker-1/bootstrap",
                json={
                    "enrollment_token": "tok",
                    "display_name": "Remote Worker",
                    "runtime_type": "local_companion",
                    "execution_targets": ["local_companion"],
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["session_token"], "session-1")
        self.assertEqual(payload["connection_mode"], "platform_relay")
        mock_run_in_threadpool.assert_called_once()
        self.assertEqual(mock_bootstrap.call_args.kwargs["enrollment_token"], "tok")

    @patch("server_modules.local_queue.handle_bootstrap_enrolled_local_companion_runtime")
    @patch("server_modules.local_queue.handle_register_local_cluster_runtime")
    def test_runtime_register_route_requires_enrollment_token_before_session_issue(self, mock_register, mock_bootstrap):
        app = _FakeApp()
        runtime_runtime_api.register_runtime_routes(app)
        handler = app.routes[("POST", "/runtime/runtimes/{runtime_id}/register")]

        with self.assertRaises(HTTPException) as raised:
            self._run_async(
                handler(
                    "worker-1",
                    runtime_runtime_api.RuntimeRegisterPayload(display_name="Worker"),
                )
            )

        self.assertEqual(raised.exception.status_code, 403)
        self.assertIn("machine enrollment token", str(raised.exception.detail))
        mock_register.assert_not_called()
        mock_bootstrap.assert_not_called()

    @patch("server_modules.runtime_runtime_api.run_in_threadpool", side_effect=lambda fn, *args, **kwargs: fn(*args, **kwargs))
    @patch("server_modules.local_queue.handle_bootstrap_enrolled_local_companion_runtime")
    def test_runtime_register_route_uses_enrollment_bootstrap(self, mock_bootstrap, mock_run_in_threadpool):
        app = _FakeApp()
        runtime_runtime_api.register_runtime_routes(app)
        handler = app.routes[("POST", "/runtime/runtimes/{runtime_id}/register")]
        mock_bootstrap.return_value = {
            "ok": True,
            "runtime_id": "worker-1",
            "machine_id": "worker-1",
            "session_token": "session-1",
            "instance_id": "instance-1",
            "capability_digest": "abcd1234",
            "session_issued_at": "2026-04-11T00:00:00Z",
            "connection_mode": "platform_relay",
            "runtime": {"runtime_id": "worker-1", "runtime_role": "specialist"},
        }

        result = self._run_async(
            handler(
                "worker-1",
                runtime_runtime_api.RuntimeRegisterPayload(
                    enrollment_token="tok",
                    runtime_role="specialist",
                    install_id="install-1",
                    specialist_key="support-bot",
                ),
            )
        )

        self.assertEqual(result["session_token"], "session-1")
        self.assertTrue(result["enrollment_bootstrap"])
        self.assertEqual(mock_bootstrap.call_args.kwargs["enrollment_token"], "tok")
        self.assertEqual(mock_bootstrap.call_args.kwargs["runtime_role"], "specialist")
        self.assertEqual(mock_bootstrap.call_args.kwargs["install_id"], "install-1")
        self.assertEqual(mock_bootstrap.call_args.kwargs["specialist_key"], "support-bot")
        mock_run_in_threadpool.assert_called_once()

    @patch("server_modules.local_queue.handle_heartbeat_local_run")
    @patch("server_modules.local_queue._assert_runtime_session")
    def test_runtime_task_heartbeat_forwards_structured_event(self, mock_assert_session, mock_heartbeat):
        app = _FakeApp()
        runtime_runtime_api.register_runtime_routes(app)
        mock_heartbeat.return_value = {"last_heartbeat_at": "2026-04-07T00:00:00Z"}
        handler = app.routes[("POST", "/runtime/tasks/{task_id}/heartbeat")]

        result = self._run_async(
            handler(
                runtime_runtime_api.uuid.UUID("00000000-0000-0000-0000-000000000001"),
                runtime_runtime_api.RuntimeTaskHeartbeatPayload(
                    runtime_id="worker-1",
                    session_token="sess",
                    instance_id="inst",
                    note="Step 1",
                    event={"event": "computer_action", "message": "Step 1", "data": {"label": "Clicking search field"}},
                ),
            )
        )

        self.assertEqual(result["task_id"], "00000000-0000-0000-0000-000000000001")
        payload = mock_heartbeat.call_args.args[1]
        self.assertEqual(payload.event["event"], "computer_action")
        self.assertEqual(payload.event["data"]["label"], "Clicking search field")
        mock_assert_session.assert_called_once()

    @patch("server_modules.runtime_runtime_api.run_in_threadpool", side_effect=lambda fn, *args, **kwargs: fn(*args, **kwargs))
    @patch("server_modules.local_queue.handle_heartbeat_local_runtime")
    @patch("server_modules.local_queue._assert_runtime_session")
    def test_runtime_heartbeat_route_offloads_runtime_session_and_heartbeat_to_threadpool(
        self,
        mock_assert_session,
        mock_heartbeat,
        mock_run_in_threadpool,
    ):
        app = _FakeApp()
        runtime_runtime_api.register_runtime_routes(app)
        mock_heartbeat.return_value = {
            "ok": True,
            "current_run_id": "run-1",
            "last_seen_at": "2026-04-11T00:00:00Z",
            "runtime": {"runtime_id": "worker-1", "health_state": "healthy"},
        }
        handler = app.routes[("POST", "/runtime/runtimes/{runtime_id}/heartbeat")]

        result = self._run_async(
            handler(
                "worker-1",
                runtime_runtime_api.RuntimeHeartbeatPayload(
                    session_token="sess",
                    instance_id="inst",
                    note="still alive",
                    health_state="healthy",
                ),
            )
        )

        self.assertEqual(result["runtime"]["health_state"], "healthy")
        mock_run_in_threadpool.assert_called_once()
        mock_assert_session.assert_called_once_with("worker-1", "sess", instance_id="inst")
        payload = mock_heartbeat.call_args.args[1]
        self.assertEqual(payload.note, "still alive")
        self.assertEqual(payload.health_state, "healthy")

    @patch("server_modules.runtime_runtime_api.run_in_threadpool", side_effect=lambda fn, *args, **kwargs: fn(*args, **kwargs))
    @patch("server_modules.local_queue.handle_claim_local_run")
    @patch("server_modules.local_queue._assert_runtime_session")
    def test_runtime_task_claim_route_offloads_session_and_claim_to_threadpool(
        self,
        mock_assert_session,
        mock_claim,
        mock_run_in_threadpool,
    ):
        app = _FakeApp()
        runtime_runtime_api.register_runtime_routes(app)
        mock_claim.return_value = {
            "ok": True,
            "worker_id": "worker-1",
            "run": {
                "run_id": "run-1",
                "status": "running_local",
                "created_at": "2026-04-11T00:00:00Z",
                "lease_seconds": 45,
                "machine_id": "worker-1",
                "machine_lease_id": "lease-1",
                "context": {"user_goal": "Research topic", "metadata": {"policy_mode": "local_default"}},
            },
        }
        handler = app.routes[("POST", "/runtime/tasks/claim")]

        with patch.object(
            runtime_runtime_api.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=self._rust_allow_side_effect,
        ) as mock_rust:
            result = self._run_async(
                handler(
                    runtime_runtime_api.RuntimeTaskClaimRequest(
                        runtime_id="worker-1",
                        session_token="sess",
                        instance_id="inst",
                        execution_target="local",
                        required_capabilities=["shell.execute"],
                    )
                )
            )

        self.assertEqual(result["task"]["task_id"], "run-1")
        self.assertEqual(mock_rust.call_args.args[0], "runtime-session-api-decision")
        self.assertEqual(mock_rust.call_args.args[1]["operation"], "runtime_task_claim")
        mock_run_in_threadpool.assert_called_once()
        mock_assert_session.assert_called_once_with("worker-1", "sess", instance_id="inst")
        claim_request = mock_claim.call_args.args[0]
        self.assertEqual(claim_request.worker_id, "worker-1")
        self.assertEqual(claim_request.required_capabilities, ["shell.execute"])

    @patch("server_modules.runtime_runtime_api.run_in_threadpool", side_effect=lambda fn, *args, **kwargs: fn(*args, **kwargs))
    @patch("server_modules.local_queue.handle_claim_local_run")
    @patch("server_modules.local_queue._assert_runtime_session")
    def test_runtime_task_claim_route_accepts_runtime_session_without_api_key(
        self,
        mock_assert_session,
        mock_claim,
        mock_run_in_threadpool,
    ):
        app = FastAPI()
        runtime_runtime_api.register_runtime_routes(app)
        mock_claim.return_value = {
            "ok": True,
            "worker_id": "worker-1",
            "run": {
                "run_id": "run-1",
                "status": "running_local",
                "created_at": "2026-04-11T00:00:00Z",
                "lease_seconds": 45,
                "machine_id": "worker-1",
                "machine_lease_id": "lease-1",
                "context": {"user_goal": "Use remote companion"},
            },
        }

        with patch.object(
            runtime_runtime_api.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=self._rust_allow_side_effect,
        ) as mock_rust, TestClient(app) as client:
            response = client.post(
                "/runtime/tasks/claim",
                json={
                    "runtime_id": "worker-1",
                    "session_token": "sess",
                    "instance_id": "inst",
                    "execution_target": "local_companion",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["task"]["task_id"], "run-1")
        self.assertEqual(mock_rust.call_args.args[0], "runtime-session-api-decision")
        self.assertEqual(mock_rust.call_args.args[1]["operation"], "runtime_task_claim")
        mock_run_in_threadpool.assert_called_once()
        mock_assert_session.assert_called_once_with("worker-1", "sess", instance_id="inst")

    @patch("server_modules.local_queue.handle_get_local_run_control_state")
    @patch("server_modules.local_queue._assert_runtime_session")
    def test_runtime_task_control_state_route_forwards_runtime_session(self, mock_assert_session, mock_control_state):
        app = _FakeApp()
        runtime_runtime_api.register_runtime_routes(app)
        mock_control_state.return_value = {"status": "waiting_for_input", "pause_requested": True, "manual_takeover": True}
        handler = app.routes[("POST", "/runtime/tasks/{task_id}/control-state")]

        result = self._run_async(
            handler(
                runtime_runtime_api.uuid.UUID("00000000-0000-0000-0000-000000000001"),
                runtime_runtime_api.RuntimeTaskControlStatePayload(
                    runtime_id="worker-1",
                    session_token="sess",
                    instance_id="inst",
                ),
            )
        )

        self.assertTrue(result["pause_requested"])
        self.assertTrue(result["manual_takeover"])
        mock_assert_session.assert_called_once()
        payload = mock_control_state.call_args.args[1]
        self.assertEqual(payload.worker_id, "worker-1")

    @patch("server_modules.local_queue.handle_delete_local_runtime")
    @patch("server_modules.runtime_runtime_api.local_queue.handle_get_local_workers_status")
    @patch("server_modules.runtime_runtime_api.revoke_workspace_owner_machine_trust")
    @patch("server_modules.runtime_runtime_api.security_audit_service.emit_security_audit_event")
    def test_register_runtime_routes_exposes_machine_delete(self, mock_audit, mock_revoke_trust, mock_status, mock_delete):
        app = _FakeApp()
        runtime_runtime_api.register_runtime_routes(app)
        mock_delete.return_value = {"ok": True, "machine_id": "machine-1", "revoked": True, "deleted": False}
        mock_status.return_value = {
            "items": [{"machine_id": "machine-1", "tenant_id": "default", "workspace_id": "default"}],
            "summary": {},
        }
        handler = app.routes[("DELETE", "/machines/{machine_id}")]

        with patch.object(
            runtime_runtime_api.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=self._rust_allow_side_effect,
        ) as mock_rust:
            result = self._run_async(handler("machine-1", current_user=self._current_user()))

        self.assertTrue(result["revoked"])
        self.assertEqual(mock_rust.call_args.args[0], "runtime-session-api-decision")
        self.assertEqual(mock_rust.call_args.args[1]["operation"], "machine_control")
        mock_delete.assert_called_once_with("machine-1")
        mock_revoke_trust.assert_called_once_with("default", "machine-1")
        mock_audit.assert_called_once()

    @patch("server_modules.local_queue.handle_set_local_runtime_control")
    @patch("server_modules.runtime_runtime_api.local_queue.handle_get_local_workers_status")
    @patch("server_modules.runtime_runtime_api.security_audit_service.emit_security_audit_event")
    def test_register_runtime_routes_exposes_machine_suspend(self, mock_audit, mock_status, mock_control):
        app = _FakeApp()
        runtime_runtime_api.register_runtime_routes(app)
        mock_control.return_value = {"ok": True, "machine_id": "machine-1", "action": "suspend"}
        mock_status.return_value = {
            "items": [{"machine_id": "machine-1", "tenant_id": "default", "workspace_id": "default"}],
            "summary": {},
        }
        handler = app.routes[("POST", "/machines/{machine_id}/suspend")]

        with patch.object(
            runtime_runtime_api.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=self._rust_allow_side_effect,
        ) as mock_rust:
            result = self._run_async(
                handler(
                    "machine-1",
                    runtime_runtime_api.MachineControlPayload(reason="Maintenance"),
                    current_user=self._current_user(),
                )
            )

        self.assertEqual(result["action"], "suspend")
        self.assertEqual(mock_rust.call_args.args[1]["operation"], "machine_control")
        mock_control.assert_called_once_with("machine-1", action="suspend", reason="Maintenance")
        mock_audit.assert_called_once()

    @patch("server_modules.local_queue.handle_set_local_runtime_control")
    @patch("server_modules.runtime_runtime_api.local_queue.handle_get_local_workers_status")
    @patch("server_modules.runtime_runtime_api.security_audit_service.emit_security_audit_event")
    def test_register_runtime_routes_exposes_machine_resume(self, mock_audit, mock_status, mock_control):
        app = _FakeApp()
        runtime_runtime_api.register_runtime_routes(app)
        mock_control.return_value = {"ok": True, "machine_id": "machine-1", "action": "resume"}
        mock_status.return_value = {
            "items": [{"machine_id": "machine-1", "tenant_id": "default", "workspace_id": "default"}],
            "summary": {},
        }
        handler = app.routes[("POST", "/machines/{machine_id}/resume")]

        with patch.object(
            runtime_runtime_api.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=self._rust_allow_side_effect,
        ) as mock_rust:
            result = self._run_async(
                handler(
                    "machine-1",
                    runtime_runtime_api.MachineControlPayload(reason="Recovered"),
                    current_user=self._current_user(),
                )
            )

        self.assertEqual(result["action"], "resume")
        self.assertEqual(mock_rust.call_args.args[1]["operation"], "machine_control")
        mock_control.assert_called_once_with("machine-1", action="resume", reason="Recovered")
        mock_audit.assert_called_once()

    @patch("server_modules.local_queue.handle_request_local_runtime_hard_kill")
    @patch("server_modules.runtime_runtime_api.local_queue.handle_get_local_workers_status")
    @patch("server_modules.runtime_runtime_api.security_audit_service.emit_security_audit_event")
    def test_register_runtime_routes_exposes_machine_hard_kill(self, mock_audit, mock_status, mock_hard_kill):
        app = _FakeApp()
        runtime_runtime_api.register_runtime_routes(app)
        mock_hard_kill.return_value = {"ok": True, "machine_id": "machine-1", "event": {"event": "hard_kill"}}
        mock_status.return_value = {
            "items": [{"machine_id": "machine-1", "tenant_id": "default", "workspace_id": "default"}],
            "summary": {},
        }
        handler = app.routes[("POST", "/machines/{machine_id}/hard-kill")]

        with patch.object(
            runtime_runtime_api.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=self._rust_allow_side_effect,
        ) as mock_rust:
            result = self._run_async(
                handler(
                    "machine-1",
                    runtime_runtime_api.MachineControlPayload(reason="Operator stop"),
                    current_user=self._current_user(),
                )
            )

        self.assertEqual(result["machine_id"], "machine-1")
        self.assertEqual(mock_rust.call_args.args[1]["operation"], "machine_control")
        mock_hard_kill.assert_called_once_with("machine-1", reason="Operator stop", requested_by="owner-1")
        mock_audit.assert_called_once()

    @patch("server_modules.local_queue.handle_request_local_run_hard_kill")
    @patch("server_modules.runtime_runtime_api.security_audit_service.emit_security_audit_event")
    def test_register_runtime_routes_exposes_run_hard_kill(self, mock_audit, mock_hard_kill):
        app = _FakeApp()
        runtime_runtime_api.register_runtime_routes(app)
        mock_hard_kill.return_value = {"ok": True, "run_id": "00000000-0000-0000-0000-000000000001"}
        original_server = getattr(runtime_runtime_api.local_queue, "_server", None)
        try:
            runtime_runtime_api.local_queue._server = type(
                "_FakeServer",
                (),
                {
                    "runs": {
                        "00000000-0000-0000-0000-000000000001": {
                            "context": {"workspace_id": "default", "tenant_id": "default", "metadata": {}}
                        }
                    }
                },
            )()
            with patch.object(runtime_runtime_api.local_queue, "_init", return_value=None):
                handler = app.routes[("POST", "/runs/{run_id}/hard-kill")]
                result = self._run_async(
                    handler(
                        runtime_runtime_api.uuid.UUID("00000000-0000-0000-0000-000000000001"),
                        runtime_runtime_api.MachineControlPayload(reason="Operator stop"),
                        current_user=self._current_user(),
                    )
                )
        finally:
            runtime_runtime_api.local_queue._server = original_server

        self.assertEqual(result["run_id"], "00000000-0000-0000-0000-000000000001")
        self.assertEqual(self.mock_rust_runtime_session.call_args.args[0], "runtime-session-api-decision")
        self.assertEqual(self.mock_rust_runtime_session.call_args.args[1]["operation"], "run_hard_kill")
        mock_hard_kill.assert_called_once_with(
            "00000000-0000-0000-0000-000000000001",
            reason="Operator stop",
            requested_by="owner-1",
        )
        mock_audit.assert_called_once()

    @patch("server_modules.runtime_runtime_api.EventSourceResponse", side_effect=lambda iterator, ping: _FakeEventSourceResponse(iterator, ping))
    @patch("server_modules.local_queue.aiter_runtime_control_stream")
    @patch("server_modules.local_queue._assert_runtime_session")
    def test_runtime_control_stream_route_forwards_runtime_session(self, mock_assert_session, mock_iter_stream, _mock_event_source):
        app = _FakeApp()
        runtime_runtime_api.register_runtime_routes(app)
        async def _stream_marker():
            if False:
                yield {"event": "hard_kill"}
        stream_marker = _stream_marker()
        mock_iter_stream.return_value = stream_marker
        handler = app.routes[("GET", "/runtime/runtimes/{runtime_id}/control/stream")]

        result = self._run_async(
            handler(
                "worker-1",
                session_token="sess",
                instance_id="inst",
                since_sequence=4,
                include_backlog=True,
                heartbeat_seconds=4.0,
                timeout_seconds=12.0,
            )
        )

        self.assertIsInstance(result, _FakeEventSourceResponse)
        self.assertIs(result.iterator, stream_marker)
        self.assertEqual(result.ping, 4)
        mock_assert_session.assert_called_once_with("worker-1", "sess", instance_id="inst")
        mock_iter_stream.assert_called_once()
        kwargs = mock_iter_stream.call_args.kwargs
        self.assertEqual(kwargs["since_sequence"], 4)
        self.assertTrue(kwargs["include_backlog"])

    @patch("server_modules.runtime_runtime_api.run_in_threadpool", side_effect=lambda fn, *args, **kwargs: fn(*args, **kwargs))
    @patch("server_modules.local_queue.handle_bootstrap_enrolled_local_companion_runtime")
    def test_register_runtime_routes_exposes_local_cluster_register(self, mock_bootstrap, mock_run_in_threadpool):
        app = _FakeApp()
        runtime_runtime_api.register_runtime_routes(app)
        mock_bootstrap.return_value = {"ok": True, "runtime_id": "worker-1", "runtime": {"runtime_id": "worker-1", "runtime_role": "specialist"}}
        handler = app.routes[("POST", "/runtime/local-cluster/{runtime_id}/register")]

        result = self._run_async(
            handler(
                "worker-1",
                runtime_runtime_api.RuntimeRegisterPayload(
                    enrollment_token="tok",
                    runtime_role="specialist",
                    install_id="install-1",
                    specialist_key="support-bot",
                ),
            )
        )

        self.assertEqual(result["runtime"]["runtime_id"], "worker-1")
        self.assertEqual(mock_bootstrap.call_args.kwargs["enrollment_token"], "tok")
        self.assertEqual(mock_bootstrap.call_args.kwargs["runtime_role"], "specialist")
        self.assertEqual(mock_bootstrap.call_args.kwargs["install_id"], "install-1")
        self.assertEqual(mock_bootstrap.call_args.kwargs["specialist_key"], "support-bot")
        mock_run_in_threadpool.assert_called_once()

    @patch("server_modules.local_queue.handle_bootstrap_enrolled_local_companion_runtime")
    @patch("server_modules.local_queue.handle_register_local_cluster_runtime")
    def test_local_cluster_register_requires_enrollment_token_before_session_issue(self, mock_register, mock_bootstrap):
        app = _FakeApp()
        runtime_runtime_api.register_runtime_routes(app)
        handler = app.routes[("POST", "/runtime/local-cluster/{runtime_id}/register")]

        with self.assertRaises(HTTPException) as raised:
            self._run_async(
                handler(
                    "worker-1",
                    runtime_runtime_api.RuntimeRegisterPayload(display_name="Worker"),
                )
            )

        self.assertEqual(raised.exception.status_code, 403)
        self.assertIn("machine enrollment token", str(raised.exception.detail))
        mock_register.assert_not_called()
        mock_bootstrap.assert_not_called()

    @patch("server_modules.local_queue.handle_start_local_runtime")
    @patch("server_modules.local_queue._assert_runtime_session")
    def test_register_runtime_routes_exposes_local_cluster_start(self, mock_assert_session, mock_start):
        app = _FakeApp()
        runtime_runtime_api.register_runtime_routes(app)
        mock_start.return_value = {"ok": True, "runtime": {"runtime_id": "worker-1", "runtime_role": "captain"}}
        handler = app.routes[("POST", "/runtime/local-cluster/{runtime_id}/start")]

        result = self._run_async(
            handler(
                "worker-1",
                runtime_runtime_api.LocalClusterLifecyclePayload(
                    session_token="sess",
                    instance_id="inst",
                    runtime_role="captain",
                    note="Start captain",
                ),
            )
        )

        self.assertEqual(result["runtime"]["runtime_role"], "captain")
        mock_assert_session.assert_called_once_with("worker-1", "sess", instance_id="inst")
        payload = mock_start.call_args.args[1]
        self.assertEqual(payload.runtime_role, "captain")
        self.assertEqual(payload.note, "Start captain")

    @patch("server_modules.local_queue.handle_get_local_runtime_health")
    def test_register_runtime_routes_exposes_local_cluster_health(self, mock_health):
        app = _FakeApp()
        runtime_runtime_api.register_runtime_routes(app)
        mock_health.return_value = {"ok": True, "runtime": {"runtime_id": "worker-1", "health_state": "healthy"}, "health_state": "healthy"}
        handler = app.routes[("GET", "/runtime/local-cluster/{runtime_id}/health")]

        result = self._run_async(handler("worker-1"))

        self.assertEqual(result["health_state"], "healthy")
        mock_health.assert_called_once_with("worker-1")

    @patch("server_modules.local_queue.handle_heartbeat_local_runtime")
    @patch("server_modules.local_queue._assert_runtime_session")
    def test_register_runtime_routes_exposes_local_cluster_heartbeat(self, mock_assert_session, mock_heartbeat):
        app = _FakeApp()
        runtime_runtime_api.register_runtime_routes(app)
        mock_heartbeat.return_value = {"ok": True, "runtime": {"runtime_id": "worker-1", "health_state": "healthy"}}
        handler = app.routes[("POST", "/runtime/local-cluster/{runtime_id}/heartbeat")]

        result = self._run_async(
            handler(
                "worker-1",
                runtime_runtime_api.LocalClusterLifecyclePayload(
                    session_token="sess",
                    instance_id="inst",
                    summary_text="Still healthy",
                    health_state="healthy",
                ),
            )
        )

        self.assertEqual(result["runtime"]["health_state"], "healthy")
        mock_assert_session.assert_called_once_with("worker-1", "sess", instance_id="inst")
        payload = mock_heartbeat.call_args.args[1]
        self.assertEqual(payload.summary_text, "Still healthy")
        self.assertEqual(payload.health_state, "healthy")

    @patch("server_modules.local_queue.handle_stop_local_runtime")
    @patch("server_modules.local_queue._assert_runtime_session")
    def test_register_runtime_routes_exposes_local_cluster_stop(self, mock_assert_session, mock_stop):
        app = _FakeApp()
        runtime_runtime_api.register_runtime_routes(app)
        mock_stop.return_value = {"ok": True, "runtime": {"runtime_id": "worker-1", "lifecycle_state": "stopped"}}
        handler = app.routes[("POST", "/runtime/local-cluster/{runtime_id}/stop")]

        result = self._run_async(
            handler(
                "worker-1",
                runtime_runtime_api.LocalClusterLifecyclePayload(
                    session_token="sess",
                    instance_id="inst",
                    reason="Operator stop",
                    note="Stop worker",
                ),
            )
        )

        self.assertEqual(result["runtime"]["lifecycle_state"], "stopped")
        mock_assert_session.assert_called_once_with("worker-1", "sess", instance_id="inst")
        mock_stop.assert_called_once_with("worker-1", reason="Operator stop", note="Stop worker", summary_text=None, artifacts=None, health_state=None)

    @patch("server_modules.local_queue.handle_revoke_local_runtime")
    def test_register_runtime_routes_exposes_local_cluster_revoke(self, mock_revoke):
        app = _FakeApp()
        runtime_runtime_api.register_runtime_routes(app)
        mock_revoke.return_value = {"ok": True, "machine": {"runtime_id": "worker-1", "control_state": "revoked"}}
        handler = app.routes[("POST", "/runtime/local-cluster/{runtime_id}/revoke")]

        result = self._run_async(
            handler(
                "worker-1",
                runtime_runtime_api.LocalClusterLifecyclePayload(reason="Retire worker"),
            )
        )

        self.assertEqual(result["machine"]["control_state"], "revoked")
        mock_revoke.assert_called_once_with("worker-1", reason="Retire worker")

    @patch("server_modules.local_queue.handle_recover_local_runtime")
    @patch("server_modules.local_queue._assert_runtime_session")
    def test_register_runtime_routes_exposes_local_cluster_recover(self, mock_assert_session, mock_recover):
        app = _FakeApp()
        runtime_runtime_api.register_runtime_routes(app)
        mock_recover.return_value = {"ok": True, "runtime": {"runtime_id": "worker-1", "lifecycle_state": "running"}, "recovered_run_ids": ["run-1"]}
        handler = app.routes[("POST", "/runtime/local-cluster/{runtime_id}/recover")]

        result = self._run_async(
            handler(
                "worker-1",
                runtime_runtime_api.LocalClusterLifecyclePayload(
                    session_token="sess",
                    instance_id="inst",
                    runtime_role="specialist",
                    install_id="install-1",
                ),
            )
        )

        self.assertEqual(result["recovered_run_ids"], ["run-1"])
        mock_assert_session.assert_called_once_with("worker-1", "sess", instance_id="inst")
        payload = mock_recover.call_args.args[1]
        self.assertEqual(payload.runtime_role, "specialist")
        self.assertEqual(payload.install_id, "install-1")

    def _run_async(self, coroutine):
        import asyncio

        return asyncio.run(coroutine)


if __name__ == "__main__":
    unittest.main()
