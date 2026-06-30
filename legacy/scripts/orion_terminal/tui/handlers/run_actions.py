from __future__ import annotations

from typing import Callable, Optional

from ... import question_catalog as q
from ...core import Choice, DEFAULT_QUICK_GOAL, ok, warn
from ...services.live_tui import (
    TUIContext,
    fetch_run_detail as _fetch_run_detail,
    format_run_detail as _format_run_detail,
    format_runs_list as _format_runs_list,
    history_runs as _history_runs,
)
from ...text_format import preview_text


def handle_runs(
    ctx: TUIContext,
    *,
    stream_run: Callable[[object, str, str, str], tuple[bool, str]],
) -> Optional[str]:
    try:
        items = _history_runs(ctx.api_url, ctx.api_key, ctx.workspace_id, limit=20)
    except Exception as exc:
        ctx.ui.note(f"{warn('warning:')} Could not load recent runs: {exc}")
        return None
    ctx.ui.message("Recent Runs", _format_runs_list(items))
    if not items:
        return None
    if not ctx.ui.confirm(q.Q_LIVE_TUI_INSPECT_RUN, default_yes=False, title=q.TITLE_LIVE_TUI):
        return None
    run_choices: list[Choice] = []
    for item in items[:20]:
        run_id = str(item.get("run_id") or "")
        if not run_id:
            continue
        label = f"{run_id[:8]}  {str(item.get('status') or 'unknown')}"
        note = preview_text(item.get("result_summary") or item.get("user_goal") or "", 84)
        run_choices.append(Choice(run_id, label, note))
    if not run_choices:
        return None
    selected_run = ctx.ui.choose(q.Q_LIVE_TUI_SELECT_RUN, run_choices, default_index=0, title=q.TITLE_LIVE_TUI)
    try:
        detail = _fetch_run_detail(ctx.api_url, ctx.api_key, selected_run.key)
    except Exception as exc:
        ctx.ui.note(f"{warn('warning:')} Could not load run detail: {exc}")
        return None
    ctx.ui.message("Run Detail", _format_run_detail(detail))
    run_status = str(detail.get("status") or "").strip().lower()
    if run_status in {"starting", "running", "running_local", "queued_local", "waiting_for_input"}:
        if ctx.ui.confirm(q.Q_LIVE_TUI_STREAM_RUN, default_yes=False, title=q.TITLE_LIVE_TUI):
            stream_run(ctx.ui, ctx.api_url, ctx.api_key, selected_run.key)
    if ctx.ui.confirm(q.Q_LIVE_TUI_REPLAY_RUN, default_yes=False, title=q.TITLE_LIVE_TUI):
        try:
            replayed = ctx.client.replay_run(selected_run.key)
            replay_id = str(replayed.get("run_id") or "").strip()
            if replay_id:
                ctx.ui.note(f"{ok('done:')} Replay started: {replay_id}")
                stream_run(ctx.ui, ctx.api_url, ctx.api_key, replay_id)
        except Exception as exc:
            ctx.ui.note(f"{warn('warning:')} Replay failed: {exc}")
    return None


def handle_run(
    ctx: TUIContext,
    *,
    run_once_fn: Callable[..., int],
    collect_pack_inputs_fn: Callable[[object, str], dict[str, str]],
) -> Optional[str]:
    default_goal = (
        f"Execute {ctx.pack.label} end-to-end with production quality."
        if ctx.pack.key
        else DEFAULT_QUICK_GOAL
    )
    goal = ctx.ui.input(
        q.Q_LIVE_TUI_RUN_GOAL,
        default=default_goal,
        required=True,
        title=q.TITLE_LIVE_TUI,
    )
    pack_inputs = None
    if ctx.pack.key:
        if ctx.ui.confirm(q.Q_LIVE_TUI_ADD_PACK_INPUTS, default_yes=False, title=q.TITLE_LIVE_TUI):
            pack_inputs = collect_pack_inputs_fn(ctx.ui, ctx.pack.key)
    elif ctx.ui.confirm(q.Q_LIVE_TUI_ADD_CONTEXT, default_yes=False, title=q.TITLE_LIVE_TUI):
        pack_inputs = collect_pack_inputs_fn(ctx.ui, "")

    run_once_fn(
        ui=ctx.ui,
        api_url=ctx.api_url,
        runtime_key=ctx.api_key,
        workspace_id=ctx.workspace_id,
        engine=ctx.engine,
        goal=goal,
        pack=ctx.pack,
        trust=ctx.trust,
        target=ctx.target,
        pack_inputs=pack_inputs,
        prompt_connectors=False,
    )
    return None
