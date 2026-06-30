from __future__ import annotations

from .core import Choice, accent, muted

_COMPACT_MODE = False
_FULL_OUTPUT = False


def configure_transcript(compact_mode: bool, full_output: bool) -> None:
    global _COMPACT_MODE, _FULL_OUTPUT
    _COMPACT_MODE = bool(compact_mode)
    _FULL_OUTPUT = bool(full_output)


def step_banner(ui, step_no: int, total_steps: int, title: str) -> None:
    if _COMPACT_MODE and not _FULL_OUTPUT:
        return
    ui.note("")
    prefix = f"{step_no}/{total_steps} " if total_steps > 0 else ""
    ui.note(f"{accent('▣')}  {prefix}{title}")


def choice_log(ui, label: str, choice: Choice) -> None:
    detail = f" ({choice.note})" if choice.note and _FULL_OUTPUT else ""
    ui.note(f"{accent('•')}  {label}")
    ui.note(f"{muted('│')}  {choice.label}{detail}")


def flag_log(ui, label: str, enabled: bool) -> None:
    ui.note(f"{accent('•')}  {label}")
    ui.note(f"{muted('│')}  {'Yes' if enabled else 'No'}")


def value_log(ui, label: str, value: str) -> None:
    ui.note(f"{accent('•')}  {label}")
    ui.note(f"{muted('│')}  {value}")
