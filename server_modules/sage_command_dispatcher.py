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

from server_modules.platform_event import (
    AI_LIMIT_REACHED,
    AUTH_FAILED,
    AUTH_FAILED_PLATFORM,
    GENERIC_ERROR,
    NO_AI_PROVIDER,
    NO_AI_PROVIDER_WEB,
    PROVIDER_PAYMENT_REQUIRED_BYOK,
    PROVIDER_PAYMENT_REQUIRED_PLATFORM,
    PROVIDER_UNREACHABLE,
    SAGE_COMPACT_NOT_NEEDED as _SAGE_COMPACT_NOT_NEEDED,
    SAGE_COMPACTED as _SAGE_COMPACTED,
    SAGE_HELP as _SAGE_HELP,
    SAGE_MAIN_RETURN as _SAGE_MAIN_RETURN,
    SAGE_NEW_SESSION as _SAGE_NEW_SESSION,
    SAGE_NO_MEMORIES as _SAGE_NO_MEMORIES,
    SAGE_OVERFLOW as _SAGE_OVERFLOW,
    SAGE_UNAVAILABLE as _SAGE_UNAVAILABLE,
    SERVICE_RATE_LIMITED,
    AI_LIMIT_REACHED_WEB,
    AUTH_FAILED_WEB,
)

_logger = logging.getLogger(__name__)

SUPPORTED_COMMANDS = ["/compact", "/new", "/main", "/help", "/memory"]

# ── Standardized error / status messages ──
# All sourced from platform_event.py — channel layer never speaks as agent.
SAGE_OVERFLOW_REPLY = _SAGE_OVERFLOW.channel_text
SAGE_UNAVAILABLE_REPLY = _SAGE_UNAVAILABLE.channel_text
SAGE_COMPACTED = _SAGE_COMPACTED.channel_text
SAGE_COMPACT_NOT_NEEDED = _SAGE_COMPACT_NOT_NEEDED.channel_text
SAGE_NEW_SESSION = _SAGE_NEW_SESSION.channel_text
SAGE_MAIN_RETURN = _SAGE_MAIN_RETURN.channel_text
SAGE_NO_MEMORIES = _SAGE_NO_MEMORIES.channel_text

# ── AI limit / attention messages (ONE source of truth for all channels) ──
_SAGE_AI_SETUP_LABEL = "AI \\& Setup"

# ── Error classification constants ────────────────────────────────────────
SAGE_AI_LIMIT_REPLY = AI_LIMIT_REACHED.channel_text
SAGE_RATE_LIMITED_REPLY = SERVICE_RATE_LIMITED.channel_text
SAGE_AI_NEEDS_ATTENTION_REPLY = AUTH_FAILED.channel_text
SAGE_AI_NEEDS_ATTENTION_PLATFORM_REPLY = AUTH_FAILED_PLATFORM.channel_text
SAGE_PAYMENT_REQUIRED_PLATFORM_REPLY = PROVIDER_PAYMENT_REQUIRED_PLATFORM.channel_text
SAGE_PAYMENT_REQUIRED_BYOK_REPLY = PROVIDER_PAYMENT_REQUIRED_BYOK.channel_text
SAGE_PROVIDER_UNREACHABLE_REPLY = PROVIDER_UNREACHABLE.channel_text
SAGE_NO_PROVIDER_REPLY = NO_AI_PROVIDER.channel_text
SAGE_ERROR_REPLY = GENERIC_ERROR.channel_text

# Web-chat plain-text variants (no Telegram markdown escaping)
SAGE_AI_LIMIT_MESSAGE = AI_LIMIT_REACHED_WEB.channel_text
SAGE_AI_NEEDS_ATTENTION_MESSAGE = AUTH_FAILED_WEB.channel_text
SAGE_NO_PROVIDER_MESSAGE = NO_AI_PROVIDER_WEB.channel_text

