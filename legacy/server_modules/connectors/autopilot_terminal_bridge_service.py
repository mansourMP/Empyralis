from __future__ import annotations

from typing import Any, Callable, Dict, Optional


class AutopilotTerminalBridgeService:
    def __init__(
        self,
        *,
        init_runtime: Callable[[], None],
        telegram_terminal_service: Callable[[], Any],
        telegram_supervisor_service: Callable[[], Any],
        autopilot_status_service: Callable[[], Any],
        autopilot_endpoint_service: Callable[[], Any],
        telegram_enabled: bool,
        telegram_default_profile: str,
        telegram_catalog: Dict[str, Any],
        telegram_webhook_path: str,
        whatsapp_enabled: bool,
        whatsapp_default_profile: str,
        whatsapp_catalog: Dict[str, Any],
        whatsapp_webhook_path: str,
    ) -> None:
        self.init_runtime = init_runtime
        self.telegram_terminal_service = telegram_terminal_service
        self.telegram_supervisor_service = telegram_supervisor_service
        self.autopilot_status_service = autopilot_status_service
        self.autopilot_endpoint_service = autopilot_endpoint_service
        self.telegram_enabled = bool(telegram_enabled)
        self.telegram_default_profile = str(telegram_default_profile or "")
        self.telegram_catalog = telegram_catalog
        self.telegram_webhook_path = str(telegram_webhook_path or "")
        self.whatsapp_enabled = bool(whatsapp_enabled)
        self.whatsapp_default_profile = str(whatsapp_default_profile or "")
        self.whatsapp_catalog = whatsapp_catalog
        self.whatsapp_webhook_path = str(whatsapp_webhook_path or "")

    async def handle_telegram_send_message(
        self,
        *,
        text: str,
        workspace_id: Optional[str] = None,
        session_key: Optional[str] = None,
        chat_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.init_runtime()
        return await self.telegram_terminal_service().handle_send_message(
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
        self.init_runtime()
        return await self.telegram_terminal_service().handle_autopilot_test_message(
            text=text,
            workspace_id=workspace_id,
            session_key=session_key,
            chat_id=chat_id,
            connector_id=connector_id,
            sender_id=sender_id,
            timeout_seconds=timeout_seconds,
        )

    def run_telegram_autopilot_forever(self) -> None:
        self.init_runtime()
        self.telegram_supervisor_service().run_forever()

    async def telegram_status_payload(self) -> Dict[str, Any]:
        self.init_runtime()
        return self.autopilot_status_service().telegram_status_payload()

    async def whatsapp_status_payload(self) -> Dict[str, Any]:
        self.init_runtime()
        return self.autopilot_status_service().whatsapp_status_payload()

    async def autopilot_profiles_payload(self) -> Dict[str, Any]:
        self.init_runtime()
        return self.autopilot_endpoint_service().autopilot_profiles_payload(
            telegram_enabled=self.telegram_enabled,
            telegram_default_profile=self.telegram_default_profile,
            telegram_catalog=self.telegram_catalog,
            telegram_webhook_path=self.telegram_webhook_path,
            whatsapp_enabled=self.whatsapp_enabled,
            whatsapp_default_profile=self.whatsapp_default_profile,
            whatsapp_catalog=self.whatsapp_catalog,
            whatsapp_webhook_path=self.whatsapp_webhook_path,
        )
