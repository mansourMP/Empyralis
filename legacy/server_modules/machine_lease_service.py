from __future__ import annotations

import uuid
from datetime import timedelta
from dataclasses import dataclass, field
import logging
import threading
from typing import Any, Callable, Dict, List, Mapping, Optional

from fastapi import HTTPException
from server_modules import run_state_repository
from server_modules import rust_runtime_kernel_client


LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class MachineRecord:
    machine_id: str
    owner_id: str
    platform: str
    capabilities: List[str] = field(default_factory=list)
    permission_probe: Dict[str, bool] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MachineLease:
    lease_id: str
    machine_id: str
    run_id: str
    workspace_id: str
    actor_id: str
    ttl_seconds: int
    capabilities_requested: List[str] = field(default_factory=list)
    capabilities_granted: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


def _normalize_machine_enrollment_scope(value: Any, *, default: str = "workspace") -> str:
    token = str(value or "").strip().lower()
    if token in {"workspace", "tenant", "global"}:
        return token
    return default


def _runtime_session_scope(record: Mapping[str, Any]) -> Dict[str, str]:
    return {
        "tenant_id": str(record.get("tenant_id") or "default").strip() or "default",
        "workspace_id": str(record.get("workspace_id") or "default").strip() or "default",
        "runtime_type": str(record.get("runtime_type") or "local").strip() or "local",
        "runtime_role": str(record.get("runtime_role") or "generic").strip().lower() or "generic",
        "install_id": str(record.get("install_id") or "").strip(),
        "specialist_key": str(record.get("specialist_key") or "").strip(),
        "instance_id": str(record.get("instance_id") or "").strip(),
        "capability_digest": str(record.get("capability_digest") or "").strip(),
        "machine_enrollment_scope": _normalize_machine_enrollment_scope(record.get("machine_enrollment_scope")),
    }


def _runtime_session_scope_key(scope: Mapping[str, Any]) -> str:
    return "|".join(
        [
            str(scope.get("tenant_id") or "default").strip() or "default",
            str(scope.get("workspace_id") or "default").strip() or "default",
            str(scope.get("runtime_type") or "local").strip() or "local",
            str(scope.get("runtime_role") or "generic").strip().lower() or "generic",
            str(scope.get("install_id") or "").strip(),
            str(scope.get("specialist_key") or "").strip(),
            str(scope.get("instance_id") or "").strip(),
            str(scope.get("capability_digest") or "").strip(),
            _normalize_machine_enrollment_scope(scope.get("machine_enrollment_scope")),
        ]
    )


def _worker_runtime_scope_allows_run(record: Mapping[str, Any], run_scope: Mapping[str, Any]) -> bool:
    if not isinstance(record, Mapping) or not record:
        return True
    worker_tenant = str(record.get("tenant_id") or "default").strip() or "default"
    worker_workspace = str(record.get("workspace_id") or "default").strip() or "default"
    enrollment_scope = _normalize_machine_enrollment_scope(record.get("machine_enrollment_scope"))
    run_tenant = str(run_scope.get("tenant_id") or "default").strip() or "default"
    run_workspace = str(run_scope.get("workspace_id") or "default").strip() or "default"
    if enrollment_scope == "global":
        return True
    if (
        enrollment_scope == "workspace"
        and run_tenant == "default"
        and worker_workspace != "default"
        and worker_workspace == run_workspace
    ):
        return True
    if worker_tenant != run_tenant:
        return False
    if enrollment_scope == "tenant":
        return True
    return worker_workspace == run_workspace


def _enforce_machine_lease_decision(
    *,
    operation: str,
    holder_id: Optional[str],
    current_holder_id: Optional[str] = None,
    lease_id: Optional[str] = None,
    current_lease_id: Optional[str] = None,
    active_lease_count: int = 0,
    max_concurrent_leases: int = 1,
    lease_ttl_seconds: int = 900,
    stale: bool = False,
    force: bool = False,
    allow_block_result: bool = False,
) -> Dict[str, Any]:
    expected_next_action_map = {
        "acquire": "persist_machine_lease_transition",
        "renew": "persist_machine_lease_transition",
        "release": "release_machine_lease_transition",
        "heartbeat": "touch_machine_lease",
    }
    payload = {
        "operation": operation,
        "holder_id": str(holder_id or "").strip(),
        "requested_holder_id": str(holder_id or "").strip(),
        "current_holder_id": str(current_holder_id or "").strip(),
        "lease_id": str(lease_id or "").strip(),
        "current_lease_id": str(current_lease_id or "").strip(),
        "active_lease_count": max(0, int(active_lease_count or 0)),
        "max_concurrent_leases": max(1, int(max_concurrent_leases or 1)),
        "lease_ttl_seconds": max(1, int(lease_ttl_seconds or 0)),
        "requested_ttl_seconds": max(1, int(lease_ttl_seconds or 0)),
        "stale": bool(stale),
        "expired": bool(stale),
        "force": bool(force),
    }
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced("machine-lease-decision", payload)
        expected_next_action = expected_next_action_map.get(str(operation or "").strip())
        next_action = str(decision.get("next_action") or "").strip()
        if str(decision.get("decision") or "").strip().lower() == "allow" and expected_next_action and next_action != expected_next_action:
            raise HTTPException(
                status_code=409,
                detail=f"Rust machine-lease gate blocked {operation}: unexpected_next_action:{next_action or 'missing'}",
            )
        return decision
    except rust_runtime_kernel_client.RustKernelApprovalRequired as exc:
        if allow_block_result and isinstance(exc.result, dict):
            return dict(exc.result)
        raise HTTPException(status_code=409, detail=f"Rust machine-lease gate requires approval for {operation}: {exc.reason}") from exc
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        if allow_block_result and isinstance(exc.result, dict):
            return dict(exc.result)
        raise HTTPException(status_code=409, detail=f"Rust machine-lease gate blocked {operation}: {exc.reason}") from exc


def _enforce_queue_transition_decision(
    *,
    operation: str,
    run_id: str,
    requester_id: Optional[str],
    lease_holder_id: Optional[str] = None,
    status: str = "queued",
    stale: bool = False,
    force: bool = False,
    attempts: int = 0,
    max_attempts: int = 3,
) -> Dict[str, Any]:
    expected_next_action = {
        "enqueue": "enqueue_queue_item",
        "claim": "claim_queue_item",
        "complete": "complete_queue_item",
        "retry": "retry_queue_item",
        "cancel": "cancel_queue_item",
        "release": "release_queue_item",
        "dead_letter": "dead_letter_queue_item",
    }.get(str(operation or "").strip())
    payload = {
        "operation": operation,
        "item_id": str(run_id or "").strip(),
        "run_id": str(run_id or "").strip(),
        "requester_id": str(requester_id or "").strip(),
        "worker_id": str(requester_id or "").strip(),
        "lease_holder_id": str(lease_holder_id or "").strip(),
        "current_holder_id": str(lease_holder_id or "").strip(),
        "status": str(status or "unknown").strip(),
        "current_status": str(status or "unknown").strip(),
        "stale": bool(stale),
        "lease_stale": bool(stale),
        "force": bool(force),
        "attempts": max(0, int(attempts or 0)),
        "max_attempts": max(1, int(max_attempts or 1)),
    }
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced("queue-transition-decision", payload)
    except rust_runtime_kernel_client.RustKernelApprovalRequired as exc:
        raise HTTPException(status_code=409, detail=f"Rust queue gate requires approval for {operation}: {exc.reason}") from exc
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise HTTPException(status_code=409, detail=f"Rust queue gate blocked {operation}: {exc.reason}") from exc
    if str(operation or "").strip() == "fail":
        expected_next_action = (
            "schedule_queue_retry"
            if bool(decision.get("retryable")) or str(decision.get("next_status") or "").strip() == "retry_scheduled"
            else "fail_queue_item"
        )
    next_action = str(decision.get("next_action") or "").strip()
    if expected_next_action and next_action != expected_next_action:
        raise HTTPException(
            status_code=409,
            detail=f"Rust queue gate returned unexpected next_action for {operation}: {next_action or 'missing'}",
        )
    return decision


