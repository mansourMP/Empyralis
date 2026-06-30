import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import orion_local_worker_execution as worker_execution


class LocalWorkerFileOperationTests(unittest.TestCase):
    def test_write_operation_uses_atomic_replace(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir).resolve()
            operation = {
                "mode": "write",
                "path": "notes.txt",
                "content": "hello atomic world",
                "overwrite": True,
                "__step_index__": 0,
            }
            with (
                patch.object(worker_execution, "assert_file_mount_access", return_value={"mode": "write"}),
                patch.object(worker_execution.os, "replace", wraps=worker_execution.os.replace) as replace_mock,
            ):
                action, artifact = worker_execution._run_file_operation(operation, root, {})

            target = root / "notes.txt"
            self.assertEqual(target.read_text(encoding="utf-8"), "hello atomic world")
            self.assertEqual(action["mode"], "write")
            self.assertEqual(artifact["file_path"], "notes.txt")
            replace_mock.assert_called_once()
            self.assertEqual(list(root.glob(".notes.txt.*.tmp")), [])

    def test_write_operation_refuses_existing_file_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir).resolve()
            target = root / "notes.txt"
            target.write_text("original", encoding="utf-8")
            operation = {
                "mode": "write",
                "path": "notes.txt",
                "content": "replacement",
                "__step_index__": 0,
            }
            with patch.object(worker_execution, "assert_file_mount_access", return_value={"mode": "write"}):
                with self.assertRaises(RuntimeError):
                    worker_execution._run_file_operation(operation, root, {})

            self.assertEqual(target.read_text(encoding="utf-8"), "original")

    def test_write_operation_fails_cleanly_when_file_is_locked(self) -> None:
        if worker_execution.fcntl is None:
            self.skipTest("fcntl unavailable on this platform")
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir).resolve()
            operation = {
                "mode": "write",
                "path": "notes.txt",
                "content": "replacement",
                "overwrite": True,
                "lock_timeout_seconds": 0.1,
                "lock_retry_interval_seconds": 0.02,
                "__step_index__": 0,
            }
            with (
                patch.object(worker_execution, "assert_file_mount_access", return_value={"mode": "write"}),
                patch.object(worker_execution.fcntl, "flock", side_effect=BlockingIOError),
            ):
                with self.assertRaisesRegex(RuntimeError, "currently locked by another local run"):
                    worker_execution._run_file_operation(operation, root, {})


if __name__ == "__main__":
    unittest.main()
