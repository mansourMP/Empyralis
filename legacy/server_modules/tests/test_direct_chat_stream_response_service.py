import asyncio
import unittest
from unittest import mock

from fastapi.responses import JSONResponse, StreamingResponse

from server_modules.direct_chat_stream_response_service import (
    DirectChatStreamResponseServices,
    build_direct_chat_stream_response,
)


class _Resolution:
    def __init__(self) -> None:
        self.turn_request = {"kind": "direct_chat"}
        self.workspace_id = "default"
        self.thread_id = "thread-1"
        self.client_request_id = "req-1"


class DirectChatStreamResponseServiceTests(unittest.TestCase):
    def setUp(self):
        self._rust_patch = mock.patch(
            "server_modules.direct_chat_stream_response_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "operation": "stream_chat",
                "next_action": "start_chat_stream",
            },
        )
        self.mock_rust = self._rust_patch.start()

    def tearDown(self):
        self._rust_patch.stop()

    def _services(self, **overrides):
        async def _execute_agent_turn_request(**kwargs):
            return {
                "workspace_id": "default",
                "session_key": "session-1",
                "thread_id": "thread-1",
                "client_request_id": "req-1",
                "producer": lambda: iter(()),
            }

        base = DirectChatStreamResponseServices(
            resolve_direct_chat_turn_request=lambda **kwargs: _Resolution(),
            chat_stream_request_signature=lambda **kwargs: "sig",
            execute_agent_turn_request=_execute_agent_turn_request,
            build_turn_execution_services=lambda **kwargs: {"services": kwargs},
            run_execution_services=lambda: "run-services",
            direct_chat_execution_services=lambda: "chat-services",
            get_chat_stream_state=lambda db_path, key: None,
            chat_stream_state_db_path=lambda: "/tmp/state.db",
            get_or_create_chat_stream_session=lambda *args, **kwargs: {"producer_started": False},
            extract_direct_chat_error_response=lambda event: None,
            start_chat_stream_producer=lambda session, producer: None,
            iter_chat_stream_events=lambda session, last_event_id: iter([b"data: ok\n\n"]),
        )
        for key, value in overrides.items():
            setattr(base, key, value)
        return base

    def test_returns_chat_unavailable_when_producer_finishes_immediately(self):
        payload = asyncio.run(
            build_direct_chat_stream_response(
                current_user={"user_id": "user-1"},
                body={"message": "hello"},
                last_event_id=None,
                services=self._services(),
            )
        )

        self.assertIsInstance(payload, JSONResponse)
        self.assertEqual(payload.status_code, 500)

    def test_returns_immediate_provider_error_response(self):
        async def _execute_agent_turn_request(**kwargs):
            return {
                "workspace_id": "default",
                "session_key": "session-1",
                "thread_id": "thread-1",
                "client_request_id": "req-1",
                "producer": lambda: iter([{"type": "final", "payload": {"error": "no_provider"}}]),
            }

        services = self._services(
            execute_agent_turn_request=_execute_agent_turn_request,
            extract_direct_chat_error_response=lambda event: {"error": "no_provider", "message": "No AI provider configured"},
        )

        payload = asyncio.run(
            build_direct_chat_stream_response(
                current_user={"user_id": "user-1"},
                body={"message": "hello"},
                last_event_id=None,
                services=services,
            )
        )

        self.assertIsInstance(payload, JSONResponse)
        self.assertEqual(payload.status_code, 409)

    def test_preflights_new_session_even_when_persisted_state_exists(self):
        started = {}

        async def _execute_agent_turn_request(**kwargs):
            return {
                "workspace_id": "default",
                "session_key": "session-1",
                "thread_id": "thread-1",
                "client_request_id": "req-1",
                "producer": lambda: iter([{"type": "final", "payload": {"error": "no_provider"}}]),
            }

        services = self._services(
            execute_agent_turn_request=_execute_agent_turn_request,
            get_chat_stream_state=lambda db_path, key: {"status": "retryable"},
            get_or_create_chat_stream_session=lambda *args, **kwargs: {"producer_started": False},
            extract_direct_chat_error_response=lambda event: {"error": "no_provider", "message": "No AI provider configured"},
            start_chat_stream_producer=lambda session, producer: started.setdefault("called", True),
        )

        payload = asyncio.run(
            build_direct_chat_stream_response(
                current_user={"user_id": "user-1"},
                body={"message": "hello"},
                last_event_id=None,
                services=services,
            )
        )

        self.assertIsInstance(payload, JSONResponse)
        self.assertEqual(payload.status_code, 409)
        self.assertNotIn("called", started)

    def test_returns_streaming_response_for_live_stream(self):
        started = {}
        session = {"producer_started": False}

        async def _execute_agent_turn_request(**kwargs):
            return {
                "workspace_id": "default",
                "session_key": "session-1",
                "thread_id": "thread-1",
                "client_request_id": "req-1",
                "producer": lambda: iter([{"type": "chunk", "delta": "Hello"}]),
            }

        services = self._services(
            execute_agent_turn_request=_execute_agent_turn_request,
            get_or_create_chat_stream_session=lambda *args, **kwargs: session,
            start_chat_stream_producer=lambda session, producer: started.setdefault("called", True),
        )

        payload = asyncio.run(
            build_direct_chat_stream_response(
                current_user={"user_id": "user-1"},
                body={"message": "hello"},
                last_event_id="0",
                services=services,
            )
        )

        self.assertIsInstance(payload, StreamingResponse)
        self.assertTrue(started["called"])
        self.assertEqual(session["metadata"]["assistant_turn"]["workspace_id"], "default")
        self.assertEqual(session["metadata"]["assistant_turn"]["thread_id"], "thread-1")
        self.assertEqual(session["metadata"]["assistant_turn"]["request_id"], "req-1")

    def test_blocks_when_rust_stream_chat_next_action_is_unexpected(self):
        async def _execute_agent_turn_request(**kwargs):
            return {
                "workspace_id": "default",
                "session_key": "session-1",
                "thread_id": "thread-1",
                "client_request_id": "req-1",
                "producer": lambda: iter([{"type": "chunk", "delta": "Hello"}]),
            }

        services = self._services(
            execute_agent_turn_request=_execute_agent_turn_request,
            get_or_create_chat_stream_session=lambda *args, **kwargs: {"producer_started": False},
        )
        self.mock_rust.return_value = {
            "ok": True,
            "decision": "allow",
            "operation": "stream_chat",
            "next_action": "get_run",
        }

        with self.assertRaisesRegex(Exception, "unexpected next_action"):
            asyncio.run(
                build_direct_chat_stream_response(
                    current_user={"user_id": "user-1"},
                    body={"message": "hello"},
                    last_event_id="0",
                    services=services,
                )
            )


if __name__ == "__main__":
    unittest.main()
