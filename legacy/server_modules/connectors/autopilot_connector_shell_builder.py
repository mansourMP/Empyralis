from __future__ import annotations

import threading
from typing import Any, Dict, Sequence

from server_modules.connectors.autopilot_connector_config import (
    _AUTOPILOT_ERROR_CATEGORY_HINTS,
    _AUTOPILOT_NON_RETRYABLE_RUN_ERROR_HINTS,
    _CHANNEL_DEAD_LETTER_LOCK,
    _TELEGRAM_MENU_GOAL_TEMPLATES,
    _TELEGRAM_QUICK_GOAL_TEMPLATES,
    _telegram_get_updates_process_lock,
    DEFAULT_CHAT_PREFIX,
    EMPYRALIST_RUNTIME_URL,
    EMPYRALIST_WEB_URL,
    EMPYRALIST_WORKFLOW_API_URL,
    ORION_CHANNEL_DEAD_LETTER_FILE,
    ORION_CHANNEL_DEAD_LETTER_LIMIT,
    ORION_LOCAL_LEASE_SECONDS,
    ORION_TELEGRAM_AUTOPILOT_MAX_UPDATES,
    ORION_TELEGRAM_AUTOPILOT_POLL_SECONDS,
    ORION_TELEGRAM_AUTOPILOT_SHOW_BUTTONS,
    ORION_TELEGRAM_GUIDED_AUTOMATION_SETUP_ENABLED,
    ORION_TELEGRAM_INSTALLED_SKILLS_ENABLED,
    ORION_TELEGRAM_MEDIA_DIR,
    ORION_TELEGRAM_MEDIA_ENABLED,
    ORION_TELEGRAM_MEDIA_INCLUDE_IN_GOAL,
    ORION_TELEGRAM_MEDIA_MAX_BYTES,
    ORION_TELEGRAM_MEDIA_MAX_ITEMS,
    ORION_TELEGRAM_ONBOARDING_ENABLED,
    ORION_TELEGRAM_ONBOARDING_STATE_FILE,
    ORION_TELEGRAM_PROFILE_STATE_FILE,
    ORION_TELEGRAM_SPACE_STATUS_ENABLED,
    ORION_TELEGRAM_CAMERA_SETUP_STATE_FILE,
    PROJECT_ROOT,
)
from server_modules.connectors.autopilot_connector_shell_service import AutopilotConnectorShellService
from server_modules.connectors.telegram.media import telegram_safe_path_token
from server_modules.agent.user_profile_service import TELEGRAM_PROFILE_FIELDS as _TELEGRAM_PROFILE_FIELDS
from server_modules.agent.space_monitoring_service import telegram_space_question_via_mcp


def _module_global(global_namespace: Dict[str, Any], name: str, default: Any = None) -> Any:
    return global_namespace[name] if name in global_namespace else default


def _import_attr(module_name: str, attr_name: str) -> Any:
    return getattr(__import__(module_name, fromlist=[attr_name]), attr_name)


def _normalize_workspace_id_fallback(global_namespace: Dict[str, Any], value: Any) -> str:
    normalize_workspace_id = global_namespace.get("_normalize_workspace_id")
    if callable(normalize_workspace_id):
        try:
            normalized = normalize_workspace_id(value)
        except Exception:
            normalized = None
    else:
        normalized = None
    token = str(normalized if normalized is not None else value or "default").strip()
    return token or "default"


