"""
Sage reply dispatcher — the SINGLE owner of ALL channel reliability logic.

Every channel (Telegram, Discord, Slack, personal bridges, web) routes
its outbound reply through dispatch_sage_reply().  This guarantees:

  - Guaranteed response (NEVER silence — fallback on empty/error/suppressed)
  - Error classification (AI limit vs attention vs generic — ONE copy)
  - Message splitting (at channel.max_message_length, paragraph-aware)
  - Typing lifecycle (start → AI turn → stop in finally)
  - Format fallback (formatted first, plain-text retry on parse error)
  - [SILENT] suppression (via shared filter_outbound_reply)

Architecture:
  Channel wrapper → dispatch_command() → dispatch_sage_reply()
  → (typing + execute_sage_turn + classify + split + send)

The channel wrapper owns: parse inbound, resolve workspace, resolve media.
The dispatcher owns: EVERYTHING after that.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from server_modules.channel_transport import ChannelTransport
from server_modules.platform_event import GUARANTEED_FALLBACK

_logger = logging.getLogger(__name__)

# ── Shared constants ──
_GUARANTEED_FALLBACK = GUARANTEED_FALLBACK.channel_text

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


def classify_error(error_text: str | None, *, raw_error: str = "") -> str:
    """Map an error string to the appropriate user-facing reply constant.

    Delegates to the single source of truth in :mod:`sage_command_dispatcher`.
    This re-export exists so existing callers in this module don't need to
    change; new callers should import directly from sage_command_dispatcher.
    """
    from server_modules.sage_command_dispatcher import classify_error as _ce
    return _ce(error_text, raw_error=raw_error)


def _build_setup_hint(workspace_id: str) -> str:
    """Build the "AI & Setup" link suffix for error messages."""
    from server_modules.sage_agent_runtime_service import _SAGE_AI_SETUP_PATH

    _ws_id = str(workspace_id or "").strip()
    _setup_path = f"/w/{_ws_id}{_SAGE_AI_SETUP_PATH}" if _ws_id else _SAGE_AI_SETUP_PATH
    return f"\n\n{_setup_path}"


async def _send_one_chunk(
    transport: ChannelTransport,
    text: str,
    *,
    reply_to_id: Optional[str] = None,
) -> bool:
    """Send a single chunk — formatted first, plain-text on failure."""
    formatted = transport.format_text(text)
    if formatted != text:
        if await transport.send_message(formatted, reply_to_id=reply_to_id):
            return True
    # Plain-text fallback
    return await transport.send_message(text, reply_to_id=reply_to_id)


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
) -> bool:
    """Execute a full Sage turn and deliver the reply via transport.

    This is THE single function every channel calls after parsing an
    inbound message.  It guarantees:

      1. Typing indicator shown during AI processing
      2. execute_sage_turn() called (unified ingress)
      3. Typing stopped (in finally)
      4. Error classified via classify_error()
      5. Reply filtered via filter_outbound_reply()
      6. If empty/suppressed → guaranteed fallback sent
      7. Message auto-split at transport.max_message_length
      8. Formatted first, plain-text retry on parse failure
      9. NEVER returns without sending at least one message

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

    Returns:
        True if at least one message was sent to the user.
    """
    from server_modules.sage_turn_adapter import execute_sage_turn
    from server_modules.channel_adapter import filter_outbound_reply

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
            )
        finally:
            if transport.supports_typing_indicator:
                await transport.stop_typing()

    # ── Build reply from result ──
    reply = str(result.message or "").strip() if result else ""
    setup_hint = _build_setup_hint(workspace_id)

    if reply and filter_outbound_reply(reply) is not None:
        # Normal success path
        await _send(reply, reply_to_id=reply_to_id)
    elif result and result.error:
        # Error captured in result (not raised)
        classified = classify_error(str(result.error), raw_error=str(result.error))
        await _send(classified + setup_hint, reply_to_id=reply_to_id)
    else:
        # GUARANTEED RESPONSE: reply empty with no error
        await _send(_GUARANTEED_FALLBACK, reply_to_id=reply_to_id)

    # ── If STILL nothing sent (send primitives all failed), log ──
    if not sent_any:
        _logger.error(
            "dispatch_sage_reply: FAILED to send ANY message for workspace=%s channel=%s",
            workspace_id, channel_origin,
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
) -> bool:
    """Like dispatch_sage_reply() but catches ALL exceptions.

    Use this when the caller absolutely must not raise (e.g. webhook
    handlers that need to return HTTP 200).  If the AI turn itself
    throws, classify the exception and send the classified error reply.

    NEVER returns without sending at least one message.
    """
    setup_hint = _build_setup_hint(workspace_id)

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
        )
    except Exception as exc:
        _logger.exception(
            "dispatch_sage_reply_safe: unhandled exception for workspace=%s channel=%s",
            workspace_id, channel_origin,
        )

        # ── Last-resort: classify + send via transport directly ──
        classified = classify_error(str(exc), raw_error=str(exc))
        text = classified + setup_hint

        sent = False
        try:
            chunks = split_long_message(text, transport.max_message_length)
            for chunk in chunks:
                if await _send_one_chunk(transport, chunk, reply_to_id=reply_to_id):
                    sent = True
        except Exception:
            _logger.critical(
                "dispatch_sage_reply_safe: even last-resort send failed for workspace=%s channel=%s",
                workspace_id, channel_origin,
            )

        # ── Stop typing on exception (typing may not have stopped if
        #     the exception happened before the try/finally in dispatch) ──
        if transport.supports_typing_indicator:
            try:
                await transport.stop_typing()
            except Exception:
                pass

        return sent
