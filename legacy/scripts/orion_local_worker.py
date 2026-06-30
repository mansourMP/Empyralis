#!/usr/bin/env python3
import argparse
import json
import os
import queue
import re
import socket
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from server_modules.usage_reporting import build_usage_record, enrich_usage_record
from server_modules.agent_turn import build_local_worker_turn_request
from server_modules import rust_runtime_kernel_client

from orion_local_worker_content import normalize_content_plan_items
from orion_local_worker_execution import (
    HardInterruptRequested,
    LocalExecutionPauseRequired,
    build_local_execution_pack_result,
    registered_local_worker_tool_names,
)
from orion_local_worker_llm import (
    generate_chat_reply_for_turn_request,
    generate_chat_reply_with_provider_fallback,
    generate_pack_with_provider_fallback,
)
from orion_local_worker_runtime import RateLimitError, RuntimeClient, RuntimeInterruptController
from orion_local_worker_spreadsheets import build_spreadsheet_pack_result
from orion_local_worker_utils import (
    build_operator_system_prompt,
    collapse_duplicate_reply_sections,
    split_items,
)
from orion_local_worker_workspace import format_explicit_path_reply, format_workspace_listing_reply


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def ensure_trailing_slashless(url: str) -> str:
    return (url or "").rstrip("/")


def build_runtime_capabilities() -> List[str]:
    capability_map = {
        "browser_control": "browser_automation.interactive",
        "read_files": "filesystem.read_write",
        "write_files": "filesystem.read_write",
        "shell_command": "shell.execute",
    }
    capabilities = []
    try:
        manifest = rust_runtime_kernel_client.capability_manifest(include_aliases=False)
        next_action = str(manifest.get("next_action") or "").strip()
        if next_action and next_action != "return_capability_manifest":
            raise RuntimeError(f"unexpected_next_action:{next_action}")
        for item in list(manifest.get("capabilities") or []):
            mapped = capability_map.get(str(item or "").strip())
            if mapped and mapped not in capabilities:
                capabilities.append(mapped)
    except Exception:
        capabilities = [
            "browser_automation.interactive",
            "filesystem.read_write",
            "shell.execute",
        ]
    for extra in ("screenshot.capture", "local.worker"):
        if extra not in capabilities:
            capabilities.append(extra)
    return capabilities


def runtime_bootstrap_shape(enrollment_token: str) -> Tuple[str, List[str]]:
    if str(enrollment_token or "").strip():
        return "local_companion", ["local_companion", "local"]
    return "local", ["local"]


def _permission_entry(status: str, *, source: str, detail: str = "") -> Dict[str, Any]:
    return {
        "status": str(status or "unknown").strip().lower() or "unknown",
        "source": str(source or "probe").strip() or "probe",
        "detail": str(detail or "").strip(),
        "updated_at": utc_now_iso(),
    }


def _macos_screen_recording_probe() -> Optional[bool]:
    try:
        import Quartz  # type: ignore

        probe = getattr(Quartz, "CGPreflightScreenCaptureAccess", None)
        if callable(probe):
            return bool(probe())
    except Exception:
        return None
    return None


def _macos_accessibility_probe() -> Optional[bool]:
    try:
        from ApplicationServices import AXIsProcessTrusted  # type: ignore

        return bool(AXIsProcessTrusted())
    except Exception:
        try:
            import Quartz  # type: ignore

            probe = getattr(Quartz, "AXIsProcessTrusted", None)
            if callable(probe):
                return bool(probe())
        except Exception:
            return None
    return None


