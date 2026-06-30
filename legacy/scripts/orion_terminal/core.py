from __future__ import annotations

import json
import os
import re
import shutil
import ssl
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

try:
    from prompt_toolkit.shortcuts import (
        button_dialog,
        checkboxlist_dialog,
        input_dialog,
        radiolist_dialog,
        yes_no_dialog,
    )

    PROMPT_TOOLKIT_AVAILABLE = True
except Exception:
    button_dialog = None
    checkboxlist_dialog = None
    input_dialog = None
    radiolist_dialog = None
    yes_no_dialog = None
    PROMPT_TOOLKIT_AVAILABLE = False

try:
    from .widgets import multi_select_menu, select_menu

    CURSES_WIDGETS_AVAILABLE = True
except Exception:
    multi_select_menu = None
    select_menu = None
    CURSES_WIDGETS_AVAILABLE = False

from .prompt_engine import MenuOption, PromptStyle, TextPromptEngine
from .constants import (
    Choice,
    DEFAULT_API_URL,
    DEFAULT_QUICK_GOAL,
    GENERAL_MODE,
    SPECIALIST_PACKS,
    TRUST_MODES,
    PREFLIGHT_TRUST_MODES,
    PREFLIGHT_OPERATOR_SAFETY_PROFILES,
    EXECUTION_TARGETS,
    FILE_ACCESS_SCOPES,
    LAUNCHER_ACTIONS,
    ORION_SETUP_GATEWAY_LOCATIONS,
    ORION_SETUP_CONFIG_SECTIONS,
    ORION_SETUP_ONBOARDING_MODES,
    ORION_SETUP_CONFIG_HANDLING,
    ORION_SETUP_RESET_SCOPES,
    ORION_SETUP_PROVIDER_GROUPS,
    ORION_SETUP_MODEL_PROVIDER_FILTERS,
    ORION_SETUP_QUICKSTART_CHANNELS,
    CONFIGURE_SECTIONS,
)
from .theme import (
    terminal_theme,
    color_enabled,
    tint,
    strong,
    accent,
    muted,
    rail,
    active,
    ok,
    warn,
    err,
    _shape_markers,
)


URL_PREFIX_RE = re.compile(r"^(https?://|file://)", re.IGNORECASE)
WINDOWS_DRIVE_RE = re.compile(r"^[a-zA-Z]:[\\/]")
FILE_LIKE_RE = re.compile(r"^[a-zA-Z0-9._-]+$")


def tls_context() -> ssl.SSLContext:
    return ssl.create_default_context()


