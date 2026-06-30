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


def test_external_write_execution_calls_rust_before_execute(monkeypatch):
    from server_modules import external_write_safety

    calls = _install_allow_gate(monkeypatch, external_write_safety.rust_runtime_kernel_client)

    result = external_write_safety._enforce_external_write_execution_decision(
        run_id="run-1",
        step_key="send-email",
        category="email_send",
        target_system="gmail",
        scope={
            "workspace_id": "workspace-1",
            "intent_root_id": "run-1",
            "target_system": "gmail",
        },
        payload_hash="payload-hash",
        idempotency_key="idempotency-key",
    )

    assert result["decision"] == "allow"
    assert calls[0]["operation"] == "execute_external_write_once"
    assert calls[0]["state_class"] == "external_write_executions"
    assert calls[0]["workspace_id"] == "workspace-1"
    assert calls[0]["run_id"] == "run-1"
    assert calls[0]["payload"]["target_system"] == "gmail"


def test_external_write_execution_blocks_with_domain_error(monkeypatch):
    from server_modules import external_write_safety
    from server_modules import rust_runtime_kernel_client

    def block_decision(**request):
        return {
            "ok": False,
            "decision": "block",
            "operation": request.get("operation"),
            "reason": "external_write_denied",
        }

    def enforce(_command, decision):
        raise rust_runtime_kernel_client.RustKernelDecisionError(
            "runtime-state-store-decision",
            decision,
        )

    monkeypatch.setattr(external_write_safety.rust_runtime_kernel_client, "runtime_state_store_decision", block_decision)
    monkeypatch.setattr(external_write_safety.rust_runtime_kernel_client, "enforce_kernel_decision", enforce)

    try:
        external_write_safety._enforce_external_write_execution_decision(
            run_id="run-1",
            step_key="send-email",
            category="email_send",
            target_system="gmail",
            scope={"workspace_id": "workspace-1"},
            payload_hash="payload-hash",
            idempotency_key="idempotency-key",
        )
    except external_write_safety.ExternalWriteRustGateError as exc:
        assert str(exc) == "external_write_denied"
    else:
        raise AssertionError("expected external write gate to fail closed")


def test_external_write_execution_blocks_on_wrong_rust_action(monkeypatch):
    from server_modules import external_write_safety

    def allow_wrong_action(**request):
        return {
            "ok": True,
            "decision": "allow",
            "operation": request.get("operation"),
            "next_action": "write_profile_api_file",
        }

    monkeypatch.setattr(
        external_write_safety.rust_runtime_kernel_client,
        "runtime_state_store_decision",
        allow_wrong_action,
    )
    monkeypatch.setattr(
        external_write_safety.rust_runtime_kernel_client,
        "enforce_kernel_decision",
        lambda _command, decision: decision,
    )

    try:
        external_write_safety._enforce_external_write_execution_decision(
            run_id="run-1",
            step_key="send-email",
            category="email_send",
            target_system="gmail",
            scope={"workspace_id": "workspace-1"},
            payload_hash="payload-hash",
            idempotency_key="idempotency-key",
        )
    except external_write_safety.ExternalWriteRustGateError as exc:
        assert str(exc) == "unexpected_next_action"
    else:
        raise AssertionError("expected external write gate to fail closed")