SAGE_HELP_TEXT = _SAGE_HELP.channel_text


# ── Error classification ──────────────────────────────────────────────────

def classify_error(
    error_text: str | None,
    *,
    raw_error: str = "",
    is_platform_credits: bool = True,
) -> str:
    """Map an error string to the appropriate user-facing reply constant.

    SINGLE source of truth for error classification — all channels use
    this ONE function.  Returns one of the SAGE_*_REPLY constants.

    *raw_error* is accepted for backward compatibility but is NOT appended
    to the chat reply — raw error details belong in logs, not the chat
    surface.  The classified base message is always in platform voice.

    *is_platform_credits* distinguishes who owns the AI credential this
    turn ran on. Defaults to True (the safer, less-blaming assumption) so a
    caller that hasn't been updated to pass it never accidentally tells a
    customer to "verify" a key they never configured — a BYOK caller must
    opt in explicitly by passing is_platform_credits=False. It only affects
    buckets where the user-facing action genuinely differs by ownership
    (payment required, auth failed); other buckets are ownership-neutral.

    Seven specific buckets, checked in order:

    1. Credits exhausted (platform usage cap) → SAGE_AI_LIMIT_REPLY
    2. Provider payment/balance required      → SAGE_PAYMENT_REQUIRED_{PLATFORM,BYOK}_REPLY
    3. Rate limited                           → SAGE_RATE_LIMITED_REPLY
    4. No provider set                        → SAGE_NO_PROVIDER_REPLY (known-fixable state)
    5. Auth / key failed                      → SAGE_AI_NEEDS_ATTENTION_{REPLY,PLATFORM_REPLY}
    6. Provider unreachable                   → SAGE_PROVIDER_UNREACHABLE_REPLY
    7. Catch-all                              → SAGE_ERROR_REPLY
    """
    if raw_error:
        import logging
        logging.getLogger(__name__).debug("classify_error raw: %s", raw_error)
    stripped = str(error_text or "").strip()
    if stripped.startswith("Heads up:"):
        # Already a final, specific, platform-voice message (e.g. from
        # _friendly_cli_subscription_error / _friendly_cli_setup_error) —
        # re-running keyword classification on it can only make it VAGUER,
        # never more accurate, since those messages often embed raw reason
        # text (e.g. "...(Gateway connection is no longer active)") whose
        # words ("connection", "timeout") collide with this function's own
        # generic buckets below and silently downgrade a specific reason to
        # SAGE_PROVIDER_UNREACHABLE_REPLY. Pass it through untouched.
        return stripped
    if not error_text:
        base = SAGE_ERROR_REPLY
    else:
        msg = str(error_text).lower().strip()

        # 1) Credits exhausted (the platform's own workspace usage cap)
        if any(kw in msg for kw in (
            "reached your ai limit", "ai limit", "cap_reached",
        )):
            base = SAGE_AI_LIMIT_REPLY
        # 2) Provider-side payment/balance required (e.g. HTTP 402) — the
        #    key authenticates fine, the account behind it is empty. Must be
        #    checked before the auth bucket: "provider_generation_failed"
        #    (the generic fallback code) is itself one of that bucket's
        #    keywords, and a stale balance must never masquerade as a
        #    customer-facing auth problem.
        elif any(kw in msg for kw in (
            "provider_payment_required", "http_402", "payment required",
            "insufficient balance", "insufficient_balance",
        )):
            base = (
                SAGE_PAYMENT_REQUIRED_PLATFORM_REPLY
                if is_platform_credits
                else SAGE_PAYMENT_REQUIRED_BYOK_REPLY
            )
        # 3) Rate limited
        elif any(kw in msg for kw in (
            "provider_rate_limited", "429", "rate limit", "too many requests",
        )):
            base = SAGE_RATE_LIMITED_REPLY
        # 4) No cloud provider configured — known, fixable state.
        #    Must be checked before the auth bucket: "not configured" is not
        #    the same as "auth failed", and the user-facing action differs
        #    (connect a provider vs verify an existing key).
        elif any(kw in msg for kw in (
            "no cloud provider is configured",
            "no provider is configured",
            "no_ai_provider",
            "provider_not_configured",
        )):
            base = SAGE_NO_PROVIDER_REPLY
        # 5) Auth / key failed
        elif any(kw in msg for kw in (
            "provider_generation_failed", "401", "403", "auth", "api key",
            "invalid key", "unauthorized",
        )):
            base = (
                SAGE_AI_NEEDS_ATTENTION_PLATFORM_REPLY
                if is_platform_credits
                else SAGE_AI_NEEDS_ATTENTION_REPLY
            )
        # 6) Provider unreachable
        elif any(kw in msg for kw in (
            "provider_transport_unavailable", "transport", "connection",
            "timeout", "unreachable",
        )):
            base = SAGE_PROVIDER_UNREACHABLE_REPLY
        # 7) Catch-all
        else:
            base = SAGE_ERROR_REPLY

    return base


