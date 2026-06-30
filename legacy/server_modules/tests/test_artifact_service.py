import os
import importlib
from pathlib import Path
import tempfile
from unittest import TestCase
from unittest.mock import patch

from server_modules import artifact_service


class FakeS3Client:
    def __init__(self) -> None:
        self.objects = {}
        self.download_calls = 0

    def upload_file(self, filename: str, bucket: str, key: str, ExtraArgs=None) -> None:
        self.objects[(bucket, key)] = {
            "body": Path(filename).read_bytes(),
            "content_type": (ExtraArgs or {}).get("ContentType"),
        }

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str | None = None) -> None:
        self.objects[(Bucket, Key)] = {
            "body": bytes(Body),
            "content_type": ContentType,
        }

    def download_file(self, bucket: str, key: str, filename: str) -> None:
        self.download_calls += 1
        payload = self.objects[(bucket, key)]
        target = Path(filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload["body"])


class ArtifactServiceTests(TestCase):
    def setUp(self) -> None:
        global artifact_service
        artifact_service = importlib.import_module("server_modules.artifact_service")

    def test_store_artifact_file_persists_metadata_and_resolves_content(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            source = Path(tempdir) / "report.txt"
            source.write_text("hello from empyralis", encoding="utf-8")
            store_root = Path(tempdir) / "object-store"

            with patch.dict(os.environ, {"EMPYRALIS_OBJECT_STORAGE_ROOT": str(store_root)}, clear=False):
                record = artifact_service.store_artifact_file(
                    source,
                    run_id="run-1",
                    kind="report",
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    step_number=2,
                    machine_id="machine-1",
                )

                self.assertTrue(record.uri.startswith("artifact://"))
                metadata = artifact_service.load_artifact_metadata(record.uri)
                self.assertIsNotNone(metadata)
                self.assertEqual(metadata["artifact_id"], record.artifact_id)
                self.assertEqual(metadata["run_id"], "run-1")
                self.assertEqual(metadata["tenant_id"], "tenant-1")
                self.assertEqual(metadata["workspace_id"], "workspace-1")
                self.assertEqual(metadata["machine_id"], "machine-1")
                self.assertEqual(metadata["step_number"], 2)
                self.assertEqual(metadata["byte_size"], len("hello from empyralis".encode("utf-8")))

                target = artifact_service.resolve_artifact_content_path(record.uri)
                self.assertIsNotNone(target)
                self.assertEqual(target.read_text(encoding="utf-8"), "hello from empyralis")

    def test_store_artifact_bytes_records_retention_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            store_root = Path(tempdir) / "object-store"
            with patch.dict(os.environ, {"EMPYRALIS_OBJECT_STORAGE_ROOT": str(store_root)}, clear=False):
                record = artifact_service.store_artifact_bytes(
                    b'{"deleted":true}',
                    run_id="run-2",
                    kind="file_delete",
                    file_name="deleted-record.json",
                    tenant_id="tenant-2",
                    workspace_id="workspace-2",
                    content_type="application/json",
                    retention_days=30,
                )

                metadata = artifact_service.load_artifact_metadata(record.uri)
                self.assertIsNotNone(metadata)
                self.assertEqual(metadata["tenant_id"], "tenant-2")
                self.assertEqual(metadata["workspace_id"], "workspace-2")
                self.assertEqual(metadata["content_type"], "application/json")
                self.assertEqual(metadata["retention"]["retention_days"], 30)
                self.assertEqual(metadata["retention"]["policy_status"], "placeholder")

    def test_artifact_resolution_respects_install_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            source = Path(tempdir) / "report.txt"
            source.write_text("install scoped artifact", encoding="utf-8")
            store_root = Path(tempdir) / "object-store"

            with patch.dict(os.environ, {"EMPYRALIS_OBJECT_STORAGE_ROOT": str(store_root)}, clear=False):
                record = artifact_service.store_artifact_file(
                    source,
                    run_id="run-install",
                    kind="report",
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    agent_install_id="install-a",
                )

                allowed = artifact_service.load_artifact_metadata(record.uri, agent_install_id="install-a")
                denied = artifact_service.load_artifact_metadata(record.uri, agent_install_id="install-b")
                allowed_path = artifact_service.resolve_artifact_content_path(record.uri, agent_install_id="install-a")
                denied_path = artifact_service.resolve_artifact_content_path(record.uri, agent_install_id="install-b")

            self.assertIsNotNone(allowed)
            self.assertEqual(allowed["agent_install_id"], "install-a")
            self.assertIsNone(denied)
            self.assertIsNotNone(allowed_path)
            self.assertIsNone(denied_path)

    @patch("server_modules.artifact_service.record_reliability_latency_sample")
    def test_store_artifact_bytes_records_artifact_availability_latency(self, mock_record_latency) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            store_root = Path(tempdir) / "object-store"
            with patch.dict(os.environ, {"EMPYRALIS_OBJECT_STORAGE_ROOT": str(store_root)}, clear=False):
                record = artifact_service.store_artifact_bytes(
                    b"hello",
                    run_id="run-metric",
                    kind="report",
                    file_name="report.txt",
                )

        self.assertEqual(record.run_id, "run-metric")
        self.assertEqual(mock_record_latency.call_args.args[0], "artifact_availability_latency_ms")
        self.assertGreaterEqual(mock_record_latency.call_args.args[1], 0.0)

    def test_store_artifact_file_uses_s3_backend_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            source = Path(tempdir) / "report.txt"
            source.write_text("hello from s3 backend", encoding="utf-8")
            store_root = Path(tempdir) / "object-store"
            fake_client = FakeS3Client()

            with (
                patch.dict(
                    os.environ,
                    {
                        "EMPYRALIS_OBJECT_STORAGE_BACKEND": "s3",
                        "EMPYRALIS_OBJECT_STORAGE_ROOT": str(store_root),
                        "EMPYRALIS_OBJECT_STORAGE_BUCKET": "empyralis-artifacts",
                        "EMPYRALIS_OBJECT_STORAGE_REGION": "us-east-1",
                        "EMPYRALIS_OBJECT_STORAGE_ENDPOINT": "https://storage.example.test",
                    },
                    clear=False,
                ),
                patch.object(artifact_service, "_build_s3_client", return_value=fake_client),
            ):
                record = artifact_service.store_artifact_file(
                    source,
                    run_id="run-s3",
                    kind="report",
                    tenant_id="tenant-s3",
                    workspace_id="workspace-s3",
                )
                metadata = artifact_service.load_artifact_metadata(record.uri)
                target = artifact_service.resolve_artifact_content_path(record.uri)

            self.assertEqual(record.storage_backend, artifact_service.S3_ARTIFACT_STORAGE_BACKEND)
            self.assertIsNotNone(metadata)
            self.assertEqual(metadata["storage_backend"], artifact_service.S3_ARTIFACT_STORAGE_BACKEND)
            self.assertEqual(metadata["storage_bucket"], "empyralis-artifacts")
            self.assertEqual(metadata["storage_region"], "us-east-1")
            self.assertEqual(metadata["storage_endpoint"], "https://storage.example.test")
            self.assertNotIn("stored_path", metadata)
            self.assertEqual(fake_client.download_calls, 1)
            self.assertIsNotNone(target)
            self.assertEqual(target.read_text(encoding="utf-8"), "hello from s3 backend")
            self.assertIn(("empyralis-artifacts", metadata["object_key"]), fake_client.objects)

    def test_export_artifact_scope_filters_records_by_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            store_root = Path(tempdir) / "object-store"
            with patch.dict(os.environ, {"EMPYRALIS_OBJECT_STORAGE_ROOT": str(store_root)}, clear=False):
                artifact_service.store_artifact_bytes(
                    b"one",
                    run_id="run-1",
                    kind="report",
                    file_name="one.txt",
                    tenant_id="tenant-1",
                    workspace_id="workspace-a",
                )
                artifact_service.store_artifact_bytes(
                    b"two",
                    run_id="run-2",
                    kind="report",
                    file_name="two.txt",
                    tenant_id="tenant-1",
                    workspace_id="workspace-b",
                )

                export_payload = artifact_service.export_artifact_scope(
                    tenant_id="tenant-1",
                    workspace_id="workspace-a",
                )

        self.assertEqual(export_payload["record_count"], 1)
        self.assertEqual(export_payload["records"][0]["workspace_id"], "workspace-a")

    def test_plan_artifact_retention_marks_expired_records(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            store_root = Path(tempdir) / "object-store"
            with patch.dict(os.environ, {"EMPYRALIS_OBJECT_STORAGE_ROOT": str(store_root)}, clear=False):
                artifact_service.store_artifact_bytes(
                    b"expired",
                    run_id="run-1",
                    kind="report",
                    file_name="expired.txt",
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    retention_days=1,
                    created_at="2026-04-01T00:00:00Z",
                )
                artifact_service.store_artifact_bytes(
                    b"active",
                    run_id="run-2",
                    kind="report",
                    file_name="active.txt",
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    retention_days=30,
                    created_at="2026-04-12T00:00:00Z",
                )

                plan = artifact_service.plan_artifact_retention(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    now_iso="2026-04-12T00:00:00Z",
                )

        self.assertEqual(plan["expired_count"], 1)
        self.assertEqual(plan["retained_count"], 1)
        self.assertEqual(plan["expired_candidates"][0]["run_id"], "run-1")

    def test_rehearse_artifact_restore_reports_ok_for_stored_files(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            store_root = Path(tempdir) / "object-store"
            with patch.dict(os.environ, {"EMPYRALIS_OBJECT_STORAGE_ROOT": str(store_root)}, clear=False):
                record = artifact_service.store_artifact_bytes(
                    b"restore-me",
                    run_id="run-restore",
                    kind="report",
                    file_name="restore.txt",
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                )
                rehearsal = artifact_service.rehearse_artifact_restore(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                )

        self.assertTrue(rehearsal["ok"])
        self.assertEqual(rehearsal["checked_count"], 1)
        self.assertEqual(rehearsal["checked_ids"], [record.artifact_id])
