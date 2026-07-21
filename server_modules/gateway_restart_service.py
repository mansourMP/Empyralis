"""In-product gateway restart: dispatch for the `gateway.restart` capability
(empyralis-gateway/src/update/gateway-restart-runtime.ts).

gap-hardware-gateway.md Part 1 item 2 — today the only way to restart a
stuck gateway process without destroying the whole VPS is SSH +
`systemctl restart` (the hardware detail page's own guided copy for
applying a new CLAUDE_CODE_OAUTH_TOKEN literally tells the operator to do
that). This module closes that gap the same way gateway_doctor_service.py
and gateway_self_update_service.py already do: a member-gated action routed
through gateway_execution_service.execute_tool_via_gateway() over the
gateway's existing outbound cloud WS / tool-invoke transport — no SSH, no
separate control channel, no artifact download (unlike self-update, this
capability takes no target_version/artifact_url — it re-execs the exact
build already on disk).
"""
from __future__ import annotations

from typing import Any, Dict, Optional
from uuid import uuid4

from server_modules import gateway_execution_service

# Must match GATEWAY_RESTART_CAPABILITY in
# empyralis-gateway/src/update/gateway-restart-runtime.ts.
RESTART_CAPABILITY = "gateway.restart"

# A restart is bounded by the handoff script's own health-grace window
# (DEFAULT_HEALTH_GRACE_MS=8s, gateway-restart-handoff.ts) plus a ~1s
# pre-shutdown delay — nowhere near self-update's 600s download budget.
# Generous headroom over that, same shape as gateway_doctor_service's
# DEFAULT_DOCTOR_TIMEOUT_SECONDS.
DEFAULT_RESTART_TIMEOUT_SECONDS = 60


class GatewayRestartError(RuntimeError):
    """Raised by trigger_gateway_restart(); the route translates status_code
    + this message directly into an HTTPException, same pattern as
    GatewaySelfUpdateError / GatewayDoctorError."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _status_code_for_reason(reason: str) -> int:
    r = str(reason or "").strip().lower()
    if "offline" in r or "heartbeat_stale" in r or "unhealthy" in r or "not currently connected" in r:
        return 409
    return 400


async def trigger_gateway_restart(
    *,
    gateway_id: str,
    workspace_id: str,
    actor_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Dispatches gateway.restart over the existing tool-invoke transport.

    No arguments beyond identity — the gateway-side runtime re-execs its own
    current entrypoint (see gateway-restart-runtime.ts's module doc comment
    for why that needs no target_version/artifact_url from here at all)."""
    run_id = f"gateway-restart-{uuid4().hex[:12]}"
    trace_id = run_id
    try:
        execution = await gateway_execution_service.execute_tool_via_gateway(
            gateway_id=gateway_id,
            capability_id=RESTART_CAPABILITY,
            arguments={},
            run_id=run_id,
            trace_id=trace_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            agent_scope="sage",
            timeout_seconds=DEFAULT_RESTART_TIMEOUT_SECONDS,
        )
    except (ValueError, PermissionError) as exc:
        reason = str(exc)
        raise GatewayRestartError(
            reason,
            status_code=403 if isinstance(exc, PermissionError) else _status_code_for_reason(reason),
        ) from exc

    result = execution.get("result") if isinstance(execution.get("result"), dict) else {}
    return {
        "gateway_id": gateway_id,
        "run_id": run_id,
        **result,
    }
