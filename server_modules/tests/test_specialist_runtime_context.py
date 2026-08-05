"""Tests for specialist_runtime_context.resolve_specialist_runtime_context.

Focus: the U3-K project-level default-gateway fallback — an agent's own
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

import unittest
from unittest.mock import AsyncMock, patch

from server_modules import specialist_runtime_context as ctx

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


def _patched(bundle, project_result=None, project_side_effect=None):
    """Context manager stack shared by every test below — patches the three
    repository calls resolve_specialist_runtime_context makes, in the exact
    module paths it imports them from (server_modules.agent_registry_
    repository / server_modules.projects_repository), so patching works
    whether the call resolves the module at import time or at call time (this
    module does both, locally, inside the function body)."""
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
        p1, p2, p3 = _patched(bundle, project_result={"default_gateway_id": "gw-project-default"})
        with p1, p2, p3 as mock_get_project:
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
        p1, p2, p3 = _patched(bundle, project_result={"default_gateway_id": "gw-project-default"})
        with p1, p2, p3:
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
        p1, p2, p3 = _patched(bundle, project_result={"default_gateway_id": "gw-project-default"})
        with p1, p2, p3:
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
        p1, p2, p3 = _patched(bundle, project_result={"default_gateway_id": ""})
        with p1, p2, p3:
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
        p1, p2, p3 = _patched(bundle, project_result={"default_gateway_id": "gw-project-default"})
        with p1, p2, p3 as mock_get_project:
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
        p1, p2, p3 = _patched(bundle, project_result=None)
        with p1, p2, p3:
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
        p1, p2, p3 = _patched(bundle, project_side_effect=RuntimeError("db down"))
        with p1, p2, p3:
            result = await ctx.resolve_specialist_runtime_context(
                workspace_id="ws-1", tenant_id="t1", active_agent_install_id="install-1",
            )
        self.assertIsNotNone(result)
        self.assertEqual(result.gateway_binding, "")
        self.assertEqual(result.preferred_gateway_id, "")


if __name__ == "__main__":
    unittest.main()