def enforce_queue_transition_decision(**request: Any) -> Dict[str, Any]:
    return _enforce_queue_transition_decision(**request)


def _dispatch_claim_repository_write(
    *,
    run_id: str,
    worker_id: str,
    lease_id: Optional[str],
    ttl_seconds: int,
    trace_id: Optional[str],
) -> None:
    def _worker() -> None:
        try:
            run_state_repository.dispatch_repository_call(
                run_state_repository.claim_run(
                    run_id=run_id,
                    worker_id=worker_id,
                    ttl=max(1, int(ttl_seconds or 0)),
                    trace_id=str(trace_id or "").strip(),
                    lease_id=str(lease_id or "").strip() or None,
                ),
                operation=f"claim_run:{run_id}",
            )
        except Exception as exc:
            LOGGER.warning("Failed to dispatch repository claim write for %s: %s", run_id, exc)

    thread = threading.Thread(
        target=_worker,
        name=f"claim-write-{str(run_id or 'run')[:12]}",
        daemon=True,
    )
    thread.start()


def _dispatch_release_repository_write(run_id: str, *, lease_id: Optional[str] = None) -> None:
    try:
        run_state_repository.dispatch_repository_call(
            run_state_repository.release_claim(run_id, lease_id=str(lease_id or "").strip() or None),
            operation=f"release_claim:{run_id}",
        )
    except Exception as exc:
        LOGGER.warning("Failed to dispatch repository claim release for %s: %s", run_id, exc)


