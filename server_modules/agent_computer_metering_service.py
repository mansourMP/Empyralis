from __future__ import annotations

"""MAN-134 (metering half only) — debit a workspace's real credit_balance_usd
for the hours an Agent Computer (a provisioned VPS) has actually run.

Explicitly METERING ONLY. This module never stops, destroys, or reclaims a
machine — see CLAUDE.md's MAN-134 entry and the ticket itself: reaping
(zero-balance destruction, idle detection) is a deliberately separate,
founder-approved change. If you are reading this file looking for a
`destroy`/`delete`/reap call, it does not belong here.

── SHAPE ──────────────────────────────────────────────────────────────────

Mirrors deployed_agent_virtual_runtime_service.runtime_usage_credit_debit_plan
exactly on purpose (ticket's own instruction — "copy the shape"):

  agent_computer_hourly_credit_debit_plan()   PURE, DB-free. Given one VPS
      record's (created_at, provider, size) and "now", decide which whole-
      hour buckets are due and what request_id/credits each one needs.
      Independently unit-testable with no database, same as its sibling.

  meter_one_agent_computer()                  DB-touching. Calls the plan
      function, then debits each due bucket through the SAME idempotent
      primitive every other credit debit in this codebase uses
      (control_plane_repository.debit_workspace_credits_for_turn_atomic),
      then advances a high-water mark in the VPS record's own metadata so
      the NEXT sweep tick does not replay the box's whole history.

  run_agent_computer_metering_sweep()         Lists every active VPS
      (agent_computers_repository.list_active_vps_for_metering) and meters
      each one, isolating one box's failure from the rest.

  agent_computer_metering_loop()              The `asyncio.create_task`
      background loop, wired into shared.py's app_lifespan exactly like
      telemetry_retention_service.telemetry_retention_loop — same
      env-gated master switch, same "never let one failed sweep kill the
      loop" posture, same interval-from-env-on-every-tick so it can be
      retuned in production without a restart.

── WHY A HIGH-WATER MARK, NOT JUST THE DEBIT PRIMITIVE'S OWN IDEMPOTENCY ──

debit_workspace_credits_for_turn_atomic is already idempotent by request_id
(a repeat call for a bucket already recorded is a cheap no-op, "insufficient"
stays false, credits_debited is 0) — that alone is what makes a double-run
financially safe. But a long-running box's hour-bucket count only grows, and
replaying its ENTIRE history every sweep tick forever would mean an
ever-growing number of debit calls per box per tick, almost all of them
no-ops. `metering.last_billed_hour_bucket` in the VPS record's metadata
(written via agent_computers_repository.update_vps_record, which MERGES
metadata rather than replacing it) is a pure optimization on top of that
safety net, not a second one: lose it, corrupt it, or run two sweeps
concurrently, and the worst case is still "some no-op debit calls", never a
double charge. This is also what makes the call-count assertion in
test_agent_computer_metering_service.py meaningful rather than tautological
— the SWEEP itself must not re-attempt an already-billed bucket, not just
"the balance happens to be right because the primitive caught it".

── UNPRICED SIZES ─────────────────────────────────────────────────────────

pricing_registry_service.droplet_hourly_rate_usd returns None for a size
outside DROPLET_PRICING_USD_PER_HOUR (see that module's own docstring for
why this table is deliberately partial). This module's answer to "I don't
know the price" is to SKIP billing that box and say so in the sweep's
report — never invent a number and never block/destroy the box over it
(destroying anything is out of scope for this ticket regardless).
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from server_modules import (
    agent_computers_repository,
    billing_credit_config,
    control_plane_repository,
    pricing_registry_service,
)

LOGGER = logging.getLogger(__name__)

# The transaction `source` this module's debits are stamped with — see
# control_plane_repository.debit_workspace_credits_for_turn_atomic's `source`
# kwarg. Read by billing_service (usage-history labelling) and
# CreditsPanel.tsx (categoryForItem) to separate this from AI-chat spend.
AGENT_COMPUTER_DEBIT_SOURCE = "agent_computer_hourly"

SECONDS_PER_HOUR = 3600.0


def metering_enabled() -> bool:
    """EMPYRALIS_AGENT_COMPUTER_METERING_ENABLED (default "1"). Master
    switch — same convention as EMPYRALIS_TELEMETRY_RETENTION_ENABLED."""
    raw = str(os.environ.get("EMPYRALIS_AGENT_COMPUTER_METERING_ENABLED", "1") or "").strip().lower()
    return raw not in {"0", "false", "off", "no"}


def _env_positive_int(name: str, default: int) -> int:
    raw = str(os.environ.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        LOGGER.warning("%s=%r is not a valid integer; using default %d", name, raw, default)
        return default
    if value <= 0:
        LOGGER.warning("%s=%r must be positive; using default %d", name, raw, default)
        return default
    return value


DEFAULT_METERING_INTERVAL_SECONDS = 300  # 5 minutes — real money, checked often


def metering_interval_seconds() -> int:
    return _env_positive_int(
        "EMPYRALIS_AGENT_COMPUTER_METERING_INTERVAL_SECONDS",
        DEFAULT_METERING_INTERVAL_SECONDS,
    )


def _text(value: Any) -> str:
    return str(value or "").strip()


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def agent_computer_hourly_credit_debit_plan(
    *,
    vps_id: str,
    provider: str,
    size: str,
    workspace_id: str,
    tenant_id: str,
    created_at: Any,
    now: Any,
    last_billed_hour_bucket: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Pure, DB-free — mirrors runtime_usage_credit_debit_plan's contract.

    Decides which whole-hour buckets of Agent Computer runtime are due, and
    what request_id/credit amount each needs. Returns zero, one, or many
    plan dicts (one per completed-but-unbilled hour bucket); each dict's
    request_id/credits_to_charge/floor_usd/credits_per_usd/source keys are
    exactly the kwargs control_plane_repository.debit_workspace_credits_for_
    turn_atomic takes, so a caller can `**`-expand one directly (see
    meter_one_agent_computer below).

    request_id = f"agent_computer:{vps_id}:{hour_bucket}" — namespaced by
    vps_id and a zero-based integer count of complete hours since
    created_at. Stable across repeated calls: the same (vps_id, hour_bucket)
    always produces the same request_id, so debit_workspace_credits_for_
    turn_atomic's own request_id dedup means a re-run can never double-charge
    even if last_billed_hour_bucket bookkeeping is stale, lost, or never
    passed at all.

    Returns [] (never bills, never raises) when: the size has no known price
    (pricing_registry_service.droplet_hourly_rate_usd returns None — "cannot
    bill safely", not "bill zero"), created_at/now cannot be parsed, no
    complete hour has elapsed yet, or every complete hour is already covered
    by last_billed_hour_bucket.
    """
    clean_vps_id = _text(vps_id)
    if not clean_vps_id:
        return []
    rate_usd_per_hour = pricing_registry_service.droplet_hourly_rate_usd(provider, size)
    if rate_usd_per_hour is None:
        return []
    started = _parse_iso_datetime(created_at)
    moment = _parse_iso_datetime(now)
    if not started or not moment or moment <= started:
        return []
    elapsed_seconds = (moment - started).total_seconds()
    complete_hours = int(elapsed_seconds // SECONDS_PER_HOUR)
    if complete_hours <= 0:
        return []
    start_bucket = 0
    if last_billed_hour_bucket is not None:
        try:
            start_bucket = max(0, int(last_billed_hour_bucket) + 1)
        except (TypeError, ValueError):
            start_bucket = 0
    if start_bucket >= complete_hours:
        return []
    credits_owed = billing_credit_config.credits_for_turn_cost_usd(rate_usd_per_hour)
    if credits_owed <= 0:
        return []
    plans: List[Dict[str, Any]] = []
    for hour_bucket in range(start_bucket, complete_hours):
        plans.append(
            {
                "vps_id": clean_vps_id,
                "hour_bucket": hour_bucket,
                "workspace_id": _text(workspace_id),
                "tenant_id": _text(tenant_id),
                "request_id": f"agent_computer:{clean_vps_id}:{hour_bucket}",
                "credits_to_charge": credits_owed,
                "floor_usd": billing_credit_config.NEW_ACCOUNT_SIGNUP_CREDIT_USD,
                "credits_per_usd": billing_credit_config.HOSTED_SAGE_AI_CREDITS_PER_USD,
                "source": AGENT_COMPUTER_DEBIT_SOURCE,
            }
        )
    return plans


def _metering_metadata(record: Dict[str, Any]) -> Dict[str, Any]:
    value = record.get("metering")
    return dict(value) if isinstance(value, dict) else {}


async def meter_one_agent_computer(
    record: Dict[str, Any],
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Meter ONE VPS record: compute its due hour buckets, debit each
    through the real idempotent primitive, then persist a high-water mark so
    the next sweep tick does not replay already-billed hours. Never raises —
    every failure is caught and reported in the returned dict so one box's
    problem can never take the sweep down (see run_agent_computer_metering_
    sweep, which relies on this)."""
    vps_id = _text(record.get("vps_id"))
    if not vps_id:
        return {"vps_id": vps_id, "billed_buckets": 0, "reason": "missing_vps_id"}
    status = _text(record.get("status")) or "provisioning"
    if status not in agent_computers_repository.ACTIVE_VPS_STATUSES:
        return {"vps_id": vps_id, "billed_buckets": 0, "reason": "not_active", "status": status}

    provider = _text(record.get("provider"))
    size = _text(record.get("size"))
    metering_meta = _metering_metadata(record)
    last_billed = metering_meta.get("last_billed_hour_bucket")
    moment = now or datetime.now(timezone.utc)

    plans = agent_computer_hourly_credit_debit_plan(
        vps_id=vps_id,
        provider=provider,
        size=size,
        workspace_id=_text(record.get("workspace_id")),
        tenant_id=_text(record.get("tenant_id")),
        created_at=record.get("created_at"),
        now=moment,
        last_billed_hour_bucket=last_billed,
    )
    if not plans:
        rate_known = pricing_registry_service.droplet_hourly_rate_usd(provider, size) is not None
        return {
            "vps_id": vps_id,
            "billed_buckets": 0,
            "reason": "no_buckets_due" if rate_known else "unpriced_size",
            "provider": provider,
            "size": size,
        }

    billed_buckets = 0
    total_credits_debited = 0
    highest_bucket = last_billed
    errors: List[str] = []
    for plan in plans:
        debit_kwargs = {k: v for k, v in plan.items() if k not in {"vps_id", "hour_bucket"}}
        try:
            result = await control_plane_repository.debit_workspace_credits_for_turn_atomic(**debit_kwargs)
        except Exception:
            LOGGER.exception(
                "agent_computer_metering: debit failed for vps_id=%s hour_bucket=%s",
                vps_id, plan["hour_bucket"],
            )
            errors.append(f"debit_failed:hour_{plan['hour_bucket']}")
            # Stop advancing the high-water mark past the first failure so a
            # transient debit error does not silently skip a real hour of
            # runtime on the next tick's replay window.
            break
        if isinstance(result, dict) and result.get("ok"):
            highest_bucket = plan["hour_bucket"]
            billed_buckets += 1
            total_credits_debited += int(result.get("credits_debited") or 0)
        else:
            errors.append(f"debit_not_ok:hour_{plan['hour_bucket']}")
            break

    if highest_bucket != last_billed:
        try:
            await agent_computers_repository.update_vps_record(
                vps_id,
                metadata_updates={
                    "metering": {
                        "last_billed_hour_bucket": highest_bucket,
                        "last_metered_at": _utc_now_iso(),
                    }
                },
            )
        except Exception:
            LOGGER.exception(
                "agent_computer_metering: failed to persist high-water mark for vps_id=%s", vps_id
            )
            errors.append("high_water_mark_persist_failed")

    return {
        "vps_id": vps_id,
        "billed_buckets": billed_buckets,
        "credits_debited": total_credits_debited,
        "last_billed_hour_bucket": highest_bucket,
        "errors": errors,
    }


class AgentComputerMeteringSweepResult:
    __slots__ = ("started_at", "vps_considered", "vps_billed", "unpriced", "errors", "total_credits_debited")

    def __init__(self) -> None:
        self.started_at = _utc_now_iso()
        self.vps_considered = 0
        self.vps_billed = 0
        self.unpriced: List[Dict[str, str]] = []
        self.errors: List[str] = []
        self.total_credits_debited = 0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "started_at": self.started_at,
            "vps_considered": self.vps_considered,
            "vps_billed": self.vps_billed,
            "unpriced": self.unpriced,
            "errors": self.errors,
            "total_credits_debited": self.total_credits_debited,
        }


async def run_agent_computer_metering_sweep() -> AgentComputerMeteringSweepResult:
    """One pass over every active Agent Computer, metering each. Never
    raises — a table read failure or one box's exception is caught and
    reported; the caller (agent_computer_metering_loop) treats a raised
    exception here as a bug in THIS function, not in an individual record's
    handling, since every per-record path already isolates its own errors."""
    result = AgentComputerMeteringSweepResult()
    if not metering_enabled():
        LOGGER.info("Agent Computer metering sweep skipped: EMPYRALIS_AGENT_COMPUTER_METERING_ENABLED is off.")
        return result
    try:
        records = await agent_computers_repository.list_active_vps_for_metering()
    except Exception:
        LOGGER.exception("agent_computer_metering: failed to list active VPS records")
        result.errors.append("list_active_vps_failed")
        return result

    result.vps_considered = len(records)
    for record in records:
        try:
            outcome = await meter_one_agent_computer(record)
        except Exception:
            LOGGER.exception(
                "agent_computer_metering: unhandled error metering vps_id=%s",
                record.get("vps_id"),
            )
            result.errors.append(f"unhandled:{record.get('vps_id')}")
            continue
        if outcome.get("billed_buckets"):
            result.vps_billed += 1
            result.total_credits_debited += int(outcome.get("credits_debited") or 0)
        if outcome.get("reason") == "unpriced_size":
            result.unpriced.append(
                {
                    "vps_id": outcome.get("vps_id") or "",
                    "provider": outcome.get("provider") or "",
                    "size": outcome.get("size") or "",
                }
            )
        for error in outcome.get("errors") or []:
            result.errors.append(f"{outcome.get('vps_id')}:{error}")

    if result.unpriced:
        LOGGER.warning(
            "agent_computer_metering: %d active VPS on unpriced sizes were not billed this sweep: %s",
            len(result.unpriced), result.unpriced,
        )
    return result


async def agent_computer_metering_loop() -> None:
    """Periodic background task: run_agent_computer_metering_sweep() every
    metering_interval_seconds(), forever, until cancelled. Wired up from
    shared.py's app_lifespan the same way telemetry_retention_loop is — a
    failed sweep must never kill the loop, and the interval is re-read from
    the env on every tick so it can be retuned in production without a
    restart."""
    import asyncio

    while True:
        try:
            await run_agent_computer_metering_sweep()
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Agent Computer metering sweep failed")
        await asyncio.sleep(max(60, metering_interval_seconds()))
