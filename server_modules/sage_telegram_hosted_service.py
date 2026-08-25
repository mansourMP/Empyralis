from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx
import asyncio


LOGGER = logging.getLogger(__name__)

from server_modules.agent_command_dispatcher import SAGE_ERROR_REPLY  # noqa: E402
from server_modules import runtime_config as runtime_config

PAIRING_CODE_LENGTH = 6
PAIRING_TOKEN_BYTES = 24
TELEGRAM_API_BASE = "https://api.telegram.org"
_TELEGRAM_MAX_MESSAGE_LENGTH = 4096
_TYPING_REFRESH_SECONDS = 4.0  # re-send typing before Telegram's ~5s expiry


def _deepseek_shortcut_model() -> str:
    """The DeepSeek chat-completion model this file's Agent Machine shortcut
    calls. Never a hardcoded literal — this used to be "deepseek-chat",
    DeepSeek's own retired pre-v4 id (retired 2026-07-24, see
    provider_profiles.py's "deepseek" catalog entry), sent on every
    shortcut call. provider_profiles.py's default_model is the single
    source of truth; "deepseek-v4-flash" is the fallback only if that
    lookup itself fails."""
    try:
        from server_modules import provider_profiles

        return str(
            provider_profiles.provider_catalog_entry("deepseek").get("default_model") or ""
        ).strip() or "deepseek-v4-flash"
    except Exception:
        return "deepseek-v4-flash"

# ── Typing indicator task registry ──
# chat_id → asyncio.Task (running typing-refresh loop)
_TYPING_TASKS: Dict[str, Any] = {}

_SAGE_HOSTED_PAIRS: Dict[str, Dict[str, Any]] = {}  # chat_id → {workspace_id, paired_at}
_PENDING_PAIRING_CODES: Dict[str, str] = {}  # code → workspace_id
# 6-digit numeric pairing codes are brute-forceable, so they must not live
# forever. Track creation time in parallel (no persisted-shape change) and expire
# on read. Deep-link tokens are 192-bit and don't need this.
_PENDING_PAIRING_CODE_TIMES: Dict[str, float] = {}  # code → created epoch seconds
_PAIRING_CODE_TTL_SECONDS = int(
    os.getenv("EMPYRALIS_TELEGRAM_PAIRING_CODE_TTL_SECONDS", "600")
)


def _pairing_code_live_workspace(code: str) -> Optional[str]:
    """Workspace for a still-valid numeric pairing code, else None (purging it).

    A code with no recorded creation time (e.g. loaded from disk before TTL
    tracking) is stamped now — a live pending code survives one restart but still
    expires thereafter.
    """
    code = str(code).strip()
    workspace_id = _PENDING_PAIRING_CODES.get(code)
    if workspace_id is None:
        return None
    created = _PENDING_PAIRING_CODE_TIMES.get(code)
    now = time.time()
    if created is None:
        _PENDING_PAIRING_CODE_TIMES[code] = now
        return workspace_id
    if now - created > _PAIRING_CODE_TTL_SECONDS:
        _PENDING_PAIRING_CODES.pop(code, None)
        _PENDING_PAIRING_CODE_TIMES.pop(code, None)
        _persist_after_mutation()
        return None
    return workspace_id
_PENDING_DEEP_LINK_TOKENS: Dict[str, str] = {}  # token → workspace_id

# --- Persistence ---
import atexit as _atexit

from server_modules.state_paths import empyralis_state_home as _empyralis_state_home

# MAN-324-adjacent incident, 2026-08-14: this used to hardcode
# os.path.expanduser('~') and ignore EMPYRALIS_STATE_HOME entirely — the
# third credential/state leak route found that night. A throwaway stack
# with EMPYRALIS_STATE_HOME pointed at a fresh temp dir still silently
# loaded the FOUNDER's real hosted-Telegram pairing records from his home
# directory (confirmed live: the exact "loaded N pairs, M pending codes,
# K pending tokens from disk" log line this module emits below, observed
# against a disposable test stack). No token was set and no write occurred
# that run, so nothing leaked that time, but a run with a bot token
# configured, or one that reaches _save_state(), would read or mutate his
# real pairing state from a disposable process. _empyralis_state_home()
# is the same shared resolver state_paths.py already provides (env var
# first, ~/.empyralis/state only when EMPYRALIS_STATE_HOME is unset) —
# called at call time via the two _state_dir()/_state_file() functions
# below, not cached into a module-level constant at import time, so a test
# that sets EMPYRALIS_STATE_HOME via monkeypatch/env before calling in
# gets the isolated path even on a process that imported this module
# earlier under a different environment.
def _state_dir() -> str:
    return str(_empyralis_state_home())


def _state_file() -> str:
    return os.path.join(_state_dir(), 'sage_telegram_hosted_pairs.json')

def _load_state() -> None:
    try:
        with open(_state_file(), 'r') as f:
            data = __import__('json').load(f)
        if isinstance(data.get('pairs'), dict):
            _SAGE_HOSTED_PAIRS.update(data['pairs'])
        if isinstance(data.get('pending_codes'), dict):
            _PENDING_PAIRING_CODES.update(data['pending_codes'])
        if isinstance(data.get('pending_tokens'), dict):
            _PENDING_DEEP_LINK_TOKENS.update(data['pending_tokens'])
        LOGGER.info('Sage Telegram hosted: loaded %d pairs, %d pending codes, %d pending tokens from disk',
                     len(_SAGE_HOSTED_PAIRS), len(_PENDING_PAIRING_CODES), len(_PENDING_DEEP_LINK_TOKENS))
    except (FileNotFoundError, __import__('json').JSONDecodeError):
        pass

def _save_state() -> None:
    state_file = _state_file()
    os.makedirs(os.path.dirname(state_file), exist_ok=True)
    tmp = state_file + '.tmp'
    with open(tmp, 'w') as f:
        __import__('json').dump({
            'pairs': _SAGE_HOSTED_PAIRS,
            'pending_codes': _PENDING_PAIRING_CODES,
            'pending_tokens': _PENDING_DEEP_LINK_TOKENS,
        }, f)
    os.replace(tmp, state_file)

def _persist_after_mutation() -> None:
    try:
        _save_state()
    except Exception:
        pass

# Load persisted state on import
_load_state()


def _text(value: Any, fallback: str = "") -> str:
    token = str(value or "").strip()
    return token or fallback


def _bot_token() -> str:
    return _text(os.getenv("EMPYRALIS_TELEGRAM_HOSTED_BOT_TOKEN"))


def _webhook_secret() -> str:
    return _text(os.getenv("EMPYRALIS_TELEGRAM_HOSTED_WEBHOOK_SECRET"))


def is_webhook_secret_configured() -> bool:
    """True when the hosted webhook secret is set (inbound updates can be authenticated)."""
    return bool(_webhook_secret())


_CACHED_BOT_USERNAME: Optional[str] = None


def _bot_username() -> str:
    """Return the bot username. Uses env if set, else returns the last cached
    value (populated on first successful get_bot_info() call)."""
    env_value = _text(os.getenv("EMPYRALIS_TELEGRAM_HOSTED_BOT_USERNAME"))
    if env_value:
        return env_value
    return _CACHED_BOT_USERNAME or ""


def is_configured() -> bool:
    return bool(_bot_token())


# ── 401 circuit breaker (dead/revoked hosted bot token) ──
#
# Telegram returns HTTP 401 with {"ok": false, "error_code": 401} when the
# bot token has been revoked or is otherwise invalid. That is a *permanent*
# auth failure — unlike 429 (rate limit) or 5xx/network errors, which are
# transient and should keep retrying at the normal cadence. Without this
# breaker, the 2s background poll loop (_background_polling_loop) and the 4s
# typing-indicator loop (_typing_loop) would hammer Telegram's API forever
# against a dead token.
#
# On a 401 we suspend the hosted bot: the poll loop backs off from
# _BG_POLL_INTERVAL (2s) to _SUSPENDED_POLL_INTERVAL_SECONDS (5min), and the
# typing loop stops calling the API entirely while suspended. Each slow-
# cadence poll doubles as a reauth probe — one successful call clears the
# suspension automatically (no restart required once the token is fixed).
# State is in-memory/per-process by design: a process restart (e.g. after an
# operator rotates the env var) also clears it.
_SUSPENDED_POLL_INTERVAL_SECONDS = 300  # 5 min probe cadence while suspended


class TelegramUnauthorizedError(RuntimeError):
    """Raised when Telegram returns 401 — the bot token is invalid/revoked."""


