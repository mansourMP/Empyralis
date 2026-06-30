import asyncio
import importlib
import unittest
from pathlib import Path
from unittest.mock import patch

from server_modules.agent_manifest import AgentManifest, AgentManifestIdentity
from server_modules import execution_sandbox_service


def _manifest() -> AgentManifest:
    return AgentManifest(
        manifest_id="manifest-parts-pro",
        identity=AgentManifestIdentity(
            name="Parts Pro",
            role="Inventory Specialist",
            archetype="support_specialist",
            summary="Help customers find parts.",
        ),
    )


class ExecutionSandboxServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        global execution_sandbox_service
        execution_sandbox_service = importlib.import_module("server_modules.execution_sandbox_service")

    def test_runtime_scope_for_hosted_secure_has_ephemeral_isolation_defaults(self):
        scope = execution_sandbox_service.runtime_scope(
            runtime_mode="hosted_secure",
            runtime_profile={
                "id": "profile-cloud",
                "runtime_class": "cloud_worker",
            },
        )

        self.assertEqual(scope["mode"], "hosted_secure")
        self.assertEqual(scope["workspace_kind"], "ephemeral")
        self.assertTrue(scope["read_only_base_image"])
        self.assertFalse(scope["host_mounts_allowed"])
        self.assertFalse(scope["docker_socket_exposed"])
        self.assertTrue(scope["network_policy"]["hooks_ready"])
        self.assertEqual(scope["state_layer_policy"]["local_private_memory_access"], "cloud_safe_summaries_only")
        self.assertFalse(scope["state_layer_policy"]["cross_install_private_memory_allowed"])
        self.assertFalse(scope["state_layer_policy"]["specialist_to_captain_private_access"])
        self.assertEqual(scope["state_layer_policy"]["artifacts_history"]["cross_install_exchange_mode"], "artifacts_only")

    def test_runtime_scope_for_local_secure_uses_approved_folders_and_apps(self):
        scope = execution_sandbox_service.runtime_scope(
            runtime_mode="local_secure",
            runtime_profile={
                "id": "profile-local",
                "runtime_class": "desktop_companion",
                "root_folder_uri": "/Users/mansur/Documents/Empyralis",
                "metadata": {
                    "allowed_folders": ["/Users/mansur/Desktop"],
                    "allowed_applications": ["Mail", "Safari"],
                },
            },
            install={"root_folder_uri": "/Users/mansur/Documents/Empyralis"},
        )

        self.assertEqual(scope["mode"], "local_secure")
        self.assertTrue(scope["host_mounts_allowed"])
        self.assertEqual(
            scope["approved_folders"],
            ["/Users/mansur/Documents/Empyralis", "/Users/mansur/Desktop"],
        )
        self.assertEqual(scope["approved_applications"], ["Mail", "Safari"])
        self.assertEqual(scope["state_layer_policy"]["local_private_memory_access"], "allowed_locally")
        self.assertFalse(scope["state_layer_policy"]["specialist_private_memory"]["cross_install_allowed"])

    def test_hosted_sandbox_preparation_delegates_to_rust_kernel(self):
        scope = {
            "mode": "hosted_secure",
            "driver": "docker",
            "read_only_base_image": True,
            "base_image_id": "empyralis-hosted-secure-v1",
            "host_mounts_allowed": False,
            "docker_socket_exposed": False,
            "limits": {
                "cpu_seconds": 20,
                "memory_mb": 512,
                "timeout_seconds": 25,
                "max_open_files": 64,
                "max_file_bytes": 16 * 1024 * 1024,
            },
            "network_policy": {
                "mode": "allowlist_hooks",
                "allow_outbound": True,
                "allow_private_hosts": False,
                "hooks_ready": True,
            },
        }
        with patch.object(
            execution_sandbox_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={"ok": True, "decision": "allow", "next_action": "prepare_isolated_workspace"},
        ) as rust_mock:
            execution_sandbox_service._enforce_hosted_sandbox_preparation(
                sandbox_root=Path("/tmp/empyralis-sandbox-proof"),
                scope=scope,
            )

        rust_mock.assert_called_once()
        self.assertEqual(rust_mock.call_args.args[0], "sandbox-execution-decision")
        self.assertEqual(rust_mock.call_args.args[1]["operation"], "prepare_sandbox")
        self.assertTrue(rust_mock.call_args.args[1]["image_approved"])

    def test_hosted_sandbox_preparation_wrong_next_action_blocks(self):
        scope = {
            "mode": "hosted_secure",
            "driver": "docker",
            "read_only_base_image": True,
            "base_image_id": "empyralis-hosted-secure-v1",
            "host_mounts_allowed": False,
            "docker_socket_exposed": False,
            "limits": {
                "cpu_seconds": 20,
                "memory_mb": 512,
                "timeout_seconds": 25,
                "max_open_files": 64,
                "max_file_bytes": 16 * 1024 * 1024,
            },
            "network_policy": {
                "mode": "allowlist_hooks",
                "allow_outbound": True,
                "allow_private_hosts": False,
                "hooks_ready": True,
            },
        }
        with patch.object(
            execution_sandbox_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={"ok": True, "decision": "allow", "next_action": "launch_hosted_worker"},
        ):
            with self.assertRaisesRegex(execution_sandbox_service.HostedSecureSandboxError, "unexpected next_action"):
                execution_sandbox_service._enforce_hosted_sandbox_preparation(
                    sandbox_root=Path("/tmp/empyralis-sandbox-proof"),
                    scope=scope,
                )

    def test_hosted_worker_launch_delegates_to_rust_kernel(self):
        scope = {
            "mode": "hosted_secure",
            "driver": "subprocess_fallback",
            "read_only_base_image": True,
            "base_image_id": "empyralis-hosted-secure-v1",
            "host_mounts_allowed": False,
            "docker_socket_exposed": False,
            "limits": {
                "cpu_seconds": 20,
                "memory_mb": 512,
                "timeout_seconds": 25,
                "max_open_files": 64,
                "max_file_bytes": 16 * 1024 * 1024,
            },
            "network_policy": {
                "mode": "allowlist_hooks",
                "allow_outbound": True,
                "allow_private_hosts": False,
                "hooks_ready": True,
            },
        }
        with patch.object(
            execution_sandbox_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={"ok": True, "decision": "allow", "next_action": "launch_hosted_worker"},
        ) as rust_mock:
            execution_sandbox_service._enforce_hosted_worker_launch(
                sandbox_root=Path("/tmp/empyralis-sandbox-proof"),
                scope=scope,
                output_file=Path("/tmp/empyralis-sandbox-proof/workspace/outputs/turn-result.json"),
            )

        rust_mock.assert_called_once()
        self.assertEqual(rust_mock.call_args.args[0], "sandbox-execution-decision")
        self.assertEqual(rust_mock.call_args.args[1]["operation"], "launch_worker")
        self.assertEqual(rust_mock.call_args.args[1]["driver"], "subprocess_fallback")

    def test_hosted_sandbox_environment_wrong_next_action_blocks(self):
        scope = {
            "mode": "hosted_secure",
            "driver": "subprocess_fallback",
            "read_only_base_image": True,
            "base_image_id": "empyralis-hosted-secure-v1",
            "host_mounts_allowed": False,
            "docker_socket_exposed": False,
            "limits": {
                "cpu_seconds": 20,
                "memory_mb": 512,
                "timeout_seconds": 25,
                "max_open_files": 64,
                "max_file_bytes": 16 * 1024 * 1024,
            },
            "network_policy": {
                "mode": "allowlist_hooks",
                "allow_outbound": True,
                "allow_private_hosts": False,
                "hooks_ready": True,
            },
        }
        with patch.object(
            execution_sandbox_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={"ok": True, "decision": "allow", "next_action": "build_restricted_sandbox_profile"},
        ):
            with self.assertRaisesRegex(execution_sandbox_service.HostedSecureSandboxError, "unexpected next_action"):
                execution_sandbox_service._enforce_hosted_sandbox_environment_policy(
                    sandbox_root=Path("/tmp/empyralis-sandbox-proof"),
                    output_path=Path("/tmp/empyralis-sandbox-proof/workspace/outputs/turn-result.json"),
                    scope=scope,
                    env={"PATH": "/usr/bin"},
                )

    def test_execute_hosted_customer_turn_routes_through_worker_and_attaches_sandbox_metadata(self):
        async def _run() -> dict:
            with patch(
                "server_modules.execution_sandbox_service._run_hosted_worker",
                return_value={"status": "ok", "reply": "Sandboxed reply"},
            ) as worker_mock:
                result = await execution_sandbox_service.execute_hosted_customer_turn(
                    manifest=_manifest(),
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    goal="Hello",
                )
            self.assertEqual(result["status"], "ok")
            worker_mock.assert_called_once()
            return result

        result = asyncio.run(_run())
        self.assertEqual(result["reply"], "Sandboxed reply")


if __name__ == "__main__":
    unittest.main()
