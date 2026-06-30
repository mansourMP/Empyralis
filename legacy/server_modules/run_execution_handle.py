from __future__ import annotations

from dataclasses import dataclass, field
import queue
from typing import Any, Dict, Iterable, Iterator, MutableMapping, Optional, Tuple

from server_modules import rust_runtime_kernel_client


TRANSIENT_RUNTIME_KEYS = {
    "logs",
    "input_queue",
    "thread_id",
    "active_coroutine",
    "stream_handle",
    "iteration_count",
    "in_process_flags",
    "_started_mono",
    "_finished_mono",
    "_first_value_mono",
    "_hitl_wait_start_mono",
}

EXECUTION_HANDLE_KEYS = {"logs", "input_queue", "thread_id"}

MONOTONIC_RUNTIME_KEYS = {
    "_started_mono",
    "_finished_mono",
    "_first_value_mono",
    "_hitl_wait_start_mono",
}

ACTIVE_EXECUTION_HANDLE_STATUSES = {
    "starting",
    "queued",
    "queued_local",
    "planning",
    "executing",
    "running_local",
    "machine_allocating",
    "retrying",
    "blocked",
    "waiting_for_input",
}


def _enforce_run_record_decision(operation: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "run-record-decision",
            {
                **payload,
                "operation": operation,
            },
        )
        expected_next_actions = {
            "register_live_run": "create_live_run_initial",
            "persist_snapshot": "update_live_run_if_version_matches",
        }
        expected_next_action = expected_next_actions.get(operation)
        if expected_next_action:
            next_action = str(decision.get("next_action") or "").strip()
            if next_action != expected_next_action:
                raise RuntimeError(f"unexpected_next_action:{next_action or 'missing'}")
        return decision
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        reason = str(getattr(exc, "reason", "") or "run_record_denied").strip()
        raise RuntimeError(f"Rust run-record kernel blocked {operation}: {reason}") from exc


def _apply_run_record_patch(payload: Dict[str, Any], decision: Dict[str, Any]) -> Dict[str, Any]:
    record_patch = decision.get("record_patch") if isinstance(decision, dict) else None
    if not isinstance(record_patch, dict):
        return payload
    for key, value in record_patch.items():
        payload[key] = value
    return payload


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


@dataclass(slots=True)
class RunRecord:
    run_id: str
    state: str
    created_at: Optional[str]
    updated_at: Optional[str]
    completed_at: Optional[str]
    actor: Dict[str, Any]
    workspace_id: str
    trace_id: str
    payload: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: MutableMapping[str, Any] | Dict[str, Any]) -> "RunRecord":
        data = dict(payload or {})
        context = data.get("context") if isinstance(data.get("context"), dict) else {}
        metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
        actor = data.get("actor") if isinstance(data.get("actor"), dict) else {}
        if not actor and isinstance(context.get("actor"), dict):
            actor = dict(context.get("actor") or {})
        return cls(
            run_id=str(data.get("run_id") or "").strip(),
            state=str(data.get("status") or data.get("state") or "queued").strip() or "queued",
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            completed_at=data.get("completed_at"),
            actor=dict(actor),
            workspace_id=str(data.get("workspace_id") or context.get("workspace_id") or "default").strip() or "default",
            trace_id=str(data.get("trace_id") or data.get("last_trace_id") or context.get("trace_id") or metadata.get("trace_id") or "").strip(),
            payload=data,
        )

    def to_payload(self) -> Dict[str, Any]:
        data = dict(self.payload or {})
        data["run_id"] = self.run_id
        data["status"] = self.state
        data["state"] = self.state
        data["created_at"] = self.created_at
        data["updated_at"] = self.updated_at
        if self.completed_at is not None:
            data["completed_at"] = self.completed_at
        context = data.get("context") if isinstance(data.get("context"), dict) else {}
        context.setdefault("workspace_id", self.workspace_id)
        if self.trace_id:
            context.setdefault("trace_id", self.trace_id)
        if self.actor:
            context.setdefault("actor", dict(self.actor))
            data.setdefault("actor", dict(self.actor))
        data["context"] = context
        data["workspace_id"] = self.workspace_id
        if self.trace_id:
            data["trace_id"] = self.trace_id
        return data


