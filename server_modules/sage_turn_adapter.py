"""
Unified Sage ingress — the SINGLE entry point for ALL Main Agent channels.

Every channel (Telegram Hosted, Telegram Personal, WhatsApp Personal,
WeChat, iMessage, Slack, Signal, Web) normalizes its inbound message into
a NormalizedSageTurn (from channel_adapter) and calls execute_sage_turn().
This guarantees identical safety rules, context loading, response envelope,
persistence, and audit for every surface.

Architecture:
  Channel wrapper → command check → NormalizedSageTurn (data structure)
  → execute_sage_turn() → handle_sage_chat() → SageTurnResult

The handoff from channel to ingress is a DATA STRUCTURE (NormalizedSageTurn),
not a direct Python function call with channel-specific parameters.  This
keeps the Option B door open — a node-resident agent can receive the same
data structure over a signed channel.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from server_modules.channel_adapter import (
    NormalizedSageTurn,
    normalize_sage_inbound,
)
from server_modules.sage_agent_runtime_contract import (
    SAGE_MODE,
    SageTurnContract,
    SageTurnResult,
    normalize_sage_mode,
    normalize_sage_surface,
)


def _coerce_text(value: Any) -> str:
    return str(value or "").strip()


async def execute_sage_turn(
    *,
    # ── Option 1: Individual parameters (convenience for Python callers) ──
    workspace_id: str = "",
    tenant_id: str = "",
    message: str = "",
    surface: str = "chat",
    mode: str = SAGE_MODE,
    current_user: Optional[dict] = None,
    channel_origin: str = "",
    channel_sender_id: str = "",
    channel_sender_name: str = "",
    attachments: Optional[List[dict]] = None,
    thread_id: str = "",
    # ── Option 2: Pre-built task (data-structure handoff for Option B) ──
    task: Optional[NormalizedSageTurn] = None,
) -> SageTurnResult:
    """
    Single entry point for ALL Main Agent (Sage) channels.

    Every channel — Telegram Hosted, Telegram Personal, WhatsApp Personal,
    WeChat, iMessage, Slack, Signal, Web endpoint — MUST route through this
    function.  It guarantees identical:

      - Safety rules (governance gate, blocked tools, restricted memory)
      - Context loading (profile, memory, heartbeat, skills, MCP tools)
      - Response envelope (all SAGE_RESPONSE_KEYS present)
      - Persistence (conversation_memory_facade_service)
      - Audit (activity + security events)

    Two calling conventions are supported:

      1. Individual parameters (convenience for Python channel wrappers).
      2. Pre-built ``task`` (NormalizedSageTurn) — the DATA STRUCTURE
         handoff required by the architecture.  A channel wrapper or a
         node-resident agent (Option B) can serialize a NormalizedSageTurn
         and pass it as a plain dict-like object.

    Args:
        workspace_id: Target workspace (ignored if task is provided).
        tenant_id: Optional tenant; resolved from workspace if empty.
        message: User message text.
        surface: "chat" | "mobile" | "web" | "desktop".
        mode: Always "owner_sage" for the Main Agent.
        current_user: Authenticated user dict (API paths only).
        channel_origin: Canonical channel identifier (e.g. "telegram_hosted").
        channel_sender_id: Sender identifier on the channel.
        channel_sender_name: Human-readable sender name.
        attachments: Media attachments resolved by the channel wrapper.
        thread_id: Optional active thread; resolved from channel_origin if empty.
        task: Pre-built NormalizedSageTurn (data-structure handoff).

    Returns:
        SageTurnResult with message, tool_calls, approvals, trace, etc.
    """
    from server_modules.sage_agent_runtime_service import handle_sage_chat

    # ── Resolve input: task data structure or individual parameters ──
    turn = None
    if task is not None:
        resolved_workspace_id = str(task.workspace_id or "").strip()
        resolved_tenant_id = str(task.tenant_id or "").strip()
        resolved_message = str(task.message or "").strip()
        resolved_surface = normalize_sage_surface(task.surface)
        resolved_mode = normalize_sage_mode(task.mode)
        resolved_current_user = task.current_user
        resolved_channel_origin = str(task.channel_origin.value if hasattr(task.channel_origin, 'value') else task.channel_origin or "").strip()
        resolved_sender_id = str(task.channel_sender_id or "").strip()
        resolved_sender_name = str(task.channel_sender_name or "").strip()
        resolved_attachments = list(task.attachments) if task.attachments else None
        resolved_thread_id = str(thread_id or "").strip()  # thread_id still passed separately
        turn = task
    else:
        resolved_workspace_id = str(workspace_id or "").strip()
        resolved_tenant_id = str(tenant_id or "").strip()
        resolved_message = str(message or "").strip()
        resolved_surface = normalize_sage_surface(surface)
        resolved_mode = normalize_sage_mode(mode)
        resolved_current_user = current_user
        resolved_channel_origin = str(channel_origin or "").strip()
        resolved_sender_id = str(channel_sender_id or "").strip()
        resolved_sender_name = str(channel_sender_name or "").strip()
        resolved_attachments = attachments
        resolved_thread_id = str(thread_id or "").strip()

    # ── Tenant resolution ──
    if not resolved_tenant_id or resolved_tenant_id == "default":
        try:
            from server_modules.control_plane_repository import get_workspace_by_id
            ws_record = await get_workspace_by_id(resolved_workspace_id)
            if isinstance(ws_record, dict):
                resolved_tenant_id = str(ws_record.get("tenant_id") or "").strip() or resolved_tenant_id
        except Exception:
            pass

    # ── Thread resolution ──
    if not resolved_thread_id and resolved_channel_origin:
        from server_modules.sage_command_dispatcher import get_active_thread as _gat
        resolved_thread_id = await _gat(resolved_workspace_id, resolved_channel_origin)
    if not resolved_thread_id:
        resolved_thread_id = "sage-main"

    # ── Build turn if not provided as task ──
    if turn is None:
        turn = normalize_sage_inbound(
            workspace_id=resolved_workspace_id,
            tenant_id=resolved_tenant_id,
            message=resolved_message,
            surface=resolved_surface,
            mode=resolved_mode,
            current_user=resolved_current_user,
            attachments=resolved_attachments,
            channel_origin=resolved_channel_origin or "sage",
            channel_sender_id=resolved_sender_id,
            channel_sender_name=resolved_sender_name,
        )

    from server_modules.sage_agent_runtime_service import _SAGE_AI_SETUP_PATH, set_persisted_model_preference

    # ── /model command: persist to workspace metadata (survives restart, applies to all channels) ──
    _msg = str(resolved_message or "").strip()
    if _msg.startswith("/model"):
        _model_arg = _msg[len("/model"):].strip()
        if not _model_arg:
            result = {"message": "Usage: /model <name>\nExample: /model deepseek-chat", "surface": resolved_surface}
        else:
            _persisted = await set_persisted_model_preference(resolved_workspace_id, _model_arg)
            if _persisted:
                result = {"message": f"Model set to {_model_arg} for this workspace.\n(This setting persists across server restarts and applies to all channels.)", "surface": resolved_surface}
            else:
                result = {"message": f"Model preference noted: {_model_arg}\n(Note: Could not persist — workspace metadata may be read-only.)", "surface": resolved_surface}
        return SageTurnResult(
            message=result.get("message", ""),
            ai_setup_url=f"/w/{resolved_workspace_id}{_SAGE_AI_SETUP_PATH}" if resolved_workspace_id else _SAGE_AI_SETUP_PATH,
        )

    result = await handle_sage_chat(
        workspace_id=turn.workspace_id,
        tenant_id=turn.tenant_id,
        message=turn.message,
        surface=turn.surface,
        mode=turn.mode,
        current_user=turn.current_user,
        attachments=turn.attachments if turn.attachments else None,
        channel_origin=turn.channel_origin,
        sender_name=turn.channel_sender_name or None,
        sender_id=turn.channel_sender_id or None,
        thread_id=resolved_thread_id,
    )

    # Build canonical AI & Setup link — backend is the single source of truth
    _ws_id = str(turn.workspace_id or "").strip()
    _ai_setup_url = f"/w/{_ws_id}{_SAGE_AI_SETUP_PATH}" if _ws_id else _SAGE_AI_SETUP_PATH

    return SageTurnResult(
        message=result.get("message", ""),
        error=result.get("error"),
        used_context=list(result.get("used_context", [])),
        tool_calls=list(result.get("tool_calls", [])),
        available_tools=list(result.get("available_tools", [])),
        blocked_tools=list(result.get("blocked_tools", [])),
        approvals_required=list(result.get("approvals_required", [])),
        memory_updates=list(result.get("memory_updates", [])),
        tool_progress_messages=list(result.get("tool_progress_messages", [])),
        trace_id=result.get("trace_id", ""),
        provider=result.get("provider", ""),
        model=result.get("model"),
        ai_setup_url=_ai_setup_url,
    )


async def execute_sage_turn_for_channel(
    *,
    workspace_id: str,
    tenant_id: str = "",
    message: str,
    surface_channel: str = "",
    gateway_id: str = "",
    remote_jid: str = "",
    push_name: Optional[str] = None,
    source_event_id: Optional[str] = None,
    current_user: Optional[dict] = None,
) -> Dict[str, Any]:
    """
    Channel-originated Sage turn (used by Path B: gateway personal channels).

    Maps gateway channel metadata to the unified execute_sage_turn() call.
    Returns a channel-compatible result dict.
    """
    normalized_channel = _coerce_text(surface_channel)

    sage_result = await execute_sage_turn(
        workspace_id=workspace_id,
        tenant_id=tenant_id,
        message=message,
        surface="chat",
        mode=SAGE_MODE,
        current_user=current_user,
        channel_origin=normalized_channel,
        channel_sender_id=_coerce_text(remote_jid),
        channel_sender_name=_coerce_text(push_name),
    )

    return sage_result.as_dict()
