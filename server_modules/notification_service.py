from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional
from urllib import error as urllib_error
from urllib import request as urllib_request

from server_modules import activity_ledger_service, billing_service, entitlements_service, runtime_state_store


LOGGER = logging.getLogger(__name__)
EXPO_PUSH_ENDPOINT = str(
    os.getenv("EMPYRALIS_EXPO_PUSH_ENDPOINT", "https://exp.host/--/api/v2/push/send")
).strip() or "https://exp.host/--/api/v2/push/send"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_ts(raw: Any) -> Optional[float]:
    if isinstance(raw, (int, float)):
        return float(raw)
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value).astimezone(timezone.utc).timestamp()
    except Exception:
        return None


def _workspace_entitlement_payload(
    cache: Dict[str, Dict[str, Any]],
    workspace_id: str,
) -> Dict[str, Any]:
    token = str(workspace_id or "default").strip() or "default"
    payload = cache.get(token)
    if payload is None:
        payload = entitlements_service.workspace_entitlement_payload_for_workspace_id(workspace_id=token)
        cache[token] = payload
    return payload


def _filter_notification_history_window(
    items: List[Dict[str, Any]],
    *,
    entitlement_cache: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    filtered: List[Dict[str, Any]] = []
    now_ts = time.time()
    for item in items:
        if not isinstance(item, dict):
            continue
        workspace_id = str(item.get("workspace_id") or "default").strip() or "default"
        payload = _workspace_entitlement_payload(entitlement_cache, workspace_id)
        capabilities = payload.get("capabilities") if isinstance(payload.get("capabilities"), dict) else {}
        history_window_days = max(1, int(capabilities.get("history_window_days") or 30))
        cutoff_ts = now_ts - float(history_window_days * 86400)
        event_ts = (
            _parse_ts(item.get("updated_at"))
            or _parse_ts(item.get("created_at"))
            or _parse_ts(item.get("ts"))
            or _parse_ts(item.get("timestamp"))
        )
        if event_ts is not None and event_ts < cutoff_ts:
            continue
        filtered.append(item)
    return filtered


def _runtime_state_db_path(db_path: Optional[Path] = None) -> Path:
    if db_path is not None:
        return Path(db_path)
    try:
        import server as _server

        value = getattr(_server, "ORION_RUNTIME_STATE_DB", None)
        if value:
            return Path(value)
    except Exception:
        pass
    from server_modules.runtime_config import ORION_RUNTIME_STATE_DB

    return Path(ORION_RUNTIME_STATE_DB)


def _ensure_runtime_state_db(db_path: Optional[Path] = None) -> Path:
    runtime_db = _runtime_state_db_path(db_path)
    runtime_state_store.init_runtime_state_db(runtime_db)
    return runtime_db


def reader_key_for_current_user(current_user: Optional[Dict[str, Any]]) -> str:
    if not isinstance(current_user, dict):
        return "anonymous"
    auth_type = str(current_user.get("auth_type") or "").strip().lower()
    if auth_type == "bearer":
        user_id = str(current_user.get("user_id") or "").strip()
        if user_id:
            return f"user:{user_id}"
    if auth_type == "api_key":
        return f"{auth_type}:owner"
    if auth_type == "local_dev":
        user_id = str(current_user.get("user_id") or "").strip()
        return f"local_dev:{user_id or 'scoped'}"
    email = str(current_user.get("email") or "").strip().lower()
    if email:
        return f"email:{email}"
    role = str(current_user.get("role") or "viewer").strip().lower() or "viewer"
    return f"role:{role}"


def _title_from_action(action: str) -> str:
    token = str(action or "").strip().replace("_", " ")
    return token[:1].upper() + token[1:] if token else "Notification"


def _default_path_for_action(action: str, *, run_id: Optional[str] = None) -> str:
    normalized = str(action or "").strip().lower()
    if normalized == "approval_requested":
        return "/approvals"
    if normalized == "artifact_created" and str(run_id or "").strip():
        return f"/runs/{str(run_id).strip()}"
    if normalized in {"run_completed", "run_failed"} and str(run_id or "").strip():
        return f"/runs/{str(run_id).strip()}"
    if normalized.startswith("machine_"):
        return "/machines"
    return "/notifications"


def build_notification_from_outbox_event(event: Any) -> Optional[Dict[str, Any]]:
    event_type = str(getattr(event, "event_type", "") or "").strip().lower()
    payload = dict(getattr(event, "payload", {}) or {})
    tenant_id = str(getattr(event, "tenant_id", "") or "default").strip() or "default"
    workspace_id = str(getattr(event, "workspace_id", "") or "default").strip() or "default"
    run_id = str(getattr(event, "run_id", "") or "").strip() or None
    machine_id = str(getattr(event, "machine_id", "") or "").strip() or None
    trace_id = str(getattr(event, "trace_id", "") or "").strip()
    event_id = str(getattr(event, "event_id", "") or "").strip()
    created_at = str(getattr(event, "created_at", "") or payload.get("emitted_at") or _utc_now_iso()).strip()

    action = ""
    title = ""
    text = ""
    priority = "normal"
    metadata: Dict[str, Any] = dict(payload.get("metadata") or {}) if isinstance(payload.get("metadata"), dict) else {}
    browser_payload = payload.get("browser") if isinstance(payload.get("browser"), dict) else None

    if event_type == "run_transition":
        to_state = str(payload.get("to_state") or "").strip().lower()
        if to_state not in {"completed", "failed"}:
            return None
        action = f"run_{to_state}"
        title = "Run completed" if to_state == "completed" else "Run failed"
        text = f"Run {run_id or 'unknown'} {to_state}."
        priority = "normal" if to_state == "completed" else "high"
        metadata = {
            **metadata,
            "status": to_state,
        }
    elif event_type == "machine_event":
        raw_action = str(payload.get("action") or "").strip().lower()
        enrollment_state = str(payload.get("enrollment_state") or payload.get("status") or "").strip().lower()
        error_text = str(payload.get("error") or payload.get("bootstrap_error") or "").strip()
        machine_label = str(payload.get("display_name") or machine_id or "machine").strip() or "machine"
        if raw_action == "revoked":
            action = "machine_revoked"
            title = "Machine revoked"
            text = f"{machine_label} was revoked."
            priority = "high"
        elif raw_action in {"enrollment_failed", "bootstrap_failed"} or (
            raw_action == "enrollment_state_updated" and enrollment_state == "failed"
        ):
            action = "machine_enrollment_failed"
            title = "Machine enrollment failed"
            text = error_text or f"{machine_label} failed to enroll."
            priority = "high"
        else:
            return None
    elif event_type == "artifact_created":
        artifact_payload = payload.get("artifact") if isinstance(payload.get("artifact"), dict) else {}
        artifact_path = str(
            payload.get("artifact_path")
            or artifact_payload.get("path")
            or artifact_payload.get("file_path")
            or artifact_payload.get("uri")
            or artifact_payload.get("url")
            or ""
        ).strip()
        action = "artifact_created"
        title = "Artifact created"
        text = artifact_path or f"Artifact created for run {run_id or 'unknown'}."
        priority = "normal"
        metadata = {
            **metadata,
            "activity_event_class": "artifact_created",
            "activity_actor_type": "system",
            "activity_actor_id": "runtime",
            "status": str(metadata.get("status") or "created").strip().lower() or "created",
            "artifacts": [artifact_payload] if artifact_payload else [],
            "path": str(metadata.get("path") or "").strip() or _default_path_for_action(action, run_id=run_id),
        }
    elif event_type == "notification":
        action = str(payload.get("action") or "notification").strip().lower() or "notification"
        title = str(metadata.get("title") or "").strip() or _title_from_action(action)
        text = str(payload.get("text") or "").strip() or "Notification"
        priority = str(metadata.get("priority") or "normal").strip().lower() or "normal"
    else:
        return None

    route_path = str(metadata.get("path") or "").strip() or _default_path_for_action(action, run_id=run_id)
    notification = {
        "id": event_id,
        "ts": created_at,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "session_key": (
            f"run:{run_id}"
            if run_id
            else f"machine:{machine_id}"
            if machine_id
            else f"workspace:{workspace_id}"
        ),
        "session_id": (
            f"run:{run_id}"
            if run_id
            else f"machine:{machine_id}"
            if machine_id
            else f"workspace:{workspace_id}"
        ),
        "channel": "runtime",
        "direction": "system",
        "event_type": event_type,
        "run_id": run_id,
        "machine_id": machine_id,
        "trace_id": trace_id,
        "action": action,
        "title": title,
        "text": text,
        "metadata": {
            **metadata,
            "path": route_path,
            "priority": priority,
            "source_event_type": event_type,
            "outbox_event_id": event_id,
            "delivery_channels": ["feed", "push"],
            "run_id": run_id,
            "machine_id": machine_id,
        },
    }
    return notification


def _workspace_filtered_items(
    items: Iterable[Dict[str, Any]],
    *,
    allowed_workspaces: Optional[set[str]],
    requested_workspace_id: Optional[str],
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        workspace_id = str(item.get("workspace_id") or "").strip()
        if requested_workspace_id and workspace_id != requested_workspace_id:
            continue
        if allowed_workspaces is not None and workspace_id not in allowed_workspaces:
            continue
        out.append(item)
    return out


def _merge_read_state(
    *,
    items: List[Dict[str, Any]],
    reader_key: str,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    if not items:
        return []
    notification_ids = [
        str(item.get("id") or "").strip()
        for item in items
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    ]
    read_index = {
        str(item.get("id") or "").strip(): str(item.get("read_at") or "").strip() or None
        for item in runtime_state_store.list_notifications_by_ids(
            _ensure_runtime_state_db(db_path),
            reader_key=reader_key,
            notification_ids=notification_ids,
        )
    }
    out: List[Dict[str, Any]] = []
    for item in items:
        record = dict(item) if isinstance(item, dict) else {}
        read_at = read_index.get(str(record.get("id") or "").strip())
        if read_at:
            record["read_at"] = read_at
        out.append(record)
    return out


def _merge_notification_sources(
    *,
    primary_items: List[Dict[str, Any]],
    secondary_items: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for item in list(primary_items) + list(secondary_items):
        if not isinstance(item, dict):
            continue
        notification_id = str(item.get("id") or "").strip()
        if not notification_id:
            continue
        if notification_id not in merged:
            order.append(notification_id)
            merged[notification_id] = dict(item)
            continue
        current = merged[notification_id]
        merged[notification_id] = {
            **dict(item),
            **current,
            "metadata": {
                **(dict(item.get("metadata") or {}) if isinstance(item.get("metadata"), dict) else {}),
                **(dict(current.get("metadata") or {}) if isinstance(current.get("metadata"), dict) else {}),
            },
        }
        if str(current.get("read_at") or "").strip():
            merged[notification_id]["read_at"] = current.get("read_at")
    out = [merged[item_id] for item_id in order]
    out.sort(
        key=lambda item: (
            -(_parse_ts(item.get("ts")) or 0.0),
            str(item.get("id") or ""),
        )
    )
    return out


def _notification_item_matches(
    item: Dict[str, Any],
    *,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    channel: Optional[str] = None,
    session_key: Optional[str] = None,
    direction: Optional[str] = None,
    action: Optional[str] = None,
    run_id: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> bool:
    if tenant_id and str(item.get("tenant_id") or "").strip() != str(tenant_id or "").strip():
        return False
    if workspace_id and str(item.get("workspace_id") or "").strip() != str(workspace_id or "").strip():
        return False
    if channel and str(item.get("channel") or "").strip().lower() != str(channel or "").strip().lower():
        return False
    if session_key and str(item.get("session_key") or "").strip() != str(session_key or "").strip():
        return False
    if direction and str(item.get("direction") or "").strip().lower() != str(direction or "").strip().lower():
        return False
    if action and str(item.get("action") or "").strip().lower() != str(action or "").strip().lower():
        return False
    if run_id and str(item.get("run_id") or "").strip() != str(run_id or "").strip():
        return False
    if trace_id and str(item.get("trace_id") or "").strip() != str(trace_id or "").strip():
        return False
    return True


def _runtime_notification_query_ids(
    *,
    db_path: Path,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    allowed_workspace_ids: Optional[set[str]] = None,
    channel: Optional[str] = None,
    session_key: Optional[str] = None,
    direction: Optional[str] = None,
    action: Optional[str] = None,
    run_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    since_ts: Optional[float] = None,
    since_id: Optional[str] = None,
    limit: int = 100,
) -> List[str]:
    clauses: List[str] = []
    params: List[Any] = []
    if str(tenant_id or "").strip():
        clauses.append("tenant_id = ?")
        params.append(str(tenant_id or "").strip())
    if str(workspace_id or "").strip():
        clauses.append("workspace_id = ?")
        params.append(str(workspace_id or "").strip())
    normalized_allowed = sorted(
        {
            str(item or "").strip()
            for item in (allowed_workspace_ids or set())
            if str(item or "").strip()
        }
    )
    if allowed_workspace_ids is not None:
        if not normalized_allowed:
            return []
        placeholders = ", ".join("?" for _ in normalized_allowed)
        clauses.append(f"workspace_id IN ({placeholders})")
        params.extend(normalized_allowed)
    if str(channel or "").strip():
        clauses.append("channel = ?")
        params.append(str(channel or "").strip().lower())
    if str(session_key or "").strip():
        clauses.append("session_key = ?")
        params.append(str(session_key or "").strip())
    if str(direction or "").strip():
        clauses.append("direction = ?")
        params.append(str(direction or "").strip().lower())
    if str(action or "").strip():
        clauses.append("action = ?")
        params.append(str(action or "").strip().lower())
    if str(run_id or "").strip():
        clauses.append("run_id = ?")
        params.append(str(run_id or "").strip())
    if str(trace_id or "").strip():
        clauses.append("trace_id = ?")
        params.append(str(trace_id or "").strip())
    if since_ts is not None:
        if str(since_id or "").strip():
            clauses.append("(sort_ts > ? OR (sort_ts = ? AND id > ?))")
            params.extend([float(since_ts), float(since_ts), str(since_id or "").strip()])
        else:
            clauses.append("sort_ts > ?")
            params.append(float(since_ts))

    query = "SELECT id FROM notifications"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY sort_ts DESC, id DESC LIMIT ?"
    params.append(max(1, int(limit or 100)))

    try:
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, tuple(params)).fetchall()
    except sqlite3.Error as exc:
        LOGGER.warning("Notification runtime store unavailable for query: %s", exc)
        return []
    return [str(row["id"] or "").strip() for row in rows if str(row["id"] or "").strip()]


def _list_runtime_notifications_since(
    *,
    db_path: Path,
    reader_key: str,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    allowed_workspace_ids: Optional[set[str]] = None,
    channel: Optional[str] = None,
    session_key: Optional[str] = None,
    direction: Optional[str] = None,
    action: Optional[str] = None,
    run_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    since_ts: Optional[float] = None,
    since_id: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    notification_ids = _runtime_notification_query_ids(
        db_path=db_path,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        allowed_workspace_ids=allowed_workspace_ids,
        channel=channel,
        session_key=session_key,
        direction=direction,
        action=action,
        run_id=run_id,
        trace_id=trace_id,
        since_ts=since_ts,
        since_id=since_id,
        limit=limit,
    )
    if not notification_ids:
        return []
    items = runtime_state_store.list_notifications_by_ids(
        db_path,
        reader_key=reader_key,
        notification_ids=notification_ids,
    )
    by_id = {
        str(item.get("id") or "").strip(): item
        for item in items
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    return [dict(by_id[item_id]) for item_id in notification_ids if item_id in by_id]


def _resolve_runtime_notification_cursor(
    *,
    db_path: Path,
    notification_id: str,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    allowed_workspace_ids: Optional[set[str]] = None,
    channel: Optional[str] = None,
    session_key: Optional[str] = None,
    direction: Optional[str] = None,
    action: Optional[str] = None,
    run_id: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    clauses = ["id = ?"]
    params: List[Any] = [str(notification_id or "").strip()]
    if str(tenant_id or "").strip():
        clauses.append("tenant_id = ?")
        params.append(str(tenant_id or "").strip())
    if str(workspace_id or "").strip():
        clauses.append("workspace_id = ?")
        params.append(str(workspace_id or "").strip())
    normalized_allowed = sorted(
        {
            str(item or "").strip()
            for item in (allowed_workspace_ids or set())
            if str(item or "").strip()
        }
    )
    if allowed_workspace_ids is not None:
        if not normalized_allowed:
            return None
        placeholders = ", ".join("?" for _ in normalized_allowed)
        clauses.append(f"workspace_id IN ({placeholders})")
        params.extend(normalized_allowed)
    if str(channel or "").strip():
        clauses.append("channel = ?")
        params.append(str(channel or "").strip().lower())
    if str(session_key or "").strip():
        clauses.append("session_key = ?")
        params.append(str(session_key or "").strip())
    if str(direction or "").strip():
        clauses.append("direction = ?")
        params.append(str(direction or "").strip().lower())
    if str(action or "").strip():
        clauses.append("action = ?")
        params.append(str(action or "").strip().lower())
    if str(run_id or "").strip():
        clauses.append("run_id = ?")
        params.append(str(run_id or "").strip())
    if str(trace_id or "").strip():
        clauses.append("trace_id = ?")
        params.append(str(trace_id or "").strip())
    query = "SELECT created_at FROM notifications WHERE " + " AND ".join(clauses) + " LIMIT 1"
    try:
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(query, tuple(params)).fetchone()
    except sqlite3.Error as exc:
        LOGGER.warning("Notification runtime store unavailable for cursor: %s", exc)
        return None
    if row is None:
        return None
    created_at = str(row["created_at"] or "").strip()
    return {"id": str(notification_id or "").strip(), "ts": created_at}


def _newest_notification_item(items: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    candidates = [dict(item) for item in items if isinstance(item, dict) and str(item.get("id") or "").strip()]
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            -(_parse_ts(item.get("ts")) or 0.0),
            str(item.get("id") or ""),
        )
    )
    return candidates[0]


def list_notification_payload(
    *,
    current_user: Any,
    limit: int,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    channel: Optional[str] = None,
    session_key: Optional[str] = None,
    direction: Optional[str] = None,
    action: Optional[str] = None,
    run_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    include_sessions: bool = True,
    session_limit: int = 25,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    from server_modules.auth import allowed_workspace_ids, enforce_workspace_access, workspace_tenant_id

    safe_limit = max(1, min(int(limit or 0), 500))
    requested_workspace_id = (
        enforce_workspace_access(current_user, workspace_id, tenant_id=tenant_id, minimum_role="viewer")
        if workspace_id
        else None
    )
    allowed_workspaces = allowed_workspace_ids(current_user)
    reader_key = reader_key_for_current_user(current_user)
    entitlement_cache: Dict[str, Dict[str, Any]] = {}
    filtered: List[Dict[str, Any]] = []
    runtime_items = runtime_state_store.list_notifications(
        _ensure_runtime_state_db(db_path),
        reader_key=reader_key,
        tenant_id=str(tenant_id or "").strip(),
        workspace_id=str(requested_workspace_id or "").strip(),
        channel=str(channel or "").strip(),
        session_key=str(session_key or "").strip(),
        direction=str(direction or "").strip(),
        action=str(action or "").strip(),
        run_id=str(run_id or "").strip(),
        trace_id=str(trace_id or "").strip(),
        limit=max(safe_limit, 500 if requested_workspace_id else safe_limit * 4),
    )
    runtime_filtered = _workspace_filtered_items(
        runtime_items,
        allowed_workspaces=allowed_workspaces,
        requested_workspace_id=requested_workspace_id,
    )
    runtime_filtered = _filter_notification_history_window(
        runtime_filtered,
        entitlement_cache=entitlement_cache,
    )
    if requested_workspace_id:
        resolved_tenant_id = str(tenant_id or "").strip() or workspace_tenant_id(current_user, requested_workspace_id)
        ledger_items = activity_ledger_service.list_notification_feed_items_sync(
            tenant_id=resolved_tenant_id,
            workspace_id=requested_workspace_id,
            channel=str(channel or "").strip(),
            session_key=str(session_key or "").strip(),
            direction=str(direction or "").strip(),
            action=str(action or "").strip(),
            run_id=str(run_id or "").strip(),
            trace_id=str(trace_id or "").strip(),
            limit=max(safe_limit, 500),
        )
        if ledger_items:
            scoped_ledger_items = _filter_notification_history_window(
                _workspace_filtered_items(
                    ledger_items,
                    allowed_workspaces=allowed_workspaces,
                    requested_workspace_id=requested_workspace_id,
                ),
                entitlement_cache=entitlement_cache,
            )
            filtered = _merge_notification_sources(
                primary_items=_merge_read_state(
                    items=scoped_ledger_items,
                    reader_key=reader_key,
                    db_path=db_path,
                ),
                secondary_items=runtime_filtered,
            )
    if not filtered:
        filtered = runtime_filtered
    payload = filtered[:safe_limit]
    sessions = (
        runtime_state_store.summarize_notification_sessions(filtered, limit=session_limit)
        if include_sessions
        else []
    )
    return {
        "items": payload,
        "count": len(payload),
        "total": len(filtered),
        "sessions": sessions,
        "session_count": len(sessions),
        "stream": False,
    }


def mark_notifications_read(
    *,
    current_user: Any,
    notification_ids: Optional[List[str]] = None,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    mark_all: bool = False,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    from server_modules.auth import allowed_workspace_ids, enforce_workspace_access

    requested_workspace_id = (
        enforce_workspace_access(current_user, workspace_id, tenant_id=tenant_id, minimum_role="viewer")
        if workspace_id
        else None
    )
    allowed_workspaces = allowed_workspace_ids(current_user)
    reader_key = reader_key_for_current_user(current_user)
    normalized_ids = [
        str(item or "").strip()
        for item in (notification_ids or [])
        if str(item or "").strip()
    ]
    if mark_all:
        target_items = _workspace_filtered_items(
            runtime_state_store.list_notifications(
                _ensure_runtime_state_db(db_path),
                reader_key=reader_key,
                tenant_id=str(tenant_id or "").strip(),
                workspace_id=str(requested_workspace_id or "").strip(),
                limit=5000,
            ),
            allowed_workspaces=allowed_workspaces,
            requested_workspace_id=requested_workspace_id,
        )
        return {
            "status": "ok",
            **runtime_state_store.mark_notifications_read(
                _ensure_runtime_state_db(db_path),
                reader_key=reader_key,
                notification_ids=[
                    str(item.get("id") or "").strip()
                    for item in target_items
                    if isinstance(item, dict) and str(item.get("id") or "").strip()
                ],
            ),
        }
    allowed_items = runtime_state_store.list_notifications_by_ids(
        _ensure_runtime_state_db(db_path),
        reader_key=reader_key,
        notification_ids=normalized_ids,
    )
    filtered_items = _workspace_filtered_items(
        allowed_items,
        allowed_workspaces=allowed_workspaces,
        requested_workspace_id=requested_workspace_id,
    )
    marked = runtime_state_store.mark_notifications_read(
        _ensure_runtime_state_db(db_path),
        reader_key=reader_key,
        notification_ids=[str(item.get("id") or "").strip() for item in filtered_items],
    )
    return {"status": "ok", **marked}


def register_notification_device(
    *,
    current_user: Any,
    workspace_id: str,
    push_token: str,
    device_id: str,
    provider: str = "expo",
    platform: str = "",
    device_name: str = "",
    app_id: str = "",
    capabilities: Optional[List[str]] = None,
    db_path: Optional[Path] = None,
    workspace: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    from server_modules.auth import enforce_workspace_access, workspace_tenant_id

    def _workspace_has_explicit_plan(payload: Optional[Dict[str, Any]]) -> bool:
        metadata = payload.get("metadata") if isinstance(payload, dict) and isinstance(payload.get("metadata"), dict) else {}
        billing = metadata.get("billing") if isinstance(metadata.get("billing"), dict) else {}
        for value in (
            billing.get("plan"),
            billing.get("plan_id"),
            billing.get("plan_tier"),
            metadata.get("plan"),
            metadata.get("plan_id"),
            metadata.get("plan_tier"),
        ):
            if str(value or "").strip():
                return True
        return False

    workspace_token = enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    tenant_id = workspace_tenant_id(current_user, workspace_token)
    try:
        entitlement_state = entitlements_service.workspace_entitlement_payload_for_workspace_id(
            workspace_id=workspace_token,
            workspace=workspace,
        )
        billing_summary = billing_service.workspace_billing_summary_for_workspace_id(workspace_token)
        subscription = billing_summary.get("subscription") if isinstance(billing_summary, dict) and isinstance(billing_summary.get("subscription"), dict) else {}
        billing_metadata = subscription.get("metadata") if isinstance(subscription.get("metadata"), dict) else {}
        default_workspace_seed = str(billing_metadata.get("source") or "").strip().lower() == "workspace_default"
        explicit_workspace_plan = _workspace_has_explicit_plan(workspace)
        should_enforce_mobile_push = explicit_workspace_plan or (
            not default_workspace_seed and str(entitlement_state.get("source") or "").strip().lower() != "default"
        )
        if should_enforce_mobile_push:
            entitlement_state = entitlements_service.enforce_mobile_push_access(workspace=workspace)
    except entitlements_service.EntitlementError as exc:
        return {
            "ok": False,
            "device_id": str(device_id or "").strip(),
            "workspace_id": workspace_token,
            "tenant_id": str(tenant_id or "default").strip() or "default",
            "status": "disabled",
            "reason": exc.reason,
            "message": exc.message,
            "entitlements": exc.entitlement_state,
        }
    record = runtime_state_store.upsert_notification_device(
        _ensure_runtime_state_db(db_path),
        {
            "device_id": str(device_id or "").strip(),
            "tenant_id": str(tenant_id or "default").strip() or "default",
            "workspace_id": workspace_token,
            "reader_key": reader_key_for_current_user(current_user),
            "provider": str(provider or "expo").strip().lower() or "expo",
            "push_token": str(push_token or "").strip(),
            "platform": str(platform or "").strip().lower(),
            "device_name": str(device_name or "").strip(),
            "app_id": str(app_id or "").strip(),
            "capabilities": [str(item or "").strip() for item in (capabilities or []) if str(item or "").strip()],
            "status": "active",
            "registered_at": _utc_now_iso(),
        },
    )
    return {
        "ok": True,
        "device_id": str(record.get("device_id") or "").strip(),
        "workspace_id": workspace_token,
        "tenant_id": str(record.get("tenant_id") or tenant_id or "default").strip() or "default",
        "status": str(record.get("status") or "active").strip() or "active",
        "registered_at": str(record.get("last_registered_at") or "").strip() or _utc_now_iso(),
        "provider": str(record.get("provider") or "expo").strip() or "expo",
        "entitlements": entitlement_state,
    }


def send_expo_push_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not messages:
        return []
    request_payload = json.dumps(messages, ensure_ascii=False).encode("utf-8")
    request = urllib_request.Request(
        EXPO_PUSH_ENDPOINT,
        data=request_payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib_request.urlopen(request, timeout=10.0) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib_error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(body or f"Expo push request failed ({exc.code}).") from exc
    except Exception as exc:
        raise RuntimeError(str(exc) or "Expo push request failed.") from exc
    try:
        payload = json.loads(raw)
    except Exception as exc:
        raise RuntimeError("Expo push response was not valid JSON.") from exc
    data = payload.get("data")
    if isinstance(data, list):
        return [dict(item) if isinstance(item, dict) else {"status": "error", "message": str(item)} for item in data]
    if isinstance(data, dict):
        return [dict(data)]
    return []


def fanout_notification_push(
    notification: Dict[str, Any],
    *,
    db_path: Optional[Path] = None,
    send_push_messages_fn=send_expo_push_messages,
) -> Dict[str, Any]:
    notification_id = str(notification.get("id") or "").strip()
    if not notification_id:
        return {"device_count": 0, "delivered_count": 0}
    runtime_db = _ensure_runtime_state_db(db_path)
    deliveries = runtime_state_store.list_notification_delivery_statuses(
        runtime_db,
        notification_id=notification_id,
    )
    devices = runtime_state_store.list_notification_devices(
        runtime_db,
        tenant_id=str(notification.get("tenant_id") or "").strip(),
        workspace_id=str(notification.get("workspace_id") or "").strip(),
        status="active",
    )
    pending_devices = [
        device
        for device in devices
        if str(device.get("provider") or "").strip().lower() == "expo"
        and str(device.get("push_token") or "").strip()
        and str((deliveries.get(str(device.get("device_id") or "").strip()) or {}).get("status") or "").strip().lower()
        != "delivered"
    ]
    if not pending_devices:
        return {"device_count": 0, "delivered_count": 0}
    metadata = dict(notification.get("metadata") or {}) if isinstance(notification.get("metadata"), dict) else {}
    messages = [
        {
            "to": str(device.get("push_token") or "").strip(),
            "title": str(notification.get("title") or "").strip() or _title_from_action(notification.get("action") or "notification"),
            "body": str(notification.get("text") or "").strip() or "Empyralis notification",
            "data": {
                "notificationId": notification_id,
                "path": str(metadata.get("path") or _default_path_for_action(notification.get("action") or "", run_id=notification.get("run_id"))).strip(),
                "runId": str(notification.get("run_id") or "").strip() or None,
                "workspaceId": str(notification.get("workspace_id") or "").strip() or None,
                "machineId": str(notification.get("machine_id") or "").strip() or None,
            },
        }
        for device in pending_devices
    ]
    results = send_push_messages_fn(messages)
    if len(results) != len(pending_devices):
        raise RuntimeError("Push delivery response count did not match device count.")
    delivered_count = 0
    failures: List[str] = []
    now_iso = _utc_now_iso()
    for device, result in zip(pending_devices, results):
        device_id = str(device.get("device_id") or "").strip()
        status = str((result or {}).get("status") or "").strip().lower()
        if status == "ok":
            runtime_state_store.upsert_notification_delivery(
                runtime_db,
                notification_id=notification_id,
                device_id=device_id,
                status="delivered",
                delivered_at=now_iso,
            )
            runtime_state_store.update_notification_device_status(
                runtime_db,
                device_id,
                status="active",
                last_error="",
                last_delivered_at=now_iso,
            )
            delivered_count += 1
            continue
        details = result.get("details") if isinstance(result, dict) else None
        error_text = ""
        if isinstance(details, dict):
            error_text = str(details.get("error") or details.get("message") or "").strip()
        if not error_text:
            error_text = str(result.get("message") or result.get("error") or "push delivery failed").strip()
        normalized_status = "invalid" if "notregistered" in error_text.lower() or "device not registered" in error_text.lower() else "failed"
        runtime_state_store.upsert_notification_delivery(
            runtime_db,
            notification_id=notification_id,
            device_id=device_id,
            status=normalized_status,
            last_error=error_text,
        )
        runtime_state_store.update_notification_device_status(
            runtime_db,
            device_id,
            status="invalid" if normalized_status == "invalid" else "active",
            last_error=error_text,
        )
        failures.append(f"{device_id}:{error_text}")
    if failures:
        raise RuntimeError("; ".join(failures))
    return {"device_count": len(pending_devices), "delivered_count": delivered_count}


def deliver_notification_from_outbox_event(
    event: Any,
    *,
    db_path: Optional[Path] = None,
    send_push_messages_fn=send_expo_push_messages,
) -> Optional[Dict[str, Any]]:
    notification = build_notification_from_outbox_event(event)
    if notification is None:
        return None
    runtime_db = _ensure_runtime_state_db(db_path)
    runtime_state_store.upsert_notification(runtime_db, notification)
    try:
        activity_ledger_service.record_notification_activity(
            event=event,
            notification=notification,
        )
    except Exception as exc:
        LOGGER.warning("Failed to persist activity-ledger notification %s: %s", notification.get("id"), exc)
    fanout_notification_push(
        notification,
        db_path=runtime_db,
        send_push_messages_fn=send_push_messages_fn,
    )
    return notification


def iter_notifications_stream(
    *,
    reader_key: str,
    allowed_workspace_ids: Optional[set[str]] = None,
    workspace_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    channel: Optional[str] = None,
    session_key: Optional[str] = None,
    direction: Optional[str] = None,
    action: Optional[str] = None,
    run_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    since_id: Optional[str] = None,
    since_ts: Optional[str] = None,
    include_backlog: bool = False,
    poll_seconds: float = 0.35,
    heartbeat_seconds: float = 5.0,
    timeout_seconds: float = 25.0,
    limit: int = 120,
    db_path: Optional[Path] = None,
):
    safe_limit = max(1, min(int(limit or 0), 500))
    safe_poll = max(0.05, min(float(poll_seconds or 0.0), 5.0))
    safe_heartbeat = max(1.0, min(float(heartbeat_seconds or 0.0), 60.0))
    safe_timeout = max(1.0, min(float(timeout_seconds or 0.0), 300.0))
    cursor_id = str(since_id or "").strip()
    cursor_ts = _parse_ts(since_ts)
    backlog_once = bool(include_backlog and not cursor_id and cursor_ts is None)
    started_mono = time.monotonic()
    next_heartbeat = started_mono + safe_heartbeat
    runtime_db = _ensure_runtime_state_db(db_path)
    ledger_enabled = bool(str(workspace_id or "").strip())
    resolved_workspace_id = str(workspace_id or "").strip() or None
    resolved_tenant_id = str(tenant_id or "").strip() or None

    while True:
        now_mono = time.monotonic()
        if (now_mono - started_mono) >= safe_timeout:
            yield {"event": "done", "data": json.dumps({"reason": "timeout", "ts": _utc_now_iso()}, ensure_ascii=True)}
            break

        if cursor_id and cursor_ts is None:
            runtime_cursor = _resolve_runtime_notification_cursor(
                db_path=runtime_db,
                notification_id=cursor_id,
                tenant_id=resolved_tenant_id,
                workspace_id=resolved_workspace_id,
                allowed_workspace_ids=allowed_workspace_ids,
                channel=channel,
                session_key=session_key,
                direction=direction,
                action=action,
                run_id=run_id,
                trace_id=trace_id,
            )
            if runtime_cursor:
                cursor_ts = _parse_ts(runtime_cursor.get("ts"))
            elif ledger_enabled:
                try:
                    ledger_cursor = activity_ledger_service.get_notification_feed_item_sync(
                        tenant_id=resolved_tenant_id or "default",
                        workspace_id=resolved_workspace_id or "default",
                        notification_id=cursor_id,
                    )
                except Exception:
                    ledger_cursor = None
                if ledger_cursor and (
                    allowed_workspace_ids is None
                    or str(ledger_cursor.get("workspace_id") or "").strip() in allowed_workspace_ids
                ):
                    if _workspace_filtered_items(
                        [ledger_cursor],
                        allowed_workspaces=allowed_workspace_ids,
                        requested_workspace_id=resolved_workspace_id,
                    ) and _notification_item_matches(
                        ledger_cursor,
                        tenant_id=resolved_tenant_id,
                        workspace_id=resolved_workspace_id,
                        channel=channel,
                        session_key=session_key,
                        direction=direction,
                        action=action,
                        run_id=run_id,
                        trace_id=trace_id,
                    ):
                        cursor_ts = _parse_ts(ledger_cursor.get("ts"))

        candidates: List[Dict[str, Any]] = []
        if backlog_once:
            runtime_items = _list_runtime_notifications_since(
                db_path=runtime_db,
                reader_key=reader_key,
                tenant_id=resolved_tenant_id,
                workspace_id=resolved_workspace_id,
                allowed_workspace_ids=allowed_workspace_ids,
                channel=channel,
                session_key=session_key,
                direction=direction,
                action=action,
                run_id=run_id,
                trace_id=trace_id,
                limit=safe_limit,
            )
            ledger_items: List[Dict[str, Any]] = []
            if ledger_enabled:
                try:
                    ledger_items = _merge_read_state(
                        items=_workspace_filtered_items(
                            activity_ledger_service.list_notification_feed_items_sync(
                                tenant_id=resolved_tenant_id or "default",
                                workspace_id=resolved_workspace_id or "default",
                                channel=str(channel or "").strip(),
                                session_key=str(session_key or "").strip(),
                                direction=str(direction or "").strip(),
                                action=str(action or "").strip(),
                                run_id=str(run_id or "").strip(),
                                trace_id=str(trace_id or "").strip(),
                                limit=safe_limit,
                            ),
                            allowed_workspaces=allowed_workspace_ids,
                            requested_workspace_id=resolved_workspace_id,
                        ),
                        reader_key=reader_key,
                        db_path=runtime_db,
                    )
                except Exception:
                    ledger_items = []
            candidates = (
                _merge_notification_sources(primary_items=ledger_items, secondary_items=runtime_items)
                if ledger_items
                else runtime_items
            )
            backlog_once = False
        elif cursor_ts is not None:
            runtime_items = _list_runtime_notifications_since(
                db_path=runtime_db,
                reader_key=reader_key,
                tenant_id=resolved_tenant_id,
                workspace_id=resolved_workspace_id,
                allowed_workspace_ids=allowed_workspace_ids,
                channel=channel,
                session_key=session_key,
                direction=direction,
                action=action,
                run_id=run_id,
                trace_id=trace_id,
                since_ts=cursor_ts,
                since_id=cursor_id,
                limit=safe_limit,
            )
            ledger_items = []
            if ledger_enabled:
                try:
                    ledger_items = _merge_read_state(
                        items=_workspace_filtered_items(
                            activity_ledger_service.list_notification_feed_items_sync(
                                tenant_id=resolved_tenant_id or "default",
                                workspace_id=resolved_workspace_id or "default",
                                channel=str(channel or "").strip(),
                                session_key=str(session_key or "").strip(),
                                direction=str(direction or "").strip(),
                                action=str(action or "").strip(),
                                run_id=str(run_id or "").strip(),
                                trace_id=str(trace_id or "").strip(),
                                since_ts=datetime.fromtimestamp(cursor_ts, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
                                since_id=cursor_id,
                                limit=safe_limit,
                            ),
                            allowed_workspaces=allowed_workspace_ids,
                            requested_workspace_id=resolved_workspace_id,
                        ),
                        reader_key=reader_key,
                        db_path=runtime_db,
                    )
                except Exception:
                    ledger_items = []
            candidates = (
                _merge_notification_sources(primary_items=ledger_items, secondary_items=runtime_items)
                if ledger_items
                else runtime_items
            )
        else:
            head_candidates: List[Dict[str, Any]] = []
            head_candidates.extend(
                _list_runtime_notifications_since(
                    db_path=runtime_db,
                    reader_key=reader_key,
                    tenant_id=resolved_tenant_id,
                    workspace_id=resolved_workspace_id,
                    allowed_workspace_ids=allowed_workspace_ids,
                    channel=channel,
                    session_key=session_key,
                    direction=direction,
                    action=action,
                    run_id=run_id,
                    trace_id=trace_id,
                    limit=1,
                )
            )
            if ledger_enabled:
                try:
                    head_candidates.extend(
                        _workspace_filtered_items(
                            activity_ledger_service.list_notification_feed_items_sync(
                                tenant_id=resolved_tenant_id or "default",
                                workspace_id=resolved_workspace_id or "default",
                                channel=str(channel or "").strip(),
                                session_key=str(session_key or "").strip(),
                                direction=str(direction or "").strip(),
                                action=str(action or "").strip(),
                                run_id=str(run_id or "").strip(),
                                trace_id=str(trace_id or "").strip(),
                                limit=1,
                            ),
                            allowed_workspaces=allowed_workspace_ids,
                            requested_workspace_id=resolved_workspace_id,
                        )
                    )
                except Exception:
                    pass
            newest = _newest_notification_item(head_candidates)
            if newest:
                cursor_id = str(newest.get("id") or "").strip()
                cursor_ts = _parse_ts(newest.get("ts")) or cursor_ts

        if candidates:
            emit_items = list(reversed(candidates[:safe_limit]))
            for item in emit_items:
                yield {"event": "notification", "data": json.dumps(item, ensure_ascii=True)}
            newest = candidates[0]
            cursor_id = str(newest.get("id") or cursor_id).strip()
            cursor_ts = _parse_ts(newest.get("ts")) or cursor_ts
            next_heartbeat = time.monotonic() + safe_heartbeat
        elif now_mono >= next_heartbeat:
            yield {"event": "heartbeat", "data": json.dumps({"ts": _utc_now_iso()}, ensure_ascii=True)}
            next_heartbeat = now_mono + safe_heartbeat

        time.sleep(safe_poll)
