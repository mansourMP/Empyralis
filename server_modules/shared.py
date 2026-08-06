import asyncio
import os
import queue
import threading
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any, Dict, List, Optional, Set

from server_modules.acp_manager import DEFAULT_ACP_MANAGER
from server_modules.runtime_config import *


# MAN-140 defense-in-depth: bound gateway_sessions/gateway_events growth.
# See gateway_state_repository.prune_gateway_state()'s own docstring for why
# this exists (neither table is covered by MAN-80's retention job, and
# sweep_stale_gateway_sessions -- already wired up elsewhere -- only UPDATEs
# status, never DELETEs). Tunable without a code change, same convention as
# the rest of this module's env-overridable constants.
_GATEWAY_STATE_PRUNE_INTERVAL_SECONDS = int(
    os.environ.get("EMPYRALIS_GATEWAY_STATE_PRUNE_INTERVAL_SECONDS", "") or 6 * 60 * 60
)
_GATEWAY_STATE_PRUNE_EVENT_RETENTION_DAYS = int(
    os.environ.get("EMPYRALIS_GATEWAY_EVENT_RETENTION_DAYS", "") or 14
)
_GATEWAY_STATE_PRUNE_SESSION_RETENTION_DAYS = int(
    os.environ.get("EMPYRALIS_GATEWAY_SESSION_RETENTION_DAYS", "") or 30
)


async def _gateway_state_prune_loop() -> None:
    import logging as _logging

    from server_modules import gateway_state_repository

    _log = _logging.getLogger("server_modules.gateway_state_repository")
    while True:
        try:
            # prune_gateway_state() is synchronous stdlib sqlite3 I/O (see
            # its own module for why every gateway_state_repository call is
            # -- deliberately, today -- blocking rather than executor-
            # offloaded). Run it via to_thread here specifically because
            # this loop shares the SAME event loop as every live gateway
            # websocket connection: a multi-hundred-thousand-row DELETE
            # blocking that loop directly would itself be exactly the kind
            # of heartbeat-response-latency hit MAN-140's root cause turned
            # on (see gateway_protocol_service.py's heartbeat handler for
            # that investigation) -- the fix for one blocking-I/O-on-the-
            # shared-loop problem should not introduce a second one.
            result = await asyncio.to_thread(
                gateway_state_repository.prune_gateway_state,
                event_retention_days=_GATEWAY_STATE_PRUNE_EVENT_RETENTION_DAYS,
                session_retention_days=_GATEWAY_STATE_PRUNE_SESSION_RETENTION_DAYS,
            )
            if result.get("gateway_events_deleted") or result.get("gateway_sessions_deleted"):
                _log.info("Gateway state prune: %s", result)
        except asyncio.CancelledError:
            raise
        except Exception:
            _log.exception("Gateway state prune failed")
        await asyncio.sleep(max(60, _GATEWAY_STATE_PRUNE_INTERVAL_SECONDS))


