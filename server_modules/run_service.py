from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
import queue
import re
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
import uuid

from fastapi import HTTPException

from server_modules.agent_turn import AgentTurnRequest, bind_agent_turn_metadata, resolve_run_start_turn_request
from server_modules import agent_trace_service
from server_modules import app_bridge_service
from server_modules import deployed_agent_cost_cap_service
from server_modules.doctor_gate import build_doctor_run_gate_live
from server_modules.direct_tool_config_service import run_async_tool_call
from server_modules import execution_sandbox_service
from server_modules import machine_lease_service
from server_modules import outbox_service
from server_modules import rust_runtime_kernel_client
from server_modules.policy_service import apply_execution_route_metadata, decide_execution_target
from server_modules.run_execution_handle import attach_execution_handle, build_run_record, durable_run_payload
from server_modules import run_state_repository
from server_modules.runtime_models import RunStartRequest
from server_modules.telemetry import get_tracer, record_reliability_latency_sample, set_span_attributes

LOGGER = logging.getLogger(__name__)
MAX_WORKFLOW_TURN_DEPTH_DEFAULT = 30
WORKFLOW_TURN_DEPTH_METADATA_KEY = "workflow_turn_depth"
_WORKFLOW_TURN_DEPTH_PROTECTED_KEYS = {
    WORKFLOW_TURN_DEPTH_METADATA_KEY,
    "workflow_turn_depth_limit",
    "workflow_turn_parent_run_id",
    "workflow_turn_child_kind",
}


class WorkflowRecursionError(RuntimeError):
    """Raised when workflow child-run recursion exceeds the configured limit."""


def _max_workflow_turn_depth() -> int:
    raw = str(os.getenv("EMPYRALIS_MAX_WORKFLOW_TURN_DEPTH") or "").strip()
    if not raw:
        return MAX_WORKFLOW_TURN_DEPTH_DEFAULT
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return MAX_WORKFLOW_TURN_DEPTH_DEFAULT


def _workflow_turn_depth_from_metadata(metadata: Any) -> int:
    payload = metadata if isinstance(metadata, dict) else {}
    try:
        return max(0, int(payload.get(WORKFLOW_TURN_DEPTH_METADATA_KEY) or 0))
    except (TypeError, ValueError):
        return 0


def assert_workflow_turn_depth_allowed(metadata: Any) -> int:
    depth = _workflow_turn_depth_from_metadata(metadata)
    limit = _max_workflow_turn_depth()
    if depth > limit:
        raise WorkflowRecursionError(f"Workflow recursion depth limit reached ({limit}).")
    return depth


# ── Phase 4: specialist subagent depth + parallelism caps ───────────────────
# A specialist may spawn bounded subagents for parallel chunks, but only ONE
# level deep (a subagent may NOT spawn further subagents) and only up to a
# configurable fan-out. depth 0 = the operator/root turn; depth 1 = its
# subagents; depth 2+ is denied by default.
MAX_SUBAGENT_DEPTH_DEFAULT = 1
MAX_PARALLEL_SUBAGENTS_DEFAULT = 3
SUBAGENT_DEPTH_METADATA_KEY = "subagent_depth"


class SubagentDepthError(RuntimeError):
    """Raised when subagent delegation would exceed the configured depth cap."""


def max_subagent_depth() -> int:
    raw = str(os.getenv("EMPYRALIS_MAX_SUBAGENT_DEPTH") or "").strip()
    if not raw:
        return MAX_SUBAGENT_DEPTH_DEFAULT
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return MAX_SUBAGENT_DEPTH_DEFAULT


def max_parallel_subagents() -> int:
    raw = str(os.getenv("EMPYRALIS_MAX_PARALLEL_SUBAGENTS") or "").strip()
    if not raw:
        return MAX_PARALLEL_SUBAGENTS_DEFAULT
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return MAX_PARALLEL_SUBAGENTS_DEFAULT


def subagent_depth_from_metadata(metadata: Any) -> int:
    payload = metadata if isinstance(metadata, dict) else {}
    try:
        return max(0, int(payload.get(SUBAGENT_DEPTH_METADATA_KEY) or 0))
    except (TypeError, ValueError):
        return 0


def assert_subagent_spawn_allowed(parent_metadata: Any) -> int:
    """Called before a parent spawns subagents. The parent at depth D produces
    children at depth D+1; deny if that would exceed max_subagent_depth().
    Returns the child depth."""
    parent_depth = subagent_depth_from_metadata(parent_metadata)
    child_depth = parent_depth + 1
    limit = max_subagent_depth()
    if child_depth > limit:
        raise SubagentDepthError(
            f"Subagent depth limit reached (max depth {limit}); a subagent may not spawn further subagents."
        )
    return child_depth


def build_workflow_child_metadata(
    context: Dict[str, Any],
    *,
    parent_run_id: str,
    child_kind: str,
) -> Dict[str, Any]:
    metadata = dict(context.get("metadata") if isinstance(context.get("metadata"), dict) else {})
    parent_depth = _workflow_turn_depth_from_metadata(metadata)
    child_depth = parent_depth + 1
    limit = _max_workflow_turn_depth()
    if child_depth > limit:
        raise WorkflowRecursionError(f"Workflow recursion depth limit reached ({limit}).")
    metadata[WORKFLOW_TURN_DEPTH_METADATA_KEY] = child_depth
    metadata["workflow_turn_depth_limit"] = limit
    metadata["workflow_turn_parent_run_id"] = str(parent_run_id or "").strip() or None
    metadata["workflow_turn_child_kind"] = str(child_kind or "").strip() or "workflow_child_run"
    return metadata


def merge_workflow_child_metadata_overrides(
    child_metadata: Dict[str, Any],
    metadata_overrides: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if not isinstance(metadata_overrides, dict):
        return child_metadata
    for key, value in metadata_overrides.items():
        if str(key) in _WORKFLOW_TURN_DEPTH_PROTECTED_KEYS:
            continue
        child_metadata[key] = value
    return child_metadata


RUN_STATES = (
    "queued",
    "planning",
    "waiting_approval",
    "machine_allocating",
    "executing",
    "blocked",
    "retrying",
    "completed",
    "failed",
    "canceled",
)

ROUTING_PROVIDER_ORDER: Tuple[str, ...] = ("openai", "deepseek", "anthropic", "gemini", "codex_cli", "ollama")
APPROVAL_SCOPE_ONCE = "once"
APPROVAL_SCOPE_CONSEQUENCE = "This confirmation applies only to this pending step in this run. Later runs or later confirmation points will ask again."
APPROVAL_APPROVE_TOKENS = {"proceed", "approve", "yes", "y", "continue", "ok"}
APPROVAL_REJECT_TOKENS = {"hold", "reject", "no", "n", "abort", "stop", "cancel"}
APPROVAL_ESCALATE_TOKENS = {"escalate", "escalated"}
SUPPORTED_RUNTIME_MODES = frozenset({"hosted_secure", "local_secure", "privileged_device"})
LOCAL_RUNTIME_MODES = frozenset({"local_secure", "privileged_device"})
RESOLVED_APPROVAL_MARKERS_KEY = "resolved_approval_markers"
EMAIL_RECIPIENT_PATTERN = re.compile(
    r"(?i)^[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+$"
)


AUTO_DELEGATION_ROLE_RULES: Dict[str, Dict[str, Any]] = {
    "research": {
        "keywords": [
            "research", "market", "analysis", "analyze", "brief", "summary", "competitor",
            "plan", "strategy", "launch", "marketing", "investigate", "study",
        ],
        "goal": "Research and summarize the key findings for this objective: {objective}",
        "reason": "Research and planning support is needed.",
    },
    "builder": {
        "keywords": [
            "build", "implement", "fix", "ship", "code", "frontend", "backend", "bug",
            "automation", "platform", "app", "browser", "workflow", "integrat",
        ],
        "goal": "Implement or validate the execution work needed for this objective: {objective}",
        "reason": "Execution or implementation work is needed.",
    },
    "sales": {
        "keywords": [
            "sales", "lead", "booking", "book", "appointment", "pipeline", "conversion",
            "convert", "follow-up", "follow up", "outreach",
        ],
        "goal": "Handle the sales or booking follow-through required for this objective: {objective}",
        "reason": "Sales or booking work is needed.",
    },
    "support": {
        "keywords": [
            "support", "customer", "inbox", "message", "feedback", "complaint", "reply",
            "telegram", "whatsapp", "email",
        ],
        "goal": "Handle customer-facing support follow-up for this objective: {objective}",
        "reason": "Customer support work is needed.",
    },
    "finance": {
        "keywords": [
            "finance", "budget", "revenue", "price", "pricing", "invoice", "expense",
            "spreadsheet", "sheet", "excel", "report",
        ],
        "goal": "Prepare the financial or spreadsheet work needed for this objective: {objective}",
        "reason": "Financial or reporting work is needed.",
    },
    "private-assistant": {
        "keywords": [
            "personal", "study", "exam", "reminder", "calendar", "routine", "habit",
            "travel", "meal", "homework",
        ],
        "goal": "Handle the personal assistant follow-up for this objective: {objective}",
        "reason": "Personal assistance is needed.",
    },
}


def resolve_fastest_routing_context(
    *,
    routing_provider_order: Tuple[str, ...],
    provider_has_key_fn: Callable[[str], bool],
    resolve_requested_model_fn: Callable[[Dict[str, Any], Dict[str, Any], str], Any],
) -> Optional[Dict[str, str]]:
    for provider in routing_provider_order:
        if not provider_has_key_fn(provider):
            continue
        model = str(
            resolve_requested_model_fn({"provider": provider}, {"provider": provider}, provider)
        ).strip() or None
        return {"provider": provider, "model": model or ""}
    return None


@dataclass(slots=True)
class RunRecord:
    run_id: str
    tenant_id: str
    workspace_id: str
    state: str = "queued"
    session_id: str = ""
    machine_target: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RunTransition:
    run_id: str
    from_state: str
    to_state: str
    reason: str = ""
    actor_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RunExecutionServices:
    stamp_request_owner: Any
    prepare_run_start_request: Any
    create_run_from_request: Any


@dataclass(slots=True)
class DurableTurnExecutionRequest:
    engine: str = "orion"
    workflow_id: Optional[str] = None
    workflow_version_id: Optional[str] = None
    workspace_id: str = "default"
    user_goal: str = ""
    business_plan: Optional[str] = None
    max_iterations: Optional[int] = None
    agent_role: Optional[str] = None
    parent_run_id: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    credential_id: Optional[str] = None
    agents: List[dict] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RunRoutingPreviewServices:
    prepare_run_start_request: Any
    compute_tool_policy_precheck: Any


@dataclass(slots=True)
class RunCreationServices:
    create_run_from_request: Any


@dataclass(slots=True)
class RunPreparedResultServices:
    create_run_from_prepared_request: Any
    build_result: Any


@dataclass(slots=True)
class LegacyRunRequestServices:
    prepare_run_start_request: Any
    build_creation_services: Any
    result_services: RunPreparedResultServices


@dataclass(slots=True)
class LegacyRunPreparationServices:
    build_preparation_services: Any


@dataclass(slots=True)
class LegacyRunExecutionCallbacks:
    stamp_request_owner: Any
    prepare_run_start_request: Any
    create_run_from_request: Any


@dataclass(slots=True)
class LegacyLocalExecutionCreationCallbacks:
    decide_execution_target: Any
    apply_execution_route_metadata: Any
    build_doctor_run_gate: Any
    agent_machine_inherited_owner_user_id: Any
    compute_tool_policy_precheck: Any
    resolve_runtime_policy_mode: Any
    agent_machine_full_trust_enabled: Any
    begin_run_pending_confirmation: Any
    create_run: Any
    local_execution_target: str
    local_execution_pack_id: str
    load_created_run: Any = None
    now_iso: Any = None


@dataclass(slots=True)
class LegacyOrionPreparationCallbacks:
    engine_registry: Any
    engine_validation_errors: Any
    supported_outcome_packs: Any
    normalize_requested_max_iterations: Callable[[Any], Optional[int]]
    normalize_trust_mode: Callable[[str], str]
    trust_mode_aliases: Any
    valid_trust_modes: Any
    normalize_execution_target: Callable[[Any], str]
    valid_execution_targets: Any
    normalize_run_id_token: Callable[[Any], Optional[str]]
    normalize_agent_role: Callable[[Any], str]
    detect_agent_role: Callable[[RunStartRequest, Dict[str, Any]], tuple[str, str]]
    resolve_app_permissions: Callable[[str], Any]
    action_policy_from_app_permissions: Callable[[Any], Dict[str, Any]]
    merge_action_policies: Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]]
    fetch_workflow_snapshot: Callable[[Any], Any]
    postprocess_metadata: Optional[Callable[[RunStartRequest, Dict[str, Any]], Dict[str, Any]]] = None


@dataclass(slots=True)
class RunPreparationServices:
    engine_registry: Any
    engine_validation_errors: Any
    supported_outcome_packs: Any
    normalize_requested_max_iterations: Callable[[Any], Optional[int]]
    normalize_trust_mode: Callable[[str], str]
    trust_mode_aliases: Any
    valid_trust_modes: Any
    normalize_execution_target: Callable[[Any], str]
    valid_execution_targets: Any
    normalize_run_id_token: Callable[[Any], Optional[str]]
    normalize_agent_role: Callable[[Any], str]
    detect_agent_role: Callable[[RunStartRequest, Dict[str, Any]], tuple[str, str]]
    resolve_app_permissions: Callable[[str], Any]
    action_policy_from_app_permissions: Callable[[Any], Dict[str, Any]]
    merge_action_policies: Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]]
    fetch_workflow_snapshot: Callable[[Any], Any]
    postprocess_metadata: Optional[Callable[[RunStartRequest, Dict[str, Any]], Dict[str, Any]]] = None


@dataclass(slots=True)
class PreparedRunCreationServices:
    decide_execution_target: Any
    apply_execution_route_metadata: Any
    build_doctor_run_gate: Any
    agent_machine_inherited_owner_user_id: Any
    compute_tool_policy_precheck: Any
    apply_browser_execution_metadata: Any
    local_execution_block_prompt: Any
    resolve_runtime_policy_mode: Any
    agent_machine_full_trust_enabled: Any
    local_execution_requires_start_confirmation: Any
    mark_local_execution_tools_approved: Any
    precheck_human_action_labels: Any
    local_execution_confirmation_prompt: Any
    begin_run_pending_confirmation: Any
    create_run: Any
    load_created_run: Any = None
    now_iso: Any = None


def build_live_run_record(
    *,
    run_id: str,
    engine: str,
    context: Dict[str, Any],
    now_iso: str,
    started_mono: float,
    log_queue: Any,
    input_queue: Any,
    memory_enabled: bool,
    memory_updated_at: str,
    active_profile_id: Optional[str],
    active_profile_label: Optional[str],
    active_provider: Optional[str],
    active_model: Optional[str],
) -> Dict[str, Any]:
    record = build_run_record(
        run_id=run_id,
        engine=engine,
        context=context,
        now_iso=now_iso,
        memory_enabled=memory_enabled,
        memory_updated_at=memory_updated_at,
        active_profile_id=active_profile_id,
        active_profile_label=active_profile_label,
        active_provider=active_provider,
        active_model=active_model,
    )
    return attach_execution_handle(
        record,
        log_queue=log_queue,
        input_queue=input_queue,
        started_mono=started_mono,
        finished_mono=None,
        first_value_mono=None,
        hitl_wait_start_mono=None,
        thread_id=None,
        event_seq=0,
    )


def register_live_run(
    run_id: str,
    run: Dict[str, Any],
    *,
    runs_by_id: Dict[str, Dict[str, Any]],
    run_queue_index: Dict[int, str],
    metrics_inc_fn: Callable[[str, int], Any],
    persist_live_run_state_fn: Callable[[str, Dict[str, Any]], Any],
    create_live_run_initial_fn: Optional[Callable[..., Any]] = None,
    record_transition_fn: Optional[Callable[..., Any]] = None,
    serialize_durable_run_payload_fn: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None,
) -> None:
    state = str(run.get("status") or run.get("state") or "queued").strip() or "queued"
    trace_id = _run_trace_id(run)
    if callable(create_live_run_initial_fn):
        payload_builder = serialize_durable_run_payload_fn or _serialize_run_for_durable_repository
        initial_snapshot = payload_builder(run_id, run)
        try:
            created = create_live_run_initial_fn(
                run_id,
                _run_workspace_id(run),
                _run_tenant_id(run),
                state,
                initial_snapshot,
                trace_id,
            )
        except run_state_repository.RunStatePersistenceError:
            raise
        run["_durable_version"] = int((created or {}).get("version") or 0)
        if (created or {}).get("registered_at"):
            run["_durable_registered_at"] = created["registered_at"]
        if callable(record_transition_fn):
            try:
                record_transition_fn(
                    run_id,
                    "unknown",
                    state,
                    "runtime",
                    trace_id,
                )
            except run_state_repository.RunStatePersistenceError as exc:
                LOGGER.warning("Failed to record initial run transition for %s: %s", run_id, exc)
    runs_by_id[run_id] = run
    log_queue = run.get("logs")
    if log_queue is not None:
        run_queue_index[id(log_queue)] = run_id
    metrics_inc_fn("runs_started", 1)
    if not callable(create_live_run_initial_fn):
        try:
            persist_live_run_state_fn(run_id, run)
        except Exception as exc:
            LOGGER.warning("Failed to persist live run state for %s: %s", run_id, exc)


def _json_safe_run_payload(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, default=str))
    except Exception:
        return str(value)


def _serialize_run_for_durable_repository(run_id: str, run: Dict[str, Any]) -> Dict[str, Any]:
    return durable_run_payload(run_id, run, json_safe=_json_safe_run_payload)


def _run_payload_store(run: Dict[str, Any]) -> Any:
    record = getattr(run, "record", None)
    payload = getattr(record, "payload", None)
    return payload if isinstance(payload, dict) else None


@contextmanager
def _suspend_run_persistence_notifications(run: Dict[str, Any]):
    payload = _run_payload_store(run)
    if payload is None or not hasattr(payload, "_suspend_notifications"):
        yield
        return
    previous = bool(getattr(payload, "_suspend_notifications", False))
    payload._suspend_notifications = True
    try:
        yield
    finally:
        payload._suspend_notifications = previous


def _set_run_field_quietly(run: Dict[str, Any], key: str, value: Any) -> None:
    with _suspend_run_persistence_notifications(run):
        run[key] = value


def _run_durable_version(run: Dict[str, Any]) -> int:
    try:
        return max(0, int(run.get("_durable_version") or 0))
    except Exception:
        return 0


def _persist_live_run_state_with_version(
    run_id: str,
    run: Dict[str, Any],
    *,
    persist_live_run_state_fn: Callable[[str, Dict[str, Any]], Any],
) -> int:
    if (
        getattr(persist_live_run_state_fn, "__module__", "") == "server_modules.runs_output"
        and getattr(persist_live_run_state_fn, "__name__", "") == "_persist_live_run_state"
    ):
        return _run_durable_version(run)
    current_version = _run_durable_version(run)
    persist_live_run_state_fn(run_id, run)
    run["_durable_version"] = current_version + 1
    return current_version + 1


def _run_workspace_id(run: Dict[str, Any]) -> str:
    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    return str(run.get("workspace_id") or context.get("workspace_id") or metadata.get("workspace_id") or "default").strip() or "default"


def _run_tenant_id(run: Dict[str, Any]) -> str:
    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    return str(run.get("tenant_id") or context.get("tenant_id") or metadata.get("tenant_id") or "default").strip() or "default"


def _run_trace_id(run: Dict[str, Any], *, fallback: Optional[str] = None) -> str:
    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    token = (
        str(run.get("trace_id") or "").strip()
        or str(metadata.get("trace_id") or "").strip()
        or str(metadata.get("request_trace_id") or "").strip()
        or str(fallback or "").strip()
    )
    return token


def _trace_surface_for_channel(channel: Any) -> str:
    normalized = str(channel or "").strip().lower()
    if normalized in {"web", "mobile", "desktop", "api"}:
        return normalized
    return "channel"


def _agent_install_id_for_metadata(metadata: Optional[Dict[str, Any]]) -> str:
    """The bare install id this run belongs to, or "" when it is the Operator.

    Extracted so _trace_root_agent_id_for_metadata and every caller that
    needs the id WITHOUT the "specialist:" prefix share one resolution
    order. Two independent answers to "which agent is this run" is exactly
    how a trace and a notification end up disagreeing about the same run.
    """
    safe_metadata = metadata if isinstance(metadata, dict) else {}
    return (
        str(safe_metadata.get("active_agent_install_id") or "").strip()
        or str(safe_metadata.get("master_agent_install_id") or "").strip()
        or str(safe_metadata.get("workspace_agent_install_id") or "").strip()
    )


def _trace_root_agent_id_for_metadata(metadata: Optional[Dict[str, Any]]) -> str:
    install_id = _agent_install_id_for_metadata(metadata)
    if install_id:
        return f"specialist:{install_id}"
    return "sage"


def _run_agent_install_id(run: Dict[str, Any]) -> str:
    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    return _agent_install_id_for_metadata(metadata)


def _run_failure_summary(run: Dict[str, Any], status: str) -> str:
    """Why this run ended badly, in one sentence.

    Was derived inline inside the blocked-task branch and used only in a
    system comment body. The same fact belongs on the outbox event so the
    Inbox row can say it too — derived HERE, once, so the comment and the
    notification can never disagree about the same run.
    """
    outcome = run.get("execution_outcome") if isinstance(run.get("execution_outcome"), dict) else {}
    return (
        str(outcome.get("summary") or "").strip()
        or str(outcome.get("stderr") or "").strip()
        or str(run.get("error") or "").strip()
        or str(run.get("result") or "").strip()
        or f'The assigned agent\'s run ended with status "{status}" and produced no result.'
    )


def _trace_runtime_target_for_turn_request(turn_request: AgentTurnRequest) -> str:
    hints = turn_request.context_hints.get("metadata") if isinstance(turn_request.context_hints.get("metadata"), dict) else {}
    return (
        str(turn_request.machine_target or "").strip()
        or str(turn_request.policy_context.get("execution_target") or "").strip()
        or str(hints.get("execution_target_selected") or "").strip()
        or str(hints.get("execution_target") or "").strip()
        or "cloud"
    )


def _trace_provider_for_turn_request(turn_request: AgentTurnRequest, *, base_request: Optional[Any] = None) -> str:
    return (
        str(turn_request.context_hints.get("provider") or "").strip()
        or str(getattr(base_request, "provider", None) or "").strip()
    )


def _trace_model_for_turn_request(turn_request: AgentTurnRequest, *, base_request: Optional[Any] = None) -> str:
    return (
        str(turn_request.context_hints.get("model") or "").strip()
        or str(getattr(base_request, "model", None) or "").strip()
    )


def _trace_routing_reason_for_turn_request(turn_request: AgentTurnRequest) -> str:
    metadata = turn_request.context_hints.get("metadata") if isinstance(turn_request.context_hints.get("metadata"), dict) else {}
    return (
        str(metadata.get("primary_engine_reason") or "").strip()
        or str(metadata.get("routing_reason") or "").strip()
        or (
            "execution_mode:durable"
            if str(turn_request.execution_mode or "").strip().lower() == "durable"
            else "execution_mode:sync"
        )
    )


def _resume_run_trace_context(run: Dict[str, Any]) -> Optional[Any]:
    if not isinstance(run, dict):
        return None
    trace_id = _run_trace_id(run)
    if not trace_id:
        return None
    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    thread_id = (
        str(run.get("thread_id") or "").strip()
        or str(context.get("thread_id") or "").strip()
        or str(metadata.get("thread_id") or "").strip()
        or str(metadata.get("session_id") or "").strip()
        or None
    )
    run_id = str(run.get("run_id") or "").strip() or None
    try:
        return run_async_tool_call(
            agent_trace_service.resume_trace(
                trace_id=trace_id,
                tenant_id=_run_tenant_id(run),
                workspace_id=_run_workspace_id(run),
                thread_id=thread_id,
                run_id=run_id,
            )
        )
    except Exception as exc:
        LOGGER.warning("Failed to resume trace context for run %s: %s", run_id or "unknown", exc)
        return None


def _emit_trace_call(awaitable: Any, *, operation: str) -> Any:
    try:
        return run_async_tool_call(awaitable)
    except Exception as exc:
        LOGGER.warning("Failed to emit durable trace event during %s: %s", operation, exc)
        return None


def _emit_artifact_trace_events(run_id: str, *, run: Dict[str, Any], trace_context: Any) -> None:
    result_data = run.get("result_data") if isinstance(run.get("result_data"), dict) else {}
    outputs = result_data.get("outputs") if isinstance(result_data.get("outputs"), dict) else {}
    artifacts = outputs.get("artifacts") if isinstance(outputs.get("artifacts"), list) else []
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict):
            continue
        artifact_id = (
            str(artifact.get("id") or "").strip()
            or str(artifact.get("artifact_id") or "").strip()
            or f"{run_id}:artifact:{index}"
        )
        title = (
            str(artifact.get("title") or "").strip()
            or str(artifact.get("name") or "").strip()
            or artifact_id
        )
        kind = (
            str(artifact.get("kind") or "").strip()
            or str(artifact.get("type") or "").strip()
            or str(artifact.get("artifact_type") or "").strip()
            or "artifact"
        )
        mime_type = (
            str(artifact.get("mime_type") or "").strip()
            or str(artifact.get("content_type") or "").strip()
            or None
        )
        _emit_trace_call(
            agent_trace_service.emit_artifact_created(
                trace_context,
                artifact_id,
                kind,
                title,
                mime_type,
            ),
            operation=f"artifact.created:{run_id}:{index}",
        )


def _elapsed_ms_between_iso(started_at: Any, ended_at: Any) -> Optional[float]:
    started_raw = str(started_at or "").strip()
    ended_raw = str(ended_at or "").strip()
    if not started_raw or not ended_raw:
        return None
    try:
        started = datetime.fromisoformat(started_raw.replace("Z", "+00:00")).astimezone(timezone.utc)
        ended = datetime.fromisoformat(ended_raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None
    return max(0.0, (ended - started).total_seconds() * 1000.0)


# Terminal run states are the run's durable source of truth. They are written
# synchronously and confirmed (below) instead of fire-and-forget, so a transient
# DB error cannot leave a run marked complete in memory but unwritten in
# Postgres — which would rehydrate as a stuck/resultless run across a redeploy.
_TERMINAL_DURABLE_STATES = frozenset(
    {"completed", "failed", "timeout", "cancelled", "stopped"}
)
_MAX_TERMINAL_PERSIST_ATTEMPTS = 4
_TERMINAL_PERSIST_RETRY_BACKOFF_SECONDS = 0.2


def _report_durability_failure(
    context: str,
    exc: BaseException,
    *,
    run: Optional[Dict[str, Any]] = None,
) -> None:
    """Single seam for run-persistence durability failures — loud, never silent.

    Delegates to durability_signal so the failure surfaces as an ERROR log, a
    Sentry capture, and a best-effort activity-ledger dead-letter event.
    """
    from server_modules import durability_signal  # noqa: PLC0415

    workspace_id = _run_workspace_id(run) if isinstance(run, dict) else None
    tenant_id = _run_tenant_id(run) if isinstance(run, dict) else None
    run_id = str(run.get("run_id") or "") if isinstance(run, dict) else None
    durability_signal.capture_durability_failure(
        context,
        exc,
        workspace_id=workspace_id,
        tenant_id=tenant_id,
        run_id=run_id or None,
        event_class="run_persistence_dead_letter",
    )


def _reconcile_durable_version(run_id: str, run: Dict[str, Any]) -> Optional[int]:
    """Re-read the persisted run version and sync it into memory.

    Called after a version conflict so a retry writes on top of the current DB
    version instead of swallowing the terminal state.
    """
    try:
        persisted = run_state_repository.sync_get_live_run(run_id)
    except Exception as exc:
        LOGGER.warning("Failed to re-read persisted run version for %s: %s", run_id, exc)
        return None
    if not isinstance(persisted, dict):
        return None
    try:
        version = max(0, int(persisted.get("_durable_version") or 0))
    except Exception:
        return None
    run["_durable_version"] = version
    return version


def _persist_run_snapshot_confirmed(
    run_id: str,
    run: Dict[str, Any],
    *,
    state: str,
    trace_id: str,
    payload: Dict[str, Any],
    expected_version: int,
) -> None:
    """Persist a terminal snapshot synchronously with bounded retry + reconcile.

    The in-memory ``_durable_version`` is advanced ONLY after the write is
    confirmed. On a version conflict we reconcile against the persisted row and
    retry; on a transient DB error we back off and retry. If bounded retries are
    exhausted we surface loudly and leave the version untouched, so in-memory and
    DB stay in agreement and a later write can repair the run.
    """
    attempt_version = expected_version
    last_error: Optional[BaseException] = None
    for attempt in range(1, _MAX_TERMINAL_PERSIST_ATTEMPTS + 1):
        payload["_durable_version"] = attempt_version
        try:
            new_version = run_state_repository.sync_update_live_run_if_version_matches(
                run_id,
                _run_workspace_id(run),
                _run_tenant_id(run),
                state,
                payload,
                trace_id,
                expected_version=attempt_version,
            )
        except Exception as exc:  # transient DB error — retry within bounds
            last_error = exc
            LOGGER.warning(
                "Terminal run persistence attempt %d/%d failed for %s (%s): %s",
                attempt,
                _MAX_TERMINAL_PERSIST_ATTEMPTS,
                run_id,
                state,
                exc,
            )
            time.sleep(_TERMINAL_PERSIST_RETRY_BACKOFF_SECONDS * attempt)
            continue

        if new_version is not None:
            run["_durable_version"] = int(new_version)
            return

        # Version conflict: another writer advanced the row. Reconcile against
        # the persisted version and retry the terminal write on top of it.
        last_error = run_state_repository.RunStateVersionConflictError(
            f"terminal snapshot conflict for {run_id} at version {attempt_version}"
        )
        reconciled = _reconcile_durable_version(run_id, run)
        if reconciled is None:
            time.sleep(_TERMINAL_PERSIST_RETRY_BACKOFF_SECONDS * attempt)
        elif reconciled == attempt_version:
            attempt_version = reconciled + 1
        else:
            attempt_version = reconciled

    _report_durability_failure(
        f"terminal run persistence for {run_id} (state={state}) after "
        f"{_MAX_TERMINAL_PERSIST_ATTEMPTS} attempts",
        last_error or RuntimeError("unresolved version conflict"),
        run=run,
    )


def _persist_run_repository_snapshot(
    run_id: str,
    run: Dict[str, Any],
    *,
    state: str,
    trace_id: str,
) -> None:
    expected_version = _run_durable_version(run)
    payload = _serialize_run_for_durable_repository(run_id, run)
    payload["_durable_version"] = expected_version

    if str(state or "").strip() in _TERMINAL_DURABLE_STATES:
        _persist_run_snapshot_confirmed(
            run_id,
            run,
            state=state,
            trace_id=trace_id,
            payload=payload,
            expected_version=expected_version,
        )
        return

    async def _write_snapshot() -> int:
        result = await run_state_repository.update_live_run_if_version_matches(
            run_id=run_id,
            workspace_id=_run_workspace_id(run),
            tenant_id=_run_tenant_id(run),
            state=state,
            payload=payload,
            trace_id=trace_id,
            expected_version=expected_version,
        )
        if result is None:
            raise run_state_repository.RunStateVersionConflictError(
                f"Durable snapshot conflict for {run_id} at version {expected_version}"
            )
        # Advance the in-memory version ONLY after the write is confirmed, so a
        # failed async write leaves memory and DB in agreement (self-heals on the
        # next write) instead of desyncing the run permanently.
        run["_durable_version"] = int(result)
        return int(result)

    try:
        run_state_repository.dispatch_repository_call(
            _write_snapshot(),
            operation=f"update_live_run_if_version_matches:{run_id}:{state}:{expected_version}",
        )
    except Exception as exc:
        _report_durability_failure(f"live run repository dispatch for {run_id}", exc, run=run)


def _record_run_repository_transition(
    run_id: str,
    *,
    from_state: Any,
    to_state: str,
    actor: str,
    trace_id: str,
) -> None:
    if str(from_state or "").strip() == str(to_state or "").strip():
        return
    try:
        run_state_repository.dispatch_repository_call(
            run_state_repository.record_transition(
                run_id=run_id,
                from_state=str(from_state or "").strip() or "unknown",
                to_state=to_state,
                actor=actor,
                trace_id=trace_id,
            ),
            operation=f"record_transition:{run_id}:{to_state}",
        )
    except Exception as exc:
        _report_durability_failure(f"run transition repository write for {run_id}", exc)


def _archive_run_repository_payload(
    run_id: str,
    run: Dict[str, Any],
    *,
    final_state: str,
    trace_id: str,
) -> None:
    try:
        run_state_repository.dispatch_repository_call(
            run_state_repository.archive_run(
                run_id=run_id,
                final_state=final_state,
                payload=dict(run),
                trace_id=trace_id,
            ),
            operation=f"archive_run:{run_id}:{final_state}",
        )
    except Exception as exc:
        LOGGER.warning("Failed to dispatch run archive repository write for %s: %s", run_id, exc)


def _emit_run_transition_outbox_event(
    run_id: str,
    *,
    run: Dict[str, Any],
    from_state: Any,
    to_state: str,
    actor: str,
    trace_id: str,
) -> None:
    if str(from_state or "").strip() == str(to_state or "").strip():
        return
    try:
        status_token = str(to_state or "").strip().lower()
        outbox_service.emit_run_transition_event(
            run_id=run_id,
            tenant_id=_run_tenant_id(run),
            workspace_id=_run_workspace_id(run),
            from_state=str(from_state or "").strip() or "unknown",
            to_state=to_state,
            actor=actor,
            trace_id=trace_id,
            # Everything a reader needs to get from the row to the cause,
            # carried WITH the transition. All of it is already on `run`.
            agent_install_id=_run_agent_install_id(run),
            failure_reason=(
                _run_failure_summary(run, status_token)
                if status_token in {"failed", "timeout"}
                else ""
            ),
        )
    except Exception as exc:
        LOGGER.warning("Failed to emit outbox run transition for %s: %s", run_id, exc)


def _emit_artifact_outbox_events(
    run_id: str,
    *,
    run: Dict[str, Any],
    trace_id: str,
) -> None:
    result_data = run.get("result_data") if isinstance(run.get("result_data"), dict) else {}
    outputs = result_data.get("outputs") if isinstance(result_data.get("outputs"), dict) else {}
    artifacts = outputs.get("artifacts") if isinstance(outputs.get("artifacts"), list) else []
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict):
            continue
        try:
            outbox_service.emit_artifact_created_event(
                run_id=run_id,
                tenant_id=_run_tenant_id(run),
                workspace_id=_run_workspace_id(run),
                artifact=artifact,
                trace_id=trace_id,
                index=index,
            )
        except Exception as exc:
            LOGGER.warning("Failed to emit outbox artifact event for %s[%s]: %s", run_id, index, exc)


