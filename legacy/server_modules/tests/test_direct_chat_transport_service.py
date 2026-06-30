import unittest

from server_modules import direct_chat_transport_service as service


class DirectChatTransportServiceTests(unittest.TestCase):
    def test_direct_chat_request_signature_is_stable_for_core_payload(self) -> None:
        body = {
            "workspace_id": "default",
            "thread_id": "thread-1",
            "message": "hello",
            "provider": "openai",
            "model": "gpt-test",
        }

        first = service.direct_chat_request_signature(body)
        second = service.direct_chat_request_signature(dict(body))

        self.assertEqual(first, second)
        self.assertEqual(len(first), 24)

    def test_direct_chat_stream_key_uses_signature_fallback(self) -> None:
        body = {
            "thread_id": "thread-1",
            "message": "hello",
            "provider": "openai",
            "model": "gpt-test",
        }

        session_key, thread_id, client_request_id = service.direct_chat_stream_key(
            {"user_id": "user-1"},
            body,
        )

        self.assertEqual(thread_id, "thread-1")
        self.assertEqual(client_request_id, service.direct_chat_request_signature(body))
        self.assertEqual(session_key, f"user-1:default:thread-1:{client_request_id}")

    def test_direct_chat_stream_key_is_scoped_by_workspace(self) -> None:
        first, _, _ = service.direct_chat_stream_key(
            {"user_id": "user-1"},
            {"workspace_id": "workspace-a", "thread_id": "thread-1", "client_request_id": "req-1"},
        )
        second, _, _ = service.direct_chat_stream_key(
            {"user_id": "user-1"},
            {"workspace_id": "workspace-b", "thread_id": "thread-1", "client_request_id": "req-1"},
        )

        self.assertNotEqual(first, second)

    def test_direct_chat_actor_key_uses_normalized_user_token(self) -> None:
        actor_key = service.direct_chat_actor_key(
            {"email": "User@Example.com"},
            "workspace-1",
            "thread-1",
        )

        self.assertEqual(actor_key, "user@example.com:workspace-1:thread-1")


if __name__ == "__main__":
    unittest.main()