def build_runtime_permission_probe(
    *,
    runtime_type: str,
    capabilities: List[str],
    execution_targets: List[str],
) -> Dict[str, Dict[str, Any]]:
    capability_set = {str(item).strip().lower() for item in capabilities if str(item).strip()}
    execution_target_set = {str(item).strip().lower() for item in execution_targets if str(item).strip()}
    local_companion_like = str(runtime_type or "").strip().lower() == "local_companion" or "local_companion" in execution_target_set
    screen_recording_applicable = local_companion_like or "screenshot.capture" in capability_set
    accessibility_applicable = local_companion_like or "browser_automation.interactive" in capability_set or any(
        capability.startswith("computer_control.") for capability in capability_set
    )
    browser_applicable = local_companion_like or "browser_automation.interactive" in capability_set
    shell_applicable = "shell.execute" in capability_set

    screen_probe = _macos_screen_recording_probe() if sys.platform == "darwin" and screen_recording_applicable else None
    accessibility_probe = _macos_accessibility_probe() if sys.platform == "darwin" and accessibility_applicable else None

    return {
        "screen_recording": (
            _permission_entry("granted" if screen_probe else "denied", source="probe", detail="macOS screen capture permission probe.")
            if isinstance(screen_probe, bool)
            else _permission_entry(
                "unknown" if screen_recording_applicable else "unsupported",
                source="probe_pending" if screen_recording_applicable else "capability_manifest",
                detail="Worker has not confirmed screen recording permission yet." if screen_recording_applicable else "Screen capture is not advertised by this worker.",
            )
        ),
        "accessibility": (
            _permission_entry("granted" if accessibility_probe else "denied", source="probe", detail="macOS accessibility permission probe.")
            if isinstance(accessibility_probe, bool)
            else _permission_entry(
                "unknown" if accessibility_applicable else "unsupported",
                source="probe_pending" if accessibility_applicable else "capability_manifest",
                detail="Worker has not confirmed accessibility permission yet." if accessibility_applicable else "Accessibility-driven control is not advertised by this worker.",
            )
        ),
        "browser_session": _permission_entry(
            "granted" if browser_applicable else "unsupported",
            source="capability_manifest",
            detail="Interactive browser/session automation available." if browser_applicable else "Browser/session automation is not advertised by this worker.",
        ),
        "shell": _permission_entry(
            "granted" if shell_applicable else "unsupported",
            source="capability_manifest",
            detail="Shell execution available." if shell_applicable else "Shell execution is not advertised by this worker.",
        ),
    }


