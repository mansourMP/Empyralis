from __future__ import annotations

import json
import shutil
import time
from typing import Any, Dict, List, Optional, Tuple, Union

from . import flows_shared as _shared
from . import question_catalog as q
from .services.live_tui import TUIContext, inbox_events as _inbox_events
from .services.provider_credentials import (
    create_or_update_profile as _create_or_update_profile,
    create_provider_credential as _create_provider_credential,
    provider_and_credential_section,
    setup_action as _setup_action,
    setup_complete as _setup_complete,
    setup_create_session as _setup_create_session,
    verify_provider_credential as _verify_provider_credential,
)
from .tui.handlers import chat_actions as _tui_chat_actions
from .tui.handlers import core_actions as _tui_core_actions
from .tui.handlers import run_actions as _tui_run_actions

globals().update({name: value for name, value in vars(_shared).items() if not name.startswith("__")})
_run_chat_goal = _tui_chat_actions.run_chat_goal
_send_chat_telegram_message = _tui_chat_actions.send_chat_telegram_message

# Concrete run/live-tui flow implementations (migrated from flows_shared.py).
def post_run_decision(api_url: str, api_key: str, run_id: str, decision: str) -> None:
    submit_run_decision(_runtime_client(api_url, api_key), run_id, decision)


def stream_run(ui: UI, api_url: str, api_key: str, run_id: str) -> Tuple[bool, str]:
    from urllib import request as urlrequest
    url = f"{api_url}/runs/{run_id}/stream"
    req = urlrequest.Request(url, headers={"X-API-Key": api_key, "Accept": "text/event-stream"}, method="GET")

    event_name = "message"
    data_lines: List[str] = []
    run_ok = False
    terminal_message = ""
    spinner = _ProgressSpinner("running")
    spinner.tick("running")

    def dispatch(evt: str, data: str) -> None:
        nonlocal run_ok, terminal_message
        if not data and evt != "pause":
            return

        if evt == "pause":
            spinner.clear()
            ui.note("\n[HITL] Human input required.")
            decision_choice = ui.choose(
                q.Q_APPROVAL_RESOLVE,
                [Choice("Proceed", "Proceed", "Continue execution"), Choice("Hold", "Hold", "Stop execution")],
                default_index=0,
                title=q.TITLE_APPROVAL,
            )
            try:
                post_run_decision(api_url, api_key, run_id, decision_choice.key)
                ui.note(f"[HITL] Decision sent: {decision_choice.key}")
            except Exception as exc:
                ui.note(f"{err('failed:')} Decision failed: {exc}")
            spinner.tick("running")
            return

        parsed: Dict[str, Any] = {}
        try:
            obj = json.loads(data)
            if isinstance(obj, dict):
                parsed = obj
        except Exception:
            pass

        message = str(parsed.get("message") or data).strip()
        level = str(parsed.get("level") or "info").upper()
        ts = short_ts(parsed.get("ts"))
        runtime_event = str(parsed.get("event") or "").strip()
        event_key = runtime_event.lower()
        next_label = _spinner_label_from_event(spinner.label, event_key, message)
        if next_label != spinner.label:
            spinner.tick(next_label)

        if FULL_OUTPUT:
            spinner.clear()
            if message:
                ui.note(f"[{ts}] {level:<5} {message}")
            spinner.tick()
        elif not COMPACT_MODE:
            level_key = level.lower()
            noisy_events = {"local_heartbeat", "pack_phase", "dag_node_start", "dag_compiled"}
            important_events = {
                "approval_requested",
                "approval_received",
                "approval_resolved",
                "run_retry",
                "run_error",
                "dag_node_error",
                "profile_failover",
                "profile_candidate_failed",
            }

            if event_key in noisy_events:
                return

            if level_key in {"warn", "error"} or event_key in important_events:
                spinner.clear()
                ui.note(f"[{ts}] {level:<5} {message}")
                spinner.tick()

        if event_key == "run_complete":
            run_ok = True
            terminal_message = message or "Run finished."
        elif event_key == "run_error":
            run_ok = False
            terminal_message = message or "Run failed."

    try:
        from urllib import request as urlrequest
        with urlrequest.urlopen(req, context=tls_context()) as response:
            for line in response:
                decoded = line.decode("utf-8").strip()
                if not decoded:
                    continue
                if decoded.startswith("event: "):
                    event_name = decoded[len("event: ") :]
                elif decoded.startswith("data: "):
                    data = decoded[len("data: ") :]
                    dispatch(event_name, data)
                    event_name = "message"
    except Exception as exc:
        spinner.done(False, f"stream error: {exc}")
        return False, str(exc)

    spinner.done(run_ok, terminal_message)
    return run_ok, terminal_message


