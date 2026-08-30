"""Empyralis operator console -- the backend behind ops.empyralis.ai.

The founder currently has to ask someone to SSH into production Postgres to
learn anything about the platform (105 users, 105 workspaces, 68 agents...).
This module is the full replacement: platform overview, a sortable/
filterable account list, per-account detail, an activation funnel,
retention, provider/tool failures, and spend -- all built from tables that
already exist, no new metrics table, no background job.

Two traps this module exists to not repeat (see CLAUDE.md and
platform_activation_service.py, the narrow first version this extends):

1. RLS-bypass canary. `users`, `workspaces`, `workspace_memberships`,
   `workspace_agent_installs`, `projects`, `project_tasks`,
   `project_documents`, `project_document_revisions` and `agent_computers`
   all carry FORCE ROW LEVEL SECURITY. A query run over a connection with no
   `app.rls_bypass` GUC set does not error -- it silently matches the empty
   set and returns a confident, wrong 0 for every table. Every function in
   this module runs its SQL through `control_plane_repository.rls_fetchrow`
   with `bypass_rls=True`, and every query reads
   `current_setting('app.rls_bypass', true)` back as its own canary in the
   SAME statement that returns the data -- not a separate check against a
   different connection, which would prove nothing about the query that
   actually ran. `_require_bypass_canary` raises `OperatorConsoleScopeBroken`
   loudly, before returning anything, if that canary did not come back 'on'.
   A real empty account and a broken scope must never share one signal.

   Some tables this module reads (agent_action_events, credit_ledger_events,
   activity_ledger_events, run_archive, auth_sessions) do NOT currently carry
   a Postgres RLS policy -- see `preflight._RLS_COVERAGE_EXCEPTIONS`. They
   are still read through the same bypassed connection and the same canary
   check for one reason: consistency. A single code path that always proves
   its own scope is the only one a future author cannot accidentally narrow
   by copy-pasting a "simpler" unscoped query, and if RLS is ever turned on
   for one of those tables later (several are already flagged as audit
   candidates), this module keeps working without anyone having to remember
   to come back and add bypass_rls=True to it.

2. No customer content. The founder decided operators see metadata and
   behaviour -- never content. This module never selects, returns, or logs:
   `project_documents.body`, `project_document_revisions.body`,
   `project_document_revisions.diff`, `project_tasks.description`,
   `agent_private_memory_notes.content`, `agent_trace_events.payload`,
   `agent_channel_events.text`, or `activity_ledger_events`'s
   `payload`/`summary`/`artifacts`. Document and task *titles*, sizes,
   counts, revision counts, timestamps, and last-editor identity are fine --
   the same metadata a customer of Dropbox or Google already sees by
   default. `agent_action_events.input_summary`/`output_summary` are, on a
   strict reading of the founder's list, not named -- but they are bounded
   copies of real tool-call arguments and can hold up to 1000 characters of
   whatever a customer or their agent actually said. This module treats them
   as content and never selects them either; "what broke" is answered from
   `action_domain`/`action_name`/`tool_kind`/`status`/`error_code`, which is
   pure behaviour. `server_modules/tests/test_operator_console_no_content_
   guard.py` is a regex over every `_SQL`-suffixed constant in this module,
   with a canary proving the scan reaches real SQL, so a future edit that
   adds one of those columns back fails the test, not a code review.

Hardware note: `gateway_registrations` (personal machine pairing/heartbeat)
has zero live Postgres presence -- it lives in per-box SQLite. This module
never claims to report on paired personal hardware. Where it surfaces
hardware at all, it is `agent_computers` (Empyralis-provisioned VPS boxes,
which genuinely live in Postgres) and it is labelled as VPS-only.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from server_modules import control_plane_repository

_decode_json_array = control_plane_repository._decode_json_array  # noqa: SLF001 -- reuse, see projects_repository.py precedent
_decode_json_object = control_plane_repository._decode_json_object  # noqa: SLF001 -- same reuse, for jsonb_object_agg columns


class OperatorConsoleScopeBroken(RuntimeError):
    """Raised when a cross-tenant operator-console query did not actually
    run with its RLS bypass active. Every value in that result is
    untrustworthy: it may be a genuinely empty platform, or it may be every
    FORCE-RLS table matching the empty set for an unscoped connection. Those
    are different facts (CLAUDE.md), so this raises rather than returning a
    payload that looks like the former but might be the latter."""


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return round(float(value or 0.0), 6)
    except (TypeError, ValueError):
        return 0.0


def _pct(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round((numerator / denominator) * 100, 1)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_bypass_canary(row: Optional[Dict[str, Any]], *, operation: str) -> Dict[str, Any]:
    """Shared canary check every function in this module runs its row
    through before touching any other field. See module docstring point 1."""
    if row is None:
        raise OperatorConsoleScopeBroken(
            f"operator console query ({operation}) returned no row at all -- "
            "cannot distinguish an empty result from a broken query."
        )
    payload = dict(row)
    scope = str(payload.get("rls_bypass_scope") or "").strip().lower()
    if scope != "on":
        raise OperatorConsoleScopeBroken(
            f"operator console query ({operation}) ran with app.rls_bypass="
            f"{scope or 'unset'!r}, not 'on'. Every value in this row is scoped "
            "to whatever tenant (if any) happened to be set on the connection "
            "and must NOT be reported as a platform-wide or account-wide "
            "number -- this is the RLS-silently-returns-zero trap CLAUDE.md "
            "names."
        )
    return payload


def _rows(payload: Dict[str, Any], key: str) -> List[Dict[str, Any]]:
    return [item for item in _decode_json_array(payload.get(key)) if isinstance(item, dict)]


# ---------------------------------------------------------------------------
# 1. Platform overview
# ---------------------------------------------------------------------------

# The `agent_run_flags` CTE is copied verbatim from platform_activation_
# service.PLATFORM_ACTIVATION_SNAPSHOT_SQL -- see that module's own comment
# for why root_agent_id has to be matched this way (a specialist's traces
# name the install directly; the workspace's master agent never carries its
# own install id on the trace).
OPERATOR_OVERVIEW_SQL = """
WITH agent_run_flags AS (
    SELECT
        wai.id,
        EXISTS (
            SELECT 1
            FROM agent_traces atr
            WHERE atr.tenant_id = wai.tenant_id
              AND atr.workspace_id = wai.workspace_id
              AND (
                  atr.root_agent_id = ('specialist:' || wai.id)
                  OR (
                      COALESCE(ad.agent_kind, 'specialist') = 'master'
                      AND atr.root_agent_id IN ('sage', 'sage_main_agent')
                  )
              )
        ) AS has_run
    FROM workspace_agent_installs wai
    LEFT JOIN agent_definitions ad ON ad.id = wai.agent_definition_id
),
membership_counts AS (
    SELECT tenant_id, workspace_id, COUNT(*) AS member_count
    FROM workspace_memberships
    WHERE status = 'active'
    GROUP BY tenant_id, workspace_id
),
computer_status_counts AS (
    SELECT status, COUNT(*) AS n
    FROM agent_computers
    GROUP BY status
)
SELECT
    current_setting('app.rls_bypass', true) AS rls_bypass_scope,
    (SELECT COUNT(*) FROM users) AS total_users,
    (SELECT COUNT(*) FROM workspaces) AS total_workspaces,
    (SELECT COUNT(*) FROM workspace_agent_installs) AS total_agents,
    (SELECT COUNT(*) FROM projects) AS total_projects,
    (SELECT COUNT(*) FROM project_tasks) AS total_tasks,
    (SELECT COUNT(*) FROM project_documents) AS total_documents,
    (SELECT COUNT(*) FROM agent_run_flags WHERE has_run) AS agents_with_runs,
    (SELECT COUNT(*) FROM membership_counts WHERE member_count > 1)
        AS workspaces_with_second_member,
    (SELECT COUNT(*) FROM users WHERE created_at >= NOW() - INTERVAL '7 days')
        AS signups_last_7_days,
    (SELECT COUNT(*) FROM users WHERE created_at >= NOW() - INTERVAL '30 days')
        AS signups_last_30_days,
    (SELECT COUNT(*) FROM users WHERE created_at >= NOW() - INTERVAL '90 days')
        AS signups_last_90_days,
    (SELECT COALESCE(SUM(platform_cost_usd), 0) FROM credit_ledger_events)
        AS total_platform_cost_usd,
    (SELECT COALESCE(SUM(credits_debited), 0) FROM credit_ledger_events)
        AS total_credits_debited,
    (SELECT COUNT(*) FROM agent_computers) AS total_agent_computers,
    COALESCE(
        (SELECT jsonb_object_agg(status, n) FROM computer_status_counts),
        '{}'::jsonb
    ) AS agent_computers_by_status
