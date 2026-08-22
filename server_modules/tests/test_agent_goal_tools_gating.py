"""feat/agent-goals: no-DB unit coverage for the goal__* tool-gating story --
goal__* is a THIRD member of agent_turn_runtime_service._PROJECT_SCOPED_
CONNECTOR_IDS (project_task__*, document__* were the first two), granted by
PROJECT MEMBERSHIP rather than a connector binding. These tests prove the
same three properties test_phase4b_specialist_tool_threading.py already
proves for project_task__*, now for goal__*:

  1. `_specialist_tool_allowed` grants goal__* to any specialist with a
     project_id, with no connector binding required.
  2. `_specialist_tool_allowed` DENIES goal__* to a specialist with no
     project — this is the "tools are absent for an agent with no project"
     requirement from the build spec, and the cheapest possible proof of
     it (no DB, no fake pool, pure function).
  3. `_filter_registry_for_specialist` (the Tier-2 registry-narrowing path)
     agrees: kept when project_id is set, dropped when it is not.

Deliberately NOT importing test_phase4b_specialist_tool_threading.py's own
fixtures — same "duplicated rather than imported" reasoning that file's own
sibling test files already give for their own duplication.
"""

from __future__ import annotations

import unittest

from server_modules import agent_turn_runtime_service as sage


class GoalConnectorMembershipTests(unittest.TestCase):
    def test_goal_connector_id_is_in_project_scoped_set(self):
        self.assertIn("goal", sage._PROJECT_SCOPED_CONNECTOR_IDS)
        # The other two members must still be present -- this is an
        # addition, not a replacement.
        self.assertIn("project_task", sage._PROJECT_SCOPED_CONNECTOR_IDS)
        self.assertIn("document", sage._PROJECT_SCOPED_CONNECTOR_IDS)

    def test_specialist_tool_allowed_grants_goal_by_membership_not_connector(self):
        ts_member = {
            "core": set(), "tools": set(), "connectors": set(),  # "goal" never bound
            "raw_tool_toggles": {}, "capability_providers": frozenset(),
            # feat/agent-context-grant: "project_ids" is the agent's context
            # grant and is what the gate asks; "project_id" is now only the
            # write target. Production sets both from one resolver.
            "project_id": "proj-1", "project_ids": ["proj-1"],
        }
        for tool_name in ("goal__create", "goal__list", "goal__get", "goal__update"):
            with self.subTest(tool_name=tool_name):
                self.assertTrue(sage._specialist_tool_allowed(tool_name, ts_member))

    def test_specialist_tool_allowed_denies_goal_with_no_project(self):
        """The build's own required proof: 'the tools are absent for an
        agent with no project.'"""
        ts_no_project = {
            "core": set(), "tools": set(), "connectors": set(),
            "raw_tool_toggles": {}, "capability_providers": frozenset(),
            "project_id": "", "project_ids": [],
        }
        for tool_name in ("goal__create", "goal__list", "goal__get", "goal__update"):
            with self.subTest(tool_name=tool_name):
                self.assertFalse(sage._specialist_tool_allowed(tool_name, ts_no_project))

    def test_registry_filter_grants_goal_by_membership(self):
        class _Entry:
            def __init__(self, name, connector):
                self.tool_name = name
                self.connector_id = connector

        registry = [_Entry("goal__list", "goal"), _Entry("slack__post", "slack")]
        ts_member = {"core": set(), "tools": set(), "connectors": set(), "project_id": "proj-1", "project_ids": ["proj-1"]}
        kept = sage._filter_registry_for_specialist(registry, ts_member)
        self.assertEqual([e.tool_name for e in kept], ["goal__list"])

        ts_no_project = {"core": set(), "tools": set(), "connectors": set(), "project_id": "", "project_ids": []}
        kept_none = sage._filter_registry_for_specialist(registry, ts_no_project)
        self.assertEqual(kept_none, [])

    def test_direct_tool_bundle_carve_out_includes_goal_descriptors(self):
        """The Tier-1 carve-out loop in _direct_tool_bundle iterates every
        builtin descriptor whose connector_id is in _PROJECT_SCOPED_
        CONNECTOR_IDS -- confirm the four goal__* descriptors registered in
        skills_service.py are actually reachable through it, not just
        declared."""
        from server_modules import skills_service

        goal_tool_names = {
            d.tool_name for d in skills_service._builtin_tool_descriptors()
            if d.connector_id == "goal"
        }
        self.assertEqual(
            goal_tool_names,
            {"goal__create", "goal__list", "goal__get", "goal__update"},
        )
        # The `audience_safe` manifest flag these four used to assert is
        # gone (2026-08-21, authority_mandate_service). The replacement fact
        # is the opposite one and is worth asserting because it inverted:
        # goal__* is now one of the two families the machine-administration
        # floor reserves to the workspace owner, so a non-owner tier is
        # refused at the execution choke point no matter how the tool
        # reached the model.
        from server_modules import authority_mandate_service

        for descriptor in skills_service._builtin_tool_descriptors():
            if descriptor.connector_id == "goal":
                with self.subTest(tool_name=descriptor.tool_name):
                    self.assertTrue(
                        authority_mandate_service.is_owner_only_tool(
                            tool_name=descriptor.tool_name,
                            connector_id=descriptor.connector_id,
                            action_id=descriptor.action_id,
                        )
                    )
                    self.assertFalse(
                        authority_mandate_service.is_tool_call_allowed(
                            "audience",
                            tool_name=descriptor.tool_name,
                            connector_id=descriptor.connector_id,
                            action_id=descriptor.action_id,
                        )
                    )


class GoalBuiltinDirectToolRoutingTests(unittest.TestCase):
    """goal__* must route through the SYNC direct-tool path (needs
    session_ctx to resolve the calling agent's identity/project) rather than
    falling through to the OAuth-connector lookup, which has no "goal"
    connector registered — the exact failure mode project_task__*/
    document__* already had to guard against (skills_service.py's
    _BUILTIN_DIRECT_TOOL_IDS)."""

    def test_goal_is_a_builtin_direct_tool_id(self):
        from server_modules import skills_service

        self.assertIn("goal", skills_service._BUILTIN_DIRECT_TOOL_IDS)


class GoalSpecialistGuardTests(unittest.TestCase):
    """direct_tool_execution_service.py's specialist_guard check (the
    runtime-side second gate, independent of the prompt-time grant above)
    must also recognize goal__* as project-scoped."""

    def test_guard_project_scoped_connectors_includes_goal(self):
        import inspect

        from server_modules import direct_tool_execution_service

        source = inspect.getsource(direct_tool_execution_service)
        self.assertIn('_guard_project_scoped_connectors = ("project_task", "document", "goal")', source)


if __name__ == "__main__":
    unittest.main()
