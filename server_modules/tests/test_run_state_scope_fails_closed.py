"""The run-state reads must fail CLOSED when nobody names a workspace.

Sibling of `test_runtime_routes_cross_tenant_scope.py`, which closed the same
defect on the fleet queries.  The idiom is

    WHERE ($1 = '' OR workspace_id = $1)

With no argument, `$1 = ''` is true and the predicate is vacuous: a forgotten
scope returns every tenant's rows.  Three run-state queries still carried it --
`list_live_runs_page`, `count_live_runs`, `list_pending_approvals_page` (plus
`list_pending_approvals`, which had no workspace predicate at all).

The containment was weaker than it looked.  `agent_workspace_api` wrote

    workspace_filter = ...enforce_workspace_access(...) if workspace_id else None

so omitting the query parameter produced an unscoped SQL read AND skipped every
downstream Python re-filter, each of which is guarded `if workspace_filter and
...`.  Two of those three routes sit behind `require_admin_api_key`, which is
`enforce_minimum_role(..., "owner")` -- satisfied by ANY workspace owner of ANY
tenant, a role check and not a tenancy check.  The third, `GET /artifacts` /
`GET /artifacts/workspace`, sits behind plain `require_api_key`, which per
`runtime_common.py` answers only "is someone logged in".

Every test below fails against the pre-fix code.
"""

from __future__ import annotations

import asyncio
import re
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


def _repo():
    """Resolve the module fresh on every call, never at import time.

    Sibling test modules `importlib.reload()` parts of `server_modules`, which
    swaps the object in `sys.modules` while a module-level `from ... import X`
    keeps pointing at the dead one.
    """
    from server_modules import run_state_repository

    return run_state_repository


def _workspace_api():
    from server_modules import agent_workspace_api

    return agent_workspace_api


# ── A fake pool that honours the parameters the query actually binds ──────────
# Not a real database, but it evaluates the SAME predicate the SQL does, from
# the SAME bound arguments -- so a fix that "passes the scope" without wiring it
# into the WHERE clause still fails here.


class _ScopeAwarePool:
    def __init__(self, rows, *, scope_flag_index: int, scope_list_index: int, workspace_of):
        self._rows = list(rows)
        self._scope_flag_index = scope_flag_index
        self._scope_list_index = scope_list_index
        self._workspace_of = workspace_of
        self.calls: list[tuple[str, tuple]] = []

    def _visible(self, args):
        include_all = bool(args[self._scope_flag_index])
        scope = list(args[self._scope_list_index] or [])
        if include_all:
            return list(self._rows)
        return [row for row in self._rows if self._workspace_of(row) in scope]

    async def fetch(self, query, *args):
        self.calls.append((query, args))
        return self._visible(args)

    async def fetchrow(self, query, *args):
        self.calls.append((query, args))
        return {"count": len(self._visible(args))}

    async def execute(self, *args, **kwargs):
        return None


LIVE_RUN_ROWS = [
    {
        "run_id": "run-a",
        "workspace_id": "ws-a",
        "tenant_id": "tenant-a",
        "state": "running",
        "payload": {"run_id": "run-a", "status": "running"},
        "trace_id": "trace-a",
        "version": 1,
        "registered_at": "2026-08-08T00:00:00Z",
    },
    {
        "run_id": "run-b-secret",
        "workspace_id": "ws-b",
        "tenant_id": "tenant-b",
        "state": "running",
        "payload": {"run_id": "run-b-secret", "status": "running"},
        "trace_id": "trace-b",
        "version": 1,
        "registered_at": "2026-08-08T00:00:00Z",
    },
]

