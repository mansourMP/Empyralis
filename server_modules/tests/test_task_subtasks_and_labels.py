"""Sub-tasks and labels -- the last two structural gaps between this board
and Linear's (migrations/add_task_parent.sql, migrations/add_task_labels.sql).

Three layers, in the order they catch things:

1. SCHEMA/DDL guards (no database needed). The orphaning decision
   (ON DELETE SET NULL, never CASCADE) and the bootstrap-ordering hazard
   that briefly broke every existing database are both asserted against the
   literal DDL text, in BOTH places it lives -- the standalone migration
   file and control_plane_repository.py's mirror -- because "the migration
   says one thing and the mirror says another" is the specific failure this
   repo's own migration comments keep warning about.

2. TOOL-SURFACE guards (no database needed). "A field was added to the
   database and the API but left unreachable by agents because the tool
   schema never advertised it" has happened twice here. These assert the
   in-house (skills_service) and external (mcp_server) tool surfaces both
   advertise sub-tasks and labels, and that the one-level constraint is
   stated in the DESCRIPTION -- a model that only learns the rule by being
   rejected burns a turn.

3. REAL-POSTGRES behaviour, skipped when no database is reachable. The
   rollup counts, the depth limit, the orphan-on-delete promise and the
   case-insensitive label uniqueness are all things a fake pool can only
   pretend to have; each of them is a property of actual SQL (a LATERAL
   aggregate, an FK action, a functional UNIQUE index), so each is tested
   against actual SQL. The agent-reachability tests in this layer drive the
   REAL native dispatch and the REAL MCP tool functions end to end, so
   "agents can reach it" is proved rather than asserted.
"""

from __future__ import annotations

import inspect
import json
import os
import unittest
import uuid
from pathlib import Path

from server_modules import control_plane_repository
from server_modules import direct_chat_operator_binding_service
from server_modules import direct_tool_config_service
from server_modules import direct_tool_execution_service
from server_modules import project_tasks_service
from server_modules import skills_service
from server_modules import workspace_labels_service

REPO_ROOT = Path(__file__).resolve().parents[2]
PARENT_MIGRATION = REPO_ROOT / "migrations" / "add_task_parent.sql"
LABELS_MIGRATION = REPO_ROOT / "migrations" / "add_task_labels.sql"


# ── Layer 1: schema / DDL guards ──────────────────────────────────────────


