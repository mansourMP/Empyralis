from __future__ import annotations

import importlib
import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from server_modules import control_plane_repository


def _fake_scoped_connection(connection):
    @asynccontextmanager
    async def _manager(*args, **kwargs):
        yield connection

    return _manager


class AgentTraceRepositorySchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        global control_plane_repository
        control_plane_repository = importlib.import_module("server_modules.control_plane_repository")
        self._rust_gate = patch.object(
            control_plane_repository.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={"decision": "allow"},
        )
        self._rust_gate.start()
        self.addCleanup(self._rust_gate.stop)

    def test_schema_sql_includes_trace_tables_and_indexes(self) -> None:
        schema = control_plane_repository.CONTROL_PLANE_SCHEMA_SQL

        for fragment in (
            "CREATE TABLE IF NOT EXISTS agent_traces",
            "CREATE TABLE IF NOT EXISTS agent_trace_events",
            "trace_id TEXT NOT NULL REFERENCES agent_traces(id) ON DELETE CASCADE",
            "root_agent_id TEXT NOT NULL",
            "final_message_id TEXT NULL",
            "payload JSONB NOT NULL DEFAULT '{}'::jsonb",
            "CREATE INDEX IF NOT EXISTS idx_agent_traces_trace_seq ON agent_trace_events(trace_id, seq)",
            "CREATE INDEX IF NOT EXISTS idx_agent_traces_workspace_started ON agent_traces(workspace_id, started_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_agent_traces_run ON agent_traces(run_id)",
            "CREATE INDEX IF NOT EXISTS idx_agent_trace_events_tool_call ON agent_trace_events(tool_call_id)",
            "CREATE INDEX IF NOT EXISTS idx_agent_trace_events_type ON agent_trace_events(event_type)",
        ):
            self.assertIn(fragment, schema)


class AgentTraceRepositoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        global control_plane_repository
        control_plane_repository = importlib.import_module("server_modules.control_plane_repository")

    async def test_create_trace_and_append_event(self) -> None:
        connection = AsyncMock()
        connection.fetchrow = AsyncMock(
            side_effect=[
                {
                    "id": "trace_1",
                    "tenant_id": "tenant-1",
                    "workspace_id": "ws-1",
                    "thread_id": "thread-1",
                    "run_id": "run-1",
                    "root_agent_id": "sage",
                    "surface": "web",
                    "runtime_target": "cloud",
                    "provider": "openai",
                    "model": "gpt-5.4",
                    "started_at": "2026-04-13T12:00:00Z",
                    "finished_at": None,
                    "outcome": None,
                    "final_message_id": None,
                },
                {
                    "id": "tevent_1",
                    "trace_id": "trace_1",
                    "seq": 1,
                    "parent_id": None,
                    "ts": "2026-04-13T12:00:01Z",
                    "event_type": "trace.started",
                    "persisted": True,
                    "agent_id": "sage",
                    "item_id": None,
                    "tool_call_id": None,
                    "child_run_id": None,
                    "approval_id": None,
                    "artifact_id": None,
                    "payload": {"input_mode": "text"},
                },
            ]
        )

        with patch("server_modules.control_plane_repository._scoped_connection", new=_fake_scoped_connection(connection)):
            trace = await control_plane_repository.create_agent_trace(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                thread_id="thread-1",
                run_id="run-1",
                root_agent_id="sage",
                surface="web",
                runtime_target="cloud",
                provider="openai",
                model="gpt-5.4",
                trace_id="trace_1",
            )
            event = await control_plane_repository.append_agent_trace_event(
                trace_id="trace_1",
                tenant_id="tenant-1",
                workspace_id="ws-1",
                seq=1,
                event_type="trace.started",
                persisted=True,
                agent_id="sage",
                payload={"input_mode": "text"},
                event_id="tevent_1",
            )

        self.assertEqual(trace["id"], "trace_1")
        self.assertEqual(trace["root_agent_id"], "sage")
        self.assertEqual(event["id"], "tevent_1")
        self.assertEqual(event["event_type"], "trace.started")
        self.assertEqual(event["payload"]["input_mode"], "text")

    async def test_get_trace_events_orders_by_seq(self) -> None:
        connection = AsyncMock()
        connection.fetch = AsyncMock(
            return_value=[
                {
                    "id": "tevent_1",
                    "trace_id": "trace_1",
                    "seq": 1,
                    "parent_id": None,
                    "ts": "2026-04-13T12:00:01Z",
                    "event_type": "trace.started",
                    "persisted": True,
                    "agent_id": "sage",
                    "item_id": None,
                    "tool_call_id": None,
                    "child_run_id": None,
                    "approval_id": None,
                    "artifact_id": None,
                    "payload": {},
                }
            ]
        )

        with patch("server_modules.control_plane_repository._scoped_connection", new=_fake_scoped_connection(connection)):
            events = await control_plane_repository.get_agent_trace_events(
                "trace_1",
                tenant_id="tenant-1",
                workspace_id="ws-1",
            )

        query = connection.fetch.await_args.args[0]
        self.assertIn("ORDER BY events.seq ASC", query)
        self.assertEqual(events[0]["seq"], 1)

    async def test_get_trace_events_persisted_filter_works(self) -> None:
        connection = AsyncMock()
        connection.fetch = AsyncMock(return_value=[])

        with patch("server_modules.control_plane_repository._scoped_connection", new=_fake_scoped_connection(connection)):
            await control_plane_repository.get_agent_trace_events(
                "trace_1",
                tenant_id="tenant-1",
                workspace_id="ws-1",
                persisted_only=True,
            )

        query = connection.fetch.await_args.args[0]
        self.assertIn("events.persisted = TRUE", query)

    async def test_finish_trace_updates_outcome_and_finished_at(self) -> None:
        connection = AsyncMock()
        connection.fetchrow = AsyncMock(
            return_value={
                "id": "trace_1",
                "tenant_id": "tenant-1",
                "workspace_id": "ws-1",
                "thread_id": "thread-1",
                "run_id": "run-1",
                "root_agent_id": "sage",
                "surface": "web",
                "runtime_target": "cloud",
                "provider": "openai",
                "model": "gpt-5.4",
                "started_at": "2026-04-13T12:00:00Z",
                "finished_at": "2026-04-13T12:00:10Z",
                "outcome": "success",
                "final_message_id": "msg-1",
            }
        )

        with patch("server_modules.control_plane_repository._scoped_connection", new=_fake_scoped_connection(connection)):
            trace = await control_plane_repository.finish_agent_trace(
                "trace_1",
                tenant_id="tenant-1",
                workspace_id="ws-1",
                outcome="success",
                final_message_id="msg-1",
            )

        query = connection.fetchrow.await_args.args[0]
        self.assertIn("SET outcome = $4", query)
        self.assertEqual(trace["outcome"], "success")
        self.assertEqual(trace["final_message_id"], "msg-1")
        self.assertIsNotNone(trace["finished_at"])

    async def test_list_traces_supports_filters(self) -> None:
        connection = AsyncMock()
        connection.fetch = AsyncMock(
            return_value=[
                {
                    "id": "trace_1",
                    "tenant_id": "tenant-1",
                    "workspace_id": "ws-1",
                    "thread_id": "thread-1",
                    "run_id": "run-1",
                    "root_agent_id": "specialist:ainstall_1",
                    "surface": "desktop",
                    "runtime_target": "local",
                    "provider": "ollama",
                    "model": "llama3",
                    "started_at": "2026-04-13T12:00:00Z",
                    "finished_at": None,
                    "outcome": "partial",
                    "final_message_id": None,
                }
            ]
        )

        with patch("server_modules.control_plane_repository._scoped_connection", new=_fake_scoped_connection(connection)):
            traces = await control_plane_repository.list_agent_traces(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                thread_id="thread-1",
                run_id="run-1",
                surface="desktop",
                outcome="partial",
                root_agent_id="specialist:ainstall_1",
            )

        query = connection.fetch.await_args.args[0]
        self.assertIn("thread_id = $3", query)
        self.assertIn("run_id = $4", query)
        self.assertIn("surface = $5", query)
        self.assertIn("outcome = $6", query)
        self.assertIn("root_agent_id = $7", query)
        self.assertEqual(traces[0]["root_agent_id"], "specialist:ainstall_1")
        self.assertEqual(traces[0]["surface"], "desktop")
