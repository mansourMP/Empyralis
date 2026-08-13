"""Startup preflight checks — verify the platform can boot before serving.

Implements Phase T: every required dependency is checked at startup.
A failure names the gap and raises :class:`PreflightError` so the
process never boots half-alive.

Checks
------
1. **Local-stack DATABASE_URL** — a dev/test/local boot must have
   DATABASE_URL set explicitly; it is never allowed to boot on "whatever
   the environment happened to contain" (MAN-202/MAN-268).
2. **Rust runtime kernel** — binary must exist (built or env-var path).
3. **PostgreSQL** — DATABASE_URL must be set, pool must be reachable,
   and ``workspace_agent_installs`` must have the stage_4b columns.
4. **Row-Level Security** — two halves, because either one alone is blind:
   (a) *enforcement* — every table listed in ``migrations/enable_rls.sql``
   must have RLS enabled + FORCEd + a policy on the live database;
   (b) *coverage* — every table in the live database that carries a
   ``tenant_id``/``workspace_id`` column must either be listed in that
   migration or be recorded in :data:`_RLS_COVERAGE_EXCEPTIONS` with a
   written reason. Without (b) the check is circular: it verified exactly
   the tables the migration already knew about, so a tenant-scoped table
   nobody added to the migration was both unprotected *and* unverified
   while preflight reported "all checks passed". 60 tables were in that
   state on 2026-08-08.
   The exception registry is seeded with those 60 so this check catches
   NEW drift immediately rather than waiting on a remediation that can
   only be done a few tables at a time. Each entry carries its audit
   verdict; working the list down is the direction of travel, and
   ``server_modules/tests/test_preflight_rls_coverage.py`` fails if an
   entry outlives the gap it describes.
5. **Redis** — REDIS_URL (default ``redis://localhost:6379``) must PONG.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
from typing import Any, Dict, List, Optional

LOGGER = logging.getLogger(__name__)

# Columns added by migrations/stage_4b_agent_isolation.sql
_STAGE_4B_COLUMNS = (
    "enabled_tools",
    "enabled_connectors",
    "channel_bindings",
    "subagents_enabled",
    "hardware_access",
)

# Redis is not yet a hard platform dependency — set this env var to
# skip the Redis check (e.g. in CI where Redis isn't available).
def _redis_check_skipped() -> bool:
    return os.getenv("EMPYRALIS_SKIP_REDIS_CHECK", "").strip().lower() in {
        "1", "true", "yes",
    }


class PreflightError(RuntimeError):
    """Raised when a startup preflight check fails.

    The message names the missing dependency so the operator can fix it
    before the process ever serves traffic.
    """


def _redacted_dsn(dsn: str) -> str:
    """Return *dsn* with password replaced by ``***`` for safe logging."""
    if "@" not in dsn:
        return dsn
    prefix, _, rest = dsn.partition("://")
    userinfo, _, hostport = rest.partition("@")
    if ":" in userinfo:
        user, _ = userinfo.split(":", 1)
        return f"{prefix}://{user}:***@{hostport}"
    return f"{prefix}://{userinfo}@{hostport}"


# ── local-stack DATABASE_URL (MAN-202 / MAN-268) ────────────────────────
#
# server_modules/runtime_config.py used to call a bare load_dotenv(), which
# lets python-dotenv search UP the directory tree from the process cwd for
# the nearest ".env". A subagent working inside a git worktree nested under
# the real checkout (.claude/worktrees/<name>/) has no .env of its own, so
# that search walked straight past the worktree into the *real* repo root's
# .env and silently handed a "throwaway" local stack production-adjacent
# credentials — DATABASE_URL included — while the agent believed it was
# running in isolation. That is very likely what let an agent wipe the
# founder's local database, and it was hit again for real running the test
# suite (MAN-268). The dotenv load itself is now scoped to an explicit path
# (no more upward search), but that alone only closes the ONE way
# DATABASE_URL could arrive unexamined. This check makes the precondition
# itself loud: a dev/test/local boot must have DATABASE_URL set on purpose
# — from an explicit shell export, or from this exact checkout's own
# repo-root .env — never merely "whatever the environment happened to
# contain."
#
# Skipped once durable_runtime_required() is true (beta/staging/production):
# that path already has its own DATABASE_URL requirement via
# _check_postgres() below, so this would just be a redundant error.

_LOCAL_STACK_ENV_TOKENS = {"dev", "development", "local", "test", "testing"}


def _resolved_environment_for_local_stack_check() -> str:
    return str(
        os.getenv("EMPYRALIS_DEPLOY_ENV") or os.getenv("ORION_ENV") or os.getenv("ENV") or os.getenv("NODE_ENV") or ""
    ).strip().lower()


def _local_stack_database_url_check_skipped() -> bool:
    """Deliberate, loud escape hatch (mirrors the Redis/RLS skips).

    Set EMPYRALIS_ALLOW_IMPLICIT_LOCAL_DATABASE_URL=true only when an
    operator has knowingly chosen to run this dev/test/local boot on the
    SQLite fallback with no Postgres configured at all. Bypassing is logged
    at warning level on every boot so it is never a silent choice.
    """
    return os.getenv("EMPYRALIS_ALLOW_IMPLICIT_LOCAL_DATABASE_URL", "").strip().lower() in {
        "1", "true", "yes",
    }


def _check_local_stack_database_url() -> Optional[str]:
    """Return ``None`` if DATABASE_URL is explicit, this isn't a dev/test/
    local boot, or durable Postgres is already required elsewhere.

    Refuses to boot a dev/test/local process with DATABASE_URL unset. See
    the module-level comment above for why: this is the exact precondition
    that let python-dotenv's directory-walking search silently resolve a
    worktree's cwd up to the real repo root's .env.
    """
    from server_modules.db import durable_runtime_required as _durable_required  # noqa: PLC0415

    if _durable_required():
        return None  # beta/staging/production already require DATABASE_URL via _check_postgres()

    if _resolved_environment_for_local_stack_check() not in _LOCAL_STACK_ENV_TOKENS:
        return None  # not a recognized local/dev/test boot — leave as-is

    if os.getenv("DATABASE_URL", "").strip():
        return None

    if _local_stack_database_url_check_skipped():
        LOGGER.warning(
            "preflight: local-stack DATABASE_URL requirement BYPASSED "
            "(EMPYRALIS_ALLOW_IMPLICIT_LOCAL_DATABASE_URL set) — this process will run "
            "on the SQLite fallback with no Postgres configured."
        )
        return None

    return (
        "DATABASE_URL is not set for this dev/test/local boot. Refusing to start rather "
        "than silently inherit whatever the environment happens to contain — this is the "
        "exact precondition behind MAN-202/MAN-268, where an unscoped dotenv load let a "
        "git worktree's cwd walk up to the real repo root's .env and hand a 'throwaway' "
        "local stack production-adjacent credentials.\n"
        "  Set DATABASE_URL explicitly to a database you know is disposable, e.g.:\n"
        "    export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/empyralis_test\n"
        "  Or set EMPYRALIS_ALLOW_IMPLICIT_LOCAL_DATABASE_URL=true to run on the SQLite "
        "fallback with no Postgres at all (rarely what you want for the seeded-data UI "
        "testing workflow described in CLAUDE.md)."
    )


# ── removed knowledge RAG pipeline ───────────────────────────────────

# The embeddings/RAG knowledge pipeline (knowledge_rag_service.py, the
# knowledge_sources/chunks/embeddings/retrieval_events tables, and the
# /knowledge/verify route) was removed 2026-08-08. CLAUDE.md already
# recorded the standing decision to prefer agentic search; the pipeline
# contradicted it and, verified before removal, fed nothing -- the only
# non-test reader of retrieve_knowledge() was the endpoint whose sole
# purpose was to report that the index worked.
#
# These env vars configured ONLY that pipeline. Per CLAUDE.md's rule that
# removed config must fail loudly rather than fall through to a default
# (see model_router's deliberately-retained `vertex` branch), a boot that
# still carries one refuses to start: a silently-ignored
# EMPYRALIS_RAG_EMBEDDING_BACKEND would leave an operator believing an
# embedding backend is selected and running when no such code path exists.
_REMOVED_KNOWLEDGE_RAG_ENV_VARS = (
    "EMPYRALIS_RAG_EMBEDDING_BACKEND",
    "EMPYRALIS_RAG_SENTENCE_TRANSFORMERS_MODEL",
    "EMPYRALIS_RAG_MAX_SOURCE_FILE_BYTES",
    "EMPYRALIS_RAG_LANCEDB_URI",
    "OPENAI_EMBEDDINGS_URL",
)


def _check_removed_knowledge_rag_config() -> Optional[str]:
    """Return ``None`` unless the environment still configures the removed
    embeddings/RAG knowledge pipeline.

    Refuses to boot rather than ignore the setting. The variables below have
    no reader left anywhere in the tree; honouring them silently is exactly
    the "stale config falls through to a default" failure CLAUDE.md calls
    out. Unset them -- there is nothing to point them at.
    """
    present = sorted(name for name in _REMOVED_KNOWLEDGE_RAG_ENV_VARS if os.getenv(name, "").strip())
    if not present:
        return None
    return (
        "The embeddings/RAG knowledge pipeline was removed (2026-08-08), but this "
        f"environment still sets: {', '.join(present)}.\n"
        "  These variables have no reader left in the codebase. Refusing to start rather "
        "than ignore them silently, so nobody is left believing an embedding backend, a "
        "vector store or a source-size cap is in force when none exists.\n"
        "  Uploaded knowledge files are unaffected: they stay on disk under the workspace "
        "knowledge dir and are still read at turn time by "
        "unified_memory_service.search_unified_memory_documents (keyword search, no index).\n"
        f"  Fix: unset {', '.join(present)} in this process's environment / .env / unit file."
    )


# ── kernel ───────────────────────────────────────────────────────────

def _check_kernel() -> Optional[str]:
    """Return ``None`` if the kernel binary is found and fresh, or an error
    string.

    "Fresh" means: not older than the Rust source that defines its
    enforcement decisions. MAN-306 — a compiled kernel binary older than
    ``empyralis-runtime-kernel/src/*.rs`` silently keeps enforcing whatever
    policy was current when it was last built, forever, with no error
    anywhere. A source fix (2026-07-28, MAN-108 Bug 2, widening
    TERMINAL_RUN_STATUSES) shipped through the documented deploy flow
    without ever running `cargo build`, so the binary kept treating an
    ordinary completed task run's archive write as non-terminal — exactly
    reproducing "archive_non_terminal_run_requires_review" on every normal
    assignment. This check makes that drift a boot-time failure instead of
    a silent one, the same posture as the DATABASE_URL check above.
    """
    try:
        from server_modules.rust_runtime_kernel_client import (  # noqa: PLC0415
            KERNEL_ENV_VAR,
            KERNEL_STALENESS_ALLOW_ENV_VAR,
            runtime_kernel_binary,
            runtime_kernel_staleness_allowed,
            stale_kernel_source_file,
        )
    except ImportError:
        return None  # client module not available — skip check
    repo_root = os.environ.get("EMPYRALIS_REPO_ROOT") or os.getcwd()
    binary = runtime_kernel_binary()
    if binary is None:
        return (
            f"Rust runtime kernel binary not found.\n"
            f"  Build: cd {repo_root} && cargo build --release --manifest-path empyralis-runtime-kernel/Cargo.toml\n"
            f"  Or set {KERNEL_ENV_VAR} to the binary path."
        )
    if runtime_kernel_staleness_allowed():
        return None
    newer_source = stale_kernel_source_file(binary)
    if newer_source is None:
        return None
    return (
        f"Rust runtime kernel binary is stale: {newer_source} was modified after "
        f"{binary} was built. A stale binary keeps enforcing whatever policy was "
        f"compiled in at build time — silently, with no error anywhere else — "
        f"which is exactly how MAN-306 happened (a binary built before the "
        f"2026-07-28 TERMINAL_RUN_STATUSES fix kept blocking every ordinary "
        f"completed task run's archive write as if the fix had never shipped).\n"
        f"  Rebuild: cd {repo_root} && cargo build --release --manifest-path empyralis-runtime-kernel/Cargo.toml\n"
        f"  Or set {KERNEL_STALENESS_ALLOW_ENV_VAR}=true to boot anyway (not recommended outside local dev)."
    )


# ── PostgreSQL ────────────────────────────────────────────────────────

async def _check_postgres() -> Optional[str]:
    """Return ``None`` if Postgres is reachable and stage_4b columns exist.

    When ``DATABASE_URL`` is not set and durable runtime is not required
    (local dev), the check is skipped — SQLite fallback is fine.
    """
    from server_modules.db import durable_runtime_required as _durable_required  # noqa: PLC0415

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        if _durable_required():
            return (
                "DATABASE_URL is not set but durable runtime is required "
                f"(env={os.getenv('EMPYRALIS_DEPLOY_ENV') or os.getenv('ORION_ENV') or 'unset'}). "
                "Set DATABASE_URL or clear the production env flag for local dev."
            )
        return None  # local dev — SQLite fallback is fine

    try:
        import asyncpg  # noqa: PLC0415
    except ImportError:
        if _durable_required():
            return (
                "asyncpg is not installed but Postgres is required for durable runtime. "
                "Install: pip install asyncpg"
            )
        return None  # local dev — without asyncpg installed, skip the check

    conn = None
    try:
        conn = await asyncpg.connect(database_url, timeout=10)
    except Exception as exc:
        if _durable_required():
            return (
                f"Postgres unreachable at {_redacted_dsn(database_url)}: {exc}\n"
                f"  Verify DATABASE_URL is correct and the database is running."
            )
        LOGGER.warning("preflight: Postgres unreachable (%s) — continuing with SQLite fallback.", exc)
        return None

    try:
        # Verify workspace_agent_installs has stage_4b columns
        existing = await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'workspace_agent_installs' "
            "AND column_name = ANY($1)",
            list(_STAGE_4B_COLUMNS),
        )
        existing_names = {row["column_name"] for row in existing}
        missing = [c for c in _STAGE_4B_COLUMNS if c not in existing_names]
        if missing:
            return (
                f"workspace_agent_installs is missing stage_4b columns: {', '.join(missing)}\n"
                f"  Apply: psql <DATABASE_URL> -f migrations/stage_4b_agent_isolation.sql"
            )
    finally:
        await conn.close()

    return None


# ── Row-Level Security (tenant isolation) ─────────────────────────────
#
# Tenant isolation is enforced by Postgres RLS installed by
# migrations/enable_rls.sql. Nothing else applies or verifies it, so a deploy
# that skips the migration — or a failover to an un-migrated replica, or a new
# tenant table added without a policy — would boot "healthy" with isolation
# silently OFF and cross-tenant reads possible. This check refuses to boot in
# that state.

def _rls_check_skipped() -> bool:
    """Deliberate, loud escape hatch (mirrors the Redis skip).

    Set EMPYRALIS_SKIP_RLS_CHECK=true only when an operator has knowingly
    accepted running without verified tenant isolation. Bypassing is logged at
    error level on every boot so it is never a silent choice.
    """
    return os.getenv("EMPYRALIS_SKIP_RLS_CHECK", "").strip().lower() in {
        "1", "true", "yes",
    }


def _tenant_scoped_tables_from_migration() -> List[str]:
    """Authoritative list of tenant-scoped tables, parsed from enable_rls.sql.

    Parsed (not hardcoded) so the check tracks the migration automatically —
    a table added to the migration is verified without editing this file.
    """
    repo_root = os.environ.get("EMPYRALIS_REPO_ROOT") or os.getcwd()
    path = os.path.join(repo_root, "migrations", "enable_rls.sql")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            sql = handle.read()
    except OSError:
        return []
    matches = re.findall(
        r"ALTER\s+TABLE\s+(?:public\.)?([a-zA-Z_][a-zA-Z0-9_]*)\s+ENABLE\s+ROW\s+LEVEL\s+SECURITY",
        sql,
        flags=re.IGNORECASE,
    )
    return sorted({name.lower() for name in matches})


# ── RLS coverage: which tables SHOULD be in the migration ─────────────
#
# _tenant_scoped_tables_from_migration() above answers "did the tables the
# migration names get their policies?" — it cannot answer "does the migration
# name every table that needs one", because it derives its own expectations
# from that same file. That circularity is why 30+ tenant-scoped tables sat
# unprotected AND unreported while preflight said "all checks passed".
#
# The coverage check closes it from the other end: ask the LIVE database which
# tables carry a tenant_id/workspace_id column, and require each one to be
# either covered by the migration or listed below with a written reason.
#
# One honest limit, stated rather than papered over: server.py's lifespan runs
# preflight_or_raise() BEFORE control_plane_repository.ensure_control_plane_
# schema(), so a table that boot itself creates is invisible on the boot that
# creates it and is caught on the NEXT one. Deploys restart, so the lag is one
# restart, not forever — but this is a drift alarm, not a gate a new table
# cannot slip past for a single boot. Moving the check after schema bootstrap
# would close that, at the cost of letting the server touch the database
# before isolation has been verified; refusing to serve unverified is the more
# important of the two, so the ordering stays.
#
# Adding a table here is a deliberate, reviewed act — it is the ONLY way to
# have a tenant-scoped table without a policy, and every entry states why.
# Entries are the 2026-08-08 audit backlog: they exist so the check starts
# catching NEW drift immediately instead of waiting for a 30-table
# remediation that can only be done a few tables at a time (turning RLS on
# for a table whose queries are not tenant-scoped makes its reads silently
# return zero rows — see the MAN-109 comment in migrations/enable_rls.sql).
# Working an entry off this list — not growing it — is the direction of
# travel. A new table belongs in migrations/enable_rls.sql, not here.
# Verdict vocabulary for the seeded backlog. Each string says what was found
# on 2026-08-08 and what has to be true before the table can move into
# migrations/enable_rls.sql — because "add RLS" is NOT free: a policy on a
# table whose queries do not set app.current_tenant_id/app.current_workspace_id
# makes every read silently return zero rows.
_SCOPED_IN_APP_SQL = (
    "2026-08-08 audit: every read carries an explicit tenant_id/workspace_id "
    "filter in application SQL. No cross-tenant read path found. RLS here "
    "would be defence in depth; safe to add once its queries are confirmed to "
    "run through the scoped rls_* helpers. Remediation, not an incident."
)
_SCOPED_BY_UNGUESSABLE_KEY = (
    "2026-08-08 audit: reads are keyed on an id the caller could only have "
    "obtained legitimately (uuid / signed token / secret hash) rather than on "
    "tenant_id, and every traced caller re-checks ownership after the fetch. "
    "Defence-in-depth gap, not a live leak — no attacker-controllable key."
)
_NO_LIVE_READ = (
    "2026-08-08 audit: nothing reads this table on a live path (write-only, or "
    "the only reader has zero callers — the repo's 'built, tested, never "
    "wired' pattern). Near-zero exposure regardless of RLS; re-audit before "
    "wiring any reader up."
)
_NOT_POSTGRES = (
    "2026-08-08 audit: not a Postgres table on the live path — backed by a "
    "local SQLite file, or only reachable in the SQLite fallback branch. "
    "Postgres RLS is structurally inapplicable. Listed so the check does not "
    "flag it if a Postgres copy is ever bootstrapped by accident."
)
_NEEDS_SCHEMA_CHANGE_FIRST = (
    "2026-08-08 audit: carries only ONE of tenant_id/workspace_id, so it "
    "cannot use empyralis_rls_scope_match(tenant_id, workspace_id) unchanged. "
    "Needs a column added or a bespoke single-column policy BEFORE RLS is "
    "possible at all. Blocked on a schema decision, not on effort."
)
_UNSCOPED_READ_CONFIRMED = (
    "2026-08-08 audit: HAD a confirmed unscoped read reachable by any "
    "authenticated user; the HTTP boundary was closed the same day (see the "
    "block below). The rows are still read globally in-process by fleet "
    "placement, so RLS remains inapplicable here — run_state_repository uses a "
    "plain asyncpg pool that never sets the session GUCs, and a policy would "
    "blank the runtime's own reads. Excused so this check still catches NEW "
    "drift; the enforcement is application-level and lives at the route."
)
_NOT_YET_AUDITED = (
    "2026-08-08: carries a scope column and is outside enable_rls.sql, but was "
    "NOT reached in the audit that seeded this list. Excused solely so the "
    "check can start catching NEW drift today; this entry is an admission of "
    "unknown status, not a judgement that the table is safe. Audit before "
    "trusting it either way."
)

_RLS_COVERAGE_EXCEPTIONS: Dict[str, str] = {
    # ── audited: scoped in application SQL ────────────────────────────
    "usage_events": _SCOPED_IN_APP_SQL,
    "workspace_hosted_ai_monthly_cost_ledger": _SCOPED_IN_APP_SQL,
    "workspace_billing_accounts": _SCOPED_IN_APP_SQL,
    "workspace_billing_subscriptions": _SCOPED_IN_APP_SQL,
    "workspace_member_invites": _SCOPED_IN_APP_SQL,
    "credit_ledger_events": _SCOPED_IN_APP_SQL,
    "agent_traces": _SCOPED_IN_APP_SQL,
    "deployed_agent_conversation_memory": _SCOPED_IN_APP_SQL,
    "activity_ledger_events": _SCOPED_IN_APP_SQL,
    # ── audited: keyed on an unguessable id, ownership re-checked ─────
    # The mcp_oauth_* trio is the cleanest case in the registry: the ONLY
    # reads are `WHERE token_hash = $1` / `code_hash = $1` — keyed on
    # possession of the raw secret, with no list-by-client_id or
    # list-by-user_id path anywhere. An unscoped WHERE on a secret hash is
    # the correct shape, not a gap.
    "mcp_oauth_access_tokens": _SCOPED_BY_UNGUESSABLE_KEY,
    "mcp_oauth_refresh_tokens": _SCOPED_BY_UNGUESSABLE_KEY,
    "mcp_oauth_authorization_codes": _SCOPED_BY_UNGUESSABLE_KEY,
    "deployed_agents": _SCOPED_BY_UNGUESSABLE_KEY,
    "runtime_sessions": _SCOPED_BY_UNGUESSABLE_KEY,
    "user_devices": _SCOPED_BY_UNGUESSABLE_KEY,
    "user_provider_connections": _SCOPED_BY_UNGUESSABLE_KEY,
    # ── audited: no live read path ────────────────────────────────────
    "governance_holds": _NO_LIVE_READ,
    "external_user_privacy_requests": _NO_LIVE_READ,
    "external_user_privacy_delete_audits": _NO_LIVE_READ,
    "fleet_queue_partitions": _NO_LIVE_READ,
    "deployed_agent_upgrade_click_events": _NO_LIVE_READ,
    # ── audited: not a Postgres table on the live path ────────────────
    "gateway_registrations": _NOT_POSTGRES,
    # MAN-307: the Postgres copy of gateway_registrations is dead (last
    # written 2026-06-24; the live store is SQLite via
    # gateway_state_repository) and had already been used as primary evidence
    # in two investigations, giving a wrong answer both times. It is renamed
    # in the database so it cannot be mistaken for truth again.
    #
    # BOTH NAMES ARE LISTED ON PURPOSE. This exception map is keyed by table
    # NAME, so renaming a table that carries tenant_id/workspace_id makes it
    # a brand-new unknown scoped table to `_check_rls_coverage` — which fails
    # closed and REFUSES TO BOOT. That is the check behaving exactly as
    # designed, and it took production down for ~3 minutes on 2026-08-13 when
    # the rename was applied before this entry existed. Renaming a scoped
    # table is therefore a TWO-part change that must ship in this order:
    # add the new key here and deploy, THEN rename in the database. Keeping
    # the old key costs nothing and means the revert path also boots.
    "zzz_dead_gateway_registrations_see_man307": _NOT_POSTGRES,
    "gateway_sessions": _NOT_POSTGRES,
    "gateway_pairing_intents": _NOT_POSTGRES,
    "gateway_action_approvals": _NOT_POSTGRES,
    "gateway_browser_sessions": _NOT_POSTGRES,
    "sage_agent_computer_selections": _NOT_POSTGRES,
    "personal_channel_whatsapp_states": _NOT_POSTGRES,
    "personal_channel_telegram_states": _NOT_POSTGRES,
    "personal_channel_local_bridge_states": _NOT_POSTGRES,
    "workspace_registry": _NOT_POSTGRES,
    # ── audited: schema blocks the standard policy ────────────────────
    # vault_credentials is the highest-blast-radius entry in this whole
    # registry: no tenant_id column at all, NULLABLE workspace_id (platform-
    # scoped credentials legitimately have NULL), and vault_repository.py:101
    # list_all() is a full-table SELECT with no WHERE that every vault
    # operation goes through. The tenant boundary is vault_helpers.
    # workspace_visible() in Python, applied after the whole table is already
    # in memory. Held at every call site traced — but a naive policy here
    # would blank the platform-scoped rows, so this needs a schema decision.
    "vault_credentials": _NEEDS_SCHEMA_CHANGE_FIRST,
    "workspace_policies": _NEEDS_SCHEMA_CHANGE_FIRST,
    "tenant_policies": _NEEDS_SCHEMA_CHANGE_FIRST,
    "tenant_enterprise_settings": _NEEDS_SCHEMA_CHANGE_FIRST,
    # ── audited: read globally in-process, scoped at the HTTP boundary ─
    # These four fed GET /runtime/runtimes/status, /local/workers/status,
    # /runtime/runtimes/reliability and /health/internal, all gated only by
    # require_api_key — which resolves ANY authenticated user of ANY tenant,
    # not a system key. FIXED 2026-08-08: those routes now scope to the
    # caller's own workspaces and require has_platform_fleet_operator_access
    # for the global view. The tables are still read globally in-process by
    # fleet placement and lease recovery, which is correct — the boundary is
    # the route, not the query. list_fleet_workers no longer accepts a silent
    # unscoped read either: a missing tenant/workspace raises unless the caller
    # passes include_all_tenants=True.
    "fleet_worker_registrations": _UNSCOPED_READ_CONFIRMED,
    "live_runs": _UNSCOPED_READ_CONFIRMED,
    "run_archive": _UNSCOPED_READ_CONFIRMED,
    "local_queue_dead_letters": _UNSCOPED_READ_CONFIRMED,
    # ── legitimately global background worker ─────────────────────────
    # Drained by runs_core.run_outbox_delivery_forever with no tenant filter
    # (FOR UPDATE SKIP LOCKED), which is correct: outbox_service.
    # deliver_outbox_event re-scopes every delivery from the claimed row's own
    # tenant_id/workspace_id. RLS would break the drain loop for no gain.
    "runtime_outbox": (
        "2026-08-08 audit: cross-tenant by design — a single global drain loop "
        "claims due events and re-scopes each delivery from the claimed row's "
        "own tenant_id/workspace_id. A policy would blank the drain loop's "
        "reads and stop all delivery. Correctly global, not an oversight."
    ),
    # agent_computers is NOT in this registry on purpose. It already carries
    # ENABLE + FORCE ROW LEVEL SECURITY from its own migration
    # (migrations/add_agent_computers.sql), so it is genuinely protected and
    # the coverage check recognises it by reading the live policy state rather
    # than by name. Worth knowing anyway: agent_computers_repository.py runs
    # every query with bypass_rls=True (list_records_for_pairing_scan has to
    # scan cross-tenant to match an inbound beacon), so the policy is not the
    # enforcement layer there today — the app-level re-check in
    # routes_gateway.py:2393/2441 is.
    # ── NOT audited — status genuinely unknown ────────────────────────
    "agent_action_events": _NOT_YET_AUDITED,
    "channel_events": _NOT_YET_AUDITED,
    "channel_links": _NOT_YET_AUDITED,
    "channel_pairing_intents": _NOT_YET_AUDITED,
    "channel_user_acquisition_touches": _NOT_YET_AUDITED,
    "chat_stream_state": _NOT_YET_AUDITED,
    "credit_ledger_events": _NOT_YET_AUDITED,
    "deployed_agent_business_insights": _NOT_YET_AUDITED,
    "deployed_agent_conversation_memory": _NOT_YET_AUDITED,
    "deployed_agent_daily_message_usage": _NOT_YET_AUDITED,
    "deployed_agent_monthly_cost_ledger": _NOT_YET_AUDITED,
    "discord_workspace_pairings": _NOT_YET_AUDITED,
    "hosted_ai_reservations": _NOT_YET_AUDITED,
    "mcp_oauth_access_tokens": _NOT_YET_AUDITED,
    "mcp_oauth_authorization_codes": _NOT_YET_AUDITED,
    "mcp_oauth_refresh_tokens": _NOT_YET_AUDITED,
    "notification_devices": _NOT_YET_AUDITED,
    "notification_reads": _NOT_YET_AUDITED,
    "notifications": _NOT_YET_AUDITED,
    "run_history": _NOT_YET_AUDITED,
}


def _rls_coverage_check_skipped() -> bool:
    """Narrow escape hatch for the coverage half only.

    Deliberately separate from EMPYRALIS_SKIP_RLS_CHECK. The seeded exception
    list above was written from an audit of the live schema; if it turns out
    to be one table short on some box, the operator's only lever would
    otherwise be EMPYRALIS_SKIP_RLS_CHECK — which also switches off the
    *enforcement* verification that has been protecting 40 tables since
    MAN-109. Trading real isolation checking for a bookkeeping miss is a bad
    trade, so it gets its own switch. Logged at error level on every boot,
    like its sibling: never a silent choice.

    Before rolling this change onto a box, run
    ``DATABASE_URL=... python3 scripts/rls_state_report.py`` there first — it
    prints exactly what this check would say, without booting anything.
    """
    return os.getenv("EMPYRALIS_SKIP_RLS_COVERAGE_CHECK", "").strip().lower() in {
        "1", "true", "yes",
    }


async def _discover_tenant_scoped_tables(conn: Any) -> Dict[str, List[str]]:
    """Live-database inventory: ``{table_name: [scope columns it carries]}``.

    Ordinary and partitioned tables in ``public`` only — views cannot carry a
    policy of their own, so they are not the boundary and would only produce
    noise. Individual partitions are deliberately NOT excluded: Postgres
    applies the *partition's* policies when one is queried directly, so a
    partition of a covered parent is still its own hole. There are none in
    this schema today; if someone adds partitioning, a loud prompt to think
    about it is the correct outcome rather than a silent exemption.
    """
    rows = await conn.fetch(
        """
        SELECT c.relname AS table_name,
               array_agg(a.attname ORDER BY a.attname) AS scope_columns,
               bool_or(c.relrowsecurity) AS rls_enabled,
               bool_or(c.relforcerowsecurity) AS rls_forced,
               COALESCE(MAX(p.policy_count), 0) AS policy_count
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid
        LEFT JOIN (
            SELECT schemaname, tablename, COUNT(*) AS policy_count
            FROM pg_policies GROUP BY schemaname, tablename
        ) p ON p.schemaname = n.nspname AND p.tablename = c.relname
        WHERE n.nspname = 'public'
          AND c.relkind IN ('r', 'p')
          AND a.attnum > 0
          AND NOT a.attisdropped
          AND a.attname IN ('tenant_id', 'workspace_id')
        GROUP BY c.relname
        """
    )
    return {
        row["table_name"]: {
            "scope_columns": list(row["scope_columns"]),
            "protected": bool(
                row["rls_enabled"] and row["rls_forced"] and int(row["policy_count"] or 0) > 0
            ),
        }
        for row in rows
    }


def _rls_coverage_problems(
    discovered: Dict[str, Dict[str, Any]],
    expected: List[str],
    exceptions: Optional[Dict[str, str]] = None,
) -> List[str]:
    """Tenant-scoped tables with no policy at all and no recorded exception.

    ``discovered`` comes from the live database, ``expected`` from
    ``enable_rls.sql``. The question asked is "is this table protected?", NOT
    "is it named in that one file" — `agent_computers` carries its own
    ``FORCE ROW LEVEL SECURITY`` in migrations/add_agent_computers.sql and is
    genuinely protected, so flagging it would be a false alarm, and false
    alarms are how a boot check gets switched off.

    (``enable_rls.sql`` is still the right home for a new policy: CLAUDE.md's
    deploy note says it must be re-run after adding any table, which is what
    keeps _check_rls's enforcement half re-asserting it. A policy that lives
    only in a one-shot migration is protected but not re-verified.)
    """
    if exceptions is None:
        exceptions = _RLS_COVERAGE_EXCEPTIONS
    covered = set(expected)
    excused = set(exceptions)
    problems: List[str] = []
    for table in sorted(discovered):
        info = discovered[table]
        if table in covered or table in excused or info.get("protected"):
            continue
        columns = ", ".join(info.get("scope_columns") or [])
        problems.append(f"{table} (carries {columns}): no RLS policy and no recorded exception")
    return problems


async def _fetch_rls_state(conn: Any, tables: List[str]) -> Dict[str, Dict[str, Any]]:
    """Return per-table {rls_enabled, rls_forced, policy_count} from the live DB."""
    rows = await conn.fetch(
        """
        SELECT c.relname AS table_name,
               c.relrowsecurity AS rls_enabled,
               c.relforcerowsecurity AS rls_forced,
               COALESCE(p.policy_count, 0) AS policy_count
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN (
            SELECT schemaname, tablename, COUNT(*) AS policy_count
            FROM pg_policies GROUP BY schemaname, tablename
        ) p ON p.schemaname = n.nspname AND p.tablename = c.relname
        WHERE n.nspname = 'public' AND c.relname = ANY($1)
        """,
        tables,
    )
    return {row["table_name"]: dict(row) for row in rows}


def _rls_problems(expected: List[str], state: Dict[str, Dict[str, Any]]) -> List[str]:
    problems: List[str] = []
    for table in expected:
        row = state.get(table)
        if row is None:
            problems.append(f"{table}: table not found")
        elif not row.get("rls_enabled"):
            problems.append(f"{table}: RLS not enabled")
        elif not row.get("rls_forced"):
            problems.append(f"{table}: RLS not FORCEd (table owner bypasses the policy)")
        elif int(row.get("policy_count") or 0) == 0:
            problems.append(f"{table}: no RLS policy")
    return problems


async def _check_rls() -> Optional[str]:
    """Return ``None`` if every tenant-scoped table has RLS + FORCE + a policy.

    Skipped when Postgres is not configured (local SQLite dev has no RLS
    concept). Gated whenever DATABASE_URL is set — real tenant data lives in
    Postgres and must be isolated.
    """
    from server_modules.db import durable_runtime_required as _durable_required  # noqa: PLC0415

    if _rls_check_skipped():
        LOGGER.error(
            "preflight: RLS verification BYPASSED (EMPYRALIS_SKIP_RLS_CHECK set) "
            "— tenant isolation is NOT verified; cross-tenant reads may be possible."
        )
        return None

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        if _durable_required():
            return (
                "DATABASE_URL is not set but durable runtime is required — "
                "tenant-isolation RLS cannot be verified. Set DATABASE_URL."
            )
        return None  # local SQLite dev — no RLS to verify

    try:
        import asyncpg  # noqa: PLC0415
    except ImportError:
        if _durable_required():
            return "asyncpg is not installed but Postgres is required — cannot verify RLS."
        return None

    expected = _tenant_scoped_tables_from_migration()
    if not expected:
        return (
            "Could not read migrations/enable_rls.sql to determine tenant-scoped "
            "tables — RLS enforcement cannot be verified. Ensure the migration file exists."
        )

    conn = None
    try:
        conn = await asyncpg.connect(database_url, timeout=10)
    except Exception as exc:
        if _durable_required():
            return f"Postgres unreachable at {_redacted_dsn(database_url)}, cannot verify RLS: {exc}"
        LOGGER.warning("preflight: Postgres unreachable (%s) — skipping RLS check.", exc)
        return None

    try:
        state = await _fetch_rls_state(conn, expected)
        problems = _rls_problems(expected, state)
        if _rls_coverage_check_skipped():
            LOGGER.error(
                "preflight: RLS COVERAGE verification BYPASSED "
                "(EMPYRALIS_SKIP_RLS_COVERAGE_CHECK set) — a tenant-scoped "
                "table outside migrations/enable_rls.sql would not be reported."
            )
            coverage_problems = []
        else:
            discovered = await _discover_tenant_scoped_tables(conn)
            coverage_problems = _rls_coverage_problems(discovered, expected)
    finally:
        await conn.close()

    if problems:
        return (
            "Tenant-isolation RLS is not fully applied — the platform would run "
            "with cross-tenant reads possible. Failing boot rather than serving "
            f"traffic without isolation.\n  {len(problems)}/{len(expected)} table(s) affected:\n"
            + "\n".join(f"    - {p}" for p in problems)
            + "\n  Apply: psql <DATABASE_URL> -f migrations/enable_rls.sql"
        )
    if coverage_problems:
        return (
            "Tenant-scoped table(s) exist with NO RLS policy and no recorded "
            "exception. The tables listed in migrations/enable_rls.sql are all "
            "enforced, but these carry tenant_id/workspace_id and are outside "
            "it entirely — so nothing at the database level keeps one tenant's "
            "rows away from another's.\n"
            f"  {len(coverage_problems)} table(s):\n"
            + "\n".join(f"    - {p}" for p in coverage_problems)
            + "\n  Fix one of two ways:\n"
            "    1. Add the table to migrations/enable_rls.sql and re-run it "
            "(preferred) — but FIRST confirm every query against it is scoped, "
            "or its reads will silently return zero rows.\n"
            "    2. If the table is legitimately global/operational, record it "
            "in preflight._RLS_COVERAGE_EXCEPTIONS with the reason."
        )
    return None


async def report_rls_state() -> Dict[str, Any]:
    """Structured per-table RLS state for the standalone report tool.

    Returns ``{"configured": bool, "reason": str|None, "tables": [...],
    "ok": bool}``. Does not raise.
    """
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        return {
            "configured": False,
            "reason": "DATABASE_URL not set (local SQLite dev has no RLS). "
                      "Run this where the production DATABASE_URL is set (the VPS).",
            "tables": [],
            "ok": None,
        }
    try:
        import asyncpg  # noqa: PLC0415
    except ImportError:
        return {"configured": False, "reason": "asyncpg not installed", "tables": [], "ok": None}

    expected = _tenant_scoped_tables_from_migration()
    conn = await asyncpg.connect(database_url, timeout=10)
    try:
        state = await _fetch_rls_state(conn, expected)
        discovered = await _discover_tenant_scoped_tables(conn)
    finally:
        await conn.close()

    tables = []
    for table in expected:
        row = state.get(table) or {}
        tables.append({
            "table": table,
            "present": table in state,
            "rls_enabled": bool(row.get("rls_enabled")),
            "rls_forced": bool(row.get("rls_forced")),
            "policy_count": int(row.get("policy_count") or 0),
        })
    problems = _rls_problems(expected, state)
    coverage_problems = _rls_coverage_problems(discovered, expected)
    # The known-and-excused backlog is reported separately from the unknown
    # ones: an operator needs to see the outstanding remediation list, not
    # just "no new drift".
    excused = sorted(set(discovered) & set(_RLS_COVERAGE_EXCEPTIONS))
    return {
        "configured": True,
        "reason": None,
        "tables": tables,
        "ok": not problems and not coverage_problems,
        "problems": problems,
        "coverage_problems": coverage_problems,
        "uncovered_but_excused": [
            {
                "table": table,
                "scope_columns": discovered[table].get("scope_columns") or [],
                "protected": bool(discovered[table].get("protected")),
                "reason": _RLS_COVERAGE_EXCEPTIONS[table],
            }
            for table in excused
        ],
    }


# ── Redis ─────────────────────────────────────────────────────────────

def _platform_credit_check_skipped() -> bool:
    return str(os.getenv("EMPYRALIS_SKIP_PLATFORM_CREDIT_CHECK") or "").strip().lower() in {"1", "true", "yes"}


async def _check_platform_credit_keys() -> None:
    """Best-effort health check of platform-hosted provider keys (e.g. the
    DeepSeek key behind "Empyralis credits") — never blocks startup, only
    warns loudly. A dead or empty-balance key here silently breaks the
    first turn of every agent running on platform credits (the failure
    surfaces downstream as a generic error, not as "the platform's key is
    out of money"), so a loud startup warning is the only chance to catch
    it before a customer does. Logged, never appended to the errors list
    run_preflight_checks() returns — this is advisory, not a boot blocker.
    """
    if _platform_credit_check_skipped():
        LOGGER.info("preflight: platform-credit key check skipped (EMPYRALIS_SKIP_PLATFORM_CREDIT_CHECK set).")
        return

    try:
        from server_modules import secrets_broker
        from server_modules.runtime_common import http_json_request
    except Exception as exc:
        LOGGER.warning("preflight: could not import dependencies for platform-credit check: %s", exc)
        return

    try:
        resolution = secrets_broker.resolve_hosted_provider_secret(
            tenant_id=None,
            workspace_id=None,
            provider_id="deepseek",
            field="api_key",
            tool_name="preflight",
            purpose="platform_credit_health_check",
        )
        api_key = str(resolution.value or "").strip()
    except Exception as exc:
        LOGGER.warning("preflight: could not resolve DeepSeek platform-credit key: %s", exc)
        return

    if not api_key:
        LOGGER.info("preflight: no DeepSeek platform-credit key configured — skipping balance check.")
        return

    try:
        result = await asyncio.to_thread(
            http_json_request,
            "https://api.deepseek.com/user/balance",
            method="GET",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
    except Exception as exc:
        LOGGER.warning("preflight: DeepSeek balance check errored (network/transport): %s", exc)
        return

    status = int(result.get("status") or 0)
    body = result.get("json") if isinstance(result.get("json"), dict) else {}
    if status == 200 and bool(body.get("is_available")):
        LOGGER.info("preflight: DeepSeek platform-credit key is healthy (balance available).")
        return

    LOGGER.critical(
        "PLATFORM-CREDIT KEY DEAD — DeepSeek 'Empyralis credits' is broken. "
        "Every agent running on platform-managed credits will fail its first "
        "turn until this is fixed (status=%s, response=%s). This is an ops "
        "action — top up or rotate the account behind DEEPSEEK_API_KEY "
        "(or ORION_HOSTED_DEEPSEEK_API_KEY); no code change fixes an empty "
        "upstream balance. Set EMPYRALIS_SKIP_PLATFORM_CREDIT_CHECK=true to "
        "silence this check.",
        status,
        body,
    )


def _platform_digitalocean_check_skipped() -> bool:
    return str(os.getenv("EMPYRALIS_SKIP_PLATFORM_DIGITALOCEAN_CHECK") or "").strip().lower() in {"1", "true", "yes"}


async def _check_platform_digitalocean_token() -> None:
    """MAN-131: best-effort health check of the platform-owned DigitalOcean
    token (``vps_provisioning_service._platform_digitalocean_token``) —
    never blocks startup, only warns loudly. A dead, expired, or revoked
    token here silently breaks the direct baked-image provisioning path
    (MAN-133): platform-account droplet creation either falls back to the
    slower customer-account path or fails outright, and nothing else in the
    product says "the platform's own DigitalOcean token is the reason".
    Advisory only, same reasoning and same shape as
    ``_check_platform_credit_keys`` above — a DigitalOcean outage or an
    expired token is a DigitalOcean-account problem, not a reason this
    process should refuse to boot.
    """
    if _platform_digitalocean_check_skipped():
        LOGGER.info(
            "preflight: platform DigitalOcean token check skipped "
            "(EMPYRALIS_SKIP_PLATFORM_DIGITALOCEAN_CHECK set)."
        )
        return

    try:
        from server_modules import secrets_broker
        from server_modules.runtime_common import http_json_request
    except Exception as exc:
        LOGGER.warning(
            "preflight: could not import dependencies for platform DigitalOcean token check: %s", exc
        )
        return

    try:
        resolution = secrets_broker.resolve_hosted_provider_secret(
            tenant_id=None,
            workspace_id=None,
            provider_id="digitalocean",
            field="api_key",
            tool_name="preflight",
            purpose="platform_digitalocean_token_health_check",
        )
        token = str(resolution.value or "").strip()
    except Exception as exc:
        LOGGER.warning("preflight: could not resolve the platform DigitalOcean token: %s", exc)
        return

    if not token:
        LOGGER.info(
            "preflight: no platform DigitalOcean token configured — skipping account check "
            "(platform-account provisioning stays off; customer-account provisioning is unaffected)."
        )
        return

    try:
        result = await asyncio.to_thread(
            http_json_request,
            "https://api.digitalocean.com/v2/account",
            method="GET",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
    except Exception as exc:
        LOGGER.warning("preflight: DigitalOcean account check errored (network/transport): %s", exc)
        return

    status = int(result.get("status") or 0)
    body = result.get("json") if isinstance(result.get("json"), dict) else {}
    account = body.get("account") if isinstance(body.get("account"), dict) else {}
    if status == 200 and account:
        LOGGER.info("preflight: platform DigitalOcean token is healthy (account reachable).")
        return

    LOGGER.critical(
        "PLATFORM DIGITALOCEAN TOKEN DEAD — the platform-owned DigitalOcean "
        "account is unreachable or the token was rejected (status=%s, "
        "response=%s). Platform-account droplet provisioning (MAN-133's "
        "direct baked-image path) will fall back to the slower "
        "customer-account installer path, or fail outright, until this is "
        "fixed. Customer-account provisioning (a customer's own pasted "
        "token) is unaffected. This is an ops action — rotate or restore "
        "the token behind EMPYRALIS_PLATFORM_DIGITALOCEAN_TOKEN; no code "
        "change fixes a revoked or expired upstream token. Set "
        "EMPYRALIS_SKIP_PLATFORM_DIGITALOCEAN_CHECK=true to silence this "
        "check.",
        status,
        body,
    )


def _platform_google_check_skipped() -> bool:
    return str(os.getenv("EMPYRALIS_SKIP_PLATFORM_GOOGLE_CHECK") or "").strip().lower() in {"1", "true", "yes"}


async def _check_platform_google_operator_credentials() -> None:
    """2026-08-13 launch-readiness audit: Google Cloud VPS provisioning had
    NO preflight check at all, unlike DigitalOcean's MAN-131 check above —
    so a missing or dead operator credential was invisible at boot, and the
    first sign of trouble would be a customer's own "Connect Google Cloud"
    attempt failing. Same shape and same reasoning as
    ``_check_platform_digitalocean_token``: never blocks startup, only
    warns loudly. Advisory only — a dead credential here degrades one
    provider (Google Cloud), not the whole platform.

    FOUR env vars gate this, not one, and all four must resolve for the
    live check to even run:
      GOOGLE_CLOUD_CLIENT_ID / GOOGLE_CLOUD_CLIENT_SECRET — a SECOND OAuth
        app, deliberately separate from the sign-in one (see
        docs/DEPLOY-RUNBOOK.md: the sensitive ``cloud-platform`` scope must
        never reach the sign-in consent screen, or Google Sign-In goes back
        under verification and the removed 100-user cap returns).
      GOOGLE_CLOUD_OPERATOR_CLIENT_EMAIL / _OPERATOR_REFRESH_TOKEN —
        Empyralis's OWN long-lived GCP identity, impersonated for every
        Compute Engine call this product makes on a customer's behalf.
        Minting the refresh token requires a human to run a one-time OAuth
        consent AS Empyralis itself; it cannot be regenerated by any
        automation here, which is exactly why its silent absence or expiry
        needs a loud boot-time signal rather than a customer finding out
        mid-connect.

    Read the same way vps_provisioning_service's own Google functions
    already read them — bare ``os.getenv`` on the same four constants,
    never the secrets broker. Unlike the DigitalOcean platform token
    (MAN-131), these are not currently broker-routed, and this check does
    not invent that registration as a side effect of a preflight pass.

    The live call refreshes the OPERATOR identity's token directly via
    ``runtime_common.http_json_request`` (never
    ``vps_provisioning_service._google_operator_access_token``, which
    raises on any failure and would collapse "network blip" and "the
    refresh token is dead" into the same exception — this needs the same
    status-code-based classification the DigitalOcean check above already
    gets from that helper). It does not mint a per-customer impersonated
    token: there is no "the" customer at boot time, and the operator
    refresh alone already proves all four credentials are alive together
    (the refresh grant is authenticated with client_id/secret and carries
    the operator's own refresh_token).
    """
    if _platform_google_check_skipped():
        LOGGER.info(
            "preflight: platform Google Cloud operator credential check skipped "
            "(EMPYRALIS_SKIP_PLATFORM_GOOGLE_CHECK set)."
        )
        return

    try:
        from server_modules import vps_provisioning_service
        from server_modules.runtime_common import http_json_request
    except Exception as exc:
        LOGGER.warning(
            "preflight: could not import dependencies for the platform Google Cloud check: %s", exc
        )
        return

    # The single derivation (also read by the /hardware/vps/provider-
    # availability route the frontend's provider picker uses) rather than a
    # second inline computation of the same four names — see
    # google_cloud_operator_missing_env_vars's own docstring.
    missing = vps_provisioning_service.google_cloud_operator_missing_env_vars()
    if missing:
        LOGGER.info(
            "preflight: Google Cloud VPS provisioning is not configured (missing %s) — skipping "
            "operator credential check. Google Cloud stays off the provider list until all four "
            "are set.",
            ", ".join(missing),
        )
        return

    client_id = (os.getenv(vps_provisioning_service.GOOGLE_CLOUD_CLIENT_ID_ENV) or "").strip()
    client_secret = (os.getenv(vps_provisioning_service.GOOGLE_CLOUD_CLIENT_SECRET_ENV) or "").strip()
    operator_refresh_token = (
        os.getenv(vps_provisioning_service.GOOGLE_CLOUD_OPERATOR_REFRESH_TOKEN_ENV) or ""
    ).strip()

    try:
        result = await asyncio.to_thread(
            http_json_request,
            vps_provisioning_service.GOOGLE_OAUTH_TOKEN_URL,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            payload={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": operator_refresh_token,
            },
            timeout=10,
        )
    except Exception as exc:
        LOGGER.warning(
            "preflight: Google Cloud operator token refresh errored (network/transport): %s", exc
        )
        return

    status = int(result.get("status") or 0)
    body = result.get("json") if isinstance(result.get("json"), dict) else {}
    access_token = str(body.get("access_token") or "").strip()
    if status == 200 and access_token:
        LOGGER.info(
            "preflight: platform Google Cloud operator credentials are healthy "
            "(operator token refresh succeeded)."
        )
        return

    LOGGER.critical(
        "PLATFORM GOOGLE CLOUD OPERATOR CREDENTIALS DEAD — Empyralis's own Google "
        "Cloud operator identity could not refresh a token (status=%s, response=%s). "
        "Google Cloud VPS provisioning (the OAuth connect flow, project bootstrap, "
        "and every impersonated Compute Engine call for an already-connected "
        "customer) will fail until this is fixed. This is an ops action — "
        "GOOGLE_CLOUD_OPERATOR_REFRESH_TOKEN can only be reminted by a human "
        "running a one-time OAuth consent as Empyralis itself (see "
        "docs/DEPLOY-RUNBOOK.md); no code change fixes a revoked or expired "
        "upstream token. Set EMPYRALIS_SKIP_PLATFORM_GOOGLE_CHECK=true to silence "
        "this check.",
        status,
        body,
    )


async def _check_redis() -> Optional[str]:
    """Return ``None`` if Redis is reachable, or an error string."""
    if _redis_check_skipped():
        LOGGER.info("preflight: Redis check skipped (EMPYRALIS_SKIP_REDIS_CHECK set).")
        return None

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379").strip()
    try:
        import redis.asyncio as redis_asyncio  # noqa: PLC0415
    except ImportError:
        return (
            "redis-py is not installed — cannot check Redis.\n"
            "  Install: pip install redis\n"
            "  Or set EMPYRALIS_SKIP_REDIS_CHECK=true to bypass."
        )

    try:
        client = redis_asyncio.from_url(redis_url)
        await client.ping()
        await client.aclose()
    except Exception as exc:
        return (
            f"Redis unreachable at {redis_url}: {exc}\n"
            f"  Verify Redis is running, or set EMPYRALIS_SKIP_REDIS_CHECK=true."
        )

    return None


# ── runner ────────────────────────────────────────────────────────────

async def run_preflight_checks() -> List[str]:
    """Run all startup preflight checks.

    Returns a list of error strings (empty = all passed).
    Does **not** raise — the caller decides whether to fail or warn.
    """
    errors: List[str] = []

    # 1. Local-stack DATABASE_URL must be explicit (MAN-202 / MAN-268) —
    #    checked first since it's a precondition for the Postgres check below,
    #    not a reachability problem.
    local_stack_db_err = _check_local_stack_database_url()
    if local_stack_db_err:
        errors.append(local_stack_db_err)

    # 1b. Config for the removed embeddings/RAG knowledge pipeline must be
    #     gone, not silently ignored (CLAUDE.md: stale config fails loudly).
    removed_rag_err = _check_removed_knowledge_rag_config()
    if removed_rag_err:
        errors.append(removed_rag_err)

    # 2. Kernel
    kernel_err = _check_kernel()
    if kernel_err:
        errors.append(kernel_err)

    # 3. PostgreSQL
    pg_err = await _check_postgres()
    if pg_err:
        errors.append(pg_err)

    # 4. Row-Level Security (tenant isolation) — only meaningful once Postgres
    #    is reachable, so skip if the Postgres check already failed.
    if not pg_err:
        rls_err = await _check_rls()
        if rls_err:
            errors.append(rls_err)

    # 5. Redis
    redis_err = await _check_redis()
    if redis_err:
        errors.append(redis_err)

    # 6. Platform-credit provider keys (e.g. DeepSeek) — advisory only.
    #    Never appended to errors: a dead upstream balance degrades one
    #    feature (agents on platform credits), not the whole platform.
    await _check_platform_credit_keys()

    # 7. Platform-owned DigitalOcean token (MAN-131) — advisory only, same
    #    reasoning as step 6: a dead/expired token degrades one feature
    #    (platform-account droplet provisioning), not the whole platform.
    await _check_platform_digitalocean_token()

    # 8. Platform Google Cloud operator credentials — advisory only, same
    #    reasoning as step 7. Closes the exact gap the 2026-08-13
    #    launch-readiness audit found: Google Cloud VPS provisioning had no
    #    preflight check at all before this, so a missing/dead operator
    #    credential was invisible until a customer's own connect attempt.
    await _check_platform_google_operator_credentials()

    if errors:
        LOGGER.error("PREFLIGHT FAILED — %d check(s) did not pass.", len(errors))
        for i, err in enumerate(errors, 1):
            LOGGER.error("  [%d] %s", i, err.replace("\n", "\n      "))
    else:
        LOGGER.info("preflight: all checks passed.")

    return errors


def fail_on_preflight_errors(errors: List[str]) -> None:
    """Raise :class:`PreflightError` if *errors* is non-empty."""
    if not errors:
        return
    header = f"{len(errors)} preflight check(s) failed:"
    body = "\n\n".join(f"  [{i}] {e}" for i, e in enumerate(errors, 1))
    raise PreflightError(f"{header}\n\n{body}")


# Convenience entry point for callers that want a single async check + raise.
async def preflight_or_raise() -> None:
    """Run all preflight checks and raise on any failure."""
    fail_on_preflight_errors(await run_preflight_checks())
