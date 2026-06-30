from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, MutableMapping, MutableSequence, Optional

from server_modules.runtime_config import (
    ORION_HISTORY_LIMIT,
    ORION_IDEMPOTENCY_FILE,
    ORION_MEMORY_ENABLED,
    ORION_PROVIDER_PROFILES_FILE,
    ORION_RUNTIME_STATE_DB,
    ORION_SETUP_SESSIONS_FILE,
)
from server_modules import outbox_service
from server_modules import run_state_repository
from server_modules import rust_runtime_kernel_client
from server_modules.run_execution_handle import (
    RunExecutionHandle,
    durable_run_payload,
    restore_run_state,
    should_restore_execution_handle,
)
from server_modules.runtime_state_store import (
    init_runtime_state_db,
    load_local_runtime_state,
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_safe(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, default=str))
    except Exception:
        return str(value)


def _safe_read_json(path: Path, fallback: Any) -> Any:
    try:
        if not path.exists():
            return fallback
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


class AcpManagerRustGateError(RuntimeError):
    pass


def _enforce_acp_manager_json_write(path: Path, payload: Any, serialized: str) -> None:
    metadata = {
        "path": str(path),
        "payload_type": type(payload).__name__,
        "keys": sorted(str(key) for key in payload.keys())[:80] if isinstance(payload, dict) else [],
        "payload_bytes": len(serialized.encode("utf-8")),
    }
    try:
        decision = rust_runtime_kernel_client.runtime_state_store_decision(
            operation="write_acp_manager_json",
            state_class="acp_manager_state",
            actor_id="system",
            status="active",
            payload=metadata,
            payload_bytes=int(metadata["payload_bytes"]),
            workspace_access=True,
            owner_access=True,
        )
        rust_runtime_kernel_client.enforce_kernel_decision(
            "runtime-state-store-decision",
            decision,
        )
        next_action = str(decision.get("next_action") or "").strip()
        if next_action != "write_acp_manager_json":
            raise AcpManagerRustGateError("unexpected_next_action")
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise AcpManagerRustGateError(exc.reason) from exc


def _safe_write_json(path: Path, payload: Any) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    _enforce_acp_manager_json_write(path, payload, serialized)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    tmp_path.write_text(serialized, encoding="utf-8")
    tmp_path.replace(path)


class _PersistentBase:
    def __init__(self, *, callback: Optional[Callable[[], None]] = None) -> None:
        self._callback = callback
        self._suspend_notifications = False

    def _notify(self) -> None:
        if self._suspend_notifications:
            return
        if callable(self._callback):
            self._callback()

    def _wrap(self, value: Any) -> Any:
        if isinstance(value, (_PersistentDict, _PersistentList)):
            return value
        if isinstance(value, dict):
            return _PersistentDict(value, callback=self._notify)
        if isinstance(value, list):
            return _PersistentList(value, callback=self._notify)
        return value


class _PersistentDict(dict, _PersistentBase):
    def __init__(self, initial: Optional[Dict[str, Any]] = None, *, callback: Optional[Callable[[], None]] = None) -> None:
        dict.__init__(self)
        _PersistentBase.__init__(self, callback=callback)
        self._suspend_notifications = True
        for key, value in (initial or {}).items():
            dict.__setitem__(self, key, self._wrap(value))
        self._suspend_notifications = False

    def __setitem__(self, key: Any, value: Any) -> None:
        dict.__setitem__(self, key, self._wrap(value))
        self._notify()

    def __delitem__(self, key: Any) -> None:
        dict.__delitem__(self, key)
        self._notify()

    def clear(self) -> None:
        dict.clear(self)
        self._notify()

    def pop(self, key: Any, default: Any = None) -> Any:
        if key in self:
            value = dict.pop(self, key)
            self._notify()
            return value
        return default

    def popitem(self) -> Any:
        item = dict.popitem(self)
        self._notify()
        return item

    def setdefault(self, key: Any, default: Any = None) -> Any:
        if key not in self:
            dict.__setitem__(self, key, self._wrap(default))
            self._notify()
        return dict.__getitem__(self, key)

    def update(self, *args: Any, **kwargs: Any) -> None:
        updates = dict(*args, **kwargs)
        for key, value in updates.items():
            dict.__setitem__(self, key, self._wrap(value))
        if updates:
            self._notify()


