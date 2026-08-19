"""A DML statement that targets an RLS-protected table, run with no scope,
matches ZERO rows under FORCE ROW LEVEL SECURITY -- silently, exit 0.

Confirmed three times in one day (2026-08-18), each independently:

    migrations + boot schema run as `empyralis_app`   (DEPLOY-RUNBOOK step 3b)
                           -- NON-superuser, so FORCE RLS binds it
    plain pool.execute() / psql sets NO app.tenant_id / app.workspace_id GUCs

      ALTER TABLE / CREATE INDEX   DDL -- RLS does not apply   -> APPLIED
      UPDATE / INSERT / DELETE     DML -- policy is FALSE      -> 0 ROWS, exit 0

    1. migrations/add_task_sequence_numbers.sql's GEN-12 backfill -- ran on
       production, wrote nothing, 9 of 10 projects left unkeyed for weeks.
       FIXED (projects_repository.backfill_task_identifiers).
    2. ensure_control_plane_schema()'s workspace_agent_installs
       label-dedupe DO $$ block -- still present, still addressing zero
       rows on every boot. Deliberately NOT fixed (see the allowlist verdict
       below -- its visible effect would be renaming the founder's own
       agents on restart, a product decision, not a bug fix).
    3. `UPDATE project_documents SET path = path || '.md'` on the documents
       branch -- reproduced against a FORCE-RLS replica with a
       NOSUPERUSER NOBYPASSRLS role. Being fixed on a sibling branch.

CLAUDE.md already documented instance 1 in detail before instance 3 was
written, by an agent that had read the codebase. Documentation demonstrably
did not prevent recurrence -- this is the structural guard instead.

While building this guard, scanning the two sources below (never a
guess -- see the offender set the scan actually produces) turned up a
FOURTH live instance: ensure_control_plane_schema()'s project_tasks
status-vocabulary DO block (plus its migrations/add_task_status_vocabulary.sql
mirror) ran the identical shape -- `UPDATE project_tasks SET status = 'todo'
WHERE status = 'open'` with no scope, on `project_tasks`, which also carries
FORCE ROW LEVEL SECURITY. FIXED alongside this file landing (SET LOCAL
app.rls_bypass = 'on' as the first statement of the same multi-statement
call -- reproduced red/green against a real NOSUPERUSER NOBYPASSRLS role
before and after; see the commit that added this docstring line). Safe as a
blanket bypass because the value written is a uniform vocabulary rename, not
tenant-specific data -- contrast the per-row `rls_execute(tenant_id=...,
workspace_id=...)` scoping `projects_repository.backfill_task_identifiers`
uses for instance 1, where the value written (a task_key) IS tenant-specific.

Five more offenders turned up in migrations/*.sql and are staying in the
allowlist below rather than being blind-fixed -- each has its own written
verdict; see `_ACCEPTED_UNSCOPED_DML`. Four are historical, hand-applied
migrations (never re-executed on boot, so a code edit here would not undo
whatever already happened in production, and this pass has no production
access to check); the fifth is a byte-for-byte standalone twin of instance
2 and inherits its verdict directly.

UPDATE, 2026-08-18 (later the same day): two of those five --
`stage_4b_agent_isolation.sql` and `unify_fleet_tool_toggle_ids.sql` --
were queried against real production over SSH by a follow-up pass with
actual database access (see their allowlist verdicts below for the exact
query and counts). One was a real, live bug (11 of 15 master installs
wrong) and is now fixed with a boot-time backfill,
`agent_registry_repository.backfill_master_agent_isolation_defaults`,
following the same shape as instance 1's fix. The other's target state was
already correct in production -- verified, not guessed. Their .sql files
are untouched on purpose (historical, hand-applied, never re-run); only the
verdict text and, for the first, a new Python backfill plus an INSERT-site
fix changed.

── Scope ───────────────────────────────────────────────────────────────────

This test deliberately does NOT scan the whole `server_modules` tree (unlike
`test_run_state_scope_fails_closed.py`'s `FailOpenScopeFilterDriftTests`,
which does). It scans exactly the two places DEPLOY-RUNBOOK.md step 3b says
run under `empyralis_app` with no RLS scope ever set:

    1. `control_plane_repository.py`'s boot-schema surface: the
       `CONTROL_PLANE_SCHEMA_SQL` constant (the DDL catalog it starts by
       executing) PLUS the rest of `ensure_control_plane_schema()`'s own
       body -- every guarded migration block that runs alongside it on
       every process boot. Instances 2 and 4 both live in the second half,
       not the constant itself, which is why the scan cannot stop at just
       the constant and still find either of them.
    2. Every `.sql` file under `migrations/`.

── The expected set and the actual set come from different places ─────────

CLAUDE.md's own rule, stated after a conformance check that derived its
expected set from the same file it was checking passed while 60 tables went
unprotected (`preflight._check_rls`): "a check that derives its own
expectations from the thing it checks is blind, and reports passed."
Here, which tables are RLS-protected is parsed from
`migrations/enable_rls.sql`'s `FORCE ROW LEVEL SECURITY` lines -- never
hand-listed, and never read from either of the two scanned sources above.

── Detection predicate ─────────────────────────────────────────────────────

A line whose stripped, upper-cased form starts with `UPDATE `, `INSERT INTO`,
or `DELETE FROM`. This is deliberately NOT anchored on `;` to delimit
statements -- CLAUDE.md documents a previous audit that silently
under-counted because it anchored on `;`, and half of this codebase's own
`CREATE TABLE` statements have no trailing semicolon (they're built inside
`_ensure_*_tables()` helpers). A line-based predicate has no statement
boundary to get wrong: `UPDATE workspace_agent_installs` sits on its own
line inside a nested `DO $$ ... END $$;` block with no semicolon of its own,
and this predicate finds it anyway (verified directly against current
`main` -- see `test_the_guard_catches_the_real_nested_do_block_offender`
below, which pins that exact shape rather than a synthetic stand-in).
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


# ── Path resolution ─────────────────────────────────────────────────────────

_SERVER_MODULES = Path(__file__).resolve().parent.parent
_REPO_ROOT = _SERVER_MODULES.parent
_MIGRATIONS_DIR = _REPO_ROOT / "migrations"
_CONTROL_PLANE_REPOSITORY_PATH = _SERVER_MODULES / "control_plane_repository.py"


# ── Source A: which tables are RLS-protected, independent of what we scan ──


_FORCE_RLS_LINE = re.compile(
    r"^ALTER TABLE\s+([A-Za-z_][A-Za-z0-9_]*)\s+FORCE ROW LEVEL SECURITY\s*;",
    re.IGNORECASE,
)


def _parse_force_rls_tables_from_text(text: str, *, source_description: str) -> set[str]:
    """Pure apart from the canary raise, so the canary tests exercise the
    EXACT code path `_rls_protected_tables()` uses -- not an inert sibling
    parser that never raises on its own, which would let that function's
    raise silently rot while its own dedicated tests kept passing.
    """
    tables: set[str] = set()
    for line in text.split("\n"):
        match = _FORCE_RLS_LINE.match(line.strip())
        if match:
            tables.add(match.group(1).lower())
    if not tables:
        # The canary. A scanner whose expected-set extraction silently
        # yields nothing enforces nothing while reporting green -- exactly
        # the shape CLAUDE.md documents for
        # exec-file-timeout-child-leak.test.ts, which scans `dist/` (zero
        # `.ts` files live there) and has therefore never caught anything.
        # This must fail LOUDLY instead, immediately, not as a soft
        # assertion some caller could filter away with `-k`.
        raise RuntimeError(
            f"Found ZERO tables carrying FORCE ROW LEVEL SECURITY in "
            f"{source_description}. Either the source moved/was renamed, "
            f"its `ALTER TABLE <t> FORCE ROW LEVEL SECURITY;` shape changed, "
            f"or the extraction regex broke. A drift test that scans an "
            f"empty expected-set enforces nothing while reporting PASSED -- "
            f"fix the regex or the path before trusting any other assertion "
            f"in this file."
        )
    return tables


def _rls_protected_tables() -> set[str]:
    path = _MIGRATIONS_DIR / "enable_rls.sql"
    text = path.read_text(encoding="utf-8")
    return _parse_force_rls_tables_from_text(text, source_description=str(path))


# ── Source B: the boot-schema surface in control_plane_repository.py ───────


def _extract_named_block(
    lines: list[str],
    start_pattern: re.Pattern,
    end_pattern: re.Pattern,
    *,
    label: str,
) -> tuple[int, int]:
    """Returns (start_index, end_index) -- a half-open [start, end) range
    into `lines` -- for the first block whose opening line matches
    `start_pattern`, ending at the first later line matching `end_pattern`
    (or end-of-file). Raises if `start_pattern` never matches: the second
    half of the canary, covering "the block this test depends on no longer
    exists at all" rather than only "the file it lives in is empty."
    """
    start = None
    for index, line in enumerate(lines):
        if start_pattern.match(line):
            start = index
            break
    if start is None:
        raise RuntimeError(
            f"Could not locate the {label} block: no line in "
            f"{_CONTROL_PLANE_REPOSITORY_PATH} matches {start_pattern.pattern!r}. "
            f"The function/constant was renamed or removed -- update this "
            f"test's extraction pattern, do not let it silently scan nothing."
        )
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if end_pattern.match(lines[index]):
            end = index
            break
    return start, end


_CONTROL_PLANE_SCHEMA_SQL_START = re.compile(r'^CONTROL_PLANE_SCHEMA_SQL\s*=\s*"""\s*$')
_TRIPLE_QUOTE_ALONE = re.compile(r'^"""\s*$')
_ENSURE_CONTROL_PLANE_SCHEMA_START = re.compile(r"^async def ensure_control_plane_schema\b")
_TOP_LEVEL_DEF_OR_CLASS = re.compile(r"^(async def |def |class )\S")


