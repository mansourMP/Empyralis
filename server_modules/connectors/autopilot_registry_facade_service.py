from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from server_modules import activity_ledger_service, entitlements_service, outbox_service
from server_modules.automation_intents import classify_automation_intent
from server_modules.connectors.autopilot_channel_support_service import AutopilotChannelSupportService
from server_modules.connectors.autopilot_common_support_service import AutopilotCommonSupportService
from server_modules.connectors.autopilot_profile_service import AutopilotProfileService
from server_modules.connectors.autopilot_run_entry_service import AutopilotRunEntryService
from server_modules.connectors.autopilot_runtime_service_registry import AutopilotRuntimeServiceRegistry
from server_modules.connectors.autopilot_runtime_support_service import AutopilotRuntimeSupportService
from server_modules.connectors.autopilot_skill_service import AutopilotSkillService
from server_modules.connectors.autopilot_support_service_registry import AutopilotSupportServiceRegistry
from server_modules.connectors.autopilot_workflow_setup_service import AutopilotWorkflowSetupService
from server_modules.connectors.runtime_status_service import RuntimeStatusService
from server_modules.connectors.telegram_connector_context_service import TelegramConnectorContextService
from server_modules.connectors.telegram.connector_support import TelegramConnectorSupportService
from server_modules.connectors.telegram_connector_services import TelegramConnectorServices
from server_modules.agent.menu_content_service import TelegramMenuService
from server_modules.connectors.telegram_terminal_service import TelegramTerminalService
from server_modules.connectors.telegram.transport import TelegramTransportService
from server_modules.connectors.whatsapp_autopilot_service_registry import WhatsAppAutopilotServiceRegistry
from server_modules.direct_tool_config_service import run_async_tool_call


