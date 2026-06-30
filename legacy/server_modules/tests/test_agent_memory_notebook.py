import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server_modules import memory_service, workspace_context


class AgentMemoryNotebookTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="agent-memory-notebook-")
        self.addCleanup(self._tmpdir.cleanup)
        self._workspace_root = Path(self._tmpdir.name) / "workspace"
        self._workspace_root.mkdir(parents=True, exist_ok=True)
        self._workspace_patch = patch.object(workspace_context, "_WORKSPACE_DIR", self._workspace_root)
        self._workspace_patch.start()
        self.addCleanup(self._workspace_patch.stop)

    def test_search_memory_notebook_finds_memory_md_and_notes(self) -> None:
        workspace_context.write_workspace_context_file(
            "MEMORY.md",
            "# Curated Memory\n\n- favorite_food: sushi\n- timezone: Asia/Shanghai\n",
            workspace_id="default",
        )
        notes_dir = memory_service._workspace_memory_store._memory_notebook_dir("default")
        (notes_dir / "2026-04-02.md").write_text(
            "# 2026-04-02\n\nUser prefers night owl work sessions.\n",
            encoding="utf-8",
        )

        sushi_results = memory_service.search_memory_notebook("default", "favorite_food")
        self.assertTrue(any(item["path"] == "MEMORY.md" for item in sushi_results))

        session_results = memory_service.search_memory_notebook("default", "night owl")
        self.assertEqual(session_results[0]["path"], "memory/2026-04-02.md")
        self.assertIn("night owl", session_results[0]["snippet"].lower())

    def test_get_memory_notebook_excerpt_reads_requested_line_window(self) -> None:
        notes_dir = memory_service._workspace_memory_store._memory_notebook_dir("default")
        (notes_dir / "people.md").write_text(
            "# People\n\nAlice likes structured updates.\nBob prefers async check-ins.\n",
            encoding="utf-8",
        )

        excerpt = memory_service.get_memory_notebook_excerpt(
            "default",
            "memory/people.md",
            from_line=2,
            line_count=2,
        )

        self.assertEqual(excerpt["path"], "memory/people.md")
        self.assertEqual(excerpt["from_line"], 2)
        self.assertEqual(excerpt["to_line"], 3)
        self.assertIn("Alice likes structured updates.", excerpt["text"])
        self.assertNotIn("Bob prefers async check-ins.", excerpt["text"])

    def test_install_scoped_notebook_search_does_not_cross_specialist_boundaries(self) -> None:
        shared_notes_dir = memory_service._workspace_memory_store._memory_notebook_dir("default")
        install_a_notes_dir = memory_service._workspace_memory_store._memory_notebook_dir(
            "default",
            agent_install_id="install-a",
        )
        install_b_notes_dir = memory_service._workspace_memory_store._memory_notebook_dir(
            "default",
            agent_install_id="install-b",
        )
        (shared_notes_dir / "shared.md").write_text("# Shared\n\nShared fact.\n", encoding="utf-8")
        (install_a_notes_dir / "a.md").write_text("# A\n\nAlpha only fact.\n", encoding="utf-8")
        (install_b_notes_dir / "b.md").write_text("# B\n\nBravo only fact.\n", encoding="utf-8")

        a_results = memory_service.search_memory_notebook("default", "fact", agent_install_id="install-a")
        b_results = memory_service.search_memory_notebook("default", "fact", agent_install_id="install-b")

        self.assertEqual([item["path"] for item in a_results], ["memory/a.md"])
        self.assertEqual([item["path"] for item in b_results], ["memory/b.md"])
