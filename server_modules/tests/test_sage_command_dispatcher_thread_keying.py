from __future__ import annotations

import unittest

from server_modules.agent_command_dispatcher import agent_sender_thread_id


class AgentSenderThreadIdTests(unittest.TestCase):
    """Pure-function coverage for the per-(agent, sender) thread key —
    see agent_turn_adapter.execute_sage_turn's thread-resolution branch for
    where this replaces the legacy get_active_thread lookup for a resolved
    specialist turn."""

    def test_deterministic_for_same_inputs(self):
        first = agent_sender_thread_id("ainstall_1", "sender_1")
        second = agent_sender_thread_id("ainstall_1", "sender_1")
        self.assertEqual(first, second)

    def test_different_agent_same_sender_differ(self):
        a = agent_sender_thread_id("ainstall_1", "sender_1")
        b = agent_sender_thread_id("ainstall_2", "sender_1")
        self.assertNotEqual(a, b)

    def test_same_agent_different_sender_differ(self):
        a = agent_sender_thread_id("ainstall_1", "sender_1")
        b = agent_sender_thread_id("ainstall_1", "sender_2")
        self.assertNotEqual(a, b)

    def test_contains_both_components_not_collapsible(self):
        # Guards against a naive f"{a}{b}" concat where ("ab", "c") and
        # ("a", "bc") would collide.
        first = agent_sender_thread_id("ab", "c")
        second = agent_sender_thread_id("a", "bc")
        self.assertNotEqual(first, second)

    def test_empty_sender_id_falls_back_to_a_per_agent_bucket_not_legacy(self):
        result = agent_sender_thread_id("ainstall_1", "")
        self.assertIn("ainstall_1", result)
        self.assertNotEqual(result, "sage-main")

    def test_whitespace_is_stripped(self):
        self.assertEqual(
            agent_sender_thread_id("  ainstall_1  ", "  sender_1  "),
            agent_sender_thread_id("ainstall_1", "sender_1"),
        )


if __name__ == "__main__":
    unittest.main()
