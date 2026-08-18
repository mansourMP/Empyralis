"""The stale-write precondition on project documents -- MAN-115's one
unbuilt piece, and the fix for a SILENT DATA-LOSS bug on the surface
CLAUDE.md calls this product's durable asset.

THE LOSS THIS FILE EXISTS TO PROVE IS GONE, in the order it happened live:

    t0  a person opens /projects/{p}/documents/{d}.  DocumentDetailView
        snapshots title+body into a draft.  The page NEVER refetches --
        documents-data.ts's own header asserted documents are "not mutated
        out from under the reader by an agent", which stopped being true
        the day document__edit shipped.
    t1  an agent lands a real edit (document__edit / empyralis_edit_
        document / empyralis_update_document).
    t2  the person types one character.  900ms later autosave PATCHes the
        WHOLE BODY from the t0 draft.

    BEFORE: the agent's paragraph is gone, and project_document_revisions
            records a clean row attributing the reversion to the HUMAN --
            so the loss is invisible even in the audit trail.
    AFTER:  the t2 write is REFUSED (nothing written), the agent's edit
            stands, and the person is handed the current state to choose
            against.

FOUR CLASSES, and the split is deliberate -- each proves something the
others structurally cannot:

1. ``DocumentStateSha256Tests`` -- known-answer vectors for the token
   itself, plus the collision property the concatenated-digest
   construction exists for.  Runs everywhere, no database.

2. ``PreconditionSourceContractTests`` -- AST/source assertions: that
   ``expected_sha256`` is a REQUIRED keyword with no default, that every
   production call site passes it, and that the compare-and-swap is in the
   UPDATE's own WHERE clause rather than in Python around it.  A
   behavioural test cannot catch the reintroduction of any of these: a
   defaulted parameter type-checks, a Python-side comparison passes every
   single-threaded test ever written, and a forgotten call-site argument is
   invisible until two writers actually collide in production.  Runs
   everywhere, no database.

3. ``PreconditionOutcomeChannelTests`` -- the three-facts split (saved /
   not found / refused) driven through the real ``update_document`` against
   a fake pool.  Runs everywhere, no database.

4. ``DocumentStaleWriteRealPostgresTests`` -- THE loss scenario, end to
   end, against real Postgres: the agent writes, the stale human write
   lands, and the agent's text is asserted to still be in the row.  This is
   the only class that proves the compare-and-swap ACTUALLY SWAPS, because
   the comparison runs inside Postgres and no fake pool can execute it --
   a fake that "implemented" the CAS in Python would be a fixture inventing
   its own semantics, which this codebase already has a written rule
   against.  Opt-in from an exported DATABASE_URL, skips cleanly without
   one, same convention as test_project_documents_repository.py.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import os
import pathlib
import unittest
import uuid
from unittest.mock import patch

from server_modules import project_documents_repository as documents


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_REPOSITORY_PATH = _REPO_ROOT / "server_modules" / "project_documents_repository.py"


def _database_url_available() -> bool:
    return bool(os.getenv("DATABASE_URL", "").strip())


_NO_PG_REASON = "No Postgres reachable (DATABASE_URL unset — export it to run this suite)"


# ── 1. The token itself ───────────────────────────────────────────────────


class DocumentStateSha256Tests(unittest.TestCase):
    def test_known_answer_vectors(self):
        """Pinned literals, not a re-derivation of the implementation with
        the implementation. The real cross-check that the SQL and the Python
        agree lives in the Postgres class below, which re-derives the same
        token straight out of the database -- two independent sources, the
        discipline CLAUDE.md's own "a check that derives its own
        expectations from the thing it checks is blind" note demands."""
        # Re-pinned when `path` joined the token (documents became
        # GitHub-shaped). Both literals below were derived INDEPENDENTLY of
        # this implementation, with shell shasum:
        #   h() { printf '%s' "$1" | shasum -a 256 | cut -d' ' -f1; }
        #   printf '%s%s%s' "$(h TITLE)" "$(h BODY)" "$(h PATH)" | shasum -a 256
        self.assertEqual(
            documents.document_state_sha256("Runbook", "hello"),
            "e1746794e2f1e8f0cd9fb4dae58fb74251a21bc16a4ea993f286ca87df4c619c",
            "a pathless document state -- the two-argument call still describes a real state",
        )
        self.assertEqual(
            documents.document_state_sha256("Runbook", "hello", "specs/api/auth.md"),
            "6aae137f40a7de869ea79854026531e66fdf80703dd649f2afc20caae9027ca4",
        )
        self.assertEqual(
            documents.document_state_sha256("", ""),
            documents.document_state_sha256(None, None),
            "None and empty string are the same document state (the SQL side COALESCEs both to '')",
        )
        self.assertEqual(
            documents.document_state_sha256("", "", ""),
            "74313561d1897af3dc03f4fae174960d28968f92b49230523faca462b848db60",
        )

    def test_a_move_is_a_conflict_like_any_other_edit(self):
        """PATH IS COVERED BY THE TOKEN. Two documents identical in title and
        body but living at different paths are different states, so a save
        composed against one is refused against the other. Without this a
        stale autosave could silently un-move a document somebody else had
        just moved -- the same silent loss the precondition exists to stop,
        pointed at the tree instead of the text."""
        self.assertNotEqual(
            documents.document_state_sha256("Runbook", "hello", "notes/runbook.md"),
            documents.document_state_sha256("Runbook", "hello", "specs/runbook.md"),
        )

    def test_the_three_field_boundary_cannot_be_forged(self):
        """The digest-concatenation argument, extended to three fields: a
        naive title||body||path would collide these two genuinely different
        states."""
        self.assertNotEqual(
            documents.document_state_sha256("a", "b", "cd"),
            documents.document_state_sha256("a", "bc", "d"),
        )

    def test_identical_content_is_the_same_token(self):
        """The whole reason the token is a CONTENT hash and not updated_at:
        a rewrite that produced byte-identical text is not a conflict, and
        must not be reported to a person as one."""
        self.assertEqual(
            documents.document_state_sha256("Runbook", "line one\nline two"),
            documents.document_state_sha256("Runbook", "line one\nline two"),
        )

    def test_title_body_boundary_cannot_be_forged(self):
        """Why the construction hashes each field and concatenates the
        DIGESTS rather than the raw text: Postgres text cannot contain a NUL
        byte, so there is no separator a title is guaranteed not to contain,
        and a naive `title || body` would collide these two genuinely
        different document states into one token -- i.e. would silently
        accept a stale write."""
        self.assertNotEqual(
            documents.document_state_sha256("ab", "c"),
            documents.document_state_sha256("a", "bc"),
        )

    def test_a_body_bearing_read_carries_the_token_and_a_list_row_does_not(self):
        row = {"id": "doc-1", "title": "Runbook", "body": "hello", "metadata": {}}
        with_body = documents._row_to_document(row, include_body=True)
        self.assertEqual(
            with_body["state_sha256"], documents.document_state_sha256("Runbook", "hello"),
        )
        without_body = documents._row_to_document(row, include_body=False)
        self.assertNotIn(
            "state_sha256", without_body,
            "a bodyless list row must carry NO token — the token covers title AND body, so a "
            "token on a row that omitted the body could only ever be wrong, and a wrong "
            "precondition is worse than an absent one.",
        )

    def test_token_is_hashed_off_the_raw_title_not_the_stripped_display_copy(self):
        """_row_to_document .strip()s the title for display while the SQL
        expression hashes the stored column verbatim. Hashing the stripped
        copy would make every precondition fail -- silently, and only for
        titles that happen to carry whitespace."""
        doc = documents._row_to_document({"id": "d", "title": "  Runbook  ", "body": "x"}, include_body=True)
        self.assertEqual(doc["title"], "Runbook")
        self.assertEqual(doc["state_sha256"], documents.document_state_sha256("  Runbook  ", "x"))


