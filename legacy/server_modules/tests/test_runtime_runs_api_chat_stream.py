import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

from server_modules import runtime_runs_api
from server_modules.runtime_state_store import init_runtime_state_db


class RuntimeRunsApiChatStreamTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="chat-stream-tests-")
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

    def test_iter_chat_stream_events_replays_only_events_after_cursor(self):
        session = runtime_runs_api._get_or_create_chat_stream_session(
            "user:thread:req",
            thread_id="thread",
            request_id="req",
            workspace_id="default",
        )
        runtime_runs_api._append_chat_stream_event(session, "chunk", {"delta": "Hello"})
        runtime_runs_api._append_chat_stream_event(session, "step", {"label": "Thinking", "status": "active"})
        runtime_runs_api._append_chat_stream_event(session, "final", {"reply": "Done"})

        payload = b"".join(runtime_runs_api._iter_chat_stream_events(session, "1")).decode("utf-8")

        self.assertNotIn("id: 1\n", payload)
        self.assertIn("id: 2\n", payload)
        self.assertIn("event: step\n", payload)
        self.assertIn("id: 3\n", payload)
        self.assertIn("event: final\n", payload)

    def test_final_stream_event_persists_assistant_turn_content(self):
        captured = {}

        async def _record_assistant_turn(**kwargs):
            captured.update(kwargs)
            return {"ok": True}

        session = runtime_runs_api._get_or_create_chat_stream_session(
            "user:thread:req",
            thread_id="thread",
            request_id="req",
            workspace_id="default",
        )
        session["metadata"] = {
            "assistant_turn": {
                "tenant_id": "tenant-1",
                "workspace_id": "default",
                "thread_id": "thread",
                "session_id": "thread",
                "request_id": "req",
                "active_agent_install_id": "agent-1",
                "runtime_profile_id": "runtime-1",
            }
        }

        with patch.object(runtime_runs_api.thread_service, "record_assistant_turn", _record_assistant_turn):
            runtime_runs_api._append_chat_stream_event(
                session,
                "trace",
                {
                    "trace_id": "trace-1",
                    "event_type": "reasoning.summary.delta",
                    "data": {"delta": "hidden reasoning"},
                },
            )
            runtime_runs_api._append_chat_stream_event(
                session,
                "trace",
                {
                    "trace_id": "trace-1",
                    "event_type": "search.query",
                    "tool_call_id": "search-1",
                    "data": {"query": "gateway state"},
                },
            )
            runtime_runs_api._append_chat_stream_event(
                session,
                "trace",
                {
                    "trace_id": "trace-1",
                    "event_type": "tool.started",
                    "tool_call_id": "shell-1",
                    "data": {
                        "tool_name": "shell__exec",
                        "input": {"command": "cat ~/.ssh/config"},
                        "args_preview": {"command": "cat ~/.ssh/config"},
                    },
                },
            )
            runtime_runs_api._append_chat_stream_event(session, "chunk", {"delta": "Done"})
            runtime_runs_api._append_chat_stream_event(
                session,
                "final",
                {
                    "reply": "Done",
                    "actions": [],
                    "mode": "answer",
                    "metadata": {"agent_role": "sage", "trace_id": "trace-1"},
                },
            )

        self.assertEqual(captured["thread_id"], "thread")
        self.assertEqual(captured["tenant_id"], "tenant-1")
        self.assertEqual(captured["workspace_id"], "default")
        self.assertEqual(captured["reply"], "Done")
        self.assertEqual(captured["status"], "completed")
        self.assertEqual(captured["active_agent_install_id"], "agent-1")
        self.assertEqual(captured["runtime_profile_id"], "runtime-1")
        self.assertEqual(captured["metadata"]["request_id"], "req")
        self.assertEqual(captured["metadata"]["trace_id"], "trace-1")
        self.assertEqual(captured["metadata"]["result_metadata"]["agent_role"], "sage")
        transcript_events = captured["metadata"]["transcript_events"]
        self.assertEqual([item["payload"]["event_type"] for item in transcript_events], ["search.query", "tool.started"])
        transcript_snapshot = json.dumps(transcript_events)
        self.assertNotIn("hidden reasoning", transcript_snapshot)
        self.assertNotIn("cat ~/.ssh/config", transcript_snapshot)
        self.assertNotIn("args_preview", transcript_snapshot)
        self.assertNotIn("chunk", transcript_snapshot)
        self.assertNotIn("final", transcript_snapshot)

    def test_final_stream_event_does_not_persist_reasoning_metadata(self):
        captured = {}

        async def _record_assistant_turn(**kwargs):
            captured.update(kwargs)
            return {"ok": True}

        session = runtime_runs_api._get_or_create_chat_stream_session(
            "user:thread:req-reasoning",
            thread_id="thread",
            request_id="req-reasoning",
            workspace_id="default",
        )
        session["metadata"] = {
            "assistant_turn": {
                "tenant_id": "tenant-1",
                "workspace_id": "default",
                "thread_id": "thread",
                "session_id": "thread",
                "request_id": "req-reasoning",
            }
        }

        with patch.object(runtime_runs_api.thread_service, "record_assistant_turn", _record_assistant_turn):
            runtime_runs_api._append_chat_stream_event(
                session,
                "final",
                {
                    "reply": "Done",
                    "metadata": {
                        "agent_role": "sage",
                        "trace_id": "trace-1",
                        "reasoning": "private reasoning",
                        "reasoning_effort": "high",
                        "context_used": {
                            "provider": "openai",
                            "reasoning_summary": "internal reasoning",
                            "chain_of_thought": "more private",
                        },
                    },
                    "result": {
                        "metadata": {
                            "thinking": "should be dropped",
                            "raw_chain_of_thought": "secret",
                        },
                    },
                },
            )

        result_metadata = captured["metadata"]["result_metadata"]
        self.assertEqual(captured["metadata"]["request_id"], "req-reasoning")
        self.assertEqual(result_metadata["agent_role"], "sage")
        self.assertNotIn("reasoning", result_metadata)
        self.assertNotIn("thinking", result_metadata)
        self.assertNotIn("reasoning_summary", result_metadata["context_used"])
        self.assertNotIn("chain_of_thought", result_metadata["context_used"])
        self.assertEqual(result_metadata["reasoning_effort"], "high")

    def test_cached_transcript_events_for_final_turn_are_sanitized_before_persist(self):
        captured = {}

        async def _record_assistant_turn(**kwargs):
            captured.update(kwargs)
            return {"ok": True}

        session = runtime_runs_api._get_or_create_chat_stream_session(
            "user:thread:req-cache",
            thread_id="thread",
            request_id="req-cache",
            workspace_id="default",
        )
        session["metadata"] = {
            "assistant_turn": {
                "tenant_id": "tenant-1",
                "workspace_id": "default",
                "thread_id": "thread",
                "session_id": "thread",
                "request_id": "req-cache",
                "active_agent_install_id": "agent-1",
                "runtime_profile_id": "runtime-1",
            },
            "transcript_events": [
                {
                    "event": "trace",
                    "schema_version": 1,
                    "payload": {
                        "event_type": "reasoning.summary.delta",
                        "data": {"delta": "hidden cached reasoning"},
                    },
                },
                {
                    "event": "trace",
                    "schema_version": 1,
                    "payload": {
                        "event_type": "tool.started",
                        "tool_call_id": "shell-1",
                        "data": {
                            "tool_name": "shell__exec",
                            "input": {"command": "cat /etc/hosts"},
                            "arguments": {"command": "cat /etc/hosts"},
                        },
                    },
                },
                {
                    "event": "step",
                    "schema_version": 1,
                    "payload": {
                        "kind": "assistant",
                        "id": "thinking-1",
                        "label": "Thinking",
                        "detail": "private thinking",
                        "status": "done",
                    },
                },
            ],
        }

        with patch.object(runtime_runs_api.thread_service, "record_assistant_turn", _record_assistant_turn):
            runtime_runs_api._append_chat_stream_event(session, "final", {
                "reply": "Done",
                "mode": "answer",
                "metadata": {"agent_role": "sage", "trace_id": "trace-1"},
            })

        self.assertEqual(captured["metadata"]["trace_id"], "trace-1")
        self.assertNotIn("thinking", json.dumps(captured["metadata"]))
        transcript_events = captured["metadata"].get("transcript_events", [])
        self.assertEqual([item["payload"]["event_type"] for item in transcript_events], ["tool.started"])
        transcript_snapshot = json.dumps(transcript_events)
        self.assertNotIn("hidden cached reasoning", transcript_snapshot)
        self.assertNotIn("cat /etc/hosts", transcript_snapshot)
        self.assertNotIn("arguments", transcript_snapshot)

    def test_final_stream_event_persists_step_proof_rows(self):
        captured = {}

        async def _record_assistant_turn(**kwargs):
            captured.update(kwargs)
            return {"ok": True}

        session = runtime_runs_api._get_or_create_chat_stream_session(
            "user:thread:req-step",
            thread_id="thread",
            request_id="req-step",
            workspace_id="default",
        )
        session["metadata"] = {
            "assistant_turn": {
                "tenant_id": "tenant-1",
                "workspace_id": "default",
                "thread_id": "thread",
                "session_id": "thread",
                "request_id": "req-step",
            }
        }

        with patch.object(runtime_runs_api.thread_service, "record_assistant_turn", _record_assistant_turn):
            runtime_runs_api._append_chat_stream_event(
                session,
                "step",
                {
                    "id": "direct-tools:explicit",
                    "kind": "tool",
                    "label": "Using direct tools",
                    "detail": "Running explicit tool request",
                    "status": "active",
                },
            )
            runtime_runs_api._append_chat_stream_event(
                session,
                "step",
                {
                    "id": "direct-tools:explicit:1",
                    "kind": "connector",
                    "label": "Search",
                    "detail": "Web",
                    "status": "done",
                },
            )
            runtime_runs_api._append_chat_stream_event(
                session,
                "final",
                {
                    "reply": "Done",
                    "actions": [],
                    "mode": "answer",
                },
            )

        transcript_events = captured["metadata"]["transcript_events"]
        self.assertEqual([item["event"] for item in transcript_events], ["step", "step"])
        self.assertEqual(transcript_events[0]["payload"]["kind"], "tool")
        self.assertEqual(transcript_events[1]["payload"]["kind"], "connector")
        self.assertEqual(transcript_events[1]["payload"]["label"], "Search")

    def test_final_stream_event_does_not_persist_notice_as_assistant_content(self):
        calls = []

        async def _record_assistant_turn(**kwargs):
            calls.append(kwargs)
            return {"ok": True}

        session = runtime_runs_api._get_or_create_chat_stream_session(
            "user:thread:req",
            thread_id="thread",
            request_id="req",
            workspace_id="default",
        )
        session["metadata"] = {
            "assistant_turn": {
                "tenant_id": "tenant-1",
                "workspace_id": "default",
                "thread_id": "thread",
                "session_id": "thread",
                "request_id": "req",
            }
        }

        with patch.object(runtime_runs_api.thread_service, "record_assistant_turn", _record_assistant_turn):
            runtime_runs_api._append_chat_stream_event(
                session,
                "final",
                {
                    "reply": "",
                    "mode": "connect",
                    "interventions": [
                        {
                            "kind": "connect_required",
                            "code": "local_setup_required",
                            "detail": "Connect My Computer in Integrations before Sage uses local computer actions.",
                        }
                    ],
                },
            )

        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
