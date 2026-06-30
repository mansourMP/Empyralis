from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from server_modules import provider_catalog_service
from server_modules import provider_profiles
from server_modules import usage_reporting


class ProviderProfilesTests(unittest.TestCase):
    def test_provider_limit_policy_covers_required_runtime_providers(self) -> None:
        openai_limits = provider_profiles.provider_limit_policy("openai", "gpt-4.1")
        codex_limits = provider_profiles.provider_limit_policy("codex_cli", "gpt-5.4")
        anthropic_limits = provider_profiles.provider_limit_policy("anthropic", "claude-3-7-sonnet-20250219")
        deepseek_limits = provider_profiles.provider_limit_policy("deepseek", "deepseek-chat")
        gemini_limits = provider_profiles.provider_limit_policy("gemini", "gemini-2.5-flash")
        ollama_limits = provider_profiles.provider_limit_policy("ollama", "llama3.2")

        self.assertGreaterEqual(int(openai_limits["max_output_tokens"]), 512)
        self.assertGreaterEqual(int(codex_limits["max_output_tokens"]), int(openai_limits["max_output_tokens"]))
        self.assertGreaterEqual(int(anthropic_limits["max_retry_attempts"]), 1)
        self.assertGreaterEqual(int(deepseek_limits["max_retry_attempts"]), int(openai_limits["max_retry_attempts"]))
        self.assertGreaterEqual(int(gemini_limits["max_output_tokens"]), 512)
        self.assertLessEqual(int(ollama_limits["max_output_tokens"]), int(openai_limits["max_output_tokens"]))

    def test_gemini_provider_defaults_to_25_flash_and_keeps_25_pro_available(self) -> None:
        gemini_entry = provider_profiles.provider_catalog_entry("gemini")
        model_ids = [item["id"] for item in provider_profiles.provider_model_catalog("gemini")]

        self.assertEqual(gemini_entry["default_model"], "gemini-2.5-flash")
        self.assertIn("gemini-2.5-flash", model_ids)
        self.assertIn("gemini-2.5-pro", model_ids)

    def test_gemini_25_model_catalog_uses_current_pricing_and_capabilities(self) -> None:
        models = {
            item["id"]: item
            for item in provider_profiles.provider_model_catalog("gemini")
        }

        self.assertAlmostEqual(models["gemini-2.5-flash"]["input_cost_per_1k_usd"], 0.0003, places=8)
        self.assertAlmostEqual(models["gemini-2.5-flash"]["output_cost_per_1k_usd"], 0.0025, places=8)
        self.assertTrue(models["gemini-2.5-flash"]["supports_reasoning"])
        self.assertAlmostEqual(models["gemini-2.5-pro"]["input_cost_per_1k_usd"], 0.00125, places=8)
        self.assertAlmostEqual(models["gemini-2.5-pro"]["output_cost_per_1k_usd"], 0.01, places=8)
        self.assertTrue(models["gemini-2.5-pro"]["supports_reasoning"])

    def test_usage_reporting_uses_current_gemini_25_flash_pricing(self) -> None:
        pricing = usage_reporting.lookup_model_pricing("gemini", "gemini-2.5-flash")
        cost = usage_reporting.estimate_cost_usd("gemini", "gemini-2.5-flash", 1_000_000, 1_000_000)

        self.assertIsNotNone(pricing)
        self.assertEqual(pricing["source"], "https://ai.google.dev/gemini-api/docs/pricing")
        self.assertAlmostEqual(float(pricing["input"]), 0.30, places=6)
        self.assertAlmostEqual(float(pricing["output"]), 2.50, places=6)
        self.assertAlmostEqual(float(cost or 0.0), 2.80, places=6)

    def test_deepseek_provider_cost_table_uses_non_zero_official_rates(self) -> None:
        rates = provider_profiles.PROVIDER_COST_PER_1K["deepseek"]

        self.assertAlmostEqual(rates["input"], 0.00014, places=8)
        self.assertAlmostEqual(rates["output"], 0.00028, places=8)

    def test_deepseek_governance_metadata_includes_privacy_and_jurisdiction(self) -> None:
        governance = provider_profiles.provider_governance_entry("deepseek")

        self.assertEqual(governance["jurisdiction"], "People's Republic of China")
        self.assertIn("privacy policy", governance["privacy_posture"].lower())
        self.assertIn("prc", governance["residency"].lower())
        self.assertIn("regulated buyers", governance["enterprise_risk_note"].lower())

    def test_deepseek_model_catalog_uses_non_zero_pricing(self) -> None:
        models = {
            item["id"]: item
            for item in provider_profiles.provider_model_catalog("deepseek")
        }

        self.assertAlmostEqual(models["deepseek-chat"]["input_cost_per_1k_usd"], 0.00014, places=8)
        self.assertAlmostEqual(models["deepseek-chat"]["output_cost_per_1k_usd"], 0.00028, places=8)
        self.assertAlmostEqual(models["deepseek-reasoner"]["input_cost_per_1k_usd"], 0.00014, places=8)
        self.assertAlmostEqual(models["deepseek-reasoner"]["output_cost_per_1k_usd"], 0.00028, places=8)

    def test_deepseek_platform_runtime_profile_is_secretless(self) -> None:
        self.assertTrue(provider_profiles.provider_supports_auth_mode("deepseek", "platform_runtime"))
        self.assertFalse(provider_profiles.provider_requires_credential("deepseek", "platform_runtime"))
        self.assertFalse(provider_profiles.provider_supports_auth_mode("openai", "platform_runtime"))
        self.assertTrue(provider_profiles.provider_requires_credential("openai", "platform_runtime"))

    def test_ollama_model_catalog_marks_local_models_as_tool_capable(self) -> None:
        models = {
            item["id"]: item
            for item in provider_profiles.provider_model_catalog("ollama")
        }

        self.assertTrue(models["llama3.2"]["supports_tools"])
        self.assertIn("Tools", models["llama3.2"]["capability_labels"])
        self.assertTrue(models["phi3"]["supports_tools"])
        self.assertIn("Tools", models["phi3"]["capability_labels"])

    def test_ollama_cloud_is_api_key_provider_not_local_runtime(self) -> None:
        entry = provider_profiles.provider_catalog_entry("ollama_cloud")
        governance = provider_profiles.provider_governance_entry("ollama_cloud")

        self.assertEqual(entry["label"], "Ollama Cloud")
        self.assertEqual(entry["default_auth_mode"], "api_key")
        self.assertEqual(entry["base_url"], "https://ollama.com")
        self.assertIn("workspace_api", entry["provider_scopes"])
        self.assertNotIn("local_only", entry["provider_scopes"])
        self.assertFalse(governance["local_self_hosted_compatible"])
        self.assertIsInstance(provider_profiles.PROVIDER_ADAPTERS["ollama_cloud"], provider_profiles.OllamaCloudAdapter)

    def test_ollama_cloud_model_catalog_marks_hosted_models_as_tool_capable(self) -> None:
        models = {
            item["id"]: item
            for item in provider_profiles.provider_model_catalog("ollama_cloud")
        }

        self.assertTrue(models["gpt-oss:120b"]["supports_tools"])
        self.assertTrue(models["gpt-oss:120b"]["supports_reasoning"])
        self.assertIn("Hosted API", models["gpt-oss:120b"]["capability_labels"])
        self.assertTrue(models["gpt-oss:20b"]["supports_tools"])

    def test_runtime_truth_marks_ollama_cloud_env_as_platform_runtime(self) -> None:
        provider_profiles._init()
        with patch.dict(os.environ, {"OLLAMA_API_KEY": "ollama-cloud-test"}, clear=False), patch.object(
            provider_profiles._server,
            "PROVIDER_PROFILES",
            {},
        ), patch("server_modules.provider_profiles._default_vault_credential_present", return_value=False):
            payload = provider_profiles.build_provider_runtime_truth("default")

        ollama_cloud = next(item for item in payload["providers"] if item["id"] == "ollama_cloud")
        self.assertEqual(ollama_cloud["identity_owner"], "platform_account")
        self.assertEqual(ollama_cloud["connection_scope"], "runtime")
        self.assertEqual(ollama_cloud["active_source"], "env-ollama_cloud")
        self.assertTrue(ollama_cloud["configured"])
        self.assertTrue(ollama_cloud["usable"])

    def test_workspace_connection_truth_treats_ollama_as_local_runtime_not_workspace_setup(self) -> None:
        payload = provider_profiles.build_workspace_provider_connection_truth("default")
        ollama = next(item for item in payload["providers"] if item["id"] == "ollama")

        self.assertEqual(ollama["connection_scope"], "machine")
        self.assertEqual(ollama["identity_owner"], "local_machine")
        self.assertEqual(ollama["active_source"], "local_runtime")
        self.assertTrue(ollama["configured"])

    def test_runtime_truth_treats_ollama_as_local_machine_not_platform_hosted(self) -> None:
        with patch("server_modules.provider_profiles.ollama_local_status", return_value={"reachable": True, "has_models": True, "models": ["qwen2.5:1.5b"], "error": ""}), patch(
            "server_modules.provider_profiles.workspace_live_gateway_available",
            return_value=True,
        ):
            payload = provider_profiles.build_provider_runtime_truth("default")
        ollama = next(item for item in payload["providers"] if item["id"] == "ollama")

        self.assertEqual(ollama["identity_owner"], "local_machine")
        self.assertEqual(ollama["identity_owner_label"], "Local machine")
        self.assertEqual(ollama["connection_scope"], "machine")
        self.assertEqual(ollama["active_source"], "local-ollama")
        self.assertTrue(ollama["configured"])

    def test_runtime_truth_marks_ollama_gateway_requirement_when_local_gateway_is_offline(self) -> None:
        with patch("server_modules.provider_profiles.ollama_local_status", return_value={"reachable": True, "has_models": True, "models": ["qwen2.5:1.5b"], "error": ""}), patch(
            "server_modules.provider_profiles.workspace_live_gateway_available",
            return_value=False,
        ):
            payload = provider_profiles.build_provider_runtime_truth("default")

        ollama = next(item for item in payload["providers"] if item["id"] == "ollama")
        self.assertEqual(ollama["state"], "configured")
        self.assertEqual(ollama["issue_code"], "local_gateway_required")
        self.assertFalse(ollama["usable"])

    def test_workspace_live_gateway_available_falls_back_to_default_only_in_local_env(self) -> None:
        def _registrations(workspace_id, include_revoked=False):
            if workspace_id == "default":
                return [{"gateway_id": "gw-local", "status": "active"}]
            return []

        with patch.dict(os.environ, {"ORION_ENV": "local"}, clear=False), patch(
            "server_modules.gateway_state_repository.list_workspace_gateway_registrations",
            side_effect=_registrations,
        ) as list_registrations, patch(
            "server_modules.gateway_protocol_service.gateway_connection_is_live",
            return_value=True,
        ):
            self.assertTrue(provider_profiles.workspace_live_gateway_available("ws-1"))

        self.assertEqual(
            [call.args[0] for call in list_registrations.call_args_list],
            ["ws-1", "default"],
        )

    def test_workspace_live_gateway_available_does_not_default_fallback_in_production(self) -> None:
        with patch.dict(os.environ, {"ORION_ENV": "production"}, clear=False), patch(
            "server_modules.gateway_state_repository.list_workspace_gateway_registrations",
            return_value=[],
        ) as list_registrations:
            self.assertFalse(provider_profiles.workspace_live_gateway_available("ws-1"))

        self.assertEqual([call.args[0] for call in list_registrations.call_args_list], ["ws-1"])

    def test_build_masked_usage_falls_back_to_deepseek_provider_rates(self) -> None:
        usage = provider_profiles.build_masked_usage(
            "deepseek",
            "unknown-deepseek-model",
            "a" * 4000,
            "b" * 4000,
        )

        self.assertAlmostEqual(float(usage["estimated_cost_usd"] or 0.0), 0.00042, places=6)
        self.assertEqual(usage["cost_band"], "< $0.001")

    def test_usage_reporting_uses_current_deepseek_pricing(self) -> None:
        pricing = usage_reporting.lookup_model_pricing("deepseek", "deepseek-chat")
        cost = usage_reporting.estimate_cost_usd("deepseek", "deepseek-chat", 1_000_000, 1_000_000)

        self.assertIsNotNone(pricing)
        self.assertEqual(pricing["source"], "https://api-docs.deepseek.com/quick_start/pricing/")
        self.assertAlmostEqual(float(pricing["input"]), 0.14, places=6)
        self.assertAlmostEqual(float(pricing["output"]), 0.28, places=6)
        self.assertAlmostEqual(float(cost or 0.0), 0.42, places=6)


class ProviderCatalogProjectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_catalog_projection_exposes_deepseek_governance_notes(self) -> None:
        runtime_truth = {
            "workspace_id": "ws-1",
            "summary": {"provider_total": 1},
            "providers": [
                {
                    "id": "deepseek",
                    "label": "DeepSeek",
                    "state": "configured",
                    "default_model": "deepseek-chat",
                }
            ],
        }

        with patch(
            "server_modules.provider_catalog_service.provider_profiles.build_workspace_provider_connection_truth",
            return_value=runtime_truth,
        ):
            payload = await provider_catalog_service.list_workspace_provider_catalog(workspace_id="ws-1")

        deepseek = payload["providers"][0]
        self.assertEqual(deepseek["privacy_posture_summary"], deepseek["privacy_posture"])
        self.assertEqual(deepseek["residency_caveat"], deepseek["residency"])
        self.assertIn("enterprise_risk_note", deepseek)
        self.assertTrue(str(deepseek["enterprise_risk_note"]).strip())


if __name__ == "__main__":
    unittest.main()
