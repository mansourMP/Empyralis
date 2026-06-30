from __future__ import annotations

import atexit
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Literal, Optional

from server_modules import runtime_config, runtime_state_store

_DEFAULT_STATE_DB_PATH = Path(runtime_config.ORION_RUNTIME_STATE_DB).expanduser().resolve() if runtime_config.ORION_RUNTIME_STATE_DB else None


def _default_restore_work_factory(item: Dict[str, Any]) -> RuntimeLaneWork:
    run_id = str(item.get("run_id") or item.get("metadata", {}).get("run_id") or "").strip()
    label = str(item.get("label") or "").strip()

    def _restored_work() -> Dict[str, Any]:
        return {
            "status": "failed",
            "summary": f"Restored lane work could not be rehydrated: label={label}, run_id={run_id}",
            "run_id": run_id or None,
            "restored": True,
        }

    return _restored_work

RuntimeLane = Literal["main", "cron", "subagent", "system"]
RuntimeLaneWork = Callable[[], Dict[str, Any]]
RuntimeLaneRestoreWorkFactory = Callable[[Dict[str, Any]], RuntimeLaneWork]

LANE_ORDER: tuple[RuntimeLane, ...] = ("main", "cron", "subagent", "system")
DEFAULT_LANE_CONCURRENCY: Dict[RuntimeLane, int] = {
    "main": 1,
    "cron": 1,
    "subagent": 1,
    "system": 1,
}
DEFAULT_MAX_TOTAL_CONCURRENCY = 4
DEFAULT_RECENT_ITEMS_LIMIT = 16
DEFAULT_CHECKPOINT_KEY = "runtime_lane_queue.v1"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat().replace("+00:00", "Z")


def _coerce_lane(value: Any) -> RuntimeLane:
    token = str(value or "").strip().lower()
    if token in DEFAULT_LANE_CONCURRENCY:
        return token  # type: ignore[return-value]
    raise ValueError(f"Unsupported runtime lane: {value!r}")


def _coerce_metadata(value: Any) -> Dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


@dataclass
class RuntimeLaneQueueItem:
    id: str
    lane: RuntimeLane
    label: str
    work: RuntimeLaneWork = field(repr=False)
    metadata: Dict[str, Any]
    status: str = "queued"
    enqueued_at: str = field(default_factory=_utc_now_iso)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    run_id: Optional[str] = None
    summary: Optional[str] = None
    error: Optional[str] = None
    result: Optional[Dict[str, Any]] = None

    def to_snapshot(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "lane": self.lane,
            "label": self.label,
            "status": self.status,
            "enqueued_at": self.enqueued_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "run_id": self.run_id,
            "summary": self.summary,
            "error": self.error,
            "metadata": dict(self.metadata),
        }


