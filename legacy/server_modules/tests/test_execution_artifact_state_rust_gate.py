from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from server_modules import artifact_service, cli_companion_service, execution_sandbox_service


_ALLOW_DECISION = {
    "ok": True,
    "decision": "allow",
    "reason": "runtime_state_store_policy_satisfied",
    "operation": "runtime-state-store-decision",
    "next_action": "write_state",
    "approval_required": False,
    "cacheable": False,
    "audit_visibility": "standard",
}


class ExecutionArtifactStateRustGateTests(unittest.TestCase):
    def test_artifact_record_calls_rust_before_persisting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "artifact.json"
            record = artifact_service.ArtifactRecord(
                artifact_id="artifact-1",
                run_id="run-1",
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                kind="file",
                uri="artifact://artifact-1/file.txt",
                label="file.txt",
                mime_type="text/plain",
                content_type="text/plain",
                byte_size=4,
                created_at="2026-01-01T00:00:00Z",
            )
            with mock.patch.object(artifact_service, "_artifact_record_path", return_value=target), mock.patch.object(
                artifact_service.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value={**_ALLOW_DECISION, "next_action": "persist_artifact_record"},
            ) as rust_decision:
                artifact_service._persist_record(record)

            self.assertTrue(target.exists())
            payload = rust_decision.call_args.kwargs
            self.assertEqual(payload["operation"], "persist_artifact_record")
            self.assertEqual(payload["state_class"], "artifact_records")
            self.assertEqual(payload["workspace_id"], "workspace-1")
            self.assertEqual(payload["run_id"], "run-1")

    def test_artifact_record_wrong_next_action_blocks_before_persisting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "artifact.json"
            record = artifact_service.ArtifactRecord(
                artifact_id="artifact-1",
                run_id="run-1",
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                kind="file",
                uri="artifact://artifact-1/file.txt",
                label="file.txt",
                mime_type="text/plain",
                content_type="text/plain",
                byte_size=4,
                created_at="2026-01-01T00:00:00Z",
            )
            with mock.patch.object(artifact_service, "_artifact_record_path", return_value=target), mock.patch.object(
                artifact_service.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value={**_ALLOW_DECISION, "next_action": "write_artifact_content_file"},
            ):
                with self.assertRaisesRegex(artifact_service.ArtifactServiceRustGateError, "unexpected next_action"):
                    artifact_service._persist_record(record)

            self.assertFalse(target.exists())

    def test_sandbox_base_image_blocks_before_file_write_when_rust_blocks(self) -> None:
        block_decision = {
            **_ALLOW_DECISION,
            "ok": False,
            "decision": "block",
            "reason": "owner_access_denied",
            "operation": "write_hosted_sandbox_base_image",
            "audit_visibility": "security",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            image_root = Path(temp_dir) / "image"
            with mock.patch.object(
                execution_sandbox_service.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=block_decision,
            ):
                with self.assertRaises(execution_sandbox_service.HostedSecureSandboxError):
                    execution_sandbox_service._write_read_only_base_image(
                        image_root=image_root,
                        payload={"manifest": {"id": "demo"}},
                        scope={"read_only_base_image": True},
                    )

            self.assertFalse((image_root / "manifest.json").exists())

    def test_sandbox_base_image_wrong_next_action_blocks_before_file_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_root = Path(temp_dir) / "image"
            with mock.patch.object(
                execution_sandbox_service.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value={**_ALLOW_DECISION, "next_action": "write_hosted_worker_output"},
            ):
                with self.assertRaisesRegex(execution_sandbox_service.HostedSecureSandboxError, "unexpected next_action"):
                    execution_sandbox_service._enforce_hosted_base_image_state_decision(
                        image_root=image_root,
                        payload={"manifest": {"id": "demo"}},
                        scope={"read_only_base_image": True},
                    )

            self.assertFalse((image_root / "manifest.json").exists())

    def test_cli_companion_state_sanitizes_and_gates_config_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir) / ".empyralis"
            config_file = config_dir / "config.json"
            with mock.patch.object(cli_companion_service, "_CONFIG_DIR", config_dir), mock.patch.object(
                cli_companion_service.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value={**_ALLOW_DECISION, "next_action": "save_cli_companion_state"},
            ) as rust_decision:
                cli_companion_service._write_json(
                    config_file,
                    {"api_url": "http://localhost:8000", "api_key": "secret", "workspace_id": "workspace-1"},
                )

            self.assertTrue(config_file.exists())
            payload = rust_decision.call_args.kwargs
            self.assertEqual(payload["operation"], "save_cli_companion_state")
            self.assertEqual(payload["state_class"], "cli_companion_state")
            self.assertTrue(payload["payload"]["has_api_key"])
            self.assertNotIn("secret", str(payload["payload"]))

    def test_cli_companion_state_wrong_next_action_blocks_before_persisting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir) / ".empyralis"
            config_file = config_dir / "config.json"
            with mock.patch.object(cli_companion_service, "_CONFIG_DIR", config_dir), mock.patch.object(
                cli_companion_service.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value={**_ALLOW_DECISION, "next_action": "write_profile_api_file"},
            ):
                with self.assertRaisesRegex(cli_companion_service.CliCompanionRustGateError, "unexpected_next_action"):
                    cli_companion_service._write_json(
                        config_file,
                        {"api_url": "http://localhost:8000", "api_key": "secret", "workspace_id": "workspace-1"},
                    )

            self.assertFalse(config_file.exists())

    def test_artifact_content_file_wrong_next_action_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "artifact.bin"
            with mock.patch.object(
                artifact_service.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value={**_ALLOW_DECISION, "next_action": "persist_artifact_record"},
            ):
                with self.assertRaisesRegex(artifact_service.ArtifactServiceRustGateError, "unexpected next_action"):
                    artifact_service._enforce_artifact_content_file_decision(
                        artifact_id="artifact-1",
                        run_id="run-1",
                        tenant_id="tenant-1",
                        workspace_id="workspace-1",
                        object_key="objects/artifact.bin",
                        target=target,
                        content_bytes=256,
                    )


if __name__ == "__main__":
    unittest.main()
