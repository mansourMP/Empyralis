from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from server_modules import docker_execution_sandbox


class DockerExecutionSandboxTests(unittest.TestCase):
    def test_docker_sandbox_command_delegates_to_rust_kernel(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            expected_command = [
                "docker",
                "run",
                "--rm",
                "--read-only",
                "--network=none",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges:true",
                "empyralis-sandbox:latest",
                "python3",
                "-m",
                "server_modules.hosted_secure_worker",
            ]
            with mock.patch.object(
                docker_execution_sandbox.rust_runtime_kernel_client,
                "run_runtime_kernel_enforced",
                return_value={
                    "ok": True,
                    "decision": "allow",
                    "next_action": "build_hardened_container_command",
                    "command": expected_command,
                },
            ) as rust_mock:
                command = docker_execution_sandbox.docker_sandbox_command(
                    sandbox_root=str(Path(temp_dir) / "sandbox"),
                )

        self.assertEqual(command, expected_command)
        rust_mock.assert_called_once()
        self.assertEqual(rust_mock.call_args.args[0], "build-sandbox-command")
        self.assertEqual(rust_mock.call_args.args[1]["worker_module"], "server_modules.hosted_secure_worker")

    def test_docker_sandbox_command_blocks_network_without_kernel_approval(self) -> None:
        with mock.patch.object(
            docker_execution_sandbox.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=docker_execution_sandbox.rust_runtime_kernel_client.RustKernelDecisionError(
                {
                    "ok": False,
                    "decision": "block",
                    "reason": "network_enabled_requires_sandbox_execution_policy",
                },
                command="build-sandbox-command",
            ),
        ):
            with self.assertRaises(RuntimeError):
                docker_execution_sandbox.docker_sandbox_command(
                    sandbox_root="/tmp/empyralis-sandbox-proof",
                    network_enabled=True,
                )

    def test_docker_sandbox_command_wrong_rust_action_blocks(self) -> None:
        with mock.patch.object(
            docker_execution_sandbox.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "next_action": "launch_hosted_worker",
                "command": ["docker", "run"],
            },
        ):
            with self.assertRaises(RuntimeError) as raised:
                docker_execution_sandbox.docker_sandbox_command(
                    sandbox_root="/tmp/empyralis-sandbox-proof",
                )

        self.assertEqual(str(raised.exception), "unexpected_next_action")

    def test_run_docker_worker_gates_launch_with_rust_before_subprocess(self) -> None:
        expected_command = [
            "docker",
            "run",
            "--rm",
            "--read-only",
            "--network=none",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
            "empyralis-sandbox:latest",
            "python3",
            "-m",
            "server_modules.hosted_secure_worker",
        ]

        def fake_rust(command: str, payload: dict, **kwargs):
            if command == "build-sandbox-command":
                return {
                    "ok": True,
                    "decision": "allow",
                    "next_action": "build_hardened_container_command",
                    "command": expected_command,
                }
            if command == "sandbox-execution-decision":
                self.assertEqual(payload["operation"], "launch_worker")
                self.assertEqual(payload["docker_args"], expected_command)
                return {"ok": True, "decision": "allow", "next_action": "launch_hosted_worker"}
            raise AssertionError(f"unexpected command {command}")

        completed = mock.Mock(returncode=0, stdout="{}", stderr="")
        with mock.patch.object(
            docker_execution_sandbox.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=fake_rust,
        ) as rust_mock, mock.patch.object(
            docker_execution_sandbox.subprocess,
            "run",
            return_value=completed,
        ) as run_mock:
            result = docker_execution_sandbox.run_docker_worker(
                sandbox_root="/tmp/empyralis-sandbox-proof",
                payload={"run_id": "run-1"},
            )

        self.assertIs(result, completed)
        self.assertEqual([call.args[0] for call in rust_mock.call_args_list], ["build-sandbox-command", "sandbox-execution-decision"])
        run_mock.assert_called_once()

    def test_run_docker_worker_wrong_launch_action_blocks_before_subprocess(self) -> None:
        expected_command = [
            "docker",
            "run",
            "--rm",
            "empyralis-sandbox:latest",
        ]

        def fake_rust(command: str, payload: dict, **kwargs):
            if command == "build-sandbox-command":
                return {
                    "ok": True,
                    "decision": "allow",
                    "next_action": "build_hardened_container_command",
                    "command": expected_command,
                }
            if command == "sandbox-execution-decision":
                return {"ok": True, "decision": "allow", "next_action": "build_restricted_sandbox_profile"}
            raise AssertionError(f"unexpected command {command}")

        with mock.patch.object(
            docker_execution_sandbox.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=fake_rust,
        ), mock.patch.object(
            docker_execution_sandbox.subprocess,
            "run",
        ) as run_mock:
            with self.assertRaises(RuntimeError) as raised:
                docker_execution_sandbox.run_docker_worker(
                    sandbox_root="/tmp/empyralis-sandbox-proof",
                    payload={"run_id": "run-1"},
                )

        self.assertEqual(str(raised.exception), "unexpected_next_action")
        run_mock.assert_not_called()

    def test_docker_sandbox_result_delegates_outcome_classification_to_rust(self) -> None:
        completed = mock.Mock(returncode=0, stdout='{"reply":"ok","artifacts":[]}', stderr="")
        expected_outcome = {
            "ok": True,
            "decision": "allow",
            "status": "succeeded",
            "retryable": False,
            "retention_class": "short",
        }
        with mock.patch.object(
            docker_execution_sandbox.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value=expected_outcome,
        ) as rust_mock:
            result = docker_execution_sandbox.docker_sandbox_result(
                completed,
                sandbox_root="/tmp/empyralis-sandbox-proof",
            )

        self.assertEqual(result["reply"], "ok")
        self.assertEqual(result["execution_outcome"], expected_outcome)
        rust_mock.assert_called_once()
        self.assertEqual(rust_mock.call_args.args[0], "execution-outcome")
        self.assertEqual(rust_mock.call_args.args[1]["exit_code"], 0)


if __name__ == "__main__":
    unittest.main()
