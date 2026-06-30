import unittest
from unittest.mock import patch

from server_modules.connectors.whatsapp_ingress_service import WhatsAppIngressService


class _PairingStub:
    def __init__(self, result=None):
        self.result = result or {
            "authorized": True,
            "status": "linked",
            "workspace_id": "ws-1",
            "link": {
                "link_id": "link-1",
                "scopes": ["chat", "whatsapp:chat", "whatsapp:opt_in"],
            },
        }
        self.calls = []

    def authorize_channel_message(self, **kwargs):
        self.calls.append(kwargs)
        return self.result

    def platform_connect_url(self, **kwargs):
        provider = str(kwargs.get("provider") or "whatsapp").strip() or "whatsapp"
        return f"https://app.empyralis.test/continue?source=channel_connect&channel={provider}"


class WhatsAppIngressServiceTests(unittest.TestCase):
    def _make_service(self, **overrides) -> WhatsAppIngressService:
        record_events = overrides.pop("record_events", [])
        state_patches = overrides.pop("state_patches", [])
        processed = overrides.pop("processed", [])
        persisted = overrides.pop("persisted", [])
        inbound_marks = overrides.pop("inbound_marks", [])
        errors = overrides.pop("errors", [])
        started = overrides.pop("started", [])
        pairing_service = overrides.pop("pairing_service", _PairingStub())

        dispatch_stub = overrides.pop(
            "dispatch_stub",
            type(
                "DispatchStub",
                (),
                {
                    "ack_text": staticmethod(lambda run_id: f"ack:{run_id}"),
                    "schedule_final_delivery": staticmethod(lambda **kwargs: started.append(kwargs)),
                },
            )(),
        )

        def connector_match(account_sid, inbound_from, inbound_to):
            return overrides.get("match")

        def _record_channel_event(**kwargs):
            record_events.append(kwargs)
            return {"id": f"evt-{len(record_events)}"}

        return WhatsAppIngressService(
            normalize_number=lambda value: value or "",
            session_key_builder=lambda inbound_from, inbound_to: f"{inbound_from}:{inbound_to}",
            safe_path_token=lambda value: str(value or "").replace(":", "_"),
            connector_match=connector_match,
            resolve_profile=overrides.get("resolve_profile", lambda entry: {"id": "profile-1", "prefix": "/empyralis"}),
            route_message=overrides.get("route_message", lambda body, profile: {"action": "ignore"}),
            help_text=lambda profile: "help",
            runtime_status_text=lambda workspace_id: "status",
            approvals_list=lambda limit, workspace_id=None: {"events": []},
            approvals_text=lambda payload, prefix: "approvals",
            approval_resolve=lambda event_id, approved, note, workspace_id=None: {"ok": True},
            approval_result_text=lambda payload, approved: "approved",
            create_run=lambda **kwargs: {"run_id": "run-1"},
            run_dispatch_service=lambda: dispatch_stub,
            record_channel_event=overrides.get("record_channel_event", _record_channel_event),
            set_connector_state=lambda connector_id, payload: state_patches.append((connector_id, payload)),
            persist_state=lambda: persisted.append(True),
            mark_processed_message=overrides.get("mark_processed_message", lambda connector_id, message_sid: True),
            increment_processed=lambda: processed.append(True),
            autopilot_activate=lambda: None,
            mark_inbound=lambda **kwargs: inbound_marks.append(kwargs),
            mark_error=lambda detail: errors.append(detail),
            utc_now_iso=lambda: "2026-04-04T00:00:00Z",
            default_chat_prefix="/empyralis",
            require_explicit_opt_in=overrides.get("require_explicit_opt_in", True),
            redact_event_text=overrides.get("redact_event_text", True),
            retention_days=overrides.get("retention_days", 30),
            channel_pairing_service=lambda: pairing_service,
        )

    def test_public_deployed_agent_free_text_routes_to_canonical_channel_router(self) -> None:
        record_events = []
        state_patches = []
        processed = []
        persisted = []
        service = self._make_service(
            record_events=record_events,
            state_patches=state_patches,
            processed=processed,
            persisted=persisted,
            match={
                "entry": {
                    "id": "entry-1",
                    "tenant_id": "tenant-1",
                    "label": "Parts Pro WhatsApp",
                    "metadata": {
                        "source": "deployed_agent",
                        "deployed_agent_id": "dagent-1",
                        "deployed_agent_name": "Parts Pro",
                        "channel_registry_bindings": {
                            "whatsapp": {"endpoint_key": "whatsapp:+200"},
                        },
                    },
                },
                "secret": {"from_number": "whatsapp:+200"},
                "connector_id": "conn-1",
                "workspace_id": "ws-1",
            },
        )

        with patch(
            "server_modules.connectors.whatsapp_ingress_service._route_inbound_channel_message",
            return_value={"status": "accepted", "reply": "Run accepted. I can help with brake pads.", "run_id": "run-123"},
        ) as route_mock:
            result = service.ingest_webhook(
                {
                    "AccountSid": "AC123",
                    "MessageSid": "SM123",
                    "From": "whatsapp:+100",
                    "To": "whatsapp:+200",
                    "Body": "Need brake pads",
                }
            )

        self.assertEqual(result, "Run accepted. I can help with brake pads.")
        self.assertEqual(route_mock.call_args.kwargs["channel_key"], "whatsapp")
        self.assertEqual(route_mock.call_args.kwargs["endpoint_key"], "whatsapp:+200")
        self.assertEqual(route_mock.call_args.kwargs["actor_id"], "whatsapp:+100")
        self.assertEqual(route_mock.call_args.kwargs["allow_master_fallback"], False)
        self.assertEqual(len(state_patches), 1)
        self.assertEqual(len(processed), 1)
        self.assertEqual(len(persisted), 1)
        self.assertTrue(any(evt["direction"] == "inbound" for evt in record_events))
        self.assertTrue(any(evt["direction"] == "outbound" for evt in record_events))

    def test_operator_run_action_schedules_durable_delivery(self) -> None:
        record_events = []
        state_patches = []
        processed = []
        persisted = []
        started = []
        service = self._make_service(
            record_events=record_events,
            state_patches=state_patches,
            processed=processed,
            persisted=persisted,
            started=started,
            match={
                "entry": {"id": "entry-1", "label": "Ops WhatsApp", "metadata": {}},
                "secret": {"from_number": "whatsapp:+100"},
                "connector_id": "conn-1",
                "workspace_id": "ws-1",
            },
            route_message=lambda body, profile: {"action": "run", "goal": "do it"},
        )

        response = service.ingest_webhook(
            {
                "AccountSid": "AC123",
                "MessageSid": "SM123",
                "From": "whatsapp:+100",
                "To": "whatsapp:+200",
                "Body": "run do it",
            }
        )

        self.assertEqual(response, "ack:run-1")
        self.assertEqual(len(started), 1)
        self.assertEqual(started[0]["run_id"], "run-1")
        self.assertEqual(len(state_patches), 1)
        self.assertEqual(len(processed), 1)
        self.assertEqual(len(persisted), 1)
        inbound_events = [evt for evt in record_events if evt["direction"] == "inbound"]
        self.assertEqual(inbound_events[0]["text"], "[redacted]")
        self.assertEqual(inbound_events[0]["metadata"]["link_id"], "link-1")

    def test_duplicate_message_sid_is_ignored_without_side_effects(self) -> None:
        record_events = []
        processed = []
        started = []
        service = self._make_service(
            record_events=record_events,
            processed=processed,
            started=started,
            match={
                "entry": {"id": "entry-1", "metadata": {}},
                "secret": {"from_number": "whatsapp:+100"},
                "connector_id": "conn-1",
                "workspace_id": "ws-1",
            },
            route_message=lambda body, profile: {"action": "run", "goal": "do it"},
            mark_processed_message=lambda connector_id, message_sid: False,
        )

        response = service.ingest_webhook(
            {
                "AccountSid": "AC123",
                "MessageSid": "SM123",
                "From": "whatsapp:+100",
                "To": "whatsapp:+200",
                "Body": "run do it",
            }
        )

        self.assertEqual(response, "")
        self.assertEqual(processed, [])
        self.assertEqual(started, [])
        self.assertTrue(any(evt["event_type"] == "duplicate" for evt in record_events))

    def test_public_delete_request_uses_public_branch_without_channel_router(self) -> None:
        service = self._make_service(
            match={
                "entry": {
                    "id": "entry-1",
                    "tenant_id": "tenant-1",
                    "label": "Parts Pro WhatsApp",
                    "metadata": {
                        "source": "deployed_agent",
                        "deployed_agent_id": "dagent-1",
                    },
                },
                "secret": {"from_number": "whatsapp:+100"},
                "connector_id": "conn-1",
                "workspace_id": "ws-1",
            },
        )

        class _PrivacyService:
            def public_command_action(self, profile, raw_message_text):
                return {"action": "privacy_delete_request", "routed": {"note": "erase history"}}

            def create_channel_deletion_request(self, **kwargs):
                return {"id": "privacy-1", **kwargs}

            def append_privacy_policy_line(self, text, *, include_delete_hint=False, **kwargs):
                return f"{text}\n\nPrivacy: https://example.com/privacy"

            def privacy_policy_message(self, *, deployed_agent_name=None, include_delete_hint=True):
                return "policy"

        with patch(
            "server_modules.connectors.whatsapp_ingress_service._privacy_service",
            return_value=_PrivacyService(),
        ), patch(
            "server_modules.connectors.whatsapp_ingress_service._route_inbound_channel_message",
        ) as route_mock:
            response = service.ingest_webhook(
                {
                    "AccountSid": "AC123",
                    "MessageSid": "SM999",
                    "From": "whatsapp:+100",
                    "To": "whatsapp:+200",
                    "Body": "/delete erase history",
                }
            )

        self.assertIn("recorded your data deletion request", response.lower())
        self.assertIn("Privacy: https://example.com/privacy", response)
        self.assertEqual(route_mock.call_count, 0)


if __name__ == "__main__":
    unittest.main()
