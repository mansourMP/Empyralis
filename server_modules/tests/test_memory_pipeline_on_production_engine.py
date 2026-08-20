"""Does agent memory actually accumulate on the engine that runs?

CLAUDE.md carried a note saying `persist_direct_chat_memory_best_effort` had
"zero callers outside its own module", and concluded the memory pipeline
never ran on the SDK/action-loop engine (the production default). Half of
that was stale by 2026-08-20: a real chain exists
(sage_agent_runtime_service -> conversation_memory_facade_service.
persist_interaction -> memory_service). The CONCLUSION was still right, for
a different and worse reason -- every branch behind that chain was gated on
metadata flags (`persist_memory` / `persist_transcript`) that only the
LEGACY engine's caller chain ever sets, and `persist_interaction` returned
`{"persisted": True}` regardless. A pipeline that ran nothing and reported
success.

These tests pin the corrected behaviour, and are written so that the
STALENESS itself cannot come back silently:

  * The production call site's metadata is not hand-written here. It is read
    out of sage_agent_runtime_service.py's own source with AST, so the day
    somebody changes what that call site passes, these tests are exercising
    the new shape rather than a fixture's memory of the old one. (CLAUDE.md:
    "a fixture that invents its own input cannot notice the real input is
    shaped differently".)
  * The honesty assertion is about the RETURN VALUE, not just about the
    write -- "nothing was persisted" has to be expressible, or the next
    regression is invisible again.
"""

from __future__ import annotations

import ast
import pathlib
import unittest
from typing import Any, Dict, List
from unittest import mock

from server_modules import conversation_memory_facade_service as facade
from server_modules import memory_service


_SAGE_RUNTIME_PATH = pathlib.Path(__file__).resolve().parents[1] / "sage_agent_runtime_service.py"


