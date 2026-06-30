from __future__ import annotations

import os
import re
import textwrap
from typing import List, Optional, Sequence, Set


def _trim(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    return text[: width - 3] + "..."


def _shape_markers(ascii_mode: bool) -> tuple[str, str]:
    if ascii_mode:
        return "*", "o"

    shape = os.getenv("ORION_CLI_SHAPE", "hex").strip().lower()
    if shape == "hex":
        return "⬢", "⬡"
    if shape == "diamond":
        return "◆", "◇"
    return "●", "○"


def _symbols(ascii_mode: bool) -> dict:
    if ascii_mode:
        return {
            "top": "+",
            "rail": "|",
            "bottom": "`",
            "prompt": ">",
            "selected": "*",
            "unselected": "o",
            "divider": "|",
        }
    return {
        "top": "┌",
        "rail": "│",
        "bottom": "└",
        "prompt": "▸",
        "selected": "●",
        "unselected": "○",
        "divider": "│",
    }


def _safe_addstr(stdscr, y: int, x: int, text: str, attr: int = 0) -> None:
    import curses

    height, width = stdscr.getmaxyx()
    if y < 0 or y >= height or x < 0 or x >= width:
        return
    max_width = max(0, width - x - 1)
    if max_width <= 0:
        return
    try:
        stdscr.addstr(y, x, _trim(text, max_width), attr)
    except curses.error:
        return


def _wrap_lines(text: str, width: int, max_lines: int) -> List[str]:
    if width <= 0 or max_lines <= 0:
        return []
    lines: List[str] = []
    chunks = str(text or "").splitlines() or [""]
    for chunk in chunks:
        wrapped = textwrap.wrap(
            chunk,
            width=width,
            break_long_words=True,
            break_on_hyphens=False,
        )
        if not wrapped:
            wrapped = [""]
        for line in wrapped:
            lines.append(line)
            if len(lines) >= max_lines:
                return lines
    return lines


def _light_terminal() -> bool:
    forced = os.getenv("ORION_CLI_TERMINAL_THEME", "auto").strip().lower()
    if forced == "light":
        return True
    if forced == "dark":
        return False

    colorfgbg = os.getenv("COLORFGBG", "")
    if not colorfgbg:
        return forced != "dark"
    parts = [p.strip() for p in re.split(r"[;:]", colorfgbg) if p.strip()]
    for raw in reversed(parts):
        if raw.isdigit():
            return int(raw) in {7, 15}
    return False


def _mouse_scroll_enabled() -> bool:
    return os.getenv("ORION_CLI_MOUSE_SCROLL", "0") == "1"


def _selector_advanced_hints_enabled() -> bool:
    raw = os.getenv("ORION_CLI_SELECTOR_ADVANCED_HINTS", "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _inline_option_notes() -> bool:
    # Keep selector geometry stable: notes are always inline with active option.
    return True


def _resolve_attrs(stdscr, light_mode: bool) -> dict:
    import curses

    profile = os.getenv("ORION_CLI_COLOR_PROFILE", "purple").strip().lower()
    attrs = {
        "title": curses.A_BOLD,
        "prompt": curses.A_BOLD,
        "active": curses.A_BOLD,
        "text": curses.A_NORMAL,
        "hint": curses.A_NORMAL if light_mode else curses.A_DIM,
        "divider": curses.A_NORMAL if light_mode else curses.A_DIM,
        "selected": curses.A_BOLD,
    }

    if not curses.has_colors():
        return attrs

    try:
        curses.start_color()
        curses.use_default_colors()
        # 1 = accent, 2 = success, 3 = optional hint.
        curses.init_pair(2, curses.COLOR_GREEN, -1)
        attrs["selected"] = curses.A_BOLD | curses.color_pair(2)

        if profile == "purple":
            curses.init_pair(1, curses.COLOR_MAGENTA, -1)
            attrs["title"] = curses.A_BOLD | curses.color_pair(1)
            attrs["prompt"] = curses.A_BOLD | curses.color_pair(1)
            attrs["active"] = curses.A_BOLD | curses.color_pair(1)
            hint_color = curses.COLOR_CYAN if not light_mode else curses.COLOR_BLUE
            curses.init_pair(3, hint_color, -1)
            attrs["hint"] = (curses.A_NORMAL if light_mode else curses.A_DIM) | curses.color_pair(3)
            attrs["divider"] = (curses.A_NORMAL if light_mode else curses.A_DIM) | curses.color_pair(3)
        else:
            # Professional default stays neutral; only success states are colorized.
            attrs["title"] = curses.A_BOLD
            attrs["prompt"] = curses.A_BOLD
            attrs["active"] = curses.A_BOLD
            attrs["hint"] = curses.A_NORMAL if light_mode else curses.A_DIM
            attrs["divider"] = curses.A_NORMAL if light_mode else curses.A_DIM
    except Exception:
        return attrs

    return attrs


def _render_lines(
    stdscr,
    title: str,
    prompt: str,
    options: Sequence[str],
    notes: Optional[Sequence[str]],
    cursor: int,
    selected: Set[int],
    multi: bool,
) -> None:
    import curses

    stdscr.erase()
    height, width = stdscr.getmaxyx()
    content_width = max(20, width - 1)

    ascii_mode = os.getenv("ORION_CLI_ASCII", "0") == "1"
    light_mode = _light_terminal()
    symbols = _symbols(ascii_mode)
    attrs = _resolve_attrs(stdscr, light_mode)
    selection_style = os.getenv("ORION_CLI_SELECTION_STYLE", "bold").strip().lower()
    rail = symbols["rail"]
    tail = symbols["bottom"]
    selected_marker, unselected_marker = _shape_markers(ascii_mode)
    hint_attr = attrs["hint"]
    text_attr = attrs["text"]
    inline_notes = _inline_option_notes()
    if selection_style == "reverse" and not light_mode:
        active_attr = curses.A_REVERSE
    elif selection_style == "standout":
        active_attr = curses.A_STANDOUT
    else:
        active_attr = attrs["active"]

    list_top = 4
    left_width = content_width

    _safe_addstr(stdscr, 0, 0, _trim(f"{symbols['top']}  {title}", left_width), attrs["title"])
    _safe_addstr(stdscr, 1, 0, rail, attrs["divider"])
    _safe_addstr(stdscr, 2, 0, rail, attrs["divider"])
    _safe_addstr(stdscr, 2, 3, symbols["prompt"], attrs["prompt"])
    _safe_addstr(stdscr, 2, 6, _trim(prompt, max(10, left_width - 6)), text_attr)
    _safe_addstr(stdscr, 3, 0, rail, attrs["divider"])

    # Keep the panel compact and leave only footer rows reserved.
    reserved_bottom = 2  # controls row + bottom tail row
    available_content_rows = max(1, height - list_top - reserved_bottom)
    max_list_height = max(1, available_content_rows)
    desired_list_height = len(options) if options else 1
    list_height = max(1, min(max_list_height, desired_list_height))

    top = 0
    if cursor >= list_height:
        top = cursor - list_height + 1
    if top < 0:
        top = 0
    y = list_top
    last_option_y = list_top
    list_bottom = list_top + list_height
    rendered_options = 0
    for idx in range(top, min(len(options), top + list_height)):
        if y >= list_bottom:
            break
        rendered_options += 1
        last_option_y = y
        marker = selected_marker if idx == cursor else unselected_marker
        check = ""
        line_attr = text_attr
        if multi:
            check = "[x]" if idx in selected else "[ ]"
            prefix = f"{rail}  {marker} {check} "
            if idx in selected:
                line_attr = attrs["selected"]
        else:
            prefix = f"{rail}  {marker} "
        line = prefix + options[idx]
        if inline_notes and idx == cursor and notes and 0 <= idx < len(notes):
            active_note = str(notes[idx] or "").strip()
            if active_note:
                compact_note = _trim(active_note, max(10, left_width // 2))
                line = f"{line}  ({compact_note})"

        if idx == cursor:
            _safe_addstr(stdscr, y, 0, _trim(line, left_width), active_attr)
        else:
            _safe_addstr(stdscr, y, 0, _trim(line, left_width), line_attr)
        y += 1


    # Show scroll indicators so long lists are discoverable.
    if top > 0:
        _safe_addstr(stdscr, list_top, max(2, left_width - 8), "↑ more", hint_attr)
    if top + rendered_options < len(options):
        _safe_addstr(stdscr, last_option_y, max(2, left_width - 8), "↓ more", hint_attr)

    footer = "Up/Down or j/k  Enter select  PgUp/PgDn page  Esc cancel"
    if multi:
        footer = "Up/Down or j/k  Space toggle  Enter confirm  PgUp/PgDn page  Esc cancel"
    if _selector_advanced_hints_enabled():
        footer += "  g top  G bottom"
    controls_y = y
    tail_y = controls_y + 1
    _safe_addstr(stdscr, controls_y, 0, rail, attrs["divider"])
    _safe_addstr(stdscr, controls_y, 2, _trim(footer, max(10, left_width - 2)), hint_attr)
    _safe_addstr(stdscr, tail_y, 0, tail, attrs["divider"])

    stdscr.refresh()


def select_menu(
    title: str,
    prompt: str,
    options: Sequence[str],
    notes: Optional[Sequence[str]] = None,
    default_index: int = 0,
) -> Optional[int]:
    if not options:
        return None

    import curses

    def _inner(stdscr):
        curses.curs_set(0)
        stdscr.keypad(True)
        mouse_enabled = _mouse_scroll_enabled()
        if mouse_enabled:
            try:
                curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
            except Exception:
                mouse_enabled = False
        idx = max(0, min(default_index, len(options) - 1))

        while True:
            _render_lines(stdscr, title, prompt, options, notes, idx, set(), multi=False)
            ch = stdscr.getch()
            if ch in (curses.KEY_UP, ord("k")):
                idx = max(0, idx - 1)
            elif ch in (curses.KEY_DOWN, ord("j")):
                idx = min(len(options) - 1, idx + 1)
            elif ch in (curses.KEY_PPAGE, ord("p")):
                page = max(5, stdscr.getmaxyx()[0] - 10)
                idx = max(0, idx - page)
            elif ch in (curses.KEY_NPAGE, ord("n")):
                page = max(5, stdscr.getmaxyx()[0] - 10)
                idx = min(len(options) - 1, idx + page)
            elif ch in (curses.KEY_HOME,):
                idx = 0
            elif ch in (curses.KEY_END,):
                idx = len(options) - 1
            elif ch == ord("g"):
                idx = 0
            elif ch == ord("G"):
                idx = len(options) - 1
            elif ch in (10, 13, curses.KEY_ENTER):
                return idx
            elif mouse_enabled and ch == curses.KEY_MOUSE:
                try:
                    _, _, _, _, bstate = curses.getmouse()
                    if bstate & getattr(curses, "BUTTON4_PRESSED", 0):
                        idx = max(0, idx - 1)
                    elif bstate & getattr(curses, "BUTTON5_PRESSED", 0):
                        idx = min(len(options) - 1, idx + 1)
                except Exception:
                    pass
            elif ch in (27, ord("q")):
                return None

    try:
        return curses.wrapper(_inner)
    except Exception:
        return None


def multi_select_menu(
    title: str,
    prompt: str,
    options: Sequence[str],
    notes: Optional[Sequence[str]] = None,
    default_indexes: Optional[Sequence[int]] = None,
) -> Optional[List[int]]:
    if not options:
        return []

    import curses

    defaults = set(default_indexes or [])

    def _inner(stdscr):
        curses.curs_set(0)
        stdscr.keypad(True)
        mouse_enabled = _mouse_scroll_enabled()
        if mouse_enabled:
            try:
                curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
            except Exception:
                mouse_enabled = False
        idx = 0
        selected = {i for i in defaults if 0 <= i < len(options)}

        while True:
            _render_lines(stdscr, title, prompt, options, notes, idx, selected, multi=True)
            ch = stdscr.getch()
            if ch in (curses.KEY_UP, ord("k")):
                idx = max(0, idx - 1)
            elif ch in (curses.KEY_DOWN, ord("j")):
                idx = min(len(options) - 1, idx + 1)
            elif ch in (curses.KEY_PPAGE, ord("p")):
                page = max(5, stdscr.getmaxyx()[0] - 10)
                idx = max(0, idx - page)
            elif ch in (curses.KEY_NPAGE, ord("n")):
                page = max(5, stdscr.getmaxyx()[0] - 10)
                idx = min(len(options) - 1, idx + page)
            elif ch in (curses.KEY_HOME,):
                idx = 0
            elif ch in (curses.KEY_END,):
                idx = len(options) - 1
            elif ch == ord("g"):
                idx = 0
            elif ch == ord("G"):
                idx = len(options) - 1
            elif ch == ord(" "):
                if idx in selected:
                    selected.remove(idx)
                else:
                    selected.add(idx)
            elif ch == ord("a"):
                selected = set(range(len(options)))
            elif ch == ord("c"):
                selected.clear()
            elif mouse_enabled and ch == curses.KEY_MOUSE:
                try:
                    _, _, _, _, bstate = curses.getmouse()
                    if bstate & getattr(curses, "BUTTON4_PRESSED", 0):
                        idx = max(0, idx - 1)
                    elif bstate & getattr(curses, "BUTTON5_PRESSED", 0):
                        idx = min(len(options) - 1, idx + 1)
                except Exception:
                    pass
            elif ch in (10, 13, curses.KEY_ENTER):
                return sorted(selected)
            elif ch in (27, ord("q")):
                return None

    try:
        return curses.wrapper(_inner)
    except Exception:
        return None
