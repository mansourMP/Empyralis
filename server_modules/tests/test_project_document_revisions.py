"""feat/document-mcp-tools-and-revisions: project_document_revisions --
durable history for project_documents, landed alongside the MCP tools that
let a teammate's own Claude/ChatGPT edit a document directly. The founder's
two asks this covers: (1) "wouldn't be tracked" with no hardware connected
-- a document write now always attempts a revision record; and (2) edits
must be diff/patch-native ("just like git -- write a line and push it"),
never a whole-blob rewrite in the history itself -- every revision carries
a human-readable unified diff against its immediately-prior state, computed
server-side by ``project_documents_repository._compute_document_diff``.

Exercises the REAL repository functions (create_document, update_document,
edit_document_by_replace, apply_unique_text_replacement,
list_document_revisions) against a FAKE Postgres pool -- no DATABASE_URL
needed, no real network, matching test_document_native_tools.py's own
fixture style (``_FakeTransaction``/``_FakeConnection``/``_FakeAcquire``/
``_QueuedFakePool``), duplicated rather than imported for the same reason
that file's own docstring gives (this file's correctness must not depend
on unrelated edits landing in a sibling test file concurrently).

Two fixture differences from that file's fake pool, both load-bearing for
what THIS file needs to prove:
  - ``_FakeConnection.execute`` special-cases the RLS session-scope SET
    statement (absorbed, never touches the pool) but forwards every OTHER
    execute() -- e.g. the revision INSERT -- to the pool, WITH tracking and
    an optional injected failure. test_document_native_tools.py's own fake
    stubs execute() unconditionally, which is exactly why adding revision
    writes there could not and did not disturb its existing assertions
    (see project_documents_repository.py's own comment on why the revision
    write goes through rls_execute, not rls_fetchrow).
  - ``_QueuedFakePool`` can be told to RAISE on execute() -- the one thing
    needed to prove the fail-open contract: a revisions-table outage must
    be reported (``revision_recorded: False`` + ``revision_error``), never
    allowed to corrupt or block the document write that already landed.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from server_modules import project_documents_repository as documents


class _FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeConnection:
    def __init__(self, pool: "_QueuedFakePool") -> None:
        self._pool = pool

    async def fetchrow(self, query, *args):
        return await self._pool.fetchrow(query, *args)

    async def fetch(self, query, *args):
        return await self._pool.fetch(query, *args)

    async def execute(self, query, *args):
        # The RLS GUC scope-set statement (_apply_connection_scope) runs
        # before every real query on this same connection -- absorb it here
        # exactly like test_document_native_tools.py's fake does for ALL
        # execute() calls, so it never touches the pool's own tracking/
        # failure-injection meant for the REAL statement (the revision
        # INSERT).
        if "set_config" in query:
            return "SELECT 1"
        return await self._pool.execute(query, *args)

    def transaction(self):
        return _FakeTransaction()


class _FakeAcquire:
    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection

    async def __aenter__(self):
        return self._connection

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _QueuedFakePool:
    def __init__(self, *, fetchrow_results=None, fetch_results=None, execute_error: Exception | None = None):
        self._fetchrow_results = list(fetchrow_results or [])
        self._fetch_results = list(fetch_results or [])
        self._execute_error = execute_error
        self.fetchrow_calls: list[tuple] = []
        self.fetch_calls: list[tuple] = []
        self.execute_calls: list[tuple] = []

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query, args))
        if not self._fetchrow_results:
            return None
        return self._fetchrow_results.pop(0)

    async def fetch(self, query, *args):
        self.fetch_calls.append((query, args))
        if not self._fetch_results:
            return []
        return self._fetch_results.pop(0)

    async def execute(self, query, *args):
        self.execute_calls.append((query, args))
        if self._execute_error is not None:
            raise self._execute_error
        return "INSERT 0 1"

    def acquire(self):
        return _FakeAcquire(_FakeConnection(self))


def _document_row(**overrides) -> dict:
    row = {
        "id": "doc-1",
        "tenant_id": "tenant-1",
        "workspace_id": "ws-1",
        "project_id": "proj-1",
        "title": "Runbook",
        "path": "runbook",
        "body": "line one\nline two\n",
        "created_by": "user-1",
        "updated_by": "user-1",
        "metadata": {},
        "created_at": "2026-08-01T00:00:00Z",
        "updated_at": "2026-08-01T00:00:00Z",
    }
    row.update(overrides)
    return row


def _patched_pool(pool: _QueuedFakePool):
    """Both create_document/update_document/get_document/list_document_
    revisions resolve their pool the same way -- one patch target covers
    every call in a test."""
    async def _resolve(*_args, **_kwargs):
        return pool

    return patch(
        "server_modules.project_documents_repository.control_plane_repository.ensure_control_plane_schema",
        side_effect=_resolve,
    )


def _run(coro):
    return asyncio.run(coro)


class ComputeDocumentDiffLineStructureTests(unittest.TestCase):
    """`_compute_document_diff` in isolation, asserting actual LINE
    structure (split on "\n" and check the exact set of lines) rather than
    substring containment. The existing diff tests above use assertIn,
    which cannot tell a correctly newline-separated diff from one where
    every line ran together into a single blob -- exactly the shape of bug
    that shipped here: `difflib.unified_diff(..., lineterm="")` suppresses
    the trailing newline on ITS OWN "---"/"+++"/"@@" lines while the
    content lines (from `splitlines(keepends=True)`) keep theirs, so
    `"".join(...)` glued the header lines onto the first content line --
    "--- before+++ after@@ -0,0 +1,2 @@+first line" on one row instead of
    four. DocumentHistory.tsx's own `diff.split("\n")` (one <div> per
    element) is exactly what turns a missing "\n" here into a visibly
    mangled row in the browser -- this is the same "a diff line is a line
    again" surface e2baf9bb fixed on the CSS side; this is the data side.
    A regression here renders correctly (CSS is unaffected) but garbles
    what a customer actually reads as their document's history."""

    def test_first_revision_diff_has_one_line_per_row_not_one_blob(self):
        diff = documents._compute_document_diff(
            previous_title=None, previous_body=None,
            title="Runbook", body="Base line one.\nBase line two.",
        )
        lines = diff.split("\n")
        self.assertIn("--- before", lines, f"header line ran into something else: {lines!r}")
        self.assertIn("+++ after", lines, f"header line ran into something else: {lines!r}")
        self.assertIn("+Base line one.", lines, f"content line ran into the header: {lines!r}")
        self.assertIn("+Base line two.", lines, f"content line ran into a neighbour: {lines!r}")
        # The specific failure mode this regresses to: everything before the
        # first real content line collapsed onto one row.
        self.assertNotIn(
            "--- before+++ after", "".join(lines[:1]),
            "the '---'/'+++' header lines ran together (lineterm dropped their newline)",
        )

    def test_editing_the_last_line_of_a_no_trailing_newline_body_stays_two_lines(self):
        """The ordinary case: a document typed in a plain textarea almost
        never ends with a trailing newline, and editing its LAST line is a
        completely routine edit (fixing a typo in the final sentence). The
        removed and added lines must not glue into one row just because the
        source string they came from had no trailing "\\n" of its own."""
        diff = documents._compute_document_diff(
            previous_title="Runbook", previous_body="Line one.\nLine two.\nLine three original.",
            title="Runbook", body="Line one.\nLine two.\nLine three edited.",
        )
        lines = diff.split("\n")
        self.assertIn("-Line three original.", lines, f"removed line glued to its neighbour: {lines!r}")
        self.assertIn("+Line three edited.", lines, f"added line glued to its neighbour: {lines!r}")

    def test_diff_still_reports_no_change_as_none(self):
        # Padding-for-diff must never turn an identical title+body into a
        # phantom change.
        self.assertIsNone(documents._compute_document_diff(
            previous_title="Runbook", previous_body="same text",
            title="Runbook", body="same text",
        ))


