import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from server_modules import agent_workspace_api
from server_modules import artifact_service


class ArtifactDetailApiTests(unittest.TestCase):
    def test_artifact_detail_payload_is_resolved_by_artifact_id(self):
        with tempfile.TemporaryDirectory() as tempdir:
            store_root = Path(tempdir) / "artifact-store"
            with patch.dict(os.environ, {"EMPYRALIS_OBJECT_STORAGE_ROOT": str(store_root)}, clear=False):
                record = artifact_service.store_artifact_bytes(
                    b"artifact body",
                    run_id="run-1",
                    kind="report",
                    file_name="report.txt",
                    workspace_id="ws-1",
                    tenant_id="tenant-1",
                    metadata={"source": "unit-test"},
                )

                request = types.SimpleNamespace(headers={}, query_params={"workspace_id": "ws-1"})
                with patch.object(agent_workspace_api, "_ensure_artifacts_access_for_workspace"), patch.object(
                    agent_workspace_api,
                    "enforce_workspace_access",
                    return_value="ws-1",
                ):
                    payload = agent_workspace_api._artifact_detail_payload_for_id(
                        record.artifact_id,
                        current_user={"user_id": "user-1"},
                        request=request,
                    )

        self.assertEqual(payload["artifact_id"], record.artifact_id)
        self.assertEqual(payload["workspace_id"], "ws-1")
        self.assertEqual(payload["preview_kind"], "text")
        self.assertEqual(payload["media_type"], "text/plain")
        self.assertIn("artifact body", payload["text_preview"])

    def test_artifact_content_response_is_attachment_with_filename(self):
        with tempfile.TemporaryDirectory() as tempdir:
            store_root = Path(tempdir) / "artifact-store"
            with patch.dict(os.environ, {"EMPYRALIS_OBJECT_STORAGE_ROOT": str(store_root)}, clear=False):
                record = artifact_service.store_artifact_bytes(
                    b"attachment payload",
                    run_id="run-2",
                    kind="export",
                    file_name="export.txt",
                    workspace_id="ws-1",
                    tenant_id="tenant-1",
                )

                request = types.SimpleNamespace(headers={"x-empyralis-workspace-id": "ws-1"})
                with patch.object(agent_workspace_api, "_ensure_artifacts_access_for_workspace"), patch.object(
                    agent_workspace_api,
                    "enforce_workspace_access",
                    return_value="ws-1",
                ):
                    response = agent_workspace_api._artifact_file_response_for_id(
                        record.artifact_id,
                        current_user={"user_id": "user-1"},
                        request=request,
                        disposition="attachment",
                    )

        self.assertEqual(response.media_type, "text/plain")
        self.assertIn("attachment;", response.headers["content-disposition"])
        self.assertIn("export.txt", response.headers["content-disposition"])

    def test_cross_workspace_artifact_access_is_rejected(self):
        with tempfile.TemporaryDirectory() as tempdir:
            store_root = Path(tempdir) / "artifact-store"
            with patch.dict(os.environ, {"EMPYRALIS_OBJECT_STORAGE_ROOT": str(store_root)}, clear=False):
                record = artifact_service.store_artifact_bytes(
                    b"cross workspace",
                    run_id="run-3",
                    kind="report",
                    file_name="cross.txt",
                    workspace_id="ws-2",
                    tenant_id="tenant-1",
                )

                request = types.SimpleNamespace(headers={}, query_params={"workspace_id": "ws-1"})
                with patch.object(agent_workspace_api, "_ensure_artifacts_access_for_workspace"), patch.object(
                    agent_workspace_api,
                    "enforce_workspace_access",
                    return_value="ws-2",
                ):
                    with self.assertRaises(HTTPException) as exc:
                        agent_workspace_api._artifact_detail_payload_for_id(
                            record.artifact_id,
                            current_user={"user_id": "user-1"},
                            request=request,
                        )

        self.assertEqual(exc.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
