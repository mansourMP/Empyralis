from __future__ import annotations

import shutil
import os
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Set


ANSI_ESCAPE_RE = __import__("re").compile(r"\x1b\[[0-9;]*m")


@dataclass(frozen=True)
class MenuOption:
    label: str
    note: str = ""


@dataclass(frozen=True)
class PromptStyle:
    top: str
    rail: str
    bottom: str
    prompt_mark: str
    bullet_on: str
    bullet_off: str
    muted: Callable[[str], str]
    accent: Callable[[str], str]
    strong: Callable[[str], str]
    rail_color: Callable[[str], str]
    active_color: Callable[[str], str]
    advanced_hints: bool = False


def _display_width(text: str) -> int:
    plain = ANSI_ESCAPE_RE.sub("", str(text or ""))
    return len(plain)


def _rendered_rows(text: str, terminal_width: int) -> int:
    width = max(1, terminal_width)
    visible = max(1, _display_width(text))
    return ((visible - 1) // width) + 1


def _cap(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    return text[: width - 1].rstrip() + "…"


def _sync_window(offset: int, visible_len: int, cursor_pos: int, page_len: int) -> int:
    page_len = max(1, page_len)
    max_offset = max(0, ((max(visible_len, 1) - 1) // page_len) * page_len)
    offset = max(0, min(offset, max_offset))
    if cursor_pos < offset:
        return (cursor_pos // page_len) * page_len
    if cursor_pos >= offset + page_len:
        return (cursor_pos // page_len) * page_len
    return offset


def _visible_indexes(options: Sequence[MenuOption], filter_query: str) -> List[int]:
    if not filter_query:
        return list(range(len(options)))
    needle = filter_query.casefold()
    out: List[int] = []
    for idx, opt in enumerate(options):
        hay = f"{opt.label} {opt.note}".casefold()
        if needle in hay:
            out.append(idx)
    return out


def _inline_option_notes() -> bool:
    # Keep selector geometry stable: notes are always inline with active option.
    return True


class TextPromptEngine:
    def __init__(
        self,
        *,
        style: PromptStyle,
        read_key: Optional[Callable[[], str]],
        raw_keys: bool,
        line_input_mode: bool,
        interactive_tty: bool,
        page_size_base: int = 12,
    ) -> None:
        self.style = style
        self.read_key = read_key
        self.raw_keys = bool(raw_keys and read_key is not None)
        self.line_input_mode = line_input_mode
        self.interactive_tty = interactive_tty
        self.page_size_base = max(5, int(page_size_base))

    def select(
        self,
        *,
        title: str,
        prompt: str,
        options: Sequence[MenuOption],
        default_index: int = 0,
    ) -> int:
        if not options:
            return 0

        cursor = max(0, min(default_index, len(options) - 1))
        filter_query = ""
        line_fallback_allowed = self.line_input_mode or not self.interactive_tty or not self.raw_keys
        rendered_lines = 0
        offset = 0
        raw_keys = self.raw_keys
        inline_notes = _inline_option_notes()

        while True:
            if raw_keys and rendered_lines > 0:
                print(f"\033[{rendered_lines}A\033[J", end="", flush=True)

            visible = _visible_indexes(options, filter_query)
            if visible and cursor not in visible:
                cursor = visible[0]
            cursor_pos = visible.index(cursor) if visible else 0
            terminal_size = shutil.get_terminal_size((96, 24))
            terminal_width = terminal_size.columns
            terminal_height = terminal_size.lines
            page_size = max(5, min(self.page_size_base, max(5, terminal_height - 10)))
            offset = _sync_window(offset, len(visible), cursor_pos, page_size)
            content_width = max(34, min(96, terminal_width - 4))
            line_count = 0

            def _emit(line: str = "") -> None:
                nonlocal line_count
                print(line)
                line_count += _rendered_rows(line, terminal_width)

            _emit(f"{self.style.rail_color(self.style.top)}  {self.style.strong(title)}")
            _emit(self.style.rail_color(self.style.rail))
            prompt_text = _cap(prompt, max(10, content_width - 6))
            _emit(f"{self.style.rail_color(self.style.rail)}  {self.style.accent(self.style.prompt_mark)}  {prompt_text}")
            _emit(self.style.rail_color(self.style.rail))
            if filter_query:
                filter_text = _cap(filter_query, max(10, content_width - 10))
                _emit(f"{self.style.rail_color(self.style.rail)}  filter: {filter_text}")
                _emit(self.style.rail_color(self.style.rail))

            end = min(len(visible), offset + page_size)
            for pos in range(offset, end):
                idx = visible[pos]
                option = options[idx]
                marker = self.style.bullet_on if idx == cursor else self.style.bullet_off
                line = f"{marker} {option.label}"
                if inline_notes and idx == cursor and option.note:
                    note = _cap(option.note, max(12, content_width // 2))
                    line = f"{line}  ({note})"
                rendered = _cap(line, content_width - 2)
                if idx == cursor:
                    rendered = self.style.active_color(rendered)
                _emit(f"{self.style.rail_color(self.style.rail)}  {rendered}")
            if not visible:
                _emit(f"{self.style.rail_color(self.style.rail)}  no matches")

            total_pages = ((max(len(visible), 1) - 1) // page_size) + 1
            current_page = (offset // page_size) + 1
            if raw_keys:
                controls = "Up/Down or j/k  Enter select  PgUp/PgDn page  Esc cancel"
                if self.style.advanced_hints:
                    controls += "  / filter  r reset"
            else:
                controls = "Type number + Enter  q cancel"
                if self.style.advanced_hints:
                    controls += "  / filter  r reset"
            if len(visible) > page_size:
                controls += f"  |  page {current_page}/{total_pages}"
            _emit(f"{self.style.rail_color(self.style.rail)}  {_cap(controls, max(10, content_width - 2))}")
            if not raw_keys and line_fallback_allowed:
                selection_hint = f"Selection [1-{len(options)}] (Enter={cursor + 1}):"
                _emit(f"{self.style.rail_color(self.style.rail)}  {_cap(selection_hint, max(10, content_width - 2))}")
            _emit(self.style.rail_color(self.style.bottom))
            rendered_lines = line_count

            if raw_keys:
                try:
                    key = self.read_key() if self.read_key else ""
                except Exception:
                    raw_keys = False
                    line_fallback_allowed = True
                    rendered_lines = 0
                    continue

                if key == "enter":
                    if visible:
                        return cursor
                    continue
                if key in {"up", "k"} and visible:
                    cursor_pos = max(0, cursor_pos - 1)
                    cursor = visible[cursor_pos]
                    offset = _sync_window(offset, len(visible), cursor_pos, page_size)
                    continue
                if key in {"down", "j"} and visible:
                    cursor_pos = min(len(visible) - 1, cursor_pos + 1)
                    cursor = visible[cursor_pos]
                    offset = _sync_window(offset, len(visible), cursor_pos, page_size)
                    continue
                if key in {"pgdn", "n"} and len(visible) > page_size and visible:
                    cursor_pos = min(len(visible) - 1, cursor_pos + page_size)
                    cursor = visible[cursor_pos]
                    offset = _sync_window(offset + page_size, len(visible), cursor_pos, page_size)
                    continue
                if key in {"pgup", "p"} and len(visible) > page_size and visible:
                    cursor_pos = max(0, cursor_pos - page_size)
                    cursor = visible[cursor_pos]
                    offset = _sync_window(offset - page_size, len(visible), cursor_pos, page_size)
                    continue
                if key in {"esc", "q"}:
                    return default_index
                if key == "/":
                    print("")
                    filter_query = input("Filter query (blank clears): ").strip()
                    offset = 0
                    rendered_lines = 0
                    continue
                if key.lower() == "r":
                    filter_query = ""
                    offset = 0
                    continue
                if key.isdigit():
                    val = int(key)
                    if 1 <= val <= len(options):
                        return val - 1
                continue

            if not line_fallback_allowed:
                line_fallback_allowed = True
                continue

            raw = input().strip()
            if not raw:
                if visible:
                    return cursor
                continue
            lowered = raw.lower()
            if raw == "/":
                filter_query = input("Filter query (blank clears): ").strip()
                offset = 0
                continue
            if lowered.startswith("/") and len(lowered) > 1:
                filter_query = raw[1:].strip()
                offset = 0
                continue
            if lowered == "r":
                filter_query = ""
                offset = 0
                continue
            if lowered == "n" and len(visible) > page_size and visible:
                cursor_pos = min(len(visible) - 1, cursor_pos + page_size)
                cursor = visible[cursor_pos]
                offset = _sync_window(offset + page_size, len(visible), cursor_pos, page_size)
                continue
            if lowered == "p" and len(visible) > page_size and visible:
                cursor_pos = max(0, cursor_pos - page_size)
                cursor = visible[cursor_pos]
                offset = _sync_window(offset - page_size, len(visible), cursor_pos, page_size)
                continue
            if lowered in {"k", "up", "u"} and visible:
                cursor_pos = max(0, cursor_pos - 1)
                cursor = visible[cursor_pos]
                offset = _sync_window(offset, len(visible), cursor_pos, page_size)
                continue
            if lowered in {"j", "down", "d"} and visible:
                cursor_pos = min(len(visible) - 1, cursor_pos + 1)
                cursor = visible[cursor_pos]
                offset = _sync_window(offset, len(visible), cursor_pos, page_size)
                continue
            if lowered in {"q", "esc", "cancel"}:
                return default_index
            if raw == "g":
                if visible:
                    cursor = visible[0]
                offset = 0
                continue
            if raw == "G":
                if visible:
                    cursor = visible[-1]
                    offset = _sync_window((len(visible) // page_size) * page_size, len(visible), len(visible) - 1, page_size)
                continue
            if raw.isdigit():
                val = int(raw)
                if 1 <= val <= len(options):
                    return val - 1
            print(f"{self.style.rail_color(self.style.rail)}  -> invalid choice")

    def multi_select(
        self,
        *,
        title: str,
        prompt: str,
        options: Sequence[MenuOption],
        default_indexes: Optional[Sequence[int]] = None,
    ) -> List[int]:
        if not options:
            return []

        defaults = set(default_indexes or [])
        cursor = max(0, min((min(defaults) if defaults else 0), len(options) - 1))
        selected = {idx for idx in defaults if 0 <= idx < len(options)}
        filter_query = ""
        line_fallback_allowed = self.line_input_mode or not self.interactive_tty or not self.raw_keys
        rendered_lines = 0
        offset = 0
        raw_keys = self.raw_keys
        inline_notes = _inline_option_notes()

        while True:
            if raw_keys and rendered_lines > 0:
                print(f"\033[{rendered_lines}A\033[J", end="", flush=True)

            visible = _visible_indexes(options, filter_query)
            if visible and cursor not in visible:
                cursor = visible[0]
            cursor_pos = visible.index(cursor) if visible else 0
            terminal_size = shutil.get_terminal_size((96, 24))
            terminal_width = terminal_size.columns
            terminal_height = terminal_size.lines
            page_size = max(5, min(self.page_size_base, max(5, terminal_height - 10)))
            offset = _sync_window(offset, len(visible), cursor_pos, page_size)
            content_width = max(34, min(96, terminal_width - 4))
            line_count = 0

            def _emit(line: str = "") -> None:
                nonlocal line_count
                print(line)
                line_count += _rendered_rows(line, terminal_width)

            _emit(f"{self.style.rail_color(self.style.top)}  {self.style.strong(title)}")
            _emit(self.style.rail_color(self.style.rail))
            prompt_text = _cap(prompt, max(10, content_width - 6))
            _emit(f"{self.style.rail_color(self.style.rail)}  {self.style.accent(self.style.prompt_mark)}  {prompt_text}")
            _emit(self.style.rail_color(self.style.rail))
            if filter_query:
                filter_text = _cap(filter_query, max(10, content_width - 10))
                _emit(f"{self.style.rail_color(self.style.rail)}  filter: {filter_text}")
                _emit(self.style.rail_color(self.style.rail))

            end = min(len(visible), offset + page_size)
            for pos in range(offset, end):
                idx = visible[pos]
                option = options[idx]
                marker = self.style.bullet_on if idx == cursor else self.style.bullet_off
                check = "x" if idx in selected else " "
                line = f"{marker} [{check}] {option.label}"
                if inline_notes and idx == cursor and option.note:
                    note = _cap(option.note, max(12, content_width // 2))
                    line = f"{line}  ({note})"
                rendered = _cap(line, content_width - 2)
                if idx == cursor:
                    rendered = self.style.active_color(rendered)
                elif idx in selected:
                    rendered = self.style.strong(rendered)
                _emit(f"{self.style.rail_color(self.style.rail)}  {rendered}")
            if not visible:
                _emit(f"{self.style.rail_color(self.style.rail)}  no matches")

            total_pages = ((max(len(visible), 1) - 1) // page_size) + 1
            current_page = (offset // page_size) + 1
            if raw_keys:
                controls = "Up/Down or j/k  Space toggle  Enter confirm  PgUp/PgDn page  Esc cancel"
                if self.style.advanced_hints:
                    controls += "  / filter  r reset  a all  c clear"
            else:
                controls = "Type number to toggle  Enter confirm  q cancel"
                if self.style.advanced_hints:
                    controls += "  / filter  r reset  a all  c clear"
            if len(visible) > page_size:
                controls += f"  |  page {current_page}/{total_pages}"
            _emit(f"{self.style.rail_color(self.style.rail)}  {_cap(controls, max(10, content_width - 2))}")
            if not raw_keys and line_fallback_allowed:
                _emit(f"{self.style.rail_color(self.style.rail)}  Selection:")
            _emit(self.style.rail_color(self.style.bottom))
            rendered_lines = line_count

            if raw_keys:
                try:
                    key = self.read_key() if self.read_key else ""
                except Exception:
                    raw_keys = False
                    line_fallback_allowed = True
                    rendered_lines = 0
                    continue
                if key == "enter":
                    return sorted(selected)
                if key in {"up", "k"} and visible:
                    cursor_pos = max(0, cursor_pos - 1)
                    cursor = visible[cursor_pos]
                    offset = _sync_window(offset, len(visible), cursor_pos, page_size)
                    continue
                if key in {"down", "j"} and visible:
                    cursor_pos = min(len(visible) - 1, cursor_pos + 1)
                    cursor = visible[cursor_pos]
                    offset = _sync_window(offset, len(visible), cursor_pos, page_size)
                    continue
                if key in {" ", "t"} and visible:
                    if cursor in selected:
                        selected.remove(cursor)
                    else:
                        selected.add(cursor)
                    continue
                if key.lower() == "a":
                    selected = set(visible) if filter_query else set(range(len(options)))
                    continue
                if key.lower() == "c":
                    selected.clear()
                    continue
                if key in {"pgdn", "n"} and len(visible) > page_size and visible:
                    cursor_pos = min(len(visible) - 1, cursor_pos + page_size)
                    cursor = visible[cursor_pos]
                    offset = _sync_window(offset + page_size, len(visible), cursor_pos, page_size)
                    continue
                if key in {"pgup", "p"} and len(visible) > page_size and visible:
                    cursor_pos = max(0, cursor_pos - page_size)
                    cursor = visible[cursor_pos]
                    offset = _sync_window(offset - page_size, len(visible), cursor_pos, page_size)
                    continue
                if key in {"esc", "q"}:
                    return sorted([idx for idx in defaults if 0 <= idx < len(options)])
                if key == "/":
                    print("")
                    filter_query = input("Filter query (blank clears): ").strip()
                    offset = 0
                    rendered_lines = 0
                    continue
                if key.lower() == "r":
                    filter_query = ""
                    offset = 0
                    continue
                if key.isdigit():
                    idx = int(key) - 1
                    if 0 <= idx < len(options):
                        if idx in selected:
                            selected.remove(idx)
                        else:
                            selected.add(idx)
                        cursor = idx
                    continue
                continue

            if not line_fallback_allowed:
                line_fallback_allowed = True
                continue

            raw = input().strip()
            if not raw:
                return sorted(selected)
            lowered = raw.lower()
            if raw == "/":
                filter_query = input("Filter query (blank clears): ").strip()
                offset = 0
                continue
            if lowered.startswith("/") and len(lowered) > 1:
                filter_query = raw[1:].strip()
                offset = 0
                continue
            if lowered == "r":
                filter_query = ""
                offset = 0
                continue
            if lowered in {"j", "down", "d"} and visible:
                cursor_pos = min(len(visible) - 1, cursor_pos + 1)
                cursor = visible[cursor_pos]
                offset = _sync_window(offset, len(visible), cursor_pos, page_size)
                continue
            if lowered in {"k", "up", "u"} and visible:
                cursor_pos = max(0, cursor_pos - 1)
                cursor = visible[cursor_pos]
                offset = _sync_window(offset, len(visible), cursor_pos, page_size)
                continue
            if lowered == "t" and visible:
                if cursor in selected:
                    selected.remove(cursor)
                else:
                    selected.add(cursor)
                continue
            if lowered == "a":
                selected = set(visible) if filter_query else set(range(len(options)))
                continue
            if lowered == "c":
                selected.clear()
                continue
            if lowered == "n" and len(visible) > page_size and visible:
                cursor_pos = min(len(visible) - 1, cursor_pos + page_size)
                cursor = visible[cursor_pos]
                offset = _sync_window(offset + page_size, len(visible), cursor_pos, page_size)
                continue
            if lowered == "p" and len(visible) > page_size and visible:
                cursor_pos = max(0, cursor_pos - page_size)
                cursor = visible[cursor_pos]
                offset = _sync_window(offset - page_size, len(visible), cursor_pos, page_size)
                continue
            if lowered in {"q", "esc", "cancel"}:
                return sorted([idx for idx in defaults if 0 <= idx < len(options)])
            if raw.isdigit():
                val = int(raw)
                if 1 <= val <= len(options):
                    idx = val - 1
                    if idx in selected:
                        selected.remove(idx)
                    else:
                        selected.add(idx)
                    cursor = idx
                    if visible:
                        cursor_pos = visible.index(cursor) if cursor in visible else 0
                        offset = _sync_window(offset, len(visible), cursor_pos, page_size)
                    continue
            print(f"{self.style.rail_color(self.style.rail)}  -> invalid choice")
