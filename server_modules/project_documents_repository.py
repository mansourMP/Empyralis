"""Project documents: the storage layer for a project's owned markdown
knowledge -- the compounding asset behind the "owned-context layer for a
team, with execution attached" positioning (CLAUDE.md).

NO RAG, NO EMBEDDINGS, NO VECTOR STORE. This is a deliberate, already-made
decision, not one this module relitigates: Anthropic's own Claude Code team
removed RAG in favour of agentic search ("Early versions of Claude Code
used RAG + a local vector db, but we found pretty quickly that agentic
search generally works better" -- Boris Cherny), and Anthropic's Managed
Agents memory stores are plain text files an agent reads with normal file
tools. An agent finds a document here by LISTING a project's set (this
module's own list_documents) and reading the ones it needs, the same way it
would `ls` and `cat` a folder -- never through a retrieval pipeline. Nothing
in this file ranks, embeds, or scores document content.

SCOPE: project, not per-agent. Every agent and every human member of a
project shares the same document set -- CLAUDE.md's "Projects hold members
directly," the same collaboration boundary project_tasks_service.py and
projects_repository.py already enforce for tasks and membership.

THIS IS THE RECOMMENDED HOME FOR "COMPANY CONTEXT" (feat/agent-memory-
shared-vs-private, 2026-08-12). When the shared memory pool
(memory_service.py/agent_memory.py) needs a durable write-up of how the
COMPANY/PROJECT operates -- as opposed to a running index of small facts --
a project document (e.g. one titled "Company Context" or "How We Operate")
is the right surface, not a new memory table. It is already project-scoped,
already has real revision history (project_document_revisions, above), and
is already reachable by every agent through the document__* tools
(skills_service.py) any project member's turn can call -- the exact
"every project member benefits" property CLAUDE.md's sharing model asks
for. Deliberately not folded into memory_service.py's MEMORY.md/topic-file
system: that system is index-first and line-per-entry by design (see
memory_service.py's own "Index-first discipline" section), suited to an
agent's own accumulated facts, not a curated prose document a human is
meant to read and edit. No code change was needed to make this true --
this note exists so the next person building a "company context" feature
finds this table first instead of inventing a parallel store. See
agent_private_memory_repository.py for the OTHER half of the split (the
per-person private layer), which is a genuinely new concept and could not
reuse this table -- see that module's own docstring for why.

STORAGE: the markdown body lives IN POSTGRES (the `body` column), not on
disk. Two existing on-disk patterns were weighed and rejected for this
table specifically:
  - agent_memory.py stores memory as files under .orion-stack/memory/ on
    local disk -- an audit flagged this as not durable across machines and
    not in the database of record.
  - uploaded knowledge files live only on disk under
    .orion-stack/workspace/... (workspace_context.workspace_knowledge_dir)
    -- the same durability gap for the part that actually matters (the
    content itself).
A project document is a small markdown text blob, not a binary asset --
Postgres already gives every other control-plane row here one database of
record, RLS-scoped isolation, and survival of a machine change with no
separate backup/sync story. There is no reason a document should be the one
control-plane entity that regresses to a local file.

SCHEMA: FLAT, NOT HIERARCHICAL. No folder/path tree -- a project's documents
are addressed by a per-project-unique `path` (`specs/api/auth.md`) --
GitHub-shaped, with folders INFERRED from the slashes and no folder object
anywhere. See migrations/add_document_paths.sql.
"A surface must earn its place" (CLAUDE.md) applies to schema too: nothing
today asks for nested folders, and a flat list is the smaller thing that
can always grow a `parent_document_id` self-reference later, the same way
project_tasks added one-level sub-tasks onto an already-shipped flat table
(migrations/add_task_parent.sql) rather than paying for a tree model
upfront.

REVISIONS + PATCH-NATIVE EDITS (feat/document-mcp-tools-and-revisions): built
on the seam this module's own history predicted -- `id` is stable and never
reused or recreated by an edit (update_document is a plain in-place UPDATE,
never a delete+reinsert), so `project_document_revisions(document_id
REFERENCES project_documents(id) ...)` hangs a snapshot off every write
without touching the live row's identity. The founder's own words for the
edit shape this implements: "to upgrade one line or one word or one
sentence of this specific document, agent must not rewrite the entire
document... just like git -- write a line and push it." Two things follow:

  1. ``edit_document_by_replace`` (backing ``empyralis_edit_document``) is
     the PRIMARY way to change a document: an old_string/new_string patch
     that must match the current body EXACTLY ONCE -- zero matches and
     multiple matches both fail loudly with NO mutation, the identical
     contract skills_service.py's document__edit already gives platform
     agents (see that function's own docstring for why "exactly once" is
     the whole guarantee). ``update_document`` (whole title/body replace)
     stays available for a genuine full rewrite, but is the FALLBACK, not
     the default path a caller reaches for.

  2. Every write records a snapshot AND a human-readable unified diff
     (`difflib.unified_diff`, computed server-side against the row's PRE-
     write state) in the SAME revision row -- "this line changed", not
     "here is the whole document again" (the founder's own framing).
     Snapshots are kept alongside the diff, not diff-only: reconstructing
     revision N by replaying N sequential patches is O(N) per read and one
     corrupt/missing patch breaks every later reconstruction, while these
     documents are small markdown text (this module's own "not on disk,
     not a binary asset" framing) -- one extra blob per edit is cheap, and
     every revision stays independently readable even if the diff column
     is ever wrong. The diff is the primary, human-scannable artifact;
     the snapshot is the safety net under it, not the point.

`create_document` and `update_document` each append one revision (via the
private `_record_document_revision`) after their own write lands --
git-log style: a revision is the document AS IT BECAME after that edit, so
"list this document's revisions" reads top-to-bottom the same way a commit
log does, and the live row is always identical to its own latest revision.
`changed_by_type` reuses the EXACT vocabulary `project_tasks_service.
add_task_comment`'s `author_type` already established (human / agent /
external_agent / system) -- not a second vocabulary for the same concept.
See migrations/add_project_document_revisions.sql for the full schema
rationale, including the fail-open write posture (a revisions-table outage
must never corrupt or block the document write that already landed).

RLS. `project_documents` is RLS-FORCEd on (tenant_id, workspace_id) from
the migration that creates it (migrations/add_project_documents.sql +
migrations/enable_rls.sql) -- every function below routes through
control_plane_repository.rls_fetch/rls_fetchrow/rls_execute, never a bare
pool call, matching task_notifications' "written correctly from day one,
no legacy call site to sequence around" posture rather than project_tasks'
original retrofit.

Postgres-first, following projects_repository.py's own convention: when
Postgres is unavailable these functions return empty/None rather than
falling back to SQLite (documents are a control-plane concept, same era as
Projects and project_memberships).
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import re
import uuid
from typing import Any, Dict, List, Optional, Sequence

from server_modules import control_plane_repository

LOGGER = logging.getLogger(__name__)

_DOCUMENT_COLUMNS = (
    "id, tenant_id, workspace_id, project_id, title, path, body, "
    "created_by, updated_by, metadata, created_at, updated_at"
)


# ── Stale-write precondition: the one thing that makes concurrent human +
# agent editing on the same document safe (MAN-115's last unbuilt piece).
#
# THE BUG THIS EXISTS FOR, stated plainly, because it is the worst class of
# defect this product can have (CLAUDE.md: documents are the durable asset):
#
#   t0  a person opens a document.  The browser snapshots title+body into a
#       draft and NEVER refetches (documents-data.ts's own file header used
#       to assert documents are "not mutated out from under the reader by an
#       agent" -- which was false the day document__edit shipped).
#   t1  an agent lands a real edit through document__edit / empyralis_edit_
#       document.  The row now holds the agent's paragraph.
#   t2  the person types one character.  900ms later autosave PATCHes the
#       WHOLE BODY from the t0 draft.  The agent's paragraph is gone, and
#       the revision history records a clean row saying the HUMAN wrote the
#       reversion -- so the loss is invisible even in the audit trail.
#
# The token is a CONTENT HASH of the document state the writer based its
# write on, not `updated_at` and not the revision counter:
#   - `updated_at` is a clock value, so an agent write that produced BYTE-
#     IDENTICAL content still moves it -- a conflict the person would be
#     asked to resolve where genuinely nothing was lost.
#   - `revision_number` lives on project_document_revisions, whose writes
#     are DELIBERATELY fail-open (see _record_document_revision): a body can
#     change while the counter does not, and a precondition that passes on a
#     stale base is worse than none at all.
#   - a content hash matches the founder's own "documents should work like
#     git" framing exactly: identical content is not a conflict, and git
#     refuses the non-fast-forward push that this API used to accept.
#
# Title and body are BOTH covered by the one token. The human PATCH writes
# both from a single draft snapshot, so a body-only hash would let a stale
# save silently revert a rename. The cost is that a concurrent rename makes
# an agent's body-only edit conflict; that is a cheap re-read for the agent
# (its tools already tell it to re-read on failure) against a silent loss
# for the person, which is not a close call.
#
# Hashing each field separately and hashing the CONCATENATED digests is not
# decoration: Postgres text cannot contain a NUL byte, so there is no
# separator that a title is guaranteed not to contain, and `title || body`
# would let ("ab", "c") and ("a", "bc") collide into one token.
_DOCUMENT_STATE_SHA256_SQL = (
    "encode(sha256(convert_to("
    "encode(sha256(convert_to(COALESCE(title, ''), 'UTF8')), 'hex') || "
    "encode(sha256(convert_to(COALESCE(body, ''), 'UTF8')), 'hex') || "
    "encode(sha256(convert_to(COALESCE(path, ''), 'UTF8')), 'hex')"
    ", 'UTF8')), 'hex')"
)


def document_state_sha256(title: Any, body: Any, path: Any = "") -> str:
    """The precondition token for one document state -- the Python mirror of
    `_DOCUMENT_STATE_SHA256_SQL` above, which is the AUTHORITY (the actual
    compare-and-swap runs inside update_document's UPDATE, in the database,
    where it is atomic; a Python-side comparison would race the very write
    it is guarding). Both must agree byte for byte, so they are proved to
    agree by two independent checks rather than by one file reading itself:
    test_document_stale_write_precondition.py pins known-answer vectors
    against this function, and its real-Postgres class re-derives the same
    token straight out of the database.

    Order of operations: sha256(hex(sha256(title)) || hex(sha256(body)) ||
    hex(sha256(path))). See the block comment above for why the digests are
    concatenated rather than the raw text.

    PATH IS COVERED for exactly the reason title is. The token exists so a
    writer that based its write on a stale read is refused; a document's
    path is now editable (a move/rename -- git mv), and the human PATCH
    writes title, body and path from ONE draft snapshot. A token that
    omitted path would let a stale save silently revert somebody else's
    move, which is the same class of silent loss this precondition was
    built to stop. The cost is that a concurrent move makes an agent's
    body-only edit conflict -- a cheap re-read for the agent (its tools
    already tell it to re-read on failure) against a silent loss for the
    person, which is not a close call. `path` defaults to "" so the two
    pinned known-answer vectors that predate it still describe a real
    state (a document whose path is empty), never a special case."""
    title_digest = hashlib.sha256(str(title or "").encode("utf-8")).hexdigest()
    body_digest = hashlib.sha256(str(body or "").encode("utf-8")).hexdigest()
    path_digest = hashlib.sha256(str(path or "").encode("utf-8")).hexdigest()
    return hashlib.sha256((title_digest + body_digest + path_digest).encode("utf-8")).hexdigest()


class DocumentPreconditionFailed(RuntimeError):
    """The document changed since the writer read it -- its write was
    REFUSED and NOTHING was written.

    A distinct exception rather than a `None` return, because "not found",
    "somebody else changed it" and "saved" are three different facts and
    must never share one channel (CLAUDE.md's own law, escalated to a
    standing rule after it hit production three times in one night).
    update_document keeps returning None for not-found; this is raised, and
    only this, for a refused stale write.

    Carries the CURRENT state (`current_document`, body included, plus its
    own `current_sha256`) so a caller can show the person what is actually
    on the server instead of only telling them that something is -- a
    conflict message with no way to see the other version is not a choice,
    it is a dead end."""

    def __init__(
        self,
        message: str,
        *,
        current_document: Optional[Dict[str, Any]] = None,
        expected_sha256: str = "",
    ) -> None:
        super().__init__(message)
        self.current_document = current_document
        self.expected_sha256 = expected_sha256
        self.current_sha256 = str((current_document or {}).get("state_sha256") or "")


def _new_document_id() -> str:
    return f"doc_{uuid.uuid4().hex[:16]}"


DEFAULT_DOCUMENT_EXTENSION = ".md"


def _slugify(value: Any, *, fallback: str = "document") -> str:
    """One PATH SEGMENT, slugified. Dots survive (a segment is allowed to be
    `auth.md`); slashes never reach here -- normalize_document_path splits on
    them first, which is what keeps a caller from smuggling a second level
    into what it claimed was one segment."""
    text = re.sub(r"[^a-z0-9.]+", "-", str(value or "").strip().lower()).strip("-.")
    return text or fallback


def normalize_document_path(value: Any, *, fallback: str = "document") -> str:
    """A document's address inside its project, GitHub-shaped:
    `specs/api/auth.md`.

    GIT'S MODEL, AND NOTHING BEYOND IT (founder, 2026-08-18: "the perfect
    shape is git, so I don't want to edit this thing"). A path is a string
    with slashes; the folders in it are INFERRED by whoever renders the
    tree. Nothing here creates, validates against, or reserves a folder --
    there is no folder object anywhere in this feature, exactly as git has
    no directory object.

    Normalizing is per SEGMENT, which is the whole reason this is not one
    regex: splitting on "/" first means empty segments (`a//b`), leading and
    trailing slashes, and `.`/`..` are all removed as STRUCTURE before any
    character rule runs. A single regex over the whole string would let
    `../../etc/passwd` normalize into something that still walks, and while
    nothing here touches a filesystem, a path that can express "up" is one
    a future reader could be tricked by.

    The final segment gets `.md` when it carries no extension: every row in
    this table is markdown (`body TEXT`, rendered by MarkdownLite), and a
    tree of extensionless names does not read like a repository."""
    raw = str(value or "").replace("\\", "/")
    segments = [
        _slugify(part, fallback="")
        for part in raw.split("/")
        if str(part or "").strip() not in ("", ".", "..")
    ]
    segments = [seg for seg in segments if seg]
    if not segments:
        segments = [_slugify(fallback, fallback="document")]
    if "." not in segments[-1]:
        segments[-1] = f"{segments[-1]}{DEFAULT_DOCUMENT_EXTENSION}"
    return "/".join(segments)


def split_document_path(path: Any) -> tuple:
    """`("specs/api", "auth.md")` -- the inferred parent prefix and the file
    name. Provided so a reader never has to re-derive the split (and get the
    root case wrong): a root-level document returns `("", "auth.md")`."""
    text = str(path or "").strip().strip("/")
    if "/" not in text:
        return "", text
    parent, _, name = text.rpartition("/")
    return parent, name


def default_path_for_title(title: Any) -> str:
    """The path `create_document` would derive from a title, WITHOUT creating
    a row -- so a caller can check "is something already there" up front.
    skills_service.py's document__write dispatch uses this to fail with a
    clear message rather than letting create_document's own _unique_path
    silently disambiguate into `title-2.md`. That silent-suffix behavior is
    correct for the human/UI "New document" flow (always wants a fresh row);
    it is the wrong behavior for an agent tool, where a collision usually
    means the agent should have called document__edit instead of minting a
    near-duplicate.

    Replaces the old `slugify_title`. A path IS the generalized slug -- see
    migrations/add_document_paths.sql for why this is a rename rather than a
    second name for the same thing."""
    return normalize_document_path(title, fallback="document")




def _coerce_metadata(value: Any) -> Dict[str, Any]:
    """Postgres JSONB sometimes arrives already-decoded (dict) and sometimes
    as a raw JSON string, depending on the pool's codec setup -- same
    footgun projects_repository._coerce_metadata guards against. Handle
    both."""
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


_REVISION_COLUMNS = (
    "id, tenant_id, workspace_id, document_id, project_id, title, body, diff, "
    "changed_by_type, changed_by_id, changed_by_display_name, revision_number, created_at"
)

# The diff is the primary, human-scannable artifact a revision carries (see
# this module's own docstring: "this line changed", not "here is the whole
# document again") -- so it rides on every revision read, summary or full,
# unlike `body` which is genuinely heavy and omitted by default.
_REVISION_SUMMARY_COLUMNS = (
    "id, tenant_id, workspace_id, document_id, project_id, title, diff, "
    "changed_by_type, changed_by_id, changed_by_display_name, revision_number, created_at"
)


def _row_to_revision(row: Any, *, include_body: bool = True) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    r = dict(row)
    revision: Dict[str, Any] = {
        "id": str(r.get("id") or "").strip(),
        "document_id": str(r.get("document_id") or "").strip(),
        "project_id": str(r.get("project_id") or "").strip() or None,
        "title": str(r.get("title") or "").strip(),
        "diff": (str(r.get("diff")) if r.get("diff") is not None else None),
        "changed_by_type": str(r.get("changed_by_type") or "").strip() or "unknown",
        "changed_by_id": str(r.get("changed_by_id") or "").strip() or None,
        "changed_by_display_name": str(r.get("changed_by_display_name") or "").strip() or None,
        "revision_number": int(r.get("revision_number") or 0),
        "created_at": str(r.get("created_at") or "") or None,
    }
    if include_body:
        revision["body"] = str(r.get("body") or "")
    return revision


def _compute_document_diff(
    *, previous_title: Optional[str], previous_body: Optional[str], title: str, body: str,
) -> Optional[str]:
    """A human-readable unified diff between the row's PRE-write state and
    the state it just became -- "this line changed", the founder's own
    framing for what a revision should read like, computed once here so
    every write path (create, whole-body update, targeted patch) produces
    the identical diff shape. `previous_*` is None for a brand-new document
    (create_document has no "before"): the diff then shows every body line
    as added, the same way git renders an initial commit -- still a real,
    readable diff, not a special-cased absence. Returns None only when
    NOTHING changed (title and body both identical to before), which
    should not happen for a genuine write but is handled rather than
    asserted against."""
    resolved_previous_title = str(previous_title or "")
    resolved_previous_body = str(previous_body or "")
    parts: list[str] = []
    if resolved_previous_title != title:
        parts.append(f"- title: {resolved_previous_title}\n+ title: {title}")
    if resolved_previous_body != body:
        body_diff = "".join(
            difflib.unified_diff(
                resolved_previous_body.splitlines(keepends=True),
                body.splitlines(keepends=True),
                fromfile="before",
                tofile="after",
                lineterm="",
            )
        )
        if body_diff:
            parts.append(body_diff)
    return "\n".join(parts) if parts else None


async def _record_document_revision(
    pool: Any,
    *,
    tenant_id: str,
    workspace_id: str,
    document_id: str,
    project_id: str,
    title: str,
    body: str,
    diff: Optional[str],
    changed_by_type: str,
    changed_by_id: Optional[str],
    changed_by_display_name: str = "",
) -> None:
    """Append one snapshot to project_document_revisions -- the write half of
    "I want history" (see this module's own docstring). Git-log style: the
    snapshot is the document AS IT BECAME after the write that just landed
    (title/body already reflect the new state by the time callers reach
    here), not the state before it. `diff` is the pre-computed
    human-readable unified diff against the PRE-write state (see
    _compute_document_diff) -- carried alongside the snapshot, not instead
    of it (see this module's own docstring for why a snapshot is still
    kept: independent, cheap-to-read reconstruction of any past version).

    `revision_number` is computed server-side in the SAME INSERT (a
    COALESCE(MAX(...), 0) + 1 subquery, single statement -- no separate
    read-then-write round trip for a caller to race against), the same
    "server computes it, not the application" posture add_task_comment's
    own jsonb_set UPDATE takes. Two truly concurrent writers to the SAME
    document could still compute the same number before either commits;
    the table's UNIQUE (document_id, revision_number) constraint turns that
    into a loud INSERT failure rather than a silently ambiguous number --
    which is exactly what this function's callers are built to tolerate
    (see create_document/update_document's own comments on the fail-open
    posture: a revision-write failure is reported, never allowed to corrupt
    or block the document write that already landed).

    This function itself does not swallow anything -- it inserts the row or
    raises. Failure isolation is the CALLER's job (create_document /
    update_document), on purpose: only the caller knows whether its own
    primary write already succeeded, i.e. whether there is a document to
    protect.
    """
    rev_id = f"docrev_{uuid.uuid4().hex[:16]}"
    resolved_changed_by_type = str(changed_by_type or "").strip().lower() or "unknown"
    resolved_changed_by_id = str(changed_by_id or "").strip() or None
    resolved_display_name = str(changed_by_display_name or "").strip()[:120] or None
    await control_plane_repository.rls_execute(
        pool,
        """
        INSERT INTO project_document_revisions (
            id, tenant_id, workspace_id, document_id, project_id, title, body, diff,
            changed_by_type, changed_by_id, changed_by_display_name, revision_number
        )
        SELECT $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
               COALESCE(MAX(revision_number), 0) + 1
        FROM project_document_revisions
        WHERE tenant_id = $2 AND workspace_id = $3 AND document_id = $4
        """,
        rev_id,
        tenant_id,
        workspace_id,
        document_id,
        project_id,
        title,
        body,
        diff,
        resolved_changed_by_type,
        resolved_changed_by_id,
        resolved_display_name,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )


async def list_document_revisions(
    *,
    tenant_id: str,
    workspace_id: str,
    document_id: str,
    include_body: bool = False,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """List a document's revision history, NEWEST first -- a commit-log read.
    `include_body=False` by default, same reasoning list_documents gives for
    its own default: a history read is normally "who touched this and when,
    and what changed" (an activity feed), not a bulk content dump -- the
    `diff` field (a human-readable unified diff against the previous
    revision) rides on EVERY read regardless of include_body, since that is
    the actual point of a revision entry; pass include_body=True only when
    you need a specific past version's full text. Read-only by design --
    there is no restore/rollback function here (CLAUDE.md: "a surface must
    earn its place"; the founder asked for tracked history, not a rollback
    UI)."""
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return []
    resolved_tenant_id = str(tenant_id or "").strip()
    resolved_workspace_id = str(workspace_id or "").strip()
    resolved_document_id = str(document_id or "").strip()
    resolved_limit = max(1, min(int(limit or 50), 200))
    columns = _REVISION_COLUMNS if include_body else _REVISION_SUMMARY_COLUMNS
    rows = await control_plane_repository.rls_fetch(
        pool,
        f"""
        SELECT {columns}
        FROM project_document_revisions
        WHERE tenant_id = $1 AND workspace_id = $2 AND document_id = $3
        ORDER BY revision_number DESC
        LIMIT $4
        """,
        resolved_tenant_id,
        resolved_workspace_id,
        resolved_document_id,
        resolved_limit,
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
    )
    return [rev for rev in (_row_to_revision(r, include_body=include_body) for r in rows) if rev]


async def list_project_document_activity(
    *,
    tenant_id: str,
    workspace_id: str,
    project_id: Optional[str] = None,
    project_ids: Optional[Sequence[str]] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Every revision across every document in scope, newest first -- the
    repository's commit log, where list_document_revisions is one file's.

    THE GAP THIS CLOSES was a READ, not a write: `project_document_revisions`
    has recorded who/what/when since 2026-08-12 (changed_by_type,
    changed_by_id, changed_by_display_name, revision_number, created_at, and
    a full snapshot), and the only reader in the codebase ended
    `AND document_id = $x`. So every fact needed to answer "who changed what,
    and when" existed and was unreachable unless you already knew which
    document to open -- which is exactly backwards from how a person looks
    for a change they did not know about.

    JOINED TO project_documents, not read alone. A revision row carries its
    own `project_id`, but the document's CURRENT path lives on the document,
    and a feed that showed a document's title as of the revision would
    disagree with the tree the reader is looking at. The join also makes the
    project scope enforceable against a real, current row rather than
    against a denormalized copy.

    BODIES NEVER RIDE HERE. `diff` is the point of a feed entry (this is the
    same reasoning list_document_revisions gives for its own default), and a
    cross-document read multiplies the cost of getting that wrong.

    Scope is mandatory and fails closed, exactly as in list_documents: one of
    project_id / project_ids, empty set means "no project in reach" and
    returns nothing rather than everything."""
    resolved_tenant_id = str(tenant_id or "").strip()
    resolved_workspace_id = str(workspace_id or "").strip()
    if (project_id is None) == (project_ids is None):
        raise ValueError(
            "list_project_document_activity requires exactly one of project_id or "
            "project_ids -- an unscoped activity read is never what a caller means."
        )
    if project_ids is None:
        resolved_project_ids = [str(project_id or "").strip()]
        if not resolved_project_ids[0]:
            raise ValueError("project_id must be a non-empty project identifier.")
    else:
        resolved_project_ids = [str(p or "").strip() for p in project_ids if str(p or "").strip()]
        if not resolved_project_ids:
            return []
    resolved_limit = max(1, min(int(limit or 50), 200))
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return []
    rows = await control_plane_repository.rls_fetch(
        pool,
        """
        SELECT r.id, r.tenant_id, r.workspace_id, r.document_id, r.project_id,
               r.title, r.diff, r.changed_by_type, r.changed_by_id,
               r.changed_by_display_name, r.revision_number, r.created_at,
               d.path AS document_path, d.title AS document_title
        FROM project_document_revisions r
        JOIN project_documents d ON d.id = r.document_id
        WHERE r.tenant_id = $1 AND r.workspace_id = $2
          AND r.project_id = ANY($3::text[])
        ORDER BY r.created_at DESC, r.revision_number DESC
        LIMIT $4
        """,
        resolved_tenant_id,
        resolved_workspace_id,
        resolved_project_ids,
        resolved_limit,
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
    )
    out: List[Dict[str, Any]] = []
    for row in rows or []:
        entry = _row_to_revision(row, include_body=False)
        if entry is None:
            continue
        r = dict(row)
        entry["document_path"] = str(r.get("document_path") or "").strip()
        entry["document_title"] = str(r.get("document_title") or "").strip()
        out.append(entry)
    return out


