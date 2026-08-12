"""MEMORY WAVE (docs/design/context-engineering-plan.md items 6, 7, 8).

Covers the founder's exact decisions:
  (6) Provenance + trust-weighted injection + write filters -- the canonical
      brother-scenario test: the owner's brother messages the agent; nothing
      he says may be saved as owner-grade truth, the saved memory must show
      its origin, and saving it at all requires the agent to state why.
  (7) The memory index hard cap, adopted at Claude Code's exact published
      numbers (200 lines / 25KB, whichever hits first) -- an overflowing
      write must return an explicit error, never silently truncate.
  (8) Decision B: update-don't-duplicate (merge/overwrite), paired with a
      provenance audit trail so every overwrite is forensically
      reconstructable -- the Mem0 ADD-only alternative was NOT adopted.

Also re-confirms (regression, not new ground) that per-agent physical
isolation -- the Part 27 audit's finding -- holds for every new surface
added here (memory_entries_history, attribution_reason, trust_tier).

These tests exercise the same low-level (agent_memory._save_memory,
memory_service.memory_write_file/update_memory_context_file/
memory_append_daily_note) surfaces test_memory_attribution.py already
covers for the pre-existing half of this work; this file is scoped to the
NEW behavior: the write filter, the exact cap numbers, and the audit trail.
"""

from __future__ import annotations

import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

from server_modules import agent_memory


# ── (6) agent_memory: trust tier + write filter, pure-function level ────────


class TestDeriveTrustTier(unittest.TestCase):
    def test_owner_true_is_owner_tier(self) -> None:
        self.assertEqual(
            agent_memory.derive_trust_tier({"sender_is_owner": True, "sender_name": "Mansur"}),
            "owner",
        )

    def test_confirmed_not_owner_is_non_owner_sender_tier(self) -> None:
        self.assertEqual(
            agent_memory.derive_trust_tier(
                {"platform": "whatsapp_personal", "sender_name": "Bilal", "sender_is_owner": False}
            ),
            "non_owner_sender",
        )

    def test_unresolved_ownership_with_real_sender_is_unverified_tier(self) -> None:
        self.assertEqual(
            agent_memory.derive_trust_tier(
                {"platform": "imessage_personal", "sender_name": "Casey", "sender_is_owner": None}
            ),
            "unverified",
        )

    def test_no_source_at_all_is_agent_inferred_tier(self) -> None:
        self.assertEqual(agent_memory.derive_trust_tier(None), "agent_inferred")
        self.assertEqual(agent_memory.derive_trust_tier({}), "agent_inferred")

    def test_requires_attribution_reason_true_only_for_non_owner_and_unverified(self) -> None:
        self.assertFalse(agent_memory.requires_attribution_reason(None))
        self.assertFalse(agent_memory.requires_attribution_reason({"sender_is_owner": True}))
        self.assertTrue(
            agent_memory.requires_attribution_reason({"sender_name": "Bilal", "sender_is_owner": False})
        )
        self.assertTrue(
            agent_memory.requires_attribution_reason({"sender_name": "Casey", "sender_is_owner": None})
        )

    def test_strip_source_marker_removes_only_the_leading_bracket(self) -> None:
        marked = "[Bilal via whatsapp_personal — not owner] dislikes cilantro"
        self.assertEqual(agent_memory.strip_source_marker(marked), "dislikes cilantro")
        # No marker present -- unchanged.
        self.assertEqual(agent_memory.strip_source_marker("plain fact"), "plain fact")


# ── (6) The brother scenario, end to end, on the structured key/value store ─


