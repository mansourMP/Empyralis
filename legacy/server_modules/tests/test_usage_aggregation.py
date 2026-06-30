import unittest
from datetime import datetime, timezone

from server_modules.usage_reporting import (
    aggregate_usage_summary,
    build_usage_record,
    estimate_cost_usd,
    usage_row_from_snapshot,
)
from server_modules import pricing_registry_service, usage_accounting_service


class UsageAggregationTests(unittest.TestCase):
    def _snapshot(
        self,
        run_id: str,
        *,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        created_at: str = "2026-04-02T08:00:00Z",
    ):
        usage = build_usage_record(
            provider,
            model,
            prompt_tokens,
            completion_tokens,
            prompt_tokens + completion_tokens,
            run_id=run_id,
            timestamp=created_at,
        )
        return {
            "run_id": run_id,
            "status": "completed",
            "owner_user_id": "user-1",
            "user_goal": f"Run {run_id}",
            "created_at": created_at,
            "completed_at": created_at,
            "usage_masked": usage,
        }

    def test_summary_totals_correct(self):
        snapshots = [
            self._snapshot("run-1", provider="openai", model="gpt-4", prompt_tokens=100_000, completion_tokens=50_000),
            self._snapshot("run-2", provider="anthropic", model="claude-3-5-sonnet-20241022", prompt_tokens=10_000, completion_tokens=5_000),
        ]

        summary = aggregate_usage_summary(
            snapshots,
            period="all",
            now=datetime(2026, 4, 2, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(summary["total_tokens"], 165_000)
        self.assertEqual(summary["runs_count"], 2)
        self.assertEqual(len(summary["daily"]), 1)
        self.assertAlmostEqual(summary["total_cost_usd"], 6.105, places=6)

    def test_by_provider_breakdown(self):
        snapshots = [
            self._snapshot("run-1", provider="openai", model="gpt-4", prompt_tokens=100_000, completion_tokens=50_000),
            self._snapshot("run-2", provider="openai", model="gpt-4", prompt_tokens=25_000, completion_tokens=25_000),
            self._snapshot("run-3", provider="anthropic", model="claude-3-5-sonnet-20241022", prompt_tokens=10_000, completion_tokens=5_000),
        ]

        summary = aggregate_usage_summary(
            snapshots,
            period="all",
            now=datetime(2026, 4, 2, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(summary["by_provider"][0]["provider"], "openai")
        self.assertEqual(summary["by_provider"][0]["total_tokens"], 200_000)
        self.assertEqual(summary["by_provider"][0]["runs_count"], 2)
        self.assertGreater(summary["by_provider"][0]["percentage"], 90.0)

    def test_cost_calculation_openai_gpt4(self):
        cost = estimate_cost_usd("openai", "gpt-4", 100_000, 50_000)
        self.assertIsNotNone(cost)
        self.assertAlmostEqual(float(cost or 0.0), 6.0, places=6)

    def test_unknown_model_cost_is_null(self):
        usage = build_usage_record("openai", "unknown-frontier-model", 1_000, 2_000, 3_000)
        self.assertIsNone(usage["estimated_cost_usd"])
        self.assertIsNone(usage["cost_est_usd"])
        self.assertEqual(usage["cost_band"], "Unknown")

        snapshot = {
            "run_id": "run-unknown",
            "status": "completed",
            "owner_user_id": "user-1",
            "user_goal": "Unknown model run",
            "created_at": "2026-04-02T08:00:00Z",
            "completed_at": "2026-04-02T08:00:00Z",
            "usage_masked": usage,
        }
        row = usage_row_from_snapshot(snapshot)
        self.assertIsNotNone(row)
        self.assertIsNone(row["estimated_cost_usd"])

    def test_deepseek_v4_pricing_includes_cache_and_reasoning_rates(self):
        pricing = pricing_registry_service.lookup_model_pricing("deepseek", "deepseek-v4-flash")

        self.assertIsNotNone(pricing)
        self.assertEqual(pricing["input"], 0.14)
        self.assertEqual(pricing["cached_input"], 0.0028)
        self.assertEqual(pricing["cache_read"], 0.0028)
        self.assertEqual(pricing["cache_write"], 0.14)
        self.assertEqual(pricing["output"], 0.28)
        self.assertEqual(pricing["reasoning_output"], 0.28)
        self.assertEqual(pricing["thinking_output"], 0.28)

        pro_pricing = pricing_registry_service.lookup_model_pricing("deepseek", "deepseek-v4-pro")
        self.assertIsNotNone(pro_pricing)
        self.assertEqual(pro_pricing["cached_input"], 0.003625)
        self.assertEqual(pro_pricing["cache_read"], 0.003625)
        self.assertEqual(pro_pricing["cache_write"], 0.435)
        self.assertEqual(pro_pricing["reasoning_output"], 0.87)
        self.assertEqual(pro_pricing["thinking_output"], 0.87)

    def test_openai_and_anthropic_pricing_include_prompt_cache_rates(self):
        openai_pricing = pricing_registry_service.lookup_model_pricing("openai", "gpt-4o-mini")
        self.assertIsNotNone(openai_pricing)
        self.assertEqual(openai_pricing["cached_input"], 0.075)
        self.assertEqual(openai_pricing["cache_read"], 0.075)
        self.assertEqual(openai_pricing["cache_write"], 0.15)

        anthropic_pricing = pricing_registry_service.lookup_model_pricing("anthropic", "claude-sonnet-4")
        self.assertIsNotNone(anthropic_pricing)
        self.assertEqual(anthropic_pricing["cache_read"], 0.3)
        self.assertEqual(anthropic_pricing["cache_write"], 3.75)

        uncached = pricing_registry_service.estimate_cost_usd("openai", "gpt-4o-mini", 1_000_000, 0)
        cached = pricing_registry_service.estimate_cost_usd(
            "openai",
            "gpt-4o-mini",
            0,
            0,
            cache_read_tokens=1_000_000,
        )
        self.assertLess(float(cached or 0.0), float(uncached or 0.0))

    def test_usage_accounting_projects_surface_and_source_surface(self):
        record = usage_accounting_service.build_usage_accounting_record(
            run_id="run-mini-app",
            workspace_id="ws-1",
            app_id="writing",
            surface="mini_app",
            source_surface="mini_app_invoke",
            requested_provider="deepseek",
            effective_provider="deepseek",
            requested_model="deepseek-v4-flash",
            effective_model="deepseek-v4-flash",
            input_tokens=10,
            output_tokens=5,
        )

        projection = usage_accounting_service.usage_projection_from_record(record)
        row = usage_accounting_service.usage_row_from_record(record)

        self.assertEqual(record["surface"], "mini_app")
        self.assertEqual(record["source_surface"], "mini_app_invoke")
        self.assertEqual(projection["surface"], "mini_app")
        self.assertEqual(projection["source_surface"], "mini_app_invoke")
        self.assertEqual(row["surface"], "mini_app")
        self.assertEqual(row["source_surface"], "mini_app_invoke")

        restored = usage_accounting_service.accounting_record_from_snapshot(
            {
                "run_id": "run-sage",
                "usage_accounting": {
                    "surface": "sage",
                    "effective_provider": "deepseek",
                    "effective_model": "deepseek-v4-flash",
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "total_tokens": 2,
                },
            }
        )
        self.assertIsNotNone(restored)
        self.assertEqual(restored["surface"], "sage")
        self.assertEqual(restored["source_surface"], "sage")

    def test_openai_usage_normalizer_captures_cache_and_reasoning_tokens(self):
        record = usage_accounting_service.normalize_provider_usage(
            provider="openai",
            model="gpt-4o-mini",
            usage={
                "prompt_tokens": 100,
                "completion_tokens": 25,
                "total_tokens": 125,
                "prompt_tokens_details": {"cached_tokens": 40},
                "completion_tokens_details": {"reasoning_tokens": 8},
            },
            run_id="run-openai",
        )

        usage = usage_accounting_service.usage_projection_from_record(record)
        self.assertEqual(usage["prompt_tokens"], 60)
        self.assertEqual(usage["cached_input_tokens"], 40)
        self.assertEqual(usage["cache_read_tokens"], 40)
        self.assertEqual(usage["visible_output_tokens"], 17)
        self.assertEqual(usage["reasoning_tokens"], 8)
        self.assertEqual(usage["completion_tokens"], 25)
        self.assertEqual(usage["estimation_mode"], "provider_usage_exact")

    def test_deepseek_usage_normalizer_captures_prompt_cache_hit_and_miss_tokens(self):
        record = usage_accounting_service.normalize_provider_usage(
            provider="deepseek",
            model="deepseek-v4-flash",
            usage={
                "prompt_tokens": 100,
                "prompt_cache_hit_tokens": 40,
                "prompt_cache_miss_tokens": 60,
                "completion_tokens": 10,
                "total_tokens": 110,
            },
            run_id="run-deepseek-cache",
            payer="platform_credits",
        )

        usage = usage_accounting_service.usage_projection_from_record(record)
        self.assertEqual(usage["prompt_tokens"], 60)
        self.assertEqual(usage["cached_input_tokens"], 40)
        self.assertEqual(usage["cache_read_tokens"], 40)
        self.assertEqual(usage["visible_output_tokens"], 10)
        self.assertEqual(usage["completion_tokens"], 10)
        self.assertEqual(usage["total_tokens"], 110)
        self.assertEqual(usage["estimation_mode"], "provider_usage_exact")
        self.assertEqual(usage["pricing_known"], True)
        self.assertAlmostEqual(float(usage["provider_cost_usd"]), 0.000011, places=6)
        self.assertIn("prompt_cache_hit_tokens", record["metadata"]["raw_usage_keys"])
        self.assertIn("prompt_cache_miss_tokens", record["metadata"]["raw_usage_keys"])

    def test_openrouter_usage_normalizer_preserves_cache_reasoning_and_reported_cost(self):
        record = usage_accounting_service.normalize_provider_usage(
            provider="openrouter",
            model="openai/gpt-5.2",
            usage={
                "prompt_tokens": 120,
                "completion_tokens": 35,
                "total_tokens": 155,
                "prompt_tokens_details": {
                    "cached_tokens": 20,
                    "cache_write_tokens": 10,
                },
                "completion_tokens_details": {"reasoning_tokens": 12},
                "cost": 0.0042,
                "currency": "USD",
            },
            run_id="run-openrouter",
            payer="BYOK",
        )

        usage = usage_accounting_service.usage_projection_from_record(record)
        self.assertEqual(usage["prompt_tokens"], 100)
        self.assertEqual(usage["cache_read_tokens"], 20)
        self.assertEqual(usage["cache_write_tokens"], 10)
        self.assertEqual(usage["reasoning_tokens"], 12)
        self.assertEqual(usage["provider_cost_usd"], 0.0042)
        self.assertEqual(record["metadata"]["provider_reported_cost"], 0.0042)
        self.assertEqual(record["metadata"]["provider_reported_currency"], "USD")

    def test_gemini_usage_normalizer_captures_cached_and_thinking_tokens(self):
        record = usage_accounting_service.normalize_provider_usage(
            provider="gemini",
            model="gemini-2.5-flash",
            usage={
                "promptTokenCount": 100,
                "cachedContentTokenCount": 25,
                "candidatesTokenCount": 10,
                "thoughtsTokenCount": 5,
                "totalTokenCount": 115,
            },
            run_id="run-gemini",
        )

        usage = usage_accounting_service.usage_projection_from_record(record)
        self.assertEqual(usage["prompt_tokens"], 75)
        self.assertEqual(usage["cache_read_tokens"], 25)
        self.assertEqual(usage["visible_output_tokens"], 10)
        self.assertEqual(usage["thinking_tokens"], 5)
        self.assertEqual(usage["completion_tokens"], 15)

    def test_platform_paid_usage_requires_known_pricing(self):
        with self.assertRaisesRegex(ValueError, "Known pricing is required"):
            usage_accounting_service.build_usage_accounting_record(
                run_id="run-unknown",
                requested_provider="openai",
                effective_provider="openai",
                requested_model="unknown-frontier-model",
                effective_model="unknown-frontier-model",
                input_tokens=10,
                output_tokens=5,
                payer="platform_credits",
                require_known_pricing=True,
            )

    def test_platform_paid_usage_validation_rejects_missing_provider_usage(self):
        row = {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "total_tokens": 0,
            "estimated_cost_usd": 0.001,
            "pricing_known": True,
            "estimation_mode": "provider_usage_missing",
        }

        self.assertEqual(
            usage_accounting_service.platform_paid_usage_validation_error(row),
            "missing_provider_token_usage",
        )

    def test_platform_paid_usage_validation_accepts_exact_priced_usage(self):
        row = {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "total_tokens": 10,
            "estimated_cost_usd": 0.001,
            "pricing_known": True,
            "estimation_mode": "provider_usage_exact",
        }

        self.assertIsNone(usage_accounting_service.platform_paid_usage_validation_error(row))

    def test_platform_paid_usage_validation_rejects_blackbox_heuristic_usage(self):
        row = {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "total_tokens": 10,
            "estimated_cost_usd": 0.001,
            "pricing_known": False,
            "estimation_mode": "heuristic_blackbox",
        }

        self.assertEqual(
            usage_accounting_service.platform_paid_usage_validation_error(row),
            "unknown_pricing",
        )

    def test_platform_paid_usage_populates_retail_credits_from_cost(self):
        record = usage_accounting_service.build_usage_accounting_record(
            run_id="run-credit-display",
            requested_provider="deepseek",
            effective_provider="deepseek",
            requested_model="deepseek-v4-flash",
            effective_model="deepseek-v4-flash",
            input_tokens=10,
            output_tokens=5,
            provider_cost_usd=0.001,
            payer="platform_credits",
        )

        self.assertEqual(record["retail_credits_charged"], 20.0)


if __name__ == "__main__":
    unittest.main()
