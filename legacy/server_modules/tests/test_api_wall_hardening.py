from __future__ import annotations

from types import SimpleNamespace

import pytest


def test_durable_quota_blocks_and_survives_new_callers(tmp_path, monkeypatch):
    from server_modules import durable_quota_store

    monkeypatch.delenv("EMPYRALIS_DEV_IN_MEMORY_QUOTAS", raising=False)
    monkeypatch.setenv("EMPYRALIS_QUOTA_DB", str(tmp_path / "quota.sqlite3"))
    durable_quota_store.reset_quota_store_for_tests()

    first = durable_quota_store.consume_window(key="actor-1:model", limit=2, now=100.0)
    second = durable_quota_store.consume_window(key="actor-1:model", limit=2, now=101.0)
    blocked = durable_quota_store.consume_window(key="actor-1:model", limit=2, now=102.0)
    snapshot = durable_quota_store.snapshot_window(key="actor-1:model", limit=2, now=103.0)

    assert first.allowed is True
    assert second.allowed is True
    assert blocked.allowed is False
    assert blocked.retry_after_seconds > 0
    assert snapshot.observed == 2
    assert snapshot.remaining == 0


def test_client_identity_ignores_forwarded_for_without_trusted_proxy(monkeypatch):
    from server_modules import client_identity_service

    monkeypatch.delenv("EMPYRALIS_TRUSTED_PROXY_CIDRS", raising=False)
    request = SimpleNamespace(
        client=SimpleNamespace(host="203.0.113.10"),
        headers={"x-forwarded-for": "198.51.100.5"},
    )

    assert client_identity_service.resolve_client_ip(request) == "203.0.113.10"


def test_client_identity_accepts_forwarded_for_from_configured_proxy(monkeypatch):
    from server_modules import client_identity_service

    monkeypatch.setenv("EMPYRALIS_TRUSTED_PROXY_CIDRS", "10.0.0.0/8")
    request = SimpleNamespace(
        client=SimpleNamespace(host="10.1.2.3"),
        headers={"x-forwarded-for": "198.51.100.5, 10.1.2.3"},
    )

    assert client_identity_service.resolve_client_ip(request) == "198.51.100.5"


def test_external_write_failure_preserves_uncertain_duplicate_guard(monkeypatch):
    from server_modules import external_write_safety

    external_write_safety._init()
    monkeypatch.setattr(external_write_safety, "IDEMPOTENCY_RECORDS", {})
    monkeypatch.setattr(external_write_safety, "_persist_idempotency", lambda: None)
    monkeypatch.setattr(
        external_write_safety,
        "_enforce_external_write_execution_decision",
        lambda **_kwargs: {"decision": "allow", "next_action": "execute_external_write_once"},
    )

    def fail_provider():
        raise RuntimeError("provider timeout after write")

    with pytest.raises(RuntimeError, match="provider timeout"):
        external_write_safety.execute_external_write_once(
            run_id="run-1",
            context={"workspace_id": "workspace-1"},
            step_key="send",
            category="message_send",
            operation={"kind": "send"},
            target_system="telegram",
            payload={"text": "hello"},
            execute=fail_provider,
        )

    assert any(record.get("state") == "uncertain" for record in external_write_safety.IDEMPOTENCY_RECORDS.values())

    with pytest.raises(RuntimeError, match="uncertain provider outcome"):
        external_write_safety.execute_external_write_once(
            run_id="run-1",
            context={"workspace_id": "workspace-1"},
            step_key="send",
            category="message_send",
            operation={"kind": "send"},
            target_system="telegram",
            payload={"text": "hello"},
            execute=lambda: {"ok": True},
        )
