"""
Tests for the cc/attendees fields added to the real connector executors
alongside the new google_workspace/microsoft_365 send_email/draft_email/
create_calendar_event tool schemas (skills_service.py). The schemas expose
cc (email send) and attendees (calendar events); these tests prove the
fields are actually CONSUMED by the executor, not just accepted and
silently dropped:

  - google_workspace_cli.py: google_workspace_local_send_message /
    google_workspace_local_create_draft grew an optional cc_email param
    (local-CLI auth path).
  - microsoft_365_graph.py: microsoft_365_send_message /
    microsoft_365_create_draft grew an optional cc_email param, converted
    to Graph API ccRecipients.
  - runs_execution.py: the shared {google_workspace, microsoft_365}
    send_email/draft_email handler reads config["cc_email"]/config["cc"]
    and threads it through both the local-CLI and direct-HTTP-MIME paths;
    the shared create_calendar_event handler reads config["attendees"]
    and converts it into the Google Calendar (`attendees: [{"email":...}]`)
    or Microsoft Graph (`attendees: [{"emailAddress": {...}, "type":...}]`)
    payload shape.
"""

from __future__ import annotations

import base64
import unittest
from unittest.mock import patch

from server_modules import acp_manager
from server_modules import google_workspace_cli
from server_modules import microsoft_365_graph
from server_modules import runs_execution


# _workflow_execute_connector_action's write path
# (external_write_safety.execute_external_write_once's own gate check, PLUS
# acp_manager's idempotency-record persistence right after) goes through the
# Rust runtime kernel's write gate at two different call sites, each
# expecting `next_action` to echo back its own `operation` kwarg. Real
# server startup wires a real kernel client; this sandbox has none, so the
# gate must be mocked to "allow" for whatever operation it's asked about —
# same pre-existing pattern test_mcp_tool_calling_wiring.py's own module
# docstring documents for mcp_registry_service's write gate ("a pre-existing,
# unrelated test-infra gap, not something introduced by this work").
def _allow_runtime_state_store_decision(**request):
    return {
        "ok": True,
        "decision": "allow",
        "reason": "runtime_state_store_policy_satisfied",
        "operation": "runtime-state-store-decision",
        "next_action": request.get("operation"),
        "approval_required": False,
        "cacheable": False,
        "audit_visibility": "standard",
    }


# ── google_workspace_cli.py: local-CLI auth path ────────────────────────────


class GoogleWorkspaceCliCcTests(unittest.TestCase):
    def test_send_message_includes_cc_header_when_provided(self) -> None:
        captured = {}

        def _fake_run_gws(args, credentials, timeout=30):
            captured["args"] = args
            return {"id": "msg-1"}

        with patch.object(google_workspace_cli, "run_gws", side_effect=_fake_run_gws):
            google_workspace_cli.google_workspace_local_send_message(
                {}, "to@example.com", "Subject", "Body", cc_email="cc@example.com"
            )
        raw_json = captured["args"][-1]
        import json as _json

        raw = _json.loads(raw_json)["raw"]
        message = base64.urlsafe_b64decode(raw + "===").decode("utf-8")
        self.assertIn("Cc: cc@example.com", message)

    def test_send_message_omits_cc_header_when_absent(self) -> None:
        captured = {}

        def _fake_run_gws(args, credentials, timeout=30):
            captured["args"] = args
            return {"id": "msg-1"}

        with patch.object(google_workspace_cli, "run_gws", side_effect=_fake_run_gws):
            google_workspace_cli.google_workspace_local_send_message({}, "to@example.com", "Subject", "Body")
        raw_json = captured["args"][-1]
        import json as _json

        raw = _json.loads(raw_json)["raw"]
        message = base64.urlsafe_b64decode(raw + "===").decode("utf-8")
        self.assertNotIn("Cc:", message)

    def test_create_draft_includes_cc_header_when_provided(self) -> None:
        captured = {}

        def _fake_run_gws(args, credentials, timeout=30):
            captured["args"] = args
            return {"id": "draft-1"}

        with patch.object(google_workspace_cli, "run_gws", side_effect=_fake_run_gws):
            google_workspace_cli.google_workspace_local_create_draft(
                {}, "to@example.com", "Subject", "Body", cc_email="cc@example.com"
            )
        raw_json = captured["args"][-1]
        import json as _json

        raw = _json.loads(raw_json)["message"]["raw"]
        message = base64.urlsafe_b64decode(raw + "===").decode("utf-8")
        self.assertIn("Cc: cc@example.com", message)


