from __future__ import annotations

"""Scheduled retention sweep for machine-generated telemetry tables.

Context: a production audit (single DigitalOcean VPS, 2GB RAM, Postgres
database ~147MB) found activity_ledger_events, agent_secret_access_events,
agent_sessions, and runtime_sessions together making up roughly 60% of the
database, growing with MACHINE activity (tool calls, heartbeats, sessions)
rather than human activity -- unlike agent_turns (actual conversation
content), which is negligible (~1.9KB/turn) and explicitly out of scope for
this job. None of the tables this module prunes had ANY age-based delete
path before it: agent_sessions rows were only ever status-flipped to
'terminated', never removed; activity_ledger_events, agent_secret_access_
events, agent_traces/agent_trace_events, runtime_outbox, and runtime_sessions
had no delete path at all beyond a handful of narrow single-row/single-
workspace call sites (session_service.terminate_session,
control_plane_repository.delete_workspace_scope_data for an explicit GDPR
purge, etc.) -- none of which bound unattended growth over time.

Two existing retention mechanisms already live in this codebase and were
deliberately NOT reused here, for reasons worth stating up front:

  - retention_enforcement_job.py (MAN-80) is a per-WORKSPACE system, invoked
    on demand (never actually wired to a schedule -- "retention job built
    but never called" is literally its own commit's framing), and even when
    invoked only performs a real delete for one store (sage_memory); every
    other entry in data_retention_service.DATA_STORE_CATALOG, including
    activity_ledger_events, gets an eligible_count of 0 because nothing
    populates it. It answers "what would a customer's GDPR export/delete
    request need to cover", not "what unbounded machine telemetry needs a
    global age sweep" -- a different question with a different shape
    (per-workspace vs. global-cross-tenant, on-demand vs. scheduled).

  - gateway_state_repository.prune_gateway_state() (MAN-140) is the
    established pattern THIS module follows -- scheduled via an asyncio
    background loop from shared.py's app_lifespan, age-based, DELETE-based,
    bypasses the Rust control-plane kernel gate for the same "no matching
    operation in the kernel's allowlist, extending it is out of scope for
    internal housekeeping" reason cited throughout this module -- but it
    only covers two SQLite tables (gateway_sessions/gateway_events) that
    live in gateway-local state, not the Postgres control-plane tables this
    module covers.

Every env var below follows the same on/off vocabulary and fresh-read-per-
call convention as EMPYRALIS_PRIMARY_COMPACTION_ENABLED /
EMPYRALIS_FORCE_LEGACY_ENGINE (server_modules/sage_agent_runtime_service.py)
-- read from os.environ on every call rather than cached at import time, so
toggling it in production takes effect on the next scheduled tick without a
restart.
"""

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional


LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_ACTIVITY_LEDGER_RETENTION_DAYS = 30
DEFAULT_AGENT_TRACE_RETENTION_DAYS = 14
DEFAULT_RUNTIME_OUTBOX_RETENTION_DAYS = 7
DEFAULT_AGENT_SESSION_RETENTION_DAYS = 30
# Deliberately much longer than the others -- agent_secret_access_events is
# a security audit trail, not debug telemetry. See control_plane_repository.
# prune_agent_secret_access_events's own docstring for the full reasoning on
# why "bounded but long" beats both "never delete" (unbounded audit table on
# a 2GB-RAM box is itself an operational risk) and "same window as
# everything else" (would silently shorten audit retention whenever the
# general telemetry window is tuned down).
DEFAULT_SECRET_ACCESS_AUDIT_RETENTION_DAYS = 180
DEFAULT_BATCH_SIZE = 500
# Bounds total rows touched per table per scheduled run to
# batch_size * max_batches (default 500 * 50 = 25,000). A backlog larger
# than that drains across multiple runs rather than in one; see
# run_telemetry_retention_sweep's docstring.
DEFAULT_MAX_BATCHES_PER_TABLE = 50
DEFAULT_INTERVAL_SECONDS = 6 * 60 * 60  # matches EMPYRALIS_GATEWAY_STATE_PRUNE_INTERVAL_SECONDS's default


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def telemetry_retention_enabled() -> bool:
    """EMPYRALIS_TELEMETRY_RETENTION_ENABLED (default "1"). Master switch --
    set to "0"/"false"/"off" to disable the entire sweep without a code
    change, same convention as EMPYRALIS_PRIMARY_COMPACTION_ENABLED."""
    return _truthy(os.environ.get("EMPYRALIS_TELEMETRY_RETENTION_ENABLED", "1"))


