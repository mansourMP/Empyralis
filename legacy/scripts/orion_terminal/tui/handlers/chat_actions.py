from __future__ import annotations

from dataclasses import dataclass, field
import inspect
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from urllib import request as urlrequest

from ... import question_catalog as q
from ...constants import GENERAL_MODE, SPECIALIST_PACKS
from ...core import Choice, muted, ok, strong, tint, tls_context, warn
from ...services.live_tui import (
    TUIContext,
    collect_chat_rows as _collect_chat_rows,
    dashboard_snapshot as _dashboard_snapshot,
    format_channel_health_rollup as _format_channel_health_rollup,
    format_chat_rows as _format_chat_rows,
    format_chat_transcript as _format_chat_transcript,
    format_inbox_events as _format_inbox_events,
    inbox_events as _default_inbox_events,
    live_tui_status_summary as _live_tui_status_summary,
    pending_approvals as _pending_approvals,
)
from ...services.chat_event_assembler import ChatEventAssembler
from ...spinner import ProgressSpinner
from ...state.workspace import load_workspace_state, save_workspace_state
from ...text_format import preview_text
from ..chat.controller import resolve_current_scope, resolve_send_hint, resolve_session_label
from ..chat.command_registry import ChatCommandInput, ChatCommandRegistry, ChatCommandResult
from ..chat.renderer import render_chat_frame, render_chat_frame_output, reset_chat_frame_renderer
from ..chat.session_router import (
    apply_session_choice as _apply_session_choice,
    open_session_overlay as _open_session_overlay,
    parse_session_command as _parse_session_command,
)
from ..commands import (
    commands_hint,
    help_text,
    input_prompt_label,
    is_supported_command,
    parse_chat_input,
    parse_command_alias,
)


@dataclass
class _ChatShellState:
    session_filter: Optional[str] = None
    channel_filter: Optional[str] = None
    input_route: str = "local_run"
    last_fetch_error: str = ""
    manual_session_override: bool = False
    prefix_notice_seen: Set[str] = field(default_factory=set)
    last_frame: str = ""
    event_assembler: ChatEventAssembler = field(default_factory=ChatEventAssembler)
    approval_notice_by_run: Dict[str, str] = field(default_factory=dict)
    approval_notice_by_event: Dict[str, str] = field(default_factory=dict)
    stream_scope_signature: str = ""
    stream_cursor_id: str = ""
    stream_cursor_ts: str = ""
    stream_recent_cache: List[Dict[str, Any]] = field(default_factory=list)
    composer_draft: str = ""
    stream_status: str = ""
    composer_hint: str = ""
    pending_runtime_approvals: int = 0
    pending_objective_approvals: int = 0
    pending_runtime_items: List[Dict[str, str]] = field(default_factory=list)
    pending_objective_items: List[Dict[str, Any]] = field(default_factory=list)


def _slug(value: str) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _chat_shell_state_signature(ctx: TUIContext, state: _ChatShellState) -> Tuple[str, str, str, str]:
    return (
        str(state.session_filter or "").strip(),
        str(state.channel_filter or "").strip().lower(),
        _input_route_label(state.input_route),
        str(ctx.engine or "").strip().lower(),
    )


def _restore_chat_shell_state(ctx: TUIContext, state: _ChatShellState) -> None:
    try:
        workspace_state = load_workspace_state(ctx.workspace_id)
    except Exception:
        return
    live_tui = getattr(workspace_state, "live_tui", None)
    if live_tui is None:
        return

    restored_session = str(getattr(live_tui, "session_filter", "") or "").strip()
    restored_channel = str(getattr(live_tui, "channel_filter", "") or "").strip().lower()
    restored_route = _input_route_label(str(getattr(live_tui, "input_route", "local_run") or "").strip())
    restored_engine = str(getattr(live_tui, "engine", "") or "").strip().lower()

    if restored_session:
        state.session_filter = restored_session
        state.channel_filter = None
        state.manual_session_override = True
    elif restored_channel in {"telegram", "whatsapp", "terminal"}:
        state.session_filter = None
        state.channel_filter = restored_channel
        state.manual_session_override = True

    if _one_app_mode_enabled():
        state.input_route = _resolve_one_app_route(state)
    else:
        state.input_route = restored_route

    if restored_engine in {"orion", "codex"}:
        ctx.engine = restored_engine


def _persist_chat_shell_state(ctx: TUIContext, state: _ChatShellState) -> None:
    try:
        workspace_state = load_workspace_state(ctx.workspace_id)
    except Exception:
        return
    live_tui = getattr(workspace_state, "live_tui", None)
    if live_tui is None:
        return
    live_tui.session_filter = str(state.session_filter or "").strip()
    live_tui.channel_filter = str(state.channel_filter or "").strip().lower()
    live_tui.input_route = _input_route_label(state.input_route)
    live_tui.engine = str(ctx.engine or "").strip().lower()
    try:
        save_workspace_state(ctx.workspace_id, workspace_state)
    except Exception:
        return


def _chat_push(ctx: TUIContext, role: str, text: str, run_id: str = "", channel: str = "terminal") -> None:
    message = str(text or "").strip()
    if not message:
        return
    # Keep transcript noise low: skip duplicates recently emitted in this session.
    recent = ctx.chat_local_entries[-16:] if ctx.chat_local_entries else []
    for prev in reversed(recent):
        if (
            str(prev.get("role") or "") == str(role)
            and str(prev.get("channel") or "") == str(channel)
            and str(prev.get("run_id") or "").strip() == str(run_id or "").strip()
            and str(prev.get("text") or "").strip() == message
        ):
            return
    if ctx.chat_local_entries:
        last = ctx.chat_local_entries[-1]
        if (
            str(last.get("role") or "") == str(role)
            and str(last.get("channel") or "") == str(channel)
            and str(last.get("run_id") or "").strip() == str(run_id or "").strip()
            and str(last.get("text") or "").strip() == message
        ):
            return
    ctx.chat_local_entries.append(
        {
            "ts": _iso_now(),
            "role": role,
            "text": message,
            "run_id": str(run_id or "").strip(),
            "channel": channel,
        }
    )
    if len(ctx.chat_local_entries) > 160:
        ctx.chat_local_entries = ctx.chat_local_entries[-160:]


def _chat_upsert_orion_entry(ctx: TUIContext, text: str, run_id: str = "", channel: str = "terminal") -> None:
    message = str(text or "").strip()
    if not message:
        return
    run_key = str(run_id or "").strip()
    if run_key:
        for item in reversed(ctx.chat_local_entries):
            if (
                str(item.get("role") or "") == "orion"
                and str(item.get("channel") or "") == str(channel or "terminal")
                and str(item.get("run_id") or "").strip() == run_key
            ):
                if str(item.get("text") or "").strip() == message:
                    return
                item["text"] = message
                item["ts"] = _iso_now()
                return
    _chat_push(ctx, "orion", message, run_id=run_key, channel=channel)


def _chat_requires_run_prefix(ctx: TUIContext) -> bool:
    raw = os.getenv("ORION_CLI_CHAT_REQUIRE_RUN_PREFIX", "auto").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"auto", ""}:
        return str(ctx.trust.key or "").strip().lower() == "strict"
    return False


