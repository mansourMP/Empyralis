from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from server_modules import (
    billing_service,
    connectors_core,
    control_plane_repository,
    deployed_agent_service,
    gateway_approval_service,
    gateway_inventory_service,
    gateway_state_repository,
    personal_channels_service,
    personal_channels_repository,
    provider_catalog_service,
)
from server_modules.direct_tool_config_service import run_async_tool_call

PROVIDER_PROBE_TTL_SECONDS = 5 * 60
_PROVIDER_PROBE_CACHE: Dict[str, Dict[str, Any]] = {}


def _parse_iso(value: Any) -> Optional[datetime]:
    token = str(value or "").strip()
    if not token:
        return None
    try:
        parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_seconds(value: Any) -> Optional[int]:
    parsed = _parse_iso(value)
    if parsed is None:
        return None
    return max(int((datetime.now(timezone.utc) - parsed).total_seconds()), 0)


def _check_status(ok: bool, *, warn: bool = False) -> str:
    if ok:
        return "pass"
    return "warn" if warn else "fail"


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _text(value: Any, fallback: str = "") -> str:
    token = str(value or "").strip()
    return token or fallback


def _provider_probe_cache_key(workspace_id: str, provider_id: str) -> str:
    return f"{str(workspace_id or '').strip()}:{str(provider_id or '').strip().lower()}"


def _provider_probe_result(
    *,
    workspace_id: str,
    provider_id: str,
    force_provider_probe: bool,
) -> Dict[str, Any]:
    key = _provider_probe_cache_key(workspace_id, provider_id)
    now = datetime.now(timezone.utc)
    cached = _PROVIDER_PROBE_CACHE.get(key)
    if not force_provider_probe and isinstance(cached, dict):
        checked_at = _parse_iso(cached.get("checked_at"))
        if checked_at is not None and (now - checked_at).total_seconds() <= PROVIDER_PROBE_TTL_SECONDS:
            status = _text(cached.get("probe_result"), "unavailable")
            return {
                **cached,
                "probe_cached": True,
                "probe_result": "cached_fail" if status == "fail" else status,
            }
    try:
        result = run_async_tool_call(
            connectors_core.probe_provider(
                provider_id,
                workspace_id=workspace_id,
            )
        ) or {}
        ok = bool(result.get("ok"))
        payload = {
            "checked_at": now.isoformat().replace("+00:00", "Z"),
            "probe_result": "ok" if ok else "fail",
            "probe_detail": _text(result.get("message"), "Live probe complete."),
            "probe_model": _text(result.get("model")) or None,
            "probe_cached": False,
        }
    except Exception as exc:
        payload = {
            "checked_at": now.isoformat().replace("+00:00", "Z"),
            "probe_result": "fail",
            "probe_detail": str(exc).strip() or "Provider live probe failed.",
            "probe_model": None,
            "probe_cached": False,
        }
    _PROVIDER_PROBE_CACHE[key] = dict(payload)
    return payload


