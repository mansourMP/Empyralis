"""Regression cover for the audit-confirmed gap: uq_agent_channel_bindings_
inbound_owner_v2 (control_plane_repository.py) enforces one-inbound-owner-
per-channel-endpoint for every channel_key that agent_specialist_repository.
_INBOUND_OWNER_CHANNEL_KEYS names as requiring an inbound owner EXCEPT
'github', so two agents in the same workspace could each claim ownership of
the same GitHub channel (endpoint) with no DB-level rejection -- the
app-level pre-check (agent_specialist_repository._assert_unique_inbound_
channel_owners) already covered 'github', but check-then-insert is not
atomic, so a race between two concurrent binds could let both win. Only
last-write-wins on the column, and agent_channel_router's linear scan makes
read-time resolution non-deterministic whenever more than one row matches.
This is the same class of bug fixed for Slack by commit b7f17d367 /
migrations/fix_slack_channel_uniqueness.sql.

These tests are pure string/regex checks against the SQL source (no DB
required, matching this repo's convention -- see
test_control_plane_repository_slack_channel_uniqueness.py and
test_preflight_rls.py's MigrationParserTests for the same style).
"""

from __future__ import annotations

import re
from pathlib import Path

from server_modules import control_plane_repository as repository

ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = ROOT / "migrations" / "fix_github_channel_uniqueness.sql"

INDEX_NAME = "uq_agent_channel_bindings_inbound_owner_v2"

# Every channel_key the guarantee is supposed to cover after this fix.
EXPECTED_CHANNEL_KEYS = {
    "telegram", "telegram_bot", "discord", "discord_bot",
    "whatsapp", "email", "phone", "web_chat", "slack", "github",
}


