import importlib.util
from pathlib import Path
import threading
import time
import sys
from unittest import TestCase
from unittest.mock import patch


def _load_module():
    module_path = Path(__file__).resolve().parents[2] / "scripts" / "orion_local_worker_runtime.py"
    spec = importlib.util.spec_from_file_location("test_orion_local_worker_runtime_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


worker_runtime = _load_module()


class LocalWorkerRuntimeClientTests(TestCase):
    def _client(self):
        client = worker_runtime.RuntimeClient(base_url="http://runtime", api_key="key")
        client.runtime_session_token = "stale-session"
        client.runtime_instance_id = "old-instance"
        client._registration = {
            "runtime_id": "worker-1",
            "runtime_type": "local",
            "display_name": "Worker",
            "platform": "darwin",
            "capabilities": [],
            "execution_targets": ["local"],
            "instance_id": "old-instance",
        }
        return client

    def test_heartbeat_worker_retry_uses_new_session_token(self):
        client = self._client()
        captured = []

        def fake_request(method, primary_path, fallback_path, payload=None):
            captured.append(payload)
            if len(captured) == 1:
                raise worker_runtime.ApiRequestError("stale", status_code=401)
            return {"ok": True}

        def fake_register(*args, **kwargs):
            client.runtime_session_token = "fresh-session"
            client.runtime_instance_id = "fresh-instance"
            return {"ok": True}

        with (
            patch.object(client, "_request_with_fallback", side_effect=fake_request),
            patch.object(client, "register_runtime", side_effect=fake_register),
        ):
            client.heartbeat_worker("worker-1", "run-1", "busy")

        self.assertEqual(captured[0]["session_token"], "stale-session")
        self.assertEqual(captured[1]["session_token"], "fresh-session")
        self.assertEqual(captured[1]["instance_id"], "fresh-instance")

    def test_complete_run_retry_uses_new_session_token(self):
        client = self._client()
        captured = []

        def fake_request(method, primary_path, fallback_path, payload=None):
            captured.append(payload)
            if len(captured) == 1:
                raise worker_runtime.ApiRequestError("stale", status_code=401)
            return {"ok": True}

        def fake_register(*args, **kwargs):
            client.runtime_session_token = "fresh-session"
            client.runtime_instance_id = "fresh-instance"
            return {"ok": True}

        with (
            patch.object(client, "_request_with_fallback", side_effect=fake_request),
            patch.object(client, "register_runtime", side_effect=fake_register),
        ):
            client.complete_run("run-1", "worker-1", "done", result_data={"ok": True})

        self.assertEqual(captured[0]["session_token"], "stale-session")
        self.assertEqual(captured[1]["session_token"], "fresh-session")
        self.assertEqual(captured[1]["instance_id"], "fresh-instance")

    def test_heartbeat_run_can_send_structured_event(self):
        client = self._client()
        captured = {}

        def fake_request(method, primary_path, fallback_path, payload=None):
            captured["payload"] = payload
            return {"ok": True}

        with patch.object(client, "_request_with_fallback", side_effect=fake_request):
            client.heartbeat_run(
                "run-1",
                "worker-1",
                "Step 1",
                event={"event": "computer_action", "message": "Step 1", "data": {"label": "Typing query"}},
            )

        self.assertEqual(captured["payload"]["event"]["event"], "computer_action")
        self.assertEqual(captured["payload"]["event"]["data"]["label"], "Typing query")

    def test_get_task_control_state_uses_runtime_session_payload(self):
        client = self._client()
        captured = {}

        def fake_request(method, path, payload=None):
            captured["method"] = method
            captured["path"] = path
            captured["payload"] = payload
            return {"ok": True, "pause_requested": True}

        with patch.object(client, "_request", side_effect=fake_request):
            payload = client.get_task_control_state("run-1", "worker-1")

        self.assertTrue(payload["pause_requested"])
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["path"], "/runtime/tasks/run-1/control-state")
        self.assertEqual(captured["payload"]["runtime_id"], "worker-1")
        self.assertEqual(captured["payload"]["session_token"], "stale-session")

    def test_pause_run_retry_uses_new_session_token(self):
        client = self._client()
        captured = []

        def fake_request(method, path, payload=None):
            captured.append(payload)
            if len(captured) == 1:
                raise worker_runtime.ApiRequestError("stale", status_code=401)
            return {"ok": True}

        def fake_register(*args, **kwargs):
            client.runtime_session_token = "fresh-session"
            client.runtime_instance_id = "fresh-instance"
            return {"ok": True}

        with (
            patch.object(client, "_request", side_effect=fake_request),
            patch.object(client, "register_runtime", side_effect=fake_register),
        ):
            client.pause_run("run-1", "worker-1", "paused", browser_checkpoint={"next_action_index": 2})

        self.assertEqual(captured[0]["session_token"], "stale-session")
        self.assertEqual(captured[1]["session_token"], "fresh-session")
        self.assertEqual(captured[1]["instance_id"], "fresh-instance")

    def test_register_runtime_sends_policy_mode(self):
        client = self._client()
        captured = {}

        def fake_request(method, path, payload=None):
            captured["payload"] = payload
            return {"ok": True, "session_token": "fresh", "instance_id": "instance"}

        with patch.object(client, "_request", side_effect=fake_request):
            client.register_runtime(
                "worker-1",
                display_name="Worker",
                platform="darwin",
                policy_mode="trusted_full_access",
                execution_targets=["local"],
                permission_probe={"screen_recording": {"status": "granted", "source": "probe"}},
            )

        self.assertEqual(captured["payload"]["policy_mode"], "trusted_full_access")
        self.assertEqual(captured["payload"]["permission_probe"]["screen_recording"]["status"], "granted")

    def test_register_runtime_sends_enrollment_token_when_available(self):
        client = worker_runtime.RuntimeClient(
            base_url="http://runtime",
            api_key="key",
            enrollment_token="enroll-1",
        )
        captured = {}

        def fake_request(method, path, payload=None):
            captured["payload"] = payload
            return {"ok": True, "session_token": "fresh", "instance_id": "instance"}

        with patch.object(client, "_request", side_effect=fake_request):
            client.register_runtime("worker-1", display_name="Worker")

        self.assertEqual(captured["payload"]["enrollment_token"], "enroll-1")

    def test_heartbeat_worker_can_send_permission_probe(self):
        client = self._client()
        captured = {}

        def fake_request(method, primary_path, fallback_path, payload=None):
            captured["payload"] = payload
            return {"ok": True}

        with patch.object(client, "_request_with_fallback", side_effect=fake_request):
            client.heartbeat_worker(
                "worker-1",
                None,
                "idle",
                permission_probe={"accessibility": {"status": "denied", "source": "probe"}},
            )

        self.assertEqual(captured["payload"]["permission_probe"]["accessibility"]["status"], "denied")

    def test_iter_control_events_parses_sse_payload(self):
        client = self._client()

        class _FakeStream:
            def __init__(self):
                self._lines = iter(
                    [
                        b"id: 7\n",
                        b"event: hard_kill\n",
                        b'data: {"event":"hard_kill","sequence":7,"machine_id":"worker-1"}\n',
                        b"\n",
                    ]
                )

            def readline(self):
                return next(self._lines, b"")

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        with patch.object(client, "_open_stream", return_value=_FakeStream()):
            event = next(client.iter_control_events("worker-1", since_sequence=0))

        self.assertEqual(event["event"], "hard_kill")
        self.assertEqual(event["id"], "7")
        self.assertEqual(event["data"]["machine_id"], "worker-1")

    def test_runtime_interrupt_controller_kills_registered_process(self):
        controller = worker_runtime.RuntimeInterruptController()
        process = worker_runtime.subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            start_new_session=hasattr(worker_runtime.os, "killpg"),
        )
        try:
            controller.register_process("run-1", process)
            controller.request_interrupt(
                scope="run",
                event_type="run_interrupt",
                reason="Operator stop",
                run_id="run-1",
                machine_id="worker-1",
            )
            deadline = time.time() + 5.0
            while process.poll() is None and time.time() < deadline:
                time.sleep(0.05)
            self.assertIsNotNone(process.poll())
            snapshot = controller.interrupt_snapshot(run_id="run-1")
            self.assertTrue(snapshot["interrupt_requested"])
        finally:
            if process.poll() is None:
                process.kill()

    def test_runtime_interrupt_controller_calls_active_supervisor_interrupt(self):
        controller = worker_runtime.RuntimeInterruptController()
        calls = []

        controller.register_supervisor_execution(
            run_id="run-1",
            request_id="req-1",
            interrupt_fn=lambda: calls.append("interrupted"),
        )
        controller.request_interrupt(
            scope="run",
            event_type="run_interrupt",
            reason="Operator stop",
            run_id="run-1",
            machine_id="worker-1",
        )

        self.assertEqual(calls, ["interrupted"])

    def test_runtime_interrupt_controller_interrupts_supervisor_registered_after_snapshot(self):
        controller = worker_runtime.RuntimeInterruptController()
        calls = []

        controller.request_interrupt(
            scope="run",
            event_type="run_interrupt",
            reason="Operator stop",
            run_id="run-1",
            machine_id="worker-1",
        )
        controller.register_supervisor_execution(
            run_id="run-1",
            request_id="req-1",
            interrupt_fn=lambda: calls.append("interrupted"),
        )

        self.assertEqual(calls, ["interrupted"])
