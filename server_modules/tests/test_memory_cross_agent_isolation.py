"""Security audit deliverable: prove agent A can never read, write, or list
agent B's memory — across every retrieval surface a turn can reach.

Three attack vectors, matching how memory is actually built:
  (a) the memory_read/write/list tools (agent_memory_tools.py) — direct
      per-install file access, defended by path-traversal rejection.
  (b) path traversal specifically — '..', absolute paths, symlink escapes.
  (c) the semantic/notebook retrieval layer (memory_search, memory_get,
      dispatched through skills_service.py) and the SQLite memory_entries
      layer (agent_memory.py) — both keyed by agent_install_id, which must
      come from trusted server-side session context, never from a model's
      own tool-call arguments.

Confirmed real, fixed 2026-07-14: memory_search/memory_get were the only
two of ~12 memory tool actions in skills_service.py's dispatcher that did
NOT thread agent_install_id through from session_metadata — meaning any
specialist agent's memory_search/memory_get calls silently fell back to
the WORKSPACE ROOT (Sage's own memory notebook) instead of that
specialist's own directory. See agent_workspace_context_dir's fallback
(workspace_context.py) and docs/PLATFORM-MAP.md's memory security audit.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server_modules import agent_memory, agent_memory_tools, memory_service, skills_service
from server_modules import direct_tool_execution_service, workspace_context


class _IsolatedMemoryTestCase(unittest.TestCase):
    """Every test runs against fresh tmp dirs for BOTH memory backends —
    never the real .orion-stack/ tree, and never shared between tests."""

    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        root = Path(self._tempdir.name)
        self._patches = [
            patch("server_modules.workspace_context._WORKSPACE_DIR", root / "workspace"),
            patch("server_modules.agent_memory._MEMORY_DIR", root / "memory"),
        ]
        for p in self._patches:
            p.start()
        self.addCleanup(self._tempdir.cleanup)
        for p in self._patches:
            self.addCleanup(p.stop)


# ── (a) + (b): agent_memory_tools.py — direct file access + path traversal ──


class MemoryToolPathTraversalTests(_IsolatedMemoryTestCase):
    def test_dotdot_traversal_is_rejected(self) -> None:
        result = asyncio.run(agent_memory_tools.memory_read(
            workspace_id="ws-1", agent_install_id="agent-a", path="../agent-b/memory/SOUL.md",
        ))
        self.assertFalse(result["ok"])
        self.assertIn("traversal", result["error"])

    def test_nested_dotdot_traversal_is_rejected(self) -> None:
        result = asyncio.run(agent_memory_tools.memory_write(
            workspace_id="ws-1", agent_install_id="agent-a",
            path="sub/../../agent-b/leak.md", content="leaked",
        ))
        self.assertFalse(result["ok"])
        self.assertIn("traversal", result["error"])

    def test_absolute_path_is_normalized_into_the_agents_own_dir_not_escaped(self) -> None:
        """A leading '/' is stripped before the path is resolved, so this
        never reaches the real filesystem root — it's safely reinterpreted
        as relative to the agent's own memory directory (hence "not
        found", never real /etc/passwd content). The property that
        actually matters — no escape onto the real filesystem — is what
        this test verifies; it does not assume any specific error string."""
        result = asyncio.run(agent_memory_tools.memory_read(
            workspace_id="ws-1", agent_install_id="agent-a", path="/etc/passwd",
        ))
        self.assertFalse(result["ok"])
        self.assertNotIn("root:", result.get("content", ""), "must never read the real /etc/passwd")

    def test_tilde_path_is_rejected(self) -> None:
        result = asyncio.run(agent_memory_tools.memory_write(
            workspace_id="ws-1", agent_install_id="agent-a", path="~/secrets.md", content="x",
        ))
        self.assertFalse(result["ok"])

    def test_symlink_escape_to_another_agents_directory_is_rejected(self) -> None:
        """A symlink planted inside agent A's own directory that POINTS at
        agent B's directory must not let a read escape through it —
        Path.resolve() follows the symlink, and the post-resolve
        startswith() check must still catch it."""
        dir_a = agent_memory_tools._agent_memory_dir(workspace_id="ws-1", agent_install_id="agent-a")
        dir_b = agent_memory_tools._agent_memory_dir(workspace_id="ws-1", agent_install_id="agent-b")
        (dir_b / "SOUL.md").write_text("Agent B's real private persona notes.", encoding="utf-8")
        (dir_a / "escape_link").symlink_to(dir_b)

        result = asyncio.run(agent_memory_tools.memory_read(
            workspace_id="ws-1", agent_install_id="agent-a", path="escape_link/SOUL.md",
        ))
        self.assertFalse(result["ok"], "a symlink inside A's own dir must not be a valid escape to B's dir")

    def test_two_installs_resolve_to_genuinely_different_directories(self) -> None:
        dir_a = agent_memory_tools._agent_memory_dir(workspace_id="ws-1", agent_install_id="agent-a")
        dir_b = agent_memory_tools._agent_memory_dir(workspace_id="ws-1", agent_install_id="agent-b")
        self.assertNotEqual(dir_a, dir_b)
        self.assertFalse(str(dir_b).startswith(str(dir_a)))
        self.assertFalse(str(dir_a).startswith(str(dir_b)))

    def test_agent_as_write_is_invisible_to_agent_bs_list(self) -> None:
        asyncio.run(agent_memory_tools.memory_write(
            workspace_id="ws-1", agent_install_id="agent-a",
            path="secret.md", content="Agent A's private business plan.",
        ))
        listing_b = asyncio.run(agent_memory_tools.memory_list(workspace_id="ws-1", agent_install_id="agent-b"))
        self.assertEqual(listing_b["files"], [])

        listing_a = asyncio.run(agent_memory_tools.memory_list(workspace_id="ws-1", agent_install_id="agent-a"))
        self.assertEqual(len(listing_a["files"]), 1)
        self.assertEqual(listing_a["files"][0]["path"], "secret.md")

    def test_agent_b_cannot_read_agent_as_file_by_guessing_its_name(self) -> None:
        """Even knowing the exact filename A used, B's memory_read call is
        scoped to B's own directory — there is no install_id parameter B's
        tool call can influence to reach into A's directory instead."""
        asyncio.run(agent_memory_tools.memory_write(
            workspace_id="ws-1", agent_install_id="agent-a",
            path="secret.md", content="Agent A's private business plan.",
        ))
        result = asyncio.run(agent_memory_tools.memory_read(
            workspace_id="ws-1", agent_install_id="agent-b", path="secret.md",
        ))
        self.assertFalse(result["ok"])
        self.assertIn("not found", result["error"].lower())

    def test_different_workspaces_are_also_isolated_from_each_other(self) -> None:
        asyncio.run(agent_memory_tools.memory_write(
            workspace_id="ws-1", agent_install_id="agent-a", path="note.md", content="ws-1 content",
        ))
        listing = asyncio.run(agent_memory_tools.memory_list(workspace_id="ws-2", agent_install_id="agent-a"))
        self.assertEqual(listing["files"], [], "same install_id in a different workspace must not see ws-1's files")


