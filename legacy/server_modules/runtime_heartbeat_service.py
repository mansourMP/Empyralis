from __future__ import annotations

import asyncio
import inspect
from typing import Any, Callable, Optional


def _resolve_sync(value: Any) -> Any:
    if not inspect.isawaitable(value):
        return value
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(value)
    finally:
        loop.close()


def _resolve_heartbeat_scope(
    *,
    metadata: dict[str, Any],
    resolve_workspace_tenant_id: Optional[Callable[[str], Any]] = None,
) -> tuple[str, str]:
    workspace_id = str(metadata.get("workspace_id") or "").strip()
    if not workspace_id:
        raise ValueError("Heartbeat workspace scope is not configured.")

    tenant_id = str(metadata.get("tenant_id") or "").strip()
    if not tenant_id and callable(resolve_workspace_tenant_id):
        resolved = _resolve_sync(resolve_workspace_tenant_id(workspace_id))
        tenant_id = str(resolved or "").strip()
    if not tenant_id:
        raise ValueError("Heartbeat tenant scope is not configured.")

    return tenant_id, workspace_id


def build_heartbeat_turn_request(
    *,
    build_inbound_agent_turn_request: Callable[..., Any],
    tasks: list[str],
    metadata: dict[str, Any],
    pending_started: Any,
    wake_requests: Optional[list[dict[str, Any]]] = None,
    recent_changes: Optional[list[dict[str, Any]]] = None,
    scheduler_goals: Optional[list[str]] = None,
    user_preferences: Optional[str] = None,
    policy_bounds: Optional[dict[str, Any]] = None,
) -> Any:
    merged_metadata = dict(metadata or {})
    merged_metadata.update(
        {
            "source": "heartbeat",
            "heartbeat_tasks": list(tasks),
            "heartbeat_pending_schedules": pending_started if isinstance(pending_started, list) else [],
            "heartbeat_trigger": str(metadata.get("trigger") or "scheduled"),
            "heartbeat_file": str(metadata.get("heartbeat_file") or ""),
        }
    )
    if wake_requests:
        merged_metadata["wake_request_ids"] = [
            str(item.get("id") or "").strip()
            for item in wake_requests
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        ]
    if recent_changes:
        merged_metadata["context_event_ids"] = [
            str(item.get("id") or "").strip()
            for item in recent_changes
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        ]
    if policy_bounds:
        merged_metadata["scheduler_policy"] = dict(policy_bounds)
    actor_id = (
        str(merged_metadata.get("owner_user_id") or "").strip()
        or str(merged_metadata.get("owner_email") or "").strip().lower()
        or "anonymous"
    )
    actor_display_name = str(merged_metadata.get("owner_email") or actor_id).strip() or actor_id
    sections: list[str] = []
    if tasks:
        sections.append(
            "Heartbeat checklist tasks:\n"
            + "\n".join(f"- {task}" for task in tasks)
        )
    if wake_requests:
        wake_lines = []
        for item in wake_requests:
            if not isinstance(item, dict):
                continue
            trigger_kind = str(item.get("trigger_kind") or "wake").strip()
            summary = str(item.get("summary") or item.get("reason") or "").strip()
            if summary:
                wake_lines.append(f"- [{trigger_kind}] {summary}")
        if wake_lines:
            sections.append("Wake reasons:\n" + "\n".join(wake_lines))
    if recent_changes:
        change_lines = []
        for item in recent_changes:
            if not isinstance(item, dict):
                continue
            summary = str(item.get("summary") or "").strip()
            if summary:
                source = str(item.get("source_app") or "context").strip()
                change_lines.append(f"- [{source}] {summary}")
        if change_lines:
            sections.append("Recent context changes:\n" + "\n".join(change_lines))
    goal_lines = [str(item or "").strip() for item in list(scheduler_goals or []) if str(item or "").strip()]
    if goal_lines:
        sections.append("Current goals:\n" + "\n".join(f"- {line}" for line in goal_lines))
    if user_preferences and str(user_preferences).strip():
        sections.append("User preferences:\n" + str(user_preferences).strip()[:2000])
    if policy_bounds:
        sections.append(
            "Scheduler policy bounds:\n"
            f"- quiet hours: {int(policy_bounds.get('quiet_hours_start', 23)):02d}:00 to {int(policy_bounds.get('quiet_hours_end', 7)):02d}:00\n"
            f"- max runtime seconds: {int(policy_bounds.get('max_runtime_seconds', 20))}\n"
            f"- plan tier: {str(policy_bounds.get('plan_tier') or 'standard')}"
        )
    sections.append(
        "Review the queued heartbeat and wake reasons. Decide whether any follow-up is needed now. "
        "If no follow-up is needed, explain briefly and stop. Stay inside policy and approval limits."
    )
    heartbeat_goal = "\n\n".join(section for section in sections if str(section).strip())
    policy_context = {
        "trust_mode": str(merged_metadata.get("trust_mode") or "").strip() or None,
        "outcome_pack": str(merged_metadata.get("outcome_pack") or "").strip() or None,
        "execution_target": str(merged_metadata.get("execution_target") or "").strip() or None,
        "action_policy": merged_metadata.get("action_policy") if isinstance(merged_metadata.get("action_policy"), dict) else None,
    }
    context_hints = {
        "engine": str(merged_metadata.get("engine") or "orion").strip().lower() or "orion",
        "workflow_id": str(merged_metadata.get("workflow_id") or "").strip() or None,
        "provider": str(merged_metadata.get("provider") or "").strip() or None,
        "model": str(merged_metadata.get("model") or "").strip() or None,
        "credential_id": str(merged_metadata.get("credential_id") or "").strip() or None,
        "agent_role": str(merged_metadata.get("agent_role") or "orchestrator").strip() or "orchestrator",
        "max_iterations": merged_metadata.get("max_iterations"),
        "metadata": merged_metadata,
    }
    tenant_id, workspace_id = _resolve_heartbeat_scope(metadata=merged_metadata)
    return build_inbound_agent_turn_request(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        session_id=(
            str(merged_metadata.get("session_id") or "").strip()
            or str(merged_metadata.get("thread_id") or "").strip()
            or str(merged_metadata.get("parent_run_id") or "").strip()
            or str(merged_metadata.get("workflow_id") or "").strip()
            or "run-start"
        ),
        channel=str(merged_metadata.get("channel") or "web").strip() or "web",
        actor_type="user",
        actor_id=actor_id,
        actor_display_name=actor_display_name,
        message=heartbeat_goal,
        context_hints={key: value for key, value in context_hints.items() if value not in (None, "", [], {})},
        execution_mode="durable",
        response_mode="artifact",
        machine_target=(
            str(merged_metadata.get("machine_target") or "").strip()
            or str(merged_metadata.get("execution_target_selected") or "").strip()
            or str(merged_metadata.get("execution_target") or "").strip()
            or None
        ),
        policy_context={key: value for key, value in policy_context.items() if value not in (None, "", [], {})},
    )


