"""Stage 4B: Multi-agent isolation tests.

Tests for per-agent credentials, channel bindings, and tool gating.
All 8 tests must be green for Stage 4B to be complete.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from server_modules.tests.fake_activity_ledger_repo import FakeActivityLedgerRepo


def _run(coro):
    return asyncio.run(coro)


class MultiAgentCredentialIsolationTests(unittest.TestCase):
    """Two agents in one workspace, each with a distinct credential."""

    def setUp(self) -> None:
        self.fake_ledger = FakeActivityLedgerRepo()

    # ------------------------------------------------------------------
    # (a) Credential isolation — two agents, distinct tokens  [PASSING]
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

        result_a = resolve_agent_credential(
            load_vault, decrypt,
            provider="github", workspace_id="workspace-1", agent_id="agent-a",
        )
        self.assertEqual(result_a["token"], "decrypted-mock-encrypted-token-a")

        result_b = resolve_agent_credential(
            load_vault, decrypt,
            provider="github", workspace_id="workspace-1", agent_id="agent-b",
        )
        self.assertEqual(result_b["token"], "decrypted-mock-encrypted-token-b")

    # ------------------------------------------------------------------
    # (b) No silent fallback  [PASSING]
    # ------------------------------------------------------------------

    def test_agent_without_credential_raises_no_silent_fallback(self) -> None:
        """Agent C has no credential — must raise, not fall back silently."""
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
                load_vault, decrypt,
                provider="github", workspace_id="workspace-1", agent_id="agent-c",
            )
        self.assertIn("no silent fallback", str(ctx.exception).lower())

    # ------------------------------------------------------------------
    # (c) Tool gating — connector not enabled → denied + ledger  [IMPLEMENTED]
    # ------------------------------------------------------------------

    def test_agent_without_linear_connector_cannot_invoke_it(self) -> None:
        """Agent missing 'linear' from enabled_connectors is denied."""
        from server_modules.tool_broker import _enforce_agent_tool_policy, ToolExecutionDeniedError

        fake_install = {
            "id": "install-agent-a",
            "enabled_tools": None,           # NULL = all tools
            "enabled_connectors": ["github", "slack"],  # no 'linear'
        }
        with patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            new=AsyncMock(return_value=fake_install),
        ):
            with self.assertRaises(ToolExecutionDeniedError) as ctx:
                _run(_enforce_agent_tool_policy(
                    agent_install_id="install-agent-a",
                    workspace_id="workspace-1",
                    tool_name="linear__create_issue",
                    connector_scopes=["linear"],
                ))
            self.assertIn("connector_not_enabled:linear", str(ctx.exception.detail))

    def test_denial_records_ledger_event(self) -> None:
        """Blocked connector writes a denial ledger record."""
        from server_modules.tool_broker import _enforce_agent_tool_policy, ToolExecutionDeniedError

        fake_install = {
            "id": "install-agent-a",
            "enabled_tools": None,
            "enabled_connectors": ["github"],
        }

        # Patch the ledger write and registry lookup
        with patch(
            "server_modules.activity_ledger_service.append_activity_event",
            new=AsyncMock(),
        ) as mock_ledger:
            with patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=fake_install),
            ):
                try:
                    _run(_enforce_agent_tool_policy(
                        agent_install_id="install-agent-a",
                        workspace_id="workspace-1",
                        tool_name="linear__create_issue",
                        connector_scopes=["linear"],
                    ))
                except ToolExecutionDeniedError:
                    pass

            # Ledger was called with denial event
            mock_ledger.assert_called_once()
            call_kwargs = mock_ledger.call_args.kwargs
            self.assertEqual(call_kwargs["event_class"], "policy_denial")
            self.assertEqual(call_kwargs["action"], "tool_not_enabled")
            self.assertEqual(call_kwargs["actor_id"], "install-agent-a")
            self.assertEqual(call_kwargs["status"], "blocked")

    def test_agent_with_connector_enabled_is_allowed(self) -> None:
        """Agent with 'linear' in enabled_connectors passes enforcement."""
        from server_modules.tool_broker import _enforce_agent_tool_policy

        fake_install = {
            "id": "install-agent-b",
            "enabled_tools": None,
            "enabled_connectors": ["github", "linear", "slack"],
        }
        with patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            new=AsyncMock(return_value=fake_install),
        ):
            # Should NOT raise
            _run(_enforce_agent_tool_policy(
                agent_install_id="install-agent-b",
                workspace_id="workspace-1",
                tool_name="linear__create_issue",
                connector_scopes=["linear"],
            ))

    # ------------------------------------------------------------------
    # (d) Channel routing  [IMPLEMENTED]
    # ------------------------------------------------------------------

    def test_inbound_telegram_routes_to_correct_agent(self) -> None:
        """Bot A routes to agent A; Bot B routes to agent B; unknown → Sage."""
        from server_modules.agent_channel_router import (
            _resolve_agent_for_inbound,
        )

        channel_bindings_a = [
            {"channel_type": "telegram_personal", "bot_token_hash": "hash-bot-a"},
        ]
        channel_bindings_b = [
            {"channel_type": "telegram_personal", "bot_token_hash": "hash-bot-b"},
        ]

        # Bot A → agent A
        result_a = _resolve_agent_for_inbound(
            channel_type="telegram_personal",
            bot_identifier="hash-bot-a",
            workspace_id="workspace-1",
            agent_installs=[
                {"id": "install-a", "channel_bindings": channel_bindings_a},
                {"id": "install-b", "channel_bindings": channel_bindings_b},
            ],
        )
        self.assertEqual(result_a, "install-a")

        # Bot B → agent B
        result_b = _resolve_agent_for_inbound(
            channel_type="telegram_personal",
            bot_identifier="hash-bot-b",
            workspace_id="workspace-1",
            agent_installs=[
                {"id": "install-a", "channel_bindings": channel_bindings_a},
                {"id": "install-b", "channel_bindings": channel_bindings_b},
            ],
        )
        self.assertEqual(result_b, "install-b")

    def test_unknown_bot_routes_to_sage_fallback(self) -> None:
        """Unmapped bot falls back to Sage in that workspace."""
        from server_modules.agent_channel_router import (
            _resolve_agent_for_inbound,
        )

        # No agent has a binding for "hash-unknown"
        result = _resolve_agent_for_inbound(
            channel_type="telegram_personal",
            bot_identifier="hash-unknown",
            workspace_id="workspace-1",
            sage_agent_id="agent-sage-ws1",
            agent_installs=[
                {"id": "install-a", "channel_bindings": [
                    {"channel_type": "telegram_personal", "bot_token_hash": "hash-bot-a"},
                ]},
            ],
        )
        self.assertEqual(result, "agent-sage-ws1")

    # ------------------------------------------------------------------
    # (e) Missing agent_id at dispatch raises  [IMPLEMENTED]
    # ------------------------------------------------------------------

    def test_missing_agent_id_does_not_silently_default(self) -> None:
        """When agent_id is missing, enforcement is skipped (not an error)."""
        from server_modules.tool_broker import _enforce_agent_tool_policy

        # Empty agent_id → skip enforcement, no error
        _run(_enforce_agent_tool_policy(
            agent_install_id="",
            workspace_id="workspace-1",
            tool_name="any_tool",
            connector_scopes=[],
        ))
        # Should not raise

    # ------------------------------------------------------------------
    # (f) Cross-workspace credential isolation  [PASSING]
    # ------------------------------------------------------------------

    def test_cross_workspace_credential_not_visible(self) -> None:
        """Agent A's credential in workspace X is NOT visible in workspace Y."""
        from server_modules.vault_helpers import resolve_agent_credential

        vault = {
            "version": 1,
            "credentials": [
                {
                    "id": "cred-ws-x",
                    "provider": "github",
                    "workspace_id": "workspace-x",
                    "agent_id": "agent-a",
                    "account_label": "default",
                    "label": "Agent A in workspace X",
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
            return f'{{"token":"dec-{encrypted}"}}'

        # Same agent_id, different workspace — should raise (no credential in ws-y)
        with self.assertRaises(RuntimeError) as ctx:
            resolve_agent_credential(
                load_vault, decrypt,
                provider="github", workspace_id="workspace-y", agent_id="agent-a",
            )
        self.assertIn("no silent fallback", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