def _env_positive_int(name: str, default: int) -> int:
    raw = str(os.environ.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        LOGGER.warning("%s=%r is not a valid integer; using default %d", name, raw, default)
        return default
    if value <= 0:
        LOGGER.warning("%s=%r must be positive; using default %d", name, raw, default)
        return default
    return value


def activity_ledger_retention_days() -> int:
    return _env_positive_int("EMPYRALIS_ACTIVITY_LEDGER_RETENTION_DAYS", DEFAULT_ACTIVITY_LEDGER_RETENTION_DAYS)


def agent_trace_retention_days() -> int:
    return _env_positive_int("EMPYRALIS_AGENT_TRACE_RETENTION_DAYS", DEFAULT_AGENT_TRACE_RETENTION_DAYS)


def runtime_outbox_retention_days() -> int:
    return _env_positive_int("EMPYRALIS_RUNTIME_OUTBOX_RETENTION_DAYS", DEFAULT_RUNTIME_OUTBOX_RETENTION_DAYS)


def agent_session_retention_days() -> int:
    return _env_positive_int("EMPYRALIS_AGENT_SESSION_RETENTION_DAYS", DEFAULT_AGENT_SESSION_RETENTION_DAYS)


def secret_access_audit_retention_days() -> int:
    return _env_positive_int(
        "EMPYRALIS_SECRET_ACCESS_AUDIT_RETENTION_DAYS", DEFAULT_SECRET_ACCESS_AUDIT_RETENTION_DAYS
    )


def retention_batch_size() -> int:
    return _env_positive_int("EMPYRALIS_TELEMETRY_RETENTION_BATCH_SIZE", DEFAULT_BATCH_SIZE)


def retention_max_batches_per_table() -> int:
    return _env_positive_int(
        "EMPYRALIS_TELEMETRY_RETENTION_MAX_BATCHES_PER_TABLE", DEFAULT_MAX_BATCHES_PER_TABLE
    )


def retention_interval_seconds() -> int:
    return _env_positive_int("EMPYRALIS_TELEMETRY_RETENTION_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS)


def _cutoff(retention_days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=retention_days)


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------


@dataclass
class TelemetryRetentionResult:
    started_at: str
    completed_at: str = ""
    enabled: bool = True
    deleted: Dict[str, int] = field(default_factory=dict)
    retention_days: Dict[str, int] = field(default_factory=dict)
    errors: Dict[str, str] = field(default_factory=dict)

    @property
    def total_deleted(self) -> int:
        return sum(self.deleted.values())


async def run_telemetry_retention_sweep() -> TelemetryRetentionResult:
    """Runs one retention pass across every table this job owns. Never
    raises -- each table's prune is wrapped individually so one failing
    table (a lock timeout, a transient connection error) never blocks the
    others, matching the "never let one failure take down a long-running
    loop" posture _gateway_state_prune_loop and its own prune function
    already establish. Safe to call repeatedly / concurrently with itself
    (each call is a bounded, idempotent batch of DELETEs; there is no
    persistent cursor or state between calls -- the next call simply picks
    up whatever is still older than its own freshly-computed cutoff).

    Explicitly out of scope, by the task this job was built for and by
    every prune function's own docstring: agent_turns (conversation
    content), the agent_conversation_memory JSONL store, tasks, projects,
    sage/workspace memory, credit_ledger_events, usage_events, and any other
    billing-related table. This function only ever imports and calls the
    five prune_* functions named below -- it has no path to touch anything
    else."""
    result = TelemetryRetentionResult(started_at=datetime.now(timezone.utc).isoformat())

    if not telemetry_retention_enabled():
        result.enabled = False
        result.completed_at = datetime.now(timezone.utc).isoformat()
        LOGGER.info("Telemetry retention sweep skipped: EMPYRALIS_TELEMETRY_RETENTION_ENABLED is off.")
        return result

    batch_size = retention_batch_size()
    max_batches = retention_max_batches_per_table()

    # (table label, retention-days getter, prune coroutine factory)
    from server_modules import control_plane_repository
    from server_modules import run_state_repository
    from server_modules import session_service

    jobs = (
        ("activity_ledger_events", activity_ledger_retention_days, control_plane_repository.prune_activity_ledger_events),
        ("agent_secret_access_events", secret_access_audit_retention_days, control_plane_repository.prune_agent_secret_access_events),
        ("agent_sessions", agent_session_retention_days, control_plane_repository.prune_expired_agent_sessions),
        ("agent_traces", agent_trace_retention_days, control_plane_repository.prune_finished_agent_traces),
        ("runtime_outbox", runtime_outbox_retention_days, run_state_repository.prune_delivered_outbox_events),
        ("runtime_sessions", agent_session_retention_days, session_service.prune_expired_runtime_sessions),
    )

    for table_label, retention_days_fn, prune_fn in jobs:
        retention_days = retention_days_fn()
        result.retention_days[table_label] = retention_days
        cutoff = _cutoff(retention_days)
        try:
            deleted = await prune_fn(cutoff=cutoff, batch_size=batch_size, max_batches=max_batches)
            result.deleted[table_label] = int(deleted or 0)
        except Exception as exc:
            result.errors[table_label] = str(exc)
            LOGGER.exception("Telemetry retention: pruning %s failed", table_label)

    result.completed_at = datetime.now(timezone.utc).isoformat()

    if result.total_deleted or result.errors:
        LOGGER.info(
            "Telemetry retention sweep: deleted=%s retention_days=%s errors=%s",
            result.deleted,
            result.retention_days,
            result.errors or None,
        )
    else:
        LOGGER.debug(
            "Telemetry retention sweep: nothing eligible (retention_days=%s)",
            result.retention_days,
        )

    return result


# ---------------------------------------------------------------------------
# Scheduled loop -- same shape as shared._gateway_state_prune_loop
# ---------------------------------------------------------------------------


async def telemetry_retention_loop() -> None:
    """Periodic background task: run_telemetry_retention_sweep() every
    retention_interval_seconds(), forever, until cancelled. Wired up from
    shared.py's app_lifespan the same way _gateway_state_prune_loop is --
    see that function's own comment for why a failed sweep must never kill
    the loop, and why the interval is re-read from the env on every tick
    (EMPYRALIS_TELEMETRY_RETENTION_ENABLED can be flipped off in production
    without a restart)."""
    import asyncio

    while True:
        try:
            await run_telemetry_retention_sweep()
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Telemetry retention sweep failed")
        await asyncio.sleep(max(60, retention_interval_seconds()))
