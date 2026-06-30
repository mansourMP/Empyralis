from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from server_modules import control_plane_repository, deployed_agent_config_schema, run_state_repository


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_optional_text(value: Any) -> Optional[str]:
    token = _normalize_text(value)
    return token or None


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _read_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 6)
    token = _normalize_text(value)
    if not token:
        return None
    try:
        return round(float(token), 6)
    except (TypeError, ValueError):
        return None


def _month_start_iso(now: Optional[datetime] = None) -> str:
    current = now.astimezone(timezone.utc) if isinstance(now, datetime) else datetime.now(timezone.utc)
    return current.date().replace(day=1).isoformat()


async def _resolve_run_outcome(run_id: Optional[str]) -> Optional[str]:
    token = _normalize_optional_text(run_id)
    if not token:
        return None
    live_run = await run_state_repository.get_live_run(token)
    if isinstance(live_run, dict):
        status = _normalize_optional_text(live_run.get("status") or live_run.get("state"))
        if status:
            return status.lower()
    archived_run = await run_state_repository.get_archived_run(token)
    if isinstance(archived_run, dict):
        status = _normalize_optional_text(archived_run.get("status") or archived_run.get("state"))
        if status:
            return status.lower()
    return None


def _normalize_outcome_token(value: Any) -> str:
    token = _normalize_text(value).lower()
    return token or "open"


async def summarize_deployed_agent_analytics(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent: Dict[str, Any],
) -> Dict[str, Any]:
    deployed_agent_id = _normalize_text(deployed_agent.get("id"))
    backing_install_id = _normalize_text(deployed_agent.get("backing_install_id"))
    config = deployed_agent_config_schema.deployed_agent_config_from_record(deployed_agent)
    operational_state = deployed_agent_config_schema.deployed_agent_operational_state_from_record(
        deployed_agent
    )
    current_cycle = _coerce_dict(operational_state.current_budget_cycle)
    empty_cost = {
        "usage_month": _normalize_optional_text(current_cycle.get("usage_month")) or _month_start_iso(),
        "current_burn_usd": 0.0,
        "cap_usd": _read_float(config.commerce_policy.monthly_cost_cap_usd),
        "percent_used": None,
        "last_threshold_reached": _normalize_optional_text(current_cycle.get("last_threshold_reached")),
    }
    if not deployed_agent_id or not backing_install_id:
        return {
            "deployed_agent_id": deployed_agent_id or None,
            "active_users_last_30d": 0,
            "message_volume": {
                "day": 0,
                "week": 0,
                "month": 0,
                "latest_message_at": None,
            },
            "escalation": {
                "total_sessions": 0,
                "escalated_sessions": 0,
                "rate": 0.0,
                "rate_percent": 0.0,
            },
            "outcomes": {
                "counts": {"open": 0},
                "top_outcome": "open",
            },
            "cost_burn": empty_cost,
            "daily_series": [],
        }

    activity_rollup, session_rows, cost_summary, daily_series = await asyncio.gather(
        control_plane_repository.summarize_deployed_agent_activity_rollup(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            deployed_agent_id=deployed_agent_id,
            backing_install_id=backing_install_id,
        ),
        control_plane_repository.list_deployed_agent_session_analytics(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            deployed_agent_id=deployed_agent_id,
            backing_install_id=backing_install_id,
        ),
        control_plane_repository.summarize_deployed_agent_monthly_cost_ledger(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            deployed_agent_id=deployed_agent_id,
            usage_month=_month_start_iso(),
        ),
        control_plane_repository.list_deployed_agent_daily_activity_series(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            deployed_agent_id=deployed_agent_id,
            backing_install_id=backing_install_id,
            days=30,
        ),
    )

    normalized_session_rows = [dict(item) for item in list(session_rows or []) if isinstance(item, dict)]
    missing_outcome_rows = [row for row in normalized_session_rows if not _normalize_optional_text(row.get("latest_outcome"))]
    if missing_outcome_rows:
        resolved_outcomes = await asyncio.gather(
            *[_resolve_run_outcome(row.get("latest_run_id")) for row in missing_outcome_rows]
        )
        for row, outcome in zip(missing_outcome_rows, resolved_outcomes):
            if outcome:
                row["latest_outcome"] = outcome

    outcome_counts: Dict[str, int] = {}
    escalated_sessions = 0
    for row in normalized_session_rows:
        outcome = _normalize_outcome_token(row.get("latest_outcome"))
        outcome_counts[outcome] = int(outcome_counts.get(outcome) or 0) + 1
        if bool(row.get("had_escalation")) or outcome in {"requested", "approval_requested", "escalated"}:
            escalated_sessions += 1
    if not outcome_counts:
        outcome_counts["open"] = 0
    total_sessions = len(normalized_session_rows)
    top_outcome = max(outcome_counts.items(), key=lambda item: (item[1], item[0]))[0] if outcome_counts else "open"
    escalation_rate = round((escalated_sessions / total_sessions), 4) if total_sessions > 0 else 0.0

    current_burn_usd = _read_float(cost_summary.get("total_cost_usd"))
    cap_usd = _read_float(config.commerce_policy.monthly_cost_cap_usd)
    percent_used = round((current_burn_usd / cap_usd) * 100.0, 2) if current_burn_usd is not None and cap_usd else None

    return {
        "deployed_agent_id": deployed_agent_id,
        "active_users_last_30d": int(activity_rollup.get("active_users_30d") or 0),
        "message_volume": {
            "day": int(activity_rollup.get("message_count_day") or 0),
            "week": int(activity_rollup.get("message_count_week") or 0),
            "month": int(activity_rollup.get("message_count_month") or 0),
            "latest_message_at": _normalize_optional_text(activity_rollup.get("latest_message_at")),
        },
        "escalation": {
            "total_sessions": total_sessions,
            "escalated_sessions": escalated_sessions,
            "rate": escalation_rate,
            "rate_percent": round(escalation_rate * 100.0, 2),
        },
        "outcomes": {
            "counts": outcome_counts,
            "top_outcome": top_outcome,
        },
        "cost_burn": {
            "usage_month": _normalize_optional_text(cost_summary.get("usage_month")) or _normalize_optional_text(current_cycle.get("usage_month")) or _month_start_iso(),
            "current_burn_usd": current_burn_usd if current_burn_usd is not None else 0.0,
            "cap_usd": cap_usd,
            "percent_used": percent_used if percent_used is not None else _read_float(current_cycle.get("percent_used")),
            "last_threshold_reached": _normalize_optional_text(current_cycle.get("last_threshold_reached")),
        },
        "daily_series": [dict(item) for item in list(daily_series or []) if isinstance(item, dict)],
    }


async def summarize_workspace_deployed_agent_analytics(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agents: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    items = [dict(item) for item in list(deployed_agents or []) if isinstance(item, dict)]
    if not items:
        return {"items": []}
    analytics_rows = await asyncio.gather(
        *[
            summarize_deployed_agent_analytics(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                deployed_agent=item,
            )
            for item in items
        ]
    )
    return {"items": analytics_rows}