# ── microsoft_365_graph.py: Graph API path ──────────────────────────────────


class Microsoft365GraphCcTests(unittest.TestCase):
    def test_cc_recipients_helper_splits_and_drops_blanks(self) -> None:
        recipients = microsoft_365_graph._microsoft_365_cc_recipients("a@example.com, b@example.com;  ,c@example.com")
        self.assertEqual(
            recipients,
            [
                {"emailAddress": {"address": "a@example.com"}},
                {"emailAddress": {"address": "b@example.com"}},
                {"emailAddress": {"address": "c@example.com"}},
            ],
        )

    def test_cc_recipients_helper_empty_string_returns_empty_list(self) -> None:
        self.assertEqual(microsoft_365_graph._microsoft_365_cc_recipients(""), [])

    def test_send_message_includes_cc_recipients_when_provided(self) -> None:
        captured = {}

        def _fake_http_json_request(url, method="GET", headers=None, payload=None, timeout=30):
            captured["payload"] = payload
            return {"status": 202, "json": {}}

        microsoft_365_graph.microsoft_365_send_message(
            {"access_token": "fake-token"}, _fake_http_json_request, "to@example.com", "Subject", "Body", cc_email="cc@example.com"
        )
        message = captured["payload"]["message"]
        self.assertEqual(message["ccRecipients"], [{"emailAddress": {"address": "cc@example.com"}}])

    def test_send_message_omits_cc_recipients_key_when_absent(self) -> None:
        captured = {}

        def _fake_http_json_request(url, method="GET", headers=None, payload=None, timeout=30):
            captured["payload"] = payload
            return {"status": 202, "json": {}}

        microsoft_365_graph.microsoft_365_send_message({"access_token": "fake-token"}, _fake_http_json_request, "to@example.com", "Subject", "Body")
        self.assertNotIn("ccRecipients", captured["payload"]["message"])

    def test_create_draft_includes_cc_recipients_when_provided(self) -> None:
        captured = {}

        def _fake_http_json_request(url, method="GET", headers=None, payload=None, timeout=30):
            captured["payload"] = payload
            return {"status": 201, "json": {}}

        microsoft_365_graph.microsoft_365_create_draft(
            {"access_token": "fake-token"}, _fake_http_json_request, "to@example.com", "Subject", "Body", cc_email="cc@example.com"
        )
        self.assertEqual(captured["payload"]["ccRecipients"], [{"emailAddress": {"address": "cc@example.com"}}])


# ── runs_execution.py: the shared executor handler, end to end ─────────────


