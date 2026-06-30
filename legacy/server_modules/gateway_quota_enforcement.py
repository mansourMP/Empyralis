from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict

from server_modules import durable_quota_store

_log = logging.getLogger(__name__)

_last_restart_time: float = time.time()
_restart_count: int = 1
_STARTUP_QUOTA_GRACE_SECONDS: float = 60.0

_quota_storage_mode = "memory" if durable_quota_store.use_in_memory_quota_mode() else "sqlite_window"
if _quota_storage_mode == "memory":
    _log.warning(
        "Gateway quota windows initialized in explicit in-memory mode (restart=%s, time=%s). "
        "Quota counters reset on restart. Use EMPYRALIS_QUOTA_DB or unset EMPYRALIS_DEV_IN_MEMORY_QUOTAS for production.",
        _restart_count,
        _last_restart_time,
    )
else:
    _log.info(
        "Gateway quota windows initialized with durable sqlite storage (restart=%s, time=%s).",
        _restart_count,
        _last_restart_time,
    )


GATEWAY_TOOL_EXECUTION = "gateway_tool_execution"
GATEWAY_BROWSER_SESSION = "gateway_browser_session"
GATEWAY_APPROVAL_ACTION = "gateway_approval_action"
GATEWAY_WS_CONNECTION = "gateway_ws_connection"
GATEWAY_CHANNEL_OUTBOUND = "gateway_channel_outbound"


@dataclass
class QuotaWindow:
    max_requests: int
    window_seconds: int
    counters: Dict[str, list[float]] = field(default_factory=dict)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        window_start = now - self.window_seconds
        timestamps = self.counters.get(key, [])
        timestamps = [t for t in timestamps if t > window_start]
        if len(timestamps) >= self.max_requests:
            self.counters[key] = timestamps
            return False
        timestamps.append(now)
        self.counters[key] = timestamps
        return True

    def remaining(self, key: str) -> int:
        now = time.monotonic()
        window_start = now - self.window_seconds
        timestamps = [t for t in self.counters.get(key, []) if t > window_start]
        return max(0, self.max_requests - len(timestamps))


_GATEWAY_QUOTA_PROFILES: Dict[str, QuotaWindow] = {
    GATEWAY_TOOL_EXECUTION: QuotaWindow(max_requests=60, window_seconds=60),
    GATEWAY_BROWSER_SESSION: QuotaWindow(max_requests=5, window_seconds=60),
    GATEWAY_APPROVAL_ACTION: QuotaWindow(max_requests=30, window_seconds=60),
    GATEWAY_WS_CONNECTION: QuotaWindow(max_requests=10, window_seconds=60),
    GATEWAY_CHANNEL_OUTBOUND: QuotaWindow(max_requests=100, window_seconds=60),
}


@dataclass(frozen=True, slots=True)
class GatewayQuotaDecision:
    allowed: bool
    profile: str
    reason: str = ""
    remaining: int = 0
    retry_after_seconds: int = 0


def evaluate_gateway_quota(*, profile: str, gateway_id: str) -> GatewayQuotaDecision:
    quota = _GATEWAY_QUOTA_PROFILES.get(profile)
    if quota is None:
        return GatewayQuotaDecision(allowed=True, profile=profile, reason="no_quota_profile")

    if time.time() - _last_restart_time < _STARTUP_QUOTA_GRACE_SECONDS:
        _log.info(
            "Gateway quota check within startup grace period (%.0fs remaining). profile=%s gateway_id=%s",
            _STARTUP_QUOTA_GRACE_SECONDS - (time.time() - _last_restart_time),
            profile,
            gateway_id,
        )

    key = f"{gateway_id}:{profile}"
    if not durable_quota_store.use_in_memory_quota_mode():
        decision = durable_quota_store.consume_window(
            key=f"gateway:{key}",
            limit=quota.max_requests,
            window_seconds=quota.window_seconds,
        )
        if decision.allowed:
            return GatewayQuotaDecision(
                allowed=True,
                profile=profile,
                remaining=decision.remaining,
            )
        return GatewayQuotaDecision(
            allowed=False,
            profile=profile,
            reason=f"{profile}_rate_limited",
            remaining=0,
            retry_after_seconds=decision.retry_after_seconds,
        )

    if quota.allow(key):
        return GatewayQuotaDecision(
            allowed=True,
            profile=profile,
            remaining=quota.remaining(key),
        )

    return GatewayQuotaDecision(
        allowed=False,
        profile=profile,
        reason=f"{profile}_rate_limited",
        remaining=0,
        retry_after_seconds=quota.window_seconds,
    )


def get_gateway_quota_snapshot(gateway_id: str) -> dict:
    snapshot: dict = {}
    for profile, quota in _GATEWAY_QUOTA_PROFILES.items():
        key = f"{gateway_id}:{profile}"
        if durable_quota_store.use_in_memory_quota_mode():
            remaining = quota.remaining(key)
            storage = "memory"
        else:
            decision = durable_quota_store.snapshot_window(
                key=f"gateway:{key}",
                limit=quota.max_requests,
                window_seconds=quota.window_seconds,
            )
            remaining = decision.remaining
            storage = decision.storage
        snapshot[profile] = {
            "remaining": remaining,
            "max_requests": quota.max_requests,
            "window_seconds": quota.window_seconds,
            "storage": storage,
        }
    return snapshot


def get_quota_restart_info() -> dict:
    """Return restart metadata so operators can detect quota resets."""
    return {
        "last_restart_time": _last_restart_time,
        "restart_count": _restart_count,
        "startup_grace_seconds": _STARTUP_QUOTA_GRACE_SECONDS,
        "in_grace_period": time.time() - _last_restart_time < _STARTUP_QUOTA_GRACE_SECONDS,
        "quota_profiles": list(_GATEWAY_QUOTA_PROFILES.keys()),
        "persistence": "in_memory_only" if durable_quota_store.use_in_memory_quota_mode() else "sqlite_window",
    }
