from __future__ import annotations

import unittest

from scripts.orion_terminal.tui.chat.command_registry import (
    ChatCommandInput,
    ChatCommandRegistry,
    ChatCommandResult,
)


def _sample_input(command: str) -> ChatCommandInput:
    return ChatCommandInput(
        raw=f"/{command}",
        command=command,
        argument="",
        parsed_kind="command",
        route_label="local_run",
        session_label="all",
        current_scope="all|auto",
        recent_events=[],
    )


class TuiChatRegistryTests(unittest.TestCase):
    def test_dispatch_returns_not_handled_for_missing_command(self) -> None:
        registry = ChatCommandRegistry()
        result = registry.dispatch(_sample_input("missing"))
        self.assertFalse(result.handled)
        self.assertFalse(result.exit_chat)

    def test_dispatch_normalizes_command_key(self) -> None:
        registry = ChatCommandRegistry()
        registry.register("HELP", lambda _item: ChatCommandResult(handled=True))
        result = registry.dispatch(_sample_input("help"))
        self.assertTrue(result.handled)

    def test_dispatch_treats_none_result_as_handled(self) -> None:
        registry = ChatCommandRegistry()
        registry.register("refresh", lambda _item: None)
        result = registry.dispatch(_sample_input("refresh"))
        self.assertTrue(result.handled)
        self.assertFalse(result.reset_frame)


if __name__ == "__main__":
    unittest.main()