def build_autopilot_connector_shell_service(
    *,
    global_namespace: Dict[str, Any],
    sync_server_globals: Sequence[str],
    server_getter,
    server_setter,
    import_server,
    shell_service_class=AutopilotConnectorShellService,
):
    return shell_service_class(
        global_namespace=global_namespace,
        sync_server_globals=sync_server_globals,
        project_root=PROJECT_ROOT,
        default_chat_prefix=DEFAULT_CHAT_PREFIX,
        telegram_onboarding_enabled=ORION_TELEGRAM_ONBOARDING_ENABLED,
        telegram_space_status_enabled=ORION_TELEGRAM_SPACE_STATUS_ENABLED,
        telegram_media_max_items=ORION_TELEGRAM_MEDIA_MAX_ITEMS,
        telegram_max_updates=ORION_TELEGRAM_AUTOPILOT_MAX_UPDATES,
        telegram_poll_seconds=ORION_TELEGRAM_AUTOPILOT_POLL_SECONDS,
        telegram_guided_automation_setup_enabled=ORION_TELEGRAM_GUIDED_AUTOMATION_SETUP_ENABLED,
        telegram_media_enabled=ORION_TELEGRAM_MEDIA_ENABLED,
        telegram_media_dir=ORION_TELEGRAM_MEDIA_DIR,
        telegram_media_max_bytes=ORION_TELEGRAM_MEDIA_MAX_BYTES,
        telegram_media_include_in_goal=ORION_TELEGRAM_MEDIA_INCLUDE_IN_GOAL,
        telegram_camera_setup_state_file=ORION_TELEGRAM_CAMERA_SETUP_STATE_FILE,
        telegram_profile_state_file=ORION_TELEGRAM_PROFILE_STATE_FILE,
        telegram_onboarding_state_file=ORION_TELEGRAM_ONBOARDING_STATE_FILE,
        quick_goal_templates=_TELEGRAM_QUICK_GOAL_TEMPLATES,
        menu_goal_templates=_TELEGRAM_MENU_GOAL_TEMPLATES,
        workflow_api_url=EMPYRALIST_WORKFLOW_API_URL,
        runtime_url=EMPYRALIST_RUNTIME_URL,
        web_url=EMPYRALIST_WEB_URL,
        installed_skills_enabled=ORION_TELEGRAM_INSTALLED_SKILLS_ENABLED,
        error_category_hints=_AUTOPILOT_ERROR_CATEGORY_HINTS,
        non_retryable_run_error_hints=_AUTOPILOT_NON_RETRYABLE_RUN_ERROR_HINTS,
        dead_letter_lock=_CHANNEL_DEAD_LETTER_LOCK,
        dead_letter_file=ORION_CHANNEL_DEAD_LETTER_FILE,
        dead_letter_limit=ORION_CHANNEL_DEAD_LETTER_LIMIT,
        local_lease_seconds=ORION_LOCAL_LEASE_SECONDS,
        telegram_show_buttons=ORION_TELEGRAM_AUTOPILOT_SHOW_BUTTONS,
        telegram_profile_fields=_TELEGRAM_PROFILE_FIELDS,
        run_history=_module_global(global_namespace, "RUN_HISTORY", {}),
        run_history_lock=_module_global(global_namespace, "RUN_HISTORY_LOCK", threading.Lock()),
        runtime_metrics=_module_global(global_namespace, "RUNTIME_METRICS", {}),
        metrics_lock=_module_global(global_namespace, "METRICS_LOCK", threading.Lock()),
        local_queue_lock=_module_global(global_namespace, "LOCAL_QUEUE_LOCK", threading.Lock()),
        local_pending_run_ids=_module_global(global_namespace, "LOCAL_PENDING_RUN_IDS", set()),
        local_claimed_runs=_module_global(global_namespace, "LOCAL_CLAIMED_RUNS", {}),
        local_worker_registry=_module_global(global_namespace, "LOCAL_WORKER_REGISTRY", {}),
        server_getter=server_getter,
        server_setter=server_setter,
        import_server=import_server,
        read_json=lambda path, default: (
            _module_global(global_namespace, "_safe_read_json")
            or _import_attr("server_modules.runtime_common", "_safe_read_json")
        )(path, default),
        write_json=lambda path, payload: (
            _module_global(global_namespace, "_safe_write_json")
            or _import_attr("server_modules.runtime_common", "_safe_write_json")
        )(path, payload),
        utc_now_iso=lambda: (
            _module_global(global_namespace, "_utc_now_iso")
            or _import_attr("server_modules.runtime_common", "_utc_now_iso")
        )(),
        normalize_workspace_id=lambda value: _normalize_workspace_id_fallback(global_namespace, value),
        load_vault=lambda: (
            _module_global(global_namespace, "load_vault")
            or _import_attr("server_modules.vault_store", "load_vault")
        )(),
        workspace_visible=lambda workspace_id, requested_workspace_id: (
            _module_global(global_namespace, "_workspace_visible")
            or _import_attr("server_modules.runtime_common", "_workspace_visible")
        )(workspace_id, requested_workspace_id),
        get_updates_process_lock=_telegram_get_updates_process_lock,
        resolve_vault_credential=lambda credential_id, workspace_id=None: (
            _module_global(global_namespace, "resolve_vault_credential")
            or _import_attr("server_modules.runtime_common", "resolve_vault_credential")
        )(credential_id, workspace_id),
        http_json_request=lambda *args, **kwargs: (
            _module_global(global_namespace, "http_json_request")
            or _import_attr("server_modules.runtime_common", "http_json_request")
        )(*args, **kwargs),
        safe_path_token_impl=telegram_safe_path_token,
        telegram_space_question_via_mcp=telegram_space_question_via_mcp,
        utc_now=lambda: (
            _module_global(global_namespace, "_utc_now")
            or _import_attr("server_modules.runtime_common", "_utc_now")
        )(),
        parse_utc_ts=lambda value: (
            _module_global(global_namespace, "_parse_utc_ts")
            or _import_attr("server_modules.runtime_common", "_parse_utc_ts")
        )(value),
        create_run=lambda **kwargs: (
            _module_global(global_namespace, "create_run")
            or _import_attr("server_modules.runs_execution", "create_run")
        )(**kwargs),
        decide_execution_target=lambda metadata: (
            _module_global(global_namespace, "decide_execution_target")
            or _import_attr("server_modules.runtime_policy", "decide_execution_target")
        )(metadata),
        apply_execution_route_metadata=lambda metadata, route: (
            _module_global(global_namespace, "apply_execution_route_metadata")
            or _import_attr("server_modules.runtime_policy", "apply_execution_route_metadata")
        )(metadata, route),
        normalize_trust_mode=lambda value: (
            _module_global(global_namespace, "normalize_trust_mode")
            or _import_attr("server_modules.runtime_policy", "normalize_trust_mode")
        )(value),
        normalize_execution_target=lambda value: (
            _module_global(global_namespace, "normalize_execution_target")
            or _import_attr("server_modules.runtime_policy", "normalize_execution_target")
        )(value),
    )
