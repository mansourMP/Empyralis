from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from server_modules import direct_chat_stream_state_service as state_service
from server_modules.direct_chat_stream_response_service import DirectChatStreamResponseServices


@dataclass(frozen=True)
class DirectChatStreamRuntimeBindings:
    default_chat_stream_session: Callable[..., dict[str, Any]]
    persist_chat_stream_session_state: Callable[[dict[str, Any]], None]
    build_chat_stream_replay_session: Callable[..., dict[str, Any]]
    load_replayable_chat_stream_session: Callable[..., Optional[dict[str, Any]]]
    direct_chat_session_manager: Callable[[], Any]
    initialize_chat_stream_runtime_state: Callable[..., None]
    direct_chat_execution_services: Callable[[], Any]
    direct_chat_stream_response_services: Callable[[], DirectChatStreamResponseServices]
    get_or_create_chat_stream_session: Callable[..., dict[str, Any]]


def resolve_chat_stream_state_db_path(
    *,
    override: Any,
    late_server_export: Callable[[str], Any],
    fallback_db_path: str,
) -> Path:
    if override:
        return Path(override)
    try:
        return Path(late_server_export("ORION_RUNTIME_STATE_DB"))
    except Exception:
        return Path(fallback_db_path)


def configured_direct_chat_worker_count(
    *,
    getenv: Callable[[str], Optional[str]] = os.getenv,
) -> int:
    for env_name in ("ORION_RUNTIME_UVICORN_WORKERS", "UVICORN_WORKERS", "WEB_CONCURRENCY"):
        raw_value = str(getenv(env_name) or "").strip()
        if not raw_value:
            continue
        try:
            parsed = int(raw_value)
        except Exception:
            continue
        if parsed > 0:
            return parsed
    return 1


def build_direct_chat_session_manager(
    *,
    get_default_session_manager: Callable[..., Any],
    db_path: Any,
) -> Any:
    return get_default_session_manager(db_path=db_path)


def build_default_direct_chat_session_manager_factory(
    *,
    chat_stream_state_db_path: Callable[[], Any],
    import_module: Callable[..., Any],
) -> Callable[[], Any]:
    def _factory() -> Any:
        manager_module = import_module(
            "server_modules.session_manager.manager",
            fromlist=["get_default_session_manager"],
        )
        return build_direct_chat_session_manager(
            get_default_session_manager=manager_module.get_default_session_manager,
            db_path=chat_stream_state_db_path(),
        )

    return _factory