_HOSTED_BOT_AUTH_STATE: Dict[str, Any] = {
    "suspended": False,
    "reason": None,
    "suspended_at": None,  # epoch seconds
    "consecutive_401s": 0,
}


def _trip_circuit_breaker(reason: str) -> None:
    """Mark the hosted bot suspended after a 401. Idempotent — safe to call
    on every subsequent 401 while already suspended (refreshes the reason)."""
    _HOSTED_BOT_AUTH_STATE["suspended"] = True
    _HOSTED_BOT_AUTH_STATE["reason"] = reason
    _HOSTED_BOT_AUTH_STATE["suspended_at"] = time.time()
    _HOSTED_BOT_AUTH_STATE["consecutive_401s"] = int(_HOSTED_BOT_AUTH_STATE.get("consecutive_401s") or 0) + 1
    LOGGER.error(
        "Sage Telegram hosted bot SUSPENDED (401 Unauthorized — token revoked/invalid): %s. "
        "Polling/typing back off to a %ss probe cadence; re-enter a valid "
        "EMPYRALIS_TELEGRAM_HOSTED_BOT_TOKEN to recover.",
        reason, _SUSPENDED_POLL_INTERVAL_SECONDS,
    )


def _clear_circuit_breaker() -> None:
    """Clear the suspended state after any call succeeds. No-op (cheap) when
    already clear."""
    if _HOSTED_BOT_AUTH_STATE.get("suspended"):
        LOGGER.info(
            "Sage Telegram hosted bot: token accepted again — clearing 401 "
            "suspension, resuming normal polling cadence."
        )
    _HOSTED_BOT_AUTH_STATE["suspended"] = False
    _HOSTED_BOT_AUTH_STATE["reason"] = None
    _HOSTED_BOT_AUTH_STATE["suspended_at"] = None
    _HOSTED_BOT_AUTH_STATE["consecutive_401s"] = 0


def hosted_bot_auth_status() -> Dict[str, Any]:
    """Public snapshot of the 401 circuit-breaker state, for surfacing to
    operators (e.g. GET /sage/telegram-hosted/info)."""
    return dict(_HOSTED_BOT_AUTH_STATE)


# --- Pairing ---

def generate_pairing_code(*, workspace_id: str) -> str:
    code = "".join(str(secrets.randbelow(10)) for _ in range(PAIRING_CODE_LENGTH))
    _PENDING_PAIRING_CODES[code] = workspace_id
    _PENDING_PAIRING_CODE_TIMES[code] = time.time()
    _persist_after_mutation()
    return code


def generate_deep_link_token(*, workspace_id: str) -> str:
    """Generate a secure token for one-click Telegram deep-link pairing."""
    token = secrets.token_urlsafe(PAIRING_TOKEN_BYTES)
    _PENDING_DEEP_LINK_TOKENS[token] = workspace_id
    _persist_after_mutation()
    return token


def build_deep_link(token: str) -> Optional[str]:
    """Build a t.me deep link. Returns None when the bot username is
    unknown — callers should either warm the cache with
    ensure_bot_username_cached() first, or omit the link from the response."""
    username = _bot_username()
    if not username:
        return None
    return f"https://t.me/{username}?start={token}"


def verify_and_pair(code: str, chat_id: str) -> Optional[str]:
    code = str(code).strip()
    # Numeric pairing codes expire — consume only if still live.
    if _pairing_code_live_workspace(code) is not None:
        workspace_id = _PENDING_PAIRING_CODES.pop(code, None)
        _PENDING_PAIRING_CODE_TIMES.pop(code, None)
    else:
        workspace_id = None
    if workspace_id is None:
        workspace_id = _PENDING_DEEP_LINK_TOKENS.pop(code, None)
    if workspace_id is None:
        return None
    _SAGE_HOSTED_PAIRS[str(chat_id)] = {
        "workspace_id": workspace_id,
        "paired_at": datetime.now(timezone.utc).isoformat(),
    }
    _persist_after_mutation()
    return workspace_id


def consume_pairing_code(code: str) -> Optional[str]:
    """Pop and return the workspace_id for *code* without creating a Telegram pair.

    Lets non-Telegram channels (Discord) consume the same pairing codes
    generated by ``generate_pairing_code()``.  The code is single-use:
    whichever channel claims it first wins.
    """
    code = str(code).strip()
    # Numeric pairing codes expire — consume only if still live.
    if _pairing_code_live_workspace(code) is not None:
        workspace_id = _PENDING_PAIRING_CODES.pop(code, None)
        _PENDING_PAIRING_CODE_TIMES.pop(code, None)
    else:
        workspace_id = None
    if workspace_id is None:
        workspace_id = _PENDING_DEEP_LINK_TOKENS.pop(code, None)
    if workspace_id is not None:
        _persist_after_mutation()
    return workspace_id


def get_workspace_for_chat(chat_id: str) -> Optional[str]:
    pair = _SAGE_HOSTED_PAIRS.get(str(chat_id))
    if pair is None:
        return None
    return _text(pair.get("workspace_id"))


def is_paired(chat_id: str) -> bool:
    return str(chat_id) in _SAGE_HOSTED_PAIRS

def is_workspace_paired(workspace_id: str) -> bool:
    """Check if any chat is paired to this workspace."""
    return any(p.get("workspace_id") == workspace_id for p in _SAGE_HOSTED_PAIRS.values())



def unpair_workspace(workspace_id: str) -> int:
    """Remove all pairings for a workspace. Returns count removed."""
    to_remove = [
        chat_id for chat_id, data in _SAGE_HOSTED_PAIRS.items()
        if data.get("workspace_id") == workspace_id
    ]
    for chat_id in to_remove:
        del _SAGE_HOSTED_PAIRS[chat_id]
    if to_remove:
        _persist_after_mutation()
    return len(to_remove)

def pairing_code_for_workspace(workspace_id: str) -> Optional[str]:
    for code, ws_id in list(_PENDING_PAIRING_CODES.items()):
        if ws_id == workspace_id:
            return code
    for token, ws_id in list(_PENDING_DEEP_LINK_TOKENS.items()):
        if ws_id == workspace_id:
            return token
    return None


# --- Telegram Bot API ---

async def _register_telegram_native_commands() -> None:
    """Register slash commands with Telegram via setMyCommands.

    Called once at startup after the bot token is confirmed available.
    Syncs the command registry to Telegram's native command menu so users
    see autocomplete suggestions when typing / in a bot chat.
    """
    token = _bot_token()
    if not token:
        return
    try:
        from server_modules.command_registry import list_for_scope
        # setMyCommands with no scope is the DEFAULT menu — the autocomplete
        # every stranger who DMs this bot sees. Owner-only commands
        # (/bash, /config, /mcp, /plugins, /debug) were being advertised
        # there. Execution was never at risk: command_registry.dispatch
        # independently refuses `access == "owner"` for a non-owner. But
        # offering a stranger a shell command and then refusing it is a
        # dead control in Telegram's own UI, and advertising /bash at all
        # is not something to launch with.
        #
        # The owner loses nothing they had: an owner-only command still
        # WORKS when typed, it simply is not suggested in a menu shared with
        # everyone. Telegram scopes a per-chat menu by chat_id, so a future
        # owner-scoped menu is additive — but it needs a paired owner chat
        # id, which does not exist yet at startup when this runs.
        commands = [
            {"command": cmd.name, "description": cmd.description[:100]}
            for cmd in list_for_scope("both")
            if not cmd.aliases  # only primary names
            and cmd.access != "owner"
        ]
        if not commands:
            return
        import httpx
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            resp = await client.post(
                f"{TELEGRAM_API_BASE}/bot{token}/setMyCommands",
                json={"commands": commands},
            )
            data = resp.json()
            if data.get("ok"):
                LOGGER.info("Telegram native commands registered: %d commands", len(commands))
            else:
                LOGGER.warning(
                    "Telegram setMyCommands failed: %s",
                    data.get("description", "unknown"),
                )
    except Exception:
        LOGGER.exception("Telegram native command registration failed")


async def _telegram_api(method: str, body: dict) -> dict:
    token = _bot_token()
    if not token:
        LOGGER.error("Telegram hosted bot token is not configured "
                     "(EMPYRALIS_TELEGRAM_HOSTED_BOT_TOKEN unset).")
        raise RuntimeError("The hosted Telegram bot isn't available on this deployment right now.")
    url = f"{TELEGRAM_API_BASE}/bot{token}/{method}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.post(url, json=body)
        try:
            data = resp.json()
        except ValueError:
            data = {}
        # 401 (Unauthorized) is Telegram's signal that the bot token itself
        # is invalid/revoked — a permanent auth failure, never fixed by
        # retrying. Distinguish it from transient 429/5xx/network errors
        # (those fall through to the `not data.get("ok")` warning below and
        # keep retrying at the normal cadence via the callers' own loops).
        if resp.status_code == 401 or data.get("error_code") == 401:
            reason = str(data.get("description") or f"HTTP {resp.status_code}").strip()
            _trip_circuit_breaker(reason)
            raise TelegramUnauthorizedError(reason)
        if not data.get("ok"):
            LOGGER.warning("Telegram API error: %s %s", method, data.get("description", "unknown"))
        else:
            _clear_circuit_breaker()
        return data


