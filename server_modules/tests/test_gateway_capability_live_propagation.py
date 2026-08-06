"""MAN-313: a paired gateway's execution-authorization check used to read a
STATIC `capabilities` array captured once from the gateway's initial `hello`
(gateway.connect) handshake, never the live per-heartbeat `capability_
readiness` structure the gateway already sends on every heartbeat tick
(empyralis-gateway/src/cloud/heartbeat-payload.ts). Reproduced live: pair a
gateway while Docker Desktop is not running (agent correctly refuses,
`gateway_capability_missing`) -> start Docker -> retry through the still-
connected gateway across several heartbeat cycles, well past the 60s passive-
inventory cache TTL -> it fails identically, shell.execute still absent from
the authorized list -> restart the gateway daemon (forcing a fresh hello) ->
capability immediately appears.

Root cause: gateway_protocol_service.py's gateway.heartbeat handler only ever
persists capability_readiness onto the CURRENT gateway_sessions row (via
touch_gateway_session) — gateway_registrations.metadata.capability_readiness
is only refreshed by the much rarer gateway.state.update frame, and
gateway_registrations.capabilities (the static list) is only refreshed by
gateway.connect. gateway_registration_execution_readiness() read only those
two registration-scoped signals, so a capability that only became available
mid-connection stayed invisible until a reconnect forced a fresh hello.

The fix: gateway_registry_service.gateway_registration_public_payload() now
folds the live gateway_sessions row's capability_readiness into the metadata
it returns (same "prefer the live session" precedence already used for
reported_health_state), and gateway_execution_service._has_gateway_capability
falls back to capability_readiness.requested (live) when the static
capabilities list (hello-time snapshot) doesn't have the capability yet. This
test exercises the real gateway_state_repository + gateway_registry_service +
gateway_execution_service stack (only the in-memory live-connection registry
and the rust-kernel subprocess are stubbed) to prove a capability that
becomes ready between heartbeats is authorized without a reconnect — and
that the reverse (a capability going away) still deauthorizes."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from server_modules import gateway_execution_service
from server_modules import gateway_state_repository


# Mirrors gateway_state_repository._enforce_gateway_state_decision's own
# operation -> expected-next_action map (not importable — it's a local var
# inside that function), so this mock satisfies whichever internal operation
# each call actually triggers. Copied from test_gateway_capability_refresh.py.
_NEXT_ACTION_BY_OPERATION = {
    "create_pairing_intent": "create_pairing_intent",
    "expire_pairing_intent": "mark_pairing_intent_expired",
    "register_gateway": "consume_pairing_and_register_gateway",
    "issue_session": "issue_gateway_session",
    "validate_session": "validate_gateway_session",
    "mark_session_connected": "mark_gateway_session_connected",
    "mark_session_disconnected": "mark_gateway_session_disconnected",
    "touch_session": "touch_gateway_session",
    "rotate_token": "rotate_gateway_token",
    "revoke_registration": "revoke_gateway_registration",
    "update_registration_state": "update_gateway_registration_state",
    "record_event": "record_gateway_event",
    "create_approval": "create_gateway_action_approval",
    "resolve_approval": "resolve_gateway_action_approval_atomic",
    "update_browser_session": "upsert_gateway_browser_session",
    "summarize_outbox": "summarize_gateway_outbox",
    "sweep_stale_sessions": "sweep_stale_gateway_sessions",
}


def _rust_allow(command: str, payload: dict) -> dict:
    operation = payload.get("operation")
    return {
        "ok": True,
        "decision": "allow",
        "next_action": _NEXT_ACTION_BY_OPERATION.get(operation, operation),
    }


def _pair_and_connect_gateway(db_path: Path, *, requested_capabilities: list[str]) -> str:
    """Mirrors the real gateway.connect handshake: create a pairing intent,
    register the gateway declaring exactly what it can route AT HELLO TIME
    (e.g. Docker not ready yet -> shell.execute absent), then issue + connect
    a session — exactly the gateway_state_repository call shape gateway_
    protocol_service.py's connect handler uses. Returns the session_id."""
    with patch.object(
        gateway_state_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=_rust_allow,
    ):
        pairing = gateway_state_repository.create_pairing_intent(
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            user_id="user-1",
            ttl_seconds=300,
            db_path=db_path,
        )
        registration = gateway_state_repository.register_gateway_from_pairing(
            pairing_token=str(pairing["pairing_token"]),
            device_id="device-1",
            gateway_id="gateway-1",
            capabilities=list(requested_capabilities),
            db_path=db_path,
        )
        session = gateway_state_repository.issue_gateway_session(
            gateway_id="gateway-1",
            gateway_token=registration["gateway_token"],
            ttl_seconds=900,
            db_path=db_path,
        )
        gateway_state_repository.mark_gateway_session_connected(
            session["session_id"], db_path=db_path,
        )
    return session["session_id"]