def _boot_schema_scope_ranges(lines: list[str]) -> list[tuple[int, int]]:
    """The two source ranges Empyralis actually executes at boot, as
    `empyralis_app`, before any RLS scope is ever set: the DDL catalog
    `CONTROL_PLANE_SCHEMA_SQL` starts by running, and the rest of
    `ensure_control_plane_schema()`'s own body -- every guarded migration
    block that runs alongside it on every process boot. Instances 2 and 4
    both live in the second range, not the first.
    """
    schema_const = _extract_named_block(
        lines,
        _CONTROL_PLANE_SCHEMA_SQL_START,
        _TRIPLE_QUOTE_ALONE,
        label="CONTROL_PLANE_SCHEMA_SQL constant",
    )
    schema_func = _extract_named_block(
        lines,
        _ENSURE_CONTROL_PLANE_SCHEMA_START,
        _TOP_LEVEL_DEF_OR_CLASS,
        label="ensure_control_plane_schema() function",
    )
    return [schema_const, schema_func]


def _enclosing_python_function(lines: list[str], index: int) -> str:
    """Same idiom as `test_run_state_scope_fails_closed.py`'s
    `_enclosing_function`: search backward for the nearest `def`. Lines
    inside `CONTROL_PLANE_SCHEMA_SQL` (a module-level constant, not inside
    any function) correctly resolve to `<module>`.
    """
    for cursor in range(index, -1, -1):
        match = re.match(r"^\s*(?:async\s+)?def\s+(\w+)", lines[cursor])
        if match:
            return match.group(1)
    return "<module>"


