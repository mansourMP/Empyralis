from __future__ import annotations

import unittest
from unittest.mock import patch

from server_modules import provider_catalog_service


class ProviderCatalogServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_workspace_provider_catalog_exposes_models_and_governance(self) -> None:
        connection_truth = {
            "workspace_id": "ws-1",
            "summary": {"provider_total": 2},
            "providers": [
                {
                    "id": "openai",
                    "label": "OpenAI",
                    "state": "active",
                    "default_model": "gpt-4o",
                    "profile_metadata": {
                        "cached_models": [
                            {
                                "id": "gpt-live-new",
                                "label": "GPT Live New",
                                "context_window_tokens": 256000,
                            }
                        ],
                        "cached_models_synced_at": "2099-01-01T00:00:00Z",
                        "cached_models_expires_at": "2099-01-01T01:00:00Z",
                    },
                },
                {
                    "id": "deepseek",
                    "label": "DeepSeek",
                    "state": "configured",
                    "default_model": "deepseek-chat",
                },
            ],
        }
        runtime_truth = {
            "workspace_id": "ws-1",
            "summary": {"provider_total": 2, "active": 2, "configured": 0, "setup_required": 0, "unavailable": 0, "degraded": 0},
            "providers": [
                {
                    "id": "openai",
                    "label": "OpenAI",
                    "state": "active",
                    "usable": True,
                    "configured": True,
                    "active": True,
                    "credential_sources": ["env_api_key"],
                    "active_source": "env_api_key",
                    "identity_owner": "platform_account",
                    "identity_owner_label": "Empyralis account",
                    "identity_boundary_note": "Platform sign-in stays separate from provider capabilities and machine-local sessions.",
                    "machine_bound": False,
                },
                {
                    "id": "deepseek",
                    "label": "DeepSeek",
                    "state": "active",
                    "usable": True,
                    "configured": True,
                    "active": True,
                    "credential_sources": ["env-deepseek"],
                    "active_source": "env-deepseek",
                    "identity_owner": "platform_account",
                    "identity_owner_label": "Empyralis account",
                    "identity_boundary_note": "Platform sign-in stays separate from provider capabilities and machine-local sessions.",
                    "machine_bound": False,
                },
            ],
        }

        with patch(
            "server_modules.provider_catalog_service.provider_profiles.build_workspace_provider_connection_truth",
            return_value=connection_truth,
        ), patch(
            "server_modules.provider_catalog_service.provider_profiles.build_provider_runtime_truth",
            return_value=runtime_truth,
        ):
            payload = await provider_catalog_service.list_workspace_provider_catalog(workspace_id="ws-1")

        self.assertEqual(payload["workspace_id"], "ws-1")
        self.assertEqual(payload["summary"]["provider_total"], 2)
        self.assertIn("model_tier_policy", payload)
        self.assertIn("tiers", payload["model_tier_policy"])
        providers = {item["id"]: item for item in payload["providers"]}
        self.assertIn("privacy_posture", providers["openai"])
        self.assertIn("jurisdiction", providers["openai"])
        self.assertTrue(any(model["id"] == "gpt-live-new" for model in providers["openai"]["models"]))
        live_model = next(model for model in providers["openai"]["models"] if model["id"] == "gpt-live-new")
        self.assertEqual(live_model["label"], "GPT Live New")
        self.assertEqual(live_model["context_window_tokens"], 256000)
        self.assertEqual(providers["openai"]["models_source"], "workspace_cached_models")
        self.assertEqual(providers["openai"]["models_expires_at"], "2099-01-01T01:00:00Z")
        self.assertEqual(
            providers["openai"]["provider_scopes"],
            ["sage_personal", "workspace_api", "studio_safe"],
        )
        self.assertTrue(providers["openai"]["sage_visible"])
        self.assertTrue(providers["openai"]["studio_visible"])
        self.assertTrue(any(model["id"] == "deepseek-chat" for model in providers["deepseek"]["models"]))
        self.assertEqual(providers["deepseek"]["state"], "active")
        self.assertEqual(providers["deepseek"]["active_source"], "env-deepseek")

    async def test_list_workspace_provider_catalog_marks_codex_as_sage_only(self) -> None:
        connection_truth = {
            "workspace_id": "ws-1",
            "summary": {"provider_total": 1},
            "providers": [
                {
                    "id": "openai-codex",
                    "kind": "provider",
                    "label": "OpenAI Codex",
                    "state": "active",
                    "default_model": "gpt-5.4",
                    "provider_scopes": ["sage_personal"],
                },
            ],
        }
        runtime_truth = {
            "workspace_id": "ws-1",
            "summary": {"provider_total": 1, "active": 1, "configured": 0, "setup_required": 0, "unavailable": 0, "degraded": 0},
            "providers": [
                {
                    "id": "openai-codex",
                    "label": "OpenAI Codex",
                    "state": "active",
                    "usable": True,
                    "configured": True,
                    "active": True,
                    "credential_sources": ["workspace_profile"],
                    "active_source": "workspace_profile",
                    "identity_owner": "workspace",
                    "identity_owner_label": "Workspace connection",
                    "identity_boundary_note": "Saved Codex transport only extends execution capability.",
                    "machine_bound": False,
                },
            ],
        }

        with patch(
            "server_modules.provider_catalog_service.provider_profiles.build_workspace_provider_connection_truth",
            return_value=connection_truth,
        ), patch(
            "server_modules.provider_catalog_service.provider_profiles.build_provider_runtime_truth",
            return_value=runtime_truth,
        ):
            payload = await provider_catalog_service.list_workspace_provider_catalog(workspace_id="ws-1")

        providers = {item["id"]: item for item in payload["providers"]}
        self.assertEqual(providers["openai-codex"]["provider_scopes"], ["sage_personal"])

    async def test_list_workspace_provider_catalog_keeps_ollama_tool_support_visible(self) -> None:
        connection_truth = {
            "workspace_id": "ws-1",
            "summary": {"provider_total": 1},
            "providers": [
                {
                    "id": "ollama",
                    "label": "Ollama",
                    "state": "configured",
                    "default_model": "llama3.2",
                    "identity_owner": "local_machine",
                    "identity_owner_label": "Local machine",
                    "identity_boundary_note": "This provider is bound to the local machine runtime and does not require a workspace credential.",
                    "active_source": "local_runtime",
                    "connection_kind": "machine_local_capability",
                    "connection_scope": "machine",
                    "connection_label": "This machine only",
                    "machine_bound": True,
                    "credential_sources": ["local_runtime"],
                },
            ],
        }
        runtime_truth = {
            "workspace_id": "ws-1",
            "summary": {"provider_total": 1, "active": 0, "configured": 1, "setup_required": 0, "unavailable": 0, "degraded": 0},
            "providers": [
                {
                    "id": "ollama",
                    "label": "Ollama",
                    "state": "configured",
                    "usable": False,
                    "configured": True,
                    "active": False,
                    "credential_sources": ["local-ollama"],
                    "active_source": "local-ollama",
                    "identity_owner": "local_machine",
                    "identity_owner_label": "Local machine",
                    "identity_boundary_note": "Local runtime stays on the paired machine.",
                    "machine_bound": True,
                },
            ],
        }

        with patch(
            "server_modules.provider_catalog_service.provider_profiles.build_workspace_provider_connection_truth",
            return_value=connection_truth,
        ), patch(
            "server_modules.provider_catalog_service.provider_profiles.build_provider_runtime_truth",
            return_value=runtime_truth,
        ):
            payload = await provider_catalog_service.list_workspace_provider_catalog(workspace_id="ws-1")

        providers = {item["id"]: item for item in payload["providers"]}
        models = {item["id"]: item for item in providers["ollama"]["models"]}
        self.assertTrue(providers["ollama"]["local_only"])
        self.assertEqual(providers["ollama"]["credential_plane"], "local_runtime")
        self.assertTrue(models["llama3.2"]["supports_tools"])
        self.assertIn("Tools", models["llama3.2"]["capability_labels"])

    def test_openai_codex_catalog_exposes_reasoning_levels(self) -> None:
        models = provider_catalog_service.provider_profiles.provider_model_catalog("openai-codex")
        gpt_54 = next((item for item in models if item.get("id") == "gpt-5.4"), None)

        self.assertIsNotNone(gpt_54)
        self.assertEqual(gpt_54["reasoning_levels"], ["low", "medium", "high", "xhigh"])

    def test_resolve_provider_model_selection_defaults_model_for_provider(self) -> None:
        selection = provider_catalog_service.resolve_provider_model_selection(provider="openai", model=None)

        self.assertEqual(selection, {"provider": "openai", "model": "gpt-5.4"})

    def test_resolve_provider_model_selection_defaults_to_gemini_25_flash(self) -> None:
        selection = provider_catalog_service.resolve_provider_model_selection(provider="gemini", model=None)

        self.assertEqual(selection, {"provider": "gemini", "model": "gemini-2.5-flash"})

    def test_resolve_provider_model_selection_defaults_to_live_anthropic_model(self) -> None:
        selection = provider_catalog_service.resolve_provider_model_selection(provider="anthropic", model=None)

        self.assertEqual(selection, {"provider": "anthropic", "model": "claude-sonnet-4-6"})

    def test_static_fallback_catalog_includes_current_frontier_models(self) -> None:
        openai_models = {item["id"]: item for item in provider_catalog_service.provider_profiles.provider_model_catalog("openai")}
        anthropic_models = {item["id"]: item for item in provider_catalog_service.provider_profiles.provider_model_catalog("anthropic")}
        gemini_models = {item["id"]: item for item in provider_catalog_service.provider_profiles.provider_model_catalog("gemini")}

        self.assertIn("gpt-5.5", openai_models)
        self.assertIn("gpt-5.4", openai_models)
        self.assertEqual(openai_models["gpt-5.5"]["context_window_tokens"], 1050000)
        self.assertIn("claude-opus-4-7", anthropic_models)
        self.assertIn("claude-sonnet-4-6", anthropic_models)
        self.assertEqual(anthropic_models["claude-sonnet-4-6"]["context_window_tokens"], 1000000)
        self.assertIn("gemini-3-pro-preview", gemini_models)
        self.assertIn("gemini-3-flash-preview", gemini_models)

    def test_resolve_provider_model_selection_normalizes_prefixed_model_ids(self) -> None:
        selection = provider_catalog_service.resolve_provider_model_selection(
            provider="anthropic",
            model="anthropic/claude-3-5-sonnet-20241022",
        )

        self.assertEqual(selection, {"provider": "anthropic", "model": "claude-3-5-sonnet-20241022"})

    def test_my_api_key_route_exposes_provider_and_model_selection(self) -> None:
        selection = provider_catalog_service.resolve_provider_model_selection(
            provider="openai",
            model="gpt-4.1",
        )

        self.assertEqual(selection["provider"], "openai")
        self.assertEqual(selection["model"], "gpt-4.1")

    def test_resolve_provider_model_selection_accepts_cached_live_model_ids(self) -> None:
        selection = provider_catalog_service.resolve_provider_model_selection(
            provider="anthropic",
            model="claude-sonnet-4-6",
            cached_models=[{"id": "claude-sonnet-4-6"}, {"id": "claude-opus-4-1"}],
        )

        self.assertEqual(selection, {"provider": "anthropic", "model": "claude-sonnet-4-6"})

    def test_cached_provider_model_ids_reads_workspace_profile_metadata(self) -> None:
        connection_truth = {
            "workspace_id": "ws-1",
            "providers": [
                {
                    "id": "openai",
                    "profile_metadata": {
                        "cached_models": [{"id": "gpt-live-new"}],
                    },
                },
            ],
        }
        with patch(
            "server_modules.provider_catalog_service.provider_profiles.build_workspace_provider_connection_truth",
            return_value=connection_truth,
        ):
            models = provider_catalog_service.cached_provider_model_ids(
                workspace_id="ws-1",
                provider="openai",
            )

        self.assertEqual(models, ["gpt-live-new"])

    async def test_openrouter_cached_models_keep_pricing_metadata(self) -> None:
        connection_truth = {
            "workspace_id": "ws-1",
            "summary": {"provider_total": 1},
            "providers": [
                {
                    "id": "openrouter",
                    "label": "OpenRouter",
                    "state": "active",
                    "default_model": "openai/gpt-live-new",
                    "profile_metadata": {
                        "cached_models": [
                            {
                                "id": "openai/gpt-live-new",
                                "label": "GPT Live New",
                                "context_window_tokens": 128000,
                                "input_cost_per_1k_usd": 0.001,
                                "output_cost_per_1k_usd": 0.004,
                                "supports_tools": True,
                                "supports_json": True,
                                "source": "openai_compatible_models_api",
                            }
                        ],
                        "cached_models_synced_at": "2099-01-01T00:00:00Z",
                    },
                },
            ],
        }
        runtime_truth = {
            "workspace_id": "ws-1",
            "summary": {"provider_total": 1, "active": 1, "configured": 0, "setup_required": 0, "unavailable": 0, "degraded": 0},
            "providers": [
                {
                    "id": "openrouter",
                    "label": "OpenRouter",
                    "state": "active",
                    "usable": True,
                    "configured": True,
                    "active": True,
                    "credential_sources": ["workspace_profile"],
                    "active_source": "workspace_profile",
                    "identity_owner": "workspace",
                },
            ],
        }

        with patch(
            "server_modules.provider_catalog_service.provider_profiles.build_workspace_provider_connection_truth",
            return_value=connection_truth,
        ), patch(
            "server_modules.provider_catalog_service.provider_profiles.build_provider_runtime_truth",
            return_value=runtime_truth,
        ):
            payload = await provider_catalog_service.list_workspace_provider_catalog(workspace_id="ws-1")

        providers = {item["id"]: item for item in payload["providers"]}
        model = providers["openrouter"]["models"][0]
        self.assertEqual(model["id"], "openai/gpt-live-new")
        self.assertEqual(model["input_cost_per_1k_usd"], 0.001)
        self.assertEqual(model["output_cost_per_1k_usd"], 0.004)
        self.assertTrue(model["supports_tools"])
        self.assertTrue(model["supports_json"])

    def test_provider_profiles_normalize_anthropic_latest_alias_to_live_model(self) -> None:
        normalized = provider_catalog_service.provider_profiles.normalize_provider_model_id(
            "anthropic",
            "claude-3-7-sonnet-latest",
            fallback_to_default=True,
        )

        self.assertEqual(normalized, "claude-3-7-sonnet-20250219")

    def test_resolve_provider_model_selection_rejects_unknown_models(self) -> None:
        with self.assertRaisesRegex(ValueError, "not supported"):
            provider_catalog_service.resolve_provider_model_selection(
                provider="deepseek",
                model="gpt-4o",
            )

    def test_model_policy_overlay_can_disable_model_without_code_catalog_rewrite(self) -> None:
        overlay = '{"deepseek":{"deepseek-v4-flash":{"enabled":false}}}'
        with patch.dict("os.environ", {"EMPYRALIS_MODEL_CATALOG_POLICY_JSON": overlay}, clear=False):
            provider_catalog_service.provider_profiles._platform_provider_catalog_config.cache_clear()
            self.addCleanup(provider_catalog_service.provider_profiles._platform_provider_catalog_config.cache_clear)
            models = provider_catalog_service.provider_profiles.provider_model_catalog("deepseek")
            self.assertNotIn("deepseek-v4-flash", {item["id"] for item in models})
            with self.assertRaisesRegex(ValueError, "disabled"):
                provider_catalog_service.resolve_provider_model_selection(
                    provider="deepseek",
                    model="deepseek-v4-flash",
                    surface="sage",
                    payer="platform_credits",
                )

    def test_model_route_policy_rejects_byok_first_providers_for_platform_credits(self) -> None:
        for provider, model in (
            ("openrouter", "openai/gpt-5.2"),
            ("groq", "llama-3.3-70b-versatile"),
            ("azure_openai", "deployment-name"),
            ("custom_openai_compatible", "custom-model"),
        ):
            with self.subTest(provider=provider):
                with self.assertRaisesRegex(ValueError, "cannot use Empyralis credits"):
                    provider_catalog_service.assert_model_route_policy(
                        provider=provider,
                        model=model,
                        surface="studio",
                        payer="platform_credits",
                    )

    def test_model_route_policy_keeps_deepseek_platform_paid_when_priced(self) -> None:
        policy = provider_catalog_service.assert_model_route_policy(
            provider="deepseek",
            model="deepseek-chat",
            surface="sage",
            payer="platform_credits",
        )

        self.assertTrue(policy["platform_paid_allowed"])
        self.assertTrue(policy["pricing_known"])

    def test_model_route_policy_rejects_non_deepseek_platform_credit_models(self) -> None:
        with self.assertRaisesRegex(ValueError, "Empyralis credits only support"):
            provider_catalog_service.assert_model_route_policy(
                provider="deepseek",
                model="deepseek-v4-flash",
                surface="sage",
                payer="platform_credits",
            )

    def test_resolve_empyralis_model_tier_selection_hides_raw_route_by_default(self) -> None:
        route = provider_catalog_service.resolve_empyralis_model_tier_selection(public_tier="pro")

        self.assertEqual(route["public_tier"], "pro")
        self.assertIsNone(route["internal_provider"])
        self.assertIsNone(route["internal_model"])

    def test_resolve_empyralis_model_tier_selection_exposes_admin_route(self) -> None:
        route = provider_catalog_service.resolve_empyralis_model_tier_selection(
            public_tier="pro",
            include_internal_route=True,
        )

        self.assertEqual(route["internal_provider"], "deepseek")
        self.assertEqual(route["internal_model"], "deepseek-v4-pro")
        self.assertEqual(route["reasoning_effort"], "high")


if __name__ == "__main__":
    unittest.main()