"""


async def build_overview(pool: Any) -> Dict[str, Any]:
    row = await control_plane_repository.rls_fetchrow(pool, OPERATOR_OVERVIEW_SQL, bypass_rls=True)
    payload = _require_bypass_canary(row, operation="overview")

    total_users = _int(payload.get("total_users"))
    total_workspaces = _int(payload.get("total_workspaces"))
    total_agents = _int(payload.get("total_agents"))
    agents_with_runs = _int(payload.get("agents_with_runs"))

    return {
        "generated_at": _now_iso(),
        "rls_bypass_verified": True,
        "totals": {
            "users": total_users,
            "workspaces": total_workspaces,
            "agents": total_agents,
            "projects": _int(payload.get("total_projects")),
            "tasks": _int(payload.get("total_tasks")),
            "documents": _int(payload.get("total_documents")),
        },
        "signups": {
            "last_7_days": _int(payload.get("signups_last_7_days")),
            "last_30_days": _int(payload.get("signups_last_30_days")),
            "last_90_days": _int(payload.get("signups_last_90_days")),
        },
        "agents_runtime": {
            "total": total_agents,
            "with_runs": agents_with_runs,
            "never_run": max(total_agents - agents_with_runs, 0),
        },
        "workspaces_with_second_member": _int(payload.get("workspaces_with_second_member")),
        "workspaces_with_second_member_pct": _pct(
            _int(payload.get("workspaces_with_second_member")), total_workspaces
        ),
        "spend": {
            "total_platform_cost_usd": _float(payload.get("total_platform_cost_usd")),
            "total_credits_debited": _float(payload.get("total_credits_debited")),
        },
        "agent_computers": {
            "total": _int(payload.get("total_agent_computers")),
            "by_status": {
                str(k): _int(v) for k, v in _decode_json_object(payload.get("agent_computers_by_status")).items()
            },
            "source": "agent_computers (Postgres, Empyralis-provisioned VPS only)",
            "excludes": "paired personal hardware -- that state is per-box SQLite, not centrally visible",
        },
    }


# ---------------------------------------------------------------------------
# 2. Account list -- the primary view. Sortable/filterable server-side.
# ---------------------------------------------------------------------------

# Whitelist only: the ONLY way a caller-controlled value ever reaches this
# SQL as anything other than a `$N` bound parameter is through this dict's
# VALUES, never through a caller-supplied column name. list_accounts()
# raises ValueError before building any SQL if sort_by/sort_dir fall outside
# it, so there is no path from an HTTP query string to a raw identifier in
# the query text.
ACCOUNT_SORT_COLUMNS: Dict[str, str] = {
    "name": "name",
    "created_at": "created_at",
    "last_active_at": "last_seen_epoch",
    "member_count": "member_count",
    "project_count": "project_count",
    "task_count": "task_count",
    "document_count": "document_count",
    "agent_count": "agent_count",
    "agents_with_runs": "agents_with_runs",
    "total_platform_cost_usd": "total_platform_cost_usd",
    "total_credits_debited": "total_credits_debited",
}

# `__WHERE__`/`__ORDER__`/`__LIMIT__`/`__OFFSET__` are plain string markers
# (not Python str.format() fields -- this template contains literal '{}'
# tokens in JSONB casts, which str.format() would misparse as positional
# fields). list_accounts() fills them in with str.replace(), and every value
# it substitutes is either a `$N` placeholder or a column name drawn from
# ACCOUNT_SORT_COLUMNS above -- never raw caller text. `__WHERE__` and
# `__ORDER__` each appear twice on purpose: once to count every matching
# row (for pagination) and once to select/sort the one page actually
# returned, and both must apply the identical filter/order.
OPERATOR_ACCOUNTS_SQL_TEMPLATE = """
WITH membership_counts AS (
    SELECT tenant_id, workspace_id, COUNT(*) AS member_count
    FROM workspace_memberships
    WHERE status = 'active'
    GROUP BY tenant_id, workspace_id
),
last_active AS (
    SELECT wm.tenant_id, wm.workspace_id, MAX(s.last_seen_at) AS last_seen_epoch
    FROM workspace_memberships wm
    JOIN auth_sessions s ON s.user_id = wm.user_id
    WHERE wm.status = 'active'
    GROUP BY wm.tenant_id, wm.workspace_id
),
project_counts AS (
    SELECT tenant_id, workspace_id, COUNT(*) AS project_count
    FROM projects
    GROUP BY tenant_id, workspace_id
),
task_counts AS (
    SELECT tenant_id, workspace_id, COUNT(*) AS task_count
    FROM project_tasks
    GROUP BY tenant_id, workspace_id
),
document_counts AS (
    SELECT tenant_id, workspace_id, COUNT(*) AS document_count
    FROM project_documents
    GROUP BY tenant_id, workspace_id
),
agent_counts AS (
    SELECT tenant_id, workspace_id, COUNT(*) AS agent_count
    FROM workspace_agent_installs
    GROUP BY tenant_id, workspace_id
),
agent_run_flags AS (
    SELECT
        wai.tenant_id,
        wai.workspace_id,
        EXISTS (
            SELECT 1
            FROM agent_traces atr
            WHERE atr.tenant_id = wai.tenant_id
              AND atr.workspace_id = wai.workspace_id
              AND (
                  atr.root_agent_id = ('specialist:' || wai.id)
                  OR (
                      COALESCE(ad.agent_kind, 'specialist') = 'master'
                      AND atr.root_agent_id IN ('sage', 'sage_main_agent')
                  )
              )
        ) AS has_run
    FROM workspace_agent_installs wai
    LEFT JOIN agent_definitions ad ON ad.id = wai.agent_definition_id
),
agents_with_runs_counts AS (
    SELECT tenant_id, workspace_id, COUNT(*) FILTER (WHERE has_run) AS agents_with_runs
    FROM agent_run_flags
    GROUP BY tenant_id, workspace_id
),
spend AS (
    SELECT tenant_id, workspace_id,
        COALESCE(SUM(platform_cost_usd), 0) AS total_platform_cost_usd,
        COALESCE(SUM(credits_debited), 0) AS total_credits_debited
    FROM credit_ledger_events
    GROUP BY tenant_id, workspace_id
),
accounts_base AS (
    SELECT
        w.workspace_id,
        w.tenant_id,
        w.name,
        w.created_at,
        w.status AS workspace_status,
        ou.id AS owner_user_id,
        ou.email AS owner_email,
        ou.display_name AS owner_display_name,
        COALESCE(mc.member_count, 0) AS member_count,
        la.last_seen_epoch,
        COALESCE(pc.project_count, 0) AS project_count,
        COALESCE(tc.task_count, 0) AS task_count,
        COALESCE(dc.document_count, 0) AS document_count,
        COALESCE(ac.agent_count, 0) AS agent_count,
        COALESCE(rc.agents_with_runs, 0) AS agents_with_runs,
        COALESCE(sp.total_platform_cost_usd, 0) AS total_platform_cost_usd,
        COALESCE(sp.total_credits_debited, 0) AS total_credits_debited
    FROM workspaces w
    LEFT JOIN users ou ON ou.id = w.created_by_user_id
    LEFT JOIN membership_counts mc ON mc.tenant_id = w.tenant_id AND mc.workspace_id = w.workspace_id
    LEFT JOIN last_active la ON la.tenant_id = w.tenant_id AND la.workspace_id = w.workspace_id
    LEFT JOIN project_counts pc ON pc.tenant_id = w.tenant_id AND pc.workspace_id = w.workspace_id
    LEFT JOIN task_counts tc ON tc.tenant_id = w.tenant_id AND tc.workspace_id = w.workspace_id
    LEFT JOIN document_counts dc ON dc.tenant_id = w.tenant_id AND dc.workspace_id = w.workspace_id
    LEFT JOIN agent_counts ac ON ac.tenant_id = w.tenant_id AND ac.workspace_id = w.workspace_id
    LEFT JOIN agents_with_runs_counts rc ON rc.tenant_id = w.tenant_id AND rc.workspace_id = w.workspace_id
    LEFT JOIN spend sp ON sp.tenant_id = w.tenant_id AND sp.workspace_id = w.workspace_id
)
SELECT
    current_setting('app.rls_bypass', true) AS rls_bypass_scope,
    (SELECT COUNT(*) FROM accounts_base WHERE __WHERE__) AS total_matching,
    COALESCE(
        (
            SELECT jsonb_agg(
                jsonb_build_object(
                    'workspace_id', filtered.workspace_id,
                    'tenant_id', filtered.tenant_id,
                    'name', filtered.name,
                    'created_at', filtered.created_at,
                    'workspace_status', filtered.workspace_status,
                    'owner', jsonb_build_object(
                        'user_id', filtered.owner_user_id,
                        'email', filtered.owner_email,
                        'display_name', filtered.owner_display_name
                    ),
                    'member_count', filtered.member_count,
                    'last_active_at', CASE WHEN filtered.last_seen_epoch IS NULL THEN NULL ELSE to_timestamp(filtered.last_seen_epoch) END,
                    'project_count', filtered.project_count,
                    'task_count', filtered.task_count,
                    'document_count', filtered.document_count,
                    'agent_count', filtered.agent_count,
                    'agents_with_runs', filtered.agents_with_runs,
                    'agents_never_run', GREATEST(filtered.agent_count - filtered.agents_with_runs, 0),
                    'total_platform_cost_usd', filtered.total_platform_cost_usd,
                    'total_credits_debited', filtered.total_credits_debited
                )
                ORDER BY __ORDER__
            )
            FROM (
                SELECT * FROM accounts_base WHERE __WHERE__
                ORDER BY __ORDER__
                LIMIT __LIMIT__ OFFSET __OFFSET__
            ) filtered
        ),
        '[]'::jsonb
    ) AS accounts
