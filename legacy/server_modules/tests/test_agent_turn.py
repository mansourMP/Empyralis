import unittest
import asyncio
import importlib
from unittest.mock import AsyncMock, patch

from server_modules import agent_trace_service
from server_modules.agent_turn import (
    AgentTurnRequest,
    agent_turn,
    bind_agent_turn_request_meta,
    bind_agent_turn_metadata,
    build_agent_turn_request,
    build_agent_turn_session_context,
    build_direct_chat_turn_request,
    build_discord_turn_request,
    build_inbound_agent_turn_request,
    build_local_worker_turn_request,
    build_telegram_turn_request,
    build_whatsapp_turn_request,
    resolve_agent_turn_session_identity,
    build_run_start_turn_request,
    ensure_direct_chat_turn_request,
    normalize_turn_policy_context,
    normalize_server_owned_turn_request,
    resolve_direct_chat_turn_request,
    resolve_agent_turn_request,
    resolve_agent_turn_request_from_runtime_context,
    resolve_agent_turn_request_with_fallback,
    resolve_run_start_turn_request,
    serialize_agent_turn_request,
    TurnActor,
)
from server_modules.run_service import build_run_start_request_from_turn
from server_modules.runtime_models import RunStartRequest


class AgentTurnTests(unittest.TestCase):
    def setUp(self) -> None:
        global agent_trace_service
        global AgentTurnRequest, agent_turn, bind_agent_turn_request_meta, bind_agent_turn_metadata
        global build_agent_turn_request, build_agent_turn_session_context, build_direct_chat_turn_request
        global build_discord_turn_request, build_inbound_agent_turn_request, build_local_worker_turn_request
        global build_telegram_turn_request, build_whatsapp_turn_request, resolve_agent_turn_session_identity
        global build_run_start_turn_request, ensure_direct_chat_turn_request, normalize_turn_policy_context
        global normalize_server_owned_turn_request, resolve_direct_chat_turn_request, resolve_agent_turn_request
        global resolve_agent_turn_request_from_runtime_context, resolve_agent_turn_request_with_fallback
        global resolve_run_start_turn_request, serialize_agent_turn_request, TurnActor
        global build_run_start_request_from_turn, RunStartRequest

        agent_trace_service = importlib.import_module("server_modules.agent_trace_service")
        agent_turn_module = importlib.import_module("server_modules.agent_turn")
        AgentTurnRequest = agent_turn_module.AgentTurnRequest
        agent_turn = agent_turn_module.agent_turn
        bind_agent_turn_request_meta = agent_turn_module.bind_agent_turn_request_meta
        bind_agent_turn_metadata = agent_turn_module.bind_agent_turn_metadata
        build_agent_turn_request = agent_turn_module.build_agent_turn_request
        build_agent_turn_session_context = agent_turn_module.build_agent_turn_session_context
        build_direct_chat_turn_request = agent_turn_module.build_direct_chat_turn_request
        build_discord_turn_request = agent_turn_module.build_discord_turn_request
        build_inbound_agent_turn_request = agent_turn_module.build_inbound_agent_turn_request
        build_local_worker_turn_request = agent_turn_module.build_local_worker_turn_request
        build_telegram_turn_request = agent_turn_module.build_telegram_turn_request
        build_whatsapp_turn_request = agent_turn_module.build_whatsapp_turn_request
        resolve_agent_turn_session_identity = agent_turn_module.resolve_agent_turn_session_identity
        build_run_start_turn_request = agent_turn_module.build_run_start_turn_request
        ensure_direct_chat_turn_request = agent_turn_module.ensure_direct_chat_turn_request
        normalize_turn_policy_context = agent_turn_module.normalize_turn_policy_context
        normalize_server_owned_turn_request = agent_turn_module.normalize_server_owned_turn_request
        resolve_direct_chat_turn_request = agent_turn_module.resolve_direct_chat_turn_request
        resolve_agent_turn_request = agent_turn_module.resolve_agent_turn_request
        resolve_agent_turn_request_from_runtime_context = agent_turn_module.resolve_agent_turn_request_from_runtime_context
        resolve_agent_turn_request_with_fallback = agent_turn_module.resolve_agent_turn_request_with_fallback
        resolve_run_start_turn_request = agent_turn_module.resolve_run_start_turn_request
        serialize_agent_turn_request = agent_turn_module.serialize_agent_turn_request
        TurnActor = agent_turn_module.TurnActor
        build_run_start_request_from_turn = importlib.import_module("server_modules.run_service").build_run_start_request_from_turn
        RunStartRequest = importlib.import_module("server_modules.runtime_models").RunStartRequest

    def test_agent_turn_starts_trace_emits_routed_and_returns_trace_id_in_sync_result(self):
        turn_request = AgentTurnRequest(
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            thread_id="thread-1",
            session_id="session-1",
            channel="web",
            actor=TurnActor(type="user", id="user-1", display_name="Alice"),
            message="hello world",
            execution_mode="sync",
            response_mode="stream",
            context_hints={
                "provider": "openai",
                "model": "gpt-5.4",
                "metadata": {
                    "workspace_agent_install_id": "install-1",
                    "runtime_profile_id": "profile-1",
                },
            },
        )
        trace_context = agent_trace_service.TraceContext(
            trace_id="trace-turn-1",
            workspace_id="workspace-1",
            tenant_id="tenant-1",
            thread_id="thread-1",
            run_id=None,
            root_agent_id="specialist:install-1",
        )

        async def _run():
            with patch("server_modules.agent_turn.agent_trace_service.start_trace", new=AsyncMock(return_value=trace_context)) as start_trace, patch("server_modules.agent_turn.agent_trace_service.emit_trace_routed", new=AsyncMock(return_value="tevent_1")) as emit_trace_routed, patch("server_modules.agent_turn.session_service.get_session_scoped", new=AsyncMock(return_value={
                "session_id": "session-1",
                "workspace_id": "workspace-1",
                "tenant_id": "tenant-1",
                "channel": "web",
                "actor": {"type": "user", "id": "user-1"},
                "created_at": "2026-04-08T00:00:00Z",
                "expires_at": "2099-01-01T00:00:00Z",
                "metadata": {"thread_id": "thread-1"},
                "status": "active",
            })), patch("server_modules.agent_turn.session_service.extend_session", new=AsyncMock(return_value=None)), patch("server_modules.agent_turn.thread_service.ensure_master_thread", new=AsyncMock(return_value={"id": "thread-1"})), patch("server_modules.agent_turn.thread_service.record_user_turn", new=AsyncMock(return_value={"thread": {"id": "thread-1"}, "turn": {"id": "turn-user"}})), patch("server_modules.agent_turn.thread_service.record_assistant_turn", new=AsyncMock(return_value={"thread": {"id": "thread-1"}, "turn": {"id": "turn-assistant"}})), patch("server_modules.turn_runtime.build_turn_execution_services", return_value={"services": "ok"}), patch("server_modules.turn_runtime.execute_agent_turn_request", new=AsyncMock(return_value={
                "status": "completed",
                "reply": "done",
                "run_id": "run-1",
                "approvals": [],
                "interventions": [],
                "metadata": {"agent_role": "master-agent"},
            })):
                result = await agent_turn(
                    turn_request=turn_request,
                    current_user={"user_id": "user-1", "role": "owner", "is_admin": True},
                    run_execution_services={"run": "services"},
                    direct_chat_services={"direct_chat": "services"},
                )
                return result, start_trace, emit_trace_routed

        result, start_trace, emit_trace_routed = asyncio.run(_run())
        self.assertEqual(result["metadata"]["trace_id"], "trace-turn-1")
        start_trace.assert_awaited_once()
        self.assertEqual(start_trace.await_args.kwargs["root_agent_id"], "specialist:install-1")
        emit_trace_routed.assert_awaited_once()
        self.assertEqual(emit_trace_routed.await_args.args[1], "sync")

    def test_agent_turn_returns_trace_id_in_direct_chat_stream_result(self):
        turn_request = AgentTurnRequest(
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            thread_id="thread-1",
            session_id="session-1",
            channel="web",
            actor=TurnActor(type="user", id="user-1", display_name="Alice"),
            message="hello world",
            execution_mode="sync",
            response_mode="stream",
            context_hints={"metadata": {}},
        )
        trace_context = agent_trace_service.TraceContext(
            trace_id="trace-stream-1",
            workspace_id="workspace-1",
            tenant_id="tenant-1",
            thread_id="thread-1",
            run_id=None,
            root_agent_id="sage",
        )

        async def _run():
            with patch("server_modules.agent_turn.agent_trace_service.start_trace", new=AsyncMock(return_value=trace_context)), patch("server_modules.agent_turn.agent_trace_service.emit_trace_routed", new=AsyncMock(return_value="tevent_1")), patch("server_modules.agent_turn.session_service.get_session_scoped", new=AsyncMock(return_value={
                "session_id": "session-1",
                "workspace_id": "workspace-1",
                "tenant_id": "tenant-1",
                "channel": "web",
                "actor": {"type": "user", "id": "user-1"},
                "created_at": "2026-04-08T00:00:00Z",
                "expires_at": "2099-01-01T00:00:00Z",
                "metadata": {"thread_id": "thread-1"},
                "status": "active",
            })), patch("server_modules.agent_turn.session_service.extend_session", new=AsyncMock(return_value=None)), patch("server_modules.agent_turn.thread_service.ensure_master_thread", new=AsyncMock(return_value={"id": "thread-1"})), patch("server_modules.agent_turn.thread_service.record_user_turn", new=AsyncMock(return_value={"thread": {"id": "thread-1"}, "turn": {"id": "turn-user"}})), patch("server_modules.agent_turn.thread_service.record_assistant_turn", new=AsyncMock(return_value={"thread": {"id": "thread-1"}, "turn": {"id": "turn-assistant"}})) as record_assistant_turn, patch("server_modules.turn_runtime.build_turn_execution_services", return_value={"services": "ok"}), patch("server_modules.turn_runtime.execute_agent_turn_request", new=AsyncMock(return_value={
                "kind": "direct_chat_stream",
                "workspace_id": "workspace-1",
                "session_key": "session-1",
                "thread_id": "thread-1",
                "client_request_id": "req-1",
            })):
                return await agent_turn(
                    turn_request=turn_request,
                    current_user={"user_id": "user-1", "role": "owner", "is_admin": True},
                    run_execution_services={"run": "services"},
                    direct_chat_services={"direct_chat": "services"},
                ), record_assistant_turn

        result, record_assistant_turn = asyncio.run(_run())
        self.assertEqual(result["metadata"]["trace_id"], "trace-stream-1")
        self.assertEqual(result["trace_id"], "trace-stream-1")
        record_assistant_turn.assert_not_awaited()

    def test_assistant_turn_metadata_persists_sanitized_transcript_events(self):
        agent_turn_module = importlib.import_module("server_modules.agent_turn")

        metadata = agent_turn_module._assistant_turn_metadata_from_result(
            {
                "status": "completed",
                "reply": "done",
                "metadata": {"agent_role": "sage", "trace_id": "trace-1"},
                "stream_events": [
                    {
                        "event": "trace",
                        "payload": {
                            "trace_id": "trace-1",
                            "event_type": "reasoning.summary.delta",
                            "data": {"delta": "private reasoning"},
                        },
                    },
                    {
                        "event": "trace",
                        "payload": {
                            "trace_id": "trace-1",
                            "event_type": "tool.started",
                            "tool_call_id": "shell-1",
                            "data": {
                                "tool_name": "shell__exec",
                                "input": {"command": "rm -rf /tmp/example"},
                            },
                        },
                    },
                    {
                        "event": "final",
                        "payload": {"reply": "done"},
                    },
                ],
            },
            request_id="req-1",
        )

        self.assertEqual(metadata["request_id"], "req-1")
        self.assertEqual(metadata["trace_id"], "trace-1")
        self.assertEqual(len(metadata["transcript_events"]), 1)
        self.assertEqual(metadata["transcript_events"][0]["payload"]["event_type"], "tool.started")
        self.assertNotIn("input", metadata["transcript_events"][0]["payload"]["data"])

    def test_assistant_turn_metadata_drops_reasoning_content(self):
        agent_turn_module = importlib.import_module("server_modules.agent_turn")

        metadata = agent_turn_module._assistant_turn_metadata_from_result(
            {
                "status": "completed",
                "reply": "done",
                "metadata": {
                    "agent_role": "sage",
                    "trace_id": "trace-1",
                    "reasoning": "private chain",
                    "reasoning_effort": "high",
                    "context_used": {
                        "provider": "openai",
                        "model": "gpt-5.5",
                        "chain_of_thought": "secret reasoning",
                        "raw_chain_of_thought": "other secret reasoning",
                    },
                    "thinking": "not persisted",
                },
            },
            request_id="req-1",
        )

        result_metadata = metadata["result_metadata"]
        self.assertNotIn("reasoning", result_metadata)
        self.assertNotIn("chain_of_thought", result_metadata.get("context_used", {}))
        self.assertNotIn("raw_chain_of_thought", result_metadata.get("context_used", {}))
        self.assertNotIn("thinking", result_metadata)
        self.assertEqual(result_metadata["reasoning_effort"], "high")
        self.assertEqual(metadata["request_id"], "req-1")

    def test_agent_turn_ensures_thread_before_starting_trace(self):
        turn_request = AgentTurnRequest(
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            thread_id="thread-1",
            session_id="session-1",
            channel="web",
            actor=TurnActor(type="user", id="user-1", display_name="Alice"),
            message="hello world",
            execution_mode="sync",
            response_mode="stream",
            context_hints={"metadata": {}},
        )
        call_order: list[str] = []
        trace_context = agent_trace_service.TraceContext(
            trace_id="trace-order-1",
            workspace_id="workspace-1",
            tenant_id="tenant-1",
            thread_id="thread-1",
            run_id=None,
            root_agent_id="sage",
        )

        async def _run():
            async def _ensure_master_thread(**kwargs):
                call_order.append("ensure_thread")
                return {"id": kwargs["thread_id"]}

            async def _start_trace(**kwargs):
                call_order.append("start_trace")
                return trace_context

            with patch("server_modules.agent_turn.agent_trace_service.start_trace", new=AsyncMock(side_effect=_start_trace)), patch("server_modules.agent_turn.agent_trace_service.emit_trace_started", new=AsyncMock(return_value="tevent_started")), patch("server_modules.agent_turn.agent_trace_service.emit_trace_routed", new=AsyncMock(return_value="tevent_routed")), patch("server_modules.agent_turn.session_service.get_session_scoped", new=AsyncMock(return_value={
                "session_id": "session-1",
                "workspace_id": "workspace-1",
                "tenant_id": "tenant-1",
                "channel": "web",
                "actor": {"type": "user", "id": "user-1"},
                "created_at": "2026-04-08T00:00:00Z",
                "expires_at": "2099-01-01T00:00:00Z",
                "metadata": {"thread_id": "thread-1"},
                "status": "active",
            })), patch("server_modules.agent_turn.session_service.extend_session", new=AsyncMock(return_value=None)), patch("server_modules.agent_turn.thread_service.ensure_master_thread", new=AsyncMock(side_effect=_ensure_master_thread)), patch("server_modules.agent_turn.thread_service.record_user_turn", new=AsyncMock(return_value={"thread": {"id": "thread-1"}, "turn": {"id": "turn-user"}})), patch("server_modules.agent_turn.thread_service.record_assistant_turn", new=AsyncMock(return_value={"thread": {"id": "thread-1"}, "turn": {"id": "turn-assistant"}})), patch("server_modules.turn_runtime.build_turn_execution_services", return_value={"services": "ok"}), patch("server_modules.turn_runtime.execute_agent_turn_request", new=AsyncMock(return_value={
                "status": "completed",
                "reply": "done",
                "metadata": {"agent_role": "master-agent"},
            })):
                return await agent_turn(
                    turn_request=turn_request,
                    current_user={"user_id": "user-1", "role": "owner", "is_admin": True},
                    run_execution_services={"run": "services"},
                    direct_chat_services={"direct_chat": "services"},
                )

        result = asyncio.run(_run())
        self.assertEqual(result["reply"], "done")
        self.assertEqual(call_order, ["ensure_thread", "start_trace"])

    def test_agent_turn_completes_when_trace_service_throws(self):
        turn_request = AgentTurnRequest(
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            thread_id="thread-1",
            session_id="session-1",
            channel="web",
            actor=TurnActor(type="user", id="user-1", display_name="Alice"),
            message="hello world",
            execution_mode="sync",
            response_mode="stream",
            context_hints={"metadata": {}},
        )

        async def _run():
            with patch("server_modules.agent_turn.agent_trace_service.start_trace", new=AsyncMock(side_effect=RuntimeError("trace unavailable"))), patch("server_modules.agent_turn.session_service.get_session_scoped", new=AsyncMock(return_value={
                "session_id": "session-1",
                "workspace_id": "workspace-1",
                "tenant_id": "tenant-1",
                "channel": "web",
                "actor": {"type": "user", "id": "user-1"},
                "created_at": "2026-04-08T00:00:00Z",
                "expires_at": "2099-01-01T00:00:00Z",
                "metadata": {"thread_id": "thread-1"},
                "status": "active",
            })), patch("server_modules.agent_turn.session_service.extend_session", new=AsyncMock(return_value=None)), patch("server_modules.agent_turn.thread_service.ensure_master_thread", new=AsyncMock(return_value={"id": "thread-1"})), patch("server_modules.agent_turn.thread_service.record_user_turn", new=AsyncMock(return_value={"thread": {"id": "thread-1"}, "turn": {"id": "turn-user"}})), patch("server_modules.agent_turn.thread_service.record_assistant_turn", new=AsyncMock(return_value={"thread": {"id": "thread-1"}, "turn": {"id": "turn-assistant"}})), patch("server_modules.turn_runtime.build_turn_execution_services", return_value={"services": "ok"}), patch("server_modules.turn_runtime.execute_agent_turn_request", new=AsyncMock(return_value={
                "status": "completed",
                "reply": "done",
                "metadata": {},
            })):
                return await agent_turn(
                    turn_request=turn_request,
                    current_user={"user_id": "user-1", "role": "owner", "is_admin": True},
                    run_execution_services={"run": "services"},
                    direct_chat_services={"direct_chat": "services"},
                )

        result = asyncio.run(_run())
        self.assertEqual(result["reply"], "done")
        self.assertNotIn("trace_id", result["metadata"])

    def test_agent_turn_injects_health_safety_business_plan_when_enabled(self):
        turn_request = AgentTurnRequest(
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            thread_id="thread-1",
            session_id="session-1",
            channel="telegram",
            actor=TurnActor(type="user", id="user-1", display_name="Alice"),
            message="I have chest pain.",
            execution_mode="durable",
            response_mode="channel_reply",
            context_hints={
                "metadata": {
                    "health_safety_enabled": True,
                    "health_safety_assistant_name": "HealthGuide",
                },
            },
        )

        async def _run():
            with patch("server_modules.agent_turn.agent_trace_service.start_trace", new=AsyncMock(return_value=None)), patch("server_modules.agent_turn.session_service.get_session_scoped", new=AsyncMock(return_value={
                "session_id": "session-1",
                "workspace_id": "workspace-1",
                "tenant_id": "tenant-1",
                "channel": "telegram",
                "actor": {"type": "user", "id": "user-1"},
                "created_at": "2026-04-08T00:00:00Z",
                "expires_at": "2099-01-01T00:00:00Z",
                "metadata": {"thread_id": "thread-1"},
                "status": "active",
            })), patch("server_modules.agent_turn.session_service.extend_session", new=AsyncMock(return_value=None)), patch("server_modules.agent_turn.thread_service.ensure_master_thread", new=AsyncMock(return_value={"id": "thread-1"})), patch("server_modules.agent_turn.thread_service.record_user_turn", new=AsyncMock(return_value={"thread": {"id": "thread-1"}, "turn": {"id": "turn-user"}})), patch("server_modules.agent_turn.thread_service.record_assistant_turn", new=AsyncMock(return_value={"thread": {"id": "thread-1"}, "turn": {"id": "turn-assistant"}})), patch("server_modules.turn_runtime.build_turn_execution_services", return_value={"services": "ok"}), patch("server_modules.turn_runtime.execute_agent_turn_request", new=AsyncMock(return_value={
                "status": "completed",
                "reply": "done",
                "metadata": {"agent_role": "master-agent"},
            })) as execute_mock:
                result = await agent_turn(
                    turn_request=turn_request,
                    current_user={"user_id": "user-1", "role": "owner", "is_admin": True},
                    run_execution_services={"run": "services"},
                    direct_chat_services={"direct_chat": "services"},
                )
                return result, execute_mock

        result, execute_mock = asyncio.run(_run())
        self.assertEqual(result["reply"], "done")
        forwarded_turn_request = execute_mock.await_args.kwargs["turn_request"]
        self.assertTrue(forwarded_turn_request.context_hints["metadata"]["health_safety_enabled"])
        self.assertIn("healthguide_safety_policy_v1", forwarded_turn_request.context_hints["business_plan"])

    def test_agent_turn_persists_master_thread_and_turns(self):
        turn_request = AgentTurnRequest(
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            thread_id="thread-1",
            session_id="session-1",
            channel="web",
            actor=TurnActor(type="user", id="user-1", display_name="Alice"),
            message="hello world",
            execution_mode="sync",
            response_mode="stream",
            context_hints={
                "request_id": "req-1",
                "metadata": {
                    "workspace_agent_install_id": "install-1",
                    "runtime_profile_id": "profile-1",
                },
            },
        )

        with patch("server_modules.agent_turn.session_service.get_session", new=AsyncMock(return_value={
            "session_id": "session-1",
            "workspace_id": "workspace-1",
            "tenant_id": "tenant-1",
            "channel": "web",
            "actor": {"type": "user", "id": "user-1"},
            "created_at": "2026-04-08T00:00:00Z",
            "expires_at": "2099-01-01T00:00:00Z",
            "metadata": {"thread_id": "thread-1"},
            "status": "active",
        })), patch("server_modules.agent_turn.session_service.extend_session", new=AsyncMock(return_value=None)), patch("server_modules.agent_turn.thread_service.ensure_master_thread", new=AsyncMock(return_value={"id": "thread-1"})) as ensure_thread_mock, patch("server_modules.agent_turn.thread_service.record_user_turn", new=AsyncMock(return_value={
            "thread": {"id": "thread-1", "updated_at": "2026-04-12T00:00:00Z"},
            "turn": {"id": "turn-user", "role": "user", "content": "hello world"},
        })) as record_user_mock, patch("server_modules.agent_turn.thread_service.record_assistant_turn", new=AsyncMock(return_value={
            "thread": {"id": "thread-1", "updated_at": "2026-04-12T00:00:01Z"},
            "turn": {"id": "turn-assistant", "role": "assistant", "content": "done"},
        })) as record_assistant_mock, patch("server_modules.turn_runtime.build_turn_execution_services", return_value={"services": "ok"}), patch("server_modules.turn_runtime.execute_agent_turn_request", new=AsyncMock(return_value={
            "status": "completed",
            "reply": "done",
            "run_id": "run-1",
            "approvals": [],
            "interventions": [],
            "metadata": {"agent_role": "master-agent"},
        })):
            result = asyncio.run(
                agent_turn(
                    turn_request=turn_request,
                    current_user={"user_id": "user-1", "role": "owner", "is_admin": True},
                    run_execution_services={"run": "services"},
                    direct_chat_services={"direct_chat": "services"},
                )
            )

        self.assertEqual(result["reply"], "done")
        ensure_thread_mock.assert_awaited_once()
        self.assertEqual(ensure_thread_mock.await_args.kwargs["thread_id"], "thread-1")
        self.assertEqual(ensure_thread_mock.await_args.kwargs["master_agent_install_id"], "install-1")
        record_user_mock.assert_awaited_once()
        self.assertEqual(record_user_mock.await_args.kwargs["content"], "hello world")
        self.assertEqual(record_user_mock.await_args.kwargs["runtime_profile_id"], "profile-1")
        record_assistant_mock.assert_awaited_once()
        self.assertEqual(record_assistant_mock.await_args.kwargs["run_id"], "run-1")
        self.assertEqual(record_assistant_mock.await_args.kwargs["active_agent_install_id"], "install-1")
        self.assertEqual(record_assistant_mock.await_args.kwargs["runtime_profile_id"], "profile-1")

    def test_build_inbound_agent_turn_request_normalizes_generic_inputs(self):
        request = build_inbound_agent_turn_request(
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            session_id="session-1",
            channel="Discord",
            actor_type="user",
            actor_id="user-1",
            actor_display_name="Alice",
            message="hello",
            execution_mode="durable",
            response_mode="artifact",
            context_hints={"source": "discord"},
        )

        self.assertEqual(request.channel, "discord")
        self.assertEqual(request.execution_mode, "durable")
        self.assertEqual(request.actor.display_name, "Alice")
        self.assertEqual(request.context_hints["source"], "discord")

    def test_build_agent_turn_request_preserves_top_level_provider_fields(self):
        request = build_agent_turn_request(
            {
                "tenant_id": "tenant-1",
                "workspace_id": "workspace-1",
                "thread_id": "thread-1",
                "session_id": "session-1",
                "channel": "web",
                "actor": {"type": "user", "id": "user-1", "display_name": "Alice"},
                "message": "hello",
                "provider": "anthropic",
                "model": "claude-sonnet-4-5",
                "reasoning_effort": "medium",
            }
        )

        self.assertEqual(request.context_hints["provider"], "anthropic")
        self.assertEqual(request.context_hints["model"], "claude-sonnet-4-5")
        self.assertEqual(request.context_hints["reasoning_effort"], "medium")

    def test_build_direct_chat_turn_request_normalizes_core_fields(self):
        request = build_direct_chat_turn_request(
            current_user={"user_id": "user-1", "email": "user@example.com"},
            body={
                "provider": "openai",
                "model": "gpt-test",
                "reasoning_effort": "high",
            },
            workspace_id="workspace-1",
            thread_id="thread-1",
            client_request_id="req-1",
            message="hello world",
        )

        self.assertEqual(request.workspace_id, "workspace-1")
        self.assertEqual(request.session_id, "thread-1")
        self.assertEqual(request.actor.id, "user-1")
        self.assertEqual(request.response_mode, "stream")
        self.assertEqual(request.execution_mode, "sync")
        self.assertEqual(request.context_hints["provider"], "openai")

    def test_build_run_start_turn_request_and_bind_metadata(self):
        run_request = RunStartRequest(
            engine="orion",
            workspace_id="workspace-1",
            workflow_id="workflow-1",
            user_goal="Review the latest inbox state",
            agent_role="orchestrator",
            metadata={
                "owner_user_id": "user-1",
                "owner_email": "user@example.com",
                "execution_target": "local_companion",
                "trust_mode": "guarded",
            },
        )

        turn_request = build_run_start_turn_request(run_request)
        metadata = bind_agent_turn_metadata(run_request.metadata, turn_request, source="runs/start")

        self.assertEqual(turn_request.execution_mode, "durable")
        self.assertEqual(turn_request.response_mode, "artifact")
        self.assertIsNone(turn_request.machine_target)
        self.assertEqual(metadata["source"], "runs/start")
        self.assertEqual(metadata["agent_turn_contract_version"], 1)
        self.assertEqual(metadata["agent_turn_request"]["workspace_id"], "workspace-1")
        self.assertEqual(metadata["agent_turn_request"]["actor"]["id"], "user-1")

    def test_build_run_start_turn_request_preserves_explicit_machine_target_only(self):
        run_request = RunStartRequest(
            engine="orion",
            workspace_id="workspace-1",
            user_goal="Write file demo.txt",
            metadata={
                "execution_target": "local_companion",
                "machine_target": "local-worker-1",
            },
        )

        turn_request = build_run_start_turn_request(run_request)

        self.assertEqual(turn_request.machine_target, "local-worker-1")

    def test_build_direct_chat_turn_request_tracks_client_request_id_in_context_hints(self):
        request = build_direct_chat_turn_request(
            current_user={"user_id": "user-1", "email": "user@example.com"},
            body={},
            workspace_id="workspace-1",
            thread_id="thread-1",
            client_request_id="req-123",
            message="hello world",
        )

        self.assertEqual(request.context_hints["request_id"], "req-123")

    def test_build_agent_turn_request_preserves_top_level_client_request_id(self):
        request = build_agent_turn_request(
            {
                "tenant_id": "tenant-1",
                "workspace_id": "workspace-1",
                "thread_id": "thread-1",
                "session_id": "thread-1",
                "channel": "web",
                "actor": {"type": "user", "id": "user-1", "display_name": "Alice"},
                "message": "hello world",
                "client_request_id": "req-canonical-1",
                "response_mode": "stream",
            }
        )

        self.assertEqual(request.context_hints["request_id"], "req-canonical-1")

    def test_build_direct_chat_turn_request_promotes_serious_task_to_durable_run(self):
        request = build_direct_chat_turn_request(
            current_user={"user_id": "user-1", "email": "user@example.com"},
            body={},
            workspace_id="workspace-1",
            thread_id="thread-1",
            client_request_id="req-serious",
            message="Research the latest project blockers and draft a summary.",
        )

        self.assertEqual(request.execution_mode, "durable")
        self.assertEqual(request.response_mode, "artifact")
        self.assertEqual(request.context_hints["metadata"]["primary_engine_path"], "durable_run")
        self.assertEqual(request.context_hints["metadata"]["primary_engine_reason"], "task_markers")

    def test_build_direct_chat_turn_request_keeps_lightweight_question_as_direct_chat(self):
        request = build_direct_chat_turn_request(
            current_user={"user_id": "user-1", "email": "user@example.com"},
            body={},
            workspace_id="workspace-1",
            thread_id="thread-1",
            client_request_id="req-light",
            message="What model are you using right now?",
        )

        self.assertEqual(request.execution_mode, "sync")
        self.assertEqual(request.response_mode, "stream")

    def test_build_agent_turn_request_respects_force_direct_chat_override(self):
        request = build_agent_turn_request(
            {
                "tenant_id": "default",
                "workspace_id": "workspace-1",
                "thread_id": "thread-1",
                "session_id": "thread-1",
                "channel": "web",
                "actor": {"type": "user", "id": "user-1"},
                "message": "Research the latest project blockers and draft a summary.",
                "execution_mode": "sync",
                "response_mode": "stream",
                "context_hints": {"metadata": {"force_direct_chat": True}},
            }
        )

        self.assertEqual(request.execution_mode, "sync")
        self.assertEqual(request.response_mode, "stream")

    def test_build_telegram_turn_request_shapes_channel_identity(self):
        request = build_telegram_turn_request(
            workspace_id="workspace-1",
            connector_entry={"metadata": {"tenant_id": "tenant-1", "trust_mode": "guarded"}},
            goal="Check status",
            chat_id="chat-1",
            sender_id="sender-1",
            update_id=42,
            message_id="msg-1",
            trace_id="tg:trace-1",
            source_event_id="event-1",
            metadata={"execution_target": "local_companion"},
        )

        self.assertEqual(request.channel, "telegram")
        self.assertEqual(request.session_id, "chat-1")
        self.assertEqual(request.actor.id, "sender-1")
        self.assertEqual(request.tenant_id, "tenant-1")
        self.assertEqual(request.context_hints["telegram"]["message_id"], "msg-1")
        self.assertEqual(request.policy_context["execution_target"], "local_companion")

    def test_build_whatsapp_turn_request_shapes_channel_identity(self):
        request = build_whatsapp_turn_request(
            workspace_id="workspace-1",
            connector_entry={"metadata": {"tenant_id": "tenant-1", "trust_mode": "guarded"}},
            goal="Handle inbound",
            from_number="whatsapp:+1",
            to_number="whatsapp:+2",
            message_sid="SM123",
            account_sid="AC123",
            session_id="whatsapp:+1->+2",
            trace_id="wa:trace-1",
            metadata={"execution_target": "cloud"},
        )

        self.assertEqual(request.channel, "whatsapp")
        self.assertEqual(request.session_id, "whatsapp:+1->+2")
        self.assertEqual(request.actor.id, "whatsapp:+1")
        self.assertEqual(request.tenant_id, "tenant-1")
        self.assertEqual(request.context_hints["whatsapp"]["message_sid"], "SM123")
        self.assertEqual(request.policy_context["execution_target"], "cloud")

    def test_resolve_agent_turn_request_round_trips_serialized_payload(self):
        request = build_direct_chat_turn_request(
            current_user={"user_id": "user-1"},
            body={},
            workspace_id="default",
            thread_id="thread-1",
            client_request_id="req-1",
            message="hello",
        )

        round_tripped = resolve_agent_turn_request(serialize_agent_turn_request(request))

        self.assertIsNotNone(round_tripped)
        self.assertEqual(round_tripped.workspace_id, "default")
        self.assertEqual(round_tripped.session_id, "thread-1")
        self.assertEqual(round_tripped.message, "hello")

    def test_resolve_agent_turn_request_with_fallback_uses_secondary_payload(self):
        request = build_direct_chat_turn_request(
            current_user={"user_id": "user-1"},
            body={},
            workspace_id="default",
            thread_id="thread-1",
            client_request_id="req-1",
            message="hello",
        )

        resolved = resolve_agent_turn_request_with_fallback(
            None,
            serialize_agent_turn_request(request),
        )

        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.workspace_id, "default")
        self.assertEqual(resolved.message, "hello")

    def test_resolve_agent_turn_request_from_runtime_context_prefers_request_meta(self):
        meta_request = build_direct_chat_turn_request(
            current_user={"user_id": "meta-user"},
            body={},
            workspace_id="workspace-meta",
            thread_id="thread-meta",
            client_request_id="req-meta",
            message="meta message",
        )
        session_request = build_direct_chat_turn_request(
            current_user={"user_id": "session-user"},
            body={},
            workspace_id="workspace-session",
            thread_id="thread-session",
            client_request_id="req-session",
            message="session message",
        )

        resolved = resolve_agent_turn_request_from_runtime_context(
            request_meta={"agent_turn_request": serialize_agent_turn_request(meta_request)},
            session_ctx={"agent_turn_request": serialize_agent_turn_request(session_request)},
        )

        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.workspace_id, "workspace-meta")
        self.assertEqual(resolved.message, "meta message")

    def test_build_run_start_request_from_turn_preserves_canonical_metadata(self):
        base_request = RunStartRequest(
            engine="orion",
            workspace_id="workspace-1",
            user_goal="Original goal",
            provider="openai",
            model="gpt-test",
            metadata={"owner_user_id": "user-1", "trust_mode": "guarded"},
        )
        turn_request = build_run_start_turn_request(base_request)

        converted = build_run_start_request_from_turn(turn_request, base_request=base_request)

        self.assertEqual(converted.workspace_id, "workspace-1")
        self.assertEqual(converted.user_goal, "Original goal")
        self.assertEqual(converted.provider, "openai")
        self.assertEqual(converted.metadata["agent_turn_request"]["workspace_id"], "workspace-1")
        self.assertEqual(converted.metadata["agent_turn_request"]["message"], "Original goal")

    def test_normalize_turn_policy_context_downgrades_non_owner_agent_mode_and_logs(self):
        with self.assertLogs("server_modules.agent_turn", level="WARNING") as captured:
            normalized = normalize_turn_policy_context(
                {
                    "session_mode": "agent",
                    "trust_mode": "auto",
                    "execution_target": "local_companion",
                    "runtime_trust_zone": "owned_dedicated_runtime",
                    "elevated_mode": "full",
                },
                current_user={"user_id": "user-2", "role": "member", "is_admin": False},
                workspace_id="workspace-1",
                session_id="thread-1",
            )

        self.assertEqual(normalized["requested_session_mode"], "agent")
        self.assertEqual(normalized["session_mode"], "copilot")
        self.assertEqual(normalized["effective_session_mode"], "copilot")
        self.assertEqual(normalized["trust_mode"], "guarded")
        self.assertEqual(normalized["runtime_trust_zone"], "user_owned_local")
        self.assertEqual(normalized["elevated_mode"], "ask")
        self.assertTrue(normalized["interactive_approvals"])
        self.assertTrue(normalized["session_mode_downgraded"])
        self.assertIn("Downgraded requested agent session mode to copilot.", captured.output[0])

    def test_normalize_turn_policy_context_allows_owner_agent_mode(self):
        with patch("server_modules.runtime_config.agent_machine_full_trust_enabled", return_value=True):
            normalized = normalize_turn_policy_context(
                {
                    "session_mode": "agent",
                    "trust_mode": "guarded",
                    "execution_target": "local_companion",
                    "machine_trust_declaration": "agent",
                    "elevated_mode": "full",
                },
                current_user={"user_id": "user-1", "role": "owner", "is_admin": True},
                workspace_id="workspace-1",
                session_id="thread-1",
            )

        self.assertEqual(normalized["session_mode"], "agent")
        self.assertEqual(normalized["effective_session_mode"], "agent")
        self.assertEqual(normalized["trust_mode"], "auto")
        self.assertEqual(normalized["runtime_trust_zone"], "owned_dedicated_runtime")
        self.assertEqual(normalized["elevated_mode"], "full")
        self.assertFalse(normalized["interactive_approvals"])

    def test_normalize_turn_policy_context_auto_review_keeps_policy_reviewer_and_approvals(self):
        with patch("server_modules.runtime_config.agent_machine_full_trust_enabled", return_value=True):
            normalized = normalize_turn_policy_context(
                {
                    "session_mode": "agent",
                    "trust_mode": "sensitive_guard",
                    "permission_mode": "auto_review",
                    "approval_reviewer": "policy",
                    "interactive_approvals": True,
                    "execution_target": "cloud",
                    "runtime_trust_zone": "shared_cloud",
                },
                current_user={"user_id": "user-1", "role": "owner", "is_admin": True},
                workspace_id="workspace-1",
                session_id="thread-1",
            )

        self.assertEqual(normalized["session_mode"], "agent")
        self.assertEqual(normalized["permission_mode"], "auto_review")
        self.assertEqual(normalized["approval_reviewer"], "policy")
        self.assertEqual(normalized["trust_mode"], "sensitive_guard")
        self.assertTrue(normalized["interactive_approvals"])

    def test_normalize_turn_policy_context_autopilot_on_personal_machine_keeps_approvals(self):
        with patch("server_modules.runtime_config.agent_machine_full_trust_enabled", return_value=True):
            normalized = normalize_turn_policy_context(
                {
                    "session_mode": "agent",
                    "trust_mode": "auto",
                    "permission_mode": "autopilot",
                    "interactive_approvals": False,
                    "execution_target": "local_companion",
                    "runtime_trust_zone": "user_owned_local",
                    "elevated_mode": "full",
                },
                current_user={"user_id": "user-1", "role": "owner", "is_admin": True},
                workspace_id="workspace-1",
                session_id="thread-1",
            )

        self.assertEqual(normalized["session_mode"], "agent")
        self.assertEqual(normalized["permission_mode"], "device_access")
        self.assertEqual(normalized["runtime_trust_zone"], "user_owned_local")
        self.assertEqual(normalized["trust_mode"], "sensitive_guard")
        self.assertTrue(normalized["interactive_approvals"])

    def test_normalize_turn_policy_context_downgrades_owner_when_full_trust_is_disabled(self):
        with patch("server_modules.runtime_config.agent_machine_full_trust_enabled", return_value=False), self.assertLogs("server_modules.agent_turn", level="WARNING") as captured:
            normalized = normalize_turn_policy_context(
                {"session_mode": "agent", "trust_mode": "auto"},
                current_user={"user_id": "user-1", "role": "owner", "is_admin": True},
                workspace_id="workspace-1",
                session_id="thread-1",
            )

        self.assertEqual(normalized["session_mode"], "copilot")
        self.assertEqual(normalized["trust_mode"], "guarded")
        self.assertEqual(normalized["session_mode_downgrade_reason"], "full_trust_disabled")
        self.assertIn("Downgraded requested agent session mode to copilot.", captured.output[0])

    def test_agent_turn_validates_existing_session_before_dispatch(self):
        request = build_inbound_agent_turn_request(
            workspace_id="workspace-1",
            session_id="thread-1",
            channel="web",
            actor_type="user",
            actor_id="user-1",
            message="hello",
        )

        async def _run():
            with patch("server_modules.agent_turn.session_service.get_session", new=AsyncMock(return_value={
                "session_id": "thread-1",
                "workspace_id": "workspace-1",
                "tenant_id": "default",
                "channel": "web",
                "actor": {"type": "user", "id": "user-1"},
                "created_at": "2026-04-06T00:00:00Z",
                "expires_at": "2026-04-07T00:00:00Z",
                "metadata": {"source": "test"},
                "status": "active",
            })) as get_session, patch("server_modules.agent_turn.session_service.extend_session", new=AsyncMock(return_value=None)) as extend_session, patch(
                "server_modules.turn_runtime.build_turn_execution_services",
                return_value="services",
            ), patch(
                "server_modules.turn_runtime.execute_agent_turn_request",
                new=AsyncMock(return_value={"status": "ok", "session_id": "thread-1"}),
            ) as execute_agent_turn_request:
                result = await agent_turn(
                    turn_request=request,
                    current_user={"user_id": "user-1"},
                    run_execution_services="run-services",
                )
                return result, get_session, extend_session, execute_agent_turn_request

        import asyncio

        result, get_session, extend_session, execute_agent_turn_request = asyncio.run(_run())
        self.assertEqual(result["status"], "ok")
        self.assertEqual(request.context_hints["session"]["session_id"], "thread-1")
        get_session.assert_awaited()
        extend_session.assert_awaited_once_with("thread-1")
        execute_agent_turn_request.assert_awaited()

    def test_agent_turn_creates_session_when_missing(self):
        request = AgentTurnRequest(
            tenant_id="default",
            workspace_id="workspace-1",
            thread_id="thread-1",
            session_id="",
            channel="web",
            actor=TurnActor(type="user", id="user-1", display_name="user-1"),
            message="hello",
        )

        async def _run():
            with patch("server_modules.agent_turn.session_service.get_session", new=AsyncMock(return_value={
                "session_id": "generated-session",
                "workspace_id": "workspace-1",
                "tenant_id": "default",
                "channel": "web",
                "actor": {"type": "user", "id": "user-1"},
                "created_at": "2026-04-06T00:00:00Z",
                "expires_at": "2026-04-07T00:00:00Z",
                "metadata": {"source": "agent_turn"},
                "status": "active",
            })), patch("server_modules.agent_turn.session_service.create_session", new=AsyncMock(return_value="generated-session")) as create_session, patch(
                "server_modules.turn_runtime.build_turn_execution_services",
                return_value="services",
            ), patch(
                "server_modules.turn_runtime.execute_agent_turn_request",
                new=AsyncMock(return_value={"status": "ok", "session_id": "generated-session"}),
            ):
                return await agent_turn(
                    turn_request=request,
                    current_user={"user_id": "user-1"},
                    run_execution_services="run-services",
                ), create_session

        import asyncio

        result, create_session = asyncio.run(_run())
        self.assertEqual(result["session_id"], "generated-session")
        self.assertEqual(request.session_id, "generated-session")
        create_session.assert_awaited_once()

    def test_agent_turn_binds_cloud_runtime_session_metadata_for_deployed_agent(self):
        request = AgentTurnRequest(
            tenant_id="default",
            workspace_id="workspace-1",
            thread_id="thread-1",
            session_id="",
            channel="web",
            actor=TurnActor(type="user", id="user-1", display_name="user-1"),
            message="hello",
            context_hints={"metadata": {"deployed_agent_id": "dagent_1"}},
        )

        async def _run():
            with (
                patch(
                    "server_modules.agent_turn.session_service.get_session",
                    new=AsyncMock(
                        return_value={
                            "session_id": "generated-session",
                            "workspace_id": "workspace-1",
                            "tenant_id": "default",
                            "channel": "web",
                            "actor": {"type": "user", "id": "user-1"},
                            "created_at": "2026-04-06T00:00:00Z",
                            "expires_at": "2026-04-07T00:00:00Z",
                            "metadata": {"source": "agent_turn", "deployed_agent_id": "dagent_1"},
                            "status": "active",
                        }
                    ),
                ),
                patch("server_modules.agent_turn.session_service.create_session", new=AsyncMock(return_value="generated-session")),
                patch("server_modules.agent_turn.session_service.extend_session", new=AsyncMock()) as extend_session,
                patch(
                    "server_modules.agent_turn.deployed_agent_virtual_runtime_service.ensure_runtime_session_binding",
                    new=AsyncMock(
                        return_value={
                            "metadata_updates": {
                                "runtime_session_id": "vcsess_1",
                                "runtime_session_binding": "cloud_computer_agent",
                                "runtime_choice": "virtual_browser",
                                "virtual_runtime_bound": True,
                            }
                        }
                    ),
                ) as bind_runtime,
                patch("server_modules.turn_runtime.build_turn_execution_services", return_value="services"),
                patch(
                    "server_modules.turn_runtime.execute_agent_turn_request",
                    new=AsyncMock(return_value={"status": "ok", "session_id": "generated-session"}),
                ),
            ):
                result = await agent_turn(
                    turn_request=request,
                    current_user={"user_id": "user-1"},
                    run_execution_services="run-services",
                )
                return result, extend_session, bind_runtime

        import asyncio

        result, extend_session, bind_runtime = asyncio.run(_run())
        self.assertEqual(result["session_id"], "generated-session")
        self.assertEqual(request.context_hints["metadata"]["runtime_session_id"], "vcsess_1")
        self.assertEqual(request.context_hints["metadata"]["runtime_session_binding"], "cloud_computer_agent")
        self.assertEqual(request.context_hints["session"]["metadata"]["runtime_session_id"], "vcsess_1")
        bind_runtime.assert_awaited_once()
        extend_session.assert_awaited_once_with(
            "generated-session",
            metadata_updates={
                "runtime_session_id": "vcsess_1",
                "runtime_session_binding": "cloud_computer_agent",
                "runtime_choice": "virtual_browser",
                "virtual_runtime_bound": True,
            },
        )

    def test_bind_agent_turn_request_meta_binds_serialized_turn_request(self):
        request = build_direct_chat_turn_request(
            current_user={"user_id": "user-1"},
            body={},
            workspace_id="workspace-1",
            thread_id="thread-1",
            client_request_id="req-1",
            message="hello",
        )

        bound = bind_agent_turn_request_meta({"request_id": "req-1"}, request)

        self.assertEqual(bound["agent_turn_request"]["workspace_id"], "workspace-1")
        self.assertEqual(bound["thread_id"], "thread-1")

    def test_build_agent_turn_session_context_serializes_turn_request(self):
        request = build_direct_chat_turn_request(
            current_user={"user_id": "user-1"},
            body={},
            workspace_id="workspace-1",
            thread_id="thread-1",
            client_request_id="req-1",
            message="hello",
        )

        context = build_agent_turn_session_context(
            request,
            workspace_id="ignored",
            session_id="ignored",
            user_id="user-1",
        )

        self.assertEqual(context["workspace_id"], "workspace-1")
        self.assertEqual(context["thread_id"], "thread-1")
        self.assertEqual(context["agent_turn_request"]["message"], "hello")
        self.assertIsNone(context["effective_session_mode"])

    def test_resolve_agent_turn_session_identity_prefers_request_fields(self):
        request = build_direct_chat_turn_request(
            current_user={"user_id": "user-1"},
            body={},
            workspace_id="workspace-1",
            thread_id="thread-1",
            client_request_id="req-1",
            message="hello",
        )

        identity = resolve_agent_turn_session_identity(
            request,
            workspace_id="ignored",
            session_id="ignored",
            user_id="fallback-user",
        )

        self.assertEqual(identity["workspace_id"], "workspace-1")
        self.assertEqual(identity["thread_id"], "thread-1")
        self.assertEqual(identity["user_id"], "fallback-user")

    def test_resolve_direct_chat_turn_request_normalizes_api_inputs(self):
        resolved = resolve_direct_chat_turn_request(
            current_user={"user_id": "user-1"},
            body={
                "message": "hello",
                "workspace_id": "workspace-1",
                "thread_id": "thread-1",
                "channel": "mobile",
                "metadata": {
                    "source": "mobile_chat",
                    "agent_id": "research-agent",
                },
            },
            request_signature_fn=lambda body: "req-1",
        )

        self.assertEqual(resolved.workspace_id, "workspace-1")
        self.assertEqual(resolved.thread_id, "thread-1")

    def test_resolve_direct_chat_turn_request_rebinds_master_mobile_thread_server_side(self):
        with patch(
            "server_modules.agent_turn.agent_registry_repository.build_master_thread_id",
            return_value="thread-sage",
        ):
            resolved = resolve_direct_chat_turn_request(
                current_user={"user_id": "user-1"},
                body={
                    "message": "hello",
                    "workspace_id": "workspace-1",
                    "channel": "mobile",
                    "session_id": "session-1",
                    "metadata": {
                        "source": "mobile_chat",
                        "agent_id": "assistant",
                    },
                },
                request_signature_fn=lambda body: "req-1",
            )

        self.assertEqual(resolved.workspace_id, "workspace-1")
        self.assertEqual(resolved.thread_id, "thread-sage")
        self.assertEqual(resolved.turn_request.thread_id, "thread-sage")
        self.assertEqual(resolved.turn_request.session_id, "session-1")

    def test_normalize_server_owned_turn_request_rebinds_master_web_thread(self):
        request = AgentTurnRequest(
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            thread_id="client-thread",
            session_id="session-1",
            channel="web",
            actor=TurnActor(type="user", id="user-1", display_name="Alice"),
            message="hello",
            execution_mode="sync",
            response_mode="stream",
            context_hints={
                "metadata": {
                    "source": "direct_chat",
                    "master_agent_install_id": "install-1",
                },
            },
        )

        with patch(
            "server_modules.agent_turn.agent_registry_repository.build_master_thread_id",
            return_value="thread-sage",
        ):
            normalized = normalize_server_owned_turn_request(
                current_user={"user_id": "user-1"},
                turn_request=request,
            )

        self.assertEqual(normalized.thread_id, "thread-sage")
        self.assertEqual(normalized.session_id, "session-1")
        self.assertEqual(normalized.context_hints["thread_id"], "thread-sage")
        self.assertEqual(normalized.context_hints["metadata"]["client_thread_id"], "client-thread")
        self.assertTrue(normalized.context_hints["metadata"]["server_owned_thread"])

    def test_normalize_server_owned_turn_request_rebinds_artifact_mode_web_thread_from_context_source(self):
        request = AgentTurnRequest(
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            thread_id="client-thread",
            session_id="session-1",
            channel="web",
            actor=TurnActor(type="user", id="user-1", display_name="Alice"),
            message="hello",
            execution_mode="sync",
            response_mode="artifact",
            context_hints={
                "source": "direct_chat",
            },
        )

        with patch(
            "server_modules.agent_turn.agent_registry_repository.build_master_thread_id",
            return_value="thread-sage",
        ):
            normalized = normalize_server_owned_turn_request(
                current_user={"user_id": "user-1"},
                turn_request=request,
            )

        self.assertEqual(normalized.thread_id, "thread-sage")
        self.assertEqual(normalized.context_hints["thread_id"], "thread-sage")
        self.assertEqual(normalized.context_hints["metadata"]["source"], "direct_chat")
        self.assertEqual(normalized.context_hints["metadata"]["client_thread_id"], "client-thread")
        self.assertTrue(normalized.context_hints["metadata"]["server_owned_thread"])

    def test_normalize_server_owned_turn_request_preserves_specialist_mobile_thread(self):
        request = AgentTurnRequest(
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            thread_id="specialist-thread",
            session_id="session-1",
            channel="mobile",
            actor=TurnActor(type="user", id="user-1", display_name="Alice"),
            message="hello",
            execution_mode="sync",
            response_mode="stream",
            context_hints={
                "metadata": {
                    "source": "mobile_chat",
                    "agent_id": "research-agent",
                },
            },
        )

        normalized = normalize_server_owned_turn_request(
            current_user={"user_id": "user-1"},
            turn_request=request,
        )

        self.assertEqual(normalized.thread_id, "specialist-thread")
        self.assertNotIn("thread_id", normalized.context_hints)

    def test_build_discord_turn_request_preserves_channel_metadata(self):
        request = build_discord_turn_request(
            parsed={
                "channel_id": "channel-1",
                "guild_id": "guild-1",
                "user_id": "user-1",
                "username": "alice",
                "message_id": "msg-1",
                "event_type": "message_create",
            },
            connector_entry={"workspace_id": "workspace-1", "metadata": {"trust_mode": "guarded"}},
            goal="please investigate this",
            trace_id="trace-1",
        )

        self.assertEqual(request.workspace_id, "workspace-1")
        self.assertEqual(request.channel, "discord")
        self.assertEqual(request.execution_mode, "durable")
        self.assertEqual(request.context_hints["discord"]["channel_id"], "channel-1")
        self.assertEqual(request.message, "please investigate this")

    def test_build_local_worker_turn_request_uses_worker_and_run_identity(self):
        request = build_local_worker_turn_request(
            worker_id="worker-1",
            run={"id": "run-1"},
            context={"workspace_id": "workspace-1"},
            metadata={"thread_id": "thread-1", "execution_target": "local_companion"},
            goal="Summarize priorities",
        )

        self.assertEqual(request.channel, "local_worker")
        self.assertEqual(request.actor.type, "worker")
        self.assertEqual(request.actor.id, "worker-1")
        self.assertEqual(request.session_id, "thread-1")
        self.assertEqual(request.machine_target, "local_companion")
        self.assertEqual(request.message, "Summarize priorities")

    def test_ensure_direct_chat_turn_request_accepts_serialized_contract(self):
        request = build_direct_chat_turn_request(
            current_user={"user_id": "user-1"},
            body={"thread_id": "thread-1"},
            workspace_id="default",
            thread_id="thread-1",
            client_request_id="req-1",
            message="hello",
        )

        ensured = ensure_direct_chat_turn_request(
            current_user={"user_id": "user-1"},
            body={"thread_id": "thread-1"},
            workspace_id="default",
            thread_id="thread-1",
            client_request_id="req-1",
            message="ignored",
            agent_turn_request=serialize_agent_turn_request(request),
        )

        self.assertEqual(ensured.workspace_id, "default")
        self.assertEqual(ensured.session_id, "thread-1")
        self.assertEqual(ensured.message, "hello")

    def test_resolve_run_start_turn_request_stamps_request_before_turn_build(self):
        resolution = resolve_run_start_turn_request(
            current_user={"user_id": "user-1"},
            body=RunStartRequest(
                engine="orion",
                workspace_id="workspace-1",
                user_goal="Review inbox",
                metadata={},
            ),
            stamp_request_owner_fn=lambda req, current_user: RunStartRequest(
                engine=req.engine,
                workspace_id=req.workspace_id,
                user_goal=req.user_goal,
                metadata={**dict(req.metadata or {}), "owner_user_id": current_user["user_id"]},
            ),
        )

        self.assertEqual(resolution.request.metadata["owner_user_id"], "user-1")
        self.assertEqual(resolution.turn_request.workspace_id, "workspace-1")
        self.assertEqual(resolution.turn_request.message, "Review inbox")
        self.assertEqual(resolution.turn_request.actor.id, "user-1")


if __name__ == "__main__":
    unittest.main()
