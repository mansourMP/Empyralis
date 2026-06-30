import unittest
from unittest.mock import MagicMock, patch
from http.client import IncompleteRead

from fastapi import HTTPException

from server_modules import connectors_actions, provider_profiles, runtime_models, workspace_admin_service
from server_modules.schemas import ConnectorCreate
from server_modules.runtime_models import CredentialUpsertRequest, configure_runtime_model_context


class _FakeUUID:
    hex = "1234567890abcdef1234567890abcdef"

    def __init__(self, value: str) -> None:
        self.value = value

    def __str__(self) -> str:
        return self.value


class ProviderValidationMessageTests(unittest.TestCase):
    def setUp(self):
        provider_profiles._init()

    def test_classify_profile_failure_marks_rate_limits_as_retryable_backpressure(self):
        result = provider_profiles.classify_profile_failure("429 Rate limit exceeded for this token.")

        self.assertEqual(result["failure_class"], "rate_limited")
        self.assertTrue(result["retryable"])
        self.assertTrue(result["backpressure"])
        self.assertGreaterEqual(result["cooldown_seconds"], 30)

    def test_classify_profile_failure_marks_auth_errors_as_non_retryable(self):
        result = provider_profiles.classify_profile_failure("401 Unauthorized API key.")

        self.assertEqual(result["failure_class"], "auth")
        self.assertFalse(result["retryable"])
        self.assertFalse(result["backpressure"])
        self.assertGreaterEqual(result["cooldown_seconds"], 60)

    def test_resolve_provider_adapter_keeps_openai_direct_for_codex_token_credentials(self):
        resolved_provider, adapter_key, adapter = provider_profiles.resolve_provider_adapter(
            "openai",
            {"oauth_token": "token-123", "account_id": "acct-123", "credential_type": "codex_token"},
        )

        self.assertEqual(resolved_provider, "openai")
        self.assertEqual(adapter_key, "openai")
        self.assertIsInstance(adapter, provider_profiles.OpenAIAdapter)

    def test_resolve_provider_adapter_uses_codex_transport_when_explicitly_selected(self):
        resolved_provider, adapter_key, adapter = provider_profiles.resolve_provider_adapter(
            "openai-codex",
            {"oauth_token": "token-123", "account_id": "acct-123", "credential_type": "codex_token"},
        )

        self.assertEqual(resolved_provider, "openai-codex")
        self.assertEqual(adapter_key, "openai-codex")
        self.assertIsInstance(adapter, provider_profiles.OpenAICodexAdapter)

    @patch("server_modules.provider_profiles.http_json_request")
    def test_openai_oauth_token_validate_uses_standard_api_probe(self, http_json_request_mock):
        http_json_request_mock.return_value = {
            "status": 200,
            "json": {"data": []},
            "text": "",
        }
        result = provider_profiles.OpenAIAdapter().validate(
            {"oauth_token": "token-123", "auth_mode": "oauth_token"}
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], 200)
        self.assertIn("valid", result["message"].lower())
        http_json_request_mock.assert_called_once()

    @patch("server_modules.provider_profiles.http_json_request")
    def test_openai_codex_list_models_returns_codex_catalog_without_probe(self, http_json_request_mock):
        models = provider_profiles.OpenAICodexAdapter().list_models(
            {"oauth_token": "token-123", "account_id": "acct-123", "credential_type": "codex_token"}
        )

        self.assertEqual(models, provider_profiles.OPENAI_CODEX_MODEL_CATALOG)
        http_json_request_mock.assert_not_called()

    @patch("server_modules.provider_profiles.http_json_request")
    def test_openai_oauth_token_list_models_uses_standard_api_catalog(self, http_json_request_mock):
        http_json_request_mock.return_value = {
            "status": 200,
            "json": {
                "data": [
                    {"id": "gpt-4o"},
                    {"id": "gpt-4o-mini"},
                ]
            },
            "text": "",
        }
        models = provider_profiles.OpenAIAdapter().list_models(
            {"oauth_token": "token-123", "auth_mode": "oauth_token"}
        )

        self.assertEqual(models, ["gpt-4o", "gpt-4o-mini"])
        http_json_request_mock.assert_called_once()

    @patch("server_modules.provider_profiles.http_json_request")
    def test_openai_compatible_list_model_records_keeps_provider_pricing_metadata(self, http_json_request_mock):
        http_json_request_mock.return_value = {
            "status": 200,
            "json": {
                "data": [
                    {
                        "id": "openai/gpt-live-new",
                        "name": "GPT Live New",
                        "context_length": 128000,
                        "pricing": {"prompt": "0.000001", "completion": "0.000004"},
                        "supported_parameters": ["tools", "response_format"],
                    }
                ]
            },
            "text": "",
        }

        records = provider_profiles.OpenAICompatibleAdapter("openrouter", "OpenRouter").list_model_records(
            {"api_key": "sk-or-test"}
        )

        self.assertEqual(records[0]["id"], "openai/gpt-live-new")
        self.assertEqual(records[0]["context_window_tokens"], 128000)
        self.assertEqual(records[0]["input_cost_per_1k_usd"], 0.001)
        self.assertEqual(records[0]["output_cost_per_1k_usd"], 0.004)
        self.assertTrue(records[0]["supports_tools"])
        self.assertTrue(records[0]["supports_json"])

    @patch("server_modules.provider_profiles.http_json_request")
    def test_azure_openai_adapter_uses_deployment_url_and_api_version(self, http_json_request_mock):
        http_json_request_mock.return_value = {
            "status": 200,
            "json": {"choices": [{"message": {"content": "hello"}}]},
            "text": "",
        }

        reply = provider_profiles.OpenAICompatibleAdapter("azure_openai", "Azure OpenAI").generate(
            "system",
            "hello",
            "deployment-a",
            {
                "api_key": "sk-azure",
                "endpoint": "https://example.openai.azure.com",
                "api_version": "2024-10-21",
            },
        )

        self.assertEqual(reply, "hello")
        args, kwargs = http_json_request_mock.call_args
        self.assertEqual(
            args[0],
            "https://example.openai.azure.com/openai/deployments/deployment-a/chat/completions?api-version=2024-10-21",
        )
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer sk-azure")

    def test_azure_openai_adapter_requires_api_version(self):
        with self.assertRaisesRegex(RuntimeError, "api_version"):
            provider_profiles.OpenAICompatibleAdapter("azure_openai", "Azure OpenAI").generate(
                "system",
                "hello",
                "deployment-a",
                {
                    "api_key": "sk-azure",
                    "endpoint": "https://example.openai.azure.com",
                },
            )

    @patch("server_modules.provider_profiles.http_json_request")
    def test_anthropic_validate_uses_models_endpoint(self, http_json_request_mock):
        http_json_request_mock.return_value = {
            "status": 200,
            "json": {"data": [{"id": "claude-3-7-sonnet-20250219"}]},
            "text": "",
        }

        result = provider_profiles.AnthropicAdapter().validate({"api_key": "test-key"})

        self.assertTrue(result["ok"])
        args, kwargs = http_json_request_mock.call_args
        self.assertEqual(args[0], "https://api.anthropic.com/v1/models")
        self.assertNotIn("payload", kwargs)

    @patch("server_modules.provider_profiles.shutil.which", return_value="/usr/bin/curl")
    @patch("server_modules.provider_profiles._curl_json_request")
    @patch("server_modules.provider_profiles.http_json_request")
    def test_anthropic_validate_falls_back_to_curl_on_incomplete_read(
        self,
        http_json_request_mock,
        curl_json_request_mock,
        _which_mock,
    ):
        http_json_request_mock.side_effect = IncompleteRead(b"", 0)
        curl_json_request_mock.return_value = {
            "status": 200,
            "json": {"data": [{"id": "claude-3-7-sonnet-20250219"}]},
            "text": "",
        }

        result = provider_profiles.AnthropicAdapter().validate({"api_key": "test-key"})

        self.assertTrue(result["ok"])
        curl_json_request_mock.assert_called_once()

    @patch("server_modules.provider_profiles.shutil.which", return_value="/usr/bin/curl")
    @patch("server_modules.provider_profiles._curl_json_request")
    @patch("server_modules.provider_profiles.http_json_request")
    def test_anthropic_list_models_falls_back_to_curl_on_incomplete_read(
        self,
        http_json_request_mock,
        curl_json_request_mock,
        _which_mock,
    ):
        http_json_request_mock.side_effect = IncompleteRead(b"", 0)
        curl_json_request_mock.return_value = {
            "status": 200,
            "json": {"data": [{"id": "claude-3-7-sonnet-20250219"}, {"id": "claude-3-5-haiku-20241022"}]},
            "text": "",
        }

        models = provider_profiles.AnthropicAdapter().list_models({"api_key": "test-key"})

        self.assertEqual(models, ["claude-3-5-haiku-20241022", "claude-3-7-sonnet-20250219"])
        curl_json_request_mock.assert_called_once()

    @patch("server_modules.provider_profiles._openai_bearer_from_credentials", return_value="token-123")
    @patch("server_modules.provider_profiles.http_json_request")
    def test_openai_validate_returns_real_error_message_on_non_200_for_standard_api_tokens(
        self,
        http_json_request_mock,
        _openai_bearer_mock,
    ):
        http_json_request_mock.return_value = {
            "status": 429,
            "json": {"error": {"message": "Rate limit exceeded for this token."}},
            "text": '{"error":{"message":"Rate limit exceeded for this token."}}',
        }

        result = provider_profiles.OpenAIAdapter().validate({"access_token": "token-123", "auth_mode": "access_token"})

        self.assertEqual(result["status"], 429)
        self.assertFalse(result["ok"])
        self.assertIn("Rate limit exceeded", result["message"])

    @patch("server_modules.provider_profiles._load_openai_codex_transport")
    def test_openai_codex_generate_uses_codex_transport(self, load_transport_mock):
        worker_llm = MagicMock()
        worker_llm.openai_codex_backend_text.return_value = ("READY", {"prompt_tokens": 1}, "gpt-5.4", "")
        load_transport_mock.return_value = worker_llm

        result = provider_profiles.OpenAICodexAdapter().generate(
            "system",
            "user",
            "gpt-5.4",
            {"oauth_token": "token-123", "account_id": "acct-123", "credential_type": "codex_token"},
        )

        self.assertEqual(result, "READY")
        worker_llm.openai_codex_backend_text.assert_called_once()


class CredentialVaultFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        provider_profiles._init()
        configure_runtime_model_context(
            memory_max_text_chars=2400,
            normalize_memory_bucket=lambda value, required=True: value,
            normalize_action_id=lambda value: str(value or "").strip().lower(),
            provider_catalog=provider_profiles.PROVIDER_CATALOG,
            connector_catalog={},
        )

    @patch("server_modules.connectors_actions.save_vault")
    @patch("server_modules.connectors_actions.load_vault", return_value={})
    @patch("server_modules.connectors_actions._openssl_encrypt", return_value="encrypted-payload")
    @patch("server_modules.connectors_actions._provider_public_metadata", return_value={"auth_mode": "oauth_token"})
    @patch("server_modules.connectors_actions.resolve_provider_adapter", side_effect=AssertionError("validation should be skipped"))
    async def test_create_vault_credential_can_skip_validation(
        self,
        _resolve_provider_adapter_mock,
        _provider_public_metadata_mock,
        _openssl_encrypt_mock,
        load_vault_mock,
        save_vault_mock,
    ):
        body = CredentialUpsertRequest(
            label="OpenAI / Codex on this Mac",
            provider="openai",
            workspace_id="default",
            mode="byok",
            credentials={"oauth_token": "token-123", "auth_mode": "oauth_token"},
            metadata={"import_source": "codex_auth_file"},
            skip_validation=True,
        )

        result = await connectors_actions.create_vault_credential(body)

        self.assertEqual(result["provider"], "openai")
        self.assertEqual(result["models_preview"], [])
        saved_vault = save_vault_mock.call_args.args[0]
        self.assertEqual(len(saved_vault["credentials"]), 1)
        self.assertEqual(saved_vault["credentials"][0]["metadata"]["import_source"], "codex_auth_file")
        load_vault_mock.assert_called_once()

    @patch("server_modules.connectors_actions.resolve_vault_credential", return_value={"_provider": "openai", "oauth_token": "token-123"})
    @patch("server_modules.connectors_actions.resolve_provider_adapter")
    async def test_test_vault_credential_does_not_list_models_when_validation_fails(
        self,
        resolve_provider_adapter_mock,
        _resolve_vault_credential_mock,
    ):
        adapter = MagicMock()
        adapter.validate.return_value = {
            "ok": False,
            "status": 429,
            "message": "Rate limit exceeded for this token.",
        }
        adapter.list_models.side_effect = AssertionError("model listing should not run when validation fails")
        resolve_provider_adapter_mock.return_value = ("openai", {}, adapter)

        result = await connectors_actions.test_vault_credential("cred-123", workspace_id="default")

        self.assertEqual(result["provider"], "openai")
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], 429)
        self.assertEqual(result["models_preview"], [])
        adapter.list_models.assert_not_called()

    @patch("server_modules.connectors_actions.save_vault")
    @patch("server_modules.connectors_actions.load_vault", return_value={})
    @patch("server_modules.connectors_actions._openssl_encrypt", return_value="encrypted-payload")
    @patch("server_modules.connectors_actions._provider_public_metadata", return_value={"auth_mode": "api_key"})
    @patch("server_modules.connectors_actions.resolve_provider_adapter")
    async def test_create_vault_credential_does_not_list_models_on_successful_validation(
        self,
        resolve_provider_adapter_mock,
        _provider_public_metadata_mock,
        _openssl_encrypt_mock,
        load_vault_mock,
        save_vault_mock,
    ):
        adapter = MagicMock()
        adapter.validate.return_value = {
            "ok": True,
            "status": 200,
            "message": "Anthropic credential is valid.",
        }
        adapter.list_models.side_effect = AssertionError("model listing should not run during credential save")
        resolve_provider_adapter_mock.return_value = ("anthropic", {}, adapter)

        body = CredentialUpsertRequest(
            label="Sage Anthropic",
            provider="anthropic",
            workspace_id="default",
            mode="byok",
            credentials={"api_key": "sk-ant-test", "auth_mode": "api_key"},
        )

        result = await connectors_actions.create_vault_credential(body)

        self.assertEqual(result["provider"], "anthropic")
        self.assertEqual(result["models_preview"], [])
        adapter.validate.assert_called_once()
        adapter.list_models.assert_not_called()
        load_vault_mock.assert_called_once()
        save_vault_mock.assert_called_once()

    @patch("server_modules.connectors_actions.save_vault")
    @patch(
        "server_modules.connectors_actions.load_vault",
        return_value={
            "credentials": [
                {
                    "id": "connector-google",
                    "label": "Google Workspace",
                    "provider": "google_workspace",
                    "workspace_id": "default",
                    "metadata": {},
                }
            ]
        },
    )
    @patch(
        "server_modules.connectors_actions.resolve_vault_credential",
        return_value={"_provider": "google_workspace"},
    )
    @patch(
        "server_modules.connectors_actions.validate_google_workspace_connector",
        side_effect=RuntimeError("Google Workspace CLI token is not valid."),
    )
    async def test_test_connector_vault_persists_failed_google_verification_as_unusable(
        self,
        _validate_google_workspace_mock,
        _resolve_vault_credential_mock,
        _load_vault_mock,
        save_vault_mock,
    ):
        with self.assertRaises(HTTPException) as excinfo:
            await connectors_actions.test_connector_vault("connector-google", workspace_id="default")

        self.assertEqual(excinfo.exception.status_code, 400)
        saved_vault = save_vault_mock.call_args.args[0]
        saved_entry = saved_vault["credentials"][0]
        verification = saved_entry["metadata"]["capability_verification"]
        self.assertFalse(verification["authenticated"])
        self.assertFalse(verification["runtime_usable"])
        self.assertEqual(verification["write_actions"], [])


