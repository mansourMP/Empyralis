"""Phase G: workspace_scope.py — truthful isolation tests."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from server_modules import workspace_scope


class ResolveWorkspaceTests(unittest.TestCase):
    def setUp(self):
        # Reset counter between tests
        with workspace_scope._counter_lock:
            workspace_scope._workspace_default_used_total.clear()

    # ------------------------------------------------------------------
    # Happy path: explicit workspace_id
    # ------------------------------------------------------------------

    def test_explicit_workspace_id_returned_directly(self):
        ws = workspace_scope.resolve_workspace(
            workspace_id="workspace-abc", site="test:explicit"
        )
        self.assertEqual(ws, "workspace-abc")

    def test_default_literal_is_rejected_and_falls_through(self):
        """'default' is NOT a valid workspace — must fall through to sources."""
        ws = workspace_scope.resolve_workspace(
            "default",
            {"workspace_id": "workspace-real"},
            site="test:default_rejected",
        )
        self.assertEqual(ws, "workspace-real")

    # ------------------------------------------------------------------
    # Source resolution: dict
    # ------------------------------------------------------------------

    def test_dict_source_resolved(self):
        ws = workspace_scope.resolve_workspace(
            None, {"workspace_id": "ws-from-dict"}, site="test:dict"
        )
        self.assertEqual(ws, "ws-from-dict")

    def test_dict_source_with_default_value_is_skipped(self):
        """A dict with workspace_id='default' is skipped."""
        ws = workspace_scope.resolve_workspace(
            None,
            {"workspace_id": "default"},
            {"workspace_id": "ws-real"},
            site="test:dict_default_skip",
        )
        self.assertEqual(ws, "ws-real")

    def test_multiple_sources_first_wins(self):
        ws = workspace_scope.resolve_workspace(
            None,
            {"workspace_id": "ws-first"},
            {"workspace_id": "ws-second"},
            site="test:multi_source",
        )
        self.assertEqual(ws, "ws-first")

    # ------------------------------------------------------------------
    # Source resolution: attribute-based
    # ------------------------------------------------------------------

    def test_attribute_source_resolved(self):
        class Req:
            workspace_id = "ws-from-attr"

        ws = workspace_scope.resolve_workspace(
            None, Req(), site="test:attr"
        )
        self.assertEqual(ws, "ws-from-attr")

    # ------------------------------------------------------------------
    # Ephemeral markers: uniqueness
    # ------------------------------------------------------------------

    def test_two_missing_workspaces_get_different_ephemeral_ids(self):
        """Two callers without workspace get DIFFERENT unscoped markers."""
        ws_a = workspace_scope.resolve_workspace(None, site="test:caller_a")
        ws_b = workspace_scope.resolve_workspace(None, site="test:caller_b")

        self.assertTrue(ws_a.startswith("_unscoped_"))
        self.assertTrue(ws_b.startswith("_unscoped_"))
        self.assertNotEqual(ws_a, ws_b)

    def test_ephemeral_ids_are_never_colliding(self):
        """Generate 100 ephemeral ids — all unique."""
        ids = {
            workspace_scope.resolve_workspace(None, site=f"test:collision_{i}")
            for i in range(100)
        }
        self.assertEqual(len(ids), 100)
        for wid in ids:
            self.assertTrue(wid.startswith("_unscoped_"), f"unexpected id: {wid}")

    # ------------------------------------------------------------------
    # Counter
    # ------------------------------------------------------------------

    def test_missing_workspace_increments_counter(self):
        """Each missing workspace increments the per-site counter."""
        # Resolve without workspace twice at same site
        workspace_scope.resolve_workspace(None, site="test:counter_a")
        workspace_scope.resolve_workspace(None, site="test:counter_a")
        # Once at a different site
        workspace_scope.resolve_workspace(None, site="test:counter_b")

        counter = workspace_scope.workspace_default_used_counter()
        self.assertEqual(counter.get("test:counter_a"), 2)
        self.assertEqual(counter.get("test:counter_b"), 1)

    # ------------------------------------------------------------------
    # Launch gate: EMPYRALIS_REQUIRE_WORKSPACE
    # ------------------------------------------------------------------

    def test_require_workspace_flag_raises_when_true(self):
        with patch.object(
            workspace_scope, "_require_workspace_flag", return_value=True
        ):
            with self.assertRaises(workspace_scope.WorkspaceUnresolvedError) as ctx:
                workspace_scope.resolve_workspace(None, site="test:strict")
            self.assertEqual(ctx.exception.site, "test:strict")

    def test_require_workspace_flag_false_returns_ephemeral(self):
        with patch.object(
            workspace_scope, "_require_workspace_flag", return_value=False
        ):
            ws = workspace_scope.resolve_workspace(None, site="test:lenient")
            self.assertTrue(ws.startswith("_unscoped_"))

    # ------------------------------------------------------------------
    # Edge cases
    # ------------------------------------------------------------------

    def test_empty_string_is_treated_as_missing(self):
        ws = workspace_scope.resolve_workspace("", site="test:empty")
        self.assertTrue(ws.startswith("_unscoped_"))

    def test_whitespace_only_is_treated_as_missing(self):
        ws = workspace_scope.resolve_workspace("   ", site="test:whitespace")
        self.assertTrue(ws.startswith("_unscoped_"))

    def test_none_sources_skipped(self):
        ws = workspace_scope.resolve_workspace(
            None, None, {"workspace_id": "ws-late"}, site="test:none_skip"
        )
        self.assertEqual(ws, "ws-late")