# ── 2. Source contract: what a behavioural test cannot see ────────────────


class PreconditionSourceContractTests(unittest.TestCase):
    def _repository_ast(self) -> ast.Module:
        return ast.parse(_REPOSITORY_PATH.read_text())

    def test_expected_sha256_is_a_required_keyword_with_no_default(self):
        """CLAUDE.md's "a scope column with a default is a loaded gun",
        applied to a precondition: a defaulted precondition is one the next
        caller silently omits, and a silently omitted precondition is
        exactly the bug this parameter closes. Reintroducing `= None` here
        type-checks, passes every existing test, and is silent in
        production -- hence a source assertion."""
        tree = self._repository_ast()
        fn = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "update_document"
        )
        names = [a.arg for a in fn.args.kwonlyargs]
        self.assertIn("expected_sha256", names)
        default = fn.args.kw_defaults[names.index("expected_sha256")]
        self.assertIsNone(
            default,
            "update_document.expected_sha256 must have NO default — an unconditional document "
            "overwrite has to be something a caller typed, never something a caller forgot.",
        )

    def test_every_production_call_site_passes_the_precondition(self):
        """A required keyword already makes an omission a TypeError at
        runtime -- but only on a line that actually executes. This enumerates
        the call sites statically so a new one is caught by the suite rather
        than by a customer."""
        call_sites = {
            _REPO_ROOT / "server_modules" / "routes_fleet.py",
            _REPO_ROOT / "server_modules" / "skills_service.py",
            _REPO_ROOT / "server_modules" / "project_documents_repository.py",
            _REPO_ROOT / "mcp_server.py",
        }
        found = 0
        for path in sorted(call_sites):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if name != "update_document":
                    continue
                found += 1
                kwargs = {kw.arg for kw in node.keywords}
                self.assertIn(
                    "expected_sha256", kwargs,
                    f"{path.name}: an update_document call site with no expected_sha256 — every "
                    "document write must state the document state it was composed against, or "
                    "explicitly pass None to ask for an unconditional overwrite.",
                )
        self.assertGreaterEqual(found, 4, "expected at least the four known update_document call sites")

    def test_the_comparison_lives_in_the_update_statement_not_in_python(self):
        """A read-then-compare-then-write in application code races the very
        write it is guarding -- it would pass every test in this file and
        close nothing. The compare-and-swap has to be one statement."""
        src = _REPOSITORY_PATH.read_text()
        self.assertIn("_DOCUMENT_STATE_SHA256_SQL", src)
        update_stmt = src.split("UPDATE project_documents", 1)[1].split('"""', 1)[0]
        self.assertIn(
            "$7::text = {_DOCUMENT_STATE_SHA256_SQL}", update_stmt,
            "the precondition must be a predicate on the UPDATE's own WHERE clause",
        )

    def test_a_refused_write_is_a_distinct_exception_not_a_none(self):
        """"not found" and "somebody else changed it" are two different
        facts and must not share the None channel -- CLAUDE.md's own law,
        which this codebase has now been bitten by four separate times."""
        self.assertTrue(issubclass(documents.DocumentPreconditionFailed, Exception))
        sig = inspect.signature(documents.DocumentPreconditionFailed.__init__)
        self.assertIn("current_document", sig.parameters)


