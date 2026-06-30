from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts.orion_terminal.tui.commands import (
    commands_hint,
    help_text,
    input_prompt_label,
    is_supported_command,
    parse_chat_input,
    parse_command_alias,
)


class TuiCommandsTests(unittest.TestCase):
    def test_prompt_label_is_stable(self) -> None:
        self.assertEqual(input_prompt_label(), "›")

    def test_commands_hint_includes_run(self) -> None:
        hint = commands_hint()
        self.assertIn("type goal", hint)
        self.assertIn("/run <goal>", hint)
        self.assertIn("/ok <id>", hint)
        self.assertIn("/help", hint)

    def test_help_text_hides_route_in_one_app_mode(self) -> None:
        with patch.dict("os.environ", {"ORION_CLI_ONE_APP_MODE": "1"}):
            rendered = help_text()
        self.assertNotIn("/route <local_run|telegram_send>", rendered)

    def test_help_text_shows_route_when_legacy_mode_enabled(self) -> None:
        with patch.dict("os.environ", {"ORION_CLI_ONE_APP_MODE": "0"}):
            rendered = help_text()
        self.assertIn("/route <local_run|telegram_send>", rendered)

    def test_parse_plain_text_as_run_when_prefix_not_required(self) -> None:
        parsed = parse_chat_input("hello world", require_run_prefix=False)
        self.assertEqual(parsed.kind, "run")
        self.assertEqual(parsed.goal, "hello world")

    def test_parse_plain_text_requires_prefix_when_enabled(self) -> None:
        parsed = parse_chat_input("hello world", require_run_prefix=True)
        self.assertEqual(parsed.kind, "command")
        self.assertEqual(parsed.command, "prefix_required")

    def test_parse_run_command(self) -> None:
        parsed = parse_chat_input("/run ship this", require_run_prefix=True)
        self.assertEqual(parsed.kind, "run")
        self.assertEqual(parsed.goal, "ship this")

    def test_parse_run_without_goal(self) -> None:
        parsed = parse_chat_input("/run", require_run_prefix=False)
        self.assertEqual(parsed.kind, "command")
        self.assertEqual(parsed.command, "run")

    def test_alias_mapping(self) -> None:
        self.assertEqual(parse_command_alias("models"), "model")
        self.assertEqual(parse_command_alias("agents"), "agent")
        self.assertEqual(parse_command_alias("sessions"), "session")
        self.assertEqual(parse_command_alias("elev"), "elevated")
        self.assertEqual(parse_command_alias("quit"), "exit")
        self.assertEqual(parse_command_alias("pending"), "approvals")
        self.assertEqual(parse_command_alias("approvalmenu"), "approval-menu")
        self.assertEqual(parse_command_alias("obj"), "objective")
        self.assertEqual(parse_command_alias("approveevent"), "approve-event")

    def test_supported_command_includes_openclaw_parity_additions(self) -> None:
        for token in [
            "sessions",
            "verbose",
            "reasoning",
            "usage",
            "elevated",
            "activation",
            "abort",
            "new",
            "reset",
            "approve-event",
            "reject-event",
            "approval-menu",
            "objective",
            "ok",
            "hold",
        ]:
            self.assertTrue(is_supported_command(token), token)


if __name__ == "__main__":
    unittest.main()
