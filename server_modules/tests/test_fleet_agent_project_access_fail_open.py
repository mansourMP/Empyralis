"""`routes_fleet._enforce_agent_project_access` failed OPEN on any
project-less agent, and it was live on production.

THE BUG
-------
    project_id = await tasks.agent_project_id(...)
    if not project_id:
        return                     # <- allows, unconditionally
    await auth_module.enforce_project_access(..., project_id, ...)

`workspace_agent_installs.project_id` is nullable BY SCHEMA
(`REFERENCES projects(id) ON DELETE SET NULL`). Confirmed against production
(read-only query, no writes): 40 `workspace_agent_installs` rows, 16 with no
`project_id`. 15 of those are `agent_kind='master'` (correct and expected —
the workspace-level Sage/Operator install is workspace-scoped by design and
must stay reachable by every member, MAN-201). **One is a real, enabled
specialist** — `ainstall_c8ec63b5f3474296`, label "Ftc", `status=draft`,
`enabled=true`, workspace `ws_38cbb418d35a` — reachable by every member of
that workspace through every fleet DETAIL route (activity, memory, channels,
connectors, tools, capabilities, usage) with no project membership at all.

This is the sibling of MAN-356's turn-path bug
(`agent_reachability_service.py`), same root cause (an empty `project_id`
read as "nothing to check" instead of "nobody but the owner"), different
seam: MAN-356 fixed turn EXECUTION; this fixes fleet DETAIL reads.

THE FIX
-------
`_enforce_agent_project_access` now shares
`agent_reachability_service.enforce_resolved_agent_access` — the exact same
grant rule the turn path already enforces — instead of computing a second,
independently-drifting opinion:

    agent_kind == "master"   ALWAYS allowed   (workspace-scoped; MAN-201)
    project_id present       the project's own ACL decides    (unchanged)
    project_id absent        workspace OWNER only, never an ordinary member

It keeps exactly ONE deliberate difference from `enforce_agent_reachable`:
when the agent's bundle cannot be resolved AT ALL (doesn't exist in this
workspace, or the lookup itself failed), this still does NOT raise — there
is nothing to leak for an agent that isn't there, and the route's own
service call right after this degrades safely on its own (a normal
not-found/empty result, never a 403/404 that would turn this seam into an
enumeration oracle). `enforce_agent_reachable` is the one that must fail
closed on that case, because it is the ONLY gate on the turn-execution path.

RED-BEFORE-GREEN
-----------------
`ProjectLessSpecialistFailOpenRegressionTests.
test_a_project_less_specialist_is_now_refused_for_an_ordinary_member` patches
BOTH the pre-fix data source (`project_tasks_service.agent_project_id`, which
the OLD code read) and the post-fix one
(`agent_registry_repository.get_workspace_agent_install_bundle`, which the
NEW code reads) to describe the identical real-world state — a project-less,
enabled specialist — so the SAME test body proves the bug on
`git show HEAD:server_modules/routes_fleet.py` (no exception raised — silent
allow) and proves the fix on the working tree (raises 404). Run both ways
before trusting this file; see the dispatch's own verification steps.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from server_modules import routes_fleet

WORKSPACE_ID = "ws-1"
TENANT_ID = "tenant-1"
# Mirrors the real production row this fix closes: a specialist, not the
# workspace master, sitting with project_id NULL.
AGENT_ID = "ainstall_c8ec63b5f3474296"

MEMBER = {
    "user_id": "u-member",
    "email": "member@example.com",
    "auth_type": "bearer",
    "workspace_access": {
        WORKSPACE_ID: {"workspace_id": WORKSPACE_ID, "tenant_id": TENANT_ID, "role": "member", "tenant_role": "member"}
    },
}

OWNER = {
    "user_id": "u-owner",
    "email": "owner@example.com",
    "auth_type": "bearer",
    "workspace_access": {
        WORKSPACE_ID: {"workspace_id": WORKSPACE_ID, "tenant_id": TENANT_ID, "role": "owner", "tenant_role": "owner"}
    },
}


def _run(current_user, *, agent_kind: str, project_id):
    """Drives the REAL `_enforce_agent_project_access`, patching both the
    pre-fix lookup (`project_tasks_service.agent_project_id`, a bare
    `Optional[str]`) and the post-fix one
    (`agent_registry_repository.get_workspace_agent_install_bundle`, a full
    bundle dict) to describe the SAME agent state — whichever one the code
    under test actually reads, it sees a consistent picture, which is what
    makes this one test body valid both before and after the fix."""
    old_style_lookup = AsyncMock(return_value=project_id)
    new_style_bundle = None
    if agent_kind or project_id is not None:
        new_style_bundle = {"id": AGENT_ID, "agent_kind": agent_kind, "project_id": project_id}
    new_style_lookup = AsyncMock(return_value=new_style_bundle)
    with patch(
        "server_modules.project_tasks_service.agent_project_id", old_style_lookup,
    ), patch(
        "server_modules.agent_registry_repository.get_workspace_agent_install_bundle", new_style_lookup,
    ):
        asyncio.run(
            routes_fleet._enforce_agent_project_access(
                current_user, WORKSPACE_ID, TENANT_ID, AGENT_ID, minimum_role="viewer",
            )
        )


class ProjectLessSpecialistFailOpenRegressionTests(unittest.TestCase):
    """THE regression this file exists for."""

    def test_a_project_less_specialist_is_now_refused_for_an_ordinary_member(self):
        """Red on `git show HEAD:server_modules/routes_fleet.py` (no
        exception — the live bug), green on the working tree."""
        with self.assertRaises(HTTPException) as ctx:
            _run(MEMBER, agent_kind="specialist", project_id=None)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_a_project_less_specialist_stays_reachable_by_the_workspace_owner(self):
        """Fail-closed must not mean 'nobody' — project-less agents exist in
        the wild (15 of them, all masters, plus the 1 specialist this fix is
        about) and their owner must keep reaching them."""
        _run(OWNER, agent_kind="specialist", project_id=None)  # must not raise

    def test_the_workspace_scoped_master_stays_reachable_by_every_member(self):
        """MAN-201 must not regress. The naive fix (any project-less agent
        needs a project) would break this — Sage/the Operator is
        project-less by design and every member must keep their Ask AI
        console. Passes on both old and new code (the old code's fail-open
        was never keyed on agent_kind, so it happened to also allow this —
        the new code allows it deliberately, by agent_kind, instead)."""
        _run(MEMBER, agent_kind="master", project_id=None)  # must not raise

    def test_a_project_having_agent_is_still_gated_by_its_own_project_acl_unchanged(self):
        """The one path this fix must leave untouched: an agent WITH a
        project is still decided by that project's own ACL, exactly as
        before."""
        enforce = AsyncMock(side_effect=HTTPException(status_code=404, detail="Project not found."))
        with patch(
            "server_modules.project_tasks_service.agent_project_id",
            AsyncMock(return_value="project-b"),
        ), patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            AsyncMock(return_value={"id": AGENT_ID, "agent_kind": "specialist", "project_id": "project-b"}),
        ), patch("server_modules.auth.enforce_project_access", enforce):
            with self.assertRaises(HTTPException):
                asyncio.run(
                    routes_fleet._enforce_agent_project_access(
                        MEMBER, WORKSPACE_ID, TENANT_ID, AGENT_ID, minimum_role="viewer",
                    )
                )
        self.assertEqual(enforce.await_count, 1)

    def test_an_unresolvable_agent_still_fails_open_here_by_design(self):
        """The ONE deliberate difference from `enforce_agent_reachable`: an
        agent that cannot be resolved at all (doesn't exist here, or the
        lookup failed) must NOT raise from this helper — there is nothing to
        leak for an agent that isn't there, and the route's own service call
        degrades safely on its own. Only the turn-execution gate
        (`enforce_agent_reachable`) must fail closed on this case."""
        _run(MEMBER, agent_kind="", project_id=None)  # both lookups resolve to "nothing" — must not raise


if __name__ == "__main__":
    unittest.main()