# ── (a) + (c): memory_search / memory_get via the REAL skills_service dispatcher ──


def _real_memory_callbacks() -> direct_tool_execution_service.DirectToolExecutionCallbacks:
    """A callbacks object wired to the REAL memory_service functions for
    search/get (not test doubles) — this is what proves the actual
    production code path is closed, not just the leaf function in
    isolation. Every other field is a minimal stub; these tests only
    exercise the memory_search/memory_get/memory_update branches."""
    return direct_tool_execution_service.DirectToolExecutionCallbacks(
        compact_step_detail=lambda value: None,
        titleize_direct_step_token=lambda value: str(value or ""),
        run_async_tool_call=lambda awaitable: awaitable,
        parse_tool_name=lambda name: (
            tuple(str(name or "").split("__", 1)) if "__" in str(name or "") else tuple(str(name or "").split("_", 1))
        ),
        tool_arguments_payload=lambda payload: payload if isinstance(payload, dict) else {},
        parse_json_object_loose=lambda value: {},
        safe_positive_int=lambda value, default=0: int(value) if str(value or "").strip().isdigit() else default,
        normalize_reasoning_effort=lambda value: None,
        build_direct_local_tool_config=skills_service.build_direct_local_tool_config,
        format_direct_local_tool_result=lambda result: json.dumps(result, ensure_ascii=False),
        build_direct_tool_config=lambda connector_id, action_id, tool_input: {
            "connector": connector_id, "action": action_id, "input": tool_input,
        },
        format_direct_tool_result=lambda result: json.dumps(result, ensure_ascii=False),
        llm_task=lambda *args, **kwargs: {"ok": True},
        web_search=lambda query: [],
        web_fetch=lambda url: "",
        # The real thing under test:
        search_memory_notebook=memory_service.search_memory_notebook,
        get_memory_notebook_excerpt=memory_service.get_memory_notebook_excerpt,
        update_memory_context_file=memory_service.update_memory_context_file,
        memory_append_daily_note=lambda *args, **kwargs: {},
        create_memory_consolidation_staging_file=lambda *args, **kwargs: {},
        consolidate_daily_memory_notes=lambda *args, **kwargs: {},
        apply_memory_consolidation_staging=lambda *args, **kwargs: {},
        list_memory_file_versions=lambda *args, **kwargs: [],
        rollback_memory_file_version=lambda *args, **kwargs: {},
    )