class TestBrotherScenarioStructuredStore(unittest.TestCase):
    """agent_memory._save_memory / _list_memory_entries -- the sqlite
    memory_entries table behind the memory_search/memory_get tools."""

    def setUp(self) -> None:
        self.m = importlib.reload(agent_memory)
        self._tmpdir = tempfile.TemporaryDirectory(prefix="memory-provenance-")
        self.addCleanup(self._tmpdir.cleanup)
        self._memory_root = Path(self._tmpdir.name) / "runtime-memory"
        for target, value in (("_MEMORY_DIR", self._memory_root),):
            p = patch.object(self.m, target, value)
            p.start()
            self.addCleanup(p.stop)

    def test_brother_fact_without_reason_is_refused_not_saved(self) -> None:
        """The core write filter: a non-owner sender's statement can still be
        saved, but never silently -- omitting attribution_reason must refuse
        the write outright (explicit error), not save it unmarked."""
        with pytest.raises(agent_memory.MemoryAttributionRequiredError):
            self.m._save_memory(
                "ws-1", "brother-claim", "says the rent is due Friday",
                agent_install_id="agent-1", sync_memory_md=False,
                source={"platform": "telegram_personal", "surface": "group",
                        "sender_id": "brother-1", "sender_name": "Karim", "sender_is_owner": False},
            )
        # Nothing was saved -- not partially, not unmarked.
        self.assertEqual(self.m._list_memory_entries("ws-1", agent_install_id="agent-1"), [])

    def test_brother_fact_with_reason_saves_attributed_never_owner_grade(self) -> None:
        self.m._save_memory(
            "ws-1", "brother-claim", "says the rent is due Friday",
            agent_install_id="agent-1", sync_memory_md=False,
            source={"platform": "telegram_personal", "surface": "group",
                    "sender_id": "brother-1", "sender_name": "Karim", "sender_is_owner": False},
            attribution_reason="Karim raised a shared-household deadline; worth surfacing to the owner.",
        )
        [entry] = self.m._list_memory_entries("ws-1", agent_install_id="agent-1")
        # Never owner-grade: trust tier is explicit and not "owner".
        self.assertEqual(entry["trust_tier"], "non_owner_sender")
        self.assertIs(entry["source_is_owner"], False)
        self.assertEqual(entry["attribution_reason"], "Karim raised a shared-household deadline; worth surfacing to the owner.")
        # Visibly attributed whenever it enters context -- never a bare fact.
        marker = self.m.format_source_marker(entry)
        self.assertIn("Karim", marker)
        self.assertIn("not owner", marker)
        rendered = self.m._get_memory("ws-1", agent_install_id="agent-1")
        self.assertIn("Karim", rendered)
        self.assertIn("not owner", rendered)
        self.assertIn("rent is due Friday", rendered)

    def test_owner_fact_needs_no_reason_and_stays_unmarked(self) -> None:
        self.m._save_memory(
            "ws-1", "owner-claim", "wants weekly status updates on Fridays",
            agent_install_id="agent-1", sync_memory_md=False,
            source={"platform": "telegram_personal", "surface": "owner_self_chat",
                    "sender_id": "owner-1", "sender_name": "Mansur", "sender_is_owner": True},
        )
        [entry] = self.m._list_memory_entries("ws-1", agent_install_id="agent-1")
        self.assertEqual(entry["trust_tier"], "owner")
        self.assertEqual(self.m.format_source_marker(entry), "")

    def test_brother_cannot_silently_overwrite_an_owner_fact_as_owner_grade(self) -> None:
        """Update-don't-duplicate (item 8) means a same-key write DOES
        overwrite -- but it must still go through the same write filter, so
        the brother's version can never quietly replace the owner's fact
        without leaving both an attribution marker AND an audit trail."""
        self.m._save_memory(
            "ws-1", "meeting-time", "standup is at 9am",
            agent_install_id="agent-1", sync_memory_md=False,
            source={"sender_name": "Mansur", "sender_is_owner": True},
        )
        with pytest.raises(agent_memory.MemoryAttributionRequiredError):
            self.m._save_memory(
                "ws-1", "meeting-time", "standup is actually at 10am",
                agent_install_id="agent-1", sync_memory_md=False,
                source={"platform": "slack", "sender_name": "Karim", "sender_is_owner": False},
            )
        # The owner's original fact is untouched by the refused write.
        [entry] = self.m._list_memory_entries("ws-1", agent_install_id="agent-1")
        self.assertEqual(entry["content"], "standup is at 9am")
        self.assertEqual(entry["trust_tier"], "owner")


# ── (6) The brother scenario on the file-based MEMORY.md / daily-note path ──


