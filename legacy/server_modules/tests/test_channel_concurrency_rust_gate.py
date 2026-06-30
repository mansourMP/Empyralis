def _install_allow_gate(monkeypatch, rust_client):
    calls = []

    def allow_decision(**request):
        calls.append(request)
        return {
            "ok": True,
            "decision": "allow",
            "operation": request.get("operation"),
            "next_action": request.get("operation"),
        }

    def enforce(_command, decision):
        return decision

    monkeypatch.setattr(rust_client, "runtime_state_store_decision", allow_decision)
    monkeypatch.setattr(rust_client, "enforce_kernel_decision", enforce)
    return calls


def test_channel_execution_lease_transition_calls_rust_gate(monkeypatch):
    from server_modules import channel_concurrency_service

    calls = _install_allow_gate(monkeypatch, channel_concurrency_service.rust_runtime_kernel_client)
    snapshot = channel_concurrency_service.ChannelQuotaSnapshot(
        max_workspace_active_threads=24,
        max_agent_active_threads=8,
        max_workspace_turns_per_minute=180,
        max_runtime_seconds=45,
    )

    channel_concurrency_service._enforce_channel_execution_lease_transition(
        operation="acquire_channel_execution_lease",
        tenant_id="tenant-1",
        workspace_id="workspace-1",
        lease_id="lease-1",
        responder_install_id="agent-1",
        thread_id="thread-1",
        session_key="session-1",
        channel_key="telegram",
        endpoint_key="bot",
        quota_snapshot=snapshot,
        metadata={"source": "test"},
    )

    assert calls[0]["operation"] == "acquire_channel_execution_lease"
    assert calls[0]["state_class"] == "channel_execution_leases"
    assert calls[0]["tenant_id"] == "tenant-1"
    assert calls[0]["workspace_id"] == "workspace-1"
    assert calls[0]["payload"]["lease_id"] == "lease-1"
    assert calls[0]["payload"]["quota_snapshot"]["max_runtime_seconds"] == 45


def test_channel_execution_lease_transition_blocks_with_domain_error(monkeypatch):
    from server_modules import channel_concurrency_service
    from server_modules import rust_runtime_kernel_client

    def block_decision(**request):
        return {
            "ok": False,
            "decision": "block",
            "operation": request.get("operation"),
            "reason": "channel_lease_denied",
        }

    def enforce(_command, decision):
        raise rust_runtime_kernel_client.RustKernelDecisionError(
            "runtime-state-store-decision",
            decision,
        )

    monkeypatch.setattr(channel_concurrency_service.rust_runtime_kernel_client, "runtime_state_store_decision", block_decision)
    monkeypatch.setattr(channel_concurrency_service.rust_runtime_kernel_client, "enforce_kernel_decision", enforce)

    try:
        channel_concurrency_service._enforce_channel_execution_lease_transition(
            operation="release_channel_execution_lease",
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            lease_id="lease-1",
        )
    except channel_concurrency_service.ChannelConcurrencyRustGateError as exc:
        assert str(exc) == "channel_lease_denied"
    else:
        raise AssertionError("expected channel lease gate to fail closed")


def test_channel_execution_lease_transition_blocks_on_wrong_rust_action(monkeypatch):
    from server_modules import channel_concurrency_service

    def allow_wrong_action(**request):
        return {
            "ok": True,
            "decision": "allow",
            "operation": request.get("operation"),
            "next_action": "write_profile_api_file",
        }

    monkeypatch.setattr(
        channel_concurrency_service.rust_runtime_kernel_client,
        "runtime_state_store_decision",
        allow_wrong_action,
    )
    monkeypatch.setattr(
        channel_concurrency_service.rust_runtime_kernel_client,
        "enforce_kernel_decision",
        lambda _command, decision: decision,
    )

    try:
        channel_concurrency_service._enforce_channel_execution_lease_transition(
            operation="release_channel_execution_lease",
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            lease_id="lease-1",
        )
    except channel_concurrency_service.ChannelConcurrencyRustGateError as exc:
        assert str(exc) == "unexpected_next_action"
    else:
        raise AssertionError("expected channel lease gate to fail closed")
