from __future__ import annotations
import json
import sentry_sdk
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, HTTPException, Request
from sse_starlette.sse import EventSourceResponse
from server_modules.auth import (
    allowed_workspace_ids,
    build_workspace_authorization_metadata,
    enforce_workspace_access,
    workspace_access_map,
    workspace_tenant_id,
)

from server_modules.agent_turn import (
    normalize_direct_chat_session_metadata,
    resolve_direct_chat_turn_request,
    resolve_run_start_turn_request,
)
from server_modules.api_contract import (
    ApiAgentTurnRequest,
    ApiAgentTurnResponse,
    ApiRunListResponse,
    ApiSessionRequest,
    ApiSessionResponse,
    ApiThreadTurnCreateRequest,
    ApiThreadListResponse,
    ApiThreadRecord,
    build_turn_chat_body,
    model_to_dict,
    normalize_agent_turn_result,
    normalize_session_record,
    request_body_to_turn_request,
)
from server_modules import turn_ingress_service
from server_modules import session_service
from server_modules import thread_service
from server_modules import run_state_repository
from server_modules import entitlements_service
from server_modules.direct_tool_config_service import run_async_tool_call
from server_modules.direct_chat_stream_response_service import (
    build_agent_turn_stream_response,
    build_direct_chat_stream_response,
)
from server_modules.direct_chat_service import (
    build_direct_chat_execution_services,
    build_direct_chat_event_producer as _service_build_direct_chat_event_producer,
    build_direct_chat_request_meta as _service_build_direct_chat_request_meta,
    direct_chat_actor_key as _service_direct_chat_actor_key,
    direct_chat_request_signature as _service_direct_chat_request_signature,
    direct_chat_session_manager_enabled as _service_direct_chat_session_manager_enabled,
    direct_chat_stream_key as _service_direct_chat_stream_key,
)
from server_modules import direct_chat_stream_runtime_service as chat_stream_runtime_service
from server_modules import direct_chat_stream_state_service as chat_stream_state_service
from server_modules import direct_chat_stream_transport_service as chat_stream_transport_service
from server_modules.heartbeat import HeartbeatScheduler
from server_modules.run_service import (
    build_server_run_creation_services,
    build_server_run_execution_services,
    build_server_run_routing_preview_services,
)
from server_modules.turn_runtime import (
    build_turn_execution_services,
)
from server_modules.runtime_policy import (
    browser_automation_plan_hash_from_pack_inputs,
    build_browser_execution_binding,
)
from server_modules.runtime_state_store import (
    delete_chat_stream_sessions_older_than,
    get_chat_stream_state,
    mark_stale_chat_stream_sessions_interrupted,
    upsert_chat_stream_state,
)
from server_modules import runtime_heartbeat_service
from server_modules import runtime_local_execution_approval_service
from server_modules import runtime_route_registration_service
from server_modules import runtime_run_access_service
from server_modules import runtime_run_detail_service
from server_modules import runtime_run_query_service
from server_modules import runtime_run_resume_service
from server_modules import rust_runtime_kernel_client
from server_modules import runtime_usage_service
from server_modules import runtime_webhook_trigger_service
from server_modules import transcript_events_service

_CHAT_STREAM_LOCK = threading.Lock()
_CHAT_STREAM_BUFFER_LIMIT = 50
_CHAT_STREAM_TTL_SECONDS = 15 * 60
_CHAT_STREAM_STATE_STALE_AFTER_SECONDS = 10 * 60
_CHAT_STREAM_STATE_TTL_SECONDS = 60 * 60
_CHAT_STREAM_SESSIONS: dict[str, dict[str, Any]] = {}
_HEARTBEAT_SCHEDULER_LOCK = threading.Lock()
_HEARTBEAT_SCHEDULER: Optional[HeartbeatScheduler] = None
_WEBHOOK_TRIGGER_LOCK = threading.Lock()
_WEBHOOK_TRIGGERS_LOADED = False
_WEBHOOK_TRIGGERS: dict[str, dict[str, Any]] = {}

# Transitional compatibility shim for tests and older helper wiring.
# Prompt 02 routes execution through `turn_ingress_service.start_turn`.
execute_canonical_agent_turn = turn_ingress_service.start_turn


def _late_server_export(name: str):
    import server as _server

    if name == "ROOT_DIR" and not hasattr(_server, name):
        return str(Path(__file__).resolve().parent.parent)
    return getattr(_server, name)


def _refresh_server_exports():
    import server as _server

    globals().update(_server.__dict__)
    return _server


def _chat_stream_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _chat_stream_now_iso()


def _coerce_dict(value: Any) -> dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


_THINKING_METADATA_ALLOWLIST = {"reasoning_effort", "reasoning_tokens", "supports_reasoning"}
_THINKING_METADATA_BLOCKLIST = {
    "thinking",
    "thinking_text",
    "reasoning",
    "reasoning_text",
    "reasoning_summary",
    "reasoning_output",
    "chain_of_thought",
    "raw_chain_of_thought",
    "internal_reasoning",
    "private_reasoning",
}


def _is_thinking_metadata_key(raw_key: Any) -> bool:
    key = str(raw_key or "").strip().lower()
    if not key:
        return False
    if key in _THINKING_METADATA_ALLOWLIST:
        return False
    if key in _THINKING_METADATA_BLOCKLIST:
        return True
    if "chain_of_thought" in key or "raw_chain_of_thought" in key or "private_reasoning" in key:
        return True
    if key in {"thought", "thoughts"}:
        return True
    if "thinking" in key and not key.endswith("_mode"):
        return True
    return False


def _strip_thinking_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in payload.items():
        if _is_thinking_metadata_key(key):
            continue
        if isinstance(value, dict):
            sanitized[str(key)] = _strip_thinking_metadata(value)
        elif isinstance(value, list):
            sanitized[str(key)] = [
                _strip_thinking_metadata(item)
                if isinstance(item, (dict, list))
                else item
                for item in value
            ]
        else:
            sanitized[str(key)] = value
    return sanitized


def _chat_stream_result_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    metadata = _coerce_dict(payload.get("metadata"))
    nested_result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    nested_metadata = _coerce_dict(nested_result.get("metadata"))
    merged = dict(nested_metadata)
    merged.update(metadata)
    return _strip_thinking_metadata(merged)


