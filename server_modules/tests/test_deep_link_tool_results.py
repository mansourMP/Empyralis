"""MAN-358 — the link has to be IN the tool result, or the agent cannot say it.

Chat left the platform; conversation happens in Telegram/Slack. An agent
composes its channel reply from what its tools handed back, so the ONLY way
"I created GEN-12 for you" can carry a tappable address is for the address to
be part of the project_task__* / document__* result. There is deliberately no
channel-side link rewriter — a per-channel builder would be the "channels is
ONE system" rule broken, and it would have to re-derive an identity the tool
layer already holds.

Two kinds of assertion here, and both are needed:

  BEHAVIOURAL   the REAL, unmocked native dispatch (tool name -> parse ->
                skills_service.execute_single_direct_tool_call -> the real
                project_tasks_service functions), reusing
                test_project_task_native_tools' own fake-pool harness rather
                than a fixture invented here — so the task dict under test is
                the one production actually builds.
  STRUCTURAL    an AST scan asserting that EVERY return in the project_task /
                document dispatch blocks passes its object through the
                annotator. A behavioural test can only ever cover the actions
                that exist today; the next action added is exactly the one
                that silently ships without a link.
"""

from __future__ import annotations

import ast
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from test_project_task_native_tools import _QueuedFakePool, _call, _task_row


PROD_ENV = {
    "EMPYRALIS_DEPLOY_ENV": "self-hosted",
    "EMPYRALIS_PUBLIC_FRONTEND_ORIGIN": "https://empyralis.ai",
}


def _with_env(env: dict):
    """Replace the process environment wholesale for the duration.

    `patch.dict(clear=True)` and not an update: leaving the developer's real
    EMPYRALIS_PUBLIC_FRONTEND_ORIGIN in place would make the no-origin test
    below pass or fail depending on whose box it runs on.
    """
    return patch.dict(os.environ, env, clear=True)


class TaskToolResultCarriesALinkTests(unittest.TestCase):
    def test_create_returns_a_tappable_url_and_the_real_identifier(self) -> None:
        pool = _QueuedFakePool(
            fetchrow_results=[
                {"project_id": "proj-1"},
                {"task_seq": 12},
                _task_row(number=12, project_task_key="GEN"),
            ],
        )
        with _with_env(PROD_ENV):
            result = _call("project_task__create", {"title": "Draft the summary"}, pool=pool)
        self.assertTrue(result["ok"])
        self.assertEqual(
            result["task"]["url"],
            "https://empyralis.ai/w/ws-1/projects/proj-1/tasks/task-1",
        )
        # The real per-project identifier, never a hex slice of the uuid.
        self.assertEqual(result["task"]["display_id"], "GEN-12")

    def test_no_configured_origin_means_no_url_key_at_all(self) -> None:
        """The honest degradation, driven through the real dispatch: not
        `None/w/...`, not an empty string the model could interpolate into a
        sentence — the key is simply absent."""
        pool = _QueuedFakePool(
            fetchrow_results=[
                {"project_id": "proj-1"},
                {"task_seq": 12},
                _task_row(number=12, project_task_key="GEN"),
            ],
        )
        with _with_env({"EMPYRALIS_DEPLOY_ENV": "self-hosted"}):
            result = _call("project_task__create", {"title": "Draft the summary"}, pool=pool)
        self.assertTrue(result["ok"])
        self.assertNotIn("url", result["task"])
        # The identifier does not depend on an origin, so it still arrives —
        # the agent can still say WHAT it made, just not where to open it.
        self.assertEqual(result["task"]["display_id"], "GEN-12")

    def test_a_task_with_no_sequence_number_gets_no_invented_identifier(self) -> None:
        pool = _QueuedFakePool(
            fetchrow_results=[{"project_id": "proj-1"}, {"task_seq": 1}, _task_row()],
        )
        with _with_env(PROD_ENV):
            result = _call("project_task__create", {"title": "Draft the summary"}, pool=pool)
        self.assertNotIn("display_id", result["task"])
        self.assertIn("url", result["task"])

    def test_list_links_every_row(self) -> None:
        pool = _QueuedFakePool(
            fetchrow_results=[{"project_id": "proj-1"}],
            fetch_results=[[
                _task_row(id="task-1", number=1, project_task_key="GEN"),
                _task_row(id="task-2", number=2, project_task_key="GEN"),
            ]],
        )
        with _with_env(PROD_ENV):
            result = _call("project_task__list", {}, pool=pool)
        self.assertEqual(
            [row["url"] for row in result["tasks"]],
            [
                "https://empyralis.ai/w/ws-1/projects/proj-1/tasks/task-1",
                "https://empyralis.ai/w/ws-1/projects/proj-1/tasks/task-2",
            ],
        )
        self.assertEqual([row["display_id"] for row in result["tasks"]], ["GEN-1", "GEN-2"])


