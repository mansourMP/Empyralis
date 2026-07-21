"""Security audit deliverable (2026-07-21, fixed 2026-07-21): does the LIVE
connector-credential resolution path — the one a real tool call hits when an
agent invokes a connector action (Stripe, Gmail, GitHub, ...) — respect
per-agent credential ownership, the same way memory does?

Original finding: `secrets_broker.resolve_provider_secret` (the no-explicit-
credential_id path — what a tool call hits when it asks for "the stripe
credential" rather than a specific vault row id) picked a credential using
only (workspace_id, provider) plus "most recently updated wins". It accepted
an `actor_id`/`actor_type` kwarg, but that was used ONLY for the audit-log
entry, never as a resolution filter. So if agent A and agent B each connect
their OWN Stripe account in the same workspace (the documented per-agent
connector model), a `resolve_provider_secret("stripe", ..., actor_type="agent",
actor_id="agent-a")` call could return agent B's Stripe credential —
determined by nothing more than which row was updated more recently.

Fix (server_modules/secrets_broker.py, resolve_provider_secret): when the
caller identifies itself as `actor_type="agent"` with a concrete `actor_id`
and a `workspace_id`, resolution is routed through
`vault_helpers.resolve_agent_credential` instead of the raw
(workspace, provider) lookup. That function's own scoring (exact agent match
> unassigned workspace-default credential > reject — never another agent's
row) is what now backs the live path; no new isolation logic was invented.
Callers that don't identify as a specific agent (actor_type != "agent" —
e.g. the runtime resolving its own model-provider key, workflow runs,
provider-profile candidate resolution) are byte-for-byte unaffected: none of
today's live callers pass actor_type="agent", so they keep the original
(provider, workspace) "most recently updated" behavior unchanged.

These tests exercise real (non-mocked) resolution logic against an injected
in-memory vault — no Postgres or Rust kernel required beyond the existing
test doubles secrets_broker's own suite already uses.
"""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import safe_mode_service, secrets_broker, vault_helpers


def _two_agents_same_provider_vault():
    """Agent A and Agent B each connected their OWN Stripe credential in the
    SAME workspace — the documented per-agent connector model. Agent B's was
    connected more recently (the only signal resolve_default_vault_credential
    uses when no explicit credential_id is given)."""
    return {
        "credentials": [
            {
                "id": "cred-stripe-agent-a",
                "provider": "stripe",
                "label": "Agent A's Stripe",
                "workspace_id": "ws-1",
                "agent_install_id": "agent-a",
                "encrypted_secret": json.dumps({"api_key": "sk_live_AGENT_A_SECRET"}),
                "metadata": {},
                "updated_at": "2026-07-01T00:00:00Z",
            },
            {
                "id": "cred-stripe-agent-b",
                "provider": "stripe",
                "label": "Agent B's Stripe",
                "workspace_id": "ws-1",
                "agent_install_id": "agent-b",
                "encrypted_secret": json.dumps({"api_key": "sk_live_AGENT_B_SECRET"}),
                "metadata": {},
                "updated_at": "2026-07-15T00:00:00Z",
            },
        ]
    }


class _SecretsBrokerHarness(unittest.TestCase):
    """Same fixture pattern as test_secrets_broker.py's own suite: a real
    HMAC secret and a fake Rust kernel that always allows (this audit is
    about identity scoping, not the approval gate)."""

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


class LiveProviderSecretResolutionCrossAgentTests(_SecretsBrokerHarness):
    """The path a real tool call actually hits: secrets_broker.resolve_provider_secret,
    with no explicit credential_id (an agent tool call asks for "the stripe
    credential", not a specific row id — the row id is an implementation
    detail the model never sees)."""

    def test_agent_a_tool_call_must_not_receive_agent_bs_stripe_key(self) -> None:
        secret = secrets_broker.resolve_provider_secret(
            _two_agents_same_provider_vault,
            lambda encrypted: encrypted,
            tenant_id="tenant-1",
            workspace_id="ws-1",
            provider_id="stripe",
            tool_name="stripe.create_charge",
            actor_type="agent",
            actor_id="agent-a",
        )
        self.assertEqual(
            secret["api_key"],
            "sk_live_AGENT_A_SECRET",
            "agent A's own tool call resolved to agent B's Stripe key instead of "
            "its own — confirmed live cross-agent credential crossing. "
            f"actor_id='agent-a' was passed to resolve_provider_secret but had no "
            f"effect on which of the two per-agent-scoped rows was returned "
            f"(got: {secret['api_key']}).",
        )

    def test_agent_b_tool_call_must_not_receive_agent_as_stripe_key(self) -> None:
        secret = secrets_broker.resolve_provider_secret(
            _two_agents_same_provider_vault,
            lambda encrypted: encrypted,
            tenant_id="tenant-1",
            workspace_id="ws-1",
            provider_id="stripe",
            tool_name="stripe.create_charge",
            actor_type="agent",
            actor_id="agent-b",
        )
        self.assertEqual(secret["api_key"], "sk_live_AGENT_B_SECRET")


