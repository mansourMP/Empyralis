from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from server_modules import pricing_registry_service, usage_accounting_service

MODEL_PRICING_USD_PER_MILLION: Dict[str, Dict[str, Dict[str, Any]]] = (
    pricing_registry_service.MODEL_PRICING_USD_PER_MILLION
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return datetime.fromisoformat(raw).astimezone(timezone.utc)
    except Exception:
        return None


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except Exception:
        return None


def normalize_provider_id(provider: Any) -> str:
    return pricing_registry_service.normalize_provider_id(provider)


def normalize_model_id(provider: Any, model: Any) -> str:
    return pricing_registry_service.normalize_model_id(provider, model)


def lookup_model_pricing(provider: Any, model: Any) -> Optional[Dict[str, Any]]:
    return pricing_registry_service.lookup_model_pricing(provider, model)


def masked_cost_band(cost_usd: Optional[float]) -> str:
    if cost_usd is None:
        return "Unknown"
    if cost_usd <= 0:
        return "$0.00"
    if cost_usd < 0.01:
        return "$0.00 - $0.01"
    if cost_usd < 0.05:
        return "$0.01 - $0.05"
    if cost_usd < 0.10:
        return "$0.05 - $0.10"
    return "$0.10+"


def estimate_cost_usd(provider: Any, model: Any, prompt_tokens: int, completion_tokens: int) -> Optional[float]:
    return pricing_registry_service.estimate_cost_usd(provider, model, prompt_tokens, completion_tokens)


def build_usage_record(
    provider: Any,
    model: Any,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: Optional[int] = None,
    *,
    run_id: Optional[str] = None,
    timestamp: Optional[str] = None,
) -> Dict[str, Any]:
    record = usage_accounting_service.build_usage_accounting_record(
        run_id=str(run_id or "").strip() or None,
        requested_provider=provider,
        effective_provider=provider,
        requested_model=model,
        effective_model=model,
        input_tokens=max(0, int(prompt_tokens)),
        output_tokens=max(0, int(completion_tokens)),
        total_tokens=max(max(0, int(prompt_tokens)) + max(0, int(completion_tokens)), int(total_tokens or 0)),
        estimation_mode="reconstructed",
        completed_at=str(timestamp or "").strip() or _utc_now_iso(),
    )
    return usage_accounting_service.usage_projection_from_record(record)


def enrich_usage_record(usage: Dict[str, Any], *, run_id: Optional[str] = None, timestamp: Optional[str] = None) -> Dict[str, Any]:
    record = usage_accounting_service.accounting_record_from_snapshot(
        {
            "run_id": str(run_id or usage.get("run_id") or "").strip() or None,
            "usage_masked": usage,
            "completed_at": str(timestamp or usage.get("timestamp") or "").strip() or None,
        }
    )
    if record is None:
        return dict(usage or {})
    merged = usage_accounting_service.usage_projection_from_record(record)
    for key, value in usage.items():
        if key not in merged:
            merged[key] = value
    return merged


def usage_row_from_snapshot(snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return usage_accounting_service.usage_row_from_snapshot(snapshot)


def _period_floor(period: str, now: Optional[datetime] = None) -> Optional[datetime]:
    reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    normalized = str(period or "all").strip().lower()
    if normalized == "day":
        return reference.replace(hour=0, minute=0, second=0, microsecond=0)
    if normalized == "week":
        midnight = reference.replace(hour=0, minute=0, second=0, microsecond=0)
        return midnight - timedelta(days=midnight.weekday())
    if normalized == "month":
        return reference.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return None


def filter_usage_rows(rows: Iterable[Dict[str, Any]], period: str, *, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    floor = _period_floor(period, now=now)
    out: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if floor is None:
            out.append(row)
            continue
        ts = _parse_timestamp(row.get("timestamp") or row.get("completed_at") or row.get("created_at"))
        if ts is None or ts >= floor:
            out.append(row)
    return out


def _dedupe_usage_rows(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_run: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
    for row in rows:
        if not isinstance(row, dict):
            continue
        run_id = str(row.get("run_id") or "").strip()
        if not run_id:
            continue
        by_run[run_id] = row
    return list(by_run.values())


def aggregate_usage_summary(
    snapshots: Iterable[Dict[str, Any]],
    period: str = "all",
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    rows = _dedupe_usage_rows(
        usage_row_from_snapshot(snapshot)  # type: ignore[arg-type]
        for snapshot in snapshots
        if isinstance(snapshot, dict)
    )
    filtered = filter_usage_rows(rows, period, now=now)
    total_tokens = sum(_safe_int(item.get("total_tokens"), 0) for item in filtered)
    total_cost = round(
        sum(_safe_float(item.get("estimated_cost_usd")) or 0.0 for item in filtered),
        6,
    )

    by_provider: Dict[str, Dict[str, Any]] = {}
    by_model: Dict[Tuple[str, str], Dict[str, Any]] = {}
    daily: Dict[str, Dict[str, Any]] = {}

    for item in filtered:
        provider = str(item.get("provider") or "unknown").strip() or "unknown"
        model = str(item.get("model") or "unknown-model").strip() or "unknown-model"
        tokens = _safe_int(item.get("total_tokens"), 0)
        cost = round(_safe_float(item.get("estimated_cost_usd")) or 0.0, 6)
        provider_bucket = by_provider.setdefault(
            provider,
            {"provider": provider, "total_tokens": 0, "total_cost_usd": 0.0, "runs_count": 0},
        )
        provider_bucket["total_tokens"] += tokens
        provider_bucket["total_cost_usd"] = round(float(provider_bucket["total_cost_usd"]) + cost, 6)
        provider_bucket["runs_count"] += 1

        model_bucket = by_model.setdefault(
            (provider, model),
            {"provider": provider, "model": model, "total_tokens": 0, "total_cost_usd": 0.0, "runs_count": 0},
        )
        model_bucket["total_tokens"] += tokens
        model_bucket["total_cost_usd"] = round(float(model_bucket["total_cost_usd"]) + cost, 6)
        model_bucket["runs_count"] += 1

        ts = _parse_timestamp(item.get("timestamp") or item.get("completed_at") or item.get("created_at"))
        day_key = (ts or (now or datetime.now(timezone.utc))).astimezone(timezone.utc).date().isoformat()
        day_bucket = daily.setdefault(day_key, {"date": day_key, "total_tokens": 0, "total_cost_usd": 0.0, "runs_count": 0})
        day_bucket["total_tokens"] += tokens
        day_bucket["total_cost_usd"] = round(float(day_bucket["total_cost_usd"]) + cost, 6)
        day_bucket["runs_count"] += 1

    provider_items = sorted(by_provider.values(), key=lambda item: (-int(item["total_tokens"]), str(item["provider"])))
    for provider_item in provider_items:
        provider_item["percentage"] = round(
            (float(provider_item["total_tokens"]) / float(total_tokens)) * 100.0,
            2,
        ) if total_tokens > 0 else 0.0

    model_items = sorted(
        by_model.values(),
        key=lambda item: (-int(item["total_tokens"]), str(item["provider"]), str(item["model"])),
    )
    run_items = sorted(
        filtered,
        key=lambda item: (-_safe_int(item.get("total_tokens"), 0), str(item.get("timestamp") or "")),
    )
    daily_items = sorted(daily.values(), key=lambda item: str(item["date"]))

    return {
        "period": str(period or "all").strip().lower() or "all",
        "total_tokens": total_tokens,
        "total_cost_usd": total_cost,
        "runs_count": len(filtered),
        "by_provider": provider_items,
        "by_model": model_items,
        "by_run": run_items[:10],
        "daily": daily_items,
    }


def list_usage_runs(
    snapshots: Iterable[Dict[str, Any]],
    *,
    period: str = "all",
    limit: int = 50,
    offset: int = 0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    rows = _dedupe_usage_rows(
        usage_row_from_snapshot(snapshot)  # type: ignore[arg-type]
        for snapshot in snapshots
        if isinstance(snapshot, dict)
    )
    filtered = filter_usage_rows(rows, period, now=now)
    ordered = sorted(
        filtered,
        key=lambda item: (-_safe_int(item.get("total_tokens"), 0), str(item.get("timestamp") or "")),
    )
    safe_limit = max(1, min(int(limit), 200))
    safe_offset = max(0, int(offset))
    sliced = ordered[safe_offset:safe_offset + safe_limit]
    return {
        "items": sliced,
        "count": len(sliced),
        "total": len(ordered),
        "limit": safe_limit,
        "offset": safe_offset,
        "period": str(period or "all").strip().lower() or "all",
    }