async def send_message(chat_id: str, text: str, *, reply_to_message_id: Optional[int] = None) -> dict:
    """Send a message via Telegram with MarkdownV2 formatting."""
    import re as _re
    import sys as _sys
    safe_text = str(text or '')
    safe_text = _re.sub(r'<[^>]*>', '', safe_text)
    safe_text = _re.sub(r'\n?memory_search\s*\n?query\s*=\s*"[^"]*"', '', safe_text, flags=_re.IGNORECASE)
    safe_text = _re.sub(r'\n?web__search\s*\n?query\s*=\s*"[^"]*"', '', safe_text, flags=_re.IGNORECASE)
    safe_text = _re.sub(r'\n?```[^`]*```', '', safe_text)
    safe_text = safe_text.strip()
    if not safe_text:
        safe_text = "[SILENT]"  # Suppressed — empty LLM output, agent handles naturally
    formatted_text = _to_telegram_markdown(safe_text)
    body: dict = {
        "chat_id": chat_id,
        "text": formatted_text,
        "parse_mode": "MarkdownV2",
    }
    if reply_to_message_id is not None:
        body["reply_to_message_id"] = reply_to_message_id
    try:
        result = await _telegram_api("sendMessage", body)
        return result
    except Exception as _exc:
        import traceback as _tb
        _tb.print_exc()
        raise


def _to_telegram_markdown(text: str) -> str:
    """Convert common markdown to Telegram MarkdownV2 format with proper escaping."""
    import re as _re
    if not text:
        return text
    # Special chars that need escaping in MarkdownV2 inline text.
    # Structural chars (# > | -) are NOT escaped — rich messages use them for headings, quotes, tables, lists.
    # $ is NOT escaped — used for inline math.
    # ! WAS missing — Telegram rejects any reply containing an unescaped '!'
    # with "can't parse entities", so parse_mode=MarkdownV2 silently failed
    # on ordinary punctuation and every send fell through to the plain-text
    # retry path (or failed outright if that retry also hit an error).
    _ESCAPE_CHARS = r'_*[]()~`{}.!'
    _BOLD_OPEN = '\x01'
    _BOLD_CLOSE = '\x02'
    _ITL_OPEN = '\x03'
    _ITL_CLOSE = '\x04'
    _STRK_OPEN = '\x05'
    _STRK_CLOSE = '\x06'
    _BLOCK_MARK = '\x07'
    
    # 1. Protect code blocks ```...```, inline `code`, and links [text](url)
    blocks = {}
    def _save(m):
        k = _BLOCK_MARK + str(len(blocks)) + _BLOCK_MARK
        blocks[k] = m.group(0)
        return k
    text = _re.sub(r'```[^`]+```', _save, text)
    text = _re.sub(r'`[^`]+`', _save, text)
    text = _re.sub(r'\[([^\]]+)\]\(([^)]+)\)', _save, text)
    
    # 2. Convert **bold** -> sentinel (avoids italic collision)
    text = _re.sub(r'\*\*(.+?)\*\*', _BOLD_OPEN + r'\1' + _BOLD_CLOSE, text)
    # 3. Convert *italic* -> sentinel
    text = _re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', _ITL_OPEN + r'\1' + _ITL_CLOSE, text)
    # 4. Convert ~~strikethrough~~ -> sentinel
    text = _re.sub(r'~~(.+?)~~', _STRK_OPEN + r'\1' + _STRK_CLOSE, text)
    
    # 5. Escape remaining special characters (skip sentinels and block placeholders)
    result = []
    i = 0
    while i < len(text):
        c = text[i]
        if c in '\x01\x02\x03\x04\x05\x06\x07':
            # Sentinel or block marker — look for matching close
            j = text.find(c, i+1)
            if j != -1 and j - i < 30:
                result.append(text[i:j+1])
                i = j + 1
                continue
        if c in _ESCAPE_CHARS:
            result.append('\\' + c)
        else:
            result.append(c)
        i += 1
    text = ''.join(result)
    
    # 6. Resolve sentinels to Telegram MarkdownV2
    text = text.replace(_BOLD_OPEN, '*')
    text = text.replace(_BOLD_CLOSE, '*')
    text = text.replace(_ITL_OPEN, '_')
    text = text.replace(_ITL_CLOSE, '_')
    text = text.replace(_STRK_OPEN, '~')
    text = text.replace(_STRK_CLOSE, '~')
    
    # 7. Restore code/links
    for key, value in blocks.items():
        text = text.replace(key, value)
    
    return text



async def send_chat_action(chat_id: str, action: str = "typing") -> dict:
    return await _telegram_api("sendChatAction", {"chat_id": chat_id, "action": action})


# ── Safe send (never raises) + message splitting ──

def _split_long_message(text: str, max_len: int = _TELEGRAM_MAX_MESSAGE_LENGTH) -> list[str]:
    """Split a message at paragraph/sentence boundaries to stay under Telegram's limit."""
    text = str(text or "").strip()
    if not text:
        return [text]
    if len(text) <= max_len:
        return [text]

    import re as _re
    chunks: list[str] = []
    remaining = text
    while len(remaining) > max_len:
        # Try to split at nearest paragraph break
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
            # Still no good break — hard cut at a space near the limit
            cut = remaining.rfind(' ', 0, max_len)
        if cut < max_len // 2:
            # No space at all — force cut at limit
            cut = max_len - 1

        chunks.append(remaining[:cut + 1].strip())
        remaining = remaining[cut + 1:].strip()

    if remaining:
        chunks.append(remaining)
    return chunks


async def send_message_safe(chat_id: str, text: str, *, reply_to_message_id: Optional[int] = None) -> bool:
    """Send a message — never raises. Returns True if at least one chunk was sent.

    Automatically splits long messages over Telegram's 4096-char limit.
    Tries MarkdownV2 first; falls back to plain text if parsing fails.
    Failures are logged but never propagated.
    """
    if not str(text or "").strip():
        return False
    chunks = _split_long_message(str(text))
    sent_any = False
    for chunk in chunks:
        success = await _send_chunk_markdown(chat_id, chunk, reply_to_message_id=reply_to_message_id)
        if not success:
            # MarkdownV2 parse error — fall back to plain text
            success = await _send_chunk_plain(chat_id, chunk, reply_to_message_id=reply_to_message_id)
        if success:
            sent_any = True
        reply_to_message_id = None  # only first chunk gets reply-to
    return sent_any


async def _send_chunk_markdown(chat_id: str, text: str, reply_to_message_id: Optional[int] = None) -> bool:
    """Send a single chunk with MarkdownV2. Returns True on success."""
    try:
        formatted = _to_telegram_markdown(text)
        result = await _telegram_api("sendMessage", {
            "chat_id": chat_id,
            "text": formatted,
            "parse_mode": "MarkdownV2",
            **( {
                "reply_to_message_id": reply_to_message_id
            } if reply_to_message_id is not None else {}),
        })
        return bool(result.get("ok"))
    except Exception as exc:
        LOGGER.warning("_send_chunk_markdown failed for chat_id=%s: %s", chat_id, exc)
        return False


async def _send_chunk_plain(chat_id: str, text: str, reply_to_message_id: Optional[int] = None) -> bool:
    """Send a single chunk as plain text (no parse_mode). Returns True on success."""
    try:
        body: dict = {"chat_id": chat_id, "text": text[:4096]}
        if reply_to_message_id is not None:
            body["reply_to_message_id"] = reply_to_message_id
        result = await _telegram_api("sendMessage", body)
        return bool(result.get("ok"))
    except Exception as exc:
        LOGGER.warning("_send_chunk_plain failed for chat_id=%s: %s", chat_id, exc)
        return False