class CreateDocumentRevisionTests(unittest.TestCase):
    def test_create_records_revision_one_as_an_all_added_diff(self):
        """No 'before' state to diff against -- the revision still carries a
        real, readable diff (every line shown as added), the same way git
        renders an initial commit. Call-count: exactly ONE revision INSERT,
        not zero and not two."""
        pool = _QueuedFakePool(
            fetch_results=[[]],  # _unique_slug's own existing-slugs SELECT
            fetchrow_results=[_document_row(title="Runbook", body="alpha\nbeta\n")],
        )
        with _patched_pool(pool):
            document = _run(documents.create_document(
                tenant_id="tenant-1", workspace_id="ws-1", project_id="proj-1",
                title="Runbook", body="alpha\nbeta\n",
                created_by="user-1", changed_by_type="human",
            ))
        self.assertTrue(document["revision_recorded"])
        self.assertNotIn("revision_error", document)
        self.assertEqual(len(pool.execute_calls), 1, "expected exactly one revision INSERT, not zero or several")
        query, args = pool.execute_calls[0]
        self.assertIn("INSERT INTO project_document_revisions", query)
        self.assertIn("COALESCE(MAX(revision_number), 0) + 1", query)
        # id, tenant_id, workspace_id, document_id, project_id, title, body, diff, changed_by_type, changed_by_id, changed_by_display_name
        diff_arg = args[7]
        self.assertIn("+alpha", diff_arg)
        self.assertIn("+beta", diff_arg)
        self.assertEqual(args[8], "human")
        self.assertEqual(args[9], "user-1")

    def test_create_document_returns_immediately_even_though_revision_is_a_second_write(self):
        """The document itself is fully created (id/slug/body all present)
        regardless of the revision side effect -- proves the two writes are
        independent, not one atomic thing a caller could half-see."""
        pool = _QueuedFakePool(
            fetch_results=[[]],
            fetchrow_results=[_document_row(title="Runbook", path="runbook", body="hi")],
        )
        with _patched_pool(pool):
            document = _run(documents.create_document(
                tenant_id="tenant-1", workspace_id="ws-1", project_id="proj-1", title="Runbook", body="hi",
            ))
        self.assertEqual(document["id"], "doc-1")
        self.assertEqual(document["path"], "runbook")
        self.assertEqual(document["body"], "hi")