class DispatchAnnotationDriftTests(unittest.TestCase):
    """Every object-returning branch of both dispatch blocks must go through
    the annotator. Reading the source is the only way to see this: a new
    action that forgets it compiles, runs, returns a perfectly valid result,
    and is silently unlinkable."""

    SOURCE = Path(__file__).resolve().parents[1] / "skills_service.py"

    @classmethod
    def setUpClass(cls) -> None:
        cls.tree = ast.parse(cls.SOURCE.read_text(encoding="utf-8"))

    def _dispatch_block(self, connector_id: str) -> ast.If:
        """The `if connector_id == "<id>":` statement, found structurally."""
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
                continue
            test = node.test
            if not (isinstance(test.left, ast.Name) and test.left.id == "connector_id"):
                continue
            comparator = test.comparators[0] if test.comparators else None
            if isinstance(comparator, ast.Constant) and comparator.value == connector_id:
                return node
        self.fail(f"no `connector_id == {connector_id!r}` dispatch block found — this scan is not looking where it thinks")

    def _returned_dict_values(self, block: ast.If, key: str) -> list[ast.expr]:
        """Every `return json.dumps({... "<key>": <value> ...})` value in the block."""
        found: list[ast.expr] = []
        for node in ast.walk(block):
            if not isinstance(node, ast.Return) or not isinstance(node.value, ast.Call):
                continue
            call = node.value
            if not (isinstance(call.func, ast.Attribute) and call.func.attr == "dumps"):
                continue
            for arg in call.args:
                if not isinstance(arg, ast.Dict):
                    continue
                for dict_key, dict_value in zip(arg.keys, arg.values):
                    if isinstance(dict_key, ast.Constant) and dict_key.value == key:
                        found.append(dict_value)
        return found

    @staticmethod
    def _mentions(node: ast.expr, name: str) -> bool:
        return any(isinstance(sub, ast.Name) and sub.id == name for sub in ast.walk(node))

    def test_canary_both_blocks_and_their_returns_are_found(self) -> None:
        task_block = self._dispatch_block("project_task")
        document_block = self._dispatch_block("document")
        self.assertGreaterEqual(len(self._returned_dict_values(task_block, "task")), 4)
        self.assertGreaterEqual(len(self._returned_dict_values(document_block, "document")), 3)

    def test_every_returned_task_is_annotated(self) -> None:
        block = self._dispatch_block("project_task")
        for key in ("task", "tasks", "subtasks"):
            for value in self._returned_dict_values(block, key):
                with self.subTest(key=key, value=ast.unparse(value)):
                    self.assertTrue(
                        self._mentions(value, "_linked"),
                        f'project_task returns "{key}" without passing it through _linked — '
                        "that result reaches a channel with no address in it",
                    )

    def test_every_returned_document_is_annotated(self) -> None:
        block = self._dispatch_block("document")
        for key in ("document", "documents"):
            for value in self._returned_dict_values(block, key):
                with self.subTest(key=key, value=ast.unparse(value)):
                    self.assertTrue(
                        self._mentions(value, "_document_link") or self._mentions(value, "_document_summary"),
                        f'document returns "{key}" without a link — _document_summary is the '
                        "chokepoint for list/write/edit, _document_link for read",
                    )

    def test_the_document_summary_chokepoint_still_returns_a_linked_object(self) -> None:
        """_document_summary is trusted by the test above as a link-carrying
        chokepoint. That trust has to be checked, or removing one line inside
        it would satisfy every assertion here while shipping no links."""
        block = self._dispatch_block("document")
        for node in ast.walk(block):
            if isinstance(node, ast.FunctionDef) and node.name == "_document_summary":
                returns = [n for n in ast.walk(node) if isinstance(n, ast.Return) and n.value is not None]
                self.assertTrue(returns, "_document_summary returns nothing")
                self.assertTrue(
                    all(self._mentions(r.value, "_document_link") for r in returns),
                    "_document_summary no longer routes through _document_link",
                )
                return
        self.fail("_document_summary not found in the document dispatch block")


if __name__ == "__main__":
    unittest.main()