"""


def _account_sort_expression(sort_by: str, sort_dir: str) -> str:
    if sort_by not in ACCOUNT_SORT_COLUMNS:
        raise ValueError(
            f"sort_by must be one of {sorted(ACCOUNT_SORT_COLUMNS)}, got {sort_by!r}."
        )
    direction = str(sort_dir or "").strip().lower()
    if direction not in {"asc", "desc"}:
        raise ValueError(f"sort_dir must be 'asc' or 'desc', got {sort_dir!r}.")
    return f"{ACCOUNT_SORT_COLUMNS[sort_by]} {direction.upper()} NULLS LAST"


async def list_accounts(
    pool: Any,
    *,
    search: Optional[str] = None,
    min_members: Optional[int] = None,
    has_agents: Optional[bool] = None,
    active_within_days: Optional[int] = None,
    sort_by: str = "name",
    sort_dir: str = "asc",
    limit: int = 200,
    offset: int = 0,
) -> Dict[str, Any]:
    """The primary operator view: every workspace with member/project/task/
    document/agent counts, last-active (from auth_sessions.last_seen_at via
    workspace_memberships -- never from users.tenant_id/workspace_id, which
    CLAUDE.md names as the wrong, never-updated column for this), agents
    that have actually run vs never run, and total spend. Filters and sort
    all run server-side in one query; limit is capped well above the
    platform's current 105 workspaces."""
    order_sql = _account_sort_expression(sort_by, sort_dir)

    where_clauses: List[str] = []
    args: List[Any] = []

    def _bind(value: Any) -> str:
        args.append(value)
        return f"${len(args)}"

    search_token = str(search or "").strip()
    if search_token:
        where_clauses.append(f"name ILIKE {_bind('%' + search_token + '%')}")
    if min_members is not None:
        where_clauses.append(f"member_count >= {_bind(int(min_members))}")
    if has_agents is not None:
        where_clauses.append(f"(agent_count > 0) = {_bind(bool(has_agents))}")
    if active_within_days is not None:
        days_float = max(float(active_within_days), 0.0)
        where_clauses.append(
            "last_seen_epoch IS NOT NULL AND last_seen_epoch >= "
            f"(extract(epoch from now()) - {_bind(days_float * 86400.0)})"
        )
    where_sql = " AND ".join(where_clauses) if where_clauses else "TRUE"

    bounded_limit = max(1, min(int(limit), 1000))
    bounded_offset = max(0, int(offset))
    limit_ph = _bind(bounded_limit)
    offset_ph = _bind(bounded_offset)

    sql = (
        OPERATOR_ACCOUNTS_SQL_TEMPLATE
        .replace("__WHERE__", where_sql)
        .replace("__ORDER__", order_sql)
        .replace("__LIMIT__", limit_ph)
        .replace("__OFFSET__", offset_ph)
    )

    row = await control_plane_repository.rls_fetchrow(pool, sql, *args, bypass_rls=True)
    payload = _require_bypass_canary(row, operation="list_accounts")

    accounts = _rows(payload, "accounts")
    return {
        "generated_at": _now_iso(),
        "rls_bypass_verified": True,
        "total_matching": _int(payload.get("total_matching")),
        "returned_count": len(accounts),
        "limit": bounded_limit,
        "offset": bounded_offset,
        "sort_by": sort_by,
        "sort_dir": sort_dir,
        "accounts": accounts,
    }