def build_machine_presence_record(
    *,
    previous_record: Optional[Mapping[str, Any]],
    machine_id: str,
    current_run_id: Optional[str],
    status_hint: str,
    lease_seconds: int,
    now_iso: str,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    previous = dict(previous_record or {})
    record: Dict[str, Any] = dict(previous)
    normalized_machine_id = str(machine_id or "").strip()
    if not normalized_machine_id:
        return record
    normalized_status = str(status_hint or previous.get("status") or "idle").strip().lower() or "idle"
    clears_run_binding = current_run_id is None and normalized_status in {"idle", "offline", "stopped", "recovering"}
    record.update(
        {
            "worker_id": normalized_machine_id,
            "runtime_id": str(previous.get("runtime_id") or normalized_machine_id).strip() or normalized_machine_id,
            "machine_id": str(previous.get("machine_id") or normalized_machine_id).strip() or normalized_machine_id,
            "last_seen_at": now_iso,
            "status": normalized_status,
            "current_run_id": None if clears_run_binding else (current_run_id if current_run_id is not None else previous.get("current_run_id")),
            "lease_seconds": int(previous.get("lease_seconds") or lease_seconds),
            "note": str(previous.get("note") or ""),
        }
    )
    if note:
        record["note"] = str(note)[:280]
    return record


def build_runtime_registration_record(
    machine_id: str,
    *,
    previous_record: Optional[Mapping[str, Any]],
    tenant_id: Optional[str],
    workspace_id: Optional[str],
    runtime_type: str,
    display_name: Optional[str],
    platform: Optional[str],
    policy_mode: Optional[str],
    capabilities: Optional[List[str]],
    execution_targets: Optional[List[str]],
    instance_id: Optional[str],
    capability_digest: Optional[str],
    prewarm_state: Optional[str],
    warm_pool: Optional[str],
    lease_seconds: int,
    now_iso: str,
    normalize_policy_mode_fn: Callable[[Any], str],
    capability_digest_fn: Callable[[Optional[List[str]]], Optional[str]],
) -> Dict[str, Any]:
    worker = str(machine_id or "").strip()
    if not worker:
        return {}
    previous = dict(previous_record or {})
    record: Dict[str, Any] = dict(previous)
    normalized_capabilities = [
        str(item).strip()
        for item in (
            capabilities if isinstance(capabilities, list) else previous.get("capabilities") or []
        )
        if str(item).strip()
    ]
    record["worker_id"] = worker
    record["runtime_id"] = worker
    record["machine_id"] = str(previous.get("machine_id") or worker).strip() or worker
    record["tenant_id"] = str(tenant_id or previous.get("tenant_id") or "default").strip() or "default"
    record["workspace_id"] = str(workspace_id or previous.get("workspace_id") or "default").strip() or "default"
    record["runtime_type"] = str(runtime_type or previous.get("runtime_type") or "local").strip() or "local"
    record["display_name"] = str(display_name or previous.get("display_name") or worker).strip() or worker
    record["platform"] = str(platform or previous.get("platform") or "").strip() or None
    record["policy_mode"] = normalize_policy_mode_fn(
        policy_mode or previous.get("policy_mode")
    )
    record["capabilities"] = normalized_capabilities
    record["execution_targets"] = [
        str(item).strip()
        for item in (
            execution_targets
            if isinstance(execution_targets, list)
            else previous.get("execution_targets") or []
        )
        if str(item).strip()
    ]
    primary_target = str(record["execution_targets"][0] if record["execution_targets"] else "").strip().lower() or "local"
    record["instance_id"] = str(instance_id or previous.get("instance_id") or worker).strip() or worker
    record["capability_digest"] = (
        str(capability_digest or "").strip()
        or str(previous.get("capability_digest") or "").strip()
        or capability_digest_fn(normalized_capabilities)
    )
    record["queue_shard"] = str(previous.get("queue_shard") or "").strip() or (
        f"{record['tenant_id']}:{record['workspace_id']}:{record['runtime_type']}:{primary_target}"
    )
    record["prewarm_state"] = str(prewarm_state or previous.get("prewarm_state") or "").strip().lower() or None
    record["warm_pool"] = str(warm_pool or previous.get("warm_pool") or "").strip() or None
    record["registered_at"] = str(previous.get("registered_at") or now_iso)
    record["last_registered_at"] = now_iso
    record["lease_seconds"] = int(previous.get("lease_seconds") or lease_seconds)
    record["trust_state"] = str(previous.get("trust_state") or "unverified").strip() or "unverified"
    record["permission_probe"] = (
        dict(previous.get("permission_probe"))
        if isinstance(previous.get("permission_probe"), Mapping)
        else {}
    )
    record["permission_probe_updated_at"] = previous.get("permission_probe_updated_at")
    record["control_state"] = str(previous.get("control_state") or "active").strip().lower() or "active"
    record["suspended_at"] = previous.get("suspended_at")
    record["suspended_reason"] = previous.get("suspended_reason")
    record["revoked_at"] = previous.get("revoked_at")
    record["revoked_reason"] = previous.get("revoked_reason")
    record["control_state_updated_at"] = previous.get("control_state_updated_at")
    return record


def build_queue_partition_key(workspace_id: str, specialist_key: str) -> str:
    workspace = str(workspace_id or "").strip() or "default"
    specialist = str(specialist_key or "").strip() or "workspace-default"
    return f"{workspace}::{specialist}"


def issue_machine_session(
    record: Dict[str, Any],
    *,
    token_factory: Callable[[int], str],
    hash_token_fn: Callable[[str], str],
    issued_at: str,
) -> Dict[str, str]:
    token = token_factory(24)
    record["session_token_hash"] = hash_token_fn(token)
    record["session_issued_at"] = issued_at
    record["session_last_authenticated_at"] = issued_at
    session_scope = _runtime_session_scope(record)
    record["session_scope"] = dict(session_scope)
    record["session_scope_key"] = _runtime_session_scope_key(session_scope)
    record["trust_state"] = "verified"
    return {
        "session_token": token,
        "session_issued_at": issued_at,
    }


def touch_machine_session(record: Dict[str, Any], *, touched_at: str) -> None:
    holder_id = str(
        record.get("machine_id")
        or record.get("runtime_id")
        or record.get("worker_id")
        or record.get("instance_id")
        or record.get("session_scope_key")
        or "runtime-session"
    ).strip() or "runtime-session"
    lease_id = str(
        record.get("lease_id")
        or record.get("machine_lease_id")
        or record.get("session_scope_key")
        or record.get("session_token_hash")
        or holder_id
    ).strip() or holder_id
    _enforce_machine_lease_decision(
        operation="heartbeat",
        holder_id=holder_id,
        current_holder_id=holder_id,
        lease_id=lease_id,
        current_lease_id=lease_id,
        lease_ttl_seconds=int(record.get("lease_seconds") or 900),
        stale=False,
    )
    record["session_last_authenticated_at"] = touched_at
    record["trust_state"] = "verified"


def assert_machine_session(
    machine_id: str,
    session_token: Optional[str],
    *,
    machine_registry: Dict[str, Dict[str, Any]],
    machine_registry_lock: Any,
    instance_id: Optional[str],
    hash_token_fn: Callable[[str], str],
    touch_machine_session_fn: Callable[[Dict[str, Any]], None],
    enforce_scope: bool = True,
) -> Dict[str, Any]:
    runtime_token = str(machine_id or "").strip()
    if not runtime_token:
        raise HTTPException(status_code=400, detail="runtime_id is required.")
    provided = str(session_token or "").strip()
    if not provided:
        raise HTTPException(status_code=401, detail="runtime session token is required.")
    with machine_registry_lock:
        record = (
            machine_registry.get(runtime_token)
            if isinstance(machine_registry.get(runtime_token), dict)
            else None
        )
        if not isinstance(record, dict):
            raise HTTPException(status_code=404, detail="runtime_id is not registered.")
        expected_hash = str(record.get("session_token_hash") or "").strip()
        if not expected_hash:
            raise HTTPException(status_code=409, detail="runtime session is not active. Re-register this machine.")
        if hash_token_fn(provided) != expected_hash:
            raise HTTPException(status_code=401, detail="runtime session is no longer valid. Re-register this machine.")
        expected_instance = str(record.get("instance_id") or "").strip()
        provided_instance = str(instance_id or "").strip()
        if expected_instance and provided_instance and expected_instance != provided_instance:
            raise HTTPException(status_code=409, detail="runtime instance changed. Re-register this machine.")
        current_scope = _runtime_session_scope(record)
        expected_scope_key = str(record.get("session_scope_key") or "").strip()
        if not expected_scope_key:
            expected_scope = record.get("session_scope") if isinstance(record.get("session_scope"), Mapping) else None
            if isinstance(expected_scope, Mapping):
                expected_scope_key = _runtime_session_scope_key(expected_scope)
                record["session_scope_key"] = expected_scope_key
            else:
                record["session_scope"] = dict(current_scope)
                record["session_scope_key"] = _runtime_session_scope_key(current_scope)
                expected_scope_key = str(record.get("session_scope_key") or "").strip()
        current_scope_key = _runtime_session_scope_key(current_scope)
        if expected_scope_key != current_scope_key:
            record["session_scope_mismatch"] = {
                "expected": dict(record.get("session_scope")) if isinstance(record.get("session_scope"), Mapping) else {},
                "current": dict(current_scope),
            }
            if enforce_scope:
                raise HTTPException(status_code=409, detail="runtime scope changed. Re-register this machine.")
        else:
            record.pop("session_scope_mismatch", None)
        touch_machine_session_fn(record)
        machine_registry[runtime_token] = record
        return dict(record)


def build_machine_lease_record(
    *,
    machine_id: str,
    run_id: str,
    workspace_id: str,
    actor_id: str,
    ttl_seconds: int,
    claimed_at: str,
    capabilities_requested: Optional[List[str]] = None,
    capabilities_granted: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    lease_id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
) -> Dict[str, Any]:
    lease = MachineLease(
        lease_id=str(lease_id_factory() or "").strip() or uuid.uuid4().hex,
        machine_id=str(machine_id or "").strip(),
        run_id=str(run_id or "").strip(),
        workspace_id=str(workspace_id or "").strip(),
        actor_id=str(actor_id or "").strip(),
        ttl_seconds=int(ttl_seconds or 0),
        capabilities_requested=list(capabilities_requested or []),
        capabilities_granted=list(capabilities_granted or []),
        metadata=dict(metadata or {}),
    )
    return {
        "worker_id": lease.machine_id,
        "machine_id": lease.machine_id,
        "lease_id": lease.lease_id,
        "run_id": lease.run_id,
        "workspace_id": lease.workspace_id,
        "actor_id": lease.actor_id,
        "claimed_at": claimed_at,
        "last_heartbeat_at": claimed_at,
        "last_progress_event_at": claimed_at,
        "lease_seconds": lease.ttl_seconds,
        "capabilities_requested": list(lease.capabilities_requested),
        "capabilities_granted": list(lease.capabilities_granted),
        "metadata": dict(lease.metadata),
    }


def _run_scope_from_payload(run: Mapping[str, Any]) -> Dict[str, str]:
    context = run.get("context") if isinstance(run.get("context"), Mapping) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), Mapping) else {}
    workspace_id = str(
        run.get("workspace_id")
        or context.get("workspace_id")
        or metadata.get("workspace_id")
        or "default"
    ).strip() or "default"
    tenant_id = str(
        run.get("tenant_id")
        or context.get("tenant_id")
        or metadata.get("tenant_id")
        or "default"
    ).strip() or "default"
    specialist_key = str(
        metadata.get("active_agent_install_id")
        or metadata.get("agent_install_id")
        or metadata.get("agent_role")
        or "workspace-default"
    ).strip() or "workspace-default"
    return {
        "workspace_id": workspace_id,
        "tenant_id": tenant_id,
        "specialist_key": specialist_key,
    }


def _metadata_for_run(run: Mapping[str, Any]) -> Dict[str, Any]:
    context = run.get("context") if isinstance(run.get("context"), Mapping) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), Mapping) else {}
    return dict(metadata)


def _count_claims_by_scope(
    *,
    claimed_runs: Mapping[str, Dict[str, Any]],
    runs_by_id: Mapping[str, Any],
) -> tuple[Dict[str, int], Dict[str, int]]:
    workspace_counts: Dict[str, int] = {}
    specialist_counts: Dict[str, int] = {}
    for run_id, claim in claimed_runs.items():
        if not isinstance(claim, Mapping):
            continue
        run = runs_by_id.get(run_id)
        if not isinstance(run, Mapping):
            continue
        scope = _run_scope_from_payload(run)
        workspace_counts[scope["workspace_id"]] = workspace_counts.get(scope["workspace_id"], 0) + 1
        specialist_counts[scope["specialist_key"]] = specialist_counts.get(scope["specialist_key"], 0) + 1
    return workspace_counts, specialist_counts