async def send_sage_reply(chat_id: str, text: str, *, reply_to_message_id: Optional[int] = None) -> dict:
    """Send a Sage reply — splits long messages, never raises on send failure.

    Returns the result of the first chunk (for API compatibility).
    If all chunks fail, returns a dict with ok=False.
    """
    if not str(text or "").strip():
        return {"ok": False, "description": "empty reply suppressed"}
    chunks = _split_long_message(str(text))
    first_result: dict = {"ok": False, "description": "no chunks sent"}
    reply_to = reply_to_message_id
    for i, chunk in enumerate(chunks):
        try:
            result = await send_message(chat_id, chunk, reply_to_message_id=reply_to)
            if i == 0:
                first_result = result
            reply_to = None  # only first chunk gets reply-to
        except Exception as exc:
            LOGGER.warning(
                "send_sage_reply: chunk %d/%d failed for chat_id=%s: %s",
                i + 1, len(chunks), chat_id, exc,
            )
            if i == 0:
                first_result = {"ok": False, "description": str(exc)[:200]}
    return first_result


# ── Typing indicator management ──

async def _typing_loop(chat_id: str) -> None:
    """Re-send typing action every _TYPING_REFRESH_SECONDS until cancelled."""
    while True:
        if _HOSTED_BOT_AUTH_STATE.get("suspended"):
            # Token is dead (401) — don't hammer sendChatAction every 4s.
            # The background poller's slow-cadence probe will clear the
            # breaker once the token is fixed; wait for that cadence here too.
            await asyncio.sleep(_SUSPENDED_POLL_INTERVAL_SECONDS)
            continue
        try:
            await send_chat_action(chat_id, "typing")
        except Exception:
            pass  # typing is best-effort
        await asyncio.sleep(_TYPING_REFRESH_SECONDS)


def start_typing(chat_id: str) -> None:
    """Start a background typing indicator for chat_id. Idempotent."""
    key = str(chat_id)
    existing = _TYPING_TASKS.get(key)
    if existing is not None and not existing.done():
        return  # already typing
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        return
    _TYPING_TASKS[key] = loop.create_task(_typing_loop(key))


async def stop_typing(chat_id: str) -> None:
    """Cancel the typing indicator for chat_id."""
    key = str(chat_id)
    task = _TYPING_TASKS.pop(key, None)
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ── ChannelTransport implementation for Telegram Hosted bot ──

from server_modules.channel_transport import ChannelTransport  # noqa: E402


class TelegramHostedTransport(ChannelTransport):
    """Thin transport over the Telegram Bot API.

    Implements the ChannelTransport contract so the shared-core dispatcher
    owns all reliability logic.  The transport only handles the Bot-API
    primitives: send a chunk, typing start/stop, MarkdownV2 formatting.
    """

    max_message_length: int = _TELEGRAM_MAX_MESSAGE_LENGTH  # 4096
    supports_typing_indicator: bool = True

    def __init__(self, chat_id: str) -> None:
        self.chat_id = str(chat_id)

    # ── Required primitive ──

    async def send_message(
        self,
        text: str,
        *,
        reply_to_id: Optional[str] = None,
    ) -> bool:
        """Send a SINGLE pre-split chunk.  Never raises.

        Tries MarkdownV2 first; falls back to plain text on parse error.
        """
        if not str(text or "").strip():
            return False

        reply_to = int(reply_to_id) if reply_to_id is not None else None
        formatted = self.format_text(text)
        body: dict = {
            "chat_id": self.chat_id,
            "text": formatted,
            "parse_mode": "MarkdownV2",
        }
        if reply_to is not None:
            body["reply_to_message_id"] = reply_to

        try:
            result = await _telegram_api("sendMessage", body)
            if result.get("ok"):
                return True
        except Exception:
            pass

        # Fallback: plain text (no parse_mode)
        try:
            body.pop("parse_mode", None)
            body["text"] = text[: self.max_message_length]
            result = await _telegram_api("sendMessage", body)
            return bool(result.get("ok"))
        except Exception as exc:
            LOGGER.warning(
                "TelegramHostedTransport.send_message failed for chat_id=%s: %s",
                self.chat_id, exc,
            )
            return False

    # ── Typing indicator primitives ──

    async def start_typing(self) -> None:
        """Idempotent — delegates to module-level start_typing."""
        start_typing(self.chat_id)

    async def stop_typing(self) -> None:
        """Cancel typing — delegates to module-level stop_typing."""
        await stop_typing(self.chat_id)

    # ── Markdown conversion ──

    def format_text(self, text: str) -> str:
        """Convert common markdown to Telegram MarkdownV2."""
        return _to_telegram_markdown(text)


async def set_webhook(*, base_url: str) -> dict:
    secret = _webhook_secret()
    webhook_url = f"{base_url.rstrip('/')}/api/sage/telegram-hosted/webhook"
    body: dict = {"url": webhook_url}
    if secret:
        body["secret_token"] = secret
    return await _telegram_api("setWebhook", body)


async def get_webhook_info() -> dict:
    return await _telegram_api("getWebhookInfo", {})


async def get_bot_info() -> dict:
    """Fetch bot identity via getMe. Caches the resolved username so
    build_deep_link() works even when EMPYRALIS_TELEGRAM_HOSTED_BOT_USERNAME
    is not set — the token alone is enough."""
    global _CACHED_BOT_USERNAME
    result = await _telegram_api("getMe", {})
    try:
        payload = result.get("result", {}) if isinstance(result.get("result"), dict) else {}
        username = str(payload.get("username") or "").strip()
        if username:
            _CACHED_BOT_USERNAME = username
    except Exception:
        pass
    return result


async def ensure_bot_username_cached() -> str:
    """Ensure the bot username is populated in the cache. Safe to call
    repeatedly — hits the Bot API at most once per process, or per lookup
    failure. Returns the resolved username, or empty string if unavailable."""
    if _CACHED_BOT_USERNAME:
        return _CACHED_BOT_USERNAME
    env_value = _text(os.getenv("EMPYRALIS_TELEGRAM_HOSTED_BOT_USERNAME"))
    if env_value:
        return env_value
    try:
        await get_bot_info()
    except Exception:
        return ""
    return _CACHED_BOT_USERNAME or ""


# --- Webhook handling ---

def verify_webhook_signature(header_signature: str, body_bytes: bytes) -> bool:
    """Telegram's `secret_token` webhook auth is a SHARED SECRET, never an
    HMAC — per Telegram's own Bot API docs, whatever string you pass as
    `secret_token` to `setWebhook` is echoed back VERBATIM in the
    `X-Telegram-Bot-Api-Secret-Token` header on every subsequent update, and
    the receiver's whole job is a constant-time equality check against that
    same string. There is no body-signing scheme here at all — that is the
    GitHub-webhook (`X-Hub-Signature`) pattern, a different mechanism this
    function was previously confusing it with.

    Found 2026-08-19 tracing a live 403 on every real inbound message: the
    prior implementation computed `hmac.new(secret, body_bytes,
    sha256).hexdigest()` and compared THAT to the header — a value Telegram
    never sends and had no way to produce, so this check could not pass for
    ANY correctly-configured secret, on any message, ever. `body_bytes` is
    no longer read at all; kept as a parameter so the call site (which
    reads the raw body before parsing JSON, for exactly this check) needs
    no change.
    """
    secret = _webhook_secret()
    if not secret:
        # Fail closed: without a configured secret the webhook cannot be
        # authenticated, so any request could be forged — reject it. (The route
        # also returns 503 when the secret is unset; this is defense-in-depth so
        # verification never passes blind even if a caller skips that check.)
        return False
    if not header_signature:
        return False
    return hmac.compare_digest(header_signature.encode("utf-8"), secret.encode("utf-8"))


