"""Tests for specialist_runtime_context.resolve_specialist_runtime_context.

Two independent areas covered in this file:

1. MAN-310 Phase 1 — resolve_specialist_runtime_context must resolve
   model_config.engine onto SpecialistRuntimeContext.engine the same way it
   already resolves reasoning_effort/mode/runtime — a per-agent choice read
   straight off the install bundle's install_metadata.model_config,
   lowercased, defaulting to "" (unset) when absent.

2. U3-K — the project-level default-gateway fallback: an agent's own
   explicit model_config.gateway_binding (cli_subscription/local modes) and
   install-level preferred_gateway_id (tool dispatch) always win when set; a
   project's metadata.default_gateway_id (projects_repository.
   set_project_default_gateway) only fills in for whichever of those two
   fields the agent carries none of its own. Every path must fail safe — no
   project, no default, a deleted project, or a lookup error must all reduce
   to exactly the pre-U3-K behavior, never raise.

Mirrors the mocking pattern test_agent_capability_service.py's
ResolveByIdTests already uses for the same
agent_registry_repository.get_workspace_agent_install_bundle /
get_workspace_master_agent_install calls this module makes.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import specialist_runtime_context as src
from server_modules import specialist_runtime_context as ctx


def _run(coro):
    return asyncio.run(coro)


def _engine_bundle(*, agent_id="agent-1", label="Research Agent", model_config=None, agent_kind="specialist"):
    return {
        "id": agent_id,
        "label": label,
        "project_id": "proj-1",
        "agent_definition": {"agent_kind": agent_kind},
        "metadata": {
            "instructions": "Help with research.",
            "model_config": dict(model_config or {}),
        },
    }


class ResolveSpecialistRuntimeContextEngineFieldTests(unittest.TestCase):
    @staticmethod
    def _resolve(model_config):
        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_master_agent_install",
                new=AsyncMock(return_value={"id": "master-1"}),
            ),
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=_engine_bundle(model_config=model_config)),
            ),
        ):
            return _run(src.resolve_specialist_runtime_context(
                workspace_id="ws-1", tenant_id="default", active_agent_install_id="agent-1",
            ))

    def test_engine_is_resolved_from_model_config(self):
        result = self._resolve({"mode": "byok_api", "engine": "claude_agent_sdk"})
        self.assertEqual(result.engine, "claude_agent_sdk")

    def test_engine_defaults_to_empty_string_when_unset(self):
        result = self._resolve({"mode": "byok_api"})
        self.assertEqual(result.engine, "")

    def test_engine_defaults_to_empty_string_when_model_config_absent(self):
        result = self._resolve({})
        self.assertEqual(result.engine, "")

    def test_engine_value_is_lowercased(self):
        result = self._resolve({"mode": "byok_api", "engine": "Claude_Agent_SDK"})
        self.assertEqual(result.engine, "claude_agent_sdk")

    def test_engine_does_not_affect_other_resolved_fields(self):
        """Sanity check: adding engine resolution must not disturb the
        existing mode/provider/reasoning_effort resolution it sits next to."""
        result = self._resolve({
            "mode": "byok_api", "provider": "anthropic", "model": "claude-sonnet-4-6",
            "reasoning_effort": "high", "engine": "claude_agent_sdk",
        })
        self.assertEqual(result.mode, "byok_api")
        self.assertEqual(result.provider, "anthropic")
        self.assertEqual(result.model, "claude-sonnet-4-6")
        self.assertEqual(result.reasoning_effort, "high")
        self.assertEqual(result.engine, "claude_agent_sdk")


_MASTER = {"id": "master-install-1"}


def _bundle(*, metadata=None, project_id="project-1", label="Rex"):
    return {
        "id": "install-1",
        "label": label,
        "project_id": project_id,
        "agent_definition": {"agent_kind": "specialist"},
        "metadata": metadata if metadata is not None else {
            "model_config": {"mode": "cli_subscription", "gateway_binding": ""},
        },
    }


def _patched(bundle, project_result=None, project_side_effect=None, gateway_opted_in=True):
    """Context manager stack shared by every test below — patches the four
    repository calls resolve_specialist_runtime_context makes, in the exact
    module paths it imports them from (server_modules.agent_registry_
    repository / server_modules.projects_repository /
    server_modules.gateway_state_repository), so patching works whether the
    call resolves the module at import time or at call time (this module
    does both, locally, inside the function body).

    gateway_opted_in defaults to True: every pre-existing test in this file
    predates the hardware-owner-opt-in gate and is exercising the OTHER
    fallback logic (own-binding-wins, mode scoping, fail-safe lookup
    failures), so it should behave exactly as before unless a test
    deliberately passes gateway_opted_in=False to exercise the new gate
    itself (see GatewayOwnerOptInGateTests below)."""
    kwargs = {}
    if project_side_effect is not None:
        kwargs["side_effect"] = project_side_effect
    else:
        kwargs["return_value"] = project_result
    return (
        patch(
            "server_modules.agent_registry_repository.get_workspace_master_agent_install",
            new=AsyncMock(return_value=_MASTER),
        ),
        patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            new=AsyncMock(return_value=bundle),
        ),
        patch("server_modules.projects_repository.get_project", new=AsyncMock(**kwargs)),
        patch(
            "server_modules.gateway_state_repository.gateway_project_sharing_opted_in",
            return_value=gateway_opted_in,
        ),
    )


class ProjectDefaultGatewayFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_agents_own_binding_wins_over_project_default(self):
        """Both fields already set on the agent -> the project is never even
        consulted, and both values pass through untouched."""
        bundle = _bundle(
            metadata={
                "model_config": {"mode": "cli_subscription", "gateway_binding": "gw-own"},
                "preferred_gateway_id": "pref-own",
            },
        )
        p1, p2, p3, p4 = _patched(bundle, project_result={"default_gateway_id": "gw-project-default"})
        with p1, p2, p3 as mock_get_project, p4:
            result = await ctx.resolve_specialist_runtime_context(
                workspace_id="ws-1", tenant_id="t1", active_agent_install_id="install-1",
            )
        self.assertIsNotNone(result)
        self.assertEqual(result.gateway_binding, "gw-own")
        self.assertEqual(result.preferred_gateway_id, "pref-own")
        mock_get_project.assert_not_awaited()
        self.assertNotIn("gateway_binding_from", result.source)
        self.assertNotIn("preferred_gateway_id_from", result.source)

    async def test_project_default_used_when_agent_has_none(self):
        """Agent carries neither field -> both inherit the project's default."""
        bundle = _bundle()
        p1, p2, p3, p4 = _patched(bundle, project_result={"default_gateway_id": "gw-project-default"})
        with p1, p2, p3, p4:
            result = await ctx.resolve_specialist_runtime_context(
                workspace_id="ws-1", tenant_id="t1", active_agent_install_id="install-1",
            )
        self.assertIsNotNone(result)
        self.assertEqual(result.gateway_binding, "gw-project-default")
        self.assertEqual(result.preferred_gateway_id, "gw-project-default")
        self.assertEqual(result.source.get("gateway_binding_from"), "project_default")
        self.assertEqual(result.source.get("preferred_gateway_id_from"), "project_default")

    async def test_gateway_binding_fallback_scoped_to_brain_hosting_modes(self):
        """gateway_binding is only meaningful for cli_subscription/local — a
        platform_credits agent must not have it silently populated, even
        though preferred_gateway_id (tool dispatch, mode-independent) still
        inherits."""
        bundle = _bundle(metadata={"model_config": {"mode": "platform_credits", "gateway_binding": ""}})
        p1, p2, p3, p4 = _patched(bundle, project_result={"default_gateway_id": "gw-project-default"})
        with p1, p2, p3, p4:
            result = await ctx.resolve_specialist_runtime_context(
                workspace_id="ws-1", tenant_id="t1", active_agent_install_id="install-1",
            )
        self.assertIsNotNone(result)
        self.assertEqual(result.gateway_binding, "")
        self.assertEqual(result.preferred_gateway_id, "gw-project-default")

    async def test_no_project_default_set_behaves_like_before(self):
        """The project exists but carries no default -> identical to a
        workspace with no U3-K feature at all."""
        bundle = _bundle()
        p1, p2, p3, p4 = _patched(bundle, project_result={"default_gateway_id": ""})
        with p1, p2, p3, p4:
            result = await ctx.resolve_specialist_runtime_context(
                workspace_id="ws-1", tenant_id="t1", active_agent_install_id="install-1",
            )
        self.assertIsNotNone(result)
        self.assertEqual(result.gateway_binding, "")
        self.assertEqual(result.preferred_gateway_id, "")
        self.assertNotIn("gateway_binding_from", result.source)

    async def test_no_project_id_never_looks_up_a_project(self):
        """An agent with no project at all (project_id="") must not even
        attempt the lookup."""
        bundle = _bundle(project_id="")
        p1, p2, p3, p4 = _patched(bundle, project_result={"default_gateway_id": "gw-project-default"})
        with p1, p2, p3 as mock_get_project, p4:
            result = await ctx.resolve_specialist_runtime_context(
                workspace_id="ws-1", tenant_id="t1", active_agent_install_id="install-1",
            )
        self.assertIsNotNone(result)
        self.assertEqual(result.gateway_binding, "")
        self.assertEqual(result.preferred_gateway_id, "")
        mock_get_project.assert_not_awaited()

    async def test_deleted_project_fails_safe(self):
        """get_project returning None (project id no longer resolves, e.g.
        deleted) must fail safe rather than raise or crash the turn."""
        bundle = _bundle()
        p1, p2, p3, p4 = _patched(bundle, project_result=None)
        with p1, p2, p3, p4:
            result = await ctx.resolve_specialist_runtime_context(
                workspace_id="ws-1", tenant_id="t1", active_agent_install_id="install-1",
            )
        self.assertIsNotNone(result)
        self.assertEqual(result.gateway_binding, "")
        self.assertEqual(result.preferred_gateway_id, "")

    async def test_project_lookup_exception_fails_safe(self):
        """A raised exception from the repository call (DB down, etc.) must
        never propagate out of context resolution — it must degrade to no
        fallback, exactly like "no project default set"."""
        bundle = _bundle()
        p1, p2, p3, p4 = _patched(bundle, project_side_effect=RuntimeError("db down"))
        with p1, p2, p3, p4:
            result = await ctx.resolve_specialist_runtime_context(
                workspace_id="ws-1", tenant_id="t1", active_agent_install_id="install-1",
            )
        self.assertIsNotNone(result)
        self.assertEqual(result.gateway_binding, "")
        self.assertEqual(result.preferred_gateway_id, "")


