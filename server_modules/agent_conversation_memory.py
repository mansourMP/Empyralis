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
from pathlib import Path
from typing import Any, Dict, List, Optional

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


def load_recent_turns(
    *,
    workspace_id: str,
    agent_id: str,
    conversation_key: str,
    limit: int = DEFAULT_RECENT_TURNS,
) -> List[Dict[str, str]]:
    """Return the last ``limit`` turns as ``[{"role","content"}, ...]`` in
    chronological order. Empty list if this conversation has no history yet —
    never raises (a broken/half-written line is skipped, not fatal)."""
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
    out: List[Dict[str, str]] = []
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
            out.append({"role": role, "content": content})
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
