"""Dead-simple, durable, per-agent conversation memory — Claude Code / Codex style.

ONE append-only JSONL file per (workspace, agent, conversation), written and
fsync'd to disk the instant each turn happens, so it survives backend
restarts. This is the fix for the silently-dead channel memory: the old path
kept turns in an in-memory dict (``_LOCAL_AGENT_TURNS``) and only ever flushed
to a single shared ``agent-threads.json`` that stopped updating ~2026-06-24 —
so every ``pm2 restart`` wiped conversation history and agents "forgot"
everything mid-conversation.

Design constraints (deliberately minimal — the tangle it replaces is the bug):
  * No Postgres, no Rust kernel gate, no shared global dicts, no
    store-selection branches. Just files under the agent's own state dir.
  * Append is atomic per line (single ``write()`` of one line in ``a`` mode is
    atomic on POSIX for our line sizes) and immediately durable (``fsync``).
  * Reads return the last N turns — conversations are bounded, so we tail the
    file rather than hold anything in memory across turns.

Storage layout (one conversation == one file):
    $EMPYRALIS_STATE_HOME/conversations/<workspace>/<agent>/<conversation>.jsonl

``agent_id`` empty == the workspace's Sage/master agent — bucketed under
``_sage`` so it is still isolated per workspace, never cross-agent.
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import logging as _logging

_logger = _logging.getLogger(__name__)

# Same env var control_plane_repository uses, so this lives beside the rest of
# the local state instead of inventing a second home.
_STATE_HOME = Path(
    os.getenv("EMPYRALIS_STATE_HOME", str(Path.home() / ".empyralis" / "state"))
).expanduser()
_CONVERSATIONS_ROOT = Path(
    os.getenv("EMPYRALIS_CONVERSATIONS_DIR", str(_STATE_HOME / "conversations"))
).expanduser()

# Default recent-turn window loaded back into a turn's context. Matches the old
# SAGE_THREAD_MAX_TURNS (10 exchanges) so behavior is unchanged where it worked.
DEFAULT_RECENT_TURNS = int(os.getenv("EMPYRALIS_CONVERSATION_RECENT_TURNS", "20"))

# Hard cap so a runaway conversation file can't grow without bound; we keep the
# most recent MAX_TURNS_RETAINED lines and drop older ones on write.
MAX_TURNS_RETAINED = int(os.getenv("EMPYRALIS_CONVERSATION_MAX_TURNS", "400"))

# One lock per process is plenty — writes are tiny and per-file appends rarely
# contend. Keeps the "simple" promise instead of a lock table.
_WRITE_LOCK = threading.Lock()

_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_segment(value: Any, *, fallback: str) -> str:
    """Filesystem-safe path segment. Never empty, never traverses (no '/'/'..')."""
    token = _SAFE_SEGMENT.sub("_", str(value or "").strip())
    token = token.strip("._") or fallback
    # Guard against pathological length blowing past filename limits.
    return token[:120]


def conversation_path(
    *, workspace_id: str, agent_id: str, conversation_key: str
) -> Path:
    """Absolute path to the JSONL file backing this one conversation."""
    ws = _safe_segment(workspace_id, fallback="_ws")
    agent = _safe_segment(agent_id, fallback="_sage")
    convo = _safe_segment(conversation_key, fallback="_main")
    return _CONVERSATIONS_ROOT / ws / agent / f"{convo}.jsonl"


# ── Provenance: recovering {agent, channel, person} from what's on disk ────
#
# conversation_key is built by callers as f"{surface_channel}:{remote_jid}"
# (see personal_channel_sage_bridge_service.py, the only writer today) and
# then run through _safe_segment above for the filename -- which replaces
# the ":" delimiter (and any "@"/"/" inside the JID) with "_", same as any
# "_" already inside surface_channel itself. That is a LOSSY transform: the
# raw key can't be losslessly recovered from the filename alone. What
# follows is a best-effort, longest-known-prefix match against the closed
# set of surface_channel values real callers use (mirrors
# channel_lane_contract_service.PERSONAL_CHANNEL_SPECS's key set, kept as a
# local, ordered list rather than imported so this module stays
# dependency-free per the design constraints in the module docstring) --
# correct for every channel actually writing here today, and it degrades to
# an honest "unknown surface" rather than a wrong guess for anything else.
_KNOWN_SURFACES: List[Tuple[str, str]] = sorted(
    [
        ("whatsapp_personal", "WhatsApp"),
        ("telegram_personal", "Telegram"),
        ("signal_personal", "Signal"),
        ("imessage_personal", "iMessage"),
        ("wechat_personal", "WeChat"),
        ("discord_personal", "Discord"),
        ("discord_guild", "Discord"),
        ("whatsapp", "WhatsApp"),
        ("telegram", "Telegram"),
        ("discord", "Discord"),
        ("slack", "Slack"),
        ("direct_chat", "Web chat"),
        ("sage_web", "Web chat"),
    ],
    key=lambda pair: -len(pair[0]),
)


def _humanize_surface(surface: str) -> str:
    """Readable fallback for a surface_channel outside the known table
    above -- e.g. a future channel wired to this store before the table is
    updated. "some_new_channel" -> "Some New Channel", never a raw token."""
    cleaned = re.sub(r"_(personal|guild|hosted|bot)$", "", surface)
    words = [w for w in cleaned.split("_") if w]
    return " ".join(w.capitalize() for w in words) or "Unknown"


def channel_label(surface_channel: str) -> str:
    """Human label for a surface_channel, e.g. "telegram_personal" ->
    "Telegram". An unrecognized surface still gets a readable label, never
    a raw snake_case token or a crash."""
    s = str(surface_channel or "").strip().lower()
    if not s:
        return "Unknown"
    for key, label in _KNOWN_SURFACES:
        if s == key:
            return label
    return _humanize_surface(s)


def _split_conversation_key(sanitized_key: str) -> Tuple[str, str]:
    """Best-effort split of an on-disk (already _safe_segment'd)
    conversation_key back into (surface_channel, remote_jid), via the
    longest matching known surface prefix. See the module note above for
    why this is a heuristic, not a lossless reversal."""
    s = str(sanitized_key or "").strip()
    for key, _label in _KNOWN_SURFACES:
        if s == key:
            return key, ""
        if s.startswith(key + "_"):
            return key, s[len(key) + 1 :]
    return "unknown", s


def load_recent_turns(
    *,
    workspace_id: str,
    agent_id: str,
    conversation_key: str,
    limit: int = DEFAULT_RECENT_TURNS,
) -> List[Dict[str, Any]]:
    """Return the last ``limit`` turns as ``[{"role","content"[,"metadata"]}, ...]``
    in chronological order. Empty list if this conversation has no history yet —
    never raises (a broken/half-written line is skipped, not fatal).

    The "metadata" key is present ONLY for turns whose stored record actually
    carries one (append_turn's optional `metadata` param, e.g. the inbound
    turn's attribution — see personal_channel_sage_bridge_service.py's
    append_turn call sites). A turn written before per-turn metadata existed,
    or written without it, round-trips to exactly ``{"role", "content"}`` —
    unchanged from before this key existed, so existing exact-equality
    callers/tests over old data are unaffected.
    """
    path = conversation_path(
        workspace_id=workspace_id, agent_id=agent_id, conversation_key=conversation_key
    )
    if not path.exists():
        return []
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except Exception as exc:  # unreadable file must not break a live reply
        _logger.warning("conversation memory read failed for %s: %s", path, exc)
        return []
    out: List[Dict[str, Any]] = []
    for line in raw_lines[-max(1, int(limit or 1)) * 2 :]:  # a little slack for skips
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        role = str(rec.get("role") or "").strip().lower()
        content = str(rec.get("content") or "")
        if role in ("user", "assistant", "system") and content:
            turn: Dict[str, Any] = {"role": role, "content": content}
            metadata = rec.get("metadata")
            if isinstance(metadata, dict) and metadata:
                turn["metadata"] = metadata
            out.append(turn)
    if limit and len(out) > int(limit):
        out = out[-int(limit) :]
    return out


def append_turn(
    *,
    workspace_id: str,
    agent_id: str,
    conversation_key: str,
    role: str,
    content: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """Append one turn and fsync it to disk immediately. Returns True on
    durable write. Best-effort: a storage failure logs + returns False rather
    than breaking the reply that triggered it (memory loss is bad, a dropped
    reply is worse)."""
    normalized_role = str(role or "").strip().lower()
    normalized_content = str(content or "")
    if normalized_role not in ("user", "assistant", "system") or not normalized_content:
        return False
    record = {"role": normalized_role, "content": normalized_content}
    if metadata:
        record["metadata"] = dict(metadata)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    path = conversation_path(
        workspace_id=workspace_id, agent_id=agent_id, conversation_key=conversation_key
    )
    try:
        with _WRITE_LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()
                os.fsync(fh.fileno())  # durable across restart — the whole point
            _trim_if_needed_locked(path)
        return True
    except Exception as exc:
        _logger.warning("conversation memory append failed for %s: %s", path, exc)
        return False


def record_exchange(
    *,
    workspace_id: str,
    agent_id: str,
    conversation_key: str,
    user_message: str,
    assistant_reply: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """Convenience: persist a user message and the assistant reply together.
    Returns True only if BOTH were written durably."""
    ok_user = append_turn(
        workspace_id=workspace_id,
        agent_id=agent_id,
        conversation_key=conversation_key,
        role="user",
        content=user_message,
        metadata=metadata,
    )
    ok_assistant = append_turn(
        workspace_id=workspace_id,
        agent_id=agent_id,
        conversation_key=conversation_key,
        role="assistant",
        content=assistant_reply,
        metadata=metadata,
    )
    return ok_user and ok_assistant


def _trim_if_needed_locked(path: Path) -> None:
    """Keep only the most recent MAX_TURNS_RETAINED lines. Caller holds the
    write lock. Rewrites atomically via a temp file + os.replace so a crash
    mid-trim can't corrupt the conversation."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return
    if len(lines) <= MAX_TURNS_RETAINED:
        return
    kept = lines[-MAX_TURNS_RETAINED:]
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write("\n".join(kept) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception as exc:
        _logger.warning("conversation memory trim failed for %s: %s", path, exc)


# ── Workspace-level read API: the Conversations view ─────────────────────
#
# Everything above is per-(workspace, agent, conversation) -- the shape a
# live channel turn needs. The owner's unified Conversations view needs the
# opposite cut: every conversation across every agent in ONE workspace,
# tagged with where it came from. That reader never existed -- the only
# conversation UI in the product (WorkTab, per-agent) reads a different,
# SQL-backed store (control_plane_repository's agent_turns table) that is
# dead under SQLite-fallback prod, same root cause as this module's own
# docstring above. Everything below only ever reads; it never writes.


def list_workspace_conversations(*, workspace_id: str) -> List[Dict[str, Any]]:
    """Every conversation on disk for this workspace, newest activity
    first. One entry per (agent, conversation) file. Never raises -- an
    unreadable directory or file is skipped, not fatal to the whole list.

    Strictly workspace-scoped: only ever walks the one sanitized workspace
    directory workspace_id resolves to. A different workspace's
    conversations live in a sibling directory, never a descendant of this
    one -- there is no path under which this can return another
    workspace's data.
    """
    ws_dir = _CONVERSATIONS_ROOT / _safe_segment(workspace_id, fallback="_ws")
    if not ws_dir.is_dir():
        return []
    out: List[Dict[str, Any]] = []
    try:
        agent_dirs = [p for p in ws_dir.iterdir() if p.is_dir()]
    except Exception as exc:
        _logger.warning("conversation list failed to scan %s: %s", ws_dir, exc)
        return []
    for agent_dir in agent_dirs:
        agent_id = "" if agent_dir.name == "_sage" else agent_dir.name
        try:
            files = [p for p in agent_dir.iterdir() if p.is_file() and p.suffix == ".jsonl"]
        except Exception as exc:
            _logger.warning("conversation list failed to scan %s: %s", agent_dir, exc)
            continue
        for path in files:
            summary = _summarize_conversation_file(path, agent_bucket=agent_dir.name, agent_id=agent_id)
            if summary is not None:
                out.append(summary)
    out.sort(key=lambda c: c.get("last_activity_at") or "", reverse=True)
    return out


def _summarize_conversation_file(path: Path, *, agent_bucket: str, agent_id: str) -> Optional[Dict[str, Any]]:
    """One list-row summary for a single conversation file, or None if the
    file is empty/unreadable (skipped rather than shown as a blank row)."""
    sanitized_key = path.stem
    surface, remote_jid = _split_conversation_key(sanitized_key)
    try:
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except Exception as exc:
        _logger.warning("conversation summary read failed for %s: %s", path, exc)
        return None
    if not lines:
        return None
    last_turn = _last_valid_turn(lines)
    # No per-turn timestamp exists in this store's record shape (role +
    # content [+ optional metadata] only, see append_turn above) -- file
    # mtime is the honest proxy for "last activity", not a fabricated
    # per-message time.
    try:
        last_activity_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    except Exception:
        last_activity_at = ""
    return {
        "conversation_id": f"{agent_bucket}:{sanitized_key}",
        "agent_id": agent_id,
        "surface_channel": surface,
        "channel_label": channel_label(surface),
        "remote_jid": remote_jid,
        "sender": remote_jid or sanitized_key,
        "turn_count": len(lines),
        "last_message_preview": last_turn["content"],
        "last_message_role": last_turn["role"],
        "last_activity_at": last_activity_at,
    }


def _last_valid_turn(lines: List[str]) -> Dict[str, str]:
    """Newest well-formed {role, content} turn in this file, scanning from
    the end -- same validity rule as load_recent_turns (role must be
    user/assistant/system, content non-empty) so a list preview never shows
    a blank or half-written line."""
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        role = str(rec.get("role") or "").strip().lower()
        content = str(rec.get("content") or "")
        if role in ("user", "assistant", "system") and content:
            return {"role": role, "content": content}
    return {"role": "", "content": ""}


def _is_safe_path_segment(token: str) -> bool:
    """True iff token is exactly what _safe_segment would produce for
    itself as input (i.e. it is already a clean, on-disk-safe path segment)
    -- OR is the literal "_sage" sentinel _safe_segment's own fallback
    injects for an empty agent_id. That sentinel is special-cased because
    _safe_segment's regular substitute-and-strip pass would otherwise mangle
    it: it strips leading/trailing "." and "_", which would turn "_sage"
    into "sage" on a second pass even though "_sage" is exactly the
    directory conversation_path() itself creates on disk."""
    if token == "_sage":
        return True
    return bool(token) and _safe_segment(token, fallback="") == token


def _parse_conversation_id(conversation_id: str) -> Optional[Tuple[str, str]]:
    """Validate + split a conversation_id ("<agent_bucket>:<sanitized_key>",
    as produced by list_workspace_conversations above) back into its two
    on-disk path segments. Requires BOTH halves to already be exactly the
    safe token a real id would carry (see _is_safe_path_segment) and
    rejects -- returns None -- rather than coercing anything else. A
    crafted id (e.g. containing "../") never gets silently redirected at
    some other file via _safe_segment's own substitution/fallback
    behavior; it just fails to resolve."""
    raw = str(conversation_id or "").strip()
    agent_bucket, sep, safe_key = raw.partition(":")
    if not sep or not agent_bucket or not safe_key:
        return None
    if not _is_safe_path_segment(agent_bucket):
        return None
    if not _is_safe_path_segment(safe_key):
        return None
    return agent_bucket, safe_key


def load_conversation(
    *, workspace_id: str, conversation_id: str, limit: int = MAX_TURNS_RETAINED,
) -> Optional[Dict[str, Any]]:
    """One conversation's turns plus its provenance tags, for the
    Conversations view's transcript pane. None if conversation_id doesn't
    parse to an on-disk-safe token pair or the file doesn't exist --
    callers should treat that as "not found", not an empty conversation."""
    parsed = _parse_conversation_id(conversation_id)
    if parsed is None:
        return None
    agent_bucket, safe_key = parsed
    agent_id = "" if agent_bucket == "_sage" else agent_bucket
    path = conversation_path(workspace_id=workspace_id, agent_id=agent_id, conversation_key=safe_key)
    if not path.is_file():
        return None
    turns = load_recent_turns(
        workspace_id=workspace_id, agent_id=agent_id, conversation_key=safe_key, limit=limit,
    )
    surface, remote_jid = _split_conversation_key(safe_key)
    return {
        "conversation_id": f"{agent_bucket}:{safe_key}",
        "agent_id": agent_id,
        "surface_channel": surface,
        "channel_label": channel_label(surface),
        "remote_jid": remote_jid,
        "sender": remote_jid or safe_key,
        "turns": turns,
    }
