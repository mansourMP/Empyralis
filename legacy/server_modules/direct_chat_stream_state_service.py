from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional

from server_modules import error_response_service
from server_modules.direct_chat_intervention_service import build_intervention
from server_modules.error_contracts import INTERNAL_ERROR


def ensure_single_worker_runtime(*, configured_worker_count: int) -> None:
    if configured_worker_count <= 1:
        return
    raise RuntimeError(
        "Direct chat streaming requires a single runtime worker because _CHAT_STREAM_SESSIONS "
        "is process-local and not safe for multi-worker deployment."
    )


def initialize_runtime_state(
    *,
    now_ts: Optional[float],
    ensure_single_worker_runtime_fn: Callable[[], None],
    db_path: Any,
    stale_after_seconds: int,
    ttl_seconds: int,
    mark_stale_sessions_interrupted: Callable[..., int],
    delete_sessions_older_than: Callable[..., int],
    metrics_inc: Callable[[str, float], None],
    session_manager_enabled: Callable[[], bool],
    session_manager_factory: Callable[[], Any],
) -> None:
    ensure_single_worker_runtime_fn()
    reference = float(now_ts or time.time())
    interrupted = mark_stale_sessions_interrupted(
        db_path,
        stale_before_ts=reference - stale_after_seconds,
        error_text="Chat stream was interrupted while the runtime was unavailable.",
    )
    if interrupted:
        metrics_inc("chat_stream_interrupted", interrupted)
    deleted = delete_sessions_older_than(
        db_path,
        older_than_ts=reference - ttl_seconds,
    )
    if deleted:
        metrics_inc("chat_stream_state_cleanup_sessions", deleted)
    if session_manager_enabled():
        try:
            session_manager_factory().interrupt_stale_sessions(now_ts=reference)
        except Exception:
            return


def default_chat_stream_session(
    key: str,
    *,
    thread_id: str,
    request_id: str,
    workspace_id: str,
    now_ts: Optional[float],
    now_iso: Callable[[], str],
) -> dict[str, Any]:
    reference = float(now_ts or time.time())
    return {
        "key": key,
        "thread_id": thread_id,
        "request_id": request_id,
        "workspace_id": workspace_id,
        "condition": threading.Condition(),
        "events": [],
        "next_event_id": 1,
        "producer_started": False,
        "completed": False,
        "last_accessed_at": reference,
        "created_at": now_iso(),
        "status": "active",
        "last_event_seq": 0,
        "partial_text": "",
        "final_payload": {},
        "error_text": "",
        "metadata": {},
    }


def persist_chat_stream_session_state(
    session: dict[str, Any],
    *,
    db_path: Any,
    now_iso: Callable[[], str],
    upsert_state: Callable[[Any, dict[str, Any]], None],
) -> None:
    upsert_state(
        db_path,
        {
            "session_id": str(session.get("key") or "").strip(),
            "thread_id": str(session.get("thread_id") or "").strip(),
            "request_id": str(session.get("request_id") or "").strip(),
            "workspace_id": str(session.get("workspace_id") or "").strip(),
            "status": str(session.get("status") or "active").strip() or "active",
            "created_at": str(session.get("created_at") or "").strip() or now_iso(),
            "updated_at": now_iso(),
            "last_event_seq": int(session.get("last_event_seq") or 0),
            "partial_text": str(session.get("partial_text") or ""),
            "final_payload": session.get("final_payload") if isinstance(session.get("final_payload"), dict) else {},
            "error_text": str(session.get("error_text") or "").strip(),
            "metadata": session.get("metadata") if isinstance(session.get("metadata"), dict) else {},
        },
    )


def chat_stream_interrupted_final_payload(partial_text: str, error_text: str) -> dict[str, Any]:
    detail = str(error_text or "").strip() or "Chat stream was interrupted while the runtime was unavailable."
    error = error_response_service.platform_error(
        code="chat_stream_interrupted",
        message=detail,
        error_class=INTERNAL_ERROR,
        retryable=True,
        status_code=503,
    )
    terminal_error = error_response_service.serialize_stream_error_envelope(
        error_response_service.build_stream_error_envelope(error=error)
    )
    return {
        "kind": "terminal_error",
        "reply": "",
        "actions": [],
        "mode": "answer",
        "error": terminal_error["error"]["code"],
        "message": terminal_error["error"]["message"],
        "terminal_error": terminal_error,
        "trace_id": terminal_error["trace_id"],
        "request_id": terminal_error["request_id"],
        "interventions": [
            build_intervention(
                "system_error",
                "Chat stream was interrupted",
                detail=detail,
                severity="error",
                status="failed",
                code="chat_stream_interrupted",
                metadata={"partial_text": str(partial_text or "").strip() or None},
            )
        ],
    }


