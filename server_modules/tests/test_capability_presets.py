"""Tests for capability_presets.py.

2026-08-14 (CLAUDE.md, founder decision): there is no more per-agent Tools
enable/disable checklist, so presets no longer seed an `enabled_tools`
restriction at all — every agent, regardless of preset, gets core tools plus
everything with no third-party integration behind it unconditionally (see
agent_turn_runtime_service._UNGATED_JUDGMENT_TOOL_NAMES), plus anything
backed by a real connector binding or resolved capability. This file used to
assert the OPPOSITE (a fresh "standard" agent ships with only a safe-basics
allowlist, shell/file-write/computer-click/hardware-action excluded) — that
was the checklist itself, and is exactly what got removed. What remains a
real, preserved boundary is hardware_access, asserted below."""

from __future__ import annotations

import unittest

from server_modules import capability_presets as caps


class PresetToolRestrictionRemovedTests(unittest.TestCase):
    def test_no_preset_returns_an_enabled_tools_key(self):
        """The whole point of this change: nothing here restricts an
        agent's toolset anymore. A stray `enabled_tools` key coming back
        would be exactly the kind of stored-but-inert field CLAUDE.md warns
        against ("no dead controls")."""
        for preset_id in ("standard", "knowledge", "operator"):
            defaults = caps.build_install_defaults(preset_id)
            self.assertNotIn("enabled_tools", defaults, preset_id)


class HardwareAccessStillEnforcedTests(unittest.TestCase):
    """The real, preserved boundary. Tool restriction is gone; hardware
    access scoping is not — this is exactly the distinction CLAUDE.md draws
    between a removed WHAT-checklist and a kept WHO/WHERE boundary."""

    def test_standard_hardware_access_none_and_unlocked(self):
        defaults = caps.build_install_defaults("standard")
        self.assertEqual(defaults["hardware_access"], "none")
        self.assertFalse(defaults["policy_context_overrides"].get("hardware_access_locked"))

    def test_knowledge_hardware_access_none_and_locked(self):
        defaults = caps.build_install_defaults("knowledge")
        self.assertEqual(defaults["hardware_access"], "none")
        self.assertTrue(defaults["policy_context_overrides"].get("hardware_access_locked"))
        self.assertEqual(defaults["policy_context_overrides"].get("hardware_access"), "none")

    def test_operator_hardware_access_all(self):
        defaults = caps.build_install_defaults("operator")
        self.assertEqual(defaults["hardware_access"], "all")


if __name__ == "__main__":
    unittest.main()
