from __future__ import annotations

from typing import Any, Dict


def build_session_manager_snapshot(
    *,
    enabled: bool,
    runtime_cache_active_sessions: int,
    runtime_cache_idle_ttl_ms: int,
    evicted_total: int,
    active_turns: int,
    queue_depth: int,
    interrupted_stale_sessions: int,
    errors_by_code: Dict[str, int],
) -> Dict[str, Any]:
    return {
        "enabled": bool(enabled),
        "runtime_cache": {
            "active_sessions": int(runtime_cache_active_sessions),
            "idle_ttl_ms": int(runtime_cache_idle_ttl_ms),
            "evicted_total": int(evicted_total),
        },
        "turns": {
            "active": int(active_turns),
            "queue_depth": int(queue_depth),
        },
        "interrupted_stale_sessions": int(interrupted_stale_sessions),
        "errors_by_code": {str(key): int(value) for key, value in sorted(errors_by_code.items())},
    }
