from __future__ import annotations

from typing import Any, Callable

from fastapi import HTTPException
from server_modules import run_state_repository, rust_runtime_kernel_client
from server_modules import usage_accounting_service


def normalize_usage_period(period: Any) -> str:
    value = str(period or "all").strip().lower()
    return value if value in {"day", "week", "month", "all"} else "all"


def _usage_user_role(current_user: Any) -> str:
    if isinstance(current_user, dict):
        if str(current_user.get("auth_type") or "").strip().lower() == "api_key":
            return "system"
        role = str(current_user.get("workspace_role") or current_user.get("role") or "viewer").strip()
        return role or "viewer"
    return "viewer"


def _enforce_usage_workspace_list_decision(
    *,
    current_user: Any,
    workspace_id: str,
    resolve_workspace_tenant_id: Callable[[Any, str], str] | Callable[[str], str] | None,
) -> dict[str, Any]:
    if not callable(resolve_workspace_tenant_id):
        return {}
    try:
        tenant_id = resolve_workspace_tenant_id(current_user, workspace_id)
    except TypeError:
        tenant_id = resolve_workspace_tenant_id(workspace_id)
    tenant_token = str(tenant_id or "").strip() or "default"
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "run-api-decision",
            {
                "operation": "list_runs",
                "workspace_id": str(workspace_id or "").strip() or "default",
                "tenant_id": tenant_token,
                "user_role": _usage_user_role(current_user),
                "workspace_access_denied": False,
                "owner_access_denied": False,
                "kill_switch_active": False,
                "history_window_allowed": True,
                "body": {
                    "user_id": str((current_user or {}).get("user_id") or "").strip() or "user",
                    "usage_requested": True,
                },
            },
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise HTTPException(status_code=409, detail=f"Rust run-api gate blocked list_runs: {exc.reason}") from exc
    next_action = str(decision.get("next_action") or "").strip()
    if next_action != "list_runs":
        raise HTTPException(
            status_code=423,
            detail=f"Rust run-api gate returned unexpected next_action for list_runs: {next_action or 'missing'}",
        )
    return dict(decision)


def usage_snapshots_for_user(
    current_user: Any,
    *,
    refresh_server_exports: Callable[[], Any],
    run_history_lock: Any,
    run_history: list[Any],
    runs: dict[str, Any],
    list_live_runs_fn: Callable[[], list[dict[str, Any]]] | None = None,
    serialize_snapshot: Callable[[str, Any], dict[str, Any]],
    current_user_is_privileged: Callable[[Any], bool],
    extract_run_owner_user_id: Callable[[Any], str],
    allowed_workspace_ids_fn: Callable[[Any], set[str] | None] | None = None,
    resolve_workspace_tenant_id: Callable[[Any, str], str] | Callable[[str], str] | None = None,
) -> list[dict[str, Any]]:
    refresh_server_exports()
    allowed_workspace_ids = allowed_workspace_ids_fn(current_user) if callable(allowed_workspace_ids_fn) else None
    if allowed_workspace_ids:
        for workspace_id in sorted(str(item or "").strip() for item in allowed_workspace_ids if str(item or "").strip()):
            _enforce_usage_workspace_list_decision(
                current_user=current_user,
                workspace_id=workspace_id,
                resolve_workspace_tenant_id=resolve_workspace_tenant_id,
            )
    with run_history_lock:
        archived_items = list(run_history)
    combined: dict[str, dict[str, Any]] = {}
    for item in archived_items:
        if isinstance(item, dict):
            run_id = str(item.get("run_id") or "").strip()
            if run_id:
                combined[run_id] = item

    live_run_items: list[tuple[str, Any]] = []
    live_runs = list_live_runs_fn() if callable(list_live_runs_fn) else run_state_repository.sync_list_live_runs()
    if live_runs:
        live_run_items.extend(
            (str(run.get("run_id") or "").strip(), run)
            for run in live_runs
            if isinstance(run, dict)
        )
    if isinstance(runs, dict):
        live_run_items.extend((str(run_id or "").strip(), run) for run_id, run in runs.items())
    for run_id, run in live_run_items:
        if not isinstance(run, dict):
            continue
        if not isinstance(run.get("usage_masked"), dict) and not isinstance(run.get("usage_accounting"), dict):
            continue
        if not run_id:
            continue
        try:
            snapshot = serialize_snapshot(run_id, run)
        except Exception:
            continue
        snapshot_run_id = str(snapshot.get("run_id") or run_id).strip()
        if snapshot_run_id:
            combined[snapshot_run_id] = snapshot

    items = list(combined.values())
    if current_user_is_privileged(current_user):
        return items
    request_user_id = str(current_user.get("user_id") or "").strip()
    if not request_user_id:
        raise HTTPException(status_code=401, detail="Authenticated user id is required.")
    return [item for item in items if extract_run_owner_user_id(item) == request_user_id]


def usage_summary_payload(
    *,
    period: Any,
    current_user: Any,
    usage_snapshots_for_user_fn: Callable[..., list[dict[str, Any]]],
    aggregate_usage_summary_fn: Callable[..., Any],
) -> Any:
    snapshots = usage_snapshots_for_user_fn(current_user)
    return aggregate_usage_summary_fn(snapshots, period=normalize_usage_period(period))


def usage_runs_payload(
    *,
    limit: int,
    offset: int,
    period: Any,
    current_user: Any,
    usage_snapshots_for_user_fn: Callable[..., list[dict[str, Any]]],
    list_usage_runs_fn: Callable[..., Any],
) -> Any:
    snapshots = usage_snapshots_for_user_fn(current_user)
    return list_usage_runs_fn(
        snapshots,
        period=normalize_usage_period(period),
        limit=limit,
        offset=offset,
    )


def summarize_provider_model_cost_rows(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return usage_accounting_service.summarize_provider_model_cost_rows(list(rows or []))
