from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from server_modules import bounded_scheduler_service
from server_modules.runtime_lane_queue import runtime_lane_queue_snapshot
from server_modules.sage_profile_service import list_sage_profile


def _coerce_text(value: Any) -> str:
    return str(value or "").strip()


def _parse_utc(value: Any) -> Optional[datetime]:
    token = _coerce_text(value)
    if not token:
        return None
    try:
        parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _format_quiet_hours(start: int, end: int) -> str:
    return f"{int(start):02d}:00–{int(end):02d}:00"


def _queue_status_label(status: str) -> str:
    token = _coerce_text(status).lower()
    if token in {"running", "active", "started"}:
        return "Running now"
    if "approval" in token:
        return "Needs your OK"
    if token in {"queued", "pending", "waiting"}:
        return "Waiting"
    if token in {"completed", "executed", "done", "succeeded", "success"}:
        return "Done"
    if token in {"failed", "cancelled", "canceled"}:
        return "Done"
    return "Waiting"


def _governed_work_item(
    *,
    item_id: str,
    label: str,
    lane: str,
    product_lane: str,
    status: str,
    summary: Optional[str] = None,
    scheduled_for: Optional[str] = None,
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "id": item_id,
        "label": label or "Queued work",
        "lane": lane or None,
        "product_lane": product_lane,
        "status": status,
        "status_label": _queue_status_label(status),
        "summary": summary or None,
        "scheduled_for": scheduled_for or None,
        "run_id": run_id or None,
    }


def _derive_queue_overview(
    *,
    schedule_items: List[Dict[str, Any]],
    next_action: Optional[Dict[str, Any]],
    wake_queue: Dict[str, Any],
    lane_queue: Dict[str, Any],
    quiet_hours: Dict[str, Any],
) -> Dict[str, Any]:
    running_items: List[Dict[str, Any]] = []
    waiting_items: List[Dict[str, Any]] = []
    blocked_items: List[Dict[str, Any]] = []
    done_items: List[Dict[str, Any]] = []
    scheduled_items: List[Dict[str, Any]] = []

    lanes = lane_queue.get("lanes") if isinstance(lane_queue.get("lanes"), dict) else {}
    for lane_name, lane_value in lanes.items():
        lane_payload = lane_value if isinstance(lane_value, dict) else {}
        for entry in list(lane_payload.get("active") or []):
            if not isinstance(entry, dict):
                continue
            running_items.append(
                _governed_work_item(
                    item_id=_coerce_text(entry.get("id")),
                    label=_coerce_text(entry.get("label")) or "Running work",
                    lane=str(lane_name),
                    product_lane="now",
                    status=_coerce_text(entry.get("status")) or "running",
                    summary=_coerce_text(entry.get("summary")) or (_coerce_text(entry.get("run_id")) and f"Run {_coerce_text(entry.get('run_id'))}") or None,
                    run_id=_coerce_text(entry.get("run_id")) or None,
                )
            )
        for entry in list(lane_payload.get("pending") or []):
            if not isinstance(entry, dict):
                continue
            status = _coerce_text(entry.get("status")) or "queued"
            destination = blocked_items if "approval" in status.lower() else waiting_items
            destination.append(
                _governed_work_item(
                    item_id=_coerce_text(entry.get("id")),
                    label=_coerce_text(entry.get("label")) or "Queued work",
                    lane=str(lane_name),
                    product_lane="needs_ok" if "approval" in status.lower() else "waiting",
                    status=status,
                    summary=_coerce_text(entry.get("summary")) or None,
                    run_id=_coerce_text(entry.get("run_id")) or None,
                )
            )

    for entry in list(lane_queue.get("recent") or []):
        if not isinstance(entry, dict):
            continue
        status = _coerce_text(entry.get("status")) or "completed"
        if "approval" in status.lower():
            blocked_items.append(
                _governed_work_item(
                    item_id=_coerce_text(entry.get("id")),
                    label=_coerce_text(entry.get("label")) or "Approval waiting",
                    lane=_coerce_text(entry.get("lane")),
                    product_lane="needs_ok",
                    status=status,
                    summary=_coerce_text(entry.get("summary")) or None,
                    run_id=_coerce_text(entry.get("run_id")) or None,
                )
            )
        else:
            done_items.append(
                _governed_work_item(
                    item_id=_coerce_text(entry.get("id")),
                    label=_coerce_text(entry.get("label")) or "Finished work",
                    lane=_coerce_text(entry.get("lane")),
                    product_lane="done",
                    status=status,
                    summary=_coerce_text(entry.get("summary")) or None,
                    run_id=_coerce_text(entry.get("run_id")) or None,
                )
            )

    for item in schedule_items:
        if not isinstance(item, dict) or not item.get("enabled"):
            continue
        scheduled_items.append(
            _governed_work_item(
                item_id=_coerce_text(item.get("id")),
                label=_coerce_text(item.get("name")) or "Scheduled action",
                lane="cron",
                product_lane="scheduled",
                status="scheduled",
                summary=(
                    f"{_coerce_text(item.get('schedule_kind')) or 'cron'} · "
                    f"{_coerce_text(item.get('wake_mode')) or 'now'} · "
                    f"{_coerce_text(item.get('delivery')) or 'announce'}"
                ),
                scheduled_for=_coerce_text(item.get("next_run_at")) or None,
            )
        )

    pending_wakeups = int(wake_queue.get("pending_count") or 0)
    return {
        "next_scheduled_action": next_action,
        "queued_count": len(waiting_items),
        "running_now_count": len(running_items),
        "blocked_on_approval_count": len(blocked_items),
        "done_count": len(done_items),
        "pending_wakeup_count": pending_wakeups,
        "quiet_hours": quiet_hours,
        "lanes": {
            "now": running_items[:6],
            "waiting": waiting_items[:6],
            "scheduled": scheduled_items[:6],
            "needs_ok": blocked_items[:6],
            "done": done_items[:6],
        },
    }


