from server_modules import rust_runtime_kernel_client
from server_modules import session_diagnostics_service as diagnostics


def test_python_redaction_remains_default(monkeypatch):
    monkeypatch.delenv("EMPYRALIS_USE_RUST_DIAGNOSTICS_REDACTION", raising=False)

    redacted = diagnostics._redact_secrets(
        {
            "token": "secret",
            "nested": {"api_key": "secret"},
            "normal": "visible",
        }
    )

    assert redacted["token"] == "***REDACTED***"
    assert redacted["nested"]["api_key"] == "***REDACTED***"
    assert redacted["normal"] == "visible"


def test_rust_redaction_opt_in_uses_adapter(monkeypatch):
    monkeypatch.setenv("EMPYRALIS_USE_RUST_DIAGNOSTICS_REDACTION", "1")
    monkeypatch.setattr(
        rust_runtime_kernel_client,
        "redact_diagnostics",
        lambda data: {
            "ok": True,
            "decision": "allow",
            "next_action": "return_redacted_diagnostics",
            "redacted": {"safe": True},
        },
    )

    redacted = diagnostics._redact_secrets({"token": "secret"})

    assert redacted == {"safe": True}


def test_rust_redaction_failure_fails_closed(monkeypatch):
    monkeypatch.setenv("EMPYRALIS_USE_RUST_DIAGNOSTICS_REDACTION", "1")
    monkeypatch.setattr(
        rust_runtime_kernel_client,
        "redact_diagnostics",
        lambda data: {
            "ok": False,
            "decision": "block",
            "reason": "runtime_kernel_unavailable",
        },
    )

    redacted = diagnostics._redact_secrets({"token": "secret", "normal": "visible"})

    assert redacted == {
        "redaction_error": "rust_runtime_kernel_blocked",
        "reason": "runtime_kernel_unavailable",
    }


def test_rust_redaction_wrong_action_fails_closed(monkeypatch):
    monkeypatch.setenv("EMPYRALIS_USE_RUST_DIAGNOSTICS_REDACTION", "1")
    monkeypatch.setattr(
        rust_runtime_kernel_client,
        "redact_diagnostics",
        lambda data: {
            "ok": True,
            "decision": "allow",
            "next_action": "write_session_diagnostics_file",
            "redacted": {"safe": True},
        },
    )

    redacted = diagnostics._redact_secrets({"token": "secret", "normal": "visible"})

    assert redacted == {
        "redaction_error": "rust_runtime_kernel_blocked",
        "reason": "unexpected_next_action:write_session_diagnostics_file",
    }
