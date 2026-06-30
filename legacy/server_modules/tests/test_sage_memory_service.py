import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server_modules import sage_memory_service


class SageMemoryServiceTests(unittest.TestCase):
    def test_upsert_memory_entry_persists_category_and_trace(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir) / "workspace-1"
            with patch("server_modules.sage_memory_service.workspace_context.workspace_scope_dir", return_value=root):
                payload = sage_memory_service.upsert_memory_entry(
                    workspace_id="workspace-1",
                    category="private",
                    title="Preferred timezone",
                    content="Uses Asia/Shanghai for planning.",
                    pinned=True,
                    actor_user_id="user-1",
                )

            entry = payload["entry"]
            self.assertEqual(entry["category"], "private")
            self.assertTrue(entry["pinned"])
            self.assertEqual(entry["history"][0]["action"], "saved")
            self.assertTrue((root / "sage_memory.json").exists())

    def test_update_and_pin_memory_entry_append_trace_history(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir) / "workspace-1"
            with patch("server_modules.sage_memory_service.workspace_context.workspace_scope_dir", return_value=root):
                created = sage_memory_service.upsert_memory_entry(
                    workspace_id="workspace-1",
                    category="safe_general",
                    title="Current project",
                    content="Preparing the private beta launch plan.",
                    actor_user_id="user-1",
                )
                entry_id = created["entry"]["id"]
                updated = sage_memory_service.upsert_memory_entry(
                    workspace_id="workspace-1",
                    entry_id=entry_id,
                    category="safe_general",
                    title="Current project",
                    content="Preparing the Sage private beta launch plan.",
                    actor_user_id="user-2",
                )
                pinned = sage_memory_service.set_memory_entry_pinned(
                    workspace_id="workspace-1",
                    entry_id=entry_id,
                    pinned=True,
                    actor_user_id="user-3",
                )

            self.assertEqual(updated["entry"]["history"][0]["action"], "corrected")
            self.assertEqual(pinned["entry"]["history"][0]["action"], "pinned")
            self.assertTrue(pinned["entry"]["pinned"])

    def test_build_sage_memory_context_block_groups_categories(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir) / "workspace-1"
            with patch("server_modules.sage_memory_service.workspace_context.workspace_scope_dir", return_value=root):
                sage_memory_service.upsert_memory_entry(
                    workspace_id="workspace-1",
                    category="private",
                    title="Timezone",
                    content="Uses Asia/Shanghai.",
                    actor_user_id="user-1",
                )
                sage_memory_service.upsert_memory_entry(
                    workspace_id="workspace-1",
                    category="sensitive",
                    title="Status updates",
                    content="Prefers concise next-step updates.",
                    pinned=True,
                    actor_user_id="user-1",
                )
                block = sage_memory_service.build_sage_memory_context_block(workspace_id="workspace-1")

            self.assertIn("Sage memory", block)
            self.assertIn("Private", block)
            self.assertIn("Sensitive", block)
            self.assertIn("[pinned]", block)

    def test_critical_memory_is_withheld_from_default_context(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir) / "workspace-1"
            with patch("server_modules.sage_memory_service.workspace_context.workspace_scope_dir", return_value=root):
                sage_memory_service.upsert_memory_entry(
                    workspace_id="workspace-1",
                    category="critical_restricted",
                    title="Production API key",
                    content="sk-secret123456",
                    actor_user_id="user-1",
                )
                default_block = sage_memory_service.build_sage_memory_context_block(workspace_id="workspace-1")
                restricted_block = sage_memory_service.build_sage_memory_context_block(
                    workspace_id="workspace-1",
                    include_restricted=True,
                )

            self.assertIn("Critical", default_block)
            self.assertIn("withheld from model context", default_block)
            self.assertNotIn("sk-secret123456", default_block)
            self.assertIn("sk-secret123456", restricted_block)

    def test_upsert_memory_entry_enforces_workspace_memory_limit(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir) / "workspace-1"
            with patch("server_modules.sage_memory_service.workspace_context.workspace_scope_dir", return_value=root):
                for index in range(sage_memory_service.SAGE_MEMORY_ENTRY_LIMIT):
                    sage_memory_service.upsert_memory_entry(
                        workspace_id="workspace-1",
                        category="safe_general",
                        title=f"Memory {index}",
                        content=f"Content {index}",
                        actor_user_id="user-1",
                    )

                with self.assertRaises(Exception) as context:
                    sage_memory_service.upsert_memory_entry(
                        workspace_id="workspace-1",
                        category="safe_general",
                        title="Overflow",
                        content="Should not persist.",
                        actor_user_id="user-1",
                    )

            self.assertEqual(getattr(context.exception, "status_code", None), 409)

    def test_legacy_memory_categories_map_to_sensitivity_classes(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir) / "workspace-1"
            with patch("server_modules.sage_memory_service.workspace_context.workspace_scope_dir", return_value=root):
                payload = sage_memory_service.upsert_memory_entry(
                    workspace_id="workspace-1",
                    category="personal_context",
                    title="Timezone",
                    content="Uses Asia/Shanghai.",
                    actor_user_id="user-1",
                )

            self.assertEqual(payload["entry"]["category"], "private")

    def test_export_memory_returns_structured_payload_and_markdown(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir) / "workspace-1"
            with patch("server_modules.sage_memory_service.workspace_context.workspace_scope_dir", return_value=root):
                sage_memory_service.upsert_memory_entry(
                    workspace_id="workspace-1",
                    category="safe_general",
                    title="Launch focus",
                    content="Keep the public demo web-first.",
                    actor_user_id="user-1",
                )
                payload = sage_memory_service.export_sage_memory(workspace_id="workspace-1")

            self.assertEqual(payload["export_type"], "sage_memory")
            self.assertEqual(payload["summary"]["total_count"], 1)
            self.assertEqual(payload["storage_policy"]["authority"], "cloud_canonical")
            self.assertIn("Launch focus", payload["markdown"])
            self.assertIn("structured classes", payload["markdown"])

    def test_wipe_memory_requires_confirmation_and_clears_entries(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir) / "workspace-1"
            with patch("server_modules.sage_memory_service.workspace_context.workspace_scope_dir", return_value=root):
                sage_memory_service.upsert_memory_entry(
                    workspace_id="workspace-1",
                    category="private",
                    title="Timezone",
                    content="Uses Asia/Shanghai.",
                    actor_user_id="user-1",
                )
                with self.assertRaises(Exception) as context:
                    sage_memory_service.wipe_sage_memory(
                        workspace_id="workspace-1",
                        actor_user_id="user-1",
                        confirm="wipe",
                    )
                wiped = sage_memory_service.wipe_sage_memory(
                    workspace_id="workspace-1",
                    actor_user_id="user-1",
                    confirm=sage_memory_service.SAGE_MEMORY_WIPE_CONFIRMATION,
                )

            self.assertEqual(getattr(context.exception, "status_code", None), 400)
            self.assertEqual(wiped["deleted_count"], 1)
            self.assertEqual(wiped["summary"]["total_count"], 0)
            self.assertEqual(wiped["storage_policy"]["used_entries"], 0)


if __name__ == "__main__":
    unittest.main()
