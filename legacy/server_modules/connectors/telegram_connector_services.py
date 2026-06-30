"""
Minimal replacement for TelegramAutopilotServiceRegistry + 6 autopilot services +
3 DI/registry files. Uses shared ConnectorRuntime for state/runtime/poll/supervisor.

Replaces: telegram_autopilot_service_registry, telegram_autopilot_state_service,
telegram_autopilot_runtime_service, telegram_autopilot_loop_service,
telegram_autopilot_supervisor_service, telegram_poll_state_service,
telegram_poll_cycle_service, telegram_helper_registry_bridge_service,
telegram_autopilot_helper_registry.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from server_modules.channel_pairing_service import get_channel_pairing_service
from server_modules.connectors.channel_workspace_scope_service import resolve_connector_workspace_scope
from server_modules.connectors.connector_runtime import ConnectorRuntime
from server_modules.agent.action_service import TelegramActionService
from server_modules.agent.automation_setup_service import TelegramCameraSetupService
from server_modules.connectors.telegram_connector_poll_service import TelegramConnectorPollService
from server_modules.connectors.telegram_ingress_service import TelegramIngressService
from server_modules.connectors.telegram_inbound_context_service import TelegramInboundContextService
from server_modules.connectors.telegram.media import TelegramMediaService
from server_modules.connectors.telegram_poll_dispatch_service import TelegramPollDispatchService
from server_modules.agent.user_profile_service import TelegramProfileService
from server_modules.agent.routing_service import TelegramRoutingService
from server_modules.connectors.telegram_run_action_service import TelegramRunActionService
from server_modules.connectors.telegram_run_dispatch_service import TelegramRunDispatchService
from server_modules.connectors.telegram.auth import TelegramSenderFilterService
from server_modules.connectors.telegram.webhook import TelegramWebhookService


class TelegramConnectorServices:
    """Provides the same lazy-init interface as TelegramAutopilotServiceRegistry
    but uses shared ConnectorRuntime internally for the 6 autopilot services."""

    def __init__(
        self,
        *,
        project_root: Path,
        default_workspace_id: str,
        default_chat_prefix: str,
        onboarding_enabled: bool,
        require_prefix: bool,
        prefix: str,
        space_status_enabled: bool,
        media_max_items: int,
        max_updates: int,
        poll_seconds: float,
        delivery_mode: str,
        run_timeout_seconds: int,
        max_reply_chars: int,
        send_ack: bool,
        state: Dict[str, Any],
        lock: Any,
        state_file: Path,
        read_json: Callable[[Path, Any], Any],
        write_json: Callable[[Path, Any], Any],
        persist_state: Callable[[], Any],
        utc_now_iso: Callable[[], str],
        classify_error: Callable[[str], str],
        iso_from_epoch: Callable[[float], str],
        normalize_workspace_id: Callable[[Any], str],
        thread_alive: Callable[[], bool],
        enabled: bool,
        default_profile: str,
        list_connector_entries: Callable[[], List[Dict[str, Any]]],
        resolve_profile: Callable[[Dict[str, Any]], Dict[str, Any]],
        resolve_allow_from: Callable[[Dict[str, Any]], List[str]],
        connector_state: Callable[[str], Dict[str, Any]],
        set_connector_state: Callable[[str, Dict[str, Any]], Any],
        resolve_secret: Callable[[Dict[str, Any]], Dict[str, Any]],
        load_vault: Callable[[], Any],
        workspace_visible: Callable[[str, Any], bool],
        connector_paused: Callable[[Any], bool],
        get_updates_process_lock: Callable[[str], Any],
        notify_pending_approvals: Callable[..., Dict[str, Any]],
        telegram_api_request: Callable[..., Dict[str, Any]],
        record_channel_event: Callable[..., Any],
        record_channel_event_throttled: Callable[..., Any],
        send_message: Callable[..., Any],
        send_chat_action: Callable[..., Any],
        edit_message: Callable[..., Any],
        autopilot_log: Callable[[str], Any],
        autopilot_mark_error: Callable[[str, str], float],
        mark_poll: Callable[[bool], Any],
        mark_started: Callable[[str], Any],
        normalize_profile_field: Callable[[Any], str],
        select_skill_from_text: Callable[[str], Optional[Dict[str, Any]]],
        skill_goal_builder: Callable[[Dict[str, Any]], str],
        help_text: Callable[[Dict[str, Any]], str],
        skills_menu_text: Callable[[Dict[str, Any]], str],
        menu_keyboard: Callable[[Dict[str, Any], str], Dict[str, Any]],
        onboarding_prompt: Callable[[int, bool], str],
        onboarding_start: Callable[[str, str], Dict[str, Any]],
        profile_text: Callable[[Dict[str, Any], Dict[str, Any]], str],
        profile_help_text: Callable[[Dict[str, Any]], str],
        profile_set: Callable[[str, str, str, str], Any],
        profile_clear: Callable[[str, str, str], Any],
        runtime_status_text: Callable[[str], str],
        approvals_list: Callable[[int], Dict[str, Any]],
        approvals_text: Callable[[Dict[str, Any], str], str],
        approval_resolve: Callable[[str, bool, str], Dict[str, Any]],
        approval_result_text: Callable[[Dict[str, Any], bool], str],
        extract_message: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]],
        chat_matches: Callable[[str, Dict[str, Any]], bool],
        store_attachments: Callable[..., List[Dict[str, Any]]],
        route_message: Callable[[str, Dict[str, Any]], Dict[str, Any]],
        session_key_builder: Callable[[str], str],
        trace_id_builder: Callable[[str, Any, Any], str],
        guided_setup_handler: Callable[..., Dict[str, Any]],
        sender_allowed: Callable[[Dict[str, Any], List[str]], bool],
        get_chat_profile: Callable[[str, str], Dict[str, Any]],
        explicit_run_command: Callable[[str], bool],
        onboarding_get_state: Callable[[str, str], Dict[str, Any]],
        onboarding_consume_answer: Callable[[str, str, str], Dict[str, Any]],
        profile_get: Callable[[str, str], Dict[str, Any]],
        profile_has_context: Callable[[Dict[str, Any]], bool],
        build_goal_with_profile: Callable[[str, Dict[str, Any]], str],
        build_goal_with_attachments: Callable[[str, List[Dict[str, Any]]], str],
        workspace_connector_context: Callable[..., Dict[str, Any]],
        build_goal_with_connector_context: Callable[[str, str], str],
        space_question_via_mcp: Callable[..., Dict[str, Any]],
        installed_skill_query: Callable[..., Dict[str, Any]],
        truncate_one_line: Callable[[str, int], str],
        create_run: Callable[..., Dict[str, Any]],
        include_run_meta: Callable[[], bool],
        humanize_run_summary: Callable[[str], str],
        runs_get: Callable[[str], Any],
        latest_run_error_message: Callable[[Any], str],
        is_non_retryable_run_error: Callable[[str], bool],
        friendly_run_error: Callable[[str], str],
        summarize_run_terminal_result: Callable[[Any, int], str],
        local_companion_snapshot: Callable[[], Dict[str, Any]],
        can_auto_approve_wait: Callable[[Any], bool],
        pending_confirmation_payload: Callable[[Any], Dict[str, Any]],
        emit_channel_run_delivery_event: Callable[..., Any],
        record_activity_event: Optional[Callable[..., Any]],
        sleep: Callable[[float], Any],
        # Helper registry fields (was TelegramAutopilotHelperRegistry / TelegramHelperRegistryBridgeService)
        helper_profile_state_file: Any = None,
        helper_onboarding_state_file: Any = None,
        helper_camera_setup_state_file: Any = None,
        helper_media_dir: Any = None,
        helper_media_enabled: bool = True,
        helper_media_max_items: int = 0,
        helper_media_max_bytes: int = 0,
        helper_media_include_in_goal: bool = True,
        helper_quick_goal_templates: Dict[str, str] | None = None,
        helper_menu_goal_templates: Dict[str, str] | None = None,
    ) -> None:
        self.project_root = Path(project_root)
        self.default_workspace_id = str(default_workspace_id or "default").strip() or "default"
        self.default_chat_prefix = default_chat_prefix
        self.onboarding_enabled = bool(onboarding_enabled)
        self.require_prefix = bool(require_prefix)
        self.prefix = str(prefix or "")
        self.space_status_enabled = bool(space_status_enabled)
        self.media_max_items = int(media_max_items)
        self.max_updates = int(max_updates)
        self.poll_seconds = float(poll_seconds)
        self.delivery_mode = str(delivery_mode or "").strip().lower() or "polling"
        self.run_timeout_seconds = int(run_timeout_seconds)
        self.max_reply_chars = int(max_reply_chars)
        self.send_ack = bool(send_ack)
        self.state = state
        self.lock = lock
        self.state_file = Path(state_file)
        self.read_json = read_json
        self.write_json = write_json
        self._persist_state_callable = persist_state
        self.utc_now_iso = utc_now_iso
        self.classify_error = classify_error
        self.iso_from_epoch = iso_from_epoch
        self.normalize_workspace_id = normalize_workspace_id
        self.thread_alive = thread_alive
        self.enabled = bool(enabled)
        self.default_profile = str(default_profile or "")
        self._list_connector_entries = list_connector_entries
        self.resolve_profile = resolve_profile
        self.resolve_allow_from = resolve_allow_from
        self._connector_state = connector_state
        self._set_connector_state = set_connector_state
        self.resolve_secret = resolve_secret
        self.load_vault = load_vault
        self.workspace_visible = workspace_visible
        self.connector_paused = connector_paused
        self.get_updates_process_lock = get_updates_process_lock
        self.notify_pending_approvals = notify_pending_approvals
        self.telegram_api_request = telegram_api_request
        self.record_channel_event = record_channel_event
        self.record_channel_event_throttled = record_channel_event_throttled
        self.send_message = send_message
        self.send_chat_action = send_chat_action
        self.edit_message = edit_message
        self.autopilot_log = autopilot_log
        self.autopilot_mark_error = autopilot_mark_error
        self.mark_poll = mark_poll
        self.mark_started = mark_started
        self.normalize_profile_field = normalize_profile_field
        self.select_skill_from_text = select_skill_from_text
        self.skill_goal_builder = skill_goal_builder
        self.help_text = help_text
        self.skills_menu_text = skills_menu_text
        self.menu_keyboard = menu_keyboard
        self.onboarding_prompt = onboarding_prompt
        self.onboarding_start = onboarding_start
        self.profile_text = profile_text
        self.profile_help_text = profile_help_text
        self.profile_set = profile_set
        self.profile_clear = profile_clear
        self.runtime_status_text = runtime_status_text
        self.approvals_list = approvals_list
        self.approvals_text = approvals_text
        self.approval_resolve = approval_resolve
        self.approval_result_text = approval_result_text
        self.extract_message = extract_message
        self.chat_matches = chat_matches
        self.store_attachments = store_attachments
        self.route_message = route_message
        self.session_key_builder = session_key_builder
        self.trace_id_builder = trace_id_builder
        self.guided_setup_handler = guided_setup_handler
        self.sender_allowed = sender_allowed
        self.get_chat_profile = get_chat_profile
        self.explicit_run_command = explicit_run_command
        self.onboarding_get_state = onboarding_get_state
        self.onboarding_consume_answer = onboarding_consume_answer
        self.profile_get = profile_get
        self.profile_has_context = profile_has_context
        self.build_goal_with_profile = build_goal_with_profile
        self.build_goal_with_attachments = build_goal_with_attachments
        self.workspace_connector_context = workspace_connector_context
        self.build_goal_with_connector_context = build_goal_with_connector_context
        self.space_question_via_mcp = space_question_via_mcp
        self.installed_skill_query = installed_skill_query
        self.truncate_one_line = truncate_one_line
        self.create_run = create_run
        self.include_run_meta = include_run_meta
        self.humanize_run_summary = humanize_run_summary
        self.runs_get = runs_get
        self.latest_run_error_message = latest_run_error_message
        self.is_non_retryable_run_error = is_non_retryable_run_error
        self.friendly_run_error = friendly_run_error
        self.summarize_run_terminal_result = summarize_run_terminal_result
        self.local_companion_snapshot = local_companion_snapshot
        self.can_auto_approve_wait = can_auto_approve_wait
        self.pending_confirmation_payload = pending_confirmation_payload
        self.emit_channel_run_delivery_event = emit_channel_run_delivery_event
        self.record_activity_event = record_activity_event
        self.sleep = sleep

        # Helper registry fields
        self.helper_profile_state_file = helper_profile_state_file
        self.helper_onboarding_state_file = helper_onboarding_state_file
        self.helper_camera_setup_state_file = helper_camera_setup_state_file
        self.helper_media_dir = helper_media_dir
        self.helper_media_enabled = bool(helper_media_enabled)
        self.helper_media_max_items = int(helper_media_max_items or 0)
        self.helper_media_max_bytes = int(helper_media_max_bytes or 0)
        self.helper_media_include_in_goal = bool(helper_media_include_in_goal)
        self.helper_quick_goal_templates = dict(helper_quick_goal_templates or {})
        self.helper_menu_goal_templates = dict(helper_menu_goal_templates or {})

        # Shared runtime (replaces 6 autopilot files)
        self._connector_runtime: Optional[ConnectorRuntime] = None

        # Lazy-init caches
        self._run_dispatch_service: Optional[TelegramRunDispatchService] = None
        self._sender_filter_service: Optional[TelegramSenderFilterService] = None
        self._action_service: Optional[TelegramActionService] = None
        self._inbound_context_service: Optional[TelegramInboundContextService] = None
        self._poll_dispatch_service: Optional[TelegramPollDispatchService] = None
        self._run_action_service: Optional[TelegramRunActionService] = None
        self._connector_poll_service: Optional[TelegramConnectorPollService] = None
        self._ingress_service: Optional[TelegramIngressService] = None
        self._webhook_service: Optional[TelegramWebhookService] = None
        # Helper services (was TelegramAutopilotHelperRegistry)
        self._profile_service: Optional[TelegramProfileService] = None
        self._camera_setup_service: Optional[TelegramCameraSetupService] = None
        self._media_service: Optional[TelegramMediaService] = None
        self._routing_service: Optional[TelegramRoutingService] = None

    # ------------------------------------------------------------------
    # ConnectorRuntime (replaces 6 autopilot files)
    # ------------------------------------------------------------------

    def _get_runtime(self) -> ConnectorRuntime:
        if self._connector_runtime is None:
            self._connector_runtime = ConnectorRuntime(
                provider="telegram",
                state=self.state,
                lock=self.lock,
                read_json=self.read_json,
                write_json=self.write_json,
                state_file=self.state_file,
                utc_now_iso=self.utc_now_iso,
                classify_error=self.classify_error,
                iso_from_epoch=self.iso_from_epoch,
                normalize_workspace_id=self.normalize_workspace_id,
                load_vault=self.load_vault,
                workspace_visible=self.workspace_visible,
                connector_paused=self.connector_paused,
                resolve_secret=self.resolve_secret,
                enabled=self.enabled,
                default_profile=self.default_profile,
                require_prefix=self.require_prefix,
                prefix=self.prefix,
                delivery_mode=self.delivery_mode,
                poll_seconds=self.poll_seconds,
                max_updates=self.max_updates,
                run_timeout_seconds=self.run_timeout_seconds,
                max_reply_chars=self.max_reply_chars,
                thread_alive=self.thread_alive,
                list_connector_entries=self._list_connector_entries,
                get_updates_process_lock=self.get_updates_process_lock,
                notify_pending_approvals=self.notify_pending_approvals,
                telegram_api_request=self.telegram_api_request,
                record_channel_event_throttled=self.record_channel_event_throttled,
                autopilot_log=self.autopilot_log,
                mark_started=self.mark_started,
                sleep=self.sleep,
                poll_connector=lambda entry: self.telegram_connector_poll_service().poll_connector(entry),
            )
        return self._connector_runtime

    def persist_state(self) -> None:
        self._get_runtime().persist_state()

    # Legacy compatibility methods that forward to ConnectorRuntime

    def telegram_autopilot_state_service(self) -> ConnectorRuntime:
        """Returns ConnectorRuntime directly — it subsumes all state/runtime/poll services."""
        return self._get_runtime()

    def telegram_autopilot_runtime_service(self) -> ConnectorRuntime:
        return self._get_runtime()

    def telegram_autopilot_loop_service(self) -> ConnectorRuntime:
        return self._get_runtime()

    def telegram_autopilot_supervisor_service(self) -> ConnectorRuntime:
        return self._get_runtime()

    def telegram_poll_state_service(self) -> ConnectorRuntime:
        return self._get_runtime()

    def telegram_poll_cycle_service(self) -> ConnectorRuntime:
        return self._get_runtime()

    # ------------------------------------------------------------------
    # Remaining services (unchanged logic, same as before)
    # ------------------------------------------------------------------

    def telegram_run_dispatch_service(self) -> TelegramRunDispatchService:
        if self._run_dispatch_service is None:
            self._run_dispatch_service = TelegramRunDispatchService(
                project_root=self.project_root,
                default_timeout_seconds=self.run_timeout_seconds,
                default_max_reply_chars=self.max_reply_chars,
                send_ack=self.send_ack,
                include_run_meta=self.include_run_meta,
                humanize_run_summary=self.humanize_run_summary,
                truncate_one_line=self.truncate_one_line,
                runs_get=self.runs_get,
                latest_run_error_message=self.latest_run_error_message,
                is_non_retryable_run_error=self.is_non_retryable_run_error,
                friendly_run_error=self.friendly_run_error,
                summarize_run_terminal_result=self.summarize_run_terminal_result,
                local_companion_snapshot=self.local_companion_snapshot,
                can_auto_approve_wait=self.can_auto_approve_wait,
                pending_confirmation_payload=self.pending_confirmation_payload,
                emit_channel_run_delivery_event=self.emit_channel_run_delivery_event,
                record_activity_event=self.record_activity_event,
            )
        return self._run_dispatch_service

    def telegram_sender_filter_service(self) -> TelegramSenderFilterService:
        if self._sender_filter_service is None:
            self._sender_filter_service = TelegramSenderFilterService(
                record_channel_event_throttled=self.record_channel_event_throttled,
                set_connector_state=self._set_connector_state,
                utc_now_iso=self.utc_now_iso,
            )
        return self._sender_filter_service

    def telegram_action_service(self) -> TelegramActionService:
        if self._action_service is None:
            self._action_service = TelegramActionService(
                default_chat_prefix=self.default_chat_prefix,
                onboarding_enabled=self.onboarding_enabled,
                help_text=self.help_text,
                skills_menu_text=self.skills_menu_text,
                menu_keyboard=self.menu_keyboard,
                onboarding_prompt=self.onboarding_prompt,
                onboarding_start=self.onboarding_start,
                profile_text=self.profile_text,
                profile_help_text=self.profile_help_text,
                profile_set=self.profile_set,
                profile_clear=self.profile_clear,
                runtime_status_text=self.runtime_status_text,
                approvals_list=self.approvals_list,
                approvals_text=self.approvals_text,
                approval_resolve=self.approval_resolve,
                approval_result_text=self.approval_result_text,
                send_message=self.send_message,
            )
        return self._action_service

    def telegram_inbound_context_service(self) -> TelegramInboundContextService:
        if self._inbound_context_service is None:
            self._inbound_context_service = TelegramInboundContextService(
                media_max_items=self.media_max_items,
                extract_message=self.extract_message,
                chat_matches=self.chat_matches,
                store_attachments=self.store_attachments,
                route_message=self.route_message,
                session_key_builder=self.session_key_builder,
                trace_id_builder=self.trace_id_builder,
                record_channel_event=self.record_channel_event,
                guided_setup_handler=self.guided_setup_handler,
                send_message=self.send_message,
            )
        return self._inbound_context_service

    def telegram_poll_dispatch_service(self) -> TelegramPollDispatchService:
        if self._poll_dispatch_service is None:
            self._poll_dispatch_service = TelegramPollDispatchService(
                sender_allowed=self.sender_allowed,
                session_key_builder=self.session_key_builder,
                inbound_context_service=lambda: self.telegram_inbound_context_service(),
                sender_filter_service=lambda: self.telegram_sender_filter_service(),
                action_service=lambda: self.telegram_action_service(),
                run_action_service=lambda: self.telegram_run_action_service(),
                get_chat_profile=self.get_chat_profile,
                explicit_run_command=self.explicit_run_command,
                help_text=self.help_text,
                send_message=self.send_message,
                channel_pairing_service=get_channel_pairing_service,
            )
        return self._poll_dispatch_service

    def telegram_ingress_service(self) -> TelegramIngressService:
        if self._ingress_service is None:
            rt = self._get_runtime()
            self._ingress_service = TelegramIngressService(
                default_workspace_id=self.default_workspace_id,
                resolve_workspace_scope=lambda entry, fallback_workspace_id, detail_prefix: resolve_connector_workspace_scope(
                    entry,
                    normalize_workspace_id=self.normalize_workspace_id,
                    fallback_workspace_id=fallback_workspace_id,
                    detail_prefix=detail_prefix,
                ),
                get_connector_entry=lambda connector_id: rt.get_connector_entry(connector_id),
                resolve_profile=self.resolve_profile,
                resolve_allow_from=self.resolve_allow_from,
                connector_state=self._connector_state,
                resolve_secret=self.resolve_secret,
                poll_cycle_service=lambda: rt,
                poll_state_service=lambda: rt,
                extract_message=self.extract_message,
                chat_matches=self.chat_matches,
                store_attachments=self.store_attachments,
                route_message=self.route_message,
                session_key_builder=self.session_key_builder,
                trace_id_builder=self.trace_id_builder,
                record_channel_event=self.record_channel_event,
                guided_setup_handler=self.guided_setup_handler,
                send_message=self.send_message,
                send_chat_action=self.send_chat_action,
                run_dispatch_service=lambda: self.telegram_run_dispatch_service(),
                action_service=lambda: self.telegram_action_service(),
                run_action_service=lambda: self.telegram_run_action_service(),
                get_chat_profile=self.get_chat_profile,
                explicit_run_command=self.explicit_run_command,
                help_text=self.help_text,
                channel_pairing_service=get_channel_pairing_service,
                media_max_items=self.media_max_items,
            )
        return self._ingress_service

    def telegram_run_action_service(self) -> TelegramRunActionService:
        if self._run_action_service is None:
            self._run_action_service = TelegramRunActionService(
                onboarding_enabled=self.onboarding_enabled,
                space_status_enabled=self.space_status_enabled,
                max_reply_chars=self.max_reply_chars,
                project_root=self.project_root,
                onboarding_get_state=self.onboarding_get_state,
                onboarding_start=self.onboarding_start,
                onboarding_consume_answer=self.onboarding_consume_answer,
                onboarding_prompt=self.onboarding_prompt,
                profile_get=self.profile_get,
                profile_has_context=self.profile_has_context,
                help_text=self.help_text,
                build_goal_with_profile=self.build_goal_with_profile,
                build_goal_with_attachments=self.build_goal_with_attachments,
                workspace_connector_context=self.workspace_connector_context,
                build_goal_with_connector_context=self.build_goal_with_connector_context,
                space_question_via_mcp=self.space_question_via_mcp,
                installed_skill_query=self.installed_skill_query,
                truncate_one_line=self.truncate_one_line,
                send_message=self.send_message,
                send_chat_action=self.send_chat_action,
                edit_message=self.edit_message,
                record_channel_event=self.record_channel_event,
                run_dispatch_service=lambda: self.telegram_run_dispatch_service(),
                create_run=self.create_run,
            )
        return self._run_action_service

    def telegram_connector_poll_service(self) -> TelegramConnectorPollService:
        if self._connector_poll_service is None:
            rt = self._get_runtime()
            self._connector_poll_service = TelegramConnectorPollService(
                default_workspace_id=self.default_workspace_id,
                normalize_workspace_id=self.normalize_workspace_id,
                resolve_workspace_scope=lambda entry, fallback_workspace_id, detail_prefix: resolve_connector_workspace_scope(
                    entry,
                    normalize_workspace_id=self.normalize_workspace_id,
                    fallback_workspace_id=fallback_workspace_id,
                    detail_prefix=detail_prefix,
                ),
                resolve_profile=self.resolve_profile,
                resolve_allow_from=self.resolve_allow_from,
                connector_state=self._connector_state,
                resolve_secret=self.resolve_secret,
                poll_cycle_service=lambda: rt,
                ingress_service=lambda: self.telegram_ingress_service(),
                inbound_context_service=lambda: self.telegram_inbound_context_service(),
                poll_dispatch_service=lambda: self.telegram_poll_dispatch_service(),
                poll_state_service=lambda: rt,
            )
        return self._connector_poll_service

    def telegram_webhook_service(self) -> TelegramWebhookService:
        if self._webhook_service is None:
            rt = self._get_runtime()
            self._webhook_service = TelegramWebhookService(
                default_workspace_id=self.default_workspace_id,
                resolve_workspace_scope=resolve_connector_workspace_scope,
                get_connector_entry=lambda connector_id: rt.get_connector_entry(connector_id),
                resolve_profile=self.resolve_profile,
                resolve_allow_from=self.resolve_allow_from,
                connector_state=self._connector_state,
                resolve_secret=self.resolve_secret,
                inbound_context_service=lambda: self.telegram_inbound_context_service(),
                poll_dispatch_service=lambda: self.telegram_poll_dispatch_service(),
                poll_state_service=lambda: rt,
                poll_cycle_service=lambda: rt,
            )
        return self._webhook_service

    # ------------------------------------------------------------------
    # Helper services (was TelegramAutopilotHelperRegistry)
    # ------------------------------------------------------------------

    def profile_service(self) -> TelegramProfileService:
        if self._profile_service is None:
            self._profile_service = TelegramProfileService(
                profile_state_file=self.helper_profile_state_file,
                onboarding_state_file=self.helper_onboarding_state_file,
                default_chat_prefix=self.default_chat_prefix,
                read_json=self.read_json,
                write_json=self.write_json,
                now_iso=self.utc_now_iso,
                truncate_one_line=self.truncate_one_line,
            )
        return self._profile_service

    def camera_setup_service(self) -> TelegramCameraSetupService:
        if self._camera_setup_service is None:
            self._camera_setup_service = TelegramCameraSetupService(
                state_file=self.helper_camera_setup_state_file,
                read_json=self.read_json,
                write_json=self.write_json,
                now_iso=self.utc_now_iso,
                session_key_builder=self.session_key_builder,
            )
        return self._camera_setup_service

    def media_service(self) -> TelegramMediaService:
        if self._media_service is None:
            self._media_service = TelegramMediaService(
                media_dir=self.helper_media_dir,
                media_enabled=self.helper_media_enabled,
                media_max_items=self.helper_media_max_items,
                media_max_bytes=self.helper_media_max_bytes,
                media_include_in_goal=self.helper_media_include_in_goal,
                telegram_api_request=self.telegram_api_request,
            )
        return self._media_service

    def routing_service(self) -> TelegramRoutingService:
        if self._routing_service is None:
            self._routing_service = TelegramRoutingService(
                default_chat_prefix=self.default_chat_prefix,
                quick_goal_templates=self.helper_quick_goal_templates,
                menu_goal_templates=self.helper_menu_goal_templates,
                normalize_profile_field=self.normalize_profile_field,
                select_skill_from_text=self.select_skill_from_text,
                skill_goal_builder=self.skill_goal_builder,
            )
        return self._routing_service