# ── 3. The three outcome channels, over the real function ─────────────────


class _FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeConnection:
    def __init__(self, pool):
        self._pool = pool

    async def fetchrow(self, query, *args):
        return await self._pool.fetchrow(query, *args)

    async def fetch(self, query, *args):
        return []

    async def execute(self, query, *args):
        if "set_config" in str(query):
            return "SET"
        self._pool.execute_calls.append((query, args))
        return "INSERT 0 1"

    def transaction(self):
        return _FakeTransaction()


class _FakeAcquire:
    def __init__(self, pool):
        self._pool = pool

    async def __aenter__(self):
        return _FakeConnection(self._pool)

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _QueuedFakePool:
    """Returns queued fetchrow results in order. It deliberately does NOT
    evaluate the precondition -- a fake that pretended to run the SQL
    compare-and-swap would be a fixture inventing its own semantics (the
    exact shape CLAUDE.md records as having killed a feature on the day it
    shipped). What Postgres would have returned is EXPRESSED here (a
    matched row, or None) and what the Python around it does with that is
    what these tests actually prove."""

    def __init__(self, fetchrow_results):
        self._fetchrow_results = list(fetchrow_results)
        self.fetchrow_queries = []
        self.execute_calls = []

    def acquire(self):
        return _FakeAcquire(self)

    async def fetchrow(self, query, *args):
        self.fetchrow_queries.append((query, args))
        return self._fetchrow_results.pop(0) if self._fetchrow_results else None

    async def fetch(self, query, *args):
        return []

    async def execute(self, query, *args):
        self.execute_calls.append((query, args))
        return "INSERT 0 1"


def _patched_pool(pool):
    return patch.object(
        documents.control_plane_repository,
        "ensure_control_plane_schema",
        new=lambda: asyncio.sleep(0, result=pool),
    )


