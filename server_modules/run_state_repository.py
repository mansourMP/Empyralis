from __future__ import annotations

import asyncio
import concurrent.futures
from datetime import datetime, timezone
import json
import logging
import threading
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional, TypeVar

from server_modules import db as runtime_db
from server_modules import rust_runtime_kernel_client


LOGGER = logging.getLogger(__name__)
_T = TypeVar("_T")
_SYNC_DISPATCH_LOCK = threading.Lock()
_SYNC_DISPATCH_READY = threading.Event()
_SYNC_DISPATCH_THREAD: Optional[threading.Thread] = None
_SYNC_DISPATCH_LOOP: Optional[asyncio.AbstractEventLoop] = None
_SYNC_DISPATCH_THREAD_ID: Optional[int] = None

_RUNTIME_STATE_STORE_NEXT_ACTIONS = {
    "upsert_live_run": {"write_live_run_state"},
    "delete_live_run": {"delete_live_run_state"},
    "archive_run": {"write_run_archive"},
    "create_or_update_approval_request": {"write_run_approval_request"},
    "resolve_approval_if_pending": {"resolve_run_approval"},
    "record_approval_resolution": {"record_run_approval_resolution"},
    "upsert_runtime_registration": {"write_runtime_registration"},
    "upsert_fleet_queue_partition": {"write_fleet_queue_partition"},
}


class RunStateRepositoryError(RuntimeError):
    """Base exception for durable run-state failures."""


class RunStatePersistenceError(RunStateRepositoryError):
    """Raised when a critical run-state persistence operation cannot complete."""


class RunStateVersionConflictError(RunStatePersistenceError):
    """Raised when a stale durable snapshot attempts to overwrite a newer row."""


