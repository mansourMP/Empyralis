"""Authority mandate — tier derivation, inheritance, and the enforcement rule.

Tests:
  (a) derive_tier_from_sender_class: owner -> owner; audience/unknown/garbage -> audience
  (b) derive_tier_from_owner_flag: True -> owner; False -> audience
  (c) normalize_tier / inherit_tier: valid values pass through; invalid/missing -> audience
  (d) is_tool_call_allowed: owner always allowed; non-owner only when audience_safe
"""

from __future__ import annotations

import unittest

from server_modules import authority_mandate_service


class DeriveTierFromSenderClassTests(unittest.TestCase):

    def test_owner_sender_class_is_owner_tier(self):
        self.assertEqual(
            authority_mandate_service.derive_tier_from_sender_class("owner"),
            authority_mandate_service.TIER_OWNER,
        )

    def test_owner_is_case_and_whitespace_insensitive(self):
        self.assertEqual(
            authority_mandate_service.derive_tier_from_sender_class("  Owner \n"),
            authority_mandate_service.TIER_OWNER,
        )

    def test_audience_sender_class_is_audience_tier(self):
        self.assertEqual(
            authority_mandate_service.derive_tier_from_sender_class("audience"),
            authority_mandate_service.TIER_AUDIENCE,
        )

    def test_unknown_sender_class_fails_safe_to_audience(self):
        """triage_service.resolve_sender_identity()'s "unknown" must collapse to audience, never owner."""
        self.assertEqual(
            authority_mandate_service.derive_tier_from_sender_class("unknown"),
            authority_mandate_service.TIER_AUDIENCE,
        )

    def test_empty_or_garbage_input_fails_safe_to_audience(self):
        for garbage in ("", None, "admin", "SUPERUSER", "  "):
            with self.subTest(garbage=garbage):
                self.assertEqual(
                    authority_mandate_service.derive_tier_from_sender_class(garbage),
                    authority_mandate_service.TIER_AUDIENCE,
                )


class DeriveTierFromOwnerFlagTests(unittest.TestCase):

    def test_true_is_owner(self):
        self.assertEqual(
            authority_mandate_service.derive_tier_from_owner_flag(True),
            authority_mandate_service.TIER_OWNER,
        )

    def test_false_is_audience(self):
        self.assertEqual(
            authority_mandate_service.derive_tier_from_owner_flag(False),
            authority_mandate_service.TIER_AUDIENCE,
        )


class NormalizeAndInheritTierTests(unittest.TestCase):

    def test_valid_tiers_pass_through(self):
        for tier in authority_mandate_service.VALID_TIERS:
            with self.subTest(tier=tier):
                self.assertEqual(authority_mandate_service.normalize_tier(tier), tier)
                self.assertEqual(authority_mandate_service.inherit_tier(tier), tier)

    def test_missing_or_invalid_value_fails_safe_to_audience(self):
        for value in (None, "", "made_up_tier", 42, {}):
            with self.subTest(value=value):
                self.assertEqual(
                    authority_mandate_service.normalize_tier(value),
                    authority_mandate_service.TIER_AUDIENCE,
                )
                self.assertEqual(
                    authority_mandate_service.inherit_tier(value),
                    authority_mandate_service.TIER_AUDIENCE,
                )

    def test_inherit_tier_never_upgrades_audience_to_owner(self):
        """The core anti-laundering invariant: a schedule/run/delegation spawned
        from an audience-tier turn must carry audience tier permanently."""
        self.assertEqual(
            authority_mandate_service.inherit_tier(authority_mandate_service.TIER_AUDIENCE),
            authority_mandate_service.TIER_AUDIENCE,
        )


class IsToolCallAllowedTests(unittest.TestCase):

    def test_owner_allowed_regardless_of_audience_safe(self):
        self.assertTrue(
            authority_mandate_service.is_tool_call_allowed("owner", audience_safe=False)
        )
        self.assertTrue(
            authority_mandate_service.is_tool_call_allowed("owner", audience_safe=True)
        )

    def test_audience_allowed_only_when_tool_is_audience_safe(self):
        self.assertTrue(
            authority_mandate_service.is_tool_call_allowed("audience", audience_safe=True)
        )
        self.assertFalse(
            authority_mandate_service.is_tool_call_allowed("audience", audience_safe=False)
        )

    def test_system_tier_is_restricted_same_as_audience(self):
        """system is a provenance label, not an elevated tier — only "owner" bypasses."""
        self.assertFalse(
            authority_mandate_service.is_tool_call_allowed("system", audience_safe=False)
        )
        self.assertTrue(
            authority_mandate_service.is_tool_call_allowed("system", audience_safe=True)
        )

    def test_malformed_tier_value_fails_closed(self):
        self.assertFalse(
            authority_mandate_service.is_tool_call_allowed("owner_typo", audience_safe=False)
        )


if __name__ == "__main__":
    unittest.main()
