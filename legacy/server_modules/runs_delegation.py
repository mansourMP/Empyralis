import threading
from datetime import datetime, timezone

from server_modules import runtime_config as config
from server_modules import run_service as run_service
from server_modules import shared as shared
from server_modules import runtime_common as common
from server_modules.doctor_gate import build_doctor_run_gate_from_snapshot
from server_modules.runs_engine import ENGINE_REGISTRY, ORION_ENGINE_VALIDATION_ERRORS
from server_modules.runs_output import (
    _get_archived_run_history_item,
    _get_replay_payload,
    _persist_live_run_state,
    _prefer_archived_snapshot,
    _refresh_archived_run_snapshot,
    _serialize_run_snapshot,
    _upsert_run_history_snapshot,
)
from server_modules.turn_runtime import (
    build_execute_unowned_system_run_start_request_via_turn_runtime,
)

globals().update({key: value for key, value in vars(config).items() if not key.startswith("__")})
globals().update({key: value for key, value in vars(shared).items() if not key.startswith("__")})
globals().update({key: value for key, value in vars(common).items() if not key.startswith("__")})

AUTO_RETRY_DELAY_SECONDS = 3.0
AUTO_RETRY_MAX_RETRIES = 1
STALE_CHILD_RUN_TIMEOUT_SECONDS = 300
ROUTING_PROVIDER_ORDER = run_service.ROUTING_PROVIDER_ORDER
_AUTO_RETRY_PENDING: Set[Tuple[str, str]] = set()
_AUTO_RETRY_ATTEMPTS: Dict[Tuple[str, str], int] = {}
_AUTO_RETRY_PENDING_LOCK = threading.Lock()

VALID_AGENT_ROLES: Set[str] = {
    "orchestrator",
    "support",
    "sales",
    "research",
    "finance",
    "builder",
    "private-assistant",
}

AGENT_ROLE_ALIASES: Dict[str, str] = {
    "ceo": "orchestrator",
    "dispatcher": "orchestrator",
    "support-inbox": "support",
    "support_inbox": "support",
    "customer-support": "support",
    "customer_support": "support",
    "sales-booking": "sales",
    "sales_booking": "sales",
    "booking": "sales",
    "research-memory": "research",
    "research_memory": "research",
    "memory": "research",
    "finance-sheets": "finance",
    "finance_sheets": "finance",
    "sheets": "finance",
    "builder-ops": "builder",
    "builder_ops": "builder",
    "developer": "builder",
    "design": "builder",
    "private assistant": "private-assistant",
    "private_assistant": "private-assistant",
}

AGENT_ROLE_KEYWORDS: Dict[str, List[str]] = {
    "support": ["support", "customer", "feedback", "reply", "inbox", "message", "complaint", "telegram", "whatsapp"],
    "sales": ["sales", "lead", "booking", "book", "appointment", "follow-up", "follow up", "pipeline", "convert", "slot"],
    "research": ["research", "study", "exam", "memory", "brief", "summary", "docs", "analyze", "analysis", "plan"],
    "finance": ["finance", "sheet", "sheets", "excel", "budget", "invoice", "expense", "revenue", "report", "spreadsheet"],
    "builder": ["build", "builder", "code", "debug", "fix", "frontend", "backend", "design", "automation", "script", "workflow", "platform"],
    "private-assistant": ["personal", "private", "reminder", "study", "habit", "routine", "travel", "note", "calendar"],
}

OUTCOME_PACK_AGENT_ROLE_MAP: Dict[str, str] = {
    "customer-ops-autopilot": "sales",
    "weekly-content": "research",
    "competitor-brief": "research",
    "spreadsheet-ops": "finance",
    "spreadsheet-ops-v1": "finance",
    "document-studio-v1": "builder",
    "local-execution-v1": "builder",
}


def emit_log(log_queue, level: str, message: str, event: str = "runtime", data: Optional[dict] = None):
    payload = {
        "event_id": str(uuid.uuid4()),
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "level": level,
        "event": event,
        "message": message,
    }
    if data:
        payload["data"] = data
    run_id = RUN_QUEUE_INDEX.get(id(log_queue))
    if run_id:
        run = runs.get(run_id)
        if isinstance(run, dict):
            seq = int(run.get("_event_seq", 0)) + 1
            run["_event_seq"] = seq
            payload["seq"] = seq
            payload["run_id"] = run_id
            events = run.setdefault("events", [])
            if isinstance(events, list):
                events.append(payload)
                if len(events) > ORION_MAX_EVENT_BUFFER:
                    del events[: len(events) - ORION_MAX_EVENT_BUFFER]
    log_queue.put(payload)


