import concurrent.futures
import logging
import base64
import asyncio
import ast
import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote_plus, urlencode

from server_modules.builder_runtime_mapping import map_builder_permissions_to_runtime_metadata
from server_modules.connectors.slack_connector import (
    get_channel_history as slack_get_channel_history,
    list_channels as slack_list_channels,
    post_reply as slack_post_reply,
    send_dm as slack_send_dm_message,
    send_message as slack_send_channel_message,
    upload_file as slack_upload_file,
)
from server_modules.connectors.discord_connector import (
    add_reaction as discord_add_reaction,
    create_thread as discord_create_thread,
    delete_message as discord_delete_message,
    edit_message as discord_edit_message,
    get_message_history as discord_get_message_history,
    list_channels as discord_list_channels,
    list_guilds as discord_list_guilds,
    list_members as discord_list_members,
    send_dm as discord_send_dm_message,
    send_embed as discord_send_embed,
    send_message as discord_send_channel_message,
)
from server_modules.connectors.github_connector import (
    comment_on_issue as github_comment_on_issue,
    create_issue as github_create_issue,
    create_or_update_file as github_create_or_update_file,
    create_pull_request as github_create_pull_request,
    get_file_content as github_get_file_content,
    get_repo as github_get_repo,
    list_commits as github_list_commits,
    list_issues as github_list_issues,
    list_pull_requests as github_list_pull_requests,
    list_repos as github_list_repos,
)
from server_modules import usage_accounting_service
from server_modules import security_audit_service
from server_modules.connectors.dropbox_connector import (
    delete as dropbox_delete,
    download_file as dropbox_download_file,
    get_shared_link as dropbox_get_shared_link,
    list_folder as dropbox_list_folder,
    move as dropbox_move,
    search as dropbox_search,
    upload_file as dropbox_upload_file,
)
from server_modules.connectors.linear_connector import (
    add_comment as linear_add_comment,
    create_issue as linear_create_issue,
    get_issue as linear_get_issue,
    list_issues as linear_list_issues,
    list_projects as linear_list_projects,
    list_teams as linear_list_teams,
    update_issue as linear_update_issue,
)
from server_modules.connectors.notion_connector import (
    append_blocks as notion_append_blocks,
    create_database_item as notion_create_database_item,
    create_page as notion_create_page,
    get_page as notion_get_page,
    query_database as notion_query_database,
    search as notion_search,
    update_page as notion_update_page,
)
from server_modules.connectors.smtp_connector import (
    fetch_emails as smtp_fetch_emails,
    send_email as smtp_send_email,
)
from server_modules.connectors.s3_connector import (
    create_bucket as s3_create_bucket,
    delete_object as s3_delete_object,
    download_file as s3_download_file,
    get_presigned_url as s3_get_presigned_url,
    list_buckets as s3_list_buckets,
    list_objects as s3_list_objects,
    upload_file as s3_upload_file,
)
from server_modules import agent_action_metering_service
from server_modules import authority_mandate_service
from server_modules import runtime_config as config
from server_modules import run_service as run_service
from server_modules import rust_runtime_kernel_client
from server_modules.capability_registry import workflow_node_capability_id
from server_modules import shared as shared
from server_modules import runtime_common as common
from server_modules import outbox_service
from server_modules import policy_service
from server_modules import safe_mode_service
from server_modules.runs_engine import (
    ENGINE_REGISTRY,
    RUN_TOOL_LOOP_REPLY,
    clear_run_tool_signature_state,
    format_agent_summary,
    generate_with_candidate_failover,
    record_run_tool_signature,
    requires_human_approval,
    wait_for_human_response,
    resolve_run_execution_context,
    wait_for_human_decision,
)
from server_modules.connector_metadata import _connector_identity_signature
from server_modules.runs_output import _compact_event_text, _json_safe, _persist_live_run_state
from server_modules.health_diagnostics import _build_skill_contract_from_metadata, _inject_runtime_skill_defaults
from server_modules.run_service import RunExecutionServices
from server_modules.runs_core import set_run_status, emit_log
from server_modules.external_write_safety import execute_external_write_once, stable_value_fingerprint
from server_modules.file_mount_security import assert_file_mount_access
from server_modules.runtime_policy import browser_automation_plan_hash
from server_modules.turn_runtime import (
    build_execute_unowned_system_run_start_request_via_turn_runtime,
    execute_built_legacy_unowned_system_run_start_request_via_turn_runtime,
)
from server_modules.url_security import assert_safe_outbound_url
from scripts.orion_local_worker_utils import build_operator_system_prompt

globals().update({key: value for key, value in vars(config).items() if not key.startswith("__")})
globals().update({key: value for key, value in vars(shared).items() if not key.startswith("__")})
globals().update({key: value for key, value in vars(common).items() if not key.startswith("__")})

LOGGER = logging.getLogger(__name__)
NODE_TERMINAL_STATUSES = {"succeeded", "failed", "skipped"}


execute_system_run_start_request_via_turn_runtime = build_execute_unowned_system_run_start_request_via_turn_runtime()


def _workflow_child_run_execution_services() -> RunExecutionServices:
    from server_modules import runs_delegation as _runs_delegation

    return run_service.build_namespace_delegated_system_run_execution_services(
        namespace=vars(_runs_delegation),
    )


def _execute_workflow_child_run_request(request: Any) -> Dict[str, Any]:
    from server_modules import runs_delegation as _runs_delegation

    run_service.assert_workflow_turn_depth_allowed(
        getattr(request, "metadata", None)
        if not isinstance(request, dict)
        else request.get("metadata")
    )
    return execute_built_legacy_unowned_system_run_start_request_via_turn_runtime(
        request,
        execute_system_run_start_request_via_turn_runtime_fn=execute_system_run_start_request_via_turn_runtime,
        build_run_execution_services_fn=_workflow_child_run_execution_services,
        create_run_from_request_fn=_runs_delegation._create_run_from_request,
    )


def _log_execution_boundary(log_queue: queue.Queue, run_id: str, phase: str, *, status: Optional[str] = None, timeout_seconds: Optional[int] = None) -> None:
    timestamp = _utc_now_iso()
    message = f"Execution {phase}: run_id={run_id} timestamp={timestamp}"
    payload = {"run_id": run_id, "timestamp": timestamp}
    if status:
        payload["status"] = status
        message = f"{message} status={status}"
    if timeout_seconds is not None:
        payload["timeout_seconds"] = timeout_seconds
        message = f"{message} timeout_seconds={timeout_seconds}"
    emit_log(log_queue, "info", message, event=f"execution_{phase}", data=payload)
    LOGGER.info(message)


def _execute_engine_with_timeout(engine: Any, run_id: str, timeout_seconds: int) -> Dict[str, Any]:
    result: Dict[str, Any] = {"error": None, "timed_out": False}

    def _target() -> None:
        try:
            engine.execute(run_id)
        except Exception as exc:
            result["error"] = exc

    worker = threading.Thread(target=_target, name=f"run-execution-{run_id}", daemon=True)
    worker.start()
    worker.join(timeout_seconds)
    if worker.is_alive():
        result["timed_out"] = True
    return result

def _first_non_empty_string(*values: Any, default: str = "") -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return default


def _runtime_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled", "allow", "allowed", "valid", "attached"}:
        return True
    if text in {"0", "false", "no", "off", "disabled", "deny", "blocked", "invalid", "detached"}:
        return False
    return default


def _active_runtime_run_count(current_run_id: str) -> int:
    active_statuses = {"queued", "running", "in_progress", "processing", "waiting_human"}
    count = 0
    for run_id, run in runs.items():
        if str(run_id) == str(current_run_id):
            continue
        if not isinstance(run, dict):
            continue
        status = str(run.get("status") or "").strip().lower()
        if status in active_statuses:
            count += 1
    return count


def _enforce_execution_runtime_decision(
    run_id: str,
    *,
    operation: str,
    run: Optional[Dict[str, Any]] = None,
    context: Optional[Dict[str, Any]] = None,
    engine_name: Optional[str] = None,
    status: Optional[str] = None,
    timeout_seconds: Optional[int] = None,
    workflow_node_id: Optional[str] = None,
    connector_id: Optional[str] = None,
    connector_action: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    policy_decision: Optional[str] = None,
    risk_level: Optional[str] = None,
    approval_required: Optional[bool] = None,
    approval_provided: Optional[bool] = None,
    external_write_requested: Optional[bool] = None,
    usage_unit_count: Optional[int] = None,
    metering_enabled: Optional[bool] = None,
) -> Dict[str, Any]:
    run_payload = run if isinstance(run, dict) else {}
    context_payload = context if isinstance(context, dict) else (
        run_payload.get("context") if isinstance(run_payload.get("context"), dict) else {}
    )
    metadata = context_payload.get("metadata") if isinstance(context_payload.get("metadata"), dict) else {}
    selected_target = selected_execution_target_from_context(context_payload)
    requested_target = _first_non_empty_string(
        metadata.get("execution_target_selected"),
        metadata.get("execution_target_requested"),
        metadata.get("execution_target"),
        context_payload.get("execution_target_selected"),
        context_payload.get("execution_target_requested"),
        context_payload.get("execution_target"),
        run_payload.get("execution_target_selected"),
        run_payload.get("execution_target_requested"),
        run_payload.get("execution_target"),
    ).strip().lower()
    target_explicitly_requested = bool(requested_target and requested_target not in {"auto", "default"})
    if operation in {"connector_action", "usage_metering"} and not target_explicitly_requested:
        selected_target = EXECUTION_TARGET_CLOUD
    lease_id = _first_non_empty_string(
        metadata.get("lease_id"),
        metadata.get("machine_lease_id"),
        run_payload.get("lease_id"),
    )
    lease_holder_id = _first_non_empty_string(
        metadata.get("lease_holder_id"),
        metadata.get("worker_id"),
        metadata.get("machine_id"),
        run_payload.get("lease_holder_id"),
        run_payload.get("worker_id"),
        run_payload.get("machine_id"),
    )
    local_target = selected_target in {EXECUTION_TARGET_LOCAL_COMPANION, "local", "this_device"}
    lease_required = _runtime_bool(
        metadata.get("lease_required", run_payload.get("lease_required")),
        default=local_target and bool(lease_id or lease_holder_id),
    )
    lease_valid = _runtime_bool(
        metadata.get("lease_valid", run_payload.get("lease_valid")),
        default=bool(lease_id and lease_holder_id),
    )
    runtime_attached = _runtime_bool(
        metadata.get("runtime_attached", metadata.get("local_runtime_attached", run_payload.get("runtime_attached"))),
        default=(not local_target) or lease_valid,
    )
    max_runtime_runs = int(
        metadata.get("max_runtime_runs")
        or globals().get("ORION_MAX_RUNTIME_RUNS", 0)
        or globals().get("ORION_MAX_CONCURRENT_RUNS", 0)
        or 32
    )
    normalized_timeout = int(
        timeout_seconds
        or metadata.get("timeout_seconds")
        or globals().get("ORION_RUN_TIMEOUT_SECONDS", 300)
        or 300
    )
    payload = {
        "operation": operation,
        "run_id": str(run_id or "").strip(),
        "workspace_id": _first_non_empty_string(
            run_payload.get("workspace_id"),
            context_payload.get("workspace_id"),
            metadata.get("workspace_id"),
            metadata.get("tenant_workspace_id"),
            default="default",
        ),
        "actor_id": _first_non_empty_string(
            metadata.get("actor_id"),
            metadata.get("owner_user_id"),
            metadata.get("user_id"),
            metadata.get("created_by"),
            run_payload.get("actor_id"),
            run_payload.get("owner_user_id"),
            run_payload.get("user_id"),
            run_payload.get("created_by"),
            default="system",
        ),
        "parent_run_id": _first_non_empty_string(metadata.get("parent_run_id"), run_payload.get("parent_run_id")),
        "engine_id": _first_non_empty_string(engine_name, run_payload.get("engine"), metadata.get("engine"), default="orion"),
        "engine_available": bool(ENGINE_REGISTRY.get(_first_non_empty_string(engine_name, run_payload.get("engine"), default="orion").lower())),
        "workflow_node_id": str(workflow_node_id or "").strip(),
        "execution_target": selected_target,
        "runtime_mode": _first_non_empty_string(metadata.get("runtime_mode"), metadata.get("mode"), default="cloud"),
        "status": _first_non_empty_string(status, run_payload.get("status"), default="queued"),
        "timeout_seconds": normalized_timeout,
        "max_timeout_seconds": int(metadata.get("max_timeout_seconds") or globals().get("ORION_MAX_RUN_TIMEOUT_SECONDS", 7200) or 7200),
        "active_runtime_runs": _active_runtime_run_count(str(run_id or "")),
        "max_runtime_runs": max(1, max_runtime_runs),
        "runtime_attached": runtime_attached,
        "lease_required": lease_required,
        "lease_id": lease_id,
        "lease_holder_id": lease_holder_id,
        "lease_valid": lease_valid,
        "kill_switch_enabled": _runtime_bool(metadata.get("kill_switch_enabled"), default=False),
        "safe_mode_enabled": _runtime_bool(metadata.get("safe_mode_enabled"), default=False),
        "entitlement_ok": _runtime_bool(metadata.get("entitlement_ok"), default=True),
        "budget_available": _runtime_bool(metadata.get("budget_available"), default=True),
        "connector_id": str(connector_id or "").strip(),
        "connector_action": str(connector_action or "").strip(),
        "idempotency_key": str(idempotency_key or "").strip(),
        "policy_decision": str(policy_decision or "allow").strip() or "allow",
        "risk_level": str(risk_level or "low").strip() or "low",
        "approval_required": bool(approval_required),
        "approval_provided": bool(approval_provided),
        "external_write_requested": bool(external_write_requested),
        "usage_unit_count": int(usage_unit_count or 0),
        "metering_enabled": True if metering_enabled is None else bool(metering_enabled),
    }
    decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
        "execution-runtime-decision",
        payload,
    )
    expected_next_actions = {
        "dispatch_run": "dispatch_runtime_run",
        "execute_run": "execute_runtime_run",
        "connector_action": "execute_connector_action",
        "usage_metering": "record_usage_event",
        "finalize_run": "finalize_runtime_run",
    }
    expected_next_action = expected_next_actions.get(operation)
    if expected_next_action:
        next_action = str(decision.get("next_action") or "").strip()
        if next_action != expected_next_action:
            raise RuntimeError(f"unexpected_next_action:{next_action or 'missing'}")
    return decision
def _set_run_status_after_execution_runtime_decision(
    run_id: str,
    status: str,
    *,
    run: Optional[Dict[str, Any]] = None,
    context: Optional[Dict[str, Any]] = None,
    engine_name: Optional[str] = None,
    timeout_seconds: Optional[int] = None,
) -> None:
    decision = _enforce_execution_runtime_decision(
        run_id,
        operation="finalize_run",
        run=run,
        context=context,
        engine_name=engine_name,
        status=status,
        timeout_seconds=timeout_seconds,
    )
    normalized_status = str(decision.get("normalized_status") or status).strip().lower() or status
    set_run_status(run_id, normalized_status)


def _normalize_execution_outcome_with_rust(
    *,
    exit_code: Optional[int] = None,
    signal: Optional[str] = None,
    timed_out: bool = False,
    cancelled: bool = False,
    stdout: Optional[str] = None,
    stderr: Optional[str] = None,
    artifacts: Optional[List[Dict[str, Any]]] = None,
    max_preview_bytes: int = 2000,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "timed_out": bool(timed_out),
        "cancelled": bool(cancelled),
        "stdout": str(stdout or ""),
        "stderr": str(stderr or ""),
        "artifacts": artifacts if isinstance(artifacts, list) else [],
        "max_preview_bytes": max(1, int(max_preview_bytes or 2000)),
    }
    if exit_code is not None:
        payload["exit_code"] = int(exit_code)
    if signal:
        payload["signal"] = str(signal)
    return rust_runtime_kernel_client.run_runtime_kernel_enforced(
        "execution-outcome",
        payload,
    )


def _runtime_final_status_from_outcome(outcome: Optional[Dict[str, Any]], fallback: str) -> str:
    token = str((outcome or {}).get("final_status") or fallback).strip().lower()
    if token in {"completed", "failed", "timeout", "cancelled"}:
        return token
    return str(fallback or "failed").strip().lower() or "failed"


def _runtime_outcome_summary(outcome: Optional[Dict[str, Any]], fallback: str) -> str:
    summary = str((outcome or {}).get("summary") or "").strip()
    if summary:
        return summary
    return str(fallback or "Execution failed.").strip() or "Execution failed."


def _runtime_outcome_event(outcome: Optional[Dict[str, Any]], fallback: str) -> str:
    token = str((outcome or {}).get("event") or fallback).strip()
    return token or str(fallback or "run_error").strip() or "run_error"


def _runtime_outcome_log_level(outcome: Optional[Dict[str, Any]], fallback: str) -> str:
    token = str((outcome or {}).get("log_level") or fallback).strip().lower()
    if token in {"info", "warn", "error"}:
        return token
    return str(fallback or "error").strip().lower() or "error"


def _apply_execution_outcome_record_patch(
    run: Dict[str, Any],
    outcome: Optional[Dict[str, Any]],
    *,
    preserve_existing_result: bool = False,
) -> None:
    if not isinstance(run, dict) or not isinstance(outcome, dict):
        return
    patch = outcome.get("record_patch") if isinstance(outcome.get("record_patch"), dict) else {}
    result_text = str(patch.get("result") or "").strip()
    if result_text and (not preserve_existing_result or not str(run.get("result") or "").strip()):
        run["result"] = result_text
    execution_outcome_patch = patch.get("execution_outcome") if isinstance(patch.get("execution_outcome"), dict) else {}
    if execution_outcome_patch:
        existing_result_data = run.get("result_data") if isinstance(run.get("result_data"), dict) else {}
        next_result_data = dict(existing_result_data)
        next_result_data["execution_outcome"] = execution_outcome_patch
        run["result_data"] = next_result_data


def selected_execution_target_from_context(context: Optional[Dict[str, Any]]) -> str:
    if not isinstance(context, dict):
        return EXECUTION_TARGET_LOCAL_COMPANION if ORION_LOCAL_COMPANION_ENABLED else EXECUTION_TARGET_CLOUD
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    selected = str(metadata.get("execution_target_selected") or "").strip().lower()
    if selected in VALID_EXECUTION_TARGETS:
        return selected
    requested = str(metadata.get("execution_target_requested") or "").strip().lower()
    if requested in VALID_EXECUTION_TARGETS:
        return requested
    return EXECUTION_TARGET_LOCAL_COMPANION if ORION_LOCAL_COMPANION_ENABLED else EXECUTION_TARGET_CLOUD


def _agent_machine_owner_user_id_from_metadata(metadata: Optional[Dict[str, Any]]) -> str:
    payload = metadata if isinstance(metadata, dict) else {}
    return str(payload.get("owner_user_id") or "").strip()


def _session_mode_from_metadata(metadata: Optional[Dict[str, Any]]) -> str:
    payload = metadata if isinstance(metadata, dict) else {}
    policy_context = payload.get("policy_context") if isinstance(payload.get("policy_context"), dict) else {}
    return str(
        payload.get("effective_session_mode")
        or payload.get("session_mode")
        or policy_context.get("effective_session_mode")
        or policy_context.get("session_mode")
        or ""
    ).strip().lower()