APPROVAL_ROWS = [
    {
        "run_id": "run-a",
        "step_id": "approval-a",
        "approval_id": "approval-a",
        "status": "requested",
        "requested_at": "2026-08-08T00:00:00Z",
        "resolved_at": None,
        "resolution": None,
        "actor": "system",
        "trace_id": "trace-a",
        "request_payload": {"prompt": "Approve", "workspace_id": "ws-a"},
        "decision_payload": {},
        "metadata": {},
        "expires_at": None,
        "updated_at": "2026-08-08T00:00:00Z",
        "version": 0,
    },
    {
        "run_id": "run-b-secret",
        "step_id": "approval-b-secret",
        "approval_id": "approval-b-secret",
        "status": "requested",
        "requested_at": "2026-08-08T00:00:00Z",
        "resolved_at": None,
        "resolution": None,
        "actor": "system",
        "trace_id": "trace-b",
        "request_payload": {"prompt": "Approve", "workspace_id": "ws-b"},
        "decision_payload": {},
        "metadata": {},
        "expires_at": None,
        "updated_at": "2026-08-08T00:00:00Z",
        "version": 0,
    },
]


def _approval_workspace(row):
    return (
        (row.get("request_payload") or {}).get("workspace_id")
        or (row.get("metadata") or {}).get("workspace_id")
        or "default"
    )