def _chat_stream_assistant_status(payload: dict[str, Any]) -> str:
    terminal_error = payload.get("terminal_error") if isinstance(payload.get("terminal_error"), dict) else {}
    terminal_error_body = terminal_error.get("error") if isinstance(terminal_error.get("error"), dict) else {}
    error_code = (
        str(terminal_error_body.get("code") or "").strip()
        or str(payload.get("error") or "").strip()
    )
    error_message = (
        str(terminal_error_body.get("message") or "").strip()
        or str(payload.get("message") or "").strip()
    )
    return "failed" if error_code or error_message else "completed"


def _assistant_turn_content(payload: dict[str, Any]) -> str:
    reply = str(payload.get("reply") or "").strip()
    return reply


def _chat_stream_transcript_events(session: dict[str, Any]) -> list[dict[str, Any]]:
    metadata = _coerce_dict(session.get("metadata"))
    cached_events = metadata.get("transcript_events")
    if isinstance(cached_events, list):
        return transcript_events_service.transcript_events_from_stream_records(cached_events)
    events = session.get("events") if isinstance(session.get("events"), list) else []
    return transcript_events_service.transcript_events_from_stream_records(events)


def _persist_final_direct_chat_assistant_turn(session: dict[str, Any], payload: dict[str, Any]) -> None:
    session_metadata = _coerce_dict(session.get("metadata"))
    assistant_turn = _coerce_dict(session_metadata.get("assistant_turn"))
    tenant_id = str(assistant_turn.get("tenant_id") or "").strip()
    workspace_id = str(assistant_turn.get("workspace_id") or "").strip()
    thread_id = str(assistant_turn.get("thread_id") or "").strip()
    session_id = str(assistant_turn.get("session_id") or "").strip() or None
    request_id = str(assistant_turn.get("request_id") or "").strip() or None
    if not tenant_id or not workspace_id or not thread_id:
        return
    content = _assistant_turn_content(payload)
    if not content:
        return
    result_metadata = _chat_stream_result_metadata(payload)
    trace_id = str(payload.get("trace_id") or result_metadata.get("trace_id") or "").strip() or None
    run_id = str(payload.get("run_id") or "").strip() or None
    if not run_id:
        nested_result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        run_id = str(nested_result.get("run_id") or "").strip() or None
    actor_id = str(result_metadata.get("agent_role") or "master-agent").strip() or "master-agent"
    metadata: dict[str, Any] = {
        "request_id": request_id,
        "result_metadata": result_metadata,
    }
    if trace_id:
        metadata["trace_id"] = trace_id
    transcript_events = _chat_stream_transcript_events(session)
    if transcript_events:
        metadata["transcript_events"] = transcript_events
    run_async_tool_call(
        thread_service.record_assistant_turn(
            thread_id=thread_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            session_id=session_id,
            actor={
                "type": "assistant",
                "id": actor_id,
                "display_name": "Empyralis",
            },
            reply=content,
            status=_chat_stream_assistant_status(payload),
            run_id=run_id,
            active_agent_install_id=str(assistant_turn.get("active_agent_install_id") or "").strip() or None,
            runtime_profile_id=str(assistant_turn.get("runtime_profile_id") or "").strip() or None,
            approvals=list(payload.get("approvals") or []) if isinstance(payload.get("approvals"), list) else [],
            interventions=list(payload.get("interventions") or [])
            if isinstance(payload.get("interventions"), list)
            else [],
            metadata=metadata,
        )
    )


def _workspace_filtered_items(items: list[dict[str, Any]], current_user: Any) -> list[dict[str, Any]]:
    allowed = allowed_workspace_ids(current_user)
    if allowed is None:
        return items
    filtered: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        workspace_id = str(item.get("workspace_id") or "default").strip() or "default"
        if workspace_id in allowed:
            filtered.append(item)
    return filtered


def _workspace_entitlement_payload(
    cache: dict[str, dict[str, Any]],
    workspace_id: str,
) -> dict[str, Any]:
    token = str(workspace_id or "default").strip() or "default"
    payload = cache.get(token)
    if payload is None:
        payload = entitlements_service.workspace_entitlement_payload_for_workspace_id(workspace_id=token)
        cache[token] = payload
    return payload


def _workspace_history_cutoff_ts(
    cache: dict[str, dict[str, Any]],
    workspace_id: str,
) -> Optional[float]:
    history_window_days = _workspace_history_window_days(cache, workspace_id)
    return float(_utc_now().timestamp()) - float(history_window_days * 86400)


def _workspace_history_window_days(
    cache: dict[str, dict[str, Any]],
    workspace_id: str,
) -> int:
    payload = _workspace_entitlement_payload(cache, workspace_id)
    capabilities = payload.get("capabilities") if isinstance(payload.get("capabilities"), dict) else {}
    return max(1, int(capabilities.get("history_window_days") or 30))


def _payload_timestamp_seconds(payload: dict[str, Any], *keys: str) -> Optional[float]:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, datetime):
            normalized = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
            return normalized.timestamp()
        text = str(value or "").strip()
        if not text:
            continue
        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    return None


