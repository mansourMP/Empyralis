from __future__ import annotations

import copy
import hashlib
import json
import time
from datetime import timedelta
from typing import Any, Callable, Dict, Optional

from server_modules import rust_runtime_kernel_client

_server = None


def _init():
    global _server
    if _server is not None:
        return
    import server as _s

    _server = _s
    for key, value in vars(_s).items():
        if not key.startswith("__") and key not in globals():
            globals()[key] = value

_EXTERNAL_WRITE_PENDING_WAIT_SECONDS = 60.0
_EXTERNAL_WRITE_PENDING_STALE_SECONDS = 300.0
_EXTERNAL_WRITE_SCOPE_VERSION = 2


class ExternalWriteRustGateError(RuntimeError):
    pass


def _stable_json_text(value: Any) -> str:
    _init()
    try:
        safe = _json_safe(value)
    except Exception:
        safe = value
    try:
        return json.dumps(safe, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    except Exception:
        return json.dumps(str(safe), ensure_ascii=True)


def _external_write_payload_hash(payload: Any) -> str:
    return hashlib.sha256(_stable_json_text(payload).encode("utf-8")).hexdigest()


def stable_value_fingerprint(value: Any) -> str:
    return hashlib.sha256(_stable_json_text(value).encode("utf-8")).hexdigest()


def _payload_size_bytes(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8"))
    except Exception:
        return len(str(value or "").encode("utf-8"))


def _normalized_scope_value(value: Any) -> Any:
    _init()
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    try:
        safe = _json_safe(value)
    except Exception:
        safe = value
    if safe in ({}, [], (), "", None):
        return None
    return safe


def _external_write_scope(
    *,
    run_id: Optional[str],
    context: Optional[Dict[str, Any]],
    step_key: str,
    category: str,
    operation: Dict[str, Any],
    target_system: Optional[str],
    account_identity: Any,
    target_identity: Any,
    payload_hash: str,
) -> Dict[str, Any]:
    metadata = context.get("metadata") if isinstance(context, dict) and isinstance(context.get("metadata"), dict) else {}
    # Write semantics:
    # - same retry/delegation lineage shares an intent_root_id and should reuse the prior write
    # - a fresh explicit run falls back to its own run_id and is treated as a new user intent
    intent_root_id = (
        str(metadata.get("retry_root_run_id") or metadata.get("retry_of_run_id") or "").strip()
        or str(metadata.get("delegation_root_run_id") or "").strip()
        or str(
            metadata.get("parent_run_id")
            or metadata.get("subflow_parent_run_id")
            or metadata.get("delegated_by_run_id")
            or ""
        ).strip()
        or str(run_id or "").strip()
        or None
    )
    normalized_target_identity = _normalized_scope_value(target_identity)
    if normalized_target_identity is None:
        normalized_target_identity = _normalized_scope_value(operation)
    return {
        "scope_version": _EXTERNAL_WRITE_SCOPE_VERSION,
        "workspace_id": str(context.get("workspace_id") or "").strip() if isinstance(context, dict) else None,
        "intent_root_id": intent_root_id,
        "step_key": str(step_key or "").strip(),
        "category": str(category or "").strip(),
        "target_system": str(target_system or "").strip() or None,
        "account_identity": _normalized_scope_value(account_identity),
        "target_identity": normalized_target_identity,
        "payload_hash": payload_hash,
    }


def _annotate_external_write_response(
    response: Any,
    *,
    idempotency_key: str,
    executed: bool,
    created_at: Optional[str],
    completed_at: Optional[str],
) -> Dict[str, Any]:
    _init()
    payload = _json_safe(response)
    if isinstance(payload, dict):
        annotated: Dict[str, Any] = copy.deepcopy(payload)
    else:
        annotated = {"result": payload}
    guard = {
        "executed": bool(executed),
        "duplicate_protected": not bool(executed),
        "idempotency_key": idempotency_key,
        "created_at": created_at,
        "completed_at": completed_at,
    }
    annotated["duplicate_guard"] = guard

    result_data = annotated.get("result_data") if isinstance(annotated.get("result_data"), dict) else None
    if isinstance(result_data, dict):
        result_data["external_write_guard"] = guard
        connector_action = result_data.get("connector_action") if isinstance(result_data.get("connector_action"), dict) else None
        if isinstance(connector_action, dict):
            connector_action["executed"] = bool(executed)
            connector_action["duplicate_protected"] = not bool(executed)
        outputs = result_data.get("outputs") if isinstance(result_data.get("outputs"), dict) else None
        if isinstance(outputs, dict):
            actions = outputs.get("actions")
            if isinstance(actions, list):
                updated_actions = []
                for item in actions:
                    if isinstance(item, dict):
                        next_item = dict(item)
                        next_item["executed"] = bool(executed)
                        next_item["duplicate_protected"] = not bool(executed)
                        updated_actions.append(next_item)
                    else:
                        updated_actions.append(item)
                outputs["actions"] = updated_actions
        annotated["result_data"] = result_data

    outputs = annotated.get("outputs") if isinstance(annotated.get("outputs"), dict) else None
    if isinstance(outputs, dict):
        actions = outputs.get("actions")
        if isinstance(actions, list):
            updated_actions = []
            for item in actions:
                if isinstance(item, dict):
                    next_item = dict(item)
                    next_item["executed"] = bool(executed)
                    next_item["duplicate_protected"] = not bool(executed)
                    updated_actions.append(next_item)
                else:
                    updated_actions.append(item)
            outputs["actions"] = updated_actions

    summary = str(annotated.get("summary") or "").strip()
    if summary and not executed:
        annotated["summary"] = (
            f"{summary.rstrip('.')} Skipped duplicate execution and reused the prior result."
        )
    return annotated


def _enforce_external_write_execution_decision(
    *,
    run_id: Optional[str],
    step_key: str,
    category: str,
    target_system: Optional[str],
    scope: Dict[str, Any],
    payload_hash: str,
    idempotency_key: str,
) -> Dict[str, Any]:
    workspace_id = str((scope or {}).get("workspace_id") or "").strip()
    payload = {
        "run_id": str(run_id or "").strip(),
        "workspace_id": workspace_id,
        "step_key": str(step_key or "").strip(),
        "category": str(category or "").strip(),
        "target_system": str(target_system or "").strip(),
        "payload_hash": str(payload_hash or "").strip(),
        "idempotency_key": str(idempotency_key or "").strip(),
        "scope": dict(scope or {}),
    }
    try:
        decision = rust_runtime_kernel_client.runtime_state_store_decision(
            operation="execute_external_write_once",
            state_class="external_write_executions",
            workspace_id=workspace_id,
            run_id=str(run_id or "").strip(),
            actor_id="system",
            status="active",
            payload=payload,
            payload_bytes=_payload_size_bytes(payload),
            workspace_access=True,
            owner_access=True,
        )
        rust_runtime_kernel_client.enforce_kernel_decision(
            "runtime-state-store-decision",
            decision,
        )
        next_action = str(decision.get("next_action") or "").strip()
        if next_action != "execute_external_write_once":
            raise ExternalWriteRustGateError("unexpected_next_action")
        return decision
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise ExternalWriteRustGateError(exc.reason) from exc


def execute_external_write_once(
    *,
    run_id: Optional[str],
    context: Optional[Dict[str, Any]],
    step_key: str,
    category: str,
    operation: Dict[str, Any],
    target_system: Optional[str] = None,
    account_identity: Any = None,
    target_identity: Any = None,
    payload: Any,
    execute: Callable[[], Any],
) -> Dict[str, Any]:
    _init()
    payload_hash = _external_write_payload_hash(payload)
    scope = _external_write_scope(
        run_id=run_id,
        context=context,
        step_key=step_key,
        category=category,
        operation=operation,
        target_system=target_system,
        account_identity=account_identity,
        target_identity=target_identity,
        payload_hash=payload_hash,
    )
    idempotency_key = hashlib.sha256(_stable_json_text(scope).encode("utf-8")).hexdigest()
    path = f"/runtime/external-write/{str(category or 'generic').strip() or 'generic'}"
    record_key = _idempotency_record_key("POST", path, idempotency_key)

    while True:
        with IDEMPOTENCY_LOCK:
            _prune_idempotency_locked()
            record = IDEMPOTENCY_RECORDS.get(record_key)
            if not isinstance(record, dict):
                created_at = _utc_now_iso()
                expires_at = (
                    _utc_now() + timedelta(seconds=max(60, ORION_IDEMPOTENCY_TTL_SECONDS))
                ).isoformat().replace("+00:00", "Z")
                IDEMPOTENCY_RECORDS[record_key] = {
                    "method": "POST",
                    "path": path,
                    "idempotency_key": idempotency_key,
                    "body_hash": payload_hash,
                    "created_at": created_at,
                    "expires_at": expires_at,
                    "state": "pending",
                    "scope": scope,
                    "response": None,
                }
                should_execute = True
                pending_created_at = created_at
            else:
                stored_hash = str(record.get("body_hash") or "").strip()
                if stored_hash != payload_hash:
                    raise RuntimeError("External write duplicate guard conflict for the same logical operation.")
                state = str(record.get("state") or "").strip().lower() or (
                    "completed" if record.get("response") is not None else "pending"
                )
                pending_created_at = str(record.get("created_at") or "").strip() or _utc_now_iso()
                if state == "uncertain":
                    raise RuntimeError(
                        "Duplicate-protected external write has an uncertain provider outcome; "
                        "manual reconciliation is required before retry."
                    )
                if state == "pending":
                    created_ts = _parse_utc_ts(record.get("created_at"))
                    if created_ts is not None and (_utc_now() - created_ts).total_seconds() > _EXTERNAL_WRITE_PENDING_STALE_SECONDS:
                        record["state"] = "uncertain"
                        record["uncertain_at"] = _utc_now_iso()
                        record["error"] = "stale_pending_provider_outcome_uncertain"
                        IDEMPOTENCY_RECORDS[record_key] = record
                        raise RuntimeError(
                            "Duplicate-protected external write has a stale pending provider outcome; "
                            "manual reconciliation is required before retry."
                        )
                    else:
                        should_execute = False
                        retry_reserve = False
                else:
                    response = record.get("response")
                    return _annotate_external_write_response(
                        response,
                        idempotency_key=idempotency_key,
                        executed=False,
                        created_at=str(record.get("created_at") or "").strip() or None,
                        completed_at=str(record.get("completed_at") or record.get("created_at") or "").strip() or None,
                    )
        _persist_idempotency()
        if should_execute:
            break
        if locals().get("retry_reserve"):
            continue
        deadline = time.monotonic() + _EXTERNAL_WRITE_PENDING_WAIT_SECONDS
        missing_record = False
        while time.monotonic() < deadline:
            time.sleep(0.25)
            with IDEMPOTENCY_LOCK:
                _prune_idempotency_locked()
                record = IDEMPOTENCY_RECORDS.get(record_key)
                if not isinstance(record, dict):
                    missing_record = True
                    break
                stored_hash = str(record.get("body_hash") or "").strip()
                if stored_hash != payload_hash:
                    raise RuntimeError("External write duplicate guard conflict for the same logical operation.")
                state = str(record.get("state") or "").strip().lower() or (
                    "completed" if record.get("response") is not None else "pending"
                )
                if state != "pending":
                    return _annotate_external_write_response(
                        record.get("response"),
                        idempotency_key=idempotency_key,
                        executed=False,
                        created_at=str(record.get("created_at") or "").strip() or None,
                        completed_at=str(record.get("completed_at") or record.get("created_at") or "").strip() or None,
                    )
            continue
        if missing_record:
            continue
        raise RuntimeError("Duplicate-protected external write is still pending from another execution.")

    try:
        _enforce_external_write_execution_decision(
            run_id=run_id,
            step_key=step_key,
            category=category,
            target_system=target_system,
            scope=scope,
            payload_hash=payload_hash,
            idempotency_key=idempotency_key,
        )
        response = execute()
    except Exception:
        with IDEMPOTENCY_LOCK:
            record = IDEMPOTENCY_RECORDS.get(record_key)
            if isinstance(record, dict) and str(record.get("body_hash") or "").strip() == payload_hash:
                record["state"] = "uncertain"
                record["uncertain_at"] = _utc_now_iso()
                record["error"] = "provider_outcome_uncertain"
                IDEMPOTENCY_RECORDS[record_key] = record
        _persist_idempotency()
        raise

    completed_at = _utc_now_iso()
    annotated = _annotate_external_write_response(
        response,
        idempotency_key=idempotency_key,
        executed=True,
        created_at=pending_created_at,
        completed_at=completed_at,
    )
    with IDEMPOTENCY_LOCK:
        IDEMPOTENCY_RECORDS[record_key] = {
            "method": "POST",
            "path": path,
            "idempotency_key": idempotency_key,
            "body_hash": payload_hash,
            "created_at": pending_created_at,
            "completed_at": completed_at,
            "expires_at": (
                _utc_now() + timedelta(seconds=max(60, ORION_IDEMPOTENCY_TTL_SECONDS))
            ).isoformat().replace("+00:00", "Z"),
            "state": "completed",
            "scope": scope,
            "response": annotated,
            "status_code": 200,
        }
    _persist_idempotency()
    return annotated