def parse_telegram_update(body: dict) -> Optional[Dict[str, Any]]:
    message = body.get("message") or body.get("edited_message")
    if not isinstance(message, dict):
        return None
    chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
    text = _text(message.get("text") or message.get("caption"))
    has_photo = bool(message.get("photo"))
    has_document = bool(message.get("document"))
    has_voice = bool(message.get("voice"))
    media_files: list[dict] = []
    if has_photo:
        photos = message.get("photo") or []
        if isinstance(photos, list) and photos:
            largest = max(photos, key=lambda p: (p.get("width", 0) or 0) * (p.get("height", 0) or 0))
            media_files.append({"type": "image", "file_id": str(largest.get("file_id", "")), "mime": "image/jpeg"})
        if not text:
            text = "[📷 Photo]"
    if has_document:
        doc = message.get("document") or {}
        if isinstance(doc, dict):
            media_files.append({
                "type": "document",
                "file_id": str(doc.get("file_id", "")),
                "mime": str(doc.get("mime_type", "application/octet-stream")),
                "filename": str(doc.get("file_name", "file")),
            })
        if not text:
            text = "[📄 Document]"
    if has_voice:
        voice = message.get("voice") or {}
        if isinstance(voice, dict):
            media_files.append({"type": "audio", "file_id": str(voice.get("file_id", "")), "mime": "audio/ogg"})
        if not text:
            text = "[🎤 Voice message]"
    if not text and not media_files:
        return None
    reply_to_message = message.get("reply_to_message") if isinstance(message.get("reply_to_message"), dict) else {}
    reply_to_from = reply_to_message.get("from") if isinstance(reply_to_message.get("from"), dict) else {}
    entities = message.get("entities") if isinstance(message.get("entities"), list) else []
    return {
        "message_id": message.get("message_id"),
        "chat_id": str(chat.get("id", "")),
        "chat_type": _text(chat.get("type")),
        "text": text,
        "from_id": str((message.get("from") or {}).get("id", "")),
        "from_username": _text((message.get("from") or {}).get("username")),
        "from_first_name": _text((message.get("from") or {}).get("first_name")),
        "date": message.get("date"),
        "media": media_files,
        # Group-addressing signals (used by is_message_addressed_to_bot):
        # a real Bot-API `entities` mention naming this bot, or a direct
        # reply to a message THIS bot sent.
        "entities": entities,
        "reply_to_from_id": str(reply_to_from.get("id", "")) if reply_to_from else "",
    }


def _bot_own_id() -> str:
    """The hosted bot's own numeric Telegram user id — a stored identity
    (env var, matching the pattern _bot_username() already uses), not a
    live getMe() lookup. Same default already relied on by the echo-loop
    check in _process_update."""
    return str(os.getenv("SAGE_TELEGRAM_HOSTED_BOT_USER_ID", "8870032163")).strip()


_GROUP_ADDRESSING_CHAT_TYPES = {"group", "supergroup"}


def text_addresses_bot(*, text: str, entities: Any, reply_to_from_id: str, bot_id: str, bot_username: str) -> bool:
    """Core, bot-identity-agnostic addressing check: True when `entities`
    contains a real Bot-API mention naming `bot_username`/`bot_id` (a
    `mention` entity resolved against the raw text, or a `text_mention`
    entity carrying the id directly), or `reply_to_from_id` equals `bot_id`
    (a direct reply to a message that bot itself sent).

    There is no Telegram Bot API equivalent of a client-side "mentioned"
    flag to lean on (unlike the gateway's full-account GramJS session, which
    had — and had to stop trusting — rawMessage.mentioned; see
    hasExplicitTelegramMention in empyralis-gateway/.../telegram/runtime.ts).
    A bot only ever sees explicit entities and reply_to_message, so those
    are the only two signals this checks.

    Shared by is_message_addressed_to_bot (the single hosted "Sage on
    Telegram" bot, identity from env/cache) and
    hosted_bot_provisioning_service's per-agent BYO bots (identity resolved
    per bot_token via getMe, since a BYO bot's numeric id is never
    persisted — only its bot_username is, at provisioning time) — every
    Telegram bot identity this platform runs needs the exact same
    entities/reply_to matching, just against a different bot_id/bot_username
    pair. Callers are responsible for only invoking this for group/
    supergroup chats — a private DM is always implicitly addressed and must
    not be routed through this function.
    """
    bot_id = str(bot_id or "").strip()
    reply_to_from_id = str(reply_to_from_id or "").strip()
    if bot_id and reply_to_from_id and reply_to_from_id == bot_id:
        return True

    bot_username = str(bot_username or "").strip().lstrip("@").lower()
    entity_list = entities if isinstance(entities, list) else []
    if not entity_list:
        return False
    text = str(text or "")
    for entity in entity_list:
        if not isinstance(entity, dict):
            continue
        entity_type = str(entity.get("type") or "").strip().lower()
        if entity_type == "text_mention":
            mentioned_user = entity.get("user") if isinstance(entity.get("user"), dict) else {}
            if bot_id and str(mentioned_user.get("id") or "").strip() == bot_id:
                return True
            continue
        if entity_type != "mention" or not bot_username:
            continue
        try:
            offset = int(entity.get("offset") or 0)
            length = int(entity.get("length") or 0)
        except (TypeError, ValueError):
            continue
        if offset < 0 or length <= 0 or offset + length > len(text):
            continue
        slice_text = text[offset : offset + length].strip().lstrip("@").lower()
        if slice_text == bot_username:
            return True
    return False


def is_message_addressed_to_bot(parsed: dict) -> bool:
    """True when a group/supergroup message explicitly addresses the single
    shared hosted "Sage on Telegram" bot — see text_addresses_bot's doc for
    the actual matching rules. Callers are responsible for only invoking
    this for group/supergroup chats — a private DM is always implicitly
    addressed and must not be routed through this function.
    """
    return text_addresses_bot(
        text=str(parsed.get("text") or ""),
        entities=parsed.get("entities"),
        reply_to_from_id=str(parsed.get("reply_to_from_id") or ""),
        bot_id=_bot_own_id(),
        bot_username=_bot_username(),
    )


async def handle_inbound_message(parsed: dict) -> Optional[str]:
    chat_id = parsed["chat_id"]
    text = parsed["text"]
    message_id = parsed.get("message_id")

    if not is_paired(chat_id):
        # Pairing (and even the "how to pair" reply) is private-chat only.
        # Without this check, ANYONE typing "/start <token>" inside a group
        # the bot had been added to permanently paired the WHOLE group to
        # that workspace — every member's message then reached this
        # function already "paired" (see is_message_addressed_to_bot below
        # for the follow-on group-mention gate that still applies once
        # paired). And short of a successful pairing, an unpaired group got
        # the "Welcome to Empyralis... pairing code" reply on EVERY single
        # message, since this branch used to reply unconditionally either
        # way — a standing spam source in any group/channel the bot was
        # merely added to, paired or not.
        if str(parsed.get("chat_type") or "").strip().lower() != "private":
            return None

        # Handle /start with deep-link token (e.g., "/start abc123...")
        pairing_input = text.strip()
        if pairing_input.startswith("/start"):
            parts = pairing_input.split(None, 1)
            pairing_input = parts[1] if len(parts) > 1 else ""

        workspace_id = verify_and_pair(pairing_input, chat_id)
        if workspace_id:
            await send_message(
                chat_id,
                "✅ You're now connected to Empyralis!\n\n"
                "Send me any message and I'll route it to your agent.",
                reply_to_message_id=message_id,
            )
            return None
        else:
            bot_username = _bot_username()
            bot_mention = f"@{bot_username}" if bot_username else "this bot"
            await send_message(
                chat_id,
                f"👋 Welcome to Empyralis on Telegram!\n\n"
                f"To connect your account:\n"
                f"1. Open Empyralis → Connections → Telegram → \"Talk to your agent on Telegram\"\n"
                f"2. Click the link shown there to pair instantly\n"
                f"3. Or send the 6-digit pairing code to {bot_mention}",
                reply_to_message_id=message_id,
            )
            return None

    return chat_id


async def send_sage_reply(chat_id: str, text: str, *, reply_to_message_id: Optional[int] = None) -> dict:
    return await send_message(chat_id, text, reply_to_message_id=reply_to_message_id)



async def send_photo(chat_id: str, photo: str, *, caption: str | None = None, reply_to_message_id: int | None = None) -> dict:
    """Send a photo to a Telegram chat.

    photo can be:
    - A file_id string (already uploaded to Telegram)
    - A URL string (Telegram will download it)
    - A file:// URL — reads the local file and uploads as multipart
    - A local filesystem path — reads and uploads as multipart
    """
    import pathlib as _pl
    token = _bot_token()
    if not token:
        LOGGER.error("Telegram hosted bot token is not configured "
                     "(EMPYRALIS_TELEGRAM_HOSTED_BOT_TOKEN unset).")
        raise RuntimeError("The hosted Telegram bot isn't available on this deployment right now.")
    # Resolve file:// URLs and local paths to actual file bytes
    file_bytes: bytes | None = None
    filename: str | None = None
    resolved_path: _pl.Path | None = None
    if photo.startswith("file://"):
        resolved_path = _pl.Path(photo[7:]).expanduser().resolve()
    elif not photo.startswith("http://") and not photo.startswith("https://") and _pl.Path(photo).expanduser().exists():
        resolved_path = _pl.Path(photo).expanduser().resolve()
    if resolved_path is not None and resolved_path.is_file():
        file_bytes = resolved_path.read_bytes()
        filename = resolved_path.name
    url = f"{TELEGRAM_API_BASE}/bot{token}/sendPhoto"
    if file_bytes:
        # Send as multipart/form-data with file bytes
        data: dict = {"chat_id": chat_id}
        if caption:
            data["caption"] = _to_telegram_markdown(str(caption))
        if reply_to_message_id is not None:
            data["reply_to_message_id"] = str(reply_to_message_id)
        import io as _io
        files = {"photo": (filename or "photo.png", _io.BytesIO(file_bytes), "image/png")}
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            resp = await client.post(url, data=data, files=files)
            result = resp.json()
            if not result.get("ok"):
                LOGGER.warning("Telegram API error: sendPhoto %s", result.get("description", "unknown"))
            return result
    body: dict = {"chat_id": chat_id, "photo": photo, "parse_mode": "MarkdownV2"}
    if caption:
        body["caption"] = _to_telegram_markdown(str(caption))
    if reply_to_message_id is not None:
        body["reply_to_message_id"] = reply_to_message_id
    return await _telegram_api("sendPhoto", body)


