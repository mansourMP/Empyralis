import os
import tempfile
import importlib
import unittest
from pathlib import Path
from unittest.mock import patch

from server_modules import artifact_service, file_bridge_service


class FileBridgeServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        global artifact_service, file_bridge_service
        artifact_service = importlib.import_module("server_modules.artifact_service")
        file_bridge_service = importlib.import_module("server_modules.file_bridge_service")

    def test_hosted_secure_export_uses_managed_bridge_root(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            store_root = Path(tempdir) / "object-store"
            bridge_root = Path(tempdir) / "file-bridge"
            with patch.dict(
                os.environ,
                {
                    "EMPYRALIS_OBJECT_STORAGE_ROOT": str(store_root),
                    "EMPYRALIS_FILE_BRIDGE_ROOT": str(bridge_root),
                },
                clear=False,
            ):
                record = artifact_service.store_artifact_bytes(
                    b"hello",
                    run_id="run-1",
                    kind="report",
                    file_name="report.txt",
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    agent_install_id="install-1",
                )
                with patch("server_modules.file_bridge_service.outbox_service.emit_runtime_event") as emit_mock:
                    emit_mock.return_value = type("Event", (), {"event_id": "evt-1", "event_type": "artifact_exported"})()
                    result = file_bridge_service.export_artifact_for_install(
                        install={
                            "id": "install-1",
                            "runtime_mode": "hosted_secure",
                            "runtime_profile": {"id": "profile-cloud", "metadata": {}},
                        },
                        tenant_id="tenant-1",
                        workspace_id="workspace-1",
                        artifact_reference=record.uri,
                        actor={"user_id": "user-1"},
                    )

            target = Path(result["destination_path"])
            self.assertTrue(target.exists())
            self.assertEqual(target.read_bytes(), b"hello")
            self.assertEqual(result["bridge_mode"], "export_bridge")
            self.assertIn("/file-bridge/exports/workspace-1/agents/install-1/", target.as_posix())
            emit_mock.assert_called_once()

    def test_export_can_target_configured_sync_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            store_root = Path(tempdir) / "object-store"
            sync_root = Path(tempdir) / "sync-folder"
            with patch.dict(os.environ, {"EMPYRALIS_OBJECT_STORAGE_ROOT": str(store_root)}, clear=False):
                record = artifact_service.store_artifact_bytes(
                    b"synced",
                    run_id="run-2",
                    kind="report",
                    file_name="daily.txt",
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    agent_install_id="install-1",
                )
                with patch("server_modules.file_bridge_service.outbox_service.emit_runtime_event") as emit_mock:
                    emit_mock.return_value = type("Event", (), {"event_id": "evt-2", "event_type": "artifact_exported"})()
                    result = file_bridge_service.export_artifact_for_install(
                        install={
                            "id": "install-1",
                            "runtime_mode": "local_secure",
                            "runtime_profile": {
                                "id": "profile-local",
                                "metadata": {
                                    "sync_folders": [
                                        {"id": "reports", "label": "Reports", "path": str(sync_root)},
                                    ]
                                },
                            },
                        },
                        tenant_id="tenant-1",
                        workspace_id="workspace-1",
                        artifact_reference=record.uri,
                        destination_path="daily/output.txt",
                        sync_folder_id="reports",
                    )

            target = Path(result["destination_path"])
            self.assertTrue(target.exists())
            self.assertEqual(target.read_bytes(), b"synced")
            self.assertEqual(result["bridge_mode"], "sync_folder")
            self.assertIn("/sync-folder/daily/output.txt", target.as_posix())
            emit_mock.assert_called_once()

    def test_privileged_export_requires_explicit_approval_for_arbitrary_destination(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            store_root = Path(tempdir) / "object-store"
            arbitrary_target = Path(tempdir) / "Desktop" / "final.txt"
            with patch.dict(os.environ, {"EMPYRALIS_OBJECT_STORAGE_ROOT": str(store_root)}, clear=False):
                record = artifact_service.store_artifact_bytes(
                    b"secret",
                    run_id="run-3",
                    kind="report",
                    file_name="secret.txt",
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    agent_install_id="install-1",
                )
                with self.assertRaises(file_bridge_service.FileBridgePermissionError):
                    file_bridge_service.export_artifact_for_install(
                        install={
                            "id": "install-1",
                            "runtime_mode": "privileged_device",
                            "runtime_profile": {"id": "profile-privileged", "metadata": {"requires_privileged_approval": True}},
                        },
                        tenant_id="tenant-1",
                        workspace_id="workspace-1",
                        artifact_reference=record.uri,
                        destination_path=str(arbitrary_target),
                    )

                with patch("server_modules.file_bridge_service.outbox_service.emit_runtime_event") as emit_mock:
                    emit_mock.return_value = type("Event", (), {"event_id": "evt-3", "event_type": "artifact_exported"})()
                    result = file_bridge_service.export_artifact_for_install(
                        install={
                            "id": "install-1",
                            "runtime_mode": "privileged_device",
                            "runtime_profile": {"id": "profile-privileged", "metadata": {"requires_privileged_approval": True}},
                        },
                        tenant_id="tenant-1",
                        workspace_id="workspace-1",
                        artifact_reference=record.uri,
                        destination_path=str(arbitrary_target),
                        policy_context={"privileged_file_export_approved": True},
                    )

            target = Path(result["destination_path"])
            self.assertTrue(target.exists())
            self.assertEqual(target.read_bytes(), b"secret")
            self.assertEqual(result["bridge_mode"], "privileged_device")
            emit_mock.assert_called_once()

    def test_export_cannot_cross_install_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            store_root = Path(tempdir) / "object-store"
            with patch.dict(os.environ, {"EMPYRALIS_OBJECT_STORAGE_ROOT": str(store_root)}, clear=False):
                record = artifact_service.store_artifact_bytes(
                    b"hello",
                    run_id="run-4",
                    kind="report",
                    file_name="report.txt",
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    agent_install_id="install-a",
                )
                with self.assertRaises(file_bridge_service.FileBridgeArtifactNotFoundError):
                    file_bridge_service.export_artifact_for_install(
                        install={
                            "id": "install-b",
                            "runtime_mode": "hosted_secure",
                            "runtime_profile": {"id": "profile-cloud", "metadata": {}},
                        },
                        tenant_id="tenant-1",
                        workspace_id="workspace-1",
                        artifact_reference=record.uri,
                    )


if __name__ == "__main__":
    unittest.main()