class AgentAwareResolverExistsAndIsNowWiredTests(unittest.TestCase):
    """Positive control: vault_helpers.resolve_agent_credential (the Stage 4B
    function whose own docstring says it should be the live entry point)
    DOES correctly isolate agent-scoped credentials when actually used. This
    proves the isolation gap above was a wiring gap, not a missing capability
    — the fix routes secrets_broker.resolve_provider_secret through this
    existing function for agent-identified callers, rather than inventing new
    isolation logic."""

    def test_resolve_agent_credential_correctly_isolates_by_agent(self) -> None:
        secret_a = vault_helpers.resolve_agent_credential(
            _two_agents_same_provider_vault,
            lambda encrypted: encrypted,
            "stripe",
            "ws-1",
            "agent-a",
        )
        self.assertEqual(secret_a["api_key"], "sk_live_AGENT_A_SECRET")

        secret_b = vault_helpers.resolve_agent_credential(
            _two_agents_same_provider_vault,
            lambda encrypted: encrypted,
            "stripe",
            "ws-1",
            "agent-b",
        )
        self.assertEqual(secret_b["api_key"], "sk_live_AGENT_B_SECRET")

    def test_resolve_agent_credential_is_now_referenced_in_secrets_broker(self) -> None:
        """Confirms the wiring landed: secrets_broker.resolve_provider_secret
        now routes agent-identified calls through resolve_agent_credential
        instead of the raw (workspace, provider) "most recently updated"
        lookup. Inverse of the old guardrail that documented the gap."""
        import inspect

        from server_modules import secrets_broker as _sb

        broker_source = inspect.getsource(_sb)
        self.assertIn(
            "resolve_agent_credential",
            broker_source,
            "resolve_agent_credential is expected to be wired into "
            "secrets_broker.resolve_provider_secret for actor_type='agent' callers.",
        )


def _workspace_shared_plus_agent_specific_vault():
    """A genuinely workspace-shared (unassigned) credential for one provider
    — e.g. a CRM connector the owner explicitly shares across all of their
    agents, with no agent_install_id set — alongside an unrelated
    agent-specific credential for a different provider. Exercises the
    "legitimate sharing must still resolve" requirement: resolve_agent_credential
    falls back to an unassigned workspace-default row when no row is scoped to
    the specific requesting agent, but never crosses into another agent's row."""
    return {
        "credentials": [
            {
                "id": "cred-crm-shared",
                "provider": "shared-crm",
                "label": "Team-wide CRM",
                "workspace_id": "ws-1",
                # No agent_install_id / agent_id — legitimately workspace-shared.
                "encrypted_secret": json.dumps({"api_key": "sk_live_SHARED_CRM_SECRET"}),
                "metadata": {},
                "updated_at": "2026-07-01T00:00:00Z",
            },
            {
                "id": "cred-stripe-agent-a",
                "provider": "stripe",
                "label": "Agent A's Stripe",
                "workspace_id": "ws-1",
                "agent_install_id": "agent-a",
                "encrypted_secret": json.dumps({"api_key": "sk_live_AGENT_A_SECRET"}),
                "metadata": {},
                "updated_at": "2026-07-01T00:00:00Z",
            },
        ]
    }


class LegitimateSharingStillResolvesTests(_SecretsBrokerHarness):
    """The fix must not turn a genuinely workspace-shared credential (no
    agent_install_id — e.g. the owner's own connector every agent may use)
    into a hard 404 for every agent. Both agent-a (who also has its own,
    unrelated stripe credential) and agent-c (a totally different agent with
    no credential of its own) must still resolve the shared "shared-crm"
    credential via the live resolve_provider_secret path."""

    def test_workspace_shared_credential_resolves_for_any_agent(self) -> None:
        for acting_agent in ("agent-a", "agent-c"):
            secret = secrets_broker.resolve_provider_secret(
                _workspace_shared_plus_agent_specific_vault,
                lambda encrypted: encrypted,
                tenant_id="tenant-1",
                workspace_id="ws-1",
                provider_id="shared-crm",
                tool_name="shared_crm.lookup_contact",
                actor_type="agent",
                actor_id=acting_agent,
            )
            self.assertEqual(secret["api_key"], "sk_live_SHARED_CRM_SECRET")

    def test_same_agent_resolution_still_works_alongside_shared_credential(self) -> None:
        """Legitimate same-agent resolution (the positive case) keeps working
        even in a vault that also contains an unrelated shared credential."""
        secret = secrets_broker.resolve_provider_secret(
            _workspace_shared_plus_agent_specific_vault,
            lambda encrypted: encrypted,
            tenant_id="tenant-1",
            workspace_id="ws-1",
            provider_id="stripe",
            tool_name="stripe.create_charge",
            actor_type="agent",
            actor_id="agent-a",
        )
        self.assertEqual(secret["api_key"], "sk_live_AGENT_A_SECRET")

    def test_non_agent_actor_keeps_original_workspace_provider_lookup(self) -> None:
        """Callers that don't identify as a specific agent (the runtime
        resolving its own model-provider key, workflow runs, provider-profile
        candidate resolution, ...) are unaffected by the fix — they keep the
        original (provider, workspace) lookup, unfiltered by actor identity."""
        secret = secrets_broker.resolve_provider_secret(
            _two_agents_same_provider_vault,
            lambda encrypted: encrypted,
            tenant_id="tenant-1",
            workspace_id="ws-1",
            provider_id="stripe",
            tool_name="stripe.create_charge",
            actor_type="runtime",
            actor_id=None,
        )
        # No agent identity given, so this is the pre-existing "most recently
        # updated wins" behavior — agent B's credential was updated later.
        self.assertEqual(secret["api_key"], "sk_live_AGENT_B_SECRET")


if __name__ == "__main__":
    unittest.main()
