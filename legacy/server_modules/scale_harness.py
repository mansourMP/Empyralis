from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
import heapq
import json
import random
import threading
import time
import uuid
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence
from unittest.mock import patch

from server_modules import agent_registry_repository, control_plane_repository, machine_lease_service


_BASE_SIM_TIME = datetime(2026, 4, 10, 0, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class ScaleSloTarget:
    name: str
    metric_id: str
    comparator: str
    target: float
    unit: str
    description: str


@dataclass(frozen=True, slots=True)
class ControlPlaneScaleConfig:
    install_count: int = 10_000
    list_iterations: int = 5
    batch_size: int = 1_000
    tenant_prefix: str = "scale-harness-tenant"
    workspace_prefix: str = "scale-harness-workspace"
    cleanup_after_run: bool = True


@dataclass(frozen=True, slots=True)
class FleetScaleConfig:
    seed: int = 20260410
    total_runs: int = 1_500
    active_threads: int = 1_200
    worker_count: int = 180
    workspace_count: int = 14
    max_queue_retry_attempts: int = 1
    queue_retry_backoff_seconds: tuple[int, ...] = (5, 15)
    local_worker_lost_timeout_seconds: int = 45
    default_lease_seconds: int = 30
    checkpoint_recovery_max_auto_retries: int = 1
    checkpoint_recovery_backoff_seconds: tuple[int, ...] = (0,)
    recovery_delay_seconds: int = 8


DEFAULT_SCALE_SLO_TARGETS: tuple[ScaleSloTarget, ...] = (
    ScaleSloTarget(
        name="control_plane_bulk_insert_10k",
        metric_id="control_plane.insert_seconds",
        comparator="lte",
        target=20.0,
        unit="seconds",
        description="Bulk specialist install seeding for a fresh workspace should stay under 20 seconds.",
    ),
    ScaleSloTarget(
        name="control_plane_list_p95",
        metric_id="control_plane.list_latency_p95_ms",
        comparator="lte",
        target=1500.0,
        unit="ms",
        description="Listing 10k installed specialists should keep p95 under 1.5 seconds.",
    ),
    ScaleSloTarget(
        name="fleet_peak_concurrency",
        metric_id="fleet.peak_concurrency",
        comparator="gte",
        target=100.0,
        unit="workers",
        description="The runtime simulation must reach at least 100 concurrent executions.",
    ),
    ScaleSloTarget(
        name="fleet_queue_delay_p95",
        metric_id="fleet.queue_delay_p95_seconds",
        comparator="lte",
        target=60.0,
        unit="seconds",
        description="Queue delay p95 should remain below 60 simulated seconds.",
    ),
    ScaleSloTarget(
        name="fleet_completion_latency_p95",
        metric_id="fleet.completion_latency_p95_seconds",
        comparator="lte",
        target=180.0,
        unit="seconds",
        description="Successful end-to-end completion latency p95 should remain below 180 simulated seconds.",
    ),
    ScaleSloTarget(
        name="fleet_saturation_ratio",
        metric_id="fleet.saturation_time_ratio",
        comparator="lte",
        target=0.35,
        unit="ratio",
        description="The fleet should not spend more than 35% of simulated time in saturation.",
    ),
    ScaleSloTarget(
        name="fleet_retry_rate",
        metric_id="fleet.retry_rate",
        comparator="lte",
        target=0.08,
        unit="ratio",
        description="Requeue retry rate should stay under 8%.",
    ),
    ScaleSloTarget(
        name="fleet_dead_letter_rate",
        metric_id="fleet.dead_letter_rate",
        comparator="lte",
        target=0.02,
        unit="ratio",
        description="Dead-letter rate should stay under 2%.",
    ),
    ScaleSloTarget(
        name="fleet_error_rate",
        metric_id="fleet.error_rate",
        comparator="lte",
        target=0.03,
        unit="ratio",
        description="Final failed run rate should stay under 3%.",
    ),
    ScaleSloTarget(
        name="fleet_throughput",
        metric_id="fleet.throughput_runs_per_second",
        comparator="gte",
        target=1.5,
        unit="runs/s",
        description="Simulation throughput should remain above 1.5 completed runs per simulated second.",
    ),
)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _iso_from_sim_seconds(seconds_since_base: float) -> str:
    return (_BASE_SIM_TIME + timedelta(seconds=float(seconds_since_base))).isoformat().replace("+00:00", "Z")


def _parse_utc_timestamp(value: Any) -> Optional[datetime]:
    token = str(value or "").strip()
    if not token:
        return None
    try:
        return datetime.fromisoformat(token.replace("Z", "+00:00"))
    except Exception:
        return None


def _percentile(values: Sequence[float], percentile: float) -> Optional[float]:
    clean = sorted(float(value) for value in values if value is not None)
    if not clean:
        return None
    if percentile <= 0:
        return clean[0]
    if percentile >= 100:
        return clean[-1]
    position = (len(clean) - 1) * (percentile / 100.0)
    lower = int(position)
    upper = min(lower + 1, len(clean) - 1)
    weight = position - lower
    return round(clean[lower] + (clean[upper] - clean[lower]) * weight, 3)


def _mean(values: Sequence[float]) -> Optional[float]:
    clean = [float(value) for value in values if value is not None]
    if not clean:
        return None
    return round(sum(clean) / len(clean), 3)


def _round_float(value: Optional[float], digits: int = 3) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), digits)


def _chunked(items: Sequence[Any], size: int) -> Iterator[Sequence[Any]]:
    safe_size = max(1, int(size or 0))
    for start in range(0, len(items), safe_size):
        yield items[start : start + safe_size]


def _command_rowcount(command_tag: str) -> int:
    token = str(command_tag or "").strip()
    if not token:
        return 0
    try:
        return int(token.split()[-1])
    except Exception:
        return 0