def _row_to_document(row: Any, *, include_body: bool = True) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    r = dict(row)
    doc: Dict[str, Any] = {
        "id": str(r.get("id") or "").strip(),
        "tenant_id": str(r.get("tenant_id") or "").strip() or None,
        "workspace_id": str(r.get("workspace_id") or "").strip() or None,
        "project_id": str(r.get("project_id") or "").strip() or None,
        "title": str(r.get("title") or "").strip(),
        "path": str(r.get("path") or "").strip(),
        "created_by": str(r.get("created_by") or "").strip() or None,
        "updated_by": str(r.get("updated_by") or "").strip() or None,
        "metadata": _coerce_metadata(r.get("metadata")),
        "created_at": str(r.get("created_at") or "") or None,
        "updated_at": str(r.get("updated_at") or "") or None,
    }
    if include_body:
        doc["body"] = str(r.get("body") or "")
        # The precondition token for THIS state, on every read that carries
        # a body -- so a writer never has to compute it (or agree with us
        # about how) to write safely. Deliberately absent from a bodyless
        # list row: the token covers title AND body, so a row that omitted
        # the body could only ever carry a token that is wrong, and a wrong
        # precondition is worse than an absent one.
        # Hashed off the RAW row values, never off the normalized `doc`
        # fields above -- `doc["title"]` is .strip()ed for display and the
        # SQL expression hashes the stored column verbatim, so hashing the
        # stripped copy would make every precondition fail (silently, and
        # only for a title that happens to carry whitespace).
        doc["state_sha256"] = document_state_sha256(r.get("title"), r.get("body"), r.get("path"))
    return doc


