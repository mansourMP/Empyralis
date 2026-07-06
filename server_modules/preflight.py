"""Startup preflight checks — verify the platform can boot before serving.

Implements Phase T: every required dependency is checked at startup.
A failure names the gap and raises :class:`PreflightError` so the
process never boots half-alive.

Checks
------
1. **Rust runtime kernel** — binary must exist (built or env-var path).
2. **PostgreSQL** — DATABASE_URL must be set, pool must be reachable,
   and ``workspace_agent_installs`` must have the stage_4b columns.
3. **Row-Level Security** — every tenant-scoped table (parsed from
   ``migrations/enable_rls.sql``) must have RLS enabled + FORCEd + a policy,
   or boot fails. Prevents serving traffic with tenant isolation silently off.
4. **Redis** — REDIS_URL (default ``redis://localhost:6379``) must PONG.
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


# ── kernel ───────────────────────────────────────────────────────────

def _check_kernel() -> Optional[str]:
    """Return ``None`` if the kernel binary is found, or an error string."""
    try:
        from server_modules.rust_runtime_kernel_client import runtime_kernel_binary, KERNEL_ENV_VAR  # noqa: PLC0415
    except ImportError:
        return None  # client module not available — skip check
    binary = runtime_kernel_binary()
    if binary is not None:
        return None
    repo_root = os.environ.get("EMPYRALIS_REPO_ROOT") or os.getcwd()
    return (
        f"Rust runtime kernel binary not found.\n"
        f"  Build: cd {repo_root} && cargo build --manifest-path empyralis-runtime-kernel/Cargo.toml\n"
        f"  Or set {KERNEL_ENV_VAR} to the binary path."
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
    return {
        "configured": True,
        "reason": None,
        "tables": tables,
        "ok": not problems,
        "problems": problems,
    }


# ── Redis ─────────────────────────────────────────────────────────────

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

    # 1. Kernel
    kernel_err = _check_kernel()
    if kernel_err:
        errors.append(kernel_err)

    # 2. PostgreSQL
    pg_err = await _check_postgres()
    if pg_err:
        errors.append(pg_err)

    # 3. Row-Level Security (tenant isolation) — only meaningful once Postgres
    #    is reachable, so skip if the Postgres check already failed.
    if not pg_err:
        rls_err = await _check_rls()
        if rls_err:
            errors.append(rls_err)

    # 4. Redis
    redis_err = await _check_redis()
    if redis_err:
        errors.append(redis_err)

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
