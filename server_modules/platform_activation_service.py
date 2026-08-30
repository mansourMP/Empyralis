"""MAN-149 ("Activation, not acquisition -- signed-up customers exist and
are not using the platform") -- a cross-tenant snapshot of whether anyone
is actually using Empyralis, built from `SELECT count(*)`-shaped queries
against tables that already exist. No new metrics table, no background
job: this runs the query fresh on every request.

Two traps this module exists to not repeat (see CLAUDE.md):

1. `users`, `workspaces`, `workspace_agent_installs`, `projects`,
   `project_tasks`, `project_documents` and friends carry FORCE ROW LEVEL
   SECURITY. A query run over a connection with no `app.current_tenant_id`
   / `app.current_workspace_id` GUC set does not error -- it silently
   matches the empty set and returns a confident, wrong 0 for every table.
   The fix is `control_plane_repository.rls_fetchrow(..., bypass_rls=True)`,
   which runs `SET LOCAL app.rls_bypass = 'on'` as the first statement of
   the same transaction. This is a genuine cross-tenant READ (the whole
   point of this endpoint), so bypassing here is the correct shape -- see
   `agent_registry_repository.backfill_master_agent_isolation_defaults` for
   the sibling precedent and its own "cross-tenant enumeration read passes
   the bypass" test.

2. A zero from this endpoint must never be silently trusted as "the
   platform is empty" when it might really mean "the bypass didn't take".
   The query reads `current_setting('app.rls_bypass', true)` back as its
   own canary; `build_platform_activation_snapshot` raises
   `PlatformActivationScopeBroken` -- loudly, before returning anything --
   if that canary did not come back 'on'. A real empty platform and a
   broken scope must never share one signal.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from server_modules import control_plane_repository

# One statement, one round trip. Every column here is a plain COUNT (or a
# COUNT of a small derived set) against a table that already exists --
# no new table, no materialized view, no scheduled job. `rls_bypass_scope`
# is not platform data at all; it is the canary described above, read back
# from the same transaction that ran the counts so a future edit that
# drops `bypass_rls=True` is caught here instead of silently reporting 0
# users.
PLATFORM_ACTIVATION_SNAPSHOT_SQL = """
WITH membership_counts AS (
    SELECT tenant_id, workspace_id, COUNT(*) AS member_count
    FROM workspace_memberships
    WHERE status = 'active'
    GROUP BY tenant_id, workspace_id
),
agent_run_flags AS (
    SELECT
        wai.id,
        EXISTS (
            SELECT 1
            FROM agent_traces atr
            WHERE atr.tenant_id = wai.tenant_id
              AND atr.workspace_id = wai.workspace_id
              AND (
                  -- A specialist agent's traces name the install directly
                  -- (agent_turn._trace_root_agent_id: f"specialist:{install_id}").
                  atr.root_agent_id = ('specialist:' || wai.id)
                  OR (
                      -- The workspace's master agent (Sage/the Operator)
                      -- never carries its own install id on the trace --
                      -- it is always root_agent_id 'sage' or the
                      -- SAGE_MAIN_AGENT_ID constant.
                      COALESCE(ad.agent_kind, 'specialist') = 'master'
                      AND atr.root_agent_id IN ('sage', 'sage_main_agent')
                  )
              )
        ) AS has_run
    FROM workspace_agent_installs wai
    LEFT JOIN agent_definitions ad ON ad.id = wai.agent_definition_id
)
SELECT
    current_setting('app.rls_bypass', true) AS rls_bypass_scope,
    (SELECT COUNT(*) FROM users) AS total_users,
    (SELECT COUNT(*) FROM workspaces) AS total_workspaces,
    (SELECT COUNT(*) FROM workspace_agent_installs) AS total_agents,
    (SELECT COUNT(*) FROM projects) AS total_projects,
    (SELECT COUNT(*) FROM project_tasks) AS total_tasks,
    (SELECT COUNT(*) FROM project_documents) AS total_documents,
    (
        SELECT COUNT(DISTINCT actor_id)
        FROM (
            SELECT created_by AS actor_id
            FROM project_tasks
            WHERE created_by IS NOT NULL AND created_by <> ''
            UNION ALL
            SELECT created_by AS actor_id
            FROM project_documents
            WHERE created_by IS NOT NULL AND created_by <> ''
        ) activity_actors
    ) AS users_with_activity,
    (SELECT COUNT(*) FROM membership_counts WHERE member_count > 1)
        AS workspaces_with_multiple_members,
    (SELECT COUNT(*) FROM users WHERE created_at >= NOW() - INTERVAL '7 days')
        AS signups_last_7_days,
    (SELECT COUNT(*) FROM users WHERE created_at >= NOW() - INTERVAL '30 days')
        AS signups_last_30_days,
    (SELECT COUNT(*) FROM agent_run_flags WHERE has_run) AS agents_with_runs
