"""Workspace search: find a task or a document by what it SAYS.

Until this module existed there was no search anywhere in the product. A
person who filed "Rewrite the onboarding email" three weeks ago could reach
it only by remembering which project it lived in and scrolling. ⌘K jumps to
projects and agents BY NAME and has never looked inside a task title, let
alone a description or a document body.

WHAT THIS IS NOT: a retrieval pipeline. No embeddings, no vector store, no
chunking -- the same already-made decision `project_documents_repository`'s
own docstring records (Anthropic removed RAG from Claude Code in favour of
agentic search; this repo deleted `knowledge_rag_service.py` for the same
reason). This is a human pressing ⌘K and typing three words. It is one
Postgres query per kind and nothing is ranked by a model.


SCOPE IS AN ARGUMENT, AND IT HAS NO DEFAULT
───────────────────────────────────────────
`project_ids` is a REQUIRED keyword on `search_workspace` with no default
value, so an unscoped search is something a caller has to TYPE `[]` for and
every such site is greppable. This is the posture CLAUDE.md records for
`run_state_repository` after the fail-open family:

    WHERE ($1 = '' OR project_id = $1)      ← BANNED. A forgotten argument
                                             returns every tenant's rows.

Nothing in this module builds a scope predicate conditionally. Tenant,
workspace and project are bound on EVERY query, unconditionally, and the
project list is bound as `= ANY($3::text[])`. There is deliberately no
"owner sees everything" branch down here: the caller resolves the concrete
set of project ids the person may see (`routes_search` does it exactly the
way `routes_fleet._visible_project_ids` does) and passes it. One code path,
so there is no owner-shaped branch that can forget the filter.

An EMPTY `project_ids` is a REAL ANSWER -- "this caller may see no
project" -- and returns nothing rather than everything. A task whose
`project_id` is NULL (the column is nullable) matches no project id and is
therefore invisible here, which is the same fail-closed answer
`_visible_project_ids` already gives (`"" in visible_ids` is False).


"NO RESULTS" AND "COULDN'T SEARCH" ARE DIFFERENT FACTS
──────────────────────────────────────────────────────
CLAUDE.md's outcome-honesty law, at the one seam where collapsing it is
most tempting: an empty list is the natural Python value for both "nothing
matched" and "the database was unreachable", and a search box that says
"No results for 'invoice'" when it never ran the query teaches a person
that their task is gone.

So an unreachable control plane RAISES `WorkspaceSearchUnavailable` rather
than returning `[]`. Every other failure propagates as itself. Only a query
that actually ran and matched nothing produces an empty list.


FULL TEXT WHERE AVAILABLE, SUBSTRING ALWAYS
───────────────────────────────────────────
Both predicates run, OR'd, in ONE query -- this is not "full text, and if
that finds nothing try ILIKE" (which would need two round trips to learn
the first one failed). They answer different questions and a search box
needs both:

    to_tsvector/websearch_to_tsquery   word-aware. "shipping invoices"
                                       matches "invoice shipped", handles
                                       quoted phrases and OR/-, never
                                       raises on punctuation the way
                                       to_tsquery does.
    ILIKE '%...%'                      substring. "auth" matches
                                       "authentication", which stemming
                                       does not, and it is what someone
                                       typing three letters expects.

`websearch_to_tsquery` is PostgreSQL 11+. A deployment older than that (or
any environment where the function is missing) is detected FROM THE
DATABASE'S OWN ERROR on first use, latched in `_FULL_TEXT_SUPPORTED`, and
every later query runs ILIKE-only. The fallback is a real wired code path
with its own test, not a comment promising one.

There is NO tsvector column and NO GIN index, deliberately. An expression
index would need a migration, and CLAUDE.md records what happens when a
migration ships ahead of the code that needs it (`add_task_sequence_
numbers.sql` sat half-live for weeks). The predicate is computed at query
time against a workspace's own rows, which is a sequential scan over one
tenant's tasks -- correct at present scale and correct on a database nobody
has migrated. Adding the index later changes this file not at all.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Sequence

from server_modules import control_plane_repository

LOGGER = logging.getLogger(__name__)

# Result kinds. Strings, not an enum, because they cross the wire to two
# clients that both switch on them ('task' | 'document').
KIND_TASK = "task"
KIND_DOCUMENT = "document"

DEFAULT_LIMIT = 20
MAX_LIMIT = 50

# How much text a result row carries. A search hit is a ROW in a list, not a
# preview pane -- enough to recognise the thing, never enough to read it.
SNIPPET_CHARS = 160
_SNIPPET_LEAD = 40

# The text-search configuration. 'english' rather than 'simple' so
# "invoices" finds "invoice"; the ILIKE half covers the words English
# stemming gets wrong.
_TS_CONFIG = "english"

# Tri-state, latched at runtime from the database's own error:
#   None  -- not yet known. Try full text.
#   True  -- websearch_to_tsquery ran. Keep using it.
#   False -- this database does not have it. ILIKE only, forever.
_FULL_TEXT_SUPPORTED: Optional[bool] = None

# The error text Postgres/asyncpg produces for a function that is not there.
# Matched on the FUNCTION NAME plus an undefined-ness marker, never on a
# whole sentence -- CLAUDE.md: "match on stable codes, never on prose", and
# a bare message match here would re-run the fallback on an unrelated error.
_MISSING_FULL_TEXT_MARKERS = ("websearch_to_tsquery", "to_tsvector")
_UNDEFINED_MARKERS = ("does not exist", "undefinedfunction", "unknown function")


class WorkspaceSearchUnavailable(RuntimeError):
    """The search could not be RUN -- not "it ran and found nothing".

    Raised when the control plane is unreachable. The two facts must never
    share a return value: `[]` is reserved for a query that executed.
    """


def reset_full_text_capability_probe() -> None:
    """Testing seam. The capability latch is process-lifetime state; a test
    that proves the fallback must be able to put the probe back."""
    global _FULL_TEXT_SUPPORTED
    _FULL_TEXT_SUPPORTED = None


def normalize_query(raw: Any) -> str:
    """The typed string, trimmed and collapsed. Empty in, empty out.

    An empty query returns NOTHING, never everything -- a search box that
    lists the entire workspace the instant it is focused is not a search
    box. The check lives here (one place) rather than at each call site.
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    return re.sub(r"\s+", " ", text)


