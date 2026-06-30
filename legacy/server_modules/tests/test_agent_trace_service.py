from __future__ import annotations

import importlib
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import agent_trace_service


def _trace_row() -> dict[str, object]:
    return {
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
    }


def _event_row() -> dict[str, object]:
    return {
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
    }


class AgentTraceServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        global agent_trace_service
        agent_trace_service = importlib.import_module("server_modules.agent_trace_service")

    async def test_start_trace_creates_repository_row(self) -> None:
        with patch(
            "server_modules.agent_trace_service.control_plane_repository.create_agent_trace",
            new=AsyncMock(return_value=_trace_row()),
        ) as create_trace:
            context = await agent_trace_service.start_trace(
                workspace_id="ws-1",
                tenant_id="tenant-1",
                thread_id="thread-1",
                run_id="run-1",
                surface="web",
                runtime_target="cloud",
                provider="openai",
                model="gpt-5.4",
                root_agent_id="sage",
            )

        self.assertIsNotNone(context)
        assert context is not None
        self.assertEqual(context.trace_id, "trace_1")
        self.assertEqual(context.workspace_id, "ws-1")
        self.assertEqual(context.tenant_id, "tenant-1")
        self.assertEqual(context.root_agent_id, "sage")
        create_trace.assert_awaited_once()

    async def test_emit_persisted_event_writes_repository_row(self) -> None:
        context = agent_trace_service.TraceContext(
            trace_id="trace_1",
            workspace_id="ws-1",
            tenant_id="tenant-1",
            thread_id="thread-1",
            run_id="run-1",
            root_agent_id="sage",
        )
        with patch(
            "server_modules.agent_trace_service.control_plane_repository.append_agent_trace_event",
            new=AsyncMock(return_value=_event_row()),
        ) as append_event, patch(
            "server_modules.agent_trace_service._publish_live_event",
            new=AsyncMock(return_value=None),
        ):
            event_id = await agent_trace_service.emit(
                context,
                "trace.started",
                {"input_mode": "text"},
                persisted=True,
            )

        self.assertTrue(str(event_id or "").startswith("tevent_"))
        append_event.assert_awaited_once()
        self.assertEqual(append_event.await_args.kwargs["seq"], 1)
        self.assertEqual(append_event.await_args.kwargs["event_type"], "trace.started")
        self.assertEqual(append_event.await_args.kwargs["payload"], {"input_mode": "text"})

    async def test_emit_non_persisted_event_does_not_write_repository_row(self) -> None:
        context = agent_trace_service.TraceContext(
            trace_id="trace_1",
            workspace_id="ws-1",
            tenant_id="tenant-1",
            thread_id="thread-1",
            run_id="run-1",
            root_agent_id="sage",
        )
        with patch(
            "server_modules.agent_trace_service.control_plane_repository.append_agent_trace_event",
            new=AsyncMock(return_value=_event_row()),
        ) as append_event, patch(
            "server_modules.agent_trace_service._publish_live_event",
            new=AsyncMock(return_value=None),
        ):
            event_id = await agent_trace_service.emit(
                context,
                "assistant.message.delta",
                {"delta": "hello"},
                persisted=False,
            )

        self.assertTrue(str(event_id or "").startswith("tevent_"))
        append_event.assert_not_awaited()
        self.assertEqual(context.next_seq(), 2)

    async def test_finish_trace_updates_outcome(self) -> None:
        context = agent_trace_service.TraceContext(
            trace_id="trace_1",
            workspace_id="ws-1",
            tenant_id="tenant-1",
            thread_id="thread-1",
            run_id="run-1",
            root_agent_id="sage",
        )
        with patch(
            "server_modules.agent_trace_service.control_plane_repository.finish_agent_trace",
            new=AsyncMock(return_value={**_trace_row(), "outcome": "success", "finished_at": "2026-04-13T12:00:10Z"}),
        ) as finish_trace:
            row = await agent_trace_service.finish_trace(
                context,
                outcome="success",
                final_message_id="msg-1",
            )

        self.assertEqual((row or {}).get("outcome"), "success")
        finish_trace.assert_awaited_once()
        self.assertEqual(finish_trace.await_args.kwargs["outcome"], "success")
        self.assertEqual(finish_trace.await_args.kwargs["final_message_id"], "msg-1")

    async def test_get_trace_replay_returns_trace_and_persisted_events(self) -> None:
        with patch(
            "server_modules.agent_trace_service.control_plane_repository.get_workspace_by_id",
            new=AsyncMock(return_value={"tenant_id": "tenant-1", "workspace_id": "ws-1"}),
        ), patch(
            "server_modules.agent_trace_service.control_plane_repository.get_agent_trace_by_id",
            new=AsyncMock(return_value=_trace_row()),
        ) as get_trace, patch(
            "server_modules.agent_trace_service.control_plane_repository.get_agent_trace_events",
            new=AsyncMock(return_value=[_event_row()]),
        ) as get_events:
            replay = await agent_trace_service.get_trace_replay("trace_1", "ws-1")

        self.assertEqual((replay.get("trace") or {}).get("id"), "trace_1")
        self.assertEqual(len(replay.get("events") or []), 1)
        get_trace.assert_awaited_once()
        get_events.assert_awaited_once()
        self.assertTrue(get_events.await_args.kwargs["persisted_only"])

    async def test_typed_helpers_emit_expected_event_types(self) -> None:
        context = agent_trace_service.TraceContext(
            trace_id="trace_1",
            workspace_id="ws-1",
            tenant_id="tenant-1",
            thread_id="thread-1",
            run_id="run-1",
            root_agent_id="sage",
        )
        cases = [
            ("emit_trace_started", ("text",), "trace.started", True),
            ("emit_trace_routed", ("sync", "cloud", "openai", "gpt-5.4", "Fast path"), "trace.routed", True),
            ("emit_plan_started", ("plan-1", "Plan", "Summary"), "plan.started", True),
            ("emit_plan_item", ("plan-1", "item-1", 1, "Search docs", "tool", "sage", ["item-0"], "Need context"), "plan.item.created", True),
            ("emit_plan_item_updated", ("item-1", "running", "Now executing"), "plan.item.updated", True),
            ("emit_plan_replanned", ("plan-2", "plan-1", "New info"), "plan.replanned", True),
            ("emit_tool_started", ("item-1", "tool-1", "web_search", "cap.search", "web", {"q": "empyralist"}), "tool.started", True),
            ("emit_tool_progress", ("tool-1", "Searching", 25), "tool.progress", False),
            ("emit_tool_result", ("tool-1", "ok", "Found results", ["artifact-1"]), "tool.result", True),
            ("emit_search_query", ("tool-1", "web", "empyralist", {"region": "us"}), "search.query", True),
            ("emit_search_results", ("tool-1", [{"title": "Home", "url": "https://example.com"}]), "search.results", True),
            ("emit_browser_action", ("tool-1", "open", "Pricing page", "https://example.com"), "browser.action", True),
            ("emit_browser_screenshot", ("tool-1", "artifact-1", "Opened page", 1280, 720), "browser.screenshot", True),
            ("emit_delegation_started", ("item-1", "child-1", "specialist-1", "Researcher", "Gather sources"), "delegation.started", True),
            ("emit_delegation_finished", ("item-1", "child-1", "ok", "Done"), "delegation.finished", True),
            ("emit_approval_requested", ("approval-1", "tool", "Need approval", "External write", "item-1"), "approval.requested", True),
            ("emit_approval_resolved", ("approval-1", "approved", "user", "Looks good"), "approval.resolved", True),
            ("emit_artifact_created", ("artifact-1", "report", "Findings", "text/markdown"), "artifact.created", True),
            ("emit_reasoning_summary_delta", ("item-1", "Looking for the best source"), "reasoning.summary.delta", False),
            ("emit_assistant_message_delta", ("msg-1", "Hello"), "assistant.message.delta", False),
            ("emit_assistant_message_completed", ("msg-1", "Done", ["cite-1"], ["artifact-1"]), "assistant.message.completed", True),
            ("emit_trace_completed", (1250, "msg-1"), "trace.completed", True),
            ("emit_trace_failed", ("runtime_error", "Boom", True, "item-1"), "trace.failed", True),
        ]

        with patch("server_modules.agent_trace_service.emit", new=AsyncMock(return_value="tevent_1")) as emit_mock:
            for helper_name, helper_args, expected_event_type, expected_persisted in cases:
                emit_mock.reset_mock()
                helper = getattr(agent_trace_service, helper_name)
                with self.subTest(helper=helper_name):
                    await helper(context, *helper_args)
                    emit_mock.assert_awaited_once()
                    self.assertEqual(emit_mock.await_args.args[1], expected_event_type)
                    self.assertEqual(emit_mock.await_args.kwargs.get("persisted"), expected_persisted)

    async def test_tool_result_can_include_runtime_correlation_metadata(self) -> None:
        context = agent_trace_service.TraceContext(
            trace_id="trace_1",
            workspace_id="ws-1",
            tenant_id="tenant-1",
            thread_id="thread-1",
            run_id="run-1",
            root_agent_id="sage",
        )
        with patch("server_modules.agent_trace_service.emit", new=AsyncMock(return_value="tevent_1")) as emit_mock:
            await agent_trace_service.emit_tool_result(
                context,
                "tool-1",
                "completed",
                "Ran command.",
                ["artifact-1"],
                tool_name="shell.execute",
                capability_id="shell.execute",
                connector_id="hardware_runtime",
                args_preview={"command": "uptime"},
                runtime_session_id="hrs-1",
                runtime_target="self_hosted_node",
                request_id="req-1",
                action_id="shell.exec",
                metadata={"runtime_access_mode": "full_access"},
            )

        data = emit_mock.await_args.args[2]
        self.assertEqual(data["tool_name"], "shell.execute")
        self.assertEqual(data["capability_id"], "shell.execute")
        self.assertEqual(data["connector_id"], "hardware_runtime")
        self.assertEqual(data["args_preview"]["command"], "uptime")
        self.assertEqual(data["runtime_session_id"], "hrs-1")
        self.assertEqual(data["runtime_target"], "self_hosted_node")
        self.assertEqual(data["request_id"], "req-1")
        self.assertEqual(data["action_id"], "shell.exec")
        self.assertEqual(data["metadata"]["runtime_access_mode"], "full_access")
        self.assertEqual(emit_mock.await_args.kwargs.get("persisted"), True)

    async def test_service_never_raises_if_repository_fails(self) -> None:
        context = agent_trace_service.TraceContext(
            trace_id="trace_1",
            workspace_id="ws-1",
            tenant_id="tenant-1",
            thread_id="thread-1",
            run_id="run-1",
            root_agent_id="sage",
        )
        with patch(
            "server_modules.agent_trace_service.control_plane_repository.create_agent_trace",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ), patch(
            "server_modules.agent_trace_service.control_plane_repository.append_agent_trace_event",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ), patch(
            "server_modules.agent_trace_service.control_plane_repository.finish_agent_trace",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ), patch(
            "server_modules.agent_trace_service.control_plane_repository.get_workspace_by_id",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ), patch(
            "server_modules.agent_trace_service.failure_policy_service.log_degraded_operation",
        ):
            started = await agent_trace_service.start_trace(
                workspace_id="ws-1",
                tenant_id="tenant-1",
                thread_id="thread-1",
                run_id="run-1",
                surface="web",
                runtime_target="cloud",
                provider="openai",
                model="gpt-5.4",
                root_agent_id="sage",
            )
            emitted = await agent_trace_service.emit(context, "trace.started", {"input_mode": "text"}, persisted=True)
            finished = await agent_trace_service.finish_trace(context, outcome="success", final_message_id="msg-1")
            replay = await agent_trace_service.get_trace_replay("trace_1", "ws-1")

        self.assertIsNone(started)
        self.assertIsNone(emitted)
        self.assertIsNone(finished)
        self.assertEqual(replay, {"trace": None, "events": []})

    async def test_repository_failures_emit_typed_observability_notice(self) -> None:
        with patch(
            "server_modules.agent_trace_service.control_plane_repository.create_agent_trace",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ), patch(
            "server_modules.agent_trace_service.failure_policy_service.log_degraded_operation",
        ) as degraded_mock:
            started = await agent_trace_service.start_trace(
                workspace_id="ws-1",
                tenant_id="tenant-1",
                thread_id="thread-1",
                run_id="run-1",
                surface="web",
                runtime_target="cloud",
                provider="openai",
                model="gpt-5.4",
                root_agent_id="sage",
            )

        self.assertIsNone(started)
        degraded_mock.assert_called_once()
        self.assertEqual(degraded_mock.call_args.kwargs["error_class"], "observability_failure")
        self.assertEqual(degraded_mock.call_args.kwargs["code"], "agent_trace_start_trace_failed")
