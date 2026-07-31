"""Per-user notifications (MAN-146) -- migrations/add_task_notifications.sql,
task_notification_service.py, the three producers it wires into
(task_mention_service.dispatch_resolved_mentions, project_tasks_service.
assign_task_to_user, project_tasks_service.add_task_comment), and the new
GET/POST /api/w/{workspace_id}/fleet/notifications routes.

THE BUG THIS CLOSES (additively). outbox_service.emit_notification_event's
OutboxEvent carries only tenant/workspace, so GET /notifications
(runtime_events_api.py) broadcasts every "you were mentioned" to the whole
workspace -- task_mention_service.py:495's own metadata.mentioned_user_id
stamp is written and never read. This file proves the NEW, correctly-
scoped mechanism this change adds instead: a real Postgres row addressed to
exactly one recipient_user_id, filtered by recipient AND workspace/tenant
on every read.

NOT a fix to the broadcast itself: server_modules/tests/test_task_mention_
service.py::MentionDispatchTests::test_mentioning_a_human_notifies_and_
never_calls_the_scheduler hard-asserts outbox_service.emit_notification_
event still fires for a human mention, and this change's own collision
protocol forbids editing existing test files. See task_notification_
service.py's own module docstring for the full reasoning.

FOUR LAYERS, same convention test_task_completion_attribution.py and
test_project_tasks_human_assignee.py already established in this suite:

1. UNIT (no database) -- create_notification's input validation (reject
   rather than silently write a malformed row).

2. SCHEMA / DDL guards (no database) -- the migration, the
   control_plane_repository.py mirror, and migrations/enable_rls.sql agree
   on the table shape, the FKs, the CHECK, and that RLS is both listed AND
   actually correct (the exact gap the brief calls out: "preflight refuses
   to boot if a listed table lacks RLS").

3. REAL POSTGRES producer + list/read proofs, skipped when unreachable
   (opt-in DATABASE_URL, this suite's own convention). Each producer is
   driven through its REAL, PUBLIC caller (dispatch_resolved_mentions,
   assign_task_to_user, add_task_comment) -- never a hand-rolled INSERT --
   so this proves the WIRING, not just that create_notification itself
   works in isolation.

4. REAL POSTGRES, throwaway non-superuser role -- the database-level RLS
   isolation proof, exactly test_rls_project_task_labels_isolation.py's own
   methodology (duplicated rather than imported, per that file's own
   reasoning: a pre-existing test file this change does not own must not
   become a dependency of a new one).

Real Postgres only for layers 3-4, opt-in from an already-exported
DATABASE_URL (never read from .env, never mutated into os.environ here).
Every row and role this file creates is removed in tearDown, even on a
partial failure.
"""

from __future__ import annotations

import inspect
import os
import unittest
import uuid
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit, urlunsplit

from server_modules import control_plane_repository
from server_modules import project_tasks_service
from server_modules import task_mention_service
from server_modules import task_notification_service

REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]
NOTIFICATIONS_MIGRATION = REPO_ROOT / "migrations" / "add_task_notifications.sql"
ENABLE_RLS_MIGRATION = REPO_ROOT / "migrations" / "enable_rls.sql"


# ── Layer 1: unit tests, no database ────────────────────────────────────


class CreateNotificationValidationTests(unittest.IsolatedAsyncioTestCase):
    """Both checks below happen BEFORE create_notification ever fetches a
    pool (mirrors update_task's own actor-validation-before-pool-fetch
    posture, and project_tasks_service.assign_task_to_user's user_id-
    required check) -- a caller bug is rejected the same way regardless of
    whether Postgres happens to be configured in this environment."""

    async def test_rejects_invalid_source_event_type(self):
        with self.assertRaises(ValueError):
            await task_notification_service.create_notification(
                tenant_id="tenant-1", workspace_id="ws-1", recipient_user_id="user-1",
                source_event_type="not_a_real_event", body="hi",
            )

    async def test_rejects_missing_recipient(self):
        with self.assertRaises(ValueError):
            await task_notification_service.create_notification(
                tenant_id="tenant-1", workspace_id="ws-1", recipient_user_id="   ",
                source_event_type=task_notification_service.SOURCE_EVENT_MENTION, body="hi",
            )

    async def test_rejects_missing_tenant_or_workspace(self):
        with self.assertRaises(ValueError):
            await task_notification_service.create_notification(
                tenant_id="", workspace_id="ws-1", recipient_user_id="user-1",
                source_event_type=task_notification_service.SOURCE_EVENT_MENTION, body="hi",
            )

    async def test_no_database_returns_none_without_raising(self):
        with patch(
            "server_modules.task_notification_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=None),
        ):
            result = await task_notification_service.create_notification(
                tenant_id="tenant-1", workspace_id="ws-1", recipient_user_id="user-1",
                source_event_type=task_notification_service.SOURCE_EVENT_MENTION, body="hi",
            )
        self.assertIsNone(result)

    async def test_list_notifications_without_database_returns_empty_list(self):
        with patch(
            "server_modules.task_notification_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=None),
        ):
            result = await task_notification_service.list_notifications(
                tenant_id="tenant-1", workspace_id="ws-1", recipient_user_id="user-1",
            )
        self.assertEqual(result, [])