def _extract_channel_keys(sql: str, *, index_name: str = INDEX_NAME) -> set[str]:
    """Pull the channel_key IN (...) allowlist out of a
    `CREATE UNIQUE INDEX IF NOT EXISTS <index_name> ...` statement,
    tolerant of the list wrapping across lines."""
    pattern = re.compile(
        r"CREATE\s+UNIQUE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+"
        + re.escape(index_name)
        + r".*?channel_key\s+IN\s*\(([^)]*)\)",
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(sql)
    assert match, f"no `channel_key IN (...)` clause found for {index_name!r}"
    return {item.strip().strip("'") for item in match.group(1).split(",")}


def test_schema_sql_covers_github_in_the_inbound_owner_index() -> None:
    """The bug: 'github' is present in agent_specialist_repository.
    _INBOUND_OWNER_CHANNEL_KEYS (the app-level pre-check) but was missing
    from this DB index's channel_key list, so
    uq_agent_channel_bindings_inbound_owner_v2 silently never applied to
    GitHub bindings at all -- the app-level check-then-insert race was not
    backed by a DB-level constraint."""
    keys = _extract_channel_keys(repository.CONTROL_PLANE_SCHEMA_SQL)
    assert "github" in keys, (
        "'github' is missing from uq_agent_channel_bindings_inbound_owner_v2's "
        "channel_key allowlist in control_plane_repository.py -- two agents "
        "in one workspace can bind the same GitHub channel with no DB-level "
        "rejection."
    )


def test_schema_sql_still_covers_every_previously_guaranteed_channel() -> None:
    """Regression guard: adding 'github' must not have dropped any of the
    channel keys the guarantee already covered (including 'slack', added by
    b7f17d367)."""
    keys = _extract_channel_keys(repository.CONTROL_PLANE_SCHEMA_SQL)
    assert keys == EXPECTED_CHANNEL_KEYS


def test_schema_sql_index_predicate_still_requires_inbound_owner_and_endpoint() -> None:
    """The channel_key list is only one clause of the predicate -- confirm
    the fix didn't accidentally widen the guarantee by dropping the other
    guards (enabled, is_inbound_owner, non-empty endpoint_key)."""
    sql = repository.CONTROL_PLANE_SCHEMA_SQL
    start = sql.index(f"CREATE UNIQUE INDEX IF NOT EXISTS {INDEX_NAME}")
    end = sql.index(";", start)
    clause = sql[start:end]

    assert "enabled = TRUE" in clause
    assert "lower(COALESCE(binding->>'is_inbound_owner', 'false')) = 'true'" in clause
    assert "NULLIF(lower(COALESCE(binding->>'endpoint_key', '')), '') IS NOT NULL" in clause


# ── Migration file ───────────────────────────────────────────────────────

def _read_migration() -> str:
    assert MIGRATION_PATH.is_file(), f"expected a migration at {MIGRATION_PATH}"
    return MIGRATION_PATH.read_text(encoding="utf-8")


def test_migration_file_exists_and_is_registered_in_the_migrations_dir() -> None:
    assert MIGRATION_PATH.parent == ROOT / "migrations"
    assert _read_migration().strip(), "migration file must not be empty"


def test_migration_applies_the_identical_corrected_predicate() -> None:
    """The migration's end-state index definition must exactly match what
    control_plane_repository.py now creates for a fresh database -- an
    already-migrated database and a brand-new database must converge on the
    same schema."""
    migration_keys = _extract_channel_keys(_read_migration())
    schema_keys = _extract_channel_keys(repository.CONTROL_PLANE_SCHEMA_SQL)
    assert migration_keys == schema_keys == EXPECTED_CHANNEL_KEYS


def test_migration_dedupes_github_conflicts_before_rebuilding_the_index() -> None:
    """CRITICAL SAFETY property: a CREATE UNIQUE INDEX over data that
    already violates it fails outright. The migration must resolve any
    pre-existing duplicate GitHub inbound-owner bindings (there may be none
    in production today, but the migration must not assume that) *before*
    it drops/recreates the index, every time -- order matters, not just
    presence."""
    sql = _read_migration()

    # The dedupe is a `WITH ... UPDATE agent_channel_bindings ...` statement
    # -- the CTE (which carries the `channel_key = 'github'` scope) precedes
    # the UPDATE keyword textually, so the clause starts at the WITH, not at
    # the UPDATE.
    dedupe_match = re.search(r"WITH\s+\w+\s+AS\s*\(", sql, re.IGNORECASE)
    drop_match = re.search(rf"DROP\s+INDEX\s+IF\s+EXISTS\s+{re.escape(INDEX_NAME)}", sql, re.IGNORECASE)
    create_match = re.search(
        rf"CREATE\s+UNIQUE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+{re.escape(INDEX_NAME)}", sql, re.IGNORECASE
    )

    assert dedupe_match, "migration must dedupe existing rows (WITH ... UPDATE ...) before rebuilding the index"
    assert drop_match, f"migration must DROP INDEX IF EXISTS {INDEX_NAME}"
    assert create_match, f"migration must recreate {INDEX_NAME}"
    assert re.search(r"UPDATE\s+agent_channel_bindings\b", sql[dedupe_match.start():drop_match.start()], re.IGNORECASE), (
        "the WITH clause found before DROP INDEX must actually be the dedupe UPDATE"
    )
    assert dedupe_match.start() < drop_match.start() < create_match.start(), (
        "dedupe must happen strictly before the index is dropped and recreated, "
        "or the CREATE UNIQUE INDEX can fail on live duplicate data"
    )

    # Scoped to channel_key = 'github' -- every other covered channel_key
    # has been enforced by some version of this index since it was
    # introduced (or, for 'slack', deduped by its own migration), so it
    # cannot hold a live conflict this migration needs to resolve; the
    # dedupe should not silently touch unrelated rows.
    dedupe_clause = sql[dedupe_match.start():drop_match.start()]
    assert "channel_key = 'github'" in dedupe_clause

    # The demotion must not delete data or disable the whole binding --
    # only strip inbound-owner status, so the losing agent's binding record
    # (and any other metadata on it) survives intact.
    assert "is_inbound_owner" in dedupe_clause
    assert re.search(r"DELETE\s+FROM\s+agent_channel_bindings", sql, re.IGNORECASE) is None


def test_migration_picks_a_deterministic_winner_per_duplicate_group() -> None:
    """The dedupe must partition by the same column set the index covers
    (tenant, workspace, channel_key, lower(endpoint_key)) and order
    deterministically, so which agent keeps ownership never depends on
    unspecified row order."""
    sql = _read_migration()
    partition_match = re.search(
        r"PARTITION\s+BY\s+tenant_id,\s*workspace_id,\s*channel_key,\s*lower\(binding->>'endpoint_key'\)",
        sql,
        re.IGNORECASE,
    )
    assert partition_match, "dedupe window function must partition by the index's exact column set"
    assert re.search(r"ORDER\s+BY\s+updated_at\s+DESC", sql, re.IGNORECASE), (
        "dedupe must order deterministically (most-recently-updated wins), not rely on physical row order"
    )


def test_migration_is_wrapped_in_a_transaction() -> None:
    """Dedupe + index rebuild must be atomic: either both happen, or
    neither does. Matches the majority convention in migrations/ (e.g.
    enable_rls.sql, add_workspace_inventory_items.sql). A leading `--`
    comment header (this migration has one explaining the safety rationale)
    doesn't count against this -- only real SQL statements do."""
    lines = [
        line for line in _read_migration().splitlines()
        if line.strip() and not line.strip().startswith("--")
    ]
    assert lines, "migration file has no executable SQL"
    assert lines[0].strip() == "BEGIN;", f"first statement must be BEGIN;, got {lines[0]!r}"
    assert lines[-1].strip() == "COMMIT;", f"last statement must be COMMIT;, got {lines[-1]!r}"
