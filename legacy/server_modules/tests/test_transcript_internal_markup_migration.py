import json
import tempfile
import unittest
from pathlib import Path

from server_modules import transcript_internal_markup_migration as migration


class TranscriptInternalMarkupMigrationTests(unittest.TestCase):
    def test_migration_backs_up_before_sanitizing_assistant_visible_content(self) -> None:
        with tempfile.TemporaryDirectory(prefix="transcript-markup-migration-") as tmp:
            root = Path(tmp)
            transcripts_root = root / "transcripts"
            backup_root = root / "backups"
            report_path = root / "reports" / "transcript_migration_report.json"
            transcript_path = transcripts_root / "ws-1" / "2026-06-11.jsonl"
            transcript_path.parent.mkdir(parents=True, exist_ok=True)
            original_payload = {
                "timestamp": "2026-06-11T12:14:00Z",
                "workspace_id": "ws-1",
                "thread_id": "thread-1",
                "provider": "deepseek",
                "model": "chat",
                "messages": [
                    {"role": "user", "content": "ok check waht things i have !"},
                    {
                        "role": "assistant",
                        "content": (
                            "Let me check.\n"
                            "<｜｜DSML｜｜tool_calls>"
                            "<｜｜DSML｜｜invoke name=\"bash\">"
                            "<｜｜DSML｜｜parameter name=\"command\" string=\"true\">system_profiler"
                            "</｜｜DSML｜｜parameter>"
                            "</｜｜DSML｜｜invoke>"
                        ),
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "name": "shell__exec",
                                "arguments": "{\"command\":\"system_profiler\"}",
                            }
                        ],
                    },
                ],
            }
            original_text = json.dumps(original_payload, ensure_ascii=False) + "\n"
            transcript_path.write_text(original_text, encoding="utf-8")

            report = migration.migrate_transcripts(
                transcripts_root=transcripts_root,
                backup_root=backup_root,
                report_path=report_path,
            )

            backup_path = backup_root / "ws-1" / "2026-06-11.jsonl"
            self.assertTrue(backup_path.exists())
            self.assertEqual(backup_path.read_text(encoding="utf-8"), original_text)
            self.assertTrue(report_path.exists())
            self.assertEqual(report["changed_file_count"], 1)
            self.assertEqual(report["changed_files"][0]["backup_path"], str(backup_path))

            sanitized_payload = json.loads(transcript_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(sanitized_payload["timestamp"], original_payload["timestamp"])
            self.assertEqual(sanitized_payload["workspace_id"], original_payload["workspace_id"])
            self.assertEqual(sanitized_payload["thread_id"], original_payload["thread_id"])
            self.assertEqual(sanitized_payload["provider"], original_payload["provider"])
            self.assertEqual(sanitized_payload["model"], original_payload["model"])
            sanitized_text = json.dumps(sanitized_payload, ensure_ascii=False)
            self.assertIn("Let me check.", sanitized_text)
            self.assertNotIn("DSML", sanitized_text)
            self.assertNotIn("tool_calls", sanitized_text)
            self.assertNotIn("call-1", sanitized_text)
            self.assertNotIn("invoke name", sanitized_text)
            self.assertNotIn("system_profiler", sanitized_text)
            self.assertNotIn("｜｜", sanitized_text)

    def test_dry_run_does_not_write_backup_or_change_transcript(self) -> None:
        with tempfile.TemporaryDirectory(prefix="transcript-markup-migration-") as tmp:
            root = Path(tmp)
            transcripts_root = root / "transcripts"
            transcript_path = transcripts_root / "ws-1" / "2026-06-11.jsonl"
            transcript_path.parent.mkdir(parents=True, exist_ok=True)
            original_text = json.dumps(
                {
                    "timestamp": "2026-06-11T12:14:00Z",
                    "workspace_id": "ws-1",
                    "thread_id": "thread-1",
                    "provider": "deepseek",
                    "model": "chat",
                    "messages": [
                        {"role": "assistant", "content": "Visible\n<|｜DSML｜|tool_calls>"},
                    ],
                },
                ensure_ascii=False,
            ) + "\n"
            transcript_path.write_text(original_text, encoding="utf-8")

            report = migration.migrate_transcripts(
                transcripts_root=transcripts_root,
                backup_root=root / "backups",
                report_path=root / "reports" / "transcript_migration_report.json",
                dry_run=True,
            )

            self.assertEqual(report["changed_file_count"], 1)
            self.assertFalse((root / "backups").exists())
            self.assertFalse((root / "reports").exists())
            self.assertEqual(transcript_path.read_text(encoding="utf-8"), original_text)


if __name__ == "__main__":
    unittest.main()