def _heartbeat(db_path: Path, *, session_id: str, capability_readiness: dict) -> None:
    """Mirrors gateway_protocol_service.py's gateway.heartbeat branch: the
    live capability_readiness a heartbeat frame carries is persisted onto the
    CURRENT gateway_sessions row via touch_gateway_session — this is the one
    call the real heartbeat handler makes on every single heartbeat tick,
    with no reconnect involved."""
    with patch.object(
        gateway_state_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=_rust_allow,
    ):
        gateway_state_repository.touch_gateway_session(
            session_id=session_id,
            gateway_id="gateway-1",
            seq=1,
            ack=1,
            metadata={"capability_readiness": capability_readiness},
            ttl_seconds=900,
            db_path=db_path,
        )


def _patched_get_latest_gateway_session(db_path: Path):
    """gateway_registry_service._gateway_connection_payload() calls
    gateway_state_repository.get_latest_gateway_session() with no db_path
    (production always uses the single real on-disk DB); route it at the
    module-attribute level to this test's tmp_path DB instead, so every
    caller (gateway_registry_service included) sees the same isolated
    database without needing its own db_path parameter."""
    real = gateway_state_repository.get_latest_gateway_session

    def _wrapped(gateway_id, **kwargs):
        kwargs.setdefault("db_path", db_path)
        return real(gateway_id, **kwargs)

    return _wrapped


def test_capability_that_becomes_ready_mid_connection_is_authorized_without_reconnect(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "gateway-state.sqlite3"

    # Hello time: Docker isn't running yet, so the gateway build never
    # claimed shell.execute in its connect-time requested_capabilities.
    session_id = _pair_and_connect_gateway(
        db_path, requested_capabilities=["browser.session.start"],
    )
    registration = gateway_state_repository.get_gateway_registration("gateway-1", db_path=db_path)
    assert registration is not None

    with (
        patch(
            "server_modules.gateway_state_repository.get_latest_gateway_session",
            side_effect=_patched_get_latest_gateway_session(db_path),
        ),
        patch(
            "server_modules.gateway_execution_service.gateway_protocol_service.gateway_connection_is_live",
            return_value=True,
        ),
    ):
        # Confirms the exact reported failure mode before any heartbeat has
        # reported readiness: refused, with the exact reason code from the
        # live incident (gateway_capability_missing).
        ready, reason = gateway_execution_service.gateway_registration_execution_readiness(
            registration, workspace_id="workspace-1", capability_id="shell.execute",
        )
        assert (ready, reason) == (False, "gateway_capability_missing")

        # Docker starts. The gateway's very next heartbeat — same session,
        # same connection, no reconnect — reports shell.execute as both
        # requested and ready.
        _heartbeat(
            db_path,
            session_id=session_id,
            capability_readiness={
                "requested": ["browser.session.start", "shell.execute"],
                "ready": ["shell.execute"],
            },
        )

        # Re-check with the SAME (unrefreshed) registration dict passed in
        # both times — proving the live session data, not a re-fetched
        # registration, is what flips the answer.
        ready, reason = gateway_execution_service.gateway_registration_execution_readiness(
            registration, workspace_id="workspace-1", capability_id="shell.execute",
        )
        assert (ready, reason) == (True, "")

        # Reverse: Docker stops again on a later heartbeat. The capability
        # must be deauthorized immediately too — no lingering authorization
        # from the previous heartbeat's readiness.
        _heartbeat(
            db_path,
            session_id=session_id,
            capability_readiness={
                "requested": ["browser.session.start", "shell.execute"],
                "blocked": ["shell.execute"],
            },
        )
        ready, reason = gateway_execution_service.gateway_registration_execution_readiness(
            registration, workspace_id="workspace-1", capability_id="shell.execute",
        )
        assert (ready, reason) == (False, "gateway_capability_not_ready")
