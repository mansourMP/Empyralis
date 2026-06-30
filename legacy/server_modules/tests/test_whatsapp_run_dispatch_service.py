import unittest

from server_modules.connectors.whatsapp_run_dispatch_service import WhatsAppRunDispatchService


class WhatsAppRunDispatchServiceTests(unittest.TestCase):
    def _make_service(self, **overrides) -> WhatsAppRunDispatchService:
        return WhatsAppRunDispatchService(
            default_timeout_seconds=60,
            default_max_reply_chars=240,
            send_ack=overrides.pop("send_ack", True),
            include_run_meta=overrides.pop("include_run_meta", lambda: False),
            truncate_one_line=lambda text, limit: str(text or "").strip()[:limit],
            poll_run_terminal_result=overrides.pop(
                "poll_run_terminal_result",
                lambda run_id, max_reply_chars=None: {
                    "ready": True,
                    "status": "completed",
                    "summary": "Run finished.",
                },
            ),
            run_reply_text=overrides.pop(
                "run_reply_text",
                lambda status, run_id, summary: f"{status}:{run_id}:{summary}",
            ),
            emit_channel_run_delivery_event=overrides.pop("emit_channel_run_delivery_event", lambda **kwargs: None),
            send_whatsapp_message=overrides.pop(
                "send_whatsapp_message",
                lambda **kwargs: {"sid": "SM123"},
            ),
            append_dead_letter=overrides.pop("append_dead_letter", lambda **kwargs: None),
            record_channel_event=overrides.pop("record_channel_event", lambda **kwargs: None),
            set_connector_state=overrides.pop("set_connector_state", lambda connector_id, payload: None),
            utc_now_iso=overrides.pop("utc_now_iso", lambda: "2026-04-04T00:00:00Z"),
            classify_error=overrides.pop("classify_error", lambda detail: "runtime"),
            log_error=overrides.pop("log_error", lambda message: None),
            mark_error=overrides.pop("mark_error", lambda detail: None),
            session_key_builder=overrides.pop("session_key_builder", lambda reply_to, from_number: f"{reply_to}:{from_number}"),
            safe_path_token=overrides.pop("safe_path_token", lambda value: str(value or "").replace(":", "_")),
        )

    def test_ack_text_includes_run_meta_when_enabled(self) -> None:
        service = self._make_service(include_run_meta=lambda: True)
        self.assertEqual(
            service.ack_text("run-1"),
            "⏣ Empyralis started your request.\nrun_id: run-1",
        )
        self.assertEqual(service.ack_text(""), "⏣ Empyralis started your request.")

    def test_deliver_final_response_records_success(self) -> None:
        events = []
        states = []
        service = self._make_service(
            record_channel_event=lambda **kwargs: events.append(kwargs),
            set_connector_state=lambda connector_id, payload: states.append((connector_id, payload)),
        )

        service.deliver_final_response(
            run_id="run-1",
            connector_id="conn-1",
            workspace_id="ws-1",
            profile={"id": "profile-1"},
            secret={
                "account_sid": "AC123",
                "auth_token": "token",
                "from_number": "whatsapp:+100",
            },
            reply_to_number="whatsapp:+200",
            status="completed",
            summary="Run finished.",
            provider_idempotency_key="whatsapp:conn-1:whatsapp:+200:run-1:message",
        )

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["direction"], "outbound")
        self.assertEqual(events[1]["event_type"], "run_completed")
        self.assertEqual(states[0][0], "conn-1")
        self.assertEqual(states[0][1]["last_error"], None)
        self.assertEqual(
            states[0][1]["last_delivery_idempotency_key"],
            "whatsapp:conn-1:whatsapp:+200:run-1:message",
        )
        self.assertEqual(states[0][1]["last_outbound_message_sid"], "SM123")

    def test_deliver_final_response_dead_letters_send_failure(self) -> None:
        dead_letters = []
        events = []
        states = []
        marked = []
        service = self._make_service(
            send_whatsapp_message=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("twilio down")),
            append_dead_letter=lambda **kwargs: dead_letters.append(kwargs),
            record_channel_event=lambda **kwargs: events.append(kwargs),
            set_connector_state=lambda connector_id, payload: states.append((connector_id, payload)),
            mark_error=lambda detail: marked.append(detail),
            classify_error=lambda detail: "network",
        )

        with self.assertRaisesRegex(RuntimeError, "twilio down"):
            service.deliver_final_response(
                run_id="run-2",
                connector_id="conn-2",
                workspace_id="ws-2",
                profile={"id": "profile-2"},
                secret={
                    "account_sid": "AC999",
                    "auth_token": "token",
                    "from_number": "whatsapp:+300",
                },
                reply_to_number="whatsapp:+400",
                status="failed",
                summary="twilio down",
                provider_idempotency_key="whatsapp:conn-2:whatsapp:+400:run-2:message",
            )

        self.assertEqual(len(dead_letters), 1)
        self.assertEqual(events[0]["event_type"], "error")
        self.assertEqual(states[0][1]["last_error_category"], "network")
        self.assertEqual(
            states[0][1]["last_delivery_idempotency_key"],
            "whatsapp:conn-2:whatsapp:+400:run-2:message",
        )
        self.assertEqual(marked, ["twilio down"])

    def test_schedule_final_delivery_emits_outbox_event(self) -> None:
        emitted = []
        service = self._make_service(emit_channel_run_delivery_event=lambda **kwargs: emitted.append(kwargs))

        service.schedule_final_delivery(
            workspace_id="ws-1",
            connector_id="conn-1",
            run_id="run-1",
            reply_to_number="whatsapp:+200",
            trace_id="trace-1",
        )

        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0]["channel"], "whatsapp")
        self.assertEqual(emitted[0]["payload"]["reply_to_number"], "whatsapp:+200")
        self.assertEqual(
            emitted[0]["payload"]["delivery"]["provider_idempotency_key"],
            "whatsapp:conn-1:whatsapp:+200:run-1:message",
        )


if __name__ == "__main__":
    unittest.main()