class SubtaskSchemaContractTests(unittest.TestCase):
    def test_parent_fk_sets_null_and_never_cascades(self):
        """THE orphaning decision. Deleting a parent must PROMOTE its
        sub-tasks to top-level, never delete them -- a sub-task is real work
        with its own assignee, comments and plan, and one click on the parent
        must not silently destroy an unbounded amount of it."""
        migration = PARENT_MIGRATION.read_text()
        mirror = control_plane_repository.CONTROL_PLANE_SCHEMA_SQL
        for name, sql in (("migration", migration), ("schema mirror", mirror)):
            with self.subTest(source=name):
                self.assertIn("REFERENCES project_tasks(id) ON DELETE SET NULL", sql)
        # The word CASCADE must not appear anywhere near parent_task_id in
        # either source. Checked as a whole-line scan rather than a substring
        # of the file (project_task_labels legitimately cascades, and
        # project_id cascades from `projects`) -- this is specifically about
        # the parent link.
        for name, sql in (("migration", migration), ("schema mirror", mirror)):
            for line in sql.splitlines():
                if "parent_task_id" in line and "REFERENCES" in line:
                    with self.subTest(source=name, line=line.strip()):
                        self.assertNotIn("CASCADE", line.upper())

    def test_parent_index_is_not_in_the_bootstrap_schema_blob(self):
        """Regression guard for a bug that crashed bootstrap on every
        already-provisioned database.

        CONTROL_PLANE_SCHEMA_SQL's `CREATE TABLE IF NOT EXISTS project_tasks`
        is a NO-OP on an existing database, so parent_task_id only arrives
        later, from the guarded ALTER in ensure_control_plane_schema()'s
        migration section. An index on that column placed in the schema blob
        therefore runs BEFORE the column exists and fails with
        `column "parent_task_id" does not exist`, taking the whole process
        down on boot. It belongs in the guarded DO block, next to the ALTER."""
        # The STATEMENT, not the name -- the blob carries a SQL comment
        # naming the index precisely to explain why it is not there.
        self.assertNotIn(
            "CREATE INDEX IF NOT EXISTS idx_project_tasks_parent",
            control_plane_repository.CONTROL_PLANE_SCHEMA_SQL,
            "idx_project_tasks_parent must not be created in CONTROL_PLANE_SCHEMA_SQL -- "
            "it indexes a column that block does not create on an existing database.",
        )
        # ...but it must still be created somewhere in the module, i.e.
        # inside the guarded self-heal. An index quietly dropped altogether
        # is the other way to get this wrong.
        source = (REPO_ROOT / "server_modules" / "control_plane_repository.py").read_text()
        self.assertIn("CREATE INDEX IF NOT EXISTS idx_project_tasks_parent", source)

    def test_migrations_are_idempotent_and_purely_additive(self):
        """Every sibling migration here is re-runnable and touches no
        existing row. These two must be as well -- they are additive by
        construction (a nullable column and two brand-new tables), and the
        guards below are what keeps them that way."""
        parent = PARENT_MIGRATION.read_text()
        labels = LABELS_MIGRATION.read_text()
        self.assertIn("ADD COLUMN IF NOT EXISTS parent_task_id", parent)
        self.assertIn("DROP CONSTRAINT IF EXISTS", parent)
        self.assertIn("CREATE INDEX IF NOT EXISTS idx_project_tasks_parent", parent)
        self.assertIn("CREATE TABLE IF NOT EXISTS workspace_labels", labels)
        self.assertIn("CREATE TABLE IF NOT EXISTS project_task_labels", labels)
        self.assertIn("CREATE UNIQUE INDEX IF NOT EXISTS uq_workspace_labels_name", labels)
        for name, sql in (("add_task_parent", parent), ("add_task_labels", labels)):
            with self.subTest(migration=name):
                # No destructive verb anywhere: an additive migration that
                # can DROP a column or DELETE a row is not additive.
                upper = sql.upper()
                self.assertNotIn("DROP TABLE", upper)
                self.assertNotIn("DROP COLUMN", upper)
                self.assertNotIn("DELETE FROM", upper)
                self.assertNotIn("TRUNCATE", upper)

    def test_label_colour_vocabulary_matches_the_database_check(self):
        """Colour is a palette TOKEN NAME, never a hex -- the frontend owns
        what each token looks like per theme (the convention task-status.tsx
        already states for --task-*). The service's list and the CHECK
        constraint must agree, in the migration AND in the mirror, or a
        colour the service accepts gets rejected by the database."""
        for token in workspace_labels_service.LABEL_COLOR_ORDER:
            with self.subTest(color=token):
                self.assertIn(f"'{token}'", LABELS_MIGRATION.read_text())
                self.assertIn(f"'{token}'", control_plane_repository.CONTROL_PLANE_SCHEMA_SQL)
        # And nothing hex-shaped is accepted.
        self.assertIsNone(workspace_labels_service._normalize_color("#ff0000", default=None))
        self.assertIsNone(workspace_labels_service._normalize_color("rgb(1,2,3)", default=None))

    def test_labels_are_workspace_scoped_not_project_scoped(self):
        """The scope decision, asserted structurally: workspace_labels has no
        project_id at all, so a label physically cannot be confined to one
        project."""
        sql = LABELS_MIGRATION.read_text()
        table = sql.split("CREATE TABLE IF NOT EXISTS workspace_labels")[1].split(");")[0]
        self.assertIn("workspace_id", table)
        self.assertNotIn("project_id", table)


# ── Layer 2: agent tool-surface guards ────────────────────────────────────


def _descriptor(tool_name: str):
    for descriptor in skills_service._builtin_tool_descriptors():
        if descriptor.tool_name == tool_name:
            return descriptor
    raise AssertionError(f"No tool descriptor named {tool_name!r}")


