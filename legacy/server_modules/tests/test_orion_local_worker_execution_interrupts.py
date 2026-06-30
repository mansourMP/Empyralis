import importlib.util
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch


def _load_module(module_name: str, filename: str):
    module_path = Path(__file__).resolve().parents[2] / "scripts" / filename
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


worker_execution = _load_module("test_orion_local_worker_execution_module", "orion_local_worker_execution.py")
worker_runtime = _load_module("test_orion_local_worker_runtime_module_for_execution", "orion_local_worker_runtime.py")


class LocalWorkerExecutionInterruptTests(TestCase):
    def test_shell_operation_hard_interrupt_kills_active_process(self):
        controller = worker_runtime.RuntimeInterruptController()
        result = {}

        def _run() -> None:
            try:
                worker_execution._run_shell_operation(
                    "run-1",
                    0,
                    {"cwd": "."},
                    root,
                    artifacts_root,
                    interrupt_state_provider=lambda: controller.interrupt_snapshot(run_id="run-1"),
                    interrupt_controller=controller,
                )
            except Exception as exc:
                result["error"] = exc

        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tempdir:
            root = Path(tempdir)
            artifacts_root = root / "artifacts"
            artifacts_root.mkdir(parents=True, exist_ok=True)
            with (
                patch.object(
                    worker_execution,
                    "_match_allowed_operation_command",
                    return_value=(
                        [sys.executable, "-c", "import time; time.sleep(30)"],
                        sys.executable,
                        f"{sys.executable} -c sleep",
                    ),
                ),
                patch.object(worker_execution, "assert_file_mount_access", return_value={"mode": "read"}),
            ):
                thread = threading.Thread(target=_run, daemon=True)
                thread.start()
                deadline = time.time() + 5.0
                while not controller.has_active_process() and thread.is_alive() and time.time() < deadline:
                    time.sleep(0.05)
                controller.request_interrupt(
                    scope="run",
                    event_type="run_interrupt",
                    reason="Operator stop",
                    run_id="run-1",
                    machine_id="worker-1",
                )
                thread.join(timeout=5.0)

        self.assertFalse(thread.is_alive(), "Shell operation did not stop after hard interrupt.")
        self.assertIsInstance(result.get("error"), worker_execution.HardInterruptRequested)
        self.assertIn("Operator stop", str(result["error"]))

    def test_computer_control_interrupt_becomes_hard_interrupt(self):
        result = {}

        def _run() -> None:
            try:
                worker_execution._run_computer_control_operation(
                    0,
                    {"action": "click", "x": 10, "y": 20},
                    interrupt_state_provider=lambda: {
                        "interrupt_requested": True,
                        "event_type": "run_interrupt",
                        "interrupt_reason": "Operator stop",
                        "run_id": "run-1",
                    },
                )
            except Exception as exc:
                result["error"] = exc

        with patch.object(
            worker_execution,
            "mouse_click",
            side_effect=worker_execution.supervisor_client.SupervisorInterruptedError("execution interrupted by operator"),
        ):
            _run()

        self.assertIsInstance(result.get("error"), worker_execution.HardInterruptRequested)
        self.assertIn("Operator stop", str(result["error"]))


if __name__ == "__main__":
    import unittest

    unittest.main()