def _json_token(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, separators=(",", ":"), default=str)


def _worker_online(record: Mapping[str, Any]) -> bool:
    status = str(record.get("status") or "").strip().lower()
    control_state = str(record.get("control_state") or "active").strip().lower()
    return control_state == "active" and status != "offline"


def _normalize_capability_ids(items: Any) -> List[str]:
    normalized: List[str] = []
    seen: set[str] = set()
    for item in items or []:
        token = str(item or "").strip().lower()
        if token and token not in seen:
            seen.add(token)
            normalized.append(token)
    return normalized


def _required_capabilities_for_run(run: Dict[str, Any]) -> List[str]:
    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    precheck = metadata.get("tool_policy_precheck") if isinstance(metadata.get("tool_policy_precheck"), dict) else {}
    return _normalize_capability_ids(precheck.get("capability_ids"))


def _set_run_status(run_id: str, status: str, *, runs_by_id: Mapping[str, Any]) -> None:
    run = runs_by_id.get(run_id)
    if not isinstance(run, dict):
        return
    run["status"] = str(status or "").strip() or run.get("status")


def _pressure_snapshot(*, queued_count: int, claimed_count: int, online_workers: int, busy_workers: int) -> Dict[str, Any]:
    idle_workers = max(0, online_workers - busy_workers)
    backlog_per_online_worker = round(queued_count / max(1, online_workers), 2) if online_workers else None
    if queued_count <= 0:
        state = "healthy"
    elif online_workers <= 0:
        state = "saturated"
    elif backlog_per_online_worker is not None and backlog_per_online_worker > 4:
        state = "saturated"
    elif backlog_per_online_worker is not None and backlog_per_online_worker > 1.5:
        state = "strained"
    else:
        state = "healthy"
    return {
        "queued_count": queued_count,
        "claimed_count": claimed_count,
        "online_workers": online_workers,
        "busy_workers": busy_workers,
        "idle_workers": idle_workers,
        "backlog_per_online_worker": backlog_per_online_worker,
        "state": state,
    }


@contextmanager
def _mute_machine_lease_repository_writes() -> Iterator[None]:
    def _drop_repository_call(awaitable: Any, operation: str) -> None:
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        return None

    with patch.object(
        machine_lease_service.run_state_repository,
        "dispatch_repository_call",
        side_effect=_drop_repository_call,
    ):
        yield


async def run_control_plane_registry_benchmark(
    *,
    config: ControlPlaneScaleConfig | None = None,
) -> Dict[str, Any]:
    config = config or ControlPlaneScaleConfig()
    run_token = uuid.uuid4().hex[:12]
    tenant_id = f"{config.tenant_prefix}-{run_token}"
    workspace_id = f"{config.workspace_prefix}-{run_token}"
    user_id = f"scale-user-{run_token}"
    email = f"scale-harness-{run_token}@empyralis.local"

    await control_plane_repository.ensure_workspace_membership(
        user_id=user_id,
        email=email,
        display_name="Scale Harness",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        role="owner",
    )
    await agent_registry_repository.ensure_workspace_agent_registry_seeded(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        created_by_user_id=user_id,
    )
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        raise RuntimeError("Control-plane pool unavailable for scale harness.")

    async with pool.acquire() as connection:
        specialist_rows = await connection.fetch(
            """
            SELECT
                ad.id AS definition_id,
                adv.id AS version_id,
                ad.slug AS definition_slug,
                ad.name AS definition_name
            FROM agent_definitions ad
            INNER JOIN agent_definition_versions adv
                ON adv.id = COALESCE(ad.published_version_id, ad.current_version_id)
            WHERE ad.tenant_id = $1
              AND ad.workspace_id = $2
              AND COALESCE(ad.agent_kind, 'specialist') <> 'master'
            ORDER BY ad.slug ASC
            """,
            tenant_id,
            workspace_id,
        )
        runtime_row = await connection.fetchrow(
            """
            SELECT id
            FROM runtime_profiles
            WHERE tenant_id = $1
              AND workspace_id = $2
              AND slug = 'empyralis-cloud'
            LIMIT 1
            """,
            tenant_id,
            workspace_id,
        )
    if not specialist_rows or runtime_row is None:
        raise RuntimeError("Scale harness could not resolve seeded specialist definitions or runtime profile.")

    runtime_profile_id = str(runtime_row.get("id") or "").strip()
    install_rows: List[tuple[Any, ...]] = []
    created_at = datetime.now(UTC)
    for index in range(config.install_count):
        definition = specialist_rows[index % len(specialist_rows)]
        install_rows.append(
            (
                f"ainstall_scale_{run_token}_{index:05d}",
                tenant_id,
                workspace_id,
                str(definition.get("definition_id") or "").strip(),
                str(definition.get("version_id") or "").strip(),
                user_id,
                user_id,
                f"Scale Specialist {index:05d}",
                runtime_profile_id,
                _json_token({"scale_harness_enabled": True}),
                _json_token([]),
                _json_token({}),
                _json_token({}),
                _json_token({"trust_mode": "guarded"}),
                _json_token(
                    {
                        "scale_harness": True,
                        "scale_harness_run_id": run_token,
                        "definition_slug": str(definition.get("definition_slug") or "").strip(),
                    }
                ),
                created_at,
            )
        )

    insert_query = """
        INSERT INTO workspace_agent_installs (
            id, tenant_id, workspace_id, agent_definition_id, agent_definition_version_id, installed_by_user_id,
            install_scope, owner_user_id, thread_id, label, status, enabled, runtime_profile_id, compiled_workflow_version_id,
            root_folder_uri, tool_toggles, folder_grants, connector_bindings, memory_scope_overrides,
            policy_context_overrides, metadata, created_at, updated_at
        ) VALUES (
            $1, $2, $3, $4, $5, $6,
            'workspace', $7, NULL, $8, 'active', TRUE, $9, NULL,
            NULL, $10::jsonb, $11::jsonb, $12::jsonb, $13::jsonb,
            $14::jsonb, $15::jsonb, $16::timestamptz, $16::timestamptz
        )
    """

    insert_started = time.perf_counter()
    async with pool.acquire() as connection:
        async with connection.transaction():
            for batch in _chunked(install_rows, config.batch_size):
                await connection.executemany(insert_query, batch)
    insert_seconds = time.perf_counter() - insert_started

    list_latencies_ms: List[float] = []
    listed_count = 0
    for _ in range(max(1, config.list_iterations)):
        started = time.perf_counter()
        installs = await agent_registry_repository.list_workspace_agent_installs(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            include_master=False,
        )
        list_latencies_ms.append((time.perf_counter() - started) * 1000.0)
        listed_count = len(installs)

    cleanup_deleted = 0
    cleanup_seconds = 0.0
    if config.cleanup_after_run:
        cleanup_started = time.perf_counter()
        command_tag = await pool.execute(
            """
            DELETE FROM workspace_agent_installs
            WHERE tenant_id = $1
              AND workspace_id = $2
              AND metadata->>'scale_harness_run_id' = $3
            """,
            tenant_id,
            workspace_id,
            run_token,
        )
        cleanup_seconds = time.perf_counter() - cleanup_started
        cleanup_deleted = _command_rowcount(command_tag)

    return {
        "scenario": "control_plane_registry_10k",
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "install_count_requested": config.install_count,
        "install_count_listed": listed_count,
        "insert_seconds": _round_float(insert_seconds),
        "list_iterations": max(1, config.list_iterations),
        "list_latency_ms_samples": [_round_float(value) for value in list_latencies_ms],
        "list_latency_p50_ms": _percentile(list_latencies_ms, 50),
        "list_latency_p95_ms": _percentile(list_latencies_ms, 95),
        "list_latency_p99_ms": _percentile(list_latencies_ms, 99),
        "supports_required_volume": listed_count >= config.install_count,
        "cleanup_deleted_installs": cleanup_deleted,
        "cleanup_seconds": _round_float(cleanup_seconds),
    }


