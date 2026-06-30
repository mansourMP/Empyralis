from __future__ import annotations

import fnmatch
import inspect
import time
from typing import Any, Callable, Optional

from fastapi import HTTPException

from server_modules import rust_runtime_kernel_client


def _enforce_run_trigger_decision(*, expected_next_action: str | None = None, **payload: Any) -> dict[str, Any]:
    operation = str(payload.get("operation") or "run_trigger").strip() or "run_trigger"
    try:
        decision = rust_runtime_kernel_client.run_trigger_decision(**payload)
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raw = exc.args[0] if exc.args and isinstance(exc.args[0], dict) else {}
        raise HTTPException(
            status_code=423,
            detail={
                "error": "rust_run_trigger_denied",
                "operation": operation,
                "reason": raw.get("reason") or str(exc),
                "decision": raw.get("decision") or "block",
            },
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "rust_run_trigger_unavailable",
                "operation": operation,
                "reason": str(exc),
            },
        ) from exc
    if not isinstance(decision, dict):
        raise HTTPException(
            status_code=503,
            detail={
                "error": "rust_run_trigger_invalid_response",
                "operation": operation,
            },
        )
    normalized_decision = str(decision.get("decision") or "").strip().lower()
    if bool(decision.get("ok")) is False or normalized_decision in {"block", "require_approval"}:
        raise HTTPException(
            status_code=423,
            detail={
                "error": "rust_run_trigger_denied",
                "operation": operation,
                "reason": decision.get("reason") or "run_trigger_denied",
                "decision": normalized_decision or "block",
            },
        )
    next_action = str(decision.get("next_action") or "").strip()
    if expected_next_action and next_action != expected_next_action:
        raise HTTPException(
            status_code=423,
            detail={
                "error": "rust_run_trigger_denied",
                "operation": operation,
                "reason": f"unexpected_next_action:{next_action or 'missing'}",
                "decision": normalized_decision or "allow",
            },
        )
    return decision


def _resolve(value_or_factory: Any) -> Any:
    if not callable(value_or_factory):
        return value_or_factory
    try:
        signature = inspect.signature(value_or_factory)
    except (TypeError, ValueError):
        return value_or_factory
    required = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.default is inspect._empty
        and parameter.kind
        in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
    ]
    if required:
        return value_or_factory
    return value_or_factory()


def build_load_webhook_triggers_fn(
    triggers: dict[str, dict[str, Any]],
    *,
    lock: Any,
    get_loaded: Callable[[], bool],
    set_loaded: Callable[[bool], None],
    path: Any,
    safe_read_json: Callable[[Any, dict[str, Any]], Any],
) -> Callable[[], None]:
    def _load() -> None:
        loaded = load_webhook_triggers(
            triggers,
            lock=lock,
            loaded=get_loaded(),
            path=_resolve(path),
            safe_read_json=_resolve(safe_read_json),
        )
        set_loaded(loaded)

    return _load


def load_webhook_triggers(
    triggers: dict[str, dict[str, Any]],
    *,
    lock: Any,
    loaded: bool,
    path: Any,
    safe_read_json: Callable[[Any, dict[str, Any]], Any],
) -> bool:
    with lock:
        if loaded:
            return loaded
        payload = safe_read_json(path, {"version": 1, "items": []})
        items = payload.get("items") if isinstance(payload, dict) else []
        triggers.clear()
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                trigger_id = str(item.get("id") or "").strip()
                if not trigger_id:
                    continue
                triggers[trigger_id] = dict(item)
        return True


def persist_webhook_triggers_locked(
    triggers: dict[str, dict[str, Any]],
    *,
    path: Any,
    safe_write_json: Callable[[Any, dict[str, Any]], None],
    now_ts: Optional[float] = None,
) -> None:
    safe_write_json(
        path,
        {
            "version": 1,
            "updated_at": float(now_ts or time.time()),
            "items": list(triggers.values()),
        },
    )


def match_webhook_trigger(
    triggers: dict[str, dict[str, Any]],
    *,
    lock: Any,
    workspace_id: str,
    request_url: str,
) -> Optional[dict[str, Any]]:
    normalized_workspace_id = str(workspace_id or "default").strip() or "default"
    with lock:
        for item in triggers.values():
            if not isinstance(item, dict) or not bool(item.get("enabled", True)):
                continue
            if str(item.get("workspace_id") or "default").strip() != normalized_workspace_id:
                continue
            pattern = str(item.get("url_pattern") or "").strip()
            if not pattern or fnmatch.fnmatch(request_url, pattern) or fnmatch.fnmatch(
                f"/webhooks/ingest/{normalized_workspace_id}",
                pattern,
            ):
                return dict(item)
    return None


def build_persist_webhook_triggers_locked_fn(
    triggers: dict[str, dict[str, Any]],
    *,
    path: Any,
    safe_write_json: Callable[[Any, dict[str, Any]], None],
) -> Callable[[], None]:
    return lambda: persist_webhook_triggers_locked(
        triggers,
        path=_resolve(path),
        safe_write_json=_resolve(safe_write_json),
    )


