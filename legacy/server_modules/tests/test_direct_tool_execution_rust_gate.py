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


def test_direct_tool_execution_transition_calls_rust_gate(monkeypatch):
    from server_modules import direct_tool_execution_service

    calls = _install_allow_gate(monkeypatch, direct_tool_execution_service.rust_runtime_kernel_client)

    direct_tool_execution_service._enforce_direct_tool_execution_transition(
        operation="start_direct_tool_execution",
        tenant_id="tenant-1",
        workspace_id="workspace-1",
        run_id="run-1",
        thread_id="thread-1",
        tool_call_id="tool-call-1",
        tool_name="shell.exec",
        connector_id="shell",
        action_id="exec",
        status="started",
        governance_metadata={"risk_level": "high"},
        metadata={"source_surface": "sage_direct_tool"},
    )

    assert calls[0]["operation"] == "start_direct_tool_execution"
    assert calls[0]["state_class"] == "direct_tool_execution"
    assert calls[0]["tenant_id"] == "tenant-1"
    assert calls[0]["workspace_id"] == "workspace-1"
    assert calls[0]["run_id"] == "run-1"
    assert calls[0]["payload"]["tool_call_id"] == "tool-call-1"


def test_direct_tool_execution_transition_blocks_with_domain_error(monkeypatch):
    from server_modules import direct_tool_execution_service
    from server_modules import rust_runtime_kernel_client

    def block_decision(**request):
        return {
            "ok": False,
            "decision": "block",
            "operation": request.get("operation"),
            "reason": "direct_tool_execution_denied",
        }

    def enforce(_command, decision):
        raise rust_runtime_kernel_client.RustKernelDecisionError(
            "runtime-state-store-decision",
            decision,
        )

    monkeypatch.setattr(direct_tool_execution_service.rust_runtime_kernel_client, "runtime_state_store_decision", block_decision)
    monkeypatch.setattr(direct_tool_execution_service.rust_runtime_kernel_client, "enforce_kernel_decision", enforce)

    try:
        direct_tool_execution_service._enforce_direct_tool_execution_transition(
            operation="complete_direct_tool_execution",
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            run_id="run-1",
            thread_id="thread-1",
            tool_call_id="tool-call-1",
            tool_name="shell.exec",
            connector_id="shell",
            action_id="exec",
            status="completed",
        )
    except direct_tool_execution_service.DirectToolExecutionRustGateError as exc:
        assert str(exc) == "direct_tool_execution_denied"
    else:
        raise AssertionError("expected direct tool execution gate to fail closed")


def test_direct_tool_execution_transition_blocks_on_wrong_rust_action(monkeypatch):
    from server_modules import direct_tool_execution_service

    def allow_wrong_action(**request):
        return {
            "ok": True,
            "decision": "allow",
            "operation": request.get("operation"),
            "next_action": "write_profile_api_file",
        }

    monkeypatch.setattr(
        direct_tool_execution_service.rust_runtime_kernel_client,
        "runtime_state_store_decision",
        allow_wrong_action,
    )
    monkeypatch.setattr(
        direct_tool_execution_service.rust_runtime_kernel_client,
        "enforce_kernel_decision",
        lambda _command, decision: decision,
    )

    try:
        direct_tool_execution_service._enforce_direct_tool_execution_transition(
            operation="complete_direct_tool_execution",
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            run_id="run-1",
            thread_id="thread-1",
            tool_call_id="tool-call-1",
            tool_name="shell.exec",
            connector_id="shell",
            action_id="exec",
            status="completed",
        )
    except direct_tool_execution_service.DirectToolExecutionRustGateError as exc:
        assert str(exc) == "unexpected_next_action"
    else:
        raise AssertionError("expected direct tool execution gate to fail closed")
