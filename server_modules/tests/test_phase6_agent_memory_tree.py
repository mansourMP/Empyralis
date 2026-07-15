"""Phase 6 — per-agent memory tree.

Proves the design's guarantees, reusing the Phase 4/4B isolation:
  1. A learned rule written to MEMORY.md rides into the next turn's injected context.
  2. A topic file is selectively retrieved — present when the turn is relevant,
     absent when it is not.
  3. Tree files are isolated both directions (a worker cannot see/read another's).
  4. Path-traversal / absolute / too-deep paths are rejected.
  5. Owner API round-trip: PUT file → GET tree lists it → GET reads it → DELETE.
"""
from __future__ import annotations

import asyncio
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from server_modules import workspace_context as wc
from server_modules import agent_memory as am
from server_modules import memory_service
from server_modules import agent_memory_tree_service as tree


class Phase6MemoryTreeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="phase6-memtree-")
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        # Unique workspace id per test — avoids any global workspace-scope state
        # leaking between tests in the full suite.
        self.ws = "ws_p6_" + uuid.uuid4().hex[:8]

        # The runtime kernel (a native binary) is absent in unit tests; mock the
        # state-store gates to "allow" with the next_action each caller expects.
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
            patch.object(wc, "_WORKSPACE_DIR", root / "ws"),
            patch.object(am, "_MEMORY_DIR", root / "mem"),
            patch.object(am, "_SEMANTIC_MODEL", False),
            patch.object(rk, "run_runtime_kernel_enforced", _run_enforced),
            patch.object(rk, "runtime_state_store_decision", _state_decision),
            patch.object(rk, "enforce_kernel_decision", lambda *_a, **_k: None),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    def _injected_context_blocks(self, agent_install_id):
        """The context blocks that ride into the system prompt — MEMORY.md and the
        other stable files are always included (no query heuristics), which is how
        a learned rule reaches every future turn."""
        from server_modules.workspace_context import read_workspace_context_files
        from server_modules.workspace_context_memory_adapter import build_workspace_context_file_blocks
        files = read_workspace_context_files(workspace_id=self.ws, agent_install_id=agent_install_id)
        sections, _diag = build_workspace_context_file_blocks(files)
        return "\n".join(sections)

    # 1 ─ learned rule → next turn's injected context
    def test_learned_rule_rides_into_injected_context(self):
        RULE = "Be pragmatic, skip pleasantries."
        memory_service.memory_write_file(
            self.ws, "MEMORY.md", f"## Learned rules\n- {RULE}",
            mode="append", agent_install_id="install-a",
        )
        # Persisted in A's namespace, and rides into A's injected context.
        self.assertIn(RULE, tree.read_file(self.ws, "MEMORY.md", agent_install_id="install-a")["content"])
        self.assertIn(RULE, self._injected_context_blocks("install-a"))
        # A different worker's context must NOT contain A's rule.
        self.assertNotIn(RULE, self._injected_context_blocks("install-b"))

    # 2 ─ topic file selectively retrieved (present when relevant, absent when not)
    def test_topic_file_selective_retrieval(self):
        tree.write_file(
            self.ws, "customers/acme.md",
            "Acme Corp. Refund window is 30 days. They prefer terse replies.",
            agent_install_id="install-a",
        )
        relevant = memory_service.direct_chat_workspace_context_text(
            self.ws, memory_query="what is Acme's refund window",
            agent_install_id="install-a",
        )
        self.assertIn("Refund window is 30 days", relevant)
        self.assertIn("customers/acme.md", relevant)

        irrelevant = memory_service.direct_chat_workspace_context_text(
            self.ws, memory_query="what's the weather forecast tomorrow",
            agent_install_id="install-a",
        )
        self.assertNotIn("Refund window is 30 days", irrelevant)

    # 3 ─ isolation both directions on tree files
    def test_tree_isolation_both_directions(self):
        tree.write_file(self.ws, "customers/acme.md", "A-SECRET-ACME", agent_install_id="install-a")
        tree.write_file(self.ws, "customers/globex.md", "B-SECRET-GLOBEX", agent_install_id="install-b")

        a_tree = tree.list_tree(self.ws, agent_install_id="install-a")
        b_tree = tree.list_tree(self.ws, agent_install_id="install-b")
        self.assertEqual([t["path"] for t in a_tree["topics"]], ["customers/acme.md"])
        self.assertEqual([t["path"] for t in b_tree["topics"]], ["customers/globex.md"])

        # cross reads return empty (the other's file does not exist in this namespace)
        self.assertEqual(tree.read_file(self.ws, "customers/globex.md", agent_install_id="install-a")["content"], "")
        self.assertEqual(tree.read_file(self.ws, "customers/acme.md", agent_install_id="install-b")["content"], "")

    # 4 ─ path traversal / absolute / too-deep rejected
    def test_path_traversal_rejected(self):
        for bad in ["../etc/passwd", "/abs.md", "customers/../../x.md", "a/b/c/deep.md", "~/secret.md"]:
            with self.assertRaises(ValueError, msg=f"should reject {bad!r}"):
                tree.write_file(self.ws, bad, "x", agent_install_id="install-a")
            with self.assertRaises(ValueError):
                tree.read_file(self.ws, bad, agent_install_id="install-a")

    # 5 ─ owner API round-trip
    def test_owner_api_round_trip(self):
        from server_modules import routes_fleet

        async def _fake_ns(workspace_id, agent_id):
            return "install-a"

        owner_user = {"user_id": "owner-1", "email": "owner@example.com"}

        def _fake_enforce_workspace_access(current_user, workspace_id, minimum_role="viewer"):
            return workspace_id

        async def _run():
            with (
                patch.object(routes_fleet, "_resolve_memory_namespace", _fake_ns),
                patch.object(routes_fleet.auth_module, "enforce_workspace_access", _fake_enforce_workspace_access),
            ):
                body = routes_fleet.FleetMemoryFileWriteRequest(content="Owner-written note.", mode="replace")
                w = await routes_fleet.fleet_agent_memory_file_write(
                    None, self.ws, "install-a", body, path="procedures/refunds.md", current_user=owner_user,
                )
                self.assertTrue(w["ok"])

                t = await routes_fleet.fleet_agent_memory_tree(None, self.ws, "install-a", current_user=owner_user)
                self.assertTrue(t["ok"])
                self.assertEqual(t["scope"], "install")
                self.assertIn("procedures/refunds.md", [x["path"] for x in t["topics"]])

                r = await routes_fleet.fleet_agent_memory_file_read(
                    None, self.ws, "install-a", path="procedures/refunds.md", current_user=owner_user,
                )
                self.assertEqual(r["content"], "Owner-written note.")

                d = await routes_fleet.fleet_agent_memory_file_delete(
                    None, self.ws, "install-a", path="procedures/refunds.md", current_user=owner_user,
                )
                self.assertTrue(d["deleted"])

                t2 = await routes_fleet.fleet_agent_memory_tree(None, self.ws, "install-a", current_user=owner_user)
                self.assertEqual(t2["topic_count"], 0)

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