def _production_persist_metadata_keys() -> List[frozenset]:
    """Every `persist_interaction(...)` call in sage_agent_runtime_service.py,
    reduced to the literal key set of its `metadata=` argument.

    Read from the real module source rather than transcribed, so this test
    tracks the production call site instead of a copy of it."""
    tree = ast.parse(_SAGE_RUNTIME_PATH.read_text(encoding="utf-8"))
    found: List[frozenset] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name != "persist_interaction":
            continue
        for kw in node.keywords:
            if kw.arg != "metadata" or not isinstance(kw.value, ast.Dict):
                continue
            keys = {k.value for k in kw.value.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            found.append(frozenset(keys))
    return found


class ProductionCallSiteShapeTests(unittest.TestCase):
    def test_production_call_sites_exist(self):
        """If this goes to zero, the chain was removed and the rest of this
        file is asserting about a path nothing takes."""
        self.assertGreater(
            len(_production_persist_metadata_keys()),
            0,
            "sage_agent_runtime_service no longer calls persist_interaction — "
            "the memory chain this file pins has moved or been deleted.",
        )

    def test_production_call_sites_still_set_no_persist_flags(self):
        """The premise of the fix: the production engine passes NEITHER flag,
        so anything gated on them is dead on the engine that runs.

        If this ever fails it is GOOD NEWS (someone wired the flags) — but it
        means the daily-log branch below would double-write, so the fix has
        to be revisited rather than left alone."""
        for keys in _production_persist_metadata_keys():
            self.assertNotIn("persist_memory", keys)
            self.assertNotIn("persist_transcript", keys)


class DailyLogFiresOnProductionEngineTests(unittest.TestCase):
    """The deterministic half of the pipeline must run for a turn shaped
    exactly like the production one."""

    def _persist(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        return facade.persist_interaction(
            subject=facade.ConversationMemorySubject(
                workspace_id="ws_test_pipeline",
                tenant_id="t_test_pipeline",
                surface_kind=facade.DIRECT_CHAT_SURFACE,
            ),
            policy_profile=facade.DIRECT_CHAT_PROFILE,
            user_message="We deploy on Fridays and Polar is the payment processor.",
            assistant_reply="Understood.",
            metadata=metadata,
        )

    def test_daily_log_is_written_for_a_production_shaped_turn(self):
        calls: List[Dict[str, Any]] = []
        with mock.patch.object(
            memory_service,
            "save_direct_chat_daily_log_summary",
            side_effect=lambda **kw: (calls.append(kw), "summary line")[1],
        ):
            for keys in _production_persist_metadata_keys():
                calls.clear()
                out = self._persist({key: None for key in keys})
                self.assertEqual(
                    len(calls),
                    1,
                    f"metadata keys {sorted(keys)} produced {len(calls)} daily-log writes, expected 1",
                )
                self.assertTrue(out["daily_log"])
                self.assertTrue(out["persisted"])

    def test_daily_log_is_not_written_twice_on_the_legacy_engine(self):
        """persist_direct_chat_memory_best_effort writes the daily log as its
        own first step. The new unconditional-looking branch must therefore
        NOT also write it when that function is going to run, or every legacy
        turn double-logs."""
        daily_calls: List[Dict[str, Any]] = []
        with mock.patch.object(
            memory_service, "save_direct_chat_daily_log_summary",
            side_effect=lambda **kw: (daily_calls.append(kw), "s")[1],
        ), mock.patch.object(memory_service, "persist_direct_chat_memory_best_effort") as extract:
            out = self._persist({"persist_memory": True})
        self.assertEqual(daily_calls, [], "daily log was written twice on the legacy path")
        self.assertEqual(extract.call_count, 1)
        self.assertTrue(out["facts"])
        self.assertTrue(out["daily_log"])

    def test_a_failed_daily_log_never_raises_and_never_claims_success(self):
        with mock.patch.object(
            memory_service, "save_direct_chat_daily_log_summary",
            side_effect=RuntimeError("runtime_kernel_unavailable"),
        ):
            out = self._persist({"trace_id": "t", "source": "sage_chat"})
        self.assertFalse(out["daily_log"])
        self.assertFalse(out["persisted"])
        self.assertIn("runtime_kernel_unavailable", out["daily_log_error"])


class PersistOutcomeHonestyTests(unittest.TestCase):
    """`persisted` must be derived from what was written, never hardcoded."""

    def test_persisted_is_false_when_nothing_was_written(self):
        with mock.patch.object(
            memory_service, "save_direct_chat_daily_log_summary", return_value=""
        ):
            out = facade.persist_interaction(
                subject=facade.ConversationMemorySubject(
                    workspace_id="ws_test_pipeline",
                    tenant_id="t_test_pipeline",
                    surface_kind=facade.DIRECT_CHAT_SURFACE,
                ),
                policy_profile=facade.DIRECT_CHAT_PROFILE,
                user_message="",
                assistant_reply="",
                metadata={},
            )
        self.assertFalse(out["persisted"])
        self.assertFalse(out["daily_log"])
        self.assertFalse(out["facts"])
        self.assertFalse(out["transcript"])

    def test_persisted_is_not_a_hardcoded_literal(self):
        """An AST assertion over CODE ONLY, because the behavioural tests
        above can be satisfied by a literal `True` the day the daily-log
        branch always succeeds in the test environment. The exact bug fixed
        here was a hardcoded True and it type-checks perfectly.

        Deliberately AST rather than a substring scan: the fixed function's
        own docstring QUOTES the banned shape while explaining it, and a
        naive text search flags its own documentation -- the tripwire this
        codebase already warns about elsewhere."""
        tree = ast.parse(pathlib.Path(facade.__file__).read_text(encoding="utf-8"))
        target = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "persist_interaction"
        )
        offenders = []
        for node in ast.walk(target):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values):
                if not (isinstance(key, ast.Constant) and key.value == "persisted"):
                    continue
                # A constant False is fine (the durable-run branch genuinely
                # persists nothing here); a constant True is the bug.
                if isinstance(value, ast.Constant) and value.value is True:
                    offenders.append(ast.unparse(node))
        self.assertEqual(
            offenders, [],
            "persist_interaction reports `persisted: True` as a literal instead of "
            "deriving it from what was actually written",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
