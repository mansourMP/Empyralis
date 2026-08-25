"""Workspace search must be scoped, honest, and actually reachable.

Three classes of failure this file exists to catch, each of which would
otherwise ship green:

1. SCOPE. A member seeing a task from a project they were never added to is
   the MAN-115 boundary breaking. The fake pool below evaluates the SAME
   predicate the SQL declares, from the SAME bound arguments -- so a "fix"
   that passes the project scope in and never wires it into the WHERE clause
   still fails here. And the call-count assertions prove the hidden row was
   NEVER FETCHED, rather than fetched and filtered out in Python (which
   leaks the moment somebody adds a `limit`).

2. OUTCOME HONESTY. `[]` means "the query ran and matched nothing". An
   unreachable database must never produce it -- CLAUDE.md's law, at the one
   seam where collapsing it teaches a person their task is gone.

3. WIRING. "Built, tested, and never wired" is the most common defect in
   this codebase. A perfect search service reachable from no URL is worth
   nothing, so the router registration itself is asserted.

Plus a source scan for the banned fail-open scope idiom, following
`test_run_state_scope_fails_closed.py`. A behavioural test cannot catch a
NEW `WHERE ($1 = '' OR project_id = $1)`: it type-checks, it runs, and it
behaves perfectly for every caller that remembers the argument.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path
from unittest.mock import patch

_SERVER_MODULES = Path(__file__).resolve().parents[1]
_REPO_ROOT = _SERVER_MODULES.parent


def _service():
    """Resolved fresh on every call, never at import time -- sibling test
    modules `importlib.reload()` parts of `server_modules`, which swaps the
    object in `sys.modules` while a module-level `from ... import X` keeps
    pointing at the dead one."""
    from server_modules import workspace_search_service

    return workspace_search_service


def _routes():
    from server_modules import routes_search

    return routes_search


# ── A pool that honours the predicate the SQL actually declares ───────────
# Not a database. It reads the emitted SQL to decide WHICH filters are in
# force, then applies exactly those to the rows using the bound arguments.
# Deleting `AND t.project_id = ANY($3::text[])` from the query therefore
# makes the hidden row appear here, exactly as it would in Postgres.

_SCOPE_PREDICATES = {
    "tenant": re.compile(r"tenant_id\s*=\s*\$1", re.IGNORECASE),
    "workspace": re.compile(r"workspace_id\s*=\s*\$2", re.IGNORECASE),
    "project": re.compile(r"project_id\s*=\s*ANY\(\$3::text\[\]\)", re.IGNORECASE),
}


class _ScopeAwarePool:
    def __init__(self, task_rows, document_rows):
        self._task_rows = list(task_rows)
        self._document_rows = list(document_rows)
        self.calls: list[tuple[str, tuple]] = []

    def _rows_for(self, query: str):
        return self._document_rows if "project_documents" in query else self._task_rows

    def fetch(self, query, *args):
        self.calls.append((query, args))
        rows = self._rows_for(query)
        if _SCOPE_PREDICATES["tenant"].search(query):
            rows = [r for r in rows if r.get("tenant_id") == args[0]]
        if _SCOPE_PREDICATES["workspace"].search(query):
            rows = [r for r in rows if r.get("workspace_id") == args[1]]
        if _SCOPE_PREDICATES["project"].search(query):
            scope = list(args[2] or [])
            rows = [r for r in rows if r.get("project_id") in scope]
        # The relevance half, approximated by substring — enough to prove a
        # non-matching row is excluded; the SQL's own ranking is Postgres'.
        needle = str(args[3] or "").lower()
        if needle:
            rows = [
                r for r in rows
                if needle in " ".join(
                    str(r.get(field) or "")
                    for field in ("title", "description", "body", "path")
                ).lower()
            ]
        return rows[: int(args[5])]

    @property
    def bound_arguments(self) -> list:
        flat = []
        for _query, args in self.calls:
            for value in args:
                if isinstance(value, (list, tuple, set)):
                    flat.extend(str(v) for v in value)
                else:
                    flat.append(str(value))
        return flat


TASK_ROWS = [
    {
        "id": "task-visible", "tenant_id": "t1", "workspace_id": "ws1",
        "project_id": "proj-mine", "title": "Rewrite the onboarding invoice email",
        "description": "The copy is stale.", "status": "open", "number": 12,
        "project_task_key": "GEN", "updated_at": "2026-08-20T00:00:00Z",
    },
    {
        "id": "task-secret", "tenant_id": "t1", "workspace_id": "ws1",
        "project_id": "proj-theirs", "title": "Secret invoice for the board",
        "description": "Not this member's project.", "status": "open", "number": 4,
        "project_task_key": "SEC", "updated_at": "2026-08-21T00:00:00Z",
    },
]

DOCUMENT_ROWS = [
    {
        "id": "doc-visible", "tenant_id": "t1", "workspace_id": "ws1",
        "project_id": "proj-mine", "title": "Invoice runbook",
        "path": "specs/invoice.md", "body": "How we send an invoice.",
        "updated_at": "2026-08-20T00:00:00Z",
    },
    {
        "id": "doc-secret", "tenant_id": "t1", "workspace_id": "ws1",
        "project_id": "proj-theirs", "title": "Board invoice memo",
        "path": "secret/invoice.md", "body": "Not this member's project.",
        "updated_at": "2026-08-21T00:00:00Z",
    },
]


class _PoolHarness:
    """Patches the two control-plane seams `search_workspace` touches."""

    def __init__(self, pool):
        self.pool = pool
        self._patches = []

    def __enter__(self):
        from server_modules import control_plane_repository as cp

        async def fake_ensure():
            return self.pool

        async def fake_rls_fetch(pool, query, *args, **kwargs):
            if pool is None:
                raise AssertionError("rls_fetch called with no pool")
            return pool.fetch(query, *args)

        self._patches = [
            patch.object(cp, "ensure_control_plane_schema", fake_ensure),
            patch.object(cp, "rls_fetch", fake_rls_fetch),
        ]
        for p in self._patches:
            p.start()
        _service().reset_full_text_capability_probe()
        return self.pool

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()
        _service().reset_full_text_capability_probe()
        return False


class ScopeIsEnforcedInSqlTests(unittest.IsolatedAsyncioTestCase):
    """A project the caller cannot see yields ZERO rows, and its row is
    never fetched in the first place."""

    async def test_a_member_never_sees_another_projects_task_or_document(self):
        pool = _ScopeAwarePool(TASK_ROWS, DOCUMENT_ROWS)
        with _PoolHarness(pool):
            results = await _service().search_workspace(
                tenant_id="t1", workspace_id="ws1",
                project_ids=["proj-mine"], query="invoice",
            )

        ids = [hit["id"] for hit in results]
        self.assertIn("task-visible", ids)
        self.assertIn("doc-visible", ids)
        self.assertNotIn("task-secret", ids, "a task from an unreachable project leaked")
        self.assertNotIn("doc-secret", ids, "a document from an unreachable project leaked")

        # THE CALL-COUNT HALF. Exactly two reads -- one per kind. Not one
        # unscoped read filtered afterwards, and not one read per project.
        self.assertEqual(
            len(pool.calls), 2,
            f"expected exactly 2 scoped reads (tasks, documents), got {len(pool.calls)}",
        )
        # And the forbidden project is never even named to the database, so
        # there is no row to filter out client-side.
        self.assertNotIn(
            "proj-theirs", pool.bound_arguments,
            "the unreachable project id reached SQL as a bound argument",
        )
        for query, args in pool.calls:
            self.assertIn(
                "ANY($3::text[])", query,
                "a search query ran without an unconditional project scope",
            )
            self.assertEqual(list(args[2]), ["proj-mine"])

    async def test_removing_the_project_predicate_would_leak(self):
        """Proof the fake is not vacuous: with the scope predicate absent
        from the SQL, the very same fake returns the hidden rows. So the
        assertions above are testing the QUERY, not the harness."""
        pool = _ScopeAwarePool(TASK_ROWS, DOCUMENT_ROWS)
        unscoped = "SELECT * FROM project_tasks WHERE tenant_id = $1 AND workspace_id = $2"
        rows = pool.fetch(unscoped, "t1", "ws1", ["proj-mine"], "invoice", "%invoice%", 20)
        self.assertIn(
            "task-secret", [r["id"] for r in rows],
            "the fake pool ignores the SQL it is given -- it would pass a broken query",
        )

    async def test_an_empty_project_scope_searches_nothing_and_touches_no_pool(self):
        pool = _ScopeAwarePool(TASK_ROWS, DOCUMENT_ROWS)
        with _PoolHarness(pool):
            results = await _service().search_workspace(
                tenant_id="t1", workspace_id="ws1", project_ids=[], query="invoice",
            )
        self.assertEqual(results, [])
        self.assertEqual(
            pool.calls, [],
            "'this caller may see no project' must not become a database read",
        )

    async def test_tenant_and_workspace_are_bound_on_every_query(self):
        pool = _ScopeAwarePool(TASK_ROWS, DOCUMENT_ROWS)
        with _PoolHarness(pool):
            await _service().search_workspace(
                tenant_id="t1", workspace_id="ws1",
                project_ids=["proj-mine"], query="invoice",
            )
        for query, args in pool.calls:
            self.assertIn("tenant_id = $1", query)
            self.assertIn("workspace_id = $2", query)
            self.assertEqual(args[0], "t1")
            self.assertEqual(args[1], "ws1")

    async def test_project_ids_has_no_default(self):
        """A scope argument with a default is a loaded gun (CLAUDE.md).
        Forgetting it must be a TypeError, never an unscoped search."""
        import inspect

        signature = inspect.signature(_service().search_workspace)
        parameter = signature.parameters["project_ids"]
        self.assertIs(
            parameter.default, inspect.Parameter.empty,
            "search_workspace(project_ids=...) grew a default -- a forgotten "
            "scope must raise, never silently widen.",
        )


class EmptyQueryTests(unittest.IsolatedAsyncioTestCase):
    """An empty box returns NOTHING, never everything."""

    async def test_empty_query_returns_empty_without_touching_the_database(self):
        pool = _ScopeAwarePool(TASK_ROWS, DOCUMENT_ROWS)
        for raw in ("", "   ", "\t\n", None):
            with self.subTest(query=raw):
                with _PoolHarness(pool):
                    results = await _service().search_workspace(
                        tenant_id="t1", workspace_id="ws1",
                        project_ids=["proj-mine"], query=raw,
                    )
                self.assertEqual(results, [])
        self.assertEqual(pool.calls, [], "an empty query must not reach SQL")

    async def test_the_route_answers_an_empty_query_as_a_success(self):
        """Idle is not an error and not a zero-result search."""
        payload = await _call_route(q="   ", visible={"proj-mine"})
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["results"], [])
        self.assertEqual(payload["query"], "")
        self.assertNotIn("error", payload)

    def test_normalize_query_collapses_whitespace_but_keeps_content(self):
        normalize = _service().normalize_query
        self.assertEqual(normalize("  invoice   email "), "invoice email")
        self.assertEqual(normalize(""), "")
        self.assertEqual(normalize(None), "")


class OutcomeHonestyTests(unittest.IsolatedAsyncioTestCase):
    """"No results" and "couldn't search" are different facts."""

    async def test_an_unreachable_database_raises_instead_of_returning_empty(self):
        from server_modules import control_plane_repository as cp

        async def no_pool():
            return None

        with patch.object(cp, "ensure_control_plane_schema", no_pool):
            with self.assertRaises(_service().WorkspaceSearchUnavailable):
                await _service().search_workspace(
                    tenant_id="t1", workspace_id="ws1",
                    project_ids=["proj-mine"], query="invoice",
                )

    async def test_the_route_reports_unavailable_as_a_failure_not_as_no_results(self):
        payload = await _call_route(
            q="invoice", visible={"proj-mine"},
            search_side_effect=_service().WorkspaceSearchUnavailable("down"),
        )
        self.assertFalse(payload["ok"], "an unrunnable search reported ok:true")
        self.assertTrue(str(payload.get("error") or "").strip())
        self.assertEqual(payload["results"], [])

    async def test_a_genuine_zero_result_search_is_a_success(self):
        payload = await _call_route(q="nothingmatchesthis", visible={"proj-mine"})
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["results"], [])
        self.assertIsNone(payload.get("error"))

    async def test_an_unexpected_failure_is_reported_not_swallowed(self):
        payload = await _call_route(
            q="invoice", visible={"proj-mine"},
            search_side_effect=RuntimeError("boom"),
        )
        self.assertFalse(payload["ok"])
        self.assertIn("boom", str(payload.get("error")))
        self.assertEqual(payload["results"], [])


class ResultShapeTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_hit_carries_everything_a_row_needs_to_render(self):
        pool = _ScopeAwarePool(TASK_ROWS, DOCUMENT_ROWS)
        with _PoolHarness(pool):
            results = await _service().search_workspace(
                tenant_id="t1", workspace_id="ws1",
                project_ids=["proj-mine"], query="invoice",
            )
        task = next(hit for hit in results if hit["kind"] == "task")
        document = next(hit for hit in results if hit["kind"] == "document")

        for hit in (task, document):
            for field in ("id", "kind", "title", "snippet", "project_id"):
                self.assertIn(field, hit)
            self.assertEqual(hit["project_id"], "proj-mine")

        self.assertEqual(task["display_id"], "GEN-12", "the task's quotable identifier")
        self.assertEqual(document["path"], "specs/invoice.md")
        self.assertIsNone(
            document["display_id"], "a document has no per-project sequence",
        )

    def test_display_id_has_no_uuid_fallback(self):
        display = _service()._display_id
        self.assertEqual(display("GEN", 12), "GEN-12")
        self.assertIsNone(display("", 12), "no key means no identifier, not a guess")
        self.assertIsNone(display("GEN", None))
        self.assertIsNone(display("GEN", 0))

    def test_snippet_windows_around_the_match(self):
        build = _service().build_snippet
        body = ("lead " * 40) + "the INVOICE lives here" + (" tail" * 40)
        snippet = build(body, "invoice")
        self.assertIn("INVOICE", snippet, "the snippet must contain what was searched for")
        self.assertLessEqual(len(snippet), _service().SNIPPET_CHARS + 2)
        self.assertEqual(build("", "invoice"), "")
        self.assertEqual(build(None, "invoice"), "")

    def test_ilike_wildcards_typed_by_a_person_are_escaped(self):
        """An unescaped `%` becomes "match every row" -- the everything
        result an empty query is already refused for."""
        pattern = _service()._ilike_pattern("100% _ok_")
        self.assertTrue(pattern.startswith("%") and pattern.endswith("%"))
        inner = pattern[1:-1]
        self.assertNotIn("100%", inner, "a literal % survived unescaped")
        self.assertIn(r"100\%", inner)
        self.assertIn(r"\_ok\_", inner)

    def test_limit_is_clamped_and_never_unbounded(self):
        normalize = _service().normalize_limit
        self.assertEqual(normalize(5), 5)
        self.assertEqual(normalize(10_000), _service().MAX_LIMIT)
        self.assertEqual(normalize(0), _service().DEFAULT_LIMIT)
        self.assertEqual(normalize("nonsense"), _service().DEFAULT_LIMIT)


class FullTextFallbackTests(unittest.IsolatedAsyncioTestCase):
    """The ILIKE fallback is a wired code path, not a comment promising one."""

    async def test_a_database_without_websearch_to_tsquery_falls_back_to_ilike(self):
        from server_modules import control_plane_repository as cp

        pool = _ScopeAwarePool(TASK_ROWS, DOCUMENT_ROWS)
        seen: list[str] = []

        async def fake_ensure():
            return pool

        async def fake_rls_fetch(_pool, query, *args, **kwargs):
            seen.append(query)
            if "websearch_to_tsquery" in query:
                raise RuntimeError(
                    'UndefinedFunctionError: function websearch_to_tsquery('
                    'unknown, text) does not exist'
                )
            return pool.fetch(query, *args)

        _service().reset_full_text_capability_probe()
        try:
            with patch.object(cp, "ensure_control_plane_schema", fake_ensure), \
                 patch.object(cp, "rls_fetch", fake_rls_fetch):
                results = await _service().search_workspace(
                    tenant_id="t1", workspace_id="ws1",
                    project_ids=["proj-mine"], query="invoice",
                )
        finally:
            _service().reset_full_text_capability_probe()

        self.assertTrue(results, "the fallback returned nothing -- it is not wired")
        self.assertIn("task-visible", [hit["id"] for hit in results])
        self.assertTrue(
            any("websearch_to_tsquery" in q for q in seen),
            "full text was never attempted",
        )
        self.assertTrue(
            any("websearch_to_tsquery" not in q and "ILIKE" in q for q in seen),
            "no ILIKE-only query was ever issued",
        )
        # The capability is latched, so the second kind does not re-probe.
        self.assertEqual(
            sum(1 for q in seen if "websearch_to_tsquery" in q), 1,
            "the missing function was probed more than once",
        )

    async def test_an_unrelated_database_error_is_not_mistaken_for_a_missing_function(self):
        from server_modules import control_plane_repository as cp

        async def fake_ensure():
            return object()

        async def fake_rls_fetch(*_args, **_kwargs):
            raise RuntimeError("connection reset by peer")

        _service().reset_full_text_capability_probe()
        try:
            with patch.object(cp, "ensure_control_plane_schema", fake_ensure), \
                 patch.object(cp, "rls_fetch", fake_rls_fetch):
                with self.assertRaises(RuntimeError) as ctx:
                    await _service().search_workspace(
                        tenant_id="t1", workspace_id="ws1",
                        project_ids=["proj-mine"], query="invoice",
                    )
            self.assertIn("connection reset", str(ctx.exception))
        finally:
            _service().reset_full_text_capability_probe()


# ── Route-level: the ACL actually reaches SQL ─────────────────────────────

async def _call_route(*, q, visible, search_side_effect=None, role_ok=True):
    """Drive `routes_search.workspace_search` with auth and the project ACL
    patched, so the assertion is about THIS route's plumbing."""
    from server_modules import auth as auth_module
    from server_modules import routes_fleet

    routes = _routes()
    service = _service()
    pool = _ScopeAwarePool(TASK_ROWS, DOCUMENT_ROWS)

    async def fake_visible(_user, _ws, _tenant):
        return visible

    async def fake_tenant(_ws):
        return "t1"

    real_search = service.search_workspace

    async def maybe_failing_search(**kwargs):
        if search_side_effect is not None:
            raise search_side_effect
        return await real_search(**kwargs)

    with patch.object(auth_module, "enforce_workspace_access", lambda *a, **k: "ws1"), \
         patch.object(routes_fleet, "_visible_project_ids", fake_visible), \
         patch.object(routes, "_resolve_tenant", fake_tenant), \
         patch.object(service, "search_workspace", maybe_failing_search), \
         _PoolHarness(pool):
        payload = await routes.workspace_search(
            request=None, workspace_id="ws1", q=q, limit=20,
            current_user={"user_id": "u1"},
        )
    payload["_pool"] = pool
    return payload


class RouteScopingTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_members_acl_reaches_sql(self):
        payload = await _call_route(q="invoice", visible={"proj-mine"})
        self.assertTrue(payload["ok"])
        ids = [hit["id"] for hit in payload["results"]]
        self.assertIn("task-visible", ids)
        self.assertNotIn("task-secret", ids)
        self.assertNotIn("doc-secret", ids)
        self.assertNotIn("proj-theirs", payload["_pool"].bound_arguments)

    async def test_an_owner_is_expanded_to_a_concrete_project_list_not_an_unscoped_read(self):
        """`_visible_project_ids` answers None for an owner. That must NOT
        become "no filter" -- owner and member take the same predicate."""
        from server_modules import projects_repository as projects

        async def fake_list_projects(**_kwargs):
            return [{"id": "proj-mine"}, {"id": "proj-theirs"}]

        with patch.object(projects, "list_projects", fake_list_projects):
            payload = await _call_route(q="invoice", visible=None)

        self.assertTrue(payload["ok"])
        for query, args in payload["_pool"].calls:
            self.assertIn(
                "ANY($3::text[])", query,
                "an owner search ran with no project predicate at all",
            )
            self.assertEqual(sorted(args[2]), ["proj-mine", "proj-theirs"])
        # An owner genuinely sees both projects -- proving the expansion is a
        # real scope, not a silent narrowing.
        self.assertIn("task-secret", [hit["id"] for hit in payload["results"]])

    async def test_a_member_of_no_project_searches_nothing(self):
        payload = await _call_route(q="invoice", visible=set())
        self.assertTrue(payload["ok"], "no projects is not an error")
        self.assertEqual(payload["results"], [])
        self.assertEqual(payload["_pool"].calls, [])

    async def test_counts_are_reported_per_kind(self):
        payload = await _call_route(q="invoice", visible={"proj-mine"})
        self.assertEqual(payload["counts"]["tasks"], 1)
        self.assertEqual(payload["counts"]["documents"], 1)

    async def test_the_route_gates_on_workspace_access(self):
        """`enforce_workspace_access` raising must not be swallowed into an
        `ok: false` body -- authorization is not a business-logic outcome."""
        from fastapi import HTTPException

        from server_modules import auth as auth_module

        def deny(*_args, **_kwargs):
            raise HTTPException(status_code=403, detail="nope")

        routes = _routes()
        with patch.object(auth_module, "enforce_workspace_access", deny):
            with self.assertRaises(HTTPException):
                await routes.workspace_search(
                    request=None, workspace_id="ws1", q="invoice", limit=20,
                    current_user={"user_id": "u1"},
                )


