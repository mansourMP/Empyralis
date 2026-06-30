from __future__ import annotations

import threading
import time
from typing import Dict, Optional

from server_modules import durable_quota_store

_MAX_BUCKETS_BEFORE_DEAD_PRUNE = 1000
_DEAD_BUCKET_PRUNE_INTERVAL_SECONDS = 60.0
_DEAD_BUCKET_RETENTION_SECONDS = 120.0
_LAST_DEAD_BUCKET_PRUNE_AT = 0.0


def evaluate_request_window(
    *,
    buckets: Dict[str, list[float]],
    lock: threading.Lock,
    key: str,
    limit: int,
    now: Optional[float] = None,
) -> Dict[str, int | bool]:
    global _LAST_DEAD_BUCKET_PRUNE_AT

    current = float(now if now is not None else time.time())
    normalized_limit = max(int(limit or 0), 1)
    if not durable_quota_store.use_in_memory_quota_mode():
        decision = durable_quota_store.consume_window(
            key=key,
            limit=normalized_limit,
            window_seconds=60,
            now=current,
        )
        return {
            "allowed": decision.allowed,
            "retry_after_seconds": decision.retry_after_seconds,
            "limit": decision.limit,
            "observed": decision.observed,
            "remaining": decision.remaining,
            "storage": decision.storage,
        }

    with lock:
        if (
            len(buckets) > _MAX_BUCKETS_BEFORE_DEAD_PRUNE
            and current - _LAST_DEAD_BUCKET_PRUNE_AT >= _DEAD_BUCKET_PRUNE_INTERVAL_SECONDS
        ):
            _LAST_DEAD_BUCKET_PRUNE_AT = current
            dead_cutoff = current - _DEAD_BUCKET_RETENTION_SECONDS
            dead_keys = [k for k, v in buckets.items() if not v or v[-1] < dead_cutoff]
            for dk in dead_keys:
                buckets.pop(dk, None)

        bucket = list(buckets.get(key, []))
        cutoff = current - 60.0
        bucket = [item for item in bucket if item >= cutoff]
        if len(bucket) >= normalized_limit:
            retry_after = max(1, int(round(bucket[0] + 60.0 - current)))
            buckets[key] = bucket
            return {
                "allowed": False,
                "retry_after_seconds": retry_after,
                "limit": normalized_limit,
                "observed": len(bucket),
            }
        bucket.append(current)
        buckets[key] = bucket
    return {
        "allowed": True,
        "retry_after_seconds": 0,
        "limit": normalized_limit,
        "observed": len(bucket),
    }