class AutopilotRegistryFacadeService:
    def __init__(
        self,
        *,
        project_root: Path,
        default_chat_prefix: str,
        telegram_default_workspace_id_getter: Callable[[], str],
        telegram_onboarding_enabled: bool,
        telegram_require_prefix_getter: Callable[[], bool],
        telegram_prefix_getter: Callable[[], str],
        telegram_space_status_enabled: bool,
        telegram_media_max_items: int,
        telegram_max_updates: int,
        telegram_poll_seconds: float,
        telegram_delivery_mode_getter: Callable[[], str],
        telegram_run_timeout_seconds_getter: Callable[[], int],
        telegram_max_reply_chars_getter: Callable[[], int],
        telegram_send_ack_getter: Callable[[], bool],
        telegram_enabled_getter: Callable[[], bool],
        telegram_default_profile_getter: Callable[[], str],
        telegram_guided_automation_setup_enabled: bool,
        telegram_trust_mode_value_getter: Callable[[], str],
        telegram_execution_target_value_getter: Callable[[], str],
        whatsapp_enabled_getter: Callable[[], bool],
        whatsapp_default_profile_getter: Callable[[], str],
        whatsapp_require_prefix_getter: Callable[[], bool],
        whatsapp_prefix_getter: Callable[[], str],
        whatsapp_run_timeout_seconds_getter: Callable[[], int],
        whatsapp_max_reply_chars_getter: Callable[[], int],
        whatsapp_send_ack_getter: Callable[[], bool],
        whatsapp_trust_mode_value_getter: Callable[[], str],
        whatsapp_execution_target_value_getter: Callable[[], str],
        telegram_state_getter: Callable[[], Dict[str, Any]],
        telegram_lock_getter: Callable[[], Any],
        telegram_state_file: Path,
        whatsapp_state_getter: Callable[[], Dict[str, Any]],
        whatsapp_lock_getter: Callable[[], Any],
        whatsapp_state_file: Path,
        read_json: Callable[[Path, Any], Any],
        write_json: Callable[[Path, Any], Any],
        utc_now_iso: Callable[[], str],
        normalize_workspace_id: Callable[[Any], str],
        load_vault: Callable[[], Any],
        workspace_visible: Callable[[str, Any], bool],
        telegram_thread_alive: Callable[[], bool],
        telegram_allow_from_value: Callable[[], str],
        get_updates_process_lock: Callable[[str], Any],
        mark_telegram_started: Callable[[str], Any],
        resolve_vault_credential: Callable[[str, Optional[str]], Any],
        safe_path_token: Callable[[Any], str],
        runs_get: Callable[[str], Any],
        telegram_space_question_via_mcp: Callable[..., Dict[str, Any]],
        helper_profile_state_file: Path,
        helper_onboarding_state_file: Path,
        helper_camera_setup_state_file: Path,
        helper_media_dir: Path,
        helper_media_enabled: bool,
        helper_media_max_items: int,
        helper_media_max_bytes: int,
        helper_media_include_in_goal: bool,
        helper_quick_goal_templates: Dict[str, str],
        helper_menu_goal_templates: Dict[str, str],
        support_workflow_api_url: str,
        support_runtime_url: str,
        support_web_url: str,
        telegram_profile_catalog_getter: Callable[[], Dict[str, Any]],
        whatsapp_profile_catalog_getter: Callable[[], Dict[str, Any]],
        support_installed_skills_enabled: bool,
        support_error_category_hints: Any,
        support_engine_validation_errors_getter: Callable[[], Any],
        env_get: Callable[[str, str], str],
        init_runtime: Callable[[], Any],
        runtime_telegram_engine_getter: Callable[[], str],
        runtime_whatsapp_engine_getter: Callable[[], str],
        runtime_telegram_show_buttons: bool,
        runtime_local_lease_seconds: int,
        runtime_non_retryable_run_error_hints: Any,
        runtime_builtin_skills_limit_builder: Callable[[str, int], Any],
        normalize_agent_role: Callable[[Any], str],
        allow_any_chat_getter: Callable[[], bool],
        http_json_request: Callable[..., Any],
        telegram_profile_fields: Any,
        normalize_trust_mode: Callable[[Any], str],
        normalize_execution_target: Callable[[Any], str],
        decide_execution_target: Callable[[Dict[str, Any]], Dict[str, Any]],
        apply_execution_route_metadata: Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]],
        execute_agent_turn_request: Optional[Callable[..., Dict[str, Any]]] = None,
        run_start_request_class: Optional[Callable[..., Any]] = None,
        start_run_request: Optional[Callable[[Any], Dict[str, Any]]] = None,
        create_run: Callable[..., Any],
        inherit_owner_user_id: Callable[[Optional[str]], Optional[str]],
        agent_machine_full_trust_enabled: Callable[[Optional[str]], bool],
        run_history: Any,
        run_history_lock: Any,
        runtime_metrics: Dict[str, Any],
        metrics_lock: Any,
        utc_now: Callable[[], Any],
        parse_utc_ts: Callable[[Any], Any],
        worker_online_helper: Callable[[Dict[str, Any], Optional[Any]], bool],
        local_queue_lock: Any,
        local_pending_run_ids: Any,
        local_claimed_runs: Any,
        local_worker_registry: Dict[str, Any],
        truncate_one_line: Callable[[str, int], str],
        bool_from_any: Callable[[Any, bool], bool],
        local_companion_snapshot: Callable[[], Dict[str, Any]],
        current_runtime_metrics: Callable[[], Dict[str, Any]],
        latest_runtime_run_summary: Callable[[], str],
        list_vault_connectors: Callable[[str], Any],
        list_recent_connector_messages: Callable[[Any, int], Any],
        query_active_installed_skills: Callable[..., Any],
        runtime_builtin_skills_getter: Callable[[], Any],
        runtime_skills_snapshot_getter: Callable[[], Any],
        bridge_facade_getter: Callable[[], Any],
        helper_registry_bridge_class: Callable[..., Any] = TelegramConnectorServices,
        telegram_service_registry_class: Callable[..., Any] = TelegramConnectorServices,
        whatsapp_service_registry_class: Callable[..., Any] = WhatsAppAutopilotServiceRegistry,
        support_registry_class: Callable[..., Any] = AutopilotSupportServiceRegistry,
        profile_service_class: Callable[..., Any] = AutopilotProfileService,
        runtime_status_service_class: Callable[..., Any] = RuntimeStatusService,
        workflow_setup_service_class: Callable[..., Any] = AutopilotWorkflowSetupService,
        connector_context_service_class: Callable[..., Any] = TelegramConnectorContextService,
        common_support_service_class: Callable[..., Any] = AutopilotCommonSupportService,
        skill_service_class: Callable[..., Any] = AutopilotSkillService,
        channel_support_service_class: Callable[..., Any] = AutopilotChannelSupportService,
        runtime_registry_class: Callable[..., Any] = AutopilotRuntimeServiceRegistry,
        connector_support_service_class: Callable[..., Any] = TelegramConnectorSupportService,
        transport_service_class: Callable[..., Any] = TelegramTransportService,
        terminal_service_class: Callable[..., Any] = TelegramTerminalService,
        run_entry_service_class: Callable[..., Any] = AutopilotRunEntryService,
        runtime_support_service_class: Callable[..., Any] = AutopilotRuntimeSupportService,
        menu_service_class: Callable[..., Any] = TelegramMenuService,
    ) -> None:
        self.project_root = Path(project_root)
        self.default_chat_prefix = str(default_chat_prefix or "")
        self.telegram_default_workspace_id_getter = telegram_default_workspace_id_getter
        self.telegram_onboarding_enabled = bool(telegram_onboarding_enabled)
        self.telegram_require_prefix_getter = telegram_require_prefix_getter
        self.telegram_prefix_getter = telegram_prefix_getter
        self.telegram_space_status_enabled = bool(telegram_space_status_enabled)
        self.telegram_media_max_items = int(telegram_media_max_items)
        self.telegram_max_updates = int(telegram_max_updates)
        self.telegram_poll_seconds = float(telegram_poll_seconds)
        self.telegram_delivery_mode_getter = telegram_delivery_mode_getter
        self.telegram_run_timeout_seconds_getter = telegram_run_timeout_seconds_getter
        self.telegram_max_reply_chars_getter = telegram_max_reply_chars_getter
        self.telegram_send_ack_getter = telegram_send_ack_getter
        self.telegram_enabled_getter = telegram_enabled_getter
        self.telegram_default_profile_getter = telegram_default_profile_getter
        self.telegram_guided_automation_setup_enabled = bool(telegram_guided_automation_setup_enabled)
        self.telegram_trust_mode_value_getter = telegram_trust_mode_value_getter
        self.telegram_execution_target_value_getter = telegram_execution_target_value_getter
        self.whatsapp_enabled_getter = whatsapp_enabled_getter
        self.whatsapp_default_profile_getter = whatsapp_default_profile_getter
        self.whatsapp_require_prefix_getter = whatsapp_require_prefix_getter
        self.whatsapp_prefix_getter = whatsapp_prefix_getter
        self.whatsapp_run_timeout_seconds_getter = whatsapp_run_timeout_seconds_getter
        self.whatsapp_max_reply_chars_getter = whatsapp_max_reply_chars_getter
        self.whatsapp_send_ack_getter = whatsapp_send_ack_getter
        self.whatsapp_trust_mode_value_getter = whatsapp_trust_mode_value_getter
        self.whatsapp_execution_target_value_getter = whatsapp_execution_target_value_getter
        self.telegram_state_getter = telegram_state_getter
        self.telegram_lock_getter = telegram_lock_getter
        self.telegram_state_file = Path(telegram_state_file)
        self.whatsapp_state_getter = whatsapp_state_getter
        self.whatsapp_lock_getter = whatsapp_lock_getter
        self.whatsapp_state_file = Path(whatsapp_state_file)
        self.read_json = read_json
        self.write_json = write_json
        self.utc_now_iso = utc_now_iso
        self.normalize_workspace_id = normalize_workspace_id
        self.load_vault = load_vault
        self.workspace_visible = workspace_visible
        self.telegram_thread_alive = telegram_thread_alive
        self.telegram_allow_from_value = telegram_allow_from_value
        self.get_updates_process_lock = get_updates_process_lock
        self.mark_telegram_started = mark_telegram_started
        self.resolve_vault_credential = resolve_vault_credential
        self.safe_path_token = safe_path_token
        self.runs_get = runs_get
        self.telegram_space_question_via_mcp = telegram_space_question_via_mcp
        self.helper_profile_state_file = Path(helper_profile_state_file)
        self.helper_onboarding_state_file = Path(helper_onboarding_state_file)
        self.helper_camera_setup_state_file = Path(helper_camera_setup_state_file)
        self.helper_media_dir = Path(helper_media_dir)
        self.helper_media_enabled = bool(helper_media_enabled)
        self.helper_media_max_items = int(helper_media_max_items)
        self.helper_media_max_bytes = int(helper_media_max_bytes)
        self.helper_media_include_in_goal = bool(helper_media_include_in_goal)
        self.helper_quick_goal_templates = helper_quick_goal_templates
        self.helper_menu_goal_templates = helper_menu_goal_templates
        self.support_workflow_api_url = str(support_workflow_api_url or "")
        self.support_runtime_url = str(support_runtime_url or "")
        self.support_web_url = str(support_web_url or "")
        self.telegram_profile_catalog_getter = telegram_profile_catalog_getter
        self.whatsapp_profile_catalog_getter = whatsapp_profile_catalog_getter
        self.support_installed_skills_enabled = bool(support_installed_skills_enabled)
        self.support_error_category_hints = support_error_category_hints
        self.support_engine_validation_errors_getter = support_engine_validation_errors_getter
        self.env_get = env_get
        self.init_runtime = init_runtime
        self.runtime_telegram_engine_getter = runtime_telegram_engine_getter
        self.runtime_whatsapp_engine_getter = runtime_whatsapp_engine_getter
        self.runtime_telegram_show_buttons = bool(runtime_telegram_show_buttons)
        self.runtime_local_lease_seconds = int(runtime_local_lease_seconds or 0)
        self.runtime_non_retryable_run_error_hints = runtime_non_retryable_run_error_hints
        self.runtime_builtin_skills_limit_builder = runtime_builtin_skills_limit_builder
        self.normalize_agent_role = normalize_agent_role
        self.allow_any_chat_getter = allow_any_chat_getter
        self.http_json_request = http_json_request
        self.telegram_profile_fields = telegram_profile_fields
        self.normalize_trust_mode = normalize_trust_mode
        self.normalize_execution_target = normalize_execution_target
        self.decide_execution_target = decide_execution_target
        self.apply_execution_route_metadata = apply_execution_route_metadata
        self.execute_agent_turn_request = execute_agent_turn_request
        self.run_start_request_class = run_start_request_class
        self.start_run_request = start_run_request
        self.create_run = create_run
        self.inherit_owner_user_id = inherit_owner_user_id
        self.agent_machine_full_trust_enabled = agent_machine_full_trust_enabled
        self.run_history = run_history
        self.run_history_lock = run_history_lock
        self.runtime_metrics = runtime_metrics
        self.metrics_lock = metrics_lock
        self.utc_now = utc_now
        self.parse_utc_ts = parse_utc_ts
        self.worker_online_helper = worker_online_helper
        self.local_queue_lock = local_queue_lock
        self.local_pending_run_ids = local_pending_run_ids
        self.local_claimed_runs = local_claimed_runs
        self.local_worker_registry = local_worker_registry
        self.truncate_one_line = truncate_one_line
        self.bool_from_any = bool_from_any
        self.local_companion_snapshot = local_companion_snapshot
        self.current_runtime_metrics = current_runtime_metrics
        self.latest_runtime_run_summary = latest_runtime_run_summary
        self.list_vault_connectors = list_vault_connectors
        self.list_recent_connector_messages = list_recent_connector_messages
        self.query_active_installed_skills = query_active_installed_skills
        self.runtime_builtin_skills_getter = runtime_builtin_skills_getter
        self.runtime_skills_snapshot_getter = runtime_skills_snapshot_getter
        self.bridge_facade_getter = bridge_facade_getter
        self.helper_registry_bridge_class = helper_registry_bridge_class
        self.telegram_service_registry_class = telegram_service_registry_class
        self.whatsapp_service_registry_class = whatsapp_service_registry_class
        self.support_registry_class = support_registry_class
        self.profile_service_class = profile_service_class
        self.runtime_status_service_class = runtime_status_service_class
        self.workflow_setup_service_class = workflow_setup_service_class
        self.connector_context_service_class = connector_context_service_class
        self.common_support_service_class = common_support_service_class
        self.skill_service_class = skill_service_class
        self.channel_support_service_class = channel_support_service_class
        self.runtime_registry_class = runtime_registry_class
        self.connector_support_service_class = connector_support_service_class
        self.transport_service_class = transport_service_class
        self.terminal_service_class = terminal_service_class
        self.run_entry_service_class = run_entry_service_class
        self.runtime_support_service_class = runtime_support_service_class
        self.menu_service_class = menu_service_class

        self._helper_registry_bridge: Optional[Any] = None
        self._telegram_service_registry: Optional[Any] = None
        self._whatsapp_service_registry: Optional[Any] = None
        self._support_service_registry: Optional[Any] = None
        self._runtime_service_registry: Optional[Any] = None

    def telegram_service_registry(self) -> Any:
        if self._telegram_service_registry is None:
            support_registry = self.support_service_registry()
            runtime_registry = self.runtime_service_registry()
            helper_registry = self.telegram_helper_registry()
            event_bridge = self.event_bridge_service()
            self._telegram_service_registry = self.telegram_service_registry_class(
                project_root=self.project_root,
                default_workspace_id=self.telegram_default_workspace_id_getter(),
                default_chat_prefix=self.default_chat_prefix,
                onboarding_enabled=self.telegram_onboarding_enabled,
                require_prefix=self.telegram_require_prefix_getter(),
                prefix=self.telegram_prefix_getter(),
                space_status_enabled=self.telegram_space_status_enabled,
                media_max_items=self.telegram_media_max_items,
                max_updates=self.telegram_max_updates,
                poll_seconds=self.telegram_poll_seconds,
                delivery_mode=self.telegram_delivery_mode_getter(),
                run_timeout_seconds=self.telegram_run_timeout_seconds_getter(),
                max_reply_chars=self.telegram_max_reply_chars_getter(),
                send_ack=self.telegram_send_ack_getter(),
                state=self.telegram_state_getter(),
                lock=self.telegram_lock_getter(),
                state_file=self.telegram_state_file,
                read_json=self.read_json,
                write_json=self.write_json,
                persist_state=lambda: self.telegram_service_registry().telegram_autopilot_state_service().persist_state(),
                utc_now_iso=self.utc_now_iso,
                classify_error=lambda detail: support_registry.channel_support_service().classify_error(detail),
                iso_from_epoch=lambda ts: support_registry.channel_support_service().iso_from_epoch(ts),
                normalize_workspace_id=self.normalize_workspace_id,
                thread_alive=self.telegram_thread_alive,
                enabled=self.telegram_enabled_getter(),
                default_profile=self.telegram_default_profile_getter(),
                list_connector_entries=lambda requested_workspace_id=None: self.telegram_service_registry().telegram_autopilot_state_service().list_connector_entries(
                    requested_workspace_id or self.telegram_default_workspace_id_getter()
                ),
                resolve_profile=lambda entry: support_registry.profile_service().resolve_telegram_profile(entry),
                resolve_allow_from=lambda entry: runtime_registry.connector_support_service().resolve_allow_from(
                    entry,
                    self.telegram_allow_from_value(),
                ),
                connector_state=lambda connector_id: self.telegram_service_registry().telegram_autopilot_state_service().connector_state(
                    connector_id
                ),
                set_connector_state=lambda connector_id, patch: self.telegram_service_registry().telegram_autopilot_state_service().set_connector_state(
                    connector_id,
                    patch,
                ),
                resolve_secret=lambda entry: runtime_registry.connector_support_service().get_secret(entry),
                load_vault=self.load_vault,
                workspace_visible=self.workspace_visible,
                connector_paused=lambda item: runtime_registry.connector_support_service().connector_paused(item),
                get_updates_process_lock=self.get_updates_process_lock,
                notify_pending_approvals=lambda **kwargs: None,
                telegram_api_request=lambda bot_token, method, **kwargs: runtime_registry.transport_service().api_request(
                    bot_token,
                    method,
                    **kwargs,
                ),
                record_channel_event=lambda **kwargs: event_bridge.record_channel_event(**kwargs),
                record_channel_event_throttled=lambda **kwargs: event_bridge.record_channel_event_throttled(**kwargs),
                send_message=lambda *args, **kwargs: runtime_registry.transport_service().send_message(*args, **kwargs),
                send_chat_action=lambda *args, **kwargs: runtime_registry.transport_service().send_chat_action(*args, **kwargs),
                edit_message=lambda *args, **kwargs: runtime_registry.transport_service().edit_message(*args, **kwargs),
                autopilot_log=lambda message: support_registry.channel_support_service().telegram_autopilot_log(message),
                autopilot_mark_error=lambda detail, source: self.telegram_service_registry().telegram_autopilot_runtime_service().mark_error(
                    detail,
                    source=source,
                ),
                mark_poll=lambda clear_error: self.telegram_service_registry().telegram_autopilot_runtime_service().mark_poll(
                    clear_error=clear_error
                ),
                mark_started=self.mark_telegram_started,
                normalize_profile_field=lambda raw_value: helper_registry.profile_service().normalize_profile_field(raw_value),
                select_skill_from_text=lambda raw_text: support_registry.skill_service().select_skill_from_text(raw_text),
                skill_goal_builder=lambda skill: support_registry.skill_service().telegram_skill_goal(skill),
                help_text=lambda profile: helper_registry.routing_service().help_text(profile),
                skills_menu_text=lambda profile: support_registry.skill_service().telegram_skills_menu_text(profile),
                menu_keyboard=lambda profile, menu_id: runtime_registry.menu_service().menu_keyboard(profile, menu_id),
                onboarding_prompt=lambda step_index, retry: helper_registry.profile_service().onboarding_prompt(
                    step_index,
                    retry=retry,
                ),
                onboarding_start=lambda workspace_id, chat_id: helper_registry.profile_service().start_onboarding(
                    workspace_id,
                    chat_id,
                ),
                profile_text=lambda profile, chat_profile: helper_registry.profile_service().profile_text(
                    profile,
                    chat_profile,
                ),
                profile_help_text=lambda profile: helper_registry.profile_service().profile_help_text(profile),
                profile_set=lambda workspace_id, chat_id, field_name, value: helper_registry.profile_service().set_profile_field(
                    workspace_id,
                    chat_id,
                    field_name,
                    value,
                ),
                profile_clear=lambda workspace_id, chat_id, field_name: helper_registry.profile_service().clear_profile(
                    workspace_id,
                    chat_id,
                    field_name,
                ),
                runtime_status_text=lambda workspace_id: support_registry.runtime_status_service().runtime_status_text(workspace_id),
                approvals_list=lambda limit, workspace_id=None: [],
                approvals_text=lambda payload, prefix: "",
                approval_resolve=lambda event_id, approved, note, workspace_id=None: {"ok": True, "resolution": "approved"},
                approval_result_text=lambda payload, approved: "",
                extract_message=lambda update: helper_registry.media_service().extract_message(update),
                chat_matches=lambda configured_chat_id, chat: runtime_registry.connector_support_service().chat_matches(
                    configured_chat_id,
                    chat,
                ),
                store_attachments=lambda **kwargs: helper_registry.media_service().store_attachments(**kwargs),
                route_message=lambda message_text, profile: helper_registry.routing_service().route_message(
                    message_text,
                    profile,
                ),
                session_key_builder=lambda chat_id: support_registry.channel_support_service().telegram_session_key(chat_id),
                trace_id_builder=lambda chat_id, update_id, message_id: support_registry.channel_support_service().telegram_trace_id(
                    chat_id,
                    update_id,
                    message_id,
                ),
                guided_setup_handler=lambda **kwargs: support_registry.workflow_setup_service().handle_telegram_guided_automation_setup(
                    **kwargs,
                    enabled=self.telegram_guided_automation_setup_enabled,
                ),
                sender_allowed=lambda sender, allow_from: runtime_registry.connector_support_service().sender_allowed(
                    sender,
                    allow_from,
                ),
                get_chat_profile=lambda workspace_id, chat_id: helper_registry.profile_service().get_profile(
                    workspace_id,
                    chat_id,
                ),
                explicit_run_command=lambda raw_text: helper_registry.routing_service().is_explicit_run_command(raw_text),
                onboarding_get_state=lambda workspace_id, chat_id: helper_registry.profile_service().get_onboarding_state(
                    workspace_id,
                    chat_id,
                ),
                onboarding_consume_answer=lambda workspace_id, chat_id, text: helper_registry.profile_service().onboarding_consume_answer(
                    workspace_id,
                    chat_id,
                    text,
                ),
                profile_get=lambda workspace_id, chat_id: helper_registry.profile_service().get_profile(
                    workspace_id,
                    chat_id,
                ),
                profile_has_context=lambda chat_profile: helper_registry.profile_service().profile_has_context(chat_profile),
                build_goal_with_profile=lambda goal, chat_profile: helper_registry.profile_service().build_goal_with_profile(
                    goal,
                    chat_profile,
                ),
                build_goal_with_attachments=lambda goal, attachments: helper_registry.media_service().build_goal_with_attachments(
                    goal,
                    attachments,
                ),
                workspace_connector_context=lambda **kwargs: support_registry.connector_context_service().workspace_connector_context(
                    **kwargs
                ),
                build_goal_with_connector_context=lambda goal, prompt_append: support_registry.connector_context_service().build_goal_with_connector_context(
                    goal,
                    prompt_append,
                ),
                space_question_via_mcp=lambda goal, enabled, project_root: self.telegram_space_question_via_mcp(
                    goal,
                    enabled=enabled,
                    project_root=project_root,
                ),
                installed_skill_query=lambda **kwargs: support_registry.connector_context_service().installed_skill_query(
                    **kwargs
                ),
                truncate_one_line=lambda text, limit: support_registry.channel_support_service().truncate_one_line(text, limit),
                create_run=lambda **kwargs: runtime_registry.run_entry_service().create_telegram_run(
                    **kwargs,
                    media_max_items=self.telegram_media_max_items,
                    trust_mode_value=self.telegram_trust_mode_value_getter(),
                    execution_target_value=self.telegram_execution_target_value_getter(),
                ),
                include_run_meta=lambda: support_registry.channel_support_service().include_run_meta(),
                humanize_run_summary=lambda text: runtime_registry.runtime_support_service().humanize_telegram_run_summary(text),
                runs_get=self.runs_get,
                latest_run_error_message=lambda run: runtime_registry.runtime_support_service().latest_run_error_message(run),
                is_non_retryable_run_error=lambda error: runtime_registry.runtime_support_service().is_non_retryable_run_error(error),
                friendly_run_error=lambda error: runtime_registry.runtime_support_service().friendly_run_error(error),
                summarize_run_terminal_result=lambda run, limit: runtime_registry.runtime_support_service().summarize_run_terminal_result(
                    run,
                    limit,
                ),
                local_companion_snapshot=lambda: runtime_registry.runtime_support_service().local_companion_snapshot(),
                can_auto_approve_wait=lambda run: runtime_registry.run_entry_service().can_auto_approve_wait(run),
                pending_confirmation_payload=lambda run: runtime_registry.run_entry_service().pending_confirmation_payload(run),
                emit_channel_run_delivery_event=outbox_service.emit_channel_run_delivery_event,
                record_activity_event=lambda **kwargs: run_async_tool_call(
                    activity_ledger_service.append_activity_event(**kwargs)
                ),
                sleep=time.sleep,
            )
        return self._telegram_service_registry

    def whatsapp_service_registry(self) -> Any:
        if self._whatsapp_service_registry is None:
            support_registry = self.support_service_registry()
            runtime_registry = self.runtime_service_registry()
            helper_registry = self.telegram_helper_registry()
            event_bridge = self.event_bridge_service()
            self._whatsapp_service_registry = self.whatsapp_service_registry_class(
                state=self.whatsapp_state_getter(),
                lock=self.whatsapp_lock_getter(),
                read_json=self.read_json,
                write_json=self.write_json,
                state_file=self.whatsapp_state_file,
                utc_now_iso=self.utc_now_iso,
                classify_error=lambda detail: support_registry.channel_support_service().classify_error(detail),
                normalize_workspace_id=self.normalize_workspace_id,
                load_vault=self.load_vault,
                workspace_visible=self.workspace_visible,
                connector_paused=lambda item: runtime_registry.connector_support_service().connector_paused(item),
                resolve_vault_credential=self.resolve_vault_credential,
                enabled=self.whatsapp_enabled_getter(),
                default_profile=self.whatsapp_default_profile_getter(),
                require_prefix=self.whatsapp_require_prefix_getter(),
                prefix=self.whatsapp_prefix_getter(),
                run_timeout_seconds=self.whatsapp_run_timeout_seconds_getter(),
                max_reply_chars=self.whatsapp_max_reply_chars_getter(),
                send_ack=self.whatsapp_send_ack_getter(),
                include_run_meta=lambda: support_registry.channel_support_service().include_run_meta(),
                truncate_one_line=lambda text, limit: support_registry.channel_support_service().truncate_one_line(text, limit),
                poll_run_terminal_result=lambda run_id, max_reply_chars=None: self.telegram_service_registry().telegram_run_dispatch_service().poll_run_terminal_result(
                    run_id,
                    max_reply_chars=max_reply_chars,
                ),
                run_reply_text=lambda status, run_id, summary: self.telegram_service_registry().telegram_run_dispatch_service().run_reply_text(
                    status,
                    run_id,
                    summary,
                ),
                emit_channel_run_delivery_event=outbox_service.emit_channel_run_delivery_event,
                append_dead_letter=lambda **kwargs: event_bridge.append_channel_dead_letter(**kwargs),
                record_channel_event=lambda **kwargs: event_bridge.record_channel_event(**kwargs),
                log_error=lambda message: print(f"[whatsapp-autopilot {self.utc_now_iso()}] {message}", flush=True),
                safe_path_token=self.safe_path_token,
                resolve_profile=lambda entry: support_registry.profile_service().resolve_whatsapp_profile(entry),
                route_message=lambda body, profile: helper_registry.routing_service().route_message(body, profile),
                help_text=lambda profile: support_registry.profile_service().whatsapp_help_text(profile),
                runtime_status_text=lambda workspace_id: support_registry.runtime_status_service().runtime_status_text(workspace_id),
                approvals_list=lambda limit, workspace_id=None: [],
                approvals_text=lambda payload, prefix: "",
                approval_resolve=lambda event_id, approved, note, workspace_id=None: {"ok": True, "resolution": "approved"},
                approval_result_text=lambda payload, approved: "",
                create_run=lambda **kwargs: runtime_registry.run_entry_service().create_whatsapp_run(
                    **kwargs,
                    trust_mode_value=self.whatsapp_trust_mode_value_getter(),
                    execution_target_value=self.whatsapp_execution_target_value_getter(),
                ),
                session_key_builder=lambda inbound_from, inbound_to: support_registry.channel_support_service().whatsapp_session_key(
                    inbound_from,
                    inbound_to,
                ),
                default_chat_prefix=self.default_chat_prefix,
                require_explicit_opt_in=self.bool_from_any(
                    self.env_get("ORION_WHATSAPP_AUTOPILOT_REQUIRE_EXPLICIT_OPT_IN", "1"),
                    True,
                ),
                redact_event_text=self.bool_from_any(
                    self.env_get("ORION_WHATSAPP_AUTOPILOT_REDACT_EVENT_TEXT", "1"),
                    True,
                ),
                retention_days=max(
                    1,
                    int(str(self.env_get("ORION_WHATSAPP_AUTOPILOT_RETENTION_DAYS", "30") or "30").strip() or "30"),
                ),
            )
        return self._whatsapp_service_registry

    def telegram_helper_registry(self) -> Any:
        return self.helper_registry_bridge_service()

    def helper_registry_bridge_service(self) -> Any:
        if self._helper_registry_bridge is None:
            self._helper_registry_bridge = TelegramConnectorServices(
                project_root=self.project_root,
                default_workspace_id=self.telegram_default_workspace_id_getter(),
                default_chat_prefix=self.default_chat_prefix,
                onboarding_enabled=self.telegram_onboarding_enabled,
                require_prefix=self.telegram_require_prefix_getter(),
                prefix=self.telegram_prefix_getter(),
                space_status_enabled=self.telegram_space_status_enabled,
                media_max_items=self.telegram_media_max_items,
                max_updates=self.telegram_max_updates,
                poll_seconds=self.telegram_poll_seconds,
                delivery_mode=self.telegram_delivery_mode_getter(),
                run_timeout_seconds=self.telegram_run_timeout_seconds_getter(),
                max_reply_chars=self.telegram_max_reply_chars_getter(),
                send_ack=self.telegram_send_ack_getter(),
                state=self.telegram_state_getter(),
                lock=self.telegram_lock_getter(),
                state_file=self.telegram_state_file,
                read_json=self.read_json,
                write_json=self.write_json,
                persist_state=lambda: self.telegram_helper_registry().persist_state(),
                utc_now_iso=self.utc_now_iso,
                classify_error=lambda detail: self.support_service_registry().channel_support_service().classify_error(detail),
                iso_from_epoch=lambda ts: self.support_service_registry().channel_support_service().iso_from_epoch(ts),
                normalize_workspace_id=self.normalize_workspace_id,
                thread_alive=self.telegram_thread_alive,
                enabled=self.telegram_enabled_getter(),
                default_profile=self.telegram_default_profile_getter(),
                list_connector_entries=lambda: self.telegram_helper_registry().list_connector_entries(self.telegram_default_workspace_id_getter()),
                resolve_profile=lambda entry: self.support_service_registry().profile_service().resolve_telegram_profile(entry),
                resolve_allow_from=lambda entry: self.runtime_service_registry().connector_support_service().resolve_allow_from(entry, self.telegram_allow_from_value()),
                connector_state=lambda connector_id: self.telegram_helper_registry().connector_state(connector_id),
                set_connector_state=lambda connector_id, patch: self.telegram_helper_registry().set_connector_state(connector_id, patch),
                resolve_secret=lambda entry: self.runtime_service_registry().connector_support_service().get_secret(entry),
                load_vault=self.load_vault,
                workspace_visible=self.workspace_visible,
                connector_paused=lambda item: self.runtime_service_registry().connector_support_service().connector_paused(item),
                get_updates_process_lock=self.get_updates_process_lock,
                notify_pending_approvals=lambda **kwargs: None,
                telegram_api_request=lambda bot_token, method, **kwargs: self.runtime_service_registry().transport_service().api_request(bot_token, method, **kwargs),
                record_channel_event=lambda **kwargs: self.event_bridge_service().record_channel_event(**kwargs),
                record_channel_event_throttled=lambda **kwargs: self.event_bridge_service().record_channel_event_throttled(**kwargs),
                send_message=lambda *args, **kwargs: self.runtime_service_registry().transport_service().send_message(*args, **kwargs),
                send_chat_action=lambda *args, **kwargs: self.runtime_service_registry().transport_service().send_chat_action(*args, **kwargs),
                edit_message=lambda *args, **kwargs: self.runtime_service_registry().transport_service().edit_message(*args, **kwargs),
                autopilot_log=lambda message: self.support_service_registry().channel_support_service().telegram_autopilot_log(message),
                autopilot_mark_error=lambda detail, source: self.telegram_helper_registry().mark_error(detail, source=source),
                mark_poll=lambda clear_error: self.telegram_helper_registry().mark_poll(clear_error=clear_error),
                mark_started=self.mark_telegram_started,
                normalize_profile_field=lambda raw_value: self.telegram_helper_registry().profile_service().normalize_profile_field(raw_value),
                select_skill_from_text=lambda raw_text: self.support_service_registry().skill_service().select_skill_from_text(raw_text),
                skill_goal_builder=lambda skill: self.support_service_registry().skill_service().telegram_skill_goal(skill),
                help_text=lambda profile: self.telegram_helper_registry().routing_service().help_text(profile),
                skills_menu_text=lambda profile: self.support_service_registry().skill_service().telegram_skills_menu_text(profile),
                menu_keyboard=lambda profile, menu_id: self.runtime_service_registry().menu_service().menu_keyboard(profile, menu_id),
                onboarding_prompt=lambda step_index, retry: self.telegram_helper_registry().profile_service().onboarding_prompt(step_index, retry=retry),
                onboarding_start=lambda workspace_id, chat_id: self.telegram_helper_registry().profile_service().start_onboarding(workspace_id, chat_id),
                profile_text=lambda profile, chat_profile: self.telegram_helper_registry().profile_service().profile_text(profile, chat_profile),
                profile_help_text=lambda profile: self.telegram_helper_registry().profile_service().profile_help_text(profile),
                profile_set=lambda workspace_id, chat_id, field_name, value: self.telegram_helper_registry().profile_service().set_profile_field(workspace_id, chat_id, field_name, value),
                profile_clear=lambda workspace_id, chat_id, field_name: self.telegram_helper_registry().profile_service().clear_profile(workspace_id, chat_id, field_name),
                runtime_status_text=lambda workspace_id: self.support_service_registry().runtime_status_service().runtime_status_text(workspace_id),
                approvals_list=lambda limit, workspace_id=None: [],
                approvals_text=lambda payload, prefix: "",
                approval_resolve=lambda event_id, approved, note, workspace_id=None: {"ok": True, "resolution": "approved"},
                approval_result_text=lambda payload, approved: "",
                extract_message=lambda update: self.telegram_helper_registry().media_service().extract_message(update),
                chat_matches=lambda configured_chat_id, chat: self.runtime_service_registry().connector_support_service().chat_matches(configured_chat_id, chat),
                store_attachments=lambda **kwargs: self.telegram_helper_registry().media_service().store_attachments(**kwargs),
                route_message=lambda message_text, profile: self.telegram_helper_registry().routing_service().route_message(message_text, profile),
                session_key_builder=lambda chat_id: self.support_service_registry().channel_support_service().telegram_session_key(chat_id),
                trace_id_builder=lambda chat_id, update_id, message_id: self.support_service_registry().channel_support_service().telegram_trace_id(chat_id, update_id, message_id),
                guided_setup_handler=lambda **kwargs: self.support_service_registry().workflow_setup_service().handle_telegram_guided_automation_setup(**kwargs, enabled=self.telegram_guided_automation_setup_enabled),
                sender_allowed=lambda sender, allow_from: self.runtime_service_registry().connector_support_service().sender_allowed(sender, allow_from),
                get_chat_profile=lambda workspace_id, chat_id: self.telegram_helper_registry().profile_service().get_profile(workspace_id, chat_id),
                explicit_run_command=lambda raw_text: self.telegram_helper_registry().routing_service().is_explicit_run_command(raw_text),
                onboarding_get_state=lambda workspace_id, chat_id: self.telegram_helper_registry().profile_service().get_onboarding_state(workspace_id, chat_id),
                onboarding_consume_answer=lambda workspace_id, chat_id, text: self.telegram_helper_registry().profile_service().onboarding_consume_answer(workspace_id, chat_id, text),
                profile_get=lambda workspace_id, chat_id: self.telegram_helper_registry().profile_service().get_profile(workspace_id, chat_id),
                profile_has_context=lambda chat_profile: self.telegram_helper_registry().profile_service().profile_has_context(chat_profile),
                build_goal_with_profile=lambda goal, chat_profile: self.telegram_helper_registry().profile_service().build_goal_with_profile(goal, chat_profile),
                build_goal_with_attachments=lambda goal, attachments: self.telegram_helper_registry().media_service().build_goal_with_attachments(goal, attachments),
                workspace_connector_context=lambda **kwargs: self.support_service_registry().connector_context_service().workspace_connector_context(**kwargs),
                build_goal_with_connector_context=lambda goal, prompt_append: self.support_service_registry().connector_context_service().build_goal_with_connector_context(goal, prompt_append),
                space_question_via_mcp=lambda goal, enabled, project_root: self.telegram_space_question_via_mcp(goal, enabled=enabled, project_root=project_root),
                installed_skill_query=lambda **kwargs: self.support_service_registry().connector_context_service().installed_skill_query(**kwargs),
                truncate_one_line=lambda text, limit: self.support_service_registry().channel_support_service().truncate_one_line(text, limit),
                create_run=lambda **kwargs: self.runtime_service_registry().run_entry_service().create_telegram_run(**kwargs, media_max_items=self.telegram_media_max_items, trust_mode_value=self.telegram_trust_mode_value_getter(), execution_target_value=self.telegram_execution_target_value_getter()),
                include_run_meta=lambda: self.support_service_registry().channel_support_service().include_run_meta(),
                humanize_run_summary=lambda text: self.runtime_service_registry().runtime_support_service().humanize_telegram_run_summary(text),
                runs_get=self.runs_get,
                latest_run_error_message=lambda run: self.runtime_service_registry().runtime_support_service().latest_run_error_message(run),
                is_non_retryable_run_error=lambda error: self.runtime_service_registry().runtime_support_service().is_non_retryable_run_error(error),
                friendly_run_error=lambda error: self.runtime_service_registry().runtime_support_service().friendly_run_error(error),
                summarize_run_terminal_result=lambda run, limit: self.runtime_service_registry().runtime_support_service().summarize_run_terminal_result(run, limit),
                local_companion_snapshot=lambda: self.runtime_service_registry().runtime_support_service().local_companion_snapshot(),
                can_auto_approve_wait=lambda run: self.runtime_service_registry().run_entry_service().can_auto_approve_wait(run),
                pending_confirmation_payload=lambda run: self.runtime_service_registry().run_entry_service().pending_confirmation_payload(run),
                emit_channel_run_delivery_event=outbox_service.emit_channel_run_delivery_event,
                record_activity_event=lambda **kwargs: run_async_tool_call(activity_ledger_service.append_activity_event(**kwargs)),
                sleep=time.sleep,
                helper_profile_state_file=self.helper_profile_state_file,
                helper_onboarding_state_file=self.helper_onboarding_state_file,
                helper_camera_setup_state_file=self.helper_camera_setup_state_file,
                helper_media_dir=self.helper_media_dir,
                helper_media_enabled=self.helper_media_enabled,
                helper_media_max_items=self.helper_media_max_items,
                helper_media_max_bytes=self.helper_media_max_bytes,
                helper_media_include_in_goal=self.helper_media_include_in_goal,
                helper_quick_goal_templates=self.helper_quick_goal_templates,
                helper_menu_goal_templates=self.helper_menu_goal_templates,
            )
        return self._helper_registry_bridge

    def support_service_registry(self) -> Any:
        if self._support_service_registry is None:
            def _build_common_support_service() -> Any:
                def _import_cognitive_module():
                    from python_engine import cognitive_daemon as _cd  # type: ignore

                    return _cd

                return self.common_support_service_class(
                    project_root=self.project_root,
                    env_get=self.env_get,
                    import_cognitive_module=_import_cognitive_module,
                )

            self._support_service_registry = self.support_registry_class(
                build_profile_service=lambda: self.profile_service_class(
                    default_chat_prefix=self.default_chat_prefix,
                    bool_from_any=self.bool_from_any,
                    telegram_default_profile=self.telegram_default_profile_getter(),
                    telegram_default_prefix=self.telegram_prefix_getter(),
                    telegram_default_require_prefix=self.telegram_require_prefix_getter(),
                    telegram_profile_catalog=self.telegram_profile_catalog_getter(),
                    whatsapp_default_profile=self.whatsapp_default_profile_getter(),
                    whatsapp_default_prefix=self.whatsapp_prefix_getter(),
                    whatsapp_default_require_prefix=self.whatsapp_require_prefix_getter(),
                    whatsapp_profile_catalog=self.whatsapp_profile_catalog_getter(),
                ),
                build_runtime_status_service=lambda: self.runtime_status_service_class(
                    local_companion_snapshot=self.local_companion_snapshot,
                    current_metrics=self.current_runtime_metrics,
                    latest_run_summary=self.latest_runtime_run_summary,
                    runtime_valid=lambda: not self.support_engine_validation_errors_getter(),
                ),
                build_workflow_setup_service=lambda: self.workflow_setup_service_class(
                    workflow_api_url=self.support_workflow_api_url,
                    runtime_url=self.support_runtime_url,
                    web_url=self.support_web_url,
                    init_runtime=self.init_runtime,
                    classify_automation_intent=classify_automation_intent,
                    list_vault_connectors=self.list_vault_connectors,
                    http_json_request=self.http_json_request,
                    runtime_api_headers=lambda: {
                        "Content-Type": "application/json",
                        **(
                            {"X-API-Key": str(self.env_get("ORION_API_KEY", "") or "").strip()}
                            if str(self.env_get("ORION_API_KEY", "") or "").strip()
                            else {}
                        ),
                    },
                    camera_setup_service=lambda: self.telegram_helper_registry().camera_setup_service(),
                ),
                build_connector_context_service=lambda: self.connector_context_service_class(
                    installed_skills_enabled=self.support_installed_skills_enabled,
                    init_runtime=self.init_runtime,
                    list_vault_connectors=self.list_vault_connectors,
                    resolve_vault_credential=self.resolve_vault_credential,
                    list_recent_connector_messages=lambda credentials, limit: self.list_recent_connector_messages(credentials, limit),
                    query_active_installed_skills=self.query_active_installed_skills,
                ),
                build_common_support_service=_build_common_support_service,
                build_skill_service=lambda: self.skill_service_class(
                    default_chat_prefix=self.default_chat_prefix,
                    init_runtime=self.init_runtime,
                    runtime_builtin_skills_getter=self.runtime_builtin_skills_getter,
                    runtime_skills_snapshot_getter=self.runtime_skills_snapshot_getter,
                ),
                build_channel_support_service=lambda: self.channel_support_service_class(
                    error_category_hints=self.support_error_category_hints,
                    utc_now_iso=self.utc_now_iso,
                    normalize_whatsapp_number=lambda value: self.whatsapp_service_registry().whatsapp_transport_service().normalize_number(value),
                    safe_path_token=self.safe_path_token,
                    env_get=self.env_get,
                ),
            )
        return self._support_service_registry

    def runtime_service_registry(self) -> Any:
        if self._runtime_service_registry is None:
            self._runtime_service_registry = self.runtime_registry_class(
                build_connector_support_service=lambda: self.connector_support_service_class(
                    normalize_workspace_id=self.normalize_workspace_id,
                    resolve_vault_credential=self.resolve_vault_credential,
                    normalize_agent_role=self.normalize_agent_role,
                    allow_any_chat=self.allow_any_chat_getter(),
                ),
                build_transport_service=lambda: self.transport_service_class(
                    poll_seconds=self.telegram_poll_seconds,
                    http_json_request=self.http_json_request,
                    session_key=lambda chat_id: self.channel_support_service().telegram_session_key(chat_id),
                    safe_path_token=self.safe_path_token,
                    reply_keyboard=lambda profile: self.menu_service().reply_keyboard(profile),
                    append_dead_letter=lambda **kwargs: self.event_bridge_service().append_channel_dead_letter(**kwargs),
                    record_channel_event=lambda **kwargs: self.event_bridge_service().record_channel_event(**kwargs),
                    utc_now_iso=self.utc_now_iso,
                ),
                build_terminal_service=lambda: self.terminal_service_class(
                    normalize_workspace_id=self.normalize_workspace_id,
                    chat_id_from_session_key=lambda key: self.common_support_service().chat_id_from_session_key(key),
                    list_connector_entries=lambda requested_workspace_id=None: self.telegram_service_registry().telegram_autopilot_state_service().list_connector_entries(
                        requested_workspace_id
                    ),
                    get_secret=lambda entry: self.connector_support_service().get_secret(entry),
                    resolve_profile=lambda entry: self.profile_service().resolve_telegram_profile(entry),
                    route_message=lambda text, profile: self.telegram_helper_registry().routing_service().route_message(text, profile),
                    get_chat_profile=lambda workspace_id, chat_id: self.telegram_helper_registry().profile_service().get_profile(
                        workspace_id,
                        chat_id,
                    ),
                    build_goal_with_profile=lambda goal, profile: self.telegram_helper_registry().profile_service().build_goal_with_profile(
                        goal,
                        profile,
                    ),
                    workspace_connector_context=lambda **kwargs: self.connector_context_service().workspace_connector_context(**kwargs),
                    build_goal_with_connector_context=lambda goal, prompt: self.connector_context_service().build_goal_with_connector_context(
                        goal,
                        prompt,
                    ),
                    installed_skill_query=lambda **kwargs: self.connector_context_service().installed_skill_query(**kwargs),
                    create_run=lambda **kwargs: self.run_entry_service().create_telegram_run(
                        **kwargs,
                        media_max_items=self.telegram_media_max_items,
                        trust_mode_value=self.telegram_trust_mode_value_getter(),
                        execution_target_value=self.telegram_execution_target_value_getter(),
                    ),
                    wait_for_run_terminal_status=lambda run_id, timeout_seconds=None, max_reply_chars=None: self.telegram_service_registry().telegram_run_dispatch_service().wait_for_terminal_status(
                        run_id,
                        timeout_seconds=timeout_seconds,
                        max_reply_chars=max_reply_chars,
                    ),
                    runs_get=self.runs_get,
                    session_key=lambda chat_id: self.channel_support_service().telegram_session_key(chat_id),
                    safe_path_token=self.safe_path_token,
                    send_message=lambda **kwargs: self.transport_service().send_message(**kwargs),
                    set_connector_state=lambda connector_id, patch: self.telegram_service_registry().telegram_autopilot_state_service().set_connector_state(
                        connector_id,
                        patch,
                    ),
                    utc_now_iso=self.utc_now_iso,
                ),
                build_run_entry_service=lambda: self.run_entry_service_class(
                    telegram_profile_fields=list(self.telegram_profile_fields),
                    telegram_engine=self.runtime_telegram_engine_getter(),
                    whatsapp_engine=self.runtime_whatsapp_engine_getter(),
                    safe_path_token=self.safe_path_token,
                    assigned_agent_role=lambda entry: self.connector_support_service().connector_assigned_agent_role(entry),
                    normalize_trust_mode=self.normalize_trust_mode,
                    normalize_execution_target=self.normalize_execution_target,
                    decide_execution_target=self.decide_execution_target,
                    apply_execution_route_metadata=self.apply_execution_route_metadata,
                    execute_agent_turn_request=self.execute_agent_turn_request,
                    route_transport_channel_message=lambda **kwargs: __import__(
                        "server_modules.agent_channel_router",
                        fromlist=["route_transport_channel_message_sync"],
                    ).route_transport_channel_message_sync(**kwargs),
                    run_start_request_class=self.run_start_request_class,
                    start_run_request=self.start_run_request,
                    create_run=self.create_run,
                    record_channel_event=lambda **kwargs: self.event_bridge_service().record_channel_event(**kwargs),
                    telegram_session_key=lambda chat_id: self.channel_support_service().telegram_session_key(chat_id),
                    whatsapp_session_key=lambda from_number, to_number: self.channel_support_service().whatsapp_session_key(
                        from_number,
                        to_number,
                    ),
                    inherit_owner_user_id=self.inherit_owner_user_id,
                    agent_machine_full_trust_enabled=self.agent_machine_full_trust_enabled,
                    telegram_runs_started=lambda: (
                        self.telegram_state_getter().__setitem__("runs_started", int(self.telegram_state_getter().get("runs_started") or 0) + 1),
                        self.telegram_service_registry().telegram_autopilot_state_service().persist_state(),
                    ),
                    whatsapp_runs_started=lambda: (
                        self.whatsapp_state_getter().__setitem__("runs_started", int(self.whatsapp_state_getter().get("runs_started") or 0) + 1),
                        self.whatsapp_service_registry().whatsapp_autopilot_state_service().persist_state(),
                    ),
                ),
                build_runtime_support_service=lambda: self.runtime_support_service_class(
                    run_history=self.run_history,
                    run_history_lock=self.run_history_lock,
                    runtime_metrics=self.runtime_metrics,
                    metrics_lock=self.metrics_lock,
                    utc_now=self.utc_now,
                    parse_utc_ts=self.parse_utc_ts,
                    worker_online_helper=self.worker_online_helper,
                    local_lease_seconds=self.runtime_local_lease_seconds,
                    local_queue_lock=self.local_queue_lock,
                    local_pending_run_ids=self.local_pending_run_ids,
                    local_claimed_runs=self.local_claimed_runs,
                    local_worker_registry=self.local_worker_registry,
                    truncate_one_line=self.truncate_one_line,
                    non_retryable_run_error_hints=list(self.runtime_non_retryable_run_error_hints),
                ),
                build_menu_service=lambda: self.menu_service_class(
                    default_chat_prefix=self.default_chat_prefix,
                    show_buttons=self.runtime_telegram_show_buttons,
                    runtime_active_skills=lambda scope_key, limit: self.runtime_builtin_skills_limit_builder(scope_key, limit),
                ),
            )
        return self._runtime_service_registry

    def profile_service(self) -> Any:
        return self.support_service_registry().profile_service()

    def connector_support_service(self) -> Any:
        return self.runtime_service_registry().connector_support_service()

    def runtime_status_service(self) -> Any:
        return self.support_service_registry().runtime_status_service()

    def workflow_setup_service(self) -> Any:
        return self.support_service_registry().workflow_setup_service()

    def connector_context_service(self) -> Any:
        return self.support_service_registry().connector_context_service()

    def transport_service(self) -> Any:
        return self.runtime_service_registry().transport_service()

    def terminal_service(self) -> Any:
        return self.runtime_service_registry().terminal_service()

    def common_support_service(self) -> Any:
        return self.support_service_registry().common_support_service()

    def run_entry_service(self) -> Any:
        return self.runtime_service_registry().run_entry_service()

    def runtime_support_service(self) -> Any:
        return self.runtime_service_registry().runtime_support_service()

    def skill_service(self) -> Any:
        return self.support_service_registry().skill_service()

    def channel_support_service(self) -> Any:
        return self.support_service_registry().channel_support_service()

    def menu_service(self) -> Any:
        return self.runtime_service_registry().menu_service()

    def event_bridge_service(self) -> Any:
        return self.bridge_facade_getter().event_bridge_service()
