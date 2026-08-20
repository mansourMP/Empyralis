import unittest
from unittest.mock import patch

from server_modules import connectors_core


class ConnectorsCoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_providers_includes_openai_codex(self):
        result = await connectors_core.list_providers()

        providers = result.get("providers", [])
        provider_ids = {item.get("id") for item in providers}
        self.assertIn("openai-codex", provider_ids)
        self.assertNotIn("google_workspace", provider_ids)
        openai_item = next(item for item in providers if item.get("id") == "openai")
        self.assertEqual(openai_item.get("kind"), "provider")
        self.assertIn(openai_item.get("state"), {"active", "configured", "setup_required", "unavailable", "degraded"})
        self.assertEqual(openai_item.get("identity_owner"), "workspace")
        self.assertEqual(openai_item.get("connection_kind"), "workspace_provider_connection")
        self.assertEqual(openai_item.get("connection_scope"), "workspace")

    async def test_list_connectors_includes_alias_entries(self):
        result = await connectors_core.list_connectors()

        connectors = {item.get("id"): item for item in result.get("connectors", [])}
        self.assertEqual(connectors["gmail"]["parent"], "google_workspace")
        self.assertEqual(connectors["google_calendar"]["parent"], "google_workspace")
        self.assertEqual(connectors["google_drive"]["parent"], "google_workspace")
        self.assertEqual(connectors["outlook"]["parent"], "microsoft_365")
        self.assertEqual(connectors["outlook_calendar"]["parent"], "microsoft_365")

    async def test_get_model_alias_catalog_returns_models_list(self):
        result = await connectors_core.get_model_alias_catalog()

        self.assertIn("models", result)
        self.assertIsInstance(result["models"], list)
        self.assertTrue(any(item.get("alias") == "gpt-4o-mini" for item in result["models"]))

    async def test_serialize_profile_redacts_sensitive_metadata(self):
        profile = connectors_core._serialize_profile(
            {
                "id": "profile-1",
                "provider": "openai",
                "label": "OpenAI",
                "workspace_id": "default",
                "priority": 100,
                "enabled": True,
                "metadata": {
                    "authorization": "Bearer abc.def.ghi",
                    "session_cookie": "sid=123",
                    "safe": "ok",
                },
            }
        )

        self.assertEqual(profile["metadata"]["authorization"], "[redacted]")
        self.assertEqual(profile["metadata"]["session_cookie"], "[redacted]")
        self.assertEqual(profile["metadata"]["safe"], "ok")


class GetProviderModelsCredentialResolutionTests(unittest.IsolatedAsyncioTestCase):
    """get_provider_models is the live-discovery source
    fleet-model-config.ts's useByokModelCatalog now calls (2026-08-20 fix).
    Before this fix, calling it with no explicit credential_id/profile_id
    resolved a saved credential ONLY for "openai" and "ollama_cloud" —
    every other BYOK provider (gemini/xai/groq/openrouter/qwen/mistral/
    bedrock) always reported credential_required=True even when the
    workspace genuinely had a key saved, because resolve_default_vault_
    credential was never called for them at all. These tests pin the
    generic fallback that closed that gap."""

    async def test_gemini_resolves_its_own_saved_default_credential(self):
        # Before the fix this returned credential_required=True
        # unconditionally for gemini — resolve_default_vault_credential
        # was only ever called for "openai"/"ollama_cloud".
        with patch.object(
            connectors_core, "resolve_default_vault_credential",
            return_value={"api_key": "g-key"},
        ) as mock_resolve, patch.object(
            connectors_core, "resolve_provider_adapter",
        ) as mock_adapter:
            fake_adapter = mock_adapter.return_value
            fake_adapter_instance = fake_adapter
            mock_adapter.return_value = ("gemini", "api_key", fake_adapter_instance)
            fake_adapter_instance.list_models.return_value = ["gemini-2.5-flash", "gemini-2.5-pro"]

            result = await connectors_core.get_provider_models("gemini", workspace_id="ws-1")

        mock_resolve.assert_called_once_with("gemini", "ws-1")
        self.assertEqual(result["models"], ["gemini-2.5-flash", "gemini-2.5-pro"])
        self.assertNotIn("credential_required", result)

    async def test_no_saved_credential_reports_credential_required_not_an_error(self):
        with patch.object(
            connectors_core, "resolve_default_vault_credential", side_effect=Exception("no default credential"),
        ):
            result = await connectors_core.get_provider_models("xai", workspace_id="ws-1")

        self.assertEqual(result, {"provider": "xai", "models": [], "credential_required": True})

    async def test_explicit_credential_id_still_wins_over_the_default_lookup(self):
        with patch.object(
            connectors_core, "resolve_vault_credential", return_value={"api_key": "explicit"},
        ) as mock_explicit, patch.object(
            connectors_core, "resolve_default_vault_credential",
        ) as mock_default, patch.object(
            connectors_core, "resolve_provider_adapter",
        ) as mock_adapter:
            mock_adapter.return_value = ("groq", "api_key", mock_adapter.return_value)
            mock_adapter.return_value[2].list_models.return_value = ["llama-3.3-70b-versatile"]

            await connectors_core.get_provider_models("groq", credential_id="cred-1", workspace_id="ws-1")

        mock_explicit.assert_called_once_with("cred-1", "ws-1")
        mock_default.assert_not_called()

    async def test_openai_still_falls_back_to_the_hosted_env_bearer(self):
        # openai's extra, provider-specific fallback (a host-level env var
        # for boxes never asked to paste a key into the vault) must
        # survive the generalization -- it now runs only AFTER the
        # generic vault lookup has been tried and come up empty.
        with patch.object(
            connectors_core, "resolve_default_vault_credential", side_effect=Exception("none saved"),
        ), patch.object(
            connectors_core, "_resolve_hosted_openai_bearer", return_value=("env-key", "env"),
        ) as mock_env, patch.object(
            connectors_core, "_openai_env_credentials", return_value={"api_key": "env-key"},
        ), patch.object(
            connectors_core, "resolve_provider_adapter",
        ) as mock_adapter:
            mock_adapter.return_value = ("openai", "api_key", mock_adapter.return_value)
            mock_adapter.return_value[2].list_models.return_value = ["gpt-5.4-mini"]

            result = await connectors_core.get_provider_models("openai", workspace_id="ws-1")

        mock_env.assert_called_once()
        self.assertEqual(result["models"], ["gpt-5.4-mini"])


if __name__ == "__main__":
    unittest.main()
