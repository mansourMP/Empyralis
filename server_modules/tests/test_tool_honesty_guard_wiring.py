"""MAN-263 wiring proof: the tool-honesty guard is a structural backstop
(tool_honesty_guard.py) that is only as good as its reachability. The
CORRECTNESS of the guard's logic is covered by test_tool_honesty_guard.py
(including NarratesToolCallAfterSuccessTests, which reproduces the exact
recorded MAN-263 incident); this file covers the other half CLAUDE.md's own
recurring-failure section keeps re-discovering the hard way — "a guard
called once inside a large function is a guard the next branch will skip"
(handle_sage_chat/_guard_sage_visible_reply, MAN-263's own sibling bug) and
"built, tested, and never wired" (the codebase's single most common defect).

Both live "final reply" pipelines call the guard exactly once, at their own
narrow waist, never per-branch:

  * agent_turn_runtime_service._handle_sage_chat_unguarded (reached ONLY via
    the handle_sage_chat wrapper -- see test_unguarded_reply_paths.py's own
    StructuralGuardSeamTests, which already proves that seam) calls
    tool_honesty_guard.apply_tool_honesty_guard on action_result["message"]
    for EVERY turn that reaches a natural-language reply, regardless of
    which turn engine produced it -- both the claude_agent_sdk engine (the
    production default since MAN-310) and the legacy engine converge on the
    SAME _collect_sage_operator_loop_v3_events collector before this call,
    so this one call site covers both.

  * direct_chat_generation_service.stream_provider_backed_direct_chat (the
    specialist's own Chat tab / api/turn surface, independent of Sage) calls
    tool_honesty_guard.apply_tool_honesty_guard_sync at its own final-reply
    point, reached whenever the tool loop naturally completes (iteration_
    tool_calls empty on a "result" event).

A behavioural test can only cover the branches that exist today; these are
AST-based so a THIRD branch added later that reaches a final reply without
crossing the guard call fails this test even before anyone writes a
regression case for it.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from server_modules import direct_chat_generation_service
from server_modules import agent_turn_runtime_service


def _module_tree(module) -> ast.Module:
    return ast.parse(Path(module.__file__).read_text())


def _find_func(tree: ast.Module, name: str):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found")


def _call_sites(tree: ast.Module, *, attr_owner: str, attr_name: str) -> list[int]:
    """Line numbers of every `<attr_owner>.<attr_name>(...)` call in `tree`."""
    sites: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == attr_name
            and isinstance(func.value, ast.Name)
            and func.value.id == attr_owner
        ):
            sites.append(node.lineno)
    return sites


class SageActionLoopGuardWiringTests(unittest.TestCase):
    def test_apply_tool_honesty_guard_has_exactly_one_call_site(self) -> None:
        tree = _module_tree(agent_turn_runtime_service)
        sites = _call_sites(tree, attr_owner="tool_honesty_guard", attr_name="apply_tool_honesty_guard")
        self.assertEqual(len(sites), 1, f"expected exactly one call site, found {sites}")

    def test_the_call_site_is_inside_the_unguarded_body_every_turn_crosses(self) -> None:
        """_handle_sage_chat_unguarded is reachable ONLY via the handle_sage_chat
        wrapper (test_unguarded_reply_paths.py's StructuralGuardSeamTests proves
        that seam independently) -- so a call site inside this function's body
        is reachable from every real caller, present and future, without this
        test having to re-derive that reachability proof itself."""
        tree = _module_tree(agent_turn_runtime_service)
        body = _find_func(tree, "_handle_sage_chat_unguarded")
        body_lines = range(body.lineno, (body.end_lineno or body.lineno) + 1)
        sites = _call_sites(tree, attr_owner="tool_honesty_guard", attr_name="apply_tool_honesty_guard")
        self.assertEqual(len(sites), 1, f"expected exactly one call site, found {sites}")
        self.assertIn(
            sites[0], body_lines,
            "tool_honesty_guard.apply_tool_honesty_guard must be called inside "
            "_handle_sage_chat_unguarded, the one body every engine (SDK and "
            "legacy) and every real caller (via the handle_sage_chat wrapper) "
            "passes through -- a call site outside it is a call site a future "
            "branch can route around.",
        )


class DirectChatGuardWiringTests(unittest.TestCase):
    def test_apply_tool_honesty_guard_sync_has_exactly_one_call_site(self) -> None:
        tree = _module_tree(direct_chat_generation_service)
        sites = _call_sites(tree, attr_owner="tool_honesty_guard", attr_name="apply_tool_honesty_guard_sync")
        self.assertEqual(len(sites), 1, f"expected exactly one call site, found {sites}")

    def test_the_call_site_is_inside_stream_provider_backed_direct_chat(self) -> None:
        tree = _module_tree(direct_chat_generation_service)
        body = _find_func(tree, "stream_provider_backed_direct_chat")
        body_lines = range(body.lineno, (body.end_lineno or body.lineno) + 1)
        sites = _call_sites(tree, attr_owner="tool_honesty_guard", attr_name="apply_tool_honesty_guard_sync")
        self.assertEqual(len(sites), 1, f"expected exactly one call site, found {sites}")
        self.assertIn(sites[0], body_lines)


if __name__ == "__main__":
    unittest.main()
