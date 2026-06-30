from __future__ import annotations

import unittest

from server_modules.kill_switch_gate import (
    KillSwitchDecision,
    KillSwitchBlockedError,
    set_kill_switch,
    clear_kill_switch,
    is_kill_active,
    evaluate_kill_switch,
    assert_not_killed,
    GLOBAL_KILL_KEY,
    WORKSPACE_KILL_PREFIX,
    AGENT_KILL_PREFIX,
    GATEWAY_KILL_PREFIX,
)


class KillSwitchGateTests(unittest.TestCase):
    def setUp(self):
        clear_kill_switch(GLOBAL_KILL_KEY)
        clear_kill_switch(f"{WORKSPACE_KILL_PREFIX}ws1")
        clear_kill_switch(f"{AGENT_KILL_PREFIX}a1")
        clear_kill_switch(f"{GATEWAY_KILL_PREFIX}gw1")

    def test_is_kill_active_returns_false_when_not_set(self):
        self.assertFalse(is_kill_active(GLOBAL_KILL_KEY))

    def test_is_kill_active_returns_true_after_set(self):
        set_kill_switch(GLOBAL_KILL_KEY)
        self.assertTrue(is_kill_active(GLOBAL_KILL_KEY))

    def test_clear_kill_switch_removes_key(self):
        set_kill_switch(GLOBAL_KILL_KEY)
        clear_kill_switch(GLOBAL_KILL_KEY)
        self.assertFalse(is_kill_active(GLOBAL_KILL_KEY))

    def test_evaluate_returns_not_blocked_by_default(self):
        decision = evaluate_kill_switch()
        self.assertFalse(decision.blocked)

    def test_evaluate_blocks_when_global_kill_active(self):
        set_kill_switch(GLOBAL_KILL_KEY)
        decision = evaluate_kill_switch()
        self.assertTrue(decision.blocked)
        self.assertEqual(decision.reason, "global_kill_active")
        self.assertEqual(decision.scope, "global")

    def test_evaluate_blocks_when_agent_kill_active(self):
        set_kill_switch(f"{AGENT_KILL_PREFIX}a1")
        decision = evaluate_kill_switch(agent_id="a1")
        self.assertTrue(decision.blocked)
        self.assertEqual(decision.reason, "agent_kill_active")
        self.assertEqual(decision.scope, "agent")

    def test_evaluate_blocks_when_workspace_kill_active(self):
        set_kill_switch(f"{WORKSPACE_KILL_PREFIX}ws1")
        decision = evaluate_kill_switch(workspace_id="ws1", agent_id="a1")
        self.assertTrue(decision.blocked)
        self.assertEqual(decision.reason, "workspace_kill_active")
        self.assertEqual(decision.scope, "workspace")

    def test_agent_kill_does_not_block_other_agents(self):
        set_kill_switch(f"{AGENT_KILL_PREFIX}a1")
        decision = evaluate_kill_switch(agent_id="a2")
        self.assertFalse(decision.blocked)

    def test_evaluate_blocks_when_gateway_kill_active(self):
        set_kill_switch(f"{GATEWAY_KILL_PREFIX}gw1")
        decision = evaluate_kill_switch(gateway_id="gw1")
        self.assertTrue(decision.blocked)
        self.assertEqual(decision.reason, "gateway_kill_active")
        self.assertEqual(decision.scope, "gateway")

    def test_global_kill_takes_precedence(self):
        set_kill_switch(GLOBAL_KILL_KEY)
        set_kill_switch(f"{AGENT_KILL_PREFIX}a1")
        decision = evaluate_kill_switch(agent_id="a1")
        self.assertTrue(decision.blocked)
        self.assertEqual(decision.reason, "global_kill_active")

    def test_assert_not_killed_raises_when_killed(self):
        set_kill_switch(GLOBAL_KILL_KEY)
        with self.assertRaises(KillSwitchBlockedError) as ctx:
            assert_not_killed()
        self.assertIn("emergency stop", str(ctx.exception))

    def test_assert_not_killed_passes_when_clear(self):
        assert_not_killed()

    def test_kill_switch_decision_is_immutable(self):
        decision = KillSwitchDecision(blocked=True, reason="test", scope="test")
        self.assertTrue(decision.blocked)
        self.assertEqual(decision.reason, "test")


if __name__ == "__main__":
    unittest.main()