def _count_pending_by_scope(
    *,
    pending_run_ids: List[str],
    runs_by_id: Mapping[str, Any],
) -> tuple[Dict[str, int], Dict[str, int]]:
    workspace_counts: Dict[str, int] = {}
    specialist_counts: Dict[str, int] = {}
    for run_id in pending_run_ids:
        run = runs_by_id.get(run_id)
        if not isinstance(run, Mapping):
            continue
        status = str(run.get("status") or "").strip().lower()
        if status not in {"queued_local", "starting"}:
            continue
        scope = _run_scope_from_payload(run)
        workspace_counts[scope["workspace_id"]] = workspace_counts.get(scope["workspace_id"], 0) + 1
        specialist_counts[scope["specialist_key"]] = specialist_counts.get(scope["specialist_key"], 0) + 1
    return workspace_counts, specialist_counts


def _worker_is_online_for_claim(
    record: Mapping[str, Any],
    *,
    now_dt: Optional[Any],
    parse_utc_ts_fn: Optional[Callable[[Any], Any]],
) -> bool:
    lifecycle_state = str(record.get("lifecycle_state") or "").strip().lower()
    if lifecycle_state in {"stopped", "recovering"}:
        return False
    control_state = str(record.get("control_state") or "active").strip().lower() or "active"
    if control_state in {"interrupting", "suspended", "revoked"}:
        return False
    status = str(record.get("status") or "idle").strip().lower() or "idle"
    if status in {"offline", "stopped", "recovering"}:
        return False
    if now_dt is None or not callable(parse_utc_ts_fn):
        return True
    seen_at = parse_utc_ts_fn(record.get("last_seen_at"))
    if seen_at is None:
        return status not in {"offline", "stopped"}
    lease_seconds = max(1, int(record.get("lease_seconds") or 30))
    return now_dt <= seen_at + timedelta(seconds=lease_seconds)


def _best_online_preferred_runtime_from_registry(
    preferred_runtime_ids: List[str],
    *,
    worker_registry: Mapping[str, Any],
    now_dt: Optional[Any],
    parse_utc_ts_fn: Optional[Callable[[Any], Any]],
) -> Optional[str]:
    for runtime_id in preferred_runtime_ids:
        token = str(runtime_id or "").strip()
        if not token:
            continue
        record = worker_registry.get(token)
        if not isinstance(record, Mapping):
            continue
        if _worker_is_online_for_claim(record, now_dt=now_dt, parse_utc_ts_fn=parse_utc_ts_fn):
            return token
    return None


