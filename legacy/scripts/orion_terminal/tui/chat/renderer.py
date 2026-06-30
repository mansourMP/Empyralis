from __future__ import annotations

import re
import shutil
import sys
from typing import Any, Dict, List


_CHAT_DIVIDER = "────────────────────────────────────────────────────────────────────────"
_LAST_FRAME_RENDER_ROWS = 0
_LAST_FRAME_RENDER_COLS = 0
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", str(text or ""))


def _terminal_columns() -> int:
    try:
        cols = int(shutil.get_terminal_size((96, 24)).columns)
    except Exception:
        cols = 96
    return max(20, cols)


def _render_rows_for_text(text: str) -> int:
    cols = _terminal_columns()
    rows = 0
    for raw_line in str(text or "").split("\n"):
        visual = _strip_ansi(raw_line)
        # Rows consumed by wrapped line in current terminal width.
        line_rows = (max(1, len(visual)) - 1) // cols + 1
        rows += max(1, line_rows)
    return max(1, rows)


def render_chat_frame(
    *,
    title: str,
    status_parts: List[str],
    channel_health: str,
    rows: List[Dict[str, Any]],
    row_formatter,
    muted_fn,
    show_footer_hints: bool,
    commands_label: str,
    send_hint: str,
    stream_status_line: str = "",
    footer_status_line: str = "",
    composer_hint_line: str = "",
    composer_line: str = "",
) -> str:
    lines: List[str] = []
    # Keep chat feed visually lean by default: one compact header + transcript.
    lines.append(title)
    summary = " | ".join([str(part).strip() for part in status_parts if str(part).strip()])
    if summary:
        lines.append(muted_fn(summary))
    if channel_health:
        lines.append(channel_health)
    lines.append(_CHAT_DIVIDER)
    lines.extend(row_formatter(rows))
    lines.append(_CHAT_DIVIDER)
    if show_footer_hints:
        if commands_label:
            lines.append(muted_fn(commands_label))
        if send_hint:
            lines.append(muted_fn(send_hint))
    if stream_status_line:
        lines.append(muted_fn(stream_status_line))
    if footer_status_line:
        lines.append(footer_status_line)
    if composer_hint_line:
        lines.append(muted_fn(composer_hint_line))
    if composer_line:
        lines.append(composer_line)
    return "\n".join(lines)


def render_chat_frame_output(
    ui: Any,
    frame: str,
    *,
    full_redraw_enabled: bool,
    clear_screen_enabled: bool,
) -> None:
    global _LAST_FRAME_RENDER_ROWS
    global _LAST_FRAME_RENDER_COLS
    if sys.stdout.isatty():
        cols_now = _terminal_columns()
        # Always clear the active prompt line before repainting chat frame.
        sys.stdout.write("\r\033[K")
        terminal_resized = _LAST_FRAME_RENDER_COLS > 0 and _LAST_FRAME_RENDER_COLS != cols_now
        if full_redraw_enabled or terminal_resized:
            if clear_screen_enabled:
                sys.stdout.write("\033[2J\033[H")
            else:
                sys.stdout.write("\033[H\033[J")
        elif _LAST_FRAME_RENDER_ROWS > 0:
            # Repaint in place without hard clearing the whole terminal.
            # This prevents stacked duplicate frames when full redraw is off.
            sys.stdout.write(f"\033[{_LAST_FRAME_RENDER_ROWS}F\033[J")
        sys.stdout.flush()
    ui.note(frame)
    _LAST_FRAME_RENDER_ROWS = _render_rows_for_text(frame)
    _LAST_FRAME_RENDER_COLS = _terminal_columns()


def reset_chat_frame_renderer() -> None:
    global _LAST_FRAME_RENDER_ROWS
    global _LAST_FRAME_RENDER_COLS
    _LAST_FRAME_RENDER_ROWS = 0
    _LAST_FRAME_RENDER_COLS = 0
