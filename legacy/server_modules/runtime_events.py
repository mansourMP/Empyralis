from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


ChannelEventsList = List[Dict[str, Any]]
ChannelEventsLock = Any
ListChannelEventsFn = Callable[[str, int], List[Dict[str, Any]]]
ReplaceChannelEventsFn = Callable[[str, List[Dict[str, Any]], int], None]
AppendChannelEventFn = Callable[[str, Dict[str, Any], int], None]
UtcNowIsoFn = Callable[[], str]
ParseUtcTsFn = Callable[[Any], Any]
NormalizeWorkspaceFn = Callable[[Any], Optional[str]]
NormalizeTenantFn = Callable[[Any], Optional[str]]
CompactTextFn = Callable[[Any, int], str]
JsonSafeFn = Callable[[Any], Any]
SafeReadJsonFn = Callable[[Any, Dict[str, Any]], Dict[str, Any]]


CHANNEL_EVENTS: ChannelEventsList = []
CHANNEL_EVENTS_LOCK: ChannelEventsLock = None
ORION_CHANNEL_EVENTS_LIMIT = 2000
ORION_CHANNEL_SESSIONS_LIMIT = 250
ORION_CHANNEL_EVENTS_FILE: Any = None
ORION_RUNTIME_STATE_DB = ""

list_channel_events: Optional[ListChannelEventsFn] = None
replace_channel_events: Optional[ReplaceChannelEventsFn] = None
append_channel_event: Optional[AppendChannelEventFn] = None
_utc_now_iso: Optional[UtcNowIsoFn] = None
_parse_utc_ts: Optional[ParseUtcTsFn] = None
_normalize_workspace_id: Optional[NormalizeWorkspaceFn] = None
_normalize_tenant_id: Optional[NormalizeTenantFn] = None
_compact_event_text: Optional[CompactTextFn] = None
_json_safe: Optional[JsonSafeFn] = None
_safe_read_json: Optional[SafeReadJsonFn] = None


@contextmanager
def _connect_runtime_state_db() -> Any:
    db_path = Path(str(ORION_RUNTIME_STATE_DB)).expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    try:
        yield conn
    finally:
        conn.close()


def configure_runtime_events(
    *,
    channel_events: ChannelEventsList,
    channel_events_lock: ChannelEventsLock,
    channel_events_limit: int,
    channel_sessions_limit: int,
    channel_events_file: Any,
    runtime_state_db: str,
    list_channel_events_fn: ListChannelEventsFn,
    replace_channel_events_fn: ReplaceChannelEventsFn,
    append_channel_event_fn: AppendChannelEventFn,
    utc_now_iso: UtcNowIsoFn,
    parse_utc_ts: ParseUtcTsFn,
    normalize_workspace_id: NormalizeWorkspaceFn,
    normalize_tenant_id: NormalizeTenantFn | None = None,
    compact_event_text: CompactTextFn,
    json_safe: JsonSafeFn,
    safe_read_json: SafeReadJsonFn,
) -> None:
    global CHANNEL_EVENTS
    global CHANNEL_EVENTS_LOCK
    global ORION_CHANNEL_EVENTS_LIMIT
    global ORION_CHANNEL_SESSIONS_LIMIT
    global ORION_CHANNEL_EVENTS_FILE
    global ORION_RUNTIME_STATE_DB
    global list_channel_events
    global replace_channel_events
    global append_channel_event
    global _utc_now_iso
    global _parse_utc_ts
    global _normalize_workspace_id
    global _normalize_tenant_id
    global _compact_event_text
    global _json_safe
    global _safe_read_json

    CHANNEL_EVENTS = channel_events
    CHANNEL_EVENTS_LOCK = channel_events_lock
    ORION_CHANNEL_EVENTS_LIMIT = int(channel_events_limit)
    ORION_CHANNEL_SESSIONS_LIMIT = int(channel_sessions_limit)
    ORION_CHANNEL_EVENTS_FILE = channel_events_file
    ORION_RUNTIME_STATE_DB = str(runtime_state_db)

    list_channel_events = list_channel_events_fn
    replace_channel_events = replace_channel_events_fn
    append_channel_event = append_channel_event_fn
    _utc_now_iso = utc_now_iso
    _parse_utc_ts = parse_utc_ts
    _normalize_workspace_id = normalize_workspace_id
    _normalize_tenant_id = normalize_tenant_id or (lambda value: str(value or "default").strip() or "default")
    _compact_event_text = compact_event_text
    _json_safe = json_safe
    _safe_read_json = safe_read_json


