"""One shared daemon-thread event loop for `sync-from-async` helpers.

Six modules used to keep their own copy of a `_run_coro_sync(coro)` helper
that ultimately called `asyncio.run(coro)` — which creates a brand-new
event loop per call. `server_modules.db.get_pool()` caches its asyncpg pool
by `id(current_loop)`, so a fresh loop meant a fresh pool AND a fresh
Postgres backend connection, none of which were explicitly torn down. A
hot SSE poller (`notification_service.iter_notifications_stream`) called
these helpers ~3× every 350 ms per open stream, exhausting Postgres's
max_connections in ~20 s and rendering the whole backend unresponsive.

All six helpers now delegate to `run_coro_sync()` below, which dispatches
the coroutine onto ONE persistent bridge loop running in ONE daemon thread.
The loop lives forever, so `db.get_pool()` sees the same `id(current_loop)`
on every call and reuses the same asyncpg pool.

Safe from a running event loop (a sync helper called from an `async`
FastAPI endpoint): dispatch happens onto a DIFFERENT loop (this module's
bridge one, in a different thread), so `future.result()` blocks the
caller's thread until the coro finishes on the bridge loop. Blocking the
caller's loop while waiting matches the existing `_run_coro_sync` behavior
exactly (that helper also blocked, either via `asyncio.run()` on the same
thread or `thread.join()`).

Not safe from within the bridge loop itself — that would be a deadlock
(the loop can't run its own scheduled coro while blocked waiting on the
result). No legitimate caller lives there today; we assert against it to
fail loud rather than silently hang.
"""

from __future__ import annotations

import asyncio
import atexit
import threading
from typing import Any, Awaitable, Coroutine


_bridge_lock = threading.Lock()
_bridge_loop: asyncio.AbstractEventLoop | None = None
_bridge_thread: threading.Thread | None = None
_bridge_ready = threading.Event()


def _run_loop_forever(loop: asyncio.AbstractEventLoop) -> None:
    asyncio.set_event_loop(loop)
    _bridge_ready.set()
    try:
        loop.run_forever()
    finally:
        try:
            loop.close()
        except Exception:
            pass


def _ensure_bridge_loop() -> asyncio.AbstractEventLoop:
    global _bridge_loop, _bridge_thread
    with _bridge_lock:
        if _bridge_loop is not None and _bridge_loop.is_running():
            return _bridge_loop
        loop = asyncio.new_event_loop()
        thread = threading.Thread(
            target=_run_loop_forever,
            args=(loop,),
            name="sync-asyncio-bridge",
            daemon=True,
        )
        thread.start()
        _bridge_ready.wait(timeout=5.0)
        _bridge_loop = loop
        _bridge_thread = thread
        return loop


def run_coro_sync(coro: Coroutine[Any, Any, Any] | Awaitable[Any]) -> Any:
    """Run `coro` on the shared bridge loop and block until it finishes.

    Callable from ANY thread — including one whose current thread is
    already running its own event loop (e.g. a sync helper invoked from an
    async FastAPI handler). NOT callable from inside the bridge loop
    itself; see module docstring for why. Returns whatever the coroutine
    returns, or raises whatever it raises."""
    loop = _ensure_bridge_loop()
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None
    if current_loop is loop:  # pragma: no cover - defensive
        raise RuntimeError(
            "run_coro_sync cannot be called from within the bridge loop "
            "itself; that would deadlock."
        )
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result()


def _shutdown() -> None:
    global _bridge_loop, _bridge_thread
    with _bridge_lock:
        loop = _bridge_loop
        thread = _bridge_thread
        _bridge_loop = None
        _bridge_thread = None
    if loop is None:
        return
    try:
        loop.call_soon_threadsafe(loop.stop)
    except Exception:
        pass
    if thread is not None:
        thread.join(timeout=2.0)


atexit.register(_shutdown)