def run_once(
    ui: UI,
    api_url: str,
    runtime_key: str,
    workspace_id: str,
    engine: str,
    goal: str,
    pack: Choice,
    trust: Choice,
    target: Choice,
    pack_inputs: Optional[Dict[str, str]] = None,
    prompt_connectors: bool = True,
    extra_metadata: Optional[Dict[str, Any]] = None,
    provider_override: Optional[str] = None,
    credential_id_override: Optional[str] = None,
    model_override: Optional[str] = None,
) -> int:
    google_connector_id = ""
    channel_connectors = None

    if prompt_connectors:
        created = choose_and_create_connectors(ui, api_url, runtime_key, workspace_id)
        if created:
            channel_connectors = created

    metadata: Dict[str, Any] = {
        "trust_mode": trust.key,
        "execution_target": target.key,
        "source": "terminal",
    }
    if pack.key:
        metadata["outcome_pack"] = pack.key
        metadata["outcome_pack_label"] = pack.label
    if pack_inputs:
        metadata["pack_inputs"] = pack_inputs
    if google_connector_id:
        metadata["connector_credential_id"] = google_connector_id
    if channel_connectors:
        metadata["channel_connectors"] = channel_connectors
    if extra_metadata:
        for key, value in extra_metadata.items():
            if value is None:
                continue
            metadata[key] = value

    payload: Dict[str, Any] = {
        "engine": engine,
        "workspace_id": workspace_id,
        "user_goal": goal,
        "metadata": metadata,
    }
    if provider_override:
        payload["provider"] = provider_override
    if credential_id_override:
        payload["credential_id"] = credential_id_override
    if model_override:
        payload["model"] = model_override

    client = _runtime_client(api_url, runtime_key)
    try:
        precheck = client.precheck_run(payload)
        policy = precheck.get("tool_policy_precheck") if isinstance(precheck.get("tool_policy_precheck"), dict) else {}
        blocked_count = int(policy.get("blocked_count") or 0)
        approval_count = int(policy.get("approval_required_count") or 0)
        allow_count = int(policy.get("allow_count") or 0)
        if blocked_count or approval_count:
            ui.note(
                f"{strong('policy precheck')}: allow={allow_count} approval={approval_count} blocked={blocked_count}"
            )
        if blocked_count > 0:
            blocked = policy.get("blocked") if isinstance(policy.get("blocked"), list) else []
            blocked_preview = ", ".join([str(item) for item in blocked[:5]]) if blocked else "unknown"
            ui.note(f"{err('failed:')} Run blocked by tool policy before start ({blocked_preview}).")
            return 6
    except Exception as exc:
        ui.note(f"{warn('warning:')} Could not run policy precheck: {exc}")

    try:
        started = service_start_run(client, payload)
    except Exception as exc:
        ui.note(f"\n{err('failed:')} Failed to start run: {exc}")
        return 3

    run_id = str(started.get("run_id") or "").strip()
    if not run_id:
        ui.note(f"\n{err('failed:')} Run started without run_id: {json.dumps(started)}")
        return 4

    if FULL_OUTPUT:
        ui.note("")
        ui.note(strong("Run started"))
        ui.note(f"{strong('run_id')}: {accent(run_id)}")
    else:
        ui.note(f"{strong('run_id')}: {accent(run_id)}")
    if not pack.key:
        if FULL_OUTPUT:
            ui.note("mode: General Digital Worker")
    else:
        if FULL_OUTPUT:
            ui.note(f"mode: Specialist Pack ({pack.label})")
    route = started.get("route") if isinstance(started.get("route"), dict) else {}
    if route and FULL_OUTPUT:
        reason_preview = preview_text(route.get("reason"), 84)
        ui.note(
            "route: requested={requested} selected={selected} reason={reason}".format(
                requested=route.get("requested"),
                selected=route.get("selected"),
                reason=reason_preview,
            )
        )
    if FULL_OUTPUT:
        ui.note("\nStreaming events...\n")
    else:
        ui.note(muted("progress: live"))

    ok_run, message = stream_run(ui, api_url, runtime_key, run_id)
    final_ok = ok_run
    status: Any = None
    summary: Any = None

    try:
        run_state = service_stream_run_state(client, run_id)
        status = run_state.get("status")
        summary = run_state.get("result")
        if isinstance(status, str):
            lowered = status.strip().lower()
            if lowered == "completed":
                final_ok = True
            elif lowered in {"failed", "timeout", "error"}:
                final_ok = False
        if (not message or message == "Run finished.") and isinstance(summary, str) and summary.strip():
            message = summary.strip()
    except Exception as exc:
        ui.note(f"{warn('warning:')} Could not fetch final run state: {exc}")

    ui.note("\n---")
    ui.note(f"Result: {ok('SUCCESS') if final_ok else err('FAILED')}")
    if FULL_OUTPUT:
        ui.note(f"Message: {message}")
        if status is not None:
            ui.note(f"status: {status}")
        if summary:
            ui.note(f"summary: {summary}")
    else:
        combined = summary if summary else message
        preview = compact_summary_line(combined) if COMPACT_MODE else preview_text(combined, RESULT_PREVIEW_CHARS)
        if status is not None and (not COMPACT_MODE):
            ui.note(f"{strong('status')}: {status}")
        if preview:
            ui.note(f"{strong('summary')}: {preview}")
        if COMPACT_MODE:
            ui.note(muted(f"details: /runs/{run_id}"))
        else:
            ui.note(muted(f"full details available via /runs/{run_id}"))

    return 0 if final_ok else 5


