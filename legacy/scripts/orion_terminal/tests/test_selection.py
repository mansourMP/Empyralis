from __future__ import annotations

import unittest
from dataclasses import dataclass
from typing import List

from scripts.orion_terminal.core import Choice
from scripts.orion_terminal.services.selection import collect_toggle_choices


@dataclass
class _FakeUI:
    picks: List[str]

    def choose(self, prompt: str, options: List[Choice], default_index: int = 0, title: str = "") -> Choice:
        key = self.picks.pop(0)
        for item in options:
            if item.key == key:
                return item
        raise AssertionError(f"choice key not found: {key}")


@dataclass
class _FakePrompter:
    picks: List[str]

    def multiselect(
        self,
        prompt: str,
        options: List[Choice],
        default_indexes: List[int] | None = None,
        title: str = "",
    ) -> List[Choice]:
        selected: List[Choice] = []
        for key in self.picks:
            for item in options:
                if item.key == key:
                    selected.append(item)
                    break
        return selected


class SelectionTests(unittest.TestCase):
    def test_toggle_and_continue(self) -> None:
        ui = _FakeUI(picks=["workspace", "model", "__continue"])
        result = collect_toggle_choices(
            ui=ui,
            prompt="Select",
            base_options=[Choice("workspace", "Workspace"), Choice("model", "Model")],
            title="Test",
            prompter=None,
        )
        self.assertEqual(result, ["workspace", "model"])

    def test_toggle_off(self) -> None:
        ui = _FakeUI(picks=["workspace", "workspace", "__continue"])
        result = collect_toggle_choices(
            ui=ui,
            prompt="Select",
            base_options=[Choice("workspace", "Workspace"), Choice("model", "Model")],
            title="Test",
            prompter=None,
        )
        self.assertEqual(result, [])

    def test_prompter_multiselect_path(self) -> None:
        ui = _FakeUI(picks=[])
        prompter = _FakePrompter(picks=["workspace", "model"])
        result = collect_toggle_choices(
            ui=ui,
            prompt="Select",
            base_options=[Choice("workspace", "Workspace"), Choice("model", "Model")],
            title="Test",
            prompter=prompter,
        )
        self.assertEqual(result, ["workspace", "model"])


if __name__ == "__main__":
    unittest.main()
