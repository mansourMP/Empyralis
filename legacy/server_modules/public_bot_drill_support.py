from __future__ import annotations

import asyncio
import json
import math
import tempfile
import threading
import time
from collections import Counter, defaultdict
from contextlib import ExitStack, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence

import pytest

from server_modules import rust_runtime_kernel_client
from server_modules.blackbox_runtime_support import BlackboxHarness, blackbox_runtime_context


UNACCEPTABLE_P95_MS = 5_000.0
BASELINE_PROVIDER_DELAY_SECONDS = 0.25
BASELINE_DB_POOL_SIZE = 16


class PublicBotDrillRustGateError(RuntimeError):
    pass


def _enforce_public_bot_drill_report_write(
    *,
    output_path: Path,
    json_path: Path,
    markdown_payload: str,
    json_payload: str,
) -> None:
    payload = {
        "output_path": str(output_path),
        "json_path": str(json_path),
        "markdown_bytes": len(markdown_payload.encode("utf-8")),
        "json_bytes": len(json_payload.encode("utf-8")),
    }
    try:
        decision = rust_runtime_kernel_client.runtime_state_store_decision(
            operation="write_public_bot_drill_report",
            state_class="public_bot_drill_reports",
            actor_id="system",
            status="active",
            payload=payload,
            payload_bytes=int(payload["markdown_bytes"]) + int(payload["json_bytes"]),
            workspace_access=True,
            owner_access=True,
        )
        rust_runtime_kernel_client.enforce_kernel_decision(
            "runtime-state-store-decision",
            decision,
        )
        next_action = str(decision.get("next_action") or "").strip()
        if next_action != "write_public_bot_drill_report":
            raise PublicBotDrillRustGateError("unexpected_next_action")
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise PublicBotDrillRustGateError(exc.reason) from exc


def _now_perf() -> float:
    return time.perf_counter()


def _sleep_sync(seconds: float) -> None:
    if float(seconds or 0.0) > 0.0:
        time.sleep(float(seconds))


def _percentile(values: Sequence[float], percentile: float) -> float:
    numbers = sorted(float(value) for value in values if value is not None)
    if not numbers:
        return 0.0
    if len(numbers) == 1:
        return numbers[0]
    rank = max(0.0, min(100.0, float(percentile or 0.0))) / 100.0 * (len(numbers) - 1)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return numbers[lower]
    fraction = rank - lower
    return numbers[lower] + (numbers[upper] - numbers[lower]) * fraction


def _timing_summary(values: Sequence[float]) -> Dict[str, float]:
    numbers = [float(value) for value in values if value is not None]
    if not numbers:
        return {"count": 0.0, "p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0, "max_ms": 0.0, "avg_ms": 0.0}
    return {
        "count": float(len(numbers)),
        "p50_ms": round(_percentile(numbers, 50), 2),
        "p95_ms": round(_percentile(numbers, 95), 2),
        "p99_ms": round(_percentile(numbers, 99), 2),
        "max_ms": round(max(numbers), 2),
        "avg_ms": round(sum(numbers) / len(numbers), 2),
    }


def _stringify(value: Any) -> str:
    return str(value or "").strip()


def _normalized_reason(reason: Any, fallback: str) -> str:
    return _stringify(reason).lower() or fallback


def _status_component(reason: str) -> str:
    token = _normalized_reason(reason, "error")
    if token in {"hosted_runtime_concurrency_exhausted", "hosted_runtime_minutes_exhausted"}:
        return "hosted_runtime_entitlement"
    if token in {"thread_busy", "agent_limit_exceeded", "workspace_limit_exceeded", "workspace_rate_limited"}:
        return "channel_execution_lease"
    if token in {"daily_message_limit_exceeded", "deployed_agent_daily_limit_exceeded"}:
        return "deployed_agent_daily_quota"
    if token == "runtime_cap_exceeded":
        return "runtime_timeout_boundary"
    if token == "transport_delivery_failure":
        return "telegram_transport_delivery"
    if token == "notification_delivery_failure":
        return "notification_delivery"
    if token == "db_slowness":
        return "control_plane_repository"
    return "channel_ingress"


def _classify_db_query(query: Any) -> str:
    sql = str(query or "").strip().lower()
    if not sql:
        return "other"
    if "agent_channel_events" in sql:
        return "agent_channel_event"
    if "deployed_agent_daily_message_usage" in sql:
        return "daily_quota"
    if "agent_channel_execution_leases" in sql or "pg_try_advisory_lock" in sql:
        return "lease"
    if "deployed_agent_monthly_cost_ledger" in sql:
        return "monthly_ledger"
    return "other"


@dataclass
class DbLatencyPlan:
    pool_acquire_ms: int = 0
    target_latency_ms: Dict[str, int] = field(default_factory=dict)

    def latency_for_target(self, target: str) -> int:
        return max(0, int((self.target_latency_ms or {}).get(str(target or "").strip().lower()) or 0))


@dataclass
class RequestDispatchResult:
    message_id: str
    chat_id: str
    sender_id: str
    text: str
    started_at: float
    responded_at: float
    status_code: int
    action: str
    run_id: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)