def _require_configured(value: Any, name: str) -> Any:
    if value is None:
        raise RuntimeError(f"{name} not configured")
    return value


def load_channel_events() -> None:
    list_fn = _require_configured(list_channel_events, "list_channel_events")
    replace_fn = _require_configured(replace_channel_events, "replace_channel_events")
    read_json = _require_configured(_safe_read_json, "_safe_read_json")
    utc_now_iso = _require_configured(_utc_now_iso, "_utc_now_iso")
    json_safe = _require_configured(_json_safe, "_json_safe")
    compact = _require_configured(_compact_event_text, "_compact_event_text")

    items: List[Dict[str, Any]] = []
    try:
        items = list_fn(ORION_RUNTIME_STATE_DB, ORION_CHANNEL_EVENTS_LIMIT)
    except Exception:
        items = []
    if not items:
        # One-time fallback for older local installs that only have JSON state.
        payload = read_json(ORION_CHANNEL_EVENTS_FILE, {"version": 1, "items": []})
        raw_items = payload.get("items")
        if isinstance(raw_items, list):
            for item in raw_items[:ORION_CHANNEL_EVENTS_LIMIT]:
                if isinstance(item, dict):
                    items.append(item)
        if items:
            try:
                replace_fn(ORION_RUNTIME_STATE_DB, items, ORION_CHANNEL_EVENTS_LIMIT)
            except Exception:
                pass
    with CHANNEL_EVENTS_LOCK:
        CHANNEL_EVENTS.clear()
        for item in items[:ORION_CHANNEL_EVENTS_LIMIT]:
            if not isinstance(item, dict):
                continue
            channel = str(item.get("channel") or "").strip().lower()
            direction = str(item.get("direction") or "").strip().lower()
            event_type = str(item.get("event_type") or "").strip().lower()
            if not channel or not direction or not event_type:
                continue
            CHANNEL_EVENTS.append(
                {
                    "id": str(item.get("id") or uuid.uuid4()),
                    "ts": str(item.get("ts") or utc_now_iso()),
                    "channel": channel,
                    "direction": direction,
                    "event_type": event_type,
                    "tenant_id": str(item.get("tenant_id") or "").strip(),
                    "workspace_id": str(item.get("workspace_id") or "").strip(),
                    "session_key": str(item.get("session_key") or "").strip(),
                    "session_id": str(
                        item.get("session_id")
                        or item.get("session_key")
                        or f"{channel}:unknown"
                    ).strip(),
                    "message_id": str(item.get("message_id") or item.get("id") or uuid.uuid4()).strip(),
                    "parent_id": str(item.get("parent_id") or "").strip(),
                    "run_id": str(item.get("run_id") or "").strip(),
                    "trace_id": str(
                        item.get("trace_id")
                        or ((item.get("metadata") or {}).get("trace_id") if isinstance(item.get("metadata"), dict) else "")
                        or ""
                    ).strip(),
                    "action": str(item.get("action") or "").strip().lower(),
                    "text": compact(item.get("text"), limit=800),
                    "metadata": json_safe(item.get("metadata") if isinstance(item.get("metadata"), dict) else {}),
                }
            )


