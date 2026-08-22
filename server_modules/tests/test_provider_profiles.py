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

        # "deepseek-chat"/"deepseek-reasoner" are retired (2026-07-24) and
        # deliberately excluded from the selectable catalog now — see this
        # module's own "deepseek" PROVIDER_CATALOG entry and
        # model_is_known_for_provider. Their real current successors carry
        # the identical rate.
        self.assertNotIn("deepseek-chat", models)
        self.assertNotIn("deepseek-reasoner", models)
        self.assertAlmostEqual(models["deepseek-v4-flash"]["input_cost_per_1k_usd"], 0.00014, places=8)
        self.assertAlmostEqual(models["deepseek-v4-flash"]["output_cost_per_1k_usd"], 0.00028, places=8)
        # v4-pro is genuinely more expensive than v4-flash — this is what
        # makes the served-vs-requested billing fix
        # (test_default_engine_credit_debit.py's ServedVsRequestedModel
        # BillingTests) a real price difference to get right, not a no-op.
        self.assertGreater(models["deepseek-v4-pro"]["input_cost_per_1k_usd"], models["deepseek-v4-flash"]["input_cost_per_1k_usd"])
        self.assertGreater(models["deepseek-v4-pro"]["output_cost_per_1k_usd"], models["deepseek-v4-flash"]["output_cost_per_1k_usd"])

    def test_retired_deepseek_model_ids_normalize_forward_via_alias_table(self) -> None:
        """PROVIDER_MODEL_ALIASES is the defense-in-depth half of the fix —
        any EXISTING caller still carrying the retired name (an old stored
        per-agent model_config, a script) resolves to the real current id
        instead of sending the dead name to the wire."""
        self.assertEqual(
            provider_profiles.normalize_provider_model_id("deepseek", "deepseek-chat"),
            "deepseek-v4-flash",
        )
        self.assertEqual(
            provider_profiles.normalize_provider_model_id("deepseek", "deepseek-reasoner"),
            "deepseek-v4-pro",
        )

    def test_model_is_known_for_provider_rejects_retired_deepseek_ids(self) -> None:
        """The other half — a NEW save of a retired id must fail loudly
        (fleet_tools.configure_agent's save-time validation), never be
        silently forwarded as free text. See CLAUDE.md's standing rule:
        stale model/provider config must fail loudly, never fall through
        to a default."""
        self.assertFalse(provider_profiles.model_is_known_for_provider("deepseek", "deepseek-chat"))
        self.assertFalse(provider_profiles.model_is_known_for_provider("deepseek", "deepseek-reasoner"))
        self.assertTrue(provider_profiles.model_is_known_for_provider("deepseek", "deepseek-v4-flash"))
        self.assertTrue(provider_profiles.model_is_known_for_provider("deepseek", "deepseek-v4-pro"))
        # Nonexistent id for a real, closed-catalog provider — rejected.
        self.assertFalse(provider_profiles.model_is_known_for_provider("deepseek", "deepseek-v5-ultra-nonexistent"))
        # Open-catalog providers: nothing to validate against, so anything
        # passes — a local Ollama pull or an Azure deployment name can
        # never be enumerated in advance.
        self.assertTrue(provider_profiles.model_is_known_for_provider("ollama", "some-locally-pulled-model"))
        self.assertTrue(provider_profiles.model_is_known_for_provider("azure_openai", "my-company-deployment-3"))
        self.assertTrue(provider_profiles.model_is_known_for_provider("custom_openai_compatible", "anything-at-all"))
        # Unknown provider: nothing to validate against, so it passes too —
        # a provider-level check is a separate concern from this one.
        self.assertTrue(provider_profiles.model_is_known_for_provider("not_a_real_provider", "whatever"))
        # Empty model string: nothing to validate — a separate "required"
        # check owns that.
        self.assertTrue(provider_profiles.model_is_known_for_provider("deepseek", ""))

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


