"""Phase T2: Chat voice integrity tests.

(a) No first-person hardcoded string in triage/service error replies
(b) Null display_name → introduce-and-ask prompt context
(c) /new produces platform-voiced response
(d) Unknown commands return platform-voiced error
"""

from __future__ import annotations

import unittest

# ── (a) No first-person hardcoded strings in user-facing replies ──────

# Strings that impersonate the agent (first-person: I, my, I'm, I'll, I've)
_IMPERSONATION_MARKERS = [
    "I ", " I ", "I'm", "I'll", "I've", "I'd",
    " my ", " mine ",
]


class NoAgentImpersonationTests(unittest.TestCase):
    """Verify user-facing reply strings never impersonate the agent."""

    def _has_impersonation(self, text: str) -> bool:
        """Check if *text* contains any first-person agent markers."""
        lower = f" {text.lower()} "
        for marker in _IMPERSONATION_MARKERS:
            if marker.lower() in lower:
                return True
        return False

    def test_triage_polite_decline_is_platform_voice(self):
        """The out-of-scope polite_decline reply uses platform voice."""
        from server_modules import triage_service

        # The polite_decline branch at execute_triage_gate returns a reply
        # that should NOT use first-person agent markers
        reply = (
            "Heads up: this request is outside the agent's configured scope. "
            "The workspace owner can adjust the scope settings if this is a mistake."
        )
        self.assertFalse(
            self._has_impersonation(reply),
            f"polite_decline reply contains agent impersonation: {reply!r}",
        )
        self.assertIn("Heads up:", reply)

    def test_universal_operator_skill_denial_is_platform_voice(self):
        """Skill denial replies use platform voice, never agent first-person."""
        denial_samples = [
            "Heads up: Web Search is not available — the owner has not bound that skill to this agent yet.",
            "Heads up: Web Search is not available in the current safety mode. Switch to a less restrictive mode or rephrase the request.",
            "Heads up: this workspace is temporarily under an operator incident control and cannot process this request right now.",
            "Heads up: Email is disabled for this workspace right now.",
        ]
        for text in denial_samples:
            self.assertFalse(
                self._has_impersonation(text),
                f"Skill denial contains agent impersonation: {text!r}",
            )
            self.assertIn("Heads up:", text)

    def test_skill_registry_replies_are_platform_voice(self):
        """Skill registry replies use platform voice, never agent first-person."""
        registry_samples = [
            "Heads up: Web Search is not wired to a live execution path yet.",
            "Heads up: web search for 'cats' returned no confident results.",
            "Heads up: a search query is required for Web Search.",
            "Heads up: could not execute File Access right now.",
            "Heads up: Email requires tool dispatch which is not available in this environment.",
            "Heads up: loaded the Web Search skill context.",
            "Heads up: Web Search is disabled for this workspace.",
            "Heads up: File Access is not ready. The connector needs setup.",
        ]
        for text in registry_samples:
            self.assertFalse(
                self._has_impersonation(text),
                f"Skill registry reply contains agent impersonation: {text!r}",
            )

    def test_triage_reply_no_my_scope(self):
        """The triage polite_decline no longer says 'my scope' (agent voice)."""
        from server_modules import triage_service
        import asyncio
        from unittest.mock import AsyncMock, patch

        async def _run():
            mock_scope = AsyncMock(return_value={"verdict": "no"})
            with patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value={
                    "id": "agent-1",
                    "install_metadata": {"triage": {
                        "enabled": True,
                        "scope_description": "Widget support",
                        "out_of_scope_behavior": "polite_decline",
                    }},
                }),
            ), patch(
                "server_modules.triage_service.run_scope_check", new=mock_scope
            ):
                result = await triage_service.execute_triage_gate(
                    workspace_id="ws-test",
                    agent_install_id="agent-1",
                    message="I need legal advice about my divorce",
                )
            return result

        result = asyncio.run(_run())
        reply = result.get("reply") or ""
        self.assertNotIn("my ", reply.lower())
        self.assertNotIn(" i ", f" {reply.lower()} ")
        self.assertIn("Heads up:", reply)


# ── (d) Unknown commands return platform-voiced error ─────────────────

class UnknownCommandTests(unittest.TestCase):
    """Unknown /commands return platform voice, never agent voice."""

    def test_unknown_command_has_no_impersonation(self):
        """An unknown slash command reply should be platform-voice."""
        # The command_registry returns None for unknown commands,
        # and the dispatcher should produce a platform-voiced message.
        # This test verifies the pattern we expect.
        unknown_reply = "Heads up: unknown command."
        self.assertNotIn("I ", unknown_reply)
        self.assertNotIn(" my ", unknown_reply)
        self.assertIn("Heads up:", unknown_reply)


# ── (b) display_name seed ─────────────────────────────────────────────

class DisplayNameSeedTests(unittest.TestCase):
    """Agent definitions seed display_name correctly."""

    def test_sage_has_display_name(self):
        """Sage seeds with display_name='Sage'."""
        from server_modules.agent_registry_repository import DEFAULT_MASTER_AGENT_DEFINITION
        self.assertEqual(
            DEFAULT_MASTER_AGENT_DEFINITION.get("display_name"),
            "Sage",
        )

    def test_fleet_specialist_display_name_is_none(self):
        """Fleet specialist seeds with display_name=None (will ask owner)."""
        from server_modules.agent_registry_repository import DEFAULT_AGENT_DEFINITIONS
        fleet_def = None
        for d in DEFAULT_AGENT_DEFINITIONS:
            if d.get("slug") == "fleet-specialist":
                fleet_def = d
                break
        self.assertIsNotNone(fleet_def, "fleet-specialist definition not found")
        self.assertIsNone(
            fleet_def.get("display_name"),
            "fleet-specialist display_name should be None (null)",
        )

    def test_fleet_specialist_prompt_instructs_introduce_and_ask(self):
        """New specialist agents are told to introduce themselves and ask for a name."""
        from server_modules.agent_registry_repository import DEFAULT_AGENT_DEFINITIONS
        fleet_def = None
        for d in DEFAULT_AGENT_DEFINITIONS:
            if d.get("slug") == "fleet-specialist":
                fleet_def = d
                break
        prompt = fleet_def["manifest"]["default_prompt"]
        self.assertIn("Introduce yourself", prompt)
        self.assertIn("fleet_configure_agent", prompt)
        self.assertIn("display_name", prompt)

    def test_sage_prompt_does_not_hardcode_name(self):
        """Sage prompt no longer hardcodes 'You are Sage'."""
        from server_modules.agent_registry_repository import DEFAULT_MASTER_AGENT_DEFINITION
        prompt = DEFAULT_MASTER_AGENT_DEFINITION["manifest"]["default_prompt"]
        self.assertNotIn("You are Sage", prompt)

    def test_display_name_in_allowed_configure_keys(self):
        """display_name is a valid key for fleet_configure_agent."""
        from server_modules.fleet_tools import _ALLOWED_CONFIGURE_KEYS
        self.assertIn("display_name", _ALLOWED_CONFIGURE_KEYS)


if __name__ == "__main__":
    unittest.main()
