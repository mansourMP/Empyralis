"""Ledger audit expansion: channel sends, file writes, shell execution.

Phase J: Every side-effectful act must be ledgered. This module provides
typed record functions that enforce hard redaction — the ledger must
never become a second copy of customer content.

Redaction rules:
  - Channel sends: target channel + recipient JID hash + byte count.
    NEVER raw message bodies.
  - File writes: path + byte count + content SHA256.
    NEVER file contents.
  - Shell commands: first token (program name) + exit status.
    NEVER full commands with arguments/URLs/secrets.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Redaction helpers
# ---------------------------------------------------------------------------

def _redact_channel_args(
    *,
    channel_key: str = "",
    remote_jid: str = "",
    byte_count: int = 0,
    chunk_count: int = 1,
) -> Dict[str, Any]:
    """Redacted summary for channel send — NEVER raw message body."""
    return {
        "channel_type": str(channel_key or "").strip().lower() or "unknown",
        "recipient_hash": (
            hashlib.sha256(str(remote_jid or "").encode()).hexdigest()[:16]
            if remote_jid
            else "none"
        ),
        "byte_count": max(0, int(byte_count or 0)),
        "chunk_count": max(1, int(chunk_count or 1)),
    }


def _redact_file_args(
    *,
    path: str = "",
    byte_count: int = 0,
) -> Dict[str, Any]:
    """Redacted summary for file write — NEVER file contents."""
    clean_path = str(path or "").strip()
    return {
        "path_hash": (
            hashlib.sha256(clean_path.encode()).hexdigest()[:16]
            if clean_path
            else "unknown"
        ),
        "path_ext": (clean_path.rsplit(".", 1)[-1][:8] if "." in clean_path else ""),
        "byte_count": max(0, int(byte_count or 0)),
    }


def _redact_shell_args(
    *,
    command: str = "",
    exit_code: Optional[int] = None,
    byte_count: int = 0,
) -> Dict[str, Any]:
    """Redacted summary for shell execution — NEVER full command."""
    # Extract only the first token (program name), strip arguments which
    # may contain secrets, URLs, or customer data.
    tokens = str(command or "").strip().split()
    program = tokens[0] if tokens else "unknown"
    # Further sanitize: if program looks like a path, take basename
    if "/" in program:
        program = program.rsplit("/", 1)[-1]
    # Strip common prefix chars that might leak paths
    program = program.lstrip("./~")
    return {
        "program": program[:64],
        "token_count": len(tokens),
        "exit_code": exit_code,
        "byte_count": max(0, int(byte_count or 0)),
    }


# ---------------------------------------------------------------------------
# Public ledger record functions
# ---------------------------------------------------------------------------

async def record_channel_send(
    *,
    workspace_id: str,
    actor_id: str = "sage",
    channel_key: str = "",
    remote_jid: str = "",
    text: str = "",
    status: str = "sent",
    trace_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Ledger a channel outbound send with hard redaction.

    Call this from the last-hop send function BEFORE the message leaves
    the system. The raw message body is NEVER stored — only a redacted
    summary (channel type, recipient hash, byte count).
    """
    try:
        from server_modules import activity_ledger_service

        byte_count = len(str(text or "").encode("utf-8"))
        redacted = _redact_channel_args(
            channel_key=channel_key,
            remote_jid=remote_jid,
            byte_count=byte_count,
        )
        return await activity_ledger_service.append_activity_event(
            tenant_id="system",
            workspace_id=workspace_id,
            actor_type="agent",
            actor_id=str(actor_id or "sage").strip() or "sage",
            event_class="sage_activity",
            detail_level="timeline_detail",
            action="channel_send",
            title=f"Channel send: {redacted['channel_type']} ({redacted['byte_count']}B)",
            summary=(
                f"Outbound {redacted['channel_type']} message "
                f"({redacted['byte_count']} bytes, "
                f"recipient={redacted['recipient_hash']}). "
                f"Raw content redacted per audit policy."
            ),
            status=status,
            run_id=str(run_id or "").strip() or None,
            trace_id=str(trace_id or "").strip() or None,
            channel=str(channel_key or "").strip().lower() or None,
            metadata={"redacted_args": redacted},
        )
    except Exception:
        return None


