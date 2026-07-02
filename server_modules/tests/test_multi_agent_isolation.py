"""Stage 4B: Multi-agent isolation tests.

Tests for per-agent credentials, channel bindings, and tool gating.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from server_modules.tests.fake_activity_ledger_repo import FakeActivityLedgerRepo


class MultiAgentCredentialIsolationTests(unittest.TestCase):
    """Two agents in one workspace, each with a distinct credential.

    Verifies that vault resolution returns each agent's own token
    and never falls back silently to another agent's credential.
    """

    def setUp(self) -> None:
        self.fake_ledger = FakeActivityLedgerRepo()

    # ------------------------------------------------------------------
    # Credential isolation
    # ------------------------------------------------------------------

    def test_two_agents_distinct_github_credentials(self) -> None:
        """Agent A and Agent B each get their own GitHub token."""
        from server_modules.vault_helpers import resolve_agent_credential

        vault = {
            "version": 1,
            "credentials": [
                {
                    "id": "cred-agent-a",
                    "provider": "github",
                    "workspace_id": "workspace-1",
                    "agent_id": "agent-a",
                    "account_label": "default",
                    "label": "Agent A GitHub",
                    "mode": "byok",
                    "created_at": "2026-07-01T00:00:00Z",
                    "updated_at": "2026-07-01T00:00:00Z",
                    "encrypted_secret": "mock-encrypted-token-a",
                },
                {
                    "id": "cred-agent-b",
                    "provider": "github",
                    "workspace_id": "workspace-1",
                    "agent_id": "agent-b",
                    "account_label": "default",
                    "label": "Agent B GitHub",
                    "mode": "byok",
                    "created_at": "2026-07-01T00:00:00Z",
                    "updated_at": "2026-07-01T00:00:00Z",
                    "encrypted_secret": "mock-encrypted-token-b",
                },
            ],
        }

        def load_vault():
            return vault

        def decrypt(encrypted: str) -> str:
            return f'{{"token":"decrypted-{encrypted}"}}'

        # Agent A resolves its own credential
        result_a = resolve_agent_credential(
            load_vault,
            decrypt,
            provider="github",
            workspace_id="workspace-1",
            agent_id="agent-a",
        )
        self.assertEqual(result_a["token"], "decrypted-mock-encrypted-token-a")

        # Agent B resolves its own credential
        result_b = resolve_agent_credential(
            load_vault,
            decrypt,
            provider="github",
            workspace_id="workspace-1",
            agent_id="agent-b",
        )
        self.assertEqual(result_b["token"], "decrypted-mock-encrypted-token-b")

    def test_agent_without_credential_raises_no_silent_fallback(self) -> None:
        """Agent C has no GitHub credential — must raise, not fall back silently."""
        from server_modules.vault_helpers import resolve_agent_credential

        vault = {
            "version": 1,
            "credentials": [
                {
                    "id": "cred-agent-a",
                    "provider": "github",
                    "workspace_id": "workspace-1",
                    "agent_id": "agent-a",
                    "account_label": "default",
                    "label": "Agent A GitHub",
                    "mode": "byok",
                    "created_at": "2026-07-01T00:00:00Z",
                    "updated_at": "2026-07-01T00:00:00Z",
                    "encrypted_secret": "mock-encrypted",
                },
            ],
        }

        def load_vault():
            return vault

        def decrypt(encrypted: str) -> str:
            return f'{{"token":"decrypted-{encrypted}"}}'

        with self.assertRaises(RuntimeError) as ctx:
            resolve_agent_credential(
                load_vault,
                decrypt,
                provider="github",
                workspace_id="workspace-1",
                agent_id="agent-c",  # no credential
            )
        self.assertIn("no silent fallback", str(ctx.exception).lower())

    # ------------------------------------------------------------------
    # Channel routing
    # ------------------------------------------------------------------

    def test_inbound_telegram_routes_to_correct_agent(self) -> None:
        """Bot A routes to agent A; Bot B routes to agent B."""
        # TODO(Phase F): implement when agent_channel_router supports
        # per-agent channel_bindings lookup.
        #
        # Expected:
        #   channel_bindings = [
        #     {"channel_type": "telegram_personal", "bot_token_hash": "bot-a-hash"},
        #   ]
        #   inbound(telegram_personal, bot_token_hash="bot-a-hash")
        #     → routes to agent A
        #   inbound(telegram_personal, bot_token_hash="bot-b-hash")
        #     → routes to agent B
        #   inbound(telegram_personal, bot_token_hash="unknown")
        #     → routes to Sage (fallback)
        pass

    def test_unknown_bot_routes_to_sage_fallback(self) -> None:
        """Unmapped bot falls back to Sage in that workspace."""
        # TODO(Phase F): implement when agent_channel_router has
        # Sage fallback for unmatched channel_bindings.
        pass

    # ------------------------------------------------------------------
    # Tool gating
    # ------------------------------------------------------------------

    def test_agent_without_linear_connector_cannot_invoke_it(self) -> None:
        """Agent missing 'linear' from enabled_connectors is denied."""
        # TODO(Phase F): implement when tool_broker enforces
        # enabled_connectors from agent registry.
        #
        # Expected:
        #   agent.install.enabled_connectors = ['github', 'slack']  # no 'linear'
        #   agent calls linear__create_issue → denial + ledger record
        pass

    def test_denial_records_ledger_event(self) -> None:
        """A blocked connector invocation writes a denial ledger record."""
        # TODO(Phase F): implement when tool_broker ledger-writes
        # denials with agent_id, tool, status='denied', ts.
        pass

    # ------------------------------------------------------------------
    # Hardware access gating
    # ------------------------------------------------------------------

    def test_specialist_agent_hardware_access_none_blocks_gateway(self) -> None:
        """Agent with hardware_access='none' cannot use gateway tools."""
        # TODO(Phase F): implement when hardware_access is enforced
        # in tool_broker or hardware_action_broker_service.
        pass

    def test_sage_agent_hardware_access_all_allows_gateway(self) -> None:
        """Sage (master) with hardware_access='all' can use gateway."""
        # TODO(Phase F): implement when hardware_access is enforced.
        pass


if __name__ == "__main__":
    unittest.main()
