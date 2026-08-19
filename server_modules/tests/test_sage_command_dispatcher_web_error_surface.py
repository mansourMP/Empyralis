"""Tests for sage_command_dispatcher.classify_error's is_web branch.

Found live 2026-08-19 while walking a fresh signup: the web "Ask AI" chat
(direct_chat_service.py's streaming error path) rendered SAGE_NO_PROVIDER_
REPLY's channel-voice text ("Your agent needs an AI provider. Connect one
→") on a surface that renders plain text, not links — a trailing arrow with
no click target, which is CLAUDE.md's "No dead controls" law. The web-voice
sibling (SAGE_NO_PROVIDER_MESSAGE, built for exactly this) had zero callers
anywhere in the codebase — "built, tested, and never wired" one level up
from a whole feature, at the level of a single kwarg nobody threaded.

These tests assert classify_error's default behavior (every existing
Telegram/WhatsApp/etc caller, none of which pass is_web) is BYTE-FOR-BYTE
unchanged, and that passing is_web=True selects the plain-text web variant
for exactly the three buckets that have one.
"""

from __future__ import annotations

import unittest

from server_modules import sage_command_dispatcher as scd


class ClassifyErrorIsWebDefaultUnchangedTests(unittest.TestCase):
    """Every caller that predates is_web must see identical behavior."""

    def test_no_provider_defaults_to_channel_text(self):
        self.assertEqual(
            scd.classify_error("no cloud provider is configured"),
            scd.SAGE_NO_PROVIDER_REPLY,
        )

    def test_ai_limit_defaults_to_channel_text(self):
        self.assertEqual(
            scd.classify_error("reached your ai limit"),
            scd.SAGE_AI_LIMIT_REPLY,
        )

    def test_auth_failed_defaults_to_channel_text(self):
        self.assertEqual(
            scd.classify_error("401 unauthorized", is_platform_credits=False),
            scd.SAGE_AI_NEEDS_ATTENTION_REPLY,
        )

    def test_is_web_false_explicit_matches_default(self):
        self.assertEqual(
            scd.classify_error("no cloud provider is configured", is_web=False),
            scd.classify_error("no cloud provider is configured"),
        )


class ClassifyErrorIsWebSelectsPlainTextVariantTests(unittest.TestCase):
    """is_web=True must select the SAGE_*_MESSAGE constants, not _REPLY."""

    def test_no_provider_web_variant(self):
        result = scd.classify_error("no cloud provider is configured", is_web=True)
        self.assertEqual(result, scd.SAGE_NO_PROVIDER_MESSAGE)
        self.assertNotEqual(result, scd.SAGE_NO_PROVIDER_REPLY)
        # The whole point: no trailing arrow implying a click this plain-
        # text chat surface cannot honor.
        self.assertFalse(result.rstrip().endswith("→"))

    def test_ai_limit_web_variant(self):
        result = scd.classify_error("reached your ai limit", is_web=True)
        self.assertEqual(result, scd.SAGE_AI_LIMIT_MESSAGE)
        self.assertNotEqual(result, scd.SAGE_AI_LIMIT_REPLY)

    def test_auth_failed_web_variant_ignores_platform_credits_split(self):
        # The web variant has no platform/BYOK split (only one _MESSAGE
        # constant exists) — is_web wins regardless of is_platform_credits.
        for platform_credits in (True, False):
            result = scd.classify_error(
                "401 unauthorized", is_platform_credits=platform_credits, is_web=True
            )
            self.assertEqual(result, scd.SAGE_AI_NEEDS_ATTENTION_MESSAGE)

    def test_buckets_without_a_web_variant_are_unaffected_by_is_web(self):
        # Bucket 3 (rate limited) has no _MESSAGE constant — is_web=True
        # must not change its output.
        self.assertEqual(
            scd.classify_error("429 too many requests", is_web=True),
            scd.SAGE_RATE_LIMITED_REPLY,
        )


if __name__ == "__main__":
    unittest.main()
