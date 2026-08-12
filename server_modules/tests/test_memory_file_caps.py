"""Memory topic-file caps + auto-maintained index (founder ruling, 2026-07-23).

Builds on the committed memory wave (b06402bf1: MEMORY.md's own
200-line/25KB index cap + provenance/attribution, both untouched here --
see test_memory_provenance.py / test_memory_attribution.py) with the next
layer of the architecture, all enforced at the write_workspace_context_file
chokepoint every topic-file write funnels through:

  (1) Per-file caps: every memory topic file (memory/files/**.md) is capped
      at 200 lines / 25KB at write time -- same numbers as MEMORY.md's own
      cap -- with an explicit reject-with-error telling the agent to
      shorten/split/consolidate. Never a silent truncation.
  (2) Placement-aware count caps: MEMORY_TOPIC_FILE_MAX_COUNT = 40
      (hardware-backed agents, the generous default every caller gets
      today) and MEMORY_TOPIC_FILE_MAX_COUNT_CLOUD_ONLY = 20 (cloud-only
      agents), selected via an explicit `topic_file_max_count` override
      parameter on write_workspace_context_file -- placement resolution
      is not yet wired into the chokepoint itself (see that file's TODO).
      At the cap, creating a NEW topic file is rejected; updating any
      existing file is always allowed, cap or no cap.
  (3) Auto-maintained index: memory_write_file / update_memory_context_file
      to a memory/files/**.md topic file REQUIRE a `description` (unless
      reason=="memory_tree_write", the owner's own manual Memory-tab
      editor, where it's optional) and auto-upsert that file's one-line
      index entry into MEMORY.md in the SAME call. Deleting a topic file
      removes its line the same way. The index can never list a file that
      doesn't exist on disk. Index-cap overflow on the upsert fails the
      WHOLE write (topic file included) -- never a partial save.
  (4) Every rejection above is a raised ValueError the caller must handle
      -- no approval/pending state, no silent partial success anywhere in
      the chain.

Also proves, as a regression (not new ground): a pre-existing bug in
workspace_context._count_existing_user_memory_files that undercounted
category-nested topic files (memory/files/customers/widgetco.md) -- silently
never counting them toward the cap at all -- is fixed, so (2) actually
holds for categorized topic files, not just flat ones.
"""

from __future__ import annotations

import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest


class _MemoryFileCapsTestBase(unittest.TestCase):
    """Shared kernel-mock + tmp-workspace scaffolding, ported from
    test_memory_provenance.py's identical setUp (see that file for the
    per-operation next_action citations)."""

    def setUp(self) -> None:
        global memory_service, workspace_context, agent_memory_tree_service
        memory_service = importlib.import_module("server_modules.memory_service")
        workspace_context = importlib.import_module("server_modules.workspace_context")
        agent_memory_tree_service = importlib.import_module("server_modules.agent_memory_tree_service")
        self._tmpdir = tempfile.TemporaryDirectory(prefix="memory-file-caps-")
        self.addCleanup(self._tmpdir.cleanup)
        tmp_root = Path(self._tmpdir.name)
        self._workspace_root = tmp_root / "workspace"
        self._workspace_root.mkdir(parents=True, exist_ok=True)
        self._memory_root = tmp_root / "runtime-memory"

        from server_modules import rust_runtime_kernel_client as rk

        mem_map = {
            "upsert_workspace_memory": "write_workspace_memory",
            "delete_workspace_memory": "delete_workspace_memory",
            "append_workspace_daily_log": "append_workspace_daily_log",
            "update_workspace_context_file": "write_workspace_context_file",
        }

        def _run_enforced(_name, payload):
            op = str((payload or {}).get("operation") or "")
            return {"decision": "allow", "next_action": mem_map.get(op, op)}

        def _state_decision(*, operation, **_kw):
            return {"decision": "allow", "next_action": operation}

        patchers = [
            patch.object(workspace_context, "_WORKSPACE_DIR", self._workspace_root),
            patch.object(memory_service._workspace_memory_store, "_MEMORY_DIR", self._memory_root),
            patch.object(rk, "run_runtime_kernel_enforced", _run_enforced),
            patch.object(rk, "runtime_state_store_decision", _state_decision),
            patch.object(rk, "enforce_kernel_decision", lambda *_a, **_k: None),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    def _memory_md(self, workspace_id: str = "ws-1") -> str:
        return workspace_context.read_workspace_context_file("MEMORY.md", workspace_id=workspace_id)

    def _topic_file(self, path: str, workspace_id: str = "ws-1") -> str:
        return workspace_context.read_workspace_context_file(f"memory/files/{path}", workspace_id=workspace_id)


# ── (1) Per-file cap: 200 lines / 25KB, whichever hits first ────────────────


class TestTopicFilePerFileCap(_MemoryFileCapsTestBase):
    def test_constants_are_200_lines_25kb(self) -> None:
        self.assertEqual(workspace_context.MEMORY_TOPIC_FILE_MAX_LINES, 200)
        self.assertEqual(workspace_context.MEMORY_TOPIC_FILE_MAX_BYTES, 25_000)

    def test_byte_overflow_rejected_at_the_chokepoint(self) -> None:
        oversized = "x" * (workspace_context.MEMORY_TOPIC_FILE_MAX_BYTES + 500)
        with pytest.raises(ValueError, match="cap"):
            workspace_context.write_workspace_context_file(
                "memory/files/big.md", oversized, workspace_id="ws-1",
            )
        self.assertEqual(self._topic_file("big.md"), "")

    def test_line_overflow_rejected_even_under_byte_cap(self) -> None:
        too_many_lines = "\n".join(
            f"- line {i}" for i in range(workspace_context.MEMORY_TOPIC_FILE_MAX_LINES + 1)
        )
        with pytest.raises(ValueError, match="cap"):
            workspace_context.write_workspace_context_file(
                "memory/files/lines.md", too_many_lines, workspace_id="ws-1",
            )
        self.assertEqual(self._topic_file("lines.md"), "")

    def test_within_cap_saves_exact_content_no_truncation(self) -> None:
        content = "\n".join(f"- fact {i}" for i in range(50))
        workspace_context.write_workspace_context_file(
            "memory/files/ok.md", content, workspace_id="ws-1",
        )
        self.assertEqual(self._topic_file("ok.md"), content)

    def test_memory_write_file_enforces_the_same_cap(self) -> None:
        oversized = "x" * (workspace_context.MEMORY_TOPIC_FILE_MAX_BYTES + 500)
        with pytest.raises(ValueError, match="cap"):
            memory_service.memory_write_file(
                "ws-1", "memory/files/big.md", oversized, mode="replace",
                reason="memory_write", description="A file that is too big.",
            )
        # Neither the topic file nor the index picked up any trace of it --
        # the description pre-check runs before the write, so the rejected
        # write leaves no side effect anywhere.
        self.assertEqual(self._topic_file("big.md"), "")
        self.assertNotIn("big.md", self._memory_md())

    def test_update_memory_context_file_enforces_the_same_cap(self) -> None:
        oversized = "x" * (workspace_context.MEMORY_TOPIC_FILE_MAX_BYTES + 500)
        with pytest.raises(ValueError, match="cap"):
            memory_service.update_memory_context_file(
                "ws-1", "memory/files/big.md", oversized,
                reason="memory_update", description="A file that is too big.",
            )
        self.assertEqual(self._topic_file("big.md"), "")

    def test_error_tells_the_agent_to_shorten_split_or_consolidate(self) -> None:
        oversized = "x" * (workspace_context.MEMORY_TOPIC_FILE_MAX_BYTES + 500)
        with pytest.raises(ValueError) as excinfo:
            workspace_context.write_workspace_context_file(
                "memory/files/big.md", oversized, workspace_id="ws-1",
            )
        message = str(excinfo.value).lower()
        self.assertIn("shorten", message)
        self.assertIn("split", message)
        self.assertIn("consolidate", message)
        self.assertIn("not saved", message)


# ── (2) Placement-aware count cap ────────────────────────────────────────────


class TestTopicFileCountCap(_MemoryFileCapsTestBase):
    def test_constants_exist_hardware_and_cloud_only(self) -> None:
        self.assertEqual(workspace_context.MEMORY_TOPIC_FILE_MAX_COUNT, 40)
        self.assertEqual(workspace_context.MEMORY_TOPIC_FILE_MAX_COUNT_CLOUD_ONLY, 20)
        # Legacy alias (predates this ruling, was a fixed 20) stays in sync
        # with the current, tunable hardware-backed default.
        self.assertEqual(
            workspace_context.MAX_CONTEXT_USER_MEMORY_FILES,
            workspace_context.MEMORY_TOPIC_FILE_MAX_COUNT,
        )

    def test_creating_new_file_at_cap_is_rejected(self) -> None:
        cap = workspace_context.MEMORY_TOPIC_FILE_MAX_COUNT
        for i in range(cap):
            workspace_context.write_workspace_context_file(
                f"memory/files/file_{i}.md", "# note\n", workspace_id="ws-1",
            )
        with pytest.raises(ValueError, match="consolidate"):
            workspace_context.write_workspace_context_file(
                "memory/files/overflow.md", "# note\n", workspace_id="ws-1",
            )
        self.assertEqual(self._topic_file("overflow.md"), "")

    def test_updating_an_existing_file_at_cap_is_always_allowed(self) -> None:
        cap = workspace_context.MEMORY_TOPIC_FILE_MAX_COUNT
        for i in range(cap):
            workspace_context.write_workspace_context_file(
                f"memory/files/file_{i}.md", "# note\n", workspace_id="ws-1",
            )
        # Updating file_0 (already existing) must not trip the count cap,
        # even though the workspace is sitting exactly at the cap.
        workspace_context.write_workspace_context_file(
            "memory/files/file_0.md", "# updated note\n", workspace_id="ws-1",
        )
        self.assertEqual(self._topic_file("file_0.md"), "# updated note\n")

    def test_cloud_only_override_caps_at_the_configured_limit(self) -> None:
        cloud_cap = workspace_context.MEMORY_TOPIC_FILE_MAX_COUNT_CLOUD_ONLY
        for i in range(cloud_cap):
            workspace_context.write_workspace_context_file(
                f"memory/files/cloud_{i}.md", "# note\n", workspace_id="ws-1",
                topic_file_max_count=cloud_cap,
            )
        with pytest.raises(ValueError, match="consolidate"):
            workspace_context.write_workspace_context_file(
                "memory/files/cloud_overflow.md", "# note\n", workspace_id="ws-1",
                topic_file_max_count=cloud_cap,
            )

    def test_cloud_only_override_does_not_block_updates_at_cap(self) -> None:
        cloud_cap = workspace_context.MEMORY_TOPIC_FILE_MAX_COUNT_CLOUD_ONLY
        for i in range(cloud_cap):
            workspace_context.write_workspace_context_file(
                f"memory/files/cloud_{i}.md", "# note\n", workspace_id="ws-1",
                topic_file_max_count=cloud_cap,
            )
        workspace_context.write_workspace_context_file(
            "memory/files/cloud_0.md", "# updated\n", workspace_id="ws-1",
            topic_file_max_count=cloud_cap,
        )
        self.assertEqual(self._topic_file("cloud_0.md"), "# updated\n")

    def test_default_omitted_override_uses_hardware_backed_cap_not_cloud(self) -> None:
        """A caller that does not know the calling agent's placement (every
        current caller, per workspace_context.py's TODO) must default to
        the generous hardware-backed cap -- never silently over-cap an
        agent that just hasn't been wired up to placement resolution yet."""
        cloud_cap = workspace_context.MEMORY_TOPIC_FILE_MAX_COUNT_CLOUD_ONLY
        for i in range(cloud_cap + 1):
            # No override passed -- must succeed past the cloud-only cap.
            workspace_context.write_workspace_context_file(
                f"memory/files/many_{i}.md", "# note\n", workspace_id="ws-1",
            )
        for i in range(cloud_cap + 1):
            self.assertNotEqual(self._topic_file(f"many_{i}.md"), "")

    def test_categorized_subdirectory_files_count_toward_the_cap(self) -> None:
        """Regression: workspace_context._count_existing_user_memory_files
        used to only scan the top level of memory/files/, silently never
        counting anything filed under a one-level category subdirectory
        (memory/files/customers/widgetco.md) -- letting an agent create
        unlimited categorized topic files past the cap. Fixed to walk both
        flat and one-level-deep category files."""
        cloud_cap = workspace_context.MEMORY_TOPIC_FILE_MAX_COUNT_CLOUD_ONLY
        for i in range(cloud_cap):
            workspace_context.write_workspace_context_file(
                f"memory/files/customers/cust_{i}.md", "# note\n", workspace_id="ws-1",
                topic_file_max_count=cloud_cap,
            )
        with pytest.raises(ValueError, match="consolidate"):
            workspace_context.write_workspace_context_file(
                "memory/files/customers/overflow.md", "# note\n", workspace_id="ws-1",
                topic_file_max_count=cloud_cap,
            )
        # A mix of flat + categorized files also counts correctly together.
        with pytest.raises(ValueError, match="consolidate"):
            workspace_context.write_workspace_context_file(
                "memory/files/flat_overflow.md", "# note\n", workspace_id="ws-1",
                topic_file_max_count=cloud_cap,
            )


# ── (3) Auto-maintained MEMORY.md index for topic files ─────────────────────


class TestTopicFileIndexAutoMaintenance(_MemoryFileCapsTestBase):
    def test_memory_write_file_requires_description_for_topic_file(self) -> None:
        with pytest.raises(ValueError, match="description"):
            memory_service.memory_write_file(
                "ws-1", "memory/files/widgetco.md", "Acme notes.", mode="replace",
                reason="memory_write",
            )
        self.assertEqual(self._topic_file("widgetco.md"), "")
        self.assertNotIn("widgetco.md", self._memory_md())

    def test_memory_write_file_with_description_creates_file_and_index_line(self) -> None:
        memory_service.memory_write_file(
            "ws-1", "memory/files/widgetco.md", "Acme notes.", mode="replace",
            reason="memory_write", description="Acme account: contract terms and contacts.",
        )
        self.assertEqual(self._topic_file("widgetco.md"), "Acme notes.")
        index = self._memory_md()
        self.assertIn("widgetco.md", index)
        self.assertIn("Acme account: contract terms and contacts.", index)

    def test_index_entry_uses_friendly_path_not_full_memory_files_prefix(self) -> None:
        memory_service.memory_write_file(
            "ws-1", "memory/files/customers/widgetco.md", "Acme notes.", mode="replace",
            reason="memory_write", description="Acme account details.",
        )
        index = self._memory_md()
        self.assertIn("customers/widgetco.md — Acme account details.", index)
        self.assertNotIn("memory/files/customers/widgetco.md", index)

    def test_second_write_refreshes_the_same_line_instead_of_duplicating(self) -> None:
        memory_service.memory_write_file(
            "ws-1", "memory/files/widgetco.md", "v1", mode="replace",
            reason="memory_write", description="First description.",
        )
        memory_service.memory_write_file(
            "ws-1", "memory/files/widgetco.md", "v2", mode="replace",
            reason="memory_write", description="Second, refreshed description.",
        )
        index = self._memory_md()
        self.assertEqual(index.count("widgetco.md"), 1)
        self.assertIn("Second, refreshed description.", index)
        self.assertNotIn("First description.", index)

    def test_update_memory_context_file_requires_description_for_topic_file(self) -> None:
        with pytest.raises(ValueError, match="description"):
            memory_service.update_memory_context_file(
                "ws-1", "memory/files/widgetco.md", "Acme notes.", reason="memory_update",
            )
        self.assertEqual(self._topic_file("widgetco.md"), "")

    def test_update_memory_context_file_with_description_upserts_index(self) -> None:
        memory_service.update_memory_context_file(
            "ws-1", "memory/files/widgetco.md", "Acme notes.", reason="memory_update",
            description="Acme account.",
        )
        self.assertIn("widgetco.md", self._memory_md())

    def test_memory_tree_write_reason_makes_description_optional(self) -> None:
        """The owner's own manual Memory-tab editor may write a topic file
        without a description -- and the index is simply left as-is (no
        entry created) when none is supplied."""
        memory_service.memory_write_file(
            "ws-1", "memory/files/manual.md", "Manually written.", mode="replace",
            reason="memory_tree_write",
        )
        self.assertEqual(self._topic_file("manual.md"), "Manually written.")
        self.assertNotIn("manual.md", self._memory_md())

    def test_memory_tree_write_still_upserts_index_when_description_given(self) -> None:
        memory_service.memory_write_file(
            "ws-1", "memory/files/manual.md", "Manually written.", mode="replace",
            reason="memory_tree_write", description="Manually curated notes.",
        )
        self.assertIn("Manually curated notes.", self._memory_md())

    def test_agent_memory_tree_write_file_facade_does_not_bypass_the_index(self) -> None:
        """agent_memory_tree_service.write_file (the owner's Memory tab) is a
        thin facade over memory_service.memory_write_file with
        reason='memory_tree_write' and no description passed through --
        confirm it lands exactly like the direct call above (file saved,
        no index entry since no description was given)."""
        agent_memory_tree_service.write_file(
            "ws-1", "customers/widgetco.md", "Acme via tree service.",
        )
        self.assertEqual(self._topic_file("customers/widgetco.md"), "Acme via tree service.")
        self.assertNotIn("widgetco.md", self._memory_md())

    def test_delete_topic_file_removes_its_index_line(self) -> None:
        memory_service.memory_write_file(
            "ws-1", "memory/files/widgetco.md", "Acme notes.", mode="replace",
            reason="memory_write", description="Acme account.",
        )
        self.assertIn("widgetco.md", self._memory_md())
        deleted = memory_service.memory_delete_topic_file("ws-1", "memory/files/widgetco.md")
        self.assertTrue(deleted)
        self.assertEqual(self._topic_file("widgetco.md"), "")
        self.assertNotIn("widgetco.md", self._memory_md())

    def test_delete_via_agent_memory_tree_service_also_removes_index_line(self) -> None:
        memory_service.memory_write_file(
            "ws-1", "memory/files/widgetco.md", "Acme notes.", mode="replace",
            reason="memory_write", description="Acme account.",
        )
        deleted = agent_memory_tree_service.delete_file("ws-1", "widgetco.md")
        self.assertTrue(deleted)
        self.assertNotIn("widgetco.md", self._memory_md())

    def test_deleting_a_file_never_indexed_is_a_harmless_noop_on_the_index(self) -> None:
        memory_service.memory_write_file(
            "ws-1", "memory/files/manual.md", "Manually written.", mode="replace",
            reason="memory_tree_write",  # no description -- never indexed
        )
        deleted = memory_service.memory_delete_topic_file("ws-1", "memory/files/manual.md")
        self.assertTrue(deleted)
        self.assertNotIn("manual.md", self._memory_md())

    def test_index_never_lists_a_file_that_was_never_created(self) -> None:
        """A rejected write (missing description) must never leave a
        dangling index entry pointing at a file that doesn't exist."""
        with pytest.raises(ValueError):
            memory_service.memory_write_file(
                "ws-1", "memory/files/ghost.md", "content", mode="replace",
                reason="memory_write",
            )
        self.assertNotIn("ghost.md", self._memory_md())

    def test_index_cap_overflow_on_upsert_fails_the_whole_write(self) -> None:
        """Filling MEMORY.md's index right up to its own cap, then writing
        one more topic file whose index line would push it over: the write
        must fail atomically -- neither the topic file nor the index change."""
        # Pad MEMORY.md close to its own 200-line cap first (bypassing
        # memory_service's own guard on purpose, the same technique
        # test_memory_provenance.py's TestIndexCapExactNumbers uses, to set
        # up the overflow precondition directly on disk).
        padding = "\n".join(f"- padding fact {i}" for i in range(198))
        workspace_context.write_workspace_context_file(
            "MEMORY.md", padding, workspace_id="ws-1",
        )
        before = self._memory_md()
        with pytest.raises(ValueError, match="cap"):
            memory_service.memory_write_file(
                "ws-1", "memory/files/pusher.md", "pusher content", mode="replace",
                reason="memory_write",
                description="A file whose index line pushes MEMORY.md over its cap.",
            )
        # Neither side effect landed -- MEMORY.md is byte-for-byte
        # unchanged and the topic file was never written.
        self.assertEqual(self._topic_file("pusher.md"), "")
        self.assertEqual(self._memory_md(), before)
        self.assertNotIn("pusher.md", self._memory_md())


# ── (4) Every rejection is a plain error return -- no approval/pending state ─


class TestRejectionsAreExplicitErrorsNotSilentOrPending(_MemoryFileCapsTestBase):
    def test_missing_description_raises_plain_value_error(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            memory_service.memory_write_file(
                "ws-1", "memory/files/x.md", "content", mode="replace", reason="memory_write",
            )
        self.assertIs(type(ctx.exception), ValueError)

    def test_per_file_overflow_raises_plain_value_error(self) -> None:
        oversized = "x" * (workspace_context.MEMORY_TOPIC_FILE_MAX_BYTES + 1)
        with self.assertRaises(ValueError) as ctx:
            workspace_context.write_workspace_context_file(
                "memory/files/x.md", oversized, workspace_id="ws-1",
            )
        self.assertIs(type(ctx.exception), ValueError)

    def test_count_cap_overflow_raises_plain_value_error(self) -> None:
        cap = workspace_context.MEMORY_TOPIC_FILE_MAX_COUNT
        for i in range(cap):
            workspace_context.write_workspace_context_file(
                f"memory/files/f_{i}.md", "x", workspace_id="ws-1",
            )
        with self.assertRaises(ValueError) as ctx:
            workspace_context.write_workspace_context_file(
                "memory/files/overflow.md", "x", workspace_id="ws-1",
            )
        self.assertIs(type(ctx.exception), ValueError)

    def test_index_cap_overflow_raises_plain_value_error(self) -> None:
        padding = "\n".join(f"- padding fact {i}" for i in range(198))
        workspace_context.write_workspace_context_file(
            "MEMORY.md", padding, workspace_id="ws-1",
        )
        with self.assertRaises(ValueError) as ctx:
            memory_service.memory_write_file(
                "ws-1", "memory/files/pusher.md", "content", mode="replace",
                reason="memory_write", description="Pushes the index over its cap.",
            )
        self.assertIs(type(ctx.exception), ValueError)

    def test_none_of_the_rejections_silently_return_instead_of_raising(self) -> None:
        """These are hard failures the caller must catch and react to --
        never a soft 'ok: False' / 'status: pending' return value that a
        careless caller could ignore."""
        scenarios = (
            lambda: memory_service.memory_write_file(
                "ws-1", "memory/files/y.md", "content", mode="replace", reason="memory_write",
            ),
            lambda: workspace_context.write_workspace_context_file(
                "memory/files/y.md", "x" * (workspace_context.MEMORY_TOPIC_FILE_MAX_BYTES + 1),
                workspace_id="ws-1",
            ),
        )
        for call in scenarios:
            with self.assertRaises(ValueError):
                result = call()
                self.fail(f"expected ValueError, got a return value instead: {result!r}")


# ── Regression: provenance/attribution still gates topic-file writes ────────


class TestProvenanceStillEnforcedForTopicFiles(_MemoryFileCapsTestBase):
    """The write-filter half of the provenance wave (b06402bf1) and the new
    description/index requirement are independent guards that must compose
    -- satisfying one must never exempt a topic-file write from the other."""

    def _brother_source(self):
        return {
            "platform": "whatsapp_personal", "surface": "dm",
            "sender_id": "brother-1", "sender_name": "Karim", "sender_is_owner": False,
        }

    def test_non_owner_topic_file_write_still_requires_attribution_reason(self) -> None:
        with pytest.raises(ValueError, match="attribution_reason"):
            memory_service.memory_write_file(
                "ws-1", "memory/files/widgetco.md", "Karim says the deal fell through.",
                mode="replace", reason="memory_write",
                source=self._brother_source(), description="Acme deal status.",
            )
        self.assertEqual(self._topic_file("widgetco.md"), "")
        self.assertNotIn("widgetco.md", self._memory_md())

    def test_attribution_reason_present_but_description_missing_still_rejected(self) -> None:
        with pytest.raises(ValueError, match="description"):
            memory_service.memory_write_file(
                "ws-1", "memory/files/widgetco.md", "Karim says the deal fell through.",
                mode="replace", reason="memory_write",
                source=self._brother_source(),
                attribution_reason="Karim has direct knowledge of the Acme deal.",
            )
        self.assertEqual(self._topic_file("widgetco.md"), "")

    def test_non_owner_topic_file_write_with_both_satisfied_succeeds(self) -> None:
        memory_service.memory_write_file(
            "ws-1", "memory/files/widgetco.md", "Karim says the deal fell through.",
            mode="replace", reason="memory_write",
            source=self._brother_source(), description="Acme deal status.",
            attribution_reason="Karim has direct knowledge of the Acme deal.",
        )
        self.assertEqual(self._topic_file("widgetco.md"), "Karim says the deal fell through.")
        self.assertIn("Acme deal status.", self._memory_md())

    def test_owner_topic_file_write_needs_no_attribution_reason_only_description(self) -> None:
        memory_service.memory_write_file(
            "ws-1", "memory/files/widgetco.md", "Owner-stated Acme facts.",
            mode="replace", reason="memory_write",
            source={"sender_name": "Mansur", "sender_is_owner": True},
            description="Acme account.",
        )
        self.assertEqual(self._topic_file("widgetco.md"), "Owner-stated Acme facts.")


if __name__ == "__main__":
    unittest.main()
