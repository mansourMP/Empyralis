"""In-memory fake control_plane_repository for activity ledger tests.

Inject this into tool-call smoke tests to exercise the full ledger write
path (activity_ledger_service → repo) without a real Postgres connection.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


class FakeActivityLedgerRepo:
    """Stores activity ledger events in memory.

    Patch the three ledger functions on
    ``server_modules.activity_ledger_service.control_plane_repository``
    with the corresponding methods of an instance of this class.
    """

    def __init__(self) -> None:
        self.events: Dict[str, Dict[str, Any]] = {}
        # (tenant_id, workspace_id) -> [event_id, ...]
        self._index: Dict[Tuple[str, str], List[str]] = {}

    # ------------------------------------------------------------------
    # Public API — mirrors control_plane_repository
    # ------------------------------------------------------------------

    async def append_activity_ledger_event(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_type: str,
        actor_id: str,
        event_class: str,
        detail_level: str = "feed_summary",
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
        resolved_event_id = str(event_id or f"aevt_{uuid.uuid4().hex[:16]}").strip()
        now = datetime.now(timezone.utc)
        row: Dict[str, Any] = {
            "id": resolved_event_id,
            "tenant_id": str(tenant_id or "").strip(),
            "workspace_id": str(workspace_id or "").strip(),
            "actor_type": str(actor_type or "system").strip().lower() or "system",
            "actor_id": str(actor_id or "").strip(),
            "event_class": str(event_class or "").strip(),
            "detail_level": str(detail_level or "feed_summary").strip(),
            "install_id": str(install_id or "").strip() or None,
            "app_id": str(app_id or "").strip() or None,
            "run_id": str(run_id or "").strip() or None,
            "thread_id": str(thread_id or "").strip() or None,
            "session_key": str(session_key or "").strip() or None,
            "channel": str(channel or "").strip().lower() or None,
            "direction": str(direction or "").strip().lower() or None,
            "action": str(action or "").strip().lower() or None,
            "trace_id": str(trace_id or "").strip() or None,
            "title": str(title or "")[:140],
            "summary": str(summary or "")[:320],
            "status": str(status or "logged").strip().lower() or "logged",
            "review_required": bool(review_required),
            "artifacts": list(artifacts or []),
            "payload": dict(payload or {}),
            "metadata": dict(metadata or {}),
            "created_at": now,
            "updated_at": now,
        }
        self.events[resolved_event_id] = row
        key = (row["tenant_id"], row["workspace_id"])
        self._index.setdefault(key, []).append(resolved_event_id)
        return row

    async def list_activity_ledger_events(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        event_classes: Optional[List[str]] = None,
        exclude_event_classes: Optional[List[str]] = None,
        detail_levels: Optional[List[str]] = None,
        actor_type: Optional[str] = None,
        actor_id: Optional[str] = None,
        install_id: Optional[str] = None,
        app_id: Optional[str] = None,
        run_id: Optional[str] = None,
        thread_id: Optional[str] = None,
        channel: Optional[str] = None,
        direction: Optional[str] = None,
        session_key: Optional[str] = None,
        action: Optional[str] = None,
        trace_id: Optional[str] = None,
        status: Optional[str] = None,
        since_created_at: Any = None,
        since_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        key = (str(tenant_id or "").strip(), str(workspace_id or "").strip())
        ids = self._index.get(key, [])
        results: List[Dict[str, Any]] = []
        for eid in reversed(ids):
            ev = self.events.get(eid)
            if ev is None:
                continue
            if event_classes and ev.get("event_class") not in event_classes:
                continue
            if exclude_event_classes and ev.get("event_class") in exclude_event_classes:
                continue
            if actor_id and ev.get("actor_id") != actor_id:
                continue
            if channel and ev.get("channel") != channel:
                continue
            if action and ev.get("action") != action:
                continue
            if run_id and ev.get("run_id") != run_id:
                continue
            results.append(ev)
            if len(results) >= limit:
                break
        return results

    async def get_activity_ledger_event(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        event_id: str,
    ) -> Optional[Dict[str, Any]]:
        return self.events.get(str(event_id or "").strip())