"""


class PlatformActivationScopeBroken(RuntimeError):
    """Raised when the cross-tenant snapshot query did not actually run
    with its RLS bypass active -- the row came back (or didn't come back
    at all) without the canary reading 'on'. Every count in that row is
    untrustworthy: it may be a real empty platform, or it may be every
    FORCE-RLS table matching the empty set for an unscoped connection.
    Those are different facts (CLAUDE.md), so this raises rather than
    returning a payload that looks like the former but might be the
    latter."""


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _pct(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round((numerator / denominator) * 100, 1)


async def build_platform_activation_snapshot(pool: Any) -> Dict[str, Any]:
    """Run the one combined snapshot query and shape it into the payload
    the operator route returns. Callers must resolve `pool` themselves
    (typically `control_plane_repository.ensure_control_plane_schema()`)
    and treat `pool is None` as "control plane unavailable" BEFORE calling
    this -- that is a different fact from "queried fine, platform is
    empty", and belongs in the route as an explicit 503, never folded into
    a zeroed snapshot here.
    """
    row: Optional[Dict[str, Any]] = await control_plane_repository.rls_fetchrow(
        pool,
        PLATFORM_ACTIVATION_SNAPSHOT_SQL,
        bypass_rls=True,
    )
    if row is None:
        raise PlatformActivationScopeBroken(
            "platform activation query returned no row at all -- cannot "
            "distinguish an empty platform from a broken query."
        )

    payload = dict(row)
    scope = str(payload.get("rls_bypass_scope") or "").strip().lower()
    if scope != "on":
        raise PlatformActivationScopeBroken(
            "platform activation query ran with app.rls_bypass="
            f"{scope or 'unset'!r}, not 'on'. Every count in this row is "
            "scoped to whatever tenant (if any) happened to be set on the "
            "connection and must NOT be reported as the platform total -- "
            "this is the RLS-silently-returns-zero trap CLAUDE.md names."
        )

    total_users = _int(payload.get("total_users"))
    total_workspaces = _int(payload.get("total_workspaces"))
    total_agents = _int(payload.get("total_agents"))
    total_projects = _int(payload.get("total_projects"))
    total_tasks = _int(payload.get("total_tasks"))
    total_documents = _int(payload.get("total_documents"))
    users_with_activity = _int(payload.get("users_with_activity"))
    workspaces_with_multiple_members = _int(payload.get("workspaces_with_multiple_members"))
    signups_7d = _int(payload.get("signups_last_7_days"))
    signups_30d = _int(payload.get("signups_last_30_days"))
    agents_with_runs = _int(payload.get("agents_with_runs"))
    agents_never_run = max(total_agents - agents_with_runs, 0)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rls_bypass_verified": True,
        "totals": {
            "users": total_users,
            "workspaces": total_workspaces,
            "agents": total_agents,
            "projects": total_projects,
            "tasks": total_tasks,
            "documents": total_documents,
        },
        "activation": {
            "users_with_activity": users_with_activity,
            "users_with_activity_pct": _pct(users_with_activity, total_users),
            "workspaces_with_multiple_members": workspaces_with_multiple_members,
            "workspaces_with_multiple_members_pct": _pct(
                workspaces_with_multiple_members, total_workspaces
            ),
        },
        "signups": {
            "last_7_days": signups_7d,
            "last_30_days": signups_30d,
        },
        "agents_runtime": {
            "total": total_agents,
            "with_runs": agents_with_runs,
            "never_run": agents_never_run,
        },
    }
