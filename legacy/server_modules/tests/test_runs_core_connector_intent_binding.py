import unittest
from unittest.mock import patch

from fastapi import HTTPException

from server_modules import runs_core, runs_execution
from server_modules.runtime_models import RunStartRequest


class RunsCoreConnectorIntentBindingTests(unittest.TestCase):
    @patch(
        "server_modules.runs_core.resolve_vault_credential",
        return_value={"_provider": "telegram_bot", "chat_id": "chat-123"},
    )
    @patch(
        "server_modules.runs_core.list_vault_connectors",
        return_value=[
            {
                "id": "cred-telegram",
                "connector": "telegram_bot",
                "label": "Telegram Bot LIVE",
                "metadata": {
                    "capability_verification": {
                        "runtime_usable": True,
                    }
                },
                "updated_at": "2026-03-29T03:00:00Z",
            }
        ],
    )
    def test_prepare_run_start_request_binds_telegram_send_to_connector_workflow(
        self,
        _list_connectors_mock,
        _resolve_vault_mock,
    ):
        req = RunStartRequest(
            engine="orion",
            workspace_id="default",
            user_goal="Send a Telegram message to my test chat saying certification probe one.",
            metadata={"source": "operator_chat", "direct_chat": True, "execution_target": "local_companion"},
        )

        prepared = runs_core._prepare_run_start_request(req)

        metadata = prepared["metadata"]
        self.assertEqual(metadata.get("execution_target"), "cloud")
        self.assertEqual(metadata.get("connector_credential_id"), "cred-telegram")
        self.assertEqual(
            metadata.get("connector_write_intent", {}).get("action_id"),
            "send_message",
        )

        workflow_definition = metadata.get("workflow_definition")
        self.assertIsInstance(workflow_definition, dict)
        tool_node = workflow_definition["nodes"][1]
        self.assertEqual(tool_node["variant"], "connector_action")
        self.assertEqual(tool_node["config"]["connector"], "telegram_bot")
        self.assertEqual(tool_node["config"]["action_id"], "send_message")
        self.assertEqual(tool_node["config"]["chat_id"], "chat-123")
        self.assertEqual(tool_node["config"]["text"], "certification probe one.")
        self.assertEqual(_resolve_vault_mock.call_args.kwargs["connector_id"], "telegram_bot")
        self.assertEqual(_resolve_vault_mock.call_args.kwargs["action_id"], "send_message")
        self.assertEqual(_resolve_vault_mock.call_args.kwargs["allowed_fields"], ["chat_id", "bot_username"])

        precheck = runs_execution._compute_tool_policy_precheck(
            {
                "workspace_id": "default",
                "user_goal": req.user_goal,
                "metadata": metadata,
            }
        )

        self.assertEqual(precheck.get("tool_ids"), ["send_message"])
        self.assertEqual(precheck.get("require_confirmation"), ["send_message"])
        self.assertEqual(precheck.get("blocked_count"), 0)

    @patch(
        "server_modules.runs_core.list_vault_connectors",
        return_value=[
            {
                "id": "cred-google",
                "connector": "google_workspace",
                "label": "Google Workspace",
                "metadata": {
                    "emailAddress": "mansurao886@gmail.com",
                    "timezone": "UTC",
                    "capability_verification": {
                        "runtime_usable": True,
                    },
                },
                "updated_at": "2026-03-29T10:00:00Z",
            }
        ],
    )
    def test_prepare_run_start_request_binds_google_draft_to_self(self, _list_connectors_mock):
        req = RunStartRequest(
            engine="orion",
            workspace_id="default",
            user_goal="Draft an email to myself summarizing today's certification results.",
            metadata={"source": "operator_chat", "direct_chat": True, "execution_target": "auto"},
        )

        prepared = runs_core._prepare_run_start_request(req)

        metadata = prepared["metadata"]
        self.assertEqual(metadata.get("execution_target"), "cloud")
        self.assertEqual(metadata.get("connector_credential_id"), "cred-google")
        self.assertEqual(metadata.get("connector_write_intent", {}).get("action_id"), "draft_email")
        self.assertEqual(metadata.get("connector_write_intent", {}).get("target_label"), "mansurao886@gmail.com")

        workflow_definition = metadata.get("workflow_definition")
        self.assertIsInstance(workflow_definition, dict)
        tool_node = workflow_definition["nodes"][1]
        self.assertEqual(tool_node["config"]["connector"], "google_workspace")
        self.assertEqual(tool_node["config"]["action_id"], "draft_email")
        self.assertEqual(tool_node["config"]["to_email"], "mansurao886@gmail.com")

        precheck = runs_execution._compute_tool_policy_precheck(
            {
                "workspace_id": "default",
                "user_goal": req.user_goal,
                "metadata": metadata,
            }
        )

        self.assertEqual(precheck.get("tool_ids"), ["draft_email"])
        self.assertEqual(precheck.get("allowed"), [])
        self.assertEqual(precheck.get("require_confirmation"), ["draft_email"])
        self.assertEqual(precheck.get("approval_required"), ["draft_email"])

    @patch(
        "server_modules.runs_core.list_vault_connectors",
        return_value=[
            {
                "id": "cred-google",
                "connector": "google_workspace",
                "label": "Google Workspace",
                "metadata": {
                    "emailAddress": "mansurao886@gmail.com",
                    "timezone": "UTC",
                    "capability_verification": {
                        "runtime_usable": True,
                    },
                },
                "updated_at": "2026-03-29T10:00:00Z",
            }
        ],
    )
    def test_prepare_run_start_request_binds_natural_calendar_request(self, _list_connectors_mock):
        req = RunStartRequest(
            engine="orion",
            workspace_id="default",
            user_goal="Create a calendar event for tomorrow at 3pm called Product review.",
            metadata={"source": "operator_chat", "direct_chat": True, "execution_target": "auto"},
        )

        prepared = runs_core._prepare_run_start_request(req)

        metadata = prepared["metadata"]
        self.assertEqual(metadata.get("execution_target"), "cloud")
        self.assertEqual(metadata.get("connector_credential_id"), "cred-google")
        self.assertEqual(metadata.get("connector_write_intent", {}).get("action_id"), "create_calendar_event")

        workflow_definition = metadata.get("workflow_definition")
        self.assertIsInstance(workflow_definition, dict)
        tool_node = workflow_definition["nodes"][1]
        self.assertEqual(tool_node["config"]["connector"], "google_workspace")
        self.assertEqual(tool_node["config"]["action_id"], "create_calendar_event")
        self.assertEqual(tool_node["config"]["title"], "Product review")
        self.assertTrue(str(tool_node["config"]["start"]).strip())
        self.assertTrue(str(tool_node["config"]["end"]).strip())
        self.assertEqual(tool_node["config"]["timezone"], "UTC")

        precheck = runs_execution._compute_tool_policy_precheck(
            {
                "workspace_id": "default",
                "user_goal": req.user_goal,
                "metadata": metadata,
            }
        )

        self.assertEqual(precheck.get("tool_ids"), ["create_calendar_event"])
        self.assertEqual(precheck.get("require_confirmation"), ["create_calendar_event"])

    @patch(
        "server_modules.runs_core.list_vault_connectors",
        return_value=[
            {
                "id": "cred-google",
                "connector": "google_workspace",
                "label": "Google Workspace",
                "metadata": {
                    "emailAddress": "mansurao886@gmail.com",
                    "timezone": "UTC",
                    "capability_verification": {
                        "runtime_usable": True,
                    },
                },
                "updated_at": "2026-03-29T10:00:00Z",
            }
        ],
    )
    def test_prepare_run_start_request_rejects_invalid_email_recipient_early(self, _list_connectors_mock):
        req = RunStartRequest(
            engine="orion",
            workspace_id="default",
            user_goal="Send an email to invalid@localhost saying hello.",
            metadata={"source": "operator_chat", "direct_chat": True, "execution_target": "auto"},
        )

        with self.assertRaises(HTTPException) as raised:
            runs_core._prepare_run_start_request(req)

        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(raised.exception.detail, "Invalid email recipient for email connector action.")

    @patch(
        "server_modules.runs_core.list_vault_connectors",
        return_value=[
            {
                "id": "cred-google",
                "connector": "google_workspace",
                "label": "Google Workspace",
                "metadata": {
                    "emailAddress": "mansurao886@gmail.com",
                    "timezone": "UTC",
                    "capability_verification": {
                        "runtime_usable": True,
                    },
                },
                "updated_at": "2026-03-29T10:00:00Z",
            }
        ],
    )
    def test_prepare_run_start_request_rejects_non_email_recipient_early(self, _list_connectors_mock):
        req = RunStartRequest(
            engine="orion",
            workspace_id="default",
            user_goal="Send an email to not-an-email saying hello.",
            metadata={"source": "operator_chat", "direct_chat": True, "execution_target": "auto"},
        )

        with self.assertRaises(HTTPException) as raised:
            runs_core._prepare_run_start_request(req)

        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(raised.exception.detail, "Invalid email recipient for email connector action.")


if __name__ == "__main__":
    unittest.main()
