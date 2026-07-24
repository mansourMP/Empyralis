"""Sender identity classification tests.

triage_service.py used to also house "Phase P": an LLM scope-classifier
gate (execute_triage_gate / run_scope_check / dispatch_out_of_scope /
resolve_triage_config) that could block an inbound message from ever
reaching the model. That gate — and its dedicated tests below — were
removed per founder ruling (2026-07-23): "Every single message goes to the
reasoning model, absolutely. We are not going to have filters that flag a
message and don't deliver it. No hardcoded outputs — everything is the
agent's own reasoning." See server_modules/tests/test_sage_turn_adapter.py's
TriageRulingTests for the regression proof that inbound messages now always
reach handle_sage_chat, even with a triage-enabled install_metadata.

resolve_sender_identity itself is unaffected by that ruling — it's a plain
classifier (no blocking, no reply substitution) still consumed elsewhere
for tool-visibility/authority-tier decisions.
"""

from __future__ import annotations

import unittest

from server_modules import triage_service


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

    def test_audience_registry_match(self):
        """Sender in the audience registry on an audience-enabled channel → audience."""
        identity = triage_service.resolve_sender_identity(
            sender_id="cust-1",
            channel_origin="telegram",
            audience_sender_ids=["cust-1"],
            audience_enabled=True,
        )
        self.assertEqual(identity, "audience")


if __name__ == "__main__":
    unittest.main()