async def send_document(chat_id: str, document: str, *, caption: str | None = None, filename: str | None = None, reply_to_message_id: int | None = None) -> dict:
    """Send a document/file to a Telegram chat.

    document can be:
    - A file_id string (already uploaded to Telegram)
    - A URL string (Telegram will download it)
    - A file:// URL — reads the local file and uploads as multipart
    - A local filesystem path — reads and uploads as multipart
    """
    import pathlib as _pl
    token = _bot_token()
    if not token:
        LOGGER.error("Telegram hosted bot token is not configured "
                     "(EMPYRALIS_TELEGRAM_HOSTED_BOT_TOKEN unset).")
        raise RuntimeError("The hosted Telegram bot isn't available on this deployment right now.")
    # Resolve file:// URLs and local paths to actual file bytes
    file_bytes: bytes | None = None
    resolved_path: _pl.Path | None = None
    if document.startswith("file://"):
        resolved_path = _pl.Path(document[7:]).expanduser().resolve()
    elif not document.startswith("http://") and not document.startswith("https://") and _pl.Path(document).expanduser().exists():
        resolved_path = _pl.Path(document).expanduser().resolve()
    if resolved_path is not None and resolved_path.is_file():
        file_bytes = resolved_path.read_bytes()
        filename = filename or resolved_path.name
    url = f"{TELEGRAM_API_BASE}/bot{token}/sendDocument"
    if file_bytes:
        # Send as multipart/form-data with file bytes
        data: dict = {"chat_id": chat_id}
        if caption:
            data["caption"] = _to_telegram_markdown(str(caption))
        if reply_to_message_id is not None:
            data["reply_to_message_id"] = str(reply_to_message_id)
        import io as _io
        mime = "application/octet-stream"
        if filename and filename.endswith(".pdf"):
            mime = "application/pdf"
        elif filename and filename.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
            mime = "image/" + filename.rsplit(".", 1)[-1]
        files = {"document": (filename or "document", _io.BytesIO(file_bytes), mime)}
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            resp = await client.post(url, data=data, files=files)
            result = resp.json()
            if not result.get("ok"):
                LOGGER.warning("Telegram API error: sendDocument %s", result.get("description", "unknown"))
            return result
    body: dict = {"chat_id": chat_id, "document": document, "parse_mode": "MarkdownV2"}
    if caption:
        body["caption"] = _to_telegram_markdown(str(caption))
    if filename:
        body["caption"] = (body.get("caption", "") + f"\n_{filename}_").strip()
    if reply_to_message_id is not None:
        body["reply_to_message_id"] = reply_to_message_id
    return await _telegram_api("sendDocument", body)


async def get_file(file_id: str) -> dict:
    """Get file info from Telegram. Returns {"file_path": str, ...} or empty dict."""
    result = await _telegram_api("getFile", {"file_id": file_id})
    if result.get("ok") and isinstance(result.get("result"), dict):
        return dict(result["result"])
    return {}


def build_file_url(file_path: str) -> str:
    """Build a download URL for a Telegram file."""
    token = str(os.environ.get("EMPYRALIS_TELEGRAM_HOSTED_BOT_TOKEN", "")).strip()
    return f"https://api.telegram.org/file/bot{token}/{file_path}"


async def register_webhook_if_configured(*, base_url: str) -> bool:
    if not is_configured():
        LOGGER.info("Sage Telegram hosted bot: not configured (no EMPYRALIS_TELEGRAM_HOSTED_BOT_TOKEN)")
        return False
    try:
        result = await set_webhook(base_url=base_url)
        if result.get("ok"):
            LOGGER.info("Sage Telegram hosted bot: webhook registered successfully")
            return True
        else:
            LOGGER.warning("Sage Telegram hosted bot: webhook registration failed: %s", result.get("description"))
            return False
    except Exception as exc:
        LOGGER.warning("Sage Telegram hosted bot: webhook registration error: %s", exc)
        return False


async def unregister_webhook() -> dict:
    return await _telegram_api("deleteWebhook", {"drop_pending_updates": False})

# --- Polling fallback for local dev (when no public webhook URL) ---

async def poll_updates(*, limit: int = 10, timeout: int = 30, offset: Optional[int] = None) -> list[dict]:
    """Poll for updates. Used as fallback when webhook can't reach localhost."""
    body: dict = {"limit": limit, "timeout": timeout}
    if offset is not None:
        body["offset"] = offset
    result = await _telegram_api("getUpdates", body)
    if not result.get("ok"):
        return []
    updates = result.get("result", [])
    return [dict(u) for u in updates] if isinstance(updates, list) else []

# --- Background polling (keeps processing Telegram messages after pairing) ---

_last_update_id: int = 0
_polling_task: Optional[Any] = None
_BG_POLL_INTERVAL = 2  # seconds


_SILENT_REPLY_MARKER = "[SILENT]"

def _should_skip_reply(reply: str) -> bool:
    """Return True if the agent explicitly chose not to send a visible reply."""
    if not reply:
        return True
    stripped = reply.strip()
    if stripped == _SILENT_REPLY_MARKER:
        return True
    if stripped.startswith(_SILENT_REPLY_MARKER):
        return True
    return False



