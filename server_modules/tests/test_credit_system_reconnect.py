"""Tests for the 2026-07-20 credit-system reconnect:
  - billing_credit_config.py's cost -> credit conversion (legible unit,
    margin over underlying cost)
  - control_plane_repository's new direct per-turn debit
    (_build_workspace_credit_turn_debit_result /
    debit_workspace_credits_for_turn_atomic) — decrements a workspace's
    credit_balance_usd on real usage, seeds a generous free floor for any
    workspace (new or pre-existing) that hasn't been touched yet, and is
    non-blocking by construction (clamps at zero, never raises).
  - billing_service.debit_workspace_credits_for_turn's missing-scope guard.

These exercise the pure, DB-free building blocks directly (no Postgres, no
Rust governance kernel, no local-identity account creation) so they run
in any environment, including this repo's current sandbox where the
compiled empyralis-runtime-kernel binary isn't available (several
unrelated control-plane-gated tests already skip/fail for that reason —
see test_billing_service.py's pre-existing failures on a clean baseline).
"""

from __future__ import annotations

import asyncio
import unittest

from server_modules import billing_credit_config
from server_modules import billing_service
from server_modules import control_plane_repository


class CreditConversionTests(unittest.TestCase):
    """(c) Conversion gives ~1 credit for a simple turn, more for heavy ones."""

    def test_simple_hello_turn_costs_one_credit(self):
        # The exact reference-turn cost from billing_credit_config.py's
        # module docstring: ~500 input + 150 output tokens on deepseek-chat
        # ($0.14 / $0.28 per 1M tokens) -> ~$0.000112 raw.
        raw_cost_usd = 500 / 1_000_000 * 0.14 + 150 / 1_000_000 * 0.28
        credits = billing_credit_config.credits_for_turn_cost_usd(raw_cost_usd)
        self.assertEqual(credits, 1)

    def test_heavier_turn_costs_proportionally_more(self):
        light_cost = 500 / 1_000_000 * 0.14 + 150 / 1_000_000 * 0.28
        heavy_cost = 30_000 / 1_000_000 * 0.14 + 5_000 / 1_000_000 * 0.28
        light_credits = billing_credit_config.credits_for_turn_cost_usd(light_cost)
        heavy_credits = billing_credit_config.credits_for_turn_cost_usd(heavy_cost)
        self.assertGreater(heavy_credits, light_credits)
        # Genuinely proportional, not just "more than 1". At the lean
        # post-2026-07-21 rate (100 credits/$, 3x margin — see
        # billing_credit_config.py), heavy_cost (~$0.0056) bills to
        # ~$0.0168 -> ceil(1.68) = 2 credits; light_cost floors at 1. This
        # threshold was 10 back when the rate was 2000 credits/$ (the
        # inflated pre-lean-grant config, where the same heavy_cost billed
        # to ~34 credits) — updated to match the intentional rate change,
        # not a regression.
        self.assertGreaterEqual(heavy_credits, 2)

    def test_zero_cost_turn_charges_nothing(self):
        # No ground-truth cost (e.g. pricing unknown) must never be
        # guessed into a charge — see the reconnect call site's
        # `_sage_usd_cost is not None` guard in agent_turn_runtime_service.py.
        self.assertEqual(billing_credit_config.credits_for_turn_cost_usd(0), 0)
        self.assertEqual(billing_credit_config.credits_for_turn_cost_usd(None), 0)

    def test_tiny_nonzero_cost_still_floors_to_the_minimum(self):
        # A turn that measurably cost *something* is never displayed/
        # charged as 0 — legibility floor.
        credits = billing_credit_config.credits_for_turn_cost_usd(0.0000001)
        self.assertEqual(credits, billing_credit_config.MIN_CREDITS_CHARGED_PER_TURN)

    def test_margin_multiplier_increases_billed_cost(self):
        raw = 0.001
        billed = billing_credit_config.billed_cost_usd_for_turn(raw)
        self.assertGreater(billing_credit_config.CREDIT_COST_MARGIN_MULTIPLIER, 1.0)
        self.assertAlmostEqual(billed, raw * billing_credit_config.CREDIT_COST_MARGIN_MULTIPLIER)
        self.assertGreater(billed, raw)