# ── The detection predicate ─────────────────────────────────────────────────


_DML_LINE = re.compile(
    r"^(UPDATE|INSERT INTO|DELETE FROM)\s+(?:ONLY\s+)?([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)


def _dml_match(stripped_line: str) -> tuple[str, str] | None:
    match = _DML_LINE.match(stripped_line)
    if not match:
        return None
    return match.group(1).upper(), match.group(2).lower()


# ── The scan itself ─────────────────────────────────────────────────────────

# key = (relative_path, context, kind, table); value = (line_no, line_text)
_ScanKey = tuple[str, str, str, str]
_ScanHit = tuple[int, str]


def _scan_control_plane_repository() -> dict[_ScanKey, _ScanHit]:
    protected = _rls_protected_tables()
    relpath = _CONTROL_PLANE_REPOSITORY_PATH.relative_to(_REPO_ROOT).as_posix()
    lines = _CONTROL_PLANE_REPOSITORY_PATH.read_text(encoding="utf-8").split("\n")
    found: dict[_ScanKey, _ScanHit] = {}
    for start, end in _boot_schema_scope_ranges(lines):
        for index in range(start, end):
            line = lines[index]
            hit = _dml_match(line.strip())
            if hit is None:
                continue
            kind, table = hit
            if table not in protected:
                continue
            context = _enclosing_python_function(lines, index)
            key = (relpath, context, kind, table)
            found[key] = (index + 1, line.strip())
    return found


def _scan_migrations_directory() -> dict[_ScanKey, _ScanHit]:
    # Canary check comes BEFORE the enable_rls.sql read on purpose: the two
    # failure modes (an empty migrations/ directory vs. a broken
    # enable_rls.sql parse) must be independently testable, and a caller
    # that swaps out _MIGRATIONS_DIR to prove the first one fires must not
    # also trip the second for an unrelated reason.
    sql_files = sorted(_MIGRATIONS_DIR.glob("*.sql"))
    if not sql_files:
        # Third canary: an empty migrations/ directory would make this half
        # of the scan vacuous in exactly the same silent way.
        raise RuntimeError(
            f"Found ZERO .sql files under {_MIGRATIONS_DIR}. Either the "
            f"directory moved or something deleted every migration -- fix "
            f"the path before trusting any other assertion in this file."
        )
    protected = _rls_protected_tables()
    found: dict[_ScanKey, _ScanHit] = {}
    for path in sql_files:
        relpath = path.relative_to(_REPO_ROOT).as_posix()
        lines = path.read_text(encoding="utf-8").split("\n")
        for index, line in enumerate(lines):
            hit = _dml_match(line.strip())
            if hit is None:
                continue
            kind, table = hit
            if table not in protected:
                continue
            key = (relpath, "<sql>", kind, table)
            found[key] = (index + 1, line.strip())
    return found


def _scan_for_unscoped_dml() -> dict[_ScanKey, _ScanHit]:
    found = dict(_scan_control_plane_repository())
    found.update(_scan_migrations_directory())
    return found


# ── The allowlist. Every entry carries a written verdict. ──────────────────
#
# The expected set here is HAND-WRITTEN; the actual set above is SCRAPED
# from source. Two different origins, matching CLAUDE.md's own
# `_ACCEPTED_FAIL_OPEN_SCOPE_FILTERS` idiom in
# `test_run_state_scope_fails_closed.py`.

_ACCEPTED_UNSCOPED_DML: dict[_ScanKey, str] = {
    (
        "server_modules/control_plane_repository.py",
        "ensure_control_plane_schema",
        "UPDATE",
        "project_tasks",
    ): (
        "FIXED 2026-08-18, the same day this guard was built: SET LOCAL "
        "app.rls_bypass = 'on' is now the first statement of this exact "
        "multi-statement pool.execute() call, immediately before this DO "
        "block. Reproduced red (row untouched) then green (row updated) "
        "against a real NOSUPERUSER NOBYPASSRLS role -- see the commit "
        "landed immediately before this test file. Safe as a blanket "
        "bypass because the value written ('todo') is a uniform vocabulary "
        "rename, not tenant-specific data. Stays allowlisted rather than "
        "teaching this scanner to parse per-call SET LOCAL boundaries: this "
        "predicate is deliberately line-based with no statement-boundary "
        "tracking (see this file's own module docstring on why -- the "
        "semicolon-anchoring trap), and reusing that same simplicity to "
        "recognize a preceding bypass would mean parsing exactly the kind "
        "of nested multi-line SQL-inside-Python structure the line-based "
        "design exists to avoid depending on. If this UPDATE is ever moved "
        "into a DIFFERENT pool.execute() call, verify the bypass moved with "
        "it before assuming this verdict still applies."
    ),
    (
        "migrations/add_task_status_vocabulary.sql",
        "<sql>",
        "UPDATE",
        "project_tasks",
    ): (
        "Same fix, standalone mirror -- SET LOCAL app.rls_bypass = 'on' "
        "immediately after this file's own BEGIN;, scoping the whole "
        "transaction (this file has exactly one). Same verdict as the "
        "control_plane_repository.py entry immediately above."
    ),
    (
        "server_modules/control_plane_repository.py",
        "ensure_control_plane_schema",
        "UPDATE",
        "workspace_agent_installs",
    ): (
        "Instance 2 (CLAUDE.md, 'Recurring failure modes'). The label-dedupe "
        "DO $$ block still addresses zero rows on every boot. Deliberately "
        "NOT fixed -- its visible effect would be renaming the founder's own "
        "live agents on the next restart, which is a product decision, not "
        "a bug fix. Do not fix without that decision being made first."
    ),
    (
        "migrations/add_workspace_agent_installs_label_uniqueness.sql",
        "<sql>",
        "UPDATE",
        "workspace_agent_installs",
    ): (
        "Byte-for-byte standalone twin of the entry directly above (its own "
        "header says so: 'the identical guarded logic in "
        "control_plane_repository.py's ensure_control_plane_schema, which "
        "runs this same dedupe-then-constrain path automatically on every "
        "process boot'). Inherits the same verdict -- fixing this file alone "
        "while leaving the boot-time DO block unfixed would just make the "
        "two diverge, and the underlying product decision (renaming the "
        "founder's live agents) has not been made either way."
    ),
    (
        "migrations/fix_slack_channel_uniqueness.sql",
        "<sql>",
        "UPDATE",
        "agent_channel_bindings",
    ): (
        "Dedupe-then-rebuild-unique-index shape, not a bare backfill: even "
        "if this UPDATE silently matches zero rows under FORCE RLS, the "
        "CREATE UNIQUE INDEX three statements later validates ALL existing "
        "rows regardless (DDL is not subject to RLS -- see this file's own "
        "module docstring) and would FAIL LOUDLY on any genuine duplicate "
        "rather than silently letting it through. Self-detecting, not "
        "silent. Also low blast radius by the migration's own note: Slack "
        "OAuth was not yet configured in production when this was written, "
        "so zero rows was the expected outcome regardless of RLS."
    ),
    (
        "migrations/fix_github_channel_uniqueness.sql",
        "<sql>",
        "UPDATE",
        "agent_channel_bindings",
    ): (
        "Same dedupe-then-rebuild-unique-index shape as "
        "fix_slack_channel_uniqueness.sql immediately above, same "
        "self-detecting CREATE UNIQUE INDEX three statements later. Inherits "
        "the identical verdict."
    ),
    (
        "migrations/stage_4b_agent_isolation.sql",
        "<sql>",
        "UPDATE",
        "workspace_agent_installs",
    ): (
        "VERIFIED AGAINST PRODUCTION 2026-08-18, and the migration DID "
        "silently no-op almost everywhere. Queried the live database "
        "directly over SSH (root@165.227.25.201, empyralis_app role, "
        "`SET app.rls_bypass = 'on'` to read past FORCE RLS -- a plain "
        "SELECT as that role returns zero rows too, same trap, so the "
        "verification query itself had to route around it): 11 of 15 "
        "agent_kind='master' installs carried hardware_access='none'/ "
        "subagents_enabled=false, spanning every workspace created since "
        "2026-07-07. The migration's one UPDATE only ever reached the 4 "
        "rows that existed at the moment someone happened to run it "
        "correctly (2026-06-25 through 2026-06-28); every workspace seeded "
        "after that missed it. FIXED, not left flagged: "
        "agent_registry_repository.backfill_master_agent_isolation_defaults "
        "(cross-tenant bypass_rls=True read, per-row rls_execute write "
        "scoped to that row's own tenant/workspace, WHERE-guard idempotency "
        "-- same shape as backfill_task_identifiers/backfill_document_paths) "
        "is wired into ensure_control_plane_schema() and heals every "
        "existing wrong row on the next boot. Also fixed the root cause, "
        "not just the historical remnant: ensure_workspace_agent_registry_"
        "seeded's own INSERT for the master install never set either "
        "column explicitly, so it fell through to the plain schema DEFAULT "
        "regardless of the migration's fate -- every NEW workspace was "
        "affected too, which is why 11 of 15 (not just the pre-migration "
        "handful) were wrong. That INSERT now sets 'all'/TRUE explicitly."
    ),
    (
        "migrations/unify_fleet_tool_toggle_ids.sql",
        "<sql>",
        "UPDATE",
        "workspace_agent_installs",
    ): (
        "VERIFIED AGAINST PRODUCTION 2026-08-18 (same session as "
        "stage_4b_agent_isolation.sql immediately above, same bypass "
        "technique). Target state is ALREADY CORRECT: 0 of 40 "
        "workspace_agent_installs rows carry any of the 13 old-spelling "
        "tool_toggles keys this migration renames (25 rows carry a "
        "non-empty tool_toggles at all, none of them old-spelling). "
        "Whether this migration actually ran under RLS or the old spellings "
        "were simply never written before the canonical names shipped is "
        "moot -- the live data is clean either way. No backfill needed; "
        "not fixed because there is nothing to fix."
    ),
}


def _failure_message(new_offenders: list[_ScanKey], found: dict[_ScanKey, _ScanHit]) -> str:
    lines = [
        "New unscoped DML statement(s) targeting an RLS-protected table.",
        "",
        "Each statement below runs (or would run) as the non-superuser "
        "`empyralis_app` role with no app.current_tenant_id / "
        "app.current_workspace_id GUC set (DEPLOY-RUNBOOK.md step 3b), "
        "against a table carrying FORCE ROW LEVEL SECURITY "
        "(migrations/enable_rls.sql). Under that combination the statement "
        "matches ZERO ROWS, reports success, and logs nothing -- it will "
        "not error, it will not warn, it will simply do nothing.",
        "",
    ]
    for key in new_offenders:
        relpath, context, kind, table = key
        line_no, line_text = found[key]
        lines.append(f"  {relpath}:{line_no} (in {context}) -- {kind} on `{table}`:")
        lines.append(f"      {line_text}")
        lines.append("")
    lines.append(
        "Fix it with a Python backfill through "
        "control_plane_repository.rls_execute(pool, query, *args, "
        "tenant_id=..., workspace_id=...) -- SCOPED PER ROW, matching the "
        "worked example in projects_repository.backfill_task_identifiers "
        "(server_modules/projects_repository.py). Never reach for a blanket "
        "bypass_rls=True on a write: that is correct only when every row "
        "gets the identical, non-tenant-specific value (the exact shape the "
        "already-fixed project_tasks status rename used, and it says so in "
        "its own comment) -- a real per-tenant value needs the per-row scope "
        "or it will cross-write another tenant's row."
    )
    lines.append(
        ""
    )
    lines.append(
        "If this statement is genuinely safe (self-detecting via a later "
        "validating DDL statement, already scoped some other way, or a "
        "deliberate, already-made product decision not to fix), add it to "
        "_ACCEPTED_UNSCOPED_DML in this file with a written verdict -- never "
        "weaken the scan to make it disappear."
    )
    return "\n".join(lines)


# ── Tests ────────────────────────────────────────────────────────────────────


class RlsDmlDriftTests(unittest.TestCase):
    """A DML statement on an RLS-protected table, with no scope, is
    structurally impossible to add here without this test naming it."""

    def test_no_new_unscoped_dml_on_an_rls_protected_table(self):
        found = _scan_for_unscoped_dml()
        accepted = set(_ACCEPTED_UNSCOPED_DML)
        new_offenders = sorted(set(found) - accepted)
        if new_offenders:
            self.fail(_failure_message(new_offenders, found))

    def test_the_allowlist_has_no_stale_entries(self):
        # An allowlist entry that has outlived the code it excuses is how
        # the next reader concludes a line is "already reviewed" when it no
        # longer exists at all.
        found = _scan_for_unscoped_dml()
        stale = sorted(set(_ACCEPTED_UNSCOPED_DML) - set(found))
        self.assertEqual(
            stale,
            [],
            "Allowlist entries no longer match any scanned source line -- "
            "remove them from _ACCEPTED_UNSCOPED_DML.",
        )

    def test_the_guard_catches_a_freshly_introduced_offender(self):
        # Proof the scanner is not vacuous on a SYNTHETIC line, independent
        # of anything currently on main.
        protected = {"agent_threads"}  # a real FORCE-RLS table, hand-named
        # here on purpose so this test does not depend on
        # _rls_protected_tables() succeeding.
        offending_lines = [
            "            await pool.execute(",
            '                """',
            "                DO $$",
            "                BEGIN",
            "                    UPDATE agent_threads SET title = 'x' WHERE title IS NULL;",
            "                END $$;",
            '                """',
            "            )",
        ]
        hits = [
            _dml_match(line.strip())
            for line in offending_lines
        ]
        hits = [hit for hit in hits if hit is not None and hit[1] in protected]
        self.assertEqual(
            hits,
            [("UPDATE", "agent_threads")],
            "The scanner failed to flag a textbook unscoped UPDATE nested "
            "inside a DO $$ ... END $$; block with no trailing semicolon of "
            "its own.",
        )

    def test_the_guard_catches_the_real_nested_do_block_offender(self):
        # Not a synthetic stand-in: this is the EXACT shape of the
        # still-allowlisted instance 2, scanned live off the real file, to
        # prove the predicate actually reaches inside a nested DO block on
        # the real source rather than only on a hand-built string.
        found = _scan_control_plane_repository()
        key = (
            "server_modules/control_plane_repository.py",
            "ensure_control_plane_schema",
            "UPDATE",
            "workspace_agent_installs",
        )
        self.assertIn(
            key,
            found,
            "The scanner no longer finds the known workspace_agent_installs "
            "label-dedupe UPDATE inside ensure_control_plane_schema() -- "
            "either the source changed shape or the scanner regressed. If "
            "the source changed, update this test; do not delete it.",
        )
        line_no, line_text = found[key]
        self.assertIn("UPDATE workspace_agent_installs", line_text)
        # No trailing semicolon on THIS line specifically -- the SET clause
        # continues on the next line -- which is exactly the shape a
        # `;`-anchored statement parser would misparse or skip.
        self.assertFalse(line_text.rstrip().endswith(";"))

    # ── Canary: the extraction itself must fail loudly, never scan nothing ──

    def test_rls_table_extraction_raises_on_empty_source(self):
        with self.assertRaises(RuntimeError):
            _parse_force_rls_tables_from_text("", source_description="<synthetic>")

    def test_rls_table_extraction_raises_when_no_line_matches(self):
        with self.assertRaises(RuntimeError):
            _parse_force_rls_tables_from_text(
                "-- a file full of comments and DDL that never FORCEs RLS\n"
                "CREATE TABLE decoy (id text);\n"
                "ALTER TABLE decoy ENABLE ROW LEVEL SECURITY;\n",
                source_description="<synthetic>",
            )

    def test_rls_table_extraction_succeeds_on_real_enable_rls_sql(self):
        # The positive control for the two canaries immediately above: the
        # real file must extract a healthy, non-trivial set. 43 tables carry
        # FORCE ROW LEVEL SECURITY as of 2026-08-18; asserting a floor
        # rather than the exact count so an unrelated future table addition
        # does not make this test the thing that has to be edited.
        tables = _rls_protected_tables()
        self.assertGreaterEqual(len(tables), 40)
        self.assertIn("workspace_agent_installs", tables)
        self.assertIn("project_tasks", tables)
        self.assertIn("agent_channel_bindings", tables)

    def test_boot_schema_block_extraction_raises_when_the_block_is_missing(self):
        synthetic_lines = [
            "SOME_OTHER_CONSTANT = 1",
            "",
            "async def some_unrelated_function() -> None:",
            "    return None",
        ]
        with self.assertRaises(RuntimeError):
            _extract_named_block(
                synthetic_lines,
                _CONTROL_PLANE_SCHEMA_SQL_START,
                _TRIPLE_QUOTE_ALONE,
                label="CONTROL_PLANE_SCHEMA_SQL constant",
            )
        with self.assertRaises(RuntimeError):
            _extract_named_block(
                synthetic_lines,
                _ENSURE_CONTROL_PLANE_SCHEMA_START,
                _TOP_LEVEL_DEF_OR_CLASS,
                label="ensure_control_plane_schema() function",
            )

    def test_boot_schema_block_extraction_succeeds_on_the_real_file(self):
        # Positive control: the real file must yield two non-empty ranges.
        lines = _CONTROL_PLANE_REPOSITORY_PATH.read_text(encoding="utf-8").split("\n")
        ranges = _boot_schema_scope_ranges(lines)
        self.assertEqual(len(ranges), 2)
        for start, end in ranges:
            self.assertGreater(end, start + 1)

    def test_migrations_directory_scan_raises_on_an_empty_directory(self):
        # _scan_migrations_directory reads a real glob, so exercise the
        # canary through a tiny synthetic monkeypatch of the glob target
        # rather than deleting real files.
        import tempfile
        import unittest.mock as mock

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch(
                f"{__name__}._MIGRATIONS_DIR", Path(tmp)
            ), self.assertRaises(RuntimeError):
                _scan_migrations_directory()


if __name__ == "__main__":
    unittest.main()
