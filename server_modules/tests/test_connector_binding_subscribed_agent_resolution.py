"""§1.2 (Multiplayer Projects plan) — connector reuse button lies.

Verified gap: connectors_actions.subscribe_agent_to_project_credential
(:2884-2937) writes an ENABLED agent_connector_bindings row pointing a
SUBSCRIBED agent at an EXISTING project-scoped credential it did not itself
connect — the documented "reuse, one click, no re-auth" path. But
vault_helpers.resolve_agent_credential (:203-285, the function
secrets_broker.resolve_provider_secret's agent-identified branch routes
through — secrets_broker.py:1104-1129) matches ONLY
vault_credentials.agent_install_id/agent_id. It has no idea bindings exist,
so a subscribed (non-connecting) agent's tool call raised
RuntimeError("No credential for provider ... — no silent fallback") even
though the UI told the owner reuse would work.

Fix (secrets_broker.py, resolve_provider_secret): on a RuntimeError from
resolve_agent_credential, consult agent_connector_bindings for an ENABLED
binding for (workspace_id, agent_id, connector_key=provider_id) and resolve
THAT binding's credential_id directly via the existing
vault_helpers.resolve_vault_credential — never the unscoped "most recently
updated in the workspace" fallback runs_execution.py's
_workflow_tool_connector_secret uses for a different, milder-risk case.

These tests exercise the real (non-mocked) secrets_broker.resolve_provider_
secret path against an injected in-memory vault, with only the Postgres-
backed agent_bindings_repository call mocked (same test-double boundary
test_connector_credential_cross_agent_isolation.py already uses).
"""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import safe_mode_service, secrets_broker


def _single_owner_vault():
    """Only ONE vault credential exists for 'stripe', owned by the agent
    that actually ran the OAuth connect ("agent-connect"). A SECOND agent
    ("agent-subscribe") never gets its own vault_credentials row under the
    reuse model — its only trace is the binding, injected per-test below."""
    return {
        "credentials": [
            {
                "id": "cred-stripe-shared-project",
                "provider": "stripe",
                "label": "Stripe (connected by agent-connect)",
                "workspace_id": "ws-1",
                "agent_install_id": "agent-connect",
                "encrypted_secret": json.dumps({"api_key": "sk_live_PROJECT_STRIPE_SECRET"}),
                "metadata": {},
                "updated_at": "2026-07-01T00:00:00Z",
            },
        ]
    }


class _SecretsBrokerHarness(unittest.TestCase):
    """Same fixture pattern as test_secrets_broker.py / test_connector_
    credential_cross_agent_isolation.py: a real HMAC secret and a fake Rust
    kernel that always allows (these tests are about credential-resolution
    identity, not the approval gate)."""

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
            side_effect=self._fake_allow,
        )
        self._kernel_patch.start()
        self._audit_patch = patch(
            "server_modules.secrets_broker.control_plane_repository.append_agent_secret_access_event",
            new=AsyncMock(return_value={"id": "sevt-test"}),
        )
        self._audit_patch.start()

    def tearDown(self) -> None:
        self._audit_patch.stop()
        self._kernel_patch.stop()
        self._env_patch.stop()
        safe_mode_service.reset_state_for_tests()
        secrets_broker.reset_secret_grant_revocations_for_tests()

    @staticmethod
    def _fake_allow(command, payload, **kwargs):
        return {
            "ok": True,
            "decision": "allow",
            "reason": "secret_access_allowed_by_policy",
            "approval_required": False,
            "next_action": "allow_secret_resolution",
            "high_risk_credential": False,
            "risk_level": "low",
            "decision_id": "rkd_secret_allowed",
        }


