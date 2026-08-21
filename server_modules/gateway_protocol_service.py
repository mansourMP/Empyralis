from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from fastapi import HTTPException
from fastapi import WebSocket, WebSocketDisconnect

PROTOCOL_VERSION = "v1alpha2"
SUPPORTED_PROTOCOL_VERSIONS = {"v1alpha2"}
from server_modules.gateway_contracts import DEFAULT_TOOL_REQUEST_TIMEOUT_SECONDS
MAX_GATEWAY_FRAME_BYTES = 16 * 1024 * 1024
MAX_GATEWAY_JSON_DEPTH = 32

from server_modules import (
    auth,
    gateway_activity_service,
    gateway_inventory_service,
    gateway_registry_service,
    gateway_state_repository,
    personal_channels_service,
    rust_runtime_kernel_client,
    session_service,
)
from server_modules.kill_switch_gate import assert_not_killed, KillSwitchBlockedError
from server_modules.gateway_quota_enforcement import (
    evaluate_gateway_quota,
    GATEWAY_CHANNEL_OUTBOUND,
)


_LIVE_GATEWAY_CONNECTIONS_BY_GATEWAY: Dict[str, "_LiveGatewayConnection"] = {}
_LIVE_GATEWAY_CONNECTIONS_BY_SESSION: Dict[str, "_LiveGatewayConnection"] = {}
_LIVE_GATEWAY_CONNECTIONS_LOCK = threading.Lock()
_LIVE_GATEWAY_STALE_SECONDS = (
    gateway_registry_service.DEFAULT_GATEWAY_HEARTBEAT_INTERVAL_SECONDS * 4
) + 10
_LIVE_GATEWAY_FRESH_INBOUND_MAX_AGE_SECONDS = float(
    gateway_registry_service.DEFAULT_GATEWAY_FRESH_HEARTBEAT_SECONDS
)
_LIVE_GATEWAY_FRESH_INBOUND_WAIT_SECONDS = 130.0
_LIVE_GATEWAY_RECONNECT_WAIT_SECONDS = 30.0
# How much of a session's TTL may burn down before a heartbeat bothers to
# persist a renewal. See the throttle at the gateway.heartbeat handler: every
# heartbeat used to write THREE rows (gateway session, auth session, runtime
# session) to renew a 15-minute TTL, at a 10-second cadence — ~90x more often
# than the TTL requires. On one long-lived gateway that produced 10k session
# rows and 271k events in a 2 GB SQLite file on a disk that also holds
# Postgres. Liveness is unaffected: staleness is decided from an in-memory
# time.monotonic() stamp (see _LiveGatewayConnection.is_stale), never from the
# persisted row, so throttling the WRITE does not slow dead-socket detection
# at all. Renewing with a third of the TTL still to run leaves a 5-minute
# safety margin against a 15-minute TTL — the same shape as Teleport, which
# refreshes a 10-minute presence TTL about once a minute rather than on every
# keep-alive.
_GATEWAY_SESSION_RENEW_AFTER_FRACTION = 2.0 / 3.0
_LIVE_GATEWAY_PROBE_TIMEOUT_SECONDS = 8
_LIVE_GATEWAY_SEND_TIMEOUT_SECONDS = 10.0
_LOGGER = logging.getLogger(__name__)

# Cross-connection response correlation for durable dispatch (see
# dispatch_tool_invoke_durable). _LiveGatewayConnection._pending_requests
# is scoped to ONE connection instance and force-failed the moment that
# connection dies (fail_pending) — correct for that connection's own
# requests (gateway.connect/heartbeat/probe, which are meaningless once
# their connection is gone), but wrong for a durable dispatch: the Gateway
# may finish the underlying work (e.g. codex exec, up to ~120s) and try to
# deliver the response on a LATER connection than the one that carried the
# original request. A waiter registered here survives that swap — whichever
# connection's receive loop sees a response frame for this request_id can
# resolve it, not just the one that sent the request.
_DURABLE_DISPATCH_WAITERS: Dict[str, _PendingGatewayRequest] = {}
_DURABLE_DISPATCH_WAITERS_LOCK = threading.Lock()


def _register_durable_waiter(request_id: str, pending: _PendingGatewayRequest) -> None:
    with _DURABLE_DISPATCH_WAITERS_LOCK:
        _DURABLE_DISPATCH_WAITERS[request_id] = pending


def _pop_durable_waiter(request_id: str) -> Optional["_PendingGatewayRequest"]:
    with _DURABLE_DISPATCH_WAITERS_LOCK:
        return _DURABLE_DISPATCH_WAITERS.pop(request_id, None)


@dataclass
class _PendingGatewayRequest:
    message_type: str
    future: asyncio.Future[Dict[str, Any]]
    loop: asyncio.AbstractEventLoop


@dataclass
class _QueuedGatewaySend:
    frame: Dict[str, Any]
    future: asyncio.Future[None]
    loop: asyncio.AbstractEventLoop


# Durable INBOUND delivery (the mirror of the gateway-side outbox).
#
# The response half of a durable dispatch already survives reconnects
# (_DURABLE_DISPATCH_WAITERS above). The DELIVERY half used to not: a
# tool.invoke was pushed at whatever socket happened to be live and, if none
# was fresh within the deadline, the whole turn failed — a timing race against
# the gateway's reconnect cycle. These invokes are instead enqueued per
# gateway_id and flushed onto the socket the moment the gateway is connected
# or heartbeats, so a command can no longer be lost just because the socket
# blinked at the wrong instant. Delivered exactly once (claim/release below);
# the gateway's own frame-id dedup + PendingResponseQueue handle the rest.
@dataclass
class _PendingInvoke:
    request_id: str
    gateway_id: str
    capability_id: str
    payload: Dict[str, Any]
    workspace_id: str
    run_id: str
    trace_id: str
    enforce_request_id: str
    # True only once a send has COMPLETED successfully — after that, nobody
    # ever resends (the gateway does not dedup inbound tool.invoke, so a
    # resend would double-execute codex).
    delivered: bool = False
    # The session currently attempting delivery, or None if free. A claim held
    # by one session can be PREEMPTED by a different session (see
    # _claim_pending_invoke) — a different session means the claim-holder's
    # connection is being superseded and its in-flight send was already killed
    # by _register_live_connection's close_stale, so it will not double-land.
    delivering_session: Optional[str] = None


_PENDING_GATEWAY_INVOKES: Dict[str, List["_PendingInvoke"]] = {}
_PENDING_GATEWAY_INVOKES_LOCK = threading.Lock()


def _enqueue_pending_invoke(pending: "_PendingInvoke") -> None:
    with _PENDING_GATEWAY_INVOKES_LOCK:
        _PENDING_GATEWAY_INVOKES.setdefault(pending.gateway_id, []).append(pending)


def _remove_pending_invoke(gateway_id: str, request_id: str) -> None:
    with _PENDING_GATEWAY_INVOKES_LOCK:
        queue = _PENDING_GATEWAY_INVOKES.get(gateway_id)
        if not queue:
            return
        queue[:] = [p for p in queue if p.request_id != request_id]
        if not queue:
            _PENDING_GATEWAY_INVOKES.pop(gateway_id, None)


def _snapshot_pending_invokes(gateway_id: str) -> List["_PendingInvoke"]:
    with _PENDING_GATEWAY_INVOKES_LOCK:
        return list(_PENDING_GATEWAY_INVOKES.get(gateway_id, ()))


def _claim_pending_invoke(pending: "_PendingInvoke", session_id: str) -> bool:
    # Claim delivery for `session_id`. Returns True if this session may send.
    #
    # Preemption is the whole point: if a DIFFERENT session already holds the
    # claim, take it over anyway. A different session exists only because the
    # gateway reconnected, which means the old claim-holder's connection was
    # superseded (and _register_live_connection.close_stale already failed its
    # in-flight send) — so the fresh connection must not be blocked waiting on
    # a doomed send that is hung against a half-open socket for up to the
    # 10s write timeout. Once `delivered` is set (a send actually completed),
    # nobody sends again.
    with _PENDING_GATEWAY_INVOKES_LOCK:
        if pending.delivered:
            return False
        if pending.delivering_session == session_id:
            return False  # this same session is already attempting
        pending.delivering_session = session_id
        return True


def _release_pending_invoke(pending: "_PendingInvoke", session_id: str) -> None:
    # A delivery attempt by `session_id` failed — release the claim, but ONLY
    # if it is still ours (a newer session may have preempted us meanwhile).
    with _PENDING_GATEWAY_INVOKES_LOCK:
        if pending.delivering_session == session_id:
            pending.delivering_session = None


def _mark_pending_invoke_delivered(pending: "_PendingInvoke") -> None:
    with _PENDING_GATEWAY_INVOKES_LOCK:
        pending.delivered = True


# ── Streaming deltas (Phase 2) ───────────────────────────────────────────────
# The Gateway may push zero or more `tool.invoke.chunk` EVENT frames for an
# in-flight durable invoke, correlated by request_id — the same id the
# eventual `response` frame resolves. This is deliberately NOT part of the
# request/response future machinery above: a chunk is fire-and-forget, never
# awaited, never retried, and losing one changes nothing about correctness —
# the durable tool.invoke/response pair remains the sole source of truth for
# the actual result. A sink is only registered while dispatch_tool_invoke_durable
# is actually awaiting a response for that request_id; any chunk that arrives
# after (a late/duplicate frame) finds nothing registered and is silently
# dropped, matching resolve_response()'s own "already popped" behavior above.
_PENDING_DELTA_SINKS: Dict[str, Callable[[str], None]] = {}
_PENDING_DELTA_SINKS_LOCK = threading.Lock()


def _register_delta_sink(request_id: str, sink: Callable[[str], None]) -> None:
    with _PENDING_DELTA_SINKS_LOCK:
        _PENDING_DELTA_SINKS[request_id] = sink


def _unregister_delta_sink(request_id: str) -> None:
    with _PENDING_DELTA_SINKS_LOCK:
        _PENDING_DELTA_SINKS.pop(request_id, None)


def _deliver_delta_to_sink(payload: Dict[str, Any]) -> None:
    request_id = str(payload.get("request_id") or "").strip()
    if not request_id:
        return
    with _PENDING_DELTA_SINKS_LOCK:
        sink = _PENDING_DELTA_SINKS.get(request_id)
    if sink is None:
        return
    delta = str(payload.get("delta") or "")
    if not delta:
        return
    try:
        sink(delta)
    except Exception:
        pass  # a broken sink must never break the gateway's receive loop


def _future_set_result_threadsafe(future: asyncio.Future[Any], loop: asyncio.AbstractEventLoop, value: Any) -> None:
    if future.done() or loop.is_closed():
        return
    loop.call_soon_threadsafe(future.set_result, value)


def _future_set_exception_threadsafe(
    future: asyncio.Future[Any],
    loop: asyncio.AbstractEventLoop,
    exception: BaseException,
) -> None:
    if future.done() or loop.is_closed():
        return
    loop.call_soon_threadsafe(future.set_exception, exception)


class GatewayFrameValidationError(ValueError):
    def __init__(self, *, error_code: str, reason: str, close_code: int = 4408) -> None:
        super().__init__(reason)
        self.error_code = str(error_code or "invalid_gateway_frame").strip() or "invalid_gateway_frame"
        self.reason = str(reason or "Invalid gateway frame.").strip() or "Invalid gateway frame."
        self.close_code = int(close_code)


class GatewayProtocolRustGateError(RuntimeError):
    pass


_GATEWAY_PROTOCOL_REQUEST_NEXT_ACTIONS = {
    "send_gateway_protocol_request_frame": {"send_gateway_protocol_request_frame"},
}
_GATEWAY_PROTOCOL_MESSAGE_NEXT_ACTIONS = {
    "tool.invoke": "dispatch_tool_invoke",
    "tool.interrupt": "dispatch_tool_interrupt",
    "channel.outbound": "dispatch_channel_outbound",
}

_GATEWAY_SESSION_MUTATION_NEXT_ACTIONS = {
    "mark_session_connected": {"mark_gateway_session_connected"},
    "mark_session_disconnected": {"mark_gateway_session_disconnected"},
    "touch_session": {"touch_gateway_session"},
}


