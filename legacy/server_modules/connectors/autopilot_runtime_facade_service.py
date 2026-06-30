from __future__ import annotations

from typing import Any, Callable, Dict, Optional


class AutopilotRuntimeFacadeService:
    def __init__(
        self,
        *,
        global_namespace: Dict[str, Any],
        sync_server_globals: tuple[str, ...],
        server_getter: Callable[[], Any],
        server_setter: Callable[[Any], None],
        import_server: Callable[[], Any],
        state_bridge_service: Callable[[], Any],
        event_bridge_service: Callable[[], Any],
        terminal_bridge_service: Callable[[], Any],
        webhook_bridge_service: Callable[[], Any],
        telegram_webhook_bridge_service: Callable[[], Any],
    ) -> None:
        self.global_namespace = global_namespace
        self.sync_server_globals = tuple(sync_server_globals)
        self.server_getter = server_getter
        self.server_setter = server_setter
        self.import_server = import_server
        self.state_bridge_service = state_bridge_service
        self.event_bridge_service = event_bridge_service
        self.terminal_bridge_service = terminal_bridge_service
        self.webhook_bridge_service = webhook_bridge_service
        self.telegram_webhook_bridge_service = telegram_webhook_bridge_service

    def init_runtime(self) -> None:
        server = self.server_getter()
        if server is None:
            server = self.import_server()
            self.server_setter(server)
            for key, value in vars(server).items():
                if not key.startswith("__") and key not in self.global_namespace:
                    self.global_namespace[key] = value
        for key in self.sync_server_globals:
            if hasattr(server, key):
                self.global_namespace[key] = getattr(server, key)

    def load_telegram_autopilot_state(self) -> None:
        self.state_bridge_service().load_telegram_autopilot_state()

    def load_whatsapp_autopilot_state(self) -> None:
        self.state_bridge_service().load_whatsapp_autopilot_state()

    def telegram_autopilot_snapshot(self) -> Dict[str, Any]:
        return self.state_bridge_service().telegram_autopilot_snapshot()

    def whatsapp_autopilot_snapshot(self) -> Dict[str, Any]:
        return self.state_bridge_service().whatsapp_autopilot_snapshot()

    def whatsapp_autopilot_activate(self) -> None:
        self.state_bridge_service().whatsapp_autopilot_activate()

    def telegram_increment_processed_updates(self) -> None:
        self.state_bridge_service().telegram_increment_processed_updates()

    def telegram_set_connectors_seen(self, count: int) -> None:
        self.state_bridge_service().telegram_set_connectors_seen(count)

    def mark_telegram_autopilot_started(self, started_at: str) -> None:
        self.state_bridge_service().mark_telegram_autopilot_started(started_at)

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
        return self.event_bridge_service().record_channel_event(
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
        self.event_bridge_service().append_channel_dead_letter(
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
        return self.event_bridge_service().record_channel_event_throttled(
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

    async def handle_telegram_send_message(
        self,
        *,
        text: str,
        workspace_id: Optional[str] = None,
        session_key: Optional[str] = None,
        chat_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await self.terminal_bridge_service().handle_telegram_send_message(
            text=text,
            workspace_id=workspace_id,
            session_key=session_key,
            chat_id=chat_id,
        )

    async def handle_telegram_autopilot_test_message(
        self,
        *,
        text: str,
        workspace_id: Optional[str] = None,
        session_key: Optional[str] = None,
        chat_id: Optional[str] = None,
        connector_id: Optional[str] = None,
        sender_id: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        return await self.terminal_bridge_service().handle_telegram_autopilot_test_message(
            text=text,
            workspace_id=workspace_id,
            session_key=session_key,
            chat_id=chat_id,
            connector_id=connector_id,
            sender_id=sender_id,
            timeout_seconds=timeout_seconds,
        )

    async def handle_whatsapp_webhook(
        self,
        *,
        raw_body: bytes,
        request_url: str,
        twilio_signature: str,
        query_secret: str,
        header_secret: str,
    ) -> Any:
        return self.webhook_bridge_service().handle_webhook(
            raw_body=raw_body,
            request_url=request_url,
            twilio_signature=twilio_signature,
            query_secret=query_secret,
            header_secret=header_secret,
        )

    async def handle_telegram_webhook(
        self,
        *,
        raw_body: bytes,
        connector_id: str,
        header_secret: str,
    ) -> Any:
        return self.telegram_webhook_bridge_service().handle_webhook(
            raw_body=raw_body,
            connector_id=connector_id,
            header_secret=header_secret,
        )

    def run_telegram_autopilot_forever(self) -> None:
        self.terminal_bridge_service().run_telegram_autopilot_forever()

    async def telegram_status_payload(self) -> Dict[str, Any]:
        return await self.terminal_bridge_service().telegram_status_payload()

    async def whatsapp_status_payload(self) -> Dict[str, Any]:
        return await self.terminal_bridge_service().whatsapp_status_payload()

    async def autopilot_profiles_payload(self) -> Dict[str, Any]:
        return await self.terminal_bridge_service().autopilot_profiles_payload()
