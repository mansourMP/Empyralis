from __future__ import annotations

import re
from typing import Any, Dict, Optional

from server_modules import error_response_service
from server_modules.channel_routing_models import ChannelExecutionResult
from server_modules.error_contracts import IDEMPOTENCY_CONFLICT, POLICY_BLOCK
from server_modules.channel_turn_request_service import coerce_dict
from server_modules import personal_channel_thread_command_service


_CONTROL_COMMAND_RE = re.compile(r"^\s*/(?P<command>[a-z][a-z0-9_-]{0,31})(?:\s|$)", re.IGNORECASE)
_OWNER_ONLY_CONTROL_COMMANDS = frozenset(
    {
        "approve",
        "config",
        "connect",
        "debug",
        "deploy",
        "model",
        "policy",
        "reset",
        "restart",
        "send",
        "setup",
        "system",
        "tool",
        "tools",
    }
)


def classify_channel_control_command(text: str) -> Optional[Dict[str, Any]]:
    match = _CONTROL_COMMAND_RE.match(str(text or ""))
    if not match:
        return None
    command = str(match.group("command") or "").strip().lower()
    if not command:
        return None
    return {
        "command": command,
        "owner_only": command in _OWNER_ONLY_CONTROL_COMMANDS,
        "authorized_roles": ["owner"] if command in _OWNER_ONLY_CONTROL_COMMANDS else ["owner", "admin"],
    }


def check_personal_channel_control_command(
    *,
    text: str,
    sender_role: str | None = None,
) -> Optional[Dict[str, Any]]:
    command = classify_channel_control_command(text)
    if not command:
        return None
    thread_command = personal_channel_thread_command_service.parse_thread_command(text)
    normalized_role = str(sender_role or "").strip().lower()
    authorized_roles = set(command.get("authorized_roles") or [])
    allowed = bool(normalized_role and normalized_role in authorized_roles)
    if allowed:
        return {
            "blocked": False,
            "command": command["command"],
            "sender_role": normalized_role,
            "reason": "",
            "thread_command": thread_command.as_dict() if thread_command else None,
        }
    return {
        "blocked": True,
        "command": command["command"],
        "sender_role": normalized_role or None,
        "reason": "owner_authorization_required",
        "reply": "This control command requires owner approval. The action was not executed.",
        "thread_command": thread_command.as_dict() if thread_command else None,
    }


def duplicate_ignored_reply() -> str:
    return "This inbound event was already processed, so it was ignored."


def duplicate_ignored_result() -> ChannelExecutionResult:
    error = error_response_service.platform_error(
        code="duplicate_inbound_event",
        message=duplicate_ignored_reply(),
        error_class=IDEMPOTENCY_CONFLICT,
        retryable=False,
        status_code=409,
    )
    return ChannelExecutionResult(
        status="duplicate_ignored",
        reply=duplicate_ignored_reply(),
        limit_reason="duplicate_inbound_event",
        payload={
            "status": "duplicate_ignored",
            "error": error_response_service.channel_error_payload(error),
        },
        error=error_response_service.channel_error_payload(error),
    )


def incident_result_payload(incident: Dict[str, Any]) -> Dict[str, Any]:
    mode = str(incident.get("mode") or "active").strip().lower()
    matched_scope = str(incident.get("scope") or "workspace").strip().lower() or "workspace"
    metadata = coerce_dict(
        incident.get("matched_chain", [])[-1].get("metadata")
        if incident.get("matched_chain")
        else incident.get("metadata")
    )
    retry_after_seconds = metadata.get("retry_after_seconds")
    if mode == "pause":
        return {
            "status": "paused",
            "reply": "This workspace is temporarily paused while the owner resolves an incident. Please try again shortly.",
            "incident_scope": matched_scope,
            "incident_mode": mode,
            "retry_after_seconds": retry_after_seconds,
        }
    if mode == "drain":
        return {
            "status": "draining",
            "reply": "This channel is temporarily draining its backlog and is not accepting new messages right now.",
            "incident_scope": matched_scope,
            "incident_mode": mode,
            "retry_after_seconds": retry_after_seconds,
        }
    if mode == "reject":
        return {
            "status": "rejected",
            "reply": "This channel is temporarily rejecting new work while the owner handles an incident.",
            "incident_scope": matched_scope,
            "incident_mode": mode,
            "retry_after_seconds": retry_after_seconds,
        }
    return {
        "status": "ok",
        "reply": "",
        "incident_scope": matched_scope,
        "incident_mode": mode,
        "retry_after_seconds": retry_after_seconds,
    }