class RunExecutionHandle(dict):
    """Process-local execution handle with a durable record backing store."""

    def __init__(
        self,
        record: MutableMapping[str, Any] | RunRecord,
        *,
        logs: Any = None,
        input_queue: Any = None,
        thread_id: Optional[int] = None,
        active_coroutine: Any = None,
        stream_handle: Any = None,
        iteration_count: int = 0,
        in_process_flags: Optional[Dict[str, Any]] = None,
        started_mono: Optional[float] = None,
        finished_mono: Optional[float] = None,
        first_value_mono: Optional[float] = None,
        hitl_wait_start_mono: Optional[float] = None,
    ) -> None:
        super().__init__()
        self.record = record if isinstance(record, RunRecord) else RunRecord.from_payload(record)
        self.logs = logs
        self.input_queue = input_queue
        self.thread_id = thread_id
        self.active_coroutine = active_coroutine
        self.stream_handle = stream_handle
        self.iteration_count = int(iteration_count or 0)
        self.in_process_flags = dict(in_process_flags or {})
        self._started_mono = started_mono
        self._finished_mono = finished_mono
        self._first_value_mono = first_value_mono
        self._hitl_wait_start_mono = hitl_wait_start_mono

    def _record_payload(self) -> MutableMapping[str, Any]:
        return self.record.payload

    def _transient_get(self, key: str) -> Any:
        return getattr(self, key)

    def _transient_set(self, key: str, value: Any) -> None:
        setattr(self, key, value)

    def _combined_keys(self) -> list[str]:
        ordered = list(self._record_payload().keys())
        for key in TRANSIENT_RUNTIME_KEYS:
            if self._transient_get(key) is not None and key not in ordered:
                ordered.append(key)
        return ordered

    def __getitem__(self, key: Any) -> Any:
        token = str(key)
        if token in TRANSIENT_RUNTIME_KEYS:
            return self._transient_get(token)
        if token == "status":
            return self.record.state
        if token == "state":
            return self.record.state
        return self._record_payload().__getitem__(key)

    def __setitem__(self, key: Any, value: Any) -> None:
        token = str(key)
        if token in TRANSIENT_RUNTIME_KEYS:
            self._transient_set(token, value)
            return
        if token in {"status", "state"}:
            self.record.state = str(value or "").strip() or "queued"
        elif token == "run_id":
            self.record.run_id = str(value or "").strip()
        elif token == "created_at":
            self.record.created_at = value
        elif token == "updated_at":
            self.record.updated_at = value
        elif token == "completed_at":
            self.record.completed_at = value
        elif token == "workspace_id":
            self.record.workspace_id = str(value or "").strip() or "default"
        elif token == "trace_id":
            self.record.trace_id = str(value or "").strip()
        elif token == "actor" and isinstance(value, dict):
            self.record.actor = dict(value)
        self._record_payload().__setitem__(key, value)

    def __delitem__(self, key: Any) -> None:
        token = str(key)
        if token in TRANSIENT_RUNTIME_KEYS:
            self._transient_set(token, None)
            return
        self._record_payload().__delitem__(key)

    def __iter__(self) -> Iterator[Any]:
        return iter(self._combined_keys())

    def __len__(self) -> int:
        return len(self._combined_keys())

    def __contains__(self, key: object) -> bool:
        token = str(key)
        if token in TRANSIENT_RUNTIME_KEYS:
            return self._transient_get(token) is not None
        return token in self._record_payload()

    def get(self, key: Any, default: Any = None) -> Any:
        token = str(key)
        if token in TRANSIENT_RUNTIME_KEYS:
            value = self._transient_get(token)
            return default if value is None else value
        if token in {"status", "state"}:
            return self.record.state or default
        return self._record_payload().get(key, default)

    def pop(self, key: Any, default: Any = None) -> Any:
        token = str(key)
        if token in TRANSIENT_RUNTIME_KEYS:
            current = self._transient_get(token)
            self._transient_set(token, None)
            return default if current is None else current
        if token in self._record_payload():
            return self._record_payload().pop(key)
        return default

    def setdefault(self, key: Any, default: Any = None) -> Any:
        token = str(key)
        if token in TRANSIENT_RUNTIME_KEYS:
            current = self._transient_get(token)
            if current is None:
                self._transient_set(token, default)
                return default
            return current
        if key not in self._record_payload():
            self[key] = default
        return self._record_payload().get(key)

    def update(self, *args: Any, **kwargs: Any) -> None:
        updates: Dict[Any, Any] = {}
        for arg in args:
            if isinstance(arg, MutableMapping):
                updates.update(dict(arg))
            else:
                updates.update(dict(arg))
        updates.update(kwargs)
        for key, value in updates.items():
            self[key] = value

    def items(self) -> Iterable[tuple[Any, Any]]:
        return ((key, self[key]) for key in self._combined_keys())

    def keys(self) -> Iterable[Any]:
        return list(self._combined_keys())

    def values(self) -> Iterable[Any]:
        return [self[key] for key in self._combined_keys()]

    def copy(self) -> Dict[str, Any]:
        return {key: self[key] for key in self._combined_keys()}

    def __eq__(self, other: object) -> bool:
        if isinstance(other, RunExecutionHandle):
            return self.copy() == other.copy()
        if isinstance(other, MutableMapping):
            return self.copy() == dict(other)
        if isinstance(other, dict):
            return self.copy() == other
        return False

    def __repr__(self) -> str:
        return f"RunExecutionHandle({self.copy()!r})"

    def durable_mapping(self) -> Dict[str, Any]:
        return self.record.to_payload()


