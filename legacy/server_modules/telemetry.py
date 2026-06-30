from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from math import ceil
import os
import sys
from threading import Lock
from typing import Any, Dict, Iterable, Iterator, Mapping, Optional, Sequence

try:
    from opentelemetry import trace as _otel_trace
    from opentelemetry.sdk.trace import TracerProvider as _TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor as _BatchSpanProcessor
    from opentelemetry.sdk.trace.export import ConsoleSpanExporter as _ConsoleSpanExporter

    _OTEL_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    _otel_trace = None
    _TracerProvider = None
    _BatchSpanProcessor = None
    _ConsoleSpanExporter = None
    _OTEL_AVAILABLE = False


_INITIALIZED = False
_INIT_LOCK = Lock()
_RELIABILITY_LOCK = Lock()
_RELIABILITY_SAMPLE_LIMIT = 256
_CONTROL_PLANE_AVAILABILITY_TARGET = 99.9
_LATENCY_TARGETS_MS: Dict[str, Dict[str, Any]] = {
    "run_enqueue_ack_latency_ms": {
        "label": "Run enqueue acknowledgment latency",
        "target_p95_ms": 500.0,
        "description": "Measured from create_live_run entry to canonical run activation/queue acknowledgment.",
    },
    "approval_propagation_latency_ms": {
        "label": "Approval propagation latency",
        "target_p95_ms": 2000.0,
        "description": "Measured from approval requested_at to approval resolved_at.",
    },
    "artifact_availability_latency_ms": {
        "label": "Artifact availability latency",
        "target_p95_ms": 5000.0,
        "description": "Measured from canonical artifact store request start to artifact metadata persistence and object availability through artifact:// resolution.",
    },
    "machine_revocation_propagation_latency_ms": {
        "label": "Machine lease revocation propagation latency",
        "target_p95_ms": 5000.0,
        "description": "Measured from operator revoke action acceptance to the first worker control-state response that reports machine_revoked and pause_requested.",
    },
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _new_latency_metric() -> Dict[str, Any]:
    return {
        "samples": deque(maxlen=_RELIABILITY_SAMPLE_LIMIT),
        "count": 0,
        "sum_ms": 0.0,
        "min_ms": None,
        "max_ms": None,
        "last_ms": None,
        "last_recorded_at": None,
        "last_metadata": None,
    }


def _new_reliability_state() -> Dict[str, Any]:
    return {
        "started_at": _utc_now_iso(),
        "control_plane_api": {
            "request_count": 0,
            "available_count": 0,
            "error_count": 0,
            "last_seen_at": None,
            "last_request": None,
            "last_error": None,
            "latency_ms": _new_latency_metric(),
        },
        "latency_metrics": {name: _new_latency_metric() for name in _LATENCY_TARGETS_MS},
        "pending_machine_revocations": {},
    }


_RELIABILITY_STATE = _new_reliability_state()


class _NoopSpanContext:
    trace_id = 0


class _NoopSpan:
    def set_attribute(self, _key: str, _value: Any) -> None:
        return None

    def record_exception(self, _exc: BaseException) -> None:
        return None

    def get_span_context(self) -> _NoopSpanContext:
        return _NoopSpanContext()


@contextmanager
def _noop_span_manager() -> Iterator[_NoopSpan]:
    yield _NoopSpan()


class _NoopTracer:
    def start_as_current_span(self, _name: str, **_kwargs: Any):
        return _noop_span_manager()


_NOOP_TRACER = _NoopTracer()


def _initialize_tracing() -> None:
    global _INITIALIZED
    if _INITIALIZED:
        return
    with _INIT_LOCK:
        if _INITIALIZED:
            return
        if not _OTEL_AVAILABLE:
            _INITIALIZED = True
            return
        traces_exporter = str(os.getenv("OTEL_TRACES_EXPORTER") or "").strip().lower()
        if traces_exporter == "none":
            _INITIALIZED = True
            return
        try:
            provider = _TracerProvider()
            console_stream = getattr(sys, "__stderr__", None) or sys.stderr
            processor = _BatchSpanProcessor(_ConsoleSpanExporter(out=console_stream))
            provider.add_span_processor(processor)
            _otel_trace.set_tracer_provider(provider)
        except Exception:
            pass
        _INITIALIZED = True


def get_tracer(name: str = "empyralis.runtime") -> Any:
    if not _OTEL_AVAILABLE:
        return _NOOP_TRACER
    _initialize_tracing()
    try:
        return _otel_trace.get_tracer(name)
    except Exception:
        return _NOOP_TRACER


def span_trace_id(span: Any) -> Optional[str]:
    try:
        context = span.get_span_context()
        trace_id = int(getattr(context, "trace_id", 0) or 0)
    except Exception:
        return None
    if trace_id <= 0:
        return None
    return f"{trace_id:032x}"


def _coerce_attribute_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        coerced = []
        for item in value:
            normalized = _coerce_attribute_value(item)
            if normalized is None:
                continue
            if isinstance(normalized, (bool, int, float, str)):
                coerced.append(normalized)
            else:
                coerced.append(str(normalized))
        return tuple(coerced)
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except Exception:
        return str(value)


def set_span_attributes(span: Any, attributes: Dict[str, Any]) -> Optional[str]:
    if span is None or not isinstance(attributes, dict):
        return span_trace_id(span)
    for key, value in attributes.items():
        normalized_key = str(key or "").strip()
        if not normalized_key:
            continue
        normalized_value = _coerce_attribute_value(value)
        if normalized_value is None:
            continue
        try:
            span.set_attribute(normalized_key, normalized_value)
        except Exception:
            continue
    trace_id = span_trace_id(span)
    if trace_id:
        try:
            span.set_attribute("trace_id", trace_id)
        except Exception:
            pass
    return trace_id


def reset_reliability_metrics() -> None:
    global _RELIABILITY_STATE
    with _RELIABILITY_LOCK:
        _RELIABILITY_STATE = _new_reliability_state()


def reliability_started_at() -> str:
    with _RELIABILITY_LOCK:
        return str(_RELIABILITY_STATE.get("started_at") or _utc_now_iso())


def _sanitize_metadata(metadata: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(metadata, Mapping):
        return None
    payload: Dict[str, Any] = {}
    for key, value in metadata.items():
        normalized_key = str(key or "").strip()
        if not normalized_key:
            continue
        if value is None:
            payload[normalized_key] = None
            continue
        if isinstance(value, (bool, int, float, str)):
            payload[normalized_key] = value
            continue
        if isinstance(value, Mapping):
            payload[normalized_key] = _sanitize_metadata(value)
            continue
        if isinstance(value, (list, tuple)):
            items = []
            for entry in value:
                if isinstance(entry, (bool, int, float, str)) or entry is None:
                    items.append(entry)
                elif isinstance(entry, Mapping):
                    items.append(_sanitize_metadata(entry))
                else:
                    items.append(str(entry))
            payload[normalized_key] = items
            continue
        payload[normalized_key] = str(value)
    return payload


def _append_latency_sample(metric: Dict[str, Any], duration_ms: float, *, recorded_at: Optional[str], metadata: Optional[Mapping[str, Any]]) -> None:
    sample_ms = max(0.0, float(duration_ms or 0.0))
    metric["samples"].append(sample_ms)
    metric["count"] = int(metric.get("count") or 0) + 1
    metric["sum_ms"] = float(metric.get("sum_ms") or 0.0) + sample_ms
    minimum = metric.get("min_ms")
    maximum = metric.get("max_ms")
    metric["min_ms"] = sample_ms if minimum is None else min(float(minimum), sample_ms)
    metric["max_ms"] = sample_ms if maximum is None else max(float(maximum), sample_ms)
    metric["last_ms"] = sample_ms
    metric["last_recorded_at"] = str(recorded_at or _utc_now_iso())
    metric["last_metadata"] = _sanitize_metadata(metadata)


def _percentile(values: Sequence[float], percentile: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(float(item) for item in values)
    index = max(0, min(len(ordered) - 1, ceil((percentile / 100.0) * len(ordered)) - 1))
    return round(ordered[index], 2)


def _latency_metric_snapshot(name: str, metric: Mapping[str, Any]) -> Dict[str, Any]:
    values = list(metric.get("samples") or [])
    count = int(metric.get("count") or 0)
    average = round(float(metric.get("sum_ms") or 0.0) / count, 2) if count > 0 else None
    target = _LATENCY_TARGETS_MS.get(name) or {}
    p95_ms = _percentile(values, 95.0)
    meets_target = None
    target_p95_ms = target.get("target_p95_ms")
    if p95_ms is not None and isinstance(target_p95_ms, (int, float)):
        meets_target = p95_ms <= float(target_p95_ms)
    return {
        "label": str(target.get("label") or name),
        "description": str(target.get("description") or "").strip() or None,
        "measurement_scope": "live_since_process_start",
        "data_status": "measured" if count > 0 else "insufficient_live_samples",
        "sample_count": count,
        "sample_window_size": len(values),
        "avg_ms": average,
        "p50_ms": _percentile(values, 50.0),
        "p95_ms": p95_ms,
        "min_ms": round(float(metric.get("min_ms")), 2) if metric.get("min_ms") is not None else None,
        "max_ms": round(float(metric.get("max_ms")), 2) if metric.get("max_ms") is not None else None,
        "last_ms": round(float(metric.get("last_ms")), 2) if metric.get("last_ms") is not None else None,
        "last_recorded_at": metric.get("last_recorded_at"),
        "last_metadata": metric.get("last_metadata"),
        "target_p95_ms": float(target_p95_ms) if isinstance(target_p95_ms, (int, float)) else None,
        "meets_target": meets_target,
    }


def record_reliability_latency_sample(
    metric_name: str,
    duration_ms: float,
    *,
    recorded_at: Optional[str] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> None:
    if metric_name not in _LATENCY_TARGETS_MS:
        return
    with _RELIABILITY_LOCK:
        metric = _RELIABILITY_STATE["latency_metrics"][metric_name]
        _append_latency_sample(metric, duration_ms, recorded_at=recorded_at, metadata=metadata)


def record_control_plane_request(
    *,
    method: str,
    path: str,
    status_code: int,
    duration_ms: float,
    recorded_at: Optional[str] = None,
    error_type: Optional[str] = None,
) -> None:
    timestamp = str(recorded_at or _utc_now_iso())
    normalized_status = int(status_code or 0)
    with _RELIABILITY_LOCK:
        control_plane = _RELIABILITY_STATE["control_plane_api"]
        control_plane["request_count"] = int(control_plane.get("request_count") or 0) + 1
        if 0 < normalized_status < 500:
            control_plane["available_count"] = int(control_plane.get("available_count") or 0) + 1
        else:
            control_plane["error_count"] = int(control_plane.get("error_count") or 0) + 1
            control_plane["last_error"] = {
                "recorded_at": timestamp,
                "status_code": normalized_status or 500,
                "method": str(method or "").upper() or "UNKNOWN",
                "path": str(path or "").strip() or "/",
                "error_type": str(error_type or "").strip() or None,
            }
        control_plane["last_seen_at"] = timestamp
        control_plane["last_request"] = {
            "recorded_at": timestamp,
            "method": str(method or "").upper() or "UNKNOWN",
            "path": str(path or "").strip() or "/",
            "status_code": normalized_status or 500,
        }
        _append_latency_sample(
            control_plane["latency_ms"],
            duration_ms,
            recorded_at=timestamp,
            metadata={
                "method": str(method or "").upper() or "UNKNOWN",
                "path": str(path or "").strip() or "/",
                "status_code": normalized_status or 500,
                "error_type": str(error_type or "").strip() or None,
            },
        )


def mark_machine_revocation_requested(
    machine_id: str,
    *,
    started_monotonic: Optional[float] = None,
    recorded_at: Optional[str] = None,
) -> None:
    token = str(machine_id or "").strip()
    if not token:
        return
    with _RELIABILITY_LOCK:
        _RELIABILITY_STATE["pending_machine_revocations"][token] = {
            "started_monotonic": float(started_monotonic) if started_monotonic is not None else None,
            "recorded_at": str(recorded_at or _utc_now_iso()),
        }


def observe_machine_revocation_propagated(
    machine_id: str,
    *,
    observed_monotonic: Optional[float] = None,
    observed_at: Optional[str] = None,
) -> Optional[float]:
    token = str(machine_id or "").strip()
    if not token:
        return None
    with _RELIABILITY_LOCK:
        pending = _RELIABILITY_STATE["pending_machine_revocations"].pop(token, None)
    if not isinstance(pending, dict):
        return None
    started_monotonic = pending.get("started_monotonic")
    if not isinstance(started_monotonic, (int, float)) or observed_monotonic is None:
        return None
    duration_ms = max(0.0, (float(observed_monotonic) - float(started_monotonic)) * 1000.0)
    record_reliability_latency_sample(
        "machine_revocation_propagation_latency_ms",
        duration_ms,
        recorded_at=observed_at,
        metadata={
            "machine_id": token,
            "requested_at": pending.get("recorded_at"),
            "observed_at": str(observed_at or _utc_now_iso()),
        },
    )
    return duration_ms


def build_failed_run_replay_completeness(
    failed_runs: Optional[Iterable[Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    relevant_runs: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for item in failed_runs or []:
        if not isinstance(item, Mapping):
            continue
        status = str(item.get("status") or "").strip().lower()
        if status not in {"failed", "timeout"}:
            continue
        run_id = str(item.get("run_id") or "").strip()
        if not run_id or run_id in seen:
            continue
        seen.add(run_id)
        relevant_runs.append(item)
    complete_ids: list[str] = []
    incomplete_ids: list[str] = []
    for item in relevant_runs:
        run_id = str(item.get("run_id") or "").strip()
        replay_request = item.get("replay_request")
        run_detail_contract = item.get("run_detail_contract")
        events = item.get("events")
        complete = (
            bool(run_id)
            and isinstance(replay_request, Mapping)
            and bool(replay_request)
            and isinstance(run_detail_contract, Mapping)
            and bool(run_detail_contract)
            and isinstance(events, list)
            and len(events) > 0
        )
        if complete:
            complete_ids.append(run_id)
        else:
            incomplete_ids.append(run_id)
    evaluated = len(relevant_runs)
    completeness_ratio = (len(complete_ids) / evaluated) if evaluated > 0 else None
    return {
        "label": "Failed run replay completeness",
        "description": "Evaluates whether failed/timeout runs currently retain replay_request, run_detail_contract, and event history needed for replay.",
        "measurement_scope": "current_live_and_archive_scan",
        "data_status": "measured" if evaluated > 0 else "insufficient_failed_runs",
        "evaluated_run_count": evaluated,
        "complete_run_count": len(complete_ids),
        "incomplete_run_count": len(incomplete_ids),
        "completeness_ratio": round(completeness_ratio, 4) if completeness_ratio is not None else None,
        "completeness_percent": round((completeness_ratio or 0.0) * 100.0, 2) if completeness_ratio is not None else None,
        "target_completeness_percent": 100.0,
        "meets_target": len(incomplete_ids) == 0 if evaluated > 0 else None,
        "last_evaluated_at": _utc_now_iso(),
        "incomplete_run_ids": incomplete_ids[:10],
    }


def get_reliability_snapshot(
    *,
    failed_run_snapshots: Optional[Iterable[Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    with _RELIABILITY_LOCK:
        started_at = str(_RELIABILITY_STATE.get("started_at") or _utc_now_iso())
        control_plane = dict(_RELIABILITY_STATE["control_plane_api"])
        control_plane_latency = _latency_metric_snapshot("control_plane_api_latency_ms", control_plane["latency_ms"])
        request_count = int(control_plane.get("request_count") or 0)
        available_count = int(control_plane.get("available_count") or 0)
        availability_ratio = (available_count / request_count) if request_count > 0 else None
        latency_metrics = {
            name: _latency_metric_snapshot(name, metric)
            for name, metric in _RELIABILITY_STATE["latency_metrics"].items()
        }
        pending_revocations = len(_RELIABILITY_STATE["pending_machine_revocations"])
    control_plane_snapshot = {
        "label": "Control plane API health",
        "measurement_scope": "live_since_process_start",
        "data_status": "measured" if request_count > 0 else "insufficient_live_samples",
        "request_count": request_count,
        "available_count": available_count,
        "error_count": int(control_plane.get("error_count") or 0),
        "availability_ratio": round(availability_ratio, 4) if availability_ratio is not None else None,
        "availability_percent": round((availability_ratio or 0.0) * 100.0, 3) if availability_ratio is not None else None,
        "target_availability_percent": _CONTROL_PLANE_AVAILABILITY_TARGET,
        "meets_target": ((availability_ratio or 0.0) * 100.0) >= _CONTROL_PLANE_AVAILABILITY_TARGET if availability_ratio is not None else None,
        "last_seen_at": control_plane.get("last_seen_at"),
        "last_request": control_plane.get("last_request"),
        "last_error": control_plane.get("last_error"),
        "latency_ms": control_plane_latency,
    }
    return {
        "generated_at": _utc_now_iso(),
        "measurement_window_started_at": started_at,
        "notes": [
            "Latency and API health metrics are measured live from this runtime process start and are not backfilled.",
            "Failed run replay completeness is computed from the current live/archive snapshot set at request time.",
        ],
        "control_plane_api": control_plane_snapshot,
        "run_enqueue_ack": latency_metrics["run_enqueue_ack_latency_ms"],
        "approval_propagation": latency_metrics["approval_propagation_latency_ms"],
        "artifact_availability": latency_metrics["artifact_availability_latency_ms"],
        "machine_revocation_propagation": {
            **latency_metrics["machine_revocation_propagation_latency_ms"],
            "pending_revocation_count": pending_revocations,
        },
        "failed_run_replay_completeness": build_failed_run_replay_completeness(failed_run_snapshots),
    }