class MemorySearchGetCrossAgentTests(_IsolatedMemoryTestCase):
    def _seed(self, *, agent_install_id: str, filename: str, content: str) -> None:
        # Write straight to the resolved path rather than going through
        # write_workspace_context_file/update_memory_context_file: both
        # route through a Rust-kernel state-store decision that this unit
        # test environment can't fully satisfy (a pre-existing gap — see
        # test_workspace_context_files.py's own baseline failures, unrelated
        # to this audit). This is purely a test-seeding shortcut; the actual
        # dispatch path under test (execute_single_direct_tool_call ->
        # memory_search/memory_get, both real, unmocked) is unaffected.
        root = workspace_context.agent_workspace_context_dir(
            workspace_id="ws-1", agent_install_id=agent_install_id,
        )
        (root / filename).write_text(content, encoding="utf-8")

    def test_agent_a_search_never_surfaces_agent_bs_content(self) -> None:
        self._seed(agent_install_id="agent-a", filename="MEMORY.md", content="## Summary\n\nAgent A tracks apollo-project deadlines.\n")
        self._seed(agent_install_id="agent-b", filename="MEMORY.md", content="## Summary\n\nAgent B holds the zeta-merger financial terms.\n")

        as_a = skills_service.execute_single_direct_tool_call(
            tool_call={"name": "memory_search", "arguments": {"query": "zeta-merger"}},
            workspace_id="ws-1", thread_id="t-1",
            session_ctx={"agent_install_id": "agent-a"},
            callbacks=_real_memory_callbacks(),
        )
        self.assertEqual(json.loads(as_a)["results"], [], "agent A's search must not find agent B's content")

        as_a_own = skills_service.execute_single_direct_tool_call(
            tool_call={"name": "memory_search", "arguments": {"query": "apollo-project"}},
            workspace_id="ws-1", thread_id="t-1",
            session_ctx={"agent_install_id": "agent-a"},
            callbacks=_real_memory_callbacks(),
        )
        self.assertGreater(len(json.loads(as_a_own)["results"]), 0, "agent A must still find its OWN content")

    def test_agent_b_search_never_surfaces_agent_as_content(self) -> None:
        self._seed(agent_install_id="agent-a", filename="MEMORY.md", content="## Summary\n\nAgent A tracks apollo-project deadlines.\n")
        self._seed(agent_install_id="agent-b", filename="MEMORY.md", content="## Summary\n\nAgent B holds the zeta-merger financial terms.\n")

        as_b = skills_service.execute_single_direct_tool_call(
            tool_call={"name": "memory_search", "arguments": {"query": "apollo-project"}},
            workspace_id="ws-1", thread_id="t-1",
            session_ctx={"agent_install_id": "agent-b"},
            callbacks=_real_memory_callbacks(),
        )
        self.assertEqual(json.loads(as_b)["results"], [], "agent B's search must not find agent A's content")

    def test_memory_get_scoped_to_the_calling_agent_not_another_specialists(self) -> None:
        self._seed(agent_install_id="agent-a", filename="MEMORY.md", content="## Summary\n\nOnly agent A knows this.\n")
        self._seed(agent_install_id="agent-b", filename="MEMORY.md", content="## Summary\n\nOnly agent B knows this.\n")

        raw = skills_service.execute_single_direct_tool_call(
            tool_call={"name": "memory_get", "arguments": {"path": "MEMORY.md"}},
            workspace_id="ws-1", thread_id="t-1",
            session_ctx={"agent_install_id": "agent-a"},
            callbacks=_real_memory_callbacks(),
        )
        payload = json.loads(raw)
        self.assertIn("Only agent A knows this", payload["text"])
        self.assertNotIn("Only agent B knows this", payload["text"])

    def test_active_agent_install_id_alias_is_also_honored(self) -> None:
        """session_metadata carries the caller's identity under either key
        (agent_install_id or active_agent_install_id, per the fallback the
        fix mirrors from every sibling memory action) — both must scope
        correctly, not just one spelling. Uses token-disjoint content (the
        notebook search matches on individual words, so "secret alpha" vs
        "secret beta" would share a "secret" hit and produce a false
        positive here) — projectnightingale / projectfirefly share no
        tokens at all."""
        self._seed(agent_install_id="agent-a", filename="MEMORY.md", content="## Summary\n\nAgent A tracks projectnightingale.\n")
        self._seed(agent_install_id="agent-b", filename="MEMORY.md", content="## Summary\n\nAgent B tracks projectfirefly.\n")

        raw = skills_service.execute_single_direct_tool_call(
            tool_call={"name": "memory_search", "arguments": {"query": "projectfirefly"}},
            workspace_id="ws-1", thread_id="t-1",
            session_ctx={"active_agent_install_id": "agent-a"},
            callbacks=_real_memory_callbacks(),
        )
        self.assertEqual(json.loads(raw)["results"], [])

    def test_sage_own_turn_with_no_agent_install_id_reads_its_own_root_not_a_specialists(self) -> None:
        """Sage (no agent_install_id at all — the legitimate root-level
        case) must see its OWN root notebook, and must NEVER see a
        specialist's install-scoped content just because no id was set."""
        self._seed(agent_install_id="agent-a", filename="MEMORY.md", content="## Summary\n\nSpecialist-only fact.\n")
        self._seed(agent_install_id=None, filename="MEMORY.md", content="## Summary\n\nSage's own root-level fact.\n")

        raw = skills_service.execute_single_direct_tool_call(
            tool_call={"name": "memory_search", "arguments": {"query": "Specialist-only"}},
            workspace_id="ws-1", thread_id="t-1",
            session_ctx={},
            callbacks=_real_memory_callbacks(),
        )
        self.assertEqual(json.loads(raw)["results"], [], "Sage's own turn must not surface a specialist's private content")


