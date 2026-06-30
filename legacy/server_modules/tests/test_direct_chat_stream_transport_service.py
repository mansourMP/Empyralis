import unittest

from server_modules import direct_chat_stream_transport_service


class DirectChatStreamTransportServiceTests(unittest.TestCase):
    def test_chat_stream_payload_unwraps_trace_payload(self):
        event_name, payload = direct_chat_stream_transport_service.chat_stream_payload(
            {
                "type": "trace",
                "payload": {
                    "trace_id": "trace-1",
                    "event_type": "plan.started",
                },
            }
        )

        self.assertEqual(event_name, "trace")
        self.assertEqual(payload["trace_id"], "trace-1")
        self.assertEqual(payload["event_type"], "plan.started")

    def test_chat_stream_error_payload_exposes_terminal_error_envelope(self):
        payload = direct_chat_stream_transport_service.chat_stream_error_payload(
            "Chat exploded.",
            request_id="req-1",
            trace_id="trace-1",
        )

        self.assertEqual(payload["kind"], "terminal_error")
        self.assertEqual(payload["error"], "direct_chat_terminal_failure")
        self.assertEqual(payload["terminal_error"]["error"]["class"], "internal_error")
        self.assertEqual(payload["terminal_error"]["request_id"], "req-1")
        self.assertEqual(payload["terminal_error"]["trace_id"], "trace-1")

    def test_normalize_chat_stream_cursor_defaults_invalid_values_to_zero(self):
        self.assertEqual(direct_chat_stream_transport_service.normalize_chat_stream_cursor(None), 0)
        self.assertEqual(direct_chat_stream_transport_service.normalize_chat_stream_cursor(""), 0)
        self.assertEqual(direct_chat_stream_transport_service.normalize_chat_stream_cursor("abc"), 0)
        self.assertEqual(direct_chat_stream_transport_service.normalize_chat_stream_cursor("-5"), 0)
        self.assertEqual(direct_chat_stream_transport_service.normalize_chat_stream_cursor("7"), 7)


if __name__ == "__main__":
    unittest.main()
