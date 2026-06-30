from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from server_modules.connectors.autopilot_event_bridge_service import AutopilotEventBridgeService
from server_modules.connectors.autopilot_shared_service_registry import AutopilotSharedServiceRegistry
from server_modules.connectors.autopilot_state_bridge_service import AutopilotStateBridgeService
from server_modules.connectors.autopilot_terminal_bridge_service import AutopilotTerminalBridgeService
from server_modules.connectors.connector_webhook import ConnectorWebhook
from server_modules.connectors.whatsapp_webhook_bridge_service import WhatsAppWebhookBridgeService


class AutopilotBridgeFacadeService:
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
        telegram_workspace_id: str,
        whatsapp_workspace_id: str,
        telegram_service_registry: Callable[[], Any],
        whatsapp_service_registry: Callable[[], Any],
        autopilot_profile_service: Callable[[], Any],
        init_runtime: Callable[[], None],
        telegram_terminal_service: Callable[[], Any],
        telegram_enabled_getter: Callable[[], bool],
        telegram_default_profile_getter: Callable[[], str],
        telegram_catalog_getter: Callable[[], Dict[str, Any]],
        whatsapp_enabled_getter: Callable[[], bool],
        whatsapp_default_profile_getter: Callable[[], str],
        whatsapp_catalog_getter: Callable[[], Dict[str, Any]],
        telegram_state_getter: Callable[[], Dict[str, Any]],
        telegram_lock_getter: Callable[[], Any],
        safe_path_token: Callable[[Any], str],
        build_goal_with_profile: Callable[[str, Dict[str, str]], str],
        workspace_connector_context: Callable[[str, str, str], Dict[str, Any]],
        extract_message: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]],
        build_goal_with_attachments: Callable[[str, Any], str],
        route_message: Callable[[str, Dict[str, Any]], Dict[str, Any]],
        parse_form_urlencoded: Callable[[bytes], Dict[str, str]],
        error_response: Callable[[int, str], Any],
        telegram_delivery_mode_getter: Callable[[], str],
        telegram_configured_webhook_secret_getter: Callable[[], str],
        telegram_public_base_url_getter: Optional[Callable[[], str]] = None,
        webhook_enabled_getter: Callable[[], bool],
        configured_webhook_secret_getter: Callable[[], str],
        whatsapp_public_base_url_getter: Optional[Callable[[], str]] = None,
        shared_registry_class: Callable[..., Any] = AutopilotSharedServiceRegistry,
        event_bridge_class: Callable[..., Any] = AutopilotEventBridgeService,
        terminal_bridge_class: Callable[..., Any] = AutopilotTerminalBridgeService,
        state_bridge_class: Callable[..., Any] = AutopilotStateBridgeService,
        compatibility_bridge_class: Callable[..., Any] = ConnectorWebhook,
        telegram_webhook_bridge_class: Callable[..., Any] = ConnectorWebhook,
        webhook_bridge_class: Callable[..., Any] = WhatsAppWebhookBridgeService,
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
        self.telegram_workspace_id = str(telegram_workspace_id or "default")
        self.whatsapp_workspace_id = str(whatsapp_workspace_id or "default")
        self.telegram_service_registry = telegram_service_registry
        self.whatsapp_service_registry = whatsapp_service_registry
        self.autopilot_profile_service = autopilot_profile_service
        self.init_runtime = init_runtime
        self.telegram_terminal_service = telegram_terminal_service
        self.telegram_enabled_getter = telegram_enabled_getter
        self.telegram_default_profile_getter = telegram_default_profile_getter
        self.telegram_catalog_getter = telegram_catalog_getter
        self.whatsapp_enabled_getter = whatsapp_enabled_getter
        self.whatsapp_default_profile_getter = whatsapp_default_profile_getter
        self.whatsapp_catalog_getter = whatsapp_catalog_getter
        self.telegram_state_getter = telegram_state_getter
        self.telegram_lock_getter = telegram_lock_getter
        self.safe_path_token = safe_path_token
        self.build_goal_with_profile = build_goal_with_profile
        self.workspace_connector_context = workspace_connector_context
        self.extract_message = extract_message
        self.build_goal_with_attachments = build_goal_with_attachments
        self.route_message = route_message
        self.parse_form_urlencoded = parse_form_urlencoded
        self.error_response = error_response
        self.telegram_delivery_mode_getter = telegram_delivery_mode_getter
        self.telegram_configured_webhook_secret_getter = telegram_configured_webhook_secret_getter
        self.telegram_public_base_url_getter = telegram_public_base_url_getter or (lambda: "")
        self.webhook_enabled_getter = webhook_enabled_getter
        self.configured_webhook_secret_getter = configured_webhook_secret_getter
        self.whatsapp_public_base_url_getter = whatsapp_public_base_url_getter or (lambda: "")
        self.shared_registry_class = shared_registry_class
        self.event_bridge_class = event_bridge_class
        self.terminal_bridge_class = terminal_bridge_class
        self.state_bridge_class = state_bridge_class
        self.compatibility_bridge_class = compatibility_bridge_class
        self.telegram_webhook_bridge_class = telegram_webhook_bridge_class
        self.webhook_bridge_class = webhook_bridge_class

        self._shared_service_registry: Optional[Any] = None
        self._event_bridge_service: Optional[Any] = None
        self._terminal_bridge_service: Optional[Any] = None
        self._state_bridge_service: Optional[Any] = None
        self._compatibility_bridge_service: Optional[Any] = None
        self._telegram_webhook_bridge_service: Optional[Any] = None
        self._webhook_bridge_service: Optional[Any] = None

    def shared_service_registry(self) -> Any:
        if self._shared_service_registry is None:
            self._shared_service_registry = self.shared_registry_class(
                normalize_workspace_id=self.normalize_workspace_id,
                append_channel_event=self.append_channel_event,
                utc_now_iso=self.utc_now_iso,
                truncate_one_line=self.truncate_one_line,
                json_safe=self.json_safe,
                dead_letter_lock=self.dead_letter_lock,
                read_dead_letter_json=self.read_dead_letter_json,
                write_dead_letter_json=self.write_dead_letter_json,
                dead_letter_file=self.dead_letter_file,
                dead_letter_limit=self.dead_letter_limit,
                collapse_whitespace=self.collapse_whitespace,
                telegram_snapshot=lambda: self.telegram_service_registry().telegram_autopilot_state_service().snapshot(include_connectors=True),
                telegram_list_entries=lambda: self.telegram_service_registry().telegram_autopilot_state_service().list_connector_entries(
                    self.telegram_workspace_id
                ),
                resolve_telegram_profile=lambda entry: self.autopilot_profile_service().resolve_telegram_profile(entry),
                telegram_webhook_path="/channels/telegram/webhook/{connector_id}",
                telegram_public_base_url=self.telegram_public_base_url_getter(),
                telegram_webhook_secret_configured=bool(self.telegram_configured_webhook_secret_getter()),
                telegram_delivery_mode=self.telegram_delivery_mode_getter(),
                whatsapp_snapshot=lambda: self.whatsapp_service_registry().whatsapp_autopilot_state_service().snapshot(include_connectors=True),
                whatsapp_list_entries=lambda: self.whatsapp_service_registry().whatsapp_autopilot_state_service().list_connector_entries(
                    self.whatsapp_workspace_id
                ),
                resolve_whatsapp_profile=lambda entry: self.autopilot_profile_service().resolve_whatsapp_profile(entry),
                whatsapp_webhook_path="/channels/whatsapp/twilio/webhook",
                whatsapp_public_base_url=self.whatsapp_public_base_url_getter(),
                whatsapp_webhook_secret_configured=bool(self.configured_webhook_secret_getter()),
            )
        return self._shared_service_registry

    def autopilot_status_service(self) -> Any:
        return self.shared_service_registry().autopilot_status_service()

    def autopilot_endpoint_service(self) -> Any:
        return self.shared_service_registry().autopilot_endpoint_service()

    def autopilot_event_service(self) -> Any:
        return self.shared_service_registry().autopilot_event_service()

    def event_bridge_service(self) -> Any:
        if self._event_bridge_service is None:
            self._event_bridge_service = self.event_bridge_class(
                init_runtime=self.init_runtime,
                event_service=self.autopilot_event_service,
            )
        return self._event_bridge_service

    def terminal_bridge_service(self) -> Any:
        if self._terminal_bridge_service is None:
            self._terminal_bridge_service = self.terminal_bridge_class(
                init_runtime=self.init_runtime,
                telegram_terminal_service=self.telegram_terminal_service,
                telegram_supervisor_service=lambda: self.telegram_service_registry().telegram_autopilot_supervisor_service(),
                autopilot_status_service=self.autopilot_status_service,
                autopilot_endpoint_service=self.autopilot_endpoint_service,
                telegram_enabled=self.telegram_enabled_getter(),
                telegram_default_profile=self.telegram_default_profile_getter(),
                telegram_catalog=self.telegram_catalog_getter(),
                telegram_webhook_path="/channels/telegram/webhook/{connector_id}",
                whatsapp_enabled=self.whatsapp_enabled_getter(),
                whatsapp_default_profile=self.whatsapp_default_profile_getter(),
                whatsapp_catalog=self.whatsapp_catalog_getter(),
                whatsapp_webhook_path="/channels/whatsapp/twilio/webhook",
            )
        return self._terminal_bridge_service

    def state_bridge_service(self) -> Any:
        if self._state_bridge_service is None:
            self._state_bridge_service = self.state_bridge_class(
                telegram_state_service=lambda: self.telegram_service_registry().telegram_autopilot_state_service(),
                whatsapp_state_service=lambda: self.whatsapp_service_registry().whatsapp_autopilot_state_service(),
                telegram_runtime_service=lambda: self.telegram_service_registry().telegram_autopilot_runtime_service(),
                telegram_state=self.telegram_state_getter() or {},
                telegram_lock=self.telegram_lock_getter(),
            )
        return self._state_bridge_service

    def compatibility_bridge_service(self) -> Any:
        if self._compatibility_bridge_service is None:
            self._compatibility_bridge_service = self.compatibility_bridge_class(
                safe_path_token=self.safe_path_token,
                build_goal_with_profile=self.build_goal_with_profile,
                workspace_connector_context=self.workspace_connector_context,
                extract_message=self.extract_message,
                build_goal_with_attachments=self.build_goal_with_attachments,
                route_message=self.route_message,
            )
        return self._compatibility_bridge_service

    def telegram_webhook_bridge_service(self) -> Any:
        if self._telegram_webhook_bridge_service is None:
            self._telegram_webhook_bridge_service = self.telegram_webhook_bridge_class(
                init_runtime=self.init_runtime,
                parse_update=lambda raw: self.telegram_service_registry().telegram_ingress_service().parse_update(raw),
                webhook_auth_result=lambda **kwargs: self.autopilot_endpoint_service().telegram_webhook_auth_result(**kwargs),
                handle_inbound=lambda connector_id, update: self.telegram_service_registry().telegram_ingress_service().ingest_update(
                    source="webhook",
                    connector_id=connector_id,
                    update=update,
                ),
                error_response=self.error_response,
                enabled=self.telegram_enabled_getter(),
                delivery_mode=self.telegram_delivery_mode_getter(),
                configured_secret=self.telegram_configured_webhook_secret_getter(),
            )
        return self._telegram_webhook_bridge_service

    def webhook_bridge_service(self) -> Any:
        if self._webhook_bridge_service is None:
            self._webhook_bridge_service = self.webhook_bridge_class(
                init_runtime=self.init_runtime,
                parse_form_urlencoded=self.parse_form_urlencoded,
                webhook_auth_result=lambda **kwargs: self.autopilot_endpoint_service().whatsapp_webhook_auth_result(**kwargs),
                resolve_inbound_connector=lambda payload: self.whatsapp_service_registry().whatsapp_autopilot_state_service().connector_match(
                    str(payload.get("AccountSid") or "").strip(),
                    str(payload.get("From") or "").strip(),
                    str(payload.get("To") or "").strip(),
                ),
                validate_signature=lambda request_url, form, signature, auth_token: self.whatsapp_service_registry().whatsapp_transport_service().validate_webhook_signature(
                    request_url=request_url,
                    form=form,
                    signature=signature,
                    auth_token=auth_token,
                ),
                ingest_webhook=lambda payload, matched=None: self.whatsapp_service_registry().whatsapp_ingress_service().ingest_webhook(
                    payload,
                    matched=matched,
                ),
                twiml_response=lambda text: self.whatsapp_service_registry().whatsapp_transport_service().twiml_response(text),
                error_response=self.error_response,
                enabled=self.webhook_enabled_getter(),
                configured_secret=self.configured_webhook_secret_getter(),
            )
        return self._webhook_bridge_service