class TestBrotherScenarioFileBased(unittest.TestCase):
    def setUp(self) -> None:
        global memory_service, workspace_context
        memory_service = importlib.import_module("server_modules.memory_service")
        workspace_context = importlib.import_module("server_modules.workspace_context")
        self._tmpdir = tempfile.TemporaryDirectory(prefix="memory-provenance-file-")
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

    def _brother_source(self):
        return {
            "platform": "whatsapp_personal", "surface": "dm",
            "sender_id": "brother-1", "sender_name": "Karim", "sender_is_owner": False,
        }

    def test_memory_write_refuses_brother_fact_without_reason(self) -> None:
        with pytest.raises(ValueError, match="attribution_reason"):
            memory_service.memory_write_file(
                "ws-1", "MEMORY.md", "says the wifi password changed", mode="append",
                reason="memory_write", source=self._brother_source(),
            )
        content = workspace_context.read_workspace_context_file("MEMORY.md", workspace_id="ws-1")
        self.assertNotIn("wifi password", content)

    def test_memory_write_saves_brother_fact_attributed_with_reason(self) -> None:
        memory_service.memory_write_file(
            "ws-1", "MEMORY.md", "says the wifi password changed", mode="append",
            reason="memory_write", source=self._brother_source(),
            attribution_reason="Karim mentioned a household wifi change; owner should know.",
        )
        content = workspace_context.read_workspace_context_file("MEMORY.md", workspace_id="ws-1")
        self.assertIn("Karim", content)
        self.assertIn("not owner", content)
        self.assertIn("wifi password", content)

    def test_daily_note_refuses_brother_note_without_reason(self) -> None:
        with pytest.raises(ValueError, match="attribution_reason"):
            memory_service.memory_append_daily_note(
                "ws-1", "Karim mentioned a new rule that guests must sign in at the front desk.",
                source=self._brother_source(),
            )

    def test_daily_note_attributes_brother_note_and_survives_similarity_check(self) -> None:
        result = memory_service.memory_append_daily_note(
            "ws-1", "Karim mentioned a new rule that guests must sign in at the front desk.",
            source=self._brother_source(),
            attribution_reason="Direct statement about a household policy change.",
        )
        self.assertTrue(result["saved"])
        self.assertIn("Karim", result["appended_entry"])
        self.assertIn("not owner", result["appended_entry"])
        self.assertIn("guests must sign in", result["appended_entry"])

        # A second, owner-sourced note stating a genuinely DIFFERENT fact
        # must not be rejected as a false-positive duplicate just because
        # the first note's marker text ("Karim", "whatsapp", "not owner")
        # would otherwise pollute the similarity comparison.
        second = memory_service.memory_append_daily_note(
            "ws-1", "Decided to renew the office lease for another year.",
            source={"sender_name": "Mansur", "sender_is_owner": True},
        )
        self.assertTrue(second["saved"])

    def test_memory_update_also_requires_reason_for_brother_content(self) -> None:
        with pytest.raises(ValueError, match="attribution_reason"):
            memory_service.update_memory_context_file(
                "ws-1", "GOALS.md", "# Goals\n\n- Karim wants us to fix the shared printer.\n",
                reason="memory_update", source=self._brother_source(),
            )

    def test_owner_paths_unaffected_no_reason_required(self) -> None:
        memory_service.memory_write_file(
            "ws-1", "MEMORY.md", "likes concise updates", mode="append", reason="memory_write",
            source={"sender_name": "Mansur", "sender_is_owner": True},
        )
        memory_service.memory_append_daily_note(
            "ws-1", "Decided to ship the memory provenance work today.",
            source={"sender_name": "Mansur", "sender_is_owner": True},
        )
        # 2026-07-23 root-taxonomy removal: GOALS.md is no longer a root
        # context file -- PROCEDURES.md exercises the same owner-path update.
        memory_service.update_memory_context_file(
            "ws-1", "PROCEDURES.md", "# Procedures\n\n- Ship the memory wave.\n", reason="memory_update",
        )
        content = workspace_context.read_workspace_context_file("MEMORY.md", workspace_id="ws-1")
        self.assertIn("likes concise updates", content)


# ── (7) Exact Claude Code numbers: 200 lines / 25KB, error not truncation ───