class ReasoningEffortLevelsForModelTests(unittest.TestCase):
    """reasoning_effort_levels_for_model is the ONE signal
    openai_compat_adapter.py trusts before ever putting a reasoning
    parameter on the wire for a BYO-subscription turn — see this file's
    own PROVIDER_MODEL_CATALOG comments (and CLAUDE.md's "BYO-subscription
    model truth" entry) for the sourcing behind each expected answer."""

    def test_openai_reasoning_model_has_levels(self) -> None:
        levels = provider_profiles.reasoning_effort_levels_for_model("openai", "gpt-5.4-mini")
        self.assertIn("high", levels)

    def test_openai_gpt4o_has_no_levels(self) -> None:
        # gpt-4o is not a reasoning model — was wrongly marked
        # supports_reasoning True before this fix.
        self.assertEqual(provider_profiles.reasoning_effort_levels_for_model("openai", "gpt-4o"), [])
        self.assertEqual(provider_profiles.reasoning_effort_levels_for_model("openai", "gpt-4.1"), [])
        self.assertEqual(provider_profiles.reasoning_effort_levels_for_model("openai", "gpt-4.1-mini"), [])

    def test_gemini_2_5_pro_has_levels_but_1_5_pro_does_not(self) -> None:
        self.assertIn("high", provider_profiles.reasoning_effort_levels_for_model("gemini", "gemini-2.5-pro"))
        self.assertEqual(provider_profiles.reasoning_effort_levels_for_model("gemini", "gemini-1.5-pro"), [])
        self.assertEqual(provider_profiles.reasoning_effort_levels_for_model("gemini", "gemini-2.0-flash"), [])

    def test_xai_grok_4_family_has_no_wire_levels(self) -> None:
        # grok-4/-4-0709/-4-latest/-3 all reason with a fixed budget and
        # expose no settable reasoning_effort (docs.x.ai, verified
        # 2026-08-20).
        for model_id in ("grok-4", "grok-4-0709", "grok-4-latest", "grok-3"):
            self.assertEqual(
                provider_profiles.reasoning_effort_levels_for_model("xai", model_id), [], model_id,
            )

    def test_xai_grok_4_5_and_later_are_deliberately_not_hardcoded(self) -> None:
        # Second pass, 2026-08-20: xAI has shipped grok-4.5/4.6/4.20-multi-
        # agent with REAL reasoning_effort support since the first catalog
        # pass. They were briefly added here as hardcoded entries and then
        # reverted on the founder's own correction — verifying against a
        # document and hand-typing the result is still transcription, and
        # this codebase has a genuine live self-describing source for the
        # cli_subscription/Codex case (codex app-server's model/list RPC,
        # verified live, 2026-08-20) but none for xai's REST API. This
        # test pins the ABSENCE as deliberate, not an oversight to "fix"
        # by re-adding a table.
        for model_id in ("grok-4.5", "grok-4.6", "grok-4.20-multi-agent"):
            self.assertEqual(
                provider_profiles.reasoning_effort_levels_for_model("xai", model_id), [], model_id,
            )

    def test_model_is_known_for_provider_distinguishes_unknown_from_confirmed_unsupported(self) -> None:
        # The signal openai_compat_adapter's staleness-observability log
        # relies on: grok-4 is a CONFIRMED "no" (in the catalog, empty
        # reasoning_levels); a model this catalog has never heard of is a
        # genuinely different fact and must classify differently.
        self.assertTrue(provider_profiles.model_is_known_for_provider("xai", "grok-4"))
        # grok-4.6 is deliberately NOT in the catalog (see the test above)
        # — it must classify the same as any other live-discovered id this
        # catalog has never heard of, which is the whole point of the
        # distinction this function exists to make.
        self.assertFalse(provider_profiles.model_is_known_for_provider("xai", "grok-4.6"))
        self.assertFalse(
            provider_profiles.model_is_known_for_provider("xai", "grok-5-hypothetical-future-release"),
        )

    def test_unknown_model_returns_empty_never_guessed(self) -> None:
        self.assertEqual(
            provider_profiles.reasoning_effort_levels_for_model("openai", "some-model-nobody-has-heard-of"), [],
        )
        self.assertEqual(provider_profiles.reasoning_effort_levels_for_model("openai", ""), [])

    def test_unverified_wire_shape_providers_have_no_levels(self) -> None:
        # qwen/mistral/ollama_cloud may reason internally but this catalog
        # has no verified OpenAI-shaped reasoning_effort wire contract for
        # any of them yet — see each entry's own sourcing comment.
        self.assertEqual(provider_profiles.reasoning_effort_levels_for_model("qwen", "qwen-plus"), [])
        self.assertEqual(
            provider_profiles.reasoning_effort_levels_for_model("mistral", "mistral-large-latest"), [],
        )
        self.assertEqual(
            provider_profiles.reasoning_effort_levels_for_model("ollama_cloud", "gpt-oss:120b"), [],
        )

    def test_openrouter_carries_the_superset_it_documents(self) -> None:
        levels = provider_profiles.reasoning_effort_levels_for_model("openrouter", "openai/gpt-5.4")
        self.assertIn("high", levels)
        self.assertEqual(
            provider_profiles.reasoning_effort_levels_for_model("openrouter", "deepseek/deepseek-chat"), [],
        )