def normalize_limit(raw: Any) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    if value < 1:
        return DEFAULT_LIMIT
    return min(value, MAX_LIMIT)


def _ilike_pattern(query: str) -> str:
    r"""`%foo%`, with the caller's own wildcards escaped.

    Load-bearing: an unescaped `%` typed into the box becomes "match every
    row", which is exactly the everything-result `normalize_query` refuses
    to produce for an empty string. `\` is escaped FIRST or it would
    double-escape the escapes this function itself adds.
    """
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def build_snippet(body: Any, query: str, *, limit: int = SNIPPET_CHARS) -> str:
    """A window of `body` around the first case-insensitive hit.

    Computed in PYTHON, not with `ts_headline`: the snippet must be right
    for a substring match too (the ILIKE half of the predicate), and
    ts_headline only knows about lexemes -- it returns the document's opening
    words for a hit it cannot see, which reads as a wrong snippet rather
    than as a missing one. Being pure also makes it directly testable.

    Never returns markup. The clients render it as plain text.
    """
    text = re.sub(r"\s+", " ", str(body or "")).strip()
    if not text:
        return ""
    needle = (query or "").strip().lower()
    start = 0
    if needle:
        found = text.lower().find(needle)
        if found > 0:
            start = max(0, found - _SNIPPET_LEAD)
    window = text[start : start + limit]
    prefix = "…" if start > 0 else ""
    suffix = "…" if start + limit < len(text) else ""
    return f"{prefix}{window.strip()}{suffix}"


def _display_id(task_key: Any, number: Any) -> Optional[str]:
    """"GEN-12", or None.

    NO HEX-SLICE FALLBACK, deliberately. `frontend/.../task-status.tsx`
    falls back to a uuid fragment so a table cell is never blank; a search
    row is different -- an identifier is either something a person can
    recognise and quote, or it should not be shown at all. "task 69D656" is
    a uuid fragment dressed up as an identifier. Same call
    `deep_link_service` already made for the identical value.
    """
    key = str(task_key or "").strip()
    try:
        seq = int(number)
    except (TypeError, ValueError):
        return None
    if not key or seq <= 0:
        return None
    return f"{key}-{seq}"


def _normalized_project_scope(project_ids: Sequence[str]) -> List[str]:
    seen: List[str] = []
    for raw in project_ids or []:
        value = str(raw or "").strip()
        if value and value not in seen:
            seen.append(value)
    return seen


def _looks_like_missing_full_text(exc: BaseException) -> bool:
    blob = f"{type(exc).__name__} {exc}".lower()
    if not any(marker in blob for marker in _MISSING_FULL_TEXT_MARKERS):
        return False
    return any(marker in blob for marker in _UNDEFINED_MARKERS)


