from __future__ import annotations

import os
import shutil
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

from ..core import accent, muted, ok, strong, tint, warn
from ..text_format import flatten_text, preview_text

_APPROVAL_EVENT_ID_RE = re.compile(r"\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b", re.IGNORECASE)
_APPROVAL_SHORT_ID_RE = re.compile(r"\[([0-9a-f]{8})\]", re.IGNORECASE)
_APPROVAL_PENDING_ID_RE = re.compile(r"pending approvals:\s*-\s*([0-9a-f]{8})", re.IGNORECASE)


def _hide_channel_commands_enabled() -> bool:
    raw = os.getenv("ORION_CLI_CHAT_HIDE_CHANNEL_COMMANDS", "1").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _chat_show_meta_enabled() -> bool:
    raw = os.getenv("ORION_CLI_CHAT_SHOW_META", "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _user_panel_block(text: str) -> List[str]:
    # Full-width slate panel with top+bottom rails, closer to OpenClaw's message blocks.
    width = max(48, min(120, int(shutil.get_terminal_size((96, 24)).columns) - 4))
    trimmed = str(text or "").strip()
    if len(trimmed) > max(4, width - 3):
        trimmed = trimmed[: max(4, width - 4)].rstrip() + "…"
    # Keep a single consistent slate tone across top/content/bottom for clearer ownership.
    fill = tint(" " * width, "48;5;238")
    content = tint(f" {trimmed}".ljust(width), "48;5;238;38;5;255")
    return [fill, content, fill]


def _assistant_panel_block(text: str) -> List[str]:
    # Subtle response panel with rail + body, separated from user cards.
    width = max(48, min(120, int(shutil.get_terminal_size((96, 24)).columns) - 4))
    raw_lines = [str(line).strip() for line in str(text or "").splitlines() if str(line).strip()]
    if not raw_lines:
        return []
    rail = tint("─" * width, "38;5;240")
    out: List[str] = [rail]
    max_body = max(8, width - 3)
    for line in raw_lines:
        body = line if len(line) <= max_body else line[: max_body - 1].rstrip() + "…"
        out.append(tint(f" {body}".ljust(width), "48;5;236;38;5;252"))
    out.append(rail)
    return out


def _assistant_panel_enabled() -> bool:
    raw = os.getenv("ORION_CLI_CHAT_ASSISTANT_PANEL", "1").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _assistant_block_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        max_lines = int(os.getenv("ORION_CLI_CHAT_ASSISTANT_MAX_LINES", "14"))
    except Exception:
        max_lines = 14
    try:
        max_chars = int(os.getenv("ORION_CLI_CHAT_ASSISTANT_MAX_CHARS", "1800"))
    except Exception:
        max_chars = 1800
    max_lines = max(4, min(max_lines, 48))
    max_chars = max(240, min(max_chars, 8000))
    lines = [str(line).rstrip() for line in text.splitlines() if str(line).strip()]
    if not lines:
        return preview_text(text, max_chars)

    out: List[str] = []
    total = 0
    truncated = False
    for idx, line in enumerate(lines):
        if idx >= max_lines:
            truncated = True
            break
        if not line:
            continue
        sep = 1 if out else 0
        if total + sep + len(line) > max_chars:
            remaining = max_chars - total - sep
            if remaining > 2:
                out.append(line[: remaining - 1].rstrip() + "…")
            truncated = True
            break
        out.append(line)
        total += sep + len(line)

    if truncated and out and not out[-1].endswith("…"):
        out.append("…")
    return "\n".join(out)


def _is_timeout_noise(text_lower: str) -> bool:
    text = str(text_lower or "")
    return "timed out" in text or "timeout" in text or "urlopen error" in text


def _is_runtime_chat_noise(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    lowered = raw.lower()
    if re.search(r"^run_id:\s*[0-9a-f-]{8,}\s*$", raw, flags=re.IGNORECASE):
        return True
    if lowered.startswith("run_id:"):
        return True
    if re.search(r"^progress:\s*live\s*$", raw, flags=re.IGNORECASE):
        return True
    if re.search(r"^result:\s*success\s*$", raw, flags=re.IGNORECASE):
        return True
    if "local worker completed orion run for goal:" in lowered:
        return True
    if "local worker completed empyralis run for goal:" in lowered:
        return True
    if lowered == "execution plan generated.":
        return True
    if lowered.startswith("run completed by local companion."):
        return True
    return False


def _approval_dedupe_marker(raw_text: str) -> str:
    text = str(raw_text or "").strip()
    if not text:
        return ""
    lower = text.lower()
    if "approval required" not in lower and "pending approvals" not in lower:
        return ""
    match = _APPROVAL_EVENT_ID_RE.search(text)
    if match:
        full = match.group(1).lower()
        return f"approval_short:{full[:8]}"
    match = _APPROVAL_SHORT_ID_RE.search(text)
    if match:
        return f"approval_short:{match.group(1).lower()}"
    match = _APPROVAL_PENDING_ID_RE.search(text)
    if match:
        return f"approval_short:{match.group(1).lower()}"
    return "approval:generic"


def _to_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except Exception:
        return None


def _skill_replay_from_row(row: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(row.get("skill_replay"), dict):
        return row.get("skill_replay") or {}
    metadata = row.get("metadata")
    if isinstance(metadata, dict) and isinstance(metadata.get("skill_replay"), dict):
        return metadata.get("skill_replay") or {}
    return {}


def _skill_replay_key(row: Dict[str, Any]) -> str:
    replay = _skill_replay_from_row(row)
    if not replay:
        return ""
    try:
        return json.dumps(replay, sort_keys=True, ensure_ascii=True)
    except Exception:
        return str(replay)


def _format_skill_replay_line(row: Dict[str, Any]) -> str:
    replay = _skill_replay_from_row(row)
    if not replay:
        return ""
    status = "applied" if bool(replay.get("applied")) else "fallback"
    confidence = _to_float(replay.get("confidence"))
    conf_text = f"{confidence:.2f}" if confidence is not None else "-"
    signature = preview_text(str(replay.get("signature") or ""), 20)
    reason = preview_text(str(replay.get("reason") or ""), 28)
    parts = [f"replay: {status}", f"conf={conf_text}"]
    if signature:
        parts.append(f"sig={signature}")
    if reason:
        parts.append(f"reason={reason}")
    return " ".join(parts)


def _is_channel_control_command(item: Dict[str, Any], raw_text: str) -> bool:
    direction = str(item.get("direction") or "").strip().lower()
    channel = str(item.get("channel") or "").strip().lower()
    if direction != "inbound" or channel not in {"telegram", "whatsapp"}:
        return False
    text = raw_text.strip()
    if not text:
        return False
    lowered = text.casefold()
    if lowered.startswith("/"):
        return True
    # Common quick-action labels seen in channel keyboards.
    if lowered in {"list files", "status", "help", "📂 list files"}:
        return True
    return False


def _normalize_chat_role(
    *,
    role: Any = None,
    direction: Any = None,
    event_type: Any = None,
) -> str:
    role_key = str(role or "").strip().lower()
    if role_key in {"you", "orion", "system"}:
        return role_key
    direction_key = str(direction or "").strip().lower()
    if direction_key == "inbound":
        return "you"
    if direction_key == "outbound":
        return "orion"
    event_key = str(event_type or "").strip().lower()
    if event_key in {"message", "run_update"}:
        return "orion"
    return "system"


def normalize_chat_row(item: Dict[str, Any], *, source: str = "inbox") -> Dict[str, Any]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    channel = str(item.get("channel") or "unknown").strip().lower()
    event_type = str(item.get("event_type") or "").strip().lower()
    direction = str(item.get("direction") or "").strip().lower()
    session_id = str(item.get("session_id") or item.get("session_key") or "").strip()
    status = str(item.get("status") or metadata.get("status") or "").strip().lower()
    row = {
        "id": str(item.get("id") or "").strip(),
        "ts": str(item.get("ts") or ""),
        "role": _normalize_chat_role(
            role=item.get("role"),
            direction=direction,
            event_type=event_type,
        ),
        "text": str(item.get("text") or "").strip(),
        "channel": channel,
        "session_id": session_id,
        "run_id": str(item.get("run_id") or "").strip(),
        "event_type": event_type,
        "action": str(item.get("action") or "").strip().lower(),
        "status": status,
        "source": str(source or "").strip().lower() or "unknown",
        "metadata": metadata,
    }
    return row


def fmt_ms(value: Any) -> str:
    try:
        raw = float(value or 0.0)
    except Exception:
        return "n/a"
    if raw <= 0:
        return "0 ms"
    if raw < 1000:
        return f"{int(raw)} ms"
    return f"{raw / 1000.0:.1f} s"


def format_runs_list(items: List[Dict[str, Any]]) -> str:
    if not items:
        return "No runs found."
    lines: List[str] = []
    for idx, item in enumerate(items[:20], start=1):
        run_id = str(item.get("run_id") or "")
        short = run_id[:8] if run_id else "unknown"
        status = str(item.get("status") or "unknown")
        pack_id = str(item.get("pack_id") or "general")
        target = str(item.get("execution_target_selected") or item.get("execution_target_requested") or "auto")
        duration = fmt_ms(item.get("duration_ms"))
        summary = preview_text(item.get("result_summary") or item.get("user_goal") or "", 68)
        lines.append(f"{idx:>2}. {short}  {status:<16} target={target:<14} mode={pack_id:<26} duration={duration}")
        if summary:
            lines.append(f"    {summary}")
    return "\n".join(lines)


def format_run_detail(item: Dict[str, Any]) -> str:
    run_id = str(item.get("run_id") or "")
    status = str(item.get("status") or "unknown")
    route = item.get("route") if isinstance(item.get("route"), dict) else {}
    route_selected = str(route.get("selected") or route.get("requested") or "auto")
    result = preview_text(item.get("result") or "", 240)
    pending = item.get("pending_approval") if isinstance(item.get("pending_approval"), dict) else {}
    pending_id = str(pending.get("approval_id") or "")
    pending_state = str(pending.get("status") or "")

    lines = [
        f"run_id: {run_id}",
        f"status: {status}",
        f"route: {route_selected}",
        f"duration: {fmt_ms(item.get('duration_ms'))}",
        f"time_to_first_value: {fmt_ms(item.get('time_to_first_value_ms'))}",
        f"human_wait: {fmt_ms(item.get('hitl_wait_total_ms'))}",
    ]
    if pending_id:
        lines.append(f"pending_approval: {pending_id} ({pending_state or 'pending'})")
    if result:
        lines.append(f"result: {result}")
    return "\n".join(lines)


def _inbox_ts(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "--:--:--"
    try:
        normalized = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        return dt.strftime("%H:%M:%S")
    except Exception:
        if "T" in text:
            tail = text.split("T", 1)[1]
            return tail[:8]
        return text[:8]


def _parse_event_ts(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def format_inbox_events(items: List[Dict[str, Any]]) -> str:
    if not items:
        return "No inbox events yet."

    direction_icon = {"inbound": "⬡", "outbound": "⬢", "system": "•"}
    lines: List[str] = []
    for item in items[:80]:
        channel = str(item.get("channel") or "unknown").strip().lower()
        direction = str(item.get("direction") or "system").strip().lower()
        action = str(item.get("action") or "").strip().lower()
        event_type = str(item.get("event_type") or "event").strip().lower()
        run_id = str(item.get("run_id") or "").strip()
        run_short = run_id[:8] if run_id else "-"
        session_key = str(item.get("session_key") or "").strip()
        text = preview_text(item.get("text") or "", 132)
        ts_short = _inbox_ts(item.get("ts"))
        icon = direction_icon.get(direction, "•")
        action_label = action or event_type
        lines.append(
            f"{ts_short}  {icon} {channel:<8} {action_label:<14} run={run_short}  session={preview_text(session_key, 24)}"
        )
        if text:
            lines.append(f"    {text}")
    return "\n".join(lines)


def collect_chat_rows(
    items: List[Dict[str, Any]],
    local_entries: Optional[List[Dict[str, Any]]] = None,
    limit: int = 40,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for item in items:
        normalized = normalize_chat_row(item, source="inbox")
        channel = str(normalized.get("channel") or "unknown").strip().lower()
        role = str(normalized.get("role") or "system").strip().lower()
        event_type = str(normalized.get("event_type") or "").strip().lower()
        if role == "system" and event_type == "error":
            continue
        if role == "system" and channel != "terminal":
            continue
        raw_text = str(normalized.get("text") or "").strip()
        raw_text_lower = raw_text.lower()
        if "[orion route smoke]" in raw_text_lower:
            continue
        if _hide_channel_commands_enabled() and _is_channel_control_command(item, raw_text):
            continue
        if raw_text_lower.startswith("orion telegram commands") or raw_text_lower.startswith("empyralis telegram commands"):
            # Keep bot command cards out of the live transcript feed.
            continue
        if event_type == "error" and _is_timeout_noise(raw_text_lower):
            continue
        if role == "system" and _is_timeout_noise(raw_text_lower):
            continue
        if role == "system" and "run prefix required in this mode" in raw_text_lower:
            continue
        if _is_runtime_chat_noise(raw_text):
            continue
        normalized["text"] = raw_text
        rows.append(normalized)
    for entry in local_entries or []:
        normalized_local = normalize_chat_row(entry, source="local")
        local_text = str(normalized_local.get("text") or "").strip()
        if _is_runtime_chat_noise(local_text):
            continue
        rows.append(normalized_local)
    rows.sort(key=lambda row: str(row.get("ts") or ""))
    seen_keys: Set[tuple[str, str, str, str, str]] = set()
    deduped_reversed: List[Dict[str, Any]] = []
    for row in reversed(rows):
        row_id = str(row.get("id") or "").strip()
        approval_marker = _approval_dedupe_marker(str(row.get("text") or ""))
        if approval_marker:
            # Collapse repeated approval banners (often emitted by multiple channels/loops).
            key = ("approval", approval_marker, "", "", "")
        elif row_id:
            # Stable IDs from inbox events should be unique; this prevents accidental
            # collapsing of repeated user messages with identical text.
            key = ("id", row_id, "", "", "")
        else:
            key = (
                str(row.get("role") or ""),
                str(row.get("channel") or ""),
                str(row.get("run_id") or ""),
                str(row.get("text") or ""),
                str(row.get("ts") or "") + "|" + _skill_replay_key(row),
            )
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped_reversed.append(row)
    rows = list(reversed(deduped_reversed))
    if len(rows) > limit:
        rows = rows[-limit:]
    return rows


def format_chat_rows(rows: List[Dict[str, Any]]) -> List[str]:
    if not rows:
        return []

    out: List[str] = []
    show_meta = _chat_show_meta_enabled()
    compact_mode = os.getenv("ORION_CLI_CHAT_COMPACT", "0").strip().lower() in {"1", "true", "yes", "on"}
    for idx, row in enumerate(rows):
        ts_short = _inbox_ts(row.get("ts"))
        role = str(row.get("role") or "system").lower()
        if role == "you":
            speaker = accent("You")
        elif role == "orion":
            speaker = strong("Empyralis")
        else:
            speaker = muted("System")
        channel = str(row.get("channel") or "terminal")
        run_short = str(row.get("run_id") or "")[:8]
        suffix = muted(f" [{channel}{' ' + run_short if run_short else ''}]")
        raw_original = str(row.get("text") or "")
        raw_text = flatten_text(raw_original)
        if role == "orion":
            text = preview_text(raw_text, 220) if compact_mode else _assistant_block_text(raw_original)
        else:
            text = preview_text(raw_text, 220)
        show_ts = os.getenv("ORION_CLI_CHAT_TIMESTAMPS", "0").strip().lower() in {"1", "true", "yes", "on"}
        replay_line = _format_skill_replay_line(row)
        prefix = f"{ts_short}  " if show_ts else ""
        if role == "you":
            if show_meta:
                out.append(f"{prefix}{speaker}{suffix}")
                if text:
                    out.append(f"  {text}")
                if replay_line:
                    out.append(f"  {muted(replay_line)}")
            else:
                for line in (text.splitlines() or [text]):
                    clean = str(line).strip()
                    if clean:
                        out.extend(_user_panel_block(clean))
                if replay_line:
                    out.append(muted(replay_line))
        elif role == "orion":
            if show_meta:
                out.append(f"{prefix}{speaker}{suffix}")
                if text:
                    out.append(f"  {text}")
                if replay_line:
                    out.append(f"  {muted(replay_line)}")
            else:
                if text:
                    if _assistant_panel_enabled():
                        out.extend(_assistant_panel_block(text))
                    else:
                        if "\n" in raw_original:
                            for line in raw_original.splitlines():
                                clean = str(line).strip()
                                if clean:
                                    out.append(clean)
                        else:
                            out.append(f"{text}")
                if replay_line:
                    out.append(muted(replay_line))
        else:
            if show_meta:
                out.append(f"{prefix}{speaker}{suffix}")
                if text:
                    out.append(f"  {muted(text)}")
                if replay_line:
                    out.append(f"  {muted(replay_line)}")
            else:
                if text:
                    if "\n" in raw_original:
                        for line in raw_original.splitlines():
                            clean = str(line).strip()
                            if clean:
                                out.append(muted(clean))
                    else:
                        out.append(muted(text))
                if replay_line:
                    out.append(muted(replay_line))
        if idx < len(rows) - 1:
            out.append("")
    return out


def format_chat_transcript(
    items: List[Dict[str, Any]],
    local_entries: Optional[List[Dict[str, Any]]] = None,
    limit: int = 40,
) -> str:
    rows = collect_chat_rows(items=items, local_entries=local_entries, limit=limit)
    return "\n".join(format_chat_rows(rows))


def channel_health_rollup(
    items: List[Dict[str, Any]],
    window_seconds: int = 300,
) -> Dict[str, Dict[str, Any]]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=max(30, int(window_seconds)))
    out: Dict[str, Dict[str, Any]] = {}
    for item in items:
        channel = str(item.get("channel") or "unknown").strip().lower() or "unknown"
        rec = out.setdefault(
            channel,
            {
                "timeout_count": 0,
                "error_count": 0,
                "last_error": "",
                "last_error_ts": "",
                "last_activity_ts": "",
                "state": "idle",
            },
        )
        text = str(item.get("text") or "").strip()
        event_type = str(item.get("event_type") or "").strip().lower()
        direction = str(item.get("direction") or "").strip().lower()
        ts = _parse_event_ts(item.get("ts"))
        ts_recent = bool(ts and ts >= cutoff)

        if direction in {"inbound", "outbound"}:
            rec["last_activity_ts"] = str(item.get("ts") or rec.get("last_activity_ts") or "")

        is_error = event_type == "error"
        timeout_like = _is_timeout_noise(text.lower())
        if is_error:
            if timeout_like:
                continue
            rec["last_error"] = text or rec.get("last_error") or ""
            rec["last_error_ts"] = str(item.get("ts") or rec.get("last_error_ts") or "")
            if ts_recent:
                rec["error_count"] = int(rec.get("error_count") or 0) + 1

    for rec in out.values():
        if int(rec.get("error_count") or 0) > 0:
            rec["state"] = "degraded"
        elif rec.get("last_activity_ts"):
            rec["state"] = "connected"
        else:
            rec["state"] = "idle"
    return out


def format_channel_health_rollup(items: List[Dict[str, Any]], window_seconds: int = 300) -> str:
    rollup = channel_health_rollup(items, window_seconds=window_seconds)
    if not rollup:
        return "channels: idle"
    parts: List[str] = []
    for channel in sorted(rollup.keys()):
        rec = rollup[channel]
        state = str(rec.get("state") or "idle")
        timeout_count = int(rec.get("timeout_count") or 0)
        error_count = int(rec.get("error_count") or 0)
        if state == "degraded":
            if timeout_count > 0:
                parts.append(f"{channel}: {warn('⬢ degraded')} (timeouts x{timeout_count})")
            else:
                parts.append(f"{channel}: {warn('⬢ degraded')} (errors x{error_count})")
        elif state == "connected":
            parts.append(f"{channel}: {ok('⬢ connected')}")
        else:
            parts.append(f"{channel}: {muted('⬡ idle')}")
    return " | ".join(parts)