def build_run_record(
    *,
    run_id: str,
    engine: str,
    context: Dict[str, Any],
    now_iso: str,
    memory_enabled: bool,
    memory_updated_at: str,
    active_profile_id: Optional[str],
    active_profile_label: Optional[str],
    active_provider: Optional[str],
    active_model: Optional[str],
) -> RunRecord:
    actor = context.get("actor") if isinstance(context.get("actor"), dict) else {}
    tenant_id = str(context.get("tenant_id") or "default").strip() or "default"
    workspace_id = str(context.get("workspace_id") or "default").strip() or "default"
    decision = _enforce_run_record_decision(
        "register_live_run",
        {
            "run_id": run_id,
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "status": "starting",
            "initial_state": "starting",
            "duplicate_run_id": False,
        },
    )
    payload = {
        "run_id": run_id,
        "status": "starting",
        "state": "starting",
        "engine": engine,
        "context": context,
        "created_at": now_iso,
        "updated_at": now_iso,
        "result": None,
        "result_data": None,
        "duration_ms": None,
        "_archived": False,
        "_event_seq": 0,
        "events": [],
        "node_states": None,
        "tool_policy_audit": [],
        "memory_trace": {
            "enabled": bool(memory_enabled),
            "reads": [],
            "writes": [],
            "last_error": None,
            "updated_at": memory_updated_at,
        },
        "active_profile_id": active_profile_id or None,
        "active_profile_label": active_profile_label or None,
        "active_provider": active_provider or None,
        "active_model": active_model or None,
        "active_adapter": None,
        "_hitl_wait_total_ms": 0.0,
    }
    _apply_run_record_patch(payload, decision)
    return RunRecord.from_payload(
        {
            **payload,
            "actor": actor,
            "workspace_id": context.get("workspace_id"),
            "trace_id": context.get("trace_id"),
        }
    )


def attach_execution_handle(
    run: Dict[str, Any] | RunRecord | RunExecutionHandle,
    *,
    log_queue: Any,
    input_queue: Any,
    started_mono: Optional[float] = None,
    finished_mono: Optional[float] = None,
    first_value_mono: Optional[float] = None,
    hitl_wait_start_mono: Optional[float] = None,
    thread_id: Optional[int] = None,
    event_seq: Optional[int] = None,
) -> RunExecutionHandle:
    handle = run if isinstance(run, RunExecutionHandle) else RunExecutionHandle(run)
    handle["logs"] = log_queue
    handle["input_queue"] = input_queue
    handle["thread_id"] = thread_id
    handle["_started_mono"] = started_mono
    handle["_finished_mono"] = finished_mono
    handle["_first_value_mono"] = first_value_mono
    handle["_hitl_wait_start_mono"] = hitl_wait_start_mono
    if event_seq is not None:
        handle["_event_seq"] = int(event_seq)
    return handle