def _build_worker_registry(config: FleetScaleConfig, *, rng: random.Random) -> Dict[str, Dict[str, Any]]:
    worker_registry: Dict[str, Dict[str, Any]] = {}
    worker_profiles = [
        ("hosted_secure", ["llm.text", "inventory.read", "filesystem.export"], 48),
        ("local_companion", ["browser.automation", "filesystem.export"], 42),
        ("hosted_secure", ["connector.message.send", "llm.text"], 32),
        ("hosted_secure", ["calendar.write", "llm.text"], 28),
        ("local_companion", ["inventory.read", "llm.text"], 20),
        ("hosted_secure", ["llm.text", "connector.message.send", "browser.automation"], 10),
    ]
    assigned = 0
    for group_index, (runtime_type, capabilities, count) in enumerate(worker_profiles):
        for worker_offset in range(count):
            if assigned >= config.worker_count:
                break
            worker_id = f"worker-{group_index}-{worker_offset:03d}"
            worker_registry[worker_id] = {
                "worker_id": worker_id,
                "runtime_id": worker_id,
                "machine_id": worker_id,
                "tenant_id": "scale-tenant",
                "workspace_id": f"ws-{assigned % max(1, config.workspace_count):02d}",
                "runtime_type": runtime_type,
                "status": "idle",
                "control_state": "active",
                "current_run_id": None,
                "capabilities": list(capabilities),
                "execution_targets": [runtime_type],
                "last_seen_at": _iso_from_sim_seconds(0),
                "lease_seconds": config.default_lease_seconds,
                "prewarm_state": "warm" if runtime_type == "hosted_secure" and assigned % 3 == 0 else None,
                "warm_pool": "hosted-primary" if runtime_type == "hosted_secure" else None,
                "note": "ready",
                "capability_digest": uuid.uuid5(uuid.NAMESPACE_DNS, ":".join(sorted(capabilities))).hex[:12],
            }
            assigned += 1
    while assigned < config.worker_count:
        worker_id = f"worker-extra-{assigned:03d}"
        capabilities = rng.choice(
            [
                ["llm.text", "inventory.read"],
                ["browser.automation", "filesystem.export"],
                ["connector.message.send", "calendar.write"],
            ]
        )
        worker_registry[worker_id] = {
            "worker_id": worker_id,
            "runtime_id": worker_id,
            "machine_id": worker_id,
            "tenant_id": "scale-tenant",
            "workspace_id": f"ws-{assigned % max(1, config.workspace_count):02d}",
            "runtime_type": "hosted_secure",
            "status": "idle",
            "control_state": "active",
            "current_run_id": None,
            "capabilities": list(capabilities),
            "execution_targets": ["hosted_secure"],
            "last_seen_at": _iso_from_sim_seconds(0),
            "lease_seconds": config.default_lease_seconds,
            "prewarm_state": "warm",
            "warm_pool": "hosted-primary",
            "note": "ready",
            "capability_digest": uuid.uuid5(uuid.NAMESPACE_DNS, ":".join(sorted(capabilities))).hex[:12],
        }
        assigned += 1
    return worker_registry


