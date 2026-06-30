from server_modules import rust_authorization_shadow_service as shadow
from server_modules import rust_runtime_kernel_client


def test_rust_authorization_default_off_does_not_call_kernel(monkeypatch):
    monkeypatch.delenv("EMPYRALIS_USE_RUST_AUTHORIZATION", raising=False)
    monkeypatch.delenv("EMPYRALIS_SHADOW_RUST_AUTHORIZATION", raising=False)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("Rust authorization should not be called by default")

    monkeypatch.setattr(rust_runtime_kernel_client, "authorize_request", fail_if_called)

    response = shadow.evaluate_authorization(
        policy={"autonomy_mode": "yolo"},
        capability="read_files",
        existing_decision="allow",
    )

    assert response["enabled"] is False
    assert response["shadow"] is False
    assert response["effective_source"] == "python_existing"
    assert response["effective_decision"] == "allow"
    assert response["rust"] is None


def test_shadow_mode_compares_but_preserves_python_decision(monkeypatch):
    monkeypatch.delenv("EMPYRALIS_USE_RUST_AUTHORIZATION", raising=False)
    monkeypatch.setenv("EMPYRALIS_SHADOW_RUST_AUTHORIZATION", "1")
    monkeypatch.setattr(
        rust_runtime_kernel_client,
        "authorize_request",
        lambda *args, **kwargs: {
            "ok": True,
            "decision": "require_approval",
            "next_action": "request_tool_execution_approval",
        },
    )

    response = shadow.evaluate_authorization(
        policy={"autonomy_mode": "cautious"},
        capability="shell",
        action_class="execute",
        existing_decision="allow",
    )

    assert response["enabled"] is False
    assert response["shadow"] is True
    assert response["effective_source"] == "python_existing"
    assert response["effective_decision"] == "allow"
    assert response["rust"]["decision"] == "require_approval"
    assert response["decision_mismatch"] is True


def test_opt_in_mode_uses_rust_decision(monkeypatch):
    monkeypatch.setenv("EMPYRALIS_USE_RUST_AUTHORIZATION", "1")
    monkeypatch.delenv("EMPYRALIS_SHADOW_RUST_AUTHORIZATION", raising=False)
    monkeypatch.setattr(
        rust_runtime_kernel_client,
        "authorize_request",
        lambda *args, **kwargs: {
            "ok": True,
            "decision": "allow",
            "next_action": "allow_tool_execution",
        },
    )

    response = shadow.evaluate_authorization(
        policy={"autonomy_mode": "yolo"},
        capability="read_files",
        existing_decision="require_approval",
    )

    assert response["enabled"] is True
    assert response["effective_source"] == "rust_runtime_kernel"
    assert response["effective_decision"] == "allow"
    assert response["decision_mismatch"] is True


def test_opt_in_mode_fails_closed_on_wrong_rust_next_action(monkeypatch):
    monkeypatch.setenv("EMPYRALIS_USE_RUST_AUTHORIZATION", "1")
    monkeypatch.delenv("EMPYRALIS_SHADOW_RUST_AUTHORIZATION", raising=False)
    monkeypatch.setattr(
        rust_runtime_kernel_client,
        "authorize_request",
        lambda *args, **kwargs: {
            "ok": True,
            "decision": "allow",
            "next_action": "request_tool_execution_approval",
        },
    )

    response = shadow.evaluate_authorization(
        policy={"autonomy_mode": "yolo"},
        capability="read_files",
        existing_decision="allow",
    )

    assert response["enabled"] is True
    assert response["effective_source"] == "rust_runtime_kernel"
    assert response["effective_decision"] == "block"
    assert response["rust"]["reason"] == "unexpected_next_action:request_tool_execution_approval"
    assert response["rust"]["next_action"] == "deny_tool_execution"


def test_opt_in_mode_fails_closed_on_rust_failure(monkeypatch):
    monkeypatch.setenv("EMPYRALIS_USE_RUST_AUTHORIZATION", "1")
    monkeypatch.setattr(
        rust_runtime_kernel_client,
        "authorize_request",
        lambda *args, **kwargs: {
            "ok": False,
            "decision": "block",
            "reason": "runtime_kernel_unavailable",
        },
    )

    response = shadow.evaluate_authorization(
        policy={"autonomy_mode": "yolo"},
        capability="read_files",
        existing_decision="allow",
    )

    assert response["enabled"] is True
    assert response["effective_source"] == "rust_runtime_kernel"
    assert response["effective_decision"] == "block"
    assert response["rust"]["reason"] == "runtime_kernel_unavailable"
