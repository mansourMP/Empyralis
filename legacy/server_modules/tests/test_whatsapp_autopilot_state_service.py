import threading
import unittest

from server_modules.connectors.whatsapp_autopilot_state_service import WhatsAppAutopilotStateService


class WhatsAppAutopilotStateServiceTests(unittest.TestCase):
    def _make_service(self, **overrides) -> WhatsAppAutopilotStateService:
        state = overrides.pop("state", {})
        writes = overrides.pop("writes", [])
        vault_payload = overrides.pop("vault_payload", {"credentials": []})
        return WhatsAppAutopilotStateService(
            state=state,
            lock=threading.RLock(),
            read_json=overrides.pop("read_json", lambda path, default: default),
            write_json=lambda path, payload: writes.append((path, payload)),
            state_file="whatsapp-state.json",
            utc_now_iso=overrides.pop("utc_now_iso", lambda: "2026-04-04T00:00:00Z"),
            classify_error=overrides.pop("classify_error", lambda detail: "runtime"),
            normalize_workspace_id=overrides.pop("normalize_workspace_id", lambda value: str(value or "")),
            resolve_workspace_scope=overrides.pop(
                "resolve_workspace_scope",
                lambda entry, fallback_workspace_id, detail_prefix: str(entry.get("workspace_id") or fallback_workspace_id or ""),
            ),
            load_vault=overrides.pop("load_vault", lambda: vault_payload),
            workspace_visible=overrides.pop("workspace_visible", lambda workspace_id, requested_ws: True),
            connector_paused=overrides.pop("connector_paused", lambda item: False),
            resolve_vault_credential=overrides.pop("resolve_vault_credential", lambda credential_id, workspace_id: {}),
            normalize_whatsapp_number=overrides.pop("normalize_whatsapp_number", lambda value: str(value or "")),
            enabled=overrides.pop("enabled", True),
            default_profile="assistant",
            require_prefix=False,
            prefix="/empyralis",
            run_timeout_seconds=180,
            max_reply_chars=1200,
        )

    def test_load_and_persist_state(self) -> None:
        writes = []
        service = self._make_service(
            writes=writes,
            read_json=lambda path, default: {
                "state": {
                    "connectors": {"c1": {"last_action": "run"}},
                    "processed_messages": 5,
                    "runs_started": 2,
                    "last_inbound_at": "t1",
                    "last_error": "boom",
                    "last_error_at": "t2",
                    "last_error_category": "runtime",
                    "last_error_source": "webhook",
                    "error_count": 3,
                    "consecutive_errors": 1,
                }
            },
        )
        service.load_state()
        self.assertEqual(service.state["processed_messages"], 5)
        service.persist_state()
        self.assertEqual(writes[0][1]["state"]["processed_messages"], 5)

    def test_mark_error_and_mark_inbound_update_state(self) -> None:
        writes = []
        service = self._make_service(writes=writes, state={})
        service.mark_error("boom", source="webhook")
        self.assertEqual(service.state["last_error"], "boom")
        self.assertEqual(service.state["last_error_category"], "runtime")
        service.mark_inbound(clear_error=True)
        self.assertIsNone(service.state["last_error"])
        self.assertGreaterEqual(len(writes), 2)

    def test_list_connector_entries_dedupes_identity(self) -> None:
        service = self._make_service(
            vault_payload={
                "credentials": [
                    {"id": "a", "provider": "whatsapp_twilio", "workspace_id": "ws", "label": "B"},
                    {"id": "b", "provider": "whatsapp_twilio", "workspace_id": "ws", "label": "A"},
                ]
            },
            resolve_vault_credential=lambda credential_id, workspace_id: {
                "account_sid": "AC1",
                "from_number": "+100",
                "to_number": "+200",
            },
        )
        entries = service.list_connector_entries("ws")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["id"], "a")

    def test_snapshot_includes_connectors_and_counts(self) -> None:
        service = self._make_service(
            state={
                "active": True,
                "connectors": {
                    "c1": {"last_error": "boom"},
                    "c2": {},
                },
                "processed_messages": 4,
                "runs_started": 1,
            },
            load_vault=lambda: {"credentials": []},
        )
        snapshot = service.snapshot(include_connectors=True, requested_workspace_id="ws")
        self.assertTrue(snapshot["enabled"])
        self.assertEqual(snapshot["connector_state_count"], 2)
        self.assertEqual(snapshot["connector_error_count"], 1)
        self.assertEqual(snapshot["connectors"], service.state["connectors"])

    def test_connector_match_returns_matching_entry(self) -> None:
        service = self._make_service(
            vault_payload={
                "credentials": [
                    {"id": "conn-1", "provider": "whatsapp_twilio", "workspace_id": "ws-1", "label": "Main"}
                ]
            },
            resolve_vault_credential=lambda credential_id, workspace_id: {
                "account_sid": "AC123",
                "from_number": "whatsapp:+200",
                "to_number": "whatsapp:+100",
            },
            normalize_whatsapp_number=lambda value: str(value or "").strip().lower(),
        )
        matched = service.connector_match("AC123", "whatsapp:+100", "whatsapp:+200")

        self.assertIsNotNone(matched)
        self.assertEqual(matched["connector_id"], "conn-1")
        self.assertEqual(matched["workspace_id"], "ws-1")
        self.assertEqual(service.state["connectors_seen"], 1)

    def test_mark_processed_message_is_duplicate_safe(self) -> None:
        service = self._make_service(state={})

        self.assertTrue(service.mark_processed_message("conn-1", "SM123"))
        self.assertFalse(service.mark_processed_message("conn-1", "SM123"))


if __name__ == "__main__":
    unittest.main()
