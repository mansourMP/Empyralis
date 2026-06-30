import unittest

from server_modules.connectors.autopilot_run_entry_service import AutopilotRunEntryService


class AutopilotRunEntryServiceTests(unittest.TestCase):
    def _make_service(self, **overrides) -> AutopilotRunEntryService:
        self.created = overrides.pop("created", [])
        self.events = overrides.pop("events", [])
        self.telegram_started = overrides.pop("telegram_started", [])
        self.whatsapp_started = overrides.pop("whatsapp_started", [])
        self.canonical_calls = overrides.pop("canonical_calls", [])
        return AutopilotRunEntryService(
            telegram_profile_fields=["project", "role"],
            telegram_engine="orion",
            whatsapp_engine="orion",
            safe_path_token=overrides.pop("safe_path_token", lambda value: str(value or "x").replace(":", "_")),
            assigned_agent_role=overrides.pop("assigned_agent_role", lambda entry: str(entry.get("agent_role") or "")),
            normalize_trust_mode=overrides.pop("normalize_trust_mode", lambda value: str(value or "").strip() or "personal"),
            normalize_execution_target=overrides.pop("normalize_execution_target", lambda value: str(value or "").strip() or "cloud"),
            decide_execution_target=overrides.pop("decide_execution_target", lambda metadata: {"selected": metadata.get("execution_target")}),
            apply_execution_route_metadata=overrides.pop(
                "apply_execution_route_metadata",
                lambda metadata, route: {**metadata, "route_selected": route.get("selected")},
            ),
            route_transport_channel_message=overrides.pop(
                "route_transport_channel_message",
                lambda **kwargs: self.canonical_calls.append(kwargs) or {
                    "run_id": f"run-canonical-{len(self.canonical_calls)}",
                    "session_key": f"canonical:{kwargs.get('channel_key')}:{kwargs.get('endpoint_key')}:{kwargs.get('actor_id')}",
                    "metadata": {"route": {"selected": "cloud"}},
                },
            ),
            run_start_request_class=overrides.pop("run_start_request_class", None),
            start_run_request=overrides.pop("start_run_request", None),
            create_run=overrides.pop(
                "create_run",
                lambda **kwargs: self.created.append(kwargs) or f"run-{len(self.created)}",
            ),
            record_channel_event=overrides.pop("record_channel_event", lambda **kwargs: self.events.append(kwargs)),
            telegram_session_key=overrides.pop("telegram_session_key", lambda chat_id: f"telegram:{chat_id}"),
            whatsapp_session_key=overrides.pop("whatsapp_session_key", lambda from_number, to_number: f"whatsapp:{from_number}->{to_number}"),
            inherit_owner_user_id=overrides.pop("inherit_owner_user_id", lambda owner_user_id=None: "user-123"),
            agent_machine_full_trust_enabled=overrides.pop("agent_machine_full_trust_enabled", lambda owner_user_id: owner_user_id == "user-123"),
            telegram_runs_started=overrides.pop("telegram_runs_started", lambda: self.telegram_started.append(True)),
            whatsapp_runs_started=overrides.pop("whatsapp_runs_started", lambda: self.whatsapp_started.append(True)),
        )

    def test_create_telegram_run_includes_owner_and_profile_context(self) -> None:
        service = self._make_service()
        result = service.create_telegram_run(
            goal="Investigate",
            workspace_id="default",
            connector_id="cred-telegram",
            chat_id="123",
            sender_id="456",
            update_id=1,
            profile_context={"project": "alpha"},
            trust_mode_value="agent",
            execution_target_value="cloud",
        )

        self.assertEqual(result["run_id"], "run-canonical-1")
        self.assertEqual(self.created, [])
        canonical = self.canonical_calls[0]
        self.assertEqual(canonical["tenant_id"], "default")
        self.assertEqual(canonical["channel_key"], "telegram")
        self.assertEqual(canonical["customer_message"], "Investigate")
        self.assertEqual(canonical["actor_id"], "456")
        self.assertEqual(canonical["metadata"]["owner_user_id"], "user-123")
        self.assertEqual(canonical["metadata"]["telegram"]["profile_context"]["project"], "alpha")
        self.assertEqual(canonical["metadata"]["execution_target"], "cloud")
        self.assertEqual(len(self.telegram_started), 1)
        self.assertEqual(self.events[0]["event_type"], "run_started")
        self.assertEqual(self.events[0]["session_key"], "canonical:telegram:cred-telegram:456")

    def test_create_whatsapp_run_includes_owner(self) -> None:
        service = self._make_service()
        result = service.create_whatsapp_run(
            goal="Handle this",
            workspace_id="default",
            connector_id="cred-whatsapp",
            from_number="whatsapp:+1",
            to_number="whatsapp:+2",
            message_sid="SM123",
            account_sid="AC123",
            trust_mode_value="agent",
            execution_target_value="cloud",
        )

        self.assertEqual(result["run_id"], "run-canonical-1")
        self.assertEqual(self.created, [])
        canonical = self.canonical_calls[0]
        self.assertEqual(canonical["channel_key"], "whatsapp")
        self.assertEqual(canonical["endpoint_key"], "whatsapp:+2")
        self.assertEqual(canonical["actor_id"], "whatsapp:+1")
        self.assertEqual(canonical["metadata"]["owner_user_id"], "user-123")
        self.assertEqual(canonical["metadata"]["execution_target"], "cloud")
        self.assertEqual(len(self.whatsapp_started), 1)
        self.assertEqual(self.events[0]["session_key"], "canonical:whatsapp:whatsapp:+2:whatsapp:+1")

    def test_create_telegram_run_can_start_via_canonical_run_request(self) -> None:
        started_requests = []

        class _RunStartRequest:
            def __init__(self, **kwargs):
                for key, value in kwargs.items():
                    setattr(self, key, value)

        service = self._make_service(
            route_transport_channel_message=None,
            run_start_request_class=_RunStartRequest,
            start_run_request=lambda request: started_requests.append(request) or {
                "run_id": "run-canonical-1",
                "route": {"selected": "local_companion"},
            },
        )

        result = service.create_telegram_run(
            goal="Investigate",
            workspace_id="default",
            connector_id="cred-telegram",
            chat_id="123",
            sender_id="456",
            update_id=1,
            trust_mode_value="agent",
            execution_target_value="local_companion",
        )

        self.assertEqual(result["run_id"], "run-canonical-1")
        self.assertEqual(started_requests[0].workspace_id, "default")
        self.assertEqual(started_requests[0].user_goal, "Investigate")
        self.assertEqual(started_requests[0].metadata["channel"], "telegram")
        self.assertEqual(started_requests[0].metadata["execution_target"], "local_companion")
        self.assertEqual(started_requests[0].metadata["owner_user_id"], "user-123")
        self.assertEqual(started_requests[0].metadata["agent_turn_request"]["channel"], "telegram")
        self.assertEqual(len(self.created), 0)

    def test_create_telegram_run_uses_channel_binding_endpoint_for_canonical_contract(self) -> None:
        service = self._make_service()

        service.create_telegram_run(
            goal="Investigate",
            workspace_id="default",
            connector_id="cred-telegram",
            chat_id="123",
            sender_id="456",
            update_id=1,
            connector_entry={
                "id": "cred-telegram",
                "label": "Parts Pro",
                "metadata": {
                    "tenant_id": "tenant-1",
                    "channel_registry_bindings": {
                        "telegram": {"endpoint_key": "@partspro_bot"},
                    },
                    "active_agent_install_id": "install-specialist",
                    "master_agent_install_id": "install-sage",
                    "runtime_profile_id": "runtime-cloud",
                },
            },
        )

        canonical = self.canonical_calls[0]
        self.assertEqual(canonical["tenant_id"], "tenant-1")
        self.assertEqual(canonical["endpoint_key"], "@partspro_bot")
        self.assertEqual(canonical["install"]["id"], "install-specialist")
        self.assertEqual(canonical["master_install_id"], "install-sage")
        self.assertEqual(canonical["runtime_profile_id"], "runtime-cloud")

    def test_create_telegram_run_returns_non_run_canonical_result_without_run_started_side_effects(self) -> None:
        service = self._make_service(
            route_transport_channel_message=lambda **kwargs: self.canonical_calls.append(kwargs) or {
                "status": "thread_busy",
                "reply": "Still working on the previous message.",
                "metadata": {},
            },
        )

        result = service.create_telegram_run(
            goal="Investigate",
            workspace_id="default",
            connector_id="cred-telegram",
            chat_id="123",
            sender_id="456",
            update_id=1,
        )

        self.assertEqual(result["run_id"], "")
        self.assertEqual(result["status"], "thread_busy")
        self.assertEqual(result["reply"], "Still working on the previous message.")
        self.assertEqual(self.telegram_started, [])
        self.assertEqual(self.events, [])

    def test_create_telegram_run_requires_canonical_ingress_callbacks(self) -> None:
        service = self._make_service(
            route_transport_channel_message=None,
            run_start_request_class=None,
            start_run_request=None,
            execute_agent_turn_request=None,
        )

        with self.assertRaises(RuntimeError):
            service.create_telegram_run(
                goal="Investigate",
                workspace_id="default",
                connector_id="cred-telegram",
                chat_id="123",
                sender_id="456",
                update_id=1,
            )

    def test_can_auto_approve_wait_is_disabled(self) -> None:
        service = self._make_service()
        run = {
            "context": {"metadata": {"owner_user_id": "user-123", "local_execution_waiting_confirmation": True}},
            "pending_confirmation": {"approval_id": "approval-1", "source": "runtime_wait"},
        }
        self.assertFalse(service.can_auto_approve_wait(run))
        run["context"]["metadata"]["owner_user_id"] = "user-999"
        self.assertFalse(service.can_auto_approve_wait(run))


if __name__ == "__main__":
    unittest.main()