def build_match_webhook_trigger_fn(
    triggers: dict[str, dict[str, Any]],
    *,
    lock: Any,
    load_webhook_triggers_fn: Callable[[], None],
) -> Callable[[str, str], Optional[dict[str, Any]]]:
    def _match(workspace_id: str, request_url: str) -> Optional[dict[str, Any]]:
        load_webhook_triggers_fn()
        return match_webhook_trigger(
            triggers,
            lock=lock,
            workspace_id=workspace_id,
            request_url=request_url,
        )

    return _match


def build_webhook_trigger(
    *,
    trigger_id: str,
    workspace_id: str,
    url_pattern: str,
    workflow_id: str,
    user_goal: Any,
    metadata: Any,
    enabled: Any,
    now_ts: Optional[float] = None,
) -> dict[str, Any]:
    return {
        "id": str(trigger_id).strip(),
        "workspace_id": str(workspace_id or "default").strip() or "default",
        "url_pattern": str(url_pattern or "").strip(),
        "workflow_id": str(workflow_id or "").strip(),
        "user_goal": str(user_goal or "").strip() or None,
        "metadata": metadata if isinstance(metadata, dict) else {},
        "enabled": bool(enabled),
        "created_at": float(now_ts or time.time()),
    }


def register_webhook_trigger_payload(
    body: Any,
    *,
    uuid_factory: Callable[[], Any],
    build_webhook_trigger_fn: Callable[..., dict[str, Any]],
    triggers: dict[str, dict[str, Any]],
    lock: Any,
    persist_webhook_triggers_locked: Callable[[], None],
) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Invalid webhook trigger payload.")
    workspace_id = str(body.get("workspace_id") or "default").strip() or "default"
    url_pattern = str(body.get("url_pattern") or "").strip()
    workflow_id = str(body.get("workflow_id") or "").strip()
    if not url_pattern:
        raise HTTPException(status_code=400, detail="url_pattern is required.")
    if not workflow_id:
        raise HTTPException(status_code=400, detail="workflow_id is required.")
    trigger_id = str(uuid_factory()).strip()
    _enforce_run_trigger_decision(
        expected_next_action="register_webhook_trigger",
        operation="register_webhook",
        workspace_id=workspace_id,
        trigger_id=trigger_id,
        actor_role="system",
        url_pattern=url_pattern,
        workflow_id=workflow_id,
        trigger_enabled=bool(body.get("enabled", True)),
    )
    trigger = build_webhook_trigger_fn(
        trigger_id=trigger_id,
        workspace_id=workspace_id,
        url_pattern=url_pattern,
        workflow_id=workflow_id,
        user_goal=body.get("user_goal"),
        metadata=body.get("metadata"),
        enabled=body.get("enabled", True),
    )
    with lock:
        triggers[trigger_id] = trigger
        persist_webhook_triggers_locked()
    return {"ok": True, **trigger}


async def ingest_webhook_payload(
    *,
    workspace_id: str,
    request_url: str,
    payload: Any,
    current_user: Any,
    match_webhook_trigger_fn: Callable[[str, str], Optional[dict[str, Any]]],
    run_start_request_class: Callable[..., Any],
    execute_run_start_request_via_turn_runtime: Callable[..., Any],
    stamp_request_owner_fn: Callable[..., Any],
    run_execution_services: Callable[[], Any],
) -> dict[str, Any]:
    normalized_workspace_id = str(workspace_id or "default").strip() or "default"
    matched_trigger = match_webhook_trigger_fn(normalized_workspace_id, request_url)
    if not isinstance(matched_trigger, dict):
        raise HTTPException(status_code=404, detail="No webhook trigger matched this request.")
    workflow_id = str(matched_trigger.get("workflow_id") or "").strip()
    user_goal = str(matched_trigger.get("user_goal") or "").strip() or f"Handle webhook event for workflow '{workflow_id}'."
    _enforce_run_trigger_decision(
        expected_next_action="start_run",
        operation="ingest_webhook",
        workspace_id=normalized_workspace_id,
        trigger_id=str(matched_trigger.get("id") or "").strip() or None,
        actor_role="system",
        trigger_matched=True,
        trigger_enabled=bool(matched_trigger.get("enabled", True)),
        workflow_id=workflow_id,
        request_url=str(request_url),
        payload_present=payload is not None,
    )
    request_payload = run_start_request_class(
        engine="orion",
        workflow_id=workflow_id or None,
        workspace_id=normalized_workspace_id,
        user_goal=user_goal,
        metadata={
            "source": "webhook",
            "webhook_trigger_id": str(matched_trigger.get("id") or "").strip() or None,
            "webhook_url_pattern": str(matched_trigger.get("url_pattern") or "").strip(),
            "webhook_request_url": str(request_url),
            "webhook_payload": payload,
            **(matched_trigger.get("metadata") if isinstance(matched_trigger.get("metadata"), dict) else {}),
        },
    )
    result = await execute_run_start_request_via_turn_runtime(
        request_payload,
        current_user=current_user,
        stamp_request_owner_fn=stamp_request_owner_fn,
        services=run_execution_services(),
    )
    return {
        "ok": True,
        "run_id": str((result or {}).get("run_id") or "").strip() or None,
        "trigger_id": str(matched_trigger.get("id") or "").strip() or None,
    }
