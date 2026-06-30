import unittest
from unittest import mock

from server_modules import direct_chat_entry_policy_service as service


class DirectChatEntryPolicyServiceTests(unittest.TestCase):
    def test_resolved_chat_iteration_limit_uses_env_default_and_ceiling(self) -> None:
        limit = service.resolved_chat_iteration_limit(
            None,
            default_limit=30,
            ceiling=100,
            env_var_name="ORION_MAX_CHAT_ITERATIONS",
            env_get_fn=lambda _name, _default: "120",
        )

        self.assertEqual(limit, 100)

    def test_resolve_direct_chat_availability_delegates(self) -> None:
        expected = {"ai_ready": True}

        with mock.patch.object(
            service.direct_chat_provider_service,
            "resolve_direct_chat_availability",
            return_value=expected,
        ) as resolve_availability:
            payload = service.resolve_direct_chat_availability(
                "default",
                "openai",
                direct_chat_runtime_available_fn=lambda: True,
                preferred_provider_fn=lambda workspace_id, provider: ("openai", {}),
                supports_direct_message_native_chat_fn=lambda provider, credentials: True,
                resolve_workspace_tool_capabilities_fn=lambda workspace_id: [],
                availability_override={"runtime_ok": True},
            )

        self.assertIs(payload, expected)
        resolve_availability.assert_called_once()

    def test_session_model_preference_delegates_with_store(self) -> None:
        store = {"default:thread": {"provider": "openai", "model": "gpt-5.4"}}

        with mock.patch.object(
            service.direct_chat_context_service,
            "session_model_preference",
            return_value={"provider": "openai", "model": "gpt-5.4"},
        ) as session_preference:
            payload = service.session_model_preference("default:thread", store=store)

        self.assertEqual(payload["provider"], "openai")
        session_preference.assert_called_once_with("default:thread", store=store)

    def test_plan_direct_chat_route_delegates(self) -> None:
        expected = mock.sentinel.route

        with mock.patch.object(
            service.direct_chat_routing_service,
            "plan_direct_chat_route",
            return_value=expected,
        ) as plan_route:
            route = service.plan_direct_chat_route(
                message="run this",
                availability={"ai_ready": True},
                provider="openai",
                tools=[],
                compact_text_fn=lambda value: str(value).strip().lower(),
                mentions_any_fn=lambda message, markers: False,
                is_obvious_smtp_write_request_fn=lambda message: False,
                preview_run_response_fn=lambda message, availability: None,
                prefer_durable_run_handoff_fn=lambda message, availability: False,
                durable_run_preferred_response_fn=lambda message: {"reply": "use a run"},
                message_can_use_direct_connector_tools_fn=lambda message: False,
                message_can_use_direct_local_tools_fn=lambda message: False,
                message_can_use_builtin_direct_tools_fn=lambda message: False,
                can_auto_start_run_handoff_fn=lambda availability: False,
                google_workspace_keywords=(),
                telegram_keywords=(),
                slack_keywords=(),
                discord_keywords=(),
                dropbox_keywords=(),
                s3_keywords=(),
            )

        self.assertIs(route, expected)
        plan_route.assert_called_once()


if __name__ == "__main__":
    unittest.main()
