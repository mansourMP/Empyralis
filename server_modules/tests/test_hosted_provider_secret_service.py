import importlib
import os
import unittest
from unittest.mock import AsyncMock, patch


def _secrets_broker_module():
    return importlib.import_module("server_modules.secrets_broker")


class HostedProviderSecretServiceTests(unittest.TestCase):
    def test_resolve_hosted_provider_secret_prefers_managed_bundle(self) -> None:
        secrets_broker = _secrets_broker_module()
        with patch.dict(
            os.environ,
            {
                "EMPYRALIS_HOSTED_PROVIDER_SECRETS_JSON": (
                    '{"providers":{"deepseek":{"api_key":"sk-bundle-deepseek"}}}'
                ),
                "DEEPSEEK_API_KEY": "sk-env-deepseek",
            },
            clear=False,
        ), patch.object(
            secrets_broker.control_plane_repository,
            "append_agent_secret_access_event",
            new=AsyncMock(),
        ) as append_event_mock:
            result = secrets_broker.resolve_hosted_provider_secret(
                tenant_id="tenant-a",
                workspace_id="ws-a",
                provider_id="deepseek",
                tool_name="test",
                purpose="unit_test",
            )

        self.assertEqual(result.value, "sk-bundle-deepseek")
        self.assertEqual(result.source_kind, "managed_bundle")
        self.assertFalse(result.bootstrap_fallback)
        append_event_mock.assert_awaited()
        metadata = dict((append_event_mock.await_args.kwargs or {}).get("metadata") or {})
        self.assertEqual(metadata.get("source_kind"), "managed_bundle")
        self.assertEqual(metadata.get("ownership"), "platform_hosted")

    def test_resolve_hosted_provider_secret_uses_env_bootstrap_fallback(self) -> None:
        secrets_broker = _secrets_broker_module()
        with patch.dict(
            os.environ,
            {
                "EMPYRALIS_HOSTED_PROVIDER_SECRETS_JSON": "",
                "DEEPSEEK_API_KEY": "sk-env-deepseek",
            },
            clear=False,
        ), patch.object(
            secrets_broker.control_plane_repository,
            "append_agent_secret_access_event",
            new=AsyncMock(),
        ) as append_event_mock:
            result = secrets_broker.resolve_hosted_provider_secret(
                tenant_id="tenant-a",
                workspace_id="ws-a",
                provider_id="deepseek",
                tool_name="test",
                purpose="unit_test",
            )

        self.assertEqual(result.value, "sk-env-deepseek")
        self.assertEqual(result.source_kind, "env_bootstrap")
        self.assertTrue(result.bootstrap_fallback)
        metadata = dict((append_event_mock.await_args.kwargs or {}).get("metadata") or {})
        self.assertEqual(metadata.get("source_kind"), "env_bootstrap")

    def test_resolve_hosted_provider_secret_missing_fails_safely(self) -> None:
        secrets_broker = _secrets_broker_module()
        with patch.dict(
            os.environ,
            {
                "EMPYRALIS_HOSTED_PROVIDER_SECRETS_JSON": "",
                "DEEPSEEK_API_KEY": "",
                "ORION_HOSTED_DEEPSEEK_API_KEY": "",
            },
            clear=False,
        ), patch.object(
            secrets_broker.control_plane_repository,
            "append_agent_secret_access_event",
            new=AsyncMock(),
        ) as append_event_mock:
            result = secrets_broker.resolve_hosted_provider_secret(
                tenant_id="tenant-a",
                workspace_id="ws-a",
                provider_id="deepseek",
                tool_name="test",
                purpose="unit_test",
            )

        self.assertEqual(result.value, "")
        self.assertEqual(result.source_kind, "missing")
        self.assertFalse(result.bootstrap_fallback)
        self.assertEqual(append_event_mock.await_args.kwargs.get("status"), "denied")
        self.assertEqual(
            append_event_mock.await_args.kwargs.get("denial_code"),
            "hosted_provider_secret_missing",
        )

    def test_digitalocean_is_registered_and_resolves_via_the_live_env_var(self) -> None:
        """MAN-131: the platform-owned DigitalOcean token registered in
        _HOSTED_PROVIDER_ENV_CANDIDATES. Uses the exact env var name
        vps_provisioning_service has read since MAN-133
        (EMPYRALIS_PLATFORM_DIGITALOCEAN_TOKEN) so an existing production
        box's token keeps resolving unchanged, and the resolution is logged
        with ownership="platform_hosted" like every other hosted secret."""
        secrets_broker = _secrets_broker_module()
        self.assertIn("digitalocean", secrets_broker._HOSTED_PROVIDER_ENV_CANDIDATES)
        with patch.dict(
            os.environ,
            {
                "EMPYRALIS_HOSTED_PROVIDER_SECRETS_JSON": "",
                "EMPYRALIS_PLATFORM_DIGITALOCEAN_TOKEN": "dop_v1_platform_secret",
            },
            clear=False,
        ), patch.object(
            secrets_broker.control_plane_repository,
            "append_agent_secret_access_event",
            new=AsyncMock(),
        ) as append_event_mock:
            result = secrets_broker.resolve_hosted_provider_secret(
                tenant_id=None,
                workspace_id=None,
                provider_id="digitalocean",
                field="api_key",
                tool_name="test",
                purpose="unit_test",
            )

        self.assertEqual(result.value, "dop_v1_platform_secret")
        self.assertEqual(result.source, "env:EMPYRALIS_PLATFORM_DIGITALOCEAN_TOKEN")
        self.assertEqual(result.source_kind, "env_bootstrap")
        self.assertEqual(result.owner_kind, "platform_hosted")
        append_event_mock.assert_awaited()
        kwargs = append_event_mock.await_args.kwargs
        self.assertEqual(kwargs.get("provider_id"), "digitalocean")
        metadata = dict(kwargs.get("metadata") or {})
        self.assertEqual(metadata.get("ownership"), "platform_hosted")
        self.assertEqual(metadata.get("source_kind"), "env_bootstrap")

    def test_digitalocean_missing_denies_with_the_standard_code(self) -> None:
        secrets_broker = _secrets_broker_module()
        with patch.dict(
            os.environ,
            {
                "EMPYRALIS_HOSTED_PROVIDER_SECRETS_JSON": "",
                "EMPYRALIS_PLATFORM_DIGITALOCEAN_TOKEN": "",
                "ORION_HOSTED_DIGITALOCEAN_TOKEN": "",
                "DIGITALOCEAN_ACCESS_TOKEN": "",
            },
            clear=False,
        ), patch.object(
            secrets_broker.control_plane_repository,
            "append_agent_secret_access_event",
            new=AsyncMock(),
        ) as append_event_mock:
            result = secrets_broker.resolve_hosted_provider_secret(
                tenant_id=None,
                workspace_id=None,
                provider_id="digitalocean",
                field="api_key",
                tool_name="test",
                purpose="unit_test",
            )

        self.assertEqual(result.value, "")
        self.assertEqual(result.source_kind, "missing")
        self.assertEqual(append_event_mock.await_args.kwargs.get("status"), "denied")
        self.assertEqual(
            append_event_mock.await_args.kwargs.get("denial_code"),
            "hosted_provider_secret_missing",
        )

    def test_resolve_hosted_openai_bearer_uses_existing_fallback_path(self) -> None:
        secrets_broker = _secrets_broker_module()
        with patch.object(
            secrets_broker.control_plane_repository,
            "append_agent_secret_access_event",
            new=AsyncMock(),
        ) as append_event_mock:
            result = secrets_broker.resolve_hosted_openai_bearer(
                tenant_id="tenant-a",
                workspace_id="ws-a",
                fallback_token="tok-openai",
                fallback_source="env_api_key",
                tool_name="test",
                purpose="unit_test",
            )

        self.assertEqual(result.value, "tok-openai")
        self.assertEqual(result.source, "env_api_key")
        self.assertEqual(result.source_kind, "env_bootstrap")
        self.assertTrue(result.bootstrap_fallback)
        metadata = dict((append_event_mock.await_args.kwargs or {}).get("metadata") or {})
        self.assertEqual(metadata.get("source_kind"), "env_bootstrap")


if __name__ == "__main__":
    unittest.main()