@asynccontextmanager
async def app_lifespan(_: Any):
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(empyralist_mcp_lifespan())
        # MAN-140 defense-in-depth: periodic gateway_sessions/gateway_events
        # prune. Guarded the same way as the Telegram polling block below --
        # a failure here must never block startup -- and cancelled cleanly
        # on shutdown via the exit stack rather than left as a dangling
        # fire-and-forget task.
        try:
            _gateway_prune_task = asyncio.create_task(_gateway_state_prune_loop())

            async def _cancel_gateway_prune_task() -> None:
                _gateway_prune_task.cancel()
                try:
                    await _gateway_prune_task
                except (asyncio.CancelledError, Exception):
                    pass

            stack.push_async_callback(_cancel_gateway_prune_task)
        except Exception as _prune_startup_exc:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "Gateway state prune loop startup failed: %s", _prune_startup_exc
            )
        # Telemetry retention: periodic prune of activity_ledger_events,
        # agent_secret_access_events, agent_sessions, agent_traces/
        # agent_trace_events, runtime_outbox, and runtime_sessions -- the
        # Postgres control-plane analog of the gateway-state prune above.
        # See server_modules/telemetry_retention_service.py for the full
        # rationale (why these tables, why not agent_turns, why not the
        # existing MAN-80 retention job). Same startup/shutdown guarding
        # convention as the gateway prune block immediately above.
        try:
            from server_modules import telemetry_retention_service

            _telemetry_retention_task = asyncio.create_task(telemetry_retention_service.telemetry_retention_loop())

            async def _cancel_telemetry_retention_task() -> None:
                _telemetry_retention_task.cancel()
                try:
                    await _telemetry_retention_task
                except (asyncio.CancelledError, Exception):
                    pass

            stack.push_async_callback(_cancel_telemetry_retention_task)
        except Exception as _telemetry_retention_startup_exc:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "Telemetry retention loop startup failed: %s", _telemetry_retention_startup_exc
            )
        # Start Telegram background polling for local dev
        try:
            from server_modules import sage_telegram_hosted_service as _hosted
            import os as _os
            _base_url = _os.getenv("EMPYRALIS_BASE_URL", "http://127.0.0.1:8001")
            _is_local_url = any(h in _base_url for h in ('127.0.0.1', 'localhost', '0.0.0.0', '::1'))
            # Same fallback chain as auth._resolved_environment(). An allowlist of
            # known dev-like values (not a blocklist of known-prod ones) so any
            # unrecognized deploy env — e.g. "self-hosted", used by the main VPS —
            # defaults to "not dev". This path deletes the production webhook
            # before polling starts, so misclassifying prod as dev is destructive.
            _deploy_env = (
                _os.getenv("EMPYRALIS_DEPLOY_ENV")
                or _os.getenv("ORION_ENV")
                or _os.getenv("ENV")
                or _os.getenv("NODE_ENV")
                or ""
            ).strip().lower()
            _is_dev_env = _deploy_env in ("", "dev", "development", "local", "test")
            if (_is_local_url or _is_dev_env) and _hosted.is_configured():
                import logging as _logging
                _log = _logging.getLogger("server_modules.sage_telegram_hosted_service")
                _log.info("Sage Telegram hosted: local dev detected, starting background polling")
                # Delete any stale webhook so getUpdates works
                try:
                    await _hosted.unregister_webhook()
                except Exception:
                    pass
                _hosted.start_background_polling()
        except Exception as _startup_exc:
            import logging as _logging
            _logging.getLogger(__name__).warning("Telegram background polling startup failed: %s", _startup_exc)
        yield

# Compatibility alias assigned by server.py. shared.py does not own app construction.
app: Optional[Any] = None
ACP_MANAGER = DEFAULT_ACP_MANAGER


def sync_acp_manager_paths(
    *,
    runtime_db_path=None,
    setup_sessions_path=None,
    provider_profiles_path=None,
    idempotency_path=None,
) -> None:
    ACP_MANAGER.reconfigure_paths(
        runtime_db_path=runtime_db_path,
        setup_sessions_path=setup_sessions_path,
        provider_profiles_path=provider_profiles_path,
        idempotency_path=idempotency_path,
    )

# shared.runs is a process-local execution cache only. Never use for queries, recovery, or inspection. All durable state is in Postgres via run_state_repository.
# Inventory of shared mutable state and its current durable backing.
# This makes the migration surface explicit and keeps active queue/session state
# aligned with the persistence layer instead of treating these containers as
# authoritative beyond the current process.
SHARED_STATE_BACKING: Dict[str, str] = {
    "runs": "Postgres live_runs",
    "RUN_HISTORY": "Postgres run_archive",
    "CHANNEL_EVENTS": "runtime_state_store.channel_events",
    "SETUP_SESSIONS": "setup_sessions.json",
    "PROVIDER_PROFILES": "provider_profiles.json",
    "IDEMPOTENCY_RECORDS": "idempotency.json",
    "LOCAL_PENDING_RUN_IDS": "runtime_state_store.local_pending_queue (local-only cache)",
    "LOCAL_CLAIMED_RUNS": "runtime_state_store.local_claims (local-only cache)",
    "LOCAL_WORKER_REGISTRY": "runtime_state_store.runtime_registrations (local-only cache)",
}

NON_DURABLE_SHARED_STATE: Dict[str, str] = {
    "RUN_QUEUE_INDEX": "ephemeral queue-object index rebuilt from live runs on startup",
    "CHANNEL_EVENTS": "runtime_events mirror loaded from durable channel event store",
    "WEEKLY_SCHEDULES": "automation mirror loaded from schedules file",
    "RUNTIME_SKILLS_STATE": "skills mirror loaded from runtime skills file",
    "RATE_LIMIT_BUCKETS": "process-local throttle buckets",
    "RUNTIME_METRICS": "process-local metrics aggregates",
    "TELEGRAM_AUTOPILOT_STATE": "process-local autopilot state with durable checkpoints elsewhere",
    "WHATSAPP_AUTOPILOT_STATE": "process-local autopilot state with durable checkpoints elsewhere",
}

