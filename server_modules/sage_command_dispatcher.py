"""Shared command dispatcher for Sage — all channels route /commands through here.

Every active channel (web, telegram hosted, telegram CSM, discord DM,
whatsapp, slack, wechat, imessage) calls dispatch_command() before
handle_sage_chat(). This guarantees identical behavior across channels.
"""

from __future__ import annotations

import logging
import random
import string
from datetime import datetime, timezone
from typing import Optional

_logger = logging.getLogger(__name__)

SUPPORTED_COMMANDS = ["/compact", "/new", "/main", "/approve", "/deny", "/help", "/memory"]

# ── Standardized error / status messages ──
SAGE_OVERFLOW_REPLY = "📦 My context was too full — I've compacted it. Please resend your message."
SAGE_UNAVAILABLE_REPLY = "😴 I'm temporarily unavailable. Please try again in a moment."
SAGE_NO_PENDING_APPROVALS = "No pending approvals."
SAGE_APPROVED = "✅ Approved."
SAGE_DENIED = "❌ Denied."
SAGE_COMPACTED = "✅ Context compacted."
SAGE_COMPACT_NOT_NEEDED = "Nothing to compact — context is still small."
SAGE_NEW_SESSION = "🆕 New session started. Type /main to return to your main thread."
SAGE_MAIN_RETURN = "🏠 Back to your main thread."
SAGE_NO_MEMORIES = "📭 No memories saved yet."

# ── AI limit / attention messages (ONE source of truth for all channels) ──
# These are the ONLY user-facing AI-stop messages.  They are generic,
# non-prescriptive, and brand-free.  The AI & Setup tab is where the
# user sees what happened and chooses what to do.
_SAGE_AI_SETUP_LABEL = "AI \\& Setup"

# ── Error classification constants ────────────────────────────────────────
# classify_error() below maps raw error strings to ONE of these five
# buckets.  Every channel (Telegram, Discord, WhatsApp, route handlers)
# uses this SINGLE function to build user-facing error text.

# Bucket 1 — Credits exhausted
SAGE_AI_LIMIT_REPLY = (
    "⚠️ Heads up — credit exhausted. "
    "Add your own API key or top up to continue."
)

# Bucket 2 — Rate limited
SAGE_RATE_LIMITED_REPLY = (
    "⚠️ Heads up — service is being rate limited. "
    "Try again in a moment."
)

# Bucket 3 — Auth / key failed
SAGE_AI_NEEDS_ATTENTION_REPLY = (
    "⚠️ Heads up — AI service authentication failed. "
    "Check your API key."
)

# Bucket 4 — Provider unreachable
SAGE_PROVIDER_UNREACHABLE_REPLY = (
    "⚠️ Heads up — AI service is unreachable right now. "
    "Try again shortly."
)

# Bucket 5 — Catch-all
SAGE_ERROR_REPLY = "⚠️ Heads up — something went wrong. Try again."

# Web-chat plain-text variants (no Telegram markdown escaping)
SAGE_AI_LIMIT_MESSAGE = "You've reached your AI limit. Open AI & Setup →"
SAGE_AI_NEEDS_ATTENTION_MESSAGE = "Your AI needs attention. Open AI & Setup →"

SAGE_HELP_TEXT = (
    "Available commands:\n"
    "/compact — summarize and clear old context\n"
    "/new — start a new task session\n"
    "/main — return to your main Sage thread\n"
    "/approve — approve pending action\n"
    "/deny — deny pending action\n"
    "/memory — show what I remember about you\n"
    "/help — show this message"
)


# ── Error classification ──────────────────────────────────────────────────

def classify_error(error_text: str | None, *, raw_error: str = "") -> str:
    """Map an error string to the appropriate user-facing reply constant.

    SINGLE source of truth for error classification — all channels use
    this ONE function.  Returns one of the five SAGE_*_REPLY constants.

    When *raw_error* is non-empty, it is appended as a second line::

        ↳ {raw_error}

    Five specific buckets, checked in order:

    1. Credits exhausted  → SAGE_AI_LIMIT_REPLY
    2. Rate limited       → SAGE_RATE_LIMITED_REPLY
    3. Auth / key failed  → SAGE_AI_NEEDS_ATTENTION_REPLY
    4. Provider unreachable → SAGE_PROVIDER_UNREACHABLE_REPLY
    5. Catch-all          → SAGE_ERROR_REPLY
    """
    if not error_text:
        base = SAGE_ERROR_REPLY
    else:
        msg = str(error_text).lower().strip()

        # 1) Credits exhausted
        if any(kw in msg for kw in (
            "reached your ai limit", "ai limit", "cap_reached",
        )):
            base = SAGE_AI_LIMIT_REPLY
        # 2) Rate limited
        elif any(kw in msg for kw in (
            "provider_rate_limited", "429", "rate limit", "too many requests",
        )):
            base = SAGE_RATE_LIMITED_REPLY
        # 3) Auth / key failed
        elif any(kw in msg for kw in (
            "provider_generation_failed", "401", "403", "auth", "api key",
            "invalid key", "unauthorized",
        )):
            base = SAGE_AI_NEEDS_ATTENTION_REPLY
        # 4) Provider unreachable
        elif any(kw in msg for kw in (
            "provider_transport_unavailable", "transport", "connection",
            "timeout", "unreachable",
        )):
            base = SAGE_PROVIDER_UNREACHABLE_REPLY
        # 5) Catch-all
        else:
            base = SAGE_ERROR_REPLY

    if raw_error:
        return base + "\n↳ " + str(raw_error)
    return base