def _generate_run_plan(config: FleetScaleConfig, *, rng: random.Random) -> List[Dict[str, Any]]:
    workloads: List[Dict[str, Any]] = [
        {
            "name": "sage_chat_reply",
            "specialist_key": "sage-reply",
            "capabilities": ["llm.text"],
            "duration_range": (8, 22),
            "weight": 34,
        },
        {
            "name": "inventory_lookup",
            "specialist_key": "inventory-specialist",
            "capabilities": ["inventory.read"],
            "duration_range": (6, 16),
            "weight": 22,
        },
        {
            "name": "browser_action",
            "specialist_key": "browser-specialist",
            "capabilities": ["browser.automation"],
            "duration_range": (32, 70),
            "weight": 16,
        },
        {
            "name": "connector_followup",
            "specialist_key": "message-specialist",
            "capabilities": ["connector.message.send"],
            "duration_range": (12, 32),
            "weight": 15,
        },
        {
            "name": "calendar_planning",
            "specialist_key": "calendar-specialist",
            "capabilities": ["calendar.write"],
            "duration_range": (10, 26),
            "weight": 8,
        },
        {
            "name": "file_export",
            "specialist_key": "file-specialist",
            "capabilities": ["filesystem.export"],
            "duration_range": (14, 40),
            "weight": 5,
        },
    ]
    weighted = [item for item in workloads for _ in range(item["weight"])]
    workspace_weights = [8, 7, 7, 6, 6, 5, 5, 5, 4, 4, 3, 3, 2, 2]
    while len(workspace_weights) < config.workspace_count:
        workspace_weights.append(2)
    workspace_ids = [f"ws-{index:02d}" for index in range(config.workspace_count)]
    burst_windows = [(0, 30), (35, 70), (72, 115), (118, 170), (175, 240)]
    burst_weights = [28, 26, 22, 16, 8]

    plans: List[Dict[str, Any]] = []
    for index in range(config.total_runs):
        workload = dict(rng.choice(weighted))
        burst_start, burst_end = rng.choices(burst_windows, weights=burst_weights, k=1)[0]
        arrival_seconds = round(rng.uniform(burst_start, burst_end), 3)
        workspace_id = rng.choices(workspace_ids, weights=workspace_weights[: config.workspace_count], k=1)[0]
        planned_loss_mode = "none"
        random_value = rng.random()
        if random_value < 0.02:
            planned_loss_mode = "dead_letter"
        elif random_value < 0.07:
            planned_loss_mode = "recover_once"
        duration_low, duration_high = workload["duration_range"]
        plans.append(
            {
                "run_id": f"scale-run-{index:05d}",
                "thread_id": f"thread-{index % max(1, config.active_threads):05d}",
                "workspace_id": workspace_id,
                "tenant_id": "scale-tenant",
                "specialist_key": workload["specialist_key"],
                "workload_name": workload["name"],
                "capabilities": list(workload["capabilities"]),
                "arrival_seconds": arrival_seconds,
                "service_seconds": float(rng.randint(duration_low, duration_high)),
                "planned_loss_mode": planned_loss_mode,
                "planned_loss_after_seconds": float(rng.randint(8, 16)),
            }
        )
    plans.sort(key=lambda item: (item["arrival_seconds"], item["run_id"]))
    return plans


def _build_run_payload(plan: Mapping[str, Any], *, config: FleetScaleConfig) -> Dict[str, Any]:
    metadata = {
        "tenant_id": str(plan["tenant_id"]),
        "active_agent_install_id": str(plan["specialist_key"]),
        "execution_target_selected": "local_companion",
        "tool_policy_precheck": {"capability_ids": list(plan["capabilities"])},
        "local_queue_max_retries": config.max_queue_retry_attempts,
    }
    return {
        "run_id": str(plan["run_id"]),
        "thread_id": str(plan["thread_id"]),
        "trace_id": f"trace-{plan['run_id']}",
        "status": "queued_local",
        "result": None,
        "result_data": None,
        "context": {
            "workspace_id": str(plan["workspace_id"]),
            "tenant_id": str(plan["tenant_id"]),
            "metadata": metadata,
        },
        "logs": None,
    }


def _run_scope_key(run: Mapping[str, Any]) -> tuple[str, str]:
    context = run.get("context") if isinstance(run.get("context"), Mapping) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), Mapping) else {}
    workspace_id = str(run.get("workspace_id") or context.get("workspace_id") or "default").strip() or "default"
    specialist_key = str(
        metadata.get("active_agent_install_id") or metadata.get("agent_install_id") or metadata.get("agent_role") or "general"
    ).strip() or "general"
    return workspace_id, specialist_key


