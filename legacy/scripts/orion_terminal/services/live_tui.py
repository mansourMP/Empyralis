from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

from .. import question_catalog as q
from ..clients.runtime import RuntimeClient
from ..core import Choice, UI, muted, ok, strong
from ..text_format import preview_text
from .live_tui_formatters import (
    collect_chat_rows,
    fmt_ms,
    format_channel_health_rollup,
    format_chat_rows,
    format_chat_transcript,
    format_inbox_events,
    format_run_detail,
    format_runs_list,
)


@dataclass
class TUIContext:
    ui: UI
    api_url: str
    api_key: str
    workspace_id: str
    engine: str
    trust: Choice
    target: Choice
    pack: Choice
    provider_override: str = ""
    model_override: str = ""
    thinking_level: str = "off"
    verbose_level: str = "off"
    reasoning_level: str = "off"
    usage_level: str = "off"
    elevated_level: str = "off"
    activation_level: str = "mention"
    chat_local_entries: List[Dict[str, Any]] = field(default_factory=list)
    chat_flags: Set[str] = field(default_factory=set)

    @property
    def client(self) -> RuntimeClient:
        return RuntimeClient(api_url=self.api_url, api_key=self.api_key)


class ActionRegistry:
    def __init__(self):
        self._handlers: Dict[str, Callable[[TUIContext], Optional[str]]] = {}

    def register(self, key: str, handler: Callable[[TUIContext], Optional[str]]):
        self._handlers[key] = handler

    def handle(self, key: str, context: TUIContext) -> Optional[str]:
        handler = self._handlers.get(key)
        if handler:
            return handler(context)
        return None


def _runtime_client(api_url: str, api_key: str) -> RuntimeClient:
    return RuntimeClient(api_url=api_url, api_key=api_key)


def history_runs(api_url: str, api_key: str, workspace_id: str, limit: int = 20, status: str | None = None) -> List[Dict[str, Any]]:
    return _runtime_client(api_url, api_key).list_history_runs(
        workspace_id=workspace_id,
        limit=limit,
        status=status,
    )


def fetch_run_detail(api_url: str, api_key: str, run_id: str) -> Dict[str, Any]:
    return _runtime_client(api_url, api_key).get_run(run_id)




def local_queue_summary(api_url: str, api_key: str, workspace_id: str) -> str:
    payload = _runtime_client(api_url, api_key).get_local_queue(workspace_id, limit=20)
    queued = payload.get("queued_runs") if isinstance(payload.get("queued_runs"), list) else []
    claimed = payload.get("claimed_runs") if isinstance(payload.get("claimed_runs"), list) else []
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    lines = [
        f"local_enabled: {'yes' if bool(payload.get('enabled')) else 'no'}",
        f"queued_count: {int(payload.get('queued_count') or len(queued))}",
        f"claimed_count: {int(payload.get('claimed_count') or len(claimed))}",
    ]
    if summary:
        lines.append(f"workers_online: {int(summary.get('online') or 0)}")
        lines.append(f"workers_busy: {int(summary.get('busy') or 0)}")
        lines.append(f"workers_idle: {int(summary.get('idle') or 0)}")
    if queued:
        lines.append("")
        lines.append("queued:")
        for item in queued[:8]:
            rid = str(item.get("run_id") or "")
            lines.append(f"- {rid[:8]}  {str(item.get('status') or 'queued')}  {str(item.get('outcome_pack') or 'general')}")
    if claimed:
        lines.append("")
        lines.append("claimed:")
        for item in claimed[:8]:
            rid = str(item.get("run_id") or "")
            worker = str(item.get("worker_id") or "n/a")
            lines.append(f"- {rid[:8]}  worker={worker}  {str(item.get('status') or 'running_local')}")
    return "\n".join(lines)


def metrics_summary(api_url: str, api_key: str) -> str:
    payload = _runtime_client(api_url, api_key).get_metrics()
    runs = payload.get("runs") if isinstance(payload.get("runs"), dict) else {}
    kpis = payload.get("kpis") if isinstance(payload.get("kpis"), dict) else {}
    local = payload.get("local_companion") if isinstance(payload.get("local_companion"), dict) else {}
    lines = [
        f"runs_started: {int(runs.get('started') or 0)}",
        f"runs_completed: {int(runs.get('completed') or 0)}",
        f"runs_failed: {int(runs.get('failed') or 0)}",
        f"runs_timeout: {int(runs.get('timeout') or 0)}",
        f"runs_active: {int(runs.get('active') or 0)}",
        f"waiting_for_input: {int(runs.get('waiting_for_input') or 0)}",
        f"completion_rate: {float(runs.get('completion_rate') or 0.0):.2%}",
        f"avg_run_duration: {fmt_ms(kpis.get('avg_run_duration_ms'))}",
        f"avg_first_value: {fmt_ms(kpis.get('avg_time_to_first_value_ms'))}",
        f"avg_human_wait: {fmt_ms(kpis.get('avg_human_wait_ms'))}",
        f"local_online_workers: {int(local.get('online_workers') or 0)}",
        f"local_pending_runs: {int(local.get('pending_runs') or 0)}",
    ]
    return "\n".join(lines)


