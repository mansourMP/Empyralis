import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server_modules import workspace_context_memory_adapter


class WorkspaceContextMemoryAdapterTests(unittest.TestCase):
    def test_load_workspace_context_payload_includes_identity_workspace_files(self):
        with (
            patch(
                "server_modules.workspace_context_memory_adapter.read_workspace_context_files",
                return_value={
                    "SOUL.md": "# Empyralis\n\n## Personal communication style\n\n- Be concise.\n",
                    "USER.md": "# User Profile\n\n- Preferred name: Mansur\n",
                    "IDENTITY.md": "# Identity\n\n- Role and focus: Agent platform founder.\n",
                    "HEARTBEAT.md": "# Heartbeat\n\n- [ ] Keep the inbox triaged.\n",
                    "MEMORY.md": "# Curated Memory\n\n- timezone: Asia/Shanghai\n",
                },
            ),
            patch(
                "server_modules.memory_service.get_recent_logs",
                return_value="",
            ),
            patch(
                "server_modules.memory_service.get_memory",
                return_value="",
            ),
            patch(
                "server_modules.sage_memory_service.build_sage_memory_context_block",
                return_value="",
            ),
            patch(
                "server_modules.sage_services_service.build_sage_services_memory_block",
                return_value="",
            ),
            patch(
                "server_modules.mini_apps_service.build_mini_apps_context_block",
                return_value="",
            ),
        ):
            payload = workspace_context_memory_adapter.load_workspace_context_payload(
                workspace_id="workspace-1",
                policy_profile=type("Profile", (), {"max_recent_log_days": 7, "semantic_retrieval_k": 5})(),
            )

        rendered = "\n\n".join(payload["contextual_blocks"])
        self.assertIn("SOUL.md", rendered)
        self.assertIn("IDENTITY.md", rendered)
        self.assertIn("HEARTBEAT.md", rendered)
        self.assertEqual(payload["diagnostics"]["context_file_count"], 5)

    def test_load_workspace_context_payload_includes_extended_root_context_and_memory_manifest(self):
        with (
            patch(
                "server_modules.workspace_context_memory_adapter.read_workspace_context_files",
                return_value={
                    "SOUL.md": "# Empyralis\n\n- Custom identity.\n",
                    "GOALS.md": "# Goals\n\n- Ship the phone app.\n",
                    "PROCEDURES.md": "# Procedures\n\n- Verify with screenshots.\n",
                    "memory/files/research.md": "# Research\n\nArchitecture comparison notes.",
                },
            ),
            patch("server_modules.memory_service.get_recent_logs", return_value=""),
            patch("server_modules.memory_service.get_memory", return_value=""),
            patch("server_modules.sage_memory_service.build_sage_memory_context_block", return_value=""),
            patch("server_modules.sage_services_service.build_sage_services_memory_block", return_value=""),
            patch("server_modules.mini_apps_service.build_mini_apps_context_block", return_value=""),
        ):
            payload = workspace_context_memory_adapter.load_workspace_context_payload(
                workspace_id="workspace-1",
                policy_profile=type("Profile", (), {"max_recent_log_days": 7, "semantic_retrieval_k": 5})(),
            )

        rendered = "\n\n".join(payload["contextual_blocks"])
        self.assertIn("GOALS.md", rendered)
        self.assertIn("PROCEDURES.md", rendered)
        self.assertIn("Available Memory Files", rendered)
        self.assertIn("memory/files/research.md", rendered)
        self.assertEqual(payload["diagnostics"]["context_file_count"], 3)
        self.assertEqual(payload["diagnostics"]["available_memory_file_count"], 1)

    def test_load_workspace_context_payload_skips_default_placeholder_files(self):
        with (
            patch(
                "server_modules.workspace_context_memory_adapter.read_workspace_context_files",
                return_value={
                    "SOUL.md": workspace_context_memory_adapter.workspace_context.DEFAULT_CONTEXT_FILE_CONTENTS["SOUL.md"],
                    "USER.md": "# User\n\n- Real preference.\n",
                },
            ),
            patch("server_modules.memory_service.get_recent_logs", return_value=""),
            patch("server_modules.memory_service.get_memory", return_value=""),
            patch("server_modules.sage_memory_service.build_sage_memory_context_block", return_value=""),
            patch("server_modules.sage_services_service.build_sage_services_memory_block", return_value=""),
            patch("server_modules.mini_apps_service.build_mini_apps_context_block", return_value=""),
        ):
            payload = workspace_context_memory_adapter.load_workspace_context_payload(
                workspace_id="workspace-1",
                policy_profile=type("Profile", (), {"max_recent_log_days": 7, "semantic_retrieval_k": 5})(),
            )

        rendered = "\n\n".join(payload["contextual_blocks"])
        self.assertIn("Real preference", rendered)
        self.assertNotIn("Empyralis is a calm mobile-first AI product", rendered)
        self.assertEqual(payload["diagnostics"]["context_file_count"], 1)

    def test_load_workspace_context_payload_skips_memory_blocks_for_unrelated_query(self):
        with (
            patch("server_modules.workspace_context_memory_adapter.read_workspace_context_files", return_value={}),
            patch("server_modules.memory_service.get_recent_logs", return_value="Recent log") as recent_logs,
            patch("server_modules.memory_service.semantic_search", return_value=[{"key": "timezone", "content": "Asia/Shanghai"}]) as semantic_search,
            patch("server_modules.memory_service.get_memory", return_value="- timezone: Asia/Shanghai") as get_memory,
            patch("server_modules.unified_memory_service.search_unified_memory_documents", return_value=[]) as unified_search,
            patch("server_modules.sage_memory_service.build_sage_memory_context_block", return_value="Sage memory") as sage_memory,
            patch("server_modules.sage_services_service.build_sage_services_memory_block", return_value="Sage services") as sage_services,
            patch("server_modules.mini_apps_service.build_mini_apps_context_block", return_value="Mini App Summaries") as mini_apps,
        ):
            payload = workspace_context_memory_adapter.load_workspace_context_payload(
                workspace_id="workspace-1",
                memory_query="what model are you?",
                policy_profile=type("Profile", (), {"max_recent_log_days": 7, "semantic_retrieval_k": 5})(),
            )

        rendered = "\n\n".join(payload["contextual_blocks"])
        self.assertEqual(rendered, "")
        self.assertEqual(payload["diagnostics"]["memory_context_included"], 0)
        recent_logs.assert_not_called()
        semantic_search.assert_not_called()
        get_memory.assert_not_called()
        unified_search.assert_not_called()
        sage_memory.assert_not_called()
        sage_services.assert_not_called()
        mini_apps.assert_not_called()

    def test_load_workspace_context_payload_includes_sage_memory_block_for_relevant_query(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir) / "workspace-1"
            with (
                patch("server_modules.workspace_context_memory_adapter.read_workspace_context_files", return_value={}),
                patch(
                    "server_modules.memory_service.get_recent_logs",
                    return_value="",
                ),
                patch(
                    "server_modules.memory_service.get_memory",
                    return_value="",
                ),
                patch(
                    "server_modules.sage_memory_service.build_sage_memory_context_block",
                    return_value="Sage memory\nProfile facts\n- Timezone: Uses Asia/Shanghai.",
                ),
                patch(
                    "server_modules.sage_services_service.build_sage_services_memory_block",
                    return_value="",
                ),
                patch(
                    "server_modules.mini_apps_service.build_mini_apps_context_block",
                    return_value="",
                ),
            ):
                payload = workspace_context_memory_adapter.load_workspace_context_payload(
                    workspace_id=str(root),
                    memory_query="what do you remember about my timezone?",
                    policy_profile=type("Profile", (), {"max_recent_log_days": 7, "semantic_retrieval_k": 5})(),
                )

        self.assertTrue(any("Sage memory" in block for block in payload["contextual_blocks"]))

    def test_load_workspace_context_payload_includes_sage_services_memory_block_for_relevant_query(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir) / "workspace-1"
            with (
                patch("server_modules.workspace_context_memory_adapter.read_workspace_context_files", return_value={}),
                patch(
                    "server_modules.memory_service.get_recent_logs",
                    return_value="",
                ),
                patch(
                    "server_modules.memory_service.get_memory",
                    return_value="",
                ),
                patch(
                    "server_modules.sage_memory_service.build_sage_memory_context_block",
                    return_value="",
                ),
                patch(
                    "server_modules.sage_services_service.build_sage_services_memory_block",
                    return_value="Sage services\nFlashcards\n- HSK1 review due.",
                ),
                patch(
                    "server_modules.mini_apps_service.build_mini_apps_context_block",
                    return_value="",
                ),
            ):
                payload = workspace_context_memory_adapter.load_workspace_context_payload(
                    workspace_id=str(root),
                    memory_query="what flashcards are review due?",
                    policy_profile=type("Profile", (), {"max_recent_log_days": 7, "semantic_retrieval_k": 5})(),
                )

        self.assertTrue(any("Sage services" in block for block in payload["contextual_blocks"]))

    def test_load_workspace_context_payload_force_skip_overrides_relevant_query(self):
        with (
            patch("server_modules.workspace_context_memory_adapter.read_workspace_context_files", return_value={}),
            patch("server_modules.memory_service.get_recent_logs", return_value="Recent log") as recent_logs,
            patch("server_modules.memory_service.get_memory", return_value="- timezone: Asia/Shanghai") as get_memory,
            patch("server_modules.sage_memory_service.build_sage_memory_context_block", return_value="Sage memory") as sage_memory,
            patch("server_modules.sage_services_service.build_sage_services_memory_block", return_value="Sage services"),
            patch("server_modules.mini_apps_service.build_mini_apps_context_block", return_value="Mini App Summaries"),
        ):
            payload = workspace_context_memory_adapter.load_workspace_context_payload(
                workspace_id="workspace-1",
                memory_query="what do you remember about my timezone?",
                include_memory_context=False,
                policy_profile=type("Profile", (), {"max_recent_log_days": 7, "semantic_retrieval_k": 5})(),
            )

        self.assertEqual(payload["contextual_blocks"], [])
        self.assertEqual(payload["diagnostics"]["memory_context_included"], 0)
        recent_logs.assert_not_called()
        get_memory.assert_not_called()
        sage_memory.assert_not_called()

    def test_load_workspace_context_payload_force_include_preserves_explicit_context_load(self):
        with (
            patch("server_modules.workspace_context_memory_adapter.read_workspace_context_files", return_value={}),
            patch("server_modules.memory_service.get_recent_logs", return_value="Recent log"),
            patch("server_modules.memory_service.get_memory", return_value="- timezone: Asia/Shanghai"),
            patch("server_modules.sage_memory_service.build_sage_memory_context_block", return_value="Sage memory"),
            patch("server_modules.sage_services_service.build_sage_services_memory_block", return_value="Sage services"),
            patch("server_modules.mini_apps_service.build_mini_apps_context_block", return_value="Mini App Summaries\n[Flashcards]\n- Review due."),
        ):
            payload = workspace_context_memory_adapter.load_workspace_context_payload(
                workspace_id="workspace-1",
                include_memory_context=True,
                policy_profile=type("Profile", (), {"max_recent_log_days": 7, "semantic_retrieval_k": 5})(),
            )

        rendered = "\n\n".join(payload["contextual_blocks"])
        self.assertIn("Recent Daily Logs", rendered)
        self.assertIn("Runtime Memory Facts", rendered)
        self.assertIn("Sage Memory", rendered)
        self.assertIn("Sage Services", rendered)
        self.assertIn("Mini App Summaries", rendered)
        self.assertEqual(payload["diagnostics"]["memory_context_included"], 1)

    def test_render_workspace_context_strips_red_facts_before_external_prompt(self):
        text = workspace_context_memory_adapter.render_workspace_context_text(
            [
                "MEMORY.md\n- [RED] Production API key: sk-secret123456789\n- Safe preference: concise replies",
                "Runtime Memory Facts\nRED: private token abcdefghijklmnopqrstuvwxyz123456\n- timezone: Asia/Shanghai",
            ]
        )

        self.assertIn("Safe preference", text)
        self.assertIn("timezone: Asia/Shanghai", text)
        self.assertIn("RED memory fact(s) stripped", text)
        self.assertNotIn("sk-secret123456789", text)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz123456", text)

    def test_load_workspace_context_payload_includes_compact_mini_app_summary_only(self):
        with (
            patch("server_modules.workspace_context_memory_adapter.read_workspace_context_files", return_value={}),
            patch("server_modules.memory_service.get_recent_logs", return_value=""),
            patch("server_modules.memory_service.get_memory", return_value=""),
            patch("server_modules.sage_memory_service.build_sage_memory_context_block", return_value=""),
            patch("server_modules.sage_services_service.build_sage_services_memory_block", return_value=""),
            patch(
                "server_modules.mini_apps_service.build_mini_apps_context_block",
                return_value=(
                    "Mini App Summaries\n"
                    "[Calorie Tracking]\n"
                    "- Daily summary: calories: 1800\n"
                    "- Retrieval hook: retrieve_records(filters) for narrow raw history slices."
                ),
            ),
        ):
            payload = workspace_context_memory_adapter.load_workspace_context_payload(
                workspace_id="workspace-1",
                memory_query="calorie tracking daily summary",
                policy_profile=type("Profile", (), {"max_recent_log_days": 7, "semantic_retrieval_k": 5})(),
            )

        rendered = "\n\n".join(payload["contextual_blocks"])
        self.assertIn("Mini App Summaries", rendered)
        self.assertIn("Retrieval hook", rendered)
        self.assertNotIn("raw transcript", rendered.lower())
        self.assertEqual(payload["diagnostics"]["mini_app_summary_count"], 1)

    def test_load_workspace_context_payload_separates_stable_and_retrieved_blocks(self):
        with (
            patch(
                "server_modules.workspace_context_memory_adapter.read_workspace_context_files",
                return_value={"USER.md": "# User\n- Preferred tone: concise"},
            ),
            patch("server_modules.memory_service.get_recent_logs", return_value="Recent line"),
            patch(
                "server_modules.memory_service.semantic_search",
                return_value=[{"key": "inventory", "content": "nike air max in stock"}],
            ),
            patch("server_modules.memory_service.get_memory", return_value=""),
            patch("server_modules.sage_memory_service.build_sage_memory_context_block", return_value=""),
            patch("server_modules.sage_services_service.build_sage_services_memory_block", return_value=""),
            patch("server_modules.mini_apps_service.build_mini_apps_context_block", return_value=""),
        ):
            payload = workspace_context_memory_adapter.load_workspace_context_payload(
                workspace_id="workspace-1",
                memory_query="remember inventory",
                policy_profile=type("Profile", (), {"max_recent_log_days": 7, "semantic_retrieval_k": 5})(),
            )

        stable = payload["stable_contextual_blocks"]
        retrieved = payload["retrieved_contextual_blocks"]
        self.assertTrue(any("USER.md" in block for block in stable))
        self.assertTrue(any("Recent Daily Logs" in block for block in retrieved))
        self.assertTrue(any("Retrieved Relevant Memory" in block for block in retrieved))
        self.assertEqual(payload["diagnostics"]["stable_context_block_count"], len(stable))
        self.assertEqual(payload["diagnostics"]["retrieved_context_block_count"], len(retrieved))

    def test_render_workspace_context_text_places_retrieved_section_after_stable(self):
        rendered = workspace_context_memory_adapter.render_workspace_context_text(
            [],
            stable_blocks=["USER.md\n- Preferred tone: concise"],
            retrieved_blocks=["Retrieved Relevant Memory\n- timezone: Asia/Shanghai"],
        )

        stable_index = rendered.index("Stable Memory Summary")
        retrieved_index = rendered.index("Retrieved Relevant Memory")
        self.assertLess(stable_index, retrieved_index)


if __name__ == "__main__":
    unittest.main()
