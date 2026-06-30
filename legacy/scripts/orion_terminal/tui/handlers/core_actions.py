from __future__ import annotations

from typing import Optional

from ... import question_catalog as q
from ...core import Choice, EXECUTION_TARGETS, GENERAL_MODE, SPECIALIST_PACKS, TRUST_MODES, warn
from ...flows_shared import print_doctor_summary
from ...services.connectors import choose_and_create_connectors
from ...services.live_tui import (
    TUIContext,
    dashboard_snapshot as _dashboard_snapshot,
    live_tui_status_summary as _live_tui_status_summary,
    local_queue_summary as _local_queue_summary,
    metrics_summary as _metrics_summary,
    resolve_pending_approval as _resolve_pending_approval,
)
from ...transcript import choice_log


def handle_doctor(ctx: TUIContext) -> Optional[str]:
    print_doctor_summary(ctx.ui, ctx.api_url, ctx.api_key)
    return None


def handle_dashboard(ctx: TUIContext) -> Optional[str]:
    while True:
        try:
            ctx.ui.message(
                "Empyralis Dashboard",
                _dashboard_snapshot(
                    api_url=ctx.api_url,
                    api_key=ctx.api_key,
                    workspace_id=ctx.workspace_id,
                    pack=ctx.pack,
                    trust=ctx.trust,
                    target=ctx.target,
                ),
            )
        except Exception as exc:
            ctx.ui.note(f"{warn('warning:')} Could not build dashboard: {exc}")
            return None
        dashboard_action = ctx.ui.choose(
            q.Q_LIVE_TUI_DASHBOARD_ACTION,
            [
                Choice("back", "Back to actions", "Return to Live TUI menu"),
                Choice("refresh", "Refresh dashboard", "Reload dashboard snapshot"),
            ],
            default_index=0,
            title=q.TITLE_LIVE_TUI,
        )
        if dashboard_action.key != "refresh":
            return "run"


def handle_status(ctx: TUIContext) -> Optional[str]:
    ctx.ui.message(
        "Empyralis Live TUI Status",
        _live_tui_status_summary(
            api_url=ctx.api_url,
            api_key=ctx.api_key,
            workspace_id=ctx.workspace_id,
            pack=ctx.pack,
            trust=ctx.trust,
            target=ctx.target,
        ),
    )
    return None


def handle_queue(ctx: TUIContext) -> Optional[str]:
    try:
        ctx.ui.message("Local Queue", _local_queue_summary(ctx.api_url, ctx.api_key, ctx.workspace_id))
    except Exception as exc:
        ctx.ui.note(f"{warn('warning:')} Could not load local queue: {exc}")
    return None


def handle_metrics(ctx: TUIContext) -> Optional[str]:
    try:
        ctx.ui.message("Runtime Metrics", _metrics_summary(ctx.api_url, ctx.api_key))
    except Exception as exc:
        ctx.ui.note(f"{warn('warning:')} Could not load runtime metrics: {exc}")
    return None


def handle_approval(ctx: TUIContext) -> Optional[str]:
    try:
        _resolve_pending_approval(ctx.ui, ctx.api_url, ctx.api_key, ctx.workspace_id)
    except Exception as exc:
        ctx.ui.note(f"{warn('warning:')} Could not resolve approval: {exc}")
    return None


def handle_connectors(ctx: TUIContext) -> Optional[str]:
    choose_and_create_connectors(ctx.ui, ctx.api_url, ctx.api_key, ctx.workspace_id)
    return None


def handle_trust(ctx: TUIContext) -> Optional[str]:
    ctx.trust = ctx.ui.choose(q.Q_LIVE_TUI_TRUST_MODE, TRUST_MODES, default_index=0, title=q.TITLE_LIVE_TUI)
    choice_log(ctx.ui, "Trust mode", ctx.trust)
    return None


def handle_target(ctx: TUIContext) -> Optional[str]:
    ctx.target = ctx.ui.choose(
        q.Q_LIVE_TUI_EXECUTION_TARGET,
        EXECUTION_TARGETS,
        default_index=0,
        title=q.TITLE_LIVE_TUI,
    )
    choice_log(ctx.ui, "Execution target", ctx.target)
    return None


def handle_mode(ctx: TUIContext) -> Optional[str]:
    pack_choice = ctx.ui.choose(
        q.Q_LIVE_TUI_ACTIVE_MODE,
        [GENERAL_MODE] + SPECIALIST_PACKS,
        default_index=0,
        title=q.TITLE_LIVE_TUI,
    )
    ctx.pack = pack_choice
    choice_log(ctx.ui, "Active mode", ctx.pack)
    return None