class TestIndexCapExactNumbers(unittest.TestCase):
    def setUp(self) -> None:
        global memory_service, workspace_context
        memory_service = importlib.import_module("server_modules.memory_service")
        workspace_context = importlib.import_module("server_modules.workspace_context")
        self._tmpdir = tempfile.TemporaryDirectory(prefix="memory-provenance-cap-")
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

    def test_constants_match_claude_codes_published_numbers(self) -> None:
        self.assertEqual(memory_service.MEMORY_MD_INDEX_MAX_LINES, 200)
        self.assertEqual(memory_service.MEMORY_MD_INDEX_MAX_BYTES, 25_000)
        # Backward-compat alias some existing tests/callers reference by name.
        self.assertEqual(memory_service.MEMORY_MD_SELF_CURATION_CAP_CHARS, 25_000)

    def test_byte_overflow_refused_with_explicit_error_not_truncated(self) -> None:
        oversized = "x" * (memory_service.MEMORY_MD_INDEX_MAX_BYTES + 500)
        with pytest.raises(ValueError, match="self-curation cap"):
            memory_service.memory_write_file(
                "ws-1", "MEMORY.md", oversized, mode="append", reason="memory_write",
            )
        content = workspace_context.read_workspace_context_file("MEMORY.md", workspace_id="ws-1")
        self.assertNotIn("x" * 100, content)

    def test_line_overflow_refused_even_under_byte_cap(self) -> None:
        """Whichever threshold hits first -- 200 short lines is well under
        25KB in bytes but must still be refused."""
        # Seed 200 short lines directly (bypassing the one-fact-per-call
        # guard, matching how a real MEMORY.md accumulates over many turns).
        seeded = "\n".join(f"- fact number {i}" for i in range(200))
        workspace_context.write_workspace_context_file(
            "MEMORY.md", seeded, workspace_id="ws-1",
        )
        with pytest.raises(ValueError, match="self-curation cap"):
            memory_service.memory_write_file(
                "ws-1", "MEMORY.md", "one fact too many", mode="append", reason="memory_write",
            )

    def test_memory_update_cannot_bypass_the_cap_for_memory_md(self) -> None:
        """The gap the audit flagged: memory_update (update_memory_context_file)
        is a whole-file replace and must be subject to the same cap as the
        append path for MEMORY.md -- otherwise it's a silent backdoor."""
        oversized = "# Curated Memory\n\n" + ("x" * (memory_service.MEMORY_MD_INDEX_MAX_BYTES + 500))
        with pytest.raises(ValueError, match="self-curation cap"):
            memory_service.update_memory_context_file(
                "ws-1", "MEMORY.md", oversized, reason="memory_update",
            )

    def test_owner_manual_tree_edit_is_exempt_from_the_cap(self) -> None:
        """reason='memory_tree_write' (the owner's own Memory-tab editor) may
        legitimately paste a large curated rewrite -- unaffected."""
        oversized = "# Curated Memory\n\n" + ("x" * (memory_service.MEMORY_MD_INDEX_MAX_BYTES + 500))
        result = memory_service.update_memory_context_file(
            "ws-1", "MEMORY.md", oversized, reason="memory_tree_write",
        )
        self.assertEqual(result.get("filename"), "MEMORY.md")


# ── (8) Decision B: update-don't-duplicate, paired with an audit trail ──────


class TestUpdateDontDuplicateAuditTrail(unittest.TestCase):
    def setUp(self) -> None:
        self.m = importlib.reload(agent_memory)
        self._tmpdir = tempfile.TemporaryDirectory(prefix="memory-provenance-audit-")
        self.addCleanup(self._tmpdir.cleanup)
        self._memory_root = Path(self._tmpdir.name) / "runtime-memory"
        for target, value in (("_MEMORY_DIR", self._memory_root),):
            p = patch.object(self.m, target, value)
            p.start()
            self.addCleanup(p.stop)

        # test_audit_trail_reconstructs_what_changed_and_why goes through
        # memory_service.save_memory (not agent_memory._save_memory
        # directly), which routes through _enforce_memory_state_decision --
        # same allow-everything rust-kernel mock as test_memory_attribution.py.
        from server_modules import rust_runtime_kernel_client as rk

        def _run_enforced(_name, payload):
            op = str((payload or {}).get("operation") or "")
            return {"decision": "allow", "next_action": {"upsert_workspace_memory": "write_workspace_memory"}.get(op, op)}

        rk_patchers = [
            patch.object(rk, "run_runtime_kernel_enforced", _run_enforced),
            patch.object(rk, "runtime_state_store_decision", lambda *, operation, **_kw: {"decision": "allow", "next_action": operation}),
            patch.object(rk, "enforce_kernel_decision", lambda *_a, **_k: None),
        ]
        for p in rk_patchers:
            p.start()
            self.addCleanup(p.stop)

    def test_contradicting_writes_overwrite_not_duplicate(self) -> None:
        """The founder's chosen stance (Claude Code's, not Mem0's ADD-only):
        one row per key, always -- never two ranked/contradicting rows."""
        self.m._save_memory("ws-1", "office-day", "Tuesdays", agent_install_id="agent-1", sync_memory_md=False)
        self.m._save_memory("ws-1", "office-day", "Wednesdays", agent_install_id="agent-1", sync_memory_md=False)
        entries = self.m._list_memory_entries("ws-1", agent_install_id="agent-1")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["content"], "Wednesdays")

    def test_audit_trail_reconstructs_what_changed_and_why(self) -> None:
        from server_modules import memory_service

        memory_service.save_memory(
            "ws-1", "office-day", "Tuesdays", agent_install_id="agent-1",
            sync_memory_md=False,
        )
        memory_service.save_memory(
            "ws-1", "office-day", "Wednesdays", agent_install_id="agent-1",
            sync_memory_md=False,
            source={"platform": "slack", "sender_name": "Karim", "sender_is_owner": False},
            attribution_reason="Karim corrected the office day in #ops.",
        )
        history = memory_service.list_memory_entry_history("ws-1", "office-day", agent_install_id="agent-1")
        # Newest first.
        self.assertEqual(len(history), 2)
        latest, original = history[0], history[1]

        self.assertEqual(original["change_kind"], "created")
        self.assertIsNone(original["old_content"])
        self.assertEqual(original["new_content"], "Tuesdays")

        self.assertEqual(latest["change_kind"], "updated")
        self.assertEqual(latest["old_content"], "Tuesdays")
        self.assertEqual(latest["new_content"], "Wednesdays")
        self.assertEqual(latest["source_sender_name"], "Karim")
        self.assertIs(latest["source_is_owner"], False)
        self.assertEqual(latest["attribution_reason"], "Karim corrected the office day in #ops.")

        # The live row only ever holds the latest content -- the audit
        # trail, not the current-state row, is what makes history visible.
        [entry] = memory_service.list_memory_entries("ws-1", agent_install_id="agent-1")
        self.assertEqual(entry["content"], "Wednesdays")

    def test_resave_with_identical_content_does_not_pad_history(self) -> None:
        """A no-op re-save (identical content) is not a 'change' -- only
        genuine content transitions get an audit-trail row."""
        self.m._save_memory("ws-1", "k", "same value", agent_install_id="agent-1", sync_memory_md=False)
        self.m._save_memory("ws-1", "k", "same value", agent_install_id="agent-1", sync_memory_md=False)
        history = self.m._list_memory_entry_history("ws-1", "k", agent_install_id="agent-1")
        self.assertEqual(len(history), 1)