def guided_run_flow(ui: UI, api_url: str, runtime_key: str, workspace_id: str, engine: str) -> int:
    step_banner(ui, 1, 7, "Run Goal")
    goal = ui.input(
        q.Q_GUIDED_GOAL,
        default=DEFAULT_QUICK_GOAL,
        required=True,
        title=q.TITLE_RUN,
    )
    choice_log(ui, "Goal", goal)

    step_banner(ui, 2, 7, "Operating Mode")
    mode = ui.choose(
        q.Q_GUIDED_OPERATING_MODE,
        [Choice("general", "General Digital Worker"), Choice("specialist", "Specialist Template")],
        default_index=0,
        title=q.TITLE_RUN,
    )
    choice_log(ui, "Operating mode", mode)

    pack = GENERAL_MODE
    if mode.key == "specialist":
        step_banner(ui, 3, 7, "Specialist Template")
        pack = ui.choose(q.Q_GUIDED_SPECIALIST_TEMPLATE, SPECIALIST_PACKS, default_index=0, title=q.TITLE_RUN)
        choice_log(ui, "Specialist pack", pack)
    else:
        ui.note(muted("skipping: specialist template selection"))

    step_banner(ui, 4, 7, "Trust Mode")
    trust = ui.choose(q.Q_GUIDED_TRUST_MODE, TRUST_MODES, default_index=0, title=q.TITLE_RUN)
    choice_log(ui, "Trust mode", trust)

    step_banner(ui, 5, 7, "Execution Target")
    target = ui.choose(q.Q_GUIDED_EXECUTION_TARGET, EXECUTION_TARGETS, default_index=0, title=q.TITLE_RUN)
    choice_log(ui, "Execution target", target)

    step_banner(ui, 6, 7, "Optional Inputs")
    pack_inputs = None
    if ui.confirm(q.Q_GUIDED_ADD_CONTEXT, default_yes=False, title=q.TITLE_RUN):
        pack_inputs = collect_pack_inputs(ui, pack.key)
        value_log(ui, "Inputs added", str(len(pack_inputs or {})))

    step_banner(ui, 7, 7, "External Channels")
    prompt_connectors = ui.confirm(q.Q_GUIDED_CONNECT_NOW, default_yes=False, title=q.TITLE_RUN)
    flag_log(ui, "Connect channels", prompt_connectors)

    ui.note("\n---")
    ui.note(strong("Guided Run Summary"))
    ui.note(f"Goal: {goal}")
    ui.note(f"Mode: {pack.label}")
    ui.note(f"Trust: {trust.label}")
    ui.note(f"Target: {target.label}")
    ui.note(f"Connectors: {'Prompt' if prompt_connectors else 'Skip'}")

    if not ui.confirm(q.Q_RUN_CONFIRM_START, default_yes=True, title=q.TITLE_RUN):
        ui.note(muted("Run cancelled by user."))
        return 0

    return run_once(
        ui=ui,
        api_url=api_url,
        runtime_key=runtime_key,
        workspace_id=workspace_id,
        engine=engine,
        goal=goal,
        pack=pack,
        trust=trust,
        target=target,
        pack_inputs=pack_inputs,
        prompt_connectors=prompt_connectors,
    )


