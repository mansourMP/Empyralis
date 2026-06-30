import importlib.util
import json
import hashlib
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from fastapi import HTTPException

from server_modules import local_queue


def _load_worker_runtime_module():
    module_path = Path(__file__).resolve().parents[2] / "scripts" / "orion_local_worker_runtime.py"
    spec = importlib.util.spec_from_file_location("test_local_worker_session_runtime", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules["test_local_worker_session_runtime"] = module
    spec.loader.exec_module(module)
    return module


worker_runtime = _load_worker_runtime_module()


class LocalWorkerSessionTests(TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="worker-session-")

    def tearDown(self) -> None:
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _client(self, *, api_key: str = "key", enrollment_token: str = ""):
        return worker_runtime.RuntimeClient(
            base_url="http://runtime",
            api_key=api_key,
            enrollment_token=enrollment_token,
            session_root=Path(self._tmpdir) / "sessions",
        )

    def test_worker_acquires_token_on_startup(self):
        client = self._client()

        with patch.object(
            client,
            "_request",
            return_value={"ok": True, "session_token": "session-1", "instance_id": "instance-1"},
        ):
            result = client.bootstrap_runtime_session(
                "worker-1",
                runtime_type="local",
                display_name="Worker",
                platform="darwin",
                capabilities=["read_write_files"],
                execution_targets=["local"],
                instance_id="instance-1",
            )

        session_path = Path(self._tmpdir) / "sessions" / "worker-1.json"
        persisted = json.loads(session_path.read_text(encoding="utf-8"))
        self.assertEqual(result["session_token"], "session-1")
        self.assertEqual(client.runtime_session_token, "session-1")
        self.assertEqual(client.runtime_instance_id, "instance-1")
        self.assertEqual(persisted["session_token"], "session-1")
        self.assertEqual(persisted["instance_id"], "instance-1")

    def test_worker_can_bootstrap_with_enrollment_token_without_runtime_api_key(self):
        client = self._client(api_key="", enrollment_token="enroll-1")
        captured = {}

        def fake_request(method, path, payload=None):
            captured["method"] = method
            captured["path"] = path
            captured["payload"] = dict(payload or {})
            return {
                "ok": True,
                "session_token": "session-1",
                "instance_id": "instance-1",
                "connection_mode": "platform_relay",
            }

        with patch.object(client, "_request", side_effect=fake_request):
            result = client.bootstrap_runtime_session(
                "worker-1",
                runtime_type="local_companion",
                display_name="Worker",
                platform="darwin",
                capabilities=["filesystem.read_write"],
                execution_targets=["local_companion"],
                instance_id="instance-1",
            )

        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["path"], "/runtime/companions/worker-1/bootstrap")
        self.assertEqual(captured["payload"]["enrollment_token"], "enroll-1")
        self.assertEqual(result["session_token"], "session-1")
        self.assertEqual(client.runtime_session_token, "session-1")
        self.assertEqual(client.runtime_instance_id, "instance-1")

    def test_worker_token_persists_across_restart(self):
        first_client = self._client()
        with patch.object(
            first_client,
            "_request",
            return_value={"ok": True, "session_token": "session-1", "instance_id": "instance-1"},
        ):
            first_client.bootstrap_runtime_session("worker-1", display_name="Worker", instance_id="instance-1")

        second_client = self._client()
        captured = {}

        def fake_request(method, primary_path, fallback_path, payload=None):
            captured["payload"] = payload
            return {"ok": True}

        with (
            patch.object(second_client, "_request_with_fallback", side_effect=fake_request),
            patch.object(second_client, "_request", side_effect=AssertionError("register_runtime should not run")),
        ):
            result = second_client.bootstrap_runtime_session("worker-1", display_name="Worker")

        self.assertTrue(result["resumed"])
        self.assertEqual(captured["payload"]["session_token"], "session-1")
        self.assertEqual(captured["payload"]["instance_id"], "instance-1")
        self.assertEqual(second_client.runtime_session_token, "session-1")

    def test_heartbeat_sent_with_token(self):
        client = self._client()
        client.runtime_session_token = "session-1"
        client.runtime_instance_id = "instance-1"
        client._registration = {
            "runtime_id": "worker-1",
            "runtime_type": "local",
            "display_name": "Worker",
            "platform": "darwin",
            "policy_mode": "local_default",
            "capabilities": [],
            "execution_targets": ["local"],
            "instance_id": "instance-1",
        }
        captured = {}

        def fake_request(method, primary_path, fallback_path, payload=None):
            captured["payload"] = payload
            return {"ok": True}

        with patch.object(client, "_request_with_fallback", side_effect=fake_request):
            client.heartbeat_worker("worker-1", None, "idle")

        self.assertEqual(captured["payload"]["session_token"], "session-1")
        self.assertEqual(captured["payload"]["instance_id"], "instance-1")

    def test_claim_requires_valid_token(self):
        local_queue._init()
        registry = local_queue._server.LOCAL_WORKER_REGISTRY
        previous_registry = dict(registry)
        try:
            registry.clear()
            with (
                patch.object(local_queue, "_persist_local_runtime_state", return_value=None),
                patch.object(local_queue.run_state_repository, "sync_upsert_fleet_worker", return_value=None),
            ):
                registration = local_queue._upsert_runtime_registration(
                    "worker-claim",
                    runtime_type="local",
                    display_name="Worker",
                    platform="darwin",
                    execution_targets=["local"],
                    instance_id="instance-claim",
                )
                session_token = registration["session_token"]
                self.assertEqual(registration["machine_id"], "worker-claim")

                with self.assertRaises(HTTPException) as missing_exc:
                    local_queue._assert_runtime_session("worker-claim", None, instance_id="instance-claim")
                self.assertEqual(missing_exc.exception.status_code, 401)

                with self.assertRaises(HTTPException) as invalid_exc:
                    local_queue._assert_runtime_session("worker-claim", "wrong-token", instance_id="instance-claim")
                self.assertEqual(invalid_exc.exception.status_code, 401)

                verified = local_queue._assert_runtime_session(
                    "worker-claim",
                    session_token,
                    instance_id="instance-claim",
                )
                self.assertEqual(verified["runtime_id"], "worker-claim")
        finally:
            registry.clear()
            registry.update(previous_registry)

    def test_assert_runtime_session_hydrates_worker_from_durable_registry(self):
        local_queue._init()
        registry = local_queue._server.LOCAL_WORKER_REGISTRY
        previous_registry = dict(registry)
        try:
            registry.clear()
            with (
                patch.object(local_queue, "_persist_local_runtime_state", return_value=None),
                patch.object(local_queue.run_state_repository, "sync_get_fleet_worker", return_value={
                    "worker_id": "worker-hydrated",
                    "runtime_id": "worker-hydrated",
                    "machine_id": "worker-hydrated",
                    "instance_id": "instance-1",
                    "session_token_hash": hashlib.sha256(b"good").hexdigest(),
                    "lease_seconds": 30,
                }),
                patch.object(local_queue.run_state_repository, "sync_upsert_fleet_worker", return_value=None),
            ):
                verified = local_queue._assert_runtime_session(
                    "worker-hydrated",
                    "good",
                    instance_id="instance-1",
                )
        finally:
            registry.clear()
            registry.update(previous_registry)

        self.assertEqual(verified["runtime_id"], "worker-hydrated")

    def test_assert_runtime_session_does_not_persist_or_sync_on_every_request(self):
        local_queue._init()
        registry = local_queue._server.LOCAL_WORKER_REGISTRY
        previous_registry = dict(registry)
        try:
            registry.clear()
            with (
                patch.object(local_queue, "_persist_local_runtime_state") as mock_persist,
                patch.object(local_queue, "_sync_durable_fleet_worker") as mock_sync,
                patch.object(local_queue.run_state_repository, "sync_upsert_fleet_worker", return_value=None),
            ):
                registration = local_queue._upsert_runtime_registration(
                    "worker-session",
                    runtime_type="local",
                    display_name="Worker",
                    platform="darwin",
                    execution_targets=["local"],
                    instance_id="instance-session",
                )
                mock_persist.reset_mock()
                mock_sync.reset_mock()
                verified = local_queue._assert_runtime_session(
                    "worker-session",
                    registration["session_token"],
                    instance_id="instance-session",
                )
        finally:
            registry.clear()
            registry.update(previous_registry)

        self.assertEqual(verified["runtime_id"], "worker-session")

    def test_assert_runtime_session_rejects_workspace_scope_drift(self):
        local_queue._init()
        registry = local_queue._server.LOCAL_WORKER_REGISTRY
        previous_registry = dict(registry)
        try:
            registry.clear()
            registry["worker-scope"] = {
                "tenant_id": "tenant-1",
                "workspace_id": "ws-1",
                "machine_enrollment_scope": "workspace",
            }
            with (
                patch.object(local_queue, "_persist_local_runtime_state", return_value=None),
                patch.object(local_queue.run_state_repository, "sync_upsert_fleet_worker", return_value=None),
            ):
                registration = local_queue._upsert_runtime_registration(
                    "worker-scope",
                    runtime_type="local_companion",
                    display_name="Worker",
                    platform="darwin",
                    execution_targets=["local_companion"],
                    instance_id="instance-scope",
                    runtime_role="specialist",
                    install_id="install-a",
                )
                registry["worker-scope"]["workspace_id"] = "ws-2"
                with self.assertRaises(HTTPException) as exc:
                    local_queue._assert_runtime_session(
                        "worker-scope",
                        registration["session_token"],
                        instance_id="instance-scope",
                    )
        finally:
            registry.clear()
            registry.update(previous_registry)

        self.assertEqual(exc.exception.status_code, 409)
        self.assertEqual(exc.exception.detail, "runtime scope changed. Re-register this machine.")

    def test_assert_runtime_session_shadow_mode_allows_scope_drift_for_rollback(self):
        local_queue._init()
        registry = local_queue._server.LOCAL_WORKER_REGISTRY
        previous_registry = dict(registry)
        previous_env = os.environ.get("ORION_ENFORCE_RUNTIME_SESSION_SCOPE")
        try:
            registry.clear()
            registry["worker-shadow"] = {
                "tenant_id": "tenant-1",
                "workspace_id": "ws-1",
                "machine_enrollment_scope": "workspace",
            }
            with (
                patch.object(local_queue, "_persist_local_runtime_state", return_value=None),
                patch.object(local_queue.run_state_repository, "sync_upsert_fleet_worker", return_value=None),
            ):
                registration = local_queue._upsert_runtime_registration(
                    "worker-shadow",
                    runtime_type="local_companion",
                    display_name="Worker",
                    platform="darwin",
                    execution_targets=["local_companion"],
                    instance_id="instance-shadow",
                )
                registry["worker-shadow"]["workspace_id"] = "ws-2"
                with patch.dict(os.environ, {"ORION_ENFORCE_RUNTIME_SESSION_SCOPE": "0"}, clear=False):
                    verified = local_queue._assert_runtime_session(
                        "worker-shadow",
                        registration["session_token"],
                        instance_id="instance-shadow",
                    )
        finally:
            if previous_env is None:
                os.environ.pop("ORION_ENFORCE_RUNTIME_SESSION_SCOPE", None)
            else:
                os.environ["ORION_ENFORCE_RUNTIME_SESSION_SCOPE"] = previous_env
            registry.clear()
            registry.update(previous_registry)

        self.assertEqual(verified["runtime_id"], "worker-shadow")
        self.assertEqual(
            verified["session_scope_mismatch"]["current"]["workspace_id"],
            "ws-2",
        )

    def test_assert_runtime_session_hydration_preserves_durable_scope_fields(self):
        local_queue._init()
        registry = local_queue._server.LOCAL_WORKER_REGISTRY
        previous_registry = dict(registry)
        try:
            registry.clear()
            registry["worker-hydrated"] = {
                "worker_id": "worker-hydrated",
                "runtime_id": "worker-hydrated",
                "machine_id": "worker-hydrated",
                "workspace_id": "stale-workspace",
                "tenant_id": "stale-tenant",
                "runtime_role": "generic",
                "specialist_key": "stale-specialist",
            }
            with patch.object(
                local_queue.run_state_repository,
                "sync_get_fleet_worker",
                return_value={
                    "worker_id": "worker-hydrated",
                    "runtime_id": "worker-hydrated",
                    "machine_id": "worker-hydrated",
                    "workspace_id": "durable-workspace",
                    "tenant_id": "durable-tenant",
                    "runtime_role": "specialist",
                    "specialist_key": "install-a",
                    "machine_enrollment_scope": "workspace",
                    "instance_id": "instance-1",
                    "session_token_hash": hashlib.sha256(b"good").hexdigest(),
                    "lease_seconds": 30,
                },
            ):
                hydrated = local_queue._hydrate_worker_from_durable_registry("worker-hydrated")
        finally:
            registry.clear()
            registry.update(previous_registry)

        self.assertEqual(hydrated["workspace_id"], "durable-workspace")
        self.assertEqual(hydrated["tenant_id"], "durable-tenant")
        self.assertEqual(hydrated["runtime_role"], "specialist")
        self.assertEqual(hydrated["specialist_key"], "install-a")

    def test_bootstrap_enrolled_local_companion_runtime_marks_platform_relay_online(self):
        local_queue._init()
        registry = local_queue._server.LOCAL_WORKER_REGISTRY
        previous_registry = dict(registry)
        try:
            registry.clear()
            with (
                patch.object(local_queue, "_persist_local_runtime_state", return_value=None),
                patch.object(local_queue, "_sync_durable_fleet_worker", return_value=None),
                patch.object(local_queue, "_emit_machine_outbox_event", return_value=None),
            ):
                intent = local_queue.create_machine_enrollment_intent(
                    tenant_id="default",
                    workspace_id="default",
                    machine_id="worker-remote",
                    display_name="Remote Worker",
                    runtime_type="local_companion",
                    execution_targets=["local_companion"],
                )
                result = local_queue.handle_bootstrap_enrolled_local_companion_runtime(
                    "worker-remote",
                    enrollment_token=intent["token"],
                    display_name="Remote Worker",
                    platform="darwin",
                    execution_targets=["local_companion"],
                    instance_id="instance-remote",
                )
                status = local_queue.handle_get_local_runtime_status("worker-remote")
        finally:
            registry.clear()
            registry.update(previous_registry)

        runtime = status.get("runtime") if isinstance(status.get("runtime"), dict) else {}
        self.assertTrue(str(result.get("session_token") or "").strip())
        self.assertEqual(runtime.get("runtime_type"), "local_companion")
        self.assertEqual(runtime.get("connection_mode"), "platform_relay")
        self.assertTrue(bool(runtime.get("online")))
        self.assertEqual(runtime.get("enrollment_state"), "healthy")
