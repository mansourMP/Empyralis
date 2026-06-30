import json
import unittest
from unittest.mock import patch

from server_modules import direct_tool_config_service, runs_execution, skills_service, tool_availability_truth


class GoogleWorkspaceFetchEmailsDirectToolTests(unittest.TestCase):
    def test_google_workspace_verification_exposes_fetch_emails_as_read_tool_action(self) -> None:
        verification = tool_availability_truth.capability_verification_from_test_result(
            "google_workspace",
            {"ok": True},
        )

        self.assertIn("gmail_threads.read", verification["read_actions"])
        self.assertIn("fetch_emails", verification["write_actions"])
        self.assertNotIn("fetch_emails", verification["approval_required_actions"])

        tools = skills_service.build_direct_chat_tools(
            [
                {
                    "id": "google_workspace",
                    "label": "Google Workspace",
                    "connected": True,
                    **verification,
                }
            ]
        )
        by_name = {item["name"]: item for item in tools}

        self.assertIn("google_workspace__fetch_emails", by_name)
        fetch_tool = by_name["google_workspace__fetch_emails"]
        self.assertEqual(fetch_tool["connector_id"], "google_workspace")
        self.assertEqual(fetch_tool["action_id"], "fetch_emails")
        self.assertEqual(fetch_tool["action_class"], "read")
        self.assertFalse(fetch_tool["requires_approval"])

    def test_google_workspace_verification_exposes_calendar_and_drive_read_tools(self) -> None:
        verification = tool_availability_truth.capability_verification_from_test_result(
            "google_workspace",
            {"ok": True, "calendar_access": True, "files_access": True},
        )

        self.assertIn("calendar_events.read", verification["read_actions"])
        self.assertIn("drive_files.read", verification["read_actions"])
        self.assertIn("list_calendar_events", verification["write_actions"])
        self.assertIn("list_drive_files", verification["write_actions"])
        self.assertNotIn("list_calendar_events", verification["approval_required_actions"])
        self.assertNotIn("list_drive_files", verification["approval_required_actions"])

        tools = skills_service.build_direct_chat_tools(
            [
                {
                    "id": "google_workspace",
                    "label": "Google Workspace",
                    "connected": True,
                    **verification,
                }
            ]
        )
        by_name = {item["name"]: item for item in tools}

        self.assertEqual(by_name["google_workspace__list_calendar_events"]["action_class"], "read")
        self.assertFalse(by_name["google_workspace__list_calendar_events"]["requires_approval"])
        self.assertEqual(by_name["google_workspace__list_drive_files"]["action_class"], "read")
        self.assertFalse(by_name["google_workspace__list_drive_files"]["requires_approval"])

    def test_google_workspace_fetch_emails_direct_tool_config_parses_limit(self) -> None:
        config = skills_service.build_direct_tool_config(
            "google_workspace",
            "fetch_emails",
            '{"limit": 5}',
            parse_json_object_loose=json.loads,
        )

        self.assertEqual(config["connector"], "google_workspace")
        self.assertEqual(config["action_id"], "fetch_emails")
        self.assertEqual(config["limit"], 5)

    def test_google_workspace_calendar_and_drive_direct_tool_configs_parse_inputs(self) -> None:
        calendar_config = skills_service.build_direct_tool_config(
            "google_workspace",
            "list_calendar_events",
            '{"top": 3, "calendar_id": "primary", "time_min": "2026-06-07T00:00:00Z"}',
            parse_json_object_loose=json.loads,
        )
        drive_config = skills_service.build_direct_tool_config(
            "google_workspace",
            "list_drive_files",
            '{"path": "gdrive:/Projects", "top": 12}',
            parse_json_object_loose=json.loads,
        )

        self.assertEqual(calendar_config["top"], 3)
        self.assertEqual(calendar_config["calendar_id"], "primary")
        self.assertEqual(calendar_config["time_min"], "2026-06-07T00:00:00Z")
        self.assertEqual(drive_config["path"], "gdrive:/Projects")
        self.assertEqual(drive_config["top"], 12)

    def test_direct_tool_result_formatter_includes_sanitized_connector_read_result(self) -> None:
        formatted = direct_tool_config_service.format_direct_tool_result(
            {
                "summary": "Connector action completed: google_workspace.fetch_emails.",
                "result_data": {
                    "connector_action": {
                        "connector": "google_workspace",
                        "action_id": "fetch_emails",
                        "result": [{"subject": "Launch", "snippet": "Ready."}],
                    }
                },
            }
        )

        self.assertIn("Connector action completed", formatted)
        self.assertIn("Launch", formatted)
        self.assertIn("Ready.", formatted)

    def test_google_workspace_fetch_emails_executes_existing_recent_message_reader(self) -> None:
        with (
            patch(
                "server_modules.runs_execution._workflow_tool_connector_secret",
                return_value=("cred-google", "google_workspace", {"_provider": "google_workspace"}),
            ),
            patch(
                "server_modules.runs_execution.common.list_recent_connector_messages",
                return_value=[
                    {
                        "id": "msg-1",
                        "threadId": "thread-1",
                        "subject": "Launch",
                        "from": "sender@example.com",
                        "to": "owner@example.com",
                        "date": "2026-06-06T00:00:00Z",
                        "snippet": "Ready." + ("x" * 1400),
                        "body_text": "full private body must not pass through",
                    }
                ],
            ) as list_recent,
        ):
            result = runs_execution._workflow_execute_connector_action(
                "run-gmail-fetch",
                "node-gmail-fetch",
                {"workspace_id": "default", "metadata": {}},
                {
                    "connector": "google_workspace",
                    "action_id": "fetch_emails",
                    "limit": 4,
                },
                current_text="Summarize my latest Gmail messages.",
            )

        list_recent.assert_called_once()
        self.assertEqual(list_recent.call_args.kwargs["limit"], 4)
        connector_action = result["result_data"]["connector_action"]
        self.assertEqual(connector_action["connector"], "google_workspace")
        self.assertEqual(connector_action["credential_id"], "cred-google")
        self.assertEqual(connector_action["action_id"], "fetch_emails")
        self.assertEqual(connector_action["result"][0]["subject"], "Launch")
        self.assertEqual(connector_action["result"][0]["threadId"], "thread-1")
        self.assertIn("body_text", connector_action["result"][0])
        self.assertEqual(
            connector_action["result"][0]["body_text"],
            "full private body must not pass through",
        )
        self.assertLessEqual(len(connector_action["result"][0]["snippet"]), 1200)

    def test_google_workspace_calendar_events_direct_tool_executes_local_reader(self) -> None:
        with (
            patch(
                "server_modules.runs_execution._workflow_tool_connector_secret",
                return_value=("cred-google", "google_workspace", {"_provider": "google_workspace", "auth_mode": "gws_local"}),
            ),
            patch("server_modules.runs_execution.google_workspace_uses_local_cli", return_value=True),
            patch(
                "server_modules.runs_execution.google_workspace_local_list_calendar_events",
                return_value={
                    "calendar_id": "primary",
                    "events": [
                        {
                            "id": "evt-1",
                            "summary": "Standup",
                            "start": "2026-06-07T09:00:00Z",
                            "description": "Private context" + ("x" * 800),
                        }
                    ],
                },
            ) as list_events,
        ):
            result = runs_execution._workflow_execute_connector_action(
                "run-calendar-list",
                "node-calendar-list",
                {"workspace_id": "default", "metadata": {}},
                {
                    "connector": "google_workspace",
                    "action_id": "list_calendar_events",
                    "calendar_id": "primary",
                    "top": 2,
                },
                current_text="Prepare me for my next meeting.",
            )

        list_events.assert_called_once()
        self.assertEqual(list_events.call_args.kwargs["top"], 2)
        connector_action = result["result_data"]["connector_action"]
        self.assertEqual(connector_action["action_id"], "list_calendar_events")
        self.assertEqual(connector_action["calendar_id"], "primary")
        self.assertEqual(connector_action["result"][0]["summary"], "Standup")
        self.assertLessEqual(len(connector_action["result"][0]["description"]), 503)

    def test_google_workspace_drive_files_direct_tool_executes_local_reader(self) -> None:
        with (
            patch(
                "server_modules.runs_execution._workflow_tool_connector_secret",
                return_value=("cred-google", "google_workspace", {"_provider": "google_workspace", "auth_mode": "gws_local"}),
            ),
            patch("server_modules.runs_execution.google_workspace_uses_local_cli", return_value=True),
            patch(
                "server_modules.runs_execution.google_workspace_local_list_drive_children",
                return_value={
                    "path": "gdrive:/Projects",
                    "items": [
                        {
                            "id": "file-1",
                            "name": "Briefing Notes",
                            "kind": "file",
                            "path": "gdrive:/Projects/Briefing Notes",
                            "mimeType": "application/vnd.google-apps.document",
                        }
                    ],
                },
            ) as list_drive,
        ):
            result = runs_execution._workflow_execute_connector_action(
                "run-drive-list",
                "node-drive-list",
                {"workspace_id": "default", "metadata": {}},
                {
                    "connector": "google_workspace",
                    "action_id": "list_drive_files",
                    "path": "gdrive:/Projects",
                    "top": 4,
                },
                current_text="Find meeting files.",
            )

        list_drive.assert_called_once()
        self.assertEqual(list_drive.call_args.kwargs["path"], "gdrive:/Projects")
        self.assertEqual(list_drive.call_args.kwargs["top"], 4)
        connector_action = result["result_data"]["connector_action"]
        self.assertEqual(connector_action["action_id"], "list_drive_files")
        self.assertEqual(connector_action["path"], "gdrive:/Projects")
        self.assertEqual(connector_action["result"][0]["name"], "Briefing Notes")


if __name__ == "__main__":
    unittest.main()