class GrokBuildCursorCliProviderCatalogTests(unittest.TestCase):
    """xAI Grok Build / Cursor CLI addition (2026-07-24) — the two new
    cli_subscription provider entries resolve correctly through the same
    catalog claude_code_cli/openai-codex use, and stay hidden from the
    workspace-facing (BYOK) catalog the same way claude_code_cli already is."""

    def test_xai_grok_cli_entry_resolves_and_aliases_to_xai(self) -> None:
        entry = provider_profiles.provider_catalog_entry("xai_grok_cli")
        self.assertTrue(entry, "xai_grok_cli must resolve to a real catalog entry")
        self.assertEqual(entry.get("alias_for"), "xai")
        self.assertTrue(entry.get("hidden"))
        self.assertEqual(entry.get("default_auth_mode"), "local_cli")
        self.assertIn("local_cli", {m["id"] for m in entry.get("auth_modes", [])})

    def test_cursor_cli_entry_resolves_hidden_with_no_alias(self) -> None:
        entry = provider_profiles.provider_catalog_entry("cursor_cli")
        self.assertTrue(entry, "cursor_cli must resolve to a real catalog entry")
        self.assertTrue(entry.get("hidden"))
        # Cursor CLI is a multi-vendor pass-through, not one vendor's model
        # family — unlike claude_code_cli/xai_grok_cli, it has no alias_for.
        self.assertNotIn("alias_for", entry)
        self.assertEqual(entry.get("default_auth_mode"), "local_cli")

    def test_both_new_providers_are_hidden_from_the_workspace_facing_catalog(self) -> None:
        # Same treatment as claude_code_cli: cli_subscription providers never
        # appear in the general BYOK/workspace provider catalog — they are
        # resolved directly by the Fleet gateway rail instead (fleet_tools.py
        # / agent_turn_runtime_service.py), never through provider_profiles'
        # credential-resolution machinery.
        self.assertNotIn("xai_grok_cli", provider_profiles.WORKSPACE_USER_FACING_AI_PROVIDERS)
        self.assertNotIn("cursor_cli", provider_profiles.WORKSPACE_USER_FACING_AI_PROVIDERS)
        self.assertNotIn("claude_code_cli", provider_profiles.WORKSPACE_USER_FACING_AI_PROVIDERS)

    def test_normalize_auth_mode_resolves_local_cli_for_both_new_providers(self) -> None:
        self.assertEqual(provider_profiles.normalize_auth_mode("xai_grok_cli"), "local_cli")
        self.assertEqual(provider_profiles.normalize_auth_mode("cursor_cli"), "local_cli")

    def test_resolve_provider_adapter_fails_loudly_for_the_new_provider_ids_directly(self) -> None:
        # These two ids are consumed ONLY through the Fleet cli_subscription
        # rail (which never calls resolve_provider_adapter at all — see
        # agent_turn_runtime_service.py's cli_subscription dispatch branch).
        # No GrokBuildCLIAdapter/CursorCLIAdapter was built (that would be a
        # DIFFERENT feature — Sage's own local-CLI direct-chat path, out of
        # scope for the Fleet gateway rail this addition wires). Confirm that
        # if anything ever DOES call this directly, it fails loudly with a
        # clear "Unsupported provider" error — never silently returns a
        # no-op adapter or crashes with an unrelated exception.
        with self.assertRaises(RuntimeError) as ctx:
            provider_profiles.resolve_provider_adapter("xai_grok_cli")
        self.assertIn("unsupported provider", str(ctx.exception).lower())
        with self.assertRaises(RuntimeError) as ctx2:
            provider_profiles.resolve_provider_adapter("cursor_cli")
        self.assertIn("unsupported provider", str(ctx2.exception).lower())


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
