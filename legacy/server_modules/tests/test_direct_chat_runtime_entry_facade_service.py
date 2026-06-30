import unittest
from unittest import mock

from server_modules import direct_chat_runtime_entry_facade_service as service


class DirectChatRuntimeEntryFacadeServiceTests(unittest.TestCase):
    def test_collect_direct_operator_reply_delegates(self) -> None:
        expected = {"reply": "Hello"}

        with mock.patch.object(
            service.direct_chat_runtime_service,
            "collect_direct_operator_reply",
            return_value=expected,
        ) as collect_reply:
            payload = service.collect_direct_operator_reply(
                services=mock.sentinel.services,
                message="hello",
                workspace_id="default",
                requested_model="gpt-5.4",
                requested_provider="openai",
            )

        collect_reply.assert_called_once_with(
            services=mock.sentinel.services,
            message="hello",
            workspace_id="default",
            requested_model="gpt-5.4",
            requested_provider="openai",
        )
        self.assertIs(payload, expected)

    def test_execute_chat_turn_delegates(self) -> None:
        expected = {"reply": "Hi"}
        stream_sink = mock.Mock()

        with mock.patch.object(
            service.direct_chat_runtime_service,
            "execute_chat_turn",
            return_value=expected,
        ) as execute_turn:
            payload = service.execute_chat_turn(
                services=mock.sentinel.services,
                session_ctx={"workspace_id": "default"},
                message="hello",
                stream_sink=stream_sink,
                request_meta={"workspace_id": "default"},
            )

        execute_turn.assert_called_once_with(
            services=mock.sentinel.services,
            session_ctx={"workspace_id": "default"},
            message="hello",
            stream_sink=stream_sink,
            request_meta={"workspace_id": "default"},
        )
        self.assertIs(payload, expected)


if __name__ == "__main__":
    unittest.main()
