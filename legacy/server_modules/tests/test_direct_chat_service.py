import importlib
import unittest
from unittest.mock import patch

from server_modules.agent_turn import build_direct_chat_turn_request, serialize_agent_turn_request
from server_modules.agent_trace_service import TraceContext
from server_modules.direct_chat_service import (
    DirectChatExecutionServices,
    direct_chat_request_signature,
    direct_chat_stream_key,
    build_direct_chat_event_producer,
    build_direct_chat_request_meta,
    execute_direct_chat_turn_request,
)


class _DummyManager:
    def __init__(self) -> None:
        self.calls = []
        self.eviction_calls = 0

    def evict_idle_handles(self, **kwargs):
        self.eviction_calls += 1
        return 0

    def iter_turn_events(self, **kwargs):
        self.calls.append(kwargs)
        yield {
            "type": "final",
            "payload": {
                "reply": "session-manager",
                "actions": [],
                "mode": "answer",
                "error": "",
            },
        }


class DirectChatServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        global DirectChatExecutionServices
        global direct_chat_request_signature
        global direct_chat_stream_key
        global build_direct_chat_event_producer
        global build_direct_chat_request_meta
        global execute_direct_chat_turn_request

        direct_chat_service = importlib.import_module("server_modules.direct_chat_service")
        DirectChatExecutionServices = direct_chat_service.DirectChatExecutionServices
        direct_chat_request_signature = direct_chat_service.direct_chat_request_signature
        direct_chat_stream_key = direct_chat_service.direct_chat_stream_key
        build_direct_chat_event_producer = direct_chat_service.build_direct_chat_event_producer
        build_direct_chat_request_meta = direct_chat_service.build_direct_chat_request_meta
        execute_direct_chat_turn_request = direct_chat_service.execute_direct_chat_turn_request

    def test_direct_chat_stream_key_uses_request_signature_fallback(self):
        body = {
            "thread_id": "thread-1",
            "message": "hello",
            "provider": "openai",
            "model": "gpt-test",
        }

        signature = direct_chat_request_signature(body)
        session_key, thread_id, client_request_id = direct_chat_stream_key(
            {"user_id": "user-1"},
            body,
        )

        self.assertEqual(thread_id, "thread-1")
        self.assertEqual(client_request_id, signature)
        self.assertEqual(session_key, f"user-1:default:thread-1:{signature}")

    def test_build_direct_chat_event_producer_uses_session_manager_when_enabled(self):
        manager = _DummyManager()
        services = DirectChatExecutionServices(
            chat_stream_key=lambda current_user, body: ("user-1:thread-1:req-1", "thread-1", "req-1"),
            session_manager_enabled=lambda: True,
            session_manager_factory=lambda: manager,
            build_direct_operator_reply=lambda **kwargs: {"reply": "direct"},
            build_chat_turn_event_stream=lambda **kwargs: iter(()),
        )

        producer = build_direct_chat_event_producer(
            current_user={"user_id": "user-1", "email": "user@example.com"},
            body={"thread_id": "thread-1", "provider": "openai", "model": "gpt-test"},
            message="hello",
            workspace_id="default",
            session_key="user-1:thread-1:req-1",
            thread_id="thread-1",
            client_request_id="req-1",
            services=services,
        )
        events = list(producer)

        self.assertEqual(events[-1]["payload"]["reply"], "session-manager")
        self.assertEqual(len(manager.calls), 1)
        call = manager.calls[0]
        self.assertEqual(call["session_id"], "user-1:default:thread-1")
        self.assertEqual(call["actor_key"], "user-1:default:thread-1")
        self.assertEqual(call["workspace_id"], "default")
        self.assertEqual(call["user_id"], "user-1")
        self.assertEqual(call["request_meta"]["request_id"], "req-1")
        self.assertEqual(call["request_meta"]["agent_turn_request"]["workspace_id"], "default")
        self.assertEqual(call["request_meta"]["agent_turn_request"]["session_id"], "thread-1")
        self.assertEqual(call["request_meta"]["agent_turn_request"]["message"], "hello")
        self.assertEqual(manager.eviction_calls, 1)

    def test_build_direct_chat_event_producer_logs_cleanup_degradation(self):
        manager = _DummyManager()

        def _broken_cleanup(**kwargs):
            raise RuntimeError("cleanup failed")

        manager.evict_idle_handles = _broken_cleanup
        services = DirectChatExecutionServices(
            chat_stream_key=lambda current_user, body: ("user-1:thread-1:req-1", "thread-1", "req-1"),
            session_manager_enabled=lambda: True,
            session_manager_factory=lambda: manager,
            build_direct_operator_reply=lambda **kwargs: {"reply": "direct"},
            build_chat_turn_event_stream=lambda **kwargs: iter(()),
        )

        with patch("server_modules.direct_chat_service.failure_policy_service.log_degraded_operation") as degrade_mock:
            producer = build_direct_chat_event_producer(
                current_user={"user_id": "user-1", "email": "user@example.com"},
                body={"thread_id": "thread-1", "provider": "openai", "model": "gpt-test"},
                message="hello",
                workspace_id="default",
                session_key="user-1:thread-1:req-1",
                thread_id="thread-1",
                client_request_id="req-1",
                services=services,
            )
            list(producer)

        degrade_mock.assert_called_once()
        self.assertEqual(degrade_mock.call_args.kwargs["code"], "direct_chat_session_cleanup_failed")

    def test_build_direct_chat_event_producer_passes_bound_session_context_without_session_manager(self):
        captured = {}

        def _build_direct_operator_reply(**kwargs):
            captured["kwargs"] = kwargs
            return {"reply": "direct"}

        services = DirectChatExecutionServices(
            chat_stream_key=lambda current_user, body: ("user-1:thread-1:req-1", "thread-1", "req-1"),
            session_manager_enabled=lambda: False,
            session_manager_factory=lambda: _DummyManager(),
            build_direct_operator_reply=_build_direct_operator_reply,
            build_chat_turn_event_stream=lambda **kwargs: iter(()),
        )

        payload = build_direct_chat_event_producer(
            current_user={"user_id": "user-1", "email": "user@example.com"},
            body={"thread_id": "thread-1", "provider": "openai", "model": "gpt-test"},
            message="hello",
            workspace_id="default",
            session_key="user-1:thread-1:req-1",
            thread_id="thread-1",
            client_request_id="req-1",
            services=services,
        )

        self.assertEqual(payload["reply"], "direct")
        self.assertEqual(captured["kwargs"]["session_ctx"]["workspace_id"], "default")
        self.assertEqual(captured["kwargs"]["session_ctx"]["thread_id"], "thread-1")
        self.assertEqual(captured["kwargs"]["session_ctx"]["request_id"], "req-1")
        self.assertEqual(captured["kwargs"]["session_ctx"]["client_request_id"], "req-1")
        self.assertEqual(captured["kwargs"]["session_ctx"]["agent_turn_request"]["message"], "hello")

    def test_build_direct_chat_request_meta_accepts_serialized_turn_request(self):
        turn_request = build_direct_chat_turn_request(
            current_user={"user_id": "user-1"},
            body={"thread_id": "thread-1"},
            workspace_id="default",
            thread_id="thread-1",
            client_request_id="req-1",
            message="hello",
        )

        request_meta = build_direct_chat_request_meta(
            body={"thread_id": "thread-1"},
            workspace_id="default",
            thread_id="thread-1",
            client_request_id="req-1",
            agent_turn_request=serialize_agent_turn_request(turn_request),
        )

        self.assertEqual(request_meta["agent_turn_request"]["workspace_id"], "default")
        self.assertEqual(request_meta["agent_turn_request"]["session_id"], "thread-1")
        self.assertEqual(request_meta["agent_turn_request"]["message"], "hello")

    def test_build_direct_chat_request_meta_uses_turn_request_runtime_hints(self):
        turn_request = build_direct_chat_turn_request(
            current_user={"user_id": "user-1"},
            body={
                "thread_id": "thread-1",
                "provider": "ollama",
                "model": "qwen2.5:1.5b",
                "reasoning_effort": "medium",
                "max_iterations": 4,
            },
            workspace_id="default",
            thread_id="thread-1",
            client_request_id="req-1",
            message="Use the local shell tool",
        )

        request_meta = build_direct_chat_request_meta(
            body={"thread_id": "thread-1"},
            workspace_id="default",
            thread_id="thread-1",
            client_request_id="req-1",
            agent_turn_request=serialize_agent_turn_request(turn_request),
        )

        self.assertEqual(request_meta["provider"], "ollama")
        self.assertEqual(request_meta["model"], "qwen2.5:1.5b")
        self.assertEqual(request_meta["reasoning_effort"], "medium")
        self.assertEqual(request_meta["max_iterations"], 4)
        self.assertEqual(request_meta["runtime_options"]["provider"], "ollama")
        self.assertEqual(request_meta["runtime_options"]["model"], "qwen2.5:1.5b")

    def test_build_direct_chat_request_meta_includes_serializable_trace_reference(self):
        turn_request = build_direct_chat_turn_request(
            current_user={"user_id": "user-1"},
            body={"thread_id": "thread-1"},
            workspace_id="default",
            thread_id="thread-1",
            client_request_id="req-1",
            message="hello",
        )
        trace_context = TraceContext(
            trace_id="trace-1",
            workspace_id="default",
            tenant_id="tenant-1",
            thread_id="thread-1",
            run_id=None,
            root_agent_id="sage",
        )

        request_meta = build_direct_chat_request_meta(
            body={"thread_id": "thread-1"},
            workspace_id="default",
            thread_id="thread-1",
            client_request_id="req-1",
            agent_turn_request=serialize_agent_turn_request(turn_request),
            trace_context=trace_context,
        )

        self.assertEqual(request_meta["trace"]["trace_id"], "trace-1")
        self.assertEqual(request_meta["trace"]["tenant_id"], "tenant-1")
        self.assertEqual(request_meta["trace"]["root_agent_id"], "sage")

    def test_execute_direct_chat_turn_request_returns_stream_plan(self):
        turn_request = build_direct_chat_turn_request(
            current_user={"user_id": "user-1"},
            body={"thread_id": "thread-1"},
            workspace_id="default",
            thread_id="thread-1",
            client_request_id="req-1",
            message="hello",
        )
        services = DirectChatExecutionServices(
            chat_stream_key=lambda current_user, body: ("user-1:thread-1:req-1", "thread-1", "req-1"),
            session_manager_enabled=lambda: False,
            session_manager_factory=lambda: _DummyManager(),
            build_direct_operator_reply=lambda **kwargs: {"reply": "direct"},
            build_chat_turn_event_stream=lambda **kwargs: iter(()),
        )

        execution = __import__("asyncio").run(
            execute_direct_chat_turn_request(
                turn_request=turn_request,
                current_user={"user_id": "user-1"},
                services=services,
                chat_body={"thread_id": "thread-1", "client_request_id": "req-1"},
            )
        )

        self.assertEqual(execution["kind"], "direct_chat_stream")
        self.assertEqual(execution["workspace_id"], "default")
        self.assertEqual(execution["thread_id"], "thread-1")
        self.assertEqual(execution["client_request_id"], "req-1")
        self.assertTrue(callable(execution["producer"]))


if __name__ == "__main__":
    unittest.main()
