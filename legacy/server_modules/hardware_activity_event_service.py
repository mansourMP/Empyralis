from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, List, Optional

from server_modules import runtime_events


HARDWARE_ACTIVITY_CHANNEL = "hardware"
HARDWARE_ACTION_EVENT_TYPE = "hardware_action"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _text(value: Any, fallback: str = "") -> str:
    token = str(value or "").strip()
    return token or fallback


def _event_payload(item: Dict[str, Any]) -> Dict[str, Any]:
    metadata = dict(item.get("metadata") or {}) if isinstance(item.get("metadata"), dict) else {}
    return {
        "id": _text(item.get("id")),
        "type": HARDWARE_ACTION_EVENT_TYPE,
        "gateway_id": _text(metadata.get("gateway_id")),
        "capability": _text(metadata.get("capability")),
        "status": _text(metadata.get("status"), "completed"),
        "duration_ms": int(metadata.get("duration_ms") or 0),
        "timestamp": _text(metadata.get("timestamp") or item.get("ts"), _utc_now_iso()),
    }


def _is_diagnostic_probe_event(item: Dict[str, Any]) -> bool:
    metadata = dict(item.get("metadata") or {}) if isinstance(item.get("metadata"), dict) else {}
    tokens = [
        _text(item.get("run_id")),
        _text(item.get("trace_id")),
        _text(metadata.get("run_id")),
        _text(metadata.get("trace_id")),
    ]
    return any(token.startswith("hardware-capability-probe-") for token in tokens)


def emit_hardware_action_event(
    *,
    workspace_id: str,
    gateway_id: str,
    capability: str,
    status: str,
    duration_ms: int,
    tenant_id: Optional[str] = None,
    run_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    timestamp: Optional[str] = None,
) -> Dict[str, Any] | None:
    normalized_status = _text(status, "completed").lower()
    if normalized_status not in {"completed", "failed"}:
        normalized_status = "completed"
    occurred_at = _text(timestamp, _utc_now_iso())
    payload = {
        "type": HARDWARE_ACTION_EVENT_TYPE,
        "gateway_id": _text(gateway_id),
        "capability": _text(capability),
        "status": normalized_status,
        "duration_ms": max(0, int(duration_ms or 0)),
        "timestamp": occurred_at,
    }
    try:
        return runtime_events.append_channel_event_item(
            channel=HARDWARE_ACTIVITY_CHANNEL,
            direction="system",
            event_type=HARDWARE_ACTION_EVENT_TYPE,
            text=f"{payload['capability']} {payload['status']}".strip(),
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            session_key=f"gateway:{payload['gateway_id'] or 'unknown'}",
            session_id=f"gateway:{payload['gateway_id'] or 'unknown'}",
            message_id=f"hardware-action:{uuid.uuid4().hex}",
            run_id=run_id,
            trace_id=trace_id,
            action=HARDWARE_ACTION_EVENT_TYPE,
            metadata=payload,
            event_id=f"hardware_action:{payload['gateway_id'] or 'unknown'}:{uuid.uuid4().hex}",
            occurred_at=occurred_at,
        )
    except Exception:
        return None


def list_hardware_action_events(*, workspace_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    safe_limit = max(1, min(int(limit or 20), 100))
    lock = getattr(runtime_events, "CHANNEL_EVENTS_LOCK", None)
    if lock is None:
        return []
    with lock:
        snapshot = list(getattr(runtime_events, "CHANNEL_EVENTS", []) or [])
    items: List[Dict[str, Any]] = []
    for item in snapshot:
        if not isinstance(item, dict):
            continue
        if _text(item.get("workspace_id")) != _text(workspace_id):
            continue
        if _text(item.get("channel")).lower() != HARDWARE_ACTIVITY_CHANNEL:
            continue
        if _text(item.get("event_type")).lower() != HARDWARE_ACTION_EVENT_TYPE:
            continue
        if _is_diagnostic_probe_event(item):
            continue
        items.append(_event_payload(item))
        if len(items) >= safe_limit:
            break
    return items


async def iter_hardware_action_sse(*, workspace_id: str) -> AsyncIterator[Dict[str, str]]:
    seen: set[str] = set()
    for item in reversed(list_hardware_action_events(workspace_id=workspace_id, limit=5)):
        item_id = _text(item.get("id")) or uuid.uuid4().hex
        seen.add(item_id)
        yield {"event": HARDWARE_ACTION_EVENT_TYPE, "data": json.dumps(item, separators=(",", ":"))}
    while True:
        for item in reversed(list_hardware_action_events(workspace_id=workspace_id, limit=20)):
            item_id = _text(item.get("id")) or uuid.uuid4().hex
            if item_id in seen:
                continue
            seen.add(item_id)
            yield {"event": HARDWARE_ACTION_EVENT_TYPE, "data": json.dumps(item, separators=(",", ":"))}
        await asyncio.sleep(0.5)
