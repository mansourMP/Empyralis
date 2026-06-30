import os
import tempfile
from datetime import datetime, timedelta, timezone
import unittest
from pathlib import Path
from unittest.mock import patch

from server_modules import sage_profile_service, workspace_context


class WorkspaceContextFilesTests(unittest.TestCase):
    def test_extended_memory_markdown_files_round_trip_exact_content(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            with patch("server_modules.workspace_context._WORKSPACE_DIR", Path(tempdir)):
                content = "# Reflection\n\n- Saved exactly.\n"
                saved = workspace_context.write_workspace_context_file(
                    "REFLECTION.md",
                    content,
                    workspace_id="workspace-1",
                )

                self.assertEqual(saved["filename"], "REFLECTION.md")
                self.assertEqual(
                    workspace_context.read_workspace_context_file(
                        "REFLECTION.md",
                        workspace_id="workspace-1",
                    ),
                    content,
                )

    def test_profile_projection_sync_does_not_overwrite_manual_file_edits(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            with patch("server_modules.workspace_context._WORKSPACE_DIR", Path(tempdir)):
                manual_content = "# IDENTITY\n\n- Manually edited file text.\n"
                workspace_context.write_workspace_context_file(
                    "IDENTITY.md",
                    manual_content,
                    workspace_id="workspace-1",
                )

                sage_profile_service.sync_profile_context_files(
                    workspace_id="workspace-1",
                    profile={
                        "identity_summary": "Structured profile text.",
                    },
                )

                self.assertEqual(
                    workspace_context.read_workspace_context_file(
                        "IDENTITY.md",
                        workspace_id="workspace-1",
                    ),
                    manual_content,
                )

    def test_memory_file_whitelist_and_daily_dream_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            with patch("server_modules.workspace_context._WORKSPACE_DIR", Path(tempdir)):
                note_filename = "memory/2026-05-10.md"
                note_content = "# Daily note\n\n- check stock levels.\n"
                dream_filename = "memory/.dreams/notes-2026-05-10.md"
                dream_content = "# Dream cache\n\n- temporary note.\n"

                saved_note = workspace_context.write_workspace_context_file(
                    note_filename,
                    note_content,
                    workspace_id="workspace-1",
                )
                saved_dream = workspace_context.write_workspace_context_file(
                    dream_filename,
                    dream_content,
                    workspace_id="workspace-1",
                )

                files = workspace_context.read_workspace_context_files(workspace_id="workspace-1")

                self.assertEqual(saved_note["filename"], note_filename)
                self.assertEqual(saved_dream["filename"], dream_filename)
                self.assertIn(note_filename, files)
                self.assertIn(dream_filename, files)
                self.assertEqual(files[note_filename], note_content)
                self.assertEqual(files[dream_filename], dream_content)
                self.assertIn("SOUL.md", files)
                self.assertIn("REFLECTION.md", files)

    def test_user_memory_files_round_trip_and_are_listed(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            with patch("server_modules.workspace_context._WORKSPACE_DIR", Path(tempdir)):
                filename = "memory/files/SHOP_PLAYBOOK.md"
                content = "# Shop playbook\n\n- Keep fulfillment notes here.\n"

                saved = workspace_context.write_workspace_context_file(
                    filename,
                    content,
                    workspace_id="workspace-1",
                )
                files = workspace_context.read_workspace_context_files(workspace_id="workspace-1")

                self.assertEqual(saved["filename"], filename)
                self.assertEqual(files[filename], content)

    def test_validate_rejects_illegal_context_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            with patch("server_modules.workspace_context._WORKSPACE_DIR", Path(tempdir)):
                invalid_filenames = [
                    "",
                    "../escape.md",
                    "memory/../escape.md",
                    "/abs/path.md",
                    "memory/2026-13-01.md",
                    "memory/2026-02-30.md",
                    "memory/README.txt",
                    "memory/.md",
                    "memory/.hidden.md",
                    "memory/.dreams",
                    "memory/.dreams/../../foo.md",
                    "memory/.dreams/.keep.md",
                    "memory/files",
                    "memory/files/.hidden.md",
                    "memory/files/../../foo.md",
                    "memory/files/bad/name.md",
                    "memory/files/bad name.md",
                    "notes.txt",
                ]
                for filename in invalid_filenames:
                    with self.subTest(filename=filename):
                        with self.assertRaises(ValueError):
                            workspace_context.write_workspace_context_file(
                                filename,
                                "# test\n",
                                workspace_id="workspace-1",
                            )

    def test_quota_rejects_file_overflow(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            with patch("server_modules.workspace_context._WORKSPACE_DIR", Path(tempdir)):
                too_large = "x" * (workspace_context.MAX_CONTEXT_FILE_BYTES + 1)
                with self.assertRaises(ValueError):
                    workspace_context.write_workspace_context_file(
                        "MEMORY.md",
                        too_large,
                        workspace_id="workspace-1",
                    )

    def test_quota_rejects_scope_overflow(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            with patch("server_modules.workspace_context._WORKSPACE_DIR", Path(tempdir)):
                near_limit_payload = "x" * 10_000
                workspace_id = "workspace-1"
                root = workspace_context.agent_workspace_context_dir(workspace_id=workspace_id)
                start = datetime(2026, 1, 1)

                # Fill the workspace context as much as possible without crossing the scope cap.
                idx = 0
                while workspace_context._current_context_bytes(root) + len(near_limit_payload) <= (
                    workspace_context.MAX_CONTEXT_SCOPE_BYTES - 1
                ):
                    day = start + timedelta(days=idx)
                    workspace_context.write_workspace_context_file(
                        f"memory/{day:%Y-%m-%d}.md",
                        near_limit_payload,
                        workspace_id=workspace_id,
                    )
                    idx += 1

                self.assertGreater(idx, 0)
                with self.assertRaises(ValueError):
                    workspace_context.write_workspace_context_file(
                        f"memory/{(start + timedelta(days=idx)):%Y-%m-%d}.md",
                        near_limit_payload,
                        workspace_id=workspace_id,
                    )

    def test_dream_staging_quota_rejects_more_than_ten_files(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            with patch("server_modules.workspace_context._WORKSPACE_DIR", Path(tempdir)):
                workspace_id = "workspace-1"
                for idx in range(workspace_context.MAX_CONTEXT_DREAM_STAGING_NOTES):
                    workspace_context.write_workspace_context_file(
                        f"memory/.dreams/stage-{idx}.md",
                        "# draft\n",
                        workspace_id=workspace_id,
                    )
                with self.assertRaises(ValueError):
                    workspace_context.write_workspace_context_file(
                        "memory/.dreams/stage-overflow.md",
                        "# draft\n",
                        workspace_id=workspace_id,
                    )

    def test_user_memory_file_quota_rejects_more_than_twenty_files(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            with patch("server_modules.workspace_context._WORKSPACE_DIR", Path(tempdir)):
                workspace_id = "workspace-1"
                for idx in range(workspace_context.MAX_CONTEXT_USER_MEMORY_FILES):
                    workspace_context.write_workspace_context_file(
                        f"memory/files/FILE_{idx}.md",
                        "# memory\n",
                        workspace_id=workspace_id,
                    )
                with self.assertRaises(ValueError):
                    workspace_context.write_workspace_context_file(
                        "memory/files/FILE_OVERFLOW.md",
                        "# memory\n",
                        workspace_id=workspace_id,
                    )

    def test_dream_staging_expired_files_are_pruned_after_seven_days(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            with patch("server_modules.workspace_context._WORKSPACE_DIR", Path(tempdir)):
                workspace_id = "workspace-1"
                workspace_context.write_workspace_context_file(
                    "memory/.dreams/old-proposal.md",
                    "# old draft\n",
                    workspace_id=workspace_id,
                )
                root = workspace_context.agent_workspace_context_dir(workspace_id=workspace_id)
                old_path = root / "memory" / ".dreams" / "old-proposal.md"
                old_time = datetime.now(timezone.utc) - timedelta(days=workspace_context.DREAM_STAGING_TTL_DAYS + 1)
                timestamp = old_time.timestamp()
                os.utime(old_path, (timestamp, timestamp))

                files = workspace_context.read_workspace_context_files(workspace_id=workspace_id)
                self.assertNotIn("memory/.dreams/old-proposal.md", files)

    def test_quota_rejects_daily_note_overflow(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            with patch("server_modules.workspace_context._WORKSPACE_DIR", Path(tempdir)):
                workspace_id = "workspace-1"
                start = datetime(2026, 1, 1)
                for idx in range(workspace_context.MAX_CONTEXT_DAILY_NOTES):
                    day = start + timedelta(days=idx)
                    workspace_context.write_workspace_context_file(
                        f"memory/{day:%Y-%m-%d}.md",
                        "x",
                        workspace_id=workspace_id,
                    )

                with self.assertRaises(ValueError):
                    workspace_context.write_workspace_context_file(
                        "memory/2027-01-01.md",
                        "x",
                        workspace_id=workspace_id,
                    )


if __name__ == "__main__":
    unittest.main()