def chat_stream_replay_payload_from_state(state: dict[str, Any]) -> dict[str, Any]:
    status = str(state.get("status") or "").strip().lower()
    if status == "completed":
        final_payload = state.get("final_payload")
        if isinstance(final_payload, dict) and final_payload:
            return final_payload
        fallback_reply = str(state.get("partial_text") or "").strip()
        return {
            "reply": fallback_reply,
            "actions": [],
            "mode": "answer",
            "error": "",
            "interventions": []
            if fallback_reply
            else [
                build_intervention(
                    "system_notice",
                    "Chat completed",
                    detail="The stream completed without a final assistant message body.",
                    severity="info",
                    status="completed",
                    code="chat_stream_completed",
                )
            ],
        }
    return chat_stream_interrupted_final_payload(
        str(state.get("partial_text") or ""),
        str(state.get("error_text") or ""),
    )


def _has_replayable_final_payload(state: dict[str, Any]) -> bool:
    final_payload = state.get("final_payload")
    if not isinstance(final_payload, dict) or not final_payload:
        return False
    if str(final_payload.get("kind") or "").strip() == "terminal_error":
        return False
    if str(final_payload.get("terminal_error") or "").strip():
        return False
    if str(final_payload.get("error") or "").strip() and not str(final_payload.get("reply") or "").strip():
        return False
    return True


def _mark_stream_state_retryable(
    state: dict[str, Any],
    *,
    db_path: Any,
    upsert_state: Callable[[Any, dict[str, Any]], None],
    now_iso: Callable[[], str],
    reason: str,
) -> None:
    metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
    metadata["retryable_reason"] = reason
    metadata["previous_status"] = str(state.get("status") or "").strip() or None
    state["metadata"] = metadata
    state["status"] = "retryable"
    state["error_text"] = ""
    state["final_payload"] = {}
    state["updated_at"] = now_iso()
    upsert_state(db_path, state)


def build_chat_stream_replay_session(
    key: str,
    *,
    thread_id: str,
    request_id: str,
    workspace_id: str,
    state: dict[str, Any],
    default_session_factory: Callable[..., dict[str, Any]],
    replay_payload_from_state: Callable[[dict[str, Any]], dict[str, Any]],
    now_iso: Callable[[], str],
) -> dict[str, Any]:
    session = default_session_factory(
        key,
        thread_id=thread_id,
        request_id=request_id,
        workspace_id=workspace_id,
    )
    seq = max(1, int(state.get("last_event_seq") or 1))
    payload = replay_payload_from_state(state)
    session.update(
        {
            "producer_started": True,
            "completed": True,
            "next_event_id": seq + 1,
            "events": [
                {
                    "id": str(seq),
                    "seq": seq,
                    "event": "final",
                    "payload": payload,
                }
            ],
            "last_event_seq": seq,
            "partial_text": str(state.get("partial_text") or ""),
            "status": str(state.get("status") or "completed").strip() or "completed",
            "final_payload": payload,
            "error_text": str(state.get("error_text") or "").strip(),
            "created_at": str(state.get("created_at") or "").strip() or now_iso(),
        }
    )
    return session


def load_replayable_chat_stream_session(
    key: str,
    *,
    thread_id: str,
    request_id: str,
    workspace_id: str,
    db_path: Any,
    get_state: Callable[[Any, str], Optional[dict[str, Any]]],
    upsert_state: Callable[[Any, dict[str, Any]], None],
    metrics_inc: Callable[[str, float], None],
    now_iso: Callable[[], str],
    interrupted_final_payload: Callable[[str, str], dict[str, Any]],
    build_replay_session: Callable[..., dict[str, Any]],
) -> Optional[dict[str, Any]]:
    state = get_state(db_path, key)
    if not isinstance(state, dict):
        return None
    if (
        str(state.get("workspace_id") or "").strip() != str(workspace_id or "").strip()
        or str(state.get("thread_id") or "").strip() != str(thread_id or "").strip()
        or str(state.get("request_id") or "").strip() != str(request_id or "").strip()
    ):
        return None
    status = str(state.get("status") or "").strip().lower()
    if status == "active":
        _mark_stream_state_retryable(
            state,
            db_path=db_path,
            upsert_state=upsert_state,
            now_iso=now_iso,
            reason="active_state_without_live_producer",
        )
        metrics_inc("chat_stream_restarted_unfinished", 1)
        return None
    if status not in {"completed", "interrupted", "error"}:
        return None
    if status in {"interrupted", "error"} and not _has_replayable_final_payload(state):
        _mark_stream_state_retryable(
            state,
            db_path=db_path,
            upsert_state=upsert_state,
            now_iso=now_iso,
            reason=f"{status}_state_without_replayable_final",
        )
        metrics_inc("chat_stream_restarted_unfinished", 1)
        return None
    if status == "completed":
        metrics_inc("chat_stream_replayed_completed", 1)
    else:
        metrics_inc("chat_stream_replayed_interrupted", 1)
    return build_replay_session(
        key,
        thread_id=thread_id,
        request_id=request_id,
        workspace_id=workspace_id,
        state=state,
    )