class RunStateReadsFailClosedTests(unittest.TestCase):
    """A missing scope raises. It never returns everything."""

    def test_list_live_runs_page_refuses_an_unscoped_read(self):
        with self.assertRaises(ValueError):
            asyncio.run(_repo().list_live_runs_page())

    def test_count_live_runs_refuses_an_unscoped_read(self):
        with self.assertRaises(ValueError):
            asyncio.run(_repo().count_live_runs())

    def test_list_pending_approvals_page_refuses_an_unscoped_read(self):
        with self.assertRaises(ValueError):
            asyncio.run(_repo().list_pending_approvals_page())

    def test_list_pending_approvals_refuses_an_unscoped_read(self):
        with self.assertRaises(ValueError):
            asyncio.run(_repo().list_pending_approvals(limit=10))

    def test_blank_workspace_id_is_a_missing_scope_not_a_wildcard(self):
        # `workspace_id=""` is what a forgotten query parameter looks like once
        # it has been through `str(x or "").strip()`.
        for kwargs in ({"workspace_id": ""}, {"workspace_id": "   "}, {"workspace_id": None}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    asyncio.run(_repo().list_live_runs_page(**kwargs))

    def test_sync_wrappers_raise_on_their_own(self):
        # `_run_sync` swallows exceptions into `fallback`, so validation only
        # inside the coroutine would turn a forgotten scope into a silent empty
        # list instead of a loud failure.
        with self.assertRaises(ValueError):
            _repo().sync_list_live_runs_page()
        with self.assertRaises(ValueError):
            _repo().sync_count_live_runs()
        with self.assertRaises(ValueError):
            _repo().sync_list_pending_approvals_page()
        with self.assertRaises(ValueError):
            _repo().sync_list_pending_approvals()

    def test_a_deliberate_global_read_is_allowed_when_named(self):
        with patch.object(_repo(), "_read_pool", new=AsyncMock(return_value=None)):
            self.assertEqual(
                asyncio.run(_repo().list_live_runs_page(include_all_workspaces=True)),
                [],
            )
            self.assertEqual(
                asyncio.run(_repo().count_live_runs(include_all_workspaces=True)),
                0,
            )
            self.assertEqual(
                asyncio.run(_repo().list_pending_approvals_page(include_all_workspaces=True)),
                [],
            )

    def test_global_read_cannot_be_combined_with_a_scope(self):
        # Silently ignoring one of the two is how a "scoped" call quietly
        # becomes a global one.
        with self.assertRaises(ValueError):
            asyncio.run(
                _repo().list_live_runs_page(
                    workspace_id="ws-a",
                    include_all_workspaces=True,
                )
            )

    def test_empty_workspace_scope_returns_nothing_rather_than_everything(self):
        # An empty allow-list means "this caller may see no workspace" and must
        # never be widened back into "no filter".
        pool = _ScopeAwarePool(
            LIVE_RUN_ROWS,
            scope_flag_index=0,
            scope_list_index=1,
            workspace_of=lambda row: row["workspace_id"],
        )
        with patch.object(_repo(), "_read_pool", new=AsyncMock(return_value=pool)), patch.object(
            _repo(), "_ensure_live_run_tables", new=AsyncMock(return_value=None)
        ):
            items = asyncio.run(_repo().list_live_runs_page(workspace_ids=[]))

        self.assertEqual(items, [])


class RunStateCrossTenantTests(unittest.TestCase):
    """A caller scoped to tenant A must not receive tenant B's rows."""

    def test_live_runs_page_excludes_a_foreign_tenants_run(self):
        pool = _ScopeAwarePool(
            LIVE_RUN_ROWS,
            scope_flag_index=0,
            scope_list_index=1,
            workspace_of=lambda row: row["workspace_id"],
        )
        with patch.object(_repo(), "_read_pool", new=AsyncMock(return_value=pool)), patch.object(
            _repo(), "_ensure_live_run_tables", new=AsyncMock(return_value=None)
        ):
            items = asyncio.run(_repo().list_live_runs_page(workspace_id="ws-a"))

        self.assertEqual([item["run_id"] for item in items], ["run-a"])
        query, args = pool.calls[-1]
        self.assertIs(args[0], False)
        self.assertEqual(args[1], ["ws-a"])
        # The predicate must be in the SQL, not only in a Python post-filter.
        self.assertIn("workspace_id = ANY($2::text[])", query)
        self.assertNotIn("$1 = ''", query)

    def test_count_live_runs_counts_only_the_callers_workspaces(self):
        pool = _ScopeAwarePool(
            LIVE_RUN_ROWS,
            scope_flag_index=0,
            scope_list_index=1,
            workspace_of=lambda row: row["workspace_id"],
        )
        with patch.object(_repo(), "_read_pool", new=AsyncMock(return_value=pool)), patch.object(
            _repo(), "_ensure_live_run_tables", new=AsyncMock(return_value=None)
        ):
            count = asyncio.run(_repo().count_live_runs(workspace_id="ws-a"))

        self.assertEqual(count, 1)

    def test_pending_approvals_page_excludes_a_foreign_tenants_approval(self):
        pool = _ScopeAwarePool(
            APPROVAL_ROWS,
            scope_flag_index=0,
            scope_list_index=1,
            workspace_of=_approval_workspace,
        )
        with patch.object(_repo(), "_read_pool", new=AsyncMock(return_value=pool)), patch.object(
            _repo(), "_ensure_run_approval_table", new=AsyncMock(return_value=None)
        ):
            items = asyncio.run(_repo().list_pending_approvals_page(workspace_id="ws-a"))

        self.assertEqual([item["approval_id"] for item in items], ["approval-a"])

    def test_a_multi_workspace_caller_sees_only_their_own_workspaces(self):
        # `/runs` legitimately spans every workspace the caller belongs to; that
        # is a list, never "all".
        rows = LIVE_RUN_ROWS + [
            {
                "run_id": "run-a2",
                "workspace_id": "ws-a2",
                "tenant_id": "tenant-a",
                "state": "running",
                "payload": {"run_id": "run-a2", "status": "running"},
                "trace_id": "trace-a2",
                "version": 1,
                "registered_at": "2026-08-08T00:00:00Z",
            }
        ]
        pool = _ScopeAwarePool(
            rows,
            scope_flag_index=0,
            scope_list_index=1,
            workspace_of=lambda row: row["workspace_id"],
        )
        with patch.object(_repo(), "_read_pool", new=AsyncMock(return_value=pool)), patch.object(
            _repo(), "_ensure_live_run_tables", new=AsyncMock(return_value=None)
        ):
            items = asyncio.run(_repo().list_live_runs_page(workspace_ids=["ws-a", "ws-a2"]))

        self.assertEqual(sorted(item["run_id"] for item in items), ["run-a", "run-a2"])


class WorkspaceApiScopeTests(unittest.TestCase):
    """The caller-side `if workspace_id else None` is gone."""

    def test_bounded_live_run_helper_requires_a_workspace(self):
        with self.assertRaises(ValueError):
            _workspace_api()._list_workspace_live_runs_bounded(workspace_id=None)
        with self.assertRaises(ValueError):
            _workspace_api()._list_workspace_live_runs_bounded(workspace_id="")

    def test_bounded_approvals_helper_requires_a_workspace(self):
        with self.assertRaises(ValueError):
            _workspace_api()._list_workspace_pending_approvals_bounded(workspace_id=None)

    def test_no_route_turns_a_missing_workspace_into_an_unscoped_read(self):
        # A behavioural test cannot reach these three route bodies without a
        # full app + auth stack, and the defect is a one-token conditional that
        # type-checks perfectly.  Assert on the source, the way the gateway's
        # `exec-file-timeout-child-leak` drift assertion does.
        source = Path(_workspace_api().__file__).read_text(encoding="utf-8")
        offenders = re.findall(
            r"workspace_filter\s*=.*if\s+workspace_id\s+else\s+None", source
        )
        self.assertEqual(
            offenders,
            [],
            "`workspace_filter = ... if workspace_id else None` converts a missing "
            "query parameter into an unscoped read AND skips every "
            "`if workspace_filter and ...` re-filter below it. Resolve the "
            "caller's own workspace with enforce_workspace_access(current_user, "
            "workspace_id) instead.",
        )


# ── Reintroduction guard ─────────────────────────────────────────────────────


_SERVER_MODULES = Path(__file__).resolve().parent.parent

# Columns whose fail-open filter is a tenancy boundary. A vacuous predicate on
# `status` or `usage_month` is a UX choice; a vacuous one on these is a leak.
_SCOPE_COLUMNS = (
    "tenant_id",
    "workspace_id",
    "owner_workspace_id",
    "org_id",
    "account_id",
)

_COLUMN_ALTERNATION = "|".join(_SCOPE_COLUMNS)

# The three shapes that fail open. Each reads "if the argument is empty/NULL,
# match every row".
_FAIL_OPEN_PATTERNS = (
    # ($1 = '' OR tenant_id = $1)   /   (? = '' OR tenant_id = ?)
    re.compile(
        r"\(\s*(?:\$\d+|\?)(?:::\w+)?\s*=\s*''\s+OR\s+(?:LOWER\(COALESCE\()?"
        rf"(?:\w+\.)?({_COLUMN_ALTERNATION})\b",
        re.IGNORECASE,
    ),
    # ($3::text IS NULL OR tenant_id = $3)
    re.compile(
        r"\(\s*(?:\$\d+|\?)(?:::[\w\[\]]+)?\s+IS\s+NULL\s+OR\s+(?:\w+\.)?"
        rf"({_COLUMN_ALTERNATION})\b",
        re.IGNORECASE,
    ),
    # (CARDINALITY($2::text[]) = 0 OR workspace_id = ANY($2::text[]))
    re.compile(
        r"CARDINALITY\(\s*\$\d+::[\w\[\]]+\s*\)\s*=\s*0\s+OR\s+(?:\w+\.)?"
        rf"({_COLUMN_ALTERNATION})\b",
        re.IGNORECASE,
    ),
)


def _enclosing_function(lines: list[str], index: int) -> str:
    for cursor in range(index, -1, -1):
        match = re.match(r"^\s*(?:async\s+)?def\s+(\w+)", lines[cursor])
        if match:
            return match.group(1)
    return "<module>"


def _is_prose(line: str) -> bool:
    """True for a comment or docstring line that merely QUOTES the idiom.

    Every guard in this codebase documents the shape it defends against, so a
    naive scan flags its own fixes. SQL here never contains a backtick and never
    starts a line with `#`; prose about SQL routinely does both.
    """
    stripped = line.strip()
    return stripped.startswith("#") or "`" in line


def _scan_for_fail_open_scope_filters() -> set[tuple[str, str, str]]:
    found: set[tuple[str, str, str]] = set()
    for path in sorted(_SERVER_MODULES.rglob("*.py")):
        if "tests" in path.parts:
            continue
        lines = path.read_text(encoding="utf-8").split("\n")
        for index, line in enumerate(lines):
            if _is_prose(line):
                continue
            for pattern in _FAIL_OPEN_PATTERNS:
                for match in pattern.finditer(line):
                    found.add(
                        (
                            path.relative_to(_SERVER_MODULES).as_posix(),
                            _enclosing_function(lines, index),
                            match.group(1).lower(),
                        )
                    )
    return found


# Every surviving instance, each with a written verdict. The expected set is
# hand-written HERE and the actual set is scraped from the SOURCE -- two
# different origins, so this check cannot merely confirm itself (see
# `preflight._RLS_COVERAGE_EXCEPTIONS`, added after a conformance check that
# derived its expectations from the file it was checking passed while 60 tables
# went unprotected).
_ACCEPTED_FAIL_OPEN_SCOPE_FILTERS: dict[tuple[str, str, str], str] = {
    ("run_state_repository.py", "list_fleet_workers", "tenant_id"):
        "Guarded in Python by _require_explicit_scope; a missing scope raises "
        "before the query runs (fix/cross-tenant-runtime-routes, 2ea28a78e).",
    ("run_state_repository.py", "list_fleet_workers", "workspace_id"):
        "Same _require_explicit_scope guard.",
    ("run_state_repository.py", "list_fleet_queue_partitions", "tenant_id"):
        "Same _require_explicit_scope guard.",
    ("run_state_repository.py", "list_fleet_queue_partitions", "workspace_id"):
        "Same _require_explicit_scope guard.",
    ("run_state_repository.py", "list_live_runs_by_state", "workspace_id"):
        "`workspace_ids=None` means every workspace by documented contract "
        "(_normalized_workspace_scope), and an EMPTY list is preserved rather "
        "than collapsed. Callers pass a concrete scope.",
    ("run_state_repository.py", "list_run_archive", "workspace_id"):
        "Same _normalized_workspace_scope contract as list_live_runs_by_state.",
    ("run_state_repository.py", "list_undelivered_outbox_events", "tenant_id"):
        "Runtime delivery daemon, not a user-facing route: draining every "
        "tenant's outbox is the job. No HTTP caller.",
    ("run_state_repository.py", "list_undelivered_outbox_events", "workspace_id"):
        "Same outbox daemon.",
    ("run_state_repository.py", "claim_due_outbox_events", "tenant_id"):
        "Same outbox daemon.",
    ("run_state_repository.py", "claim_due_outbox_events", "workspace_id"):
        "Same outbox daemon.",
    ("control_plane_repository.py", "_local_get_deployed_agent", "tenant_id"):
        "Keyed on the deployed_agents primary key; scope narrows an already "
        "unique row. SQLite dev fallback only.",
    ("control_plane_repository.py", "_local_get_deployed_agent", "owner_workspace_id"):
        "Same id-keyed lookup.",
    ("control_plane_repository.py", "_local_get_deployed_agent_by_backing_install_id", "tenant_id"):
        "Keyed on the unique backing_install_id. SQLite dev fallback only.",
    ("control_plane_repository.py", "_local_get_deployed_agent_by_backing_install_id", "owner_workspace_id"):
        "Same id-keyed lookup.",
    ("control_plane_repository.py", "_local_list_deployed_agents_for_workspace", "tenant_id"):
        "owner_workspace_id is bound unconditionally by the caller; only the "
        "tenant narrowing is optional. SQLite dev fallback only.",
    ("control_plane_repository.py", "get_deployed_agent_by_id", "tenant_id"):
        "Keyed on the primary key AND executed on an RLS-scoped connection "
        "(_scoped_connection sets the tenant/workspace GUCs).",
    ("control_plane_repository.py", "get_deployed_agent_by_id", "owner_workspace_id"):
        "Same id-keyed, RLS-scoped read.",
    ("control_plane_repository.py", "get_deployed_agent_by_backing_install_id", "tenant_id"):
        "Keyed on the unique backing_install_id, RLS-scoped connection.",
    ("control_plane_repository.py", "get_deployed_agent_by_backing_install_id", "owner_workspace_id"):
        "Same id-keyed, RLS-scoped read.",
    ("control_plane_repository.py", "list_deployed_agents_for_workspace", "tenant_id"):
        "owner_workspace_id = $1 is bound unconditionally; only the tenant "
        "narrowing is optional.",
    ("control_plane_repository.py", "build_governance_scope_export", "workspace_id"):
        "tenant_id = $1 is bound unconditionally on every subquery; the "
        "workspace narrowing is an optional zoom inside one tenant.",
}


class FailOpenScopeFilterDriftTests(unittest.TestCase):
    """A convention nobody can enforce comes back.

    A behavioural test cannot catch a NEW `($1 = '' OR tenant_id = $1)`: it
    type-checks, it runs, and it behaves perfectly for every caller that
    remembers the argument. Only a source scan sees it.
    """

    def test_no_new_fail_open_tenant_or_workspace_filter(self):
        found = _scan_for_fail_open_scope_filters()
        accepted = set(_ACCEPTED_FAIL_OPEN_SCOPE_FILTERS)
        new_offenders = sorted(found - accepted)
        self.assertEqual(
            new_offenders,
            [],
            "New fail-open tenant/workspace filter(s). "
            "`WHERE ($1 = '' OR tenant_id = $1)` returns EVERY tenant's rows "
            "when the argument is forgotten. Either bind the scope "
            "unconditionally (`= ANY($n::text[])` with an explicit "
            "include-all boolean, as run_state_repository.list_live_runs_page "
            "does), or guard the entry point so a missing scope raises (as "
            "_require_explicit_scope / _require_explicit_workspace_scope do). "
            "If the query is genuinely global on purpose, add it to "
            "_ACCEPTED_FAIL_OPEN_SCOPE_FILTERS in this file with a written "
            "verdict.",
        )

    def test_the_allowlist_has_no_stale_entries(self):
        # An allowlist that outlives the code it excuses is how the next reader
        # concludes a filter is "already reviewed" when it no longer exists.
        found = _scan_for_fail_open_scope_filters()
        stale = sorted(set(_ACCEPTED_FAIL_OPEN_SCOPE_FILTERS) - found)
        self.assertEqual(stale, [], "Allowlist entries no longer match any source line.")

    def test_the_guard_catches_a_freshly_introduced_offender(self):
        # Proof the scanner is not vacuous: run the same patterns over a
        # synthetic module body and require a hit.
        offending = [
            'rows = await pool.fetch(',
            '    """',
            "    SELECT * FROM some_new_table",
            "    WHERE ($1 = '' OR tenant_id = $1)",
            '    """,',
        ]
        hits = [
            line
            for line in offending
            for pattern in _FAIL_OPEN_PATTERNS
            if pattern.search(line)
        ]
        self.assertTrue(hits, "The scanner failed to flag a textbook fail-open filter.")

    def test_the_fixed_queries_are_no_longer_in_the_scan(self):
        found = _scan_for_fail_open_scope_filters()
        for function in (
            "list_live_runs_page",
            "count_live_runs",
            "list_pending_approvals_page",
        ):
            for column in ("workspace_id", "tenant_id"):
                self.assertNotIn(
                    ("run_state_repository.py", function, column),
                    found,
                    f"{function} still carries the fail-open idiom.",
                )


if __name__ == "__main__":
    unittest.main()
