"""Documents are GitHub-shaped: a `path` with slashes, folders INFERRED.

Covers the three things that pass added and that nothing else asserts:
the path normalizer, list_documents' fail-closed scope, and the
agent_document_scope_service resolver that finally lets the workspace-level
Operator read a document at all.
"""

import asyncio
import unittest

from server_modules import agent_document_scope_service as scope_service
from server_modules import project_documents_repository as documents


def _run(coro):
    return asyncio.run(coro)


class NormalizeDocumentPathTests(unittest.TestCase):
    def test_a_plain_title_becomes_a_markdown_file(self):
        self.assertEqual(documents.normalize_document_path("Runbook"), "runbook.md")

    def test_slashes_are_structure_and_survive(self):
        self.assertEqual(
            documents.normalize_document_path("Specs/API/Auth Notes"),
            "specs/api/auth-notes.md",
        )

    def test_an_explicit_extension_is_kept(self):
        self.assertEqual(documents.normalize_document_path("specs/auth.md"), "specs/auth.md")
        self.assertEqual(documents.normalize_document_path("notes/data.csv"), "notes/data.csv")

    def test_empty_leading_and_duplicate_separators_collapse(self):
        self.assertEqual(documents.normalize_document_path("/specs//api/"), "specs/api.md")

    def test_traversal_cannot_survive_normalization(self):
        """`..` is removed as STRUCTURE, before any character rule -- the
        reason the normalizer splits on '/' first instead of running one
        regex over the whole string."""
        self.assertEqual(documents.normalize_document_path("../../etc/passwd"), "etc/passwd.md")
        self.assertNotIn("..", documents.normalize_document_path("a/../../b"))

    def test_a_backslash_is_a_separator_too(self):
        self.assertEqual(documents.normalize_document_path(r"specs\api\auth"), "specs/api/auth.md")

    def test_empty_input_still_yields_a_usable_path(self):
        self.assertEqual(documents.normalize_document_path(""), "document.md")
        self.assertEqual(documents.normalize_document_path(None), "document.md")

    def test_split_is_parent_and_name(self):
        self.assertEqual(documents.split_document_path("specs/api/auth.md"), ("specs/api", "auth.md"))
        self.assertEqual(
            documents.split_document_path("auth.md"), ("", "auth.md"),
            "a root-level document has no parent prefix -- the case a naive rsplit gets wrong",
        )


class UniquePathSuffixTests(unittest.TestCase):
    """The suffix goes BEFORE the extension, and never moves the document to
    a different folder."""

    def _unique(self, existing, base):
        from server_modules import control_plane_repository as cp

        async def fake_rls_fetch(pool, query, *args, **kwargs):
            return [{"path": p} for p in existing]

        async def go():
            original = cp.rls_fetch
            cp.rls_fetch = fake_rls_fetch
            try:
                return await documents._unique_path(
                    object(), tenant_id="t", workspace_id="w",
                    project_id="p", base=base,
                )
            finally:
                cp.rls_fetch = original
        return _run(go())

    def test_no_clash_returns_the_base(self):
        self.assertEqual(self._unique([], "specs/auth.md"), "specs/auth.md")

    def test_suffix_lands_before_the_extension(self):
        self.assertEqual(
            self._unique(["specs/auth.md"], "specs/auth.md"), "specs/auth-2.md",
            "auth.md-2 would be a name with no usable extension",
        )

    def test_suffix_walks_until_free_and_keeps_the_folder(self):
        got = self._unique(["specs/auth.md", "specs/auth-2.md"], "specs/auth.md")
        self.assertEqual(got, "specs/auth-3.md")
        self.assertTrue(got.startswith("specs/"), "a clash is resolved inside its own folder")