async def _run_agent_machine_shortcut(
    *,
    workspace_id: str,
    message: str,
    chat_id: str,
    parsed: dict,
) -> dict | None:
    """Simple agentic loop for local dev — bypasses the full runtime stack.

    LLM call → tool call → execute locally → feed result back → repeat.
    Gated on AGENT_MACHINE_MODE == "agent".
    Screenshots are routed through empyralis-supervisor (port 7788).
    Generated images are always sent to Telegram via sendPhoto.
    """
    LOGGER.info("Agent machine shortcut: ENTERED for message=%s", str(message)[:80])
    import json as _json
    import os as _os

    _deepseek_key = _os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not _deepseek_key:
        LOGGER.warning("Agent machine shortcut: DEEPSEEK_API_KEY not set, falling through to full runtime")
        return None

    try:
        from openai import AsyncOpenAI
    except ImportError:
        LOGGER.warning("Agent machine shortcut: openai not installed, falling through")
        return None

    from server_modules.local_tool_executor import shell_execute, filesystem_read, filesystem_write
    # ARCHIVED (Phase U1): supervisor_client capture_screenshot import removed.
    # The Rust empyralis-supervisor daemon is no longer part of the Empyralis product.

    client = AsyncOpenAI(
        api_key=_deepseek_key,
        base_url="https://api.deepseek.com",
    )
    LOGGER.info("Agent machine shortcut: DeepSeek client created, starting loop")

    thread_id = f"sage-main-{workspace_id}"

    # ── Load prior turns from thread_service ──
    prior_messages: list[dict[str, str]] = []
    try:
        from server_modules import thread_service as _ts
        print(f"[SHORTCUT_LOAD] Loading thread_id={thread_id} ws={workspace_id}", flush=True)
        await _ts.ensure_master_thread(
            thread_id=thread_id,
            tenant_id=workspace_id,
            workspace_id=workspace_id,
            owner_user_id="sage",
            channel="telegram_hosted",
        )
        thread_record = await _ts.get_thread(
            thread_id,
            tenant_id=workspace_id,
            workspace_id=workspace_id,
            include_turns=True,
        )
        raw_turns = list((thread_record or {}).get("turns") or [])
        prior_messages = [
            {"role": str(t.get("role", "")).strip().lower(),
             "content": str(t.get("content", "")).strip()}
            for t in raw_turns
            if str(t.get("role", "")).strip().lower() in {"user", "assistant"}
            and str(t.get("content", "")).strip()
        ][-16:]  # last 16 turns
        print(f"[SHORTCUT_LOAD] Loaded {len(prior_messages)} prior turns", flush=True)
        LOGGER.info("Shortcut: loaded %d prior turns for thread %s", len(prior_messages), thread_id)
    except Exception as _e:
        print(f"[SHORTCUT_LOAD] FAILED: {type(_e).__name__}: {_e}", flush=True)
        LOGGER.warning("Shortcut: failed to load thread history: %s", _e)

    async def _save_turn_and_return(reply_text: str) -> dict:
        """Persist user + assistant turns, then return the result dict."""
        try:
            from server_modules import thread_service as _ts
            print(f"[SHORTCUT_SAVE] Saving turn to thread_id={thread_id} ws={workspace_id}", flush=True)
            await _ts.ensure_master_thread(
                thread_id=thread_id, tenant_id=workspace_id,
                workspace_id=workspace_id, owner_user_id="sage", channel="telegram_hosted",
            )
            await _ts.record_user_turn(
                thread_id=thread_id,
                tenant_id=workspace_id,
                workspace_id=workspace_id,
                session_id=None,
                actor={"role": "user"},
                content=message,
            )
            await _ts.record_assistant_turn(
                thread_id=thread_id,
                tenant_id=workspace_id,
                workspace_id=workspace_id,
                session_id=None,
                actor={"role": "assistant"},
                reply=reply_text,
                status="completed",
            )
            print(f"[SHORTCUT_SAVE] DONE", flush=True)
        except Exception as _e:
            print(f"[SHORTCUT_SAVE] FAILED: {type(_e).__name__}: {_e}", flush=True)
            import traceback; traceback.print_exc()
            LOGGER.warning("Shortcut: failed to save thread turn: %s", _e)
        return {"message": reply_text}

    tools = [
        {
            "type": "function",
            "function": {
                "name": "shell_exec",
                "description": "Execute a shell command on the local machine. Returns stdout, stderr, and exit_code.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "The shell command to execute"}
                    },
                    "required": ["command"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "file_read",
                "description": "Read the contents of a file from the local filesystem.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Absolute path to the file"}
                    },
                    "required": ["path"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "file_write",
                "description": "Write content to a file on the local filesystem.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Absolute path to the file"},
                        "content": {"type": "string", "description": "Content to write"}
                    },
                    "required": ["path", "content"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "screenshot",
                "description": "Take a screenshot of the current screen. The image is always sent to the chat — no need to describe it. Call this, then give a short caption.",
                "parameters": {"type": "object", "properties": {}}
            }
        },
    ]

    sender_name = str(parsed.get("from_first_name", "")).strip()
    system_prompt = (
        f"You are the user's personal AI assistant running locally on their machine. "
        f"You are chatting with {sender_name or 'the user'} via Telegram. "
        f"You have direct shell, file, and screenshot access — no approval needed. "
        f"Execute commands immediately when asked. Keep replies concise and conversational. "
        f"Telegram markdown is supported (**bold**, *italic*, `code`, etc). "
        f"IMPORTANT: When you call screenshot(), the image is always sent to the chat automatically. "
        f"Just give a short caption about what you see — do NOT describe the screenshot contents in detail."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        *prior_messages,
        {"role": "user", "content": message},
    ]

    screenshot_paths: list[str] = []
    max_turns = 5

    print(f"[SHORTCUT_SYSTEM_PROMPT] {system_prompt[:2000]}", flush=True)
    print(f"[SHORTCUT_MESSAGES] len={len(messages)} prior_turns={len(prior_messages)} last3={messages[-3:] if len(messages)>=3 else messages}", flush=True)

    for _turn in range(max_turns):
        try:
            response = await client.chat.completions.create(
                model=_deepseek_shortcut_model(),
                messages=messages,
                tools=tools,
                temperature=0.7,
                max_tokens=2000,
            )
        except Exception as exc:
            LOGGER.warning("Agent machine shortcut: LLM call failed: %s", exc)
            return None

        msg = response.choices[0].message

        # If the LLM produced text and no tool calls → done
        if msg.content and not msg.tool_calls:
            # Send any accumulated screenshots
            for sp in screenshot_paths:
                try:
                    await send_photo(chat_id, sp, caption=None)
                except Exception as _pexc:
                    LOGGER.warning("Agent machine shortcut: send_photo failed: %s", _pexc)
            return await _save_turn_and_return(msg.content)

        # If tool calls, execute them
        if msg.tool_calls:
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments}
                    }
                    for tc in msg.tool_calls
                ]
            })
            for tc in msg.tool_calls:
                args = _json.loads(tc.function.arguments or "{}")
                tool_name = tc.function.name
                try:
                    if tool_name == "shell_exec":
                        result = shell_execute(str(args.get("command", "")))
                    elif tool_name == "file_read":
                        result = filesystem_read(str(args.get("path", "")))
                    elif tool_name == "file_write":
                        result = filesystem_write(
                            str(args.get("path", "")),
                            str(args.get("content", ""))
                        )
                    elif tool_name == "screenshot":
                        # ARCHIVED (Phase U1): supervisor screenshot capability removed.
                        # Desktop control (screenshot/mouse/keyboard/screen) is OUT of scope.
                        result = {"error": "Screenshot is not available. Desktop control has been removed from the Empyralis product (Phase U1)."}
                    else:
                        result = {"error": f"Unknown tool: {tool_name}"}
                except Exception as exc:
                    result = {"error": str(exc)}

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": _json.dumps(result, ensure_ascii=False)[:8000]
                })
            continue

        # No content and no tool calls — shouldn't happen, but handle it
        return await _save_turn_and_return(msg.content or "The request was processed.")

    # Max turns exceeded — get final response
    for sp in screenshot_paths:
        try:
            await send_photo(chat_id, sp, caption=None)
        except Exception:
            pass
    try:
        response = await client.chat.completions.create(
            model=_deepseek_shortcut_model(),
            messages=messages + [{"role": "user", "content": "Please summarize what you did in one concise reply."}],
            max_tokens=500,
        )
        return await _save_turn_and_return(response.choices[0].message.content or "Done.")
    except Exception:
        return await _save_turn_and_return("Several tools were executed. Additional instructions can be provided if needed.")