async def record_file_write(
    *,
    workspace_id: str,
    actor_id: str = "sage",
    path: str = "",
    byte_count: int = 0,
    status: str = "written",
    trace_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Ledger a file write with hard redaction.

    NEVER stores file contents — only path hash, extension, and byte count.
    """
    try:
        from server_modules import activity_ledger_service

        redacted = _redact_file_args(path=path, byte_count=byte_count)
        return await activity_ledger_service.append_activity_event(
            tenant_id="system",
            workspace_id=workspace_id,
            actor_type="agent",
            actor_id=str(actor_id or "sage").strip() or "sage",
            event_class="sage_activity",
            detail_level="timeline_detail",
            action="file_write",
            title=f"File write: {redacted['path_ext'] or 'file'} ({redacted['byte_count']}B)",
            summary=(
                f"Wrote {redacted['byte_count']} bytes to "
                f"path_hash={redacted['path_hash']}. "
                f"Raw content redacted per audit policy."
            ),
            status=status,
            run_id=str(run_id or "").strip() or None,
            trace_id=str(trace_id or "").strip() or None,
            metadata={"redacted_args": redacted},
        )
    except Exception:
        return None


async def record_shell_exec(
    *,
    workspace_id: str,
    actor_id: str = "sage",
    command: str = "",
    exit_code: Optional[int] = None,
    combined_bytes: int = 0,
    status: str = "executed",
    trace_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Ledger a shell execution with hard redaction.

    NEVER stores the full command — only program name (first token),
    token count, exit code, and combined stdout+stderr byte count.
    """
    try:
        from server_modules import activity_ledger_service

        redacted = _redact_shell_args(
            command=command,
            exit_code=exit_code,
            byte_count=combined_bytes,
        )
        return await activity_ledger_service.append_activity_event(
            tenant_id="system",
            workspace_id=workspace_id,
            actor_type="agent",
            actor_id=str(actor_id or "sage").strip() or "sage",
            event_class="sage_activity",
            detail_level="timeline_detail",
            action="shell_exec",
            title=f"Shell: {redacted['program']} (exit={redacted['exit_code']})",
            summary=(
                f"Executed '{redacted['program']}' "
                f"({redacted['token_count']} tokens, "
                f"exit={redacted['exit_code']}, "
                f"{redacted['byte_count']}B output). "
                f"Raw command redacted per audit policy."
            ),
            status=status,
            run_id=str(run_id or "").strip() or None,
            trace_id=str(trace_id or "").strip() or None,
            metadata={"redacted_args": redacted},
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Phase K: Gateway audit trail — outbound + hardware choke points
# ---------------------------------------------------------------------------


async def record_gateway_channel_send(
    *,
    workspace_id: str,
    actor_id: str = "sage",
    channel_key: str = "",
    remote_jid: str = "",
    text: str = "",
    status: str = "sent",
    trace_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Ledger a gateway-dispatched channel outbound send with hard redaction.

    Called from dispatch_channel_outbound — the gateway outbound choke point.
    NEVER stores message bodies, phone numbers, or usernames in the clear.
    """
    try:
        from server_modules import activity_ledger_service

        byte_count = len(str(text or "").encode("utf-8"))
        redacted = _redact_channel_args(
            channel_key=channel_key,
            remote_jid=remote_jid,
            byte_count=byte_count,
        )
        return await activity_ledger_service.append_activity_event(
            tenant_id="system",
            workspace_id=workspace_id,
            actor_type="agent",
            actor_id=str(actor_id or "sage").strip() or "sage",
            event_class="gateway_channel",
            detail_level="audit_reference",
            action="channel_send",
            title=f"Gateway send: {redacted['channel_type']} ({redacted['byte_count']}B)",
            summary=(
                f"Gateway outbound {redacted['channel_type']} message "
                f"({redacted['byte_count']} bytes, "
                f"recipient={redacted['recipient_hash']}). "
                f"Raw content redacted per audit policy."
            ),
            status=status,
            trace_id=str(trace_id or "").strip() or None,
            channel=str(channel_key or "").strip().lower() or None,
            metadata={"redacted_args": redacted},
        )
    except Exception:
        return None


async def record_gateway_hardware_invoke(
    *,
    workspace_id: str,
    actor_id: str = "sage",
    capability_id: str = "",
    arguments: Optional[Dict[str, Any]] = None,
    status: str = "executed",
    trace_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Ledger a gateway-dispatched hardware/tool invocation with hard redaction.

    Called from dispatch_tool_invoke — the gateway hardware choke point.
    NEVER stores command arguments, file contents, or secrets.
    Only capability_id and arg key names.
    """
    try:
        from server_modules import activity_ledger_service

        args = dict(arguments or {})
        cap = str(capability_id or "").strip().lower()
        if "shell" in cap:
            action = "shell_execute"
        elif "file" in cap or "filesystem" in cap:
            action = "file_write"
        elif "screenshot" in cap:
            action = "screenshot"
        else:
            action = "other"

        args_summary: Dict[str, Any] = {
            "capability_id": cap,
            "arg_keys": sorted(args.keys()) if args else [],
        }

        return await activity_ledger_service.append_activity_event(
            tenant_id="system",
            workspace_id=workspace_id,
            actor_type="agent",
            actor_id=str(actor_id or "sage").strip() or "sage",
            event_class="gateway_hardware",
            detail_level="audit_reference",
            action=action,
            title=f"Gateway hardware: {action} ({cap})",
            summary=(
                f"Gateway {action} via {cap}. "
                f"Args redacted per audit policy."
            ),
            status=status,
            trace_id=str(trace_id or "").strip() or None,
            run_id=str(run_id or "").strip() or None,
            metadata={
                "execution_tier": "gateway",
                "redacted_args": args_summary,
            },
        )
    except Exception:
        return None
