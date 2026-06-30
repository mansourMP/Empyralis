from server_modules import runtime_config as config
from server_modules import shared as shared
from server_modules import browser_checkpoint_service
from server_modules import runtime_common as common
from server_modules import run_state_repository
from server_modules import usage_accounting_service
from server_modules.run_execution_handle import durable_run_payload, restore_run_state, should_restore_execution_handle

globals().update({key: value for key, value in vars(config).items() if not key.startswith("__")})
globals().update({key: value for key, value in vars(shared).items() if not key.startswith("__")})
globals().update({key: value for key, value in vars(common).items() if not key.startswith("__")})


def _normalize_agent_role_safe(value: Any) -> str:
    normalize = globals().get("normalize_agent_role")
    if callable(normalize):
        try:
            return str(normalize(value) or "").strip().lower()
        except Exception:
            return ""
    try:
        from server_modules.runs_delegation import normalize_agent_role as normalize

        return str(normalize(value) or "").strip().lower()
    except Exception:
        return ""

def _pack_id_from_context(run: Dict[str, Any]) -> Optional[str]:
    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    pack_id = metadata.get("outcome_pack")
    if isinstance(pack_id, str) and pack_id.strip():
        return pack_id.strip().lower()
    result_data = run.get("result_data")
    if isinstance(result_data, dict):
        from_result = result_data.get("pack_id")
        if isinstance(from_result, str) and from_result.strip():
            return from_result.strip().lower()
    return None


def _execution_environment_from_metadata(metadata: Dict[str, Any]) -> Optional[str]:
    value = str(metadata.get("execution_environment") or "").strip()
    if value:
        return value
    target = str(
        metadata.get("execution_target_selected")
        or metadata.get("runtime_target")
        or metadata.get("canonical_runtime_target")
        or ""
    ).strip()
    if not target:
        return None
    try:
        from server_modules import hardware_runtime_target_resolver

        resolved = hardware_runtime_target_resolver.resolve_runtime_target(target)
        return resolved.execution_environment
    except Exception:
        return None


def _build_replay_request_from_context(context: Dict[str, Any]) -> Dict[str, Any]:
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    replay_metadata = dict(metadata)
    replay_metadata.pop("credentials", None)
    replay_metadata.pop("access_token", None)
    replay_metadata.pop("api_key", None)
    replay_metadata.pop("secret", None)
    return {
        "engine": context.get("engine") or "orion",
        "workflow_id": context.get("workflow_id"),
        "workspace_id": context.get("workspace_id"),
        "user_goal": context.get("user_goal"),
        "business_plan": context.get("business_plan"),
        "provider": context.get("provider"),
        "model": context.get("model"),
        "credential_id": context.get("credential_id"),
        "agents": context.get("agents") if isinstance(context.get("agents"), list) else [],
        "metadata": replay_metadata,
    }


def _active_profile_snapshot(run: Dict[str, Any]) -> Dict[str, Any]:
    profile_id = str(run.get("active_profile_id") or "").strip()
    profile = PROVIDER_PROFILES.get(profile_id) if profile_id else None
    profile_row = profile if isinstance(profile, dict) else {}
    provider = str(profile_row.get("provider") or run.get("active_provider") or "").strip()
    model = str(profile_row.get("model") or run.get("active_model") or "").strip()
    label = str(profile_row.get("label") or run.get("active_profile_label") or "").strip()
    return {
        "id": profile_id or None,
        "label": label or None,
        "provider": provider or None,
        "model": model or None,
        "adapter": str(run.get("active_adapter") or "").strip() or None,
    }