def prune_chat_stream_sessions_locked(
    sessions: dict[str, dict[str, Any]],
    *,
    ttl_seconds: int,
    now_ts: Optional[float],
) -> None:
    reference = float(now_ts or time.time())
    stale_keys = [
        key
        for key, session in sessions.items()
        if reference - float(session.get("last_accessed_at") or reference) > ttl_seconds
    ]
    for key in stale_keys:
        sessions.pop(key, None)


def get_or_create_chat_stream_session(
    sessions: dict[str, dict[str, Any]],
    *,
    key: str,
    thread_id: str,
    request_id: str,
    workspace_id: str,
    prune_sessions_locked: Callable[[], None],
    delete_sessions_older_than: Callable[..., int],
    db_path: Any,
    state_ttl_seconds: int,
    load_replayable_session: Callable[..., Optional[dict[str, Any]]],
    default_session_factory: Callable[..., dict[str, Any]],
    persist_session_state: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    prune_sessions_locked()
    delete_sessions_older_than(
        db_path,
        older_than_ts=time.time() - state_ttl_seconds,
    )
    session = sessions.get(key)
    if isinstance(session, dict):
        session["last_accessed_at"] = time.time()
        return session
    replay_session = load_replayable_session(
        key,
        thread_id=thread_id,
        request_id=request_id,
        workspace_id=workspace_id,
    )
    if isinstance(replay_session, dict):
        sessions[key] = replay_session
        return replay_session
    session = default_session_factory(
        key,
        thread_id=thread_id,
        request_id=request_id,
        workspace_id=workspace_id,
    )
    sessions[key] = session
    persist_session_state(session)
    return session


def append_chat_stream_event(
    session: dict[str, Any],
    *,
    event_name: str,
    payload: dict[str, Any],
    buffer_limit: int,
    persist_session_state: Callable[[dict[str, Any]], None],
) -> None:
    condition = session.get("condition")
    if not isinstance(condition, threading.Condition):
        return
    with condition:
        next_event_id = int(session.get("next_event_id") or 1)
        record = {
            "id": str(next_event_id),
            "seq": next_event_id,
            "event": event_name,
            "payload": payload,
        }
        session["next_event_id"] = next_event_id + 1
        events = session.get("events") if isinstance(session.get("events"), list) else []
        events.append(record)
        if len(events) > buffer_limit:
            del events[:-buffer_limit]
        session["events"] = events
        session["last_accessed_at"] = time.time()
        session["last_event_seq"] = next_event_id
        if event_name == "chunk":
            session["partial_text"] = str(session.get("partial_text") or "") + str(payload.get("delta") or "")
        if event_name == "final":
            session["final_payload"] = dict(payload)
            session["completed"] = True
            terminal_error = payload.get("terminal_error") if isinstance(payload.get("terminal_error"), dict) else {}
            terminal_error_body = terminal_error.get("error") if isinstance(terminal_error.get("error"), dict) else {}
            error_code = str(terminal_error_body.get("code") or payload.get("error") or "").strip()
            error_message = (
                str(terminal_error_body.get("message") or "").strip()
                or str(payload.get("message") or "").strip()
                or error_code
            )
            if error_code or error_message:
                session["status"] = "error"
                session["error_text"] = error_message
            else:
                session["status"] = "completed"
                if not str(session.get("partial_text") or "").strip():
                    session["partial_text"] = str(payload.get("reply") or "")
        condition.notify_all()
    persist_session_state(session)


def complete_chat_stream_session(
    session: dict[str, Any],
    *,
    persist_session_state: Callable[[dict[str, Any]], None],
) -> None:
    condition = session.get("condition")
    if not isinstance(condition, threading.Condition):
        return
    with condition:
        session["completed"] = True
        session["last_accessed_at"] = time.time()
        if str(session.get("status") or "active").strip() == "active":
            session["status"] = "completed"
        condition.notify_all()
    persist_session_state(session)
