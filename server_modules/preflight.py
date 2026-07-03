"""Startup preflight checks — verify the platform can boot before serving.

Implements Phase T: every required dependency is checked at startup.
A failure names the gap and raises :class:`PreflightError` so the
process never boots half-alive.

Checks
------
1. **Rust runtime kernel** — binary must exist (built or env-var path).
2. **PostgreSQL** — DATABASE_URL must be set, pool must be reachable,
   and ``workspace_agent_installs`` must have the stage_4b columns.
3. **Redis** — REDIS_URL (default ``redis://localhost:6379``) must PONG.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import List, Optional

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

    # 3. Redis
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
