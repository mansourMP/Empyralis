from __future__ import annotations
import os, json, time, threading, uuid, hashlib
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from pathlib import Path

def _init():
    return None


def _utc_now() -> datetime:
    from server_modules import runtime_common

    return runtime_common._utc_now()


def _utc_now_iso() -> str:
    from server_modules import runtime_common

    return runtime_common._utc_now_iso()


def _parse_utc_ts(value: Any) -> Optional[datetime]:
    from server_modules import runtime_common

    return runtime_common._parse_utc_ts(value)


def _shared_state():
    from server_modules import shared

    return shared


def _runtime_config():
    from server_modules import runtime_config

    return runtime_config

def _idempotency_record_key(method: str, path: str, idem_key: str) -> str:
    raw = f"{method.upper()}:{path}:{idem_key.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _prune_idempotency_locked():
    now = _utc_now()
    expired: List[str] = []
    for key, record in _shared_state().IDEMPOTENCY_RECORDS.items():
        expires_at = _parse_utc_ts(record.get("expires_at"))
        if expires_at is None or expires_at <= now:
            expired.append(key)
    for key in expired:
        _shared_state().IDEMPOTENCY_RECORDS.pop(key, None)


def _persist_idempotency():
    shared = _shared_state()
    with shared.IDEMPOTENCY_LOCK:
        _prune_idempotency_locked()
    shared.sync_acp_manager_paths(idempotency_path=_runtime_config().ORION_IDEMPOTENCY_FILE)
    shared.ACP_MANAGER._persist_idempotency()


def _load_idempotency():
    shared = _shared_state()
    with shared.IDEMPOTENCY_LOCK:
        shared.sync_acp_manager_paths(idempotency_path=_runtime_config().ORION_IDEMPOTENCY_FILE)
        shared.ACP_MANAGER.reload_secondary_state()
        _prune_idempotency_locked()


def _idempotency_get(method: str, path: str, idem_key: str, body_hash: str) -> Dict[str, Any]:
    composite = _idempotency_record_key(method, path, idem_key)
    shared = _shared_state()
    with shared.IDEMPOTENCY_LOCK:
        _prune_idempotency_locked()
        record = shared.IDEMPOTENCY_RECORDS.get(composite)
    if not isinstance(record, dict):
        return {"hit": False}
    stored_hash = str(record.get("body_hash") or "")
    if stored_hash != body_hash:
        return {"hit": True, "conflict": True}
    return {"hit": True, "record": record}


def _idempotency_store(method: str, path: str, idem_key: str, body_hash: str, status_code: int, response_json: Any):
    composite = _idempotency_record_key(method, path, idem_key)
    expires_at = (_utc_now() + timedelta(seconds=max(60, _runtime_config().ORION_IDEMPOTENCY_TTL_SECONDS))).isoformat().replace("+00:00", "Z")
    record = {
        "method": method.upper(),
        "path": path,
        "idempotency_key": idem_key,
        "body_hash": body_hash,
        "status_code": int(status_code),
        "response": response_json,
        "created_at": _utc_now_iso(),
        "expires_at": expires_at,
    }
    shared = _shared_state()
    with shared.IDEMPOTENCY_LOCK:
        shared.IDEMPOTENCY_RECORDS[composite] = record
    _persist_idempotency()
