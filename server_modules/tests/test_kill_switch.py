"""Phase C1: Kill-switch tests.

Verify that GLOBAL_KILL_KEY, WORKSPACE_KILL_PREFIX, AGENT_KILL_PREFIX,
and GATEWAY_KILL_PREFIX each block the correct scope and nothing more.

Tests manipulate the in-memory kill state directly to avoid the
kernel-enforced setter (which requires a real Rust binary).
"""

from __future__ import annotations

import unittest


class KillSwitchScopeTests(unittest.TestCase):

    def setUp(self):
        from server_modules.kill_switch_gate import _KILL_STATE, GLOBAL_KILL_KEY
        _KILL_STATE.clear()

    # ── Global kill ────────────────────────────────────────────────────

    def test_global_kill_blocks_everything(self):
        from server_modules.kill_switch_gate import (
            GLOBAL_KILL_KEY, _KILL_STATE, evaluate_kill_switch,
        )
        _KILL_STATE[GLOBAL_KILL_KEY] = True
        decision = evaluate_kill_switch(
            workspace_id="ws-1", agent_id="agent-1", gateway_id="gw-1",
        )
        self.assertTrue(decision.blocked)
        self.assertEqual(decision.scope, "global")
        self.assertEqual(decision.reason, "global_kill_active")

    def test_global_kill_blocks_with_no_ids(self):
        from server_modules.kill_switch_gate import (
            GLOBAL_KILL_KEY, _KILL_STATE, evaluate_kill_switch,
        )
        _KILL_STATE[GLOBAL_KILL_KEY] = True
        decision = evaluate_kill_switch()
        self.assertTrue(decision.blocked)

    def test_global_kill_cleared_allows(self):
        from server_modules.kill_switch_gate import (
            GLOBAL_KILL_KEY, _KILL_STATE, evaluate_kill_switch,
        )
        _KILL_STATE[GLOBAL_KILL_KEY] = True
        _KILL_STATE[GLOBAL_KILL_KEY] = False
        decision = evaluate_kill_switch(workspace_id="ws-1")
        self.assertFalse(decision.blocked)

    # ── Workspace kill ─────────────────────────────────────────────────

    def test_workspace_kill_blocks_target_workspace(self):
        from server_modules.kill_switch_gate import (
            WORKSPACE_KILL_PREFIX, _KILL_STATE, evaluate_kill_switch,
        )
        _KILL_STATE[f"{WORKSPACE_KILL_PREFIX}ws-target"] = True
        decision = evaluate_kill_switch(workspace_id="ws-target")
        self.assertTrue(decision.blocked)
        self.assertEqual(decision.scope, "workspace")

    def test_workspace_kill_does_not_block_other_workspace(self):
        from server_modules.kill_switch_gate import (
            WORKSPACE_KILL_PREFIX, _KILL_STATE, evaluate_kill_switch,
        )
        _KILL_STATE[f"{WORKSPACE_KILL_PREFIX}ws-target"] = True
        decision = evaluate_kill_switch(workspace_id="ws-other")
        self.assertFalse(decision.blocked)

    # ── Agent kill ─────────────────────────────────────────────────────

    def test_agent_kill_blocks_target_agent(self):
        from server_modules.kill_switch_gate import (
            AGENT_KILL_PREFIX, _KILL_STATE, evaluate_kill_switch,
        )
        _KILL_STATE[f"{AGENT_KILL_PREFIX}agent-target"] = True
        decision = evaluate_kill_switch(workspace_id="ws-1", agent_id="agent-target")
        self.assertTrue(decision.blocked)
        self.assertEqual(decision.scope, "agent")

    def test_agent_kill_does_not_block_other_agent(self):
        from server_modules.kill_switch_gate import (
            AGENT_KILL_PREFIX, _KILL_STATE, evaluate_kill_switch,
        )
        _KILL_STATE[f"{AGENT_KILL_PREFIX}agent-target"] = True
        decision = evaluate_kill_switch(workspace_id="ws-1", agent_id="agent-other")
        self.assertFalse(decision.blocked)

    # ── Gateway kill ───────────────────────────────────────────────────

    def test_gateway_kill_blocks_target_gateway(self):
        from server_modules.kill_switch_gate import (
            GATEWAY_KILL_PREFIX, _KILL_STATE, evaluate_kill_switch,
        )
        _KILL_STATE[f"{GATEWAY_KILL_PREFIX}gw-target"] = True
        decision = evaluate_kill_switch(gateway_id="gw-target")
        self.assertTrue(decision.blocked)
        self.assertEqual(decision.scope, "gateway")

    def test_gateway_kill_does_not_block_other_gateway(self):
        from server_modules.kill_switch_gate import (
            GATEWAY_KILL_PREFIX, _KILL_STATE, evaluate_kill_switch,
        )
        _KILL_STATE[f"{GATEWAY_KILL_PREFIX}gw-target"] = True
        decision = evaluate_kill_switch(gateway_id="gw-other")
        self.assertFalse(decision.blocked)

    # ── assert_not_killed ──────────────────────────────────────────────

    def test_assert_not_killed_raises_when_killed(self):
        from server_modules.kill_switch_gate import (
            GLOBAL_KILL_KEY, _KILL_STATE, KillSwitchBlockedError, assert_not_killed,
        )
        _KILL_STATE[GLOBAL_KILL_KEY] = True
        with self.assertRaises(KillSwitchBlockedError) as ctx:
            assert_not_killed(workspace_id="ws-1")
        self.assertEqual(ctx.exception.decision.scope, "global")

    def test_assert_not_killed_passes_when_clear(self):
        from server_modules.kill_switch_gate import assert_not_killed
        assert_not_killed(workspace_id="ws-1", agent_id="agent-1")

    # ── Priority: global overrides workspace ───────────────────────────

    def test_global_kill_takes_priority_over_workspace(self):
        from server_modules.kill_switch_gate import (
            GLOBAL_KILL_KEY, WORKSPACE_KILL_PREFIX, _KILL_STATE, evaluate_kill_switch,
        )
        _KILL_STATE[GLOBAL_KILL_KEY] = True
        _KILL_STATE[f"{WORKSPACE_KILL_PREFIX}ws-1"] = True
        decision = evaluate_kill_switch(workspace_id="ws-1")
        self.assertTrue(decision.blocked)
        self.assertEqual(decision.scope, "global")

    # ── is_kill_active ─────────────────────────────────────────────────

    def test_is_kill_active_returns_false_for_unknown_key(self):
        from server_modules.kill_switch_gate import is_kill_active
        self.assertFalse(is_kill_active("nonexistent_key"))

    def test_is_kill_active_returns_true_for_active_key(self):
        from server_modules.kill_switch_gate import (
            GLOBAL_KILL_KEY, _KILL_STATE, is_kill_active,
        )
        _KILL_STATE[GLOBAL_KILL_KEY] = True
        self.assertTrue(is_kill_active(GLOBAL_KILL_KEY))

    # ── Scope helper ───────────────────────────────────────────────────

    def test_kill_switch_scope_returns_correct_scopes(self):
        from server_modules.kill_switch_gate import (
            _kill_switch_scope,
            GLOBAL_KILL_KEY,
            WORKSPACE_KILL_PREFIX,
            AGENT_KILL_PREFIX,
            GATEWAY_KILL_PREFIX,
        )
        self.assertEqual(_kill_switch_scope(GLOBAL_KILL_KEY), "global")
        self.assertEqual(_kill_switch_scope(f"{WORKSPACE_KILL_PREFIX}ws-1"), "workspace")
        self.assertEqual(_kill_switch_scope(f"{AGENT_KILL_PREFIX}agent-1"), "agent")
        self.assertEqual(_kill_switch_scope(f"{GATEWAY_KILL_PREFIX}gw-1"), "gateway")
        self.assertEqual(_kill_switch_scope("random_key"), "custom")


if __name__ == "__main__":
    unittest.main()