# ---------------------------------------------------------------------------
# 3. Account detail -- one workspace, everything an operator needs about it
# in one round trip.
# ---------------------------------------------------------------------------

# `target_workspace` resolves the account by its public `workspace_id` alone
# (globally UNIQUE on the `workspaces` table -- see its CREATE TABLE) rather
# than by `users.tenant_id`/`users.workspace_id`, which CLAUDE.md names as
# the wrong column for this: it is the user's HOME workspace, written once
# at signup and never updated. Every other CTE below re-derives tenant_id
# from `target_workspace`, never from a caller-supplied value, so an
# operator cannot point tenant_id at one workspace and workspace_id at
# another.
OPERATOR_ACCOUNT_DETAIL_SQL = """
WITH target_workspace AS (
    SELECT id, tenant_id, workspace_id, name, created_at, status, workspace_type, created_by_user_id
    FROM workspaces
    WHERE workspace_id = $1
    LIMIT 1
),
owner_info AS (
    SELECT u.id AS owner_user_id, u.email AS owner_email, u.display_name AS owner_display_name
    FROM users u
    WHERE u.id = (SELECT created_by_user_id FROM target_workspace)
),
members_with_activity AS (
    SELECT
        u.id AS user_id,
        u.email,
        u.display_name,
        wm.role,
        wm.status AS membership_status,
        wm.created_at AS joined_at,
        (SELECT MAX(s.last_seen_at) FROM auth_sessions s WHERE s.user_id = u.id) AS last_seen_epoch
    FROM workspace_memberships wm
    JOIN users u ON u.id = wm.user_id
    WHERE wm.tenant_id = (SELECT tenant_id FROM target_workspace)
      AND wm.workspace_id = (SELECT workspace_id FROM target_workspace)
),
agents_with_last_trace AS (
    SELECT
        wai.id,
        COALESCE(NULLIF(wai.label, ''), ad.name, 'Unnamed agent') AS display_name,
        ad.name AS definition_name,
        COALESCE(ad.agent_kind, 'specialist') AS agent_kind,
        wai.status,
        wai.enabled,
        wai.created_at,
        lt.started_at AS last_run_started_at,
        lt.finished_at AS last_run_finished_at,
        lt.outcome AS last_run_outcome
    FROM workspace_agent_installs wai
    LEFT JOIN agent_definitions ad ON ad.id = wai.agent_definition_id
    LEFT JOIN LATERAL (
        SELECT atr.started_at, atr.finished_at, atr.outcome
        FROM agent_traces atr
        WHERE atr.tenant_id = wai.tenant_id
          AND atr.workspace_id = wai.workspace_id
          AND (
              atr.root_agent_id = ('specialist:' || wai.id)
              OR (
                  COALESCE(ad.agent_kind, 'specialist') = 'master'
                  AND atr.root_agent_id IN ('sage', 'sage_main_agent')
              )
          )
        ORDER BY atr.started_at DESC
        LIMIT 1
    ) lt ON TRUE
    WHERE wai.tenant_id = (SELECT tenant_id FROM target_workspace)
      AND wai.workspace_id = (SELECT workspace_id FROM target_workspace)
),
projects_with_counts AS (
    SELECT
        p.id, p.name, p.slug, p.archived, p.created_at,
        (SELECT COUNT(*) FROM project_tasks pt WHERE pt.project_id = p.id) AS task_count,
        (SELECT COUNT(*) FROM project_documents pd WHERE pd.project_id = p.id) AS document_count
    FROM projects p
    WHERE p.tenant_id = (SELECT tenant_id FROM target_workspace)
      AND p.workspace_id = (SELECT workspace_id FROM target_workspace)
),
recent_activity AS (
    SELECT id, actor_type, actor_id, install_id, event_class, action, status, title, created_at
    FROM activity_ledger_events
    WHERE tenant_id = (SELECT tenant_id FROM target_workspace)
      AND workspace_id = (SELECT workspace_id FROM target_workspace)
    ORDER BY created_at DESC
    LIMIT 50
),
spend_breakdown AS (
    SELECT
        provider, model, credit_type,
        COUNT(*) AS event_count,
        COALESCE(SUM(platform_cost_usd), 0) AS total_platform_cost_usd,
        COALESCE(SUM(credits_debited), 0) AS total_credits_debited
    FROM credit_ledger_events
    WHERE tenant_id = (SELECT tenant_id FROM target_workspace)
      AND workspace_id = (SELECT workspace_id FROM target_workspace)
    GROUP BY provider, model, credit_type
),
recent_failures AS (
    SELECT id, agent_id, action_domain, action_name, tool_kind, tool_id, status, error_code, run_id, trace_id, created_at
    FROM agent_action_events
    WHERE tenant_id = (SELECT tenant_id FROM target_workspace)
      AND workspace_id = (SELECT workspace_id FROM target_workspace)
      AND status IN ('failed', 'blocked', 'denied')
    ORDER BY created_at DESC
    LIMIT 50
),
runs_by_outcome AS (
    SELECT final_state, COUNT(*) AS n
    FROM run_archive
    WHERE tenant_id = (SELECT tenant_id FROM target_workspace)
      AND workspace_id = (SELECT workspace_id FROM target_workspace)
    GROUP BY final_state
)
SELECT
    current_setting('app.rls_bypass', true) AS rls_bypass_scope,
    EXISTS(SELECT 1 FROM target_workspace) AS workspace_found,
    (SELECT tenant_id FROM target_workspace) AS tenant_id,
    (SELECT workspace_id FROM target_workspace) AS workspace_id,
    (SELECT name FROM target_workspace) AS name,
    (SELECT created_at FROM target_workspace) AS created_at,
    (SELECT status FROM target_workspace) AS workspace_status,
    (SELECT workspace_type FROM target_workspace) AS workspace_type,
    (SELECT jsonb_build_object('user_id', owner_user_id, 'email', owner_email, 'display_name', owner_display_name)
        FROM owner_info) AS owner,
    COALESCE((SELECT jsonb_agg(jsonb_build_object(
        'user_id', user_id, 'email', email, 'display_name', display_name, 'role', role,
        'membership_status', membership_status, 'joined_at', joined_at,
        'last_active_at', CASE WHEN last_seen_epoch IS NULL THEN NULL ELSE to_timestamp(last_seen_epoch) END
    ) ORDER BY joined_at ASC) FROM members_with_activity), '[]'::jsonb) AS members,
    COALESCE((SELECT jsonb_agg(jsonb_build_object(
        'id', id, 'display_name', display_name, 'definition_name', definition_name, 'agent_kind', agent_kind,
        'status', status, 'enabled', enabled, 'created_at', created_at,
        'last_run_started_at', last_run_started_at, 'last_run_finished_at', last_run_finished_at,
        'last_run_outcome', last_run_outcome
    ) ORDER BY created_at DESC) FROM agents_with_last_trace), '[]'::jsonb) AS agents,
    COALESCE((SELECT jsonb_agg(jsonb_build_object(
        'id', id, 'name', name, 'slug', slug, 'archived', archived, 'created_at', created_at,
        'task_count', task_count, 'document_count', document_count
    ) ORDER BY created_at ASC) FROM projects_with_counts), '[]'::jsonb) AS projects,
    COALESCE((SELECT jsonb_agg(jsonb_build_object(
        'id', id, 'actor_type', actor_type, 'actor_id', actor_id, 'install_id', install_id,
        'event_class', event_class, 'action', action, 'status', status, 'title', title, 'created_at', created_at
    ) ORDER BY created_at DESC) FROM recent_activity), '[]'::jsonb) AS recent_activity,
    COALESCE((SELECT jsonb_agg(jsonb_build_object(
        'provider', provider, 'model', model, 'credit_type', credit_type, 'event_count', event_count,
        'total_platform_cost_usd', total_platform_cost_usd, 'total_credits_debited', total_credits_debited
    ) ORDER BY total_platform_cost_usd DESC) FROM spend_breakdown), '[]'::jsonb) AS spend_breakdown,
    COALESCE((SELECT jsonb_agg(jsonb_build_object(
        'id', id, 'agent_id', agent_id, 'action_domain', action_domain, 'action_name', action_name,
        'tool_kind', tool_kind, 'tool_id', tool_id, 'status', status, 'error_code', error_code,
        'run_id', run_id, 'trace_id', trace_id, 'created_at', created_at
    ) ORDER BY created_at DESC) FROM recent_failures), '[]'::jsonb) AS recent_failures,
    COALESCE((SELECT jsonb_object_agg(final_state, n) FROM runs_by_outcome), '{}'::jsonb) AS runs_by_outcome
"""