def _serialize_schedule_item(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": _coerce_text(item.get("id")),
        "name": _coerce_text(item.get("name")) or "Scheduled action",
        "enabled": bool(item.get("enabled")),
        "schedule_kind": _coerce_text(item.get("schedule_kind")) or "cron",
        "day_of_week": _coerce_text(item.get("day_of_week")) or None,
        "time_hhmm": _coerce_text(item.get("time_hhmm")) or None,
        "timezone": _coerce_text(item.get("timezone")) or "local",
        "next_run_at": _coerce_text(item.get("next_run_at")) or None,
        "wake_mode": _coerce_text(item.get("wake_mode")) or "now",
        "delivery": _coerce_text(item.get("delivery")) or "announce",
        "pending_heartbeat": bool(item.get("pending_heartbeat")),
        "pending_heartbeat_slot": _coerce_text(item.get("pending_heartbeat_slot")) or None,
        "last_run_at": _coerce_text(item.get("last_run_at")) or None,
        "last_error": _coerce_text(item.get("last_error")) or None,
    }


def _next_action(items: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    ranked = sorted(
        (
            item
            for item in items
            if item.get("enabled") and _parse_utc(item.get("next_run_at")) is not None
        ),
        key=lambda item: _parse_utc(item.get("next_run_at")) or datetime.max.replace(tzinfo=timezone.utc),
    )
    if not ranked:
        return None
    return dict(ranked[0])


async def build_sage_heartbeat_snapshot(
    *,
    tenant_id: str,
    workspace_id: str,
    account_seed: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    profile_payload = list_sage_profile(
        workspace_id=workspace_id,
        account_seed=account_seed,
    )
    scheduler_payload = await bounded_scheduler_service.scheduler_status_snapshot(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    policy = scheduler_payload.get("policy") if isinstance(scheduler_payload.get("policy"), dict) else {}
    quiet_start = int(policy.get("quiet_hours_start") or 22)
    quiet_end = int(policy.get("quiet_hours_end") or 7)
    raw_jobs = (
        scheduler_payload.get("exact_jobs", {}).get("items")
        if isinstance(scheduler_payload.get("exact_jobs"), dict)
        else []
    )
    schedule_items = [
        _serialize_schedule_item(item)
        for item in raw_jobs
        if isinstance(item, dict)
    ]
    next_action = _next_action(schedule_items)
    wake_queue = scheduler_payload.get("wake_queue") if isinstance(scheduler_payload.get("wake_queue"), dict) else {}
    lane_queue = runtime_lane_queue_snapshot()
    profile = profile_payload.get("profile") if isinstance(profile_payload.get("profile"), dict) else {}
    bootstrap = profile_payload.get("bootstrap") if isinstance(profile_payload.get("bootstrap"), dict) else {}
    quiet_hours_status = bounded_scheduler_service.quiet_hours_status_snapshot(
        policy=bounded_scheduler_service.SchedulerPolicyBounds(
            quiet_hours_start=quiet_start,
            quiet_hours_end=quiet_end,
            max_event_triggers_per_hour=int(policy.get("max_event_triggers_per_hour") or bounded_scheduler_service.DEFAULT_MAX_EVENT_TRIGGERS_PER_HOUR),
            max_self_proposed_per_hour=int(policy.get("max_self_proposed_per_hour") or bounded_scheduler_service.DEFAULT_MAX_SELF_PROPOSED_PER_HOUR),
            max_runtime_seconds=int(policy.get("max_runtime_seconds") or bounded_scheduler_service.DEFAULT_MAX_RUNTIME_SECONDS),
            minimum_battery_percent=int(policy.get("minimum_battery_percent") or bounded_scheduler_service.DEFAULT_MINIMUM_BATTERY_PERCENT),
            require_network_online=bool(policy.get("require_network_online")),
            require_owner_approval_for_privileged_wakeups=bool(policy.get("require_owner_approval_for_privileged_wakeups")),
            plan_tier=_coerce_text(policy.get("plan_tier")) or "default",
        )
    )
    queue_overview = _derive_queue_overview(
        schedule_items=schedule_items,
        next_action=next_action,
        wake_queue=wake_queue,
        lane_queue=lane_queue if isinstance(lane_queue, dict) else {},
        quiet_hours=quiet_hours_status,
    )
    plugin_health = _plugin_health_snapshot()
    rust_kernel_health = _rust_kernel_health_snapshot()
    heartbeat_decision = _heartbeat_snapshot_decision(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        queue_overview=queue_overview,
        quiet_hours=quiet_hours_status,
        policy=policy,
        rust_kernel=rust_kernel_health,
        lane_queue=lane_queue if isinstance(lane_queue, dict) else {},
    )
    runtime_health = _runtime_health_decision(
        operation="heartbeat_snapshot",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        profile=profile,
        bootstrap=bootstrap,
        quiet_hours=quiet_hours_status,
        ambient_monitor=scheduler_payload.get("ambient_monitor") if isinstance(scheduler_payload.get("ambient_monitor"), dict) else {},
        reminders_count=len(schedule_items),
        wake_queue=wake_queue,
        lane_queue=lane_queue if isinstance(lane_queue, dict) else {},
        queue_overview=queue_overview,
        policy=policy,
        plugin=plugin_health,
        rust_kernel=rust_kernel_health,
    )
    readiness_gate = _runtime_health_decision(
        operation="readiness_gate",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        profile=profile,
        bootstrap=bootstrap,
        quiet_hours=quiet_hours_status,
        ambient_monitor=scheduler_payload.get("ambient_monitor") if isinstance(scheduler_payload.get("ambient_monitor"), dict) else {},
        reminders_count=len(schedule_items),
        wake_queue=wake_queue,
        lane_queue=lane_queue if isinstance(lane_queue, dict) else {},
        queue_overview=queue_overview,
        policy=policy,
        plugin=plugin_health,
        rust_kernel=rust_kernel_health,
    )
    return {
        "workspace_id": workspace_id,
        "health": heartbeat_decision.get("health"),
        "readiness": readiness_gate.get("readiness") if isinstance(readiness_gate.get("readiness"), dict) else {},
        "readiness_gate": readiness_gate,
        "operator_next_action": heartbeat_decision.get("next_action"),
        "heartbeat_snapshot": heartbeat_decision,
        "runtime_health": runtime_health,
        "profile": {
            "recurring_responsibility": _coerce_text(profile.get("recurring_responsibility")) or None,
            "communication_style": _coerce_text(profile.get("communication_style")) or None,
        },
        "bootstrap": {
            "complete": bool(bootstrap.get("complete")),
            "progress_label": _coerce_text(bootstrap.get("progress_label")) or None,
        },
        "quiet_hours": {
            "start_hour": quiet_start,
            "end_hour": quiet_end,
            "label": _format_quiet_hours(quiet_start, quiet_end),
        },
        "ambient_monitor": scheduler_payload.get("ambient_monitor") if isinstance(scheduler_payload.get("ambient_monitor"), dict) else {},
        "reminders": {
            "count": len(schedule_items),
            "items": schedule_items,
        },
        "next_scheduled_action": next_action,
        "wake_queue": {
            "pending_count": int(wake_queue.get("pending_count") or 0),
            "claimed_count": int(wake_queue.get("claimed_count") or 0),
            "pending": wake_queue.get("pending") if isinstance(wake_queue.get("pending"), list) else [],
            "claimed": wake_queue.get("claimed") if isinstance(wake_queue.get("claimed"), list) else [],
        },
        "lane_queue": lane_queue if isinstance(lane_queue, dict) else {},
        "queue_overview": queue_overview,
        "policy": {
            "plan_tier": _coerce_text(policy.get("plan_tier")) or None,
            "require_network_online": bool(policy.get("require_network_online")),
            "minimum_battery_percent": int(policy.get("minimum_battery_percent") or 0),
            "max_self_proposed_per_hour": int(policy.get("max_self_proposed_per_hour") or 0),
        },
        "retry": bounded_scheduler_service.retry_queue_status(workspace_id),
        "plugin": plugin_health,
        "rust_kernel": rust_kernel_health,
    }


def _runtime_health_decision(
    *,
    operation: str = "heartbeat_snapshot",
    tenant_id: str,
    workspace_id: str,
    profile: Dict[str, Any],
    bootstrap: Dict[str, Any],
    quiet_hours: Dict[str, Any],
    ambient_monitor: Dict[str, Any],
    reminders_count: int,
    wake_queue: Dict[str, Any],
    lane_queue: Dict[str, Any],
    queue_overview: Dict[str, Any],
    policy: Dict[str, Any],
    plugin: Dict[str, Any],
    rust_kernel: Dict[str, Any],
) -> Dict[str, Any]:
    from server_modules import rust_runtime_kernel_client

    payload = {
        "operation": operation,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "profile": {
            "complete": bool(profile) and not bool(bootstrap.get("complete") is False),
        },
        "bootstrap": {
            "complete": bool(bootstrap.get("complete")),
        },
        "scheduler": {
            "enabled": bool(policy) or bool(wake_queue),
        },
        "policy": {
            **(policy if isinstance(policy, dict) else {}),
            "scheduler_enabled": True,
        },
        "quiet_hours": {
            "active": bool(quiet_hours.get("active") or quiet_hours.get("quiet_hours_active")),
        },
        "ambient_monitor": ambient_monitor if isinstance(ambient_monitor, dict) else {},
        "reminders": {
            "count": int(reminders_count or 0),
        },
        "wake_queue": wake_queue if isinstance(wake_queue, dict) else {},
        "lane_queue": lane_queue if isinstance(lane_queue, dict) else {},
        "queue_overview": queue_overview if isinstance(queue_overview, dict) else {},
        "retry": bounded_scheduler_service.retry_queue_status(workspace_id),
        "plugin": plugin if isinstance(plugin, dict) else {},
        "rust_kernel": rust_kernel if isinstance(rust_kernel, dict) else {},
        "runtime": {
            "rust_kernel_available": bool((rust_kernel if isinstance(rust_kernel, dict) else {}).get("available")),
            "stale_heartbeat_count": int((lane_queue if isinstance(lane_queue, dict) else {}).get("stale_heartbeat_count") or 0),
            "unavailable_worker_count": int((lane_queue if isinstance(lane_queue, dict) else {}).get("unavailable_worker_count") or 0),
        },
    }
    decision = rust_runtime_kernel_client.runtime_health_decision(**payload)
    expected_next_actions = {
        "restore_rust_kernel",
        "inspect_plugin_system",
        "investigate_runtime_health",
        "complete_workspace_bootstrap",
        "request_owner_approval",
        "wait_for_quiet_hours",
        "monitor_running_work",
        "wait_for_retry",
        "claim_next_work",
        "idle",
    }
    next_action = _coerce_text(decision.get("next_action"))
    if next_action not in expected_next_actions:
        raise RuntimeError(f"unexpected_next_action:{next_action or 'missing'}")
    return decision


def _heartbeat_snapshot_decision(
    *,
    tenant_id: str,
    workspace_id: str,
    queue_overview: Dict[str, Any],
    quiet_hours: Dict[str, Any],
    policy: Dict[str, Any],
    rust_kernel: Dict[str, Any],
    lane_queue: Dict[str, Any],
) -> Dict[str, Any]:
    from server_modules import rust_runtime_kernel_client

    recent = lane_queue.get("recent") if isinstance(lane_queue.get("recent"), list) else []
    failed_count = sum(
        1
        for entry in recent
        if isinstance(entry, dict)
        and _coerce_text(entry.get("status")).lower() in {"failed", "cancelled", "canceled"}
    )
    retry_status = bounded_scheduler_service.retry_queue_status(workspace_id)
    payload = {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "queue": {
            "queued_count": int(queue_overview.get("queued_count") or 0),
            "running_now_count": int(queue_overview.get("running_now_count") or 0),
            "blocked_on_approval_count": int(queue_overview.get("blocked_on_approval_count") or 0),
            "failed_count": int(failed_count),
        },
        "scheduler": {
            "retry_pending_count": int(retry_status.get("pending_count") or 0),
            "quiet_hours_active": bool(quiet_hours.get("active") or quiet_hours.get("quiet_hours_active")),
            "enabled": bool(policy) or bool(queue_overview.get("pending_wakeup_count")),
        },
        "session": {
            "status": "active",
            "turn_count": 0,
            "idle_seconds": 0,
        },
        "rust_kernel_available": bool((rust_kernel if isinstance(rust_kernel, dict) else {}).get("available")),
    }
    decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
        "heartbeat-snapshot",
        payload,
    )
    expected_next_actions = {
        "investigate",
        "request_owner_approval",
        "reset_session",
        "wait_for_quiet_hours",
        "monitor_running_work",
        "wait_for_retry",
        "claim_next_work",
        "idle",
    }
    next_action = _coerce_text(decision.get("next_action"))
    if next_action not in expected_next_actions:
        raise RuntimeError(f"unexpected_next_action:{next_action or 'missing'}")
    return decision


def _plugin_health_snapshot() -> Dict[str, Any]:
    try:
        from server_modules.plugin_system import get_global_hook_registry
        return get_global_hook_registry().snapshot()
    except Exception:
        return {"error": "plugin_system_unavailable"}


def _rust_kernel_health_snapshot() -> Dict[str, Any]:
    try:
        from server_modules import rust_runtime_kernel_client

        binary = rust_runtime_kernel_client.runtime_kernel_binary()
        return {
            "available": binary is not None,
            "binary_path": str(binary) if binary is not None else None,
            "commands": sorted(rust_runtime_kernel_client.ALLOWED_KERNEL_COMMANDS),
            "adapter": "server_modules.rust_runtime_kernel_client",
        }
    except Exception:
        return {
            "available": False,
            "error": "rust_runtime_kernel_status_unavailable",
            "adapter": "server_modules.rust_runtime_kernel_client",
        }