def append_channel_event_item(
    channel: str,
    direction: str,
    event_type: str,
    text: Optional[str] = None,
    workspace_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    session_key: Optional[str] = None,
    session_id: Optional[str] = None,
    message_id: Optional[str] = None,
    parent_id: Optional[str] = None,
    run_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    action: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    event_id: Optional[str] = None,
    occurred_at: Optional[str] = None,
) -> Dict[str, Any]:
    append_fn = _require_configured(append_channel_event, "append_channel_event")
    utc_now_iso = _require_configured(_utc_now_iso, "_utc_now_iso")
    compact = _require_configured(_compact_event_text, "_compact_event_text")
    json_safe = _require_configured(_json_safe, "_json_safe")
    normalize_ws = _require_configured(_normalize_workspace_id, "_normalize_workspace_id")
    normalize_tenant = _require_configured(_normalize_tenant_id, "_normalize_tenant_id")

    channel_id = str(channel or "").strip().lower() or "unknown"
    direction_id = str(direction or "").strip().lower()
    if direction_id not in {"inbound", "outbound", "system"}:
        direction_id = "system"
    event_type_id = str(event_type or "").strip().lower() or "event"
    session_key_value = str(session_key or "").strip()
    session_id_value = str(session_id or session_key_value or f"{channel_id}:unknown").strip()
    metadata_value = json_safe(metadata if isinstance(metadata, dict) else {})
    trace_value = str(trace_id or metadata_value.get("trace_id") or "").strip()
    item_id = str(event_id or "").strip() or str(uuid.uuid4())
    item_ts = str(occurred_at or "").strip() or utc_now_iso()
    item = {
        "id": item_id,
        "ts": item_ts,
        "channel": channel_id,
        "direction": direction_id,
        "event_type": event_type_id,
        "tenant_id": normalize_tenant(tenant_id),
        "workspace_id": normalize_ws(workspace_id),
        "session_key": session_key_value,
        "session_id": session_id_value,
        "message_id": str(message_id or uuid.uuid4()).strip(),
        "parent_id": str(parent_id or "").strip(),
        "run_id": str(run_id or "").strip(),
        "trace_id": trace_value,
        "action": str(action or "").strip().lower(),
        "text": compact(text, limit=800),
        "metadata": metadata_value,
    }
    with CHANNEL_EVENTS_LOCK:
        CHANNEL_EVENTS[:] = [
            existing
            for existing in CHANNEL_EVENTS
            if str((existing or {}).get("id") or "").strip() != item_id
        ]
        CHANNEL_EVENTS.insert(0, item)
        del CHANNEL_EVENTS[ORION_CHANNEL_EVENTS_LIMIT:]
    try:
        append_fn(ORION_RUNTIME_STATE_DB, item, ORION_CHANNEL_EVENTS_LIMIT)
    except Exception:
        pass
    return item