class UpdateDocumentRevisionTests(unittest.TestCase):
    def test_update_records_a_diff_reflecting_the_actual_change(self):
        """The diff must show what ACTUALLY changed (old line removed, new
        line added), not the whole new body dressed up as a diff -- proves
        _compute_document_diff is fed the real pre-write state via the
        UPDATE's own CTE, not a blank slate."""
        pool = _QueuedFakePool(
            fetchrow_results=[{
                **_document_row(title="Runbook", body="line one\nline TWO, revised\n"),
                "_previous_title": "Runbook",
                "_previous_body": "line one\nline two\n",
            }],
        )
        with _patched_pool(pool):
            document = _run(documents.update_document(
                tenant_id="tenant-1", workspace_id="ws-1", document_id="doc-1",
                expected_sha256=None,
                body="line one\nline TWO, revised\n",
                updated_by="agent-1", changed_by_type="agent",
            ))
        self.assertTrue(document["revision_recorded"])
        self.assertEqual(len(pool.execute_calls), 1)
        _query, args = pool.execute_calls[0]
        diff_arg = args[7]
        self.assertIn("-line two", diff_arg)
        self.assertIn("+line TWO, revised", diff_arg)
        # The unchanged line must NOT be reported as touched.
        self.assertNotIn("-line one", diff_arg)
        self.assertEqual(args[8], "agent")
        self.assertEqual(args[9], "agent-1")

    def test_update_that_matches_no_row_records_no_revision(self):
        """A cross-tenant/not-found update writes NOTHING -- not to
        project_documents, and not a fabricated revision for an edit that
        never happened."""
        pool = _QueuedFakePool(fetchrow_results=[None])
        with _patched_pool(pool):
            document = _run(documents.update_document(
                tenant_id="tenant-1", workspace_id="ws-1", document_id="doc-missing",
                expected_sha256=None, body="new body",
            ))
        self.assertIsNone(document)
        self.assertEqual(len(pool.execute_calls), 0, "no revision should be written for a no-op update")

    def test_revision_write_failure_does_not_corrupt_or_block_the_document_update(self):
        """THE fail-mode proof: the document row already committed (the
        UPDATE...RETURNING succeeded) -- a revisions-table outage must be
        REPORTED, never allowed to undo or hide the edit the caller was
        just told succeeded."""
        pool = _QueuedFakePool(
            fetchrow_results=[{
                **_document_row(title="Runbook", body="new content"),
                "_previous_title": "Runbook",
                "_previous_body": "old content",
            }],
            execute_error=RuntimeError("revisions table is unreachable"),
        )
        with _patched_pool(pool):
            document = _run(documents.update_document(
                tenant_id="tenant-1", workspace_id="ws-1", document_id="doc-1",
                expected_sha256=None,
                body="new content", updated_by="user-1", changed_by_type="human",
            ))
        # The document write is intact and returned normally -- not None,
        # not raised.
        self.assertIsNotNone(document)
        self.assertEqual(document["body"], "new content")
        self.assertFalse(document["revision_recorded"])
        self.assertIn("revisions table is unreachable", document["revision_error"])
        # The failed attempt still counts as one call -- this is not "never
        # tried", it is "tried once and reported".
        self.assertEqual(len(pool.execute_calls), 1)

    def test_create_revision_write_failure_does_not_undo_the_create(self):
        """Same fail-open proof, on the create path."""
        pool = _QueuedFakePool(
            fetch_results=[[]],
            fetchrow_results=[_document_row(title="Runbook", body="hello")],
            execute_error=RuntimeError("revisions table is unreachable"),
        )
        with _patched_pool(pool):
            document = _run(documents.create_document(
                tenant_id="tenant-1", workspace_id="ws-1", project_id="proj-1",
                title="Runbook", body="hello",
            ))
        self.assertEqual(document["id"], "doc-1")
        self.assertFalse(document["revision_recorded"])
        self.assertIn("revisions table is unreachable", document["revision_error"])