class SubscribedAgentReuseResolutionTests(_SecretsBrokerHarness):
    def test_subscribed_agent_resolves_the_bound_project_credential(self) -> None:
        """The exact bug: agent-subscribe clicked "reuse" in the UI
        (subscribe_agent_to_project_credential wrote an enabled binding), but
        owns no vault_credentials row of its own. Its tool call must resolve
        the project credential via the binding, not 404."""
        with patch(
            "server_modules.agent_bindings_repository.list_agent_connector_bindings",
            new=AsyncMock(return_value=[
                {
                    "key": "stripe",
                    "agent_install_id": "agent-subscribe",
                    "enabled": True,
                    "binding": {"credential_id": "cred-stripe-shared-project"},
                },
            ]),
        ) as mock_bindings:
            secret = secrets_broker.resolve_provider_secret(
                _single_owner_vault,
                lambda encrypted: encrypted,
                tenant_id="tenant-1",
                workspace_id="ws-1",
                provider_id="stripe",
                tool_name="stripe.create_charge",
                actor_type="agent",
                actor_id="agent-subscribe",
            )
        self.assertEqual(secret["api_key"], "sk_live_PROJECT_STRIPE_SECRET")
        mock_bindings.assert_awaited()

    def test_connecting_agent_never_needs_the_binding_fallback(self) -> None:
        """Migration/behavior safety: the agent that actually OWNS the vault
        credential resolves it directly via resolve_agent_credential exactly
        as before — the new binding lookup must not even be attempted, let
        alone change the outcome."""
        with patch(
            "server_modules.agent_bindings_repository.list_agent_connector_bindings",
            new=AsyncMock(side_effect=AssertionError("binding fallback should not run for the owning agent")),
        ) as mock_bindings:
            secret = secrets_broker.resolve_provider_secret(
                _single_owner_vault,
                lambda encrypted: encrypted,
                tenant_id="tenant-1",
                workspace_id="ws-1",
                provider_id="stripe",
                tool_name="stripe.create_charge",
                actor_type="agent",
                actor_id="agent-connect",
            )
        self.assertEqual(secret["api_key"], "sk_live_PROJECT_STRIPE_SECRET")
        mock_bindings.assert_not_called()

    def test_agent_with_no_credential_and_no_binding_still_fails_loudly(self) -> None:
        """No silent fallback: an agent with neither its own credential NOR
        an enabled binding must still be rejected -- the fix must not widen
        access beyond the explicit binding."""
        with patch(
            "server_modules.agent_bindings_repository.list_agent_connector_bindings",
            new=AsyncMock(return_value=[]),
        ):
            with self.assertRaises(RuntimeError):
                secrets_broker.resolve_provider_secret(
                    _single_owner_vault,
                    lambda encrypted: encrypted,
                    tenant_id="tenant-1",
                    workspace_id="ws-1",
                    provider_id="stripe",
                    tool_name="stripe.create_charge",
                    actor_type="agent",
                    actor_id="agent-stranger",
                )

    def test_disabled_or_wrong_connector_binding_does_not_resolve(self) -> None:
        """A binding row for a DIFFERENT connector_key must not leak this
        credential to an unrelated tool call."""
        with patch(
            "server_modules.agent_bindings_repository.list_agent_connector_bindings",
            new=AsyncMock(return_value=[
                {
                    "key": "github",  # wrong connector — subscribed to something else entirely
                    "agent_install_id": "agent-subscribe",
                    "enabled": True,
                    "binding": {"credential_id": "cred-stripe-shared-project"},
                },
            ]),
        ):
            with self.assertRaises(RuntimeError):
                secrets_broker.resolve_provider_secret(
                    _single_owner_vault,
                    lambda encrypted: encrypted,
                    tenant_id="tenant-1",
                    workspace_id="ws-1",
                    provider_id="stripe",
                    tool_name="stripe.create_charge",
                    actor_type="agent",
                    actor_id="agent-subscribe",
                )

    def test_binding_lookup_failure_surfaces_the_original_runtime_error(self) -> None:
        """If the bindings repository itself is unavailable, the ORIGINAL
        "no credential" RuntimeError from resolve_agent_credential must still
        surface -- never masked, never silently swallowed into a different
        failure mode."""
        with patch(
            "server_modules.agent_bindings_repository.list_agent_connector_bindings",
            new=AsyncMock(side_effect=RuntimeError("control plane unavailable")),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                secrets_broker.resolve_provider_secret(
                    _single_owner_vault,
                    lambda encrypted: encrypted,
                    tenant_id="tenant-1",
                    workspace_id="ws-1",
                    provider_id="stripe",
                    tool_name="stripe.create_charge",
                    actor_type="agent",
                    actor_id="agent-subscribe",
                )
        self.assertIn("No credential for provider", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