def inbox_events(
    api_url: str,
    api_key: str,
    workspace_id: str,
    limit: int = 80,
    channel: Optional[str] = None,
    session_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    return _runtime_client(api_url, api_key).list_inbox_events(
        workspace_id=workspace_id,
        limit=limit,
        channel=channel,
        session_key=session_key,
    )


def pending_approvals(api_url: str, api_key: str, workspace_id: str) -> List[Dict[str, str]]:
    waiting = history_runs(api_url, api_key, workspace_id, limit=25, status="waiting_for_input")
    if not waiting:
        waiting = history_runs(api_url, api_key, workspace_id, limit=30)
    out: List[Dict[str, str]] = []
    seen: set[str] = set()
    for item in waiting:
        run_id = str(item.get("run_id") or "").strip()
        if not run_id or run_id in seen:
            continue
        seen.add(run_id)
        try:
            detail = fetch_run_detail(api_url, api_key, run_id)
        except Exception:
            continue
        pending = detail.get("pending_approval") if isinstance(detail.get("pending_approval"), dict) else {}
        approval_id = str(pending.get("approval_id") or "").strip()
        if not approval_id:
            continue
        reason = preview_text(pending.get("reason") or pending.get("prompt") or "", 120)
        out.append(
            {
                "run_id": run_id,
                "approval_id": approval_id,
                "reason": reason,
            }
        )
    return out


def resolve_pending_approval(ui: UI, api_url: str, api_key: str, workspace_id: str) -> None:
    runtime = _runtime_client(api_url, api_key)
    pending = pending_approvals(api_url, api_key, workspace_id)
    if not pending:
        ui.note(muted("No pending approvals found."))
        return

    options: List[Choice] = []
    for item in pending:
        run_short = item["run_id"][:8]
        label = f"run {run_short}"
        note = item.get("reason") or item["approval_id"]
        options.append(Choice(f"{item['run_id']}::{item['approval_id']}", label, note))

    selected = ui.choose(q.Q_APPROVAL_CHOOSE_REQUEST, options, default_index=0, title=q.TITLE_LIVE_TUI)
    run_id, approval_id = selected.key.split("::", 1)
    decision = ui.choose(
        q.Q_APPROVAL_DECISION,
        [
            Choice("approve", "Approve", "Proceed with requested action"),
            Choice("reject", "Reject", "Block this action"),
        ],
        default_index=0,
        title=q.TITLE_LIVE_TUI,
    )
    note = ui.input(q.Q_APPROVAL_NOTE, required=False, title=q.TITLE_LIVE_TUI)
    result = runtime.resolve_approval(
        run_id=run_id,
        approval_id=approval_id,
        decision="approve" if decision.key == "approve" else "reject",
        note=note or None,
    )
    ui.note(f"{ok('done:')} Approval resolved for run {run_id[:8]} ({result.get('status')})")


def live_tui_status_summary(
    api_url: str,
    api_key: str,
    workspace_id: str,
    pack: Choice,
    trust: Choice,
    target: Choice,
) -> str:
    runtime = _runtime_client(api_url, api_key)
    health_ok = "unknown"
    runtime_valid = "unknown"
    openai_source = "unknown"
    targets_line = ""
    workers_online = "n/a"
    workers_idle = "n/a"
    workers_busy = "n/a"

    try:
        health = runtime.get_health()
        health_ok = "yes" if bool(health.get("ok")) else "no"
        runtime_valid = "yes" if bool(health.get("runtime_valid", True)) else "no"
        openai_source = str(health.get("openai_credential_source") or "none")
        targets = health.get("supported_execution_targets")
        if isinstance(targets, list):
            targets_line = ", ".join([str(item) for item in targets if str(item).strip()])
    except Exception:
        pass

    try:
        workers = runtime.get_workers_status()
        summary = workers.get("summary") if isinstance(workers.get("summary"), dict) else {}
        workers_online = str(int(summary.get("online", 0) or 0))
        workers_idle = str(int(summary.get("idle", 0) or 0))
        workers_busy = str(int(summary.get("busy", 0) or 0))
    except Exception:
        pass

    lines = [
        f"workspace: {workspace_id}",
        f"mode: {pack.label}",
        f"trust: {trust.label}",
        f"target: {target.label}",
        f"runtime_ok: {health_ok}",
        f"runtime_valid: {runtime_valid}",
        f"openai_source: {openai_source}",
        f"workers_online: {workers_online}",
        f"workers_idle: {workers_idle}",
        f"workers_busy: {workers_busy}",
    ]
    if targets_line:
        lines.append(f"execution_targets: {targets_line}")
    return "\n".join(lines)


def dashboard_snapshot(
    api_url: str,
    api_key: str,
    workspace_id: str,
    pack: Choice,
    trust: Choice,
    target: Choice,
) -> str:
    runtime = _runtime_client(api_url, api_key)
    health: Dict[str, Any] = {}
    workers: Dict[str, Any] = {}
    metrics: Dict[str, Any] = {}
    queue: Dict[str, Any] = {}
    recent: List[Dict[str, Any]] = []
    pending: List[Dict[str, str]] = []

    try:
        health = runtime.get_health()
    except Exception:
        health = {}
    try:
        workers = runtime.get_workers_status()
    except Exception:
        workers = {}
    try:
        metrics = runtime.get_metrics()
    except Exception:
        metrics = {}
    try:
        queue = runtime.get_local_queue(workspace_id, limit=8)
    except Exception:
        queue = {}
    try:
        recent = history_runs(api_url, api_key, workspace_id, limit=5)
    except Exception:
        recent = []
    try:
        pending = pending_approvals(api_url, api_key, workspace_id)
    except Exception:
        pending = []

    worker_summary = workers.get("summary") if isinstance(workers.get("summary"), dict) else {}
    runs = metrics.get("runs") if isinstance(metrics.get("runs"), dict) else {}
    kpis = metrics.get("kpis") if isinstance(metrics.get("kpis"), dict) else {}
    local = metrics.get("local_companion") if isinstance(metrics.get("local_companion"), dict) else {}
    route_targets = health.get("supported_execution_targets")
    route_line = ", ".join([str(x) for x in route_targets if str(x).strip()]) if isinstance(route_targets, list) else "n/a"

    lines: List[str] = []
    lines.append("SYSTEM")
    lines.append(f"- runtime_ok: {'yes' if bool(health.get('ok')) else 'no'}")
    lines.append(f"- runtime_valid: {'yes' if bool(health.get('runtime_valid', True)) else 'no'}")
    lines.append(f"- openai_source: {str(health.get('openai_credential_source') or 'none')}")
    lines.append(f"- codex_signin_mode: {'oauth-web' if bool(health.get('codex_oauth_interactive_supported')) else 'token-vault'}")
    lines.append(f"- execution_targets: {route_line}")
    lines.append("")
    lines.append("ACTIVE PROFILE")
    lines.append(f"- workspace: {workspace_id}")
    lines.append(f"- mode: {pack.label}")
    lines.append(f"- trust: {trust.label}")
    lines.append(f"- target: {target.label}")
    lines.append("")
    lines.append("RUNTIME KPIS")
    lines.append(f"- runs_started: {int(runs.get('started') or 0)}")
    lines.append(f"- runs_completed: {int(runs.get('completed') or 0)}")
    lines.append(f"- runs_failed: {int(runs.get('failed') or 0)}")
    lines.append(f"- waiting_for_input: {int(runs.get('waiting_for_input') or 0)}")
    lines.append(f"- avg_run_duration: {fmt_ms(kpis.get('avg_run_duration_ms'))}")
    lines.append(f"- avg_first_value: {fmt_ms(kpis.get('avg_time_to_first_value_ms'))}")
    lines.append(f"- avg_human_wait: {fmt_ms(kpis.get('avg_human_wait_ms'))}")
    lines.append("")
    lines.append("LOCAL COMPANION")
    lines.append(f"- online_workers: {int(worker_summary.get('online') or local.get('online_workers') or 0)}")
    lines.append(f"- busy_workers: {int(worker_summary.get('busy') or 0)}")
    lines.append(f"- idle_workers: {int(worker_summary.get('idle') or 0)}")
    lines.append(f"- queued_runs: {int(queue.get('queued_count') or local.get('pending_runs') or 0)}")
    lines.append(f"- claimed_runs: {int(queue.get('claimed_count') or 0)}")
    lines.append("")
    lines.append("PENDING APPROVALS")
    if pending:
        for item in pending[:5]:
            lines.append(f"- {item['run_id'][:8]}: {preview_text(item.get('reason') or '', 80)}")
    else:
        lines.append("- none")
    lines.append("")
    lines.append("RECENT RUNS")
    if recent:
        for item in recent[:5]:
            run_id = str(item.get("run_id") or "")
            status = str(item.get("status") or "unknown")
            summary = preview_text(item.get("result_summary") or item.get("user_goal") or "", 84)
            lines.append(f"- {run_id[:8]}  {status}")
            if summary:
                lines.append(f"  {summary}")
    else:
        lines.append("- none")
    return "\n".join(lines)
