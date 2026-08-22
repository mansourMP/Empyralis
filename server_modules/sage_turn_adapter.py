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
    ChannelOrigin,
    NormalizedSageTurn,
    normalize_sage_inbound,
)
from server_modules.inbound_envelope import (
    InboundEnvelope,
    envelope_allows_owner_commands,
    prepend_envelope_header,
)
from server_modules.agent_turn_runtime_contract import (
    SAGE_MODE,
    SageTurnContract,
    SageTurnResult,
    normalize_sage_mode,
    normalize_sage_surface,
)


def _channel_origin_for_turn(turn: Any, resolved_channel_origin: str) -> str:
    """The channel key handle_sage_chat should actually see — a plain string,
    and the REAL one.

    Two separate defects lived on the single line this replaces
    (`channel_origin=turn.channel_origin`), and both are silent:

      1. `NormalizedSageTurn.channel_origin` is a ChannelOrigin MEMBER, and
         handle_sage_chat's parameter is a `str` it later does
         `str(...).strip().lower()` on. For a `class X(str, Enum)` on Python
         3.11+ that yields "channelorigin.telegram_hosted", not
         "telegram_hosted" — so every downstream comparison against a real
         channel key failed for EVERY channel, not just the new ones.
      2. ChannelOrigin is a hand-written enum whose members are the
         first-party channel keys. The 2026-08-14 cutover moved every live
         channel onto `openclaw_*`, none of which are members, so
         ChannelOrigin.coerce collapses all 24 of them to UNKNOWN and the
         real key — which normalize_sage_inbound was handed and did not
         keep — is gone by the time anything asks which channel this was.

    Together they are why agent_turn_runtime_service._resolve_channel_sender_class
    could never match a channel binding, so an owner recognised by the DM
    gate arrived at the turn as "audience" and had every tool stripped.

    A known member yields its VALUE; anything the enum does not know keeps
    the raw key the caller actually resolved. Deliberately not "teach the
    enum about OpenClaw": that enum is a hand-maintained channel list, which
    is precisely the shape this codebase has already had to derive away
    twice on this same channel set — and rebuilding it dynamically would
    change a type dozens of call sites depend on. Not losing the string is
    the smaller and more durable fix.
    """
    origin = getattr(turn, "channel_origin", None)
    value = str(getattr(origin, "value", origin) or "").strip()
    if value and value != ChannelOrigin.UNKNOWN.value:
        return value
    return str(resolved_channel_origin or "").strip()


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
    # Canonical inbound attribution (inbound_envelope.py) — who sent this,
    # from where (owner-self-chat / DM / group / channel / console), verified
    # by the channel. Rendered into a one-line header on the message below and
    # consulted by the code-level owner-command gate. None = legacy caller;
    # behavior is then byte-for-byte unchanged.
    envelope: Optional["InboundEnvelope"] = None,
    attachments: Optional[List[dict]] = None,
    thread_id: str = "",
    request_id: str = "",
    # Phase 4: when set, the turn runs as this specialist (persona/model/memory).
    specialist_context: Any = None,
    # Durable per-agent conversation recall the channel wrapper loaded from
    # agent_conversation_memory; forwarded verbatim to handle_sage_chat, which
    # trusts it over the (dead-under-SQLite) control-plane thread store.
    channel_prior_messages: Optional[List[dict]] = None,
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
    from server_modules.agent_turn_runtime_service import handle_sage_chat

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
    # Use the canonical, deterministic resolver (workspaces row → oldest-install
    # fallback). get_workspace_by_id alone returns None for legacy installs-only
    # workspaces (e.g. ws-1) and, in mixed-store runtimes, can surface a
    # polluting tenant binding — either of which mis-attributes the turn (the
    # master-install lookup then misses and usage records with a null agent).
    if not resolved_tenant_id or resolved_tenant_id == "default":
        try:
            from server_modules.control_plane_repository import resolve_tenant_id_for_workspace
            resolved_tenant_id = await resolve_tenant_id_for_workspace(
                resolved_workspace_id, default=resolved_tenant_id or "default"
            )
        except Exception:
            pass

    # ── Thread resolution ──
    # Per-(agent, sender) keying (additive): a resolved specialist turn gets
    # a deterministic thread scoped to that agent + sender, so different
    # agents and different senders on the same channel type never interleave
    # (see agent_sender_thread_id's docstring). LEGACY_UNSCOPED — no
    # specialist resolved, i.e. this turn runs as Sage/master — is
    # completely unchanged: it keeps going through get_active_thread, which
    # preserves "sage-main" and any existing per-channel active-thread
    # override for every existing conversation.
    if not resolved_thread_id and resolved_channel_origin:
        _spec_agent_id = str(getattr(specialist_context, "agent_install_id", "") or "").strip()
        if _spec_agent_id:
            from server_modules.sage_command_dispatcher import agent_sender_thread_id as _astid
            resolved_thread_id = _astid(_spec_agent_id, resolved_sender_id)
        else:
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
            envelope=envelope,
        )

    # Canonical attribution for THIS turn: the task's own envelope wins, the
    # keyword param covers callers that pass individual parameters. None =
    # unwired legacy caller — every gate below then behaves exactly as before.
    _envelope = turn.envelope if turn.envelope is not None else (
        envelope if isinstance(envelope, InboundEnvelope) else None
    )

    from server_modules.agent_turn_runtime_service import _SAGE_AI_SETUP_PATH

    # ── Directive & shortcut processing ─────────────────────────────────
    # Strip /model, /thinking, /help etc. before the LLM sees the message.
    # Directive-only messages return early — no LLM call.
    #
    # STRUCTURAL owner-command gate (inbound_envelope.py): when this turn
    # carries a canonical envelope, "/" commands are honored ONLY on
    # owner-command surfaces (verified owner + self-chat/DM/console). A "/"
    # typed in a group or by a non-owner is treated as plain text — the model
    # sees it with its group header and decides like any other message. This
    # is enforcement in code, not model reasoning: a family group can never be
    # the owner, no matter what the text says. Envelope-less legacy callers
    # (console, unwired channels) keep the pre-envelope behavior unchanged.
    _msg = str(resolved_message or "").strip()
    _cleaned_msg = _msg
    _commands_allowed = _envelope is None or envelope_allows_owner_commands(_envelope)
    if _msg.startswith("/") and _commands_allowed:
        from server_modules.command_registry import process_message as _proc_msg
        from server_modules.command_registry import dispatch as _cmd_dispatch
        from server_modules.command_registry import build_service_kwargs_for_text as _build_service_kwargs

        # Phase 4B / Phase 1 (reasoning effort): the resolved specialist's
        # own install id, so directive handlers that persist per-agent state
        # (e.g. /thinking — see command_registry.py's _handle_thinking)
        # target THIS agent instead of always falling back to the workspace
        # master. Empty when this turn runs as Sage/master, unchanged.
        _directive_agent_install_id = str(getattr(specialist_context, "agent_install_id", "") or "").strip()

        # /stop /model /tools /status /debug all read a `services` and/or
        # `availability_payload`/`tool_capabilities` kwarg that nothing on
        # this live path used to build — see build_service_kwargs_for_text's
        # own docstring. Built once, from the whole message (a superset of
        # whatever _proc_msg strips as a directive/shortcut), and forwarded
        # to BOTH call sites below: the inline-directive/shortcut path
        # (/model, /tools, /status) and the standalone fallback path
        # (/stop, /debug). Empty dict — no extra kwargs, no extra cost — for
        # every other command.
        _service_kwargs = _build_service_kwargs(_msg, resolved_workspace_id)

        _proc = await _proc_msg(
            text=_msg,
            workspace_id=resolved_workspace_id,
            surface="channel" if resolved_channel_origin else "web",
            channel_origin=resolved_channel_origin,
            sender_id=resolved_sender_id,
            agent_install_id=_directive_agent_install_id,
            **_service_kwargs,
        )
        if _proc.is_command_only and not _proc.text.strip():
            # Pure command/directive message — skip LLM entirely
            _reply = "\n".join(_proc.replies) if _proc.replies else "OK."
            return SageTurnResult(
                message=_reply,
                ai_setup_url=f"/w/{resolved_workspace_id}{_SAGE_AI_SETUP_PATH}" if resolved_workspace_id else _SAGE_AI_SETUP_PATH,
            )

        # Fallback: standalone commands like /new or /compact that aren't
        # directives still start with / after directive stripping.
        #
        # sender_id is REQUIRED here, not optional plumbing: this is the
        # ONE place every owner-gated command (/config /mcp /plugins /debug
        # /bash — none of them directives or inline shortcuts, so none of
        # them go through _proc_msg's own sender_id-carrying dispatch calls
        # above) resolves on the live path, for BOTH web and every channel
        # (this function is the single unified entry both route through).
        # Omitting it means command_registry._is_sender_owner can never
        # return True here, so dispatch() always refuses and returns None —
        # and the caller below then falls through to `_cleaned_msg =
        # _remaining`, handing the raw command text (e.g. a real /bash
        # argument) to the model as ordinary chat. Same bug shape, same
        # fix, as command_registry.py's own sender_id fix for the web
        # surface (see direct_chat_runtime_service.build_direct_operator_
        # reply's history) — that fix landed on a call site this unified
        # pipeline had already made unreachable; this is the call site
        # that actually runs today.
        _remaining = _proc.text.strip()
        if _remaining.startswith("/"):
            _cmd_result = await _cmd_dispatch(
                text=_remaining,
                workspace_id=resolved_workspace_id,
                surface="channel" if resolved_channel_origin else "web",
                sender_id=resolved_sender_id,
                agent_install_id=_directive_agent_install_id,
                **_service_kwargs,
            )
            if _cmd_result is not None:
                return SageTurnResult(
                    message=str(_cmd_result.get("reply") or ""),
                    ai_setup_url=f"/w/{resolved_workspace_id}{_SAGE_AI_SETUP_PATH}" if resolved_workspace_id else _SAGE_AI_SETUP_PATH,
                )

        _cleaned_msg = _remaining if _remaining else _msg

    # ── Phase P triage gate REMOVED (founder ruling, 2026-07-23) ─────────
    # "Every single message goes to the reasoning model, absolutely. We are
    # not going to have filters that flag a message and don't deliver it.
    # No hardcoded outputs — everything is the agent's own reasoning."
    # This used to run an LLM scope-classifier here and, on a "no" verdict,
    # substitute a canned decline/silence/escalation instead of reaching the
    # model (server_modules/triage_service.py's execute_triage_gate, plus
    # the Layer-2 identity check that never exempted owners). Every inbound
    # message now always reaches handle_sage_chat below; an agent choosing
    # to stay silent via its own [SILENT] output is untouched — that is
    # output-side reasoning, not an input-side block.

    # ── Envelope header injection (the ONE place it happens) ────────────
    # The model must SEE the attribution, not infer it: prepend the canonical
    # one-line header ("[Telegram · group “Family” · from X — NOT your owner
    # · …]") to the message content. Deterministic shape, token-lean,
    # idempotent (a retried turn can't double it). Envelope-less turns pass
    # through untouched.
    _cleaned_msg = prepend_envelope_header(_cleaned_msg, _envelope)

    result = await handle_sage_chat(
        workspace_id=turn.workspace_id,
        tenant_id=turn.tenant_id,
        message=_cleaned_msg,
        surface=turn.surface,
        mode=turn.mode,
        current_user=turn.current_user,
        attachments=turn.attachments if turn.attachments else None,
        channel_origin=_channel_origin_for_turn(turn, resolved_channel_origin),
        sender_name=turn.channel_sender_name or None,
        sender_id=turn.channel_sender_id or None,
        thread_id=resolved_thread_id,
        request_id=request_id,
        specialist_context=specialist_context,
        channel_prior_messages=channel_prior_messages,
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
        media=list(result.get("media") or []),
        context_usage=result.get("context_usage") if isinstance(result.get("context_usage"), dict) else None,
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
    agent_id: str = "",
    attachments: Optional[List[dict]] = None,
    channel_prior_messages: Optional[List[dict]] = None,
) -> Dict[str, Any]:
    """
    Channel-originated Sage turn (used by Path B: gateway personal channels).

    Maps gateway channel metadata to the unified execute_sage_turn() call.
    Returns a channel-compatible result dict.

    agent_id: the specialist install this personal-channel session is bound
    to (empty = the pre-existing behavior, run as Sage). Reuses the SAME
    specialist_context resolution every other channel (Discord/Slack/hosted
    Telegram) already runs turns through — see
    specialist_runtime_context.resolve_specialist_runtime_context's own
    docstring for the two guarantees this carries: the turn's memory scope
    becomes the specialist's own install id, and it never receives
    fleet_tools/operator powers. Resolution failures fail safe to Sage
    (unchanged pre-existing behavior), never to an error.

    attachments: media-pipeline attachments already resolved+stored by
    personal_channel_media_store_service (image/file kinds only — voice/audio
    are transcribed into `message` text by the caller instead). Forwarded
    as-is to execute_sage_turn(), which already threads attachments all the
    way through to handle_sage_chat() for the web-chat upload flow — this is
    the same pipe, just fed from a personal channel instead of the web UI.
    """
    normalized_channel = _coerce_text(surface_channel)
    normalized_agent_id = _coerce_text(agent_id)

    specialist_context = None
    if normalized_agent_id:
        from server_modules.specialist_runtime_context import resolve_specialist_runtime_context

        try:
            specialist_context = await resolve_specialist_runtime_context(
                workspace_id=workspace_id,
                tenant_id=tenant_id or "default",
                active_agent_install_id=normalized_agent_id,
            )
        except Exception:
            specialist_context = None  # fail safe to Sage, exactly like the resolver's own None cases

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
        attachments=list(attachments) if attachments else None,
        specialist_context=specialist_context,
        channel_prior_messages=channel_prior_messages,
    )

    return sage_result.as_dict()