class AgentToolSurfaceTests(unittest.TestCase):
    """"A field was added to the database and the API but left unreachable by
    agents because the tool schema never advertised it" has happened twice in
    this repo. These are the guards against a third time."""

    def test_in_house_agents_can_create_a_subtask(self):
        create = _descriptor("project_task__create")
        self.assertIn("parent_task_id", create.parameters["properties"])

    def test_in_house_agents_can_reparent_and_read_the_rollup(self):
        self.assertIn("parent_task_id", _descriptor("project_task__set_parent").parameters["properties"])
        get_description = _descriptor("project_task__get").description
        self.assertIn("subtask_count", get_description)
        self.assertIn("subtask_done_count", get_description)

    def test_in_house_agents_can_list_and_attach_and_detach_labels(self):
        for tool_name in ("project_task__list_labels", "project_task__add_label", "project_task__remove_label"):
            with self.subTest(tool=tool_name):
                descriptor = _descriptor(tool_name)
                self.assertEqual(descriptor.connector_id, "project_task")
        for tool_name in ("project_task__add_label", "project_task__remove_label"):
            with self.subTest(tool=tool_name):
                props = _descriptor(tool_name).parameters["properties"]
                self.assertIn("task_id", props)
                self.assertIn("label", props)

    def test_single_level_constraint_is_stated_in_the_tool_descriptions(self):
        """Stated up front, not left to the error path: a model that only
        discovers the rule by being rejected burns a turn and often retries
        the same shape."""
        create_parent_doc = _descriptor("project_task__create").parameters["properties"]["parent_task_id"]["description"]
        self.assertIn("ONE level", create_parent_doc)
        self.assertIn("ONE level", _descriptor("project_task__set_parent").description)

    def test_external_mcp_agents_reach_all_of_it(self):
        import mcp_server

        for tool_name in (
            "empyralis_set_task_parent",
            "empyralis_list_labels",
            "empyralis_add_task_label",
            "empyralis_remove_task_label",
        ):
            with self.subTest(tool=tool_name):
                self.assertIn(tool_name, mcp_server.EMPYRALIST_MCP_TOOLS)

    def test_external_create_task_advertises_parent_task_id(self):
        """The MCP tool schema is generated from the function SIGNATURE, so
        the parameter has to be on the signature -- a docstring mention alone
        would leave it uncallable, which is exactly the "added to the API but
        never advertised" failure this file exists to prevent."""
        import inspect

        import mcp_server

        signature = inspect.signature(mcp_server.empyralis_create_task)
        self.assertIn("parent_task_id", signature.parameters)
        self.assertIn("ONE level of nesting", mcp_server.empyralis_create_task.__doc__)

    def test_external_label_and_parent_tools_are_callable_functions(self):
        import inspect

        import mcp_server

        for tool_name, expected_params in (
            ("empyralis_set_task_parent", ("task_id", "parent_task_id")),
            ("empyralis_list_labels", ()),
            ("empyralis_add_task_label", ("task_id", "label")),
            ("empyralis_remove_task_label", ("task_id", "label")),
        ):
            with self.subTest(tool=tool_name):
                fn = getattr(mcp_server, tool_name, None)
                self.assertIsNotNone(fn, f"{tool_name} is listed but not defined")
                params = inspect.signature(fn).parameters
                for expected in expected_params:
                    self.assertIn(expected, params)

    def test_external_get_task_documents_the_rollup(self):
        import mcp_server

        doc = mcp_server.empyralis_get_task.__doc__ or ""
        self.assertIn("subtask_count", doc)
        self.assertIn("subtask_done_count", doc)

    def test_external_agents_cannot_create_labels(self):
        """The curated-vocabulary decision, asserted as an ABSENCE: an agent
        that can mint a label on a guessed word fills the workspace with
        'bug'/'Bugs'/'bugfix' within a week."""
        import mcp_server

        self.assertNotIn("empyralis_create_label", mcp_server.EMPYRALIST_MCP_TOOLS)
        self.assertFalse(hasattr(mcp_server, "empyralis_create_label"))
        for descriptor in skills_service._builtin_tool_descriptors():
            self.assertNotEqual(descriptor.tool_name, "project_task__create_label")


# ── Layer 3: real Postgres ────────────────────────────────────────────────


def _database_url_available() -> bool:
    """Opt-in, and ONLY from an already-exported DATABASE_URL.

    An earlier revision of this file read the URL out of .env so the layer
    would run by default on a developer machine. That was wrong, and the
    symptom was immediate: setting os.environ["DATABASE_URL"] leaks
    process-wide for the rest of the pytest session, so every LATER test in
    the run silently switched from its SQLite path to real Postgres and one
    of them (test_runtime_durable_state) started failing purely because this
    file had run first. conftest.py's own dcr_client_vault docstring names
    that exact hazard -- "write real rows into whatever database DATABASE_URL
    points at ... which is exactly how these tests started failing for each
    other".

    So: no .env reading, no os.environ mutation. A plain `pytest` run skips
    this layer and leaves global state untouched. To actually exercise it:

        DATABASE_URL=postgresql://... python3 -m pytest \\
            server_modules/tests/test_task_subtasks_and_labels.py

    Purely synchronous, so it is safe to call from inside a coroutine already
    running on the bridge loop.
    """
    return bool(os.getenv("DATABASE_URL", "").strip())


async def _pool_or_none():
    """Resolved LAZILY, never at import time: conftest's autouse isolation
    fixtures have not run at collection, so an import-time decision would
    skip this layer on a perfectly good database."""
    if not _database_url_available():
        return None
    try:
        return await control_plane_repository.ensure_control_plane_schema()
    except Exception:  # noqa: BLE001 — no database is a skip, not a failure
        return None


_NO_PG_REASON = "No Postgres reachable (DATABASE_URL unset or down)"


def _run(coro):
    """Every real-database test in this file goes through the SAME persistent
    event loop -- production's own `sync_asyncio_bridge`, the one
    direct_tool_config_service.run_async_tool_call uses.

    This is not a stylistic choice. An asyncpg pool is bound to the loop that
    created it, and control_plane_repository caches one pool globally; a test
    that used `asyncio.run` per call (or IsolatedAsyncioTestCase, which mints
    a fresh loop per test) would create the pool on one loop and then use it
    from another, producing "Event loop is closed" / "another operation is in
    progress". That is exactly the pool-init leak sync_asyncio_bridge's own
    docstring says it exists to fix -- so these tests use the real fix rather
    than a test-only workaround, which also makes the agent-dispatch tests
    below run on precisely the bridge production runs them on.
    """
    from server_modules import sync_asyncio_bridge

    return sync_asyncio_bridge.run_coro_sync(coro)