def _browser_introspection_snapshot(run: Dict[str, Any], result_data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return browser_checkpoint_service.browser_introspection_snapshot(run, result_data)


def _serialize_provider_model_truth(
    *,
    context: Dict[str, Any],
    metadata: Dict[str, Any],
    active_profile: Dict[str, Any],
    usage_masked: Dict[str, Any],
    events: List[Dict[str, Any]],
    result_data: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    requested_provider = str(context.get("provider") or metadata.get("provider") or "").strip().lower() or None
    requested_model = str(context.get("model") or metadata.get("model") or "").strip() or None
    effective_provider = str(
        usage_masked.get("provider")
        or active_profile.get("provider")
        or ""
    ).strip().lower() or None
    effective_model = str(
        usage_masked.get("model")
        or active_profile.get("model")
        or ""
    ).strip() or None
    provider_overridden = bool(requested_provider and effective_provider and requested_provider != effective_provider)
    model_overridden = bool(requested_model and effective_model and requested_model != effective_model)
    event_names = {
        str(item.get("event") or "").strip().lower()
        for item in events
        if isinstance(item, dict)
    }
    attempted_tokens = []
    if isinstance(result_data, dict):
        attempted_tokens = [
            str(token or "").strip().lower()
            for token in str(result_data.get("attempted_providers") or "").split(",")
            if str(token or "").strip()
        ]
    fallback_reason = None
    fallback_used = False
    if "profile_failover" in event_names or len(attempted_tokens) > 1:
        fallback_used = True
        fallback_reason = "provider_failover"
    elif "profile_model_fallback" in event_names:
        fallback_used = True
        fallback_reason = "model_fallback"
    payload = {
        "requested_provider": requested_provider,
        "effective_provider": effective_provider,
        "requested_model": requested_model,
        "effective_model": effective_model,
        "provider_overridden": provider_overridden,
        "model_overridden": model_overridden,
        "fallback_used": fallback_used,
    }
    if fallback_reason:
        payload["fallback_reason"] = fallback_reason
    return payload


def _connector_mutation_snapshot(
    *,
    result_data: Optional[Dict[str, Any]],
    connector_binding: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    connector_action = _result_connector_action(result_data)
    if not isinstance(connector_action, dict) and not isinstance(connector_binding, dict):
        return None
    target_label = None
    if isinstance(connector_action, dict):
        target_label = (
            connector_action.get("recipient")
            or connector_action.get("to_number")
            or connector_action.get("channel_id")
            or connector_action.get("chat_id")
            or connector_action.get("recipient_id")
            or connector_action.get("comment_id")
            or connector_action.get("path")
            or connector_action.get("title")
            or connector_action.get("url")
        )
    if not target_label and isinstance(connector_binding, dict):
        target_label = connector_binding.get("identity_label")
    execution_label = None
    if isinstance(connector_action, dict):
        if bool(connector_action.get("duplicate_protected")):
            execution_label = "Duplicate protected"
        elif bool(connector_action.get("executed")):
            execution_label = "Executed"
    return {
        "binding": connector_binding if isinstance(connector_binding, dict) else None,
        "action": connector_action if isinstance(connector_action, dict) else None,
        "execution_label": execution_label,
        "action_label": _humanize_evidence_token(
            connector_action.get("action_id") if isinstance(connector_action, dict) else "connector_action"
        ),
        "system_label": (
            connector_binding.get("label")
            if isinstance(connector_binding, dict)
            else _humanize_evidence_token(connector_action.get("connector"))
            if isinstance(connector_action, dict)
            else None
        ),
        "target_label": target_label,
        "result_label": _connector_action_result_hint(
            connector_action.get("result") if isinstance(connector_action, dict) else None
        ),
    }


def _run_detail_contract_snapshot(
    *,
    provider_model_truth: Dict[str, Any],
    approval_outcome: Optional[Dict[str, Any]],
    connector_binding: Optional[Dict[str, Any]],
    result_data: Optional[Dict[str, Any]],
    evidence_items: List[Dict[str, str]],
) -> Dict[str, Any]:
    contract = {
        "provider_model": {
            "requested_provider": provider_model_truth.get("requested_provider"),
            "effective_provider": provider_model_truth.get("effective_provider"),
            "requested_model": provider_model_truth.get("requested_model"),
            "effective_model": provider_model_truth.get("effective_model"),
            "provider_overridden": bool(provider_model_truth.get("provider_overridden")),
            "model_overridden": bool(provider_model_truth.get("model_overridden")),
            "fallback_used": bool(provider_model_truth.get("fallback_used")),
        },
        "approval_outcome": approval_outcome or None,
        "connector_mutation": _connector_mutation_snapshot(
            result_data=result_data,
            connector_binding=connector_binding,
        ),
        "evidence_items": evidence_items[:5],
    }
    fallback_reason = provider_model_truth.get("fallback_reason")
    if fallback_reason:
        contract["provider_model"]["fallback_reason"] = fallback_reason
    return contract


def _serialize_node_states(
    node_states: Any,
    *,
    include_previews: bool = True,
    include_detail: bool = True,
) -> Dict[str, Any]:
    if not isinstance(node_states, dict):
        return {
            "version": 1,
            "graph_kind": None,
            "active_node_id": None,
            "final_node_id": None,
            "updated_at": None,
            "counts": {},
            "items": [],
        }

    items_raw = node_states.get("items")
    order_raw = node_states.get("order") if isinstance(node_states.get("order"), list) else []
    if isinstance(items_raw, dict):
        item_map = items_raw
        order = [str(item).strip() for item in order_raw if str(item).strip()]
        for node_id in item_map.keys():
            token = str(node_id or "").strip()
            if token and token not in order:
                order.append(token)
        ordered_items = [item_map.get(node_id) for node_id in order]
    elif isinstance(items_raw, list):
        ordered_items = items_raw
    else:
        ordered_items = []

    counts: Dict[str, int] = {}
    payload_items: List[Dict[str, Any]] = []
    for raw_item in ordered_items:
        if not isinstance(raw_item, dict):
            continue
        status = str(raw_item.get("status") or "queued").strip().lower() or "queued"
        counts[status] = counts.get(status, 0) + 1
        item_payload = {
            "node_id": str(raw_item.get("node_id") or "").strip() or None,
            "label": str(raw_item.get("label") or "").strip() or None,
            "type": str(raw_item.get("type") or "").strip().lower() or None,
            "variant": str(raw_item.get("variant") or "").strip().lower() or None,
            "status": status,
            "started_at": raw_item.get("started_at"),
            "completed_at": raw_item.get("completed_at"),
            "duration_ms": raw_item.get("duration_ms"),
            "summary": _compact_event_text(raw_item.get("summary"), limit=240) if raw_item.get("summary") else None,
            "error": _compact_event_text(raw_item.get("error"), limit=320) if raw_item.get("error") else None,
            "child_run_id": str(raw_item.get("child_run_id") or "").strip() or None,
            "child_workflow_id": str(raw_item.get("child_workflow_id") or "").strip() or None,
            "waiting_for_approval": bool(raw_item.get("waiting_for_approval")),
        }
        if include_previews:
            item_payload["input_preview"] = _compact_event_text(raw_item.get("input_preview"), limit=280) if raw_item.get("input_preview") else None
            item_payload["output_preview"] = _compact_event_text(raw_item.get("output_preview"), limit=280) if raw_item.get("output_preview") else None
        if include_detail:
            item_payload["detail"] = _json_safe(raw_item.get("detail")) if raw_item.get("detail") is not None else None
        payload_items.append(item_payload)

    return {
        "version": int(node_states.get("version") or 1),
        "graph_kind": str(node_states.get("graph_kind") or "").strip().lower() or None,
        "active_node_id": str(node_states.get("active_node_id") or "").strip() or None,
        "final_node_id": str(node_states.get("final_node_id") or "").strip() or None,
        "updated_at": node_states.get("updated_at"),
        "counts": counts,
        "items": payload_items,
    }


def _limited_node_states_view(node_states: Any) -> Dict[str, Any]:
    return _serialize_node_states(node_states, include_previews=False, include_detail=False)


def _serialize_run_snapshot(run_id: str, run: Dict[str, Any]) -> Dict[str, Any]:
    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    usage_masked = run.get("usage_masked") if isinstance(run.get("usage_masked"), dict) else {}
    usage_accounting = usage_accounting_service.accounting_record_from_snapshot(
        {
            "run_id": run_id,
            "status": run.get("status"),
            "created_at": run.get("created_at"),
            "updated_at": run.get("updated_at"),
            "completed_at": run.get("completed_at"),
            "context": context,
            "usage_accounting": run.get("usage_accounting") if isinstance(run.get("usage_accounting"), dict) else None,
            "usage_masked": usage_masked,
        }
    )
    usage_view = dict(usage_masked)
    if isinstance(usage_accounting, dict):
        usage_view.update(usage_accounting_service.usage_projection_from_record(usage_accounting))
    events = run.get("events") if isinstance(run.get("events"), list) else []
    trimmed_events = events[-min(len(events), ORION_MAX_EVENT_BUFFER):]
    tool_policy_audit = run.get("tool_policy_audit") if isinstance(run.get("tool_policy_audit"), list) else []
    trimmed_tool_policy_audit = tool_policy_audit[-min(len(tool_policy_audit), 500):]
    memory_trace = run.get("memory_trace") if isinstance(run.get("memory_trace"), dict) else {}
    trimmed_memory_trace = _trim_memory_trace(memory_trace)
    result_data = run.get("result_data") if isinstance(run.get("result_data"), dict) else None
    active_profile = _active_profile_snapshot(run)
    connector_binding = _resolve_run_connector_binding(run)
    approval_outcome = _approval_outcome_snapshot(run, trimmed_events)
    evidence_items = _evidence_items_snapshot(
        run,
        result_data=result_data,
        events=trimmed_events,
        connector_binding=connector_binding,
    )
    browser_introspection = _browser_introspection_snapshot(run, result_data)
    provider_model_truth = _serialize_provider_model_truth(
        context=context,
        metadata=metadata,
        active_profile=active_profile,
        usage_masked=usage_view,
        events=trimmed_events,
        result_data=result_data,
    )
    run_detail_contract = _run_detail_contract_snapshot(
        provider_model_truth=provider_model_truth,
        approval_outcome=approval_outcome,
        connector_binding=connector_binding,
        result_data=result_data,
        evidence_items=evidence_items,
    )
    node_states = _serialize_node_states(run.get("node_states"))
    result_summary = run.get("result")
    if isinstance(result_summary, str):
        summary_text = result_summary.strip()
    elif isinstance(result_data, dict) and isinstance(result_data.get("summary"), str):
        summary_text = str(result_data.get("summary")).strip()
    else:
        summary_text = ""

    payload = {
        "run_id": run_id,
        "engine": run.get("engine", "orion"),
        "status": run.get("status", "unknown"),
        "created_at": run.get("created_at"),
        "updated_at": run.get("updated_at"),
        "completed_at": run.get("completed_at"),
        "duration_ms": run.get("duration_ms"),
        "time_to_first_value_ms": run.get("time_to_first_value_ms"),
        "hitl_wait_total_ms": round(float(run.get("_hitl_wait_total_ms", 0.0)), 2),
        "workflow_id": context.get("workflow_id"),
        "workspace_id": context.get("workspace_id"),
        "owner_user_id": metadata.get("owner_user_id"),
        "owner_email": metadata.get("owner_email"),
        "active_profile_id": active_profile.get("id"),
        "active_profile_label": active_profile.get("label"),
        "active_profile_provider": active_profile.get("provider"),
        "active_profile_model": active_profile.get("model"),
        "active_adapter": active_profile.get("adapter"),
        "requested_provider": provider_model_truth.get("requested_provider"),
        "effective_provider": provider_model_truth.get("effective_provider"),
        "requested_model": provider_model_truth.get("requested_model"),
        "effective_model": provider_model_truth.get("effective_model"),
        "provider_overridden": provider_model_truth.get("provider_overridden"),
        "model_overridden": provider_model_truth.get("model_overridden"),
        "fallback_used": provider_model_truth.get("fallback_used"),
        "pack_id": _pack_id_from_context(run),
        "deployed_agent_id": metadata.get("deployed_agent_id"),
        "deployment_state": metadata.get("deployment_state"),
        "agent_role": metadata.get("agent_role"),
        "agent_role_source": metadata.get("agent_role_source"),
        "parent_run_id": metadata.get("parent_run_id"),
        "delegation_root_run_id": metadata.get("delegation_root_run_id"),
        "delegated_by_run_id": metadata.get("delegated_by_run_id"),
        "delegated_by_role": metadata.get("delegated_by_role"),
        "delegation_note": metadata.get("delegation_note"),
        "delegation_summary_cache": metadata.get("delegation_summary_cache"),
        "delegation_next_action": metadata.get("delegation_next_action"),
        "delegation_ready": metadata.get("delegation_ready"),
        "retry_of_run_id": metadata.get("retry_of_run_id"),
        "retry_root_run_id": metadata.get("retry_root_run_id"),
        "retry_sequence": metadata.get("retry_sequence"),
        "retry_count": metadata.get("retry_count"),
        "scheduled": metadata.get("scheduled"),
        "schedule_id": metadata.get("schedule_id"),
        "max_iterations": metadata.get("max_iterations"),
        "trust_mode": metadata.get("trust_mode"),
        "execution_target_requested": metadata.get("execution_target_requested"),
        "execution_target_selected": metadata.get("execution_target_selected"),
        "execution_environment": _execution_environment_from_metadata(metadata),
        "execution_target_reason": metadata.get("execution_target_reason"),
        "execution_target_fallback": metadata.get("execution_target_fallback"),
        "execution_target_required_capabilities": metadata.get("execution_target_required_capabilities"),
        "execution_target_missing_capabilities": metadata.get("execution_target_missing_capabilities"),
        "execution_target_matching_runtime_ids": metadata.get("execution_target_matching_runtime_ids"),
        "execution_target_available_runtime_ids": metadata.get("execution_target_available_runtime_ids"),
        "execution_target_busy_runtime_ids": metadata.get("execution_target_busy_runtime_ids"),
        "execution_target_busy_runtime_labels": metadata.get("execution_target_busy_runtime_labels"),
        "execution_target_queued_ahead_count": metadata.get("execution_target_queued_ahead_count"),
        "execution_target_estimated_wait_band": metadata.get("execution_target_estimated_wait_band"),
        "execution_target_waiting_for_runtime": metadata.get("execution_target_waiting_for_runtime"),
        "execution_target_waiting_for_capacity": metadata.get("execution_target_waiting_for_capacity"),
        "execution_target_preferred_runtime_id": metadata.get("execution_target_preferred_runtime_id"),
        "execution_target_preferred_runtime_label": metadata.get("execution_target_preferred_runtime_label"),
        "execution_target_preferred_runtime_reason": metadata.get("execution_target_preferred_runtime_reason"),
        "user_goal": context.get("user_goal"),
        "result_summary": summary_text[:5000],
        "connector_binding": connector_binding,
        "approval_outcome": approval_outcome,
        "evidence_items": evidence_items,
        "run_detail_contract": run_detail_contract,
        "usage_masked": usage_view,
        "usage_accounting": usage_accounting if isinstance(usage_accounting, dict) else None,
        "usage_provider": usage_view.get("provider"),
        "usage_model": usage_view.get("model"),
        "usage_prompt_tokens": usage_view.get("prompt_tokens", usage_view.get("input_tokens_est")),
        "usage_completion_tokens": usage_view.get("completion_tokens", usage_view.get("output_tokens_est")),
        "usage_total_tokens": usage_view.get("total_tokens", usage_view.get("total_tokens_est")),
        "usage_total_tokens_est": usage_view.get("total_tokens_est"),
        "usage_estimated_cost_usd": usage_view.get("estimated_cost_usd", usage_view.get("cost_est_usd")),
        "usage_cost_band": usage_view.get("cost_band"),
        "usage_timestamp": usage_view.get("timestamp"),
        "result_data": result_data,
        "node_states": node_states,
        "tool_policy_precheck": metadata.get("tool_policy_precheck"),
        "tool_policy_audit": trimmed_tool_policy_audit,
        "memory_trace": trimmed_memory_trace,
        "dag": run.get("dag"),
        "context": redact_sensitive(context),
        "replay_request": _build_replay_request_from_context(context),
        "pending_confirmation": run.get("pending_confirmation") if isinstance(run.get("pending_confirmation"), dict) else None,
        # Deprecated compatibility alias. Prefer `pending_confirmation`.
        "pending_approval": run.get("pending_confirmation") if isinstance(run.get("pending_confirmation"), dict)
        else run.get("pending_approval") if isinstance(run.get("pending_approval"), dict)
        else None,
        "browser_checkpoint": browser_checkpoint_service.browser_checkpoint_from_run(run) or None,
        "browser_introspection": browser_introspection,
        "browser_execution_binding": metadata.get("browser_execution_binding") if isinstance(metadata.get("browser_execution_binding"), dict) else None,
        "browser_resume_supported": browser_checkpoint_service.browser_resume_supported(run, metadata),
        "local_claimed_at": run.get("local_claimed_at"),
        "local_last_heartbeat_at": run.get("local_last_heartbeat_at"),
        "resume_after_confirmation_scheduled": bool(run.get("_resume_after_confirmation_scheduled")),
        "events": trimmed_events,
    }
    if provider_model_truth.get("fallback_reason"):
        payload["fallback_reason"] = provider_model_truth.get("fallback_reason")
    return payload


def _archive_run_if_terminal(run_id: str, run: Dict[str, Any]):
    if run.get("_archived"):
        return
    snapshot = _serialize_run_snapshot(run_id, run)
    run_state_repository.sync_archive_run(
        run_id,
        str(snapshot.get("status") or run.get("status") or "completed"),
        snapshot,
        str(snapshot.get("trace_id") or run.get("trace_id") or ""),
    )
    with RUN_HISTORY_LOCK:
        RUN_HISTORY.insert(0, snapshot)
        del RUN_HISTORY[ORION_HISTORY_LIMIT:]
    payload = getattr(getattr(run, "record", None), "payload", None)
    if payload is None or not hasattr(payload, "_suspend_notifications"):
        run["_archived"] = True
        return
    previous = bool(getattr(payload, "_suspend_notifications", False))
    payload._suspend_notifications = True
    try:
        run["_archived"] = True
    finally:
        payload._suspend_notifications = previous


def _refresh_archived_run_snapshot(run_id: str, run: Dict[str, Any]) -> None:
    if not run.get("_archived"):
        return
    snapshot = _serialize_run_snapshot(run_id, run)
    run_state_repository.sync_archive_run(
        run_id,
        str(snapshot.get("status") or run.get("status") or "completed"),
        snapshot,
        str(snapshot.get("trace_id") or run.get("trace_id") or ""),
    )
    with RUN_HISTORY_LOCK:
        for idx, item in enumerate(RUN_HISTORY):
            if str(item.get("run_id") or "").strip() == run_id:
                RUN_HISTORY[idx] = snapshot
                break


def _upsert_run_history_snapshot(snapshot: Dict[str, Any]) -> None:
    run_id = str(snapshot.get("run_id") or "").strip()
    if not run_id:
        return
    run_state_repository.sync_archive_run(
        run_id,
        str(snapshot.get("status") or "completed"),
        snapshot,
        str(snapshot.get("trace_id") or ""),
    )
    with RUN_HISTORY_LOCK:
        for idx, item in enumerate(RUN_HISTORY):
            if str(item.get("run_id") or "").strip() == run_id:
                RUN_HISTORY[idx] = snapshot
                break
        else:
            RUN_HISTORY.insert(0, snapshot)
            del RUN_HISTORY[ORION_HISTORY_LIMIT:]


def _load_run_history():
    items = run_state_repository.sync_list_run_archive(ORION_HISTORY_LIMIT)
    with RUN_HISTORY_LOCK:
        ACP_MANAGER.run_history.reload(items[:ORION_HISTORY_LIMIT])

def _json_safe(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, default=str))
    except Exception:
        return str(value)


def _serialize_live_run_state(run_id: str, run: Dict[str, Any]) -> Dict[str, Any]:
    return durable_run_payload(run_id, run, json_safe=_json_safe)


def _restore_live_run_state(item: Dict[str, Any]) -> Optional[tuple[str, Dict[str, Any]]]:
    return restore_run_state(
        item,
        json_safe=_json_safe,
        memory_enabled=ORION_MEMORY_ENABLED,
        now_iso=_utc_now_iso(),
        hydrate_execution_handle=should_restore_execution_handle(item),
    )


def _persist_live_run_state(run_id: str, run: Dict[str, Any]) -> None:
    payload = _serialize_live_run_state(run_id, run)
    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    run_state_repository.sync_upsert_live_run(
        run_id,
        str(payload.get("workspace_id") or context.get("workspace_id") or "default"),
        str(payload.get("tenant_id") or context.get("tenant_id") or metadata.get("tenant_id") or "default"),
        str(payload.get("status") or "queued"),
        payload,
        str(payload.get("trace_id") or context.get("trace_id") or ""),
    )


def _remove_live_run_state(run_id: str) -> None:
    run_state_repository.sync_delete_live_run(run_id)


def _compact_event_text(value: Any, limit: int = 800) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"

def redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: Dict[str, Any] = {}
        for key, sub in value.items():
            key_lower = str(key).lower()
            if any(token in key_lower for token in ["token", "key", "secret", "password", "authorization", "credential", "access"]):
                redacted[key] = "***redacted***"
            else:
                redacted[key] = redact_sensitive(sub)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    return value

def _history_item_matches(
    item: Dict[str, Any],
    workspace_id: Optional[str],
    status: Optional[str],
    pack_id: Optional[str],
) -> bool:
    if workspace_id and str(item.get("workspace_id") or "").strip() != workspace_id:
        return False
    if status and str(item.get("status") or "").strip().lower() != status.lower().strip():
        return False
    if pack_id and str(item.get("pack_id") or "").strip().lower() != pack_id.lower().strip():
        return False
    return True


def _resolve_run_connector_binding(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    context = item.get("context") if isinstance(item.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    connector_id = str(
        item.get("connector_credential_id")
        or metadata.get("connector_credential_id")
        or ""
    ).strip()
    channel = str(
        item.get("source_channel")
        or metadata.get("source_channel")
        or metadata.get("channel")
        or ""
    ).strip().lower()
    if not connector_id and not channel:
        return None

    workspace_id = _normalize_workspace_id(
        item.get("workspace_id")
        or context.get("workspace_id")
        or metadata.get("workspace_id")
    )
    connector_row = None
    if connector_id:
        for entry in list_vault_connectors(workspace_id):
            if str(entry.get("id") or "").strip() == connector_id:
                connector_row = entry
                break

    provider = str((connector_row or {}).get("connector") or "").strip().lower()
    connector_metadata = (connector_row or {}).get("metadata") if isinstance((connector_row or {}).get("metadata"), dict) else {}
    assigned_role = _normalize_agent_role_safe(connector_metadata.get("agent_role"))
    identity_label = (
        str(connector_metadata.get("chat_id") or "").strip()
        or str(connector_metadata.get("bot_username") or "").strip()
        or str(connector_metadata.get("from_number") or "").strip()
        or str(connector_metadata.get("phone_number") or "").strip()
        or str(connector_metadata.get("emailAddress") or "").strip()
        or str(connector_metadata.get("mail") or "").strip()
        or str(connector_metadata.get("userPrincipalName") or "").strip()
        or str(connector_metadata.get("displayName") or "").strip()
        or str(connector_metadata.get("calendar_id") or "").strip()
    )
    if connector_id and not identity_label:
        try:
            secret = resolve_vault_credential(connector_id, workspace_id)
        except Exception:
            secret = {}
        identity_label = (
            str(secret.get("chat_id") or "").strip()
            or str(secret.get("channel_id") or "").strip()
            or str(secret.get("instagram_account_id") or "").strip()
            or str(secret.get("page_id") or "").strip()
            or str(secret.get("bot_username") or "").strip()
            or str(secret.get("from_number") or "").strip()
            or str(secret.get("phone_number") or "").strip()
            or str(secret.get("emailAddress") or "").strip()
            or str(secret.get("calendar_id") or "").strip()
        )

    if not channel:
        connector_channel_map = {
            "telegram_bot": "telegram",
            "whatsapp_twilio": "whatsapp",
            "google_workspace": "email",
            "microsoft_365": "email",
            "discord_bot": "discord",
            "instagram_business": "instagram",
            "irc": "irc",
        }
        channel = connector_channel_map.get(provider, "")

    if not connector_id and not channel and not provider and not identity_label:
        return None

    return {
        "connector_id": connector_id or None,
        "channel": channel or None,
        "connector": provider or None,
        "label": (connector_row or {}).get("label"),
        "identity_label": identity_label or None,
        "routing_scope": AGENT_WORKSPACE_LABELS.get(assigned_role, assigned_role) if assigned_role else "Shared workspace",
        "agent_role": assigned_role or None,
    }


def _run_tool_capabilities(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    from server_modules.tool_availability_truth import resolve_workspace_tool_capabilities

    context = item.get("context") if isinstance(item.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    workspace_id = _normalize_workspace_id(
        item.get("workspace_id")
        or context.get("workspace_id")
        or metadata.get("workspace_id")
    ) or "default"
    connector_binding = _resolve_run_connector_binding(item)
    connector_id = str((connector_binding or {}).get("connector") or "").strip().lower()
    if not connector_id:
        return []
    return resolve_workspace_tool_capabilities(workspace_id, connector_filter=[connector_id])


def _humanize_evidence_token(value: Any) -> str:
    return (
        str(value or "")
        .strip()
        .replace("_", " ")
        .replace("-", " ")
        .title()
    )


def _evidence_item(identifier: str, label: str, value: Any) -> Optional[Dict[str, str]]:
    text = _compact_event_text(value, limit=180)
    if not text:
        return None
    return {
        "id": identifier,
        "label": label,
        "value": text,
    }


def _connector_action_result_hint(result: Any) -> str:
    if isinstance(result, dict):
        for key in ("message_id", "id", "name", "path", "url", "htmlLink", "webViewLink", "status"):
            value = str(result.get(key) or "").strip()
            if value:
                return value
    if isinstance(result, list):
        return f"{len(result)} item{'s' if len(result) != 1 else ''}"
    return _compact_event_text(result, limit=180)


def _result_connector_action(result_data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(result_data, dict):
        return {}
    connector_action = result_data.get("connector_action")
    if isinstance(connector_action, dict):
        return connector_action
    last_node_data = result_data.get("last_node_data")
    if isinstance(last_node_data, dict):
        nested_connector_action = last_node_data.get("connector_action")
        if isinstance(nested_connector_action, dict):
            return nested_connector_action
    return {}


def _approval_outcome_snapshot(
    item: Dict[str, Any],
    events: List[Dict[str, Any]],
) -> Optional[Dict[str, str]]:
    # `pending_confirmation` is primary. `pending_approval` remains as a deprecated
    # compatibility alias for older callers and archived payloads.
    pending = (
        item.get("pending_confirmation")
        if isinstance(item.get("pending_confirmation"), dict)
        else item.get("pending_approval")
        if isinstance(item.get("pending_approval"), dict)
        else {}
    )
    if str(pending.get("approval_id") or "").strip():
        return {"status": "pending", "label": "Confirmation required"}
    for entry in reversed(events):
        if not isinstance(entry, dict):
            continue
        event_name = str(entry.get("event") or "").strip().lower()
        data = entry.get("data") if isinstance(entry.get("data"), dict) else {}
        decision = str(data.get("decision") or entry.get("decision") or "").strip().lower()
        if event_name == "approval_resolved":
            if bool(data.get("approved")) or decision in {"approved", "approve", "proceed", "yes"}:
                return {"status": "approved", "label": "Confirmed"}
            if bool(data.get("escalated")) or decision in {"escalated", "escalate"}:
                return {"status": "escalated", "label": "Blocked by policy"}
            return {"status": "rejected", "label": "Declined"}
        if event_name == "approval_skipped":
            return None
        if event_name == "workflow_human_resolved":
            if decision == "approved":
                return {"status": "approved", "label": "Confirmed"}
            if decision in {"rejected", "reject", "declined", "decline", "hold"}:
                return {"status": "rejected", "label": "Declined"}
            if decision in {"blocked", "denied", "deny", "escalated", "escalate"}:
                return {"status": "blocked", "label": "Blocked by policy"}
    return None


def _evidence_items_snapshot(
    item: Dict[str, Any],
    *,
    result_data: Optional[Dict[str, Any]],
    events: List[Dict[str, Any]],
    connector_binding: Optional[Dict[str, Any]],
) -> List[Dict[str, str]]:
    evidence: List[Dict[str, str]] = []
    connector_action = _result_connector_action(result_data)
    approval_outcome = _approval_outcome_snapshot(item, events)
    if connector_action:
        execution_label = None
        if bool(connector_action.get("duplicate_protected")):
            execution_label = "Duplicate protected"
        elif bool(connector_action.get("executed")):
            execution_label = "Executed"
        if execution_label:
            execution_item = _evidence_item("connector:execution", "Execution", execution_label)
            if execution_item:
                evidence.append(execution_item)
        action_item = _evidence_item(
            "connector:action",
            "Action",
            _humanize_evidence_token(connector_action.get("action_id") or "connector_action"),
        )
        if action_item:
            evidence.append(action_item)
        system_item = _evidence_item(
            "connector:system",
            "System",
            connector_binding.get("label") if isinstance(connector_binding, dict) else (
                _humanize_evidence_token(connector_action.get("connector"))
            ),
        )
        if system_item:
            evidence.append(system_item)
        target_value = (
            connector_action.get("recipient")
            or connector_action.get("to_number")
            or connector_action.get("channel_id")
            or connector_action.get("chat_id")
            or connector_action.get("recipient_id")
            or connector_action.get("comment_id")
            or connector_action.get("path")
            or connector_action.get("title")
            or connector_action.get("url")
            or (connector_binding.get("identity_label") if isinstance(connector_binding, dict) else None)
        )
        target_item = _evidence_item("connector:target", "Target", target_value)
        if target_item:
            evidence.append(target_item)
        result_item = _evidence_item(
            "connector:result",
            "Evidence",
            _connector_action_result_hint(connector_action.get("result")),
        )
        if result_item:
            evidence.append(result_item)
        if approval_outcome and approval_outcome.get("status") != "pending":
            approval_item = _evidence_item("approval", "Confirmation", approval_outcome.get("label"))
            if approval_item:
                evidence.append(approval_item)
        return evidence[:5]

    if approval_outcome and approval_outcome.get("status") != "pending":
        approval_item = _evidence_item("approval", "Confirmation", approval_outcome.get("label"))
        if approval_item:
            evidence.append(approval_item)

    outputs = result_data.get("outputs") if isinstance(result_data, dict) and isinstance(result_data.get("outputs"), dict) else {}
    actions = outputs.get("actions") if isinstance(outputs.get("actions"), list) else []
    if any(isinstance(item, dict) and bool(item.get("duplicate_protected")) for item in actions):
        guard_item = _evidence_item("output:execution", "Execution", "Duplicate protected")
        if guard_item:
            evidence.append(guard_item)
    preferred_keys = (
        "file_path",
        "path",
        "title",
        "sheet_name",
        "storage",
        "format",
        "operation",
        "rows_written",
        "rows_read",
        "items_written",
        "operations_executed",
        "outbound_actions",
    )
    seen: Set[str] = set()
    for key in preferred_keys:
        if key in seen or key not in outputs:
            continue
        seen.add(key)
        value = outputs.get(key)
        if isinstance(value, list):
            value = f"{len(value)} item{'s' if len(value) != 1 else ''}"
        elif isinstance(value, dict):
            value = "Structured output ready"
        item_payload = _evidence_item(f"output:{key}", _humanize_evidence_token(key), value)
        if item_payload:
            evidence.append(item_payload)
        if len(evidence) >= 4:
            return evidence[:5]
    return evidence[:5]


def _summarize_history_item(item: Dict[str, Any]) -> Dict[str, Any]:
    policy_audit = item.get("tool_policy_audit") if isinstance(item.get("tool_policy_audit"), list) else []
    memory_trace = item.get("memory_trace") if isinstance(item.get("memory_trace"), dict) else {}
    memory_reads = memory_trace.get("reads") if isinstance(memory_trace.get("reads"), list) else []
    memory_writes = memory_trace.get("writes") if isinstance(memory_trace.get("writes"), list) else []
    events = item.get("events") if isinstance(item.get("events"), list) else []
    result_data = item.get("result_data") if isinstance(item.get("result_data"), dict) else None
    blocked_count = 0
    approval_required_count = 0
    allow_count = 0
    node_states = item.get("node_states") if isinstance(item.get("node_states"), dict) else {}
    node_items = node_states.get("items") if isinstance(node_states.get("items"), list) else []
    connector_binding = _resolve_run_connector_binding(item)
    tool_capabilities = _run_tool_capabilities(item)
    approval_outcome = _approval_outcome_snapshot(item, events)
    evidence_items = _evidence_items_snapshot(
        item,
        result_data=result_data,
        events=events,
        connector_binding=connector_binding,
    )
    for entry in policy_audit:
        if not isinstance(entry, dict):
            continue
        decision = str(entry.get("decision") or "").strip().lower()
        if decision == "blocked":
            blocked_count += 1
        elif decision == "approval_required":
            approval_required_count += 1
        elif decision == "allow":
            allow_count += 1
    payload = {
        "run_id": item.get("run_id"),
        "engine": item.get("engine"),
        "status": item.get("status"),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "completed_at": item.get("completed_at"),
        "duration_ms": item.get("duration_ms"),
        "time_to_first_value_ms": item.get("time_to_first_value_ms"),
        "hitl_wait_total_ms": item.get("hitl_wait_total_ms"),
        "workflow_id": item.get("workflow_id"),
        "workspace_id": item.get("workspace_id"),
        "owner_user_id": item.get("owner_user_id"),
        "owner_email": item.get("owner_email"),
        "active_profile_id": item.get("active_profile_id"),
        "active_profile_label": item.get("active_profile_label"),
        "active_profile_provider": item.get("active_profile_provider"),
        "active_profile_model": item.get("active_profile_model"),
        "active_adapter": item.get("active_adapter"),
        "requested_provider": item.get("requested_provider"),
        "effective_provider": item.get("effective_provider"),
        "requested_model": item.get("requested_model"),
        "effective_model": item.get("effective_model"),
        "provider_overridden": bool(item.get("provider_overridden")),
        "model_overridden": bool(item.get("model_overridden")),
        "fallback_used": bool(item.get("fallback_used")),
        "pack_id": item.get("pack_id"),
        "agent_role": item.get("agent_role"),
        "agent_role_source": item.get("agent_role_source"),
        "parent_run_id": item.get("parent_run_id"),
        "delegation_root_run_id": item.get("delegation_root_run_id"),
        "delegated_by_run_id": item.get("delegated_by_run_id"),
        "delegated_by_role": item.get("delegated_by_role"),
        "delegation_note": item.get("delegation_note"),
        "delegation_summary": item.get("delegation_summary_cache"),
        "delegation_next_action": item.get("delegation_next_action"),
        "delegation_ready": bool(item.get("delegation_ready")),
        "trust_mode": item.get("trust_mode"),
        "execution_target_requested": item.get("execution_target_requested"),
        "execution_target_selected": item.get("execution_target_selected"),
        "execution_target_reason": item.get("execution_target_reason"),
        "execution_target_fallback": item.get("execution_target_fallback"),
        "user_goal": item.get("user_goal"),
        "result_summary": item.get("result_summary"),
        "connector_binding": connector_binding,
        "tool_capabilities": tool_capabilities,
        "usage_provider": item.get("usage_provider"),
        "usage_model": item.get("usage_model"),
        "usage_prompt_tokens": item.get("usage_prompt_tokens"),
        "usage_completion_tokens": item.get("usage_completion_tokens"),
        "usage_total_tokens": item.get("usage_total_tokens"),
        "usage_total_tokens_est": item.get("usage_total_tokens_est"),
        "usage_estimated_cost_usd": item.get("usage_estimated_cost_usd"),
        "usage_cost_band": item.get("usage_cost_band"),
        "usage_timestamp": item.get("usage_timestamp"),
        "graph_kind": node_states.get("graph_kind"),
        "active_node_id": node_states.get("active_node_id"),
        "final_node_id": node_states.get("final_node_id"),
        "workflow_node_count": len(node_items),
        "node_state_counts": node_states.get("counts") if isinstance(node_states.get("counts"), dict) else {},
        "tool_policy_precheck": item.get("tool_policy_precheck"),
        "tool_policy_audit_count": len(policy_audit),
        "tool_policy_blocked_count": blocked_count,
        "tool_policy_approval_required_count": approval_required_count,
        "tool_policy_allow_count": allow_count,
        "memory_enabled": bool(memory_trace.get("enabled")),
        "memory_read_count": len(memory_reads),
        "memory_write_count": len(memory_writes),
        "memory_last_error": memory_trace.get("last_error"),
        "evidence_items": evidence_items,
    }
    if approval_outcome:
        payload["approval_outcome"] = approval_outcome
    if item.get("fallback_reason"):
        payload["fallback_reason"] = item.get("fallback_reason")
    return payload


def _get_archived_run_history_item(run_id: str) -> Optional[Dict[str, Any]]:
    with RUN_HISTORY_LOCK:
        for item in RUN_HISTORY:
            if str(item.get("run_id")) == run_id:
                return dict(item)
    return None


def _snapshot_updated_sort_ts(snapshot: Optional[Dict[str, Any]]) -> datetime:
    if not isinstance(snapshot, dict):
        return datetime.min.replace(tzinfo=timezone.utc)
    return (
        _parse_utc_ts(snapshot.get("updated_at"))
        or _parse_utc_ts(snapshot.get("completed_at"))
        or _parse_utc_ts(snapshot.get("created_at"))
        or datetime.min.replace(tzinfo=timezone.utc)
    )


def _prefer_archived_snapshot(active_snapshot: Optional[Dict[str, Any]], archived_snapshot: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(active_snapshot, dict):
        return archived_snapshot if isinstance(archived_snapshot, dict) else None
    if not isinstance(archived_snapshot, dict):
        return active_snapshot
    if _snapshot_updated_sort_ts(archived_snapshot) >= _snapshot_updated_sort_ts(active_snapshot):
        return archived_snapshot
    return active_snapshot


def _get_replay_payload(run_id: str) -> Dict[str, Any]:
    active = run_state_repository.sync_get_live_run(run_id)
    archived_item = run_state_repository.sync_get_archived_run(run_id) or _get_archived_run_history_item(run_id)
    active_snapshot = _serialize_run_snapshot(run_id, active) if isinstance(active, dict) else None
    chosen_snapshot = _prefer_archived_snapshot(active_snapshot, archived_item)
    if isinstance(chosen_snapshot, dict):
        chosen_snapshot["connector_binding"] = _resolve_run_connector_binding(chosen_snapshot)
        return chosen_snapshot

    raise HTTPException(status_code=404, detail="Run ID not found")

def iter_logs_for_run(run_id: str):
    run = runs.get(run_id)
    if not run:
        return
    q = run["logs"]
    while True:
        try:
            data = q.get(timeout=1)
            if data is None:
                break
            if data == "__PAUSE_REQUIRED__":
                yield {"event": "pause", "data": "Human Input Needed"}
            else:
                if run.get("_first_value_mono") is None:
                    first_mono = time.monotonic()
                    run["_first_value_mono"] = first_mono
                    started = run.get("_started_mono")
                    if isinstance(started, (int, float)):
                        ttfv_ms = max(0.0, (first_mono - started) * 1000.0)
                        run["time_to_first_value_ms"] = round(ttfv_ms, 2)
                        metrics_add("first_value_sum_ms", ttfv_ms)
                        metrics_inc("first_value_count", 1)
                yield {"event": "log", "data": json.dumps(data)}
        except queue.Empty:
            status = runs.get(run_id, {}).get("status")
            if status in ["completed", "failed", "timeout"]:
                break
            continue
