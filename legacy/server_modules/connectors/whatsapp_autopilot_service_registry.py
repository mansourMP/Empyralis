from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from server_modules.channel_pairing_service import get_channel_pairing_service
from server_modules.connectors.channel_workspace_scope_service import resolve_connector_workspace_scope
from server_modules.connectors.whatsapp_ingress_service import WhatsAppIngressService
from server_modules.connectors.whatsapp_autopilot_state_service import WhatsAppAutopilotStateService
from server_modules.connectors.whatsapp_run_dispatch_service import WhatsAppRunDispatchService
from server_modules.connectors.whatsapp_transport_service import WhatsAppTransportService
from server_modules.connectors.whatsapp_webhook_service import WhatsAppWebhookService


class WhatsAppAutopilotServiceRegistry:
    def __init__(
        self,
        *,
        state: Dict[str, Any],
        lock: Any,
        read_json: Callable[[Any, Any], Dict[str, Any]],
        write_json: Callable[[Any, Dict[str, Any]], Any],
        state_file: Any,
        utc_now_iso: Callable[[], str],
        classify_error: Callable[[Any], str],
        normalize_workspace_id: Callable[[Any], str],
        load_vault: Callable[[], Dict[str, Any]],
        workspace_visible: Callable[[Any, Optional[str]], bool],
        connector_paused: Callable[[Dict[str, Any]], bool],
        resolve_vault_credential: Callable[[str, Optional[str]], Dict[str, Any]],
        enabled: bool,
        default_profile: str,
        require_prefix: bool,
        prefix: str,
        run_timeout_seconds: int,
        max_reply_chars: int,
        send_ack: bool,
        include_run_meta: Callable[[], bool],
        truncate_one_line: Callable[[str, int], str],
        poll_run_terminal_result: Callable[..., Dict[str, Any]],
        run_reply_text: Callable[[str, str, str], str],
        emit_channel_run_delivery_event: Callable[..., Any],
        append_dead_letter: Callable[..., Any],
        record_channel_event: Callable[..., Any],
        log_error: Callable[[str], Any],
        safe_path_token: Callable[[Any], str],
        resolve_profile: Callable[[Dict[str, Any]], Dict[str, Any]],
        route_message: Callable[[str, Dict[str, Any]], Dict[str, Any]],
        help_text: Callable[[Dict[str, Any]], str],
        runtime_status_text: Callable[[str], str],
        approvals_list: Callable[[int], Dict[str, Any]],
        approvals_text: Callable[[Dict[str, Any], str], str],
        approval_resolve: Callable[[str, bool, str], Dict[str, Any]],
        approval_result_text: Callable[[Dict[str, Any], bool], str],
        create_run: Callable[..., Dict[str, Any]],
        session_key_builder: Callable[[str, str], str],
        default_chat_prefix: str,
        require_explicit_opt_in: bool,
        redact_event_text: bool,
        retention_days: int,
    ) -> None:
        self.state = state
        self.lock = lock
        self.read_json = read_json
        self.write_json = write_json
        self.state_file = state_file
        self.utc_now_iso = utc_now_iso
        self.classify_error = classify_error
        self.normalize_workspace_id = normalize_workspace_id
        self.load_vault = load_vault
        self.workspace_visible = workspace_visible
        self.connector_paused = connector_paused
        self.resolve_vault_credential = resolve_vault_credential
        self.enabled = bool(enabled)
        self.default_profile = str(default_profile or "")
        self.require_prefix = bool(require_prefix)
        self.prefix = str(prefix or "")
        self.run_timeout_seconds = int(run_timeout_seconds or 0)
        self.max_reply_chars = int(max_reply_chars or 0)
        self.send_ack = bool(send_ack)
        self.include_run_meta = include_run_meta
        self.truncate_one_line = truncate_one_line
        self.poll_run_terminal_result = poll_run_terminal_result
        self.run_reply_text = run_reply_text
        self.emit_channel_run_delivery_event = emit_channel_run_delivery_event
        self.append_dead_letter = append_dead_letter
        self.record_channel_event = record_channel_event
        self.log_error = log_error
        self.safe_path_token = safe_path_token
        self.resolve_profile = resolve_profile
        self.route_message = route_message
        self.help_text = help_text
        self.runtime_status_text = runtime_status_text
        self.approvals_list = approvals_list
        self.approvals_text = approvals_text
        self.approval_resolve = approval_resolve
        self.approval_result_text = approval_result_text
        self.create_run = create_run
        self.session_key_builder = session_key_builder
        self.default_chat_prefix = default_chat_prefix
        self.require_explicit_opt_in = bool(require_explicit_opt_in)
        self.redact_event_text = bool(redact_event_text)
        self.retention_days = max(1, int(retention_days or 30))

        self._transport_service: Optional[WhatsAppTransportService] = None
        self._state_service: Optional[WhatsAppAutopilotStateService] = None
        self._run_dispatch_service: Optional[WhatsAppRunDispatchService] = None
        self._ingress_service: Optional[WhatsAppIngressService] = None
        self._webhook_service: Optional[WhatsAppWebhookService] = None

    def whatsapp_transport_service(self) -> WhatsAppTransportService:
        if self._transport_service is None:
            self._transport_service = WhatsAppTransportService()
        return self._transport_service

    def whatsapp_autopilot_state_service(self) -> WhatsAppAutopilotStateService:
        if self._state_service is None:
            self._state_service = WhatsAppAutopilotStateService(
                state=self.state,
                lock=self.lock,
                read_json=self.read_json,
                write_json=self.write_json,
                state_file=self.state_file,
                utc_now_iso=self.utc_now_iso,
                classify_error=self.classify_error,
                normalize_workspace_id=self.normalize_workspace_id,
                resolve_workspace_scope=lambda entry, fallback_workspace_id, detail_prefix: resolve_connector_workspace_scope(
                    entry,
                    normalize_workspace_id=self.normalize_workspace_id,
                    fallback_workspace_id=fallback_workspace_id,
                    detail_prefix=detail_prefix,
                ),
                load_vault=self.load_vault,
                workspace_visible=self.workspace_visible,
                connector_paused=self.connector_paused,
                resolve_vault_credential=self.resolve_vault_credential,
                normalize_whatsapp_number=self.whatsapp_transport_service().normalize_number,
                enabled=self.enabled,
                default_profile=self.default_profile,
                require_prefix=self.require_prefix,
                prefix=self.prefix,
                run_timeout_seconds=self.run_timeout_seconds,
                max_reply_chars=self.max_reply_chars,
            )
        return self._state_service

    def whatsapp_run_dispatch_service(self) -> WhatsAppRunDispatchService:
        if self._run_dispatch_service is None:
            self._run_dispatch_service = WhatsAppRunDispatchService(
                default_timeout_seconds=self.run_timeout_seconds,
                default_max_reply_chars=self.max_reply_chars,
                send_ack=self.send_ack,
                include_run_meta=self.include_run_meta,
                truncate_one_line=self.truncate_one_line,
                poll_run_terminal_result=self.poll_run_terminal_result,
                run_reply_text=self.run_reply_text,
                emit_channel_run_delivery_event=self.emit_channel_run_delivery_event,
                send_whatsapp_message=self.whatsapp_transport_service().send_message,
                append_dead_letter=self.append_dead_letter,
                record_channel_event=self.record_channel_event,
                set_connector_state=lambda connector_id, payload: self.whatsapp_autopilot_state_service().set_connector_state(
                    connector_id,
                    payload,
                ),
                utc_now_iso=self.utc_now_iso,
                classify_error=self.classify_error,
                log_error=self.log_error,
                mark_error=lambda detail: self.whatsapp_autopilot_state_service().mark_error(detail, source="run_finalize"),
                session_key_builder=self.session_key_builder,
                safe_path_token=self.safe_path_token,
            )
        return self._run_dispatch_service

    def whatsapp_ingress_service(self) -> WhatsAppIngressService:
        if self._ingress_service is None:
            self._ingress_service = WhatsAppIngressService(
                normalize_number=self.whatsapp_transport_service().normalize_number,
                session_key_builder=self.session_key_builder,
                safe_path_token=self.safe_path_token,
                connector_match=lambda account_sid, inbound_from, inbound_to: self.whatsapp_autopilot_state_service().connector_match(
                    account_sid,
                    inbound_from,
                    inbound_to,
                ),
                resolve_profile=self.resolve_profile,
                route_message=self.route_message,
                help_text=self.help_text,
                runtime_status_text=self.runtime_status_text,
                approvals_list=self.approvals_list,
                approvals_text=self.approvals_text,
                approval_resolve=self.approval_resolve,
                approval_result_text=self.approval_result_text,
                create_run=self.create_run,
                run_dispatch_service=lambda: self.whatsapp_run_dispatch_service(),
                record_channel_event=self.record_channel_event,
                set_connector_state=lambda connector_id, payload: self.whatsapp_autopilot_state_service().set_connector_state(
                    connector_id,
                    payload,
                ),
                persist_state=lambda: self.whatsapp_autopilot_state_service().persist_state(),
                mark_processed_message=lambda connector_id, message_sid: self.whatsapp_autopilot_state_service().mark_processed_message(
                    connector_id,
                    message_sid,
                ),
                increment_processed=lambda: self.whatsapp_autopilot_state_service().increment_processed(),
                autopilot_activate=lambda: self.whatsapp_autopilot_state_service().activate(),
                mark_inbound=lambda **kwargs: self.whatsapp_autopilot_state_service().mark_inbound(**kwargs),
                mark_error=lambda detail: self.whatsapp_autopilot_state_service().mark_error(detail, source="match_connector"),
                utc_now_iso=self.utc_now_iso,
                default_chat_prefix=self.default_chat_prefix,
                require_explicit_opt_in=self.require_explicit_opt_in,
                redact_event_text=self.redact_event_text,
                retention_days=self.retention_days,
                channel_pairing_service=get_channel_pairing_service,
            )
        return self._ingress_service

    def whatsapp_webhook_service(self) -> WhatsAppWebhookService:
        if self._webhook_service is None:
            self._webhook_service = WhatsAppWebhookService(
                ingress_service=self.whatsapp_ingress_service,
            )
        return self._webhook_service