class RuntimeLaneQueue:
    def __init__(
        self,
        *,
        lane_concurrency: Optional[Dict[str, int]] = None,
        max_total_concurrency: int = DEFAULT_MAX_TOTAL_CONCURRENCY,
        recent_items_limit: int = DEFAULT_RECENT_ITEMS_LIMIT,
        state_db_path: Optional[Path] = None,
        checkpoint_key: str = DEFAULT_CHECKPOINT_KEY,
        restore_work_factory: Optional[RuntimeLaneRestoreWorkFactory] = None,
    ) -> None:
        resolved_concurrency = dict(DEFAULT_LANE_CONCURRENCY)
        for key, value in dict(lane_concurrency or {}).items():
            lane = _coerce_lane(key)
            resolved_concurrency[lane] = max(1, int(value or 1))
        self._lane_concurrency: Dict[RuntimeLane, int] = resolved_concurrency
        self._max_total_concurrency = max(1, int(max_total_concurrency or DEFAULT_MAX_TOTAL_CONCURRENCY))
        self._recent_items_limit = max(4, int(recent_items_limit or DEFAULT_RECENT_ITEMS_LIMIT))
        self._pending: Dict[RuntimeLane, Deque[RuntimeLaneQueueItem]] = {
            lane: deque() for lane in LANE_ORDER
        }
        self._active: Dict[RuntimeLane, Dict[str, RuntimeLaneQueueItem]] = {
            lane: {} for lane in LANE_ORDER
        }
        self._recent: Deque[RuntimeLaneQueueItem] = deque(maxlen=self._recent_items_limit)
        self._items: Dict[str, RuntimeLaneQueueItem] = {}
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._dispatcher: Optional[threading.Thread] = None
        self._started = False
        self._accepting = True
        self._draining = False
        self._shutdown = False
        self._lane_cursor = 0
        self._state_db_path = Path(state_db_path).expanduser() if state_db_path is not None else None
        self._checkpoint_key = str(checkpoint_key or DEFAULT_CHECKPOINT_KEY).strip() or DEFAULT_CHECKPOINT_KEY
        self._restore_work_factory = restore_work_factory
        self._last_checkpoint_error: Optional[str] = None
        self._restore_checkpoint()

    def start(self) -> None:
        with self._condition:
            if self._started:
                return
            self._dispatcher = threading.Thread(
                target=self._dispatch_forever,
                name="runtime-lane-queue",
                daemon=True,
            )
            self._dispatcher.start()
            self._started = True

    def set_lane_concurrency(self, lane: str, value: int) -> Dict[str, Any]:
        resolved_lane = _coerce_lane(lane)
        with self._condition:
            self._lane_concurrency[resolved_lane] = max(1, int(value or 1))
            self._condition.notify_all()
            return self.snapshot()

    def enqueue(
        self,
        *,
        lane: str,
        label: str,
        work: RuntimeLaneWork,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not callable(work):
            raise TypeError("Lane queue work must be callable.")
        resolved_lane = _coerce_lane(lane)
        resolved_metadata = _coerce_metadata(metadata)
        item = RuntimeLaneQueueItem(
            id=f"lane_{uuid.uuid4().hex[:12]}",
            lane=resolved_lane,
            label=str(label or "").strip() or "Queued work",
            work=work,
            metadata=resolved_metadata,
            run_id=str(resolved_metadata.get("run_id") or "").strip() or None,
        )
        with self._condition:
            if not self._started:
                self.start()
            if not self._accepting:
                raise RuntimeError("Runtime lane queue is shutting down.")
            self._pending[resolved_lane].append(item)
            self._items[item.id] = item
            self._persist_checkpoint_locked()
            self._condition.notify_all()
            return {
                "queued": True,
                "item_id": item.id,
                "lane": resolved_lane,
                "status": item.status,
                "snapshot": self._snapshot_locked(),
            }

    def snapshot(self) -> Dict[str, Any]:
        with self._condition:
            return self._snapshot_locked()

    def shutdown(self, *, wait: bool = True, timeout: float = 5.0) -> Dict[str, Any]:
        deadline = time.monotonic() + max(0.0, float(timeout or 0.0))
        with self._condition:
            self._accepting = False
            self._draining = True
            self._condition.notify_all()
            if wait:
                while (self._has_pending_locked() or self._has_active_locked()) and time.monotonic() < deadline:
                    remaining = max(0.0, deadline - time.monotonic())
                    self._condition.wait(timeout=min(0.2, remaining))
            self._shutdown = True
            self._persist_checkpoint_locked()
            self._condition.notify_all()
            snapshot = self._snapshot_locked()
        dispatcher = self._dispatcher
        if wait and dispatcher is not None and dispatcher.is_alive():
            dispatcher.join(timeout=max(0.0, deadline - time.monotonic()))
        return snapshot

    def _dispatch_forever(self) -> None:
        while True:
            with self._condition:
                while True:
                    if self._shutdown:
                        return
                    item = self._next_dispatchable_item_locked()
                    if item is not None:
                        break
                    if self._draining and not self._has_pending_locked() and not self._has_active_locked():
                        self._shutdown = True
                        self._condition.notify_all()
                        return
                    self._condition.wait(timeout=0.5)
            self._start_item(item)

    def _start_item(self, item: RuntimeLaneQueueItem) -> None:
        worker = threading.Thread(
            target=self._run_item,
            args=(item.id,),
            name=f"runtime-lane-{item.lane}-{item.id}",
            daemon=True,
        )
        worker.start()

    def _run_item(self, item_id: str) -> None:
        with self._condition:
            item = self._items.get(item_id)
        if item is None:
            return
        try:
            result = item.work()
            payload = dict(result or {}) if isinstance(result, dict) else {"value": result}
            status = str(payload.get("status") or "completed").strip() or "completed"
            summary = str(payload.get("summary") or "").strip() or None
            run_id = str(payload.get("run_id") or "").strip() or None
            self._finish_item(
                item_id,
                status="completed" if status == "completed" else status,
                result=payload,
                run_id=run_id,
                summary=summary,
            )
        except Exception as exc:
            self._finish_item(
                item_id,
                status="failed",
                error=str(exc),
                summary=f"Lane work failed: {exc}",
            )

    def _finish_item(
        self,
        item_id: str,
        *,
        status: str,
        result: Optional[Dict[str, Any]] = None,
        run_id: Optional[str] = None,
        summary: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        with self._condition:
            item = self._items.get(item_id)
            if item is None:
                return
            active = self._active.get(item.lane, {})
            active.pop(item_id, None)
            item.status = status
            item.finished_at = _utc_now_iso()
            item.result = dict(result or {}) if isinstance(result, dict) else result
            item.run_id = str(run_id or "").strip() or item.run_id
            item.summary = str(summary or "").strip() or item.summary
            item.error = str(error or "").strip() or item.error
            self._recent.appendleft(item)
            self._persist_checkpoint_locked()
            self._condition.notify_all()

    def _next_dispatchable_item_locked(self) -> Optional[RuntimeLaneQueueItem]:
        if self._total_active_locked() >= self._max_total_concurrency:
            return None
        for offset in range(len(LANE_ORDER)):
            lane = LANE_ORDER[(self._lane_cursor + offset) % len(LANE_ORDER)]
            if len(self._active[lane]) >= int(self._lane_concurrency.get(lane, 1)):
                continue
            queue = self._pending[lane]
            if not queue:
                continue
            item = queue.popleft()
            item.status = "running"
            item.started_at = _utc_now_iso()
            self._active[lane][item.id] = item
            self._persist_checkpoint_locked()
            self._lane_cursor = (self._lane_cursor + offset + 1) % len(LANE_ORDER)
            return item
        return None

    def _restore_checkpoint(self) -> None:
        if self._state_db_path is None or not callable(self._restore_work_factory):
            return
        try:
            runtime_state_store.init_runtime_state_db(self._state_db_path)
            state = runtime_state_store.get_local_app_state(self._state_db_path, self._checkpoint_key)
            value = dict(state.get("value") or {}) if isinstance(state, dict) and isinstance(state.get("value"), dict) else {}
            items = list(value.get("items") or [])
            self._last_checkpoint_error = None
        except Exception as exc:
            self._last_checkpoint_error = str(exc)
            return
        for raw_item in items:
            payload = dict(raw_item or {}) if isinstance(raw_item, dict) else {}
            run_id = str(payload.get("run_id") or "").strip()
            if not run_id:
                continue
            try:
                lane = _coerce_lane(payload.get("lane"))
                work = self._restore_work_factory(payload)
            except Exception:
                continue
            if not callable(work):
                continue
            item = RuntimeLaneQueueItem(
                id=str(payload.get("id") or f"lane_{uuid.uuid4().hex[:12]}").strip(),
                lane=lane,
                label=str(payload.get("label") or "Restored queued work").strip() or "Restored queued work",
                work=work,
                metadata=_coerce_metadata(payload.get("metadata")),
                status="queued",
                enqueued_at=str(payload.get("enqueued_at") or _utc_now_iso()).strip() or _utc_now_iso(),
                run_id=run_id,
                summary=str(payload.get("summary") or "").strip() or None,
            )
            self._pending[lane].append(item)
            self._items[item.id] = item

    def _persist_checkpoint_locked(self) -> None:
        if self._state_db_path is None:
            return
        items: List[Dict[str, Any]] = []
        for lane in LANE_ORDER:
            items.extend(item.to_snapshot() for item in list(self._active[lane].values()) if item.run_id)
            items.extend(item.to_snapshot() for item in list(self._pending[lane]) if item.run_id)
        try:
            runtime_state_store.init_runtime_state_db(self._state_db_path)
            runtime_state_store.put_local_app_state(
                self._state_db_path,
                self._checkpoint_key,
                {"items": items, "persisted_at": _utc_now_iso()},
            )
            self._last_checkpoint_error = None
        except Exception as exc:
            self._last_checkpoint_error = str(exc)
            return

    def _total_active_locked(self) -> int:
        return sum(len(items) for items in self._active.values())

    def _has_pending_locked(self) -> bool:
        return any(self._pending[lane] for lane in LANE_ORDER)

    def _has_active_locked(self) -> bool:
        return any(self._active[lane] for lane in LANE_ORDER)

    def _snapshot_locked(self) -> Dict[str, Any]:
        lane_payload: Dict[str, Any] = {}
        for lane in LANE_ORDER:
            lane_payload[lane] = {
                "concurrency": int(self._lane_concurrency.get(lane, 1)),
                "pending_count": len(self._pending[lane]),
                "active_count": len(self._active[lane]),
                "pending": [item.to_snapshot() for item in list(self._pending[lane])[:4]],
                "active": [item.to_snapshot() for item in list(self._active[lane].values())[:4]],
            }
        return {
            "accepting_new_work": self._accepting,
            "draining": self._draining,
            "running": self._started and not self._shutdown,
            "max_total_concurrency": self._max_total_concurrency,
            "pending_count": sum(len(self._pending[lane]) for lane in LANE_ORDER),
            "active_count": self._total_active_locked(),
            "checkpoint": {
                "enabled": self._state_db_path is not None,
                "key": self._checkpoint_key if self._state_db_path is not None else None,
                "last_error": self._last_checkpoint_error,
            },
            "lanes": lane_payload,
            "recent": [item.to_snapshot() for item in list(self._recent)[:6]],
        }


_GLOBAL_QUEUE_LOCK = threading.Lock()
_GLOBAL_QUEUE: Optional[RuntimeLaneQueue] = None


def get_runtime_lane_queue() -> RuntimeLaneQueue:
    global _GLOBAL_QUEUE
    with _GLOBAL_QUEUE_LOCK:
        if _GLOBAL_QUEUE is None:
            _GLOBAL_QUEUE = RuntimeLaneQueue(
                state_db_path=_DEFAULT_STATE_DB_PATH,
                restore_work_factory=_default_restore_work_factory,
            )
            atexit.register(_GLOBAL_QUEUE.shutdown, wait=True, timeout=3.0)
        return _GLOBAL_QUEUE


def enqueue_runtime_lane_work(
    *,
    lane: str,
    label: str,
    work: RuntimeLaneWork,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    queue = get_runtime_lane_queue()
    return queue.enqueue(lane=lane, label=label, work=work, metadata=metadata)


def runtime_lane_queue_snapshot() -> Dict[str, Any]:
    queue = get_runtime_lane_queue()
    return queue.snapshot()