def normalize_agent_role(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    raw = AGENT_ROLE_ALIASES.get(raw, raw)
    return raw if raw in VALID_AGENT_ROLES else ""


_safe_int = run_service.safe_int
_normalize_requested_max_iterations = run_service.normalize_requested_max_iterations


execute_system_run_start_request_via_turn_runtime = build_execute_unowned_system_run_start_request_via_turn_runtime()


def _delegation_run_execution_services() -> run_service.RunExecutionServices:
    return run_service.build_delegated_run_execution_services(
        namespace=globals(),
    )


def _execute_delegated_run_request(req: RunStartRequest) -> Dict[str, Any]:
    return run_service.execute_delegated_run_request(
        req,
        namespace=globals(),
        execute_system_run_start_request_via_turn_runtime_fn=execute_system_run_start_request_via_turn_runtime,
    )


def _failure_status(status: Any) -> bool:
    return str(status or "").strip().lower() in {"failed", "error", "timeout", "cancelled", "stopped"}


def _child_lineage_key(child: Dict[str, Any]) -> str:
    return (
        _normalize_run_id_token(child.get("retry_root_run_id"))
        or _normalize_run_id_token(child.get("retry_of_run_id"))
        or _normalize_run_id_token(child.get("run_id"))
        or str(child.get("run_id") or "")
    )


def _child_metadata(child: Dict[str, Any]) -> Dict[str, Any]:
    context = child.get("context") if isinstance(child.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    return dict(metadata) if isinstance(metadata, dict) else {}


def _child_retry_count(child: Dict[str, Any]) -> int:
    metadata = _child_metadata(child)
    return max(
        _safe_int(metadata.get("retry_count"), 0),
        _safe_int(metadata.get("retry_sequence"), 0),
        _safe_int(child.get("retry_sequence"), 0),
    )


def _parent_pending_retry_lineages(parent_run_id: str) -> Set[str]:
    pending: Set[str] = set()
    run = runs.get(str(parent_run_id or "").strip())
    if isinstance(run, dict):
        context = run.get("context") if isinstance(run.get("context"), dict) else {}
        metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
        pending_state = metadata.get("delegation_pending_retries") if isinstance(metadata.get("delegation_pending_retries"), dict) else {}
        pending |= {str(lineage).strip() for lineage in pending_state.keys() if str(lineage).strip()}
    with _AUTO_RETRY_PENDING_LOCK:
        pending |= {lineage for parent, lineage in _AUTO_RETRY_PENDING if parent == str(parent_run_id or "").strip()}
    return pending


def _auto_retry_attempt_count(parent_run_id: str, lineage_key: str, fallback: int = 0) -> int:
    with _AUTO_RETRY_PENDING_LOCK:
        return max(_safe_int(_AUTO_RETRY_ATTEMPTS.get((parent_run_id, lineage_key)), 0), _safe_int(fallback, 0))


def _detect_agent_role(req: RunStartRequest, metadata: Dict[str, Any]) -> Tuple[str, str]:
    if ORION_SINGLE_AGENT_MODE:
        return ORION_SINGLE_AGENT_ROLE, "single_agent"
    explicit = normalize_agent_role(req.agent_role or metadata.get("agent_role"))
    if explicit:
        return explicit, "explicit"

    agents = req.agents if isinstance(req.agents, list) else []
    for item in agents:
        if not isinstance(item, dict):
            continue
        candidate = normalize_agent_role(item.get("agent_role") or item.get("role") or item.get("kind") or item.get("id"))
        if candidate:
            return candidate, "agents"

    outcome_pack = str(metadata.get("outcome_pack") or "").strip().lower()
    if outcome_pack:
        mapped = OUTCOME_PACK_AGENT_ROLE_MAP.get(outcome_pack)
        if mapped:
            return mapped, "outcome_pack"

    haystack = " ".join(
        part
        for part in [
            str(req.user_goal or "").strip(),
            str(req.business_plan or "").strip(),
            outcome_pack,
            " ".join(
                sorted(str(key).strip().lower() for key in (metadata.get("pack_inputs") or {}).keys())
            ) if isinstance(metadata.get("pack_inputs"), dict) else "",
        ]
        if part
    ).lower()

    best_role = "builder"
    best_score = 0
    for role, keywords in AGENT_ROLE_KEYWORDS.items():
        score = sum(1 for keyword in keywords if keyword in haystack)
        if score > best_score:
            best_role = role
            best_score = score
    if best_score > 0:
        return best_role, "inferred"

    if any(token in haystack for token in ["plan", "summary", "analyze", "analysis", "docs"]):
        return "research", "inferred"

    return "builder", "default"


def _normalize_run_id_token(value: Any) -> Optional[str]:
    token = str(value or "").strip()
    if not token:
        return None
    try:
        return str(uuid.UUID(token))
    except Exception:
        return None


def _extract_run_relationships_from_context(context: Dict[str, Any]) -> Dict[str, Optional[str]]:
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    if not isinstance(metadata, dict):
        metadata = {}
    return {
        "parent_run_id": _normalize_run_id_token(metadata.get("parent_run_id")),
        "delegation_root_run_id": _normalize_run_id_token(metadata.get("delegation_root_run_id")),
        "delegated_by_run_id": _normalize_run_id_token(metadata.get("delegated_by_run_id")),
        "delegated_by_role": normalize_agent_role(metadata.get("delegated_by_role")) or None,
        "delegation_note": str(metadata.get("delegation_note") or "").strip() or None,
        "retry_of_run_id": _normalize_run_id_token(metadata.get("retry_of_run_id")),
        "retry_root_run_id": _normalize_run_id_token(metadata.get("retry_root_run_id")),
    }


def _run_relation_summary(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    return run_service.build_run_relation_summary(
        snapshot,
        agent_workspace_labels=AGENT_WORKSPACE_LABELS,
    )


def _iter_known_run_snapshots() -> List[Dict[str, Any]]:
    with RUN_HISTORY_LOCK:
        history_items = list(RUN_HISTORY)
    return run_service.iter_known_run_snapshots(
        runs_by_id=runs,
        run_history=history_items,
        serialize_run_snapshot_fn=_serialize_run_snapshot,
    )


def _find_run_relationships(target_run_id: str, snapshot: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    return run_service.find_run_relationships(
        target_run_id,
        snapshot,
        extract_run_relationships_from_context_fn=_extract_run_relationships_from_context,
        iter_known_run_snapshots_fn=_iter_known_run_snapshots,
        normalize_run_id_token=_normalize_run_id_token,
        build_run_relation_summary_fn=_run_relation_summary,
        parse_utc_ts=_parse_utc_ts,
    )


def _build_delegation_summary(
    snapshot: Dict[str, Any],
    child_runs: List[Dict[str, Any]],
    *,
    extra_retry_pending_lineages: Optional[Set[str]] = None,
) -> Optional[Dict[str, Any]]:
    return run_service.build_delegation_summary(
        snapshot,
        child_runs,
        normalize_run_id_token=_normalize_run_id_token,
        normalize_agent_role=normalize_agent_role,
        parse_utc_ts=_parse_utc_ts,
        terminal_run_statuses=TERMINAL_RUN_STATUSES,
        agent_workspace_labels=AGENT_WORKSPACE_LABELS,
        child_lineage_key=_child_lineage_key,
        failure_status=_failure_status,
        parent_pending_retry_lineages=_parent_pending_retry_lineages,
        extra_retry_pending_lineages=extra_retry_pending_lineages,
    )


def _prepare_run_start_request(req: RunStartRequest) -> Dict[str, Any]:
    return run_service.prepare_legacy_run_start_request(
        req,
        services=run_service.build_runs_delegation_runtime_preparation_services_from_namespace(
            namespace=globals(),
            engine_registry=ENGINE_REGISTRY,
            engine_validation_errors=ORION_ENGINE_VALIDATION_ERRORS,
            supported_outcome_packs=SUPPORTED_OUTCOME_PACKS,
            normalize_trust_mode=normalize_trust_mode,
            trust_mode_aliases=TRUST_MODE_ALIASES,
            valid_trust_modes=VALID_TRUST_MODES,
            normalize_execution_target=normalize_execution_target,
            valid_execution_targets=VALID_EXECUTION_TARGETS,
            normalize_agent_role=normalize_agent_role,
            resolve_app_permissions=resolve_app_permissions,
            action_policy_from_app_permissions=action_policy_from_app_permissions,
            merge_action_policies=merge_action_policies,
            fetch_workflow_snapshot=fetch_workflow_snapshot,
        ),
    )


_local_execution_requires_start_confirmation = lambda metadata, precheck: run_service.local_execution_requires_start_confirmation(
    metadata,
    precheck,
    local_execution_target=EXECUTION_TARGET_LOCAL_COMPANION,
    local_execution_pack_id=LOCAL_EXECUTION_PACK_ID,
)
_precheck_human_action_labels = run_service.precheck_human_action_labels
_local_execution_confirmation_prompt = run_service.local_execution_confirmation_prompt
_local_execution_block_prompt = run_service.local_execution_block_prompt
_mark_local_execution_tools_approved = run_service.mark_local_execution_tools_approved
_apply_browser_execution_metadata = run_service.apply_browser_execution_metadata


def _create_run_from_request(req: RunStartRequest, schedule_id: Optional[str] = None) -> Dict[str, Any]:
    return run_service.create_legacy_run_result_from_request(
        req,
        schedule_id=schedule_id,
        services=run_service.build_runs_delegation_runtime_request_services_from_namespace(
            namespace=globals(),
            prepare_run_start_request=_prepare_run_start_request,
            decide_execution_target=decide_execution_target,
            apply_execution_route_metadata=apply_execution_route_metadata,
            build_doctor_run_gate=build_doctor_run_gate_from_snapshot,
            agent_machine_inherited_owner_user_id=agent_machine_inherited_owner_user_id,
            resolve_runtime_policy_mode=resolve_runtime_policy_mode,
            agent_machine_full_trust_enabled=agent_machine_full_trust_enabled,
            local_execution_target=EXECUTION_TARGET_LOCAL_COMPANION,
            local_execution_pack_id=LOCAL_EXECUTION_PACK_ID,
            now_iso=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            compute_tool_policy_precheck_fallback=lambda: __import__(
                "server_modules.runs_execution",
                fromlist=["_compute_tool_policy_precheck"],
            )._compute_tool_policy_precheck,
            create_run_fallback=lambda: __import__(
                "server_modules.runs_execution",
                fromlist=["create_run"],
            ).create_run,
            begin_run_pending_confirmation_fallback=lambda: __import__(
                "server_modules.runs_core",
                fromlist=["_begin_run_pending_confirmation"],
            )._begin_run_pending_confirmation,
        ),
    )


def _lookup_run_snapshot(run_id: str) -> Dict[str, Any]:
    active = runs.get(run_id)
    archived_item = _get_archived_run_history_item(run_id)
    active_snapshot = _serialize_run_snapshot(run_id, active) if isinstance(active, dict) else None
    chosen_snapshot = _prefer_archived_snapshot(active_snapshot, archived_item)
    if isinstance(chosen_snapshot, dict):
        return chosen_snapshot
    return _get_replay_payload(run_id)


def _build_delegated_run_request(
    parent_snapshot: Dict[str, Any],
    child_payload: Dict[str, Any],
    note: Optional[str] = None,
) -> RunStartRequest:
    return run_service.build_delegated_child_run_request(
        parent_snapshot,
        child_payload,
        normalize_run_id_token=_normalize_run_id_token,
        normalize_agent_role=normalize_agent_role,
        normalize_requested_max_iterations=_normalize_requested_max_iterations,
        valid_execution_targets=VALID_EXECUTION_TARGETS,
        note=note,
    )


def _timeout_stale_child_runs(parent_run_id: str, child_runs: List[Dict[str, Any]]) -> List[str]:
    status_setter = globals().get("set_run_status")
    if not callable(status_setter):
        from server_modules.runs_core import set_run_status as status_setter  # type: ignore[assignment]
    return run_service.timeout_stale_delegated_child_runs(
        parent_run_id,
        child_runs,
        runs_by_id=runs,
        terminal_run_statuses=TERMINAL_RUN_STATUSES,
        stale_child_run_timeout_seconds=STALE_CHILD_RUN_TIMEOUT_SECONDS,
        normalize_run_id_token=_normalize_run_id_token,
        parse_utc_ts=_parse_utc_ts,
        utc_now=_utc_now,
        set_run_status=status_setter,
        emit_log=emit_log,
    )


def _schedule_auto_retry_for_failed_children(
    parent_snapshot: Dict[str, Any],
    child_runs: List[Dict[str, Any]],
    *,
    triggering_run_id: Optional[str] = None,
) -> Set[str]:
    return run_service.schedule_auto_retry_for_failed_children(
        parent_snapshot,
        child_runs,
        runs_by_id=runs,
        parse_utc_ts=_parse_utc_ts,
        child_lineage_key=_child_lineage_key,
        failure_status=_failure_status,
        child_retry_count=_child_retry_count,
        safe_int=_safe_int,
        auto_retry_pending=_AUTO_RETRY_PENDING,
        auto_retry_attempts=_AUTO_RETRY_ATTEMPTS,
        auto_retry_pending_lock=_AUTO_RETRY_PENDING_LOCK,
        auto_retry_max_retries=AUTO_RETRY_MAX_RETRIES,
        auto_retry_delay_seconds=AUTO_RETRY_DELAY_SECONDS,
        emit_log=emit_log,
        build_retry_child_payload_fn=_build_retry_child_payload,
        build_delegated_child_run_request_fn=_build_delegated_run_request,
        execute_delegated_run_request_fn=_execute_delegated_run_request,
        lookup_run_snapshot_fn=_lookup_run_snapshot,
        find_run_relationships_fn=_find_run_relationships,
        refresh_parent_delegation_state_fn=_refresh_parent_delegation_state,
        timer_factory=threading.Timer,
        persist_live_run_state_fn=_persist_live_run_state,
        triggering_run_id=triggering_run_id,
    )


def _refresh_parent_delegation_state(parent_run_id: str, *, triggering_run_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    return run_service.refresh_parent_delegation_state(
        parent_run_id,
        lookup_run_snapshot_fn=_lookup_run_snapshot,
        find_run_relationships_fn=_find_run_relationships,
        timeout_stale_child_runs_fn=_timeout_stale_child_runs,
        schedule_auto_retry_for_failed_children_fn=_schedule_auto_retry_for_failed_children,
        build_delegation_summary_fn=_build_delegation_summary,
        runs_by_id=runs,
        refresh_archived_run_snapshot_fn=_refresh_archived_run_snapshot,
        upsert_run_history_snapshot_fn=_upsert_run_history_snapshot,
        emit_log_fn=emit_log,
        utc_now_iso_fn=_utc_now_iso,
        triggering_run_id=triggering_run_id,
    )


def recover_pending_delegation_retries_on_startup() -> List[str]:
    recovered: List[str] = []
    for run_id, run in list(runs.items()):
        if not isinstance(run, dict):
            continue
        context = run.get("context") if isinstance(run.get("context"), dict) else {}
        metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
        agent_role = normalize_agent_role(run.get("agent_role") or metadata.get("agent_role"))
        pending_state = metadata.get("delegation_pending_retries") if isinstance(metadata.get("delegation_pending_retries"), dict) else {}
        if agent_role != "orchestrator" and not pending_state:
            continue
        summary = _refresh_parent_delegation_state(str(run_id or "").strip())
        if summary is not None and pending_state:
            recovered.append(str(run_id or "").strip())
    return recovered


def _build_retry_child_payload(parent_snapshot: Dict[str, Any], child_snapshot: Dict[str, Any], *, note: Optional[str] = None) -> Dict[str, Any]:
    return run_service.build_retry_child_payload(
        parent_snapshot,
        child_snapshot,
        normalize_run_id_token=_normalize_run_id_token,
        normalize_agent_role=normalize_agent_role,
        normalize_requested_max_iterations=_normalize_requested_max_iterations,
        child_retry_count=_child_retry_count,
        note=note,
    )


AUTO_DELEGATION_ROLE_RULES = run_service.AUTO_DELEGATION_ROLE_RULES


def _fastest_routing_context() -> Optional[Dict[str, str]]:
    from scripts.orion_local_worker_llm import provider_has_key, resolve_requested_model

    return run_service.resolve_fastest_routing_context(
        routing_provider_order=ROUTING_PROVIDER_ORDER,
        provider_has_key_fn=provider_has_key,
        resolve_requested_model_fn=resolve_requested_model,
    )


def _llm_auto_delegate_role(
    *,
    objective: str,
    business_plan: Optional[str],
    parent_snapshot: Dict[str, Any],
) -> Optional[Dict[str, str]]:
    from server_modules.llm_task import llm_task

    routing_context = _fastest_routing_context()
    return run_service.llm_auto_delegate_role(
        objective=objective,
        business_plan=business_plan,
        parent_snapshot=parent_snapshot,
        routing_role_rules=AUTO_DELEGATION_ROLE_RULES,
        normalize_agent_role=normalize_agent_role,
        llm_task_fn=llm_task,
        routing_context=routing_context,
    )


def _emit_auto_delegation_routing_log(
    parent_run_id: str,
    plan: List[Dict[str, Any]],
    *,
    strategy: str,
    reason: str = "",
) -> None:
    run_service.emit_auto_delegation_routing_log(
        parent_run_id,
        plan,
        runs_by_id=runs,
        emit_log_fn=emit_log,
        strategy=strategy,
        reason=reason,
    )


def _build_auto_delegation_plan(
    parent_snapshot: Dict[str, Any],
    *,
    max_children: int = 3,
) -> List[Dict[str, Any]]:
    return run_service.build_auto_delegation_plan(
        parent_snapshot,
        max_children=max_children,
        routing_role_rules=AUTO_DELEGATION_ROLE_RULES,
        normalize_agent_role=normalize_agent_role,
        llm_auto_delegate_role_fn=_llm_auto_delegate_role,
    )