class ListDocumentsScopeFailsClosedTests(unittest.IsolatedAsyncioTestCase):
    """An unscoped document read must be impossible to ask for by accident --
    the posture CLAUDE.md records after the fail-open `WHERE ($1 = '' OR ...)`
    family."""

    async def test_neither_scope_raises(self):
        with self.assertRaises(ValueError) as ctx:
            await documents.list_documents(tenant_id="t", workspace_id="w")
        self.assertIn("exactly one", str(ctx.exception))

    async def test_both_scopes_raises(self):
        with self.assertRaises(ValueError):
            await documents.list_documents(
                tenant_id="t", workspace_id="w", project_id="p", project_ids=["p"],
            )

    async def test_empty_project_ids_returns_nothing_not_everything(self):
        """'This caller may see no project' is a real answer, and the only
        safe one -- widening here would be the disclosure."""
        self.assertEqual(
            await documents.list_documents(tenant_id="t", workspace_id="w", project_ids=[]),
            [],
        )


class AgentDocumentScopeResolverTests(unittest.IsolatedAsyncioTestCase):
    """The seam MAN-357 rewrites. Behaviour asserted, not implementation."""

    async def test_a_specialist_reads_and_writes_its_own_project_in_one_query(self):
        calls = []

        async def fake_agent_project_id(*, tenant_id, workspace_id, agent_id):
            calls.append(agent_id)
            return "proj-1"

        import server_modules.project_tasks_service as tasks
        original = tasks.agent_project_id
        tasks.agent_project_id = fake_agent_project_id
        try:
            got = await scope_service.resolve_agent_document_project_scope(
                tenant_id="t", workspace_id="w", agent_install_id="agent-1", user_id="user-1",
            )
        finally:
            tasks.agent_project_id = original
        self.assertEqual(got.project_ids, ["proj-1"])
        self.assertEqual(got.own_project_id, "proj-1")
        self.assertEqual(
            len(calls), 1,
            "the read scope must not be a SECOND lookup of a fact already in hand",
        )

    async def test_an_agent_with_no_project_and_no_user_gets_nothing(self):
        """Fails CLOSED. The Operator with no resolvable human must not
        inherit the workspace."""
        async def fake_agent_project_id(**_kwargs):
            return None

        import server_modules.project_tasks_service as tasks
        original = tasks.agent_project_id
        tasks.agent_project_id = fake_agent_project_id
        try:
            got = await scope_service.resolve_agent_document_project_scope(
                tenant_id="t", workspace_id="w", agent_install_id="agent-1", user_id="",
            )
        finally:
            tasks.agent_project_id = original
        self.assertEqual(got.project_ids, [])
        self.assertEqual(got.own_project_id, "", "no own project means writes stay refused")

    async def test_the_operator_inherits_the_asking_persons_projects_and_may_not_write(self):
        """The whole point of 'connected by default': an install with no
        project of its own reads what the PERSON could open, and nothing
        wider -- and still cannot write, because there is no single target."""
        async def fake_agent_project_id(**_kwargs):
            return None

        async def fake_visible(*, tenant_id, workspace_id, user_id):
            return ["proj-a", "proj-b"]

        import server_modules.project_tasks_service as tasks
        original = tasks.agent_project_id
        original_visible = scope_service._project_ids_visible_to_user
        tasks.agent_project_id = fake_agent_project_id
        scope_service._project_ids_visible_to_user = fake_visible
        try:
            got = await scope_service.resolve_agent_document_project_scope(
                tenant_id="t", workspace_id="w", agent_install_id="agent-1", user_id="user-1",
            )
        finally:
            tasks.agent_project_id = original
            scope_service._project_ids_visible_to_user = original_visible
        self.assertEqual(got.project_ids, ["proj-a", "proj-b"])
        self.assertEqual(got.own_project_id, "")


class DocumentTreeIndexTests(unittest.TestCase):
    """Push the INDEX, pull the content."""

    def test_the_index_carries_paths_and_never_bodies(self):
        text = scope_service.document_tree_index([
            {"path": "specs/api/auth.md", "project_id": "proj-1", "body": "SECRET BODY"},
            {"path": "notes/standup.md", "project_id": "proj-1", "body": "ALSO SECRET"},
        ])
        self.assertIn("specs/api/auth.md", text)
        self.assertIn("notes/standup.md", text)
        self.assertNotIn("SECRET BODY", text)
        self.assertNotIn("ALSO SECRET", text)

    def test_an_empty_set_produces_no_block_at_all(self):
        self.assertEqual(scope_service.document_tree_index([]), "")

    def test_truncation_announces_itself(self):
        """A silently cut index would have an agent conclude a document does
        not exist."""
        docs = [{"path": f"notes/n{i}.md", "project_id": "p"} for i in range(10)]
        text = scope_service.document_tree_index(docs, max_entries=3)
        self.assertIn("and 7 more", text)