class DrillRecorder:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.requests: Dict[str, Dict[str, Any]] = {}
        self.route_results: Dict[str, Dict[str, Any]] = {}
        self.transport_calls: List[Dict[str, Any]] = []
        self.operations: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self.db_queries: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    def register_request(self, *, message_id: str, chat_id: str, sender_id: str, text: str, started_at: float) -> None:
        with self._lock:
            self.requests[str(message_id)] = {
                "message_id": str(message_id),
                "chat_id": str(chat_id),
                "sender_id": str(sender_id),
                "text": str(text),
                "started_at": float(started_at),
            }

    def record_request_response(
        self,
        *,
        message_id: str,
        responded_at: float,
        status_code: int,
        action: str,
        run_id: Optional[str],
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self._lock:
            self.requests.setdefault(str(message_id), {"message_id": str(message_id)}).update(
                {
                    "responded_at": float(responded_at),
                    "status_code": int(status_code or 0),
                    "action": str(action or "").strip().lower(),
                    "run_id": _stringify(run_id) or None,
                    "payload": dict(payload or {}),
                }
            )

    def record_route_result(
        self,
        *,
        message_id: str,
        session_key: Optional[str],
        status: str,
        limit_reason: Optional[str],
        run_id: Optional[str],
        elapsed_ms: float,
        ok: bool,
        reply: Optional[str] = None,
    ) -> None:
        record = {
            "message_id": str(message_id),
            "session_key": _stringify(session_key) or None,
            "status": str(status or "").strip().lower(),
            "limit_reason": _stringify(limit_reason).lower() or None,
            "run_id": _stringify(run_id) or None,
            "elapsed_ms": round(float(elapsed_ms), 2),
            "ok": bool(ok),
            "reply": str(reply or "").strip(),
        }
        with self._lock:
            self.route_results[str(message_id)] = record
            self.operations["route_inbound_channel_message"].append(record)

    def record_transport(self, *, call: Dict[str, Any], success: bool, error: Optional[str]) -> None:
        payload = call.get("payload") if isinstance(call.get("payload"), dict) else {}
        params = call.get("params") if isinstance(call.get("params"), dict) else {}
        chat_id = _stringify(payload.get("chat_id") or params.get("chat_id"))
        record = {
            **dict(call),
            "chat_id": chat_id,
            "method_name": _stringify(call.get("method_name")),
            "timestamp": float(call.get("timestamp") or time.time()),
            "perf_counter": float(call.get("perf_counter") or _now_perf()),
            "success": bool(success),
            "error": _stringify(error) or None,
        }
        with self._lock:
            self.transport_calls.append(record)
            self.operations["telegram_transport"].append(record)

    def record_operation(
        self,
        name: str,
        *,
        elapsed_ms: float,
        ok: bool,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self._lock:
            self.operations[str(name)].append(
                {
                    "elapsed_ms": round(float(elapsed_ms), 2),
                    "ok": bool(ok),
                    "timestamp": time.time(),
                    **dict(metadata or {}),
                }
            )

    def record_db_query(
        self,
        target: str,
        *,
        elapsed_ms: float,
        ok: bool,
        sql: Optional[str] = None,
    ) -> None:
        with self._lock:
            self.db_queries[str(target)].append(
                {
                    "elapsed_ms": round(float(elapsed_ms), 2),
                    "ok": bool(ok),
                    "timestamp": time.time(),
                    "sql": str(sql or "").strip() or None,
                }
            )

    def route_result_for_message(self, message_id: str) -> Dict[str, Any]:
        with self._lock:
            return dict(self.route_results.get(str(message_id), {}))

    def request_for_message(self, message_id: str) -> Dict[str, Any]:
        with self._lock:
            return dict(self.requests.get(str(message_id), {}))

    def transport_events_for_chat(self, chat_id: str, *, after_started_at: float = 0.0) -> List[Dict[str, Any]]:
        normalized_chat_id = str(chat_id)
        with self._lock:
            return [
                dict(item)
                for item in self.transport_calls
                if str(item.get("chat_id") or "") == normalized_chat_id
                and float(item.get("perf_counter") or 0.0) >= float(after_started_at)
            ]

    def final_transport_event_for_chat(self, chat_id: str, *, after_started_at: float = 0.0) -> Optional[Dict[str, Any]]:
        events = self.transport_events_for_chat(chat_id, after_started_at=after_started_at)
        edits = [item for item in events if str(item.get("method_name") or "") == "editMessageText" and bool(item.get("success"))]
        if edits:
            return dict(edits[-1])
        sends = [item for item in events if str(item.get("method_name") or "") == "sendMessage" and bool(item.get("success"))]
        if sends:
            return dict(sends[-1])
        return None

    def transport_failures_for_chat(self, chat_id: str, *, after_started_at: float = 0.0) -> List[Dict[str, Any]]:
        return [
            item
            for item in self.transport_events_for_chat(chat_id, after_started_at=after_started_at)
            if not bool(item.get("success"))
        ]

    def operation_timings(self, name: str) -> List[float]:
        with self._lock:
            return [float(item.get("elapsed_ms") or 0.0) for item in self.operations.get(str(name), [])]

    def operation_records(self, name: str) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self.operations.get(str(name), [])]

    def db_query_timings(self, target: str) -> List[float]:
        with self._lock:
            return [float(item.get("elapsed_ms") or 0.0) for item in self.db_queries.get(str(target), [])]

    def export(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "requests": dict(self.requests),
                "route_results": dict(self.route_results),
                "transport_calls": [dict(item) for item in self.transport_calls],
                "operations": {key: [dict(item) for item in values] for key, values in self.operations.items()},
                "db_queries": {key: [dict(item) for item in values] for key, values in self.db_queries.items()},
            }


class _LatencyInjectedConnection:
    def __init__(self, connection: Any, *, plan_provider: Callable[[], Optional[DbLatencyPlan]], recorder: DrillRecorder) -> None:
        self._connection = connection
        self._plan_provider = plan_provider
        self._recorder = recorder

    async def _call(self, method_name: str, query: Any, *args: Any) -> Any:
        target = _classify_db_query(query)
        plan = self._plan_provider()
        delay_ms = plan.latency_for_target(target) if plan is not None else 0
        if delay_ms > 0:
            await asyncio.sleep(delay_ms / 1000.0)
        started = _now_perf()
        try:
            result = await getattr(self._connection, method_name)(query, *args)
        except Exception:
            self._recorder.record_db_query(
                target,
                elapsed_ms=(_now_perf() - started) * 1000.0 + float(delay_ms),
                ok=False,
                sql=str(query or "").strip()[:240],
            )
            raise
        self._recorder.record_db_query(
            target,
            elapsed_ms=(_now_perf() - started) * 1000.0 + float(delay_ms),
            ok=True,
            sql=str(query or "").strip()[:240],
        )
        return result

    async def execute(self, query: Any, *args: Any) -> Any:
        return await self._call("execute", query, *args)

    async def fetch(self, query: Any, *args: Any) -> Any:
        return await self._call("fetch", query, *args)

    async def fetchrow(self, query: Any, *args: Any) -> Any:
        return await self._call("fetchrow", query, *args)

    async def fetchval(self, query: Any, *args: Any) -> Any:
        return await self._call("fetchval", query, *args)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)