# --- TUI Action Handlers ---

def _handle_doctor(ctx: TUIContext) -> Optional[str]:
    return _tui_core_actions.handle_doctor(ctx)

def _handle_dashboard(ctx: TUIContext) -> Optional[str]:
    return _tui_core_actions.handle_dashboard(ctx)

def _handle_status(ctx: TUIContext) -> Optional[str]:
    return _tui_core_actions.handle_status(ctx)

def _handle_chat(ctx: TUIContext) -> Optional[str]:
    return _tui_chat_actions.handle_chat(
        ctx,
        handle_mode_fn=_handle_mode,
        handle_trust_fn=_handle_trust,
        handle_target_fn=_handle_target,
        print_doctor_summary_fn=print_doctor_summary,
        inbox_events_fn=_inbox_events,
        run_chat_goal_fn=_run_chat_goal,
        send_telegram_message_fn=_send_chat_telegram_message,
    )

def _handle_inbox(ctx: TUIContext) -> Optional[str]:
    return _tui_chat_actions.handle_inbox(ctx, inbox_events_fn=_inbox_events)

def _handle_runs(ctx: TUIContext) -> Optional[str]:
    return _tui_run_actions.handle_runs(ctx, stream_run=stream_run)

def _handle_queue(ctx: TUIContext) -> Optional[str]:
    return _tui_core_actions.handle_queue(ctx)

def _handle_metrics(ctx: TUIContext) -> Optional[str]:
    return _tui_core_actions.handle_metrics(ctx)

def _handle_approval(ctx: TUIContext) -> Optional[str]:
    return _tui_core_actions.handle_approval(ctx)

def _handle_connectors(ctx: TUIContext) -> Optional[str]:
    return _tui_core_actions.handle_connectors(ctx)

def _handle_trust(ctx: TUIContext) -> Optional[str]:
    return _tui_core_actions.handle_trust(ctx)

def _handle_target(ctx: TUIContext) -> Optional[str]:
    return _tui_core_actions.handle_target(ctx)

def _handle_mode(ctx: TUIContext) -> Optional[str]:
    return _tui_core_actions.handle_mode(ctx)

def _handle_run(ctx: TUIContext) -> Optional[str]:
    return _tui_run_actions.handle_run(
        ctx,
        run_once_fn=run_once,
        collect_pack_inputs_fn=collect_pack_inputs,
    )

def _handle_advanced(ctx: TUIContext) -> Optional[str]:
    return _tui_chat_actions.handle_advanced(
        ctx,
        handle_chat_fn=_handle_chat,
        handle_doctor_fn=_handle_doctor,
        handle_dashboard_fn=_handle_dashboard,
        handle_inbox_fn=_handle_inbox,
        handle_runs_fn=_handle_runs,
        handle_metrics_fn=_handle_metrics,
        handle_queue_fn=_handle_queue,
        handle_approval_fn=_handle_approval,
        handle_connectors_fn=_handle_connectors,
        run_once_fn=run_once,
    )


def run_tui_shell(ui: UI, api_url: str, api_key: str, workspace_id: str, engine: str) -> int:
    from .tui.shell import run_tui_shell_impl

    return run_tui_shell_impl(
        ui=ui,
        api_url=api_url,
        api_key=api_key,
        workspace_id=workspace_id,
        engine=engine,
        handle_chat_fn=_handle_chat,
        trust_choice=TRUST_MODES[0],
        target_choice=EXECUTION_TARGETS[0],
        pack_choice=GENERAL_MODE,
    )



__all__ = [
    "guided_run_flow",
    "run_once",
    "run_tui_shell",
]
