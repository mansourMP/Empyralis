from __future__ import annotations

from typing import Any, Callable

from fastapi import HTTPException
from server_modules import rust_runtime_kernel_client


def build_runs_history_callbacks(
    *,
    refresh_server_exports: Callable[[], Any],
    run_history_lock: Any,
    run_history: list[Any],
    history_item_matches: Callable[[Any, Any, Any, Any], bool],
    current_user_is_privileged: Callable[[Any], bool],
    extract_run_owner_user_id: Callable[[Any], str],
    enforce_workspace_access: Callable[..., str] | None,
    resolve_workspace_tenant_id: Callable[[str], str] | None,
    normalize_run_id_token: Callable[[Any], str | None],
    summarize_history_item: Callable[[Any], dict[str, Any]],
) -> dict[str, Any]:
    return {
        "refresh_server_exports": refresh_server_exports,
        "run_history_lock": run_history_lock,
        "run_history": run_history,
        "history_item_matches": history_item_matches,
        "current_user_is_privileged": current_user_is_privileged,
        "extract_run_owner_user_id": extract_run_owner_user_id,
        "enforce_workspace_access": enforce_workspace_access,
        "resolve_workspace_tenant_id": resolve_workspace_tenant_id,
        "normalize_run_id_token": normalize_run_id_token,
        "summarize_history_item": summarize_history_item,
    }


def _history_user_role(current_user: Any) -> str:
    if isinstance(current_user, dict):
        if str(current_user.get("auth_type") or "").strip().lower() == "api_key":
            return "system"
        role = str(current_user.get("workspace_role") or current_user.get("role") or "viewer").strip()
        return role or "viewer"
    return "viewer"


def _enforce_runs_history_list_decision(
    *,
    resolved_workspace_id: str,
    current_user: Any,
    limit: int,
    status: str | None,
    pack_id: str | None,
    resolve_workspace_tenant_id: Callable[[str], str] | None,
) -> dict[str, Any]:
    if not callable(resolve_workspace_tenant_id):
        return {}
    tenant_id = str(resolve_workspace_tenant_id(resolved_workspace_id) or "").strip() or "default"
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "run-api-decision",
            {
                "operation": "list_runs",
                "workspace_id": str(resolved_workspace_id or "").strip() or "default",
                "tenant_id": tenant_id,
                "user_role": _history_user_role(current_user),
                "workspace_access_denied": False,
                "owner_access_denied": False,
                "kill_switch_active": False,
                "history_window_allowed": True,
                "body": {
                    "user_id": str((current_user or {}).get("user_id") or "").strip() or "user",
                    "limit": max(1, min(int(limit or 30), 200)),
                    "status": str(status or "").strip() or None,
                    "pack_id": str(pack_id or "").strip() or None,
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


def build_runs_history_payload(
    *,
    limit: int,
    workspace_id: str | None,
    status: str | None,
    pack_id: str | None,
    current_user: Any,
    refresh_server_exports: Callable[[], Any],
    run_history_lock: Any,
    run_history: list[Any],
    history_item_matches: Callable[[Any, Any, Any, Any], bool],
    current_user_is_privileged: Callable[[Any], bool],
    extract_run_owner_user_id: Callable[[Any], str],
    enforce_workspace_access: Callable[..., str] | None,
    resolve_workspace_tenant_id: Callable[[str], str] | None,
    normalize_run_id_token: Callable[[Any], str | None],
    summarize_history_item: Callable[[Any], dict[str, Any]],
) -> dict[str, Any]:
    refresh_server_exports()
    safe_limit = max(1, min(limit, 200))
    resolved_workspace_id = workspace_id
    if workspace_id is not None and callable(enforce_workspace_access):
        resolved_workspace_id = enforce_workspace_access(
            current_user,
            workspace_id,
            minimum_role="viewer",
        )
    if resolved_workspace_id is not None:
        _enforce_runs_history_list_decision(
            resolved_workspace_id=str(resolved_workspace_id or "").strip() or "default",
            current_user=current_user,
            limit=safe_limit,
            status=status,
            pack_id=pack_id,
            resolve_workspace_tenant_id=resolve_workspace_tenant_id,
        )
    with run_history_lock:
        items = list(run_history)
    filtered = [item for item in items if history_item_matches(item, resolved_workspace_id, status, pack_id)]
    if not current_user_is_privileged(current_user):
        request_user_id = str(current_user.get("user_id") or "").strip()
        if not request_user_id:
            raise HTTPException(status_code=401, detail="Authenticated user id is required.")
        filtered = [item for item in filtered if extract_run_owner_user_id(item) == request_user_id]
    child_counts: dict[str, int] = {}
    for item in filtered:
        parent_run_id = normalize_run_id_token(item.get("parent_run_id"))
        if parent_run_id:
            child_counts[parent_run_id] = child_counts.get(parent_run_id, 0) + 1
    payload = []
    for item in filtered[:safe_limit]:
        summary = summarize_history_item(item)
        run_id_value = str(summary.get("run_id") or "").strip()
        summary["child_run_count"] = child_counts.get(run_id_value, 0)
        payload.append(summary)
    return {
        "items": payload,
        "count": len(payload),
        "total": len(filtered),
    }
