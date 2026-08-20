"""The owner-facing memory routes: what they expose, and what they must
structurally refuse to expose.

Three of an agent's four memory stores had no HTTP reader at all before
2026-08-20 -- the facts table, the rolling day-log, and the per-person
private note. Adding readers for the first two is ordinary. Adding one for
the THIRD is a boundary decision, and this file exists mainly to pin it:

  agent_private_memory_notes is scoped by (tenant, workspace, agent_install,
  USER). The user must come from the authenticated session and from nowhere
  else. A `user_id` query/path/body parameter on these routes would turn a
  workspace owner into a reader of every teammate's private note with one
  URL edit, defeating in one line the four-column WHERE clause
  agent_private_memory_repository exists to enforce.

These are AST assertions over the real route source, not behavioural tests,
for the reason CLAUDE.md gives every time this pattern appears here: a
behavioural test can only cover the parameters that exist today, and the
regression being guarded against is somebody ADDING one.
"""

from __future__ import annotations

import ast
import pathlib
import unittest
from unittest import mock

_ROUTES = pathlib.Path(__file__).resolve().parents[1] / "routes_fleet.py"
_PRIVATE_NOTE_HANDLERS = {
    "fleet_agent_private_memory_note_read",
    "fleet_agent_private_memory_note_write",
}
_MEMORY_HANDLERS = _PRIVATE_NOTE_HANDLERS | {
    "fleet_agent_memory_facts",
    "fleet_agent_memory_fact_delete",
    "fleet_agent_memory_daily",
}


def _handlers():
    tree = ast.parse(_ROUTES.read_text(encoding="utf-8"))
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in _MEMORY_HANDLERS:
            out[node.name] = node
    return out


class MemoryRoutesExistTests(unittest.TestCase):
    def test_every_memory_route_handler_is_present(self):
        """A canary: if these are renamed or removed, every assertion below
        would vacuously pass over an empty set."""
        self.assertEqual(set(_handlers()), _MEMORY_HANDLERS)


class PrivateNoteIsSessionScopedTests(unittest.TestCase):
    def test_no_private_note_route_accepts_a_user_id_parameter(self):
        for name in _PRIVATE_NOTE_HANDLERS:
            handler = _handlers()[name]
            args = [a.arg for a in handler.args.args + handler.args.kwonlyargs]
            self.assertNotIn(
                "user_id", args,
                f"{name} accepts a caller-supplied user_id — a private note must be "
                "addressable only as the signed-in caller's own",
            )

    def test_private_note_body_model_carries_only_content(self):
        tree = ast.parse(_ROUTES.read_text(encoding="utf-8"))
        model = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name == "FleetPrivateMemoryNoteWriteRequest"
        )
        fields = [n.target.id for n in model.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)]
        self.assertEqual(fields, ["content"])

    def test_user_id_is_read_from_current_user_in_both_handlers(self):
        for name in _PRIVATE_NOTE_HANDLERS:
            source = ast.unparse(_handlers()[name])
            self.assertIn("current_user", source)
            self.assertIn("'user_id'", source)
            self.assertIn("user_id=user_id", source)


class MemoryRoutesEnforceAccessTests(unittest.TestCase):
    def test_every_memory_handler_enforces_workspace_and_agent_access(self):
        for name, handler in _handlers().items():
            source = ast.unparse(handler)
            self.assertIn("enforce_workspace_access", source, f"{name} has no workspace gate")
            self.assertIn("_enforce_agent_project_access", source, f"{name} has no agent gate")

    def test_mutations_require_the_right_minimum_role(self):
        handlers = _handlers()
        # ast.unparse normalizes string literals to single quotes, so every
        # comparison here is written in that form rather than the source's.
        # Forgetting a fact edits the SHARED pool every project member reads.
        self.assertIn("minimum_role='owner'", ast.unparse(handlers["fleet_agent_memory_fact_delete"]))
        # A person's own private note is theirs: gating the WRITE on owner
        # would show a teammate a note about themselves they cannot correct.
        write = ast.unparse(handlers["fleet_agent_private_memory_note_write"])
        self.assertIn("minimum_role='viewer'", write)
        self.assertNotIn("minimum_role='owner'", write)