# ── Regression: per-agent isolation preserved on every new surface ──────────


class TestPerAgentIsolationPreserved(unittest.TestCase):
    """Part 27's finding (PLATFORM-MAP.md) -- physical per-(workspace,
    agent_install_id) .db file separation -- must hold for the new
    memory_entries_history table and the new attribution_reason column
    exactly as it already does for content/source_*."""

    def setUp(self) -> None:
        self.m = importlib.reload(agent_memory)
        self._tmpdir = tempfile.TemporaryDirectory(prefix="memory-provenance-isolation-")
        self.addCleanup(self._tmpdir.cleanup)
        self._memory_root = Path(self._tmpdir.name) / "runtime-memory"
        for target, value in (("_MEMORY_DIR", self._memory_root),):
            p = patch.object(self.m, target, value)
            p.start()
            self.addCleanup(p.stop)

    def test_history_never_crosses_agent_installs(self) -> None:
        self.m._save_memory(
            "ws-1", "shared-key-name", "Agent A's value", agent_install_id="agent-a", sync_memory_md=False,
        )
        self.m._save_memory(
            "ws-1", "shared-key-name", "Agent A's second value", agent_install_id="agent-a", sync_memory_md=False,
        )
        self.m._save_memory(
            "ws-1", "shared-key-name", "Agent B's value", agent_install_id="agent-b", sync_memory_md=False,
        )

        history_a = self.m._list_memory_entry_history("ws-1", "shared-key-name", agent_install_id="agent-a")
        history_b = self.m._list_memory_entry_history("ws-1", "shared-key-name", agent_install_id="agent-b")

        self.assertEqual(len(history_a), 2)
        self.assertEqual(len(history_b), 1)
        self.assertTrue(all("Agent A" in item["new_content"] for item in history_a))
        self.assertTrue(all("Agent B" in item["new_content"] for item in history_b))

    def test_brother_sourced_fact_for_one_agent_never_leaks_to_another(self) -> None:
        self.m._save_memory(
            "ws-1", "note", "the launch codeword is nightingale", agent_install_id="agent-a", sync_memory_md=False,
            source={"platform": "slack", "sender_name": "Karim", "sender_is_owner": False},
            attribution_reason="Karim shared this in a planning channel.",
        )
        entries_b = self.m._list_memory_entries("ws-1", agent_install_id="agent-b")
        self.assertEqual(entries_b, [])


if __name__ == "__main__":
    unittest.main()
