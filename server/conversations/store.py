"""JSONL conversation store — one file per chat per channel.

Layout: ~/.empyralis/v2/conversations/<workspace>/<channel>/<chat_id>.jsonl
Each line: {"role": "user"|"assistant", "content": "...", "ts": "..."}
"""

import json
import os
from pathlib import Path

CONV_ROOT = Path.home() / ".empyralis" / "v2" / "conversations"


def _path(workspace: str, channel: str, chat_id: str | int) -> Path:
    return CONV_ROOT / workspace / channel / f"{chat_id}.jsonl"


def append(workspace: str, channel: str, chat_id: str | int,
           role: str, content: str) -> None:
    p = _path(workspace, channel, chat_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        f.write(json.dumps({"role": role, "content": content}) + "\n")


def load_window(workspace: str, channel: str, chat_id: str | int,
                max_messages: int = 20, max_tokens: int = 50_000) -> list[dict]:
    """Return trimmed conversation window.
    Token count is rough: 4 chars ~ 1 token. Capped at whichever bound hits first."""
    p = _path(workspace, channel, chat_id)
    if not p.exists():
        return []
    lines = p.read_text().splitlines()
    # Reverse: count from most recent
    selected: list[dict] = []
    token_est = 0
    for line in reversed(lines):
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        selected.insert(0, msg)
        token_est += len(msg.get("content", "")) // 4
        if len(selected) >= max_messages or token_est >= max_tokens:
            break
    return selected


def clear(workspace: str, channel: str, chat_id: str | int) -> None:
    p = _path(workspace, channel, chat_id)
    if p.exists():
        p.unlink()
