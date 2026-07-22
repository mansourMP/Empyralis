"""
Tests for real per-connector-action tool schemas and structured-args wiring —
docs/design/audit-tool-reliability.md finding G-1 (the audit's single
biggest reliability risk): every dynamically generated connector-action tool
(build_direct_chat_tools, skills_service.py) used to get ONE opaque
`{"input": string}` param and a description of `f"Execute {action} on
{label}"`. The model had to guess a free-text blob, and
build_direct_tool_config's regex heuristics (extract_first_email /
extract_subject_text / extract_body_text) guessed back at what it meant —
only wired for a handful of connectors, and never for microsoft_365 or
whatsapp_twilio at all (they fell through to the generic catch-all, which
can't extract a recipient/subject/etc. from anything).

Three things are covered here:
  1. build_direct_chat_tools emits real named-field JSON schemas (+ real
     descriptions) for the covered high-traffic actions, and leaves
     everything else on the legacy {"input": string} shape.
  2. build_direct_tool_config consumes `structured_args` (the model's real
     named arguments) directly when present, bypassing the tool_input
     regex-guessing path entirely — while the legacy string-blob path (no
     structured_args) behaves exactly as before, unchanged.
  3. _execute_custom_connector_tool_call_sync — the actual live dispatch
     path for a model-invoked connector-action tool call — passes the
     model's structured arguments through to build_direct_tool_config as
     structured_args, not just via the legacy tool_input string.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from server_modules import direct_chat_operator_binding_service
from server_modules import direct_tool_execution_service
from server_modules import skills_service


# ── 1. build_direct_chat_tools: real schemas ────────────────────────────────


class BuildDirectChatToolsSchemaTests(unittest.TestCase):
    def _tools_by_name(self, capabilities):
        tools = skills_service.build_direct_chat_tools(capabilities)
        return {t["name"]: t for t in tools}

    def _cap(self, connector_id, label, actions):
        return {
            "id": connector_id,
            "label": label,
            "runtime_usable": True,
            "write_actions": actions,
        }

    def test_google_workspace_send_email_has_named_fields(self):
        tools = self._tools_by_name([
            self._cap("google_workspace", "Google Workspace", ["send_email"]),
        ])
        tool = tools["google_workspace__send_email"]
        props = tool["parameters"]["properties"]
        self.assertEqual(set(props.keys()), {"to", "subject", "body", "cc"})
        self.assertEqual(set(tool["parameters"]["required"]), {"to", "subject", "body"})
        # Anthropic's "at least 3-4 sentences, when to use" bar, not the old
        # one-clause f"Execute {action} on {label}".
        sentence_count = tool["description"].count(". ") + 1
        self.assertGreaterEqual(sentence_count, 3)
        self.assertIn("email", tool["description"].lower())

    def test_google_workspace_draft_email_has_named_fields(self):
        tools = self._tools_by_name([
            self._cap("google_workspace", "Google Workspace", ["draft_email"]),
        ])
        tool = tools["google_workspace__draft_email"]
        props = tool["parameters"]["properties"]
        self.assertEqual(set(props.keys()), {"to", "subject", "body", "cc"})
        self.assertIn("draft", tool["description"].lower())

    def test_google_workspace_create_calendar_event_has_named_fields(self):
        tools = self._tools_by_name([
            self._cap("google_workspace", "Google Workspace", ["create_calendar_event"]),
        ])
        tool = tools["google_workspace__create_calendar_event"]
        props = tool["parameters"]["properties"]
        self.assertEqual(
            set(props.keys()),
            {"title", "start", "end", "attendees", "description", "timezone", "calendar_id"},
        )
        self.assertEqual(set(tool["parameters"]["required"]), {"title", "start", "end"})
        self.assertEqual(props["attendees"]["type"], "array")

    def test_microsoft_365_send_email_no_longer_falls_through_to_generic(self):
        """The audit's specific microsoft_365 finding: previously
        unhandled by _structured_connector_tool_schema, so it fell through
        to the generic {"input": string} shape same as any never-converted
        connector."""
        tools = self._tools_by_name([
            self._cap("microsoft_365", "Microsoft 365", ["send_email", "draft_email", "create_calendar_event"]),
        ])
        for name in ("microsoft_365__send_email", "microsoft_365__draft_email"):
            props = tools[name]["parameters"]["properties"]
            self.assertEqual(set(props.keys()), {"to", "subject", "body", "cc"})
            self.assertNotIn("input", props)
        cal_props = tools["microsoft_365__create_calendar_event"]["parameters"]["properties"]
        self.assertIn("attendees", cal_props)
        self.assertNotIn("input", cal_props)

    def test_smtp_send_email_has_named_fields_no_cc(self):
        """smtp's real executor (runs_execution.py) never reads a cc field
        for smtp — only google_workspace/microsoft_365 do — so the schema
        must not invent one for smtp."""
        tools = self._tools_by_name([
            self._cap("smtp", "SMTP Email", ["send_email"]),
        ])
        props = tools["smtp__send_email"]["parameters"]["properties"]
        self.assertEqual(set(props.keys()), {"to", "subject", "body"})

    def test_slack_send_message_and_send_dm_have_distinct_named_fields(self):
        tools = self._tools_by_name([
            self._cap("slack", "Slack", ["send_message", "send_dm"]),
        ])
        send_message_props = tools["slack__send_message"]["parameters"]["properties"]
        self.assertEqual(set(send_message_props.keys()), {"channel", "text"})
        send_dm_props = tools["slack__send_dm"]["parameters"]["properties"]
        self.assertEqual(set(send_dm_props.keys()), {"user_id", "text"})

    def test_telegram_bot_send_message_chat_id_optional(self):
        tools = self._tools_by_name([
            self._cap("telegram_bot", "Telegram Bot", ["send_message"]),
        ])
        tool = tools["telegram_bot__send_message"]
        props = tool["parameters"]["properties"]
        self.assertEqual(set(props.keys()), {"chat_id", "text"})
        self.assertEqual(tool["parameters"]["required"], ["text"])

    def test_discord_bot_send_message_has_channel_id_field(self):
        tools = self._tools_by_name([
            self._cap("discord_bot", "Discord Bot", ["send_message"]),
        ])
        props = tools["discord_bot__send_message"]["parameters"]["properties"]
        self.assertEqual(set(props.keys()), {"channel_id", "text"})

    def test_whatsapp_twilio_send_message_has_named_fields(self):
        """The task calls this connector "whatsapp"; the real registered
        connector id (connection_catalog_service.py, runs_execution.py) is
        "whatsapp_twilio" — the schema is keyed on the real id, not the
        shorthand, since that's what tool_name_for_action actually
        produces."""
        tools = self._tools_by_name([
            self._cap("whatsapp_twilio", "WhatsApp (Twilio)", ["send_message"]),
        ])
        props = tools["whatsapp_twilio__send_message"]["parameters"]["properties"]
        self.assertEqual(set(props.keys()), {"to_number", "text"})
        self.assertEqual(tools["whatsapp_twilio__send_message"]["parameters"]["required"], ["text"])

    def test_unconverted_action_keeps_legacy_input_schema(self):
        """Actions this change did NOT convert (e.g. slack post_reply,
        google_workspace fetch_emails) must keep working exactly as before —
        the generic single-string {"input": ...} shape, with the original
        f"Execute {action} on {label}" description."""
        tools = self._tools_by_name([
            self._cap("slack", "Slack", ["post_reply", "list_channels"]),
            self._cap("google_workspace", "Google Workspace", ["fetch_emails"]),
            self._cap("microsoft_365", "Microsoft 365", ["upload_drive_file"]),
        ])
        for name in (
            "slack__post_reply",
            "slack__list_channels",
            "google_workspace__fetch_emails",
            "microsoft_365__upload_drive_file",
        ):
            tool = tools[name]
            self.assertEqual(
                tool["parameters"],
                {
                    "type": "object",
                    "properties": {"input": {"type": "string", "description": "The input for this action"}},
                    "required": ["input"],
                },
            )
            self.assertTrue(tool["description"].startswith("Execute "))


# ── 2. build_direct_tool_config: structured_args ────────────────────────────


class BuildDirectToolConfigStructuredArgsTests(unittest.TestCase):
    def test_google_workspace_send_email_structured_args_bypass_regex(self):
        """When structured_args carries real named fields, none of the
        tool_input regex-extraction helpers should ever run."""
        with (
            patch.object(skills_service, "extract_first_email") as mock_email,
            patch.object(skills_service, "extract_subject_text") as mock_subject,
            patch.object(skills_service, "extract_body_text") as mock_body,
        ):
            config = skills_service.build_direct_tool_config(
                "google_workspace",
                "send_email",
                "",
                parse_json_object_loose=lambda value: {},
                structured_args={
                    "to": "client@example.com",
                    "subject": "Project update",
                    "body": "See attached report.",
                    "cc": "manager@example.com",
                },
            )
        mock_email.assert_not_called()
        mock_subject.assert_not_called()
        mock_body.assert_not_called()
        self.assertEqual(config["to_email"], "client@example.com")
        self.assertEqual(config["subject"], "Project update")
        self.assertEqual(config["text"], "See attached report.")
        self.assertEqual(config["cc_email"], "manager@example.com")

    def test_microsoft_365_send_email_structured_args_now_populated(self):
        """Before this change, microsoft_365 had no branch in
        build_direct_tool_config at all — it fell through to the generic
        `config["text"] = tool_input` catch-all, which the shared
        {google_workspace, microsoft_365} executor handler in
        runs_execution.py can't extract a recipient/subject from. Now it
        shares the same handling as google_workspace."""
        config = skills_service.build_direct_tool_config(
            "microsoft_365",
            "send_email",
            "",
            parse_json_object_loose=lambda value: {},
            structured_args={"to": "vendor@example.com", "subject": "PO #123", "body": "Confirming."},
        )
        self.assertEqual(config["to_email"], "vendor@example.com")
        self.assertEqual(config["subject"], "PO #123")
        self.assertEqual(config["text"], "Confirming.")
        self.assertNotIn("cc_email", config)

    def test_microsoft_365_create_calendar_event_structured_args_with_attendees(self):
        config = skills_service.build_direct_tool_config(
            "microsoft_365",
            "create_calendar_event",
            "",
            parse_json_object_loose=lambda value: {},
            structured_args={
                "title": "Kickoff",
                "start": "2026-08-01T10:00:00Z",
                "end": "2026-08-01T10:30:00Z",
                "attendees": ["a@example.com", "b@example.com"],
                "description": "Quarterly kickoff",
            },
        )
        self.assertEqual(config["title"], "Kickoff")
        self.assertEqual(config["start"], "2026-08-01T10:00:00Z")
        self.assertEqual(config["end"], "2026-08-01T10:30:00Z")
        self.assertEqual(config["attendees"], ["a@example.com", "b@example.com"])
        self.assertEqual(config["description"], "Quarterly kickoff")

    def test_attendees_normalized_from_comma_separated_string(self):
        config = skills_service.build_direct_tool_config(
            "google_workspace",
            "create_calendar_event",
            "",
            parse_json_object_loose=lambda value: {},
            structured_args={
                "title": "Sync",
                "start": "2026-08-01T10:00:00Z",
                "end": "2026-08-01T10:30:00Z",
                "attendees": "a@example.com, b@example.com; c@example.com",
            },
        )
        self.assertEqual(config["attendees"], ["a@example.com", "b@example.com", "c@example.com"])

    def test_attendees_normalized_from_list_of_dicts(self):
        emails = skills_service._normalize_attendee_emails(
            [{"email": "a@example.com"}, {"address": "b@example.com"}, "c@example.com", "", None]
        )
        self.assertEqual(emails, ["a@example.com", "b@example.com", "c@example.com"])

    def test_slack_send_message_structured_args_text_field_used_directly(self):
        config = skills_service.build_direct_tool_config(
            "slack",
            "send_message",
            "",
            parse_json_object_loose=lambda value: {},
            structured_args={"channel": "#general", "text": "Deploy is done."},
        )
        self.assertEqual(config["channel"], "#general")
        self.assertEqual(config["text"], "Deploy is done.")

    def test_discord_bot_send_message_structured_args(self):
        config = skills_service.build_direct_tool_config(
            "discord_bot",
            "send_message",
            "",
            parse_json_object_loose=lambda value: {},
            structured_args={"channel_id": "123456789012345678", "text": "Release shipped."},
        )
        self.assertEqual(config["channel_id"], "123456789012345678")
        self.assertEqual(config["text"], "Release shipped.")

    def test_telegram_bot_send_message_structured_args_chat_id_optional(self):
        config = skills_service.build_direct_tool_config(
            "telegram_bot",
            "send_message",
            "",
            parse_json_object_loose=lambda value: {},
            structured_args={"text": "Reminder: standup in 5."},
        )
        self.assertNotIn("chat_id", config)
        self.assertEqual(config["text"], "Reminder: standup in 5.")

    def test_whatsapp_twilio_send_message_new_branch(self):
        """whatsapp_twilio had no branch at all before this change — it
        fell through to the generic catch-all, which never set to_number,
        so runs_execution.py's WhatsApp handler always raised."""
        config = skills_service.build_direct_tool_config(
            "whatsapp_twilio",
            "send_message",
            "",
            parse_json_object_loose=lambda value: {},
            structured_args={"to_number": "+14155551234", "text": "Your order shipped."},
        )
        self.assertEqual(config["to_number"], "+14155551234")
        self.assertEqual(config["text"], "Your order shipped.")

    def test_legacy_string_blob_path_unchanged_without_structured_args(self):
        """Regression guard: calling with structured_args=None (or
        omitted) must behave exactly like before this change — the
        tool_input string is loosely parsed and regex-extracted."""
        config = skills_service.build_direct_tool_config(
            "smtp",
            "send_email",
            "email jane@example.com subject: Hello body: How are you?",
            parse_json_object_loose=lambda value: {},
        )
        self.assertEqual(config["to_email"], "jane@example.com")
        self.assertTrue(config["subject"].startswith("Hello"))
        self.assertIn("How are you?", config["text"])

    def test_empty_structured_args_dict_falls_back_to_legacy_path(self):
        """An empty dict is falsy -- must fall back to tool_input parsing,
        not silently produce an empty parsed_input that drops every field."""
        config = skills_service.build_direct_tool_config(
            "smtp",
            "send_email",
            "",
            parse_json_object_loose=lambda value: {"to": "x@example.com", "subject": "s", "body": "b"},
            structured_args={},
        )
        self.assertEqual(config["to_email"], "x@example.com")


