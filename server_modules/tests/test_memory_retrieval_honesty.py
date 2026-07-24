"""Memory retrieval honesty (docs/design/memory-retrieval-reliability.md):
a failed or skipped retrieval must never be indistinguishable from a
confirmed-empty one. This is the founder's exact hallucination fear -- a
weak tool-calling model that receives an ambiguous empty result treats it as
"nothing exists" and invents content.

Covers the three fixes from that report:

  FIX 1 (agent_memory._search_memory_notebook, vector 1): a bare
  `{"results": []}` used to be returned for both "no query given" and
  "searched everything, genuinely zero matches" -- structurally
  indistinguishable. The response is now a self-describing envelope with an
  explicit `status` ("not_searched" vs "no_matches" vs "matches_found" vs
  "incomplete") and `files_searched`, so the model can tell "I checked N
  files and there is genuinely nothing" from "your query was empty so I did
  nothing."

  FIX 2 (same function, vector 2): a topic file that exists and might
  contain the answer, but can't be read (encoding issue, permission race),
  used to silently `continue` -- disappearing from the candidate set exactly
  like a file that never existed. It now surfaces in an explicit `errors`
  list (path + reason), and a search that hit unreadable files with zero
  matches is reported as "incomplete", never "no_matches" -- the model must
  not be told "confirmed nothing" when the view was incomplete.

  FIX 3 (workspace_context.py / memory_service.memory_read_file, vector 3):
  `memory_read` on a nonexistent topic-file path used to return
  `is_default: False` -- structurally indistinguishable from a topic file
  that was created and is legitimately empty (both read as "real, curated,
  non-placeholder content that happens to be empty"). An explicit `exists`
  field now disambiguates the two.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server_modules import agent_memory, memory_service, workspace_context


class _NotebookTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="memory-retrieval-honesty-")
        self.addCleanup(self._tmpdir.cleanup)
        self._workspace_root = Path(self._tmpdir.name) / "workspace"
        self._workspace_root.mkdir(parents=True, exist_ok=True)
        self._workspace_patch = patch.object(workspace_context, "_WORKSPACE_DIR", self._workspace_root)
        self._workspace_patch.start()
        self.addCleanup(self._workspace_patch.stop)

    def _notes_dir(self, workspace_id: str = "ws-1") -> Path:
        return memory_service._workspace_memory_store._memory_notebook_dir(workspace_id)


# ── FIX 1: empty/missing query ("not searched") vs a real, confirmed-empty ──
# ── search ("no matches") must be structurally distinguishable ─────────────


class TestSearchDistinguishesNotSearchedFromConfirmedEmpty(_NotebookTestCase):
    def test_empty_query_reports_not_searched_and_touches_zero_files(self) -> None:
        (self._notes_dir() / "notes.md").write_text("# Notes\n\nsomething here\n", encoding="utf-8")

        result = agent_memory._search_memory_notebook("ws-1", "")

        self.assertEqual(result["status"], "not_searched")
        self.assertEqual(result["files_searched"], 0)
        self.assertEqual(result["results"], [])
        self.assertEqual(result["errors"], [])
        # Model-facing: must say nothing was searched, not that a search
        # came back empty -- these are different facts.
        self.assertIn("no query", result["message"].lower())

    def test_whitespace_only_query_is_also_not_searched(self) -> None:
        result = agent_memory._search_memory_notebook("ws-1", "   ")
        self.assertEqual(result["status"], "not_searched")
        self.assertEqual(result["files_searched"], 0)

    def test_missing_query_argument_is_also_not_searched(self) -> None:
        result = agent_memory._search_memory_notebook("ws-1", None)  # type: ignore[arg-type]
        self.assertEqual(result["status"], "not_searched")

    def test_real_query_with_zero_matches_is_confirmed_not_not_searched(self) -> None:
        (self._notes_dir() / "notes.md").write_text(
            "# Notes\n\nfavorite color is teal\n", encoding="utf-8",
        )

        result = agent_memory._search_memory_notebook("ws-1", "zzznonexistenttoken")

        self.assertEqual(result["status"], "no_matches")
        self.assertEqual(result["results"], [])
        self.assertGreaterEqual(result["files_searched"], 1)
        self.assertEqual(result["errors"], [])
        # Model-facing: must say it WAS searched -- distinct wording from
        # the not_searched case, and must not claim a search never happened.
        self.assertIn("searched", result["message"].lower())
        self.assertNotIn("no query", result["message"].lower())

    def test_not_searched_and_no_matches_share_the_bare_shape_but_differ_in_meaning(self) -> None:
        """The exact regression this fix targets: both cases have `results
        == []`, so a caller reading only `results` still can't tell them
        apart -- `status` and `message` are what disambiguate."""
        (self._notes_dir() / "notes.md").write_text(
            "# Notes\n\nfavorite color is teal\n", encoding="utf-8",
        )

        not_searched = agent_memory._search_memory_notebook("ws-1", "")
        no_matches = agent_memory._search_memory_notebook("ws-1", "zzznonexistenttoken")

        self.assertEqual(not_searched["results"], no_matches["results"])
        self.assertNotEqual(not_searched["status"], no_matches["status"])
        self.assertNotEqual(not_searched["message"], no_matches["message"])
        self.assertNotEqual(not_searched["files_searched"], no_matches["files_searched"])

    def test_matches_found_status_when_query_hits(self) -> None:
        (self._notes_dir() / "notes.md").write_text(
            "# Notes\n\nfavorite color is teal\n", encoding="utf-8",
        )

        result = agent_memory._search_memory_notebook("ws-1", "favorite color")

        self.assertEqual(result["status"], "matches_found")
        self.assertTrue(result["results"])
        self.assertEqual(result["errors"], [])

    def test_genuinely_empty_notebook_is_still_a_confirmed_no_matches_not_not_searched(self) -> None:
        """No topic files exist at all yet (a brand-new agent) -- a valid
        query against an empty notebook is still a confirmed search, not a
        bad-input case; the model should say "nothing saved yet", not treat
        this like its own query was rejected."""
        result = agent_memory._search_memory_notebook("ws-1", "anything")
        self.assertEqual(result["status"], "no_matches")
        self.assertEqual(result["files_searched"], 0)


# ── FIX 2: a topic file that exists but fails to read must surface in an ───
# ── explicit `errors` list, never silently vanish from the candidate set ───


class TestUnreadableFilesSurfaceInErrors(_NotebookTestCase):
    def test_undecodable_file_is_reported_in_errors_not_dropped(self) -> None:
        notes_dir = self._notes_dir()
        notes_dir.joinpath("readable.md").write_text(
            "# Readable\n\nthe answer is teal\n", encoding="utf-8",
        )
        # Invalid UTF-8 byte sequence -- read_text(encoding="utf-8") raises
        # UnicodeDecodeError, simulating an encoding/permission-race failure
        # portably (no platform-specific chmod tricks needed).
        notes_dir.joinpath("corrupt.md").write_bytes(
            b"\xff\xfe\x00 the answer is teal but this file is unreadable\n"
        )

        result = agent_memory._search_memory_notebook("ws-1", "teal")

        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(result["errors"][0]["path"], "memory/corrupt.md")
        self.assertTrue(result["errors"][0]["reason"])
        # The readable file's match must still come through -- one bad file
        # must not sink the whole search.
        self.assertTrue(any(item["path"] == "memory/readable.md" for item in result["results"]))
        # files_searched only counts files actually opened and scanned.
        self.assertEqual(result["files_searched"], 1)

    def test_all_candidates_unreadable_with_zero_matches_is_incomplete_not_no_matches(self) -> None:
        self._notes_dir().joinpath("corrupt.md").write_bytes(b"\xff\xfe\x00 unreadable content\n")

        result = agent_memory._search_memory_notebook("ws-1", "anything")

        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["results"], [])
        self.assertEqual(len(result["errors"]), 1)
        # Model-facing: must not claim a confirmed "nothing" when the view
        # of the notebook was incomplete -- that would itself be a
        # fabrication dressed up as a fact.
        self.assertNotIn("confirmed", result["message"].lower())
        self.assertIn("do not conclude", result["message"].lower())

    def test_unreadable_file_alongside_a_match_still_flags_the_gap(self) -> None:
        notes_dir = self._notes_dir()
        notes_dir.joinpath("readable.md").write_text(
            "# Readable\n\nteal is the answer\n", encoding="utf-8",
        )
        notes_dir.joinpath("corrupt.md").write_bytes(b"\xff\xfe\x00 teal too\n")

        result = agent_memory._search_memory_notebook("ws-1", "teal")

        self.assertEqual(result["status"], "matches_found")
        self.assertTrue(result["results"])
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("could not be read", result["message"].lower())


# ── FIX 3: memory_read must report `exists` -- a missing topic file and a ──
# ── curated-but-empty one both read `is_default: False` today, and that ────
# ── alone is not enough to tell them apart ──────────────────────────────────


class TestMemoryReadExistsField(_NotebookTestCase):
    def test_missing_topic_file_reports_exists_false(self) -> None:
        result = memory_service.memory_read_file("ws-1", "memory/files/never-created.md")

        self.assertFalse(result["exists"])
        self.assertFalse(result["is_default"])
        self.assertEqual(result["content"], "")

    def test_real_empty_topic_file_reports_exists_true(self) -> None:
        workspace_context.write_workspace_context_file(
            "memory/files/curated-empty.md", "", workspace_id="ws-1",
        )

        result = memory_service.memory_read_file("ws-1", "memory/files/curated-empty.md")

        self.assertTrue(result["exists"])
        self.assertFalse(result["is_default"])
        self.assertEqual(result["content"], "")

    def test_missing_and_empty_topic_files_are_indistinguishable_on_is_default_alone(self) -> None:
        """Regression guard for the exact false-negative the report flagged:
        `is_default` is False for BOTH the missing and the real-empty file,
        so a caller reading only `is_default` cannot tell them apart --
        `exists` is what disambiguates them."""
        workspace_context.write_workspace_context_file(
            "memory/files/curated-empty.md", "", workspace_id="ws-1",
        )
        missing = memory_service.memory_read_file("ws-1", "memory/files/never-created.md")
        real_empty = memory_service.memory_read_file("ws-1", "memory/files/curated-empty.md")

        self.assertEqual(missing["is_default"], real_empty["is_default"])
        self.assertNotEqual(missing["exists"], real_empty["exists"])

    def test_topic_file_with_real_content_reports_exists_true(self) -> None:
        workspace_context.write_workspace_context_file(
            "memory/files/customers/acme.md", "# Acme\n\nRenewal in March.\n", workspace_id="ws-1",
        )

        result = memory_service.memory_read_file("ws-1", "memory/files/customers/acme.md")

        self.assertTrue(result["exists"])
        self.assertEqual(result["content"], "# Acme\n\nRenewal in March.\n")

    def test_root_file_always_reports_exists_true(self) -> None:
        """Root files (MEMORY.md, SOUL.md, ...) are auto-seeded with default
        scaffold content on first read -- "does not exist" is never a real
        state for them."""
        result = memory_service.memory_read_file("ws-1", "MEMORY.md")
        self.assertTrue(result["exists"])

    def test_workspace_context_file_exists_helper_matches_memory_read(self) -> None:
        self.assertFalse(
            workspace_context.workspace_context_file_exists(
                "memory/files/never-created.md", workspace_id="ws-1",
            )
        )
        workspace_context.write_workspace_context_file(
            "memory/files/created.md", "content", workspace_id="ws-1",
        )
        self.assertTrue(
            workspace_context.workspace_context_file_exists(
                "memory/files/created.md", workspace_id="ws-1",
            )
        )


if __name__ == "__main__":
    unittest.main()