# ── Active thread resolution ──

async def get_active_thread(
    workspace_id: str,
    channel_origin: str,
) -> str:
    """Return the active thread_id for this workspace+channel.

    Reads workspace.channel_active_threads JSONB.
    Returns "sage-main" if no active task thread is set.
    Tries direct asyncpg first, falls back to server pool.
    """
    if not channel_origin:
        return "sage-main"

    query = "SELECT channel_active_threads FROM workspaces WHERE id = $1"

    # 1) Direct asyncpg (works standalone + test)
    import os as _os
    _dsn = _os.environ.get("DATABASE_URL", "").strip()
    if _dsn:
        try:
            import asyncpg as _apg
            _conn = await _apg.connect(_dsn)
            try:
                row = await _conn.fetchrow(query, workspace_id)
                if row:
                    raw = row["channel_active_threads"]
                    cat = {}
                    if isinstance(raw, dict):
                        cat = raw
                    elif isinstance(raw, str) and raw.strip():
                        import json as _json
                        try:
                            cat = _json.loads(raw)
                        except Exception:
                            pass
                    active = cat.get(channel_origin) if isinstance(cat, dict) else None
                    if active and isinstance(active, str) and active.strip():
                        return active.strip()
            finally:
                await _conn.close()
        except Exception:
            pass

    # 2) Server pool fallback
    try:
        from server_modules.control_plane_repository import get_workspace_by_id
        ws = await get_workspace_by_id(workspace_id)
        if isinstance(ws, dict):
            cat = ws.get("channel_active_threads")
            if isinstance(cat, dict):
                active = cat.get(channel_origin)
                if active and isinstance(active, str) and active.strip():
                    return active.strip()
    except Exception:
        pass
    return "sage-main"


async def _set_active_thread(
    workspace_id: str,
    channel_origin: str,
    thread_id: str,
) -> None:
    """Store the active thread_id for this workspace+channel in DB.

    Tries direct asyncpg connection first (works standalone), falls back
    to server pool (production context).
    """
    import json as _json
    import os as _os

    cat: dict = {}
    try:
        from server_modules.control_plane_repository import get_workspace_by_id
        ws = await get_workspace_by_id(workspace_id)
        if isinstance(ws, dict):
            raw = ws.get("channel_active_threads")
            if isinstance(raw, dict):
                cat = dict(raw)
    except Exception:
        pass

    if thread_id == "sage-main":
        cat.pop(channel_origin, None)
    else:
        cat[channel_origin] = thread_id

    query = "UPDATE workspaces SET channel_active_threads = $1::jsonb WHERE id = $2"
    params = (_json.dumps(cat), workspace_id)

    # 1) Direct asyncpg connection (works standalone + test)
    _dsn = _os.environ.get("DATABASE_URL", "").strip()
    if _dsn:
        try:
            import asyncpg as _apg
            _conn = await _apg.connect(_dsn)
            try:
                await _conn.execute(query, *params)
                return  # success
            finally:
                await _conn.close()
        except Exception:
            pass

    # 2) Server pool fallback (production context)
    try:
        from server_modules.control_plane_repository import ensure_control_plane_schema
        pool = await ensure_control_plane_schema()
        if pool is not None:
            async with pool.acquire() as conn:
                await conn.execute(query, *params)
    except Exception as exc:
        _logger.warning("_set_active_thread failed (pool path): %s", exc)


# ── Main dispatcher ──

async def dispatch_command(
    *,
    command: str,
    workspace_id: str,
    thread_id: str = "sage-main",
    channel_origin: str = "",
    sender_id: str | None = None,
) -> str | None:
    """Dispatch a /command and return the reply string.

    Delegates to the single :mod:`command_registry`.  Returns ``None`` when
    *command* is not a recognised slash command (caller should pass it through
    to handle_sage_chat() as a normal message).
    """
    from server_modules.command_registry import dispatch as _dispatch

    result = await _dispatch(
        text=str(command or ""),
        workspace_id=workspace_id,
        surface="channel",
        thread_id=thread_id,
        channel_origin=channel_origin,
        sender_id=sender_id,
    )
    if result is None:
        return None
    return str(result.get("reply") or "") or None


