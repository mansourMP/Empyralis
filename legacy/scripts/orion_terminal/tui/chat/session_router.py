from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Protocol, Set, Tuple

from ... import question_catalog as q
from ...core import Choice


class SessionRouterState(Protocol):
    session_filter: Optional[str]
    channel_filter: Optional[str]
    event_assembler: Any


def _preview_text(value: Any, limit: int = 42) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    if limit <= 1:
        return "…"
    return text[: limit - 1].rstrip() + "…"


def _ts_sort_key(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt.timestamp()
    except Exception:
        return 0.0


def parse_session_command(line: str) -> Tuple[str, str]:
    stripped = line.strip()
    lowered = stripped.lower()
    if lowered == "/sessions":
        return "list", ""
    if lowered == "/sessions list":
        return "list", ""
    if lowered == "/sessions all":
        return "all", ""
    if lowered.startswith("/sessions use "):
        return "use", stripped[len("/sessions use ") :].strip()
    if lowered == "/sessions clear":
        return "all", ""
    if lowered.startswith("/sessions "):
        return "use", stripped[len("/sessions ") :].strip()
    if lowered == "/session":
        return "list", ""
    if lowered == "/session list":
        return "list", ""
    if lowered == "/session all":
        return "all", ""
    if lowered.startswith("/session use "):
        return "use", stripped[len("/session use ") :].strip()
    if lowered == "/session clear":
        return "all", ""
    if lowered.startswith("/session "):
        # Support shorthand: /session telegram | /session whatsapp | /session <session_key>
        return "use", stripped[len("/session ") :].strip()
    return "", ""


def _session_overlay_options(events: List[Dict[str, Any]], sessions: Optional[List[Dict[str, Any]]] = None) -> List[Choice]:
    options: List[Choice] = [
        Choice("__all__", "All channels", "Show Telegram + WhatsApp + terminal"),
        Choice("__telegram__", "Telegram channel", "Only Telegram messages"),
        Choice("__whatsapp__", "WhatsApp channel", "Only WhatsApp messages"),
    ]
    seen: Set[str] = set()
    sorted_sessions = sorted(
        [item for item in (sessions or []) if isinstance(item, dict)],
        key=lambda item: (
            _ts_sort_key(item.get("last_ts")),
            int(item.get("event_count") or 0),
        ),
        reverse=True,
    )
    for item in sorted_sessions:
        if not isinstance(item, dict):
            continue
        key = str(item.get("session_id") or item.get("session_key") or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        channel = str(item.get("channel") or "unknown").strip().lower()
        count = int(item.get("event_count") or 0)
        inbound = int(item.get("inbound_count") or 0)
        outbound = int(item.get("outbound_count") or 0)
        last_text = _preview_text(item.get("last_text") or "", limit=46)
        last_ts = str(item.get("last_ts") or "").strip()
        note_parts = [f"channel={channel}", f"events={count}", f"in={inbound}", f"out={outbound}"]
        if last_text:
            note_parts.append(f"last={last_text}")
        if last_ts:
            note_parts.append(f"at={_preview_text(last_ts, limit=19)}")
        note = " ".join(note_parts)
        options.append(Choice(f"session:{key}", key, note))
        if len(options) >= 18:
            return options
    for item in events:
        key = str(item.get("session_id") or item.get("session_key") or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        channel = str(item.get("channel") or "unknown").strip().lower()
        options.append(Choice(f"session:{key}", key, f"channel={channel}"))
        if len(options) >= 18:
            break
    return options


def _reorder_overlay_options_for_active_channel(options: List[Choice], active_channel: Optional[str]) -> List[Choice]:
    channel = str(active_channel or "").strip().lower()
    if channel not in {"telegram", "whatsapp", "terminal"}:
        return options
    if len(options) <= 3:
        return options
    pinned: List[Choice] = []
    others: List[Choice] = []
    for item in options[3:]:
        key = str(item.key or "")
        if key.startswith(f"session:{channel}:"):
            pinned.append(item)
        else:
            others.append(item)
    if not pinned:
        return options
    return options[:3] + pinned + others


def apply_session_choice(state: SessionRouterState, selected_key: str) -> str:
    if selected_key == "__all__":
        state.session_filter = None
        state.channel_filter = None
        state.event_assembler.reset()
        return "Session set to all channels."
    if selected_key == "__telegram__":
        state.session_filter = None
        state.channel_filter = "telegram"
        state.event_assembler.reset()
        return "Session set to telegram channel."
    if selected_key == "__whatsapp__":
        state.session_filter = None
        state.channel_filter = "whatsapp"
        state.event_assembler.reset()
        return "Session set to whatsapp channel."
    if selected_key.startswith("session:"):
        value = selected_key[len("session:") :].strip()
        if value:
            state.session_filter = value
            state.channel_filter = None
            state.event_assembler.reset()
            return f"Session set to {value}"
    return "Session unchanged."


def open_session_overlay(
    ctx,
    state: SessionRouterState,
    events: List[Dict[str, Any]],
    sessions: Optional[List[Dict[str, Any]]] = None,
) -> str:
    options = _session_overlay_options(events, sessions=sessions)
    options = _reorder_overlay_options_for_active_channel(options, state.channel_filter)
    default_index = 0
    if state.channel_filter == "telegram":
        default_index = 1
    elif state.channel_filter == "whatsapp":
        default_index = 2
    elif state.session_filter:
        key = f"session:{state.session_filter}"
        for idx, option in enumerate(options):
            if option.key == key:
                default_index = idx
                break
    choice = ctx.ui.choose(
        q.Q_LIVE_TUI_CHAT_SESSION,
        options,
        default_index=default_index,
        title=q.TITLE_LIVE_TUI,
    )
    return apply_session_choice(state, choice.key)
