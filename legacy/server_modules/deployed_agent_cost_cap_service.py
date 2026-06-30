from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any, Dict, Optional

from server_modules import (
    activity_ledger_service,
    control_plane_repository,
    credit_ledger_contract,
    deployed_agent_config_schema,
    outbox_service,
    usage_accounting_service,
)


LOGGER = logging.getLogger(__name__)

MONTHLY_COST_CAP_METADATA_KEY = "monthly_cost_cap_usd"


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_optional_text(value: Any) -> Optional[str]:
    token = _normalize_text(value)
    return token or None


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _round_usd(value: Any) -> float:
    try:
        return round(float(value or 0.0), 6)
    except Exception:
        return 0.0


def _month_start_iso(value: Any = None) -> str:
    raw = _normalize_text(value)
    parsed: Optional[datetime] = None
    if raw:
        try:
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            parsed = datetime.fromisoformat(raw).astimezone(timezone.utc)
        except Exception:
            parsed = None
    reference = parsed or datetime.now(timezone.utc)
    return reference.date().replace(day=1).isoformat()


def _parse_positive_usd(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    token = _normalize_text(value)
    if not token:
        return None
    try:
        parsed = round(float(token), 6)
    except Exception as exc:
        raise ValueError("monthly_cost_cap_usd must be a positive USD amount.") from exc
    if parsed <= 0:
        return None
    return parsed


def deployed_agent_monthly_cost_cap_usd(deployed_agent: Optional[Dict[str, Any]]) -> Optional[float]:
    config = deployed_agent_config_schema.deployed_agent_config_from_record(deployed_agent)
    return _parse_positive_usd(config.commerce_policy.monthly_cost_cap_usd)


def _deployed_agent_ai_source(deployed_agent: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    config = deployed_agent_config_schema.deployed_agent_config_from_record(deployed_agent)
    runtime_supply = _coerce_dict(config.runtime_supply)
    ai_source = _coerce_dict(runtime_supply.get("ai_source"))
    return ai_source


def _budget_cycle_from_metadata(deployed_agent: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    state = deployed_agent_config_schema.deployed_agent_operational_state_from_record(deployed_agent)
    return dict(state.current_budget_cycle or {})


def _run_usage_row(run_id: str, run: Dict[str, Any], *, status: str, now_iso: str) -> Optional[Dict[str, Any]]:
    usage_accounting = run.get("usage_accounting") if isinstance(run.get("usage_accounting"), dict) else {}
    usage_masked = run.get("usage_masked") if isinstance(run.get("usage_masked"), dict) else {}
    if not usage_accounting and not usage_masked:
        return None
    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    snapshot = {
        "run_id": run_id,
        "status": status,
        "completed_at": now_iso,
        "updated_at": now_iso,
        "created_at": run.get("created_at"),
        "owner_user_id": context.get("owner_user_id"),
        "user_goal": context.get("user_goal") or run.get("result"),
        "context": context,
        "usage_accounting": usage_accounting,
        "usage_masked": usage_masked,
    }
    return usage_accounting_service.usage_row_from_snapshot(snapshot)


def _run_has_usage_signal(run: Dict[str, Any]) -> bool:
    usage_accounting = run.get("usage_accounting") if isinstance(run.get("usage_accounting"), dict) else {}
    usage_masked = run.get("usage_masked") if isinstance(run.get("usage_masked"), dict) else {}
    return bool(usage_accounting or usage_masked)


def _threshold_already_notified(
    budget_cycle: Dict[str, Any],
    *,
    threshold: int,
    cap_usd: float,
    usage_month: str,
) -> bool:
    if _normalize_text(budget_cycle.get("usage_month")) != usage_month:
        return False
    threshold_token = str(int(threshold))
    notified_at = _normalize_optional_text(budget_cycle.get(f"threshold_{threshold_token}_notified_at"))
    recorded_cap = budget_cycle.get(f"threshold_{threshold_token}_cap_usd")
    if notified_at is None:
        return False
    try:
        return round(float(recorded_cap or 0.0), 6) == round(float(cap_usd), 6)
    except Exception:
        return False


def _build_budget_cycle(
    *,
    existing_cycle: Dict[str, Any],
    usage_month: str,
    cap_usd: Optional[float],
    total_cost_usd: float,
    runs_count: int,
    last_run_id: Optional[str],
    last_run_completed_at: Optional[str],
    last_threshold_reached: Optional[str],
    auto_paused_at: Optional[str],
    accounting_state: str,
    last_error: Optional[str],
) -> Dict[str, Any]:
    percent_used = 0.0
    if cap_usd is not None and cap_usd > 0:
        percent_used = round((float(total_cost_usd) / float(cap_usd)) * 100.0, 2)
    next_cycle = dict(existing_cycle or {})
    next_cycle.update(
        {
            "usage_month": usage_month,
            "monthly_cost_cap_usd": cap_usd,
            "current_burn_usd": _round_usd(total_cost_usd),
            "percent_used": percent_used,
            "runs_count": max(0, int(runs_count or 0)),
            "last_run_id": _normalize_optional_text(last_run_id),
            "last_run_completed_at": _normalize_optional_text(last_run_completed_at),
            "last_threshold_reached": _normalize_optional_text(last_threshold_reached),
            "auto_paused_at": _normalize_optional_text(auto_paused_at),
            "accounting_state": accounting_state,
        }
    )
    if last_error:
        next_cycle["last_error"] = str(last_error)[:400]
    else:
        next_cycle.pop("last_error", None)
    return next_cycle


def _notification_metadata(
    *,
    deployed_agent: Dict[str, Any],
    title: str,
    priority: str,
    threshold: Optional[str],
    cap_usd: Optional[float],
    burn_usd: Optional[float],
    usage_month: str,
) -> Dict[str, Any]:
    return {
        "title": title,
        "priority": priority,
        "path": "/deployed-agents",
        "deployed_agent_id": _normalize_optional_text(deployed_agent.get("id")),
        "deployed_agent_name": _normalize_optional_text(deployed_agent.get("name")),
        "threshold": threshold,
        "monthly_cost_cap_usd": _round_usd(cap_usd) if cap_usd is not None else None,
        "current_burn_usd": _round_usd(burn_usd) if burn_usd is not None else None,
        "usage_month": usage_month,
    }


def _emit_budget_notification(
    *,
    deployed_agent: Dict[str, Any],
    tenant_id: str,
    workspace_id: str,
    action: str,
    text: str,
    title: str,
    priority: str,
    threshold: Optional[str],
    cap_usd: Optional[float],
    burn_usd: Optional[float],
    usage_month: str,
) -> None:
    outbox_service.emit_notification_event(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        action=action,
        text=text,
        metadata=_notification_metadata(
            deployed_agent=deployed_agent,
            title=title,
            priority=priority,
            threshold=threshold,
            cap_usd=cap_usd,
            burn_usd=burn_usd,
            usage_month=usage_month,
        ),
    )


async def _append_budget_activity_event(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent: Dict[str, Any],
    action: str,
    title: str,
    summary: str,
    payload: Optional[Dict[str, Any]] = None,
    status: str = "logged",
    review_required: bool = False,
) -> None:
    deployed_agent_id = _normalize_optional_text(deployed_agent.get("id"))
    if not deployed_agent_id:
        return
    try:
        await activity_ledger_service.append_activity_event(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_type="deployed_agent",
            actor_id=deployed_agent_id,
            install_id=_normalize_optional_text(deployed_agent.get("backing_install_id")),
            event_class="system_activity",
            detail_level="audit_reference",
            action=action,
            title=title,
            summary=summary,
            status=status,
            review_required=bool(review_required),
            payload=dict(payload or {}),
            metadata={
                "deployed_agent_id": deployed_agent_id,
                "workspace_id": _normalize_optional_text(workspace_id),
            },
        )
    except Exception:
        LOGGER.exception(
            "Failed to append deployed-agent budget activity event",
            extra={"workspace_id": workspace_id, "deployed_agent_id": deployed_agent_id, "action": action},
        )


async def _persist_budget_cycle(
    *,
    deployed_agent: Dict[str, Any],
    tenant_id: str,
    workspace_id: str,
    budget_cycle: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    existing_state = deployed_agent_config_schema.deployed_agent_operational_state_from_record(deployed_agent)
    next_state = existing_state.model_copy(
        update={
            "current_budget_cycle": dict(budget_cycle or {}),
        }
    )
    updated = await control_plane_repository.update_deployed_agent(
        _normalize_text(deployed_agent.get("id")),
        tenant_id=tenant_id,
        owner_workspace_id=workspace_id,
        updates={"operational_state": next_state.model_dump(exclude_none=True)},
    )
    return updated if isinstance(updated, dict) else deployed_agent


async def _pause_deployment_fail_closed(
    *,
    deployed_agent: Dict[str, Any],
    tenant_id: str,
    workspace_id: str,
    usage_month: str,
    cap_usd: Optional[float],
    burn_usd: Optional[float],
    now_iso: str,
    error_text: str,
) -> Dict[str, Any]:
    budget_cycle = _build_budget_cycle(
        existing_cycle=_budget_cycle_from_metadata(deployed_agent),
        usage_month=usage_month,
        cap_usd=cap_usd,
        total_cost_usd=burn_usd or 0.0,
        runs_count=int((_budget_cycle_from_metadata(deployed_agent).get("runs_count") or 0)),
        last_run_id=None,
        last_run_completed_at=None,
        last_threshold_reached="fail_closed",
        auto_paused_at=now_iso,
        accounting_state="suspended_accounting_error",
        last_error=error_text,
    )
    updated = await _persist_budget_cycle(
        deployed_agent=deployed_agent,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        budget_cycle=budget_cycle,
    )
    if str((deployed_agent or {}).get("deployment_state") or "").strip().lower() != "suspended":
        await control_plane_repository.set_deployed_agent_state(
            _normalize_text(deployed_agent.get("id")),
            tenant_id=tenant_id,
            owner_workspace_id=workspace_id,
            deployment_state="suspended",
            last_paused_at=now_iso,
        )
    _emit_budget_notification(
        deployed_agent=updated or deployed_agent,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        action="deployed_agent_budget_tracking_failed",
        text=f"{_normalize_text(deployed_agent.get('name')) or 'This deployment'} was auto-suspended because monthly budget tracking could not be computed safely.",
        title="Deployment auto-suspended",
        priority="high",
        threshold=None,
        cap_usd=cap_usd,
        burn_usd=burn_usd,
        usage_month=usage_month,
    )
    await _append_budget_activity_event(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        deployed_agent=updated or deployed_agent,
        action="deployed_agent_budget_fail_closed_suspended",
        title="Deployment auto-suspended",
        summary="Monthly budget tracking failed closed and suspended deployment.",
        status="suspended",
        payload={
            "reason": "budget_tracking_failed",
            "usage_month": usage_month,
            "monthly_cost_cap_usd": _round_usd(cap_usd) if cap_usd is not None else None,
            "current_burn_usd": _round_usd(burn_usd) if burn_usd is not None else None,
        },
        review_required=True,
    )
    return {
        "applied": True,
        "fail_closed": True,
        "auto_paused": True,
        "auto_suspended": True,
        "usage_month": usage_month,
        "reason": "budget_tracking_failed",
        "budget_cycle": budget_cycle,
    }


async def settle_deployed_agent_monthly_cost_cap(
    *,
    run_id: str,
    run: Dict[str, Any],
    status: str,
    now_iso: str,
) -> Dict[str, Any]:
    if not isinstance(run, dict):
        return {"applied": False, "reason": "missing_run"}
    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    tenant_id = _normalize_text(context.get("tenant_id") or metadata.get("tenant_id") or "default") or "default"
    workspace_id = _normalize_text(context.get("workspace_id") or metadata.get("workspace_id") or "default") or "default"
    deployed_agent_id = _normalize_optional_text(metadata.get("deployed_agent_id"))
    if not deployed_agent_id:
        return {"applied": False, "reason": "not_deployed_agent"}
    workspace = None
    if workspace_id:
        workspace = await control_plane_repository.get_workspace_by_id(workspace_id)
        workspace_tenant_id = _normalize_optional_text((workspace or {}).get("tenant_id"))
        if workspace_tenant_id and tenant_id in {"", "default"}:
            tenant_id = workspace_tenant_id
    deployed_agent = await control_plane_repository.get_deployed_agent_by_id(
        deployed_agent_id,
        tenant_id=tenant_id,
        owner_workspace_id=workspace_id,
    )
    if not isinstance(deployed_agent, dict) and workspace_id:
        deployed_agent = await control_plane_repository.get_deployed_agent_by_id(
            deployed_agent_id,
            owner_workspace_id=workspace_id,
        )
    if not isinstance(deployed_agent, dict):
        return {"applied": False, "reason": "missing_deployment"}
    tenant_id = _normalize_text(deployed_agent.get("tenant_id") or tenant_id) or tenant_id
    workspace_id = _normalize_text(deployed_agent.get("owner_workspace_id") or workspace_id) or workspace_id
    cap_usd = deployed_agent_monthly_cost_cap_usd(deployed_agent)
    ai_source = _deployed_agent_ai_source(deployed_agent)
    ai_source_kind = _normalize_text(ai_source.get("kind") or ai_source.get("source")).lower()
    ai_payer = _normalize_text(ai_source.get("payer")).lower()
    platform_paid_ai = bool(ai_source.get("uses_platform_credits")) or usage_accounting_service.is_platform_paid_usage_value(
        ai_payer or ai_source_kind
    )
    usage_row = _run_usage_row(run_id, run, status=status, now_iso=now_iso)
    if usage_row is None:
        if (cap_usd is not None or platform_paid_ai) and _run_has_usage_signal(run):
            return await _pause_deployment_fail_closed(
                deployed_agent=deployed_agent,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                usage_month=_month_start_iso(now_iso),
                cap_usd=cap_usd,
                burn_usd=None,
                now_iso=now_iso,
                error_text=f"Run {run_id} completed with usage that could not be priced safely.",
        )
        return {"applied": False, "reason": "no_billable_usage"}
    if platform_paid_ai:
        validation_error = usage_accounting_service.platform_paid_usage_validation_error(usage_row)
        if validation_error:
            return await _pause_deployment_fail_closed(
                deployed_agent=deployed_agent,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                usage_month=_month_start_iso(usage_row.get("timestamp") or now_iso),
                cap_usd=cap_usd,
                burn_usd=None,
                now_iso=now_iso,
                error_text=(
                    f"Run {run_id} platform-credit AI usage was not exact enough for deployment "
                    f"billing ({validation_error})."
                ),
            )
    if cap_usd is not None and usage_row.get("estimated_cost_usd") is None and _run_has_usage_signal(run):
        return await _pause_deployment_fail_closed(
            deployed_agent=deployed_agent,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            usage_month=_month_start_iso(usage_row.get("timestamp") or now_iso),
            cap_usd=cap_usd,
            burn_usd=None,
            now_iso=now_iso,
            error_text=f"Run {run_id} usage could not be priced safely for deployment budget enforcement.",
        )

    usage_month = _month_start_iso(usage_row.get("timestamp") or now_iso)
    usage_metadata = usage_row.get("metadata") if isinstance(usage_row.get("metadata"), dict) else {}
    line_item_metadata = credit_ledger_contract.build_credit_ledger_line_item(
        metadata={
            **usage_metadata,
            "source_surface": _normalize_optional_text(usage_row.get("source_surface")),
            "ai_source_kind": ai_source_kind or None,
            "ai_payer": ai_payer or None,
            "platform_credit_enforced": platform_paid_ai,
        },
        public_tier=_normalize_optional_text(
            usage_metadata.get("public_tier")
        ),
        billing_source=_normalize_optional_text(
            ai_source_kind
            or usage_metadata.get("billing_source")
        ),
        total_tokens=int(usage_row.get("total_tokens") or 0),
    )
    unified_ledger_event = credit_ledger_contract.build_unified_credit_ledger_event(
        surface="studio",
        source_surface=_normalize_optional_text(usage_row.get("source_surface")) or "deployed_agent_channel",
        payer=ai_payer or ("platform_credits" if platform_paid_ai else "BYOK"),
        credit_type=line_item_metadata.get("credit_type") or "ai_tokens",
        provider=_normalize_optional_text(usage_row.get("provider")),
        model=_normalize_optional_text(usage_row.get("model")),
        runtime_target=_normalize_optional_text(
            usage_metadata.get("runtime_target")
            or _coerce_dict(deployed_agent.get("metadata")).get("runtime_target")
            or deployed_agent.get("runtime_target")
        ),
        workspace_id=workspace_id,
        user_id=_normalize_optional_text(usage_row.get("user_id")),
        thread_id=_normalize_optional_text(usage_row.get("thread_id")),
        run_id=run_id,
        agent_id=deployed_agent_id,
        provider_usage=usage_row.get("usage_accounting") if isinstance(usage_row.get("usage_accounting"), dict) else usage_row,
        platform_cost_usd=usage_row.get("estimated_cost_usd") if platform_paid_ai else 0,
        provider_reported_cost=usage_row.get("provider_cost_usd") or usage_row.get("estimated_cost_usd"),
        provider_reported_currency="USD",
        credits_debited=usage_row.get("retail_credits_charged") if platform_paid_ai else 0,
        estimation_mode=_normalize_optional_text(usage_row.get("estimation_mode")),
        created_at=_normalize_optional_text(usage_row.get("completed_at") or now_iso),
    )
    try:
        ledger_entry = await control_plane_repository.record_deployed_agent_monthly_cost_ledger_entry(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            deployed_agent_id=deployed_agent_id,
            usage_month=usage_month,
            run_id=run_id,
            run_status=status,
            provider=_normalize_optional_text(usage_row.get("provider")),
            model=_normalize_optional_text(usage_row.get("model")),
            prompt_tokens=int(usage_row.get("prompt_tokens") or 0),
            completion_tokens=int(usage_row.get("completion_tokens") or 0),
            total_tokens=int(usage_row.get("total_tokens") or 0),
            estimated_cost_usd=_round_usd(usage_row.get("estimated_cost_usd")),
            completed_at=_normalize_optional_text(usage_row.get("completed_at") or now_iso),
            metadata={
                "usage_record_id": _normalize_optional_text(usage_row.get("usage_record_id")),
                "pricing_known": bool(usage_row.get("pricing_known")),
                "pricing_registry_version": _normalize_optional_text(usage_row.get("pricing_registry_version")),
                "pricing_source": _normalize_optional_text(usage_row.get("pricing_source")),
                "estimation_mode": _normalize_optional_text(usage_row.get("estimation_mode")),
                "source_surface": _normalize_optional_text(usage_row.get("source_surface")),
                "cost_band": _normalize_optional_text(usage_row.get("cost_band")),
                "billing_source": line_item_metadata.get("billing_source"),
                "ai_source_kind": ai_source_kind or None,
                "ai_payer": ai_payer or None,
                "platform_credit_enforced": platform_paid_ai,
                "credit_type": line_item_metadata.get("credit_type"),
                "public_tier": line_item_metadata.get("public_tier"),
                "credit_item_type": line_item_metadata.get("credit_item_type"),
                "credit_quantity": line_item_metadata.get("quantity"),
                "credit_quantity_unit": line_item_metadata.get("quantity_unit"),
                "credit_multiplier": line_item_metadata.get("credit_multiplier"),
                "unified_credit_ledger_event": unified_ledger_event,
            },
        )
        if not isinstance(ledger_entry, dict):
            raise RuntimeError("deployed-agent monthly cost ledger persistence returned no row")
        durable_event = await control_plane_repository.record_credit_ledger_event(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            event=unified_ledger_event,
            source_table="deployed_agent_monthly_cost_ledger",
            source_event_id=_normalize_optional_text(ledger_entry.get("id")) or run_id,
        )
        if not isinstance(durable_event, dict):
            raise RuntimeError("deployed-agent unified credit ledger persistence returned no row")
        summary = await control_plane_repository.summarize_deployed_agent_monthly_cost_ledger(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            deployed_agent_id=deployed_agent_id,
            usage_month=usage_month,
        )
    except Exception as exc:
        LOGGER.warning("Failed to record deployed-agent cost ledger for %s: %s", run_id, exc)
        if cap_usd is not None or platform_paid_ai:
            return await _pause_deployment_fail_closed(
                deployed_agent=deployed_agent,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                usage_month=usage_month,
                cap_usd=cap_usd,
                burn_usd=None,
                now_iso=now_iso,
                error_text=f"Run {run_id} could not be recorded in the monthly deployment budget ledger.",
            )
        return {"applied": False, "reason": "ledger_write_failed"}

    budget_cycle = _budget_cycle_from_metadata(deployed_agent)
    total_cost_usd = _round_usd(summary.get("total_cost_usd"))
    runs_count = int(summary.get("runs_count") or 0)
    last_run_id = _normalize_optional_text(summary.get("last_run_id"))
    last_run_completed_at = _normalize_optional_text(summary.get("last_run_completed_at"))
    last_threshold_reached: Optional[str] = None
    auto_paused = False

    if cap_usd is not None and total_cost_usd >= cap_usd:
        last_threshold_reached = "100"
    elif cap_usd is not None and total_cost_usd >= (cap_usd * 0.8):
        last_threshold_reached = "80"

    next_budget_cycle = _build_budget_cycle(
        existing_cycle=budget_cycle if _normalize_text(budget_cycle.get("usage_month")) == usage_month else {},
        usage_month=usage_month,
        cap_usd=cap_usd,
        total_cost_usd=total_cost_usd,
        runs_count=runs_count,
        last_run_id=last_run_id,
        last_run_completed_at=last_run_completed_at,
        last_threshold_reached=last_threshold_reached,
        auto_paused_at=_normalize_optional_text(budget_cycle.get("auto_paused_at")),
        accounting_state="tracked",
        last_error=None,
    )

    if cap_usd is not None and total_cost_usd >= (cap_usd * 0.8) and not _threshold_already_notified(
        budget_cycle,
        threshold=80,
        cap_usd=cap_usd,
        usage_month=usage_month,
    ):
        next_budget_cycle["threshold_80_notified_at"] = now_iso
        next_budget_cycle["threshold_80_cap_usd"] = cap_usd
        _emit_budget_notification(
            deployed_agent=deployed_agent,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            action="deployed_agent_budget_80",
            text=f"{_normalize_text(deployed_agent.get('name')) or 'This deployment'} reached 80% of its monthly cost cap (${total_cost_usd:.2f} of ${cap_usd:.2f}).",
            title="Deployment at 80% of budget",
            priority="normal",
            threshold="80",
            cap_usd=cap_usd,
            burn_usd=total_cost_usd,
            usage_month=usage_month,
        )

    if cap_usd is not None and total_cost_usd >= cap_usd and not _threshold_already_notified(
        budget_cycle,
        threshold=100,
        cap_usd=cap_usd,
        usage_month=usage_month,
    ):
        will_auto_suspend = str(deployed_agent.get("deployment_state") or "").strip().lower() != "suspended"
        next_budget_cycle["threshold_100_notified_at"] = now_iso
        next_budget_cycle["threshold_100_cap_usd"] = cap_usd
        _emit_budget_notification(
            deployed_agent=deployed_agent,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            action="deployed_agent_budget_100",
            text=(
                f"{_normalize_text(deployed_agent.get('name')) or 'This deployment'} reached 100% of its monthly cost cap "
                f"(${total_cost_usd:.2f} of ${cap_usd:.2f}) and was auto-suspended."
                if will_auto_suspend
                else f"{_normalize_text(deployed_agent.get('name')) or 'This deployment'} reached 100% of its monthly cost cap "
                f"(${total_cost_usd:.2f} of ${cap_usd:.2f})."
            ),
            title="Deployment hit monthly budget cap",
            priority="high",
            threshold="100",
            cap_usd=cap_usd,
            burn_usd=total_cost_usd,
            usage_month=usage_month,
        )

    if cap_usd is not None and total_cost_usd >= cap_usd:
        auto_paused = str(deployed_agent.get("deployment_state") or "").strip().lower() != "suspended"
        next_budget_cycle["auto_paused_at"] = now_iso

    updated = await _persist_budget_cycle(
        deployed_agent=deployed_agent,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        budget_cycle=next_budget_cycle,
    )
    await _append_budget_activity_event(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        deployed_agent=updated or deployed_agent,
        action="deployed_agent_cost_spend_recorded",
        title="Deployment spend recorded",
        summary=(
            f"Monthly spend is now ${total_cost_usd:.2f}"
            + (f" of ${cap_usd:.2f} cap." if cap_usd is not None else ".")
        ),
        payload={
            "usage_month": usage_month,
            "total_cost_usd": total_cost_usd,
            "runs_count": runs_count,
            "monthly_cost_cap_usd": _round_usd(cap_usd) if cap_usd is not None else None,
            "percent_used": next_budget_cycle.get("percent_used"),
            "last_threshold_reached": last_threshold_reached,
            "run_id": run_id,
        },
    )

    if auto_paused:
        await control_plane_repository.set_deployed_agent_state(
            deployed_agent_id,
            tenant_id=tenant_id,
            owner_workspace_id=workspace_id,
            deployment_state="suspended",
            last_paused_at=now_iso,
        )
        await _append_budget_activity_event(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            deployed_agent=updated or deployed_agent,
            action="deployed_agent_budget_cap_suspended",
            title="Deployment auto-suspended",
            summary="Monthly spend cap reached and deployment was auto-suspended.",
            status="suspended",
            payload={
                "usage_month": usage_month,
                "total_cost_usd": total_cost_usd,
                "monthly_cost_cap_usd": _round_usd(cap_usd) if cap_usd is not None else None,
                "threshold": "100",
                "run_id": run_id,
            },
            review_required=True,
        )

    return {
        "applied": True,
        "deployed_agent_id": deployed_agent_id,
        "usage_month": usage_month,
        "total_cost_usd": total_cost_usd,
        "runs_count": runs_count,
        "cap_usd": cap_usd,
        "percent_used": next_budget_cycle.get("percent_used"),
        "last_threshold_reached": last_threshold_reached,
        "auto_paused": auto_paused,
        "auto_suspended": auto_paused,
        "budget_cycle": next_budget_cycle,
        "deployment_state": "suspended" if auto_paused else _normalize_optional_text((updated or deployed_agent).get("deployment_state")),
    }