def initialize_chat_stream_runtime_state(
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
    return state_service.initialize_runtime_state(
        now_ts=now_ts,
        ensure_single_worker_runtime_fn=ensure_single_worker_runtime_fn,
        db_path=db_path,
        stale_after_seconds=stale_after_seconds,
        ttl_seconds=ttl_seconds,
        mark_stale_sessions_interrupted=mark_stale_sessions_interrupted,
        delete_sessions_older_than=delete_sessions_older_than,
        metrics_inc=metrics_inc,
        session_manager_enabled=session_manager_enabled,
        session_manager_factory=session_manager_factory,
    )


def build_initialize_chat_stream_runtime_state_fn(
    *,
    ensure_single_worker_runtime_fn: Callable[[], None],
    chat_stream_state_db_path: Callable[[], Any],
    stale_after_seconds: int,
    ttl_seconds: int,
    mark_stale_sessions_interrupted: Callable[..., int],
    delete_sessions_older_than: Callable[..., int],
    metrics_inc: Callable[[str, float], None],
    session_manager_enabled: Callable[[], bool],
    session_manager_factory: Callable[[], Any],
) -> Callable[..., None]:
    return lambda *, now_ts=None: initialize_chat_stream_runtime_state(
        now_ts=now_ts,
        ensure_single_worker_runtime_fn=ensure_single_worker_runtime_fn,
        db_path=chat_stream_state_db_path(),
        stale_after_seconds=stale_after_seconds,
        ttl_seconds=ttl_seconds,
        mark_stale_sessions_interrupted=mark_stale_sessions_interrupted,
        delete_sessions_older_than=delete_sessions_older_than,
        metrics_inc=metrics_inc,
        session_manager_enabled=session_manager_enabled,
        session_manager_factory=session_manager_factory,
    )


def build_default_chat_stream_session_factory(
    *,
    now_iso: Callable[[], str],
) -> Callable[..., dict[str, Any]]:
    return lambda key, *, thread_id, request_id, workspace_id: state_service.default_chat_stream_session(
        key,
        thread_id=thread_id,
        request_id=request_id,
        workspace_id=workspace_id,
        now_ts=None,
        now_iso=now_iso,
    )


def build_persist_chat_stream_session_state(
    *,
    chat_stream_state_db_path: Callable[[], Any],
    now_iso: Callable[[], str],
    upsert_state: Callable[[Any, dict[str, Any]], None],
) -> Callable[[dict[str, Any]], None]:
    return lambda session: state_service.persist_chat_stream_session_state(
        session,
        db_path=chat_stream_state_db_path(),
        now_iso=now_iso,
        upsert_state=upsert_state,
    )


def build_chat_stream_replay_session_factory(
    *,
    default_session_factory: Callable[..., dict[str, Any]],
    replay_payload_from_state: Callable[[dict[str, Any]], dict[str, Any]],
    now_iso: Callable[[], str],
) -> Callable[..., dict[str, Any]]:
    return lambda key, *, thread_id, request_id, workspace_id, state: state_service.build_chat_stream_replay_session(
        key,
        thread_id=thread_id,
        request_id=request_id,
        workspace_id=workspace_id,
        state=state,
        default_session_factory=default_session_factory,
        replay_payload_from_state=replay_payload_from_state,
        now_iso=now_iso,
    )


def build_replayable_chat_stream_session_loader(
    *,
    chat_stream_state_db_path: Callable[[], Any],
    get_state: Callable[[Any, str], Optional[dict[str, Any]]],
    upsert_state: Callable[[Any, dict[str, Any]], None],
    metrics_inc: Callable[[str, float], None],
    now_iso: Callable[[], str],
    interrupted_final_payload: Callable[[str, str], dict[str, Any]],
    build_replay_session: Callable[..., dict[str, Any]],
) -> Callable[..., Optional[dict[str, Any]]]:
    return lambda key, *, thread_id, request_id, workspace_id: state_service.load_replayable_chat_stream_session(
        key,
        thread_id=thread_id,
        request_id=request_id,
        workspace_id=workspace_id,
        db_path=chat_stream_state_db_path(),
        get_state=get_state,
        upsert_state=upsert_state,
        metrics_inc=metrics_inc,
        now_iso=now_iso,
        interrupted_final_payload=interrupted_final_payload,
        build_replay_session=build_replay_session,
    )


def build_direct_chat_execution_services(
    *,
    builder: Callable[..., Any],
    chat_stream_key: Callable[..., str],
    session_manager_enabled: Callable[[], bool],
    session_manager_factory: Callable[[], Any],
    build_direct_operator_reply: Callable[..., Any],
    build_chat_turn_event_stream: Callable[..., Any],
) -> Any:
    return builder(
        chat_stream_key=chat_stream_key,
        session_manager_enabled=session_manager_enabled,
        session_manager_factory=session_manager_factory,
        build_direct_operator_reply=build_direct_operator_reply,
        build_chat_turn_event_stream=build_chat_turn_event_stream,
    )


def build_imported_direct_chat_execution_services(
    *,
    builder: Callable[..., Any],
    chat_stream_key: Callable[..., str],
    session_manager_enabled: Callable[[], bool],
    session_manager_factory: Callable[[], Any],
    import_module: Callable[..., Any],
) -> Any:
    direct_chat_module = import_module(
        "server_modules.direct_chat_runtime_exports",
        fromlist=["build_direct_operator_reply", "build_chat_turn_event_stream"],
    )
    return build_direct_chat_execution_services(
        builder=builder,
        chat_stream_key=chat_stream_key,
        session_manager_enabled=session_manager_enabled,
        session_manager_factory=session_manager_factory,
        build_direct_operator_reply=direct_chat_module.build_direct_operator_reply,
        build_chat_turn_event_stream=direct_chat_module.build_chat_turn_event_stream,
    )


def build_direct_chat_stream_response_services(
    *,
    resolve_direct_chat_turn_request: Callable[..., Any],
    chat_stream_request_signature: Callable[..., str],
    execute_agent_turn_request: Callable[..., Any],
    build_turn_execution_services: Callable[..., Any],
    run_execution_services: Callable[[], Any],
    direct_chat_execution_services: Callable[[], Any],
    get_chat_stream_state: Callable[[Any, str], Optional[dict[str, Any]]],
    chat_stream_state_db_path: Callable[[], Any],
    get_or_create_chat_stream_session: Callable[..., dict[str, Any]],
    extract_direct_chat_error_response: Callable[[Any], Optional[dict[str, str]]],
    start_chat_stream_producer: Callable[[dict[str, Any], Any], None],
    iter_chat_stream_events: Callable[[dict[str, Any], Any], Any],
) -> DirectChatStreamResponseServices:
    return DirectChatStreamResponseServices(
        resolve_direct_chat_turn_request=resolve_direct_chat_turn_request,
        chat_stream_request_signature=chat_stream_request_signature,
        execute_agent_turn_request=execute_agent_turn_request,
        build_turn_execution_services=build_turn_execution_services,
        run_execution_services=run_execution_services,
        direct_chat_execution_services=direct_chat_execution_services,
        get_chat_stream_state=get_chat_stream_state,
        chat_stream_state_db_path=chat_stream_state_db_path,
        get_or_create_chat_stream_session=get_or_create_chat_stream_session,
        extract_direct_chat_error_response=extract_direct_chat_error_response,
        start_chat_stream_producer=start_chat_stream_producer,
        iter_chat_stream_events=iter_chat_stream_events,
    )


def build_direct_chat_stream_response_services_factory(
    *,
    resolve_direct_chat_turn_request: Callable[..., Any],
    chat_stream_request_signature: Callable[..., str],
    execute_agent_turn_request: Callable[..., Any],
    build_turn_execution_services: Callable[..., Any],
    run_execution_services: Callable[[], Any],
    direct_chat_execution_services: Callable[[], Any],
    get_chat_stream_state: Callable[[Any, str], Optional[dict[str, Any]]],
    chat_stream_state_db_path: Callable[[], Any],
    get_or_create_chat_stream_session: Callable[..., dict[str, Any]],
    extract_direct_chat_error_response: Callable[[Any], Optional[dict[str, str]]],
    start_chat_stream_producer: Callable[[dict[str, Any], Any], None],
    iter_chat_stream_events: Callable[[dict[str, Any], Any], Any],
) -> Callable[[], DirectChatStreamResponseServices]:
    return lambda: build_direct_chat_stream_response_services(
        resolve_direct_chat_turn_request=resolve_direct_chat_turn_request,
        chat_stream_request_signature=chat_stream_request_signature,
        execute_agent_turn_request=execute_agent_turn_request,
        build_turn_execution_services=build_turn_execution_services,
        run_execution_services=run_execution_services,
        direct_chat_execution_services=direct_chat_execution_services,
        get_chat_stream_state=get_chat_stream_state,
        chat_stream_state_db_path=chat_stream_state_db_path,
        get_or_create_chat_stream_session=get_or_create_chat_stream_session,
        extract_direct_chat_error_response=extract_direct_chat_error_response,
        start_chat_stream_producer=start_chat_stream_producer,
        iter_chat_stream_events=iter_chat_stream_events,
    )


def build_direct_chat_stream_runtime_bindings(
    sessions: dict[str, dict[str, Any]],
    *,
    lock: Any,
    ensure_single_worker_runtime_fn: Callable[[], None],
    chat_stream_state_db_path: Callable[[], Any],
    stale_after_seconds: int,
    ttl_seconds: int,
    state_ttl_seconds: int,
    mark_stale_sessions_interrupted: Callable[..., int],
    delete_sessions_older_than: Callable[..., int],
    metrics_inc: Callable[[str, float], None],
    session_manager_enabled: Callable[[], bool],
    session_manager_factory: Optional[Callable[[], Any]] = None,
    import_module: Callable[..., Any],
    now_iso: Callable[[], str],
    replay_payload_from_state: Callable[[dict[str, Any]], dict[str, Any]],
    get_state: Callable[[Any, str], Optional[dict[str, Any]]],
    upsert_state: Callable[[Any, dict[str, Any]], None],
    interrupted_final_payload: Callable[[str, str], dict[str, Any]],
    direct_chat_execution_services_builder: Callable[..., Any],
    chat_stream_key: Callable[..., str],
    resolve_direct_chat_turn_request: Callable[..., Any],
    chat_stream_request_signature: Callable[..., str],
    execute_agent_turn_request: Callable[..., Any],
    build_turn_execution_services: Callable[..., Any],
    run_execution_services: Callable[[], Any],
    extract_direct_chat_error_response: Callable[[Any], Optional[dict[str, str]]],
    start_chat_stream_producer: Callable[[dict[str, Any], Any], None],
    iter_chat_stream_events: Callable[[dict[str, Any], Any], Any],
    prune_sessions_locked: Callable[[], None],
) -> DirectChatStreamRuntimeBindings:
    direct_chat_session_manager = build_default_direct_chat_session_manager_factory(
        chat_stream_state_db_path=chat_stream_state_db_path,
        import_module=import_module,
    )
    resolved_session_manager_factory = session_manager_factory or direct_chat_session_manager
    default_chat_stream_session = build_default_chat_stream_session_factory(
        now_iso=now_iso,
    )
    persist_chat_stream_session_state = build_persist_chat_stream_session_state(
        chat_stream_state_db_path=chat_stream_state_db_path,
        now_iso=now_iso,
        upsert_state=upsert_state,
    )
    build_chat_stream_replay_session = build_chat_stream_replay_session_factory(
        default_session_factory=default_chat_stream_session,
        replay_payload_from_state=replay_payload_from_state,
        now_iso=now_iso,
    )
    load_replayable_chat_stream_session = build_replayable_chat_stream_session_loader(
        chat_stream_state_db_path=chat_stream_state_db_path,
        get_state=get_state,
        upsert_state=upsert_state,
        metrics_inc=metrics_inc,
        now_iso=now_iso,
        interrupted_final_payload=interrupted_final_payload,
        build_replay_session=build_chat_stream_replay_session,
    )
    initialize_chat_stream_runtime_state = build_initialize_chat_stream_runtime_state_fn(
        ensure_single_worker_runtime_fn=ensure_single_worker_runtime_fn,
        chat_stream_state_db_path=chat_stream_state_db_path,
        stale_after_seconds=stale_after_seconds,
        ttl_seconds=ttl_seconds,
        mark_stale_sessions_interrupted=mark_stale_sessions_interrupted,
        delete_sessions_older_than=delete_sessions_older_than,
        metrics_inc=metrics_inc,
        session_manager_enabled=session_manager_enabled,
        session_manager_factory=resolved_session_manager_factory,
    )
    direct_chat_execution_services = lambda: build_imported_direct_chat_execution_services(
        builder=direct_chat_execution_services_builder,
        chat_stream_key=chat_stream_key,
        session_manager_enabled=session_manager_enabled,
        session_manager_factory=resolved_session_manager_factory,
        import_module=import_module,
    )
    get_or_create_chat_stream_session = build_get_or_create_chat_stream_session_fn(
        sessions,
        lock=lock,
        prune_sessions_locked=prune_sessions_locked,
        delete_sessions_older_than=delete_sessions_older_than,
        chat_stream_state_db_path=chat_stream_state_db_path,
        state_ttl_seconds=state_ttl_seconds,
        load_replayable_session=load_replayable_chat_stream_session,
        default_session_factory=default_chat_stream_session,
        persist_session_state=persist_chat_stream_session_state,
    )
    direct_chat_stream_response_services = build_direct_chat_stream_response_services_factory(
        resolve_direct_chat_turn_request=resolve_direct_chat_turn_request,
        chat_stream_request_signature=chat_stream_request_signature,
        execute_agent_turn_request=execute_agent_turn_request,
        build_turn_execution_services=build_turn_execution_services,
        run_execution_services=run_execution_services,
        direct_chat_execution_services=direct_chat_execution_services,
        get_chat_stream_state=get_state,
        chat_stream_state_db_path=chat_stream_state_db_path,
        get_or_create_chat_stream_session=get_or_create_chat_stream_session,
        extract_direct_chat_error_response=extract_direct_chat_error_response,
        start_chat_stream_producer=start_chat_stream_producer,
        iter_chat_stream_events=iter_chat_stream_events,
    )
    return DirectChatStreamRuntimeBindings(
        default_chat_stream_session=default_chat_stream_session,
        persist_chat_stream_session_state=persist_chat_stream_session_state,
        build_chat_stream_replay_session=build_chat_stream_replay_session,
        load_replayable_chat_stream_session=load_replayable_chat_stream_session,
        direct_chat_session_manager=direct_chat_session_manager,
        initialize_chat_stream_runtime_state=initialize_chat_stream_runtime_state,
        direct_chat_execution_services=direct_chat_execution_services,
        direct_chat_stream_response_services=direct_chat_stream_response_services,
        get_or_create_chat_stream_session=get_or_create_chat_stream_session,
    )


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
    return state_service.get_or_create_chat_stream_session(
        sessions,
        key=key,
        thread_id=thread_id,
        request_id=request_id,
        workspace_id=workspace_id,
        prune_sessions_locked=prune_sessions_locked,
        delete_sessions_older_than=delete_sessions_older_than,
        db_path=db_path,
        state_ttl_seconds=state_ttl_seconds,
        load_replayable_session=load_replayable_session,
        default_session_factory=default_session_factory,
        persist_session_state=persist_session_state,
    )


def build_get_or_create_chat_stream_session_fn(
    sessions: dict[str, dict[str, Any]],
    *,
    lock: Any,
    prune_sessions_locked: Callable[[], None],
    delete_sessions_older_than: Callable[..., int],
    chat_stream_state_db_path: Callable[[], Any],
    state_ttl_seconds: int,
    load_replayable_session: Callable[..., Optional[dict[str, Any]]],
    default_session_factory: Callable[..., dict[str, Any]],
    persist_session_state: Callable[[dict[str, Any]], None],
) -> Callable[..., dict[str, Any]]:
    def _get_or_create(
        key: str,
        *,
        thread_id: str,
        request_id: str,
        workspace_id: str,
    ) -> dict[str, Any]:
        with lock:
            return get_or_create_chat_stream_session(
                sessions,
                key=key,
                thread_id=thread_id,
                request_id=request_id,
                workspace_id=workspace_id,
                prune_sessions_locked=prune_sessions_locked,
                delete_sessions_older_than=delete_sessions_older_than,
                db_path=chat_stream_state_db_path(),
                state_ttl_seconds=state_ttl_seconds,
                load_replayable_session=load_replayable_session,
                default_session_factory=default_session_factory,
                persist_session_state=persist_session_state,
            )

    return _get_or_create


def append_chat_stream_event(
    session: dict[str, Any],
    *,
    event_name: str,
    payload: dict[str, Any],
    buffer_limit: int,
    persist_session_state: Callable[[dict[str, Any]], None],
) -> None:
    return state_service.append_chat_stream_event(
        session,
        event_name=event_name,
        payload=payload,
        buffer_limit=buffer_limit,
        persist_session_state=persist_session_state,
    )


def complete_chat_stream_session(
    session: dict[str, Any],
    *,
    persist_session_state: Callable[[dict[str, Any]], None],
) -> None:
    return state_service.complete_chat_stream_session(
        session,
        persist_session_state=persist_session_state,
    )