class TaskDeepLinkTests(unittest.TestCase):
    def test_builds_the_same_shape_dispatch_resolved_mentions_already_used(self):
        self.assertEqual(task_notification_service.task_deep_link("task-1"), "/tasks/task-1")

    def test_blank_task_id_returns_none(self):
        self.assertIsNone(task_notification_service.task_deep_link(""))


# ── Layer 2: schema / DDL guards, no database ───────────────────────────


class NotificationsSchemaContractTests(unittest.TestCase):
    def test_recipient_is_not_null_and_cascades(self):
        """A notification with no recipient is not a per-user notification
        at all -- NOT NULL backstops create_notification's own ValueError.
        CASCADE (not SET NULL, unlike completed_by_user_id) -- an orphaned
        notification with no recipient would violate the NOT NULL anyway,
        so cascading the delete is the only coherent choice."""
        migration = NOTIFICATIONS_MIGRATION.read_text()
        mirror = control_plane_repository.CONTROL_PLANE_SCHEMA_SQL
        for name, sql in (("migration", migration), ("schema mirror", mirror)):
            with self.subTest(source=name):
                self.assertIn("recipient_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE", sql)

    def test_source_event_type_vocabulary_matches_the_three_producers(self):
        migration = NOTIFICATIONS_MIGRATION.read_text()
        mirror = control_plane_repository.CONTROL_PLANE_SCHEMA_SQL
        for name, sql in (("migration", migration), ("schema mirror", mirror)):
            with self.subTest(source=name):
                self.assertIn("'task_mention', 'task_assigned', 'task_comment'", sql)
        self.assertEqual(
            set(task_notification_service.VALID_SOURCE_EVENT_TYPES),
            {"task_mention", "task_assigned", "task_comment"},
        )

    def test_table_is_purely_additive(self):
        sql = NOTIFICATIONS_MIGRATION.read_text()
        self.assertIn("CREATE TABLE IF NOT EXISTS task_notifications", sql)
        upper = sql.upper()
        self.assertNotIn("DROP TABLE", upper)
        self.assertNotIn("ALTER TABLE PROJECT_TASKS", upper)
        self.assertNotIn("DELETE FROM", upper)

    def test_read_at_column_exists_as_the_one_unread_mechanism(self):
        migration = NOTIFICATIONS_MIGRATION.read_text()
        mirror = control_plane_repository.CONTROL_PLANE_SCHEMA_SQL
        for name, sql in (("migration", migration), ("schema mirror", mirror)):
            with self.subTest(source=name):
                self.assertIn("read_at TIMESTAMPTZ NULL", sql)


class NotificationsRlsListingContractTests(unittest.TestCase):
    """THE brief's own warning: "preflight refuses to boot if a listed
    table lacks RLS -- that took production down once tonight." This class
    proves the static claim (the table is listed correctly, with a real
    policy) without needing a live database -- layer 4 below proves it is
    ALSO true against real Postgres."""

    def test_task_notifications_is_listed_with_enable_and_force_and_a_policy(self):
        sql = ENABLE_RLS_MIGRATION.read_text()
        self.assertIn("ALTER TABLE task_notifications ENABLE ROW LEVEL SECURITY;", sql)
        self.assertIn("ALTER TABLE task_notifications FORCE ROW LEVEL SECURITY;", sql)
        self.assertIn("CREATE POLICY empyralis_task_notifications_scope ON task_notifications", sql)

    def test_preflights_own_table_list_parser_picks_it_up(self):
        """Exercises the SAME regex server_modules/preflight.py's
        _tenant_scoped_tables_from_migration uses, so this test breaks if
        that parser and this migration's ALTER TABLE spelling ever drift
        apart, instead of the drift only surfacing at boot time."""
        import re

        sql = ENABLE_RLS_MIGRATION.read_text()
        matches = re.findall(
            r"ALTER\s+TABLE\s+(?:public\.)?([a-zA-Z_][a-zA-Z0-9_]*)\s+ENABLE\s+ROW\s+LEVEL\s+SECURITY",
            sql, flags=re.IGNORECASE,
        )
        self.assertIn("task_notifications", {name.lower() for name in matches})