# Global state
runs = ACP_MANAGER.runs
RUN_QUEUE_INDEX: Dict[int, str] = {}
RUN_HISTORY_LOCK = threading.Lock()
RUN_HISTORY = ACP_MANAGER.run_history
APPROVAL_AUDIT_LOCK = threading.Lock()  # Phase 3 stub: approval system removed
APPROVAL_AUDIT: List[Dict[str, Any]] = []  # Phase 3 stub: approval system removed
CHANNEL_EVENTS_LOCK = threading.Lock()
CHANNEL_EVENTS: List[Dict[str, Any]] = []
SCHEDULES_LOCK = threading.Lock()
WEEKLY_SCHEDULES: Dict[str, Dict[str, Any]] = {}
SETUP_SESSIONS_LOCK = threading.Lock()
SETUP_SESSIONS = ACP_MANAGER.setup_sessions
PROFILES_LOCK = threading.Lock()
PROVIDER_PROFILES = ACP_MANAGER.provider_profiles
RUNTIME_SKILLS_LOCK = threading.Lock()
RUNTIME_SKILLS_STATE: Dict[str, Any] = {
    "version": 1,
    "custom_skills": [],
    "bindings": {"assistant_defaults": [], "automation_defaults": []},
    "updated_at": None,
}
IDEMPOTENCY_LOCK = threading.Lock()
IDEMPOTENCY_RECORDS = ACP_MANAGER.idempotency_records
RATE_LIMIT_LOCK = threading.Lock()
RATE_LIMIT_BUCKETS: Dict[str, List[float]] = {}
LOCAL_QUEUE_LOCK = threading.Lock()
LOCAL_PENDING_RUN_IDS = ACP_MANAGER.local_pending_run_ids
LOCAL_CLAIMED_RUNS = ACP_MANAGER.local_claimed_runs
LOCAL_WORKER_REGISTRY = ACP_MANAGER.local_worker_registry
TERMINAL_RUN_STATUSES: Set[str] = {
    "completed",
    "failed",
    "timeout",
    "waiting_for_input",
    "stopped",
    "cancelled",
}
MEMORY_BUCKETS: Set[str] = {"profile", "project", "session"}
METRICS_LOCK = threading.Lock()
RUNTIME_METRICS: Dict[str, float] = {
    "runs_started": 0,
    "runs_completed": 0,
    "runs_failed": 0,
    "runs_timeout": 0,
    "runs_waiting_for_input": 0,
    "run_duration_sum_ms": 0,
    "run_duration_count": 0,
    "first_value_sum_ms": 0,
    "first_value_count": 0,
    "hitl_wait_sum_ms": 0,
    "hitl_wait_count": 0,
    "chat_stream_interrupted": 0,
    "chat_stream_restarted_unfinished": 0,
    "chat_stream_replayed_completed": 0,
    "chat_stream_replayed_interrupted": 0,
}
TELEGRAM_AUTOPILOT_LOCK = threading.Lock()
TELEGRAM_AUTOPILOT_STATE: Dict[str, Any] = {
    "enabled": ORION_TELEGRAM_AUTOPILOT_ENABLED,
    "active": False,
    "started_at": None,
    "last_poll_at": None,
    "last_error": None,
    "last_error_at": None,
    "last_error_category": None,
    "last_error_source": None,
    "error_count": 0,
    "consecutive_errors": 0,
    "retry_count": 0,
    "last_retry_at": None,
    "backoff_seconds": 0.0,
    "next_retry_at": None,
    "last_success_at": None,
    "connectors_seen": 0,
    "processed_updates": 0,
    "runs_started": 0,
    "connectors": {},
}
TELEGRAM_AUTOPILOT_THREAD: Optional[threading.Thread] = None
WHATSAPP_AUTOPILOT_LOCK = threading.Lock()
WHATSAPP_AUTOPILOT_STATE: Dict[str, Any] = {
    "enabled": ORION_WHATSAPP_AUTOPILOT_ENABLED,
    "active": False,
    "started_at": None,
    "last_inbound_at": None,
    "last_error": None,
    "last_error_at": None,
    "last_error_category": None,
    "last_error_source": None,
    "error_count": 0,
    "consecutive_errors": 0,
    "connectors_seen": 0,
    "processed_messages": 0,
    "runs_started": 0,
    "connectors": {},
}
ORION_ENGINE_VALIDATION_ERRORS: List[str] = []