def heartbeat_scheduler(*, lock: Any, scheduler: Any) -> Any:
    with lock:
        return scheduler


def ensure_heartbeat_scheduler_started(
    *,
    lock: Any,
    scheduler: Any,
    scheduler_factory: Callable[[], Any],
) -> Any:
    with lock:
        if scheduler is None:
            scheduler = scheduler_factory()
            scheduler.start()
        return scheduler


def heartbeat_status_payload(*, scheduler: Optional[Any]) -> dict[str, Any]:
    if scheduler is None:
        return {
            "ok": False,
            "detail": "Heartbeat scheduler is not configured.",
        }
    return {
        "ok": True,
        **scheduler.status(),
    }


def trigger_heartbeat_payload(*, scheduler: Optional[Any]) -> dict[str, Any]:
    if scheduler is None:
        raise RuntimeError("Heartbeat scheduler is not configured.")
    return {
        "ok": True,
        **scheduler.trigger_now(),
    }


def _append_heartbeat_entry(workspace_id: str, result: dict) -> None:
    """Append a timestamped entry to HEARTBEAT.md after a heartbeat run."""
    try:
        from server_modules.workspace_context import (
            read_workspace_context_file,
            write_workspace_context_file,
        )
        from datetime import datetime, timezone

        acted = bool(result.get("acted"))
        summary = str(result.get("summary") or "").strip()
        scheduler_mode = str(result.get("scheduler_mode") or "heartbeat").strip()
        run_id = str(result.get("run_id") or "").strip() or None

        now = datetime.now(timezone.utc)
        timestamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        date_str = now.strftime("%Y-%m-%d")

        status_icon = "✅" if acted else "⏸️"

        entry = (
            f"### {status_icon} {timestamp}\n"
            f"- **Mode**: {scheduler_mode}\n"
            f"- **Summary**: {summary}\n"
        )
        if run_id:
            entry += f"- **Run**: {run_id}\n"
        entry += "\n"

        existing = read_workspace_context_file(
            "HEARTBEAT.md",
            workspace_id=workspace_id,
        )

        # Keep last 50 entries — split on ### markers
        parts = (existing or "").split("### ")
        # parts[0] is the header/content before first ###
        header = parts[0].strip() if parts else ""
        entries = []
        for p in parts[1:]:
            p = p.strip()
            if p:
                entries.append("### " + p)

        # Add new entry, trim to 50
        entries.append(entry.strip())
        if len(entries) > 50:
            entries = entries[-50:]

        new_content = header + "\n\n" + "\n".join(entries) if header else "\n".join(entries)
        new_content = new_content.strip() + "\n"

        write_workspace_context_file(
            "HEARTBEAT.md",
            new_content,
            workspace_id=workspace_id,
        )
    except Exception:
        pass