class WorkflowExecuteConnectorActionCcAndAttendeesTests(unittest.TestCase):
    """_workflow_execute_connector_action itself, with just enough mocked
    (connector secret resolution + outbound HTTP + the mandate gate's
    authority_tier requirement) to reach the real cc/attendees-handling
    code added to the shared google_workspace/microsoft_365 handlers."""

    _OWNER_CONTEXT = {"workspace_id": "default", "metadata": {}, "authority_tier": "owner"}

    def test_google_workspace_send_email_cc_reaches_mime_message(self) -> None:
        captured = {}

        def _fake_http_json_request(url, method="GET", headers=None, payload=None, timeout=30):
            captured["url"] = url
            captured["payload"] = payload
            return {"status": 200, "json": {"id": "msg-1"}}

        with (
            patch(
                "server_modules.runs_execution._workflow_tool_connector_secret",
                return_value=("cred-google", "google_workspace", {"access_token": "fake-token"}),
            ),
            patch("server_modules.runs_execution.http_json_request", side_effect=_fake_http_json_request),
            patch.object(
                acp_manager.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                side_effect=_allow_runtime_state_store_decision,
            ),
        ):
            result = runs_execution._workflow_execute_connector_action(
                "run-1",
                "node-1",
                dict(self._OWNER_CONTEXT),
                {
                    "connector": "google_workspace",
                    "action_id": "send_email",
                    "to_email": "client@example.com",
                    "subject": "Update",
                    "text": "See attached.",
                    "cc_email": "manager@example.com",
                },
                current_text="",
            )

        self.assertIn("/messages/send", captured["url"])
        raw = captured["payload"]["raw"]
        message = base64.urlsafe_b64decode(raw + "===").decode("utf-8")
        self.assertIn("Cc: manager@example.com", message)
        self.assertEqual(result["result_data"]["connector_action"]["recipient"], "client@example.com")

    def test_google_workspace_create_calendar_event_attendees_reach_payload(self) -> None:
        captured = {}

        def _fake_http_json_request(url, method="GET", headers=None, payload=None, timeout=30):
            captured["url"] = url
            captured["payload"] = payload
            return {"status": 200, "json": {"id": "evt-1"}}

        with (
            patch(
                "server_modules.runs_execution._workflow_tool_connector_secret",
                return_value=("cred-google", "google_workspace", {"access_token": "fake-token"}),
            ),
            patch("server_modules.runs_execution.http_json_request", side_effect=_fake_http_json_request),
            patch.object(
                acp_manager.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                side_effect=_allow_runtime_state_store_decision,
            ),
        ):
            runs_execution._workflow_execute_connector_action(
                "run-1",
                "node-1",
                dict(self._OWNER_CONTEXT),
                {
                    "connector": "google_workspace",
                    "action_id": "create_calendar_event",
                    "title": "Kickoff",
                    "start": "2026-08-01T10:00:00Z",
                    "end": "2026-08-01T10:30:00Z",
                    "attendees": ["a@example.com", "b@example.com"],
                },
                current_text="",
            )

        self.assertIn("/events", captured["url"])
        self.assertEqual(
            captured["payload"]["attendees"],
            [{"email": "a@example.com"}, {"email": "b@example.com"}],
        )

    def test_microsoft_365_create_calendar_event_attendees_use_graph_shape(self) -> None:
        captured = {}

        def _fake_http_json_request(url, method="GET", headers=None, payload=None, timeout=30):
            captured["url"] = url
            captured["payload"] = payload
            return {"status": 200, "json": {"id": "evt-1"}}

        with (
            patch(
                "server_modules.runs_execution._workflow_tool_connector_secret",
                return_value=("cred-ms", "microsoft_365", {"access_token": "fake-token"}),
            ),
            patch("server_modules.runs_execution.http_json_request", side_effect=_fake_http_json_request),
            patch.object(
                acp_manager.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                side_effect=_allow_runtime_state_store_decision,
            ),
        ):
            runs_execution._workflow_execute_connector_action(
                "run-1",
                "node-1",
                dict(self._OWNER_CONTEXT),
                {
                    "connector": "microsoft_365",
                    "action_id": "create_calendar_event",
                    "title": "Kickoff",
                    "start": "2026-08-01T10:00:00Z",
                    "end": "2026-08-01T10:30:00Z",
                    "attendees": ["a@example.com"],
                },
                current_text="",
            )

        self.assertIn("/events", captured["url"])
        self.assertEqual(
            captured["payload"]["attendees"],
            [{"emailAddress": {"address": "a@example.com"}, "type": "required"}],
        )

    def test_create_calendar_event_without_attendees_omits_the_key(self) -> None:
        """Regression guard: no attendees supplied must not add an empty
        "attendees" key to the payload (both providers)."""
        captured = {}

        def _fake_http_json_request(url, method="GET", headers=None, payload=None, timeout=30):
            captured["payload"] = payload
            return {"status": 200, "json": {"id": "evt-1"}}

        with (
            patch(
                "server_modules.runs_execution._workflow_tool_connector_secret",
                return_value=("cred-google", "google_workspace", {"access_token": "fake-token"}),
            ),
            patch("server_modules.runs_execution.http_json_request", side_effect=_fake_http_json_request),
            patch.object(
                acp_manager.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                side_effect=_allow_runtime_state_store_decision,
            ),
        ):
            runs_execution._workflow_execute_connector_action(
                "run-1",
                "node-1",
                dict(self._OWNER_CONTEXT),
                {
                    "connector": "google_workspace",
                    "action_id": "create_calendar_event",
                    "title": "Solo block",
                    "start": "2026-08-01T10:00:00Z",
                    "end": "2026-08-01T10:30:00Z",
                },
                current_text="",
            )
        self.assertNotIn("attendees", captured["payload"])


if __name__ == "__main__":
    unittest.main()