# ── 3. _execute_custom_connector_tool_call_sync: live dispatch wiring ──────


class ExecuteCustomConnectorToolCallStructuredArgsWiringTests(unittest.TestCase):
    """The actual path a model-invoked connector-action tool call travels:
    skills_service._execute_custom_connector_tool_call_sync ->
    build_direct_tool_config -> runs_execution._workflow_execute_connector_action.
    Confirms structured tool-call arguments reach the executor's config with
    named fields resolved, not stuffed through a JSON-string round trip that
    the legacy callback (fixed 3-arg signature, can't carry structured_args)
    would have silently dropped back to.
    """

    def _callbacks(self) -> direct_tool_execution_service.DirectToolExecutionCallbacks:
        return direct_tool_execution_service.DirectToolExecutionCallbacks(
            compact_step_detail=lambda value: None,
            titleize_direct_step_token=lambda value: str(value or ""),
            run_async_tool_call=lambda awaitable: awaitable,
            parse_tool_name=direct_chat_operator_binding_service.parse_tool_name,
            tool_arguments_payload=lambda payload: payload if isinstance(payload, dict) else {},
            parse_json_object_loose=lambda value: {},
            safe_positive_int=lambda value, default=0: default,
            normalize_reasoning_effort=lambda value: None,
            build_direct_local_tool_config=lambda *a, **k: ("", {}),
            format_direct_local_tool_result=lambda result: json.dumps(result, ensure_ascii=False),
            # Deliberately a poisoned stub: if _execute_custom_connector_tool_call_sync
            # still routed through this callback (the old, fixed-signature
            # path) instead of calling build_direct_tool_config directly,
            # the resulting config would be exactly this dict and every
            # assertion below would fail loudly.
            build_direct_tool_config=lambda *a, **k: {"connector": "UNUSED_LEGACY_CALLBACK_PATH"},
            format_direct_tool_result=lambda result: json.dumps(result, ensure_ascii=False),
            llm_task=lambda *a, **k: {},
            web_search=lambda query: [],
            web_fetch=lambda url: "",
            search_memory_notebook=lambda *a, **k: [],
            get_memory_notebook_excerpt=lambda *a, **k: {},
        )

    def test_google_workspace_send_email_structured_tool_call_reaches_executor(self):
        captured = {}

        def _fake_execute(run_id, node_id, context, config, *, current_text):
            captured["config"] = config
            return {"summary": "sent", "result_data": {"connector_action": {}}}

        with (
            patch("server_modules.local_tool_executor.is_local_dev", return_value=False),
            patch(
                "server_modules.runs_execution._workflow_execute_connector_action",
                side_effect=_fake_execute,
            ),
        ):
            skills_service._execute_custom_connector_tool_call_sync(
                tool_call={
                    "name": "google_workspace__send_email",
                    "arguments": {
                        "to": "client@example.com",
                        "subject": "Update",
                        "body": "See attached.",
                        "cc": "manager@example.com",
                    },
                },
                workspace_id="ws-1",
                thread_id="thread-1",
                connector_id="google_workspace",
                action_id="send_email",
                callbacks=self._callbacks(),
            )

        config = captured["config"]
        self.assertEqual(config["connector"], "google_workspace")
        self.assertEqual(config["to_email"], "client@example.com")
        self.assertEqual(config["subject"], "Update")
        self.assertEqual(config["text"], "See attached.")
        self.assertEqual(config["cc_email"], "manager@example.com")

    def test_whatsapp_twilio_structured_tool_call_reaches_executor(self):
        captured = {}

        def _fake_execute(run_id, node_id, context, config, *, current_text):
            captured["config"] = config
            return {"summary": "sent", "result_data": {"connector_action": {}}}

        with (
            patch("server_modules.local_tool_executor.is_local_dev", return_value=False),
            patch(
                "server_modules.runs_execution._workflow_execute_connector_action",
                side_effect=_fake_execute,
            ),
        ):
            skills_service._execute_custom_connector_tool_call_sync(
                tool_call={
                    "name": "whatsapp_twilio__send_message",
                    "arguments": {"to_number": "+14155551234", "text": "Your order shipped."},
                },
                workspace_id="ws-1",
                thread_id="thread-1",
                connector_id="whatsapp_twilio",
                action_id="send_message",
                callbacks=self._callbacks(),
            )

        config = captured["config"]
        self.assertEqual(config["to_number"], "+14155551234")
        self.assertEqual(config["text"], "Your order shipped.")

    def test_legacy_input_string_tool_call_still_works(self):
        """A tool call using the old {"input": "<blob>"} shape (unconverted
        action, or an older client) must still work exactly as before —
        structured_args stays None, and tool_input carries the blob."""
        captured = {}

        def _fake_execute(run_id, node_id, context, config, *, current_text):
            captured["config"] = config
            return {"summary": "ok", "result_data": {"connector_action": {}}}

        with (
            patch("server_modules.local_tool_executor.is_local_dev", return_value=False),
            patch(
                "server_modules.runs_execution._workflow_execute_connector_action",
                side_effect=_fake_execute,
            ),
        ):
            skills_service._execute_custom_connector_tool_call_sync(
                tool_call={
                    "name": "smtp__send_email",
                    "arguments": {"input": "email jane@example.com subject: Hi body: Hello there"},
                },
                workspace_id="ws-1",
                thread_id="thread-1",
                connector_id="smtp",
                action_id="send_email",
                callbacks=self._callbacks(),
            )

        config = captured["config"]
        self.assertEqual(config["to_email"], "jane@example.com")
        self.assertTrue(config["subject"].startswith("Hi"))


if __name__ == "__main__":
    unittest.main()
