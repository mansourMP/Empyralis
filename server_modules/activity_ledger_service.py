from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from server_modules import control_plane_repository
from server_modules import entitlements_service
from server_modules import secret_redaction_service


DETAIL_LEVELS = {"feed_summary", "timeline_detail", "audit_reference"}
EVENT_CLASSES = {
    "sage_activity",
    "specialist_activity",
    "application_activity",
    "delegation",
    "artifact_created",
    "artifact_updated",
    "approval",
    "blocked_action",
    "memory_update",
    "run_status",
    "system_activity",
    "gateway_channel",
    "gateway_hardware",
    "fleet_control",
    "platform_integrity",
    "mandate_blocked",
}


# Rust kernel gate removed — the kernel is never compiled/running.
# Activity ledger writes directly; structural safety is enforced via
# secret redaction and payload validation in append_activity_event.

_PUBLIC_TIMELINE_FORBIDDEN_KEYS = frozenset(
    {
        "raw_chain_of_thought",
        "raw_cot",
        "chain_of_thought",
        "internal_reasoning",
        "model_internals",
        "reasoning_trace",
        "scratchpad",
        "logprobs",
    }
)


def _run_coro_sync(coro: Any) -> Any:
    try:
        return asyncio.run(coro)
    except RuntimeError as exc:
        if "asyncio.run() cannot be called from a running event loop" not in str(exc):
            raise

    result: Dict[str, Any] = {}
    failure: Dict[str, BaseException] = {}

    def _runner() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as err:  # pragma: no cover
            failure["error"] = err

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    if "error" in failure:
        raise failure["error"]
    return result.get("value")


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _strip_forbidden_timeline_fields(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: Dict[str, Any] = {}
        for item_key, item_value in value.items():
            key = str(item_key or "")
            if key.strip().lower() in _PUBLIC_TIMELINE_FORBIDDEN_KEYS:
                continue
            sanitized[key] = _strip_forbidden_timeline_fields(item_value)
        return sanitized
    if isinstance(value, list):
        return [_strip_forbidden_timeline_fields(item) for item in value]
    return value


def _public_timeline_dict(value: Any) -> Dict[str, Any]:
    sanitized = secret_redaction_service.sanitize_mapping(_coerce_dict(value))
    cleaned = _strip_forbidden_timeline_fields(sanitized)
    return cleaned if isinstance(cleaned, dict) else {}


def _coerce_artifacts(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, dict):
        items = [value]
    elif isinstance(value, list):
        items = value
    else:
        items = []
    out: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("label") or item.get("title") or "").strip() or None
        preview_path = str(item.get("preview_path") or item.get("preview_url") or "").strip() or None
        record = {
            "id": str(item.get("id") or "").strip() or None,
            "name": name,
            "label": name,
            "kind": str(item.get("kind") or item.get("type") or "").strip() or None,
            "path": str(item.get("path") or item.get("uri") or "").strip() or None,
            "preview_path": preview_path,
            "preview_url": preview_path,
            "review_required": bool(item.get("review_required")),
        }
        out.append({key: value for key, value in record.items() if value is not None})
    return out


def _normalize_detail_level(value: Any, *, default: str = "timeline_detail") -> str:
    token = str(value or "").strip().lower() or default
    return token if token in DETAIL_LEVELS else default


def _normalize_event_class(value: Any, *, default: str = "system_activity") -> str:
    token = str(value or "").strip().lower() or default
    return token if token in EVENT_CLASSES else default