class ApplyUniqueTextReplacementTests(unittest.TestCase):
    """Pure-function tests -- no I/O, no fake pool needed. The exact
    contract empyralis_edit_document (and skills_service.py's own
    document__edit) is built to guarantee."""

    def test_exactly_one_match_is_applied(self):
        result = documents.apply_unique_text_replacement(
            current_body="line one\nline two\nline three\n",
            old_string="line two",
            new_string="line TWO, revised",
        )
        self.assertEqual(result, "line one\nline TWO, revised\nline three\n")

    def test_zero_matches_raises_and_the_message_says_no_changes_were_made(self):
        with self.assertRaises(RuntimeError) as ctx:
            documents.apply_unique_text_replacement(
                current_body="line one\nline two\n", old_string="line NOPE", new_string="x",
            )
        self.assertIn("not found", str(ctx.exception))
        self.assertIn("No changes were made", str(ctx.exception))

    def test_multiple_matches_raises_and_the_message_says_no_changes_were_made(self):
        with self.assertRaises(RuntimeError) as ctx:
            documents.apply_unique_text_replacement(
                current_body="dup\ndup\n", old_string="dup", new_string="x",
            )
        self.assertIn("appears 2 times", str(ctx.exception))
        self.assertIn("exactly once", str(ctx.exception))
        self.assertIn("No changes were made", str(ctx.exception))

    def test_identical_strings_are_rejected(self):
        with self.assertRaises(ValueError):
            documents.apply_unique_text_replacement(current_body="x", old_string="x", new_string="x")

    def test_empty_old_string_is_rejected(self):
        with self.assertRaises(ValueError):
            documents.apply_unique_text_replacement(current_body="x", old_string="", new_string="y")