def get_pending_confirmation(run: Dict[str, Any]) -> Dict[str, Any]:
    pending = run.get("pending_confirmation")
    if isinstance(pending, dict):
        return pending
    legacy = run.get("pending_approval")
    if isinstance(legacy, dict):
        return legacy
    return {}


def set_pending_confirmation(run: Dict[str, Any], payload: Optional[Dict[str, Any]]) -> None:
    run["pending_confirmation"] = payload
    run["pending_approval"] = payload


def clear_pending_confirmation(run: Dict[str, Any]) -> None:
    run["pending_confirmation"] = None
    run["pending_approval"] = None


def is_valid_email_recipient(value: Any) -> bool:
    token = str(value or "").strip()
    if not token:
        return False
    return bool(EMAIL_RECIPIENT_PATTERN.match(token))


def _approval_marker_payload(metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(metadata, dict):
        return {}
    payload: Dict[str, Any] = {}
    for key in (
        "approval_node_key",
        "connector",
        "connector_action",
        "target",
        "approval_target",
        "kind",
    ):
        value = metadata.get(key)
        token = str(value or "").strip()
        if token:
            payload[key] = token
    for key in ("approval_actions", "approval_capabilities", "approval_labels"):
        values = metadata.get(key)
        if isinstance(values, list):
            tokens = [str(item).strip() for item in values if str(item).strip()]
            if tokens:
                payload[key] = tokens
    email_preview = metadata.get("email_preview")
    if isinstance(email_preview, dict):
        normalized_preview = {
            "recipient": str(email_preview.get("recipient") or "").strip() or None,
            "subject": str(email_preview.get("subject") or "").strip() or None,
            "body_preview": str(email_preview.get("body_preview") or "").strip() or None,
            "connector": str(email_preview.get("connector") or "").strip() or None,
            "action_id": str(email_preview.get("action_id") or "").strip() or None,
            "artifact_reference": str(email_preview.get("artifact_reference") or "").strip() or None,
        }
        normalized_preview = {key: value for key, value in normalized_preview.items() if value}
        if normalized_preview:
            payload["email_preview"] = normalized_preview
    return payload


def approval_resolution_fingerprint(metadata: Optional[Dict[str, Any]]) -> Optional[str]:
    payload = _approval_marker_payload(metadata)
    if not payload:
        return None
    text = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def resolved_approval_marker_for_metadata(
    context: Optional[Dict[str, Any]],
    approval_metadata: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not isinstance(context, dict):
        return None
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    markers = metadata.get(RESOLVED_APPROVAL_MARKERS_KEY)
    if not isinstance(markers, list):
        return None
    fingerprint = approval_resolution_fingerprint(approval_metadata)
    if not fingerprint:
        return None
    for item in markers:
        if not isinstance(item, dict):
            continue
        if str(item.get("fingerprint") or "").strip() != fingerprint:
            continue
        if str(item.get("decision") or "").strip().lower() != "approved":
            continue
        return dict(item)
    return None


def record_run_approval_resolution(
    run: Dict[str, Any],
    pending: Optional[Dict[str, Any]],
    *,
    decision_text: str,
    decision_note: str,
    approved: bool,
    rejected: bool,
    escalated: bool,
) -> None:
    if not isinstance(run, dict):
        return
    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    pending_payload = pending if isinstance(pending, dict) else {}
    approval_metadata = pending_payload.get("metadata") if isinstance(pending_payload.get("metadata"), dict) else {}
    fingerprint = approval_resolution_fingerprint(approval_metadata)
    approval_id = str(pending_payload.get("approval_id") or "").strip() or None
    resolved_at = str(pending_payload.get("resolved_at") or "").strip() or None
    marker = {
        "approval_id": approval_id,
        "decision": ("approved" if approved else "escalated" if escalated else "rejected"),
        "raw_decision": str(decision_text or "").strip().lower(),
        "note": str(decision_note or "").strip() or None,
        "approved": bool(approved),
        "rejected": bool(rejected),
        "escalated": bool(escalated),
        "resolved_at": resolved_at,
        "fingerprint": fingerprint,
        "connector": str(approval_metadata.get("connector") or "").strip() or None,
        "connector_action": str(approval_metadata.get("connector_action") or "").strip() or None,
        "target": str(
            approval_metadata.get("approval_target")
            or approval_metadata.get("target")
            or ""
        ).strip()
        or None,
        "metadata": _approval_marker_payload(approval_metadata) or None,
    }
    metadata["last_approval_result"] = marker
    if approved and fingerprint:
        markers = metadata.get(RESOLVED_APPROVAL_MARKERS_KEY)
        next_markers = [dict(item) for item in markers if isinstance(item, dict)] if isinstance(markers, list) else []
        next_markers = [
            item
            for item in next_markers
            if str(item.get("fingerprint") or "").strip() != fingerprint
        ]
        next_markers.append(marker)
        if len(next_markers) > 50:
            next_markers = next_markers[-50:]
        metadata[RESOLVED_APPROVAL_MARKERS_KEY] = next_markers
    context["metadata"] = metadata
    run["context"] = context


def _durable_approval_request_payload(
    run_id: str,
    run: Dict[str, Any],
    *,
    prompt: str,
    approval_id: str,
    correlation_id: str,
    requested_at: str,
    expires_at: str,
    ttl_seconds: int,
    source: str,
    approval_actions: list[str],
    approval_target: Optional[str],
    safe_metadata: Dict[str, Any],
) -> Dict[str, Any]:
    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    context_metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    payload = {
        "approval_id": approval_id,
        "run_id": run_id,
        "workspace_id": _run_workspace_id(run),
        "tenant_id": _run_tenant_id(run),
        "owner_user_id": str(context_metadata.get("owner_user_id") or "").strip() or None,
        "owner_email": str(context_metadata.get("owner_email") or "").strip().lower() or None,
        "agent_role": str(context_metadata.get("agent_role") or "").strip() or None,
        "prompt": prompt,
        "requested_at": requested_at,
        "expires_at": expires_at,
        "ttl_seconds": ttl_seconds,
        "correlation_id": correlation_id,
        "source": source,
        "scope": APPROVAL_SCOPE_ONCE,
        "reusable": False,
        "consequence": APPROVAL_SCOPE_CONSEQUENCE,
        "actions": list(approval_actions),
        "target": approval_target,
        "metadata": dict(safe_metadata),
        "email_preview": safe_metadata.get("email_preview") if isinstance(safe_metadata.get("email_preview"), dict) else None,
        "labels": [
            str(item or "").strip()
            for item in (safe_metadata.get("approval_labels") if isinstance(safe_metadata.get("approval_labels"), list) else [])
            if str(item or "").strip()
        ],
        "capabilities": [
            str(item or "").strip()
            for item in (safe_metadata.get("approval_capabilities") if isinstance(safe_metadata.get("approval_capabilities"), list) else [])
            if str(item or "").strip()
        ],
    }
    return payload


def begin_run_pending_confirmation(
    run_id: str,
    prompt: str,
    *,
    runs_by_id: Dict[str, Dict[str, Any]],
    default_approval_ttl_seconds: int,
    approval_correlation_id_fn: Callable[..., str],
    append_approval_audit_fn: Callable[..., Any],
    json_safe_fn: Callable[[Any], Any],
    emit_log_fn: Callable[..., Any],
    set_run_status_fn: Callable[[str, str], Any],
    utc_now_fn: Callable[[], Any],
    utc_now_iso_fn: Callable[[], str],
    source: str = "runtime",
    metadata: Optional[Dict[str, Any]] = None,
    emit_pause_required: bool = False,
) -> Dict[str, Any]:
    run = runs_by_id.get(run_id)
    if not isinstance(run, dict):
        raise RuntimeError("Run ID not found.")
    context = run.get("context")
    context_metadata = {}
    if isinstance(context, dict):
        raw_metadata = context.get("metadata")
        if isinstance(raw_metadata, dict):
            context_metadata = raw_metadata
    configured_ttl = context_metadata.get("approval_ttl_seconds") if isinstance(context_metadata, dict) else None
    ttl_seconds = default_approval_ttl_seconds
    if isinstance(configured_ttl, (int, float)):
        ttl_seconds = int(configured_ttl)
    ttl_seconds = max(30, min(1800, ttl_seconds))
    requested_at = utc_now_iso_fn()
    expires_at = (utc_now_fn() + timedelta(seconds=ttl_seconds)).isoformat().replace("+00:00", "Z")
    approval_id = str(uuid.uuid4())
    correlation_id = approval_correlation_id_fn(approval_id, run_id=run_id)
    safe_metadata = json_safe_fn(metadata if isinstance(metadata, dict) else {})
    approval_actions = [
        str(item).strip()
        for item in (safe_metadata.get("approval_actions") if isinstance(safe_metadata.get("approval_actions"), list) else [])
        if str(item).strip()
    ]
    approval_target = str(safe_metadata.get("target") or "").strip() or None
    payload = {
        "approval_id": approval_id,
        "correlation_id": correlation_id,
        "status": "waiting",
        "requested_at": requested_at,
        "expires_at": expires_at,
        "prompt": prompt,
        "ttl_seconds": ttl_seconds,
        "scope": APPROVAL_SCOPE_ONCE,
        "reusable": False,
        "consequence": APPROVAL_SCOPE_CONSEQUENCE,
        "actions": approval_actions,
        "target": approval_target,
        "metadata": safe_metadata,
    }
    durable_payload = _durable_approval_request_payload(
        run_id,
        run,
        prompt=prompt,
        approval_id=approval_id,
        correlation_id=correlation_id,
        requested_at=requested_at,
        expires_at=expires_at,
        ttl_seconds=ttl_seconds,
        source=source,
        approval_actions=approval_actions,
        approval_target=approval_target,
        safe_metadata=safe_metadata,
    )
    try:
        durable_request = run_state_repository.sync_create_or_update_approval_request(
            run_id,
            approval_id,
            durable_payload,
            "system",
            _run_trace_id(run, fallback=correlation_id),
            metadata=safe_metadata,
            expires_at=expires_at,
        )
    except run_state_repository.RunStatePersistenceError:
        durable_request = durable_payload
    if isinstance(durable_request, dict):
        payload["_durable_approval_version"] = int(durable_request.get("version") or 0)
    set_pending_confirmation(run, payload)
    try:
        outbox_service.emit_approval_requested_event(
            approval_id=approval_id,
            run_id=run_id,
            tenant_id=_run_tenant_id(run),
            workspace_id=_run_workspace_id(run),
            prompt=prompt,
            ttl_seconds=ttl_seconds,
            expires_at=expires_at,
            correlation_id=correlation_id,
            source=source,
            actor="system",
            target=approval_target,
            actions=approval_actions,
            metadata=safe_metadata,
            trace_id=_run_trace_id(run, fallback=correlation_id),
        )
    except Exception as exc:
        LOGGER.warning("Failed to emit outbox approval_requested for %s: %s", run_id, exc)
    trace_context = _resume_run_trace_context(run)
    if trace_context is not None:
        _emit_trace_call(
            agent_trace_service.emit_approval_requested(
                trace_context,
                approval_id,
                str(safe_metadata.get("kind") or source or "approval").strip() or "approval",
                str(prompt or "").strip() or "Approval required.",
                str(safe_metadata.get("description") or safe_metadata.get("summary") or "").strip() or None,
                str(safe_metadata.get("approval_node_key") or approval_id).strip() or approval_id,
            ),
            operation=f"approval.requested:{run_id}:{approval_id}",
        )
    emit_log_fn(
        run["logs"],
        "warn",
        prompt,
        event="approval_requested",
        data={
            "approval_id": approval_id,
            "correlation_id": correlation_id,
            "ttl_seconds": ttl_seconds,
            "expires_at": expires_at,
            "scope": APPROVAL_SCOPE_ONCE,
            "reusable": False,
            **safe_metadata,
        },
    )
    append_approval_audit_fn(
        approval_id=approval_id,
        stage="requested",
        actor="system",
        source=source,
        run_id=run_id,
        note=prompt,
        correlation_id=correlation_id,
        metadata={
            "ttl_seconds": ttl_seconds,
            "expires_at": expires_at,
            "scope": APPROVAL_SCOPE_ONCE,
            "reusable": False,
            **(safe_metadata if isinstance(safe_metadata, dict) else {}),
        },
    )
    emit_log_fn(
        run["logs"],
        "info",
        "Approval request is waiting for user resolution.",
        event="approval_waiting",
        data={
            "approval_id": approval_id,
            "correlation_id": correlation_id,
            "ttl_seconds": ttl_seconds,
            "expires_at": expires_at,
        },
    )
    append_approval_audit_fn(
        approval_id=approval_id,
        stage="waiting",
        actor="system",
        source=source,
        run_id=run_id,
        correlation_id=correlation_id,
    )
    set_run_status_fn(run_id, "waiting_for_input")
    if emit_pause_required:
        run["logs"].put("__PAUSE_REQUIRED__")
    return payload


def begin_run_pending_approval(
    run_id: str,
    prompt: str,
    *,
    begin_run_pending_confirmation_fn: Callable[..., Dict[str, Any]],
    source: str = "runtime",
    metadata: Optional[Dict[str, Any]] = None,
    emit_pause_required: bool = False,
) -> Dict[str, Any]:
    return begin_run_pending_confirmation_fn(
        run_id,
        prompt,
        source=source,
        metadata=metadata,
        emit_pause_required=emit_pause_required,
    )


def wait_for_human_response(
    run_id: str,
    prompt: str,
    *,
    runs_by_id: Dict[str, Dict[str, Any]],
    begin_run_pending_confirmation_fn: Callable[..., Dict[str, Any]],
    clear_pending_confirmation_fn: Callable[[Dict[str, Any]], None],
    get_pending_confirmation_fn: Callable[[Dict[str, Any]], Dict[str, Any]],
    set_pending_confirmation_fn: Callable[[Dict[str, Any], Optional[Dict[str, Any]]], None],
    approval_correlation_id_fn: Callable[..., str],
    append_approval_audit_fn: Callable[..., Any],
    emit_log_fn: Callable[..., Any],
    set_run_status_fn: Callable[[str, str], Any],
    utc_now_iso_fn: Callable[[], str],
    approval_ttl_seconds: int,
    monotonic_fn: Callable[[], float] = time.monotonic,
    queue_empty_exception: type[BaseException] = queue.Empty,
    source: str = "runtime_wait",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    run = runs_by_id[run_id]
    existing_pending = get_pending_confirmation_fn(run)
    resume_token = run.get("_resume_confirmation_token") if isinstance(run.get("_resume_confirmation_token"), dict) else None
    if isinstance(resume_token, dict):
        token_prompt = str(resume_token.get("prompt") or "").strip()
        token_decision = str(resume_token.get("decision") or "").strip()
        if token_decision and (not token_prompt or token_prompt == str(prompt or "").strip()):
            if not isinstance(existing_pending, dict):
                existing_pending = {
                    "approval_id": str(resume_token.get("approval_id") or "").strip() or "restored-approval",
                    "correlation_id": str(resume_token.get("correlation_id") or "").strip()
                    or approval_correlation_id_fn(
                        str(resume_token.get("approval_id") or "").strip() or "restored-approval",
                        run_id=run_id,
                    ),
                    "status": "resolved",
                    "scope": str(resume_token.get("scope") or "once").strip() or "once",
                    "reusable": bool(resume_token.get("reusable", False)),
                    "prompt": token_prompt or str(prompt or "").strip(),
                    "decision": token_decision,
                    "note": str(resume_token.get("note") or "").strip(),
                    "resolved_at": str(resume_token.get("resolved_at") or "").strip() or utc_now_iso_fn(),
                }
            elif str(existing_pending.get("status") or "").strip().lower() != "resolved":
                existing_pending = dict(existing_pending)
                existing_pending["status"] = "resolved"
                existing_pending["prompt"] = str(existing_pending.get("prompt") or token_prompt or prompt).strip()
                existing_pending["decision"] = token_decision
                existing_pending["note"] = str(resume_token.get("note") or existing_pending.get("note") or "").strip()
                existing_pending["resolved_at"] = (
                    str(resume_token.get("resolved_at") or "").strip() or utc_now_iso_fn()
                )
                existing_pending["approval_id"] = (
                    str(existing_pending.get("approval_id") or "").strip()
                    or str(resume_token.get("approval_id") or "").strip()
                    or "restored-approval"
                )
                existing_pending["correlation_id"] = (
                    str(existing_pending.get("correlation_id") or "").strip()
                    or str(resume_token.get("correlation_id") or "").strip()
                    or approval_correlation_id_fn(
                        str(existing_pending.get("approval_id") or "").strip() or "restored-approval",
                        run_id=run_id,
                    )
                )
            set_pending_confirmation_fn(run, existing_pending)
            run.pop("_resume_confirmation_token", None)
            existing_pending = get_pending_confirmation_fn(run)
    if isinstance(existing_pending, dict):
        approval_id = str(existing_pending.get("approval_id") or "").strip()
        if approval_id:
            approval_record = run_state_repository.sync_get_approval_record(approval_id)
            if isinstance(approval_record, dict):
                approval_decision_payload = (
                    approval_record.get("decision_payload")
                    if isinstance(approval_record.get("decision_payload"), dict)
                    else {}
                )
                durable_status = str(approval_record.get("status") or "").strip().lower()
                durable_resolution = str(
                    approval_record.get("resolution")
                    or approval_decision_payload.get("resolution")
                    or approval_decision_payload.get("decision")
                    or ""
                ).strip().lower()
                if durable_status in {"decision_submitted", "resolved", "approved", "rejected", "expired", "cancelled", "canceled", "dismissed"} and durable_resolution:
                    hydrated_pending = dict(existing_pending)
                    hydrated_pending["status"] = "resolved" if durable_status == "decision_submitted" else durable_status
                    hydrated_pending["decision"] = str(
                        hydrated_pending.get("decision")
                        or approval_decision_payload.get("decision")
                        or approval_decision_payload.get("resolution")
                        or durable_resolution
                    ).strip()
                    hydrated_pending["note"] = str(
                        hydrated_pending.get("note")
                        or approval_decision_payload.get("note")
                        or ""
                    ).strip()
                    hydrated_pending["resolved_at"] = str(
                        hydrated_pending.get("resolved_at")
                        or approval_record.get("resolved_at")
                        or approval_record.get("updated_at")
                        or utc_now_iso_fn()
                    ).strip() or utc_now_iso_fn()
                    hydrated_pending["correlation_id"] = (
                        str(hydrated_pending.get("correlation_id") or "").strip()
                        or str(approval_record.get("correlation_id") or "").strip()
                        or approval_correlation_id_fn(approval_id, run_id=run_id)
                    )
                    set_pending_confirmation_fn(run, hydrated_pending)
                    existing_pending = get_pending_confirmation_fn(run)
        existing_status = str(existing_pending.get("status") or "").strip().lower()
        existing_prompt = str(existing_pending.get("prompt") or "").strip()
        decision_raw_text = str(existing_pending.get("decision") or "").strip()
        if (
            existing_status == "resolved"
            and decision_raw_text
            and (not existing_prompt or existing_prompt == str(prompt or "").strip())
        ):
            approval_id = str(existing_pending.get("approval_id") or "").strip()
            correlation_id = str(existing_pending.get("correlation_id") or "").strip() or approval_correlation_id_fn(
                approval_id,
                run_id=run_id,
            )
            decision_text = decision_raw_text.lower()
            decision_note = str(existing_pending.get("note") or "").strip()
            approved = decision_text in APPROVAL_APPROVE_TOKENS
            rejected = decision_text in APPROVAL_REJECT_TOKENS
            escalated = decision_text in APPROVAL_ESCALATE_TOKENS
            if not approved and not rejected and not escalated:
                rejected = True
            run.pop("_resume_after_confirmation_scheduled", None)
            set_run_status_fn(run_id, "executing")
            emit_log_fn(
                run["logs"],
                "info",
                f"Decision received: {decision_text}",
                event="approval_received",
                data={
                    "approval_id": approval_id,
                    "correlation_id": correlation_id,
                    "decision": decision_text,
                    "scope": "once",
                    "reusable": False,
                },
            )
            append_approval_audit_fn(
                approval_id=approval_id,
                stage="received",
                decision=decision_text,
                actor="user",
                source="runtime_wait",
                run_id=run_id,
                note=decision_note or decision_raw_text,
                correlation_id=correlation_id,
                metadata={"scope": "once", "reusable": False, "resumed_after_restart": True},
            )
            emit_log_fn(
                run["logs"],
                "info" if approved else "warn",
                "Confirmation resolved.",
                event="approval_resolved",
                data={
                    "approval_id": approval_id,
                    "correlation_id": correlation_id,
                    "decision": decision_text,
                    "approved": approved,
                    "rejected": bool(rejected),
                    "escalated": bool(escalated),
                    "scope": "once",
                    "reusable": False,
                    "resumed_after_restart": True,
                },
            )
            append_approval_audit_fn(
                approval_id=approval_id,
                stage="resolved",
                decision=("approved" if approved else "escalated" if escalated else "rejected"),
                actor="runtime",
                source="runtime_wait",
                run_id=run_id,
                correlation_id=correlation_id,
                metadata={
                    "raw_decision": decision_text,
                    "approved": bool(approved),
                    "rejected": bool(rejected),
                    "escalated": bool(escalated),
                    "scope": "once",
                    "reusable": False,
                    "resumed_after_restart": True,
                },
            )
            elapsed_ms = _elapsed_ms_between_iso(existing_pending.get("requested_at"), existing_pending.get("resolved_at"))
            if elapsed_ms is not None:
                record_reliability_latency_sample(
                    "approval_propagation_latency_ms",
                    elapsed_ms,
                    recorded_at=str(existing_pending.get("resolved_at") or "").strip() or None,
                    metadata={
                        "run_id": run_id,
                        "approval_id": approval_id,
                        "decision": decision_text,
                        "resumed_after_restart": True,
                    },
                )
            try:
                run_state_repository.sync_record_approval_resolution(
                    run_id,
                    approval_id,
                    ("approved" if approved else "escalated" if escalated else "expired" if decision_text == "expired" else "rejected"),
                    "runtime",
                    correlation_id,
                    note=decision_note,
                )
            except run_state_repository.RunStatePersistenceError as exc:
                LOGGER.warning("Failed to sync recovered approval resolution for %s/%s: %s", run_id, approval_id, exc)
            record_run_approval_resolution(
                run,
                existing_pending,
                decision_text=decision_text,
                decision_note=decision_note,
                approved=bool(approved),
                rejected=bool(rejected),
                escalated=bool(escalated),
            )
            clear_pending_confirmation_fn(run)
            run.pop("_resume_confirmation_token", None)
            return {
                "approval_id": approval_id,
                "correlation_id": correlation_id,
                "decision": decision_text,
                "raw_decision": decision_raw_text,
                "note": decision_note or None,
                "approved": bool(approved),
                "rejected": bool(rejected),
                "escalated": bool(escalated),
            }
    pending_payload = begin_run_pending_confirmation_fn(
        run_id,
        prompt,
        source=source,
        metadata=metadata,
        emit_pause_required=True,
    )
    approval_id = str(pending_payload.get("approval_id") or "").strip()
    correlation_id = str(pending_payload.get("correlation_id") or "").strip()
    ttl_seconds = int(pending_payload.get("ttl_seconds") or approval_ttl_seconds)
    deadline = monotonic_fn() + ttl_seconds
    while True:
        remaining = deadline - monotonic_fn()
        if remaining <= 0:
            pending = get_pending_confirmation_fn(run)
            pending["status"] = "expired"
            pending["expired_at"] = utc_now_iso_fn()
            set_pending_confirmation_fn(run, pending)
            run_state_repository.sync_record_approval_resolution(
                run_id,
                approval_id,
                "expired",
                "system",
                correlation_id,
                note="Confirmation timeout reached while waiting for user decision.",
            )
            emit_log_fn(
                run["logs"],
                "error",
                "Confirmation request expired before user decision.",
                event="approval_timeout",
                data={"approval_id": approval_id, "correlation_id": correlation_id, "ttl_seconds": ttl_seconds},
            )
            append_approval_audit_fn(
                approval_id=approval_id,
                stage="timeout",
                decision="timeout",
                actor="system",
                source="runtime_wait",
                run_id=run_id,
                note="Confirmation timeout reached while waiting for user decision.",
                correlation_id=correlation_id,
            )
            raise RuntimeError("Confirmation timeout reached while waiting for user decision.")
        try:
            decision_raw = run["input_queue"].get(timeout=remaining)
        except queue_empty_exception:
            continue

        incoming_approval_id: Optional[str] = None
        decision_raw_text = ""
        decision_text = ""
        decision_note = ""
        if isinstance(decision_raw, dict):
            incoming_approval_id = str(decision_raw.get("approval_id") or "").strip() or None
            decision_raw_text = str(decision_raw.get("decision") or "").strip()
            decision_text = decision_raw_text.lower()
            decision_note = str(decision_raw.get("note") or "").strip()
        else:
            decision_raw_text = str(decision_raw or "").strip()
            decision_text = decision_raw_text.lower()

        if incoming_approval_id and incoming_approval_id != approval_id:
            emit_log_fn(
                run["logs"],
                "warn",
                "Ignored stale confirmation resolution for different approval_id.",
                event="approval_ignored",
                data={
                    "approval_id": incoming_approval_id,
                    "expected_approval_id": approval_id,
                    "correlation_id": correlation_id,
                },
            )
            append_approval_audit_fn(
                approval_id=incoming_approval_id,
                stage="ignored",
                decision="ignored",
                actor="runtime",
                source="runtime_wait",
                run_id=run_id,
                note=f"Expected approval_id={approval_id}",
                correlation_id=correlation_id,
                metadata={"expected_approval_id": approval_id},
            )
            continue

        pending = get_pending_confirmation_fn(run)
        pending["status"] = "resolved"
        pending["resolved_at"] = utc_now_iso_fn()
        pending["decision"] = decision_text
        set_pending_confirmation_fn(run, pending)
        run.pop("_resume_confirmation_token", None)
        elapsed_ms = _elapsed_ms_between_iso(pending.get("requested_at"), pending.get("resolved_at"))
        set_run_status_fn(run_id, "executing")
        emit_log_fn(
            run["logs"],
            "info",
            f"Decision received: {decision_text}",
            event="approval_received",
            data={
                "approval_id": approval_id,
                "correlation_id": correlation_id,
                "decision": decision_text,
                "scope": "once",
                "reusable": False,
            },
        )
        append_approval_audit_fn(
            approval_id=approval_id,
            stage="received",
            decision=decision_text,
            actor="user",
            source="runtime_wait",
            run_id=run_id,
            note=decision_note or str(decision_raw),
            correlation_id=correlation_id,
            metadata={"scope": "once", "reusable": False},
        )

        approved = decision_text in APPROVAL_APPROVE_TOKENS
        rejected = decision_text in APPROVAL_REJECT_TOKENS
        escalated = decision_text in APPROVAL_ESCALATE_TOKENS
        if not approved and not rejected and not escalated:
            rejected = True
        emit_log_fn(
            run["logs"],
            "info" if approved else "warn",
            "Confirmation resolved.",
            event="approval_resolved",
            data={
                "approval_id": approval_id,
                "correlation_id": correlation_id,
                "decision": decision_text,
                "approved": approved,
                "rejected": bool(rejected),
                "escalated": bool(escalated),
                "scope": "once",
                "reusable": False,
            },
        )
        append_approval_audit_fn(
            approval_id=approval_id,
            stage="resolved",
            decision=("approved" if approved else "escalated" if escalated else "rejected"),
            actor="runtime",
            source="runtime_wait",
            run_id=run_id,
            correlation_id=correlation_id,
            metadata={
                "raw_decision": decision_text,
                "approved": bool(approved),
                "rejected": bool(rejected),
                "escalated": bool(escalated),
                "scope": "once",
                "reusable": False,
            },
        )
        if elapsed_ms is not None:
            record_reliability_latency_sample(
                "approval_propagation_latency_ms",
                elapsed_ms,
                recorded_at=str(pending.get("resolved_at") or "").strip() or None,
                metadata={
                    "run_id": run_id,
                    "approval_id": approval_id,
                    "decision": decision_text,
                    "resumed_after_restart": False,
                },
            )
        run_state_repository.sync_record_approval_resolution(
            run_id,
            approval_id,
            ("approved" if approved else "escalated" if escalated else "rejected"),
            "runtime",
            correlation_id,
            note=decision_note or str(decision_raw),
        )
        record_run_approval_resolution(
            run,
            pending,
            decision_text=decision_text,
            decision_note=decision_note,
            approved=bool(approved),
            rejected=bool(rejected),
            escalated=bool(escalated),
        )
        clear_pending_confirmation_fn(run)
        return {
            "approval_id": approval_id,
            "correlation_id": correlation_id,
            "decision": decision_text,
            "raw_decision": decision_raw_text,
            "note": decision_note or None,
            "approved": bool(approved),
            "rejected": bool(rejected),
            "escalated": bool(escalated),
        }


def activate_live_run(
    run_id: str,
    run: Dict[str, Any],
    *,
    selected_target: str,
    local_companion_target: str,
    defer_local_enqueue: bool,
    hydrate_run_memory_context_fn: Callable[[str, Dict[str, Any]], Any],
    enqueue_local_companion_run_fn: Callable[[str], Any],
    start_background_run_fn: Callable[[str], Any],
) -> str:
    if str(selected_target or "").strip().lower() == str(local_companion_target or "").strip().lower():
        try:
            hydrate_run_memory_context_fn(run_id, run)
        except Exception as exc:
            LOGGER.warning("Failed to replay outbox events on startup: %s", exc)
        if not defer_local_enqueue:
            enqueue_local_companion_run_fn(run_id)
        return run_id

    start_background_run_fn(run_id)
    return run_id


def create_live_run(
    engine: str,
    context: Optional[dict] = None,
    *,
    defer_local_enqueue: bool = False,
    runtime_db_path: Any,
    sync_acp_manager_paths_fn: Callable[..., Any],
    selected_execution_target_from_context_fn: Callable[[Dict[str, Any]], str],
    runs_by_id: Dict[str, Dict[str, Any]],
    run_queue_index: Dict[int, str],
    metrics_inc_fn: Callable[[str, int], Any],
    persist_live_run_state_fn: Callable[[str, Dict[str, Any]], Any],
    hydrate_run_memory_context_fn: Callable[[str, Dict[str, Any]], Any],
    enqueue_local_companion_run_fn: Callable[[str], Any],
    start_background_run_fn: Callable[[str], Any],
    local_companion_target: str,
    provider_profiles: Dict[str, Any],
    memory_enabled: bool,
    utc_now_iso_fn: Callable[[], str],
    inject_runtime_skill_defaults_fn: Optional[Callable[[Dict[str, Any]], Any]] = None,
    compute_tool_policy_precheck_fn: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    create_live_run_initial_fn: Optional[Callable[..., Any]] = None,
    record_run_transition_fn: Optional[Callable[..., Any]] = None,
    log_queue_factory: Callable[[], Any] = queue.Queue,
    input_queue_factory: Callable[[], Any] = queue.Queue,
    uuid4_fn: Callable[[], Any] = uuid.uuid4,
    utcnow_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> str:
    if create_live_run_initial_fn is None:
        create_live_run_initial_fn = run_state_repository.sync_create_live_run_initial
    if record_run_transition_fn is None:
        record_run_transition_fn = run_state_repository.sync_record_transition
    sync_acp_manager_paths_fn(runtime_db_path=runtime_db_path)
    run_id = str(uuid4_fn())
    now = utcnow_fn().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    started_mono = monotonic_fn()
    log_queue = log_queue_factory()
    run_context = context or {}
    if isinstance(run_context, dict) and callable(inject_runtime_skill_defaults_fn):
        try:
            inject_runtime_skill_defaults_fn(run_context)
        except Exception as exc:
            LOGGER.warning("Failed to recover expired worker leases on startup: %s", exc)
    if engine == "orion" and isinstance(run_context, dict) and callable(compute_tool_policy_precheck_fn):
        metadata = run_context.get("metadata") if isinstance(run_context.get("metadata"), dict) else {}
        if isinstance(metadata, dict) and not isinstance(metadata.get("tool_policy_precheck"), dict):
            try:
                metadata["tool_policy_precheck"] = compute_tool_policy_precheck_fn(run_context)
                run_context["metadata"] = metadata
            except Exception:
                pass
    metadata = run_context.get("metadata") if isinstance(run_context.get("metadata"), dict) else {}
    runtime_profile_id = str(
        metadata.get("runtime_profile_id") or metadata.get("profile_id") or ""
    ).strip()
    runtime_profile = provider_profiles.get(runtime_profile_id) if runtime_profile_id else None
    runtime_profile_row = runtime_profile if isinstance(runtime_profile, dict) else {}
    initial_active_provider = str(
        runtime_profile_row.get("provider")
        or metadata.get("runtime_profile_provider")
        or run_context.get("provider")
        or ""
    ).strip()
    initial_active_model = str(
        runtime_profile_row.get("model")
        or metadata.get("runtime_profile_model")
        or run_context.get("model")
        or ""
    ).strip()
    initial_active_label = str(
        runtime_profile_row.get("label")
        or metadata.get("runtime_profile_label")
        or ""
    ).strip()
    selected_target = selected_execution_target_from_context_fn(run_context)
    run = build_live_run_record(
        run_id=run_id,
        engine=engine,
        context=run_context,
        now_iso=now,
        started_mono=started_mono,
        log_queue=log_queue,
        input_queue=input_queue_factory(),
        memory_enabled=memory_enabled,
        memory_updated_at=utc_now_iso_fn(),
        active_profile_id=runtime_profile_id or None,
        active_profile_label=initial_active_label or None,
        active_provider=initial_active_provider or None,
        active_model=initial_active_model or None,
    )
    register_live_run(
        run_id,
        run,
        runs_by_id=runs_by_id,
        run_queue_index=run_queue_index,
        metrics_inc_fn=metrics_inc_fn,
        persist_live_run_state_fn=persist_live_run_state_fn,
        create_live_run_initial_fn=create_live_run_initial_fn,
        record_transition_fn=record_run_transition_fn,
        serialize_durable_run_payload_fn=_serialize_run_for_durable_repository,
    )
    active_run_id = activate_live_run(
        run_id,
        run,
        selected_target=selected_target,
        local_companion_target=local_companion_target,
        defer_local_enqueue=defer_local_enqueue,
        hydrate_run_memory_context_fn=hydrate_run_memory_context_fn,
        enqueue_local_companion_run_fn=enqueue_local_companion_run_fn,
        start_background_run_fn=start_background_run_fn,
    )
    record_reliability_latency_sample(
        "run_enqueue_ack_latency_ms",
        max(0.0, (monotonic_fn() - started_mono) * 1000.0),
        recorded_at=utc_now_iso_fn(),
        metadata={
            "run_id": active_run_id,
            "engine": engine,
            "execution_target_selected": selected_target,
        },
    )
    return active_run_id


def transition_live_run_status(
    run_id: str,
    status: str,
    *,
    run: Optional[Dict[str, Any]],
    now_mono: float,
    now_iso: str,
    terminal_statuses: set[str],
    local_queue_lock: Any,
    local_pending_run_ids: List[str],
    local_claimed_runs: Dict[str, Any],
    archive_run_if_terminal_fn: Callable[[str, Dict[str, Any]], Any],
    remove_live_run_state_fn: Callable[[str], Any],
    sync_local_runtime_state_snapshot_fn: Callable[[], Any],
    persist_live_run_state_fn: Callable[[str, Dict[str, Any]], Any],
    run_queue_index: Dict[int, str],
    metrics_add_fn: Callable[[str, float], Any],
    metrics_inc_fn: Callable[[str, int], Any],
    parent_run_id: Optional[str] = None,
    refresh_parent_delegation_state_fn: Optional[Callable[..., Any]] = None,
) -> None:
    if not isinstance(run, dict):
        return

    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    tracer = get_tracer("server_modules.run_service")
    with tracer.start_as_current_span("run_service.transition_live_run_status") as span:
        previous = run.get("status")
        actor = (
            str(metadata.get("request_actor_type") or "").strip()
            or str(metadata.get("actor_type") or "").strip()
            or "runtime"
        )
        set_span_attributes(
            span,
            {
                "run_id": str(run_id or "").strip(),
                "state": str(status or "").strip(),
                "previous_state": str(previous or "").strip() or None,
                "actor": actor,
                "workspace_id": str(context.get("workspace_id") or metadata.get("workspace_id") or "default").strip() or "default",
                "tenant_id": str(context.get("tenant_id") or metadata.get("tenant_id") or "default").strip() or "default",
            },
        )
        try:
            with _suspend_run_persistence_notifications(run):
                if previous == "waiting_for_input" and status != "waiting_for_input":
                    wait_start = run.get("_hitl_wait_start_mono")
                    if isinstance(wait_start, (int, float)):
                        waited_ms = max(0.0, (now_mono - wait_start) * 1000.0)
                        run["_hitl_wait_total_ms"] = run.get("_hitl_wait_total_ms", 0.0) + waited_ms
                        metrics_add_fn("hitl_wait_sum_ms", waited_ms)
                        metrics_inc_fn("hitl_wait_count", 1)
                        run["_hitl_wait_start_mono"] = None

                if status == "waiting_for_input":
                    run["_hitl_wait_start_mono"] = now_mono
                    metrics_inc_fn("runs_waiting_for_input", 1)

                if status in {"completed", "failed", "timeout"} and run.get("_finished_mono") is None:
                    run["_finished_mono"] = now_mono
                    started = run.get("_started_mono")
                    if isinstance(started, (int, float)):
                        duration_ms = max(0.0, (now_mono - started) * 1000.0)
                        run["duration_ms"] = round(duration_ms, 2)
                        metrics_add_fn("run_duration_sum_ms", duration_ms)
                        metrics_inc_fn("run_duration_count", 1)
                    run["completed_at"] = now_iso
                    if status == "completed":
                        metrics_inc_fn("runs_completed", 1)
                    elif status == "failed":
                        metrics_inc_fn("runs_failed", 1)
                    elif status == "timeout":
                        metrics_inc_fn("runs_timeout", 1)

                run["status"] = status
                run["updated_at"] = now_iso
            trace_id = _run_trace_id(run, fallback=f"{span.get_span_context().trace_id:032x}" if span is not None else None)
            _persist_run_repository_snapshot(run_id, run, state=status, trace_id=trace_id)
            _record_run_repository_transition(
                run_id,
                from_state=previous,
                to_state=status,
                actor=actor,
                trace_id=trace_id,
            )
            _emit_run_transition_outbox_event(
                run_id,
                run=run,
                from_state=previous,
                to_state=status,
                actor=actor,
                trace_id=trace_id,
            )
            if status in {"completed", "failed", "timeout"}:
                trace_context = _resume_run_trace_context(run) if str(previous or "").strip() != str(status or "").strip() else None
                if status == "completed" and str(previous or "").strip() != "completed":
                    _publish_local_completion_summary_bridge(run_id, run)
                    try:
                        from server_modules import agent_completion_notification_service

                        metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
                        run_async_tool_call(
                            agent_completion_notification_service.notify_completion(
                                workspace_id=str(context.get("workspace_id") or run.get("workspace_id") or "").strip() or "default",
                                agent_name=str(metadata.get("agent_name") or metadata.get("deployed_agent_name") or run.get("agent_name") or "Agent").strip() or "Agent",
                                result_summary=run.get("result") or run.get("summary") or "Completed.",
                                artifact_url=str(metadata.get("artifact_url") or "").strip() or None,
                            )
                        )
                    except Exception:
                        pass
                if status in {"failed", "timeout"} and str(previous or "").strip() != str(status or "").strip():
                    # MAN-108 Bug 2: a task-assigned wake-up's run failing (e.g.
                    # runs_execution._honest_no_provider_error, or any other
                    # in-run exception) was previously invisible everywhere a
                    # human/agent could see it -- run_orion_mission only ever
                    # calls emit_log into that run's own ephemeral in-memory
                    # event buffer, never Python logging, and nothing ever told
                    # the originating task its run had died. The wake request
                    # itself is no help either: it's already marked "executed"
                    # the moment the turn was successfully dispatched (see
                    # runtime_heartbeat_service.build_heartbeat_run_callback),
                    # well before this run actually finishes or fails minutes
                    # later. task_id here is set by runtime_heartbeat_service.
                    # build_heartbeat_turn_request only for a task_assigned
                    # wakeup (see its own comment) -- a no-op for every other
                    # run, so this can't affect ordinary chat/workflow runs.
                    task_metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
                    failed_task_id = str(task_metadata.get("task_id") or "").strip()
                    if failed_task_id:
                        try:
                            from server_modules import project_tasks_service

                            failure_summary = _run_failure_summary(run, status)
                            failed_task_tenant_id = _run_tenant_id(run)
                            failed_task_workspace_id = _run_workspace_id(run)
                            run_async_tool_call(
                                project_tasks_service.update_task(
                                    tenant_id=failed_task_tenant_id,
                                    workspace_id=failed_task_workspace_id,
                                    task_id=failed_task_id,
                                    status="blocked",
                                )
                            )
                            # The status change and its EXPLANATION are two
                            # writes, deliberately: "blocked" has to land
                            # even if this does. Before it existed the run
                            # id survived only as prose inside the comment
                            # below, which nothing parses — so the board
                            # said "Blocked" and the cause was reachable
                            # from nowhere.
                            run_async_tool_call(
                                project_tasks_service.record_task_run_failure(
                                    tenant_id=failed_task_tenant_id,
                                    workspace_id=failed_task_workspace_id,
                                    task_id=failed_task_id,
                                    run_id=run_id,
                                    trace_id=(
                                        trace_id
                                        if agent_trace_service.is_linkable_trace_id(trace_id)
                                        else ""
                                    ),
                                    reason=failure_summary,
                                )
                            )
                            run_async_tool_call(
                                project_tasks_service.add_task_comment(
                                    tenant_id=failed_task_tenant_id,
                                    workspace_id=failed_task_workspace_id,
                                    task_id=failed_task_id,
                                    author_type="system",
                                    author_id="runtime",
                                    body=f"This task's run ({run_id}) did not complete: {failure_summary[:1500]}",
                                )
                            )
                        except Exception as exc:
                            LOGGER.warning(
                                "Failed to surface run failure onto task %s for run %s: %s",
                                failed_task_id,
                                run_id,
                                exc,
                            )
                if str(previous or "").strip() != str(status or "").strip():
                    if str((context.get("metadata") if isinstance(context.get("metadata"), dict) else {}).get("deployed_agent_id") or "").strip():
                        cost_cap_settlement_key = f"{run_id}:{status}"
                        if run.get("_deployed_agent_cost_cap_settlement_key") != cost_cap_settlement_key:
                            run["_deployed_agent_cost_cap_settlement_key"] = cost_cap_settlement_key
                            try:
                                run_async_tool_call(
                                    deployed_agent_cost_cap_service.settle_deployed_agent_monthly_cost_cap(
                                        run_id=run_id,
                                        run=run,
                                        status=status,
                                        now_iso=now_iso,
                                    )
                                )
                            except Exception as exc:
                                run.pop("_deployed_agent_cost_cap_settlement_key", None)
                                LOGGER.warning("Failed to settle deployed-agent cost cap for %s: %s", run_id, exc)
                    _emit_artifact_outbox_events(
                        run_id,
                        run=run,
                        trace_id=trace_id,
                    )
                    if trace_context is not None:
                        _emit_artifact_trace_events(run_id, run=run, trace_context=trace_context)
                        if status == "completed":
                            _emit_trace_call(
                                agent_trace_service.emit_trace_completed(
                                    trace_context,
                                    int(float(run.get("duration_ms") or 0.0)),
                                    None,
                                ),
                                operation=f"trace.completed:{run_id}",
                            )
                            _emit_trace_call(
                                agent_trace_service.finish_trace(
                                    trace_context,
                                    outcome="success",
                                    final_message_id=None,
                                ),
                                operation=f"trace.finish:{run_id}:success",
                            )
                        else:
                            _emit_trace_call(
                                agent_trace_service.emit_trace_failed(
                                    trace_context,
                                    "run_timeout" if status == "timeout" else "run_failed",
                                    str(run.get("error") or run.get("result") or f"Run ended with status {status}.")[:400],
                                    bool(status == "timeout"),
                                    None,
                                ),
                                operation=f"trace.failed:{run_id}:{status}",
                            )
                            # FAILED, not "partial": this branch only runs
                            # for status in {failed, timeout}, and "partial"
                            # claims work happened. WorkTab reads only
                            # needs_input, so a failed run used to render
                            # identically to a successful one.
                            _emit_trace_call(
                                agent_trace_service.finish_trace(
                                    trace_context,
                                    outcome=agent_trace_service.TRACE_OUTCOME_FAILED,
                                    final_message_id=None,
                                ),
                                operation=f"trace.finish:{run_id}:failed",
                            )
                machine_lease_service.reconcile_machine_lease_release(
                    run_id,
                    local_queue_lock=local_queue_lock,
                    local_pending_run_ids=local_pending_run_ids,
                    local_claimed_runs=local_claimed_runs,
                    sync_local_runtime_state_snapshot_fn=sync_local_runtime_state_snapshot_fn,
                )
                _archive_run_repository_payload(run_id, run, final_state=status, trace_id=trace_id)
                archive_run_if_terminal_fn(run_id, run)
                log_queue = run.get("logs")
                if log_queue is not None:
                    run_queue_index.pop(id(log_queue), None)
                remove_live_run_state_fn(run_id)
            else:
                _persist_live_run_state_with_version(
                    run_id,
                    run,
                    persist_live_run_state_fn=persist_live_run_state_fn,
                )

            if parent_run_id and status in terminal_statuses and callable(refresh_parent_delegation_state_fn):
                refresh_parent_delegation_state_fn(parent_run_id, triggering_run_id=run_id)
        except Exception as exc:
            try:
                span.record_exception(exc)
            except Exception:
                pass
            LOGGER.warning(
                "transition_live_run_status failed for %s -> %s: %s",
                str(run_id or "").strip() or "unknown",
                str(status or "").strip() or "unknown",
                str(exc or "unknown error"),
            )
            raise


def _publish_local_completion_summary_bridge(run_id: str, run: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    runtime_mode = _normalize_runtime_mode(metadata.get("runtime_mode"))
    if runtime_mode not in LOCAL_RUNTIME_MODES:
        return None
    runtime_selection = _runtime_selection_payload(metadata)
    hybrid_policy = runtime_selection.get("hybrid_policy") if isinstance(runtime_selection.get("hybrid_policy"), dict) else {}
    summary_bridge = hybrid_policy.get("summary_bridge") if isinstance(hybrid_policy.get("summary_bridge"), dict) else {}
    if not bool(summary_bridge.get("enabled")):
        return None
    allowed_payload_classes = list(summary_bridge.get("payload_classes") or [])
    if not allowed_payload_classes:
        return None

    from server_modules import workspace_scope as _ws

    workspace_id = _ws.resolve_workspace(
        None, context, metadata,
        site="run_service:build_step_summary_bridge",
    )
    summary_text = ""
    try:
        from server_modules import memory_service

        summary_text = memory_service._run_result_summary(run)
    except Exception:
        summary_text = str(run.get("result") or "").strip()
    summary_text = str(summary_text or "").strip()
    if not summary_text:
        return None

    payload_class = ""
    memory_layer: Optional[str] = None
    agent_install_id = _runtime_binding_install_id(metadata)
    if "specialist_outcome_summaries" in allowed_payload_classes and (agent_install_id or str(run_id or "").strip()):
        payload_class = "specialist_outcome_summaries"
    elif "bounded_local_private_summaries" in allowed_payload_classes:
        payload_class = "bounded_local_private_summaries"
        memory_layer = "local_private_memory"
    else:
        return None

    try:
        from server_modules import memory_service

        return memory_service.publish_hybrid_summary_bridge_payload(
            workspace_id,
            payload_class=payload_class,
            summary=summary_text,
            memory_layer=memory_layer,
            agent_install_id=agent_install_id,
            run_id=str(run_id or "").strip() or None,
            source_runtime_mode=runtime_mode,
            last_safe=True,
            allowed_payload_classes=allowed_payload_classes,
        )
    except Exception as exc:
        LOGGER.warning(
            "hybrid_summary_bridge_publish_failed",
            extra={
                "run_id": str(run_id or "").strip() or None,
                "workspace_id": workspace_id,
                "runtime_mode": runtime_mode,
                "error": str(exc)[:400],
            },
        )
        return None


def build_run_routing_preview_services(
    *,
    prepare_run_start_request: Any,
    compute_tool_policy_precheck: Any,
) -> RunRoutingPreviewServices:
    return RunRoutingPreviewServices(
        prepare_run_start_request=prepare_run_start_request,
        compute_tool_policy_precheck=compute_tool_policy_precheck,
    )


def build_server_run_routing_preview_services(
    *,
    late_server_export: Callable[[str], Any],
) -> RunRoutingPreviewServices:
    return build_run_routing_preview_services(
        prepare_run_start_request=late_server_export("_prepare_run_start_request"),
        compute_tool_policy_precheck=late_server_export("_compute_tool_policy_precheck"),
    )


def build_run_creation_services(
    *,
    create_run_from_request: Any,
) -> RunCreationServices:
    return RunCreationServices(
        create_run_from_request=create_run_from_request,
    )


def build_server_run_creation_services(
    *,
    late_server_export: Callable[[str], Any],
) -> RunCreationServices:
    return build_run_creation_services(
        create_run_from_request=late_server_export("_create_run_from_request"),
    )


def build_run_execution_services(
    *,
    stamp_request_owner: Any,
    prepare_run_start_request: Any,
    create_run_from_request: Any,
) -> RunExecutionServices:
    return RunExecutionServices(
        stamp_request_owner=stamp_request_owner,
        prepare_run_start_request=prepare_run_start_request,
        create_run_from_request=create_run_from_request,
    )


def build_server_run_execution_services(
    *,
    stamp_request_owner: Any,
    late_server_export: Callable[[str], Any],
) -> RunExecutionServices:
    return build_run_execution_services(
        stamp_request_owner=stamp_request_owner,
        prepare_run_start_request=late_server_export("_prepare_run_start_request"),
        create_run_from_request=late_server_export("_create_run_from_request"),
    )


def build_system_run_execution_services(
    *,
    prepare_run_start_request: Any,
    create_run_from_request: Any,
) -> RunExecutionServices:
    return build_run_execution_services(
        stamp_request_owner=lambda req, current_user: req,
        prepare_run_start_request=prepare_run_start_request,
        create_run_from_request=create_run_from_request,
    )


def build_system_run_execution_services_from_namespace(
    *,
    namespace: Dict[str, Any],
    prepare_run_start_request_name: str = "_prepare_run_start_request",
    create_run_from_request_name: str = "_create_run_from_request",
    prepare_run_start_request_fallback: Optional[Callable[[], Any]] = None,
    create_run_from_request_fallback: Optional[Callable[[], Any]] = None,
) -> RunExecutionServices:
    return build_system_run_execution_services(
        prepare_run_start_request=_resolve_namespace_callable(
            namespace,
            prepare_run_start_request_name,
            fallback_loader=prepare_run_start_request_fallback,
        ),
        create_run_from_request=_resolve_namespace_callable(
            namespace,
            create_run_from_request_name,
            fallback_loader=create_run_from_request_fallback,
        ),
    )


def build_server_system_run_execution_services(
    *,
    late_server_export: Callable[[str], Any],
) -> RunExecutionServices:
    return build_system_run_execution_services(
        prepare_run_start_request=late_server_export("_prepare_run_start_request"),
        create_run_from_request=late_server_export("_create_run_from_request"),
    )


def build_schedule_system_run_execution_services(
    *,
    prepare_run_start_request: Any,
    create_run_from_request: Callable[..., Any],
    schedule_id: Optional[str] = None,
) -> RunExecutionServices:
    return build_system_run_execution_services(
        prepare_run_start_request=prepare_run_start_request,
        create_run_from_request=build_schedule_bound_create_run_from_request(
            create_run_from_request,
            schedule_id=schedule_id,
        ),
    )


def build_namespace_delegated_system_run_execution_services(
    *,
    namespace: Dict[str, Any],
) -> RunExecutionServices:
    return build_system_run_execution_services_from_namespace(
        namespace=namespace,
    )


def build_delegated_run_execution_services(
    *,
    namespace: Dict[str, Any],
) -> RunExecutionServices:
    return build_namespace_delegated_system_run_execution_services(
        namespace=namespace,
    )


def execute_delegated_run_request(
    request: Any,
    *,
    namespace: Dict[str, Any],
    execute_system_run_start_request_via_turn_runtime_fn: Any,
    execute_built_unowned_system_run_start_request_via_turn_runtime_fn: Any = None,
) -> Dict[str, Any]:
    execute_fn = execute_built_unowned_system_run_start_request_via_turn_runtime_fn
    if execute_fn is None:
        from server_modules.turn_runtime import (
            execute_built_unowned_system_run_start_request_via_turn_runtime as execute_fn,
        )

    return execute_fn(
        request,
        execute_system_run_start_request_via_turn_runtime_fn=execute_system_run_start_request_via_turn_runtime_fn,
        build_run_execution_services_fn=lambda: build_delegated_run_execution_services(namespace=namespace),
    )


async def execute_run_start_request_via_turn_runtime(
    request: Any,
    *,
    current_user: Any,
    stamp_request_owner_fn: Any,
    services: RunExecutionServices,
    resolve_run_start_turn_request_fn: Any = resolve_run_start_turn_request,
    execute_durable_turn_request_fn: Any = None,
) -> Dict[str, Any]:
    if (
        resolve_run_start_turn_request_fn is not resolve_run_start_turn_request
        or execute_durable_turn_request_fn is not None
    ):
        resolved_services = services() if callable(services) else services
        execution_fn = execute_durable_turn_request_fn or execute_durable_turn_request
        resolution = resolve_run_start_turn_request_fn(
            current_user=current_user,
            body=request,
            stamp_request_owner_fn=stamp_request_owner_fn,
        )
        execution = await execution_fn(
            turn_request=resolution.turn_request,
            current_user=current_user,
            services=resolved_services,
            base_request=resolution.request,
        )
        result = execution.get("result")
        return dict(result) if isinstance(result, dict) else {"result": result}

    from server_modules import turn_ingress_service

    return await turn_ingress_service.start_run_start(
        request,
        current_user=current_user,
        stamp_request_owner_fn=stamp_request_owner_fn,
        services=services,
    )


def execute_system_run_start_request_via_turn_runtime(
    request: Any,
    *,
    stamp_request_owner_fn: Any,
    services: RunExecutionServices,
    current_user: Optional[Dict[str, Any]] = None,
    execute_run_start_request_via_turn_runtime_fn: Any = None,
) -> Dict[str, Any]:
    if execute_run_start_request_via_turn_runtime_fn is not None:
        return run_async_tool_call(
            execute_run_start_request_via_turn_runtime_fn(
                request,
                current_user=(
                    dict(current_user)
                    if isinstance(current_user, dict)
                    else {"auth_type": "api_key", "user_id": "", "email": ""}
                ),
                stamp_request_owner_fn=stamp_request_owner_fn,
                services=services,
            )
        )

    from server_modules import turn_ingress_service

    return turn_ingress_service.start_system_run_start(
        request,
        stamp_request_owner_fn=stamp_request_owner_fn,
        services=services,
        current_user=current_user,
    )


def execute_unowned_system_run_start_request_via_turn_runtime(
    request: Any,
    *,
    services: RunExecutionServices,
    current_user: Optional[Dict[str, Any]] = None,
    execute_system_run_start_request_via_turn_runtime_fn: Any = None,
) -> Dict[str, Any]:
    execute_fn = execute_system_run_start_request_via_turn_runtime_fn or execute_system_run_start_request_via_turn_runtime
    return execute_fn(
        request,
        stamp_request_owner_fn=lambda req, current_user: req,
        services=services,
        current_user=current_user,
    )


def execute_built_unowned_system_run_start_request_via_turn_runtime(
    request: Any,
    *,
    execute_system_run_start_request_via_turn_runtime_fn: Any,
    build_run_execution_services_fn: Any,
    current_user: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return execute_system_run_start_request_via_turn_runtime_fn(
        request,
        stamp_request_owner_fn=lambda req, current_user: req,
        services=build_run_execution_services_fn(),
        current_user=current_user,
    )


def execute_built_legacy_unowned_system_run_start_request_via_turn_runtime(
    request: Any,
    *,
    execute_system_run_start_request_via_turn_runtime_fn: Any,
    build_run_execution_services_fn: Any,
    create_run_from_request_fn: Any = None,
    current_user: Optional[Dict[str, Any]] = None,
    execute_built_unowned_system_run_start_request_via_turn_runtime_fn: Any = None,
) -> Dict[str, Any]:
    execute_fn = (
        execute_built_unowned_system_run_start_request_via_turn_runtime_fn
        or execute_built_unowned_system_run_start_request_via_turn_runtime
    )
    return execute_fn(
        request,
        execute_system_run_start_request_via_turn_runtime_fn=execute_system_run_start_request_via_turn_runtime_fn,
        build_run_execution_services_fn=build_run_execution_services_fn,
        current_user=current_user,
    )


def build_execute_unowned_system_run_start_request_via_turn_runtime(
    *,
    execute_unowned_system_run_start_request_via_turn_runtime_fn: Any = execute_unowned_system_run_start_request_via_turn_runtime,
) -> Any:
    def _execute(
        request: Any,
        *,
        stamp_request_owner_fn: Any,
        services: RunExecutionServices,
        current_user: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return execute_unowned_system_run_start_request_via_turn_runtime_fn(
            request,
            services=services,
            current_user=current_user,
        )

    return _execute


def is_local_runtime_run(run: Dict[str, Any]) -> bool:
    status = str(run.get("status") or "").strip().lower()
    if status.endswith("_local"):
        return True
    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    selected = str(
        metadata.get("execution_target_selected")
        or metadata.get("execution_target_requested")
        or ""
    ).strip().lower()
    return selected in {"local", "local_companion"}


def load_live_runtime_state(
    *,
    startup_sync_fn: Callable[[], Any],
    runs_by_id: Dict[str, Any],
    run_queue_index: Dict[int, str],
    local_claimed_runs: Dict[str, Any],
    local_pending_run_ids: List[str],
    local_queue_lock: Any,
    persisted_terminal_statuses: Set[str],
    remove_live_run_state_fn: Callable[[str], Any],
    persist_live_run_state_fn: Callable[[str, Dict[str, Any]], Any],
    now_iso_fn: Callable[[], str],
    emit_log_fn: Callable[..., Any],
    set_run_status_fn: Callable[[str, str], Any],
    sync_local_runtime_state_snapshot_fn: Callable[[], Any],
) -> None:
    startup_sync_fn()

    local_state_pruned = False
    with local_queue_lock:
        pending_snapshot = list(local_pending_run_ids)
        claimed_snapshot = {
            run_id: dict(claim)
            for run_id, claim in local_claimed_runs.items()
            if isinstance(claim, dict)
        }
        next_pending: list[str] = []
        seen_pending: set[str] = set()
        for run_id in pending_snapshot:
            run = runs_by_id.get(run_id)
            if not isinstance(run, dict):
                local_state_pruned = True
                continue
            status = str(run.get("status") or "").strip().lower()
            if status not in {"starting", "queued_local"}:
                local_state_pruned = True
                continue
            if run_id in seen_pending:
                local_state_pruned = True
                continue
            seen_pending.add(run_id)
            next_pending.append(run_id)
        next_claimed: Dict[str, Dict[str, Any]] = {}
        for run_id, claim in claimed_snapshot.items():
            run = runs_by_id.get(run_id)
            if not isinstance(run, dict):
                local_state_pruned = True
                continue
            status = str(run.get("status") or "").strip().lower()
            if status in persisted_terminal_statuses:
                local_state_pruned = True
                continue
            next_claimed[run_id] = claim
        if next_pending != pending_snapshot:
            local_pending_run_ids[:] = next_pending
        if next_claimed != claimed_snapshot:
            local_claimed_runs.clear()
            local_claimed_runs.update(next_claimed)
    if local_state_pruned:
        sync_local_runtime_state_snapshot_fn()

    run_queue_index.clear()
    for run_id, run in list(runs_by_id.items()):
        log_queue = run.get("logs") if isinstance(run, dict) else None
        if log_queue is not None:
            run_queue_index[id(log_queue)] = run_id

    recovered_queue = False
    for run_id, run in list(runs_by_id.items()):
        status = str(run.get("status") or "").strip().lower()
        if status in persisted_terminal_statuses:
            remove_live_run_state_fn(run_id)
            log_queue = run.get("logs")
            if log_queue is not None:
                run_queue_index.pop(id(log_queue), None)
            runs_by_id.pop(run_id, None)
            continue
        if is_local_runtime_run(run):
            if status == "running_local" and run_id not in local_claimed_runs:
                previous_status = status
                with _suspend_run_persistence_notifications(run):
                    run["status"] = "queued_local"
                    run["local_worker_id"] = None
                    run["local_claimed_at"] = None
                    run["local_last_heartbeat_at"] = None
                    run["updated_at"] = now_iso_fn()
                with local_queue_lock:
                    if run_id not in local_pending_run_ids:
                        local_pending_run_ids.append(run_id)
                trace_id = _run_trace_id(run)
                _persist_run_repository_snapshot(run_id, run, state="queued_local", trace_id=trace_id)
                _record_run_repository_transition(
                    run_id,
                    from_state=previous_status,
                    to_state="queued_local",
                    actor="runtime",
                    trace_id=trace_id,
                )
                _persist_live_run_state_with_version(
                    run_id,
                    run,
                    persist_live_run_state_fn=persist_live_run_state_fn,
                )
                recovered_queue = True
                continue
            if status in {"starting", "queued_local"}:
                previous_status = status
                with _suspend_run_persistence_notifications(run):
                    run["status"] = "queued_local"
                    run["updated_at"] = now_iso_fn()
                with local_queue_lock:
                    if run_id not in local_pending_run_ids and run_id not in local_claimed_runs:
                        local_pending_run_ids.append(run_id)
                trace_id = _run_trace_id(run)
                _persist_run_repository_snapshot(run_id, run, state="queued_local", trace_id=trace_id)
                _record_run_repository_transition(
                    run_id,
                    from_state=previous_status,
                    to_state="queued_local",
                    actor="runtime",
                    trace_id=trace_id,
                )
                _persist_live_run_state_with_version(
                    run_id,
                    run,
                    persist_live_run_state_fn=persist_live_run_state_fn,
                )
                recovered_queue = True
                continue
            _persist_run_repository_snapshot(
                run_id,
                run,
                state=str(run.get("status") or "").strip() or "queued",
                trace_id=_run_trace_id(run),
            )
            _persist_live_run_state_with_version(
                run_id,
                run,
                persist_live_run_state_fn=persist_live_run_state_fn,
            )
            continue
        if status in {"starting", "running"}:
            emit_log_fn(
                run["logs"],
                "error",
                "Run interrupted when the runtime restarted.",
                event="runtime_restart_interrupted_run",
                data={"run_id": run_id},
            )
            run["result"] = "Run interrupted when the runtime restarted."
            set_run_status_fn(run_id, "failed")
            run["logs"].put(None)
            continue
        _persist_run_repository_snapshot(
            run_id,
            run,
            state=str(run.get("status") or "").strip() or "queued",
            trace_id=_run_trace_id(run),
        )
        _persist_live_run_state_with_version(
            run_id,
            run,
            persist_live_run_state_fn=persist_live_run_state_fn,
        )
    if recovered_queue:
        sync_local_runtime_state_snapshot_fn()


def persist_weekly_schedules(
    *,
    schedules_lock: Any,
    weekly_schedules: Dict[str, Dict[str, Any]],
    safe_write_json_fn: Callable[[Any, Any], Any],
    schedules_file: Any,
    utc_now_iso_fn: Callable[[], str],
) -> None:
    with schedules_lock:
        payload = {
            "version": 1,
            "updated_at": utc_now_iso_fn(),
            "items": list(weekly_schedules.values()),
        }
    safe_write_json_fn(schedules_file, payload)


def load_weekly_schedules(
    *,
    schedules_lock: Any,
    weekly_schedules: Dict[str, Dict[str, Any]],
    safe_read_json_fn: Callable[[Any, Any], Any],
    schedules_file: Any,
    compute_schedule_next_run_at_fn: Callable[[Dict[str, Any]], Optional[str]],
) -> None:
    payload = safe_read_json_fn(schedules_file, {"version": 1, "items": []})
    items = payload.get("items")
    if not isinstance(items, list):
        return
    with schedules_lock:
        weekly_schedules.clear()
        for item in items:
            if not isinstance(item, dict):
                continue
            schedule_id = item.get("id")
            if isinstance(schedule_id, str) and schedule_id.strip():
                item["wake_mode"] = str(item.get("wake_mode") or "now").strip().lower() or "now"
                item["delivery"] = str(item.get("delivery") or "announce").strip().lower() or "announce"
                item["run_log"] = list(item.get("run_log") or [])[-10:]
                item["pending_heartbeat"] = bool(item.get("pending_heartbeat"))
                item["pending_heartbeat_slot"] = str(item.get("pending_heartbeat_slot") or "").strip() or None
                item["next_run_at"] = str(item.get("next_run_at") or "").strip() or compute_schedule_next_run_at_fn(item)
                weekly_schedules[schedule_id] = item


def persist_schedules(
    *,
    persist_weekly_schedules_fn: Callable[[], Any],
) -> None:
    persist_weekly_schedules_fn()


def load_schedules(
    *,
    load_weekly_schedules_fn: Callable[[], Any],
) -> None:
    load_weekly_schedules_fn()


def trigger_pending_heartbeat_schedules(
    *,
    schedules_lock: Any,
    weekly_schedules: Dict[str, Dict[str, Any]],
    run_start_request_class: Any,
    execute_scheduled_run_request_fn: Callable[[Any], Dict[str, Any]],
    append_schedule_run_log_fn: Callable[..., Dict[str, Any]],
    persist_schedules_fn: Callable[[], Any],
    utc_now_fn: Callable[[], datetime],
    utc_now_iso_fn: Callable[[], str],
    workspace_id: Optional[str] = None,
) -> Dict[str, Any]:
    now = utc_now_fn()
    started: List[Dict[str, Any]] = []
    changed = False
    scoped_workspace_id = str(workspace_id or "").strip() or None
    with schedules_lock:
        pending_items = [
            dict(item)
            for item in weekly_schedules.values()
            if bool(item.get("enabled")) and bool(item.get("pending_heartbeat"))
            and (
                scoped_workspace_id is None
                or str(item.get("workspace_id") or "").strip() == scoped_workspace_id
            )
        ]
    for schedule in pending_items:
        schedule_id = str(schedule.get("id") or "").strip()
        req_payload = schedule.get("run_request") if isinstance(schedule.get("run_request"), dict) else {}
        if not schedule_id or not isinstance(req_payload, dict):
            continue
        try:
            req = run_start_request_class(**req_payload)
            run_result = execute_scheduled_run_request_fn(req, schedule_id=schedule_id)
            with schedules_lock:
                current = weekly_schedules.get(schedule_id)
                if current is None:
                    continue
                current["pending_heartbeat"] = False
                current["pending_heartbeat_slot"] = None
                current = append_schedule_run_log_fn(
                    current,
                    status="started",
                    now_utc=now,
                    run_id=str(run_result.get("run_id") or "").strip() or None,
                    detail="Scheduled run started on heartbeat wake.",
                )
                current["updated_at"] = utc_now_iso_fn()
                weekly_schedules[schedule_id] = current
                changed = True
            started.append(
                {
                    "schedule_id": schedule_id,
                    "run_id": str(run_result.get("run_id") or "").strip() or None,
                    "name": str(schedule.get("name") or "").strip() or schedule_id,
                }
            )
        except Exception as exc:
            with schedules_lock:
                current = weekly_schedules.get(schedule_id)
                if current is None:
                    continue
                current["pending_heartbeat"] = False
                current["pending_heartbeat_slot"] = None
                current = append_schedule_run_log_fn(
                    current,
                    status="failed",
                    now_utc=now,
                    detail=str(exc),
                )
                current["updated_at"] = utc_now_iso_fn()
                weekly_schedules[schedule_id] = current
                changed = True
    if changed:
        persist_schedules_fn()
    return {
        "acted": bool(started),
        "started": started,
    }


def run_weekly_scheduler_forever(
    *,
    scheduler_poll_seconds: int,
    schedules_lock: Any,
    weekly_schedules: Dict[str, Dict[str, Any]],
    latest_schedule_slot_in_window_fn: Callable[[Dict[str, Any], datetime, datetime], Optional[datetime]],
    schedule_now_snapshot_fn: Callable[[datetime, str], Dict[str, Any]],
    normalized_schedule_timezone_fn: Callable[[str], str],
    run_start_request_class: Any,
    execute_scheduled_run_request_fn: Callable[[Any], Dict[str, Any]],
    append_schedule_run_log_fn: Callable[..., Dict[str, Any]],
    compute_schedule_next_run_at_fn: Callable[..., Optional[str]],
    persist_schedules_fn: Callable[[], Any],
    sleep_fn: Callable[[float], Any] = time.sleep,
    utc_now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    utc_now_iso_fn: Callable[[], str] = lambda: datetime.utcnow().isoformat() + "Z",
) -> None:
    poll_seconds = max(5, scheduler_poll_seconds)
    while True:
        sleep_fn(poll_seconds)
        now = utc_now_fn()
        window_start = now - timedelta(seconds=poll_seconds + 1)
        changed = False
        with schedules_lock:
            schedule_items = [dict(item) for item in weekly_schedules.values()]

        for schedule in schedule_items:
            if not bool(schedule.get("enabled")):
                continue
            matched_slot = latest_schedule_slot_in_window_fn(schedule, window_start, now)
            if matched_slot is None:
                continue
            snapshot = schedule_now_snapshot_fn(now, normalized_schedule_timezone_fn(str(schedule.get("timezone") or "local")))
            slot_key = matched_slot.astimezone(timezone.utc).replace(second=0, microsecond=0).isoformat().replace("+00:00", "Z")
            if str(schedule.get("last_trigger_slot") or "") == slot_key:
                continue

            schedule_id = str(schedule.get("id") or "")
            req_payload = schedule.get("run_request") if isinstance(schedule.get("run_request"), dict) else {}
            if not schedule_id or not isinstance(req_payload, dict):
                continue

            try:
                with schedules_lock:
                    current = weekly_schedules.get(schedule_id)
                    if current is None:
                        continue
                    current["last_trigger_slot"] = slot_key
                    current["last_trigger_date"] = snapshot["date_key"]
                    wake_mode = str(current.get("wake_mode") or "now").strip().lower() or "now"
                    if wake_mode == "next-heartbeat":
                        current["pending_heartbeat"] = True
                        current["pending_heartbeat_slot"] = slot_key
                        current["last_error"] = None
                        current["next_run_at"] = compute_schedule_next_run_at_fn(current, now_utc=now)
                        current["updated_at"] = utc_now_iso_fn()
                        weekly_schedules[schedule_id] = current
                        changed = True
                        continue
                req = run_start_request_class(**req_payload)
                run_result = execute_scheduled_run_request_fn(req, schedule_id=schedule_id)
                with schedules_lock:
                    current = weekly_schedules.get(schedule_id)
                    if current is not None:
                        current["last_trigger_slot"] = slot_key
                        current["last_trigger_date"] = snapshot["date_key"]
                        current["pending_heartbeat"] = False
                        current["pending_heartbeat_slot"] = None
                        current = append_schedule_run_log_fn(
                            current,
                            status="started",
                            now_utc=now,
                            run_id=str(run_result.get("run_id") or "").strip() or None,
                            detail="Scheduled run started immediately.",
                        )
                        current["updated_at"] = utc_now_iso_fn()
                        weekly_schedules[schedule_id] = current
                        changed = True
            except Exception as exc:
                with schedules_lock:
                    current = weekly_schedules.get(schedule_id)
                    if current is not None:
                        current["last_trigger_slot"] = slot_key
                        current["last_trigger_date"] = snapshot["date_key"]
                        current = append_schedule_run_log_fn(
                            current,
                            status="failed",
                            now_utc=now,
                            detail=str(exc),
                        )
                        current["updated_at"] = utc_now_iso_fn()
                        weekly_schedules[schedule_id] = current
                        changed = True

        if changed:
            persist_schedules_fn()


def initialize_runtime_services(
    *,
    is_initialized_fn: Callable[[], bool],
    mark_initialized_fn: Callable[[], Any],
    runtime_state_db_path: Any,
    setup_sessions_path: Any,
    provider_profiles_path: Any,
    idempotency_path: Any,
    init_runtime_state_db_fn: Callable[[Any], Any],
    sync_acp_manager_paths_fn: Callable[..., Any],
    initialize_chat_stream_runtime_state_fn: Callable[[], Any],
    load_live_runtime_state_fn: Callable[[], Any],
    load_run_history_fn: Callable[[], Any],
    load_approval_audit_fn: Callable[[], Any],
    load_channel_events_fn: Callable[[], Any],
    load_schedules_fn: Callable[[], Any],
    load_setup_sessions_fn: Callable[[], Any],
    load_provider_profiles_fn: Callable[[], Any],
    load_idempotency_fn: Callable[[], Any],
    replay_outbox_events_on_startup_fn: Optional[Callable[[], Any]] = None,
    run_outbox_delivery_forever_fn: Optional[Callable[[], Any]] = None,
    recover_expired_worker_leases_on_startup_fn: Optional[Callable[[], Any]] = None,
    recover_orphaned_local_runs_on_startup_fn: Callable[[], Any],
    load_runtime_skills_state_fn: Callable[[], Any],
    load_telegram_autopilot_state_fn: Callable[[], Any],
    load_whatsapp_autopilot_state_fn: Callable[[], Any],
    scheduler_enabled: bool,
    thread_factory: Callable[..., Any],
    run_weekly_scheduler_forever_fn: Callable[[], Any],
    telegram_autopilot_enabled: bool,
    run_telegram_autopilot_forever_fn: Callable[[], Any],
    set_telegram_autopilot_thread_fn: Callable[[Any], Any],
    whatsapp_autopilot_enabled: bool,
    whatsapp_autopilot_activate_fn: Callable[[], Any],
    local_runtime_watchdog_enabled: bool = False,
    run_local_runtime_watchdog_forever_fn: Optional[Callable[[], Any]] = None,
    set_local_runtime_watchdog_thread_fn: Optional[Callable[[Any], Any]] = None,
    recover_delegation_retries_on_startup_fn: Optional[Callable[[], Any]] = None,
) -> None:
    if is_initialized_fn():
        return
    init_runtime_state_db_fn(runtime_state_db_path)
    sync_acp_manager_paths_fn(
        runtime_db_path=runtime_state_db_path,
        setup_sessions_path=setup_sessions_path,
        provider_profiles_path=provider_profiles_path,
        idempotency_path=idempotency_path,
    )
    initialize_chat_stream_runtime_state_fn()
    load_live_runtime_state_fn()
    load_run_history_fn()
    load_approval_audit_fn()
    load_channel_events_fn()
    load_schedules_fn()
    load_setup_sessions_fn()
    load_provider_profiles_fn()
    load_idempotency_fn()
    if callable(replay_outbox_events_on_startup_fn):
        try:
            replay_outbox_events_on_startup_fn()
        except Exception:
            pass
    if callable(run_outbox_delivery_forever_fn):
        outbox_thread = thread_factory(target=run_outbox_delivery_forever_fn, daemon=True)
        outbox_thread.start()
    if callable(recover_expired_worker_leases_on_startup_fn):
        try:
            recover_expired_worker_leases_on_startup_fn()
        except Exception:
            pass
    try:
        recover_orphaned_local_runs_on_startup_fn()
    except Exception as exc:
        LOGGER.warning("Failed to recover orphaned local runs on startup: %s", exc)
    if callable(recover_delegation_retries_on_startup_fn):
        try:
            recover_delegation_retries_on_startup_fn()
        except Exception as exc:
            LOGGER.warning("Failed to recover delegation retries on startup: %s", exc)
    load_runtime_skills_state_fn()
    load_telegram_autopilot_state_fn()
    load_whatsapp_autopilot_state_fn()

    if local_runtime_watchdog_enabled and callable(run_local_runtime_watchdog_forever_fn):
        watchdog_thread = thread_factory(target=run_local_runtime_watchdog_forever_fn, daemon=True)
        if callable(set_local_runtime_watchdog_thread_fn):
            set_local_runtime_watchdog_thread_fn(watchdog_thread)
        watchdog_thread.start()
    if scheduler_enabled:
        scheduler_thread = thread_factory(target=run_weekly_scheduler_forever_fn, daemon=True)
        scheduler_thread.start()
    if telegram_autopilot_enabled:
        telegram_thread = thread_factory(target=run_telegram_autopilot_forever_fn, daemon=True)
        set_telegram_autopilot_thread_fn(telegram_thread)
        telegram_thread.start()
    if whatsapp_autopilot_enabled:
        whatsapp_autopilot_activate_fn()
    mark_initialized_fn()


def run_local_runtime_watchdog_pass(
    *,
    cleanup_stale_local_claims_fn: Callable[[], Any],
    resume_due_checkpoint_recoveries_fn: Optional[Callable[[], Any]],
    update_watchdog_status_fn: Callable[..., Any],
    utc_now_iso_fn: Callable[[], str],
    interval_seconds: int,
) -> Dict[str, Any]:
    checked_at = utc_now_iso_fn()
    cleaned_run_ids: List[str] = []
    resumed_run_ids: List[str] = []
    try:
        result = cleanup_stale_local_claims_fn()
        cleaned_run_ids = [str(item) for item in (result or []) if str(item or "").strip()]
        if callable(resume_due_checkpoint_recoveries_fn):
            resumed = resume_due_checkpoint_recoveries_fn()
            resumed_run_ids = [str(item) for item in (resumed or []) if str(item or "").strip()]
        if cleaned_run_ids or resumed_run_ids:
            summary_parts = []
            if cleaned_run_ids:
                summary_parts.append(
                    f"Recovered {len(cleaned_run_ids)} stale local claim{'s' if len(cleaned_run_ids) != 1 else ''}"
                )
            if resumed_run_ids:
                summary_parts.append(
                    f"scheduled {len(resumed_run_ids)} checkpoint resum{'es' if len(resumed_run_ids) != 1 else 'e'}"
                )
            summary = ". ".join(summary_parts) + "."
        else:
            summary = "No stale local claims or due checkpoint resumes found."
        status = "ok"
    except Exception as exc:
        cleaned_run_ids = []
        resumed_run_ids = []
        summary = f"Local runtime watchdog failed: {exc}"
        status = "error"
    update_watchdog_status_fn(
        checked_at=checked_at,
        status=status,
        summary=summary,
        cleaned_run_ids=cleaned_run_ids,
        resumed_run_ids=resumed_run_ids,
        interval_seconds=interval_seconds,
    )
    return {
        "checked_at": checked_at,
        "status": status,
        "summary": summary,
        "cleaned_run_ids": cleaned_run_ids,
        "resumed_run_ids": resumed_run_ids,
    }


def run_local_runtime_watchdog_forever(
    *,
    cleanup_stale_local_claims_fn: Callable[[], Any],
    resume_due_checkpoint_recoveries_fn: Optional[Callable[[], Any]],
    update_watchdog_status_fn: Callable[..., Any],
    utc_now_iso_fn: Callable[[], str],
    interval_seconds: int = 5,
    sleep_fn: Callable[[float], Any] = time.sleep,
) -> None:
    safe_interval = max(2, int(interval_seconds or 5))
    while True:
        run_local_runtime_watchdog_pass(
            cleanup_stale_local_claims_fn=cleanup_stale_local_claims_fn,
            resume_due_checkpoint_recoveries_fn=resume_due_checkpoint_recoveries_fn,
            update_watchdog_status_fn=update_watchdog_status_fn,
            utc_now_iso_fn=utc_now_iso_fn,
            interval_seconds=safe_interval,
        )
        sleep_fn(safe_interval)


def execute_workflow_human_node(
    *,
    run_id: str,
    node_id: str,
    label: str,
    variant: str,
    config: Dict[str, Any],
    current_text: str,
    state: Dict[str, Any],
    context: Dict[str, Any],
    log_queue: Any,
    update_node_state_fn: Callable[..., Any],
    wait_for_human_response_fn: Callable[..., Dict[str, Any]],
    agent_machine_full_trust_for_context_fn: Callable[[Optional[Dict[str, Any]]], bool],
    node_preview_text_fn: Callable[[Any], Optional[str]],
    json_safe_fn: Callable[[Any], Any],
    emit_log_fn: Callable[..., Any],
) -> str:
    title = str(config.get("title") or label or "Approval required").strip() or "Approval required"
    instructions = str(config.get("instructions") or "").strip()
    decision_options = config.get("decision_options") if isinstance(config.get("decision_options"), list) else []
    option_text = ", ".join(str(item).strip() for item in decision_options if str(item).strip()) or "approve / reject"
    if variant == "wait_for_reply":
        prompt = (
            f"{title}. {instructions} "
            f"Current workflow context: {current_text or 'No current output.'} "
            "Reply with the information needed to continue."
        ).strip()
    elif variant == "review":
        prompt = (
            f"{title}. {instructions} "
            f"Current workflow context: {current_text or 'No current output.'} "
            f"Reply with feedback or choose one of: {option_text}."
        ).strip()
    else:
        prompt = (
            f"{title}. {instructions} "
            f"Current workflow context: {current_text or 'No current output.'} "
            f"Reply with one of: {option_text}."
        ).strip()
    resolved_variant = variant or "approval"
    update_node_state_fn(
        run_id,
        node_id,
        status="waiting_human",
        summary=title,
        detail={"variant": resolved_variant, "decision_options": decision_options},
        waiting_for_approval=True,
    )
    human_response = wait_for_human_response_fn(
        run_id,
        prompt,
        source="workflow_human_node",
        metadata={
            "node_id": node_id,
            "node_label": label,
            "variant": resolved_variant,
            "decision_options": decision_options,
        },
    )
    response_decision = str(human_response.get("decision") or "").strip().lower()
    response_raw_decision = str(human_response.get("raw_decision") or "").strip()
    response_note = str(human_response.get("note") or "").strip()
    if variant == "approval":
        if not bool(human_response.get("approved")):
            raise RuntimeError(f"Workflow stopped at human node '{label}'.")
        current_text = f"{title}: approved"
        summary_text = f"{title}: approved"
        output_preview = node_preview_text_fn(current_text)
    else:
        reply_text = response_note or response_raw_decision or response_decision
        if not reply_text:
            raise RuntimeError(f"Human node '{label}' did not receive a usable response.")
        current_text = reply_text
        summary_text = (
            f"Reply received: {title}"
            if variant == "wait_for_reply"
            else f"Review received: {title}"
        )
        output_preview = node_preview_text_fn(reply_text)
    state["last_text"] = current_text
    state["last_data"] = {
        "node_id": node_id,
        "node_type": "human",
        "variant": variant,
        "decision": response_decision or None,
        "raw_decision": response_raw_decision or None,
        "note": response_note or None,
        "human_response": json_safe_fn(human_response),
    }
    emit_log_fn(
        log_queue,
        "info",
        current_text,
        event="workflow_human_resolved",
        data={"node_id": node_id, "variant": resolved_variant, "decision": response_decision or None},
    )
    update_node_state_fn(
        run_id,
        node_id,
        status="succeeded",
        finalize=True,
        output_preview=output_preview,
        summary=summary_text,
        detail={
            "decision": response_decision or None,
            "note": response_note or None,
            "variant": resolved_variant,
            "decision_options": decision_options,
        },
        waiting_for_approval=False,
    )
    return current_text


def create_workflow_child_local_run(
    *,
    run_id: str,
    context: Dict[str, Any],
    label: str,
    operation: Dict[str, Any],
    summary: str,
    execute_workflow_child_run_request_fn: Callable[[Any], Dict[str, Any]],
    local_execution_pack_id: str,
    execution_target_local_companion: str,
    trust_mode_auto: str,
    metadata_overrides: Optional[Dict[str, Any]] = None,
) -> str:
    child_metadata = build_workflow_child_metadata(
        context,
        parent_run_id=run_id,
        child_kind="local_tool_child_run",
    )
    child_metadata.update(
        {
            "outcome_pack": local_execution_pack_id,
            "execution_target": execution_target_local_companion,
            "trust_mode": trust_mode_auto,
            "pack_inputs": {"operations": [operation]},
            "subflow_parent_run_id": run_id,
            "workflow_tool_parent_run_id": run_id,
        }
    )
    merge_workflow_child_metadata_overrides(child_metadata, metadata_overrides)
    child_req = RunStartRequest(
        engine=str(context.get("engine") or "orion"),
        workflow_id=None,
        workspace_id=context.get("workspace_id"),
        user_goal=summary or f"Execute local workflow tool node: {label}",
        business_plan=context.get("business_plan"),
        agent_role=context.get("agent_role"),
        provider=context.get("provider"),
        model=context.get("model"),
        credential_id=context.get("credential_id"),
        parent_run_id=run_id,
        metadata=child_metadata,
    )
    child_result = execute_workflow_child_run_request_fn(child_req)
    route = child_result.get("route") if isinstance(child_result.get("route"), dict) else {}
    if str(route.get("selected") or "").strip().lower() != execution_target_local_companion:
        raise RuntimeError(f"Local tool node '{label}' requires a local_companion route.")
    child_run_id = str(child_result.get("run_id") or "").strip()
    if not child_run_id:
        raise RuntimeError(f"Local tool node '{label}' did not produce a child run id.")
    return child_run_id


def wait_for_workflow_child_run(
    *,
    child_run_id: str,
    timeout_seconds: int,
    load_run_fn: Callable[[str], Any],
    monotonic_fn: Callable[[], float] = time.monotonic,
    sleep_fn: Callable[[float], Any] = time.sleep,
    on_waiting_for_input: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    on_resumed: Optional[Callable[[str, Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    deadline = monotonic_fn() + max(5, int(timeout_seconds or 300))
    waiting_emitted = False
    while True:
        if monotonic_fn() > deadline:
            raise RuntimeError(f"Child run '{child_run_id}' did not finish within {timeout_seconds}s.")
        child_run = load_run_fn(child_run_id)
        if not isinstance(child_run, dict):
            sleep_fn(0.25)
            continue
        child_status = str(child_run.get("status") or "").strip().lower()
        if child_status == "waiting_for_input":
            if not waiting_emitted and callable(on_waiting_for_input):
                on_waiting_for_input(child_run_id, child_run)
            waiting_emitted = True
            sleep_fn(0.25)
            continue
        if waiting_emitted and callable(on_resumed):
            on_resumed(child_run_id, child_run)
            waiting_emitted = False
        if child_status == "completed":
            return child_run
        if child_status in {"failed", "timeout", "cancelled", "stopped"}:
            raise RuntimeError(f"Child run '{child_run_id}' ended with status '{child_status}'.")
        sleep_fn(0.25)


def execute_workflow_subflow_node(
    *,
    run_id: str,
    node_id: str,
    label: str,
    context: Dict[str, Any],
    config: Dict[str, Any],
    current_text: str,
    state: Dict[str, Any],
    update_node_state_fn: Callable[..., Any],
    emit_log_fn: Callable[..., Any],
    workflow_text_payload_fn: Callable[[Any], str],
    node_preview_text_fn: Callable[[Any], Optional[str]],
    execute_workflow_child_run_request_fn: Callable[[Any], Dict[str, Any]],
    load_run_fn: Callable[[str], Any],
    log_queue: Any,
    execution_target_local_companion: str,
) -> str:
    child_workflow_id = str(config.get("workflow_id") or "").strip()
    if not child_workflow_id:
        raise RuntimeError(f"Subflow node '{label}' is missing workflow_id.")
    if child_workflow_id == str(context.get("workflow_id") or "").strip():
        raise RuntimeError("Recursive subflow calls are not allowed.")

    child_metadata = build_workflow_child_metadata(
        context,
        parent_run_id=run_id,
        child_kind="subflow",
    )
    child_metadata["subflow_parent_run_id"] = run_id
    child_metadata["subflow_parent_workflow_id"] = str(context.get("workflow_id") or "").strip() or None
    child_req = RunStartRequest(
        engine=str(context.get("engine") or "orion"),
        workflow_id=child_workflow_id,
        workspace_id=context.get("workspace_id"),
        user_goal=current_text or context.get("user_goal"),
        business_plan=current_text or context.get("business_plan"),
        agent_role=context.get("agent_role"),
        provider=context.get("provider"),
        model=context.get("model"),
        credential_id=context.get("credential_id"),
        parent_run_id=run_id,
        metadata=child_metadata,
    )
    child_result = execute_workflow_child_run_request_fn(child_req)
    child_run_id = str(child_result.get("run_id") or "").strip()
    if not child_run_id:
        raise RuntimeError("Subflow execution did not return a child run id.")
    emit_log_fn(
        log_queue,
        "info",
        f"Subflow started: {child_workflow_id}",
        event="workflow_subflow_start",
        data={"node_id": node_id, "child_run_id": child_run_id, "workflow_id": child_workflow_id},
    )
    update_node_state_fn(
        run_id,
        node_id,
        summary=f"Waiting for subflow {child_workflow_id}",
        detail={"mode": str(config.get("mode") or "sync").strip() or "sync"},
        child_run_id=child_run_id,
        child_workflow_id=child_workflow_id,
    )
    child_run = wait_for_workflow_child_run(
        child_run_id=child_run_id,
        timeout_seconds=max(30, int(config.get("timeout_seconds") or 300)),
        load_run_fn=load_run_fn,
        on_waiting_for_input=lambda active_child_run_id, active_child_run: update_node_state_fn(
            run_id,
            node_id,
            status="waiting_human",
            summary=f"Subflow waiting for input: {child_workflow_id}",
            detail={
                "mode": str(config.get("mode") or "sync").strip() or "sync",
                "child_status": "waiting_for_input",
                "child_pending_approval_id": str(
                    ((active_child_run.get("pending_approval") or {}) if isinstance(active_child_run, dict) else {}).get("approval_id") or ""
                ).strip() or None,
            },
            child_run_id=active_child_run_id,
            child_workflow_id=child_workflow_id,
            waiting_for_approval=True,
        ),
        on_resumed=lambda active_child_run_id, _child_run: update_node_state_fn(
            run_id,
            node_id,
            status="running",
            activate=True,
            summary=f"Subflow resumed: {child_workflow_id}",
            child_run_id=active_child_run_id,
            child_workflow_id=child_workflow_id,
            waiting_for_approval=False,
        ),
    )
    child_status = str(child_run.get("status") or "").strip().lower()
    child_result_text = workflow_text_payload_fn(child_run.get("result") or "")
    child_result_data = child_run.get("result_data") if isinstance(child_run.get("result_data"), dict) else {}
    current_text = child_result_text or current_text
    state["last_text"] = current_text
    state["last_data"] = {
        "node_id": node_id,
        "node_type": "subflow",
        "variant": "call_workflow",
        "child_run_id": child_run_id,
        "child_workflow_id": child_workflow_id,
        "child_status": child_status,
        "child_result_data": child_result_data,
    }
    emit_log_fn(
        log_queue,
        "info",
        f"Subflow completed: {child_workflow_id}",
        event="workflow_subflow_complete",
        data={"node_id": node_id, "child_run_id": child_run_id},
    )
    update_node_state_fn(
        run_id,
        node_id,
        status="succeeded",
        finalize=True,
        output_preview=node_preview_text_fn(current_text),
        summary=f"Subflow completed: {child_workflow_id}",
        detail={"child_status": child_status},
        child_run_id=child_run_id,
        child_workflow_id=child_workflow_id,
    )
    return current_text


def execute_workflow_local_tool(
    *,
    run_id: str,
    context: Dict[str, Any],
    config: Dict[str, Any],
    label: str,
    variant: str,
    current_text: str,
    normalize_execution_target_fn: Callable[[Any], str],
    assert_file_mount_access_fn: Callable[[Any, Any, Any, Any], Dict[str, Any]],
    browser_automation_policy_from_operations_fn: Callable[[List[Dict[str, Any]]], Dict[str, Any]],
    normalize_action_id_fn: Callable[[Any], str],
    workflow_text_payload_fn: Callable[[Any], str],
    json_safe_fn: Callable[[Any], Any],
    execute_workflow_child_run_request_fn: Callable[[Any], Dict[str, Any]],
    local_execution_pack_id: str,
    execution_target_local_companion: str,
    execution_target_cloud: str,
    trust_mode_auto: str,
    browser_auth_actions: Set[str],
    on_waiting_for_input: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    on_resumed: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    load_run_fn: Optional[Callable[[str], Any]] = None,
) -> Dict[str, Any]:
    load_run = load_run_fn or (lambda _run_id: None)
    execution_target = normalize_execution_target_fn(
        config.get("execution_target")
        or context.get("metadata", {}).get("execution_target_selected")
        or context.get("metadata", {}).get("execution_target")
        or "auto"
    )
    permissions = config.get("permissions") if isinstance(config.get("permissions"), dict) else {}
    file_mount_grants = (
        permissions.get("file_mount_grants")
        if isinstance(permissions.get("file_mount_grants"), list)
        else context.get("metadata", {}).get("file_mount_grants")
    )
    if variant in {"shell", "browser", "computer", "code"} and execution_target == execution_target_cloud:
        raise RuntimeError(f"{variant.title()} tool nodes cannot target cloud directly; use local_companion or auto.")
    if variant in {"shell", "code"}:
        has_command = bool(str(config.get("command") or "").strip())
        has_argv = isinstance(config.get("argv"), list) and any(str(item or "").strip() for item in (config.get("argv") or []))
        has_capability = bool(str(config.get("capability") or "").strip())
        if has_capability and (has_command or has_argv):
            raise RuntimeError(f"{variant.title()} tool nodes cannot mix capability with command or argv.")
    if variant == "code":
        has_command = bool(str(config.get("command") or "").strip())
        has_argv = isinstance(config.get("argv"), list) and any(str(item or "").strip() for item in (config.get("argv") or []))
        has_capability = bool(str(config.get("capability") or "").strip())
        if has_command or has_argv or has_capability:
            raise RuntimeError("Code tool nodes cannot use command, argv, or capability in the current runtime.")
        raise RuntimeError(
            "Code tool nodes are not executable through Gateway yet; they require a reviewed higher-trust execution path."
        )
    if variant == "file":
        file_access = assert_file_mount_access_fn(
            config.get("path") or config.get("file_path"),
            config.get("mode") or config.get("operation") or "read",
            file_mount_grants,
            execution_target,
        )
        operation = {
            "tool": "filesystem.read_write",
            "mode": file_access["mode"],
            "path": str(config.get("path") or config.get("file_path") or "").strip(),
            "content": str(config.get("content") or current_text or ""),
            "overwrite": bool(config.get("overwrite")),
            "file_mount_grants": file_mount_grants if isinstance(file_mount_grants, list) else [],
            "mount": file_access["mount"],
        }
        if not operation["path"]:
            raise RuntimeError("File tool node requires path or file_path.")
    elif variant == "shell":
        cwd_access = assert_file_mount_access_fn(
            config.get("cwd") or ".",
            "read",
            file_mount_grants,
            execution_target,
        )
        operation = {
            "tool": "shell.execute",
            "command": str(config.get("command") or "").strip() or None,
            "argv": list(config.get("argv") or []) if isinstance(config.get("argv"), list) else None,
            "cwd": str(config.get("cwd") or ".").strip() or ".",
            "timeout_seconds": int(config.get("timeout_seconds") or 60),
            "capability": str(config.get("capability") or "").strip() or None,
            "file_mount_grants": file_mount_grants if isinstance(file_mount_grants, list) else [],
            "cwd_mount": cwd_access["mount"],
        }
        if not operation["command"] and not operation["argv"]:
            raise RuntimeError(f"{variant.title()} tool nodes require command or argv in the current runtime.")
    elif variant == "browser":
        browser_path = str(config.get("path") or "").strip()
        browser_path_mount: Optional[Dict[str, str]] = None
        if browser_path:
            browser_path_mount = assert_file_mount_access_fn(
                browser_path,
                "write",
                file_mount_grants,
                execution_target,
            )
        browser_context = {
            "browser_permissions": {},
            "browser_actions": [],
            "session_profile": None,
        }
        browser_permissions = browser_context["browser_permissions"]
        browser_actions = browser_context["browser_actions"]
        session_profile = browser_context["session_profile"]
        operation = {
            "tool": "browser_automation.interactive",
            "mode": str(config.get("mode") or "extract_text").strip() or "extract_text",
            "url": str(config.get("url") or "").strip(),
            "path": browser_path or None,
            "session_profile": session_profile or None,
            "browser_actions": browser_actions,
            "file_mount_grants": file_mount_grants if isinstance(file_mount_grants, list) else [],
            "path_mount": browser_path_mount["mount"] if browser_path_mount else None,
            "browser_permissions": browser_permissions if isinstance(browser_permissions, dict) else {"allow": False},
        }
        if not operation["url"]:
            raise RuntimeError("Browser tool node requires a URL.")
    elif variant == "screenshot":
        operation = {
            "tool": "screenshot.capture",
            "capability": str(config.get("capability") or "screenshot.capture").strip() or "screenshot.capture",
            "monitor": str(config.get("monitor") or "primary").strip() or "primary",
            "region": config.get("region"),
            "path": str(config.get("path") or config.get("file_path") or "").strip() or None,
        }
    elif variant == "computer":
        operation = {
            "tool": "computer_control",
            "action": str(config.get("action") or "").strip(),
            "region": config.get("region"),
            "x": config.get("x"),
            "y": config.get("y"),
            "text": config.get("text"),
            "script": config.get("script"),
            "title": config.get("title"),
            "message": config.get("message"),
            "name_or_path": config.get("name_or_path"),
        }
        if not operation["action"]:
            raise RuntimeError("Computer tool node requires an action.")
    else:
        raise RuntimeError(f"Local tool variant '{variant}' is not supported.")

    operation = {key: value for key, value in operation.items() if value is not None}
    child_run_id = create_workflow_child_local_run(
        run_id=run_id,
        context=context,
        label=label,
        operation=operation,
        summary=str(config.get("summary") or f"Execute {variant} tool node {label}").strip(),
        execute_workflow_child_run_request_fn=execute_workflow_child_run_request_fn,
        local_execution_pack_id=local_execution_pack_id,
        execution_target_local_companion=execution_target_local_companion,
        trust_mode_auto=trust_mode_auto,
        metadata_overrides=(
            dict(browser_context["metadata_overrides"])
            if variant == "browser"
            else None
        ),
    )
    child_run = wait_for_workflow_child_run(
        child_run_id=child_run_id,
        timeout_seconds=int(config.get("timeout_seconds") or 300),
        load_run_fn=load_run,
        on_waiting_for_input=on_waiting_for_input,
        on_resumed=on_resumed,
    )
    result_text = workflow_text_payload_fn(child_run.get("result") or "")
    result_data = child_run.get("result_data") if isinstance(child_run.get("result_data"), dict) else {}
    return {
        "summary": result_text or f"Local tool node completed: {label}",
        "result_data": {
            "local_child_run_id": child_run_id,
            "tool_variant": variant,
            "child_result": json_safe_fn(result_data),
        },
    }


async def execute_durable_agent_turn_dispatch(
    *,
    turn_request: AgentTurnRequest,
    current_user: Any,
    services: RunExecutionServices,
    base_request: Optional[Any] = None,
    trace_context: Optional[Any] = None,
    execute_durable_turn_request_fn: Any = None,
) -> Optional[dict[str, Any]]:
    # Internal delegate. Not an alternate turn engine. Called only from agent_turn().
    if turn_request.execution_mode != "durable":
        return None
    durable_execute = execute_durable_turn_request_fn or execute_durable_turn_request
    return await durable_execute(
        turn_request=turn_request,
        current_user=current_user,
        services=services,
        base_request=base_request,
        trace_context=trace_context,
    )


def build_legacy_run_execution_services(
    *,
    callbacks: LegacyRunExecutionCallbacks,
) -> RunExecutionServices:
    return build_run_execution_services(
        stamp_request_owner=callbacks.stamp_request_owner,
        prepare_run_start_request=callbacks.prepare_run_start_request,
        create_run_from_request=callbacks.create_run_from_request,
    )


def build_legacy_run_execution_services_from_values(
    *,
    stamp_request_owner: Any,
    prepare_run_start_request: Any,
    create_run_from_request: Any,
) -> RunExecutionServices:
    return build_legacy_run_execution_services(
        callbacks=LegacyRunExecutionCallbacks(
            stamp_request_owner=stamp_request_owner,
            prepare_run_start_request=prepare_run_start_request,
            create_run_from_request=create_run_from_request,
        )
    )


def build_run_preparation_services(
    *,
    engine_registry: Any,
    engine_validation_errors: Any,
    supported_outcome_packs: Any,
    normalize_requested_max_iterations: Callable[[Any], Optional[int]],
    normalize_trust_mode: Callable[[str], str],
    trust_mode_aliases: Any,
    valid_trust_modes: Any,
    normalize_execution_target: Callable[[Any], str],
    valid_execution_targets: Any,
    normalize_run_id_token: Callable[[Any], Optional[str]],
    normalize_agent_role: Callable[[Any], str],
    detect_agent_role: Callable[[RunStartRequest, Dict[str, Any]], tuple[str, str]],
    resolve_app_permissions: Callable[[str], Any],
    action_policy_from_app_permissions: Callable[[Any], Dict[str, Any]],
    merge_action_policies: Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]],
    fetch_workflow_snapshot: Callable[[Any], Any],
    postprocess_metadata: Optional[Callable[[RunStartRequest, Dict[str, Any]], Dict[str, Any]]] = None,
) -> RunPreparationServices:
    return RunPreparationServices(
        engine_registry=engine_registry,
        engine_validation_errors=engine_validation_errors,
        supported_outcome_packs=supported_outcome_packs,
        normalize_requested_max_iterations=normalize_requested_max_iterations,
        normalize_trust_mode=normalize_trust_mode,
        trust_mode_aliases=trust_mode_aliases,
        valid_trust_modes=valid_trust_modes,
        normalize_execution_target=normalize_execution_target,
        valid_execution_targets=valid_execution_targets,
        normalize_run_id_token=normalize_run_id_token,
        normalize_agent_role=normalize_agent_role,
        detect_agent_role=detect_agent_role,
        resolve_app_permissions=resolve_app_permissions,
        action_policy_from_app_permissions=action_policy_from_app_permissions,
        merge_action_policies=merge_action_policies,
        fetch_workflow_snapshot=fetch_workflow_snapshot,
        postprocess_metadata=postprocess_metadata,
    )


def build_legacy_orion_preparation_services(
    *,
    callbacks: LegacyOrionPreparationCallbacks,
) -> RunPreparationServices:
    return build_run_preparation_services(
        engine_registry=callbacks.engine_registry,
        engine_validation_errors=callbacks.engine_validation_errors,
        supported_outcome_packs=callbacks.supported_outcome_packs,
        normalize_requested_max_iterations=callbacks.normalize_requested_max_iterations,
        normalize_trust_mode=callbacks.normalize_trust_mode,
        trust_mode_aliases=callbacks.trust_mode_aliases,
        valid_trust_modes=callbacks.valid_trust_modes,
        normalize_execution_target=callbacks.normalize_execution_target,
        valid_execution_targets=callbacks.valid_execution_targets,
        normalize_run_id_token=callbacks.normalize_run_id_token,
        normalize_agent_role=callbacks.normalize_agent_role,
        detect_agent_role=callbacks.detect_agent_role,
        resolve_app_permissions=callbacks.resolve_app_permissions,
        action_policy_from_app_permissions=callbacks.action_policy_from_app_permissions,
        merge_action_policies=callbacks.merge_action_policies,
        fetch_workflow_snapshot=callbacks.fetch_workflow_snapshot,
        postprocess_metadata=callbacks.postprocess_metadata,
    )


def build_legacy_run_preparation_services(
    *,
    callbacks: LegacyOrionPreparationCallbacks,
) -> LegacyRunPreparationServices:
    return LegacyRunPreparationServices(
        build_preparation_services=lambda: build_legacy_orion_preparation_services(callbacks=callbacks),
    )


def build_legacy_run_preparation_services_from_values(
    *,
    engine_registry: dict[str, Any],
    engine_validation_errors: list[str],
    supported_outcome_packs: set[str],
    normalize_requested_max_iterations: Callable[[Any], Optional[int]],
    normalize_trust_mode: Callable[[Any], str],
    trust_mode_aliases: dict[str, str],
    valid_trust_modes: set[str],
    normalize_execution_target: Callable[[Any], str],
    valid_execution_targets: set[str],
    normalize_run_id_token: Callable[[Any], Optional[str]],
    normalize_agent_role: Callable[[Any], str],
    detect_agent_role: Callable[[RunStartRequest, dict[str, Any]], tuple[str, str]],
    resolve_app_permissions: Callable[[Any], dict[str, Any]],
    action_policy_from_app_permissions: Callable[[dict[str, Any]], dict[str, Any]],
    merge_action_policies: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    fetch_workflow_snapshot: Callable[[Any], Optional[dict[str, Any]]],
    postprocess_metadata: Optional[Callable[[dict[str, Any], RunStartRequest], dict[str, Any]]] = None,
) -> LegacyRunPreparationServices:
    return build_legacy_run_preparation_services(
        callbacks=LegacyOrionPreparationCallbacks(
            engine_registry=engine_registry,
            engine_validation_errors=engine_validation_errors,
            supported_outcome_packs=supported_outcome_packs,
            normalize_requested_max_iterations=normalize_requested_max_iterations,
            normalize_trust_mode=normalize_trust_mode,
            trust_mode_aliases=trust_mode_aliases,
            valid_trust_modes=valid_trust_modes,
            normalize_execution_target=normalize_execution_target,
            valid_execution_targets=valid_execution_targets,
            normalize_run_id_token=normalize_run_id_token,
            normalize_agent_role=normalize_agent_role,
            detect_agent_role=detect_agent_role,
            resolve_app_permissions=resolve_app_permissions,
            action_policy_from_app_permissions=action_policy_from_app_permissions,
            merge_action_policies=merge_action_policies,
            fetch_workflow_snapshot=fetch_workflow_snapshot,
            postprocess_metadata=postprocess_metadata,
        ),
    )


def build_prepared_run_creation_services(
    *,
    decide_execution_target: Any,
    apply_execution_route_metadata: Any,
    build_doctor_run_gate: Any,
    agent_machine_inherited_owner_user_id: Any,
    compute_tool_policy_precheck: Any,
    apply_browser_execution_metadata: Any,
    local_execution_block_prompt: Any,
    resolve_runtime_policy_mode: Any,
    agent_machine_full_trust_enabled: Any,
    local_execution_requires_start_confirmation: Any,
    mark_local_execution_tools_approved: Any,
    precheck_human_action_labels: Any,
    local_execution_confirmation_prompt: Any,
    begin_run_pending_confirmation: Any,
    create_run: Any,
    load_created_run: Any = None,
    now_iso: Any = None,
) -> PreparedRunCreationServices:
    return PreparedRunCreationServices(
        decide_execution_target=decide_execution_target,
        apply_execution_route_metadata=apply_execution_route_metadata,
        build_doctor_run_gate=build_doctor_run_gate,
        agent_machine_inherited_owner_user_id=agent_machine_inherited_owner_user_id,
        compute_tool_policy_precheck=compute_tool_policy_precheck,
        apply_browser_execution_metadata=apply_browser_execution_metadata,
        local_execution_block_prompt=local_execution_block_prompt,
        resolve_runtime_policy_mode=resolve_runtime_policy_mode,
        agent_machine_full_trust_enabled=agent_machine_full_trust_enabled,
        local_execution_requires_start_confirmation=local_execution_requires_start_confirmation,
        mark_local_execution_tools_approved=mark_local_execution_tools_approved,
        precheck_human_action_labels=precheck_human_action_labels,
        local_execution_confirmation_prompt=local_execution_confirmation_prompt,
        begin_run_pending_confirmation=begin_run_pending_confirmation,
        create_run=create_run,
        load_created_run=load_created_run,
        now_iso=now_iso,
    )


def build_legacy_local_execution_creation_services(
    *,
    callbacks: LegacyLocalExecutionCreationCallbacks,
) -> PreparedRunCreationServices:
    return build_prepared_run_creation_services(
        decide_execution_target=callbacks.decide_execution_target,
        apply_execution_route_metadata=callbacks.apply_execution_route_metadata,
        build_doctor_run_gate=callbacks.build_doctor_run_gate,
        agent_machine_inherited_owner_user_id=callbacks.agent_machine_inherited_owner_user_id,
        compute_tool_policy_precheck=callbacks.compute_tool_policy_precheck,
        apply_browser_execution_metadata=apply_browser_execution_metadata,
        local_execution_block_prompt=local_execution_block_prompt,
        resolve_runtime_policy_mode=callbacks.resolve_runtime_policy_mode,
        agent_machine_full_trust_enabled=callbacks.agent_machine_full_trust_enabled,
        local_execution_requires_start_confirmation=lambda metadata, precheck: local_execution_requires_start_confirmation(
            metadata,
            precheck,
            local_execution_target=callbacks.local_execution_target,
            local_execution_pack_id=callbacks.local_execution_pack_id,
        ),
        mark_local_execution_tools_approved=mark_local_execution_tools_approved,
        precheck_human_action_labels=precheck_human_action_labels,
        local_execution_confirmation_prompt=local_execution_confirmation_prompt,
        begin_run_pending_confirmation=callbacks.begin_run_pending_confirmation,
        create_run=callbacks.create_run,
        load_created_run=callbacks.load_created_run,
        now_iso=callbacks.now_iso,
    )


def build_legacy_run_request_services(
    *,
    prepare_run_start_request: Any,
    callbacks: LegacyLocalExecutionCreationCallbacks,
    result_services: RunPreparedResultServices,
) -> LegacyRunRequestServices:
    return LegacyRunRequestServices(
        prepare_run_start_request=prepare_run_start_request,
        build_creation_services=lambda: build_legacy_local_execution_creation_services(callbacks=callbacks),
        result_services=result_services,
    )


def build_legacy_run_request_services_from_values(
    *,
    prepare_run_start_request: Any,
    decide_execution_target: Callable[..., dict[str, Any]],
    apply_execution_route_metadata: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    build_doctor_run_gate: Callable[..., dict[str, Any]],
    agent_machine_inherited_owner_user_id: Callable[[Any], Any],
    compute_tool_policy_precheck: Callable[[dict[str, Any]], dict[str, Any]],
    resolve_runtime_policy_mode: Callable[..., dict[str, Any]],
    agent_machine_full_trust_enabled: Callable[[Any], bool],
    begin_run_pending_confirmation: Callable[..., dict[str, Any]],
    create_run: Callable[..., str],
    local_execution_target: str,
    local_execution_pack_id: str,
    result_services: RunPreparedResultServices,
    load_created_run: Optional[Callable[[str], dict[str, Any]]] = None,
    now_iso: Optional[Callable[[], str]] = None,
) -> LegacyRunRequestServices:
    return build_legacy_run_request_services(
        prepare_run_start_request=prepare_run_start_request,
        callbacks=LegacyLocalExecutionCreationCallbacks(
            decide_execution_target=decide_execution_target,
            apply_execution_route_metadata=apply_execution_route_metadata,
            build_doctor_run_gate=build_doctor_run_gate,
            agent_machine_inherited_owner_user_id=agent_machine_inherited_owner_user_id,
            compute_tool_policy_precheck=compute_tool_policy_precheck,
            resolve_runtime_policy_mode=resolve_runtime_policy_mode,
            agent_machine_full_trust_enabled=agent_machine_full_trust_enabled,
            begin_run_pending_confirmation=begin_run_pending_confirmation,
            create_run=create_run,
            local_execution_target=local_execution_target,
            local_execution_pack_id=local_execution_pack_id,
            load_created_run=load_created_run,
            now_iso=now_iso,
        ),
        result_services=result_services,
    )


def build_runs_core_creation_result(
    req: RunStartRequest,
    *,
    created: Dict[str, Any],
) -> Dict[str, Any]:
    metadata = created["metadata"]
    created_run = created["created_run"]
    return {
        "run_id": created["run_id"],
        "engine": created["engine"],
        "status": created["status"],
        "active_profile_id": created_run.get("active_profile_id"),
        "active_profile_label": created_run.get("active_profile_label"),
        "active_profile_provider": created_run.get("active_provider"),
        "active_profile_model": created_run.get("active_model"),
        "requested_provider": str(req.provider or "").strip().lower() or None,
        "requested_model": str(req.model or "").strip() or None,
        "policy_mode": metadata.get("policy_mode"),
        "agent_role": metadata.get("agent_role"),
        "agent_role_source": metadata.get("agent_role_source"),
        "parent_run_id": metadata.get("parent_run_id"),
        "delegation_root_run_id": metadata.get("delegation_root_run_id"),
        "delegated_by_run_id": metadata.get("delegated_by_run_id"),
        "delegated_by_role": metadata.get("delegated_by_role"),
        "route": created["route"],
        "doctor_preflight": created["doctor_preflight"],
        "pending_approval": created["pending_confirmation"],
    }


def build_runs_delegation_creation_result(
    *,
    created: Dict[str, Any],
) -> Dict[str, Any]:
    metadata = created["metadata"]
    return {
        "run_id": created["run_id"],
        "engine": created["engine"],
        "status": created["status"],
        "agent_role": metadata.get("agent_role"),
        "agent_role_source": metadata.get("agent_role_source"),
        "parent_run_id": metadata.get("parent_run_id"),
        "delegation_root_run_id": metadata.get("delegation_root_run_id"),
        "delegated_by_run_id": metadata.get("delegated_by_run_id"),
        "delegated_by_role": metadata.get("delegated_by_role"),
        "route": created["route"],
        "doctor_preflight": created["doctor_preflight"],
        "pending_confirmation": created["pending_confirmation"],
        "pending_approval": created["pending_confirmation"],
    }


def build_runs_core_result_services() -> RunPreparedResultServices:
    return build_run_prepared_result_services(
        create_run_from_prepared_request=create_run_from_prepared_request,
        build_result=lambda request, *, created: build_runs_core_creation_result(request, created=created),
    )


def build_runs_delegation_result_services() -> RunPreparedResultServices:
    return build_run_prepared_result_services(
        create_run_from_prepared_request=create_run_from_prepared_request,
        build_result=lambda _request, *, created: build_runs_delegation_creation_result(created=created),
    )


def build_runs_core_legacy_request_services(
    *,
    prepare_run_start_request: Any,
    decide_execution_target: Callable[..., dict[str, Any]],
    apply_execution_route_metadata: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    build_doctor_run_gate: Callable[..., dict[str, Any]],
    agent_machine_inherited_owner_user_id: Callable[[Any], Any],
    compute_tool_policy_precheck: Callable[[dict[str, Any]], dict[str, Any]],
    resolve_runtime_policy_mode: Callable[..., dict[str, Any]],
    agent_machine_full_trust_enabled: Callable[[Any], bool],
    begin_run_pending_confirmation: Callable[..., dict[str, Any]],
    create_run: Callable[..., str],
    local_execution_target: str,
    local_execution_pack_id: str,
    load_created_run: Optional[Callable[[str], dict[str, Any]]] = None,
    now_iso: Optional[Callable[[], str]] = None,
) -> LegacyRunRequestServices:
    return build_legacy_run_request_services_from_values(
        prepare_run_start_request=prepare_run_start_request,
        decide_execution_target=decide_execution_target,
        apply_execution_route_metadata=apply_execution_route_metadata,
        build_doctor_run_gate=build_doctor_run_gate,
        agent_machine_inherited_owner_user_id=agent_machine_inherited_owner_user_id,
        compute_tool_policy_precheck=compute_tool_policy_precheck,
        resolve_runtime_policy_mode=resolve_runtime_policy_mode,
        agent_machine_full_trust_enabled=agent_machine_full_trust_enabled,
        begin_run_pending_confirmation=begin_run_pending_confirmation,
        create_run=create_run,
        local_execution_target=local_execution_target,
        local_execution_pack_id=local_execution_pack_id,
        result_services=build_runs_core_result_services(),
        load_created_run=load_created_run,
        now_iso=now_iso,
    )


def build_runs_delegation_legacy_request_services(
    *,
    prepare_run_start_request: Any,
    decide_execution_target: Callable[..., dict[str, Any]],
    apply_execution_route_metadata: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    build_doctor_run_gate: Callable[..., dict[str, Any]],
    agent_machine_inherited_owner_user_id: Callable[[Any], Any],
    compute_tool_policy_precheck: Callable[[dict[str, Any]], dict[str, Any]],
    resolve_runtime_policy_mode: Callable[..., dict[str, Any]],
    agent_machine_full_trust_enabled: Callable[[Any], bool],
    begin_run_pending_confirmation: Callable[..., dict[str, Any]],
    create_run: Callable[..., str],
    local_execution_target: str,
    local_execution_pack_id: str,
    now_iso: Optional[Callable[[], str]] = None,
) -> LegacyRunRequestServices:
    return build_legacy_run_request_services_from_values(
        prepare_run_start_request=prepare_run_start_request,
        decide_execution_target=decide_execution_target,
        apply_execution_route_metadata=apply_execution_route_metadata,
        build_doctor_run_gate=build_doctor_run_gate,
        agent_machine_inherited_owner_user_id=agent_machine_inherited_owner_user_id,
        compute_tool_policy_precheck=compute_tool_policy_precheck,
        resolve_runtime_policy_mode=resolve_runtime_policy_mode,
        agent_machine_full_trust_enabled=agent_machine_full_trust_enabled,
        begin_run_pending_confirmation=begin_run_pending_confirmation,
        create_run=create_run,
        local_execution_target=local_execution_target,
        local_execution_pack_id=local_execution_pack_id,
        result_services=build_runs_delegation_result_services(),
        now_iso=now_iso,
    )


def _resolve_namespace_callable(
    namespace: Dict[str, Any],
    *names: str,
    fallback_loader: Optional[Callable[[], Any]] = None,
) -> Any:
    for name in names:
        candidate = namespace.get(name)
        if callable(candidate):
            return candidate
    if fallback_loader is not None:
        return fallback_loader()
    for name in names:
        candidate = namespace.get(name)
        if candidate is not None:
            return candidate
    return None


def build_runs_delegation_runtime_request_services_from_namespace(
    *,
    namespace: Dict[str, Any],
    prepare_run_start_request: Any,
    decide_execution_target: Callable[..., dict[str, Any]],
    apply_execution_route_metadata: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    build_doctor_run_gate: Callable[..., dict[str, Any]],
    agent_machine_inherited_owner_user_id: Callable[[Any], Any],
    resolve_runtime_policy_mode: Callable[..., dict[str, Any]],
    agent_machine_full_trust_enabled: Callable[[Any], bool],
    local_execution_target: str,
    local_execution_pack_id: str,
    now_iso: Optional[Callable[[], str]] = None,
    compute_tool_policy_precheck_fallback: Optional[Callable[[], Any]] = None,
    create_run_fallback: Optional[Callable[[], Any]] = None,
    begin_run_pending_confirmation_fallback: Optional[Callable[[], Any]] = None,
) -> LegacyRunRequestServices:
    return build_runs_delegation_legacy_request_services(
        prepare_run_start_request=prepare_run_start_request,
        decide_execution_target=decide_execution_target,
        apply_execution_route_metadata=apply_execution_route_metadata,
        build_doctor_run_gate=build_doctor_run_gate,
        agent_machine_inherited_owner_user_id=agent_machine_inherited_owner_user_id,
        compute_tool_policy_precheck=_resolve_namespace_callable(
            namespace,
            "_compute_tool_policy_precheck",
            fallback_loader=compute_tool_policy_precheck_fallback,
        ),
        resolve_runtime_policy_mode=resolve_runtime_policy_mode,
        agent_machine_full_trust_enabled=agent_machine_full_trust_enabled,
        begin_run_pending_confirmation=_resolve_namespace_callable(
            namespace,
            "_begin_run_pending_confirmation",
            "_begin_run_pending_approval",
            fallback_loader=begin_run_pending_confirmation_fallback,
        ),
        create_run=_resolve_namespace_callable(
            namespace,
            "create_run",
            fallback_loader=create_run_fallback,
        ),
        local_execution_target=local_execution_target,
        local_execution_pack_id=local_execution_pack_id,
        now_iso=now_iso,
    )


def build_runs_core_runtime_request_services_from_namespace(
    *,
    namespace: Dict[str, Any],
    prepare_run_start_request: Any,
    decide_execution_target: Callable[..., dict[str, Any]],
    apply_execution_route_metadata: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    build_doctor_run_gate: Callable[..., dict[str, Any]],
    agent_machine_inherited_owner_user_id: Callable[[Any], Any],
    resolve_runtime_policy_mode: Callable[..., dict[str, Any]],
    agent_machine_full_trust_enabled: Callable[[Any], bool],
    local_execution_target: str,
    local_execution_pack_id: str,
    load_created_run: Optional[Callable[[str], dict[str, Any]]] = None,
    now_iso: Optional[Callable[[], str]] = None,
    compute_tool_policy_precheck_fallback: Optional[Callable[[], Any]] = None,
    create_run_fallback: Optional[Callable[[], Any]] = None,
    begin_run_pending_confirmation_fallback: Optional[Callable[[], Any]] = None,
) -> LegacyRunRequestServices:
    return build_runs_core_legacy_request_services(
        prepare_run_start_request=prepare_run_start_request,
        decide_execution_target=decide_execution_target,
        apply_execution_route_metadata=apply_execution_route_metadata,
        build_doctor_run_gate=build_doctor_run_gate,
        agent_machine_inherited_owner_user_id=agent_machine_inherited_owner_user_id,
        compute_tool_policy_precheck=_resolve_namespace_callable(
            namespace,
            "_compute_tool_policy_precheck",
            fallback_loader=compute_tool_policy_precheck_fallback,
        ),
        resolve_runtime_policy_mode=resolve_runtime_policy_mode,
        agent_machine_full_trust_enabled=agent_machine_full_trust_enabled,
        begin_run_pending_confirmation=_resolve_namespace_callable(
            namespace,
            "_begin_run_pending_confirmation",
            fallback_loader=begin_run_pending_confirmation_fallback,
        ),
        create_run=_resolve_namespace_callable(
            namespace,
            "create_run",
            fallback_loader=create_run_fallback,
        ),
        local_execution_target=local_execution_target,
        local_execution_pack_id=local_execution_pack_id,
        load_created_run=load_created_run,
        now_iso=now_iso,
    )


def build_runs_core_legacy_preparation_services(
    *,
    engine_registry: dict[str, Any],
    engine_validation_errors: list[str],
    supported_outcome_packs: set[str],
    normalize_requested_max_iterations: Callable[[Any], Optional[int]],
    normalize_trust_mode: Callable[[Any], str],
    trust_mode_aliases: dict[str, str],
    valid_trust_modes: set[str],
    normalize_execution_target: Callable[[Any], str],
    valid_execution_targets: set[str],
    normalize_run_id_token: Callable[[Any], Optional[str]],
    normalize_agent_role: Callable[[Any], str],
    detect_agent_role: Callable[[RunStartRequest, dict[str, Any]], tuple[str, str]],
    resolve_app_permissions: Callable[[Any], dict[str, Any]],
    action_policy_from_app_permissions: Callable[[dict[str, Any]], dict[str, Any]],
    merge_action_policies: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    fetch_workflow_snapshot: Callable[[Any], Optional[dict[str, Any]]],
    postprocess_metadata: Optional[Callable[[dict[str, Any], RunStartRequest], dict[str, Any]]] = None,
) -> LegacyRunPreparationServices:
    return build_legacy_run_preparation_services_from_values(
        engine_registry=engine_registry,
        engine_validation_errors=engine_validation_errors,
        supported_outcome_packs=supported_outcome_packs,
        normalize_requested_max_iterations=normalize_requested_max_iterations,
        normalize_trust_mode=normalize_trust_mode,
        trust_mode_aliases=trust_mode_aliases,
        valid_trust_modes=valid_trust_modes,
        normalize_execution_target=normalize_execution_target,
        valid_execution_targets=valid_execution_targets,
        normalize_run_id_token=normalize_run_id_token,
        normalize_agent_role=normalize_agent_role,
        detect_agent_role=detect_agent_role,
        resolve_app_permissions=resolve_app_permissions,
        action_policy_from_app_permissions=action_policy_from_app_permissions,
        merge_action_policies=merge_action_policies,
        fetch_workflow_snapshot=fetch_workflow_snapshot,
        postprocess_metadata=postprocess_metadata,
    )


def build_runs_core_runtime_preparation_services_from_namespace(
    *,
    namespace: Dict[str, Any],
    engine_registry: dict[str, Any],
    engine_validation_errors: list[str],
    supported_outcome_packs: set[str],
    normalize_trust_mode: Callable[[Any], str],
    trust_mode_aliases: dict[str, str],
    valid_trust_modes: set[str],
    normalize_execution_target: Callable[[Any], str],
    valid_execution_targets: set[str],
    normalize_agent_role: Callable[[Any], str],
    resolve_app_permissions: Callable[[Any], dict[str, Any]],
    action_policy_from_app_permissions: Callable[[dict[str, Any]], dict[str, Any]],
    merge_action_policies: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    fetch_workflow_snapshot: Callable[[Any], Optional[dict[str, Any]]],
) -> LegacyRunPreparationServices:
    return build_runs_core_legacy_preparation_services(
        engine_registry=engine_registry,
        engine_validation_errors=engine_validation_errors,
        supported_outcome_packs=supported_outcome_packs,
        normalize_requested_max_iterations=_resolve_namespace_callable(
            namespace,
            "_normalize_requested_max_iterations",
        ),
        normalize_trust_mode=normalize_trust_mode,
        trust_mode_aliases=trust_mode_aliases,
        valid_trust_modes=valid_trust_modes,
        normalize_execution_target=normalize_execution_target,
        valid_execution_targets=valid_execution_targets,
        normalize_run_id_token=_resolve_namespace_callable(
            namespace,
            "_normalize_run_id_token",
        ),
        normalize_agent_role=normalize_agent_role,
        detect_agent_role=_resolve_namespace_callable(
            namespace,
            "_detect_agent_role",
        ),
        resolve_app_permissions=resolve_app_permissions,
        action_policy_from_app_permissions=action_policy_from_app_permissions,
        merge_action_policies=merge_action_policies,
        fetch_workflow_snapshot=fetch_workflow_snapshot,
        postprocess_metadata=_resolve_namespace_callable(
            namespace,
            "_bind_obvious_connector_write_intent",
        ),
    )


def build_runs_delegation_legacy_preparation_services(
    *,
    engine_registry: dict[str, Any],
    engine_validation_errors: list[str],
    supported_outcome_packs: set[str],
    normalize_requested_max_iterations: Callable[[Any], Optional[int]],
    normalize_trust_mode: Callable[[Any], str],
    trust_mode_aliases: dict[str, str],
    valid_trust_modes: set[str],
    normalize_execution_target: Callable[[Any], str],
    valid_execution_targets: set[str],
    normalize_run_id_token: Callable[[Any], Optional[str]],
    normalize_agent_role: Callable[[Any], str],
    detect_agent_role: Callable[[RunStartRequest, dict[str, Any]], tuple[str, str]],
    resolve_app_permissions: Callable[[Any], dict[str, Any]],
    action_policy_from_app_permissions: Callable[[dict[str, Any]], dict[str, Any]],
    merge_action_policies: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    fetch_workflow_snapshot: Callable[[Any], Optional[dict[str, Any]]],
) -> LegacyRunPreparationServices:
    return build_legacy_run_preparation_services_from_values(
        engine_registry=engine_registry,
        engine_validation_errors=engine_validation_errors,
        supported_outcome_packs=supported_outcome_packs,
        normalize_requested_max_iterations=normalize_requested_max_iterations,
        normalize_trust_mode=normalize_trust_mode,
        trust_mode_aliases=trust_mode_aliases,
        valid_trust_modes=valid_trust_modes,
        normalize_execution_target=normalize_execution_target,
        valid_execution_targets=valid_execution_targets,
        normalize_run_id_token=normalize_run_id_token,
        normalize_agent_role=normalize_agent_role,
        detect_agent_role=detect_agent_role,
        resolve_app_permissions=resolve_app_permissions,
        action_policy_from_app_permissions=action_policy_from_app_permissions,
        merge_action_policies=merge_action_policies,
        fetch_workflow_snapshot=fetch_workflow_snapshot,
    )


def build_runs_delegation_runtime_preparation_services_from_namespace(
    *,
    namespace: Dict[str, Any],
    engine_registry: dict[str, Any],
    engine_validation_errors: list[str],
    supported_outcome_packs: set[str],
    normalize_trust_mode: Callable[[Any], str],
    trust_mode_aliases: dict[str, str],
    valid_trust_modes: set[str],
    normalize_execution_target: Callable[[Any], str],
    valid_execution_targets: set[str],
    normalize_agent_role: Callable[[Any], str],
    resolve_app_permissions: Callable[[Any], dict[str, Any]],
    action_policy_from_app_permissions: Callable[[dict[str, Any]], dict[str, Any]],
    merge_action_policies: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    fetch_workflow_snapshot: Callable[[Any], Optional[dict[str, Any]]],
) -> LegacyRunPreparationServices:
    return build_runs_delegation_legacy_preparation_services(
        engine_registry=engine_registry,
        engine_validation_errors=engine_validation_errors,
        supported_outcome_packs=supported_outcome_packs,
        normalize_requested_max_iterations=_resolve_namespace_callable(
            namespace,
            "_normalize_requested_max_iterations",
        ),
        normalize_trust_mode=normalize_trust_mode,
        trust_mode_aliases=trust_mode_aliases,
        valid_trust_modes=valid_trust_modes,
        normalize_execution_target=normalize_execution_target,
        valid_execution_targets=valid_execution_targets,
        normalize_run_id_token=_resolve_namespace_callable(
            namespace,
            "_normalize_run_id_token",
        ),
        normalize_agent_role=normalize_agent_role,
        detect_agent_role=_resolve_namespace_callable(
            namespace,
            "_detect_agent_role",
        ),
        resolve_app_permissions=resolve_app_permissions,
        action_policy_from_app_permissions=action_policy_from_app_permissions,
        merge_action_policies=merge_action_policies,
        fetch_workflow_snapshot=fetch_workflow_snapshot,
    )


def build_schedule_bound_create_run_from_request(
    create_run_from_request: Callable[..., Any],
    *,
    schedule_id: Optional[str] = None,
) -> Callable[[Any], Any]:
    if schedule_id is None:
        return lambda req: create_run_from_request(req)
    return lambda req: create_run_from_request(req, schedule_id=schedule_id)


def is_valid_run_state(value: str) -> bool:
    return value in RUN_STATES


def validate_transition(transition: RunTransition) -> None:
    if not is_valid_run_state(transition.from_state):
        raise ValueError(f"Unknown from_state '{transition.from_state}'.")
    if not is_valid_run_state(transition.to_state):
        raise ValueError(f"Unknown to_state '{transition.to_state}'.")


def initial_run_record(run_id: str, tenant_id: str, workspace_id: str, **metadata: Any) -> RunRecord:
    return RunRecord(run_id=run_id, tenant_id=tenant_id, workspace_id=workspace_id, metadata=dict(metadata))


def _metadata_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _hint_text(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _hint_list(value: Any) -> Optional[List[dict]]:
    return list(value) if isinstance(value, list) else None


def _normalize_runtime_mode(value: Any) -> Optional[str]:
    token = str(value or "").strip().lower()
    return token if token in SUPPORTED_RUNTIME_MODES else None


def _runtime_binding_install_id(metadata: Dict[str, Any]) -> Optional[str]:
    for key in ("workspace_agent_install_id", "active_agent_install_id", "master_agent_install_id"):
        token = _hint_text(metadata.get(key))
        if token:
            return token
    return None


def _runtime_selection_payload(metadata: Dict[str, Any]) -> Dict[str, Any]:
    return _metadata_dict(metadata.get("runtime_selection"))


def _runtime_binding_requested(metadata: Dict[str, Any]) -> bool:
    if _runtime_binding_install_id(metadata):
        return True
    if _hint_text(metadata.get("runtime_attachment_id")):
        return True
    if _hint_text(metadata.get("runtime_attachment_kind")):
        return True
    if _hint_text(metadata.get("workspace_runtime_deployment_mode")):
        return True
    return bool(_runtime_selection_payload(metadata))


def _expected_runtime_mode_for_target(selected_target: str) -> str:
    return "local_secure" if selected_target == "local_companion" else "hosted_secure"


def _resolve_runtime_mode_from_metadata(metadata: Dict[str, Any], *, selected_target: str) -> str:
    explicit = _normalize_runtime_mode(metadata.get("runtime_mode"))
    if explicit:
        if selected_target == "local_companion" and explicit not in LOCAL_RUNTIME_MODES:
            raise HTTPException(
                status_code=409,
                detail="Agent mode does not match Gateway placement.",
            )
        if selected_target != "local_companion" and explicit in LOCAL_RUNTIME_MODES:
            raise HTTPException(
                status_code=409,
                detail="Agent mode does not match hosted placement.",
            )
        return explicit
    return _expected_runtime_mode_for_target(selected_target)


def _merge_runtime_selection_metadata(
    metadata: Dict[str, Any],
    *,
    runtime_selection: Dict[str, Any],
    runtime_mode: str,
    selected_target: str,
) -> Dict[str, Any]:
    resolved = dict(metadata)
    selected_attachment = _metadata_dict(runtime_selection.get("selected_attachment"))
    selection_target = _hint_text(runtime_selection.get("execution_target_selected"))
    if not selection_target:
        raise HTTPException(status_code=409, detail="Computer selection is incomplete.")
    if selection_target != selected_target:
        raise HTTPException(
            status_code=409,
            detail="Computer selection does not match the resolved execution target.",
        )
    if runtime_mode in LOCAL_RUNTIME_MODES and selection_target != "local_companion":
        raise HTTPException(
            status_code=409,
            detail="My Computer mode requires Gateway.",
        )
    if runtime_mode == "hosted_secure" and selection_target != "cloud":
        raise HTTPException(
            status_code=409,
            detail="Hosted secure mode requires hosted execution.",
        )
    attachment_id = _hint_text(selected_attachment.get("attachment_id")) or _hint_text(resolved.get("runtime_attachment_id"))
    if not attachment_id:
        raise HTTPException(status_code=409, detail="Computer selection is missing its attachment id.")
    attachment_kind = _hint_text(selected_attachment.get("attachment_kind")) or _hint_text(resolved.get("runtime_attachment_kind"))
    if not attachment_kind:
        raise HTTPException(status_code=409, detail="Runtime attachment selection is missing attachment_kind.")
    provided_attachment_id = _hint_text(resolved.get("runtime_attachment_id"))
    if provided_attachment_id and provided_attachment_id != attachment_id:
        raise HTTPException(
            status_code=409,
            detail="Runtime attachment metadata does not match the resolved attachment.",
        )
    provided_attachment_kind = _hint_text(resolved.get("runtime_attachment_kind"))
    if provided_attachment_kind and provided_attachment_kind != attachment_kind:
        raise HTTPException(
            status_code=409,
            detail="Runtime attachment kind does not match the resolved attachment.",
        )
    machine_target = _hint_text(runtime_selection.get("machine_target"))
    provided_machine_target = _hint_text(resolved.get("machine_target"))
    if runtime_mode in LOCAL_RUNTIME_MODES:
        effective_machine_target = machine_target or provided_machine_target
        if not effective_machine_target:
            raise HTTPException(
                status_code=409,
                detail="Local runtime selection is missing machine_target.",
            )
        if provided_machine_target and machine_target and provided_machine_target != machine_target:
            raise HTTPException(
                status_code=409,
                detail="Runtime machine target does not match the resolved attachment.",
            )
        resolved["machine_target"] = effective_machine_target
    resolved["runtime_selection"] = dict(runtime_selection)
    resolved["runtime_attachment_id"] = attachment_id
    resolved["runtime_attachment_kind"] = attachment_kind
    deployment_mode = _hint_text(runtime_selection.get("deployment_mode"))
    if deployment_mode:
        resolved["workspace_runtime_deployment_mode"] = deployment_mode
    resolved["execution_target"] = selection_target
    resolved["execution_target_selected"] = selection_target
    return resolved


def resolve_run_execution_boundary(
    metadata: Dict[str, Any],
    *,
    decide_execution_target_fn: Callable[..., Dict[str, Any]],
    apply_execution_route_metadata_fn: Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]],
    schedule_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    def _enforce_run_routing_decision(
        *,
        operation: str,
        selected_target: str,
        runtime_mode: str,
        runtime_selection: Optional[Dict[str, Any]] = None,
        precheck: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "operation": operation,
            "workspace_id": _run_service_text(metadata.get("workspace_id")) or "default",
            "run_id": _run_service_text(metadata.get("run_id")),
            "schedule_id": _run_service_text(schedule_id),
            "parent_run_id": _run_service_text(metadata.get("parent_run_id")),
            "execution_target_selected": selected_target,
            "execution_target": selected_target,
            "runtime_mode": runtime_mode,
        }
        if operation == "execution_boundary":
            selected_attachment = _metadata_dict((runtime_selection or {}).get("selected_attachment"))
            payload.update(
                {
                    "runtime_selection_present": bool(runtime_selection),
                    "runtime_attachment_id": _hint_text(selected_attachment.get("attachment_id")),
                    "runtime_attachment_kind": _hint_text(selected_attachment.get("attachment_kind")),
                    "runtime_selection_target": _hint_text((runtime_selection or {}).get("execution_target_selected")) or selected_target,
                    "machine_target": _hint_text((runtime_selection or {}).get("machine_target")) or _hint_text(metadata.get("machine_target")),
                    "workspace_runtime_deployment_mode": _hint_text((runtime_selection or {}).get("deployment_mode"))
                    or _hint_text(metadata.get("workspace_runtime_deployment_mode")),
                }
            )
        elif operation == "local_confirmation":
            precheck = precheck if isinstance(precheck, dict) else {}
            payload.update(
                {
                    "outcome_pack": _run_service_text(metadata.get("outcome_pack")),
                    "blocked_count": int(precheck.get("blocked_count") or 0),
                    "require_confirmation_count": int(precheck.get("require_confirmation_count") or 0),
                    "approval_required_count": int(precheck.get("approval_required_count") or 0),
                }
            )
        try:
            return rust_runtime_kernel_client.run_runtime_kernel_enforced(
                "run-routing-decision",
                payload,
                allow_approval_required=(operation == "local_confirmation"),
            )
        except rust_runtime_kernel_client.RustKernelDecisionError as exc:
            detail = _run_service_text(exc.reason) or "rust_run_routing_denied"
            raise HTTPException(status_code=409, detail=f"Rust run-routing gate blocked {operation}: {detail}") from exc

    def _require_run_routing_next_action(decision: Dict[str, Any], *allowed_actions: str) -> str:
        next_action = _run_service_text(decision.get("next_action"))
        if next_action in allowed_actions:
            return next_action
        raise HTTPException(
            status_code=409,
            detail=f"Rust run-routing gate blocked {decision.get('operation') or 'operation'}: unexpected_next_action:{next_action or 'missing'}",
        )

    try:
        route = decide_execution_target_fn(metadata, schedule_id=schedule_id)
    except TypeError:
        route = decide_execution_target_fn(metadata)
    resolved_metadata = apply_execution_route_metadata_fn(dict(metadata), route)
    selected_target = str(
        route.get("selected")
        or resolved_metadata.get("execution_target_selected")
        or resolved_metadata.get("execution_target")
        or ""
    ).strip().lower() or "cloud"
    runtime_mode = _resolve_runtime_mode_from_metadata(resolved_metadata, selected_target=selected_target)
    runtime_selection = _runtime_selection_payload(resolved_metadata)
    _require_run_routing_next_action(
        _enforce_run_routing_decision(
            operation="execution_boundary",
            selected_target=selected_target,
            runtime_mode=runtime_mode,
            runtime_selection=runtime_selection,
        ),
        "write_execution_boundary_metadata",
    )
    if runtime_selection:
        resolved_metadata = _merge_runtime_selection_metadata(
            resolved_metadata,
            runtime_selection=runtime_selection,
            runtime_mode=runtime_mode,
            selected_target=selected_target,
        )
    elif _runtime_binding_requested(resolved_metadata) or (
        selected_target == "local_companion" and _hint_text(resolved_metadata.get("machine_target"))
    ):
        raise HTTPException(
            status_code=409,
            detail="Runtime attachment selection is required before this run can start.",
        )
    resolved_metadata["runtime_mode"] = runtime_mode
    current_scope = _metadata_dict(resolved_metadata.get("runtime_scope"))
    if _normalize_runtime_mode(current_scope.get("mode")) == runtime_mode:
        resolved_metadata["runtime_scope"] = current_scope
    else:
        resolved_metadata["runtime_scope"] = execution_sandbox_service.runtime_scope(
            runtime_mode=runtime_mode,
            runtime_profile=_metadata_dict(resolved_metadata.get("runtime_profile")),
        )
    return resolved_metadata, route


async def resolve_turn_runtime_attachment_selection(
    req: DurableTurnExecutionRequest,
    *,
    turn_request: AgentTurnRequest,
) -> DurableTurnExecutionRequest:
    metadata = _metadata_dict(getattr(req, "metadata", None))
    if _runtime_selection_payload(metadata):
        return req
    install_id = _runtime_binding_install_id(metadata)
    if not install_id:
        return req

    from server_modules import agent_registry_repository, runtime_attachment_service

    install = await agent_registry_repository.get_workspace_agent_install_bundle(
        install_id,
        tenant_id=str(turn_request.tenant_id or "").strip() or None,
        workspace_id=str(req.workspace_id or turn_request.workspace_id or "default").strip() or "default",
    )
    if not isinstance(install, dict):
        raise HTTPException(status_code=404, detail="Specialist install not found.")
    runtime_profile = install.get("runtime_profile") if isinstance(install.get("runtime_profile"), dict) else {}
    runtime_mode = _normalize_runtime_mode(install.get("runtime_mode")) or "hosted_secure"
    runtime_scope = execution_sandbox_service.runtime_scope(
        runtime_mode=runtime_mode,
        runtime_profile=runtime_profile,
        install=install,
    )
    try:
        runtime_selection = await runtime_attachment_service.resolve_install_runtime_plan(
            tenant_id=str(turn_request.tenant_id or "").strip() or None,
            workspace_id=str(req.workspace_id or turn_request.workspace_id or "default").strip() or "default",
            install=install,
            requested_machine_target=_hint_text(metadata.get("machine_target")) or _hint_text(turn_request.machine_target),
            policy_context=_metadata_dict(turn_request.policy_context),
        )
    except runtime_attachment_service.RuntimeAttachmentSelectionError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    metadata["runtime_mode"] = runtime_mode
    metadata["runtime_scope"] = runtime_scope
    metadata["runtime_selection"] = runtime_selection
    metadata["runtime_profile_id"] = metadata.get("runtime_profile_id") or install.get("runtime_profile_id")
    metadata["runtime_profile_label"] = metadata.get("runtime_profile_label") or runtime_profile.get("label")
    metadata["runtime_id"] = metadata.get("runtime_id") or runtime_profile.get("runtime_id")
    metadata["machine_id"] = metadata.get("machine_id") or runtime_profile.get("machine_id")
    metadata["runtime_attachment_id"] = runtime_selection.get("selected_attachment", {}).get("attachment_id")
    metadata["runtime_attachment_kind"] = runtime_selection.get("selected_attachment", {}).get("attachment_kind")
    metadata["workspace_runtime_deployment_mode"] = runtime_selection.get("deployment_mode")
    metadata["execution_target"] = str(runtime_selection.get("execution_target_selected") or metadata.get("execution_target") or "").strip() or None
    metadata["execution_target_selected"] = str(runtime_selection.get("execution_target_selected") or metadata.get("execution_target_selected") or "").strip() or None
    machine_target = _hint_text(runtime_selection.get("machine_target"))
    if machine_target:
        metadata["machine_target"] = machine_target
    req.metadata = metadata
    return req


def build_turn_seed_from_request(
    turn_request: AgentTurnRequest,
    *,
    base_request: Optional[Any] = None,
) -> Dict[str, Any]:
    base_metadata = _metadata_dict(getattr(base_request, "metadata", None))
    hint_metadata = _metadata_dict(turn_request.context_hints.get("metadata"))
    policy_context = _metadata_dict(turn_request.policy_context)
    metadata = {**hint_metadata, **base_metadata}
    metadata = bind_agent_turn_metadata(metadata, turn_request, source="runs/start")
    if policy_context:
        metadata["policy_context"] = dict(policy_context)
        session_mode = _hint_text(policy_context.get("session_mode"))
        if session_mode and not _hint_text(metadata.get("session_mode")):
            metadata["session_mode"] = session_mode
        effective_session_mode = _hint_text(policy_context.get("effective_session_mode"))
        if effective_session_mode and not _hint_text(metadata.get("effective_session_mode")):
            metadata["effective_session_mode"] = effective_session_mode
        requested_session_mode = _hint_text(policy_context.get("requested_session_mode"))
        if requested_session_mode and not _hint_text(metadata.get("requested_session_mode")):
            metadata["requested_session_mode"] = requested_session_mode
        policy_trust_mode = _hint_text(policy_context.get("trust_mode"))
        if policy_trust_mode and not _hint_text(metadata.get("trust_mode")):
            metadata["trust_mode"] = policy_trust_mode
        runtime_trust_zone = _hint_text(policy_context.get("runtime_trust_zone"))
        if runtime_trust_zone and not _hint_text(metadata.get("runtime_trust_zone")):
            metadata["runtime_trust_zone"] = runtime_trust_zone
        elevated_mode = _hint_text(policy_context.get("elevated_mode"))
        if elevated_mode and not _hint_text(metadata.get("elevated_mode")):
            metadata["elevated_mode"] = elevated_mode
        elevated = policy_context.get("elevated")
        if "elevated" not in metadata and isinstance(elevated, (dict, str)):
            metadata["elevated"] = elevated
        if "interactive_approvals" not in metadata and isinstance(policy_context.get("interactive_approvals"), bool):
            metadata["interactive_approvals"] = bool(policy_context.get("interactive_approvals"))
    if turn_request.machine_target and not _hint_text(metadata.get("machine_target")):
        metadata["machine_target"] = str(turn_request.machine_target).strip()
    if not _hint_text(metadata.get("channel")):
        metadata["channel"] = str(turn_request.channel or "").strip() or "web"
    if not _hint_text(metadata.get("session_id")):
        metadata["session_id"] = str(turn_request.session_id or "").strip()

    return {
        "engine": _hint_text(getattr(base_request, "engine", None)) or _hint_text(turn_request.context_hints.get("engine")) or "orion",
        "workflow_id": _hint_text(getattr(base_request, "workflow_id", None)) or _hint_text(turn_request.context_hints.get("workflow_id")),
        "workflow_version_id": (
            _hint_text(getattr(base_request, "workflow_version_id", None))
            or _hint_text(turn_request.context_hints.get("workflow_version_id"))
            or _hint_text(metadata.get("workflow_version_id"))
        ),
        "workspace_id": str(turn_request.workspace_id or getattr(base_request, "workspace_id", None) or "default").strip() or "default",
        "user_goal": _hint_text(turn_request.message) or _hint_text(getattr(base_request, "user_goal", None)) or "",
        "business_plan": _hint_text(getattr(base_request, "business_plan", None)) or _hint_text(turn_request.context_hints.get("business_plan")),
        "max_iterations": (
            getattr(base_request, "max_iterations", None)
            if getattr(base_request, "max_iterations", None) is not None
            else turn_request.context_hints.get("max_iterations")
        ),
        "agent_role": _hint_text(getattr(base_request, "agent_role", None)) or _hint_text(turn_request.context_hints.get("agent_role")),
        "parent_run_id": _hint_text(getattr(base_request, "parent_run_id", None)) or _hint_text(metadata.get("parent_run_id")),
        "provider": _hint_text(getattr(base_request, "provider", None)) or _hint_text(turn_request.context_hints.get("provider")),
        "model": _hint_text(getattr(base_request, "model", None)) or _hint_text(turn_request.context_hints.get("model")),
        "credential_id": _hint_text(getattr(base_request, "credential_id", None)) or _hint_text(turn_request.context_hints.get("credential_id")),
        "agents": _hint_list(getattr(base_request, "agents", None)) or _hint_list(turn_request.context_hints.get("agents")) or [],
        "metadata": metadata,
    }


def build_durable_turn_execution_request(
    turn_request: AgentTurnRequest,
    *,
    base_request: Optional[Any] = None,
) -> DurableTurnExecutionRequest:
    return DurableTurnExecutionRequest(
        **build_turn_seed_from_request(turn_request, base_request=base_request),
    )


def build_run_preview_context(
    req: Any,
    *,
    metadata: Dict[str, Any],
    workflow_snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "workflow_id": req.workflow_id,
        "workflow_version_id": getattr(req, "workflow_version_id", None) or (
            workflow_snapshot.get("workflowVersionId") if isinstance(workflow_snapshot, dict) else None
        ),
        "workspace_id": req.workspace_id,
        "user_goal": req.user_goal,
        "business_plan": req.business_plan,
        "max_iterations": getattr(req, "max_iterations", None),
        "agent_role": req.agent_role,
        "provider": req.provider,
        "model": req.model,
        "credential_id": req.credential_id,
        "agents": req.agents or [],
        "tools": metadata.get("tools") if isinstance(metadata.get("tools"), list) else [],
        "metadata": metadata,
        "workflow_definition": workflow_snapshot.get("definition") if isinstance(workflow_snapshot, dict) else None,
        "workflow_name": workflow_snapshot.get("name") if isinstance(workflow_snapshot, dict) else None,
        "workflow_status": workflow_snapshot.get("status") if isinstance(workflow_snapshot, dict) else None,
    }


def _fetch_workflow_snapshot_for_request(
    fetcher: Callable[..., Any],
    req: Any,
    *,
    metadata: Dict[str, Any],
) -> Any:
    workflow_id = str(getattr(req, "workflow_id", None) or "").strip()
    if not workflow_id:
        return None
    workflow_version_id = str(
        getattr(req, "workflow_version_id", None)
        or metadata.get("workflow_version_id")
        or ""
    ).strip() or None
    try:
        return fetcher(
            workflow_id,
            workflow_version_id=workflow_version_id,
            workspace_id=str(getattr(req, "workspace_id", None) or metadata.get("workspace_id") or "").strip() or None,
            tenant_id=str(metadata.get("tenant_id") or "").strip() or None,
        )
    except TypeError:
        return fetcher(workflow_id)


def _workflow_snapshot_from_metadata(
    req: Any,
    *,
    metadata: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    direct_snapshot = metadata.get("workflow_snapshot") if isinstance(metadata.get("workflow_snapshot"), dict) else None
    if isinstance(direct_snapshot, dict) and isinstance(direct_snapshot.get("definition"), dict):
        return dict(direct_snapshot)
    direct_definition = metadata.get("workflow_definition") if isinstance(metadata.get("workflow_definition"), dict) else None
    if not isinstance(direct_definition, dict):
        return None
    workflow_id = str(
        getattr(req, "workflow_id", None)
        or metadata.get("compiled_workflow_id")
        or metadata.get("workflow_id")
        or ""
    ).strip()
    workflow_version_id = str(
        getattr(req, "workflow_version_id", None)
        or metadata.get("compiled_workflow_version_id")
        or metadata.get("workflow_version_id")
        or ""
    ).strip()
    return {
        "id": workflow_id or "compiled-template",
        "name": str(metadata.get("workflow_name") or metadata.get("compiled_workflow_name") or "").strip() or "Compiled Template",
        "status": str(metadata.get("workflow_status") or "compiled").strip() or "compiled",
        "definition": dict(direct_definition),
        "workflowVersionId": workflow_version_id or None,
        "currentVersionId": workflow_version_id or None,
        "versionNumber": metadata.get("workflow_version_number"),
        "metadata": dict(metadata.get("workflow_metadata") or {}) if isinstance(metadata.get("workflow_metadata"), dict) else {},
    }


def build_run_routing_preview(
    req: RunStartRequest,
    *,
    services: RunRoutingPreviewServices,
) -> Dict[str, Any]:
    prepared = services.prepare_run_start_request(req)
    metadata, route = resolve_run_execution_boundary(
        dict(prepared["metadata"]),
        decide_execution_target_fn=decide_execution_target,
        apply_execution_route_metadata_fn=apply_execution_route_metadata,
    )
    workflow_snapshot = prepared.get("workflow_snapshot") if isinstance(prepared.get("workflow_snapshot"), dict) else None
    preview_context = build_run_preview_context(
        req,
        metadata=metadata,
        workflow_snapshot=workflow_snapshot,
    )
    return {
        "engine": prepared["engine"],
        "metadata": metadata,
        "route": route,
        "tool_policy_precheck": services.compute_tool_policy_precheck(preview_context),
        "workflow_snapshot": workflow_snapshot,
    }


async def build_run_precheck_result(
    req: RunStartRequest,
    *,
    services: RunRoutingPreviewServices,
) -> Dict[str, Any]:
    preview = build_run_routing_preview(req, services=services)
    doctor_preflight = await build_doctor_run_gate_live(
        execution_target=preview["route"]["selected"],
        metadata=preview["metadata"],
        provider=req.provider,
        credential_id=req.credential_id,
    )
    return {
        **preview,
        "doctor_preflight": doctor_preflight,
    }


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def normalize_requested_max_iterations(value: Any) -> Optional[int]:
    if value in {None, ""}:
        return None
    parsed = safe_int(value, 0)
    if parsed <= 0:
        raise HTTPException(status_code=400, detail="max_iterations must be greater than zero.")
    return parsed


def local_execution_requires_start_confirmation(
    metadata: Dict[str, Any],
    precheck: Dict[str, Any],
    *,
    local_execution_target: str,
    local_execution_pack_id: str,
) -> bool:
    target = str(metadata.get("execution_target_selected") or metadata.get("execution_target") or "").strip().lower()
    outcome_pack = str(metadata.get("outcome_pack") or "").strip().lower()
    if target != local_execution_target:
        return False
    if outcome_pack != local_execution_pack_id:
        return False
    return bool(precheck.get("require_confirmation_count") or precheck.get("approval_required_count"))


def precheck_human_action_labels(precheck: Dict[str, Any], decision: str = "require_confirmation") -> List[str]:
    items = precheck.get("items") if isinstance(precheck.get("items"), list) else []
    labels: List[str] = []
    seen: set[str] = set()
    accepted = {str(decision or "").strip().lower()}
    if "require_confirmation" in accepted:
        accepted.add("approval_required")
    if "deny" in accepted:
        accepted.add("blocked")
    for item in items:
        if not isinstance(item, dict):
            continue
        item_decision = str(item.get("execution_decision") or item.get("decision") or "").strip().lower()
        if item_decision not in accepted:
            continue
        explicit_label = str(item.get("approval_label") or "").strip()
        if explicit_label:
            clean = explicit_label.strip()
            if clean and clean.lower() not in seen:
                seen.add(clean.lower())
                labels.append(clean)
            continue
        capabilities = item.get("capabilities") if isinstance(item.get("capabilities"), list) else []
        capability_labels = [
            str(cap.get("title") or "").strip()
            for cap in capabilities
            if isinstance(cap, dict) and str(cap.get("title") or "").strip()
        ]
        raw_label = capability_labels[0] if capability_labels else str(item.get("tool_id") or "").strip().replace("_", " ")
        clean = raw_label.strip()
        if clean and clean.lower() not in seen:
            seen.add(clean.lower())
            labels.append(clean)
    return labels


def local_execution_confirmation_prompt(precheck: Dict[str, Any]) -> str:
    labels = precheck_human_action_labels(precheck, decision="require_confirmation")
    if labels:
        return f"Confirmation required before Gateway execution: {', '.join(labels)}."
    return "Confirmation required before Gateway execution."


def local_execution_block_prompt(precheck: Dict[str, Any]) -> str:
    labels = precheck_human_action_labels(precheck, decision="deny")
    if labels:
        return f"Run blocked by local execution policy: {', '.join(labels)}."
    return "Run blocked by local execution policy."


def mark_local_execution_tools_approved(metadata: Dict[str, Any]) -> None:
    precheck = metadata.get("tool_policy_precheck") if isinstance(metadata.get("tool_policy_precheck"), dict) else None
    if not isinstance(precheck, dict):
        return

    approved_tools = [
        str(item).strip().lower()
        for item in (precheck.get("require_confirmation") or precheck.get("approval_required") or [])
        if str(item).strip()
    ]
    if not approved_tools:
        return

    allowed = [
        str(item).strip().lower()
        for item in (precheck.get("allowed") or [])
        if str(item).strip()
    ]
    allowed_set = set(allowed)
    for tool in approved_tools:
        allowed_set.add(tool)

    items = precheck.get("items") if isinstance(precheck.get("items"), list) else []
    rewritten_items: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        next_item = dict(item)
        tool_id = str(next_item.get("tool_id") or "").strip().lower()
        if tool_id in approved_tools:
            next_item["execution_decision"] = "allow"
            next_item["decision"] = "allow"
            next_item["reason"] = "confirmed_for_local_execution"
        rewritten_items.append(next_item)

    precheck["require_confirmation"] = []
    precheck["require_confirmation_count"] = 0
    precheck["approval_required"] = []
    precheck["approval_required_count"] = 0
    precheck["allowed"] = sorted(allowed_set)
    precheck["allow_count"] = len(precheck["allowed"])
    precheck["items"] = rewritten_items
    metadata["tool_policy_precheck"] = precheck


def apply_browser_execution_metadata(metadata: Dict[str, Any]) -> None:
    return


def build_run_start_request_from_turn(
    turn_request: AgentTurnRequest,
    *,
    base_request: Optional[Any] = None,
) -> RunStartRequest:
    return RunStartRequest(
        **build_turn_seed_from_request(turn_request, base_request=base_request),
    )


def prepare_run_start_request(
    req: Any,
    *,
    services: RunPreparationServices,
) -> Dict[str, Any]:
    engine = (req.engine or "orion").lower().strip()
    metadata = dict(req.metadata) if isinstance(req.metadata, dict) else {}
    normalized_max_iterations = services.normalize_requested_max_iterations(getattr(req, "max_iterations", None))
    outcome_pack = str(metadata.get("outcome_pack") or "").strip().lower()
    if engine not in services.engine_registry:
        raise HTTPException(status_code=400, detail=f"Unsupported engine '{engine}'")
    if engine == "orion" and services.engine_validation_errors:
        raise HTTPException(status_code=503, detail="Empyralis runtime validation failed.")
    if engine == "orion" and outcome_pack and outcome_pack not in services.supported_outcome_packs:
        raise HTTPException(status_code=400, detail=f"Unsupported outcome pack '{outcome_pack}'")

    if engine == "orion":
        from server_modules import runtime_policy as runtime_policy_module

        raw_trust_mode = str(metadata.get("trust_mode") or "").strip().lower()
        normalized_trust_mode = services.normalize_trust_mode(raw_trust_mode)
        if (
            raw_trust_mode
            and raw_trust_mode not in services.trust_mode_aliases
            and raw_trust_mode not in services.valid_trust_modes
        ):
            raise HTTPException(
                status_code=400,
                detail="Unsupported trust_mode. Use one of: auto, guarded, strict, cost_guard, sensitive_guard.",
            )
        metadata["trust_mode"] = normalized_trust_mode

        if "pack_inputs" in metadata and not isinstance(metadata.get("pack_inputs"), dict):
            raise HTTPException(status_code=400, detail="metadata.pack_inputs must be an object.")
        if "outcome_scope" in metadata and not isinstance(metadata.get("outcome_scope"), list):
            raise HTTPException(status_code=400, detail="metadata.outcome_scope must be a list.")
        if "connector_credential_id" in metadata and metadata.get("connector_credential_id") is not None:
            if not isinstance(metadata.get("connector_credential_id"), str):
                raise HTTPException(status_code=400, detail="metadata.connector_credential_id must be a string.")
        if "approval_rules" in metadata and not isinstance(metadata.get("approval_rules"), dict):
            raise HTTPException(status_code=400, detail="metadata.approval_rules must be an object.")
        if "schedule" in metadata and not isinstance(metadata.get("schedule"), dict):
            raise HTTPException(status_code=400, detail="metadata.schedule must be an object.")
        if "execution_target" in metadata:
            normalized_target = services.normalize_execution_target(metadata.get("execution_target"))
            if normalized_target not in services.valid_execution_targets:
                raise HTTPException(
                    status_code=400,
                    detail="Unsupported execution_target. Use one of: auto, cloud, local_companion.",
                )
            metadata["execution_target"] = normalized_target
        metadata["runtime_trust_zone"] = runtime_policy_module.normalize_runtime_trust_zone(
            metadata.get("runtime_trust_zone"),
            target=metadata.get("execution_target"),
        )
        if "elevated_mode" in metadata:
            metadata["elevated_mode"] = runtime_policy_module.normalize_elevated_mode(metadata.get("elevated_mode"))
        if "elevated" in metadata:
            raw_elevated = metadata.get("elevated")
            if isinstance(raw_elevated, str):
                metadata["elevated"] = {
                    "mode": runtime_policy_module.normalize_elevated_mode(raw_elevated),
                    "runtime_trust_zone": metadata.get("runtime_trust_zone"),
                }
            elif isinstance(raw_elevated, dict):
                normalized_elevated = dict(raw_elevated)
                normalized_elevated["mode"] = runtime_policy_module.normalize_elevated_mode(
                    normalized_elevated.get("mode") or metadata.get("elevated_mode")
                )
                normalized_elevated["runtime_trust_zone"] = runtime_policy_module.normalize_runtime_trust_zone(
                    normalized_elevated.get("runtime_trust_zone") or metadata.get("runtime_trust_zone"),
                    target=metadata.get("execution_target"),
                )
                for field_name in ("allowed_action_classes", "denied_action_classes"):
                    if field_name in normalized_elevated and not isinstance(normalized_elevated.get(field_name), list):
                        raise HTTPException(status_code=400, detail=f"metadata.elevated.{field_name} must be a list.")
                    normalized_elevated[field_name] = runtime_policy_module.normalize_action_type_list(
                        normalized_elevated.get(field_name) or []
                    )
                if "ttl_seconds" in normalized_elevated:
                    try:
                        normalized_elevated["ttl_seconds"] = max(0, int(normalized_elevated.get("ttl_seconds")))
                    except Exception as exc:
                        raise HTTPException(status_code=400, detail="metadata.elevated.ttl_seconds must be an integer.") from exc
                metadata["elevated"] = normalized_elevated
            else:
                raise HTTPException(status_code=400, detail="metadata.elevated must be a string or object.")

    if normalized_max_iterations is not None:
        metadata["max_iterations"] = normalized_max_iterations
    else:
        metadata.pop("max_iterations", None)

    parent_run_id = services.normalize_run_id_token(req.parent_run_id or metadata.get("parent_run_id"))
    if parent_run_id:
        metadata["parent_run_id"] = parent_run_id
    elif "parent_run_id" in metadata:
        metadata.pop("parent_run_id", None)

    delegation_root_run_id = services.normalize_run_id_token(metadata.get("delegation_root_run_id"))
    if delegation_root_run_id:
        metadata["delegation_root_run_id"] = delegation_root_run_id
    elif "delegation_root_run_id" in metadata:
        metadata.pop("delegation_root_run_id", None)

    delegated_by_run_id = services.normalize_run_id_token(metadata.get("delegated_by_run_id"))
    if delegated_by_run_id:
        metadata["delegated_by_run_id"] = delegated_by_run_id
    elif "delegated_by_run_id" in metadata:
        metadata.pop("delegated_by_run_id", None)

    delegated_by_role = services.normalize_agent_role(metadata.get("delegated_by_role"))
    if delegated_by_role:
        metadata["delegated_by_role"] = delegated_by_role
    elif "delegated_by_role" in metadata:
        metadata.pop("delegated_by_role", None)

    agent_role, agent_role_source = services.detect_agent_role(req, metadata)
    metadata["agent_role"] = agent_role
    metadata["agent_role_source"] = agent_role_source

    app_id = str(metadata.get("app_id") or "").strip()
    if app_id:
        app_permissions = services.resolve_app_permissions(app_id)
        metadata["app_id"] = app_id
        metadata["app_permissions"] = app_permissions
        app_policy = services.action_policy_from_app_permissions(app_permissions)
        existing_policy = metadata.get("action_policy") if isinstance(metadata.get("action_policy"), dict) else {}
        metadata["action_policy"] = services.merge_action_policies(
            {"action_policy": existing_policy},
            {"action_policy": app_policy},
        )
        metadata = app_bridge_service.enforce_app_metadata_contract(metadata=metadata)

    workflow_snapshot = _workflow_snapshot_from_metadata(req, metadata=metadata)
    if str(req.workflow_id or "").strip():
        if not isinstance(workflow_snapshot, dict):
            workflow_snapshot = _fetch_workflow_snapshot_for_request(
                services.fetch_workflow_snapshot,
                req,
                metadata=metadata,
            )
        if isinstance(workflow_snapshot, dict):
            metadata["workflow_schema_version"] = str(
                workflow_snapshot.get("definition", {}).get("version") or ""
            ).strip() or None
            if workflow_snapshot.get("name") and "workflow_name" not in metadata:
                metadata["workflow_name"] = workflow_snapshot.get("name")
            if workflow_snapshot.get("status"):
                metadata["workflow_status"] = workflow_snapshot.get("status")
            workflow_version_id = str(
                workflow_snapshot.get("workflowVersionId")
                or workflow_snapshot.get("currentVersionId")
                or getattr(req, "workflow_version_id", None)
                or ""
            ).strip()
            if workflow_version_id:
                metadata["workflow_version_id"] = workflow_version_id
            workflow_version_number = workflow_snapshot.get("versionNumber")
            if workflow_version_number is not None:
                metadata["workflow_version_number"] = workflow_version_number

    if callable(services.postprocess_metadata):
        metadata = services.postprocess_metadata(req, metadata)

    return {
        "engine": engine,
        "metadata": metadata,
        "workflow_snapshot": workflow_snapshot,
    }


def prepare_legacy_run_start_request(
    request: RunStartRequest,
    *,
    services: LegacyRunPreparationServices,
) -> Dict[str, Any]:
    preparation_services = services.build_preparation_services()
    return prepare_run_start_request(request, services=preparation_services)


def create_run_result_from_request(
    request: RunStartRequest,
    *,
    services: RunCreationServices,
    schedule_id: Optional[str] = None,
) -> Dict[str, Any]:
    if schedule_id is not None:
        result = services.create_run_from_request(request, schedule_id=schedule_id)
    else:
        result = services.create_run_from_request(request)
    return dict(result) if isinstance(result, dict) else {"result": result}


def build_run_prepared_result_services(
    *,
    create_run_from_prepared_request: Any,
    build_result: Any,
) -> RunPreparedResultServices:
    return RunPreparedResultServices(
        create_run_from_prepared_request=create_run_from_prepared_request,
        build_result=build_result,
    )


def create_run_result_from_prepared_request(
    request: RunStartRequest,
    *,
    prepared: Dict[str, Any],
    services: PreparedRunCreationServices,
    result_services: RunPreparedResultServices,
    schedule_id: Optional[str] = None,
) -> Dict[str, Any]:
    created = result_services.create_run_from_prepared_request(
        request,
        prepared=prepared,
        services=services,
        schedule_id=schedule_id,
    )
    result = result_services.build_result(request, created=created)
    return dict(result) if isinstance(result, dict) else {"result": result}


def _run_service_text(value: Any) -> str:
    return str(value or "").strip()


def _run_service_idempotency_key(req: Any, metadata: Dict[str, Any], *, schedule_id: Optional[str]) -> str:
    for key in (
        "idempotency_key",
        "client_request_id",
        "request_id",
        "run_request_id",
        "turn_id",
    ):
        token = _run_service_text(metadata.get(key) or getattr(req, key, None))
        if token:
            return token
    seed = {
        "schedule_id": schedule_id,
        "workspace_id": metadata.get("workspace_id") or getattr(req, "workspace_id", None),
        "tenant_id": metadata.get("tenant_id") or getattr(req, "tenant_id", None),
        "thread_id": metadata.get("thread_id") or getattr(req, "thread_id", None),
        "session_id": metadata.get("session_id") or getattr(req, "session_id", None),
        "provider": getattr(req, "provider", None),
        "credential_id": getattr(req, "credential_id", None),
        "engine": metadata.get("engine") or getattr(req, "engine", None),
    }
    digest = hashlib.sha256(json.dumps(seed, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return f"run-create:{digest[:32]}"


def _enforce_rust_run_service_decision(
    *,
    operation: str,
    req: Any,
    metadata: Dict[str, Any],
    route: Dict[str, Any],
    schedule_id: Optional[str],
    local_confirmation_required: bool = False,
) -> Dict[str, Any]:
    trigger_source = "schedule" if schedule_id else _run_service_text(metadata.get("trigger_source") or "user")
    actor_id = (
        _run_service_text(metadata.get("actor_id"))
        or _run_service_text(metadata.get("owner_user_id"))
        or _run_service_text(getattr(req, "actor_id", None))
        or _run_service_text(getattr(req, "user_id", None))
        or ("scheduler" if schedule_id else "system")
    )
    payload = {
        "operation": operation,
        "tenant_id": _run_service_text(metadata.get("tenant_id") or getattr(req, "tenant_id", None) or "default"),
        "workspace_id": _run_service_text(metadata.get("workspace_id") or getattr(req, "workspace_id", None)),
        "actor_id": actor_id,
        "actor_role": _run_service_text(metadata.get("actor_role") or "member"),
        "thread_id": _run_service_text(metadata.get("thread_id") or getattr(req, "thread_id", None) or metadata.get("session_id") or "run_start"),
        "workflow_id": _run_service_text(metadata.get("workflow_id") or schedule_id),
        "runtime_target": _run_service_text(route.get("runtime_target") or route.get("selected") or metadata.get("runtime_target") or "auto"),
        "execution_target": _run_service_text(route.get("selected") or metadata.get("execution_target_selected") or metadata.get("execution_target") or "text"),
        "trigger_source": trigger_source,
        "idempotency_key": _run_service_idempotency_key(req, metadata, schedule_id=schedule_id),
        "workspace_access": True,
        "runtime_attached": True,
        "runtime_healthy": True,
        "local_confirmation_required": bool(local_confirmation_required),
        "local_confirmation_provided": False,
        "browser_required": bool(metadata.get("browser_execution_required") or metadata.get("browser_required")),
        "browser_ready": not bool(metadata.get("browser_blocked")),
        "webhook_signature_valid": True,
        "quota_ok": True,
        "budget_ok": True,
        "policy_decision": "allow",
        "risk_level": "normal",
    }
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "run-service-decision",
            payload,
            allow_approval_required=True,
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        detail = _run_service_text(exc.reason) or "rust_run_service_denied"
        raise HTTPException(status_code=409, detail=f"Rust run-service gate blocked {operation}: {detail}")
    metadata["rust_run_service_decision"] = {
        "command": "run-service-decision",
        "decision": decision.get("decision"),
        "next_action": decision.get("next_action"),
        "reason": decision.get("reason"),
        "approval_required": bool(decision.get("approval_required")),
    }
    return decision


def create_legacy_run_result_from_request(
    request: RunStartRequest,
    *,
    services: LegacyRunRequestServices,
    schedule_id: Optional[str] = None,
) -> Dict[str, Any]:
    prepared = services.prepare_run_start_request(request)
    creation_services = services.build_creation_services()
    return create_run_result_from_prepared_request(
        request,
        prepared=prepared,
        services=creation_services,
        result_services=services.result_services,
        schedule_id=schedule_id,
    )


def build_delegated_child_run_request(
    parent_snapshot: Dict[str, Any],
    child_payload: Dict[str, Any],
    *,
    normalize_run_id_token: Callable[[Any], Optional[str]],
    normalize_agent_role: Callable[[Any], str],
    normalize_requested_max_iterations: Callable[[Any], Optional[int]],
    valid_execution_targets: Any,
    note: Optional[str] = None,
) -> RunStartRequest:
    parent_context = parent_snapshot.get("context") if isinstance(parent_snapshot.get("context"), dict) else {}
    parent_metadata = parent_context.get("metadata") if isinstance(parent_context.get("metadata"), dict) else {}
    if not isinstance(parent_metadata, dict):
        parent_metadata = {}
    child_metadata = dict(child_payload.get("metadata") or {})
    parent_run_id = str(parent_snapshot.get("run_id") or "").strip()
    root_run_id = normalize_run_id_token(
        parent_snapshot.get("delegation_root_run_id") or parent_metadata.get("delegation_root_run_id")
    ) or parent_run_id
    delegated_by_role = normalize_agent_role(
        parent_snapshot.get("agent_role") or parent_metadata.get("agent_role")
    ) or "orchestrator"

    child_metadata["parent_run_id"] = parent_run_id
    child_metadata["delegation_root_run_id"] = root_run_id
    child_metadata["delegated_by_run_id"] = parent_run_id
    child_metadata["delegated_by_role"] = delegated_by_role
    # Phase 4: stamp the child's depth (parent_depth + 1). A subagent inherits
    # this and the gate denies it spawning further (default max depth 1).
    child_metadata[SUBAGENT_DEPTH_METADATA_KEY] = subagent_depth_from_metadata(parent_metadata) + 1
    if note:
        child_metadata["delegation_note"] = note
    if "owner_user_id" not in child_metadata:
        owner_user_id = parent_metadata.get("owner_user_id")
        if owner_user_id is not None:
            child_metadata["owner_user_id"] = owner_user_id
    if "user_id" not in child_metadata:
        parent_user_id = parent_metadata.get("user_id")
        if parent_user_id is not None:
            child_metadata["user_id"] = parent_user_id
        elif parent_metadata.get("owner_user_id") is not None:
            child_metadata["user_id"] = parent_metadata.get("owner_user_id")
    if "master_agent_install_id" not in child_metadata:
        parent_master_install_id = (
            parent_metadata.get("master_agent_install_id")
            or parent_metadata.get("workspace_agent_install_id")
        )
        if parent_master_install_id is not None:
            child_metadata["master_agent_install_id"] = parent_master_install_id
    # Delegated specialists must resolve their own install identity; never trust
    # caller-provided active/workspace install bindings on child requests.
    child_metadata.pop("active_agent_install_id", None)
    child_metadata.pop("workspace_agent_install_id", None)

    return RunStartRequest(
        engine=str(child_payload.get("engine") or "orion"),
        workflow_id=child_payload.get("workflow_id"),
        workspace_id=parent_context.get("workspace_id"),
        user_goal=str(child_payload.get("user_goal") or "").strip(),
        business_plan=str(child_payload.get("business_plan") or "").strip() or None,
        max_iterations=normalize_requested_max_iterations(
            child_payload.get("max_iterations")
            or child_metadata.get("max_iterations")
        ),
        agent_role=str(child_payload.get("agent_role") or "").strip(),
        provider=child_payload.get("provider"),
        model=child_payload.get("model"),
        credential_id=child_payload.get("credential_id"),
        parent_run_id=parent_run_id,
        metadata=child_metadata,
    )


def timeout_stale_delegated_child_runs(
    parent_run_id: str,
    child_runs: List[Dict[str, Any]],
    *,
    runs_by_id: Dict[str, Any],
    terminal_run_statuses: Any,
    stale_child_run_timeout_seconds: int,
    normalize_run_id_token: Callable[[Any], Optional[str]],
    parse_utc_ts: Callable[[Any], Any],
    utc_now: Callable[[], Any],
    set_run_status: Callable[[str, str], Any],
    emit_log: Callable[..., Any],
) -> List[str]:
    now = utc_now()
    timed_out: List[str] = []
    for child in child_runs:
        status = str(child.get("status") or "").strip().lower()
        if not status or status in terminal_run_statuses or status in {"waiting", "waiting_for_input"}:
            continue
        run_id = normalize_run_id_token(child.get("run_id"))
        if not run_id:
            run_id = str(child.get("run_id") or "").strip() or None
        if not run_id:
            continue
        last_progress = (
            parse_utc_ts(child.get("local_last_progress_at"))
            or parse_utc_ts(child.get("local_last_heartbeat_at"))
            or parse_utc_ts(child.get("updated_at"))
            or parse_utc_ts(child.get("created_at"))
        )
        if last_progress is None or (now - last_progress).total_seconds() <= stale_child_run_timeout_seconds:
            continue
        live_child = runs_by_id.get(run_id)
        if not isinstance(live_child, dict):
            continue
        live_child["result"] = "Child run timed out after 5 minutes without progress."
        result_data = live_child.get("result_data")
        if not isinstance(result_data, dict):
            result_data = {}
        result_data.update(
            {
                "summary": "Child run timed out after 5 minutes without progress.",
                "error": "delegated_child_timeout",
                "parent_run_id": parent_run_id,
            }
        )
        live_child["result_data"] = result_data
        log_queue = live_child.get("logs")
        if log_queue is not None:
            emit_log(
                log_queue,
                "error",
                "Child run timed out after 5 minutes without progress.",
                event="delegated_child_timeout",
                data={"run_id": run_id, "parent_run_id": parent_run_id},
            )
        set_run_status(run_id, "failed")
        timed_out.append(run_id)
    return timed_out


def build_retry_child_payload(
    parent_snapshot: Dict[str, Any],
    child_snapshot: Dict[str, Any],
    *,
    normalize_run_id_token: Callable[[Any], Optional[str]],
    normalize_agent_role: Callable[[Any], str],
    normalize_requested_max_iterations: Callable[[Any], Optional[int]],
    child_retry_count: Callable[[Dict[str, Any]], int],
    note: Optional[str] = None,
) -> Dict[str, Any]:
    child_context = child_snapshot.get("context") if isinstance(child_snapshot.get("context"), dict) else {}
    child_metadata = child_context.get("metadata") if isinstance(child_context.get("metadata"), dict) else {}
    if not isinstance(child_metadata, dict):
        child_metadata = {}
    retry_root_run_id = normalize_run_id_token(child_metadata.get("retry_root_run_id")) or normalize_run_id_token(
        child_snapshot.get("run_id")
    )
    retry_sequence = int(child_metadata.get("retry_sequence") or 0) + 1
    next_metadata = dict(child_metadata)
    next_metadata["retry_of_run_id"] = str(child_snapshot.get("run_id") or "").strip()
    next_metadata["retry_root_run_id"] = retry_root_run_id
    next_metadata["retry_sequence"] = retry_sequence
    next_metadata["retry_count"] = child_retry_count(child_snapshot) + 1
    if note:
        next_metadata["delegation_retry_note"] = note
    return {
        "agent_role": normalize_agent_role(child_snapshot.get("agent_role") or child_metadata.get("agent_role"))
        or str(child_snapshot.get("agent_role") or "").strip(),
        "user_goal": str(child_context.get("user_goal") or child_snapshot.get("user_goal") or "").strip(),
        "business_plan": str(child_context.get("business_plan") or "").strip() or None,
        "max_iterations": normalize_requested_max_iterations(
            child_context.get("max_iterations") or child_metadata.get("max_iterations")
        ),
        "metadata": next_metadata,
    }


def schedule_auto_retry_for_failed_children(
    parent_snapshot: Dict[str, Any],
    child_runs: List[Dict[str, Any]],
    *,
    runs_by_id: Dict[str, Any],
    parse_utc_ts: Callable[[Any], Any],
    child_lineage_key: Callable[[Dict[str, Any]], str],
    failure_status: Callable[[Any], bool],
    child_retry_count: Callable[[Dict[str, Any]], int],
    safe_int: Callable[[Any, int], int],
    auto_retry_pending: set,
    auto_retry_attempts: Dict[Any, Any],
    auto_retry_pending_lock: Any,
    auto_retry_max_retries: int,
    auto_retry_delay_seconds: float,
    emit_log: Callable[..., Any],
    build_retry_child_payload_fn: Callable[..., Dict[str, Any]],
    build_delegated_child_run_request_fn: Callable[..., RunStartRequest],
    execute_delegated_run_request_fn: Callable[[RunStartRequest], Dict[str, Any]],
    lookup_run_snapshot_fn: Callable[[str], Dict[str, Any]],
    find_run_relationships_fn: Callable[[str, Dict[str, Any]], Any],
    refresh_parent_delegation_state_fn: Callable[..., Any],
    timer_factory: Callable[..., Any],
    persist_live_run_state_fn: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
    triggering_run_id: Optional[str] = None,
) -> set[str]:
    from datetime import datetime, timedelta, timezone

    parent_run_id = str(parent_snapshot.get("run_id") or "").strip()
    if not parent_run_id:
        return set()

    latest_by_lineage: Dict[str, Dict[str, Any]] = {}
    for child in child_runs:
        lineage_key = child_lineage_key(child)
        previous = latest_by_lineage.get(lineage_key)
        child_sort_key = (
            parse_utc_ts(child.get("updated_at")) or parse_utc_ts(child.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
            str(child.get("run_id") or ""),
        )
        previous_sort_key = (
            parse_utc_ts(previous.get("updated_at")) or parse_utc_ts(previous.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
            str(previous.get("run_id") or ""),
        ) if isinstance(previous, dict) else None
        if previous_sort_key is None or child_sort_key > previous_sort_key:
            latest_by_lineage[lineage_key] = child

    scheduled: set[str] = set()
    live_parent = runs_by_id.get(parent_run_id)
    log_queue = live_parent.get("logs") if isinstance(live_parent, dict) else None
    parent_context = live_parent.get("context") if isinstance(live_parent, dict) and isinstance(live_parent.get("context"), dict) else {}
    parent_metadata = parent_context.get("metadata") if isinstance(parent_context, dict) and isinstance(parent_context.get("metadata"), dict) else {}
    if not isinstance(parent_metadata, dict):
        parent_metadata = {}
    pending_retry_state = (
        dict(parent_metadata.get("delegation_pending_retries"))
        if isinstance(parent_metadata.get("delegation_pending_retries"), dict)
        else {}
    )

    def _persist_parent_retry_state() -> None:
        if not isinstance(live_parent, dict):
            return
        context = live_parent.setdefault("context", {})
        if not isinstance(context, dict):
            context = {}
            live_parent["context"] = context
        metadata = context.setdefault("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
            context["metadata"] = metadata
        metadata["delegation_pending_retries"] = dict(pending_retry_state)
        if callable(persist_live_run_state_fn):
            _persist_live_run_state_with_version(
                parent_run_id,
                live_parent,
                persist_live_run_state_fn=persist_live_run_state_fn,
            )

    def _schedule_retry_timer(
        *,
        failed_child: Dict[str, Any],
        pending: tuple[str, str],
        delay_seconds: float,
        attempt: int,
    ) -> None:
        with auto_retry_pending_lock:
            auto_retry_pending.add(pending)
            auto_retry_attempts[pending] = max(safe_int(auto_retry_attempts.get(pending), 0), safe_int(attempt, 0))

        def _retry_job(
            parent_id: str = parent_run_id,
            failed_child: Dict[str, Any] = dict(failed_child),
            pending: tuple[str, str] = pending,
        ) -> None:
            try:
                current_parent_snapshot = lookup_run_snapshot_fn(parent_id)
                _, current_children = find_run_relationships_fn(parent_id, current_parent_snapshot)
                latest_for_lineage = None
                for current_child in current_children:
                    if child_lineage_key(current_child) != pending[1]:
                        continue
                    if latest_for_lineage is None:
                        latest_for_lineage = current_child
                        continue
                    current_sort_key = (
                        parse_utc_ts(current_child.get("updated_at")) or parse_utc_ts(current_child.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
                        str(current_child.get("run_id") or ""),
                    )
                    latest_sort_key = (
                        parse_utc_ts(latest_for_lineage.get("updated_at")) or parse_utc_ts(latest_for_lineage.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
                        str(latest_for_lineage.get("run_id") or ""),
                    )
                    if current_sort_key > latest_sort_key:
                        latest_for_lineage = current_child
                if not isinstance(latest_for_lineage, dict):
                    return
                if str(latest_for_lineage.get("run_id") or "").strip() != str(failed_child.get("run_id") or "").strip():
                    return
                if not failure_status(latest_for_lineage.get("status")):
                    return
                retry_payload = build_retry_child_payload_fn(
                    current_parent_snapshot,
                    latest_for_lineage,
                    note="Automatic retry after delegated child failure.",
                )
                retry_metadata = retry_payload.get("metadata") if isinstance(retry_payload.get("metadata"), dict) else {}
                if isinstance(retry_metadata, dict):
                    retry_metadata["auto_retry"] = True
                    retry_payload["metadata"] = retry_metadata
                delegated_req = build_delegated_child_run_request_fn(
                    current_parent_snapshot,
                    retry_payload,
                    note="Automatic retry after delegated child failure.",
                )
                execute_delegated_run_request_fn(delegated_req)
            except Exception as exc:
                current_parent = runs_by_id.get(parent_id)
                current_log_queue = current_parent.get("logs") if isinstance(current_parent, dict) else None
                if current_log_queue is not None:
                    emit_log(
                        current_log_queue,
                        "error",
                        f"Automatic child retry failed: {str(exc)[:280]}",
                        event="delegation_retry_failed",
                        data={"parent_run_id": parent_id, "child_run_id": str(failed_child.get('run_id') or '').strip()},
                    )
            finally:
                with auto_retry_pending_lock:
                    auto_retry_pending.discard(pending)
                pending_retry_state.pop(pending[1], None)
                _persist_parent_retry_state()
                refresh_parent_delegation_state_fn(
                    parent_id,
                    triggering_run_id=str(failed_child.get("run_id") or "").strip() or None,
                )

        timer = timer_factory(max(0.0, float(delay_seconds or 0.0)), _retry_job)
        timer.daemon = True
        timer.start()

    def _scheduler_retry_decision(*, retry_count: int) -> Optional[Dict[str, Any]]:
        try:
            decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
                "scheduler-decision",
                {
                    "operation": "retry",
                    "enabled": True,
                    "retry_count": max(0, int(retry_count or 0)),
                    "max_retries": max(1, int(auto_retry_max_retries or 1)),
                    "current_hour": datetime.now(timezone.utc).hour,
                    "quiet_hours_override": True,
                    "wake_mode": "owner_approved",
                },
            )
        except rust_runtime_kernel_client.RustKernelDecisionError:
            return None
        if not isinstance(decision, dict):
            return None
        next_action = str(decision.get("next_action") or "").strip()
        if next_action != "schedule_retry":
            return None
        return decision

    stale_lineages = {
        lineage_key
        for lineage_key in list(pending_retry_state.keys())
        if lineage_key not in latest_by_lineage or not failure_status((latest_by_lineage.get(lineage_key) or {}).get("status"))
    }
    for lineage_key in stale_lineages:
        pending_retry_state.pop(lineage_key, None)
    if stale_lineages:
        _persist_parent_retry_state()

    for lineage_key, child in latest_by_lineage.items():
        status = str(child.get("status") or "").strip().lower()
        if not failure_status(status) or not lineage_key:
            continue
        pending_key = (parent_run_id, lineage_key)
        pending_info = pending_retry_state.get(lineage_key) if isinstance(pending_retry_state.get(lineage_key), dict) else None
        if pending_info is not None:
            pending_failed_child_run_id = str(pending_info.get("failed_child_run_id") or "").strip()
            if pending_failed_child_run_id and pending_failed_child_run_id != str(child.get("run_id") or "").strip():
                pending_retry_state.pop(lineage_key, None)
                _persist_parent_retry_state()
                pending_info = None
        if pending_info is not None:
            attempt = max(1, safe_int(pending_info.get("attempt"), 1))
            if attempt > auto_retry_max_retries:
                pending_retry_state.pop(lineage_key, None)
                _persist_parent_retry_state()
                continue
            with auto_retry_pending_lock:
                if pending_key in auto_retry_pending:
                    scheduled.add(lineage_key)
                    continue
            next_retry_at = parse_utc_ts(pending_info.get("next_retry_at"))
            now = datetime.now(timezone.utc)
            delay_seconds = max(0.0, (next_retry_at - now).total_seconds()) if next_retry_at is not None else 0.0
            _schedule_retry_timer(
                failed_child=child,
                pending=pending_key,
                delay_seconds=delay_seconds,
                attempt=attempt,
            )
            scheduled.add(lineage_key)
            continue
        with auto_retry_pending_lock:
            retry_count = max(
                child_retry_count(child),
                safe_int(auto_retry_attempts.get(pending_key), 0),
            )
        scheduler_decision = _scheduler_retry_decision(retry_count=retry_count)
        if scheduler_decision is None:
            continue
        child_run_id = str(child.get("run_id") or "").strip()
        scheduled.add(lineage_key)
        attempt = retry_count + 1
        next_retry_at = None
        scheduler_delay_seconds = float(scheduler_decision.get("delay_seconds") or auto_retry_delay_seconds or 0.0)
        if scheduler_delay_seconds > 0:
            next_retry_at = (datetime.now(timezone.utc) + timedelta(seconds=scheduler_delay_seconds)).isoformat().replace("+00:00", "Z")
        pending_retry_state[lineage_key] = {
            "failed_child_run_id": child_run_id,
            "attempt": attempt,
            "next_retry_at": next_retry_at,
        }
        _persist_parent_retry_state()
        if log_queue is not None:
            emit_log(
                log_queue,
                "info",
                f"Child run {child_run_id} failed, retrying (attempt {retry_count + 2}/{auto_retry_max_retries + 1})...",
                event="delegation_retry",
                data={
                    "parent_run_id": parent_run_id,
                    "triggering_run_id": triggering_run_id,
                    "child_run_id": child_run_id,
                    "retry_count": retry_count + 1,
                },
            )
        _schedule_retry_timer(
            failed_child=child,
            pending=pending_key,
            delay_seconds=scheduler_delay_seconds,
            attempt=attempt,
        )

    return scheduled


def build_run_relation_summary(
    snapshot: Dict[str, Any],
    *,
    agent_workspace_labels: Dict[str, str],
) -> Dict[str, Any]:
    agent_role = str(snapshot.get("agent_role") or "").strip()
    return {
        "run_id": snapshot.get("run_id"),
        "status": snapshot.get("status"),
        "agent_role": snapshot.get("agent_role"),
        "agent_role_source": snapshot.get("agent_role_source"),
        "agent_label": (
            agent_workspace_labels.get(agent_role.lower(), agent_role) if agent_role else None
        ),
        "user_goal": snapshot.get("user_goal"),
        "result_summary": snapshot.get("result_summary"),
        "created_at": snapshot.get("created_at"),
        "updated_at": snapshot.get("updated_at"),
        "completed_at": snapshot.get("completed_at"),
        "parent_run_id": snapshot.get("parent_run_id"),
        "delegation_root_run_id": snapshot.get("delegation_root_run_id"),
        "delegated_by_run_id": snapshot.get("delegated_by_run_id"),
        "delegated_by_role": snapshot.get("delegated_by_role"),
        "delegation_note": snapshot.get("delegation_note"),
        "retry_of_run_id": snapshot.get("retry_of_run_id"),
        "retry_root_run_id": snapshot.get("retry_root_run_id"),
        "retry_sequence": snapshot.get("retry_sequence"),
        "retry_count": snapshot.get("retry_count"),
    }


def iter_known_run_snapshots(
    *,
    runs_by_id: Dict[str, Any],
    run_history: List[Dict[str, Any]],
    serialize_run_snapshot_fn: Callable[[str, Dict[str, Any]], Dict[str, Any]],
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for run_id, run in list(runs_by_id.items()):
        if not isinstance(run, dict):
            continue
        snapshot = serialize_run_snapshot_fn(run_id, run)
        run_id_value = str(snapshot.get("run_id") or "").strip()
        if not run_id_value or run_id_value in seen:
            continue
        seen.add(run_id_value)
        items.append(snapshot)
    for item in run_history:
        if not isinstance(item, dict):
            continue
        run_id_value = str(item.get("run_id") or "").strip()
        if not run_id_value or run_id_value in seen:
            continue
        seen.add(run_id_value)
        items.append(item)
    return items


def find_run_relationships(
    target_run_id: str,
    snapshot: Dict[str, Any],
    *,
    extract_run_relationships_from_context_fn: Callable[[Dict[str, Any]], Dict[str, Optional[str]]],
    iter_known_run_snapshots_fn: Callable[[], List[Dict[str, Any]]],
    normalize_run_id_token: Callable[[Any], Optional[str]],
    build_run_relation_summary_fn: Callable[[Dict[str, Any]], Dict[str, Any]],
    parse_utc_ts: Callable[[Any], Any],
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    from datetime import datetime, timezone

    context = snapshot.get("context") if isinstance(snapshot.get("context"), dict) else {}
    relationships = extract_run_relationships_from_context_fn(context) if isinstance(context, dict) else {}
    parent_run_id = relationships.get("parent_run_id")
    parent_summary: Optional[Dict[str, Any]] = None
    child_summaries: List[Dict[str, Any]] = []
    for candidate in iter_known_run_snapshots_fn():
        candidate_run_id = str(candidate.get("run_id") or "").strip()
        if not candidate_run_id or candidate_run_id == target_run_id:
            continue
        candidate_parent_run_id = normalize_run_id_token(candidate.get("parent_run_id"))
        if candidate_parent_run_id == target_run_id:
            child_summaries.append(build_run_relation_summary_fn(candidate))
        if parent_run_id and candidate_run_id == parent_run_id:
            parent_summary = build_run_relation_summary_fn(candidate)
    child_summaries.sort(
        key=lambda item: (
            parse_utc_ts(item.get("updated_at"))
            or parse_utc_ts(item.get("created_at"))
            or datetime.min.replace(tzinfo=timezone.utc),
            str(item.get("run_id") or ""),
        ),
        reverse=True,
    )
    return parent_summary, child_summaries


def llm_auto_delegate_role(
    *,
    objective: str,
    business_plan: Optional[str],
    parent_snapshot: Dict[str, Any],
    routing_role_rules: Dict[str, Dict[str, Any]],
    normalize_agent_role: Callable[[Any], str],
    llm_task_fn: Callable[..., Any],
    routing_context: Optional[Dict[str, str]],
) -> Optional[Dict[str, str]]:
    if not isinstance(routing_context, dict):
        return None

    agent_list_lines = [
        f"- {role}: {str(rule.get('reason') or '').strip() or str(rule.get('goal') or '').strip()}"
        for role, rule in routing_role_rules.items()
    ]
    prompt_parts = [
        "You are routing a task to the most appropriate specialist agent.",
        "",
        "Available agents:",
        *agent_list_lines,
        "",
        "Task to route:",
        objective.strip() or "Coordinate the delegated work needed for this run.",
    ]
    if business_plan:
        prompt_parts.extend(["", f"Business context: {business_plan.strip()}"])
    result = llm_task_fn(
        "\n".join(prompt_parts).strip() + '\n\nRespond with JSON only: {"agent":"<agent_name>","reason":"<one sentence why>"}',
        schema={
            "type": "object",
            "properties": {
                "agent": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["agent", "reason"],
        },
        context={
            "provider": routing_context.get("provider"),
            "model": routing_context.get("model"),
        },
        metadata={
            "provider": routing_context.get("provider"),
            "model": routing_context.get("model"),
            "source": "delegation_routing",
            "disable_provider_fallback": True,
            "tools": [],
            "workspace_id": parent_snapshot.get("workspace_id"),
        },
    )
    if not isinstance(result, dict):
        return None
    agent_role = normalize_agent_role(result.get("agent"))
    reason = str(result.get("reason") or "").strip()
    if not agent_role or not reason:
        return None
    return {"agent_role": agent_role, "reason": reason}


def emit_auto_delegation_routing_log(
    parent_run_id: str,
    plan: List[Dict[str, Any]],
    *,
    runs_by_id: Dict[str, Any],
    emit_log_fn: Callable[..., Any],
    strategy: str,
    reason: str = "",
) -> None:
    run = runs_by_id.get(parent_run_id)
    if not isinstance(run, dict):
        return
    log_queue = run.get("logs")
    if log_queue is None:
        return
    emit_log_fn(
        log_queue,
        "info",
        "Delegation routing selected specialist agents.",
        event="delegation_routing",
        data={
            "parent_run_id": parent_run_id,
            "strategy": strategy,
            "reason": str(reason or "").strip() or None,
            "plan": plan,
        },
    )


def build_auto_delegation_plan(
    parent_snapshot: Dict[str, Any],
    *,
    max_children: int = 3,
    routing_role_rules: Dict[str, Dict[str, Any]],
    normalize_agent_role: Callable[[Any], str],
    llm_auto_delegate_role_fn: Callable[..., Optional[Dict[str, str]]],
) -> List[Dict[str, Any]]:
    parent_context = parent_snapshot.get("context") if isinstance(parent_snapshot.get("context"), dict) else {}
    parent_metadata = parent_context.get("metadata") if isinstance(parent_context.get("metadata"), dict) else {}
    objective = str(
        parent_snapshot.get("user_goal")
        or parent_context.get("user_goal")
        or parent_context.get("business_plan")
        or parent_snapshot.get("result_summary")
        or ""
    ).strip()
    business_plan = str(parent_context.get("business_plan") or "").strip() or None
    combined = " ".join(
        part for part in [
            objective,
            business_plan or "",
            str(parent_snapshot.get("result_summary") or "").strip(),
            str((parent_metadata or {}).get("skill_scope") or "").strip(),
        ] if part
    ).lower()

    if not objective:
        objective = "Coordinate the delegated work needed for this run."

    llm_route = None
    try:
        llm_route = llm_auto_delegate_role_fn(
            objective=objective,
            business_plan=business_plan,
            parent_snapshot=parent_snapshot,
        )
    except Exception:
        llm_route = None

    scored: List[Tuple[int, str, Dict[str, Any]]] = []
    for role, rule in routing_role_rules.items():
        keywords = [str(item).lower() for item in (rule.get("keywords") or []) if str(item).strip()]
        score = sum(1 for keyword in keywords if keyword in combined)
        if role == "research" and any(term in combined for term in ("plan", "strategy", "launch", "marketing")):
            score += 1
        if score <= 0:
            continue
        scored.append((score, role, rule))

    scored.sort(key=lambda item: (-item[0], item[1]))
    chosen: List[Tuple[str, Dict[str, Any]]] = []
    seen_roles: Set[str] = set()
    if isinstance(llm_route, dict):
        llm_role = normalize_agent_role(llm_route.get("agent_role"))
        llm_rule = routing_role_rules.get(llm_role or "")
        if llm_role and isinstance(llm_rule, dict):
            chosen.append((llm_role, llm_rule))
            seen_roles.add(llm_role)
    for _, role, rule in scored:
        if role in seen_roles:
            continue
        chosen.append((role, rule))
        seen_roles.add(role)
        if len(chosen) >= max_children:
            break

    if not chosen:
        chosen.append(("research", routing_role_rules["research"]))
        if len(chosen) < max_children and any(term in combined for term in ("build", "implement", "fix", "platform", "app", "automation")):
            chosen.append(("builder", routing_role_rules["builder"]))

    plan: List[Dict[str, Any]] = []
    for role, rule in chosen[:max_children]:
        route_reason = str(rule.get("reason") or "").strip() or None
        route_source = "keyword"
        if isinstance(llm_route, dict) and role == str(llm_route.get("agent_role") or "").strip():
            route_reason = str(llm_route.get("reason") or "").strip() or route_reason
            route_source = "llm"
        plan.append(
            {
                "agent_role": role,
                "user_goal": str(rule.get("goal") or "{objective}").format(objective=objective),
                "business_plan": business_plan,
                "metadata": {
                    "auto_delegation_rule": role,
                    "auto_delegation_reason": route_reason,
                    "auto_delegation_source": route_source,
                },
            }
        )
    return plan


def build_delegation_summary(
    snapshot: Dict[str, Any],
    child_runs: List[Dict[str, Any]],
    *,
    normalize_run_id_token: Callable[[Any], Optional[str]],
    normalize_agent_role: Callable[[Any], str],
    parse_utc_ts: Callable[[Any], Any],
    terminal_run_statuses: Any,
    agent_workspace_labels: Dict[str, str],
    child_lineage_key: Callable[[Dict[str, Any]], str],
    failure_status: Callable[[Any], bool],
    parent_pending_retry_lineages: Callable[[str], set[str]],
    extra_retry_pending_lineages: Optional[set[str]] = None,
) -> Optional[Dict[str, Any]]:
    from datetime import datetime, timezone

    if not isinstance(child_runs, list) or not child_runs:
        return None

    total_children = len(child_runs)
    latest_by_lineage: Dict[str, Dict[str, Any]] = {}
    for child in child_runs:
        lineage_key = (
            normalize_run_id_token(child.get("retry_root_run_id"))
            or normalize_run_id_token(child.get("retry_of_run_id"))
            or normalize_run_id_token(child.get("run_id"))
            or str(child.get("run_id") or "")
        )
        previous = latest_by_lineage.get(lineage_key)
        child_sort_key = (
            parse_utc_ts(child.get("updated_at")) or parse_utc_ts(child.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
            str(child.get("run_id") or ""),
        )
        previous_sort_key = (
            parse_utc_ts(previous.get("updated_at")) or parse_utc_ts(previous.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
            str(previous.get("run_id") or ""),
        ) if isinstance(previous, dict) else None
        if previous_sort_key is None or child_sort_key > previous_sort_key:
            latest_by_lineage[lineage_key] = child

    effective_child_runs = list(latest_by_lineage.values())
    effective_children = len(effective_child_runs)
    terminal_children = 0
    completed_children = 0
    failed_children = 0
    waiting_children = 0
    active_children = 0
    child_roles: List[str] = []
    child_summaries: List[str] = []
    failed_run_ids: List[str] = []

    pending_retry_lineages = parent_pending_retry_lineages(str(snapshot.get("run_id") or ""))
    if isinstance(extra_retry_pending_lineages, set):
        pending_retry_lineages |= {str(item).strip() for item in extra_retry_pending_lineages if str(item).strip()}

    for child in effective_child_runs:
        status = str(child.get("status") or "").strip().lower()
        role = normalize_agent_role(child.get("agent_role")) or str(child.get("agent_role") or "").strip().lower()
        lineage_key = child_lineage_key(child)
        retry_pending = bool(lineage_key and lineage_key in pending_retry_lineages and failure_status(status))
        if role and role not in child_roles:
            child_roles.append(role)
        if retry_pending:
            active_children += 1
        elif status in terminal_run_statuses:
            terminal_children += 1
        else:
            active_children += 1
        if status == "completed":
            completed_children += 1
        elif retry_pending:
            child_summaries.append(f"{agent_workspace_labels.get(role, role or 'Agent')}: retrying")
            continue
        elif failure_status(status):
            failed_children += 1
            child_run_id = normalize_run_id_token(child.get("run_id"))
            if child_run_id:
                failed_run_ids.append(child_run_id)
        elif status in {"waiting", "waiting_for_input"}:
            waiting_children += 1

        label = agent_workspace_labels.get(role, role or "Agent")
        summary = str(child.get("result_summary") or child.get("user_goal") or "").strip()
        if status == "completed" and summary:
            child_summaries.append(f"{label}: {summary}")
        elif failure_status(status):
            child_summaries.append(f"{label}: failed")
        elif status in {"waiting", "waiting_for_input"}:
            child_summaries.append(f"{label}: waiting")
        elif status and status not in terminal_run_statuses:
            child_summaries.append(f"{label}: {status}")

    if active_children > 0:
        overall_status = "active"
        next_action = "waiting_for_children"
    elif waiting_children > 0:
        overall_status = "waiting"
        next_action = "resolve_child_approvals"
    elif failed_children > 0:
        overall_status = "attention"
        next_action = "retry_failed_children"
    else:
        overall_status = "completed"
        next_action = "merge_results"

    summary_text = "; ".join(child_summaries[:4]).strip() or None
    return {
        "ready": active_children == 0 and waiting_children == 0,
        "overall_status": overall_status,
        "next_action": next_action,
        "total_children": total_children,
        "effective_children": effective_children,
        "terminal_children": terminal_children,
        "completed_children": completed_children,
        "failed_children": failed_children,
        "waiting_children": waiting_children,
        "active_children": active_children,
        "failed_run_ids": failed_run_ids,
        "retryable_failed_children": len(failed_run_ids),
        "ready_for_merge": active_children == 0 and waiting_children == 0 and failed_children == 0 and completed_children > 0,
        "child_roles": child_roles,
        "summary_text": summary_text,
        "parent_run_id": snapshot.get("run_id"),
    }


def refresh_parent_delegation_state(
    parent_run_id: str,
    *,
    lookup_run_snapshot_fn: Callable[[str], Dict[str, Any]],
    find_run_relationships_fn: Callable[[str, Dict[str, Any]], Any],
    timeout_stale_child_runs_fn: Callable[[str, List[Dict[str, Any]]], List[str]],
    schedule_auto_retry_for_failed_children_fn: Callable[..., set[str]],
    build_delegation_summary_fn: Callable[..., Optional[Dict[str, Any]]],
    runs_by_id: Dict[str, Any],
    refresh_archived_run_snapshot_fn: Callable[[str, Dict[str, Any]], Any],
    upsert_run_history_snapshot_fn: Callable[[Dict[str, Any]], Any],
    emit_log_fn: Callable[..., Any],
    utc_now_iso_fn: Callable[[], str],
    triggering_run_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    try:
        snapshot = lookup_run_snapshot_fn(parent_run_id)
    except HTTPException:
        return None

    parent_run, child_runs = find_run_relationships_fn(parent_run_id, snapshot)
    stale_timeouts = timeout_stale_child_runs_fn(parent_run_id, child_runs)
    if stale_timeouts:
        try:
            snapshot = lookup_run_snapshot_fn(parent_run_id)
            parent_run, child_runs = find_run_relationships_fn(parent_run_id, snapshot)
        except HTTPException:
            return None
    scheduled_retries = schedule_auto_retry_for_failed_children_fn(
        snapshot,
        child_runs,
        triggering_run_id=triggering_run_id or (stale_timeouts[0] if stale_timeouts else None),
    )
    if stale_timeouts or scheduled_retries:
        try:
            snapshot = lookup_run_snapshot_fn(parent_run_id)
            parent_run, child_runs = find_run_relationships_fn(parent_run_id, snapshot)
        except HTTPException:
            return None
    delegation_summary = build_delegation_summary_fn(
        snapshot,
        child_runs,
        extra_retry_pending_lineages=scheduled_retries,
    )
    if delegation_summary is None:
        return None

    refreshed_at = utc_now_iso_fn()
    orchestration_payload = {
        "summary": delegation_summary,
        "parent_run": parent_run,
        "child_runs": child_runs,
        "triggering_run_id": triggering_run_id,
        "updated_at": refreshed_at,
    }

    live_parent = runs_by_id.get(parent_run_id)
    if isinstance(live_parent, dict):
        context = live_parent.setdefault("context", {})
        if not isinstance(context, dict):
            context = {}
            live_parent["context"] = context
        metadata = context.setdefault("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
            context["metadata"] = metadata
        previous_next_action = str(metadata.get("delegation_next_action") or "").strip()
        metadata["delegation_summary_cache"] = delegation_summary
        metadata["delegation_last_refreshed_at"] = refreshed_at
        metadata["delegation_next_action"] = delegation_summary.get("next_action")
        metadata["delegation_ready"] = bool(delegation_summary.get("ready"))

        result_data = live_parent.get("result_data")
        if not isinstance(result_data, dict):
            result_data = {}
        result_data["orchestration"] = orchestration_payload
        live_parent["result_data"] = result_data
        live_parent["updated_at"] = refreshed_at
        refresh_archived_run_snapshot_fn(parent_run_id, live_parent)

        if previous_next_action != str(delegation_summary.get("next_action") or "").strip():
            log_queue = live_parent.get("logs")
            if log_queue is not None:
                next_action = str(delegation_summary.get("next_action") or "").strip()
                message_map = {
                    "waiting_for_children": "Delegated child runs are still in progress.",
                    "resolve_child_approvals": "Delegated child runs are waiting for confirmation.",
                    "retry_failed_children": "Delegated child runs failed and can be retried.",
                    "merge_results": "Delegated child runs finished and results are ready to merge.",
                }
                emit_log_fn(
                    log_queue,
                    "info",
                    message_map.get(next_action, "Delegation state updated."),
                    event="delegation_state",
                    data={
                        "parent_run_id": parent_run_id,
                        "triggering_run_id": triggering_run_id,
                        "summary": delegation_summary,
                    },
                )
    else:
        context = snapshot.get("context") if isinstance(snapshot.get("context"), dict) else {}
        metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
        if isinstance(metadata, dict):
            metadata["delegation_summary_cache"] = delegation_summary
            metadata["delegation_last_refreshed_at"] = refreshed_at
            metadata["delegation_next_action"] = delegation_summary.get("next_action")
            metadata["delegation_ready"] = bool(delegation_summary.get("ready"))
        result_data = snapshot.get("result_data")
        if not isinstance(result_data, dict):
            result_data = {}
        result_data["orchestration"] = orchestration_payload
        snapshot["result_data"] = result_data
        snapshot["updated_at"] = refreshed_at
        upsert_run_history_snapshot_fn(snapshot)

    return delegation_summary


def create_run_from_prepared_request(
    req: Any,
    *,
    prepared: Dict[str, Any],
    services: PreparedRunCreationServices,
    schedule_id: Optional[str] = None,
) -> Dict[str, Any]:
    engine = prepared["engine"]
    metadata, route = resolve_run_execution_boundary(
        dict(prepared["metadata"]),
        decide_execution_target_fn=services.decide_execution_target,
        apply_execution_route_metadata_fn=services.apply_execution_route_metadata,
        schedule_id=schedule_id,
    )
    workflow_snapshot = prepared.get("workflow_snapshot") if isinstance(prepared.get("workflow_snapshot"), dict) else None
    doctor_preflight = services.build_doctor_run_gate(
        execution_target=route["selected"],
        metadata=metadata,
        provider=req.provider,
        credential_id=req.credential_id,
    )
    if bool(doctor_preflight.get("blocking")):
        raise HTTPException(
            status_code=409,
            detail=str(
                doctor_preflight.get("detail")
                or doctor_preflight.get("title")
                or "Run blocked by doctor policy."
            ),
        )
    metadata["doctor_preflight"] = doctor_preflight

    if schedule_id:
        metadata["schedule_id"] = schedule_id
        metadata["scheduled"] = True
    preview_context = build_run_preview_context(
        req,
        metadata=metadata,
        workflow_snapshot=workflow_snapshot,
    )
    owner_user_id = services.agent_machine_inherited_owner_user_id(str(metadata.get("owner_user_id") or "").strip())
    if owner_user_id:
        metadata["owner_user_id"] = owner_user_id
    metadata["tool_policy_precheck"] = services.compute_tool_policy_precheck(preview_context)
    services.apply_browser_execution_metadata(metadata)
    if metadata["tool_policy_precheck"].get("blocked_count"):
        raise HTTPException(
            status_code=409,
            detail=services.local_execution_block_prompt(metadata["tool_policy_precheck"]),
        )
    runtime_policy = services.resolve_runtime_policy_mode(
        metadata,
        selected_target=metadata.get("execution_target_selected") or metadata.get("execution_target"),
    )
    metadata["policy_mode"] = runtime_policy.get("policy_mode")
    try:
        local_confirmation_decision = resolve_run_execution_boundary.__globals__["rust_runtime_kernel_client"].run_runtime_kernel_enforced(
            "run-routing-decision",
            {
                "operation": "local_confirmation",
                "workspace_id": _run_service_text(metadata.get("workspace_id")) or "default",
                "run_id": _run_service_text(metadata.get("run_id")),
                "schedule_id": _run_service_text(schedule_id),
                "parent_run_id": _run_service_text(metadata.get("parent_run_id")),
                "execution_target_selected": _run_service_text(
                    metadata.get("execution_target_selected") or metadata.get("execution_target")
                )
                or "cloud",
                "execution_target": _run_service_text(
                    metadata.get("execution_target_selected") or metadata.get("execution_target")
                )
                or "cloud",
                "runtime_mode": _run_service_text(metadata.get("runtime_mode")),
                "outcome_pack": _run_service_text(metadata.get("outcome_pack")),
                "blocked_count": int(metadata["tool_policy_precheck"].get("blocked_count") or 0),
                "require_confirmation_count": int(metadata["tool_policy_precheck"].get("require_confirmation_count") or 0),
                "approval_required_count": int(metadata["tool_policy_precheck"].get("approval_required_count") or 0),
            },
            allow_approval_required=True,
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        detail = _run_service_text(exc.reason) or "rust_run_routing_denied"
        raise HTTPException(
            status_code=409,
            detail=f"Rust run-routing gate blocked local_confirmation: {detail}",
        ) from exc
    local_confirmation_next_action = _run_service_text(local_confirmation_decision.get("next_action"))
    if local_confirmation_next_action not in {
        "continue_without_confirmation",
        "continue_local_execution",
        "request_local_execution_confirmation",
    }:
        raise HTTPException(
            status_code=409,
            detail=(
                "Rust run-routing gate blocked local_confirmation: "
                f"unexpected_next_action:{local_confirmation_next_action or 'missing'}"
            ),
        )
    needs_local_confirmation = local_confirmation_next_action == "request_local_execution_confirmation"
    if needs_local_confirmation:
        metadata["local_execution_waiting_confirmation"] = True
        metadata["local_execution_waiting_approval"] = True
        preview_context["metadata"] = metadata
    rust_decision = _enforce_rust_run_service_decision(
        operation="create",
        req=req,
        metadata=metadata,
        route=route,
        schedule_id=schedule_id,
        local_confirmation_required=needs_local_confirmation,
    )
    rust_requires_approval = bool(rust_decision.get("approval_required")) or _run_service_text(
        rust_decision.get("decision")
    ) in {"require_approval", "requires_approval"}
    rust_next_action = _run_service_text(rust_decision.get("next_action"))
    expected_next_action = "request_run_service_approval" if rust_requires_approval else "create_run_record"
    if rust_next_action != expected_next_action:
        raise HTTPException(
            status_code=409,
            detail=(
                "Rust run-service gate blocked create: "
                f"unexpected_next_action:{rust_next_action or 'missing'}"
            ),
        )
    if rust_requires_approval:
        metadata["run_service_waiting_approval"] = True
        preview_context["metadata"] = metadata
    run_id = services.create_run(
        engine=engine,
        context=preview_context,
        defer_local_enqueue=rust_requires_approval,
    )
    created_run = services.load_created_run(run_id) if callable(services.load_created_run) else {}
    if not isinstance(created_run, dict):
        created_run = {}
    status = "starting"
    if rust_requires_approval:
        approval_labels = []
        approval_prompt = "Run requires confirmation before execution."
        approval_source = "run_service_approval"
        pending_metadata = {
            "target": metadata.get("execution_target_selected"),
            "policy_mode": metadata.get("policy_mode"),
            "outcome_pack": metadata.get("outcome_pack"),
            "rust_reason": _run_service_text(rust_decision.get("reason")) or None,
            "rust_next_action": _run_service_text(rust_decision.get("next_action")) or None,
        }
        if needs_local_confirmation:
            approval_labels = services.precheck_human_action_labels(
                metadata["tool_policy_precheck"],
                decision="require_confirmation",
            )
            approval_prompt = services.local_execution_confirmation_prompt(
                metadata["tool_policy_precheck"]
            )
            approval_source = "local_execution_start"
            pending_metadata.update(
                {
                    "approval_actions": list(
                        metadata["tool_policy_precheck"].get("require_confirmation") or []
                    ),
                    "approval_labels": approval_labels,
                    "approval_capabilities": list(
                        metadata["tool_policy_precheck"].get("capability_ids") or []
                    ),
                }
            )
        approval_labels = services.precheck_human_action_labels(
            metadata["tool_policy_precheck"],
            decision="require_confirmation",
        )
        pending = services.begin_run_pending_confirmation(
            run_id,
            approval_prompt,
            source=approval_source,
            metadata=pending_metadata,
        )
        status = "waiting_for_input"
    else:
        pending = None
    return {
        "run_id": run_id,
        "engine": engine,
        "status": status,
        "metadata": metadata,
        "route": route,
        "doctor_preflight": doctor_preflight,
        "pending_confirmation": pending,
        "created_run": created_run,
    }


async def _ensure_durable_turn_thread_row(
    *,
    turn_request: AgentTurnRequest,
    thread_id: str,
    current_user: Any,
) -> Optional[str]:
    """Guarantee `thread_id` has an agent_threads row, or report that it does not.

    Returns the thread id when the row is known to exist, and ``None`` when it
    could not be created — because ``agent_traces.thread_id`` is a foreign key,
    and handing ``start_trace`` an id with no row costs the ENTIRE trace (the FK
    violation is caught by start_trace's own except and becomes a silent
    ``None``), while handing it ``NULL`` costs only the thread LINK. Losing the
    link is a smaller, honest degradation than losing the record.

    Failure is swallowed rather than raised on purpose: a durable run must never
    die because its observability row could not be prepared. It is logged, so
    "the trace is unlinked" is never a fact only the database knows.
    """
    from server_modules import thread_service

    try:
        await thread_service.ensure_master_thread(
            thread_id=thread_id,
            tenant_id=str(turn_request.tenant_id or "").strip() or "default",
            workspace_id=str(turn_request.workspace_id or "").strip() or "default",
            owner_user_id=(
                str(
                    (current_user.get("user_id") if isinstance(current_user, dict) else None)
                    or getattr(current_user, "user_id", "")
                    or ""
                ).strip()
                or str(getattr(getattr(turn_request, "actor", None), "id", "") or "").strip()
                or None
            ),
            channel=str(getattr(turn_request, "channel", "") or "").strip() or "runs",
            title=thread_service.build_default_thread_title(str(turn_request.message or "")),
            metadata={"source": "execute_durable_turn_request"},
        )
        return thread_id
    except Exception as exc:  # noqa: BLE001 - degradation, never a run failure
        LOGGER.warning(
            "Durable turn could not ensure its thread row; the trace will be recorded unlinked.",
            exc_info=exc,
            extra={
                "thread_id": thread_id,
                "workspace_id": str(turn_request.workspace_id or "").strip(),
            },
        )
        return None


async def _ensure_durable_turn_provider(
    turn_request: AgentTurnRequest,
    *,
    base_request: Optional[Any] = None,
) -> AgentTurnRequest:
    """Resolve the workspace's provider for a durable turn that carries none.

    A durable run REQUIRES an explicit provider — `runs_execution` raises
    `_honest_no_provider_error` rather than guessing, which is right. But the
    web chat surface never sends one: the platform resolves the workspace's
    single provider server-side (`_resolve_cloud_provider`, "ONE AI ROAD"),
    and only the direct-chat branch was doing that resolution.

    So the moment `_promote_turn_request_to_primary_engine_path` decided a
    message "looks like a task" and swapped the engine underneath it, the
    provider stopped being resolved by anyone:

        browser sends no provider   (correct — it is not the browser's to pick)
          -> promoted to durable_run on task markers
          -> context_hints.provider empty, metadata.provider empty
          -> "No AI provider is configured for this run", 3 attempts, all fail
          -> an EMPTY assistant turn, and canned copy blaming the customer's
             hardware for a routing bug

    Same family as the engine-swap billing bug in CLAUDE.md: a second engine
    behind one dispatch seam inherited the return contract and dropped what
    the old branch's callee was quietly supplying. Resolved HERE rather than
    in the sync promotion builder because this is the first async frame that
    knows the workspace, and resolution needs the control plane.

    Deliberately does not invent a fallback: if the workspace's provider
    cannot be resolved (blocked, exhausted, misconfigured) the resolver's own
    hard stop carries the real reason, which is strictly better than the
    generic "the initiator must set a provider".
    """
    if _hint_text(getattr(base_request, "provider", None)):
        return turn_request
    if _hint_text(turn_request.context_hints.get("provider")):
        return turn_request
    if _hint_text(_metadata_dict(turn_request.context_hints.get("metadata")).get("provider")):
        return turn_request

    workspace_id = str(turn_request.workspace_id or "").strip() or "default"
    from server_modules import agent_turn_runtime_service as _sage_runtime

    provider, _credentials = await _sage_runtime._resolve_cloud_provider(workspace_id)
    provider = str(provider or "").strip()
    if not provider:
        return turn_request

    context_hints = dict(turn_request.context_hints or {})
    context_hints["provider"] = provider
    metadata = dict(_metadata_dict(context_hints.get("metadata")))
    metadata.setdefault("provider", provider)
    # Recorded so a run's own row says where its provider came from — a
    # resolved provider and a caller-supplied one must stay tellable apart.
    metadata.setdefault("provider_source", "workspace_resolved")
    context_hints["metadata"] = metadata
    return replace(turn_request, context_hints=context_hints)


async def execute_durable_turn_request(
    *,
    turn_request: AgentTurnRequest,
    current_user: Any,
    services: RunExecutionServices,
    base_request: Optional[Any] = None,
    trace_context: Optional[Any] = None,
) -> Dict[str, Any]:
    # Canonical durable execution should run from AgentTurnRequest-derived state directly.
    # RunStartRequest remains a compatibility input at the edges, not the primary internal shape here.
    turn_request = await _ensure_durable_turn_provider(turn_request, base_request=base_request)
    req = build_durable_turn_execution_request(turn_request, base_request=base_request)
    created_trace_context = False
    if trace_context is None:
        # agent_traces.thread_id is a FK into agent_threads, so the row has
        # to exist before the trace is written — the same precondition
        # agent_turn.py satisfies with ensure_master_thread immediately
        # before its own start_trace. This path (POST /runs/start ->
        # turn_ingress_service.start_run_start) has no ensure of its own, so
        # it does one here.
        #
        # The old `turn_request.thread_id or turn_request.session_id`
        # fallback is gone deliberately: a session id is not a thread id and
        # never has a row in agent_threads, so that branch could only ever
        # raise ForeignKeyViolationError inside start_trace's own except and
        # lose the whole trace in silence. No thread -> NULL thread_id (the
        # column is nullable), which is an honest unlinked trace rather than
        # a dangling reference.
        _durable_thread_id = str(getattr(turn_request, "thread_id", "") or "").strip() or None
        if _durable_thread_id:
            _durable_thread_id = await _ensure_durable_turn_thread_row(
                turn_request=turn_request,
                thread_id=_durable_thread_id,
                current_user=current_user,
            )
        trace_context = await agent_trace_service.start_trace(
            workspace_id=str(turn_request.workspace_id or "").strip() or "default",
            tenant_id=str(turn_request.tenant_id or "").strip() or "default",
            thread_id=_durable_thread_id,
            run_id=None,
            surface=_trace_surface_for_channel(turn_request.channel),
            runtime_target=_trace_runtime_target_for_turn_request(turn_request),
            provider=_trace_provider_for_turn_request(turn_request, base_request=base_request),
            model=_trace_model_for_turn_request(turn_request, base_request=base_request),
            root_agent_id=_trace_root_agent_id_for_metadata(req.metadata if isinstance(req.metadata, dict) else {}),
        )
        created_trace_context = trace_context is not None
        if created_trace_context:
            await agent_trace_service.emit_trace_started(trace_context, "text")
    if trace_context is not None:
        req.metadata = dict(req.metadata or {})
        req.metadata["trace_id"] = str(trace_context.trace_id or "").strip()
        req.metadata.setdefault("request_trace_id", str(trace_context.trace_id or "").strip())
    req = services.stamp_request_owner(req, current_user)
    try:
        req = await resolve_turn_runtime_attachment_selection(req, turn_request=turn_request)
        prepared = services.prepare_run_start_request(req)
        metadata, route = resolve_run_execution_boundary(
            dict(prepared["metadata"]),
            decide_execution_target_fn=decide_execution_target,
            apply_execution_route_metadata_fn=apply_execution_route_metadata,
        )
        req.metadata = dict(metadata)
        if created_trace_context and trace_context is not None:
            await agent_trace_service.emit_trace_routed(
                trace_context,
                "durable",
                str(route.get("selected") or "").strip() or _trace_runtime_target_for_turn_request(turn_request),
                _trace_provider_for_turn_request(turn_request, base_request=base_request),
                _trace_model_for_turn_request(turn_request, base_request=base_request),
                _trace_routing_reason_for_turn_request(turn_request),
            )
        doctor_preflight = await build_doctor_run_gate_live(
            execution_target=route["selected"],
            metadata=metadata,
            provider=req.provider,
            credential_id=req.credential_id,
        )
        if bool(doctor_preflight.get("blocking")):
            raise HTTPException(
                status_code=409,
                detail=str(
                    doctor_preflight.get("detail")
                    or doctor_preflight.get("title")
                    or "Run blocked by doctor policy."
                ),
            )
        result = services.create_run_from_request(req)
        if isinstance(result, dict):
            result["doctor_preflight"] = doctor_preflight
        return {
            "kind": "durable_run",
            "result": result,
            "turn_request": turn_request,
        }
    except Exception as exc:
        if created_trace_context and trace_context is not None:
            await agent_trace_service.emit_trace_failed(
                trace_context,
                "durable_run_start_failed",
                str(exc)[:400],
                isinstance(exc, HTTPException) and int(getattr(exc, "status_code", 0) or 0) >= 500,
                None,
            )
            # The run never started, so nothing was even partly done.
            await agent_trace_service.finish_trace(
                trace_context,
                outcome=agent_trace_service.TRACE_OUTCOME_FAILED,
                final_message_id=None,
            )
        raise
