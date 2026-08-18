from __future__ import annotations

"""MAN-353 — an Agent Computer whose Gateway registration was revoked while
its cloud droplet kept running (and kept billing).

Explicitly DETECTION ONLY. This module never destroys, stops, or reclaims a
machine. It answers "which cloud resources are we still paying for that
nothing is using any more" and says so loudly; acting on that answer is a
separate, founder-approved change — the same line
agent_computer_metering_service.py draws for the metering half of MAN-134,
and drawn here for the same reason: a revoke can be accidental or temporary,
and destroying the box takes the agent's local state with it. If you are
reading this file looking for a `destroy`/`delete`/reap call, it does not
belong here; `vps_provisioning_service.delete_recorded_vps` is the one
place that tears a resource down, reached from the owner's own
`DELETE /api/hardware/vps/{vps_id}`.

── THE GAP THIS EXISTS FOR ────────────────────────────────────────────────

The two lifecycle endpoints are asymmetric, and only one direction was ever
wired:

    DELETE /hardware/vps/{vps_id}            destroy droplet ─▶ revoke gateway  ✓
    POST /gateway/registrations/{id}/revoke  revoke gateway  ─▶ (nothing)       ✗

So "remove this from the Hardware page" stops the box being reachable,
leaves the row marked revoked, and leaves the droplet running on the
provider's meter forever. Confirmed live 2026-08-14: a box was revoked and
was still billing two days later, with nothing anywhere saying so.

── THE ONLY DURABLE LINK ──────────────────────────────────────────────────

`record_vps_provision` never writes the gateway_id back onto the VPS record
(it stores the pairing token, which the Gateway consumes once at register
time), so the sole link between "this droplet" and "this Gateway box" is
`metadata.vps_id` on the REGISTRATION — the same field
routes_gateway._find_gateway_bound_to_vps reads for the opposite direction.
Everything here is built on that one field, which means:

  * a registration WITHOUT metadata.vps_id is not evidence of anything. It
    is a physical Mac, or a box someone stood up by hand outside the
    product's provisioning path. `not_cloud_backed` is a distinct verdict
    from `orphaned`, never folded into it.
  * a registration WITH a vps_id whose record cannot be read is a THIRD
    fact — we know it was cloud-backed and we cannot tell what happened to
    it. `backing_record_missing`, never quietly reported as released.

CLAUDE.md, "After an action, the product must tell the person what actually
happened": "empty" and "I could not load this" are different facts. The
verdicts below keep them apart on purpose.

── WHAT THIS CANNOT SEE ───────────────────────────────────────────────────

Both directions of the check start from OUR OWN tables, so a cloud resource
this product never recorded is invisible to it — a droplet created by hand
in the provider console, or one whose `agent_computers` row was lost, will
never appear in any report here no matter how long it bills. Closing that
needs a provider-side inventory reconciliation (list the account's real
droplets, diff against `agent_computers`), which is a different check with
a different failure mode (it needs a live provider credential, and a
provider outage must not be reported as "everything is orphaned"). It is
deliberately not attempted here.
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence

from server_modules import agent_computers_repository

LOGGER = logging.getLogger(__name__)

DEFAULT_ORPHAN_SWEEP_INTERVAL_SECONDS = 60 * 60

# Every verdict the classifiers below can return. Named constants rather
# than bare literals so a caller comparing against one gets an ImportError
# on a rename instead of an `if` that silently stops matching.
VERDICT_ORPHANED = "orphaned"
VERDICT_LIVE = "live"
VERDICT_NEVER_PAIRED = "never_paired"
VERDICT_NOT_CLOUD_BACKED = "not_cloud_backed"
VERDICT_BACKING_RECORD_MISSING = "backing_record_missing"

ORPHAN_VERDICTS: tuple[str, ...] = (
    VERDICT_ORPHANED,
    VERDICT_LIVE,
    VERDICT_NEVER_PAIRED,
    VERDICT_NOT_CLOUD_BACKED,
    VERDICT_BACKING_RECORD_MISSING,
)


def orphan_sweep_enabled() -> bool:
    """EMPYRALIS_AGENT_COMPUTER_ORPHAN_SWEEP_ENABLED (default "1").

    Defaults ON, unlike a switch guarding anything that mutates state: this
    sweep reads two tables and writes a log line. The failure mode of it
    being on is noise; the failure mode of it being off is the bug it exists
    to catch, silently, exactly as before.
    """
    raw = str(
        os.environ.get("EMPYRALIS_AGENT_COMPUTER_ORPHAN_SWEEP_ENABLED", "1") or ""
    ).strip().lower()
    return raw not in {"0", "false", "off", "no"}


def orphan_sweep_interval_seconds() -> int:
    raw = str(
        os.environ.get("EMPYRALIS_AGENT_COMPUTER_ORPHAN_SWEEP_INTERVAL_SECONDS", "") or ""
    ).strip()
    if not raw:
        return DEFAULT_ORPHAN_SWEEP_INTERVAL_SECONDS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_ORPHAN_SWEEP_INTERVAL_SECONDS
    return value if value > 0 else DEFAULT_ORPHAN_SWEEP_INTERVAL_SECONDS


def _text(value: Any) -> str:
    return str(value or "").strip()


def backing_vps_id(registration: Optional[Mapping[str, Any]]) -> str:
    """The vps_id a Gateway registration says it is backed by, or "".

    PURE. `metadata` arrives as a dict off gateway_state_repository's own row
    mapper, but a hand-built registration (or a row whose JSON failed to
    parse) can carry anything, so a non-mapping metadata is treated as
    absent rather than raised on — this is called from a best-effort path on
    the revoke route, where an exception would turn a successful revoke into
    a 500.
    """
    if not isinstance(registration, Mapping):
        return ""
    metadata = registration.get("metadata")
    if not isinstance(metadata, Mapping):
        return ""
    return _text(metadata.get("vps_id"))


def _registration_is_revoked(registration: Mapping[str, Any]) -> bool:
    return _text(registration.get("status")).lower() == "revoked"


def classify_agent_computer_orphan(
    *,
    vps_record: Mapping[str, Any],
    registrations: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Given ONE active VPS record and every Gateway registration in its
    workspace, decide whether the cloud resource is still earning its bill.

    PURE and DB-free, the same split agent_computer_hourly_credit_debit_plan
    uses, so the decision is unit-testable without a database or a provider.

    `registrations` must INCLUDE revoked ones — a revoked registration is
    the entire signal here, so filtering them out upstream turns every
    orphan into `never_paired` and the check reports nothing forever.

    The caller is responsible for only passing ACTIVE (non-terminal) VPS
    records: a record already marked deleted/failed has had
    _destroy_vps_provider_resource run against it and is not a billing
    question.
    """
    vps_id = _text(vps_record.get("vps_id"))
    bound = [reg for reg in registrations if vps_id and backing_vps_id(reg) == vps_id]
    base: Dict[str, Any] = {
        "vps_id": vps_id,
        "workspace_id": _text(vps_record.get("workspace_id")),
        "tenant_id": _text(vps_record.get("tenant_id")),
        "provider": _text(vps_record.get("provider")),
        "provider_resource_id": _text(vps_record.get("provider_resource_id")),
        "region": _text(vps_record.get("region")),
        "size": _text(vps_record.get("size")),
        "status": _text(vps_record.get("status")),
        "created_at": _text(vps_record.get("created_at")),
        "gateway_ids": [_text(reg.get("gateway_id")) for reg in bound],
    }
    if not bound:
        # Normal for a box mid-provision (it pairs only once the installer
        # finishes). NOT an orphan — but `created_at` rides along so a
        # record stuck here for days is visible to whoever reads the report,
        # rather than being flattened into "nothing to see".
        base["verdict"] = VERDICT_NEVER_PAIRED
        return base
    if all(_registration_is_revoked(reg) for reg in bound):
        revoked_timestamps = sorted(
            _text(reg.get("revoked_at")) for reg in bound if _text(reg.get("revoked_at"))
        )
        base["verdict"] = VERDICT_ORPHANED
        base["revoked_at"] = revoked_timestamps[-1] if revoked_timestamps else ""
        base["revoked_reason"] = next(
            (_text(reg.get("revoked_reason")) for reg in bound if _text(reg.get("revoked_reason"))),
            "",
        )
        return base
    base["verdict"] = VERDICT_LIVE
    return base