# ── Layer 3: real Postgres -- producers, listing, the HTTP routes ──────


def _database_url_available() -> bool:
    return bool(os.getenv("DATABASE_URL", "").strip())


_NO_PG_REASON = "No Postgres reachable (DATABASE_URL unset or down)"


def _run(coro):
    """Same persistent bridge loop this suite's other real-Postgres files
    use -- the cached asyncpg pool is bound to the loop that created it."""
    from server_modules import sync_asyncio_bridge

    return sync_asyncio_bridge.run_coro_sync(coro)


class _BridgeAsyncTestCase(unittest.TestCase):
    def _maybe_await(self, value):
        return _run(value) if inspect.iscoroutine(value) else value

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
        return None

    async def async_teardown(self):
        return None


class NotificationProducersDatabaseTests(_BridgeAsyncTestCase):
    """One workspace, three members (alice files/owns, bob gets assigned,
    carol is the third party who mentions/comments), one task. Each test
    drives a REAL producer function end-to-end and reads back through
    task_notification_service.list_notifications -- never a raw SQL
    assertion -- so this proves the wiring a route or a tool actually
    exercises, not a hand-built row."""

    async def async_setup(self):
        self.pool = await self._pool_or_skip()
        suffix = uuid.uuid4().hex[:10]
        self.tenant_id = f"t_notif_{suffix}"
        self.workspace_id = f"ws_notif_{suffix}"
        self.project_id = f"proj_notif_{suffix}"
        self.alice_id = f"user_alice_{suffix}"
        self.bob_id = f"user_bob_{suffix}"
        self.carol_id = f"user_carol_{suffix}"
        await self.pool.execute(
            "INSERT INTO projects (id, tenant_id, workspace_id, name, slug) VALUES ($1, $2, $3, $4, $1)",
            self.project_id, self.tenant_id, self.workspace_id, "Notifications test project",
        )
        for user_id, display_name in (
            (self.alice_id, "Alice"), (self.bob_id, "Bob"), (self.carol_id, "Carol"),
        ):
            await self.pool.execute(
                "INSERT INTO users (id, tenant_id, workspace_id, email, display_name) VALUES ($1, $2, $3, $4, $5)",
                user_id, self.tenant_id, self.workspace_id, f"{user_id}@example.com", display_name,
            )
            await self.pool.execute(
                """
                INSERT INTO workspace_memberships (id, tenant_id, workspace_id, user_id, role, status)
                VALUES ($1, $2, $3, $4, 'member', 'active')
                """,
                f"membership_{user_id}", self.tenant_id, self.workspace_id, user_id,
            )
        self.task = await project_tasks_service.create_task(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id,
            project_id=self.project_id, title="Ship the widget", created_by=self.alice_id,
        )

    async def _pool_or_skip(self):
        if not _database_url_available():
            self.skipTest(_NO_PG_REASON)
        pool = await control_plane_repository.ensure_control_plane_schema()
        if pool is None:
            self.skipTest(_NO_PG_REASON)
        return pool

    async def async_teardown(self):
        pool = getattr(self, "pool", None)
        if pool is None:
            return
        await pool.execute("DELETE FROM task_notifications WHERE tenant_id = $1", self.tenant_id)
        await pool.execute("DELETE FROM projects WHERE tenant_id = $1", self.tenant_id)
        await pool.execute("DELETE FROM users WHERE tenant_id = $1", self.tenant_id)

    # -- 1. Mention producer -------------------------------------------

    async def test_mention_producer_notifies_only_the_mentioned_user(self):
        await task_mention_service.dispatch_resolved_mentions(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id,
            task_id=self.task["id"], task_title=self.task["title"],
            resolved_mentions=[{"kind": "user", "id": self.bob_id, "display_name": "Bob"}],
            author_type="human", author_id=self.carol_id,
        )
        bobs = await task_notification_service.list_notifications(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, recipient_user_id=self.bob_id,
        )
        self.assertEqual(len(bobs), 1)
        self.assertEqual(bobs[0]["source_event_type"], "task_mention")
        self.assertEqual(bobs[0]["task_id"], self.task["id"])
        self.assertEqual(bobs[0]["actor_id"], self.carol_id)
        self.assertIsNone(bobs[0]["read_at"])
        # A second workspace member (alice -- present, active, but never
        # mentioned) must see nothing from this mention.
        alices = await task_notification_service.list_notifications(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, recipient_user_id=self.alice_id,
        )
        self.assertEqual(alices, [])

    async def test_mention_flood_bound_holds(self):
        """The bounding decision under test: mentioning MORE distinct
        humans than task_mention_service.max_mentioned_human_notifications_
        per_comment() notifies only the first N -- one comment can never
        flood an unbounded number of inboxes. Patches the cap to a small
        number rather than minting the default's worth of extra users, same
        "shrink the constant, not the proof" approach test_task_mention_
        service.py's own bound test takes with the AGENT cap."""
        extra_suffix = uuid.uuid4().hex[:8]
        extra_user_ids = []
        for i in range(4):
            uid = f"user_flood_{extra_suffix}_{i}"
            await self.pool.execute(
                "INSERT INTO users (id, tenant_id, workspace_id, email, display_name) VALUES ($1, $2, $3, $4, $5)",
                uid, self.tenant_id, self.workspace_id, f"{uid}@example.com", f"Flood{i}",
            )
            await self.pool.execute(
                """
                INSERT INTO workspace_memberships (id, tenant_id, workspace_id, user_id, role, status)
                VALUES ($1, $2, $3, $4, 'member', 'active')
                """,
                f"membership_{uid}", self.tenant_id, self.workspace_id, uid,
            )
            extra_user_ids.append(uid)
        try:
            mentions = [{"kind": "user", "id": uid, "display_name": uid} for uid in extra_user_ids]
            with patch(
                "server_modules.task_mention_service.max_mentioned_human_notifications_per_comment",
                return_value=2,
            ):
                await task_mention_service.dispatch_resolved_mentions(
                    tenant_id=self.tenant_id, workspace_id=self.workspace_id,
                    task_id=self.task["id"], task_title=self.task["title"],
                    resolved_mentions=mentions, author_type="human", author_id=self.carol_id,
                )
            notified_count = 0
            for uid in extra_user_ids:
                rows = await task_notification_service.list_notifications(
                    tenant_id=self.tenant_id, workspace_id=self.workspace_id, recipient_user_id=uid,
                )
                notified_count += len(rows)
            self.assertEqual(notified_count, 2, "the bound must cap notified recipients, not just wake targets")
        finally:
            for uid in extra_user_ids:
                await self.pool.execute("DELETE FROM users WHERE id = $1", uid)

    # -- 2. Assignment producer ------------------------------------------

    async def test_assignment_producer_notifies_the_new_assignee(self):
        await project_tasks_service.assign_task_to_user(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id,
            task_id=self.task["id"], user_id=self.bob_id, triggered_by=self.alice_id,
        )
        bobs = await task_notification_service.list_notifications(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, recipient_user_id=self.bob_id,
        )
        self.assertEqual(len(bobs), 1)
        self.assertEqual(bobs[0]["source_event_type"], "task_assigned")
        self.assertEqual(bobs[0]["actor_id"], self.alice_id)

    async def test_self_assignment_notifies_no_one(self):
        await project_tasks_service.assign_task_to_user(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id,
            task_id=self.task["id"], user_id=self.bob_id, triggered_by=self.bob_id,
        )
        bobs = await task_notification_service.list_notifications(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, recipient_user_id=self.bob_id,
        )
        self.assertEqual(bobs, [])

    # -- 3. Comment-on-an-owned-task producer -----------------------------

    async def test_comment_notifies_both_filer_and_assignee_excluding_commenter(self):
        # Assigning bob is itself a producer (source_event_type=
        # "task_assigned") -- filter every assertion below to "task_comment"
        # so that unrelated notification doesn't get counted as this
        # producer's output.
        await project_tasks_service.assign_task_to_user(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id,
            task_id=self.task["id"], user_id=self.bob_id, triggered_by=self.alice_id,
        )
        await project_tasks_service.add_task_comment(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, task_id=self.task["id"],
            author_type="human", author_id=self.carol_id, body="status update, looking good",
        )
        alice_comments = [
            n for n in await task_notification_service.list_notifications(
                tenant_id=self.tenant_id, workspace_id=self.workspace_id, recipient_user_id=self.alice_id,
            ) if n["source_event_type"] == "task_comment"
        ]
        bob_comments = [
            n for n in await task_notification_service.list_notifications(
                tenant_id=self.tenant_id, workspace_id=self.workspace_id, recipient_user_id=self.bob_id,
            ) if n["source_event_type"] == "task_comment"
        ]
        carols = await task_notification_service.list_notifications(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, recipient_user_id=self.carol_id,
        )
        self.assertEqual(len(alice_comments), 1)
        self.assertEqual(len(bob_comments), 1)
        self.assertEqual(carols, [], "the commenter must never notify themselves")

    async def test_comment_by_the_filer_only_notifies_the_assignee(self):
        await project_tasks_service.assign_task_to_user(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id,
            task_id=self.task["id"], user_id=self.bob_id, triggered_by=self.alice_id,
        )
        await project_tasks_service.add_task_comment(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, task_id=self.task["id"],
            author_type="human", author_id=self.alice_id, body="just circling back",
        )
        alice_comments = [
            n for n in await task_notification_service.list_notifications(
                tenant_id=self.tenant_id, workspace_id=self.workspace_id, recipient_user_id=self.alice_id,
            ) if n["source_event_type"] == "task_comment"
        ]
        bob_comments = [
            n for n in await task_notification_service.list_notifications(
                tenant_id=self.tenant_id, workspace_id=self.workspace_id, recipient_user_id=self.bob_id,
            ) if n["source_event_type"] == "task_comment"
        ]
        self.assertEqual(alice_comments, [], "the filer must not notify themselves for their own comment")
        self.assertEqual(len(bob_comments), 1)

    async def test_comment_when_filer_and_assignee_are_the_same_person_dedupes(self):
        """Self-assigning alice's own task means created_by == assignee_
        user_id -- carol's comment must produce exactly ONE notification
        for alice, not two."""
        await project_tasks_service.assign_task_to_user(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id,
            task_id=self.task["id"], user_id=self.alice_id, triggered_by=self.alice_id,
        )
        await project_tasks_service.add_task_comment(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, task_id=self.task["id"],
            author_type="human", author_id=self.carol_id, body="quick question",
        )
        alices = await task_notification_service.list_notifications(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, recipient_user_id=self.alice_id,
        )
        self.assertEqual(len(alices), 1)

    async def test_agent_commenting_still_notifies_the_human_owner(self):
        """The commenter-exclusion check compares ids, not author kinds --
        an agent's author_id can never collide with a human owner's user
        id, so an agent comment must notify the human owner exactly like a
        human comment would."""
        await project_tasks_service.add_task_comment(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, task_id=self.task["id"],
            author_type="agent", author_id="agent-999", body="done from my end",
        )
        alices = await task_notification_service.list_notifications(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, recipient_user_id=self.alice_id,
        )
        self.assertEqual(len(alices), 1)
        self.assertEqual(alices[0]["actor_type"], "agent")
        self.assertEqual(alices[0]["actor_id"], "agent-999")

    # -- 4. list_notifications / mark_notification_read primitives -------

    async def test_unread_only_filter_and_mark_read_round_trip(self):
        created = await task_notification_service.create_notification(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, recipient_user_id=self.bob_id,
            source_event_type=task_notification_service.SOURCE_EVENT_ASSIGNED,
            task_id=self.task["id"], actor_type="user", actor_id=self.alice_id, body="You were assigned",
        )
        unread_before = await task_notification_service.list_notifications(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, recipient_user_id=self.bob_id,
            unread_only=True,
        )
        self.assertEqual(len(unread_before), 1)

        marked = await task_notification_service.mark_notification_read(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id,
            recipient_user_id=self.bob_id, notification_id=created["id"],
        )
        self.assertIsNotNone(marked["read_at"])

        unread_after = await task_notification_service.list_notifications(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, recipient_user_id=self.bob_id,
            unread_only=True,
        )
        self.assertEqual(unread_after, [])

    async def test_cannot_mark_someone_elses_notification_read(self):
        created = await task_notification_service.create_notification(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, recipient_user_id=self.bob_id,
            source_event_type=task_notification_service.SOURCE_EVENT_ASSIGNED,
            task_id=self.task["id"], actor_type="user", actor_id=self.alice_id, body="You were assigned",
        )
        # alice tries to mark BOB's notification read using its real id.
        result = await task_notification_service.mark_notification_read(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id,
            recipient_user_id=self.alice_id, notification_id=created["id"],
        )
        self.assertIsNone(result)
        still_unread = await task_notification_service.list_notifications(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, recipient_user_id=self.bob_id,
            unread_only=True,
        )
        self.assertEqual(len(still_unread), 1)


