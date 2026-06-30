from server_modules import rust_runtime_kernel_client
from server_modules import sage_heartbeat_service as heartbeat


def test_rust_kernel_heartbeat_reports_unavailable(monkeypatch):
    monkeypatch.setattr(rust_runtime_kernel_client, "runtime_kernel_binary", lambda: None)

    snapshot = heartbeat._rust_kernel_health_snapshot()

    assert snapshot["available"] is False
    assert snapshot["binary_path"] is None
    assert "approval-requirement" in snapshot["commands"]
    assert "artifact-policy" in snapshot["commands"]
    assert "authorize-execution" in snapshot["commands"]
    assert "authorize-request" in snapshot["commands"]
    assert "capability-manifest" in snapshot["commands"]
    assert "control-plane-decision" in snapshot["commands"]
    assert "control-plane-service-decision" in snapshot["commands"]
    assert "deployed-agent-decision" in snapshot["commands"]
    assert "deployed-agent-service-decision" in snapshot["commands"]
    assert "deployed-data-decision" in snapshot["commands"]
    assert "deployed-readiness-decision" in snapshot["commands"]
    assert "deployed-virtual-runtime-decision" in snapshot["commands"]
    assert "deployed-virtual-runtime-service-decision" in snapshot["commands"]
    assert "execution-outcome" in snapshot["commands"]
    assert "execution-plan" in snapshot["commands"]
    assert "execution-runtime-decision" in snapshot["commands"]
    assert "gateway-action-decision" in snapshot["commands"]
    assert "gateway-frame-decision" in snapshot["commands"]
    assert "gateway-service-decision" in snapshot["commands"]
    assert "gateway-protocol-decision" in snapshot["commands"]
    assert "gateway-state-decision" in snapshot["commands"]
    assert "heartbeat-snapshot" in snapshot["commands"]
    assert "local-worker-decision" in snapshot["commands"]
    assert "machine-lease-decision" in snapshot["commands"]
    assert "outbox-delivery-decision" in snapshot["commands"]
    assert "policy-preset" in snapshot["commands"]
    assert "platform-orchestration-decision" in snapshot["commands"]
    assert "process-lifecycle-decision" in snapshot["commands"]
    assert "queue-transition-decision" in snapshot["commands"]
    assert "inspect-secret-reference" in snapshot["commands"]
    assert "redact-diagnostics" in snapshot["commands"]
    assert "run-api-decision" in snapshot["commands"]
    assert "run-approval-decision" in snapshot["commands"]
    assert "run-preparation-decision" in snapshot["commands"]
    assert "run-record-decision" in snapshot["commands"]
    assert "run-routing-decision" in snapshot["commands"]
    assert "run-service-decision" in snapshot["commands"]
    assert "run-trigger-decision" in snapshot["commands"]
    assert "run-orchestration-decision" in snapshot["commands"]
    assert "runtime-action-decision" in snapshot["commands"]
    assert "runtime-attachment-decision" in snapshot["commands"]
    assert "runtime-binding-decision" in snapshot["commands"]
    assert "runtime-health-decision" in snapshot["commands"]
    assert "runtime-session-api-decision" in snapshot["commands"]
    assert "runtime-state-store-decision" in snapshot["commands"]
    assert "sandbox-execution-decision" in snapshot["commands"]
    assert "safe-mode-decision" in snapshot["commands"]
    assert "scheduler-decision" in snapshot["commands"]
    assert "session-lifecycle-decision" in snapshot["commands"]
    assert "session-scheduler-decision" in snapshot["commands"]
    assert "state-transition-decision" in snapshot["commands"]
    assert "thread-record-decision" in snapshot["commands"]
    assert "virtual-computer-decision" in snapshot["commands"]
    assert snapshot["adapter"] == "server_modules.rust_runtime_kernel_client"


def test_rust_kernel_heartbeat_reports_available(monkeypatch, tmp_path):
    binary = tmp_path / "empyralis-runtime-kernel"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o700)
    monkeypatch.setattr(rust_runtime_kernel_client, "runtime_kernel_binary", lambda: binary)

    snapshot = heartbeat._rust_kernel_health_snapshot()

    assert snapshot["available"] is True
    assert snapshot["binary_path"] == str(binary)
    assert "validate-policy" in snapshot["commands"]