class _PersistentList(list, _PersistentBase):
    def __init__(self, initial: Optional[Iterable[Any]] = None, *, callback: Optional[Callable[[], None]] = None) -> None:
        list.__init__(self)
        _PersistentBase.__init__(self, callback=callback)
        self._suspend_notifications = True
        for item in (initial or []):
            list.append(self, self._wrap(item))
        self._suspend_notifications = False

    def __setitem__(self, index: Any, value: Any) -> None:
        if isinstance(index, slice):
            list.__setitem__(self, index, [self._wrap(item) for item in value])
        else:
            list.__setitem__(self, index, self._wrap(value))
        self._notify()

    def __delitem__(self, index: Any) -> None:
        list.__delitem__(self, index)
        self._notify()

    def append(self, value: Any) -> None:
        list.append(self, self._wrap(value))
        self._notify()

    def extend(self, values: Iterable[Any]) -> None:
        list.extend(self, [self._wrap(item) for item in values])
        self._notify()

    def insert(self, index: int, value: Any) -> None:
        list.insert(self, index, self._wrap(value))
        self._notify()

    def pop(self, index: int = -1) -> Any:
        value = list.pop(self, index)
        self._notify()
        return value

    def remove(self, value: Any) -> None:
        list.remove(self, value)
        self._notify()

    def clear(self) -> None:
        list.clear(self)
        self._notify()

    def sort(self, *args: Any, **kwargs: Any) -> None:
        list.sort(self, *args, **kwargs)
        self._notify()

    def reverse(self) -> None:
        list.reverse(self)
        self._notify()

    def __iadd__(self, values: Iterable[Any]):
        self.extend(values)
        return self


