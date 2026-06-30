import json
import os
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import safe_mode_service, secrets_broker


def _load_vault():
    return {
        "credentials": [
            {
                "id": "cred-telegram",
                "provider": "telegram_bot",
                "label": "Telegram Bot",
                "workspace_id": "workspace-1",
                "encrypted_secret": json.dumps(
                    {
                        "chat_id": "chat-123",
                        "api_key": "telegram-secret",
                    }
                ),
                "metadata": {"bot_username": "@partspro_bot"},
                "updated_at": "2026-04-10T00:00:00Z",
            },
            {
                "id": "cred-openai",
                "provider": "openai",
                "label": "OpenAI Primary",
                "workspace_id": "workspace-1",
                "encrypted_secret": json.dumps(
                    {
                        "access_token": "openai-secret",
                        "org_id": "org-1",
                    }
                ),
                "metadata": {"model": "gpt-5.4"},
                "updated_at": "2026-04-10T00:00:00Z",
            },
        ]
    }


class SecretsBrokerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env_patch = patch.dict(
            os.environ,
            {"EMPYRALIS_SECRETS_BROKER_SECRET": "test-secrets-broker-secret-value"},
            clear=False,
        )
        self._env_patch.start()
        self._kernel_patch = patch.object(
            secrets_broker.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=self._fake_rust_secret_inspection,
        )
        self._kernel_patch.start()

    def tearDown(self) -> None:
        self._kernel_patch.stop()
        self._env_patch.stop()
        safe_mode_service.reset_state_for_tests()
        secrets_broker.reset_secret_grant_revocations_for_tests()

    def _fake_rust_secret_inspection(self, command: str, payload: dict, **kwargs):
        self.assertEqual(command, "inspect-secret-reference")
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        risk_tags = " ".join(str(item or "").lower() for item in list(payload.get("risk_tags") or []))
        joined = " ".join(
            [
                str(metadata).lower(),
                risk_tags,
                str(payload.get("provider_id") or "").lower(),
                str(payload.get("connector_id") or "").lower(),
            ]
        )
        high_risk = bool(payload.get("approval_required")) or any(
            marker in joined
            for marker in ("admin", "financial", "finance", "billing", "payment", "payout")
        )
        if high_risk and not str(payload.get("approval_id") or "").strip():
            if kwargs.get("allow_approval_required"):
                return {
                    "ok": True,
                    "decision": "require_approval",
                    "reason": "secret_access_requires_approval",
                    "approval_required": True,
                    "next_action": "request_secret_access_approval",
                    "high_risk_credential": True,
                    "risk_level": "high",
                    "decision_id": "rkd_secret_requires_approval",
                }
            raise secrets_broker.rust_runtime_kernel_client.RustKernelApprovalRequired(
                {
                    "ok": True,
                    "decision": "require_approval",
                    "reason": "secret_access_requires_approval",
                    "approval_required": True,
                    "high_risk_credential": True,
                    "risk_level": "high",
                    "decision_id": "rkd_secret_requires_approval",
                },
                command="inspect-secret-reference",
            )
        return {
            "ok": True,
            "decision": "allow",
            "reason": "secret_access_approved" if high_risk else "secret_access_allowed_by_policy",
            "approval_required": False,
            "next_action": "allow_secret_resolution",
            "high_risk_credential": high_risk,
            "risk_level": "high" if high_risk else "medium",
            "decision_id": "rkd_secret_allowed",
        }

    def test_verify_secret_access_token_rejects_expired_grant(self):
        grant = secrets_broker.issue_connector_secret_grant(
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            credential_id="cred-telegram",
            connector_id="telegram_bot",
            ttl_seconds=1,
        )

        with self.assertRaises(secrets_broker.SecretAccessDeniedError) as raised:
            secrets_broker.verify_secret_access_token(
                grant.token,
                expected_secret_kind="connector_credential",
                expected_workspace_id="workspace-1",
                expected_credential_id="cred-telegram",
                now=grant.claims["expires_at"] + 1,
            )

        self.assertEqual(raised.exception.code, "token_expired")

    def test_resolve_connector_secret_projects_allowed_fields_and_keeps_metadata(self):
        with patch(
            "server_modules.secrets_broker.control_plane_repository.append_agent_secret_access_event",
            new=AsyncMock(return_value={"id": "sevt-1"}),
        ) as audit_mock:
            secret = secrets_broker.resolve_connector_secret(
                _load_vault,
                lambda encrypted: encrypted,
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                credential_id="cred-telegram",
                connector_id="telegram_bot",
                tool_name="send_message",
                action_id="send_message",
                allowed_fields=["chat_id"],
            )

        self.assertEqual(secret["chat_id"], "chat-123")
        self.assertNotIn("api_key", secret)
        self.assertEqual(secret["_provider"], "telegram_bot")
        self.assertEqual(secret["_label"], "Telegram Bot")
        self.assertTrue(audit_mock.called)

    def test_resolve_connector_secret_rejects_connector_mismatch_and_audits_denial(self):
        with patch(
            "server_modules.secrets_broker.control_plane_repository.append_agent_secret_access_event",
            new=AsyncMock(return_value={"id": "sevt-2"}),
        ) as audit_mock:
            with self.assertRaises(secrets_broker.SecretAccessDeniedError) as raised:
                secrets_broker.resolve_connector_secret(
                    _load_vault,
                    lambda encrypted: encrypted,
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    credential_id="cred-telegram",
                    connector_id="google_workspace",
                    tool_name="send_email",
                )

        self.assertEqual(raised.exception.code, "connector_mismatch")
        self.assertTrue(audit_mock.called)
        self.assertEqual(audit_mock.await_args.kwargs["status"], "denied")

    def test_resolve_connector_secret_audits_connector_class_metadata(self):
        with patch(
            "server_modules.secrets_broker.control_plane_repository.append_agent_secret_access_event",
            new=AsyncMock(return_value={"id": "sevt-class"}),
        ) as audit_mock:
            secret = secrets_broker.resolve_connector_secret(
                _load_vault,
                lambda encrypted: encrypted,
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                credential_id="cred-telegram",
                connector_id="telegram_bot",
                connector_class="api_connector",
                tool_name="send_message",
                action_id="send_message",
                allowed_fields=["chat_id"],
            )

        self.assertEqual(secret["chat_id"], "chat-123")
        self.assertEqual(audit_mock.await_args.kwargs["metadata"]["connector_class"], "api_connector")

    def test_resolve_provider_secret_supports_default_provider_resolution(self):
        with patch(
            "server_modules.secrets_broker.control_plane_repository.append_agent_secret_access_event",
            new=AsyncMock(return_value={"id": "sevt-3"}),
        ) as audit_mock:
            secret = secrets_broker.resolve_provider_secret(
                _load_vault,
                lambda encrypted: encrypted,
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                provider_id="openai",
                tool_name="provider_inference",
                run_id="run-1",
            )

        self.assertEqual(secret["access_token"], "openai-secret")
        self.assertEqual(secret["_provider"], "openai")
        self.assertTrue(audit_mock.called)

    def test_resolve_provider_secret_prefers_workspace_scoped_credential(self):
        def _load_workspace_vault():
            return {
                "credentials": [
                    {
                        "id": "cred-openai-global",
                        "provider": "openai",
                        "label": "OpenAI Global",
                        "workspace_id": None,
                        "encrypted_secret": json.dumps({"access_token": "global-token"}),
                        "updated_at": "2026-04-01T00:00:00Z",
                    },
                    {
                        "id": "cred-openai-workspace",
                        "provider": "openai",
                        "label": "OpenAI Workspace",
                        "workspace_id": "workspace-2",
                        "encrypted_secret": json.dumps({"access_token": "workspace-token"}),
                        "updated_at": "2026-04-10T00:00:00Z",
                    },
                ]
            }

        with patch(
            "server_modules.secrets_broker.control_plane_repository.append_agent_secret_access_event",
            new=AsyncMock(return_value={"id": "sevt-3b"}),
        ):
            secret = secrets_broker.resolve_provider_secret(
                _load_workspace_vault,
                lambda encrypted: encrypted,
                tenant_id="tenant-1",
                workspace_id="workspace-2",
                provider_id="openai",
                tool_name="provider_inference",
            )

        self.assertEqual(secret["access_token"], "workspace-token")
        self.assertEqual(secret["_workspace_id"], "workspace-2")

    def test_resolve_connector_secret_rejects_revoked_credential(self):
        safe_mode_service.revoke_connector_access(
            workspace_id="workspace-1",
            connector_id="telegram_bot",
            credential_id="cred-telegram",
            reason="incident",
        )
        with patch(
            "server_modules.secrets_broker.control_plane_repository.append_agent_secret_access_event",
            new=AsyncMock(return_value={"id": "sevt-4"}),
        ) as audit_mock:
            with self.assertRaises(secrets_broker.SecretAccessDeniedError) as raised:
                secrets_broker.resolve_connector_secret(
                    _load_vault,
                    lambda encrypted: encrypted,
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    credential_id="cred-telegram",
                    connector_id="telegram_bot",
                    tool_name="send_message",
                )

        self.assertEqual(raised.exception.code, "credential_rotation_required")
        self.assertEqual(audit_mock.await_args.kwargs["status"], "denied")

    def test_resolve_connector_secret_rejects_rotated_out_credential(self):
        safe_mode_service.rotate_connector_access(
            workspace_id="workspace-1",
            connector_id="telegram_bot",
            previous_credential_id="cred-telegram",
            replacement_credential_id="cred-telegram-new",
            reason="rotated",
        )
        with patch(
            "server_modules.secrets_broker.control_plane_repository.append_agent_secret_access_event",
            new=AsyncMock(return_value={"id": "sevt-5"}),
        ):
            with self.assertRaises(secrets_broker.SecretAccessDeniedError) as raised:
                secrets_broker.resolve_connector_secret(
                    _load_vault,
                    lambda encrypted: encrypted,
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    credential_id="cred-telegram",
                    connector_id="telegram_bot",
                    tool_name="send_message",
                )

        self.assertEqual(raised.exception.code, "credential_rotated")

    def test_resolve_provider_secret_rejects_domain_outside_scope(self):
        with patch(
            "server_modules.secrets_broker.control_plane_repository.append_agent_secret_access_event",
            new=AsyncMock(return_value={"id": "sevt-domain"}),
        ):
            with self.assertRaises(secrets_broker.SecretAccessDeniedError) as raised:
                secrets_broker.resolve_provider_secret(
                    _load_vault,
                    lambda encrypted: encrypted,
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    provider_id="openai",
                    tool_name="provider_inference",
                    target_domain="api.not-openai.com",
                    allowed_domains=["api.openai.com"],
                )

        self.assertEqual(raised.exception.code, "domain_scope_denied")

    def test_resolve_provider_secret_requires_approval_for_high_risk_credentials(self):
        def _load_risky_vault():
            payload = _load_vault()
            for entry in payload.get("credentials", []):
                if entry.get("id") == "cred-openai":
                    entry["metadata"] = {"sensitivity": "financial_admin"}
            return payload

        with patch(
            "server_modules.secrets_broker.control_plane_repository.append_agent_secret_access_event",
            new=AsyncMock(return_value={"id": "sevt-approval"}),
        ):
            with self.assertRaises(secrets_broker.SecretAccessDeniedError) as raised:
                secrets_broker.resolve_provider_secret(
                    _load_risky_vault,
                    lambda encrypted: encrypted,
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    provider_id="openai",
                    tool_name="provider_inference",
                )

        self.assertEqual(raised.exception.code, "approval_required")

        with patch(
            "server_modules.secrets_broker.control_plane_repository.append_agent_secret_access_event",
            new=AsyncMock(return_value={"id": "sevt-approval-ok"}),
        ):
            secret = secrets_broker.resolve_provider_secret(
                _load_risky_vault,
                lambda encrypted: encrypted,
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                provider_id="openai",
                tool_name="provider_inference",
                approval_id="appr_123",
                approval_actor_id="owner_1",
                approval_reason="approved_for_billing_automation",
            )

        self.assertEqual(secret.get("access_token"), "openai-secret")

    def test_resolve_provider_secret_blocks_unexpected_rust_next_action(self):
        with patch(
            "server_modules.secrets_broker.control_plane_repository.append_agent_secret_access_event",
            new=AsyncMock(return_value={"id": "sevt-wrong-action"}),
        ), patch.object(
            secrets_broker.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "secret_access_allowed_by_policy",
                "approval_required": False,
                "next_action": "request_secret_access_approval",
                "high_risk_credential": False,
                "risk_level": "medium",
                "decision_id": "rkd_secret_wrong_action",
            },
        ):
            with self.assertRaises(secrets_broker.SecretAccessDeniedError) as raised:
                secrets_broker.resolve_provider_secret(
                    _load_vault,
                    lambda encrypted: encrypted,
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    provider_id="openai",
                    tool_name="provider_inference",
                )

        self.assertEqual(raised.exception.code, "unexpected_next_action")

    def test_resolve_provider_secret_denies_revoked_session_scope(self):
        with patch(
            "server_modules.secrets_broker.control_plane_repository.append_agent_secret_access_event",
            new=AsyncMock(return_value={"id": "sevt-session"}),
        ):
            first = secrets_broker.resolve_provider_secret(
                _load_vault,
                lambda encrypted: encrypted,
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                provider_id="openai",
                tool_name="provider_inference",
                session_id="vcsess_1",
            )
            self.assertEqual(first.get("access_token"), "openai-secret")
            secrets_broker.revoke_secret_grant_scope(session_id="vcsess_1")
            with self.assertRaises(secrets_broker.SecretAccessDeniedError) as raised:
                secrets_broker.resolve_provider_secret(
                    _load_vault,
                    lambda encrypted: encrypted,
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    provider_id="openai",
                    tool_name="provider_inference",
                    session_id="vcsess_1",
                )

        self.assertEqual(raised.exception.code, "grant_revoked")


if __name__ == "__main__":
    unittest.main()
