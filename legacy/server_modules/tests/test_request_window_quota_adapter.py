from __future__ import annotations

import threading

from server_modules import request_window_quota_adapter


def test_request_window_prunes_dead_buckets_without_random_name_error(monkeypatch):
    monkeypatch.setenv("EMPYRALIS_DEV_IN_MEMORY_QUOTAS", "1")
    request_window_quota_adapter._LAST_DEAD_BUCKET_PRUNE_AT = 0.0
    buckets = {f"dead-{idx}": [0.0] for idx in range(1001)}

    decision = request_window_quota_adapter.evaluate_request_window(
        buckets=buckets,
        lock=threading.Lock(),
        key="active-user",
        limit=2,
        now=200.0,
    )

    assert decision["allowed"] is True
    assert "active-user" in buckets
    assert len(buckets) == 1


def test_request_window_keeps_recent_buckets_during_dead_prune(monkeypatch):
    monkeypatch.setenv("EMPYRALIS_DEV_IN_MEMORY_QUOTAS", "1")
    request_window_quota_adapter._LAST_DEAD_BUCKET_PRUNE_AT = 0.0
    buckets = {f"recent-{idx}": [190.0] for idx in range(1001)}

    decision = request_window_quota_adapter.evaluate_request_window(
        buckets=buckets,
        lock=threading.Lock(),
        key="active-user",
        limit=2,
        now=200.0,
    )

    assert decision["allowed"] is True
    assert "active-user" in buckets
    assert len(buckets) == 1002
