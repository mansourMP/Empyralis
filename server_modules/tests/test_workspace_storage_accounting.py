"""Per-project storage accounting and the cap it exists to make possible.

Four concerns, four classes, following the split
test_agent_private_memory_repository.py already established for a scoped
control-plane table:

1. ``StorageCapDecisionTests`` -- the PURE decision in
   upload_content_policy (assert_within_storage_cap / format_bytes /
   storage_cap_sentence). No database. This is where the product law is
   asserted: a refusal names the limit, what is left, and what to do.

2. ``StorageReservationTests`` -- the REAL workspace_storage_service
   functions driven against a small in-memory fake standing in for
   control_plane_repository's pool, so "an over-cap upload writes NOTHING"
   can be proven without a live Postgres. The fake's call log is the
   evidence: a refused reservation must show zero INSERT statements, not
   merely raise.

3. ``StorageAccountingWiringTests`` -- AST assertions that the cap is
   actually reached on the live upload path. CLAUDE.md's single most
   common defect in this codebase is complete, correct, tested code with
   zero callers; a cap that is never called is exactly that, and it would
   pass every test in class 1 and 2.

4. ``StorageLedgerShapeTests`` -- source-shape checks over the schema and
   the migration: the table exists in both places that must create it, is
   RLS'd, and carries NO DML (a backfill in a migration against a FORCE-RLS
   table silently addresses zero rows when applied as the non-superuser app
   role -- documented twice in CLAUDE.md, and the reason this ledger
   deliberately starts empty).
"""

from __future__ import annotations

import ast
import inspect
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _run(coro):
    from server_modules import sync_asyncio_bridge

    return sync_asyncio_bridge.run_coro_sync(coro)


# ── 1. The pure decision ───────────────────────────────────────────────────


class StorageCapDecisionTests(unittest.TestCase):
    def test_an_upload_that_fits_returns_the_resulting_total(self) -> None:
        from server_modules import upload_content_policy as policy

        self.assertEqual(
            policy.assert_within_storage_cap(
                used_bytes=100, incoming_bytes=50, cap_bytes=1000,
            ),
            150,
        )

    def test_exactly_filling_the_cap_is_allowed(self) -> None:
        from server_modules import upload_content_policy as policy

        self.assertEqual(
            policy.assert_within_storage_cap(used_bytes=900, incoming_bytes=100, cap_bytes=1000),
            1000,
        )

    def test_one_byte_over_the_cap_is_refused(self) -> None:
        from server_modules import upload_content_policy as policy

        with self.assertRaises(policy.StorageCapExceeded):
            policy.assert_within_storage_cap(used_bytes=900, incoming_bytes=101, cap_bytes=1000)

    def test_a_cap_refusal_is_also_an_upload_rejection(self) -> None:
        """Every route already catches UploadRejected. A cap refusal that
        was not one would 500 instead of answering the customer."""
        from server_modules import upload_content_policy as policy

        self.assertTrue(issubclass(policy.StorageCapExceeded, policy.UploadRejected))

    def test_the_refusal_names_the_limit_the_remaining_room_and_the_way_out(self) -> None:
        from server_modules import upload_content_policy as policy

        with self.assertRaises(policy.StorageCapExceeded) as caught:
            policy.assert_within_storage_cap(
                used_bytes=1024 * 1024 * 1023,
                incoming_bytes=32 * 1024 * 1024,
                cap_bytes=1024 * 1024 * 1024,
            )
        message = str(caught.exception)
        self.assertIn("32 MB", message)          # what was asked for
        self.assertIn("1 GB", message)           # the limit itself
        self.assertIn("Delete", message)         # the way out
        # A cap message must never be a bare number of bytes.
        self.assertNotIn("1073741824", message)

    def test_an_unset_cap_admits_everything_rather_than_refusing_everything(self) -> None:
        """A misconfigured dial must fail OPEN. Zero meaning "no room at
        all" would take uploads away from every workspace at once."""
        from server_modules import upload_content_policy as policy

        self.assertEqual(
            policy.assert_within_storage_cap(used_bytes=10**12, incoming_bytes=10**9, cap_bytes=0),
            10**12 + 10**9,
        )

    def test_byte_formatting_never_produces_a_unit_it_should_have_promoted(self) -> None:
        from server_modules import upload_content_policy as policy

        self.assertEqual(policy.format_bytes(1024 ** 3), "1 GB")
        # 1 GiB minus ten bytes must not read "1024 MB".
        self.assertEqual(policy.format_bytes(1024 ** 3 - 10), "1 GB")
        self.assertEqual(policy.format_bytes(32 * 1024 * 1024), "32 MB")
        self.assertEqual(policy.format_bytes(512), "512 bytes")
        self.assertEqual(policy.format_bytes(0), "0 bytes")

    def test_the_workspace_bucket_and_a_project_bucket_share_one_number(self) -> None:
        """An unattributed bucket with no cap, or its own cap, would be a
        way around the limit. Same dial, different label."""
        from server_modules import billing_credit_config, upload_content_policy as policy

        cap = billing_credit_config.project_storage_cap_bytes()
        for label in ("project", "workspace"):
            with self.assertRaises(policy.StorageCapExceeded):
                policy.assert_within_storage_cap(
                    used_bytes=cap, incoming_bytes=1, cap_bytes=cap, scope_label=label,
                )