def classify_registration_backing(
    *,
    registration: Mapping[str, Any],
    vps_record: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    """The revoke-time half: given the registration being revoked and the
    VPS record it names (or None because the lookup found nothing), say what
    is left running.

    PURE. Four outcomes rather than two, per this module's header:

        no vps_id at all            ─▶ not_cloud_backed
        vps_id, record unreadable   ─▶ backing_record_missing
        vps_id, record terminal     ─▶ live (already released, not billing)
        vps_id, record still active ─▶ orphaned  (a droplet is still billing)
    """
    vps_id = backing_vps_id(registration)
    payload: Dict[str, Any] = {
        "gateway_id": _text(registration.get("gateway_id")),
        "vps_id": vps_id,
    }
    if not vps_id:
        payload["verdict"] = VERDICT_NOT_CLOUD_BACKED
        return payload
    if vps_record is None:
        payload["verdict"] = VERDICT_BACKING_RECORD_MISSING
        return payload
    status = _text(vps_record.get("status")).lower()
    payload.update(
        {
            "provider": _text(vps_record.get("provider")),
            "provider_resource_id": _text(vps_record.get("provider_resource_id")),
            "region": _text(vps_record.get("region")),
            "size": _text(vps_record.get("size")),
            "status": status,
        }
    )
    if status in set(agent_computers_repository.TERMINAL_VPS_STATUSES):
        payload["verdict"] = VERDICT_LIVE
        payload["still_billing"] = False
        return payload
    payload["verdict"] = VERDICT_ORPHANED
    payload["still_billing"] = True
    return payload


async def resolve_revoked_registration_backing(
    registration: Mapping[str, Any],
) -> Optional[Dict[str, Any]]:
    """What `POST /gateway/registrations/{id}/revoke` should say about the
    cloud resource it just orphaned.

    NEVER raises and never blocks the revoke: the revoke has already
    committed by the time this runs, and CLAUDE.md's outcome-honesty rule
    means a failure to describe the aftermath must not be reported as a
    failure to revoke. Returns None only when this registration was never
    cloud-backed in the first place, so the caller can omit the key
    entirely rather than render an empty box for a physical Mac.
    """
    vps_id = backing_vps_id(registration)
    if not vps_id:
        return None
    record: Optional[Mapping[str, Any]] = None
    try:
        record = await agent_computers_repository.get_vps_record(vps_id)
    except Exception:
        LOGGER.exception(
            "agent_computer_orphan: could not read the VPS record backing gateway_id=%s vps_id=%s",
            _text(registration.get("gateway_id")),
            vps_id,
        )
        record = None
    verdict = classify_registration_backing(registration=registration, vps_record=record)
    if verdict.get("verdict") == VERDICT_ORPHANED:
        LOGGER.error(
            "agent_computer_orphan: gateway_id=%s was revoked but its %s resource %s (vps_id=%s, %s/%s) "
            "is still running and still billing. Revoking a registration does not destroy the box; "
            "tearing it down is DELETE /api/hardware/vps/%s.",
            verdict.get("gateway_id"),
            verdict.get("provider"),
            verdict.get("provider_resource_id"),
            vps_id,
            verdict.get("region"),
            verdict.get("size"),
            vps_id,
        )
    elif verdict.get("verdict") == VERDICT_BACKING_RECORD_MISSING:
        LOGGER.error(
            "agent_computer_orphan: gateway_id=%s names vps_id=%s but no such Agent Computer record could "
            "be read, so whether a cloud resource is still billing for it is UNKNOWN.",
            verdict.get("gateway_id"),
            vps_id,
        )
    return verdict


class AgentComputerOrphanSweepResult:
    """Mirrors AgentComputerMeteringSweepResult's shape so the two sweeps
    read the same way in a log or an operator tool."""

    def __init__(self) -> None:
        self.started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        self.vps_considered = 0
        self.orphans: List[Dict[str, Any]] = []
        self.never_paired: List[Dict[str, Any]] = []
        self.errors: List[str] = []

    def as_dict(self) -> Dict[str, Any]:
        return {
            "started_at": self.started_at,
            "vps_considered": self.vps_considered,
            "orphans": self.orphans,
            "never_paired": self.never_paired,
            "errors": self.errors,
        }


def _list_workspace_registrations(workspace_id: str, tenant_id: str) -> List[Dict[str, Any]]:
    # Imported at call time: gateway_state_repository is the SQLite-backed
    # store, and the pure helpers above are imported by the revoke route on a
    # path that has no reason to touch it.
    from server_modules import gateway_state_repository

    return gateway_state_repository.list_workspace_gateway_registrations(
        workspace_id,
        tenant_id=tenant_id or None,
        # A revoked registration IS the signal — see classify_agent_computer_orphan.
        include_revoked=True,
    )


async def run_agent_computer_orphan_sweep() -> AgentComputerOrphanSweepResult:
    """One read-only pass over every ACTIVE Agent Computer record, asking of
    each whether anything still points at it.

    This is the "read-only orphan report"
    agent_computers_repository.list_active_vps_for_metering's own docstring
    has named since MAN-134 and which nothing had ever built — the candidate
    set is identical (an active record is exactly a resource we believe is
    running), so it reuses that reader rather than adding a second
    cross-tenant scan of the same table.

    Never raises: one workspace's unreadable gateway state must not cost the
    report every other workspace's answer.
    """
    result = AgentComputerOrphanSweepResult()
    if not orphan_sweep_enabled():
        LOGGER.info(
            "Agent Computer orphan sweep skipped: EMPYRALIS_AGENT_COMPUTER_ORPHAN_SWEEP_ENABLED is off."
        )
        return result
    try:
        records = await agent_computers_repository.list_active_vps_for_metering()
    except Exception:
        LOGGER.exception("agent_computer_orphan: failed to list active VPS records")
        result.errors.append("list_active_vps_failed")
        return result

    result.vps_considered = len(records)
    registration_cache: Dict[tuple[str, str], List[Dict[str, Any]]] = {}
    unreadable_scopes: set[tuple[str, str]] = set()
    for record in records:
        workspace_id = _text(record.get("workspace_id"))
        tenant_id = _text(record.get("tenant_id"))
        key = (workspace_id, tenant_id)
        if key not in registration_cache and key not in unreadable_scopes:
            try:
                registration_cache[key] = await asyncio.to_thread(
                    _list_workspace_registrations, workspace_id, tenant_id
                )
            except Exception:
                LOGGER.exception(
                    "agent_computer_orphan: could not list gateway registrations for workspace_id=%s",
                    workspace_id,
                )
                result.errors.append(f"registrations_unreadable:{workspace_id}")
                unreadable_scopes.add(key)
        if key in unreadable_scopes:
            # A workspace whose registrations cannot be read must NOT be
            # reported as "nothing points at this box" — that is exactly the
            # empty-vs-could-not-load collapse. It is counted in
            # vps_considered and named in errors, and classified as nothing.
            continue
        verdict = classify_agent_computer_orphan(
            vps_record=record, registrations=registration_cache[key]
        )
        if verdict.get("verdict") == VERDICT_ORPHANED:
            result.orphans.append(verdict)
        elif verdict.get("verdict") == VERDICT_NEVER_PAIRED:
            result.never_paired.append(verdict)

    if result.orphans:
        LOGGER.error(
            "agent_computer_orphan: %d Agent Computer(s) are still running and billing with every "
            "Gateway registration bound to them revoked: %s. Nothing has been destroyed — tearing one "
            "down is DELETE /api/hardware/vps/{vps_id}, an owner action.",
            len(result.orphans),
            [
                {
                    "vps_id": item.get("vps_id"),
                    "provider": item.get("provider"),
                    "provider_resource_id": item.get("provider_resource_id"),
                    "size": item.get("size"),
                    "workspace_id": item.get("workspace_id"),
                }
                for item in result.orphans
            ],
        )
    return result


async def agent_computer_orphan_loop() -> None:
    """Periodic background task, wired from shared.py's app_lifespan exactly
    like agent_computer_metering_loop — a failed sweep must never kill the
    loop, and the interval is re-read from the env on every tick so it can
    be retuned in production without a restart."""
    while True:
        try:
            await run_agent_computer_orphan_sweep()
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Agent Computer orphan sweep failed")
        await asyncio.sleep(max(60, orphan_sweep_interval_seconds()))
