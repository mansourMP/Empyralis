import threading
import unittest

from server_modules.connectors.whatsapp_autopilot_service_registry import WhatsAppAutopilotServiceRegistry


class WhatsAppAutopilotServiceRegistryTests(unittest.TestCase):
    def _make_registry(self) -> WhatsAppAutopilotServiceRegistry:
        return WhatsAppAutopilotServiceRegistry(
            state={},
            lock=threading.RLock(),
            read_json=lambda path, default: default,
            write_json=lambda path, payload: None,
            state_file="whatsapp-state.json",
            utc_now_iso=lambda: "2026-04-05T00:00:00Z",
            classify_error=lambda detail: "runtime",
            normalize_workspace_id=lambda value: str(value or ""),
            load_vault=lambda: {"credentials": []},
            workspace_visible=lambda workspace_id, requested_ws: True,
            connector_paused=lambda item: False,
            resolve_vault_credential=lambda credential_id, workspace_id: {},
            enabled=True,
            default_profile="assistant",
            require_prefix=False,
            prefix="/empyralis",
            run_timeout_seconds=180,
            max_reply_chars=1200,
            send_ack=True,
            require_explicit_opt_in=True,
            redact_event_text=True,
            retention_days=30,
            include_run_meta=lambda: True,
            truncate_one_line=lambda text, limit: str(text or "")[:limit],
            poll_run_terminal_result=lambda run_id, max_reply_chars=None: {
                "ready": True,
                "status": "completed",
                "summary": "done",
            },
            run_reply_text=lambda status, run_id, summary: f"{status}:{run_id}:{summary}",
            emit_channel_run_delivery_event=lambda **kwargs: None,
            append_dead_letter=lambda **kwargs: None,
            record_channel_event=lambda **kwargs: None,
            log_error=lambda message: None,
            safe_path_token=lambda value: str(value or "").replace(":", "_"),
            resolve_profile=lambda entry: {"id": "profile-1"},
            route_message=lambda body, profile: {"action": "ignore"},
            help_text=lambda profile: "help",
            runtime_status_text=lambda workspace_id: "status",
            approvals_list=lambda limit, workspace_id=None: {"events": []},
            approvals_text=lambda payload, prefix: "approvals",
            approval_resolve=lambda event_id, approved, note, workspace_id=None: {"ok": True},
            approval_result_text=lambda payload, approved: "approved",
            create_run=lambda **kwargs: {"run_id": "run-1"},
            session_key_builder=lambda inbound_from, inbound_to: f"{inbound_from}:{inbound_to}",
            default_chat_prefix="/empyralis",
        )

    def test_registry_caches_whatsapp_services(self) -> None:
        registry = self._make_registry()
        self.assertIs(registry.whatsapp_transport_service(), registry.whatsapp_transport_service())
        self.assertIs(registry.whatsapp_autopilot_state_service(), registry.whatsapp_autopilot_state_service())
        self.assertIs(registry.whatsapp_run_dispatch_service(), registry.whatsapp_run_dispatch_service())
        self.assertIs(registry.whatsapp_ingress_service(), registry.whatsapp_ingress_service())
        self.assertIs(registry.whatsapp_webhook_service(), registry.whatsapp_webhook_service())

    def test_webhook_service_wraps_registry_ingress_service(self) -> None:
        registry = self._make_registry()
        service = registry.whatsapp_webhook_service()
        ingress = registry.whatsapp_ingress_service()
        self.assertIs(service.ingress_service(), ingress)
        self.assertEqual(service.parse_form_urlencoded(b"Body=hello"), {"Body": "hello"})
        self.assertTrue(ingress.require_explicit_opt_in)
        self.assertTrue(ingress.redact_event_text)
        self.assertEqual(ingress.retention_days, 30)


if __name__ == "__main__":
    unittest.main()
