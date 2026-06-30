from __future__ import annotations

from typing import Any, Callable, Dict, Optional


class AutopilotEventBridgeService:
    def __init__(
        self,
        *,
        init_runtime: Callable[[], None],
        event_service: Callable[[], Any],
    ) -> None:
        self.init_runtime = init_runtime
        self.event_service = event_service

    def record_channel_event(
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
        self.init_runtime()
        return self.event_service().record_event(
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

    def append_channel_dead_letter(
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
        self.init_runtime()
        self.event_service().append_dead_letter(
            channel=channel,
            direction=direction,
            event_type=event_type,
            reason=reason,
            text=text,
            workspace_id=workspace_id,
            session_key=session_key,
            run_id=run_id,
            action=action,
            connector_id=connector_id,
            trace_id=trace_id,
            source_event_id=source_event_id,
            metadata=metadata,
        )

    def record_channel_event_throttled(
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
        self.init_runtime()
        return self.event_service().record_event_throttled(
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
            dedupe_seconds=dedupe_seconds,
            record_event_func=record_event_func,
        )