class NotificationsRouteDatabaseTests(_BridgeAsyncTestCase):
    """The actual HTTP surface, real Postgres underneath, real auth
    dependency wiring (only `_resolve_tenant` is mocked -- that lookup is
    unrelated plumbing every routes_fleet.py test in this suite already
    mocks the same way). httpx's ASGITransport needs no separate event
    loop management of its own, so this stays on the SAME bridge loop as
    every other real-Postgres test in this file, avoiding the "asyncpg pool
    bound to a different loop" hazard the module-level _run() docstring
    warns about."""

    async def async_setup(self):
        self.pool = await self._pool_or_skip()
        suffix = uuid.uuid4().hex[:10]
        self.tenant_id = f"t_notifroute_{suffix}"
        self.workspace_id = f"ws_notifroute_{suffix}"
        self.project_id = f"proj_notifroute_{suffix}"
        self.alice_id = f"user_alice_r_{suffix}"
        self.bob_id = f"user_bob_r_{suffix}"
        await self.pool.execute(
            "INSERT INTO projects (id, tenant_id, workspace_id, name, slug) VALUES ($1, $2, $3, $4, $1)",
            self.project_id, self.tenant_id, self.workspace_id, "Notifications route test project",
        )
        for user_id in (self.alice_id, self.bob_id):
            await self.pool.execute(
                "INSERT INTO users (id, tenant_id, workspace_id, email) VALUES ($1, $2, $3, $4)",
                user_id, self.tenant_id, self.workspace_id, f"{user_id}@example.com",
            )
        self.alice_notification = await task_notification_service.create_notification(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, recipient_user_id=self.alice_id,
            source_event_type=task_notification_service.SOURCE_EVENT_MENTION,
            task_id=None, actor_type="user", actor_id=self.bob_id, body="You were mentioned",
        )
        self.bob_notification = await task_notification_service.create_notification(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, recipient_user_id=self.bob_id,
            source_event_type=task_notification_service.SOURCE_EVENT_ASSIGNED,
            task_id=None, actor_type="user", actor_id=self.alice_id, body="You were assigned",
        )

    async def _pool_or_skip(self):
        if not _database_url_available():
            self.skipTest(_NO_PG_REASON)
        pool = await control_plane_repository.ensure_control_plane_schema()
        if pool is None:
            self.skipTest(_NO_PG_REASON)
        return pool

    async def async_teardown(self):
        pool = getattr(self, "pool", None)
        if pool is None:
            return
        await pool.execute("DELETE FROM task_notifications WHERE tenant_id = $1", self.tenant_id)
        await pool.execute("DELETE FROM projects WHERE tenant_id = $1", self.tenant_id)
        await pool.execute("DELETE FROM users WHERE tenant_id = $1", self.tenant_id)

    def _app_and_client(self):
        import httpx
        from fastapi import FastAPI

        from server_modules import routes_fleet

        app = FastAPI()
        app.include_router(routes_fleet.router)
        return routes_fleet, app, httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    def _auth_as(self, user_id: str) -> dict:
        return {
            "user_id": user_id,
            "email": f"{user_id}@example.com",
            "auth_type": "bearer",
            "workspace_access": {
                self.workspace_id: {
                    "workspace_id": self.workspace_id,
                    "tenant_id": self.tenant_id,
                    "role": "viewer",
                    "tenant_role": "viewer",
                }
            },
        }

    async def test_list_notifications_route_returns_only_the_callers_own(self):
        routes_fleet, app, client = self._app_and_client()
        app.dependency_overrides[routes_fleet.auth_module.get_current_user] = lambda: self._auth_as(self.alice_id)
        async with client:
            with patch(
                "server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value=self.tenant_id),
            ):
                response = await client.get(f"/api/w/{self.workspace_id}/fleet/notifications")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        ids = {item["id"] for item in payload["notifications"]}
        self.assertIn(self.alice_notification["id"], ids)
        self.assertNotIn(self.bob_notification["id"], ids, "a second workspace member's notification leaked")

    async def test_second_workspace_member_sees_only_their_own(self):
        """The mirror of the test above, run as bob -- proves this is a
        real per-caller filter, not "alice happens to be first"."""
        routes_fleet, app, client = self._app_and_client()
        app.dependency_overrides[routes_fleet.auth_module.get_current_user] = lambda: self._auth_as(self.bob_id)
        async with client:
            with patch(
                "server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value=self.tenant_id),
            ):
                response = await client.get(f"/api/w/{self.workspace_id}/fleet/notifications")
        payload = response.json()
        ids = {item["id"] for item in payload["notifications"]}
        self.assertIn(self.bob_notification["id"], ids)
        self.assertNotIn(self.alice_notification["id"], ids)

    async def test_mark_read_route_cannot_touch_someone_elses_notification(self):
        routes_fleet, app, client = self._app_and_client()
        # Authenticated as bob, but naming ALICE's notification id.
        app.dependency_overrides[routes_fleet.auth_module.get_current_user] = lambda: self._auth_as(self.bob_id)
        async with client:
            with patch(
                "server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value=self.tenant_id),
            ):
                response = await client.post(
                    f"/api/w/{self.workspace_id}/fleet/notifications/{self.alice_notification['id']}/read"
                )
        self.assertEqual(response.status_code, 200)  # service-failure contract: ok:false, not a 500
        payload = response.json()
        self.assertFalse(payload["ok"])

        alices_still_unread = await task_notification_service.list_notifications(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id,
            recipient_user_id=self.alice_id, unread_only=True,
        )
        self.assertEqual(len(alices_still_unread), 1)

    async def test_mark_read_route_marks_the_callers_own(self):
        routes_fleet, app, client = self._app_and_client()
        app.dependency_overrides[routes_fleet.auth_module.get_current_user] = lambda: self._auth_as(self.alice_id)
        async with client:
            with patch(
                "server_modules.routes_fleet._resolve_tenant", new=AsyncMock(return_value=self.tenant_id),
            ):
                response = await client.post(
                    f"/api/w/{self.workspace_id}/fleet/notifications/{self.alice_notification['id']}/read"
                )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertIsNotNone(payload["notification"]["read_at"])


