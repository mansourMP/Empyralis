import unittest
from unittest.mock import patch

from server_modules.connectors.telegram_ingress_service import TelegramIngressService


class _ActionStub:
    def __init__(self, result=None):
        self.result = result or {"handled": True, "action": "help"}
        self.calls = []

    def handle_non_run_action(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


class _RunActionStub:
    def __init__(self, result=None):
        self.result = result or {"action": "run", "run_id": "run-1"}
        self.calls = []

    def handle_run_action(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


class _PairingStub:
    def __init__(self, result=None):
        self.result = result or {"authorized": True, "status": "linked", "workspace_id": "ws-1"}
        self.calls = []

    def authorize_channel_message(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


class _PollCycleStub:
    def __init__(self):
        self.errors = []

    def handle_connector_error(self, **kwargs):
        self.errors.append(kwargs)


class _PollStateStub:
    def __init__(self):
        self.processed = []
        self.completed = []

    def record_processed_update(self, **kwargs):
        self.processed.append(kwargs)

    def record_poll_completion(self, **kwargs):
        self.completed.append(kwargs)


class _RunDispatchStub:
    def __init__(self):
        self.calls = []

    def schedule_final_delivery(self, **kwargs):
        self.calls.append(kwargs)


def _build_service(**overrides) -> TelegramIngressService:
    action_service = overrides.pop("action_service", _ActionStub())
    run_action_service = overrides.pop("run_action_service", _RunActionStub())
    pairing_service = overrides.pop("pairing_service", _PairingStub())
    poll_cycle = overrides.pop("poll_cycle", _PollCycleStub())
    poll_state = overrides.pop("poll_state", _PollStateStub())
    run_dispatch = overrides.pop("run_dispatch", _RunDispatchStub())
    messages = overrides.pop("messages", [])
    recorded_events = overrides.pop("recorded_events", [])

    def _record_channel_event(**kwargs):
        recorded_events.append(kwargs)
        return {"id": f"evt-{len(recorded_events)}"}

    return TelegramIngressService(
        default_workspace_id="default",
        resolve_workspace_scope=overrides.pop(
            "resolve_workspace_scope",
            lambda entry, fallback_workspace_id, detail_prefix: str(entry.get("workspace_id") or fallback_workspace_id or ""),
        ),
        get_connector_entry=overrides.pop(
            "get_connector_entry",
            lambda connector_id: {
                "id": connector_id,
                "tenant_id": "tenant-1",
                "workspace_id": "ws-1",
                "label": "Parts Pro Telegram",
                "metadata": {
                    "source": "deployed_agent",
                    "deployed_agent_id": "dagent-1",
                    "deployed_agent_name": "Parts Pro",
                    "channel_registry_bindings": {
                        "telegram": {"endpoint_key": "@partspro_bot"},
                    },
                },
            },
        ),
        resolve_profile=overrides.pop(
            "resolve_profile",
            lambda entry: {
                "id": "assistant",
                "prefix": "/empyralis",
                "require_prefix": True,
                "allow_free_text": True,
                "allow_help": True,
                "allow_status": True,
            },
        ),
        resolve_allow_from=overrides.pop("resolve_allow_from", lambda entry: []),
        connector_state=overrides.pop("connector_state", lambda connector_id: {"last_update_id": 0}),
        resolve_secret=overrides.pop("resolve_secret", lambda entry: {"bot_token": "token", "chat_id": "chat-1"}),
        poll_cycle_service=lambda: poll_cycle,
        poll_state_service=lambda: poll_state,
        extract_message=overrides.pop("extract_message", lambda update: update.get("message")),
        chat_matches=overrides.pop("chat_matches", lambda configured_chat_id, chat: True),
        store_attachments=overrides.pop("store_attachments", lambda **kwargs: []),
        route_message=overrides.pop("route_message", lambda message_text, profile: {"action": "run", "goal": message_text}),
        session_key_builder=overrides.pop("session_key_builder", lambda chat_id: f"session:{chat_id}"),
        trace_id_builder=overrides.pop("trace_id_builder", lambda chat_id, update_id, message_id: f"trace:{chat_id}:{update_id}:{message_id}"),
        record_channel_event=overrides.pop("record_channel_event", _record_channel_event),
        guided_setup_handler=overrides.pop("guided_setup_handler", lambda **kwargs: {"handled": False}),
        send_message=overrides.pop("send_message", lambda *args, **kwargs: messages.append((args, kwargs)) or "sent-1"),
        send_chat_action=overrides.pop("send_chat_action", lambda *args, **kwargs: None),
        run_dispatch_service=lambda: run_dispatch,
        action_service=lambda: action_service,
        run_action_service=lambda: run_action_service,
        get_chat_profile=overrides.pop("get_chat_profile", lambda workspace_id, chat_id: {"project": "alpha"}),
        explicit_run_command=overrides.pop("explicit_run_command", lambda text: text.startswith("run ")),
        help_text=overrides.pop("help_text", lambda profile: "help"),
        channel_pairing_service=lambda: pairing_service,
        media_max_items=4,
    )


class TelegramIngressServiceTests(unittest.TestCase):
    def _make_service(self, **overrides) -> TelegramIngressService:
        return _build_service(**overrides)

    def test_public_deployed_agent_free_text_routes_to_canonical_channel_router(self) -> None:
        messages = []
        run_dispatch = _RunDispatchStub()
        service = self._make_service(messages=messages, run_dispatch=run_dispatch)
        update = {
            "update_id": 11,
            "message": {
                "message_id": "msg-1",
                "text": "Need brake pads",
                "chat": {"id": "chat-1"},
                "from": {"id": "telegram-user-1", "username": "alice", "first_name": "Alice"},
            },
        }

        with patch(
            "server_modules.connectors.telegram_ingress_service._route_inbound_channel_message",
            return_value={"status": "accepted", "reply": "Run accepted. I can help with brake pads.", "run_id": "run-123"},
        ) as route_mock:
            result = service.ingest_update(source="webhook", connector_id="conn-1", update=update)

        self.assertTrue(result["processed"])
        self.assertEqual(result["action"], "accepted")
        self.assertEqual(result["run_id"], "run-123")
        self.assertEqual(route_mock.call_args.kwargs["endpoint_key"], "@partspro_bot")
        self.assertEqual(route_mock.call_args.kwargs["actor_id"], "telegram-user-1")
        self.assertEqual(messages[0][1]["text"], "Thinking...")
        self.assertFalse(messages[0][1]["include_keyboard"])
        self.assertEqual(len(run_dispatch.calls), 1)
        self.assertEqual(run_dispatch.calls[0]["run_id"], "run-123")
        self.assertEqual(run_dispatch.calls[0]["pending_message_id"], "sent-1")

    def test_public_start_command_uses_public_branch_without_channel_router(self) -> None:
        messages = []
        service = self._make_service(messages=messages)
        update = {
            "update_id": 12,
            "message": {
                "message_id": "msg-2",
                "text": "/start campaign-1",
                "chat": {"id": "chat-1"},
                "from": {"id": "telegram-user-1", "username": "alice", "first_name": "Alice"},
            },
        }

        class _AcquisitionService:
            def public_start_enabled(self, profile):
                return True

            def start_payload(self, text):
                return "campaign-1"

            def prepare_public_start_response(self, **kwargs):
                return {
                    "text": "Parts Pro\n\nStart here.",
                    "reply_markup": {"inline_keyboard": [[{"text": "Continue", "url": "https://example.com"}]]},
                }

        class _PrivacyService:
            def public_command_action(self, profile, raw_message_text):
                return None

            def append_privacy_policy_line(
                self,
                text,
                *,
                include_delete_hint=False,
                workspace_id=None,
                profile=None,
            ):
                return f"{text}\n\nPrivacy: https://example.com/privacy"

        with patch(
            "server_modules.connectors.telegram_ingress_service._acquisition_service",
            return_value=_AcquisitionService(),
        ), patch(
            "server_modules.connectors.telegram_ingress_service._privacy_service",
            return_value=_PrivacyService(),
        ), patch(
            "server_modules.connectors.telegram_ingress_service._route_inbound_channel_message",
        ) as route_mock:
            result = service.ingest_update(source="webhook", connector_id="conn-1", update=update)

        self.assertTrue(result["processed"])
        self.assertEqual(result["action"], "public_start")
        self.assertEqual(route_mock.call_count, 0)
        self.assertIn("Privacy: https://example.com/privacy", messages[0][1]["text"])

    def test_operator_message_uses_operator_branch_and_run_dispatch(self) -> None:
        messages = []
        run_action = _RunActionStub({"action": "run", "run_id": "run-operator"})
        service = self._make_service(
            messages=messages,
            run_action_service=run_action,
            get_connector_entry=lambda connector_id: {
                "id": connector_id,
                "tenant_id": "tenant-1",
                "workspace_id": "ws-1",
                "label": "Ops Telegram",
                "metadata": {},
            },
        )
        update = {
            "update_id": 13,
            "message": {
                "message_id": "msg-3",
                "text": "run summarize inbox",
                "chat": {"id": "chat-1"},
                "from": {"id": "operator-1", "username": "ops", "first_name": "Ops"},
            },
        }

        with patch(
            "server_modules.connectors.telegram_ingress_service._route_inbound_channel_message",
        ) as route_mock:
            result = service.ingest_update(source="polling", connector_id="conn-1", update=update)

        self.assertTrue(result["processed"])
        self.assertEqual(result["run_id"], "run-operator")
        self.assertEqual(len(run_action.calls), 1)
        self.assertEqual(route_mock.call_count, 0)


def _free_text_profile(entry):
    return {
        "id": "assistant",
        "prefix": "/empyralis",
        "require_prefix": False,
        "allow_free_text": True,
        "allow_help": True,
        "allow_status": True,
    }


class GroupAddressingGateTests(unittest.TestCase):
    """FIX C: zero mention/addressing parsing in groups.

    Under the "allow any chat" (require_prefix=False, free-text) profile
    config public-deployed-agent connectors use, a group/supergroup message
    with no prefix and no mention used to reach a live, dispatched reply —
    ordinary chatter with nobody asking for the bot's attention. These
    prove the new group-addressing gate closes that without breaking the
    prefix convention or private DMs.
    """

    def _make_service(self, **overrides) -> TelegramIngressService:
        return _build_service(**overrides)

    def test_group_message_with_no_mention_and_no_prefix_is_ignored(self) -> None:
        messages = []
        service = self._make_service(messages=messages, resolve_profile=_free_text_profile)
        update = {
            "update_id": 21,
            "message": {
                "message_id": "msg-21",
                "text": "does anyone know a good mechanic nearby?",
                "chat": {"id": "chat-1", "type": "group", "title": "Neighbors"},
                "from": {"id": "stranger-1", "username": "rando", "first_name": "Rando"},
            },
        }
        with patch(
            "server_modules.connectors.telegram_ingress_service._route_inbound_channel_message",
        ) as route_mock:
            result = service.ingest_update(source="webhook", connector_id="conn-1", update=update)

        self.assertFalse(result["processed"], "un-addressed group chatter must never be processed")
        self.assertEqual(route_mock.call_count, 0)
        self.assertEqual(messages, [], "no reply of any kind may be sent for un-addressed group chatter")

    def test_group_message_with_explicit_mention_is_processed(self) -> None:
        messages = []
        run_dispatch = _RunDispatchStub()
        service = self._make_service(messages=messages, run_dispatch=run_dispatch, resolve_profile=_free_text_profile)
        mention_text = "@partspro_bot need brake pads"
        update = {
            "update_id": 22,
            "message": {
                "message_id": "msg-22",
                "text": mention_text,
                "chat": {"id": "chat-1", "type": "group", "title": "Neighbors"},
                "from": {"id": "telegram-user-1", "username": "alice", "first_name": "Alice"},
                "entities": [{"type": "mention", "offset": 0, "length": len("@partspro_bot")}],
            },
        }
        with patch(
            "server_modules.connectors.telegram_ingress_service._route_inbound_channel_message",
            return_value={"status": "accepted", "reply": "On it.", "run_id": "run-mentioned"},
        ) as route_mock:
            result = service.ingest_update(source="webhook", connector_id="conn-1", update=update)

        self.assertTrue(result["processed"], "an explicit @mention must still be admitted")
        self.assertEqual(route_mock.call_count, 1)
        self.assertEqual(route_mock.call_args.kwargs["actor_id"], "telegram-user-1")

    def test_group_message_replying_to_the_bot_with_no_resolvable_bot_id_stays_ignored(self) -> None:
        # Path A connectors have no stored/resolved numeric bot id today
        # (only the stored username, via _channel_endpoint_key) — see the
        # report's "flagged as deeper" note. A reply-to-bot with no
        # resolvable bot_id therefore fails closed (not addressed) rather
        # than guessing; only the mention signal is verifiable here.
        messages = []
        service = self._make_service(messages=messages, resolve_profile=_free_text_profile)
        update = {
            "update_id": 23,
            "message": {
                "message_id": "msg-23",
                "text": "yes please",
                "chat": {"id": "chat-1", "type": "supergroup", "title": "Neighbors"},
                "from": {"id": "telegram-user-1", "username": "alice", "first_name": "Alice"},
                "reply_to_message": {"message_id": "bot-msg-1", "from": {"id": "the-bot-itself"}},
            },
        }
        with patch(
            "server_modules.connectors.telegram_ingress_service._route_inbound_channel_message",
            return_value={"status": "accepted", "reply": "Sure.", "run_id": "run-reply"},
        ):
            result = service.ingest_update(source="webhook", connector_id="conn-1", update=update)
        self.assertFalse(result["processed"])

    def test_group_message_using_the_connector_prefix_is_processed_without_any_mention(self) -> None:
        # Regression guard: the pre-existing, deliberate prefix convention
        # (an explicit "/empyralis ..." command) must keep working exactly
        # as before — the new gate only closes the free-text fallthrough,
        # never a deliberate prefixed invocation.
        messages = []
        run_dispatch = _RunDispatchStub()
        service = self._make_service(messages=messages, run_dispatch=run_dispatch, resolve_profile=_free_text_profile)
        update = {
            "update_id": 24,
            "message": {
                "message_id": "msg-24",
                "text": "/empyralis run summarize the thread",
                "chat": {"id": "chat-1", "type": "group", "title": "Neighbors"},
                "from": {"id": "telegram-user-1", "username": "alice", "first_name": "Alice"},
            },
        }
        with patch(
            "server_modules.connectors.telegram_ingress_service._route_inbound_channel_message",
            return_value={"status": "accepted", "reply": "On it.", "run_id": "run-prefixed"},
        ) as route_mock:
            result = service.ingest_update(source="webhook", connector_id="conn-1", update=update)

        self.assertTrue(result["processed"])
        self.assertEqual(route_mock.call_count, 1)

    def test_private_chat_free_text_is_never_gated(self) -> None:
        # The group-addressing gate must never apply outside group/
        # supergroup — a private DM is always implicitly addressed.
        messages = []
        run_dispatch = _RunDispatchStub()
        service = self._make_service(messages=messages, run_dispatch=run_dispatch, resolve_profile=_free_text_profile)
        update = {
            "update_id": 25,
            "message": {
                "message_id": "msg-25",
                "text": "need brake pads please",
                "chat": {"id": "chat-1", "type": "private"},
                "from": {"id": "telegram-user-1", "username": "alice", "first_name": "Alice"},
            },
        }
        with patch(
            "server_modules.connectors.telegram_ingress_service._route_inbound_channel_message",
            return_value={"status": "accepted", "reply": "Sure.", "run_id": "run-dm"},
        ) as route_mock:
            result = service.ingest_update(source="webhook", connector_id="conn-1", update=update)

        self.assertTrue(result["processed"])
        self.assertEqual(route_mock.call_count, 1)

    def test_image_only_group_message_with_no_mention_stays_ignored(self) -> None:
        # The action=="ignore" -> "run" attachment-fallback upgrade must
        # also respect the group gate — an unaddressed group message with
        # a photo attached must not be "upgraded" into a live run either.
        messages = []
        service = self._make_service(
            messages=messages,
            resolve_profile=_free_text_profile,
            store_attachments=lambda **kwargs: [{"kind": "photo", "path": "x.jpg"}],
        )
        update = {
            "update_id": 26,
            "message": {
                "message_id": "msg-26",
                "text": "",
                "chat": {"id": "chat-1", "type": "group", "title": "Neighbors"},
                "from": {"id": "stranger-1", "username": "rando", "first_name": "Rando"},
            },
        }
        with patch(
            "server_modules.connectors.telegram_ingress_service._route_inbound_channel_message",
        ) as route_mock:
            result = service.ingest_update(source="webhook", connector_id="conn-1", update=update)

        self.assertFalse(result["processed"])
        self.assertEqual(route_mock.call_count, 0)


if __name__ == "__main__":
    unittest.main()
