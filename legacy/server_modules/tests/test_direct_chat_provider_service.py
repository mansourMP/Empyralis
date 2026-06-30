import unittest
from unittest.mock import patch

from server_modules import direct_chat_provider_service


class DirectChatProviderServiceTests(unittest.TestCase):
    def test_direct_chat_credentials_falls_back_to_runtime_candidates_when_workspace_only_is_empty(self) -> None:
        calls: list[tuple[dict[str, object], str]] = []

        def fake_candidates(_context: dict[str, object], metadata: dict[str, object], provider: str) -> list[dict[str, object]]:
            calls.append((dict(metadata), provider))
            if metadata.get("workspace_only"):
                return []
            return [{"credentials": {"auth_mode": "local_cli"}, "label": "local-claude-cli"}]

        credentials = direct_chat_provider_service.direct_chat_credentials(
            "workspace-a",
            "anthropic",
            build_provider_credential_candidates_fn=fake_candidates,
            hosted_sage_ai_access_state_for_workspace_id_fn=lambda **_kwargs: {"allowed": True},
        )

        self.assertEqual(credentials, {"auth_mode": "local_cli"})
        self.assertEqual(calls, [({"source": "chat_direct", "workspace_only": True}, "anthropic"), ({"source": "chat_direct"}, "anthropic")])

    def test_direct_chat_credentials_filters_platform_runtime_when_hosted_ai_disabled(self) -> None:
        def fake_candidates(_context: dict[str, object], metadata: dict[str, object], _provider: str) -> list[dict[str, object]]:
            if metadata.get("workspace_only"):
                return []
            return [
                {"source": "env", "credentials": {"api_key": "sk-platform"}, "label": "env-deepseek"},
                {"source": "local", "credentials": {"auth_mode": "none"}, "label": "local-ollama"},
            ]

        credentials = direct_chat_provider_service.direct_chat_credentials(
            "workspace-a",
            "deepseek",
            build_provider_credential_candidates_fn=fake_candidates,
            hosted_sage_ai_access_state_for_workspace_id_fn=lambda **_kwargs: {
                "allowed": False,
                "reason": "policy_disabled",
            },
        )

        self.assertEqual(credentials, {"auth_mode": "none"})

    def test_direct_chat_credentials_prefers_usable_candidate_over_codex_token(self) -> None:
        def fake_candidates(_context: dict[str, object], metadata: dict[str, object], _provider: str) -> list[dict[str, object]]:
            if metadata.get("workspace_only"):
                return []
            return [
                {
                    "source": "vault_default",
                    "credentials": {
                        "auth_mode": "oauth_token",
                        "credential_type": "codex_token",
                        "oauth_token": "codex-token",
                    },
                    "label": "vault-default",
                },
                {
                    "source": "env",
                    "credentials": {
                        "auth_mode": "api_key",
                        "credential_type": "api_key",
                        "api_key": "sk-openai",
                    },
                    "label": "env-openai",
                },
            ]

        credentials = direct_chat_provider_service.direct_chat_credentials(
            "workspace-a",
            "openai",
            build_provider_credential_candidates_fn=fake_candidates,
            hosted_sage_ai_access_state_for_workspace_id_fn=lambda **_kwargs: {"allowed": True},
        )

        self.assertEqual(credentials["auth_mode"], "api_key")
        self.assertEqual(credentials["api_key"], "sk-openai")

    def test_direct_chat_credentials_can_use_explicit_openai_env_when_codex_token_shadows_candidates(self) -> None:
        def fake_candidates(_context: dict[str, object], _metadata: dict[str, object], _provider: str) -> list[dict[str, object]]:
            return [
                {
                    "source": "vault_default",
                    "credentials": {
                        "auth_mode": "oauth_token",
                        "credential_type": "codex_token",
                        "oauth_token": "codex-token",
                    },
                    "label": "vault-default",
                },
            ]

        with patch.dict(
            "os.environ",
            {
                "ORION_DISABLE_OPENAI_API_KEY": "0",
                "OPENAI_API_KEY": "sk-openai-env",
            },
        ):
            credentials = direct_chat_provider_service.direct_chat_credentials(
                "workspace-a",
                "openai",
                build_provider_credential_candidates_fn=fake_candidates,
                hosted_sage_ai_access_state_for_workspace_id_fn=lambda **_kwargs: {"allowed": True},
            )

        self.assertEqual(credentials["auth_mode"], "api_key")
        self.assertEqual(credentials["api_key"], "sk-openai-env")

    def test_supports_direct_message_native_chat_requires_scoped_key_for_cloud_api_providers(self) -> None:
        for provider in (
            "deepseek",
            "qwen",
            "mistral",
            "vertex",
            "ollama_cloud",
            "groq",
            "openrouter",
            "azure_openai",
            "custom_openai_compatible",
        ):
            with self.subTest(provider=provider):
                self.assertFalse(
                    direct_chat_provider_service.supports_direct_message_native_chat(
                        provider,
                        {},
                        provider_has_key_fn=lambda _provider: True,
                    )
                )
                self.assertTrue(
                    direct_chat_provider_service.supports_direct_message_native_chat(
                        provider,
                        {"api_key": "sk-scoped"},
                        provider_has_key_fn=lambda _provider: False,
                    )
                )

    def test_provider_display_name_includes_ollama_cloud(self) -> None:
        self.assertEqual(direct_chat_provider_service.provider_display_name("ollama_cloud"), "Ollama Cloud")

    def test_preferred_provider_does_not_use_platform_key_when_hosted_ai_is_filtered(self) -> None:
        def fake_credentials(_workspace_id: str, _provider: str) -> dict[str, str]:
            return {}

        provider, credentials = direct_chat_provider_service.preferred_provider(
            "workspace-a",
            "",
            supported_providers=["openai", "anthropic", "gemini", "deepseek", "codex_cli"],
            direct_chat_credentials_fn=fake_credentials,
            supports_direct_message_native_chat_fn=lambda provider, credentials: direct_chat_provider_service.supports_direct_message_native_chat(
                provider,
                credentials,
                provider_has_key_fn=lambda key_provider: key_provider == "deepseek",
            ),
            credential_auth_mode_fn=lambda _provider, credentials: str((credentials or {}).get("auth_mode") or ""),
            provider_runtime_usable_fn=lambda _workspace_id, _provider: False,
        )

        self.assertEqual(provider, "openai")
        self.assertEqual(credentials, {})

    def test_preferred_provider_uses_deepseek_as_auto_hosted_default(self) -> None:
        def fake_credentials(_workspace_id: str, provider: str) -> dict[str, str]:
            if provider == "anthropic":
                return {"api_key": "sk-ant"}
            if provider == "deepseek":
                return {"api_key": "sk-deepseek"}
            return {}

        provider, credentials = direct_chat_provider_service.preferred_provider(
            "workspace-a",
            "",
            supported_providers=["openai", "anthropic", "gemini", "deepseek", "codex_cli"],
            direct_chat_credentials_fn=fake_credentials,
            supports_direct_message_native_chat_fn=lambda _provider, credentials: bool(credentials),
            credential_auth_mode_fn=lambda _provider, credentials: str((credentials or {}).get("auth_mode") or ""),
            provider_runtime_usable_fn=lambda _workspace_id, _provider: False,
        )

        self.assertEqual(provider, "deepseek")
        self.assertEqual(credentials, {"api_key": "sk-deepseek"})

    def test_preferred_provider_prefers_gemini_before_openai_and_anthropic_when_deepseek_missing(self) -> None:
        def fake_credentials(_workspace_id: str, provider: str) -> dict[str, str]:
            if provider == "gemini":
                return {"api_key": "sk-gem"}
            if provider == "openai":
                return {"api_key": "sk-openai"}
            if provider == "anthropic":
                return {"api_key": "sk-ant"}
            return {}

        provider, credentials = direct_chat_provider_service.preferred_provider(
            "workspace-a",
            "",
            supported_providers=["openai", "anthropic", "gemini", "deepseek", "codex_cli"],
            direct_chat_credentials_fn=fake_credentials,
            supports_direct_message_native_chat_fn=lambda _provider, creds: bool(creds),
            credential_auth_mode_fn=lambda _provider, creds: str((creds or {}).get("auth_mode") or ""),
            provider_runtime_usable_fn=lambda _workspace_id, _provider: False,
        )

        self.assertEqual(provider, "gemini")
        self.assertEqual(credentials, {"api_key": "sk-gem"})

    def test_preferred_provider_uses_ollama_cloud_when_primary_hosted_providers_are_missing(self) -> None:
        def fake_credentials(_workspace_id: str, provider: str) -> dict[str, str]:
            if provider == "ollama_cloud":
                return {"api_key": "ollama-cloud-key"}
            if provider == "anthropic":
                return {"api_key": "sk-ant"}
            return {}

        provider, credentials = direct_chat_provider_service.preferred_provider(
            "workspace-a",
            "",
            supported_providers=["openai", "anthropic", "gemini", "deepseek", "ollama_cloud", "codex_cli"],
            direct_chat_credentials_fn=fake_credentials,
            supports_direct_message_native_chat_fn=lambda _provider, creds: bool(creds),
            credential_auth_mode_fn=lambda _provider, creds: str((creds or {}).get("auth_mode") or ""),
            provider_runtime_usable_fn=lambda _workspace_id, _provider: False,
        )

        self.assertEqual(provider, "ollama_cloud")
        self.assertEqual(credentials, {"api_key": "ollama-cloud-key"})

    def test_preferred_provider_maps_openai_oauth_to_codex_cli_without_explicit_selection(self) -> None:
        def fake_credentials(_workspace_id: str, provider: str) -> dict[str, str]:
            if provider == "openai":
                return {"auth_mode": "oauth_token", "oauth_token": "token"}
            if provider == "codex_cli":
                return {"auth_mode": "oauth_token", "oauth_token": "token"}
            return {}

        def fake_support(provider: str, credentials: dict[str, str] | None) -> bool:
            return provider == "codex_cli" and bool(credentials)

        provider, credentials = direct_chat_provider_service.preferred_provider(
            "default",
            "",
            supported_providers=["openai", "codex_cli", "anthropic", "gemini"],
            direct_chat_credentials_fn=fake_credentials,
            supports_direct_message_native_chat_fn=fake_support,
            credential_auth_mode_fn=lambda _provider, credentials: str((credentials or {}).get("auth_mode") or ""),
            provider_runtime_usable_fn=lambda _workspace_id, _provider: True,
        )

        self.assertEqual(provider, "codex_cli")
        self.assertEqual(credentials.get("auth_mode"), "oauth_token")

    def test_preferred_provider_maps_openai_codex_token_to_codex_cli_without_explicit_selection(self) -> None:
        def fake_credentials(_workspace_id: str, provider: str) -> dict[str, str]:
            if provider == "openai":
                return {"credential_type": "codex_token", "access_token": "token"}
            if provider == "codex_cli":
                return {"auth_mode": "oauth_token", "oauth_token": "token"}
            return {}

        def fake_support(provider: str, credentials: dict[str, str] | None) -> bool:
            return provider == "codex_cli" and bool(credentials)

        provider, credentials = direct_chat_provider_service.preferred_provider(
            "default",
            "",
            supported_providers=["openai", "codex_cli", "anthropic", "gemini"],
            direct_chat_credentials_fn=fake_credentials,
            supports_direct_message_native_chat_fn=fake_support,
            credential_auth_mode_fn=lambda _provider, credentials: str((credentials or {}).get("auth_mode") or ""),
            provider_runtime_usable_fn=lambda _workspace_id, _provider: True,
        )

        self.assertEqual(provider, "codex_cli")
        self.assertEqual(credentials.get("auth_mode"), "oauth_token")

    def test_preferred_provider_blocks_explicit_codex_when_runtime_unusable(self) -> None:
        provider, credentials = direct_chat_provider_service.preferred_provider(
            "default",
            "openai-codex",
            supported_providers=["openai", "codex_cli", "anthropic", "gemini"],
            direct_chat_credentials_fn=lambda _workspace_id, provider: {"auth_mode": "oauth_token", "oauth_token": "token"} if provider == "codex_cli" else {},
            supports_direct_message_native_chat_fn=lambda provider, credentials: provider == "codex_cli" and bool(credentials),
            credential_auth_mode_fn=lambda _provider, credentials: str((credentials or {}).get("auth_mode") or ""),
            provider_runtime_usable_fn=lambda _workspace_id, provider: provider != "codex_cli",
        )

        self.assertEqual(provider, "codex_cli")
        self.assertEqual(credentials, {})

    def test_preferred_provider_keeps_explicit_selection_when_unavailable(self) -> None:
        provider, credentials = direct_chat_provider_service.preferred_provider(
            "default",
            "anthropic",
            supported_providers=["openai", "codex_cli", "anthropic", "gemini"],
            direct_chat_credentials_fn=lambda _workspace_id, provider: {"api_key": "sk-openai"} if provider == "openai" else {},
            supports_direct_message_native_chat_fn=lambda provider, credentials: provider == "openai" and bool(credentials),
            credential_auth_mode_fn=lambda _provider, credentials: str((credentials or {}).get("auth_mode") or ""),
        )

        self.assertEqual(provider, "anthropic")
        self.assertEqual(credentials, {})

    def test_preferred_provider_does_not_override_explicit_openai_to_codex_cli(self) -> None:
        def fake_credentials(_workspace_id: str, provider: str) -> dict[str, str]:
            if provider == "openai":
                return {"auth_mode": "oauth_token", "oauth_token": "token"}
            if provider == "codex_cli":
                return {"auth_mode": "oauth_token", "oauth_token": "token"}
            return {}

        provider, credentials = direct_chat_provider_service.preferred_provider(
            "default",
            "openai",
            supported_providers=["openai", "codex_cli", "anthropic", "gemini"],
            direct_chat_credentials_fn=fake_credentials,
            supports_direct_message_native_chat_fn=lambda _provider, _credentials: False,
            credential_auth_mode_fn=lambda _provider, creds: str((creds or {}).get("auth_mode") or ""),
        )

        self.assertEqual(provider, "openai")
        self.assertEqual(credentials.get("auth_mode"), "oauth_token")

    def test_resolve_direct_chat_availability_uses_override_and_default_tools(self) -> None:
        payload = direct_chat_provider_service.resolve_direct_chat_availability(
            "workspace-a",
            "openai",
            direct_chat_runtime_available_fn=lambda: True,
            preferred_provider_fn=lambda workspace_id, requested: (requested or "openai", {"api_key": "sk-test"}),
            supports_direct_message_native_chat_fn=lambda _provider, credentials: bool(credentials),
            resolve_workspace_tool_capabilities_fn=lambda workspace_id: [{"id": "gmail", "workspace_id": workspace_id}],
            direct_chat_provider_truth_fn=lambda _workspace_id, _provider: {
                "credential_plane": "workspace_connection",
                "credential_owner_kind": "workspace_byok",
                "workspace_connected": True,
                "connection_state": "active",
                "runtime_state": "active",
            },
            availability_override={"runtime_ok": False, "custom": "value"},
        )

        self.assertEqual(payload["provider"], "openai")
        self.assertTrue(payload["ai_ready"])
        self.assertFalse(payload["runtime_ok"])
        self.assertEqual(payload["connection_mode"], "byok")
        self.assertEqual(payload["credential_plane"], "workspace_connection")
        self.assertEqual(payload["credential_owner_kind"], "workspace_byok")
        self.assertTrue(payload["workspace_connected"])
        self.assertEqual(payload["custom"], "value")
        self.assertEqual(payload["tool_capabilities"], [{"id": "gmail", "workspace_id": "workspace-a"}])
        self.assertIsInstance(payload.get("capability_truth"), dict)
        self.assertEqual(payload["capability_truth"]["provider"]["id"], "openai")
        self.assertEqual(payload["capability_truth"]["my_computer"]["state"], "offline")
        self.assertEqual(payload["capability_truth"]["required_setup_actions"], [])

    def test_resolve_direct_chat_availability_prefers_platform_mode_for_mobile(self) -> None:
        payload = direct_chat_provider_service.resolve_direct_chat_availability(
            "workspace-a",
            "openai",
            direct_chat_runtime_available_fn=lambda: True,
            preferred_provider_fn=lambda workspace_id, requested: (requested or "openai", {"api_key": "sk-test"}),
            supports_direct_message_native_chat_fn=lambda _provider, credentials: bool(credentials),
            resolve_workspace_tool_capabilities_fn=lambda _workspace_id: [],
            direct_chat_provider_truth_fn=lambda _workspace_id, _provider: {
                "credential_plane": "platform_runtime",
                "credential_owner_kind": "platform_hosted",
                "workspace_connected": False,
                "connection_state": "setup_required",
                "runtime_state": "active",
            },
            availability_override={"surface_channel": "mobile", "source": "mobile_chat"},
        )

        self.assertTrue(payload["runtime_ok"])
        self.assertEqual(payload["connection_mode"], "platform")

    def test_direct_chat_provider_truth_restricts_platform_runtime_when_hosted_ai_disabled(self) -> None:
        payload = direct_chat_provider_service.direct_chat_provider_truth(
            "workspace-a",
            "deepseek",
            build_workspace_provider_connection_truth_fn=lambda _workspace_id: {
                "providers_by_id": {
                    "deepseek": {
                        "id": "deepseek",
                        "state": "setup_required",
                    },
                },
            },
            build_provider_runtime_truth_fn=lambda _workspace_id: {
                "providers_by_id": {
                    "deepseek": {
                        "id": "deepseek",
                        "label": "DeepSeek",
                        "state": "active",
                        "active_source": "env-deepseek",
                        "credential_sources": ["env-deepseek"],
                        "identity_owner": "platform_account",
                    },
                },
            },
            hosted_sage_ai_access_state_for_workspace_id_fn=lambda **_kwargs: {
                "allowed": False,
                "policy": "owner_opt_in",
                "monthly_cap_usd": 5.0,
                "monthly_cost_usd": 0.0,
                "monthly_remaining_usd": 5.0,
                "reason": "owner_approval_required",
            },
        )

        self.assertFalse(payload["hosted_ai_enabled"])
        self.assertFalse(payload["platform_runtime_allowed"])
        self.assertEqual(payload["credential_plane"], "platform_runtime")
        self.assertEqual(payload["runtime_state"], "restricted")
        self.assertEqual(payload["issue_code"], "hosted_ai_owner_approval_required")

    def test_resolve_direct_chat_availability_prefers_local_companion_for_online_local_runtime_provider_plane(self) -> None:
        payload = direct_chat_provider_service.resolve_direct_chat_availability(
            "workspace-a",
            "anthropic",
            direct_chat_runtime_available_fn=lambda: True,
            preferred_provider_fn=lambda workspace_id, requested: (requested or "anthropic", {"auth_mode": "local_cli"}),
            supports_direct_message_native_chat_fn=lambda _provider, credentials: bool(credentials),
            resolve_workspace_tool_capabilities_fn=lambda _workspace_id: [],
            direct_chat_provider_truth_fn=lambda _workspace_id, _provider: {
                "credential_plane": "local_runtime",
                "credential_owner_kind": "local_machine",
                "workspace_connected": False,
                "connection_state": "setup_required",
                "runtime_state": "active",
            },
            workspace_live_gateway_available_fn=lambda _workspace_id: True,
        )

        self.assertEqual(payload["connection_mode"], "local_companion")
        self.assertEqual(payload["credential_owner_kind"], "local_machine")
        self.assertTrue(payload["ai_ready"])
        self.assertTrue(payload["local_gateway_online"])

    def test_resolve_direct_chat_availability_avoids_local_companion_mode_when_local_runtime_is_offline(self) -> None:
        payload = direct_chat_provider_service.resolve_direct_chat_availability(
            "workspace-a",
            "anthropic",
            direct_chat_runtime_available_fn=lambda: False,
            preferred_provider_fn=lambda workspace_id, requested: (requested or "anthropic", {"auth_mode": "local_cli"}),
            supports_direct_message_native_chat_fn=lambda _provider, credentials: bool(credentials),
            resolve_workspace_tool_capabilities_fn=lambda _workspace_id: [],
            direct_chat_provider_truth_fn=lambda _workspace_id, _provider: {
                "credential_plane": "local_runtime",
                "credential_owner_kind": "local_machine",
                "workspace_connected": False,
                "connection_state": "setup_required",
                "runtime_state": "active",
            },
            workspace_live_gateway_available_fn=lambda _workspace_id: False,
        )

        self.assertEqual(payload["connection_mode"], "")
        self.assertFalse(payload["ai_ready"])
        self.assertFalse(payload["local_gateway_online"])

    def test_resolve_direct_chat_availability_uses_default_local_worker_in_local_env(self) -> None:
        worker_payload = {
            "items": [
                {
                    "workspace_id": "default",
                    "online": True,
                    "capabilities": ["shell.execute", "local.worker"],
                }
            ]
        }
        with patch.dict("os.environ", {"ORION_ENV": "local"}, clear=False), patch(
            "server_modules.local_queue.handle_get_local_workers_status",
            return_value=worker_payload,
        ):
            payload = direct_chat_provider_service.resolve_direct_chat_availability(
                "ws-1",
                "openai",
                direct_chat_runtime_available_fn=lambda: True,
                preferred_provider_fn=lambda workspace_id, requested: (requested or "openai", {"api_key": "sk-test"}),
                supports_direct_message_native_chat_fn=lambda _provider, credentials: bool(credentials),
                resolve_workspace_tool_capabilities_fn=lambda _workspace_id: [],
                direct_chat_provider_truth_fn=lambda _workspace_id, _provider: {
                    "credential_plane": "workspace_connection",
                    "credential_owner_kind": "workspace_byok",
                    "workspace_connected": True,
                    "connection_state": "active",
                    "runtime_state": "active",
                },
                workspace_live_gateway_available_fn=lambda _workspace_id: False,
            )

        self.assertTrue(payload["local_worker_online"])
        self.assertTrue(payload["capability_truth"]["my_computer"]["local_tools_available"])

    def test_connected_provider_tokens_filters_empty_credentials(self) -> None:
        connected = direct_chat_provider_service.connected_provider_tokens(
            "default",
            supported_providers=["openai", "anthropic", "gemini"],
            direct_chat_credentials_fn=lambda _workspace_id, provider: {"api_key": "sk-test"} if provider != "gemini" else {},
        )

        self.assertEqual(connected, ["openai", "anthropic"])

    def test_provider_unavailable_response_uses_connect_action(self) -> None:
        payload = direct_chat_provider_service.provider_unavailable_response(
            "codex_cli",
            connect_action=lambda label, href: {"label": label, "href": href},
        )

        self.assertEqual(payload["mode"], "connect")
        self.assertEqual(payload["reply"], "")
        self.assertEqual(payload["interventions"][0]["kind"], "connect_required")
        self.assertEqual(payload["interventions"][0]["title"], "Workspace AI account is not ready")
        self.assertEqual(payload["actions"], [{"label": "Connect", "href": "/connect-ai"}])

    def test_resolve_provider_for_direct_chat_message_forces_codex_for_connector_heavy_requests_without_explicit_selection(self) -> None:
        provider, credentials = direct_chat_provider_service.resolve_provider_for_direct_chat_message(
            "default",
            "",
            "Send a Slack message to the team",
            tools_present=True,
            preferred_provider_fn=lambda _workspace_id, _requested_provider: ("openai", {"api_key": "sk-test"}),
            direct_chat_credentials_fn=lambda _workspace_id, provider: {"oauth_token": "token"} if provider == "codex_cli" else {},
            supports_direct_message_native_chat_fn=lambda provider, credentials: provider == "codex_cli" and bool(credentials),
            provider_runtime_usable_fn=lambda _workspace_id, _provider: True,
            compact_text_fn=lambda value: str(value or "").strip().lower(),
            mentions_any_fn=lambda text, keywords: any(keyword in text for keyword in keywords),
            message_requests_local_file_tool_fn=lambda _message: False,
            message_requests_local_shell_tool_fn=lambda _message: False,
            message_requests_local_screenshot_tool_fn=lambda _message: False,
            message_requests_local_computer_tool_fn=lambda _message: False,
            google_workspace_keywords=("gmail",),
            telegram_keywords=("telegram",),
            slack_keywords=("slack",),
            dropbox_keywords=("dropbox",),
            s3_keywords=("s3",),
        )

        self.assertEqual(provider, "codex_cli")
        self.assertEqual(credentials, {"oauth_token": "token"})

    def test_resolve_provider_for_direct_chat_message_keeps_explicit_provider_for_connector_heavy_requests(self) -> None:
        provider, credentials = direct_chat_provider_service.resolve_provider_for_direct_chat_message(
            "default",
            "openai",
            "Send a Slack message to the team",
            tools_present=True,
            preferred_provider_fn=lambda _workspace_id, _requested_provider: ("openai", {"api_key": "sk-test"}),
            direct_chat_credentials_fn=lambda _workspace_id, provider: {"oauth_token": "token"} if provider == "codex_cli" else {},
            supports_direct_message_native_chat_fn=lambda provider, credentials: provider == "codex_cli" and bool(credentials),
            compact_text_fn=lambda value: str(value or "").strip().lower(),
            mentions_any_fn=lambda text, keywords: any(keyword in text for keyword in keywords),
            message_requests_local_file_tool_fn=lambda _message: False,
            message_requests_local_shell_tool_fn=lambda _message: False,
            message_requests_local_screenshot_tool_fn=lambda _message: False,
            message_requests_local_computer_tool_fn=lambda _message: False,
            google_workspace_keywords=("gmail",),
            telegram_keywords=("telegram",),
            slack_keywords=("slack",),
            dropbox_keywords=("dropbox",),
            s3_keywords=("s3",),
        )

        self.assertEqual(provider, "openai")
        self.assertEqual(credentials, {"api_key": "sk-test"})

    def test_resolve_provider_for_direct_chat_message_keeps_preferred_provider_when_codex_unavailable(self) -> None:
        provider, credentials = direct_chat_provider_service.resolve_provider_for_direct_chat_message(
            "default",
            "openai",
            "Take a screenshot of the current page",
            tools_present=True,
            preferred_provider_fn=lambda _workspace_id, _requested_provider: ("openai", {"api_key": "sk-test"}),
            direct_chat_credentials_fn=lambda _workspace_id, _provider: {},
            supports_direct_message_native_chat_fn=lambda _provider, _credentials: False,
            compact_text_fn=lambda value: str(value or "").strip().lower(),
            mentions_any_fn=lambda text, keywords: any(keyword in text for keyword in keywords),
            message_requests_local_file_tool_fn=lambda _message: False,
            message_requests_local_shell_tool_fn=lambda _message: False,
            message_requests_local_screenshot_tool_fn=lambda _message: True,
            message_requests_local_computer_tool_fn=lambda _message: False,
            google_workspace_keywords=("gmail",),
            telegram_keywords=("telegram",),
            slack_keywords=("slack",),
            dropbox_keywords=("dropbox",),
            s3_keywords=("s3",),
        )

        self.assertEqual(provider, "openai")
        self.assertEqual(credentials, {"api_key": "sk-test"})


if __name__ == "__main__":
    unittest.main()