async def _unique_path(pool: Any, *, tenant_id: str, workspace_id: str, project_id: str, base: str) -> str:
    """Return a path unique within this PROJECT (not the whole workspace --
    a document is addressed within its project), suffixing -2, -3, ... on
    clash.

    THE SUFFIX GOES BEFORE THE EXTENSION: `auth-2.md`, never `auth.md-2`.
    The naive version produces a name with no usable extension, which every
    tree renderer and every human then reads as a different kind of file.
    The parent prefix is untouched -- a clash is resolved inside the folder
    it happened in, never by moving the document somewhere else."""
    rows = await control_plane_repository.rls_fetch(
        pool,
        "SELECT path FROM project_documents WHERE tenant_id = $1 AND workspace_id = $2 AND project_id = $3",
        tenant_id,
        workspace_id,
        project_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    existing = {str(r["path"] or "").strip() for r in (rows or [])}
    if base not in existing:
        return base
    parent, name = split_document_path(base)
    stem, dot, ext = name.rpartition(".")
    prefix = f"{parent}/" if parent else ""

    def candidate(n: int) -> str:
        return f"{prefix}{stem}-{n}{dot}{ext}" if dot else f"{prefix}{name}-{n}"

    n = 2
    while candidate(n) in existing:
        n += 1
    return candidate(n)


async def list_documents(
    *,
    tenant_id: str,
    workspace_id: str,
    project_id: Optional[str] = None,
    project_ids: Optional[Sequence[str]] = None,
    include_body: bool = False,
) -> List[Dict[str, Any]]:
    """List documents, ordered by path -- a repository tree, not a feed.
    `include_body=False` by default: a list call is the common "what
    documents are there" read (a tree render, an agent doing an initial
    scan) and a workspace can hold many documents, so the default response
    never ships every document's full markdown body over the wire just to
    draw a list. Pass include_body=True for callers that actually need the
    content inline.

    ORDERED BY PATH, not by title. The tree is built by splitting `path` on
    "/", so path order is the order the rows are already in when a renderer
    walks them -- sorting by title would scatter siblings that share a
    folder and make the caller re-sort to get a repository back.

    SCOPE IS MANDATORY AND FAILS CLOSED. Exactly one of `project_id` (one
    project) or `project_ids` (the caller's visible set, for the
    cross-project Context view) must be given, and neither has a default
    that means "everything". This is the posture CLAUDE.md records for
    run_state_repository after the fail-open `WHERE ($1 = '' OR tenant_id =
    $1)` family: a forgotten scope must raise, never silently widen to
    every project in the workspace. An EMPTY `project_ids` is a real
    answer -- "this caller may see no project" -- and returns nothing
    rather than everything."""
    # SCOPE IS VALIDATED BEFORE THE POOL IS TOUCHED, deliberately. The pool
    # check returns [] when Postgres is unreachable, so validating after it
    # would make a forgotten scope SILENT on exactly the boxes where nothing
    # else is working either -- a fail-closed guard that only fires when the
    # database is up is not a guard.
    resolved_tenant_id = str(tenant_id or "").strip()
    resolved_workspace_id = str(workspace_id or "").strip()
    if (project_id is None) == (project_ids is None):
        raise ValueError(
            "list_documents requires exactly one of project_id or project_ids -- "
            "an unscoped document read is never what a caller means."
        )
    if project_ids is None:
        resolved_project_ids = [str(project_id or "").strip()]
        if not resolved_project_ids[0]:
            raise ValueError("project_id must be a non-empty project identifier.")
    else:
        resolved_project_ids = [str(p or "").strip() for p in project_ids if str(p or "").strip()]
        if not resolved_project_ids:
            # "may see no project" -- a real answer, and the only safe one.
            return []
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return []
    columns = _DOCUMENT_COLUMNS if include_body else (
        "id, tenant_id, workspace_id, project_id, title, path, "
        "created_by, updated_by, metadata, created_at, updated_at"
    )
    rows = await control_plane_repository.rls_fetch(
        pool,
        f"""
        SELECT {columns}
        FROM project_documents
        WHERE tenant_id = $1 AND workspace_id = $2
          AND project_id = ANY($3::text[])
        ORDER BY project_id ASC, path ASC, created_at ASC
        """,
        resolved_tenant_id,
        resolved_workspace_id,
        resolved_project_ids,
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
    )
    return [d for d in (_row_to_document(r, include_body=include_body) for r in rows) if d]


async def count_documents_by_project(
    *,
    tenant_id: str,
    workspace_id: str,
) -> Dict[str, int]:
    """Return {project_id: document_count} for the whole workspace, one
    query -- same shape as projects_repository.count_agents_by_project /
    project_tasks_service.count_tasks_by_project, so a project-list caller
    (fleet_projects) can put a real "N documents" beside "N tasks" without
    fetching every document body."""
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return {}
    resolved_tenant_id = str(tenant_id or "").strip()
    resolved_workspace_id = str(workspace_id or "").strip()
    rows = await control_plane_repository.rls_fetch(
        pool,
        """
        SELECT project_id, COUNT(*) AS n
        FROM project_documents
        WHERE tenant_id = $1 AND workspace_id = $2 AND project_id IS NOT NULL
        GROUP BY project_id
        """,
        resolved_tenant_id,
        resolved_workspace_id,
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
    )
    return {str(r["project_id"]): int(r["n"]) for r in (rows or [])}


async def get_document(
    *,
    tenant_id: str,
    workspace_id: str,
    document_id: str,
) -> Optional[Dict[str, Any]]:
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    resolved_tenant_id = str(tenant_id or "").strip()
    resolved_workspace_id = str(workspace_id or "").strip()
    row = await control_plane_repository.rls_fetchrow(
        pool,
        f"""
        SELECT {_DOCUMENT_COLUMNS}
        FROM project_documents
        WHERE tenant_id = $1 AND workspace_id = $2 AND id = $3
        """,
        resolved_tenant_id,
        resolved_workspace_id,
        str(document_id or "").strip(),
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
    )
    return _row_to_document(row)


async def get_document_by_path(
    *,
    tenant_id: str,
    workspace_id: str,
    project_id: str,
    path: str,
) -> Optional[Dict[str, Any]]:
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    resolved_tenant_id = str(tenant_id or "").strip()
    resolved_workspace_id = str(workspace_id or "").strip()
    row = await control_plane_repository.rls_fetchrow(
        pool,
        f"""
        SELECT {_DOCUMENT_COLUMNS}
        FROM project_documents
        WHERE tenant_id = $1 AND workspace_id = $2 AND project_id = $3 AND path = $4
        """,
        resolved_tenant_id,
        resolved_workspace_id,
        str(project_id or "").strip(),
        str(path or "").strip(),
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
    )
    return _row_to_document(row)


async def create_document(
    *,
    tenant_id: str,
    workspace_id: str,
    project_id: str,
    title: str,
    body: str = "",
    path: Optional[str] = None,
    document_id: Optional[str] = None,
    created_by: Optional[str] = None,
    changed_by_type: str = "",
    changed_by_display_name: str = "",
) -> Dict[str, Any]:
    """``changed_by_type``/``changed_by_display_name`` feed the revision-1
    snapshot recorded after the INSERT below lands (see _record_document_
    revision) -- reusing add_task_comment's own author_type vocabulary
    (human / agent / external_agent / system), not a new one. Both are
    optional and default to "unknown"/absent so every pre-existing caller
    that does not pass them keeps working unchanged; callers that know who
    is acting (routes_fleet.py: "human", skills_service.py's document__write:
    "agent", mcp_server.py's empyralis_create_document: "external_agent")
    should pass it."""
    tenant_id = str(tenant_id or "").strip()
    workspace_id = str(workspace_id or "").strip()
    project_id = str(project_id or "").strip()
    title = str(title or "").strip()
    if not tenant_id or not workspace_id:
        raise ValueError("tenant_id and workspace_id are required to create a document.")
    if not project_id:
        raise ValueError("project_id is required to create a document.")
    if not title:
        raise ValueError("Document title is required.")
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        raise control_plane_repository.runtime_db.DurableRuntimeConfigurationError(
            "Postgres is required to create a document."
        )
    base_path = normalize_document_path(path or title, fallback="document")
    final_path = await _unique_path(
        pool, tenant_id=tenant_id, workspace_id=workspace_id, project_id=project_id, base=base_path,
    )
    did = str(document_id or "").strip() or _new_document_id()
    clean_created_by = str(created_by or "").strip() or None
    row = await control_plane_repository.rls_fetchrow(
        pool,
        f"""
        INSERT INTO project_documents (id, tenant_id, workspace_id, project_id, title, path, body, created_by, updated_by)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $8)
        RETURNING {_DOCUMENT_COLUMNS}
        """,
        did,
        tenant_id,
        workspace_id,
        project_id,
        title,
        final_path,
        str(body or ""),
        clean_created_by,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    document = _row_to_document(row)
    if document is None:
        raise RuntimeError("Document insert did not return a row.")
    # Fail-open, deliberately: the document row above already committed --
    # a revisions-table outage must never look like a failed create (the
    # caller was about to be told "created", and undoing that after the
    # fact by raising here would corrupt a write that genuinely succeeded).
    # "Say so, don't corrupt" (this pass's brief): report the outcome on the
    # returned dict rather than silently dropping it -- these two keys are
    # additive, the same way get_task already enriches its row with
    # subtask_count/labels beyond its own raw columns.
    document["revision_recorded"] = True
    try:
        # previous_title/previous_body=None -- a create has no "before"; the
        # diff renders as an all-added body, the same way git shows an
        # initial commit (see _compute_document_diff's own docstring).
        creation_diff = _compute_document_diff(
            previous_title=None, previous_body=None,
            title=document["title"], body=document.get("body", ""),
        )
        await _record_document_revision(
            pool,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            document_id=document["id"],
            project_id=project_id,
            title=document["title"],
            body=document.get("body", ""),
            diff=creation_diff,
            changed_by_type=changed_by_type,
            changed_by_id=created_by,
            changed_by_display_name=changed_by_display_name,
        )
    except Exception as exc:  # noqa: BLE001 -- never let history recording undo a real create
        LOGGER.error("Failed to record creation revision for document %s", document["id"], exc_info=True)
        document["revision_recorded"] = False
        document["revision_error"] = str(exc)
    return document


async def update_document(
    *,
    tenant_id: str,
    workspace_id: str,
    document_id: str,
    expected_sha256: Optional[str],
    title: Optional[str] = None,
    body: Optional[str] = None,
    path: Optional[str] = None,
    updated_by: Optional[str] = None,
    changed_by_type: str = "",
    changed_by_display_name: str = "",
) -> Optional[Dict[str, Any]]:
    """Edit a document's title and/or body. Both fields are optional and
    independently patchable -- omitting one leaves it untouched (COALESCE),
    the same partial-update posture projects_repository.rename_project
    takes for name/description. `path` IS editable here -- that is a move
    or a rename, i.e. `git mv`, and the approved model is git's. It is
    still never DERIVED from a title change: renaming the visible `title`
    leaves the path exactly where it was, so a document's address only
    moves when somebody asks for it to move. History is safe either way --
    project_document_revisions hangs off `document_id`, not the path, so no
    move can orphan it. A path change is covered by the stale-write
    precondition like any other field (see document_state_sha256). Returns None when
    the document does not resolve in this tenant/workspace (not found, or
    belongs to someone else) -- and in that case NOTHING is written, to
    project_documents or to project_document_revisions.

    THE SEAM this module's docstring names: every caller of this function --
    the human PATCH route, the agent's document__edit tool, and MCP's
    empyralis_update_document -- gets a revision recorded for free, in one
    place, rather than three callers each having to remember to call a
    second function. ``changed_by_type``/``changed_by_display_name`` are the
    same optional pair create_document takes, for the same reason (see that
    function's own docstring).

    ``expected_sha256`` IS THE STALE-WRITE PRECONDITION, and it is a
    REQUIRED keyword with NO DEFAULT on purpose. It is the same "a scope
    column with a default is a loaded gun" posture this codebase already
    took for `agent_id` on the personal-channel inbound writes and for
    `workspace_ids` on run_state_repository: a defaulted precondition is one
    the next caller silently omits, and a silently omitted precondition is
    EXACTLY the bug this parameter exists to close. Pass the
    ``state_sha256`` of the document state you based your write on. Pass an
    explicit ``None`` only for a write that genuinely has no base and is
    meant to land unconditionally -- that is greppable, and it is supposed
    to be, because every one of them is a place a person's paragraph can
    still be overwritten.

    The comparison runs INSIDE the UPDATE's own WHERE clause (see
    _DOCUMENT_STATE_SHA256_SQL), never in Python around it: a read-then-
    compare-then-write in application code races the very write it is
    guarding, which is the same accepted race this precondition exists to
    close for edit_document_by_replace.

    THREE OUTCOMES, THREE CHANNELS -- never collapsed (CLAUDE.md's standing
    "after an action, the product must tell the person what actually
    happened" law):
      * a document dict          the write landed
      * ``None``                 the document does not resolve in this
                                 tenant/workspace (not found, or someone
                                 else's) -- nothing written
      * ``DocumentPreconditionFailed``  the document is there but has moved
                                 on since ``expected_sha256`` -- nothing
                                 written, and the exception carries the
                                 CURRENT state so the caller can show it"""
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    resolved_tenant_id = str(tenant_id or "").strip()
    resolved_workspace_id = str(workspace_id or "").strip()
    resolved_updated_by = str(updated_by or "").strip() or None
    # The `previous` CTE captures the PRE-write title/body in the SAME
    # statement as the UPDATE (a well-known single-statement pattern:
    # Postgres evaluates every CTE in a data-modifying query against the
    # snapshot at the START of the statement, before the UPDATE's own
    # writes are visible) -- one round trip, no separate SELECT-then-UPDATE
    # for a concurrent writer to race between. This is what lets the diff
    # below be computed against the row's real prior state rather than
    # nothing.
    resolved_document_id = str(document_id or "").strip()
    resolved_expected_sha256 = str(expected_sha256 or "").strip().lower() or None
    # Normalized HERE rather than trusted from the caller: `path` arrives
    # from a browser, an agent tool and MCP, and all three must land on the
    # same address for the same input or two callers disagree about where a
    # document lives. An empty/whitespace path means "not patching path"
    # (COALESCE/NULLIF below), never "move it to the root".
    resolved_path = (
        normalize_document_path(path) if str(path or "").strip() else None
    )
    # `$7::text IS NULL OR ...` reads like the fail-open tenant filter this
    # codebase already banned (`WHERE ($1 = '' OR tenant_id = $1)`), and the
    # difference is worth stating: that shape failed open on a FORGOTTEN
    # argument, because the parameter had a default nobody had to think
    # about. `expected_sha256` has no default -- an unconditional write is
    # something a caller had to type `None` to ask for.
    row = await control_plane_repository.rls_fetchrow(
        pool,
        f"""
        WITH previous AS (
            SELECT title, body, path FROM project_documents
            WHERE tenant_id = $1 AND workspace_id = $2 AND id = $3
        )
        UPDATE project_documents
        SET title = COALESCE(NULLIF($4, ''), title),
            body = COALESCE($5, body),
            updated_by = COALESCE($6, updated_by),
            path = COALESCE(NULLIF($8, ''), path),
            updated_at = NOW()
        WHERE tenant_id = $1 AND workspace_id = $2 AND id = $3
          AND ($7::text IS NULL OR $7::text = {_DOCUMENT_STATE_SHA256_SQL})
        RETURNING {_DOCUMENT_COLUMNS},
            (SELECT title FROM previous) AS _previous_title,
            (SELECT body FROM previous) AS _previous_body
        """,
        resolved_tenant_id,
        resolved_workspace_id,
        resolved_document_id,
        None if title is None else str(title).strip(),
        body,
        resolved_updated_by,
        resolved_expected_sha256,
        resolved_path,
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
    )
    raw_row = dict(row) if row is not None else None
    # Absent (rather than None) on a fake/stub row that doesn't carry these
    # synthetic RETURNING columns (e.g. test fixtures) -- .get() makes that
    # the same as "no previous state known", which _compute_document_diff
    # already treats as a from-nothing diff, never a crash.
    previous_title = raw_row.get("_previous_title") if raw_row else None
    previous_body = raw_row.get("_previous_body") if raw_row else None
    document = _row_to_document(row)
    if document is None:
        # Nothing to protect -- the UPDATE matched zero rows, so there is no
        # new state to snapshot. Recording a revision here would fabricate
        # history for an edit that never happened.
        #
        # But zero rows is now TWO different facts, and telling them apart
        # is the whole point (see this function's docstring): the document
        # may not resolve at all, or it may resolve perfectly well and have
        # moved on since the caller read it. Re-reading costs one round trip
        # on a path that already failed, and buys the caller the current
        # state to show the person -- the difference between "someone else
        # changed this, here is what it says now" and a bare error.
        if resolved_expected_sha256 is None:
            return None
        current = await get_document(
            tenant_id=resolved_tenant_id,
            workspace_id=resolved_workspace_id,
            document_id=resolved_document_id,
        )
        if current is None:
            return None
        raise DocumentPreconditionFailed(
            "This document changed since you last read it, so nothing was written. "
            "Re-read it and reapply your change on top of the current version.",
            current_document=current,
            expected_sha256=resolved_expected_sha256,
        )
    # Same fail-open posture as create_document (see that function's own
    # comment): the document row above already committed, so a revisions
    # write failure is reported on the return value, never allowed to
    # unwind or corrupt the edit the caller was just told succeeded.
    document["revision_recorded"] = True
    try:
        edit_diff = _compute_document_diff(
            previous_title=previous_title, previous_body=previous_body,
            title=document["title"], body=document.get("body", ""),
        )
        await _record_document_revision(
            pool,
            tenant_id=resolved_tenant_id,
            workspace_id=resolved_workspace_id,
            document_id=document["id"],
            project_id=document.get("project_id") or "",
            title=document["title"],
            body=document.get("body", ""),
            diff=edit_diff,
            changed_by_type=changed_by_type,
            changed_by_id=updated_by,
            changed_by_display_name=changed_by_display_name,
        )
    except Exception as exc:  # noqa: BLE001 -- never let history recording undo a real edit
        LOGGER.error("Failed to record revision for document %s", document["id"], exc_info=True)
        document["revision_recorded"] = False
        document["revision_error"] = str(exc)
    return document


# ── Document path backfill (migrations/add_document_paths.sql) ─────────────
# WHY THIS EXISTS IN PYTHON, and it is the same trap as
# projects_repository.backfill_task_identifiers on a different table -- read
# that one first, this is the identical shape one table over.
# `project_documents` carries FORCE ROW LEVEL SECURITY (migrations/
# enable_rls.sql) behind empyralis_rls_scope_match(tenant_id, workspace_id),
# and DEPLOY-RUNBOOK step 3b applies BOTH the migration file and
# CONTROL_PLANE_SCHEMA_SQL as the app's own NON-SUPERUSER role
# (`empyralis_app`), which FORCE binds. So the `slug` -> `path` rename's own
# backfill --
#
#   ALTER TABLE ... RENAME COLUMN     DDL, not subject to RLS   -> APPLIED
#   UPDATE project_documents SET ...  DML, policy evaluates FALSE -> 0 rows
#
# -- silently touched nothing on a real database, exit 0, no error anywhere.
# WORSE than an ordinary silent backfill: the rename makes `path` exist, so
# the `NOT EXISTS (... 'path')` guard around the whole rename block is false
# on every later boot -- that block never runs again, and a database that
# missed its one shot could never self-heal there. Reproduced on a
# disposable NOSUPERUSER NOBYPASSRLS-owned table exactly like the
# task-identifier bug: column renamed, rows unchanged.
#
# THIS FUNCTION IS DECOUPLED FROM THAT ONE-SHOT GUARD ON PURPOSE. It runs on
# EVERY boot, scoped by a guard on the ROW itself (`path` has neither a dot
# nor a slash), so a database that already missed its chance heals on its
# next restart rather than needing the rename to fire again.
#
# COLLISION-SAFE, never silently clobbering: if `<name>.md` is already taken
# by another document in the same project, this reuses _unique_path's own
# `-2`, `-3`, ... suffixing rather than raising or overwriting -- the same
# disambiguation create_document already gives a human typing a duplicate
# title.
async def backfill_document_paths(pool: Any) -> Dict[str, int]:
    """Append `.md` to any `project_documents.path` that has neither a dot
    nor a slash -- the extension migrations/add_document_paths.sql's own
    UPDATE could never apply under FORCE RLS (see the block comment above).

    Returns a count dict; the guard makes every step a no-op once applied.
    Safe to call on every boot and safe to call concurrently with a fresh
    write, because the WHERE clause on the UPDATE is what makes it
    idempotent, not a "has this run" flag."""
    stats = {"documents_backfilled": 0}
    if pool is None:
        return stats

    # Cross-tenant, so this READ genuinely needs the bypass; every WRITE
    # below is scoped to the single (tenant, workspace) the row itself
    # names, same split as backfill_task_identifiers.
    candidates = await control_plane_repository.rls_fetch(
        pool,
        """
        SELECT id, tenant_id, workspace_id, project_id, path
        FROM project_documents
        WHERE path NOT LIKE '%.%' AND path NOT LIKE '%/%'
        ORDER BY tenant_id, workspace_id, project_id, created_at ASC, id ASC
        """,
        bypass_rls=True,
    )
    for doc in candidates or []:
        tenant_id = str(doc["tenant_id"] or "")
        workspace_id = str(doc["workspace_id"] or "")
        project_id = str(doc["project_id"] or "")
        original_path = str(doc["path"] or "")
        base = f"{original_path}{DEFAULT_DOCUMENT_EXTENSION}"
        # Live read under the row's own scope, so a sibling document
        # already renamed earlier in THIS loop is visible to the collision
        # check -- the same discipline _unique_task_key uses inside the
        # task backfill.
        target_path = await _unique_path(
            pool,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            project_id=project_id,
            base=base,
        )
        # The WHERE guard IS the idempotency: once this UPDATE lands the
        # row carries a dot and can never match again on a later boot, so
        # two processes racing to boot at once cannot double-append.
        result = await control_plane_repository.rls_execute(
            pool,
            """
            UPDATE project_documents
            SET path = $1
            WHERE id = $2 AND path NOT LIKE '%.%' AND path NOT LIKE '%/%'
            """,
            target_path,
            str(doc["id"] or ""),
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
        if str(result or "").strip().endswith(" 1"):
            stats["documents_backfilled"] += 1

    if stats["documents_backfilled"]:
        LOGGER.warning(
            "DOCUMENT PATH BACKFILL: appended .md to %d document path(s) "
            "that predated migrations/add_document_paths.sql's own "
            "(RLS-defeated) backfill.",
            stats["documents_backfilled"],
        )
    return stats


def apply_unique_text_replacement(
    *, current_body: str, old_string: str, new_string: str, subject: str = "the document",
) -> str:
    """Pure helper (no I/O): replace `old_string` with `new_string` in
    `current_body`, requiring an EXACT, UNIQUE match. The founder's own
    words for the edit shape this exists to enforce: "to upgrade one line
    or one word or one sentence of this specific document, agent must not
    rewrite the entire document... just like git -- write a line and push
    it." Zero matches and multiple matches both FAIL LOUDLY (RuntimeError)
    with NO mutation -- never guess which occurrence was meant, never
    silently no-op, never silently fall back to a whole-body rewrite. This
    is the identical contract skills_service.py's own document__edit tool
    already gives platform agents (same three branches, same "no changes
    were made" framing) -- kept as a shared, independently-testable pure
    function so a future caller reaches for THIS rather than writing a
    fourth copy of the same three branches."""
    if not isinstance(old_string, str) or old_string == "":
        raise ValueError("old_string must be a non-empty string.")
    if not isinstance(new_string, str):
        raise ValueError("new_string is required.")
    if old_string == new_string:
        raise ValueError("old_string and new_string must differ — there is nothing to change.")
    occurrences = current_body.count(old_string)
    if occurrences == 0:
        raise RuntimeError(
            f"old_string not found in {subject}. No changes were made. Re-read the current content — the text may "
            "not match exactly, or may have changed since you last saw it."
        )
    if occurrences > 1:
        raise RuntimeError(
            f"old_string appears {occurrences} times in {subject} — it must match exactly once. No changes were "
            "made. Include more surrounding context (e.g. a nearby heading or line) so the match is unique."
        )
    return current_body.replace(old_string, new_string, 1)


async def edit_document_by_replace(
    *,
    tenant_id: str,
    workspace_id: str,
    document_id: str,
    old_string: str,
    new_string: str,
    updated_by: Optional[str] = None,
    changed_by_type: str = "",
    changed_by_display_name: str = "",
) -> Optional[Dict[str, Any]]:
    """The PRIMARY way to change a document (see this module's own
    docstring): a targeted, line/sentence-level patch, never a whole-body
    rewrite. Reads the current body, applies apply_unique_text_replacement
    (fail loudly on a zero-match or ambiguous-match old_string, no
    mutation), then goes through update_document -- the SAME seam every
    other document write goes through, so the resulting edit is revisioned
    (snapshot + diff) exactly like a whole-body update.

    Returns None if document_id does not resolve in this tenant/workspace
    (not found) -- same "return None, don't raise" convention get_document/
    update_document already use for that case. Raises (via
    apply_unique_text_replacement) for the 0-match/multi-match cases: those
    ARE exceptional -- the document exists, the instruction just cannot be
    applied unambiguously, and fails loudly rather than guessing.

    The read-then-write race this function used to document as "narrow and
    accepted" IS NOW CLOSED, and no caller had to change to get it: the
    state hash of the document this function actually read is carried into
    update_document as the precondition, so a concurrent edit landing in
    between makes this write fail loudly (DocumentPreconditionFailed) rather
    than silently applying a replacement computed against text that is no
    longer there. Re-reading and retrying was already the documented
    mitigation for a failed edit -- it is now the mitigation for this case
    too, which is the same instruction the tool descriptions already give
    the model."""
    document = await get_document(tenant_id=tenant_id, workspace_id=workspace_id, document_id=document_id)
    if document is None:
        return None
    current_body = str(document.get("body") or "")
    new_body = apply_unique_text_replacement(
        current_body=current_body,
        old_string=old_string,
        new_string=new_string,
        subject=f"document '{document.get('title') or document_id}'",
    )
    return await update_document(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        document_id=document_id,
        # The state this replacement was computed against -- not a caller-
        # supplied token, so this path is safe whether or not the tool above
        # it knows preconditions exist.
        expected_sha256=str(document.get("state_sha256") or "") or None,
        body=new_body,
        updated_by=updated_by,
        changed_by_type=changed_by_type,
        changed_by_display_name=changed_by_display_name,
    )


async def delete_document(
    *,
    tenant_id: str,
    workspace_id: str,
    document_id: str,
) -> bool:
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return False
    resolved_tenant_id = str(tenant_id or "").strip()
    resolved_workspace_id = str(workspace_id or "").strip()
    result = await control_plane_repository.rls_execute(
        pool,
        """
        DELETE FROM project_documents
        WHERE tenant_id = $1 AND workspace_id = $2 AND id = $3
        """,
        resolved_tenant_id,
        resolved_workspace_id,
        str(document_id or "").strip(),
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
    )
    return str(result or "").strip().endswith(" 1")