def build_heartbeat_run_callback(
    *,
    build_inbound_agent_turn_request: Callable[..., Any],
    trigger_pending_heartbeat_schedules: Callable[..., Any],
    execute_system_agent_turn: Callable[..., Any],
    run_execution_services: Callable[[], Any],
    resolve_workspace_tenant_id: Optional[Callable[[str], Any]] = None,
    claim_due_scheduler_wake_requests: Optional[Callable[..., Any]] = None,
    build_wakeup_execution_bundle: Optional[Callable[..., Any]] = None,
    finalize_scheduler_wake_requests: Optional[Callable[..., Any]] = None,
    enqueue_lane_work: Optional[Callable[..., Any]] = None,
) -> Callable[[list[str], dict[str, Any]], dict[str, Any]]:
    def _execute_heartbeat_run(tasks: list[str], metadata: dict[str, Any]) -> dict[str, Any]:
        scoped_metadata = dict(metadata or {})
        workspace_id = str(scoped_metadata.get("workspace_id") or "").strip()
        if not workspace_id:
            return {
                "acted": False,
                "summary": "Heartbeat workspace scope is not configured.",
            }

        if not str(scoped_metadata.get("tenant_id") or "").strip() and callable(resolve_workspace_tenant_id):
            resolved_tenant_id = _resolve_sync(resolve_workspace_tenant_id(workspace_id))
            if str(resolved_tenant_id or "").strip():
                scoped_metadata["tenant_id"] = str(resolved_tenant_id).strip()

        pending_schedule_result = trigger_pending_heartbeat_schedules(workspace_id=workspace_id)
        pending_started = pending_schedule_result.get("started") if isinstance(pending_schedule_result, dict) else []
        wake_requests: list[dict[str, Any]] = []
        execution_bundle: dict[str, Any] = {}
        tenant_id = str(scoped_metadata.get("tenant_id") or "").strip()
        if tenant_id and callable(claim_due_scheduler_wake_requests):
            claimed = _resolve_sync(
                claim_due_scheduler_wake_requests(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                )
            )
            if isinstance(claimed, dict):
                wake_requests = [
                    dict(item)
                    for item in list(claimed.get("items") or [])
                    if isinstance(item, dict)
                ]
            if wake_requests and callable(build_wakeup_execution_bundle):
                execution_bundle = _resolve_sync(
                    build_wakeup_execution_bundle(
                        tenant_id=tenant_id,
                        workspace_id=workspace_id,
                        heartbeat_tasks=list(tasks),
                        wake_requests=wake_requests,
                    )
                )
        if not tasks and not wake_requests:
            if pending_started:
                first = pending_started[0] if isinstance(pending_started, list) and pending_started else {}
                return {
                    "acted": True,
                    "run_id": str((first or {}).get("run_id") or "").strip() or None,
                    "summary": f"Heartbeat started {len(pending_started)} pending schedule(s).",
                    "scheduler_mode": "exact_schedule",
                }
            return {
                "acted": False,
                "summary": "No pending heartbeat tasks.",
                "scheduler_mode": "idle",
            }
        try:
            turn_request = build_heartbeat_turn_request(
                build_inbound_agent_turn_request=build_inbound_agent_turn_request,
                tasks=tasks,
                metadata=scoped_metadata,
                pending_started=pending_started,
                wake_requests=wake_requests,
                recent_changes=execution_bundle.get("recent_changes") if isinstance(execution_bundle, dict) else None,
                scheduler_goals=execution_bundle.get("scheduler_goals") if isinstance(execution_bundle, dict) else None,
                user_preferences=execution_bundle.get("user_preferences") if isinstance(execution_bundle, dict) else None,
                policy_bounds=execution_bundle.get("policy") if isinstance(execution_bundle, dict) else None,
            )
        except ValueError as exc:
            return {
                "acted": False,
                "summary": str(exc),
            }
        try:
            result = execute_system_agent_turn(
                turn_request=turn_request,
                run_execution_services=run_execution_services(),
            )
        except Exception:
            if wake_requests and tenant_id and callable(finalize_scheduler_wake_requests):
                _resolve_sync(
                    finalize_scheduler_wake_requests(
                        tenant_id=tenant_id,
                        workspace_id=workspace_id,
                        wake_requests=wake_requests,
                        status="failed",
                        denial_reason="execution_failed",
                        mark_context_seen=False,
                    )
                )
            raise
        result_payload = result if isinstance(result, dict) else {}
        if wake_requests and tenant_id and callable(finalize_scheduler_wake_requests):
            _resolve_sync(
                finalize_scheduler_wake_requests(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    wake_requests=wake_requests,
                    status="executed",
                    mark_context_seen=True,
                    metadata_patch={"run_id": str(result_payload.get("run_id") or "").strip() or None},
                )
            )
        _heartbeat_result = {
            "acted": True,
            **result_payload,
            "summary": (
                str(execution_bundle.get("summary") or "").strip()
                if isinstance(execution_bundle, dict) and str(execution_bundle.get("summary") or "").strip()
                else (
                    f"Heartbeat started a run for {len(tasks)} task(s)."
                    + (f" Also started {len(pending_started)} pending schedule(s)." if pending_started else "")
                )
            ),
            "scheduler_mode": (
                str(execution_bundle.get("metadata", {}).get("scheduler_mode") or "").strip()
                if isinstance(execution_bundle, dict)
                else ""
            ) or ("mixed" if wake_requests and tasks else ("wakeup" if wake_requests else "heartbeat")),
            "wake_request_ids": [
                str(item.get("id") or "").strip()
                for item in wake_requests
                if str(item.get("id") or "").strip()
            ],
            "context_event_ids": list(execution_bundle.get("metadata", {}).get("context_event_ids") or []) if isinstance(execution_bundle, dict) else [],
        }
        _append_heartbeat_entry(workspace_id, _heartbeat_result)
        return _heartbeat_result

    def _start_heartbeat_run(tasks: list[str], metadata: dict[str, Any]) -> dict[str, Any]:
        scoped_metadata = dict(metadata or {})
        workspace_id = str(scoped_metadata.get("workspace_id") or "").strip()
        if not workspace_id:
            return {
                "acted": False,
                "summary": "Heartbeat workspace scope is not configured.",
            }
        if callable(enqueue_lane_work):
            queue_result = enqueue_lane_work(
                lane="cron",
                label="Heartbeat follow-up" if tasks else "Scheduler wakeup",
                metadata={
                    "workspace_id": workspace_id,
                    "tenant_id": str(scoped_metadata.get("tenant_id") or "").strip() or None,
                    "trigger": str(scoped_metadata.get("trigger") or "scheduled").strip() or "scheduled",
                    "heartbeat_task_count": len(tasks),
                    "source": "heartbeat",
                },
                work=lambda: _execute_heartbeat_run(tasks, scoped_metadata),
            )
            queue_item_id = (
                str(queue_result.get("item_id") or "").strip()
                if isinstance(queue_result, dict)
                else ""
            )
            return {
                "acted": True,
                "queued": True,
                "lane": "cron",
                "queue_item_id": queue_item_id or None,
                "summary": (
                    "Queued heartbeat work in the cron lane."
                    + (f" Queue item: {queue_item_id}." if queue_item_id else "")
                ),
                "scheduler_mode": "queued",
            }
        return _execute_heartbeat_run(tasks, scoped_metadata)

    return _start_heartbeat_run


def build_heartbeat_notify_callback(
    *,
    handle_telegram_send_message: Callable[..., Any],
    workspace_id: Optional[str],
    dispatch_cloud_channel_outbound_fn: Optional[Callable[..., Any]] = None,
    resolve_cloud_telegram_session_id_fn: Optional[Callable[[], Optional[str]]] = None,
) -> Callable[[str], None]:
    scoped_workspace_id = str(workspace_id or "").strip() or None

    def _heartbeat_notify(message: str) -> None:
        if not scoped_workspace_id:
            return

        # Stage 6: Try cloud-session-manager path first (proactive delivery via GramJS)
        if callable(resolve_cloud_telegram_session_id_fn) and callable(dispatch_cloud_channel_outbound_fn):
            try:
                session_id = resolve_cloud_telegram_session_id_fn()
                if session_id:
                    asyncio.run(dispatch_cloud_channel_outbound_fn(
                        session_id=session_id,
                        text=message,
                        remote_jid="me",
                    ))
                    return
            except Exception:
                pass

        # Fall back to old Telegram bot path
        try:
            asyncio.run(handle_telegram_send_message(message, workspace_id=scoped_workspace_id))
        except Exception:
            return

    return _heartbeat_notify
