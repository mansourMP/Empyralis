import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from server_modules import provider_profiles


class ProviderCatalogTruthTests(unittest.TestCase):
    def setUp(self) -> None:
        provider_profiles._init()
        self._profiles_tmpdir = tempfile.TemporaryDirectory()
        self._temp_profiles_path = Path(self._profiles_tmpdir.name) / "profiles.json"
        self._original_profiles_path = provider_profiles._server.ORION_PROVIDER_PROFILES_FILE
        provider_profiles._server.ORION_PROVIDER_PROFILES_FILE = self._temp_profiles_path
        provider_profiles._server.sync_acp_manager_paths(provider_profiles_path=self._temp_profiles_path)
        provider_profiles._server.ACP_MANAGER.reload_secondary_state()

    def tearDown(self) -> None:
        provider_profiles._server.ORION_PROVIDER_PROFILES_FILE = self._original_profiles_path
        provider_profiles._server.sync_acp_manager_paths(provider_profiles_path=self._original_profiles_path)
        provider_profiles._server.ACP_MANAGER.reload_secondary_state()
        self._profiles_tmpdir.cleanup()

    def _assert_codex_token_does_not_claim_direct_openai_is_ready(self, source: str, *, gateway_online: bool = True) -> None:
        with patch("server_modules.provider_profiles._openai_env_bearer_with_source", return_value=("codex-token", source)), patch(
            "server_modules.provider_profiles.workspace_live_gateway_available",
            return_value=gateway_online,
        ):
            with patch.dict(provider_profiles._server.PROVIDER_PROFILES, {}, clear=True):
                payload = provider_profiles.build_provider_runtime_truth("default")

        providers = payload["providers_by_id"]
        self.assertEqual(providers["openai"]["state"], "setup_required")
        self.assertEqual(providers["openai"]["issue_code"], "direct_openai_credential_required")
        self.assertEqual(providers["openai-codex"]["active_source"], source)
        if source == "codex_token_vault" and not gateway_online:
            self.assertEqual(providers["openai-codex"]["state"], "configured")
            self.assertEqual(providers["openai-codex"]["issue_code"], "local_gateway_required")
            self.assertFalse(providers["openai-codex"]["usable"])
        else:
            self.assertEqual(providers["openai-codex"]["state"], "active")
            self.assertTrue(providers["openai-codex"]["usable"])

    @patch("server_modules.provider_profiles.resolve_default_vault_credential", side_effect=RuntimeError("missing"))
    @patch("server_modules.provider_profiles.gemini_cli_available", return_value=False)
    @patch(
        "server_modules.provider_profiles.claude_code_cli_status",
        return_value={"available": False, "logged_in": False, "message": "Claude Code CLI is not installed."},
    )
    def test_codex_env_token_does_not_claim_direct_openai_is_ready(
        self,
        _claude_status_mock,
        _gemini_cli_mock,
        _resolve_default_mock,
    ) -> None:
        self._assert_codex_token_does_not_claim_direct_openai_is_ready("env_codex_oauth_token")

    @patch("server_modules.provider_profiles.resolve_default_vault_credential", side_effect=RuntimeError("missing"))
    @patch("server_modules.provider_profiles.gemini_cli_available", return_value=False)
    @patch(
        "server_modules.provider_profiles.claude_code_cli_status",
        return_value={"available": False, "logged_in": False, "message": "Claude Code CLI is not installed."},
    )
    def test_codex_token_vault_does_not_claim_direct_openai_is_ready(
        self,
        _claude_status_mock,
        _gemini_cli_mock,
        _resolve_default_mock,
    ) -> None:
        self._assert_codex_token_does_not_claim_direct_openai_is_ready("codex_token_vault")

    @patch("server_modules.provider_profiles.resolve_default_vault_credential", side_effect=RuntimeError("missing"))
    @patch("server_modules.provider_profiles.gemini_cli_available", return_value=False)
    @patch(
        "server_modules.provider_profiles.claude_code_cli_status",
        return_value={"available": False, "logged_in": False, "message": "Claude Code CLI is not installed."},
    )
    def test_codex_token_vault_requires_local_gateway(
        self,
        _claude_status_mock,
        _gemini_cli_mock,
        _resolve_default_mock,
    ) -> None:
        self._assert_codex_token_does_not_claim_direct_openai_is_ready("codex_token_vault", gateway_online=False)

    @patch("server_modules.provider_profiles.resolve_default_vault_credential", side_effect=RuntimeError("missing"))
    @patch("server_modules.provider_profiles.gemini_cli_available", return_value=False)
    @patch("server_modules.provider_profiles._openai_env_bearer_with_source", return_value=("", ""))
    @patch(
        "server_modules.provider_profiles.claude_code_cli_status",
        return_value={"available": False, "logged_in": False, "message": "Claude Code CLI is not installed."},
    )
    def test_anthropic_local_cli_profile_is_unavailable_when_cli_missing(
        self,
        _claude_status_mock,
        _openai_env_mock,
        _gemini_cli_mock,
        _resolve_default_mock,
    ) -> None:
        profile = {
            "id": "profile-anthropic-local",
            "provider": "anthropic",
            "label": "Claude Subscription",
            "auth_mode": "local_cli",
            "workspace_id": "default",
            "enabled": True,
            "cooldown_until": None,
            "last_error": None,
        }
        with patch.dict(provider_profiles._server.PROVIDER_PROFILES, {"profile-anthropic-local": profile}, clear=True):
            payload = provider_profiles.build_provider_runtime_truth("default")

        anthropic = payload["providers_by_id"]["anthropic"]
        self.assertEqual(anthropic["state"], "unavailable")
        self.assertEqual(anthropic["issue_code"], "local_cli_missing")
        self.assertEqual(anthropic["connection_kind"], "machine_local_capability")
        self.assertEqual(anthropic["connection_scope"], "machine")
        self.assertEqual(anthropic["connection_label"], "This machine only")

    @patch("server_modules.provider_profiles.resolve_default_vault_credential", side_effect=RuntimeError("missing"))
    @patch("server_modules.provider_profiles._openai_env_bearer_with_source", return_value=("", ""))
    @patch(
        "server_modules.provider_profiles.claude_code_cli_status",
        return_value={"available": False, "logged_in": False, "message": "Claude Code CLI is not installed."},
    )
    @patch("server_modules.provider_profiles.gemini_cli_available", return_value=True)
    def test_gemini_cli_presence_alone_is_setup_required_not_active(
        self,
        _gemini_cli_mock,
        _claude_status_mock,
        _openai_env_mock,
        _resolve_default_mock,
    ) -> None:
        with patch.dict(provider_profiles._server.PROVIDER_PROFILES, {}, clear=True):
            payload = provider_profiles.build_provider_runtime_truth("default")

        gemini = payload["providers_by_id"]["gemini"]
        self.assertEqual(gemini["state"], "setup_required")
        self.assertEqual(gemini["issue_code"], "gemini_cli_oauth_incomplete")
        self.assertFalse(gemini["active"])

    @patch("server_modules.provider_profiles.resolve_default_vault_credential", side_effect=RuntimeError("missing"))
    @patch("server_modules.provider_profiles.gemini_cli_available", return_value=False)
    @patch(
        "server_modules.provider_profiles.claude_code_cli_status",
        return_value={"available": False, "logged_in": False, "message": "Claude Code CLI is not installed."},
    )
    @patch("server_modules.provider_profiles._openai_env_bearer_with_source", return_value=("", ""))
    def test_rate_limited_profile_projects_degraded_provider_state(
        self,
        _openai_env_mock,
        _claude_status_mock,
        _gemini_cli_mock,
        _resolve_default_mock,
    ) -> None:
        profile = {
            "id": "profile-openai-rate-limit",
            "provider": "openai",
            "label": "OpenAI Direct",
            "auth_mode": "api_key",
            "workspace_id": "default",
            "enabled": True,
            "cooldown_until": "2099-01-01T00:00:30Z",
            "last_error": "429 Rate limit exceeded.",
            "last_failure_at": "2099-01-01T00:00:00Z",
            "last_success_at": None,
        }
        with patch.dict(provider_profiles._server.PROVIDER_PROFILES, {"profile-openai-rate-limit": profile}, clear=True):
            payload = provider_profiles.build_provider_runtime_truth("default")

        openai = payload["providers_by_id"]["openai"]
        self.assertEqual(openai["state"], "degraded")
        self.assertTrue(openai["backpressure"])
        self.assertEqual(openai["failure_class"], "rate_limited")
        self.assertEqual(openai["connection_kind"], "workspace_provider_connection")
        self.assertEqual(openai["connection_scope"], "workspace")
        self.assertEqual(openai["connection_label"], "Workspace provider")
        self.assertIsInstance(openai["retry_after_seconds"], int)
        self.assertGreaterEqual(openai["retry_after_seconds"], 0)

    @patch("server_modules.provider_profiles.resolve_default_vault_credential", side_effect=RuntimeError("missing"))
    @patch("server_modules.provider_profiles.gemini_cli_available", return_value=False)
    @patch(
        "server_modules.provider_profiles.claude_code_cli_status",
        return_value={"available": False, "logged_in": False, "message": "Claude Code CLI is not installed."},
    )
    @patch("server_modules.provider_profiles._openai_env_bearer_with_source", return_value=("sk-live", "env_api_key"))
    def test_openai_env_api_key_is_runtime_environment_not_identity(
        self,
        _openai_env_mock,
        _claude_status_mock,
        _gemini_cli_mock,
        _resolve_default_mock,
    ) -> None:
        with patch.dict(provider_profiles._server.PROVIDER_PROFILES, {}, clear=True):
            payload = provider_profiles.build_provider_runtime_truth("default")

        openai = payload["providers_by_id"]["openai"]
        self.assertEqual(openai["state"], "active")
        self.assertEqual(openai["connection_kind"], "runtime_environment")
        self.assertEqual(openai["connection_scope"], "runtime")
        self.assertEqual(openai["connection_label"], "Runtime environment")
        self.assertEqual(openai["identity_owner"], "platform_account")
        self.assertEqual(openai["identity_owner_label"], "Empyralis account")

    @patch("server_modules.provider_profiles.resolve_default_vault_credential", side_effect=RuntimeError("missing"))
    @patch("server_modules.provider_profiles.gemini_cli_available", return_value=False)
    @patch("server_modules.provider_profiles._openai_env_bearer_with_source", return_value=("", ""))
    @patch(
        "server_modules.provider_profiles.claude_code_cli_status",
        return_value={"available": False, "logged_in": False, "message": "Claude Code CLI is not installed."},
    )
    def test_runtime_truth_normalizes_stale_anthropic_profile_model_alias(
        self,
        _claude_status_mock,
        _openai_env_mock,
        _gemini_cli_mock,
        _resolve_default_mock,
    ) -> None:
        profile = {
            "id": "profile-anthropic-stale",
            "provider": "anthropic",
            "label": "Anthropic",
            "auth_mode": "api_key",
            "workspace_id": "default",
            "enabled": True,
            "credential_id": "cred-anthropic",
            "model": "claude-3-7-sonnet-latest",
        }
        with patch.dict(provider_profiles._server.PROVIDER_PROFILES, {"profile-anthropic-stale": profile}, clear=True):
            payload = provider_profiles.build_provider_runtime_truth("default")

        anthropic_profile = next(
            item for item in payload["items"] if item.get("provider") == "anthropic"
        )
        self.assertEqual(anthropic_profile["model"], "claude-3-7-sonnet-20250219")

    @patch("server_modules.provider_profiles.gemini_cli_available", return_value=False)
    @patch(
        "server_modules.provider_profiles.claude_code_cli_status",
        return_value={"available": False, "logged_in": False, "message": "Claude Code CLI is not installed."},
    )
    @patch("server_modules.provider_profiles._openai_env_bearer_with_source", return_value=("", ""))
    def test_workspace_provider_connection_truth_ignores_runtime_environment_credentials(
        self,
        _openai_env_mock,
        _claude_status_mock,
        _gemini_cli_mock,
    ) -> None:
        with patch.dict(provider_profiles._server.PROVIDER_PROFILES, {}, clear=True), patch.dict(
            "os.environ",
            {
                "ANTHROPIC_API_KEY": "sk-ant-live",
                "GEMINI_API_KEY": "gem-live",
            },
            clear=False,
        ):
            payload = provider_profiles.build_workspace_provider_connection_truth("default")

        providers = payload["providers_by_id"]
        self.assertEqual(providers["anthropic"]["state"], "setup_required")
        self.assertEqual(providers["gemini"]["state"], "setup_required")
        self.assertEqual(providers["openai"]["state"], "setup_required")

    @patch("server_modules.provider_profiles.gemini_cli_available", return_value=False)
    @patch(
        "server_modules.provider_profiles.claude_code_cli_status",
        return_value={"available": True, "logged_in": True, "message": "Claude Code CLI is ready."},
    )
    @patch("server_modules.provider_profiles._openai_env_bearer_with_source", return_value=("sk-live", "env_api_key"))
    def test_workspace_provider_connection_truth_exposes_api_key_only_ai_auth_modes(
        self,
        _openai_env_mock,
        _claude_status_mock,
        _gemini_cli_mock,
    ) -> None:
        with patch.dict(provider_profiles._server.PROVIDER_PROFILES, {}, clear=True), patch.dict(
            "os.environ",
            {
                "ANTHROPIC_API_KEY": "sk-ant-live",
                "GEMINI_API_KEY": "gem-live",
            },
            clear=False,
        ):
            payload = provider_profiles.build_workspace_provider_connection_truth("default")

        providers = payload["providers_by_id"]
        self.assertEqual(providers["openai"]["auth"], ["api_key"])
        self.assertEqual(providers["anthropic"]["auth"], ["api_key"])
        self.assertEqual(providers["gemini"]["auth"], ["api_key"])
        self.assertEqual(providers["anthropic"]["default_auth_mode"], "api_key")
        self.assertEqual(
            [item["id"] for item in providers["anthropic"]["auth_modes"]],
            ["api_key"],
        )

        providers = payload["providers_by_id"]
        self.assertEqual(providers["anthropic"]["state"], "setup_required")
        self.assertFalse(providers["anthropic"]["configured"])
        self.assertIsNone(providers["anthropic"]["active_source"])
        self.assertEqual(providers["gemini"]["state"], "setup_required")
        self.assertFalse(providers["gemini"]["configured"])

    @patch("server_modules.provider_profiles.gemini_cli_available", return_value=False)
    @patch(
        "server_modules.provider_profiles.claude_code_cli_status",
        return_value={"available": True, "logged_in": True, "message": "Claude Code CLI is available."},
    )
    @patch("server_modules.provider_profiles._openai_env_bearer_with_source", return_value=("", ""))
    def test_workspace_provider_connection_truth_requires_saved_credential_even_if_secretless_profile_exists(
        self,
        _openai_env_mock,
        _claude_status_mock,
        _gemini_cli_mock,
    ) -> None:
        profile = {
            "id": "profile-anthropic-local",
            "provider": "anthropic",
            "label": "Claude Subscription",
            "auth_mode": "local_cli",
            "workspace_id": "default",
            "enabled": True,
            "cooldown_until": None,
            "last_error": None,
        }
        with patch.dict(provider_profiles._server.PROVIDER_PROFILES, {"profile-anthropic-local": profile}, clear=True):
            payload = provider_profiles.build_workspace_provider_connection_truth("default")

        anthropic = payload["providers_by_id"]["anthropic"]
        self.assertEqual(anthropic["state"], "setup_required")
        self.assertFalse(anthropic["configured"])
        self.assertFalse(anthropic["active"])


if __name__ == "__main__":
    unittest.main()
