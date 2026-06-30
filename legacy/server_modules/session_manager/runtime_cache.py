from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from server_modules.session_manager.types import CachedRuntimeSnapshot, SessionRuntimeHandle


@dataclass
class _RuntimeCacheEntry:
    state: SessionRuntimeHandle
    last_touched_at: float


class RuntimeHandleCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: Dict[str, _RuntimeCacheEntry] = {}

    def size(self) -> int:
        with self._lock:
            return len(self._cache)

    def has(self, actor_key: str) -> bool:
        token = str(actor_key or "").strip().lower()
        with self._lock:
            return token in self._cache

    def get(self, actor_key: str, *, touch: bool = True, now: Optional[float] = None) -> Optional[SessionRuntimeHandle]:
        token = str(actor_key or "").strip().lower()
        with self._lock:
            entry = self._cache.get(token)
            if entry is None:
                return None
            if touch:
                entry.last_touched_at = float(now if now is not None else time.time())
                entry.state.last_touched_at = entry.last_touched_at
            return entry.state

    def peek(self, actor_key: str) -> Optional[SessionRuntimeHandle]:
        return self.get(actor_key, touch=False)

    def get_last_touched_at(self, actor_key: str) -> Optional[float]:
        token = str(actor_key or "").strip().lower()
        with self._lock:
            entry = self._cache.get(token)
            return float(entry.last_touched_at) if entry is not None else None

    def set(self, actor_key: str, state: SessionRuntimeHandle, *, now: Optional[float] = None) -> None:
        token = str(actor_key or "").strip().lower()
        touched_at = float(now if now is not None else time.time())
        state.last_touched_at = touched_at
        with self._lock:
            self._cache[token] = _RuntimeCacheEntry(state=state, last_touched_at=touched_at)

    def clear(self, actor_key: str) -> None:
        token = str(actor_key or "").strip().lower()
        with self._lock:
            self._cache.pop(token, None)

    def snapshot(self, *, now: Optional[float] = None) -> List[CachedRuntimeSnapshot]:
        current = float(now if now is not None else time.time())
        with self._lock:
            return [
                CachedRuntimeSnapshot(
                    actor_key=actor_key,
                    state=entry.state,
                    last_touched_at=entry.last_touched_at,
                    idle_ms=max(0.0, (current - entry.last_touched_at) * 1000.0),
                )
                for actor_key, entry in self._cache.items()
            ]

    def collect_idle_candidates(self, *, max_idle_ms: float, now: Optional[float] = None) -> List[CachedRuntimeSnapshot]:
        if not max_idle_ms or max_idle_ms <= 0:
            return []
        return [
            item
            for item in self.snapshot(now=now)
            if float(item.idle_ms) >= float(max_idle_ms)
        ]
