from __future__ import annotations

import os
import unittest

from scripts.orion_terminal.core import Choice
from scripts.orion_terminal.tests.helpers import FakeUI, assert_snapshot
from scripts.orion_terminal.transcript import choice_log, configure_transcript, flag_log, step_banner, value_log


class TranscriptSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.prev_no_color = os.environ.get("NO_COLOR")
        os.environ["NO_COLOR"] = "1"

    def tearDown(self) -> None:
        if self.prev_no_color is None:
            os.environ.pop("NO_COLOR", None)
        else:
            os.environ["NO_COLOR"] = self.prev_no_color

    def test_transcript_default_snapshot(self) -> None:
        configure_transcript(False, False)
        ui = FakeUI()
        step_banner(ui, 1, 3, "Gateway Location")
        choice_log(ui, "Onboarding mode", Choice("quickstart", "QuickStart", "Configure later"))
        flag_log(ui, "Security acknowledged", True)
        value_log(ui, "Workspace", "default")
        assert_snapshot(ui.render(), "transcript_default")

    def test_transcript_compact_snapshot(self) -> None:
        configure_transcript(True, False)
        ui = FakeUI()
        step_banner(ui, 1, 3, "Gateway Location")
        choice_log(ui, "Execution", Choice("auto", "Auto", ""))
        value_log(ui, "Run mode", "General")
        assert_snapshot(ui.render(), "transcript_compact")


if __name__ == "__main__":
    unittest.main()

