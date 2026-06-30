import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server_modules import sage_memory_service, workspace_context_memory_adapter


class SageMemoryRedStrippingTests(unittest.TestCase):
    def test_critical_restricted_memory_is_withheld_from_default_sage_context(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir) / "workspace-1"
            with patch("server_modules.sage_memory_service.workspace_context.workspace_scope_dir", return_value=root):
                sage_memory_service.upsert_memory_entry(
                    workspace_id="workspace-1",
                    category="critical_restricted",
                    title="Production API key",
                    content="sk-production-secret-123456",
                    actor_user_id="user-1",
                )

                block = sage_memory_service.build_sage_memory_context_block(workspace_id="workspace-1")

        self.assertIn("Restricted memory exists but is withheld from model context.", block)
        self.assertNotIn("sk-production-secret-123456", block)

    def test_workspace_context_payload_returns_red_stripped_blocks(self) -> None:
        with (
            patch(
                "server_modules.workspace_context_memory_adapter.read_workspace_context_files",
                return_value={
                    "USER.md": (
                        "# User\n"
                        "- Preferred name: Mansur\n"
                        "- [RED] Production API key: sk-secret123456789\n"
                    ),
                    "MEMORY.md": (
                        "# Memory\n"
                        "RED: private token abcdefghijklmnopqrstuvwxyz123456\n"
                        "- Safe preference: direct answers\n"
                    ),
                },
            ),
            patch(
                "server_modules.memory_service.get_recent_logs",
                return_value="RED: bank token abcdefghijklmnopqrstuvwxyz987654\nNormal log line.",
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
        ):
            payload = workspace_context_memory_adapter.load_workspace_context_payload(
                workspace_id="workspace-1",
                include_memory_context=True,
                policy_profile=type("Profile", (), {"max_recent_log_days": 7, "semantic_retrieval_k": 5})(),
            )

        rendered_blocks = "\n\n".join(payload["contextual_blocks"])
        self.assertIn("Preferred name: Mansur", rendered_blocks)
        self.assertIn("Safe preference: direct answers", rendered_blocks)
        self.assertIn("Normal log line.", rendered_blocks)
        self.assertIn("RED memory fact(s) stripped", rendered_blocks)
        self.assertNotIn("sk-secret123456789", rendered_blocks)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz123456", rendered_blocks)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz987654", rendered_blocks)

    def test_workspace_context_payload_strips_red_facts_from_mini_app_block(self) -> None:
        with (
            patch(
                "server_modules.workspace_context_memory_adapter.read_workspace_context_files",
                return_value={},
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
                return_value=(
                    "Mini App Summaries\n"
                    "- [RED] api key: sk-mini-app-secret-123456\n"
                    "- Safe trend: hydration improved"
                ),
            ),
        ):
            payload = workspace_context_memory_adapter.load_workspace_context_payload(
                workspace_id="workspace-1",
                include_memory_context=True,
                policy_profile=type("Profile", (), {"max_recent_log_days": 7, "semantic_retrieval_k": 5})(),
            )

        rendered_blocks = "\n\n".join(payload["contextual_blocks"])
        self.assertIn("Safe trend: hydration improved", rendered_blocks)
        self.assertIn("RED memory fact(s) stripped", rendered_blocks)
        self.assertNotIn("sk-mini-app-secret-123456", rendered_blocks)

    def test_workspace_context_payload_strips_red_facts_from_semantic_hits(self) -> None:
        with (
            patch(
                "server_modules.workspace_context_memory_adapter.read_workspace_context_files",
                return_value={},
            ),
            patch(
                "server_modules.memory_service.get_recent_logs",
                return_value="",
            ),
            patch(
                "server_modules.memory_service.semantic_search",
                return_value=[
                    {"key": "pricing_note", "content": "RED: discount token abcdefghijklmnopqrstuvwxyz123456"},
                    {"key": "safe_note", "content": "ask owner before discount over 15%"},
                ],
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
                memory_query="pricing",
                include_memory_context=True,
                policy_profile=type("Profile", (), {"max_recent_log_days": 7, "semantic_retrieval_k": 5})(),
            )

        rendered_blocks = "\n\n".join(payload["contextual_blocks"])
        self.assertIn("ask owner before discount over 15%", rendered_blocks)
        self.assertIn("RED memory fact(s) stripped", rendered_blocks)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz123456", rendered_blocks)


if __name__ == "__main__":
    unittest.main()