class EditDocumentByReplaceTests(unittest.TestCase):
    """edit_document_by_replace: read -> apply_unique_text_replacement ->
    update_document. The end-to-end 'write a line and push it' path."""

    def test_successful_edit_applies_through_update_document_and_is_revisioned(self):
        pool = _QueuedFakePool(
            fetchrow_results=[
                _document_row(title="Runbook", body="line one\nline two\nline three\n"),  # get_document
                {
                    **_document_row(title="Runbook", body="line one\nline TWO, revised\nline three\n"),
                    "_previous_title": "Runbook",
                    "_previous_body": "line one\nline two\nline three\n",
                },  # update_document's own UPDATE ... RETURNING
            ],
        )
        with _patched_pool(pool):
            document = _run(documents.edit_document_by_replace(
                tenant_id="tenant-1", workspace_id="ws-1", document_id="doc-1",
                old_string="line two", new_string="line TWO, revised",
                updated_by="ext-agent-1", changed_by_type="external_agent",
            ))
        self.assertEqual(document["body"], "line one\nline TWO, revised\nline three\n")
        self.assertTrue(document["revision_recorded"])
        # Exactly one revision write for one successful edit.
        self.assertEqual(len(pool.execute_calls), 1)
        _query, args = pool.execute_calls[0]
        self.assertIn("-line two", args[7])
        self.assertIn("+line TWO, revised", args[7])
        self.assertEqual(args[8], "external_agent")

    def test_no_match_fails_loudly_with_zero_writes(self):
        pool = _QueuedFakePool(fetchrow_results=[_document_row(body="line one\nline two\n")])
        with _patched_pool(pool):
            with self.assertRaises(RuntimeError) as ctx:
                _run(documents.edit_document_by_replace(
                    tenant_id="tenant-1", workspace_id="ws-1", document_id="doc-1",
                    old_string="line NOPE", new_string="x",
                ))
        self.assertIn("not found", str(ctx.exception))
        # get_document ran (1 fetchrow); update_document never started.
        self.assertEqual(len(pool.fetchrow_calls), 1)
        self.assertEqual(len(pool.execute_calls), 0)

    def test_multiple_matches_fails_loudly_with_zero_writes(self):
        pool = _QueuedFakePool(fetchrow_results=[_document_row(body="dup\ndup\n")])
        with _patched_pool(pool):
            with self.assertRaises(RuntimeError) as ctx:
                _run(documents.edit_document_by_replace(
                    tenant_id="tenant-1", workspace_id="ws-1", document_id="doc-1",
                    old_string="dup", new_string="x",
                ))
        self.assertIn("exactly once", str(ctx.exception))
        self.assertEqual(len(pool.fetchrow_calls), 1)
        self.assertEqual(len(pool.execute_calls), 0)

    def test_document_not_found_returns_none_without_raising(self):
        pool = _QueuedFakePool(fetchrow_results=[None])
        with _patched_pool(pool):
            result = _run(documents.edit_document_by_replace(
                tenant_id="tenant-1", workspace_id="ws-1", document_id="doc-missing",
                old_string="x", new_string="y",
            ))
        self.assertIsNone(result)


class ListDocumentRevisionsTests(unittest.TestCase):
    def test_default_read_carries_the_diff_but_not_the_body(self):
        pool = _QueuedFakePool(
            fetch_results=[[
                {
                    "id": "docrev-2", "tenant_id": "tenant-1", "workspace_id": "ws-1",
                    "document_id": "doc-1", "project_id": "proj-1", "title": "Runbook",
                    "diff": "-old\n+new\n", "changed_by_type": "human", "changed_by_id": "user-1",
                    "changed_by_display_name": None, "revision_number": 2,
                    "created_at": "2026-08-02T00:00:00Z",
                },
                {
                    "id": "docrev-1", "tenant_id": "tenant-1", "workspace_id": "ws-1",
                    "document_id": "doc-1", "project_id": "proj-1", "title": "Runbook",
                    "diff": None, "changed_by_type": "agent", "changed_by_id": "agent-1",
                    "changed_by_display_name": None, "revision_number": 1,
                    "created_at": "2026-08-01T00:00:00Z",
                },
            ]],
        )
        with _patched_pool(pool):
            revisions = _run(documents.list_document_revisions(
                tenant_id="tenant-1", workspace_id="ws-1", document_id="doc-1",
            ))
        self.assertEqual(len(revisions), 2)
        self.assertEqual(revisions[0]["revision_number"], 2)
        self.assertEqual(revisions[0]["diff"], "-old\n+new\n")
        self.assertNotIn("body", revisions[0])
        self.assertEqual(revisions[0]["changed_by_type"], "human")
        self.assertEqual(revisions[1]["changed_by_type"], "agent")
        _query, _args = pool.fetch_calls[0]
        self.assertIn("ORDER BY revision_number DESC", _query)

    def test_include_body_true_adds_the_full_snapshot(self):
        pool = _QueuedFakePool(
            fetch_results=[[
                {
                    "id": "docrev-1", "tenant_id": "tenant-1", "workspace_id": "ws-1",
                    "document_id": "doc-1", "project_id": "proj-1", "title": "Runbook",
                    "body": "full text here", "diff": None, "changed_by_type": "human",
                    "changed_by_id": "user-1", "changed_by_display_name": None,
                    "revision_number": 1, "created_at": "2026-08-01T00:00:00Z",
                },
            ]],
        )
        with _patched_pool(pool):
            revisions = _run(documents.list_document_revisions(
                tenant_id="tenant-1", workspace_id="ws-1", document_id="doc-1", include_body=True,
            ))
        self.assertEqual(revisions[0]["body"], "full text here")


if __name__ == "__main__":
    unittest.main()
