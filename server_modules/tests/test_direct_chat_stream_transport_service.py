import threading
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

    def _session(self):
        return {
            "condition": threading.Condition(),
            "events": [],
            "completed": False,
        }

    def test_iter_chat_stream_events_yields_keepalive_while_idle(self):
        # Regression test for the production hang (2026-08-06): a turn
        # blocked on a slow gateway tool call left this generator with
        # nothing to send for minutes, and an idle intermediary (Cloudflare)
        # tore the connection down long before the turn actually finished.
        # With no pending events and the session not yet completed, the
        # generator must keep producing SOME byte on the wire — a bare SSE
        # comment line — every idle_wait_seconds, instead of going silent.
        session = self._session()
        gen = direct_chat_stream_transport_service.iter_chat_stream_events(
            session,
            last_event_id=None,
            normalize_cursor=direct_chat_stream_transport_service.normalize_chat_stream_cursor,
            idle_wait_seconds=0.01,
        )
        try:
            first = next(gen)
            second = next(gen)
        finally:
            gen.close()

        self.assertEqual(first, b": keepalive\n\n")
        self.assertEqual(second, b": keepalive\n\n")
        # A comment line (leading ":") with no "data:" line is exactly what
        # AgentChat.tsx's parseSseBlock already discards — assert the shape
        # stays within that contract rather than just eyeballing the bytes.
        self.assertTrue(first.decode("utf-8").startswith(":"))
        self.assertNotIn(b"data:", first)

    def test_iter_chat_stream_events_stops_cleanly_when_completed_with_nothing_pending(self):
        session = self._session()
        session["completed"] = True
        gen = direct_chat_stream_transport_service.iter_chat_stream_events(
            session,
            last_event_id=None,
            normalize_cursor=direct_chat_stream_transport_service.normalize_chat_stream_cursor,
            idle_wait_seconds=0.01,
        )

        self.assertEqual(list(gen), [])

    def test_iter_chat_stream_events_yields_pending_event_without_keepalive_noise(self):
        session = self._session()
        session["events"] = [
            {"id": "1", "seq": 1, "event": "chunk", "payload": {"delta": "hi"}},
        ]
        session["completed"] = True
        gen = direct_chat_stream_transport_service.iter_chat_stream_events(
            session,
            last_event_id=None,
            normalize_cursor=direct_chat_stream_transport_service.normalize_chat_stream_cursor,
            idle_wait_seconds=0.01,
        )

        chunks = list(gen)

        self.assertEqual(
            chunks,
            [
                b"id: 1\n",
                b"event: chunk\n",
                b'data: {"delta": "hi"}\n\n',
            ],
        )

    def test_iter_chat_stream_events_recovers_from_idle_once_event_arrives(self):
        # Confirms the keepalive path doesn't swallow or delay a real event
        # that arrives after a period of silence.
        session = self._session()

        def _publish_after_delay():
            with session["condition"]:
                session["events"].append(
                    {"id": "1", "seq": 1, "event": "final", "payload": {"reply": "done"}}
                )
                session["completed"] = True
                session["condition"].notify_all()

        timer = threading.Timer(0.03, _publish_after_delay)
        timer.start()
        try:
            gen = direct_chat_stream_transport_service.iter_chat_stream_events(
                session,
                last_event_id=None,
                normalize_cursor=direct_chat_stream_transport_service.normalize_chat_stream_cursor,
                idle_wait_seconds=0.01,
            )
            chunks = list(gen)
        finally:
            timer.join()

        # At least one keepalive fired while waiting, and the real event
        # still made it through afterward, framed the same as before.
        self.assertIn(b": keepalive\n\n", chunks)
        self.assertEqual(chunks[-3:], [
            b"id: 1\n",
            b"event: final\n",
            b'data: {"reply": "done"}\n\n',
        ])


if __name__ == "__main__":
    unittest.main()
