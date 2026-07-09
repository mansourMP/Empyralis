"""Tests for capability_presets.py — the 2026-07-09 first-run integrity fix:
a fresh "standard" agent must ship with safe-basics tools already on, not a
blank toolset that silently blocks every request needing a tool."""

from __future__ import annotations

import unittest

from server_modules import capability_presets as caps


class StandardPresetDefaultToolsTests(unittest.TestCase):
    def test_standard_seeds_safe_basics_not_none(self):
        defaults = caps.build_install_defaults("standard")
        self.assertIsNotNone(defaults["enabled_tools"])
        self.assertTrue(defaults["enabled_tools"])

    def test_standard_includes_web_search_and_memory(self):
        defaults = caps.build_install_defaults("standard")
        tools = set(defaults["enabled_tools"])
        self.assertIn("web__search", tools)
        self.assertIn("memory_search", tools)
        self.assertIn("task_complete", tools)

    def test_standard_excludes_hardware_and_write_tools(self):
        defaults = caps.build_install_defaults("standard")
        tools = set(defaults["enabled_tools"])
        for dangerous in ("shell__exec", "file__write", "computer__click", "hardware__action"):
            self.assertNotIn(dangerous, tools)

    def test_standard_hardware_access_still_none(self):
        """Tool defaults changed; hardware policy must not have moved."""
        defaults = caps.build_install_defaults("standard")
        self.assertEqual(defaults["hardware_access"], "none")

    def test_knowledge_and_standard_share_the_same_safe_toolset(self):
        standard = caps.build_install_defaults("standard")
        knowledge = caps.build_install_defaults("knowledge")
        self.assertEqual(set(standard["enabled_tools"]), set(knowledge["enabled_tools"]))

    def test_operator_preset_unaffected(self):
        """Operator (Sage-class) isn't gated by tool_toggles at all — its
        enabled_tools stays None (inherit / not applicable)."""
        defaults = caps.build_install_defaults("operator")
        self.assertIsNone(defaults["enabled_tools"])


if __name__ == "__main__":
    unittest.main()