class _PersistentRunStore(dict):
    def __init__(self, manager: "AcpSessionManager") -> None:
        super().__init__()
        self._manager = manager
        self._loading = False

    def _wrap_run(self, run_id: str, payload: Dict[str, Any] | RunExecutionHandle) -> RunExecutionHandle:
        holder: Dict[str, RunExecutionHandle] = {}

        def _persist() -> None:
            current = holder.get("run")
            if current is None or self._loading:
                return
            self._manager.persist_live_run(run_id, current)

        if isinstance(payload, RunExecutionHandle):
            durable_payload = durable_run_payload(run_id, payload, json_safe=_json_safe)
            record_payload = _PersistentDict(durable_payload, callback=_persist)
            run = RunExecutionHandle(
                record_payload,
                logs=payload.get("logs"),
                input_queue=payload.get("input_queue"),
                thread_id=payload.get("thread_id"),
                active_coroutine=payload.get("active_coroutine"),
                stream_handle=payload.get("stream_handle"),
                iteration_count=int(payload.get("iteration_count") or 0),
                in_process_flags=dict(payload.get("in_process_flags") or {}),
                started_mono=payload.get("_started_mono"),
                finished_mono=payload.get("_finished_mono"),
                first_value_mono=payload.get("_first_value_mono"),
                hitl_wait_start_mono=payload.get("_hitl_wait_start_mono"),
            )
        else:
            durable_payload = durable_run_payload(run_id, payload, json_safe=_json_safe)
            record_payload = _PersistentDict(durable_payload, callback=_persist)
            run = RunExecutionHandle(
                record_payload,
                logs=payload.get("logs") if isinstance(payload, dict) else None,
                input_queue=payload.get("input_queue") if isinstance(payload, dict) else None,
                thread_id=payload.get("thread_id") if isinstance(payload, dict) else None,
                active_coroutine=payload.get("active_coroutine") if isinstance(payload, dict) else None,
                stream_handle=payload.get("stream_handle") if isinstance(payload, dict) else None,
                iteration_count=int((payload.get("iteration_count") if isinstance(payload, dict) else 0) or 0),
                in_process_flags=dict(payload.get("in_process_flags") or {}) if isinstance(payload, dict) else {},
                started_mono=payload.get("_started_mono") if isinstance(payload, dict) else None,
                finished_mono=payload.get("_finished_mono") if isinstance(payload, dict) else None,
                first_value_mono=payload.get("_first_value_mono") if isinstance(payload, dict) else None,
                hitl_wait_start_mono=payload.get("_hitl_wait_start_mono") if isinstance(payload, dict) else None,
            )
        holder["run"] = run
        return run

    def __setitem__(self, key: str, value: Dict[str, Any] | RunExecutionHandle) -> None:
        run_id = str(key or "").strip()
        wrapped = self._wrap_run(run_id, value if isinstance(value, (dict, RunExecutionHandle)) else {})
        dict.__setitem__(self, run_id, wrapped)
        if not self._loading:
            if wrapped.get("_durable_registered_at") and "_durable_version" in wrapped:
                return
            self._manager.persist_live_run(run_id, wrapped)

    def clear(self) -> None:
        run_ids = [str(key) for key in dict.keys(self)]
        dict.clear(self)
        if not self._loading:
            for run_id in run_ids:
                self._manager.delete_live_run(run_id)

    def pop(self, key: str, default: Any = None) -> Any:
        token = str(key or "").strip()
        if token in self:
            value = dict.pop(self, token)
            if not self._loading:
                self._manager.delete_live_run(token)
            return value
        return default

    def reload(self, persisted_items: List[Dict[str, Any]], *, active_only: bool = False) -> None:
        self._loading = True
        try:
            dict.clear(self)
            for item in persisted_items:
                if active_only and not should_restore_execution_handle(item):
                    continue
                restored = self._manager.restore_live_run(
                    item,
                    hydrate_execution_handle=(should_restore_execution_handle(item) if active_only else True),
                )
                if not restored:
                    continue
                run_id, run = restored
                dict.__setitem__(self, run_id, self._wrap_run(run_id, run))
        finally:
            self._loading = False


class _PersistentSnapshotList(_PersistentList):
    def __init__(self, *, callback: Optional[Callable[[], None]] = None) -> None:
        super().__init__([], callback=callback)
        self._loading = False

    def reload(self, items: Iterable[Any]) -> None:
        self._loading = True
        self._suspend_notifications = True
        try:
            list.clear(self)
            for item in items:
                list.append(self, self._wrap(item))
        finally:
            self._suspend_notifications = False
            self._loading = False


class _PersistentStateDict(_PersistentDict):
    def __init__(self, *, callback: Optional[Callable[[], None]] = None) -> None:
        super().__init__({}, callback=callback)
        self._loading = False

    def reload(self, items: Optional[Dict[str, Any]]) -> None:
        self._loading = True
        self._suspend_notifications = True
        try:
            dict.clear(self)
            for key, value in (items or {}).items():
                dict.__setitem__(self, str(key), self._wrap(value))
        finally:
            self._suspend_notifications = False
            self._loading = False


class _PersistentStateList(_PersistentList):
    def __init__(self, *, callback: Optional[Callable[[], None]] = None) -> None:
        super().__init__([], callback=callback)
        self._loading = False

    def reload(self, items: Optional[Iterable[Any]]) -> None:
        self._loading = True
        self._suspend_notifications = True
        try:
            list.clear(self)
            for item in items or []:
                list.append(self, self._wrap(item))
        finally:
            self._suspend_notifications = False
            self._loading = False