class GatewayOwnerOptInGateTests(unittest.IsolatedAsyncioTestCase):
    """CLAUDE.md: 'Hardware attaches to its owner, never to the project' —
    sharing is a per-machine opt-in by the hardware's owner, default off.
    projects_repository.set_project_default_gateway rejects an un-opted-in
    machine at SET time (see test_projects_repository_hardware_owner_opt_in.
    py), but resolution must ALSO re-check live, on every turn — never trust
    a value that got in before the check existed, or whose owner has since
    revoked consent. gateway_state_repository.gateway_project_sharing_
    opted_in is the single gate both paths call; these tests patch it
    directly (via _patched(..., gateway_opted_in=...)) to isolate THIS
    gate's behavior from project-lookup mechanics already covered above."""

    async def test_un_opted_in_project_default_grants_no_hardware(self):
        """The project HAS a default_gateway_id, and the agent has no
        binding of its own -- but the machine's owner never opted in. Must
        NOT raise, must NOT fill gateway_binding/preferred_gateway_id, and
        must record why in `source` (never a silent, unexplained no-op)."""
        bundle = _bundle()  # mode=cli_subscription, gateway_binding=""
        p1, p2, p3, p4 = _patched(
            bundle, project_result={"default_gateway_id": "gw-not-opted-in"}, gateway_opted_in=False,
        )
        with p1, p2, p3, p4:
            result = await ctx.resolve_specialist_runtime_context(
                workspace_id="ws-1", tenant_id="t1", active_agent_install_id="install-1",
            )
        self.assertIsNotNone(result)
        self.assertEqual(result.gateway_binding, "")
        self.assertEqual(result.preferred_gateway_id, "")
        self.assertNotIn("gateway_binding_from", result.source)
        self.assertNotIn("preferred_gateway_id_from", result.source)
        self.assertEqual(result.source.get("project_default_gateway_denied"), "no_owner_opt_in")

    async def test_un_opted_in_project_default_denies_platform_credits_agent_too(self):
        """The common case: a platform_credits specialist with no gateway
        preference of its own must not be pinned to an un-consented box's
        preferred_gateway_id either -- it simply runs with none (cloud-
        side), never a silent borrow of that hardware."""
        bundle = _bundle(metadata={"model_config": {"mode": "platform_credits", "gateway_binding": ""}})
        p1, p2, p3, p4 = _patched(
            bundle, project_result={"default_gateway_id": "gw-not-opted-in"}, gateway_opted_in=False,
        )
        with p1, p2, p3, p4:
            result = await ctx.resolve_specialist_runtime_context(
                workspace_id="ws-1", tenant_id="t1", active_agent_install_id="install-1",
            )
        self.assertIsNotNone(result)
        self.assertEqual(result.gateway_binding, "")
        self.assertEqual(result.preferred_gateway_id, "")

    async def test_opted_in_project_default_still_grants_hardware(self):
        """Sanity check on the gate itself: when the flag says opted in,
        behavior is unchanged from the pre-existing fallback (already
        covered in detail by ProjectDefaultGatewayFallbackTests, which all
        pass gateway_opted_in=True via _patched's default)."""
        bundle = _bundle()
        p1, p2, p3, p4 = _patched(
            bundle, project_result={"default_gateway_id": "gw-opted-in"}, gateway_opted_in=True,
        )
        with p1, p2, p3, p4:
            result = await ctx.resolve_specialist_runtime_context(
                workspace_id="ws-1", tenant_id="t1", active_agent_install_id="install-1",
            )
        self.assertEqual(result.gateway_binding, "gw-opted-in")
        self.assertEqual(result.preferred_gateway_id, "gw-opted-in")

    async def test_agents_own_explicit_binding_never_needs_opt_in_check(self):
        """An agent's own explicit gateway_binding/preferred_gateway_id
        (set directly on the agent, not inherited from a project default)
        must never be gated by this check at all -- the opt-in gate only
        ever applies to the PROJECT-DEFAULT fallback path, never to an
        agent's own deliberate configuration. gateway_project_sharing_
        opted_in is mocked to always return False here specifically to
        prove it is never even consulted for this case."""
        bundle = _bundle(
            metadata={
                "model_config": {"mode": "cli_subscription", "gateway_binding": "gw-own"},
                "preferred_gateway_id": "pref-own",
            },
        )
        p1, p2, p3, p4 = _patched(bundle, gateway_opted_in=False)
        with p1, p2, p3, p4:
            result = await ctx.resolve_specialist_runtime_context(
                workspace_id="ws-1", tenant_id="t1", active_agent_install_id="install-1",
            )
        self.assertEqual(result.gateway_binding, "gw-own")
        self.assertEqual(result.preferred_gateway_id, "pref-own")


if __name__ == "__main__":
    unittest.main()
