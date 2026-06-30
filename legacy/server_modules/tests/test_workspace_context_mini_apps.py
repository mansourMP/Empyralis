from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server_modules import mini_apps_service, workspace_context, workspace_context_memory_adapter
from server_modules.conversation_memory_policy import DIRECT_CHAT_PROFILE, get_memory_policy_profile


class WorkspaceContextMiniAppsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="workspace-context-mini-apps-")
        self._workspace_patch = patch.object(workspace_context, "_WORKSPACE_DIR", Path(self._tmpdir.name) / "workspace")
        self._workspace_patch.start()

    def tearDown(self) -> None:
        self._workspace_patch.stop()
        self._tmpdir.cleanup()

    def test_workspace_context_payload_includes_compact_mini_app_summaries_without_raw_history(self) -> None:
        mini_apps_service.upsert_mini_app_contract(
            "ws-1",
            "flashcards",
            label="Flashcards",
            current_state={"active_deck": "Spanish verbs"},
            weekly_summary={"trend": "recall is improving"},
            long_term_facts=["User struggles with irregular verbs"],
            records=[{"id": "raw-1", "summary": "full raw history should stay retrievable", "created_at": "2026-04-17T09:00:00Z"}],
        )

        with patch("server_modules.memory_service.get_recent_logs", return_value=""), patch(
            "server_modules.memory_service.semantic_search", return_value=[]
        ), patch("server_modules.memory_service.get_memory", return_value=""):
            payload = workspace_context_memory_adapter.load_workspace_context_payload(
                workspace_id="ws-1",
                memory_query="verbs",
                include_memory_context=True,
                policy_profile=get_memory_policy_profile(DIRECT_CHAT_PROFILE),
            )

        rendered = "\n\n".join(payload["contextual_blocks"])
        self.assertIn("Mini App Summaries", rendered)
        self.assertIn("active deck: Spanish verbs", rendered)
        self.assertEqual(payload["diagnostics"]["mini_app_summary_count"], 1)
        self.assertIn("retrieve_records(filters) for narrow raw history slices", rendered)
        self.assertNotIn("full raw history should stay retrievable", rendered)


if __name__ == "__main__":
    unittest.main()