def run_fleet_runtime_simulation(*, config: FleetScaleConfig | None = None) -> Dict[str, Any]:
    config = config or FleetScaleConfig()
    rng = random.Random(config.seed)
    worker_registry = _build_worker_registry(config, rng=rng)
    pending_run_ids: List[str] = []
    claimed_runs: Dict[str, Dict[str, Any]] = {}
    runs_by_id: Dict[str, Dict[str, Any]] = {}
    local_queue_lock = threading.Lock()
    dead_letters: List[Dict[str, Any]] = []
    run_stats: Dict[str, Dict[str, Any]] = {}
    worker_busy_seconds: Dict[str, float] = defaultdict(float)
    workspace_peak_outstanding: Dict[str, int] = defaultdict(int)
    specialist_peak_outstanding: Dict[str, int] = defaultdict(int)
    burst_counter: Counter[int] = Counter()
    current_sim_seconds = 0.0

    def now_iso() -> str:
        return _iso_from_sim_seconds(current_sim_seconds)

    def now_dt() -> datetime:
        return _BASE_SIM_TIME + timedelta(seconds=current_sim_seconds)

    def persist_local_runtime_state() -> None:
        return None

    def emit_log(*_args: Any, **_kwargs: Any) -> None:
        return None

    def mark_worker_seen(worker_id: str, run_id: Optional[str], status: str, note: Optional[str] = None) -> None:
        previous = worker_registry.get(worker_id, {})
        worker_registry[worker_id] = machine_lease_service.build_machine_presence_record(
            previous_record=previous,
            machine_id=worker_id,
            current_run_id=run_id,
            status_hint=status,
            lease_seconds=int(previous.get("lease_seconds") or config.default_lease_seconds),
            now_iso=now_iso(),
            note=note,
        )
        worker_registry[worker_id]["control_state"] = previous.get("control_state", "active")
        worker_registry[worker_id]["capabilities"] = list(previous.get("capabilities") or [])
        worker_registry[worker_id]["runtime_type"] = previous.get("runtime_type", "hosted_secure")
        worker_registry[worker_id]["execution_targets"] = list(previous.get("execution_targets") or ["hosted_secure"])
        worker_registry[worker_id]["prewarm_state"] = previous.get("prewarm_state")
        worker_registry[worker_id]["warm_pool"] = previous.get("warm_pool")
        if status == "offline":
            worker_registry[worker_id]["current_run_id"] = None

    def append_dead_letter(**payload: Any) -> None:
        dead_letters.append(dict(payload))

    def schedule_restored_run_resume(_run_id: str, _run: Dict[str, Any]) -> bool:
        return False

    def set_run_status(run_id: str, status: str) -> None:
        _set_run_status(run_id, status, runs_by_id=runs_by_id)

    plans = _generate_run_plan(config, rng=rng)
    events: List[tuple[float, int, str, Dict[str, Any]]] = []
    event_sequence = 0

    def schedule_event(at_seconds: float, kind: str, payload: Dict[str, Any]) -> None:
        nonlocal event_sequence
        heapq.heappush(events, (round(float(at_seconds), 3), event_sequence, kind, dict(payload)))
        event_sequence += 1

    for plan in plans:
        schedule_event(plan["arrival_seconds"], "arrival", dict(plan))

    pressure_state_durations: Counter[str] = Counter()
    peak_concurrency = 0
    max_backlog_per_online_worker = 0.0
    last_pressure = _pressure_snapshot(
        queued_count=0,
        claimed_count=0,
        online_workers=len(worker_registry),
        busy_workers=0,
    )
    last_pressure_time = 0.0

    def snapshot_pressure(now_seconds: float) -> None:
        nonlocal last_pressure_time, last_pressure, peak_concurrency, max_backlog_per_online_worker
        elapsed = max(0.0, now_seconds - last_pressure_time)
        pressure_state_durations[last_pressure["state"]] += elapsed
        for worker_id, record in worker_registry.items():
            if _worker_online(record) and str(record.get("status") or "").strip().lower() == "busy":
                worker_busy_seconds[worker_id] += elapsed
        outstanding_workspace_counts: Counter[str] = Counter()
        outstanding_specialist_counts: Counter[str] = Counter()
        for run_id in list(pending_run_ids) + list(claimed_runs.keys()):
            run = runs_by_id.get(run_id)
            if not isinstance(run, dict):
                continue
            workspace_id, specialist_key = _run_scope_key(run)
            outstanding_workspace_counts[workspace_id] += 1
            outstanding_specialist_counts[specialist_key] += 1
        for workspace_id, count in outstanding_workspace_counts.items():
            workspace_peak_outstanding[workspace_id] = max(workspace_peak_outstanding[workspace_id], count)
        for specialist_key, count in outstanding_specialist_counts.items():
            specialist_peak_outstanding[specialist_key] = max(specialist_peak_outstanding[specialist_key], count)
        online_workers = len([item for item in worker_registry.values() if _worker_online(item)])
        busy_workers = len(
            [
                item
                for item in worker_registry.values()
                if _worker_online(item) and str(item.get("status") or "").strip().lower() == "busy"
            ]
        )
        last_pressure = _pressure_snapshot(
            queued_count=len(pending_run_ids),
            claimed_count=len(claimed_runs),
            online_workers=online_workers,
            busy_workers=busy_workers,
        )
        peak_concurrency = max(peak_concurrency, busy_workers)
        if last_pressure["backlog_per_online_worker"] is not None:
            max_backlog_per_online_worker = max(
                max_backlog_per_online_worker,
                float(last_pressure["backlog_per_online_worker"]),
            )
        last_pressure_time = now_seconds

    def activate_claims(now_seconds: float) -> None:
        nonlocal current_sim_seconds
        current_sim_seconds = now_seconds
        while True:
            claimed_any = False
            idle_workers = [
                worker_id
                for worker_id, record in worker_registry.items()
                if _worker_online(record) and str(record.get("status") or "").strip().lower() != "busy"
            ]
            if not idle_workers or not pending_run_ids:
                return
            for worker_id in idle_workers:
                claimed_run_id = machine_lease_service.claim_local_machine_lease(
                    worker_id,
                    required_capabilities=None,
                    local_queue_lock=local_queue_lock,
                    pending_run_ids=pending_run_ids,
                    claimed_runs=claimed_runs,
                    worker_registry=worker_registry,
                    runs_by_id=runs_by_id,
                    lease_seconds=config.default_lease_seconds,
                    cleanup_stale_local_claims_fn=lambda: None,
                    ordered_runtime_preferences_for_run_fn=lambda run: [],
                    best_online_preferred_runtime_fn=lambda runtime_ids: None,
                    required_capabilities_for_run_fn=_required_capabilities_for_run,
                    normalize_capability_ids_fn=_normalize_capability_ids,
                    persist_local_runtime_state_fn=persist_local_runtime_state,
                    mark_local_worker_seen_fn=mark_worker_seen,
                    now_iso_fn=now_iso,
                    parse_utc_ts_fn=_parse_utc_timestamp,
                    now_fn=now_dt,
                    lease_id_factory=lambda: uuid.uuid4().hex[:12],
                )
                if not claimed_run_id:
                    continue
                claimed_any = True
                run = runs_by_id[claimed_run_id]
                claim = claimed_runs[claimed_run_id]
                worker_state = worker_registry[worker_id]
                machine_lease_service.bind_machine_lease_to_run(
                    run,
                    worker_id=worker_id,
                    claim=claim,
                    worker_state=worker_state,
                    now_iso=now_iso(),
                    normalize_policy_mode_fn=lambda value: str(value or "local_default"),
                )
                run["status"] = "running_local"
                stats = run_stats[claimed_run_id]
                stats["attempts_started"] += 1
                stats.setdefault("first_started_at_seconds", now_seconds)
                plan = stats["plan"]
                loss_mode = str(plan["planned_loss_mode"])
                start_attempt = stats["attempts_started"]
                if loss_mode == "recover_once" and start_attempt == 1:
                    loss_time = now_seconds + float(plan["planned_loss_after_seconds"])
                    cleanup_time = loss_time + float(config.local_worker_lost_timeout_seconds) + 0.25
                    schedule_event(cleanup_time, "cleanup", {"run_id": claimed_run_id, "worker_id": worker_id, "loss_time": loss_time})
                    schedule_event(cleanup_time + float(config.recovery_delay_seconds), "worker_recover", {"worker_id": worker_id})
                elif loss_mode == "dead_letter" and start_attempt <= (config.max_queue_retry_attempts + 1):
                    loss_time = now_seconds + float(plan["planned_loss_after_seconds"])
                    cleanup_time = loss_time + float(config.local_worker_lost_timeout_seconds) + 0.25
                    schedule_event(cleanup_time, "cleanup", {"run_id": claimed_run_id, "worker_id": worker_id, "loss_time": loss_time})
                    schedule_event(cleanup_time + float(config.recovery_delay_seconds), "worker_recover", {"worker_id": worker_id})
                else:
                    schedule_event(now_seconds + float(plan["service_seconds"]), "complete", {"run_id": claimed_run_id, "worker_id": worker_id})
            if not claimed_any:
                return

    with _mute_machine_lease_repository_writes():
        while events:
            event_time, _seq, event_kind, payload = heapq.heappop(events)
            simultaneous_events = [(event_time, event_kind, payload)]
            while events and events[0][0] == event_time:
                same_time, _same_seq, same_kind, same_payload = heapq.heappop(events)
                simultaneous_events.append((same_time, same_kind, same_payload))
            snapshot_pressure(event_time)
            current_sim_seconds = event_time
            for _same_time, same_kind, same_payload in simultaneous_events:
                if same_kind == "arrival":
                    run = _build_run_payload(same_payload, config=config)
                    run_id = str(run["run_id"])
                    runs_by_id[run_id] = run
                    pending_run_ids.append(run_id)
                    burst_counter[int(event_time // 60)] += 1
                    run_stats[run_id] = {
                        "plan": dict(same_payload),
                        "arrival_seconds": float(same_payload["arrival_seconds"]),
                        "attempts_started": 0,
                    }
                elif same_kind == "complete":
                    run_id = str(same_payload["run_id"])
                    worker_id = str(same_payload["worker_id"])
                    claim = claimed_runs.get(run_id)
                    if not isinstance(claim, dict):
                        continue
                    if str(claim.get("worker_id") or "").strip() != worker_id:
                        continue
                    machine_lease_service.release_machine_lease_claim(
                        run_id,
                        worker_id=worker_id,
                        local_queue_lock=local_queue_lock,
                        claimed_runs=claimed_runs,
                        persist_local_runtime_state_fn=persist_local_runtime_state,
                        mark_local_worker_seen_fn=mark_worker_seen,
                        status_hint="idle",
                        note="completed_scale_run",
                    )
                    run = runs_by_id.get(run_id)
                    if isinstance(run, dict):
                        machine_lease_service.clear_active_machine_lease_binding(run)
                        run["status"] = "completed"
                    run_stats[run_id]["completed_at_seconds"] = event_time
                    run_stats[run_id]["terminal_state"] = "completed"
                elif same_kind == "cleanup":
                    run_id = str(same_payload["run_id"])
                    worker_id = str(same_payload["worker_id"])
                    claim = claimed_runs.get(run_id)
                    if isinstance(claim, dict):
                        claim["last_heartbeat_at"] = _iso_from_sim_seconds(float(same_payload["loss_time"]))
                    before_dead_letters = len(dead_letters)
                    machine_lease_service.cleanup_stale_machine_leases(
                        now=now_dt(),
                        local_queue_lock=local_queue_lock,
                        claimed_runs=claimed_runs,
                        worker_registry=worker_registry,
                        runs_by_id=runs_by_id,
                        parse_utc_ts_fn=_parse_utc_timestamp,
                        utc_now_iso_fn=now_iso,
                        persist_local_runtime_state_fn=persist_local_runtime_state,
                        emit_log_fn=emit_log,
                        set_run_status_fn=lambda rid, status: set_run_status(rid, status),
                        schedule_restored_run_resume_fn=schedule_restored_run_resume,
                        checkpoint_recovery_max_auto_retries=config.checkpoint_recovery_max_auto_retries,
                        checkpoint_recovery_backoff_seconds=list(config.checkpoint_recovery_backoff_seconds),
                        local_worker_lost_timeout_seconds=config.local_worker_lost_timeout_seconds,
                        default_lease_seconds=config.default_lease_seconds,
                        local_pending_run_ids=pending_run_ids,
                        max_queue_retry_attempts=config.max_queue_retry_attempts,
                        queue_retry_backoff_seconds=list(config.queue_retry_backoff_seconds),
                        append_dead_letter_fn=append_dead_letter,
                    )
                    run = runs_by_id.get(run_id)
                    if isinstance(run, dict):
                        machine_lease_service.clear_active_machine_lease_binding(run)
                        stats = run_stats[run_id]
                        if str(run.get("status") or "").strip().lower() == "queued_local":
                            stats["requeued_count"] = int(stats.get("requeued_count") or 0) + 1
                            next_retry_at = (
                                ((run.get("context") or {}).get("metadata") or {}).get("local_queue_next_retry_at")
                                if isinstance(run.get("context"), dict)
                                else None
                            )
                            due_at = _parse_utc_timestamp(next_retry_at)
                            if due_at is not None:
                                retry_seconds = max(0.0, (due_at - _BASE_SIM_TIME).total_seconds())
                                schedule_event(retry_seconds, "retry_due", {"run_id": run_id})
                        elif str(run.get("status") or "").strip().lower() == "failed":
                            stats["failed_at_seconds"] = event_time
                            stats["terminal_state"] = "failed"
                    if len(dead_letters) > before_dead_letters and worker_id in worker_registry:
                        worker_registry[worker_id]["status"] = "offline"
                elif same_kind == "worker_recover":
                    worker_id = str(same_payload["worker_id"])
                    previous = worker_registry.get(worker_id, {})
                    worker_registry[worker_id] = machine_lease_service.build_machine_presence_record(
                        previous_record=previous,
                        machine_id=worker_id,
                        current_run_id=None,
                        status_hint="idle",
                        lease_seconds=int(previous.get("lease_seconds") or config.default_lease_seconds),
                        now_iso=now_iso(),
                        note="recovered_after_loss",
                    )
                    worker_registry[worker_id]["control_state"] = "active"
                    worker_registry[worker_id]["capabilities"] = list(previous.get("capabilities") or [])
                    worker_registry[worker_id]["runtime_type"] = previous.get("runtime_type", "hosted_secure")
                    worker_registry[worker_id]["execution_targets"] = list(previous.get("execution_targets") or ["hosted_secure"])
                    worker_registry[worker_id]["prewarm_state"] = previous.get("prewarm_state")
                    worker_registry[worker_id]["warm_pool"] = previous.get("warm_pool")
                elif same_kind == "retry_due":
                    pass
            activate_claims(event_time)

        snapshot_pressure(last_pressure_time)

    queue_delays = [
        float(stats["first_started_at_seconds"]) - float(stats["arrival_seconds"])
        for stats in run_stats.values()
        if stats.get("first_started_at_seconds") is not None
    ]
    completion_latencies = [
        float(stats["completed_at_seconds"]) - float(stats["arrival_seconds"])
        for stats in run_stats.values()
        if stats.get("completed_at_seconds") is not None
    ]
    terminal_latencies = [
        float(stats.get("completed_at_seconds", stats.get("failed_at_seconds"))) - float(stats["arrival_seconds"])
        for stats in run_stats.values()
        if stats.get("completed_at_seconds") is not None or stats.get("failed_at_seconds") is not None
    ]
    completed_runs = len([stats for stats in run_stats.values() if stats.get("terminal_state") == "completed"])
    failed_runs = len([stats for stats in run_stats.values() if stats.get("terminal_state") == "failed"])
    retry_count = sum(int(stats.get("requeued_count") or 0) for stats in run_stats.values())
    total_sim_seconds = max(last_pressure_time, max((plan["arrival_seconds"] for plan in plans), default=0.0))
    worker_utilization_samples = [
        worker_busy_seconds.get(worker_id, 0.0) / max(total_sim_seconds or 1.0, 1.0)
        for worker_id in worker_registry
    ]
    hotspot_workspaces = [
        {"workspace_id": workspace_id, "peak_outstanding": count}
        for workspace_id, count in sorted(workspace_peak_outstanding.items(), key=lambda item: (-item[1], item[0]))[:5]
    ]
    hotspot_specialists = [
        {"specialist_key": specialist_key, "peak_outstanding": count}
        for specialist_key, count in sorted(specialist_peak_outstanding.items(), key=lambda item: (-item[1], item[0]))[:5]
    ]
    peak_burst_per_minute = max(burst_counter.values()) if burst_counter else 0
    state_duration_total = sum(pressure_state_durations.values()) or 1.0

    return {
        "scenario": "fleet_runtime_concurrency",
        "seed": config.seed,
        "runs_requested": config.total_runs,
        "active_threads": len({str(plan["thread_id"]) for plan in plans}),
        "worker_count": config.worker_count,
        "completed_runs": completed_runs,
        "failed_runs": failed_runs,
        "dead_letters": len(dead_letters),
        "retry_count": retry_count,
        "error_rate": _round_float(failed_runs / max(1, config.total_runs), 4),
        "dead_letter_rate": _round_float(len(dead_letters) / max(1, config.total_runs), 4),
        "retry_rate": _round_float(retry_count / max(1, config.total_runs), 4),
        "queue_delay_p50_seconds": _percentile(queue_delays, 50),
        "queue_delay_p95_seconds": _percentile(queue_delays, 95),
        "queue_delay_p99_seconds": _percentile(queue_delays, 99),
        "completion_latency_p50_seconds": _percentile(completion_latencies, 50),
        "completion_latency_p95_seconds": _percentile(completion_latencies, 95),
        "completion_latency_p99_seconds": _percentile(completion_latencies, 99),
        "terminal_latency_p95_seconds": _percentile(terminal_latencies, 95),
        "throughput_runs_per_second": _round_float(completed_runs / max(total_sim_seconds, 1.0), 4),
        "peak_concurrency": peak_concurrency,
        "peak_arrival_burst_per_minute": peak_burst_per_minute,
        "saturation_time_ratio": _round_float(pressure_state_durations["saturated"] / state_duration_total, 4),
        "strained_time_ratio": _round_float(pressure_state_durations["strained"] / state_duration_total, 4),
        "healthy_time_ratio": _round_float(pressure_state_durations["healthy"] / state_duration_total, 4),
        "max_backlog_per_online_worker": _round_float(max_backlog_per_online_worker, 3),
        "average_worker_utilization": _mean(worker_utilization_samples),
        "worker_utilization_p95": _percentile(worker_utilization_samples, 95),
        "hotspot_workspaces": hotspot_workspaces,
        "hotspot_specialists": hotspot_specialists,
        "pressure_state_durations_seconds": {
            state: _round_float(duration, 3) for state, duration in sorted(pressure_state_durations.items())
        },
    }


def _metric_from_report(report: Mapping[str, Any], metric_id: str) -> Optional[float]:
    cursor: Any = report
    for token in metric_id.split("."):
        if not isinstance(cursor, Mapping):
            return None
        cursor = cursor.get(token)
    if isinstance(cursor, bool):
        return 1.0 if cursor else 0.0
    if cursor is None:
        return None
    try:
        return float(cursor)
    except Exception:
        return None


def evaluate_scale_slos(
    report: Mapping[str, Any],
    *,
    targets: Sequence[ScaleSloTarget] = DEFAULT_SCALE_SLO_TARGETS,
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for target in targets:
        actual = _metric_from_report(report, target.metric_id)
        if actual is None:
            passed = False
        elif target.comparator == "lte":
            passed = actual <= target.target
        elif target.comparator == "gte":
            passed = actual >= target.target
        else:
            raise ValueError(f"Unsupported comparator: {target.comparator}")
        results.append(
            {
                "name": target.name,
                "metric_id": target.metric_id,
                "actual": _round_float(actual, 4) if actual is not None else None,
                "comparator": target.comparator,
                "target": target.target,
                "unit": target.unit,
                "passed": bool(passed),
                "description": target.description,
            }
        )
    return results


def build_scale_findings(report: Mapping[str, Any], slo_results: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    bottlenecks: List[str] = []
    recommendations: List[str] = []
    failed_names = {str(item.get("name")) for item in slo_results if not bool(item.get("passed"))}
    control_plane = report.get("control_plane") if isinstance(report.get("control_plane"), Mapping) else {}
    fleet = report.get("fleet") if isinstance(report.get("fleet"), Mapping) else {}

    if not bool(control_plane.get("supports_required_volume")):
        bottlenecks.append("Control-plane registry listing did not return the full 10k installed specialist set.")
        recommendations.append("Add a dedicated install-summary projection and explicit pagination/count endpoints before pushing beyond 10k installs per workspace.")
    if "control_plane_list_p95" in failed_names:
        bottlenecks.append("Listing installed specialists at 10k scale is above the current p95 target.")
        recommendations.append("Cache install summaries and trim manifest joins from high-volume list queries, especially for owner/mobile list surfaces.")
    if "fleet_peak_concurrency" in failed_names:
        bottlenecks.append("The runtime fleet did not reach the minimum concurrent execution target.")
        recommendations.append("Increase hosted prewarm pool size or widen capability coverage on warm workers so bursts do not stall below 100 concurrent runs.")
    if "fleet_saturation_ratio" in failed_names or float(fleet.get("max_backlog_per_online_worker") or 0) > 4:
        bottlenecks.append("Worker demand spends too much time saturated under burst traffic.")
        recommendations.append("Shard workers by workspace/specialist hotspot and raise prewarmed capacity for browser and connector-heavy lanes.")
    if "fleet_retry_rate" in failed_names or "fleet_dead_letter_rate" in failed_names:
        bottlenecks.append("Worker-loss recovery is creating too many retries or dead letters.")
        recommendations.append("Reduce worker-loss detection latency and add more aggressive rebalancing for scarce capability pools before raising customer load.")
    if "fleet_completion_latency_p95" in failed_names or "fleet_queue_delay_p95" in failed_names:
        bottlenecks.append("Queueing and completion latency are above the target envelope during burst load.")
        recommendations.append("Apply rate-limited acceptance/backpressure earlier and add queue partition visibility to keep hot workspaces from starving the rest of the fleet.")
    if "fleet_error_rate" in failed_names:
        bottlenecks.append("Final failed-run rate is above the target envelope.")
        recommendations.append("Increase retry budget only for recoverable workloads and add workload-specific fallback paths instead of letting them collapse into dead letters.")
    if not bottlenecks:
        bottlenecks.append("No Phase 15 SLO breach was detected in the current harness run.")
        recommendations.append("Keep the harness in CI/ops and ratchet the targets tighter before increasing real customer traffic.")
    return {
        "bottlenecks": bottlenecks,
        "correction_recommendations": recommendations,
    }


async def run_scale_harness(
    *,
    control_plane_config: ControlPlaneScaleConfig | None = None,
    fleet_config: FleetScaleConfig | None = None,
    targets: Sequence[ScaleSloTarget] = DEFAULT_SCALE_SLO_TARGETS,
) -> Dict[str, Any]:
    control_plane = await run_control_plane_registry_benchmark(config=control_plane_config)
    fleet = run_fleet_runtime_simulation(config=fleet_config)
    report = {
        "generated_at": _utc_now_iso(),
        "control_plane": control_plane,
        "fleet": fleet,
        "slo_targets": [asdict(target) for target in targets],
    }
    slo_results = evaluate_scale_slos(report, targets=targets)
    report["slo_results"] = slo_results
    report["overall"] = {
        "passed": len([item for item in slo_results if bool(item.get("passed"))]),
        "failed": len([item for item in slo_results if not bool(item.get("passed"))]),
    }
    report.update(build_scale_findings(report, slo_results))
    return report
