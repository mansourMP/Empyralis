import asyncio
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from server_modules.agent_turn import build_inbound_agent_turn_request
from server_modules.memory_service import MemoryQuery, query_memory
from server_modules.run_service import transition_live_run_status
from scripts.orion_local_worker_execution import build_local_execution_pack_result


class _FakeSpanContext:
    trace_id = int("1234abcd", 16)


class _FakeSpan:
    def __init__(self, name: str):
        self.name = name
        self.attributes = {}
        self.exceptions = []

    def set_attribute(self, key, value):
        self.attributes[key] = value

    def record_exception(self, exc):
        self.exceptions.append(str(exc))

    def get_span_context(self):
        return _FakeSpanContext()


class _FakeTracer:
    def __init__(self):
        self.spans = []

    @contextmanager
    def start_as_current_span(self, name: str, **_kwargs):
        span = _FakeSpan(name)
        self.spans.append(span)
        yield span


class TelemetryInstrumentationTests(unittest.TestCase):
    def test_agent_turn_emits_canonical_turn_span(self):
        tracer = _FakeTracer()
        turn_request = build_inbound_agent_turn_request(
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            session_id="session-1",
            channel="web",
            actor_type="user",
            actor_id="user-1",
            message="hello",
            execution_mode="sync",
        )

        async def _run():
            with patch("server_modules.agent_turn.get_tracer", return_value=tracer):
                with patch("server_modules.turn_runtime.build_turn_execution_services", return_value={"ok": True}):
                    with patch(
                        "server_modules.turn_runtime.execute_agent_turn_request",
                        return_value={"status": "completed", "run_id": "run-123"},
                    ):
                        return await __import__("server_modules.agent_turn", fromlist=["agent_turn"]).agent_turn(
                            turn_request=turn_request,
                            current_user={"user_id": "user-1"},
                            run_execution_services=object(),
                        )

        result = asyncio.run(_run())

        self.assertEqual(result["run_id"], "run-123")
        span = tracer.spans[0]
        self.assertEqual(span.attributes["workspace_id"], "workspace-1")
        self.assertEqual(span.attributes["session_id"], "session-1")
        self.assertEqual(span.attributes["channel"], "web")
        self.assertEqual(span.attributes["tenant_id"], "tenant-1")
        self.assertEqual(span.attributes["actor_type"], "user")
        self.assertEqual(span.attributes["run_id"], "run-123")
        self.assertEqual(span.attributes["turn_status"], "completed")
        self.assertIn("trace_id", span.attributes)

    def test_transition_live_run_status_emits_state_transition_span(self):
        tracer = _FakeTracer()
        run = {
            "run_id": "run-1",
            "status": "queued",
            "logs": None,
            "context": {
                "workspace_id": "workspace-1",
                "tenant_id": "tenant-1",
                "metadata": {"request_actor_type": "user"},
            },
        }

        with patch("server_modules.run_service.get_tracer", return_value=tracer):
            transition_live_run_status(
                "run-1",
                "executing",
                run=run,
                now_mono=1.0,
                now_iso="2026-04-06T00:00:00Z",
                terminal_statuses={"completed", "failed", "canceled"},
                local_queue_lock=None,
                local_pending_run_ids=[],
                local_claimed_runs={},
                archive_run_if_terminal_fn=lambda *_args, **_kwargs: None,
                remove_live_run_state_fn=lambda *_args, **_kwargs: None,
                sync_local_runtime_state_snapshot_fn=lambda: None,
                persist_live_run_state_fn=lambda *_args, **_kwargs: None,
                run_queue_index={},
                metrics_add_fn=lambda *_args, **_kwargs: None,
                metrics_inc_fn=lambda *_args, **_kwargs: None,
            )

        span = tracer.spans[0]
        self.assertEqual(span.attributes["run_id"], "run-1")
        self.assertEqual(span.attributes["state"], "executing")
        self.assertEqual(span.attributes["previous_state"], "queued")
        self.assertEqual(span.attributes["actor"], "user")
        self.assertEqual(span.attributes["workspace_id"], "workspace-1")
        self.assertEqual(span.attributes["tenant_id"], "tenant-1")
        self.assertIn("trace_id", span.attributes)

    def test_query_memory_emits_memory_span(self):
        tracer = _FakeTracer()
        query = MemoryQuery(
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            text="favorite color",
            metadata={"actor_type": "user", "run_id": "run-1"},
        )

        with patch("server_modules.memory_service.get_tracer", return_value=tracer):
            with patch("server_modules.memory_service.semantic_search", return_value=[{"key": "color", "content": "blue", "score": 0.9}]):
                with patch("server_modules.memory_service.get_memory", return_value="favorite_color: blue"):
                    with patch("server_modules.memory_service.get_recent_logs", return_value=""):
                        result = query_memory(query)

        self.assertEqual(len(result.items), 1)
        span = tracer.spans[0]
        self.assertEqual(span.attributes["memory_type"], "workspace_query")
        self.assertEqual(span.attributes["workspace_id"], "workspace-1")
        self.assertEqual(span.attributes["tenant_id"], "tenant-1")
        self.assertEqual(span.attributes["actor_type"], "user")
        self.assertEqual(span.attributes["run_id"], "run-1")
        self.assertEqual(span.attributes["memory_result_count"], 1)
        self.assertIn("trace_id", span.attributes)

    def test_local_worker_operation_emits_tool_execution_span(self):
        tracer = _FakeTracer()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "notes.txt"
            target.write_text("hello", encoding="utf-8")
            run = {
                "run_id": "run-local-1",
                "context": {"workspace_id": "workspace-1", "tenant_id": "tenant-1"},
            }
            metadata = {
                "execution_target": "local_companion",
                "workspace_id": "workspace-1",
                "tenant_id": "tenant-1",
                "file_mount_grants": [{"mount": "project", "mode": "read"}],
            }
            pack_inputs = {
                "operations": [
                    {
                        "tool": "filesystem.read_write",
                        "mode": "read",
                        "path": "notes.txt",
                    }
                ]
            }

            with patch("scripts.orion_local_worker_execution.get_tracer", return_value=tracer):
                with patch.dict(os.environ, {"ORION_LOCAL_COMPANION_ROOT": str(root)}, clear=False):
                    summary, data = build_local_execution_pack_result(run, metadata, pack_inputs)

        self.assertIn("Executed 1 of 1 local operations.", summary)
        self.assertEqual(data["outputs"]["operations_executed"], 1)
        span = tracer.spans[0]
        self.assertEqual(span.attributes["capability_id"], "filesystem.read_write")
        self.assertEqual(span.attributes["risk_level"], "medium")
        self.assertEqual(span.attributes["run_id"], "run-local-1")
        self.assertEqual(span.attributes["workspace_id"], "workspace-1")
        self.assertEqual(span.attributes["tenant_id"], "tenant-1")
        self.assertEqual(span.attributes["actor_type"], "worker")
        self.assertEqual(span.attributes["tool_status"], "completed")
        self.assertIn("trace_id", span.attributes)
