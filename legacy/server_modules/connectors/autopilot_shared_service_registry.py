from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from server_modules.connectors.autopilot_endpoint_service import AutopilotEndpointService
from server_modules.connectors.autopilot_event_service import AutopilotEventService
from server_modules.connectors.autopilot_status_service import AutopilotStatusService


class AutopilotSharedServiceRegistry:
    def __init__(
        self,
        *,
        normalize_workspace_id: Callable[[Any], str],
        append_channel_event: Callable[..., Any],
        utc_now_iso: Callable[[], str],
        truncate_one_line: Callable[[str, int], str],
        json_safe: Callable[[Any], Any],
        dead_letter_lock: Any,
        read_dead_letter_json: Callable[[Any, Any], Dict[str, Any]],
        write_dead_letter_json: Callable[[Any, Dict[str, Any]], Any],
        dead_letter_file: Any,
        dead_letter_limit: int,
        collapse_whitespace: Callable[[str], str],
        telegram_snapshot: Callable[[], Dict[str, Any]],
        telegram_list_entries: Callable[[], List[Dict[str, Any]]],
        resolve_telegram_profile: Callable[[Dict[str, Any]], Dict[str, Any]],
        telegram_webhook_path: str = "/channels/telegram/webhook/{connector_id}",
        telegram_public_base_url: Any = "",
        telegram_webhook_secret_configured: Any = False,
        telegram_delivery_mode: Any = "polling",
        whatsapp_snapshot: Callable[[], Dict[str, Any]],
        whatsapp_list_entries: Callable[[], List[Dict[str, Any]]],
        resolve_whatsapp_profile: Callable[[Dict[str, Any]], Dict[str, Any]],
        whatsapp_webhook_path: str = "/channels/whatsapp/twilio/webhook",
        whatsapp_public_base_url: Any = "",
        whatsapp_webhook_secret_configured: Any = False,
    ) -> None:
        self.normalize_workspace_id = normalize_workspace_id
        self.append_channel_event = append_channel_event
        self.utc_now_iso = utc_now_iso
        self.truncate_one_line = truncate_one_line
        self.json_safe = json_safe
        self.dead_letter_lock = dead_letter_lock
        self.read_dead_letter_json = read_dead_letter_json
        self.write_dead_letter_json = write_dead_letter_json
        self.dead_letter_file = dead_letter_file
        self.dead_letter_limit = int(dead_letter_limit or 0)
        self.collapse_whitespace = collapse_whitespace
        self.telegram_snapshot = telegram_snapshot
        self.telegram_list_entries = telegram_list_entries
        self.resolve_telegram_profile = resolve_telegram_profile
        self.telegram_webhook_path = str(telegram_webhook_path or "/channels/telegram/webhook/{connector_id}")
        self.telegram_public_base_url = telegram_public_base_url
        self.telegram_webhook_secret_configured = telegram_webhook_secret_configured
        self.telegram_delivery_mode = telegram_delivery_mode
        self.whatsapp_snapshot = whatsapp_snapshot
        self.whatsapp_list_entries = whatsapp_list_entries
        self.resolve_whatsapp_profile = resolve_whatsapp_profile
        self.whatsapp_webhook_path = str(whatsapp_webhook_path or "/channels/whatsapp/twilio/webhook")
        self.whatsapp_public_base_url = whatsapp_public_base_url
        self.whatsapp_webhook_secret_configured = whatsapp_webhook_secret_configured

        self._event_service: Optional[AutopilotEventService] = None
        self._status_service: Optional[AutopilotStatusService] = None
        self._endpoint_service: Optional[AutopilotEndpointService] = None

    def autopilot_event_service(self) -> AutopilotEventService:
        if self._event_service is None:
            self._event_service = AutopilotEventService(
                append_channel_event=self.append_channel_event,
                utc_now_iso=self.utc_now_iso,
                normalize_workspace_id=self.normalize_workspace_id,
                truncate_one_line=self.truncate_one_line,
                json_safe=self.json_safe,
                dead_letter_lock=self.dead_letter_lock,
                read_json=self.read_dead_letter_json,
                write_json=self.write_dead_letter_json,
                dead_letter_file=self.dead_letter_file,
                dead_letter_limit=self.dead_letter_limit,
                collapse_whitespace=self.collapse_whitespace,
            )
        return self._event_service

    def autopilot_status_service(self) -> AutopilotStatusService:
        if self._status_service is None:
            self._status_service = AutopilotStatusService(
                normalize_workspace_id=self.normalize_workspace_id,
                telegram_snapshot=self.telegram_snapshot,
                telegram_list_entries=self.telegram_list_entries,
                resolve_telegram_profile=self.resolve_telegram_profile,
                telegram_webhook_path=self.telegram_webhook_path,
                telegram_public_base_url=self.telegram_public_base_url,
                telegram_webhook_secret_configured=self.telegram_webhook_secret_configured,
                telegram_delivery_mode=self.telegram_delivery_mode,
                whatsapp_snapshot=self.whatsapp_snapshot,
                whatsapp_list_entries=self.whatsapp_list_entries,
                resolve_whatsapp_profile=self.resolve_whatsapp_profile,
                whatsapp_webhook_path=self.whatsapp_webhook_path,
                whatsapp_public_base_url=self.whatsapp_public_base_url,
                whatsapp_webhook_secret_configured=self.whatsapp_webhook_secret_configured,
            )
        return self._status_service

    def autopilot_endpoint_service(self) -> AutopilotEndpointService:
        if self._endpoint_service is None:
            self._endpoint_service = AutopilotEndpointService()
        return self._endpoint_service