class RunClaimConflictError(RunStatePersistenceError):
    """Raised when a run is already claimed by another live worker."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def dispatch_repository_call(awaitable: Awaitable[Any], *, operation: str) -> None:
    try:
        future = _submit_awaitable(awaitable, operation=operation)
    except Exception as exc:
        _close_awaitable_quietly(awaitable)
        LOGGER.warning("Repository async dispatch failed during %s: %s", operation, exc)
        return

    def _report_completion(done: concurrent.futures.Future[Any]) -> None:
        try:
            done.result()
        except Exception as exc:  # pragma: no cover - background logging path
            from server_modules import durability_signal  # noqa: PLC0415

            durability_signal.capture_durability_failure(
                f"repository async operation {operation}",
                exc,
                event_class="repository_write_dead_letter",
            )

    future.add_done_callback(_report_completion)


def _close_awaitable_quietly(awaitable: Awaitable[Any]) -> None:
    close_fn = getattr(awaitable, "close", None)
    if callable(close_fn):
        try:
            close_fn()
        except Exception:
            return


def _run_sync_dispatch_loop() -> None:
    global _SYNC_DISPATCH_LOOP, _SYNC_DISPATCH_THREAD_ID

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _SYNC_DISPATCH_LOOP = loop
    _SYNC_DISPATCH_THREAD_ID = threading.get_ident()
    _SYNC_DISPATCH_READY.set()
    try:
        loop.run_forever()
    finally:  # pragma: no cover - shutdown path
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()


def _ensure_sync_dispatch_loop() -> asyncio.AbstractEventLoop:
    global _SYNC_DISPATCH_THREAD, _SYNC_DISPATCH_LOOP, _SYNC_DISPATCH_THREAD_ID

    with _SYNC_DISPATCH_LOCK:
        if (
            _SYNC_DISPATCH_LOOP is not None
            and not _SYNC_DISPATCH_LOOP.is_closed()
            and _SYNC_DISPATCH_THREAD is not None
            and _SYNC_DISPATCH_THREAD.is_alive()
        ):
            return _SYNC_DISPATCH_LOOP
        _SYNC_DISPATCH_READY.clear()
        _SYNC_DISPATCH_LOOP = None
        _SYNC_DISPATCH_THREAD_ID = None
        _SYNC_DISPATCH_THREAD = threading.Thread(
            target=_run_sync_dispatch_loop,
            name="run-state-sync-dispatch",
            daemon=True,
        )
        _SYNC_DISPATCH_THREAD.start()

    if not _SYNC_DISPATCH_READY.wait(timeout=5.0):
        raise RuntimeError("Repository sync dispatch loop failed to start.")
    if _SYNC_DISPATCH_LOOP is None:
        raise RuntimeError("Repository sync dispatch loop is unavailable.")
    return _SYNC_DISPATCH_LOOP


def _submit_awaitable(
    awaitable: Awaitable[_T],
    *,
    operation: str,
) -> concurrent.futures.Future[_T]:
    loop = _ensure_sync_dispatch_loop()
    try:
        return asyncio.run_coroutine_threadsafe(awaitable, loop)
    except Exception:
        _close_awaitable_quietly(awaitable)
        raise


def _run_sync(
    awaitable_factory: Callable[[], Awaitable[_T]],
    *,
    operation: str,
    fallback: _T,
    raise_on_error: bool = False,
) -> _T:
    try:
        future = _submit_awaitable(awaitable_factory(), operation=operation)
        return future.result()
    except Exception as exc:
        if raise_on_error:
            raise
        LOGGER.warning("Repository sync dispatch failed during %s: %s", operation, exc)
        return fallback


async def _require_pool(*, operation: str) -> Any:
    try:
        return await runtime_db.require_durable_pool(operation=operation)
    except runtime_db.DurableRuntimeConfigurationError as exc:
        raise RunStatePersistenceError(str(exc)) from exc


async def _read_pool(*, operation: str) -> Any:
    if runtime_db.durable_runtime_required():
        return await _require_pool(operation=operation)
    return await runtime_db.get_pool()


def _sync_raise_on_read_failure() -> bool:
    return runtime_db.durable_runtime_required()


def _enforce_runtime_state_store_decision(
    *,
    operation: str,
    run_id: Optional[str] = None,
    runtime_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    status: Optional[str] = None,
    previous_status: Optional[str] = None,
    trace_id: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    expected_version: Optional[int] = None,
    current_version: Optional[int] = None,
    state_class: Optional[str] = None,
    force: bool = False,
) -> Dict[str, Any]:
    payload_text = _json_payload(payload or {})
    if state_class is None:
        if operation == "archive_run":
            state_class = "run_archive"
        elif operation == "upsert_runtime_registration":
            state_class = "runtime_registrations"
        elif operation in (
            "create_or_update_approval_request",
            "resolve_approval_if_pending",
            "record_approval_resolution",
        ):
            # Must match the Rust kernel's own default_state_class()
            # (empyralis-runtime-kernel/src/runtime_state_store.rs) exactly --
            # that function is authoritative. Falling through to "live_runs"
            # here made Python and Rust disagree about what these three
            # approval operations are.
            state_class = "run_approvals"
        else:
            state_class = "live_runs"
    request = {
        "operation": operation,
        "state_class": state_class,
        "storage_engine": "durable_postgres",
        "workspace_id": str(workspace_id or "").strip() or "default",
        "tenant_id": str(tenant_id or "").strip() or "default",
        "run_id": str(run_id or "").strip(),
        "runtime_id": str(runtime_id or "").strip(),
        "status": str(status or "").strip() or "unknown",
        "previous_status": str(previous_status or "").strip() or "unknown",
        "trace_id": str(trace_id or "").strip(),
        "payload": bool(payload is not None),
        "payload_bytes": len(payload_text.encode("utf-8")),
        "expected_version": expected_version,
        "current_version": current_version,
        "durable_runtime_required": True,
        "durable_pool_available": True,
        "workspace_access": True,
        "owner_access": True,
        "force": bool(force),
    }
    decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
        "runtime-state-store-decision",
        request,
    )
    expected_actions = _RUNTIME_STATE_STORE_NEXT_ACTIONS.get(operation)
    next_action = str(decision.get("next_action") or "").strip()
    if expected_actions is None:
        raise RuntimeError(f"unexpected runtime_state_store operation: {operation}")
    if next_action not in expected_actions:
        expected_list = ", ".join(sorted(expected_actions))
        raise RuntimeError(
            f"unexpected next_action for runtime-state-store-decision: {next_action or '<missing>'} "
            f"(expected {expected_list})"
        )
    return decision


def _enforce_run_record_decision(
    *,
    operation: str,
    run_id: str,
    workspace_id: str,
    tenant_id: str,
    status: Optional[str] = None,
    final_state: Optional[str] = None,
    from_state: Optional[str] = None,
    to_state: Optional[str] = None,
    version: Optional[int] = None,
) -> Dict[str, Any]:
    request: Dict[str, Any] = {
        "operation": operation,
        "run_id": str(run_id or "").strip(),
        "workspace_id": str(workspace_id or "").strip() or "default",
        "tenant_id": str(tenant_id or "").strip() or "default",
    }
    if status is not None:
        request["status"] = str(status or "").strip()
    if final_state is not None:
        request["final_state"] = str(final_state or "").strip()
    if from_state is not None:
        request["from_state"] = str(from_state or "").strip()
    if to_state is not None:
        request["to_state"] = str(to_state or "").strip()
    if version is not None:
        request["version"] = int(version)
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "run-record-decision",
            request,
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        reason = str(getattr(exc, "reason", "") or "run_record_denied").strip()
        raise RunStatePersistenceError(f"Rust run-record kernel blocked {operation}: {reason}") from exc
    expected_actions = _RUN_RECORD_NEXT_ACTIONS.get(operation)
    next_action = str(decision.get("next_action") or "").strip()
    if expected_actions is None:
        raise RuntimeError(f"unexpected run_record operation: {operation}")
    if next_action not in expected_actions:
        expected_list = ", ".join(sorted(expected_actions))
        raise RuntimeError(
            f"unexpected next_action for run-record-decision: {next_action or '<missing>'} "
            f"(expected {expected_list})"
        )
    return decision


_RUN_RECORD_NEXT_ACTIONS = {
    "register_live_run": {"create_live_run_initial"},
    "persist_snapshot": {"update_live_run_if_version_matches"},
    "record_transition": {"record_transition", "noop"},
    "archive_payload": {"archive_run"},
    "emit_transition_outbox": {"emit_run_transition_event", "noop"},
    "emit_artifact_outbox": {"emit_artifact_created_events", "noop"},
    "activate_live_run": {
        "enqueue_local_companion_run",
        "hydrate_local_memory_context",
        "start_background_run",
    },
}


def _apply_run_record_patch(payload: Dict[str, Any], decision: Dict[str, Any]) -> Dict[str, Any]:
    record_patch = decision.get("record_patch") if isinstance(decision, dict) else None
    if not isinstance(record_patch, dict):
        return payload
    for key, value in record_patch.items():
        payload[key] = value
    return payload


_OUTBOX_DELIVERY_NEXT_ACTIONS = {
    "persist_event": {"persist_outbox_event"},
    "list_undelivered": {"list_undelivered_outbox_events"},
    "claim_due": {"claim_due_outbox_events"},
    "patch_payload": {"patch_outbox_event_payload"},
    "mark_delivered": {"mark_outbox_event_delivered"},
    "record_failure": {"record_outbox_delivery_failure"},
    "list_poisoned": {"list_poisoned_outbox_events"},
    "delivery_status": {"get_outbox_delivery_status"},
}


def _enforce_outbox_delivery_decision(
    *,
    operation: str,
    event_id: Optional[str] = None,
    event_type: Optional[str] = None,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    run_id: Optional[str] = None,
    machine_id: Optional[str] = None,
    claim_token: Optional[str] = None,
    claimed_by: Optional[str] = None,
    limit: Optional[int] = None,
    claim_ttl_seconds: Optional[int] = None,
    retry_count: Optional[int] = None,
    retry_delay_seconds: Optional[int] = None,
    poison: bool = False,
    error_text: Optional[str] = None,
    payload_patch_present: bool = False,
) -> Dict[str, Any]:
    request = {
        "operation": operation,
        "event_id": str(event_id or "").strip(),
        "event_type": str(event_type or "").strip(),
        "tenant_id": str(tenant_id or "").strip(),
        "workspace_id": str(workspace_id or "").strip(),
        "run_id": str(run_id or "").strip(),
        "machine_id": str(machine_id or "").strip(),
        "claim_token": str(claim_token or "").strip(),
        "claimed_by": str(claimed_by or "").strip(),
        "limit": int(limit or 200),
        "claim_ttl_seconds": int(claim_ttl_seconds or 30),
        "retry_count": int(retry_count or 0),
        "max_retries": 5,
        "retry_delay_seconds": retry_delay_seconds,
        "poison": bool(poison),
        "error_text": str(error_text or "").strip(),
        "payload_patch_present": bool(payload_patch_present),
        "repository_read_only": False,
    }
    decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
        "outbox-delivery-decision",
        request,
    )
    expected_actions = _OUTBOX_DELIVERY_NEXT_ACTIONS.get(operation)
    next_action = str(decision.get("next_action") or "").strip()
    if expected_actions is None:
        raise RuntimeError(f"unexpected outbox_delivery operation: {operation}")
    if next_action not in expected_actions:
        expected_list = ", ".join(sorted(expected_actions))
        raise RuntimeError(
            f"unexpected next_action for outbox-delivery-decision: {next_action or '<missing>'} "
            f"(expected {expected_list})"
        )
    return decision


_QUEUE_CLAIM_TRANSITION_NEXT_ACTIONS = {
    "claim": {"claim_queue_item"},
    "release": {"release_queue_item"},
    "dead_letter": {"dead_letter_queue_item"},
}


def _enforce_queue_claim_transition_decision(
    *,
    operation: str,
    run_id: str,
    worker_id: Optional[str] = None,
    lease_id: Optional[str] = None,
    status: str = "claimed",
    stale: bool = False,
    reason: Optional[str] = None,
    attempts: Optional[int] = None,
) -> Dict[str, Any]:
    request = {
        "operation": operation,
        "run_id": str(run_id or "").strip(),
        "item_id": str(run_id or "").strip(),
        "requester_id": str(worker_id or "").strip(),
        "worker_id": str(worker_id or "").strip(),
        "lease_holder_id": str(worker_id or lease_id or "").strip(),
        "current_status": str(status or "claimed").strip() or "claimed",
        "status": str(status or "claimed").strip() or "claimed",
        "lease_stale": bool(stale),
        "reason": str(reason or "").strip(),
    }
    if attempts is not None:
        request["attempts"] = max(0, int(attempts))
    decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
        "queue-transition-decision",
        request,
    )
    expected_actions = _QUEUE_CLAIM_TRANSITION_NEXT_ACTIONS.get(operation)
    next_action = str(decision.get("next_action") or "").strip()
    if expected_actions is None:
        raise RuntimeError(f"unexpected queue_claim_transition operation: {operation}")
    if next_action not in expected_actions:
        expected_list = ", ".join(sorted(expected_actions))
        raise RuntimeError(
            f"unexpected next_action for queue-transition-decision: {next_action or '<missing>'} "
            f"(expected {expected_list})"
        )
    return decision


def _json_payload(value: Any) -> str:
    try:
        return json.dumps(value if value is not None else {}, ensure_ascii=False, separators=(",", ":"), default=str)
    except Exception:
        return "{}"


def _json_object(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            return parsed
    return {}


def _payload_workspace_id(payload: Dict[str, Any]) -> str:
    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    return str(payload.get("workspace_id") or context.get("workspace_id") or "default").strip() or "default"


def _payload_tenant_id(payload: Dict[str, Any]) -> str:
    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    return str(payload.get("tenant_id") or context.get("tenant_id") or metadata.get("tenant_id") or "default").strip() or "default"


def _normalize_live_run_payload(
    run_id: str,
    workspace_id: str,
    tenant_id: str,
    state: str,
    payload: Dict[str, Any],
    trace_id: str,
    *,
    version: Optional[int] = None,
    registered_at: Optional[str] = None,
) -> Dict[str, Any]:
    item = _json_object(payload)
    item["run_id"] = str(run_id or "").strip()
    item["workspace_id"] = str(workspace_id or "").strip() or "default"
    item["tenant_id"] = str(tenant_id or "").strip() or "default"
    normalized_state = str(state or "").strip() or "queued"
    item["status"] = normalized_state
    item["state"] = normalized_state
    trace_token = str(trace_id or "").strip()
    if trace_token:
        item["trace_id"] = trace_token
    if version is not None:
        item["_durable_version"] = int(version)
    if registered_at:
        item["_durable_registered_at"] = str(registered_at).strip()
    return item


def _normalized_live_run_compare_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    item = _json_object(payload)
    item.pop("_durable_version", None)
    item.pop("_durable_registered_at", None)
    return item


def _live_run_payload_from_row(row: Any) -> Dict[str, Any]:
    payload = _json_object(row["payload"])
    state = str(row["state"] or payload.get("status") or payload.get("state") or "queued").strip() or "queued"
    workspace_id = str(row["workspace_id"] or payload.get("workspace_id") or _payload_workspace_id(payload)).strip() or "default"
    tenant_id = str(row["tenant_id"] or payload.get("tenant_id") or _payload_tenant_id(payload)).strip() or "default"
    trace_id = str(row["trace_id"] or payload.get("trace_id") or "").strip()
    registered_at = str(row["registered_at"] or "").strip() or None
    version = int(row["version"] or 0)
    return _normalize_live_run_payload(
        str(row["run_id"] or payload.get("run_id") or "").strip(),
        workspace_id,
        tenant_id,
        state,
        payload,
        trace_id,
        version=version,
        registered_at=registered_at,
    )


async def _ensure_live_run_tables(pool: Any) -> None:
    await pool.execute(
        """
        CREATE TABLE IF NOT EXISTS live_runs (
            run_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            state TEXT NOT NULL,
            payload JSONB NOT NULL,
            trace_id TEXT,
            version BIGINT NOT NULL DEFAULT 0,
            registered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    await pool.execute("ALTER TABLE live_runs ADD COLUMN IF NOT EXISTS version BIGINT NOT NULL DEFAULT 0")
    await pool.execute("ALTER TABLE live_runs ADD COLUMN IF NOT EXISTS registered_at TIMESTAMPTZ NOT NULL DEFAULT NOW()")
    await pool.execute("CREATE INDEX IF NOT EXISTS idx_live_runs_workspace_state ON live_runs(workspace_id, state)")
    await pool.execute("CREATE INDEX IF NOT EXISTS idx_live_runs_updated_at ON live_runs(updated_at DESC, created_at DESC)")
    await pool.execute("CREATE INDEX IF NOT EXISTS idx_live_runs_workspace_updated_at ON live_runs(workspace_id, updated_at DESC, created_at DESC)")
    await pool.execute("CREATE INDEX IF NOT EXISTS idx_live_runs_state_updated_at ON live_runs(state, updated_at DESC, created_at DESC)")
    await pool.execute(
        """
        CREATE TABLE IF NOT EXISTS run_transitions (
            id BIGSERIAL PRIMARY KEY,
            run_id TEXT NOT NULL,
            from_state TEXT NOT NULL,
            to_state TEXT NOT NULL,
            actor TEXT NOT NULL,
            trace_id TEXT,
            timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    await pool.execute("CREATE INDEX IF NOT EXISTS idx_run_transitions_run_id ON run_transitions(run_id, timestamp DESC)")


async def _ensure_run_archive_table(pool: Any) -> None:
    await pool.execute(
        """
        CREATE TABLE IF NOT EXISTS run_archive (
            run_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            final_state TEXT NOT NULL,
            payload JSONB NOT NULL,
            trace_id TEXT,
            completed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    await pool.execute(
        "CREATE INDEX IF NOT EXISTS idx_run_archive_workspace_state ON run_archive(workspace_id, final_state, completed_at DESC)"
    )


async def _ensure_local_queue_tables(pool: Any) -> None:
    await pool.execute(
        """
        CREATE TABLE IF NOT EXISTS local_queue_claims (
            run_id TEXT PRIMARY KEY,
            worker_id TEXT NOT NULL,
            lease_id TEXT NULL,
            claimed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            ttl INTEGER NOT NULL,
            trace_id TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    await pool.execute("ALTER TABLE local_queue_claims ADD COLUMN IF NOT EXISTS lease_id TEXT NULL")
    await pool.execute("ALTER TABLE local_queue_claims ADD COLUMN IF NOT EXISTS last_heartbeat_at TIMESTAMPTZ NULL")
    await pool.execute("ALTER TABLE local_queue_claims ADD COLUMN IF NOT EXISTS last_progress_at TIMESTAMPTZ NULL")
    await pool.execute("ALTER TABLE local_queue_claims ADD COLUMN IF NOT EXISTS last_worker_note TEXT NULL")
    await pool.execute("ALTER TABLE local_queue_claims ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()")
    await pool.execute(
        "CREATE INDEX IF NOT EXISTS idx_local_queue_claims_worker_id ON local_queue_claims(worker_id, claimed_at DESC)"
    )
    await pool.execute(
        "CREATE INDEX IF NOT EXISTS idx_local_queue_claims_heartbeat ON local_queue_claims(last_heartbeat_at DESC, claimed_at DESC)"
    )
    await pool.execute(
        """
        CREATE TABLE IF NOT EXISTS local_queue_dead_letters (
            run_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            specialist_key TEXT NULL,
            reason TEXT NOT NULL,
            trace_id TEXT NULL,
            failure_count INTEGER NOT NULL DEFAULT 0,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            first_recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    await pool.execute(
        "CREATE INDEX IF NOT EXISTS idx_local_queue_dead_letters_workspace ON local_queue_dead_letters(workspace_id, last_recorded_at DESC)"
    )
    await pool.execute(
        "CREATE INDEX IF NOT EXISTS idx_local_queue_dead_letters_specialist ON local_queue_dead_letters(specialist_key, last_recorded_at DESC)"
    )


async def _ensure_fleet_runtime_tables(pool: Any) -> None:
    await pool.execute(
        """
        CREATE TABLE IF NOT EXISTS fleet_worker_registrations (
            worker_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            machine_id TEXT NOT NULL,
            runtime_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'idle',
            control_state TEXT NOT NULL DEFAULT 'active',
            current_run_id TEXT NULL,
            instance_id TEXT NULL,
            shard_key TEXT NOT NULL,
            prewarm_state TEXT NULL,
            warm_pool TEXT NULL,
            lease_seconds INTEGER NOT NULL DEFAULT 30,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            registered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_registered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_heartbeat_at TIMESTAMPTZ NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    await pool.execute(
        "CREATE INDEX IF NOT EXISTS idx_fleet_worker_registrations_workspace_seen ON fleet_worker_registrations(workspace_id, last_heartbeat_at DESC, updated_at DESC)"
    )
    await pool.execute(
        "CREATE INDEX IF NOT EXISTS idx_fleet_worker_registrations_shard ON fleet_worker_registrations(shard_key, status, control_state)"
    )
    await pool.execute(
        "CREATE INDEX IF NOT EXISTS idx_fleet_worker_registrations_prewarm ON fleet_worker_registrations(prewarm_state, runtime_type, updated_at DESC)"
    )
    await pool.execute(
        """
        CREATE TABLE IF NOT EXISTS fleet_queue_partitions (
            partition_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            specialist_key TEXT NOT NULL,
            pending_count INTEGER NOT NULL DEFAULT 0,
            claimed_count INTEGER NOT NULL DEFAULT 0,
            online_workers INTEGER NOT NULL DEFAULT 0,
            busy_workers INTEGER NOT NULL DEFAULT 0,
            idle_workers INTEGER NOT NULL DEFAULT 0,
            prewarmed_workers INTEGER NOT NULL DEFAULT 0,
            state TEXT NOT NULL DEFAULT 'healthy',
            retry_after_seconds INTEGER NOT NULL DEFAULT 0,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    await pool.execute(
        "CREATE INDEX IF NOT EXISTS idx_fleet_queue_partitions_workspace ON fleet_queue_partitions(workspace_id, state, updated_at DESC)"
    )
    await pool.execute(
        "CREATE INDEX IF NOT EXISTS idx_fleet_queue_partitions_specialist ON fleet_queue_partitions(specialist_key, updated_at DESC)"
    )


async def upsert_live_run(
    run_id: str,
    workspace_id: str,
    tenant_id: str,
    state: str,
    payload: Dict[str, Any],
    trace_id: str,
) -> None:
    token = str(run_id or "").strip()
    if not token:
        return None
    pool = await _require_pool(operation="upsert_live_run")
    normalized_workspace = str(workspace_id or "").strip() or "default"
    normalized_tenant = str(tenant_id or "").strip() or "default"
    normalized_state = str(state or "").strip() or "queued"
    normalized_trace = str(trace_id or "").strip() or None
    expected_version_raw = payload.get("_durable_version") if isinstance(payload, dict) else None
    try:
        expected_version = int(expected_version_raw) if expected_version_raw is not None else None
    except Exception:
        expected_version = None
    _enforce_runtime_state_store_decision(
        operation="upsert_live_run",
        run_id=token,
        workspace_id=normalized_workspace,
        tenant_id=normalized_tenant,
        status=normalized_state,
        trace_id=normalized_trace,
        payload=payload,
        expected_version=expected_version,
    )
    try:
        await _ensure_live_run_tables(pool)
        if expected_version is not None:
            result = await update_live_run_if_version_matches(
                token,
                normalized_workspace,
                normalized_tenant,
                normalized_state,
                payload,
                normalized_trace or "",
                expected_version=expected_version,
            )
            if result is None:
                raise RunStateVersionConflictError(
                    f"Postgres upsert_live_run version conflict for {token} at expected version {expected_version}"
                )
            return None
        await pool.execute(
            """
            INSERT INTO live_runs (
                run_id, workspace_id, tenant_id, state, payload, trace_id, version, registered_at, created_at, updated_at
            )
            VALUES ($1, $2, $3, $4, $5::jsonb, $6, 0, NOW(), NOW(), NOW())
            ON CONFLICT (run_id) DO UPDATE SET
                workspace_id = EXCLUDED.workspace_id,
                tenant_id = EXCLUDED.tenant_id,
                state = EXCLUDED.state,
                payload = EXCLUDED.payload,
                trace_id = COALESCE(NULLIF(EXCLUDED.trace_id, ''), live_runs.trace_id),
                version = live_runs.version + 1,
                updated_at = NOW()
            """,
            token,
            normalized_workspace,
            normalized_tenant,
            normalized_state,
            _json_payload(
                _normalize_live_run_payload(
                    token,
                    normalized_workspace,
                    normalized_tenant,
                    normalized_state,
                    payload,
                    normalized_trace or "",
                )
            ),
            normalized_trace,
        )
    except Exception as exc:
        raise RunStatePersistenceError(f"Postgres upsert_live_run failed for {token}: {exc}") from exc
    return None


async def create_live_run_initial(
    run_id: str,
    workspace_id: str,
    tenant_id: str,
    state: str,
    payload: Dict[str, Any],
    trace_id: str,
) -> Dict[str, Any]:
    token = str(run_id or "").strip()
    if not token:
        raise RunStatePersistenceError("Postgres create_live_run_initial requires a run_id")
    normalized_workspace = str(workspace_id or "").strip() or "default"
    normalized_tenant = str(tenant_id or "").strip() or "default"
    normalized_state = str(state or "").strip() or "queued"
    normalized_trace = str(trace_id or "").strip()
    _enforce_runtime_state_store_decision(
        operation="upsert_live_run",
        run_id=token,
        workspace_id=normalized_workspace,
        tenant_id=normalized_tenant,
        status=normalized_state,
        trace_id=normalized_trace,
        payload=payload,
        current_version=0,
    )
    pool = await _require_pool(operation="create_live_run_initial")
    try:
        await _ensure_live_run_tables(pool)
        row = await pool.fetchrow(
            """
            INSERT INTO live_runs (
                run_id, workspace_id, tenant_id, state, payload, trace_id, version, registered_at, created_at, updated_at
            )
            VALUES ($1, $2, $3, $4, $5::jsonb, $6, 0, NOW(), NOW(), NOW())
            ON CONFLICT (run_id) DO NOTHING
            RETURNING run_id, workspace_id, tenant_id, state, payload, trace_id, version, registered_at
            """,
            token,
            normalized_workspace,
            normalized_tenant,
            normalized_state,
            _json_payload(
                _normalize_live_run_payload(
                    token,
                    normalized_workspace,
                    normalized_tenant,
                    normalized_state,
                    payload,
                    normalized_trace,
                    version=0,
                )
            ),
            normalized_trace or None,
        )
    except Exception as exc:
        raise RunStatePersistenceError(f"Postgres create_live_run_initial failed for {token}: {exc}") from exc
    if row is None:
        raise RunStatePersistenceError(f"Postgres create_live_run_initial refused duplicate run_id {token}")
    return {
        "version": int(row["version"] or 0),
        "registered_at": str(row["registered_at"] or "").strip() or None,
    }


async def update_live_run_if_version_matches(
    run_id: str,
    workspace_id: str,
    tenant_id: str,
    state: str,
    payload: Dict[str, Any],
    trace_id: str,
    *,
    expected_version: int,
) -> Optional[int]:
    token = str(run_id or "").strip()
    if not token:
        return None
    normalized_workspace = str(workspace_id or "").strip() or "default"
    normalized_tenant = str(tenant_id or "").strip() or "default"
    normalized_state = str(state or "").strip() or "queued"
    normalized_trace = str(trace_id or "").strip()
    current_version = max(0, int(expected_version or 0))
    next_version = current_version + 1
    _enforce_runtime_state_store_decision(
        operation="upsert_live_run",
        run_id=token,
        workspace_id=normalized_workspace,
        tenant_id=normalized_tenant,
        status=normalized_state,
        trace_id=normalized_trace,
        payload=payload,
        expected_version=current_version,
        current_version=current_version,
    )
    desired_payload = _normalize_live_run_payload(
        token,
        normalized_workspace,
        normalized_tenant,
        normalized_state,
        payload,
        normalized_trace,
        version=next_version,
    )
    pool = await _require_pool(operation="update_live_run_if_version_matches")
    try:
        await _ensure_live_run_tables(pool)
        row = await pool.fetchrow(
            """
            UPDATE live_runs
            SET workspace_id = $2,
                tenant_id = $3,
                state = $4,
                payload = $5::jsonb,
                trace_id = COALESCE(NULLIF($6, ''), live_runs.trace_id),
                version = $8,
                updated_at = NOW()
            WHERE run_id = $1
              AND version = $7
            RETURNING version
            """,
            token,
            normalized_workspace,
            normalized_tenant,
            normalized_state,
            _json_payload(desired_payload),
            normalized_trace or None,
            current_version,
            next_version,
        )
        if row is not None:
            return int(row["version"] or next_version)
        existing = await pool.fetchrow(
            """
            SELECT run_id, workspace_id, tenant_id, state, payload, trace_id, version, registered_at
            FROM live_runs
            WHERE run_id = $1
            LIMIT 1
            """,
            token,
        )
    except Exception as exc:
        raise RunStatePersistenceError(f"Postgres update_live_run_if_version_matches failed for {token}: {exc}") from exc
    if existing is None:
        return None
    existing_payload = _live_run_payload_from_row(existing)
    if (
        str(existing_payload.get("status") or "").strip() == normalized_state
        and _normalized_live_run_compare_payload(existing_payload) == _normalized_live_run_compare_payload(desired_payload)
        and int(existing["version"] or 0) >= next_version
    ):
        return int(existing["version"] or 0)
    return None


async def get_live_run(run_id: str) -> Optional[Dict[str, Any]]:
    token = str(run_id or "").strip()
    if not token:
        return None
    pool = await _read_pool(operation="get_live_run")
    if pool is not None:
        try:
            await _ensure_live_run_tables(pool)
            row = await pool.fetchrow(
                """
                SELECT run_id, workspace_id, tenant_id, state, payload, trace_id, version, registered_at
                FROM live_runs
                WHERE run_id = $1
                LIMIT 1
                """,
                token,
            )
            if row is not None:
                return _live_run_payload_from_row(row)
        except Exception as exc:
            LOGGER.warning("Postgres get_live_run failed for %s: %s", token, exc)
    return None


async def get_archived_run(run_id: str) -> Optional[Dict[str, Any]]:
    token = str(run_id or "").strip()
    if not token:
        return None
    pool = await _read_pool(operation="get_archived_run")
    if pool is None:
        return None
    try:
        row = await pool.fetchrow(
            """
            SELECT payload
            FROM run_archive
            WHERE run_id = $1
            LIMIT 1
            """,
            token,
        )
    except Exception as exc:
        LOGGER.warning("Postgres get_archived_run failed for %s: %s", token, exc)
        return None
    if row is None:
        return None
    payload = row["payload"]
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            return parsed
    return None


async def delete_live_run(run_id: str) -> None:
    token = str(run_id or "").strip()
    if not token:
        return None
    live_run = await get_live_run(token)
    if not isinstance(live_run, dict):
        return None
    _enforce_runtime_state_store_decision(
        operation="delete_live_run",
        run_id=token,
        workspace_id=_payload_workspace_id(live_run),
        tenant_id=_payload_tenant_id(live_run),
        status=str(live_run.get("status") or live_run.get("state") or "archived").strip() or "archived",
        trace_id=str(live_run.get("trace_id") or "").strip(),
        state_class="live_runs",
    )
    pool = await _require_pool(operation="delete_live_run")
    try:
        await _ensure_live_run_tables(pool)
        await pool.execute(
            """
            DELETE FROM live_runs
            WHERE run_id = $1
            """,
            token,
        )
    except Exception as exc:
        raise RunStatePersistenceError(f"Postgres delete_live_run failed for {token}: {exc}") from exc
    return None


async def list_live_runs() -> list[Dict[str, Any]]:
    pool = await _read_pool(operation="list_live_runs")
    if pool is None:
        return []
    try:
        await _ensure_live_run_tables(pool)
        rows = await pool.fetch(
            """
            SELECT run_id, workspace_id, tenant_id, state, payload, trace_id, version, registered_at
            FROM live_runs
            ORDER BY updated_at DESC, created_at DESC
            """
        )
    except Exception as exc:
        LOGGER.warning("Postgres list_live_runs failed: %s", exc)
        return []
    items: list[Dict[str, Any]] = []
    for row in rows or []:
        items.append(_live_run_payload_from_row(row))
    return items


async def list_live_runs_page(
    *,
    limit: int = 100,
    offset: int = 0,
    workspace_id: Optional[str] = None,
    states: Optional[list[str]] = None,
) -> list[Dict[str, Any]]:
    pool = await _read_pool(operation="list_live_runs_page")
    if pool is None:
        return []
    workspace_filter = str(workspace_id or "").strip()
    normalized_states = [str(state or "").strip().lower() for state in (states or []) if str(state or "").strip()]
    try:
        await _ensure_live_run_tables(pool)
        rows = await pool.fetch(
            """
            SELECT run_id, workspace_id, tenant_id, state, payload, trace_id, version, registered_at
            FROM live_runs
            WHERE ($1 = '' OR workspace_id = $1)
              AND (CARDINALITY($2::text[]) = 0 OR LOWER(COALESCE(state, '')) = ANY($2::text[]))
            ORDER BY updated_at DESC, created_at DESC
            LIMIT $3
            OFFSET $4
            """,
            workspace_filter,
            normalized_states,
            max(1, min(int(limit or 0), 500)),
            max(0, int(offset or 0)),
        )
    except Exception as exc:
        LOGGER.warning("Postgres list_live_runs_page failed: %s", exc)
        return []
    return [_live_run_payload_from_row(row) for row in (rows or [])]


async def count_live_runs(
    *,
    workspace_id: Optional[str] = None,
    states: Optional[list[str]] = None,
) -> int:
    pool = await _read_pool(operation="count_live_runs")
    if pool is None:
        return 0
    workspace_filter = str(workspace_id or "").strip()
    normalized_states = [str(state or "").strip().lower() for state in (states or []) if str(state or "").strip()]
    try:
        await _ensure_live_run_tables(pool)
        row = await pool.fetchrow(
            """
            SELECT COUNT(*)::int AS count
            FROM live_runs
            WHERE ($1 = '' OR workspace_id = $1)
              AND (CARDINALITY($2::text[]) = 0 OR LOWER(COALESCE(state, '')) = ANY($2::text[]))
            """,
            workspace_filter,
            normalized_states,
        )
    except Exception as exc:
        LOGGER.warning("Postgres count_live_runs failed: %s", exc)
        return 0
    if row is None:
        return 0
    return int(row.get("count") if isinstance(row, dict) else row["count"] or 0)


async def count_hosted_live_runs(
    workspace_id: str,
    *,
    terminal_states: Optional[list[str]] = None,
) -> int:
    workspace_filter = str(workspace_id or "").strip()
    if not workspace_filter:
        return 0
    pool = await _read_pool(operation="count_hosted_live_runs")
    if pool is None:
        return 0
    normalized_terminal_states = [
        str(state or "").strip().lower()
        for state in (terminal_states or ["completed", "failed", "cancelled", "canceled", "timeout", "aborted"])
        if str(state or "").strip()
    ]
    try:
        await _ensure_live_run_tables(pool)
        row = await pool.fetchrow(
            """
            SELECT COUNT(*)::int AS count
            FROM live_runs
            WHERE workspace_id = $1
              AND NOT (LOWER(COALESCE(state, '')) = ANY($2::text[]))
              AND LOWER(COALESCE(payload #>> '{context,metadata,runtime_attachment_kind}', '')) <> 'self_hosted_business_node'
              AND (
                    LOWER(COALESCE(payload #>> '{context,metadata,execution_target_selected}', '')) = 'cloud'
                 OR LOWER(COALESCE(payload #>> '{context,metadata,runtime_mode}', payload->>'runtime_mode', '')) = 'hosted_secure'
              )
            """,
            workspace_filter,
            normalized_terminal_states,
        )
    except Exception as exc:
        LOGGER.warning("Postgres count_hosted_live_runs failed: %s", exc)
        return 0
    if row is None:
        return 0
    return int(row.get("count") if isinstance(row, dict) else row["count"] or 0)


async def list_live_runs_by_state(states: list[str]) -> list[Dict[str, Any]]:
    normalized_states = [str(state or "").strip().lower() for state in (states or []) if str(state or "").strip()]
    if not normalized_states:
        return await list_live_runs()
    pool = await _read_pool(operation="list_live_runs_by_state")
    if pool is None:
        return []
    try:
        await _ensure_live_run_tables(pool)
        rows = await pool.fetch(
            """
            SELECT run_id, workspace_id, tenant_id, state, payload, trace_id, version, registered_at
            FROM live_runs
            WHERE LOWER(COALESCE(state, '')) = ANY($1::text[])
            ORDER BY updated_at DESC, created_at DESC
            """,
            normalized_states,
        )
    except Exception as exc:
        LOGGER.warning("Postgres list_live_runs_by_state failed: %s", exc)
        return []
    items: list[Dict[str, Any]] = []
    for row in rows or []:
        items.append(_live_run_payload_from_row(row))
    return items


async def list_run_archive(limit: int = 200) -> list[Dict[str, Any]]:
    pool = await _read_pool(operation="list_run_archive")
    if pool is None:
        return []
    try:
        rows = await pool.fetch(
            """
            SELECT payload
            FROM run_archive
            ORDER BY completed_at DESC
            LIMIT $1
            """,
            max(1, int(limit or 0)),
        )
    except Exception as exc:
        LOGGER.warning("Postgres list_run_archive failed: %s", exc)
        return []
    items: list[Dict[str, Any]] = []
    for row in rows or []:
        payload = row["payload"]
        if isinstance(payload, dict):
            items.append(payload)
            continue
        if isinstance(payload, str):
            try:
                parsed = json.loads(payload)
            except Exception:
                parsed = None
            if isinstance(parsed, dict):
                items.append(parsed)
    return items


async def find_live_run_by_approval_id(approval_id: str) -> Optional[Dict[str, Any]]:
    approval_token = str(approval_id or "").strip()
    if not approval_token:
        return None
    pool = await _read_pool(operation="find_live_run_by_approval_id")
    if pool is None:
        return None
    try:
        await _ensure_live_run_tables(pool)
        row = await pool.fetchrow(
            """
            SELECT run_id, workspace_id, tenant_id, state, payload, trace_id, version, registered_at
            FROM live_runs
            WHERE
                payload @> jsonb_build_object('pending_confirmation', jsonb_build_object('approval_id', $1::text)) OR
                payload @> jsonb_build_object('pending_approval', jsonb_build_object('approval_id', $1::text))
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            approval_token,
        )
    except Exception as exc:
        LOGGER.warning("Postgres find_live_run_by_approval_id failed for %s: %s", approval_token, exc)
        return None
    if row is None:
        return None
    return _live_run_payload_from_row(row)


async def record_transition(
    run_id: str,
    from_state: str,
    to_state: str,
    actor: str,
    trace_id: str,
) -> None:
    token = str(run_id or "").strip()
    if not token:
        return None
    live_run = await get_live_run(token)
    live_payload = dict(live_run or {})
    decision = _enforce_run_record_decision(
        operation="record_transition",
        run_id=token,
        workspace_id=_payload_workspace_id(live_payload),
        tenant_id=_payload_tenant_id(live_payload),
        from_state=str(from_state or "").strip() or "unknown",
        to_state=str(to_state or "").strip() or "unknown",
        version=_safe_int(live_payload.get("_event_seq") or live_payload.get("version"), 0),
    )
    if str(decision.get("next_action") or "").strip() == "noop":
        return None
    pool = await _require_pool(operation="record_transition")
    try:
        await _ensure_live_run_tables(pool)
        await pool.execute(
            """
            INSERT INTO run_transitions (run_id, from_state, to_state, actor, trace_id, timestamp)
            SELECT $1, $2, $3, $4, $5, NOW()
            WHERE NOT EXISTS (
                SELECT 1
                FROM run_transitions
                WHERE run_id = $1
                  AND from_state = $2
                  AND to_state = $3
                  AND actor = $4
                  AND COALESCE(trace_id, '') = COALESCE($5, '')
            )
            """,
            token,
            str(from_state or "").strip() or "unknown",
            str(to_state or "").strip() or "unknown",
            str(actor or "").strip() or "runtime",
            str(trace_id or "").strip() or None,
        )
    except Exception as exc:
        raise RunStatePersistenceError(f"Postgres record_transition failed for {token}: {exc}") from exc
    return None


async def archive_run(
    run_id: str,
    final_state: str,
    payload: Dict[str, Any],
    trace_id: str,
) -> None:
    token = str(run_id or "").strip()
    if not token:
        return None
    normalized_final_state = str(final_state or "").strip() or "completed"
    archive_payload = dict(payload or {})
    run_record_decision = _enforce_run_record_decision(
        operation="archive_payload",
        run_id=token,
        workspace_id=_payload_workspace_id(archive_payload),
        tenant_id=_payload_tenant_id(archive_payload),
        status=normalized_final_state,
        final_state=normalized_final_state,
        version=_safe_int(archive_payload.get("_event_seq") or archive_payload.get("version"), 0),
    )
    _apply_run_record_patch(archive_payload, run_record_decision)
    archive_state = str(archive_payload.get("status") or archive_payload.get("state") or normalized_final_state).strip() or normalized_final_state
    _enforce_runtime_state_store_decision(
        operation="archive_run",
        run_id=token,
        workspace_id=_payload_workspace_id(archive_payload),
        tenant_id=_payload_tenant_id(archive_payload),
        status=archive_state,
        trace_id=trace_id,
        payload=archive_payload,
    )
    pool = await _require_pool(operation="archive_run")
    try:
        await _ensure_run_archive_table(pool)
        await pool.execute(
            """
            INSERT INTO run_archive (run_id, workspace_id, tenant_id, final_state, payload, trace_id, completed_at)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6, NOW())
            ON CONFLICT (run_id) DO UPDATE SET
                workspace_id = EXCLUDED.workspace_id,
                tenant_id = EXCLUDED.tenant_id,
                final_state = EXCLUDED.final_state,
                payload = EXCLUDED.payload,
                trace_id = COALESCE(NULLIF(EXCLUDED.trace_id, ''), run_archive.trace_id),
                completed_at = NOW()
            """,
            token,
            _payload_workspace_id(archive_payload),
            _payload_tenant_id(archive_payload),
            archive_state,
            _json_payload(archive_payload),
            str(trace_id or "").strip() or None,
        )
    except Exception as exc:
        raise RunStatePersistenceError(f"Postgres archive_run failed for {token}: {exc}") from exc
    return None


async def claim_run(run_id: str, worker_id: str, ttl: int, trace_id: str, *, lease_id: Optional[str] = None) -> None:
    token = str(run_id or "").strip()
    worker = str(worker_id or "").strip()
    lease = str(lease_id or "").strip() or None
    if not token or not worker:
        return None
    _enforce_queue_claim_transition_decision(
        operation="claim",
        run_id=token,
        worker_id=worker,
        lease_id=lease,
        status="queued",
    )
    pool = await _require_pool(operation="claim_run")
    try:
        await _ensure_local_queue_tables(pool)
        row = await pool.fetchrow(
            """
            INSERT INTO local_queue_claims (
                run_id,
                worker_id,
                lease_id,
                claimed_at,
                ttl,
                trace_id,
                last_heartbeat_at,
                last_progress_at,
                last_worker_note,
                updated_at
            )
            VALUES ($1, $2, $3, NOW(), $4, $5, NOW(), NOW(), NULL, NOW())
            ON CONFLICT (run_id) DO UPDATE SET
                worker_id = EXCLUDED.worker_id,
                lease_id = COALESCE(EXCLUDED.lease_id, local_queue_claims.lease_id),
                claimed_at = NOW(),
                ttl = EXCLUDED.ttl,
                trace_id = COALESCE(NULLIF(EXCLUDED.trace_id, ''), local_queue_claims.trace_id),
                last_heartbeat_at = NOW(),
                last_progress_at = NOW(),
                last_worker_note = NULL,
                updated_at = NOW()
            WHERE
                (
                    local_queue_claims.worker_id = EXCLUDED.worker_id
                    AND (
                        (EXCLUDED.lease_id IS NULL AND local_queue_claims.lease_id IS NULL)
                        OR local_queue_claims.lease_id = EXCLUDED.lease_id
                    )
                ) OR
                COALESCE(local_queue_claims.last_heartbeat_at, local_queue_claims.claimed_at) +
                    (GREATEST(COALESCE(local_queue_claims.ttl, 0), 1) * INTERVAL '1 second') <= NOW()
            RETURNING run_id
            """,
            token,
            worker,
            lease,
            max(1, int(ttl or 0)),
            str(trace_id or "").strip() or None,
        )
    except Exception as exc:
        raise RunStatePersistenceError(f"Postgres claim_run failed for {token}: {exc}") from exc
    if row is None:
        raise RunClaimConflictError(
            f"Run {token} is already claimed by another live worker; refusing to overwrite the active claim"
        )
    return None


async def release_claim(run_id: str, *, lease_id: Optional[str] = None) -> bool:
    token = str(run_id or "").strip()
    lease = str(lease_id or "").strip() or None
    if not token:
        return False
    _enforce_queue_claim_transition_decision(
        operation="release",
        run_id=token,
        lease_id=lease,
        status="claimed",
        stale=lease is None,
    )
    pool = await _require_pool(operation="release_claim")
    try:
        await _ensure_local_queue_tables(pool)
        row = await pool.fetchrow(
            """
            DELETE FROM local_queue_claims
            WHERE run_id = $1
              AND (
                ($2::text IS NULL AND lease_id IS NULL)
                OR lease_id = $2
              )
            RETURNING run_id
            """,
            token,
            lease,
        )
    except Exception as exc:
        raise RunStatePersistenceError(f"Postgres release_claim failed for {token}: {exc}") from exc
    return row is not None


async def touch_claim_heartbeat(
    run_id: str,
    worker_id: str,
    *,
    lease_id: Optional[str] = None,
    note: Optional[str] = None,
    progress: bool = False,
) -> bool:
    token = str(run_id or "").strip()
    worker = str(worker_id or "").strip()
    lease = str(lease_id or "").strip() or None
    if not token or not worker:
        return False
    _enforce_queue_claim_transition_decision(
        operation="claim",
        run_id=token,
        worker_id=worker,
        lease_id=lease,
        status="claimed",
    )
    pool = await _require_pool(operation="touch_claim_heartbeat")
    try:
        await _ensure_local_queue_tables(pool)
        row = await pool.fetchrow(
            """
            UPDATE local_queue_claims
            SET
                last_heartbeat_at = NOW(),
                last_progress_at = CASE WHEN $3 THEN NOW() ELSE last_progress_at END,
                last_worker_note = CASE
                    WHEN NULLIF($4, '') IS NOT NULL THEN LEFT($4, 280)
                    ELSE last_worker_note
                END,
                updated_at = NOW()
            WHERE run_id = $1
              AND worker_id = $2
              AND (
                ($5::text IS NULL AND lease_id IS NULL)
                OR lease_id = $5
              )
            RETURNING run_id
            """,
            token,
            worker,
            bool(progress),
            str(note or "").strip() or None,
            lease,
        )
    except Exception as exc:
        raise RunStatePersistenceError(f"Postgres touch_claim_heartbeat failed for {token}: {exc}") from exc
    return row is not None


async def append_local_queue_dead_letter(
    *,
    run_id: str,
    tenant_id: str,
    workspace_id: str,
    specialist_key: Optional[str],
    reason: str,
    trace_id: str,
    failure_count: int,
    payload: Dict[str, Any],
) -> None:
    token = str(run_id or "").strip()
    if not token:
        return None
    normalized_reason = str(reason or "").strip() or "worker_failure"
    _enforce_queue_claim_transition_decision(
        operation="dead_letter",
        run_id=token,
        status="failed",
        reason=normalized_reason,
        attempts=max(0, int(failure_count or 0)),
    )
    pool = await _require_pool(operation="append_local_queue_dead_letter")
    try:
        await _ensure_local_queue_tables(pool)
        await pool.execute(
            """
            INSERT INTO local_queue_dead_letters (
                run_id,
                tenant_id,
                workspace_id,
                specialist_key,
                reason,
                trace_id,
                failure_count,
                payload,
                first_recorded_at,
                last_recorded_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, NOW(), NOW())
            ON CONFLICT (run_id) DO UPDATE SET
                tenant_id = EXCLUDED.tenant_id,
                workspace_id = EXCLUDED.workspace_id,
                specialist_key = EXCLUDED.specialist_key,
                reason = EXCLUDED.reason,
                trace_id = COALESCE(NULLIF(EXCLUDED.trace_id, ''), local_queue_dead_letters.trace_id),
                failure_count = GREATEST(local_queue_dead_letters.failure_count, EXCLUDED.failure_count),
                payload = EXCLUDED.payload,
                last_recorded_at = NOW()
            """,
            token,
            str(tenant_id or "").strip() or "default",
            str(workspace_id or "").strip() or "default",
            str(specialist_key or "").strip() or None,
            normalized_reason,
            str(trace_id or "").strip() or None,
            max(0, int(failure_count or 0)),
            _json_payload(payload),
        )
    except Exception as exc:
        raise RunStatePersistenceError(f"Postgres append_local_queue_dead_letter failed for {token}: {exc}") from exc
    return None


async def get_local_queue_dead_letter_status() -> Dict[str, Any]:
    pool = await _read_pool(operation="get_local_queue_dead_letter_status")
    if pool is None:
        return {
            "dead_letter_count": 0,
            "total_failure_count": 0,
            "last_recorded_at": None,
            "workspace_hotspots": [],
            "specialist_hotspots": [],
        }
    try:
        await _ensure_local_queue_tables(pool)
        summary_row = await pool.fetchrow(
            """
            SELECT
                COUNT(*)::int AS dead_letter_count,
                COALESCE(SUM(failure_count), 0)::int AS total_failure_count,
                MAX(last_recorded_at) AS last_recorded_at
            FROM local_queue_dead_letters
            """
        )
        workspace_rows = await pool.fetch(
            """
            SELECT workspace_id, COUNT(*)::int AS count
            FROM local_queue_dead_letters
            GROUP BY workspace_id
            ORDER BY count DESC, workspace_id ASC
            LIMIT 5
            """
        )
        specialist_rows = await pool.fetch(
            """
            SELECT COALESCE(NULLIF(specialist_key, ''), 'workspace-default') AS specialist_key, COUNT(*)::int AS count
            FROM local_queue_dead_letters
            GROUP BY COALESCE(NULLIF(specialist_key, ''), 'workspace-default')
            ORDER BY count DESC, specialist_key ASC
            LIMIT 5
            """
        )
    except Exception as exc:
        LOGGER.warning("Postgres get_local_queue_dead_letter_status failed: %s", exc)
        return {
            "dead_letter_count": 0,
            "total_failure_count": 0,
            "last_recorded_at": None,
            "workspace_hotspots": [],
            "specialist_hotspots": [],
        }
    return {
        "dead_letter_count": int(summary_row["dead_letter_count"] if summary_row is not None else 0),
        "total_failure_count": int(summary_row["total_failure_count"] if summary_row is not None else 0),
        "last_recorded_at": str(summary_row["last_recorded_at"] or "").strip() or None if summary_row is not None else None,
        "workspace_hotspots": [
            {
                "workspace_id": str(row["workspace_id"] or "").strip() or "default",
                "count": int(row["count"] or 0),
            }
            for row in (workspace_rows or [])
        ],
        "specialist_hotspots": [
            {
                "specialist_key": str(row["specialist_key"] or "").strip() or "workspace-default",
                "count": int(row["count"] or 0),
            }
            for row in (specialist_rows or [])
        ],
    }


async def upsert_fleet_worker(record: Dict[str, Any], *, heartbeat_seen: bool = True) -> None:
    worker_id = str(record.get("worker_id") or record.get("runtime_id") or "").strip()
    if not worker_id:
        return None
    payload = dict(record or {})
    payload["worker_id"] = worker_id
    payload["runtime_id"] = str(payload.get("runtime_id") or worker_id).strip() or worker_id
    payload["machine_id"] = str(payload.get("machine_id") or payload["runtime_id"] or worker_id).strip() or worker_id
    payload["tenant_id"] = str(payload.get("tenant_id") or "default").strip() or "default"
    payload["workspace_id"] = str(payload.get("workspace_id") or "default").strip() or "default"
    execution_targets = payload.get("execution_targets") if isinstance(payload.get("execution_targets"), list) else []
    first_target = str(execution_targets[0] if execution_targets else "").strip().lower() or "local"
    shard_key = str(payload.get("queue_shard") or "").strip() or (
        f"{payload['tenant_id']}:{payload['workspace_id']}:{str(payload.get('runtime_type') or 'local').strip() or 'local'}:{first_target}"
    )
    payload["queue_shard"] = shard_key
    _enforce_runtime_state_store_decision(
        operation="upsert_runtime_registration",
        runtime_id=worker_id,
        workspace_id=payload["workspace_id"],
        tenant_id=payload["tenant_id"],
        status=str(payload.get("status") or "idle").strip() or "idle",
        trace_id=str(payload.get("trace_id") or "").strip(),
        payload=payload,
        state_class="runtime_registrations",
    )
    pool = await _require_pool(operation="upsert_fleet_worker")
    try:
        await _ensure_fleet_runtime_tables(pool)
        await pool.execute(
            """
            INSERT INTO fleet_worker_registrations (
                worker_id,
                tenant_id,
                workspace_id,
                machine_id,
                runtime_type,
                status,
                control_state,
                current_run_id,
                instance_id,
                shard_key,
                prewarm_state,
                warm_pool,
                lease_seconds,
                payload,
                registered_at,
                last_registered_at,
                last_heartbeat_at,
                updated_at
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14::jsonb,
                NOW(), NOW(), CASE WHEN $15 THEN NOW() ELSE NULL END, NOW()
            )
            ON CONFLICT (worker_id) DO UPDATE SET
                tenant_id = EXCLUDED.tenant_id,
                workspace_id = EXCLUDED.workspace_id,
                machine_id = EXCLUDED.machine_id,
                runtime_type = EXCLUDED.runtime_type,
                status = EXCLUDED.status,
                control_state = EXCLUDED.control_state,
                current_run_id = EXCLUDED.current_run_id,
                instance_id = EXCLUDED.instance_id,
                shard_key = EXCLUDED.shard_key,
                prewarm_state = EXCLUDED.prewarm_state,
                warm_pool = EXCLUDED.warm_pool,
                lease_seconds = EXCLUDED.lease_seconds,
                payload = EXCLUDED.payload,
                last_registered_at = NOW(),
                last_heartbeat_at = CASE
                    WHEN $15 THEN NOW()
                    ELSE fleet_worker_registrations.last_heartbeat_at
                END,
                updated_at = NOW()
            """,
            worker_id,
            payload["tenant_id"],
            payload["workspace_id"],
            payload["machine_id"],
            str(payload.get("runtime_type") or "local").strip() or "local",
            str(payload.get("status") or "idle").strip() or "idle",
            str(payload.get("control_state") or "active").strip().lower() or "active",
            str(payload.get("current_run_id") or "").strip() or None,
            str(payload.get("instance_id") or "").strip() or None,
            shard_key,
            str(payload.get("prewarm_state") or "").strip().lower() or None,
            str(payload.get("warm_pool") or "").strip() or None,
            max(1, int(payload.get("lease_seconds") or 30)),
            _json_payload(payload),
            bool(heartbeat_seen),
        )
    except Exception as exc:
        raise RunStatePersistenceError(f"Postgres upsert_fleet_worker failed for {worker_id}: {exc}") from exc
    return None


async def get_fleet_worker(worker_id: str) -> Optional[Dict[str, Any]]:
    token = str(worker_id or "").strip()
    if not token:
        return None
    pool = await _read_pool(operation="get_fleet_worker")
    if pool is None:
        return None
    try:
        await _ensure_fleet_runtime_tables(pool)
        row = await pool.fetchrow(
            """
            SELECT worker_id, tenant_id, workspace_id, machine_id, runtime_type, status, control_state,
                   current_run_id, instance_id, shard_key, prewarm_state, warm_pool, lease_seconds,
                   registered_at, last_registered_at, last_heartbeat_at, updated_at, payload
            FROM fleet_worker_registrations
            WHERE worker_id = $1
            LIMIT 1
            """,
            token,
        )
    except Exception as exc:
        LOGGER.warning("Postgres get_fleet_worker failed for %s: %s", token, exc)
        return None
    if row is None:
        return None
    payload = _json_object(row["payload"])
    payload.update(
        {
            "worker_id": str(row["worker_id"] or "").strip(),
            "runtime_id": str(payload.get("runtime_id") or row["worker_id"] or "").strip(),
            "tenant_id": str(row["tenant_id"] or "").strip() or "default",
            "workspace_id": str(row["workspace_id"] or "").strip() or "default",
            "machine_id": str(row["machine_id"] or "").strip() or token,
            "runtime_type": str(row["runtime_type"] or "").strip() or "local",
            "status": str(row["status"] or "").strip() or "idle",
            "control_state": str(row["control_state"] or "").strip().lower() or "active",
            "current_run_id": str(row["current_run_id"] or "").strip() or None,
            "instance_id": str(row["instance_id"] or "").strip() or None,
            "queue_shard": str(row["shard_key"] or "").strip() or None,
            "prewarm_state": str(row["prewarm_state"] or "").strip() or None,
            "warm_pool": str(row["warm_pool"] or "").strip() or None,
            "lease_seconds": int(row["lease_seconds"] or 30),
            "registered_at": str(row["registered_at"] or "").strip() or None,
            "last_registered_at": str(row["last_registered_at"] or "").strip() or None,
            "last_heartbeat_at": str(row["last_heartbeat_at"] or "").strip() or None,
            "updated_at": str(row["updated_at"] or "").strip() or None,
        }
    )
    return payload


async def list_fleet_workers(
    *,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
) -> list[Dict[str, Any]]:
    pool = await _read_pool(operation="list_fleet_workers")
    if pool is None:
        return []
    tenant_filter = str(tenant_id or "").strip()
    workspace_filter = str(workspace_id or "").strip()
    try:
        await _ensure_fleet_runtime_tables(pool)
        rows = await pool.fetch(
            """
            SELECT worker_id, tenant_id, workspace_id, machine_id, runtime_type, status, control_state,
                   current_run_id, instance_id, shard_key, prewarm_state, warm_pool, lease_seconds,
                   registered_at, last_registered_at, last_heartbeat_at, updated_at, payload
            FROM fleet_worker_registrations
            WHERE ($1 = '' OR tenant_id = $1)
              AND ($2 = '' OR workspace_id = $2)
            ORDER BY COALESCE(last_heartbeat_at, updated_at, last_registered_at) DESC, worker_id ASC
            """,
            tenant_filter,
            workspace_filter,
        )
    except Exception as exc:
        LOGGER.warning("Postgres list_fleet_workers failed: %s", exc)
        return []
    items: list[Dict[str, Any]] = []
    for row in rows or []:
        payload = _json_object(row["payload"])
        payload.update(
            {
                "worker_id": str(row["worker_id"] or "").strip(),
                "runtime_id": str(payload.get("runtime_id") or row["worker_id"] or "").strip(),
                "tenant_id": str(row["tenant_id"] or "").strip() or "default",
                "workspace_id": str(row["workspace_id"] or "").strip() or "default",
                "machine_id": str(row["machine_id"] or "").strip() or str(row["worker_id"] or "").strip(),
                "runtime_type": str(row["runtime_type"] or "").strip() or "local",
                "status": str(row["status"] or "").strip() or "idle",
                "control_state": str(row["control_state"] or "").strip().lower() or "active",
                "current_run_id": str(row["current_run_id"] or "").strip() or None,
                "instance_id": str(row["instance_id"] or "").strip() or None,
                "queue_shard": str(row["shard_key"] or "").strip() or None,
                "prewarm_state": str(row["prewarm_state"] or "").strip() or None,
                "warm_pool": str(row["warm_pool"] or "").strip() or None,
                "lease_seconds": int(row["lease_seconds"] or 30),
                "registered_at": str(row["registered_at"] or "").strip() or None,
                "last_registered_at": str(row["last_registered_at"] or "").strip() or None,
                "last_heartbeat_at": str(row["last_heartbeat_at"] or "").strip() or None,
                "updated_at": str(row["updated_at"] or "").strip() or None,
            }
        )
        items.append(payload)
    return items


async def upsert_fleet_queue_partition(
    *,
    partition_id: str,
    tenant_id: str,
    workspace_id: str,
    specialist_key: str,
    pending_count: int,
    claimed_count: int,
    online_workers: int,
    busy_workers: int,
    idle_workers: int,
    prewarmed_workers: int,
    state: str,
    retry_after_seconds: int,
    payload: Dict[str, Any],
) -> None:
    token = str(partition_id or "").strip()
    if not token:
        return None
    _enforce_runtime_state_store_decision(
        operation="upsert_fleet_queue_partition",
        workspace_id=str(workspace_id or "").strip() or "default",
        tenant_id=str(tenant_id or "").strip() or "default",
        status=str(state or "").strip().lower() or "healthy",
        payload=payload,
        state_class="fleet_queue_partitions",
    )
    pool = await _require_pool(operation="upsert_fleet_queue_partition")
    try:
        await _ensure_fleet_runtime_tables(pool)
        await pool.execute(
            """
            INSERT INTO fleet_queue_partitions (
                partition_id,
                tenant_id,
                workspace_id,
                specialist_key,
                pending_count,
                claimed_count,
                online_workers,
                busy_workers,
                idle_workers,
                prewarmed_workers,
                state,
                retry_after_seconds,
                payload,
                updated_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13::jsonb, NOW())
            ON CONFLICT (partition_id) DO UPDATE SET
                tenant_id = EXCLUDED.tenant_id,
                workspace_id = EXCLUDED.workspace_id,
                specialist_key = EXCLUDED.specialist_key,
                pending_count = EXCLUDED.pending_count,
                claimed_count = EXCLUDED.claimed_count,
                online_workers = EXCLUDED.online_workers,
                busy_workers = EXCLUDED.busy_workers,
                idle_workers = EXCLUDED.idle_workers,
                prewarmed_workers = EXCLUDED.prewarmed_workers,
                state = EXCLUDED.state,
                retry_after_seconds = EXCLUDED.retry_after_seconds,
                payload = EXCLUDED.payload,
                updated_at = NOW()
            """,
            token,
            str(tenant_id or "").strip() or "default",
            str(workspace_id or "").strip() or "default",
            str(specialist_key or "").strip() or "workspace-default",
            max(0, int(pending_count or 0)),
            max(0, int(claimed_count or 0)),
            max(0, int(online_workers or 0)),
            max(0, int(busy_workers or 0)),
            max(0, int(idle_workers or 0)),
            max(0, int(prewarmed_workers or 0)),
            str(state or "").strip().lower() or "healthy",
            max(0, int(retry_after_seconds or 0)),
            _json_payload(payload),
        )
    except Exception as exc:
        raise RunStatePersistenceError(f"Postgres upsert_fleet_queue_partition failed for {token}: {exc}") from exc
    return None


async def list_fleet_queue_partitions(
    *,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
) -> list[Dict[str, Any]]:
    pool = await _read_pool(operation="list_fleet_queue_partitions")
    if pool is None:
        return []
    tenant_filter = str(tenant_id or "").strip()
    workspace_filter = str(workspace_id or "").strip()
    try:
        await _ensure_fleet_runtime_tables(pool)
        rows = await pool.fetch(
            """
            SELECT partition_id, tenant_id, workspace_id, specialist_key, pending_count, claimed_count,
                   online_workers, busy_workers, idle_workers, prewarmed_workers, state, retry_after_seconds,
                   updated_at, payload
            FROM fleet_queue_partitions
            WHERE ($1 = '' OR tenant_id = $1)
              AND ($2 = '' OR workspace_id = $2)
            ORDER BY updated_at DESC, partition_id ASC
            """,
            tenant_filter,
            workspace_filter,
        )
    except Exception as exc:
        LOGGER.warning("Postgres list_fleet_queue_partitions failed: %s", exc)
        return []
    items: list[Dict[str, Any]] = []
    for row in rows or []:
        payload = _json_object(row["payload"])
        payload.update(
            {
                "partition_id": str(row["partition_id"] or "").strip(),
                "tenant_id": str(row["tenant_id"] or "").strip() or "default",
                "workspace_id": str(row["workspace_id"] or "").strip() or "default",
                "specialist_key": str(row["specialist_key"] or "").strip() or "workspace-default",
                "pending_count": int(row["pending_count"] or 0),
                "claimed_count": int(row["claimed_count"] or 0),
                "online_workers": int(row["online_workers"] or 0),
                "busy_workers": int(row["busy_workers"] or 0),
                "idle_workers": int(row["idle_workers"] or 0),
                "prewarmed_workers": int(row["prewarmed_workers"] or 0),
                "state": str(row["state"] or "").strip().lower() or "healthy",
                "retry_after_seconds": int(row["retry_after_seconds"] or 0),
                "updated_at": str(row["updated_at"] or "").strip() or None,
            }
        )
        items.append(payload)
    return items


def _approval_status_token(value: Any) -> str:
    token = str(value or "").strip().lower()
    return token or "requested"


def _approval_resolution_token(value: Any) -> str:
    token = str(value or "").strip().lower()
    return token or "approved"


def _approval_final_status(resolution: str) -> str:
    resolution_token = _approval_resolution_token(resolution)
    if resolution_token in {"expired", "cancelled", "canceled", "dismissed"}:
        return resolution_token
    return "resolved"


def _coerce_postgres_timestamptz(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    token = str(value or "").strip()
    if not token:
        return None
    try:
        return datetime.fromisoformat(token.replace("Z", "+00:00"))
    except ValueError:
        return None


def _approval_request_timestamp(request_payload: Dict[str, Any]) -> datetime | None:
    token = str(
        request_payload.get("requested_at")
        or request_payload.get("created_at")
        or ""
    ).strip()
    return _coerce_postgres_timestamptz(token)


def _approval_record_from_row(row: Any) -> Dict[str, Any]:
    row_run_id = row.get("run_id") if isinstance(row, dict) else row["run_id"]
    row_step_id = row.get("step_id") if isinstance(row, dict) else row["step_id"]
    row_approval_id = row.get("approval_id") if isinstance(row, dict) else row["approval_id"]
    row_status = row.get("status") if isinstance(row, dict) else row["status"]
    row_requested_at = row.get("requested_at") if isinstance(row, dict) else row["requested_at"]
    row_resolved_at = row.get("resolved_at") if isinstance(row, dict) else row["resolved_at"]
    row_resolution = row.get("resolution") if isinstance(row, dict) else row["resolution"]
    row_actor = row.get("actor") if isinstance(row, dict) else row["actor"]
    row_trace_id = row.get("trace_id") if isinstance(row, dict) else row["trace_id"]
    row_expires_at = row.get("expires_at") if isinstance(row, dict) else row["expires_at"]
    row_updated_at = row.get("updated_at") if isinstance(row, dict) else row["updated_at"]
    row_version = row.get("version") if isinstance(row, dict) else row["version"]
    request_payload = _json_object(row["request_payload"])
    decision_payload = _json_object(row["decision_payload"])
    metadata = _json_object(row["metadata"])
    approval_id = str(row_approval_id or row_step_id or request_payload.get("approval_id") or "").strip()
    workspace_id = str(
        request_payload.get("workspace_id")
        or metadata.get("workspace_id")
        or "default"
    ).strip() or "default"
    tenant_id = str(
        request_payload.get("tenant_id")
        or metadata.get("tenant_id")
        or "default"
    ).strip() or "default"
    owner_user_id = str(
        request_payload.get("owner_user_id")
        or metadata.get("owner_user_id")
        or ""
    ).strip() or None
    owner_email = str(
        request_payload.get("owner_email")
        or metadata.get("owner_email")
        or ""
    ).strip().lower() or None
    prompt = str(
        request_payload.get("prompt")
        or request_payload.get("reason")
        or "Approval required."
    ).strip()
    actions = request_payload.get("actions")
    if not isinstance(actions, list):
        actions = metadata.get("approval_actions")
    labels = request_payload.get("labels")
    if not isinstance(labels, list):
        labels = metadata.get("approval_labels")
    capabilities = request_payload.get("capabilities")
    if not isinstance(capabilities, list):
        capabilities = metadata.get("approval_capabilities")
    return {
        "run_id": str(row_run_id or "").strip(),
        "step_id": str(row_step_id or approval_id).strip() or approval_id,
        "approval_id": approval_id,
        "status": _approval_status_token(row_status),
        "requested_at": str(row_requested_at or "").strip() or None,
        "resolved_at": str(row_resolved_at or "").strip() or None,
        "resolution": str(row_resolution or "").strip().lower() or None,
        "actor": str(row_actor or "").strip() or None,
        "trace_id": str(row_trace_id or "").strip() or None,
        "request_payload": request_payload,
        "decision_payload": decision_payload,
        "metadata": metadata,
        "expires_at": str(row_expires_at or "").strip() or None,
        "updated_at": str(row_updated_at or "").strip() or None,
        "version": int(row_version or 0),
        "workspace_id": workspace_id,
        "tenant_id": tenant_id,
        "owner_user_id": owner_user_id,
        "owner_email": owner_email,
        "prompt": prompt,
        "actions": [str(item or "").strip() for item in (actions or []) if str(item or "").strip()],
        "labels": [str(item or "").strip() for item in (labels or []) if str(item or "").strip()],
        "capabilities": [str(item or "").strip() for item in (capabilities or []) if str(item or "").strip()],
        "target": str(
            request_payload.get("target")
            or metadata.get("approval_target")
            or metadata.get("target")
            or ""
        ).strip() or None,
        "correlation_id": str(request_payload.get("correlation_id") or "").strip() or None,
        "scope": str(request_payload.get("scope") or "once").strip().lower() or "once",
        "reusable": bool(request_payload.get("reusable")),
        "consequence": str(request_payload.get("consequence") or "").strip() or None,
        "agent_role": str(
            request_payload.get("agent_role")
            or metadata.get("agent_role")
            or ""
        ).strip() or None,
        "email_preview": request_payload.get("email_preview")
        if isinstance(request_payload.get("email_preview"), dict)
        else metadata.get("email_preview")
        if isinstance(metadata.get("email_preview"), dict)
        else None,
    }


async def _fetch_approval_row(
    pool: Any,
    *,
    approval_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> Optional[Any]:
    approval_token = str(approval_id or "").strip()
    run_token = str(run_id or "").strip()
    if approval_token:
        return await pool.fetchrow(
            """
            SELECT run_id, step_id, approval_id, status, requested_at, resolved_at, resolution, actor, trace_id,
                   request_payload, decision_payload, metadata, expires_at, updated_at, version
            FROM run_approvals
            WHERE COALESCE(approval_id, step_id) = $1
            LIMIT 1
            """,
            approval_token,
        )
    if run_token:
        return await pool.fetchrow(
            """
            SELECT run_id, step_id, approval_id, status, requested_at, resolved_at, resolution, actor, trace_id,
                   request_payload, decision_payload, metadata, expires_at, updated_at, version
            FROM run_approvals
            WHERE run_id = $1
            ORDER BY requested_at DESC
            LIMIT 1
            """,
            run_token,
        )
    return None


async def _ensure_run_approval_table(pool: Any) -> None:
    await pool.execute(
        """
        CREATE TABLE IF NOT EXISTS run_approvals (
            id BIGSERIAL PRIMARY KEY,
            run_id TEXT NOT NULL,
            step_id TEXT NOT NULL,
            approval_id TEXT,
            status TEXT NOT NULL DEFAULT 'requested',
            requested_at TIMESTAMPTZ NOT NULL,
            resolved_at TIMESTAMPTZ,
            resolution TEXT,
            actor TEXT,
            trace_id TEXT,
            request_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            decision_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            expires_at TIMESTAMPTZ NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            version BIGINT NOT NULL DEFAULT 0
        )
        """
    )
    await pool.execute("ALTER TABLE run_approvals ADD COLUMN IF NOT EXISTS approval_id TEXT")
    await pool.execute("ALTER TABLE run_approvals ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'requested'")
    await pool.execute("ALTER TABLE run_approvals ADD COLUMN IF NOT EXISTS request_payload JSONB NOT NULL DEFAULT '{}'::jsonb")
    await pool.execute("ALTER TABLE run_approvals ADD COLUMN IF NOT EXISTS decision_payload JSONB NOT NULL DEFAULT '{}'::jsonb")
    await pool.execute("ALTER TABLE run_approvals ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb")
    await pool.execute("ALTER TABLE run_approvals ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ NULL")
    await pool.execute("ALTER TABLE run_approvals ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()")
    await pool.execute("ALTER TABLE run_approvals ADD COLUMN IF NOT EXISTS version BIGINT NOT NULL DEFAULT 0")
    await pool.execute("UPDATE run_approvals SET approval_id = step_id WHERE COALESCE(approval_id, '') = ''")
    await pool.execute(
        """
        UPDATE run_approvals
        SET status = CASE
            WHEN COALESCE(status, '') <> '' THEN LOWER(status)
            WHEN COALESCE(resolution, '') IN ('expired', 'cancelled', 'canceled', 'dismissed') THEN LOWER(resolution)
            WHEN resolved_at IS NOT NULL OR COALESCE(resolution, '') <> '' THEN 'resolved'
            ELSE 'requested'
        END
        """
    )
    await pool.execute("CREATE INDEX IF NOT EXISTS idx_run_approvals_run_id ON run_approvals(run_id, requested_at DESC)")
    await pool.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_run_approvals_approval_id ON run_approvals(approval_id)")
    await pool.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_run_approvals_run_step ON run_approvals(run_id, step_id)")
    await pool.execute("CREATE INDEX IF NOT EXISTS idx_run_approvals_status_requested ON run_approvals(status, requested_at DESC)")
    await pool.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_run_approvals_workspace_status_requested
        ON run_approvals ((COALESCE(request_payload->>'workspace_id', metadata->>'workspace_id', 'default')), status, requested_at DESC)
        """
    )


async def create_or_update_approval_request(
    run_id: str,
    approval_id: str,
    request_payload: Dict[str, Any],
    actor: str,
    trace_id: str,
    *,
    metadata: Optional[Dict[str, Any]] = None,
    expires_at: Optional[str] = None,
) -> Dict[str, Any]:
    run_token = str(run_id or "").strip()
    approval_token = str(approval_id or "").strip()
    if not run_token or not approval_token:
        raise RunStatePersistenceError("Postgres create_or_update_approval_request requires run_id and approval_id")
    request_item = _json_object(request_payload)
    request_item["approval_id"] = approval_token
    request_metadata = _json_object(metadata)
    if not request_metadata and isinstance(request_item.get("metadata"), dict):
        request_metadata = _json_object(request_item.get("metadata"))
    requested_at = _approval_request_timestamp(request_item)
    expires_at_token = _coerce_postgres_timestamptz(expires_at or request_item.get("expires_at"))
    _enforce_runtime_state_store_decision(
        operation="create_or_update_approval_request",
        run_id=run_token,
        workspace_id=str(request_item.get("workspace_id") or request_metadata.get("workspace_id") or "").strip() or "default",
        tenant_id=str(request_item.get("tenant_id") or request_metadata.get("tenant_id") or "").strip() or "default",
        status="requested",
        trace_id=str(trace_id or "").strip(),
        payload=request_item,
    )
    pool = await _require_pool(operation="create_or_update_approval_request")
    try:
        await _ensure_run_approval_table(pool)
        row = await pool.fetchrow(
            """
            INSERT INTO run_approvals (
                run_id,
                step_id,
                approval_id,
                status,
                requested_at,
                actor,
                trace_id,
                request_payload,
                decision_payload,
                metadata,
                expires_at,
                updated_at,
                version
            )
            VALUES (
                $1,
                $2,
                $2,
                'requested',
                COALESCE($3::timestamptz, NOW()),
                $4,
                $5,
                $6::jsonb,
                '{}'::jsonb,
                $7::jsonb,
                $8::timestamptz,
                NOW(),
                0
            )
            ON CONFLICT (run_id, step_id) DO UPDATE SET
                approval_id = COALESCE(NULLIF(EXCLUDED.approval_id, ''), run_approvals.approval_id, run_approvals.step_id),
                request_payload = CASE
                    WHEN run_approvals.status = 'requested' THEN EXCLUDED.request_payload
                    ELSE run_approvals.request_payload
                END,
                metadata = CASE
                    WHEN run_approvals.status = 'requested' THEN EXCLUDED.metadata
                    ELSE run_approvals.metadata
                END,
                expires_at = COALESCE(EXCLUDED.expires_at, run_approvals.expires_at),
                updated_at = NOW()
            RETURNING run_id, step_id, approval_id, status, requested_at, resolved_at, resolution, actor, trace_id,
                      request_payload, decision_payload, metadata, expires_at, updated_at, version
            """,
            run_token,
            approval_token,
            requested_at,
            str(actor or "").strip() or "system",
            str(trace_id or "").strip() or None,
            _json_payload(request_item),
            _json_payload(request_metadata),
            expires_at_token,
        )
    except Exception as exc:
        raise RunStatePersistenceError(
            f"Postgres create_or_update_approval_request failed for {run_token}/{approval_token}: {exc}"
        ) from exc
    if row is None:
        raise RunStatePersistenceError(
            f"Postgres create_or_update_approval_request returned no row for {run_token}/{approval_token}"
        )
    return _approval_record_from_row(row)


async def list_pending_approvals(limit: int = 100) -> list[Dict[str, Any]]:
    pool = await _read_pool(operation="list_pending_approvals")
    if pool is None:
        return []
    try:
        await _ensure_run_approval_table(pool)
        rows = await pool.fetch(
            """
            SELECT run_id, step_id, approval_id, status, requested_at, resolved_at, resolution, actor, trace_id,
                   request_payload, decision_payload, metadata, expires_at, updated_at, version
            FROM run_approvals
            WHERE status = 'requested'
            ORDER BY requested_at DESC, id DESC
            LIMIT $1
            """,
            max(1, min(int(limit or 0), 300)),
        )
    except Exception as exc:
        LOGGER.warning("Postgres list_pending_approvals failed: %s", exc)
        return []
    return [_approval_record_from_row(row) for row in (rows or [])]


async def list_pending_approvals_page(
    *,
    limit: int = 100,
    offset: int = 0,
    workspace_id: Optional[str] = None,
) -> list[Dict[str, Any]]:
    pool = await _read_pool(operation="list_pending_approvals_page")
    if pool is None:
        return []
    workspace_filter = str(workspace_id or "").strip()
    try:
        await _ensure_run_approval_table(pool)
        rows = await pool.fetch(
            """
            SELECT run_id, step_id, approval_id, status, requested_at, resolved_at, resolution, actor, trace_id,
                   request_payload, decision_payload, metadata, expires_at, updated_at, version
            FROM run_approvals
            WHERE status = 'requested'
              AND ($1 = '' OR COALESCE(request_payload->>'workspace_id', metadata->>'workspace_id', 'default') = $1)
            ORDER BY requested_at DESC, id DESC
            LIMIT $2
            OFFSET $3
            """,
            workspace_filter,
            max(1, min(int(limit or 0), 500)),
            max(0, int(offset or 0)),
        )
    except Exception as exc:
        LOGGER.warning("Postgres list_pending_approvals_page failed: %s", exc)
        return []
    return [_approval_record_from_row(row) for row in (rows or [])]


async def get_approval_record(approval_id: str) -> Optional[Dict[str, Any]]:
    approval_token = str(approval_id or "").strip()
    if not approval_token:
        return None
    pool = await _read_pool(operation="get_approval_record")
    if pool is None:
        return None
    try:
        await _ensure_run_approval_table(pool)
        row = await pool.fetchrow(
            """
            SELECT run_id, step_id, approval_id, status, requested_at, resolved_at, resolution, actor, trace_id,
                   request_payload, decision_payload, metadata, expires_at, updated_at, version
            FROM run_approvals
            WHERE COALESCE(approval_id, step_id) = $1
            LIMIT 1
            """,
            approval_token,
        )
    except Exception as exc:
        LOGGER.warning("Postgres get_approval_record failed for %s: %s", approval_token, exc)
        return None
    if row is None:
        return None
    return _approval_record_from_row(row)


async def find_run_snapshot_for_approval_id(approval_id: str) -> Optional[Dict[str, Any]]:
    approval_record = await get_approval_record(approval_id)
    if not isinstance(approval_record, dict):
        return None
    run_id = str(approval_record.get("run_id") or "").strip()
    if not run_id:
        return None
    live_run = await get_live_run(run_id)
    if isinstance(live_run, dict):
        return {
            "source": "live",
            "payload": live_run,
        }
    archived_run = await get_archived_run(run_id)
    if isinstance(archived_run, dict):
        return {
            "source": "archive",
            "payload": archived_run,
        }
    return {
        "source": "missing",
        "payload": {
            "run_id": run_id,
            "workspace_id": approval_record.get("workspace_id"),
            "tenant_id": approval_record.get("tenant_id"),
        },
    }


async def resolve_approval_if_pending(
    run_id: str,
    approval_id: str,
    resolution: str,
    actor: str,
    trace_id: str,
    *,
    note: Optional[str] = None,
    decision_payload: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    run_token = str(run_id or "").strip()
    approval_token = str(approval_id or "").strip()
    if not run_token or not approval_token:
        return None
    resolution_token = _approval_resolution_token(resolution)
    final_status = _approval_final_status(resolution_token)
    decision_item = _json_object(decision_payload)
    if note is not None:
        decision_item["note"] = str(note or "")
    decision_item.setdefault("resolution", resolution_token)
    decision_item.setdefault("decision", resolution_token)
    _enforce_runtime_state_store_decision(
        operation="resolve_approval_if_pending",
        run_id=run_token,
        workspace_id=str(decision_item.get("workspace_id") or "").strip() or "default",
        tenant_id=str(decision_item.get("tenant_id") or "").strip() or "default",
        status=final_status,
        previous_status="requested",
        trace_id=str(trace_id or "").strip(),
        payload=decision_item,
    )
    pool = await _require_pool(operation="resolve_approval_if_pending")
    try:
        await _ensure_run_approval_table(pool)
        row = await pool.fetchrow(
            """
            UPDATE run_approvals
            SET status = $3,
                resolved_at = NOW(),
                resolution = $4,
                actor = $5,
                trace_id = COALESCE(NULLIF($6, ''), trace_id),
                decision_payload = COALESCE(decision_payload, '{}'::jsonb) || $7::jsonb,
                updated_at = NOW(),
                version = version + 1
            WHERE run_id = $1
              AND COALESCE(approval_id, step_id) = $2
              AND status = 'requested'
            RETURNING run_id, step_id, approval_id, status, requested_at, resolved_at, resolution, actor, trace_id,
                      request_payload, decision_payload, metadata, expires_at, updated_at, version
            """,
            run_token,
            approval_token,
            final_status,
            resolution_token,
            str(actor or "").strip() or "system",
            str(trace_id or "").strip() or None,
            _json_payload(decision_item),
        )
        if row is not None:
            return _approval_record_from_row(row)
        existing = await pool.fetchrow(
            """
            SELECT run_id, step_id, approval_id, status, requested_at, resolved_at, resolution, actor, trace_id,
                   request_payload, decision_payload, metadata, expires_at, updated_at, version
            FROM run_approvals
            WHERE run_id = $1
              AND COALESCE(approval_id, step_id) = $2
            LIMIT 1
            """,
            run_token,
            approval_token,
        )
    except Exception as exc:
        raise RunStatePersistenceError(
            f"Postgres resolve_approval_if_pending failed for {run_token}/{approval_token}: {exc}"
        ) from exc
    if existing is None:
        return None
    existing_record = _approval_record_from_row(existing)
    if existing_record.get("resolution") == resolution_token:
        return None
    return None


async def record_approval_resolution(
    run_id: str,
    approval_id: str,
    resolution: str,
    actor: str,
    trace_id: str,
    note: Optional[str] = None,
) -> Dict[str, Any] | None:
    run_token = str(run_id or "").strip()
    approval_token = str(approval_id or "").strip()
    if not run_token or not approval_token:
        return None
    resolution_token = _approval_resolution_token(resolution)
    final_status = _approval_final_status(resolution_token)
    decision_item = {"resolution": resolution_token, "decision": resolution_token}
    if note is not None:
        decision_item["note"] = str(note or "")
    _enforce_runtime_state_store_decision(
        operation="record_approval_resolution",
        run_id=run_token,
        workspace_id=str(decision_item.get("workspace_id") or "").strip() or "default",
        tenant_id=str(decision_item.get("tenant_id") or "").strip() or "default",
        status=final_status,
        trace_id=str(trace_id or "").strip(),
        payload=decision_item,
    )
    pool = await _require_pool(operation="record_approval_resolution")
    try:
        await _ensure_run_approval_table(pool)
        row = await pool.fetchrow(
            """
            UPDATE run_approvals
            SET status = $3,
                resolved_at = COALESCE(resolved_at, NOW()),
                resolution = $4,
                actor = $5,
                trace_id = COALESCE(NULLIF($6, ''), trace_id),
                decision_payload = COALESCE(decision_payload, '{}'::jsonb) || $7::jsonb,
                updated_at = NOW(),
                version = CASE
                    WHEN status IN ('requested', 'decision_submitted') THEN version + 1
                    ELSE version
                END
            WHERE run_id = $1
              AND COALESCE(approval_id, step_id) = $2
              AND (
                  status NOT IN ('resolved', 'approved', 'rejected', 'expired', 'cancelled', 'canceled', 'dismissed')
                  OR (
                      status = $3
                      AND COALESCE(resolution, '') = $4
                  )
              )
            RETURNING run_id, step_id, approval_id, status, requested_at, resolved_at, resolution, actor, trace_id,
                      request_payload, decision_payload, metadata, expires_at, updated_at, version
            """,
            run_token,
            approval_token,
            final_status,
            resolution_token,
            str(actor or "").strip() or "system",
            str(trace_id or "").strip() or None,
            _json_payload(decision_item),
        )
        if row is None:
            existing = await pool.fetchrow(
                """
                SELECT run_id, step_id, approval_id, status, requested_at, resolved_at, resolution, actor, trace_id,
                       request_payload, decision_payload, metadata, expires_at, updated_at, version
                FROM run_approvals
                WHERE run_id = $1
                  AND COALESCE(approval_id, step_id) = $2
                LIMIT 1
                """,
                run_token,
                approval_token,
            )
            if existing is not None:
                existing_record = _approval_record_from_row(existing)
                if (
                    str(existing_record.get("status") or "").strip().lower() == final_status
                    and str(existing_record.get("resolution") or "").strip().lower() == resolution_token
                ):
                    return existing_record
                raise RunStateVersionConflictError(
                    f"Postgres record_approval_resolution refused conflicting final state for {run_token}/{approval_token}"
                )
            if row is None:
                row = await pool.fetchrow(
                    """
                    INSERT INTO run_approvals (
                        run_id,
                        step_id,
                        approval_id,
                        status,
                        requested_at,
                        resolved_at,
                        resolution,
                        actor,
                        trace_id,
                        request_payload,
                        decision_payload,
                        metadata,
                        expires_at,
                        updated_at,
                        version
                    )
                    VALUES (
                        $1,
                        $2,
                        $2,
                        $3,
                        NOW(),
                        NOW(),
                        $4,
                        $5,
                        $6,
                        '{}'::jsonb,
                        $7::jsonb,
                        '{}'::jsonb,
                        NULL,
                        NOW(),
                        0
                    )
                    ON CONFLICT (run_id, step_id) DO UPDATE SET
                        updated_at = NOW()
                    RETURNING run_id, step_id, approval_id, status, requested_at, resolved_at, resolution, actor, trace_id,
                              request_payload, decision_payload, metadata, expires_at, updated_at, version
                    """,
                    run_token,
                    approval_token,
                    final_status,
                    resolution_token,
                    str(actor or "").strip() or "system",
                    str(trace_id or "").strip() or None,
                    _json_payload(decision_item),
                )
    except Exception as exc:
        raise RunStatePersistenceError(
            f"Postgres record_approval_resolution failed for {run_token}/{approval_token}: {exc}"
        ) from exc
    return _approval_record_from_row(row) if row is not None else None


async def _ensure_runtime_outbox_table(pool: Any) -> None:
    await pool.execute(
        """
        CREATE TABLE IF NOT EXISTS runtime_outbox (
            event_id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            run_id TEXT NULL,
            machine_id TEXT NULL,
            trace_id TEXT NULL,
            idempotency_key TEXT NULL,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            delivered_at TIMESTAMPTZ NULL,
            last_replayed_at TIMESTAMPTZ NULL,
            retry_count INTEGER NOT NULL DEFAULT 0,
            last_delivery_error TEXT NULL,
            last_attempted_at TIMESTAMPTZ NULL,
            next_attempt_at TIMESTAMPTZ NULL,
            poisoned_at TIMESTAMPTZ NULL,
            claim_token TEXT NULL,
            claimed_by TEXT NULL,
            claimed_at TIMESTAMPTZ NULL,
            claim_expires_at TIMESTAMPTZ NULL
        )
        """
    )
    await pool.execute("ALTER TABLE runtime_outbox ADD COLUMN IF NOT EXISTS retry_count INTEGER NOT NULL DEFAULT 0")
    await pool.execute("ALTER TABLE runtime_outbox ADD COLUMN IF NOT EXISTS last_delivery_error TEXT NULL")
    await pool.execute("ALTER TABLE runtime_outbox ADD COLUMN IF NOT EXISTS last_attempted_at TIMESTAMPTZ NULL")
    await pool.execute("ALTER TABLE runtime_outbox ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMPTZ NULL")
    await pool.execute("ALTER TABLE runtime_outbox ADD COLUMN IF NOT EXISTS poisoned_at TIMESTAMPTZ NULL")
    await pool.execute("ALTER TABLE runtime_outbox ADD COLUMN IF NOT EXISTS claim_token TEXT NULL")
    await pool.execute("ALTER TABLE runtime_outbox ADD COLUMN IF NOT EXISTS claimed_by TEXT NULL")
    await pool.execute("ALTER TABLE runtime_outbox ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ NULL")
    await pool.execute("ALTER TABLE runtime_outbox ADD COLUMN IF NOT EXISTS claim_expires_at TIMESTAMPTZ NULL")
    await pool.execute(
        "CREATE INDEX IF NOT EXISTS idx_runtime_outbox_due ON runtime_outbox (delivered_at, poisoned_at, next_attempt_at, created_at)"
    )
    await pool.execute(
        "CREATE INDEX IF NOT EXISTS idx_runtime_outbox_claim_expiry ON runtime_outbox (claim_expires_at, created_at) WHERE delivered_at IS NULL"
    )


def _outbox_record_from_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    payload = row["payload"]
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return {
        "event_id": str(row["event_id"] or "").strip(),
        "event_type": str(row["event_type"] or "").strip(),
        "tenant_id": str(row["tenant_id"] or "").strip() or "default",
        "workspace_id": str(row["workspace_id"] or "").strip() or "default",
        "run_id": str(row["run_id"] or "").strip() or None,
        "machine_id": str(row["machine_id"] or "").strip() or None,
        "trace_id": str(row["trace_id"] or "").strip(),
        "idempotency_key": str(row["idempotency_key"] or "").strip(),
        "payload": payload,
        "created_at": str(row["created_at"] or "").strip() or None,
        "delivered_at": str(row["delivered_at"] or "").strip() or None,
        "last_replayed_at": str(row["last_replayed_at"] or "").strip() or None,
        "retry_count": int(row["retry_count"] or 0),
        "last_delivery_error": str(row["last_delivery_error"] or "").strip() or None,
        "last_attempted_at": str(row["last_attempted_at"] or "").strip() or None,
        "next_attempt_at": str(row["next_attempt_at"] or "").strip() or None,
        "poisoned_at": str(row["poisoned_at"] or "").strip() or None,
        "claim_token": str(row.get("claim_token") or "").strip() or None,
        "claimed_by": str(row.get("claimed_by") or "").strip() or None,
        "claimed_at": str(row.get("claimed_at") or "").strip() or None,
        "claim_expires_at": str(row.get("claim_expires_at") or "").strip() or None,
    }


async def persist_outbox_event(
    *,
    event_id: str,
    event_type: str,
    tenant_id: str,
    workspace_id: str,
    run_id: Optional[str],
    machine_id: Optional[str],
    trace_id: str,
    idempotency_key: str,
    payload: Dict[str, Any],
) -> None:
    token = str(event_id or "").strip()
    if not token:
        return None
    _enforce_outbox_delivery_decision(
        operation="persist_event",
        event_id=token,
        event_type=event_type,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        run_id=run_id,
        machine_id=machine_id,
    )
    pool = await _require_pool(operation="persist_outbox_event")
    try:
        await _ensure_runtime_outbox_table(pool)
        await pool.execute(
            """
            INSERT INTO runtime_outbox (
                event_id,
                event_type,
                tenant_id,
                workspace_id,
                run_id,
                machine_id,
                trace_id,
                idempotency_key,
                payload,
                created_at,
                delivered_at,
                last_replayed_at,
                retry_count,
                last_delivery_error,
                last_attempted_at,
                next_attempt_at,
                poisoned_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, NOW(), NULL, NULL, 0, NULL, NULL, NULL, NULL)
            ON CONFLICT (event_id) DO UPDATE SET
                event_type = EXCLUDED.event_type,
                tenant_id = EXCLUDED.tenant_id,
                workspace_id = EXCLUDED.workspace_id,
                run_id = EXCLUDED.run_id,
                machine_id = EXCLUDED.machine_id,
                trace_id = COALESCE(NULLIF(EXCLUDED.trace_id, ''), runtime_outbox.trace_id),
                idempotency_key = COALESCE(NULLIF(EXCLUDED.idempotency_key, ''), runtime_outbox.idempotency_key),
                payload = EXCLUDED.payload
            """,
            token,
            str(event_type or "").strip() or "runtime_event",
            str(tenant_id or "").strip() or "default",
            str(workspace_id or "").strip() or "default",
            str(run_id or "").strip() or None,
            str(machine_id or "").strip() or None,
            str(trace_id or "").strip() or None,
            str(idempotency_key or "").strip() or None,
            _json_payload(payload),
        )
    except Exception as exc:
        raise RunStatePersistenceError(f"Postgres persist_outbox_event failed for {token}: {exc}") from exc
    return None


async def list_undelivered_outbox_events(
    *,
    older_than_seconds: int = 30,
    limit: int = 200,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    run_id: Optional[str] = None,
    event_type: Optional[str] = None,
) -> list[Dict[str, Any]]:
    pool = await _read_pool(operation="list_undelivered_outbox_events")
    if pool is None:
        return []
    try:
        await _ensure_runtime_outbox_table(pool)
        rows = await pool.fetch(
            """
            SELECT
                event_id,
                event_type,
                tenant_id,
                workspace_id,
                run_id,
                machine_id,
                trace_id,
                idempotency_key,
                payload,
                created_at,
                delivered_at,
                last_replayed_at,
                retry_count,
                last_delivery_error,
                last_attempted_at,
                next_attempt_at,
                poisoned_at,
                claim_token,
                claimed_by,
                claimed_at,
                claim_expires_at
            FROM runtime_outbox
            WHERE delivered_at IS NULL
              AND poisoned_at IS NULL
              AND COALESCE(next_attempt_at, created_at) <= NOW() - ($1 * INTERVAL '1 second')
              AND ($3::text IS NULL OR tenant_id = $3)
              AND ($4::text IS NULL OR workspace_id = $4)
              AND ($5::text IS NULL OR run_id = $5)
              AND ($6::text IS NULL OR event_type = $6)
            ORDER BY COALESCE(next_attempt_at, created_at) ASC, created_at ASC
            LIMIT $2
            """,
            max(0, int(older_than_seconds or 0)),
            max(1, int(limit or 0)),
            str(tenant_id or "").strip() or None,
            str(workspace_id or "").strip() or None,
            str(run_id or "").strip() or None,
            str(event_type or "").strip() or None,
        )
    except Exception as exc:
        LOGGER.warning("Postgres list_undelivered_outbox_events failed: %s", exc)
        return []
    return [_outbox_record_from_row(row) for row in rows or []]


async def claim_due_outbox_events(
    *,
    older_than_seconds: int = 0,
    limit: int = 200,
    claimed_by: str,
    claim_ttl_seconds: int = 30,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    run_id: Optional[str] = None,
    event_type: Optional[str] = None,
) -> list[Dict[str, Any]]:
    claimed_by_token = str(claimed_by or "").strip() or "outbox-delivery"
    _enforce_outbox_delivery_decision(
        operation="claim_due",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        run_id=run_id,
        event_type=event_type,
        claimed_by=claimed_by_token,
        limit=limit,
        claim_ttl_seconds=claim_ttl_seconds,
    )
    pool = await _read_pool(operation="claim_due_outbox_events")
    if pool is None:
        return []
    try:
        await _ensure_runtime_outbox_table(pool)
        rows = await pool.fetch(
            """
            WITH due AS (
                SELECT event_id
                FROM runtime_outbox
                WHERE delivered_at IS NULL
                  AND poisoned_at IS NULL
                  AND COALESCE(next_attempt_at, created_at) <= NOW() - ($1 * INTERVAL '1 second')
                  AND (
                    claim_expires_at IS NULL
                    OR claim_expires_at <= NOW()
                  )
                  AND ($5::text IS NULL OR tenant_id = $5)
                  AND ($6::text IS NULL OR workspace_id = $6)
                  AND ($7::text IS NULL OR run_id = $7)
                  AND ($8::text IS NULL OR event_type = $8)
                ORDER BY COALESCE(next_attempt_at, created_at) ASC, created_at ASC
                LIMIT $2
                FOR UPDATE SKIP LOCKED
            )
            UPDATE runtime_outbox AS outbox
            SET claim_token = CONCAT(
                    outbox.event_id,
                    ':',
                    SUBSTRING(MD5(outbox.event_id || CLOCK_TIMESTAMP()::text || RANDOM()::text) FROM 1 FOR 24)
                ),
                claimed_by = $3,
                claimed_at = NOW(),
                claim_expires_at = NOW() + ($4 * INTERVAL '1 second')
            FROM due
            WHERE outbox.event_id = due.event_id
            RETURNING
                outbox.event_id,
                outbox.event_type,
                outbox.tenant_id,
                outbox.workspace_id,
                outbox.run_id,
                outbox.machine_id,
                outbox.trace_id,
                outbox.idempotency_key,
                outbox.payload,
                outbox.created_at,
                outbox.delivered_at,
                outbox.last_replayed_at,
                outbox.retry_count,
                outbox.last_delivery_error,
                outbox.last_attempted_at,
                outbox.next_attempt_at,
                outbox.poisoned_at,
                outbox.claim_token,
                outbox.claimed_by,
                outbox.claimed_at,
                outbox.claim_expires_at
            """,
            max(0, int(older_than_seconds or 0)),
            max(1, int(limit or 0)),
            claimed_by_token,
            max(1, int(claim_ttl_seconds or 0)),
            str(tenant_id or "").strip() or None,
            str(workspace_id or "").strip() or None,
            str(run_id or "").strip() or None,
            str(event_type or "").strip() or None,
        )
    except Exception as exc:
        LOGGER.warning("Postgres claim_due_outbox_events failed: %s", exc)
        return []
    return [_outbox_record_from_row(row) for row in rows or []]


async def patch_outbox_event_payload(event_id: str, payload_patch: Dict[str, Any]) -> None:
    token = str(event_id or "").strip()
    patch = dict(payload_patch or {}) if isinstance(payload_patch, dict) else {}
    if not token or not patch:
        return None
    _enforce_outbox_delivery_decision(
        operation="patch_payload",
        event_id=token,
        payload_patch_present=True,
    )
    pool = await _require_pool(operation="patch_outbox_event_payload")
    try:
        await _ensure_runtime_outbox_table(pool)
        await pool.execute(
            """
            UPDATE runtime_outbox
            SET payload = COALESCE(runtime_outbox.payload, '{}'::jsonb) || $2::jsonb,
                last_replayed_at = NOW()
            WHERE event_id = $1
            """,
            token,
            _json_payload(patch),
        )
    except Exception as exc:
        raise RunStatePersistenceError(f"Postgres patch_outbox_event_payload failed for {token}: {exc}") from exc
    return None


async def mark_outbox_event_delivered(event_id: str, *, claim_token: str) -> bool:
    token = str(event_id or "").strip()
    claim = str(claim_token or "").strip()
    if not token or not claim:
        return False
    _enforce_outbox_delivery_decision(
        operation="mark_delivered",
        event_id=token,
        claim_token=claim,
    )
    pool = await _require_pool(operation="mark_outbox_event_delivered")
    try:
        await _ensure_runtime_outbox_table(pool)
        row = await pool.fetchrow(
            """
            UPDATE runtime_outbox
            SET delivered_at = NOW(),
                last_replayed_at = NOW(),
                last_attempted_at = NOW(),
                last_delivery_error = NULL,
                next_attempt_at = NULL,
                poisoned_at = NULL,
                claim_token = NULL,
                claimed_by = NULL,
                claimed_at = NULL,
                claim_expires_at = NULL
            WHERE event_id = $1
              AND claim_token = $2
              AND delivered_at IS NULL
            RETURNING event_id
            """,
            token,
            claim,
        )
    except Exception as exc:
        raise RunStatePersistenceError(f"Postgres mark_outbox_event_delivered failed for {token}: {exc}") from exc
    return row is not None


async def record_outbox_delivery_failure(
    event_id: str,
    *,
    claim_token: str,
    error_text: str,
    retry_delay_seconds: Optional[int],
    poison: bool = False,
    increment_retry: bool = True,
) -> bool:
    token = str(event_id or "").strip()
    claim = str(claim_token or "").strip()
    if not token or not claim:
        return False
    _enforce_outbox_delivery_decision(
        operation="record_failure",
        event_id=token,
        claim_token=claim,
        error_text=error_text,
        retry_delay_seconds=retry_delay_seconds,
        poison=poison,
    )
    pool = await _require_pool(operation="record_outbox_delivery_failure")
    try:
        await _ensure_runtime_outbox_table(pool)
        status = await pool.execute(
            """
            UPDATE runtime_outbox
            SET retry_count = COALESCE(retry_count, 0) + CASE WHEN $5::boolean THEN 1 ELSE 0 END,
                last_delivery_error = LEFT($2, 2000),
                last_attempted_at = NOW(),
                last_replayed_at = NOW(),
                next_attempt_at = CASE
                    WHEN $3::boolean THEN NULL
                    WHEN $4::integer IS NULL THEN NULL
                    ELSE NOW() + ($4::text || ' seconds')::interval
                END,
                poisoned_at = CASE WHEN $3::boolean THEN NOW() ELSE NULL END,
                claim_token = NULL,
                claimed_by = NULL,
                claimed_at = NULL,
                claim_expires_at = NULL
            WHERE event_id = $1
              AND claim_token = $6
              AND delivered_at IS NULL
            RETURNING event_id
            """,
            token,
            str(error_text or "").strip() or "outbox_delivery_failed",
            bool(poison),
            (None if retry_delay_seconds is None else max(0, int(retry_delay_seconds))),
            bool(increment_retry),
            claim,
        )
        row = await pool.fetchrow(
            """
            SELECT event_id
            FROM runtime_outbox
            WHERE event_id = $1
            """,
            token,
        )
    except Exception as exc:
        raise RunStatePersistenceError(f"Postgres record_outbox_delivery_failure failed for {token}: {exc}") from exc
    return row is not None or str(status or "").strip().upper().endswith("1")


async def list_poisoned_outbox_events(
    *,
    limit: int = 200,
) -> list[Dict[str, Any]]:
    pool = await _read_pool(operation="list_poisoned_outbox_events")
    if pool is None:
        return []
    try:
        await _ensure_runtime_outbox_table(pool)
        rows = await pool.fetch(
            """
            SELECT
                event_id,
                event_type,
                tenant_id,
                workspace_id,
                run_id,
                machine_id,
                trace_id,
                idempotency_key,
                payload,
                created_at,
                delivered_at,
                last_replayed_at,
                retry_count,
                last_delivery_error,
                last_attempted_at,
                next_attempt_at,
                poisoned_at,
                claim_token,
                claimed_by,
                claimed_at,
                claim_expires_at
            FROM runtime_outbox
            WHERE delivered_at IS NULL
              AND poisoned_at IS NOT NULL
            ORDER BY poisoned_at DESC, created_at DESC
            LIMIT $1
            """,
            max(1, int(limit or 0)),
        )
    except Exception as exc:
        LOGGER.warning("Postgres list_poisoned_outbox_events failed: %s", exc)
        return []
    return [_outbox_record_from_row(row) for row in rows or []]


async def get_outbox_delivery_status() -> Dict[str, Any]:
    pool = await _read_pool(operation="get_outbox_delivery_status")
    if pool is None:
        return {
            "undelivered_count": 0,
            "poisoned_count": 0,
            "claimed_count": 0,
            "repeated_failure_count": 0,
            "stuck_count": 0,
            "total_retry_count": 0,
            "max_retry_count": 0,
            "last_delivery_error": None,
        }
    try:
        await _ensure_runtime_outbox_table(pool)
        summary_row = await pool.fetchrow(
            """
            SELECT
                COUNT(*) FILTER (WHERE delivered_at IS NULL AND poisoned_at IS NULL) AS undelivered_count,
                COUNT(*) FILTER (WHERE delivered_at IS NULL AND poisoned_at IS NOT NULL) AS poisoned_count,
                COUNT(*) FILTER (
                    WHERE delivered_at IS NULL
                      AND poisoned_at IS NULL
                      AND claim_expires_at IS NOT NULL
                      AND claim_expires_at > NOW()
                ) AS claimed_count,
                COUNT(*) FILTER (
                    WHERE delivered_at IS NULL
                      AND retry_count >= 3
                ) AS repeated_failure_count,
                COUNT(*) FILTER (
                    WHERE delivered_at IS NULL
                      AND poisoned_at IS NULL
                      AND COALESCE(next_attempt_at, last_attempted_at, created_at) <= NOW() - INTERVAL '60 seconds'
                ) AS stuck_count,
                COALESCE(SUM(retry_count), 0) AS total_retry_count,
                COALESCE(MAX(retry_count), 0) AS max_retry_count
            FROM runtime_outbox
            """
        )
        error_row = await pool.fetchrow(
            """
            SELECT event_id, last_delivery_error, last_attempted_at, retry_count
            FROM runtime_outbox
            WHERE COALESCE(last_delivery_error, '') <> ''
            ORDER BY last_attempted_at DESC NULLS LAST, created_at DESC
            LIMIT 1
            """
        )
    except Exception as exc:
        LOGGER.warning("Postgres get_outbox_delivery_status failed: %s", exc)
        return {
            "undelivered_count": 0,
            "poisoned_count": 0,
            "claimed_count": 0,
            "repeated_failure_count": 0,
            "stuck_count": 0,
            "total_retry_count": 0,
            "max_retry_count": 0,
            "last_delivery_error": None,
        }
    return {
        "undelivered_count": int(summary_row["undelivered_count"] if summary_row is not None else 0),
        "poisoned_count": int(summary_row["poisoned_count"] if summary_row is not None else 0),
        "claimed_count": int(summary_row["claimed_count"] if summary_row is not None else 0),
        "repeated_failure_count": int(summary_row["repeated_failure_count"] if summary_row is not None else 0),
        "stuck_count": int(summary_row["stuck_count"] if summary_row is not None else 0),
        "total_retry_count": int(summary_row["total_retry_count"] if summary_row is not None else 0),
        "max_retry_count": int(summary_row["max_retry_count"] if summary_row is not None else 0),
        "last_delivery_error": (
            {
                "event_id": str(error_row["event_id"] or "").strip(),
                "message": str(error_row["last_delivery_error"] or "").strip(),
                "last_attempted_at": str(error_row["last_attempted_at"] or "").strip() or None,
                "retry_count": int(error_row["retry_count"] or 0),
            }
            if error_row is not None
            else None
        ),
    }


async def list_expired_local_claims() -> list[Dict[str, Any]]:
    pool = await _read_pool(operation="list_expired_local_claims")
    if pool is None:
        return []
    try:
        await _ensure_local_queue_tables(pool)
        rows = await pool.fetch(
            """
            SELECT
                claims.run_id,
                claims.worker_id,
                claims.lease_id,
                claims.claimed_at,
                claims.last_heartbeat_at,
                claims.last_progress_at,
                claims.ttl,
                claims.trace_id,
                live_runs.payload AS run_payload
            FROM local_queue_claims AS claims
            LEFT JOIN live_runs ON live_runs.run_id = claims.run_id
            WHERE COALESCE(claims.last_heartbeat_at, claims.claimed_at) +
                    (GREATEST(COALESCE(claims.ttl, 0), 1) * INTERVAL '1 second') <= NOW()
            ORDER BY COALESCE(claims.last_heartbeat_at, claims.claimed_at) ASC
            """
        )
    except Exception as exc:
        LOGGER.warning("Postgres list_expired_local_claims failed: %s", exc)
        return []
    items: list[Dict[str, Any]] = []
    for row in rows or []:
        payload = row["run_payload"]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = None
        items.append(
            {
                "run_id": str(row["run_id"] or "").strip() or None,
                "worker_id": str(row["worker_id"] or "").strip() or None,
                "lease_id": str(row["lease_id"] or "").strip() or None,
                "claimed_at": str(row["claimed_at"] or "").strip() or None,
                "last_heartbeat_at": str(row["last_heartbeat_at"] or "").strip() or None,
                "last_progress_at": str(row["last_progress_at"] or "").strip() or None,
                "ttl": int(row["ttl"] or 0),
                "trace_id": str(row["trace_id"] or "").strip() or None,
                "run_payload": payload if isinstance(payload, dict) else None,
            }
        )
    return items


def sync_upsert_live_run(
    run_id: str,
    workspace_id: str,
    tenant_id: str,
    state: str,
    payload: Dict[str, Any],
    trace_id: str,
) -> None:
    _run_sync(
        lambda: upsert_live_run(run_id, workspace_id, tenant_id, state, payload, trace_id),
        operation="sync_upsert_live_run",
        fallback=None,
        raise_on_error=True,
    )


def sync_create_live_run_initial(
    run_id: str,
    workspace_id: str,
    tenant_id: str,
    state: str,
    payload: Dict[str, Any],
    trace_id: str,
) -> Dict[str, Any]:
    return _run_sync(
        lambda: create_live_run_initial(run_id, workspace_id, tenant_id, state, payload, trace_id),
        operation="sync_create_live_run_initial",
        fallback={},
        raise_on_error=True,
    )


def sync_update_live_run_if_version_matches(
    run_id: str,
    workspace_id: str,
    tenant_id: str,
    state: str,
    payload: Dict[str, Any],
    trace_id: str,
    *,
    expected_version: int,
) -> Optional[int]:
    return _run_sync(
        lambda: update_live_run_if_version_matches(
            run_id,
            workspace_id,
            tenant_id,
            state,
            payload,
            trace_id,
            expected_version=expected_version,
        ),
        operation="sync_update_live_run_if_version_matches",
        fallback=None,
        raise_on_error=True,
    )


def sync_delete_live_run(run_id: str) -> None:
    _run_sync(
        lambda: delete_live_run(run_id),
        operation="sync_delete_live_run",
        fallback=None,
        raise_on_error=True,
    )


def sync_archive_run(run_id: str, final_state: str, payload: Dict[str, Any], trace_id: str) -> None:
    _run_sync(
        lambda: archive_run(run_id, final_state, payload, trace_id),
        operation="sync_archive_run",
        fallback=None,
        raise_on_error=True,
    )


def sync_list_live_runs() -> list[Dict[str, Any]]:
    return _run_sync(
        lambda: list_live_runs(),
        operation="sync_list_live_runs",
        fallback=[],
        raise_on_error=_sync_raise_on_read_failure(),
    )


def sync_list_live_runs_page(
    *,
    limit: int = 100,
    offset: int = 0,
    workspace_id: Optional[str] = None,
    states: Optional[list[str]] = None,
) -> list[Dict[str, Any]]:
    return _run_sync(
        lambda: list_live_runs_page(limit=limit, offset=offset, workspace_id=workspace_id, states=states),
        operation="sync_list_live_runs_page",
        fallback=[],
        raise_on_error=_sync_raise_on_read_failure(),
    )


def sync_count_live_runs(
    *,
    workspace_id: Optional[str] = None,
    states: Optional[list[str]] = None,
) -> int:
    return _run_sync(
        lambda: count_live_runs(workspace_id=workspace_id, states=states),
        operation="sync_count_live_runs",
        fallback=0,
        raise_on_error=_sync_raise_on_read_failure(),
    )


def sync_count_hosted_live_runs(
    workspace_id: str,
    *,
    terminal_states: Optional[list[str]] = None,
) -> int:
    return _run_sync(
        lambda: count_hosted_live_runs(workspace_id, terminal_states=terminal_states),
        operation="sync_count_hosted_live_runs",
        fallback=0,
        raise_on_error=_sync_raise_on_read_failure(),
    )


def sync_get_live_run(run_id: str) -> Optional[Dict[str, Any]]:
    return _run_sync(
        lambda: get_live_run(run_id),
        operation="sync_get_live_run",
        fallback=None,
        raise_on_error=_sync_raise_on_read_failure(),
    )


def sync_get_archived_run(run_id: str) -> Optional[Dict[str, Any]]:
    return _run_sync(
        lambda: get_archived_run(run_id),
        operation="sync_get_archived_run",
        fallback=None,
        raise_on_error=_sync_raise_on_read_failure(),
    )


def sync_list_live_runs_by_state(states: list[str]) -> list[Dict[str, Any]]:
    return _run_sync(
        lambda: list_live_runs_by_state(states),
        operation="sync_list_live_runs_by_state",
        fallback=[],
        raise_on_error=_sync_raise_on_read_failure(),
    )


def sync_list_run_archive(limit: int = 200) -> list[Dict[str, Any]]:
    return _run_sync(
        lambda: list_run_archive(limit),
        operation="sync_list_run_archive",
        fallback=[],
        raise_on_error=_sync_raise_on_read_failure(),
    )


def sync_find_live_run_by_approval_id(approval_id: str) -> Optional[Dict[str, Any]]:
    return _run_sync(
        lambda: find_live_run_by_approval_id(approval_id),
        operation="sync_find_live_run_by_approval_id",
        fallback=None,
        raise_on_error=_sync_raise_on_read_failure(),
    )


def sync_create_or_update_approval_request(
    run_id: str,
    approval_id: str,
    request_payload: Dict[str, Any],
    actor: str,
    trace_id: str,
    *,
    metadata: Optional[Dict[str, Any]] = None,
    expires_at: Optional[str] = None,
) -> Dict[str, Any]:
    return _run_sync(
        lambda: create_or_update_approval_request(
            run_id,
            approval_id,
            request_payload,
            actor,
            trace_id,
            metadata=metadata,
            expires_at=expires_at,
        ),
        operation="sync_create_or_update_approval_request",
        fallback={},
        raise_on_error=True,
    )


def sync_list_pending_approvals(limit: int = 100) -> list[Dict[str, Any]]:
    return _run_sync(
        lambda: list_pending_approvals(limit),
        operation="sync_list_pending_approvals",
        fallback=[],
        raise_on_error=_sync_raise_on_read_failure(),
    )


def sync_list_pending_approvals_page(
    *,
    limit: int = 100,
    offset: int = 0,
    workspace_id: Optional[str] = None,
) -> list[Dict[str, Any]]:
    return _run_sync(
        lambda: list_pending_approvals_page(limit=limit, offset=offset, workspace_id=workspace_id),
        operation="sync_list_pending_approvals_page",
        fallback=[],
        raise_on_error=_sync_raise_on_read_failure(),
    )


def sync_get_approval_record(approval_id: str) -> Optional[Dict[str, Any]]:
    return _run_sync(
        lambda: get_approval_record(approval_id),
        operation="sync_get_approval_record",
        fallback=None,
        raise_on_error=_sync_raise_on_read_failure(),
    )


def sync_find_run_snapshot_for_approval_id(approval_id: str) -> Optional[Dict[str, Any]]:
    return _run_sync(
        lambda: find_run_snapshot_for_approval_id(approval_id),
        operation="sync_find_run_snapshot_for_approval_id",
        fallback=None,
        raise_on_error=_sync_raise_on_read_failure(),
    )


def sync_resolve_approval_if_pending(
    run_id: str,
    approval_id: str,
    resolution: str,
    actor: str,
    trace_id: str,
    *,
    note: Optional[str] = None,
    decision_payload: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    return _run_sync(
        lambda: resolve_approval_if_pending(
            run_id,
            approval_id,
            resolution,
            actor,
            trace_id,
            note=note,
            decision_payload=decision_payload,
        ),
        operation="sync_resolve_approval_if_pending",
        fallback=None,
        raise_on_error=True,
    )


def sync_record_transition(
    run_id: str,
    from_state: str,
    to_state: str,
    actor: str,
    trace_id: str,
) -> None:
    _run_sync(
        lambda: record_transition(run_id, from_state, to_state, actor, trace_id),
        operation="sync_record_transition",
        fallback=None,
        raise_on_error=True,
    )


def sync_record_approval_resolution(
    run_id: str,
    approval_id: str,
    resolution: str,
    actor: str,
    trace_id: str,
    note: Optional[str] = None,
) -> Dict[str, Any] | None:
    return _run_sync(
        lambda: record_approval_resolution(run_id, approval_id, resolution, actor, trace_id, note),
        operation="sync_record_approval_resolution",
        fallback=None,
        raise_on_error=True,
    )


def sync_persist_outbox_event(
    *,
    event_id: str,
    event_type: str,
    tenant_id: str,
    workspace_id: str,
    run_id: Optional[str],
    machine_id: Optional[str],
    trace_id: str,
    idempotency_key: str,
    payload: Dict[str, Any],
) -> None:
    _run_sync(
        lambda: persist_outbox_event(
            event_id=event_id,
            event_type=event_type,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            run_id=run_id,
            machine_id=machine_id,
            trace_id=trace_id,
            idempotency_key=idempotency_key,
            payload=payload,
        ),
        operation="sync_persist_outbox_event",
        fallback=None,
        raise_on_error=True,
    )


def sync_list_undelivered_outbox_events(
    *,
    older_than_seconds: int = 30,
    limit: int = 200,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    run_id: Optional[str] = None,
    event_type: Optional[str] = None,
) -> list[Dict[str, Any]]:
    return _run_sync(
        lambda: list_undelivered_outbox_events(
            older_than_seconds=older_than_seconds,
            limit=limit,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            run_id=run_id,
            event_type=event_type,
        ),
        operation="sync_list_undelivered_outbox_events",
        fallback=[],
        raise_on_error=_sync_raise_on_read_failure(),
    )


def sync_claim_due_outbox_events(
    *,
    older_than_seconds: int = 0,
    limit: int = 200,
    claimed_by: str,
    claim_ttl_seconds: int = 30,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    run_id: Optional[str] = None,
    event_type: Optional[str] = None,
) -> list[Dict[str, Any]]:
    return _run_sync(
        lambda: claim_due_outbox_events(
            older_than_seconds=older_than_seconds,
            limit=limit,
            claimed_by=claimed_by,
            claim_ttl_seconds=claim_ttl_seconds,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            run_id=run_id,
            event_type=event_type,
        ),
        operation="sync_claim_due_outbox_events",
        fallback=[],
        raise_on_error=_sync_raise_on_read_failure(),
    )


def sync_mark_outbox_event_delivered(event_id: str, *, claim_token: str) -> bool:
    return _run_sync(
        lambda: mark_outbox_event_delivered(event_id, claim_token=claim_token),
        operation="sync_mark_outbox_event_delivered",
        fallback=False,
        raise_on_error=True,
    )


def sync_patch_outbox_event_payload(event_id: str, payload_patch: Dict[str, Any]) -> None:
    _run_sync(
        lambda: patch_outbox_event_payload(event_id, payload_patch),
        operation="sync_patch_outbox_event_payload",
        fallback=None,
        raise_on_error=True,
    )


def sync_record_outbox_delivery_failure(
    event_id: str,
    *,
    claim_token: str,
    error_text: str,
    retry_delay_seconds: Optional[int],
    poison: bool = False,
    increment_retry: bool = True,
) -> bool:
    return _run_sync(
        lambda: record_outbox_delivery_failure(
            event_id,
            claim_token=claim_token,
            error_text=error_text,
            retry_delay_seconds=retry_delay_seconds,
            poison=poison,
            increment_retry=increment_retry,
        ),
        operation="sync_record_outbox_delivery_failure",
        fallback=False,
        raise_on_error=True,
    )


def sync_list_poisoned_outbox_events(*, limit: int = 200) -> list[Dict[str, Any]]:
    return _run_sync(
        lambda: list_poisoned_outbox_events(limit=limit),
        operation="sync_list_poisoned_outbox_events",
        fallback=[],
        raise_on_error=_sync_raise_on_read_failure(),
    )


def sync_get_outbox_delivery_status() -> Dict[str, Any]:
    return _run_sync(
        get_outbox_delivery_status,
        operation="sync_get_outbox_delivery_status",
        fallback={
            "undelivered_count": 0,
            "poisoned_count": 0,
            "claimed_count": 0,
            "repeated_failure_count": 0,
            "stuck_count": 0,
            "total_retry_count": 0,
            "max_retry_count": 0,
            "last_delivery_error": None,
        },
        raise_on_error=_sync_raise_on_read_failure(),
    )


def sync_list_expired_local_claims() -> list[Dict[str, Any]]:
    return _run_sync(
        lambda: list_expired_local_claims(),
        operation="sync_list_expired_local_claims",
        fallback=[],
        raise_on_error=_sync_raise_on_read_failure(),
    )


def sync_release_claim(run_id: str, *, lease_id: Optional[str] = None) -> bool:
    return _run_sync(
        lambda: release_claim(run_id, lease_id=lease_id),
        operation="sync_release_claim",
        fallback=False,
        raise_on_error=True,
    )


def sync_touch_claim_heartbeat(
    run_id: str,
    worker_id: str,
    *,
    lease_id: Optional[str] = None,
    note: Optional[str] = None,
    progress: bool = False,
) -> bool:
    return _run_sync(
        lambda: touch_claim_heartbeat(run_id, worker_id, lease_id=lease_id, note=note, progress=progress),
        operation="sync_touch_claim_heartbeat",
        fallback=False,
        raise_on_error=True,
    )


def sync_append_local_queue_dead_letter(
    *,
    run_id: str,
    tenant_id: str,
    workspace_id: str,
    specialist_key: Optional[str],
    reason: str,
    trace_id: str,
    failure_count: int,
    payload: Dict[str, Any],
) -> None:
    _run_sync(
        lambda: append_local_queue_dead_letter(
            run_id=run_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            specialist_key=specialist_key,
            reason=reason,
            trace_id=trace_id,
            failure_count=failure_count,
            payload=payload,
        ),
        operation="sync_append_local_queue_dead_letter",
        fallback=None,
        raise_on_error=True,
    )


def sync_get_local_queue_dead_letter_status() -> Dict[str, Any]:
    return _run_sync(
        get_local_queue_dead_letter_status,
        operation="sync_get_local_queue_dead_letter_status",
        fallback={
            "dead_letter_count": 0,
            "total_failure_count": 0,
            "last_recorded_at": None,
            "workspace_hotspots": [],
            "specialist_hotspots": [],
        },
        raise_on_error=_sync_raise_on_read_failure(),
    )


def sync_upsert_fleet_worker(record: Dict[str, Any], *, heartbeat_seen: bool = True) -> None:
    _run_sync(
        lambda: upsert_fleet_worker(record, heartbeat_seen=heartbeat_seen),
        operation="sync_upsert_fleet_worker",
        fallback=None,
        raise_on_error=True,
    )


def sync_get_fleet_worker(worker_id: str) -> Optional[Dict[str, Any]]:
    return _run_sync(
        lambda: get_fleet_worker(worker_id),
        operation="sync_get_fleet_worker",
        fallback=None,
        raise_on_error=_sync_raise_on_read_failure(),
    )


def sync_list_fleet_workers(
    *,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
) -> list[Dict[str, Any]]:
    return _run_sync(
        lambda: list_fleet_workers(tenant_id=tenant_id, workspace_id=workspace_id),
        operation="sync_list_fleet_workers",
        fallback=[],
        raise_on_error=_sync_raise_on_read_failure(),
    )


def sync_upsert_fleet_queue_partition(
    *,
    partition_id: str,
    tenant_id: str,
    workspace_id: str,
    specialist_key: str,
    pending_count: int,
    claimed_count: int,
    online_workers: int,
    busy_workers: int,
    idle_workers: int,
    prewarmed_workers: int,
    state: str,
    retry_after_seconds: int,
    payload: Dict[str, Any],
) -> None:
    _run_sync(
        lambda: upsert_fleet_queue_partition(
            partition_id=partition_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            specialist_key=specialist_key,
            pending_count=pending_count,
            claimed_count=claimed_count,
            online_workers=online_workers,
            busy_workers=busy_workers,
            idle_workers=idle_workers,
            prewarmed_workers=prewarmed_workers,
            state=state,
            retry_after_seconds=retry_after_seconds,
            payload=payload,
        ),
        operation="sync_upsert_fleet_queue_partition",
        fallback=None,
        raise_on_error=True,
    )


def sync_list_fleet_queue_partitions(
    *,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
) -> list[Dict[str, Any]]:
    return _run_sync(
        lambda: list_fleet_queue_partitions(tenant_id=tenant_id, workspace_id=workspace_id),
        operation="sync_list_fleet_queue_partitions",
        fallback=[],
        raise_on_error=_sync_raise_on_read_failure(),
    )