# ── (c): the SQLite memory_entries layer (agent_memory.py) ─────────────────


class SqliteMemoryEntriesIsolationTests(_IsolatedMemoryTestCase):
    def test_two_installs_get_physically_separate_database_files(self) -> None:
        path_a = agent_memory._memory_db_path("ws-1", agent_install_id="agent-a")
        path_b = agent_memory._memory_db_path("ws-1", agent_install_id="agent-b")
        self.assertNotEqual(path_a, path_b)

    def test_agent_a_entry_never_appears_in_agent_bs_list(self) -> None:
        agent_memory._save_memory("ws-1", "customer_deal", "Agent A: acme corp, $50k ARR.", agent_install_id="agent-a", sync_memory_md=False)
        agent_memory._save_memory("ws-1", "customer_deal", "Agent B: initech, $12k ARR.", agent_install_id="agent-b", sync_memory_md=False)

        entries_a = agent_memory._list_memory_entries("ws-1", agent_install_id="agent-a")
        entries_b = agent_memory._list_memory_entries("ws-1", agent_install_id="agent-b")

        self.assertEqual(len(entries_a), 1)
        self.assertIn("acme corp", entries_a[0]["content"])
        self.assertEqual(len(entries_b), 1)
        self.assertIn("initech", entries_b[0]["content"])
        self.assertNotIn("acme corp", entries_b[0]["content"])

    def test_keyword_search_is_isolated_per_install(self) -> None:
        agent_memory._save_memory("ws-1", "note", "The launch codeword is nightingale.", agent_install_id="agent-a", sync_memory_md=False)
        agent_memory._save_memory("ws-1", "note", "Unrelated content for agent B.", agent_install_id="agent-b", sync_memory_md=False)

        hits_b = agent_memory._search_memory("ws-1", "nightingale", agent_install_id="agent-b")
        self.assertEqual(hits_b, [])

        hits_a = agent_memory._search_memory("ws-1", "nightingale", agent_install_id="agent-a")
        self.assertEqual(len(hits_a), 1)

    def test_semantic_search_is_isolated_per_install(self) -> None:
        agent_memory._save_memory("ws-1", "note", "Quarterly revenue target is 2 million dollars.", agent_install_id="agent-a", sync_memory_md=False)
        # No entries at all for agent-b — semantic search over an empty
        # install must return nothing, never fall through to agent-a's data.
        results_b = agent_memory._semantic_search("ws-1", "revenue target", agent_install_id="agent-b")
        self.assertEqual(results_b, [])

    def test_delete_on_one_install_never_touches_anothers_row(self) -> None:
        agent_memory._save_memory("ws-1", "shared_key_name", "Agent A's value.", agent_install_id="agent-a", sync_memory_md=False)
        agent_memory._save_memory("ws-1", "shared_key_name", "Agent B's value.", agent_install_id="agent-b", sync_memory_md=False)

        agent_memory._delete_memory("ws-1", "shared_key_name", agent_install_id="agent-a")

        self.assertEqual(agent_memory._list_memory_entries("ws-1", agent_install_id="agent-a"), [])
        entries_b = agent_memory._list_memory_entries("ws-1", agent_install_id="agent-b")
        self.assertEqual(len(entries_b), 1)
        self.assertIn("Agent B's value", entries_b[0]["content"])


if __name__ == "__main__":
    unittest.main()
