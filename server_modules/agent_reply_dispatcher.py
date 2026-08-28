"""
Sage reply dispatcher — the SINGLE owner of ALL channel reliability logic.

Every channel (Telegram, Discord, Slack, personal bridges) routes its
outbound reply through dispatch_sage_reply().  This guarantees:

  - No hardcoded status/error/failure message EVER reaches a channel (DM
    or group) — on a turn error, quota denial, timeout, or empty result,
    the channel gets NOTHING; the failure is logged and surfaced on the
    dashboard/activity feed via durability_signal instead. See
    channel_adapter.filter_channel_outbound_reply() and
    platform_event.CHANNEL_SUPPRESSED_TEXTS.
  - Message splitting (at channel.max_message_length, paragraph-aware)
  - Typing lifecycle (start → AI turn → stop in finally)
  - Format fallback (formatted first, plain-text retry on parse error)

Architecture:
  Channel wrapper → dispatch_command() → dispatch_sage_reply()
  → (typing + execute_sage_turn + filter + split + send)

The channel wrapper owns: parse inbound, resolve workspace, resolve media.
The dispatcher owns: EVERYTHING after that.

Web chat is a SEPARATE path (assistant_chat_api.py calls handle_sage_chat()
directly) and is intentionally not routed through here — it may still show
the user what went wrong.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from server_modules.channel_transport import ChannelTransport
from server_modules.inbound_envelope import InboundEnvelope

_logger = logging.getLogger(__name__)

# ── Per-channel turn serialization ──────────────────────────────────────
# Channel paths (Telegram, Discord, Slack, etc.) bypass the web session
# manager's actor queue.  Two concurrent messages for the same
# (workspace_id, thread_id) would race — interleaved LLM calls, tool
# execution, and state mutations.  This lightweight per-key asyncio lock
# serialises turns so only ONE executes per (workspace, thread) at a time.
#
# Keys are cleaned up when the dict exceeds 500 entries (stale locks
# unused for ≥10 minutes are dropped).

_CHANNEL_TURN_LOCKS: dict[str, asyncio.Lock] = {}
_CHANNEL_TURN_LOCKS_LAST_SEEN: dict[str, float] = {}
_CHANNEL_TURN_LOCK_MAX_KEYS = 500
_CHANNEL_TURN_LOCK_STALE_SECONDS = 600  # 10 minutes


async def _with_channel_turn_lock(
    workspace_id: str,
    thread_id: str,
) -> asyncio.Lock:
    """Return the per-thread lock, creating it on first use."""
    lock_key = f"{workspace_id}:{thread_id}"
    if lock_key not in _CHANNEL_TURN_LOCKS:
        # Cheap cleanup sweep when the dict grows too large
        if len(_CHANNEL_TURN_LOCKS) >= _CHANNEL_TURN_LOCK_MAX_KEYS:
            now = time.time()
            stale = [
                k
                for k, ts in _CHANNEL_TURN_LOCKS_LAST_SEEN.items()
                if now - ts > _CHANNEL_TURN_LOCK_STALE_SECONDS
            ]
            for k in stale:
                _CHANNEL_TURN_LOCKS.pop(k, None)
                _CHANNEL_TURN_LOCKS_LAST_SEEN.pop(k, None)
        _CHANNEL_TURN_LOCKS[lock_key] = asyncio.Lock()
    _CHANNEL_TURN_LOCKS_LAST_SEEN[lock_key] = time.time()
    return _CHANNEL_TURN_LOCKS[lock_key]


async def acquire_channel_turn_lock(workspace_id: str, thread_id: str) -> asyncio.Lock:
    """Public entry point onto the SAME lock dict dispatch_sage_reply already
    uses — not a second mechanism.

    Added so personal_channel_sage_bridge_service.py's
    _execute_channel_turn_with_envelope (the single chokepoint every
    personal-channel reply — WhatsApp, Telegram-personal, Discord DMs, and
    the whole local-bridge/OpenClaw family via build_personal_channel_reply_
    async — funnels through before calling execute_sage_turn) can serialize
    per-thread the identical way hosted-bot/WeChat-official channels already
    do through dispatch_sage_reply. Personal channels have no explicit
    thread_id of their own; callers pass a derived one
    (f"personal:{surface_channel}:{remote_jid}" — see that module), which
    lands in the same _CHANNEL_TURN_LOCKS dict under its own namespaced key,
    so it can never collide with a hosted-bot thread_id (typically
    "sage-main") for the same workspace.
    """
    return await _with_channel_turn_lock(workspace_id, thread_id)


def split_long_message(text: str, max_len: int) -> list[str]:
    """Split a message at paragraph/sentence/space boundaries.

    Pure text utility — no channel-specific logic.  The dispatcher
    uses this with transport.max_message_length to auto-split before
    sending.

    Returns a list of chunks, each ≤ max_len.
    """
    text = str(text or "").strip()
    if not text:
        return [text]
    if len(text) <= max_len:
        return [text]

    import re as _re
    chunks: list[str] = []
    remaining = text
    while len(remaining) > max_len:
        # Try paragraph break first
        cut = remaining.rfind('\n\n', 0, max_len)
        if cut < max_len // 2:
            # No good paragraph break — try sentence end
            cut = max(
                remaining.rfind('. ', 0, max_len),
                remaining.rfind('! ', 0, max_len),
                remaining.rfind('? ', 0, max_len),
                remaining.rfind('\n', 0, max_len),
            )
        if cut < max_len // 2:
            # Still no good break — try space
            cut = remaining.rfind(' ', 0, max_len)
        if cut < max_len // 2:
            # No space at all — force cut at limit
            cut = max_len - 1

        chunks.append(remaining[:cut + 1].strip())
        remaining = remaining[cut + 1:].strip()

    if remaining:
        chunks.append(remaining)
    return chunks


def classify_error(
    error_text: str | None,
    *,
    raw_error: str = "",
    is_platform_credits: bool = True,
) -> str:
    """Map an error string to the appropriate user-facing reply constant.

    Delegates to the single source of truth in :mod:`agent_command_dispatcher`.
    This re-export exists so existing callers in this module don't need to
    change; new callers should import directly from agent_command_dispatcher.
    """
    from server_modules.agent_command_dispatcher import classify_error as _ce
    return _ce(error_text, raw_error=raw_error, is_platform_credits=is_platform_credits)


def _build_setup_hint(workspace_id: str) -> str:
    """Build the "AI & Setup" link suffix for error messages."""
    from server_modules.agent_turn_runtime_service import _SAGE_AI_SETUP_PATH

    _ws_id = str(workspace_id or "").strip()
    _setup_path = f"/w/{_ws_id}{_SAGE_AI_SETUP_PATH}" if _ws_id else _SAGE_AI_SETUP_PATH
    return f"\n\n{_setup_path}"


# Bounded in-band retry for transient channel send failures (provider 5xx,
# dropped socket). Without this, a single transient error dropped the reply
# permanently — the turn completed but the user never received the answer.
_SEND_MAX_ATTEMPTS = 3
_SEND_RETRY_BACKOFF_SECONDS = 0.5


async def _send_one_chunk(
    transport: ChannelTransport,
    text: str,
    *,
    reply_to_id: Optional[str] = None,
) -> bool:
    """Send a single chunk, with bounded retry so a transient send failure
    (5xx / dropped socket) is retried in-band instead of silently dropping
    the reply. Returns False only after all attempts fail, so the caller
    can decline to ACK the webhook.

    `text` is passed to transport.send_message() UNCHANGED — raw,
    unformatted — and EXACTLY ONCE per attempt. Formatting (and the
    formatted-vs-plain-text fallback on a parse-mode rejection) is owned
    entirely by the transport: every ChannelTransport.send_message()
    implementation is documented as a self-contained "try this channel's
    native markup, fall back to plain text on rejection" unit (see
    TelegramHostedTransport.send_message / AgentBotTransport.send_message).

    This function used to ALSO call transport.format_text(text) here and
    hand the already-formatted string into send_message() — which then
    formatted it a second time internally. Escaping/converting markup
    twice is not idempotent (Telegram MarkdownV2 turned "\\(" into
    "\\\\(" — an escaped backslash followed by a now-bare, unescaped
    paren), so the doubly-formatted send was rejected by Telegram and the
    transport's own fallback then delivered the ONCE-formatted text as
    literal plain text: visible backslashes in front of every '.', '(',
    ')', '!' the model wrote, in production, on the hosted Telegram bot.
    Formatting now happens exactly once, entirely inside the transport —
    do not reintroduce a pre-format call here.
    """
    for attempt in range(1, _SEND_MAX_ATTEMPTS + 1):
        try:
            if await transport.send_message(text, reply_to_id=reply_to_id):
                return True
        except Exception as exc:
            _logger.warning(
                "channel send attempt %d/%d raised (%s): %s",
                attempt,
                _SEND_MAX_ATTEMPTS,
                type(exc).__name__,
                exc,
            )
        if attempt < _SEND_MAX_ATTEMPTS:
            await asyncio.sleep(_SEND_RETRY_BACKOFF_SECONDS * attempt)
    return False


async def dispatch_sage_reply(
    *,
    transport: ChannelTransport,
    workspace_id: str,
    message: str,
    channel_origin: str,
    sender_id: str = "",
    sender_name: str = "",
    thread_id: str = "sage-main",
    attachments: list | None = None,
    reply_to_id: Optional[str] = None,
    specialist_context: Any = None,
    # Canonical inbound attribution (inbound_envelope.py) — who sent this,
    # from where, verified by the channel. None = unwired caller; forwarded
    # to execute_sage_turn() unchanged (that chokepoint treats None exactly
    # like a legacy pre-envelope caller — see its own docstring).
    envelope: Optional[InboundEnvelope] = None,
    # Durable per-agent conversation recall (agent_conversation_memory) —
    # the caller loads this BEFORE invoking dispatch_sage_reply (e.g. via
    # agent_conversation_memory.load_recent_turns) and hands it here so it
    # reaches execute_sage_turn's own channel_prior_messages parameter,
    # which the runtime trusts over the (dead-under-SQLite-fallback, see
    # agent_conversation_memory.py's module doc) control-plane thread store.
    # None = unwired caller, byte-for-byte unchanged behavior.
    channel_prior_messages: Optional[List[dict]] = None,
    # When set, this exchange (the inbound user turn, and the assistant
    # reply/media summary if one was actually sent) is persisted to
    # agent_conversation_memory once the turn completes — the write-side
    # half of the same durability fix, mirroring
    # personal_channel_sage_bridge_service.py's own append_turn call sites
    # (including tagging the inbound turn with envelope.to_metadata() when
    # an envelope is present, never the assistant's own reply — see that
    # module's docstring for why). Shape: {"workspace_id", "agent_id",
    # "conversation_key"}; "conversation_key" empty or dict None = no write
    # (unchanged behavior). Best-effort — a memory write failure is logged,
    # never allowed to affect what was already delivered to the channel.
    conversation_memory: Optional[Dict[str, str]] = None,
) -> bool:
    """Execute a full Sage turn and deliver the reply via transport.

    This is THE single function every channel calls after parsing an
    inbound message.  It guarantees:

      1. Typing indicator shown during AI processing
      2. execute_sage_turn() called (unified ingress)
      3. Typing stopped (in finally)
      4. Reply filtered via filter_channel_outbound_reply() — [SILENT]
         markers AND any hardcoded platform status/error string are both
         suppressed before anything is sent
      5. If empty/error/suppressed → the channel gets NOTHING; the failure
         is logged and surfaced on the dashboard/activity feed instead
      6. Message auto-split at transport.max_message_length
      7. Formatted first, plain-text retry on parse failure

    Returns True only if a real, non-suppressed reply was actually
    delivered — it does NOT guarantee a message is sent.

    Args:
        transport: Channel transport (Telegram, Discord, Slack, etc.)
        workspace_id: Target workspace
        message: User message text
        channel_origin: Canonical channel identifier ("telegram_hosted", etc.)
        sender_id: Sender identifier on the channel
        sender_name: Human-readable sender name
        thread_id: Active thread ID (default: "sage-main")
        attachments: Resolved media attachments (optional)
        reply_to_id: ID of the inbound message to reply to (first chunk only)
        specialist_context: When set, the turn runs as this specialist agent
            (persona/model/memory) instead of Sage — see specialist_runtime_context.py

    Returns:
        True if at least one message was sent to the user.
    """
    from server_modules.agent_turn_adapter import execute_sage_turn
    from server_modules.channel_adapter import filter_channel_outbound_reply

    sent_any = False

    async def _send(text: str, *, reply_to_id: Optional[str] = None) -> None:
        """Send a message — split, format, never raise."""
        nonlocal sent_any
        if not str(text or "").strip():
            return
        chunks = split_long_message(str(text), transport.max_message_length)
        for chunk in chunks:
            try:
                ok = await _send_one_chunk(transport, chunk, reply_to_id=reply_to_id)
                if ok:
                    sent_any = True
                    # ── Stage J: ledger channel send (redacted) ──────────
                    try:
                        from server_modules import ledger_audit as _la
                        await _la.record_channel_send(
                            workspace_id=workspace_id,
                            channel_key=channel_origin,
                            remote_jid=sender_id or "unknown",
                            text=chunk,
                            status="sent",
                        )
                    except Exception:
                        pass  # ledger is best-effort, never blocks send
                reply_to_id = None  # only first chunk gets reply-to
            except Exception as exc:
                _logger.warning(
                    "dispatch_sage_reply: chunk send failed for channel=%s: %s",
                    channel_origin, exc,
                )

    # ── Process directives & inline shortcuts ──────────────────────────
    # Strip /model, /thinking, /help etc. from the text before the LLM
    # sees it.  Directive-only messages are handled without calling the LLM.
    from server_modules.command_registry import process_message as _proc_msg

    _proc = await _proc_msg(
        text=message,
        workspace_id=workspace_id,
        surface="channel",
        channel_origin=channel_origin,
        sender_id=sender_id,
        sender_name=sender_name,
        thread_id=thread_id,
    )

    # Send directive/shortcut replies immediately (no typing indicator —
    # these are fast, sub-millisecond handler calls).
    for _reply_text in _proc.replies:
        await _send(_reply_text)

    if _proc.is_command_only:
        # All-directive message — nothing to send to the LLM.
        if not sent_any:
            await _send("OK.")
        return sent_any

    # Use cleaned text for the LLM turn.
    _cleaned_message = _proc.text if _proc.text.strip() else message

    # ── Start typing ──
    if transport.supports_typing_indicator:
        await transport.start_typing()

    result = None
    # Serialize per (workspace, thread) so only ONE turn executes at a time
    # for a given thread across ALL channels.  Web path has its own
    # serialization via SessionActorQueue; this covers everything else.
    lock = await _with_channel_turn_lock(workspace_id, thread_id)
    async with lock:
        try:
            result = await execute_sage_turn(
                workspace_id=workspace_id,
                message=_cleaned_message if _cleaned_message else "[Media]",
                attachments=attachments if attachments else None,
                channel_origin=channel_origin,
                channel_sender_id=sender_id,
                channel_sender_name=sender_name,
                thread_id=thread_id,
                specialist_context=specialist_context,
                envelope=envelope,
                channel_prior_messages=channel_prior_messages,
            )
        finally:
            if transport.supports_typing_indicator:
                await transport.stop_typing()

    # ── Build reply from result ──
    # ABSOLUTE RULE: no hardcoded platform status/error/failure message may
    # EVER reach a channel — DM or group. filter_channel_outbound_reply()
    # suppresses both [SILENT]/NO_REPLY markers AND any text matching a
    # known platform status/error string (quota denial, provider failure,
    # cli_subscription failure, the "nothing to say" fallback, etc.) —
    # regardless of whether it arrived via result.error or embedded
    # directly in result.message. On suppression the channel gets nothing;
    # the failure is logged and surfaced on the dashboard/activity feed.
    reply = str(result.message or "").strip() if result else ""
    channel_safe_reply = filter_channel_outbound_reply(reply) if reply else None
    media = list(getattr(result, "media", None) or []) if result else []

    from server_modules import durability_signal

    attempted_real_send = False
    if channel_safe_reply is not None:
        attempted_real_send = True
        await _send(channel_safe_reply, reply_to_id=reply_to_id)
    elif result and result.error:
        _logger.warning(
            "dispatch_sage_reply: turn error suppressed from channel (workspace=%s channel=%s): %s",
            workspace_id, channel_origin, result.error,
        )
        durability_signal.capture_durability_failure(
            f"channel turn error suppressed for workspace={workspace_id} channel={channel_origin}",
            workspace_id=workspace_id,
            channel=channel_origin,
            event_class="channel_error_suppressed",
            action="turn_error",
            summary=str(result.error)[:500],
        )
    elif reply:
        # Non-empty reply, but it matched a known platform status/error
        # string (e.g. TOOLS_LIMITED_NO_REPLY, a cli_subscription failure
        # returned as a "successful" message with error=None) — still must
        # never reach the channel.
        _logger.warning(
            "dispatch_sage_reply: status/error-flavored reply suppressed from channel (workspace=%s channel=%s)",
            workspace_id, channel_origin,
        )
        durability_signal.capture_durability_failure(
            f"channel status reply suppressed for workspace={workspace_id} channel={channel_origin}",
            workspace_id=workspace_id,
            channel=channel_origin,
            event_class="channel_error_suppressed",
            action="status_reply",
            summary=reply[:500],
        )
    else:
        # Turn genuinely produced nothing and reported no error — stay
        # silent rather than announce "no response could be produced".
        _logger.info(
            "dispatch_sage_reply: empty turn — nothing sent to channel (workspace=%s channel=%s)",
            workspace_id, channel_origin,
        )

    # ── If we HAD a legitimate reply and STILL nothing sent (send
    #    primitives all failed after retries), surface loudly: this is a
    #    genuine channel-delivery dead-letter, not intentional silence ──
    if attempted_real_send and not sent_any:
        durability_signal.capture_durability_failure(
            f"channel reply delivery for workspace={workspace_id} channel={channel_origin}",
            workspace_id=workspace_id,
            channel=channel_origin,
            event_class="channel_delivery_dead_letter",
            summary="Reply generated but could not be delivered after bounded retries.",
        )

    # ── Durable per-agent conversation memory (agent_conversation_memory) ──
    # Opt-in write-side counterpart to channel_prior_messages above. Mirrors
    # personal_channel_sage_bridge_service.py's own write sites: the inbound
    # user turn is always recorded (tagged with the envelope's metadata when
    # one was supplied — never the assistant's own reply, which isn't
    # "said by" the channel's sender), the assistant turn only when a real
    # reply or outbound media was actually produced. Best-effort: a failure
    # here must never affect a reply that already reached the channel.
    if conversation_memory:
        _mem_key = str(conversation_memory.get("conversation_key") or "").strip()
        if _mem_key:
            try:
                from server_modules import agent_conversation_memory

                _mem_ws = str(conversation_memory.get("workspace_id") or workspace_id or "default").strip() or "default"
                _mem_agent = str(conversation_memory.get("agent_id") or "").strip()
                agent_conversation_memory.append_turn(
                    workspace_id=_mem_ws, agent_id=_mem_agent,
                    conversation_key=_mem_key, role="user", content=message,
                    metadata=envelope.to_metadata() if envelope is not None else None,
                )
                if channel_safe_reply or media:
                    _assistant_content = channel_safe_reply or (
                        "[sent " + ", ".join(sorted({str(item.get("kind") or "file") for item in media})) + "]"
                    )
                    agent_conversation_memory.append_turn(
                        workspace_id=_mem_ws, agent_id=_mem_agent,
                        conversation_key=_mem_key, role="assistant", content=_assistant_content,
                    )
            except Exception:
                _logger.warning(
                    "dispatch_sage_reply: conversation memory write failed (workspace=%s channel=%s)",
                    workspace_id, channel_origin, exc_info=True,
                )

    return sent_any


async def dispatch_sage_reply_safe(
    *,
    transport: ChannelTransport,
    workspace_id: str,
    message: str,
    channel_origin: str,
    sender_id: str = "",
    sender_name: str = "",
    thread_id: str = "sage-main",
    attachments: list | None = None,
    reply_to_id: Optional[str] = None,
    specialist_context: Any = None,
    envelope: Optional[InboundEnvelope] = None,
    channel_prior_messages: Optional[List[dict]] = None,
    conversation_memory: Optional[Dict[str, str]] = None,
) -> bool:
    """Like dispatch_sage_reply() but catches ALL exceptions.

    Use this when the caller absolutely must not raise (e.g. webhook
    handlers that need to return HTTP 200).

    ABSOLUTE RULE: no hardcoded status/error message may EVER reach a
    channel. If the AI turn itself throws, the exception is logged and
    surfaced on the dashboard/activity feed via durability_signal — the
    channel receives NOTHING. This intentionally does NOT guarantee a
    message is sent; it guarantees the caller never raises and the channel
    never sees a canned error string.
    """
    try:
        return await dispatch_sage_reply(
            transport=transport,
            workspace_id=workspace_id,
            message=message,
            channel_origin=channel_origin,
            sender_id=sender_id,
            sender_name=sender_name,
            thread_id=thread_id,
            attachments=attachments,
            reply_to_id=reply_to_id,
            specialist_context=specialist_context,
            envelope=envelope,
            channel_prior_messages=channel_prior_messages,
            conversation_memory=conversation_memory,
        )
    except Exception as exc:
        _logger.exception(
            "dispatch_sage_reply_safe: unhandled exception suppressed from channel (workspace=%s channel=%s)",
            workspace_id, channel_origin,
        )

        from server_modules import durability_signal

        durability_signal.capture_durability_failure(
            f"dispatch_sage_reply_safe unhandled exception for workspace={workspace_id} channel={channel_origin}",
            exc,
            workspace_id=workspace_id,
            channel=channel_origin,
            event_class="channel_error_suppressed",
            action="unhandled_exception",
            summary=str(exc)[:500],
        )

        # ── Stop typing on exception (typing may not have stopped if
        #     the exception happened before the try/finally in dispatch) ──
        if transport.supports_typing_indicator:
            try:
                await transport.stop_typing()
            except Exception:
                pass

        return False
