from __future__ import annotations

from typing import Any, Callable

from fastapi import HTTPException

from server_modules import run_state_repository, rust_runtime_kernel_client


def build_run_detail_response_callbacks(
    *,
    get_replay_payload: Callable[[str], dict[str, Any]],
    serialize_run_snapshot: Callable[[str, dict[str, Any]], dict[str, Any]],
    enforce_run_owner_access: Callable[[Any, Any], None],
    can_view_sensitive_run_payload: Callable[[Any], bool],
    limited_run_context_view: Callable[[dict[str, Any]], dict[str, Any]],
    build_delegation_summary: Callable[[dict[str, Any], Any], Any],
    find_run_relationships: Callable[[str, dict[str, Any]], tuple[Any, Any]],
    resolve_run_connector_binding: Callable[[dict[str, Any]], Any],
    redact_sensitive: Callable[[dict[str, Any]], dict[str, Any]],
    limited_result_data_view_fn: Callable[[Any], Any],
    limited_node_states_view_fn: Callable[[Any], Any],
    trim_memory_trace_fn: Callable[[dict[str, Any]], Any],
    get_pending_confirmation_fn: Callable[[dict[str, Any]], Any],
    build_archived_run_detail_response: Callable[..., dict[str, Any]],
    build_live_run_detail_response: Callable[..., dict[str, Any]],
    build_run_browser_checkpoint_payload: Callable[..., dict[str, Any]],
    build_run_browser_session_payload: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    return {
        "get_replay_payload": get_replay_payload,
        "serialize_run_snapshot": serialize_run_snapshot,
        "enforce_run_owner_access": enforce_run_owner_access,
        "can_view_sensitive_run_payload": can_view_sensitive_run_payload,
        "limited_run_context_view": limited_run_context_view,
        "build_delegation_summary": build_delegation_summary,
        "find_run_relationships": find_run_relationships,
        "resolve_run_connector_binding": resolve_run_connector_binding,
        "redact_sensitive": redact_sensitive,
        "limited_result_data_view_fn": limited_result_data_view_fn,
        "limited_node_states_view_fn": limited_node_states_view_fn,
        "trim_memory_trace_fn": trim_memory_trace_fn,
        "get_pending_confirmation_fn": get_pending_confirmation_fn,
        "build_archived_run_detail_response": build_archived_run_detail_response,
        "build_live_run_detail_response": build_live_run_detail_response,
        "build_run_browser_checkpoint_payload": build_run_browser_checkpoint_payload,
        "build_run_browser_session_payload": build_run_browser_session_payload,
    }


def build_default_run_detail_response_callbacks(
    *,
    import_module: Callable[..., Any],
    enforce_run_owner_access: Callable[[Any, Any], None],
    can_view_sensitive_run_payload: Callable[[Any], bool],
    limited_run_context_view: Callable[[dict[str, Any]], dict[str, Any]],
    limited_result_data_view_fn: Callable[[Any], Any],
    get_pending_confirmation_fn: Callable[[dict[str, Any]], Any],
    build_archived_run_detail_response: Callable[..., dict[str, Any]],
    build_live_run_detail_response: Callable[..., dict[str, Any]],
    build_run_browser_checkpoint_payload: Callable[..., dict[str, Any]],
    build_run_browser_session_payload: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    runs_delegation = import_module(
        "server_modules.runs_delegation",
        fromlist=["_build_delegation_summary", "_find_run_relationships"],
    )
    runs_output = import_module(
        "server_modules.runs_output",
        fromlist=[
            "_get_replay_payload",
            "_limited_node_states_view",
            "_resolve_run_connector_binding",
            "_serialize_run_snapshot",
            "redact_sensitive",
        ],
    )
    memory_service = import_module(
        "server_modules.memory_service",
        fromlist=["trim_memory_trace"],
    )
    return build_run_detail_response_callbacks(
        get_replay_payload=runs_output._get_replay_payload,
        serialize_run_snapshot=runs_output._serialize_run_snapshot,
        enforce_run_owner_access=enforce_run_owner_access,
        can_view_sensitive_run_payload=can_view_sensitive_run_payload,
        limited_run_context_view=limited_run_context_view,
        build_delegation_summary=runs_delegation._build_delegation_summary,
        find_run_relationships=runs_delegation._find_run_relationships,
        resolve_run_connector_binding=runs_output._resolve_run_connector_binding,
        redact_sensitive=runs_output.redact_sensitive,
        limited_result_data_view_fn=limited_result_data_view_fn,
        limited_node_states_view_fn=runs_output._limited_node_states_view,
        trim_memory_trace_fn=memory_service.trim_memory_trace,
        get_pending_confirmation_fn=get_pending_confirmation_fn,
        build_archived_run_detail_response=build_archived_run_detail_response,
        build_live_run_detail_response=build_live_run_detail_response,
        build_run_browser_checkpoint_payload=build_run_browser_checkpoint_payload,
        build_run_browser_session_payload=build_run_browser_session_payload,
    )


def _run_query_user_role(current_user: Any) -> str:
    if isinstance(current_user, dict):
        if str(current_user.get("auth_type") or "").strip().lower() == "api_key":
            return "system"
        role = str(current_user.get("workspace_role") or current_user.get("role") or "viewer").strip()
        return role or "viewer"
    return "viewer"


def _run_query_scope(payload: dict[str, Any], metadata: dict[str, Any]) -> tuple[str, str, str]:
    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    workspace_id = str(
        payload.get("workspace_id")
        or context.get("workspace_id")
        or metadata.get("workspace_id")
        or "default"
    ).strip() or "default"
    tenant_id = str(
        payload.get("tenant_id")
        or context.get("tenant_id")
        or metadata.get("tenant_id")
        or "default"
    ).strip() or "default"
    run_status = str(
        payload.get("status")
        or payload.get("state")
        or context.get("status")
        or metadata.get("status")
        or ""
    ).strip()
    return workspace_id, tenant_id, run_status


def _enforce_run_query_get_run_decision(
    *,
    run_id: str,
    current_user: Any,
    payload: dict[str, Any],
    metadata: dict[str, Any],
    sensitive_payload_requested: bool = False,
) -> dict[str, Any]:
    workspace_id, tenant_id, run_status = _run_query_scope(payload, metadata)
    rust_payload = {
        "operation": "get_run",
        "workspace_id": workspace_id,
        "tenant_id": tenant_id,
        "run_id": str(run_id or "").strip(),
        "run_status": run_status,
        "user_role": _run_query_user_role(current_user),
        "workspace_access_denied": False,
        "owner_access_denied": False,
        "kill_switch_active": False,
        "history_window_allowed": True,
        "body": {
            "user_id": str((current_user or {}).get("user_id") or "").strip() or "user",
            "sensitive_payload_requested": bool(sensitive_payload_requested),
        },
    }
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "run-api-decision",
            rust_payload,
            allow_approval_required=True,
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise HTTPException(status_code=409, detail=f"Rust run-api gate blocked get_run: {exc.reason}") from exc
    next_action = str(decision.get("next_action") or "").strip()
    if sensitive_payload_requested and next_action == "request_owner_approval":
        raise HTTPException(
            status_code=409,
            detail=f"Rust run-api gate blocked get_run: {decision.get('reason') or 'owner_approval_required'}",
        )
    if next_action != "get_run":
        raise HTTPException(
            status_code=423,
            detail=f"Rust run-api gate returned unexpected next_action for get_run: {next_action or 'missing'}",
        )
    return dict(decision)


def _resolve_run_detail_source(
    run_id: str,
    *,
    current_user: Any,
    runs: dict[str, dict[str, Any]],
    get_live_run_fn: Callable[[str], dict[str, Any] | None] | None,
    get_replay_payload: Callable[[str], dict[str, Any]],
    serialize_run_snapshot: Callable[[str, dict[str, Any]], dict[str, Any]],
    enforce_run_owner_access: Callable[[Any, Any], None],
    can_view_sensitive_run_payload: Callable[[Any], bool],
    sensitive_payload_requested: bool = False,
) -> dict[str, Any]:
    include_sensitive = can_view_sensitive_run_payload(current_user)
    run = runs.get(run_id) if isinstance(runs, dict) else None
    if not isinstance(run, dict) and callable(get_live_run_fn):
        run = get_live_run_fn(run_id)

    if run is None:
        try:
            snapshot = get_replay_payload(run_id)
        except HTTPException:
            raise HTTPException(404, "Run ID not found")
        enforce_run_owner_access(current_user, snapshot)
        context = snapshot.get("context") if isinstance(snapshot.get("context"), dict) else {}
        metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
        _enforce_run_query_get_run_decision(
            run_id=run_id,
            current_user=current_user,
            payload=snapshot,
            metadata=metadata,
            sensitive_payload_requested=sensitive_payload_requested,
        )
        return {
            "archived": True,
            "include_sensitive": include_sensitive,
            "run": None,
            "snapshot": snapshot,
            "context": context,
            "metadata": metadata,
        }

    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    snapshot = serialize_run_snapshot(run_id, run)
    enforce_run_owner_access(current_user, snapshot)
    _enforce_run_query_get_run_decision(
        run_id=run_id,
        current_user=current_user,
        payload=run,
        metadata=metadata,
        sensitive_payload_requested=sensitive_payload_requested,
    )
    return {
        "archived": False,
        "include_sensitive": include_sensitive,
        "run": run,
        "snapshot": snapshot,
        "context": context,
        "metadata": metadata,
    }


def build_run_detail_response(
    run_id: str,
    *,
    current_user: Any,
    runs: dict[str, dict[str, Any]],
    get_live_run_fn: Callable[[str], dict[str, Any] | None] | None = None,
    get_replay_payload: Callable[[str], dict[str, Any]],
    serialize_run_snapshot: Callable[[str, dict[str, Any]], dict[str, Any]],
    enforce_run_owner_access: Callable[[Any, Any], None],
    can_view_sensitive_run_payload: Callable[[Any], bool],
    limited_run_context_view: Callable[[dict[str, Any]], dict[str, Any]],
    build_delegation_summary: Callable[[dict[str, Any], Any], Any],
    find_run_relationships: Callable[[str, dict[str, Any]], tuple[Any, Any]],
    resolve_run_connector_binding: Callable[[dict[str, Any]], Any],
    redact_sensitive: Callable[[dict[str, Any]], dict[str, Any]],
    limited_result_data_view_fn: Callable[[Any], Any],
    limited_node_states_view_fn: Callable[[Any], Any],
    trim_memory_trace_fn: Callable[[dict[str, Any]], Any],
    get_pending_confirmation_fn: Callable[[dict[str, Any]], Any],
    build_archived_run_detail_response: Callable[..., dict[str, Any]],
    build_live_run_detail_response: Callable[..., dict[str, Any]],
    build_run_browser_checkpoint_payload: Callable[..., dict[str, Any]] | None = None,
    build_run_browser_session_payload: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    resolved = _resolve_run_detail_source(
        run_id,
        current_user=current_user,
        runs=runs,
        get_live_run_fn=get_live_run_fn,
        get_replay_payload=get_replay_payload,
        serialize_run_snapshot=serialize_run_snapshot,
        enforce_run_owner_access=enforce_run_owner_access,
        can_view_sensitive_run_payload=can_view_sensitive_run_payload,
        sensitive_payload_requested=False,
    )
    include_sensitive = bool(resolved.get("include_sensitive"))

    if bool(resolved.get("archived")):
        snapshot = resolved["snapshot"]
        context = resolved["context"]
        metadata = resolved["metadata"]
        parent_run, child_runs = find_run_relationships(run_id, snapshot)
        delegation_summary = build_delegation_summary(snapshot, child_runs)
        safe_context = redact_sensitive(context) if include_sensitive else limited_run_context_view(context)
        return build_archived_run_detail_response(
            run_id=run_id,
            snapshot=snapshot,
            metadata=metadata,
            include_sensitive=include_sensitive,
            safe_context=safe_context,
            parent_run=parent_run,
            child_runs=child_runs,
            delegation_summary=delegation_summary,
            connector_binding=resolve_run_connector_binding(snapshot),
            limited_result_data_view_fn=limited_result_data_view_fn,
            limited_node_states_view_fn=limited_node_states_view_fn,
        )

    run = resolved["run"]
    snapshot = resolved["snapshot"]
    context = resolved["context"]
    metadata = resolved["metadata"]
    parent_run, child_runs = find_run_relationships(run_id, snapshot)
    delegation_summary = build_delegation_summary(snapshot, child_runs)
    safe_context = redact_sensitive(context) if include_sensitive else limited_run_context_view(context)
    return build_live_run_detail_response(
        run_id=run_id,
        run=run,
        snapshot=snapshot,
        metadata=metadata,
        include_sensitive=include_sensitive,
        safe_context=safe_context,
        parent_run=parent_run,
        child_runs=child_runs,
        delegation_summary=delegation_summary,
        connector_binding=resolve_run_connector_binding(snapshot),
        limited_result_data_view_fn=limited_result_data_view_fn,
        limited_node_states_view_fn=limited_node_states_view_fn,
        trim_memory_trace_fn=trim_memory_trace_fn,
        get_pending_confirmation_fn=get_pending_confirmation_fn,
    )


def build_run_browser_checkpoint_response(
    run_id: str,
    *,
    current_user: Any,
    runs: dict[str, dict[str, Any]],
    get_live_run_fn: Callable[[str], dict[str, Any] | None] | None = None,
    get_replay_payload: Callable[[str], dict[str, Any]],
    serialize_run_snapshot: Callable[[str, dict[str, Any]], dict[str, Any]],
    enforce_run_owner_access: Callable[[Any, Any], None],
    can_view_sensitive_run_payload: Callable[[Any], bool],
    build_run_browser_checkpoint_payload: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    resolved = _resolve_run_detail_source(
        run_id,
        current_user=current_user,
        runs=runs,
        get_live_run_fn=get_live_run_fn,
        get_replay_payload=get_replay_payload,
        serialize_run_snapshot=serialize_run_snapshot,
        enforce_run_owner_access=enforce_run_owner_access,
        can_view_sensitive_run_payload=can_view_sensitive_run_payload,
        sensitive_payload_requested=True,
    )
    source = resolved["snapshot"] if bool(resolved.get("archived")) else resolved["run"]
    return build_run_browser_checkpoint_payload(
        run_id=run_id,
        source=source,
        metadata=resolved["metadata"],
        include_sensitive=bool(resolved.get("include_sensitive")),
        archived=bool(resolved.get("archived")),
    )


def build_run_browser_session_response(
    run_id: str,
    *,
    current_user: Any,
    runs: dict[str, dict[str, Any]],
    get_live_run_fn: Callable[[str], dict[str, Any] | None] | None = None,
    get_replay_payload: Callable[[str], dict[str, Any]],
    serialize_run_snapshot: Callable[[str, dict[str, Any]], dict[str, Any]],
    enforce_run_owner_access: Callable[[Any, Any], None],
    can_view_sensitive_run_payload: Callable[[Any], bool],
    build_run_browser_session_payload: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    resolved = _resolve_run_detail_source(
        run_id,
        current_user=current_user,
        runs=runs,
        get_live_run_fn=get_live_run_fn,
        get_replay_payload=get_replay_payload,
        serialize_run_snapshot=serialize_run_snapshot,
        enforce_run_owner_access=enforce_run_owner_access,
        can_view_sensitive_run_payload=can_view_sensitive_run_payload,
        sensitive_payload_requested=True,
    )
    source = resolved["snapshot"] if bool(resolved.get("archived")) else resolved["run"]
    return build_run_browser_session_payload(
        run_id=run_id,
        source=source,
        metadata=resolved["metadata"],
        include_sensitive=bool(resolved.get("include_sensitive")),
        archived=bool(resolved.get("archived")),
    )


def build_run_list_response(
    *,
    limit: int,
    offset: int,
    workspace_id: str | None,
    status: str | None,
    pack_id: str | None,
    current_user: Any,
    runs: dict[str, dict[str, Any]],
    list_live_runs_fn: Callable[[], list[dict[str, Any]]] | None = None,
    run_history_lock: Any,
    run_history: list[dict[str, Any]],
    serialize_run_snapshot: Callable[[str, dict[str, Any]], dict[str, Any]],
    history_item_matches: Callable[[Any, Any, Any, Any], bool],
    current_user_is_privileged: Callable[[Any], bool],
    extract_run_owner_user_id: Callable[[Any], str],
    summarize_history_item: Callable[[dict[str, Any]], dict[str, Any]],
    parse_utc_ts: Callable[[Any], Any],
    list_live_runs_page_fn: Callable[[int, int, str | None, list[str] | None], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    safe_limit = max(1, min(int(limit), 200))
    safe_offset = max(0, int(offset))
    request_user_id = str((current_user or {}).get("user_id") or "").strip()
    include_all = current_user_is_privileged(current_user)
    if not include_all and not request_user_id:
        raise HTTPException(status_code=401, detail="Authenticated user id is required.")

    def _visible(payload: Any) -> bool:
        if include_all:
            return True
        return extract_run_owner_user_id(payload) == request_user_id

    items: list[dict[str, Any]] = []
    seen_run_ids: set[str] = set()

    live_runs: list[dict[str, Any]] = []
    if callable(list_live_runs_page_fn):
        page_size = max(safe_limit + safe_offset, 200)
        page_offset = 0
        requested_states = [str(status or "").strip().lower()] if str(status or "").strip() else None
        while True:
            page = list_live_runs_page_fn(page_size, page_offset, workspace_id, requested_states)
            if not page:
                break
            live_runs.extend(page)
            if len(page) < page_size:
                break
            page_offset += page_size
    else:
        live_runs = list_live_runs_fn() if callable(list_live_runs_fn) else run_state_repository.sync_list_live_runs()
    for run in live_runs:
        if not isinstance(run, dict):
            continue
        run_id = str(run.get("run_id") or "").strip()
        if not run_id:
            continue
        snapshot = serialize_run_snapshot(run_id, run)
        if not history_item_matches(snapshot, workspace_id, status, pack_id):
            continue
        if not _visible(snapshot):
            continue
        summary = summarize_history_item(snapshot)
        summary["source"] = "live"
        items.append(summary)
        token = str(summary.get("run_id") or run_id).strip()
        if token:
            seen_run_ids.add(token)

    with run_history_lock:
        history_items = list(run_history)
    for item in history_items:
        if not isinstance(item, dict):
            continue
        if not history_item_matches(item, workspace_id, status, pack_id):
            continue
        if not _visible(item):
            continue
        run_id = str(item.get("run_id") or "").strip()
        if run_id and run_id in seen_run_ids:
            continue
        summary = summarize_history_item(item)
        summary["source"] = "history"
        items.append(summary)
        if run_id:
            seen_run_ids.add(run_id)

    items.sort(
        key=lambda item: (
            parse_utc_ts(item.get("updated_at")) or parse_utc_ts(item.get("created_at")),
            str(item.get("run_id") or ""),
        ),
        reverse=True,
    )
    total = len(items)
    payload = items[safe_offset : safe_offset + safe_limit]
    next_offset = safe_offset + safe_limit if safe_offset + safe_limit < total else None
    return {
        "items": payload,
        "count": len(payload),
        "total": total,
        "limit": safe_limit,
        "offset": safe_offset,
        "next_offset": next_offset,
    }