def _line_input_mode_enabled() -> bool:
    raw = os.getenv("ORION_CLI_LINE_INPUT", "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _raw_key_mode_enabled() -> bool:
    if os.name != "posix":
        return False
    if _line_input_mode_enabled():
        return False
    if os.getenv("TERM", "").strip().lower() == "dumb":
        return False
    return sys.stdin.isatty() and sys.stdout.isatty()


def _read_menu_key() -> str:
    # POSIX raw key reader for inline selectors (arrow keys without Enter).
    import os as _os
    import select
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = _os.read(fd, 1).decode("utf-8", errors="ignore")
        if not ch:
            return ""
        if ch == "\x03":
            raise KeyboardInterrupt
        if ch in {"\r", "\n"}:
            return "enter"
        if ch == "\x7f":
            return "backspace"
        if ch == "\x1b":
            seq = ch
            while select.select([fd], [], [], 0.01)[0]:
                nxt = _os.read(fd, 1).decode("utf-8", errors="ignore")
                if not nxt:
                    break
                seq += nxt
                if len(seq) >= 6:
                    break
            mapping = {
                "\x1b[A": "up",
                "\x1b[B": "down",
                "\x1b[5~": "pgup",
                "\x1b[6~": "pgdn",
            }
            return mapping.get(seq, "esc")
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _is_copy_sensitive_token(word: str) -> bool:
    if not word:
        return False
    if URL_PREFIX_RE.match(word):
        return True
    if word.startswith(("/", "~/", "./", "../")):
        return True
    if WINDOWS_DRIVE_RE.match(word) or word.startswith("\\\\"):
        return True
    if "/" in word or "\\" in word:
        return True
    return "_" in word and bool(FILE_LIKE_RE.match(word))


def _split_long_word(word: str, max_len: int) -> List[str]:
    if max_len <= 0:
        return [word]
    if len(word) <= max_len:
        return [word]
    return [word[i : i + max_len] for i in range(0, len(word), max_len)]


def _selector_advanced_hints_enabled() -> bool:
    raw = os.getenv("ORION_CLI_SELECTOR_ADVANCED_HINTS", "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _wrap_note_line(line: str, max_width: int) -> List[str]:
    if not line.strip():
        return [line]

    match = re.match(r"^(\s*)([-*]\s+)?(.*)$", line)
    indent = match.group(1) if match else ""
    bullet = (match.group(2) or "") if match else ""
    content = match.group(3) if match else line

    first_prefix = f"{indent}{bullet}"
    next_prefix = f"{indent}{' ' * len(bullet) if bullet else ''}"
    first_width = max(10, max_width - len(first_prefix))
    next_width = max(10, max_width - len(next_prefix))

    words = [w for w in content.split() if w]
    if not words:
        return [first_prefix.rstrip()]

    wrapped: List[str] = []
    current = ""
    prefix = first_prefix
    available = first_width

    for word in words:
        if not current:
            if len(word) > available and not _is_copy_sensitive_token(word):
                parts = _split_long_word(word, available)
                wrapped.append(prefix + parts[0])
                prefix = next_prefix
                available = next_width
                for part in parts[1:]:
                    wrapped.append(prefix + part)
                continue
            current = word
            continue

        candidate = f"{current} {word}"
        if len(candidate) <= available:
            current = candidate
            continue

        wrapped.append(prefix + current)
        prefix = next_prefix
        available = next_width
        if len(word) > available and not _is_copy_sensitive_token(word):
            parts = _split_long_word(word, available)
            wrapped.append(prefix + parts[0])
            for part in parts[1:]:
                wrapped.append(prefix + part)
            current = ""
            continue
        current = word

    if current:
        wrapped.append(prefix + current)

    return wrapped


def wrap_note_message(message: str, columns: Optional[int] = None, max_width: Optional[int] = None) -> str:
    terminal_columns = columns if columns is not None else shutil.get_terminal_size((96, 24)).columns
    target_width = max_width if max_width is not None else max(44, min(92, terminal_columns - 8))
    lines: List[str] = []
    for raw_line in (message or "").splitlines() or [""]:
        lines.extend(_wrap_note_line(raw_line, target_width))
    return "\n".join(lines)


def _colorize_box_line(line: str) -> str:
    text = str(line or "")
    if not text.strip():
        return text
    match = re.match(r"^(\s*)([A-Za-z][A-Za-z0-9 _/\-]{0,40}):(.*)$", text)
    if match:
        indent, key, rest = match.groups()
        return f"{indent}{strong(key + ':')}{rest}"
    if text.lstrip().startswith("- "):
        lead = len(text) - len(text.lstrip())
        prefix = text[:lead]
        body = text[lead + 2 :]
        return f"{prefix}{accent('-')} {body}"
    return text


class UI:
    def __init__(self, prefer_tui: bool = True):
        light_mode = terminal_theme() == "light"
        force_tui_on_light = os.getenv("ORION_CLI_TUI_ON_LIGHT", "0") == "1"
        no_tui = os.getenv("ORION_CLI_NO_TUI", "0") == "1"
        prompt_toolkit_opt_in = os.getenv("ORION_CLI_PROMPT_TOOLKIT", "0") == "1"
        force_curses = os.getenv("ORION_CLI_FORCE_CURSES", "0") == "1"
        prefer_inline = os.getenv("ORION_CLI_PREFER_INLINE", "1") == "1"
        self.use_tui = (
            prefer_tui
            and prompt_toolkit_opt_in
            and PROMPT_TOOLKIT_AVAILABLE
            and sys.stdin.isatty()
            and sys.stdout.isatty()
            and os.getenv("ORION_CLI_TEXT_MODE", "0") != "1"
            and not no_tui
            and (not light_mode or force_tui_on_light)
        )
        self.use_curses = (
            force_curses
            and
            not self.use_tui
            and CURSES_WIDGETS_AVAILABLE
            and sys.stdin.isatty()
            and sys.stdout.isatty()
            and os.getenv("ORION_CLI_NO_CURSES", "0") != "1"
            and (force_curses or not prefer_inline)
        )

    @staticmethod
    def _print_box(title: str, body: str) -> None:
        ascii_mode = os.getenv("ORION_CLI_ASCII", "0") == "1"
        lead = "*" if ascii_mode else "▸"
        rail_chr = "|" if ascii_mode else "│"
        tee = "+" if ascii_mode else "├"
        bend = "`" if ascii_mode else "╯"
        hor = "-" if ascii_mode else "─"
        top_bend = "'" if ascii_mode else "╮"

        terminal_width = shutil.get_terminal_size((96, 24)).columns
        inner_width = max(40, min(88, terminal_width - 8))
        wrapped = wrap_note_message(body, max_width=inner_width)
        lines = [line.rstrip() for line in wrapped.splitlines()] or [""]
        title_rule_len = max(6, inner_width - len(title) - 6)
        bottom_rule_len = max(12, min(inner_width + 2, terminal_width - 4))

        print(f"{accent(lead)}  {strong(title)} {accent(hor * title_rule_len + top_bend)}")
        print(rail(rail_chr))
        for line in lines:
            print(f"{rail(rail_chr)}  {_colorize_box_line(line)}")
        print(accent(f"{tee}{hor * bottom_rule_len}{bend}"))

    def note(self, message: str) -> None:
        print(message)

    @staticmethod
    def _secret_preview(value: str) -> str:
        text = str(value or "")
        n = len(text)
        if n <= 0:
            return ""
        if n <= 4:
            return "*" * n
        if n <= 8:
            return f"{text[0]}{'*' * (n - 2)}{text[-1]}"
        return f"{text[:4]}...{text[-4:]}"

    def message(self, title: str, body: str) -> None:
        if self.use_tui:
            try:
                button_dialog(
                    title=title,
                    text=body,
                    buttons=[("OK", True)],
                ).run()
                return
            except Exception:
                pass
        print("")
        self._print_box(title, body)

    def choose(self, prompt: str, options: Sequence[Choice], default_index: int = 0, title: str = "Empyralis") -> Choice:
        items = list(options)
        if not items:
            raise RuntimeError("No options available")

        if self.use_tui:
            values = []
            for idx, opt in enumerate(items):
                label = opt.label
                if opt.note:
                    label = f"{label}  ({opt.note})"
                values.append((str(idx), label))
            try:
                result = radiolist_dialog(
                    title=title,
                    text=prompt,
                    values=values,
                    default=str(max(0, min(default_index, len(items) - 1))),
                    ok_text="Select",
                    cancel_text="Cancel",
                ).run()
                if result is None:
                    return items[default_index]
                parsed = int(result)
                if 0 <= parsed < len(items):
                    return items[parsed]
            except Exception:
                pass

        if self.use_curses:
            labels: List[str] = []
            notes: List[str] = []
            for opt in items:
                labels.append(opt.label)
                notes.append(opt.note)
            result = select_menu(
                title=title,
                prompt=prompt,
                options=labels,
                notes=notes,
                default_index=default_index,
            )
            if result is None:
                return items[default_index]
            if 0 <= result < len(items):
                return items[result]

        interactive_tty = sys.stdin.isatty() and sys.stdout.isatty()
        line_input_mode = _line_input_mode_enabled()
        raw_keys = _raw_key_mode_enabled()
        ascii_mode = os.getenv("ORION_CLI_ASCII", "0") == "1"
        top = "+" if ascii_mode else "┌"
        rail_chr = "|" if ascii_mode else "│"
        bottom = "`" if ascii_mode else "└"
        bullet_on, bullet_off = _shape_markers(ascii_mode)
        prompt_mark = ">" if ascii_mode else "▸"

        style = PromptStyle(
            top=top,
            rail=rail_chr,
            bottom=bottom,
            prompt_mark=prompt_mark,
            bullet_on=bullet_on,
            bullet_off=bullet_off,
            muted=muted,
            accent=accent,
            strong=strong,
            rail_color=rail,
            active_color=active,
            advanced_hints=_selector_advanced_hints_enabled(),
        )
        engine = TextPromptEngine(
            style=style,
            read_key=_read_menu_key,
            raw_keys=raw_keys,
            line_input_mode=line_input_mode,
            interactive_tty=interactive_tty,
            page_size_base=max(5, int(os.getenv("ORION_CLI_PAGE_SIZE", "12"))),
        )
        try:
            selected_idx = engine.select(
                title=title,
                prompt=prompt,
                options=[MenuOption(label=item.label, note=item.note) for item in items],
                default_index=default_index,
            )
        except Exception:
            selected_idx = default_index
        if 0 <= selected_idx < len(items):
            return items[selected_idx]
        return items[default_index]

    def multi_select(
        self,
        prompt: str,
        options: Sequence[Choice],
        default_indexes: Optional[Sequence[int]] = None,
        title: str = "Empyralis",
    ) -> List[Choice]:
        items = list(options)
        defaults = set(default_indexes or [])

        if self.use_tui:
            values = []
            for idx, opt in enumerate(items):
                label = opt.label
                if opt.note:
                    label = f"{label}  ({opt.note})"
                values.append((str(idx), label))
            default_values = [str(idx) for idx in defaults if 0 <= idx < len(items)]
            try:
                result = checkboxlist_dialog(
                    title=title,
                    text=prompt,
                    values=values,
                    default_values=default_values,
                    ok_text="Continue",
                    cancel_text="Cancel",
                ).run()
                if result is None:
                    return [items[idx] for idx in sorted(defaults) if 0 <= idx < len(items)]
                out: List[Choice] = []
                for item in result:
                    idx = int(item)
                    if 0 <= idx < len(items):
                        out.append(items[idx])
                return out
            except Exception:
                pass

        if self.use_curses:
            labels: List[str] = []
            notes: List[str] = []
            for opt in items:
                labels.append(opt.label)
                notes.append(opt.note)
            result = multi_select_menu(
                title=title,
                prompt=prompt,
                options=labels,
                notes=notes,
                default_indexes=sorted(defaults),
            )
            if result is None:
                return [items[idx] for idx in sorted(defaults) if 0 <= idx < len(items)]
            return [items[idx] for idx in result if 0 <= idx < len(items)]

        interactive_tty = sys.stdin.isatty() and sys.stdout.isatty()
        line_input_mode = _line_input_mode_enabled()
        raw_keys = _raw_key_mode_enabled()
        ascii_mode = os.getenv("ORION_CLI_ASCII", "0") == "1"
        top = "+" if ascii_mode else "┌"
        rail_chr = "|" if ascii_mode else "│"
        bottom = "`" if ascii_mode else "└"
        bullet_on, bullet_off = _shape_markers(ascii_mode)
        prompt_mark = ">" if ascii_mode else "▸"

        style = PromptStyle(
            top=top,
            rail=rail_chr,
            bottom=bottom,
            prompt_mark=prompt_mark,
            bullet_on=bullet_on,
            bullet_off=bullet_off,
            muted=muted,
            accent=accent,
            strong=strong,
            rail_color=rail,
            active_color=active,
            advanced_hints=_selector_advanced_hints_enabled(),
        )
        engine = TextPromptEngine(
            style=style,
            read_key=_read_menu_key,
            raw_keys=raw_keys,
            line_input_mode=line_input_mode,
            interactive_tty=interactive_tty,
            page_size_base=max(5, int(os.getenv("ORION_CLI_PAGE_SIZE", "12"))),
        )
        try:
            selected_indexes = engine.multi_select(
                title=title,
                prompt=prompt,
                options=[MenuOption(label=item.label, note=item.note) for item in items],
                default_indexes=sorted(defaults),
            )
        except Exception:
            selected_indexes = sorted(defaults)
        return [items[idx] for idx in selected_indexes if 0 <= idx < len(items)]

    def input(self, prompt: str, default: Optional[str] = None, required: bool = False, secret: bool = False, title: str = "Empyralis") -> str:
        while True:
            if self.use_tui:
                try:
                    result = input_dialog(
                        title=title,
                        text=prompt,
                        default=default or "",
                        password=secret,
                        ok_text="Continue",
                        cancel_text="Cancel",
                    ).run()
                    value = (result or "").strip()
                except Exception:
                    value = ""
            else:
                suffix = f" [{default}]" if default else ""
                stripped_prompt = str(prompt or "").strip()
                composer_prompt = stripped_prompt in {">", "›"}
                if stripped_prompt == "":
                    if sys.stdout.isatty():
                        sys.stdout.write("\r\033[K")
                        sys.stdout.flush()
                    label = ""
                elif composer_prompt:
                    if sys.stdout.isatty():
                        # Keep a single stable compose cursor line for chat mode.
                        sys.stdout.write("\r\033[K")
                        sys.stdout.flush()
                    label = f"{stripped_prompt} "
                else:
                    label = f"{prompt}{suffix}: "
                if secret:
                    try:
                        import getpass

                        raw = getpass.getpass(label)
                    except Exception:
                        raw = input(label)
                else:
                    raw = input(label)
                if composer_prompt and sys.stdout.isatty():
                    # Clear echoed compose prompt after Enter so only transcript remains.
                    sys.stdout.write("\r\033[K")
                    sys.stdout.flush()
                value = raw.strip()

            if not value and default is not None:
                value = default

            if secret and value and os.getenv("ORION_CLI_SECRET_CONFIRM", "1") != "0":
                preview = self._secret_preview(value)
                while True:
                    decision = input(
                        f"  -> received {len(value)} chars ({preview}). "
                        "Enter=use  r=re-enter  s=show once: "
                    ).strip().lower()
                    if not decision:
                        break
                    if decision == "r":
                        value = ""
                        break
                    if decision == "s":
                        self.note(f"  -> secret: {value}")
                        continue
                    self.note("  -> enter Enter, r, or s")
                if not value:
                    continue

            if required and not value:
                self.note("  -> value is required")
                continue
            return value

    def confirm(self, prompt: str, default_yes: bool = True, title: str = "Empyralis") -> bool:
        if self.use_tui:
            try:
                result = yes_no_dialog(title=title, text=prompt, yes_text="Yes", no_text="No").run()
                if isinstance(result, bool):
                    return result
            except Exception:
                pass

        if self.use_curses:
            default_index = 0 if default_yes else 1
            result = select_menu(
                title=title,
                prompt=prompt,
                options=["Yes", "No"],
                default_index=default_index,
            )
            if result is None:
                return default_yes
            return result == 0

        hint = "Y/n" if default_yes else "y/N"
        while True:
            raw = input(f"{prompt} [{hint}]: ").strip().lower()
            if not raw:
                return default_yes
            if raw in {"y", "yes"}:
                return True
            if raw in {"n", "no"}:
                return False
            self.note("  -> enter y or n")


def http_json(
    api_url: str,
    path: str,
    api_key: str,
    method: str = "GET",
    payload: Optional[Dict[str, Any]] = None,
    extra_headers: Optional[Dict[str, str]] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    body = None
    headers = {"X-API-Key": api_key, "Accept": "application/json"}
    if isinstance(extra_headers, dict):
        for key, value in extra_headers.items():
            if key is None or value is None:
                continue
            headers[str(key)] = str(value)
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    req = urlrequest.Request(f"{api_url}{path}", data=body, headers=headers, method=method)
    try:
        with urlrequest.urlopen(req, timeout=timeout, context=tls_context()) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}
    except urlerror.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
        detail = raw or str(exc)
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except urlerror.URLError as exc:
        raise RuntimeError(f"Network error: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(str(exc)) from exc


def short_ts(iso_ts: Any) -> str:
    if not isinstance(iso_ts, str):
        return "--:--:--"
    text = iso_ts.strip()
    if not text:
        return "--:--:--"
    try:
        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
        dt = datetime.fromisoformat(normalized)
        return dt.strftime("%H:%M:%S")
    except Exception:
        return "--:--:--"


def print_launcher_header(api_url: str, workspace_id: str, health: Dict[str, Any]) -> None:
    print("")
    ascii_mode = os.getenv("ORION_CLI_ASCII", "0") == "1"
    top = "+" if ascii_mode else "┌"
    rail_chr = "|" if ascii_mode else "│"
    bottom = "`" if ascii_mode else "└"
    print(f"{accent(top)}  {strong('Empyralis Command Center')}")
    runtime_ok = bool(health.get("ok"))
    runtime_valid = bool(health.get("runtime_valid", True))
    local_companion = bool(health.get("local_companion_enabled", False))
    auth_required = bool(health.get("auth_required", False))
    source = str(health.get("openai_credential_source") or "unknown")
    codex_oauth_interactive = bool(health.get("codex_oauth_interactive_supported", False))
    codex_auth_mode = "oauth-web" if codex_oauth_interactive else "token-vault"
    status = ok("healthy") if runtime_ok and runtime_valid else warn("check")
    print(f"{rail(rail_chr)}  runtime: {api_url}")
    print(f"{rail(rail_chr)}  workspace: {workspace_id}")
    print(
        f"{rail(rail_chr)}  status: {status}  "
        f"(local={'on' if local_companion else 'off'}, "
        f"auth={'on' if auth_required else 'off'}, "
        f"key={source}, codex={codex_auth_mode})"
    )
    print(accent(bottom))


def list_provider_catalog(api_url: str, api_key: str) -> List[Choice]:
    payload = http_json(api_url, "/providers", api_key)
    items = payload.get("providers") if isinstance(payload.get("providers"), list) else []
    out: List[Choice] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        provider_id = str(item.get("id") or "").strip().lower()
        label = str(item.get("label") or provider_id or "provider")
        auth = item.get("auth") if isinstance(item.get("auth"), list) else []
        default_model = str(item.get("default_model") or "").strip()
        note_parts = []
        if auth:
            note_parts.append("auth:" + ",".join([str(x) for x in auth]))
        if default_model:
            note_parts.append("default:" + default_model)
        note = " | ".join(note_parts)
        if provider_id:
            out.append(Choice(provider_id, label, note))
    out.sort(key=lambda c: c.label.lower())
    return out


def list_workspace_credentials(api_url: str, api_key: str, workspace_id: str, provider: Optional[str] = None) -> List[Dict[str, Any]]:
    qs = f"?workspace_id={urlparse.quote(workspace_id)}"
    payload = http_json(api_url, f"/credentials/vault{qs}", api_key)
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    out: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        p = str(item.get("provider") or "").strip().lower()
        if provider and p != provider:
            continue
        out.append(item)
    return out


def list_workspace_profiles(api_url: str, api_key: str, workspace_id: str, provider: Optional[str] = None) -> List[Dict[str, Any]]:
    qs = f"?workspace_id={urlparse.quote(workspace_id)}"
    if provider:
        qs += f"&provider={urlparse.quote(provider)}"
    payload = http_json(api_url, f"/providers/profiles{qs}", api_key)
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    return [item for item in items if isinstance(item, dict)]
