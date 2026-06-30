from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional

from server_modules.plugin_system.hook_points import (
    HookPoint,
    HookContext,
    HookResult,
    ALL_HOOK_POINTS,
)

HookCallback = Callable[[HookContext], HookResult]

DEFAULT_HOOK_TIMEOUT_MS = 500


class HookRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hooks: Dict[HookPoint, List[tuple[str, int, HookCallback]]] = {
            point: [] for point in ALL_HOOK_POINTS
        }
        self._error_count: int = 0
        self._total_executions: int = 0

    def register(
        self,
        hook_point: HookPoint,
        callback: HookCallback,
        *,
        plugin_id: str = "",
        priority: int = 50,
    ) -> None:
        with self._lock:
            entry = (str(plugin_id or "unknown").strip(), max(0, min(100, priority)), callback)
            self._hooks[hook_point].append(entry)
            self._hooks[hook_point].sort(key=lambda e: e[1], reverse=True)

    def unregister(self, plugin_id: str) -> int:
        removed = 0
        with self._lock:
            for point in ALL_HOOK_POINTS:
                before = len(self._hooks[point])
                self._hooks[point] = [
                    (pid, pri, cb) for pid, pri, cb in self._hooks[point]
                    if pid != plugin_id
                ]
                removed += before - len(self._hooks[point])
        return removed

    def execute(
        self,
        hook_point: HookPoint,
        ctx: HookContext,
        *,
        timeout_ms: int = DEFAULT_HOOK_TIMEOUT_MS,
    ) -> HookContext:
        entries = list(self._hooks.get(hook_point, []))
        if not entries:
            return ctx

        for plugin_id, _priority, callback in entries:
            start = time.monotonic()
            try:
                result = callback(ctx)
                if result is None:
                    continue

                if isinstance(result, HookResult):
                    ctx = result.apply(ctx)
                elif isinstance(result, dict):
                    hr = HookResult(
                        abort=bool(result.get("abort", False)),
                        abort_reason=str(result.get("abort_reason", "")),
                        messages=result.get("messages"),
                        system_prompt=result.get("system_prompt"),
                        tools=result.get("tools"),
                        reply=result.get("reply"),
                        tool_arguments=result.get("tool_arguments"),
                        tool_result=result.get("tool_result"),
                    )
                    ctx = hr.apply(ctx)

                elapsed_ms = (time.monotonic() - start) * 1000
                if elapsed_ms > timeout_ms:
                    break
            except Exception:
                with self._lock:
                    self._error_count += 1
                continue

            if ctx.aborted:
                break

        with self._lock:
            self._total_executions += 1
        return ctx

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "registered_hooks": {
                    point.value: len(entries) for point, entries in self._hooks.items()
                },
                "total_hooks": sum(len(e) for e in self._hooks.values()),
                "total_executions": self._total_executions,
                "error_count": self._error_count,
            }


_global_registry: Optional[HookRegistry] = None
_registry_lock = threading.Lock()


def get_global_hook_registry() -> HookRegistry:
    global _global_registry
    with _registry_lock:
        if _global_registry is None:
            _global_registry = HookRegistry()
        return _global_registry


def register_hook(
    hook_point: HookPoint,
    callback: HookCallback,
    *,
    plugin_id: str = "",
    priority: int = 50,
) -> None:
    get_global_hook_registry().register(hook_point, callback, plugin_id=plugin_id, priority=priority)


def execute_hooks(
    hook_point: HookPoint,
    ctx: HookContext,
    *,
    timeout_ms: int = DEFAULT_HOOK_TIMEOUT_MS,
) -> HookContext:
    return get_global_hook_registry().execute(hook_point, ctx, timeout_ms=timeout_ms)
