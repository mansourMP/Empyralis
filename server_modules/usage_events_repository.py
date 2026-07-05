"""Normalized per-call LLM usage metering (Phase 5A).

One row per LLM call in `usage_events`, with full attribution
(agent_install_id, project_id, workspace_id), provider/model, token counts,
and a usd_cost computed via pricing_registry_service. This is the normalized,
queryable per-call table the existing monthly cost ledgers never provided —
those aggregate by month and lack agent_install_id / project_id.

Postgres-first with a SQLite fallback, mirroring control_plane_repository's
pattern. Recording is best-effort: a metering failure must never break a turn.
"""

from __future__ import annotations

import contextvars
import logging
import uuid
from typing import Any, Dict, List, Optional

from server_modules import control_plane_repository as _cpr

logger = logging.getLogger(__name__)

# Attribution for the LLM call(s) inside the current turn. Set once at the top of
# handle_sage_chat (which both the web and channel paths converge on), read by
# record_usage_from_context() at the generation persist point — so the deep
# action-loop / generation code needn't thread agent_install_id all the way down.
USAGE_ATTRIBUTION: "contextvars.ContextVar[Optional[Dict[str, Any]]]" = contextvars.ContextVar(
    "usage_attribution", default=None
)


def set_usage_attribution(
    *,
    tenant_id: str,
    workspace_id: str,
    agent_install_id: Optional[str],
    project_id: Optional[str],
    mode: Optional[str] = None,
    run_id: Optional[str] = None,
    surface: Optional[str] = None,
) -> None:
    USAGE_ATTRIBUTION.set(
        {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "agent_install_id": agent_install_id,
            "project_id": project_id,
            "mode": mode,
            "run_id": run_id,
            "surface": surface,
        }
    )


