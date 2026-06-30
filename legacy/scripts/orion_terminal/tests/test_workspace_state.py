from __future__ import annotations

import os
import tempfile
import unittest

from scripts.orion_terminal.state.workspace import (
    load_workspace_state,
    save_workspace_state,
    workspace_state_path,
)


class WorkspaceStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.prev_state_dir = os.environ.get("ORION_CLI_STATE_DIR")
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["ORION_CLI_STATE_DIR"] = self.tmp.name

    def tearDown(self) -> None:
        if self.prev_state_dir is None:
            os.environ.pop("ORION_CLI_STATE_DIR", None)
        else:
            os.environ["ORION_CLI_STATE_DIR"] = self.prev_state_dir
        self.tmp.cleanup()

    def test_load_default_when_missing(self) -> None:
        state = load_workspace_state("default")
        self.assertEqual(state.workspace_id, "default")
        self.assertEqual(state.preflight.execution_target_key, "local_companion")
        self.assertEqual(state.preflight.trust_mode_key, "auto")
        self.assertEqual(state.live_tui.session_filter, "")
        self.assertEqual(state.live_tui.channel_filter, "")
        self.assertFalse(workspace_state_path("default").exists())

    def test_save_and_load_roundtrip(self) -> None:
        state = load_workspace_state("team-prod")
        state.preflight.execution_target_key = "cloud"
        state.preflight.trust_mode_key = "strict"
        state.preflight.provider_key = "openai"
        state.live_tui.session_filter = "telegram:1932934047"
        state.live_tui.channel_filter = ""
        state.live_tui.input_route = "local_run"
        state.live_tui.engine = "codex"
        path = save_workspace_state("team-prod", state)
        self.assertTrue(os.path.exists(path))

        loaded = load_workspace_state("team-prod")
        self.assertEqual(loaded.workspace_id, "team-prod")
        self.assertEqual(loaded.preflight.execution_target_key, "cloud")
        self.assertEqual(loaded.preflight.trust_mode_key, "strict")
        self.assertEqual(loaded.preflight.provider_key, "openai")
        self.assertEqual(loaded.live_tui.session_filter, "telegram:1932934047")
        self.assertEqual(loaded.live_tui.input_route, "local_run")
        self.assertEqual(loaded.live_tui.engine, "codex")


if __name__ == "__main__":
    unittest.main()