class WorkspaceProviderCredentialFlowTests(unittest.IsolatedAsyncioTestCase):
    @patch("server_modules.workspace_admin_service._cached_provider_model_metadata", return_value={})
    @patch("server_modules.workspace_admin_service.connectors_core.upsert_provider_profile")
    @patch("server_modules.workspace_admin_service.connectors_core.list_provider_profiles", return_value={"items": []})
    @patch("server_modules.workspace_admin_service.connectors_actions.create_vault_credential")
    @patch("server_modules.workspace_admin_service._normalize_provider_id", return_value="anthropic")
    @patch("server_modules.workspace_admin_service._provider_requires_secret", return_value=True)
    @patch("server_modules.workspace_admin_service._enforce_owner_scope", return_value="ws-1")
    async def test_upsert_workspace_provider_credential_does_not_persist_model_when_not_explicitly_selected(
        self,
        _enforce_owner_scope_mock,
        _provider_requires_secret_mock,
        _normalize_provider_id_mock,
        create_vault_credential_mock,
        _list_provider_profiles_mock,
        upsert_provider_profile_mock,
        _cached_provider_model_metadata_mock,
    ):
        create_vault_credential_mock.return_value = {"id": "cred-1"}
        upsert_provider_profile_mock.return_value = {"item": {"id": "profile-1", "model": None}}

        await workspace_admin_service.upsert_workspace_provider_credential(
            workspace_id="ws-1",
            current_user={"user_id": "owner-1"},
            provider="anthropic",
            api_key="sk-ant-test",
            model=None,
        )

        request = upsert_provider_profile_mock.call_args.args[0]
        self.assertIsNone(request.model)

    @patch("server_modules.workspace_admin_service.connectors_core.upsert_provider_profile")
    @patch("server_modules.workspace_admin_service.connectors_core.list_provider_profiles", return_value={"items": []})
    @patch("server_modules.workspace_admin_service.connectors_actions.create_vault_credential")
    @patch("server_modules.workspace_admin_service.provider_profiles_service.resolve_vault_credential", return_value={"api_key": "sk-ant-test"})
    @patch("server_modules.workspace_admin_service.provider_profiles_service.resolve_provider_adapter")
    @patch("server_modules.workspace_admin_service._enforce_owner_scope", return_value="ws-1")
    async def test_upsert_workspace_provider_credential_persists_dynamic_cached_models(
        self,
        _enforce_owner_scope_mock,
        resolve_provider_adapter_mock,
        _resolve_vault_credential_mock,
        create_vault_credential_mock,
        _list_provider_profiles_mock,
        upsert_provider_profile_mock,
    ):
        adapter = MagicMock()
        adapter.list_models.return_value = ["claude-sonnet-4-6", "claude-opus-4-1"]
        resolve_provider_adapter_mock.return_value = ("anthropic", "anthropic", adapter)
        create_vault_credential_mock.return_value = {"id": "cred-1"}
        upsert_provider_profile_mock.return_value = {"item": {"id": "profile-1"}}

        await workspace_admin_service.upsert_workspace_provider_credential(
            workspace_id="ws-1",
            current_user={"user_id": "owner-1"},
            provider="anthropic",
            api_key="sk-ant-test",
            model=None,
        )

        request = upsert_provider_profile_mock.call_args.args[0]
        self.assertEqual(
            [item["id"] for item in request.metadata["cached_models"]],
            ["claude-sonnet-4-6", "claude-opus-4-1"],
        )
        self.assertEqual(request.metadata["cached_models_source"], "provider_adapter")
        self.assertIn("cached_models_expires_at", request.metadata)
        self.assertIn("cached_models_provider_fingerprint", request.metadata)
        self.assertIsNone(request.metadata["cached_models_error"])

    @patch("server_modules.workspace_admin_service.connectors_core.upsert_provider_profile")
    @patch("server_modules.workspace_admin_service.connectors_core.list_provider_profiles", return_value={"items": []})
    @patch("server_modules.workspace_admin_service.connectors_actions.create_vault_credential")
    @patch("server_modules.workspace_admin_service.provider_profiles_service.resolve_vault_credential", return_value={"oauth_token": "codex-token", "auth_mode": "oauth_token"})
    @patch("server_modules.workspace_admin_service.provider_profiles_service.resolve_provider_adapter")
    @patch("server_modules.workspace_admin_service._enforce_owner_scope", return_value="ws-1")
    async def test_upsert_workspace_provider_credential_maps_codex_secret_to_oauth_token(
        self,
        _enforce_owner_scope_mock,
        resolve_provider_adapter_mock,
        _resolve_vault_credential_mock,
        create_vault_credential_mock,
        _list_provider_profiles_mock,
        upsert_provider_profile_mock,
    ):
        adapter = MagicMock()
        adapter.list_models.return_value = ["gpt-5.4", "gpt-5.3-codex"]
        resolve_provider_adapter_mock.return_value = ("openai-codex", "openai-codex", adapter)
        create_vault_credential_mock.return_value = {"id": "cred-codex"}
        upsert_provider_profile_mock.return_value = {"item": {"id": "profile-1"}}

        await workspace_admin_service.upsert_workspace_provider_credential(
            workspace_id="ws-1",
            current_user={"user_id": "owner-1"},
            provider="openai-codex",
            api_key="codex-token",
            model=None,
        )

        request = create_vault_credential_mock.call_args.args[0]
        self.assertEqual(
            request.credentials,
            {"oauth_token": "codex-token", "auth_mode": "oauth_token"},
        )
        profile_request = upsert_provider_profile_mock.call_args.args[0]
        self.assertEqual(profile_request.auth_mode, "oauth_token")
        self.assertEqual(
            [item["id"] for item in profile_request.metadata["cached_models"]],
            ["gpt-5.4", "gpt-5.3-codex"],
        )

    @patch("server_modules.workspace_admin_service.provider_profiles_service.resolve_vault_credential", return_value={"api_key": "sk-ant-test"})
    @patch("server_modules.workspace_admin_service.provider_profiles_service.resolve_provider_adapter")
    def test_cached_provider_model_metadata_reuses_fresh_cache_with_matching_fingerprint(
        self,
        resolve_provider_adapter_mock,
        _resolve_vault_credential_mock,
    ):
        existing = {
            "cached_models": [{"id": "claude-cached"}],
            "cached_models_source": "provider_adapter",
            "cached_models_synced_at": "2099-01-01T00:00:00Z",
            "cached_models_expires_at": "2099-01-01T01:00:00Z",
            "cached_models_error": None,
            "cached_models_provider_fingerprint": workspace_admin_service._credential_fingerprint(
                "anthropic",
                {"api_key": "sk-ant-test"},
            ),
        }

        result = workspace_admin_service._cached_provider_model_metadata(
            "anthropic",
            "ws-1",
            "cred-1",
            existing_metadata=existing,
        )

        self.assertEqual(result["cached_models"], [{"id": "claude-cached"}])
        resolve_provider_adapter_mock.assert_not_called()

    @patch("server_modules.workspace_admin_service.provider_profiles_service.resolve_vault_credential", return_value={"api_key": "sk-ant-test"})
    @patch("server_modules.workspace_admin_service.provider_profiles_service.resolve_provider_adapter")
    def test_cached_provider_model_metadata_refresh_bypasses_fresh_cache(
        self,
        resolve_provider_adapter_mock,
        _resolve_vault_credential_mock,
    ):
        adapter = MagicMock()
        adapter.list_model_records.return_value = [{"id": "claude-live"}]
        resolve_provider_adapter_mock.return_value = ("anthropic", "anthropic", adapter)
        existing = {
            "cached_models": [{"id": "claude-cached"}],
            "cached_models_source": "provider_adapter",
            "cached_models_synced_at": "2099-01-01T00:00:00Z",
            "cached_models_expires_at": "2099-01-01T01:00:00Z",
            "cached_models_provider_fingerprint": workspace_admin_service._credential_fingerprint(
                "anthropic",
                {"api_key": "sk-ant-test"},
            ),
        }

        result = workspace_admin_service._cached_provider_model_metadata(
            "anthropic",
            "ws-1",
            "cred-1",
            existing_metadata=existing,
            force_refresh=True,
        )

        self.assertEqual([item["id"] for item in result["cached_models"]], ["claude-live"])
        adapter.list_model_records.assert_called_once()

    @patch("server_modules.workspace_admin_service.provider_profiles_service.resolve_vault_credential", return_value={"api_key": "sk-ant-test"})
    @patch("server_modules.workspace_admin_service.provider_profiles_service.resolve_provider_adapter")
    def test_cached_provider_model_metadata_preserves_old_cache_on_refresh_failure(
        self,
        resolve_provider_adapter_mock,
        _resolve_vault_credential_mock,
    ):
        adapter = MagicMock()
        adapter.list_model_records.side_effect = RuntimeError("provider unavailable")
        resolve_provider_adapter_mock.return_value = ("anthropic", "anthropic", adapter)
        existing = {
            "cached_models": [{"id": "claude-cached"}],
            "cached_models_source": "provider_adapter",
            "cached_models_synced_at": "2026-01-01T00:00:00Z",
            "cached_models_expires_at": "2026-01-01T01:00:00Z",
        }

        result = workspace_admin_service._cached_provider_model_metadata(
            "anthropic",
            "ws-1",
            "cred-1",
            existing_metadata=existing,
            force_refresh=True,
        )

        self.assertEqual(result["cached_models"], [{"id": "claude-cached"}])
        self.assertIn("provider unavailable", result["cached_models_error"])


