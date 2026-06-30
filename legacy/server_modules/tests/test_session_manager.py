import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from server_modules.runtime_state_store import (
    get_runtime_session,
    init_runtime_state_db,
    get_runtime_session_turn,
    list_runtime_session_turns,
    upsert_runtime_session,
    upsert_runtime_session_turn,
)
from server_modules.session_manager.manager import EmpyralisSessionManager


class _DummyBrowser:
    instances = []

    def __init__(self) -> None:
        self.closed = 0
        self.__class__.instances.append(self)

    def close_sync(self) -> None:
        self.closed += 1


def _final_executor(*, session_ctx, message, request_meta):
    yield {"type": "chunk", "delta": "Hello"}
    yield {
        "type": "final",
        "payload": {
            "reply": f"Done: {message}",
            "actions": [],
            "mode": "answer",
            "error": "",
        },
    }


def _history_echo_executor(*, session_ctx, message, request_meta):
    prior_messages = request_meta.get("prior_messages") if isinstance(request_meta, dict) else []
    last_content = ""
    if isinstance(prior_messages, list) and prior_messages:
        last_item = prior_messages[-1]
        if isinstance(last_item, dict):
            last_content = str(last_item.get("content") or "")
    yield {
        "type": "final",
        "payload": {
            "reply": f"prior_count={len(prior_messages or [])};last={last_content}",
            "actions": [],
            "mode": "answer",
            "error": "",
        },
    }


class SessionManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        _DummyBrowser.instances = []
        self._tmpdir = tempfile.TemporaryDirectory(prefix="session-manager-tests-")
        self.db_path = Path(self._tmpdir.name) / "runtime.db"
        init_runtime_state_db(self.db_path)
        self.manager = EmpyralisSessionManager(
            db_path=self.db_path,
            handle_idle_ttl_seconds=1,
            stale_session_seconds=1,
            browser_factory=_DummyBrowser,
        )

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_session_metadata_persists(self):
        self.manager.ensure_session(
            session_id="session-1",
            actor_key="actor-1",
            workspace_id="workspace-a",
            user_id="user-1",
            runtime_options={"cwd": "/tmp"},
            meta={"thread_id": "thread-1"},
        )
        session = get_runtime_session(self.db_path, "session-1")
        self.assertIsNotNone(session)
        self.assertEqual(session["actor_key"], "actor-1")
        self.assertEqual(session["workspace_id"], "workspace-a")
        self.assertEqual(session["user_id"], "user-1")
        self.assertEqual(session["runtime_options"]["cwd"], "/tmp")
        self.assertEqual(session["meta"]["thread_id"], "thread-1")

    def test_turn_row_persists_final_payload(self):
        payload = self.manager.run_turn(
            session_id="session-1",
            actor_key="actor-1",
            workspace_id="workspace-a",
            user_id="user-1",
            message="count functions",
            request_meta={"request_id": "req-1"},
            turn_executor=_final_executor,
        )
        self.assertEqual(payload["reply"], "Done: count functions")
        turns = list_runtime_session_turns(self.db_path, session_id="session-1")
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["request_id"], "req-1")
        self.assertEqual(turns[0]["status"], "completed")
        self.assertEqual(turns[0]["final_payload"]["reply"], "Done: count functions")

    def test_completed_turn_with_same_request_id_replays_without_reexecution(self):
        calls = {"count": 0}

        def counting_executor(*, session_ctx, message, request_meta):
            calls["count"] += 1
            yield from _final_executor(
                session_ctx=session_ctx,
                message=message,
                request_meta=request_meta,
            )

        first = self.manager.run_turn(
            session_id="session-1",
            actor_key="actor-1",
            workspace_id="workspace-a",
            user_id="user-1",
            message="first attempt",
            request_meta={"request_id": "req-1"},
            turn_executor=counting_executor,
        )
        second_events = list(
            self.manager.iter_turn_events(
                session_id="session-1",
                actor_key="actor-1",
                workspace_id="workspace-a",
                user_id="user-1",
                message="retry after edge failure",
                request_meta={"request_id": "req-1"},
                turn_executor=counting_executor,
            )
        )

        self.assertEqual(first["reply"], "Done: first attempt")
        self.assertEqual(calls["count"], 1)
        self.assertEqual(len(second_events), 1)
        self.assertEqual(second_events[0]["type"], "final")
        self.assertEqual(second_events[0]["payload"]["reply"], "Done: first attempt")
        self.assertTrue(second_events[0]["metadata"]["replayed"])

    def test_run_turn_hydrates_prior_messages_from_session_history(self):
        first = self.manager.run_turn(
            session_id="session-1",
            actor_key="actor-1",
            workspace_id="workspace-a",
            user_id="user-1",
            message="first question",
            request_meta={"request_id": "req-1"},
            turn_executor=_final_executor,
        )
        self.assertEqual(first["reply"], "Done: first question")

        second = self.manager.run_turn(
            session_id="session-1",
            actor_key="actor-1",
            workspace_id="workspace-a",
            user_id="user-1",
            message="second question",
            request_meta={"request_id": "req-2"},
            turn_executor=_history_echo_executor,
        )

        self.assertEqual(second["reply"], "prior_count=2;last=Done: first question")

    def test_idle_runtime_handle_evicted(self):
        self.manager.ensure_session(
            session_id="session-1",
            actor_key="actor-1",
            workspace_id="workspace-a",
            user_id="user-1",
        )
        handle = self.manager.runtime_cache.peek("actor-1")
        self.assertIsNotNone(handle)
        old_ts = time.time() - 10
        self.manager.runtime_cache.set("actor-1", handle, now=old_ts)

        evicted = self.manager.evict_idle_handles(now_ts=time.time())

        self.assertEqual(evicted, 1)
        self.assertIsNone(self.manager.runtime_cache.peek("actor-1"))

    def test_browser_handle_closed_on_evict(self):
        self.manager.ensure_session(
            session_id="session-1",
            actor_key="actor-1",
            workspace_id="workspace-a",
            user_id="user-1",
        )
        handle = self.manager.runtime_cache.peek("actor-1")
        self.assertIsNotNone(handle)
        browser = handle.browser
        self.assertIsInstance(browser, _DummyBrowser)
        self.manager.runtime_cache.set("actor-1", handle, now=time.time() - 10)

        self.manager.evict_idle_handles(now_ts=time.time())

        self.assertEqual(browser.closed, 1)

    def test_snapshot_exposes_queue_depth(self):
        first_entered = threading.Event()
        release_first = threading.Event()

        def blocking_executor(*, session_ctx, message, request_meta):
            first_entered.set()
            release_first.wait(timeout=1.0)
            yield {
                "type": "final",
                "payload": {
                    "reply": "done",
                    "actions": [],
                    "mode": "answer",
                    "error": "",
                },
            }

        def worker(request_id: str) -> None:
            self.manager.run_turn(
                session_id="session-1",
                actor_key="actor-1",
                workspace_id="workspace-a",
                user_id="user-1",
                message="do work",
                request_meta={"request_id": request_id},
                turn_executor=blocking_executor,
            )

        first_thread = threading.Thread(target=worker, args=("req-1",))
        second_thread = threading.Thread(target=worker, args=("req-2",))
        first_thread.start()
        self.assertTrue(first_entered.wait(timeout=1.0))
        second_thread.start()

        deadline = time.time() + 1.0
        snapshot = self.manager.get_observability_snapshot()
        while snapshot["turns"]["queue_depth"] < 2 and time.time() < deadline:
            time.sleep(0.02)
            snapshot = self.manager.get_observability_snapshot()

        release_first.set()
        first_thread.join(timeout=1.0)
        second_thread.join(timeout=1.0)

        self.assertGreaterEqual(snapshot["turns"]["queue_depth"], 2)

    def test_interrupt_stale_sessions_marks_running_turns_error(self):
        stale_now = datetime.now(timezone.utc) - timedelta(seconds=10)
        stale_iso = stale_now.isoformat().replace("+00:00", "Z")
        self.manager.ensure_session(
            session_id="session-1",
            actor_key="actor-1",
            workspace_id="workspace-a",
            user_id="user-1",
        )
        upsert_runtime_session(
            self.db_path,
            {
                "session_id": "session-1",
                "actor_key": "actor-1",
                "workspace_id": "workspace-a",
                "user_id": "user-1",
                "status": "active",
                "runtime_options": {},
                "created_at": stale_iso,
                "updated_at": stale_iso,
                "last_touched_at": stale_iso,
                "last_error": "",
                "meta": {},
            },
        )
        upsert_runtime_session_turn(
            self.db_path,
            {
                "turn_id": "turn-1",
                "session_id": "session-1",
                "request_id": "req-1",
                "status": "running",
                "started_at": stale_iso,
                "updated_at": stale_iso,
                "input": {"message": "hello"},
                "metrics": {},
            },
        )

        interrupted = self.manager.interrupt_stale_sessions(now_ts=time.time())

        self.assertEqual(interrupted, 1)
        session = get_runtime_session(self.db_path, "session-1")
        turn = get_runtime_session_turn(self.db_path, "turn-1")
        self.assertIsNotNone(session)
        self.assertIsNotNone(turn)
        self.assertEqual(session["status"], "interrupted")
        self.assertIn("interrupted", session["last_error"].lower())
        self.assertEqual(turn["status"], "error")
        self.assertIn("interrupted", turn["error_text"].lower())


if __name__ == "__main__":
    unittest.main()
