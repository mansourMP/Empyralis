from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "guided_run_flow",
    "run_once",
    "run_tui_shell",
    "ActionRegistry",
    "TUIContext",
    "dashboard_snapshot",
    "format_channel_health_rollup",
    "format_chat_transcript",
    "format_inbox_events",
    "format_run_detail",
    "format_runs_list",
    "live_tui_status_summary",
]


def __getattr__(name: str) -> Any:
    if name in {"guided_run_flow", "run_once", "run_tui_shell"}:
        mod = import_module(".shell", __name__)
        return getattr(mod, name)
    if name in {
        "ActionRegistry",
        "TUIContext",
        "dashboard_snapshot",
        "format_channel_health_rollup",
        "format_chat_transcript",
        "format_inbox_events",
        "format_run_detail",
        "format_runs_list",
        "live_tui_status_summary",
    }:
        mod = import_module(".live_tui", __name__)
        return getattr(mod, name)
    raise AttributeError(name)