async def record_usage_from_context(
    *,
    provider: Optional[str],
    model: Optional[str],
    tokens_in: int,
    tokens_out: int,
    usd_cost: Optional[float] = None,
    run_id: Optional[str] = None,
    mode: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Record a usage_event using the current turn's attribution contextvar.
    No-op (returns None) when no attribution is set or workspace is unknown.
    `mode` (payer) overrides the contextvar mode when supplied."""
    attr = USAGE_ATTRIBUTION.get()
    if not isinstance(attr, dict) or not str(attr.get("workspace_id") or "").strip():
        return None
    return await record_usage_event(
        tenant_id=str(attr.get("tenant_id") or "default"),
        workspace_id=str(attr.get("workspace_id") or ""),
        provider=provider,
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        agent_install_id=attr.get("agent_install_id"),
        project_id=attr.get("project_id"),
        mode=mode or attr.get("mode"),
        run_id=run_id or attr.get("run_id"),
        surface=attr.get("surface"),
        usd_cost=usd_cost,
    )

USAGE_EVENTS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS usage_events (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    agent_install_id TEXT NULL,
    project_id TEXT NULL,
    provider TEXT NULL,
    model TEXT NULL,
    mode TEXT NULL,                       -- platform_credits | byok | local | no_provider
    tokens_in BIGINT NOT NULL DEFAULT 0,
    tokens_out BIGINT NOT NULL DEFAULT 0,
    total_tokens BIGINT NOT NULL DEFAULT 0,
    usd_cost DOUBLE PRECISION NOT NULL DEFAULT 0,
    pricing_known BOOLEAN NOT NULL DEFAULT FALSE,
    run_id TEXT NULL,                     -- turn / run id
    surface TEXT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_usage_events_ws_created ON usage_events(tenant_id, workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_usage_events_agent ON usage_events(tenant_id, workspace_id, agent_install_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_usage_events_project ON usage_events(tenant_id, workspace_id, project_id, created_at DESC);
"""

_SCHEMA_READY = False


async def _ensure_schema(conn: Any) -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    await conn.execute(USAGE_EVENTS_SCHEMA_SQL)
    _SCHEMA_READY = True


async def record_usage_event(
    *,
    tenant_id: str,
    workspace_id: str,
    provider: Optional[str],
    model: Optional[str],
    tokens_in: int,
    tokens_out: int,
    agent_install_id: Optional[str] = None,
    project_id: Optional[str] = None,
    mode: Optional[str] = None,
    run_id: Optional[str] = None,
    surface: Optional[str] = None,
    usd_cost: Optional[float] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Record one LLM call. usd_cost is computed via pricing_registry_service
    when not supplied. Returns the row dict, or None on failure/no-pool."""
    import json as _json

    from server_modules import pricing_registry_service

    ti = max(0, int(tokens_in or 0))
    to = max(0, int(tokens_out or 0))
    computed_cost = usd_cost
    pricing_known = usd_cost is not None
    if computed_cost is None:
        try:
            computed_cost = pricing_registry_service.estimate_cost_usd(provider, model, ti, to)
            pricing_known = computed_cost is not None
        except Exception:
            computed_cost = None
            pricing_known = False
    row = {
        "id": f"usage_{uuid.uuid4().hex}",
        "tenant_id": str(tenant_id or "default").strip() or "default",
        "workspace_id": str(workspace_id or "").strip(),
        "agent_install_id": (str(agent_install_id).strip() or None) if agent_install_id else None,
        "project_id": (str(project_id).strip() or None) if project_id else None,
        "provider": (str(provider).strip() or None) if provider else None,
        "model": (str(model).strip() or None) if model else None,
        "mode": (str(mode).strip() or None) if mode else None,
        "tokens_in": ti,
        "tokens_out": to,
        "total_tokens": ti + to,
        "usd_cost": float(computed_cost or 0.0),
        "pricing_known": bool(pricing_known),
        "run_id": (str(run_id).strip() or None) if run_id else None,
        "surface": (str(surface).strip() or None) if surface else None,
    }
    try:
        pool = await _cpr.ensure_control_plane_schema()
        if pool is None:
            return None
        async with _cpr._scoped_connection(bypass_rls=True) as conn:
            if conn is None:
                return None
            await _ensure_schema(conn)
            await conn.execute(
                """
                INSERT INTO usage_events (
                    id, tenant_id, workspace_id, agent_install_id, project_id, provider, model, mode,
                    tokens_in, tokens_out, total_tokens, usd_cost, pricing_known, run_id, surface, metadata
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16::jsonb)
                """,
                row["id"], row["tenant_id"], row["workspace_id"], row["agent_install_id"], row["project_id"],
                row["provider"], row["model"], row["mode"], row["tokens_in"], row["tokens_out"],
                row["total_tokens"], row["usd_cost"], row["pricing_known"], row["run_id"], row["surface"],
                _json.dumps(metadata or {}),
            )
        return row
    except Exception:
        logger.exception("record_usage_event failed (non-fatal)")
        return None


_PERIOD_TRUNC = {"day": "day", "week": "week", "month": "month"}


async def summarize_usage(
    *,
    tenant_id: str,
    workspace_id: str,
    scope: str = "workspace",
    scope_id: Optional[str] = None,
    period: str = "day",
) -> Dict[str, Any]:
    """Rollup usage. scope ∈ {workspace, agent, project}; scope_id is the
    agent_install_id / project_id when scope isn't workspace. period ∈
    {day, week, month} groups the time buckets. Returns totals + buckets +
    a per-agent breakdown (used by project rollups)."""
    period_key = _PERIOD_TRUNC.get(str(period or "day").strip().lower(), "day")
    where = ["tenant_id = $1", "workspace_id = $2"]
    args: List[Any] = [str(tenant_id or "default").strip() or "default", str(workspace_id or "").strip()]
    scope_norm = str(scope or "workspace").strip().lower()
    if scope_norm == "agent" and scope_id:
        where.append(f"agent_install_id = ${len(args) + 1}")
        args.append(str(scope_id).strip())
    elif scope_norm == "project" and scope_id:
        where.append(f"project_id = ${len(args) + 1}")
        args.append(str(scope_id).strip())
    where_sql = " AND ".join(where)

    empty = {
        "ok": True, "scope": scope_norm, "scope_id": scope_id, "period": period_key,
        "totals": {"events": 0, "tokens_in": 0, "tokens_out": 0, "total_tokens": 0, "usd_cost": 0.0},
        "buckets": [], "by_agent": [],
    }
    try:
        pool = await _cpr.ensure_control_plane_schema()
        if pool is None:
            return empty
        async with _cpr._scoped_connection(bypass_rls=True) as conn:
            if conn is None:
                return empty
            await _ensure_schema(conn)
            totals = await conn.fetchrow(
                f"""
                SELECT count(*) AS events, COALESCE(sum(tokens_in),0) AS tokens_in,
                       COALESCE(sum(tokens_out),0) AS tokens_out, COALESCE(sum(total_tokens),0) AS total_tokens,
                       COALESCE(sum(usd_cost),0) AS usd_cost
                FROM usage_events WHERE {where_sql}
                """,
                *args,
            )
            buckets = await conn.fetch(
                f"""
                SELECT date_trunc('{period_key}', created_at) AS bucket,
                       count(*) AS events, COALESCE(sum(total_tokens),0) AS total_tokens,
                       COALESCE(sum(usd_cost),0) AS usd_cost
                FROM usage_events WHERE {where_sql}
                GROUP BY bucket ORDER BY bucket DESC LIMIT 90
                """,
                *args,
            )
            by_agent = await conn.fetch(
                f"""
                SELECT agent_install_id, count(*) AS events,
                       COALESCE(sum(total_tokens),0) AS total_tokens, COALESCE(sum(usd_cost),0) AS usd_cost
                FROM usage_events WHERE {where_sql}
                GROUP BY agent_install_id ORDER BY usd_cost DESC
                """,
                *args,
            )
        return {
            "ok": True,
            "scope": scope_norm,
            "scope_id": scope_id,
            "period": period_key,
            "totals": {
                "events": int(totals["events"]), "tokens_in": int(totals["tokens_in"]),
                "tokens_out": int(totals["tokens_out"]), "total_tokens": int(totals["total_tokens"]),
                "usd_cost": round(float(totals["usd_cost"]), 6),
            },
            "buckets": [
                {"bucket": b["bucket"].isoformat() if b["bucket"] else None, "events": int(b["events"]),
                 "total_tokens": int(b["total_tokens"]), "usd_cost": round(float(b["usd_cost"]), 6)}
                for b in buckets
            ],
            "by_agent": [
                {"agent_install_id": r["agent_install_id"], "events": int(r["events"]),
                 "total_tokens": int(r["total_tokens"]), "usd_cost": round(float(r["usd_cost"]), 6)}
                for r in by_agent
            ],
        }
    except Exception:
        logger.exception("summarize_usage failed")
        return empty