class DirectTurnDebitTests(unittest.TestCase):
    """(a) Credits decrement on a turn. (b) Free allowance prevents blocking."""

    def test_first_turn_seeds_the_free_floor_then_debits(self):
        """A workspace that has never been touched (no credit_balance_usd
        anywhere in its metadata — the state of every real pre-existing
        workspace before this reconnect) gets backfilled to the generous
        floor on its very first real turn, then the turn's own cost is
        debited from that floor — credits genuinely go down, and the
        workspace was never blocked getting there."""
        metadata: dict = {}
        next_metadata, result = control_plane_repository._build_workspace_credit_turn_debit_result(
            metadata=metadata,
            request_id="turn-1",
            credits_to_charge=5,
            floor_usd=5.0,
            credits_per_usd=2_000,
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["backfilled"])
        self.assertEqual(result["credits_owed"], 5)
        self.assertEqual(result["credits_debited"], 5)
        self.assertFalse(result["insufficient"])
        # Floor (5.0) minus this turn's 5 credits (5 / 2000 = $0.0025).
        self.assertAlmostEqual(result["credit_balance_usd"], 5.0 - (5 / 2_000))
        admin_payload = next_metadata["admin_defaults"]
        self.assertAlmostEqual(admin_payload["credit_balance_usd"], result["credit_balance_usd"])
        transactions = admin_payload["credit_transactions"]
        kinds = [t["kind"] for t in transactions]
        self.assertIn("bonus", kinds)  # the safety backfill grant
        self.assertIn("usage_debit", kinds)  # the turn's own debit

    def test_second_turn_only_debits_does_not_reseed(self):
        """The floor top-up is a ONE-TIME safety net, not a per-turn
        refill — verifies it doesn't silently top the balance back up
        every turn (which would make the balance number meaningless)."""
        metadata: dict = {}
        after_first, first_result = control_plane_repository._build_workspace_credit_turn_debit_result(
            metadata=metadata,
            request_id="turn-1",
            credits_to_charge=5,
            floor_usd=5.0,
            credits_per_usd=2_000,
        )
        after_second, second_result = control_plane_repository._build_workspace_credit_turn_debit_result(
            metadata=after_first,
            request_id="turn-2",
            credits_to_charge=3,
            floor_usd=5.0,
            credits_per_usd=2_000,
        )
        self.assertTrue(second_result["ok"])
        self.assertFalse(second_result["backfilled"])
        self.assertEqual(second_result["credits_debited"], 3)
        # Strictly less than after the first turn — real decrement, not a
        # flat/reset number.
        self.assertLess(second_result["credit_balance_usd"], first_result["credit_balance_usd"])

    def test_never_blocks_even_when_balance_cannot_cover_the_charge(self):
        """SAFETY: if a turn would drive the balance negative, the debit
        clamps at zero and reports `insufficient` — it never raises and
        never leaves a negative balance. This is what makes the reconnect
        non-blocking by construction, independent of the entitlement
        service's separate (untouched) hard-stop gate."""
        # A workspace that already has a small balance (e.g. mostly spent) —
        # admin_defaults is where debits/backfills actually live once a
        # workspace has been touched (see _build_workspace_credit_turn_debit_result).
        metadata = {"admin_defaults": {"credit_balance_usd": 0.001, "credit_transactions": [
            {"kind": "bonus", "source": "safety_backfill_grant", "amount_usd": 5.0,
             "credits": 10000, "created_at": 1},
        ]}}
        next_metadata, result = control_plane_repository._build_workspace_credit_turn_debit_result(
            metadata=metadata,
            request_id="turn-huge",
            credits_to_charge=1_000_000,  # absurdly large on purpose
            floor_usd=5.0,
            credits_per_usd=2_000,
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["insufficient"])
        self.assertGreaterEqual(result["credit_balance_usd"], 0.0)
        self.assertEqual(result["credit_balance_usd"], 0.0)
        # Already backfilled once (marker transaction present) — must not
        # backfill again just because the balance is now low.
        self.assertFalse(result["backfilled"])

    def test_idempotent_per_request_id(self):
        """A retried turn (same request_id) must not double-charge."""
        metadata: dict = {}
        after_first, first_result = control_plane_repository._build_workspace_credit_turn_debit_result(
            metadata=metadata,
            request_id="turn-retry",
            credits_to_charge=4,
            floor_usd=5.0,
            credits_per_usd=2_000,
        )
        _after_retry, retry_result = control_plane_repository._build_workspace_credit_turn_debit_result(
            metadata=after_first,
            request_id="turn-retry",
            credits_to_charge=4,
            floor_usd=5.0,
            credits_per_usd=2_000,
        )
        self.assertTrue(retry_result["ok"])
        self.assertEqual(retry_result["reason"], "already_recorded")
        self.assertEqual(retry_result["credits_debited"], 0)
        self.assertEqual(retry_result["credit_balance_usd"], first_result["credit_balance_usd"])


class BillingServiceWrapperTests(unittest.TestCase):
    def test_debit_workspace_credits_for_turn_rejects_missing_scope_without_raising(self):
        result = billing_service.debit_workspace_credits_for_turn(
            workspace_id="",
            tenant_id="",
            request_id="",
            credits_to_charge=1,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "missing_scope")


if __name__ == "__main__":
    unittest.main()