# ── SQL ───────────────────────────────────────────────────────────────────
# Both statements bind, unconditionally and in this order:
#   $1 tenant_id   $2 workspace_id   $3 project_ids[]   $4 query   $5 ilike
#   $6 limit
# There is no branch in which a scope parameter is omitted, and no `OR`
# anywhere in the scope predicate -- the only OR is between the two MATCH
# predicates, which are about relevance and never about who may read a row.

_TASK_SEARCHABLE = (
    "coalesce(t.title, '') || ' ' || coalesce(t.description, '')"
)
_DOCUMENT_SEARCHABLE = (
    "coalesce(d.title, '') || ' ' || coalesce(d.path, '') || ' ' || coalesce(d.body, '')"
)


def _match_sql(searchable: str, alias: str, columns: Sequence[str], *, full_text: bool) -> str:
    ilike = " OR ".join(f"{alias}.{column} ILIKE $5 ESCAPE '\\'" for column in columns)
    if not full_text:
        return f"({ilike})"
    return (
        f"(to_tsvector('{_TS_CONFIG}', {searchable}) @@ websearch_to_tsquery('{_TS_CONFIG}', $4)"
        f" OR {ilike})"
    )


def _rank_sql(searchable: str, alias: str, title_column: str, *, full_text: bool) -> str:
    """Relevance, per branch.

    THE ILIKE BRANCH MUST REFERENCE $4, and that is not a stylistic choice:
    a prepared statement in which a parameter appears NOWHERE cannot have
    its type inferred, and asyncpg fails the prepare outright --

        asyncpg.exceptions.IndeterminateDatatypeError:
            could not determine data type of parameter $4

    An earlier version of this function returned the constant `0` here. The
    query was valid SQL, every mocked test passed (a fake pool does not
    type-check parameters), and the fallback would have raised on the first
    real call against every database that needed it -- i.e. the path would
    have been broken precisely where it was the only path. CLAUDE.md: "a
    mock protects a seam, not a path." Caught by running the generated SQL
    against a real PostgreSQL, not by reading it.

    So the fallback ranks for real instead of pretending it cannot: an exact
    title match beats a title substring, which beats a body-only hit. That
    is a better answer than `0` anyway, and it consumes $4 honestly rather
    than with a no-op cast bolted on to satisfy the planner.
    """
    if not full_text:
        return (
            f"CASE WHEN lower({alias}.{title_column}) = lower($4::text) THEN 2"
            f" WHEN {alias}.{title_column} ILIKE $5 ESCAPE '\\' THEN 1"
            f" ELSE 0 END"
        )
    return (
        f"ts_rank(to_tsvector('{_TS_CONFIG}', {searchable}),"
        f" websearch_to_tsquery('{_TS_CONFIG}', $4))"
    )


def _task_query(*, full_text: bool) -> str:
    return f"""
        SELECT t.id,
               t.project_id,
               t.title,
               t.description,
               t.status,
               t.number,
               t.updated_at,
               proj.task_key AS project_task_key,
               {_rank_sql(_TASK_SEARCHABLE, 't', 'title', full_text=full_text)} AS search_rank
        FROM project_tasks t
        LEFT JOIN LATERAL (
            SELECT p.task_key
            FROM projects p
            WHERE p.id = t.project_id
              AND p.tenant_id = t.tenant_id
              AND p.workspace_id = t.workspace_id
        ) proj ON TRUE
        WHERE t.tenant_id = $1
          AND t.workspace_id = $2
          AND t.project_id = ANY($3::text[])
          AND {_match_sql(_TASK_SEARCHABLE, 't', ('title', 'description'), full_text=full_text)}
        ORDER BY search_rank DESC, t.updated_at DESC NULLS LAST, t.id ASC
        LIMIT $6
    """


def _document_query(*, full_text: bool) -> str:
    return f"""
        SELECT d.id,
               d.project_id,
               d.title,
               d.path,
               d.body,
               d.updated_at,
               {_rank_sql(_DOCUMENT_SEARCHABLE, 'd', 'title', full_text=full_text)} AS search_rank
        FROM project_documents d
        WHERE d.tenant_id = $1
          AND d.workspace_id = $2
          AND d.project_id = ANY($3::text[])
          AND {_match_sql(_DOCUMENT_SEARCHABLE, 'd', ('title', 'path', 'body'), full_text=full_text)}
        ORDER BY search_rank DESC, d.updated_at DESC NULLS LAST, d.id ASC
        LIMIT $6
    """


