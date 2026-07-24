"""Regression cover for STEP 5 (agent-identity plan): workspace_agent_installs
had no DB-level uniqueness guarantee on `label` -- fleet_create_agent's
auto-naming path collision-checks in Python (agent_name_pool.assign_agent_name)
but the manual rename path (fleet_configure_agent's display_name PATCH) wrote
straight to the column with zero checking, and no migration had ever added a
UNIQUE constraint. Two agents in the same workspace could share a display
name with nothing to catch it -- ambiguous for a human reading the fleet
roster AND for the closed-roster mention autocomplete that has to resolve a
typed name back to exactly one agent_id.

Unlike the Slack/GitHub channel-binding fixes (see
test_control_plane_repository_slack_channel_uniqueness.py /
test_control_plane_repository_github_channel_uniqueness.py), this is a BRAND
NEW index -- there is no existing-name trick to make a bare
`CREATE UNIQUE INDEX IF NOT EXISTS` a guaranteed no-op on an already-
provisioned database. So it is not baked unconditionally into
CONTROL_PLANE_SCHEMA_SQL (which re-runs on every process boot, including
prod's already-provisioned one). Instead it lives as a guarded step inside
ensure_control_plane_schema, mirroring how excl_one_tenant_per_workspace (the
other brand-new, non-renamed constraint in that same function) is applied:
dedupe first, then attempt the constraint, wrapped in try/except so an
unexpected failure degrades to a loud warning instead of crashing bootstrap.

These are pure string/regex checks against the source (no DB required),
matching this repo's convention -- see test_preflight_rls.py's
MigrationParserTests and test_control_plane_repository_slack_channel_
uniqueness.py for the same style.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

from server_modules import control_plane_repository as repository

ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = ROOT / "migrations" / "add_workspace_agent_installs_label_uniqueness.sql"

INDEX_NAME = "uq_workspace_agent_installs_label"

# [\s"]+ (rather than \s+) between tokens: the live source builds this
# statement from adjacent-concatenated Python string literals
# ("CREATE ... " "ON ... " "WHERE ..."), so inspect.getsource() returns text
# with stray `"` characters at each literal boundary that the raw migration
# SQL (no quotes at all) doesn't have. One pattern matches both forms.
_INDEX_DEFINITION_PATTERN = re.compile(
    r"CREATE[\s\"]+UNIQUE[\s\"]+INDEX[\s\"]+IF[\s\"]+NOT[\s\"]+EXISTS[\s\"]+"
    + re.escape(INDEX_NAME)
    + r"[\s\"]+ON[\s\"]+workspace_agent_installs\(tenant_id,[\s\"]*workspace_id,[\s\"]*lower\(label\)\)[\s\"]+"
    r"WHERE[\s\"]+label[\s\"]+IS[\s\"]+NOT[\s\"]+NULL[\s\"]+AND[\s\"]+label[\s\"]+<>[\s\"]+''",
    re.IGNORECASE,
)


def _bootstrap_source() -> str:
    return inspect.getsource(repository.ensure_control_plane_schema)


# ── The live bootstrap path (server_modules/control_plane_repository.py) ────


def test_bootstrap_creates_the_unique_index_scoped_per_workspace_case_insensitive() -> None:
    source = _bootstrap_source()
    assert _INDEX_DEFINITION_PATTERN.search(source), (
        f"{INDEX_NAME} must be defined on (tenant_id, workspace_id, lower(label)) "
        "with a non-null/non-empty label predicate"
    )


def test_bootstrap_dedupes_before_attempting_the_constraint() -> None:
    """CRITICAL SAFETY property: this is a brand-new index (no existing-name
    no-op trick like uq_agent_channel_bindings_inbound_owner_v2 has), so a
    bare CREATE UNIQUE INDEX would attempt real enforcement on every boot --
    including prod's already-provisioned DB -- and crash outright if any
    duplicate labels already exist there. The dedupe DO $$ block must run
    strictly before the CREATE UNIQUE INDEX attempt."""
    source = _bootstrap_source()
    label_section = source[source.index("STEP 5 (agent-identity plan)"):]
    dedupe_match = re.search(r"DO\s+\$\$", label_section)
    create_match = _INDEX_DEFINITION_PATTERN.search(label_section)
    assert dedupe_match, "expected a DO $$ dedupe block in the label-uniqueness section"
    assert create_match, "expected the CREATE UNIQUE INDEX statement in the label-uniqueness section"
    assert dedupe_match.start() < create_match.start(), (
        "the dedupe block must run before the CREATE UNIQUE INDEX attempt, "
        "or it can fail outright on live duplicate data"
    )


def test_bootstrap_wraps_the_attempt_in_try_except_so_it_cannot_crash_bootstrap() -> None:
    """Matches the excl_one_tenant_per_workspace guard immediately above it in
    the same function -- a brand-new constraint must never be able to take
    the whole process down if it fails for an unexpected reason."""
    source = _bootstrap_source()
    label_index = source.index(f"CREATE UNIQUE INDEX IF NOT EXISTS {INDEX_NAME}")
    try_index = source.rindex("try:", 0, label_index)
    except_index = source.index("except Exception as exc", label_index)
    assert try_index < label_index < except_index, (
        "the CREATE UNIQUE INDEX attempt must sit inside a try/except guard"
    )
    guarded_clause = source[try_index:except_index + 400]
    assert "LOGGER.warning" in guarded_clause, "a failure here must log loudly, not fail silently"


def test_dedupe_partitions_per_tenant_and_workspace_case_insensitively() -> None:
    source = _bootstrap_source()
    assert "PARTITION BY tenant_id, workspace_id, lower(label)" in source


def test_dedupe_orders_deterministically_oldest_row_keeps_its_name() -> None:
    """The earliest-created row in a duplicate group keeps its name
    unchanged; every later duplicate gets renamed -- so which agent "wins"
    an existing name never depends on unspecified row order."""
    source = _bootstrap_source()
    assert re.search(r"ORDER BY\s+created_at\s+ASC,\s*id\s+ASC", source, re.IGNORECASE)


def test_dedupe_never_deletes_rows_only_renames_them() -> None:
    source = _bootstrap_source()
    label_section = source[source.index("STEP 5 (agent-identity plan)"):]
    assert re.search(r"DELETE\s+FROM\s+workspace_agent_installs", label_section, re.IGNORECASE) is None


def test_dedupe_suffix_check_is_scoped_to_the_whole_workspace_not_just_the_duplicate_group() -> None:
    """The collision-proofing property: the "smallest available ' N' suffix"
    search must check against every OTHER label already in the workspace
    (not merely the other members of the duplicate group), so renaming a
    duplicate can never manufacture a NEW collision against an unrelated
    agent that already happens to be named e.g. "Atlas 2"."""
    source = _bootstrap_source()
    label_section = source[source.index("STEP 5 (agent-identity plan)"):]
    assert re.search(
        r"WHERE\s+w\.tenant_id\s*=\s*dup\.tenant_id\s+AND\s+w\.workspace_id\s*=\s*dup\.workspace_id\s+"
        r"AND\s+lower\(w\.label\)\s*=\s*lower\(candidate_label\)",
        label_section,
        re.IGNORECASE,
    ), "the candidate-label collision check must be scoped to the workspace, checked live against the table"


# ── Migration file (applies the identical logic by hand to an existing DB) ──


def _read_migration() -> str:
    assert MIGRATION_PATH.is_file(), f"expected a migration at {MIGRATION_PATH}"
    return MIGRATION_PATH.read_text(encoding="utf-8")


def test_migration_file_exists_and_is_registered_in_the_migrations_dir() -> None:
    assert MIGRATION_PATH.parent == ROOT / "migrations"
    assert _read_migration().strip(), "migration file must not be empty"


def test_migration_dedupes_before_creating_the_unique_index() -> None:
    sql = _read_migration()
    dedupe_match = re.search(r"DO\s+\$\$", sql)
    create_match = _INDEX_DEFINITION_PATTERN.search(sql)
    assert dedupe_match and create_match
    assert dedupe_match.start() < create_match.start(), (
        "dedupe must happen strictly before the index is created, "
        "or the CREATE UNIQUE INDEX can fail on live duplicate data"
    )


def test_migration_creates_the_identical_index_definition_as_the_bootstrap_path() -> None:
    migration_sql = _read_migration()
    bootstrap_source = _bootstrap_source()
    assert _INDEX_DEFINITION_PATTERN.search(migration_sql), "migration index definition drifted from the expected shape"
    assert _INDEX_DEFINITION_PATTERN.search(bootstrap_source), "bootstrap index definition drifted from the expected shape"


def test_migration_never_deletes_rows_only_renames_them() -> None:
    sql = _read_migration()
    assert re.search(r"DELETE\s+FROM\s+workspace_agent_installs", sql, re.IGNORECASE) is None


def test_migration_picks_a_deterministic_winner_per_duplicate_group() -> None:
    sql = _read_migration()
    assert re.search(
        r"PARTITION\s+BY\s+tenant_id,\s*workspace_id,\s*lower\(label\)",
        sql,
        re.IGNORECASE,
    ), "dedupe window function must partition by the index's exact column set"
    assert re.search(r"ORDER\s+BY\s+created_at\s+ASC,\s*id\s+ASC", sql, re.IGNORECASE), (
        "dedupe must order deterministically (earliest-created keeps its name), not rely on physical row order"
    )


def test_migration_is_wrapped_in_a_transaction() -> None:
    lines = [
        line for line in _read_migration().splitlines()
        if line.strip() and not line.strip().startswith("--")
    ]
    assert lines, "migration file has no executable SQL"
    assert lines[0].strip() == "BEGIN;", f"first statement must be BEGIN;, got {lines[0]!r}"
    assert lines[-1].strip() == "COMMIT;", f"last statement must be COMMIT;, got {lines[-1]!r}"
