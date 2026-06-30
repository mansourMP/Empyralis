from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence

from server_modules.connectors.autopilot_bridge_facade_service import AutopilotBridgeFacadeService
from server_modules.connectors.autopilot_registry_facade_service import AutopilotRegistryFacadeService
from server_modules.connectors.autopilot_runtime_facade_service import AutopilotRuntimeFacadeService


class AutopilotConnectorShellService:
    def __init__(
        self,
        *,
        global_namespace: Dict[str, Any],
        sync_server_globals: Sequence[str],
        project_root: Path,
        default_chat_prefix: str,
        telegram_onboarding_enabled: bool,
        telegram_space_status_enabled: bool,
        telegram_media_max_items: int,
        telegram_max_updates: int,
        telegram_poll_seconds: float,
        telegram_guided_automation_setup_enabled: bool,
        telegram_media_enabled: bool,
        telegram_media_dir: Path,
        telegram_media_max_bytes: int,
        telegram_media_include_in_goal: bool,
        telegram_camera_setup_state_file: Path,
        telegram_profile_state_file: Path,
        telegram_onboarding_state_file: Path,
        quick_goal_templates: Dict[str, str],
        menu_goal_templates: Dict[str, str],
        workflow_api_url: str,
        runtime_url: str,
        web_url: str,
        installed_skills_enabled: bool,
        error_category_hints: Any,
        non_retryable_run_error_hints: Any,
        dead_letter_lock: Any,
        dead_letter_file: Path,
        dead_letter_limit: int,
        local_lease_seconds: int,
        telegram_show_buttons: bool,
        telegram_profile_fields: Any,
        run_history: Any,
        run_history_lock: Any,
        runtime_metrics: Dict[str, Any],
        metrics_lock: Any,
        local_queue_lock: Any,
        local_pending_run_ids: Any,
        local_claimed_runs: Any,
        local_worker_registry: Dict[str, Any],
        server_getter: Callable[[], Any],
        server_setter: Callable[[Any], None],
        import_server: Callable[[], Any],
        read_json: Callable[[Path, Any], Any],
        write_json: Callable[[Path, Any], Any],
        utc_now_iso: Callable[[], str],
        normalize_workspace_id: Callable[[Any], str],
        load_vault: Callable[[], Any],
        workspace_visible: Callable[[str, Any], bool],
        get_updates_process_lock: Callable[[str], Any],
        resolve_vault_credential: Callable[[str, Optional[str]], Any],
        http_json_request: Callable[..., Any],
        safe_path_token_impl: Callable[[Any], str],
        telegram_space_question_via_mcp: Callable[..., Dict[str, Any]],
        utc_now: Callable[[], Any],
        parse_utc_ts: Callable[[Any], Any],
        create_run: Callable[..., Any],
        decide_execution_target: Callable[[Dict[str, Any]], Dict[str, Any]],
        apply_execution_route_metadata: Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]],
        normalize_trust_mode: Callable[[Any], str],
        normalize_execution_target: Callable[[Any], str],
        bridge_facade_class: Callable[..., Any] = AutopilotBridgeFacadeService,
        registry_facade_class: Callable[..., Any] = AutopilotRegistryFacadeService,
        runtime_facade_class: Callable[..., Any] = AutopilotRuntimeFacadeService,
    ) -> None:
        self.global_namespace = global_namespace
        self.sync_server_globals = tuple(sync_server_globals)
        self.project_root = Path(project_root)
        self.default_chat_prefix = str(default_chat_prefix or "")
        self.telegram_onboarding_enabled = bool(telegram_onboarding_enabled)
        self.telegram_space_status_enabled = bool(telegram_space_status_enabled)
        self.telegram_media_max_items = int(telegram_media_max_items)
        self.telegram_max_updates = int(telegram_max_updates)
        self.telegram_poll_seconds = float(telegram_poll_seconds)
        self.telegram_guided_automation_setup_enabled = bool(telegram_guided_automation_setup_enabled)
        self.telegram_media_enabled = bool(telegram_media_enabled)
        self.telegram_media_dir = Path(telegram_media_dir)
        self.telegram_media_max_bytes = int(telegram_media_max_bytes)
        self.telegram_media_include_in_goal = bool(telegram_media_include_in_goal)
        self.telegram_camera_setup_state_file = Path(telegram_camera_setup_state_file)
        self.telegram_profile_state_file = Path(telegram_profile_state_file)
        self.telegram_onboarding_state_file = Path(telegram_onboarding_state_file)
        self.quick_goal_templates = quick_goal_templates
        self.menu_goal_templates = menu_goal_templates
        self.workflow_api_url = str(workflow_api_url or "")
        self.runtime_url = str(runtime_url or "")
        self.web_url = str(web_url or "")
        self.installed_skills_enabled = bool(installed_skills_enabled)
        self.error_category_hints = error_category_hints
        self.non_retryable_run_error_hints = non_retryable_run_error_hints
        self.dead_letter_lock = dead_letter_lock
        self.dead_letter_file = Path(dead_letter_file)
        self.dead_letter_limit = int(dead_letter_limit or 0)
        self.local_lease_seconds = int(local_lease_seconds or 0)
        self.telegram_show_buttons = bool(telegram_show_buttons)
        self.telegram_profile_fields = telegram_profile_fields
        self.run_history = run_history
        self.run_history_lock = run_history_lock
        self.runtime_metrics = runtime_metrics
        self.metrics_lock = metrics_lock
        self.local_queue_lock = local_queue_lock
        self.local_pending_run_ids = local_pending_run_ids
        self.local_claimed_runs = local_claimed_runs
        self.local_worker_registry = local_worker_registry
        self.server_getter = server_getter
        self.server_setter = server_setter
        self.import_server = import_server
        self.read_json = read_json
        self.write_json = write_json
        self.utc_now_iso = utc_now_iso
        self.normalize_workspace_id = normalize_workspace_id
        self.load_vault = load_vault
        self.workspace_visible = workspace_visible
        self.get_updates_process_lock = get_updates_process_lock
        self.resolve_vault_credential = resolve_vault_credential
        self.http_json_request = http_json_request
        self.safe_path_token_impl = safe_path_token_impl
        self.telegram_space_question_via_mcp = telegram_space_question_via_mcp
        self.utc_now = utc_now
        self.parse_utc_ts = parse_utc_ts
        self.create_run = create_run
        self.decide_execution_target = decide_execution_target
        self.apply_execution_route_metadata = apply_execution_route_metadata
        self.normalize_trust_mode = normalize_trust_mode
        self.normalize_execution_target = normalize_execution_target
        self.bridge_facade_class = bridge_facade_class
        self.registry_facade_class = registry_facade_class
        self.runtime_facade_class = runtime_facade_class

        self._bridge_facade: Optional[Any] = None
        self._registry_facade: Optional[Any] = None
        self._runtime_facade: Optional[Any] = None

    def _g(self, name: str, default: Any = None) -> Any:
        return self.global_namespace.get(name, default)

    def runs_get(self, run_id: Any) -> Optional[Dict[str, Any]]:
        resolved_run_id = str(run_id or "").strip()
        if not resolved_run_id:
            return None
        runs = self._g("runs")
        if isinstance(runs, dict) and isinstance(runs.get(resolved_run_id), dict):
            return runs.get(resolved_run_id)
        try:
            from server_modules import run_state_repository

            live = run_state_repository.sync_get_live_run(resolved_run_id)
            if isinstance(live, dict):
                return live
            archived = run_state_repository.sync_get_archived_run(resolved_run_id)
            return archived if isinstance(archived, dict) else None
        except Exception:
            return None

    def _telegram_lock(self) -> Any:
        return self._g("TELEGRAM_AUTOPILOT_LOCK") or threading.Lock()

    def _whatsapp_lock(self) -> Any:
        return self._g("WHATSAPP_AUTOPILOT_LOCK") or threading.Lock()

    def _telegram_workspace_id(self) -> str:
        return str(self._g("ORION_TELEGRAM_AUTOPILOT_WORKSPACE_ID") or "default")

    def _whatsapp_workspace_id(self) -> str:
        return str(self._g("ORION_WHATSAPP_AUTOPILOT_WORKSPACE_ID") or "default")

    def _init_runtime(self) -> None:
        self.runtime_facade_service().init_runtime()

    def registry_facade_service(self) -> Any:
        if self._registry_facade is None:
            self._registry_facade = self.registry_facade_class(
                project_root=self.project_root,
                default_chat_prefix=self.default_chat_prefix,
                telegram_default_workspace_id_getter=self._telegram_workspace_id,
                telegram_onboarding_enabled=self.telegram_onboarding_enabled,
                telegram_require_prefix_getter=lambda: bool(self._g("ORION_TELEGRAM_AUTOPILOT_REQUIRE_PREFIX", False)),
                telegram_prefix_getter=lambda: str(self._g("ORION_TELEGRAM_AUTOPILOT_PREFIX") or ""),
                telegram_space_status_enabled=self.telegram_space_status_enabled,
                telegram_media_max_items=self.telegram_media_max_items,
                telegram_max_updates=self.telegram_max_updates,
                telegram_poll_seconds=self.telegram_poll_seconds,
                telegram_delivery_mode_getter=lambda: str(self._g("ORION_TELEGRAM_AUTOPILOT_DELIVERY_MODE") or "polling"),
                telegram_run_timeout_seconds_getter=lambda: int(self._g("ORION_TELEGRAM_AUTOPILOT_RUN_TIMEOUT_SECONDS") or 180),
                telegram_max_reply_chars_getter=lambda: int(self._g("ORION_TELEGRAM_AUTOPILOT_MAX_REPLY_CHARS") or 1200),
                telegram_send_ack_getter=lambda: bool(self._g("ORION_TELEGRAM_AUTOPILOT_SEND_ACK")),
                telegram_enabled_getter=lambda: bool(self._g("ORION_TELEGRAM_AUTOPILOT_ENABLED", False)),
                telegram_default_profile_getter=lambda: str(self._g("ORION_TELEGRAM_AUTOPILOT_PROFILE") or ""),
                telegram_guided_automation_setup_enabled=self.telegram_guided_automation_setup_enabled,
                telegram_trust_mode_value_getter=lambda: str(self._g("ORION_TELEGRAM_AUTOPILOT_TRUST_MODE") or ""),
                telegram_execution_target_value_getter=lambda: str(self._g("ORION_TELEGRAM_AUTOPILOT_EXECUTION_TARGET") or ""),
                whatsapp_enabled_getter=lambda: bool(self._g("ORION_WHATSAPP_AUTOPILOT_ENABLED", False)),
                whatsapp_default_profile_getter=lambda: str(self._g("ORION_WHATSAPP_AUTOPILOT_PROFILE") or ""),
                whatsapp_require_prefix_getter=lambda: bool(self._g("ORION_WHATSAPP_AUTOPILOT_REQUIRE_PREFIX", False)),
                whatsapp_prefix_getter=lambda: str(self._g("ORION_WHATSAPP_AUTOPILOT_PREFIX") or ""),
                whatsapp_run_timeout_seconds_getter=lambda: int(self._g("ORION_WHATSAPP_AUTOPILOT_RUN_TIMEOUT_SECONDS") or 180),
                whatsapp_max_reply_chars_getter=lambda: int(self._g("ORION_WHATSAPP_AUTOPILOT_MAX_REPLY_CHARS") or 1200),
                whatsapp_send_ack_getter=lambda: bool(self._g("ORION_WHATSAPP_AUTOPILOT_SEND_ACK")),
                whatsapp_trust_mode_value_getter=lambda: str(self._g("ORION_WHATSAPP_AUTOPILOT_TRUST_MODE") or ""),
                whatsapp_execution_target_value_getter=lambda: str(self._g("ORION_WHATSAPP_AUTOPILOT_EXECUTION_TARGET") or ""),
                telegram_state_getter=lambda: self._g("TELEGRAM_AUTOPILOT_STATE") or {},
                telegram_lock_getter=self._telegram_lock,
                telegram_state_file=self._g("ORION_TELEGRAM_AUTOPILOT_STATE_FILE"),
                whatsapp_state_getter=lambda: self._g("WHATSAPP_AUTOPILOT_STATE") or {},
                whatsapp_lock_getter=self._whatsapp_lock,
                whatsapp_state_file=self._g("ORION_WHATSAPP_AUTOPILOT_STATE_FILE"),
                read_json=self.read_json,
                write_json=self.write_json,
                utc_now_iso=self.utc_now_iso,
                normalize_workspace_id=self.normalize_workspace_id,
                load_vault=self.load_vault,
                workspace_visible=self.workspace_visible,
                telegram_thread_alive=lambda: bool(
                    (getattr(self.server_getter(), "TELEGRAM_AUTOPILOT_THREAD", None) if self.server_getter() is not None else self._g("TELEGRAM_AUTOPILOT_THREAD"))
                    and (getattr(self.server_getter(), "TELEGRAM_AUTOPILOT_THREAD", None) if self.server_getter() is not None else self._g("TELEGRAM_AUTOPILOT_THREAD")).is_alive()
                ),
                telegram_allow_from_value=lambda: os.getenv("ORION_TELEGRAM_AUTOPILOT_ALLOW_FROM", ""),
                get_updates_process_lock=self.get_updates_process_lock,
                mark_telegram_started=lambda started_at: self.runtime_facade_service().mark_telegram_autopilot_started(started_at),
                resolve_vault_credential=self.resolve_vault_credential,
                safe_path_token=lambda value: self.compatibility_bridge_service().safe_path_token(value),
                runs_get=self.runs_get,
                telegram_space_question_via_mcp=self.telegram_space_question_via_mcp,
                helper_profile_state_file=self.telegram_profile_state_file,
                helper_onboarding_state_file=self.telegram_onboarding_state_file,
                helper_camera_setup_state_file=self.telegram_camera_setup_state_file,
                helper_media_dir=self.telegram_media_dir,
                helper_media_enabled=self.telegram_media_enabled,
                helper_media_max_items=self.telegram_media_max_items,
                helper_media_max_bytes=self.telegram_media_max_bytes,
                helper_media_include_in_goal=self.telegram_media_include_in_goal,
                helper_quick_goal_templates=self.quick_goal_templates,
                helper_menu_goal_templates=self.menu_goal_templates,
                support_workflow_api_url=self.workflow_api_url,
                support_runtime_url=self.runtime_url,
                support_web_url=self.web_url,
                telegram_profile_catalog_getter=lambda: self._g("TELEGRAM_AUTOPILOT_PROFILE_CATALOG") or {},
                whatsapp_profile_catalog_getter=lambda: self._g("WHATSAPP_AUTOPILOT_PROFILE_CATALOG") or {},
                support_installed_skills_enabled=self.installed_skills_enabled,
                support_error_category_hints=self.error_category_hints,
                support_engine_validation_errors_getter=lambda: self._g("ORION_ENGINE_VALIDATION_ERRORS") or [],
                env_get=lambda key, default="": os.getenv(key, default),
                init_runtime=self._init_runtime,
                runtime_telegram_engine_getter=lambda: (
                    str(self._g("ORION_TELEGRAM_AUTOPILOT_ENGINE") or "")
                    if str(self._g("ORION_TELEGRAM_AUTOPILOT_ENGINE") or "") in self._g("ENGINE_REGISTRY", {})
                    else "orion"
                ),
                runtime_whatsapp_engine_getter=lambda: (
                    str(self._g("ORION_WHATSAPP_AUTOPILOT_ENGINE") or "")
                    if str(self._g("ORION_WHATSAPP_AUTOPILOT_ENGINE") or "") in self._g("ENGINE_REGISTRY", {})
                    else "orion"
                ),
                runtime_telegram_show_buttons=self.telegram_show_buttons,
                runtime_local_lease_seconds=int(self._g("ORION_LOCAL_LEASE_SECONDS") or self.local_lease_seconds),
                runtime_non_retryable_run_error_hints=self.non_retryable_run_error_hints,
                runtime_builtin_skills_limit_builder=lambda scope_key, limit: self.skill_service().runtime_active_skills(scope_key, limit=limit),
                normalize_agent_role=lambda value: str((self._g("normalize_agent_role") or (lambda item: item))(value) or "").strip().lower(),
                allow_any_chat_getter=lambda: bool(self._g("ORION_TELEGRAM_AUTOPILOT_ALLOW_ANY_CHAT", False)),
                http_json_request=self.http_json_request,
                telegram_profile_fields=self.telegram_profile_fields,
                normalize_trust_mode=self.normalize_trust_mode,
                normalize_execution_target=self.normalize_execution_target,
                decide_execution_target=self.decide_execution_target,
                apply_execution_route_metadata=self.apply_execution_route_metadata,
                execute_agent_turn_request=lambda **kwargs: (
                    __import__("server_modules.turn_ingress_service", fromlist=["start_system_turn"]).start_system_turn(
                        run_execution_services=self._g("_run_execution_services")(),
                        **kwargs,
                    )
                    if callable(self._g("_run_execution_services", None))
                    and hasattr(self._g("_run_execution_services")(), "stamp_request_owner")
                    else None
                ),
                run_start_request_class=lambda **kwargs: __import__("server_modules.runtime_models", fromlist=["RunStartRequest"]).RunStartRequest(**kwargs),
                start_run_request=lambda request: __import__("server_modules.turn_ingress_service", fromlist=["start_system_run_start"]).start_system_run_start(
                    request,
                    stamp_request_owner_fn=self._g("_stamp_request_owner"),
                    services=self._g("_run_execution_services")(),
                ) if callable(self._g("_run_execution_services", None)) else None,
                create_run=self.create_run,
                inherit_owner_user_id=lambda owner_user_id=None: __import__("server_modules.runtime_config", fromlist=["x"]).agent_machine_inherited_owner_user_id(owner_user_id),
                agent_machine_full_trust_enabled=lambda owner_user_id: __import__("server_modules.runtime_config", fromlist=["x"]).agent_machine_full_trust_enabled(owner_user_id),
                run_history=self.run_history,
                run_history_lock=self.run_history_lock,
                runtime_metrics=self.runtime_metrics,
                metrics_lock=self.metrics_lock,
                utc_now=self.utc_now,
                parse_utc_ts=self.parse_utc_ts,
                worker_online_helper=lambda record, now=None: bool((self._g("_is_worker_online") or (lambda *_args, **_kwargs: False))(record, now)),
                local_queue_lock=self.local_queue_lock,
                local_pending_run_ids=self.local_pending_run_ids,
                local_claimed_runs=self.local_claimed_runs,
                local_worker_registry=self.local_worker_registry,
                truncate_one_line=lambda text, limit: self.channel_support_service().truncate_one_line(text, limit),
                bool_from_any=lambda value, default=False: self.connector_support_service().bool_from_any(value, default),
                local_companion_snapshot=lambda: self.runtime_support_service().local_companion_snapshot(),
                current_runtime_metrics=lambda: self.runtime_support_service().current_runtime_metrics(),
                latest_runtime_run_summary=lambda: self.runtime_support_service().latest_runtime_run_summary(),
                list_vault_connectors=lambda workspace_id: self._g("list_vault_connectors")(workspace_id),
                list_recent_connector_messages=lambda credentials, limit: self._g("list_recent_connector_messages")(credentials, limit=limit),
                query_active_installed_skills=lambda **kwargs: self._g("query_active_installed_skills")(**kwargs),
                runtime_builtin_skills_getter=lambda: self._g("RUNTIME_BUILTIN_SKILLS"),
                runtime_skills_snapshot_getter=lambda: self._g("_runtime_skills_snapshot"),
                bridge_facade_getter=self.bridge_facade_service,
            )
        return self._registry_facade

    def bridge_facade_service(self) -> Any:
        if self._bridge_facade is None:
            self._bridge_facade = self.bridge_facade_class(
                normalize_workspace_id=self.normalize_workspace_id,
                append_channel_event=lambda **kwargs: self._g("_append_channel_event")(**kwargs),
                utc_now_iso=self.utc_now_iso,
                truncate_one_line=lambda text, limit: self.channel_support_service().truncate_one_line(text, limit),
                json_safe=lambda value: (self._g("_json_safe") or (lambda item: item))(value),
                dead_letter_lock=self.dead_letter_lock,
                read_dead_letter_json=lambda path, default: self.read_json(path, default),
                write_dead_letter_json=lambda path, payload: self.write_json(path, payload),
                dead_letter_file=self.dead_letter_file,
                dead_letter_limit=self.dead_letter_limit,
                collapse_whitespace=lambda text: re.sub(r"\s+", " ", str(text or "").strip().lower()),
                telegram_workspace_id=self._telegram_workspace_id(),
                whatsapp_workspace_id=self._whatsapp_workspace_id(),
                telegram_service_registry=self.telegram_service_registry,
                whatsapp_service_registry=self.whatsapp_service_registry,
                autopilot_profile_service=self.profile_service,
                init_runtime=self._init_runtime,
                telegram_terminal_service=self.terminal_service,
                telegram_enabled_getter=lambda: bool(self._g("ORION_TELEGRAM_AUTOPILOT_ENABLED", False)),
                telegram_default_profile_getter=lambda: str(self._g("ORION_TELEGRAM_AUTOPILOT_PROFILE") or ""),
                telegram_catalog_getter=lambda: self._g("TELEGRAM_AUTOPILOT_PROFILE_CATALOG") or {},
                whatsapp_enabled_getter=lambda: bool(self._g("ORION_WHATSAPP_AUTOPILOT_ENABLED", False)),
                whatsapp_default_profile_getter=lambda: str(self._g("ORION_WHATSAPP_AUTOPILOT_PROFILE") or ""),
                whatsapp_catalog_getter=lambda: self._g("WHATSAPP_AUTOPILOT_PROFILE_CATALOG") or {},
                telegram_state_getter=lambda: self._g("TELEGRAM_AUTOPILOT_STATE") or {},
                telegram_lock_getter=self._telegram_lock,
                safe_path_token=self.safe_path_token_impl,
                build_goal_with_profile=lambda goal, profile_data: self.telegram_helper_registry().profile_service().build_goal_with_profile(goal, profile_data),
                workspace_connector_context=lambda goal, workspace_id, current_connector_id: self.connector_context_service().workspace_connector_context(
                    goal,
                    workspace_id,
                    current_connector_id,
                ),
                extract_message=lambda update: self.telegram_helper_registry().media_service().extract_message(update),
                build_goal_with_attachments=lambda goal, attachments: self.telegram_helper_registry().media_service().build_goal_with_attachments(goal, attachments),
                route_message=lambda raw_text, profile: self.telegram_helper_registry().routing_service().route_message(raw_text, profile),
                parse_form_urlencoded=lambda raw: self.whatsapp_service_registry().whatsapp_ingress_service().parse_form_urlencoded(raw),
                error_response=lambda status_code, content: self._g("Response")(status_code=status_code, content=content),
                telegram_delivery_mode_getter=lambda: str(self._g("ORION_TELEGRAM_AUTOPILOT_DELIVERY_MODE") or "polling"),
                telegram_configured_webhook_secret_getter=lambda: str(self._g("ORION_TELEGRAM_AUTOPILOT_WEBHOOK_SECRET") or ""),
                telegram_public_base_url_getter=lambda: str(os.getenv("ORION_TELEGRAM_AUTOPILOT_PUBLIC_BASE_URL") or self.runtime_url or ""),
                webhook_enabled_getter=lambda: bool(self._g("ORION_WHATSAPP_AUTOPILOT_ENABLED", False)),
                configured_webhook_secret_getter=lambda: str(self._g("ORION_WHATSAPP_AUTOPILOT_WEBHOOK_SECRET") or ""),
                whatsapp_public_base_url_getter=lambda: str(os.getenv("ORION_WHATSAPP_AUTOPILOT_PUBLIC_BASE_URL") or self.runtime_url or ""),
            )
        return self._bridge_facade

    def runtime_facade_service(self) -> Any:
        if self._runtime_facade is None:
            self._runtime_facade = self.runtime_facade_class(
                global_namespace=self.global_namespace,
                sync_server_globals=self.sync_server_globals,
                server_getter=self.server_getter,
                server_setter=self.server_setter,
                import_server=self.import_server,
                state_bridge_service=self.state_bridge_service,
                event_bridge_service=self.event_bridge_service,
                terminal_bridge_service=self.terminal_bridge_service,
                webhook_bridge_service=self.webhook_bridge_service,
                telegram_webhook_bridge_service=self.telegram_webhook_bridge_service,
            )
        return self._runtime_facade

    def telegram_service_registry(self) -> Any:
        return self.registry_facade_service().telegram_service_registry()

    def whatsapp_service_registry(self) -> Any:
        return self.registry_facade_service().whatsapp_service_registry()

    def telegram_helper_registry(self) -> Any:
        return self.registry_facade_service().telegram_helper_registry()

    def support_service_registry(self) -> Any:
        return self.registry_facade_service().support_service_registry()

    def runtime_service_registry(self) -> Any:
        return self.registry_facade_service().runtime_service_registry()

    def profile_service(self) -> Any:
        return self.registry_facade_service().profile_service()

    def connector_support_service(self) -> Any:
        return self.registry_facade_service().connector_support_service()

    def runtime_status_service(self) -> Any:
        return self.registry_facade_service().runtime_status_service()

    def workflow_setup_service(self) -> Any:
        return self.registry_facade_service().workflow_setup_service()

    def connector_context_service(self) -> Any:
        return self.registry_facade_service().connector_context_service()

    def approval_service(self) -> Any:
        return self.registry_facade_service().approval_service()

    def transport_service(self) -> Any:
        return self.registry_facade_service().transport_service()

    def terminal_service(self) -> Any:
        return self.registry_facade_service().terminal_service()

    def common_support_service(self) -> Any:
        return self.registry_facade_service().common_support_service()

    def run_entry_service(self) -> Any:
        return self.registry_facade_service().run_entry_service()

    def runtime_support_service(self) -> Any:
        return self.registry_facade_service().runtime_support_service()

    def skill_service(self) -> Any:
        return self.registry_facade_service().skill_service()

    def channel_support_service(self) -> Any:
        return self.registry_facade_service().channel_support_service()

    def menu_service(self) -> Any:
        return self.registry_facade_service().menu_service()

    def shared_service_registry(self) -> Any:
        return self.bridge_facade_service().shared_service_registry()

    def autopilot_status_service(self) -> Any:
        return self.bridge_facade_service().autopilot_status_service()

    def autopilot_endpoint_service(self) -> Any:
        return self.bridge_facade_service().autopilot_endpoint_service()

    def autopilot_event_service(self) -> Any:
        return self.bridge_facade_service().autopilot_event_service()

    def event_bridge_service(self) -> Any:
        return self.bridge_facade_service().event_bridge_service()

    def terminal_bridge_service(self) -> Any:
        return self.bridge_facade_service().terminal_bridge_service()

    def state_bridge_service(self) -> Any:
        return self.bridge_facade_service().state_bridge_service()

    def compatibility_bridge_service(self) -> Any:
        return self.bridge_facade_service().compatibility_bridge_service()

    def webhook_bridge_service(self) -> Any:
        return self.bridge_facade_service().webhook_bridge_service()

    def telegram_webhook_bridge_service(self) -> Any:
        return self.bridge_facade_service().telegram_webhook_bridge_service()