# ── Layer 4: real Postgres, throwaway non-superuser role -- the actual
# database-level RLS isolation proof (tenant/workspace only; recipient
# scoping is the application-level layer proven above). ────────────────


def _probe_dsn(admin_dsn: str, *, user: str, password: str) -> str:
    parts = urlsplit(admin_dsn)
    netloc = f"{user}:{password}@{parts.hostname or 'localhost'}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


class TaskNotificationsRlsIsolationTests(_BridgeAsyncTestCase):
    """Duplicates test_rls_project_task_labels_isolation.py's own
    throwaway-role fixture rather than importing it -- that file belongs to
    a different, concurrently-running agent's collision-protocol territory
    (see this module's own docstring), and per that file's own stated
    reasoning for not importing ITS predecessor's fixture, this keeps the
    two files independently correct."""

    GRANT_TABLES = ("task_notifications",)

    async def async_setup(self):
        if not _database_url_available():
            self.skipTest(_NO_PG_REASON)
            return
        import asyncpg

        self.admin_dsn = os.environ["DATABASE_URL"].strip()
        try:
            self.admin_conn = await asyncpg.connect(self.admin_dsn, timeout=10)
        except Exception as exc:  # noqa: BLE001 — unreachable Postgres is a skip
            self.skipTest(f"{_NO_PG_REASON}: {exc}")
            return

        # Confirm the app itself already created the table (via
        # ensure_control_plane_schema's CREATE TABLE IF NOT EXISTS) before
        # trying to enable RLS on it -- a throwaway role GRANT against a
        # nonexistent table would fail with a confusing error otherwise.
        pool = await control_plane_repository.ensure_control_plane_schema()
        if pool is None:
            self.skipTest(_NO_PG_REASON)
            return

        suffix = uuid.uuid4().hex[:10]
        self.tenant_a = f"t_notifrls_a_{suffix}"
        self.tenant_b = f"t_notifrls_b_{suffix}"
        self.ws_a = f"ws_notifrls_a_{suffix}"
        self.ws_b = f"ws_notifrls_b_{suffix}"
        self.role = f"rls_notif_probe_{suffix}"
        self.role_password = uuid.uuid4().hex

        await self.admin_conn.execute(
            f'CREATE ROLE "{self.role}" LOGIN PASSWORD \'{self.role_password}\''
        )
        table_list = ", ".join(self.GRANT_TABLES)
        await self.admin_conn.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table_list} TO \"{self.role}\""
        )
        self.probe_dsn = _probe_dsn(self.admin_dsn, user=self.role, password=self.role_password)

        self.user_a_id = f"user_notifrls_a_{suffix}"
        self.user_b_id = f"user_notifrls_b_{suffix}"
        await self.admin_conn.execute(
            """
            INSERT INTO users (id, tenant_id, workspace_id, email)
            VALUES ($1, $2, $3, $4), ($5, $6, $7, $8)
            """,
            self.user_a_id, self.tenant_a, self.ws_a, f"{self.user_a_id}@example.com",
            self.user_b_id, self.tenant_b, self.ws_b, f"{self.user_b_id}@example.com",
        )
        self.notif_a_id = f"notif_a_{suffix}"
        self.notif_b_id = f"notif_b_{suffix}"
        await self.admin_conn.execute(
            """
            INSERT INTO task_notifications
                (id, tenant_id, workspace_id, recipient_user_id, source_event_type, body)
            VALUES
                ($1, $2, $3, $4, 'task_mention', 'Tenant A notification'),
                ($5, $6, $7, $8, 'task_mention', 'Tenant B notification')
            """,
            self.notif_a_id, self.tenant_a, self.ws_a, self.user_a_id,
            self.notif_b_id, self.tenant_b, self.ws_b, self.user_b_id,
        )

    async def async_teardown(self):
        conn = getattr(self, "admin_conn", None)
        if conn is None:
            return
        try:
            for tenant_id in (getattr(self, "tenant_a", None), getattr(self, "tenant_b", None)):
                if not tenant_id:
                    continue
                await conn.execute("DELETE FROM task_notifications WHERE tenant_id = $1", tenant_id)
                await conn.execute("DELETE FROM users WHERE tenant_id = $1", tenant_id)
        finally:
            role = getattr(self, "role", None)
            if role:
                table_list = ", ".join(self.GRANT_TABLES)
                await conn.execute(f"REVOKE ALL ON {table_list} FROM \"{role}\"")
                await conn.execute(f'DROP ROLE IF EXISTS "{role}"')
            await conn.close()

    async def _scoped_probe_read(self, tenant_id: str, workspace_id: str, query: str, *args):
        import asyncpg

        conn = await asyncpg.connect(self.probe_dsn, timeout=10)
        try:
            async with conn.transaction():
                await conn.execute(
                    """
                    SELECT set_config('app.current_tenant_id', $1, true),
                           set_config('app.current_workspace_id', $2, true)
                    """,
                    tenant_id, workspace_id,
                )
                return await conn.fetch(query, *args)
        finally:
            await conn.close()

    async def test_task_notifications_isolated_by_tenant_and_workspace(self) -> None:
        """A query with NO tenant/workspace filter in its WHERE clause,
        scoped to tenant A's session, must see tenant A's row and MUST NOT
        see tenant B's -- proves the POLICY filters, not "the service layer
        remembered to filter" (a superuser connection would pass even with
        zero protection, which is why this uses an ordinary role)."""
        rows = await self._scoped_probe_read(
            self.tenant_a, self.ws_a,
            "SELECT id, tenant_id FROM task_notifications WHERE id = ANY($1)",
            [self.notif_a_id, self.notif_b_id],
        )
        seen = {row["id"] for row in rows}
        self.assertIn(self.notif_a_id, seen)
        self.assertNotIn(self.notif_b_id, seen)
        self.assertEqual(len(rows), 1)

    async def test_regression_guard_tenant_b_sees_its_own_row_too(self) -> None:
        rows = await self._scoped_probe_read(
            self.tenant_b, self.ws_b,
            "SELECT id, tenant_id FROM task_notifications WHERE id = ANY($1)",
            [self.notif_a_id, self.notif_b_id],
        )
        seen = {row["id"] for row in rows}
        self.assertIn(self.notif_b_id, seen)
        self.assertNotIn(self.notif_a_id, seen)
        self.assertEqual(len(rows), 1)

    async def test_unscoped_session_sees_neither_row(self) -> None:
        rows = await self._scoped_probe_read(
            "", "",
            "SELECT id FROM task_notifications WHERE id = ANY($1)",
            [self.notif_a_id, self.notif_b_id],
        )
        self.assertEqual(list(rows), [])

    async def test_catalog_confirms_rls_enabled_and_forced(self) -> None:
        rows = await self.admin_conn.fetch(
            """
            SELECT c.relname AS table_name, c.relrowsecurity AS rls_enabled, c.relforcerowsecurity AS rls_forced,
                   COALESCE(p.policy_count, 0) AS policy_count
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            LEFT JOIN (
                SELECT schemaname, tablename, COUNT(*) AS policy_count
                FROM pg_policies GROUP BY schemaname, tablename
            ) p ON p.schemaname = n.nspname AND p.tablename = c.relname
            WHERE n.nspname = 'public' AND c.relname = 'task_notifications'
            """,
        )
        self.assertEqual(len(rows), 1, "task_notifications not found in pg_class.")
        row = rows[0]
        self.assertTrue(row["rls_enabled"], "task_notifications: RLS not enabled.")
        self.assertTrue(row["rls_forced"], "task_notifications: RLS not FORCEd.")
        self.assertGreaterEqual(row["policy_count"], 1, "task_notifications: no RLS policy.")


if __name__ == "__main__":
    unittest.main()