async def get_account_detail(pool: Any, workspace_id: str) -> Optional[Dict[str, Any]]:
    """One workspace: members, agents (with last run and outcome), projects,
    recent activity, spend by model/provider, recent failures, and a
    workspace-level terminal-run-outcome breakdown from run_archive.
    Returns None when the workspace_id does not exist (the route turns that
    into 404) -- distinct from OperatorConsoleScopeBroken, which means the
    query itself could not be trusted at all."""
    token = str(workspace_id or "").strip()
    if not token:
        return None
    row = await control_plane_repository.rls_fetchrow(
        pool, OPERATOR_ACCOUNT_DETAIL_SQL, token, bypass_rls=True
    )
    payload = _require_bypass_canary(row, operation="get_account_detail")
    if not bool(payload.get("workspace_found")):
        return None

    return {
        "generated_at": _now_iso(),
        "rls_bypass_verified": True,
        "tenant_id": payload.get("tenant_id"),
        "workspace_id": payload.get("workspace_id"),
        "name": payload.get("name"),
        "created_at": payload.get("created_at"),
        "workspace_status": payload.get("workspace_status"),
        "workspace_type": payload.get("workspace_type"),
        "owner": payload.get("owner") or {},
        "members": _rows(payload, "members"),
        "agents": _rows(payload, "agents"),
        "projects": _rows(payload, "projects"),
        "recent_activity": _rows(payload, "recent_activity"),
        "spend_breakdown": _rows(payload, "spend_breakdown"),
        "recent_failures": _rows(payload, "recent_failures"),
        "runs_by_outcome": {
            str(k): _int(v) for k, v in _decode_json_object(payload.get("runs_by_outcome")).items()
        },
    }