# ── Internal handlers ──

async def _handle_compact(workspace_id: str, thread_id: str) -> str:
    try:
        from server_modules.compaction_service import (
            compact_turns, find_cut_point, should_compact,
            load_previous_summary, resolve_context_window,
        )
        from server_modules import thread_service
        from server_modules.workspace_config_schema import workspace_admin_defaults_from_metadata
        from server_modules.control_plane_repository import get_workspace_by_id

        # Resolve the workspace's active provider to compute the real context window
        _ws_provider: str = ""
        try:
            _ws_rec = await get_workspace_by_id(workspace_id)
            _ws_meta = dict((_ws_rec or {}).get("metadata") or {})
            _ws_defaults = workspace_admin_defaults_from_metadata(_ws_meta)
            _ws_provider = str(_ws_defaults.sage_ai_provider or "").strip().lower()
        except Exception:
            _ws_provider = ""
        _ctx_window = resolve_context_window(_ws_provider or None, None)

        tenant_id = "default"
        await thread_service.ensure_master_thread(
            thread_id=thread_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            owner_user_id="sage",
            channel="sage",
        )
        thread_record = await thread_service.get_thread(
            thread_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            include_turns=True,
        )
        raw_turns = list(thread_record.get("turns") or []) if isinstance(thread_record, dict) else []
        if raw_turns and should_compact(raw_turns, context_window=_ctx_window):
            cut_idx = find_cut_point(raw_turns, context_window=_ctx_window)
            if cut_idx > 0:
                prev = await load_previous_summary(
                    workspace_id=workspace_id,
                    tenant_id=tenant_id,
                    thread_id=thread_id,
                )
                await compact_turns(
                    turns=raw_turns[:cut_idx],
                    workspace_id=workspace_id,
                    tenant_id=tenant_id,
                    thread_id=thread_id,
                    previous_summary=prev,
                )
            return SAGE_COMPACTED
        return SAGE_COMPACT_NOT_NEEDED
    except Exception as exc:
        _logger.warning("compact failed for workspace=%s: %s", workspace_id, exc)
        return SAGE_ERROR_REPLY


async def _handle_new(workspace_id: str, channel_origin: str) -> str:
    """Create a new task thread and switch this channel to it."""
    try:
        from server_modules import thread_service
        from server_modules.control_plane_repository import get_workspace_by_id

        ws = await get_workspace_by_id(workspace_id)
        tenant_id = str((ws or {}).get("tenant_id") or "default").strip() or "default"

        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
        new_thread_id = f"task-{date_str}-{suffix}"

        await thread_service.ensure_master_thread(
            thread_id=new_thread_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            owner_user_id="sage",
            channel="sage",
            title=f"Task — {date_str}",
        )

        if channel_origin:
            await _set_active_thread(workspace_id, channel_origin, new_thread_id)

        return SAGE_NEW_SESSION
    except Exception as exc:
        _logger.warning("/new failed for workspace=%s: %s", workspace_id, exc)
        return SAGE_ERROR_REPLY


async def _handle_main(workspace_id: str, channel_origin: str) -> str:
    """Switch back to the sage-main thread."""
    try:
        if channel_origin:
            await _set_active_thread(workspace_id, channel_origin, "sage-main")
        return SAGE_MAIN_RETURN
    except Exception as exc:
        _logger.warning("/main failed for workspace=%s: %s", workspace_id, exc)
        return SAGE_ERROR_REPLY


async def _handle_approve(workspace_id: str, *, approved: bool) -> str:
    try:
        from server_modules import gateway_state_repository
        pending = gateway_state_repository.list_gateway_action_approvals(
            gateway_id="",
            status="pending",
            limit=1,
        )
        if not pending or len(pending) == 0:
            return SAGE_NO_PENDING_APPROVALS
        return SAGE_NO_PENDING_APPROVALS
    except Exception as exc:
        _logger.warning("approve/deny failed for workspace=%s: %s", workspace_id, exc)
        return SAGE_ERROR_REPLY


async def _handle_memory(workspace_id: str) -> str:
    try:
        from server_modules.workspace_context import workspace_scope_dir
        memory_path = workspace_scope_dir(workspace_id) / "MEMORY.md"
        if not memory_path.exists():
            return SAGE_NO_MEMORIES
        content = memory_path.read_text(encoding="utf-8").strip()
        if len(content) < 10:
            return SAGE_NO_MEMORIES
        preview = content[:1500]
        return "📋 What I remember:\n\n" + preview

    except Exception as exc:
        _logger.warning("memory read failed for workspace=%s: %s", workspace_id, exc)
        return SAGE_ERROR_REPLY