def _chat_auto_focus_telegram() -> bool:
    # Keep timeline unified by default; users can opt-in to telegram autofocused view.
    default_value = "0"
    raw = os.getenv("ORION_CLI_CHAT_AUTOFOCUS_TELEGRAM", default_value).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _chat_spinner_enabled() -> bool:
    raw = os.getenv("ORION_CLI_CHAT_SPINNER", "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _chat_full_redraw_enabled() -> bool:
    # Keep full redraw on by default to avoid prompt/frame residue in long live sessions.
    raw = os.getenv("ORION_CLI_CHAT_FULL_REDRAW", "1").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _chat_clear_screen_enabled() -> bool:
    raw = os.getenv("ORION_CLI_CHAT_CLEAR_SCREEN", "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _chat_show_footer_hints() -> bool:
    # Keep one compact command/send footer visible by default.
    raw = os.getenv("ORION_CLI_CHAT_SHOW_HINTS", "1").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _chat_show_profile_line() -> bool:
    raw = os.getenv("ORION_CLI_CHAT_SHOW_PROFILE", "1").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _chat_compact_header_enabled() -> bool:
    # OpenClaw-like compact header by default.
    raw = os.getenv("ORION_CLI_CHAT_COMPACT_HEADER", "1").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _chat_show_fetch_errors() -> bool:
    raw = os.getenv("ORION_CLI_CHAT_SHOW_FETCH_ERRORS", "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _chat_show_channel_health() -> bool:
    raw = os.getenv("ORION_CLI_CHAT_SHOW_CHANNEL_HEALTH", "1").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _chat_inline_composer_enabled() -> bool:
    # Keep input anchored in the frame by default (OpenClaw-like single pane).
    raw = os.getenv("ORION_CLI_CHAT_INLINE_COMPOSER", "1").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _chat_show_stream_status_line() -> bool:
    raw = os.getenv("ORION_CLI_CHAT_STREAM_STATUS_LINE", "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _chat_show_connection_footer() -> bool:
    raw = os.getenv("ORION_CLI_CHAT_CONNECTION_FOOTER", "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _chat_connection_footer_line(
    channel_health: str,
    last_fetch_error: str,
    runtime_approvals: int = 0,
    objective_approvals: int = 0,
) -> str:
    if str(last_fetch_error or "").strip():
        status = warn("disconnected")
    else:
        lowered = str(channel_health or "").lower()
        if "degraded" in lowered:
            status = warn("degraded")
        elif "connected" in lowered:
            status = ok("connected")
        else:
            status = muted("idle")
    parts = [str(status)]
    if int(runtime_approvals or 0) > 0 or int(objective_approvals or 0) > 0:
        parts.append(
            warn(
                f"approvals r={int(runtime_approvals or 0)} "
                f"o={int(objective_approvals or 0)}"
            )
        )
        parts.append(muted("use /approvals"))
    parts.append("press Ctrl+C again to exit")
    return " | ".join(parts)


def _chat_command_suggestions(draft: str) -> str:
    text = str(draft or "").strip()
    if not text.startswith("/"):
        return ""
    token = text.split(maxsplit=1)[0].lower()
    commands = [
        "/run",
        "/session",
        "/status",
        "/help",
        "/exit",
        "/engine",
        "/model",
        "/agent",
        "/approvals",
        "/ok",
        "/hold",
        "/approve",
        "/reject",
        "/objective",
        "/preflight",
    ]
    matches = [cmd for cmd in commands if cmd.startswith(token)]
    if matches:
        return "suggest: " + "  ".join(matches[:5])
    return "suggest: /run  /session  /status  /help  /exit"


def _chat_stream_status_label(raw: str) -> str:
    key = str(raw or "").strip().lower()
    mapping = {
        "": "",
        "queued": "queued",
        "running": "running",
        "streaming": "streaming",
        "waiting_for_input": "awaiting approval",
        "completed": "completed",
        "failed": "failed",
        "telegram_sent": "telegram sent",
        "telegram_error": "telegram error",
    }
    return mapping.get(key, key)


def _chat_composer_bar(text: str) -> str:
    width = max(44, min(140, int(shutil.get_terminal_size((96, 24)).columns) - 2))
    body = str(text or "").replace("\n", " ").strip()
    if not body:
        body = "type message and press Enter"
    available = max(8, width - 3)
    if len(body) > available:
        body = body[: available - 1].rstrip() + "…"
    line = f"› {body}".ljust(width)
    return tint(line, "48;5;238;38;5;255")


def _chat_inbox_stream_enabled() -> bool:
    # Keep off by default during rollout; polling remains the stable fallback.
    raw = os.getenv("ORION_CLI_CHAT_INBOX_STREAM", "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _chat_inbox_stream_timeout_seconds() -> float:
    raw = str(os.getenv("ORION_CLI_CHAT_INBOX_STREAM_TIMEOUT_SECONDS", "0.35") or "").strip()
    try:
        value = float(raw)
    except Exception:
        value = 0.35
    return max(0.1, min(value, 5.0))


def _chat_inbox_stream_poll_seconds() -> float:
    raw = str(os.getenv("ORION_CLI_CHAT_INBOX_STREAM_POLL_SECONDS", "0.10") or "").strip()
    try:
        value = float(raw)
    except Exception:
        value = 0.10
    return max(0.05, min(value, 1.5))


def _chat_inbox_stream_heartbeat_seconds() -> float:
    raw = str(os.getenv("ORION_CLI_CHAT_INBOX_STREAM_HEARTBEAT_SECONDS", "5.0") or "").strip()
    try:
        value = float(raw)
    except Exception:
        value = 5.0
    return max(1.0, min(value, 30.0))


def _chat_live_input_enabled() -> bool:
    # Stable line input by default to avoid prompt artifacts on resize/TTY differences.
    raw = os.getenv("ORION_CLI_CHAT_LIVE_INPUT", "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _chat_live_input_interval_seconds() -> float:
    raw = str(os.getenv("ORION_CLI_CHAT_LIVE_INPUT_INTERVAL_SECONDS", "0.35") or "").strip()
    try:
        value = float(raw)
    except Exception:
        value = 0.35
    return max(0.10, min(value, 2.0))


def _chat_live_input_supported() -> bool:
    if not _chat_live_input_enabled():
        return False
    if os.name != "posix":
        return False
    if os.getenv("ORION_CLI_LINE_INPUT", "0").strip().lower() in {"1", "true", "yes", "on"}:
        return False
    return sys.stdin.isatty() and sys.stdout.isatty()


def _read_chat_input_with_idle_refresh(
    prompt_label: str,
    *,
    on_idle_tick: Callable[[], None],
    on_change: Optional[Callable[[str], None]] = None,
    show_external_prompt: bool = True,
) -> str:
    # Keep one active prompt line while allowing periodic feed refresh.
    import select
    import termios
    import tty

    fd = sys.stdin.fileno()
    old_attrs = termios.tcgetattr(fd)
    interval = _chat_live_input_interval_seconds()
    prompt_prefix = f"{str(prompt_label or '>').strip()} "
    chars: List[str] = []
    skip_ansi = False
    last_prompt_rows = 0
    ansi_re = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

    def _terminal_columns() -> int:
        try:
            cols = int(shutil.get_terminal_size((96, 24)).columns)
        except Exception:
            cols = 96
        return max(20, cols)

    def _prompt_render_rows(text: str) -> int:
        plain = ansi_re.sub("", str(text or ""))
        cols = _terminal_columns()
        return max(1, ((max(1, len(plain)) - 1) // cols) + 1)

    def _clear_prompt_rows() -> None:
        nonlocal last_prompt_rows
        if not show_external_prompt or not sys.stdout.isatty():
            return
        if last_prompt_rows <= 0:
            sys.stdout.write("\r\033[K")
            sys.stdout.flush()
            return
        sys.stdout.write("\r\033[K")
        for _ in range(last_prompt_rows - 1):
            sys.stdout.write("\033[F\033[K")
        sys.stdout.flush()
        last_prompt_rows = 0

    def _draw_prompt() -> None:
        nonlocal last_prompt_rows
        if not show_external_prompt:
            return
        if not sys.stdout.isatty():
            return
        current_prompt = prompt_prefix + "".join(chars)
        if last_prompt_rows > 0:
            sys.stdout.write("\r\033[K")
            for _ in range(last_prompt_rows - 1):
                # Clear wrapped prompt rows left by narrower terminal widths.
                sys.stdout.write("\033[F\033[K")
        else:
            sys.stdout.write("\r\033[K")
        sys.stdout.write(current_prompt)
        sys.stdout.flush()
        last_prompt_rows = _prompt_render_rows(current_prompt)

    def _emit_change() -> None:
        if on_change:
            on_change("".join(chars))

    try:
        tty.setraw(fd)
        last_tick = 0.0
        _emit_change()
        _draw_prompt()
        while True:
            now = time.monotonic()
            if (now - last_tick) >= interval:
                on_idle_tick()
                _draw_prompt()
                last_tick = now

            ready, _, _ = select.select([fd], [], [], interval)
            if not ready:
                continue
            payload = os.read(fd, 128)
            if not payload:
                raise EOFError

            text = payload.decode("utf-8", errors="ignore")
            for ch in text:
                if skip_ansi:
                    if ch.isalpha() or ch == "~":
                        skip_ansi = False
                    continue
                if ch == "\x1b":
                    skip_ansi = True
                    continue
                if ch in {"\r", "\n"}:
                    _clear_prompt_rows()
                    _emit_change()
                    return "".join(chars)
                if ch == "\x03":
                    raise KeyboardInterrupt
                if ch == "\x04":
                    if not chars:
                        raise EOFError
                    continue
                if ch in {"\x7f", "\b"}:
                    if chars:
                        chars.pop()
                        _emit_change()
                    continue
                if ord(ch) < 32:
                    continue
                chars.append(ch)
                _emit_change()
            _draw_prompt()
    finally:
        _clear_prompt_rows()
        termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)


def _chat_stream_enabled() -> bool:
    raw = os.getenv("ORION_CLI_CHAT_STREAM", "1").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _chat_live_repaint_enabled() -> bool:
    # Enable live frame updates by default for OpenClaw-like streaming feel.
    raw = os.getenv("ORION_CLI_CHAT_LIVE_REPAINT", "1").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _one_app_mode_enabled() -> bool:
    raw = str(os.getenv("ORION_CLI_ONE_APP_MODE", "1") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _one_app_session_send_enabled() -> bool:
    raw = str(os.getenv("ORION_CLI_ONE_APP_SESSION_SEND", "0") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _default_chat_input_route() -> str:
    if _one_app_mode_enabled():
        return "local_run"
    raw = str(os.getenv("ORION_CLI_CHAT_INPUT_ROUTE", "local_run") or "").strip().lower()
    if raw in {"telegram", "telegram_send", "tg"}:
        return "telegram_send"
    return "local_run"


def _input_route_options() -> List[Choice]:
    if _one_app_mode_enabled():
        return [
            Choice(
                "local_run",
                "One-App mode (local run)",
                "Terminal input always starts Empyralis runs; channels stay unified in timeline",
            )
        ]
    return [
        Choice("local_run", "Local run (terminal)", "Type text to start Empyralis run locally"),
        Choice("telegram_send", "Telegram send", "Type text to send a Telegram message"),
    ]


def _input_route_label(route_key: str) -> str:
    return "telegram_send" if str(route_key or "").strip().lower() == "telegram_send" else "local_run"


def _resolve_one_app_route(state: _ChatShellState) -> str:
    if not _one_app_session_send_enabled():
        return "local_run"
    session_key = str(state.session_filter or "").strip().lower()
    channel_key = str(state.channel_filter or "").strip().lower()
    if session_key.startswith("telegram:") or channel_key == "telegram":
        return "telegram_send"
    return "local_run"


def _apply_input_route_choice(state: _ChatShellState, key: str) -> str:
    if _one_app_mode_enabled():
        resolved = _resolve_one_app_route(state)
        if state.input_route != resolved:
            state.input_route = resolved
            state.event_assembler.reset()
        if resolved == "telegram_send":
            return "One-App Mode route: telegram_send (session-send enabled)."
        return "One-App Mode route: local_run."
    value = _input_route_label(key)
    if value == state.input_route:
        return f"Input route unchanged: {value}"
    state.input_route = value
    state.event_assembler.reset()
    return f"Input route set to {value}"


def _agent_mode_options() -> List[Choice]:
    return [GENERAL_MODE] + list(SPECIALIST_PACKS)


def _resolve_agent_mode(raw_value: str) -> Optional[Choice]:
    token = str(raw_value or "").strip()
    if not token:
        return None
    lowered = token.lower()
    if lowered in {"default", "general", "main"}:
        return GENERAL_MODE
    token_slug = _slug(token)
    for option in _agent_mode_options():
        option_key = str(option.key or "").strip().lower()
        option_slug = _slug(option.label)
        if token == option.key or lowered == option_key or token_slug == option_slug:
            return option
    return None


def _set_agent_mode(ctx: TUIContext, choice: Choice) -> str:
    ctx.pack = choice
    if choice.key:
        return f"Agent set to {choice.label} ({choice.key})."
    return f"Agent set to {choice.label}."


def _model_route_label(ctx: TUIContext) -> str:
    provider = str(ctx.provider_override or "").strip()
    model = str(ctx.model_override or "").strip()
    if provider and model:
        return f"{provider}/{model}"
    if provider and not model:
        return f"{provider} (provider default)"
    if model and not provider:
        return model
    return "runtime default"


def _parse_model_ref(raw_value: str) -> Tuple[str, str]:
    token = str(raw_value or "").strip()
    if not token:
        return "", ""
    if "/" not in token:
        return "", token
    provider, model = token.split("/", 1)
    return provider.strip().lower(), model.strip()


def _clear_model_route(ctx: TUIContext) -> str:
    ctx.provider_override = ""
    ctx.model_override = ""
    return "Model route reset to runtime default."


def _set_model_route(ctx: TUIContext, value: str) -> str:
    token = str(value or "").strip()
    lowered = token.lower()
    if lowered in {"clear", "reset", "default", "runtime"}:
        return _clear_model_route(ctx)
    provider, model = _parse_model_ref(token)
    if provider and model:
        ctx.provider_override = provider
        ctx.model_override = model
        return f"Model set to {provider}/{model}."
    if model and not provider and str(ctx.provider_override or "").strip():
        ctx.model_override = model
        return f"Model set to {ctx.provider_override}/{model}."
    return "Usage: /model <provider/model> (or /models to pick)."


def _open_model_picker(ctx: TUIContext) -> str:
    providers = ctx.client.list_provider_catalog()
    if not providers:
        return "No providers available from runtime."
    default_provider_idx = 0
    current_provider = str(ctx.provider_override or "").strip().lower()
    if current_provider:
        for idx, item in enumerate(providers):
            if str(item.key or "").strip().lower() == current_provider:
                default_provider_idx = idx
                break
    provider_choice = ctx.ui.choose(
        q.Q_LIVE_TUI_MODEL_PROVIDER,
        providers,
        default_index=default_provider_idx,
        title=q.TITLE_LIVE_TUI,
    )
    provider_id = str(provider_choice.key or "").strip().lower()
    if not provider_id:
        return "Provider selection cancelled."

    models = ctx.client.list_provider_models(provider_id, ctx.workspace_id)
    model_options: List[Choice] = [Choice("__provider_default__", f"{provider_id} (provider default)", "No explicit model override")]
    for model in models[:120]:
        text = str(model or "").strip()
        if text:
            model_options.append(Choice(text, text, ""))
    default_model_idx = 0
    current_model = str(ctx.model_override or "").strip()
    if current_model:
        for idx, item in enumerate(model_options):
            if item.key == current_model:
                default_model_idx = idx
                break
    model_choice = ctx.ui.choose(
        q.Q_LIVE_TUI_MODEL_PICK,
        model_options,
        default_index=default_model_idx,
        title=q.TITLE_LIVE_TUI,
    )
    selected = str(model_choice.key or "").strip()
    ctx.provider_override = provider_id
    if selected == "__provider_default__":
        ctx.model_override = ""
        return f"Model route set to {provider_id} (provider default)."
    ctx.model_override = selected
    return f"Model route set to {provider_id}/{selected}."


def _normalize_think_level(raw_value: str) -> str:
    token = str(raw_value or "").strip().lower()
    if token in {"", "off", "none", "0"}:
        return "off"
    if token in {"low", "medium", "high", "max", "auto"}:
        return token
    return ""


def _normalize_on_off(raw_value: str) -> str:
    token = str(raw_value or "").strip().lower()
    if token in {"", "off", "none", "0", "false", "no"}:
        return "off"
    if token in {"on", "1", "true", "yes"}:
        return "on"
    return ""


def _set_verbose_level(ctx: TUIContext, value: str) -> str:
    normalized = _normalize_on_off(value)
    if not normalized:
        return "Usage: /verbose <on|off>"
    ctx.verbose_level = normalized
    return f"Verbose set to {normalized}."


def _set_reasoning_level(ctx: TUIContext, value: str) -> str:
    normalized = _normalize_on_off(value)
    if not normalized:
        return "Usage: /reasoning <on|off>"
    ctx.reasoning_level = normalized
    return f"Reasoning set to {normalized}."


def _set_usage_level(ctx: TUIContext, value: str) -> str:
    token = str(value or "").strip().lower()
    if token in {"off", "none", "0"}:
        ctx.usage_level = "off"
        return "Usage display set to off."
    if token in {"tokens", "full"}:
        ctx.usage_level = token
        return f"Usage display set to {token}."
    return "Usage: /usage <off|tokens|full>"


def _set_elevated_level(ctx: TUIContext, value: str) -> str:
    token = str(value or "").strip().lower()
    if token in {"off", "on", "ask", "full"}:
        ctx.elevated_level = token
        return f"Elevated execution set to {token}."
    return "Usage: /elevated <on|off|ask|full>"


def _set_activation_level(ctx: TUIContext, value: str) -> str:
    token = str(value or "").strip().lower()
    if token in {"mention", "always"}:
        ctx.activation_level = token
        return f"Activation mode set to {token}."
    return "Usage: /activation <mention|always>"


def _set_think_level(ctx: TUIContext, value: str) -> str:
    normalized = _normalize_think_level(value)
    if not normalized:
        return "Usage: /think <off|low|medium|high|auto>"
    ctx.thinking_level = normalized
    return f"Thinking set to {normalized}."


def _settings_summary(ctx: TUIContext, route_label: str, session_label: str) -> str:
    commands = "commands: /engine /agent /model /think /verbose /reasoning /usage /elevated /activation /session /mode /trust /target"
    if not _one_app_mode_enabled():
        commands += " /route"
    return "\n".join(
        [
            f"session: {session_label}",
            f"engine: {str(ctx.engine or 'orion')}",
            f"input route: {route_label}",
            f"agent: {ctx.pack.label}",
            f"model: {_model_route_label(ctx)}",
            f"thinking: {str(ctx.thinking_level or 'off')}",
            f"verbose: {str(ctx.verbose_level or 'off')}",
            f"reasoning: {str(ctx.reasoning_level or 'off')}",
            f"usage: {str(ctx.usage_level or 'off')}",
            f"elevated: {str(ctx.elevated_level or 'off')}",
            f"activation: {str(ctx.activation_level or 'mention')}",
            commands,
        ]
    )


def _available_engines(ctx: TUIContext) -> List[str]:
    try:
        payload = ctx.client.get_health()
        engines = payload.get("engines")
        if isinstance(engines, list):
            out = [str(item or "").strip().lower() for item in engines if str(item or "").strip()]
            if out:
                return out
    except Exception:
        pass
    return ["orion", "codex"]


def _set_engine(ctx: TUIContext, value: str) -> str:
    token = str(value or "").strip().lower()
    if not token:
        return "Usage: /engine <codex|orion>"
    engines = _available_engines(ctx)
    if token not in engines:
        return f"Unsupported engine '{token}'. Available: {', '.join(engines)}"
    ctx.engine = token
    return f"Engine set to {token}."


def _open_engine_picker(ctx: TUIContext) -> str:
    engines = _available_engines(ctx)
    options = [Choice(name, name, "runtime engine") for name in engines]
    default_index = 0
    current = str(ctx.engine or "").strip().lower()
    for idx, item in enumerate(options):
        if str(item.key or "").strip().lower() == current:
            default_index = idx
            break
    selected = ctx.ui.choose(
        q.Q_LIVE_TUI_ENGINE,
        options,
        default_index=default_index,
        title=q.TITLE_LIVE_TUI,
    )
    return _set_engine(ctx, selected.key)


def _chat_clean_summary(raw_text: Any) -> str:
    raw = str(raw_text or "").strip()
    if not raw:
        return ""
    hide_runtime_noise = str(os.getenv("ORION_CLI_CHAT_HIDE_RUNTIME_NOISE", "1") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    lowered = raw.lower()
    if hide_runtime_noise:
        if re.search(r"^run_id:\s*[0-9a-f-]{8,}\s*$", raw, flags=re.IGNORECASE):
            return ""
        if re.search(r"^progress:\s*live\s*$", raw, flags=re.IGNORECASE):
            return ""
        if re.search(r"^result:\s*success\s*$", raw, flags=re.IGNORECASE):
            return ""
        if "local worker completed orion run for goal:" in lowered:
            return ""
        if lowered == "execution plan generated.":
            return ""
        if lowered.startswith("run completed by local companion."):
            return ""
    # Preserve rich assistant output for the chat transcript; only trim extremes.
    try:
        max_chars = int(os.getenv("ORION_CLI_CHAT_RESULT_MAX_CHARS", "2000"))
    except Exception:
        max_chars = 2000
    max_chars = max(280, min(max_chars, 9000))
    if len(raw) <= max_chars:
        return raw
    return raw[: max_chars - 1].rstrip() + "…"


def _extract_chat_run_summary(run_state: Dict[str, Any]) -> str:
    summary = run_state.get("result_summary")
    if isinstance(summary, str) and summary.strip():
        return summary.strip()
    result = run_state.get("result")
    if isinstance(result, str) and result.strip():
        return result.strip()
    if isinstance(result, dict):
        for key in ("summary", "message", "result"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    goal = run_state.get("user_goal")
    if isinstance(goal, str) and goal.strip():
        return goal.strip()
    return ""


_STREAM_NOISY_EVENTS = {
    "stdout",
    "local_heartbeat",
    "pack_phase",
    "dag_node_start",
    "dag_compiled",
    "usage_masked",
}

_STREAM_HIGHLIGHT_EVENTS = {
    "orion_plan",
    "orion_result",
    "pack_summary",
    "codex_output",
    "local_result",
    "run_retry",
    "route_fallback",
    "approval_requested",
    "approval_received",
    "approval_resolved",
    "profile_failover",
    "profile_candidate_failed",
    "run_error",
    "timeout",
    "run_complete",
}


def _stream_update_from_log(log_entry: Dict[str, Any]) -> Tuple[str, bool]:
    if not isinstance(log_entry, dict):
        return "", False
    event_key = str(log_entry.get("event") or "").strip().lower()
    level = str(log_entry.get("level") or "").strip().lower()
    message = str(log_entry.get("message") or "").strip()
    if not event_key and not message:
        return "", False
    if event_key in _STREAM_NOISY_EVENTS:
        return "", False

    terminal = event_key in {"run_complete", "run_error", "timeout"}
    if event_key == "approval_requested":
        msg = message or "Human approval required."
        return f"Approval required: {_chat_clean_summary(msg)}", False
    if event_key in {"approval_received", "approval_resolved"}:
        msg = message or "Approval updated."
        return _chat_clean_summary(msg), False
    if event_key in _STREAM_HIGHLIGHT_EVENTS:
        msg = message or ("Run completed." if event_key == "run_complete" else "")
        return _chat_clean_summary(msg), terminal
    if level in {"warn", "error"}:
        return _chat_clean_summary(message), terminal
    return "", False


def _consume_run_stream(
    *,
    ctx: TUIContext,
    run_id: str,
    timeout_seconds: int,
    spinner: Optional[ProgressSpinner],
    on_update: Optional[Callable[[str, str, bool], None]],
) -> str:
    url = f"{ctx.api_url}/runs/{run_id}/stream"
    req = urlrequest.Request(
        url,
        headers={"X-API-Key": ctx.api_key, "Accept": "text/event-stream"},
        method="GET",
    )
    terminal_status = ""
    event_name = "message"
    data_lines: List[str] = []

    def _push(text: str, *, final: bool = False) -> None:
        cleaned = _chat_clean_summary(text)
        if cleaned and on_update:
            on_update(run_id, cleaned, final)

    def _dispatch(evt: str, raw_data: str) -> None:
        nonlocal terminal_status
        name = str(evt or "message").strip().lower()
        payload = str(raw_data or "")
        if name == "pause":
            if spinner:
                spinner.tick("waiting for approval")
            _push("Human input needed.", final=False)
            return
        if name != "log":
            if payload.strip():
                _push(payload.strip(), final=False)
            return
        parsed: Dict[str, Any]
        try:
            obj = json.loads(payload) if payload else {}
            parsed = obj if isinstance(obj, dict) else {"message": str(obj)}
        except Exception:
            parsed = {"message": payload}

        event_key = str(parsed.get("event") or "").strip().lower()
        if event_key in {"waiting_for_input", "awaiting_approval", "approval_requested"} and spinner:
            spinner.tick("waiting for approval")
        elif event_key in {"completed", "run_complete"} and spinner:
            spinner.tick("complete")
        elif event_key in {"run_error", "timeout", "failed"} and spinner:
            spinner.tick("failed")
        elif spinner:
            spinner.tick("running")

        text, terminal = _stream_update_from_log(parsed)
        if text:
            _push(text, final=terminal)
        if terminal:
            terminal_status = "completed" if event_key == "run_complete" else "failed"

    with urlrequest.urlopen(req, timeout=max(15, int(timeout_seconds)), context=tls_context()) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").rstrip("\n")
            if line.endswith("\r"):
                line = line[:-1]
            if not line:
                if data_lines:
                    _dispatch(event_name, "\n".join(data_lines))
                event_name = "message"
                data_lines = []
                if terminal_status:
                    break
                continue
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip() or "message"
                continue
            if line.startswith("data:"):
                data_lines.append(line.split(":", 1)[1].lstrip())
        if data_lines and not terminal_status:
            _dispatch(event_name, "\n".join(data_lines))
    return terminal_status


def run_chat_goal(
    ctx: TUIContext,
    goal: str,
    on_update: Optional[Callable[[str, str, bool], None]] = None,
) -> Tuple[bool, str, str]:
    thinking_level = str(ctx.thinking_level or "").strip().lower()
    metadata: Dict[str, Any] = {
        "trust_mode": ctx.trust.key,
        "execution_target": ctx.target.key,
        "source": "live_tui_chat",
    }
    if ctx.pack.key:
        metadata["outcome_pack"] = ctx.pack.key
        metadata["outcome_pack_label"] = ctx.pack.label
    if thinking_level and thinking_level != "off":
        metadata["thinking_level"] = thinking_level

    payload: Dict[str, Any] = {
        "engine": ctx.engine,
        "workspace_id": ctx.workspace_id,
        "user_goal": goal,
        "metadata": metadata,
    }
    provider_override = str(ctx.provider_override or "").strip().lower()
    model_override = str(ctx.model_override or "").strip()
    if provider_override:
        payload["provider"] = provider_override
    if model_override:
        payload["model"] = model_override

    try:
        precheck = ctx.client.precheck_run(payload)
        policy = precheck.get("tool_policy_precheck") if isinstance(precheck.get("tool_policy_precheck"), dict) else {}
        blocked_count = int(policy.get("blocked_count") or 0)
        approval_count = int(policy.get("approval_required_count") or 0)
        allow_count = int(policy.get("allow_count") or 0)
        if blocked_count or approval_count:
            policy_msg = f"Policy precheck: allow={allow_count} approval={approval_count} blocked={blocked_count}"
            if on_update:
                on_update("", policy_msg, False)
        if blocked_count > 0:
            blocked = policy.get("blocked") if isinstance(policy.get("blocked"), list) else []
            blocked_preview = ", ".join([str(item) for item in blocked[:5]]) if blocked else "unknown"
            return False, "", f"Run blocked by tool policy before start ({blocked_preview})."
    except Exception:
        pass

    started = ctx.client.start_run(payload)
    run_id = str(started.get("run_id") or "").strip()
    if not run_id:
        raise RuntimeError("Run started without run_id.")

    timeout_s_raw = os.getenv("ORION_CLI_CHAT_RUN_TIMEOUT_SECONDS", "180").strip()
    try:
        timeout_s = max(20, int(timeout_s_raw))
    except Exception:
        timeout_s = 180

    spinner = ProgressSpinner("running") if _chat_spinner_enabled() else None
    if spinner:
        spinner.tick("running")
    deadline = time.monotonic() + float(timeout_s)
    status = ""
    run_state: Dict[str, Any] = {}
    stream_failed = False
    stream_summary = ""
    if _chat_stream_enabled():
        try:
            status = _consume_run_stream(
                ctx=ctx,
                run_id=run_id,
                timeout_seconds=timeout_s,
                spinner=spinner,
                on_update=on_update,
            )
        except Exception:
            stream_failed = True
            if spinner:
                spinner.tick("running")
    if not status or status not in {"completed", "failed"}:
        while time.monotonic() < deadline:
            run_state = ctx.client.get_run(run_id)
            status = str(run_state.get("status") or "").strip().lower()
            if status in {"waiting_for_input", "awaiting_approval", "paused"}:
                if spinner:
                    spinner.tick("waiting for approval")
                if on_update:
                    on_update(run_id, "Approval required.", False)
            elif status in {"queued_local", "starting", "running", "running_local", "queued"}:
                if spinner:
                    spinner.tick("running")
            elif status in {"completed", "failed", "timeout", "error", "aborted", "cancelled"}:
                break
            time.sleep(1.0)
    else:
        try:
            run_state = ctx.client.get_run(run_id)
            status = str(run_state.get("status") or status).strip().lower()
        except Exception:
            # Stream path already reached terminal state; keep stream-derived status.
            pass

    if not run_state:
        try:
            run_state = ctx.client.get_run(run_id)
            status = str(run_state.get("status") or status).strip().lower()
        except Exception:
            run_state = {}
    stream_summary = _extract_chat_run_summary(run_state)
    if not stream_summary and stream_failed:
        stream_summary = "Run completed." if status == "completed" else "Run finished."

    if status == "completed":
        if spinner:
            spinner.done(True, "Empyralis run completed.")
        return True, run_id, stream_summary
    if status in {"failed", "timeout", "error", "aborted", "cancelled"}:
        if spinner:
            spinner.done(False, "Empyralis run failed.")
        return False, run_id, stream_summary

    if spinner:
        spinner.done(False, "Empyralis run timeout.")
    return False, run_id, stream_summary


def send_chat_telegram_message(
    ctx: TUIContext,
    text: str,
    *,
    session_filter: Optional[str] = None,
    channel_filter: Optional[str] = None,
) -> Tuple[bool, str]:
    message = str(text or "").strip()
    if not message:
        return False, "Message text is required."
    payload: Dict[str, Any] = {"workspace_id": ctx.workspace_id, "text": message}
    session_key = str(session_filter or "").strip()
    if session_key.startswith("telegram:"):
        payload["session_key"] = session_key
    result = ctx.client.send_telegram_message(payload)
    chat_id = str(result.get("chat_id") or "").strip()
    connector_label = str(result.get("connector_label") or "Telegram Bot").strip()
    if chat_id:
        return True, f"Sent to Telegram chat {chat_id} via {connector_label}."
    return True, f"Sent to Telegram via {connector_label}."


def _run_chat_goal_supports_updates(run_chat_goal_fn: Callable[..., Tuple[bool, str, str]]) -> bool:
    try:
        signature = inspect.signature(run_chat_goal_fn)
    except Exception:
        return False
    for parameter in signature.parameters.values():
        if parameter.kind == inspect.Parameter.VAR_KEYWORD:
            return True
    return "on_update" in signature.parameters


def _pending_approvals_for_chat(ctx: TUIContext) -> List[Dict[str, str]]:
    try:
        return _pending_approvals(ctx.api_url, ctx.api_key, ctx.workspace_id)
    except Exception:
        return []


def _pending_cognitive_approvals_for_chat(ctx: TUIContext) -> List[Dict[str, Any]]:
    try:
        return ctx.client.list_cognitive_approvals(limit=20)
    except Exception:
        return []


def _approval_row_timestamp(*values: Any) -> str:
    for raw in values:
        text = str(raw or "").strip()
        if text:
            return text
    return _iso_now()


def _inline_approval_rows(state: _ChatShellState, *, limit: int = 4) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    objective_items = list(state.pending_objective_items or [])
    runtime_items = list(state.pending_runtime_items or [])

    for item in objective_items[: max(0, limit)]:
        if not isinstance(item, dict):
            continue
        event_id = str(item.get("event_id") or "").strip()
        if not event_id:
            continue
        short = event_id[:8]
        objective = preview_text(item.get("objective_title") or item.get("objective_id") or "", 28)
        summary = preview_text(item.get("summary") or item.get("event_text") or "", 92)
        prefix = f"Objective approval required [{short}]"
        if objective:
            prefix += f" ({objective})"
        action_hint = f"Use /ok {short} or /hold {short} <reason>."
        text = f"{prefix}. {preview_text(summary, 84)} {action_hint}".strip()
        rows.append(
            {
                "ts": _approval_row_timestamp(item.get("updated_at"), item.get("created_at")),
                "role": "system",
                "channel": "terminal",
                "run_id": str(item.get("run_id") or "").strip(),
                "event_type": "approval",
                "text": text,
                "metadata": {
                    "approval_inline": True,
                    "approval_kind": "objective",
                    "event_id": event_id,
                    "objective_id": str(item.get("objective_id") or "").strip(),
                },
            }
        )

    remaining = max(0, int(limit) - len(rows))
    for item in runtime_items[:remaining]:
        if not isinstance(item, dict):
            continue
        run_id = str(item.get("run_id") or "").strip()
        approval_id = str(item.get("approval_id") or "").strip()
        if not run_id or not approval_id:
            continue
        short = run_id[:8]
        reason = preview_text(item.get("reason") or "", 92)
        action_hint = f"Use /ok {short} or /hold {short} <reason>."
        text = f"Runtime approval required [{short}]. {preview_text(reason, 84)} {action_hint}".strip()
        rows.append(
            {
                "ts": _approval_row_timestamp(item.get("updated_at"), item.get("requested_at"), item.get("expires_at")),
                "role": "system",
                "channel": "terminal",
                "run_id": run_id,
                "event_type": "approval",
                "text": text,
                "metadata": {
                    "approval_inline": True,
                    "approval_kind": "runtime",
                    "approval_id": approval_id,
                    "run_id": run_id,
                },
            }
        )
    return rows


def _session_summaries_for_chat(ctx: TUIContext, state: _ChatShellState, limit: int = 120) -> List[Dict[str, Any]]:
    list_fn = getattr(ctx.client, "list_inbox_sessions", None)
    if not callable(list_fn):
        return []
    try:
        safe_limit = max(1, min(int(limit), 200))
    except Exception:
        safe_limit = 120
    try:
        items = list_fn(
            workspace_id=ctx.workspace_id,
            limit=safe_limit,
            channel=state.channel_filter,
        )
    except Exception:
        return []
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    return []


def _session_rail_line(
    *,
    sessions: List[Dict[str, Any]],
    session_filter: Optional[str],
    channel_filter: Optional[str],
) -> str:
    def _short_channel(value: str) -> str:
        token = str(value or "").strip().lower()
        if token == "telegram":
            return "tg"
        if token == "whatsapp":
            return "wa"
        if token == "terminal":
            return "tm"
        return token[:2] or "na"

    def _relative_age(ts_raw: Any) -> str:
        text = str(ts_raw or "").strip()
        if not text:
            return "-"
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            delta = max(0, int((now - dt).total_seconds()))
        except Exception:
            return "-"
        if delta < 60:
            return "<1m"
        minutes = delta // 60
        if minutes < 60:
            return f"{minutes}m"
        hours = minutes // 60
        if hours < 24:
            return f"{hours}h"
        days = hours // 24
        return f"{days}d"

    def _session_label(sid: str) -> str:
        token = str(sid or "").strip()
        if ":" in token:
            token = token.split(":", 1)[1]
        if len(token) <= 16:
            return token
        return token[:15] + "…"

    if not sessions:
        return ""
    chips: List[str] = []
    active_session = str(session_filter or "").strip()
    active_channel = str(channel_filter or "").strip().lower()
    for item in sessions[:4]:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("session_id") or item.get("session_key") or "").strip()
        if not sid:
            continue
        channel = str(item.get("channel") or "unknown").strip().lower()
        count = int(item.get("event_count") or 0)
        inbound = int(item.get("inbound_count") or 0)
        outbound = int(item.get("outbound_count") or 0)
        backlog = max(0, inbound - outbound)
        age = _relative_age(item.get("last_ts"))
        label = _session_label(sid)
        marker = "●" if (active_session and sid == active_session) or (not active_session and active_channel and channel == active_channel) else "○"
        tail = f" +{backlog}" if backlog > 0 else ""
        chips.append(f"{marker} {_short_channel(channel)}:{label} {count}@{age}{tail}")
    if not chips:
        return ""
    return "threads: " + " | ".join(chips)


def _cognitive_objective_runtime() -> Tuple[str, str, str, str]:
    root = Path(__file__).resolve().parents[4]
    daemon = root / "python_engine" / "cognitive_daemon.py"
    python_bin = root / "python_engine" / ".venv" / "bin" / "python"
    python_exec = str(python_bin) if python_bin.exists() else sys.executable
    niche_id = str(os.getenv("ORION_COGNITIVE_NICHE_ID", "astronomy")).strip() or "astronomy"
    state_home = Path(os.getenv("EMPYRALIS_STATE_HOME", str(Path.home() / ".empyralis" / "state"))).expanduser()
    db_path = str(
        os.getenv(
            "ORION_COGNITIVE_DB_PATH",
            str(state_home / "memory" / "agency_memory.db"),
        )
    ).strip()
    return python_exec, str(daemon), niche_id, db_path


def _run_cognitive_objective_command(args: List[str]) -> Dict[str, Any]:
    python_exec, daemon_path, niche_id, db_path = _cognitive_objective_runtime()
    cmd = [
        python_exec,
        daemon_path,
        *args,
        "--niche-id",
        niche_id,
        "--db-path",
        db_path,
    ]
    completed = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=25,
        check=False,
    )
    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    payload: Dict[str, Any] = {}
    if stdout:
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        for line in reversed(lines):
            if not (line.startswith("{") and line.endswith("}")):
                continue
            try:
                parsed = json.loads(line)
                if isinstance(parsed, dict):
                    payload = parsed
                    break
            except Exception:
                continue
    if payload:
        return payload
    detail = stderr or stdout or f"exit={completed.returncode}"
    return {"ok": False, "error": detail}


def _resolve_pending_approval_interactive(ctx: TUIContext) -> Tuple[bool, str]:
    pending_events = _pending_cognitive_approvals_for_chat(ctx)
    pending_runs = _pending_approvals_for_chat(ctx)
    if not pending_events and not pending_runs:
        return False, "No pending approvals."

    options: List[Choice] = []
    for item in pending_events[:20]:
        event_id = str(item.get("event_id") or "").strip()
        if not event_id:
            continue
        risk = str(item.get("risk_level") or "").strip() or "unknown"
        objective = str(item.get("objective_title") or item.get("objective_id") or "").strip()
        summary = preview_text(item.get("summary") or item.get("event_text") or "", 92)
        label = f"Objective event {event_id[:8]} ({risk})"
        note = f"{preview_text(objective, 28)} · {summary}" if objective else summary
        options.append(Choice(f"event::{event_id}", label, note))

    for item in pending_runs[:20]:
        run_id = str(item.get("run_id") or "").strip()
        approval_id = str(item.get("approval_id") or "").strip()
        if not run_id or not approval_id:
            continue
        reason = preview_text(item.get("reason") or approval_id, 92)
        label = f"Run approval {run_id[:8]}"
        options.append(Choice(f"run::{run_id}::{approval_id}", label, reason))

    if not options:
        return False, "No pending approvals."

    options.append(Choice("cancel", "Cancel", "Return to chat"))
    selected = ctx.ui.choose(
        "Resolve pending approval",
        options,
        default_index=0,
        title=q.TITLE_LIVE_TUI,
    )
    if selected.key == "cancel":
        return False, "Approval menu cancelled."

    decision_choice = ctx.ui.choose(
        q.Q_APPROVAL_DECISION,
        [
            Choice("approve", "Approve", "Proceed with requested action"),
            Choice("reject", "Reject", "Block this action"),
        ],
        default_index=0,
        title=q.TITLE_LIVE_TUI,
    )
    note = ctx.ui.input(q.Q_APPROVAL_NOTE, required=False, title=q.TITLE_LIVE_TUI).strip()
    decision = str(decision_choice.key or "approve").strip().lower()
    if selected.key.startswith("event::"):
        event_id = selected.key.split("::", 1)[1]
        response_note = note or ("Rejected from Live TUI." if decision == "reject" else None)
        try:
            result = ctx.client.resolve_cognitive_approval(
                event_id=event_id,
                decision=decision,
                note=response_note,
            )
            status = str(result.get("status") or "pending")
            action = "Approved" if decision == "approve" else "Rejected"
            return True, f"{action} event {event_id[:8]} ({status})."
        except Exception as exc:
            return False, f"Failed to resolve event {event_id[:8]}: {exc}"

    run_id = ""
    approval_id = ""
    parts = selected.key.split("::")
    if len(parts) == 3:
        _, run_id, approval_id = parts
    if not run_id or not approval_id:
        return False, "Selected approval entry is invalid."
    response_note = note or ("Rejected from Live TUI." if decision == "reject" else None)
    try:
        result = ctx.client.resolve_approval(
            run_id=run_id,
            approval_id=approval_id,
            decision=decision,
            note=response_note,
        )
        status = str(result.get("status") or "ok")
        action = "Approved" if decision == "approve" else "Rejected"
        return True, f"{action} run {run_id[:8]} ({status})."
    except Exception as exc:
        return False, f"Failed to resolve run {run_id[:8]}: {exc}"


def _resolve_pending_approval_ref(items: List[Dict[str, str]], ref: str) -> Optional[Dict[str, str]]:
    token = str(ref or "").strip()
    if not token:
        return None
    lowered = token.lower()
    for item in items:
        run_id = str(item.get("run_id") or "").strip()
        if not run_id:
            continue
        if lowered == run_id.lower() or lowered == run_id[:8].lower():
            return item
    return None


def _resolve_cognitive_event_ref(items: List[Dict[str, Any]], ref: str) -> Optional[Dict[str, Any]]:
    token = str(ref or "").strip()
    if not token:
        return None
    lowered = token.lower()
    for item in items:
        event_id = str(item.get("event_id") or "").strip()
        if not event_id:
            continue
        if lowered == event_id.lower() or lowered == event_id[:8].lower():
            return item
    return None


def _objective_matches_ref(item: Dict[str, Any], ref: str) -> bool:
    token = str(ref or "").strip().lower()
    if not token:
        return False
    objective_id = str(item.get("objective_id") or "").strip()
    objective_title = str(item.get("objective_title") or "").strip().lower()
    if objective_id and (token == objective_id.lower() or objective_id.lower().startswith(token)):
        return True
    return bool(objective_title and token in objective_title)


def _objective_match_entry(items: List[Dict[str, Any]], ref: str) -> Optional[Dict[str, Any]]:
    token = str(ref or "").strip().lower()
    if not items:
        return None
    if not token:
        for item in items:
            if str(item.get("status") or "").strip().lower() == "active":
                return item
        return items[0]
    for item in items:
        objective_id = str(item.get("id") or "").strip().lower()
        if objective_id and (token == objective_id or objective_id.startswith(token)):
            return item
    for item in items:
        title = str(item.get("title") or "").strip().lower()
        if title and token in title:
            return item
    return None


def _approval_sort_ts(item: Dict[str, Any]) -> float:
    for key in ("updated_at", "finished_at", "started_at", "created_at"):
        raw = item.get(key)
        try:
            return float(raw)
        except Exception:
            pass
    return 0.0


def _split_ref_and_note(argument: str) -> Tuple[str, str]:
    text = str(argument or "").strip()
    if not text:
        return "", ""
    parts = text.split(maxsplit=1)
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1].strip()


def _format_objective_list(items: List[Dict[str, Any]]) -> str:
    if not items:
        return "No objectives."
    lines = ["Objectives:"]
    for item in items[:20]:
        objective_id = str(item.get("id") or "").strip()
        short = objective_id[:8] if objective_id else "unknown"
        status = str(item.get("status") or "unknown")
        priority = str(item.get("priority") or "70")
        title = preview_text(item.get("title") or "Untitled objective", 60)
        lines.append(f"- {short} status={status} priority={priority} {title}".rstrip())
    return "\n".join(lines)


def _format_objective_detail(payload: Dict[str, Any]) -> str:
    objective = payload.get("objective") if isinstance(payload.get("objective"), dict) else {}
    if not objective:
        return "Objective not found."
    lines = [
        f"id: {str(objective.get('id') or '')}",
        f"title: {str(objective.get('title') or '')}",
        f"status: {str(objective.get('status') or '')}",
        f"priority: {str(objective.get('priority') or '')}",
        f"goal: {preview_text(objective.get('goal_text') or '', 140)}",
        f"next_run_at: {str(objective.get('next_run_at') or '')}",
        f"last_run_at: {str(objective.get('last_run_at') or '')}",
    ]
    return "\n".join(lines)


def _extract_skill_replay_for_event(event: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(event, dict):
        return {}
    if isinstance(event.get("skill_replay"), dict):
        return event.get("skill_replay") or {}
    metadata = event.get("metadata")
    if isinstance(metadata, dict) and isinstance(metadata.get("skill_replay"), dict):
        return metadata.get("skill_replay") or {}
    result = event.get("result") if isinstance(event.get("result"), dict) else {}
    tick = result.get("tick") if isinstance(result.get("tick"), dict) else {}
    observation = tick.get("observation") if isinstance(tick.get("observation"), dict) else {}
    obs_meta = observation.get("metadata") if isinstance(observation.get("metadata"), dict) else {}
    replay = obs_meta.get("skill_replay") if isinstance(obs_meta.get("skill_replay"), dict) else {}
    return replay or {}


def _format_skill_replay_text(replay: Dict[str, Any]) -> str:
    if not replay:
        return ""
    status = "applied" if bool(replay.get("applied")) else "fallback"
    try:
        conf = float(replay.get("confidence"))
        conf_text = f"{conf:.2f}"
    except Exception:
        conf_text = "-"
    signature = preview_text(replay.get("signature") or "", 24)
    reason = preview_text(replay.get("reason") or "", 48)
    parts = [f"skill_replay: {status}", f"conf={conf_text}"]
    if signature:
        parts.append(f"sig={signature}")
    if reason:
        parts.append(f"reason={reason}")
    return " ".join(parts)


def _format_objective_tail(payload: Dict[str, Any]) -> str:
    if not bool(payload.get("ok")):
        return f"Objective tail failed: {str(payload.get('error') or 'unknown')}"
    objective = payload.get("objective") if isinstance(payload.get("objective"), dict) else {}
    events = payload.get("events") if isinstance(payload.get("events"), list) else []
    reflections = payload.get("reflections") if isinstance(payload.get("reflections"), list) else []
    title = str(objective.get("title") or "objective")
    objective_id = str(objective.get("id") or "")
    lines = [
        f"Objective tail: {title} [{objective_id[:8]}]",
        f"status={str(objective.get('status') or 'unknown')} priority={str(objective.get('priority') or '70')}",
        f"events={len(events)} reflections={len(reflections)}",
    ]
    if events:
        latest = events[0] if isinstance(events[0], dict) else {}
        event_id = str(latest.get("id") or "")[:8]
        event_status = str(latest.get("status") or "unknown")
        summary = ""
        digest = latest.get("digest") if isinstance(latest.get("digest"), dict) else {}
        if digest:
            summary = str(digest.get("one_line_summary") or "")
        if not summary:
            result = latest.get("result") if isinstance(latest.get("result"), dict) else {}
            tick = result.get("tick") if isinstance(result.get("tick"), dict) else {}
            tick_result = tick.get("result") if isinstance(tick.get("result"), dict) else {}
            summary = str(tick_result.get("one_line_summary") or "")
        lines.append(f"latest_event: {event_id} status={event_status} {preview_text(summary, 120)}".rstrip())
        replay = _extract_skill_replay_for_event(latest)
        replay_line = _format_skill_replay_text(replay)
        if replay_line:
            lines.append(replay_line)
    if reflections:
        latest_ref = reflections[0] if isinstance(reflections[0], dict) else {}
        passed = bool(latest_ref.get("passed"))
        confidence = latest_ref.get("confidence")
        lesson = preview_text(latest_ref.get("lesson") or "", 120)
        lines.append(f"latest_reflection: passed={passed} confidence={confidence} {lesson}".rstrip())
    return "\n".join(lines)


def _build_chat_command_registry(
    *,
    ctx: TUIContext,
    state: _ChatShellState,
    handle_mode_fn: Callable[[TUIContext], Optional[str]],
    handle_trust_fn: Callable[[TUIContext], Optional[str]],
    handle_target_fn: Callable[[TUIContext], Optional[str]],
    print_doctor_summary_fn: Callable[[Any, str, str], None],
    inbox_events_fn: Callable[..., List[Dict[str, Any]]],
) -> ChatCommandRegistry:
    registry = ChatCommandRegistry()

    def _ok(*, reset_frame: bool = False, exit_chat: bool = False) -> ChatCommandResult:
        return ChatCommandResult(handled=True, reset_frame=reset_frame, exit_chat=exit_chat)

    def _cmd_help(_: ChatCommandInput) -> ChatCommandResult:
        _chat_push(ctx, "system", help_text())
        return _ok()

    def _cmd_engine(item: ChatCommandInput) -> ChatCommandResult:
        if not item.argument:
            _chat_push(ctx, "system", _open_engine_picker(ctx))
        else:
            _chat_push(ctx, "system", _set_engine(ctx, item.argument))
        return _ok(reset_frame=True)

    def _cmd_exit(_: ChatCommandInput) -> ChatCommandResult:
        return _ok(exit_chat=True)

    def _cmd_refresh(_: ChatCommandInput) -> ChatCommandResult:
        return _ok(reset_frame=True)

    def _cmd_route(item: ChatCommandInput) -> ChatCommandResult:
        if _one_app_mode_enabled():
            _chat_push(ctx, "system", _apply_input_route_choice(state, "local_run"))
            return _ok(reset_frame=True)
        if not item.argument:
            route_choice = ctx.ui.choose(
                q.Q_LIVE_TUI_CHAT_ROUTE,
                _input_route_options(),
                default_index=1 if state.input_route == "telegram_send" else 0,
                title=q.TITLE_LIVE_TUI,
            )
            _chat_push(ctx, "system", _apply_input_route_choice(state, route_choice.key))
            return _ok(reset_frame=True)
        raw_route = item.argument.strip().lower()
        route_key = "local_run"
        if raw_route in {"telegram", "telegram_send", "tg"}:
            route_key = "telegram_send"
        _chat_push(ctx, "system", _apply_input_route_choice(state, route_key))
        return _ok(reset_frame=True)

    def _cmd_session(item: ChatCommandInput) -> ChatCommandResult:
        session_action, session_arg = _parse_session_command(item.raw)
        if not session_action:
            return ChatCommandResult(handled=False)
        state.manual_session_override = True
        if session_action == "all":
            _chat_push(ctx, "system", _apply_session_choice(state, "__all__"))
            return _ok(reset_frame=True)
        if session_action == "list":
            all_events = inbox_events_fn(
                api_url=ctx.api_url,
                api_key=ctx.api_key,
                workspace_id=ctx.workspace_id,
                limit=200,
                channel=state.channel_filter,
            )
            _chat_push(
                ctx,
                "system",
                _open_session_overlay(
                    ctx,
                    state,
                    all_events,
                    sessions=_session_summaries_for_chat(ctx, state, limit=160),
                ),
            )
            return _ok(reset_frame=True)
        if session_action == "use":
            if not session_arg:
                _chat_push(
                    ctx,
                    "system",
                    _open_session_overlay(
                        ctx,
                        state,
                        item.recent_events,
                        sessions=_session_summaries_for_chat(ctx, state, limit=160),
                    ),
                )
                return _ok(reset_frame=True)
            lowered_arg = session_arg.strip().lower()
            if lowered_arg in {"all", "*"}:
                _chat_push(ctx, "system", _apply_session_choice(state, "__all__"))
                return _ok(reset_frame=True)
            if lowered_arg in {"telegram", "whatsapp"}:
                choice_key = "__telegram__" if lowered_arg == "telegram" else "__whatsapp__"
                _chat_push(ctx, "system", _apply_session_choice(state, choice_key))
                return _ok(reset_frame=True)
            _chat_push(ctx, "system", _apply_session_choice(state, f"session:{session_arg}"))
            return _ok(reset_frame=True)
        return _ok()

    def _cmd_status(_: ChatCommandInput) -> ChatCommandResult:
        _chat_push(
            ctx,
            "system",
            _live_tui_status_summary(
                api_url=ctx.api_url,
                api_key=ctx.api_key,
                workspace_id=ctx.workspace_id,
                pack=ctx.pack,
                trust=ctx.trust,
                target=ctx.target,
            ),
        )
        return _ok()

    def _cmd_agent(item: ChatCommandInput) -> ChatCommandResult:
        if not item.argument:
            selected = ctx.ui.choose(
                q.Q_LIVE_TUI_AGENT_MODE,
                _agent_mode_options(),
                default_index=0,
                title=q.TITLE_LIVE_TUI,
            )
            _chat_push(ctx, "system", _set_agent_mode(ctx, selected))
            return _ok(reset_frame=True)
        selected = _resolve_agent_mode(item.argument.strip())
        if not selected:
            available = ", ".join(
                [choice.key for choice in _agent_mode_options() if str(choice.key or "").strip()] or ["general"]
            )
            _chat_push(ctx, "system", f"Unknown agent '{item.argument}'. Available: {available}")
            return _ok()
        _chat_push(ctx, "system", _set_agent_mode(ctx, selected))
        return _ok(reset_frame=True)

    def _cmd_model(item: ChatCommandInput) -> ChatCommandResult:
        if not item.argument:
            try:
                _chat_push(ctx, "system", _open_model_picker(ctx))
            except Exception as exc:
                _chat_push(ctx, "system", f"Model picker failed: {exc}")
            return _ok(reset_frame=True)
        _chat_push(ctx, "system", _set_model_route(ctx, item.argument))
        return _ok(reset_frame=True)

    def _cmd_think(item: ChatCommandInput) -> ChatCommandResult:
        if not item.argument:
            _chat_push(
                ctx,
                "system",
                f"Thinking is {str(ctx.thinking_level or 'off')}. Usage: /think <off|low|medium|high|auto>",
            )
            return _ok()
        _chat_push(ctx, "system", _set_think_level(ctx, item.argument))
        return _ok(reset_frame=True)

    def _cmd_verbose(item: ChatCommandInput) -> ChatCommandResult:
        if not item.argument:
            _chat_push(ctx, "system", f"Verbose is {str(ctx.verbose_level or 'off')}. Usage: /verbose <on|off>")
            return _ok()
        _chat_push(ctx, "system", _set_verbose_level(ctx, item.argument))
        return _ok(reset_frame=True)

    def _cmd_reasoning(item: ChatCommandInput) -> ChatCommandResult:
        if not item.argument:
            _chat_push(ctx, "system", f"Reasoning is {str(ctx.reasoning_level or 'off')}. Usage: /reasoning <on|off>")
            return _ok()
        _chat_push(ctx, "system", _set_reasoning_level(ctx, item.argument))
        return _ok(reset_frame=True)

    def _cmd_usage(item: ChatCommandInput) -> ChatCommandResult:
        if not item.argument:
            _chat_push(ctx, "system", f"Usage display is {str(ctx.usage_level or 'off')}. Usage: /usage <off|tokens|full>")
            return _ok()
        _chat_push(ctx, "system", _set_usage_level(ctx, item.argument))
        return _ok(reset_frame=True)

    def _cmd_elevated(item: ChatCommandInput) -> ChatCommandResult:
        if not item.argument:
            _chat_push(
                ctx,
                "system",
                f"Elevated execution is {str(ctx.elevated_level or 'off')}. Usage: /elevated <on|off|ask|full>",
            )
            return _ok()
        _chat_push(ctx, "system", _set_elevated_level(ctx, item.argument))
        return _ok(reset_frame=True)

    def _cmd_activation(item: ChatCommandInput) -> ChatCommandResult:
        if not item.argument:
            _chat_push(
                ctx,
                "system",
                f"Activation mode is {str(ctx.activation_level or 'mention')}. Usage: /activation <mention|always>",
            )
            return _ok()
        _chat_push(ctx, "system", _set_activation_level(ctx, item.argument))
        return _ok(reset_frame=True)

    def _cmd_settings(item: ChatCommandInput) -> ChatCommandResult:
        if not item.argument:
            _chat_push(ctx, "system", _settings_summary(ctx, route_label=item.route_label, session_label=item.session_label))
            return _ok()
        action = item.argument.strip().lower()
        if action in {"reset", "defaults"}:
            ctx.thinking_level = "off"
            ctx.verbose_level = "off"
            ctx.reasoning_level = "off"
            ctx.usage_level = "off"
            ctx.elevated_level = "off"
            ctx.activation_level = "mention"
            _chat_push(ctx, "system", _clear_model_route(ctx))
            _chat_push(ctx, "system", "Chat settings reset to defaults.")
            return _ok(reset_frame=True)
        _chat_push(ctx, "system", "Usage: /settings or /settings reset")
        return _ok(reset_frame=True)

    def _cmd_mode(_: ChatCommandInput) -> ChatCommandResult:
        handle_mode_fn(ctx)
        return _ok(reset_frame=True)

    def _cmd_trust(_: ChatCommandInput) -> ChatCommandResult:
        handle_trust_fn(ctx)
        return _ok(reset_frame=True)

    def _cmd_target(_: ChatCommandInput) -> ChatCommandResult:
        handle_target_fn(ctx)
        return _ok(reset_frame=True)

    def _cmd_events(item: ChatCommandInput) -> ChatCommandResult:
        _chat_push(ctx, "system", _format_inbox_events(item.recent_events[:20]))
        return _ok()

    def _cmd_dashboard(_: ChatCommandInput) -> ChatCommandResult:
        _chat_push(
            ctx,
            "system",
            _dashboard_snapshot(
                api_url=ctx.api_url,
                api_key=ctx.api_key,
                workspace_id=ctx.workspace_id,
                pack=ctx.pack,
                trust=ctx.trust,
                target=ctx.target,
            ),
        )
        return _ok()

    def _cmd_doctor(_: ChatCommandInput) -> ChatCommandResult:
        print_doctor_summary_fn(ctx.ui, ctx.api_url, ctx.api_key)
        return _ok(reset_frame=True)

    def _cmd_approvals(_: ChatCommandInput) -> ChatCommandResult:
        arg = _.argument.strip().lower()
        if arg in {"resolve", "menu"}:
            ok_result, message = _resolve_pending_approval_interactive(ctx)
            _chat_push(ctx, "system", message)
            return _ok(reset_frame=ok_result)
        pending_events = _pending_cognitive_approvals_for_chat(ctx)
        pending_runs = _pending_approvals_for_chat(ctx)
        if not pending_events and not pending_runs:
            _chat_push(ctx, "system", "No pending approvals.")
            return _ok()
        lines: List[str] = []
        if pending_events:
            lines.append("Pending cognitive approvals:")
            for item in pending_events[:8]:
                event_id = str(item.get("event_id") or "").strip()
                short = event_id[:8] if event_id else "unknown"
                risk = str(item.get("risk_level") or "").strip() or "unknown"
                objective = str(item.get("objective_title") or item.get("objective_id") or "").strip()
                summary = preview_text(item.get("summary") or item.get("event_text") or "", 88)
                objective_text = f" objective={preview_text(objective, 30)}" if objective else ""
                lines.append(f"- event {short} risk={risk}{objective_text} {summary}".rstrip())
            lines.append("Use /approve-event <event_id_or_short> [note] or /reject-event <event_id_or_short> <reason>")
        if pending_runs:
            if lines:
                lines.append("")
            lines.append("Pending runtime approvals:")
            for item in pending_runs[:8]:
                run_id = str(item.get("run_id") or "").strip()
                approval_id = str(item.get("approval_id") or "").strip()
                reason = preview_text(item.get("reason") or "", 96)
                lines.append(f"- run {run_id[:8]} approval={approval_id[:8]} {reason}".rstrip())
            lines.append("Use /approve <run_id_or_short> [note] or /reject <run_id_or_short> <reason>")
        lines.append("Tip: run /approvals resolve (or /approval-menu) for interactive resolution.")
        _chat_push(ctx, "system", "\n".join(lines))
        return _ok()

    def _cmd_approval_menu(_: ChatCommandInput) -> ChatCommandResult:
        ok_result, message = _resolve_pending_approval_interactive(ctx)
        _chat_push(ctx, "system", message)
        return _ok(reset_frame=ok_result)

    def _cmd_approve_event(item: ChatCommandInput) -> ChatCommandResult:
        pending_events = _pending_cognitive_approvals_for_chat(ctx)
        if not pending_events:
            _chat_push(ctx, "system", "No pending cognitive approvals.")
            return _ok()
        ref, note = _split_ref_and_note(item.argument)
        if not ref:
            if len(pending_events) == 1:
                selected = pending_events[0]
            else:
                _chat_push(
                    ctx,
                    "system",
                    "Multiple pending cognitive approvals. Use /approvals then /approve-event <event_id_or_short> [note].",
                )
                return _ok()
        else:
            selected = _resolve_cognitive_event_ref(pending_events, ref)
        if not selected:
            _chat_push(ctx, "system", "Event not found in pending cognitive approvals. Use /approvals.")
            return _ok()
        event_id = str(selected.get("event_id") or "").strip()
        try:
            result = ctx.client.resolve_cognitive_approval(
                event_id=event_id,
                decision="approve",
                note=note or None,
            )
            status = str(result.get("status") or "pending")
            _chat_push(ctx, "system", f"Approved event {event_id[:8]} ({status}).")
        except Exception as exc:
            _chat_push(ctx, "system", f"Approval failed for event {event_id[:8]}: {exc}")
        return _ok(reset_frame=True)

    def _cmd_reject_event(item: ChatCommandInput) -> ChatCommandResult:
        pending_events = _pending_cognitive_approvals_for_chat(ctx)
        if not pending_events:
            _chat_push(ctx, "system", "No pending cognitive approvals.")
            return _ok()
        ref, note = _split_ref_and_note(item.argument)
        if not ref:
            if len(pending_events) == 1:
                selected = pending_events[0]
            else:
                _chat_push(
                    ctx,
                    "system",
                    "Multiple pending cognitive approvals. Use /approvals then /reject-event <event_id_or_short> <reason>.",
                )
                return _ok()
        else:
            selected = _resolve_cognitive_event_ref(pending_events, ref)
        if not selected:
            _chat_push(ctx, "system", "Event not found in pending cognitive approvals. Use /approvals.")
            return _ok()
        event_id = str(selected.get("event_id") or "").strip()
        reason = note or "Rejected from Live TUI."
        try:
            result = ctx.client.resolve_cognitive_approval(
                event_id=event_id,
                decision="reject",
                note=reason,
            )
            status = str(result.get("status") or "failed")
            _chat_push(ctx, "system", f"Rejected event {event_id[:8]} ({status}).")
        except Exception as exc:
            _chat_push(ctx, "system", f"Reject failed for event {event_id[:8]}: {exc}")
        return _ok(reset_frame=True)

    def _cmd_approve(item: ChatCommandInput) -> ChatCommandResult:
        pending = _pending_approvals_for_chat(ctx)
        if not pending:
            _chat_push(ctx, "system", "No pending approvals.")
            return _ok()
        ref, note = _split_ref_and_note(item.argument)
        if not ref:
            if len(pending) == 1:
                selected = pending[0]
            else:
                _chat_push(ctx, "system", "Multiple pending approvals. Use /approvals then /approve <run_id_or_short> [note].")
                return _ok()
        else:
            selected = _resolve_pending_approval_ref(pending, ref)
        if not selected:
            _chat_push(ctx, "system", "Run not found in pending approvals. Use /approvals.")
            return _ok()
        run_id = str(selected.get("run_id") or "").strip()
        approval_id = str(selected.get("approval_id") or "").strip()
        try:
            result = ctx.client.resolve_approval(
                run_id=run_id,
                approval_id=approval_id,
                decision="approve",
                note=note or None,
            )
            status = str(result.get("status") or "ok")
            _chat_push(ctx, "system", f"Approved run {run_id[:8]} ({status}).")
        except Exception as exc:
            _chat_push(ctx, "system", f"Approval failed for run {run_id[:8]}: {exc}")
        return _ok(reset_frame=True)

    def _cmd_reject(item: ChatCommandInput) -> ChatCommandResult:
        pending = _pending_approvals_for_chat(ctx)
        if not pending:
            _chat_push(ctx, "system", "No pending approvals.")
            return _ok()
        ref, note = _split_ref_and_note(item.argument)
        if not ref:
            if len(pending) == 1:
                selected = pending[0]
            else:
                _chat_push(ctx, "system", "Multiple pending approvals. Use /approvals then /reject <run_id_or_short> <reason>.")
                return _ok()
        else:
            selected = _resolve_pending_approval_ref(pending, ref)
        if not selected:
            _chat_push(ctx, "system", "Run not found in pending approvals. Use /approvals.")
            return _ok()
        run_id = str(selected.get("run_id") or "").strip()
        approval_id = str(selected.get("approval_id") or "").strip()
        reason = note or "Rejected from Live TUI."
        try:
            result = ctx.client.resolve_approval(
                run_id=run_id,
                approval_id=approval_id,
                decision="reject",
                note=reason,
            )
            status = str(result.get("status") or "ok")
            _chat_push(ctx, "system", f"Rejected run {run_id[:8]} ({status}).")
        except Exception as exc:
            _chat_push(ctx, "system", f"Reject failed for run {run_id[:8]}: {exc}")
        return _ok(reset_frame=True)

    def _resolve_inline_approval(decision: str, argument: str) -> Tuple[bool, str]:
        pending_events = _pending_cognitive_approvals_for_chat(ctx)
        pending_runs = _pending_approvals_for_chat(ctx)
        if not pending_events and not pending_runs:
            return False, "No pending approvals."

        ref, note = _split_ref_and_note(argument)
        normalized = str(decision or "").strip().lower()
        if normalized not in {"approve", "reject"}:
            normalized = "approve"
        reject_default = "Rejected from Live TUI."

        selected_event: Optional[Dict[str, Any]] = None
        selected_run: Optional[Dict[str, str]] = None
        if ref:
            selected_event = _resolve_cognitive_event_ref(pending_events, ref)
            selected_run = _resolve_pending_approval_ref(pending_runs, ref)
            if selected_event and selected_run:
                return False, (
                    "Reference matches both objective and runtime approvals. "
                    "Use /approve-event <event_id_or_short> or /approve <run_id_or_short>."
                )
            if not selected_event and not selected_run:
                return False, "Approval reference not found. Use /approvals."
        else:
            total = len(pending_events) + len(pending_runs)
            if total != 1:
                return False, "Multiple pending approvals. Use /approvals or pass an id (e.g. /ok <id>)."
            if pending_events:
                selected_event = pending_events[0]
            elif pending_runs:
                selected_run = pending_runs[0]

        response_note = note or (reject_default if normalized == "reject" else None)
        verb = "Approved" if normalized == "approve" else "Rejected"

        if selected_event:
            event_id = str(selected_event.get("event_id") or "").strip()
            if not event_id:
                return False, "Selected objective approval is missing event id."
            try:
                result = ctx.client.resolve_cognitive_approval(
                    event_id=event_id,
                    decision=normalized,
                    note=response_note,
                )
                status = str(result.get("status") or "pending")
                return True, f"{verb} event {event_id[:8]} ({status})."
            except Exception as exc:
                return False, f"{verb} failed for event {event_id[:8]}: {exc}"

        if selected_run:
            run_id = str(selected_run.get("run_id") or "").strip()
            approval_id = str(selected_run.get("approval_id") or "").strip()
            if not run_id or not approval_id:
                return False, "Selected runtime approval is invalid."
            try:
                result = ctx.client.resolve_approval(
                    run_id=run_id,
                    approval_id=approval_id,
                    decision=normalized,
                    note=response_note,
                )
                status = str(result.get("status") or "ok")
                return True, f"{verb} run {run_id[:8]} ({status})."
            except Exception as exc:
                return False, f"{verb} failed for run {run_id[:8]}: {exc}"

        return False, "Approval target not found."

    def _cmd_ok(item: ChatCommandInput) -> ChatCommandResult:
        ok_result, message = _resolve_inline_approval("approve", item.argument)
        _chat_push(ctx, "system", message)
        return _ok(reset_frame=ok_result)

    def _cmd_hold(item: ChatCommandInput) -> ChatCommandResult:
        ok_result, message = _resolve_inline_approval("reject", item.argument)
        _chat_push(ctx, "system", message)
        return _ok(reset_frame=ok_result)

    def _cmd_abort(_: ChatCommandInput) -> ChatCommandResult:
        candidates = ctx.client.list_history_runs(ctx.workspace_id, limit=30)
        run_id = ""
        for item in candidates:
            status = str(item.get("status") or "").strip().lower()
            if status == "waiting_for_input":
                run_id = str(item.get("run_id") or "").strip()
                break
        if not run_id:
            _chat_push(ctx, "system", "No pending approval run to abort.")
            return _ok()
        try:
            ctx.client.post_run_decision(run_id, "abort")
            _chat_push(ctx, "system", f"Abort signal sent for run {run_id[:8]}.")
        except Exception as exc:
            _chat_push(ctx, "system", f"Abort failed for run {run_id[:8]}: {exc}")
        return _ok(reset_frame=True)

    def _cmd_new(_: ChatCommandInput) -> ChatCommandResult:
        ctx.chat_local_entries.clear()
        ctx.chat_flags.clear()
        state.prefix_notice_seen.clear()
        state.approval_notice_by_run.clear()
        state.approval_notice_by_event.clear()
        state.last_fetch_error = ""
        state.event_assembler.reset()
        state.session_filter = None
        state.channel_filter = None
        state.manual_session_override = False
        _chat_push(ctx, "system", "Started a new chat session.")
        return _ok(reset_frame=True)

    def _cmd_run(_: ChatCommandInput) -> ChatCommandResult:
        _chat_push(ctx, "system", "Usage: /run <goal>")
        return _ok()

    def _cmd_preflight(_: ChatCommandInput) -> ChatCommandResult:
        try:
            from ...flows_preflight import preflight_flow as _preflight_flow

            rc = _preflight_flow(
                ui=ctx.ui,
                api_url=ctx.api_url,
                api_key=ctx.api_key,
                workspace_id=ctx.workspace_id,
                engine=ctx.engine,
                goal=None,
                auto_confirm=False,
            )
            if rc == 0:
                _chat_push(ctx, "system", "Preflight completed.")
            else:
                _chat_push(ctx, "system", f"Preflight exited with code {rc}.")
        except Exception as exc:
            _chat_push(ctx, "system", f"Preflight failed: {exc}")
        return _ok(reset_frame=True)

    def _cmd_objective(item: ChatCommandInput) -> ChatCommandResult:
        arg = str(item.argument or "").strip()
        try:
            tokens = shlex.split(arg) if arg else []
        except ValueError as exc:
            _chat_push(ctx, "system", f"Objective command parse failed: {exc}")
            return _ok()
        action = str(tokens[0] if tokens else "list").strip().lower()

        def _usage() -> str:
            return (
                "Usage:\n"
                "/objective add \"<title>\" \"<goal>\" [--cadence N] [--priority N] [--sla N] [--target auto|cloud|local_companion] [--trust auto|guarded|strict]\n"
                "/objective list [status] [limit]\n"
                "/objective board [status] [limit]\n"
                "/objective get <objective_id>\n"
                "/objective tail <objective_id> [limit]\n"
                "/objective lessons [objective_id_or_title] [limit]\n"
                "/objective doctor [objective_id_or_title] [--limit N]\n"
                "/objective pause|resume|complete <objective_id>\n"
                "/objective run-now [objective_id_or_title]\n"
                "/objective resume-latest [objective_id_or_title]\n"
                "/objective tick [max_dispatch]\n"
                "/objective approvals [objective_id_or_title]\n"
                "/objective approve-latest [objective_id_or_title] [--note \"...\"]\n"
                "/objective approve|reject <objective_id_or_title> [note]"
            )

        if action == "add":
            parsed = tokens
            if len(parsed) < 3:
                _chat_push(ctx, "system", _usage())
                return _ok()
            title = str(parsed[1]).strip()
            goal = str(parsed[2]).strip()
            if not title or not goal:
                _chat_push(ctx, "system", _usage())
                return _ok()
            cmd = ["objective-add", "--title", title, "--goal", goal]
            idx = 3
            option_map = {
                "--priority": "--priority",
                "--cadence": "--cadence-seconds",
                "--sla": "--sla-seconds",
                "--target": "--execution-target",
                "--trust": "--trust-mode",
                "--max-fail-streak": "--max-fail-streak",
                "--aging-window": "--aging-window-seconds",
                "--aging-max-boost": "--aging-max-boost",
                "--next-run": "--next-run-seconds",
            }
            while idx < len(parsed):
                token = str(parsed[idx]).strip()
                if token in option_map:
                    if (idx + 1) >= len(parsed):
                        _chat_push(ctx, "system", f"Missing value for {token}.")
                        return _ok()
                    cmd.extend([option_map[token], str(parsed[idx + 1]).strip()])
                    idx += 2
                    continue
                if token == "--pause-on-escalation":
                    cmd.append("--pause-on-escalation")
                    idx += 1
                    continue
                if token == "--no-escalation":
                    cmd.append("--disable-escalation")
                    idx += 1
                    continue
                _chat_push(ctx, "system", f"Unknown objective add option: {token}")
                _chat_push(ctx, "system", _usage())
                return _ok()

            out = _run_cognitive_objective_command(cmd)
            if not bool(out.get("ok")):
                _chat_push(ctx, "system", f"Objective add failed: {str(out.get('error') or 'unknown')}")
                return _ok()
            objective = out.get("objective") if isinstance(out.get("objective"), dict) else {}
            objective_id = str(objective.get("id") or "").strip()
            status = str(objective.get("status") or "active").strip()
            summary = f"Objective created: {preview_text(title, 40)} [{objective_id[:8]}] status={status}"
            _chat_push(ctx, "system", summary)
            return _ok(reset_frame=True)

        if action in {"list", "ls"}:
            status = ""
            limit = 10
            for token in tokens[1:]:
                if str(token).isdigit():
                    limit = max(1, min(int(token), 50))
                elif not status:
                    status = str(token).strip().lower()
            cmd = ["objective-list", "--limit", str(limit)]
            if status and status not in {"all", "*"}:
                cmd.extend(["--status", status])
            out = _run_cognitive_objective_command(cmd)
            if not bool(out.get("ok")):
                _chat_push(ctx, "system", f"Objective list failed: {str(out.get('error') or 'unknown')}")
                return _ok()
            items = out.get("items") if isinstance(out.get("items"), list) else []
            _chat_push(ctx, "system", _format_objective_list([i for i in items if isinstance(i, dict)]))
            return _ok()

        if action in {"board", "health"}:
            status = "active"
            limit = 12
            for token in tokens[1:]:
                if str(token).isdigit():
                    limit = max(1, min(int(token), 50))
                elif str(token).strip():
                    status = str(token).strip().lower()
            cmd = ["objective-list", "--limit", str(limit)]
            if status and status not in {"all", "*"}:
                cmd.extend(["--status", status])
            listed = _run_cognitive_objective_command(cmd)
            if not bool(listed.get("ok")):
                _chat_push(ctx, "system", f"Objective board failed: {str(listed.get('error') or 'list_failed')}")
                return _ok()
            list_items = [x for x in (listed.get("items") if isinstance(listed.get("items"), list) else []) if isinstance(x, dict)]
            approvals_payload = _run_cognitive_objective_command(["approvals", "--limit", str(max(50, limit * 8))])
            approvals_items = approvals_payload.get("items") if isinstance(approvals_payload.get("items"), list) else []
            approvals_by_objective = {}
            for item in approvals_items:
                if not isinstance(item, dict):
                    continue
                objective_id = str(item.get("objective_id") or "").strip()
                if not objective_id:
                    continue
                approvals_by_objective[objective_id] = int(approvals_by_objective.get(objective_id, 0)) + 1

            lessons_payload = _run_cognitive_objective_command(["objective-lessons", "--limit", str(max(100, limit * 12))])
            lessons_items = lessons_payload.get("items") if isinstance(lessons_payload.get("items"), list) else []
            lessons_by_objective: Dict[str, List[Dict[str, Any]]] = {}
            for item in lessons_items:
                if not isinstance(item, dict):
                    continue
                objective_id = str(item.get("objective_id") or "").strip()
                if not objective_id:
                    continue
                bucket = lessons_by_objective.get(objective_id)
                if bucket is None:
                    bucket = []
                    lessons_by_objective[objective_id] = bucket
                bucket.append(item)

            lines = [
                f"Objective health board ({status or 'all'}, limit={limit})",
            ]
            if not list_items:
                lines.append("No objectives found.")
                _chat_push(ctx, "system", "\n".join(lines))
                return _ok()
            for entry in list_items:
                objective_id = str(entry.get("id") or "").strip()
                title = preview_text(entry.get("title") or objective_id, 28)
                obj_status = str(entry.get("status") or "unknown")
                priority = str(entry.get("priority") or "-")
                metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
                policy = metadata.get("policy") if isinstance(metadata.get("policy"), dict) else {}
                fail_streak = int(policy.get("verification_fail_streak") or 0)
                escalation_state = str(policy.get("escalation_state") or "-")
                escalation_resolution = str(policy.get("escalation_resolution") or "-")
                auto_approvals = int(policy.get("auto_approval_count") or 0)
                remediation_state = str(policy.get("remediation_state") or "-")
                pending = int(approvals_by_objective.get(objective_id, 0))
                reflections = [row for row in lessons_by_objective.get(objective_id, []) if isinstance(row, dict)]
                reflection_count = len(reflections)
                pass_count = sum(1 for row in reflections if bool(row.get("passed")))
                pass_rate = ((pass_count * 100.0) / reflection_count) if reflection_count else None
                confidence_values: List[float] = []
                for row in reflections:
                    try:
                        confidence_values.append(float(row.get("confidence") or 0.0))
                    except Exception:
                        continue
                avg_conf = (sum(confidence_values) / len(confidence_values)) if confidence_values else None
                trend = "flat"
                if len(confidence_values) >= 2:
                    delta = confidence_values[0] - confidence_values[-1]
                    if delta > 0.08:
                        trend = "up"
                    elif delta < -0.08:
                        trend = "down"
                pass_text = f"{pass_rate:.1f}%" if pass_rate is not None else "-"
                conf_text = f"{avg_conf:.2f}" if avg_conf is not None else "-"
                lines.append(
                    (
                        f"- {title} [{objective_id[:8]}] status={obj_status} p={priority} "
                        f"fails={fail_streak} esc={escalation_state}/{escalation_resolution} "
                        f"rem={remediation_state} auto={auto_approvals} approvals={pending} "
                        f"refl={reflection_count} pass={pass_text} conf={conf_text} trend={trend}"
                    ).rstrip()
                )
            _chat_push(ctx, "system", "\n".join(lines))
            return _ok()

        if action in {"get", "show"}:
            if len(tokens) < 2:
                _chat_push(ctx, "system", _usage())
                return _ok()
            objective_id = str(tokens[1]).strip()
            out = _run_cognitive_objective_command(["objective-get", "--objective-id", objective_id])
            if not bool(out.get("ok")):
                _chat_push(ctx, "system", f"Objective get failed: {str(out.get('error') or 'unknown')}")
                return _ok()
            _chat_push(ctx, "system", _format_objective_detail(out))
            return _ok()

        if action == "tail":
            if len(tokens) < 2:
                _chat_push(ctx, "system", _usage())
                return _ok()
            objective_id = str(tokens[1]).strip()
            limit = 10
            if len(tokens) >= 3 and str(tokens[2]).isdigit():
                limit = max(1, min(int(tokens[2]), 50))
            out = _run_cognitive_objective_command(
                ["objective-tail", "--objective-id", objective_id, "--limit", str(limit)]
            )
            _chat_push(ctx, "system", _format_objective_tail(out))
            return _ok()

        if action == "lessons":
            objective_ref = str(tokens[1]).strip() if len(tokens) >= 2 else ""
            limit = 10
            if len(tokens) >= 3 and str(tokens[2]).isdigit():
                limit = max(1, min(int(tokens[2]), 50))
            listed = _run_cognitive_objective_command(["objective-list", "--limit", "100"])
            if not bool(listed.get("ok")):
                _chat_push(ctx, "system", f"Objective lessons failed: {str(listed.get('error') or 'list_failed')}")
                return _ok()
            list_items = [x for x in (listed.get("items") if isinstance(listed.get("items"), list) else []) if isinstance(x, dict)]
            selected = _objective_match_entry(list_items, objective_ref) if objective_ref else None
            objective_id = str(selected.get("id") or "").strip() if isinstance(selected, dict) else ""
            objective_title = (
                str(selected.get("title") or objective_id).strip() if isinstance(selected, dict) else "all objectives"
            )
            cmd = ["objective-lessons", "--limit", str(limit)]
            if objective_id:
                cmd.extend(["--objective-id", objective_id])
            out = _run_cognitive_objective_command(cmd)
            if not bool(out.get("ok")):
                _chat_push(ctx, "system", f"Objective lessons failed: {str(out.get('error') or 'unknown')}")
                return _ok()
            items = out.get("items") if isinstance(out.get("items"), list) else []
            if not items:
                _chat_push(ctx, "system", f"Objective lessons: no reflections found for {preview_text(objective_title, 42)}.")
                return _ok()
            lines = [f"Objective lessons ({preview_text(objective_title, 42)}, limit={limit})"]
            for row in items[:limit]:
                if not isinstance(row, dict):
                    continue
                event_id = str(row.get("event_id") or "")[:8]
                passed = bool(row.get("passed"))
                status = str(row.get("status") or "")
                confidence = str(row.get("confidence") or "0")
                lesson = preview_text(row.get("lesson") or "", 110)
                objective_short = str(row.get("objective_id") or "")[:8]
                lines.append(
                    f"- {event_id} objective={objective_short} passed={passed} status={status or '-'} confidence={confidence} {lesson}".rstrip()
                )
            _chat_push(ctx, "system", "\n".join(lines))
            return _ok()

        if action == "doctor":
            objective_ref = ""
            limit = 5
            idx = 1
            while idx < len(tokens):
                token = str(tokens[idx]).strip()
                if token in {"--limit", "-n"}:
                    if (idx + 1) >= len(tokens):
                        _chat_push(ctx, "system", "Missing value for --limit.")
                        return _ok()
                    try:
                        limit = max(1, min(int(str(tokens[idx + 1]).strip()), 50))
                    except Exception:
                        _chat_push(ctx, "system", "Invalid --limit value.")
                        return _ok()
                    idx += 2
                    continue
                if not objective_ref:
                    objective_ref = token
                else:
                    objective_ref = f"{objective_ref} {token}".strip()
                idx += 1

            listed = _run_cognitive_objective_command(["objective-list", "--limit", "50"])
            if not bool(listed.get("ok")):
                _chat_push(ctx, "system", f"Objective doctor failed: {str(listed.get('error') or 'list_failed')}")
                return _ok()
            list_items = [x for x in (listed.get("items") if isinstance(listed.get("items"), list) else []) if isinstance(x, dict)]
            selected = _objective_match_entry(list_items, objective_ref)
            if not selected:
                _chat_push(ctx, "system", "Objective doctor: no matching objective.")
                return _ok()

            objective_id = str(selected.get("id") or "").strip()
            objective_title = str(selected.get("title") or objective_id).strip() or objective_id

            objective_payload = _run_cognitive_objective_command(["objective-get", "--objective-id", objective_id])
            tail_payload = _run_cognitive_objective_command(["objective-tail", "--objective-id", objective_id, "--limit", str(limit)])
            approvals_payload = _run_cognitive_objective_command(["approvals", "--objective-id", objective_id, "--limit", str(limit)])

            objective = objective_payload.get("objective") if isinstance(objective_payload.get("objective"), dict) else {}
            tail_events = tail_payload.get("events") if isinstance(tail_payload.get("events"), list) else []
            tail_reflections = tail_payload.get("reflections") if isinstance(tail_payload.get("reflections"), list) else []
            approvals = approvals_payload.get("items") if isinstance(approvals_payload.get("items"), list) else []

            latest_event = tail_events[0] if tail_events and isinstance(tail_events[0], dict) else {}
            latest_ref = tail_reflections[0] if tail_reflections and isinstance(tail_reflections[0], dict) else {}

            latest_event_id = str(latest_event.get("id") or "")[:8]
            latest_event_status = str(latest_event.get("status") or "unknown")
            latest_event_summary = ""
            latest_digest = latest_event.get("digest") if isinstance(latest_event.get("digest"), dict) else {}
            latest_flags = latest_digest.get("risk_flags") if isinstance(latest_digest.get("risk_flags"), list) else []
            latest_flags_text = ",".join(
                [str(x).strip().lower() for x in latest_flags if str(x).strip()]
            ) or "-"
            if latest_digest:
                latest_event_summary = str(latest_digest.get("one_line_summary") or "").strip()
            if not latest_event_summary:
                latest_result = latest_event.get("result") if isinstance(latest_event.get("result"), dict) else {}
                latest_tick = latest_result.get("tick") if isinstance(latest_result.get("tick"), dict) else {}
                latest_tick_result = latest_tick.get("result") if isinstance(latest_tick.get("result"), dict) else {}
                latest_event_summary = str(latest_tick_result.get("one_line_summary") or "").strip()
            latest_ref_lesson = preview_text(latest_ref.get("lesson") or "", 100)
            objective_meta = objective.get("metadata") if isinstance(objective.get("metadata"), dict) else {}
            policy = objective_meta.get("policy") if isinstance(objective_meta.get("policy"), dict) else {}
            policy_fail_streak = str(policy.get("verification_fail_streak") or 0)
            policy_escalation_state = str(policy.get("escalation_state") or "-")
            policy_escalation_resolution = str(policy.get("escalation_resolution") or "-")
            policy_auto_approvals = str(policy.get("auto_approval_count") or 0)
            policy_hold_reason = str(policy.get("approval_hold_reason") or "-")
            policy_hold_until = str(policy.get("approval_hold_until") or "-")
            policy_remediation_state = str(policy.get("remediation_state") or "-")
            policy_remediation_reason = str(policy.get("remediation_reason") or "-")

            lines = [
                f"Objective doctor: {preview_text(objective_title, 48)} [{objective_id[:8]}]",
                (
                    f"status={str(objective.get('status') or selected.get('status') or 'unknown')} "
                    f"priority={str(objective.get('priority') or selected.get('priority') or '70')}"
                ),
                (
                    f"next_run_at={str(objective.get('next_run_at') or '')} "
                    f"last_run_at={str(objective.get('last_run_at') or '')}"
                ),
                f"counts: events={len(tail_events)} reflections={len(tail_reflections)} approvals={len(approvals)}",
                (
                    f"policy: fail_streak={policy_fail_streak} "
                    f"escalation={policy_escalation_state}/{policy_escalation_resolution} "
                    f"remediation={policy_remediation_state}/{policy_remediation_reason} "
                    f"auto_approvals={policy_auto_approvals}"
                ),
                f"policy_hold: reason={policy_hold_reason} until={policy_hold_until}",
            ]
            if latest_event:
                lines.append(
                    f"latest_event: {latest_event_id} status={latest_event_status} {preview_text(latest_event_summary, 110)}".rstrip()
                )
                lines.append(f"latest_event_flags: {latest_flags_text}")
                replay_line = _format_skill_replay_text(_extract_skill_replay_for_event(latest_event))
                if replay_line:
                    lines.append(replay_line)
            if latest_ref:
                lines.append(
                    f"latest_reflection: passed={bool(latest_ref.get('passed'))} confidence={latest_ref.get('confidence')} {latest_ref_lesson}".rstrip()
                )
            _chat_push(ctx, "system", "\n".join(lines))
            return _ok()

        if action == "run-now":
            objective_ref = " ".join(tokens[1:]).strip()
            listed = _run_cognitive_objective_command(["objective-list", "--limit", "100"])
            if not bool(listed.get("ok")):
                _chat_push(ctx, "system", f"Run-now failed: {str(listed.get('error') or 'list_failed')}")
                return _ok()
            list_items = [x for x in (listed.get("items") if isinstance(listed.get("items"), list) else []) if isinstance(x, dict)]
            selected = _objective_match_entry(list_items, objective_ref)
            if not selected:
                _chat_push(ctx, "system", "Run-now failed: no matching objective.")
                return _ok()
            objective_id = str(selected.get("id") or "").strip()
            title = str(selected.get("title") or objective_id).strip() or objective_id
            dispatch = _run_cognitive_objective_command(["objective-dispatch", "--objective-id", objective_id])
            if not bool(dispatch.get("ok")):
                _chat_push(
                    ctx,
                    "system",
                    f"Run-now failed for {preview_text(title, 36)} [{objective_id[:8]}]: {str(dispatch.get('error') or 'dispatch_failed')}",
                )
                return _ok()
            event_id = str(dispatch.get("event_id") or "").strip()
            event_text = str(dispatch.get("event_text") or "").strip()
            _chat_push(
                ctx,
                "system",
                (
                    f"Run-now dispatched {preview_text(title, 36)} [{objective_id[:8]}] "
                    f"event={event_id[:8]} {preview_text(event_text, 70)}"
                ).rstrip(),
            )
            return _ok(reset_frame=True)

        if action == "resume-latest":
            objective_ref = " ".join(tokens[1:]).strip()
            listed = _run_cognitive_objective_command(["objective-list", "--limit", "100"])
            if not bool(listed.get("ok")):
                _chat_push(ctx, "system", f"Resume-latest failed: {str(listed.get('error') or 'list_failed')}")
                return _ok()
            list_items = [x for x in (listed.get("items") if isinstance(listed.get("items"), list) else []) if isinstance(x, dict)]
            paused_items = [x for x in list_items if str(x.get("status") or "").strip().lower() == "paused"]
            paused_items.sort(
                key=lambda x: (
                    float(x.get("updated_at") or 0.0)
                    if str(x.get("updated_at") or "").strip()
                    else 0.0
                ),
                reverse=True,
            )
            selected = _objective_match_entry(paused_items, objective_ref)
            if not selected:
                _chat_push(ctx, "system", "No paused objective matched.")
                return _ok()
            objective_id = str(selected.get("id") or "").strip()
            title = str(selected.get("title") or objective_id).strip() or objective_id
            resumed = _run_cognitive_objective_command(["objective-resume", "--objective-id", objective_id])
            if not bool(resumed.get("ok")):
                _chat_push(
                    ctx,
                    "system",
                    f"Resume-latest failed for {preview_text(title, 36)} [{objective_id[:8]}]: {str(resumed.get('error') or 'resume_failed')}",
                )
                return _ok()
            _chat_push(ctx, "system", f"Resumed objective {preview_text(title, 36)} [{objective_id[:8]}].")
            return _ok(reset_frame=True)

        if action in {"pause", "resume", "complete"}:
            if len(tokens) < 2:
                _chat_push(ctx, "system", _usage())
                return _ok()
            objective_id = str(tokens[1]).strip()
            out = _run_cognitive_objective_command([f"objective-{action}", "--objective-id", objective_id])
            if not bool(out.get("ok")):
                _chat_push(ctx, "system", f"Objective {action} failed: {str(out.get('error') or 'unknown')}")
                return _ok()
            objective = out.get("objective") if isinstance(out.get("objective"), dict) else {}
            status = str(objective.get("status") or "")
            title = str(objective.get("title") or objective_id)
            _chat_push(ctx, "system", f"Objective {preview_text(title, 40)} is now {status or 'updated'}.")
            return _ok(reset_frame=True)

        if action == "tick":
            max_dispatch = 3
            if len(tokens) >= 2 and str(tokens[1]).isdigit():
                max_dispatch = max(1, min(int(tokens[1]), 20))
            out = _run_cognitive_objective_command(["objective-tick", "--max-dispatch", str(max_dispatch)])
            if not bool(out.get("ok")):
                _chat_push(ctx, "system", f"Objective tick failed: {str(out.get('error') or 'unknown')}")
                return _ok()
            dispatched = out.get("dispatched_event_ids") if isinstance(out.get("dispatched_event_ids"), list) else []
            completed = out.get("completed_objective_ids") if isinstance(out.get("completed_objective_ids"), list) else []
            _chat_push(
                ctx,
                "system",
                (
                    f"Objective tick: dispatched={len(dispatched)} "
                    f"completed={len(completed)} "
                    f"skipped_deadline={int(out.get('skipped_deadline') or 0)} "
                    f"skipped_approval_hold={int(out.get('skipped_approval_hold') or 0)}"
                ),
            )
            return _ok(reset_frame=True)

        if action == "approvals":
            objective_ref = str(tokens[1]).strip() if len(tokens) >= 2 else ""
            pending_events = _pending_cognitive_approvals_for_chat(ctx)
            if objective_ref:
                pending_events = [item for item in pending_events if _objective_matches_ref(item, objective_ref)]
            if not pending_events:
                _chat_push(ctx, "system", "No pending objective approvals.")
                return _ok()
            lines = ["Pending objective approvals:"]
            for item in pending_events[:10]:
                event_id = str(item.get("event_id") or "").strip()
                risk = str(item.get("risk_level") or "unknown").strip() or "unknown"
                objective = str(item.get("objective_title") or item.get("objective_id") or "").strip()
                summary = preview_text(item.get("summary") or item.get("event_text") or "", 92)
                lines.append(f"- {event_id[:8]} risk={risk} objective={preview_text(objective, 30)} {summary}".rstrip())
            _chat_push(ctx, "system", "\n".join(lines))
            return _ok()

        if action == "approve-latest":
            objective_ref = ""
            note = ""
            idx = 1
            while idx < len(tokens):
                token = str(tokens[idx]).strip()
                if token == "--note":
                    if (idx + 1) >= len(tokens):
                        _chat_push(ctx, "system", "Missing value for --note.")
                        return _ok()
                    note = str(tokens[idx + 1]).strip()
                    idx += 2
                    continue
                if not objective_ref:
                    objective_ref = token
                else:
                    objective_ref = f"{objective_ref} {token}".strip()
                idx += 1

            pending_events = _pending_cognitive_approvals_for_chat(ctx)
            if objective_ref:
                pending_events = [evt for evt in pending_events if _objective_matches_ref(evt, objective_ref)]
            if not pending_events:
                _chat_push(ctx, "system", "No pending objective approvals.")
                return _ok()
            selected = sorted(pending_events, key=_approval_sort_ts, reverse=True)[0]
            event_id = str(selected.get("event_id") or "").strip()
            if not event_id:
                _chat_push(ctx, "system", "Pending approval is missing event_id.")
                return _ok()
            objective_label = str(selected.get("objective_title") or selected.get("objective_id") or "").strip()
            try:
                result = ctx.client.resolve_cognitive_approval(
                    event_id=event_id,
                    decision="approve",
                    note=(note or None),
                )
                status = str(result.get("status") or "pending")
                _chat_push(
                    ctx,
                    "system",
                    f"Approved latest objective event {event_id[:8]} ({status}) {preview_text(objective_label, 40)}".rstrip(),
                )
                return _ok(reset_frame=True)
            except Exception as exc:
                _chat_push(ctx, "system", f"Failed to approve latest objective event {event_id[:8]}: {exc}")
                return _ok()

        if action in {"approve", "reject"}:
            if len(tokens) < 2:
                _chat_push(ctx, "system", _usage())
                return _ok()
            objective_ref = str(tokens[1]).strip()
            note = str(" ".join(tokens[2:]) or "").strip()
            pending_events = _pending_cognitive_approvals_for_chat(ctx)
            matches = [item for item in pending_events if _objective_matches_ref(item, objective_ref)]
            if not matches:
                _chat_push(ctx, "system", "No pending approvals for that objective.")
                return _ok()
            if len(matches) > 1:
                lines = ["Multiple approvals match that objective. Use a more specific objective id."]
                for item in matches[:5]:
                    event_id = str(item.get("event_id") or "").strip()
                    objective = str(item.get("objective_title") or item.get("objective_id") or "").strip()
                    lines.append(f"- event {event_id[:8]} objective={preview_text(objective, 30)}")
                _chat_push(ctx, "system", "\n".join(lines))
                return _ok()
            selected = matches[0]
            event_id = str(selected.get("event_id") or "").strip()
            if not event_id:
                _chat_push(ctx, "system", "Pending event is missing event_id.")
                return _ok()
            decision = "approve" if action == "approve" else "reject"
            final_note = note or ("Rejected from objective command." if decision == "reject" else None)
            try:
                result = ctx.client.resolve_cognitive_approval(
                    event_id=event_id,
                    decision=decision,
                    note=final_note,
                )
                status = str(result.get("status") or "pending")
                verb = "Approved" if decision == "approve" else "Rejected"
                _chat_push(ctx, "system", f"{verb} objective event {event_id[:8]} ({status}).")
                return _ok(reset_frame=True)
            except Exception as exc:
                _chat_push(ctx, "system", f"Failed to resolve objective event {event_id[:8]}: {exc}")
                return _ok()

        _chat_push(ctx, "system", _usage())
        return _ok()

    def _cmd_prefix_required(item: ChatCommandInput) -> ChatCommandResult:
        if item.current_scope not in state.prefix_notice_seen:
            _chat_push(ctx, "system", "Run prefix mode is enabled. Use /run <goal>.")
            state.prefix_notice_seen.add(item.current_scope)
        return _ok()

    registry.register("help", _cmd_help)
    registry.register("engine", _cmd_engine)
    registry.register("exit", _cmd_exit)
    registry.register("refresh", _cmd_refresh)
    registry.register("route", _cmd_route)
    registry.register("session", _cmd_session)
    registry.register("status", _cmd_status)
    registry.register("agent", _cmd_agent)
    registry.register("model", _cmd_model)
    registry.register("think", _cmd_think)
    registry.register("verbose", _cmd_verbose)
    registry.register("reasoning", _cmd_reasoning)
    registry.register("usage", _cmd_usage)
    registry.register("elevated", _cmd_elevated)
    registry.register("activation", _cmd_activation)
    registry.register("settings", _cmd_settings)
    registry.register("mode", _cmd_mode)
    registry.register("trust", _cmd_trust)
    registry.register("target", _cmd_target)
    registry.register("events", _cmd_events)
    registry.register("dashboard", _cmd_dashboard)
    registry.register("doctor", _cmd_doctor)
    registry.register("approvals", _cmd_approvals)
    registry.register("approval-menu", _cmd_approval_menu)
    registry.register("approve-event", _cmd_approve_event)
    registry.register("reject-event", _cmd_reject_event)
    registry.register("approve", _cmd_approve)
    registry.register("reject", _cmd_reject)
    registry.register("ok", _cmd_ok)
    registry.register("hold", _cmd_hold)
    registry.register("abort", _cmd_abort)
    registry.register("new", _cmd_new)
    registry.register("reset", _cmd_new)
    registry.register("run", _cmd_run)
    registry.register("preflight", _cmd_preflight)
    registry.register("objective", _cmd_objective)
    registry.register("prefix_required", _cmd_prefix_required)
    return registry


def _reset_chat_stream_state(state: _ChatShellState) -> None:
    state.stream_cursor_id = ""
    state.stream_cursor_ts = ""
    state.stream_recent_cache = []


def _event_identity(item: Dict[str, Any]) -> str:
    event_id = str(item.get("id") or "").strip()
    if event_id:
        return event_id
    return "|".join(
        [
            str(item.get("ts") or "").strip(),
            str(item.get("channel") or "").strip().lower(),
            str(item.get("direction") or "").strip().lower(),
            str(item.get("event_type") or "").strip().lower(),
            str(item.get("run_id") or "").strip(),
            str(item.get("text") or "").strip(),
        ]
    )


def _update_chat_stream_cache(state: _ChatShellState, delta_events: List[Dict[str, Any]], limit: int = 120) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for item in list(delta_events) + list(state.stream_recent_cache):
        if not isinstance(item, dict):
            continue
        marker = _event_identity(item)
        if not marker or marker in seen:
            continue
        seen.add(marker)
        merged.append(item)
    merged.sort(key=lambda item: str(item.get("ts") or ""), reverse=True)
    state.stream_recent_cache = merged[: max(1, min(int(limit), 500))]
    if state.stream_recent_cache:
        head = state.stream_recent_cache[0]
        state.stream_cursor_id = str(head.get("id") or state.stream_cursor_id).strip()
        state.stream_cursor_ts = str(head.get("ts") or state.stream_cursor_ts).strip()
    return list(state.stream_recent_cache)


def _poll_chat_recent_events(
    ctx: TUIContext,
    state: _ChatShellState,
    inbox_events_fn: Callable[..., List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    items = inbox_events_fn(
        api_url=ctx.api_url,
        api_key=ctx.api_key,
        workspace_id=ctx.workspace_id,
        limit=120,
        session_key=state.session_filter,
        channel=state.channel_filter,
    )
    state.stream_recent_cache = [item for item in items if isinstance(item, dict)]
    if state.stream_recent_cache:
        head = state.stream_recent_cache[0]
        state.stream_cursor_id = str(head.get("id") or "").strip()
        state.stream_cursor_ts = str(head.get("ts") or "").strip()
    else:
        state.stream_cursor_id = ""
        state.stream_cursor_ts = ""
    return list(state.stream_recent_cache)


def _fetch_chat_recent_events(
    ctx: TUIContext,
    state: _ChatShellState,
    inbox_events_fn: Callable[..., List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    if not _chat_inbox_stream_enabled():
        return _poll_chat_recent_events(ctx, state, inbox_events_fn)

    if not state.stream_recent_cache:
        return _poll_chat_recent_events(ctx, state, inbox_events_fn)

    try:
        delta = ctx.client.stream_inbox_events(
            workspace_id=ctx.workspace_id,
            limit=120,
            channel=state.channel_filter,
            session_key=state.session_filter,
            since_id=(state.stream_cursor_id or None),
            since_ts=(state.stream_cursor_ts or None),
            include_backlog=False,
            timeout_seconds=_chat_inbox_stream_timeout_seconds(),
            poll_seconds=_chat_inbox_stream_poll_seconds(),
            heartbeat_seconds=_chat_inbox_stream_heartbeat_seconds(),
            max_events=120,
        )
        if delta:
            return _update_chat_stream_cache(state, delta, limit=120)
        return list(state.stream_recent_cache)
    except Exception:
        # Stream is an optimization path; always degrade gracefully to polling.
        return _poll_chat_recent_events(ctx, state, inbox_events_fn)


def handle_chat(
    ctx: TUIContext,
    *,
    handle_mode_fn: Callable[[TUIContext], Optional[str]],
    handle_trust_fn: Callable[[TUIContext], Optional[str]],
    handle_target_fn: Callable[[TUIContext], Optional[str]],
    print_doctor_summary_fn: Callable[[Any, str, str], None],
    inbox_events_fn: Callable[..., List[Dict[str, Any]]] = _default_inbox_events,
    run_chat_goal_fn: Callable[..., Tuple[bool, str, str]] = run_chat_goal,
    send_telegram_message_fn: Callable[..., Tuple[bool, str]] = send_chat_telegram_message,
) -> Optional[str]:
    state = _ChatShellState()
    reset_chat_frame_renderer()
    state.input_route = _default_chat_input_route()
    _restore_chat_shell_state(ctx, state)
    current_scope = "all"
    last_persisted_signature = _chat_shell_state_signature(ctx, state)

    def _persist_if_changed(*, force: bool = False) -> None:
        nonlocal last_persisted_signature
        current = _chat_shell_state_signature(ctx, state)
        if force or current != last_persisted_signature:
            _persist_chat_shell_state(ctx, state)
            last_persisted_signature = current

    command_registry = _build_chat_command_registry(
        ctx=ctx,
        state=state,
        handle_mode_fn=handle_mode_fn,
        handle_trust_fn=handle_trust_fn,
        handle_target_fn=handle_target_fn,
        print_doctor_summary_fn=print_doctor_summary_fn,
        inbox_events_fn=inbox_events_fn,
    )

    recent_events: List[Dict[str, Any]] = []
    session_label = "all"
    current_scope = "all"
    requires_run_prefix = False
    route_label = "local_run"
    commands_label = commands_hint()
    send_hint = resolve_send_hint(route_label)
    status_parts: List[str] = [f"session {session_label}"]
    channel_health = ""

    def _refresh_chat_surface(*, push_notices: bool = True, fetch_events: bool = True) -> None:
        nonlocal recent_events
        nonlocal session_label
        nonlocal current_scope
        nonlocal requires_run_prefix
        nonlocal route_label
        nonlocal commands_label
        nonlocal send_hint
        nonlocal status_parts
        nonlocal channel_health

        scope_signature = f"{str(state.session_filter or '').strip()}::{str(state.channel_filter or '').strip().lower()}"
        if scope_signature != state.stream_scope_signature:
            _reset_chat_stream_state(state)
            state.stream_scope_signature = scope_signature
        if fetch_events:
            try:
                recent_events = _fetch_chat_recent_events(ctx, state, inbox_events_fn)
                if state.last_fetch_error:
                    _chat_push(ctx, "system", "Connection restored.")
                    state.last_fetch_error = ""
            except Exception as exc:
                recent_events = []
                detail = f"Could not load chat transcript: {exc}"
                if detail != state.last_fetch_error and _chat_show_fetch_errors():
                    _chat_push(ctx, "system", detail)
                    state.last_fetch_error = detail

        if (
            _chat_auto_focus_telegram()
            and not state.manual_session_override
            and state.session_filter is None
            and state.channel_filter is None
        ):
            has_telegram = any(str(item.get("channel") or "").strip().lower() == "telegram" for item in recent_events)
            if has_telegram:
                state.channel_filter = "telegram"
                state.event_assembler.reset()
        _persist_if_changed()

        if push_notices:
            pending_for_notice = _pending_approvals_for_chat(ctx)
            pending_map: Dict[str, str] = {}
            pending_run_ids: Set[str] = set()
            for item in pending_for_notice:
                run_id = str(item.get("run_id") or "").strip()
                approval_id = str(item.get("approval_id") or "").strip()
                if not run_id or not approval_id:
                    continue
                pending_run_ids.add(run_id)
                pending_map[run_id] = approval_id
            state.approval_notice_by_run = pending_map
            state.pending_runtime_approvals = len(pending_map)
            state.pending_runtime_items = [item for item in pending_for_notice if isinstance(item, dict)]

            pending_events_for_notice = _pending_cognitive_approvals_for_chat(ctx)
            event_notice_map: Dict[str, str] = {}
            objective_only_count = 0
            for item in pending_events_for_notice:
                event_id = str(item.get("event_id") or "").strip()
                if not event_id:
                    continue
                marker = str(item.get("updated_at") or "")
                event_notice_map[event_id] = marker
                digest = item.get("digest") if isinstance(item.get("digest"), dict) else {}
                run_id = str(item.get("run_id") or digest.get("run_id") or "").strip()
                if run_id and run_id in pending_run_ids:
                    # Runtime approval notice for this run is already displayed.
                    continue
                objective_only_count += 1
            state.approval_notice_by_event = event_notice_map
            state.pending_objective_approvals = objective_only_count
            state.pending_objective_items = [item for item in pending_events_for_notice if isinstance(item, dict)]

        session_label = resolve_session_label(
            session_filter=state.session_filter,
            channel_filter=state.channel_filter,
        )
        current_scope = resolve_current_scope(session_label, str(ctx.trust.key or ""))
        requires_run_prefix = _chat_requires_run_prefix(ctx)
        route_label = _resolve_one_app_route(state) if _one_app_mode_enabled() else _input_route_label(state.input_route)
        if state.input_route != route_label:
            state.input_route = route_label
        session_summaries = _session_summaries_for_chat(ctx, state, limit=8)
        channel_health = _format_channel_health_rollup(recent_events, window_seconds=300) if _chat_show_channel_health() else ""
        thread_rail = _session_rail_line(
            sessions=session_summaries,
            session_filter=state.session_filter,
            channel_filter=state.channel_filter,
        )
        if thread_rail:
            channel_health = f"{channel_health}\n{muted(thread_rail)}" if channel_health else muted(thread_rail)
        rows = _collect_chat_rows(items=recent_events, local_entries=ctx.chat_local_entries, limit=42)
        approval_rows = _inline_approval_rows(state, limit=4)
        if approval_rows:
            rows.extend(approval_rows)
            rows.sort(key=lambda row: str(row.get("ts") or ""))
            if len(rows) > 42:
                rows = rows[-42:]
        state.event_assembler.changed(rows)

        commands_label = commands_hint()
        send_hint = resolve_send_hint(route_label)
        status_parts = [f"session {session_label}"]
        if _chat_show_profile_line():
            agent_id = "main" if str(ctx.pack.key or "").strip().lower() in {"", "general"} else str(ctx.pack.key or "main")
            status_parts = [f"agent {agent_id}", f"session {session_label}", f"engine {str(ctx.engine or 'orion')}"]
            if not _chat_compact_header_enabled():
                status_parts.append(f"input {route_label}")
            model_label = _model_route_label(ctx)
            if model_label != "runtime default":
                status_parts.append(f"model {model_label}")
            if not _chat_compact_header_enabled():
                thinking_level = str(ctx.thinking_level or "off").strip().lower()
                if thinking_level and thinking_level != "off":
                    status_parts.append(f"think {thinking_level}")
                status_parts.extend([f"mode {ctx.pack.label}", f"trust {ctx.trust.label}", f"target {ctx.target.label}"])

        frame = render_chat_frame(
            title=strong(f"orion tui - {ctx.api_url}"),
            status_parts=status_parts,
            channel_health=channel_health,
            rows=rows,
            row_formatter=_format_chat_rows,
            muted_fn=muted,
            show_footer_hints=_chat_show_footer_hints(),
            commands_label=commands_label,
            send_hint=send_hint,
            stream_status_line=(
                f"run: {_chat_stream_status_label(state.stream_status)}"
                if state.stream_status and _chat_show_stream_status_line()
                else ""
            ),
            footer_status_line=(
                _chat_connection_footer_line(
                    channel_health,
                    state.last_fetch_error,
                    runtime_approvals=state.pending_runtime_approvals,
                    objective_approvals=state.pending_objective_approvals,
                )
                if _chat_show_connection_footer()
                else ""
            ),
            composer_hint_line=(state.composer_hint if state.composer_hint else ""),
            composer_line=(
                _chat_composer_bar(state.composer_draft)
                if _chat_inline_composer_enabled() and _chat_live_input_supported()
                else ""
            ),
        )
        if frame != state.last_frame:
            render_chat_frame_output(
                ctx.ui,
                frame,
                full_redraw_enabled=_chat_full_redraw_enabled(),
                clear_screen_enabled=_chat_clear_screen_enabled(),
            )
            state.last_frame = frame

    while True:
        _refresh_chat_surface(push_notices=True)

        # Keep chat prompt minimal and stable; command hints stay in the commands line.
        try:
            if _chat_live_input_supported():
                inline_composer = _chat_inline_composer_enabled()
                draft_handler: Optional[Callable[[str], None]] = None
                if inline_composer:
                    def _on_draft_change(value: str) -> None:
                        draft = str(value or "").replace("\n", " ").strip()
                        hint = _chat_command_suggestions(draft)
                        if draft == state.composer_draft and hint == state.composer_hint:
                            return
                        state.composer_draft = draft
                        state.composer_hint = hint
                        _refresh_chat_surface(push_notices=False, fetch_events=False)

                    draft_handler = _on_draft_change
                raw = _read_chat_input_with_idle_refresh(
                    input_prompt_label(),
                    on_idle_tick=lambda: _refresh_chat_surface(push_notices=False),
                    on_change=draft_handler,
                    show_external_prompt=not inline_composer,
                ).strip()
                state.composer_draft = ""
                state.composer_hint = ""
                if inline_composer:
                    _refresh_chat_surface(push_notices=False, fetch_events=False)
            else:
                raw = ctx.ui.input(input_prompt_label(), required=False, title=q.TITLE_LIVE_TUI).strip()
        except EOFError:
            _persist_if_changed(force=True)
            return None
        if not raw:
            continue

        parsed = parse_chat_input(raw, require_run_prefix=requires_run_prefix and route_label == "local_run")
        if parsed.kind == "empty":
            continue

        command = parse_command_alias(parsed.command)
        if parsed.kind == "command":
            dispatch = command_registry.dispatch(
                ChatCommandInput(
                    raw=raw,
                    command=command,
                    argument=parsed.argument,
                    parsed_kind=parsed.kind,
                    route_label=route_label,
                    session_label=session_label,
                    current_scope=current_scope,
                    recent_events=recent_events,
                )
            )
            if dispatch.handled:
                if dispatch.exit_chat:
                    _persist_if_changed(force=True)
                    return None
                if dispatch.reset_frame:
                    state.last_frame = ""
                _persist_if_changed()
                continue

        if parsed.kind == "command" and not is_supported_command(command):
            if route_label != "telegram_send":
                _chat_push(ctx, "system", "Unknown command. Use /help.")
                continue

        goal = parsed.goal if parsed.kind == "run" else raw
        if not goal:
            continue
        ctx.chat_flags.discard("prefix_required_notice")
        if route_label == "telegram_send":
            outbound_text = goal
            _chat_push(ctx, "you", outbound_text, channel="terminal")
            send_ok = False
            send_summary = ""
            try:
                send_ok, send_summary = send_telegram_message_fn(
                    ctx,
                    outbound_text,
                    session_filter=state.session_filter,
                    channel_filter=state.channel_filter,
                )
            except Exception as exc:
                send_summary = str(exc)
            if not send_summary:
                send_summary = "Telegram message sent." if send_ok else "Telegram send failed."
            _chat_push(ctx, "system" if send_ok else "orion", send_summary, channel="terminal")
            state.stream_status = "telegram_sent" if send_ok else "telegram_error"
            state.composer_hint = ""
            continue

        active_channel = "terminal"
        _chat_push(ctx, "you", goal, channel=active_channel)
        state.stream_status = "queued"
        _refresh_chat_surface(push_notices=False, fetch_events=False)
        run_ok = False
        run_id = ""
        summary = ""
        recent_events_snapshot = list(recent_events)
        render_throttle = {"last_ts": 0.0}

        def _render_live_frame(force: bool = False) -> None:
            now = time.monotonic()
            if not force and (now - float(render_throttle["last_ts"])) < 0.35:
                return
            live_rows = _collect_chat_rows(items=recent_events_snapshot, local_entries=ctx.chat_local_entries, limit=42)
            live_frame = render_chat_frame(
                title=strong(f"orion tui - {ctx.api_url}"),
                status_parts=status_parts,
                channel_health=channel_health,
                rows=live_rows,
                row_formatter=_format_chat_rows,
                muted_fn=muted,
                show_footer_hints=_chat_show_footer_hints(),
                commands_label=commands_label,
                send_hint=send_hint,
                stream_status_line=(
                    f"run: {_chat_stream_status_label(state.stream_status)}"
                    if state.stream_status and _chat_show_stream_status_line()
                    else ""
                ),
                footer_status_line=(
                    _chat_connection_footer_line(
                        channel_health,
                        state.last_fetch_error,
                        runtime_approvals=state.pending_runtime_approvals,
                        objective_approvals=state.pending_objective_approvals,
                    )
                    if _chat_show_connection_footer()
                    else ""
                ),
                composer_hint_line=(state.composer_hint if state.composer_hint else ""),
                composer_line=(
                    _chat_composer_bar(state.composer_draft)
                    if _chat_inline_composer_enabled() and _chat_live_input_supported()
                    else ""
                ),
            )
            if force or live_frame != state.last_frame:
                render_chat_frame_output(
                    ctx.ui,
                    live_frame,
                    full_redraw_enabled=_chat_full_redraw_enabled(),
                    clear_screen_enabled=_chat_clear_screen_enabled(),
                )
                state.last_frame = live_frame
            render_throttle["last_ts"] = now

        def _on_run_update(update_run_id: str, text: str, final: bool = False) -> None:
            cleaned = _chat_clean_summary(text)
            if not cleaned:
                return
            lowered = cleaned.lower()
            if "approval required" in lowered or "human input needed" in lowered:
                state.stream_status = "waiting_for_input"
            elif final:
                state.stream_status = "completed"
            else:
                state.stream_status = "streaming"
            _chat_upsert_orion_entry(
                ctx,
                cleaned,
                run_id=str(update_run_id or run_id or "").strip(),
                channel=active_channel,
            )
            if _chat_live_repaint_enabled():
                _render_live_frame(force=bool(final))

        try:
            state.stream_status = "running"
            if _run_chat_goal_supports_updates(run_chat_goal_fn):
                try:
                    run_ok, run_id, raw_summary = run_chat_goal_fn(ctx, goal, on_update=_on_run_update)
                except TypeError as exc:
                    if "on_update" not in str(exc):
                        raise
                    run_ok, run_id, raw_summary = run_chat_goal_fn(ctx, goal)
            else:
                run_ok, run_id, raw_summary = run_chat_goal_fn(ctx, goal)
            summary = _chat_clean_summary(raw_summary)
        except Exception as exc:
            summary = _chat_clean_summary(str(exc))
        if not summary:
            summary = "" if run_ok else "Run failed."
        if summary:
            _chat_upsert_orion_entry(ctx, summary, run_id=run_id, channel=active_channel)
        if run_ok:
            state.stream_status = "completed"
        elif "approval" in summary.lower() or "waiting_for_input" in summary.lower():
            state.stream_status = "waiting_for_input"
        else:
            state.stream_status = "failed"
        state.composer_hint = ""
        state.last_frame = ""


def handle_inbox(
    ctx: TUIContext,
    *,
    inbox_events_fn: Callable[..., List[Dict[str, Any]]] = _default_inbox_events,
) -> Optional[str]:
    channel_choice = ctx.ui.choose(
        q.Q_LIVE_TUI_INBOX_FILTER,
        [
            Choice("all", "All channels", "Telegram + WhatsApp + system events"),
            Choice("telegram", "Telegram", "Only Telegram session events"),
            Choice("whatsapp", "WhatsApp", "Only WhatsApp session events"),
        ],
        default_index=0,
        title=q.TITLE_LIVE_TUI,
    )
    channel_filter = None if channel_choice.key == "all" else channel_choice.key
    while True:
        try:
            items = inbox_events_fn(
                api_url=ctx.api_url,
                api_key=ctx.api_key,
                workspace_id=ctx.workspace_id,
                limit=80,
                channel=channel_filter,
            )
            ctx.ui.message("Unified Inbox", _format_inbox_events(items))
        except Exception as exc:
            ctx.ui.note(f"{warn('warning:')} Could not load inbox events: {exc}")
            return None
        inbox_action = ctx.ui.choose(
            q.Q_LIVE_TUI_INBOX_ACTION,
            [
                Choice("back", "Back to actions", "Return to Live TUI menu"),
                Choice("refresh", "Refresh inbox", "Reload channel event timeline"),
            ],
            default_index=0,
            title=q.TITLE_LIVE_TUI,
        )
        if inbox_action.key != "refresh":
            return None


def handle_advanced(
    ctx: TUIContext,
    *,
    handle_chat_fn: Callable[[TUIContext], Optional[str]],
    handle_doctor_fn: Callable[[TUIContext], Optional[str]],
    handle_dashboard_fn: Callable[[TUIContext], Optional[str]],
    handle_inbox_fn: Callable[[TUIContext], Optional[str]],
    handle_runs_fn: Callable[[TUIContext], Optional[str]],
    handle_metrics_fn: Callable[[TUIContext], Optional[str]],
    handle_queue_fn: Callable[[TUIContext], Optional[str]],
    handle_approval_fn: Callable[[TUIContext], Optional[str]],
    handle_connectors_fn: Callable[[TUIContext], Optional[str]],
    run_once_fn: Callable[..., int],
) -> Optional[str]:
    raw = ctx.ui.input(q.Q_LIVE_TUI_ADVANCED_INPUT, required=False, title=q.TITLE_LIVE_TUI)
    line = (raw or "").strip()
    if not line:
        return None
    lowered = line.lower()
    if lowered in {"/exit", "/quit", "exit", "quit"}:
        ctx.ui.note(muted("Leaving Empyralis Live TUI."))
        return "exit"
    if lowered in {"/help", "help"}:
        ctx.ui.note("Use menu actions for most operations. Commands: /chat /doctor /dashboard /inbox /runs /metrics /queue /approvals /approval-menu /objective /exit")
        return None

    command_aliases = {
        "/chat": handle_chat_fn,
        "/doctor": handle_doctor_fn,
        "/dashboard": handle_dashboard_fn,
        "/inbox": handle_inbox_fn,
        "/runs": handle_runs_fn,
        "/metrics": handle_metrics_fn,
        "/queue": handle_queue_fn,
        "/approvals": handle_approval_fn,
        "/approval-menu": handle_approval_fn,
        "/connectors": handle_connectors_fn,
    }
    if lowered in command_aliases:
        return command_aliases[lowered](ctx)

    run_once_fn(
        ui=ctx.ui,
        api_url=ctx.api_url,
        runtime_key=ctx.api_key,
        workspace_id=ctx.workspace_id,
        engine=ctx.engine,
        goal=line,
        pack=ctx.pack,
        trust=ctx.trust,
        target=ctx.target,
        pack_inputs=None,
        prompt_connectors=False,
    )
    return None
