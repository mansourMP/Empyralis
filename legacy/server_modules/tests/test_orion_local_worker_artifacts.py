import base64
import importlib.util
import os
from pathlib import Path
import tempfile
from unittest import TestCase
from unittest.mock import patch

from server_modules import artifact_service


def _load_module():
    module_path = Path(__file__).resolve().parents[2] / "scripts" / "orion_local_worker_execution.py"
    spec = importlib.util.spec_from_file_location("test_orion_local_worker_execution_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


worker_execution = _load_module()


class LocalWorkerArtifactStorageTests(TestCase):
    def test_screenshot_artifact_is_promoted_to_canonical_object_storage(self) -> None:
        png_bytes = b"fake-png-binary"
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir) / "local-root"
            root.mkdir(parents=True, exist_ok=True)
            store_root = Path(tempdir) / "object-store"
            with (
                patch.dict(
                    os.environ,
                    {
                        "ORION_LOCAL_COMPANION_ROOT": str(root),
                        "EMPYRALIS_OBJECT_STORAGE_ROOT": str(store_root),
                    },
                    clear=False,
                ),
                patch.object(
                    worker_execution,
                    "capture_screenshot",
                    return_value={
                        "success": True,
                        "images": [{"data_base64": base64.b64encode(png_bytes).decode("ascii")}],
                    },
                ),
            ):
                _summary, result = worker_execution.build_local_execution_pack_result(
                    {"run_id": "run-1"},
                    {"execution_target": "local_companion"},
                    {"operations": [{"tool": "screenshot.capture", "path": "captures/demo.png"}]},
                )
                artifact = result["outputs"]["artifacts"][0]
                stored_path = artifact_service.resolve_artifact_content_path(artifact["uri"])

            action = result["outputs"]["actions"][0]
            step = result["outputs"]["steps"][0]

            self.assertTrue(str(artifact["uri"]).startswith("artifact://"))
            self.assertEqual(artifact["file_path"], artifact["uri"])
            self.assertEqual(action["file_path"], artifact["uri"])
            self.assertEqual(step["artifact_file_path"], artifact["uri"])
            self.assertIsNotNone(stored_path)
            self.assertEqual(stored_path.read_bytes(), png_bytes)