class _BridgeAsyncTestCase(unittest.TestCase):
    """A plain TestCase that still lets tests be written as `async def`, but
    runs them (and async_setup/async_teardown) on the ONE bridge loop.

    Deliberately not unittest.IsolatedAsyncioTestCase, which mints a fresh
    event loop per test and would therefore orphan the cached asyncpg pool
    after the first one -- see _run above."""

    def _maybe_await(self, value):
        return _run(value) if inspect.iscoroutine(value) else value

    def _callTestMethod(self, method):
        self._maybe_await(method())

    def setUp(self):
        self._maybe_await(self.async_setup())

    def tearDown(self):
        self._maybe_await(self.async_teardown())

    async def async_setup(self):
        return None

    async def async_teardown(self):
        return None


class SubtaskAndLabelDatabaseTests(_BridgeAsyncTestCase):
    """Against real SQL, because every property here IS a property of real
    SQL: a LATERAL aggregate, an FK action, a functional UNIQUE index. A fake
    pool can only pretend to have them.

    Each test mints its own tenant/workspace/project ids, so runs never
    collide with each other or with whatever else is in the dev database, and
    tearDown removes exactly what it made."""

    async def async_setup(self):
        self.pool = await _pool_or_none()
        if self.pool is None:
            self.skipTest(_NO_PG_REASON)
        suffix = uuid.uuid4().hex[:10]
        self.tenant_id = f"t_test_{suffix}"
        self.workspace_id = f"ws_test_{suffix}"
        self.project_id = f"proj_test_{suffix}"
        self.other_project_id = f"proj_other_{suffix}"
        for project_id in (self.project_id, self.other_project_id):
            await self.pool.execute(
                """
                INSERT INTO projects (id, tenant_id, workspace_id, name, slug)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (id) DO NOTHING
                """,
                project_id, self.tenant_id, self.workspace_id, f"Test {project_id}", project_id,
            )

    async def async_teardown(self):
        # project_tasks cascades from projects; workspace_labels does not, so
        # both go explicitly. Sub-tasks are removed by the same cascade, which
        # is the documented "the project is the thing being destroyed" case.
        await self.pool.execute(
            "DELETE FROM workspace_labels WHERE tenant_id = $1 AND workspace_id = $2",
            self.tenant_id, self.workspace_id,
        )
        await self.pool.execute(
            "DELETE FROM projects WHERE tenant_id = $1 AND workspace_id = $2",
            self.tenant_id, self.workspace_id,
        )

    async def _task(self, title, *, project_id=None, parent_task_id=None, status=None):
        task = await project_tasks_service.create_task(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            project_id=project_id or self.project_id,
            title=title,
            parent_task_id=parent_task_id,
        )
        if status:
            task = await project_tasks_service.update_task(
                tenant_id=self.tenant_id, workspace_id=self.workspace_id,
                task_id=task["id"], status=status,
            )
        return task

    # ── Rollup counts ────────────────────────────────────────────────────

    async def test_rollup_counts_are_correct_and_move_with_status(self):
        """The "1/3" badge. Counts must be exact, and `done` must be the only
        status that counts as done."""
        parent = await self._task("Ship the feature")
        self.assertEqual((parent["subtask_count"], parent["subtask_done_count"]), (0, 0))

        await self._task("Step one", parent_task_id=parent["id"], status="done")
        await self._task("Step two", parent_task_id=parent["id"], status="in_review")
        await self._task("Step three", parent_task_id=parent["id"])

        refreshed = await project_tasks_service.get_task(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, task_id=parent["id"],
        )
        self.assertEqual(refreshed["subtask_count"], 3)
        # in_review is NOT done -- that is the whole point of the status
        # existing (agent-complete work parked for a human).
        self.assertEqual(refreshed["subtask_done_count"], 1)

    async def test_rollup_rides_the_list_query_without_extra_round_trips(self):
        """The counts must come back on a LIST, not just a single get --
        otherwise a board renders "0/0" on every card until something else
        goes and fetches them one at a time."""
        parent = await self._task("Parent with children")
        await self._task("Child A", parent_task_id=parent["id"], status="done")
        await self._task("Child B", parent_task_id=parent["id"])

        rows = await project_tasks_service.list_tasks(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, project_id=self.project_id,
        )
        by_id = {row["id"]: row for row in rows}
        self.assertEqual(by_id[parent["id"]]["subtask_count"], 2)
        self.assertEqual(by_id[parent["id"]]["subtask_done_count"], 1)

    async def test_top_level_only_excludes_subtasks_from_the_board(self):
        parent = await self._task("Board card")
        await self._task("Not a board card", parent_task_id=parent["id"])
        board = await project_tasks_service.list_tasks(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id,
            project_id=self.project_id, top_level_only=True,
        )
        self.assertEqual([row["id"] for row in board], [parent["id"]])
        children = await project_tasks_service.list_subtasks(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, parent_task_id=parent["id"],
        )
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0]["parent_task_id"], parent["id"])

    async def test_list_subtasks_of_nothing_returns_nothing_not_everything(self):
        await self._task("Some task")
        self.assertEqual(
            await project_tasks_service.list_subtasks(
                tenant_id=self.tenant_id, workspace_id=self.workspace_id, parent_task_id="",
            ),
            [],
        )

    # ── The single-level depth limit ─────────────────────────────────────

    async def test_cannot_parent_a_task_to_an_existing_subtask(self):
        parent = await self._task("Top level")
        child = await self._task("Middle", parent_task_id=parent["id"])
        with self.assertRaises(ValueError) as ctx:
            await self._task("Grandchild", parent_task_id=child["id"])
        self.assertIn("one level", str(ctx.exception).lower())

    async def test_cannot_demote_a_task_that_already_has_subtasks(self):
        """The same violation from the other end -- it would make the task
        the middle of a 3-level tree."""
        other_top = await self._task("Another top level")
        parent = await self._task("Has children")
        await self._task("A child", parent_task_id=parent["id"])
        with self.assertRaises(ValueError) as ctx:
            await project_tasks_service.set_task_parent(
                tenant_id=self.tenant_id, workspace_id=self.workspace_id,
                task_id=parent["id"], parent_task_id=other_top["id"],
            )
        self.assertIn("one level", str(ctx.exception).lower())

    async def test_cannot_parent_a_task_to_itself(self):
        task = await self._task("Lonely")
        with self.assertRaises(ValueError):
            await project_tasks_service.set_task_parent(
                tenant_id=self.tenant_id, workspace_id=self.workspace_id,
                task_id=task["id"], parent_task_id=task["id"],
            )

    async def test_cannot_parent_across_projects(self):
        elsewhere = await self._task("Elsewhere", project_id=self.other_project_id)
        with self.assertRaises(ValueError) as ctx:
            await self._task("Here", parent_task_id=elsewhere["id"])
        self.assertIn("different project", str(ctx.exception))

    async def test_unknown_parent_is_rejected_not_silently_ignored(self):
        with self.assertRaises(ValueError):
            await self._task("Orphan-to-be", parent_task_id="task_does_not_exist")

    async def test_a_rejected_parent_writes_no_row_at_all(self):
        """Validation runs BEFORE the INSERT, so a rejected sub-task never
        exists in a half-parented state."""
        before = await project_tasks_service.list_tasks(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, project_id=self.project_id,
        )
        with self.assertRaises(ValueError):
            await self._task("Never born", parent_task_id="task_does_not_exist")
        after = await project_tasks_service.list_tasks(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, project_id=self.project_id,
        )
        self.assertEqual(len(before), len(after))

    # ── Orphaning ────────────────────────────────────────────────────────

    async def test_deleting_a_parent_promotes_its_subtasks_and_never_deletes_them(self):
        """THE conservative choice, proved against the real FK. A sub-task is
        real work with its own assignee, comments and plan -- deleting the
        parent must not destroy it."""
        parent = await self._task("Doomed parent")
        child = await self._task("Survivor", parent_task_id=parent["id"])
        await self.pool.execute(
            "DELETE FROM project_tasks WHERE id = $1 AND tenant_id = $2",
            parent["id"], self.tenant_id,
        )
        survivor = await project_tasks_service.get_task(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, task_id=child["id"],
        )
        self.assertIsNotNone(survivor, "sub-task was cascade-deleted with its parent")
        self.assertIsNone(survivor["parent_task_id"], "sub-task was not promoted to top-level")
        self.assertEqual(survivor["title"], "Survivor")

    async def test_detaching_promotes_a_subtask_without_touching_anything_else(self):
        parent = await self._task("Parent")
        child = await self._task("Child", parent_task_id=parent["id"], status="in_progress")
        promoted = await project_tasks_service.set_task_parent(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id,
            task_id=child["id"], parent_task_id=None,
        )
        self.assertIsNone(promoted["parent_task_id"])
        self.assertEqual(promoted["status"], "in_progress")
        self.assertEqual(promoted["title"], "Child")

    # ── Labels ───────────────────────────────────────────────────────────

    async def _label(self, name, color=None):
        return await workspace_labels_service.create_label(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, name=name, color=color,
        )

    async def test_label_names_are_unique_per_workspace_case_insensitively(self):
        await self._label("Bug")
        for attempt in ("bug", "BUG", "  BuG  "):
            with self.subTest(name=attempt):
                with self.assertRaises(ValueError) as ctx:
                    await self._label(attempt)
                self.assertIn("already exists", str(ctx.exception))

    async def test_the_database_itself_enforces_case_insensitive_uniqueness(self):
        """Not just the app-layer pre-check: the functional UNIQUE index is
        what holds under a race between two concurrent creates."""
        import asyncpg

        label = await self._label("Duplicate me")
        with self.assertRaises(asyncpg.exceptions.UniqueViolationError):
            await self.pool.execute(
                "INSERT INTO workspace_labels (id, tenant_id, workspace_id, name) VALUES ($1, $2, $3, $4)",
                f"label_{uuid.uuid4().hex[:12]}", self.tenant_id, self.workspace_id,
                label["name"].upper(),
            )

    async def test_the_same_name_is_free_in_a_different_workspace(self):
        """Uniqueness is per workspace, not global -- two customers must both
        be able to have a 'bug' label."""
        await self._label("Bug")
        other_workspace = f"{self.workspace_id}_other"
        try:
            twin = await workspace_labels_service.create_label(
                tenant_id=self.tenant_id, workspace_id=other_workspace, name="bug",
            )
            self.assertEqual(twin["name"], "bug")
        finally:
            await self.pool.execute(
                "DELETE FROM workspace_labels WHERE workspace_id = $1", other_workspace,
            )

    async def test_invalid_colour_is_rejected_and_valid_tokens_round_trip(self):
        with self.assertRaises(ValueError) as ctx:
            await self._label("Hexy", color="#ff0000")
        self.assertIn("palette", str(ctx.exception).lower())
        label = await self._label("Coloured", color="Violet")
        self.assertEqual(label["color"], "violet")
        # Alias input still lands on a real token.
        aliased = await self._label("Aliased", color="gray")
        self.assertEqual(aliased["color"], "grey")

    async def test_attach_detach_shows_up_on_the_task_and_is_idempotent(self):
        task = await self._task("Labelled work")
        label = await self._label("bug", color="red")

        after_attach = await workspace_labels_service.attach_label(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id,
            task_id=task["id"], label="BUG",  # resolved case-insensitively by name
        )
        self.assertEqual([item["name"] for item in after_attach], ["bug"])
        # Attaching again is a no-op, not an error.
        again = await workspace_labels_service.attach_label(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id,
            task_id=task["id"], label=label["id"],
        )
        self.assertEqual(len(again), 1)

        # And it rides the normal task read -- the whole point.
        read_back = await project_tasks_service.get_task(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, task_id=task["id"],
        )
        self.assertEqual([item["name"] for item in read_back["labels"]], ["bug"])
        self.assertEqual(read_back["labels"][0]["color"], "red")

        after_detach = await workspace_labels_service.detach_label(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id,
            task_id=task["id"], label="bug",
        )
        self.assertEqual(after_detach, [])
        # The label itself survives -- only the link went.
        self.assertIsNotNone(
            await workspace_labels_service.get_label(
                tenant_id=self.tenant_id, workspace_id=self.workspace_id, label_id=label["id"],
            )
        )

    async def test_attaching_an_unknown_label_does_not_invent_one(self):
        task = await self._task("Untagged")
        with self.assertRaises(ValueError) as ctx:
            await workspace_labels_service.attach_label(
                tenant_id=self.tenant_id, workspace_id=self.workspace_id,
                task_id=task["id"], label="totally-made-up",
            )
        self.assertIn("does not create labels", str(ctx.exception))
        self.assertEqual(
            await workspace_labels_service.list_labels(
                tenant_id=self.tenant_id, workspace_id=self.workspace_id,
            ),
            [],
        )

    async def test_deleting_a_label_detaches_it_without_touching_the_task(self):
        task = await self._task("Keeps living")
        label = await self._label("temporary")
        await workspace_labels_service.attach_label(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id,
            task_id=task["id"], label=label["id"],
        )
        self.assertTrue(
            await workspace_labels_service.delete_label(
                tenant_id=self.tenant_id, workspace_id=self.workspace_id, label_id=label["id"],
            )
        )
        survivor = await project_tasks_service.get_task(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, task_id=task["id"],
        )
        self.assertIsNotNone(survivor, "deleting a label deleted the task")
        self.assertEqual(survivor["labels"], [])

    async def test_renaming_a_label_updates_every_task_at_once(self):
        """The property that made this a join table rather than a jsonb array
        on the task."""
        first = await self._task("One")
        second = await self._task("Two")
        label = await self._label("typo-name")
        for task in (first, second):
            await workspace_labels_service.attach_label(
                tenant_id=self.tenant_id, workspace_id=self.workspace_id,
                task_id=task["id"], label=label["id"],
            )
        await workspace_labels_service.update_label(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id,
            label_id=label["id"], name="fixed-name", color="teal",
        )
        for task in (first, second):
            read_back = await project_tasks_service.get_task(
                tenant_id=self.tenant_id, workspace_id=self.workspace_id, task_id=task["id"],
            )
            self.assertEqual([item["name"] for item in read_back["labels"]], ["fixed-name"])
            self.assertEqual(read_back["labels"][0]["color"], "teal")

    async def test_renaming_onto_an_existing_name_is_rejected_not_merged(self):
        keeper = await self._label("keep")
        other = await self._label("other")
        with self.assertRaises(ValueError):
            await workspace_labels_service.update_label(
                tenant_id=self.tenant_id, workspace_id=self.workspace_id,
                label_id=other["id"], name="KEEP",
            )
        self.assertEqual(
            (await workspace_labels_service.get_label(
                tenant_id=self.tenant_id, workspace_id=self.workspace_id, label_id=keeper["id"],
            ))["name"],
            "keep",
        )

    async def test_label_list_reports_how_many_tasks_carry_each_label(self):
        label = await self._label("counted")
        for title in ("A", "B"):
            task = await self._task(title)
            await workspace_labels_service.attach_label(
                tenant_id=self.tenant_id, workspace_id=self.workspace_id,
                task_id=task["id"], label=label["id"],
            )
        rows = await workspace_labels_service.list_labels(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id,
        )
        self.assertEqual([(r["name"], r["task_count"]) for r in rows], [("counted", 2)])

    # ── Cross-tenant scoping (this repo has a live history of leaks) ──────

    async def test_another_workspace_cannot_see_or_reparent_these_tasks(self):
        parent = await self._task("Private")
        child = await self._task("Private child", parent_task_id=parent["id"])
        stranger_workspace = f"{self.workspace_id}_stranger"
        self.assertIsNone(
            await project_tasks_service.get_task(
                tenant_id=self.tenant_id, workspace_id=stranger_workspace, task_id=parent["id"],
            )
        )
        self.assertEqual(
            await project_tasks_service.list_subtasks(
                tenant_id=self.tenant_id, workspace_id=stranger_workspace, parent_task_id=parent["id"],
            ),
            [],
        )
        self.assertIsNone(
            await project_tasks_service.set_task_parent(
                tenant_id=self.tenant_id, workspace_id=stranger_workspace,
                task_id=child["id"], parent_task_id=None,
            )
        )


