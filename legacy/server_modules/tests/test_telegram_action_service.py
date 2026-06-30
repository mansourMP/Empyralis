import unittest
from unittest.mock import patch

from server_modules.connectors.telegram_action_service import TelegramActionService


class TelegramActionServiceTests(unittest.TestCase):
    def _make_service(self, **overrides) -> TelegramActionService:
        messages = overrides.pop("messages", [])
        approvals = overrides.pop("approvals", {"events": []})
        return TelegramActionService(
            default_chat_prefix="/empyralis",
            onboarding_enabled=overrides.pop("onboarding_enabled", True),
            help_text=lambda profile: "help",
            skills_menu_text=lambda profile: "skills",
            menu_keyboard=lambda profile, menu_id: {"menu": menu_id},
            onboarding_prompt=lambda step, retry: f"prompt-{step}",
            onboarding_start=lambda workspace_id, chat_id: {"active": True, "step_index": 0},
            profile_text=lambda profile, chat_profile: "profile",
            profile_help_text=lambda profile: "profile-help",
            profile_set=lambda workspace_id, chat_id, field_name, value: {"field": field_name, "value": value},
            profile_clear=lambda workspace_id, chat_id, field_name: {},
            runtime_status_text=lambda workspace_id: "status",
            approvals_list=lambda limit, workspace_id=None: approvals,
            approvals_text=lambda payload, prefix: "approvals",
            approval_resolve=lambda event_id, approved, note, workspace_id=None: {"ok": True},
            approval_result_text=lambda payload, approved: "approved" if approved else "rejected",
            send_message=lambda **kwargs: messages.append(kwargs),
        )

    def test_help_action_sends_message(self) -> None:
        messages = []
        service = self._make_service(messages=messages)
        result = service.handle_non_run_action(
            action="help",
            routed={},
            profile={},
            chat_profile={},
            workspace_id="ws",
            connector_id="conn",
            bot_token="token",
            chat_id="chat",
            inbound_message_id="msg",
            trace_id="trace",
            source_event_id="evt",
        )

        self.assertTrue(result["handled"])
        self.assertEqual(messages[0]["text"], "help")

    def test_profile_set_updates_profile(self) -> None:
        messages = []
        service = self._make_service(messages=messages)
        result = service.handle_non_run_action(
            action="profile_set",
            routed={"field": "project", "value": "alpha"},
            profile={},
            chat_profile={},
            workspace_id="ws",
            connector_id="conn",
            bot_token="token",
            chat_id="chat",
            inbound_message_id="msg",
            trace_id="trace",
            source_event_id="evt",
        )

        self.assertTrue(result["handled"])
        self.assertEqual(result["chat_profile"]["field"], "project")
        self.assertIn("Saved", messages[0]["text"])

    def test_approvals_action_sends_payload(self) -> None:
        messages = []
        service = self._make_service(messages=messages, approvals={"events": ["a"]})
        result = service.handle_non_run_action(
            action="approvals",
            routed={"limit": 3},
            profile={},
            chat_profile={},
            workspace_id="ws",
            connector_id="conn",
            bot_token="token",
            chat_id="chat",
            inbound_message_id="msg",
            trace_id="trace",
            source_event_id="evt",
        )

        self.assertTrue(result["handled"])
        self.assertEqual(messages[0]["text"], "approvals")

    def test_public_start_sends_intro_with_single_platform_cta(self) -> None:
        messages = []
        service = self._make_service(messages=messages)

        class _AcquisitionService:
            def public_start_enabled(self, profile):
                return True

            def start_payload(self, text):
                return "youtube-health"

            def prepare_public_start_response(self, **kwargs):
                return {
                    "text": "HealthGuide\n\nHealthGuide gives fast, cautious health information on Telegram.",
                    "cta_label": "Continue on Empyralist",
                    "cta_url": "https://app.empyralist.com/signup?channel_attribution=acu-token",
                    "reply_markup": {
                        "inline_keyboard": [[
                            {
                                "text": "Continue on Empyralist",
                                "url": "https://app.empyralist.com/signup?channel_attribution=acu-token",
                            }
                        ]]
                    },
                }

        class _PrivacyService:
            def append_privacy_policy_line(self, text, *, include_delete_hint=False):
                return f"{text}\n\nPrivacy: https://app.empyralist.com/privacy\nDelete saved data anytime with /delete."

        with patch(
            "server_modules.connectors.telegram_action_service.channel_user_acquisition_service.get_channel_user_acquisition_service",
            return_value=_AcquisitionService(),
        ), patch(
            "server_modules.connectors.telegram_action_service.external_user_privacy_service.get_external_user_privacy_service",
            return_value=_PrivacyService(),
        ):
            result = service.handle_non_run_action(
                action="public_start",
                routed={"campaign_token": "youtube-health"},
                profile={"deployed_agent_id": "dagent-1"},
                chat_profile={},
                workspace_id="ws",
                connector_id="conn",
                bot_token="token",
                chat_id="chat",
                inbound_message_id="msg",
                trace_id="trace",
                source_event_id="evt",
                sender_id="telegram-user-1",
                sender_username="alice",
                sender_display_name="Alice",
            )

        self.assertTrue(result["handled"])
        self.assertIn("HealthGuide gives fast, cautious health information on Telegram.", messages[0]["text"])
        self.assertIn("Privacy: https://app.empyralist.com/privacy", messages[0]["text"])
        self.assertEqual(
            messages[0]["reply_markup"]["inline_keyboard"],
            [[{"text": "Continue on Empyralist", "url": "https://app.empyralist.com/signup?channel_attribution=acu-token"}]],
        )

    def test_privacy_delete_request_records_request_and_confirms(self) -> None:
        messages = []
        service = self._make_service(messages=messages)

        class _PrivacyService:
            def create_channel_deletion_request(self, **kwargs):
                return {"id": "privreq_1"}

            def append_privacy_policy_line(self, text, *, include_delete_hint=False):
                return f"{text}\n\nPrivacy: https://app.empyralist.com/privacy"

        with patch(
            "server_modules.connectors.telegram_action_service.external_user_privacy_service.get_external_user_privacy_service",
            return_value=_PrivacyService(),
        ):
            result = service.handle_non_run_action(
                action="privacy_delete_request",
                routed={"note": "please remove everything"},
                profile={"deployed_agent_id": "dagent-1", "workspace_id": "ws"},
                chat_profile={},
                workspace_id="ws",
                connector_id="conn",
                bot_token="token",
                chat_id="chat",
                inbound_message_id="msg",
                trace_id="trace",
                source_event_id="evt",
                sender_id="telegram-user-1",
                sender_username="alice",
                sender_display_name="Alice",
            )

        self.assertTrue(result["handled"])
        self.assertIn("We recorded your data deletion request.", messages[0]["text"])
        self.assertIn("Privacy: https://app.empyralist.com/privacy", messages[0]["text"])


if __name__ == "__main__":
    unittest.main()