def claim_local_machine_lease(
    worker_id: str,
    *,
    required_capabilities: Optional[List[str]],
    local_queue_lock: Any,
    pending_run_ids: List[str],
    claimed_runs: Dict[str, Dict[str, Any]],
    worker_registry: Mapping[str, Any],
    runs_by_id: Mapping[str, Any],
    lease_seconds: int,
    cleanup_stale_local_claims_fn: Callable[[], Any],
    ordered_runtime_preferences_for_run_fn: Callable[[Dict[str, Any]], List[str]],
    best_online_preferred_runtime_fn: Callable[[List[str]], Optional[str]],
    required_capabilities_for_run_fn: Callable[[Dict[str, Any]], List[str]],
    normalize_capability_ids_fn: Callable[[Any], List[str]],
    persist_local_runtime_state_fn: Callable[[], Any],
    mark_local_worker_seen_fn: Callable[..., Any],
    now_iso_fn: Callable[[], str],
    parse_utc_ts_fn: Optional[Callable[[Any], Any]] = None,
    now_fn: Optional[Callable[[], Any]] = None,
    lease_id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
) -> Optional[str]:
    cleanup_stale_local_claims_fn()
    claimed_run_id: Optional[str] = None
    deferred_run_ids: List[str] = []
    capability_filtered = False
    state_changed = False
    deferred_worker_presence: Optional[tuple[str, Optional[str], str, str]] = None

    with local_queue_lock:
        worker_state = (
            worker_registry.get(worker_id)
            if isinstance(worker_registry.get(worker_id), dict)
            else {}
        )
        worker_capabilities = normalize_capability_ids_fn(
            worker_state.get("capabilities") if isinstance(worker_state, dict) else []
        )
        worker_control_state = str(
            (worker_state.get("control_state") if isinstance(worker_state, dict) else None) or "active"
        ).strip().lower() or "active"
        worker_capability_set = set(worker_capabilities)
        requested_capability_filter = set(normalize_capability_ids_fn(required_capabilities))
        machine_id = str(
            (worker_state.get("machine_id") if isinstance(worker_state, dict) else None)
            or (worker_state.get("runtime_id") if isinstance(worker_state, dict) else None)
            or worker_id
        ).strip() or str(worker_id or "").strip()
        if worker_control_state in {"interrupting", "suspended", "revoked"}:
            deferred_worker_presence = (
                worker_id,
                None,
                "idle",
                f"idle_machine_{worker_control_state}",
            )
        if deferred_worker_presence is None:
            pending_snapshot = list(pending_run_ids)
            pending_workspace_counts, pending_specialist_counts = _count_pending_by_scope(
                pending_run_ids=pending_snapshot,
                runs_by_id=runs_by_id,
            )
            active_workspace_counts, active_specialist_counts = _count_claims_by_scope(
                claimed_runs=claimed_runs,
                runs_by_id=runs_by_id,
            )
            now_dt = now_fn() if callable(now_fn) else None
            eligible: List[Dict[str, Any]] = []
            remaining_pending: List[str] = []
            for queue_index, run_id in enumerate(pending_snapshot):
                run = runs_by_id.get(run_id)
                if not isinstance(run, dict):
                    continue
                status = str(run.get("status") or "").strip().lower()
                if status not in {"queued_local", "starting"}:
                    continue
                metadata = _metadata_for_run(run)
                retry_due_at = metadata.get("local_queue_next_retry_at")
                if retry_due_at and now_dt is not None and callable(parse_utc_ts_fn):
                    due_at = parse_utc_ts_fn(retry_due_at)
                    if due_at is not None and now_dt < due_at:
                        deferred_run_ids.append(run_id)
                        remaining_pending.append(run_id)
                        capability_filtered = True
                        continue
                preferred_runtime_ids = ordered_runtime_preferences_for_run_fn(run)
                preferred_runtime_id = _best_online_preferred_runtime_from_registry(
                    preferred_runtime_ids,
                    worker_registry=worker_registry,
                    now_dt=now_dt,
                    parse_utc_ts_fn=parse_utc_ts_fn,
                )
                if preferred_runtime_id and preferred_runtime_id != worker_id:
                    deferred_run_ids.append(run_id)
                    remaining_pending.append(run_id)
                    capability_filtered = True
                    continue
                preferred_match_rank = 0 if preferred_runtime_id and preferred_runtime_id == worker_id else 1
                run_required_capabilities = required_capabilities_for_run_fn(run)
                required_capability_set = set(run_required_capabilities)
                if run_required_capabilities and not required_capability_set.issubset(worker_capability_set):
                    deferred_run_ids.append(run_id)
                    remaining_pending.append(run_id)
                    capability_filtered = True
                    continue
                if (
                    requested_capability_filter
                    and run_required_capabilities
                    and not required_capability_set.issubset(requested_capability_filter)
                ):
                    deferred_run_ids.append(run_id)
                    remaining_pending.append(run_id)
                    capability_filtered = True
                    continue
                scope = _run_scope_from_payload(run)
                if not _worker_runtime_scope_allows_run(worker_state, scope):
                    deferred_run_ids.append(run_id)
                    remaining_pending.append(run_id)
                    capability_filtered = True
                    continue
                retry_count = max(0, int(metadata.get("local_queue_retry_count") or 0))
                eligible.append(
                    {
                        "queue_index": queue_index,
                        "run_id": run_id,
                        "run": run,
                        "required_capabilities": run_required_capabilities,
                        "scope": scope,
                        "score": (
                            preferred_match_rank,
                            active_workspace_counts.get(scope["workspace_id"], 0),
                            active_specialist_counts.get(scope["specialist_key"], 0),
                            pending_workspace_counts.get(scope["workspace_id"], 0),
                            pending_specialist_counts.get(scope["specialist_key"], 0),
                            retry_count,
                            queue_index,
                        ),
                    }
                )
                remaining_pending.append(run_id)
            if eligible:
                chosen = min(eligible, key=lambda item: item["score"])
                claimed_run_id = str(chosen["run_id"] or "").strip()
                run = chosen["run"]
                run_required_capabilities = list(chosen["required_capabilities"] or [])
                scope = dict(chosen["scope"] or {})
                now_iso = now_iso_fn()
                context = run.get("context") if isinstance(run.get("context"), dict) else {}
                metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
                workspace_id = str(scope.get("workspace_id") or "").strip()
                actor_id = str(
                    metadata.get("owner_user_id")
                    or metadata.get("user_id")
                    or context.get("user_id")
                    or ""
                ).strip()
                active_worker_lease_count = len(
                    [
                        item
                        for item in claimed_runs.values()
                        if isinstance(item, Mapping)
                        and str(item.get("worker_id") or item.get("machine_id") or "").strip() == worker_id
                    ]
                )
                max_concurrent_leases = max(1, int(worker_state.get("max_concurrent_sessions") or 1))
                _enforce_machine_lease_decision(
                    operation="acquire",
                    holder_id=worker_id,
                    active_lease_count=active_worker_lease_count,
                    max_concurrent_leases=max_concurrent_leases,
                    lease_ttl_seconds=lease_seconds,
                )
                _enforce_queue_transition_decision(
                    operation="claim",
                    run_id=claimed_run_id,
                    requester_id=worker_id,
                    lease_holder_id=None,
                    status=str(run.get("status") or "queued_local").strip() or "queued_local",
                )
                claim_record = build_machine_lease_record(
                    machine_id=machine_id,
                    run_id=claimed_run_id,
                    workspace_id=workspace_id,
                    actor_id=actor_id,
                    ttl_seconds=lease_seconds,
                    claimed_at=now_iso,
                    capabilities_requested=run_required_capabilities,
                    capabilities_granted=[
                        capability
                        for capability in run_required_capabilities
                        if capability in worker_capability_set
                    ],
                    metadata={
                        "runtime_id": worker_id,
                        "contention_strategy": "workspace_specialist_weighted_fifo",
                        "tenant_id": scope.get("tenant_id"),
                        "specialist_key": scope.get("specialist_key"),
                        "queue_partition": build_queue_partition_key(
                            str(scope.get("workspace_id") or "").strip(),
                            str(scope.get("specialist_key") or "").strip(),
                        ),
                    },
                    lease_id_factory=lease_id_factory,
                )
                claimed_runs[claimed_run_id] = claim_record
                pending_run_ids[:] = [run_id for run_id in remaining_pending if run_id != claimed_run_id]
                state_changed = True
            elif remaining_pending != pending_snapshot:
                pending_run_ids[:] = remaining_pending
                state_changed = True

    if state_changed:
        persist_local_runtime_state_fn()
    if claimed_run_id:
        claim = claimed_runs.get(claimed_run_id) if isinstance(claimed_runs.get(claimed_run_id), dict) else {}
        claim_trace_id = ""
        run = runs_by_id.get(claimed_run_id)
        if isinstance(run, dict):
            context = run.get("context") if isinstance(run.get("context"), dict) else {}
            metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
            claim_trace_id = str(run.get("trace_id") or metadata.get("trace_id") or metadata.get("request_trace_id") or "").strip()
        _dispatch_claim_repository_write(
            run_id=claimed_run_id,
            worker_id=worker_id,
            lease_id=str(claim.get("lease_id") or "").strip() or None,
            ttl_seconds=int(claim.get("lease_seconds") or lease_seconds),
            trace_id=claim_trace_id,
        )
        mark_local_worker_seen_fn(worker_id, claimed_run_id, "busy", note="claimed_local_run")
    elif deferred_worker_presence is not None:
        mark_local_worker_seen_fn(*deferred_worker_presence)
    else:
        mark_local_worker_seen_fn(
            worker_id,
            None,
            "idle",
            note="idle_capability_wait" if capability_filtered else "idle_poll",
        )
    return claimed_run_id


def bind_machine_lease_to_run(
    run: Dict[str, Any],
    *,
    worker_id: str,
    claim: Mapping[str, Any],
    worker_state: Optional[Mapping[str, Any]],
    now_iso: str,
    normalize_policy_mode_fn: Callable[[Any], str],
) -> None:
    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    metadata["runtime_id"] = worker_id
    metadata["policy_mode"] = normalize_policy_mode_fn(
        worker_state.get("policy_mode") if isinstance(worker_state, dict) else None
    )
    if claim.get("machine_id"):
        metadata["machine_id"] = claim.get("machine_id")
    if claim.get("lease_id"):
        metadata["machine_lease_id"] = claim.get("lease_id")
    context["metadata"] = metadata
    run["context"] = context
    run["local_worker_id"] = worker_id
    run["machine_id"] = claim.get("machine_id") or worker_id
    run["machine_lease_id"] = claim.get("lease_id")
    run["local_claimed_at"] = now_iso
    run["local_last_heartbeat_at"] = now_iso
    run["lease_seconds"] = int(claim.get("lease_seconds") or 0)
    run.pop("_resume_after_confirmation_scheduled", None)


def clear_active_machine_lease_binding(run: Dict[str, Any]) -> None:
    run["local_worker_id"] = None
    run["local_claimed_at"] = None
    run["local_last_heartbeat_at"] = None
    run["machine_lease_id"] = None


