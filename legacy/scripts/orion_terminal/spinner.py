from __future__ import annotations

import shutil
import sys
import threading
import time
from typing import Dict, List, Optional, Tuple

from .core import accent, err, muted, ok
from .runtime_flags import (
    COMPACT_MODE,
    FULL_OUTPUT,
    SPINNER_ENABLED,
    SPINNER_INTERVAL_SECONDS,
    SPINNER_STYLE,
)


def spinner_frames(style: str) -> List[str]:
    mapping: Dict[str, List[str]] = {
        "hex": ["⬢", "⬡", "⬢", "⬡"],
        "hexflow": ["⬡", "⏣", "⬢", "⏣"],
        "arc": ["◴", "◷", "◶", "◵"],
        "dot": ["·", "••", "•••", "••", "·"],
        "line": ["-", "\\", "|", "/"],
        "pulse": ["▁", "▃", "▅", "▇", "▅", "▃"],
    }
    aliases = {"orb": "arc"}
    style_key = aliases.get(style, style)
    return mapping.get(style_key, mapping["hex"])


def spinner_final_glyph(success: bool) -> str:
    if SPINNER_STYLE in {"hex", "hexflow"}:
        return ok("⬢") if success else err("⬢")
    return ok("done") if success else err("failed")


def message_has_signin_wait(message: str) -> bool:
    text = str(message or "").lower()
    if not text:
        return False
    signin_terms = (
        "sign in",
        "signin",
        "oauth",
        "authorize",
        "authorization",
        "auth code",
        "verification code",
        "device code",
    )
    wait_terms = ("wait", "waiting", "pending", "required", "needed", "continue", "open", "visit", "browser")
    return any(term in text for term in signin_terms) and any(term in text for term in wait_terms)


def spinner_label_from_event(current: str, event_key: str, message: str) -> str:
    if event_key in {"approval_requested", "approval_waiting", "waiting_for_input"}:
        return "waiting for approval"
    if event_key in {"approval_received", "approval_resolved"}:
        return "running"
    if event_key in {"run_retry"}:
        return "retrying"
    if event_key in {"local_queued"}:
        return "queued local"
    if event_key in {"local_claimed"}:
        return "running local"
    if event_key in {"auth_waiting", "oauth_waiting", "signin_waiting"}:
        return "waiting for sign-in"
    if message_has_signin_wait(message):
        return "waiting for sign-in"
    return current


def fit_spinner_line(frame: str, label: str) -> Tuple[str, str]:
    columns = max(40, int(shutil.get_terminal_size((96, 24)).columns))
    prefix = "└─ "
    sep = " " if frame else ""
    visible_len = len(prefix) + len(frame) + len(sep) + len(label)
    max_len = max(20, columns - 1)
    if visible_len <= max_len:
        return frame, label

    remaining = max_len - len(prefix) - len(frame) - len(sep)
    if remaining < 0:
        remaining = 0
    if remaining >= len(label):
        return frame, label
    if remaining <= 1:
        return frame, ""
    return frame, (label[: remaining - 1] + "…")


class ProgressSpinner:
    def __init__(self, label: str) -> None:
        self.label = label
        self.frames = spinner_frames(SPINNER_STYLE)
        self.index = 0
        self.started = time.monotonic()
        self.enabled = bool(SPINNER_ENABLED and COMPACT_MODE and not FULL_OUTPUT and sys.stdout.isatty())
        self.interval = SPINNER_INTERVAL_SECONDS
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._paused = False

    @staticmethod
    def _is_live_label(label: str) -> bool:
        text = str(label or "").strip().lower()
        if not text:
            return False
        live_terms = ("running", "waiting", "queued", "retry", "local", "sign-in")
        return any(term in text for term in live_terms)

    def _render_once_locked(self) -> None:
        frame = self.frames[self.index % len(self.frames)]
        self.index += 1
        status = self.label
        if self._is_live_label(status):
            elapsed = max(0, int(time.monotonic() - self.started))
            mm, ss = divmod(elapsed, 60)
            status = f"{status} {mm:02d}:{ss:02d}"
        frame, status = fit_spinner_line(frame, status)
        line = f"{muted('└─')} {accent(frame)} {status}"
        sys.stdout.write(f"\r\033[2K{line}")
        sys.stdout.flush()

    def _run_loop(self) -> None:
        while not self._stop_event.wait(self.interval):
            with self._lock:
                if self._paused:
                    continue
                self._render_once_locked()

    def _start_loop_if_needed(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def tick(self, label: Optional[str] = None) -> None:
        if not self.enabled:
            return
        with self._lock:
            if label is not None:
                self.label = label
            self._paused = False
            self._start_loop_if_needed()
            self._render_once_locked()

    def clear(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._paused = True
            sys.stdout.write("\r\033[2K")
            sys.stdout.flush()

    def done(self, success: bool = True, label: Optional[str] = None) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._paused = True
            if label is not None:
                self.label = label
            final_label = self.label
            self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=max(0.1, self.interval * 2))
        state = spinner_final_glyph(success)
        _, final_status = fit_spinner_line("", final_label)
        line = f"{muted('└─')} {state} {final_status}"
        sys.stdout.write(f"\r\033[2K{line}\n")
        sys.stdout.flush()