def _row(**over):
    row = {
        "id": "doc-1",
        "tenant_id": "tenant-1",
        "workspace_id": "ws-1",
        "project_id": "proj-1",
        "title": "Runbook",
        "slug": "runbook",
        "body": "agent paragraph",
        "created_by": "user-1",
        "updated_by": "user-1",
        "metadata": {},
        "created_at": "2026-08-18T00:00:00+00:00",
        "updated_at": "2026-08-18T00:00:00+00:00",
    }
    row.update(over)
    return row


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class PreconditionOutcomeChannelTests(unittest.TestCase):
    def test_a_matching_precondition_writes_and_returns_the_document(self):
        pool = _QueuedFakePool([{**_row(body="new"), "_previous_title": "Runbook", "_previous_body": "old"}])
        with _patched_pool(pool):
            doc = _run(documents.update_document(
                tenant_id="tenant-1", workspace_id="ws-1", document_id="doc-1",
                expected_sha256=documents.document_state_sha256("Runbook", "old"),
                body="new", changed_by_type="human",
            ))
        self.assertIsNotNone(doc)
        self.assertEqual(doc["body"], "new")
        # The response carries the NEXT precondition, so the caller never has
        # to compute one (or agree with us about how) to keep writing safely.
        self.assertEqual(doc["state_sha256"], documents.document_state_sha256("Runbook", "new"))

    def test_the_precondition_is_bound_as_a_query_argument(self):
        pool = _QueuedFakePool([{**_row(), "_previous_title": "Runbook", "_previous_body": "old"}])
        token = documents.document_state_sha256("Runbook", "old")
        with _patched_pool(pool):
            _run(documents.update_document(
                tenant_id="tenant-1", workspace_id="ws-1", document_id="doc-1",
                expected_sha256=token, body="new",
            ))
        _query, args = pool.fetchrow_queries[0]
        self.assertIn(token, args, "expected_sha256 must reach the database as a bound parameter")

    def test_zero_rows_with_no_precondition_is_not_found_and_never_a_conflict(self):
        """An unconditional write that matched nothing has exactly one
        possible meaning, and inventing a conflict for it would send a
        person to resolve a document that is not there."""
        pool = _QueuedFakePool([None])
        with _patched_pool(pool):
            doc = _run(documents.update_document(
                tenant_id="tenant-1", workspace_id="ws-1", document_id="gone",
                expected_sha256=None, body="new",
            ))
        self.assertIsNone(doc)
        self.assertEqual(len(pool.execute_calls), 0, "no revision may be fabricated for a write that never landed")

    def test_zero_rows_with_a_precondition_and_a_live_row_is_a_refusal_carrying_the_current_state(self):
        """The conflict channel. Two fetchrow results: the UPDATE matched
        nothing, then the re-read finds the document alive and moved on."""
        pool = _QueuedFakePool([None, _row(body="agent paragraph")])
        with _patched_pool(pool):
            with self.assertRaises(documents.DocumentPreconditionFailed) as caught:
                _run(documents.update_document(
                    tenant_id="tenant-1", workspace_id="ws-1", document_id="doc-1",
                    expected_sha256=documents.document_state_sha256("Runbook", "what the human loaded"),
                    body="the human's stale whole-body draft",
                ))
        exc = caught.exception
        self.assertEqual(exc.current_document["body"], "agent paragraph")
        self.assertEqual(exc.current_sha256, documents.document_state_sha256("Runbook", "agent paragraph"))
        self.assertEqual(len(pool.execute_calls), 0, "a refused write must record no revision")

    def test_zero_rows_with_a_precondition_and_no_row_at_all_is_still_not_found(self):
        """A deleted document must not be reported as a conflict — there is
        nothing to choose between."""
        pool = _QueuedFakePool([None, None])
        with _patched_pool(pool):
            doc = _run(documents.update_document(
                tenant_id="tenant-1", workspace_id="ws-1", document_id="gone",
                expected_sha256=documents.document_state_sha256("Runbook", "old"), body="new",
            ))
        self.assertIsNone(doc)

    def test_edit_by_replace_carries_its_own_precondition_without_being_asked(self):
        """The read-then-write race project_documents_repository.py used to
        document as "narrow and accepted" — closed with no change to either
        agent tool above it. The token it passes must be the state it
        actually READ, not None."""
        seen = {}

        async def _fake_update(**kwargs):
            seen.update(kwargs)
            return _row(body=kwargs.get("body"))

        with patch.object(documents, "get_document", new=lambda **kw: asyncio.sleep(
            0, result=documents._row_to_document(_row(body="one two three"), include_body=True),
        )):
            with patch.object(documents, "update_document", new=_fake_update):
                _run(documents.edit_document_by_replace(
                    tenant_id="tenant-1", workspace_id="ws-1", document_id="doc-1",
                    old_string="two", new_string="TWO",
                ))
        self.assertEqual(
            seen.get("expected_sha256"),
            documents.document_state_sha256("Runbook", "one two three"),
        )