def release_machine_lease_claim(
    run_id: str,
    *,
    worker_id: Optional[str],
    lease_id: Optional[str] = None,
    local_queue_lock: Any,
    claimed_runs: Dict[str, Dict[str, Any]],
    persist_local_runtime_state_fn: Optional[Callable[[], Any]] = None,
    mark_local_worker_seen_fn: Optional[Callable[..., Any]] = None,
    status_hint: Optional[str] = None,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    with local_queue_lock:
        claim = claimed_runs.get(run_id)
        incoming_worker = str(worker_id or "").strip()
        incoming_lease = str(lease_id or "").strip()
        resolved_worker = incoming_worker or (
            str(claim.get("worker_id") or "").strip() if isinstance(claim, dict) else ""
        )
        released_claim = dict(claim) if isinstance(claim, dict) else None
        if isinstance(released_claim, dict):
            _enforce_queue_transition_decision(
                operation="release",
                run_id=run_id,
                requester_id=resolved_worker,
                lease_holder_id=str(released_claim.get("worker_id") or released_claim.get("machine_id") or "").strip(),
                status="claimed",
            )
            lease_decision = _enforce_machine_lease_decision(
                operation="release",
                holder_id=resolved_worker,
                current_holder_id=str(released_claim.get("worker_id") or released_claim.get("machine_id") or "").strip(),
                lease_id=incoming_lease or str(released_claim.get("lease_id") or "").strip(),
                current_lease_id=str(released_claim.get("lease_id") or "").strip(),
                force=False,
                allow_block_result=True,
            )
            if str(lease_decision.get("decision") or "").strip().lower() == "block":
                reason = str(lease_decision.get("reason") or "").strip().lower()
                if reason == "lease_release_not_owned":
                    raise HTTPException(status_code=403, detail="Worker does not own this local run.")
                if reason == "lease_release_id_mismatch":
                    return {
                        "claim": dict(released_claim),
                        "resolved_worker": resolved_worker,
                        "released": False,
                        "lease_mismatch": True,
                        "decision_id": lease_decision.get("decision_id"),
                    }
                raise HTTPException(status_code=409, detail=f"Rust machine-lease gate blocked release: {reason or 'unknown_reason'}")
        released = claimed_runs.pop(run_id, None) is not None

    if released and callable(persist_local_runtime_state_fn):
        persist_local_runtime_state_fn()
    if released:
        _dispatch_release_repository_write(run_id, lease_id=str((released_claim or {}).get("lease_id") or "").strip() or None)
    if resolved_worker and callable(mark_local_worker_seen_fn) and status_hint:
        mark_local_worker_seen_fn(resolved_worker, None, status_hint, note=note or None)
    return {
        "claim": released_claim,
        "resolved_worker": resolved_worker,
        "released": released,
    }


def reconcile_machine_lease_release(
    run_id: str,
    *,
    local_queue_lock: Any,
    local_pending_run_ids: List[str],
    local_claimed_runs: Dict[str, Dict[str, Any]],
    sync_local_runtime_state_snapshot_fn: Callable[[], Any],
) -> bool:
    changed = False
    released_claim: Optional[Dict[str, Any]] = None
    with local_queue_lock:
        if run_id in local_pending_run_ids:
            local_pending_run_ids[:] = [rid for rid in local_pending_run_ids if rid != run_id]
            changed = True
        popped = local_claimed_runs.get(run_id)
        if isinstance(popped, dict):
            _enforce_queue_transition_decision(
                operation="release",
                run_id=run_id,
                requester_id=str(popped.get("worker_id") or popped.get("machine_id") or "").strip(),
                lease_holder_id=str(popped.get("worker_id") or popped.get("machine_id") or "").strip(),
                status="claimed",
            )
            _enforce_machine_lease_decision(
                operation="release",
                holder_id=str(popped.get("worker_id") or popped.get("machine_id") or "").strip(),
                current_holder_id=str(popped.get("worker_id") or popped.get("machine_id") or "").strip(),
                lease_id=str(popped.get("lease_id") or "").strip(),
                current_lease_id=str(popped.get("lease_id") or "").strip(),
                force=False,
            )
            local_claimed_runs.pop(run_id, None)
            released_claim = dict(popped)
            changed = True
    if changed:
        _dispatch_release_repository_write(run_id, lease_id=str((released_claim or {}).get("lease_id") or "").strip() or None)
        sync_local_runtime_state_snapshot_fn()
    return changed


def reconcile_recovered_machine_leases(
    recovered_run_ids: List[str],
    *,
    local_queue_lock: Any,
    local_pending_run_ids: List[str],
    local_claimed_runs: Dict[str, Dict[str, Any]],
    persist_local_runtime_state_fn: Callable[[], Any],
) -> bool:
    recovered_set = {str(run_id) for run_id in recovered_run_ids if str(run_id or "").strip()}
    if not recovered_set:
        return False
    changed = False
    released_claims: Dict[str, Dict[str, Any]] = {}
    with local_queue_lock:
        next_pending = [run_id for run_id in local_pending_run_ids if run_id not in recovered_set]
        if next_pending != list(local_pending_run_ids):
            local_pending_run_ids[:] = next_pending
            changed = True
        for run_id in recovered_set:
            popped = local_claimed_runs.get(run_id)
            if isinstance(popped, dict):
                _enforce_queue_transition_decision(
                    operation="release",
                    run_id=run_id,
                    requester_id=str(popped.get("worker_id") or popped.get("machine_id") or "").strip(),
                    lease_holder_id=str(popped.get("worker_id") or popped.get("machine_id") or "").strip(),
                    status="claimed",
                )
                _enforce_machine_lease_decision(
                    operation="release",
                    holder_id=str(popped.get("worker_id") or popped.get("machine_id") or "").strip(),
                    current_holder_id=str(popped.get("worker_id") or popped.get("machine_id") or "").strip(),
                    lease_id=str(popped.get("lease_id") or "").strip(),
                    current_lease_id=str(popped.get("lease_id") or "").strip(),
                    force=False,
                )
                local_claimed_runs.pop(run_id, None)
                released_claims[run_id] = dict(popped)
                changed = True
    if changed:
        for run_id in recovered_set:
            _dispatch_release_repository_write(
                run_id,
                lease_id=str((released_claims.get(run_id) or {}).get("lease_id") or "").strip() or None,
            )
        persist_local_runtime_state_fn()
    return changed


def cleanup_stale_machine_leases(
    *,
    now: Any,
    local_queue_lock: Any,
    claimed_runs: Dict[str, Dict[str, Any]],
    worker_registry: Dict[str, Dict[str, Any]],
    runs_by_id: Mapping[str, Any],
    parse_utc_ts_fn: Callable[[Any], Any],
    utc_now_iso_fn: Callable[[], str],
    persist_local_runtime_state_fn: Callable[[], Any],
    emit_log_fn: Callable[..., Any],
    set_run_status_fn: Callable[[str, str], Any],
    schedule_restored_run_resume_fn: Optional[Callable[[str, Dict[str, Any]], bool]],
    checkpoint_recovery_max_auto_retries: int,
    checkpoint_recovery_backoff_seconds: List[int],
    local_worker_lost_timeout_seconds: int,
    default_lease_seconds: int,
    local_pending_run_ids: Optional[List[str]] = None,
    max_queue_retry_attempts: int = 0,
    queue_retry_backoff_seconds: Optional[List[int]] = None,
    append_dead_letter_fn: Optional[Callable[..., Any]] = None,
) -> List[str]:
    stale: List[Dict[str, Any]] = []
    changed = False

    with local_queue_lock:
        for run_id, claim in list(claimed_runs.items()):
            if not isinstance(claim, dict):
                claimed_runs.pop(run_id, None)
                changed = True
                continue
            lease_seconds = max(10, int(claim.get("lease_seconds") or default_lease_seconds))
            last_heartbeat = parse_utc_ts_fn(claim.get("last_heartbeat_at")) or parse_utc_ts_fn(claim.get("claimed_at"))
            if last_heartbeat is None:
                last_heartbeat = now
            if (now - last_heartbeat).total_seconds() <= local_worker_lost_timeout_seconds:
                continue

            worker_id = str(claim.get("worker_id") or "").strip() or None
            machine_id = str(claim.get("machine_id") or worker_id or "").strip() or None
            _enforce_queue_transition_decision(
                operation="release",
                run_id=run_id,
                requester_id=worker_id or machine_id,
                lease_holder_id=worker_id or machine_id,
                status="claimed",
                stale=True,
            )
            _enforce_machine_lease_decision(
                operation="release",
                holder_id=worker_id or machine_id,
                current_holder_id=worker_id or machine_id,
                lease_id=str(claim.get("lease_id") or "").strip(),
                current_lease_id=str(claim.get("lease_id") or "").strip(),
                stale=True,
                force=False,
            )
            claimed_runs.pop(run_id, None)
            changed = True
            _dispatch_release_repository_write(run_id, lease_id=str(claim.get("lease_id") or "").strip() or None)
            if worker_id:
                worker_state = (
                    worker_registry.get(worker_id)
                    if isinstance(worker_registry.get(worker_id), dict)
                    else {}
                )
                last_seen_at = (
                    worker_state.get("last_seen_at")
                    or claim.get("last_heartbeat_at")
                    or claim.get("claimed_at")
                    or utc_now_iso_fn()
                )
                worker_registry[worker_id] = build_machine_presence_record(
                    previous_record=worker_state,
                    machine_id=worker_id,
                    current_run_id=None,
                    status_hint="offline",
                    lease_seconds=int(worker_state.get("lease_seconds") or lease_seconds),
                    now_iso=str(last_seen_at),
                    note=worker_state.get("note") if isinstance(worker_state, dict) else None,
                )
                changed = True
            stale.append(
                {
                    "run_id": run_id,
                    "worker_id": worker_id,
                    "machine_id": machine_id,
                    "lease_seconds": lease_seconds,
                    "last_heartbeat_at": claim.get("last_heartbeat_at"),
                }
            )
    if changed:
        persist_local_runtime_state_fn()

    for item in stale:
        run_id = str(item.get("run_id") or "")
        run = runs_by_id.get(run_id)
        if not isinstance(run, dict):
            continue
        status = str(run.get("status") or "").strip().lower()
        if status in {"completed", "failed", "timeout"}:
            continue
        checkpoint = run.get("browser_checkpoint") if isinstance(run.get("browser_checkpoint"), dict) else {}
        context = run.get("context") if isinstance(run.get("context"), dict) else {}
        metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
        scope = _run_scope_from_payload(run)
        selected_target = str(
            metadata.get("execution_target_selected")
            or metadata.get("execution_target")
            or ""
        ).strip().lower()
        clear_active_machine_lease_binding(run)
        if checkpoint and selected_target in {"local", "local_companion"}:
            previous_attempts = int(metadata.get("local_worker_recovery_attempt_count") or 0)
            attempt_count = previous_attempts + 1
            max_auto_retries = max(1, int(checkpoint_recovery_max_auto_retries or 1))
            backoff_schedule = [max(0, int(item or 0)) for item in (checkpoint_recovery_backoff_seconds or [])]
            if not backoff_schedule:
                backoff_schedule = [0]
            if attempt_count > max_auto_retries:
                if callable(append_dead_letter_fn):
                    try:
                        append_dead_letter_fn(
                            run_id=run_id,
                            tenant_id=scope["tenant_id"],
                            workspace_id=scope["workspace_id"],
                            specialist_key=scope["specialist_key"],
                            reason="checkpoint_recovery_exhausted",
                            trace_id=str(run.get("trace_id") or metadata.get("trace_id") or "").strip(),
                            failure_count=previous_attempts,
                            payload={
                                "run_id": run_id,
                                "worker_id": item.get("worker_id"),
                                "machine_id": item.get("machine_id"),
                                "last_heartbeat_at": item.get("last_heartbeat_at"),
                                "next_action_index": checkpoint.get("next_action_index"),
                                "session_profile": checkpoint.get("session_profile"),
                                "attempt_count": previous_attempts,
                                "max_auto_retries": max_auto_retries,
                                "failure_kind": "checkpoint_recovery",
                            },
                        )
                    except Exception:
                        LOGGER.warning("Failed to append checkpoint dead-letter for %s", run_id, exc_info=True)
                run["result"] = "Worker lost connection repeatedly. Automatic checkpoint recovery is paused."
                run["result_data"] = {
                    "summary": "Worker lost connection repeatedly. Automatic checkpoint recovery is paused.",
                    "error": "local_worker_recovery_exhausted",
                    "resume_available": True,
                    "manual_confirmation_required": True,
                    "worker_id": item.get("worker_id"),
                    "machine_id": item.get("machine_id"),
                    "last_heartbeat_at": item.get("last_heartbeat_at"),
                    "next_action_index": checkpoint.get("next_action_index"),
                    "session_profile": checkpoint.get("session_profile"),
                    "attempt_count": previous_attempts,
                    "max_auto_retries": max_auto_retries,
                }
                metadata["browser_resume_supported"] = True
                metadata["resume_ready"] = True
                metadata["local_worker_recovery_reason"] = "worker_lost"
                metadata["local_worker_recovery_auto_retry_exhausted"] = True
                metadata["local_worker_recovery_manual_confirmation_required"] = True
                metadata["local_worker_recovery_attempt_count"] = previous_attempts
                metadata["local_worker_recovery_next_retry_at"] = None
                metadata["local_worker_recovery_backoff_seconds"] = 0
                context["metadata"] = metadata
                run["context"] = context
                log_queue = run.get("logs")
                if log_queue is not None:
                    emit_log_fn(
                        log_queue,
                        "error",
                        "Worker lost connection repeatedly. Automatic checkpoint recovery is paused.",
                        event="local_worker_recovery_exhausted",
                        data={
                            "run_id": run_id,
                            "worker_id": item.get("worker_id"),
                            "machine_id": item.get("machine_id"),
                            "attempt_count": previous_attempts,
                            "max_auto_retries": max_auto_retries,
                            "next_action_index": checkpoint.get("next_action_index"),
                            "session_profile": checkpoint.get("session_profile"),
                        },
                    )
                set_run_status_fn(run_id, "waiting_for_input")
                continue

            backoff_index = min(max(0, attempt_count - 1), len(backoff_schedule) - 1)
            backoff_seconds = backoff_schedule[backoff_index]
            next_retry_at = None
            if backoff_seconds > 0:
                try:
                    next_retry_at = (now + timedelta(seconds=backoff_seconds)).isoformat().replace("+00:00", "Z")
                except Exception:
                    next_retry_at = utc_now_iso_fn()

            run["result"] = (
                "Worker lost connection. Recovery from saved browser checkpoint is delayed before retry."
                if backoff_seconds > 0
                else "Worker lost connection. Recovering from saved browser checkpoint."
            )
            run["result_data"] = {
                "summary": run["result"],
                "error": "local_worker_lost_recoverable",
                "resume_available": True,
                "worker_id": item.get("worker_id"),
                "machine_id": item.get("machine_id"),
                "last_heartbeat_at": item.get("last_heartbeat_at"),
                "next_action_index": checkpoint.get("next_action_index"),
                "session_profile": checkpoint.get("session_profile"),
                "attempt_count": attempt_count,
                "max_auto_retries": max_auto_retries,
                "retry_backoff_seconds": backoff_seconds,
                "next_retry_at": next_retry_at,
            }
            metadata["browser_resume_supported"] = True
            metadata["resume_ready"] = True
            metadata["local_worker_recovery_reason"] = "worker_lost"
            metadata["local_worker_recovery_attempt_count"] = attempt_count
            metadata["local_worker_recovery_backoff_seconds"] = backoff_seconds
            metadata["local_worker_recovery_max_auto_retries"] = max_auto_retries
            metadata["local_worker_recovery_auto_retry_exhausted"] = False
            metadata["local_worker_recovery_manual_confirmation_required"] = False
            metadata["local_worker_recovery_next_retry_at"] = next_retry_at
            context["metadata"] = metadata
            run["context"] = context
            log_queue = run.get("logs")
            if log_queue is not None:
                emit_log_fn(
                    log_queue,
                    "warn",
                    "Worker lost connection. Recovering from saved browser checkpoint.",
                    event="local_worker_lost_recoverable",
                    data={
                        "run_id": run_id,
                        "worker_id": item.get("worker_id"),
                        "machine_id": item.get("machine_id"),
                        "lease_seconds": item.get("lease_seconds"),
                        "last_heartbeat_at": item.get("last_heartbeat_at"),
                        "next_action_index": checkpoint.get("next_action_index"),
                        "session_profile": checkpoint.get("session_profile"),
                        "attempt_count": attempt_count,
                        "retry_backoff_seconds": backoff_seconds,
                        "next_retry_at": next_retry_at,
                    },
                )
            set_run_status_fn(run_id, "waiting_for_input")
            if backoff_seconds > 0:
                if log_queue is not None:
                    emit_log_fn(
                        log_queue,
                        "warn",
                        f"Automatic checkpoint recovery will retry in {backoff_seconds} second(s).",
                        event="local_worker_recovery_backoff",
                        data={
                            "run_id": run_id,
                            "attempt_count": attempt_count,
                            "retry_backoff_seconds": backoff_seconds,
                            "next_retry_at": next_retry_at,
                        },
                    )
                continue
            resumed = False
            if callable(schedule_restored_run_resume_fn):
                try:
                    resumed = bool(schedule_restored_run_resume_fn(run_id, run))
                except Exception:
                    resumed = False
            if resumed:
                metadata["local_worker_recovery_next_retry_at"] = None
                metadata["local_worker_recovery_backoff_seconds"] = 0
                metadata["local_worker_recovery_last_resume_scheduled_at"] = utc_now_iso_fn()
                context["metadata"] = metadata
                run["context"] = context
                if log_queue is not None:
                    emit_log_fn(
                        log_queue,
                        "info",
                        "Automatic local checkpoint recovery was scheduled after worker loss.",
                        event="local_resume_scheduled_after_worker_loss",
                        data={
                            "run_id": run_id,
                            "worker_id": item.get("worker_id"),
                            "machine_id": item.get("machine_id"),
                            "next_action_index": checkpoint.get("next_action_index"),
                            "session_profile": checkpoint.get("session_profile"),
                            "attempt_count": attempt_count,
                        },
                    )
                continue
            run.pop("_resume_after_confirmation_scheduled", None)
        should_retry_local_queue = selected_target in {"local", "local_companion"} and local_pending_run_ids is not None
        previous_retry_count = int(metadata.get("local_queue_retry_count") or 0)
        queue_retry_limit = max(0, int(metadata.get("local_queue_max_retries") or max_queue_retry_attempts or 0))
        queue_backoff_schedule = [max(0, int(value or 0)) for value in (queue_retry_backoff_seconds or [])]
        if should_retry_local_queue and previous_retry_count < queue_retry_limit:
            attempt_count = previous_retry_count + 1
            if not queue_backoff_schedule:
                queue_backoff_schedule = [0]
            backoff_index = min(max(0, attempt_count - 1), len(queue_backoff_schedule) - 1)
            backoff_seconds = queue_backoff_schedule[backoff_index]
            next_retry_at = None
            if backoff_seconds > 0:
                try:
                    next_retry_at = (now + timedelta(seconds=backoff_seconds)).isoformat().replace("+00:00", "Z")
                except Exception:
                    next_retry_at = utc_now_iso_fn()
            metadata["local_queue_retry_reason"] = "worker_lost"
            metadata["local_queue_retry_count"] = attempt_count
            metadata["local_queue_max_retries"] = queue_retry_limit
            metadata["local_queue_retry_backoff_seconds"] = backoff_seconds
            metadata["local_queue_next_retry_at"] = next_retry_at
            context["metadata"] = metadata
            run["context"] = context
            run["result"] = (
                "Worker lost connection. Requeued for another local worker after backoff."
                if backoff_seconds > 0
                else "Worker lost connection. Requeued for another local worker."
            )
            run["result_data"] = {
                "summary": run["result"],
                "error": "local_worker_requeued",
                "worker_id": item.get("worker_id"),
                "machine_id": item.get("machine_id"),
                "last_heartbeat_at": item.get("last_heartbeat_at"),
                "retry_count": attempt_count,
                "max_retries": queue_retry_limit,
                "retry_backoff_seconds": backoff_seconds,
                "next_retry_at": next_retry_at,
            }
            log_queue = run.get("logs")
            if log_queue is not None:
                emit_log_fn(
                    log_queue,
                    "warn",
                    run["result"],
                    event="local_worker_requeued",
                    data={
                        "run_id": run_id,
                        "retry_count": attempt_count,
                        "max_retries": queue_retry_limit,
                        "retry_backoff_seconds": backoff_seconds,
                        "next_retry_at": next_retry_at,
                        "worker_id": item.get("worker_id"),
                        "machine_id": item.get("machine_id"),
                    },
                )
            set_run_status_fn(run_id, "queued_local")
            with local_queue_lock:
                if run_id not in local_pending_run_ids:
                    local_pending_run_ids.append(run_id)
            persist_local_runtime_state_fn()
            continue
        if callable(append_dead_letter_fn):
            try:
                append_dead_letter_fn(
                    run_id=run_id,
                    tenant_id=scope["tenant_id"],
                    workspace_id=scope["workspace_id"],
                    specialist_key=scope["specialist_key"],
                    reason="worker_lost_retry_exhausted",
                    trace_id=str(run.get("trace_id") or metadata.get("trace_id") or "").strip(),
                    failure_count=max(previous_retry_count, queue_retry_limit),
                    payload={
                        "run_id": run_id,
                        "worker_id": item.get("worker_id"),
                        "machine_id": item.get("machine_id"),
                        "last_heartbeat_at": item.get("last_heartbeat_at"),
                        "retry_count": previous_retry_count,
                        "max_retries": queue_retry_limit,
                        "failure_kind": "worker_lost",
                    },
                )
            except Exception:
                LOGGER.warning("Failed to append worker-loss dead-letter for %s", run_id, exc_info=True)
        run["result"] = "Worker lost connection."
        run["result_data"] = {
            "summary": "Worker lost connection.",
            "error": "local_worker_lost_connection",
            "worker_id": item.get("worker_id"),
            "machine_id": item.get("machine_id"),
            "last_heartbeat_at": item.get("last_heartbeat_at"),
            "retry_count": previous_retry_count,
            "max_retries": queue_retry_limit,
        }
        log_queue = run.get("logs")
        if log_queue is not None:
            emit_log_fn(
                log_queue,
                "error",
                "Worker lost connection. Run moved to the dead-letter queue after retry budget exhaustion.",
                event="local_worker_lost",
                data={
                    "run_id": run_id,
                    "worker_id": item.get("worker_id"),
                    "machine_id": item.get("machine_id"),
                    "lease_seconds": item.get("lease_seconds"),
                    "last_heartbeat_at": item.get("last_heartbeat_at"),
                    "retry_count": previous_retry_count,
                    "max_retries": queue_retry_limit,
                },
            )
        set_run_status_fn(run_id, "failed")
        if log_queue is not None:
            log_queue.put(None)

    return [str(item.get("run_id") or "") for item in stale]