class TelegramWebhookRegistrationTests(unittest.IsolatedAsyncioTestCase):
    @patch.dict(
        "os.environ",
        {
            "ORION_TELEGRAM_AUTOPILOT_PUBLIC_BASE_URL": "https://runtime.example",
            "ORION_TELEGRAM_AUTOPILOT_WEBHOOK_SECRET": "secret-token",
        },
        clear=False,
    )
    @patch("server_modules.connectors_actions.ORION_TELEGRAM_AUTOPILOT_DELIVERY_MODE", "webhook")
    @patch("server_modules.connectors_actions.ORION_TELEGRAM_AUTOPILOT_WEBHOOK_SECRET", "secret-token")
    @patch("server_modules.connectors_actions.validate_telegram_connector", return_value={"ok": True, "bot": {"username": "cafe_bot"}})
    @patch("server_modules.connectors_actions.http_json_request")
    @patch("server_modules.connectors_actions.load_vault", return_value={"credentials": []})
    @patch("server_modules.connectors_actions.save_vault")
    async def test_create_telegram_connector_persists_webhook_registration_metadata(
        self,
        save_vault_mock,
        _load_vault_mock,
        http_json_request_mock,
        _validate_mock,
    ):
        http_json_request_mock.side_effect = [
            {"status": 200, "json": {"ok": True, "result": True}, "text": ""},
            {"status": 200, "json": {"ok": True, "result": {"url": "https://runtime.example/channels/telegram/webhook/tg-1"}}, "text": ""},
        ]
        with patch("server_modules.connectors_actions.uuid.uuid4", return_value=_FakeUUID("tg-1")), patch.dict(
            runtime_models._CONNECTOR_CATALOG,
            {"telegram_bot": {"label": "Telegram Bot", "auth": ["bot_token"]}},
            clear=False,
        ):
            result = await connectors_actions.create_connector_vault(
                ConnectorCreate(
                    label="Cafe bot",
                    connector="telegram_bot",
                    workspace_id="ws-1",
                    credentials={"bot_token": "123:token", "chat_id": "42"},
                )
            )

        registration = result["metadata"]["telegram_webhook_registration"]
        self.assertEqual(registration["status"], "registered")
        self.assertEqual(registration["webhook_url"], "https://runtime.example/channels/telegram/webhook/tg-1")
        self.assertGreaterEqual(save_vault_mock.call_count, 1)


if __name__ == "__main__":
    unittest.main()