# ── Layer 3b: agents actually reach it, through the real dispatch ─────────


def _callbacks():
    return direct_tool_execution_service.DirectToolExecutionCallbacks(
        compact_step_detail=lambda value: None,
        titleize_direct_step_token=lambda value: str(value or ""),
        # The REAL production bridge, not asyncio.run: an asyncpg pool is
        # bound to the loop that made it, and a fresh loop per tool call is
        # exactly the leak sync_asyncio_bridge exists to fix. Using it here
        # also means these tests exercise the same runner production does.
        run_async_tool_call=direct_tool_config_service.run_async_tool_call,
        parse_tool_name=direct_chat_operator_binding_service.parse_tool_name,
        tool_arguments_payload=lambda payload: payload if isinstance(payload, dict) else {},
        parse_json_object_loose=lambda value: {},
        safe_positive_int=lambda value, default=0: int(value) if str(value or "").strip().isdigit() else default,
        normalize_reasoning_effort=lambda value: None,
        build_direct_local_tool_config=lambda connector_id, action_id, tool_input: ("", {}),
        format_direct_local_tool_result=lambda result: json.dumps(result, ensure_ascii=False),
        build_direct_tool_config=lambda connector_id, action_id, tool_input: {
            "connector": connector_id, "action": action_id, "input": tool_input,
        },
        format_direct_tool_result=lambda result: json.dumps(result, ensure_ascii=False),
        llm_task=lambda *args, **kwargs: {"ok": True},
        web_search=lambda query: [],
        web_fetch=lambda url: "",
        search_memory_notebook=lambda *args, **kwargs: {
            "results": [], "files_searched": 0, "errors": [], "status": "no_files", "message": "",
        },
        get_memory_notebook_excerpt=lambda *args, **kwargs: {},
    )