# ── 2. The real service against a fake pool ────────────────────────────────


class _FakeTransaction:
    def __init__(self, connection: "_FakeConnection") -> None:
        self._connection = connection

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        # Model a real transaction: anything that raised rolls the
        # statements back, which is precisely the property a refusal
        # depends on.
        if exc_type is not None:
            self._connection.rolled_back = True
            self._connection.statements = list(self._connection.committed)
        else:
            self._connection.committed = list(self._connection.statements)
        return False


class _FakeConnection:
    def __init__(self, rows):
        self.rows = list(rows)
        self.statements = []
        self.committed = []
        self.rolled_back = False

    def transaction(self):
        return _FakeTransaction(self)

    async def execute(self, query, *args):
        self.statements.append((" ".join(str(query).split()), args))
        if "INSERT INTO workspace_storage_objects" in query:
            self.rows.append(
                {
                    "id": args[0],
                    "tenant_id": args[1],
                    "workspace_id": args[2],
                    "project_id": args[3],
                    "surface": args[4],
                    "object_key": args[5],
                    "byte_size": args[6],
                }
            )
        return "INSERT 0 1"

    async def fetchval(self, query, *args):
        self.statements.append((" ".join(str(query).split()), args))
        if "SUM(byte_size)" in query:
            tenant_id, workspace_id, project_id = args[0], args[1], args[2]
            return sum(
                int(row["byte_size"])
                for row in self.rows
                if row["tenant_id"] == tenant_id
                and row["workspace_id"] == workspace_id
                and row["project_id"] == project_id
            )
        return None


class _FakeAcquire:
    def __init__(self, connection):
        self._connection = connection

    async def __aenter__(self):
        return self._connection

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakePool:
    def __init__(self, rows=()):
        self.connection = _FakeConnection(rows)

    def acquire(self):
        return _FakeAcquire(self.connection)


