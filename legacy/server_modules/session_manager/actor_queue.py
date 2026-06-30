from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterator, TypeVar


T = TypeVar("T")


class SessionActorQueue:
    def __init__(self) -> None:
        self._global_lock = threading.Lock()
        self._lock_by_actor: Dict[str, threading.Lock] = {}
        self._pending_by_actor: Dict[str, int] = {}

    def _normalize_actor_key(self, actor_key: str) -> str:
        token = str(actor_key or "").strip().lower()
        return token or "__default__"

    @contextmanager
    def claim(self, actor_key: str) -> Iterator[None]:
        token = self._normalize_actor_key(actor_key)
        with self._global_lock:
            lock = self._lock_by_actor.get(token)
            if lock is None:
                lock = threading.Lock()
                self._lock_by_actor[token] = lock
            self._pending_by_actor[token] = (self._pending_by_actor.get(token) or 0) + 1
        lock.acquire()
        try:
            yield
        finally:
            lock.release()
            with self._global_lock:
                pending = (self._pending_by_actor.get(token) or 1) - 1
                if pending <= 0:
                    self._pending_by_actor.pop(token, None)
                    self._lock_by_actor.pop(token, None)
                else:
                    self._pending_by_actor[token] = pending

    def run(self, actor_key: str, op: Callable[[], T]) -> T:
        with self.claim(actor_key):
            return op()

    def get_total_pending_count(self) -> int:
        with self._global_lock:
            return int(sum(self._pending_by_actor.values()))

    def get_pending_count_for_session(self, actor_key: str) -> int:
        token = self._normalize_actor_key(actor_key)
        with self._global_lock:
            return int(self._pending_by_actor.get(token) or 0)
