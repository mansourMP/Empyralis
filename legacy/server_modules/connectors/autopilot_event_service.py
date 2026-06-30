from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Callable, Dict, Optional


class AutopilotEventService:
    def __init__(
        self,
        *,
        append_channel_event: Callable[..., Any],
        utc_now_iso: Callable[[], str],
        normalize_workspace_id: Callable[[Any], str],
        truncate_one_line: Callable[[str, int], str],
        json_safe: Callable[[Any], Any],
        dead_letter_lock: Any,
        read_json: Callable[[Any, Any], Dict[str, Any]],
        write_json: Callable[[Any, Dict[str, Any]], Any],
        dead_letter_file: Any,
        dead_letter_limit: int,
        collapse_whitespace: Callable[[str], str],
    ) -> None:
        self.append_channel_event = append_channel_event
        self.utc_now_iso = utc_now_iso
        self.normalize_workspace_id = normalize_workspace_id
        self.truncate_one_line = truncate_one_line
        self.json_safe = json_safe
        self.dead_letter_lock = dead_letter_lock
        self.read_json = read_json
        self.write_json = write_json
        self.dead_letter_file = dead_letter_file
        self.dead_letter_limit = max(1, int(dead_letter_limit or 1))
        self.collapse_whitespace = collapse_whitespace
        self._dedupe_lock = threading.Lock()
        self._dedupe_index: Dict[str, float] = {}

    def record_event(
        self,
        *,
        channel: str,
        direction: str,
        event_type: str,
        text: str = "",
        workspace_id: Optional[str] = None,
        session_key: Optional[str] = None,
        session_id: Optional[str] = None,
        message_id: Optional[str] = None,
        parent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        action: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        try:
            return self.append_channel_event(
                channel=channel,
                direction=direction,
                event_type=event_type,
                text=text,
                workspace_id=workspace_id,
                session_key=session_key,
                session_id=session_id,
                message_id=message_id,
                parent_id=parent_id,
                run_id=run_id,
                action=action,
                metadata=metadata or {},
            )
        except Exception:
            return None

    def append_dead_letter(
        self,
        *,
        channel: str,
        direction: str,
        event_type: str,
        reason: str,
        text: str = "",
        workspace_id: str = "",
        session_key: str = "",
        run_id: str = "",
        action: str = "",
        connector_id: str = "",
        trace_id: str = "",
        source_event_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        item = {
            "id": str(uuid.uuid4()),
            "ts": self.utc_now_iso(),
            "channel": str(channel or "").strip().lower(),
            "direction": str(direction or "").strip().lower() or "outbound",
            "event_type": str(event_type or "").strip().lower() or "message",
            "workspace_id": self.normalize_workspace_id(workspace_id),
            "session_key": str(session_key or "").strip(),
            "run_id": str(run_id or "").strip(),
            "action": str(action or "").strip().lower(),
            "reason": self.truncate_one_line(str(reason or "").strip(), 240),
            "text": self.truncate_one_line(str(text or "").strip(), 1600),
            "connector_id": str(connector_id or "").strip(),
            "trace_id": str(trace_id or "").strip(),
            "source_event_id": str(source_event_id or "").strip(),
            "metadata": self.json_safe(metadata if isinstance(metadata, dict) else {}),
        }
        with self.dead_letter_lock:
            payload = self.read_json(self.dead_letter_file, {"version": 1, "items": []})
            items = payload.get("items") if isinstance(payload.get("items"), list) else []
            items.insert(0, item)
            payload["version"] = 1
            payload["updated_at"] = self.utc_now_iso()
            payload["items"] = items[: self.dead_letter_limit]
            self.write_json(self.dead_letter_file, payload)

    def record_event_throttled(
        self,
        *,
        channel: str,
        direction: str,
        event_type: str,
        text: str = "",
        workspace_id: Optional[str] = None,
        session_key: Optional[str] = None,
        session_id: Optional[str] = None,
        message_id: Optional[str] = None,
        parent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        action: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        dedupe_seconds: float = 30.0,
        record_event_func: Optional[Callable[..., Optional[Dict[str, Any]]]] = None,
    ) -> bool:
        normalized_workspace_id = self.normalize_workspace_id(workspace_id)
        normalized_text = self.collapse_whitespace(str(text or "").strip().lower())
        key = "|".join(
            [
                str(channel or "").strip().lower(),
                str(direction or "").strip().lower(),
                str(event_type or "").strip().lower(),
                str(action or "").strip().lower(),
                str(normalized_workspace_id or ""),
                normalized_text[:220],
            ]
        )
        now_ts = time.time()
        window = max(0.0, float(dedupe_seconds))
        if window > 0.0:
            with self._dedupe_lock:
                last_ts = float(self._dedupe_index.get(key) or 0.0)
                if (now_ts - last_ts) < window:
                    return False
                self._dedupe_index[key] = now_ts
                if len(self._dedupe_index) > 2048:
                    cutoff = now_ts - max(window * 4.0, 120.0)
                    stale = [k for k, ts in self._dedupe_index.items() if ts < cutoff]
                    for stale_key in stale[:1024]:
                        self._dedupe_index.pop(stale_key, None)
        record_event = record_event_func or self.record_event
        record_event(
            channel=channel,
            direction=direction,
            event_type=event_type,
            text=text,
            workspace_id=workspace_id,
            session_key=session_key,
            session_id=session_id,
            message_id=message_id,
            parent_id=parent_id,
            run_id=run_id,
            action=action,
            metadata=metadata,
        )
        return True