class StorageReservationTests(unittest.TestCase):
    def setUp(self) -> None:
        from server_modules import billing_credit_config, workspace_storage_service

        self.service = workspace_storage_service
        self.billing = billing_credit_config
        self._original_pool = workspace_storage_service._pool
        self._original_cap = billing_credit_config.PROJECT_STORAGE_CAP_BYTES

    def tearDown(self) -> None:
        self.service._pool = self._original_pool
        self.billing.PROJECT_STORAGE_CAP_BYTES = self._original_cap

    def _install(self, pool):
        async def _fake_pool():
            return pool

        self.service._pool = _fake_pool

    def test_an_upload_that_fits_is_recorded(self) -> None:
        pool = _FakePool()
        self._install(pool)
        self.billing.PROJECT_STORAGE_CAP_BYTES = 1000

        result = _run(
            self.service.reserve_storage_for_upload(
                tenant_id="t1",
                workspace_id="ws1",
                object_id="obj1",
                surface=self.service.SURFACE_CHAT_ATTACHMENT,
                object_key="obj1.png",
                byte_size=400,
            )
        )
        self.assertTrue(result["recorded"])
        self.assertEqual(result["used_bytes"], 400)
        self.assertEqual(len(pool.connection.rows), 1)

    def test_an_over_cap_upload_is_refused_and_writes_NOTHING(self) -> None:
        """The assertion that matters: not just "it raised" but "no INSERT
        statement was ever issued." A cap that refuses after recording the
        row charges a customer for bytes that never existed."""
        from server_modules import upload_content_policy as policy

        pool = _FakePool(
            [
                {
                    "id": "existing",
                    "tenant_id": "t1",
                    "workspace_id": "ws1",
                    "project_id": "",
                    "surface": "chat_attachment",
                    "object_key": "existing.png",
                    "byte_size": 900,
                }
            ]
        )
        self._install(pool)
        self.billing.PROJECT_STORAGE_CAP_BYTES = 1000

        with self.assertRaises(policy.StorageCapExceeded) as caught:
            _run(
                self.service.reserve_storage_for_upload(
                    tenant_id="t1",
                    workspace_id="ws1",
                    object_id="obj2",
                    surface=self.service.SURFACE_CHAT_ATTACHMENT,
                    object_key="obj2.png",
                    byte_size=200,
                )
            )
        self.assertIn("does not fit", str(caught.exception))
        inserts = [q for q, _ in pool.connection.statements if q.startswith("INSERT INTO workspace_storage_objects")]
        self.assertEqual(inserts, [], "a refused reservation must issue no INSERT")
        self.assertTrue(pool.connection.rolled_back)
        self.assertEqual(len(pool.connection.rows), 1, "the pre-existing object is the only row")

    def test_concurrent_uploads_into_one_bucket_are_serialised_by_an_advisory_lock(self) -> None:
        """Without this, two requests each read the same "there is room" and
        both write -- the classic check-then-act race, on the one number the
        cap depends on."""
        pool = _FakePool()
        self._install(pool)
        self.billing.PROJECT_STORAGE_CAP_BYTES = 1000
        _run(
            self.service.reserve_storage_for_upload(
                tenant_id="t1",
                workspace_id="ws1",
                object_id="obj1",
                surface=self.service.SURFACE_CHAT_ATTACHMENT,
                object_key="obj1.png",
                byte_size=1,
            )
        )
        locks = [args for q, args in pool.connection.statements if "pg_advisory_xact_lock" in q]
        self.assertEqual(len(locks), 1)
        # The lock is taken BEFORE the usage read, or it protects nothing.
        order = [q for q, _ in pool.connection.statements]
        lock_at = next(i for i, q in enumerate(order) if "pg_advisory_xact_lock" in q)
        sum_at = next(i for i, q in enumerate(order) if "SUM(byte_size)" in q)
        self.assertLess(lock_at, sum_at)

    def test_two_different_buckets_do_not_share_a_lock_key(self) -> None:
        a = self.service._bucket_lock_key("ws1", "")
        b = self.service._bucket_lock_key("ws1", "proj_a")
        c = self.service._bucket_lock_key("ws2", "")
        self.assertNotEqual(a, b)
        self.assertNotEqual(a, c)
        # Postgres advisory locks take a signed bigint; a negative key from a
        # sign-extended hash would be legal but confusing in pg_locks.
        for key in (a, b, c):
            self.assertGreaterEqual(key, 0)

    def test_an_unreachable_control_plane_admits_the_upload_uncounted_and_says_so(self) -> None:
        """Fails OPEN, deliberately. The cap bounds cost; it must not become
        a second availability dependency in front of a working feature. The
        undercount is REPORTED (recorded: False), never hidden."""
        self._install(None)
        result = _run(
            self.service.reserve_storage_for_upload(
                tenant_id="t1",
                workspace_id="ws1",
                object_id="obj1",
                surface=self.service.SURFACE_CHAT_ATTACHMENT,
                object_key="obj1.png",
                byte_size=999_999_999,
            )
        )
        self.assertFalse(result["recorded"])

    def test_usage_rolls_project_buckets_up_to_a_workspace_total(self) -> None:
        from server_modules import control_plane_repository as cpr

        # Already in the order the real query's ORDER BY bytes DESC produces
        # -- the fake stands in for the pool, not for Postgres's planner.
        rows = [
            {"project_id": "", "bytes": 700, "objects": 1},
            {"project_id": "proj_a", "bytes": 300, "objects": 2},
        ]

        async def _fake_fetch(pool, query, *args, **kwargs):
            return rows

        original_fetch = cpr.rls_fetch
        self._install(_FakePool())
        cpr.rls_fetch = _fake_fetch
        try:
            usage = _run(self.service.workspace_storage_usage(tenant_id="t1", workspace_id="ws1"))
        finally:
            cpr.rls_fetch = original_fetch
        self.assertEqual(usage["total_bytes"], 1000)
        self.assertEqual([b["project_id"] for b in usage["buckets"]], ["", "proj_a"])