def _payload_size_bytes(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8"))
    except Exception:
        return len(str(value or "").encode("utf-8"))


def _enforce_gateway_protocol_request_frame(
    *,
    gateway_id: str,
    session_id: str,
    message_type: str,
    frame: Dict[str, Any],
) -> Dict[str, Any]:
    scope = frame.get("scope") if isinstance(frame.get("scope"), dict) else {}
    payload = frame.get("payload") if isinstance(frame.get("payload"), dict) else {}
    workspace_id = str(scope.get("workspace_id") or payload.get("workspace_id") or "").strip()
    run_id = str(payload.get("run_id") or "").strip()
    metadata = {
        "gateway_id": str(gateway_id or "").strip(),
        "session_id": str(session_id or "").strip(),
        "message_type": str(message_type or "").strip(),
        "frame_id": str(frame.get("id") or "").strip(),
        "workspace_id": workspace_id,
        "run_id": run_id,
        "payload_keys": sorted(str(key) for key in payload.keys())[:80],
    }
    try:
        decision = rust_runtime_kernel_client.runtime_state_store_decision(
            operation="send_gateway_protocol_request_frame",
            state_class="gateway_protocol_frames",
            workspace_id=workspace_id,
            run_id=run_id,
            actor_id="system",
            status="active",
            payload=metadata,
            payload_bytes=_payload_size_bytes(frame),
            workspace_access=True,
            owner_access=True,
        )
        enforced = rust_runtime_kernel_client.enforce_kernel_decision(
            "runtime-state-store-decision",
            decision,
        )
        next_action = str(enforced.get("next_action") or "").strip()
        allowed_next_actions = _GATEWAY_PROTOCOL_REQUEST_NEXT_ACTIONS["send_gateway_protocol_request_frame"]
        if next_action not in allowed_next_actions:
            expected = ", ".join(sorted(allowed_next_actions))
            raise GatewayProtocolRustGateError(
                f"unexpected next_action for runtime-state-store-decision: {next_action or '<missing>'} "
                f"(expected {expected})"
            )
        return enforced
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise GatewayProtocolRustGateError(exc.reason) from exc


def _enforce_gateway_protocol_message_decision(
    *,
    gateway_id: str,
    session_id: str,
    workspace_id: str,
    message_type: str,
    payload: Dict[str, Any],
    tool_name: str | None = None,
) -> Dict[str, Any]:
    resolved_message_type = str(message_type or "").strip()
    expected_next_action = _GATEWAY_PROTOCOL_MESSAGE_NEXT_ACTIONS.get(resolved_message_type)
    if expected_next_action is None:
        raise GatewayProtocolRustGateError(
            f"unexpected gateway protocol message_type for Rust gate: {resolved_message_type or '<missing>'}"
        )
    rust_payload: Dict[str, Any] = {
        "gateway_id": str(gateway_id or "").strip(),
        "session_id": str(session_id or "").strip(),
        "workspace_id": str(workspace_id or "").strip() or "default",
        "message_type": resolved_message_type,
        "authenticated": True,
        "protocol_version": "gateway.v1",
        "payload": dict(payload or {}),
    }
    resolved_tool_name = str(tool_name or "").strip()
    if resolved_tool_name:
        rust_payload["tool_name"] = resolved_tool_name
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "gateway-protocol-decision",
            rust_payload,
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise GatewayProtocolRustGateError(exc.reason) from exc
    next_action = str(decision.get("next_action") or "").strip()
    if next_action != expected_next_action:
        raise GatewayProtocolRustGateError(
            f"unexpected next_action for gateway-protocol-decision: {next_action or '<missing>'}"
        )
    return decision


def _enforce_gateway_session_mutation(
    *,
    registration: Dict[str, Any] | None,
    session: Dict[str, Any] | None,
    operation: str,
    reason: str | None = None,
) -> Dict[str, Any]:
    registration = registration if isinstance(registration, dict) else {}
    session = session if isinstance(session, dict) else {}
    gateway_id = str(
        registration.get("gateway_id")
        or session.get("gateway_id")
        or ""
    ).strip()
    session_id = str(session.get("session_id") or "").strip()
    workspace_id = str(
        registration.get("workspace_id")
        or session.get("workspace_id")
        or ""
    ).strip()
    tenant_id = str(
        registration.get("tenant_id")
        or session.get("tenant_id")
        or ""
    ).strip()
    user_id = str(
        registration.get("user_id")
        or session.get("user_id")
        or ""
    ).strip()
    device_id = str(
        registration.get("device_id")
        or session.get("device_id")
        or ""
    ).strip()
    status = str(
        session.get("status")
        or session.get("session_status")
        or registration.get("status")
        or "pending"
    ).strip() or "pending"
    metadata = {
        "gateway_id": gateway_id,
        "session_id": session_id,
        "workspace_id": workspace_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "device_id": device_id,
        "reason": str(reason or "").strip(),
    }
    try:
        decision = rust_runtime_kernel_client.gateway_state_decision(
            operation=operation,
            gateway_id=gateway_id,
            session_id=session_id,
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            user_id=user_id,
            device_id=device_id,
            status=status,
            payload=metadata,
            is_service=True,
        )
        enforced = rust_runtime_kernel_client.enforce_kernel_decision(
            "gateway-state-decision",
            decision,
        )
        allowed_next_actions = _GATEWAY_SESSION_MUTATION_NEXT_ACTIONS.get(operation)
        next_action = str(enforced.get("next_action") or "").strip()
        if allowed_next_actions is None:
            raise GatewayProtocolRustGateError(f"unexpected gateway session mutation operation: {operation}")
        if next_action not in allowed_next_actions:
            expected = ", ".join(sorted(allowed_next_actions))
            raise GatewayProtocolRustGateError(
                f"unexpected next_action for gateway-state-decision: {next_action or '<missing>'} "
                f"(expected {expected})"
            )
        return enforced
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise GatewayProtocolRustGateError(exc.reason) from exc


class _LiveGatewayConnection:
    def __init__(
        self,
        *,
        websocket: WebSocket,
        gateway_id: str,
        session_id: str,
        scope: Dict[str, str],
    ) -> None:
        self.websocket = websocket
        self.gateway_id = str(gateway_id or "").strip()
        self.session_id = str(session_id or "").strip()
        self.scope = dict(scope or {})
        self._pending_requests: Dict[str, _PendingGatewayRequest] = {}
        self._seen_inbound_frame_ids: set[str] = set()
        self._frame_response_cache: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._send_lock = asyncio.Lock()
        self._outbound_queue: Optional[asyncio.Queue[_QueuedGatewaySend]] = None
        self._owner_loop: Optional[asyncio.AbstractEventLoop] = None
        self._owner_task: Optional[asyncio.Task[Any]] = None
        self._writer_task: Optional[asyncio.Task[Any]] = None
        self._closed = False
        self._last_inbound_monotonic = time.monotonic()
        # When this connection last PERSISTED a session-TTL renewal. Separate
        # from _last_inbound_monotonic (which is liveness and updates on every
        # frame) because the two have different jobs: liveness must be exact
        # and is free, TTL renewal is three database writes and only has to
        # happen before the TTL runs out. See _GATEWAY_SESSION_RENEW_AFTER_FRACTION.
        self._last_session_renew_monotonic = 0.0

    def session_renewal_due(self, ttl_seconds: float) -> bool:
        """True when the persisted session TTL should be renewed now.

        First call on a connection always returns True, so a fresh socket
        writes its renewal immediately and never inherits a stale expiry.
        """
        if self._last_session_renew_monotonic <= 0.0:
            return True
        elapsed = time.monotonic() - self._last_session_renew_monotonic
        return elapsed >= max(float(ttl_seconds), 1.0) * _GATEWAY_SESSION_RENEW_AFTER_FRACTION

    def note_session_renewed(self) -> None:
        self._last_session_renew_monotonic = time.monotonic()

    def start_writer(self) -> None:
        self._owner_loop = asyncio.get_running_loop()
        self._owner_task = asyncio.current_task()
        self._outbound_queue = asyncio.Queue(maxsize=256)
        self._writer_task = asyncio.create_task(self._outbound_writer_loop())

    async def send_request(
        self,
        *,
        message_type: str,
        payload: Dict[str, Any],
        timeout_seconds: int,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if self._closed:
            raise RuntimeError("Gateway connection is no longer active.")
        if self.is_stale():
            raise RuntimeError("Gateway connection heartbeat is stale.")
        resolved_request_id = str(request_id or f"greq_{uuid.uuid4().hex}").strip()
        frame = {
            "kind": "request",
            "id": resolved_request_id,
            "type": str(message_type or "").strip(),
            "ts": gateway_state_repository._utc_now_iso(),
            "scope": dict(self.scope),
            "payload": dict(payload or {}),
        }
        _enforce_gateway_protocol_request_frame(
            gateway_id=self.gateway_id,
            session_id=self.session_id,
            message_type=str(message_type or "").strip() or "unknown",
            frame=frame,
        )
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Dict[str, Any]] = loop.create_future()
        self._pending_requests[resolved_request_id] = _PendingGatewayRequest(
            message_type=str(message_type or "").strip() or "unknown",
            future=future,
            loop=loop,
        )
        gateway_state_repository.record_gateway_event(
            gateway_id=self.gateway_id,
            session_id=self.session_id,
            direction="outbound",
            frame_kind="request",
            message_type=str(message_type or "").strip() or "unknown",
            payload=frame,
        )
        await self.send_frame(frame)
        try:
            return await asyncio.wait_for(future, timeout=max(int(timeout_seconds or 0), 1))
        finally:
            self._pending_requests.pop(resolved_request_id, None)

    async def send_frame(self, frame: Dict[str, Any]) -> None:
        if self._closed:
            raise RuntimeError("Gateway connection is no longer active.")
        owner_loop = self._owner_loop
        try:
            caller_loop = asyncio.get_running_loop()
        except RuntimeError:
            caller_loop = None
        if owner_loop is None or caller_loop is owner_loop:
            await self._write_frame_direct(frame)
            return

        if owner_loop is None or not owner_loop.is_running():
            raise RuntimeError("Gateway websocket owner loop is not active.")
        if caller_loop is None:
            raise RuntimeError("Gateway websocket send requires an active caller event loop.")
        future: asyncio.Future[None] = caller_loop.create_future()
        item = _QueuedGatewaySend(frame=dict(frame or {}), future=future, loop=caller_loop)

        def enqueue() -> None:
            if self._closed:
                _future_set_exception_threadsafe(
                    item.future,
                    item.loop,
                    RuntimeError("Gateway connection is no longer active."),
                )
                return
            if self._outbound_queue is None:
                _future_set_exception_threadsafe(
                    item.future,
                    item.loop,
                    RuntimeError("Gateway websocket outbound queue is not active."),
                )
                return
            try:
                self._outbound_queue.put_nowait(item)
                _future_set_result_threadsafe(item.future, item.loop, None)
            except asyncio.QueueFull:
                _future_set_exception_threadsafe(
                    item.future,
                    item.loop,
                    RuntimeError("Gateway websocket outbound queue is full."),
                )

        owner_loop.call_soon_threadsafe(enqueue)
        try:
            await asyncio.wait_for(future, timeout=_LIVE_GATEWAY_SEND_TIMEOUT_SECONDS)
        except asyncio.TimeoutError as exc:
            self._fail_pending_threadsafe("Gateway websocket send timed out.")
            raise RuntimeError("Gateway websocket send timed out.") from exc

    async def wait_for_queued_send(self) -> _QueuedGatewaySend:
        if self._outbound_queue is None:
            raise RuntimeError("Gateway websocket outbound queue is not active.")
        return await self._outbound_queue.get()

    async def drain_queued_sends(self) -> None:
        if self._outbound_queue is None:
            return
        while True:
            try:
                item = self._outbound_queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            await self.write_queued_frame(item)
            self._outbound_queue.task_done()

    async def _outbound_writer_loop(self) -> None:
        queue = self._outbound_queue
        if queue is None:
            return
        try:
            while not self._closed:
                item = await queue.get()
                try:
                    await self.write_queued_frame(item)
                finally:
                    queue.task_done()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.fail_pending(str(exc) or "Gateway socket writer failed.")

    async def write_queued_frame(self, item: _QueuedGatewaySend) -> None:
        if self._closed:
            _future_set_exception_threadsafe(
                item.future,
                item.loop,
                RuntimeError("Gateway connection is no longer active."),
            )
            return
        try:
            await self._write_frame_direct(item.frame)
        except Exception as exc:
            message = str(exc) or "Gateway socket write failed."
            _future_set_exception_threadsafe(item.future, item.loop, RuntimeError(message))
            self.fail_pending(message)
            raise
        _future_set_result_threadsafe(item.future, item.loop, None)

    def _fail_pending_threadsafe(self, reason: str) -> None:
        owner_loop = self._owner_loop
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if owner_loop is not None and owner_loop is not current_loop:
            owner_loop.call_soon_threadsafe(self.fail_pending, reason)
            return
        self.fail_pending(reason)

    async def _write_frame_direct(self, frame: Dict[str, Any]) -> None:
        frame_type = str(frame.get("type") or frame.get("kind") or "unknown")
        frame_id = str(frame.get("id") or "")
        lock_acquired = False
        try:
            encoded = json.dumps(dict(frame or {}), ensure_ascii=False, separators=(",", ":"), default=str)
            await asyncio.wait_for(self._send_lock.acquire(), timeout=5.0)
            lock_acquired = True
            await asyncio.wait_for(
                self.websocket.send_text(encoded),
                timeout=_LIVE_GATEWAY_SEND_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as exc:
            _LOGGER.warning(
                "Gateway websocket send timeout gateway_id=%s session_id=%s frame_type=%s frame_id=%s lock_acquired=%s",
                self.gateway_id,
                self.session_id,
                frame_type,
                frame_id,
                lock_acquired,
            )
            self.fail_pending("Gateway websocket send timed out.")
            raise
        except Exception as exc:
            _LOGGER.warning(
                "Gateway websocket send failed gateway_id=%s session_id=%s frame_type=%s frame_id=%s error=%s",
                self.gateway_id,
                self.session_id,
                frame_type,
                frame_id,
                str(exc) or type(exc).__name__,
            )
            self.fail_pending(str(exc) or "Gateway socket write failed.")
            raise
        finally:
            if lock_acquired:
                self._send_lock.release()

    def resolve_response(self, frame: Dict[str, Any]) -> str:
        self.mark_inbound_frame()
        request_id = str(frame.get("id") or "").strip()
        pending = self._pending_requests.pop(request_id, None)
        if pending is not None:
            _future_set_result_threadsafe(pending.future, pending.loop, dict(frame or {}))
            return pending.message_type
        # Not tracked on THIS connection instance — either an untracked/
        # replayed response, or a durable dispatch whose original connection
        # died mid-wait and is only now being answered on this later one.
        durable = _pop_durable_waiter(request_id)
        if durable is not None:
            _future_set_result_threadsafe(durable.future, durable.loop, dict(frame or {}))
            return durable.message_type
        return "response"

    def remember_inbound_frame_id(self, frame_id: Any) -> bool:
        self.mark_inbound_frame()
        normalized = str(frame_id or "").strip()
        if not normalized:
            return True
        if normalized in self._seen_inbound_frame_ids:
            return False
        if len(self._seen_inbound_frame_ids) >= 4096:
            import logging
            logging.warning("Inbound frame ID set reached capacity (%d); clearing oldest 2048 entries", len(self._seen_inbound_frame_ids))
            self._seen_inbound_frame_ids = set(list(self._seen_inbound_frame_ids)[2048:])
        self._seen_inbound_frame_ids.add(normalized)
        return True

    def cache_frame_response(self, frame_id: str, response: Dict[str, Any]) -> None:
        self._frame_response_cache[frame_id] = response
        self._frame_response_cache.move_to_end(frame_id)
        if len(self._frame_response_cache) > 2048:
            self._frame_response_cache.popitem(last=False)

    def get_cached_frame_response(self, frame_id: str) -> Optional[Dict[str, Any]]:
        return self._frame_response_cache.get(frame_id)

    def mark_inbound_frame(self) -> None:
        self._last_inbound_monotonic = time.monotonic()

    def last_inbound_age_seconds(self) -> float:
        return max(time.monotonic() - self._last_inbound_monotonic, 0.0)

    def has_recent_inbound_frame(self) -> bool:
        return self.last_inbound_age_seconds() <= _LIVE_GATEWAY_FRESH_INBOUND_MAX_AGE_SECONDS

    def is_stale(self) -> bool:
        if self._closed:
            return True
        return (time.monotonic() - self._last_inbound_monotonic) > _LIVE_GATEWAY_STALE_SECONDS

    async def close_stale(self, reason: str) -> None:
        self.fail_pending(reason)
        try:
            await self.websocket.close(code=4408, reason=reason[:120])
        except Exception:
            pass

    def fail_pending(self, reason: str) -> None:
        if self._closed:
            return
        self._closed = True
        writer_task = self._writer_task
        if writer_task is not None and not writer_task.done():
            current_loop: asyncio.AbstractEventLoop | None = None
            current_task: asyncio.Task[Any] | None = None
            try:
                current_loop = asyncio.get_running_loop()
                current_task = asyncio.current_task()
            except RuntimeError:
                current_loop = None
                current_task = None

            if writer_task is not current_task:
                owner_loop = self._owner_loop

                def _cancel_writer() -> None:
                    if not writer_task.done():
                        writer_task.cancel()

                if owner_loop is not None and owner_loop.is_running() and current_loop is not owner_loop:
                    owner_loop.call_soon_threadsafe(_cancel_writer)
                else:
                    _cancel_writer()
        outbound_queue = self._outbound_queue
        if outbound_queue is not None:
            while True:
                try:
                    item = outbound_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                _future_set_exception_threadsafe(
                    item.future,
                    item.loop,
                    RuntimeError(str(reason or "Gateway connection closed.")),
                )
        for pending in list(self._pending_requests.values()):
            _future_set_exception_threadsafe(
                pending.future,
                pending.loop,
                RuntimeError(str(reason or "Gateway connection closed.")),
            )
        self._pending_requests.clear()


async def _register_live_connection(connection: _LiveGatewayConnection) -> None:
    stale: Optional[_LiveGatewayConnection] = None
    with _LIVE_GATEWAY_CONNECTIONS_LOCK:
        existing = _LIVE_GATEWAY_CONNECTIONS_BY_GATEWAY.get(connection.gateway_id)
        if existing is not None and existing is not connection:
            stale = existing
            _LIVE_GATEWAY_CONNECTIONS_BY_SESSION.pop(existing.session_id, None)
        _LIVE_GATEWAY_CONNECTIONS_BY_GATEWAY[connection.gateway_id] = connection
        _LIVE_GATEWAY_CONNECTIONS_BY_SESSION[connection.session_id] = connection
    if stale is not None:
        # Evict the old socket outright so exactly one live connection can
        # ever answer for this gateway_id — otherwise the old connection's
        # own eventual teardown could pop the map entry this new connection
        # just wrote (see _unregister_live_connection's identity guard).
        await stale.close_stale("Superseded by a newer connection for this gateway.")


def _unregister_live_connection(*, gateway_id: str, session_id: str, reason: str) -> None:
    gid = str(gateway_id or "").strip()
    sid = str(session_id or "").strip()
    with _LIVE_GATEWAY_CONNECTIONS_LOCK:
        connection = _LIVE_GATEWAY_CONNECTIONS_BY_SESSION.pop(sid, None)
        current = _LIVE_GATEWAY_CONNECTIONS_BY_GATEWAY.get(gid)
        # Only remove the gateway_id entry if it still points at THIS
        # connection (or this session, if the connection object was already
        # gone) — a stale connection tearing down after a reconnect must
        # never pop the newer connection that already replaced it.
        if current is not None and (current is connection or (connection is None and current.session_id == sid)):
            _LIVE_GATEWAY_CONNECTIONS_BY_GATEWAY.pop(gid, None)
    if connection is not None:
        connection.fail_pending(reason)


def _get_live_connection(gateway_id: str) -> Optional[_LiveGatewayConnection]:
    with _LIVE_GATEWAY_CONNECTIONS_LOCK:
        return _LIVE_GATEWAY_CONNECTIONS_BY_GATEWAY.get(str(gateway_id or "").strip())


def _connection_is_stale(connection: Any) -> bool:
    checker = getattr(connection, "is_stale", None)
    if callable(checker):
        return bool(checker())
    return False


def _connection_has_recent_inbound_frame(connection: Any) -> bool:
    checker = getattr(connection, "has_recent_inbound_frame", None)
    if callable(checker):
        return bool(checker())
    age_getter = getattr(connection, "last_inbound_age_seconds", None)
    if callable(age_getter):
        try:
            return float(age_getter()) <= _LIVE_GATEWAY_FRESH_INBOUND_MAX_AGE_SECONDS
        except Exception:
            return False
    return True


def gateway_connection_is_live(gateway_id: str) -> bool:
    return _get_live_connection(gateway_id) is not None


async def _fresh_live_connection(
    gateway_id: str,
    *,
    require_recent_inbound: bool = False,
    deadline: Optional[float] = None,
) -> _LiveGatewayConnection:
    connection = _get_live_connection(gateway_id)
    if connection is None:
        raise ValueError("Gateway is not currently connected.")
    if require_recent_inbound:
        fresh = await _wait_for_recent_inbound_frame(gateway_id, deadline=deadline)
        if fresh is not None:
            return fresh
        return await _close_and_wait_for_fresh_connection(
            gateway_id=gateway_id,
            connection=_get_live_connection(gateway_id) or connection,
            reason="gateway heartbeat not recent before dispatch",
            deadline=deadline,
        )
    if not _connection_is_stale(connection):
        return connection
    return await _close_and_wait_for_fresh_connection(
        gateway_id=gateway_id,
        connection=connection,
        reason="gateway heartbeat stale before dispatch",
        deadline=deadline,
    )


async def _wait_for_recent_inbound_frame(
    gateway_id: str,
    *,
    deadline: Optional[float] = None,
) -> Optional[_LiveGatewayConnection]:
    # Root cause confirmed 2026-07-13: this used to watch a single captured
    # connection object for its whole wait, so a reconnect that landed a
    # fresh connection in the map mid-wait went unnoticed — this kept
    # polling the old, now-replaced object until its own timeout, even
    # though a fresh one was already sitting in _get_live_connection.
    # Re-read the map each tick instead, and cap the wait at whatever
    # budget the caller's outer deadline has left, not a fixed ceiling that
    # can alone exceed the whole dispatch's durable_deadline_seconds.
    effective_deadline = time.monotonic() + _LIVE_GATEWAY_FRESH_INBOUND_WAIT_SECONDS
    if deadline is not None:
        effective_deadline = min(effective_deadline, deadline)
    while time.monotonic() < effective_deadline:
        current = _get_live_connection(gateway_id)
        if current is not None and _connection_has_recent_inbound_frame(current):
            return current
        await asyncio.sleep(0.2)
    current = _get_live_connection(gateway_id)
    if current is not None and _connection_has_recent_inbound_frame(current):
        return current
    return None


async def _close_and_wait_for_fresh_connection(
    *,
    gateway_id: str,
    connection: _LiveGatewayConnection,
    reason: str,
    deadline: Optional[float] = None,
) -> _LiveGatewayConnection:
    previous_session_id = connection.session_id
    await connection.close_stale(reason)
    _unregister_live_connection(
        gateway_id=gateway_id,
        session_id=previous_session_id,
        reason=reason,
    )
    effective_deadline = time.monotonic() + _LIVE_GATEWAY_RECONNECT_WAIT_SECONDS
    if deadline is not None:
        effective_deadline = min(effective_deadline, deadline)
    while time.monotonic() < effective_deadline:
        candidate = _get_live_connection(gateway_id)
        if (
            candidate is not None
            and candidate.session_id != previous_session_id
            and not _connection_is_stale(candidate)
            and _connection_has_recent_inbound_frame(candidate)
        ):
            return candidate
        await asyncio.sleep(0.2)
    raise ValueError("Gateway connection is stale and has not reconnected yet.")


async def _probe_live_connection(connection: _LiveGatewayConnection) -> None:
    response = await connection.send_request(
        message_type="gateway.probe",
        payload={"purpose": "pre_dispatch"},
        timeout_seconds=_LIVE_GATEWAY_PROBE_TIMEOUT_SECONDS,
        request_id=f"gprobe_{uuid.uuid4().hex}",
    )
    if not bool(response.get("ok")):
        error = dict(response.get("error") or {})
        raise RuntimeError(
            str(error.get("message") or "Gateway probe failed.").strip()
            or "Gateway probe failed."
        )


async def _connection_ready_for_tool_dispatch(
    *,
    gateway_id: str,
    connection: _LiveGatewayConnection,
) -> _LiveGatewayConnection:
    if _connection_is_stale(connection):
        return await _close_and_wait_for_fresh_connection(
            gateway_id=gateway_id,
            connection=connection,
            reason="gateway heartbeat stale before dispatch",
        )
    try:
        await _probe_live_connection(connection)
        return connection
    except Exception:
        return await _close_and_wait_for_fresh_connection(
            gateway_id=gateway_id,
            connection=connection,
            reason="gateway probe failed before dispatch",
        )


async def dispatch_tool_invoke(
    *,
    gateway_id: str,
    capability_id: str,
    arguments: Optional[Dict[str, Any]],
    run_id: str,
    trace_id: str,
    workspace_id: str,
    timeout_seconds: int = DEFAULT_TOOL_REQUEST_TIMEOUT_SECONDS,
    request_id: Optional[str] = None,
    runtime_access_mode: Optional[str] = None,
    empyralis_approved: bool = False,
    agent_scope: Optional[str] = None,
    policy: Optional[Dict[str, Any]] = None,
    actor_id: str = "",
) -> Dict[str, Any]:
    assert_not_killed(gateway_id=gateway_id, trace_id=trace_id)
    connection = await _fresh_live_connection(gateway_id, require_recent_inbound=True)
    registration = gateway_state_repository.get_gateway_registration(gateway_id) or {}
    _enforce_gateway_quota_check(
        gateway_id=str(gateway_id or "").strip(),
        session_id=str(connection.session_id or "").strip(),
        workspace_id=str(workspace_id or "").strip(),
        tenant_id=str(registration.get("tenant_id") or connection.scope.get("tenant_id") or "").strip(),
        device_id=str(registration.get("device_id") or "").strip(),
        request_id=str(request_id or trace_id or run_id or "").strip(),
        quota_profile="gateway_tool_execution",
    )
    _enforce_gateway_tool_execute_protocol_route(
        gateway_id=str(gateway_id or "").strip(),
        session_id=str(connection.session_id or "").strip(),
        workspace_id=str(workspace_id or "").strip(),
        tenant_id=str(registration.get("tenant_id") or connection.scope.get("tenant_id") or "").strip(),
        device_id=str(registration.get("device_id") or "").strip(),
        capability_id=str(capability_id or "").strip(),
        run_id=str(run_id or "").strip(),
        trace_id=str(trace_id or "").strip(),
        request_id=str(request_id or trace_id or run_id or "").strip(),
    )
    payload = {
        "capability_id": str(capability_id or "").strip(),
        "arguments": dict(arguments or {}),
        "run_id": str(run_id or "").strip(),
        "trace_id": str(trace_id or "").strip(),
        "workspace_id": str(workspace_id or "").strip(),
    }
    if str(runtime_access_mode or "").strip():
        payload["runtime_access_mode"] = str(runtime_access_mode or "").strip()
    if empyralis_approved:
        payload["empyralis_approved"] = True
    if str(agent_scope or "").strip():
        payload["agent_scope"] = str(agent_scope or "").strip()
    if isinstance(policy, dict):
        payload["policy"] = dict(policy)
    _enforce_gateway_protocol_message_decision(
        gateway_id=str(gateway_id or "").strip(),
        session_id=str(connection.session_id or "").strip(),
        workspace_id=str(workspace_id or "").strip(),
        message_type="tool.invoke",
        payload=payload,
        tool_name=str(capability_id or "").strip(),
    )
    try:
        response = await connection.send_request(
            message_type="tool.invoke",
            payload=payload,
            timeout_seconds=timeout_seconds,
            request_id=request_id,
        )
    except RuntimeError as exc:
        # The connection can die between _fresh_live_connection returning it
        # and the send actually going out (e.g. the socket closes mid-call).
        # Routes already turn a plain ValueError with this text into a clean
        # 409 (routes_gateway.py) instead of an unhandled 500.
        raise ValueError("Gateway is not currently connected.") from exc
    if not bool(response.get("ok")):
        error = dict(response.get("error") or {})
        raise ValueError(str(error.get("message") or "Gateway tool invocation failed.").strip() or "Gateway tool invocation failed.")
    # ── Phase K: gateway hardware ledger (redacted, best-effort) ─────
    _resolved_actor = str(actor_id or "").strip() or "sage"
    try:
        from server_modules import ledger_audit as _la
        import asyncio as _aio
        async def _ledger():
            await _la.record_gateway_hardware_invoke(
                workspace_id=str(workspace_id or "").strip(),
                actor_id=_resolved_actor,
                capability_id=str(capability_id or "").strip(),
                arguments=dict(arguments or {}),
                status="executed",
                trace_id=str(trace_id or "").strip() or None,
                run_id=str(run_id or "").strip() or None,
            )
        try:
            _aio.get_running_loop()
            _aio.create_task(_ledger())
        except RuntimeError:
            _aio.run(_ledger())
    except Exception:
        pass  # ledger is best-effort, never blocks execution
    return dict(response.get("payload") or {})


_DURABLE_DISPATCH_DEFAULT_DEADLINE_SECONDS = 240  # ~4 minutes


async def _deliver_tool_invoke(connection: _LiveGatewayConnection, pending: _PendingInvoke) -> None:
    """Run the per-send enforcement gates against `connection` and write the
    tool.invoke frame. Raises on any enforcement failure or write error — the
    caller decides whether to leave the invoke enqueued for a later retry. The
    enforcement checks must run here (not at enqueue time) because they need a
    concrete live connection's session_id/scope, and the delivering connection
    may not be the one that existed when the dispatch started."""
    gateway_id = pending.gateway_id
    registration = gateway_state_repository.get_gateway_registration(gateway_id) or {}
    _enforce_gateway_quota_check(
        gateway_id=str(gateway_id or "").strip(),
        session_id=str(connection.session_id or "").strip(),
        workspace_id=str(pending.workspace_id or "").strip(),
        tenant_id=str(registration.get("tenant_id") or connection.scope.get("tenant_id") or "").strip(),
        device_id=str(registration.get("device_id") or "").strip(),
        request_id=pending.enforce_request_id,
        quota_profile="gateway_tool_execution",
    )
    _enforce_gateway_tool_execute_protocol_route(
        gateway_id=str(gateway_id or "").strip(),
        session_id=str(connection.session_id or "").strip(),
        workspace_id=str(pending.workspace_id or "").strip(),
        tenant_id=str(registration.get("tenant_id") or connection.scope.get("tenant_id") or "").strip(),
        device_id=str(registration.get("device_id") or "").strip(),
        capability_id=str(pending.capability_id or "").strip(),
        run_id=str(pending.run_id or "").strip(),
        trace_id=str(pending.trace_id or "").strip(),
        request_id=pending.enforce_request_id,
    )
    _enforce_gateway_protocol_message_decision(
        gateway_id=str(gateway_id or "").strip(),
        session_id=str(connection.session_id or "").strip(),
        workspace_id=str(pending.workspace_id or "").strip(),
        message_type="tool.invoke",
        payload=pending.payload,
        tool_name=str(pending.capability_id or "").strip(),
    )
    frame = {
        "kind": "request",
        "id": pending.request_id,
        "type": "tool.invoke",
        "ts": gateway_state_repository._utc_now_iso(),
        "scope": dict(connection.scope),
        "payload": dict(pending.payload),
    }
    _enforce_gateway_protocol_request_frame(
        gateway_id=str(gateway_id or "").strip(),
        session_id=str(connection.session_id or "").strip(),
        message_type="tool.invoke",
        frame=frame,
    )
    await connection.send_frame(frame)
    gateway_state_repository.record_gateway_event(
        gateway_id=str(gateway_id or "").strip(),
        session_id=str(connection.session_id or "").strip(),
        direction="outbound",
        frame_kind="request",
        message_type="tool.invoke",
        payload=frame,
    )


async def _deliver_pending_invoke_via(connection: _LiveGatewayConnection, pending: _PendingInvoke) -> bool:
    """Claim, deliver, and dequeue a pending invoke over `connection`. Returns
    True if THIS caller delivered it, False if it was already delivered or is
    being attempted by this same session. `delivered` is set only after the
    send COMPLETES, so a hung/failed send on a superseded connection leaves the
    invoke deliverable — a fresh connection preempts the claim (see
    _claim_pending_invoke) rather than waiting on the doomed send."""
    session_id = str(connection.session_id or "").strip()
    if not _claim_pending_invoke(pending, session_id):
        return False
    try:
        await _deliver_tool_invoke(connection, pending)
    except Exception:
        _release_pending_invoke(pending, session_id)
        raise
    _mark_pending_invoke_delivered(pending)
    _remove_pending_invoke(pending.gateway_id, pending.request_id)
    return True


async def _flush_pending_invokes(gateway_id: str, connection: _LiveGatewayConnection) -> None:
    """Deliver any invokes queued for this gateway onto a just-connected /
    freshly-heartbeating socket. Best-effort per invoke: a failure leaves that
    invoke enqueued for the next connect/heartbeat, and the dispatcher's own
    deadline bounds how long a truly-unreachable gateway keeps one alive."""
    for pending in _snapshot_pending_invokes(gateway_id):
        try:
            await _deliver_pending_invoke_via(connection, pending)
        except Exception as exc:
            _LOGGER.info(
                "Pending tool.invoke flush deferred gateway_id=%s request_id=%s error=%s",
                gateway_id,
                pending.request_id,
                str(exc) or type(exc).__name__,
            )


def _kick_immediate_flush(gateway_id: str) -> None:
    """Deliver a just-enqueued invoke NOW instead of waiting up to a full
    heartbeat interval for the next heartbeat-flush. The flush is scheduled on
    the live connection's OWN owner loop (via call_soon_threadsafe), so the
    send is a direct same-loop write to the socket — never the cross-loop
    writer path that previously (before the event-loop-freeze fix) could stall
    the owner loop. If no socket is live yet, this is a no-op and the
    connect/heartbeat flush remains the backstop, so the command is never lost.

    This is what makes a healthy turn fast: measured 2026-07-13, waiting for
    the heartbeat added ~10-16s of pure latency before Codex even saw the
    command."""
    connection = _get_live_connection(gateway_id)
    if connection is None:
        return
    owner_loop = getattr(connection, "_owner_loop", None)
    if owner_loop is None or not owner_loop.is_running():
        return

    def _run() -> None:
        try:
            asyncio.ensure_future(_flush_pending_invokes(gateway_id, connection))
        except Exception:
            pass  # heartbeat-flush is the backstop

    try:
        owner_loop.call_soon_threadsafe(_run)
    except RuntimeError:
        pass  # loop shutting down — heartbeat-flush remains the backstop


async def dispatch_tool_invoke_durable(
    *,
    gateway_id: str,
    capability_id: str,
    arguments: Optional[Dict[str, Any]],
    run_id: str,
    trace_id: str,
    workspace_id: str,
    deadline_seconds: int = _DURABLE_DISPATCH_DEFAULT_DEADLINE_SECONDS,
    request_id: Optional[str] = None,
    runtime_access_mode: Optional[str] = None,
    empyralis_approved: bool = False,
    agent_scope: Optional[str] = None,
    policy: Optional[Dict[str, Any]] = None,
    actor_id: str = "",
    on_delta: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """Durable sibling of dispatch_tool_invoke: the WebSocket is transport,
    not the unit of delivery. Both directions are durable, symmetric with the
    gateway-side outbox that already makes RESULTS reconnect-proof:

      - DELIVERY (this side, cloud -> gateway): the invoke is enqueued per
        gateway_id (_enqueue_pending_invoke) and delivered exactly once, either
        immediately on a currently-fresh socket (the fast path below) or by the
        flush hook the instant the gateway next connects or heartbeats
        (_flush_pending_invokes in handle_gateway_websocket). A command can no
        longer be dropped just because no socket was live at the exact instant
        the turn arrived — the old race against the gateway's reconnect cycle.
      - RESPONSE (gateway -> cloud): once delivered, the gateway's own
        PendingResponseQueue returns the reply over whichever connection is up
        when it's ready, tagged with this request_id; the cross-connection
        correlation that lets a LATER connection resolve a wait registered
        against an EARLIER, now-dead one lives in
        resolve_response()/_DURABLE_DISPATCH_WAITERS above.

    deadline_seconds is now a genuine upper bound — "the gateway was actually
    unreachable this whole time" — not a race window. Delivered exactly once,
    so the expensive work (codex exec, ~120s) never double-executes even across
    multiple reconnects.

    Callers that don't need this (one-shot interactive capabilities like
    screenshot.capture or a browser action, where "the box was unreachable,
    tell the user now" is the right answer) should keep using
    dispatch_tool_invoke — this is specifically for turns where "eventually
    deliver" beats "fail fast," which today means cli_subscription chat
    turns on consumer hardware (sleeping Macs, VPNs, NAT) that legitimately
    drop and come back within a few minutes."""
    assert_not_killed(gateway_id=gateway_id, trace_id=trace_id)
    resolved_request_id = str(request_id or f"greq_{uuid.uuid4().hex}").strip()
    deadline = time.monotonic() + max(int(deadline_seconds or 0), 1)

    loop = asyncio.get_running_loop()
    durable_future: asyncio.Future[Dict[str, Any]] = loop.create_future()
    _register_durable_waiter(
        resolved_request_id,
        _PendingGatewayRequest(message_type="tool.invoke", future=durable_future, loop=loop),
    )
    # Phase 2 (streaming): register on_delta under the SAME request_id so any
    # tool.invoke.chunk event frame the Gateway sends while this invoke is
    # in flight reaches the caller's sink (see _deliver_delta_to_sink). Purely
    # additive — callers that don't pass on_delta see no change at all.
    if on_delta is not None:
        _register_delta_sink(resolved_request_id, on_delta)

    payload = {
        "capability_id": str(capability_id or "").strip(),
        "arguments": dict(arguments or {}),
        "run_id": str(run_id or "").strip(),
        "trace_id": str(trace_id or "").strip(),
        "workspace_id": str(workspace_id or "").strip(),
    }
    if str(runtime_access_mode or "").strip():
        payload["runtime_access_mode"] = str(runtime_access_mode or "").strip()
    if empyralis_approved:
        payload["empyralis_approved"] = True
    if str(agent_scope or "").strip():
        payload["agent_scope"] = str(agent_scope or "").strip()
    if isinstance(policy, dict):
        payload["policy"] = dict(policy)

    pending = _PendingInvoke(
        request_id=resolved_request_id,
        gateway_id=str(gateway_id or "").strip(),
        capability_id=str(capability_id or "").strip(),
        payload=payload,
        workspace_id=str(workspace_id or "").strip(),
        run_id=str(run_id or "").strip(),
        trace_id=str(trace_id or "").strip(),
        enforce_request_id=str(request_id or trace_id or run_id or "").strip(),
    )
    # Enqueue FIRST. If the gateway is offline or mid-reconnect right now, the
    # flush hook in handle_gateway_websocket delivers this invoke the instant
    # the gateway next connects or heartbeats — the command can no longer be
    # lost just because no socket was live at this exact moment. The response
    # half is already reconnect-proof via _DURABLE_DISPATCH_WAITERS.
    _enqueue_pending_invoke(pending)
    # Deliver NOW: schedule an immediate flush on the connection's owner loop
    # rather than waiting up to a full heartbeat interval. The flush runs on
    # the loop that owns the socket (a direct write), so it does not take the
    # cross-loop writer path that — before the event-loop-freeze fix — could
    # stall the owner loop. If no socket is live yet, this no-ops and the
    # connect/heartbeat flush in handle_gateway_websocket still delivers.
    _kick_immediate_flush(gateway_id)
    try:
        remaining = deadline - time.monotonic()
        try:
            response = await asyncio.wait_for(
                asyncio.shield(durable_future), timeout=max(remaining, 0.1)
            )
        except asyncio.TimeoutError:
            # Genuinely out of budget. If it was never delivered, the gateway
            # was unreachable for the whole window; if it WAS delivered, the
            # work ran but no response came back in time — don't resend either
            # way (a resend would double-execute; the gateway does not dedup).
            if not pending.delivered:
                raise RuntimeError(
                    f"Gateway dispatch could not be delivered within {deadline_seconds}s: "
                    f"Gateway is not currently connected."
                )
            raise RuntimeError(
                f"Gateway dispatch was delivered but no response arrived within {deadline_seconds}s."
            )

        if not bool(response.get("ok")):
            error = dict(response.get("error") or {})
            raise ValueError(
                str(error.get("message") or "Gateway tool invocation failed.").strip()
                or "Gateway tool invocation failed."
            )
        _resolved_actor = str(actor_id or "").strip() or "sage"
        try:
            from server_modules import ledger_audit as _la
            import asyncio as _aio
            async def _ledger():
                await _la.record_gateway_hardware_invoke(
                    workspace_id=str(workspace_id or "").strip(),
                    actor_id=_resolved_actor,
                    capability_id=str(capability_id or "").strip(),
                    arguments=dict(arguments or {}),
                    status="executed",
                    trace_id=str(trace_id or "").strip() or None,
                    run_id=str(run_id or "").strip() or None,
                )
            try:
                _aio.get_running_loop()
                _aio.create_task(_ledger())
            except RuntimeError:
                _aio.run(_ledger())
        except Exception:
            pass  # ledger is best-effort, never blocks execution
        return dict(response.get("payload") or {})
    finally:
        _pop_durable_waiter(resolved_request_id)
        _remove_pending_invoke(gateway_id, resolved_request_id)
        if on_delta is not None:
            _unregister_delta_sink(resolved_request_id)


async def dispatch_tool_interrupt(
    *,
    gateway_id: str,
    run_id: str,
    trace_id: str,
    workspace_id: str,
    target_request_id: Optional[str] = None,
    reason: Optional[str] = None,
    timeout_seconds: int = DEFAULT_TOOL_REQUEST_TIMEOUT_SECONDS,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    connection = _get_live_connection(gateway_id)
    if connection is None:
        raise ValueError("Gateway is not currently connected.")
    registration = gateway_state_repository.get_gateway_registration(gateway_id) or {}
    _enforce_gateway_quota_check(
        gateway_id=str(gateway_id or "").strip(),
        session_id=str(connection.session_id or "").strip(),
        workspace_id=str(workspace_id or "").strip(),
        tenant_id=str(registration.get("tenant_id") or connection.scope.get("tenant_id") or "").strip(),
        device_id=str(registration.get("device_id") or "").strip(),
        request_id=str(request_id or trace_id or run_id or "").strip(),
        quota_profile="gateway_tool_execution",
    )
    _enforce_gateway_tool_interrupt_protocol_route(
        gateway_id=str(gateway_id or "").strip(),
        session_id=str(connection.session_id or "").strip(),
        workspace_id=str(workspace_id or "").strip(),
        tenant_id=str(registration.get("tenant_id") or connection.scope.get("tenant_id") or "").strip(),
        device_id=str(registration.get("device_id") or "").strip(),
        run_id=str(run_id or "").strip(),
        trace_id=str(trace_id or "").strip(),
        request_id=str(request_id or trace_id or run_id or "").strip(),
    )
    payload = {
        "run_id": str(run_id or "").strip(),
        "trace_id": str(trace_id or "").strip(),
        "workspace_id": str(workspace_id or "").strip(),
        "target_request_id": str(target_request_id or "").strip() or None,
        "reason": str(reason or "").strip() or None,
    }
    _enforce_gateway_protocol_message_decision(
        gateway_id=str(gateway_id or "").strip(),
        session_id=str(connection.session_id or "").strip(),
        workspace_id=str(workspace_id or "").strip(),
        message_type="tool.interrupt",
        payload=payload,
        tool_name="tool.interrupt",
    )
    try:
        response = await connection.send_request(
            message_type="tool.interrupt",
            payload=payload,
            timeout_seconds=timeout_seconds,
            request_id=request_id,
        )
    except RuntimeError as exc:
        raise ValueError("Gateway is not currently connected.") from exc
    if not bool(response.get("ok")):
        error = dict(response.get("error") or {})
        raise ValueError(str(error.get("message") or "Gateway tool interrupt failed.").strip() or "Gateway tool interrupt failed.")
    return dict(response.get("payload") or {})


def _enforce_gateway_channel_protocol_route(
    *,
    gateway_id: str,
    session_id: str,
    workspace_id: str,
    tenant_id: str,
    device_id: str,
    capability_id: str,
    request_id: str,
) -> Dict[str, Any]:
    payload = {
        "operation": "protocol_route",
        "tenant_id": str(tenant_id or "").strip() or "default",
        "workspace_id": str(workspace_id or "").strip() or "default",
        "actor_id": "system",
        "actor_role": "owner",
        "gateway_id": str(gateway_id or "").strip(),
        "session_id": str(session_id or "").strip(),
        "request_id": str(request_id or "").strip() or None,
        "capability_id": str(capability_id or "").strip(),
        "trace_id": str(request_id or "").strip() or None,
        "quota_profile": "standard",
        "risk_level": "normal",
        "policy_decision": "allow",
        "device_trust_state": "trusted",
        "protocol_version": "gateway.v1",
        "approval_provided": True,
        "approval_memory_hit": False,
        "kill_switch_enabled": False,
        "quota_ok": True,
        "gateway_registered": True,
        "session_valid": True,
        "websocket_token_present": True,
        "frame_valid": True,
        "payload_present": True,
        "metadata": {
            "gateway_id": str(gateway_id or "").strip(),
            "session_id": str(session_id or "").strip(),
            "workspace_id": str(workspace_id or "").strip() or "default",
            "tenant_id": str(tenant_id or "").strip() or "default",
            "device_id": str(device_id or "").strip() or None,
        },
    }
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "gateway-service-decision",
            payload,
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise GatewayProtocolRustGateError(exc.reason) from exc
    next_action = str(decision.get("next_action") or "").strip()
    if next_action != "dispatch_gateway_operation":
        raise GatewayProtocolRustGateError(
            f"unexpected next_action for gateway-service-decision: {next_action or '<missing>'}"
        )
    return decision


def _enforce_gateway_tool_execute_protocol_route(
    *,
    gateway_id: str,
    session_id: str,
    workspace_id: str,
    tenant_id: str,
    device_id: str,
    capability_id: str,
    run_id: str,
    trace_id: str,
    request_id: str,
) -> Dict[str, Any]:
    payload = {
        "operation": "tool_execute",
        "tenant_id": str(tenant_id or "").strip() or "default",
        "workspace_id": str(workspace_id or "").strip() or "default",
        "actor_id": "system",
        "actor_role": "owner",
        "gateway_id": str(gateway_id or "").strip(),
        "session_id": str(session_id or "").strip(),
        "request_id": str(request_id or "").strip() or None,
        "capability_id": str(capability_id or "").strip(),
        "run_id": str(run_id or "").strip(),
        "trace_id": str(trace_id or "").strip() or None,
        "quota_profile": "standard",
        "risk_level": "normal",
        "policy_decision": "allow",
        "device_trust_state": "trusted",
        "protocol_version": "gateway.v1",
        "approval_provided": True,
        "approval_memory_hit": False,
        "kill_switch_enabled": False,
        "quota_ok": True,
        "gateway_registered": True,
        "session_valid": True,
        "websocket_token_present": True,
        "frame_valid": True,
        "payload_present": True,
        "metadata": {
            "gateway_id": str(gateway_id or "").strip(),
            "session_id": str(session_id or "").strip(),
            "workspace_id": str(workspace_id or "").strip() or "default",
            "tenant_id": str(tenant_id or "").strip() or "default",
            "device_id": str(device_id or "").strip() or None,
        },
    }
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "gateway-service-decision",
            payload,
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise GatewayProtocolRustGateError(exc.reason) from exc
    next_action = str(decision.get("next_action") or "").strip()
    if next_action != "dispatch_gateway_operation":
        raise GatewayProtocolRustGateError(
            f"unexpected next_action for gateway-service-decision: {next_action or '<missing>'}"
        )
    return decision


def _enforce_gateway_tool_interrupt_protocol_route(
    *,
    gateway_id: str,
    session_id: str,
    workspace_id: str,
    tenant_id: str,
    device_id: str,
    run_id: str,
    trace_id: str,
    request_id: str,
) -> Dict[str, Any]:
    payload = {
        "operation": "tool_interrupt",
        "tenant_id": str(tenant_id or "").strip() or "default",
        "workspace_id": str(workspace_id or "").strip() or "default",
        "actor_id": "system",
        "actor_role": "owner",
        "gateway_id": str(gateway_id or "").strip(),
        "session_id": str(session_id or "").strip(),
        "request_id": str(request_id or "").strip() or None,
        "capability_id": "tool.interrupt",
        "run_id": str(run_id or "").strip(),
        "trace_id": str(trace_id or "").strip() or None,
        "quota_profile": "standard",
        "risk_level": "normal",
        "policy_decision": "allow",
        "device_trust_state": "trusted",
        "protocol_version": "gateway.v1",
        "approval_provided": True,
        "approval_memory_hit": False,
        "kill_switch_enabled": False,
        "quota_ok": True,
        "gateway_registered": True,
        "session_valid": True,
        "websocket_token_present": True,
        "frame_valid": True,
        "payload_present": True,
        "metadata": {
            "gateway_id": str(gateway_id or "").strip(),
            "session_id": str(session_id or "").strip(),
            "workspace_id": str(workspace_id or "").strip() or "default",
            "tenant_id": str(tenant_id or "").strip() or "default",
            "device_id": str(device_id or "").strip() or None,
        },
    }
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "gateway-service-decision",
            payload,
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise GatewayProtocolRustGateError(exc.reason) from exc
    next_action = str(decision.get("next_action") or "").strip()
    if next_action != "dispatch_gateway_operation":
        raise GatewayProtocolRustGateError(
            f"unexpected next_action for gateway-service-decision: {next_action or '<missing>'}"
        )
    return decision


def _enforce_gateway_websocket_connect_decision(
    *,
    gateway_id: str,
    session_id: str,
    workspace_id: str,
    tenant_id: str,
    device_id: str,
    request_id: str,
    actor_id: str,
    device_trust_state: str,
) -> Dict[str, Any]:
    payload = {
        "operation": "websocket_connect",
        "tenant_id": str(tenant_id or "").strip() or "default",
        "workspace_id": str(workspace_id or "").strip() or "default",
        "actor_id": str(actor_id or "").strip() or "system",
        "actor_role": "owner",
        "gateway_id": str(gateway_id or "").strip(),
        "session_id": str(session_id or "").strip(),
        "request_id": str(request_id or "").strip() or None,
        "capability_id": "gateway.websocket.connect",
        "trace_id": str(request_id or "").strip() or None,
        "quota_profile": "standard",
        "risk_level": "normal",
        "policy_decision": "allow",
        "device_trust_state": str(device_trust_state or "").strip() or "trusted",
        "protocol_version": "gateway.v1",
        "approval_provided": True,
        "approval_memory_hit": False,
        "kill_switch_enabled": False,
        "quota_ok": True,
        "gateway_registered": True,
        "session_valid": True,
        "websocket_token_present": True,
        "frame_valid": True,
        "payload_present": True,
        "metadata": {
            "gateway_id": str(gateway_id or "").strip(),
            "session_id": str(session_id or "").strip(),
            "workspace_id": str(workspace_id or "").strip() or "default",
            "tenant_id": str(tenant_id or "").strip() or "default",
            "device_id": str(device_id or "").strip() or None,
        },
    }
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "gateway-service-decision",
            payload,
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise GatewayProtocolRustGateError(exc.reason) from exc
    next_action = str(decision.get("next_action") or "").strip()
    if next_action != "allow_gateway_service_operation":
        raise GatewayProtocolRustGateError(
            f"unexpected next_action for gateway-service-decision: {next_action or '<missing>'}"
        )
    return decision


def _enforce_gateway_quota_check(
    *,
    gateway_id: str,
    session_id: str,
    workspace_id: str,
    tenant_id: str,
    device_id: str,
    request_id: str,
    quota_profile: str,
) -> Dict[str, Any]:
    payload = {
        "operation": "quota_check",
        "tenant_id": str(tenant_id or "").strip() or "default",
        "workspace_id": str(workspace_id or "").strip() or "default",
        "actor_id": "system",
        "actor_role": "owner",
        "gateway_id": str(gateway_id or "").strip(),
        "session_id": str(session_id or "").strip(),
        "request_id": str(request_id or "").strip() or None,
        "capability_id": "gateway.quota.check",
        "trace_id": str(request_id or "").strip() or None,
        "quota_profile": str(quota_profile or "").strip() or "standard",
        "risk_level": "normal",
        "policy_decision": "allow",
        "device_trust_state": "trusted",
        "protocol_version": "gateway.v1",
        "approval_provided": True,
        "approval_memory_hit": False,
        "kill_switch_enabled": False,
        "quota_ok": True,
        "gateway_registered": True,
        "session_valid": True,
        "websocket_token_present": True,
        "frame_valid": True,
        "payload_present": True,
        "metadata": {
            "gateway_id": str(gateway_id or "").strip(),
            "session_id": str(session_id or "").strip(),
            "workspace_id": str(workspace_id or "").strip() or "default",
            "tenant_id": str(tenant_id or "").strip() or "default",
            "device_id": str(device_id or "").strip() or None,
            "quota_profile": str(quota_profile or "").strip() or "standard",
        },
    }
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "gateway-service-decision",
            payload,
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise GatewayProtocolRustGateError(exc.reason) from exc
    next_action = str(decision.get("next_action") or "").strip()
    if next_action != "allow_gateway_service_operation":
        raise GatewayProtocolRustGateError(
            f"unexpected next_action for gateway-service-decision: {next_action or '<missing>'}"
        )
    return decision


async def dispatch_channel_outbound(
    *,
    gateway_id: str,
    channel_key: str,
    provider: str,
    remote_jid: str,
    text: str,
    idempotency_key: str,
    operation: Optional[str] = None,
    draft_id: Optional[str] = None,
    sequence: Optional[int] = None,
    delta: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    reply_to_external_message_id: Optional[str] = None,
    media: Optional[List[Dict[str, Any]]] = None,
    timeout_seconds: int = DEFAULT_TOOL_REQUEST_TIMEOUT_SECONDS,
    request_id: Optional[str] = None,
    actor_id: str = "",
) -> Dict[str, Any]:
    assert_not_killed(gateway_id=gateway_id)
    quota_decision = evaluate_gateway_quota(profile=GATEWAY_CHANNEL_OUTBOUND, gateway_id=gateway_id)
    if not quota_decision.allowed:
        return {"ok": False, "error": "quota_exceeded", "detail": "Channel outbound quota exceeded.", "retry_after_seconds": quota_decision.retry_after_seconds}
    connection = _get_live_connection(gateway_id)
    if connection is None:
        raise ValueError("Gateway is not currently connected.")
    registration = gateway_state_repository.get_gateway_registration(gateway_id) or {}
    _enforce_gateway_quota_check(
        gateway_id=str(gateway_id or "").strip(),
        session_id=str(connection.session_id or "").strip(),
        workspace_id=str(registration.get("workspace_id") or connection.scope.get("workspace_id") or "").strip(),
        tenant_id=str(registration.get("tenant_id") or connection.scope.get("tenant_id") or "").strip(),
        device_id=str(registration.get("device_id") or "").strip(),
        request_id=str(request_id or idempotency_key or "").strip(),
        quota_profile=GATEWAY_CHANNEL_OUTBOUND,
    )
    _enforce_gateway_channel_protocol_route(
        gateway_id=str(gateway_id or "").strip(),
        session_id=str(connection.session_id or "").strip(),
        workspace_id=str(registration.get("workspace_id") or connection.scope.get("workspace_id") or "").strip(),
        tenant_id=str(registration.get("tenant_id") or connection.scope.get("tenant_id") or "").strip(),
        device_id=str(registration.get("device_id") or "").strip(),
        capability_id=f"{str(channel_key or '').strip()}.send",
        request_id=str(request_id or idempotency_key or "").strip(),
    )
    payload = {
        "channel_key": str(channel_key or "").strip(),
        "provider": str(provider or "").strip(),
        "remote_jid": str(remote_jid or "").strip(),
        "text": str(text or "").strip(),
        "idempotency_key": str(idempotency_key or "").strip(),
        "operation": str(operation or "").strip() or None,
        "draft_id": str(draft_id or "").strip() or None,
        "sequence": sequence if sequence is not None else None,
        "delta": str(delta or ""),
        "metadata": dict(metadata or {}),
        "reply_to_external_message_id": str(reply_to_external_message_id or "").strip() or None,
        # Feature B media contract (outbound leg): each item is
        # {kind, source_path|source_url, mime_type, caption?, as_voice?}.
        # Always present as a list (possibly empty) so the gateway can treat
        # a missing key and an empty list identically.
        "media": [dict(item) for item in media if isinstance(item, dict)] if media else [],
    }
    _enforce_gateway_protocol_message_decision(
        gateway_id=str(gateway_id or "").strip(),
        session_id=str(connection.session_id or "").strip(),
        workspace_id=str(registration.get("workspace_id") or connection.scope.get("workspace_id") or "").strip(),
        message_type="channel.outbound",
        payload=payload,
        tool_name=f"{str(channel_key or '').strip()}.send",
    )
    response = await connection.send_request(
        message_type="channel.outbound",
        payload=payload,
        timeout_seconds=timeout_seconds,
        request_id=request_id,
    )
    if not bool(response.get("ok")):
        error = dict(response.get("error") or {})
        raise ValueError(str(error.get("message") or "Gateway channel outbound failed.").strip() or "Gateway channel outbound failed.")
    # ── Phase K: gateway outbound ledger (redacted, best-effort) ──────
    _resolved_ws = str(registration.get("workspace_id") or connection.scope.get("workspace_id") or "").strip()
    _resolved_actor = str(actor_id or "").strip() or "sage"
    try:
        from server_modules import ledger_audit as _la
        import asyncio as _aio
        async def _ledger():
            await _la.record_gateway_channel_send(
                workspace_id=_resolved_ws,
                actor_id=_resolved_actor,
                channel_key=str(channel_key or "").strip(),
                remote_jid=str(remote_jid or "").strip(),
                text=str(text or "").strip(),
                status="sent",
                trace_id=str(request_id or idempotency_key or "").strip() or None,
            )
        try:
            _aio.get_running_loop()
            _aio.create_task(_ledger())
        except RuntimeError:
            _aio.run(_ledger())
    except Exception:
        pass  # ledger is best-effort, never blocks send
    return dict(response.get("payload") or {})


MAX_MEDIA_FETCH_BYTES = MAX_GATEWAY_FRAME_BYTES
DEFAULT_MEDIA_FETCH_TIMEOUT_SECONDS = 60


async def fetch_channel_media(
    *,
    gateway_id: str,
    channel_key: str,
    provider: str,
    media_id: str,
    timeout_seconds: int = DEFAULT_MEDIA_FETCH_TIMEOUT_SECONDS,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Fetch one inbound media attachment's raw bytes from the Gateway.

    THE FETCH MECHANISM (server<->gateway contract, Feature B inbound leg):
    the Gateway always DIALS OUT to this server (it commonly sits behind a
    user's home NAT/firewall, or an Agent Computer box with no inbound port
    opened) — the server can never reach back in with a plain HTTP GET. So
    media fetch reuses the SAME authenticated request/response RPC frame
    channel that ``dispatch_channel_outbound`` already uses for
    ``channel.outbound``: the server sends a ``channel.media_fetch``
    REQUEST frame over the live gateway WebSocket connection with
    ``{channel_key, provider, media_id}``; the Gateway resolves media_id
    against however it downloaded/cached that attachment (e.g. Baileys'
    encrypted-media downloader for WhatsApp, GramJS's file API for
    Telegram, a local bridge's tmp path) and replies on the SAME frame id
    with a RESPONSE payload of::

        {
          "media_id": str,
          "mime_type": str,
          "filename": str | None,
          "size_bytes": int | None,
          "data_base64": str,   # raw bytes, base64-encoded
        }

    A gateway that cannot serve a media_id inline (evicted from local
    cache, too large, fetch failed) responds ``{"ok": False, "error":
    {"code": "media_unavailable", "message": ...}}`` on the frame instead —
    this function raises ValueError in that case, which callers treat as
    "skip this one attachment" (see personal_channel_media_store_service),
    never as a reason to drop the whole inbound turn.

    Frames are capped at MAX_GATEWAY_FRAME_BYTES (16 MiB); base64 inflates
    the payload ~33%, so this comfortably covers voice notes/images/typical
    files but not large video — a future chunked-transfer mode is out of
    scope here.
    """
    assert_not_killed(gateway_id=gateway_id)
    connection = _get_live_connection(gateway_id)
    if connection is None:
        raise ValueError("Gateway is not currently connected.")
    normalized_media_id = str(media_id or "").strip()
    if not normalized_media_id:
        raise ValueError("media_id is required to fetch channel media.")
    payload = {
        "channel_key": str(channel_key or "").strip(),
        "provider": str(provider or "").strip(),
        "media_id": normalized_media_id,
    }
    response = await connection.send_request(
        message_type="channel.media_fetch",
        payload=payload,
        timeout_seconds=timeout_seconds,
        request_id=request_id,
    )
    if not bool(response.get("ok")):
        error = dict(response.get("error") or {})
        raise ValueError(
            str(error.get("message") or "Gateway media fetch failed.").strip()
            or "Gateway media fetch failed."
        )
    return dict(response.get("payload") or {})


def _response_frame(request_id: str, *, ok: bool, payload: Optional[Dict[str, Any]] = None, error: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    frame: Dict[str, Any] = {
        "kind": "response",
        "id": str(request_id or "").strip(),
        "ok": bool(ok),
        "ts": gateway_state_repository._utc_now_iso(),
    }
    if ok:
        frame["payload"] = dict(payload or {})
    else:
        frame["error"] = dict(error or {})
    return frame


def _event_frame(
    event_type: str,
    *,
    scope: Dict[str, str],
    payload: Optional[Dict[str, Any]] = None,
    seq: int,
    ack: Optional[int] = None,
) -> Dict[str, Any]:
    frame: Dict[str, Any] = {
        "kind": "event",
        "type": str(event_type or "").strip(),
        "seq": int(seq),
        "ts": gateway_state_repository._utc_now_iso(),
        "scope": dict(scope or {}),
        "payload": dict(payload or {}),
    }
    if ack is not None:
        frame["ack"] = int(ack)
    return frame


def _json_depth(value: Any, *, limit: int = MAX_GATEWAY_JSON_DEPTH) -> int:
    def _walk(item: Any, depth: int) -> int:
        if depth > limit:
            return depth
        if isinstance(item, dict):
            if not item:
                return depth
            return max(_walk(child, depth + 1) for child in item.values())
        if isinstance(item, list):
            if not item:
                return depth
            return max(_walk(child, depth + 1) for child in item)
        return depth

    return _walk(value, 0)


def _parse_frame(raw_text: str) -> Dict[str, Any]:
    raw = str(raw_text or "")
    if len(raw.encode("utf-8")) > MAX_GATEWAY_FRAME_BYTES:
        raise GatewayFrameValidationError(
            error_code="gateway_frame_too_large",
            reason="Gateway frame exceeds the maximum allowed size.",
            close_code=4409,
        )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GatewayFrameValidationError(
            error_code="gateway_frame_invalid_json",
            reason="Gateway frame must be valid JSON.",
        ) from exc
    if not isinstance(payload, dict):
        raise GatewayFrameValidationError(
            error_code="gateway_frame_not_object",
            reason="Gateway frame must decode to an object.",
        )
    if _json_depth(payload) > MAX_GATEWAY_JSON_DEPTH:
        raise GatewayFrameValidationError(
            error_code="gateway_frame_too_deep",
            reason="Gateway frame exceeds the maximum allowed nesting depth.",
        )
    return payload


def _expected_inbound_gateway_frame_next_action(frame: Dict[str, Any]) -> str:
    frame_kind = str(frame.get("kind") or "").strip()
    message_type = _normalized_request_type(frame)
    if frame_kind == "request" and message_type == "gateway.connect":
        return "accept_gateway_connect"
    if frame_kind == "request":
        return "route_gateway_request"
    if frame_kind == "response":
        return "resolve_gateway_response"
    if frame_kind == "event":
        return "handle_gateway_event"
    return "record_gateway_frame"


def _enforce_gateway_inbound_frame_decision(
    *,
    raw_text: str,
    frame: Dict[str, Any],
    seq: Optional[int],
    ack: Optional[int],
) -> Dict[str, Any]:
    payload = frame.get("payload") if isinstance(frame.get("payload"), dict) else {}
    protocol_version = _connect_frame_protocol_version(frame, payload)
    try:
        decision = rust_runtime_kernel_client.gateway_frame_decision(
            operation="validate_frame",
            kind=str(frame.get("kind") or "").strip(),
            type=_normalized_request_type(frame),
            id=str(frame.get("id") or "").strip(),
            raw_size_bytes=len(str(raw_text or "").encode("utf-8")),
            frame_object=True,
            json_depth=_json_depth(frame),
            protocol_version=protocol_version,
            seq=seq,
            ack=ack,
        )
        decision = rust_runtime_kernel_client.enforce_kernel_decision(
            "gateway-frame-decision",
            decision,
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        result = getattr(exc, "result", None)
        if not isinstance(result, dict):
            result = {}
        error_code = str(
            result.get("reason")
            or getattr(exc, "reason", "")
            or "gateway_frame_denied"
        ).strip() or "gateway_frame_denied"
        try:
            close_code = int(result.get("close_code") or 4408)
        except (TypeError, ValueError):
            close_code = 4408
        raise GatewayFrameValidationError(
            error_code=error_code,
            reason=f"Gateway frame rejected by Rust gateway-frame gate: {error_code}.",
            close_code=close_code,
        ) from exc
    expected_next_action = _expected_inbound_gateway_frame_next_action(frame)
    next_action = str(decision.get("next_action") or "").strip()
    if next_action != expected_next_action:
        raise GatewayFrameValidationError(
            error_code="gateway_frame_invalid_next_action",
            reason=(
                "Gateway frame returned unexpected next_action: "
                f"{next_action or 'missing'}."
            ),
        )
    return decision


def _normalized_request_type(frame: Dict[str, Any]) -> str:
    return str(frame.get("type") or "").strip()


def _validate_client_protocol_version(client_version: Optional[str]) -> Optional[str]:
    if not client_version:
        return "Protocol version is required. Supported: " + ", ".join(sorted(SUPPORTED_PROTOCOL_VERSIONS))
    if client_version not in SUPPORTED_PROTOCOL_VERSIONS:
        return "Unsupported protocol version: " + client_version + ". Supported: " + ", ".join(sorted(SUPPORTED_PROTOCOL_VERSIONS))
    return None


def _connect_frame_protocol_version(frame: Dict[str, Any], connect_payload: Dict[str, Any]) -> str:
    return str(
        frame.get("protocolVersion")
        or frame.get("protocol_version")
        or connect_payload.get("protocol_version")
        or ""
    ).strip()


def _scope_matches_registration(frame_scope: Dict[str, Any], registration: Dict[str, Any]) -> bool:
    expected = gateway_registry_service.gateway_scope_payload(registration)
    for key, value in expected.items():
        raw = str(frame_scope.get(key) or "").strip()
        if raw != value:
            return False
    return True


def _normalize_frame_seq_ack(frame: Dict[str, Any]) -> tuple[Optional[int], Optional[int]]:
    def _normalize(name: str) -> Optional[int]:
        if name not in frame:
            return None
        value = frame.get(name)
        if value is None or value == "":
            return None
        if isinstance(value, bool):
            raise GatewayFrameValidationError(
                error_code="gateway_frame_invalid_sequence",
                reason=f"Gateway frame {name} must be an integer.",
            )
        try:
            normalized = int(value)
        except (TypeError, ValueError) as exc:
            raise GatewayFrameValidationError(
                error_code="gateway_frame_invalid_sequence",
                reason=f"Gateway frame {name} must be an integer.",
            ) from exc
        if normalized < 0:
            raise GatewayFrameValidationError(
                error_code="gateway_frame_invalid_sequence",
                reason=f"Gateway frame {name} must be non-negative.",
            )
        return normalized

    return (_normalize("seq"), _normalize("ack"))


async def _validate_gateway_binding(
    *,
    session: Dict[str, Any],
    registration: Dict[str, Any],
) -> Dict[str, Any]:
    try:
        device_link = auth.validate_local_gateway_device_link(
            user_id=str(registration.get("user_id") or "").strip(),
            workspace_id=str(registration.get("workspace_id") or "").strip(),
            device_id=str(registration.get("device_id") or "").strip(),
            gateway_id=str(registration.get("gateway_id") or "").strip(),
        )
    except HTTPException as exc:
        raise ValueError(str(exc.detail)) from exc

    auth_session = auth.get_auth_session(str(session.get("session_id") or "").strip())
    if not auth_session:
        raise ValueError("Gateway auth session was not found.")
    if str(auth_session.get("status") or "").strip().lower() != "active":
        raise ValueError("Gateway auth session is not active.")
    if str(auth_session.get("device_id") or "").strip() != str(registration.get("device_id") or "").strip():
        raise ValueError("Gateway auth session device mismatch.")
    if str(auth_session.get("runtime_id") or "").strip() != str(registration.get("gateway_id") or "").strip():
        raise ValueError("Gateway auth session runtime mismatch.")
    if str(auth_session.get("channel") or "").strip().lower() != "local_runtime_companion":
        raise ValueError("Gateway auth session channel is invalid.")
    if str(auth_session.get("trust_state") or "").strip().lower() == "revoked":
        raise ValueError("Gateway auth session trust was revoked.")

    runtime_session = await session_service.get_local_gateway_session(str(session.get("session_id") or "").strip())
    if not runtime_session:
        raise ValueError("Gateway runtime session was not found.")
    if str(runtime_session.get("status") or "").strip().lower() in {"expired", "revoked"}:
        raise ValueError("Gateway runtime session is not active.")
    if str(runtime_session.get("workspace_id") or "").strip() != str(registration.get("workspace_id") or "").strip():
        raise ValueError("Gateway runtime session workspace mismatch.")
    if str(runtime_session.get("tenant_id") or "").strip() != str(registration.get("tenant_id") or "").strip():
        raise ValueError("Gateway runtime session tenant mismatch.")
    if str(runtime_session.get("channel") or "").strip().lower() != "local_runtime_companion":
        raise ValueError("Gateway runtime session channel is invalid.")
    runtime_metadata = dict(runtime_session.get("metadata") or {})
    if str(runtime_metadata.get("gateway_id") or "").strip() != str(registration.get("gateway_id") or "").strip():
        raise ValueError("Gateway runtime session binding mismatch.")
    if str(runtime_metadata.get("device_id") or "").strip() != str(registration.get("device_id") or "").strip():
        raise ValueError("Gateway runtime session device binding mismatch.")
    if str(runtime_metadata.get("user_id") or "").strip() != str(registration.get("user_id") or "").strip():
        raise ValueError("Gateway runtime session user binding mismatch.")
    return {
        "device_link": device_link,
        "auth_session": auth_session,
        "runtime_session": runtime_session,
    }


async def handle_gateway_websocket(
    websocket: WebSocket,
    *,
    gateway_id: str,
    session_token: str,
    accept_subprotocol: Optional[str] = None,
) -> None:
    connection: Optional[_LiveGatewayConnection] = None
    background_tasks: set[asyncio.Task[Any]] = set()

    async def _send_frame(frame: Dict[str, Any]) -> None:
        if connection is not None:
            try:
                await connection.send_frame(frame)
            except RuntimeError as exc:
                # The connection can die between this handler accepting a
                # frame and getting around to acknowledging it (e.g. the
                # socket closes mid-heartbeat-processing) — an expected
                # race, not a crash: the gateway gets nothing either way
                # once its socket is gone, and will retry on its next
                # heartbeat or reconnect. Same exception-translation intent
                # as dispatch_tool_invoke/interrupt's RuntimeError guard,
                # applied here at the one shared send choke point instead
                # of enumerating every message-type call site.
                _LOGGER.info(
                    "Gateway frame send skipped, connection already gone "
                    "gateway_id=%s session_id=%s frame_type=%s error=%s",
                    gateway_id,
                    session_id,
                    str(frame.get("type") or frame.get("kind") or "unknown"),
                    str(exc),
                )
            return
        await websocket.send_json(frame)

    async def _drain_connection_outbound_queue() -> None:
        if connection is not None:
            await connection.drain_queued_sends()

    def _track_background_task(task: asyncio.Task[Any]) -> None:
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)

    async def _handle_personal_channel_inbound_event(event_payload: Dict[str, Any]) -> None:
        try:
            await personal_channels_service.handle_gateway_channel_inbound(
                gateway_id=gateway_id,
                registration=registration,
                payload=event_payload,
            )
        except Exception as exc:
            gateway_state_repository.record_gateway_event(
                gateway_id=gateway_id,
                session_id=session_id,
                direction="system",
                frame_kind="event_handler",
                message_type="channel.inbound.error",
                payload={
                    "error": str(exc),
                    "payload": event_payload,
                },
            )

    try:
        session = gateway_state_repository.validate_gateway_session(
            gateway_id=gateway_id,
            session_token=session_token,
        )
    except ValueError as exc:
        await websocket.close(code=4401, reason=str(exc))
        return
    registration = gateway_state_repository.get_gateway_registration(gateway_id)
    if not registration:
        await websocket.close(code=4404, reason="Gateway registration not found.")
        return
    try:
        binding = await _validate_gateway_binding(session=session, registration=registration)
    except ValueError as exc:
        _enforce_gateway_session_mutation(
            registration=registration,
            session=session,
            operation="mark_session_disconnected",
            reason="binding_validation_failed",
        )
        gateway_state_repository.mark_gateway_session_disconnected(
            str(session.get("session_id") or "").strip(),
            reason="binding_validation_failed",
        )
        await websocket.close(code=4401, reason=str(exc))
        return

    session_id = str(session.get("session_id") or "")
    scope = gateway_registry_service.gateway_scope_payload(registration)
    server_event_seq = 0
    last_client_seq: Optional[int] = None
    disconnected = False
    _enforce_gateway_quota_check(
        gateway_id=gateway_id,
        session_id=session_id,
        workspace_id=str((registration or {}).get("workspace_id") or "").strip() or "default",
        tenant_id=str((registration or {}).get("tenant_id") or "").strip() or "default",
        device_id=str((registration or {}).get("device_id") or "").strip(),
        request_id=session_id,
        quota_profile="gateway_ws_connection",
    )
    _enforce_gateway_websocket_connect_decision(
        gateway_id=gateway_id,
        session_id=session_id,
        workspace_id=str((registration or {}).get("workspace_id") or "").strip() or "default",
        tenant_id=str((registration or {}).get("tenant_id") or "").strip() or "default",
        device_id=str((registration or {}).get("device_id") or "").strip(),
        request_id=session_id,
        actor_id=str((registration or {}).get("user_id") or "").strip() or "system",
        device_trust_state=str((registration or {}).get("device_trust_state") or "").strip() or "trusted",
    )
    await websocket.accept(subprotocol=accept_subprotocol)

    try:
        try:
            first_raw_text = await websocket.receive_text()
            first_frame = _parse_frame(first_raw_text)
            first_seq, first_ack = _normalize_frame_seq_ack(first_frame)
        except GatewayFrameValidationError as exc:
            await websocket.close(code=exc.close_code, reason=exc.reason)
            disconnected = True
            _enforce_gateway_session_mutation(
                registration=registration,
                session=session,
                operation="mark_session_disconnected",
                reason=exc.error_code,
            )
            gateway_state_repository.mark_gateway_session_disconnected(session_id, reason=exc.error_code)
            return
        gateway_state_repository.record_gateway_event(
            gateway_id=gateway_id,
            session_id=session_id,
            direction="inbound",
            frame_kind=str(first_frame.get("kind") or ""),
            message_type=_normalized_request_type(first_frame),
            payload=first_frame,
            seq=first_seq,
            ack=first_ack,
        )
        if str(first_frame.get("kind") or "").strip() != "request" or _normalized_request_type(first_frame) != "gateway.connect":
            await _send_frame(
                _response_frame(
                    str(first_frame.get("id") or "connect"),
                    ok=False,
                    error={"code": "gateway_connect_required", "message": "First frame must be gateway.connect."},
                )
            )
            await websocket.close(code=4408, reason="gateway.connect required")
            disconnected = True
            _enforce_gateway_session_mutation(
                registration=registration,
                session=session,
                operation="mark_session_disconnected",
                reason="connect_required",
            )
            gateway_state_repository.mark_gateway_session_disconnected(session_id, reason="connect_required")
            return
        frame_scope = first_frame.get("scope") if isinstance(first_frame.get("scope"), dict) else {}
        if not _scope_matches_registration(frame_scope, registration):
            await _send_frame(
                _response_frame(
                    str(first_frame.get("id") or "connect"),
                    ok=False,
                    error={"code": "scope_mismatch", "message": "Gateway scope does not match registration."},
                )
            )
            await websocket.close(code=4403, reason="scope mismatch")
            disconnected = True
            _enforce_gateway_session_mutation(
                registration=registration,
                session=session,
                operation="mark_session_disconnected",
                reason="scope_mismatch",
            )
            gateway_state_repository.mark_gateway_session_disconnected(session_id, reason="scope_mismatch")
            return

        connect_payload = first_frame.get("payload") if isinstance(first_frame.get("payload"), dict) else {}
        client_version = _connect_frame_protocol_version(first_frame, connect_payload)
        version_error = _validate_client_protocol_version(client_version)
        if version_error:
            await _send_frame(
                _response_frame(
                    str(first_frame.get("id") or "connect"),
                    ok=False,
                    error={"code": "unsupported_protocol_version", "message": version_error},
                )
            )
            await websocket.close(code=4408, reason="unsupported protocol version")
            disconnected = True
            _enforce_gateway_session_mutation(
                registration=registration,
                session=session,
                operation="mark_session_disconnected",
                reason="unsupported_protocol_version",
            )
            gateway_state_repository.mark_gateway_session_disconnected(session_id, reason="unsupported_protocol_version")
            return
        _enforce_gateway_inbound_frame_decision(
            raw_text=first_raw_text,
            frame=first_frame,
            seq=first_seq,
            ack=first_ack,
        )

        _enforce_gateway_session_mutation(
            registration=registration,
            session=session,
            operation="mark_session_connected",
        )
        gateway_state_repository.mark_gateway_session_connected(session_id)
        # MAN-140: no separate _enforce_gateway_session_mutation(operation=
        # "touch_session") pre-check here — touch_gateway_session() below
        # already calls the IDENTICAL "gateway-state-decision" rust-kernel
        # check internally (gateway_state_repository.py's own
        # _enforce_gateway_state_decision("touch_session", ...), gated on the
        # exact same _GATEWAY_SESSION_MUTATION_NEXT_ACTIONS["touch_session"]
        # == expected_next_actions["touch_session"] == {"touch_gateway_session"}
        # contract). Calling both spawned the "empyralis-runtime-kernel"
        # subprocess (rust_runtime_kernel_client.run_runtime_kernel ->
        # subprocess.run) twice for one decision, on literally every
        # heartbeat/state.update/channel.inbound/connect frame this gateway
        # ever sends, fully serialized behind gateway_state_repository's
        # process-wide _DB_LOCK. See the MAN-140 investigation note atop this
        # file's heartbeat handler for why that mattered: doubling the
        # blocking work on this path made it that much more likely to push a
        # heartbeat past the client's own ~20s per-attempt timeout under
        # concurrent fleet load, which the client — correctly, by its own
        # design — treats as a dead connection and reconnects, minting a new
        # gateway_sessions row. Removing the duplicate halves the per-frame
        # kernel-spawn + DB-lock cost without changing what gets enforced:
        # the repository layer's check still runs, unconditionally, baked
        # into touch_gateway_session() itself — so it stays in force for
        # every current AND future caller with zero risk of a caller
        # forgetting the separate pre-check this file used to require.
        gateway_state_repository.touch_gateway_session(
            session_id=session_id,
            gateway_id=gateway_id,
            seq=first_seq,
            ack=first_ack,
            journal_cursor=connect_payload.get("journal_cursor"),
            checkpoint_cursor=connect_payload.get("checkpoint_cursor"),
            metadata={
                "gateway_version": connect_payload.get("gateway_version"),
                "gateway_build_fingerprint": connect_payload.get("gateway_build_fingerprint"),
                "gateway_launch_updatability": connect_payload.get("gateway_launch_updatability"),
                "device_metadata": connect_payload.get("device_metadata"),
                "requested_capabilities": connect_payload.get("requested_capabilities"),
                "auth_session_id": session_id,
                "runtime_session_id": session_id,
            },
        )
        gateway_state_repository.update_gateway_registration_state(
            gateway_id=gateway_id,
            device_trust_state=str(binding["device_link"].get("trust_state") or "verified").strip() or "verified",
            metadata={
                "auth_session_id": session_id,
                "runtime_session_id": session_id,
                # Self-update version-check (gateway_self_update_service.py)
                # reads this off the registration's own metadata, not the
                # short-lived session row, so "current version" survives a
                # disconnect and is queryable without a live socket. Refreshed
                # on every connect (never dropped like it used to be — this
                # session-metadata copy at touch_gateway_session() above was
                # the only place it landed before this line existed).
                "gateway_version": connect_payload.get("gateway_version"),
                # Persisted for the SAME reason gateway_version is, and it is
                # the field that actually carries the information: every box
                # reports gateway_version "0.1.0" and always has, so the
                # fingerprint beside it is the only thing on this frame that
                # can distinguish the build a box is running from the build it
                # is supposed to be running. gateway_build_identity_service
                # reads it from here.
                "gateway_build_fingerprint": connect_payload.get("gateway_build_fingerprint"),
                # Whether an update on this box could ever take effect (MAN-355).
                # Persisted on the REGISTRATION for the same reason as the two
                # above: the box most worth asking this about is one that has
                # gone dark, and the session row does not survive that.
                # Refreshed on every connect, so a box whose unit an operator
                # has just repaired stops reporting itself stuck as soon as it
                # reconnects — no second action, no cache to clear.
                "gateway_launch_updatability": connect_payload.get("gateway_launch_updatability"),
                # WHICH UPDATE PATH this box is on. A desktop-app box ships
                # its gateway inside Empyralis.app and gets a new one when the
                # app updates, so a gateway self-update there would install
                # correctly and change nothing. Coerced to a real bool rather
                # than stored as whatever arrived: an absent field and a
                # literal false both have to mean "not desktop-managed", and
                # every box that predates this sends nothing at all.
                "gateway_desktop_managed": connect_payload.get("gateway_desktop_managed") is True,
            },
            # Refresh from what THIS connect declares — a Gateway build that
            # adds a capability (e.g. a new disconnect/reset action) becomes
            # dispatchable on its very next reconnect instead of needing a
            # manual registry patch or a full re-pair. Safe: this list only
            # gates whether the backend will attempt a dispatch (routing
            # feasibility) — it is never consulted to choose what capability
            # to invoke (that's always a fixed constant in application code)
            # and the kill-switch/policy/approval decision in
            # _enforce_gateway_service_decision runs independently of it.
            capabilities=connect_payload.get("requested_capabilities"),
        )
        connection = _LiveGatewayConnection(
            websocket=websocket,
            gateway_id=gateway_id,
            session_id=session_id,
            scope=scope,
        )
        connection.start_writer()
        await _register_live_connection(connection)
        connect_response = _response_frame(
            str(first_frame.get("id") or "connect"),
            ok=True,
            payload={"accepted": True, "protocol_version": PROTOCOL_VERSION},
        )
        await _send_frame(connect_response)
        if connection is not None:
            connection.cache_frame_response(str(first_frame.get("id") or "").strip(), connect_response)
        server_event_seq += 1
        hello_frame = _event_frame(
            "gateway.hello",
            scope=scope,
            payload={
                "session_id": session_id,
                "protocol_version": PROTOCOL_VERSION,
                "heartbeat_interval_seconds": gateway_registry_service.DEFAULT_GATEWAY_HEARTBEAT_INTERVAL_SECONDS,
                "scope": scope,
            },
            seq=server_event_seq,
            ack=first_ack,
        )
        gateway_state_repository.record_gateway_event(
            gateway_id=gateway_id,
            session_id=session_id,
            direction="outbound",
            frame_kind="event",
            message_type="gateway.hello",
            payload=hello_frame,
            seq=server_event_seq,
            ack=first_ack,
        )
        await _send_frame(hello_frame)
        server_event_seq += 1
        presence_frame = _event_frame(
            "gateway.presence",
            scope=scope,
            payload={"status": "online", "gateway_id": gateway_id},
            seq=server_event_seq,
            ack=first_ack,
        )
        gateway_state_repository.record_gateway_event(
            gateway_id=gateway_id,
            session_id=session_id,
            direction="outbound",
            frame_kind="event",
            message_type="gateway.presence",
            payload=presence_frame,
            seq=server_event_seq,
            ack=first_ack,
        )
        await _send_frame(presence_frame)
        await gateway_activity_service.emit_gateway_presence_activity(
            registration,
            action="gateway_connected",
            title="Gateway connected",
            summary="Paired local gateway connected to the cloud control plane.",
            status="online",
            payload={"session_id": session_id, "scope": scope},
        )
        last_client_seq = first_seq

        # Durable inbound delivery: deliver any tool.invokes that were enqueued
        # while this gateway was offline or mid-reconnect. The connection is
        # fully handshaked and fresh here — the reliable moment to flush the
        # inbound queue, mirroring the gateway replaying its own outbox on
        # connect (ws-client.ts replayPendingOutbox).
        if connection is not None:
            await _flush_pending_invokes(gateway_id, connection)

        while True:
            try:
                raw_text = await websocket.receive_text()
                frame = _parse_frame(raw_text)
                frame_seq, frame_ack = _normalize_frame_seq_ack(frame)
                _enforce_gateway_inbound_frame_decision(
                    raw_text=raw_text,
                    frame=frame,
                    seq=frame_seq,
                    ack=frame_ack,
                )
            except GatewayFrameValidationError as exc:
                await _send_frame(
                    _response_frame(
                        "invalid",
                        ok=False,
                        error={"code": exc.error_code, "message": exc.reason},
                    )
                )
                await websocket.close(code=exc.close_code, reason=exc.reason)
                disconnected = True
                _enforce_gateway_session_mutation(
                    registration=registration,
                    session=session,
                    operation="mark_session_disconnected",
                    reason=exc.error_code,
                )
                gateway_state_repository.mark_gateway_session_disconnected(session_id, reason=exc.error_code)
                break
            except RuntimeError:
                # Starlette raises a bare RuntimeError (not WebSocketDisconnect)
                # from receive_text() when the socket's own state already
                # moved past "connected" -- e.g. it closed in the gap between
                # this loop's previous iteration and this one. Found live in
                # prod 2026-07-13: unhandled here, it crashed the whole
                # handler instead of exiting the loop the same way a normal
                # disconnect does. Don't try to send or close on a socket
                # Starlette itself says isn't there -- that would just raise
                # again; only the outer finally's _unregister_live_connection
                # is needed, same as the WebSocketDisconnect path below.
                disconnected = True
                _enforce_gateway_session_mutation(
                    registration=registration,
                    session=session,
                    operation="mark_session_disconnected",
                    reason="websocket_disconnect",
                )
                gateway_state_repository.mark_gateway_session_disconnected(session_id, reason="websocket_disconnect")
                break
            if frame_seq is not None and last_client_seq is not None and frame_seq <= last_client_seq:
                await _send_frame(
                    _response_frame(
                        str(frame.get("id") or "replayed"),
                        ok=False,
                        error={
                            "code": "gateway_frame_replayed",
                            "message": "Gateway frame sequence must be strictly increasing.",
                        },
                    )
                )
                disconnected = True
                _enforce_gateway_session_mutation(
                    registration=registration,
                    session=session,
                    operation="mark_session_disconnected",
                    reason="gateway_frame_replayed",
                )
                gateway_state_repository.mark_gateway_session_disconnected(session_id, reason="gateway_frame_replayed")
                await websocket.close(code=4408, reason="gateway frame replay detected")
                break
            if frame_seq is not None:
                last_client_seq = frame_seq
            frame_kind = str(frame.get("kind") or "").strip()
            message_type = _normalized_request_type(frame)
            payload = frame.get("payload") if isinstance(frame.get("payload"), dict) else {}
            if frame_kind == "request" and connection is not None and not connection.remember_inbound_frame_id(frame.get("id")):
                frame_id = str(frame.get("id") or "").strip()
                cached = connection.get_cached_frame_response(frame_id)
                if cached is not None:
                    await _send_frame(cached)
                else:
                    await _send_frame(
                        _response_frame(
                            frame_id or "replayed",
                            ok=False,
                            error={
                                "code": "duplicate_frame_id",
                                "detail": "Frame ID already processed",
                            },
                        )
                    )
                continue
            if frame_kind == "response" and connection is not None:
                resolved_message_type = connection.resolve_response(frame)
                if resolved_message_type == "tool.invoke":
                    message_type = "tool.result"
                elif resolved_message_type == "tool.interrupt":
                    message_type = "tool.interrupt.result"
                elif resolved_message_type == "channel.outbound":
                    message_type = "channel.outbound.result"
                else:
                    message_type = resolved_message_type or "response"
            # MAN-309: this fires for EVERY inbound frame -- including every
            # single heartbeat, before the frame-type branch below even runs
            # -- and record_gateway_event() enforces via the identical
            # "gateway-state-decision" rust-kernel command touch_gateway_
            # session() uses (_enforce_gateway_state_decision("record_event",
            # ...) -> rust_runtime_kernel_client.run_runtime_kernel ->
            # subprocess.run). Called inline, that is a synchronous subprocess
            # spawn on THIS coroutine's thread -- which, since uvicorn runs
            # this single-vCPU box with no --workers flag (one process, one
            # event loop, see deploy/empyralis-backend.service), is the SAME
            # thread that services every other gateway's socket and every
            # HTTP request the backend handles. asyncio.to_thread moves the
            # call (kernel decision + the gateway_events INSERT it gates,
            # both still executed exactly once per frame, nothing skipped or
            # cached -- the kernel marks this decision "cacheable": false in
            # gateway_state.rs's record_event_decision(), a real per-call
            # anti-replay/audit check, not a static gate memoization would be
            # safe to skip) onto a worker thread so a slow kernel spawn stops
            # stalling every other gateway's heartbeat ack budget.
            await asyncio.to_thread(
                gateway_state_repository.record_gateway_event,
                gateway_id=gateway_id,
                session_id=session_id,
                direction="inbound",
                frame_kind=frame_kind,
                message_type=message_type,
                payload=frame,
                seq=frame_seq,
                ack=frame_ack,
            )
            if frame_kind == "response":
                if message_type == "channel.outbound.result":
                    try:
                        personal_channels_service.sync_gateway_channel_outbound_result(
                            gateway_id=gateway_id,
                            payload=dict(payload),
                        )
                    except Exception:
                        pass
                # MAN-140: see the comment on the connect-path touch_gateway_
                # session() call above — touch_gateway_session() already
                # performs the identical "gateway-state-decision" rust-kernel
                # check internally; no separate pre-check needed here.
                gateway_state_repository.touch_gateway_session(
                    session_id=session_id,
                    gateway_id=gateway_id,
                    metadata={"last_cloud_request_type": message_type},
                )
                continue
            if frame_kind == "event":
                if str(frame.get("type") or "").strip() == "channel.inbound":
                    _track_background_task(
                        asyncio.create_task(
                            _handle_personal_channel_inbound_event(dict(payload)),
                        )
                    )
                    # MAN-140: see the comment on the connect-path
                    # touch_gateway_session() call above — no separate
                    # pre-check needed; the repository call enforces the
                    # identical decision internally.
                    gateway_state_repository.touch_gateway_session(
                        session_id=session_id,
                        gateway_id=gateway_id,
                        seq=frame_seq,
                        ack=frame_ack,
                        metadata={"last_personal_channel_event": "channel.inbound"},
                    )
                    continue
                if str(frame.get("type") or "").strip() == "tool.invoke.chunk":
                    # Fire-and-forget: no future to resolve, no ack needed, no
                    # session mutation — just route the delta to whichever
                    # in-flight durable dispatch registered a sink for this
                    # request_id (a no-op if none did, or if it already
                    # finished — see _deliver_delta_to_sink).
                    _deliver_delta_to_sink(dict(payload))
                    continue
                continue
            if frame_kind != "request":
                await _send_frame(
                    _response_frame(
                        str(frame.get("id") or "invalid"),
                        ok=False,
                        error={"code": "invalid_frame_kind", "message": "Only request frames are accepted."},
                    )
                )
                continue
            try:
                binding = await _validate_gateway_binding(session=session, registration=registration)
            except ValueError as exc:
                _enforce_gateway_session_mutation(
                    registration=registration,
                    session=session,
                    operation="mark_session_disconnected",
                    reason="binding_validation_failed",
                )
                gateway_state_repository.mark_gateway_session_disconnected(
                    session_id,
                    reason="binding_validation_failed",
                )
                await _send_frame(
                    _response_frame(
                        str(frame.get("id") or "binding"),
                        ok=False,
                        error={"code": "binding_validation_failed", "message": str(exc)},
                    )
                )
                disconnected = True
                break
            if message_type == "gateway.heartbeat":
                capability_readiness = gateway_inventory_service.sanitize_capability_readiness(
                    payload.get("capability_readiness")
                )
                service_inventory = gateway_inventory_service.sanitize_service_inventory(
                    payload.get("service_inventory")
                )
                native_runtime = gateway_inventory_service.sanitize_native_runtime(
                    payload.get("native_runtime")
                )
                # Live CPU/memory/GPU/temperature telemetry (empyralis-gateway/
                # src/health/resource-metrics.ts) — additive, best-effort;
                # sanitize_resources() degrades any malformed/missing field to
                # None rather than raising or fabricating a value.
                resources = gateway_inventory_service.sanitize_resources(
                    payload.get("resources")
                )
                # Root cause confirmed 2026-07-13: expires_at on all three
                # session records was set once at connect and never renewed,
                # so a live, heartbeating WS still expired at its original
                # ~15-minute TTL. A heartbeat is proof of life — so renew all
                # three, but NOT on every single beat.
                #
                # These are three database writes. At a 10s heartbeat against a
                # 15-minute TTL that is ~90x more renewal than the TTL needs,
                # and it is measurable: one long-lived gateway produced 10k
                # session rows and 271k events in a 2 GB SQLite file, on the
                # same disk as Postgres. The throttle renews once roughly every
                # two thirds of the TTL, leaving a ~5 minute margin — the shape
                # Teleport uses (a 10-minute presence TTL refreshed about once a
                # minute, not on every keep-alive).
                #
                # Liveness is NOT affected. Staleness is decided from
                # _LiveGatewayConnection's in-memory time.monotonic() stamp
                # (is_stale / has_recent_inbound_frame), which still updates on
                # every frame — so dead-socket detection keeps the exact timing
                # it has today. Only the persisted expiry write is throttled.
                # REVERTED 2026-07-29, same night it shipped. touch_gateway_session
                # is what writes last_heartbeat_at, and last_heartbeat_at is the ONLY
                # signal gateway_registry_service's online/degraded derivation reads
                # (heartbeat_age_seconds <= 45s -> "online", else "degraded" for an
                # otherwise-connected session — see _gateway_connection_payload).
                # Throttling this call to once per ~10 minutes did not reduce row
                # count at all (this is an UPDATE on the existing session_id row, not
                # an INSERT — see the SQL in touch_gateway_session), it only reduced
                # write FREQUENCY, while making every gateway report "degraded" for
                # ~9 of every 10 minutes it was actually healthy and heartbeating
                # normally underneath. Confirmed live: both "Mansur's Mac" and
                # "Production Gateway" flipped to Degraded within a minute of this
                # shipping. The actual 2GB/10k-session growth this was meant to fix
                # is from RECONNECTS creating new session rows, not from this UPDATE
                # — a real fix has to address that, not this. Back to renewing every
                # heartbeat, exactly as before this change existed.
                #
                # MAN-140 follow-up: that real fix. This heartbeat branch is the
                # single hottest call site for touch_gateway_session() — one
                # invocation per gateway per DEFAULT_GATEWAY_HEARTBEAT_INTERVAL_
                # SECONDS (10s), for every connected gateway, forever. It used to
                # be preceded by its own _enforce_gateway_session_mutation(
                # operation="touch_session") pre-check, which called the exact
                # same "gateway-state-decision" rust-kernel command (subprocess
                # spawn via rust_runtime_kernel_client.run_runtime_kernel) that
                # touch_gateway_session() below ALSO performs internally, gated
                # on the identical next_action contract
                # (_GATEWAY_SESSION_MUTATION_NEXT_ACTIONS["touch_session"] ==
                # gateway_state_repository.py's expected_next_actions["touch_
                # session"] == {"touch_gateway_session"}) — two full subprocess
                # forks, both serialized behind gateway_state_repository's
                # process-wide _DB_LOCK, for one decision, on the highest-
                # frequency path in the whole protocol. Removed: it was pure,
                # provable duplication (see the identical removal + comment at
                # this file's connect/response/channel.inbound/state.update
                # touch_gateway_session() call sites), not a deliberate second
                # layer of defense — touch_gateway_session() is currently only
                # ever called from this file (verified: no other production
                # module calls it), and its own internal enforcement call
                # covers every one of those call sites unconditionally, so
                # removing the redundant pre-check here does not weaken
                # enforcement anywhere, only the doubled-up half of it. This
                # does NOT touch how often
                # touch_gateway_session() itself is called or what it writes —
                # last_heartbeat_at is still set on every single heartbeat,
                # exactly as the paragraphs above this one insist on. See
                # server_modules/tests/test_gateway_routes.py::
                # GatewayRoutesTests::test_heartbeats_over_one_connection_
                # produce_exactly_one_session_row for the regression coverage.
                #
                # MAN-309 follow-up to the MAN-140 paragraph above: removing
                # the duplicate pre-check halved the subprocess count per
                # heartbeat but left the *remaining* spawn synchronous and
                # inline on this coroutine -- still one full fork+exec+wait
                # (rust_runtime_kernel_client.run_runtime_kernel's
                # subprocess.run, DEFAULT_TIMEOUT_SECONDS=5) blocking this
                # single-vCPU box's ONE uvicorn worker (no --workers flag,
                # deploy/empyralis-backend.service) on every heartbeat, for
                # every connected gateway. Production evidence: one gateway
                # logged 1,331 connections and a reconnect storm running
                # continuously for 30+ hours at a 35-39s cadence -- the
                # client's own 10s-heartbeat/2-missed-beats dead-socket
                # detection (empyralis-gateway/src/cloud/ws-client.ts,
                # maxConsecutiveFailures: 2) firing because THIS call, plus
                # the record_gateway_event() one just above in the frame
                # loop, was blocking the ack past its budget under load --
                # the client correctly, by its own design, concluding a
                # perfectly healthy connection was dead. asyncio.to_thread
                # moves the call off the event loop onto a worker thread;
                # gateway_state_repository._connect() already opens a fresh
                # per-call sqlite3 connection with check_same_thread=False
                # and _DB_LOCK is a plain threading.Lock, so this is safe
                # from any other thread. This does NOT change what runs, how
                # often, or what it enforces -- the kernel still sees every
                # single heartbeat's seq/ack pair (touch_session_decision in
                # empyralis-runtime-kernel/src/gateway_state.rs blocks a
                # regressed seq/ack -- a real per-call check, which is why
                # that decision comes back "cacheable": false; caching it
                # instead of just moving it off-thread would have silently
                # skipped that regression check for however long the cache
                # stayed warm). Same last_heartbeat_at write, same cadence,
                # same enforcement -- only which thread waits on the fork.
                await asyncio.to_thread(
                    gateway_state_repository.touch_gateway_session,
                    session_id=session_id,
                    gateway_id=gateway_id,
                    seq=frame_seq,
                    ack=frame_ack,
                    health_state=payload.get("health_state"),
                    journal_cursor=payload.get("journal_cursor"),
                    checkpoint_cursor=payload.get("checkpoint_cursor"),
                    metadata={
                        "capability_readiness": capability_readiness,
                        "queue_depth_summary": payload.get("queue_depth_summary"),
                        "service_inventory": service_inventory,
                        "native_runtime": native_runtime,
                        "resources": resources,
                        "device_trust_state": str(binding["device_link"].get("trust_state") or "verified").strip()
                        or "verified",
                    },
                    ttl_seconds=gateway_registry_service.DEFAULT_GATEWAY_SESSION_TTL_SECONDS,
                )
                auth.touch_auth_session(
                    session_id,
                    ttl_seconds=gateway_registry_service.DEFAULT_GATEWAY_SESSION_TTL_SECONDS,
                )
                await session_service.extend_session(session_id)
                if connection is not None:
                    connection.note_session_renewed()
                heartbeat_response = _response_frame(
                    str(frame.get("id") or "heartbeat"),
                    ok=True,
                    payload={
                        "status": "online",
                        "heartbeat_interval_seconds": gateway_registry_service.DEFAULT_GATEWAY_HEARTBEAT_INTERVAL_SECONDS,
                    },
                )
                await _send_frame(heartbeat_response)
                if connection is not None:
                    connection.cache_frame_response(str(frame.get("id") or "").strip(), heartbeat_response)
                await _drain_connection_outbound_queue()
                # A heartbeat just proved this connection is alive and fresh —
                # also the moment to deliver any invoke that was enqueued while
                # the socket was up but momentarily stale (so it doesn't wait
                # for a full disconnect/reconnect cycle to be flushed).
                if connection is not None:
                    await _flush_pending_invokes(gateway_id, connection)
                continue
            if message_type == "gateway.state.update":
                previous_health_state = str(registration.get("metadata", {}).get("health_state") or "").strip().lower()
                capability_readiness = gateway_inventory_service.sanitize_capability_readiness(
                    payload.get("capability_readiness")
                )
                service_inventory = gateway_inventory_service.sanitize_service_inventory(
                    payload.get("service_inventory")
                )
                native_runtime = gateway_inventory_service.sanitize_native_runtime(
                    payload.get("native_runtime")
                )
                registration_metadata_payload = dict(payload)
                if "service_inventory" in registration_metadata_payload:
                    registration_metadata_payload["service_inventory"] = service_inventory
                if "native_runtime" in registration_metadata_payload:
                    registration_metadata_payload["native_runtime"] = native_runtime
                if "capability_readiness" in registration_metadata_payload:
                    registration_metadata_payload["capability_readiness"] = capability_readiness
                registration = gateway_state_repository.update_gateway_registration_state(
                    gateway_id=gateway_id,
                    metadata=registration_metadata_payload,
                    journal_cursor=payload.get("journal_cursor"),
                    checkpoint_cursor=payload.get("checkpoint_cursor"),
                ) or registration
                personal_channels_service.sync_gateway_personal_channel_state(
                    gateway_id=gateway_id,
                    registration=registration,
                    payload=payload,
                )
                # MAN-140: see the comment on the heartbeat-branch
                # touch_gateway_session() call above — no separate pre-check
                # needed here either.
                gateway_state_repository.touch_gateway_session(
                    session_id=session_id,
                    gateway_id=gateway_id,
                    seq=frame_seq,
                    ack=frame_ack,
                    health_state=payload.get("health_state"),
                    journal_cursor=payload.get("journal_cursor"),
                    checkpoint_cursor=payload.get("checkpoint_cursor"),
                    metadata={
                        "personal_channels": payload.get("personal_channels"),
                        "personal_channel_manifests": payload.get("personal_channel_manifests"),
                        "personal_channel_health": payload.get("personal_channel_health"),
                        **({"capability_readiness": capability_readiness} if "capability_readiness" in payload else {}),
                        **({"service_inventory": service_inventory} if "service_inventory" in payload else {}),
                        **({"native_runtime": native_runtime} if "native_runtime" in payload else {}),
                    },
                )
                next_health_state = str(payload.get("health_state") or registration.get("metadata", {}).get("health_state") or "").strip().lower()
                if payload.get("personal_channels") or next_health_state != previous_health_state:
                    await gateway_activity_service.emit_gateway_presence_activity(
                        registration,
                        action="gateway_state_updated" if next_health_state in {"", "online"} else "gateway_degraded",
                        title="Gateway state updated",
                        summary=(
                            f"Gateway reported {next_health_state} state."
                            if next_health_state
                            else "Gateway reported updated state."
                        ),
                        status=next_health_state or "online",
                        payload=payload,
                    )
                state_update_response = _response_frame(
                    str(frame.get("id") or "state_update"),
                    ok=True,
                    payload={"updated": True},
                )
                await _send_frame(state_update_response)
                if connection is not None:
                    connection.cache_frame_response(str(frame.get("id") or "").strip(), state_update_response)
                await _drain_connection_outbound_queue()
                server_event_seq += 1
                state_event = _event_frame(
                    "gateway.presence",
                    scope=scope,
                    payload={
                        "status": "online",
                        "gateway_id": gateway_id,
                        "state": payload,
                    },
                    seq=server_event_seq,
                    ack=frame_ack,
                )
                gateway_state_repository.record_gateway_event(
                    gateway_id=gateway_id,
                    session_id=session_id,
                    direction="outbound",
                    frame_kind="event",
                    message_type="gateway.presence",
                    payload=state_event,
                    seq=server_event_seq,
                    ack=frame_ack,
                )
                await _send_frame(state_event)
                continue
            if message_type == "gateway.disconnect":
                _enforce_gateway_session_mutation(
                    registration=registration,
                    session=session,
                    operation="mark_session_disconnected",
                    reason=str(payload.get("reason") or "client_disconnect"),
                )
                gateway_state_repository.mark_gateway_session_disconnected(
                    session_id,
                    reason=str(payload.get("reason") or "client_disconnect"),
                )
                await gateway_activity_service.emit_gateway_presence_activity(
                    registration,
                    action="gateway_disconnected",
                    title="Gateway disconnected",
                    summary=str(payload.get("reason") or "Gateway disconnected from the control plane."),
                    status="offline",
                    payload={"session_id": session_id, "reason": payload.get("reason")},
                )
                disconnect_response = _response_frame(
                    str(frame.get("id") or "disconnect"),
                    ok=True,
                    payload={"disconnected": True},
                )
                await _send_frame(disconnect_response)
                if connection is not None:
                    connection.cache_frame_response(str(frame.get("id") or "").strip(), disconnect_response)
                disconnected = True
                break

            unsupported_response = _response_frame(
                str(frame.get("id") or "unsupported"),
                ok=False,
                error={"code": "unsupported_message_type", "message": f"Unsupported message type: {message_type}"},
            )
            await _send_frame(unsupported_response)
            if connection is not None:
                connection.cache_frame_response(str(frame.get("id") or "").strip(), unsupported_response)
    except WebSocketDisconnect:
        if not disconnected:
            _enforce_gateway_session_mutation(
                registration=registration,
                session=session,
                operation="mark_session_disconnected",
                reason="websocket_disconnect",
            )
            gateway_state_repository.mark_gateway_session_disconnected(session_id, reason="websocket_disconnect")
            await gateway_activity_service.emit_gateway_presence_activity(
                registration,
                action="gateway_disconnected",
                title="Gateway disconnected",
                summary="Gateway websocket disconnected.",
                status="offline",
                payload={"session_id": session_id, "reason": "websocket_disconnect"},
            )
    finally:
        _unregister_live_connection(
            gateway_id=gateway_id,
            session_id=session_id,
            reason="gateway_websocket_closed",
        )
        if background_tasks:
            for task in list(background_tasks):
                task.cancel()
            await asyncio.gather(*background_tasks, return_exceptions=True)
        if not disconnected:
            _enforce_gateway_session_mutation(
                registration=registration,
                session=session,
                operation="mark_session_disconnected",
                reason="socket_closed",
            )
            gateway_state_repository.mark_gateway_session_disconnected(session_id, reason="socket_closed")