async def _fetch_with_full_text_fallback(
    pool: Any,
    build_query,
    *,
    tenant_id: str,
    workspace_id: str,
    args: Sequence[Any],
) -> List[Any]:
    """Run the full-text form; drop to ILIKE-only if this database has no
    `websearch_to_tsquery`, and remember that for the process."""
    global _FULL_TEXT_SUPPORTED

    if _FULL_TEXT_SUPPORTED is False:
        return list(
            await control_plane_repository.rls_fetch(
                pool, build_query(full_text=False), *args,
                tenant_id=tenant_id, workspace_id=workspace_id,
            )
            or []
        )
    try:
        rows = await control_plane_repository.rls_fetch(
            pool, build_query(full_text=True), *args,
            tenant_id=tenant_id, workspace_id=workspace_id,
        )
    except Exception as exc:  # noqa: BLE001 - re-raised unless it is the one case we handle
        if not _looks_like_missing_full_text(exc):
            raise
        LOGGER.warning(
            "workspace_search: this database has no full-text search function "
            "(%s); falling back to substring matching for the rest of the "
            "process.", exc,
        )
        _FULL_TEXT_SUPPORTED = False
        return list(
            await control_plane_repository.rls_fetch(
                pool, build_query(full_text=False), *args,
                tenant_id=tenant_id, workspace_id=workspace_id,
            )
            or []
        )
    _FULL_TEXT_SUPPORTED = True
    return list(rows or [])


async def search_workspace(
    *,
    tenant_id: str,
    workspace_id: str,
    project_ids: Sequence[str],
    query: str,
    limit: int = DEFAULT_LIMIT,
) -> List[Dict[str, Any]]:
    """Tasks and documents matching `query`, inside `project_ids` and
    nowhere else.

    `project_ids` has NO DEFAULT on purpose -- see the module docstring.
    `limit` is PER KIND, not a total: a workspace with 400 matching tasks
    must still show the document that was searched for, so tasks can never
    crowd documents off the end of one shared budget.

    Raises `WorkspaceSearchUnavailable` when the control plane cannot be
    reached. Returns `[]` only for a query that ran and matched nothing.
    """
    text = normalize_query(query)
    if not text:
        # No pool touched: an empty query is answered without a database.
        return []

    scope = _normalized_project_scope(project_ids)
    if not scope:
        # "This caller may see no project" -- a real answer, and the only
        # safe one. Checked BEFORE the pool, so a forgotten scope is never
        # silently correct just because Postgres happened to be down.
        return []

    resolved_tenant_id = str(tenant_id or "").strip()
    resolved_workspace_id = str(workspace_id or "").strip()

    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        raise WorkspaceSearchUnavailable(
            "Search is unavailable because the workspace database could not be reached."
        )

    per_kind = normalize_limit(limit)
    args = (
        resolved_tenant_id,
        resolved_workspace_id,
        scope,
        text,
        _ilike_pattern(text),
        per_kind,
    )

    task_rows = await _fetch_with_full_text_fallback(
        pool, _task_query,
        tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id, args=args,
    )
    document_rows = await _fetch_with_full_text_fallback(
        pool, _document_query,
        tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id, args=args,
    )

    results: List[Dict[str, Any]] = []
    for row in task_rows:
        hit = _row_to_task_hit(row, text)
        if hit:
            results.append(hit)
    for row in document_rows:
        hit = _row_to_document_hit(row, text)
        if hit:
            results.append(hit)
    return results


def _row_to_task_hit(row: Any, query: str) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    r = dict(row)
    identifier = str(r.get("id") or "").strip()
    if not identifier:
        return None
    return {
        "id": identifier,
        "kind": KIND_TASK,
        "title": str(r.get("title") or "").strip() or "Untitled task",
        "snippet": build_snippet(r.get("description"), query),
        "project_id": str(r.get("project_id") or "").strip() or None,
        "display_id": _display_id(r.get("project_task_key"), r.get("number")),
        "status": str(r.get("status") or "").strip() or None,
        "path": None,
        "updated_at": str(r.get("updated_at") or "") or None,
    }


def _row_to_document_hit(row: Any, query: str) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    r = dict(row)
    identifier = str(r.get("id") or "").strip()
    if not identifier:
        return None
    return {
        "id": identifier,
        "kind": KIND_DOCUMENT,
        "title": str(r.get("title") or "").strip() or "Untitled document",
        "snippet": build_snippet(r.get("body"), query),
        "project_id": str(r.get("project_id") or "").strip() or None,
        # A document has no per-project sequence the way a task does; its
        # `path` is the human-quotable identifier and rides its own field.
        "display_id": None,
        "status": None,
        "path": str(r.get("path") or "").strip() or None,
        "updated_at": str(r.get("updated_at") or "") or None,
    }
