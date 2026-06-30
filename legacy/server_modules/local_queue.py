"""
Local worker queue and heartbeat logic.
Extracted from server.py to reduce hotspot size.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field
from server_modules import machine_lease_service, outbox_service, run_state_repository, rust_runtime_kernel_client, safe_mode_service, worker_dispatch_service
from server_modules import browser_checkpoint_service
from server_modules.telemetry import mark_machine_revocation_requested, observe_machine_revocation_propagated

_server = None
LOCAL_RUN_STILL_WORKING_INTERVAL_SECONDS = 15
LOCAL_RUN_WORKER_LOST_TIMEOUT_SECONDS = 30
LOCAL_RUNTIME_WATCHDOG_INTERVAL_SECONDS = 5
LOCAL_CHECKPOINT_RECOVERY_MAX_AUTO_RETRIES = 3
LOCAL_CHECKPOINT_RECOVERY_BACKOFF_SECONDS = [0, 10, 30]
LOCAL_QUEUE_MAX_AUTO_RETRIES = 3
LOCAL_QUEUE_RETRY_BACKOFF_SECONDS = [0, 10, 30]
LOCAL_QUEUE_STALE_UNCLAIMED_SECONDS = 600
LOCAL_QUEUE_STALE_CLAIM_CLEANUP_INTERVAL_SECONDS = 5
LOCAL_WORKER_STATE_PERSIST_INTERVAL_SECONDS = 10
LOCAL_WORKER_DURABLE_SYNC_INTERVAL_SECONDS = 15
MACHINE_ENROLLMENT_TIMEOUT_SECONDS = 300
_COLD_BOOT_RECOVERY_DONE = False
_EXPIRED_LEASE_RECOVERY_DONE = False
_LOCAL_RUNTIME_WATCHDOG_LOCK = threading.Lock()
_LOCAL_CLAIM_CLEANUP_LOCK = threading.Lock()
_LOCAL_CLAIM_CLEANUP_NEXT_AT_MONOTONIC = 0.0
_LOCAL_RUNTIME_WATCHDOG_STATE: Dict[str, Any] = {
    "running": False,
    "interval_seconds": LOCAL_RUNTIME_WATCHDOG_INTERVAL_SECONDS,
    "last_checked_at": None,
    "last_status": "idle",
    "last_summary": "Local runtime watchdog not started yet.",
    "last_cleaned_count": 0,
    "last_cleaned_run_ids": [],
    "last_resumed_count": 0,
    "last_resumed_run_ids": [],
}
_RUNTIME_CONTROL_STREAM_BACKLOG_LIMIT = 32
_RUNTIME_CONTROL_STREAM_LOCK = threading.Lock()
_RUNTIME_CONTROL_STREAM_CONDITION = threading.Condition(_RUNTIME_CONTROL_STREAM_LOCK)
_RUNTIME_CONTROL_STREAM_SEQUENCE = 0
_RUNTIME_CONTROL_STREAM_EVENTS: Dict[str, List[Dict[str, Any]]] = {}
_RUNTIME_CONTROL_STREAM_ASYNC_SUBSCRIBERS: Dict[str, List[Dict[str, Any]]] = {}
_TRANSIENT_WORKER_NOTES = {
    "",
    "idle",
    "idle_poll",
    "idle_capability_wait",
    "runtime_registered",
    "runtime_session_resume",
}
_DURABLE_RUNTIME_SCOPE_FIELDS = (
    "tenant_id",
    "workspace_id",
    "runtime_type",
    "runtime_role",
    "install_id",
    "specialist_key",
    "instance_id",
    "capability_digest",
    "machine_enrollment_scope",
    "session_scope",
    "session_scope_key",
)
_RUNTIME_HEALTH_OPERATOR_NEXT_ACTIONS = {
    "restore_rust_kernel",
    "inspect_plugin_system",
    "investigate_runtime_health",
    "complete_workspace_bootstrap",
    "request_owner_approval",
    "wait_for_quiet_hours",
    "monitor_running_work",
    "wait_for_retry",
    "claim_next_work",
    "idle",
}


def _init():
    global _server
    if _server is not None:
        return
    import server as _s
    _server = _s


def _monotonic() -> float:
    return time.monotonic()


# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------


class LocalRunClaimRequest(BaseModel):
    worker_id: Optional[str] = None
    required_capabilities: List[str] = Field(default_factory=list)


class LocalRunHeartbeatPayload(BaseModel):
    worker_id: Optional[str] = None
    note: Optional[str] = None
    event: Optional[Dict[str, Any]] = None


class LocalRunControlStatePayload(BaseModel):
    worker_id: Optional[str] = None


class LocalWorkerHeartbeatPayload(BaseModel):
    current_run_id: Optional[str] = None
    note: Optional[str] = None
    permission_probe: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    runtime_role: Optional[str] = None
    install_id: Optional[str] = None
    specialist_key: Optional[str] = None
    summary_channel: Optional[str] = None
    artifact_channel: Optional[str] = None
    local_private_memory_only: Optional[bool] = None
    summary_text: Optional[str] = None
    artifacts: Optional[List[Dict[str, Any]]] = None
    health_state: Optional[str] = None
    lifecycle_reason: Optional[str] = None


class LocalMachineControlPayload(BaseModel):
    reason: Optional[str] = None


class LocalRunCompletePayload(BaseModel):
    worker_id: Optional[str] = None
    result_text: Optional[str] = None
    result_data: Optional[Dict[str, Any]] = None
    usage_masked: Optional[Dict[str, Any]] = None


class LocalRunPausePayload(BaseModel):
    worker_id: Optional[str] = None
    result_text: Optional[str] = None
    result_data: Optional[Dict[str, Any]] = None
    browser_checkpoint: Optional[Dict[str, Any]] = None
    wait_reason: Optional[str] = None


class LocalRunFailPayload(BaseModel):
    worker_id: Optional[str] = None
    error: str


# ---------------------------------------------------------------------------
# Internal Helpers
# ---------------------------------------------------------------------------


def _worker_online_window_seconds(lease_seconds: Optional[int] = None) -> int:
    _init()
    effective_lease = int(lease_seconds or _server.ORION_LOCAL_LEASE_SECONDS)
    return max(20, effective_lease * 2)


def _normalize_runtime_role(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token in {"captain", "specialist"}:
        return token
    return "generic"


def _normalize_runtime_artifacts(raw_items: Any) -> List[Dict[str, Any]]:
    if isinstance(raw_items, dict):
        items = [raw_items]
    elif isinstance(raw_items, list):
        items = raw_items
    else:
        items = []
    normalized: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        artifact = {
            "id": str(item.get("id") or "").strip() or None,
            "name": str(item.get("name") or "").strip() or None,
            "kind": str(item.get("kind") or item.get("type") or "").strip() or None,
            "path": str(item.get("path") or item.get("uri") or "").strip() or None,
            "preview_path": str(item.get("preview_path") or item.get("preview_url") or "").strip() or None,
            "review_required": bool(item.get("review_required")),
        }
        normalized.append({key: value for key, value in artifact.items() if value is not None})
    return normalized


def _set_runtime_lifecycle_state(
    record: Dict[str, Any],
    state: str,
    *,
    reason: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> Dict[str, Any]:
    _init()
    timestamp = str(now_iso or _server._utc_now_iso())
    normalized = str(state or "").strip().lower() or "registered"
    if normalized not in {"registered", "running", "stopped", "recovering", "revoked"}:
        normalized = "registered"
    previous_state = str(record.get("lifecycle_state") or "").strip().lower()
    record["lifecycle_state"] = normalized
    record["lifecycle_state_updated_at"] = timestamp
    record["lifecycle_reason"] = str(reason or record.get("lifecycle_reason") or "").strip() or None
    if normalized == "registered":
        record.setdefault("registered_at", timestamp)
    elif normalized == "running":
        record["started_at"] = record.get("started_at") or timestamp
        record["last_started_at"] = timestamp
        if previous_state == "recovering":
            record["last_recovered_at"] = timestamp
    elif normalized == "stopped":
        record["stopped_at"] = timestamp
    elif normalized == "recovering":
        record["recovery_requested_at"] = timestamp
    elif normalized == "revoked":
        record["revoked_at"] = record.get("revoked_at") or timestamp
    return record


def _apply_runtime_identity_fields(
    record: Dict[str, Any],
    *,
    runtime_role: Optional[str] = None,
    install_id: Optional[str] = None,
    specialist_key: Optional[str] = None,
    summary_channel: Optional[str] = None,
    artifact_channel: Optional[str] = None,
    local_private_memory_only: Optional[bool] = None,
) -> Dict[str, Any]:
    role = _normalize_runtime_role(runtime_role or record.get("runtime_role"))
    install_token = str(install_id or record.get("install_id") or "").strip() or None
    specialist_token = str(specialist_key or record.get("specialist_key") or "").strip() or None
    if role == "specialist" and not (install_token or specialist_token):
        raise HTTPException(status_code=400, detail="specialist local runtimes require install_id or specialist_key.")
    record["runtime_role"] = role
    if role == "captain":
        record["install_id"] = None
        record["specialist_key"] = None
    else:
        record["install_id"] = install_token
        record["specialist_key"] = specialist_token or install_token
    if summary_channel is not None:
        record["summary_channel"] = str(summary_channel or "").strip() or None
    elif "summary_channel" not in record:
        record["summary_channel"] = None
    if artifact_channel is not None:
        record["artifact_channel"] = str(artifact_channel or "").strip() or None
    elif "artifact_channel" not in record:
        record["artifact_channel"] = None
    if local_private_memory_only is not None:
        record["local_private_memory_only"] = bool(local_private_memory_only)
    elif "local_private_memory_only" not in record:
        record["local_private_memory_only"] = True
    return record


def _apply_runtime_surface_hooks(
    record: Dict[str, Any],
    *,
    summary_text: Optional[str] = None,
    artifacts: Optional[List[Dict[str, Any]]] = None,
    health_state: Optional[str] = None,
) -> Dict[str, Any]:
    _init()
    now_iso = _server._utc_now_iso()
    if summary_text is not None:
        summary = " ".join(str(summary_text or "").strip().split())
        record["last_summary"] = summary or None
        if summary:
            record["last_summary_at"] = now_iso
    if artifacts is not None:
        normalized_artifacts = _normalize_runtime_artifacts(artifacts)
        record["last_artifacts"] = normalized_artifacts
        if normalized_artifacts:
            record["last_artifact_at"] = now_iso
    if health_state is not None:
        token = str(health_state or "").strip().lower() or "healthy"
        record["health_state"] = token
        record["health_updated_at"] = now_iso
    elif not str(record.get("health_state") or "").strip():
        record["health_state"] = "unknown"
    return record


def _mark_local_worker_seen(worker_id: str, current_run_id: Optional[str], status_hint: str, note: Optional[str] = None):
    _init()
    worker = str(worker_id or "").strip()
    if not worker:
        return
    now_iso = _server._utc_now_iso()
    with _server.LOCAL_QUEUE_LOCK:
        previous = _server.LOCAL_WORKER_REGISTRY.get(worker) if isinstance(_server.LOCAL_WORKER_REGISTRY.get(worker), dict) else {}
        next_record = machine_lease_service.build_machine_presence_record(
            previous_record=previous,
            machine_id=worker,
            current_run_id=current_run_id,
            status_hint=status_hint,
            lease_seconds=_server.ORION_LOCAL_LEASE_SECONDS,
            now_iso=now_iso,
            note=note,
        )
        should_persist, should_sync_durable = _worker_presence_sync_policy(previous, next_record)
        if should_persist:
            next_record["_runtime_state_persisted_at"] = now_iso
        elif isinstance(previous, dict) and previous.get("_runtime_state_persisted_at"):
            next_record["_runtime_state_persisted_at"] = previous.get("_runtime_state_persisted_at")
        if should_sync_durable:
            next_record["_durable_sync_at"] = now_iso
        elif isinstance(previous, dict) and previous.get("_durable_sync_at"):
            next_record["_durable_sync_at"] = previous.get("_durable_sync_at")
        _server.LOCAL_WORKER_REGISTRY[worker] = next_record
    if should_sync_durable:
        _sync_durable_fleet_worker(next_record, heartbeat_seen=True)
    if should_persist:
        _persist_local_runtime_state()


def _worker_presence_sync_policy(previous: Dict[str, Any], next_record: Dict[str, Any]) -> tuple[bool, bool]:
    _init()
    previous_record = dict(previous or {})
    state_fields = (
        "status",
        "current_run_id",
        "control_state",
        "lifecycle_state",
        "runtime_role",
        "health_state",
    )
    state_changed = any(previous_record.get(field) != next_record.get(field) for field in state_fields)
    previous_note = str(previous_record.get("note") or "").strip().lower()
    next_note = str(next_record.get("note") or "").strip().lower()
    noteworthy_note_change = (
        previous_note != next_note
        and (previous_note not in _TRANSIENT_WORKER_NOTES or next_note not in _TRANSIENT_WORKER_NOTES)
    )
    if not previous_record:
        return True, True

    now_seen = _server._parse_utc_ts(next_record.get("last_seen_at"))
    previous_persisted = _server._parse_utc_ts(previous_record.get("_runtime_state_persisted_at"))
    previous_durable_sync = _server._parse_utc_ts(previous_record.get("_durable_sync_at"))

    def _elapsed_seconds(previous_ts: Optional[datetime]) -> float:
        if now_seen is None or previous_ts is None:
            return float("inf")
        return max(0.0, (now_seen - previous_ts).total_seconds())

    should_persist = (
        state_changed
        or noteworthy_note_change
        or _elapsed_seconds(previous_persisted) >= LOCAL_WORKER_STATE_PERSIST_INTERVAL_SECONDS
    )
    should_sync_durable = (
        state_changed
        or noteworthy_note_change
        or _elapsed_seconds(previous_durable_sync) >= LOCAL_WORKER_DURABLE_SYNC_INTERVAL_SECONDS
    )
    return should_persist, should_sync_durable


def _manual_takeover_active(run: Dict[str, Any]) -> bool:
    result_data = run.get("result_data") if isinstance(run.get("result_data"), dict) else {}
    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    return bool(result_data.get("manual_takeover") or metadata.get("manual_takeover"))


def _local_execution_checkpoint_payload(run: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    checkpoint = run.get("local_execution_checkpoint")
    if isinstance(checkpoint, dict) and checkpoint:
        return dict(checkpoint)
    result_data = run.get("result_data") if isinstance(run.get("result_data"), dict) else {}
    result_checkpoint = result_data.get("local_execution_checkpoint")
    if isinstance(result_checkpoint, dict) and result_checkpoint:
        return dict(result_checkpoint)
    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    metadata_checkpoint = metadata.get("local_execution_checkpoint")
    if isinstance(metadata_checkpoint, dict) and metadata_checkpoint:
        return dict(metadata_checkpoint)
    pack_inputs = metadata.get("pack_inputs") if isinstance(metadata.get("pack_inputs"), dict) else {}
    operations = pack_inputs.get("operations") if isinstance(pack_inputs.get("operations"), list) else []
    if operations:
        return {
            "kind": "local_execution_v1",
            "next_operation_index": 0,
            "total_operations": len(operations),
            "phase": "planned",
            "mode": "observing",
        }
    return None


def _update_run_progress_from_structured_event(run: Dict[str, Any], event_name: str, event_data: Optional[Dict[str, Any]]) -> None:
    if event_name != "computer_action" or not isinstance(event_data, dict):
        return
    step_number = event_data.get("step_number")
    step_total = event_data.get("step_total")
    phase = str(event_data.get("phase") or "").strip().lower() or "completed"
    label = str(event_data.get("label") or "").strip() or "Computer action"
    next_operation_index: Optional[int] = None
    if isinstance(step_number, (int, float)):
        normalized_step = max(1, int(step_number))
        if phase == "completed":
            next_operation_index = normalized_step
        else:
            next_operation_index = normalized_step - 1
    checkpoint: Dict[str, Any] = {
        "kind": "local_execution_v1",
        "phase": phase,
        "mode": str(event_data.get("mode") or "").strip().lower() or "acting",
        "current_label": label,
        "action_type": str(event_data.get("action_type") or "").strip().lower() or None,
        "updated_at": _server._utc_now_iso(),
    }
    if next_operation_index is not None:
        checkpoint["next_operation_index"] = max(0, next_operation_index)
    if isinstance(step_total, (int, float)):
        checkpoint["total_operations"] = max(1, int(step_total))
    run["local_execution_checkpoint"] = checkpoint
    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    metadata["local_execution_checkpoint"] = checkpoint
    metadata["local_execution_resume_supported"] = True
    context["metadata"] = metadata
    run["context"] = context


def _maybe_emit_local_still_working(
    run_id: str,
    run: Dict[str, Any],
    claim: Dict[str, Any],
    *,
    note: Optional[str] = None,
) -> bool:
    _init()
    now = _server._utc_now()
    last_progress = _server._parse_utc_ts(claim.get("last_progress_event_at")) or _server._parse_utc_ts(claim.get("claimed_at"))
    if last_progress is not None and (now - last_progress).total_seconds() < LOCAL_RUN_STILL_WORKING_INTERVAL_SECONDS:
        return False
    now_iso = _server._utc_now_iso()
    claim["last_progress_event_at"] = now_iso
    run["local_last_progress_at"] = now_iso
    _server.emit_log(
        run["logs"],
        "info",
        str(note or "Still working on your laptop.").strip()[:400],
        event="local_still_working",
        data={"run_id": run_id, "last_progress_at": now_iso},
    )
    return True


def _capability_digest(capabilities: Optional[List[str]]) -> Optional[str]:
    items = [str(item).strip() for item in (capabilities or []) if str(item).strip()]
    if not items:
        return None
    normalized = "\n".join(sorted(set(items)))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _safe_utc_now_iso() -> str:
    _init()
    utc_now_iso_fn = getattr(_server, "_utc_now_iso", None)
    if callable(utc_now_iso_fn):
        return str(utc_now_iso_fn())
    return datetime.utcnow().isoformat() + "Z"


def _append_runtime_control_event(runtime_id: str, event_type: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    runtime_token = str(runtime_id or "").strip()
    if not runtime_token:
        raise HTTPException(status_code=400, detail="runtime_id is required.")
    event_type_token = str(event_type or "runtime_control").strip().lower() or "runtime_control"
    event_payload = dict(payload or {})
    global _RUNTIME_CONTROL_STREAM_SEQUENCE
    with _RUNTIME_CONTROL_STREAM_CONDITION:
        _RUNTIME_CONTROL_STREAM_SEQUENCE += 1
        event_record = {
            "sequence": _RUNTIME_CONTROL_STREAM_SEQUENCE,
            "event": event_type_token,
            "runtime_id": runtime_token,
            "requested_at": str(event_payload.get("requested_at") or _safe_utc_now_iso()),
            **event_payload,
        }
        backlog = list(_RUNTIME_CONTROL_STREAM_EVENTS.get(runtime_token) or [])
        backlog.append(event_record)
        if len(backlog) > _RUNTIME_CONTROL_STREAM_BACKLOG_LIMIT:
            backlog = backlog[-_RUNTIME_CONTROL_STREAM_BACKLOG_LIMIT :]
        _RUNTIME_CONTROL_STREAM_EVENTS[runtime_token] = backlog
        _RUNTIME_CONTROL_STREAM_CONDITION.notify_all()
    _notify_runtime_control_async_subscribers(runtime_token)
    return dict(event_record)


def _list_runtime_control_events(runtime_id: str, *, since_sequence: int = 0) -> List[Dict[str, Any]]:
    runtime_token = str(runtime_id or "").strip()
    if not runtime_token:
        return []
    with _RUNTIME_CONTROL_STREAM_LOCK:
        backlog = list(_RUNTIME_CONTROL_STREAM_EVENTS.get(runtime_token) or [])
    return [
        dict(item)
        for item in backlog
        if isinstance(item, dict) and int(item.get("sequence") or 0) > int(since_sequence or 0)
    ]


def iter_runtime_control_stream(
    runtime_id: str,
    *,
    since_sequence: int = 0,
    include_backlog: bool = True,
    heartbeat_seconds: float = 5.0,
    timeout_seconds: float = 30.0,
):
    runtime_token = str(runtime_id or "").strip()
    if not runtime_token:
        return
    last_sequence = max(0, int(since_sequence or 0))
    start = _monotonic()
    last_heartbeat = start
    while True:
        pending = _list_runtime_control_events(runtime_token, since_sequence=last_sequence) if include_backlog or last_sequence else _list_runtime_control_events(runtime_token, since_sequence=last_sequence)
        for event_record in pending:
            last_sequence = max(last_sequence, int(event_record.get("sequence") or 0))
            yield {
                "event": str(event_record.get("event") or "runtime_control").strip().lower() or "runtime_control",
                "id": str(event_record.get("sequence") or ""),
                "data": dict(event_record),
            }
        now = _monotonic()
        if (now - start) >= max(1.0, float(timeout_seconds or 30.0)):
            break
        if (now - last_heartbeat) >= max(1.0, float(heartbeat_seconds or 5.0)):
            last_heartbeat = now
            yield {
                "event": "heartbeat",
                "id": str(last_sequence),
                "data": {
                    "event": "heartbeat",
                    "runtime_id": runtime_token,
                    "sequence": last_sequence,
                    "requested_at": _safe_utc_now_iso(),
                },
            }
        wait_seconds = min(max(0.1, float(heartbeat_seconds or 5.0)), max(0.1, float(timeout_seconds or 30.0)))
        with _RUNTIME_CONTROL_STREAM_CONDITION:
            _RUNTIME_CONTROL_STREAM_CONDITION.wait(timeout=wait_seconds)


def _register_runtime_control_async_subscriber(runtime_id: str, subscriber: Dict[str, Any]) -> None:
    runtime_token = str(runtime_id or "").strip()
    if not runtime_token:
        return
    with _RUNTIME_CONTROL_STREAM_LOCK:
        subscribers = list(_RUNTIME_CONTROL_STREAM_ASYNC_SUBSCRIBERS.get(runtime_token) or [])
        subscribers.append(subscriber)
        _RUNTIME_CONTROL_STREAM_ASYNC_SUBSCRIBERS[runtime_token] = subscribers


def _unregister_runtime_control_async_subscriber(runtime_id: str, subscriber: Dict[str, Any]) -> None:
    runtime_token = str(runtime_id or "").strip()
    if not runtime_token:
        return
    with _RUNTIME_CONTROL_STREAM_LOCK:
        subscribers = [
            item
            for item in (_RUNTIME_CONTROL_STREAM_ASYNC_SUBSCRIBERS.get(runtime_token) or [])
            if item is not subscriber
        ]
        if subscribers:
            _RUNTIME_CONTROL_STREAM_ASYNC_SUBSCRIBERS[runtime_token] = subscribers
        else:
            _RUNTIME_CONTROL_STREAM_ASYNC_SUBSCRIBERS.pop(runtime_token, None)


def _notify_runtime_control_async_subscribers(runtime_id: str) -> None:
    runtime_token = str(runtime_id or "").strip()
    if not runtime_token:
        return
    with _RUNTIME_CONTROL_STREAM_LOCK:
        subscribers = list(_RUNTIME_CONTROL_STREAM_ASYNC_SUBSCRIBERS.get(runtime_token) or [])
    for subscriber in subscribers:
        loop = subscriber.get("loop")
        signal = subscriber.get("signal")
        try:
            if loop and signal and not loop.is_closed():
                loop.call_soon_threadsafe(signal.set)
        except RuntimeError:
            continue


async def aiter_runtime_control_stream(
    runtime_id: str,
    *,
    since_sequence: int = 0,
    include_backlog: bool = True,
    heartbeat_seconds: float = 5.0,
    timeout_seconds: float = 30.0,
):
    runtime_token = str(runtime_id or "").strip()
    if not runtime_token:
        return
    last_sequence = max(0, int(since_sequence or 0))
    start = _monotonic()
    last_heartbeat = start
    safe_heartbeat = max(1.0, float(heartbeat_seconds or 5.0))
    safe_timeout = max(1.0, float(timeout_seconds or 30.0))
    signal = asyncio.Event()
    subscriber = {"loop": asyncio.get_running_loop(), "signal": signal}
    _register_runtime_control_async_subscriber(runtime_token, subscriber)
    try:
        while True:
            pending = _list_runtime_control_events(runtime_token, since_sequence=last_sequence) if include_backlog or last_sequence else _list_runtime_control_events(runtime_token, since_sequence=last_sequence)
            for event_record in pending:
                last_sequence = max(last_sequence, int(event_record.get("sequence") or 0))
                yield {
                    "event": str(event_record.get("event") or "runtime_control").strip().lower() or "runtime_control",
                    "id": str(event_record.get("sequence") or ""),
                    "data": dict(event_record),
                }
            now = _monotonic()
            remaining = safe_timeout - (now - start)
            if remaining <= 0:
                break
            if (now - last_heartbeat) >= safe_heartbeat:
                last_heartbeat = now
                yield {
                    "event": "heartbeat",
                    "id": str(last_sequence),
                    "data": {
                        "event": "heartbeat",
                        "runtime_id": runtime_token,
                        "sequence": last_sequence,
                        "requested_at": _safe_utc_now_iso(),
                    },
                }
                continue
            wait_seconds = max(0.05, min(safe_heartbeat - (now - last_heartbeat), remaining))
            try:
                await asyncio.wait_for(signal.wait(), timeout=wait_seconds)
            except asyncio.TimeoutError:
                pass
            finally:
                signal.clear()
    finally:
        _unregister_runtime_control_async_subscriber(runtime_token, subscriber)


def reset_runtime_control_stream_state_for_tests() -> None:
    global _RUNTIME_CONTROL_STREAM_SEQUENCE, _LOCAL_CLAIM_CLEANUP_NEXT_AT_MONOTONIC
    with _RUNTIME_CONTROL_STREAM_CONDITION:
        _RUNTIME_CONTROL_STREAM_EVENTS.clear()
        _RUNTIME_CONTROL_STREAM_ASYNC_SUBSCRIBERS.clear()
        _RUNTIME_CONTROL_STREAM_SEQUENCE = 0
        _RUNTIME_CONTROL_STREAM_CONDITION.notify_all()
    with _LOCAL_CLAIM_CLEANUP_LOCK:
        _LOCAL_CLAIM_CLEANUP_NEXT_AT_MONOTONIC = 0.0


def _normalize_capability_ids(raw_items: Any) -> List[str]:
    seen = set()
    normalized: List[str] = []
    for item in raw_items or []:
        clean = str(item or "").strip().lower()
        if clean and clean not in seen:
            seen.add(clean)
            normalized.append(clean)
    return normalized


def _normalize_runtime_ids(raw_items: Any) -> List[str]:
    seen = set()
    normalized: List[str] = []
    for item in raw_items or []:
        clean = str(item or "").strip()
        if clean and clean not in seen:
            seen.add(clean)
            normalized.append(clean)
    return normalized


def _runtime_id_from_attachment_token(value: Any) -> Optional[str]:
    token = str(value or "").strip()
    if not token:
        return None
    if ":" not in token:
        return token
    prefix, _, suffix = token.partition(":")
    if prefix in {"local_companion", "managed_cloud", "self_hosted_business_node"} and suffix.strip():
        return suffix.strip()
    return token


def _preferred_runtime_ids_from_metadata(metadata: Dict[str, Any]) -> List[str]:
    ordered: List[str] = []
    seen = set()

    def _append(value: Any) -> None:
        token = _runtime_id_from_attachment_token(value)
        if not token or token in seen:
            return
        seen.add(token)
        ordered.append(token)

    runtime_selection = metadata.get("runtime_selection") if isinstance(metadata.get("runtime_selection"), dict) else {}
    selected_attachment = runtime_selection.get("selected_attachment") if isinstance(runtime_selection.get("selected_attachment"), dict) else {}

    _append(metadata.get("execution_target_preferred_runtime_id"))
    _append(metadata.get("machine_target"))
    _append(runtime_selection.get("machine_target"))
    _append(metadata.get("runtime_attachment_id"))
    _append(selected_attachment.get("attachment_id"))
    for item in metadata.get("execution_target_matching_runtime_ids") or []:
        _append(item)
    return ordered


def _persist_local_runtime_state() -> None:
    _init()
    with _server.LOCAL_QUEUE_LOCK:
        pending_run_ids = list(_server.LOCAL_PENDING_RUN_IDS)
        claimed_runs = dict(_server.LOCAL_CLAIMED_RUNS)
        runtime_registrations = dict(_server.LOCAL_WORKER_REGISTRY)
    outbox_service.persist_local_runtime_state(
        db_path=_server.ORION_RUNTIME_STATE_DB,
        pending_run_ids=pending_run_ids,
        claimed_runs=claimed_runs,
        runtime_registrations=runtime_registrations,
    )


def _durable_fleet_worker_payload(record: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(record or {})
    payload.pop("session_token", None)
    payload.pop("_runtime_state_persisted_at", None)
    payload.pop("_durable_sync_at", None)
    return payload


def _sync_durable_fleet_worker(record: Dict[str, Any], *, heartbeat_seen: bool) -> None:
    if not isinstance(record, dict):
        return
    payload = _durable_fleet_worker_payload(record)

    def _worker() -> None:
        try:
            run_state_repository.sync_upsert_fleet_worker(
                payload,
                heartbeat_seen=heartbeat_seen,
            )
        except Exception:
            return

    thread = threading.Thread(
        target=_worker,
        name=f"fleet-worker-sync-{str(record.get('runtime_id') or record.get('machine_id') or 'worker')}",
        daemon=True,
    )
    thread.start()


def _dispatch_touch_claim_heartbeat(
    run_id: str,
    worker_id: str,
    *,
    note: Optional[str] = None,
    progress: bool = False,
) -> None:
    with _server.LOCAL_QUEUE_LOCK:
        claim = _server.LOCAL_CLAIMED_RUNS.get(run_id) if isinstance(_server.LOCAL_CLAIMED_RUNS.get(run_id), dict) else {}
        lease_id = str((claim or {}).get("lease_id") or "").strip() or None

    def _worker() -> None:
        try:
            run_state_repository.sync_touch_claim_heartbeat(
                run_id,
                worker_id,
                lease_id=lease_id,
                note=note,
                progress=progress,
            )
        except Exception:
            return

    thread = threading.Thread(
        target=_worker,
        name=f"claim-heartbeat-{str(run_id or 'run')[:12]}",
        daemon=True,
    )
    thread.start()


def _hydrate_worker_from_durable_registry(worker_id: str) -> Optional[Dict[str, Any]]:
    _init()
    token = str(worker_id or "").strip()
    if not token:
        return None
    try:
        record = run_state_repository.sync_get_fleet_worker(token)
    except Exception:
        record = None
    if not isinstance(record, dict):
        return None
    with _server.LOCAL_QUEUE_LOCK:
        existing = _server.LOCAL_WORKER_REGISTRY.get(token) if isinstance(_server.LOCAL_WORKER_REGISTRY.get(token), dict) else {}
        merged = dict(record)
        merged.update(existing)
        for field in _DURABLE_RUNTIME_SCOPE_FIELDS:
            if field in record:
                merged[field] = record[field]
        _server.LOCAL_WORKER_REGISTRY[token] = merged
    return dict(merged)


def _local_worker_registry_snapshot() -> Dict[str, Dict[str, Any]]:
    _init()
    with _server.LOCAL_QUEUE_LOCK:
        return {
            str(worker_id): dict(record)
            for worker_id, record in _server.LOCAL_WORKER_REGISTRY.items()
            if isinstance(record, dict)
        }


def _merged_worker_registry_snapshot() -> Dict[str, Dict[str, Any]]:
    _init()
    merged: Dict[str, Dict[str, Any]] = {}
    try:
        durable_items = run_state_repository.sync_list_fleet_workers()
    except Exception:
        durable_items = []
    for item in durable_items or []:
        if not isinstance(item, dict):
            continue
        worker_id = str(item.get("worker_id") or item.get("runtime_id") or "").strip()
        if worker_id:
            merged[worker_id] = dict(item)
    with _server.LOCAL_QUEUE_LOCK:
        for worker_id, record in _server.LOCAL_WORKER_REGISTRY.items():
            if not isinstance(record, dict):
                continue
            base = dict(merged.get(worker_id) or {})
            base.update(dict(record))
            durable = merged.get(worker_id) if isinstance(merged.get(worker_id), dict) else {}
            for field in _DURABLE_RUNTIME_SCOPE_FIELDS:
                if field in durable:
                    base[field] = durable[field]
            merged[worker_id] = base
    return merged


def _emit_machine_outbox_event(action: str, record: Dict[str, Any], *, error: Optional[str] = None) -> None:
    _init()
    if not isinstance(record, dict):
        return
    machine_id = str(record.get("machine_id") or record.get("runtime_id") or "").strip()
    if not machine_id:
        return
    payload = dict(record)
    payload.pop("enrollment_token_hash", None)
    payload.pop("session_token", None)
    try:
        outbox_service.emit_machine_event(
            machine_id=machine_id,
            tenant_id=str(record.get("tenant_id") or "default").strip() or "default",
            workspace_id=str(record.get("workspace_id") or "default").strip() or "default",
            action=action,
            machine_payload=payload,
            trace_id=str(record.get("trace_id") or record.get("session_token") or machine_id).strip(),
            error=error,
        )
    except Exception:
        return


def _machine_runtime_base_url() -> str:
    _init()
    value = str(
        getattr(_server, "EMPYRALIST_WORKFLOW_API_URL", "")
        or getattr(_server, "ORION_API_URL", "")
        or "http://127.0.0.1:8001"
    ).strip().rstrip("/")
    return value or "http://127.0.0.1:8001"


def _set_enrollment_state(
    record: Dict[str, Any],
    state: str,
    *,
    error: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> Dict[str, Any]:
    _init()
    timestamp = str(now_iso or _server._utc_now_iso())
    normalized = str(state or "").strip().lower() or "requested"
    record["enrollment_state"] = normalized
    record["enrollment_updated_at"] = timestamp
    if not record.get("enrollment_requested_at"):
        record["enrollment_requested_at"] = timestamp
    if normalized == "installing" and not record.get("installing_started_at"):
        record["installing_started_at"] = timestamp
    if normalized == "starting" and not record.get("starting_started_at"):
        record["starting_started_at"] = timestamp
    if normalized == "registering" and not record.get("registering_started_at"):
        record["registering_started_at"] = timestamp
    if normalized == "healthy":
        record["bootstrap_completed_at"] = timestamp
        record["bootstrap_error"] = None
    elif normalized == "failed":
        record["bootstrap_error"] = str(error or record.get("bootstrap_error") or "bootstrap_failed")[:400]
    elif error is not None:
        record["bootstrap_error"] = str(error)[:400]
    return record


def _machine_control_state(record: Dict[str, Any]) -> str:
    state = str(record.get("control_state") or "").strip().lower()
    if state in {"interrupting", "suspended", "revoked"}:
        return state
    if record.get("interrupt_requested_at"):
        return "interrupting"
    if record.get("revoked_at"):
        return "revoked"
    if record.get("suspended_at"):
        return "suspended"
    return "active"


def _set_machine_control_state(
    record: Dict[str, Any],
    state: str,
    *,
    reason: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> Dict[str, Any]:
    _init()
    timestamp = str(now_iso or _server._utc_now_iso())
    normalized = str(state or "").strip().lower() or "active"
    record["control_state"] = normalized
    record["control_state_updated_at"] = timestamp
    if normalized == "interrupting":
        record["interrupt_requested_at"] = timestamp
        record["interrupt_reason"] = str(reason or record.get("interrupt_reason") or "Hard kill requested by operator.")[:280]
    elif normalized == "suspended":
        record["suspended_at"] = timestamp
        record["suspended_reason"] = str(reason or record.get("suspended_reason") or "Suspended by operator.")[:280]
    elif normalized == "revoked":
        record["revoked_at"] = timestamp
        record["revoked_reason"] = str(reason or record.get("revoked_reason") or "Revoked by operator.")[:280]
    elif normalized == "active":
        record["interrupt_requested_at"] = None
        record["interrupt_reason"] = None
        record["suspended_at"] = None
        record["suspended_reason"] = None
    return record


def _normalize_permission_probe_entry(
    *,
    raw_entry: Any,
    fallback_status: str,
    fallback_source: str,
    fallback_detail: str = "",
) -> Dict[str, Any]:
    allowed_statuses = {"granted", "denied", "unsupported", "unknown"}
    if isinstance(raw_entry, dict):
        status = str(raw_entry.get("status") or "").strip().lower()
        if not status and isinstance(raw_entry.get("granted"), bool):
            status = "granted" if bool(raw_entry.get("granted")) else "denied"
        if status not in allowed_statuses:
            status = fallback_status
        source = str(raw_entry.get("source") or fallback_source).strip() or fallback_source
        detail = str(raw_entry.get("detail") or fallback_detail).strip()
        updated_at = str(raw_entry.get("updated_at") or "").strip() or None
        return {
            "status": status,
            "source": source,
            "detail": detail,
            "updated_at": updated_at,
        }
    if isinstance(raw_entry, bool):
        return {
            "status": "granted" if raw_entry else "denied",
            "source": fallback_source,
            "detail": fallback_detail,
            "updated_at": None,
        }
    default_policy_mode = getattr(_server, "ORION_RUNTIME_POLICY_MODE_DEFAULT", "local_default")

    return {
        "status": fallback_status,
        "source": fallback_source,
        "detail": fallback_detail,
        "updated_at": None,
    }


def _permission_probe_defaults(record: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    capabilities = {str(item).strip().lower() for item in (record.get("capabilities") or []) if str(item).strip()}
    execution_targets = {str(item).strip().lower() for item in (record.get("execution_targets") or []) if str(item).strip()}
    runtime_type = str(record.get("runtime_type") or "").strip().lower()
    local_companion_like = runtime_type == "local_companion" or "local_companion" in execution_targets
    control_capability = any(capability.startswith("computer_control.") for capability in capabilities)
    browser_capability = "browser_automation.interactive" in capabilities
    shell_capability = "shell.execute" in capabilities
    screenshot_capability = "screenshot.capture" in capabilities
    return {
        "screen_recording": _normalize_permission_probe_entry(
            raw_entry=None,
            fallback_status="unknown" if (screenshot_capability or local_companion_like) else "unsupported",
            fallback_source="probe_pending" if (screenshot_capability or local_companion_like) else "capability_manifest",
            fallback_detail="Waiting for worker probe." if (screenshot_capability or local_companion_like) else "Screen capture is not advertised by this machine.",
        ),
        "accessibility": _normalize_permission_probe_entry(
            raw_entry=None,
            fallback_status="unknown" if (control_capability or browser_capability or local_companion_like) else "unsupported",
            fallback_source="probe_pending" if (control_capability or browser_capability or local_companion_like) else "capability_manifest",
            fallback_detail="Waiting for worker probe." if (control_capability or browser_capability or local_companion_like) else "Accessibility-driven control is not advertised by this machine.",
        ),
        "browser_session": _normalize_permission_probe_entry(
            raw_entry=None,
            fallback_status="granted" if (browser_capability or local_companion_like) else "unsupported",
            fallback_source="capability_manifest",
            fallback_detail="Browser/session automation available." if (browser_capability or local_companion_like) else "Browser/session automation is not advertised by this machine.",
        ),
        "shell": _normalize_permission_probe_entry(
            raw_entry=None,
            fallback_status="granted" if shell_capability else "unsupported",
            fallback_source="capability_manifest",
            fallback_detail="Shell execution available." if shell_capability else "Shell execution is not advertised by this machine.",
        ),
    }


def _normalized_permission_probe(record: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    defaults = _permission_probe_defaults(record)
    raw_probe = record.get("permission_probe") if isinstance(record.get("permission_probe"), dict) else {}
    return {
        key: _normalize_permission_probe_entry(
            raw_entry=raw_probe.get(key),
            fallback_status=str(defaults[key].get("status") or "unknown"),
            fallback_source=str(defaults[key].get("source") or "probe_pending"),
            fallback_detail=str(defaults[key].get("detail") or ""),
        )
        for key in defaults
    }


def _apply_permission_probe(record: Dict[str, Any], probe: Optional[Dict[str, Dict[str, Any]]]) -> Dict[str, Any]:
    if isinstance(probe, dict) and probe:
        record["permission_probe"] = dict(probe)
        _init()
        record["permission_probe_updated_at"] = _server._utc_now_iso()
    elif not isinstance(record.get("permission_probe"), dict):
        record["permission_probe"] = {}
    return record


def _permission_probe_semantically_equal(current: Optional[Dict[str, Dict[str, Any]]], proposed: Optional[Dict[str, Dict[str, Any]]]) -> bool:
    current_probe = current if isinstance(current, dict) else {}
    proposed_probe = proposed if isinstance(proposed, dict) else {}
    keys = set(current_probe.keys()) | set(proposed_probe.keys())
    if not keys:
        return True
    for key in keys:
        current_entry = current_probe.get(key) if isinstance(current_probe.get(key), dict) else {}
        proposed_entry = proposed_probe.get(key) if isinstance(proposed_probe.get(key), dict) else {}
        current_shape = (
            str(current_entry.get("status") or "").strip().lower(),
            str(current_entry.get("source") or "").strip(),
            str(current_entry.get("detail") or "").strip(),
        )
        proposed_shape = (
            str(proposed_entry.get("status") or "").strip().lower(),
            str(proposed_entry.get("source") or "").strip(),
            str(proposed_entry.get("detail") or "").strip(),
        )
        if current_shape != proposed_shape:
            return False
    return True


def _machine_policy_status(record: Dict[str, Any]) -> Dict[str, Any]:
    return safe_mode_service.resolve_machine_policy_status(
        tenant_id=str(record.get("tenant_id") or "default").strip() or "default",
        workspace_id=str(record.get("workspace_id") or "default").strip() or "default",
        machine_id=str(record.get("machine_id") or record.get("runtime_id") or "").strip() or None,
        capability_ids=list(record.get("capabilities") or []),
    )


def _assert_enrollment_token(record: Dict[str, Any], provided_token: Optional[str]) -> str:
    _init()
    provided = str(provided_token or "").strip()
    if not provided:
        raise HTTPException(status_code=401, detail="Machine enrollment token is required.")
    expected_hash = str(record.get("enrollment_token_hash") or "").strip()
    if not expected_hash:
        raise HTTPException(status_code=409, detail="Machine enrollment is not pending.")
    actual_hash = hashlib.sha256(provided.encode("utf-8")).hexdigest()
    if actual_hash != expected_hash:
        raise HTTPException(status_code=401, detail="Machine enrollment token is invalid.")
    return provided


def _mark_ghost_enrollments_failed() -> List[str]:
    _init()
    now = _server._utc_now()
    failed_machine_ids: List[str] = []
    changed = False
    with _server.LOCAL_QUEUE_LOCK:
        for machine_id, record in list(_server.LOCAL_WORKER_REGISTRY.items()):
            if not isinstance(record, dict):
                continue
            state = str(record.get("enrollment_state") or "").strip().lower()
            if state in {"", "healthy", "failed"}:
                continue
            requested_at = _server._parse_utc_ts(record.get("enrollment_requested_at"))
            if requested_at is None:
                continue
            if (now - requested_at).total_seconds() <= MACHINE_ENROLLMENT_TIMEOUT_SECONDS:
                continue
            last_seen_at = _server._parse_utc_ts(record.get("last_seen_at"))
            if last_seen_at is not None and _is_worker_online(record, now):
                continue
            _set_enrollment_state(
                record,
                "failed",
                error="Machine bootstrap timed out before the worker heartbeated.",
                now_iso=_server._utc_now_iso(),
            )
            _server.LOCAL_WORKER_REGISTRY[machine_id] = record
            _sync_durable_fleet_worker(record, heartbeat_seen=False)
            failed_machine_ids.append(machine_id)
            _emit_machine_outbox_event(
                "bootstrap_failed",
                record,
                error="Machine bootstrap timed out before the worker heartbeated.",
            )
            changed = True
    if changed:
        _persist_local_runtime_state()
    return failed_machine_ids


def _record_local_runtime_watchdog_status(
    *,
    checked_at: str,
    status: str,
    summary: str,
    cleaned_run_ids: Optional[List[str]] = None,
    resumed_run_ids: Optional[List[str]] = None,
    interval_seconds: Optional[int] = None,
) -> None:
    with _LOCAL_RUNTIME_WATCHDOG_LOCK:
        _LOCAL_RUNTIME_WATCHDOG_STATE.update(
            {
                "running": True,
                "interval_seconds": int(interval_seconds or _LOCAL_RUNTIME_WATCHDOG_STATE.get("interval_seconds") or LOCAL_RUNTIME_WATCHDOG_INTERVAL_SECONDS),
                "last_checked_at": checked_at,
                "last_status": str(status or "ok").strip() or "ok",
                "last_summary": str(summary or "").strip() or "Local runtime watchdog ran.",
                "last_cleaned_count": len(cleaned_run_ids or []),
                "last_cleaned_run_ids": [str(item) for item in (cleaned_run_ids or []) if str(item or "").strip()],
                "last_resumed_count": len(resumed_run_ids or []),
                "last_resumed_run_ids": [str(item) for item in (resumed_run_ids or []) if str(item or "").strip()],
            }
        )


def local_runtime_watchdog_status_snapshot() -> Dict[str, Any]:
    with _LOCAL_RUNTIME_WATCHDOG_LOCK:
        return {
            "running": bool(_LOCAL_RUNTIME_WATCHDOG_STATE.get("running")),
            "interval_seconds": int(_LOCAL_RUNTIME_WATCHDOG_STATE.get("interval_seconds") or LOCAL_RUNTIME_WATCHDOG_INTERVAL_SECONDS),
            "last_checked_at": _LOCAL_RUNTIME_WATCHDOG_STATE.get("last_checked_at"),
            "last_status": _LOCAL_RUNTIME_WATCHDOG_STATE.get("last_status"),
            "last_summary": _LOCAL_RUNTIME_WATCHDOG_STATE.get("last_summary"),
            "last_cleaned_count": int(_LOCAL_RUNTIME_WATCHDOG_STATE.get("last_cleaned_count") or 0),
            "last_cleaned_run_ids": list(_LOCAL_RUNTIME_WATCHDOG_STATE.get("last_cleaned_run_ids") or []),
            "last_resumed_count": int(_LOCAL_RUNTIME_WATCHDOG_STATE.get("last_resumed_count") or 0),
            "last_resumed_run_ids": list(_LOCAL_RUNTIME_WATCHDOG_STATE.get("last_resumed_run_ids") or []),
        }


def _required_capabilities_for_run(run: Dict[str, Any]) -> List[str]:
    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    precheck = metadata.get("tool_policy_precheck") if isinstance(metadata.get("tool_policy_precheck"), dict) else {}
    return _normalize_capability_ids(precheck.get("capability_ids") if isinstance(precheck, dict) else [])


def _ordered_runtime_preferences_for_run(run: Dict[str, Any]) -> List[str]:
    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    return _preferred_runtime_ids_from_metadata(metadata)


def _runtime_group_for_run(run: Dict[str, Any]) -> Dict[str, Any]:
    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    ordered_runtime_ids = _preferred_runtime_ids_from_metadata(metadata)
    matching_runtime_ids = _normalize_runtime_ids(metadata.get("execution_target_matching_runtime_ids") or ordered_runtime_ids)
    preferred_runtime_id = ordered_runtime_ids[0] if ordered_runtime_ids else None
    return {
        "matching_runtime_ids": matching_runtime_ids,
        "preferred_runtime_id": preferred_runtime_id,
    }


def _queue_pressure_for_runtime_group(
    queued_run_ids: List[str],
    matching_runtime_ids: List[str],
    preferred_runtime_id: Optional[str],
    *,
    stop_before_run_id: Optional[str] = None,
) -> Dict[str, Any]:
    _init()
    if _server is None or getattr(_server, "runs", None) is None:
        return {
            "queued_ahead_count": 0,
            "contender_run_ids": [],
        }
    matching_set = set(_normalize_runtime_ids(matching_runtime_ids))
    preferred_token = str(preferred_runtime_id or "").strip()
    queued_ahead_count = 0
    contender_ids: List[str] = []

    for run_id in queued_run_ids:
        if stop_before_run_id and run_id == stop_before_run_id:
            break
        run = _server.runs.get(run_id)
        if not isinstance(run, dict):
            continue
        group = _runtime_group_for_run(run)
        run_matching = set(group.get("matching_runtime_ids") or [])
        run_preferred = str(group.get("preferred_runtime_id") or "").strip()
        if preferred_token and run_preferred == preferred_token:
            queued_ahead_count += 1
            if len(contender_ids) < 5:
                contender_ids.append(run_id)
            continue
        if matching_set and run_matching and run_matching.intersection(matching_set):
            queued_ahead_count += 1
            if len(contender_ids) < 5:
                contender_ids.append(run_id)

    return {
        "queued_ahead_count": queued_ahead_count,
        "contender_run_ids": contender_ids,
    }


def _capacity_wait_band(queued_ahead_count: int, busy_runtime_count: int) -> str:
    if busy_runtime_count <= 0:
        return "unknown"
    load = queued_ahead_count / max(1, busy_runtime_count)
    if load <= 0.5:
        return "short"
    if load <= 2:
        return "moderate"
    return "long"


def _best_online_preferred_runtime(preferred_runtime_ids: List[str]) -> Optional[str]:
    _init()
    if not preferred_runtime_ids:
        return None
    now = _server._utc_now()
    runtime_snapshot = _merged_worker_registry_snapshot()
    for runtime_id in preferred_runtime_ids:
        record = runtime_snapshot.get(runtime_id) if isinstance(runtime_snapshot.get(runtime_id), dict) else None
        if not isinstance(record, dict):
            continue
        if not _is_worker_online(record, now):
            continue
        return runtime_id
    return None


def _issue_runtime_session(record: Dict[str, Any]) -> Dict[str, Any]:
    _init()
    return machine_lease_service.issue_machine_session(
        record,
        token_factory=_server.secrets.token_urlsafe,
        hash_token_fn=lambda token: hashlib.sha256(token.encode("utf-8")).hexdigest(),
        issued_at=_server._utc_now_iso(),
    )


def _touch_runtime_session(record: Dict[str, Any]) -> None:
    _init()
    machine_lease_service.touch_machine_session(record, touched_at=_server._utc_now_iso())


def _assert_runtime_session(runtime_id: str, session_token: Optional[str], *, instance_id: Optional[str] = None) -> Dict[str, Any]:
    _init()
    with _server.LOCAL_QUEUE_LOCK:
        existing = _server.LOCAL_WORKER_REGISTRY.get(runtime_id) if isinstance(_server.LOCAL_WORKER_REGISTRY.get(runtime_id), dict) else None
    if not isinstance(existing, dict):
        _hydrate_worker_from_durable_registry(runtime_id)
    next_record = machine_lease_service.assert_machine_session(
        runtime_id,
        session_token,
        machine_registry=_server.LOCAL_WORKER_REGISTRY,
        machine_registry_lock=_server.LOCAL_QUEUE_LOCK,
        instance_id=instance_id,
        hash_token_fn=lambda token: hashlib.sha256(token.encode("utf-8")).hexdigest(),
        touch_machine_session_fn=_touch_runtime_session,
        enforce_scope=str(os.getenv("ORION_ENFORCE_RUNTIME_SESSION_SCOPE", "1")).strip().lower() not in {"0", "false", "no", "off"},
    )
    return next_record


def _upsert_runtime_registration(
    runtime_id: str,
    *,
    runtime_type: str = "local",
    display_name: Optional[str] = None,
    platform: Optional[str] = None,
    policy_mode: Optional[str] = None,
    capabilities: Optional[List[str]] = None,
    execution_targets: Optional[List[str]] = None,
    instance_id: Optional[str] = None,
    capability_digest: Optional[str] = None,
    prewarm_state: Optional[str] = None,
    warm_pool: Optional[str] = None,
    permission_probe: Optional[Dict[str, Dict[str, Any]]] = None,
    runtime_role: Optional[str] = None,
    install_id: Optional[str] = None,
    specialist_key: Optional[str] = None,
    summary_channel: Optional[str] = None,
    artifact_channel: Optional[str] = None,
    local_private_memory_only: Optional[bool] = None,
    lifecycle_state: Optional[str] = None,
    lifecycle_reason: Optional[str] = None,
    note: Optional[str] = None,
    summary_text: Optional[str] = None,
    artifacts: Optional[List[Dict[str, Any]]] = None,
    health_state: Optional[str] = None,
) -> Dict[str, Any]:
    _init()
    worker = str(runtime_id or "").strip()
    if not worker:
        return
    with _server.LOCAL_QUEUE_LOCK:
        previous = _server.LOCAL_WORKER_REGISTRY.get(worker) if isinstance(_server.LOCAL_WORKER_REGISTRY.get(worker), dict) else {}
        record = machine_lease_service.build_runtime_registration_record(
            worker,
            previous_record=previous,
            tenant_id=previous.get("tenant_id"),
            workspace_id=previous.get("workspace_id"),
            runtime_type=runtime_type,
            display_name=display_name,
            platform=platform,
            policy_mode=(
                policy_mode
                or previous.get("policy_mode")
                or _server.ORION_RUNTIME_POLICY_MODE_DEFAULT
            ),
            capabilities=capabilities,
            execution_targets=execution_targets,
            instance_id=instance_id,
            capability_digest=capability_digest,
            prewarm_state=prewarm_state,
            warm_pool=warm_pool,
            lease_seconds=_server.ORION_LOCAL_LEASE_SECONDS,
            now_iso=_server._utc_now_iso(),
            normalize_policy_mode_fn=_server.normalize_policy_mode,
            capability_digest_fn=_capability_digest,
        )
        _apply_runtime_identity_fields(
            record,
            runtime_role=runtime_role,
            install_id=install_id,
            specialist_key=specialist_key,
            summary_channel=summary_channel,
            artifact_channel=artifact_channel,
            local_private_memory_only=local_private_memory_only,
        )
        session = _issue_runtime_session(record)
        _set_runtime_lifecycle_state(
            record,
            lifecycle_state or str(record.get("lifecycle_state") or "registered"),
            reason=lifecycle_reason,
        )
        if note is not None:
            record["note"] = str(note or "").strip()[:280] or None
        _apply_permission_probe(record, permission_probe)
        _apply_runtime_surface_hooks(
            record,
            summary_text=summary_text,
            artifacts=artifacts,
            health_state=health_state,
        )
        _server.LOCAL_WORKER_REGISTRY[worker] = record
    _persist_local_runtime_state()
    _sync_durable_fleet_worker(record, heartbeat_seen=True)
    _emit_machine_outbox_event("runtime_registered", record)
    return {
        "runtime_id": worker,
        "machine_id": record.get("machine_id") or worker,
        "instance_id": record.get("instance_id"),
        "capability_digest": record.get("capability_digest"),
        **session,
    }


def create_machine_enrollment_intent(
    *,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    machine_id: Optional[str] = None,
    runtime_type: str = "local_companion",
    display_name: Optional[str] = None,
    platform: Optional[str] = None,
    policy_mode: Optional[str] = None,
    capabilities: Optional[List[str]] = None,
    execution_targets: Optional[List[str]] = None,
    note: Optional[str] = None,
    machine_enrollment_scope: Optional[str] = None,
) -> Dict[str, Any]:
    _init()
    runtime_id = str(machine_id or "").strip() or f"machine-{uuid.uuid4().hex[:8]}"
    token = _server.secrets.token_urlsafe(24)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now_iso = _server._utc_now_iso()
    with _server.LOCAL_QUEUE_LOCK:
        previous = _server.LOCAL_WORKER_REGISTRY.get(runtime_id) if isinstance(_server.LOCAL_WORKER_REGISTRY.get(runtime_id), dict) else {}
        record = machine_lease_service.build_runtime_registration_record(
            runtime_id,
            previous_record=previous,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            runtime_type=runtime_type,
            display_name=display_name,
            platform=platform,
            policy_mode=(policy_mode or previous.get("policy_mode") or _server.ORION_RUNTIME_POLICY_MODE_DEFAULT),
            capabilities=capabilities,
            execution_targets=execution_targets or ["local_companion"],
            instance_id=runtime_id,
            capability_digest=None,
            prewarm_state=None,
            warm_pool=None,
            lease_seconds=_server.ORION_LOCAL_LEASE_SECONDS,
            now_iso=now_iso,
            normalize_policy_mode_fn=_server.normalize_policy_mode,
            capability_digest_fn=_capability_digest,
        )
        record["current_run_id"] = None
        record["tenant_id"] = str(tenant_id or previous.get("tenant_id") or "default").strip() or "default"
        record["workspace_id"] = str(workspace_id or previous.get("workspace_id") or "default").strip() or "default"
        record["machine_enrollment_scope"] = str(
            machine_enrollment_scope or previous.get("machine_enrollment_scope") or "workspace"
        ).strip() or "workspace"
        record["note"] = str(note or "machine_enrollment_requested")[:280]
        record["enrollment_token_hash"] = token_hash
        record["bootstrap_error"] = None
        _set_machine_control_state(record, "active", now_iso=now_iso)
        _set_enrollment_state(record, "requested", now_iso=now_iso)
        _set_enrollment_state(record, "awaiting_local_acceptance", now_iso=now_iso)
        _server.LOCAL_WORKER_REGISTRY[runtime_id] = record
    _persist_local_runtime_state()
    _sync_durable_fleet_worker(record, heartbeat_seen=False)
    _emit_machine_outbox_event("enrollment_requested", record)
    return {
        "ok": True,
        "machine_id": runtime_id,
        "tenant_id": str(record.get("tenant_id") or "default").strip() or "default",
        "workspace_id": str(record.get("workspace_id") or "default").strip() or "default",
        "token": token,
        "runtime_url": _machine_runtime_base_url(),
        "worker_config": {
            "worker_id": runtime_id,
            "tenant_id": str(record.get("tenant_id") or "default").strip() or "default",
            "workspace_id": str(record.get("workspace_id") or "default").strip() or "default",
            "runtime_type": runtime_type,
            "display_name": str(display_name or runtime_id).strip() or runtime_id,
            "execution_targets": list(execution_targets or ["local_companion"]),
            "policy_mode": str(policy_mode or _server.ORION_RUNTIME_POLICY_MODE_DEFAULT),
            "machine_enrollment_scope": str(record.get("machine_enrollment_scope") or "workspace").strip() or "workspace",
        },
    }


def handle_bootstrap_enrolled_local_companion_runtime(
    runtime_id: str,
    *,
    enrollment_token: str,
    runtime_type: str = "local_companion",
    display_name: Optional[str] = None,
    platform: Optional[str] = None,
    policy_mode: Optional[str] = None,
    capabilities: Optional[List[str]] = None,
    execution_targets: Optional[List[str]] = None,
    instance_id: Optional[str] = None,
    capability_digest: Optional[str] = None,
    prewarm_state: Optional[str] = None,
    warm_pool: Optional[str] = None,
    permission_probe: Optional[Dict[str, Dict[str, Any]]] = None,
    runtime_role: Optional[str] = None,
    install_id: Optional[str] = None,
    specialist_key: Optional[str] = None,
    summary_channel: Optional[str] = None,
    artifact_channel: Optional[str] = None,
    local_private_memory_only: Optional[bool] = None,
    note: Optional[str] = None,
    current_run_id: Optional[str] = None,
    summary_text: Optional[str] = None,
    artifacts: Optional[List[Dict[str, Any]]] = None,
    health_state: Optional[str] = None,
) -> Dict[str, Any]:
    _init()
    runtime_token = str(runtime_id or "").strip()
    if not runtime_token:
        raise HTTPException(status_code=400, detail="runtime_id is required.")
    _hydrate_worker_from_durable_registry(runtime_token)
    with _server.LOCAL_QUEUE_LOCK:
        record = _server.LOCAL_WORKER_REGISTRY.get(runtime_token) if isinstance(_server.LOCAL_WORKER_REGISTRY.get(runtime_token), dict) else None
        if not isinstance(record, dict):
            raise HTTPException(status_code=404, detail="Machine not found.")
        _assert_enrollment_token(record, enrollment_token)
        control_state = _machine_control_state(record)
        if control_state == "revoked":
            raise HTTPException(status_code=409, detail="Revoked runtimes cannot be bootstrapped.")
        if control_state == "suspended":
            raise HTTPException(status_code=409, detail="Suspended runtimes must be resumed before bootstrap.")
        record["connection_mode"] = "platform_relay"
        _set_enrollment_state(record, "registering")
        _server.LOCAL_WORKER_REGISTRY[runtime_token] = record
    _persist_local_runtime_state()
    _sync_durable_fleet_worker(record, heartbeat_seen=False)
    _emit_machine_outbox_event("bootstrap_started", record)
    try:
        registration = handle_register_local_cluster_runtime(
            runtime_token,
            runtime_type=runtime_type,
            display_name=display_name,
            platform=platform,
            policy_mode=policy_mode,
            capabilities=capabilities,
            execution_targets=execution_targets or ["local_companion"],
            instance_id=instance_id,
            capability_digest=capability_digest,
            prewarm_state=prewarm_state,
            warm_pool=warm_pool,
            permission_probe=permission_probe,
            runtime_role=runtime_role,
            install_id=install_id,
            specialist_key=specialist_key,
            summary_channel=summary_channel,
            artifact_channel=artifact_channel,
            local_private_memory_only=local_private_memory_only,
            note=note or "companion_platform_relay_bootstrap",
            summary_text=summary_text,
            artifacts=artifacts,
            health_state=health_state,
        )
        runtime = handle_start_local_runtime(
            runtime_token,
            LocalWorkerHeartbeatPayload(
                current_run_id=current_run_id,
                note=note or "companion_platform_relay_bootstrap",
                permission_probe=dict(permission_probe or {}),
                runtime_role=runtime_role,
                install_id=install_id,
                specialist_key=specialist_key,
                summary_channel=summary_channel,
                artifact_channel=artifact_channel,
                local_private_memory_only=local_private_memory_only,
                summary_text=summary_text,
                artifacts=artifacts,
                health_state=health_state or "healthy",
            ),
        )
    except Exception as exc:
        with _server.LOCAL_QUEUE_LOCK:
            record = _server.LOCAL_WORKER_REGISTRY.get(runtime_token) if isinstance(_server.LOCAL_WORKER_REGISTRY.get(runtime_token), dict) else None
            if isinstance(record, dict):
                _set_enrollment_state(record, "failed", error=str(exc))
                _server.LOCAL_WORKER_REGISTRY[runtime_token] = record
        _persist_local_runtime_state()
        if isinstance(record, dict):
            _sync_durable_fleet_worker(record, heartbeat_seen=False)
            _emit_machine_outbox_event("bootstrap_failed", record, error=str(exc))
        raise
    with _server.LOCAL_QUEUE_LOCK:
        record = _server.LOCAL_WORKER_REGISTRY.get(runtime_token) if isinstance(_server.LOCAL_WORKER_REGISTRY.get(runtime_token), dict) else None
        if isinstance(record, dict):
            record["connection_mode"] = "platform_relay"
            record["enrollment_token_hash"] = None
            _set_enrollment_state(record, "healthy")
            _server.LOCAL_WORKER_REGISTRY[runtime_token] = record
    _persist_local_runtime_state()
    if isinstance(record, dict):
        _sync_durable_fleet_worker(record, heartbeat_seen=True)
        _emit_machine_outbox_event("bootstrap_completed", record)
    return {
        "ok": True,
        "runtime_id": str(registration.get("runtime_id") or runtime_token).strip() or runtime_token,
        "machine_id": str(registration.get("machine_id") or runtime_token).strip() or runtime_token,
        "session_token": registration.get("session_token"),
        "instance_id": registration.get("instance_id"),
        "capability_digest": registration.get("capability_digest"),
        "session_issued_at": registration.get("session_issued_at"),
        "connection_mode": "platform_relay",
        "runtime": runtime.get("runtime"),
    }


def update_machine_enrollment_state(
    machine_id: str,
    *,
    enrollment_token: Optional[str] = None,
    state: str,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    _init()
    runtime_id = str(machine_id or "").strip()
    if not runtime_id:
        raise HTTPException(status_code=400, detail="machine_id is required.")
    with _server.LOCAL_QUEUE_LOCK:
        record = _server.LOCAL_WORKER_REGISTRY.get(runtime_id) if isinstance(_server.LOCAL_WORKER_REGISTRY.get(runtime_id), dict) else None
        if not isinstance(record, dict):
            raise HTTPException(status_code=404, detail="Machine not found.")
        _assert_enrollment_token(record, enrollment_token)
        _set_enrollment_state(record, state, error=error)
        _server.LOCAL_WORKER_REGISTRY[runtime_id] = record
    _persist_local_runtime_state()
    _sync_durable_fleet_worker(record, heartbeat_seen=False)
    _emit_machine_outbox_event("enrollment_state_updated", record, error=error)
    return {"ok": True, "machine_id": runtime_id, "enrollment_state": str(state or "").strip().lower()}


def complete_machine_bootstrap(
    machine_id: str,
    *,
    enrollment_token: Optional[str] = None,
) -> Dict[str, Any]:
    _init()
    runtime_id = str(machine_id or "").strip()
    if not runtime_id:
        raise HTTPException(status_code=400, detail="machine_id is required.")
    with _server.LOCAL_QUEUE_LOCK:
        record = _server.LOCAL_WORKER_REGISTRY.get(runtime_id) if isinstance(_server.LOCAL_WORKER_REGISTRY.get(runtime_id), dict) else None
        if not isinstance(record, dict):
            raise HTTPException(status_code=404, detail="Machine not found.")
        _assert_enrollment_token(record, enrollment_token)
        if not _is_worker_online(record, _server._utc_now()):
            raise HTTPException(status_code=409, detail="Machine worker has not heartbeated yet.")
        _set_enrollment_state(record, "healthy")
        record["enrollment_token_hash"] = None
        _server.LOCAL_WORKER_REGISTRY[runtime_id] = record
    _persist_local_runtime_state()
    _sync_durable_fleet_worker(record, heartbeat_seen=False)
    _emit_machine_outbox_event("bootstrap_completed", record)
    status_payload = handle_get_local_workers_status()
    items = status_payload.get("items") if isinstance(status_payload.get("items"), list) else []
    machine = next(
        (
            item
            for item in items
            if isinstance(item, dict)
            and str(item.get("machine_id") or item.get("runtime_id") or "").strip() == runtime_id
        ),
        None,
    )
    return {"ok": True, "machine_id": runtime_id, "machine": machine}


def _is_worker_online(record: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    _init()
    status = str(record.get("status") or "").strip().lower()
    lifecycle_state = str(record.get("lifecycle_state") or "").strip().lower()
    if status in {"offline", "failed", "stopped", "revoked"}:
        return False
    if lifecycle_state in {"stopped", "revoked"}:
        return False
    if _machine_control_state(record) == "revoked":
        return False
    ref = now or _server._utc_now()
    seen_at = _server._parse_utc_ts(record.get("last_seen_at"))
    if seen_at is None:
        return False
    lease_seconds = int(record.get("lease_seconds") or _server.ORION_LOCAL_LEASE_SECONDS)
    delta = (ref - seen_at).total_seconds()
    return delta <= _worker_online_window_seconds(lease_seconds)


def _cleanup_stale_local_claims() -> List[str]:
    _init()
    global _LOCAL_CLAIM_CLEANUP_NEXT_AT_MONOTONIC
    now_monotonic = _monotonic()
    with _LOCAL_CLAIM_CLEANUP_LOCK:
        if now_monotonic < _LOCAL_CLAIM_CLEANUP_NEXT_AT_MONOTONIC:
            return []
        _LOCAL_CLAIM_CLEANUP_NEXT_AT_MONOTONIC = now_monotonic + LOCAL_QUEUE_STALE_CLAIM_CLEANUP_INTERVAL_SECONDS

    for item in run_state_repository.sync_list_expired_local_claims() or []:
        if not isinstance(item, dict):
            continue
        run_id = str(item.get("run_id") or "").strip()
        if not run_id:
            continue
        lease_id = str(item.get("lease_id") or "").strip() or None
        released = run_state_repository.sync_release_claim(
            run_id,
            lease_id=lease_id,
        )
        if not released:
            continue
        with _server.LOCAL_QUEUE_LOCK:
            claim = _server.LOCAL_CLAIMED_RUNS.get(run_id)
            if (
                isinstance(claim, dict)
                and str(claim.get("lease_id") or "").strip()
                and str(claim.get("lease_id") or "").strip() == str(lease_id or "").strip()
            ):
                _server.LOCAL_CLAIMED_RUNS.pop(run_id, None)

    def _schedule_restored_run_resume(run_id: str, run: Dict[str, Any]) -> bool:
        try:
            from server_modules import runtime_runs_api

            return bool(runtime_runs_api._schedule_restored_run_resume(run_id, run))
        except Exception:
            return False

    return machine_lease_service.cleanup_stale_machine_leases(
        now=_server._utc_now(),
        local_queue_lock=_server.LOCAL_QUEUE_LOCK,
        local_pending_run_ids=_server.LOCAL_PENDING_RUN_IDS,
        claimed_runs=_server.LOCAL_CLAIMED_RUNS,
        worker_registry=_server.LOCAL_WORKER_REGISTRY,
        runs_by_id=_server.runs,
        parse_utc_ts_fn=_server._parse_utc_ts,
        utc_now_iso_fn=_server._utc_now_iso,
        persist_local_runtime_state_fn=_persist_local_runtime_state,
        emit_log_fn=_server.emit_log,
        set_run_status_fn=_server.set_run_status,
        schedule_restored_run_resume_fn=_schedule_restored_run_resume,
        checkpoint_recovery_max_auto_retries=LOCAL_CHECKPOINT_RECOVERY_MAX_AUTO_RETRIES,
        checkpoint_recovery_backoff_seconds=LOCAL_CHECKPOINT_RECOVERY_BACKOFF_SECONDS,
        max_queue_retry_attempts=LOCAL_QUEUE_MAX_AUTO_RETRIES,
        queue_retry_backoff_seconds=LOCAL_QUEUE_RETRY_BACKOFF_SECONDS,
        append_dead_letter_fn=run_state_repository.sync_append_local_queue_dead_letter,
        local_worker_lost_timeout_seconds=LOCAL_RUN_WORKER_LOST_TIMEOUT_SECONDS,
        default_lease_seconds=_server.ORION_LOCAL_LEASE_SECONDS,
    )


def _resume_due_checkpoint_recoveries() -> List[str]:
    _init()
    now = _server._utc_now()
    resumed_run_ids: List[str] = []
    for run_id, run in list(_server.runs.items()):
        if not isinstance(run, dict):
            continue
        if str(run.get("status") or "").strip().lower() != "waiting_for_input":
            continue
        if bool(run.get("_resume_after_confirmation_scheduled")):
            continue
        checkpoint = browser_checkpoint_service.browser_checkpoint_from_run(run)
        if not checkpoint:
            continue
        context = run.get("context") if isinstance(run.get("context"), dict) else {}
        metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
        if str(metadata.get("local_worker_recovery_reason") or "").strip().lower() != "worker_lost":
            continue
        if bool(metadata.get("local_worker_recovery_manual_confirmation_required")):
            continue
        if bool(metadata.get("local_worker_recovery_auto_retry_exhausted")):
            continue
        selected_target = str(
            metadata.get("execution_target_selected")
            or metadata.get("execution_target")
            or ""
        ).strip().lower()
        if selected_target not in {"local", "local_companion"}:
            continue
        if isinstance(run.get("pending_confirmation"), dict) and run.get("pending_confirmation"):
            continue
        next_retry_at = _server._parse_utc_ts(metadata.get("local_worker_recovery_next_retry_at"))
        if next_retry_at is not None and now < next_retry_at:
            continue
        try:
            from server_modules import runtime_runs_api

            resumed = bool(runtime_runs_api._schedule_restored_run_resume(run_id, run))
        except Exception:
            resumed = False
        if not resumed:
            continue
        metadata["local_worker_recovery_next_retry_at"] = None
        metadata["local_worker_recovery_backoff_seconds"] = 0
        metadata["local_worker_recovery_last_resume_scheduled_at"] = _server._utc_now_iso()
        context["metadata"] = metadata
        run["context"] = context
        resumed_run_ids.append(run_id)
        log_queue = run.get("logs")
        if log_queue is not None:
            _server.emit_log(
                log_queue,
                "info",
                "Automatic checkpoint recovery resumed after backoff.",
                event="local_resume_scheduled_after_backoff",
                data={
                    "run_id": run_id,
                    "attempt_count": int(metadata.get("local_worker_recovery_attempt_count") or 0),
                    **browser_checkpoint_service.checkpoint_summary(checkpoint),
                },
            )
    return resumed_run_ids


def _apply_cold_boot_checkpoint_recovery_state(run_id: str, run: Dict[str, Any], checkpoint: Dict[str, Any], metadata: Dict[str, Any]) -> str:
    checkpoint_summary = browser_checkpoint_service.checkpoint_summary(checkpoint)
    checkpoint_next_action_index = checkpoint_summary.get("next_action_index")
    checkpoint_session_profile = checkpoint_summary.get("session_profile")
    recovery_reason = str(metadata.get("local_worker_recovery_reason") or "").strip().lower()
    attempt_count = int(metadata.get("local_worker_recovery_attempt_count") or 0)
    max_auto_retries = int(
        metadata.get("local_worker_recovery_max_auto_retries")
        or attempt_count
        or LOCAL_CHECKPOINT_RECOVERY_MAX_AUTO_RETRIES
    )
    backoff_seconds = max(0, int(metadata.get("local_worker_recovery_backoff_seconds") or 0))
    next_retry_at = metadata.get("local_worker_recovery_next_retry_at")
    manual_confirmation_required = bool(metadata.get("local_worker_recovery_manual_confirmation_required"))
    auto_retry_exhausted = bool(metadata.get("local_worker_recovery_auto_retry_exhausted"))

    run["status"] = "waiting_for_input"
    machine_lease_service.clear_active_machine_lease_binding(run)
    metadata["browser_resume_supported"] = browser_checkpoint_service.browser_resume_supported(
        {"browser_checkpoint": checkpoint},
        metadata,
    )
    metadata["resume_ready"] = True
    metadata["cold_boot_recovered"] = True

    if recovery_reason == "worker_lost" and (manual_confirmation_required or auto_retry_exhausted):
        run["result"] = (
            "Local operator recovered after runtime restart. Automatic checkpoint recovery remains paused after repeated worker loss."
        )
        run["result_data"] = {
            "summary": run["result"],
            "error": "local_worker_recovery_exhausted",
            "resume_available": True,
            "manual_confirmation_required": True,
            "attempt_count": attempt_count,
            "max_auto_retries": max_auto_retries,
            "next_action_index": checkpoint_next_action_index,
            "session_profile": checkpoint_session_profile,
        }
        metadata["local_worker_recovery_auto_retry_exhausted"] = True
        metadata["local_worker_recovery_manual_confirmation_required"] = True
        event = "local_cold_boot_recovered_manual_gate"
        message = run["result"]
    elif recovery_reason == "worker_lost":
        delayed_retry = bool(next_retry_at or backoff_seconds > 0)
        run["result"] = (
            "Local operator recovered after runtime restart. Automatic checkpoint recovery remains delayed before retry."
            if delayed_retry
            else "Local operator recovered after runtime restart. Automatic checkpoint recovery remains available."
        )
        run["result_data"] = {
            "summary": run["result"],
            "error": "local_worker_lost_recoverable",
            "resume_available": True,
            "attempt_count": attempt_count,
            "max_auto_retries": max_auto_retries,
            "retry_backoff_seconds": backoff_seconds,
            "next_retry_at": next_retry_at,
            "next_action_index": checkpoint_next_action_index,
            "session_profile": checkpoint_session_profile,
        }
        metadata["local_worker_recovery_manual_confirmation_required"] = False
        metadata["local_worker_recovery_auto_retry_exhausted"] = False
        event = "local_cold_boot_recovered_backoff" if delayed_retry else "local_cold_boot_recovered"
        message = run["result"]
    else:
        run["result"] = "Local operator paused at saved checkpoint after runtime restart."
        run["result_data"] = {
            "summary": run["result"],
            "resume_available": True,
            "error": "local_worker_orphaned_recovered",
            "next_action_index": checkpoint_next_action_index,
            "session_profile": checkpoint_session_profile,
        }
        event = "local_cold_boot_recovered"
        message = "Recovered local run from durable checkpoint after runtime restart."

    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    context["metadata"] = metadata
    run["context"] = context

    log_queue = run.get("logs")
    if log_queue is not None:
        _server.emit_log(
            log_queue,
            "warn",
            message,
            event=event,
            data={
                "run_id": run_id,
                "attempt_count": attempt_count,
                "max_auto_retries": max_auto_retries,
                "retry_backoff_seconds": backoff_seconds,
                "next_retry_at": next_retry_at,
                "manual_confirmation_required": bool(metadata.get("local_worker_recovery_manual_confirmation_required")),
                "next_action_index": checkpoint_next_action_index,
                "session_profile": checkpoint_session_profile,
            },
        )
    return event


def recover_orphaned_local_runs_on_startup() -> List[str]:
    global _COLD_BOOT_RECOVERY_DONE
    _init()
    if _COLD_BOOT_RECOVERY_DONE:
        return []

    recovered: List[str] = []
    now = _server._utc_now()
    with _server.LOCAL_QUEUE_LOCK:
        active_claims = {
            run_id: dict(claim)
            for run_id, claim in _server.LOCAL_CLAIMED_RUNS.items()
            if isinstance(claim, dict)
        }
        online_workers = {
            worker_id
            for worker_id, record in _server.LOCAL_WORKER_REGISTRY.items()
            if isinstance(record, dict) and _is_worker_online(record, now)
        }
        pending_ids = set(str(run_id) for run_id in _server.LOCAL_PENDING_RUN_IDS)
    terminal_states = {"completed", "failed", "timeout", "stopped", "cancelled"}

    for run_id, run in list(_server.runs.items()):
        if not isinstance(run, dict):
            continue
        status = str(run.get("status") or "").strip().lower()
        if status in terminal_states:
            continue
        checkpoint = run.get("browser_checkpoint") if isinstance(run.get("browser_checkpoint"), dict) else {}
        context = run.get("context") if isinstance(run.get("context"), dict) else {}
        metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
        selected_target = str(
            metadata.get("execution_target_selected")
            or metadata.get("execution_target")
            or ""
        ).strip().lower()
        if selected_target not in {"local", "local_companion"} and status not in {"running_local", "queued_local", "starting", "waiting_for_input"}:
            continue
        claim = active_claims.get(run_id) if isinstance(active_claims.get(run_id), dict) else {}
        worker_id = str(claim.get("worker_id") or run.get("local_worker_id") or "").strip()
        has_live_worker = bool(worker_id and worker_id in online_workers)
        if status == "waiting_for_input":
            if checkpoint:
                recovered.append(run_id)
            continue
        if has_live_worker or run_id in pending_ids:
            continue
        if not checkpoint:
            continue
        _apply_cold_boot_checkpoint_recovery_state(run_id, run, checkpoint, metadata)
        recovered.append(run_id)
        _server._persist_live_run_state(run_id, run)

    if recovered:
        machine_lease_service.reconcile_recovered_machine_leases(
            recovered,
            local_queue_lock=_server.LOCAL_QUEUE_LOCK,
            local_pending_run_ids=_server.LOCAL_PENDING_RUN_IDS,
            local_claimed_runs=_server.LOCAL_CLAIMED_RUNS,
            persist_local_runtime_state_fn=_persist_local_runtime_state,
        )
    _COLD_BOOT_RECOVERY_DONE = True
    return recovered


def recover_expired_worker_leases_on_startup() -> List[str]:
    global _EXPIRED_LEASE_RECOVERY_DONE
    _init()
    if _EXPIRED_LEASE_RECOVERY_DONE:
        return []
    from server_modules import run_state_repository

    recovered: List[str] = []
    expired_claims = run_state_repository.sync_list_expired_local_claims()
    for item in expired_claims or []:
        if not isinstance(item, dict):
            continue
        run_id = str(item.get("run_id") or "").strip()
        if not run_id:
            continue
        released = run_state_repository.sync_release_claim(
            run_id,
            lease_id=str(item.get("lease_id") or "").strip() or None,
        )
        if not released:
            continue
        with _server.LOCAL_QUEUE_LOCK:
            _server.LOCAL_CLAIMED_RUNS.pop(run_id, None)
        run = _server.runs.get(run_id)
        if not isinstance(run, dict):
            continue
        status = str(run.get("status") or "").strip().lower()
        if status in {"completed", "failed", "timeout", "stopped", "cancelled"}:
            continue
        context = run.get("context") if isinstance(run.get("context"), dict) else {}
        metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
        selected_target = str(
            metadata.get("execution_target_selected")
            or metadata.get("execution_target")
            or ""
        ).strip().lower()
        if selected_target not in {"local", "local_companion"} and status not in {"running_local", "queued_local", "starting"}:
            continue
        machine_lease_service.clear_active_machine_lease_binding(run)
        run["local_worker_id"] = None
        run["local_claimed_at"] = None
        run["local_last_heartbeat_at"] = None
        _server.set_run_status(run_id, "queued_local")
        with _server.LOCAL_QUEUE_LOCK:
            if run_id not in _server.LOCAL_PENDING_RUN_IDS:
                _server.LOCAL_PENDING_RUN_IDS.append(run_id)
        _persist_local_runtime_state()
        log_queue = run.get("logs")
        if log_queue is not None:
            _server.emit_log(
                log_queue,
                "warn",
                "Recovered expired worker lease on startup and requeued the run.",
                event="local_claim_requeued_startup",
                data={
                    "run_id": run_id,
                    "worker_id": str(item.get("worker_id") or "").strip() or None,
                    "ttl_seconds": int(item.get("ttl") or 0),
                    "claimed_at": item.get("claimed_at"),
                },
            )
        recovered.append(run_id)
    _EXPIRED_LEASE_RECOVERY_DONE = True
    return recovered


def _claim_local_run(worker_id: str, required_capabilities: Optional[List[str]] = None) -> Optional[str]:
    _init()
    return worker_dispatch_service.claim_local_run(
        worker_id,
        required_capabilities=required_capabilities,
        local_queue_lock=_server.LOCAL_QUEUE_LOCK,
        pending_run_ids=_server.LOCAL_PENDING_RUN_IDS,
        claimed_runs=_server.LOCAL_CLAIMED_RUNS,
        worker_registry=_server.LOCAL_WORKER_REGISTRY,
        runs_by_id=_server.runs,
        lease_seconds=_server.ORION_LOCAL_LEASE_SECONDS,
        cleanup_stale_local_claims_fn=_cleanup_stale_local_claims,
        ordered_runtime_preferences_for_run_fn=_ordered_runtime_preferences_for_run,
        best_online_preferred_runtime_fn=_best_online_preferred_runtime,
        required_capabilities_for_run_fn=_required_capabilities_for_run,
        normalize_capability_ids_fn=_normalize_capability_ids,
        persist_local_runtime_state_fn=_persist_local_runtime_state,
        mark_local_worker_seen_fn=_mark_local_worker_seen,
        now_iso_fn=_server._utc_now_iso,
        parse_utc_ts_fn=_server._parse_utc_ts,
        now_fn=_server._utc_now,
    )


def _enforce_local_queue_cancel_transition(
    *,
    run_id: str,
    previous_status: str,
    requester_id: str = "operator_local_queue_cleanup",
) -> Dict[str, Any]:
    return machine_lease_service.enforce_queue_transition_decision(
        operation="cancel",
        run_id=run_id,
        requester_id=requester_id,
        lease_holder_id="",
        status=str(previous_status or "queued_local").strip() or "queued_local",
        force=True,
    )


def _local_run_summary(run_id: str, run: Dict[str, Any], claim: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    _init()
    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    required_capabilities = _required_capabilities_for_run(run)
    preferred_runtime_ids = _preferred_runtime_ids_from_metadata(metadata)
    preferred_runtime_id = preferred_runtime_ids[0] if preferred_runtime_ids else None
    preferred_runtime_label = (
        str(metadata.get("execution_target_preferred_runtime_label") or "").strip()
        or str(metadata.get("machine_target") or "").strip()
        or preferred_runtime_id
        or None
    )
    return {
        "run_id": run_id,
        "engine": run.get("engine"),
        "status": run.get("status"),
        "created_at": run.get("created_at"),
        "updated_at": run.get("updated_at"),
        "user_goal": str(context.get("user_goal") or ""),
        "outcome_pack": str(metadata.get("outcome_pack") or ""),
        "agent_role": str(metadata.get("agent_role") or "").strip() or None,
        "agent_role_source": str(metadata.get("agent_role_source") or "").strip() or None,
        "parent_run_id": str(metadata.get("parent_run_id") or "").strip() or None,
        "delegation_root_run_id": str(metadata.get("delegation_root_run_id") or "").strip() or None,
        "delegated_by_run_id": str(metadata.get("delegated_by_run_id") or "").strip() or None,
        "delegated_by_role": str(metadata.get("delegated_by_role") or "").strip() or None,
        "delegation_note": str(metadata.get("delegation_note") or "").strip() or None,
        "required_capabilities": required_capabilities,
        "preferred_runtime_id": preferred_runtime_id,
        "preferred_runtime_label": preferred_runtime_label,
        "machine_target": str(metadata.get("machine_target") or "").strip() or None,
        "workspace_id": str(context.get("workspace_id") or ""),
        "worker_id": str(claim.get("worker_id") or "") if isinstance(claim, dict) else None,
        "claimed_at": claim.get("claimed_at") if isinstance(claim, dict) else None,
        "last_heartbeat_at": claim.get("last_heartbeat_at") if isinstance(claim, dict) else None,
        "lease_seconds": int(claim.get("lease_seconds") or _server.ORION_LOCAL_LEASE_SECONDS) if isinstance(claim, dict) else _server.ORION_LOCAL_LEASE_SECONDS,
    }


def _annotate_local_queue_wait_state(
    summary: Dict[str, Any],
    run: Dict[str, Any],
    runtime_snapshot: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    _init()
    annotated = dict(summary or {})
    now = _server._utc_now()
    preferred_runtime_id = str(annotated.get("preferred_runtime_id") or "").strip() or None
    preferred_runtime_label = str(annotated.get("preferred_runtime_label") or preferred_runtime_id or "").strip() or None
    preferred_record = runtime_snapshot.get(preferred_runtime_id) if preferred_runtime_id else None
    preferred_online = isinstance(preferred_record, dict) and _is_worker_online(preferred_record, now)
    online_items = [record for record in runtime_snapshot.values() if isinstance(record, dict) and _is_worker_online(record, now)]
    online_count = len(online_items)
    queue_reference = _server._parse_utc_ts(annotated.get("updated_at")) or _server._parse_utc_ts(annotated.get("created_at"))
    queue_age_seconds = max(0, int((now - queue_reference).total_seconds())) if queue_reference is not None else None
    stale_after_seconds = max(
        LOCAL_QUEUE_STALE_UNCLAIMED_SECONDS,
        int(annotated.get("lease_seconds") or _server.ORION_LOCAL_LEASE_SECONDS) * 5,
    )
    is_stale = queue_age_seconds is not None and queue_age_seconds >= stale_after_seconds
    queue_state = "queued"
    queue_reason = "Queued for local execution."

    if preferred_runtime_id and not preferred_online:
        queue_state = "waiting_for_preferred_runtime"
        queue_reason = f"Waiting for preferred local runtime {preferred_runtime_label or preferred_runtime_id} to come online."
    elif online_count <= 0:
        queue_state = "waiting_for_worker"
        queue_reason = "No healthy local worker is online."

    if is_stale:
        queue_state = f"stale_{queue_state}"
        queue_reason = f"{queue_reason} The run has been unclaimed longer than the stale queue threshold."

    annotated["queue_age_seconds"] = queue_age_seconds
    annotated["stale_after_seconds"] = stale_after_seconds
    annotated["queue_state"] = queue_state
    annotated["queue_reason"] = queue_reason
    annotated["preferred_runtime_online"] = bool(preferred_online)
    annotated["online_worker_count"] = online_count
    return annotated


def _capability_queue_summary(
    queued_run_ids: List[str],
    online_machine_items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    online_capabilities = sorted(
        {
            capability
            for item in online_machine_items
            if isinstance(item, dict)
            for capability in _normalize_capability_ids(item.get("capabilities"))
        }
    )
    online_capability_set = set(online_capabilities)
    waiting_count = 0
    waiting_items: List[Dict[str, Any]] = []

    for run_id in queued_run_ids:
        run = _server.runs.get(run_id)
        if not isinstance(run, dict):
            continue
        summary = _local_run_summary(run_id, run)
        required_capabilities = list(summary.get("required_capabilities") or [])
        if not required_capabilities:
            continue
        required_set = set(required_capabilities)
        matching_items = [
            item
            for item in online_machine_items
            if isinstance(item, dict)
            and required_set.issubset(set(_normalize_capability_ids(item.get("capabilities"))))
        ]
        available_items = [item for item in matching_items if not bool(item.get("current_run_id"))]
        missing_capabilities = [
            capability
            for capability in required_capabilities
            if capability not in online_capability_set
        ]
        if not missing_capabilities and available_items:
            continue
        waiting_count += 1
        if len(waiting_items) >= 5:
            continue
        preferred_runtime_id = str(summary.get("preferred_runtime_id") or "").strip() or None
        matching_runtime_ids = _runtime_group_for_run(run).get("matching_runtime_ids") or []
        queue_pressure = _queue_pressure_for_runtime_group(
            queued_run_ids,
            list(matching_runtime_ids),
            preferred_runtime_id,
            stop_before_run_id=run_id,
        )
        waiting_state = "missing_capabilities"
        waiting_reason = "No online machine currently reports the required capabilities."
        busy_runtime_labels: List[str] = []
        queued_ahead_count = int(queue_pressure.get("queued_ahead_count") or 0)
        estimated_wait_band = "unknown"
        if not missing_capabilities and matching_items and not available_items:
            waiting_state = "capacity"
            busy_runtime_labels = [
                str(item.get("display_name") or item.get("runtime_id") or item.get("worker_id") or "").strip()
                for item in matching_items
                if str(item.get("display_name") or item.get("runtime_id") or item.get("worker_id") or "").strip()
            ]
            estimated_wait_band = _capacity_wait_band(queued_ahead_count, len(matching_items))
            if busy_runtime_labels:
                waiting_reason = f"Capable machines are online but busy: {', '.join(busy_runtime_labels)}."
            else:
                waiting_reason = "Capable machines are online but currently busy."
            if queued_ahead_count > 0:
                waiting_reason = f"{waiting_reason} {queued_ahead_count} similar local run{'s are' if queued_ahead_count != 1 else ' is'} ahead in the queue."
        waiting_items.append(
            {
                "run_id": run_id,
                "user_goal": summary.get("user_goal"),
                "outcome_pack": summary.get("outcome_pack"),
                "required_capabilities": required_capabilities,
                "missing_capabilities": missing_capabilities,
                "waiting_state": waiting_state,
                "waiting_reason": waiting_reason,
                "busy_runtime_labels": busy_runtime_labels,
                "queued_ahead_count": queued_ahead_count,
                "estimated_wait_band": estimated_wait_band,
            }
        )

    return {
        "waiting_count": waiting_count,
        "online_capabilities": online_capabilities,
        "items": waiting_items,
    }


def _worker_display_sort_key(item: Dict[str, Any]) -> tuple:
    online_rank = 0 if bool(item.get("online")) else 1
    busy_rank = 1 if item.get("current_run_id") else 0
    trust_state = str(item.get("trust_state") or "").strip().lower()
    if trust_state == "verified":
        trust_rank = 0
    elif trust_state:
        trust_rank = 1
    else:
        trust_rank = 2
    try:
        seen_rank = int(item.get("seconds_since_seen"))
    except Exception:
        seen_rank = 999999
    runtime_id = str(item.get("runtime_id") or item.get("worker_id") or "").strip()
    return (online_rank, busy_rank, trust_rank, seen_rank, runtime_id)


def _runtime_status_item_from_record(
    worker_id: str,
    record: Dict[str, Any],
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    _init()
    ref = now or _server._utc_now()
    lease_seconds = int(record.get("lease_seconds") or _server.ORION_LOCAL_LEASE_SECONDS)
    default_policy_mode = getattr(_server, "ORION_RUNTIME_POLICY_MODE_DEFAULT", "local_default")
    online = _is_worker_online(record, ref)
    control_state = _machine_control_state(record)
    lifecycle_state = str(record.get("lifecycle_state") or "registered").strip().lower() or "registered"
    policy_status = _machine_policy_status(record)
    permission_probe = _normalized_permission_probe(record)
    seen_at = _server._parse_utc_ts(record.get("last_seen_at"))
    since_seen = max(0, int((ref - seen_at).total_seconds())) if seen_at is not None else None
    status = str(record.get("status") or "idle")
    if lifecycle_state == "stopped":
        status = "stopped"
    elif lifecycle_state == "recovering":
        status = "recovering"
    elif control_state == "revoked":
        status = "revoked"
    elif not online:
        status = "offline"

    return {
        "worker_id": worker_id,
        "tenant_id": str(record.get("tenant_id") or "default").strip() or "default",
        "workspace_id": str(record.get("workspace_id") or "default").strip() or "default",
        "machine_id": str(record.get("machine_id") or record.get("runtime_id") or worker_id).strip() or worker_id,
        "runtime_id": record.get("runtime_id") or worker_id,
        "runtime_type": record.get("runtime_type") or "local",
        "connection_mode": str(record.get("connection_mode") or "direct_runtime_api").strip().lower() or "direct_runtime_api",
        "runtime_role": str(record.get("runtime_role") or "generic").strip().lower() or "generic",
        "install_id": str(record.get("install_id") or "").strip() or None,
        "specialist_key": str(record.get("specialist_key") or "").strip() or None,
        "display_name": record.get("display_name") or worker_id,
        "platform": record.get("platform"),
        "policy_mode": record.get("policy_mode") or default_policy_mode,
        "capabilities": list(record.get("capabilities") or []),
        "execution_targets": list(record.get("execution_targets") or []),
        "trust_state": record.get("trust_state") or "unverified",
        "instance_id": record.get("instance_id"),
        "capability_digest": record.get("capability_digest"),
        "registered_at": record.get("registered_at"),
        "last_registered_at": record.get("last_registered_at"),
        "session_issued_at": record.get("session_issued_at"),
        "status": status,
        "online": online,
        "current_run_id": record.get("current_run_id"),
        "current_lease_holder": (
            f"Run {str(record.get('current_run_id') or '').strip()[:8]}"
            if str(record.get("current_run_id") or "").strip()
            else None
        ),
        "last_seen_at": record.get("last_seen_at"),
        "seconds_since_seen": since_seen,
        "lease_seconds": lease_seconds,
        "note": record.get("note"),
        "permission_probe": permission_probe,
        "permission_probe_updated_at": record.get("permission_probe_updated_at"),
        "lifecycle_state": lifecycle_state,
        "lifecycle_state_updated_at": record.get("lifecycle_state_updated_at"),
        "lifecycle_reason": record.get("lifecycle_reason"),
        "started_at": record.get("started_at"),
        "last_started_at": record.get("last_started_at"),
        "stopped_at": record.get("stopped_at"),
        "recovery_requested_at": record.get("recovery_requested_at"),
        "last_recovered_at": record.get("last_recovered_at"),
        "last_recovered_run_ids": list(record.get("last_recovered_run_ids") or []),
        "last_resumed_run_ids": list(record.get("last_resumed_run_ids") or []),
        "health_state": record.get("health_state") or "unknown",
        "health_updated_at": record.get("health_updated_at"),
        "summary_channel": record.get("summary_channel"),
        "artifact_channel": record.get("artifact_channel"),
        "local_private_memory_only": bool(record.get("local_private_memory_only", True)),
        "last_summary": record.get("last_summary"),
        "last_summary_at": record.get("last_summary_at"),
        "last_artifacts": list(record.get("last_artifacts") or []),
        "last_artifact_at": record.get("last_artifact_at"),
        "control_state": control_state,
        "control_state_updated_at": record.get("control_state_updated_at"),
        "interrupt_requested_at": record.get("interrupt_requested_at"),
        "interrupt_reason": record.get("interrupt_reason"),
        "suspended_at": record.get("suspended_at"),
        "suspended_reason": record.get("suspended_reason"),
        "revoked_at": record.get("revoked_at"),
        "revoked_reason": record.get("revoked_reason"),
        "safe_mode_status": policy_status.get("safe_mode"),
        "kill_switch_status": policy_status.get("kill_switch"),
        "enrollment_state": record.get("enrollment_state"),
        "enrollment_requested_at": record.get("enrollment_requested_at"),
        "enrollment_updated_at": record.get("enrollment_updated_at"),
        "bootstrap_error": record.get("bootstrap_error"),
        "machine_enrollment_scope": record.get("machine_enrollment_scope") or "workspace",
        "prewarm_state": record.get("prewarm_state"),
        "warm_pool": record.get("warm_pool"),
        "queue_shard": record.get("queue_shard"),
    }


def _outstanding_scope_counts(
    queued_ids: List[str],
    claimed_map: Dict[str, Dict[str, Any]],
) -> tuple[Dict[str, int], Dict[str, int]]:
    workspace_counts: Dict[str, int] = {}
    specialist_counts: Dict[str, int] = {}
    for run_id in queued_ids:
        run = _server.runs.get(run_id)
        if not isinstance(run, dict):
            continue
        scope = machine_lease_service._run_scope_from_payload(run)
        workspace_counts[scope["workspace_id"]] = workspace_counts.get(scope["workspace_id"], 0) + 1
        specialist_counts[scope["specialist_key"]] = specialist_counts.get(scope["specialist_key"], 0) + 1
    for run_id in claimed_map:
        run = _server.runs.get(run_id)
        if not isinstance(run, dict):
            continue
        scope = machine_lease_service._run_scope_from_payload(run)
        workspace_counts[scope["workspace_id"]] = workspace_counts.get(scope["workspace_id"], 0) + 1
        specialist_counts[scope["specialist_key"]] = specialist_counts.get(scope["specialist_key"], 0) + 1
    return workspace_counts, specialist_counts


def _queued_retry_after_seconds(queued_ids: List[str]) -> Optional[int]:
    retry_after: Optional[int] = None
    now = _server._utc_now()
    for run_id in queued_ids:
        run = _server.runs.get(run_id)
        if not isinstance(run, dict):
            continue
        context = run.get("context") if isinstance(run.get("context"), dict) else {}
        metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
        next_retry_at = _server._parse_utc_ts(metadata.get("local_queue_next_retry_at"))
        if next_retry_at is None:
            continue
        seconds = max(0, int((next_retry_at - now).total_seconds()))
        retry_after = seconds if retry_after is None else min(retry_after, seconds)
    return retry_after


def _sync_fleet_queue_partition_snapshots(
    *,
    queued_ids: List[str],
    claimed_map: Dict[str, Dict[str, Any]],
    worker_items: List[Dict[str, Any]],
    retry_after_seconds: int,
) -> None:
    partitions: Dict[str, Dict[str, Any]] = {}

    def _ensure_partition(scope: Dict[str, str]) -> Dict[str, Any]:
        workspace_id = str(scope.get("workspace_id") or "default").strip() or "default"
        tenant_id = str(scope.get("tenant_id") or "default").strip() or "default"
        specialist_key = str(scope.get("specialist_key") or "workspace-default").strip() or "workspace-default"
        partition_id = machine_lease_service.build_queue_partition_key(workspace_id, specialist_key)
        record = partitions.get(partition_id)
        if isinstance(record, dict):
            return record
        record = {
            "partition_id": partition_id,
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "specialist_key": specialist_key,
            "pending_count": 0,
            "claimed_count": 0,
            "online_workers": 0,
            "busy_workers": 0,
            "idle_workers": 0,
            "prewarmed_workers": 0,
        }
        partitions[partition_id] = record
        return record

    for run_id in queued_ids:
        run = _server.runs.get(run_id)
        if not isinstance(run, dict):
            continue
        scope = machine_lease_service._run_scope_from_payload(run)
        _ensure_partition(scope)["pending_count"] += 1

    for run_id in claimed_map:
        run = _server.runs.get(run_id)
        if not isinstance(run, dict):
            continue
        scope = machine_lease_service._run_scope_from_payload(run)
        _ensure_partition(scope)["claimed_count"] += 1

    workspace_worker_counts: Dict[str, Dict[str, Any]] = {}
    for item in worker_items:
        if not isinstance(item, dict):
            continue
        workspace_id = str(item.get("workspace_id") or "default").strip() or "default"
        bucket = workspace_worker_counts.setdefault(
            workspace_id,
            {
                "tenant_id": str(item.get("tenant_id") or "default").strip() or "default",
                "online_workers": 0,
                "busy_workers": 0,
                "idle_workers": 0,
                "prewarmed_workers": 0,
            },
        )
        if bool(item.get("online")):
            bucket["online_workers"] += 1
            if item.get("current_run_id"):
                bucket["busy_workers"] += 1
            else:
                bucket["idle_workers"] += 1
        if (
            bool(item.get("online"))
            and not item.get("current_run_id")
            and str(item.get("prewarm_state") or "").strip().lower() in {"warm", "ready", "prewarmed"}
        ):
            bucket["prewarmed_workers"] += 1

    if not partitions:
        for workspace_id, counts in workspace_worker_counts.items():
            record = _ensure_partition(
                {
                    "tenant_id": str(counts.get("tenant_id") or "default").strip() or "default",
                    "workspace_id": workspace_id,
                    "specialist_key": "workspace-default",
                }
            )
            record.update(counts)

    for record in partitions.values():
        worker_counts = workspace_worker_counts.get(record["workspace_id"], {})
        record["online_workers"] = int(worker_counts.get("online_workers") or 0)
        record["busy_workers"] = int(worker_counts.get("busy_workers") or 0)
        record["idle_workers"] = int(worker_counts.get("idle_workers") or 0)
        record["prewarmed_workers"] = int(worker_counts.get("prewarmed_workers") or 0)
        pending_count = int(record["pending_count"] or 0)
        claimed_count = int(record["claimed_count"] or 0)
        ready_capacity = int(record["idle_workers"] or 0) + int(record["prewarmed_workers"] or 0)
        if pending_count <= 0 and claimed_count <= 0:
            state = "healthy"
            summary = "Partition idle."
        elif ready_capacity <= 0 and pending_count > 0:
            state = "saturated"
            summary = "Partition backlog is waiting for worker capacity."
        elif pending_count > max(1, ready_capacity * 2):
            state = "saturated"
            summary = "Partition is saturated and backpressure is expected."
        elif pending_count > max(1, ready_capacity):
            state = "strained"
            summary = "Partition backlog is elevated but progressing."
        else:
            state = "healthy"
            summary = "Partition is within normal operating range."
        payload = {
            "summary": summary,
            "workspace_id": record["workspace_id"],
            "specialist_key": record["specialist_key"],
            "pending_count": pending_count,
            "claimed_count": claimed_count,
            "online_workers": int(record["online_workers"] or 0),
            "busy_workers": int(record["busy_workers"] or 0),
            "idle_workers": int(record["idle_workers"] or 0),
            "prewarmed_workers": int(record["prewarmed_workers"] or 0),
        }
        try:
            run_state_repository.sync_upsert_fleet_queue_partition(
                partition_id=record["partition_id"],
                tenant_id=record["tenant_id"],
                workspace_id=record["workspace_id"],
                specialist_key=record["specialist_key"],
                pending_count=pending_count,
                claimed_count=claimed_count,
                online_workers=int(record["online_workers"] or 0),
                busy_workers=int(record["busy_workers"] or 0),
                idle_workers=int(record["idle_workers"] or 0),
                prewarmed_workers=int(record["prewarmed_workers"] or 0),
                state=state,
                retry_after_seconds=max(0, int(retry_after_seconds or 0)),
                payload=payload,
            )
        except Exception:
            continue


def _build_local_fleet_pressure_snapshot(
    *,
    queued_ids: List[str],
    claimed_map: Dict[str, Dict[str, Any]],
    worker_items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    online_workers = len([item for item in worker_items if bool(item.get("online"))])
    busy_workers = len([item for item in worker_items if bool(item.get("online")) and item.get("current_run_id")])
    idle_workers = max(0, online_workers - busy_workers)
    queued_count = len(queued_ids)
    claimed_count = len(claimed_map)
    backlog_per_online_worker = round(queued_count / max(1, online_workers), 2) if online_workers else None
    retry_after_seconds = _queued_retry_after_seconds(queued_ids)
    dead_letter_status = run_state_repository.sync_get_local_queue_dead_letter_status()
    workspace_counts, specialist_counts = _outstanding_scope_counts(queued_ids, claimed_map)
    workspace_hotspots = [
        {"workspace_id": workspace_id, "count": count}
        for workspace_id, count in sorted(workspace_counts.items(), key=lambda item: (-item[1], item[0]))[:5]
    ]
    specialist_hotspots = [
        {"specialist_key": specialist_key, "count": count}
        for specialist_key, count in sorted(specialist_counts.items(), key=lambda item: (-item[1], item[0]))[:5]
    ]

    if queued_count <= 0:
        state = "healthy"
        summary = "No queued local runs."
    elif online_workers <= 0:
        state = "saturated"
        summary = "Queued local runs are waiting for an online worker."
    elif backlog_per_online_worker is not None and backlog_per_online_worker > 4:
        state = "saturated"
        summary = "Local worker demand is saturated and backpressure should be expected."
    elif backlog_per_online_worker is not None and backlog_per_online_worker > 1.5:
        state = "strained"
        summary = "Local worker demand is elevated but still progressing."
    else:
        state = "healthy"
        summary = "Local worker fleet is within normal operating range."

    if retry_after_seconds is None:
        if state == "saturated":
            retry_after_seconds = max(15, int(_server.ORION_LOCAL_LEASE_SECONDS or 30))
        elif state == "strained":
            retry_after_seconds = 10
        else:
            retry_after_seconds = 0

    _sync_fleet_queue_partition_snapshots(
        queued_ids=queued_ids,
        claimed_map=claimed_map,
        worker_items=worker_items,
        retry_after_seconds=retry_after_seconds,
    )

    return {
        "mode": "workspace_specialist_weighted_fifo",
        "state": state,
        "summary": summary,
        "queued_count": queued_count,
        "claimed_count": claimed_count,
        "online_workers": online_workers,
        "busy_workers": busy_workers,
        "idle_workers": idle_workers,
        "backlog_per_online_worker": backlog_per_online_worker,
        "retry_after_seconds": retry_after_seconds,
        "workspace_hotspots": workspace_hotspots,
        "specialist_hotspots": specialist_hotspots,
        "dead_letters": dead_letter_status,
    }


def _build_lightweight_local_fleet_pressure_snapshot(
    *,
    queued_ids: List[str],
    claimed_map: Dict[str, Dict[str, Any]],
    worker_items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    online_workers = len([item for item in worker_items if bool(item.get("online"))])
    busy_workers = len([item for item in worker_items if bool(item.get("online")) and item.get("current_run_id")])
    idle_workers = max(0, online_workers - busy_workers)
    queued_count = len(queued_ids)
    claimed_count = len(claimed_map)
    backlog_per_online_worker = round(queued_count / max(1, online_workers), 2) if online_workers else None
    retry_after_seconds = _queued_retry_after_seconds(queued_ids)

    if queued_count <= 0:
        state = "healthy"
        summary = "No queued local runs."
    elif online_workers <= 0:
        state = "saturated"
        summary = "Queued local runs are waiting for an online worker."
    elif backlog_per_online_worker is not None and backlog_per_online_worker > 4:
        state = "saturated"
        summary = "Local worker demand is saturated and backpressure should be expected."
    elif backlog_per_online_worker is not None and backlog_per_online_worker > 1.5:
        state = "strained"
        summary = "Local worker demand is elevated but still progressing."
    else:
        state = "healthy"
        summary = "Local worker fleet is within normal operating range."

    if retry_after_seconds is None:
        if state == "saturated":
            retry_after_seconds = max(15, int(_server.ORION_LOCAL_LEASE_SECONDS or 30))
        elif state == "strained":
            retry_after_seconds = 10
        else:
            retry_after_seconds = 0

    return {
        "mode": "workspace_specialist_weighted_fifo",
        "state": state,
        "summary": summary,
        "queued_count": queued_count,
        "claimed_count": claimed_count,
        "online_workers": online_workers,
        "busy_workers": busy_workers,
        "idle_workers": idle_workers,
        "backlog_per_online_worker": backlog_per_online_worker,
        "retry_after_seconds": retry_after_seconds,
        "workspace_hotspots": [],
        "specialist_hotspots": [],
        "dead_letters": None,
    }


def build_local_scale_safety_baseline() -> Dict[str, Any]:
    _init()
    _cleanup_stale_local_claims()
    now = _server._utc_now()
    with _server.LOCAL_QUEUE_LOCK:
        queued_ids = list(_server.LOCAL_PENDING_RUN_IDS)
        claimed_map = {
            rid: dict(info)
            for rid, info in _server.LOCAL_CLAIMED_RUNS.items()
            if isinstance(info, dict)
        }
        worker_items = [
            {
                "worker_id": runtime_id,
                "online": _is_worker_online(record, now),
                "current_run_id": record.get("current_run_id"),
            }
            for runtime_id, record in _server.LOCAL_WORKER_REGISTRY.items()
            if isinstance(record, dict)
        ]
    snapshot = _build_lightweight_local_fleet_pressure_snapshot(
        queued_ids=queued_ids,
        claimed_map=claimed_map,
        worker_items=worker_items,
    )
    workspace_counts, specialist_counts = _outstanding_scope_counts(queued_ids, claimed_map)
    dead_letter_status = run_state_repository.sync_get_local_queue_dead_letter_status()

    merged_workspace_counts = dict(workspace_counts)
    for item in (dead_letter_status or {}).get("workspace_hotspots", []):
        workspace_id = str(item.get("workspace_id") or "").strip()
        if not workspace_id:
            continue
        merged_workspace_counts[workspace_id] = (
            merged_workspace_counts.get(workspace_id, 0) + int(item.get("count") or 0)
        )

    merged_specialist_counts = dict(specialist_counts)
    for item in (dead_letter_status or {}).get("specialist_hotspots", []):
        specialist_key = str(item.get("specialist_key") or "").strip()
        if not specialist_key:
            continue
        merged_specialist_counts[specialist_key] = (
            merged_specialist_counts.get(specialist_key, 0) + int(item.get("count") or 0)
        )

    snapshot["workspace_hotspots"] = [
        {"workspace_id": workspace_id, "count": count}
        for workspace_id, count in sorted(
            merged_workspace_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )[:5]
    ]
    snapshot["specialist_hotspots"] = [
        {"specialist_key": specialist_key, "count": count}
        for specialist_key, count in sorted(
            merged_specialist_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )[:5]
    ]
    snapshot["dead_letters"] = dead_letter_status
    snapshot["durable_intake"] = True
    snapshot["queue_strategy"] = "workspace_specialist_weighted_fifo"
    snapshot["queued_state_instead_of_collapse"] = True
    snapshot["safe_retry_enabled"] = True
    return snapshot


# ---------------------------------------------------------------------------
# Handlers for endpoints
# ---------------------------------------------------------------------------


def handle_get_local_run_queue(workspace_id: Optional[str] = None, limit: int = 50) -> Dict[str, Any]:
    _init()
    _cleanup_stale_local_claims()
    safe_limit = max(1, min(limit, 300))
    queued: List[Dict[str, Any]] = []
    claimed: List[Dict[str, Any]] = []
    workspace_filter = str(workspace_id or "").strip()

    runtime_snapshot = _local_worker_registry_snapshot()
    with _server.LOCAL_QUEUE_LOCK:
        queued_ids = list(_server.LOCAL_PENDING_RUN_IDS)
        claimed_map = {rid: dict(info) for rid, info in _server.LOCAL_CLAIMED_RUNS.items() if isinstance(info, dict)}
        worker_items = [
            {
                "worker_id": worker_id,
                "online": _is_worker_online(record, _server._utc_now()),
                "current_run_id": record.get("current_run_id"),
            }
            for worker_id, record in runtime_snapshot.items()
            if isinstance(record, dict)
        ]

    for run_id in queued_ids:
        run = _server.runs.get(run_id)
        if not isinstance(run, dict):
            continue
        summary = _annotate_local_queue_wait_state(_local_run_summary(run_id, run), run, runtime_snapshot)
        if workspace_filter and summary.get("workspace_id") != workspace_filter:
            continue
        queued.append(summary)
        if len(queued) >= safe_limit:
            break

    for run_id, claim in claimed_map.items():
        run = _server.runs.get(run_id)
        if not isinstance(run, dict):
            continue
        summary = _local_run_summary(run_id, run, claim)
        if workspace_filter and summary.get("workspace_id") != workspace_filter:
            continue
        claimed.append(summary)
        if len(claimed) >= safe_limit:
            break

    return {
        "enabled": _server.ORION_LOCAL_COMPANION_ENABLED,
        "lease_seconds": _server.ORION_LOCAL_LEASE_SECONDS,
        "queued_count": len(queued),
        "claimed_count": len(claimed),
        # Keep the queue/status hot path off durable partition writes so worker claim polls stay responsive.
        "pressure": _build_lightweight_local_fleet_pressure_snapshot(
            queued_ids=queued_ids,
            claimed_map=claimed_map,
            worker_items=worker_items,
        ),
        "queued_runs": queued,
        "claimed_runs": claimed,
    }


def handle_cleanup_local_run_queue(
    workspace_id: str,
    *,
    older_than_seconds: int = 600,
    limit: int = 200,
    dry_run: bool = True,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    _init()
    workspace_token = str(workspace_id or "").strip()
    if not workspace_token:
        raise HTTPException(status_code=400, detail="workspace_id is required.")
    threshold_seconds = max(60, min(int(older_than_seconds or 600), 604800))
    safe_limit = max(1, min(int(limit or 200), 1000))
    cleanup_reason = str(reason or "").strip() or "Cancelled by operator stale local queue cleanup."
    now_iso = _safe_utc_now_iso()
    runtime_snapshot = _local_worker_registry_snapshot()
    matched_total = 0
    selected: List[Dict[str, Any]] = []
    cleaned_payloads: List[Dict[str, Any]] = []
    skipped = {
        "missing_run": 0,
        "workspace_mismatch": 0,
        "claimed_or_active": 0,
        "status_mismatch": 0,
        "below_threshold": 0,
    }

    _cleanup_stale_local_claims()

    with _server.LOCAL_QUEUE_LOCK:
        queued_ids = list(_server.LOCAL_PENDING_RUN_IDS)
        claimed_map = {
            run_id: dict(claim)
            for run_id, claim in _server.LOCAL_CLAIMED_RUNS.items()
            if isinstance(claim, dict)
        }
        for run_id in queued_ids:
            run = _server.runs.get(run_id)
            if not isinstance(run, dict):
                skipped["missing_run"] += 1
                continue
            if run_id in claimed_map:
                skipped["claimed_or_active"] += 1
                continue
            status = str(run.get("status") or "").strip().lower()
            if status != "queued_local":
                skipped["status_mismatch"] += 1
                continue
            summary = _annotate_local_queue_wait_state(
                _local_run_summary(run_id, run),
                run,
                runtime_snapshot,
            )
            if str(summary.get("workspace_id") or "").strip() != workspace_token:
                skipped["workspace_mismatch"] += 1
                continue
            queue_age_seconds = summary.get("queue_age_seconds")
            if not isinstance(queue_age_seconds, int) or queue_age_seconds < threshold_seconds:
                skipped["below_threshold"] += 1
                continue
            matched_total += 1
            item = {
                "run_id": run_id,
                "queue_age_seconds": queue_age_seconds,
                "queue_state": summary.get("queue_state"),
                "queue_reason": summary.get("queue_reason"),
                "user_goal": summary.get("user_goal"),
                "created_at": summary.get("created_at"),
                "updated_at": summary.get("updated_at"),
                "agent_role": summary.get("agent_role"),
                "preferred_runtime_id": summary.get("preferred_runtime_id"),
                "preferred_runtime_label": summary.get("preferred_runtime_label"),
            }
            if len(selected) < safe_limit:
                selected.append(item)
                if not dry_run:
                    previous_status = str(run.get("status") or "").strip() or "queued_local"
                    _enforce_local_queue_cancel_transition(
                        run_id=run_id,
                        previous_status=previous_status,
                    )
                    log_queue = run.get("logs")
                    result_data = run.get("result_data") if isinstance(run.get("result_data"), dict) else {}
                    result_data = dict(result_data)
                    result_data.update(
                        {
                            "error": "stale_local_queue_cancelled",
                            "cleanup_reason": cleanup_reason,
                            "cleanup_age_seconds": queue_age_seconds,
                            "cleanup_threshold_seconds": threshold_seconds,
                            "cleaned_at": now_iso,
                            "queue_state": summary.get("queue_state"),
                        }
                    )
                    context = run.get("context") if isinstance(run.get("context"), dict) else {}
                    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
                    metadata["local_queue_cleanup"] = {
                        "reason": cleanup_reason,
                        "cleaned_at": now_iso,
                        "older_than_seconds": threshold_seconds,
                        "queue_age_seconds": queue_age_seconds,
                        "workspace_id": workspace_token,
                    }
                    context["metadata"] = metadata
                    run["context"] = context
                    run["status"] = "cancelled"
                    run["updated_at"] = now_iso
                    run["completed_at"] = now_iso
                    run["result"] = cleanup_reason
                    run["result_data"] = result_data
                    run["local_worker_id"] = None
                    run["local_claimed_at"] = None
                    run["local_last_heartbeat_at"] = None
                    run["_finished_mono"] = run.get("_finished_mono") or _monotonic()
                    _server.LOCAL_PENDING_RUN_IDS[:] = [item_id for item_id in _server.LOCAL_PENDING_RUN_IDS if item_id != run_id]
                    cleaned_payloads.append(
                        {
                            "run_id": run_id,
                            "run": run,
                            "previous_status": previous_status,
                            "log_queue": log_queue,
                            "summary": item,
                        }
                    )
        if cleaned_payloads:
            _persist_local_runtime_state()

    cleaned_runs: List[Dict[str, Any]] = []
    if cleaned_payloads:
        from server_modules import run_service as runtime_run_service

        archive_run_if_terminal_fn = getattr(_server, "_archive_run_if_terminal", None)
        remove_live_run_state_fn = getattr(_server, "_remove_live_run_state", None)
        run_queue_index = getattr(_server, "RUN_QUEUE_INDEX", None)
        for payload in cleaned_payloads:
            run_id = payload["run_id"]
            run = payload["run"]
            previous_status = payload["previous_status"]
            trace_id = runtime_run_service._run_trace_id(run)
            runtime_run_service._persist_run_repository_snapshot(
                run_id,
                run,
                state="cancelled",
                trace_id=trace_id,
            )
            runtime_run_service._record_run_repository_transition(
                run_id,
                from_state=previous_status,
                to_state="cancelled",
                actor="operator_local_queue_cleanup",
                trace_id=trace_id,
            )
            runtime_run_service._emit_run_transition_outbox_event(
                run_id,
                run=run,
                from_state=previous_status,
                to_state="cancelled",
                actor="operator_local_queue_cleanup",
                trace_id=trace_id,
            )
            runtime_run_service._archive_run_repository_payload(
                run_id,
                run,
                final_state="cancelled",
                trace_id=trace_id,
            )
            if payload["log_queue"] is not None:
                _server.emit_log(
                    payload["log_queue"],
                    "warning",
                    cleanup_reason[:400],
                    event="local_queue_cleanup_cancelled",
                    data={
                        "run_id": run_id,
                        "workspace_id": workspace_token,
                        "older_than_seconds": threshold_seconds,
                        "queue_age_seconds": payload["summary"]["queue_age_seconds"],
                    },
                )
            if isinstance(run_queue_index, dict) and payload["log_queue"] is not None:
                run_queue_index.pop(id(payload["log_queue"]), None)
            if callable(archive_run_if_terminal_fn):
                archive_run_if_terminal_fn(run_id, run)
            if callable(remove_live_run_state_fn):
                remove_live_run_state_fn(run_id)
            cleaned_runs.append(
                {
                    **payload["summary"],
                    "action": "cancelled_and_archived",
                    "status": "cancelled",
                }
            )

    return {
        "workspace_id": workspace_token,
        "older_than_seconds": threshold_seconds,
        "limit": safe_limit,
        "dry_run": bool(dry_run),
        "reason": cleanup_reason,
        "scanned_count": len(queued_ids) if 'queued_ids' in locals() else 0,
        "matched_count": matched_total,
        "selected_count": len(selected),
        "truncated_count": max(0, matched_total - len(selected)),
        "cleaned_count": len(cleaned_runs),
        "skipped_counts": skipped,
        "runs": cleaned_runs if cleaned_runs else [{**item, "action": "preview"} for item in selected],
    }


def handle_get_local_workers_status() -> Dict[str, Any]:
    _init()
    _cleanup_stale_local_claims()
    _mark_ghost_enrollments_failed()
    items: List[Dict[str, Any]] = []
    now = _server._utc_now()
    runtime_snapshot = _local_worker_registry_snapshot()
    if not runtime_snapshot:
        runtime_snapshot = _merged_worker_registry_snapshot()
    queued_ids: List[str] = []
    with _server.LOCAL_QUEUE_LOCK:
        for worker_id, record in list(runtime_snapshot.items()):
            if not isinstance(record, dict):
                continue
            items.append(_runtime_status_item_from_record(worker_id, record, now=now))
        pending_runs = len(_server.LOCAL_PENDING_RUN_IDS)
        claimed_runs = len(_server.LOCAL_CLAIMED_RUNS)
        queued_ids = list(_server.LOCAL_PENDING_RUN_IDS)

    known = len(items)
    online = len([item for item in items if item.get("online")])
    busy = len([item for item in items if item.get("online") and item.get("current_run_id")])
    idle = max(0, online - busy)
    offline = max(0, known - online)
    interrupting = len([item for item in items if str(item.get("control_state") or "").strip().lower() == "interrupting"])
    suspended = len([item for item in items if str(item.get("control_state") or "").strip().lower() == "suspended"])
    revoked = len([item for item in items if str(item.get("control_state") or "").strip().lower() == "revoked"])
    prewarmed_ready = len(
        [
            item
            for item in items
            if bool(item.get("online"))
            and not item.get("current_run_id")
            and str(item.get("prewarm_state") or "").strip().lower() in {"warm", "ready", "prewarmed"}
        ]
    )
    hosted_ready = len(
        [
            item
            for item in items
            if bool(item.get("online"))
            and not item.get("current_run_id")
            and "hosted_secure" in {str(target).strip().lower() for target in (item.get("execution_targets") or [])}
        ]
    )
    captain_known = len([item for item in items if str(item.get("runtime_role") or "").strip().lower() == "captain"])
    captain_online = len(
        [
            item
            for item in items
            if str(item.get("runtime_role") or "").strip().lower() == "captain" and bool(item.get("online"))
        ]
    )
    specialist_known = len([item for item in items if str(item.get("runtime_role") or "").strip().lower() == "specialist"])
    specialist_online = len(
        [
            item
            for item in items
            if str(item.get("runtime_role") or "").strip().lower() == "specialist" and bool(item.get("online"))
        ]
    )
    recovering = len([item for item in items if str(item.get("lifecycle_state") or "").strip().lower() == "recovering"])
    stopped = len([item for item in items if str(item.get("lifecycle_state") or "").strip().lower() == "stopped"])

    items.sort(key=_worker_display_sort_key)
    capability_queue = _capability_queue_summary(
        queued_ids,
        [item for item in items if bool(item.get("online"))],
    )
    pressure = _build_lightweight_local_fleet_pressure_snapshot(
        queued_ids=queued_ids,
        claimed_map={rid: dict(info) for rid, info in _server.LOCAL_CLAIMED_RUNS.items() if isinstance(info, dict)},
        worker_items=items,
    )

    return {
        "enabled": _server.ORION_LOCAL_COMPANION_ENABLED,
        "lease_seconds": _server.ORION_LOCAL_LEASE_SECONDS,
        "summary": {
            "known": known,
            "online": online,
            "busy": busy,
            "idle": idle,
            "offline": offline,
            "interrupting": interrupting,
            "suspended": suspended,
            "revoked": revoked,
            "recovering": recovering,
            "stopped": stopped,
            "prewarmed_ready": prewarmed_ready,
            "hosted_ready": hosted_ready,
            "captain_known": captain_known,
            "captain_online": captain_online,
            "specialist_known": specialist_known,
            "specialist_online": specialist_online,
            "pending_runs": pending_runs,
            "claimed_runs": claimed_runs,
        },
        "watchdog": local_runtime_watchdog_status_snapshot(),
        "pressure": pressure,
        "capability_queue": capability_queue,
        "items": items,
    }


def handle_enroll_local_runtime(
    *,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    machine_id: Optional[str] = None,
    runtime_type: str = "local_companion",
    display_name: Optional[str] = None,
    platform: Optional[str] = None,
    policy_mode: Optional[str] = None,
    capabilities: Optional[List[str]] = None,
    execution_targets: Optional[List[str]] = None,
    note: Optional[str] = None,
    machine_enrollment_scope: Optional[str] = None,
) -> Dict[str, Any]:
    return create_machine_enrollment_intent(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        machine_id=machine_id,
        runtime_type=runtime_type,
        display_name=display_name,
        platform=platform,
        policy_mode=policy_mode,
        capabilities=capabilities,
        execution_targets=execution_targets,
        note=note,
        machine_enrollment_scope=machine_enrollment_scope,
    )


def _runtime_status_item(runtime_id: str) -> Dict[str, Any]:
    _init()
    runtime_token = str(runtime_id or "").strip()
    if not runtime_token:
        raise HTTPException(status_code=400, detail="runtime_id is required.")
    with _server.LOCAL_QUEUE_LOCK:
        record = (
            dict(_server.LOCAL_WORKER_REGISTRY.get(runtime_token))
            if isinstance(_server.LOCAL_WORKER_REGISTRY.get(runtime_token), dict)
            else None
        )
    if not isinstance(record, dict):
        record = _hydrate_worker_from_durable_registry(runtime_token)
    if not isinstance(record, dict):
        raise HTTPException(status_code=404, detail="Runtime not found.")
    return _runtime_status_item_from_record(runtime_token, record)


def handle_get_local_runtime_status(runtime_id: str) -> Dict[str, Any]:
    runtime = _runtime_status_item(runtime_id)
    runtime_token = str(runtime.get("runtime_id") or runtime.get("machine_id") or runtime_id).strip() or str(runtime_id or "").strip()
    return {
        "ok": True,
        "runtime_id": runtime_token,
        "machine_id": str(runtime.get("machine_id") or runtime_token).strip() or runtime_token,
        "runtime": runtime,
    }


def _local_runtime_health_decision(runtime_token: str, runtime: Dict[str, Any]) -> Dict[str, Any]:
    online = bool(runtime.get("online"))
    status = str(runtime.get("status") or "offline").strip().lower() or "offline"
    raw_health = str(runtime.get("health_state") or "unknown").strip().lower() or "unknown"
    unavailable_worker_count = 0 if online and status not in {"offline", "failed", "revoked"} else 1
    stale_heartbeat_count = 1 if raw_health in {"stale", "unhealthy", "failed"} else 0
    decision = rust_runtime_kernel_client.runtime_health_decision(
        operation="runtime_lane_health",
        tenant_id=str(runtime.get("tenant_id") or "default").strip() or "default",
        workspace_id=str(runtime.get("workspace_id") or "default").strip() or "default",
        runtime={
            "runtime_id": runtime_token,
            "online": online,
            "status": status,
            "reported_health_state": raw_health,
            "unavailable_worker_count": unavailable_worker_count,
            "stale_heartbeat_count": stale_heartbeat_count,
            "rust_kernel_available": rust_runtime_kernel_client.runtime_kernel_available(),
        },
        lane_queue={
            "unavailable_worker_count": unavailable_worker_count,
            "stale_heartbeat_count": stale_heartbeat_count,
        },
        scheduler={"enabled": True},
        plugin={"ok": True},
        rust_kernel={"available": rust_runtime_kernel_client.runtime_kernel_available()},
        profile={"complete": True},
        bootstrap={"complete": True},
    )
    next_action = str((decision or {}).get("next_action") or "").strip()
    if next_action not in _RUNTIME_HEALTH_OPERATOR_NEXT_ACTIONS:
        raise RuntimeError(f"unexpected_next_action:{next_action or 'missing'}")
    return decision


def handle_get_local_runtime_health(runtime_id: str) -> Dict[str, Any]:
    runtime = _runtime_status_item(runtime_id)
    runtime_token = str(runtime.get("runtime_id") or runtime.get("machine_id") or runtime_id).strip() or str(runtime_id or "").strip()
    raw_health_state = str(runtime.get("health_state") or "unknown").strip().lower() or "unknown"
    rust_health = _local_runtime_health_decision(runtime_token, runtime)
    classified_health_state = str(rust_health.get("health") or "").strip().lower()
    if not classified_health_state and str(rust_health.get("decision") or "").strip().lower() == "block":
        classified_health_state = "critical"
    return {
        "ok": True,
        "runtime_id": runtime_token,
        "machine_id": str(runtime.get("machine_id") or runtime_token).strip() or runtime_token,
        "runtime_role": str(runtime.get("runtime_role") or "generic").strip().lower() or "generic",
        "lifecycle_state": str(runtime.get("lifecycle_state") or "registered").strip().lower() or "registered",
        "control_state": str(runtime.get("control_state") or "active").strip().lower() or "active",
        "online": bool(runtime.get("online")),
        "status": str(runtime.get("status") or "offline").strip().lower() or "offline",
        "current_run_id": runtime.get("current_run_id"),
        "last_seen_at": runtime.get("last_seen_at"),
        "health_state": classified_health_state or raw_health_state,
        "reported_health_state": raw_health_state,
        "runtime_health": rust_health,
        "readiness": rust_health.get("readiness") if isinstance(rust_health.get("readiness"), dict) else {},
        "health_updated_at": runtime.get("health_updated_at"),
        "summary_channel": runtime.get("summary_channel"),
        "artifact_channel": runtime.get("artifact_channel"),
        "last_summary": runtime.get("last_summary"),
        "last_summary_at": runtime.get("last_summary_at"),
        "last_artifacts": list(runtime.get("last_artifacts") or []),
        "last_artifact_at": runtime.get("last_artifact_at"),
        "local_private_memory_only": bool(runtime.get("local_private_memory_only", True)),
        "runtime": runtime,
    }


def handle_register_local_cluster_runtime(
    runtime_id: str,
    *,
    runtime_type: str = "local_companion",
    display_name: Optional[str] = None,
    platform: Optional[str] = None,
    policy_mode: Optional[str] = None,
    capabilities: Optional[List[str]] = None,
    execution_targets: Optional[List[str]] = None,
    instance_id: Optional[str] = None,
    capability_digest: Optional[str] = None,
    prewarm_state: Optional[str] = None,
    warm_pool: Optional[str] = None,
    permission_probe: Optional[Dict[str, Dict[str, Any]]] = None,
    runtime_role: Optional[str] = None,
    install_id: Optional[str] = None,
    specialist_key: Optional[str] = None,
    summary_channel: Optional[str] = None,
    artifact_channel: Optional[str] = None,
    local_private_memory_only: Optional[bool] = None,
    note: Optional[str] = None,
    summary_text: Optional[str] = None,
    artifacts: Optional[List[Dict[str, Any]]] = None,
    health_state: Optional[str] = None,
) -> Dict[str, Any]:
    registration = _upsert_runtime_registration(
        runtime_id,
        runtime_type=runtime_type,
        display_name=display_name,
        platform=platform,
        policy_mode=policy_mode,
        capabilities=capabilities,
        execution_targets=execution_targets or ["local_companion"],
        instance_id=instance_id,
        capability_digest=capability_digest,
        prewarm_state=prewarm_state,
        warm_pool=warm_pool,
        permission_probe=permission_probe,
        runtime_role=runtime_role,
        install_id=install_id,
        specialist_key=specialist_key,
        summary_channel=summary_channel,
        artifact_channel=artifact_channel,
        local_private_memory_only=local_private_memory_only,
        lifecycle_state="registered",
        lifecycle_reason=note or "local_cluster_registered",
        note=note or "local_cluster_registered",
        summary_text=summary_text,
        artifacts=artifacts,
        health_state=health_state or "registered",
    )
    return {
        "ok": True,
        "runtime_id": str(registration.get("runtime_id") or runtime_id).strip() or str(runtime_id or "").strip(),
        "machine_id": str(registration.get("machine_id") or runtime_id).strip() or str(runtime_id or "").strip(),
        "session_token": registration.get("session_token"),
        "instance_id": registration.get("instance_id"),
        "capability_digest": registration.get("capability_digest"),
        "session_issued_at": registration.get("session_issued_at"),
        "runtime": handle_get_local_runtime_status(runtime_id).get("runtime"),
    }


def handle_start_local_runtime(runtime_id: str, payload: Optional[LocalWorkerHeartbeatPayload] = None) -> Dict[str, Any]:
    _init()
    runtime_token = str(runtime_id or "").strip()
    if not runtime_token:
        raise HTTPException(status_code=400, detail="runtime_id is required.")
    body = payload or LocalWorkerHeartbeatPayload()
    _hydrate_worker_from_durable_registry(runtime_token)
    with _server.LOCAL_QUEUE_LOCK:
        record = _server.LOCAL_WORKER_REGISTRY.get(runtime_token) if isinstance(_server.LOCAL_WORKER_REGISTRY.get(runtime_token), dict) else None
        if not isinstance(record, dict):
            raise HTTPException(status_code=404, detail="Runtime not found.")
        control_state = _machine_control_state(record)
        if control_state == "revoked":
            raise HTTPException(status_code=409, detail="Revoked runtimes cannot be started.")
        if control_state == "suspended":
            raise HTTPException(status_code=409, detail="Suspended runtimes must be resumed before they can start.")
    _mark_local_worker_seen(
        runtime_token,
        body.current_run_id,
        "busy" if str(body.current_run_id or "").strip() else "idle",
        note=body.note or "local_runtime_started",
    )
    with _server.LOCAL_QUEUE_LOCK:
        record = _server.LOCAL_WORKER_REGISTRY.get(runtime_token) if isinstance(_server.LOCAL_WORKER_REGISTRY.get(runtime_token), dict) else {}
        _apply_permission_probe(record, body.permission_probe)
        _apply_runtime_identity_fields(
            record,
            runtime_role=body.runtime_role,
            install_id=body.install_id,
            specialist_key=body.specialist_key,
            summary_channel=body.summary_channel,
            artifact_channel=body.artifact_channel,
            local_private_memory_only=body.local_private_memory_only,
        )
        _set_runtime_lifecycle_state(record, "running", reason=body.lifecycle_reason or body.note or "local_runtime_started")
        _apply_runtime_surface_hooks(
            record,
            summary_text=body.summary_text,
            artifacts=body.artifacts,
            health_state=body.health_state or "healthy",
        )
        _server.LOCAL_WORKER_REGISTRY[runtime_token] = record
    _persist_local_runtime_state()
    _sync_durable_fleet_worker(record, heartbeat_seen=True)
    _emit_machine_outbox_event("runtime_started", record)
    return handle_get_local_runtime_status(runtime_token)


def handle_stop_local_runtime(
    runtime_id: str,
    *,
    reason: Optional[str] = None,
    note: Optional[str] = None,
    summary_text: Optional[str] = None,
    artifacts: Optional[List[Dict[str, Any]]] = None,
    health_state: Optional[str] = None,
) -> Dict[str, Any]:
    _init()
    runtime_token = str(runtime_id or "").strip()
    if not runtime_token:
        raise HTTPException(status_code=400, detail="runtime_id is required.")
    _hydrate_worker_from_durable_registry(runtime_token)
    _mark_local_worker_seen(runtime_token, None, "stopped", note=note or "local_runtime_stopped")
    with _server.LOCAL_QUEUE_LOCK:
        record = _server.LOCAL_WORKER_REGISTRY.get(runtime_token) if isinstance(_server.LOCAL_WORKER_REGISTRY.get(runtime_token), dict) else None
        if not isinstance(record, dict):
            raise HTTPException(status_code=404, detail="Runtime not found.")
        if _machine_control_state(record) == "revoked":
            raise HTTPException(status_code=409, detail="Revoked runtimes cannot be stopped again.")
        record["current_run_id"] = None
        record["status"] = "stopped"
        _set_runtime_lifecycle_state(record, "stopped", reason=reason or note or "local_runtime_stopped")
        _apply_runtime_surface_hooks(
            record,
            summary_text=summary_text,
            artifacts=artifacts,
            health_state=health_state or "stopped",
        )
        _server.LOCAL_WORKER_REGISTRY[runtime_token] = record
    _persist_local_runtime_state()
    _sync_durable_fleet_worker(record, heartbeat_seen=False)
    _emit_machine_outbox_event("runtime_stopped", record)
    return handle_get_local_runtime_status(runtime_token)


def _run_belongs_to_runtime(run_id: str, runtime_id: str) -> bool:
    _init()
    run = _server.runs.get(str(run_id or "").strip())
    if not isinstance(run, dict):
        return False
    runtime_token = str(runtime_id or "").strip()
    if not runtime_token:
        return False
    direct_tokens = {
        str(run.get("local_worker_id") or "").strip(),
        str(run.get("machine_id") or "").strip(),
        str(run.get("interrupt_machine_id") or "").strip(),
        str(run.get("interrupt_runtime_id") or "").strip(),
    }
    if runtime_token in direct_tokens:
        return True
    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    metadata_tokens = {
        str(metadata.get("machine_id") or "").strip(),
        str(metadata.get("local_worker_id") or "").strip(),
        str(metadata.get("interrupt_machine_id") or "").strip(),
        str(metadata.get("interrupt_runtime_id") or "").strip(),
    }
    return runtime_token in metadata_tokens


def handle_recover_local_runtime(runtime_id: str, payload: Optional[LocalWorkerHeartbeatPayload] = None) -> Dict[str, Any]:
    _init()
    runtime_token = str(runtime_id or "").strip()
    if not runtime_token:
        raise HTTPException(status_code=400, detail="runtime_id is required.")
    body = payload or LocalWorkerHeartbeatPayload()
    _hydrate_worker_from_durable_registry(runtime_token)
    with _server.LOCAL_QUEUE_LOCK:
        record = _server.LOCAL_WORKER_REGISTRY.get(runtime_token) if isinstance(_server.LOCAL_WORKER_REGISTRY.get(runtime_token), dict) else None
        if not isinstance(record, dict):
            raise HTTPException(status_code=404, detail="Runtime not found.")
        control_state = _machine_control_state(record)
        if control_state == "revoked":
            raise HTTPException(status_code=409, detail="Revoked runtimes cannot be recovered.")
        if control_state == "suspended":
            raise HTTPException(status_code=409, detail="Suspended runtimes must be resumed before recovery.")
        _apply_permission_probe(record, body.permission_probe)
        _apply_runtime_identity_fields(
            record,
            runtime_role=body.runtime_role,
            install_id=body.install_id,
            specialist_key=body.specialist_key,
            summary_channel=body.summary_channel,
            artifact_channel=body.artifact_channel,
            local_private_memory_only=body.local_private_memory_only,
        )
        _set_runtime_lifecycle_state(record, "recovering", reason=body.lifecycle_reason or body.note or "local_runtime_recovering")
        _apply_runtime_surface_hooks(
            record,
            summary_text=body.summary_text,
            artifacts=body.artifacts,
            health_state=body.health_state or "recovering",
        )
        _server.LOCAL_WORKER_REGISTRY[runtime_token] = record
    _persist_local_runtime_state()
    _sync_durable_fleet_worker(record, heartbeat_seen=False)
    _mark_local_worker_seen(
        runtime_token,
        body.current_run_id,
        "busy" if str(body.current_run_id or "").strip() else "idle",
        note=body.note or "local_runtime_recovering",
    )
    recovered_run_ids = [
        run_id
        for run_id in (recover_expired_worker_leases_on_startup() + recover_orphaned_local_runs_on_startup())
        if _run_belongs_to_runtime(run_id, runtime_token)
    ]
    resumed_run_ids = [run_id for run_id in _resume_due_checkpoint_recoveries() if _run_belongs_to_runtime(run_id, runtime_token)]
    with _server.LOCAL_QUEUE_LOCK:
        record = _server.LOCAL_WORKER_REGISTRY.get(runtime_token) if isinstance(_server.LOCAL_WORKER_REGISTRY.get(runtime_token), dict) else {}
        record["last_recovered_run_ids"] = recovered_run_ids
        record["last_resumed_run_ids"] = resumed_run_ids
        _set_runtime_lifecycle_state(record, "running", reason=body.lifecycle_reason or body.note or "local_runtime_recovered")
        _apply_runtime_surface_hooks(
            record,
            summary_text=body.summary_text,
            artifacts=body.artifacts,
            health_state=body.health_state or "healthy",
        )
        _server.LOCAL_WORKER_REGISTRY[runtime_token] = record
    _persist_local_runtime_state()
    _sync_durable_fleet_worker(record, heartbeat_seen=True)
    _emit_machine_outbox_event("runtime_recovered", record)
    payload = handle_get_local_runtime_status(runtime_token)
    payload["recovered_run_ids"] = recovered_run_ids
    payload["resumed_run_ids"] = resumed_run_ids
    return payload


def handle_delete_local_runtime(machine_id: str, *, reason: Optional[str] = None) -> Dict[str, Any]:
    _init()
    runtime_id = str(machine_id or "").strip()
    if not runtime_id:
        raise HTTPException(status_code=400, detail="machine_id is required.")
    started_mono = _monotonic()

    with _server.LOCAL_QUEUE_LOCK:
        record = _server.LOCAL_WORKER_REGISTRY.get(runtime_id) if isinstance(_server.LOCAL_WORKER_REGISTRY.get(runtime_id), dict) else None
        if not isinstance(record, dict):
            raise HTTPException(status_code=404, detail="Machine not found.")
        revoke_reason = str(reason or "Revoked from fleet controls.").strip() or "Revoked from fleet controls."
        _set_machine_control_state(record, "revoked", reason=revoke_reason)
        _set_runtime_lifecycle_state(record, "revoked", reason=revoke_reason)
        record["note"] = "machine_revoked"
        _server.LOCAL_WORKER_REGISTRY[runtime_id] = record
    mark_machine_revocation_requested(runtime_id, started_monotonic=started_mono, recorded_at=_server._utc_now_iso())

    _persist_local_runtime_state()
    _sync_durable_fleet_worker(record, heartbeat_seen=False)
    _emit_machine_outbox_event("revoked", record)
    status_payload = handle_get_local_workers_status()
    items = status_payload.get("items") if isinstance(status_payload.get("items"), list) else []
    machine = next(
        (
            item
            for item in items
            if isinstance(item, dict)
            and str(item.get("machine_id") or item.get("runtime_id") or "").strip() == runtime_id
        ),
        None,
    )
    return {
        "ok": True,
        "machine_id": runtime_id,
        "deleted": False,
        "revoked": True,
        "machine": machine or dict(record),
    }


def handle_revoke_local_runtime(runtime_id: str, *, reason: Optional[str] = None) -> Dict[str, Any]:
    return handle_delete_local_runtime(runtime_id, reason=reason or "Revoked from local cluster controls.")


def _mark_run_interrupt_requested(
    run_id: str,
    run: Dict[str, Any],
    *,
    reason: Optional[str],
    worker_id: Optional[str],
    machine_id: Optional[str],
    scope: str,
    requested_by: Optional[str] = None,
) -> Dict[str, Any]:
    _init()
    now_iso = _safe_utc_now_iso()
    summary = str(reason or "").strip() or "Hard kill requested by operator."
    run["interrupt_requested"] = True
    run["interrupt_requested_at"] = now_iso
    run["interrupt_reason"] = summary
    run["interrupt_scope"] = str(scope or "run").strip().lower() or "run"
    if machine_id:
        run["interrupt_machine_id"] = str(machine_id).strip()
    if worker_id:
        run["interrupt_runtime_id"] = str(worker_id).strip()
    if requested_by:
        run["interrupt_requested_by"] = str(requested_by).strip()
    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    metadata["interrupt_requested"] = True
    metadata["interrupt_requested_at"] = now_iso
    metadata["interrupt_reason"] = summary
    metadata["interrupt_scope"] = run["interrupt_scope"]
    if worker_id:
        metadata["interrupt_runtime_id"] = str(worker_id).strip()
    if machine_id:
        metadata["interrupt_machine_id"] = str(machine_id).strip()
    context["metadata"] = metadata
    run["context"] = context
    log_queue = run.get("logs")
    if log_queue is not None:
        _server.emit_log(
            log_queue,
            "error",
            summary[:400],
            event="run_interrupt_requested",
            data={
                "run_id": run_id,
                "worker_id": str(worker_id or "").strip() or None,
                "machine_id": str(machine_id or "").strip() or None,
                "scope": run["interrupt_scope"],
                "requested_by": str(requested_by or "").strip() or None,
            },
        )
    return {
        "interrupt_requested": True,
        "interrupt_requested_at": now_iso,
        "interrupt_reason": summary,
        "interrupt_scope": run["interrupt_scope"],
    }


def handle_request_local_run_hard_kill(
    run_id: str,
    *,
    reason: Optional[str] = None,
    requested_by: Optional[str] = None,
) -> Dict[str, Any]:
    _init()
    run_token = str(run_id or "").strip()
    if not run_token:
        raise HTTPException(status_code=400, detail="run_id is required.")
    run = _server.runs.get(run_token)
    if not isinstance(run, dict):
        raise HTTPException(status_code=404, detail="Run ID not found")
    status = str(run.get("status") or "").strip().lower()
    if status in {"completed", "failed", "timeout", "stopped", "cancelled"}:
        return {"ok": True, "run_id": run_token, "already_terminal": True, "status": status}
    with _server.LOCAL_QUEUE_LOCK:
        claim = _server.LOCAL_CLAIMED_RUNS.get(run_token) if isinstance(_server.LOCAL_CLAIMED_RUNS.get(run_token), dict) else {}
    worker_id = str((claim or {}).get("worker_id") or run.get("local_worker_id") or "").strip() or None
    machine_id = str((claim or {}).get("machine_id") or run.get("machine_id") or worker_id or "").strip() or None
    interrupt_state = _mark_run_interrupt_requested(
        run_token,
        run,
        reason=reason,
        worker_id=worker_id,
        machine_id=machine_id,
        scope="run",
        requested_by=requested_by,
    )
    if worker_id:
        event = _append_runtime_control_event(
            worker_id,
            "run_interrupt",
            {
                "run_id": run_token,
                "machine_id": machine_id,
                "reason": interrupt_state["interrupt_reason"],
                "scope": "run",
            },
        )
        _persist_local_runtime_state()
        return {
            "ok": True,
            "run_id": run_token,
            "worker_id": worker_id,
            "machine_id": machine_id,
            "broadcasted": True,
            "event": event,
            **interrupt_state,
        }
    run["result"] = interrupt_state["interrupt_reason"]
    _server.set_run_status(run_token, "failed")
    _persist_local_runtime_state()
    return {
        "ok": True,
        "run_id": run_token,
        "worker_id": None,
        "machine_id": machine_id,
        "broadcasted": False,
        "failed_immediately": True,
        **interrupt_state,
    }


def handle_request_local_runtime_hard_kill(
    machine_id: str,
    *,
    reason: Optional[str] = None,
    requested_by: Optional[str] = None,
) -> Dict[str, Any]:
    _init()
    runtime_id = str(machine_id or "").strip()
    if not runtime_id:
        raise HTTPException(status_code=400, detail="machine_id is required.")
    with _server.LOCAL_QUEUE_LOCK:
        record = _server.LOCAL_WORKER_REGISTRY.get(runtime_id) if isinstance(_server.LOCAL_WORKER_REGISTRY.get(runtime_id), dict) else None
        if not isinstance(record, dict):
            raise HTTPException(status_code=404, detail="Machine not found.")
        _set_machine_control_state(record, "interrupting", reason=reason or "Hard kill requested by operator.")
        record["note"] = "machine_interrupting"
        _server.LOCAL_WORKER_REGISTRY[runtime_id] = record
        active_run_ids = [
            run_id
            for run_id, claim in _server.LOCAL_CLAIMED_RUNS.items()
            if isinstance(claim, dict) and str(claim.get("worker_id") or claim.get("machine_id") or "").strip() == runtime_id
        ]
    requested_runs: List[Dict[str, Any]] = []
    for active_run_id in active_run_ids:
        try:
            requested_runs.append(
                handle_request_local_run_hard_kill(
                    active_run_id,
                    reason=reason or "Hard kill requested by operator.",
                    requested_by=requested_by,
                )
            )
        except HTTPException:
            continue
    machine_event = _append_runtime_control_event(
        runtime_id,
        "hard_kill",
        {
            "machine_id": runtime_id,
            "reason": str(reason or "Hard kill requested by operator.").strip() or "Hard kill requested by operator.",
            "scope": "machine",
            "requested_by": str(requested_by or "").strip() or None,
        },
    )
    _persist_local_runtime_state()
    _sync_durable_fleet_worker(record, heartbeat_seen=False)
    _emit_machine_outbox_event("hard_kill_requested", record)
    status_payload = handle_get_local_workers_status()
    items = status_payload.get("items") if isinstance(status_payload.get("items"), list) else []
    machine = next(
        (
            item
            for item in items
            if isinstance(item, dict)
            and str(item.get("machine_id") or item.get("runtime_id") or "").strip() == runtime_id
        ),
        None,
    )
    return {
        "ok": True,
        "machine_id": runtime_id,
        "machine": machine or dict(record),
        "event": machine_event,
        "requested_run_ids": [item.get("run_id") for item in requested_runs if isinstance(item, dict)],
    }


def handle_set_local_runtime_control(machine_id: str, *, action: str, reason: Optional[str] = None) -> Dict[str, Any]:
    _init()
    runtime_id = str(machine_id or "").strip()
    control_action = str(action or "").strip().lower()
    if not runtime_id:
        raise HTTPException(status_code=400, detail="machine_id is required.")
    if control_action not in {"suspend", "resume"}:
        raise HTTPException(status_code=400, detail="Unsupported machine control action.")

    with _server.LOCAL_QUEUE_LOCK:
        record = _server.LOCAL_WORKER_REGISTRY.get(runtime_id) if isinstance(_server.LOCAL_WORKER_REGISTRY.get(runtime_id), dict) else None
        if not isinstance(record, dict):
            raise HTTPException(status_code=404, detail="Machine not found.")
        current_state = _machine_control_state(record)
        if control_action == "resume":
            if current_state == "revoked":
                raise HTTPException(status_code=409, detail="Revoked machines must be re-enrolled instead of resumed.")
            _set_machine_control_state(record, "active", reason=reason or "Resumed from fleet controls.")
            record["note"] = "machine_resumed"
            outbox_action = "resumed"
        else:
            if current_state == "revoked":
                raise HTTPException(status_code=409, detail="Revoked machines cannot be suspended.")
            _set_machine_control_state(record, "suspended", reason=reason or "Suspended from fleet controls.")
            record["note"] = "machine_suspended"
            outbox_action = "suspended"
        _server.LOCAL_WORKER_REGISTRY[runtime_id] = record

    _persist_local_runtime_state()
    _sync_durable_fleet_worker(record, heartbeat_seen=False)
    _emit_machine_outbox_event(outbox_action, record)
    if control_action == "resume":
        _append_runtime_control_event(
            runtime_id,
            "machine_resume",
            {
                "machine_id": runtime_id,
                "reason": str(reason or "Machine resumed by operator.").strip() or "Machine resumed by operator.",
                "scope": "machine",
            },
        )
    status_payload = handle_get_local_workers_status()
    items = status_payload.get("items") if isinstance(status_payload.get("items"), list) else []
    machine = next(
        (
            item
            for item in items
            if isinstance(item, dict)
            and str(item.get("machine_id") or item.get("runtime_id") or "").strip() == runtime_id
        ),
        None,
    )
    return {"ok": True, "machine_id": runtime_id, "action": control_action, "machine": machine or dict(record)}


def handle_heartbeat_local_worker(worker_id: str, payload: Optional[LocalWorkerHeartbeatPayload] = None) -> Dict[str, Any]:
    _init()
    worker = str(worker_id or "").strip()
    if not worker:
        raise HTTPException(status_code=400, detail="worker_id is required.")
    if payload and payload.permission_probe:
        should_persist_probe = False
        with _server.LOCAL_QUEUE_LOCK:
            record = _server.LOCAL_WORKER_REGISTRY.get(worker) if isinstance(_server.LOCAL_WORKER_REGISTRY.get(worker), dict) else {}
            next_record = dict(record or {})
            if not _permission_probe_semantically_equal(next_record.get("permission_probe"), payload.permission_probe):
                _apply_permission_probe(next_record, payload.permission_probe)
                _server.LOCAL_WORKER_REGISTRY[worker] = next_record
                should_persist_probe = True
        if should_persist_probe:
            _persist_local_runtime_state()
    return worker_dispatch_service.heartbeat_local_worker(
        worker,
        current_run_id=(payload.current_run_id if payload else None),
        note=(payload.note if payload else None),
        runs_by_id=_server.runs,
        local_queue_lock=_server.LOCAL_QUEUE_LOCK,
        claimed_runs=_server.LOCAL_CLAIMED_RUNS,
        mark_local_worker_seen_fn=_mark_local_worker_seen,
        maybe_emit_local_still_working_fn=_maybe_emit_local_still_working,
        persist_local_runtime_state_fn=_persist_local_runtime_state,
        utc_now_iso_fn=_server._utc_now_iso,
        touch_claim_heartbeat_fn=_dispatch_touch_claim_heartbeat,
    )


def handle_heartbeat_local_runtime(runtime_id: str, payload: Optional[LocalWorkerHeartbeatPayload] = None) -> Dict[str, Any]:
    _init()
    runtime_token = str(runtime_id or "").strip()
    if not runtime_token:
        raise HTTPException(status_code=400, detail="runtime_id is required.")
    body = payload or LocalWorkerHeartbeatPayload()
    result = handle_heartbeat_local_worker(runtime_token, body)
    with _server.LOCAL_QUEUE_LOCK:
        record = _server.LOCAL_WORKER_REGISTRY.get(runtime_token) if isinstance(_server.LOCAL_WORKER_REGISTRY.get(runtime_token), dict) else None
        if not isinstance(record, dict):
            raise HTTPException(status_code=404, detail="Runtime not found.")
        _apply_permission_probe(record, body.permission_probe)
        _apply_runtime_identity_fields(
            record,
            runtime_role=body.runtime_role,
            install_id=body.install_id,
            specialist_key=body.specialist_key,
            summary_channel=body.summary_channel,
            artifact_channel=body.artifact_channel,
            local_private_memory_only=body.local_private_memory_only,
        )
        lifecycle_state = str(record.get("lifecycle_state") or "registered").strip().lower() or "registered"
        if lifecycle_state not in {"stopped", "revoked"} and _machine_control_state(record) != "revoked":
            _set_runtime_lifecycle_state(record, "running", reason=body.lifecycle_reason or body.note or record.get("lifecycle_reason"))
        _apply_runtime_surface_hooks(
            record,
            summary_text=body.summary_text,
            artifacts=body.artifacts,
            health_state=body.health_state,
        )
        _server.LOCAL_WORKER_REGISTRY[runtime_token] = record
    _persist_local_runtime_state()
    _sync_durable_fleet_worker(record, heartbeat_seen=True)
    payload = handle_get_local_runtime_status(runtime_token)
    payload["last_seen_at"] = result.get("last_seen_at")
    payload["current_run_id"] = result.get("current_run_id")
    return payload


def handle_claim_local_run(body: Optional[LocalRunClaimRequest] = None) -> Dict[str, Any]:
    _init()
    if not _server.ORION_LOCAL_COMPANION_ENABLED:
        raise HTTPException(status_code=400, detail="Gateway routing is disabled for this workspace.")

    worker_id = str((body.worker_id if body else None) or "").strip() if body else ""
    if not worker_id:
        worker_id = f"local-worker-{uuid.uuid4().hex[:8]}"

    requested_capabilities = list(body.required_capabilities or []) if body else []
    run_id = _claim_local_run(worker_id, required_capabilities=requested_capabilities)
    if not run_id:
        now = _server._utc_now()
        with _server.LOCAL_QUEUE_LOCK:
            queued_ids = list(_server.LOCAL_PENDING_RUN_IDS)
            claimed_map = {
                rid: dict(info)
                for rid, info in _server.LOCAL_CLAIMED_RUNS.items()
                if isinstance(info, dict)
            }
            worker_items = [
                {
                    "worker_id": runtime_id,
                    "online": _is_worker_online(record, now),
                    "current_run_id": record.get("current_run_id"),
                }
                for runtime_id, record in _server.LOCAL_WORKER_REGISTRY.items()
                if isinstance(record, dict)
            ]
        pressure = _build_lightweight_local_fleet_pressure_snapshot(
            queued_ids=queued_ids,
            claimed_map=claimed_map,
            worker_items=worker_items,
        )
        return {"ok": True, "worker_id": worker_id, "run": None, "backpressure": pressure}

    run = _server.runs.get(run_id)
    if not isinstance(run, dict):
        now = _server._utc_now()
        with _server.LOCAL_QUEUE_LOCK:
            queued_ids = list(_server.LOCAL_PENDING_RUN_IDS)
            claimed_map = {
                rid: dict(info)
                for rid, info in _server.LOCAL_CLAIMED_RUNS.items()
                if isinstance(info, dict)
            }
            worker_items = [
                {
                    "worker_id": runtime_id,
                    "online": _is_worker_online(record, now),
                    "current_run_id": record.get("current_run_id"),
                }
                for runtime_id, record in _server.LOCAL_WORKER_REGISTRY.items()
                if isinstance(record, dict)
            ]
        pressure = _build_lightweight_local_fleet_pressure_snapshot(
            queued_ids=queued_ids,
            claimed_map=claimed_map,
            worker_items=worker_items,
        )
        return {"ok": True, "worker_id": worker_id, "run": None, "backpressure": pressure}

    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    worker_state = _server.LOCAL_WORKER_REGISTRY.get(worker_id) if isinstance(_server.LOCAL_WORKER_REGISTRY.get(worker_id), dict) else {}
    now = datetime.utcnow().isoformat() + "Z"
    claim = _server.LOCAL_CLAIMED_RUNS.get(run_id) if isinstance(_server.LOCAL_CLAIMED_RUNS.get(run_id), dict) else {}
    machine_lease_service.bind_machine_lease_to_run(
        run,
        worker_id=worker_id,
        claim=claim,
        worker_state=worker_state,
        now_iso=now,
        normalize_policy_mode_fn=_server.normalize_policy_mode,
    )
    _server.set_run_status(run_id, "running_local")
    _server.emit_log(
        run["logs"],
        "info",
        f"Gateway claimed run ({worker_id}).",
        event="local_claimed",
        data={
            "run_id": run_id,
            "worker_id": worker_id,
            "machine_id": claim.get("machine_id") or worker_id,
            "lease_id": claim.get("lease_id"),
            "lease_seconds": _server.ORION_LOCAL_LEASE_SECONDS,
        },
    )

    return {
        "ok": True,
        "worker_id": worker_id,
        "run": {
            "run_id": run_id,
            "engine": run.get("engine"),
            "status": run.get("status"),
            "lease_seconds": int(claim.get("lease_seconds") or _server.ORION_LOCAL_LEASE_SECONDS),
            "machine_id": claim.get("machine_id") or worker_id,
            "machine_lease_id": claim.get("lease_id"),
            "context": _server.redact_sensitive(run.get("context", {})),
            "browser_checkpoint": (
                run.get("browser_checkpoint")
                if isinstance(run.get("browser_checkpoint"), dict)
                else None
            ),
            "created_at": run.get("created_at"),
        },
    }


def handle_heartbeat_local_run(run_id: uuid.UUID, payload: Optional[LocalRunHeartbeatPayload] = None) -> Dict[str, Any]:
    _init()
    run_id_str = str(run_id)
    structured_event = dict(payload.event) if payload and isinstance(payload.event, dict) else None
    result = worker_dispatch_service.heartbeat_local_run(
        run_id_str,
        worker_id=(payload.worker_id if payload else None),
        note=(payload.note if payload else None),
        runs_by_id=_server.runs,
        local_queue_lock=_server.LOCAL_QUEUE_LOCK,
        claimed_runs=_server.LOCAL_CLAIMED_RUNS,
        maybe_emit_local_still_working_fn=_maybe_emit_local_still_working,
        mark_local_worker_seen_fn=_mark_local_worker_seen,
        utc_now_iso_fn=_server._utc_now_iso,
        touch_claim_heartbeat_fn=_dispatch_touch_claim_heartbeat,
        progress_event=bool(structured_event),
    )
    note = str((payload.note if payload else None) or "").strip() if payload else ""
    run = _server.runs.get(run_id_str)
    if isinstance(run, dict):
        if structured_event:
            event_name = str(structured_event.get("event") or "").strip().lower() or "local_heartbeat"
            event_message = str(structured_event.get("message") or note or event_name).strip() or event_name
            event_level = str(structured_event.get("level") or "info").strip().lower() or "info"
            event_data = structured_event.get("data") if isinstance(structured_event.get("data"), dict) else None
            _update_run_progress_from_structured_event(run, event_name, event_data)
            _server.emit_log(run["logs"], event_level, event_message[:400], event=event_name, data=event_data)
        elif note:
            _server.emit_log(run["logs"], "info", note[:400], event="local_heartbeat")
    return result


def handle_get_local_run_control_state(
    run_id: uuid.UUID,
    payload: Optional[LocalRunControlStatePayload] = None,
) -> Dict[str, Any]:
    _init()
    worker_id = str((payload.worker_id if payload else None) or "").strip()
    run_id_str = str(run_id)
    run = _server.runs.get(run_id_str)
    if not isinstance(run, dict):
        raise HTTPException(status_code=404, detail="Run ID not found")
    status = str(run.get("status") or "").strip().lower()
    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    machine_id = str(
        run.get("machine_id")
        or run.get("local_worker_id")
        or metadata.get("machine_id")
        or worker_id
        or ""
    ).strip()
    machine_control_state = "active"
    machine_wait_reason: Optional[str] = None
    machine_interrupt_requested = False
    machine_interrupt_reason: Optional[str] = None
    machine_interrupt_requested_at: Optional[str] = None
    with _server.LOCAL_QUEUE_LOCK:
        record = _server.LOCAL_WORKER_REGISTRY.get(machine_id) if isinstance(_server.LOCAL_WORKER_REGISTRY.get(machine_id), dict) else None
        if isinstance(record, dict):
            machine_control_state = _machine_control_state(record)
            if machine_control_state == "interrupting":
                machine_wait_reason = "machine_interrupt_requested"
                machine_interrupt_requested = True
                machine_interrupt_reason = str(record.get("interrupt_reason") or "").strip() or None
                machine_interrupt_requested_at = str(record.get("interrupt_requested_at") or "").strip() or None
            elif machine_control_state == "revoked":
                machine_wait_reason = "machine_revoked"
            elif machine_control_state == "suspended":
                machine_wait_reason = "machine_suspended"
    browser_checkpoint = run.get("browser_checkpoint") if isinstance(run.get("browser_checkpoint"), dict) else None
    local_execution_checkpoint = _local_execution_checkpoint_payload(run)
    wait_reason = str(
        run.get("wait_reason")
        or ((run.get("result_data") if isinstance(run.get("result_data"), dict) else {}).get("pause_reason"))
        or ""
    ).strip() or None
    run_interrupt_requested = bool(run.get("interrupt_requested"))
    interrupt_requested = machine_interrupt_requested or run_interrupt_requested
    interrupt_reason = (
        machine_interrupt_reason
        or str(run.get("interrupt_reason") or "").strip()
        or None
    )
    interrupt_scope = (
        "machine"
        if machine_interrupt_requested
        else str(run.get("interrupt_scope") or "").strip().lower() or None
    )
    interrupt_requested_at = (
        machine_interrupt_requested_at
        or str(run.get("interrupt_requested_at") or "").strip()
        or None
    )
    manual_takeover = _manual_takeover_active(run)
    if machine_wait_reason == "machine_revoked":
        observed_at = _server._utc_now_iso() if callable(getattr(_server, "_utc_now_iso", None)) else None
        observe_machine_revocation_propagated(
            machine_id,
            observed_monotonic=_monotonic(),
            observed_at=observed_at,
        )
    return {
        "status": status,
        "pause_requested": status == "waiting_for_input" or bool(machine_wait_reason) or interrupt_requested,
        "manual_takeover": manual_takeover,
        "wait_reason": machine_wait_reason or ("run_interrupt_requested" if run_interrupt_requested else wait_reason),
        "browser_checkpoint": dict(browser_checkpoint) if isinstance(browser_checkpoint, dict) else None,
        "local_execution_checkpoint": local_execution_checkpoint,
        "machine_control_state": machine_control_state,
        "resume_available": bool(browser_checkpoint or local_execution_checkpoint),
        "interrupt_requested": interrupt_requested,
        "interrupt_reason": interrupt_reason,
        "interrupt_requested_at": interrupt_requested_at,
        "scope": interrupt_scope,
        "run_id": run_id_str,
        "machine_id": machine_id or None,
        "event_type": "hard_kill" if interrupt_scope == "machine" else "run_interrupt" if interrupt_requested else None,
    }


def handle_complete_local_run(run_id: uuid.UUID, payload: LocalRunCompletePayload) -> Dict[str, Any]:
    _init()
    return worker_dispatch_service.complete_local_run(
        str(run_id),
        worker_id=payload.worker_id,
        result_text=payload.result_text,
        result_data=payload.result_data,
        usage_masked=payload.usage_masked,
        runs_by_id=_server.runs,
        local_queue_lock=_server.LOCAL_QUEUE_LOCK,
        claimed_runs=_server.LOCAL_CLAIMED_RUNS,
        emit_log_fn=_server.emit_log,
        mark_local_worker_seen_fn=_mark_local_worker_seen,
        set_run_status_fn=_server.set_run_status,
        persist_run_memory_fn=_server._persist_run_memory,
        persist_local_runtime_state_fn=_persist_local_runtime_state,
    )


def handle_pause_local_run(run_id: uuid.UUID, payload: LocalRunPausePayload) -> Dict[str, Any]:
    _init()
    return worker_dispatch_service.pause_local_run(
        str(run_id),
        worker_id=payload.worker_id,
        result_text=payload.result_text,
        result_data=payload.result_data,
        browser_checkpoint=payload.browser_checkpoint,
        wait_reason=payload.wait_reason,
        runs_by_id=_server.runs,
        local_queue_lock=_server.LOCAL_QUEUE_LOCK,
        claimed_runs=_server.LOCAL_CLAIMED_RUNS,
        emit_log_fn=_server.emit_log,
        mark_local_worker_seen_fn=_mark_local_worker_seen,
        set_run_status_fn=_server.set_run_status,
        persist_local_runtime_state_fn=_persist_local_runtime_state,
    )


def handle_fail_local_run(run_id: uuid.UUID, payload: LocalRunFailPayload) -> Dict[str, Any]:
    _init()
    run = _server.runs.get(str(run_id))
    result = worker_dispatch_service.fail_local_run(
        str(run_id),
        worker_id=payload.worker_id,
        error=payload.error,
        runs_by_id=_server.runs,
        local_queue_lock=_server.LOCAL_QUEUE_LOCK,
        claimed_runs=_server.LOCAL_CLAIMED_RUNS,
        emit_log_fn=_server.emit_log,
        mark_local_worker_seen_fn=_mark_local_worker_seen,
        set_run_status_fn=_server.set_run_status,
        persist_local_runtime_state_fn=_persist_local_runtime_state,
    )
    if isinstance(run, dict) and bool(run.get("interrupt_requested")):
        machine_id = str(run.get("interrupt_machine_id") or run.get("machine_id") or payload.worker_id or "").strip()
        if machine_id:
            with _server.LOCAL_QUEUE_LOCK:
                record = _server.LOCAL_WORKER_REGISTRY.get(machine_id) if isinstance(_server.LOCAL_WORKER_REGISTRY.get(machine_id), dict) else None
            if isinstance(record, dict):
                record = dict(record)
                record["note"] = "machine_interrupt_acknowledged"
                _emit_machine_outbox_event("interrupt_completed", record)
    return result
