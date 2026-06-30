import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server_modules import memory_service, session_transcript_store, workspace_context


class SessionTranscriptStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="session-transcript-store-")
        self.addCleanup(self._tmpdir.cleanup)
        tmp_root = Path(self._tmpdir.name)
        self._transcripts_root = tmp_root / "transcripts"
        self._workspace_root = tmp_root / "workspace"
        self._workspace_root.mkdir(parents=True, exist_ok=True)
        self._memory_root = tmp_root / "runtime-memory"

        self._transcript_patch = patch.object(session_transcript_store, "_TRANSCRIPTS_ROOT", self._transcripts_root)
        self._workspace_patch = patch.object(workspace_context, "_WORKSPACE_DIR", self._workspace_root)
        self._memory_patch = patch.object(memory_service._workspace_memory_store, "_MEMORY_DIR", self._memory_root)

        self._transcript_patch.start()
        self._workspace_patch.start()
        self._memory_patch.start()

        self.addCleanup(self._transcript_patch.stop)
        self.addCleanup(self._workspace_patch.stop)
        self.addCleanup(self._memory_patch.stop)

    def test_save_session_transcript_writes_jsonl_and_daily_log(self) -> None:
        result = session_transcript_store.save_session_transcript(
            workspace_id="default",
            thread_id="thread-1",
            provider="openai",
            model="gpt-test",
            messages=[
                {"role": "user", "content": "Earlier request about docs."},
                {"role": "assistant", "content": "Earlier response."},
            ],
            user_message="Continue the runtime refactor.",
            assistant_reply="Moved transcript logging behind the memory service.",
        )

        transcript_path = Path(result["path"])
        self.assertTrue(transcript_path.exists())

        rows = transcript_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(rows), 1)
        payload = json.loads(rows[0])
        self.assertEqual(payload["thread_id"], "thread-1")
        self.assertEqual(payload["provider"], "openai")
        self.assertEqual(payload["model"], "gpt-test")

        recent_logs = memory_service.get_recent_logs("default", days=7)
        self.assertIn("Transcript summary:", recent_logs)
        self.assertIn("Continue the runtime refactor.", recent_logs)
        self.assertIn("Moved transcript logging behind the memory service.", recent_logs)

    def test_save_session_transcript_filters_non_conversation_entries(self) -> None:
        result = session_transcript_store.save_session_transcript(
            workspace_id="default",
            thread_id="thread-1",
            provider="openai",
            model="gpt-test",
            messages=[
                {"role": "system", "content": "Sage boundary: internal prompt."},
                {"role": "user", "content": "/new"},
                {"type": "tool_use", "tool": "search", "input": "secret"},
                {"role": "user", "content": "Real user message."},
                {"role": "assistant", "content": [{"type": "text", "text": "Real assistant reply."}]},
                {
                    "role": "user",
                    "content": "Forwarded internal instruction",
                    "provenance": {"kind": "inter_session", "sourceTool": "sessions_send"},
                },
            ],
            user_message="Real user message.",
            assistant_reply="Real assistant reply.",
        )

        payload = json.loads(Path(result["path"]).read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(
            payload["messages"],
            [
                {"role": "user", "content": "Real user message."},
                {"role": "assistant", "content": "Real assistant reply."},
            ],
        )

        recent_logs = memory_service.get_recent_logs("default", days=7)
        self.assertIn("Real user message.", recent_logs)
        self.assertIn("Real assistant reply.", recent_logs)
        self.assertNotIn("Sage boundary", recent_logs)
        self.assertNotIn("tool_use", recent_logs)
        self.assertNotIn("/new", recent_logs)
        self.assertNotIn("Forwarded internal instruction", recent_logs)

    def test_save_session_transcript_isolates_install_namespace(self) -> None:
        result = session_transcript_store.save_session_transcript(
            workspace_id="default",
            agent_install_id="install-specialist",
            thread_id="thread-2",
            provider="openai",
            model="gpt-test",
            messages=[{"role": "user", "content": "Private specialist context."}],
            user_message="Continue the specialist flow.",
            assistant_reply="Stored inside the specialist transcript namespace.",
        )

        transcript_path = Path(result["path"])
        self.assertTrue(transcript_path.exists())
        self.assertIn("/agents/install-specialist/", transcript_path.as_posix())

        shared_logs = memory_service.get_recent_logs("default", days=7)
        install_logs = memory_service.get_recent_logs("default", days=7, agent_install_id="install-specialist")

        self.assertNotIn("Continue the specialist flow.", shared_logs)
        self.assertIn("Continue the specialist flow.", install_logs)

    def test_save_session_transcript_reports_daily_log_degradation(self) -> None:
        with patch("server_modules.session_transcript_store.save_daily_log", side_effect=RuntimeError("disk full")):
            result = session_transcript_store.save_session_transcript(
                workspace_id="default",
                thread_id="thread-3",
                provider="openai",
                model="gpt-test",
                messages=[{"role": "user", "content": "Hello"}],
                user_message="Hello",
                assistant_reply="Hi",
            )

        self.assertEqual(len(result["degraded_operations"]), 1)
        self.assertEqual(result["degraded_operations"][0]["error_code"], "daily_log_persist_failed")

    def test_list_session_transcript_summaries_logs_parse_degradation(self) -> None:
        transcript_dir = self._transcripts_root / "default"
        transcript_dir.mkdir(parents=True, exist_ok=True)
        (transcript_dir / "2026-04-14.jsonl").write_text("{not-json}\n", encoding="utf-8")

        with patch("server_modules.session_transcript_store.logger.warning") as warning_mock:
            payload = session_transcript_store.list_session_transcript_summaries(workspace_id="default")

        self.assertEqual(payload, [])
        warning_mock.assert_called_once()
        self.assertEqual(warning_mock.call_args.kwargs["extra"]["error_code"], "transcript_parse_failed")

    def test_load_latest_session_transcript_messages_returns_latest_matching_thread(self) -> None:
        session_transcript_store.save_session_transcript(
            workspace_id="default",
            thread_id="thread-1",
            provider="openai",
            model="gpt-test",
            messages=[{"role": "user", "content": "first"}],
            user_message="first",
            assistant_reply="one",
        )
        session_transcript_store.save_session_transcript(
            workspace_id="default",
            thread_id="thread-2",
            provider="openai",
            model="gpt-test",
            messages=[{"role": "user", "content": "other"}],
            user_message="other",
            assistant_reply="two",
        )
        session_transcript_store.save_session_transcript(
            workspace_id="default",
            thread_id="thread-1",
            provider="openai",
            model="gpt-test",
            messages=[
                {"role": "user", "content": "latest question"},
                {"role": "assistant", "content": "latest answer"},
            ],
            user_message="latest question",
            assistant_reply="latest answer",
        )

        messages = session_transcript_store.load_latest_session_transcript_messages(
            workspace_id="default",
            thread_id="thread-1",
        )

        self.assertEqual(
            messages,
            [
                {"role": "user", "content": "latest question"},
                {"role": "assistant", "content": "latest answer"},
            ],
        )

    def test_legacy_fullwidth_dsml_transcript_replay_is_sanitized(self) -> None:
        transcript_dir = self._transcripts_root / "default"
        transcript_dir.mkdir(parents=True, exist_ok=True)
        transcript_payload = {
            "timestamp": "2026-06-11T12:14:00Z",
            "workspace_id": "default",
            "thread_id": "thread-dsml",
            "provider": "deepseek",
            "model": "chat",
            "messages": [
                {"role": "user", "content": "ok check waht things i have !"},
                {
                    "role": "assistant",
                    "content": (
                        "Let me take a look.\n"
                        "<｜｜DSML｜｜tool_calls>"
                        "<｜｜DSML｜｜invoke name=\"bash\">"
                        "<｜｜DSML｜｜parameter name=\"command\" string=\"true\">system_profiler"
                        "</｜｜DSML｜｜parameter>"
                        "</｜｜DSML｜｜invoke>"
                    ),
                },
            ],
        }
        (transcript_dir / "2026-06-11.jsonl").write_text(
            json.dumps(transcript_payload, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        messages = session_transcript_store.load_latest_session_transcript_messages(
            workspace_id="default",
            thread_id="thread-dsml",
        )
        summaries = session_transcript_store.list_session_transcript_summaries(workspace_id="default")
        replay_text = json.dumps({"messages": messages, "summaries": summaries}, ensure_ascii=False)

        self.assertIn("Let me take a look.", replay_text)
        self.assertNotIn("DSML", replay_text)
        self.assertNotIn("tool_calls", replay_text)
        self.assertNotIn("invoke name", replay_text)
        self.assertNotIn("｜｜", replay_text)
        self.assertNotIn("system_profiler", replay_text)


if __name__ == "__main__":
    unittest.main()