class PublicBotDrillEnvironment:
    def __init__(
        self,
        *,
        tmp_root: Optional[Path] = None,
        db_pool_size: int = BASELINE_DB_POOL_SIZE,
        provider_delay_seconds: float = BASELINE_PROVIDER_DELAY_SECONDS,
    ) -> None:
        self.tmp_root = Path(tmp_root or Path(tempfile.mkdtemp(prefix="prompt14-public-bot-")))
        self.db_pool_size = max(1, int(db_pool_size or 1))
        self.provider_delay_seconds = max(0.0, float(provider_delay_seconds or 0.0))
        self.monkeypatch = pytest.MonkeyPatch()
        self.runtime_cm: Any = None
        self.harness: Optional[BlackboxHarness] = None
        self.recorder = DrillRecorder()
        self._stack = ExitStack()
        self._db_latency_plan: Optional[DbLatencyPlan] = None
        self._notification_failure_enabled = False
        self._notification_failure_message = "prompt14_notification_delivery_failure"
        self._telegram_failure_methods: set[str] = set()
        self._telegram_failure_message = "prompt14_telegram_delivery_failure"
        self._telegram_method_delay_seconds: Dict[str, float] = {}

    def __enter__(self) -> "PublicBotDrillEnvironment":
        self.tmp_root.mkdir(parents=True, exist_ok=True)
        self.runtime_cm = blackbox_runtime_context(
            monkeypatch=self.monkeypatch,
            tmp_path=self.tmp_root,
            db_pool_size=self.db_pool_size,
            provider_delay_seconds=self.provider_delay_seconds,
            telegram_api_request_handler=self._telegram_api_request_handler,
        )
        self.harness = self.runtime_cm.__enter__()
        self._install_wrappers()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self._stack.close()
        finally:
            try:
                if self.runtime_cm is not None:
                    self.runtime_cm.__exit__(exc_type, exc, tb)
            finally:
                self.monkeypatch.undo()

    def set_db_latency_plan(self, plan: Optional[DbLatencyPlan]) -> None:
        self._db_latency_plan = plan

    def clear_db_latency_plan(self) -> None:
        self._db_latency_plan = None

    def enable_notification_failure(self, *, message: str = "prompt14_notification_delivery_failure") -> None:
        self._notification_failure_enabled = True
        self._notification_failure_message = str(message or "").strip() or "prompt14_notification_delivery_failure"

    def clear_notification_failure(self) -> None:
        self._notification_failure_enabled = False
        self._notification_failure_message = "prompt14_notification_delivery_failure"

    def set_telegram_delivery_failure(
        self,
        *,
        methods: Iterable[str],
        message: str = "prompt14_telegram_delivery_failure",
    ) -> None:
        self._telegram_failure_methods = {str(item or "").strip() for item in list(methods or []) if str(item or "").strip()}
        self._telegram_failure_message = str(message or "").strip() or "prompt14_telegram_delivery_failure"

    def clear_telegram_delivery_failure(self) -> None:
        self._telegram_failure_methods = set()
        self._telegram_failure_message = "prompt14_telegram_delivery_failure"

    def set_telegram_method_delay(self, *, method_name: str, delay_seconds: float) -> None:
        self._telegram_method_delay_seconds[str(method_name or "").strip()] = max(0.0, float(delay_seconds or 0.0))

    def clear_telegram_method_delay(self) -> None:
        self._telegram_method_delay_seconds = {}

    def _current_db_latency_plan(self) -> Optional[DbLatencyPlan]:
        return self._db_latency_plan

    def _telegram_api_request_handler(self, call: Dict[str, Any], default_response: Callable[[], Any]) -> Any:
        method_name = _stringify(call.get("method_name"))
        _sleep_sync(self._telegram_method_delay_seconds.get(method_name, 0.0))
        if method_name in self._telegram_failure_methods:
            self.recorder.record_transport(
                call=call,
                success=False,
                error=self._telegram_failure_message,
            )
            raise RuntimeError(self._telegram_failure_message)
        try:
            result = default_response()
        except Exception as exc:
            self.recorder.record_transport(call=call, success=False, error=str(exc))
            raise
        recorded_call = {**dict(call), "result": result}
        self.recorder.record_transport(call=recorded_call, success=True, error=None)
        return result

    def _install_wrappers(self) -> None:
        assert self.harness is not None
        import server_modules.agent_channel_router as agent_channel_router
        import server_modules.channel_concurrency_service as channel_concurrency_service
        import server_modules.channel_memory_overlay_service as channel_memory_overlay_service
        import server_modules.control_plane_repository as control_plane_repository
        import server_modules.deployed_agent_cost_cap_service as deployed_agent_cost_cap_service
        import server_modules.entitlements_service as entitlements_service
        import server_modules.notification_service as notification_service

        original_route = agent_channel_router.route_inbound_channel_message
        original_slot = channel_concurrency_service.channel_execution_slot
        original_append_channel_event = control_plane_repository.append_agent_channel_event
        original_consume_daily_quota = control_plane_repository.consume_deployed_agent_daily_message_quota
        original_append_activity = control_plane_repository.append_activity_ledger_event
        original_persist_snapshot = channel_memory_overlay_service.persist_snapshot
        original_settle_budget = deployed_agent_cost_cap_service.settle_deployed_agent_monthly_cost_cap
        original_enforce_hosted_runtime_access = entitlements_service.enforce_hosted_runtime_access
        original_deliver_notification = notification_service.deliver_notification_from_outbox_event
        original_scoped_connection = control_plane_repository._scoped_connection

        async def wrapped_route_inbound_channel_message(*args: Any, **kwargs: Any) -> Dict[str, Any]:
            started = _now_perf()
            message_id = _stringify(kwargs.get("message_id"))
            session_key = _stringify(kwargs.get("session_key")) or None
            try:
                result = await original_route(*args, **kwargs)
            except Exception:
                self.recorder.record_route_result(
                    message_id=message_id,
                    session_key=session_key,
                    status="error",
                    limit_reason="internal_error",
                    run_id=None,
                    elapsed_ms=(_now_perf() - started) * 1000.0,
                    ok=False,
                )
                raise
            self.recorder.record_route_result(
                message_id=message_id,
                session_key=_stringify(result.get("session_key")) or session_key,
                status=_stringify(result.get("status")).lower(),
                limit_reason=_stringify(result.get("limit_reason")).lower() or None,
                run_id=_stringify(result.get("run_id")) or None,
                elapsed_ms=(_now_perf() - started) * 1000.0,
                ok=True,
                reply=_stringify(result.get("reply")) or None,
            )
            return result

        @asynccontextmanager
        async def wrapped_channel_execution_slot(*args: Any, **kwargs: Any) -> AsyncIterator[Dict[str, Any]]:
            started = _now_perf()
            try:
                async with original_slot(*args, **kwargs) as slot:
                    self.recorder.record_operation(
                        "channel_execution_slot",
                        elapsed_ms=(_now_perf() - started) * 1000.0,
                        ok=True,
                        metadata={"quota_snapshot": dict((slot or {}).get("quota_snapshot").as_dict()) if hasattr((slot or {}).get("quota_snapshot"), "as_dict") else None},
                    )
                    yield slot
            except Exception as exc:
                self.recorder.record_operation(
                    "channel_execution_slot",
                    elapsed_ms=(_now_perf() - started) * 1000.0,
                    ok=False,
                    metadata={"reason": _stringify(getattr(exc, "reason", None)) or exc.__class__.__name__},
                )
                raise

        async def wrapped_append_agent_channel_event(*args: Any, **kwargs: Any) -> Any:
            started = _now_perf()
            direction = _stringify(kwargs.get("direction")).lower() or "system"
            event_type = _stringify(kwargs.get("event_type")).lower() or "event"
            try:
                result = await original_append_channel_event(*args, **kwargs)
            except Exception:
                self.recorder.record_operation(
                    "append_agent_channel_event",
                    elapsed_ms=(_now_perf() - started) * 1000.0,
                    ok=False,
                    metadata={"direction": direction, "event_type": event_type},
                )
                raise
            self.recorder.record_operation(
                "append_agent_channel_event",
                elapsed_ms=(_now_perf() - started) * 1000.0,
                ok=True,
                metadata={
                    "direction": direction,
                    "event_type": event_type,
                    "message_id": _stringify(kwargs.get("message_id")) or _stringify((result or {}).get("message_id")) or None,
                    "session_key": _stringify(kwargs.get("session_key")) or _stringify((result or {}).get("session_key")) or None,
                    "event_id": _stringify((result or {}).get("id")) or None,
                    "parent_event_id": _stringify(kwargs.get("parent_event_id")) or None,
                },
            )
            return result

        async def wrapped_consume_daily_quota(*args: Any, **kwargs: Any) -> Any:
            started = _now_perf()
            try:
                result = await original_consume_daily_quota(*args, **kwargs)
            except Exception:
                self.recorder.record_operation(
                    "consume_deployed_agent_daily_message_quota",
                    elapsed_ms=(_now_perf() - started) * 1000.0,
                    ok=False,
                    metadata={"deployed_agent_id": _stringify(kwargs.get("deployed_agent_id")) or None},
                )
                raise
            self.recorder.record_operation(
                "consume_deployed_agent_daily_message_quota",
                elapsed_ms=(_now_perf() - started) * 1000.0,
                ok=True,
                metadata={
                    "deployed_agent_id": _stringify(kwargs.get("deployed_agent_id")) or None,
                    "quota_consumed": bool((result or {}).get("quota_consumed")),
                    "message_count": int((result or {}).get("message_count") or 0),
                },
            )
            return result

        async def wrapped_append_activity_ledger_event(*args: Any, **kwargs: Any) -> Any:
            started = _now_perf()
            try:
                result = await original_append_activity(*args, **kwargs)
            except Exception:
                self.recorder.record_operation(
                    "append_activity_ledger_event",
                    elapsed_ms=(_now_perf() - started) * 1000.0,
                    ok=False,
                    metadata={"action": _stringify(kwargs.get("action")) or None},
                )
                raise
            self.recorder.record_operation(
                "append_activity_ledger_event",
                elapsed_ms=(_now_perf() - started) * 1000.0,
                ok=True,
                metadata={"action": _stringify(kwargs.get("action")) or None},
            )
            return result

        async def wrapped_persist_snapshot(*args: Any, **kwargs: Any) -> Any:
            started = _now_perf()
            result = await original_persist_snapshot(*args, **kwargs)
            self.recorder.record_operation(
                "persist_deployed_agent_memory_snapshot",
                elapsed_ms=(_now_perf() - started) * 1000.0,
                ok=result is None,
                metadata={"degraded": result is not None},
            )
            return result

        async def wrapped_settle_budget(*args: Any, **kwargs: Any) -> Any:
            started = _now_perf()
            try:
                result = await original_settle_budget(*args, **kwargs)
            except Exception:
                self.recorder.record_operation(
                    "settle_deployed_agent_monthly_cost_cap",
                    elapsed_ms=(_now_perf() - started) * 1000.0,
                    ok=False,
                    metadata={},
                )
                raise
            self.recorder.record_operation(
                "settle_deployed_agent_monthly_cost_cap",
                elapsed_ms=(_now_perf() - started) * 1000.0,
                ok=True,
                metadata={
                    "applied": bool((result or {}).get("applied")),
                    "auto_paused": bool((result or {}).get("auto_paused")),
                    "reason": _stringify((result or {}).get("reason")) or None,
                },
            )
            return result

        def wrapped_deliver_notification(*args: Any, **kwargs: Any) -> Any:
            started = _now_perf()
            if self._notification_failure_enabled:
                self.recorder.record_operation(
                    "deliver_notification_from_outbox_event",
                    elapsed_ms=(_now_perf() - started) * 1000.0,
                    ok=False,
                    metadata={"reason": self._notification_failure_message},
                )
                raise RuntimeError(self._notification_failure_message)
            try:
                result = original_deliver_notification(*args, **kwargs)
            except Exception:
                self.recorder.record_operation(
                    "deliver_notification_from_outbox_event",
                    elapsed_ms=(_now_perf() - started) * 1000.0,
                    ok=False,
                    metadata={},
                )
                raise
            self.recorder.record_operation(
                "deliver_notification_from_outbox_event",
                elapsed_ms=(_now_perf() - started) * 1000.0,
                ok=True,
                metadata={"notification_id": _stringify((result or {}).get("id")) or None},
            )
            return result

        def wrapped_enforce_hosted_runtime_access(*args: Any, **kwargs: Any) -> Any:
            started = _now_perf()
            try:
                result = original_enforce_hosted_runtime_access(*args, **kwargs)
            except Exception as exc:
                self.recorder.record_operation(
                    "enforce_hosted_runtime_access",
                    elapsed_ms=(_now_perf() - started) * 1000.0,
                    ok=False,
                    metadata={"reason": _stringify(getattr(exc, "reason", None)) or exc.__class__.__name__},
                )
                raise
            self.recorder.record_operation(
                "enforce_hosted_runtime_access",
                elapsed_ms=(_now_perf() - started) * 1000.0,
                ok=True,
                metadata={"enforcement_target": _stringify((result or {}).get("enforcement_target")) or None},
            )
            return result

        @asynccontextmanager
        async def wrapped_scoped_connection(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
            plan = self._current_db_latency_plan()
            delay_ms = int(plan.pool_acquire_ms or 0) if plan is not None else 0
            started = _now_perf()
            if delay_ms > 0:
                await asyncio.sleep(delay_ms / 1000.0)
            async with original_scoped_connection(*args, **kwargs) as connection:
                self.recorder.record_operation(
                    "scoped_connection_acquire",
                    elapsed_ms=(_now_perf() - started) * 1000.0,
                    ok=connection is not None,
                    metadata={"workspace_id": _stringify(kwargs.get("workspace_id")) or None},
                )
                if connection is None:
                    yield None
                else:
                    yield _LatencyInjectedConnection(
                        connection,
                        plan_provider=self._current_db_latency_plan,
                        recorder=self.recorder,
                    )

        self.monkeypatch.setattr(agent_channel_router, "route_inbound_channel_message", wrapped_route_inbound_channel_message)
        self.monkeypatch.setattr(channel_concurrency_service, "channel_execution_slot", wrapped_channel_execution_slot)
        self.monkeypatch.setattr(control_plane_repository, "append_agent_channel_event", wrapped_append_agent_channel_event)
        self.monkeypatch.setattr(control_plane_repository, "consume_deployed_agent_daily_message_quota", wrapped_consume_daily_quota)
        self.monkeypatch.setattr(control_plane_repository, "append_activity_ledger_event", wrapped_append_activity_ledger_event)
        self.monkeypatch.setattr(channel_memory_overlay_service, "persist_snapshot", wrapped_persist_snapshot)
        self.monkeypatch.setattr(deployed_agent_cost_cap_service, "settle_deployed_agent_monthly_cost_cap", wrapped_settle_budget)
        self.monkeypatch.setattr(entitlements_service, "enforce_hosted_runtime_access", wrapped_enforce_hosted_runtime_access)
        self.monkeypatch.setattr(notification_service, "deliver_notification_from_outbox_event", wrapped_deliver_notification)
        self.monkeypatch.setattr(control_plane_repository, "_scoped_connection", wrapped_scoped_connection)

    async def _pump_outbox_background(self, stop_event: asyncio.Event) -> None:
        assert self.harness is not None
        while not stop_event.is_set():
            await asyncio.to_thread(self.harness.pump_outbox_once)
            await asyncio.sleep(0.05)

    async def _run_dispatch_burst(
        self,
        *,
        connector_id: str,
        messages: Sequence[Dict[str, Any]],
        final_timeout_seconds: float = 60.0,
    ) -> List[RequestDispatchResult]:
        assert self.harness is not None
        stop_event = asyncio.Event()
        pump_task = asyncio.create_task(self._pump_outbox_background(stop_event))
        try:
            async with self.harness.async_client() as client:
                async def _send_one(item: Dict[str, Any]) -> RequestDispatchResult:
                    message_id = _stringify(item.get("message_id"))
                    chat_id = _stringify(item.get("chat_id"))
                    sender_id = _stringify(item.get("sender_id"))
                    text = _stringify(item.get("text"))
                    started_at = _now_perf()
                    self.recorder.register_request(
                        message_id=message_id,
                        chat_id=chat_id,
                        sender_id=sender_id,
                        text=text,
                        started_at=started_at,
                    )
                    response = await self.harness.telegram_webhook_async(
                        connector_id=connector_id,
                        text=text,
                        chat_id=chat_id,
                        sender_id=sender_id,
                        sender_username=_stringify(item.get("sender_username")) or sender_id,
                        sender_display_name=_stringify(item.get("sender_display_name")) or sender_id,
                        update_id=int(item.get("update_id") or 0) or None,
                        message_id=int(item.get("message_id") or 0) or None,
                        client=client,
                    )
                    responded_at = _now_perf()
                    payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
                    action = _stringify(payload.get("action")).lower() or ("accepted" if response.status_code == 200 else "error")
                    run_id = _stringify(payload.get("run_id")) or None
                    self.recorder.record_request_response(
                        message_id=message_id,
                        responded_at=responded_at,
                        status_code=response.status_code,
                        action=action,
                        run_id=run_id,
                        payload=payload if isinstance(payload, dict) else {},
                    )
                    return RequestDispatchResult(
                        message_id=message_id,
                        chat_id=chat_id,
                        sender_id=sender_id,
                        text=text,
                        started_at=started_at,
                        responded_at=responded_at,
                        status_code=response.status_code,
                        action=action,
                        run_id=run_id,
                        payload=payload if isinstance(payload, dict) else {},
                    )

                dispatches = list(await asyncio.gather(*[_send_one(item) for item in messages]))
                deadline = time.time() + max(3.0, float(final_timeout_seconds))
                while time.time() < deadline:
                    if self._dispatches_have_visible_result(dispatches):
                        break
                    await asyncio.sleep(0.1)
                return dispatches
        finally:
            stop_event.set()
            await asyncio.gather(pump_task, return_exceptions=True)

    def _dispatches_have_visible_result(self, dispatches: Sequence[RequestDispatchResult]) -> bool:
        for dispatch in dispatches:
            if dispatch.action == "accepted":
                final_event = self.recorder.final_transport_event_for_chat(
                    dispatch.chat_id,
                    after_started_at=dispatch.started_at,
                )
                if final_event is None and not self.recorder.transport_failures_for_chat(
                    dispatch.chat_id,
                    after_started_at=dispatch.started_at,
                ):
                    return False
            else:
                if self.recorder.final_transport_event_for_chat(dispatch.chat_id, after_started_at=dispatch.started_at) is None:
                    return False
        return True

    def run_telegram_burst(
        self,
        *,
        connector_id: str,
        messages: Sequence[Dict[str, Any]],
        final_timeout_seconds: float = 60.0,
    ) -> List[RequestDispatchResult]:
        return asyncio.run(
            self._run_dispatch_burst(
                connector_id=connector_id,
                messages=messages,
                final_timeout_seconds=final_timeout_seconds,
            )
        )


def _build_message_batch(
    *,
    count: int,
    prefix: str,
    text_template: str,
    start_message_id: int = 10_000,
    start_update_id: int = 20_000,
) -> List[Dict[str, Any]]:
    batch: List[Dict[str, Any]] = []
    for index in range(int(count)):
        batch.append(
            {
                "message_id": start_message_id + index,
                "update_id": start_update_id + index,
                "chat_id": f"{prefix}-chat-{index}",
                "sender_id": f"{prefix}-user-{index}",
                "sender_username": f"{prefix}_user_{index}",
                "sender_display_name": f"{prefix.title()} User {index}",
                "text": text_template.format(index=index),
            }
        )
    return batch


def _dispatch_status(dispatch: RequestDispatchResult, recorder: DrillRecorder) -> str:
    route = recorder.route_result_for_message(dispatch.message_id)
    route_reason = _stringify(route.get("limit_reason")).lower()
    route_status = _stringify(route.get("status")).lower()
    if dispatch.action != "accepted":
        return route_reason or route_status or dispatch.action or "error"
    if route_reason:
        return route_reason
    if recorder.final_transport_event_for_chat(dispatch.chat_id, after_started_at=dispatch.started_at) is not None:
        return "completed"
    if recorder.transport_failures_for_chat(dispatch.chat_id, after_started_at=dispatch.started_at):
        return "transport_delivery_failure"
    return "delivery_timeout"


def _dispatch_latency_ms(dispatch: RequestDispatchResult, recorder: DrillRecorder) -> Optional[float]:
    event = recorder.final_transport_event_for_chat(dispatch.chat_id, after_started_at=dispatch.started_at)
    if event is None:
        return None
    return max(0.0, (float(event.get("perf_counter") or 0.0) - float(dispatch.started_at)) * 1000.0)


def _status_mix(dispatches: Sequence[RequestDispatchResult], recorder: DrillRecorder) -> Dict[str, int]:
    counts: Counter[str] = Counter()
    for dispatch in dispatches:
        counts[_dispatch_status(dispatch, recorder)] += 1
    return {key: int(value) for key, value in sorted(counts.items())}


def _customer_latency_summary(dispatches: Sequence[RequestDispatchResult], recorder: DrillRecorder) -> Dict[str, float]:
    values = [
        latency
        for latency in (_dispatch_latency_ms(dispatch, recorder) for dispatch in dispatches)
        if latency is not None
    ]
    return _timing_summary(values)


def _first_failure(dispatches: Sequence[RequestDispatchResult], recorder: DrillRecorder, *, load_level: int) -> Optional[Dict[str, Any]]:
    failing = []
    for dispatch in dispatches:
        status = _dispatch_status(dispatch, recorder)
        if status == "completed":
            continue
        if status == "internal_error":
            entitlement_failures = [
                item
                for item in recorder.operation_records("enforce_hosted_runtime_access")
                if not bool(item.get("ok"))
            ]
            if entitlement_failures:
                status = _normalized_reason(entitlement_failures[0].get("reason"), "internal_error")
        failing.append(
            {
                "message_id": dispatch.message_id,
                "chat_id": dispatch.chat_id,
                "status": status,
                "component": _status_component(status),
            }
        )
    if not failing:
        return None
    first = failing[0]
    return {
        "load_level": int(load_level),
        "component": first["component"],
        "reason": first["status"],
    }


def _db_pressure_summary(recorder: DrillRecorder) -> Dict[str, Dict[str, float]]:
    return {
        "pool_wait": _timing_summary(recorder.operation_timings("scoped_connection_acquire")),
        "lease_acquisition": _timing_summary(recorder.operation_timings("channel_execution_slot")),
        "channel_event_write": _timing_summary(recorder.operation_timings("append_agent_channel_event")),
        "daily_quota_upsert": _timing_summary(recorder.operation_timings("consume_deployed_agent_daily_message_quota")),
        "activity_write": _timing_summary(recorder.operation_timings("append_activity_ledger_event")),
        "monthly_ledger": _timing_summary(recorder.operation_timings("settle_deployed_agent_monthly_cost_cap")),
    }


def _side_effect_summary(recorder: DrillRecorder) -> Dict[str, Any]:
    memory_records = recorder.operation_records("persist_deployed_agent_memory_snapshot")
    activity_records = recorder.operation_records("append_activity_ledger_event")
    settlement_records = recorder.operation_records("settle_deployed_agent_monthly_cost_cap")
    notification_records = recorder.operation_records("deliver_notification_from_outbox_event")
    return {
        "memory_snapshot": {
            "latency": _timing_summary([float(item.get("elapsed_ms") or 0.0) for item in memory_records]),
            "failures": int(sum(1 for item in memory_records if not bool(item.get("ok")))),
        },
        "activity": {
            "latency": _timing_summary([float(item.get("elapsed_ms") or 0.0) for item in activity_records]),
            "failures": int(sum(1 for item in activity_records if not bool(item.get("ok")))),
        },
        "budget_settlement": {
            "latency": _timing_summary([float(item.get("elapsed_ms") or 0.0) for item in settlement_records]),
            "failures": int(sum(1 for item in settlement_records if not bool(item.get("ok")))),
        },
        "notification_delivery": {
            "latency": _timing_summary([float(item.get("elapsed_ms") or 0.0) for item in notification_records]),
            "failures": int(sum(1 for item in notification_records if not bool(item.get("ok")))),
        },
    }


def _baseline_workload(*, users: int, deployments: int, workspaces: int, channel: str, duration_seconds: float) -> Dict[str, Any]:
    return {
        "users": int(users),
        "deployments": int(deployments),
        "workspaces": int(workspaces),
        "channel": str(channel),
        "duration_seconds": round(float(duration_seconds), 2),
        "provider_stub_latency_seconds": BASELINE_PROVIDER_DELAY_SECONDS,
        "db_pool_size": BASELINE_DB_POOL_SIZE,
    }


def run_fan_in_scenario(*, users: int) -> Dict[str, Any]:
    started = _now_perf()
    with PublicBotDrillEnvironment() as env:
        owner = env.harness.register_owner(email_prefix=f"prompt14-fanin-{users}")  # type: ignore[union-attr]
        live = env.harness.create_live_telegram_deployed_agent(owner=owner, name=f"FanIn-{users}")  # type: ignore[union-attr]
        connector = live["connector"]
        dispatches = env.run_telegram_burst(
            connector_id=connector["id"],
            messages=_build_message_batch(
                count=users,
                prefix=f"fanin{users}",
                text_template="Fan-in load message {index}",
            ),
            final_timeout_seconds=120.0,
        )
        duration = _now_perf() - started
        status_mix = _status_mix(dispatches, env.recorder)
        return {
            "scenario": "fan_in",
            "workload": _baseline_workload(users=users, deployments=1, workspaces=1, channel="telegram", duration_seconds=duration),
            "user_latency": _customer_latency_summary(dispatches, env.recorder),
            "status_mix": status_mix,
            "first_failure": _first_failure(dispatches, env.recorder, load_level=users),
            "db_pressure": _db_pressure_summary(env.recorder),
            "side_effects": _side_effect_summary(env.recorder),
            "customer_impact": {
                "lost_replies": int(sum(1 for dispatch in dispatches if _dispatch_status(dispatch, env.recorder) in {"transport_delivery_failure", "delivery_timeout"})),
                "duplicate_replies": 0,
                "wrong_fallback_responses": 0,
            },
            "owner_impact": {
                "missing_activity_events": int(sum(1 for item in env.recorder.operation_records("append_activity_ledger_event") if not bool(item.get("ok")))),
                "stale_notifications": 0,
            },
        }


def run_fan_out_scenario(*, users: int, workspace_count: int = 8, deployments_per_workspace: int = 2) -> Dict[str, Any]:
    started = _now_perf()
    with PublicBotDrillEnvironment() as env:
        connectors: List[str] = []
        for workspace_index in range(int(workspace_count)):
            owner = env.harness.register_owner(email_prefix=f"prompt14-fanout-{workspace_index}")  # type: ignore[union-attr]
            for deployment_index in range(int(deployments_per_workspace)):
                live = env.harness.create_live_telegram_deployed_agent(  # type: ignore[union-attr]
                    owner=owner,
                    name=f"FanOut-{workspace_index}-{deployment_index}",
                )
                connectors.append(str(live["connector"]["id"]))
        messages: List[Dict[str, Any]] = []
        for index in range(int(users)):
            connector_id = connectors[index % len(connectors)]
            messages.append(
                {
                    "connector_id": connector_id,
                    "message_id": 30_000 + index,
                    "update_id": 40_000 + index,
                    "chat_id": f"fanout-chat-{index}",
                    "sender_id": f"fanout-user-{index}",
                    "sender_username": f"fanout_user_{index}",
                    "sender_display_name": f"FanOut User {index}",
                    "text": f"Fan-out load message {index}",
                }
            )

        async def _dispatch() -> List[RequestDispatchResult]:
            assert env.harness is not None
            stop_event = asyncio.Event()
            pump_task = asyncio.create_task(env._pump_outbox_background(stop_event))
            try:
                async with env.harness.async_client() as client:
                    async def _send(item: Dict[str, Any]) -> RequestDispatchResult:
                        message_id = _stringify(item.get("message_id"))
                        started_at = _now_perf()
                        env.recorder.register_request(
                            message_id=message_id,
                            chat_id=_stringify(item.get("chat_id")),
                            sender_id=_stringify(item.get("sender_id")),
                            text=_stringify(item.get("text")),
                            started_at=started_at,
                        )
                        response = await env.harness.telegram_webhook_async(
                            connector_id=_stringify(item.get("connector_id")),
                            text=_stringify(item.get("text")),
                            chat_id=_stringify(item.get("chat_id")),
                            sender_id=_stringify(item.get("sender_id")),
                            sender_username=_stringify(item.get("sender_username")),
                            sender_display_name=_stringify(item.get("sender_display_name")),
                            update_id=int(item.get("update_id") or 0) or None,
                            message_id=int(item.get("message_id") or 0) or None,
                            client=client,
                        )
                        responded_at = _now_perf()
                        payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
                        action = _stringify(payload.get("action")).lower() or ("accepted" if response.status_code == 200 else "error")
                        run_id = _stringify(payload.get("run_id")) or None
                        env.recorder.record_request_response(
                            message_id=message_id,
                            responded_at=responded_at,
                            status_code=response.status_code,
                            action=action,
                            run_id=run_id,
                            payload=payload if isinstance(payload, dict) else {},
                        )
                        return RequestDispatchResult(
                            message_id=message_id,
                            chat_id=_stringify(item.get("chat_id")),
                            sender_id=_stringify(item.get("sender_id")),
                            text=_stringify(item.get("text")),
                            started_at=started_at,
                            responded_at=responded_at,
                            status_code=response.status_code,
                            action=action,
                            run_id=run_id,
                            payload=payload if isinstance(payload, dict) else {},
                        )

                    dispatches = list(await asyncio.gather(*[_send(item) for item in messages]))
                    deadline = time.time() + 120.0
                    while time.time() < deadline:
                        if env._dispatches_have_visible_result(dispatches):
                            break
                        await asyncio.sleep(0.1)
                    return dispatches
            finally:
                stop_event.set()
                await asyncio.gather(pump_task, return_exceptions=True)

        dispatches = asyncio.run(_dispatch())
        duration = _now_perf() - started
        return {
            "scenario": "fan_out",
            "workload": _baseline_workload(
                users=users,
                deployments=workspace_count * deployments_per_workspace,
                workspaces=workspace_count,
                channel="telegram",
                duration_seconds=duration,
            ),
            "user_latency": _customer_latency_summary(dispatches, env.recorder),
            "status_mix": _status_mix(dispatches, env.recorder),
            "first_failure": _first_failure(dispatches, env.recorder, load_level=users),
            "db_pressure": _db_pressure_summary(env.recorder),
            "side_effects": _side_effect_summary(env.recorder),
            "customer_impact": {
                "lost_replies": int(sum(1 for dispatch in dispatches if _dispatch_status(dispatch, env.recorder) in {"transport_delivery_failure", "delivery_timeout"})),
                "duplicate_replies": 0,
                "wrong_fallback_responses": 0,
            },
            "owner_impact": {
                "missing_activity_events": int(sum(1 for item in env.recorder.operation_records("append_activity_ledger_event") if not bool(item.get("ok")))),
                "stale_notifications": 0,
            },
        }


def run_quota_pressure_scenario() -> Dict[str, Any]:
    started = _now_perf()
    with PublicBotDrillEnvironment() as env:
        owner = env.harness.register_owner(email_prefix="prompt14-quota")  # type: ignore[union-attr]
        live = env.harness.create_live_telegram_deployed_agent(  # type: ignore[union-attr]
            owner=owner,
            name="QuotaPressure",
            config_overrides={
                "customer_policy": {
                    "daily_message_limit": 1,
                    "upgrade_cta_label": "Upgrade",
                    "upgrade_cta_url": "https://blackbox.example/upgrade",
                },
            },
        )
        connector_id = live["connector"]["id"]
        first = env.run_telegram_burst(
            connector_id=connector_id,
            messages=[
                {
                    "message_id": 50_000,
                    "update_id": 60_000,
                    "chat_id": "quota-target-chat",
                    "sender_id": "quota-target-user",
                    "sender_username": "quota_target",
                    "sender_display_name": "Quota Target",
                    "text": "First question",
                }
            ],
        )[0]
        second_and_background = env.run_telegram_burst(
            connector_id=connector_id,
            messages=[
                {
                    "message_id": 50_001,
                    "update_id": 60_001,
                    "chat_id": "quota-target-chat",
                    "sender_id": "quota-target-user",
                    "sender_username": "quota_target",
                    "sender_display_name": "Quota Target",
                    "text": "Second question should be capped",
                },
                *_build_message_batch(
                    count=64,
                    prefix="quota-bg",
                    text_template="Background traffic {index}",
                    start_message_id=51_000,
                    start_update_id=61_000,
                ),
            ],
            final_timeout_seconds=90.0,
        )
        duration = _now_perf() - started
        target_dispatch = next(item for item in second_and_background if item.message_id == "50001")
        target_status = _dispatch_status(target_dispatch, env.recorder)
        target_transport = env.recorder.final_transport_event_for_chat(
            target_dispatch.chat_id,
            after_started_at=target_dispatch.started_at,
        )
        target_text = _stringify(((target_transport or {}).get("payload") or {}).get("text"))
        wrong_responses = 0 if target_status in {"daily_message_limit_exceeded", "deployed_agent_daily_limit_exceeded"} else 1
        return {
            "scenario": "quota_pressure",
            "workload": _baseline_workload(users=65, deployments=1, workspaces=1, channel="telegram", duration_seconds=duration),
            "target_status": target_status,
            "target_visible_text": target_text,
            "wrong_response_count": wrong_responses,
            "quota_row_latency": _timing_summary(env.recorder.operation_timings("consume_deployed_agent_daily_message_quota")),
            "status_mix": _status_mix([first, *second_and_background], env.recorder),
            "first_failure": {
                "load_level": 2,
                "component": _status_component(target_status),
                "reason": target_status,
            },
            "db_pressure": _db_pressure_summary(env.recorder),
            "side_effects": _side_effect_summary(env.recorder),
            "customer_impact": {
                "lost_replies": 0,
                "duplicate_replies": 0,
                "wrong_fallback_responses": wrong_responses,
            },
            "owner_impact": {
                "missing_activity_events": int(sum(1 for item in env.recorder.operation_records("append_activity_ledger_event") if not bool(item.get("ok")))),
                "stale_notifications": 0,
            },
        }


def run_connector_failure_scenario() -> Dict[str, Any]:
    started = _now_perf()
    with PublicBotDrillEnvironment() as env:
        owner = env.harness.register_owner(email_prefix="prompt14-connector")  # type: ignore[union-attr]
        live = env.harness.create_live_telegram_deployed_agent(owner=owner, name="ConnectorFailure")  # type: ignore[union-attr]
        connector_id = live["connector"]["id"]
        env.set_telegram_delivery_failure(methods={"editMessageText"})
        dispatches = env.run_telegram_burst(
            connector_id=connector_id,
            messages=_build_message_batch(
                count=1,
                prefix="connector-fail",
                text_template="Connector failure message {index}",
                start_message_id=70_000,
                start_update_id=80_000,
            ),
            final_timeout_seconds=45.0,
        )
        failure_status = env.harness.get_outbox_status()  # type: ignore[union-attr]
        poisoned_before = env.harness.list_poisoned_outbox_events(limit=200)  # type: ignore[union-attr]
        recovery_started = _now_perf()
        env.clear_telegram_delivery_failure()
        recovery_observed = False
        try:
            env.harness.pump_outbox_until_idle(  # type: ignore[union-attr]
                timeout_seconds=45.0,
                interval_seconds=0.25,
            )
            recovery_observed = True
        except AssertionError:
            recovery_observed = False
        recovery_duration = (_now_perf() - recovery_started) * 1000.0
        recovery_status = env.harness.get_outbox_status()  # type: ignore[union-attr]
        recovery_complete = (
            recovery_observed
            and int(recovery_status.get("undelivered_count") or 0) == 0
            and int(recovery_status.get("claimed_count") or 0) == 0
        )
        duration = _now_perf() - started
        duplicate_replies = 0
        for dispatch in dispatches:
            successful = [
                event
                for event in env.recorder.transport_events_for_chat(dispatch.chat_id, after_started_at=dispatch.started_at)
                if bool(event.get("success")) and str(event.get("method_name") or "") == "editMessageText"
            ]
            if len(successful) > 1:
                duplicate_replies += len(successful) - 1
        return {
            "scenario": "connector_failure",
            "workload": _baseline_workload(users=len(dispatches), deployments=1, workspaces=1, channel="telegram", duration_seconds=duration),
            "status_mix": _status_mix(dispatches, env.recorder),
            "first_failure": {
                "load_level": 1,
                "component": "telegram_transport_delivery",
                "reason": "transport_delivery_failure",
            },
            "delivery_backlog": dict(failure_status),
            "recovery_complete": recovery_complete,
            "recovery_status": dict(recovery_status),
            "poisoned_before_recovery": int(len(poisoned_before)),
            "recovery_ms": round(recovery_duration, 2),
            "db_pressure": _db_pressure_summary(env.recorder),
            "side_effects": _side_effect_summary(env.recorder),
            "customer_impact": {
                "lost_replies": int(sum(1 for dispatch in dispatches if _dispatch_status(dispatch, env.recorder) == "transport_delivery_failure")),
                "duplicate_replies": int(duplicate_replies),
                "wrong_fallback_responses": 0,
            },
            "owner_impact": {
                "missing_activity_events": int(sum(1 for item in env.recorder.operation_records("append_activity_ledger_event") if not bool(item.get("ok")))),
                "stale_notifications": 0,
            },
        }


def run_db_slowness_scenario(*, injected_latency_ms: int) -> Dict[str, Any]:
    started = _now_perf()
    with PublicBotDrillEnvironment() as env:
        env.set_db_latency_plan(
            DbLatencyPlan(
                pool_acquire_ms=int(injected_latency_ms),
                target_latency_ms={
                    "agent_channel_event": int(injected_latency_ms),
                    "daily_quota": int(injected_latency_ms),
                    "lease": int(injected_latency_ms),
                    "monthly_ledger": int(injected_latency_ms),
                },
            )
        )
        owner = env.harness.register_owner(email_prefix=f"prompt14-db-{injected_latency_ms}")  # type: ignore[union-attr]
        live = env.harness.create_live_telegram_deployed_agent(owner=owner, name=f"DbSlow-{injected_latency_ms}")  # type: ignore[union-attr]
        connector_id = live["connector"]["id"]
        dispatches: List[RequestDispatchResult] = []
        for index, message in enumerate(
            _build_message_batch(
                count=4,
                prefix=f"dbslow{injected_latency_ms}",
                text_template="DB slow message {index}",
                start_message_id=90_000 + injected_latency_ms,
                start_update_id=100_000 + injected_latency_ms,
            )
        ):
            dispatches.extend(
                env.run_telegram_burst(
                    connector_id=connector_id,
                    messages=[message],
                    final_timeout_seconds=120.0,
                )
            )
        duration = _now_perf() - started
        return {
            "scenario": "db_slowness",
            "injected_latency_ms": int(injected_latency_ms),
            "workload": _baseline_workload(users=len(dispatches), deployments=1, workspaces=1, channel="telegram", duration_seconds=duration),
            "user_latency": _customer_latency_summary(dispatches, env.recorder),
            "status_mix": _status_mix(dispatches, env.recorder),
            "first_failure": _first_failure(dispatches, env.recorder, load_level=len(dispatches)),
            "db_pressure": _db_pressure_summary(env.recorder),
            "side_effects": _side_effect_summary(env.recorder),
            "customer_impact": {
                "lost_replies": int(sum(1 for dispatch in dispatches if _dispatch_status(dispatch, env.recorder) in {"delivery_timeout", "transport_delivery_failure"})),
                "duplicate_replies": 0,
                "wrong_fallback_responses": 0,
            },
            "owner_impact": {
                "missing_activity_events": int(sum(1 for item in env.recorder.operation_records("append_activity_ledger_event") if not bool(item.get("ok")))),
                "stale_notifications": 0,
            },
        }


def run_notification_loss_scenario() -> Dict[str, Any]:
    started = _now_perf()
    with PublicBotDrillEnvironment() as env:
        env.enable_notification_failure()
        owner = env.harness.register_owner(email_prefix="prompt14-notify")  # type: ignore[union-attr]
        live = env.harness.create_live_telegram_deployed_agent(  # type: ignore[union-attr]
            owner=owner,
            name="NotifyFailure",
            config_overrides={
                "customer_policy": {
                    "paused_message": "NotifyFailure paused after budget breach.",
                },
                "commerce_policy": {
                    "monthly_cost_cap_usd": 0.000001,
                },
            },
        )
        deployed_agent = live["deployed_agent"]
        connector_id = live["connector"]["id"]
        dispatch = env.run_telegram_burst(
            connector_id=connector_id,
            messages=[
                {
                    "message_id": 120_000,
                    "update_id": 130_000,
                    "chat_id": "notify-loss-chat",
                    "sender_id": "notify-loss-user",
                    "sender_username": "notify_loss_user",
                    "sender_display_name": "Notify Loss User",
                    "text": "Trip the monthly budget cap",
                }
            ],
            final_timeout_seconds=90.0,
        )[0]
        paused = env.harness.wait_for_deployed_agent_state(  # type: ignore[union-attr]
            owner=owner,
            deployed_agent_id=deployed_agent["id"],
            expected_state="paused",
            timeout_seconds=30.0,
        )
        def _budget_notification_count() -> int:
            payload = env.harness.list_notifications(owner=owner)  # type: ignore[union-attr]
            return int(
                sum(
                    1
                    for item in list(payload.get("items") or [])
                    if _stringify(item.get("action")).startswith("deployed_agent_budget_")
                )
            )

        notifications_before = _budget_notification_count()
        recovery_started = _now_perf()
        env.clear_notification_failure()
        env.harness.pump_outbox_until(  # type: ignore[union-attr]
            lambda: _budget_notification_count() > 0,
            timeout_seconds=45.0,
            interval_seconds=0.25,
        )
        recovery_ms = (_now_perf() - recovery_started) * 1000.0
        notifications_after = _budget_notification_count()
        duration = _now_perf() - started
        return {
            "scenario": "notification_loss",
            "workload": _baseline_workload(users=1, deployments=1, workspaces=1, channel="telegram", duration_seconds=duration),
            "paused_state": {
                "deployment_state": paused.get("deployment_state"),
                "budget_cycle": ((paused.get("operational_state") or {}).get("current_budget_cycle") or {}) if isinstance(paused.get("operational_state"), dict) else {},
            },
            "notifications_before_recovery": notifications_before,
            "notifications_after_recovery": notifications_after,
            "recovery_ms": round(recovery_ms, 2),
            "first_failure": {
                "load_level": 1,
                "component": "notification_delivery",
                "reason": "notification_delivery_failure",
            },
            "db_pressure": _db_pressure_summary(env.recorder),
            "side_effects": _side_effect_summary(env.recorder),
            "customer_impact": {
                "lost_replies": 0 if _dispatch_status(dispatch, env.recorder) == "completed" else 1,
                "duplicate_replies": 0,
                "wrong_fallback_responses": 0,
            },
            "owner_impact": {
                "missing_activity_events": 0,
                "stale_notifications": 1 if int(notifications_before or 0) <= 0 else 0,
            },
        }


def _find_failure_threshold(
    *,
    scenario_runner: Callable[[int], Dict[str, Any]],
    search_levels: Sequence[int],
) -> Dict[str, Any]:
    runs: List[Dict[str, Any]] = []
    first_failure: Optional[Dict[str, Any]] = None
    for level in list(search_levels):
        result = scenario_runner(int(level))
        runs.append(result)
        failure = result.get("first_failure") if isinstance(result.get("first_failure"), dict) else None
        if failure is not None:
            first_failure = {
                "load_level": int(level),
                "reason": failure.get("reason"),
                "component": failure.get("component"),
            }
            break
    return {
        "runs": runs,
        "first_failure": first_failure,
    }


def _playbooks(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    fan_in = report.get("fan_in_1000") if isinstance(report.get("fan_in_1000"), dict) else {}
    db_threshold = report.get("db_threshold") if isinstance(report.get("db_threshold"), dict) else {}
    connector = report.get("connector_failure") if isinstance(report.get("connector_failure"), dict) else {}
    notification = report.get("notification_loss") if isinstance(report.get("notification_loss"), dict) else {}
    return [
        {
            "name": "saturation",
            "trigger_signal": fan_in.get("first_failure"),
            "customer_visible_symptom": "Busy or rate-limited public replies start appearing instead of normal completions.",
            "owner_visible_symptom": "Activity volume stays high while completion mix shifts toward limits.",
            "first_mitigation": "Enable incident drain or reduce ingress to keep the first visible failure deterministic.",
            "exit_criteria": "Lease-acquisition latency and public completion mix return to baseline.",
        },
        {
            "name": "db_slowness",
            "trigger_signal": db_threshold.get("first_unacceptable_p95"),
            "customer_visible_symptom": "Ingress-to-visible reply latency crosses the operational p95 threshold.",
            "owner_visible_symptom": "Channel-event, quota, and lease timings all inflate together.",
            "first_mitigation": "Drain traffic or pause public ingress while control-plane query latency normalizes.",
            "exit_criteria": "Channel-event and lease timings recover and p95 visible latency drops below the threshold.",
        },
        {
            "name": "connector_delivery_failure",
            "trigger_signal": connector.get("first_failure"),
            "customer_visible_symptom": "Inbound webhook succeeds but final Telegram delivery does not arrive.",
            "owner_visible_symptom": "Outbox backlog or poisoned events rise while runs still complete.",
            "first_mitigation": "Stop unsafe replay, restore Telegram delivery, then drain the outbox.",
            "exit_criteria": "Undelivered and poisoned outbox counts return to zero without duplicate replies.",
        },
        {
            "name": "notification_loss",
            "trigger_signal": notification.get("first_failure"),
            "customer_visible_symptom": "Customer path stays healthy while owner alerts are missing.",
            "owner_visible_symptom": "Paused deployment state is visible before the owner receives the budget notification.",
            "first_mitigation": "Recover notification delivery and replay the notification outbox.",
            "exit_criteria": "Notification lag returns to zero and owner-visible alerts match paused state.",
        },
        {
            "name": "budget_inconsistency",
            "trigger_signal": notification.get("paused_state"),
            "customer_visible_symptom": "Budget-triggered pause may lag behind recent spend if settlement stalls.",
            "owner_visible_symptom": "Budget cycle, deployment state, and notifications disagree temporarily.",
            "first_mitigation": "Inspect budget-cycle state, monthly ledger rows, and pending notification outbox together.",
            "exit_criteria": "Deployment state, budget cycle, and owner notifications all agree on the same cap event.",
        },
    ]


def build_prompt14_report() -> Dict[str, Any]:
    fan_in_threshold = _find_failure_threshold(
        scenario_runner=lambda users: run_fan_in_scenario(users=users),
        search_levels=(1, 2, 4, 8, 10, 12, 16, 24, 32, 48, 64, 96, 128, 160, 180, 181, 192, 256, 512, 1000),
    )
    fan_in_1000 = run_fan_in_scenario(users=1000)
    fan_out_threshold = _find_failure_threshold(
        scenario_runner=lambda users: run_fan_out_scenario(users=users),
        search_levels=(1, 8, 9, 10, 16, 32, 64, 128, 256, 512, 1000),
    )
    fan_out_1000 = run_fan_out_scenario(users=1000)
    quota_pressure = run_quota_pressure_scenario()
    connector_failure = run_connector_failure_scenario()
    db_runs = [run_db_slowness_scenario(injected_latency_ms=value) for value in (50, 200, 500, 1000)]
    notification_loss = run_notification_loss_scenario()

    first_unacceptable_p95 = next(
        (
            item
            for item in db_runs
            if float(((item.get("user_latency") or {}).get("p95_ms") or 0.0)) >= UNACCEPTABLE_P95_MS
        ),
        None,
    )
    first_runtime_cap = next(
        (
            item
            for item in db_runs
            if int(((item.get("status_mix") or {}).get("runtime_cap_exceeded") or 0)) > 0
        ),
        None,
    )

    report: Dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "assumptions": {
            "operationally_unacceptable_p95_ms": UNACCEPTABLE_P95_MS,
            "provider_stub_latency_seconds": BASELINE_PROVIDER_DELAY_SECONDS,
            "db_pool_size": BASELINE_DB_POOL_SIZE,
        },
        "fan_in_1000": fan_in_1000,
        "fan_in_threshold_search": fan_in_threshold,
        "fan_out_1000": fan_out_1000,
        "fan_out_threshold_search": fan_out_threshold,
        "quota_pressure": quota_pressure,
        "connector_failure": connector_failure,
        "db_slowness": db_runs,
        "db_threshold": {
            "first_unacceptable_p95": first_unacceptable_p95,
            "first_runtime_cap": first_runtime_cap,
        },
        "notification_loss": notification_loss,
    }
    fan_in_first_failure = fan_in_threshold.get("first_failure") if isinstance(fan_in_threshold.get("first_failure"), dict) else None
    fan_out_first_failure = fan_out_threshold.get("first_failure") if isinstance(fan_out_threshold.get("first_failure"), dict) else None
    report["hard_numbers"] = {
        "fan_in_first_visible_failure_count": ((fan_in_first_failure or {}).get("load_level")),
        "fan_in_first_visible_failure_reason": ((fan_in_first_failure or {}).get("reason")),
        "fan_out_first_visible_failure_count": ((fan_out_first_failure or {}).get("load_level")),
        "fan_out_first_visible_failure_reason": ((fan_out_first_failure or {}).get("reason") if fan_out_first_failure else "none_observed_up_to_1000"),
        "quota_pressure_first_failure_reason": quota_pressure.get("target_status"),
        "connector_failure_first_failure_reason": ((connector_failure.get("first_failure") or {}).get("reason")),
        "db_first_unacceptable_p95_latency_ms": ((first_unacceptable_p95 or {}).get("injected_latency_ms")),
        "db_runtime_cap_onset_latency_ms": ((first_runtime_cap or {}).get("injected_latency_ms")),
        "notification_recovery_ms": notification_loss.get("recovery_ms"),
        "budget_settlement_p95_ms_under_notification_loss": (((notification_loss.get("side_effects") or {}).get("budget_settlement") or {}).get("latency") or {}).get("p95_ms"),
    }
    report["playbooks"] = _playbooks(report)
    return report


def render_prompt14_markdown(report: Mapping[str, Any]) -> str:
    hard_numbers = report.get("hard_numbers") if isinstance(report.get("hard_numbers"), dict) else {}
    fan_in = report.get("fan_in_1000") if isinstance(report.get("fan_in_1000"), dict) else {}
    fan_out = report.get("fan_out_1000") if isinstance(report.get("fan_out_1000"), dict) else {}
    quota = report.get("quota_pressure") if isinstance(report.get("quota_pressure"), dict) else {}
    connector = report.get("connector_failure") if isinstance(report.get("connector_failure"), dict) else {}
    notification = report.get("notification_loss") if isinstance(report.get("notification_loss"), dict) else {}
    db_threshold = report.get("db_threshold") if isinstance(report.get("db_threshold"), dict) else {}
    lines = [
        "# Prompt 14 Public Bot Drill Report",
        "",
        f"Generated at: {report.get('generated_at')}",
        "",
        "## Hard Numbers",
        "",
        f"- Fan-in first visible failure count: {hard_numbers.get('fan_in_first_visible_failure_count')}",
        f"- Fan-in first visible failure reason: {hard_numbers.get('fan_in_first_visible_failure_reason')}",
        f"- Fan-out first visible failure count: {hard_numbers.get('fan_out_first_visible_failure_count') or '>1000 not observed'}",
        f"- Fan-out first visible failure reason: {hard_numbers.get('fan_out_first_visible_failure_reason')}",
        f"- Quota-pressure visible winner: {hard_numbers.get('quota_pressure_first_failure_reason')}",
        f"- Connector-failure first visible reason: {hard_numbers.get('connector_failure_first_failure_reason')}",
        f"- First injected DB latency with unacceptable p95: {hard_numbers.get('db_first_unacceptable_p95_latency_ms')}",
        f"- First injected DB latency with runtime caps: {hard_numbers.get('db_runtime_cap_onset_latency_ms') or 'not observed'}",
        f"- Notification recovery ms: {hard_numbers.get('notification_recovery_ms')}",
        f"- Budget-settlement p95 ms during notification-loss drill: {hard_numbers.get('budget_settlement_p95_ms_under_notification_loss')}",
        "",
        "## Scenario Summary",
        "",
        f"- Fan-in 1000 status mix: {json.dumps(fan_in.get('status_mix') or {}, sort_keys=True)}",
        f"- Fan-out 1000 status mix: {json.dumps(fan_out.get('status_mix') or {}, sort_keys=True)}",
        f"- Quota pressure wrong fallback count: {quota.get('wrong_response_count')}",
        f"- Connector failure backlog snapshot: {json.dumps(connector.get('delivery_backlog') or {}, sort_keys=True)}",
        f"- Notification loss before recovery: {notification.get('notifications_before_recovery')}",
        f"- Notification loss after recovery: {notification.get('notifications_after_recovery')}",
        "",
        "## DB Threshold",
        "",
        f"- First unacceptable p95 record: {json.dumps(db_threshold.get('first_unacceptable_p95') or {}, sort_keys=True)}",
        f"- First runtime-cap record: {json.dumps(db_threshold.get('first_runtime_cap') or {}, sort_keys=True)}",
        "",
        "## Recovery Playbooks",
        "",
    ]
    for item in list(report.get("playbooks") or []):
        if not isinstance(item, dict):
            continue
        lines.extend(
            [
                f"### {item.get('name')}",
                "",
                f"- Trigger signal: {json.dumps(item.get('trigger_signal') or {}, sort_keys=True)}",
                f"- Customer-visible symptom: {item.get('customer_visible_symptom')}",
                f"- Owner-visible symptom: {item.get('owner_visible_symptom')}",
                f"- First mitigation: {item.get('first_mitigation')}",
                f"- Exit criteria: {item.get('exit_criteria')}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def write_prompt14_report(*, output_path: Path) -> Dict[str, Any]:
    report = build_prompt14_report()
    json_path = output_path.with_suffix(".json")
    markdown_payload = render_prompt14_markdown(report)
    json_payload = json.dumps(report, indent=2, sort_keys=True)
    _enforce_public_bot_drill_report_write(
        output_path=output_path,
        json_path=json_path,
        markdown_payload=markdown_payload,
        json_payload=json_payload,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown_payload, encoding="utf-8")
    json_path.write_text(json_payload, encoding="utf-8")
    return report
