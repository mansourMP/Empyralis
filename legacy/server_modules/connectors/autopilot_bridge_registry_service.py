from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from server_modules.connectors.autopilot_event_bridge_service import AutopilotEventBridgeService
from server_modules.connectors.autopilot_shared_service_registry import AutopilotSharedServiceRegistry
from server_modules.connectors.autopilot_state_bridge_service import AutopilotStateBridgeService
from server_modules.connectors.autopilot_terminal_bridge_service import AutopilotTerminalBridgeService
from server_modules.connectors.connector_webhook import ConnectorWebhook
from server_modules.connectors.whatsapp_webhook_bridge_service import WhatsAppWebhookBridgeService


class AutopilotBridgeRegistryService:
    """Compatibility registry for older bridge-registry callers and tests."""

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
        telegram_list_entries: Callable[[], Any],
        resolve_telegram_profile: Callable[[Dict[str, Any]], Dict[str, Any]],
        telegram_webhook_path: str,
        telegram_public_base_url: Any,
        telegram_webhook_secret_configured: Any,
        telegram_delivery_mode: Any,
        whatsapp_snapshot: Callable[[], Dict[str, Any]],
        whatsapp_list_entries: Callable[[], Any],
        resolve_whatsapp_profile: Callable[[Dict[str, Any]], Dict[str, Any]],
        init_runtime: Callable[[], Any],
        event_service: Callable[[], Any],
        telegram_terminal_service: Callable[[], Any],
        telegram_supervisor_service: Callable[[], Any],
        autopilot_status_service: Callable[[], Any],
        autopilot_endpoint_service: Callable[[], Any],
        telegram_enabled: bool,
        telegram_default_profile: str,
        telegram_catalog: Dict[str, Any],
        whatsapp_enabled: bool,
        whatsapp_default_profile: str,
        whatsapp_catalog: Dict[str, Any],
        whatsapp_webhook_path: str,
        whatsapp_public_base_url: Any,
        whatsapp_webhook_secret_configured: Any,
        telegram_state_service: Callable[[], Any],
        whatsapp_state_service: Callable[[], Any],
        telegram_runtime_service: Callable[[], Any],
        telegram_state: Dict[str, Any],
        telegram_lock: Any,
        safe_path_token: Callable[[Any], str],
        build_goal_with_profile: Callable[[str, Dict[str, Any]], str],
        workspace_connector_context: Callable[[str, str, str], Dict[str, Any]],
        extract_message: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]],
        build_goal_with_attachments: Callable[[str, Any], str],
        route_message: Callable[[str, Dict[str, Any]], Dict[str, Any]],
        telegram_parse_update: Callable[[bytes], Dict[str, Any]],
        telegram_webhook_auth_result: Callable[..., Any],
        telegram_handle_inbound: Callable[[str, Dict[str, Any]], Any],
        parse_form_urlencoded: Callable[[bytes], Dict[str, str]],
        webhook_auth_result: Callable[..., Any],
        resolve_inbound_connector: Callable[[Dict[str, Any]], Any],
        validate_webhook_signature: Callable[[str, Dict[str, str], str, str], bool],
        ingest_webhook: Callable[..., Any],
        twiml_response: Callable[[str], Any],
        error_response: Callable[[int, str], Any],
        telegram_webhook_enabled: bool,
        telegram_configured_webhook_secret: str,
        webhook_enabled: bool,
        configured_webhook_secret: str,
        shared_registry_class: Callable[..., Any] = AutopilotSharedServiceRegistry,
        event_bridge_class: Callable[..., Any] = AutopilotEventBridgeService,
        terminal_bridge_class: Callable[..., Any] = AutopilotTerminalBridgeService,
        state_bridge_class: Callable[..., Any] = AutopilotStateBridgeService,
        compatibility_bridge_class: Callable[..., Any] = ConnectorWebhook,
        telegram_webhook_bridge_class: Callable[..., Any] = ConnectorWebhook,
        webhook_bridge_class: Callable[..., Any] = WhatsAppWebhookBridgeService,
    ) -> None:
        self._deps = locals()
        self._deps.pop("self", None)
        self._shared_service_registry: Optional[Any] = None
        self._event_bridge_service: Optional[Any] = None
        self._terminal_bridge_service: Optional[Any] = None
        self._state_bridge_service: Optional[Any] = None
        self._compatibility_bridge_service: Optional[Any] = None
        self._telegram_webhook_bridge_service: Optional[Any] = None
        self._webhook_bridge_service: Optional[Any] = None

    def shared_service_registry(self) -> Any:
        if self._shared_service_registry is None:
            d = self._deps
            self._shared_service_registry = d["shared_registry_class"](
                normalize_workspace_id=d["normalize_workspace_id"],
                append_channel_event=d["append_channel_event"],
                utc_now_iso=d["utc_now_iso"],
                truncate_one_line=d["truncate_one_line"],
                json_safe=d["json_safe"],
                dead_letter_lock=d["dead_letter_lock"],
                read_dead_letter_json=d["read_dead_letter_json"],
                write_dead_letter_json=d["write_dead_letter_json"],
                dead_letter_file=d["dead_letter_file"],
                dead_letter_limit=d["dead_letter_limit"],
                collapse_whitespace=d["collapse_whitespace"],
                telegram_snapshot=d["telegram_snapshot"],
                telegram_list_entries=d["telegram_list_entries"],
                resolve_telegram_profile=d["resolve_telegram_profile"],
                telegram_webhook_path=d["telegram_webhook_path"],
                telegram_public_base_url=d["telegram_public_base_url"],
                telegram_webhook_secret_configured=d["telegram_webhook_secret_configured"],
                telegram_delivery_mode=d["telegram_delivery_mode"],
                whatsapp_snapshot=d["whatsapp_snapshot"],
                whatsapp_list_entries=d["whatsapp_list_entries"],
                resolve_whatsapp_profile=d["resolve_whatsapp_profile"],
                whatsapp_webhook_path=d["whatsapp_webhook_path"],
                whatsapp_public_base_url=d["whatsapp_public_base_url"],
                whatsapp_webhook_secret_configured=d["whatsapp_webhook_secret_configured"],
            )
        return self._shared_service_registry

    def event_bridge_service(self) -> Any:
        if self._event_bridge_service is None:
            d = self._deps
            self._event_bridge_service = d["event_bridge_class"](
                init_runtime=d["init_runtime"],
                event_service=d["event_service"],
            )
        return self._event_bridge_service

    def terminal_bridge_service(self) -> Any:
        if self._terminal_bridge_service is None:
            d = self._deps
            self._terminal_bridge_service = d["terminal_bridge_class"](
                init_runtime=d["init_runtime"],
                telegram_terminal_service=d["telegram_terminal_service"],
                telegram_supervisor_service=d["telegram_supervisor_service"],
                autopilot_status_service=d["autopilot_status_service"],
                autopilot_endpoint_service=d["autopilot_endpoint_service"],
                telegram_enabled=d["telegram_enabled"],
                telegram_default_profile=d["telegram_default_profile"],
                telegram_catalog=d["telegram_catalog"],
                telegram_webhook_path=d["telegram_webhook_path"],
                whatsapp_enabled=d["whatsapp_enabled"],
                whatsapp_default_profile=d["whatsapp_default_profile"],
                whatsapp_catalog=d["whatsapp_catalog"],
                whatsapp_webhook_path=d["whatsapp_webhook_path"],
            )
        return self._terminal_bridge_service

    def state_bridge_service(self) -> Any:
        if self._state_bridge_service is None:
            d = self._deps
            self._state_bridge_service = d["state_bridge_class"](
                telegram_state_service=d["telegram_state_service"],
                whatsapp_state_service=d["whatsapp_state_service"],
                telegram_runtime_service=d["telegram_runtime_service"],
                telegram_state=d["telegram_state"],
                telegram_lock=d["telegram_lock"],
            )
        return self._state_bridge_service

    def compatibility_bridge_service(self) -> Any:
        if self._compatibility_bridge_service is None:
            d = self._deps
            self._compatibility_bridge_service = d["compatibility_bridge_class"](
                safe_path_token=d["safe_path_token"],
                build_goal_with_profile=d["build_goal_with_profile"],
                workspace_connector_context=d["workspace_connector_context"],
                extract_message=d["extract_message"],
                build_goal_with_attachments=d["build_goal_with_attachments"],
                route_message=d["route_message"],
            )
        return self._compatibility_bridge_service

    def telegram_webhook_bridge_service(self) -> Any:
        if self._telegram_webhook_bridge_service is None:
            d = self._deps
            self._telegram_webhook_bridge_service = d["telegram_webhook_bridge_class"](
                init_runtime=d["init_runtime"],
                parse_update=d["telegram_parse_update"],
                webhook_auth_result=d["telegram_webhook_auth_result"],
                handle_inbound=d["telegram_handle_inbound"],
                error_response=d["error_response"],
                enabled=d["telegram_webhook_enabled"],
                delivery_mode=d["telegram_delivery_mode"],
                configured_secret=d["telegram_configured_webhook_secret"],
            )
        return self._telegram_webhook_bridge_service

    def webhook_bridge_service(self) -> Any:
        if self._webhook_bridge_service is None:
            d = self._deps
            self._webhook_bridge_service = d["webhook_bridge_class"](
                init_runtime=d["init_runtime"],
                parse_form_urlencoded=d["parse_form_urlencoded"],
                webhook_auth_result=d["webhook_auth_result"],
                resolve_inbound_connector=d["resolve_inbound_connector"],
                validate_signature=d["validate_webhook_signature"],
                ingest_webhook=d["ingest_webhook"],
                twiml_response=d["twiml_response"],
                error_response=d["error_response"],
                enabled=d["webhook_enabled"],
                configured_secret=d["configured_webhook_secret"],
            )
        return self._webhook_bridge_service
