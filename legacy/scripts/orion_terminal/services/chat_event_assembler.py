from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Dict, Iterable, List, Tuple


ChatRowKey = Tuple[str, str, str, str, str, str]


def _replay_key(row: Dict[str, Any]) -> str:
    if isinstance(row.get("skill_replay"), dict):
        replay = row.get("skill_replay") or {}
    else:
        metadata = row.get("metadata")
        replay = metadata.get("skill_replay") if isinstance(metadata, dict) and isinstance(metadata.get("skill_replay"), dict) else {}
    if not replay:
        return ""
    try:
        return json.dumps(replay, sort_keys=True, ensure_ascii=True)
    except Exception:
        return str(replay)


def _row_key(row: Dict[str, Any]) -> ChatRowKey:
    return (
        str(row.get("ts") or "").strip(),
        str(row.get("role") or "").strip().lower(),
        str(row.get("channel") or "").strip().lower(),
        str(row.get("run_id") or "").strip(),
        " ".join(str(row.get("text") or "").split()),
        _replay_key(row),
    )


@dataclass
class ChatEventAssembler:
    """Track whether chat rows changed between render passes."""

    _last_keys: Tuple[ChatRowKey, ...] = field(default_factory=tuple)

    def reset(self) -> None:
        self._last_keys = tuple()

    def changed(self, rows: Iterable[Dict[str, Any]]) -> bool:
        current = tuple(_row_key(row) for row in rows)
        changed = current != self._last_keys
        self._last_keys = current
        return changed

    def snapshot(self) -> List[ChatRowKey]:
        return list(self._last_keys)
