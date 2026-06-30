import queue
import threading
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any, Dict, List, Optional, Set

from server_modules.acp_manager import DEFAULT_ACP_MANAGER
from server_modules.runtime_config import *


@asynccontextmanager
async def app_lifespan(_: Any):
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(empyralist_mcp_lifespan())
        # Start Telegram background polling for local dev
        try:
            from server_modules import sage_telegram_hosted_service as _hosted
            import os as _os
            _base_url = _os.getenv("EMPYRALIS_BASE_URL", "http://127.0.0.1:8001")
            _is_local = any(h in _base_url for h in ('127.0.0.1', 'localhost', '0.0.0.0', '::1'))
            _deploy_env = _os.getenv("EMPYRALIS_DEPLOY_ENV", _os.getenv("NODE_ENV", "")).lower()
            _is_prod = _deploy_env in ('production', 'prod', 'staging')
            if (_is_local or not _is_prod) and _hosted.is_configured():
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
