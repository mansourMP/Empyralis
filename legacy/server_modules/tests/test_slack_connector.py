import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from starlette.requests import Request

from server_modules import connectors_actions
from server_modules import channel_lane_contract_service
from server_modules import channel_preflight_service
from server_modules import connection_catalog_service
from server_modules.connectors import slack_connector


ROOT_DIR = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT_DIR / "server_modules" / "direct_chat_runtime_exports.py"
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
spec = importlib.util.spec_from_file_location("operator_chat_slack_under_test", MODULE_PATH)
operator_chat = importlib.util.module_from_spec(spec)
sys.modules["operator_chat_slack_under_test"] = operator_chat
assert spec and spec.loader
spec.loader.exec_module(operator_chat)


def _request_from_body(body: bytes, *, headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    async def _receive() -> dict:
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/channels/slack/events",
        "raw_path": b"/channels/slack/events",
        "query_string": b"",
        "headers": headers or [],
        "client": ("127.0.0.1", 54321),
        "server": ("127.0.0.1", 8001),
    }
    return Request(scope, _receive)


class SlackConnectorTests(unittest.IsolatedAsyncioTestCase):
    def test_send_message_requires_approval(self):
        payload = operator_chat._build_direct_tool_approval_response(
            tool_calls=[
                {
                    "name": "slack__send_message",
                    "arguments": json.dumps({"input": "{\"channel\":\"C123\",\"message\":\"hello from slack\"}"}),
                }
            ],
            tool_capabilities=[
                {
                    "id": "slack",
                    "label": "Slack LIVE",
                    "connected": True,
                    "authenticated": True,
                    "runtime_usable": True,
                    "read_actions": ["channels.read"],
                    "write_actions": ["send_message"],
                    "approval_required_actions": ["send_message"],
                }
            ],
        )

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["mode"], "answer_with_action")
        self.assertEqual(payload["actions"][0]["connector"], "slack")
        self.assertEqual(payload["actions"][0]["action"], "send_message")

    @patch("server_modules.runs_execution._workflow_execute_connector_action")
    @patch("operator_chat_slack_under_test._direct_chat_workspace_context_text", return_value="")
    @patch(
        "operator_chat_slack_under_test.resolve_workspace_tool_capabilities",
        return_value=[
            {
                "id": "slack",
                "label": "Slack LIVE",
                "connected": True,
                "authenticated": True,
                "runtime_usable": True,
                "read_actions": ["channels.read"],
                "write_actions": ["send_message"],
                "approval_required_actions": ["send_message"],
            }
        ],
    )
    def test_send_message_with_approval_granted(
        self,
        _resolve_workspace_tool_capabilities,
        _workspace_context_text,
        execute_tool_mock,
    ):
        execute_tool_mock.return_value = {
            "summary": "Connector action completed: slack.send_message.",
            "result_data": {
                "connector_action": {
                    "connector": "slack",
                    "action_id": "send_message",
                    "channel_id": "C123",
                }
            },
        }

        payload = operator_chat.collect_direct_operator_reply(
            message="__approval_confirmed__",
            workspace_id="default",
            requested_model="gpt-5.4",
            requested_provider="openai",
            availability={"ai_ready": True},
            approved_action={
                "connector": "slack",
                "action": "send_message",
                "input": "{\"channel\":\"C123\",\"message\":\"hello from slack\"}",
            },
        )

        self.assertEqual(payload["mode"], "answer")
        self.assertIn("slack.send_message", payload["reply"])
        execute_tool_mock.assert_called_once()

    def test_inbound_event_parsed_correctly(self):
        parsed = slack_connector.parse_inbound_event(
            {
                "type": "event_callback",
                "team_id": "T123",
                "event_id": "Ev123",
                "api_app_id": "A123",
                "event": {
                    "type": "app_mention",
                    "channel": "C123",
                    "user": "U123",
                    "text": "hello bot",
                    "ts": "1712000000.000100",
                    "thread_ts": "1712000000.000001",
                },
            }
        )

        self.assertEqual(parsed["kind"], "event")
        self.assertEqual(parsed["message_type"], "mention")
        self.assertEqual(parsed["channel"], "C123")
        self.assertEqual(parsed["user_id"], "U123")
        self.assertEqual(parsed["thread_ts"], "1712000000.000001")

    def test_inbound_trigger_policy_matches_mentions_and_dms(self):
        parsed = slack_connector.parse_inbound_event(
            {
                "type": "event_callback",
                "team_id": "T123",
                "event_id": "Ev123",
                "event": {
                    "type": "app_mention",
                    "channel": "C123",
                    "user": "U123",
                    "text": "<@BOT> hello",
                    "ts": "1712000000.000100",
                },
            }
        )

        self.assertTrue(
            slack_connector.event_matches_connector(
                parsed,
                {"team_id": "T123", "bot_user_id": "BOT"},
                {"slack_channel_id": "C123"},
            )
        )
        self.assertTrue(
            slack_connector.should_trigger_agent_run(
                parsed,
                {"team_id": "T123", "bot_user_id": "BOT"},
                metadata={"slack_channel_id": "C123"},
            )
        )
        self.assertFalse(
            slack_connector.should_trigger_agent_run(
                {**parsed, "message_type": "message", "channel_type": "channel"},
                {"team_id": "T123", "bot_user_id": "BOT"},
                metadata={"slack_channel_id": "C123"},
            )
        )
        self.assertTrue(
            slack_connector.should_trigger_agent_run(
                {**parsed, "message_type": "message", "channel_type": "im"},
                {"team_id": "T123", "bot_user_id": "BOT"},
                metadata={"slack_channel_id": "C123"},
            )
        )
        self.assertFalse(
            slack_connector.should_trigger_agent_run(
                {**parsed, "user_id": "BOT"},
                {"team_id": "T123", "bot_user_id": "BOT"},
                metadata={"slack_channel_id": "C123"},
            )
        )

    def test_slack_is_allowed_by_canonical_inbound_preflight(self):
        with (
            patch(
                "server_modules.channel_preflight_service.safe_mode_service.resolve_machine_policy_status",
                return_value={"kill_switch": {"active": False}},
            ),
            patch(
                "server_modules.channel_preflight_service.safe_mode_service.is_channel_disabled",
                return_value=False,
            ),
        ):
            channel_key, endpoint_key, message = channel_preflight_service.assert_inbound_allowed(
                tenant_id="tenant-default",
                workspace_id="default",
                channel_key="slack",
                endpoint_key="C123",
                customer_message="hello",
            )

        self.assertEqual(channel_key, "slack")
        self.assertEqual(endpoint_key, "c123")
        self.assertEqual(message, "hello")

    async def test_slack_webhook_routes_message_through_agent_channel_router(self):
        body = json.dumps({"type": "event_callback"}).encode("utf-8")
        request = _request_from_body(body)
        connector_row = {
            "id": "cred-slack",
            "provider": "slack",
            "workspace_id": "default",
            "tenant_id": "tenant-default",
            "metadata": {"team_id": "T123", "channel_registry_bindings": {"slack": {"endpoint_key": "C123"}}},
        }
        parsed = {
            "kind": "event",
            "event_id": "Ev123",
            "event_type": "app_mention",
            "message_type": "mention",
            "team_id": "T123",
            "channel": "C123",
            "user_id": "U123",
            "text": "<@BOT> hello",
            "ts": "1712000000.000100",
            "thread_ts": "1712000000.000100",
            "message_ts": "1712000000.000100",
        }
        route_message = AsyncMock(return_value={"ok": True, "run_id": "run-123", "reply": "Working on it."})

        with (
            patch("server_modules.connectors_actions.slack_verify_request_signature", return_value=True),
            patch("server_modules.connectors_actions.slack_parse_inbound_event", return_value=parsed),
            patch("server_modules.connectors_actions.load_vault", return_value={"credentials": [connector_row]}),
            patch("server_modules.connectors_actions.resolve_vault_credential", return_value={"team_id": "T123", "bot_user_id": "BOT"}),
            patch("server_modules.connectors_actions._append_channel_event", return_value=None) as append_event,
            patch("server_modules.agent_channel_router.route_inbound_channel_message", new=route_message),
        ):
            result = await connectors_actions.slack_events_webhook(request)

        self.assertEqual(result["handled"], 1)
        self.assertEqual(result["triggered"], 1)
        self.assertEqual(result["run_id"], "run-123")
        append_event.assert_called_once()
        route_message.assert_awaited_once()
        kwargs = route_message.await_args.kwargs
        self.assertEqual(kwargs["tenant_id"], "tenant-default")
        self.assertEqual(kwargs["workspace_id"], "default")
        self.assertEqual(kwargs["channel_key"], "slack")
        self.assertEqual(kwargs["endpoint_key"], "C123")
        self.assertEqual(kwargs["customer_message"], "<@BOT> hello")
        self.assertFalse(kwargs["allow_master_fallback"])

    def test_slack_catalog_is_launchable_when_configured(self):
        catalog_item = next(item for item in connection_catalog_service.catalog_items() if item["id"] == "slack")
        self.assertTrue(catalog_item["setup_available"])
        self.assertTrue(catalog_item["runtime_usable"])
        self.assertTrue(catalog_item["launchable"])
        platform = next(item for item in channel_lane_contract_service.platform_channel_catalog() if item["channel_key"] == "slack")
        self.assertTrue(platform["live_capable"])
        self.assertTrue(platform["launch_allowed"])

    @patch("server_modules.connectors_actions.save_vault")
    @patch("server_modules.connectors_actions.load_vault", return_value={"credentials": []})
    @patch("server_modules.connectors_actions._openssl_encrypt", side_effect=lambda raw: raw)
    def test_oauth_token_stored_and_retrieved(
        self,
        _encrypt_mock,
        _load_vault_mock,
        save_vault_mock,
    ):
        result = connectors_actions._upsert_slack_oauth_connector_entry(
            workspace_id="default",
            label="Slack",
            credentials={
                "bot_token": "xoxb-test",
                "user_token": "xoxp-test",
                "team_id": "T123",
                "team_name": "Acme",
            },
            metadata={},
            test_result={
                "team": {"id": "T123", "name": "Acme"},
                "bot": {"user_id": "UBOT", "bot_status": "active"},
                "authed_user": {"id": "U123"},
                "ok": True,
            },
        )

        saved_vault = save_vault_mock.call_args.args[0]
        stored = saved_vault["credentials"][0]
        recovered = json.loads(stored["encrypted_secret"])

        self.assertEqual(result["connector"], "slack")
        self.assertEqual(result["metadata"]["team_name"], "Acme")
        self.assertEqual(recovered["bot_token"], "xoxb-test")
        self.assertEqual(recovered["user_token"], "xoxp-test")


if __name__ == "__main__":
    unittest.main()