if __name__ == "__main__":
    unittest.main()


class DocumentActivityFeedScopeTests(unittest.IsolatedAsyncioTestCase):
    """The cross-document commit log. Every fact it shows was already being
    recorded; the only reader ended `AND document_id = $x`, so a revision was
    invisible unless you already knew which document to open."""

    async def test_neither_scope_raises(self):
        with self.assertRaises(ValueError) as ctx:
            await documents.list_project_document_activity(tenant_id="t", workspace_id="w")
        self.assertIn("exactly one", str(ctx.exception))

    async def test_both_scopes_raises(self):
        with self.assertRaises(ValueError):
            await documents.list_project_document_activity(
                tenant_id="t", workspace_id="w", project_id="p", project_ids=["p"],
            )

    async def test_empty_scope_returns_nothing_not_everything(self):
        self.assertEqual(
            await documents.list_project_document_activity(
                tenant_id="t", workspace_id="w", project_ids=[],
            ),
            [],
        )

    async def test_the_query_is_scoped_by_tenant_workspace_and_project(self):
        """Scope is bound to the statement unconditionally -- never a
        caller-supplied id trusted straight through, and no fail-open
        `$1 = '' OR` shape."""
        from server_modules import control_plane_repository as cp

        seen = {}

        async def fake_rls_fetch(pool, query, *args, **kwargs):
            seen["query"] = query
            seen["args"] = args
            seen["kwargs"] = kwargs
            return []

        async def fake_pool():
            return object()

        original_fetch, original_ensure = cp.rls_fetch, cp.ensure_control_plane_schema
        cp.rls_fetch = fake_rls_fetch
        cp.ensure_control_plane_schema = fake_pool
        try:
            await documents.list_project_document_activity(
                tenant_id="tenant-1", workspace_id="ws-1", project_ids=["proj-a", "proj-b"],
            )
        finally:
            cp.rls_fetch, cp.ensure_control_plane_schema = original_fetch, original_ensure

        q = seen["query"]
        self.assertIn("r.tenant_id = $1", q)
        self.assertIn("r.workspace_id = $2", q)
        self.assertIn("r.project_id = ANY($3::text[])", q)
        self.assertNotIn("$1 = ''", q, "no fail-open scope filter")
        self.assertEqual(seen["args"][0], "tenant-1")
        self.assertEqual(seen["args"][1], "ws-1")
        self.assertEqual(seen["args"][2], ["proj-a", "proj-b"])
        self.assertEqual(seen["kwargs"].get("tenant_id"), "tenant-1")
        self.assertEqual(seen["kwargs"].get("workspace_id"), "ws-1")

    async def test_the_feed_never_carries_bodies(self):
        """A feed answers who/what/when. `diff` is the payload; a full body
        snapshot across every document is not."""
        from server_modules import control_plane_repository as cp

        seen = {}

        async def fake_rls_fetch(pool, query, *args, **kwargs):
            seen["query"] = query
            return []

        async def fake_pool():
            return object()

        original_fetch, original_ensure = cp.rls_fetch, cp.ensure_control_plane_schema
        cp.rls_fetch = fake_rls_fetch
        cp.ensure_control_plane_schema = fake_pool
        try:
            await documents.list_project_document_activity(
                tenant_id="t", workspace_id="w", project_id="p",
            )
        finally:
            cp.rls_fetch, cp.ensure_control_plane_schema = original_fetch, original_ensure
        self.assertNotIn("r.body", seen["query"])
        self.assertIn("r.diff", seen["query"], "the diff IS the feed entry")
        self.assertIn(
            "r.changed_by_type", seen["query"],
            "human vs agent is the distinction that makes this feed worth more than GitHub's",
        )
