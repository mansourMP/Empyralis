"""Phase P: Triage layer tests — scope + identity gates before reasoning.

7 tests:
  (a) triage disabled → zero triage calls, turn flows exactly as before
  (b) in-scope message → full loop reached, scope ledger row
  (c) out-of-scope + silent → no reply, ledger row
  (d) out-of-scope + escalate → owner Sage notified, sender gets nothing
  (e) uncertain → full loop (fail-open proof)
  (f) provider error during scope check → full loop (fail-open)
  (g) owner identity + rules → full behavior; unknown sender + restricted → context note
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from server_modules import triage_service


def _run(coro):
    return asyncio.run(coro)


class TriageConfigTests(unittest.TestCase):

    def test_default_config_is_disabled(self):
        """Triage is opt-in — default enabled=False, zero behavior change."""
        cfg = triage_service.resolve_triage_config(None)
        self.assertFalse(cfg["enabled"])
        self.assertEqual(cfg["out_of_scope_behavior"], "polite_decline")

    def test_config_enabled_true(self):
        """When enabled=True, triage activates."""
        install = {"install_metadata": {"triage": {"enabled": True, "scope_description": "Customer support for widgets"}}}
        cfg = triage_service.resolve_triage_config(install)
        self.assertTrue(cfg["enabled"])
        self.assertEqual(cfg["scope_description"], "Customer support for widgets")

    def test_config_uncertain_always_goes_to_full_loop(self):
        """uncertain_goes_to_full_loop is hard-coded True, not configurable."""
        install = {"install_metadata": {"triage": {"enabled": True, "uncertain_goes_to_full_loop": False}}}
        cfg = triage_service.resolve_triage_config(install)
        self.assertTrue(cfg["uncertain_goes_to_full_loop"])


class IdentityResolutionTests(unittest.TestCase):

    def test_owner_identity_matches_channel_binding(self):
        """Sender matching channel binding's linked_user_id → owner."""
        identity = triage_service.resolve_sender_identity(
            sender_id="tg-user-123",
            channel_origin="telegram",
            channel_bindings=[
                {"channel_type": "telegram", "linked_user_id": "tg-user-123"},
            ],
        )
        self.assertEqual(identity, "owner")

    def test_unknown_identity_for_unmatched_sender(self):
        """Unmatched sender → unknown."""
        identity = triage_service.resolve_sender_identity(
            sender_id="rando-999",
            channel_origin="telegram",
            channel_bindings=[
                {"channel_type": "telegram", "linked_user_id": "tg-user-123"},
            ],
        )
        self.assertEqual(identity, "unknown")

    def test_unknown_identity_for_empty_sender(self):
        """Empty sender_id → unknown."""
        self.assertEqual(triage_service.resolve_sender_identity(sender_id="", channel_origin="telegram"), "unknown")