class AcpSessionManager:
    def __init__(
        self,
        *,
        runtime_db_path: Path,
        setup_sessions_path: Path,
        provider_profiles_path: Path,
        idempotency_path: Path,
    ) -> None:
        self.runtime_db_path = Path(runtime_db_path).expanduser().resolve()
        self.setup_sessions_path = Path(setup_sessions_path).expanduser().resolve()
        self.provider_profiles_path = Path(provider_profiles_path).expanduser().resolve()
        self.idempotency_path = Path(idempotency_path).expanduser().resolve()
        self._global_lock = threading.Lock()
        self._scope_locks: Dict[str, threading.Lock] = {}

        self.runs = _PersistentRunStore(self)
        self.run_history = _PersistentSnapshotList(callback=self._persist_run_history)
        self.setup_sessions = _PersistentStateDict(callback=self._persist_setup_sessions)
        self.provider_profiles = _PersistentStateDict(callback=self._persist_provider_profiles)
        self.idempotency_records = _PersistentStateDict(callback=self._persist_idempotency)
        self.local_pending_run_ids = _PersistentStateList(callback=self._persist_local_runtime_state)
        self.local_claimed_runs = _PersistentStateDict(callback=self._persist_local_runtime_state)
        self.local_worker_registry = _PersistentStateDict(callback=self._persist_local_runtime_state)
        self.reload_all()

    def _ensure_runtime_db(self) -> None:
        try:
            init_runtime_state_db(self.runtime_db_path)
        except Exception:
            pass

    def reconfigure_paths(
        self,
        *,
        runtime_db_path: Optional[Path] = None,
        setup_sessions_path: Optional[Path] = None,
        provider_profiles_path: Optional[Path] = None,
        idempotency_path: Optional[Path] = None,
    ) -> None:
        if runtime_db_path is not None:
            self.runtime_db_path = Path(runtime_db_path).expanduser().resolve()
        if setup_sessions_path is not None:
            self.setup_sessions_path = Path(setup_sessions_path).expanduser().resolve()
        if provider_profiles_path is not None:
            self.provider_profiles_path = Path(provider_profiles_path).expanduser().resolve()
        if idempotency_path is not None:
            self.idempotency_path = Path(idempotency_path).expanduser().resolve()

    def _lock_for(self, scope_key: str) -> threading.Lock:
        with self._global_lock:
            lock = self._scope_locks.get(scope_key)
            if lock is None:
                lock = threading.Lock()
                self._scope_locks[scope_key] = lock
            return lock

    def restore_live_run(
        self,
        item: Dict[str, Any],
        *,
        hydrate_execution_handle: bool = True,
    ) -> Optional[tuple[str, Dict[str, Any]]]:
        return restore_run_state(
            item,
            json_safe=_json_safe,
            memory_enabled=ORION_MEMORY_ENABLED,
            now_iso=_utc_now_iso(),
            hydrate_execution_handle=hydrate_execution_handle,
        )

    def _serialize_live_run(self, run_id: str, run: MutableMapping[str, Any]) -> Dict[str, Any]:
        return durable_run_payload(run_id, dict(run), json_safe=_json_safe)

    def _set_run_field_quietly(self, run: MutableMapping[str, Any], key: str, value: Any) -> None:
        payload = getattr(getattr(run, "record", None), "payload", None)
        if payload is None or not hasattr(payload, "_suspend_notifications"):
            run[key] = value
            return
        previous = bool(getattr(payload, "_suspend_notifications", False))
        payload._suspend_notifications = True
        try:
            run[key] = value
        finally:
            payload._suspend_notifications = previous

    def persist_live_run(self, run_id: str, run: MutableMapping[str, Any]) -> None:
        token = str(run_id or "").strip()
        if not token:
            return
        with self._lock_for(f"run:{token}"):
            payload = self._serialize_live_run(token, run)
            expected_version_raw = payload.get("_durable_version") if isinstance(payload, dict) else None
            try:
                expected_version = int(expected_version_raw) if expected_version_raw is not None else None
            except Exception:
                expected_version = None
            run_state_repository.sync_upsert_live_run(
                token,
                str(payload.get("workspace_id") or payload.get("context", {}).get("workspace_id") or "default"),
                str(payload.get("tenant_id") or payload.get("context", {}).get("tenant_id") or payload.get("context", {}).get("metadata", {}).get("tenant_id") or "default"),
                str(payload.get("status") or "queued"),
                payload,
                str(payload.get("trace_id") or payload.get("context", {}).get("trace_id") or ""),
            )
            if expected_version is not None:
                self._set_run_field_quietly(run, "_durable_version", expected_version + 1)

    def delete_live_run(self, run_id: str) -> None:
        token = str(run_id or "").strip()
        if not token:
            return
        with self._lock_for(f"run:{token}"):
            run_state_repository.sync_delete_live_run(token)

    def _persist_run_history(self) -> None:
        # Archived run history is durably sourced from Postgres run_archive.
        # Keep this in-memory list as a hot cache only.
        return None

    def _persist_local_runtime_state(self) -> None:
        self._ensure_runtime_db()
        with self._lock_for("local_runtime_state"):
            # SQLite remains only as a local-only offline queue/session cache.
            # It is not the canonical store for live run truth.
            outbox_service.persist_local_runtime_state(
                db_path=self.runtime_db_path,
                pending_run_ids=[str(item) for item in self.local_pending_run_ids],
                claimed_runs={str(key): _json_safe(value) for key, value in self.local_claimed_runs.items()},
                runtime_registrations={str(key): _json_safe(value) for key, value in self.local_worker_registry.items()},
            )

    def _persist_setup_sessions(self) -> None:
        with self._lock_for("setup_sessions"):
            _safe_write_json(
                self.setup_sessions_path,
                {
                    "version": 1,
                    "updated_at": _utc_now_iso(),
                    "items": _json_safe(dict(self.setup_sessions)),
                },
            )

    def _persist_provider_profiles(self) -> None:
        with self._lock_for("provider_profiles"):
            _safe_write_json(
                self.provider_profiles_path,
                {
                    "version": 1,
                    "updated_at": _utc_now_iso(),
                    "items": _json_safe(dict(self.provider_profiles)),
                },
            )

    def _persist_idempotency(self) -> None:
        with self._lock_for("idempotency"):
            _safe_write_json(
                self.idempotency_path,
                {
                    "version": 1,
                    "updated_at": _utc_now_iso(),
                    "items": _json_safe(dict(self.idempotency_records)),
                },
            )

    def reload_runtime_state(self) -> None:
        self._ensure_runtime_db()
        persisted_runs = run_state_repository.sync_list_live_runs()
        try:
            local_state = load_local_runtime_state(self.runtime_db_path)
        except Exception:
            local_state = {}
        history_items = run_state_repository.sync_list_run_archive(ORION_HISTORY_LIMIT)
        self.runs.reload(persisted_runs, active_only=True)
        self.local_pending_run_ids.reload(local_state.get("pending_run_ids") if isinstance(local_state, dict) else [])
        self.local_claimed_runs.reload(local_state.get("claimed_runs") if isinstance(local_state, dict) else {})
        self.local_worker_registry.reload(local_state.get("runtime_registrations") if isinstance(local_state, dict) else {})
        self.run_history.reload(history_items)

    def reload_secondary_state(self) -> None:
        setup_payload = _safe_read_json(self.setup_sessions_path, {"version": 1, "items": {}})
        self.setup_sessions.reload(setup_payload.get("items") if isinstance(setup_payload.get("items"), dict) else {})
        provider_payload = _safe_read_json(self.provider_profiles_path, {"version": 1, "items": {}})
        self.provider_profiles.reload(provider_payload.get("items") if isinstance(provider_payload.get("items"), dict) else {})
        idem_payload = _safe_read_json(self.idempotency_path, {"version": 1, "items": {}})
        self.idempotency_records.reload(idem_payload.get("items") if isinstance(idem_payload.get("items"), dict) else {})

    def reload_all(self) -> None:
        self.reload_runtime_state()
        self.reload_secondary_state()


DEFAULT_ACP_MANAGER = AcpSessionManager(
    runtime_db_path=ORION_RUNTIME_STATE_DB,
    setup_sessions_path=ORION_SETUP_SESSIONS_FILE,
    provider_profiles_path=ORION_PROVIDER_PROFILES_FILE,
    idempotency_path=ORION_IDEMPOTENCY_FILE,
)