class MemoryFileMutationsAreGatedTests(unittest.TestCase):
    """The memory-tree GETs carried _enforce_agent_project_access and the
    PUT/DELETE beside them did not -- a no-op for a workspace owner today,
    and exactly the read-gated/write-ungated asymmetry that becomes a real
    gap the moment those roles change."""

    def test_tree_file_write_and_delete_enforce_agent_access(self):
        tree = ast.parse(_ROUTES.read_text(encoding="utf-8"))
        for name in ("fleet_agent_memory_file_write", "fleet_agent_memory_file_delete"):
            handler = next(
                node for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
            )
            self.assertIn("_enforce_agent_project_access", ast.unparse(handler), f"{name} is ungated")


class AsyncPrivateNoteTwinsHoldTheSameGuardsTests(unittest.IsolatedAsyncioTestCase):
    """The route awaits `a*_private_memory_note` rather than calling the sync
    entrypoints, because `run_coro_sync` blocks the caller's thread on a
    separate loop and would stall the event loop from inside a FastAPI
    handler. That split is exactly how a guard gets enforced on one path and
    quietly skipped on the other, so the twins are proven to keep all of
    them."""

    async def asyncSetUp(self):
        from server_modules import agent_private_memory_service as svc
        self.svc = svc
        self.written = []

        async def _fake_upsert(**kwargs):
            self.written.append(kwargs)
            return dict(kwargs)

        async def _fake_get(**kwargs):
            self.written.append(kwargs)
            return None

        self._patches = [
            mock.patch.object(svc.agent_private_memory_repository, "upsert_private_note", _fake_upsert),
            mock.patch.object(svc.agent_private_memory_repository, "get_private_note", _fake_get),
            mock.patch.object(svc, "_resolve_tenant_id", mock.AsyncMock(return_value="t1")),
        ]
        for patch in self._patches:
            patch.start()

    async def asyncTearDown(self):
        for patch in self._patches:
            patch.stop()

    async def test_write_requires_a_resolved_user_identity(self):
        with self.assertRaises(ValueError):
            await self.svc.awrite_private_memory_note(
                "ws", agent_install_id="a1", user_id="", content="hello",
            )
        self.assertEqual(self.written, [])

    async def test_write_refuses_empty_content(self):
        with self.assertRaises(ValueError):
            await self.svc.awrite_private_memory_note(
                "ws", agent_install_id="a1", user_id="u1", content="   ",
            )
        self.assertEqual(self.written, [])

    async def test_write_enforces_the_size_cap(self):
        with self.assertRaises(ValueError):
            await self.svc.awrite_private_memory_note(
                "ws", agent_install_id="a1", user_id="u1",
                content="x" * (self.svc.PRIVATE_MEMORY_NOTE_MAX_CHARS + 1),
            )
        self.assertEqual(self.written, [])

    async def test_write_redacts_before_anything_is_stored(self):
        saved = await self.svc.awrite_private_memory_note(
            "ws", agent_install_id="a1", user_id="u1",
            content="my key is sk-abcdef0123456789 keep it",
        )
        self.assertEqual(len(self.written), 1)
        self.assertNotIn("sk-abcdef0123456789", self.written[0]["content"])
        self.assertTrue(saved["redacted"])

    async def test_read_binds_the_callers_own_user_id(self):
        await self.svc.aget_private_memory_note("ws", agent_install_id="a1", user_id="u1")
        self.assertEqual(self.written[0]["user_id"], "u1")

    async def test_read_requires_a_resolved_user_identity(self):
        with self.assertRaises(ValueError):
            await self.svc.aget_private_memory_note("ws", agent_install_id="a1", user_id="")
        self.assertEqual(self.written, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
