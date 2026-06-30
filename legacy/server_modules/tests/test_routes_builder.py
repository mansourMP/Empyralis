import unittest
from unittest.mock import patch

from fastapi import HTTPException

from server_modules.routes_builder import (
    BuilderGenerateRequest,
    _parse_workflow_payload,
    builder_connector_manifests,
    builder_generate,
)


class BuilderRouteTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _current_user():
        return {
            "auth_type": "api_key",
            "is_admin": True,
            "role": "owner",
            "user_id": "builder-owner",
            "workspace_access": {
                "default": {
                    "workspace_id": "default",
                    "tenant_id": "default",
                    "role": "owner",
                    "tenant_role": "owner",
                }
            },
        }

    def test_parse_workflow_payload_returns_canonical_workflow(self):
        payload = _parse_workflow_payload(
            """
            {
              "version": "empyralist.workflow.v2",
              "nodes": [
                {
                  "id": "trigger_1",
                  "type": "trigger",
                  "variant": "manual",
                  "label": "Manual trigger",
                  "subtitle": "Test only",
                  "x": 10,
                  "y": 20
                },
                {
                  "id": "agent_1",
                  "type": "agent",
                  "label": "Triage agent",
                  "subtitle": "Classifies the request",
                  "x": 220,
                  "y": 20,
                  "config": {
                    "identity": {
                      "name": "Triage agent",
                      "goal": "Classify the request"
                    }
                  }
                }
              ],
              "edges": [
                {"source": "trigger_1", "target": "agent_1"}
              ]
            }
            """
        )

        self.assertEqual(payload["version"], "empyralist.workflow.v2")
        self.assertEqual([node["type"] for node in payload["nodes"]], ["trigger", "agent"])
        self.assertEqual(payload["nodes"][0]["variant"], "manual")
        self.assertEqual(payload["nodes"][1]["config"]["identity"]["name"], "Triage agent")
        self.assertEqual(payload["edges"][0]["source"], "trigger_1")
        self.assertEqual(payload["nodes"][1]["config"]["permissions"]["trust_preset"], "standard_local")
        self.assertEqual(
            payload["nodes"][1]["config"]["permissions"]["remembered_grants"],
            {"folders": [], "browser_session": False, "shell_capabilities": []},
        )

    def test_parse_workflow_payload_rejects_invalid_json(self):
        with self.assertRaises(HTTPException) as ctx:
            _parse_workflow_payload("not-json")
        self.assertEqual(ctx.exception.status_code, 502)

    def test_parse_workflow_payload_accepts_file_watch_as_draft_schema(self):
        payload = _parse_workflow_payload(
            """
            {
              "nodes": [
                {
                  "id": "trigger_1",
                  "type": "trigger",
                  "variant": "file_watch"
                }
              ],
              "edges": []
            }
            """
        )
        self.assertEqual(payload["nodes"][0]["variant"], "file_watch")
        self.assertTrue(
            any(issue.get("code") == "file_watch_not_executable_yet" for issue in payload.get("issues", []))
        )

    def test_parse_workflow_payload_infers_connector_trigger_config(self):
        payload = _parse_workflow_payload(
            """
            {
              "nodes": [
                {
                  "id": "trigger_1",
                  "type": "trigger",
                  "label": "Telegram inbound message",
                  "subtitle": "When a Telegram user sends a message"
                }
              ],
              "edges": []
            }
            """
        )
        node = payload["nodes"][0]
        self.assertEqual(node["variant"], "connector_event")
        self.assertEqual(node["config"]["connector"], "telegram_bot")
        self.assertEqual(node["config"]["event"], "inbound_message")

    def test_parse_workflow_payload_infers_connector_action_config(self):
        payload = _parse_workflow_payload(
            """
            {
              "nodes": [
                {
                  "id": "tool_1",
                  "type": "tool",
                  "label": "Send Telegram update",
                  "subtitle": "Reply back in Telegram with the result"
                }
              ],
              "edges": []
            }
            """
        )
        node = payload["nodes"][0]
        self.assertEqual(node["variant"], "connector_action")
        self.assertEqual(node["config"]["connector"], "telegram_bot")
        self.assertEqual(node["config"]["action_id"], "send_message")
        self.assertEqual(node["policy"]["capability_id"], "send_message")

    def test_parse_workflow_payload_stamps_canonical_capability_ids(self):
        payload = _parse_workflow_payload(
            """
            {
              "version": "empyralist.workflow.v2",
              "nodes": [
                {
                  "id": "trigger_1",
                  "type": "trigger",
                  "variant": "manual"
                },
                {
                  "id": "loop_1",
                  "type": "loop",
                  "variant": "for_each",
                  "config": {
                    "array_source": "$.items"
                  }
                }
              ],
              "edges": [
                {"source": "trigger_1", "target": "loop_1"}
              ]
            }
            """
        )
        self.assertEqual(payload["nodes"][0]["policy"]["capability_id"], "workflow.trigger.manual")
        self.assertEqual(payload["nodes"][1]["policy"]["capability_id"], "workflow.loop.for_each")
        self.assertEqual(payload["nodes"][1]["config"]["body"]["version"], "empyralist.workflow.v2")
        self.assertEqual(payload["nodes"][1]["config"]["body"]["nodes"][0]["policy"]["capability_id"], "workflow.data.transform")

    def test_parse_workflow_payload_flags_custom_api_signed_webhook_without_secret(self):
        payload = _parse_workflow_payload(
            """
            {
              "nodes": [
                {
                  "id": "tool_1",
                  "type": "tool",
                  "variant": "connector_action",
                  "config": {
                    "connector": "custom_api",
                    "action_id": "signed_webhook",
                    "url": "https://example.com/webhook"
                  }
                }
              ],
              "edges": []
            }
            """
        )
        self.assertTrue(
            any(issue.get("code") == "custom_api_signing_secret_missing" for issue in payload.get("issues", []))
        )

    def test_parse_workflow_payload_flags_custom_api_private_url(self):
        payload = _parse_workflow_payload(
            """
            {
              "nodes": [
                {
                  "id": "tool_1",
                  "type": "tool",
                  "variant": "connector_action",
                  "config": {
                    "connector": "custom_api",
                    "action_id": "http_request",
                    "url": "http://127.0.0.1:8080/hook"
                  }
                }
              ],
              "edges": []
            }
            """
        )
        self.assertTrue(
            any(issue.get("code") == "custom_api_private_url_disallowed" for issue in payload.get("issues", []))
        )

    def test_parse_workflow_payload_flags_code_tool_targeting_cloud(self):
        payload = _parse_workflow_payload(
            """
            {
              "nodes": [
                {
                  "id": "tool_1",
                  "type": "tool",
                  "variant": "code",
                  "config": {
                    "execution_target": "cloud"
                  }
                }
              ],
              "edges": []
            }
            """
        )
        self.assertTrue(
            any(issue.get("code") == "code_requires_local_companion_or_auto" for issue in payload.get("issues", []))
        )

    def test_parse_workflow_payload_flags_code_reviewed_execution_requirement(self):
        payload = _parse_workflow_payload(
            """
            {
              "nodes": [
                {
                  "id": "tool_1",
                  "type": "tool",
                  "variant": "code",
                  "config": {
                    "code": "print('ok')"
                  }
                }
              ],
              "edges": []
            }
            """
        )
        self.assertTrue(
            any(issue.get("code") == "code_reviewed_execution_path_required" for issue in payload.get("issues", []))
        )

    def test_parse_workflow_payload_preserves_trust_preset_and_remembered_grants(self):
        payload = _parse_workflow_payload(
            """
            {
              "nodes": [
                {
                  "id": "agent_1",
                  "type": "agent",
                  "config": {
                    "identity": {
                      "name": "Desktop agent"
                    },
                    "permissions": {
                      "trust_preset": "trusted_workflow",
                      "remembered_grants": {
                        "folders": ["/Users/mansur/Downloads"],
                        "browser_session": true,
                        "shell_capabilities": ["stack.status"]
                      }
                    }
                  }
                }
              ],
              "edges": []
            }
            """
        )
        permissions = payload["nodes"][0]["config"]["permissions"]
        self.assertEqual(permissions["trust_preset"], "trusted_workflow")
        self.assertEqual(
            permissions["remembered_grants"],
            {
                "folders": ["/Users/mansur/Downloads"],
                "browser_session": True,
                "shell_capabilities": ["stack.status"],
            },
        )

    def test_parse_workflow_payload_flags_shell_fields_on_code_tool(self):
        payload = _parse_workflow_payload(
            """
            {
              "nodes": [
                {
                  "id": "tool_1",
                  "type": "tool",
                  "variant": "code",
                  "config": {
                    "command": "python3 -c \\"print(1)\\""
                  }
                }
              ],
              "edges": []
            }
            """
        )
        self.assertTrue(
            any(issue.get("code") == "code_shell_fields_disallowed" for issue in payload.get("issues", []))
        )

    def test_parse_workflow_payload_flags_shell_tool_without_command(self):
        payload = _parse_workflow_payload(
            """
            {
              "nodes": [
                {
                  "id": "tool_1",
                  "type": "tool",
                  "variant": "shell",
                  "config": {}
                }
              ],
              "edges": []
            }
            """
        )
        self.assertTrue(any(issue.get("code") == "shell_command_missing" for issue in payload.get("issues", [])))

    def test_parse_workflow_payload_warns_on_raw_shell_command(self):
        payload = _parse_workflow_payload(
            """
            {
              "nodes": [
                {
                  "id": "tool_1",
                  "type": "tool",
                  "variant": "shell",
                  "config": {
                    "command": "pwd"
                  }
                }
              ],
              "edges": []
            }
            """
        )
        self.assertTrue(any(issue.get("code") == "shell_raw_command_high_trust" for issue in payload.get("issues", [])))

    def test_parse_workflow_payload_flags_browser_tool_without_url(self):
        payload = _parse_workflow_payload(
            """
            {
              "nodes": [
                {
                  "id": "tool_1",
                  "type": "tool",
                  "variant": "browser",
                  "config": {}
                }
              ],
              "edges": []
            }
            """
        )
        self.assertTrue(any(issue.get("code") == "browser_url_missing" for issue in payload.get("issues", [])))

    def test_parse_workflow_payload_flags_browser_tool_private_url(self):
        payload = _parse_workflow_payload(
            """
            {
              "nodes": [
                {
                  "id": "tool_1",
                  "type": "tool",
                  "variant": "browser",
                  "config": {
                    "url": "http://127.0.0.1:8000/admin"
                  }
                }
              ],
              "edges": []
            }
            """
        )
        self.assertTrue(any(issue.get("code") == "browser_private_url_disallowed" for issue in payload.get("issues", [])))

    def test_parse_workflow_payload_flags_instagram_reply_without_comment_id(self):
        payload = _parse_workflow_payload(
            """
            {
              "nodes": [
                {
                  "id": "tool_1",
                  "type": "tool",
                  "variant": "connector_action",
                  "config": {
                    "connector": "instagram_business",
                    "action_id": "publish_reply"
                  }
                }
              ],
              "edges": []
            }
            """
        )
        self.assertTrue(any(issue.get("code") == "instagram_comment_id_missing" for issue in payload.get("issues", [])))

    def test_parse_workflow_payload_flags_connector_trigger_without_event(self):
        payload = _parse_workflow_payload(
            """
            {
              "nodes": [
                {
                  "id": "trigger_1",
                  "type": "trigger",
                  "variant": "connector_event",
                  "config": {
                    "connector": "telegram_bot"
                  }
                }
              ],
              "edges": []
            }
            """
        )
        self.assertTrue(any(issue.get("code") == "trigger_event_missing" for issue in payload.get("issues", [])))

    def test_parse_workflow_payload_flags_shell_capability_conflict(self):
        payload = _parse_workflow_payload(
            """
            {
              "nodes": [
                {
                  "id": "tool_1",
                  "type": "tool",
                  "variant": "shell",
                  "config": {
                    "capability": "stack.status",
                    "command": "pwd"
                  }
                }
              ],
              "edges": []
            }
            """
        )
        self.assertTrue(any(issue.get("code") == "shell_capability_conflict" for issue in payload.get("issues", [])))

    def test_parse_workflow_payload_flags_authenticated_interactive_browser(self):
        payload = _parse_workflow_payload(
            """
            {
              "nodes": [
                {
                  "id": "tool_1",
                  "type": "tool",
                  "variant": "browser",
                  "config": {
                    "url": "https://example.com",
                    "session_profile": "default",
                    "browser_actions": [{"action": "click", "selector": "#login"}],
                    "permissions": {
                      "browser_permissions": {"allow": true}
                    }
                  }
                }
              ],
              "edges": []
            }
            """
        )
        self.assertTrue(any(issue.get("code") == "browser_reviewed_approval_required" for issue in payload.get("issues", [])))

    def test_parse_workflow_payload_warns_on_session_backed_browser(self):
        payload = _parse_workflow_payload(
            """
            {
              "nodes": [
                {
                  "id": "tool_1",
                  "type": "tool",
                  "variant": "browser",
                  "config": {
                    "url": "https://example.com/private",
                    "session_profile": "default",
                    "permissions": {
                      "browser_permissions": {"allow": true}
                    }
                  }
                }
              ],
              "edges": []
            }
            """
        )
        self.assertTrue(any(issue.get("code") == "browser_session_requires_approval" for issue in payload.get("issues", [])))

    async def test_builder_generate_returns_parsed_workflow(self):
        with (
            patch("server_modules.routes_builder.resolve_call_credentials") as resolve_mock,
            patch("server_modules.routes_builder.call_model") as call_model_mock,
        ):
            resolve_mock.return_value = {
                "provider": "openai",
                "model": "gpt-4o-mini",
                "credentials": {"api_key": "test-key"},
            }
            call_model_mock.return_value = {
                "content": """
                {
                  "version": "empyralist.workflow.v2",
                  "nodes": [
                    {
                      "id": "trigger_1",
                      "type": "trigger",
                      "variant": "schedule",
                      "label": "Daily trigger",
                      "subtitle": "Runs each morning",
                      "x": 100,
                      "y": 100
                    },
                    {
                      "id": "agent_1",
                      "type": "agent",
                      "label": "Planner",
                      "subtitle": "Plans the next move",
                      "x": 320,
                      "y": 100,
                      "config": {
                        "identity": {
                          "name": "Planner",
                          "goal": "Plan the next move"
                        }
                      }
                    }
                  ],
                  "edges": [{"source": "trigger_1", "target": "agent_1"}]
                }
                """,
                "usage": {"prompt_tokens": 11, "completion_tokens": 22, "total_tokens": 33},
                "model": "gpt-4o-mini",
                "provider": "openai",
            }

            result = await builder_generate(
                BuilderGenerateRequest(prompt="Build me a workflow", model="gpt-4o-mini", workspace_id="default"),
                current_user=self._current_user(),
            )

        self.assertEqual(result["provider"], "openai")
        self.assertEqual(result["model"], "gpt-4o-mini")
        self.assertEqual(result["usage"]["total_tokens"], 33)
        self.assertEqual(len(result["workflow"]["nodes"]), 2)
        self.assertEqual(result["workflow"]["nodes"][0]["variant"], "schedule")
        self.assertEqual(result["workflow"]["nodes"][1]["config"]["identity"]["name"], "Planner")
        self.assertEqual(result["workflow"]["edges"][0]["source"], "trigger_1")
        messages = call_model_mock.call_args.kwargs["messages"]
        self.assertTrue(any("Connector registry:" in str(item.get("content") or "") for item in messages))

    async def test_builder_generate_rejects_empty_prompt(self):
        with self.assertRaises(HTTPException) as ctx:
            await builder_generate(BuilderGenerateRequest(prompt="   "))
        self.assertEqual(ctx.exception.status_code, 400)

    async def test_builder_generate_rejects_invalid_model_output(self):
        with (
            patch("server_modules.routes_builder.resolve_call_credentials") as resolve_mock,
            patch("server_modules.routes_builder.call_model") as call_model_mock,
        ):
            resolve_mock.return_value = {
                "provider": "openai",
                "model": "gpt-4o-mini",
                "credentials": {"api_key": "test-key"},
            }
            call_model_mock.return_value = {
                "content": "not valid json",
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                "model": "gpt-4o-mini",
                "provider": "openai",
            }

            with self.assertRaises(HTTPException) as ctx:
                await builder_generate(
                    BuilderGenerateRequest(prompt="Build me a workflow", workspace_id="default"),
                    current_user=self._current_user(),
                )

        self.assertEqual(ctx.exception.status_code, 502)

    async def test_builder_connector_manifests_returns_seed_connectors(self):
        payload = await builder_connector_manifests()
        self.assertIn("items", payload)
        manifest_ids = {item["id"] for item in payload["items"]}
        self.assertIn("google_workspace", manifest_ids)
        self.assertIn("telegram_bot", manifest_ids)
        self.assertIn("custom_api", manifest_ids)


if __name__ == "__main__":
    unittest.main()
