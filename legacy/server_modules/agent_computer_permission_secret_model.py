from __future__ import annotations

from typing import Any, Dict, Mapping

from server_modules import secret_redaction_service


PERMISSION_DENIED_MARKERS = (
    "permission denied",
    "operation not permitted",
    "not authorized",
    "not authorised",
    "access denied",
    "requires accessibility",
    "accessibility permission",
    "screen recording",
    "screen capture permission",
    "tcc",
    "privacy permission",
    "automation permission",
    "clipboard permission",
    "local_permission_denied",
    "blocked/local_permission_denied",
)

OFFLINE_MARKERS = (
    "not currently connected",
    "gateway offline",
    "connection closed",
    "socket closed",
)


def sanitize_local_diagnostic(value: Any) -> Any:
    return secret_redaction_service.sanitize_value(value)


def sanitize_local_failure_message(value: Any) -> str:
    return secret_redaction_service.redact_text(str(value or "").strip())


def local_failure_classification(value: Any) -> Dict[str, str]:
    message = sanitize_local_failure_message(value)
    lowered = message.lower()
    if any(marker in lowered for marker in OFFLINE_MARKERS):
        return {
            "state": "offline",
            "reason": "gateway_offline",
            "summary": message or "Agent Computer is offline.",
        }
    if any(marker in lowered for marker in PERMISSION_DENIED_MARKERS):
        return {
            "state": "blocked",
            "reason": "local_permission_denied",
            "summary": "Agent Computer needs local OS permission before this action can run.",
        }
    return {
        "state": "failed",
        "reason": message or "hardware_action_failed",
        "summary": message or "Hardware action failed.",
    }


def diagnostic_metadata(value: Mapping[str, Any] | None) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    sanitized = sanitize_local_diagnostic(dict(value))
    return sanitized if isinstance(sanitized, dict) else {}