# ---------------------------------------------------------------------------
# 4. Activation funnel -- signup -> project -> task/document -> agent ->
# agent ran -> invited a second person, each step a WORKSPACE-level fact
# (this platform is currently one workspace per signup, see overview) with
# its own count and drop-off from the step before.
# ---------------------------------------------------------------------------

# "created a project" counts only a NON-default project. `ensure_default_
# project` (projects_repository.py) lazily creates a "General" project the
# first time a workspace's own owner opens the fleet view -- that is a
# passive side effect of navigation, not a deliberate "I made a project"
# action, so counting it here would overstate real activation. Every other
# step is a genuine, deliberate row: a real task/document, a real agent
# install, a real completed agent run (the same agent_traces EXISTS pattern
# platform_activation_service established), a real second active member.
OPERATOR_ACTIVATION_FUNNEL_SQL = """
WITH workspace_projects AS (
    SELECT DISTINCT tenant_id, workspace_id
    FROM projects
    WHERE is_default = FALSE
),
workspace_task_or_doc AS (
    SELECT tenant_id, workspace_id FROM project_tasks
    UNION
    SELECT tenant_id, workspace_id FROM project_documents
),
workspace_agents AS (
    SELECT DISTINCT tenant_id, workspace_id
    FROM workspace_agent_installs
),
agent_run_flags AS (
    SELECT
        wai.tenant_id, wai.workspace_id,
        EXISTS (
            SELECT 1 FROM agent_traces atr
            WHERE atr.tenant_id = wai.tenant_id AND atr.workspace_id = wai.workspace_id
              AND (
                  atr.root_agent_id = ('specialist:' || wai.id)
                  OR (COALESCE(ad.agent_kind, 'specialist') = 'master' AND atr.root_agent_id IN ('sage', 'sage_main_agent'))
              )
        ) AS has_run
    FROM workspace_agent_installs wai
    LEFT JOIN agent_definitions ad ON ad.id = wai.agent_definition_id
),
workspace_with_agent_runs AS (
    SELECT DISTINCT tenant_id, workspace_id FROM agent_run_flags WHERE has_run
),
membership_counts AS (
    SELECT tenant_id, workspace_id, COUNT(*) AS member_count
    FROM workspace_memberships
    WHERE status = 'active'
    GROUP BY tenant_id, workspace_id
)
SELECT
    current_setting('app.rls_bypass', true) AS rls_bypass_scope,
    (SELECT COUNT(*) FROM workspaces) AS step_signup,
    (SELECT COUNT(*) FROM workspace_projects) AS step_created_project,
    (SELECT COUNT(*) FROM workspace_task_or_doc) AS step_created_task_or_document,
    (SELECT COUNT(*) FROM workspace_agents) AS step_created_agent,
    (SELECT COUNT(*) FROM workspace_with_agent_runs) AS step_agent_ran,
    (SELECT COUNT(*) FROM membership_counts WHERE member_count > 1) AS step_second_member
"""

