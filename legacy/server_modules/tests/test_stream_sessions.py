from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server_modules import runtime_runs_api
from server_modules.runtime_state_store import (
    _parse_ts,
    get_chat_stream_state,
    init_runtime_state_db,
    list_chat_stream_states,
    upsert_chat_stream_state,
)


class StreamSessionStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="stream-state-")
        self.db_path = Path(self._tmpdir.name) / "runtime.db"
        init_runtime_state_db(self.db_path)
        self._db_patcher = patch.object(runtime_runs_api, "_chat_stream_state_db_path", return_value=self.db_path)
        self._db_patcher.start()
        with runtime_runs_api._CHAT_STREAM_LOCK:
            runtime_runs_api._CHAT_STREAM_SESSIONS.clear()

    def tearDown(self) -> None:
        with runtime_runs_api._CHAT_STREAM_LOCK:
            runtime_runs_api._CHAT_STREAM_SESSIONS.clear()
        self._db_patcher.stop()
        self._tmpdir.cleanup()

    def test_stream_session_persists_to_sqlite(self) -> None:
        session = runtime_runs_api._get_or_create_chat_stream_session(
            "user:thread:req",
            thread_id="thread",
            request_id="req",
            workspace_id="default",
        )

        runtime_runs_api._append_chat_stream_event(session, "chunk", {"delta": "Hello"})
        runtime_runs_api._append_chat_stream_event(session, "final", {"reply": "Done", "actions": [], "mode": "answer"})

        persisted = get_chat_stream_state(self.db_path, "user:thread:req")

        self.assertIsNotNone(persisted)
        assert persisted is not None
        self.assertEqual(persisted["status"], "completed")
        self.assertEqual(persisted["last_event_seq"], 2)
        self.assertEqual(persisted["partial_text"], "Hello")
        self.assertEqual(persisted["final_payload"]["reply"], "Done")

    def test_completed_session_replays_final_after_simulated_restart(self) -> None:
        session = runtime_runs_api._get_or_create_chat_stream_session(
            "user:thread:req",
            thread_id="thread",
            request_id="req",
            workspace_id="default",
        )
        runtime_runs_api._append_chat_stream_event(session, "final", {"reply": "Done", "actions": [], "mode": "answer"})

        with runtime_runs_api._CHAT_STREAM_LOCK:
            runtime_runs_api._CHAT_STREAM_SESSIONS.clear()

        replay_session = runtime_runs_api._get_or_create_chat_stream_session(
            "user:thread:req",
            thread_id="thread",
            request_id="req",
            workspace_id="default",
        )

        payload = b"".join(runtime_runs_api._iter_chat_stream_events(replay_session, "0")).decode("utf-8")

        self.assertTrue(replay_session["producer_started"])
        self.assertTrue(replay_session["completed"])
        self.assertIn("event: final", payload)
        self.assertIn('"reply": "Done"', payload)

    def test_active_session_restarts_after_simulated_restart(self) -> None:
        session = runtime_runs_api._get_or_create_chat_stream_session(
            "user:thread:req-active",
            thread_id="thread",
            request_id="req-active",
            workspace_id="default",
        )
        runtime_runs_api._append_chat_stream_event(session, "chunk", {"delta": "Hello"})

        with runtime_runs_api._CHAT_STREAM_LOCK:
            runtime_runs_api._CHAT_STREAM_SESSIONS.clear()

        replay_session = runtime_runs_api._get_or_create_chat_stream_session(
            "user:thread:req-active",
            thread_id="thread",
            request_id="req-active",
            workspace_id="default",
        )
        persisted = get_chat_stream_state(self.db_path, "user:thread:req-active")

        self.assertIsNotNone(persisted)
        assert persisted is not None
        self.assertEqual(persisted["status"], "active")
        self.assertFalse(replay_session["completed"])
        self.assertEqual(replay_session["status"], "active")
        self.assertEqual(replay_session["events"], [])

    def test_stale_sessions_marked_interrupted_on_boot(self) -> None:
        upsert_chat_stream_state(
            self.db_path,
            {
                "session_id": "stale-session",
                "thread_id": "thread",
                "request_id": "req",
                "workspace_id": "default",
                "status": "active",
                "created_at": "2026-04-02T00:00:00Z",
                "updated_at": "2026-04-02T00:00:00Z",
                "last_event_seq": 1,
                "partial_text": "partial",
                "metadata": {},
            },
        )

        runtime_runs_api.initialize_chat_stream_runtime_state(now_ts=_parse_ts("2026-04-02T00:20:01Z"))

        persisted = get_chat_stream_state(self.db_path, "stale-session")

        self.assertIsNotNone(persisted)
        assert persisted is not None
        self.assertEqual(persisted["status"], "interrupted")
        self.assertEqual(persisted["last_event_seq"], 2)
        self.assertIn("interrupted", persisted["error_text"].lower())

    def test_session_cleaned_up_after_ttl(self) -> None:
        upsert_chat_stream_state(
            self.db_path,
            {
                "session_id": "expired-session",
                "thread_id": "thread",
                "request_id": "req",
                "workspace_id": "default",
                "status": "completed",
                "created_at": "2026-04-02T00:00:00Z",
                "updated_at": "2026-04-02T00:00:00Z",
                "last_event_seq": 2,
                "partial_text": "Done",
                "final_payload": {"reply": "Done", "actions": [], "mode": "answer"},
                "metadata": {},
            },
        )

        runtime_runs_api.initialize_chat_stream_runtime_state(now_ts=_parse_ts("2026-04-02T02:00:01Z"))

        sessions = list_chat_stream_states(self.db_path)
        self.assertEqual(sessions, [])

    def test_multi_worker_guard_fails_fast(self) -> None:
        with patch.dict("os.environ", {"WEB_CONCURRENCY": "2"}, clear=False):
            with self.assertRaises(RuntimeError) as ctx:
                runtime_runs_api.ensure_single_worker_direct_chat_stream_runtime()

        self.assertIn("_CHAT_STREAM_SESSIONS", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
