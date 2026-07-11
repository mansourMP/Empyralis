"""A Gateway's declared capabilities used to be frozen at initial pairing
time — nothing ever refreshed gateway_registrations.capabilities afterward,
so a Gateway build that added a new capability (e.g. the
channel.*.personal.disconnect capabilities) stayed permanently invisible to
the backend until an operator hand-patched the SQLite row directly or the
gateway was fully re-paired. This proves the real fix: the gateway.connect
handshake now refreshes capabilities via update_gateway_registration_state's
new `capabilities` parameter, and a capability absent at pairing time becomes
callable after a normal reconnect — no manual patch, no re-pair."""

from pathlib import Path
from unittest.mock import patch

from server_modules import gateway_state_repository
from server_modules import gateway_inventory_service

# Mirrors gateway_state_repository._enforce_gateway_state_decision's own
# operation -> expected-next_action map (not importable — it's a local var),
# so this mock satisfies whichever internal operation each call actually
# triggers (e.g. create_pairing_intent() also expires stale intents as
# routine upkeep, which fires its own "expire_pairing_intent" check).
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


def test_reconnect_refreshes_capabilities_without_manual_patch_or_repair(tmp_path: Path) -> None:
    db_path = tmp_path / "gateway-state.sqlite3"

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
        # Original pairing: an older Gateway build that doesn't know about
        # the disconnect capability yet.
        registration = gateway_state_repository.register_gateway_from_pairing(
            pairing_token=str(pairing["pairing_token"]),
            device_id="device-1",
            gateway_id="gateway-1",
            capabilities=["channel.telegram.personal.configure"],
            db_path=db_path,
        )
        assert not gateway_inventory_service.registration_has_execution_capability(
            registration, "channel.telegram.personal.disconnect"
        )

        # A reconnect on updated Gateway code declares the new capability —
        # this is the gateway.connect handshake's real call shape.
        updated = gateway_state_repository.update_gateway_registration_state(
            gateway_id="gateway-1",
            capabilities=[
                "channel.telegram.personal.configure",
                "channel.telegram.personal.disconnect",
            ],
            db_path=db_path,
        )

    assert gateway_inventory_service.registration_has_execution_capability(
        updated, "channel.telegram.personal.disconnect"
    )
    # The pre-existing capability must survive too — this is a refresh from
    # the live declaration, not an accidental narrowing.
    assert gateway_inventory_service.registration_has_execution_capability(
        updated, "channel.telegram.personal.configure"
    )


def test_update_without_capabilities_argument_leaves_existing_list_untouched(tmp_path: Path) -> None:
    """The overwhelming majority of update_gateway_registration_state() callers
    (device-trust changes, metadata bumps, status transitions) don't know or
    care about capabilities — they must not silently wipe them by omission."""
    db_path = tmp_path / "gateway-state.sqlite3"

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
        gateway_state_repository.register_gateway_from_pairing(
            pairing_token=str(pairing["pairing_token"]),
            device_id="device-1",
            gateway_id="gateway-1",
            capabilities=["channel.whatsapp.personal.configure"],
            db_path=db_path,
        )

        updated = gateway_state_repository.update_gateway_registration_state(
            gateway_id="gateway-1",
            metadata={"unrelated_field": "value"},
            db_path=db_path,
        )

    assert gateway_inventory_service.registration_has_execution_capability(
        updated, "channel.whatsapp.personal.configure"
    )


def test_normalize_gateway_capabilities_bounds_untrusted_wire_input() -> None:
    normalize = gateway_state_repository._normalize_gateway_capabilities

    assert normalize(None) is None
    assert normalize("not-a-list") == []
    assert normalize(["a", "a", "", None, "b"]) == ["a", "b"]

    oversized = [f"cap-{i}" for i in range(gateway_state_repository.MAX_GATEWAY_CAPABILITY_COUNT + 50)]
    assert len(normalize(oversized)) == gateway_state_repository.MAX_GATEWAY_CAPABILITY_COUNT

    long_id = "x" * 500
    assert len(normalize([long_id])[0]) == gateway_state_repository.MAX_GATEWAY_CAPABILITY_ID_LENGTH