def _summarize_specialist_health(
    *,
    workspace_id: str,
    tenant_id: Optional[str],
    provider_states: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    try:
        records = run_async_tool_call(
            control_plane_repository.list_deployed_agents_for_workspace(
                workspace_id,
                tenant_id=tenant_id,
            )
        ) or []
    except Exception:
        records = []
    items: List[Dict[str, Any]] = []
    live_total = 0
    healthy_total = 0
    failing_total = 0
    required_provider_ids: set[str] = set()

    for raw in records:
        if not isinstance(raw, dict):
            continue
        projected = deployed_agent_service.project_deployed_agent(raw, include_internal=True) or {}
        config = _coerce_dict(projected.get("config"))
        operational_state = _coerce_dict(projected.get("operational_state"))
        deployment_state = _text(
            projected.get("deployment_state") or operational_state.get("deployment_state"),
            "draft",
        ).lower()
        channels_payload = _coerce_dict(config.get("channels"))
        enabled_channels = [
            channel_key
            for channel_key, channel_config in channels_payload.items()
            if isinstance(channel_config, dict) and bool(channel_config.get("enabled"))
        ]
        provider_id = _text(projected.get("provider") or config.get("provider")).lower()
        provider_entry = provider_states.get(provider_id, {}) if provider_id else {}
        provider_state = _text(provider_entry.get("state"), "unconfigured").lower() if provider_id else "not_required"
        if provider_id:
            required_provider_ids.add(provider_id)

        if deployment_state == "live":
            live_total += 1
            if not enabled_channels:
                status = "fail"
                summary = "Live specialist has no enabled business channel."
            elif provider_id and provider_state != "active":
                status = "fail"
                summary = f"Live specialist depends on {provider_id}, which is {provider_state}."
            else:
                status = "pass"
                summary = (
                    f"Live on {', '.join(enabled_channels)}."
                    if enabled_channels
                    else "Live and ready."
                )
        elif deployment_state == "paused":
            status = "warn"
            summary = "Specialist is paused."
        elif deployment_state == "staging":
            status = "warn"
            summary = "Specialist is staged but not live yet."
        else:
            status = "warn"
            summary = "Specialist setup is still in draft."

        if status == "pass":
            healthy_total += 1
        elif status == "fail":
            failing_total += 1

        items.append(
            {
                "deployed_agent_id": _text(projected.get("id")) or None,
                "name": _text(projected.get("name"), "Studio specialist"),
                "deployment_state": deployment_state,
                "status": status,
                "summary": summary,
                "provider": provider_id or None,
                "provider_state": provider_state if provider_id else None,
                "channels": enabled_channels,
                "model": _text(projected.get("model")) or None,
            }
        )

    if not items:
        status = "pass"
        summary = "No Studio specialists are deployed in this workspace yet."
    elif failing_total > 0:
        status = "fail"
        summary = f"{failing_total} specialist deployment(s) need attention."
    elif live_total == 0:
        status = "warn"
        summary = "Specialists exist, but none are live yet."
    else:
        status = "pass"
        summary = f"{healthy_total} live specialist deployment(s) are healthy."

    return {
        "status": status,
        "summary": summary,
        "count": len(items),
        "live_count": live_total,
        "healthy_count": healthy_total,
        "failing_count": failing_total,
        "items": items,
        "required_provider_ids": sorted(required_provider_ids),
    }


def _summarize_provider_health(
    *,
    workspace_id: str,
    required_provider_ids: List[str],
    force_provider_probe: bool = False,
) -> Dict[str, Any]:
    try:
        catalog = run_async_tool_call(
            provider_catalog_service.list_workspace_provider_catalog(workspace_id=workspace_id)
        ) or {}
    except Exception:
        catalog = {}
    provider_items = [
        item
        for item in list(catalog.get("providers") or [])
        if isinstance(item, dict)
    ]
    provider_by_id = {
        _text(item.get("id")).lower(): item
        for item in provider_items
        if _text(item.get("id"))
    }
    required_ids = [provider_id for provider_id in required_provider_ids if provider_id]
    required_items = []
    for provider_id in required_ids:
        item = dict(provider_by_id.get(provider_id) or {"id": provider_id, "label": provider_id, "state": "setup_required"})
        probe = _provider_probe_result(
            workspace_id=workspace_id,
            provider_id=provider_id,
            force_provider_probe=force_provider_probe,
        )
        item.update(probe)
        if _text(probe.get("probe_result")).lower() not in {"ok"}:
            item["state"] = "degraded" if _text(item.get("state")).lower() == "active" else _text(item.get("state"), "setup_required")
            item["state_detail"] = _text(probe.get("probe_detail"), _text(item.get("state_detail")))
        provider_by_id[provider_id] = item
        required_items.append(item)
    failing_items = [
        item for item in required_items
        if _text(item.get("state"), "setup_required").lower() != "active"
        or _text(item.get("probe_result"), "unavailable").lower() not in {"ok"}
    ]

    if not required_items:
        status = "pass"
        summary = "No live Studio specialist currently requires a hosted provider."
    elif failing_items:
        status = "fail"
        summary = f"{len(failing_items)} required provider connection(s) are not healthy."
    else:
        status = "pass"
        summary = f"{len(required_items)} required provider connection(s) are healthy."

    return {
        "status": status,
        "summary": summary,
        "count": len(required_items),
        "healthy_count": len(required_items) - len(failing_items),
        "failing_count": len(failing_items),
        "items": [
            {
                "id": _text(item.get("id")) or None,
                "label": _text(item.get("label"), _text(item.get("id"), "Provider")),
                "state": _text(item.get("state"), "setup_required").lower(),
                "state_detail": _text(item.get("state_detail")) or None,
                "checked_at": _text(item.get("checked_at")) or None,
                "probe_result": _text(item.get("probe_result"), "unavailable"),
                "probe_detail": _text(item.get("probe_detail")) or None,
                "probe_model": _text(item.get("probe_model")) or None,
                "probe_cached": bool(item.get("probe_cached")),
            }
            for item in required_items
        ],
        "provider_states": provider_by_id,
    }


def _summarize_quota_health(
    *,
    workspace_id: str,
    workspace: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    try:
        summary = billing_service.workspace_billing_summary_for_workspace_id(
            workspace_id,
            workspace=workspace,
        )
    except Exception:
        return {
            "status": "warn",
            "summary": "Workspace quota status is unavailable right now.",
            "plan_id": "free",
            "limits": {},
            "usage": {},
        }
    limits = _coerce_dict(summary.get("limits"))
    usage = _coerce_dict(summary.get("usage"))
    plan_id = _text(_coerce_dict(summary.get("subscription")).get("effective_plan_id"), "free")
    issues: List[str] = []

    specialists_in_use = int(usage.get("specialists_in_use") or 0)
    max_specialists = int(limits.get("max_specialists") or 0)
    hosted_minutes = float(usage.get("hosted_runtime_minutes_monthly") or 0.0)
    hosted_limit = float(limits.get("hosted_runtime_minutes_monthly") or 0.0)
    hosted_ai_enabled = bool(limits.get("hosted_ai_enabled"))

    if max_specialists > 0 and specialists_in_use > max_specialists:
        issues.append("specialist limit exceeded")
    if hosted_ai_enabled and hosted_limit > 0 and hosted_minutes > hosted_limit:
        issues.append("hosted runtime limit exceeded")

    if issues:
        status = "fail"
        summary_text = ", ".join(issues).capitalize() + "."
    else:
        status = "pass"
        summary_text = "Workspace billing and quota limits are within plan."

    return {
        "status": status,
        "summary": summary_text,
        "plan_id": plan_id,
        "limits": limits,
        "usage": usage,
    }


def _summarize_personal_channel_health(gateway_id: str) -> Dict[str, Any]:
    try:
        payload = personal_channels_service.get_gateway_personal_channel_surfaces(gateway_id)
    except Exception as exc:
        return {
            "status": "warn",
            "summary": f"Personal channel readiness is unavailable: {str(exc).strip() or 'unknown error'}.",
            "count": 0,
            "connected_count": 0,
            "running_count": 0,
            "items": [],
        }

    items = [
        dict(item)
        for item in list(payload.get("items") or [])
        if isinstance(item, dict)
    ]
    connected_items = [item for item in items if bool(item.get("connected"))]
    running_items = [item for item in items if bool(item.get("running"))]
    issue_items = [
        item for item in items
        if bool(item.get("live_capable"))
        and not bool(item.get("connected"))
        and _text(item.get("status")).lower() in {"unavailable", "error", "failed", "fail"}
    ]

    if not items:
        status = "warn"
        summary = "No personal messaging channel manifests are available from Agent Computer."
    elif issue_items:
        status = "warn"
        summary = f"{len(issue_items)} personal messaging channel(s) report bridge errors."
    elif connected_items:
        status = "pass"
        summary = f"{len(connected_items)} personal messaging channel(s) connected on Agent Computer."
    else:
        status = "warn"
        summary = "Personal messaging channels are available, but none are connected yet."

    return {
        "status": status,
        "summary": summary,
        "count": len(items),
        "connected_count": len(connected_items),
        "running_count": len(running_items),
        "items": items,
    }


def gateway_doctor_payload(gateway_id: str, *, force_provider_probe: bool = False, trace_id: Optional[str] = None) -> Dict[str, Any]:
    registration = gateway_state_repository.get_gateway_registration(gateway_id)
    if not registration:
        raise ValueError("Gateway registration was not found.")
    workspace_id = _text(registration.get("workspace_id"), "default")
    try:
        workspace = run_async_tool_call(control_plane_repository.get_workspace_by_id(workspace_id)) or {}
    except Exception:
        workspace = {}
    if not isinstance(workspace, dict):
        workspace = {}
    if not _text(workspace.get("workspace_id")):
        workspace["workspace_id"] = workspace_id
    tenant_id = _text(workspace.get("tenant_id")) or None
    latest_session = gateway_state_repository.get_latest_gateway_session(gateway_id)
    approvals = gateway_approval_service.list_gateway_tool_approvals(gateway_id=gateway_id, limit=50)
    browser_sessions = gateway_state_repository.list_gateway_browser_sessions(gateway_id, limit=10)
    whatsapp_state = personal_channels_repository.get_whatsapp_state(
        gateway_id,
        channel_key="whatsapp_personal",
    )
    telegram_state = personal_channels_repository.get_telegram_state(
        gateway_id,
        channel_key="telegram_personal",
    )
    recent_events = gateway_state_repository.list_gateway_events(gateway_id, limit=25)

    heartbeat_age = _age_seconds((latest_session or {}).get("last_heartbeat_at") if latest_session else registration.get("last_heartbeat_at"))
    session_status = str((latest_session or {}).get("status") or "").strip().lower()
    session_metadata = dict((latest_session or {}).get("metadata") or {})
    registration_metadata = dict(registration.get("metadata") or {})
    reported_health = str(session_metadata.get("health_state") or registration_metadata.get("health_state") or "").strip().lower()
    session_connected = session_status == "connected"
    heartbeat_fresh = heartbeat_age is not None and heartbeat_age <= 45
    service_inventory = gateway_inventory_service.service_inventory_payload(
        session_metadata=session_metadata,
        registration_metadata=registration_metadata,
        heartbeat_fresh=heartbeat_fresh,
    )
    registration_active = str(registration.get("status") or "").strip().lower() == "active"
    device_verified = str(registration.get("device_trust_state") or "").strip().lower() not in {"revoked", ""}
    checkpoint_drift = max(
        int(registration.get("journal_cursor") or 0) - int((latest_session or {}).get("last_ack") or 0),
        0,
    )
    resume_ready = registration_active and device_verified and int(registration.get("checkpoint_cursor") or 0) >= 0

    if not registration_active or not device_verified:
        overall_status = "blocked"
    elif not session_connected:
        overall_status = "offline"
    elif reported_health == "degraded" or not heartbeat_fresh:
        overall_status = "degraded"
    else:
        overall_status = "healthy"

    checks: List[Dict[str, Any]] = [
        {
            "id": "registration_active",
            "status": _check_status(registration_active),
            "summary": "Gateway registration is active." if registration_active else "Gateway registration is not active.",
        },
        {
            "id": "device_trust",
            "status": _check_status(device_verified),
            "summary": "Gateway device trust is valid." if device_verified else "Gateway device trust was revoked.",
        },
        {
            "id": "live_session",
            "status": _check_status(session_connected, warn=registration_active),
            "summary": "Gateway websocket session is connected." if session_connected else "Gateway websocket session is offline.",
        },
        {
            "id": "heartbeat_fresh",
            "status": _check_status(bool(heartbeat_fresh), warn=session_connected),
            "summary": "Gateway heartbeat is fresh." if heartbeat_fresh else "Gateway heartbeat is stale or missing.",
            "age_seconds": heartbeat_age,
        },
        {
            "id": "checkpoint_resume",
            "status": _check_status(resume_ready, warn=registration_active),
            "summary": "Checkpoint state is resumable." if resume_ready else "Checkpoint state is not ready for resume.",
            "drift": checkpoint_drift,
        },
        {
            "id": "passive_service_inventory",
            "status": _text(service_inventory.get("status"), "warn"),
            "summary": _text(
                service_inventory.get("summary"),
                "Passive service inventory is unavailable.",
            ),
            "count": int(service_inventory.get("count") or 0),
        },
        {
            "id": "pending_approvals",
            "status": "warn" if int(approvals.get("pending_count") or 0) > 0 else "pass",
            "summary": (
                f"{approvals.get('pending_count')} gateway approval(s) waiting."
                if int(approvals.get("pending_count") or 0) > 0
                else "No pending gateway approvals."
            ),
        },
    ]
    if whatsapp_state is not None:
        whatsapp_status = str(whatsapp_state.get("status") or "").strip().lower()
        checks.append(
            {
                "id": "whatsapp_personal",
                "status": "pass" if whatsapp_status == "connected" else "warn",
                "summary": (
                    "WhatsApp personal session is connected."
                    if whatsapp_status == "connected"
                    else f"WhatsApp personal session is {whatsapp_status or 'unknown'}."
                ),
            }
        )
    if telegram_state is not None:
        telegram_status = str(telegram_state.get("status") or "").strip().lower()
        checks.append(
            {
                "id": "telegram_personal",
                "status": "pass" if telegram_status == "connected" else "warn",
                "summary": (
                    "Telegram personal session is connected."
                    if telegram_status == "connected"
                    else f"Telegram personal session is {telegram_status or 'unknown'}."
                ),
            }
        )
    personal_channel_health = _summarize_personal_channel_health(gateway_id)
    checks.append(
        {
            "id": "personal_messaging_channels",
            "status": _text(personal_channel_health.get("status"), "warn"),
            "summary": _text(
                personal_channel_health.get("summary"),
                "Personal messaging channel readiness is unavailable.",
            ),
            "connected_count": int(personal_channel_health.get("connected_count") or 0),
            "running_count": int(personal_channel_health.get("running_count") or 0),
            "count": int(personal_channel_health.get("count") or 0),
        }
    )
    active_browser_sessions = [
        item
        for item in browser_sessions
        if str(item.get("status") or "").strip().lower() in {"active", "attached", "waiting_for_input", "fallback_ready"}
    ]
    attach_browser_sessions = [
        item
        for item in browser_sessions
        if str((item.get("metadata") or {}).get("browser_session_mode") or "").strip().lower() == "existing_session_attach"
    ]
    attach_ready_sessions = [
        item for item in attach_browser_sessions if str(item.get("status") or "").strip().lower() == "attached"
    ]
    attach_pending_sessions = [
        item for item in attach_browser_sessions if str(item.get("status") or "").strip().lower() in {"attach_required", "not_attached"}
    ]
    attach_failed_sessions = [
        item for item in attach_browser_sessions if str(item.get("status") or "").strip().lower() == "attach_failed"
    ]
    attach_approval_required_sessions = [
        item
        for item in attach_browser_sessions
        if bool(item.get("reviewed_approval_required") or (item.get("metadata") or {}).get("browser_reviewed_approval_required"))
    ]
    if browser_sessions:
        checks.append(
            {
                "id": "browser_sessions",
                "status": "pass" if active_browser_sessions else "warn",
                "summary": (
                    f"{len(active_browser_sessions)} gateway browser session(s) available."
                    if active_browser_sessions
                    else "No active gateway browser sessions are currently available."
                ),
            }
        )
    if attach_browser_sessions:
        checks.append(
            {
                "id": "browser_attach",
                "status": "fail" if attach_failed_sessions else ("pass" if attach_ready_sessions else "warn"),
                "summary": (
                    f"{len(attach_ready_sessions)} existing-session browser attach session(s) are attached."
                    if attach_ready_sessions
                    else f"{len(attach_failed_sessions)} existing-session browser attach session(s) failed to attach."
                    if attach_failed_sessions
                    else "Existing-session browser attach is configured but not currently attached."
                ),
            }
        )

    provider_health = _summarize_provider_health(
        workspace_id=workspace_id,
        required_provider_ids=[],
        force_provider_probe=force_provider_probe,
    )
    specialist_health = _summarize_specialist_health(
        workspace_id=workspace_id,
        tenant_id=tenant_id,
        provider_states=_coerce_dict(provider_health.get("provider_states")),
    )
    provider_health = _summarize_provider_health(
        workspace_id=workspace_id,
        required_provider_ids=list(specialist_health.get("required_provider_ids") or []),
        force_provider_probe=force_provider_probe,
    )
    specialist_health = _summarize_specialist_health(
        workspace_id=workspace_id,
        tenant_id=tenant_id,
        provider_states=_coerce_dict(provider_health.get("provider_states")),
    )
    quota_health = _summarize_quota_health(
        workspace_id=workspace_id,
        workspace=workspace if isinstance(workspace, dict) else None,
    )

    checks.extend(
        [
            {
                "id": "studio_specialists",
                "status": _text(specialist_health.get("status"), "warn"),
                "summary": _text(specialist_health.get("summary"), "Specialist readiness is unavailable."),
            },
            {
                "id": "provider_reachability",
                "status": _text(provider_health.get("status"), "warn"),
                "summary": _text(provider_health.get("summary"), "Provider reachability is unavailable."),
            },
            {
                "id": "workspace_quota",
                "status": _text(quota_health.get("status"), "warn"),
                "summary": _text(quota_health.get("summary"), "Workspace quota status is unavailable."),
            },
        ]
    )

    # P1 health checks
    outbox_summary = gateway_state_repository.summarize_gateway_outbox(gateway_id)
    outbox_pending = int(outbox_summary.get("pending") or 0)
    outbox_uncertain = int(outbox_summary.get("uncertain") or 0)
    outbox_abandoned = int(outbox_summary.get("abandoned") or 0)

    checks.append({
        "id": "outbox_backlog",
        "status": "fail" if outbox_pending > 200 else ("warn" if outbox_pending > 50 else "pass"),
        "summary": f"{outbox_pending} pending outbox item(s).",
        "pending_count": outbox_pending,
    })
    checks.append({
        "id": "uncertain_outbox",
        "status": "fail" if outbox_uncertain > 50 else ("warn" if outbox_uncertain > 10 else "pass"),
        "summary": f"{outbox_uncertain} uncertain outbox item(s).",
        "uncertain_count": outbox_uncertain,
    })
    checks.append({
        "id": "abandoned_outbox",
        "status": "fail" if outbox_abandoned > 20 else ("warn" if outbox_abandoned > 5 else "pass"),
        "summary": f"{outbox_abandoned} abandoned outbox item(s).",
        "abandoned_count": outbox_abandoned,
    })

    # Database health check
    db_ok = False
    db_error = None
    try:
        _ = gateway_state_repository.get_gateway_registration(gateway_id)
        db_ok = True
    except Exception as exc:
        db_error = str(exc)
    checks.append({
        "id": "database_health",
        "status": "pass" if db_ok else "fail",
        "summary": "Gateway state database is reachable." if db_ok else f"Gateway state database check failed: {db_error}",
    })

    # Stale websocket check
    stale_ws = heartbeat_age is not None and heartbeat_age > (15 * 60)  # 15 minutes
    checks.append({
        "id": "stale_websocket",
        "status": "fail" if stale_ws else ("warn" if heartbeat_age is not None and heartbeat_age > (5 * 60) else "pass"),
        "summary": f"WebSocket heartbeat age: {heartbeat_age}s." if heartbeat_age is not None else "No heartbeat data.",
        "heartbeat_age_seconds": heartbeat_age,
    })

    # Session sweep — mark stale sessions as expired
    swept = gateway_state_repository.sweep_stale_gateway_sessions(gateway_id)
    if swept > 0:
        checks.append({
            "id": "session_sweep",
            "status": "warn",
            "summary": f"{swept} stale session(s) swept.",
        })

    if overall_status not in {"blocked", "offline"}:
        if any(_text(item.get("status")).lower() == "fail" for item in checks):
            overall_status = "degraded"

    return {
        "gateway_id": str(gateway_id or "").strip(),
        "workspace_id": workspace_id,
        "status": overall_status,
        "registration": registration,
        "latest_session": latest_session,
        "checks": checks,
        "approvals": {
            "pending_count": int(approvals.get("pending_count") or 0),
            "retryable_count": int(approvals.get("retryable_count") or 0),
        },
        "checkpoint": {
            "status": "pass" if resume_ready and checkpoint_drift == 0 else ("warn" if resume_ready else "fail"),
            "summary": (
                "Gateway checkpoint is aligned and resumable."
                if resume_ready and checkpoint_drift == 0
                else "Gateway checkpoint can resume, but it is carrying drift."
                if resume_ready
                else "Gateway checkpoint cannot resume cleanly right now."
            ),
            "journal_cursor": int(registration.get("journal_cursor") or 0),
            "checkpoint_cursor": int(registration.get("checkpoint_cursor") or 0),
            "last_session_seq": int((latest_session or {}).get("last_seq") or 0),
            "last_session_ack": int((latest_session or {}).get("last_ack") or 0),
            "resume_ready": resume_ready,
            "drift": checkpoint_drift,
        },
        "whatsapp_personal": whatsapp_state,
        "telegram_personal": telegram_state,
        "personal_channels": personal_channel_health,
        "service_inventory": service_inventory,
        "browser": {
            "status": "pass" if active_browser_sessions else ("warn" if browser_sessions else "pass"),
            "summary": (
                f"{len(active_browser_sessions)} gateway browser session(s) are active."
                if active_browser_sessions
                else "Gateway browser sessions exist but none are active right now."
                if browser_sessions
                else "No gateway browser sessions are active."
            ),
            "count": len(browser_sessions),
            "active_count": len(active_browser_sessions),
            "items": browser_sessions,
        },
        "browser_attach": {
            "status": "fail" if attach_failed_sessions else ("pass" if attach_ready_sessions else ("warn" if attach_browser_sessions else "pass")),
            "summary": (
                f"{len(attach_ready_sessions)} existing-session browser attach session(s) are attached."
                if attach_ready_sessions
                else f"{len(attach_failed_sessions)} existing-session browser attach session(s) failed to attach."
                if attach_failed_sessions
                else "Existing-session browser attach is configured but not currently attached."
                if attach_browser_sessions
                else "No existing-session browser attach sessions are active."
            ),
            "count": len(attach_browser_sessions),
            "attached_count": len(attach_ready_sessions),
            "pending_count": len(attach_pending_sessions),
            "failed_count": len(attach_failed_sessions),
            "approval_required_count": len(attach_approval_required_sessions),
            "items": attach_browser_sessions,
        },
        "specialists": {
            key: value
            for key, value in specialist_health.items()
            if key != "required_provider_ids"
        },
        "providers": {
            key: value
            for key, value in provider_health.items()
            if key != "provider_states"
        },
        "quota": quota_health,
        "journal": {
            "count": len(recent_events),
            "recent": recent_events,
        },
    }