# ── Source scan: the banned fail-open idiom ───────────────────────────────

_COLUMN_ALTERNATION = r"tenant_id|workspace_id|project_id"

_FAIL_OPEN_PATTERNS = (
    # ($1 = '' OR tenant_id = $1)
    re.compile(
        r"\(\s*(?:\$\d+|\?)(?:::\w+)?\s*=\s*''\s+OR\s+(?:\w+\.)?"
        rf"({_COLUMN_ALTERNATION})\b",
        re.IGNORECASE,
    ),
    # ($3::text IS NULL OR project_id = $3)
    re.compile(
        r"\(\s*(?:\$\d+|\?)(?:::[\w\[\]]+)?\s+IS\s+NULL\s+OR\s+(?:\w+\.)?"
        rf"({_COLUMN_ALTERNATION})\b",
        re.IGNORECASE,
    ),
    # (CARDINALITY($3::text[]) = 0 OR project_id = ANY($3::text[]))
    re.compile(
        r"CARDINALITY\(\s*\$\d+::[\w\[\]]+\s*\)\s*=\s*0\s+OR\s+(?:\w+\.)?"
        rf"({_COLUMN_ALTERNATION})\b",
        re.IGNORECASE,
    ),
)


def _sql_bearing_strings(path: Path) -> str:
    """Every string literal in the module EXCEPT docstrings.

    Docstrings are excluded on purpose, and it is not cosmetic: this
    feature's own module docstrings QUOTE the banned idiom in order to
    forbid it, so a naive text scan would flag the prose that exists to
    prevent the bug -- the tripwire tripping on its own warning sign
    (CLAUDE.md records the same trap for the CSS drift tests). Working from
    the AST also means a `#` comment can never be mistaken for SQL.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None) or []
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            docstrings.add(id(body[0].value))

    # `ast.walk` descends INTO f-strings, so the literal parts of a
    # JoinedStr already arrive here as ordinary Constant nodes. Handling
    # JoinedStr separately as well counts every f-string fragment twice --
    # which is exactly what the positive-presence assertion below caught
    # when this extractor was first written.
    chunks: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                chunks.append(node.value)
    return "\n".join(chunks)


_SEARCH_SOURCES = (
    _SERVER_MODULES / "workspace_search_service.py",
    _SERVER_MODULES / "routes_search.py",
)


class FailOpenScopeFilterDriftTests(unittest.TestCase):
    def test_no_fail_open_scope_predicate_anywhere_in_the_search_surface(self):
        offenders = []
        for path in _SEARCH_SOURCES:
            sql = _sql_bearing_strings(path)
            for pattern in _FAIL_OPEN_PATTERNS:
                for match in pattern.finditer(sql):
                    offenders.append((path.name, match.group(0)))
        self.assertEqual(
            offenders, [],
            "Fail-open tenant/workspace/project filter in the search surface. "
            "`WHERE ($1 = '' OR project_id = $1)` returns EVERY project's rows "
            "when the argument is forgotten. Bind the scope unconditionally "
            "(`= ANY($n::text[])`), as workspace_search_service already does.",
        )

    def test_the_scanner_catches_a_freshly_introduced_offender(self):
        """Proof the scan is not vacuous."""
        offending = "SELECT * FROM project_tasks WHERE ($1 = '' OR tenant_id = $1)"
        hits = [p for p in _FAIL_OPEN_PATTERNS if p.search(offending)]
        self.assertTrue(hits, "the scanner failed to flag a textbook fail-open filter")

    def test_the_scanner_actually_reads_the_real_sql(self):
        """Canary: an extractor that silently returns nothing would make
        every assertion above pass while enforcing nothing."""
        for path in _SEARCH_SOURCES:
            sql = _sql_bearing_strings(path)
            self.assertTrue(sql.strip(), f"extracted no strings at all from {path.name}")
        service_sql = _sql_bearing_strings(_SEARCH_SOURCES[0])
        self.assertIn("FROM project_tasks", service_sql)
        self.assertIn("FROM project_documents", service_sql)

    def test_the_scanner_ignores_the_docstring_that_quotes_the_banned_idiom(self):
        """The module docstring names `WHERE ($1 = '' OR project_id = $1)` in
        order to forbid it. If that tripped the scan, the fix would be to
        delete the warning -- exactly backwards."""
        raw = _SEARCH_SOURCES[0].read_text(encoding="utf-8")
        self.assertIn("$1 = '' OR project_id = $1", raw, "the warning was removed")
        extracted = _sql_bearing_strings(_SEARCH_SOURCES[0])
        self.assertNotIn("$1 = '' OR project_id = $1", extracted)

    def test_the_scope_predicate_is_positively_present(self):
        """"No fail-open predicate" is also satisfied by a query with NO
        scope predicate at all, which is worse. Assert the real one exists."""
        sql = _sql_bearing_strings(_SEARCH_SOURCES[0])
        self.assertEqual(
            sql.count("project_id = ANY($3::text[])"), 2,
            "both the task and the document query must bind the project scope",
        )
        self.assertEqual(sql.count("tenant_id = $1"), 2)
        self.assertEqual(sql.count("workspace_id = $2"), 2)


class EveryBoundParameterIsReferencedTests(unittest.TestCase):
    """A prepared statement that binds a parameter it never mentions cannot
    have that parameter's type inferred, and asyncpg refuses to prepare it:

        asyncpg.exceptions.IndeterminateDatatypeError:
            could not determine data type of parameter $4

    This is a REGRESSION TEST for a real bug in this module's first version.
    `_rank_sql` returned the constant `0` on the ILIKE-only branch, so the
    fallback query referenced $1, $2, $3, $5 and $6 but never $4 -- valid
    SQL, green against every mocked pool in this file, and guaranteed to
    raise on the first real call against the one kind of database that
    NEEDS the fallback. It was caught by executing the generated SQL against
    a real PostgreSQL 17.6, not by reading it.

    A behavioural test with a fake pool structurally cannot catch this: a
    fake never type-checks parameters. The check has to be static, so it
    lives here and runs in the ordinary suite with no database at all.
    """

    def _query_forms(self):
        service = _service()
        for full_text in (True, False):
            for name, build in (("tasks", service._task_query), ("documents", service._document_query)):
                yield f"{name}/{'full_text' if full_text else 'ilike_only'}", build(full_text=full_text)

    def test_every_parameter_from_one_to_six_appears_in_every_query_form(self):
        # search_workspace binds exactly six arguments, positionally.
        expected = [f"${n}" for n in range(1, 7)]
        for label, sql in self._query_forms():
            for placeholder in expected:
                with self.subTest(query=label, parameter=placeholder):
                    self.assertIn(
                        placeholder, sql,
                        f"{label} binds {placeholder} but never references it -- "
                        "asyncpg cannot infer its type and will refuse to "
                        "prepare the statement at runtime.",
                    )

    def test_no_query_form_references_a_parameter_that_is_never_bound(self):
        """The other direction: a `$7` nobody passes raises just as loudly."""
        for label, sql in self._query_forms():
            over_bound = sorted({int(n) for n in re.findall(r"\$(\d+)", sql) if int(n) > 6})
            self.assertEqual(
                over_bound, [],
                f"{label} references {over_bound}, but search_workspace binds only six arguments.",
            )

    def test_the_check_would_fail_on_the_original_bug(self):
        """Canary: rebuild the exact pre-fix shape and require a miss."""
        broken = (
            "SELECT t.id, 0 AS search_rank FROM project_tasks t "
            "WHERE t.tenant_id = $1 AND t.workspace_id = $2 "
            "AND t.project_id = ANY($3::text[]) AND t.title ILIKE $5 LIMIT $6"
        )
        self.assertNotIn("$4", broken, "the canary no longer models the original bug")


class WiringTests(unittest.TestCase):
    """"Built, tested, and never wired" is the most common defect here.
    A search service reachable from no URL is worth nothing."""

    def test_the_router_is_registered_in_server_py(self):
        source = (_REPO_ROOT / "server.py").read_text(encoding="utf-8")
        self.assertIn("from server_modules.routes_search import router as search_router", source)
        self.assertIn("app.include_router(search_router", source)

    def test_the_route_path_is_the_one_the_clients_call(self):
        routes = _routes()
        paths = {
            route.path
            for route in routes.router.routes
            if getattr(route, "path", None)
        }
        self.assertIn("/api/w/{workspace_id}/search", paths)

    def test_the_route_is_not_a_redirect_source(self):
        """A next.config redirect resolves AHEAD of the router and can make
        a live route unreachable with no code saying so (CLAUDE.md)."""
        config = _REPO_ROOT / "frontend" / "next.config.ts"
        if not config.exists():
            self.skipTest("frontend/next.config.ts not present")
        self.assertNotIn("/search'", config.read_text(encoding="utf-8"))

    def test_the_route_enforces_workspace_access_at_viewer(self):
        """Structural: the gate must be present in the source, so deleting
        it fails here rather than only in a mocked behavioural path."""
        source = (_SERVER_MODULES / "routes_search.py").read_text(encoding="utf-8")
        self.assertIn("enforce_workspace_access", source)
        self.assertIn('minimum_role="viewer"', source)

    def test_the_route_uses_the_shared_acl_rather_than_a_second_copy(self):
        """One opinion about who may see which project. A same-language
        second implementation of an ACL is the drift CLAUDE.md names."""
        source = (_SERVER_MODULES / "routes_search.py").read_text(encoding="utf-8")
        self.assertIn("routes_fleet._visible_project_ids", source)
        self.assertNotIn(
            "list_member_project_ids", source,
            "routes_search re-derives the project ACL instead of importing it",
        )


if __name__ == "__main__":
    unittest.main()