class AgentEndToEndReachabilityTests(unittest.TestCase):
    """Drives the REAL native dispatch (tool name -> parse_tool_name ->
    skills_service.execute_single_direct_tool_call -> the real service
    functions -> real Postgres). If a tool is advertised but not wired, or
    wired but rejected by the database, this is what catches it."""

    def setUp(self):
        self.pool = _run(_pool_or_none())
        if self.pool is None:
            self.skipTest(_NO_PG_REASON)
        suffix = uuid.uuid4().hex[:10]
        self.tenant_id = f"t_e2e_{suffix}"
        self.workspace_id = f"ws_e2e_{suffix}"
        self.project_id = f"proj_e2e_{suffix}"
        self.agent_id = f"agent_e2e_{suffix}"
        _run(self._seed())

    async def _seed(self):
        await self.pool.execute(
            "INSERT INTO projects (id, tenant_id, workspace_id, name, slug) VALUES ($1, $2, $3, $4, $5)",
            self.project_id, self.tenant_id, self.workspace_id, "E2E project", self.project_id,
        )
        # NOTE: no workspace_agent_installs row is created. That table's FKs
        # require a whole agent definition + version chain, and the ONLY
        # thing the dispatch reads from it is agent_project_id() -> a single
        # project_id. _call() patches exactly that one function, so
        # everything downstream of it -- the tools, the services, the SQL --
        # is the real thing.

    def tearDown(self):
        async def _cleanup():
            await self.pool.execute(
                "DELETE FROM workspace_labels WHERE tenant_id = $1 AND workspace_id = $2",
                self.tenant_id, self.workspace_id,
            )
            await self.pool.execute(
                "DELETE FROM projects WHERE tenant_id = $1 AND workspace_id = $2",
                self.tenant_id, self.workspace_id,
            )

        _run(_cleanup())

    def _call(self, tool_name, arguments):
        from unittest.mock import AsyncMock, patch

        with patch(
            "server_modules.project_tasks_service.agent_project_id",
            new=AsyncMock(return_value=self.project_id),
        ):
            raw = skills_service.execute_single_direct_tool_call(
                tool_call={"name": tool_name, "arguments": arguments},
                workspace_id=self.workspace_id,
                thread_id="thread-e2e",
                session_ctx={"agent_install_id": self.agent_id, "tenant_id": self.tenant_id},
                callbacks=_callbacks(),
            )
        return json.loads(raw)

    def test_an_agent_can_create_a_subtask_and_read_its_own_rollup(self):
        parent = self._call("project_task__create", {"title": "Agent parent"})["task"]
        child = self._call(
            "project_task__create", {"title": "Agent child", "parent_task_id": parent["id"]},
        )["task"]
        self.assertEqual(child["parent_task_id"], parent["id"])

        self._call("project_task__update", {"task_id": child["id"], "status": "done"})
        read_back = self._call("project_task__get", {"task_id": parent["id"]})
        self.assertEqual(read_back["task"]["subtask_count"], 1)
        self.assertEqual(read_back["task"]["subtask_done_count"], 1)
        self.assertEqual([t["id"] for t in read_back["subtasks"]], [child["id"]])

    def test_an_agent_hits_the_depth_limit_with_an_actionable_error(self):
        parent = self._call("project_task__create", {"title": "Top"})["task"]
        child = self._call(
            "project_task__create", {"title": "Middle", "parent_task_id": parent["id"]},
        )["task"]
        with self.assertRaises(RuntimeError) as ctx:
            self._call(
                "project_task__create", {"title": "Too deep", "parent_task_id": child["id"]},
            )
        self.assertIn("one level", str(ctx.exception).lower())

    def test_an_agent_can_reparent_and_detach(self):
        first = self._call("project_task__create", {"title": "First"})["task"]
        loose = self._call("project_task__create", {"title": "Loose"})["task"]
        filed = self._call(
            "project_task__set_parent", {"task_id": loose["id"], "parent_task_id": first["id"]},
        )["task"]
        self.assertEqual(filed["parent_task_id"], first["id"])
        detached = self._call("project_task__set_parent", {"task_id": loose["id"]})["task"]
        self.assertIsNone(detached["parent_task_id"])

    def test_an_agent_can_list_attach_and_detach_labels(self):
        label = _run(
            workspace_labels_service.create_label(
                tenant_id=self.tenant_id, workspace_id=self.workspace_id,
                name="Bug", color="red",
            )
        )
        task = self._call("project_task__create", {"title": "Buggy"})["task"]

        listed = self._call("project_task__list_labels", {})
        self.assertEqual([item["name"] for item in listed["labels"]], ["Bug"])

        # By NAME, lower-cased -- the shape a model actually produces.
        attached = self._call("project_task__add_label", {"task_id": task["id"], "label": "bug"})
        self.assertEqual([item["name"] for item in attached["labels"]], ["Bug"])

        read_back = self._call("project_task__get", {"task_id": task["id"]})
        self.assertEqual([item["id"] for item in read_back["task"]["labels"]], [label["id"]])

        removed = self._call("project_task__remove_label", {"task_id": task["id"], "label": "bug"})
        self.assertEqual(removed["labels"], [])

    def test_an_agent_cannot_invent_a_label(self):
        task = self._call("project_task__create", {"title": "Untagged"})["task"]
        with self.assertRaises(RuntimeError) as ctx:
            self._call("project_task__add_label", {"task_id": task["id"], "label": "invented"})
        self.assertIn("does not create labels", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