# ── 3. Is the cap actually on the live path ────────────────────────────────


class StorageAccountingWiringTests(unittest.TestCase):
    """"The function is there" is not evidence it runs (CLAUDE.md). These
    read the real source of the two registrations of
    POST /api/sage-chat/attachments."""

    LIVE_HANDLER = _REPO_ROOT / "server_modules" / "sage_context_files_api.py"
    SHADOWED_TWIN = _REPO_ROOT / "server_modules" / "sage_chat_api.py"

    def _source(self, path: Path) -> str:
        self.assertTrue(path.exists(), f"{path} is missing — this test's target moved")
        return path.read_text(encoding="utf-8")

    def test_both_registrations_of_the_attachment_route_reserve_storage(self) -> None:
        """Two registrations of one path must never accept two different
        things — the same reason both already call assert_allowed_upload."""
        for path in (self.LIVE_HANDLER, self.SHADOWED_TWIN):
            source = self._source(path)
            self.assertIn(
                "workspace_storage_service.reserve_storage_for_upload",
                source,
                f"{path.name} writes attachment bytes without going through the storage cap",
            )

    def test_the_live_handler_checks_the_cap_before_writing_the_file(self) -> None:
        """Order is the whole point: a cap checked after the write has
        already spent the disk it was meant to protect."""
        source = self._source(self.LIVE_HANDLER)
        reserve_at = source.index("reserve_storage_for_upload")
        write_at = source.index("dest_path.write_bytes(raw)")
        self.assertLess(reserve_at, write_at)

    def test_the_live_handler_releases_the_reservation_when_the_file_write_fails(self) -> None:
        """Otherwise those bytes count against the cap forever with nothing
        on disk to delete."""
        source = self._source(self.LIVE_HANDLER)
        self.assertIn("forget_stored_object", source)

    def test_the_service_never_reads_the_cap_constant_directly(self) -> None:
        """One interception point for the number (CLAUDE.md: tier tuning
        happens in billing_credit_config and nowhere else). A direct
        constant read is how a second, un-tunable copy appears."""
        from server_modules import workspace_storage_service

        source = inspect.getsource(workspace_storage_service)
        self.assertIn("billing_credit_config.project_storage_cap_bytes()", source)
        self.assertNotIn("billing_credit_config.PROJECT_STORAGE_CAP_BYTES", source)


# ── 4. The ledger's own shape ──────────────────────────────────────────────