def channel_event_matches(
    item: Dict[str, Any],
    workspace_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    channel: Optional[str] = None,
    session_key: Optional[str] = None,
    direction: Optional[str] = None,
    action: Optional[str] = None,
    run_id: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> bool:
    normalize_ws = _require_configured(_normalize_workspace_id, "_normalize_workspace_id")
    normalize_tenant = _require_configured(_normalize_tenant_id, "_normalize_tenant_id")
    if tenant_id and str(item.get("tenant_id") or "").strip() != normalize_tenant(tenant_id):
        return False
    if workspace_id and str(item.get("workspace_id") or "").strip() != normalize_ws(workspace_id):
        return False
    if channel and str(item.get("channel") or "").strip().lower() != str(channel).strip().lower():
        return False
    if session_key and str(item.get("session_key") or "").strip() != str(session_key).strip():
        return False
    if direction and str(item.get("direction") or "").strip().lower() != str(direction).strip().lower():
        return False
    if action and str(item.get("action") or "").strip().lower() != str(action).strip().lower():
        return False
    if run_id and str(item.get("run_id") or "").strip() != str(run_id).strip():
        return False
    if trace_id and str(item.get("trace_id") or "").strip() != str(trace_id).strip():
        return False
    return True


def _list_durable_channel_events(
    *,
    allowed_workspace_ids: Optional[set[str]] = None,
    workspace_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
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
    parse_ts = _require_configured(_parse_utc_ts, "_parse_utc_ts")
    normalize_ws = _require_configured(_normalize_workspace_id, "_normalize_workspace_id")
    clauses: List[str] = []
    params: List[Any] = []
    if workspace_id:
        clauses.append("workspace_id = ?")
        params.append(normalize_ws(workspace_id))
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
    if channel:
        clauses.append("channel = ?")
        params.append(str(channel).strip().lower())
    if session_key:
        clauses.append("session_key = ?")
        params.append(str(session_key).strip())
    if direction:
        clauses.append("direction = ?")
        params.append(str(direction).strip().lower())
    if action:
        clauses.append("action = ?")
        params.append(str(action).strip().lower())
    if run_id:
        clauses.append("run_id = ?")
        params.append(str(run_id).strip())
    if trace_id:
        clauses.append("trace_id = ?")
        params.append(str(trace_id).strip())
    if since_ts is not None:
        if str(since_id or "").strip():
            clauses.append("(sort_ts > ? OR (sort_ts = ? AND id > ?))")
            params.extend([float(since_ts), float(since_ts), str(since_id or "").strip()])
        else:
            clauses.append("sort_ts > ?")
            params.append(float(since_ts))

    query = "SELECT id, ts, event_json FROM channel_events"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY sort_ts DESC, id DESC LIMIT ?"
    params.append(max(1, int(limit or 100)))

    with _connect_runtime_state_db() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    out: List[Dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row["event_json"])
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        item = dict(payload)
        item["id"] = str(item.get("id") or row["id"] or "").strip()
        item["ts"] = str(item.get("ts") or row["ts"] or "").strip()
        if not item["id"]:
            continue
        if parse_ts(item.get("ts")) is None:
            continue
        if not channel_event_matches(
            item=item,
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            channel=channel,
            session_key=session_key,
            direction=direction,
            action=action,
            run_id=run_id,
            trace_id=trace_id,
        ):
            continue
        out.append(item)
    return out


def _resolve_durable_channel_event_cursor(
    *,
    event_id: str,
    allowed_workspace_ids: Optional[set[str]] = None,
    workspace_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    channel: Optional[str] = None,
    session_key: Optional[str] = None,
    direction: Optional[str] = None,
    action: Optional[str] = None,
    run_id: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    parse_ts = _require_configured(_parse_utc_ts, "_parse_utc_ts")
    normalize_ws = _require_configured(_normalize_workspace_id, "_normalize_workspace_id")
    clauses: List[str] = ["id = ?"]
    params: List[Any] = [str(event_id or "").strip()]
    if workspace_id:
        clauses.append("workspace_id = ?")
        params.append(normalize_ws(workspace_id))
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
    if channel:
        clauses.append("channel = ?")
        params.append(str(channel).strip().lower())
    if session_key:
        clauses.append("session_key = ?")
        params.append(str(session_key).strip())
    if direction:
        clauses.append("direction = ?")
        params.append(str(direction).strip().lower())
    if action:
        clauses.append("action = ?")
        params.append(str(action).strip().lower())
    if run_id:
        clauses.append("run_id = ?")
        params.append(str(run_id).strip())
    if trace_id:
        clauses.append("trace_id = ?")
        params.append(str(trace_id).strip())
    query = "SELECT id, ts, event_json FROM channel_events WHERE " + " AND ".join(clauses) + " LIMIT 1"
    with _connect_runtime_state_db() as conn:
        row = conn.execute(query, tuple(params)).fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(row["event_json"])
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    item = dict(payload)
    item["id"] = str(item.get("id") or row["id"] or "").strip()
    item["ts"] = str(item.get("ts") or row["ts"] or "").strip()
    if not item["id"] or parse_ts(item.get("ts")) is None:
        return None
    if not channel_event_matches(
        item=item,
        workspace_id=workspace_id,
        tenant_id=tenant_id,
        channel=channel,
        session_key=session_key,
        direction=direction,
        action=action,
        run_id=run_id,
        trace_id=trace_id,
    ):
        return None
    return item


def iter_channel_events_stream(
    *,
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
):
    parse_ts = _require_configured(_parse_utc_ts, "_parse_utc_ts")
    utc_now_iso = _require_configured(_utc_now_iso, "_utc_now_iso")

    safe_limit = max(1, min(int(limit), 500))
    safe_poll = max(0.05, min(float(poll_seconds), 5.0))
    safe_heartbeat = max(1.0, min(float(heartbeat_seconds), 60.0))
    safe_timeout = max(1.0, min(float(timeout_seconds), 300.0))

    cursor_id = str(since_id or "").strip()
    cursor_ts = parse_ts(since_ts)
    backlog_once = bool(include_backlog and not cursor_id and cursor_ts is None)

    started_mono = time.monotonic()
    next_heartbeat = started_mono + safe_heartbeat

    while True:
        now_mono = time.monotonic()
        if (now_mono - started_mono) >= safe_timeout:
            yield {"event": "done", "data": json.dumps({"reason": "timeout", "ts": utc_now_iso()}, ensure_ascii=True)}
            break

        if cursor_id and cursor_ts is None:
            cursor_item = _resolve_durable_channel_event_cursor(
                event_id=cursor_id,
                allowed_workspace_ids=allowed_workspace_ids,
                workspace_id=workspace_id,
                tenant_id=tenant_id,
                channel=channel,
                session_key=session_key,
                direction=direction,
                action=action,
                run_id=run_id,
                trace_id=trace_id,
            )
            if cursor_item is not None:
                cursor_ts = parse_ts(cursor_item.get("ts"))

        if backlog_once:
            filtered = _list_durable_channel_events(
                allowed_workspace_ids=allowed_workspace_ids,
                workspace_id=workspace_id,
                tenant_id=tenant_id,
                channel=channel,
                session_key=session_key,
                direction=direction,
                action=action,
                run_id=run_id,
                trace_id=trace_id,
                limit=safe_limit,
            )
            backlog_once = False
        elif cursor_ts is not None:
            filtered = _list_durable_channel_events(
                allowed_workspace_ids=allowed_workspace_ids,
                workspace_id=workspace_id,
                tenant_id=tenant_id,
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
        else:
            filtered = _list_durable_channel_events(
                allowed_workspace_ids=allowed_workspace_ids,
                workspace_id=workspace_id,
                tenant_id=tenant_id,
                channel=channel,
                session_key=session_key,
                direction=direction,
                action=action,
                run_id=run_id,
                trace_id=trace_id,
                limit=1,
            )
            if filtered:
                cursor_id = str(filtered[0].get("id") or "").strip()
                cursor_ts = parse_ts(filtered[0].get("ts")) or cursor_ts
                filtered = []

        if filtered:
            emit_items = list(reversed(filtered[:safe_limit]))
            for item in emit_items:
                yield {"event": "inbox_event", "data": json.dumps(item, ensure_ascii=True)}
            newest = filtered[0]
            cursor_id = str(newest.get("id") or cursor_id).strip()
            cursor_ts = parse_ts(newest.get("ts")) or cursor_ts
            next_heartbeat = time.monotonic() + safe_heartbeat
        elif now_mono >= next_heartbeat:
            yield {"event": "heartbeat", "data": json.dumps({"ts": utc_now_iso()}, ensure_ascii=True)}
            next_heartbeat = now_mono + safe_heartbeat

        time.sleep(safe_poll)


def summarize_channel_sessions(items: List[Dict[str, Any]], limit: Optional[int] = None) -> List[Dict[str, Any]]:
    compact = _require_configured(_compact_event_text, "_compact_event_text")

    ordered: List[str] = []
    sessions: Dict[str, Dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("session_id") or item.get("session_key") or "").strip()
        if not sid:
            continue
        rec = sessions.get(sid)
        if rec is None:
            ordered.append(sid)
            rec = {
                "session_id": sid,
                "session_key": str(item.get("session_key") or sid).strip(),
                "channel": str(item.get("channel") or "unknown").strip().lower(),
                "workspace_id": str(item.get("workspace_id") or "").strip(),
                "event_count": 0,
                "inbound_count": 0,
                "outbound_count": 0,
                "system_count": 0,
                "last_ts": str(item.get("ts") or ""),
                "last_event_type": str(item.get("event_type") or ""),
                "last_action": str(item.get("action") or ""),
                "last_run_id": str(item.get("run_id") or ""),
                "last_trace_id": str(item.get("trace_id") or ""),
                "last_event_id": str(item.get("id") or ""),
                "last_message_id": str(item.get("message_id") or ""),
                "last_parent_id": str(item.get("parent_id") or ""),
                "last_text": compact(item.get("text"), limit=180),
            }
            sessions[sid] = rec
        rec["event_count"] = int(rec.get("event_count") or 0) + 1
        direction = str(item.get("direction") or "").strip().lower()
        if direction == "inbound":
            rec["inbound_count"] = int(rec.get("inbound_count") or 0) + 1
        elif direction == "outbound":
            rec["outbound_count"] = int(rec.get("outbound_count") or 0) + 1
        else:
            rec["system_count"] = int(rec.get("system_count") or 0) + 1
    out = [sessions[sid] for sid in ordered if sid in sessions]
    if limit is not None:
        safe_limit = max(1, min(int(limit), max(1, ORION_CHANNEL_SESSIONS_LIMIT)))
        return out[:safe_limit]
    return out