def _truncate_text(value: str, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _deterministic_chat_fallback(goal: str, llm_error: str) -> str:
    compact_error = re.sub(r"\s+", " ", str(llm_error or "").strip().lower())

    if "api.responses.write" in compact_error or "missing scopes" in compact_error:
        return "Reconnect your Codex/OpenAI auth, then retry. The current token is missing the scope needed for model replies."
    if "insufficient_quota" in compact_error or "exceeded your current quota" in compact_error:
        return "The connected AI account has quota or billing limits right now. Reconnect the account or switch provider, then retry."
    if "no provider credentials available" in compact_error or "missing_api_key" in compact_error:
        return "No active AI credential is available right now. Reconnect Codex/OpenAI, then retry."
    if compact_error:
        return f"I couldn’t get a model reply right now. Retry in a moment. Error: {_truncate_text(compact_error, limit=220)}"
    return "I couldn’t get a model reply right now. Retry in a moment."


def build_pack_result(
    run: Dict[str, Any],
    worker_id: str,
    *,
    progress_callback=None,
    control_state_provider=None,
    interrupt_controller=None,
) -> Tuple[str, Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    workflow_definition = (
        context.get("workflow_definition")
        if isinstance(context.get("workflow_definition"), dict)
        else metadata.get("workflow_definition")
    )
    if isinstance(workflow_definition, dict) and isinstance(workflow_definition.get("nodes"), list) and workflow_definition.get("nodes"):
        from server_modules.runs_execution import _execute_workflow_graph

        result = _execute_workflow_graph(
            str(run.get("run_id") or "").strip(),
            context,
            queue.Queue(),
            workflow_definition,
        )
        return (
            str(result.get("result_text") or "").strip(),
            result.get("result_data") if isinstance(result.get("result_data"), dict) else None,
            result.get("usage_masked") if isinstance(result.get("usage_masked"), dict) else None,
        )
    pack_id = str(metadata.get("outcome_pack") or "").strip()
    goal = str(context.get("user_goal") or "Complete requested task").strip()
    pack_inputs = metadata.get("pack_inputs") if isinstance(metadata.get("pack_inputs"), dict) else {}

    if pack_id == "weekly-content-studio":
        topics = split_items(str(pack_inputs.get("topics") or "General update"))
        channels = split_items(str(pack_inputs.get("channels") or "Instagram"))
        offers = str(pack_inputs.get("offers") or "Learn more").strip() or "Learn more"
        use_llm = str(os.getenv("ORION_LOCAL_WORKER_USE_LLM", "1")).strip().lower() not in {"0", "false", "no", "off"}
        llm_required = str(os.getenv("ORION_LOCAL_WORKER_LLM_REQUIRED", "0")).strip().lower() in {"1", "true", "yes", "on"}
        llm_usage_masked: Optional[Dict[str, Any]] = None
        llm_result: Optional[Dict[str, Any]] = None
        attempted_providers = ""
        llm_error = ""
        if use_llm:
            system_prompt = build_operator_system_prompt() or None
            user_prompt = (
                f"Business goal: {goal}\n"
                f"Topics: {', '.join(topics or ['General update'])}\n"
                f"Channels: {', '.join(channels or ['Instagram'])}\n"
                f"Offer/CTA: {offers}\n"
                "Create a practical weekly plan (max 7 items) for SMB users."
            )
            llm_result, llm_usage_masked, attempted_providers, llm_error = generate_pack_with_provider_fallback(
                context,
                metadata,
                system_prompt,
                user_prompt,
            )
            if llm_required and not isinstance(llm_result, dict):
                raise RuntimeError(
                    "LLM generation required but unavailable. "
                    f"attempted={attempted_providers or 'none'} error={llm_error or 'unknown'}"
                )

        content_plan = normalize_content_plan_items(
            (llm_result or {}).get("content_plan") if isinstance(llm_result, dict) else None,
            topics,
            channels,
            offers,
        )
        summary = f"Local worker drafted {len(content_plan)} weekly content items for {', '.join(channels[:2] or ['social channels'])}."
        if isinstance(llm_result, dict):
            llm_summary = str(llm_result.get("summary") or "").strip()
            if llm_summary:
                summary = llm_summary
        next_steps = [
            "Review generated headlines and CTAs.",
            "Approve calendar scheduling.",
            "Connect publish tool when ready.",
        ]
        if isinstance(llm_result, dict):
            llm_steps = llm_result.get("next_steps")
            if isinstance(llm_steps, list):
                normalized_steps = [str(item).strip() for item in llm_steps if str(item).strip()]
                if normalized_steps:
                    next_steps = normalized_steps[:5]
        data = {
            "pack_id": "weekly-content-studio",
            "summary": summary,
            "inputs": {
                "topics_count": len(topics),
                "channels_count": len(channels),
                "offers_count": 1 if offers else 0,
            },
            "outputs": {"content_plan": content_plan},
            "next_steps": next_steps,
        }
        if isinstance(llm_result, dict) and isinstance(llm_usage_masked, dict):
            data["generation"] = {
                "mode": "llm",
                "provider": llm_usage_masked.get("provider"),
                "model": llm_usage_masked.get("model"),
                "attempted_providers": attempted_providers,
            }
        else:
            data["generation"] = {
                "mode": "deterministic_fallback",
                "provider": "local_companion",
                "model": "local-worker-v0",
                "attempted_providers": attempted_providers,
                "error": llm_error or "llm disabled or unavailable",
            }
        return summary, data, llm_usage_masked

    if pack_id == "customer-ops-autopilot":
        inbox_items = split_items(str(pack_inputs.get("inbox") or ""))
        leads = split_items(str(pack_inputs.get("leads") or ""))
        slots = split_items(str(pack_inputs.get("slots") or ""))

        if not leads and inbox_items:
            leads = [f"Contact {idx + 1}" for idx, _ in enumerate(inbox_items[:3])]

        if not inbox_items and not leads:
            summary = "Client Workflow Autopilot needs inbox or lead inputs before execution."
            data = {
                "pack_id": "customer-ops-autopilot",
                "summary": summary,
                "inputs": {
                    "inbox_count": 0,
                    "lead_count": 0,
                    "slot_count": len(slots),
                },
                "outputs": {
                    "urgent_count": 0,
                    "outbound_actions": 0,
                    "triage": [],
                    "follow_ups": [],
                    "bookings": [],
                },
                "connector": {
                    "enabled": False,
                    "gmail_drafts_created": 0,
                    "calendar_events_created": 0,
                    "warnings": ["Connector not attached in local worker v0."],
                },
                "next_steps": [
                    "Add inbox messages or lead list.",
                    "Optionally add booking slots.",
                    "Run again to generate follow-ups and booking proposals.",
                ],
            }
            return summary, data, None

        triage = [
            {
                "id": "triage-1",
                "lead_name": leads[0] if leads else "Lead A",
                "message": "Prioritized for response",
                "priority": "high",
                "status": "triaged",
            }
        ]
        follow_ups = [
            {
                "id": "followup-1",
                "lead_name": leads[0] if leads else "Lead A",
                "draft_message": "Thanks for your interest. Here is our recommended next step.",
                "status": "drafted",
            }
        ]
        bookings = []
        if slots:
            bookings = [
                {
                    "id": "booking-1",
                    "lead_name": leads[0] if leads else "Lead A",
                    "proposed_slot": slots[0],
                    "status": "proposed",
                }
            ]
        summary = f"Local worker triaged inbox and prepared follow-up + booking proposal for {len(leads) or 1} lead(s)."
        data = {
            "pack_id": "customer-ops-autopilot",
            "summary": summary,
            "inputs": {
                "inbox_count": len(inbox_items),
                "lead_count": len(leads),
                "slot_count": len(slots),
            },
            "outputs": {
                "urgent_count": 1,
                "outbound_actions": len(follow_ups) + len(bookings),
                "triage": triage,
                "follow_ups": follow_ups,
                "bookings": bookings,
            },
            "connector": {
                "enabled": False,
                "gmail_drafts_created": 0,
                "calendar_events_created": 0,
                "warnings": ["Connector not attached in local worker v0."],
            },
            "next_steps": [
                "Approve outbound follow-up draft.",
                "Approve booking slot proposal." if slots else "Add booking slots to generate scheduling proposals.",
                "Connect Gmail/Calendar when ready.",
            ],
        }
        return summary, data, None

    if pack_id == "competitor-brief-digest":
        competitors = split_items(str(pack_inputs.get("competitors") or "Competitor A, Competitor B"))
        objectives = split_items(str(pack_inputs.get("objectives") or "Improve messaging"))
        briefs = []
        for idx, name in enumerate(competitors[:5] or ["Competitor A"]):
            threat = "high" if idx == 0 else "medium"
            briefs.append(
                {
                    "id": f"brief-{idx + 1}",
                    "name": name,
                    "threat_level": threat,
                    "summary": f"{name} is active in your category. Monitor pricing and messaging updates.",
                    "recommended_move": "Differentiate offer and tighten follow-up speed.",
                }
            )
        summary = f"Local worker created competitor brief for {len(briefs)} competitor(s)."
        data = {
            "pack_id": "competitor-brief-digest",
            "summary": summary,
            "inputs": {
                "competitors_count": len(competitors),
                "positioning_count": max(1, len(split_items(str(pack_inputs.get('positioning') or 'positioning')))),
                "objectives_count": len(objectives),
            },
            "outputs": {
                "high_threat_count": len([item for item in briefs if item.get("threat_level") == "high"]),
                "briefs": briefs,
            },
            "next_steps": [
                "Pick top competitor and run deeper teardown.",
                "Map pricing deltas.",
                "Adjust offer messaging for next campaign.",
            ],
        }
        return summary, data, None

    if pack_id == "spreadsheet-ops-v1":
        summary, data = build_spreadsheet_pack_result(pack_inputs)
        return summary, data, None

    if pack_id == "local-execution-v1":
        summary, data = build_local_execution_pack_result(
            run,
            metadata,
            pack_inputs,
            progress_callback=progress_callback,
            control_state_provider=control_state_provider,
            interrupt_controller=interrupt_controller,
        )
        return summary, data, None

    allow_workspace_listing_reply = str(metadata.get("allow_workspace_listing_reply") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    explicit_path_reply = format_explicit_path_reply(goal)
    if explicit_path_reply is not None:
        return explicit_path_reply

    listing_reply = format_workspace_listing_reply(goal) if allow_workspace_listing_reply else None
    if listing_reply is not None:
        return listing_reply

    use_llm = str(os.getenv("ORION_LOCAL_WORKER_USE_LLM", "1")).strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    attempted_providers = ""
    llm_error = ""
    if use_llm:
        system_prompt = build_operator_system_prompt() or None
        turn_request = build_local_worker_turn_request(
            worker_id=worker_id,
            run=run,
            context=context,
            metadata=metadata,
            goal=goal,
        )
        reply, usage_masked, attempted_providers, llm_error = generate_chat_reply_for_turn_request(
            turn_request=turn_request,
            context=context,
            metadata=metadata,
            system_prompt=system_prompt,
        )
        if reply:
            cleaned_reply = collapse_duplicate_reply_sections(reply)
            data = {
                "generated_by": "empyralis_local_worker_llm_v1",
                "worker_id": worker_id,
                "goal": goal,
                "reply": cleaned_reply,
                "completed_at": utc_now_iso(),
                "generation": {
                    "mode": "llm_chat",
                    "attempted_providers": attempted_providers,
                },
                "skills": {
                    "scope": str(metadata.get("skill_scope") or "").strip() or None,
                    "skill_ids": (
                        list((metadata.get("skill_bundle") or {}).get("skill_ids"))
                        if isinstance(metadata.get("skill_bundle"), dict) and isinstance((metadata.get("skill_bundle") or {}).get("skill_ids"), list)
                        else []
                    ),
                },
            }
            return cleaned_reply, data, usage_masked

    summary = _deterministic_chat_fallback(goal, llm_error or "")
    fallback = {
        "generated_by": "empyralis_local_worker_v0",
        "worker_id": worker_id,
        "goal": goal,
        "completed_at": utc_now_iso(),
        "generation": {
            "mode": "deterministic_fallback",
            "attempted_providers": attempted_providers,
            "error": llm_error or "llm unavailable for general chat goal",
        },
    }
    return summary, fallback, None


def process_run(client: RuntimeClient, worker_id: str, run: Dict[str, Any], step_delay_seconds: float, verbose: bool = True):
    run_id = str(run.get("run_id") or "").strip()
    if not run_id:
        raise RuntimeError("Claimed run payload missing run_id.")

    interrupt_controller = run.get("__interrupt_controller__")

    def _interrupt_snapshot() -> Dict[str, Any]:
        if interrupt_controller is None or not hasattr(interrupt_controller, "interrupt_snapshot"):
            return {}
        return interrupt_controller.interrupt_snapshot(run_id=run_id)

    def _raise_if_hard_interrupted() -> None:
        snapshot = _interrupt_snapshot()
        if bool(snapshot.get("interrupt_requested")):
            raise HardInterruptRequested(
                str(snapshot.get("reason") or "Hard kill requested by operator. Local execution halted.").strip()
                or "Hard kill requested by operator. Local execution halted.",
                state=snapshot,
            )

    phases = [
        "Local worker accepted task.",
        "Preparing local output.",
        "Finalizing result payload.",
    ]
    permission_probe = build_runtime_permission_probe(
        runtime_type="local",
        capabilities=build_runtime_capabilities(),
        execution_targets=["local"],
    )
    for phase in phases:
        _raise_if_hard_interrupted()
        if verbose:
            print(f"[{run_id[:8]}] {phase}")
        client.heartbeat_worker(worker_id, run_id, phase, permission_probe=permission_probe)
        client.heartbeat_run(run_id, worker_id, phase)
        if step_delay_seconds > 0:
            time.sleep(step_delay_seconds)
        _raise_if_hard_interrupted()

    try:
        def _progress_callback(event: Dict[str, Any]) -> None:
            if not isinstance(event, dict):
                return
            note = str(event.get("message") or "runtime_task_heartbeat").strip() or "runtime_task_heartbeat"
            try:
                client.heartbeat_run(run_id, worker_id, note, event=event)
            except Exception:
                return

        def _control_state_provider(active_run_id: str) -> Dict[str, Any]:
            snapshot = _interrupt_snapshot()
            if snapshot:
                return {
                    "status": "interrupt_requested",
                    "pause_requested": True,
                    "interrupt_requested": True,
                    "interrupt_reason": str(snapshot.get("reason") or "").strip() or None,
                    "machine_id": snapshot.get("machine_id"),
                    "machine_control_state": "interrupting" if str(snapshot.get("scope") or "").strip().lower() == "machine" else "active",
                    "run_id": snapshot.get("run_id"),
                    "event_type": snapshot.get("event_type"),
                    "requested_at": snapshot.get("requested_at"),
                    "sequence": snapshot.get("sequence"),
                    "scope": snapshot.get("scope"),
                    "wait_reason": "hard_kill_requested",
                }
            remote_state = client.get_task_control_state(active_run_id, worker_id)
            if bool(remote_state.get("interrupt_requested")) and interrupt_controller is not None and hasattr(interrupt_controller, "request_interrupt"):
                interrupt_controller.request_interrupt(
                    scope=str(remote_state.get("scope") or "run").strip().lower() or "run",
                    event_type=str(remote_state.get("event_type") or "run_interrupt").strip().lower() or "run_interrupt",
                    reason=str(remote_state.get("interrupt_reason") or remote_state.get("reason") or "").strip() or None,
                    run_id=str(remote_state.get("run_id") or active_run_id).strip() or active_run_id,
                    machine_id=str(remote_state.get("machine_id") or worker_id).strip() or worker_id,
                    requested_at=str(remote_state.get("requested_at") or "").strip() or None,
                    sequence=remote_state.get("sequence"),
                )
                snapshot = _interrupt_snapshot()
                if snapshot:
                    return {
                        **remote_state,
                        "interrupt_requested": True,
                        "interrupt_reason": str(snapshot.get("reason") or "").strip() or remote_state.get("interrupt_reason"),
                        "event_type": snapshot.get("event_type"),
                        "requested_at": snapshot.get("requested_at"),
                        "sequence": snapshot.get("sequence"),
                        "scope": snapshot.get("scope"),
                    }
            return remote_state

        summary, result_data, usage_override = build_pack_result(
            run,
            worker_id,
            progress_callback=_progress_callback,
            control_state_provider=_control_state_provider,
            interrupt_controller=interrupt_controller,
        )
    except LocalExecutionPauseRequired as pause:
        client.pause_run(
            run_id,
            worker_id,
            pause.summary,
            result_data=pause.result_data,
            browser_checkpoint=pause.browser_checkpoint,
            wait_reason=str(pause.result_data.get("pause_reason") or "human_unblock_required") if isinstance(pause.result_data, dict) else "human_unblock_required",
        )
        if verbose:
            print(f"[{run_id[:8]}] Paused for human unblock.")
        return
    except HardInterruptRequested as interrupt:
        client.fail_run(run_id, worker_id, interrupt.summary)
        if verbose:
            print(f"[{run_id[:8]}] Hard kill acknowledged.")
        return
    finally:
        if interrupt_controller is not None and hasattr(interrupt_controller, "clear_run_interrupt"):
            interrupt_controller.clear_run_interrupt(run_id)
    usage_timestamp = utc_now_iso()
    usage = build_usage_record(
        "local_companion",
        "local-worker-v0",
        0,
        0,
        0,
        run_id=run_id,
        timestamp=usage_timestamp,
    )
    if isinstance(usage_override, dict):
        usage = enrich_usage_record(
            usage_override,
            run_id=run_id,
            timestamp=usage_timestamp,
        )
    client.complete_run(run_id, worker_id, summary, result_data=result_data, usage_masked=usage)
    if verbose:
        print(f"[{run_id[:8]}] Completed.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Empyralis Local Worker v0")
    parser.add_argument("--runtime-url", default="http://127.0.0.1:8001", help="Empyralis runtime base URL")
    parser.add_argument("--api-key", default="", help="Runtime API key (or set ORION_API_KEY/RUNTIME_KEY)")
    parser.add_argument("--enrollment-token", default="", help="Machine enrollment token for platform-relay bootstrap")
    parser.add_argument("--worker-id", default="", help="Stable worker ID")
    parser.add_argument("--poll-seconds", type=float, default=2.0, help="Claim polling interval")
    parser.add_argument("--idle-heartbeat-seconds", type=float, default=10.0, help="Idle heartbeat interval")
    parser.add_argument("--step-delay-seconds", type=float, default=1.0, help="Delay between simulated run steps")
    parser.add_argument("--once", action="store_true", help="Process at most one run then exit")
    parser.add_argument("--max-wait-seconds", type=float, default=120.0, help="When --once is used, stop after this wait if no run is claimed")
    parser.add_argument("--quiet", action="store_true", help="Minimize logs")
    args = parser.parse_args()

    api_key = (args.api_key or "").strip()
    enrollment_token = (args.enrollment_token or os.getenv("ORION_MACHINE_ENROLLMENT_TOKEN") or "").strip()
    if not api_key:
        api_key = (
            os.getenv("ORION_API_KEY")
            or os.getenv("RUNTIME_KEY")
            or os.getenv("CREW_API_KEY")
            or ""
        ).strip()

    worker_id = (args.worker_id or "").strip()
    if not worker_id:
        host = socket.gethostname().split(".")[0]
        worker_id = f"empyralis-local-{host}-{uuid.uuid4().hex[:6]}"
    client = RuntimeClient(base_url=args.runtime_url, api_key=api_key, enrollment_token=enrollment_token)
    client.load_runtime_session(worker_id)
    if not api_key and not enrollment_token and not client.runtime_session_token:
        print("Missing runtime API key, machine enrollment token, or saved runtime session.", file=sys.stderr)
        return 2
    runtime_instance_id = client.runtime_instance_id or f"{socket.gethostname().split('.')[0]}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    verbose = not args.quiet

    if verbose:
        print(f"Empyralis Local Worker v0")
        print(f"Runtime: {ensure_trailing_slashless(args.runtime_url)}")
        print(f"Worker:  {worker_id}")
        auth_mode = 'saved runtime session' if not api_key and not enrollment_token and client.runtime_session_token else (
            'platform-relay bootstrap' if enrollment_token else 'runtime api key'
        )
        print(f"Auth:    {auth_mode}")
        print(f"Registered tools: {registered_local_worker_tool_names()}")

    runtime_type, execution_targets = runtime_bootstrap_shape(enrollment_token)

    try:
        permission_probe = build_runtime_permission_probe(
            runtime_type=runtime_type,
            capabilities=build_runtime_capabilities(),
            execution_targets=execution_targets,
        )
        registration = client.bootstrap_runtime_session(
            worker_id,
            runtime_type=runtime_type,
            display_name=f"Empyralis Local Worker ({socket.gethostname().split('.')[0]})",
            platform=sys.platform,
            policy_mode=str(os.getenv("ORION_RUNTIME_POLICY_MODE_DEFAULT") or "local_default").strip() or "local_default",
            capabilities=build_runtime_capabilities(),
            execution_targets=execution_targets,
            instance_id=runtime_instance_id,
            permission_probe=permission_probe,
        )
        if verbose and bool(registration.get("resumed")):
            print("[info] Restored persisted runtime session.")
        if not bool(registration.get("legacy")) and not str(client.runtime_session_token or "").strip():
            print("[error] Runtime session bootstrap did not yield a session token.", file=sys.stderr)
            return 5
    except Exception as exc:
        print(f"[error] Runtime session bootstrap failed: {exc}", file=sys.stderr)
        return 5

    deadline = time.time() + max(5.0, args.max_wait_seconds)
    next_idle_heartbeat_at = 0.0
    processed_runs = 0
    consecutive_errors = 0
    interrupt_controller = RuntimeInterruptController()
    control_listener_stop = threading.Event()
    control_listener_thread: Optional[threading.Thread] = None

    def _handle_control_event(event: Dict[str, Any]) -> None:
        payload = event.get("data") if isinstance(event.get("data"), dict) else {}
        event_type = str(payload.get("event") or event.get("event") or "").strip().lower() or "message"
        if event_type == "machine_resume":
            interrupt_controller.clear_machine_interrupt(str(payload.get("machine_id") or worker_id).strip() or worker_id)
            return
        if event_type not in {"hard_kill", "run_interrupt"}:
            return
        interrupt_controller.request_interrupt(
            scope="machine" if event_type == "hard_kill" else "run",
            event_type=event_type,
            reason=str(payload.get("reason") or "").strip() or None,
            run_id=str(payload.get("run_id") or "").strip() or None,
            machine_id=str(payload.get("machine_id") or worker_id).strip() or worker_id,
            requested_at=str(payload.get("requested_at") or "").strip() or None,
            sequence=payload.get("sequence"),
        )

    if not bool(registration.get("legacy")) and str(client.runtime_session_token or "").strip():
        control_listener_thread = client.start_control_stream_listener(
            worker_id,
            on_event=_handle_control_event,
            stop_event=control_listener_stop,
        )

    try:
        while True:
            now = time.time()
            if now >= next_idle_heartbeat_at:
                try:
                    client.heartbeat_worker(
                        worker_id,
                        None,
                        "idle",
                        permission_probe=build_runtime_permission_probe(
                            runtime_type=runtime_type,
                            capabilities=build_runtime_capabilities(),
                            execution_targets=execution_targets,
                        ),
                    )
                    consecutive_errors = 0
                except RateLimitError as exc:
                    if verbose:
                        print(f"[warn] Worker heartbeat rate-limited, retry in {exc.retry_after_seconds}s")
                    next_idle_heartbeat_at = time.time() + exc.retry_after_seconds
                    time.sleep(exc.retry_after_seconds)
                    continue
                except Exception as exc:
                    if verbose:
                        print(f"[warn] Worker heartbeat failed: {exc}")
                next_idle_heartbeat_at = now + max(2.0, args.idle_heartbeat_seconds)

            try:
                claimed = client.claim_run(worker_id)
                run = claimed.get("run") if isinstance(claimed, dict) else None
                if isinstance(run, dict):
                    run["__interrupt_controller__"] = interrupt_controller
                    run_id = str(run.get("run_id") or "").strip()
                    try:
                        process_run(client, worker_id, run, max(0.0, args.step_delay_seconds), verbose=verbose)
                        processed_runs += 1
                        consecutive_errors = 0
                        if args.once:
                            break
                    except Exception as run_exc:
                        message = str(run_exc)[:1000] or "Local worker execution failed."
                        if verbose:
                            print(f"[error] Run failed ({run_id[:8] if run_id else 'unknown'}): {message}")
                        if run_id:
                            try:
                                client.fail_run(run_id, worker_id, message)
                            except Exception as fail_exc:
                                if verbose:
                                    print(f"[warn] Could not mark run as failed ({run_id[:8]}): {fail_exc}")
                        consecutive_errors += 1
                        if args.once:
                            return 4
                else:
                    if args.once and time.time() > deadline:
                        if verbose:
                            print("No local run was queued before timeout.")
                        return 3
                    time.sleep(max(0.2, args.poll_seconds))
            except KeyboardInterrupt:
                if verbose:
                    print("Interrupted, shutting down worker.")
                break
            except RateLimitError as exc:
                consecutive_errors += 1
                delay = max(float(exc.retry_after_seconds), max(0.5, args.poll_seconds))
                if verbose:
                    print(f"[warn] Worker rate-limited, sleeping {delay:.1f}s before retry.")
                time.sleep(delay)
            except Exception as exc:
                consecutive_errors += 1
                if verbose:
                    print(f"[error] Worker loop error: {exc}")
                delay = min(15.0, max(1.0, args.poll_seconds) * (1.0 + (consecutive_errors * 0.35)))
                time.sleep(delay)
    finally:
        control_listener_stop.set()
        if control_listener_thread is not None:
            control_listener_thread.join(timeout=1.5)

    if verbose:
        print(f"Processed runs: {processed_runs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