class StorageLedgerShapeTests(unittest.TestCase):
    MIGRATION = _REPO_ROOT / "migrations" / "add_workspace_storage_objects.sql"
    ENABLE_RLS = _REPO_ROOT / "migrations" / "enable_rls.sql"
    SCHEMA = _REPO_ROOT / "server_modules" / "control_plane_repository.py"

    def test_the_table_is_created_by_the_boot_schema_and_by_a_migration(self) -> None:
        """A fresh boot must not need a migration, and an existing database
        must not need a redeploy. Both, or one of them is broken."""
        self.assertIn(
            "CREATE TABLE IF NOT EXISTS workspace_storage_objects",
            self.SCHEMA.read_text(encoding="utf-8"),
        )
        self.assertIn(
            "CREATE TABLE IF NOT EXISTS workspace_storage_objects",
            self.MIGRATION.read_text(encoding="utf-8"),
        )

    def test_the_table_is_rls_scoped_in_the_shared_migration(self) -> None:
        """preflight._check_rls_coverage fails CLOSED on a scoped table it
        has never heard of — a missing entry here is a boot failure, not a
        quiet gap."""
        source = self.ENABLE_RLS.read_text(encoding="utf-8")
        self.assertIn("ALTER TABLE workspace_storage_objects FORCE ROW LEVEL SECURITY;", source)
        self.assertIn("empyralis_workspace_storage_objects_scope", source)

    def test_the_migration_carries_no_DML(self) -> None:
        """A backfill in a migration against a FORCE-RLS table, applied as
        the non-superuser app role with no scope GUCs set, addresses ZERO
        rows and exits 0. This ledger has nothing to backfill; asserting it
        stays that way is cheaper than discovering the silence later."""
        source = self.MIGRATION.read_text(encoding="utf-8")
        for line in source.splitlines():
            statement = line.strip().upper()
            if statement.startswith("--"):
                continue
            for verb in ("UPDATE ", "INSERT INTO", "DELETE FROM"):
                self.assertFalse(
                    statement.startswith(verb),
                    f"DML in a migration against an RLS'd table: {line!r}",
                )

    def test_project_id_is_not_a_foreign_key(self) -> None:
        """'' is a real bucket (an object not attributable to a project) and
        a REFERENCES projects(id) would forbid that row outright — which is
        every row the product writes today."""
        source = self.MIGRATION.read_text(encoding="utf-8")
        table = source[source.index("CREATE TABLE IF NOT EXISTS workspace_storage_objects") :]
        table = table[: table.index(");")]
        project_line = next(line for line in table.splitlines() if "project_id" in line)
        self.assertIn("NOT NULL DEFAULT ''", project_line)
        self.assertNotIn("REFERENCES", project_line)


# ── 5. The live route, end to end ──────────────────────────────────────────


class _FakeUploadFile:
    """Mirrors the two members of Starlette's UploadFile the route touches --
    the same stand-in test_sage_context_files_api.py already uses, for the
    same reason its own comment gives: the route reads the bytes up front."""

    def __init__(self, filename: str, content: bytes, content_type: str = "text/plain") -> None:
        import io

        self.filename = filename
        self.content_type = content_type
        self._content = content
        self.file = io.BytesIO(content)

    async def read(self) -> bytes:
        return self._content


class _FakeApp:
    def __init__(self) -> None:
        self.routes = {}

    def _register(self, method, path, **kwargs):
        def _decorator(fn):
            self.routes[(method, path)] = fn
            return fn

        return _decorator

    def get(self, path, **kwargs):
        return self._register("GET", path, **kwargs)

    def post(self, path, **kwargs):
        return self._register("POST", path, **kwargs)

    def patch(self, path, **kwargs):
        return self._register("PATCH", path, **kwargs)


