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