def durable_run_payload(run_id: str, run: Dict[str, Any] | RunExecutionHandle, *, json_safe) -> Dict[str, Any]:
    if isinstance(run, RunExecutionHandle):
        payload = run.durable_mapping()
    else:
        payload = {"run_id": str(run_id or "").strip()}
        for key, value in run.items():
            if key in TRANSIENT_RUNTIME_KEYS:
                continue
            payload[key] = value
    payload["run_id"] = str(run_id or payload.get("run_id") or "").strip()
    payload["status"] = str(payload.get("status") or payload.get("state") or "queued").strip() or "queued"
    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    decision = _enforce_run_record_decision(
        "persist_snapshot",
        {
            "run_id": payload["run_id"],
            "tenant_id": str(context.get("tenant_id") or payload.get("tenant_id") or "default").strip() or "default",
            "workspace_id": str(context.get("workspace_id") or payload.get("workspace_id") or "default").strip() or "default",
            "status": payload["status"],
            "state": str(payload.get("state") or payload["status"]).strip() or payload["status"],
            "version": _safe_int(payload.get("_event_seq") or payload.get("version"), 0),
        },
    )
    _apply_run_record_patch(payload, decision)
    payload["status"] = str(payload.get("status") or payload.get("state") or "queued").strip() or "queued"
    payload["state"] = str(payload.get("state") or payload["status"]).strip() or payload["status"]
    if "thread_id" not in payload:
        payload["thread_id"] = None
    if "_archived" not in payload:
        payload["_archived"] = False
    return json_safe(payload)


def should_restore_execution_handle(item: Dict[str, Any]) -> bool:
    status = str((item or {}).get("status") or (item or {}).get("state") or "").strip().lower()
    return status in ACTIVE_EXECUTION_HANDLE_STATUSES


def restore_run_state(
    item: Dict[str, Any],
    *,
    json_safe,
    memory_enabled: bool,
    now_iso: str,
    hydrate_execution_handle: bool,
) -> Optional[Tuple[str, Dict[str, Any] | RunExecutionHandle]]:
    payload = json_safe(item)
    if not isinstance(payload, dict):
        return None
    run_id = str(payload.pop("run_id", "") or "").strip()
    if not run_id:
        return None
    events = payload.get("events") if isinstance(payload.get("events"), list) else []
    event_seq = int(payload.get("_event_seq") or 0)
    for entry in events:
        if not isinstance(entry, dict):
            continue
        try:
            event_seq = max(event_seq, int(entry.get("seq") or 0))
        except Exception:
            continue
    run: Dict[str, Any] = dict(payload)
    run["run_id"] = run_id
    run["_archived"] = bool(run.get("_archived", False))
    run["_event_seq"] = event_seq
    if not isinstance(run.get("events"), list):
        run["events"] = []
    if not isinstance(run.get("tool_policy_audit"), list):
        run["tool_policy_audit"] = []
    if not isinstance(run.get("memory_trace"), dict):
        run["memory_trace"] = {
            "enabled": memory_enabled,
            "reads": [],
            "writes": [],
            "last_error": None,
            "updated_at": now_iso,
        }
    if "_hitl_wait_total_ms" not in run:
        run["_hitl_wait_total_ms"] = 0.0
    if hydrate_execution_handle:
        return run_id, attach_execution_handle(
            run,
            log_queue=queue.Queue(),
            input_queue=queue.Queue(),
            started_mono=None,
            finished_mono=None,
            first_value_mono=None,
            hitl_wait_start_mono=None,
            thread_id=None,
            event_seq=event_seq,
        )
    run.pop("logs", None)
    run.pop("input_queue", None)
    run["thread_id"] = None
    return run_id, RunExecutionHandle(run, logs=None, input_queue=None, thread_id=None)