_FUNNEL_STEPS = (
    ("signup", "step_signup"),
    ("created_project", "step_created_project"),
    ("created_task_or_document", "step_created_task_or_document"),
    ("created_agent", "step_created_agent"),
    ("agent_ran", "step_agent_ran"),
    ("second_member", "step_second_member"),
)


async def build_activation_funnel(pool: Any) -> Dict[str, Any]:
    row = await control_plane_repository.rls_fetchrow(pool, OPERATOR_ACTIVATION_FUNNEL_SQL, bypass_rls=True)
    payload = _require_bypass_canary(row, operation="activation_funnel")

    signup_count = _int(payload.get("step_signup"))
    steps: List[Dict[str, Any]] = []
    previous_count = signup_count
    for step_name, column in _FUNNEL_STEPS:
        count = _int(payload.get(column))
        steps.append({
            "step": step_name,
            "count": count,
            "pct_of_signups": _pct(count, signup_count),
            "drop_off_from_previous": max(previous_count - count, 0) if step_name != "signup" else 0,
            "drop_off_pct_from_previous": (
                _pct(max(previous_count - count, 0), previous_count) if step_name != "signup" else 0.0
            ),
        })
        previous_count = count

    return {
        "generated_at": _now_iso(),
        "rls_bypass_verified": True,
        "steps": steps,
    }


# ---------------------------------------------------------------------------
# 5. Retention -- active in the last 1/7/30 days, from auth_sessions.
# last_seen_at, which is touched on EVERY authenticated request
# (auth_store_repository.touch_auth_session, called with touch_session=True
# from auth.py). auth_sessions carries no tenant_id/workspace_id column at
# all (it is keyed on user_id, session_id) so it is not RLS-protected and
# was never a candidate for the bypass trap on its own -- but `users` is,
# and this query still runs through the same bypassed connection/canary as
# every other function in this module, for the consistency reason in the
# module docstring.
# ---------------------------------------------------------------------------

OPERATOR_RETENTION_SQL = """
SELECT
    current_setting('app.rls_bypass', true) AS rls_bypass_scope,
    (SELECT COUNT(*) FROM users) AS total_users,
    (SELECT COUNT(DISTINCT user_id) FROM auth_sessions
        WHERE last_seen_at IS NOT NULL AND last_seen_at >= extract(epoch from now()) - 86400
    ) AS active_last_1_day,
    (SELECT COUNT(DISTINCT user_id) FROM auth_sessions
        WHERE last_seen_at IS NOT NULL AND last_seen_at >= extract(epoch from now()) - 604800
    ) AS active_last_7_days,
    (SELECT COUNT(DISTINCT user_id) FROM auth_sessions
        WHERE last_seen_at IS NOT NULL AND last_seen_at >= extract(epoch from now()) - 2592000
    ) AS active_last_30_days
"""


async def build_retention(pool: Any) -> Dict[str, Any]:
    row = await control_plane_repository.rls_fetchrow(pool, OPERATOR_RETENTION_SQL, bypass_rls=True)
    payload = _require_bypass_canary(row, operation="retention")

    total_users = _int(payload.get("total_users"))
    active_1d = _int(payload.get("active_last_1_day"))
    active_7d = _int(payload.get("active_last_7_days"))
    active_30d = _int(payload.get("active_last_30_days"))

    return {
        "generated_at": _now_iso(),
        "rls_bypass_verified": True,
        "total_users": total_users,
        "active": {
            "last_1_day": {"count": active_1d, "pct_of_users": _pct(active_1d, total_users)},
            "last_7_days": {"count": active_7d, "pct_of_users": _pct(active_7d, total_users)},
            "last_30_days": {"count": active_30d, "pct_of_users": _pct(active_30d, total_users)},
        },
    }


# ---------------------------------------------------------------------------
# 6. Failures -- recent agent_action_events with a non-success status,
# grouped by (status, error_code, action_domain) so a systemic breakage is
# visible, plus a bounded recent list. "non-success" is the ACTION_STATUSES
# vocabulary's own failure-shaped subset (agent_action_metering_service.
# ACTION_STATUSES): 'failed', 'blocked', 'denied' -- not 'started'
# (in-flight, not a failure) and not 'approval_required'/'policy_decision'
# (neutral control-flow states, not failures).
# ---------------------------------------------------------------------------

_FAILURE_STATUSES = ("failed", "blocked", "denied")

OPERATOR_FAILURES_SQL = """
WITH failing_events AS (
    SELECT *
    FROM agent_action_events
    WHERE status = ANY($1::text[])
      AND created_at >= NOW() - make_interval(days => $2::int)
),
grouped AS (
    SELECT
        status, error_code, action_domain,
        COUNT(*) AS event_count,
        COUNT(DISTINCT workspace_id) AS distinct_workspaces,
        COUNT(DISTINCT agent_id) AS distinct_agents,
        MIN(created_at) AS first_seen,
        MAX(created_at) AS last_seen
    FROM failing_events
    GROUP BY status, error_code, action_domain
),
recent AS (
    SELECT id, tenant_id, workspace_id, agent_id, action_domain, action_name, tool_kind, tool_id,
        status, error_code, run_id, trace_id, created_at
    FROM failing_events
    ORDER BY created_at DESC
    LIMIT $3
)
SELECT
    current_setting('app.rls_bypass', true) AS rls_bypass_scope,
    (SELECT COUNT(*) FROM failing_events) AS total_failures,
    COALESCE((SELECT jsonb_agg(jsonb_build_object(
        'status', status, 'error_code', error_code, 'action_domain', action_domain,
        'event_count', event_count, 'distinct_workspaces', distinct_workspaces,
        'distinct_agents', distinct_agents, 'first_seen', first_seen, 'last_seen', last_seen
    ) ORDER BY event_count DESC) FROM grouped), '[]'::jsonb) AS grouped,
    COALESCE((SELECT jsonb_agg(jsonb_build_object(
        'id', id, 'tenant_id', tenant_id, 'workspace_id', workspace_id, 'agent_id', agent_id,
        'action_domain', action_domain, 'action_name', action_name, 'tool_kind', tool_kind, 'tool_id', tool_id,
        'status', status, 'error_code', error_code, 'run_id', run_id, 'trace_id', trace_id, 'created_at', created_at
    ) ORDER BY created_at DESC) FROM recent), '[]'::jsonb) AS recent
"""