def _compact_text(value: Any, *, limit: int = 220) -> str:
    text = " ".join(str(value or "").strip().split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _iso_ts(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    token = str(value or "").strip()
    return token or None


def _utc_now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _payload_timestamp_seconds(payload: Dict[str, Any], *keys: str) -> Optional[float]:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, datetime):
            normalized = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
            return normalized.timestamp()
        token = str(value or "").strip()
        if not token:
            continue
        normalized = token[:-1] + "+00:00" if token.endswith("Z") else token
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    return None


def _workspace_history_cutoff_ts(workspace_id: str) -> Optional[float]:
    token = str(workspace_id or "default").strip() or "default"
    return entitlements_service.history_window_cutoff_ts_for_workspace_id(
        workspace_id=token,
        now_ts=_utc_now_ts(),
    )


def _row_within_workspace_history_window(row: Dict[str, Any], *, workspace_id: str) -> bool:
    cutoff_ts = _workspace_history_cutoff_ts(workspace_id)
    event_ts = _payload_timestamp_seconds(row, "created_at", "updated_at")
    return cutoff_ts is None or event_ts is None or event_ts >= cutoff_ts


def _row_metadata(row: Dict[str, Any]) -> Dict[str, Any]:
    metadata = _coerce_dict(row.get("metadata"))
    metadata.setdefault("detail_level", str(row.get("detail_level") or "feed_summary"))
    metadata.setdefault("actor_type", str(row.get("actor_type") or "system"))
    metadata.setdefault("actor_id", str(row.get("actor_id") or "system"))
    if str(row.get("install_id") or "").strip():
        metadata.setdefault("install_id", str(row.get("install_id") or "").strip())
    if str(row.get("app_id") or "").strip():
        metadata.setdefault("app_id", str(row.get("app_id") or "").strip())
    if str(row.get("thread_id") or "").strip():
        metadata.setdefault("thread_id", str(row.get("thread_id") or "").strip())
    if str(row.get("status") or "").strip():
        metadata.setdefault("status", str(row.get("status") or "").strip())
    metadata["review_required"] = bool(row.get("review_required"))
    metadata["artifacts"] = _coerce_artifacts(row.get("artifacts"))
    return metadata


def _notification_session_key(row: Dict[str, Any]) -> str:
    session_key = str(row.get("session_key") or "").strip()
    if session_key:
        return session_key
    run_id = str(row.get("run_id") or "").strip()
    if run_id:
        return f"run:{run_id}"
    actor_type = str(row.get("actor_type") or "activity").strip()
    actor_id = str(row.get("actor_id") or "activity").strip()
    return f"activity:{actor_type}:{actor_id}"


def _project_notification_item(row: Dict[str, Any]) -> Dict[str, Any]:
    action = str(row.get("action") or row.get("event_class") or "notification").strip() or "notification"
    title = str(row.get("title") or "").strip() or action.replace("_", " ").title()
    return {
        "id": str(row.get("id") or "").strip() or None,
        "ts": _iso_ts(row.get("created_at")),
        "channel": str(row.get("channel") or "activity").strip() or "activity",
        "direction": str(row.get("direction") or "system").strip() or "system",
        "event_type": str(row.get("event_class") or "").strip() or None,
        "workspace_id": str(row.get("workspace_id") or "").strip() or None,
        "session_key": _notification_session_key(row),
        "session_id": _notification_session_key(row),
        "run_id": str(row.get("run_id") or "").strip() or None,
        "trace_id": str(row.get("trace_id") or "").strip() or None,
        "action": action,
        "title": title,
        "text": str(row.get("summary") or "").strip() or title,
        "metadata": _row_metadata(row),
    }


def _project_timeline_item(row: Dict[str, Any]) -> Dict[str, Any]:
    ts = _iso_ts(row.get("created_at"))
    actor_type = str(row.get("actor_type") or "system").strip() or "system"
    actor_id = str(row.get("actor_id") or "system").strip() or "system"
    install_id = str(row.get("install_id") or "").strip() or None
    app_id = str(row.get("app_id") or "").strip() or None
    metadata = _coerce_dict(row.get("metadata"))
    payload = _coerce_dict(row.get("payload"))
    public_metadata = _public_timeline_dict(metadata)
    public_payload = _public_timeline_dict(payload)
    visible_activity = _project_visible_activity(
        row=row,
        metadata=metadata,
        payload=payload,
    )
    admin_audit = _project_admin_audit(
        row=row,
        metadata=metadata,
        payload=payload,
    )
    return {
        "id": str(row.get("id") or "").strip() or None,
        "ts": ts,
        "created_at": ts,
        "workspace_id": str(row.get("workspace_id") or "").strip() or None,
        "event_class": str(row.get("event_class") or "").strip() or None,
        "detail_level": str(row.get("detail_level") or "").strip() or None,
        "status": str(row.get("status") or "").strip() or None,
        "action": str(row.get("action") or "").strip() or None,
        "channel": str(row.get("channel") or "").strip() or None,
        "direction": str(row.get("direction") or "").strip() or None,
        "session_key": str(row.get("session_key") or "").strip() or None,
        "run_id": str(row.get("run_id") or "").strip() or None,
        "thread_id": str(row.get("thread_id") or "").strip() or None,
        "trace_id": str(row.get("trace_id") or "").strip() or None,
        "title": str(row.get("title") or "").strip() or None,
        "summary": str(row.get("summary") or "").strip() or None,
        "review_required": bool(row.get("review_required")),
        "actor_type": actor_type,
        "actor_id": actor_id,
        "install_id": install_id,
        "app_id": app_id,
        "actor": {
            "type": actor_type,
            "id": actor_id,
            "install_id": install_id,
            "app_id": app_id,
        },
        "artifacts": _coerce_artifacts(row.get("artifacts")),
        "metadata": public_metadata,
        "payload": public_payload,
        "visible_activity": visible_activity,
        "admin_audit": admin_audit,
    }


def _read_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not parsed or parsed < 0:
        return 0.0
    return round(parsed, 6)


def _read_int(value: Any) -> Optional[int]:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed < 0:
        return 0
    return parsed


def _normalize_public_tier(value: Any) -> Optional[str]:
    token = str(value or "").strip().lower().replace("-", "_")
    return token or None


def _title_case_tier(value: Optional[str]) -> Optional[str]:
    token = str(value or "").strip()
    if not token:
        return None
    return token.replace("_", " ").title()


def _project_visible_activity(
    *,
    row: Dict[str, Any],
    metadata: Dict[str, Any],
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    public_tier = (
        _normalize_public_tier(metadata.get("public_tier"))
        or _normalize_public_tier(metadata.get("model_tier"))
        or _normalize_public_tier(metadata.get("empyralis_model_tier"))
    )
    credit_quantity = _read_float(metadata.get("credit_quantity"))
    credit_multiplier = _read_float(metadata.get("credit_multiplier"))
    used_credits = 0.0
    if credit_quantity is not None and credit_multiplier is not None and credit_multiplier > 0:
        used_credits = round(float(credit_quantity) * float(credit_multiplier), 3)
    runtime_minutes = 0.0
    if str(metadata.get("credit_item_type") or "").strip().lower() == "virtual_browser_minutes":
        runtime_minutes = _read_float(metadata.get("credit_quantity")) or 0.0
    review_required = bool(row.get("review_required") or metadata.get("review_required"))
    payment_related = (
        str(row.get("action") or "").strip().lower().startswith("payment")
        or str(row.get("event_class") or "").strip().lower().startswith("payment")
        or "payment" in str(row.get("title") or "").strip().lower()
        or "payment" in str(row.get("summary") or "").strip().lower()
    )
    return {
        "sage_tier": _title_case_tier(public_tier),
        "used_credits": used_credits if used_credits > 0 else None,
        "virtual_browser_minutes": runtime_minutes if runtime_minutes > 0 else None,
        "owner_approval_required_for_payment": bool(review_required and payment_related),
        "owner_approval_required": review_required,
    }


def _project_admin_audit(
    *,
    row: Dict[str, Any],
    metadata: Dict[str, Any],
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    usage_accounting = _coerce_dict(metadata.get("usage_accounting")) or _coerce_dict(payload.get("usage_accounting"))
    duration_seconds = _read_float(
        metadata.get("runtime_duration_seconds")
        or payload.get("runtime_duration_seconds")
        or metadata.get("duration_seconds")
        or payload.get("duration_seconds")
    )
    if duration_seconds is None:
        duration_ms = _read_float(metadata.get("duration_ms") or payload.get("duration_ms"))
        duration_seconds = round((duration_ms or 0.0) / 1000.0, 6) if duration_ms is not None else None
    prompt_tokens = _read_int(
        usage_accounting.get("input_tokens")
        or usage_accounting.get("prompt_tokens")
        or metadata.get("prompt_tokens")
        or payload.get("prompt_tokens")
    )
    completion_tokens = _read_int(
        usage_accounting.get("output_tokens")
        or usage_accounting.get("completion_tokens")
        or metadata.get("completion_tokens")
        or payload.get("completion_tokens")
    )
    total_tokens = _read_int(
        usage_accounting.get("total_tokens")
        or metadata.get("total_tokens")
        or payload.get("total_tokens")
    )
    ledger_item_ids: List[str] = []
    row_id = str(row.get("id") or "").strip()
    if row_id:
        ledger_item_ids.append(row_id)
    extra_ledger_ids = metadata.get("ledger_item_ids") or payload.get("ledger_item_ids")
    if isinstance(extra_ledger_ids, list):
        for value in extra_ledger_ids:
            token = str(value or "").strip()
            if token and token not in ledger_item_ids:
                ledger_item_ids.append(token)
    return {
        "raw_provider": str(metadata.get("effective_provider") or metadata.get("requested_provider") or row.get("channel") or "").strip() or None,
        "raw_model": str(metadata.get("effective_model") or metadata.get("requested_model") or "").strip() or None,
        "fallback_provider": str(metadata.get("fallback_provider") or payload.get("fallback_provider") or "").strip() or None,
        "fallback_model": str(metadata.get("fallback_model") or payload.get("fallback_model") or "").strip() or None,
        "token_usage": {
            "prompt_tokens": prompt_tokens or 0,
            "completion_tokens": completion_tokens or 0,
            "total_tokens": total_tokens or 0,
        },
        "runtime_duration_seconds": duration_seconds,
        "ledger_item_ids": ledger_item_ids,
    }


async def append_activity_event(
    *,
    tenant_id: str,
    workspace_id: str,
    actor_type: str,
    actor_id: str,
    event_class: str,
    detail_level: str = "timeline_detail",
    install_id: Optional[str] = None,
    app_id: Optional[str] = None,
    run_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    session_key: Optional[str] = None,
    channel: Optional[str] = None,
    direction: Optional[str] = None,
    action: Optional[str] = None,
    trace_id: Optional[str] = None,
    title: str = "",
    summary: str = "",
    status: str = "logged",
    review_required: bool = False,
    artifacts: Optional[List[Dict[str, Any]]] = None,
    payload: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    event_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    sanitized_artifacts = secret_redaction_service.sanitize_value(_coerce_artifacts(artifacts))
    sanitized_payload = secret_redaction_service.sanitize_mapping(payload)
    sanitized_metadata = secret_redaction_service.sanitize_mapping(metadata)
    sanitized_record = {
        "title": secret_redaction_service.redact_text(title),
        "summary": secret_redaction_service.redact_text(summary),
        "artifacts": sanitized_artifacts,
        "payload": sanitized_payload,
        "metadata": sanitized_metadata,
    }
    secret_redaction_service.assert_secrets_free(sanitized_record, context="activity_ledger_event")
    normalized_actor_id = str(actor_id or "").strip() or str(actor_type or "system").strip().lower() or "system"
    return await control_plane_repository.append_activity_ledger_event(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor_type=str(actor_type or "").strip().lower() or "system",
        actor_id=normalized_actor_id,
        event_class=_normalize_event_class(event_class),
        detail_level=_normalize_detail_level(detail_level),
        install_id=str(install_id or "").strip() or None,
        app_id=str(app_id or "").strip() or None,
        run_id=str(run_id or "").strip() or None,
        thread_id=str(thread_id or "").strip() or None,
        session_key=str(session_key or "").strip() or None,
        channel=str(channel or "").strip().lower() or None,
        direction=str(direction or "").strip().lower() or None,
        action=str(action or "").strip().lower() or None,
        trace_id=str(trace_id or "").strip() or None,
        title=_compact_text(sanitized_record["title"], limit=140),
        summary=_compact_text(sanitized_record["summary"], limit=320),
        status=str(status or "logged").strip().lower() or "logged",
        review_required=bool(review_required),
        artifacts=sanitized_artifacts if isinstance(sanitized_artifacts, list) else [],
        payload=sanitized_payload,
        metadata=sanitized_metadata,
        event_id=event_id,
    )


async def append_execution_activity(
    *,
    tenant_id: str,
    workspace_id: str,
    agent_id: str,
    tool: str,
    args_summary: Any,
    result_status: str,
    execution_tier: str,
    run_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    channel: Optional[str] = None,
    direction: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    payload = {
        "agent_id": str(agent_id or "").strip() or "agent",
        "tool": str(tool or "").strip() or "unknown",
        "args_summary": args_summary,
        "result_status": str(result_status or "").strip().lower() or "logged",
        "ts": _utc_now_iso(),
        "execution_tier": str(execution_tier or "").strip().lower() or "unknown",
    }
    return await append_activity_event(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor_type="agent",
        actor_id=payload["agent_id"],
        event_class="sage_activity",
        detail_level="timeline_detail",
        run_id=run_id,
        thread_id=thread_id,
        trace_id=trace_id,
        channel=channel,
        direction=direction,
        action="tool_execution",
        title=f"{payload['tool']} {payload['result_status']}",
        summary=f"{payload['tool']} {payload['result_status']} on {payload['execution_tier']}",
        status=payload["result_status"],
        review_required=False,
        payload=payload,
        metadata=metadata,
    )


def record_notification_activity(*, event: Any, notification: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    payload = _coerce_dict(getattr(event, "payload", {}))
    metadata = _coerce_dict(notification.get("metadata"))
    action = str(notification.get("action") or "").strip().lower()
    event_class = _normalize_event_class(
        metadata.get("activity_event_class")
        or payload.get("activity_event_class")
        or (
            "approval"
            if action == "approval_requested"
            else "blocked_action"
            if action in {"run_failed", "machine_revoked", "machine_enrollment_failed"}
            else "run_status"
            if action == "run_completed"
            else "system_activity"
        )
    )
    actor_type = str(
        metadata.get("activity_actor_type")
        or payload.get("activity_actor_type")
        or ("system" if event_class in {"blocked_action", "run_status", "system_activity"} else "application")
    ).strip().lower() or "system"
    actor_id = str(
        metadata.get("activity_actor_id")
        or payload.get("activity_actor_id")
        or metadata.get("agent_install_id")
        or payload.get("agent_install_id")
        or getattr(event, "run_id", None)
        or actor_type
    ).strip() or actor_type
    artifacts = metadata.get("artifacts") or payload.get("artifacts")
    return _run_coro_sync(
        append_activity_event(
            tenant_id=str(notification.get("tenant_id") or getattr(event, "tenant_id", "")).strip() or "default",
            workspace_id=str(notification.get("workspace_id") or getattr(event, "workspace_id", "")).strip() or "default",
            actor_type=actor_type,
            actor_id=actor_id,
            install_id=str(metadata.get("agent_install_id") or payload.get("agent_install_id") or "").strip() or None,
            app_id=str(metadata.get("app_id") or payload.get("source_app") or "").strip() or None,
            run_id=str(notification.get("run_id") or getattr(event, "run_id", "")).strip() or None,
            thread_id=str(metadata.get("thread_id") or "").strip() or None,
            session_key=str(notification.get("session_key") or "").strip() or None,
            channel=str(notification.get("channel") or "runtime").strip().lower() or "runtime",
            direction=str(notification.get("direction") or "system").strip().lower() or "system",
            event_class=event_class,
            detail_level="feed_summary",
            action=action or event_class,
            trace_id=str(notification.get("trace_id") or getattr(event, "trace_id", "")).strip() or None,
            title=str(notification.get("title") or "").strip(),
            summary=str(notification.get("text") or notification.get("title") or "").strip(),
            status=str(metadata.get("status") or "delivered").strip().lower() or "delivered",
            review_required=bool(metadata.get("review_required")),
            artifacts=artifacts if isinstance(artifacts, list) else _coerce_artifacts(artifacts),
            payload={
                "outbox_event_type": str(getattr(event, "event_type", "") or "").strip(),
                "notification": {
                    "id": str(notification.get("id") or "").strip() or None,
                    "action": action or None,
                    "title": str(notification.get("title") or "").strip() or None,
                },
                "outbox_payload": payload,
            },
            metadata=metadata,
            event_id=str(notification.get("id") or "").strip() or None,
        )
    )


async def list_notification_feed_items(
    *,
    tenant_id: str,
    workspace_id: str,
    channel: Optional[str] = None,
    session_key: Optional[str] = None,
    direction: Optional[str] = None,
    action: Optional[str] = None,
    run_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    since_ts: Optional[str] = None,
    since_id: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    rows = await control_plane_repository.list_activity_ledger_events(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        detail_levels=["feed_summary"],
        channel=str(channel or "").strip().lower() or None,
        session_key=str(session_key or "").strip() or None,
        direction=str(direction or "").strip().lower() or None,
        action=str(action or "").strip().lower() or None,
        run_id=str(run_id or "").strip() or None,
        trace_id=str(trace_id or "").strip() or None,
        since_created_at=since_ts,
        since_id=str(since_id or "").strip() or None,
        limit=max(1, int(limit or 100)),
    )
    return [_project_notification_item(row) for row in rows]


def list_notification_feed_items_sync(**kwargs: Any) -> List[Dict[str, Any]]:
    return list(_run_coro_sync(list_notification_feed_items(**kwargs)) or [])


async def get_notification_feed_item(
    *,
    tenant_id: str,
    workspace_id: str,
    notification_id: str,
) -> Optional[Dict[str, Any]]:
    row = await control_plane_repository.get_activity_ledger_event(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        event_id=notification_id,
    )
    if not row:
        return None
    return _project_notification_item(row)


def get_notification_feed_item_sync(**kwargs: Any) -> Optional[Dict[str, Any]]:
    return _run_coro_sync(get_notification_feed_item(**kwargs))


async def list_activity_timeline_payload(
    *,
    tenant_id: str,
    workspace_id: str,
    limit: int = 80,
    trace_id: Optional[str] = None,
    event_class: Optional[str] = None,
    detail_level: Optional[str] = None,
    actor_type: Optional[str] = None,
    actor_id: Optional[str] = None,
    install_id: Optional[str] = None,
    app_id: Optional[str] = None,
    run_id: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> Dict[str, Any]:
    rows = await control_plane_repository.list_activity_ledger_events(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        event_classes=[str(event_class or "").strip().lower()] if str(event_class or "").strip() else None,
        detail_levels=[_normalize_detail_level(detail_level)] if str(detail_level or "").strip() else None,
        actor_type=str(actor_type or "").strip().lower() or None,
        actor_id=str(actor_id or "").strip() or None,
        install_id=str(install_id or "").strip() or None,
        app_id=str(app_id or "").strip() or None,
        run_id=str(run_id or "").strip() or None,
        thread_id=str(thread_id or "").strip() or None,
        trace_id=str(trace_id or "").strip() or None,
        limit=max(1, min(int(limit or 80), 500)),
    )
    rows = [
        row
        for row in rows
        if isinstance(row, dict) and _row_within_workspace_history_window(row, workspace_id=workspace_id)
    ]
    items = [_project_timeline_item(row) for row in rows]
    by_class: Dict[str, int] = {}
    review_required_count = 0
    for item in items:
        token = str(item.get("event_class") or "").strip()
        if token:
            by_class[token] = by_class.get(token, 0) + 1
        if bool(item.get("review_required")):
            review_required_count += 1
    return {
        "items": items,
        "count": len(items),
        "total": len(items),
        "summary": {
            "review_required_count": review_required_count,
            "by_class": by_class,
        },
    }


async def list_sage_recent_activity_payload(
    *,
    tenant_id: str,
    workspace_id: str,
    limit: int = 12,
) -> Dict[str, Any]:
    rows = await control_plane_repository.list_activity_ledger_events(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        detail_levels=["feed_summary", "timeline_detail"],
        limit=max(1, min(int(limit or 12), 50)),
    )
    rows = [
        row
        for row in rows
        if isinstance(row, dict) and _row_within_workspace_history_window(row, workspace_id=workspace_id)
    ]
    items = [
        {
            "id": str(row.get("id") or "").strip() or None,
            "ts": _iso_ts(row.get("created_at")),
            "created_at": _iso_ts(row.get("created_at")),
            "event_class": str(row.get("event_class") or "").strip() or None,
            "action": str(row.get("action") or "").strip() or None,
            "title": str(row.get("title") or "").strip() or None,
            "summary": str(row.get("summary") or "").strip() or None,
            "actor_type": str(row.get("actor_type") or "").strip() or None,
            "actor_id": str(row.get("actor_id") or "").strip() or None,
            "install_id": str(row.get("install_id") or "").strip() or None,
            "app_id": str(row.get("app_id") or "").strip() or None,
            "run_id": str(row.get("run_id") or "").strip() or None,
            "review_required": bool(row.get("review_required")),
            "artifacts": _coerce_artifacts(row.get("artifacts")),
        }
        for row in rows
    ]
    by_class: Dict[str, int] = {}
    review_required_count = 0
    for item in items:
        token = str(item.get("event_class") or "").strip()
        if token:
            by_class[token] = by_class.get(token, 0) + 1
        if bool(item.get("review_required")):
            review_required_count += 1
    return {
        "items": items,
        "count": len(items),
        "summary": {
            "count": len(items),
            "by_class": by_class,
            "review_required_count": review_required_count,
        },
    }
