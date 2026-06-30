from __future__ import annotations

from typing import Callable

from ..core import UI
from ..services.live_tui import TUIContext


def guided_run_flow(*args, **kwargs):
    from .. import flows_run as _flows_run

    return _flows_run.guided_run_flow(*args, **kwargs)


def run_once(*args, **kwargs):
    from .. import flows_run as _flows_run

    return _flows_run.run_once(*args, **kwargs)


def run_tui_shell_impl(
    ui: UI,
    api_url: str,
    api_key: str,
    workspace_id: str,
    engine: str,
    *,
    handle_chat_fn: Callable[[TUIContext], int | None],
    trust_choice,
    target_choice,
    pack_choice,
) -> int:
    ctx = TUIContext(
        ui=ui,
        api_url=api_url,
        api_key=api_key,
        workspace_id=workspace_id,
        engine=engine,
        trust=trust_choice,
        target=target_choice,
        pack=pack_choice,
    )
    handle_chat_fn(ctx)
    return 0


def run_tui_shell(ui: UI, api_url: str, api_key: str, workspace_id: str, engine: str) -> int:
    from .. import flows_run as _flows_run

    return run_tui_shell_impl(
        ui=ui,
        api_url=api_url,
        api_key=api_key,
        workspace_id=workspace_id,
        engine=engine,
        handle_chat_fn=_flows_run._handle_chat,
        trust_choice=_flows_run.TRUST_MODES[0],
        target_choice=_flows_run.EXECUTION_TARGETS[0],
        pack_choice=_flows_run.GENERAL_MODE,
    )


__all__ = ["guided_run_flow", "run_once", "run_tui_shell", "run_tui_shell_impl"]