async def _process_update(update: dict) -> bool:
    """Process a single Telegram update. Returns True if a Sage reply was sent.

    GUARANTEED RESPONSE: every inbound message path ends with at least one
    sent message — an answer, an honest error, or a plain fallback. Never silence.
    All reliability logic is owned by the shared-core agent_reply_dispatcher.
    """
    parsed = parse_telegram_update(update)
    if parsed is None:
        return False
    # Skip messages from the bot itself to prevent echo loops.
    _bot_own_id = str(os.getenv("SAGE_TELEGRAM_HOSTED_BOT_USER_ID", "8870032163")).strip()
    from_id = str(parsed.get("from_id", "")).strip()
    LOGGER.info("Sage Telegram hosted: from_id=%s bot_id=%s match=%s text=%s",
                from_id, _bot_own_id, from_id == _bot_own_id,
                (parsed.get("text") or "")[:60])
    if _bot_own_id and from_id == _bot_own_id:
        LOGGER.info("Sage Telegram hosted: skipping own message (fromMe)")
        return False
    chat_id = await handle_inbound_message(parsed)
    if chat_id is None:
        return False
    # Group gate: a group/supergroup chat can only reach this point already
    # paired (handle_inbound_message's private-chat-only pairing check
    # blocks any NEW group pairing) — but legacy state from before that fix
    # can still hold an old group pairing, and this is the defense-in-depth
    # backstop regardless. Ordinary, un-addressed group chatter must never
    # trigger a reply; see is_message_addressed_to_bot's doc.
    if str(parsed.get("chat_type") or "").strip().lower() in _GROUP_ADDRESSING_CHAT_TYPES:
        if not is_message_addressed_to_bot(parsed):
            LOGGER.info("Sage Telegram hosted: skipping unaddressed group message chat_id=%s", chat_id)
            return False
    workspace_id = get_workspace_for_chat(chat_id)
    if workspace_id is None:
        # Stale pair — tell the user instead of going silent
        await send_message_safe(
            chat_id,
            "⚠️ This chat is no longer linked to a workspace. Please re-pair from Empyralis → Connections → Telegram.",
        )
        return True

    message_text = str(parsed.get("text") or "").strip()
    msg_id = str(parsed.get("message_id") or "")

    # sender_id is the actual per-message Telegram user id, never the chat
    # id — a group chat_id is shared by every member, so substituting it
    # here collapsed every distinct sender into one identity (a stranger's
    # message would carry the exact same sender_id an owner's message in
    # that same chat would). from_id was already resolved above for the
    # echo-loop check; reused here as the real identity signal for command
    # permission checks (_is_sender_owner), audit trails, and any future
    # per-sender scoping. Falls back to chat_id only in the pathological
    # case where Telegram omitted `from` entirely (never a real 1:1 DM).
    real_sender_id = from_id or str(chat_id)

    # ── Canonical inbound envelope (docs/design/inbound-envelope-design.md) ──
    # Hosted Telegram is architected as one paired chat = one workspace
    # (_SAGE_HOSTED_PAIRS, keyed by chat_id) — "owner" is implicit in the
    # pairing itself (verify_and_pair is private-chat-only), not a per-sender
    # check. Every message that reaches this point already passed that
    # pairing gate, so the sender is treated as the verified owner talking
    # to their own agent directly.
    from server_modules.inbound_envelope import InboundEnvelope, EnvelopeSender, SurfaceKind
    envelope = InboundEnvelope(
        platform="telegram_hosted",
        surface=SurfaceKind.DM,
        sender=EnvelopeSender(
            id=real_sender_id,
            display_name=str(parsed.get("from_first_name", "")).strip(),
            is_owner=True,
        ),
    )

    # ── Shared command dispatcher (handles /compact, /new, /help, etc.) ──
    from server_modules.agent_command_dispatcher import dispatch_command
    cmd_reply = await dispatch_command(
        command=message_text,
        workspace_id=workspace_id,
        thread_id="sage-main",
        channel_origin="telegram_hosted",
        sender_id=real_sender_id,
    )
    if cmd_reply is not None:
        await send_message_safe(chat_id, cmd_reply, reply_to_message_id=parsed.get("message_id"))
        return True

    # ── Durable per-agent conversation memory (agent_conversation_memory) ──
    # The SQL thread store (thread_id="sage-main" above) is dead under the
    # SQLite-fallback deployment path (agent_conversation_memory.py's own
    # module doc; docs/design/audit-history-memory.md Part 1A) — this is the
    # durable read+write source for Telegram-hosted's history instead.
    # Keyed per PAIRED CHAT (one paired chat = one workspace, so this stays
    # a single continuous thread per pairing — see the envelope's own
    # "owner implicit in the pairing" note above), agent_id="" (Sage/master —
    # hosted Telegram has no specialist-agent binding concept).
    from server_modules import agent_conversation_memory
    _mem_key = f"telegram_hosted:{chat_id}"
    try:
        _mem_prior = agent_conversation_memory.load_recent_turns(
            workspace_id=workspace_id, agent_id="", conversation_key=_mem_key,
        )
    except Exception:
        _mem_prior = []

    # ── Route through shared-core reply dispatcher ──
    # This ONE call owns: typing, execute_sage_turn, error classification,
    # [SILENT] suppression, message splitting, guaranteed fallback.
    from server_modules.agent_reply_dispatcher import dispatch_sage_reply_safe

    transport = TelegramHostedTransport(str(chat_id))
    return await dispatch_sage_reply_safe(
        transport=transport,
        workspace_id=workspace_id,
        message=message_text,
        channel_origin="telegram_hosted",
        sender_id=real_sender_id,
        sender_name=str(parsed.get("from_first_name", "")).strip(),
        reply_to_id=msg_id,
        envelope=envelope,
        channel_prior_messages=_mem_prior,
        conversation_memory={
            "workspace_id": workspace_id, "agent_id": "", "conversation_key": _mem_key,
        },
    )


async def _background_polling_loop() -> None:
    """Continuously poll Telegram for updates when at least one workspace is paired."""
    global _last_update_id
    LOGGER.info("Sage Telegram hosted: background polling started")
    while True:
        sleep_seconds = _BG_POLL_INTERVAL
        try:
            if not _SAGE_HOSTED_PAIRS:
                await asyncio.sleep(_BG_POLL_INTERVAL)
                continue
            offset = _last_update_id + 1 if _last_update_id > 0 else None
            updates = await poll_updates(limit=10, timeout=5, offset=offset)
            # ── Batch incoming messages with a short delay buffer ──
            if updates:
                # Collect all updates from this batch
                buffered: list[dict] = list(updates)
                # Wait 2.5s for any additional updates to arrive
                await asyncio.sleep(2.5)
                # Poll once more to catch late-arriving messages
                extra = await poll_updates(limit=10, timeout=3, offset=_last_update_id + 1 if _last_update_id > 0 else None)
                for ue in (extra or []):
                    eid = int(ue.get("update_id", 0))
                    if eid > _last_update_id:
                        _last_update_id = eid
                    buffered.append(ue)
                # Extract text from all buffered updates (skip own messages, duplicates)
                messages: list[str] = []
                seen_ids: set[int] = set()
                for u in buffered:
                    uid = int(u.get("update_id", 0))
                    if uid in seen_ids:
                        continue
                    seen_ids.add(uid)
                    if uid > _last_update_id:
                        _last_update_id = uid
                    parsed = parse_telegram_update(u)
                    if parsed is None:
                        continue
                    _bot_own_id = str(os.getenv("SAGE_TELEGRAM_HOSTED_BOT_USER_ID", "8870032163")).strip()
                    if _bot_own_id and str(parsed.get("from_id", "")).strip() == _bot_own_id:
                        continue
                    text = str(parsed.get("text") or "").strip()
                    if text:
                        messages.append(text)
                    # Also handle inbound message tracking (pairing, etc.)
                    await handle_inbound_message(parsed)
                # If we collected multiple messages, combine them
                if len(messages) > 1:
                    combined = "\n---\n".join(messages)
                    # Create a synthetic update from the first update's metadata
                    first = buffered[0]
                    first_parsed = parse_telegram_update(first)
                    if first_parsed:
                        first_parsed["text"] = combined
                        first_parsed["batched_count"] = len(messages)
                        await _process_update(first_parsed)
                elif len(messages) == 1:
                    # Single message — process normally via the first update
                    for u in buffered:
                        parsed = parse_telegram_update(u)
                        if parsed and str(parsed.get("text") or "").strip():
                            await _process_update(u)
                            break
        except TelegramUnauthorizedError as exc:
            # Permanent auth failure (401) — the token is dead. Stop hammering
            # getUpdates every _BG_POLL_INTERVAL seconds; back off hard, and
            # let this slow cadence double as the next reauth probe (the
            # circuit breaker self-clears the moment a call succeeds again).
            LOGGER.warning(
                "Sage Telegram hosted: polling suspended pending token fix (%s) — "
                "next probe in %ss", exc, _SUSPENDED_POLL_INTERVAL_SECONDS,
            )
            sleep_seconds = _SUSPENDED_POLL_INTERVAL_SECONDS
        except Exception as exc:
            # Transient (network/429/5xx/etc.) — keep the normal retry cadence.
            LOGGER.warning("Sage Telegram hosted: polling loop error: %s", exc)
        await asyncio.sleep(sleep_seconds)


def start_background_polling() -> None:
    """Start the background Telegram polling loop if not already running.
    Uses a module-level guard to prevent duplicate loops even when
    called multiple times (e.g. from startup hooks and module init).
    """
    global _polling_task
    if _polling_task is not None and not _polling_task.done():
        LOGGER.info("Sage Telegram hosted: background polling already running (skipping duplicate start)")
        return

    # Phase 3A: on-host single-poller guarantee. Telegram getUpdates long-polling
    # is single-consumer — two gateway processes on one box polling the same bot
    # token steal each other's updates (confirmed-offset races). The in-process
    # guard above only covers this process; this machine-local credential lock is
    # the cross-process counterpart, mirroring discord_bot_runtime_service. Held
    # for process lifetime; a dead owner's lock is auto-reclaimed via PID
    # staleness detection.
    _token = _bot_token()
    if _token:
        from server_modules import gateway_credential_lock as _cred_lock
        _acquired, _existing = _cred_lock.acquire_scoped_lock(
            "telegram_hosted_poll", _token,
            metadata={"component": "sage_telegram_hosted_polling"},
        )
        if not _acquired:
            _owner_pid = _existing.get("pid") if isinstance(_existing, dict) else None
            LOGGER.warning(
                "Sage Telegram hosted: another live process%s already polls this bot "
                "on this host — not starting a second poller.",
                f" (PID {_owner_pid})" if _owner_pid else "",
            )
            return
        import atexit as _atexit
        _atexit.register(_cred_lock.release_scoped_lock, "telegram_hosted_poll", _token)

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    _polling_task = loop.create_task(_background_polling_loop())
    LOGGER.info("Sage Telegram hosted: background polling started")

    # Register native slash commands with Telegram (fire-and-forget).
    try:
        loop.create_task(_register_telegram_native_commands())
    except Exception:
        pass