# ── Active thread resolution ──

def agent_sender_thread_id(agent_install_id: str, sender_id: str) -> str:
    """Deterministic per-(agent, sender) thread id for a resolved specialist turn.

    Every distinct sender gets its own thread per agent — including the
    owner's own identity when it reaches this path — so different customers,
    and different agents on the same channel type, never interleave into one
    context. Deterministic and unstored, unlike get_active_thread's DB
    pointer: the same (agent, sender) pair always names the same thread, no
    lookup required.

    sender_id empty is the one edge case (a channel wrapper that didn't
    resolve one) — falls back to a per-agent-only bucket rather than
    collapsing into the shared legacy "sage-main" pointer, which would
    reintroduce cross-sender bleed for exactly the callers this exists to fix.
    """
    agent_token = str(agent_install_id or "").strip()
    sender_token = str(sender_id or "").strip() or "unscoped"
    return f"agent:{agent_token}:{sender_token}"


async def get_active_thread(
    workspace_id: str,
    channel_origin: str,
) -> str:
    """Return the active thread_id for this workspace+channel.

    Reads workspace.channel_active_threads JSONB via the tenant-scoped
    control-plane repository. Returns "sage-main" if no active task thread set.

    LEGACY_UNSCOPED path: only reached today when no specialist agent is
    resolved for the turn (running as Sage/master) — see
    sage_turn_adapter.execute_sage_turn's thread-resolution branch, which
    routes specialist turns through agent_sender_thread_id() instead so
    different agents (and different senders) on the same channel type don't
    share this one workspace+channel-type pointer.
    """
    if not channel_origin:
        return "sage-main"

    # get_workspace_by_id runs under the RLS scope (bypass, keyed by workspace_id);
    # a raw asyncpg connection here would carry no tenant GUC and RLS would
    # silently return no rows.
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

    Runs under the RLS scope: a raw asyncpg or unscoped pool connection carries
    no tenant GUC, so under FORCE RLS the UPDATE would violate the workspaces
    policy and never persist. bypass_rls matches how get_workspace_by_id reads
    this same row by workspace id.
    """
    import json as _json
    from server_modules import control_plane_repository as _cpr

    cat: dict = {}
    try:
        ws = await _cpr.get_workspace_by_id(workspace_id)
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

    try:
        pool = await _cpr.ensure_control_plane_schema()
        if pool is not None:
            await _cpr.rls_execute(pool, query, *params, bypass_rls=True)
    except Exception as exc:
        _logger.warning("_set_active_thread failed: %s", exc)


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
    from server_modules.command_registry import build_service_kwargs_for_text as _build_service_kwargs

    # Same gap sage_turn_adapter.execute_sage_turn's own command block had:
    # /stop /model /tools /status /debug read a `services` and/or
    # `availability_payload`/`tool_capabilities` kwarg that a bare
    # command_registry.dispatch() call never supplies. This is the OTHER
    # live entry point every channel routes through before
    # execute_sage_turn (hosted Telegram, WeChat official) — see this
    # module's own docstring — so it needs the identical fix, built from
    # the same function rather than a second hand-copied definition.
    _service_kwargs = _build_service_kwargs(command, workspace_id)

    result = await _dispatch(
        text=str(command or ""),
        workspace_id=workspace_id,
        surface="channel",
        thread_id=thread_id,
        channel_origin=channel_origin,
        sender_id=sender_id,
        **_service_kwargs,
    )
    if result is None:
        return None
    return str(result.get("reply") or "") or None


# ── Internal handlers ──

async def _handle_compact(workspace_id: str, thread_id: str) -> str:
    try:
        from server_modules.compaction_service import (
            compact_turns, find_cut_point_with_fallback, should_compact,
            load_previous_summary, resolve_context_window,
        )
        from server_modules import thread_service
        from server_modules.workspace_config_schema import workspace_admin_defaults_from_metadata
        from server_modules.control_plane_repository import get_workspace_by_id

        # Resolve the workspace's active provider/model (its configured brain)
        # both to compute the real context window and, below, so a manual
        # `/sage compact` summarizes on that same model instead of
        # compact_turns' silent platform-wide "deepseek" default.
        _ws_provider: str = ""
        _ws_model: str = ""
        try:
            _ws_rec = await get_workspace_by_id(workspace_id)
            _ws_meta = dict((_ws_rec or {}).get("metadata") or {})
            _ws_defaults = workspace_admin_defaults_from_metadata(_ws_meta)
            _ws_provider = str(_ws_defaults.sage_ai_provider or "").strip().lower()
            _ws_model = str(_ws_defaults.sage_ai_model or "").strip()
        except Exception:
            _ws_provider = ""
            _ws_model = ""
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
        # BUG 5 fix: pass provider/model so should_compact uses the real
        # per-model threshold formula instead of the old flat reserve.
        if raw_turns and should_compact(
            raw_turns, context_window=_ctx_window, provider=_ws_provider or None, model=_ws_model or None,
        ):
            # BUG 2 fix: forced-floor fallback — a plain find_cut_point can
            # return 0 ("nothing to cut") for a short conversation even
            # though should_compact just said the turn is over threshold.
            # The old code silently reported SAGE_COMPACTED in that case
            # despite compact_turns() never having been called — exactly
            # the "returns ok while doing nothing" pattern that's
            # prohibited. Now honest: only claims success when a cut point
            # (forced or not) was actually found and compact_turns ran.
            cut_idx, forced = find_cut_point_with_fallback(raw_turns, context_window=_ctx_window)
            if cut_idx <= 0:
                _logger.warning(
                    "compact: /sage compact for workspace=%s found nothing "
                    "cuttable even with the forced floor (%d raw turns) — "
                    "reporting honestly instead of a false 'compacted'",
                    workspace_id, len(raw_turns),
                )
                return SAGE_COMPACT_NOT_NEEDED
            if forced:
                _logger.info(
                    "compact: /sage compact for workspace=%s used the forced "
                    "keep-recent floor (normal budget exceeded the entire "
                    "%d-turn history)",
                    workspace_id, len(raw_turns),
                )
            prev = await load_previous_summary(
                workspace_id=workspace_id,
                tenant_id=tenant_id,
                thread_id=thread_id,
            )
            summary = await compact_turns(
                turns=raw_turns[:cut_idx],
                workspace_id=workspace_id,
                tenant_id=tenant_id,
                thread_id=thread_id,
                previous_summary=prev,
                provider=_ws_provider or None,
                model=_ws_model or None,
            )
            if not summary:
                # compact_turns already logs/traces WHY (no provider, empty
                # summary, overflow) — don't compound it with a false claim.
                return SAGE_COMPACT_NOT_NEEDED
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