def _agent_machine_full_trust_for_context(context: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(context, dict):
        return False
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    effective_session_mode = _session_mode_from_metadata(metadata)
    if effective_session_mode:
        if effective_session_mode != "agent":
            return False
    return agent_machine_full_trust_enabled(_agent_machine_owner_user_id_from_metadata(metadata))


def _apply_agent_machine_bypass_to_tool_policy_evaluation(
    evaluation: Dict[str, Any],
    *,
    context: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    return evaluation


def _apply_agent_machine_bypass_to_action_policy(
    action_policy: Dict[str, Any],
    *,
    context: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    return action_policy


def _workflow_definition_from_context(context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    raw = context.get("workflow_definition")
    if isinstance(raw, dict) and isinstance(raw.get("nodes"), list):
        return raw
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    raw = metadata.get("workflow_definition")
    if isinstance(raw, dict) and isinstance(raw.get("nodes"), list):
        return raw
    return None


def _workflow_label(node: Dict[str, Any]) -> str:
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    config = node.get("config") if isinstance(node.get("config"), dict) else {}
    identity = config.get("identity") if isinstance(config.get("identity"), dict) else {}
    return (
        str(data.get("label") or "").strip()
        or str(node.get("label") or "").strip()
        or str(identity.get("name") or identity.get("role") or "").strip()
        or str(node.get("id") or "").strip()
        or "Node"
    )


def _workflow_text_payload(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=True, indent=2)
        except Exception:
            return str(value)
    return str(value or "").strip()


def _node_preview_text(value: Any, *, limit: int = 280) -> Optional[str]:
    text = _workflow_text_payload(value)
    if not text:
        return None
    return _compact_event_text(text, limit=limit)


def _node_detail_payload(value: Any) -> Optional[Any]:
    if value is None:
        return None
    safe = _json_safe(value)
    if isinstance(safe, (dict, list)):
        return safe
    return _node_preview_text(safe, limit=400)


def _ensure_run_node_states(
    run_id: str,
    *,
    graph_kind: str,
    nodes: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    run = runs.get(run_id)
    if not isinstance(run, dict):
        return None
    existing = run.get("node_states") if isinstance(run.get("node_states"), dict) else None
    if not existing or str(existing.get("graph_kind") or "").strip().lower() != str(graph_kind or "").strip().lower():
        existing = {
            "version": 1,
            "graph_kind": str(graph_kind or "").strip().lower() or "workflow",
            "active_node_id": None,
            "final_node_id": None,
            "order": [],
            "items": {},
            "updated_at": _utc_now_iso(),
        }
        run["node_states"] = existing

    items = existing.get("items") if isinstance(existing.get("items"), dict) else {}
    order: List[str] = []
    for raw_node in nodes:
        if not isinstance(raw_node, dict):
            continue
        node_id = str(raw_node.get("id") or "").strip()
        if not node_id:
            continue
        order.append(node_id)
        current = items.get(node_id) if isinstance(items.get(node_id), dict) else {}
        if existing["graph_kind"] == "workflow":
            node_type = str(raw_node.get("type") or "").strip().lower() or "node"
            variant = str(raw_node.get("variant") or "").strip().lower() or None
            label = _workflow_label(raw_node)
        else:
            node_type = "dag"
            variant = str(raw_node.get("kind") or "").strip().lower() or None
            label = str(raw_node.get("label") or node_id).strip() or node_id
        items[node_id] = {
            "node_id": node_id,
            "label": label,
            "type": node_type,
            "variant": variant,
            "status": str(current.get("status") or "queued").strip().lower() or "queued",
            "started_at": current.get("started_at"),
            "completed_at": current.get("completed_at"),
            "duration_ms": current.get("duration_ms"),
            "input_preview": current.get("input_preview"),
            "output_preview": current.get("output_preview"),
            "summary": current.get("summary"),
            "error": current.get("error"),
            "detail": current.get("detail"),
            "child_run_id": current.get("child_run_id"),
            "child_workflow_id": current.get("child_workflow_id"),
            "waiting_for_approval": bool(current.get("waiting_for_approval")),
            "_started_mono": current.get("_started_mono"),
        }
    existing["items"] = items
    existing["order"] = order
    existing["updated_at"] = _utc_now_iso()
    run["node_states"] = existing
    return existing


def _update_run_node_state(
    run_id: str,
    node_id: str,
    *,
    status: Optional[str] = None,
    activate: bool = False,
    finalize: bool = False,
    label: Optional[str] = None,
    node_type: Optional[str] = None,
    variant: Optional[str] = None,
    input_preview: Optional[str] = None,
    output_preview: Optional[str] = None,
    summary: Optional[str] = None,
    error: Optional[str] = None,
    detail: Any = None,
    child_run_id: Optional[str] = None,
    child_workflow_id: Optional[str] = None,
    waiting_for_approval: Optional[bool] = None,
    reset_started: bool = False,
) -> None:
    run = runs.get(run_id)
    if not isinstance(run, dict):
        return
    node_states = run.get("node_states") if isinstance(run.get("node_states"), dict) else None
    if not node_states:
        return
    items = node_states.get("items") if isinstance(node_states.get("items"), dict) else {}
    item = items.get(node_id) if isinstance(items.get(node_id), dict) else {"node_id": node_id}
    previous_status = str(item.get("status") or "").strip().lower()
    if label is not None:
        item["label"] = label
    if node_type is not None:
        item["type"] = node_type
    if variant is not None:
        item["variant"] = variant or None
    timestamp = _utc_now_iso()
    if activate:
        node_states["active_node_id"] = node_id
    if status:
        clean_status = str(status or "").strip().lower()
        item["status"] = clean_status
        if clean_status == "running":
            if reset_started or not item.get("started_at"):
                item["started_at"] = timestamp
                item["_started_mono"] = time.monotonic()
            item["completed_at"] = None
            item["duration_ms"] = None
            item["error"] = None
            if waiting_for_approval is None:
                item["waiting_for_approval"] = False
        elif clean_status == "waiting_human":
            if not item.get("started_at"):
                item["started_at"] = timestamp
                item["_started_mono"] = time.monotonic()
            if waiting_for_approval is None:
                item["waiting_for_approval"] = True
        elif clean_status in NODE_TERMINAL_STATUSES:
            item["completed_at"] = timestamp
            started_mono = item.get("_started_mono")
            if isinstance(started_mono, (int, float)):
                item["duration_ms"] = round(max(0.0, (time.monotonic() - started_mono) * 1000.0), 2)
            node_states["active_node_id"] = None
            if finalize:
                node_states["final_node_id"] = node_id
            if waiting_for_approval is None:
                item["waiting_for_approval"] = False
    if input_preview is not None:
        item["input_preview"] = input_preview or None
    if output_preview is not None:
        item["output_preview"] = output_preview or None
    if summary is not None:
        item["summary"] = summary or None
    if error is not None:
        item["error"] = error or None
    if detail is not None:
        item["detail"] = _node_detail_payload(detail)
    if child_run_id is not None:
        item["child_run_id"] = child_run_id or None
    if child_workflow_id is not None:
        item["child_workflow_id"] = child_workflow_id or None
    if waiting_for_approval is not None:
        item["waiting_for_approval"] = bool(waiting_for_approval)
    items[node_id] = item
    node_states["items"] = items
    node_states["updated_at"] = timestamp
    run["node_states"] = node_states
    if status:
        clean_status = str(status or "").strip().lower()
        if clean_status and clean_status != previous_status:
            emit_log(
                run["logs"],
                "info" if clean_status not in {"failed"} else "error",
                f"Node state: {str(item.get('label') or node_id).strip() or node_id} -> {clean_status}",
                event="node_state",
                data={
                    "graph_kind": node_states.get("graph_kind"),
                    "node_id": node_id,
                    "label": item.get("label"),
                    "type": item.get("type"),
                    "variant": item.get("variant"),
                    "status": clean_status,
                    "active_node_id": node_states.get("active_node_id"),
                    "final_node_id": node_states.get("final_node_id"),
                    "waiting_for_approval": bool(item.get("waiting_for_approval")),
                },
            )


def _workflow_variant_default_tool_id(variant: str) -> str:
    return workflow_node_capability_id("tool", variant=variant, config={})


def _workflow_tool_policy_tool_id(variant: str, config: Dict[str, Any]) -> str:
    policy = config.get("policy") if isinstance(config.get("policy"), dict) else {}
    capability_id = workflow_node_capability_id(
        "tool",
        variant=variant,
        config=config,
        policy=policy,
    )
    return normalize_action_id(capability_id or _workflow_variant_default_tool_id(variant))


def _predict_tool_ids_from_workflow_definition(definition: Dict[str, Any]) -> List[str]:
    nodes = definition.get("nodes") if isinstance(definition.get("nodes"), list) else []
    out: List[str] = []
    seen: Set[str] = set()

    def _append(raw_tool: Any) -> None:
        clean = normalize_action_id(raw_tool)
        if clean and clean not in seen:
            seen.add(clean)
            out.append(clean)

    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_type = str(node.get("type") or "").strip().lower()
        variant = str(node.get("variant") or "").strip().lower()
        config = node.get("config") if isinstance(node.get("config"), dict) else {}
        policy = node.get("policy") if isinstance(node.get("policy"), dict) else {}
        if node_type == "agent":
            tools = config.get("tools") if isinstance(config.get("tools"), dict) else {}
            for item in tools.get("dynamic_allowed") if isinstance(tools.get("dynamic_allowed"), list) else []:
                _append(item)
            for item in tools.get("explicit_required") if isinstance(tools.get("explicit_required"), list) else []:
                _append(item)
        elif node_type == "tool":
            _append(workflow_node_capability_id("tool", variant=variant, config=config, policy=policy))
    return out


def _predict_tool_ids_for_context(context: Dict[str, Any]) -> List[str]:
    workflow_definition = _workflow_definition_from_context(context)
    if isinstance(workflow_definition, dict):
        predicted = _predict_tool_ids_from_workflow_definition(workflow_definition)
        if predicted:
            return predicted

    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    pack_id = str(metadata.get("outcome_pack") or "").strip().lower()
    pack_inputs = metadata.get("pack_inputs") if isinstance(metadata.get("pack_inputs"), dict) else {}

    if pack_id == CUSTOMER_OPS_PACK_ID:
        return ["draft_email", "create_calendar_event", "send_message"]
    if pack_id == WEEKLY_CONTENT_PACK_ID:
        return ["publish_content"]
    if pack_id == COMPETITOR_BRIEF_PACK_ID:
        return ["external_research"]
    if pack_id == SPREADSHEET_OPS_PACK_ID:
        operation = normalize_action_id(
            pack_inputs.get("operation")
            or pack_inputs.get("operation_type")
            or pack_inputs.get("leads")
            or "read"
        )
        if operation in {"append"}:
            return ["spreadsheet_append"]
        if operation in {"update", "edit"}:
            return ["spreadsheet_update"]
        if operation in {"create", "new"}:
            return ["spreadsheet_create"]
        return ["spreadsheet_read"]
    if pack_id == DOCUMENT_STUDIO_PACK_ID:
        operation = normalize_action_id(pack_inputs.get("operation") or pack_inputs.get("operation_type") or pack_inputs.get("leads") or "create")
        file_path = str(pack_inputs.get("file_path") or pack_inputs.get("path") or pack_inputs.get("inbox") or "").strip().lower()
        is_presentation = file_path.endswith(".pptx")
        if is_presentation:
            return ["presentation_update" if operation in {"update", "edit", "append"} else "presentation_create"]
        return ["document_update" if operation in {"update", "edit", "append"} else "document_create"]
    if pack_id == LOCAL_EXECUTION_PACK_ID:
        operations = pack_inputs.get("operations") if isinstance(pack_inputs.get("operations"), list) else []
        predicted: List[str] = []
        seen_tools: Set[str] = set()

        def _append_predicted(raw_tool: Any) -> None:
            clean = normalize_action_id(raw_tool)
            if clean and clean not in seen_tools:
                seen_tools.add(clean)
                predicted.append(clean)

        if operations:
            for item in operations:
                if not isinstance(item, dict):
                    continue
                _append_predicted(item.get("tool") or item.get("action"))
        else:
            inferred_tool = (
                pack_inputs.get("tool")
                or pack_inputs.get("action")
                or capability_tool_id(pack_inputs.get("capability"))
                or ("shell.execute" if str(pack_inputs.get("command") or "").strip() or isinstance(pack_inputs.get("argv"), list) else "")
                or ("browser_automation.interactive" if str(pack_inputs.get("url") or "").strip() else "")
                or ("screenshot.capture" if bool(pack_inputs.get("screenshot")) else "")
            )
            if not inferred_tool and str(pack_inputs.get("path") or pack_inputs.get("file_path") or "").strip():
                inferred_tool = "filesystem.read_write"
            _append_predicted(inferred_tool)
        return predicted

    text_parts: List[str] = []
    user_goal = str(context.get("user_goal") or "").strip()
    business_plan = str(context.get("business_plan") or "").strip()
    if user_goal:
        text_parts.append(user_goal)
    if business_plan:
        text_parts.append(business_plan)
    inferred = infer_actions_from_text("\n".join(text_parts))
    ordered = [normalize_action_id(action) for action in inferred.keys()]
    unique: List[str] = []
    seen: Set[str] = set()
    for action in ordered:
        if action and action not in seen:
            seen.add(action)
            unique.append(action)
    return unique


def _predict_capability_ids_for_context(context: Dict[str, Any]) -> List[str]:
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    pack_id = normalize_action_id(metadata.get("outcome_pack"))
    pack_inputs = metadata.get("pack_inputs") if isinstance(metadata.get("pack_inputs"), dict) else {}
    if pack_id != LOCAL_EXECUTION_PACK_ID:
        return []

    operations = pack_inputs.get("operations") if isinstance(pack_inputs.get("operations"), list) else []
    seen: Set[str] = set()
    capability_ids: List[str] = []

    def _append_capability(raw_capability: Any) -> None:
        clean = str(raw_capability or "").strip().lower()
        if clean and clean not in seen:
            seen.add(clean)
            capability_ids.append(clean)

    if operations:
        for item in operations:
            if not isinstance(item, dict):
                continue
            _append_capability(item.get("capability"))
    else:
        _append_capability(pack_inputs.get("capability"))
    return capability_ids


_BROWSER_AUTH_ACTIONS: Set[str] = {
    "type",
    "click",
    "select",
    "upload",
    "download",
    "open_popup",
    "open_tab",
    "switch_tab",
    "close_tab",
    "navigate",
}
_BROWSER_PRIVILEGED_ACTIONS: Set[str] = {
    "upload",
    "download",
    "open_popup",
    "open_tab",
    "close_tab",
}


def _browser_automation_policy_from_operations(browser_ops: List[Dict[str, Any]]) -> Dict[str, Any]:
    session_profiles: Set[str] = set()
    interactive_actions: Set[str] = set()
    privileged_actions: Set[str] = set()
    capture_page = False
    for operation in browser_ops:
        profile = str(operation.get("session_profile") or operation.get("sessionProfile") or "").strip()
        if profile:
            session_profiles.add(profile)
        if normalize_action_id(operation.get("mode") or "extract_text") == "capture_page":
            capture_page = True
        raw_actions = operation.get("browser_actions") if isinstance(operation.get("browser_actions"), list) else []
        for raw in raw_actions:
            if not isinstance(raw, dict):
                continue
            action = normalize_action_id(raw.get("action"))
            if not action:
                continue
            if action in _BROWSER_AUTH_ACTIONS:
                interactive_actions.add(action)
            if action in _BROWSER_PRIVILEGED_ACTIONS:
                privileged_actions.add(action)

    profile = "public_readonly"
    if session_profiles and privileged_actions:
        profile = "authenticated_privileged"
    elif session_profiles and interactive_actions:
        profile = "authenticated_interactive"
    elif session_profiles:
        profile = "authenticated_readonly"
    elif privileged_actions:
        profile = "public_privileged"
    elif interactive_actions:
        profile = "public_interactive"

    approval_reason = ""
    requires_approval = False
    if session_profiles and privileged_actions:
        requires_approval = True
        approval_reason = "session-backed privileged browser automation requires approval"
    elif session_profiles and interactive_actions:
        requires_approval = True
        approval_reason = "session-backed interactive browser automation requires approval"
    elif session_profiles:
        requires_approval = True
        approval_reason = "session-backed browser automation requires approval"

    return {
        "profile": profile,
        "session_profiles": sorted(session_profiles),
        "session_profile_count": len(session_profiles),
        "interactive_actions": sorted(interactive_actions),
        "privileged_actions": sorted(privileged_actions),
        "capture_page": bool(capture_page),
        "requires_approval": requires_approval,
        "reason": approval_reason,
        "reviewed_approval_required": profile in {"authenticated_interactive", "authenticated_privileged"},
        "immutable_plan_hash": browser_automation_plan_hash(browser_ops),
    }


def _derive_browser_automation_policy(context: Dict[str, Any]) -> Dict[str, Any]:
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    if normalize_action_id(metadata.get("outcome_pack")) != normalize_action_id(LOCAL_EXECUTION_PACK_ID):
        return {}
    pack_inputs = metadata.get("pack_inputs") if isinstance(metadata.get("pack_inputs"), dict) else {}
    operations = pack_inputs.get("operations") if isinstance(pack_inputs.get("operations"), list) else []
    browser_ops: List[Dict[str, Any]] = []
    if operations:
        for item in operations:
            if not isinstance(item, dict):
                continue
            if normalize_action_id(item.get("tool") or item.get("action")) == "browser_automation.interactive":
                browser_ops.append(item)
    elif str(pack_inputs.get("url") or "").strip():
        browser_ops.append(pack_inputs)
    if not browser_ops:
        return {}
    return _browser_automation_policy_from_operations(browser_ops)


def _compute_tool_policy_precheck(context: Dict[str, Any]) -> Dict[str, Any]:
    return policy_service.compute_tool_policy_precheck(
        context,
        derive_browser_automation_policy_fn=_derive_browser_automation_policy,
        predict_tool_ids_for_context_fn=_predict_tool_ids_for_context,
        build_skill_contract_from_metadata_fn=_build_skill_contract_from_metadata,
        predict_capability_ids_for_context_fn=_predict_capability_ids_for_context,
        apply_agent_machine_bypass_to_tool_policy_evaluation_fn=lambda evaluation: _apply_agent_machine_bypass_to_tool_policy_evaluation(
            evaluation,
            context=context,
        ),
        capability_metadata_root=Path(__file__).resolve().parent,
    )


def _append_run_tool_policy_audit(
    run_id: Optional[str],
    evaluation: Dict[str, Any],
    source: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    if not run_id:
        return
    run = runs.get(str(run_id))
    if not isinstance(run, dict):
        return
    payload = {
        "ts": _utc_now_iso(),
        "source": str(source or "runtime").strip().lower(),
        "tool_id": str(evaluation.get("tool_id") or "").strip().lower(),
        "decision": str(evaluation.get("decision") or "").strip().lower(),
        "execution_decision": str(evaluation.get("execution_decision") or "").strip().lower(),
        "reason": str(evaluation.get("reason") or "").strip(),
        "policy_mode": str(evaluation.get("policy_mode") or "").strip().lower(),
        "target": str(evaluation.get("target") or "").strip().lower(),
        "is_sensitive": bool(evaluation.get("is_sensitive")),
        "is_critical": bool(evaluation.get("is_critical")),
        "approval_label": str(evaluation.get("approval_label") or "").strip() or None,
        "classification": _json_safe(evaluation.get("classification") if isinstance(evaluation.get("classification"), dict) else {}),
        "metadata": _json_safe(metadata if isinstance(metadata, dict) else {}),
    }
    items = run.setdefault("tool_policy_audit", [])
    if isinstance(items, list):
        items.append(payload)
        if len(items) > 500:
            del items[:-500]
        run["tool_policy_audit"] = items


def _truncate_email_preview_text(value: Any, *, limit: int = 280) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 1)].rstrip()}…"


def _connector_tool_node_approval_metadata(
    *,
    node_id: str,
    label: str,
    tool_id: str,
    variant: str,
    config: Dict[str, Any],
    evaluation: Dict[str, Any],
) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {}
    if str(node_id or "").strip():
        metadata["approval_node_key"] = str(node_id).strip()
    normalized_tool_id = normalize_action_id(tool_id)
    if normalized_tool_id:
        metadata["approval_actions"] = [normalized_tool_id]
    approval_label = str(evaluation.get("approval_label") or "").strip() or label
    if approval_label:
        metadata["approval_labels"] = [approval_label]

    if variant != "connector_action":
        return metadata

    connector_id = str(config.get("connector") or "").strip()
    action_id = str(config.get("action_id") or "").strip()
    target_label = str(
        config.get("target_label")
        or config.get("to_email")
        or config.get("recipient")
        or config.get("to")
        or ""
    ).strip()
    if target_label:
        metadata["target"] = target_label
        metadata["approval_target"] = target_label
    if connector_id:
        metadata["connector"] = connector_id
    if action_id:
        metadata["connector_action"] = action_id
        metadata["approval_actions"] = [action_id]
    if connector_id and action_id:
        metadata["approval_capabilities"] = [f"{connector_id}.{action_id}"]

    recipient = str(
        config.get("to_email")
        or config.get("to")
        or config.get("email")
        or config.get("recipient")
        or ""
    ).strip()
    if recipient and action_id in {"send_email", "draft_email", "send_message"}:
        metadata["kind"] = "email_review"
        metadata["target"] = recipient
        metadata["approval_target"] = recipient
        metadata["approval_labels"] = ["Email"]
        metadata["email_preview"] = {
            "recipient": recipient,
            "subject": str(config.get("subject") or "").strip() or None,
            "body_preview": _truncate_email_preview_text(_workflow_tool_text_input(config, "")) or None,
            "connector": connector_id or None,
            "action_id": action_id or None,
            "artifact_reference": str(
                config.get("artifact_reference")
                or config.get("artifact_uri")
                or config.get("summary_artifact_reference")
                or ""
            ).strip()
            or None,
        }

    return metadata


def _validate_connector_action_request(
    *,
    connector_id: str,
    action_id: str,
    config: Dict[str, Any],
) -> None:
    if connector_id not in {"smtp", "google_workspace", "microsoft_365"}:
        return
    if action_id not in {"send_email", "draft_email"}:
        return
    recipient = str(
        config.get("to_email")
        or config.get("to")
        or config.get("email")
        or config.get("recipient")
        or ""
    ).strip()
    if not recipient:
        raise RuntimeError(f"Connector action '{action_id}' requires a recipient email.")
    if not run_service.is_valid_email_recipient(recipient):
        raise RuntimeError(f"Connector action '{action_id}' requires a valid recipient email.")


def _enqueue_local_companion_run(run_id: str, *, message: str = "Run queued for Local Companion execution.", event: str = "local_queued") -> None:
    outbox_service.enqueue_local_companion_run(
        run_id,
        runs_by_id=runs,
        set_run_status_fn=set_run_status,
        utc_now_iso_fn=_utc_now_iso,
        local_queue_lock=LOCAL_QUEUE_LOCK,
        pending_run_ids=LOCAL_PENDING_RUN_IDS,
        claimed_runs=LOCAL_CLAIMED_RUNS,
        runtime_registrations=LOCAL_WORKER_REGISTRY,
        db_path=ORION_RUNTIME_STATE_DB,
        lease_seconds=ORION_LOCAL_LEASE_SECONDS,
        emit_log_fn=emit_log,
        message=message,
        event=event,
    )



def create_run(engine: str, context: Optional[dict] = None, *, defer_local_enqueue: bool = False) -> str:
    return run_service.create_live_run(
        engine,
        context,
        defer_local_enqueue=defer_local_enqueue,
        runtime_db_path=ORION_RUNTIME_STATE_DB,
        sync_acp_manager_paths_fn=shared.sync_acp_manager_paths,
        selected_execution_target_from_context_fn=selected_execution_target_from_context,
        runs_by_id=runs,
        run_queue_index=RUN_QUEUE_INDEX,
        metrics_inc_fn=metrics_inc,
        persist_live_run_state_fn=_persist_live_run_state,
        hydrate_run_memory_context_fn=_hydrate_run_memory_context,
        enqueue_local_companion_run_fn=_enqueue_local_companion_run,
        start_background_run_fn=lambda active_run_id: threading.Thread(
            target=run_mission,
            args=(active_run_id,),
            daemon=True,
        ).start(),
        local_companion_target=EXECUTION_TARGET_LOCAL_COMPANION,
        provider_profiles=PROVIDER_PROFILES,
        memory_enabled=ORION_MEMORY_ENABLED,
        utc_now_iso_fn=_utc_now_iso,
        inject_runtime_skill_defaults_fn=_inject_runtime_skill_defaults,
        compute_tool_policy_precheck_fn=_compute_tool_policy_precheck,
    )


def _workflow_outgoing_edges(edges: List[Dict[str, Any]], node_id: str) -> List[Dict[str, Any]]:
    return [
        edge
        for edge in edges
        if isinstance(edge, dict) and str(edge.get("source") or "").strip() == node_id
    ]


def _workflow_next_node_id(
    edges: List[Dict[str, Any]],
    node_id: str,
    *,
    preferred_handle: Optional[str] = None,
) -> Optional[str]:
    outgoing = _workflow_outgoing_edges(edges, node_id)
    if preferred_handle:
        desired = str(preferred_handle or "").strip().lower()
        for edge in outgoing:
            if str(edge.get("sourceHandle") or "").strip().lower() == desired:
                target = str(edge.get("target") or "").strip()
                if target:
                    return target
    for edge in outgoing:
        target = str(edge.get("target") or "").strip()
        if target:
            return target
    return None


def _build_workflow_agent_system_prompt(config: Dict[str, Any]) -> str:
    runtime = config.get("runtime") if isinstance(config.get("runtime"), dict) else {}
    tools = config.get("tools") if isinstance(config.get("tools"), dict) else {}
    availability_lines: List[str] = []
    execution_target = str(runtime.get("execution_target") or "").strip()
    if execution_target:
        availability_lines.append(f"Execution target: {execution_target}")
    dynamic_allowed = tools.get("dynamic_allowed") if isinstance(tools.get("dynamic_allowed"), list) else []
    tool_lines = [str(item).strip() for item in dynamic_allowed if str(item).strip()]
    return build_operator_system_prompt(availability_lines, tool_lines)


def _workflow_agent_identity(config: Dict[str, Any]) -> Dict[str, Any]:
    identity = config.get("identity") if isinstance(config.get("identity"), dict) else {}
    return identity if isinstance(identity, dict) else {}


def _workflow_json_path_from_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    patterns = (
        r"\bfield\s+path\s*[:=]\s*['\"]?([a-zA-Z0-9_.]+)['\"]?",
        r"\bextract(?:\s+the)?\s+['\"]?([a-zA-Z0-9_.]+)['\"]?\s+field\b",
        r"\bextract(?:\s+the)?\s+field\s+['\"]?([a-zA-Z0-9_.]+)['\"]?",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return str(match.group(1) or "").strip()
    if re.fullmatch(r"[a-zA-Z0-9_.]+", text):
        return text
    return ""


def _workflow_agent_requested_json_path(label: str, config: Dict[str, Any]) -> str:
    identity = _workflow_agent_identity(config)
    for candidate in (
        identity.get("output_contract"),
        identity.get("goal"),
        identity.get("success_condition"),
        label,
    ):
        path = _workflow_json_path_from_text(candidate)
        if path:
            return path
    return ""


def _workflow_parse_structured_payload(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _workflow_json_lookup(payload: Any, path: str) -> Any:
    current = payload
    for part in [item for item in str(path or "").split(".") if item]:
        if isinstance(current, dict):
            if part not in current:
                raise KeyError(part)
            current = current[part]
            continue
        if isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index]
            continue
        raise KeyError(part)
    return current


def _workflow_agent_fast_extract(
    *,
    label: str,
    config: Dict[str, Any],
    current_text: str,
    state: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    field_path = _workflow_agent_requested_json_path(label, config)
    if not field_path:
        return None
    last_data = state.get("last_data") if isinstance(state.get("last_data"), dict) else {}
    candidates = [
        last_data.get("output"),
        last_data.get("http", {}).get("json") if isinstance(last_data.get("http"), dict) else None,
        current_text,
    ]
    for candidate in candidates:
        parsed = _workflow_parse_structured_payload(candidate)
        if parsed is None:
            continue
        try:
            value = _workflow_json_lookup(parsed, field_path)
        except Exception:
            continue
        text = _workflow_text_payload(value)
        return {
            "field_path": field_path,
            "value": _json_safe(value),
            "text": text,
        }
    return None


def _honest_no_provider_error(context: Dict[str, Any], metadata: Dict[str, Any]) -> RuntimeError:
    """Clear, honest error for a durable run reaching provider resolution
    with no provider signal anywhere (context, metadata, node runtime
    config). Without this, resolve_run_execution_context silently assumes
    "openai" and fails one layer down with a misleading "No credentials
    available for provider 'openai'" -- indistinguishable from an actually
    missing OpenAI key even though no provider was ever requested.
    """
    is_heartbeat = bool(
        metadata.get("wake_request_ids")
        or str(metadata.get("source") or "").strip().lower() == "heartbeat"
        or str(metadata.get("heartbeat_trigger") or "").strip()
    )
    if is_heartbeat:
        return RuntimeError(
            "No AI provider is configured for this scheduled/heartbeat run. "
            "Heartbeat and wake-up runs currently execute as the workspace "
            "orchestrator and do not dispatch to a specific agent's own "
            "cli_subscription/BYOK model_config -- that requires the acting "
            "agent's identity to be threaded through the scheduler, which is "
            "not wired up today. Set an explicit provider for heartbeat runs, "
            "or wire per-agent dispatch upstream before relying on scheduled "
            "cli_subscription/BYOK turns."
        )
    workflow_id = str(context.get("workflow_id") or metadata.get("workflow_id") or "").strip()
    where = f"workflow '{workflow_id}'" if workflow_id else "this run"
    return RuntimeError(
        f"No AI provider is configured for {where}: context.provider and "
        "metadata.provider are both empty. The run's initiator must set a "
        "provider explicitly -- silently assuming 'openai' here previously "
        "produced a misleading credential error instead of surfacing the "
        "real problem."
    )


def _resolve_agent_generation_state(base_context: Dict[str, Any], config: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any]]:
    runtime = config.get("runtime") if isinstance(config.get("runtime"), dict) else {}
    execution_context = dict(base_context)
    metadata = dict(base_context.get("metadata") if isinstance(base_context.get("metadata"), dict) else {})
    profile_id = str(runtime.get("provider_profile_id") or "").strip()
    provider = str(runtime.get("provider") or execution_context.get("provider") or metadata.get("provider") or "").strip()
    if not provider:
        raise _honest_no_provider_error(execution_context, metadata)
    model = str(runtime.get("model") or execution_context.get("model") or metadata.get("model") or "").strip()
    if profile_id:
        metadata["profile_id"] = profile_id
    metadata["provider"] = provider
    execution_context["provider"] = provider
    if model:
        metadata["model"] = model
        execution_context["model"] = model
    execution_context["metadata"] = metadata
    provider_id, selected_model, candidates, _ = resolve_run_execution_context(execution_context)
    return execution_context, {
        "provider": provider_id,
        "selected_model": str(selected_model),
        "credential_candidates": candidates,
        "credentials": candidates[0].get("credentials") if candidates else {},
    }


def _workflow_expression_scope(current_text: str, state: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    loop_vars = state.get("loop_vars") if isinstance(state.get("loop_vars"), dict) else {}
    scope = {
        "context_text": current_text,
        "result_text": current_text,
        "result_data": state.get("last_data") if isinstance(state.get("last_data"), dict) else {},
        "state": state,
        "loop_vars": loop_vars,
        "context": context if isinstance(context, dict) else {},
    }
    for key, value in loop_vars.items():
        if isinstance(key, str) and key.strip():
            scope[key] = value
    return scope


def _workflow_evaluate_expression_value(
    current_text: str,
    state: Dict[str, Any],
    expression: str,
    *,
    context: Optional[Dict[str, Any]] = None,
) -> Any:
    scope = _workflow_expression_scope(current_text, state, context)
    parsed = ast.parse(expression, mode="eval")

    def _resolve_name(name: str) -> Any:
        if name in scope:
            return scope[name]
        if name == "True":
            return True
        if name == "False":
            return False
        if name == "None":
            return None
        raise ValueError(f"Unsupported decision name '{name}'.")

    def _resolve_attribute(value: Any, attr: str) -> Any:
        if attr.startswith("__"):
            raise ValueError("Decision expressions cannot access dunder attributes.")
        if isinstance(value, dict):
            return value.get(attr)
        return getattr(value, attr)

    def _resolve_subscript(value: Any, node: ast.Subscript) -> Any:
        key = _evaluate(node.slice)
        if isinstance(value, dict):
            return value.get(key)
        return value[key]

    def _compare(operator_node: ast.cmpop, left: Any, right: Any) -> bool:
        if isinstance(operator_node, ast.Eq):
            return left == right
        if isinstance(operator_node, ast.NotEq):
            return left != right
        if isinstance(operator_node, ast.Gt):
            return left > right
        if isinstance(operator_node, ast.GtE):
            return left >= right
        if isinstance(operator_node, ast.Lt):
            return left < right
        if isinstance(operator_node, ast.LtE):
            return left <= right
        if isinstance(operator_node, ast.In):
            return left in right
        if isinstance(operator_node, ast.NotIn):
            return left not in right
        if isinstance(operator_node, ast.Is):
            return left is right
        if isinstance(operator_node, ast.IsNot):
            return left is not right
        raise ValueError(f"Unsupported decision comparator '{type(operator_node).__name__}'.")

    def _evaluate(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return _evaluate(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return _resolve_name(node.id)
        if isinstance(node, ast.Attribute):
            return _resolve_attribute(_evaluate(node.value), node.attr)
        if isinstance(node, ast.Subscript):
            return _resolve_subscript(_evaluate(node.value), node)
        if isinstance(node, ast.BoolOp):
            values = [_evaluate(value) for value in node.values]
            if isinstance(node.op, ast.And):
                return all(values)
            if isinstance(node.op, ast.Or):
                return any(values)
            raise ValueError(f"Unsupported decision boolean operator '{type(node.op).__name__}'.")
        if isinstance(node, ast.UnaryOp):
            operand = _evaluate(node.operand)
            if isinstance(node.op, ast.Not):
                return not operand
            if isinstance(node.op, ast.UAdd):
                return +operand
            if isinstance(node.op, ast.USub):
                return -operand
            raise ValueError(f"Unsupported decision unary operator '{type(node.op).__name__}'.")
        if isinstance(node, ast.Compare):
            left_value = _evaluate(node.left)
            comparisons = zip(node.ops, node.comparators)
            for operator_node, comparator in comparisons:
                right_value = _evaluate(comparator)
                if not _compare(operator_node, left_value, right_value):
                    return False
                left_value = right_value
            return True
        if isinstance(node, ast.List):
            return [_evaluate(item) for item in node.elts]
        if isinstance(node, ast.Tuple):
            return tuple(_evaluate(item) for item in node.elts)
        if isinstance(node, ast.Dict):
            return {_evaluate(key): _evaluate(value) for key, value in zip(node.keys, node.values)}
        raise ValueError(f"Unsupported decision expression node '{type(node).__name__}'.")

    return _evaluate(parsed)


def _workflow_decision_value(current_text: str, state: Dict[str, Any], expression: str) -> bool:
    return bool(_workflow_evaluate_expression_value(current_text, state, expression))


def _workflow_noop_node_state_update(*_args: Any, **_kwargs: Any) -> None:
    return None


def _workflow_raise_if_cancelled(run_id: str) -> None:
    run = runs.get(run_id)
    if not isinstance(run, dict):
        return
    status = str(run.get("status") or "").strip().lower()
    if status in {"cancelled", "stopped"}:
        raise RuntimeError(f"Workflow run {run_id} was {status}.")


def _workflow_loop_body_definition(config: Dict[str, Any]) -> Dict[str, Any]:
    body = config.get("body") if isinstance(config.get("body"), dict) else {}
    return {
        "version": str(body.get("version") or EMPYRALIST_WORKFLOW_SCHEMA_VERSION).strip() or EMPYRALIST_WORKFLOW_SCHEMA_VERSION,
        "nodes": body.get("nodes") if isinstance(body.get("nodes"), list) else [],
        "edges": body.get("edges") if isinstance(body.get("edges"), list) else [],
    }


def _workflow_loop_cap(variant: str, requested: Any) -> int:
    normalized_variant = str(variant or "").strip().lower()
    default_limit = 100 if normalized_variant == "for_each" else 50
    hard_cap = 1000 if normalized_variant == "for_each" else 500
    try:
        resolved = int(requested if requested is not None else default_limit)
    except (TypeError, ValueError):
        resolved = default_limit
    resolved = max(0, resolved)
    return min(resolved, hard_cap)


def _workflow_resolve_reference_value(
    reference: Any,
    *,
    current_text: str,
    state: Dict[str, Any],
    context: Dict[str, Any],
) -> Any:
    if reference is None:
        return None
    if isinstance(reference, (dict, list, int, float, bool)):
        return reference
    token = str(reference).strip()
    if not token:
        return None
    if token.startswith("$."):
        token = token[2:]
    if token in {"context_text", "result_text"}:
        return current_text
    if token.startswith("state.") or token.startswith("result_data.") or token.startswith("loop_vars.") or token.startswith("context."):
        parts = token.split(".")
        root = parts[0]
        if root == "state":
            value: Any = state
        elif root == "result_data":
            value = state.get("last_data") if isinstance(state.get("last_data"), dict) else {}
        elif root == "loop_vars":
            value = state.get("loop_vars") if isinstance(state.get("loop_vars"), dict) else {}
        else:
            value = context
        for part in parts[1:]:
            if isinstance(value, dict):
                value = value.get(part)
            elif isinstance(value, list):
                try:
                    value = value[int(part)]
                except (TypeError, ValueError, IndexError):
                    return None
            else:
                value = getattr(value, part, None)
            if value is None:
                return None
        return value
    return _workflow_evaluate_expression_value(current_text, state, token, context=context)


def _workflow_loop_condition_met(
    config: Dict[str, Any],
    *,
    current_text: str,
    state: Dict[str, Any],
    context: Dict[str, Any],
) -> bool:
    expression = str(config.get("expression") or "").strip()
    if expression:
        return bool(_workflow_evaluate_expression_value(current_text, state, expression, context=context))
    condition = config.get("condition") if isinstance(config.get("condition"), dict) else {}
    left = _workflow_resolve_reference_value(
        condition.get("left") or condition.get("field"),
        current_text=current_text,
        state=state,
        context=context,
    )
    operator = str(condition.get("operator") or condition.get("op") or "==").strip()
    right = condition.get("value")
    if isinstance(right, str):
        stripped = right.strip()
        if stripped.startswith(("state.", "result_data.", "loop_vars.", "context.", "$.")):
            right = _workflow_resolve_reference_value(stripped, current_text=current_text, state=state, context=context)
    if operator == "==":
        return left == right
    if operator == "!=":
        return left != right
    if operator == ">":
        return left > right
    if operator == ">=":
        return left >= right
    if operator == "<":
        return left < right
    if operator == "<=":
        return left <= right
    if operator == "in":
        return left in right
    if operator == "not_in":
        return left not in right
    raise RuntimeError(f"Unsupported WHILE operator '{operator}'.")


def _workflow_loop_result_payload(body_result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "result_text": _workflow_text_payload(body_result.get("result_text") or ""),
        "result_data": _json_safe(body_result.get("result_data")) if body_result.get("result_data") is not None else None,
        "final_node_id": str(body_result.get("final_node_id") or "").strip() or None,
    }


def _workflow_execute_loop_node(
    run_id: str,
    node_id: str,
    label: str,
    variant: str,
    config: Dict[str, Any],
    *,
    context: Dict[str, Any],
    log_queue: queue.Queue,
    state: Dict[str, Any],
    update_node_state: Callable[..., Any],
    loop_depth: int,
) -> Dict[str, Any]:
    if loop_depth >= 3:
        raise RuntimeError("Workflow loop depth exceeds the maximum nested depth of 3.")

    body_definition = _workflow_loop_body_definition(config)
    if not body_definition["nodes"]:
        raise RuntimeError(f"Loop node '{label}' requires a non-empty body workflow.")

    continue_on_error = bool(config.get("continue_on_error"))
    detail: Dict[str, Any] = {
        "variant": variant,
        "continue_on_error": continue_on_error,
        "parallel": bool(config.get("parallel")),
        "iterations": [],
    }

    def _emit_iteration_progress(summary: str) -> None:
        update_node_state(
            run_id,
            node_id,
            status="running",
            activate=True,
            summary=summary,
            detail=detail,
        )

    def _run_iteration(iteration_index: int, item_value: Any, base_state: Dict[str, Any]) -> Dict[str, Any]:
        _workflow_raise_if_cancelled(run_id)
        loop_vars = dict(base_state.get("loop_vars") if isinstance(base_state.get("loop_vars"), dict) else {})
        variable_name = str(config.get("item_variable_name") or "item").strip() or "item"
        if variant == "for_each":
            loop_vars[variable_name] = _json_safe(item_value)
            loop_vars["item_index"] = iteration_index
        loop_vars["iteration_index"] = iteration_index
        iteration_context = dict(context)
        metadata = dict(iteration_context.get("metadata") if isinstance(iteration_context.get("metadata"), dict) else {})
        metadata["loop_iteration"] = {
            "node_id": node_id,
            "label": label,
            "variant": variant,
            "index": iteration_index,
        }
        iteration_context["metadata"] = metadata
        iteration_text = _workflow_text_payload(item_value) if item_value is not None else _workflow_text_payload(base_state.get("last_text") or "")
        iteration_state = {
            "last_text": iteration_text or _workflow_text_payload(base_state.get("last_text") or ""),
            "last_data": {"item": _json_safe(item_value), "index": iteration_index} if item_value is not None else _json_safe(base_state.get("last_data")) or {},
            "active_provider": str(base_state.get("active_provider") or context.get("provider") or "openai"),
            "active_model": str(base_state.get("active_model") or context.get("model") or CODEX_MODEL),
            "loop_vars": loop_vars,
        }
        emit_log(
            log_queue,
            "info",
            f"Loop iteration {iteration_index + 1} started for {label}",
            event="workflow_loop_iteration_start",
            data={"node_id": node_id, "variant": variant, "index": iteration_index},
        )
        result = _execute_workflow_graph(
            run_id,
            iteration_context,
            log_queue,
            body_definition,
            _state=iteration_state,
            _track_node_states=False,
            _loop_depth=loop_depth + 1,
        )
        emit_log(
            log_queue,
            "info",
            f"Loop iteration {iteration_index + 1} completed for {label}",
            event="workflow_loop_iteration_complete",
            data={"node_id": node_id, "variant": variant, "index": iteration_index},
        )
        return result

    if variant == "for_each":
        raw_items = _workflow_resolve_reference_value(
            config.get("array_source"),
            current_text=_workflow_text_payload(state.get("last_text") or ""),
            state=state,
            context=context,
        )
        if not isinstance(raw_items, list):
            raise RuntimeError(f"FOR_EACH node '{label}' resolved a non-array array_source.")
        max_iterations = _workflow_loop_cap("for_each", config.get("max_iterations"))
        items = list(raw_items[:max_iterations])
        detail["requested_iterations"] = len(raw_items)
        detail["capped_iterations"] = len(items)
        detail["parallel"] = bool(config.get("parallel"))
        results: List[Optional[Dict[str, Any]]] = [None] * len(items)
        iteration_entries: List[Dict[str, Any]] = []
        last_state = state

        if bool(config.get("parallel")) and len(items) > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(items), 8)) as executor:
                future_map = {
                    executor.submit(_run_iteration, index, item_value, state): index
                    for index, item_value in enumerate(items)
                }
                for future in concurrent.futures.as_completed(future_map):
                    index = future_map[future]
                    item_value = items[index]
                    try:
                        iteration_result = future.result()
                        last_state = iteration_result.get("state") if isinstance(iteration_result.get("state"), dict) else last_state
                        results[index] = _workflow_loop_result_payload(iteration_result)
                        iteration_entries.append(
                            {
                                "index": index,
                                "status": "succeeded",
                                "item_preview": _node_preview_text(item_value),
                                "summary": _node_preview_text(results[index]),
                            }
                        )
                    except Exception as exc:
                        iteration_entries.append(
                            {
                                "index": index,
                                "status": "failed",
                                "item_preview": _node_preview_text(item_value),
                                "error": _node_preview_text(friendly_runtime_error_message(exc), limit=400),
                            }
                        )
                        if not continue_on_error:
                            for pending in future_map:
                                pending.cancel()
                            raise RuntimeError(f"FOR_EACH iteration {index + 1} failed: {friendly_runtime_error_message(exc)}") from exc
                    detail["iterations"] = sorted(iteration_entries, key=lambda item: int(item.get("index") or 0))
                    _emit_iteration_progress(f"{label}: {len(iteration_entries)}/{len(items)} iterations completed")
                    _workflow_raise_if_cancelled(run_id)
        else:
            for index, item_value in enumerate(items):
                try:
                    iteration_result = _run_iteration(index, item_value, last_state)
                    last_state = iteration_result.get("state") if isinstance(iteration_result.get("state"), dict) else last_state
                    results[index] = _workflow_loop_result_payload(iteration_result)
                    iteration_entries.append(
                        {
                            "index": index,
                            "status": "succeeded",
                            "item_preview": _node_preview_text(item_value),
                            "summary": _node_preview_text(results[index]),
                        }
                    )
                except Exception as exc:
                    iteration_entries.append(
                        {
                            "index": index,
                            "status": "failed",
                            "item_preview": _node_preview_text(item_value),
                            "error": _node_preview_text(friendly_runtime_error_message(exc), limit=400),
                        }
                    )
                    if not continue_on_error:
                        detail["iterations"] = iteration_entries
                        raise RuntimeError(f"FOR_EACH iteration {index + 1} failed: {friendly_runtime_error_message(exc)}") from exc
                detail["iterations"] = list(iteration_entries)
                _emit_iteration_progress(f"{label}: {index + 1}/{len(items)} iterations completed")
                _workflow_raise_if_cancelled(run_id)

        resolved_results = [item for item in results if item is not None]
        return {
            "current_text": _workflow_text_payload(resolved_results),
            "last_data": {
                "node_id": node_id,
                "node_type": "loop",
                "variant": variant,
                "results": resolved_results,
            },
            "summary": f"{label}: completed {len(detail['iterations'])} iterations",
            "detail": detail,
            "active_provider": str(last_state.get("active_provider") or state.get("active_provider") or context.get("provider") or "openai"),
            "active_model": str(last_state.get("active_model") or state.get("active_model") or context.get("model") or CODEX_MODEL),
        }

    if variant == "repeat":
        count_value = config.get("count")
        if count_value in {None, ""}:
            count_value = _workflow_resolve_reference_value(
                config.get("count_source"),
                current_text=_workflow_text_payload(state.get("last_text") or ""),
                state=state,
                context=context,
            )
        try:
            requested_count = int(count_value or 0)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"REPEAT node '{label}' could not resolve count.") from exc
        iteration_total = min(max(requested_count, 0), _workflow_loop_cap("repeat", config.get("max_iterations")))
        detail["requested_iterations"] = requested_count
        detail["capped_iterations"] = iteration_total
        results: List[Dict[str, Any]] = []
        last_state = state
        for index in range(iteration_total):
            try:
                iteration_result = _run_iteration(index, {"iteration": index}, last_state)
                last_state = iteration_result.get("state") if isinstance(iteration_result.get("state"), dict) else last_state
                payload = _workflow_loop_result_payload(iteration_result)
                results.append(payload)
                detail["iterations"].append({"index": index, "status": "succeeded", "summary": _node_preview_text(payload)})
            except Exception as exc:
                detail["iterations"].append({"index": index, "status": "failed", "error": _node_preview_text(friendly_runtime_error_message(exc), limit=400)})
                if not continue_on_error:
                    raise RuntimeError(f"REPEAT iteration {index + 1} failed: {friendly_runtime_error_message(exc)}") from exc
            _emit_iteration_progress(f"{label}: {index + 1}/{iteration_total} iterations completed")
            _workflow_raise_if_cancelled(run_id)
        return {
            "current_text": _workflow_text_payload(results),
            "last_data": {
                "node_id": node_id,
                "node_type": "loop",
                "variant": variant,
                "results": results,
            },
            "summary": f"{label}: completed {len(detail['iterations'])} iterations",
            "detail": detail,
            "active_provider": str(last_state.get("active_provider") or state.get("active_provider") or context.get("provider") or "openai"),
            "active_model": str(last_state.get("active_model") or state.get("active_model") or context.get("model") or CODEX_MODEL),
        }

    max_iterations = _workflow_loop_cap("while", config.get("max_iterations"))
    detail["capped_iterations"] = max_iterations
    iteration_index = 0
    last_result: Optional[Dict[str, Any]] = None
    current_state = dict(state)
    current_text = _workflow_text_payload(state.get("last_text") or "")
    while iteration_index < max_iterations and _workflow_loop_condition_met(config, current_text=current_text, state=current_state, context=context):
        try:
            iteration_result = _run_iteration(iteration_index, None, current_state)
            current_state = iteration_result.get("state") if isinstance(iteration_result.get("state"), dict) else current_state
            current_text = _workflow_text_payload(iteration_result.get("result_text") or current_text)
            last_result = iteration_result
            detail["iterations"].append(
                {
                    "index": iteration_index,
                    "status": "succeeded",
                    "summary": _node_preview_text(_workflow_loop_result_payload(iteration_result)),
                }
            )
        except Exception as exc:
            detail["iterations"].append(
                {
                    "index": iteration_index,
                    "status": "failed",
                    "error": _node_preview_text(friendly_runtime_error_message(exc), limit=400),
                }
            )
            if not continue_on_error:
                raise RuntimeError(f"WHILE iteration {iteration_index + 1} failed: {friendly_runtime_error_message(exc)}") from exc
        iteration_index += 1
        _emit_iteration_progress(f"{label}: iteration {iteration_index}")
        _workflow_raise_if_cancelled(run_id)

    detail["iterations_run"] = iteration_index
    payload = _workflow_loop_result_payload(last_result or {"result_text": current_text, "result_data": current_state.get("last_data")})
    return {
        "current_text": payload.get("result_text") or current_text,
        "last_data": payload.get("result_data") if isinstance(payload.get("result_data"), dict) else current_state.get("last_data"),
        "summary": f"{label}: {iteration_index} iterations",
        "detail": detail,
        "active_provider": str(current_state.get("active_provider") or state.get("active_provider") or context.get("provider") or "openai"),
        "active_model": str(current_state.get("active_model") or state.get("active_model") or context.get("model") or CODEX_MODEL),
    }


def _workflow_tool_text_input(config: Dict[str, Any], current_text: str) -> str:
    for candidate in [
        config.get("text"),
        config.get("body_text"),
        config.get("body"),
        config.get("message"),
        config.get("content"),
        current_text,
    ]:
        text = _workflow_text_payload(candidate)
        if text:
            return text
    return ""


def _workflow_tool_workspace_id(context: Dict[str, Any]) -> Optional[str]:
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    workspace_id = str(
        context.get("workspace_id")
        or metadata.get("workspace_id")
        or metadata.get("tenant_workspace_id")
        or "default"
    ).strip()
    return workspace_id or "default"


def _workflow_execution_source(context: Dict[str, Any]) -> str:
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    return str(metadata.get("source") or "").strip().lower()


def _assert_direct_chat_tool_execution_allowed(context: Dict[str, Any]) -> None:
    if _workflow_execution_source(context) in {"chat_direct", "chat_direct_llm_task"}:
        raise RuntimeError("direct_chat_tool_execution_blocked")


def _workflow_tool_tenant_id(context: Dict[str, Any]) -> Optional[str]:
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    tenant_id = str(context.get("tenant_id") or metadata.get("tenant_id") or "").strip()
    return tenant_id or None


def _workflow_tool_connector_secret(
    context: Dict[str, Any],
    config: Dict[str, Any],
) -> tuple[str, str, Dict[str, Any]]:
    requested_connector = str(config.get("connector") or "").strip().lower()
    if not requested_connector:
        raise RuntimeError("Connector action tool node is missing connector.")

    workspace_id = _workflow_tool_workspace_id(context)
    explicit_ids = [
        str(config.get("binding_id") or "").strip(),
        str(config.get("connector_credential_id") or "").strip(),
        str(config.get("credential_id") or "").strip(),
    ]
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    tenant_id = str(context.get("tenant_id") or metadata.get("tenant_id") or "").strip() or None
    run_id = str(context.get("run_id") or metadata.get("run_id") or "").strip() or None
    action_id = str(config.get("action_id") or metadata.get("action_id") or "").strip().lower() or None
    explicit_ids.append(str(metadata.get("connector_credential_id") or "").strip())

    for credential_id in explicit_ids:
        if not credential_id:
            continue
        secret = resolve_vault_credential(
            credential_id,
            workspace_id,
            tenant_id=tenant_id,
            connector_id=requested_connector,
            action_id=action_id,
            tool_name="workflow_connector_action",
            run_id=run_id,
            purpose="workflow_connector_action",
            actor_type="runtime",
        )
        provider = str(secret.get("_provider") or "").strip().lower()
        if provider == requested_connector:
            return credential_id, requested_connector, secret

    candidates = [
        item
        for item in list_vault_connectors(workspace_id)
        if isinstance(item, dict) and str(item.get("connector") or "").strip().lower() == requested_connector
    ]
    if not candidates:
        raise RuntimeError(f"No connector binding is available for '{requested_connector}'.")
    candidates.sort(key=lambda item: _parse_iso_datetime(item.get("updated_at")), reverse=True)
    selected = candidates[0]
    credential_id = str(selected.get("id") or "").strip()
    if not credential_id:
        raise RuntimeError(f"Connector binding for '{requested_connector}' is invalid.")
    secret = resolve_vault_credential(
        credential_id,
        workspace_id,
        tenant_id=tenant_id,
        connector_id=requested_connector,
        action_id=action_id,
        tool_name="workflow_connector_action",
        run_id=run_id,
        purpose="workflow_connector_action",
        actor_type="runtime",
    )
    return credential_id, requested_connector, secret


def _workflow_emit_connector_reliability_event(
    *,
    run_id: str,
    context: Dict[str, Any],
    connector_id: str,
    action_id: str,
    node_id: str,
    payload: Dict[str, Any],
) -> None:
    tenant_id = _workflow_tool_tenant_id(context)
    workspace_id = _workflow_tool_workspace_id(context)
    if not tenant_id or not workspace_id:
        return
    outbox_service.emit_runtime_event(
        event_type="connector_action_degraded",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        run_id=run_id,
        idempotency_key=f"{run_id}:{node_id}:{connector_id}:{action_id}:degraded",
        payload={
            "node_id": node_id,
            "connector": connector_id,
            "action_id": action_id,
            **dict(payload or {}),
        },
    )


def _workflow_connector_degraded_result(
    *,
    connector_id: str,
    credential_id: str | None,
    action_id: str,
    reason: str,
    incident_mode: str | None = None,
    incident_scope: str | None = None,
    retryable_outage: bool = False,
) -> Dict[str, Any]:
    summary = (
        f"Connector action delayed: {connector_id}.{action_id} is temporarily unavailable."
        if retryable_outage
        else f"Connector action deferred: {connector_id}.{action_id} is under an incident control."
    )
    return {
        "summary": summary,
        "result_data": {
            "connector_action": {
                "connector": connector_id,
                "credential_id": credential_id,
                "action_id": action_id,
                "executed": False,
                "degraded": True,
                "retryable_outage": bool(retryable_outage),
                "incident_mode": incident_mode,
                "incident_scope": incident_scope,
                "reason": reason,
            }
        },
    }


def _workflow_is_retryable_connector_outage(error: Exception) -> bool:
    token = str(error or "").strip().lower()
    if not token:
        return False
    return any(
        marker in token
        for marker in (
            "connector offline",
            "temporarily unavailable",
            "service unavailable",
            "upstream unavailable",
            "timed out",
            "timeout",
            "connection refused",
            "connection reset",
            "network request failed",
            "temporary failure",
            "dns",
            "502",
            "503",
            "504",
            "rate limit",
        )
    )


def _workflow_tool_connector_headers(secret: Dict[str, Any]) -> Dict[str, str]:
    access_token = str(secret.get("access_token") or "").strip()
    if not access_token:
        raise RuntimeError("Connector access token is missing.")
    return {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}


def _workflow_connector_account_identity(
    connector_id: str,
    credential_id: str,
    secret: Dict[str, Any],
) -> Optional[str]:
    credential_token = str(credential_id or "").strip()
    if credential_token:
        return f"credential:{credential_token}"
    signature = _connector_identity_signature(connector_id, secret if isinstance(secret, dict) else {}) or ""
    signature = signature.strip()
    if signature:
        return f"signature:{stable_value_fingerprint(signature)}"
    return None


def _workflow_headers_fingerprint(headers: Dict[str, Any]) -> Optional[str]:
    normalized: Dict[str, str] = {}
    for raw_key, raw_value in (headers or {}).items():
        key = str(raw_key or "").strip().lower()
        value = str(raw_value or "").strip()
        if key and value:
            normalized[key] = value
    if not normalized:
        return None
    return f"headers:{stable_value_fingerprint(normalized)}"


def _workflow_target_identity(action_id: str, **fields: Any) -> Dict[str, Any]:
    identity: Dict[str, Any] = {"action_id": normalize_action_id(action_id) or str(action_id or "").strip()}
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, str):
            trimmed = value.strip()
            if not trimmed:
                continue
            identity[key] = trimmed
            continue
        if value in ({}, [], ()):
            continue
        identity[key] = _json_safe(value)
    return identity


def _workflow_whatsapp_number(raw_value: Any) -> str:
    value = str(raw_value or "").strip()
    if not value:
        return ""
    if value.lower().startswith("whatsapp:"):
        return value
    return f"whatsapp:{value}"


def _workflow_http_headers(value: Any) -> Dict[str, str]:
    headers = value if isinstance(value, dict) else {}
    normalized: Dict[str, str] = {}
    for raw_key, raw_header_value in headers.items():
        key = str(raw_key or "").strip()
        if not key:
            continue
        normalized[key] = str(raw_header_value or "").strip()
    return normalized


def _workflow_tool_create_child_local_run(
    run_id: str,
    context: Dict[str, Any],
    *,
    label: str,
    operation: Dict[str, Any],
    summary: str,
    metadata_overrides: Optional[Dict[str, Any]] = None,
) -> str:
    return run_service.create_workflow_child_local_run(
        run_id=run_id,
        context=context,
        label=label,
        operation=operation,
        summary=summary,
        execute_workflow_child_run_request_fn=_execute_workflow_child_run_request,
        local_execution_pack_id=LOCAL_EXECUTION_PACK_ID,
        execution_target_local_companion=EXECUTION_TARGET_LOCAL_COMPANION,
        trust_mode_auto=TRUST_MODE_AUTO,
        metadata_overrides=metadata_overrides,
    )


def _workflow_wait_for_child_run(
    child_run_id: str,
    *,
    timeout_seconds: int,
    on_waiting_for_input: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    on_resumed: Optional[Callable[[str, Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    return run_service.wait_for_workflow_child_run(
        child_run_id=child_run_id,
        timeout_seconds=timeout_seconds,
        load_run_fn=runs.get,
        on_waiting_for_input=on_waiting_for_input,
        on_resumed=on_resumed,
    )


def _sanitize_recent_email_tool_results(messages: Any, *, snippet_limit: int = 1200) -> List[Dict[str, Any]]:
    safe_limit = max(80, min(int(snippet_limit or 1200), 2000))
    sanitized: List[Dict[str, Any]] = []
    if not isinstance(messages, list):
        return sanitized
    for item in messages:
        if not isinstance(item, dict):
            continue
        snippet = str(item.get("snippet") or "").strip()
        if len(snippet) > safe_limit:
            snippet = snippet[: safe_limit - 3] + "..."
        sanitized.append(
            {
                "id": str(item.get("id") or "").strip() or None,
                "threadId": str(item.get("threadId") or "").strip() or None,
                "subject": str(item.get("subject") or "").strip(),
                "from": str(item.get("from") or "").strip(),
                "to": str(item.get("to") or "").strip(),
                "date": str(item.get("date") or "").strip(),
                "snippet": snippet,
                "body_text": (str(item.get("body_text") or "").strip()[:3000]) or None,
            }
        )
    return sanitized


def _sanitize_calendar_event_tool_results(payload: Any, *, description_limit: int = 500) -> List[Dict[str, Any]]:
    safe_limit = max(80, min(int(description_limit or 500), 1000))
    if isinstance(payload, dict):
        raw_items = payload.get("events") if isinstance(payload.get("events"), list) else payload.get("items")
    else:
        raw_items = payload
    if not isinstance(raw_items, list):
        return []
    sanitized: List[Dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        start_payload = item.get("start") if isinstance(item.get("start"), dict) else {}
        end_payload = item.get("end") if isinstance(item.get("end"), dict) else {}
        description = str(item.get("description") or "").strip()
        if len(description) > safe_limit:
            description = description[: safe_limit - 3] + "..."
        sanitized.append(
            {
                "id": str(item.get("id") or "").strip() or None,
                "summary": str(item.get("summary") or "").strip(),
                "start": str(item.get("start") or start_payload.get("dateTime") or start_payload.get("date") or "").strip(),
                "end": str(item.get("end") or end_payload.get("dateTime") or end_payload.get("date") or "").strip(),
                "location": str(item.get("location") or "").strip(),
                "description": description,
                "attendeeCount": int(item.get("attendeeCount") or len(item.get("attendees") or []))
                if isinstance(item.get("attendees"), list) or str(item.get("attendeeCount") or "").isdigit()
                else 0,
                "htmlLink": str(item.get("htmlLink") or "").strip() or None,
            }
        )
    return sanitized


def _sanitize_drive_file_tool_results(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, dict):
        raw_items = payload.get("items") if isinstance(payload.get("items"), list) else payload.get("files")
    else:
        raw_items = payload
    if not isinstance(raw_items, list):
        return []
    sanitized: List[Dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        sanitized.append(
            {
                "id": str(item.get("id") or "").strip() or None,
                "name": str(item.get("name") or "").strip(),
                "kind": str(item.get("kind") or "").strip() or ("folder" if str(item.get("mimeType") or "") == "application/vnd.google-apps.folder" else "file"),
                "path": str(item.get("path") or "").strip() or None,
                "mimeType": str(item.get("mimeType") or "").strip() or None,
                "modifiedTime": str(item.get("modifiedTime") or "").strip() or None,
                "webUrl": str(item.get("webUrl") or item.get("webViewLink") or "").strip() or None,
            }
        )
    return sanitized


def _run_authority_tier(context: Dict[str, Any]) -> Optional[str]:
    """Mirrors skills_service._authority_mandate_gate's session_ctx lookup,
    adapted for the run/workflow execution context. A direct "authority_tier"
    key covers a producer that stamps it straight onto the run context or its
    metadata; the nested agent_turn_request.authority_tier lookup covers the
    path an AgentTurnRequest's tier actually survives through today
    (serialize_agent_turn_request -> bind_agent_turn_metadata ->
    build_turn_seed_from_request -> create_run's metadata). None means "not
    stamped anywhere this function knows to look."""
    if not isinstance(context, dict):
        return None
    if "authority_tier" in context:
        return context.get("authority_tier")
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    if "authority_tier" in metadata:
        return metadata.get("authority_tier")
    agent_turn_request = metadata.get("agent_turn_request") if isinstance(metadata.get("agent_turn_request"), dict) else {}
    if "authority_tier" in agent_turn_request:
        return agent_turn_request.get("authority_tier")
    return None


def _run_agent_mandate_audience_tools(context: Dict[str, Any]) -> List[str]:
    """The calling agent's owner-declared mandate.audience_tools allowlist
    (fleet_tools._ALLOWED_CONFIGURE_KEYS "mandate" patch), fetched fresh —
    this execution surface has no pre-loaded specialist toolset the way the
    live chat/skills_service path does."""
    agent_id = str(context.get("agent_id") or context.get("deployed_agent_id") or "").strip() if isinstance(context, dict) else ""
    if not agent_id:
        return []
    workspace_id = _workflow_tool_workspace_id(context) or ""
    if not workspace_id:
        return []
    tenant_id = _workflow_tool_tenant_id(context) or "default"
    try:
        from server_modules import agent_registry_repository

        bundle = asyncio.run(
            agent_registry_repository.get_workspace_agent_install_bundle(
                agent_id, tenant_id=tenant_id, workspace_id=workspace_id,
            )
        )
    except Exception:
        return []
    if not isinstance(bundle, dict):
        return []
    meta = bundle.get("install_metadata") if isinstance(bundle.get("install_metadata"), dict) else bundle.get("metadata")
    mandate = meta.get("mandate") if isinstance(meta, dict) and isinstance(meta.get("mandate"), dict) else {}
    tools = mandate.get("audience_tools")
    return [str(t) for t in tools] if isinstance(tools, list) else []


def _connector_mandate_gate(
    connector_id: str,
    action_id: str,
    context: Dict[str, Any],
) -> "tuple[bool, Optional[str], Optional[bool], bool]":
    """The connector/MCP counterpart to skills_service._authority_mandate_gate
    — same enforcement rule (authority_mandate_service.is_tool_call_allowed),
    applied at the choke point runs/workflows take instead of skills_
    service's tool-call loop, which this function historically bypassed
    entirely.

    Connector/MCP actions have no catalog-level audience_safe manifest
    (skills_service.ToolDescriptor only covers local/builtin tools) — so
    audience_safe here comes ONLY from the calling agent's
    mandate.audience_tools allowlist. An unlisted connector/MCP action
    defaults to NOT audience_safe (fail-safe), unlike manifest-backed tools.

    FAIL-CLOSED (mandate completion follow-up): absent tier now normalizes
    to audience, same as skills_service's gate — never a pass-through.
    Verified safe by enumerating every producer that reaches run_service.
    create_run's single call site: agent-turn-spawned runs carry
    authority_tier via serialize_agent_turn_request -> bind_agent_turn_
    metadata -> metadata.agent_turn_request.authority_tier; child/subflow/
    delegated runs inherit it via build_workflow_child_metadata's wholesale
    parent-metadata copy; schedule-fired and webhook-ingested runs resolve
    it through build_run_start_turn_request's current_user derivation
    (fixed to no longer misresolve the "no real caller" system placeholder
    to owner — see agent_turn._current_user_is_owner). Returns (allowed,
    tier, audience_safe, unattributed) — unattributed=True means the key
    was absent, ledgered as mandate_unattributed (non-blocking) rather than
    mandate_blocked, distinct from an actual denial.
    """
    raw_tier = _run_authority_tier(context)
    unattributed = raw_tier is None
    tier = authority_mandate_service.normalize_tier(raw_tier)
    audience_tools = _run_agent_mandate_audience_tools(context)
    tool_key = authority_mandate_service.connector_tool_key(connector_id, action_id)
    audience_safe = authority_mandate_service.is_audience_tool_allowed(audience_tools, tool_key)
    allowed = authority_mandate_service.is_tool_call_allowed(tier, audience_safe=audience_safe)
    return allowed, tier, audience_safe, unattributed


def _connector_mandate_blocked_ledger_kwargs(
    *,
    connector_id: str,
    action_id: str,
    tier: str,
    audience_safe: bool,
    workspace_id: str,
    tenant_id: str,
    run_id: str,
    node_id: str,
) -> Dict[str, Any]:
    return dict(
        tenant_id=str(tenant_id or "default").strip() or "default",
        workspace_id=str(workspace_id or "default").strip() or "default",
        actor_type="run",
        actor_id=str(run_id or "workflow").strip() or "workflow",
        event_class=authority_mandate_service.MANDATE_BLOCKED_EVENT_CLASS,
        detail_level="audit_reference",
        action="mandate_blocked",
        thread_id=None,
        title=f"Blocked: {connector_id}.{action_id} requires the workspace owner",
        summary=(
            f"Tier '{tier}' attempted '{connector_id}.{action_id}' in run {run_id} (node {node_id}), "
            "which is not audience_safe. Blocked at the connector/MCP execution choke point."
        ),
        status="blocked",
        metadata={
            "connector_id": connector_id or None,
            "action_id": action_id or None,
            "authority_tier": tier,
            "audience_safe": audience_safe,
            "run_id": run_id or None,
            "node_id": node_id or None,
        },
    )


def _connector_mandate_unattributed_ledger_kwargs(
    *,
    connector_id: str,
    action_id: str,
    workspace_id: str,
    tenant_id: str,
    run_id: str,
    node_id: str,
) -> Dict[str, Any]:
    return dict(
        tenant_id=str(tenant_id or "default").strip() or "default",
        workspace_id=str(workspace_id or "default").strip() or "default",
        actor_type="run",
        actor_id=str(run_id or "workflow").strip() or "workflow",
        event_class=authority_mandate_service.MANDATE_UNATTRIBUTED_EVENT_CLASS,
        detail_level="audit_reference",
        action="run_tier_unattributed",
        thread_id=None,
        title=f"Run reached the connector mandate gate with no authority_tier: {connector_id}.{action_id}",
        summary=(
            f"Run {run_id} (node {node_id}) called '{connector_id}.{action_id}' with no authority_tier "
            "anywhere in its context/metadata. Defaulted to audience (fail-closed)."
        ),
        status="logged",
        metadata={
            "connector_id": connector_id or None,
            "action_id": action_id or None,
            "run_id": run_id or None,
            "node_id": node_id or None,
        },
    )


def _workflow_execute_connector_action(
    run_id: str,
    node_id: str,
    context: Dict[str, Any],
    config: Dict[str, Any],
    *,
    current_text: str,
) -> Dict[str, Any]:
    _assert_direct_chat_tool_execution_allowed(context)
    resolved_workspace_id = _workflow_tool_workspace_id(context)
    if resolved_workspace_id and not str(context.get("workspace_id") or "").strip():
        context = {**context, "workspace_id": resolved_workspace_id}
    requested_connector = str(config.get("connector") or "").strip().lower()
    action_id = normalize_action_id(config.get("action_id"))
    if not action_id:
        raise RuntimeError("Connector action tool node is missing action_id.")
    if not requested_connector:
        raise RuntimeError("Connector action tool node is missing connector.")

    mandate_allowed, mandate_tier, mandate_audience_safe, mandate_unattributed = _connector_mandate_gate(
        requested_connector, action_id, context
    )
    if mandate_unattributed:
        try:
            from server_modules import activity_ledger_service

            asyncio.run(
                activity_ledger_service.append_activity_event(
                    **_connector_mandate_unattributed_ledger_kwargs(
                        connector_id=requested_connector,
                        action_id=action_id,
                        workspace_id=_workflow_tool_workspace_id(context) or "default",
                        tenant_id=_workflow_tool_tenant_id(context) or "default",
                        run_id=run_id,
                        node_id=node_id,
                    )
                )
            )
        except Exception:
            pass
    if not mandate_allowed:
        try:
            from server_modules import activity_ledger_service

            asyncio.run(
                activity_ledger_service.append_activity_event(
                    **_connector_mandate_blocked_ledger_kwargs(
                        connector_id=requested_connector,
                        action_id=action_id,
                        tier=mandate_tier or "",
                        audience_safe=bool(mandate_audience_safe),
                        workspace_id=_workflow_tool_workspace_id(context) or "default",
                        tenant_id=_workflow_tool_tenant_id(context) or "default",
                        run_id=run_id,
                        node_id=node_id,
                    )
                )
            )
        except Exception:
            pass
        raise RuntimeError(authority_mandate_service.MANDATE_BLOCKED_MESSAGE)

    if requested_connector == "custom_api" and action_id in {"http_request", "signed_webhook"}:
        method = str(config.get("method") or ("POST" if action_id == "signed_webhook" else "GET")).strip().upper() or "GET"
        url = str(config.get("url") or "").strip()
        if not url:
            raise RuntimeError(f"Connector action '{requested_connector}.{action_id}' requires a URL.")
        assert_safe_outbound_url(url)
        headers = _workflow_http_headers(config.get("headers"))
        payload_value = config.get("payload")
        if payload_value is None and method != "GET":
            payload_value = {"context": _workflow_tool_text_input(config, current_text)}
        request_payload: Any = payload_value
        if action_id == "signed_webhook":
            signing_secret = str(
                config.get("signing_secret")
                or config.get("secret")
                or ""
            ).strip()
            if not signing_secret:
                raise RuntimeError("signed_webhook requires signing_secret.")
            if isinstance(payload_value, (bytes, bytearray)):
                body_bytes = bytes(payload_value)
            else:
                body_bytes = json.dumps(payload_value if payload_value is not None else {}).encode("utf-8")
                headers.setdefault("Content-Type", "application/json")
            signature = hmac.new(
                signing_secret.encode("utf-8"),
                body_bytes,
                hashlib.sha256,
            ).hexdigest()
            headers.setdefault("X-Empyralist-Signature-SHA256", signature)
            request_payload = body_bytes
        def _perform_custom_api() -> Dict[str, Any]:
            response = http_json_request(
                url,
                method=method,
                headers=headers,
                payload=request_payload,
                timeout=max(5, int(config.get("timeout_seconds") or 30)),
            )
            status_code = int(response.get("status") or 500)
            if status_code >= 400:
                body = response.get("json") if isinstance(response.get("json"), dict) else {}
                detail = str(body.get("message") or body.get("detail") or response.get("text") or "").strip()
                raise RuntimeError(detail or f"Custom API request failed with status {status_code}.")
            result = response.get("json") if response.get("json") is not None else response.get("text")
            return {
                "summary": f"Connector action completed: custom_api.{action_id}.",
                "result_data": {
                    "connector_action": {
                        "connector": "custom_api",
                        "credential_id": None,
                        "action_id": action_id,
                        "url": url,
                        "method": method,
                        "result": _json_safe(result),
                    }
                },
            }

        if method == "GET":
            return _perform_custom_api()
        return execute_external_write_once(
            run_id=run_id,
            context=context,
            step_key=f"workflow_node:{node_id}",
            category="workflow_connector_action",
            operation={"connector": "custom_api", "action_id": action_id, "method": method, "url": url},
            target_system="custom_api",
            account_identity=_workflow_headers_fingerprint(headers),
            target_identity=_workflow_target_identity(
                action_id,
                method=method,
                url_fingerprint=stable_value_fingerprint(url),
            ),
            payload={"connector": "custom_api", "action_id": action_id, "method": method, "url": url, "payload": _json_safe(payload_value)},
            execute=_perform_custom_api,
        )

    credential_id, connector_id, secret = _workflow_tool_connector_secret(context, config)
    workspace_id = _workflow_tool_workspace_id(context)
    tenant_id = _workflow_tool_tenant_id(context)
    connector_account_identity = _workflow_connector_account_identity(connector_id, credential_id, secret)
    connector_incident = safe_mode_service.resolve_connector_incident_state(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        connector_id=connector_id,
        credential_id=credential_id,
    )
    if bool(connector_incident.get("active")):
        reason = str(connector_incident.get("reason") or "").strip() or "Connector incident control is active."
        mode = str(connector_incident.get("mode") or "pause").strip().lower() or "pause"
        scope = str(connector_incident.get("scope") or "connector").strip().lower() or "connector"
        _workflow_emit_connector_reliability_event(
            run_id=run_id,
            context=context,
            connector_id=connector_id,
            action_id=action_id,
            node_id=node_id,
            payload={
                "reason": reason,
                "incident_mode": mode,
                "incident_scope": scope,
            },
        )
        return _workflow_connector_degraded_result(
            connector_id=connector_id,
            credential_id=credential_id,
            action_id=action_id,
            reason=reason,
            incident_mode=mode,
            incident_scope=scope,
        )

    if connector_id == "telegram_bot" and action_id in {"send_message", "send_media", "update_message"}:
        chat_id = str(config.get("chat_id") or secret.get("chat_id") or "").strip()
        if not chat_id:
            raise RuntimeError("Telegram connector action requires chat_id.")
        body_text = _workflow_tool_text_input(config, current_text)

        def _perform_telegram() -> Dict[str, Any]:
            result = asyncio.run(
                handle_telegram_send_message(
                    text=body_text,
                    workspace_id=workspace_id,
                    session_key=str(config.get("session_key") or "").strip() or None,
                    chat_id=chat_id,
                )
            )
            return {
                "summary": f"Connector action completed: telegram_bot.{action_id}.",
                "result_data": {
                    "connector_action": {
                        "connector": connector_id,
                        "credential_id": credential_id,
                        "action_id": action_id,
                        "chat_id": chat_id,
                        "result": _json_safe(result),
                    }
                },
            }

        return execute_external_write_once(
            run_id=run_id,
            context=context,
            step_key=f"workflow_node:{node_id}",
            category="workflow_connector_action",
            operation={"connector": connector_id, "action_id": action_id, "chat_id": chat_id},
            target_system=connector_id,
            account_identity=connector_account_identity,
            target_identity=_workflow_target_identity(action_id, chat_id=chat_id),
            payload={"connector": connector_id, "action_id": action_id, "chat_id": chat_id, "text": body_text},
            execute=_perform_telegram,
        )

    if connector_id == "discord_bot" and action_id in {
        "send_message",
        "send_embed",
        "send_dm",
        "edit_message",
        "delete_message",
        "list_guilds",
        "list_channels",
        "list_members",
        "get_message_history",
        "create_thread",
        "add_reaction",
    }:
        bot_token = str(secret.get("bot_token") or "").strip()
        channel_id = str(config.get("channel_id") or secret.get("channel_id") or "").strip()
        guild_id = str(config.get("guild_id") or secret.get("guild_id") or "").strip()
        user_id = str(config.get("user_id") or config.get("recipient_id") or "").strip()
        message_id = str(config.get("message_id") or "").strip()
        emoji = str(config.get("emoji") or "").strip()
        thread_name = str(config.get("name") or config.get("title") or "").strip()
        raw_files = config.get("files") if isinstance(config.get("files"), list) else None
        file_path = str(config.get("file_path") or config.get("path") or "").strip()
        files = raw_files if raw_files else ([file_path] if file_path else [])
        embeds = config.get("embeds") if isinstance(config.get("embeds"), list) else None
        try:
            limit = int(config.get("limit") or 20)
        except Exception:
            limit = 20
        safe_limit = max(1, min(limit, 100))
        if not bot_token:
            raise RuntimeError("Discord connector action requires bot_token.")
        body_text = _workflow_tool_text_input(config, current_text)
        if action_id == "list_guilds":
            result = discord_list_guilds(secret, limit=safe_limit, http_json_request=http_json_request)
            return {
                "summary": "Connector action completed: discord_bot.list_guilds.",
                "result_data": {
                    "connector_action": {
                        "connector": connector_id,
                        "credential_id": credential_id,
                        "action_id": action_id,
                        "result": _json_safe(result),
                    }
                },
            }
        if action_id == "list_channels":
            if not guild_id:
                raise RuntimeError("Discord list_channels requires guild_id.")
            result = discord_list_channels(secret, guild_id, http_json_request=http_json_request)
            return {
                "summary": "Connector action completed: discord_bot.list_channels.",
                "result_data": {
                    "connector_action": {
                        "connector": connector_id,
                        "credential_id": credential_id,
                        "action_id": action_id,
                        "guild_id": guild_id,
                        "result": _json_safe(result),
                    }
                },
            }
        if action_id == "list_members":
            if not guild_id:
                raise RuntimeError("Discord list_members requires guild_id.")
            result = discord_list_members(secret, guild_id, limit=safe_limit, http_json_request=http_json_request)
            return {
                "summary": "Connector action completed: discord_bot.list_members.",
                "result_data": {
                    "connector_action": {
                        "connector": connector_id,
                        "credential_id": credential_id,
                        "action_id": action_id,
                        "guild_id": guild_id,
                        "result": _json_safe(result),
                    }
                },
            }
        if action_id == "get_message_history":
            if not channel_id:
                raise RuntimeError("Discord get_message_history requires channel_id.")
            result = discord_get_message_history(secret, channel_id, limit=safe_limit, http_json_request=http_json_request)
            return {
                "summary": "Connector action completed: discord_bot.get_message_history.",
                "result_data": {
                    "connector_action": {
                        "connector": connector_id,
                        "credential_id": credential_id,
                        "action_id": action_id,
                        "channel_id": channel_id,
                        "result": _json_safe(result),
                    }
                },
            }

        def _perform_discord() -> Dict[str, Any]:
            if action_id == "send_message":
                if not channel_id:
                    raise RuntimeError("Discord send_message requires channel_id.")
                result = discord_send_channel_message(
                    secret,
                    channel_id,
                    body_text,
                    embeds=embeds,
                    files=files,
                    http_json_request=http_json_request,
                )
            elif action_id == "send_embed":
                if not channel_id:
                    raise RuntimeError("Discord send_embed requires channel_id.")
                normalized_embeds = embeds if embeds else [
                    {
                        "title": str(
                            config.get("title")
                            or context.get("workflow_name")
                            or context.get("workflow_id")
                            or "Empyralist"
                        ).strip() or "Empyralist",
                        "description": body_text,
                    }
                ]
                result = discord_send_embed(
                    secret,
                    channel_id,
                    title=str(config.get("title") or "").strip(),
                    description=body_text,
                    embeds=normalized_embeds,
                    http_json_request=http_json_request,
                )
            elif action_id == "send_dm":
                if not user_id:
                    raise RuntimeError("Discord send_dm requires user_id.")
                result = discord_send_dm_message(secret, user_id, body_text, http_json_request=http_json_request)
            elif action_id == "edit_message":
                if not channel_id or not message_id:
                    raise RuntimeError("Discord edit_message requires channel_id and message_id.")
                result = discord_edit_message(secret, channel_id, message_id, body_text, http_json_request=http_json_request)
            elif action_id == "delete_message":
                if not channel_id or not message_id:
                    raise RuntimeError("Discord delete_message requires channel_id and message_id.")
                result = discord_delete_message(secret, channel_id, message_id, http_json_request=http_json_request)
            elif action_id == "create_thread":
                if not channel_id:
                    raise RuntimeError("Discord create_thread requires channel_id.")
                result = discord_create_thread(
                    secret,
                    channel_id,
                    thread_name or "New thread",
                    message_id=message_id or None,
                    http_json_request=http_json_request,
                )
            elif action_id == "add_reaction":
                if not channel_id or not message_id or not emoji:
                    raise RuntimeError("Discord add_reaction requires channel_id, message_id, and emoji.")
                result = discord_add_reaction(secret, channel_id, message_id, emoji, http_json_request=http_json_request)
            else:
                raise RuntimeError(f"Unsupported Discord action '{action_id}'.")
            return {
                "summary": f"Connector action completed: discord_bot.{action_id}.",
                "result_data": {
                    "connector_action": {
                        "connector": connector_id,
                        "credential_id": credential_id,
                        "action_id": action_id,
                        "channel_id": channel_id or None,
                        "guild_id": guild_id or None,
                        "message_id": message_id or None,
                        "user_id": user_id or None,
                        "result": _json_safe(result),
                    }
                },
            }

        if action_id in {"send_message", "send_embed", "send_dm", "delete_message"}:
            operation_target = channel_id or user_id or guild_id
            return execute_external_write_once(
                run_id=run_id,
                context=context,
                step_key=f"workflow_node:{node_id}",
                category="workflow_connector_action",
                operation={"connector": connector_id, "action_id": action_id, "channel_id": channel_id, "user_id": user_id, "message_id": message_id},
                target_system=connector_id,
                account_identity=connector_account_identity,
                target_identity=_workflow_target_identity(action_id, channel_id=operation_target, message_id=message_id),
                payload={
                    "connector": connector_id,
                    "action_id": action_id,
                    "channel_id": channel_id or None,
                    "guild_id": guild_id or None,
                    "user_id": user_id or None,
                    "message_id": message_id or None,
                    "text": body_text,
                    "embeds": _json_safe(embeds) if embeds else None,
                },
                execute=_perform_discord,
            )
        return _perform_discord()

    if connector_id == "slack" and action_id in {"send_message", "send_dm", "post_reply", "upload_file", "list_channels", "get_history"}:
        channel_id = str(
            config.get("channel_id")
            or config.get("channel")
            or secret.get("incoming_webhook_channel_id")
            or secret.get("channel_id")
            or ""
        ).strip()
        user_id = str(
            config.get("user_id")
            or config.get("recipient_id")
            or config.get("recipient")
            or ""
        ).strip()
        thread_ts = str(config.get("thread_ts") or config.get("parent_ts") or "").strip()
        file_path = str(config.get("file_path") or config.get("path") or "").strip()
        body_text = _workflow_tool_text_input(config, current_text)

        if action_id == "list_channels":
            result = slack_list_channels(secret, limit=int(config.get("limit") or 20))
            return {
                "summary": "Connector action completed: slack.list_channels.",
                "result_data": {
                    "connector_action": {
                        "connector": connector_id,
                        "credential_id": credential_id,
                        "action_id": action_id,
                        "result": _json_safe(result),
                    }
                },
            }

        if action_id == "get_history":
            if not channel_id:
                raise RuntimeError("Slack get_history requires channel or channel_id.")
            result = slack_get_channel_history(secret, channel_id, limit=int(config.get("limit") or 20))
            return {
                "summary": "Connector action completed: slack.get_history.",
                "result_data": {
                    "connector_action": {
                        "connector": connector_id,
                        "credential_id": credential_id,
                        "action_id": action_id,
                        "channel_id": channel_id,
                        "result": _json_safe(result),
                    }
                },
            }

        def _perform_slack() -> Dict[str, Any]:
            if action_id == "send_message":
                if not channel_id:
                    raise RuntimeError("Slack send_message requires channel or channel_id.")
                result = slack_send_channel_message(secret, channel_id, body_text)
            elif action_id == "send_dm":
                if not user_id:
                    raise RuntimeError("Slack send_dm requires user_id.")
                result = slack_send_dm_message(secret, user_id, body_text)
            elif action_id == "post_reply":
                if not channel_id:
                    raise RuntimeError("Slack post_reply requires channel or channel_id.")
                if not thread_ts:
                    raise RuntimeError("Slack post_reply requires thread_ts.")
                result = slack_post_reply(secret, channel_id, thread_ts, body_text)
            else:
                if not channel_id:
                    raise RuntimeError("Slack upload_file requires channel or channel_id.")
                if not file_path:
                    raise RuntimeError("Slack upload_file requires file_path.")
                result = slack_upload_file(
                    secret,
                    channel_id,
                    file_path,
                    str(config.get("title") or Path(file_path).name).strip() or Path(file_path).name,
                )
            return {
                "summary": f"Connector action completed: slack.{action_id}.",
                "result_data": {
                    "connector_action": {
                        "connector": connector_id,
                        "credential_id": credential_id,
                        "action_id": action_id,
                        "channel_id": channel_id or None,
                        "user_id": user_id or None,
                        "thread_ts": thread_ts or None,
                        "result": _json_safe(result),
                    }
                },
            }

        target_identity = _workflow_target_identity(
            action_id,
            channel_id=channel_id or None,
            recipient=user_id or None,
            thread_ts=thread_ts or None,
            path=file_path or None,
        )
        payload = {
            "connector": connector_id,
            "action_id": action_id,
            "channel_id": channel_id or None,
            "user_id": user_id or None,
            "thread_ts": thread_ts or None,
            "text": body_text or None,
            "file_path": file_path or None,
        }
        return execute_external_write_once(
            run_id=run_id,
            context=context,
            step_key=f"workflow_node:{node_id}",
            category="workflow_connector_action",
            operation={"connector": connector_id, "action_id": action_id, "channel_id": channel_id or None, "user_id": user_id or None},
            target_system=connector_id,
            account_identity=connector_account_identity,
            target_identity=target_identity,
            payload=payload,
            execute=_perform_slack,
        )

    if connector_id == "smtp" and action_id in {"send_email", "fetch_emails"}:
        if action_id == "fetch_emails":
            result = smtp_fetch_emails(
                secret,
                folder=str(config.get("folder") or "INBOX").strip() or "INBOX",
                limit=int(config.get("limit") or 10),
                unread_only=bool(config.get("unread_only", True)),
            )
            return {
                "summary": "Connector action completed: smtp.fetch_emails.",
                "result_data": {
                    "connector_action": {
                        "connector": connector_id,
                        "credential_id": credential_id,
                        "action_id": action_id,
                        "result": _json_safe(result),
                    }
                },
            }

        to_email = str(
            config.get("to_email")
            or config.get("to")
            or config.get("email")
            or config.get("recipient")
            or ""
        ).strip()
        if not to_email:
            raise RuntimeError("SMTP send_email requires a recipient email.")
        if not run_service.is_valid_email_recipient(to_email):
            raise RuntimeError("SMTP send_email requires a valid recipient email.")
        subject = str(
            config.get("subject")
            or f"Empyralist workflow: {context.get('workflow_name') or context.get('workflow_id') or 'Untitled'}"
        ).strip()
        body_text = _workflow_tool_text_input(config, current_text)

        def _perform_smtp() -> Dict[str, Any]:
            result = smtp_send_email(
                secret,
                to_email,
                subject,
                body_text,
                html_body=str(config.get("html_body") or "").strip() or None,
            )
            return {
                "summary": "Connector action completed: smtp.send_email.",
                "result_data": {
                    "connector_action": {
                        "connector": connector_id,
                        "credential_id": credential_id,
                        "action_id": action_id,
                        "recipient": to_email,
                        "subject": subject,
                        "result": _json_safe(result),
                    }
                },
            }

        return execute_external_write_once(
            run_id=run_id,
            context=context,
            step_key=f"workflow_node:{node_id}",
            category="workflow_connector_action",
            operation={"connector": connector_id, "action_id": action_id, "recipient": to_email, "subject": subject},
            target_system=connector_id,
            account_identity=connector_account_identity,
            target_identity=_workflow_target_identity(action_id, recipient=to_email, subject=subject),
            payload={"connector": connector_id, "action_id": action_id, "recipient": to_email, "subject": subject, "body": body_text},
            execute=_perform_smtp,
        )

    if connector_id == "github" and action_id in {
        "list_repos",
        "get_repo",
        "list_issues",
        "create_issue",
        "comment_on_issue",
        "list_pull_requests",
        "create_pull_request",
        "get_file_content",
        "create_or_update_file",
        "list_commits",
    }:
        owner = str(config.get("owner") or "").strip()
        repo_name = str(config.get("repo") or config.get("repository") or "").strip()
        if not owner and "/" in repo_name:
            owner, repo_name = repo_name.split("/", 1)
        org = str(config.get("org") or "").strip() or None
        file_path = str(config.get("file_path") or config.get("path") or "").strip()
        branch = str(config.get("branch") or "").strip() or None
        body_text = _workflow_tool_text_input(config, current_text)

        if action_id == "list_repos":
            result = github_list_repos(secret, org=org, limit=int(config.get("limit") or 50))
            return {
                "summary": "Connector action completed: github.list_repos.",
                "result_data": {
                    "connector_action": {
                        "connector": connector_id,
                        "credential_id": credential_id,
                        "action_id": action_id,
                        "org": org,
                        "result": _json_safe(result),
                    }
                },
            }

        if not owner or not repo_name:
            raise RuntimeError(f"GitHub {action_id} requires owner and repo.")

        if action_id == "get_repo":
            result = github_get_repo(secret, owner, repo_name)
            return {
                "summary": "Connector action completed: github.get_repo.",
                "result_data": {
                    "connector_action": {
                        "connector": connector_id,
                        "credential_id": credential_id,
                        "action_id": action_id,
                        "owner": owner,
                        "repo": repo_name,
                        "result": _json_safe(result),
                    }
                },
            }

        if action_id == "list_issues":
            result = github_list_issues(
                secret,
                owner,
                repo_name,
                state=str(config.get("state") or "open").strip() or "open",
                limit=int(config.get("limit") or 20),
            )
            return {
                "summary": "Connector action completed: github.list_issues.",
                "result_data": {
                    "connector_action": {
                        "connector": connector_id,
                        "credential_id": credential_id,
                        "action_id": action_id,
                        "owner": owner,
                        "repo": repo_name,
                        "result": _json_safe(result),
                    }
                },
            }

        if action_id == "list_pull_requests":
            result = github_list_pull_requests(
                secret,
                owner,
                repo_name,
                state=str(config.get("state") or "open").strip() or "open",
                limit=int(config.get("limit") or 20),
            )
            return {
                "summary": "Connector action completed: github.list_pull_requests.",
                "result_data": {
                    "connector_action": {
                        "connector": connector_id,
                        "credential_id": credential_id,
                        "action_id": action_id,
                        "owner": owner,
                        "repo": repo_name,
                        "result": _json_safe(result),
                    }
                },
            }

        if action_id == "get_file_content":
            if not file_path:
                raise RuntimeError("GitHub get_file_content requires file_path.")
            result = github_get_file_content(
                secret,
                owner,
                repo_name,
                file_path,
                ref=str(config.get("ref") or branch or "main").strip() or "main",
            )
            return {
                "summary": "Connector action completed: github.get_file_content.",
                "result_data": {
                    "connector_action": {
                        "connector": connector_id,
                        "credential_id": credential_id,
                        "action_id": action_id,
                        "owner": owner,
                        "repo": repo_name,
                        "path": file_path,
                        "result": _json_safe(result),
                    }
                },
            }

        if action_id == "list_commits":
            result = github_list_commits(secret, owner, repo_name, limit=int(config.get("limit") or 10))
            return {
                "summary": "Connector action completed: github.list_commits.",
                "result_data": {
                    "connector_action": {
                        "connector": connector_id,
                        "credential_id": credential_id,
                        "action_id": action_id,
                        "owner": owner,
                        "repo": repo_name,
                        "result": _json_safe(result),
                    }
                },
            }

        title = str(config.get("title") or "").strip()
        target_identity = _workflow_target_identity(
            action_id,
            owner=owner,
            repo=repo_name,
            issue_number=config.get("issue_number"),
            path=file_path or None,
            branch=branch,
            title=title or None,
        )

        def _perform_github() -> Dict[str, Any]:
            if action_id == "create_issue":
                if not title:
                    raise RuntimeError("GitHub create_issue requires title.")
                result = github_create_issue(
                    secret,
                    owner,
                    repo_name,
                    title,
                    str(config.get("body") or body_text or ""),
                    labels=[str(item).strip() for item in (config.get("labels") if isinstance(config.get("labels"), list) else []) if str(item).strip()] or None,
                )
            elif action_id == "comment_on_issue":
                issue_number = int(config.get("issue_number") or 0)
                if issue_number <= 0:
                    raise RuntimeError("GitHub comment_on_issue requires issue_number.")
                result = github_comment_on_issue(
                    secret,
                    owner,
                    repo_name,
                    issue_number,
                    str(config.get("body") or body_text or ""),
                )
            elif action_id == "create_pull_request":
                head = str(config.get("head") or "").strip()
                base = str(config.get("base") or "").strip()
                if not title or not head or not base:
                    raise RuntimeError("GitHub create_pull_request requires title, head, and base.")
                result = github_create_pull_request(
                    secret,
                    owner,
                    repo_name,
                    title,
                    str(config.get("body") or body_text or ""),
                    head,
                    base,
                )
            else:
                message = str(config.get("message") or title or "").strip()
                content = str(config.get("content") or body_text or "")
                if not file_path or not message:
                    raise RuntimeError("GitHub create_or_update_file requires file_path and message.")
                result = github_create_or_update_file(
                    secret,
                    owner,
                    repo_name,
                    file_path,
                    message,
                    content,
                    sha=str(config.get("sha") or "").strip() or None,
                    branch=branch,
                )
            return {
                "summary": f"Connector action completed: github.{action_id}.",
                "result_data": {
                    "connector_action": {
                        "connector": connector_id,
                        "credential_id": credential_id,
                        "action_id": action_id,
                        "owner": owner,
                        "repo": repo_name,
                        "path": file_path or None,
                        "result": _json_safe(result),
                    }
                },
            }

        payload = {
            "connector": connector_id,
            "action_id": action_id,
            "owner": owner,
            "repo": repo_name,
            "title": title or None,
            "body": str(config.get("body") or body_text or "") or None,
            "issue_number": config.get("issue_number"),
            "path": file_path or None,
            "branch": branch,
        }
        return execute_external_write_once(
            run_id=run_id,
            context=context,
            step_key=f"workflow_node:{node_id}",
            category="workflow_connector_action",
            operation={"connector": connector_id, "action_id": action_id, "owner": owner, "repo": repo_name},
            target_system=connector_id,
            account_identity=connector_account_identity,
            target_identity=target_identity,
            payload=payload,
            execute=_perform_github,
        )

    if connector_id == "dropbox" and action_id in {
        "list_folder",
        "upload_file",
        "download_file",
        "delete",
        "move",
        "get_shared_link",
        "search",
    }:
        local_path = str(config.get("local_path") or config.get("path") or config.get("file_path") or "").strip()
        dropbox_path = str(config.get("dropbox_path") or config.get("remote_path") or "").strip()
        from_path = str(config.get("from_path") or "").strip()
        to_path = str(config.get("to_path") or "").strip()
        folder_path = str(config.get("folder_path") or config.get("path") or "/").strip() or "/"
        query = str(config.get("query") or _workflow_tool_text_input(config, current_text) or "").strip()

        if action_id == "list_folder":
            result = dropbox_list_folder(secret, folder_path)
            return {
                "summary": "Connector action completed: dropbox.list_folder.",
                "result_data": {
                    "connector_action": {
                        "connector": connector_id,
                        "credential_id": credential_id,
                        "action_id": action_id,
                        "path": folder_path,
                        "result": _json_safe(result),
                    }
                },
            }

        if action_id == "search":
            result = dropbox_search(secret, query)
            return {
                "summary": "Connector action completed: dropbox.search.",
                "result_data": {
                    "connector_action": {
                        "connector": connector_id,
                        "credential_id": credential_id,
                        "action_id": action_id,
                        "query": query,
                        "result": _json_safe(result),
                    }
                },
            }

        if action_id == "get_shared_link":
            target_path = dropbox_path or folder_path
            if not target_path:
                raise RuntimeError("Dropbox get_shared_link requires dropbox_path.")
            result = dropbox_get_shared_link(secret, target_path)
            return {
                "summary": "Connector action completed: dropbox.get_shared_link.",
                "result_data": {
                    "connector_action": {
                        "connector": connector_id,
                        "credential_id": credential_id,
                        "action_id": action_id,
                        "path": target_path,
                        "result": _json_safe(result),
                    }
                },
            }

        target_identity = _workflow_target_identity(
            action_id,
            path=dropbox_path or folder_path or from_path or None,
            file_path=local_path or None,
        )

        def _perform_dropbox() -> Dict[str, Any]:
            if action_id == "upload_file":
                if not local_path or not dropbox_path:
                    raise RuntimeError("Dropbox upload_file requires local_path and dropbox_path.")
                result = dropbox_upload_file(
                    secret,
                    local_path,
                    dropbox_path,
                    overwrite=bool(config.get("overwrite", True)),
                )
            elif action_id == "download_file":
                if not local_path or not dropbox_path:
                    raise RuntimeError("Dropbox download_file requires dropbox_path and local_path.")
                result = dropbox_download_file(secret, dropbox_path, local_path)
            elif action_id == "delete":
                target_path = dropbox_path or folder_path
                if not target_path:
                    raise RuntimeError("Dropbox delete requires path.")
                result = dropbox_delete(secret, target_path)
            else:
                if not from_path or not to_path:
                    raise RuntimeError("Dropbox move requires from_path and to_path.")
                result = dropbox_move(secret, from_path, to_path)
            return {
                "summary": f"Connector action completed: dropbox.{action_id}.",
                "result_data": {
                    "connector_action": {
                        "connector": connector_id,
                        "credential_id": credential_id,
                        "action_id": action_id,
                        "path": dropbox_path or folder_path or from_path or None,
                        "local_path": local_path or None,
                        "result": _json_safe(result),
                    }
                },
            }

        payload = {
            "connector": connector_id,
            "action_id": action_id,
            "dropbox_path": dropbox_path or folder_path or None,
            "local_path": local_path or None,
            "from_path": from_path or None,
            "to_path": to_path or None,
        }
        return execute_external_write_once(
            run_id=run_id,
            context=context,
            step_key=f"workflow_node:{node_id}",
            category="workflow_connector_action",
            operation={"connector": connector_id, "action_id": action_id, "path": dropbox_path or folder_path or None},
            target_system=connector_id,
            account_identity=connector_account_identity,
            target_identity=target_identity,
            payload=payload,
            execute=_perform_dropbox,
        )

    if connector_id == "s3" and action_id in {
        "list_buckets",
        "list_objects",
        "upload_file",
        "download_file",
        "delete_object",
        "get_presigned_url",
        "create_bucket",
    }:
        bucket = str(config.get("bucket") or "").strip()
        key = str(config.get("key") or config.get("path") or "").strip()
        local_path = str(config.get("local_path") or config.get("file_path") or "").strip()

        if action_id == "list_buckets":
            result = s3_list_buckets(secret)
            return {
                "summary": "Connector action completed: s3.list_buckets.",
                "result_data": {
                    "connector_action": {
                        "connector": connector_id,
                        "credential_id": credential_id,
                        "action_id": action_id,
                        "result": _json_safe(result),
                    }
                },
            }

        if action_id == "list_objects":
            if not bucket:
                raise RuntimeError("S3 list_objects requires bucket.")
            result = s3_list_objects(
                secret,
                bucket,
                prefix=str(config.get("prefix") or "").strip(),
                limit=int(config.get("limit") or 100),
            )
            return {
                "summary": "Connector action completed: s3.list_objects.",
                "result_data": {
                    "connector_action": {
                        "connector": connector_id,
                        "credential_id": credential_id,
                        "action_id": action_id,
                        "bucket": bucket,
                        "result": _json_safe(result),
                    }
                },
            }

        if action_id == "get_presigned_url":
            if not bucket or not key:
                raise RuntimeError("S3 get_presigned_url requires bucket and key.")
            result = s3_get_presigned_url(
                secret,
                bucket,
                key,
                expires_in=int(config.get("expires_in") or 3600),
            )
            return {
                "summary": "Connector action completed: s3.get_presigned_url.",
                "result_data": {
                    "connector_action": {
                        "connector": connector_id,
                        "credential_id": credential_id,
                        "action_id": action_id,
                        "bucket": bucket,
                        "key": key,
                        "result": _json_safe(result),
                    }
                },
            }

        target_identity = _workflow_target_identity(
            action_id,
            bucket=bucket or None,
            key=key or None,
            file_path=local_path or None,
        )

        def _perform_s3() -> Dict[str, Any]:
            if action_id == "upload_file":
                if not bucket or not key or not local_path:
                    raise RuntimeError("S3 upload_file requires bucket, key, and local_path.")
                result = s3_upload_file(secret, local_path, bucket, key)
            elif action_id == "download_file":
                if not bucket or not key or not local_path:
                    raise RuntimeError("S3 download_file requires bucket, key, and local_path.")
                result = s3_download_file(secret, bucket, key, local_path)
            elif action_id == "delete_object":
                if not bucket or not key:
                    raise RuntimeError("S3 delete_object requires bucket and key.")
                result = s3_delete_object(secret, bucket, key)
            else:
                if not bucket:
                    raise RuntimeError("S3 create_bucket requires bucket.")
                result = s3_create_bucket(
                    secret,
                    bucket,
                    region=str(config.get("region") or "").strip() or None,
                )
            return {
                "summary": f"Connector action completed: s3.{action_id}.",
                "result_data": {
                    "connector_action": {
                        "connector": connector_id,
                        "credential_id": credential_id,
                        "action_id": action_id,
                        "bucket": bucket or None,
                        "key": key or None,
                        "local_path": local_path or None,
                        "result": _json_safe(result),
                    }
                },
            }

        payload = {
            "connector": connector_id,
            "action_id": action_id,
            "bucket": bucket or None,
            "key": key or None,
            "local_path": local_path or None,
        }
        return execute_external_write_once(
            run_id=run_id,
            context=context,
            step_key=f"workflow_node:{node_id}",
            category="workflow_connector_action",
            operation={"connector": connector_id, "action_id": action_id, "bucket": bucket or None, "key": key or None},
            target_system=connector_id,
            account_identity=connector_account_identity,
            target_identity=target_identity,
            payload=payload,
            execute=_perform_s3,
        )

    if connector_id == "notion" and action_id in {
        "search",
        "get_page",
        "create_page",
        "update_page",
        "append_blocks",
        "query_database",
        "create_database_item",
    }:
        page_id = str(config.get("page_id") or "").strip()
        parent_id = str(config.get("parent_id") or config.get("database_id") or page_id or "").strip()
        database_id = str(config.get("database_id") or "").strip()
        body_text = _workflow_tool_text_input(config, current_text)

        if action_id == "search":
            result = notion_search(
                secret,
                str(config.get("query") or body_text or "").strip(),
                filter_type=str(config.get("filter_type") or "").strip() or None,
            )
            return {
                "summary": "Connector action completed: notion.search.",
                "result_data": {
                    "connector_action": {
                        "connector": connector_id,
                        "credential_id": credential_id,
                        "action_id": action_id,
                        "result": _json_safe(result),
                    }
                },
            }

        if action_id == "get_page":
            if not page_id:
                raise RuntimeError("Notion get_page requires page_id.")
            result = notion_get_page(secret, page_id)
            return {
                "summary": "Connector action completed: notion.get_page.",
                "result_data": {
                    "connector_action": {
                        "connector": connector_id,
                        "credential_id": credential_id,
                        "action_id": action_id,
                        "page_id": page_id,
                        "result": _json_safe(result),
                    }
                },
            }

        if action_id == "query_database":
            if not database_id:
                raise RuntimeError("Notion query_database requires database_id.")
            result = notion_query_database(
                secret,
                database_id,
                filter=config.get("filter") if isinstance(config.get("filter"), dict) else None,
                sorts=config.get("sorts") if isinstance(config.get("sorts"), list) else None,
            )
            return {
                "summary": "Connector action completed: notion.query_database.",
                "result_data": {
                    "connector_action": {
                        "connector": connector_id,
                        "credential_id": credential_id,
                        "action_id": action_id,
                        "database_id": database_id,
                        "result": _json_safe(result),
                    }
                },
            }

        title = str(config.get("title") or "").strip()
        target_identity = _workflow_target_identity(
            action_id,
            page_id=page_id or None,
            parent_id=parent_id or None,
            database_id=database_id or None,
            title=title or None,
        )

        def _perform_notion() -> Dict[str, Any]:
            if action_id == "create_page":
                if not parent_id or not title:
                    raise RuntimeError("Notion create_page requires parent_id and title.")
                result = notion_create_page(
                    secret,
                    parent_id,
                    title,
                    [item for item in (config.get("content_blocks") if isinstance(config.get("content_blocks"), list) else []) if isinstance(item, dict)],
                    parent_type=str(config.get("parent_type") or "page_id").strip() or "page_id",
                    title_property_name=str(config.get("title_property_name") or "title").strip() or "title",
                )
            elif action_id == "update_page":
                if not page_id:
                    raise RuntimeError("Notion update_page requires page_id.")
                result = notion_update_page(
                    secret,
                    page_id,
                    config.get("properties") if isinstance(config.get("properties"), dict) else {},
                    archived=config.get("archived") if isinstance(config.get("archived"), bool) else None,
                )
            elif action_id == "append_blocks":
                if not page_id:
                    raise RuntimeError("Notion append_blocks requires page_id.")
                result = notion_append_blocks(
                    secret,
                    page_id,
                    [item for item in (config.get("blocks") if isinstance(config.get("blocks"), list) else []) if isinstance(item, dict)],
                )
            else:
                if not database_id:
                    raise RuntimeError("Notion create_database_item requires database_id.")
                result = notion_create_database_item(
                    secret,
                    database_id,
                    config.get("properties") if isinstance(config.get("properties"), dict) else {},
                    content_blocks=[item for item in (config.get("content_blocks") if isinstance(config.get("content_blocks"), list) else []) if isinstance(item, dict)] or None,
                )
            return {
                "summary": f"Connector action completed: notion.{action_id}.",
                "result_data": {
                    "connector_action": {
                        "connector": connector_id,
                        "credential_id": credential_id,
                        "action_id": action_id,
                        "page_id": page_id or None,
                        "database_id": database_id or None,
                        "result": _json_safe(result),
                    }
                },
            }

        payload = {
            "connector": connector_id,
            "action_id": action_id,
            "page_id": page_id or None,
            "parent_id": parent_id or None,
            "database_id": database_id or None,
            "title": title or None,
        }
        return execute_external_write_once(
            run_id=run_id,
            context=context,
            step_key=f"workflow_node:{node_id}",
            category="workflow_connector_action",
            operation={"connector": connector_id, "action_id": action_id, "page_id": page_id or None, "database_id": database_id or None},
            target_system=connector_id,
            account_identity=connector_account_identity,
            target_identity=target_identity,
            payload=payload,
            execute=_perform_notion,
        )

    if connector_id == "linear" and action_id in {
        "list_teams",
        "list_issues",
        "get_issue",
        "create_issue",
        "update_issue",
        "list_projects",
        "add_comment",
    }:
        team_id = str(config.get("team_id") or "").strip()
        issue_id = str(config.get("issue_id") or "").strip()
        body_text = _workflow_tool_text_input(config, current_text)

        if action_id == "list_teams":
            result = linear_list_teams(secret)
            return {
                "summary": "Connector action completed: linear.list_teams.",
                "result_data": {
                    "connector_action": {
                        "connector": connector_id,
                        "credential_id": credential_id,
                        "action_id": action_id,
                        "result": _json_safe(result),
                    }
                },
            }

        if action_id == "list_issues":
            if not team_id:
                raise RuntimeError("Linear list_issues requires team_id.")
            result = linear_list_issues(
                secret,
                team_id,
                state=str(config.get("state") or "").strip() or None,
                assignee=str(config.get("assignee") or config.get("assignee_id") or "").strip() or None,
                limit=int(config.get("limit") or 20),
            )
            return {
                "summary": "Connector action completed: linear.list_issues.",
                "result_data": {
                    "connector_action": {
                        "connector": connector_id,
                        "credential_id": credential_id,
                        "action_id": action_id,
                        "team_id": team_id,
                        "result": _json_safe(result),
                    }
                },
            }

        if action_id == "get_issue":
            if not issue_id:
                raise RuntimeError("Linear get_issue requires issue_id.")
            result = linear_get_issue(secret, issue_id)
            return {
                "summary": "Connector action completed: linear.get_issue.",
                "result_data": {
                    "connector_action": {
                        "connector": connector_id,
                        "credential_id": credential_id,
                        "action_id": action_id,
                        "issue_id": issue_id,
                        "result": _json_safe(result),
                    }
                },
            }

        if action_id == "list_projects":
            if not team_id:
                raise RuntimeError("Linear list_projects requires team_id.")
            result = linear_list_projects(secret, team_id)
            return {
                "summary": "Connector action completed: linear.list_projects.",
                "result_data": {
                    "connector_action": {
                        "connector": connector_id,
                        "credential_id": credential_id,
                        "action_id": action_id,
                        "team_id": team_id,
                        "result": _json_safe(result),
                    }
                },
            }

        title = str(config.get("title") or "").strip()
        target_identity = _workflow_target_identity(
            action_id,
            team_id=team_id or None,
            issue_id=issue_id or None,
            title=title or None,
        )

        def _perform_linear() -> Dict[str, Any]:
            if action_id == "create_issue":
                if not team_id or not title:
                    raise RuntimeError("Linear create_issue requires team_id and title.")
                result = linear_create_issue(
                    secret,
                    team_id,
                    title,
                    str(config.get("description") or body_text or ""),
                    priority=int(config.get("priority")) if config.get("priority") not in {None, ""} else None,
                    assignee_id=str(config.get("assignee_id") or "").strip() or None,
                )
            elif action_id == "update_issue":
                if not issue_id:
                    raise RuntimeError("Linear update_issue requires issue_id.")
                result = linear_update_issue(
                    secret,
                    issue_id,
                    state=str(config.get("state") or "").strip() or None,
                    priority=int(config.get("priority")) if config.get("priority") not in {None, ""} else None,
                    assignee_id=str(config.get("assignee_id") or "").strip() or None,
                )
            else:
                if not issue_id:
                    raise RuntimeError("Linear add_comment requires issue_id.")
                result = linear_add_comment(
                    secret,
                    issue_id,
                    str(config.get("body") or body_text or ""),
                )
            return {
                "summary": f"Connector action completed: linear.{action_id}.",
                "result_data": {
                    "connector_action": {
                        "connector": connector_id,
                        "credential_id": credential_id,
                        "action_id": action_id,
                        "team_id": team_id or None,
                        "issue_id": issue_id or None,
                        "result": _json_safe(result),
                    }
                },
            }

        payload = {
            "connector": connector_id,
            "action_id": action_id,
            "team_id": team_id or None,
            "issue_id": issue_id or None,
            "title": title or None,
        }
        return execute_external_write_once(
            run_id=run_id,
            context=context,
            step_key=f"workflow_node:{node_id}",
            category="workflow_connector_action",
            operation={"connector": connector_id, "action_id": action_id, "team_id": team_id or None, "issue_id": issue_id or None},
            target_system=connector_id,
            account_identity=connector_account_identity,
            target_identity=target_identity,
            payload=payload,
            execute=_perform_linear,
        )

    if connector_id == "whatsapp_twilio" and action_id == "send_message":
        account_sid = str(secret.get("account_sid") or "").strip()
        auth_token = str(secret.get("auth_token") or "").strip()
        from_number = _workflow_whatsapp_number(config.get("from_number") or secret.get("from_number"))
        to_number = _workflow_whatsapp_number(
            config.get("to_number")
            or config.get("recipient")
            or secret.get("to_number")
        )
        if not account_sid or not auth_token:
            raise RuntimeError("WhatsApp (Twilio) connector action requires account_sid and auth_token.")
        if not from_number or not to_number:
            raise RuntimeError("WhatsApp (Twilio) connector action requires from_number and to_number.")
        basic = base64.b64encode(f"{account_sid}:{auth_token}".encode("utf-8")).decode("ascii")
        payload = urlencode(
            {
                "From": from_number,
                "To": to_number,
                "Body": _workflow_tool_text_input(config, current_text),
            }
        ).encode("utf-8")
        def _perform_whatsapp() -> Dict[str, Any]:
            response = http_json_request(
                f"https://api.twilio.com/2010-04-01/Accounts/{quote_plus(account_sid)}/Messages.json",
                method="POST",
                headers={
                    "Authorization": f"Basic {basic}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                payload=payload,
            )
            status_code = int(response.get("status") or 500)
            if status_code not in {200, 201}:
                body = response.get("json") if isinstance(response.get("json"), dict) else {}
                detail = str(body.get("message") or response.get("text") or "").strip()
                raise RuntimeError(detail or f"Twilio send failed with status {status_code}.")
            result = response.get("json") if isinstance(response.get("json"), dict) else response
            return {
                "summary": f"Connector action completed: whatsapp_twilio.{action_id}.",
                "result_data": {
                    "connector_action": {
                        "connector": connector_id,
                        "credential_id": credential_id,
                        "action_id": action_id,
                        "to_number": to_number,
                        "result": _json_safe(result),
                    }
                },
            }

        return execute_external_write_once(
            run_id=run_id,
            context=context,
            step_key=f"workflow_node:{node_id}",
            category="workflow_connector_action",
            operation={"connector": connector_id, "action_id": action_id, "to_number": to_number},
            target_system=connector_id,
            account_identity=connector_account_identity,
            target_identity=_workflow_target_identity(action_id, to_number=to_number, from_number=from_number),
            payload={"connector": connector_id, "action_id": action_id, "to_number": to_number, "from_number": from_number, "body": _workflow_tool_text_input(config, current_text)},
            execute=_perform_whatsapp,
        )

    if connector_id == "wechat_work" and action_id == "send_message":
        webhook_url = str(config.get("webhook_url") or secret.get("webhook_url") or "").strip()
        if not webhook_url:
            raise RuntimeError("WeChat Work connector action requires webhook_url.")
        body_text = _workflow_tool_text_input(config, current_text)
        payload = config.get("payload") if isinstance(config.get("payload"), dict) else {
            "msgtype": "text",
            "text": {
                "content": body_text,
            },
        }
        def _perform_wechat() -> Dict[str, Any]:
            response = http_json_request(
                webhook_url,
                method="POST",
                headers={"Content-Type": "application/json"},
                payload=payload,
            )
            status_code = int(response.get("status") or 500)
            body = response.get("json") if isinstance(response.get("json"), dict) else {}
            if status_code != 200:
                detail = str(body.get("errmsg") or body.get("message") or response.get("text") or "").strip()
                raise RuntimeError(detail or f"WeChat Work send failed with status {status_code}.")
            if int(body.get("errcode") or 0) != 0:
                detail = str(body.get("errmsg") or "").strip()
                raise RuntimeError(detail or "WeChat Work webhook was rejected.")
            return {
                "summary": f"Connector action completed: wechat_work.{action_id}.",
                "result_data": {
                    "connector_action": {
                        "connector": connector_id,
                        "credential_id": credential_id,
                        "action_id": action_id,
                        "result": _json_safe(body or response),
                    }
                },
            }

        return execute_external_write_once(
            run_id=run_id,
            context=context,
            step_key=f"workflow_node:{node_id}",
            category="workflow_connector_action",
            operation={"connector": connector_id, "action_id": action_id, "webhook_url": webhook_url},
            target_system=connector_id,
            account_identity=connector_account_identity,
            target_identity=_workflow_target_identity(
                action_id,
                webhook_fingerprint=stable_value_fingerprint(webhook_url),
            ),
            payload={"connector": connector_id, "action_id": action_id, "webhook_url": webhook_url, "payload": _json_safe(payload)},
            execute=_perform_wechat,
        )

    if connector_id == "instagram_business" and action_id == "publish_reply":
        comment_id = str(config.get("comment_id") or config.get("media_comment_id") or "").strip()
        if not comment_id:
            raise RuntimeError("Instagram Business publish_reply requires comment_id.")
        payload = config.get("payload") if isinstance(config.get("payload"), dict) else {
            "message": _workflow_tool_text_input(config, current_text),
        }
        def _perform_instagram_reply() -> Dict[str, Any]:
            response = http_json_request(
                f"https://graph.facebook.com/v23.0/{quote_plus(comment_id)}/replies",
                method="POST",
                headers=_workflow_tool_connector_headers(secret),
                payload=payload,
            )
            status_code = int(response.get("status") or 500)
            body = response.get("json") if isinstance(response.get("json"), dict) else {}
            if status_code not in {200, 201}:
                error_obj = body.get("error") if isinstance(body, dict) else {}
                detail = (
                    str(error_obj.get("message") or "").strip()
                    if isinstance(error_obj, dict)
                    else ""
                ) or str(body.get("message") or response.get("text") or "").strip()
                raise RuntimeError(detail or f"Instagram publish_reply failed with status {status_code}.")
            return {
                "summary": f"Connector action completed: instagram_business.{action_id}.",
                "result_data": {
                    "connector_action": {
                        "connector": connector_id,
                        "credential_id": credential_id,
                        "action_id": action_id,
                        "comment_id": comment_id,
                        "result": _json_safe(body or response),
                    }
                },
            }

        return execute_external_write_once(
            run_id=run_id,
            context=context,
            step_key=f"workflow_node:{node_id}",
            category="workflow_connector_action",
            operation={"connector": connector_id, "action_id": action_id, "comment_id": comment_id},
            target_system=connector_id,
            account_identity=connector_account_identity,
            target_identity=_workflow_target_identity(action_id, comment_id=comment_id),
            payload={"connector": connector_id, "action_id": action_id, "comment_id": comment_id, "payload": _json_safe(payload)},
            execute=_perform_instagram_reply,
        )

    if connector_id == "instagram_business" and action_id == "send_dm":
        page_id = str(config.get("page_id") or secret.get("page_id") or "").strip()
        recipient_id = str(
            config.get("recipient_id")
            or config.get("recipient")
            or config.get("user_id")
            or config.get("instagram_user_id")
            or ""
        ).strip()
        if not page_id:
            raise RuntimeError("Instagram Business send_dm requires page_id from config or binding.")
        if not recipient_id:
            raise RuntimeError("Instagram Business send_dm requires recipient_id.")
        payload = config.get("payload") if isinstance(config.get("payload"), dict) else {
            "messaging_product": "instagram",
            "recipient": {"id": recipient_id},
            "message": {"text": _workflow_tool_text_input(config, current_text)},
        }
        if isinstance(payload, dict):
            payload.setdefault("messaging_product", "instagram")
        def _perform_instagram_dm() -> Dict[str, Any]:
            response = http_json_request(
                f"https://graph.facebook.com/v23.0/{quote_plus(page_id)}/messages",
                method="POST",
                headers=_workflow_tool_connector_headers(secret),
                payload=payload,
            )
            status_code = int(response.get("status") or 500)
            body = response.get("json") if isinstance(response.get("json"), dict) else {}
            if status_code not in {200, 201}:
                error_obj = body.get("error") if isinstance(body, dict) else {}
                detail = (
                    str(error_obj.get("message") or "").strip()
                    if isinstance(error_obj, dict)
                    else ""
                ) or str(body.get("message") or response.get("text") or "").strip()
                raise RuntimeError(detail or f"Instagram send_dm failed with status {status_code}.")
            return {
                "summary": f"Connector action completed: instagram_business.{action_id}.",
                "result_data": {
                    "connector_action": {
                        "connector": connector_id,
                        "credential_id": credential_id,
                        "action_id": action_id,
                        "page_id": page_id,
                        "recipient_id": recipient_id,
                        "result": _json_safe(body or response),
                    }
                },
            }

        return execute_external_write_once(
            run_id=run_id,
            context=context,
            step_key=f"workflow_node:{node_id}",
            category="workflow_connector_action",
            operation={"connector": connector_id, "action_id": action_id, "page_id": page_id, "recipient_id": recipient_id},
            target_system=connector_id,
            account_identity=connector_account_identity,
            target_identity=_workflow_target_identity(action_id, page_id=page_id, recipient_id=recipient_id),
            payload={"connector": connector_id, "action_id": action_id, "page_id": page_id, "recipient_id": recipient_id, "payload": _json_safe(payload)},
            execute=_perform_instagram_dm,
        )

    if connector_id == "google_workspace" and action_id == "fetch_emails":
        result = _sanitize_recent_email_tool_results(common.list_recent_connector_messages(
            secret,
            limit=int(config.get("limit") or 10),
        ))
        return {
            "summary": "Connector action completed: google_workspace.fetch_emails.",
            "result_data": {
                "connector_action": {
                    "connector": connector_id,
                    "credential_id": credential_id,
                    "action_id": action_id,
                    "result": _json_safe(result),
                }
            },
        }

    if connector_id == "google_workspace" and action_id == "list_calendar_events":
        calendar_id = str(config.get("calendar_id") or "primary").strip() or "primary"
        now = datetime.now(timezone.utc)
        time_min = str(config.get("time_min") or now.isoformat().replace("+00:00", "Z")).strip()
        time_max = str(config.get("time_max") or (now + timedelta(days=1)).isoformat().replace("+00:00", "Z")).strip()
        safe_top = max(1, min(int(config.get("top") or config.get("limit") or 10), 20))
        if google_workspace_uses_local_cli(secret):
            raw_result = google_workspace_local_list_calendar_events(
                secret,
                calendar_id=calendar_id,
                time_min=time_min,
                time_max=time_max,
                top=safe_top,
            )
        else:
            params = {
                "timeMin": time_min,
                "timeMax": time_max,
                "maxResults": safe_top,
                "singleEvents": "true",
                "orderBy": "startTime",
            }
            response = http_json_request(
                f"https://www.googleapis.com/calendar/v3/calendars/{quote_plus(calendar_id)}/events?{urlencode(params)}",
                headers=_workflow_tool_connector_headers(secret),
                payload=None,
                timeout=20,
            )
            body = response.get("json") if isinstance(response.get("json"), dict) else {}
            raw_result = {
                "calendar_id": calendar_id,
                "time_min": time_min,
                "time_max": time_max,
                "events": body.get("items") if isinstance(body.get("items"), list) else [],
            }
        result = _sanitize_calendar_event_tool_results(raw_result)
        return {
            "summary": "Connector action completed: google_workspace.list_calendar_events.",
            "result_data": {
                "connector_action": {
                    "connector": connector_id,
                    "credential_id": credential_id,
                    "action_id": action_id,
                    "calendar_id": calendar_id,
                    "time_min": time_min,
                    "time_max": time_max,
                    "result": _json_safe(result),
                }
            },
        }

    if connector_id == "google_workspace" and action_id == "list_drive_files":
        path = str(config.get("path") or "gdrive:/").strip() or "gdrive:/"
        safe_top = max(1, min(int(config.get("top") or config.get("limit") or 20), 50))
        if google_workspace_uses_local_cli(secret):
            raw_result = google_workspace_local_list_drive_children(secret, path=path, top=safe_top)
        else:
            if path not in {"", "/", "gdrive:/", "gdrive://", "drive:/"}:
                raise RuntimeError("Google Drive OAuth listing currently supports the root folder path only.")
            params = {
                "pageSize": safe_top,
                "fields": "files(id,name,mimeType,webViewLink,modifiedTime)",
                "q": "trashed = false and 'root' in parents",
                "orderBy": "folder,name",
            }
            response = http_json_request(
                f"https://www.googleapis.com/drive/v3/files?{urlencode(params)}",
                headers=_workflow_tool_connector_headers(secret),
                payload=None,
                timeout=20,
            )
            body = response.get("json") if isinstance(response.get("json"), dict) else {}
            raw_result = {"path": "gdrive:/", "items": body.get("files") if isinstance(body.get("files"), list) else []}
        result = _sanitize_drive_file_tool_results(raw_result)
        return {
            "summary": "Connector action completed: google_workspace.list_drive_files.",
            "result_data": {
                "connector_action": {
                    "connector": connector_id,
                    "credential_id": credential_id,
                    "action_id": action_id,
                    "path": path,
                    "result": _json_safe(result),
                }
            },
        }

    if connector_id in {"google_workspace", "microsoft_365"} and action_id in {"send_email", "send_message", "draft_email"}:
        to_email = str(
            config.get("to_email")
            or config.get("to")
            or config.get("email")
            or config.get("recipient")
            or ""
        ).strip()
        if not to_email:
            raise RuntimeError(f"Connector action '{action_id}' requires a recipient email.")
        if not run_service.is_valid_email_recipient(to_email):
            raise RuntimeError(f"Connector action '{action_id}' requires a valid recipient email.")
        subject = str(config.get("subject") or f"Empyralist workflow: {context.get('workflow_name') or context.get('workflow_id') or 'Untitled'}").strip()
        body_text = _workflow_tool_text_input(config, current_text)
        cc_email = str(config.get("cc_email") or config.get("cc") or "").strip()
        def _perform_mail_write() -> Dict[str, Any]:
            if connector_id == "google_workspace":
                if google_workspace_uses_local_cli(secret):
                    result = (
                        google_workspace_local_create_draft(secret, to_email, subject, body_text, cc_email=cc_email)
                        if action_id == "draft_email"
                        else google_workspace_local_send_message(secret, to_email, subject, body_text, cc_email=cc_email)
                    )
                else:
                    message = (
                        f"To: {to_email}\r\n"
                        + (f"Cc: {cc_email}\r\n" if cc_email else "")
                        + f"Subject: {subject}\r\n"
                        "Content-Type: text/plain; charset=UTF-8\r\n"
                        "\r\n"
                        f"{body_text}\r\n"
                    )
                    raw_encoded = base64.urlsafe_b64encode(message.encode("utf-8")).decode("utf-8").rstrip("=")
                    if action_id == "draft_email":
                        payload = {"message": {"raw": raw_encoded}}
                        response = http_json_request(
                            "https://gmail.googleapis.com/gmail/v1/users/me/drafts",
                            method="POST",
                            headers=_workflow_tool_connector_headers(secret),
                            payload=payload,
                        )
                    else:
                        payload = {"raw": raw_encoded}
                        response = http_json_request(
                            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                            method="POST",
                            headers=_workflow_tool_connector_headers(secret),
                            payload=payload,
                        )
                    result = response.get("json") if isinstance(response.get("json"), dict) else response
            else:
                result = (
                    microsoft_365_create_draft(secret, http_json_request, to_email, subject, body_text, cc_email=cc_email)
                    if action_id == "draft_email"
                    else microsoft_365_send_message(secret, http_json_request, to_email, subject, body_text, cc_email=cc_email)
                )
            return {
                "summary": f"Connector action completed: {connector_id}.{action_id}.",
                "result_data": {
                    "connector_action": {
                        "connector": connector_id,
                        "credential_id": credential_id,
                        "action_id": action_id,
                        "recipient": to_email,
                        "subject": subject,
                        "result": _json_safe(result),
                    }
                },
            }

        return execute_external_write_once(
            run_id=run_id,
            context=context,
            step_key=f"workflow_node:{node_id}",
            category="workflow_connector_action",
            operation={"connector": connector_id, "action_id": action_id, "recipient": to_email, "subject": subject},
            target_system=connector_id,
            account_identity=connector_account_identity,
            target_identity=_workflow_target_identity(action_id, recipient=to_email, subject=subject),
            payload={"connector": connector_id, "action_id": action_id, "recipient": to_email, "subject": subject, "body": body_text},
            execute=_perform_mail_write,
        )

    if connector_id in {"google_workspace", "microsoft_365"} and action_id == "create_calendar_event":
        payload = config.get("payload") if isinstance(config.get("payload"), dict) else {}
        if not payload:
            start = str(config.get("start") or "").strip()
            end = str(config.get("end") or "").strip()
            event_timezone = str(config.get("timezone") or "UTC").strip() or "UTC"
            if not start or not end:
                raise RuntimeError("create_calendar_event requires payload or start/end values.")
            title = str(config.get("title") or "Empyralist workflow event").strip() or "Empyralist workflow event"
            description = str(config.get("description") or current_text or "").strip()
            attendee_emails = [
                str(addr or "").strip()
                for addr in (config.get("attendees") or [])
                if str(addr or "").strip()
            ]
            if connector_id == "microsoft_365":
                payload = {
                    "subject": title,
                    "body": {"contentType": "Text", "content": description},
                    "start": {"dateTime": start, "timeZone": event_timezone},
                    "end": {"dateTime": end, "timeZone": event_timezone},
                }
                if attendee_emails:
                    payload["attendees"] = [
                        {"emailAddress": {"address": addr}, "type": "required"}
                        for addr in attendee_emails
                    ]
            else:
                payload = {
                    "summary": title,
                    "description": description,
                    "start": {"dateTime": start, "timeZone": event_timezone},
                    "end": {"dateTime": end, "timeZone": event_timezone},
                }
                if attendee_emails:
                    payload["attendees"] = [{"email": addr} for addr in attendee_emails]
        calendar_id = str(config.get("calendar_id") or "primary").strip() or "primary"

        def _perform_calendar_write() -> Dict[str, Any]:
            if connector_id == "microsoft_365":
                result = microsoft_365_create_calendar_event(secret, http_json_request, payload=payload)
            else:
                if google_workspace_uses_local_cli(secret):
                    result = google_workspace_local_create_calendar_event(
                        secret,
                        calendar_id=calendar_id,
                        send_updates="none",
                        payload=payload,
                    )
                else:
                    url = (
                        f"https://www.googleapis.com/calendar/v3/calendars/{quote_plus(calendar_id)}"
                        f"/events?sendUpdates=none"
                    )
                    response = http_json_request(
                        url,
                        method="POST",
                        headers=_workflow_tool_connector_headers(secret),
                        payload=payload,
                    )
                    result = response.get("json") if isinstance(response.get("json"), dict) else response
            return {
                "summary": f"Connector action completed: {connector_id}.create_calendar_event.",
                "result_data": {
                    "connector_action": {
                        "connector": connector_id,
                        "credential_id": credential_id,
                        "action_id": action_id,
                        "calendar_id": calendar_id if connector_id == "google_workspace" else None,
                        "result": _json_safe(result),
                    }
                },
            }

        return execute_external_write_once(
            run_id=run_id,
            context=context,
            step_key=f"workflow_node:{node_id}",
            category="workflow_connector_action",
            operation={"connector": connector_id, "action_id": action_id, "calendar_id": calendar_id if connector_id == "google_workspace" else None},
            target_system=connector_id,
            account_identity=connector_account_identity,
            target_identity=_workflow_target_identity(
                action_id,
                calendar_id=calendar_id if connector_id == "google_workspace" else None,
                payload_fingerprint=stable_value_fingerprint(payload),
            ),
            payload={"connector": connector_id, "action_id": action_id, "calendar_id": calendar_id if connector_id == "google_workspace" else None, "payload": _json_safe(payload)},
            execute=_perform_calendar_write,
        )

    if connector_id == "microsoft_365" and action_id == "upload_drive_file":
        drive_path = str(config.get("path") or config.get("file_path") or "").strip()
        if not drive_path:
            raise RuntimeError("upload_drive_file requires path or file_path.")
        content_value = config.get("content")
        if content_value is None:
            content_value = current_text
        if isinstance(content_value, (dict, list)):
            content_bytes = json.dumps(content_value).encode("utf-8")
            content_type = "application/json"
        elif isinstance(content_value, (bytes, bytearray)):
            content_bytes = bytes(content_value)
            content_type = str(config.get("content_type") or "application/octet-stream").strip() or "application/octet-stream"
        else:
            content_bytes = str(content_value or "").encode("utf-8")
            content_type = str(config.get("content_type") or "text/plain; charset=utf-8").strip() or "text/plain; charset=utf-8"
        def _perform_upload() -> Dict[str, Any]:
            result = microsoft_365_upload_drive_file(
                secret,
                drive_path,
                content_bytes,
                content_type=content_type,
            )
            return {
                "summary": f"Connector action completed: microsoft_365.upload_drive_file.",
                "result_data": {
                    "connector_action": {
                        "connector": connector_id,
                        "credential_id": credential_id,
                        "action_id": action_id,
                        "path": drive_path,
                        "content_type": content_type,
                        "result": _json_safe(result),
                    }
                },
            }

        return execute_external_write_once(
            run_id=run_id,
            context=context,
            step_key=f"workflow_node:{node_id}",
            category="workflow_connector_action",
            operation={"connector": connector_id, "action_id": action_id, "path": drive_path},
            target_system=connector_id,
            account_identity=connector_account_identity,
            target_identity=_workflow_target_identity(action_id, path=drive_path),
            payload={"connector": connector_id, "action_id": action_id, "path": drive_path, "content_type": content_type, "content_sha256": hashlib.sha256(content_bytes).hexdigest()},
            execute=_perform_upload,
        )

    if connector_id == "google_workspace" and action_id in {"create_doc", "create_document"}:
        title = str(config.get("title") or "Empyralist Document").strip() or "Empyralist Document"
        intent_token = str(config.get("intent_token") or config.get("client_action_id") or "").strip() or None
        def _perform_create_doc() -> Dict[str, Any]:
            result = google_workspace_create_document(secret, title)
            return {
                "summary": f"Connector action completed: google_workspace.{action_id}.",
                "result_data": {
                    "connector_action": {
                        "connector": connector_id,
                        "credential_id": credential_id,
                        "action_id": action_id,
                        "title": title,
                        "result": _json_safe(result),
                    }
                },
            }

        return execute_external_write_once(
            run_id=run_id,
            context=context,
            step_key=f"workflow_node:{node_id}",
            category="workflow_connector_action",
            operation={"connector": connector_id, "action_id": action_id, "title": title},
            target_system=connector_id,
            account_identity=connector_account_identity,
            target_identity=_workflow_target_identity(action_id, title=title, intent_token=intent_token),
            payload={"connector": connector_id, "action_id": action_id, "title": title},
            execute=_perform_create_doc,
        )

    if connector_id == "google_workspace" and action_id in {"create_sheet", "create_spreadsheet"}:
        title = str(config.get("title") or "Empyralist Sheet").strip() or "Empyralist Sheet"
        intent_token = str(config.get("intent_token") or config.get("client_action_id") or "").strip() or None
        def _perform_create_sheet() -> Dict[str, Any]:
            result = google_workspace_create_spreadsheet(secret, title)
            return {
                "summary": f"Connector action completed: google_workspace.{action_id}.",
                "result_data": {
                    "connector_action": {
                        "connector": connector_id,
                        "credential_id": credential_id,
                        "action_id": action_id,
                        "title": title,
                        "result": _json_safe(result),
                    }
                },
            }

        return execute_external_write_once(
            run_id=run_id,
            context=context,
            step_key=f"workflow_node:{node_id}",
            category="workflow_connector_action",
            operation={"connector": connector_id, "action_id": action_id, "title": title},
            target_system=connector_id,
            account_identity=connector_account_identity,
            target_identity=_workflow_target_identity(action_id, title=title, intent_token=intent_token),
            payload={"connector": connector_id, "action_id": action_id, "title": title},
            execute=_perform_create_sheet,
        )

    raise RuntimeError(f"Connector action '{connector_id}.{action_id}' is not executable in the Orion graph runtime yet.")


def _workflow_execute_document_or_spreadsheet_tool(
    context: Dict[str, Any],
    config: Dict[str, Any],
    *,
    variant: str,
) -> Dict[str, Any]:
    metadata = dict(context.get("metadata") if isinstance(context.get("metadata"), dict) else {})
    pack_inputs = {
        "file_path": config.get("file_path") or config.get("path"),
        "operation": config.get("operation") or ("create" if variant == "document" else "read"),
        "title": config.get("title"),
        "payload": config.get("payload"),
        "rows": config.get("rows"),
        "values": config.get("values"),
        "row_index": config.get("row_index"),
        "sheet_name": config.get("sheet_name"),
        "row_limit": config.get("row_limit"),
        "overwrite": config.get("overwrite"),
    }
    connector_credential_id = str(
        config.get("binding_id")
        or config.get("connector_credential_id")
        or config.get("credential_id")
        or metadata.get("connector_credential_id")
        or ""
    ).strip()
    if connector_credential_id:
        metadata["connector_credential_id"] = connector_credential_id
    metadata["pack_inputs"] = {key: value for key, value in pack_inputs.items() if value is not None}
    pack_id = DOCUMENT_STUDIO_PACK_ID if variant == "document" else SPREADSHEET_OPS_PACK_ID
    raw_result = execute_outcome_pack(pack_id, {"metadata": metadata}, run_id=context.get("run_id"))
    result_data = normalize_pack_result(pack_id, raw_result)
    summary = str(result_data.get("summary") or f"{variant.title()} tool completed.").strip()
    return {
        "summary": summary,
        "result_data": {
            "pack_id": pack_id,
            "tool_variant": variant,
            **_json_safe(result_data),
        },
    }


def _workflow_execute_local_tool(
    run_id: str,
    context: Dict[str, Any],
    config: Dict[str, Any],
    *,
    label: str,
    variant: str,
    current_text: str,
    on_waiting_for_input: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    on_resumed: Optional[Callable[[str, Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    _assert_direct_chat_tool_execution_allowed(context)
    return run_service.execute_workflow_local_tool(
        run_id=run_id,
        context=context,
        config=config,
        label=label,
        variant=variant,
        current_text=current_text,
        normalize_execution_target_fn=normalize_execution_target,
        assert_file_mount_access_fn=assert_file_mount_access,
        browser_automation_policy_from_operations_fn=_browser_automation_policy_from_operations,
        normalize_action_id_fn=normalize_action_id,
        workflow_text_payload_fn=_workflow_text_payload,
        json_safe_fn=_json_safe,
        execute_workflow_child_run_request_fn=_execute_workflow_child_run_request,
        local_execution_pack_id=LOCAL_EXECUTION_PACK_ID,
        execution_target_local_companion=EXECUTION_TARGET_LOCAL_COMPANION,
        execution_target_cloud=EXECUTION_TARGET_CLOUD,
        trust_mode_auto=TRUST_MODE_AUTO,
        browser_auth_actions=_BROWSER_AUTH_ACTIONS,
        on_waiting_for_input=on_waiting_for_input,
        on_resumed=on_resumed,
        load_run_fn=runs.get,
    )


def _workflow_final_result_data(
    workflow_definition: Dict[str, Any],
    *,
    final_node_id: Optional[str],
    final_text: str,
    final_data: Optional[Dict[str, Any]],
    run_id: str,
    workflow_id: Optional[str] = None,
    workflow_version_id: Optional[str] = None,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "summary": final_text,
        "workflow_execution": {
            "workflow_id": str(workflow_id or "").strip() or None,
            "workflow_version_id": str(workflow_version_id or "").strip() or None,
            "schema_version": str(workflow_definition.get("version") or "").strip() or None,
            "node_count": len(workflow_definition.get("nodes") or []),
            "edge_count": len(workflow_definition.get("edges") or []),
            "final_node_id": final_node_id,
            "run_id": run_id,
            "final_data": _json_safe(final_data) if isinstance(final_data, dict) and final_data else None,
        },
    }
    if isinstance(final_data, dict) and final_data:
        result["last_node_data"] = _json_safe(final_data)
    return result


def _execute_workflow_graph(
    run_id: str,
    context: Dict[str, Any],
    log_queue: queue.Queue,
    workflow_definition: Dict[str, Any],
    *,
    _state: Optional[Dict[str, Any]] = None,
    _track_node_states: bool = True,
    _loop_depth: int = 0,
) -> Dict[str, Any]:
    nodes = workflow_definition.get("nodes") if isinstance(workflow_definition.get("nodes"), list) else []
    edges = workflow_definition.get("edges") if isinstance(workflow_definition.get("edges"), list) else []
    if not nodes:
        raise RuntimeError("Workflow definition has no nodes.")

    node_map: Dict[str, Dict[str, Any]] = {
        str(node.get("id") or "").strip(): node
        for node in nodes
        if isinstance(node, dict) and str(node.get("id") or "").strip()
    }
    if not node_map:
        raise RuntimeError("Workflow definition has no usable node ids.")
    if _track_node_states:
        _ensure_run_node_states(run_id, graph_kind="workflow", nodes=nodes)
    update_node_state = _update_run_node_state if _track_node_states else _workflow_noop_node_state_update

    trigger_nodes = [
        node for node in nodes if isinstance(node, dict) and str(node.get("type") or "").strip().lower() == "trigger"
    ]
    current_node = None
    if trigger_nodes:
        non_manual = [node for node in trigger_nodes if str(node.get("variant") or "").strip().lower() != "manual"]
        current_node = (non_manual or trigger_nodes)[0]
    else:
        current_node = nodes[0] if isinstance(nodes[0], dict) else None

    if isinstance(_state, dict):
        state = _state
        current_text = _workflow_text_payload(state.get("last_text") or "")
    else:
        current_text = (
            _workflow_text_payload(context.get("business_plan"))
            or _workflow_text_payload(context.get("user_goal"))
            or "Workflow run started."
        )
        state = {
            "last_text": current_text,
            "last_data": None,
            "active_provider": str(context.get("provider") or "openai").strip() or "openai",
            "active_model": str(context.get("model") or CODEX_MODEL).strip() or CODEX_MODEL,
        }
    final_node_id: Optional[str] = None
    safety_counter = 0

    while current_node and safety_counter < 100:
        _workflow_raise_if_cancelled(run_id)
        safety_counter += 1
        node_id = str(current_node.get("id") or "").strip()
        node_type = str(current_node.get("type") or "").strip().lower()
        variant = str(current_node.get("variant") or "").strip().lower()
        config = current_node.get("config") if isinstance(current_node.get("config"), dict) else {}
        label = _workflow_label(current_node)
        final_node_id = node_id or final_node_id
        update_node_state(
            run_id,
            node_id,
            status="running",
            activate=True,
            label=label,
            node_type=node_type,
            variant=variant,
            input_preview=_node_preview_text(current_text),
            summary=f"Executing {label}",
            reset_started=True,
        )
        emit_log(log_queue, "info", f"Workflow node: {label}", event="workflow_node_start", data={"node_id": node_id, "type": node_type, "variant": variant})

        next_handle: Optional[str] = None
        try:
            if node_type == "trigger":
                emit_log(log_queue, "info", f"Trigger active: {label}", event="workflow_trigger", data={"variant": variant or "manual", "node_id": node_id})
                update_node_state(
                    run_id,
                    node_id,
                    status="succeeded",
                    finalize=True,
                    output_preview=_node_preview_text(current_text),
                    summary=f"Trigger active: {variant or 'manual'}",
                    detail={"node_id": node_id, "variant": variant or "manual"},
                )

            elif node_type == "agent":
                fast_extract = _workflow_agent_fast_extract(
                    label=label,
                    config=config,
                    current_text=current_text,
                    state=state,
                )
                if fast_extract is not None:
                    text = str(fast_extract.get("text") or "").strip()
                    current_text = text
                    state["last_text"] = text
                    state["last_data"] = {
                        "node_id": node_id,
                        "node_type": node_type,
                        "variant": variant,
                        "text": text,
                        "output": fast_extract.get("value"),
                        "field_path": fast_extract.get("field_path"),
                        "extracted_without_llm": True,
                    }
                    emit_log(
                        log_queue,
                        "info",
                        text,
                        event="workflow_agent_output",
                        data={"node_id": node_id, "field_path": fast_extract.get("field_path"), "extracted_without_llm": True},
                    )
                    update_node_state(
                        run_id,
                        node_id,
                        status="succeeded",
                        finalize=True,
                        output_preview=_node_preview_text(text),
                        summary=f"Agent completed: {label}",
                        detail={"field_path": fast_extract.get("field_path"), "extracted_without_llm": True},
                    )
                else:
                    execution_context, agent_state = _resolve_agent_generation_state(context, config)
                    system_prompt = _build_workflow_agent_system_prompt(config)
                    user_input = (
                        f"Workflow context:\n{current_text or 'No previous node output.'}\n\n"
                        f"Current workflow node: {label}\n"
                        "Produce the result for this node only."
                    )
                    text = generate_with_candidate_failover(agent_state, execution_context, log_queue, system_prompt, user_input)
                    current_text = text
                    state["last_text"] = text
                    state["last_data"] = {
                        "node_id": node_id,
                        "node_type": node_type,
                        "variant": variant,
                        "text": text,
                    }
                    state["active_provider"] = str(agent_state.get("active_provider") or agent_state.get("provider") or state.get("active_provider") or "openai")
                    state["active_model"] = str(agent_state.get("active_model") or agent_state.get("selected_model") or state.get("active_model") or CODEX_MODEL)
                    emit_log(log_queue, "info", text, event="workflow_agent_output", data={"node_id": node_id})
                    update_node_state(
                        run_id,
                        node_id,
                        status="succeeded",
                        finalize=True,
                        output_preview=_node_preview_text(text),
                        summary=f"Agent completed: {label}",
                        detail={
                            "provider": state.get("active_provider"),
                            "model": state.get("active_model"),
                        },
                    )

            elif node_type == "decision":
                expression = str(config.get("expression") or "").strip() or "False"
                try:
                    decision = _workflow_decision_value(current_text, state, expression)
                except Exception as exc:
                    raise RuntimeError(f"Decision node '{label}' failed to evaluate expression: {exc}") from exc
                next_handle = "true" if decision else "false"
                state["last_data"] = {
                    "node_id": node_id,
                    "node_type": node_type,
                    "variant": variant,
                    "decision": bool(decision),
                    "expression": expression,
                }
                current_text = f"{label}: {'true' if decision else 'false'}"
                state["last_text"] = current_text
                emit_log(log_queue, "info", current_text, event="workflow_decision", data={"node_id": node_id, "decision": bool(decision), "expression": expression})
                update_node_state(
                    run_id,
                    node_id,
                    status="succeeded",
                    finalize=True,
                    output_preview=_node_preview_text(current_text),
                    summary=f"Decision: {'true' if decision else 'false'}",
                    detail={"decision": bool(decision), "expression": expression},
                )

            elif node_type == "human":
                current_text = run_service.execute_workflow_human_node(
                    run_id=run_id,
                    node_id=node_id,
                    label=label,
                    variant=variant,
                    config=config,
                    current_text=current_text,
                    state=state,
                    context=context,
                    log_queue=log_queue,
                    update_node_state_fn=update_node_state,
                    wait_for_human_response_fn=wait_for_human_response,
                    agent_machine_full_trust_for_context_fn=_agent_machine_full_trust_for_context,
                    node_preview_text_fn=_node_preview_text,
                    json_safe_fn=_json_safe,
                    emit_log_fn=emit_log,
                )

            elif node_type == "data":
                template = str(config.get("template") or "").strip()
                mapping = str(config.get("mapping") or "").strip()
                summary = template or mapping or label
                current_text = summary if not current_text else f"{summary}\n\n{current_text}"
                state["last_text"] = current_text
                state["last_data"] = {
                    "node_id": node_id,
                    "node_type": node_type,
                    "variant": variant,
                    "summary": summary,
                }
                emit_log(log_queue, "info", f"Data step: {summary}", event="workflow_data_step", data={"node_id": node_id, "variant": variant})
                update_node_state(
                    run_id,
                    node_id,
                    status="succeeded",
                    finalize=True,
                    output_preview=_node_preview_text(current_text),
                    summary=summary,
                    detail={"variant": variant or "transform"},
                )

            elif node_type == "subflow":
                current_text = run_service.execute_workflow_subflow_node(
                    run_id=run_id,
                    node_id=node_id,
                    label=label,
                    context=context,
                    config=config,
                    current_text=current_text,
                    state=state,
                    update_node_state_fn=update_node_state,
                    emit_log_fn=emit_log,
                    workflow_text_payload_fn=_workflow_text_payload,
                    node_preview_text_fn=_node_preview_text,
                    execute_workflow_child_run_request_fn=_execute_workflow_child_run_request,
                    load_run_fn=runs.get,
                    log_queue=log_queue,
                    execution_target_local_companion=EXECUTION_TARGET_LOCAL_COMPANION,
                )

            elif node_type == "loop":
                loop_result = _workflow_execute_loop_node(
                    run_id,
                    node_id,
                    label,
                    variant or "for_each",
                    config,
                    context=context,
                    log_queue=log_queue,
                    state=state,
                    update_node_state=update_node_state,
                    loop_depth=_loop_depth,
                )
                current_text = _workflow_text_payload(loop_result.get("current_text") or current_text)
                state["last_text"] = current_text
                state["last_data"] = loop_result.get("last_data")
                state["active_provider"] = str(loop_result.get("active_provider") or state.get("active_provider") or context.get("provider") or "openai")
                state["active_model"] = str(loop_result.get("active_model") or state.get("active_model") or context.get("model") or CODEX_MODEL)
                emit_log(log_queue, "info", current_text or f"Loop completed: {label}", event="workflow_loop_complete", data={"node_id": node_id, "variant": variant or "for_each"})
                update_node_state(
                    run_id,
                    node_id,
                    status="succeeded",
                    finalize=True,
                    output_preview=_node_preview_text(current_text),
                    summary=str(loop_result.get("summary") or f"Loop completed: {label}"),
                    detail=loop_result.get("detail"),
                )

            elif node_type == "tool":
                execution_target = str(
                    config.get("execution_target")
                    or context.get("metadata", {}).get("execution_target_selected")
                    or context.get("metadata", {}).get("execution_target")
                    or "auto"
                ).strip() or "auto"
                normalized_execution_target = normalize_execution_target(execution_target)
                if variant in {"shell", "browser", "code"} and normalized_execution_target == EXECUTION_TARGET_CLOUD:
                    raise RuntimeError(f"{variant.title()} tool nodes cannot target cloud directly; use local_companion or auto.")
                permissions = config.get("permissions") if isinstance(config.get("permissions"), dict) else {}
                policy_metadata = map_builder_permissions_to_runtime_metadata(permissions, execution_target=execution_target)
                if variant == "browser":
                    browser_policy = _browser_automation_policy_from_operations(
                        [
                            {
                                "mode": config.get("mode"),
                                "session_profile": config.get("session_profile"),
                                "browser_actions": config.get("browser_actions"),
                            }
                        ]
                    )
                    if browser_policy:
                        policy_metadata["browser_automation_policy"] = browser_policy
                capability_ids = []
                if str(config.get("capability") or "").strip():
                    capability_ids = [str(config.get("capability") or "").strip()]
                tool_id = _workflow_tool_policy_tool_id(variant, config)
                if tool_id == "shell.execute":
                    raw_command = str(config.get("command") or "").strip()
                    raw_argv = list(config.get("argv") or []) if isinstance(config.get("argv"), list) else None
                    if raw_command:
                        policy_metadata["raw_shell_command"] = raw_command
                    if raw_argv:
                        policy_metadata["raw_shell_argv"] = raw_argv
                approval_metadata = (
                    _connector_tool_node_approval_metadata(
                        node_id=node_id,
                        label=label,
                        tool_id=tool_id or variant,
                        variant=variant,
                        config=config if isinstance(config, dict) else {},
                        evaluation={},
                    )
                    if variant == "connector_action"
                    else None
                )
                tool_approval_provided = False
                decision = "allow"
                if variant == "connector_action":
                    _validate_connector_action_request(
                        connector_id=str(config.get("connector") or "").strip(),
                        action_id=str(config.get("action_id") or "").strip(),
                        config=config if isinstance(config, dict) else {},
                    )
                if tool_id:
                    evaluation = policy_service.evaluate_tool_policy_decision(
                        tool_id=tool_id,
                        trust_mode=str(policy_metadata.get("trust_mode") or "guarded"),
                        target=str(policy_metadata.get("execution_target") or execution_target or "auto"),
                        metadata=policy_metadata,
                        capability_ids=capability_ids,
                    )
                    evaluation = _apply_agent_machine_bypass_to_tool_policy_evaluation(evaluation, context=context)
                    _append_run_tool_policy_audit(
                        run_id,
                        evaluation,
                        source="workflow_tool_node",
                        metadata={"node_id": node_id, "variant": variant},
                    )
                    decision = str(
                        evaluation.get("execution_decision")
                        or evaluation.get("decision")
                        or ""
                    ).strip().lower()
                    if decision == "deny" or decision == "blocked":
                        security_audit_service.emit_security_audit_event(
                            action="tool_policy.rejected",
                            status="blocked",
                            tenant_id=_workflow_tool_tenant_id(context),
                            workspace_id=_workflow_tool_workspace_id(context),
                            run_id=str(run_id or "").strip() or None,
                            detail=f"Tool node '{label}' blocked by runtime policy.",
                            metadata={
                                "node_id": str(node_id or "").strip() or None,
                                "tool_id": str(tool_id or "").strip() or None,
                                "variant": str(variant or "").strip() or None,
                                "decision": decision,
                                "reason": str(evaluation.get("reason") or "").strip() or None,
                                "target": str(evaluation.get("target") or "").strip() or None,
                                "policy_mode": str(evaluation.get("policy_mode") or "").strip() or None,
                                "classification": _json_safe(evaluation.get("classification") or {}),
                            },
                            idempotency_key=f"tool_policy.rejected:{run_id}:{node_id}:{tool_id}:{decision}",
                        )
                        raise RuntimeError(f"Tool node '{label}' is blocked by runtime policy.")
                    if decision == "require_confirmation" or decision == "approval_required":
                        approval_metadata = _connector_tool_node_approval_metadata(
                            node_id=node_id,
                            label=label,
                            tool_id=tool_id or variant,
                            variant=variant,
                            config=config if isinstance(config, dict) else {},
                            evaluation=evaluation if isinstance(evaluation, dict) else {},
                        )
                        resolved_marker = run_service.resolved_approval_marker_for_metadata(context, approval_metadata)
                        if isinstance(resolved_marker, dict):
                            emit_log(
                                log_queue,
                                "info",
                                "Reused persisted approval for connector action.",
                                event="approval_reused",
                                data={
                                    "node_id": node_id,
                                    "approval_id": str(resolved_marker.get("approval_id") or "").strip() or None,
                                    "connector": approval_metadata.get("connector"),
                                    "connector_action": approval_metadata.get("connector_action"),
                                },
                            )
                            update_node_state(
                                run_id,
                                node_id,
                                status="running",
                                activate=True,
                                summary=f"Executing approved tool: {label}",
                                detail={
                                    "tool_id": tool_id,
                                    "variant": variant,
                                    "evaluation": evaluation,
                                    "approval_reused": True,
                                    "approval_id": str(resolved_marker.get("approval_id") or "").strip() or None,
                                },
                                waiting_for_approval=False,
                            )
                            decision = "allow"
                            tool_approval_provided = True
                        else:
                            update_node_state(
                                run_id,
                                node_id,
                                status="waiting_human",
                                summary=f"Confirmation required before {label}",
                                detail={"tool_id": tool_id, "variant": variant, "evaluation": evaluation},
                                waiting_for_approval=True,
                            )
                            approval_prompt = (
                                f"Tool node '{label}' requires confirmation before execution. "
                                "Reply with Proceed to continue or Hold to stop."
                            )
                            approved = wait_for_human_decision(
                                run_id,
                                approval_prompt,
                                source="workflow_tool_node",
                                metadata=approval_metadata,
                            )
                            if not approved:
                                raise RuntimeError(f"Workflow stopped before tool node '{label}'.")
                            tool_approval_provided = True
                            update_node_state(
                                run_id,
                                node_id,
                                status="running",
                                activate=True,
                                summary=f"Executing approved tool: {label}",
                                waiting_for_approval=False,
                            )

                tool_signature_payload = {
                    "variant": variant,
                    "tool_id": tool_id or variant,
                    "config": _json_safe(config),
                    "current_text": current_text,
                }
                if record_run_tool_signature(run_id, str(tool_id or label or variant), tool_signature_payload):
                    emit_log(
                        log_queue,
                        "error",
                        RUN_TOOL_LOOP_REPLY,
                        event="workflow_tool_loop_detected",
                        data={"node_id": node_id, "tool_id": tool_id or variant, "variant": variant},
                    )
                    update_node_state(
                        run_id,
                        node_id,
                        status="failed",
                        finalize=True,
                        output_preview=RUN_TOOL_LOOP_REPLY,
                        summary="Tool loop detected",
                        detail={"tool_id": tool_id or variant, "variant": variant},
                    )
                    raise RuntimeError(RUN_TOOL_LOOP_REPLY)

                if variant == "http":
                    method = str(config.get("method") or "GET").strip().upper() or "GET"
                    url = str(config.get("url") or "").strip()
                    if not url:
                        raise RuntimeError(f"HTTP tool node '{label}' requires a URL.")
                    assert_safe_outbound_url(url)
                    payload = None if method == "GET" else {"context": current_text}
                    response = http_json_request(url, method=method, payload=payload, timeout=30)
                    if int(response.get("status") or 500) >= 400:
                        raise RuntimeError(f"HTTP tool node '{label}' failed with status {int(response.get('status') or 500)}.")
                    tool_output = response.get("json") if response.get("json") is not None else response.get("text")
                    current_text = _workflow_text_payload(tool_output)
                    state["last_text"] = current_text
                    state["last_data"] = {
                        "node_id": node_id,
                        "node_type": node_type,
                        "variant": variant,
                        "tool_id": tool_id or "http",
                        "http": {
                            "status": int(response.get("status") or 0),
                            "headers": _json_safe(response.get("headers") or {}),
                            "text": str(response.get("text") or ""),
                            "json": _json_safe(response.get("json")) if response.get("json") is not None else None,
                            "url": url,
                            "method": method,
                        },
                        "output": _json_safe(tool_output),
                    }
                    emit_log(log_queue, "info", f"HTTP tool completed: {label}", event="workflow_tool_http", data={"node_id": node_id, "url": url, "method": method})
                    update_node_state(
                        run_id,
                        node_id,
                        status="succeeded",
                        finalize=True,
                        output_preview=_node_preview_text(current_text),
                        summary=f"HTTP tool completed: {label}",
                        detail={"tool_id": tool_id or "http", "url": url, "method": method},
                    )
                elif variant == "connector_action":
                    requested_connector_for_meter = str(config.get("connector") or "").strip().lower() or "connector"
                    action_id_for_meter = normalize_action_id(config.get("action_id")) or "action"
                    action_type_for_meter = (
                        "read"
                        if action_id_for_meter.startswith(("get_", "list_", "read_", "fetch_", "search_", "download_"))
                        or action_id_for_meter in {"get", "list", "read", "fetch", "search", "download"}
                        else "write"
                    )
                    connector_meter_event = {
                        "tenant_id": _workflow_tool_tenant_id(context) or "default",
                        "workspace_id": _workflow_tool_workspace_id(context) or "default",
                        "surface": "studio",
                        "source_surface": "workflow_connector_action",
                        "action_domain": "connector",
                        "action_type": action_type_for_meter,
                        "action_name": f"{requested_connector_for_meter}.{action_id_for_meter}",
                        "tool_kind": "workflow_connector_action",
                        "tool_id": tool_id or "connector_action",
                        "connector_id": requested_connector_for_meter,
                        "payer": "platform_credits",
                        "billing_mode": "transparency",
                        "run_id": run_id,
                        "agent_id": str(context.get("agent_id") or context.get("deployed_agent_id") or "").strip() or None,
                        "input_summary": label,
                        "source_table": "workflow_connector_actions",
                        "source_event_id": agent_action_metering_service.build_source_event_id(
                            source_surface="workflow_connector_action",
                            run_id=run_id,
                            node_id=node_id,
                            action_name=f"{requested_connector_for_meter}.{action_id_for_meter}",
                        ),
                        "metadata": {"node_id": node_id, "variant": variant},
                    }
                    _enforce_execution_runtime_decision(
                        run_id,
                        operation="connector_action",
                        context=context,
                        workflow_node_id=node_id,
                        connector_id=requested_connector_for_meter,
                        connector_action=action_id_for_meter,
                        idempotency_key=connector_meter_event["source_event_id"],
                        policy_decision=decision or "allow",
                        approval_required=decision in {"require_confirmation", "approval_required"},
                        approval_provided=tool_approval_provided,
                        external_write_requested=action_type_for_meter == "write",
                    )
                    _enforce_execution_runtime_decision(
                        run_id,
                        operation="usage_metering",
                        context=context,
                        workflow_node_id=node_id,
                        connector_id=requested_connector_for_meter,
                        connector_action=action_id_for_meter,
                        idempotency_key=connector_meter_event["source_event_id"],
                        usage_unit_count=1,
                        metering_enabled=True,
                    )
                    agent_action_metering_service.record_started_sync(**connector_meter_event)
                    try:
                        tool_result = _workflow_execute_connector_action(
                            run_id,
                            node_id,
                            context,
                            config,
                            current_text=current_text,
                        )
                    except RuntimeError as error:
                        if not _workflow_is_retryable_connector_outage(error):
                            agent_action_metering_service.record_failed_sync(
                                **connector_meter_event,
                                error_code=type(error).__name__,
                                output_summary=str(error),
                            )
                            raise
                        detail = str(error or "").strip() or "Connector temporarily unavailable."
                        _workflow_emit_connector_reliability_event(
                            run_id=run_id,
                            context=context,
                            connector_id=requested_connector_for_meter,
                            action_id=action_id_for_meter,
                            node_id=node_id,
                            payload={
                                "reason": detail,
                                "retryable_outage": True,
                            },
                        )
                        tool_result = _workflow_connector_degraded_result(
                            connector_id=requested_connector_for_meter,
                            credential_id=None,
                            action_id=action_id_for_meter,
                            reason=detail,
                            retryable_outage=True,
                        )
                    connector_action = (
                        tool_result.get("result_data", {}).get("connector_action")
                        if isinstance(tool_result.get("result_data"), dict)
                        else {}
                    )
                    degraded = bool((connector_action or {}).get("degraded"))
                    connector_meter_completed = {
                        **connector_meter_event,
                        "metadata": {
                            **connector_meter_event["metadata"],
                            "degraded": degraded,
                            "credential_id": (connector_action or {}).get("credential_id"),
                        },
                    }
                    agent_action_metering_service.record_completed_sync(
                        **connector_meter_completed,
                        output_summary=tool_result.get("summary") or current_text,
                    )
                    current_text = _workflow_text_payload(tool_result.get("summary") or current_text)
                    state["last_text"] = current_text
                    state["last_data"] = {
                        "node_id": node_id,
                        "node_type": node_type,
                        "variant": variant,
                        "tool_id": tool_id or "connector_action",
                        **(_json_safe(tool_result.get("result_data")) if isinstance(tool_result.get("result_data"), dict) else {}),
                    }
                    emit_log(
                        log_queue,
                        "warning" if degraded else "info",
                        current_text,
                        event="workflow_tool_connector_action",
                        data={"node_id": node_id, "tool_id": tool_id, "degraded": degraded},
                    )
                    update_node_state(
                        run_id,
                        node_id,
                        status="succeeded",
                        finalize=True,
                        output_preview=_node_preview_text(current_text),
                        summary=current_text if degraded else f"Connector action completed: {label}",
                        detail={
                            "tool_id": tool_id or "connector_action",
                            "result": tool_result.get("result_data"),
                            "degraded": degraded,
                        },
                    )
                elif variant in {"document", "spreadsheet"}:
                    tool_result = _workflow_execute_document_or_spreadsheet_tool(
                        context,
                        config,
                        variant=variant,
                    )
                    current_text = _workflow_text_payload(tool_result.get("summary") or current_text)
                    state["last_text"] = current_text
                    state["last_data"] = {
                        "node_id": node_id,
                        "node_type": node_type,
                        "variant": variant,
                        "tool_id": tool_id or variant,
                        **(_json_safe(tool_result.get("result_data")) if isinstance(tool_result.get("result_data"), dict) else {}),
                    }
                    emit_log(log_queue, "info", current_text, event=f"workflow_tool_{variant}", data={"node_id": node_id, "tool_id": tool_id})
                    update_node_state(
                        run_id,
                        node_id,
                        status="succeeded",
                        finalize=True,
                        output_preview=_node_preview_text(current_text),
                        summary=f"{variant.title()} tool completed: {label}",
                        detail={"tool_id": tool_id or variant, "result": tool_result.get("result_data")},
                    )
                elif variant in {"file", "shell", "browser", "code"}:
                    tool_result = run_service.execute_workflow_local_tool(
                        run_id=run_id,
                        context=context,
                        config=config,
                        label=label,
                        variant=variant,
                        current_text=current_text,
                        normalize_execution_target_fn=normalize_execution_target,
                        assert_file_mount_access_fn=assert_file_mount_access,
                        browser_automation_policy_from_operations_fn=_browser_automation_policy_from_operations,
                        normalize_action_id_fn=normalize_action_id,
                        workflow_text_payload_fn=_workflow_text_payload,
                        json_safe_fn=_json_safe,
                        execute_workflow_child_run_request_fn=_execute_workflow_child_run_request,
                        local_execution_pack_id=LOCAL_EXECUTION_PACK_ID,
                        execution_target_local_companion=EXECUTION_TARGET_LOCAL_COMPANION,
                        execution_target_cloud=EXECUTION_TARGET_CLOUD,
                        trust_mode_auto=TRUST_MODE_AUTO,
                        browser_auth_actions=_BROWSER_AUTH_ACTIONS,
                        on_waiting_for_input=lambda active_child_run_id, child_run: update_node_state(
                            run_id,
                            node_id,
                            status="waiting_human",
                            summary=f"Local tool waiting for input: {label}",
                            detail={
                                "tool_id": tool_id or variant,
                                "variant": variant,
                                "child_status": "waiting_for_input",
                                "child_pending_approval_id": str(
                                    ((child_run.get("pending_approval") or {}) if isinstance(child_run, dict) else {}).get("approval_id") or ""
                                ).strip() or None,
                            },
                            child_run_id=active_child_run_id,
                            waiting_for_approval=True,
                        ),
                        on_resumed=lambda active_child_run_id, _child_run: update_node_state(
                            run_id,
                            node_id,
                            status="running",
                            activate=True,
                            summary=f"Local tool resumed: {label}",
                            child_run_id=active_child_run_id,
                            waiting_for_approval=False,
                        ),
                        load_run_fn=runs.get,
                    )
                    current_text = _workflow_text_payload(tool_result.get("summary") or current_text)
                    state["last_text"] = current_text
                    state["last_data"] = {
                        "node_id": node_id,
                        "node_type": node_type,
                        "variant": variant,
                        "tool_id": tool_id or variant,
                        **(_json_safe(tool_result.get("result_data")) if isinstance(tool_result.get("result_data"), dict) else {}),
                    }
                    emit_log(log_queue, "info", current_text, event=f"workflow_tool_{variant}", data={"node_id": node_id, "tool_id": tool_id})
                    update_node_state(
                        run_id,
                        node_id,
                        status="succeeded",
                        finalize=True,
                        output_preview=_node_preview_text(current_text),
                        summary=f"{variant.title()} tool completed: {label}",
                        detail={"tool_id": tool_id or variant, "result": tool_result.get("result_data")},
                    )
                else:
                    raise RuntimeError(f"Tool variant '{variant or 'unknown'}' is not executable in the Orion graph runtime yet.")

            else:
                raise RuntimeError(f"Unsupported workflow node type '{node_type}'.")
        except Exception as exc:
            update_node_state(
                run_id,
                node_id,
                status="failed",
                finalize=True,
                error=_node_preview_text(friendly_runtime_error_message(exc), limit=400),
                summary=f"Failed: {label}",
                detail={"node_id": node_id, "type": node_type, "variant": variant},
                waiting_for_approval=False,
            )
            raise

        next_node_id = _workflow_next_node_id(edges, node_id, preferred_handle=next_handle)
        current_node = node_map.get(next_node_id) if next_node_id else None

    if safety_counter >= 100:
        raise RuntimeError("Workflow graph exceeded the maximum node execution limit.")

    final_text = state.get("last_text") if isinstance(state.get("last_text"), str) else current_text
    final_data = state.get("last_data") if isinstance(state.get("last_data"), dict) else None
    if not _track_node_states:
        return {
            "result_text": final_text,
            "result_data": _json_safe(final_data) if final_data is not None else None,
            "usage_masked": None,
            "active_profile_id": None,
            "active_provider": str(state.get("active_provider") or context.get("provider") or "openai"),
            "active_model": str(state.get("active_model") or context.get("model") or CODEX_MODEL),
            "active_adapter": None,
            "final_node_id": final_node_id,
            "state": state,
        }
    usage = build_masked_usage(
        str(state.get("active_provider") or context.get("provider") or "openai"),
        str(state.get("active_model") or context.get("model") or CODEX_MODEL),
        f"{_workflow_text_payload(context.get('user_goal'))}\n\n{_workflow_text_payload(context.get('business_plan'))}",
        final_text,
    )
    return {
        "result_text": final_text,
        "result_data": _workflow_final_result_data(
            workflow_definition,
            final_node_id=final_node_id,
            final_text=final_text,
            final_data=final_data,
            run_id=run_id,
            workflow_id=str(context.get("workflow_id") or "").strip() or None,
            workflow_version_id=str(context.get("workflow_version_id") or "").strip() or None,
        ),
        "usage_masked": usage,
        "active_profile_id": None,
        "active_provider": str(state.get("active_provider") or context.get("provider") or "openai"),
        "active_model": str(state.get("active_model") or context.get("model") or CODEX_MODEL),
        "active_adapter": None,
    }

def _compile_orion_dag(context: Dict[str, Any]) -> Dict[str, Any]:
    workflow_definition = _workflow_definition_from_context(context)
    if isinstance(workflow_definition, dict) and isinstance(workflow_definition.get("nodes"), list) and workflow_definition.get("nodes"):
        return {
            "id": str(context.get("workflow_id") or "workflow-graph"),
            "type": "workflow_graph",
            "workflow_id": context.get("workflow_id"),
            "graph_node_count": len(workflow_definition.get("nodes") or []),
            "nodes": [
                {"id": "workflow.graph", "kind": "workflow_graph_execute", "deps": []},
            ],
        }

    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    outcome_pack = str(metadata.get("outcome_pack") or "").strip().lower()

    if outcome_pack in SUPPORTED_OUTCOME_PACKS:
        nodes = [
            {"id": "pack.prepare", "kind": "pack_prepare", "deps": []},
            {"id": "pack.approval", "kind": "pack_approval", "deps": ["pack.prepare"]},
            {"id": "pack.finalize", "kind": "pack_finalize", "deps": ["pack.approval"]},
        ]
        return {
            "id": f"orion-pack-{outcome_pack}-v1",
            "type": "outcome_pack",
            "outcome_pack": outcome_pack,
            "nodes": nodes,
        }

    nodes = [
        {"id": "runtime.resolve", "kind": "runtime_resolve", "deps": []},
        {"id": "plan.generate", "kind": "plan_generate", "deps": ["runtime.resolve"]},
        {"id": "plan.approval", "kind": "plan_approval", "deps": ["plan.generate"]},
        {"id": "result.generate", "kind": "result_generate", "deps": ["plan.approval"]},
        {"id": "usage.finalize", "kind": "usage_finalize", "deps": ["result.generate"]},
    ]
    return {
        "id": "orion-standard-v1",
        "type": "standard",
        "nodes": nodes,
    }


def _resolve_dag_order(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    node_map: Dict[str, Dict[str, Any]] = {}
    indegree: Dict[str, int] = {}
    adjacency: Dict[str, List[str]] = {}

    for raw_node in nodes:
        node_id = str(raw_node.get("id") or "").strip()
        if not node_id:
            raise RuntimeError("DAG node id is required.")
        if node_id in node_map:
            raise RuntimeError(f"Duplicate DAG node id '{node_id}'.")
        deps_raw = raw_node.get("deps")
        deps: List[str] = []
        if isinstance(deps_raw, list):
            for dep in deps_raw:
                dep_id = str(dep or "").strip()
                if dep_id:
                    deps.append(dep_id)
        node = dict(raw_node)
        node["deps"] = deps
        node_map[node_id] = node
        indegree[node_id] = 0

    for node_id, node in node_map.items():
        deps = node.get("deps", [])
        if not isinstance(deps, list):
            raise RuntimeError(f"DAG node '{node_id}' deps must be a list.")
        for dep_id in deps:
            if dep_id not in node_map:
                raise RuntimeError(f"DAG node '{node_id}' depends on unknown node '{dep_id}'.")
            adjacency.setdefault(dep_id, []).append(node_id)
            indegree[node_id] += 1

    ready = sorted([nid for nid, count in indegree.items() if count == 0])
    ordered_ids: List[str] = []
    while ready:
        current = ready.pop(0)
        ordered_ids.append(current)
        for nxt in sorted(adjacency.get(current, [])):
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                ready.append(nxt)
        ready.sort()

    if len(ordered_ids) != len(node_map):
        raise RuntimeError("DAG contains a cycle or unresolved dependency.")
    return [node_map[nid] for nid in ordered_ids]


def _execute_orion_dag_node(
    run_id: str,
    context: Dict[str, Any],
    log_queue: queue.Queue,
    node: Dict[str, Any],
    state: Dict[str, Any],
):
    kind = str(node.get("kind") or "").strip()
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    user_goal = str(context.get("user_goal") or "Execute the requested business objective.")

    if kind == "workflow_graph_execute":
        workflow_definition = _workflow_definition_from_context(context)
        if not isinstance(workflow_definition, dict):
            raise RuntimeError("Workflow graph execution requested without a workflow definition.")
        result = _execute_workflow_graph(run_id, context, log_queue, workflow_definition)
        emit_log(
            log_queue,
            "info",
            "Workflow graph completed.",
            event="workflow_graph_complete",
            data={
                "workflow_id": context.get("workflow_id"),
                "node_count": len(workflow_definition.get("nodes") or []),
            },
        )
        state["final_result_text"] = result["result_text"]
        state["final_result_data"] = result.get("result_data")
        state["final_usage"] = result["usage_masked"]
        state["active_profile_id"] = result.get("active_profile_id")
        state["active_provider"] = result.get("active_provider")
        state["active_model"] = result.get("active_model")
        state["active_adapter"] = result.get("active_adapter")
        return {"done": True}

    if kind == "pack_prepare":
        outcome_pack = str(state.get("outcome_pack") or "").strip().lower()
        if outcome_pack not in SUPPORTED_OUTCOME_PACKS:
            raise RuntimeError(f"Unsupported outcome pack '{outcome_pack}'.")
        emit_log(log_queue, "info", f"Outcome pack started: {outcome_pack}.", event="pack_start")
        for phase in PACK_PHASES.get(outcome_pack, []):
            emit_log(log_queue, "info", phase, event="pack_phase")
        tool_precheck = _compute_tool_policy_precheck(context)
        state["tool_policy_precheck"] = tool_precheck
        blocked_tools = list(tool_precheck.get("blocked") or [])
        for evaluation in tool_precheck.get("items") if isinstance(tool_precheck.get("items"), list) else []:
            if isinstance(evaluation, dict):
                _append_run_tool_policy_audit(
                    run_id,
                    evaluation,
                    source="dag_precheck",
                    metadata={"node": "pack_prepare", "pack_id": outcome_pack},
                )
        emit_log(
            log_queue,
            "info",
            (
                "Tool policy precheck: "
                f"allow={tool_precheck.get('allow_count', 0)} "
                f"approval={tool_precheck.get('approval_required_count', 0)} "
                f"blocked={tool_precheck.get('blocked_count', 0)}"
            ),
            event="tool_policy_precheck",
            data=tool_precheck,
        )
        if blocked_tools:
            raise RuntimeError(f"Tool policy blocked requested actions: {', '.join(blocked_tools)}.")

        raw_result = execute_outcome_pack(outcome_pack, context, run_id=run_id)
        result_data = normalize_pack_result(outcome_pack, raw_result)
        validate_pack_tool_contracts(outcome_pack, result_data, role="orion_operator")
        runtime_policy = policy_service.resolve_runtime_policy_mode(
            metadata,
            selected_target=metadata.get("execution_target_selected") or metadata.get("execution_target"),
        )
        policy_mode = str(runtime_policy.get("policy_mode") or POLICY_MODE_LOCAL_DEFAULT)
        action_counts = infer_actions_from_pack_result(outcome_pack, result_data)
        action_policy = policy_service.evaluate_action_policy(
            action_counts,
            policy_mode,
            metadata,
            metadata.get("execution_target_selected") or metadata.get("execution_target"),
        )
        action_policy = _apply_agent_machine_bypass_to_action_policy(action_policy, context=context)
        state["action_policy"] = action_policy
        emit_log(
            log_queue,
            "info",
            policy_service.summarize_action_policy_eval(action_policy),
            event="action_policy_evaluated",
            data={
                "phase": "pack_prepare",
                "policy_mode": action_policy.get("policy_mode"),
                "target": action_policy.get("target"),
                "denied_actions": action_policy.get("denied_actions"),
                "confirmation_required_actions": action_policy.get("confirmation_required_actions"),
            },
        )
        blocked_actions = action_policy.get("denied_actions") if isinstance(action_policy.get("denied_actions"), list) else []
        if blocked_actions:
            raise RuntimeError(f"Action policy blocked requested actions: {', '.join(blocked_actions)}.")
        confirmation_required = bool(action_policy.get("requires_confirmation"))
        confirmation_reason = str(action_policy.get("confirmation_reason") or "").strip()
        summary_text = str(result_data.get("summary") or "Outcome pack completed.")
        state["result_data"] = result_data
        state["policy_mode"] = policy_mode
        state["confirmation_required"] = confirmation_required
        state["confirmation_reason"] = confirmation_reason
        state["summary_text"] = summary_text
        return {"confirmation_required": confirmation_required, "policy_mode": policy_mode}

    if kind == "pack_approval":
        if not bool(state.get("confirmation_required")):
            emit_log(log_queue, "info", "Confirmation not required by runtime policy.", event="approval_skipped")
            return {"skipped": True}
        result_data = state.get("result_data") if isinstance(state.get("result_data"), dict) else {}
        outputs = result_data.get("outputs") if isinstance(result_data.get("outputs"), dict) else {}
        outbound_actions = parse_positive_int(outputs.get("outbound_actions"), 0)
        confirmation_reason = str(state.get("confirmation_reason") or "Confirmation required.")
        prompt = (
            f"Confirmation required. {confirmation_reason} "
            f"Planned outbound actions: {outbound_actions}. "
            "Reply with Proceed to continue or Hold to stop."
        )
        approved = wait_for_human_decision(run_id, prompt)
        if not approved:
            raise RuntimeError("Run stopped by human decision.")
        return {"approved": True}

    if kind == "pack_finalize":
        outcome_pack = str(state.get("outcome_pack") or "").strip().lower()
        result_data = state.get("result_data") if isinstance(state.get("result_data"), dict) else {}
        summary_text = str(state.get("summary_text") or "Outcome pack completed.")
        policy_mode = str(
            state.get("policy_mode")
            or policy_service.resolve_runtime_policy_mode(metadata).get("policy_mode")
            or POLICY_MODE_LOCAL_DEFAULT
        )
        approval_required = bool(state.get("confirmation_required"))
        approval_reason = str(state.get("confirmation_reason") or "")
        execution_summary = build_pack_execution_summary(
            outcome_pack,
            result_data,
            policy_mode,
            approval_required,
            approval_reason,
        )
        action_policy = state.get("action_policy") if isinstance(state.get("action_policy"), dict) else {}
        execution_summary["action_policy"] = {
            "blocked_actions": action_policy.get("blocked_actions", []),
            "approval_actions": action_policy.get("approval_actions", []),
            "target": action_policy.get("target"),
            "policy_mode": action_policy.get("policy_mode"),
        }
        result_data["execution_summary"] = execution_summary
        result_data["result_schema_version"] = 2
        result_data["policy_mode"] = policy_mode
        emit_log(log_queue, "info", summary_text, event="pack_summary", data=result_data)
        emit_log(
            log_queue,
            "info",
            (
                f"Execution summary: risk={execution_summary['risk_level']} "
                f"time_saved~{execution_summary['estimated_time_saved_minutes']}m "
                f"approval_required={execution_summary['approval_required']}"
            ),
            event="execution_summary",
            data=execution_summary,
        )
        usage = build_masked_usage(
            "orion",
            outcome_pack,
            f"{user_goal}\n{json.dumps(result_data.get('inputs', {}), ensure_ascii=True)}",
            summary_text,
        )
        emit_log(
            log_queue,
            "info",
            f"[Telemetry] provider={usage['provider']} model={usage['model']} "
            f"tokens~{usage['total_tokens_est']} cost={usage['cost_band']}",
            event="usage_masked",
            data=usage,
        )
        emit_log(log_queue, "info", "Empyralis run completed.", event="run_complete")
        state["final_result_text"] = summary_text
        state["final_result_data"] = result_data
        state["final_usage"] = usage
        return {"done": True}

    if kind == "runtime_resolve":
        workflow_id = context.get("workflow_id") or "n/a"
        business_plan = str(context.get("business_plan") or "")
        agent_summary = format_agent_summary(context.get("agents"))
        memory_context_block = _memory_prompt_context_block(context)
        if not (str(context.get("provider") or "").strip() or str(metadata.get("provider") or "").strip()):
            raise _honest_no_provider_error(context, metadata)
        provider, selected_model, candidates, _ = resolve_run_execution_context(context)
        plan_input = (
            f"Workflow ID: {workflow_id}\n"
            f"User Goal: {user_goal}\n\n"
            f"Business Plan:\n{business_plan or 'No business plan provided.'}\n\n"
            f"{memory_context_block}\n\n"
            f"Agent Setup:\n{agent_summary}\n\n"
            "Output only:\n"
            "1) Ordered plan\n"
            "2) External actions required\n"
            "3) Risks and assumptions\n"
        )
        state["provider"] = provider
        state["selected_model"] = str(selected_model)
        state["credential_candidates"] = candidates
        state["credentials"] = candidates[0].get("credentials") if candidates else {}
        state["plan_input"] = plan_input
        return {"provider": provider, "model": str(selected_model)}

    if kind == "plan_generate":
        plan_input = str(state.get("plan_input") or "")
        plan_prompt = ORION_PLANNER_SYSTEM_PROMPT
        plan_text = generate_with_candidate_failover(state, context, log_queue, plan_prompt, plan_input)
        emit_log(log_queue, "info", plan_text, event="orion_plan")
        state["plan_text"] = plan_text
        return {"chars": len(plan_text)}

    if kind == "plan_approval":
        plan_text = str(state.get("plan_text") or "")
        runtime_policy = policy_service.resolve_runtime_policy_mode(
            metadata,
            selected_target=metadata.get("execution_target_selected") or metadata.get("execution_target"),
        )
        policy_mode = str(runtime_policy.get("policy_mode") or POLICY_MODE_LOCAL_DEFAULT)
        plan_actions = infer_actions_from_text(plan_text)
        action_policy = policy_service.evaluate_action_policy(
            plan_actions,
            policy_mode,
            metadata,
            metadata.get("execution_target_selected") or metadata.get("execution_target"),
        )
        action_policy = _apply_agent_machine_bypass_to_action_policy(action_policy, context=context)
        state["action_policy"] = action_policy
        emit_log(
            log_queue,
            "info",
            policy_service.summarize_action_policy_eval(action_policy),
            event="action_policy_evaluated",
            data={
                "phase": "plan_approval",
                "policy_mode": action_policy.get("policy_mode"),
                "target": action_policy.get("target"),
                "denied_actions": action_policy.get("denied_actions"),
                "confirmation_required_actions": action_policy.get("confirmation_required_actions"),
            },
        )
        blocked_actions = action_policy.get("denied_actions") if isinstance(action_policy.get("denied_actions"), list) else []
        if blocked_actions:
            raise RuntimeError(f"Action policy blocked requested actions: {', '.join(blocked_actions)}.")
        if not bool(action_policy.get("requires_confirmation")):
            emit_log(log_queue, "info", "Confirmation not required by runtime policy.", event="approval_skipped")
            return {"skipped": True}
        reason = str(action_policy.get("confirmation_reason") or "Confirmation required before execution.").strip()
        prompt = (
            f"Confirmation required before execution. {reason} "
            "Reply with Proceed to continue or Hold to stop."
        ).strip()
        approved = wait_for_human_decision(run_id, prompt)
        if not approved:
            raise RuntimeError("Run stopped by human decision.")
        return {"approved": True}

    if kind == "result_generate":
        plan_text = str(state.get("plan_text") or "")
        execute_input = (
            f"User Goal: {user_goal}\n\n"
            f"Execution Plan:\n{plan_text}\n\n"
            "Now return:\n"
            "1) What Empyralis did\n"
            "2) What Empyralis needs from user\n"
            "3) Next immediate steps\n"
            "Keep the response concise and operational."
        )
        execute_prompt = ORION_OPERATOR_SYSTEM_PROMPT
        result_text = generate_with_candidate_failover(state, context, log_queue, execute_prompt, execute_input)
        emit_log(log_queue, "info", result_text, event="orion_result")
        final_text = (
            "Execution Plan\n"
            f"{plan_text}\n\n"
            "Execution Result\n"
            f"{result_text}"
        )
        state["execute_input"] = execute_input
        state["result_text"] = result_text
        state["final_text"] = final_text
        return {"chars": len(final_text)}

    if kind == "usage_finalize":
        provider = str(state.get("provider") or "openai")
        selected_model = str(state.get("active_model") or state.get("selected_model") or CODEX_MODEL)
        plan_input = str(state.get("plan_input") or "")
        execute_input = str(state.get("execute_input") or "")
        final_text = str(state.get("final_text") or "")
        usage = build_masked_usage(
            provider,
            selected_model,
            f"{plan_input}\n\n{execute_input}",
            final_text,
        )
        emit_log(
            log_queue,
            "info",
            f"[Telemetry] provider={usage['provider']} model={usage['model']} "
            f"tokens~{usage['total_tokens_est']} cost={usage['cost_band']}",
            event="usage_masked",
            data=usage,
        )
        emit_log(log_queue, "info", "Empyralis run completed.", event="run_complete")
        state["final_result_text"] = final_text
        state["final_result_data"] = None
        state["final_usage"] = usage
        return {"done": True}

    raise RuntimeError(f"Unsupported DAG node kind '{kind}'.")


def _execute_orion_dag_once(run_id: str, context: Dict[str, Any], log_queue: queue.Queue, dag_spec: Dict[str, Any]) -> Dict[str, Any]:
    nodes_raw = dag_spec.get("nodes")
    if not isinstance(nodes_raw, list) or not nodes_raw:
        raise RuntimeError("Compiled DAG has no nodes.")

    ordered_nodes = _resolve_dag_order(nodes_raw)
    if str(dag_spec.get("type") or "").strip().lower() != "workflow_graph":
        _ensure_run_node_states(run_id, graph_kind="dag", nodes=ordered_nodes)
    emit_log(
        log_queue,
        "info",
        f"DAG compiled: {dag_spec.get('id')} ({len(ordered_nodes)} nodes).",
        event="dag_compiled",
        data={
            "dag_id": dag_spec.get("id"),
            "dag_type": dag_spec.get("type"),
            "node_count": len(ordered_nodes),
            "nodes": [str(node.get("id") or "") for node in ordered_nodes],
        },
    )

    state: Dict[str, Any] = {
        "outcome_pack": dag_spec.get("outcome_pack"),
        "node_results": {},
    }
    completed: Set[str] = set()
    for node in ordered_nodes:
        node_id = str(node.get("id") or "").strip()
        deps = node.get("deps", [])
        if any(dep not in completed for dep in deps):
            missing = [dep for dep in deps if dep not in completed]
            raise RuntimeError(f"DAG node '{node_id}' has unresolved dependencies: {', '.join(missing)}")

        node_started = time.monotonic()
        if str(dag_spec.get("type") or "").strip().lower() != "workflow_graph":
            _update_run_node_state(
                run_id,
                node_id,
                status="running",
                activate=True,
                label=str(node.get("label") or node_id).strip() or node_id,
                node_type="dag",
                variant=str(node.get("kind") or "").strip().lower(),
                input_preview=_node_preview_text({"deps": deps, "completed": sorted(completed)}),
                summary=f"Executing DAG node {node_id}",
                reset_started=True,
            )
        emit_log(log_queue, "info", f"Node started: {node_id}", event="dag_node_start", data={"node_id": node_id})
        try:
            node_output = _execute_orion_dag_node(run_id, context, log_queue, node, state)
        except Exception as exc:
            if str(dag_spec.get("type") or "").strip().lower() != "workflow_graph":
                _update_run_node_state(
                    run_id,
                    node_id,
                    status="failed",
                    finalize=True,
                    error=_node_preview_text(friendly_runtime_error_message(exc), limit=400),
                    summary=f"Failed: {node_id}",
                    detail={"kind": node.get("kind")},
                )
            emit_log(
                log_queue,
                "error",
                friendly_runtime_error_message(exc),
                event="dag_node_error",
                data={"node_id": node_id},
            )
            raise
        elapsed_ms = round((time.monotonic() - node_started) * 1000.0, 2)
        state["node_results"][node_id] = node_output
        completed.add(node_id)
        if str(dag_spec.get("type") or "").strip().lower() != "workflow_graph":
            node_status = "skipped" if isinstance(node_output, dict) and bool(node_output.get("skipped")) else "succeeded"
            _update_run_node_state(
                run_id,
                node_id,
                status=node_status,
                finalize=True,
                output_preview=_node_preview_text(node_output),
                summary=(f"Skipped: {node_id}" if node_status == "skipped" else f"Completed: {node_id}"),
                detail={"kind": node.get("kind"), "result": node_output},
            )
        emit_log(
            log_queue,
            "info",
            f"Node completed: {node_id} ({elapsed_ms} ms)",
            event="dag_node_complete",
            data={"node_id": node_id, "duration_ms": elapsed_ms},
        )

    final_text = state.get("final_result_text")
    final_usage = state.get("final_usage")
    if not isinstance(final_text, str) or not isinstance(final_usage, dict):
        raise RuntimeError("DAG execution finished without final result payload.")
    final_data = state.get("final_result_data") if isinstance(state.get("final_result_data"), dict) else None
    return {
        "result_text": final_text,
        "result_data": final_data,
        "usage_masked": final_usage,
        "active_profile_id": state.get("active_profile_id"),
        "active_provider": state.get("active_provider") or state.get("provider"),
        "active_model": state.get("active_model") or state.get("selected_model"),
        "active_adapter": state.get("active_adapter"),
    }


def run_orion_mission(run_id: str):
    run = runs[run_id]
    run["thread_id"] = threading.get_ident()
    log_queue = run["logs"]
    context = run.get("context", {}) if isinstance(run.get("context"), dict) else {}
    clear_run_tool_signature_state(run_id)

    _enforce_execution_runtime_decision(
        run_id,
        operation="execute_run",
        run=run,
        context=context,
        engine_name=run.get("engine") or "orion",
        timeout_seconds=ORION_RUN_TIMEOUT_SECONDS,
    )
    set_run_status(run_id, "running")
    emit_log(log_queue, "info", "Empyralis run started.", event="run_start", data={"run_id": run_id})
    started_at = time.time()
    last_error: Optional[Exception] = None

    dag_spec = _compile_orion_dag(context)
    run["dag"] = {
        "id": dag_spec.get("id"),
        "type": dag_spec.get("type"),
        "nodes": [str(node.get("id") or "") for node in dag_spec.get("nodes", []) if isinstance(node, dict)],
    }

    for attempt in range(ORION_MAX_RETRIES + 1):
        try:
            if (time.time() - started_at) > ORION_RUN_TIMEOUT_SECONDS:
                raise RuntimeError(f"Empyralis run exceeded {ORION_RUN_TIMEOUT_SECONDS}s timeout.")

            result = _execute_orion_dag_once(run_id, context, log_queue, dag_spec)
            run["result"] = result["result_text"]
            run["result_data"] = result.get("result_data")
            run["usage_masked"] = result["usage_masked"]
            run["execution_outcome"] = _normalize_execution_outcome_with_rust(
                exit_code=0,
                stdout=str(result.get("result_text") or ""),
                artifacts=(result.get("result_data") or {}).get("artifacts") if isinstance(result.get("result_data"), dict) else [],
            )
            _apply_execution_outcome_record_patch(run, run.get("execution_outcome"), preserve_existing_result=True)
            final_status = _runtime_final_status_from_outcome(run.get("execution_outcome"), "completed")
            usage_accounting = usage_accounting_service.accounting_record_from_snapshot(
                {
                    "run_id": run_id,
                    "status": "completed",
                    "created_at": run.get("created_at"),
                    "updated_at": run.get("updated_at"),
                    "completed_at": run.get("completed_at"),
                    "context": context,
                    "usage_accounting": result.get("usage_accounting") if isinstance(result.get("usage_accounting"), dict) else None,
                    "usage_masked": result.get("usage_masked") if isinstance(result.get("usage_masked"), dict) else None,
                }
            )
            if isinstance(usage_accounting, dict):
                run["usage_accounting"] = usage_accounting
            run["active_profile_id"] = result.get("active_profile_id")
            run["active_provider"] = result.get("active_provider")
            run["active_model"] = result.get("active_model")
            run["active_adapter"] = result.get("active_adapter")
            clear_run_tool_signature_state(run_id)
            _set_run_status_after_execution_runtime_decision(
                run_id,
                final_status,
                run=run,
                context=context,
                engine_name=run.get("engine") or "orion",
                timeout_seconds=ORION_RUN_TIMEOUT_SECONDS,
            )
            run["logs"].put(None)
            return
        except Exception as exc:
            last_error = exc
            raw_message = str(exc)
            message = friendly_runtime_error_message(exc)
            non_retryable = is_non_retryable_runtime_error(exc)
            # MAN-108 Bug 2: everywhere else in this function, a failure here
            # only ever reaches emit_log (this run's own ephemeral in-memory
            # event buffer -- read only by a client actively streaming this
            # specific run's logs). Nothing was ever also going to Python
            # logging, so a durable/wake-triggered run's real exception never
            # reached server logs at all -- the only visible symptom was the
            # run's status silently flipping to "failed" minutes later with
            # no trace of why. Log it for real too.
            LOGGER.exception(
                "Empyralis run %s failed on attempt %s/%s: %s",
                run_id,
                attempt + 1,
                ORION_MAX_RETRIES + 1,
                raw_message,
            )

            if "timeout" in raw_message.lower() or "timeout" in message.lower():
                run["execution_outcome"] = _normalize_execution_outcome_with_rust(
                    timed_out=True,
                    stderr=message or raw_message,
                )
                _apply_execution_outcome_record_patch(run, run.get("execution_outcome"))
                final_status = _runtime_final_status_from_outcome(run.get("execution_outcome"), "timeout")
                emit_log(
                    log_queue,
                    _runtime_outcome_log_level(run.get("execution_outcome"), "error"),
                    _runtime_outcome_summary(run.get("execution_outcome"), message),
                    event=_runtime_outcome_event(run.get("execution_outcome"), "timeout"),
                )
                clear_run_tool_signature_state(run_id)
                _set_run_status_after_execution_runtime_decision(
                    run_id,
                    final_status,
                    run=run,
                    context=context,
                    engine_name=run.get("engine") or "orion",
                    timeout_seconds=ORION_RUN_TIMEOUT_SECONDS,
                )
                run["logs"].put(None)
                return

            if "stopped by human decision" in raw_message.lower() or "stopped by human decision" in message.lower():
                run["execution_outcome"] = _normalize_execution_outcome_with_rust(
                    cancelled=True,
                    stderr=message or raw_message,
                )
                _apply_execution_outcome_record_patch(run, run.get("execution_outcome"))
                final_status = _runtime_final_status_from_outcome(run.get("execution_outcome"), "cancelled")
                emit_log(
                    log_queue,
                    _runtime_outcome_log_level(run.get("execution_outcome"), "warn"),
                    _runtime_outcome_summary(run.get("execution_outcome"), message),
                    event=_runtime_outcome_event(run.get("execution_outcome"), "run_stopped"),
                )
                clear_run_tool_signature_state(run_id)
                _set_run_status_after_execution_runtime_decision(
                    run_id,
                    final_status,
                    run=run,
                    context=context,
                    engine_name=run.get("engine") or "orion",
                    timeout_seconds=ORION_RUN_TIMEOUT_SECONDS,
                )
                run["logs"].put(None)
                return

            failure_outcome = _normalize_execution_outcome_with_rust(
                exit_code=1,
                stderr=raw_message or message,
            )
            run["execution_outcome"] = failure_outcome
            _apply_execution_outcome_record_patch(run, failure_outcome)
            rust_retryable = bool(failure_outcome.get("retryable"))
            final_status = _runtime_final_status_from_outcome(failure_outcome, "failed")

            if non_retryable or not rust_retryable:
                emit_log(
                    log_queue,
                    _runtime_outcome_log_level(failure_outcome, "error"),
                    _runtime_outcome_summary(failure_outcome, message),
                    event=_runtime_outcome_event(failure_outcome, "run_error"),
                    data={"attempt": attempt + 1, "retryable": False, "raw_error": raw_message},
                )
                clear_run_tool_signature_state(run_id)
                _set_run_status_after_execution_runtime_decision(
                    run_id,
                    final_status,
                    run=run,
                    context=context,
                    engine_name=run.get("engine") or "orion",
                    timeout_seconds=ORION_RUN_TIMEOUT_SECONDS,
                )
                run["logs"].put(None)
                return

            emit_log(
                log_queue,
                "warn",
                f"Empyralis run failed on attempt {attempt + 1}.",
                event="run_retry",
                data={"attempt": attempt + 1, "error": message, "raw_error": raw_message},
            )
            if attempt < ORION_MAX_RETRIES:
                backoff = ORION_RETRY_BACKOFF_SECONDS * (2 ** attempt)
                time.sleep(backoff)

    run["execution_outcome"] = _normalize_execution_outcome_with_rust(
        exit_code=1,
        stderr=friendly_runtime_error_message(last_error or Exception("Unknown runtime failure")),
    )
    _apply_execution_outcome_record_patch(run, run.get("execution_outcome"))
    emit_log(
        log_queue,
        _runtime_outcome_log_level(run.get("execution_outcome"), "error"),
        _runtime_outcome_summary(
            run.get("execution_outcome"),
            friendly_runtime_error_message(last_error or Exception("Unknown runtime failure")),
        ),
        event=_runtime_outcome_event(run.get("execution_outcome"), "run_error"),
    )
    clear_run_tool_signature_state(run_id)
    _set_run_status_after_execution_runtime_decision(
        run_id,
        _runtime_final_status_from_outcome(run.get("execution_outcome"), "failed"),
        run=run,
        context=context,
        engine_name=run.get("engine") or "orion",
        timeout_seconds=ORION_RUN_TIMEOUT_SECONDS,
    )
    run["logs"].put(None)


def run_mission(run_id):
    # Internal delegate. Not an alternate turn engine. Called only from agent_turn().
    run = runs.get(run_id)
    if not run:
        return
    engine_name = (run.get("engine") or "orion").lower()
    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    selected_target = str(metadata.get("execution_target_selected") or "").strip().lower()
    requested_target = str(metadata.get("execution_target_requested") or "").strip().lower()
    route_reason = str(metadata.get("execution_target_reason") or "").strip()
    route_fallback = str(metadata.get("execution_target_fallback") or "").strip()

    if selected_target:
        route_msg = (
            f"Routing: requested={requested_target or 'auto'}, "
            f"selected={selected_target}. {route_reason}".strip()
        )
        emit_log(
            run["logs"],
            "info",
            route_msg,
            event="route_decision",
            data={
                "requested": requested_target or EXECUTION_TARGET_AUTO,
                "selected": selected_target,
                "reason": route_reason,
                "fallback": route_fallback or None,
            },
        )
        if route_fallback:
            emit_log(run["logs"], "warn", route_fallback, event="route_fallback")

    engine = ENGINE_REGISTRY.get(engine_name)
    if not engine:
        emit_log(run["logs"], "error", f"Unsupported engine: {engine_name}", event="run_error")
        set_run_status(run_id, "failed")
        run["logs"].put(None)
        return
    timeout_seconds = max(1, int(metadata.get("timeout_seconds") or ORION_RUN_TIMEOUT_SECONDS or 300))

    try:
        _hydrate_run_memory_context(run_id, run)
    except Exception as exc:
        trace = run.setdefault("memory_trace", {"enabled": ORION_MEMORY_ENABLED, "reads": [], "writes": [], "last_error": None, "updated_at": None})
        trace["last_error"] = f"memory_read_failed:{exc}"
        trace["updated_at"] = _utc_now_iso()
        run["memory_trace"] = trace
        emit_log(run["logs"], "warn", "Memory context read failed; continuing without memory.", event="memory_context_error")

    try:
        _enforce_execution_runtime_decision(
            run_id,
            operation="dispatch_run",
            run=run,
            context=context,
            engine_name=engine_name,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:
        emit_log(run["logs"], "error", friendly_runtime_error_message(exc), event="run_error")
        try:
            _set_run_status_after_execution_runtime_decision(
                run_id,
                "failed",
                run=run,
                context=context,
                engine_name=engine_name,
                timeout_seconds=timeout_seconds,
            )
        except Exception:
            pass
        run["logs"].put(None)
        return

    _log_execution_boundary(run["logs"], run_id, "start", timeout_seconds=timeout_seconds)
    try:
        execution_result = _execute_engine_with_timeout(engine, run_id, timeout_seconds)
        if execution_result.get("timed_out"):
            run["execution_outcome"] = _normalize_execution_outcome_with_rust(
                timed_out=True,
                stderr=f"Run exceeded {timeout_seconds}s timeout.",
            )
            _apply_execution_outcome_record_patch(run, run.get("execution_outcome"))
            final_status = _runtime_final_status_from_outcome(run.get("execution_outcome"), "timeout")
            emit_log(
                run["logs"],
                _runtime_outcome_log_level(run.get("execution_outcome"), "error"),
                _runtime_outcome_summary(run.get("execution_outcome"), f"Run exceeded {timeout_seconds}s timeout."),
                event=_runtime_outcome_event(run.get("execution_outcome"), "timeout"),
                data={"run_id": run_id, "timeout_seconds": timeout_seconds},
            )
            _set_run_status_after_execution_runtime_decision(
                run_id,
                final_status,
                run=run,
                context=context,
                engine_name=engine_name,
                timeout_seconds=timeout_seconds,
            )
            _log_execution_boundary(run["logs"], run_id, "end", status="timeout")
            run["logs"].put(None)
            return
        if execution_result.get("error") is not None:
            raise execution_result["error"]
        try:
            _persist_run_memory(run_id, run)
        except Exception as exc:
            trace = run.setdefault("memory_trace", {"enabled": ORION_MEMORY_ENABLED, "reads": [], "writes": [], "last_error": None, "updated_at": None})
            trace["last_error"] = f"memory_write_failed:{exc}"
            trace["updated_at"] = _utc_now_iso()
            run["memory_trace"] = trace
            emit_log(run["logs"], "warn", "Memory write failed after run completion.", event="memory_write_error")
        if not isinstance(run.get("execution_outcome"), dict):
            run["execution_outcome"] = _normalize_execution_outcome_with_rust(
                exit_code=0,
                stdout=str(run.get("result") or ""),
                artifacts=(run.get("result_data") or {}).get("artifacts") if isinstance(run.get("result_data"), dict) else [],
            )
            _apply_execution_outcome_record_patch(run, run.get("execution_outcome"), preserve_existing_result=True)
        _log_execution_boundary(run["logs"], run_id, "end", status=str(run.get("status") or "completed"))
    except Exception as exc:
        run["execution_outcome"] = _normalize_execution_outcome_with_rust(
            exit_code=1,
            stderr=friendly_runtime_error_message(exc),
        )
        _apply_execution_outcome_record_patch(run, run.get("execution_outcome"))
        emit_log(
            run["logs"],
            _runtime_outcome_log_level(run.get("execution_outcome"), "error"),
            _runtime_outcome_summary(run.get("execution_outcome"), friendly_runtime_error_message(exc)),
            event=_runtime_outcome_event(run.get("execution_outcome"), "run_error"),
        )
        _set_run_status_after_execution_runtime_decision(
            run_id,
            _runtime_final_status_from_outcome(run.get("execution_outcome"), "failed"),
            run=run,
            context=context,
            engine_name=engine_name,
            timeout_seconds=timeout_seconds,
        )
        _log_execution_boundary(run["logs"], run_id, "end", status="failed")
        run["logs"].put(None)