def _coerce_list_payload(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except Exception:
            return []
        return list(parsed) if isinstance(parsed, list) else []
    return []


def _payload_within_workspace_history_window(
    *,
    payload: dict[str, Any],
    workspace_id: str,
    cache: dict[str, dict[str, Any]],
    timestamp_keys: tuple[str, ...],
) -> bool:
    cutoff_ts = _workspace_history_cutoff_ts(cache, workspace_id)
    event_ts = _payload_timestamp_seconds(payload, *timestamp_keys)
    return cutoff_ts is None or event_ts is None or event_ts >= cutoff_ts


def normalize_thread_turn_record(record: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(record or {})
    decision = thread_service._enforce_thread_record_decision(
        operation="normalize_turn",
        tenant_id=str(payload.get("tenant_id") or "default").strip() or "default",
        workspace_id=str(payload.get("workspace_id") or "default").strip() or "default",
        thread_id=str(payload.get("thread_id") or "").strip(),
        actor_role="member",
        turn_role=str(payload.get("role") or "").strip() or "assistant",
        status=str(payload.get("status") or "").strip() or "",
    )
    thread_service._require_thread_next_action(decision, "normalize_thread_turn_record")
    return {
        "id": str(payload.get("id") or "").strip(),
        "tenant_id": str(payload.get("tenant_id") or "").strip() or None,
        "workspace_id": str(payload.get("workspace_id") or "").strip() or None,
        "thread_id": str(payload.get("thread_id") or "").strip(),
        "session_id": str(payload.get("session_id") or "").strip() or None,
        "request_id": str(payload.get("request_id") or "").strip() or None,
        "role": str(decision.get("normalized_role") or payload.get("role") or "").strip() or "assistant",
        "status": str(decision.get("normalized_status") or payload.get("status") or "").strip() or None,
        "content": str(payload.get("content") or ""),
        "run_id": str(payload.get("run_id") or "").strip() or None,
        "actor": _coerce_dict(payload.get("actor")),
        "approvals": _coerce_list_payload(payload.get("approvals")),
        "interventions": _coerce_list_payload(payload.get("interventions")),
        "metadata": _coerce_dict(payload.get("metadata")),
        "attachments": _coerce_list_payload(_coerce_dict(payload.get("metadata")).get("attachments")),
        "created_at": str(payload.get("created_at") or "").strip() or None,
        "updated_at": str(payload.get("updated_at") or "").strip() or None,
    }


def normalize_thread_record(record: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(record or {})
    turns = payload.get("turns") if isinstance(payload.get("turns"), list) else []
    decision = thread_service._enforce_thread_record_decision(
        operation="normalize_thread",
        tenant_id=str(payload.get("tenant_id") or "default").strip() or "default",
        workspace_id=str(payload.get("workspace_id") or "default").strip() or "default",
        thread_id=str(payload.get("id") or payload.get("thread_id") or "").strip(),
        actor_role="member",
        title=str(payload.get("title") or "").strip() or "New chat",
        status=str(payload.get("status") or "").strip() or "active",
    )
    thread_service._require_thread_next_action(decision, "normalize_thread_record")
    return {
        "id": str(payload.get("id") or "").strip(),
        "tenant_id": str(payload.get("tenant_id") or "").strip() or None,
        "workspace_id": str(payload.get("workspace_id") or "").strip() or None,
        "owner_user_id": str(payload.get("owner_user_id") or "").strip() or None,
        "master_agent_install_id": str(payload.get("master_agent_install_id") or "").strip() or None,
        "channel": str(payload.get("channel") or "").strip() or None,
        "title": str(decision.get("normalized_title") or payload.get("title") or "").strip() or "New chat",
        "status": str(decision.get("normalized_status") or payload.get("status") or "").strip() or None,
        "metadata": _coerce_dict(payload.get("metadata")),
        "created_at": str(payload.get("created_at") or "").strip() or None,
        "updated_at": str(payload.get("updated_at") or "").strip() or None,
        "last_turn_at": str(payload.get("last_turn_at") or "").strip() or None,
        "turns": [normalize_thread_turn_record(item) for item in turns if isinstance(item, dict)],
    }


def _history_filtered_thread_record(
    record: Dict[str, Any],
    *,
    cache: dict[str, dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    payload = normalize_thread_record(record)
    from server_modules import workspace_scope as _ws
    workspace_id = _ws.resolve_workspace(
        payload.get("workspace_id"), payload,
        site="runtime_runs_api:cache_thread_record",
    )
    history_window_days = _workspace_history_window_days(cache, workspace_id)
    decision = thread_service._enforce_thread_record_decision(
        operation="history_filter",
        tenant_id=str(payload.get("tenant_id") or "default").strip() or "default",
        workspace_id=workspace_id,
        thread_id=str(payload.get("id") or "").strip(),
        actor_role="member",
        history_window_days=history_window_days,
    )
    thread_service._require_thread_next_action(decision, "include_history_record")
    if not _payload_within_workspace_history_window(
        payload=payload,
        workspace_id=workspace_id,
        cache=cache,
        timestamp_keys=("last_turn_at", "updated_at", "created_at"),
    ):
        return None
    payload["turns"] = [
        turn
        for turn in (payload.get("turns") or [])
        if isinstance(turn, dict)
        and _payload_within_workspace_history_window(
            payload=turn,
            workspace_id=workspace_id,
            cache=cache,
            timestamp_keys=("created_at", "updated_at"),
        )
    ]
    return payload


def _workspace_id_from_turn_payload(payload: dict[str, Any]) -> str:
    return str(payload.get("workspace_id") or "default").strip() or "default"


def _machine_target_from_turn_payload(payload: dict[str, Any]) -> str | None:
    context_hints = payload.get("context_hints") if isinstance(payload.get("context_hints"), dict) else {}
    metadata = context_hints.get("metadata") if isinstance(context_hints.get("metadata"), dict) else {}
    token = (
        str(payload.get("machine_target") or "").strip()
        or str(metadata.get("machine_id") or "").strip()
        or str(metadata.get("machine_target") or "").strip()
    )
    return token or None


def _stamp_workspace_authorization_on_turn_payload(
    payload: dict[str, Any],
    *,
    current_user: Any,
    minimum_role: str,
) -> dict[str, Any]:
    body = dict(payload or {})
    workspace_id = enforce_workspace_access(
        current_user,
        _workspace_id_from_turn_payload(body),
        tenant_id=body.get("tenant_id"),
        minimum_role=minimum_role,
    )
    tenant_id = workspace_tenant_id(current_user, workspace_id)
    machine_target = _machine_target_from_turn_payload(body)
    context_hints = body.get("context_hints") if isinstance(body.get("context_hints"), dict) else {}
    metadata = context_hints.get("metadata") if isinstance(context_hints.get("metadata"), dict) else {}
    metadata = {
        **metadata,
        **build_workspace_authorization_metadata(
            current_user,
            workspace_id,
            connector_id=str(metadata.get("connector") or "").strip() or None,
            machine_id=machine_target,
        ),
    }
    body["tenant_id"] = tenant_id
    body["workspace_id"] = workspace_id
    body["context_hints"] = {
        **context_hints,
        "metadata": metadata,
    }
    return body


def _chat_stream_metrics_inc(key: str, amount: float = 1) -> None:
    try:
        metrics_fn = _late_server_export("metrics_inc")
    except Exception:
        return


def _looks_like_legacy_run_start_body(payload: dict[str, Any]) -> bool:
    if not isinstance(payload, dict) or not payload:
        return False
    if "actor" in payload or "session_id" in payload or "thread_id" in payload:
        return False
    return any(key in payload for key in ("user_goal", "business_plan", "workflow_id", "engine"))
    try:
        metrics_fn(key, amount)
    except Exception:
        return


_heartbeat_scheduler = lambda: runtime_heartbeat_service.heartbeat_scheduler(
    lock=_HEARTBEAT_SCHEDULER_LOCK,
    scheduler=_HEARTBEAT_SCHEDULER,
)


_run_creation_services = lambda: build_server_run_creation_services(
    late_server_export=_late_server_export,
)


_run_execution_services = lambda: build_server_run_execution_services(
    stamp_request_owner=_stamp_request_owner,
    late_server_export=_late_server_export,
)


_run_routing_preview_services = lambda: build_server_run_routing_preview_services(
    late_server_export=_late_server_export,
)


_get_webhook_triggers_loaded = lambda: _WEBHOOK_TRIGGERS_LOADED


def _set_webhook_triggers_loaded(loaded: bool) -> None:
    global _WEBHOOK_TRIGGERS_LOADED
    _WEBHOOK_TRIGGERS_LOADED = bool(loaded)


_load_webhook_triggers = runtime_webhook_trigger_service.build_load_webhook_triggers_fn(
    _WEBHOOK_TRIGGERS,
    lock=_WEBHOOK_TRIGGER_LOCK,
    get_loaded=_get_webhook_triggers_loaded,
    set_loaded=_set_webhook_triggers_loaded,
    path=lambda: _late_server_export("ORION_WEBHOOK_TRIGGERS_FILE"),
    safe_read_json=lambda: _late_server_export("_safe_read_json"),
)


_persist_webhook_triggers_locked = runtime_webhook_trigger_service.build_persist_webhook_triggers_locked_fn(
    _WEBHOOK_TRIGGERS,
    path=lambda: _late_server_export("ORION_WEBHOOK_TRIGGERS_FILE"),
    safe_write_json=lambda: _late_server_export("_safe_write_json"),
)


_match_webhook_trigger = runtime_webhook_trigger_service.build_match_webhook_trigger_fn(
    _WEBHOOK_TRIGGERS,
    lock=_WEBHOOK_TRIGGER_LOCK,
    load_webhook_triggers_fn=_load_webhook_triggers,
)


def _enforce_run_api_decision(
    *,
    operation: str,
    workspace_id: str,
    tenant_id: str,
    user_role: str,
    body: Optional[dict[str, Any]] = None,
    run_id: Optional[str] = None,
    run_status: Optional[str] = None,
) -> dict[str, Any]:
    payload = {
        "operation": operation,
        "workspace_id": str(workspace_id or "").strip(),
        "tenant_id": str(tenant_id or "").strip(),
        "user_role": str(user_role or "viewer").strip() or "viewer",
        "run_id": str(run_id or "").strip() or None,
        "run_status": str(run_status or "").strip() or None,
        "body": dict(body or {}),
        "workspace_access_denied": False,
        "owner_access_denied": False,
        "kill_switch_active": False,
        "entitlement_blocked": False,
        "budget_exhausted": False,
        "history_window_allowed": True,
    }
    try:
        decision = dict(
            rust_runtime_kernel_client.run_runtime_kernel_enforced(
                "run-api-decision",
                payload,
            )
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        reason = str(exc.reason or "rust_run_api_denied").strip()
        raise HTTPException(status_code=409, detail=f"Rust run-api gate blocked {operation}: {reason}")
    allowed_next_actions = {
        "list_runs": {"list_runs"},
        "get_run": {"get_run", "request_owner_approval"},
        "start_turn": {
            "start_turn",
            "request_local_execution_confirmation",
            "request_browser_execution_confirmation",
        },
        "start_run": {
            "start_run",
            "request_local_execution_confirmation",
            "request_browser_execution_confirmation",
        },
        "stream_chat": {"start_chat_stream"},
        "cancel_run": {"cancel_run"},
        "retry_run": {"retry_run"},
        "resume_run": {"resume_run"},
        "approve_run": {"resolve_run_approval"},
        "webhook_trigger": {"trigger_webhook_run"},
    }.get(operation)
    next_action = str(decision.get("next_action") or "").strip()
    if allowed_next_actions and next_action not in allowed_next_actions:
        raise HTTPException(
            status_code=423,
            detail=(
                "Rust run-api gate returned unexpected next_action for "
                f"{operation}: {next_action or 'missing'}"
            ),
        )
    return decision


def _enforce_session_lifecycle_route_decision(
    *,
    operation: str,
    session_record: dict[str, Any],
    force: bool = False,
) -> dict[str, Any]:
    payload = {
        "operation": str(operation or "").strip(),
        "session_id": str(session_record.get("session_id") or "").strip() or None,
        "status": str(session_record.get("status") or "active").strip() or "active",
        "preset": "standard",
        "turn_count": max(0, int(session_record.get("turn_count") or 0)),
        "age_hours": max(0, int(session_record.get("age_hours") or 0)),
        "idle_seconds": max(0, int(session_record.get("idle_seconds") or 0)),
        "max_age_hours": max(1, int(session_record.get("max_age_hours") or 24)),
        "idle_timeout_seconds": max(1, int(session_record.get("idle_timeout_seconds") or 3600)),
        "force": bool(force),
    }
    try:
        decision = dict(
            rust_runtime_kernel_client.run_runtime_kernel_enforced(
                "session-lifecycle-decision",
                payload,
            )
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        reason = str(exc.reason or "rust_session_lifecycle_denied").strip()
        raise HTTPException(status_code=409, detail=f"Rust session lifecycle gate blocked {operation}: {reason}")
    expected_next_actions = {
        "create": {"create"},
        "close": {"close"},
    }.get(str(operation or "").strip())
    next_action = str(decision.get("next_action") or "").strip()
    if expected_next_actions and next_action not in expected_next_actions:
        raise HTTPException(
            status_code=423,
            detail=(
                "Rust session lifecycle gate returned unexpected next_action for "
                f"{operation}: {next_action or 'missing'}"
            ),
        )
    return decision


_normalize_usage_period = runtime_usage_service.normalize_usage_period


_usage_snapshots_for_user = lambda current_user: runtime_usage_service.usage_snapshots_for_user(
    current_user,
    refresh_server_exports=_refresh_server_exports,
    run_history_lock=RUN_HISTORY_LOCK,
    run_history=RUN_HISTORY,
    runs=runs,
    list_live_runs_fn=run_state_repository.sync_list_live_runs,
    serialize_snapshot=_late_server_export("_serialize_run_snapshot"),
    current_user_is_privileged=_current_user_is_privileged,
    extract_run_owner_user_id=_extract_run_owner_user_id,
    allowed_workspace_ids_fn=allowed_workspace_ids,
    resolve_workspace_tenant_id=workspace_tenant_id,
)


_normalize_chat_stream_cursor = chat_stream_transport_service.normalize_chat_stream_cursor


_chat_stream_state_db_path = lambda: chat_stream_runtime_service.resolve_chat_stream_state_db_path(
    override=globals().get("_CHAT_STREAM_STATE_DB_OVERRIDE"),
    late_server_export=_late_server_export,
    fallback_db_path=__import__(
        "server_modules.runtime_config",
        fromlist=["ORION_RUNTIME_STATE_DB"],
    ).ORION_RUNTIME_STATE_DB,
)


_configured_direct_chat_worker_count = chat_stream_runtime_service.configured_direct_chat_worker_count


ensure_single_worker_direct_chat_stream_runtime = lambda: chat_stream_state_service.ensure_single_worker_runtime(
    configured_worker_count=_configured_direct_chat_worker_count(),
)


_chat_stream_interrupted_final_payload = chat_stream_state_service.chat_stream_interrupted_final_payload
_chat_stream_replay_payload_from_state = chat_stream_state_service.chat_stream_replay_payload_from_state

_chat_stream_request_signature = _service_direct_chat_request_signature
_chat_stream_key = _service_direct_chat_stream_key
_direct_chat_actor_key = _service_direct_chat_actor_key
_direct_chat_session_manager_enabled = lambda: _service_direct_chat_session_manager_enabled(
    globals().get("ORION_DIRECT_CHAT_SESSION_MANAGER")
)

_build_direct_chat_request_meta = _service_build_direct_chat_request_meta

_prune_chat_stream_sessions_locked = lambda now_ts=None: chat_stream_state_service.prune_chat_stream_sessions_locked(
    _CHAT_STREAM_SESSIONS,
    ttl_seconds=_CHAT_STREAM_TTL_SECONDS,
    now_ts=now_ts,
)

_direct_chat_stream_runtime_bindings = chat_stream_runtime_service.build_direct_chat_stream_runtime_bindings(
    _CHAT_STREAM_SESSIONS,
    lock=_CHAT_STREAM_LOCK,
    ensure_single_worker_runtime_fn=ensure_single_worker_direct_chat_stream_runtime,
    chat_stream_state_db_path=lambda: _chat_stream_state_db_path(),
    stale_after_seconds=_CHAT_STREAM_STATE_STALE_AFTER_SECONDS,
    ttl_seconds=_CHAT_STREAM_STATE_TTL_SECONDS,
    state_ttl_seconds=_CHAT_STREAM_STATE_TTL_SECONDS,
    mark_stale_sessions_interrupted=mark_stale_chat_stream_sessions_interrupted,
    delete_sessions_older_than=delete_chat_stream_sessions_older_than,
    metrics_inc=_chat_stream_metrics_inc,
    session_manager_enabled=lambda: _direct_chat_session_manager_enabled(),
    session_manager_factory=lambda: _direct_chat_session_manager(),
    import_module=__import__,
    now_iso=_chat_stream_now_iso,
    replay_payload_from_state=_chat_stream_replay_payload_from_state,
    get_state=get_chat_stream_state,
    upsert_state=upsert_chat_stream_state,
    interrupted_final_payload=_chat_stream_interrupted_final_payload,
    direct_chat_execution_services_builder=build_direct_chat_execution_services,
    chat_stream_key=_chat_stream_key,
    resolve_direct_chat_turn_request=resolve_direct_chat_turn_request,
    chat_stream_request_signature=_chat_stream_request_signature,
    execute_agent_turn_request=lambda **kwargs: turn_ingress_service.start_turn(
        return_result_only=True,
        **kwargs,
    ),
    build_turn_execution_services=build_turn_execution_services,
    run_execution_services=_run_execution_services,
    extract_direct_chat_error_response=lambda *args, **kwargs: _extract_direct_chat_error_response(*args, **kwargs),
    start_chat_stream_producer=lambda *args, **kwargs: _start_chat_stream_producer(*args, **kwargs),
    iter_chat_stream_events=lambda *args, **kwargs: _iter_chat_stream_events(*args, **kwargs),
    prune_sessions_locked=_prune_chat_stream_sessions_locked,
)

_default_chat_stream_session = _direct_chat_stream_runtime_bindings.default_chat_stream_session
_persist_chat_stream_session_state = _direct_chat_stream_runtime_bindings.persist_chat_stream_session_state
_build_chat_stream_replay_session = _direct_chat_stream_runtime_bindings.build_chat_stream_replay_session
_load_replayable_chat_stream_session = _direct_chat_stream_runtime_bindings.load_replayable_chat_stream_session
_direct_chat_session_manager = _direct_chat_stream_runtime_bindings.direct_chat_session_manager
initialize_chat_stream_runtime_state = _direct_chat_stream_runtime_bindings.initialize_chat_stream_runtime_state
_direct_chat_execution_services = _direct_chat_stream_runtime_bindings.direct_chat_execution_services
_direct_chat_stream_response_services = _direct_chat_stream_runtime_bindings.direct_chat_stream_response_services
_get_or_create_chat_stream_session = _direct_chat_stream_runtime_bindings.get_or_create_chat_stream_session

_build_direct_chat_event_producer = lambda *, current_user, body, message, workspace_id, session_key, thread_id, client_request_id, agent_turn_request=None: _service_build_direct_chat_event_producer(
    current_user=current_user,
    body=body,
    message=message,
    workspace_id=workspace_id,
    session_key=session_key,
    thread_id=thread_id,
    client_request_id=client_request_id,
    services=_direct_chat_execution_services(),
    agent_turn_request=agent_turn_request,
)


_chat_stream_payload = chat_stream_transport_service.chat_stream_payload


def _append_chat_stream_event(session: dict[str, Any], event_name: str, payload: dict[str, Any]) -> None:
    transcript_event = transcript_events_service.build_transcript_event(event_name, payload)
    if transcript_event is not None:
        metadata = _coerce_dict(session.get("metadata"))
        metadata["transcript_events"] = transcript_events_service.append_transcript_event(
            metadata.get("transcript_events"),
            transcript_event,
        )
        session["metadata"] = metadata
    chat_stream_runtime_service.append_chat_stream_event(
        session,
        event_name=event_name,
        payload=payload,
        buffer_limit=_CHAT_STREAM_BUFFER_LIMIT,
        persist_session_state=_persist_chat_stream_session_state,
    )
    if event_name != "final" or not isinstance(payload, dict):
        return
    try:
        _persist_final_direct_chat_assistant_turn(session, payload)
    except Exception as exc:
        sentry_sdk.capture_exception(exc)


_complete_chat_stream_session = lambda session: chat_stream_runtime_service.complete_chat_stream_session(
    session,
    persist_session_state=_persist_chat_stream_session_state,
)


_chat_stream_error_payload = chat_stream_transport_service.chat_stream_error_payload


_extract_direct_chat_error_response = chat_stream_transport_service.extract_direct_chat_error_response


_start_chat_stream_producer = lambda session, producer_fn: chat_stream_transport_service.start_chat_stream_producer(
    session,
    producer_fn=producer_fn,
    append_event=_append_chat_stream_event,
    complete_session=_complete_chat_stream_session,
    capture_exception=sentry_sdk.capture_exception,
)


_iter_chat_stream_events = lambda session, last_event_id: chat_stream_transport_service.iter_chat_stream_events(
    session,
    last_event_id=last_event_id,
    normalize_cursor=_normalize_chat_stream_cursor,
)


_can_view_sensitive_run_payload = runtime_run_detail_service.can_view_sensitive_run_payload


_limited_run_context_view = runtime_run_detail_service.limited_run_context_view


_limited_result_data_view = runtime_run_detail_service.limited_result_data_view


_extract_run_owner_user_id = runtime_run_access_service.extract_run_owner_user_id


_current_user_is_privileged = lambda current_user: runtime_run_access_service.current_user_is_privileged(
    current_user,
    admin_user_ids=set(globals().get("ORION_ADMIN_USER_IDS") or []),
    admin_emails=set(globals().get("ORION_ADMIN_EMAILS") or []),
)


_enforce_run_owner_access = lambda current_user, payload: runtime_run_access_service.enforce_run_owner_access(
    current_user,
    payload,
    current_user_is_privileged_fn=_current_user_is_privileged,
    extract_run_owner_user_id_fn=_extract_run_owner_user_id,
)


_stamp_request_owner = runtime_run_access_service.stamp_request_owner


_resolve_local_execution_start_approval = runtime_local_execution_approval_service.build_resolve_local_execution_start_approval_fn(
    get_pending_confirmation=lambda run: _get_pending_confirmation(run),
    approval_correlation_id=lambda *args, **kwargs: _approval_correlation_id(*args, **kwargs),
    parse_utc_ts=lambda value: _parse_utc_ts(value),
    utc_now=_utc_now,
    utc_now_iso=_utc_now_iso,
    set_pending_confirmation=lambda run, pending: _set_pending_confirmation(run, pending),
    emit_log=lambda *args, **kwargs: emit_log(*args, **kwargs),
    append_approval_audit=lambda **kwargs: _append_approval_audit(**kwargs),
    browser_plan_hash_from_inputs=browser_automation_plan_hash_from_pack_inputs,
    clear_pending_confirmation=lambda run: _clear_pending_confirmation(run),
    set_run_status=lambda run_id, status: set_run_status(run_id, status),
    mark_local_execution_tools_approved=lambda metadata: _mark_local_execution_tools_approved(metadata),
    build_browser_execution_binding=build_browser_execution_binding,
    root_dir=lambda: _late_server_export("ROOT_DIR"),
    enqueue_local_companion_run=lambda run_id, **kwargs: _enqueue_local_companion_run(run_id, **kwargs),
)


_run_thread_is_alive = runtime_run_resume_service.build_run_thread_is_alive_fn(
    enumerate_threads=lambda: threading.enumerate(),
)


_schedule_restored_run_resume = runtime_run_resume_service.build_schedule_restored_run_resume_fn(
    run_thread_is_alive_fn=lambda run: _run_thread_is_alive(run),
    utc_now_iso=_utc_now_iso,
    late_server_export=lambda name: _late_server_export(name),
    thread_class=lambda **kwargs: threading.Thread(**kwargs),
)


def register_run_routes(app) -> None:
    import server as _server
    viewer_dependency = getattr(_server, "require_viewer_api_key", _server.require_api_key)
    member_dependency = getattr(_server, "require_member_api_key", _server.require_api_key)

    global _HEARTBEAT_SCHEDULER
    _HEARTBEAT_SCHEDULER = runtime_route_registration_service.register_runtime_run_routes_from_api(
        app,
        import_module=__import__,
        module_globals=globals(),
        server_module=_server,
        heartbeat_lock=_HEARTBEAT_SCHEDULER_LOCK,
        heartbeat_scheduler=_HEARTBEAT_SCHEDULER,
        heartbeat_scheduler_refresher=lambda scheduler: scheduler,
        load_webhook_triggers=_load_webhook_triggers,
        depends=Depends,
        request_class=Request,
        event_source_response_class=EventSourceResponse,
        require_api_key=_server.require_api_key,
        require_admin_api_key=_server.require_admin_api_key,
        refresh_server_exports=_refresh_server_exports,
        match_webhook_trigger_fn=_match_webhook_trigger,
        webhook_triggers=_WEBHOOK_TRIGGERS,
        webhook_trigger_lock=_WEBHOOK_TRIGGER_LOCK,
        persist_webhook_triggers_locked=_persist_webhook_triggers_locked,
        single_agent_mode=_server.ORION_SINGLE_AGENT_MODE,
        runs=_server.runs,
        iter_logs_for_run=_server.iter_logs_for_run,
        get_replay_payload=_server._get_replay_payload,
        execute_run_start_request_via_turn_runtime=turn_ingress_service.start_run_start,
        execute_system_run_start_request_via_turn_runtime=turn_ingress_service.start_system_run_start,
        enforce_run_owner_access=_enforce_run_owner_access,
    )

    @app.post("/turn", dependencies=[Depends(member_dependency)], response_model=ApiAgentTurnResponse)
    async def canonical_turn(
        request: Request,
        body: Optional[dict[str, Any]] = None,
        current_user=Depends(member_dependency),
    ):
        _refresh_server_exports()
        raw_payload = dict(body or {})
        if not raw_payload:
            raw_payload = await request.json()
            if not isinstance(raw_payload, dict):
                raise HTTPException(status_code=400, detail="Invalid turn payload")
        payload = dict(raw_payload)
        payload = _stamp_workspace_authorization_on_turn_payload(
            payload,
            current_user=current_user,
            minimum_role="member",
        )
        turn_workspace_id = str(payload.get("workspace_id") or "default").strip() or "default"
        turn_tenant_id = workspace_tenant_id(current_user, turn_workspace_id)
        run_api_decision = _enforce_run_api_decision(
            operation="start_turn",
            workspace_id=turn_workspace_id,
            tenant_id=turn_tenant_id,
            user_role="member",
            body=payload,
            run_id=str(payload.get("run_id") or payload.get("id") or "").strip() or None,
        )
        payload["rust_run_api_decision"] = {
            "command": "run-api-decision",
            "decision": run_api_decision.get("decision"),
            "next_action": run_api_decision.get("next_action"),
            "reason": run_api_decision.get("reason"),
        }

        try:
            ingress = await turn_ingress_service.start_turn(
                payload=payload,
                compatibility_payload=raw_payload,
                current_user=current_user,
                run_execution_services=_run_execution_services(),
                direct_chat_services=_direct_chat_execution_services(),
                stamp_request_owner_fn=_stamp_request_owner,
                request_signature_fn=_chat_stream_request_signature,
                stream_response_builder=build_agent_turn_stream_response,
                stream_response_services=_direct_chat_stream_response_services(),
                stream_last_event_id=request.headers.get("last-event-id"),
            )
            if not isinstance(ingress.result, dict) or str(ingress.result.get("kind") or "").strip().lower() == "stream":
                return ingress.result
            return normalize_agent_turn_result(ingress.result, turn_request=ingress.turn_request)
        except session_service.SessionScopeViolationError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": exc.code, "message": exc.detail},
            ) from exc

    @app.get("/runs", dependencies=[Depends(viewer_dependency)], response_model=ApiRunListResponse)
    async def list_runs(
        limit: int = 50,
        offset: int = 0,
        workspace_id: Optional[str] = None,
        status: Optional[str] = None,
        pack_id: Optional[str] = None,
        current_user=Depends(viewer_dependency),
    ):
        _refresh_server_exports()
        allowed_workspaces = allowed_workspace_ids(current_user)
        requested_workspace_id = (
            enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
            if workspace_id
            else None
        )
        if requested_workspace_id:
            requested_workspace_token = str(requested_workspace_id).strip() or "default"
            _enforce_run_api_decision(
                operation="list_runs",
                workspace_id=requested_workspace_token,
                tenant_id=workspace_tenant_id(current_user, requested_workspace_token),
                user_role="viewer",
                body={
                    "limit": max(1, min(int(limit or 50), 200)),
                    "offset": max(0, int(offset or 0)),
                    "status": str(status or "").strip() or None,
                    "pack_id": str(pack_id or "").strip() or None,
                },
            )
        base_history_item_matches = _late_server_export("_history_item_matches")
        entitlement_cache: dict[str, dict[str, Any]] = {}

        def _authorized_history_item_matches(item: Any, requested_workspace: Any, requested_status: Any, requested_pack_id: Any) -> bool:
            if not base_history_item_matches(item, requested_workspace, requested_status, requested_pack_id):
                return False
            if not isinstance(item, dict):
                return False
            run_workspace_id = str(item.get("workspace_id") or "default").strip() or "default"
            if allowed_workspaces is not None and run_workspace_id not in allowed_workspaces:
                return False
            return _payload_within_workspace_history_window(
                payload=item,
                workspace_id=run_workspace_id,
                cache=entitlement_cache,
                timestamp_keys=("updated_at", "created_at", "completed_at"),
            )

        payload = runtime_run_query_service.build_run_list_response(
            limit=limit,
            offset=offset,
            workspace_id=requested_workspace_id,
            status=status,
            pack_id=pack_id,
            current_user=current_user,
            runs=_late_server_export("runs"),
            list_live_runs_fn=run_state_repository.sync_list_live_runs,
            list_live_runs_page_fn=lambda page_limit, page_offset, page_workspace_id, page_states: run_state_repository.sync_list_live_runs_page(
                limit=page_limit,
                offset=page_offset,
                workspace_id=page_workspace_id,
                states=page_states,
            ),
            run_history_lock=_late_server_export("RUN_HISTORY_LOCK"),
            run_history=_late_server_export("RUN_HISTORY"),
            serialize_run_snapshot=_late_server_export("_serialize_run_snapshot"),
            history_item_matches=_authorized_history_item_matches,
            current_user_is_privileged=_current_user_is_privileged,
            extract_run_owner_user_id=_extract_run_owner_user_id,
            summarize_history_item=_late_server_export("_summarize_history_item"),
            parse_utc_ts=_late_server_export("_parse_utc_ts"),
        )
        return payload

    @app.post("/sessions", dependencies=[Depends(member_dependency)], response_model=ApiSessionResponse)
    async def create_runtime_session(
        body: ApiSessionRequest,
        current_user=Depends(member_dependency),
    ):
        payload = body.model_dump() if hasattr(body, "model_dump") else body.dict()
        workspace_id = enforce_workspace_access(
            current_user,
            payload.get("workspace_id"),
            tenant_id=payload.get("tenant_id"),
            minimum_role="member",
        )
        tenant_id = workspace_tenant_id(current_user, workspace_id)
        actor = _coerce_dict(payload.get("actor"))
        if not str(actor.get("id") or "").strip():
            actor["id"] = str((current_user or {}).get("user_id") or (current_user or {}).get("email") or "anonymous").strip()
        if not str(actor.get("display_name") or "").strip():
            actor["display_name"] = str((current_user or {}).get("email") or actor.get("id") or "").strip()
        requested_session_id = str(payload.get("session_id") or "").strip() or None
        existing_session_record = (
            await session_service.get_session(requested_session_id)
            if requested_session_id
            else None
        )
        _enforce_session_lifecycle_route_decision(
            operation="create",
            session_record=existing_session_record
            if isinstance(existing_session_record, dict)
            else {
                "session_id": requested_session_id or "",
                "status": "new",
            },
        )
        metadata = normalize_direct_chat_session_metadata(
            current_user=current_user,
            workspace_id=workspace_id,
            channel=str(payload.get("channel") or "web").strip() or "web",
            metadata=_coerce_dict(payload.get("metadata")),
        )
        session_id = await session_service.create_session(
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            actor=actor,
            channel=str(payload.get("channel") or "web").strip() or "web",
            metadata=metadata,
            session_id=requested_session_id,
        )
        record = await session_service.get_session(session_id) or {
            "session_id": session_id,
            "workspace_id": workspace_id,
            "tenant_id": tenant_id,
            "channel": str(payload.get("channel") or "web").strip() or "web",
            "actor": actor,
            "metadata": metadata,
            "status": "active",
        }
        return normalize_session_record(record)

    @app.get("/sessions/{session_id}", dependencies=[Depends(viewer_dependency)], response_model=ApiSessionResponse)
    async def get_runtime_session(
        session_id: str,
        current_user=Depends(viewer_dependency),
    ):
        record = await session_service.get_session(session_id)
        if not isinstance(record, dict):
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Session not found.")
        enforce_workspace_access(
            current_user,
            record.get("workspace_id"),
            tenant_id=record.get("tenant_id"),
            minimum_role="viewer",
        )
        return normalize_session_record(record)

    @app.delete("/sessions/{session_id}", dependencies=[Depends(member_dependency)])
    async def delete_runtime_session(
        session_id: str,
        current_user=Depends(member_dependency),
    ):
        record = await session_service.get_session(session_id)
        _enforce_session_lifecycle_route_decision(
            operation="close",
            session_record=record
            if isinstance(record, dict)
            else {
                "session_id": str(session_id or "").strip(),
                "status": "active",
            },
        )
        if isinstance(record, dict):
            enforce_workspace_access(
                current_user,
                record.get("workspace_id"),
                tenant_id=record.get("tenant_id"),
                minimum_role="member",
            )
        await session_service.terminate_session(session_id)
        return {"ok": True, "session_id": str(session_id or "").strip()}

    @app.get("/threads", dependencies=[Depends(viewer_dependency)], response_model=ApiThreadListResponse)
    async def list_threads(
        workspace_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        include_turns: bool = False,
        limit: int = 50,
        current_user=Depends(viewer_dependency),
    ):
        requested_workspace_id = enforce_workspace_access(
            current_user,
            workspace_id,
            minimum_role="viewer",
        )
        tenant_id = workspace_tenant_id(current_user, requested_workspace_id)
        owner_user_id = None if _current_user_is_privileged(current_user) else str(current_user.get("user_id") or "").strip() or None
        records = await thread_service.list_threads(
            workspace_id=requested_workspace_id,
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            active_agent_install_id=str(agent_id or "").strip() or None,
            include_turns=bool(include_turns),
            limit=max(1, min(int(limit or 50), 200)),
        )
        entitlement_cache: dict[str, dict[str, Any]] = {}
        items = [
            normalized
            for item in records
            if isinstance(item, dict)
            for normalized in [_history_filtered_thread_record(item, cache=entitlement_cache)]
            if isinstance(normalized, dict)
        ]
        return {
            "items": items,
            "count": len(items),
            "workspace_id": requested_workspace_id,
            "tenant_id": tenant_id,
        }

    @app.get("/threads/{thread_id}", dependencies=[Depends(viewer_dependency)], response_model=ApiThreadRecord)
    async def get_thread(
        thread_id: str,
        current_user=Depends(viewer_dependency),
    ):
        from fastapi import HTTPException

        record = None
        for workspace_id, access in workspace_access_map(current_user).items():
            tenant_id = str((access or {}).get("tenant_id") or "").strip()
            if not tenant_id:
                continue
            record = await thread_service.get_thread(
                thread_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                include_turns=True,
            )
            if isinstance(record, dict):
                break
        if not isinstance(record, dict):
            if str(thread_id or "").strip() in ("primary", "sage-main"):
                for workspace_id, access in workspace_access_map(current_user).items():
                    tenant_id = str((access or {}).get("tenant_id") or "").strip()
                    if not tenant_id:
                        continue
                    now_iso = _chat_stream_now_iso()
                    return {
                        "id": "primary",
                        "tenant_id": tenant_id,
                        "workspace_id": workspace_id,
                        "owner_user_id": None if _current_user_is_privileged(current_user) else str(current_user.get("user_id") or "").strip() or None,
                        "master_agent_install_id": None,
                        "channel": "web",
                        "title": "New chat",
                        "status": "active",
                        "metadata": {"source": "empty_primary_thread"},
                        "created_at": now_iso,
                        "updated_at": now_iso,
                        "last_turn_at": None,
                        "turns": [],
                    }
            raise HTTPException(status_code=404, detail="Thread not found.")
        enforce_workspace_access(
            current_user,
            record.get("workspace_id"),
            tenant_id=record.get("tenant_id"),
            minimum_role="viewer",
        )
        if not _current_user_is_privileged(current_user):
            request_user_id = str(current_user.get("user_id") or "").strip()
            owner_user_id = str(record.get("owner_user_id") or "").strip()
            if owner_user_id and request_user_id and owner_user_id != request_user_id:
                raise HTTPException(status_code=404, detail="Thread not found.")
        normalized = _history_filtered_thread_record(record, cache={})
        if not isinstance(normalized, dict):
            raise HTTPException(status_code=404, detail="Thread not found.")
        return normalized

    @app.post("/threads/{thread_id}/turns", dependencies=[Depends(member_dependency)], response_model=ApiThreadRecord)
    async def create_thread_turn(
        thread_id: str,
        body: ApiThreadTurnCreateRequest,
        current_user=Depends(member_dependency),
    ):
        payload = body.model_dump() if hasattr(body, "model_dump") else body.dict()
        workspace_id = enforce_workspace_access(
            current_user,
            payload.get("workspace_id"),
            tenant_id=payload.get("tenant_id"),
            minimum_role="member",
        )
        tenant_id = workspace_tenant_id(current_user, workspace_id)
        actor = _coerce_dict(payload.get("actor"))
        if not str(actor.get("id") or "").strip():
            actor["id"] = str((current_user or {}).get("user_id") or (current_user or {}).get("email") or "anonymous").strip()
        if not str(actor.get("display_name") or "").strip():
            actor["display_name"] = str((current_user or {}).get("email") or actor.get("id") or "").strip()
        channel = str(payload.get("channel") or "web").strip() or "web"
        request_id = (
            str(payload.get("client_request_id") or "").strip()
            or str(payload.get("request_id") or "").strip()
            or None
        )
        metadata = _coerce_dict(payload.get("metadata"))
        if request_id:
            metadata.setdefault("client_request_id", request_id)
            metadata.setdefault("request_id", request_id)
        owner_user_id = (
            None
            if _current_user_is_privileged(current_user)
            else str(current_user.get("user_id") or "").strip() or None
        )
        resolved_thread_id = str(thread_id or "").strip()
        await thread_service.ensure_master_thread(
            thread_id=resolved_thread_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            owner_user_id=owner_user_id,
            channel=channel,
            title=thread_service.build_default_thread_title(str(payload.get("content") or "")),
            metadata={
                "source": "runtime_runs_api",
                "session_id": str(payload.get("session_id") or "").strip() or None,
            },
        )
        await thread_service.record_user_turn(
            thread_id=resolved_thread_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            session_id=str(payload.get("session_id") or "").strip() or None,
            actor=actor,
            content=str(payload.get("content") or ""),
            runtime_profile_id=str(payload.get("runtime_profile_id") or "").strip() or None,
            metadata=metadata,
        )
        record = await thread_service.get_thread(
            resolved_thread_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            include_turns=True,
        )
        if not isinstance(record, dict):
            raise HTTPException(status_code=404, detail="Thread not found.")
        normalized = _history_filtered_thread_record(record, cache={})
        if not isinstance(normalized, dict):
            raise HTTPException(status_code=404, detail="Thread not found.")
        return normalized
