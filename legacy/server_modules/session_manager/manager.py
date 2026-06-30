from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, Optional

from server_modules.runtime_state_store import (
    delete_runtime_session,
    get_runtime_session,
    get_runtime_session_turn,
    init_runtime_state_db,
    list_runtime_session_turns,
    list_runtime_sessions,
    upsert_runtime_session,
    upsert_runtime_session_turn,
)
from server_modules.session_manager.actor_queue import SessionActorQueue
from server_modules.session_manager.observability import build_session_manager_snapshot
from server_modules.session_manager.runtime_cache import RuntimeHandleCache
from server_modules.session_manager.types import SessionRuntimeHandle
from server_modules.state_paths import runtime_state_db_path


TurnExecutor = Callable[..., Iterator[Dict[str, Any]]]

_DEFAULT_MANAGER_LOCK = threading.Lock()
_DEFAULT_MANAGER: Optional["EmpyralisSessionManager"] = None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class EmpyralisSessionManager:
    def __init__(
        self,
        *,
        db_path: Path,
        stale_session_seconds: int = 10 * 60,
        handle_idle_ttl_seconds: int = 15 * 60,
        browser_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.stale_session_seconds = max(1, int(stale_session_seconds))
        self.handle_idle_ttl_seconds = max(1, int(handle_idle_ttl_seconds))
        self.browser_factory = browser_factory
        self.actor_queue = SessionActorQueue()
        self.runtime_cache = RuntimeHandleCache()
        self._stats_lock = threading.Lock()
        self._active_turns = 0
        self._evicted_runtime_count = 0
        self._interrupted_stale_session_count = 0
        self._error_counts_by_code: Dict[str, int] = {}
        init_runtime_state_db(self.db_path)

    def _should_eager_create_browser(self) -> bool:
        return callable(self.browser_factory)

    def _close_owned_browser(self, browser: Any) -> None:
        if browser is None:
            return
        # Direct chat can still attach the global browser singleton to a handle.
        # The session manager does not own that lifecycle, so it must not close it.
        if (
            (
                type(browser).__name__ == "BrowserEngine"
                and str(type(browser).__module__ or "").strip() == "server_modules.browser_engine"
            )
            or bool(getattr(browser, "__empyralis_browser_adapter__", False))
        ):
            return
        if hasattr(browser, "close_sync"):
            try:
                browser.close_sync()
            except Exception:
                pass

    def _record_error(self, code: str) -> None:
        token = str(code or "unknown_error").strip() or "unknown_error"
        with self._stats_lock:
            self._error_counts_by_code[token] = (self._error_counts_by_code.get(token) or 0) + 1

    def _add_active_turn_delta(self, delta: int) -> None:
        with self._stats_lock:
            self._active_turns = max(0, int(self._active_turns) + int(delta))

    def _build_prior_messages_from_turn_history(
        self,
        session_id: str,
        *,
        limit: int = 8,
    ) -> list[dict[str, str]]:
        token = str(session_id or "").strip()
        if not token:
            return []
        turns = list_runtime_session_turns(
            self.db_path,
            session_id=token,
            statuses=["completed"],
            limit=max(1, int(limit)),
        )
        if not turns:
            return []
        ordered_turns = list(reversed(turns))
        prior_messages: list[dict[str, str]] = []
        for turn in ordered_turns:
            if not isinstance(turn, dict):
                continue
            input_payload = dict(turn.get("input") or {})
            final_payload = dict(turn.get("final_payload") or {})
            user_message = str(input_payload.get("message") or "").strip()
            assistant_reply = str(final_payload.get("reply") or "").strip()
            if user_message:
                prior_messages.append({"role": "user", "content": user_message})
            if assistant_reply:
                prior_messages.append({"role": "assistant", "content": assistant_reply})
        return prior_messages

    def _completed_turn_replay_event(self, turn_id: str) -> Optional[Dict[str, Any]]:
        existing_turn = get_runtime_session_turn(self.db_path, turn_id)
        if not isinstance(existing_turn, dict):
            return None
        if str(existing_turn.get("status") or "").strip() != "completed":
            return None
        final_payload = existing_turn.get("final_payload")
        if not isinstance(final_payload, dict) or not final_payload:
            return None
        return {
            "type": "final",
            "payload": dict(final_payload),
            "metadata": {
                "replayed": True,
                "request_id": str(existing_turn.get("request_id") or turn_id).strip() or turn_id,
            },
        }

    def _build_runtime_handle(
        self,
        *,
        session_id: str,
        actor_key: str,
        workspace_id: str,
        user_id: str,
        runtime_options: Dict[str, Any],
        meta: Dict[str, Any],
    ) -> SessionRuntimeHandle:
        browser = None
        if self._should_eager_create_browser():
            try:
                browser = self.browser_factory()
            except Exception:
                browser = None
        return SessionRuntimeHandle(
            session_id=session_id,
            actor_key=actor_key,
            workspace_id=workspace_id,
            user_id=user_id,
            cwd=str(runtime_options.get("cwd") or "").strip(),
            runtime_options=dict(runtime_options),
            meta=dict(meta),
            browser=browser,
            last_touched_at=time.time(),
        )

    def _ensure_runtime_handle(self, session: Dict[str, Any]) -> SessionRuntimeHandle:
        actor_key = str(session.get("actor_key") or session.get("session_id") or "").strip()
        runtime_options = dict(session.get("runtime_options") or {})
        meta = dict(session.get("meta") or {})
        cached = self.runtime_cache.get(actor_key)
        if cached is not None:
            cached.workspace_id = str(session.get("workspace_id") or "").strip()
            cached.user_id = str(session.get("user_id") or "").strip()
            cached.cwd = str(runtime_options.get("cwd") or cached.cwd or "").strip()
            cached.runtime_options = runtime_options
            cached.meta = meta
            return cached
        handle = self._build_runtime_handle(
            session_id=str(session.get("session_id") or "").strip(),
            actor_key=actor_key,
            workspace_id=str(session.get("workspace_id") or "").strip(),
            user_id=str(session.get("user_id") or "").strip(),
            runtime_options=runtime_options,
            meta=meta,
        )
        self.runtime_cache.set(actor_key, handle)
        return handle

    def ensure_session(
        self,
        *,
        session_id: str,
        actor_key: str,
        workspace_id: str,
        user_id: str = "",
        runtime_options: Optional[Dict[str, Any]] = None,
        meta: Optional[Dict[str, Any]] = None,
        status: str = "ready",
    ) -> Dict[str, Any]:
        token = str(session_id or "").strip()
        if not token:
            raise ValueError("session_id is required")
        actor_token = str(actor_key or "").strip() or token
        now_iso = _utc_now_iso()
        existing = get_runtime_session(self.db_path, token) or {}
        payload = {
            "session_id": token,
            "actor_key": actor_token,
            "workspace_id": str(workspace_id or existing.get("workspace_id") or "").strip(),
            "user_id": str(user_id or existing.get("user_id") or "").strip(),
            "status": str(status or existing.get("status") or "ready").strip() or "ready",
            "runtime_options": (
                dict(runtime_options)
                if isinstance(runtime_options, dict)
                else dict(existing.get("runtime_options") or {})
            ),
            "created_at": str(existing.get("created_at") or "").strip() or now_iso,
            "updated_at": now_iso,
            "last_touched_at": now_iso,
            "last_error": str(existing.get("last_error") or "").strip(),
            "meta": (
                dict(meta)
                if isinstance(meta, dict)
                else dict(existing.get("meta") or {})
            ),
        }
        upsert_runtime_session(self.db_path, payload)
        session = get_runtime_session(self.db_path, token) or payload
        self._ensure_runtime_handle(session)
        return session

    def iter_turn_events(
        self,
        *,
        session_id: str,
        actor_key: str,
        workspace_id: str,
        user_id: str,
        message: str,
        request_meta: Optional[Dict[str, Any]],
        turn_executor: TurnExecutor,
        stream_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Iterator[Dict[str, Any]]:
        meta = dict(request_meta or {})
        existing_prior_messages = meta.get("prior_messages")
        if not isinstance(existing_prior_messages, list) or not existing_prior_messages:
            hydrated_prior_messages = self._build_prior_messages_from_turn_history(session_id)
            if hydrated_prior_messages:
                meta["prior_messages"] = hydrated_prior_messages
        request_id = str(meta.get("request_id") or meta.get("client_request_id") or uuid.uuid4().hex).strip()
        turn_id = str(meta.get("turn_id") or request_id or uuid.uuid4().hex).strip() or uuid.uuid4().hex
        replay_event = self._completed_turn_replay_event(turn_id)
        if replay_event is not None:
            yield replay_event
            return
        runtime_options = dict(meta.get("runtime_options") or {})
        now_iso = _utc_now_iso()
        session = self.ensure_session(
            session_id=session_id,
            actor_key=actor_key,
            workspace_id=workspace_id,
            user_id=user_id,
            runtime_options=runtime_options,
            meta=meta,
            status="ready",
        )
        actor_token = str(session.get("actor_key") or actor_key or session_id).strip() or session_id
        with self.actor_queue.claim(actor_token):
            replay_event = self._completed_turn_replay_event(turn_id)
            if replay_event is not None:
                yield replay_event
                return
            session = self.ensure_session(
                session_id=session_id,
                actor_key=actor_token,
                workspace_id=workspace_id,
                user_id=user_id,
                runtime_options=runtime_options,
                meta=meta,
                status="active",
            )
            handle = self._ensure_runtime_handle(session)
            self._add_active_turn_delta(1)
            event_count = 0
            chunk_count = 0
            final_payload: Dict[str, Any] = {}
            final_seen = False
            upsert_runtime_session_turn(
                self.db_path,
                {
                    "turn_id": turn_id,
                    "session_id": session_id,
                    "request_id": request_id,
                    "status": "running",
                    "started_at": now_iso,
                    "updated_at": now_iso,
                    "input": {
                        "message": str(message or ""),
                        "request_meta": meta,
                    },
                    "metrics": {},
                },
            )
            session_ctx = {
                "session_id": session_id,
                "actor_key": actor_token,
                "workspace_id": workspace_id,
                "user_id": user_id,
                "runtime_handle": handle,
                "runtime_options": dict(handle.runtime_options),
                "meta": dict(handle.meta),
                "browser": handle.browser,
            }
            try:
                for event in turn_executor(
                    session_ctx=session_ctx,
                    message=message,
                    request_meta=meta,
                ):
                    event_payload = dict(event) if isinstance(event, dict) else {}
                    if callable(stream_sink):
                        stream_sink(event_payload)
                    event_type = str(event_payload.get("type") or "").strip().lower()
                    event_count += 1
                    if event_type == "chunk":
                        chunk_count += 1
                    if event_type == "final" and isinstance(event_payload.get("payload"), dict):
                        final_payload = dict(event_payload.get("payload") or {})
                        final_seen = True
                    yield event_payload
                if not final_seen:
                    final_payload = final_payload or {"reply": ""}
                error_text = str(final_payload.get("error") or "").strip()
                completed_at = _utc_now_iso()
                upsert_runtime_session_turn(
                    self.db_path,
                    {
                        "turn_id": turn_id,
                        "session_id": session_id,
                        "request_id": request_id,
                        "status": "error" if error_text else "completed",
                        "started_at": now_iso,
                        "updated_at": completed_at,
                        "completed_at": completed_at,
                        "input": {
                            "message": str(message or ""),
                            "request_meta": meta,
                        },
                        "final_payload": final_payload if isinstance(final_payload, dict) else {},
                        "error_text": error_text,
                        "metrics": {
                            "event_count": event_count,
                            "chunk_count": chunk_count,
                        },
                    },
                )
                upsert_runtime_session(
                    self.db_path,
                    {
                        **session,
                        "status": "error" if error_text else "ready",
                        "updated_at": completed_at,
                        "last_touched_at": completed_at,
                        "last_error": error_text,
                    },
                )
                if error_text:
                    self._record_error(error_text.split(":", 1)[0])
            except Exception as exc:
                error_text = str(exc).strip() or "turn_failed"
                finished_at = _utc_now_iso()
                upsert_runtime_session_turn(
                    self.db_path,
                    {
                        "turn_id": turn_id,
                        "session_id": session_id,
                        "request_id": request_id,
                        "status": "error",
                        "started_at": now_iso,
                        "updated_at": finished_at,
                        "completed_at": finished_at,
                        "input": {
                            "message": str(message or ""),
                            "request_meta": meta,
                        },
                        "final_payload": final_payload if isinstance(final_payload, dict) else {},
                        "error_text": error_text,
                        "metrics": {
                            "event_count": event_count,
                            "chunk_count": chunk_count,
                        },
                    },
                )
                upsert_runtime_session(
                    self.db_path,
                    {
                        **session,
                        "status": "error",
                        "updated_at": finished_at,
                        "last_touched_at": finished_at,
                        "last_error": error_text,
                    },
                )
                self._record_error(error_text.split(":", 1)[0])
                raise
            finally:
                handle.last_touched_at = time.time()
                self.runtime_cache.set(actor_token, handle, now=handle.last_touched_at)
                self._add_active_turn_delta(-1)

    def run_turn(
        self,
        *,
        session_id: str,
        actor_key: str,
        workspace_id: str,
        user_id: str,
        message: str,
        request_meta: Optional[Dict[str, Any]],
        turn_executor: TurnExecutor,
        stream_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        final_payload: Dict[str, Any] = {}
        accumulated_reply = ""
        for event in self.iter_turn_events(
            session_id=session_id,
            actor_key=actor_key,
            workspace_id=workspace_id,
            user_id=user_id,
            message=message,
            request_meta=request_meta,
            turn_executor=turn_executor,
        ):
            if callable(stream_sink):
                stream_sink(event)
            event_type = str(event.get("type") or "").strip().lower()
            if event_type == "chunk":
                accumulated_reply += str(event.get("delta") or "")
            elif event_type == "final" and isinstance(event.get("payload"), dict):
                final_payload = dict(event.get("payload") or {})
        if not final_payload and accumulated_reply:
            final_payload["reply"] = accumulated_reply
        return final_payload

    def close_session(self, session_id: str) -> None:
        session = get_runtime_session(self.db_path, session_id)
        if not isinstance(session, dict):
            return
        actor_key = str(session.get("actor_key") or session_id).strip()
        handle = self.runtime_cache.peek(actor_key)
        if handle is not None:
            self._close_owned_browser(getattr(handle, "browser", None))
        self.runtime_cache.clear(actor_key)
        upsert_runtime_session(
            self.db_path,
            {
                **session,
                "status": "closed",
                "updated_at": _utc_now_iso(),
                "last_touched_at": _utc_now_iso(),
            },
        )

    def reset_session(self, session_id: str) -> None:
        session = get_runtime_session(self.db_path, session_id)
        if not isinstance(session, dict):
            return
        actor_key = str(session.get("actor_key") or session_id).strip()
        handle = self.runtime_cache.peek(actor_key)
        if handle is not None:
            self._close_owned_browser(getattr(handle, "browser", None))
        self.runtime_cache.clear(actor_key)
        turns = list_runtime_session_turns(
            self.db_path, session_id=str(session.get("session_id") or ""), statuses=["running", "completed"]
        )
        for turn in turns:
            upsert_runtime_session_turn(
                self.db_path,
                {
                    **turn,
                    "status": "archived",
                    "updated_at": _utc_now_iso(),
                    "completed_at": _utc_now_iso(),
                },
            )
        upsert_runtime_session(
            self.db_path,
            {
                **session,
                "status": "active",
                "updated_at": _utc_now_iso(),
                "last_touched_at": _utc_now_iso(),
                "reset_count": int(session.get("reset_count", 0)) + 1,
                "last_reset_at": _utc_now_iso(),
                "turn_count": 0,
            },
        )

    def export_trace(self, session_id: str) -> Dict[str, Any]:
        session = get_runtime_session(self.db_path, session_id)
        if not isinstance(session, dict):
            return {"error": "session_not_found", "session_id": session_id}
        turns = list_runtime_session_turns(
            self.db_path,
            session_id=str(session.get("session_id") or session_id).strip(),
            statuses=["running", "completed", "error", "archived"],
        )
        handle = self.runtime_cache.peek(
            str(session.get("actor_key") or session_id).strip()
        )
        cache_state = None
        if handle is not None:
            cache_state = {
                "has_browser": getattr(handle, "browser", None) is not None,
                "actor_key": str(getattr(handle, "actor_key", "")),
            }
        return {
            "session_id": str(session.get("session_id") or session_id),
            "workspace_id": str(session.get("workspace_id") or ""),
            "status": str(session.get("status") or "active"),
            "created_at": str(session.get("created_at") or ""),
            "updated_at": str(session.get("updated_at") or ""),
            "last_touched_at": str(session.get("last_touched_at") or ""),
            "reset_count": int(session.get("reset_count", 0)),
            "turn_count": int(session.get("turn_count", len(turns))),
            "total_historical_turns": len(turns),
            "turns": [
                {
                    "turn_id": str(t.get("turn_id") or ""),
                    "status": str(t.get("status") or ""),
                    "created_at": str(t.get("created_at") or ""),
                    "completed_at": str(t.get("completed_at") or ""),
                    "error_text": str(t.get("error_text") or "") or None,
                }
                for t in turns
            ],
            "runtime_cache": cache_state,
            "errors": {
                "total": int(self._error_count),
                "interrupted_stale": int(self._interrupted_stale_session_count),
                "evicted_runtime": int(self._evicted_runtime_count),
            },
        }

    def interrupt_stale_sessions(self, *, now_ts: Optional[float] = None) -> int:
        reference = float(now_ts if now_ts is not None else time.time())
        interrupted = 0
        for session in list_runtime_sessions(self.db_path, statuses=["active"]):
            touched = str(session.get("last_touched_at") or session.get("updated_at") or "").strip()
            touched_ts = 0.0
            try:
                touched_ts = datetime.fromisoformat(touched.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
            except Exception:
                touched_ts = reference
            if touched_ts > reference - self.stale_session_seconds:
                continue
            upsert_runtime_session(
                self.db_path,
                {
                    **session,
                    "status": "interrupted",
                    "updated_at": _utc_now_iso(),
                    "last_touched_at": _utc_now_iso(),
                    "last_error": "Session interrupted while the runtime was unavailable.",
                },
            )
            turns = list_runtime_session_turns(self.db_path, session_id=str(session.get("session_id") or ""), statuses=["running"])
            for turn in turns:
                upsert_runtime_session_turn(
                    self.db_path,
                    {
                        **turn,
                        "status": "error",
                        "updated_at": _utc_now_iso(),
                        "completed_at": _utc_now_iso(),
                        "error_text": "Session interrupted while the runtime was unavailable.",
                    },
                )
            interrupted += 1
        if interrupted:
            with self._stats_lock:
                self._interrupted_stale_session_count += interrupted
        return interrupted

    def evict_idle_handles(self, *, now_ts: Optional[float] = None) -> int:
        candidates = self.runtime_cache.collect_idle_candidates(
            max_idle_ms=self.handle_idle_ttl_seconds * 1000,
            now=now_ts,
        )
        evicted = 0
        for candidate in candidates:
            current = self.runtime_cache.peek(candidate.actor_key)
            if current is None or current is not candidate.state:
                continue
            self._close_owned_browser(getattr(candidate.state, "browser", None))
            self.runtime_cache.clear(candidate.actor_key)
            evicted += 1
        if evicted:
            with self._stats_lock:
                self._evicted_runtime_count += evicted
        return evicted

    def get_observability_snapshot(self, *, enabled: bool = True) -> Dict[str, Any]:
        with self._stats_lock:
            active_turns = int(self._active_turns)
            evicted_total = int(self._evicted_runtime_count)
            interrupted_stale_sessions = int(self._interrupted_stale_session_count)
            errors_by_code = dict(self._error_counts_by_code)
        return build_session_manager_snapshot(
            enabled=enabled,
            runtime_cache_active_sessions=self.runtime_cache.size(),
            runtime_cache_idle_ttl_ms=self.handle_idle_ttl_seconds * 1000,
            evicted_total=evicted_total,
            active_turns=active_turns,
            queue_depth=self.actor_queue.get_total_pending_count(),
            interrupted_stale_sessions=interrupted_stale_sessions,
            errors_by_code=errors_by_code,
        )


def get_default_session_manager(*, db_path: Optional[Path] = None) -> EmpyralisSessionManager:
    global _DEFAULT_MANAGER
    resolved_db_path = Path(db_path).expanduser().resolve() if db_path else runtime_state_db_path().resolve()
    with _DEFAULT_MANAGER_LOCK:
        if _DEFAULT_MANAGER is None or _DEFAULT_MANAGER.db_path != resolved_db_path:
            _DEFAULT_MANAGER = EmpyralisSessionManager(db_path=resolved_db_path)
        return _DEFAULT_MANAGER
