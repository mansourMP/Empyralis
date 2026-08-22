from __future__ import annotations

import importlib
import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from server_modules import usage_events_repository


def _fake_scoped_connection(connection):
    @asynccontextmanager
    async def _manager(*args, **kwargs):
        yield connection

    return _manager


class CanonicalUsagePayerTests(unittest.TestCase):
    """The usage matrix's `source` column must always read as one of the
    four canonical ledger payers (credit_ledger_contract.LEDGER_PAYERS),
    never a raw internal mode token — including `cli_subscription`, the
    literal value agent_turn_runtime_service._meter_and_ledger_cli_subscription_turn
    writes for CLI-subscription (Claude Code / Codex) turns."""

    def test_platform_credits_aliases(self) -> None:
        for token in ("platform_credits", "empyralis_credits", "empyralis", "PLATFORM_CREDITS"):
            self.assertEqual(usage_events_repository._canonical_usage_payer(token), "platform_credits")

    def test_empty_or_missing_mode_is_unknown_not_silently_platform(self) -> None:
        for token in (None, "", "   "):
            self.assertEqual(usage_events_repository._canonical_usage_payer(token), "unknown")

    def test_byok_aliases(self) -> None:
        for token in ("byok", "byok_api", "workspace_api_key", "workspace_connection"):
            self.assertEqual(usage_events_repository._canonical_usage_payer(token), "BYOK")

    def test_local_aliases(self) -> None:
        for token in ("local", "local_model", "local_companion"):
            self.assertEqual(usage_events_repository._canonical_usage_payer(token), "local")

    def test_subscription_aliases_including_cli_subscription(self) -> None:
        for token in ("cli_subscription", "subscription", "subscription_passthrough", "codex_cli", "claude_code_cli"):
            self.assertEqual(usage_events_repository._canonical_usage_payer(token), "subscription_passthrough")

    def test_unknown_token_falls_back_to_unknown(self) -> None:
        self.assertEqual(usage_events_repository._canonical_usage_payer("something_new"), "unknown")


class SummarizeUsageMatrixTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        global usage_events_repository
        usage_events_repository = importlib.import_module("server_modules.usage_events_repository")
        usage_events_repository._SCHEMA_READY = True

    async def test_summarize_usage_returns_full_attribution_matrix(self) -> None:
        connection = AsyncMock()
        connection.fetchrow = AsyncMock(
            return_value={
                "events": 3,
                "tokens_in": 1500,
                "tokens_out": 450,
                "total_tokens": 1950,
                "tokens_cache_creation": 40,
                "tokens_cache_read": 200,
                "usd_cost": 0.00042,
            }
        )
        connection.fetch = AsyncMock(
            side_effect=[
                [],  # buckets
                [],  # by_agent
                [
                    {
                        "agent_install_id": "agent_1",
                        "provider": "deepseek",
                        "model": "deepseek-reasoner",
                        "mode": "platform_credits",
                        "events": 2,
                        "tokens_in": 1000,
                        "tokens_out": 300,
                        "total_tokens": 1300,
                        "tokens_cache_creation": 10,
                        "tokens_cache_read": 150,
                        "usd_cost": 0.00022,
                        "pricing_known": True,
                    },
                    {
                        "agent_install_id": "agent_1",
                        "provider": "claude_code",
                        "model": "sonnet",
                        "mode": "cli_subscription",
                        "events": 1,
                        "tokens_in": 500,
                        "tokens_out": 150,
                        "total_tokens": 650,
                        "tokens_cache_creation": 30,
                        "tokens_cache_read": 50,
                        "usd_cost": 0.0,
                        "pricing_known": False,
                    },
                ],
            ]
        )

        with (
            patch(
                "server_modules.usage_events_repository._cpr.ensure_control_plane_schema",
                new=AsyncMock(return_value=object()),
            ),
            patch(
                "server_modules.usage_events_repository._cpr._scoped_connection",
                new=_fake_scoped_connection(connection),
            ),
        ):
            result = await usage_events_repository.summarize_usage(
                tenant_id="tenant-1", workspace_id="workspace-1", scope="agent", scope_id="agent_1", period="day",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(len(result["matrix"]), 2)

        platform_row = result["matrix"][0]
        self.assertEqual(platform_row["agent_install_id"], "agent_1")
        self.assertEqual(platform_row["provider"], "deepseek")
        self.assertEqual(platform_row["model"], "deepseek-reasoner")
        self.assertEqual(platform_row["mode"], "platform_credits")
        self.assertEqual(platform_row["payer"], "platform_credits")
        self.assertEqual(platform_row["tokens_in"], 1000)
        self.assertEqual(platform_row["tokens_out"], 300)
        self.assertEqual(platform_row["tokens_cache_creation"], 10)
        self.assertEqual(platform_row["tokens_cache_read"], 150)
        self.assertEqual(platform_row["usd_cost"], 0.00022)
        self.assertTrue(platform_row["pricing_known"])
        self.assertEqual(result["totals"]["tokens_cache_creation"], 40)
        self.assertEqual(result["totals"]["tokens_cache_read"], 200)

        subscription_row = result["matrix"][1]
        self.assertEqual(subscription_row["mode"], "cli_subscription")
        # cli_subscription must canonicalize to the ledger's
        # subscription_passthrough payer, not leak the raw internal token.
        self.assertEqual(subscription_row["payer"], "subscription_passthrough")
        self.assertFalse(subscription_row["pricing_known"])

    async def test_summarize_usage_without_pool_returns_empty_matrix(self) -> None:
        with patch(
            "server_modules.usage_events_repository._cpr.ensure_control_plane_schema",
            new=AsyncMock(return_value=None),
        ):
            result = await usage_events_repository.summarize_usage(
                tenant_id="tenant-1", workspace_id="workspace-1",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["matrix"], [])


class RecordUsageEventCacheTokenTests(unittest.IsolatedAsyncioTestCase):
    """tokens_cache_creation/tokens_cache_read (Anthropic's cache_creation_
    input_tokens/cache_read_input_tokens, aka ModelUsage.cacheCreation
    InputTokens/cacheReadInputTokens) must actually reach the INSERT, not
    just get computed and dropped."""

    def setUp(self) -> None:
        global usage_events_repository
        usage_events_repository = importlib.import_module("server_modules.usage_events_repository")
        usage_events_repository._SCHEMA_READY = True

    async def test_cache_tokens_are_persisted_in_the_row_and_insert(self) -> None:
        connection = AsyncMock()
        connection.execute = AsyncMock()

        with (
            patch(
                "server_modules.usage_events_repository._cpr.ensure_control_plane_schema",
                new=AsyncMock(return_value=object()),
            ),
            patch(
                "server_modules.usage_events_repository._cpr._scoped_connection",
                new=_fake_scoped_connection(connection),
            ),
        ):
            row = await usage_events_repository.record_usage_event(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                provider="anthropic",
                model="claude-sonnet-4-5",
                tokens_in=1000,
                tokens_out=200,
                tokens_cache_creation=10,
                tokens_cache_read=50,
                usd_cost=0.0033,
            )

        self.assertIsNotNone(row)
        self.assertEqual(row["tokens_cache_creation"], 10)
        self.assertEqual(row["tokens_cache_read"], 50)
        # The actual INSERT call must carry the cache-token values through —
        # a row dict that "looks right" but whose SQL never mentions the
        # columns would silently persist zeros.
        insert_call = connection.execute.call_args_list[-1]
        insert_args = insert_call.args
        self.assertIn("tokens_cache_creation", insert_args[0])
        self.assertIn("tokens_cache_read", insert_args[0])
        self.assertIn(10, insert_args)
        self.assertIn(50, insert_args)

    async def test_cache_tokens_default_to_zero(self) -> None:
        connection = AsyncMock()
        connection.execute = AsyncMock()

        with (
            patch(
                "server_modules.usage_events_repository._cpr.ensure_control_plane_schema",
                new=AsyncMock(return_value=object()),
            ),
            patch(
                "server_modules.usage_events_repository._cpr._scoped_connection",
                new=_fake_scoped_connection(connection),
            ),
        ):
            row = await usage_events_repository.record_usage_event(
                tenant_id="tenant-1", workspace_id="workspace-1", provider="anthropic",
                model="claude-sonnet-4-5", tokens_in=100, tokens_out=20,
            )

        self.assertEqual(row["tokens_cache_creation"], 0)
        self.assertEqual(row["tokens_cache_read"], 0)


if __name__ == "__main__":
    unittest.main()