# ── 4. The real loss scenario, against real Postgres ──────────────────────


class DocumentStaleWriteRealPostgresTests(unittest.TestCase):
    """The only class here that proves the compare-and-swap actually swaps.
    Opt-in from an exported DATABASE_URL; skips cleanly without one."""

    def _maybe_await(self, value):
        return _bridge(value) if inspect.iscoroutine(value) else value

    def _callTestMethod(self, method):
        self._maybe_await(method())

    def setUp(self):
        try:
            self._maybe_await(self.async_setup())
        except Exception:
            try:
                self._maybe_await(self.async_teardown())
            except Exception:
                pass
            raise

    def tearDown(self):
        self._maybe_await(self.async_teardown())

    async def async_setup(self):
        if not _database_url_available():
            self.skipTest(_NO_PG_REASON)
            return
        from server_modules import control_plane_repository as cpr

        pool = await cpr.ensure_control_plane_schema()
        if pool is None:
            self.skipTest(_NO_PG_REASON)
            return
        self.pool = pool
        suffix = uuid.uuid4().hex[:10]
        self.tenant = f"t_docprecond_{suffix}"
        self.ws = f"ws_docprecond_{suffix}"

        from server_modules import projects_repository as repo

        self.project = await repo.create_project(
            tenant_id=self.tenant, workspace_id=self.ws, name="Docs Precondition",
        )

    async def async_teardown(self):
        pool = getattr(self, "pool", None)
        tenant = getattr(self, "tenant", None)
        if pool is None or not tenant:
            return
        for table in ("project_document_revisions", "project_documents", "projects"):
            try:
                await pool.execute(f"DELETE FROM {table} WHERE tenant_id = $1", tenant)
            except Exception:
                pass

    async def test_a_stale_human_whole_body_save_cannot_destroy_an_agents_edit(self):
        """THE REGRESSION TEST. Reverting project_documents_repository.py to
        its pre-fix state makes this fail by asserting the agent's paragraph
        is still in the row — which is the entire bug, in one assertion."""
        doc = await documents.create_document(
            tenant_id=self.tenant, workspace_id=self.ws, project_id=self.project["id"],
            title="Runbook", body="# Deploy\n\nStep one.\n", created_by="user_human",
        )

        # t0 -- the person opens the page. This is the ONLY state their
        # browser will ever have; the document page does not poll.
        human_base_sha = doc["state_sha256"]
        human_draft_body = "# Deploy\n\nStep one.\n\nSomething the person typed.\n"

        # t1 -- an agent lands a real edit through its own tool path.
        after_agent = await documents.edit_document_by_replace(
            tenant_id=self.tenant, workspace_id=self.ws, document_id=doc["id"],
            old_string="Step one.", new_string="Step one.\nStep two, added by the agent.",
            updated_by="agent_1", changed_by_type="agent",
        )
        self.assertIn("added by the agent", after_agent["body"])

        # t2 -- the person's 900ms autosave fires with the t0 draft.
        with self.assertRaises(documents.DocumentPreconditionFailed) as caught:
            await documents.update_document(
                tenant_id=self.tenant, workspace_id=self.ws, document_id=doc["id"],
                expected_sha256=human_base_sha,
                title="Runbook", body=human_draft_body,
                updated_by="user_human", changed_by_type="human",
            )

        # The whole point: the agent's text is STILL THERE.
        live = await documents.get_document(
            tenant_id=self.tenant, workspace_id=self.ws, document_id=doc["id"],
        )
        self.assertIn(
            "Step two, added by the agent.", live["body"],
            "a stale human whole-body save destroyed an agent's edit — the bug this fix exists for",
        )
        self.assertNotIn("Something the person typed.", live["body"])

        # And the refusal hands back what is actually there, so the person
        # can be shown the version they would have overwritten rather than
        # only being told that one exists.
        self.assertEqual(caught.exception.current_document["body"], live["body"])
        self.assertEqual(caught.exception.current_sha256, live["state_sha256"])

        # No fabricated revision for a write that never landed — the audit
        # trail must not record the human as having written the reversion.
        revisions = await documents.list_document_revisions(
            tenant_id=self.tenant, workspace_id=self.ws, document_id=doc["id"],
        )
        self.assertEqual([r["changed_by_type"] for r in revisions][0], "agent")
        self.assertEqual(len(revisions), 2, "create + the agent's edit, and nothing from the refused save")

    async def test_rebasing_onto_the_current_state_lets_the_person_through(self):
        """"Keep my version" in the UI: the same write, retried against the
        state the conflict handed back, must land. A precondition that
        cannot be satisfied is a lock, not a safety net."""
        doc = await documents.create_document(
            tenant_id=self.tenant, workspace_id=self.ws, project_id=self.project["id"],
            title="Runbook", body="original\n", created_by="user_human",
        )
        await documents.update_document(
            tenant_id=self.tenant, workspace_id=self.ws, document_id=doc["id"],
            expected_sha256=doc["state_sha256"], body="agent wrote this\n",
            updated_by="agent_1", changed_by_type="agent",
        )
        with self.assertRaises(documents.DocumentPreconditionFailed) as caught:
            await documents.update_document(
                tenant_id=self.tenant, workspace_id=self.ws, document_id=doc["id"],
                expected_sha256=doc["state_sha256"], body="the person's version\n",
                updated_by="user_human", changed_by_type="human",
            )
        rebased = await documents.update_document(
            tenant_id=self.tenant, workspace_id=self.ws, document_id=doc["id"],
            expected_sha256=caught.exception.current_sha256,
            body="the person's version\n", updated_by="user_human", changed_by_type="human",
        )
        self.assertEqual(rebased["body"], "the person's version\n")
        # The agent's version is not lost — it is a revision, recoverable.
        revisions = await documents.list_document_revisions(
            tenant_id=self.tenant, workspace_id=self.ws, document_id=doc["id"], include_body=True,
        )
        self.assertIn("agent wrote this\n", [r["body"] for r in revisions])

    async def test_an_identical_rewrite_is_not_a_conflict(self):
        """Content-addressed, not clock-addressed: an agent that rewrote the
        document to byte-identical text changed nothing, and the person must
        not be interrupted to resolve it."""
        doc = await documents.create_document(
            tenant_id=self.tenant, workspace_id=self.ws, project_id=self.project["id"],
            title="Runbook", body="same bytes\n", created_by="user_human",
        )
        await documents.update_document(
            tenant_id=self.tenant, workspace_id=self.ws, document_id=doc["id"],
            expected_sha256=None, body="same bytes\n", updated_by="agent_1", changed_by_type="agent",
        )
        saved = await documents.update_document(
            tenant_id=self.tenant, workspace_id=self.ws, document_id=doc["id"],
            expected_sha256=doc["state_sha256"], body="the person's edit\n",
            updated_by="user_human", changed_by_type="human",
        )
        self.assertIsNotNone(saved, "an identical-content rewrite must not invalidate a person's base")

    async def test_the_sql_expression_and_the_python_mirror_agree(self):
        """The two sides of the token come from DIFFERENT sources -- the
        Python function and the database's own evaluation of the SQL
        expression the UPDATE uses. A drift between them would make every
        precondition fail, or (worse) make one silently pass."""
        doc = await documents.create_document(
            tenant_id=self.tenant, workspace_id=self.ws, project_id=self.project["id"],
            title="Weird 'title' — ünïcode\ttab", body="body with ünïcode\nand \"quotes\"\n",
            created_by="user_human",
        )
        from_database = await self.pool.fetchval(
            f"SELECT {documents._DOCUMENT_STATE_SHA256_SQL} FROM project_documents "
            "WHERE tenant_id = $1 AND workspace_id = $2 AND id = $3",
            self.tenant, self.ws, doc["id"],
        )
        self.assertEqual(from_database, doc["state_sha256"])


def _bridge(coro):
    from server_modules import sync_asyncio_bridge

    return sync_asyncio_bridge.run_coro_sync(coro)


if __name__ == "__main__":
    unittest.main()
