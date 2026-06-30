import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.orion_local_worker_execution import build_local_execution_pack_result
from server_modules.builder_runtime_mapping import default_file_mount_grants
from server_modules.file_mount_security import assert_file_mount_access


class FileMountSecurityTests(unittest.TestCase):
    def test_assert_file_mount_access_allows_project_reads_by_default(self):
        access = assert_file_mount_access(
            "README.md",
            "read",
            default_file_mount_grants(),
            "local_companion",
        )

        self.assertEqual(access["mount"], "project")
        self.assertEqual(access["mode"], "read")

    def test_assert_file_mount_access_allows_project_root_dot_path(self):
        access = assert_file_mount_access(
            ".",
            "read",
            default_file_mount_grants(),
            "local_companion",
        )

        self.assertEqual(access["mount"], "project")
        self.assertEqual(access["path"], ".")
        self.assertEqual(access["mode"], "read")

    def test_assert_file_mount_access_blocks_relative_path_traversal(self):
        for path in ("../secret.txt", "project/../secret.txt", "project\\..\\secret.txt", "%2e%2e/secret.txt"):
            with self.subTest(path=path):
                with self.assertRaises(RuntimeError):
                    assert_file_mount_access(
                        path,
                        "read",
                        default_file_mount_grants(),
                        "local_companion",
                    )

    def test_assert_file_mount_access_blocks_absolute_local_root_without_grant(self):
        with self.assertRaises(RuntimeError):
            assert_file_mount_access(
                str(Path.cwd() / "README.md"),
                "read",
                default_file_mount_grants(),
                "local_companion",
            )

    def test_local_worker_blocks_absolute_file_path_without_local_root_grant(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "secret.txt"
            target.write_text("classified", encoding="utf-8")
            metadata = {
                "execution_target": "local_companion",
                "file_mount_grants": default_file_mount_grants(),
            }
            pack_inputs = {
                "operations": [
                    {
                        "tool": "read_write_files",
                        "mode": "read",
                        "path": str(target),
                    }
                ]
            }
            with patch.dict(os.environ, {"ORION_LOCAL_COMPANION_ROOT": str(root)}, clear=False):
                with self.assertRaises(RuntimeError):
                    build_local_execution_pack_result({"run_id": "run-abs-blocked"}, metadata, pack_inputs)

    def test_local_worker_blocks_absolute_shell_cwd_without_local_root_grant(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            metadata = {
                "execution_target": "local_companion",
                "file_mount_grants": default_file_mount_grants(),
            }
            pack_inputs = {
                "operations": [
                    {
                        "tool": "execute_shell_command",
                        "command": "pwd",
                        "cwd": str(root),
                    }
                ]
            }
            with patch.dict(os.environ, {"ORION_LOCAL_COMPANION_ROOT": str(root)}, clear=False):
                with self.assertRaises(RuntimeError):
                    build_local_execution_pack_result({"run_id": "run-shell-blocked"}, metadata, pack_inputs)

    def test_local_worker_rejects_mixed_shell_capability_and_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            metadata = {
                "execution_target": "local_companion",
                "file_mount_grants": default_file_mount_grants(),
            }
            pack_inputs = {
                "operations": [
                    {
                        "tool": "execute_shell_command",
                        "capability": "stack.status",
                        "command": "pwd",
                    }
                ]
            }
            with patch.dict(os.environ, {"ORION_LOCAL_COMPANION_ROOT": str(root)}, clear=False):
                with self.assertRaises(RuntimeError):
                    build_local_execution_pack_result({"run_id": "run-shell-mixed"}, metadata, pack_inputs)

    def test_local_worker_allows_project_relative_file_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "notes.txt"
            target.write_text("hello", encoding="utf-8")
            metadata = {
                "execution_target": "local_companion",
                "file_mount_grants": default_file_mount_grants(),
            }
            pack_inputs = {
                "operations": [
                    {
                        "tool": "read_write_files",
                        "mode": "read",
                        "path": "notes.txt",
                    }
                ]
            }
            with patch.dict(os.environ, {"ORION_LOCAL_COMPANION_ROOT": str(root)}, clear=False):
                summary, data = build_local_execution_pack_result({"run_id": "run-rel-ok"}, metadata, pack_inputs)

            self.assertIn("Executed 1 of 1 local operations.", summary)
            self.assertEqual(data["outputs"]["operations_executed"], 1)

    @patch("scripts.orion_local_worker_execution._run_browser_capture_task")
    def test_local_worker_blocks_absolute_browser_artifact_path_without_local_root_grant(self, capture_mock):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            metadata = {
                "execution_target": "local_companion",
                "file_mount_grants": default_file_mount_grants(),
            }
            pack_inputs = {
                "operations": [
                    {
                        "tool": "browser_automation",
                        "mode": "capture_page",
                        "url": "https://example.com",
                        "path": str(root / "capture.png"),
                    }
                ]
            }
            with patch.dict(os.environ, {"ORION_LOCAL_COMPANION_ROOT": str(root)}, clear=False):
                with self.assertRaises(RuntimeError):
                    build_local_execution_pack_result({"run_id": "run-browser-blocked"}, metadata, pack_inputs)

        capture_mock.assert_not_called()

    @patch("scripts.orion_local_worker_execution._run_browser_capture_task")
    def test_local_worker_blocks_session_browser_without_explicit_permission(self, capture_mock):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            metadata = {
                "execution_target": "local_companion",
                "file_mount_grants": default_file_mount_grants(),
            }
            pack_inputs = {
                "operations": [
                    {
                        "tool": "browser_automation",
                        "mode": "capture_page",
                        "url": "https://example.com",
                        "session_profile": "default",
                        "browser_permissions": {"allow": False},
                    }
                ]
            }
            with patch.dict(os.environ, {"ORION_LOCAL_COMPANION_ROOT": str(root)}, clear=False):
                with self.assertRaises(RuntimeError):
                    build_local_execution_pack_result({"run_id": "run-browser-auth-blocked"}, metadata, pack_inputs)

        capture_mock.assert_not_called()

    @patch("scripts.orion_local_worker_execution._run_browser_capture_task")
    def test_local_worker_blocks_session_backed_interactive_browser_actions_without_reviewed_approval(self, capture_mock):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            metadata = {
                "execution_target": "local_companion",
                "file_mount_grants": default_file_mount_grants(),
            }
            pack_inputs = {
                "operations": [
                    {
                        "tool": "browser_automation",
                        "mode": "capture_page",
                        "url": "https://example.com",
                        "session_profile": "default",
                        "browser_permissions": {"allow": True},
                        "browser_actions": [{"action": "click", "selector": "#login"}],
                    }
                ]
            }
            with patch.dict(os.environ, {"ORION_LOCAL_COMPANION_ROOT": str(root)}, clear=False):
                with self.assertRaises(RuntimeError):
                    build_local_execution_pack_result({"run_id": "run-browser-interactive-blocked"}, metadata, pack_inputs)

        capture_mock.assert_not_called()

    @patch("scripts.orion_local_worker_execution._run_browser_capture_task")
    def test_local_worker_allows_session_backed_interactive_browser_actions_with_reviewed_approval(self, capture_mock):
        capture_mock.return_value = {
            "ok": True,
            "title": "Vendor portal",
            "finalUrl": "https://example.com/private",
            "textPreview": "Authenticated page",
            "links": [],
            "downloads": [],
            "actionResults": [{"action": "click", "selector": "#login"}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            metadata = {
                "execution_target": "local_companion",
                "file_mount_grants": default_file_mount_grants(),
                "browser_reviewed_approved": True,
                "browser_immutable_plan_hash": "pending",
            }
            operation = {
                "tool": "browser_automation",
                "mode": "capture_page",
                "url": "https://example.com/private",
                "session_profile": "default",
                "browser_permissions": {"allow": True},
                "browser_actions": [{"action": "click", "selector": "#login"}],
            }
            from server_modules.runtime_policy import browser_automation_plan_hash

            metadata["browser_immutable_plan_hash"] = browser_automation_plan_hash([operation])
            pack_inputs = {"operations": [operation]}
            with patch.dict(os.environ, {"ORION_LOCAL_COMPANION_ROOT": str(root)}, clear=False):
                summary, data = build_local_execution_pack_result({"run_id": "run-browser-approved"}, metadata, pack_inputs)

        self.assertIn("Executed 1 of 1 local operations.", summary)
        self.assertEqual(data["outputs"]["actions"][0]["status"], "completed")
        capture_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
