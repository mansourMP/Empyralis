from __future__ import annotations

from datetime import datetime, timezone
import threading
from typing import Any, Dict, List, Optional

from server_modules import activity_ledger_service
from server_modules.api_contract import ApiActivityListResponse, ApiNotificationListResponse


_NOTIFICATION_READ_STATE_LOCK = threading.Lock()
_NOTIFICATION_READ_AT_BY_ID: Dict[str, str] = {}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def register_inbox_routes(app) -> None:
    import server as _server

    module_globals = globals()
    module_globals.update(_server.__dict__)

    def _notification_runtime_db_path():
        return getattr(_server, "ORION_RUNTIME_STATE_DB", globals().get("ORION_RUNTIME_STATE_DB", None))

    def _notification_with_read_state(item: Any) -> Dict[str, Any]:
        record = dict(item) if isinstance(item, dict) else {}
        notification_id = str(record.get("id") or "").strip()
        if notification_id:
            with _NOTIFICATION_READ_STATE_LOCK:
                read_at = _NOTIFICATION_READ_AT_BY_ID.get(notification_id)
            if read_at:
                record["read_at"] = read_at
        return record

    def _notification_payload(
        *,
        limit: int,
        current_user: Any,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        channel: Optional[str] = None,
        session_key: Optional[str] = None,
        direction: Optional[str] = None,
        action: Optional[str] = None,
        run_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        include_sessions: bool = True,
        session_limit: int = ORION_CHANNEL_SESSIONS_LIMIT,
    ) -> dict[str, Any]:
        from server_modules.auth import allowed_workspace_ids, enforce_workspace_access

        safe_limit = max(1, min(limit, 500))
        requested_workspace_id = (
            enforce_workspace_access(current_user, workspace_id, tenant_id=tenant_id, minimum_role="viewer")
            if workspace_id
            else None
        )
        allowed_workspaces = allowed_workspace_ids(current_user)
        with CHANNEL_EVENTS_LOCK:
            items = list(CHANNEL_EVENTS)
        filtered = [
            item
            for item in items
            if (not tenant_id or str(item.get("tenant_id") or "").strip() == str(tenant_id).strip())
            and (not requested_workspace_id or str(item.get("workspace_id") or "").strip() == requested_workspace_id)
            and (allowed_workspaces is None or str(item.get("workspace_id") or "").strip() in allowed_workspaces)
            if _channel_event_matches(
                item=item,
                workspace_id=requested_workspace_id,
                tenant_id=tenant_id,
                channel=channel,
                session_key=session_key,
                direction=direction,
                action=action,
                run_id=run_id,
                trace_id=trace_id,
            )
        ]
        payload = [_notification_with_read_state(item) for item in filtered[:safe_limit]]
        sessions: List[Dict[str, Any]] = []
        if include_sessions:
            sessions = _summarize_channel_sessions(filtered, limit=session_limit)
        return {
            "items": payload,
            "count": len(payload),
            "total": len(filtered),
            "sessions": sessions,
            "session_count": len(sessions),
            "stream": False,
        }

    def _mark_notifications_read(
        *,
        current_user: Any,
        notification_ids: Optional[List[str]] = None,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        mark_all: bool = False,
    ) -> Dict[str, Any]:
        from server_modules.auth import allowed_workspace_ids, enforce_workspace_access

        normalized_ids = [
            str(item or "").strip()
            for item in (notification_ids or [])
            if str(item or "").strip()
        ]
        target_ids: List[str] = []
        requested_workspace_id = (
            enforce_workspace_access(current_user, workspace_id, tenant_id=tenant_id, minimum_role="viewer")
            if workspace_id
            else None
        )
        allowed_workspaces = allowed_workspace_ids(current_user)
        if mark_all:
            with CHANNEL_EVENTS_LOCK:
                items = list(CHANNEL_EVENTS)
            filtered = [
                item for item in items
                if (not tenant_id or str(item.get("tenant_id") or "").strip() == str(tenant_id).strip())
                and (not requested_workspace_id or str(item.get("workspace_id") or "").strip() == requested_workspace_id)
                and (allowed_workspaces is None or str(item.get("workspace_id") or "").strip() in allowed_workspaces)
                if _channel_event_matches(
                    item=item,
                    workspace_id=requested_workspace_id,
                    tenant_id=tenant_id,
                    channel=None,
                    session_key=None,
                    direction=None,
                    action=None,
                    run_id=None,
                    trace_id=None,
                )
            ]
            target_ids.extend(
                str(item.get("id") or "").strip()
                for item in filtered
                if isinstance(item, dict) and str(item.get("id") or "").strip()
            )
        target_ids.extend(normalized_ids)
        deduped_ids: List[str] = []
        seen = set()
        for item in target_ids:
            if not item or item in seen:
                continue
            seen.add(item)
            deduped_ids.append(item)
        marked_at = _utc_now_iso()
        with _NOTIFICATION_READ_STATE_LOCK:
            for notification_id in deduped_ids:
                _NOTIFICATION_READ_AT_BY_ID[notification_id] = marked_at
        return {
            "status": "ok",
            "marked_count": len(deduped_ids),
            "marked_ids": deduped_ids,
        }

    def _notification_stream_response(
        *,
        allowed_workspaces: Optional[set[str]] = None,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
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
        safe_heartbeat = max(1.0, min(float(heartbeat_seconds), 60.0))
        return EventSourceResponse(
            _iter_channel_events_stream(
                allowed_workspace_ids=allowed_workspaces,
                workspace_id=workspace_id,
                tenant_id=tenant_id,
                channel=channel,
                session_key=session_key,
                direction=direction,
                action=action,
                run_id=run_id,
                trace_id=trace_id,
                since_id=since_id,
                since_ts=since_ts,
                include_backlog=include_backlog,
                poll_seconds=poll_seconds,
                heartbeat_seconds=heartbeat_seconds,
                timeout_seconds=timeout_seconds,
                limit=limit,
            ),
            ping=max(3, int(safe_heartbeat)),
        )

    @app.get("/events/inbox", dependencies=[Depends(require_api_key)])
    async def get_channel_events(
        limit: int = 80,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        channel: Optional[str] = None,
        session_key: Optional[str] = None,
        direction: Optional[str] = None,
        action: Optional[str] = None,
        run_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        include_sessions: bool = True,
        session_limit: int = ORION_CHANNEL_SESSIONS_LIMIT,
        current_user=Depends(require_api_key),
    ):
        return _notification_payload(
            limit=limit,
            current_user=current_user,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            channel=channel,
            session_key=session_key,
            direction=direction,
            action=action,
            run_id=run_id,
            trace_id=trace_id,
            include_sessions=include_sessions,
            session_limit=session_limit,
        )

    @app.get("/events/inbox/stream", dependencies=[Depends(require_api_key)])
    async def stream_channel_events(
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
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
        current_user=Depends(require_api_key),
    ):
        from server_modules.auth import enforce_workspace_access
        from server_modules.auth import allowed_workspace_ids

        requested_workspace_id = (
            enforce_workspace_access(current_user, workspace_id, tenant_id=tenant_id, minimum_role="viewer")
            if workspace_id
            else None
        )
        allowed_workspaces = allowed_workspace_ids(current_user)
        return _notification_stream_response(
            allowed_workspaces=allowed_workspaces,
            tenant_id=tenant_id,
            workspace_id=requested_workspace_id,
            channel=channel,
            session_key=session_key,
            direction=direction,
            action=action,
            run_id=run_id,
            trace_id=trace_id,
            since_id=since_id,
            since_ts=since_ts,
            include_backlog=include_backlog,
            poll_seconds=poll_seconds,
            heartbeat_seconds=heartbeat_seconds,
            timeout_seconds=timeout_seconds,
            limit=limit,
        )

    @app.get("/notifications", dependencies=[Depends(require_api_key)], response_model=ApiNotificationListResponse)
    async def get_notifications(
        limit: int = 80,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        channel: Optional[str] = None,
        session_key: Optional[str] = None,
        direction: Optional[str] = None,
        action: Optional[str] = None,
        run_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        include_sessions: bool = True,
        session_limit: int = ORION_CHANNEL_SESSIONS_LIMIT,
        stream: bool = False,
        since_id: Optional[str] = None,
        since_ts: Optional[str] = None,
        include_backlog: bool = False,
        poll_seconds: float = 0.35,
        heartbeat_seconds: float = 5.0,
        timeout_seconds: float = 25.0,
        current_user=Depends(require_api_key),
    ):
        from server_modules.auth import allowed_workspace_ids, enforce_workspace_access
        from server_modules import notification_service

        requested_workspace_id = (
            enforce_workspace_access(current_user, workspace_id, tenant_id=tenant_id, minimum_role="viewer")
            if workspace_id
            else None
        )
        allowed_workspaces = allowed_workspace_ids(current_user)
        if stream:
            safe_heartbeat = max(1.0, min(float(heartbeat_seconds), 60.0))
            return EventSourceResponse(
                notification_service.iter_notifications_stream(
                    reader_key=notification_service.reader_key_for_current_user(current_user),
                    allowed_workspace_ids=allowed_workspaces,
                    workspace_id=requested_workspace_id,
                    tenant_id=tenant_id,
                    channel=channel,
                    session_key=session_key,
                    direction=direction,
                    action=action,
                    run_id=run_id,
                    trace_id=trace_id,
                    since_id=since_id,
                    since_ts=since_ts,
                    include_backlog=include_backlog,
                    poll_seconds=poll_seconds,
                    heartbeat_seconds=heartbeat_seconds,
                    timeout_seconds=timeout_seconds,
                    limit=limit,
                    db_path=_notification_runtime_db_path(),
                ),
                ping=max(3, int(safe_heartbeat)),
            )
        return notification_service.list_notification_payload(
            current_user=current_user,
            limit=limit,
            tenant_id=tenant_id,
            workspace_id=requested_workspace_id,
            channel=channel,
            session_key=session_key,
            direction=direction,
            action=action,
            run_id=run_id,
            trace_id=trace_id,
            include_sessions=include_sessions,
            session_limit=session_limit,
            db_path=_notification_runtime_db_path(),
        )

    @app.post("/notifications", dependencies=[Depends(require_api_key)])
    async def mark_notifications_read(body: Optional[Dict[str, Any]] = None, current_user=Depends(require_api_key)):
        from server_modules import notification_service

        payload = body if isinstance(body, dict) else {}
        notification_ids = payload.get("notification_ids")
        tenant_id = payload.get("tenant_id")
        workspace_id = payload.get("workspace_id")
        mark_all = bool(payload.get("mark_all"))
        if not mark_all and not isinstance(notification_ids, list):
            raise HTTPException(status_code=400, detail="notification_ids or mark_all is required.")
        return notification_service.mark_notifications_read(
            current_user=current_user,
            notification_ids=notification_ids if isinstance(notification_ids, list) else None,
            tenant_id=str(tenant_id or "").strip() or None,
            workspace_id=str(workspace_id or "").strip() or None,
            mark_all=mark_all,
            db_path=_notification_runtime_db_path(),
        )

    @app.post("/notifications/devices", dependencies=[Depends(require_api_key)])
    async def register_notification_device(body: Optional[Dict[str, Any]] = None, current_user=Depends(require_api_key)):
        from server_modules import control_plane_repository, notification_service

        payload = body if isinstance(body, dict) else {}
        workspace_id = str(payload.get("workspace_id") or "").strip()
        device_id = str(payload.get("device_id") or "").strip()
        push_token = str(payload.get("push_token") or "").strip()
        if not workspace_id:
            raise HTTPException(status_code=400, detail="workspace_id is required.")
        if not device_id:
            raise HTTPException(status_code=400, detail="device_id is required.")
        if not push_token:
            raise HTTPException(status_code=400, detail="push_token is required.")
        capabilities = payload.get("capabilities")
        workspace = await control_plane_repository.get_workspace_by_id(workspace_id)
        return notification_service.register_notification_device(
            current_user=current_user,
            workspace_id=workspace_id,
            device_id=device_id,
            push_token=push_token,
            provider=str(payload.get("provider") or "expo").strip() or "expo",
            platform=str(payload.get("platform") or "").strip(),
            device_name=str(payload.get("device_name") or "").strip(),
            app_id=str(payload.get("app_id") or "").strip(),
            capabilities=capabilities if isinstance(capabilities, list) else None,
            workspace=workspace,
            db_path=_notification_runtime_db_path(),
        )

    @app.get("/activity/timeline", dependencies=[Depends(require_api_key)], response_model=ApiActivityListResponse)
    async def get_activity_timeline(
        workspace_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
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
        current_user=Depends(require_api_key),
    ):
        from server_modules.auth import enforce_workspace_access, workspace_tenant_id

        resolved_workspace_id = enforce_workspace_access(
            current_user,
            workspace_id,
            tenant_id=tenant_id,
            minimum_role="viewer",
        )
        resolved_tenant_id = str(tenant_id or "").strip() or workspace_tenant_id(current_user, resolved_workspace_id)
        return await activity_ledger_service.list_activity_timeline_payload(
            tenant_id=resolved_tenant_id,
            workspace_id=resolved_workspace_id,
            limit=limit,
            trace_id=trace_id,
            event_class=event_class,
            detail_level=detail_level,
            actor_type=actor_type,
            actor_id=actor_id,
            install_id=install_id,
            app_id=app_id,
            run_id=run_id,
            thread_id=thread_id,
        )

    @app.get("/events/inbox/sessions", dependencies=[Depends(require_api_key)])
    async def get_channel_sessions(
        limit: int = ORION_CHANNEL_SESSIONS_LIMIT,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        channel: Optional[str] = None,
        session_key: Optional[str] = None,
        direction: Optional[str] = None,
        action: Optional[str] = None,
        run_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        current_user=Depends(require_api_key),
    ):
        from server_modules.auth import allowed_workspace_ids, enforce_workspace_access

        safe_limit = max(1, min(limit, max(1, ORION_CHANNEL_SESSIONS_LIMIT)))
        requested_workspace_id = (
            enforce_workspace_access(current_user, workspace_id, tenant_id=tenant_id, minimum_role="viewer")
            if workspace_id
            else None
        )
        allowed_workspaces = allowed_workspace_ids(current_user)
        with CHANNEL_EVENTS_LOCK:
            items = list(CHANNEL_EVENTS)
        filtered = [
            item
            for item in items
            if (not tenant_id or str(item.get("tenant_id") or "").strip() == str(tenant_id).strip())
            and (not requested_workspace_id or str(item.get("workspace_id") or "").strip() == requested_workspace_id)
            and (allowed_workspaces is None or str(item.get("workspace_id") or "").strip() in allowed_workspaces)
            if _channel_event_matches(
                item=item,
                workspace_id=requested_workspace_id,
                tenant_id=tenant_id,
                channel=channel,
                session_key=session_key,
                direction=direction,
                action=action,
                run_id=run_id,
                trace_id=trace_id,
            )
        ]
        sessions_all = _summarize_channel_sessions(filtered, limit=None)
        payload = sessions_all[:safe_limit]
        return {
            "items": payload,
            "count": len(payload),
            "total": len(sessions_all),
        }

    @app.get("/channels/events/trace", dependencies=[Depends(require_api_key)])
    async def get_channel_event_trace(
        run_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        channel: Optional[str] = None,
        limit: int = 200,
        current_user=Depends(require_api_key),
    ):
        from server_modules.auth import allowed_workspace_ids, enforce_workspace_access

        if not str(run_id or "").strip() and not str(trace_id or "").strip():
            raise HTTPException(status_code=400, detail="run_id or trace_id is required.")
        safe_limit = max(1, min(limit, 500))
        run_id_value = str(run_id or "").strip()
        trace_id_value = str(trace_id or "").strip()
        requested_workspace_id = (
            enforce_workspace_access(current_user, workspace_id, tenant_id=tenant_id, minimum_role="viewer")
            if workspace_id
            else None
        )
        allowed_workspaces = allowed_workspace_ids(current_user)
        with CHANNEL_EVENTS_LOCK:
            snapshot = list(CHANNEL_EVENTS)
        ordered = list(reversed(snapshot))
        items: List[Dict[str, Any]] = []
        for item in ordered:
            if not isinstance(item, dict):
                continue
            if tenant_id and str(item.get("tenant_id") or "").strip() != str(tenant_id).strip():
                continue
            if requested_workspace_id and str(item.get("workspace_id") or "").strip() != _normalize_workspace_id(requested_workspace_id):
                continue
            if allowed_workspaces is not None and str(item.get("workspace_id") or "").strip() not in allowed_workspaces:
                continue
            if channel and str(item.get("channel") or "").strip().lower() != str(channel).strip().lower():
                continue
            if run_id_value and str(item.get("run_id") or "").strip() != run_id_value:
                continue
            item_trace = str(item.get("trace_id") or "").strip()
            if trace_id_value and item_trace != trace_id_value:
                continue
            items.append(item)
        payload = items[-safe_limit:] if len(items) > safe_limit else items
        trace_values = []
        seen_traces = set()
        for item in payload:
            token = str(item.get("trace_id") or "").strip()
            if token and token not in seen_traces:
                seen_traces.add(token)
                trace_values.append(token)
        return {
            "items": payload,
            "count": len(payload),
            "trace_ids": trace_values,
            "run_id": run_id_value or None,
            "trace_id": trace_id_value or None,
        }

    @app.get("/channels/events/dead_letters", dependencies=[Depends(require_api_key)])
    async def get_channel_dead_letters(limit: int = 80, channel: Optional[str] = None, run_id: Optional[str] = None):
        safe_limit = max(1, min(limit, 500))
        payload = _safe_read_json(ORION_CHANNEL_DEAD_LETTER_FILE, {"version": 1, "items": []})
        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        filtered: List[Dict[str, Any]] = []
        channel_value = str(channel or "").strip().lower()
        run_value = str(run_id or "").strip()
        for item in items:
            if not isinstance(item, dict):
                continue
            if channel_value and str(item.get("channel") or "").strip().lower() != channel_value:
                continue
            if run_value and str(item.get("run_id") or "").strip() != run_value:
                continue
            filtered.append(item)
        return {
            "items": filtered[:safe_limit],
            "count": len(filtered[:safe_limit]),
            "total": len(filtered),
            "source_file": str(ORION_CHANNEL_DEAD_LETTER_FILE),
            "limit_configured": int(ORION_CHANNEL_DEAD_LETTER_LIMIT),
        }
