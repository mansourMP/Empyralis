"""Gateway doctor: dispatch for the `gateway.doctor.run` capability
(empyralis-gateway/src/health/gateway-doctor.ts).

Distinct from the pre-existing, read-only `GET /gateway/registrations/
{gateway_id}/doctor` (gateway_health_service.gateway_doctor_payload) — that
endpoint aggregates STORED state (registration/session/heartbeat rows) plus
a few live provider probes, entirely backend-side, and has no repair mode.
This module dispatches a LIVE detect -> (safe) repair -> re-validate pass
that runs *inside* the gateway process itself (cloud connection state,
on-box feature readiness, CLI sign-in, iMessage, restart-supervision
presence — see gateway-doctor.ts's module doc comment for the full check
list and why each is or isn't repairable).

Dispatch shape mirrors gateway_self_update_service.trigger_gateway_self_
update() and cli_setup_service.install_cli_runtime(): a member-gated action
routed through gateway_execution_service.execute_tool_via_gateway() over the
gateway's existing outbound cloud WS / tool-invoke transport — no SSH, no
separate control channel.
"""
from __future__ import annotations

from typing import Any, Dict, Optional
from uuid import uuid4

from server_modules import gateway_execution_service

# Must match GATEWAY_DOCTOR_CAPABILITY in
# empyralis-gateway/src/health/gateway-doctor.ts.
DOCTOR_CAPABILITY = "gateway.doctor.run"

# The check set today (cloud connection, capability readiness, CLI
# subscription, iMessage staged probe, supervisor detection) is a handful of
# already-cached/fast local probes — nowhere near self-update's 600s budget
# for a cold artifact download. Generous headroom over the slowest single
# check (the iMessage staged probe's own DEFAULT_PROBE_TIMEOUT_MS=15s in
# bridges/imsg-imessage-client.ts) covers a cold/uncached run of every check.
DEFAULT_DOCTOR_TIMEOUT_SECONDS = 60


class GatewayDoctorError(RuntimeError):
    """Raised by run_gateway_doctor(); the route translates status_code +
    this message directly into an HTTPException, same pattern as
    GatewaySelfUpdateError (gateway_self_update_service.py)."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _status_code_for_reason(reason: str) -> int:
    r = str(reason or "").strip().lower()
    if "offline" in r or "heartbeat_stale" in r or "unhealthy" in r or "not currently connected" in r:
        return 409
    return 400


async def run_gateway_doctor(
    *,
    gateway_id: str,
    workspace_id: str,
    repair: bool = False,
    actor_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Dispatches gateway.doctor.run over the existing tool-invoke transport.

    repair=False (the default): report-only — every check still runs, but no
    check's repair() is attempted, so nothing on the box changes. repair=True
    lets each check's own conservative, idempotent repair run for anything
    that isn't already passing, then re-validates via a fresh detect() before
    reporting `repaired: true` — see gateway-doctor.ts's runGatewayDoctor()
    for the exact contract; this function trusts the gateway's own result
    verbatim rather than re-interpreting it.
    """
    run_id = f"gateway-doctor-{uuid4().hex[:12]}"
    trace_id = run_id
    try:
        execution = await gateway_execution_service.execute_tool_via_gateway(
            gateway_id=gateway_id,
            capability_id=DOCTOR_CAPABILITY,
            arguments={"repair": bool(repair)},
            run_id=run_id,
            trace_id=trace_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            agent_scope="sage",
            timeout_seconds=DEFAULT_DOCTOR_TIMEOUT_SECONDS,
        )
    except (ValueError, PermissionError) as exc:
        reason = str(exc)
        raise GatewayDoctorError(
            reason,
            status_code=403 if isinstance(exc, PermissionError) else _status_code_for_reason(reason),
        ) from exc

    result = execution.get("result") if isinstance(execution.get("result"), dict) else {}
    return {
        "gateway_id": gateway_id,
        "run_id": run_id,
        "repair_requested": bool(repair),
        **result,
    }