async def list_failures(pool: Any, *, days: int = 7, limit: int = 200) -> Dict[str, Any]:
    bounded_days = max(1, min(int(days), 90))
    bounded_limit = max(1, min(int(limit), 1000))
    row = await control_plane_repository.rls_fetchrow(
        pool,
        OPERATOR_FAILURES_SQL,
        list(_FAILURE_STATUSES),
        bounded_days,
        bounded_limit,
        bypass_rls=True,
    )
    payload = _require_bypass_canary(row, operation="list_failures")

    return {
        "generated_at": _now_iso(),
        "rls_bypass_verified": True,
        "window_days": bounded_days,
        "total_failures": _int(payload.get("total_failures")),
        "grouped": _rows(payload, "grouped"),
        "recent": _rows(payload, "recent"),
    }


# ---------------------------------------------------------------------------
# 7. Spend -- by workspace, by provider/model, over time. credit_ledger_
# events already unifies AI-token spend and Agent-Computer (VPS) hardware
# spend under one credit_type ('ai_tokens' vs 'computer_runtime'), so no
# separate hardware-metering query is needed here -- provider/model are
# simply NULL on 'computer_runtime' rows, left as-is rather than relabelled
# in SQL so the API layer/frontend decides how to present a "compute" row.
# ---------------------------------------------------------------------------

OPERATOR_SPEND_SQL = """
WITH windowed AS (
    SELECT *
    FROM credit_ledger_events
    WHERE created_at >= NOW() - make_interval(days => $1::int)
),
by_workspace AS (
    SELECT
        ev.workspace_id,
        COALESCE(MAX(w.name), ev.workspace_id) AS name,
        COALESCE(SUM(ev.platform_cost_usd), 0) AS total_platform_cost_usd,
        COALESCE(SUM(ev.credits_debited), 0) AS total_credits_debited,
        COUNT(*) AS event_count
    FROM windowed ev
    LEFT JOIN workspaces w ON w.tenant_id = ev.tenant_id AND w.workspace_id = ev.workspace_id
    GROUP BY ev.workspace_id
),
by_provider_model AS (
    SELECT
        provider, model, credit_type,
        COALESCE(SUM(platform_cost_usd), 0) AS total_platform_cost_usd,
        COALESCE(SUM(credits_debited), 0) AS total_credits_debited,
        COUNT(*) AS event_count
    FROM windowed
    GROUP BY provider, model, credit_type
),
over_time AS (
    SELECT
        date_trunc('day', created_at) AS day,
        COALESCE(SUM(platform_cost_usd), 0) AS total_platform_cost_usd,
        COALESCE(SUM(credits_debited), 0) AS total_credits_debited,
        COUNT(*) AS event_count
    FROM windowed
    GROUP BY date_trunc('day', created_at)
)
SELECT
    current_setting('app.rls_bypass', true) AS rls_bypass_scope,
    (SELECT COALESCE(SUM(platform_cost_usd), 0) FROM windowed) AS total_platform_cost_usd,
    (SELECT COALESCE(SUM(credits_debited), 0) FROM windowed) AS total_credits_debited,
    (SELECT COUNT(*) FROM windowed) AS total_events,
    COALESCE((SELECT jsonb_agg(jsonb_build_object(
        'workspace_id', workspace_id, 'name', name, 'total_platform_cost_usd', total_platform_cost_usd,
        'total_credits_debited', total_credits_debited, 'event_count', event_count
    ) ORDER BY total_platform_cost_usd DESC) FROM by_workspace), '[]'::jsonb) AS by_workspace,
    COALESCE((SELECT jsonb_agg(jsonb_build_object(
        'provider', provider, 'model', model, 'credit_type', credit_type,
        'total_platform_cost_usd', total_platform_cost_usd, 'total_credits_debited', total_credits_debited,
        'event_count', event_count
    ) ORDER BY total_platform_cost_usd DESC) FROM by_provider_model), '[]'::jsonb) AS by_provider_model,
    COALESCE((SELECT jsonb_agg(jsonb_build_object(
        'day', day, 'total_platform_cost_usd', total_platform_cost_usd, 'total_credits_debited', total_credits_debited,
        'event_count', event_count
    ) ORDER BY day ASC) FROM over_time), '[]'::jsonb) AS over_time
"""


async def build_spend(pool: Any, *, days: int = 30) -> Dict[str, Any]:
    bounded_days = max(1, min(int(days), 365))
    row = await control_plane_repository.rls_fetchrow(pool, OPERATOR_SPEND_SQL, bounded_days, bypass_rls=True)
    payload = _require_bypass_canary(row, operation="spend")

    return {
        "generated_at": _now_iso(),
        "rls_bypass_verified": True,
        "window_days": bounded_days,
        "total_platform_cost_usd": _float(payload.get("total_platform_cost_usd")),
        "total_credits_debited": _float(payload.get("total_credits_debited")),
        "total_events": _int(payload.get("total_events")),
        "by_workspace": _rows(payload, "by_workspace"),
        "by_provider_model": _rows(payload, "by_provider_model"),
        "over_time": _rows(payload, "over_time"),
    }
