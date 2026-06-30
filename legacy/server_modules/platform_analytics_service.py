from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from fastapi import HTTPException

from server_modules import auth as auth_module
from server_modules import billing_service
from server_modules import control_plane_repository
from server_modules import deployed_agent_config_schema
from server_modules import runtime_usage_service


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_optional_text(value: Any) -> Optional[str]:
    token = _normalize_text(value)
    return token or None


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _read_float(value: Any) -> float:
    try:
        return round(float(value or 0.0), 6)
    except (TypeError, ValueError):
        return 0.0


def _month_start_iso(now: Optional[datetime] = None) -> str:
    current = now.astimezone(timezone.utc) if isinstance(now, datetime) else datetime.now(timezone.utc)
    return current.date().replace(day=1).isoformat()


def _require_platform_admin(current_user: Any) -> Dict[str, Any]:
    if not auth_module.current_user_has_auth_admin_access(current_user if isinstance(current_user, dict) else None):
        raise HTTPException(status_code=403, detail="Admin role required.")
    return dict(current_user or {})


async def _workspace_lookup(workspace_ids: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    tokens = sorted({str(item or "").strip() for item in workspace_ids if str(item or "").strip()})
    if not tokens:
        return {}
    rows = await asyncio.gather(
        *[control_plane_repository.get_workspace_by_id(workspace_id) for workspace_id in tokens]
    )
    return {
        workspace_id: dict(row)
        for workspace_id, row in zip(tokens, rows)
        if isinstance(row, dict)
    }


async def _billing_proxy_lookup(workspace_ids: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    tokens = sorted({str(item or "").strip() for item in workspace_ids if str(item or "").strip()})
    if not tokens:
        return {}
    summaries = await asyncio.gather(
        *[control_plane_repository.get_workspace_billing_summary(workspace_id) for workspace_id in tokens]
    )
    return {
        workspace_id: billing_service.billing_proxy_from_summary(summary if isinstance(summary, dict) else None)
        for workspace_id, summary in zip(tokens, summaries)
    }


async def _summarize_platform_deployment(
    deployed_agent: Dict[str, Any],
    *,
    usage_month: str,
    workspace_lookup: Dict[str, Dict[str, Any]],
    billing_proxy_lookup: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    deployed_agent_id = _normalize_text(deployed_agent.get("id"))
    workspace_id = _normalize_text(deployed_agent.get("owner_workspace_id"))
    backing_install_id = _normalize_text(deployed_agent.get("backing_install_id"))
    activity_rollup, cost_summary = await asyncio.gather(
        control_plane_repository.summarize_deployed_agent_activity_rollup(
            tenant_id=_normalize_text(deployed_agent.get("tenant_id")),
            workspace_id=workspace_id,
            deployed_agent_id=deployed_agent_id,
            backing_install_id=backing_install_id,
        ),
        control_plane_repository.summarize_deployed_agent_monthly_cost_ledger(
            tenant_id=_normalize_text(deployed_agent.get("tenant_id")),
            workspace_id=workspace_id,
            deployed_agent_id=deployed_agent_id,
            usage_month=usage_month,
        ),
    )
    workspace = _coerce_dict(workspace_lookup.get(workspace_id))
    billing_proxy = _coerce_dict(billing_proxy_lookup.get(workspace_id))
    config = deployed_agent_config_schema.deployed_agent_config_from_record(deployed_agent)
    return {
        "deployed_agent_id": deployed_agent_id or None,
        "workspace": {
            "id": workspace_id or None,
            "label": _normalize_optional_text(workspace.get("name")) or workspace_id or None,
        },
        "name": _normalize_optional_text(deployed_agent.get("name")),
        "deployment_state": _normalize_optional_text(deployed_agent.get("deployment_state")) or "draft",
        "runtime_target": _normalize_optional_text(deployed_agent.get("runtime_target")),
        "provider": _normalize_optional_text(config.provider),
        "model": _normalize_optional_text(config.model),
        "deployment_billing_plan": _normalize_optional_text(deployed_agent.get("billing_plan")) or "free",
        "active_users_last_30d": int(activity_rollup.get("active_users_30d") or 0),
        "message_volume": {
            "day": int(activity_rollup.get("message_count_day") or 0),
            "week": int(activity_rollup.get("message_count_week") or 0),
            "month": int(activity_rollup.get("message_count_month") or 0),
            "latest_message_at": _normalize_optional_text(activity_rollup.get("latest_message_at")),
        },
        "cost_burn": {
            "usage_month": _normalize_optional_text(cost_summary.get("usage_month")) or usage_month,
            "current_burn_usd": _read_float(cost_summary.get("total_cost_usd")),
            "runs_count": int(cost_summary.get("runs_count") or 0),
        },
        "billing_proxy": {
            **billing_proxy,
            "deployment_billing_plan": _normalize_optional_text(deployed_agent.get("billing_plan")) or "free",
        },
    }


async def build_platform_analytics_payload(
    *,
    current_user: Any,
    usage_month: Optional[str] = None,
    top_limit: int = 10,
) -> Dict[str, Any]:
    _require_platform_admin(current_user)
    resolved_usage_month = _normalize_optional_text(usage_month) or _month_start_iso()
    deployed_agents = await control_plane_repository.list_platform_deployed_agents(limit=1000)
    workspace_ids = [
        _normalize_text(item.get("owner_workspace_id"))
        for item in deployed_agents
        if isinstance(item, dict)
    ]
    workspace_lookup, billing_proxy_lookup, cost_ledger_rows = await asyncio.gather(
        _workspace_lookup(workspace_ids),
        _billing_proxy_lookup(workspace_ids),
        control_plane_repository.list_platform_deployed_agent_monthly_cost_ledger_entries(
            usage_month=resolved_usage_month,
            limit=10000,
        ),
    )
    deployment_rows = await asyncio.gather(
        *[
            _summarize_platform_deployment(
                dict(item),
                usage_month=resolved_usage_month,
                workspace_lookup=workspace_lookup,
                billing_proxy_lookup=billing_proxy_lookup,
            )
            for item in deployed_agents
            if isinstance(item, dict)
        ]
    )
    deployment_rows = [
        dict(item)
        for item in deployment_rows
        if isinstance(item, dict)
    ]
    deployment_rows.sort(
        key=lambda item: (
            -int(_coerce_dict(item.get("message_volume")).get("month") or 0),
            -int(item.get("active_users_last_30d") or 0),
            -_read_float(_coerce_dict(item.get("cost_burn")).get("current_burn_usd")),
            _normalize_text(item.get("name") or item.get("deployed_agent_id")),
        )
    )
    model_cost_breakdown = runtime_usage_service.summarize_provider_model_cost_rows(
        [dict(item) for item in list(cost_ledger_rows or []) if isinstance(item, dict)]
    )
    total_active_users = sum(int(item.get("active_users_last_30d") or 0) for item in deployment_rows)
    total_messages_day = sum(int(_coerce_dict(item.get("message_volume")).get("day") or 0) for item in deployment_rows)
    total_messages_week = sum(int(_coerce_dict(item.get("message_volume")).get("week") or 0) for item in deployment_rows)
    total_messages_month = sum(int(_coerce_dict(item.get("message_volume")).get("month") or 0) for item in deployment_rows)
    total_current_month_cost = round(
        sum(_read_float(_coerce_dict(item.get("cost_burn")).get("current_burn_usd")) for item in deployment_rows),
        6,
    )

    return {
        "scope": "platform",
        "usage_month": resolved_usage_month,
        "summary": {
            "total_deployed_agents": len(deployment_rows),
            "total_workspaces_with_deployments": len(workspace_lookup),
            "total_active_external_users_30d": total_active_users,
            "total_messages": total_messages_month,
            "message_volume": {
                "day": total_messages_day,
                "week": total_messages_week,
                "month": total_messages_month,
            },
            "current_month_cost_usd": total_current_month_cost,
        },
        "deployments": deployment_rows,
        "most_active_deployed_agents": deployment_rows[: max(1, int(top_limit or 10))],
        "model_cost_breakdown": model_cost_breakdown,
    }
