from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

try:
    import asyncpg
except Exception:  # pragma: no cover - optional dependency at import time
    asyncpg = None

from server_modules.state_paths import runtime_state_db_path


LOGGER = logging.getLogger(__name__)

_POOLS_BY_LOOP: dict[int, tuple[Any, Any, str]] = {}
_POOL_INIT_LOCKS_BY_LOOP: dict[int, Any] = {}
_POOL_INIT_FAILED = False
_MISSING_DSN_LOGGED = False
_DURABLE_ENV_TOKENS = {"beta", "preview", "stage", "staging", "prod", "production"}


class DurableRuntimeConfigurationError(RuntimeError):
    """Raised when durable runtime state is required but Postgres is unavailable."""


def _resolved_environment() -> str:
    return str(os.getenv("EMPYRALIS_DEPLOY_ENV") or os.getenv("ORION_ENV") or os.getenv("ENV") or os.getenv("NODE_ENV") or "").strip().lower()


def _truthy_env(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def durable_runtime_required() -> bool:
    if _truthy_env(os.getenv("ORION_REQUIRE_DURABLE_RUN_STATE")):
        return True
    if _truthy_env(os.getenv("ORION_MOBILE_BETA_ENABLED")):
        return True
    return _resolved_environment() in _DURABLE_ENV_TOKENS


def durability_mode() -> str:
    return "required" if durable_runtime_required() else "best_effort_local_dev"


def sqlite_fallback_allowed() -> bool:
    return not durable_runtime_required()


def _required_pool_status(reason: str) -> str:
    normalized = str(reason or "").strip().lower()
    if normalized in {"missing_dsn", "missing_database_url"}:
        return "required_unconfigured"
    if normalized in {"driver_missing", "missing_driver"}:
        return "required_driver_missing"
    return "required_unreachable"


def _pool_unavailable_message(*, operation: str, reason: str) -> str:
    normalized_reason = str(reason or "").strip().lower() or "unavailable"
    if durable_runtime_required():
        if normalized_reason == "missing_dsn":
            detail = "DATABASE_URL is not configured"
        elif normalized_reason == "driver_missing":
            detail = "asyncpg is not installed"
        else:
            detail = "Postgres is unreachable"
        return (
            f"Postgres is required for durable run state during {operation}; "
            f"{detail}; refusing to continue with memory/SQLite fallback"
        )
    if normalized_reason == "missing_dsn":
        return "DATABASE_URL not set or Postgres unavailable — local runtime remains memory/SQLite only"
    if normalized_reason == "driver_missing":
        return "asyncpg unavailable — local runtime remains memory/SQLite only"
    return "Postgres unavailable — local runtime remains memory/SQLite only"


def configured_database_url() -> str:
    return str(os.getenv("DATABASE_URL") or "").strip()


def configured_postgres_pool_max_size() -> int:
    raw = str(os.getenv("ORION_POSTGRES_POOL_MAX_SIZE") or "").strip()
    if not raw:
        return 50
    try:
        return max(50, int(raw))
    except Exception:
        return 50


def sqlite_health_status() -> str:
    if durable_runtime_required():
        return "fallback_blocked"
    try:
        path = runtime_state_db_path().resolve()
        return "active" if path.exists() else "inactive"
    except Exception:
        return "inactive"


async def postgres_health_status() -> str:
    database_url = configured_database_url()
    if not database_url:
        return _required_pool_status("missing_dsn") if durable_runtime_required() else "unavailable"
    if asyncpg is None:
        return _required_pool_status("driver_missing") if durable_runtime_required() else "unavailable"
    pool = await get_pool()
    if pool is not None:
        return "connected"
    return _required_pool_status("unreachable") if durable_runtime_required() else "unavailable"


async def require_durable_pool(*, operation: str) -> Any:
    pool = await get_pool()
    if pool is not None:
        return pool
    if not configured_database_url():
        raise DurableRuntimeConfigurationError(_pool_unavailable_message(operation=operation, reason="missing_dsn"))
    if asyncpg is None:
        raise DurableRuntimeConfigurationError(_pool_unavailable_message(operation=operation, reason="driver_missing"))
    raise DurableRuntimeConfigurationError(_pool_unavailable_message(operation=operation, reason="unreachable"))


async def get_pool() -> Any:
    global _POOL_INIT_FAILED, _MISSING_DSN_LOGGED

    database_url = configured_database_url()
    if not database_url:
        if not _MISSING_DSN_LOGGED:
            LOGGER.error(_pool_unavailable_message(operation="runtime startup", reason="missing_dsn")) if durable_runtime_required() else LOGGER.warning(
                _pool_unavailable_message(operation="runtime startup", reason="missing_dsn")
            )
            _MISSING_DSN_LOGGED = True
        return None

    if asyncpg is None:
        if not _POOL_INIT_FAILED:
            LOGGER.error(_pool_unavailable_message(operation="runtime startup", reason="driver_missing")) if durable_runtime_required() else LOGGER.warning(
                _pool_unavailable_message(operation="runtime startup", reason="driver_missing")
            )
            _POOL_INIT_FAILED = True
        return None

    current_loop = asyncio.get_running_loop()
    current_loop_key = id(current_loop)

    stale_loop_keys = [
        loop_key
        for loop_key, (loop_obj, _pool, _dsn) in list(_POOLS_BY_LOOP.items())
        if bool(getattr(loop_obj, "is_closed", lambda: False)())
    ]
    for loop_key in stale_loop_keys:
        _loop_obj, stale_pool, _dsn = _POOLS_BY_LOOP.pop(loop_key)
        _POOL_INIT_LOCKS_BY_LOOP.pop(loop_key, None)
        try:
            stale_pool.terminate()
        except Exception:
            pass

    existing = _POOLS_BY_LOOP.get(current_loop_key)
    if existing is not None:
        loop_obj, pool, dsn = existing
        if loop_obj is current_loop and dsn == database_url:
            return pool
        try:
            await pool.close()
        except Exception:
            try:
                pool.terminate()
            except Exception:
                pass
        _POOLS_BY_LOOP.pop(current_loop_key, None)

    lock = _POOL_INIT_LOCKS_BY_LOOP.get(current_loop_key)
    if lock is None:
        lock = asyncio.Lock()
        _POOL_INIT_LOCKS_BY_LOOP[current_loop_key] = lock

    async with lock:
        existing = _POOLS_BY_LOOP.get(current_loop_key)
        if existing is not None:
            loop_obj, pool, dsn = existing
            if loop_obj is current_loop and dsn == database_url:
                return pool
            try:
                await pool.close()
            except Exception:
                try:
                    pool.terminate()
                except Exception:
                    pass
            _POOLS_BY_LOOP.pop(current_loop_key, None)

        try:
            pool = await asyncpg.create_pool(
                dsn=database_url,
                min_size=1,
                max_size=configured_postgres_pool_max_size(),
                command_timeout=10.0,
            )
            _POOLS_BY_LOOP[current_loop_key] = (current_loop, pool, database_url)
            _POOL_INIT_FAILED = False
            LOGGER.info("Postgres pool initialized — run state will be durable")
            return pool
        except Exception as exc:  # pragma: no cover - network/db dependent
            if not _POOL_INIT_FAILED:
                LOGGER.error(_pool_unavailable_message(operation="runtime startup", reason="unreachable")) if durable_runtime_required() else LOGGER.warning(
                    _pool_unavailable_message(operation="runtime startup", reason="unreachable")
                )
                LOGGER.debug("Postgres pool initialization failure detail: %s", exc)
            _POOLS_BY_LOOP.pop(current_loop_key, None)
            _POOL_INIT_FAILED = True
            return None