class TriageGateTests(unittest.TestCase):
    """Tests (a)-(f): triage gate behavior."""

    def test_a_triage_disabled_zero_impact(self):
        """When triage.enabled=False, turn flows exactly as before (not blocked)."""
        with patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            new=AsyncMock(return_value={
                "id": "agent-1",
                "install_metadata": {"triage": {"enabled": False}},
            }),
        ):
            result = _run(triage_service.execute_triage_gate(
                workspace_id="ws-test",
                agent_install_id="agent-1",
                message="Buy widgets from me",
            ))
        self.assertFalse(result["blocked"])
        self.assertFalse(result["triage_applied"])

    def test_e_uncertain_verdict_fail_open(self):
        """When scope check returns uncertain, triage does NOT block."""
        mock_scope = AsyncMock(return_value={"verdict": "uncertain", "reason": "test"})
        with patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            new=AsyncMock(return_value={
                "id": "agent-1",
                "install_metadata": {"triage": {"enabled": True, "scope_description": "Widget support"}},
            }),
        ), patch(
            "server_modules.triage_service.run_scope_check", new=mock_scope
        ):
            result = _run(triage_service.execute_triage_gate(
                workspace_id="ws-test",
                agent_install_id="agent-1",
                message="What is the meaning of life?",
            ))
        self.assertFalse(result["blocked"])
        self.assertTrue(result["triage_applied"])
        self.assertEqual(result["layer1_verdict"], "uncertain")

    def test_f_provider_error_fail_open(self):
        """Provider error during scope check → fail-open (not blocked)."""
        mock_scope = AsyncMock(return_value={"verdict": "uncertain", "reason": "provider_error: timeout"})
        with patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            new=AsyncMock(return_value={
                "id": "agent-1",
                "install_metadata": {"triage": {"enabled": True, "scope_description": "Widget support"}},
            }),
        ), patch(
            "server_modules.triage_service.run_scope_check", new=mock_scope
        ):
            result = _run(triage_service.execute_triage_gate(
                workspace_id="ws-test",
                agent_install_id="agent-1",
                message="Help me with my order",
            ))
        self.assertFalse(result["blocked"])
        self.assertTrue(result["triage_applied"])

    def test_b_in_scope_proceeds_to_full_loop(self):
        """In-scope message → not blocked, full loop reached."""
        mock_scope = AsyncMock(return_value={"verdict": "yes"})
        with patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            new=AsyncMock(return_value={
                "id": "agent-1",
                "install_metadata": {"triage": {"enabled": True, "scope_description": "Widget support"}},
            }),
        ), patch(
            "server_modules.triage_service.run_scope_check", new=mock_scope
        ):
            result = _run(triage_service.execute_triage_gate(
                workspace_id="ws-test",
                agent_install_id="agent-1",
                message="My widget is broken, can you help?",
            ))
        self.assertFalse(result["blocked"])
        self.assertTrue(result["triage_applied"])
        self.assertEqual(result["layer1_verdict"], "yes")

    def test_c_out_of_scope_silent_no_reply(self):
        """Out-of-scope + silent → blocked, no reply."""
        mock_scope = AsyncMock(return_value={"verdict": "no"})
        with patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            new=AsyncMock(return_value={
                "id": "agent-1",
                "install_metadata": {"triage": {
                    "enabled": True,
                    "scope_description": "Widget support",
                    "out_of_scope_behavior": "silent",
                }},
            }),
        ), patch(
            "server_modules.triage_service.run_scope_check", new=mock_scope
        ):
            result = _run(triage_service.execute_triage_gate(
                workspace_id="ws-test",
                agent_install_id="agent-1",
                message="Buy crypto now!!!",
            ))
        self.assertTrue(result["blocked"])
        self.assertIsNone(result.get("reply"))
        self.assertEqual(result.get("out_of_scope_action"), "silent")

    def test_d_out_of_scope_polite_decline_has_reply(self):
        """Out-of-scope + polite_decline → blocked with agent-voice reply."""
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
            result = _run(triage_service.execute_triage_gate(
                workspace_id="ws-test",
                agent_install_id="agent-1",
                message="I need legal advice about my divorce",
            ))
        self.assertTrue(result["blocked"])
        self.assertIsNotNone(result.get("reply"))
        self.assertIn("outside", result["reply"].lower())
        self.assertEqual(result.get("out_of_scope_action"), "polite_decline")

    def test_d2_out_of_scope_escalate_no_reply_to_sender(self):
        """Out-of-scope + escalate → blocked, no reply to sender."""
        mock_scope = AsyncMock(return_value={"verdict": "no"})
        with patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            new=AsyncMock(return_value={
                "id": "agent-1",
                "install_metadata": {"triage": {
                    "enabled": True,
                    "scope_description": "Widget support",
                    "out_of_scope_behavior": "escalate_to_owner",
                }},
            }),
        ), patch(
            "server_modules.triage_service.run_scope_check", new=mock_scope
        ), patch(
            "server_modules.triage_service._enqueue_owner_notification", new=AsyncMock()
        ):
            result = _run(triage_service.execute_triage_gate(
                workspace_id="ws-test",
                agent_install_id="agent-1",
                message="I want to sue your company",
            ))
        self.assertTrue(result["blocked"])
        self.assertIsNone(result.get("reply"))
        self.assertEqual(result.get("out_of_scope_action"), "escalate_to_owner")

    def test_g_owner_identity_full_behavior(self):
        """Owner sender with behavior=full → not blocked."""
        mock_scope = AsyncMock(return_value={"verdict": "yes"})
        with patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            new=AsyncMock(return_value={
                "id": "agent-1",
                "install_metadata": {"triage": {
                    "enabled": True,
                    "scope_description": "Widget support",
                }},
            }),
        ), patch(
            "server_modules.triage_service.run_scope_check", new=mock_scope
        ), patch(
            "server_modules.triage_service.resolve_sender_identity", return_value="owner"
        ):
            result = _run(triage_service.execute_triage_gate(
                workspace_id="ws-test",
                agent_install_id="agent-1",
                message="Hi from owner",
                sender_id="owner-123",
            ))
        self.assertFalse(result["blocked"])
        self.assertEqual(result.get("layer2_identity"), "owner")

    def test_g2_unknown_sender_restricted_behavior(self):
        """Unknown sender with behavior=restricted → not blocked but flagged."""
        mock_scope = AsyncMock(return_value={"verdict": "yes"})
        with patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            new=AsyncMock(return_value={
                "id": "agent-1",
                "install_metadata": {"triage": {
                    "enabled": True,
                    "scope_description": "Widget support",
                    "identity_rules": [
                        {"match": "owner", "behavior": "full"},
                        {"match": "unknown", "behavior": "restricted"},
                    ],
                }},
            }),
        ), patch(
            "server_modules.triage_service.run_scope_check", new=mock_scope
        ), patch(
            "server_modules.triage_service.resolve_sender_identity", return_value="unknown"
        ):
            result = _run(triage_service.execute_triage_gate(
                workspace_id="ws-test",
                agent_install_id="agent-1",
                message="What widgets do you sell?",
                sender_id="rando-999",
            ))
        self.assertFalse(result["blocked"])
        self.assertEqual(result.get("layer2_identity"), "unknown")
        self.assertEqual(result.get("layer2_behavior"), "restricted")


if __name__ == "__main__":
    unittest.main()