class AttachmentRouteCapTests(unittest.TestCase):
    """Drives the REAL handler registered by
    sage_context_files_api.register_sage_context_file_routes -- the one
    FastAPI actually serves for POST /api/sage-chat/attachments -- so what
    is asserted here is what a customer's browser would receive, not what a
    service function returns in isolation."""

    def setUp(self) -> None:
        import sys
        import tempfile
        import types

        from server_modules import billing_credit_config, sage_context_files_api, workspace_storage_service

        fake_server = types.ModuleType("server")
        fake_server.Depends = lambda dependency: dependency
        fake_server.require_api_key = object()
        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        self.addCleanup(
            lambda: sys.modules.pop("server", None)
            if previous_server is None
            else sys.modules.__setitem__("server", previous_server)
        )

        self.api = sage_context_files_api
        self.service = workspace_storage_service
        self.billing = billing_credit_config
        app = _FakeApp()
        sage_context_files_api.register_sage_context_file_routes(app)
        self.upload = app.routes[("POST", "/api/sage-chat/attachments")]

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

        original_pool = workspace_storage_service._pool
        original_cap = billing_credit_config.PROJECT_STORAGE_CAP_BYTES
        self.addCleanup(lambda: setattr(workspace_storage_service, "_pool", original_pool))
        self.addCleanup(
            lambda: setattr(billing_credit_config, "PROJECT_STORAGE_CAP_BYTES", original_cap)
        )

    def _install(self, pool):
        async def _fake_pool():
            return pool

        self.service._pool = _fake_pool

    def _call(self, *, content: bytes, filename: str = "note.txt"):
        from unittest.mock import patch

        with (
            patch("server_modules.sage_context_files_api.enforce_workspace_access", return_value="ws1"),
            patch("server_modules.sage_context_files_api.workspace_tenant_id", return_value="t1"),
            patch(
                "server_modules.sage_context_files_api.workspace_attachments_dir",
                return_value=Path(self._tmp.name),
            ),
        ):
            return _run(
                self.upload(
                    workspace_id="ws1",
                    file=_FakeUploadFile(filename, content),
                    current_user={"id": "user-1"},
                )
            )

    def test_an_under_cap_upload_still_succeeds_and_lands_on_disk(self) -> None:
        pool = _FakePool()
        self._install(pool)
        self.billing.PROJECT_STORAGE_CAP_BYTES = 1000

        payload = self._call(content=b"hello world")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["size"], len(b"hello world"))
        written = list(Path(self._tmp.name).iterdir())
        self.assertEqual(len(written), 1)
        self.assertEqual(len(pool.connection.rows), 1, "the upload was not accounted for")

    def test_an_over_cap_upload_is_refused_with_a_message_naming_the_limit(self) -> None:
        from fastapi import HTTPException

        pool = _FakePool(
            [
                {
                    "id": "existing",
                    "tenant_id": "t1",
                    "workspace_id": "ws1",
                    "project_id": "",
                    "surface": "chat_attachment",
                    "object_key": "existing.png",
                    "byte_size": 995,
                }
            ]
        )
        self._install(pool)
        self.billing.PROJECT_STORAGE_CAP_BYTES = 1000

        with self.assertRaises(HTTPException) as caught:
            self._call(content=b"hello world")
        self.assertEqual(caught.exception.status_code, 400)
        detail = str(caught.exception.detail)
        self.assertIn("1000 bytes", detail)     # the limit
        self.assertIn("Delete", detail)         # the way out
        self.assertEqual(
            list(Path(self._tmp.name).iterdir()), [], "a refused upload still wrote a file",
        )

    def test_the_extension_allowlist_still_refuses_first(self) -> None:
        """Cap accounting must not have displaced the content policy -- a
        .py file is refused for WHAT it is, before anything asks whether
        there is room for it."""
        from fastapi import HTTPException

        pool = _FakePool()
        self._install(pool)
        with self.assertRaises(HTTPException) as caught:
            self._call(content=b"print(1)", filename="main.py")
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("Accepted files", str(caught.exception.detail))
        self.assertEqual(pool.connection.rows, [], "a type-refused file was accounted for anyway")


if __name__ == "__main__":
    unittest.main()
