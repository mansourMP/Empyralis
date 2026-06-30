from pathlib import Path
import sqlite3
from unittest.mock import patch

import pytest

from server_modules import gateway_state_repository


def test_gateway_action_approval_create_blocks_before_sqlite_mutation() -> None:
    with patch.object(
        gateway_state_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            gateway_state_repository.create_gateway_action_approval(
                gateway_id="gateway-1",
                device_id="device-1",
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                user_id="user-1",
                capability_id="browser.open",
                run_id="run-1",
                db_path=Path("/tmp/not-used-gateway-state.sqlite3"),
            )

    command, payload = kernel.call_args.args
    assert command == "gateway-state-decision"
    assert payload["operation"] == "create_approval"
    assert payload["tenant_id"] == "tenant-1"
    assert payload["workspace_id"] == "workspace-1"
    assert payload["gateway_id"] == "gateway-1"
    assert payload["run_id"] == "run-1"
    assert payload["capability_id"] == "browser.open"


def test_gateway_action_approval_create_wrong_next_action_blocks_before_sqlite_mutation() -> None:
    with patch.object(
        gateway_state_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        return_value={"ok": True, "decision": "allow", "next_action": "upsert_gateway_browser_session"},
    ):
        with pytest.raises(RuntimeError, match="unexpected_next_action:upsert_gateway_browser_session"):
            gateway_state_repository.create_gateway_action_approval(
                gateway_id="gateway-1",
                device_id="device-1",
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                user_id="user-1",
                capability_id="browser.open",
                run_id="run-1",
                db_path=Path("/tmp/not-used-gateway-state.sqlite3"),
            )


def test_gateway_browser_session_upsert_blocks_before_sqlite_mutation() -> None:
    with patch.object(
        gateway_state_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            gateway_state_repository.upsert_gateway_browser_session(
                gateway_id="gateway-1",
                device_id="device-1",
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                user_id="user-1",
                run_id="run-1",
                browser_session_id="browser-session-1",
                db_path=Path("/tmp/not-used-gateway-state.sqlite3"),
            )

    command, payload = kernel.call_args.args
    assert command == "gateway-state-decision"
    assert payload["operation"] == "update_browser_session"
    assert payload["browser_session_id"] == "browser-session-1"
    assert payload["run_id"] == "run-1"
    assert payload["status"] == "active"
    assert payload["takeover_confirmed"] is True


def test_gateway_browser_session_upsert_wrong_next_action_blocks_before_sqlite_mutation() -> None:
    with patch.object(
        gateway_state_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        return_value={"ok": True, "decision": "allow", "next_action": "create_gateway_action_approval"},
    ):
        with pytest.raises(RuntimeError, match="unexpected_next_action:create_gateway_action_approval"):
            gateway_state_repository.upsert_gateway_browser_session(
                gateway_id="gateway-1",
                device_id="device-1",
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                user_id="user-1",
                run_id="run-1",
                browser_session_id="browser-session-1",
                db_path=Path("/tmp/not-used-gateway-state.sqlite3"),
            )


def test_gateway_pairing_intent_create_blocks_before_sqlite_mutation() -> None:
    with patch.object(
        gateway_state_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            gateway_state_repository.create_pairing_intent(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                user_id="user-1",
                ttl_seconds=300,
                db_path=Path("/tmp/not-used-gateway-pairing.sqlite3"),
            )

    command, payload = kernel.call_args.args
    assert command == "gateway-state-decision"
    assert payload["operation"] == "create_pairing_intent"
    assert payload["tenant_id"] == "tenant-1"
    assert payload["workspace_id"] == "workspace-1"
    assert payload["user_id"] == "user-1"
    assert payload["ttl_seconds"] == 300


def test_gateway_register_from_pairing_blocks_before_registration_mutation(tmp_path: Path) -> None:
    db_path = tmp_path / "gateway-state.sqlite3"

    with patch.object(
        gateway_state_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        return_value={"ok": True, "decision": "allow"},
    ):
        pairing = gateway_state_repository.create_pairing_intent(
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            user_id="user-1",
            ttl_seconds=300,
            db_path=db_path,
        )

    with patch.object(
        gateway_state_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            gateway_state_repository.register_gateway_from_pairing(
                pairing_token=str(pairing["pairing_token"]),
                device_id="device-1",
                gateway_id="gateway-1",
                db_path=db_path,
            )

    command, payload = kernel.call_args.args
    assert command == "gateway-state-decision"
    assert payload["operation"] == "register_gateway"
    assert payload["tenant_id"] == "tenant-1"
    assert payload["workspace_id"] == "workspace-1"
    assert payload["user_id"] == "user-1"
    assert payload["device_id"] == "device-1"
    assert payload["gateway_id"] == "gateway-1"
    assert payload["token_valid"] is True


def test_gateway_identity_sync_blocks_before_registration_mutation(tmp_path: Path) -> None:
    db_path = tmp_path / "gateway-state.sqlite3"

    with patch.object(
        gateway_state_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        return_value={"ok": True, "decision": "allow"},
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
            db_path=db_path,
        )

    with patch.object(
        gateway_state_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            gateway_state_repository.sync_gateway_registration_identity(
                gateway_id=str(registration["gateway_id"]),
                tenant_id="tenant-2",
                workspace_id="workspace-2",
                user_id="user-2",
                device_id="device-2",
                metadata={"note": "identity-sync"},
                db_path=db_path,
            )

    command, payload = kernel.call_args.args
    assert command == "gateway-state-decision"
    assert payload["operation"] == "update_registration_state"
    assert payload["gateway_id"] == "gateway-1"
    assert payload["registration_status"] == "active"
    assert payload["next_status"] == "active"
    assert payload["next_tenant_id"] == "tenant-2"
    assert payload["next_workspace_id"] == "workspace-2"
    assert payload["next_user_id"] == "user-2"
    assert payload["next_device_id"] == "device-2"


def test_gateway_stale_session_sweep_noop_skips_mutation(tmp_path: Path) -> None:
    db_path = tmp_path / "gateway-state.sqlite3"

    def _allow_gateway_state(command: str, payload: dict[str, object]) -> dict[str, object]:
        assert command == "gateway-state-decision"
        operation = payload.get("operation")
        next_action = {
            "create_pairing_intent": "create_pairing_intent",
            "expire_pairing_intent": "mark_pairing_intent_expired",
            "register_gateway": "consume_pairing_and_register_gateway",
            "issue_session": "issue_gateway_session",
            "sweep_stale_sessions": "noop",
        }[str(operation)]
        return {"ok": True, "decision": "allow", "operation": operation, "next_action": next_action}

    with patch.object(
        gateway_state_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=_allow_gateway_state,
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
            db_path=db_path,
        )
        session = gateway_state_repository.issue_gateway_session(
            gateway_id="gateway-1",
            gateway_token=str(registration["gateway_token"]),
            ttl_seconds=300,
            db_path=db_path,
        )
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE gateway_sessions SET status = 'connected', updated_at = '2000-01-01T00:00:00+00:00' WHERE session_id = ?",
                (str(session["session_id"]),),
            )
            conn.commit()

        swept = gateway_state_repository.sweep_stale_gateway_sessions(
            "gateway-1",
            stale_threshold_seconds=1,
            db_path=db_path,
        )

    assert swept == 0
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT status FROM gateway_sessions WHERE session_id = ?",
            (str(session["session_id"]),),
        ).fetchone()
    assert row is not None
    assert row[0] == "connected"


def test_gateway_stale_session_sweep_wrong_next_action_blocks_before_mutation(tmp_path: Path) -> None:
    db_path = tmp_path / "gateway-state.sqlite3"

    def _allow_then_wrong_sweep(command: str, payload: dict[str, object]) -> dict[str, object]:
        assert command == "gateway-state-decision"
        operation = str(payload.get("operation"))
        if operation == "sweep_stale_sessions":
            return {"ok": True, "decision": "allow", "operation": operation, "next_action": "issue_gateway_session"}
        next_action = {
            "create_pairing_intent": "create_pairing_intent",
            "expire_pairing_intent": "mark_pairing_intent_expired",
            "register_gateway": "consume_pairing_and_register_gateway",
            "issue_session": "issue_gateway_session",
        }[operation]
        return {"ok": True, "decision": "allow", "operation": operation, "next_action": next_action}

    with patch.object(
        gateway_state_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=_allow_then_wrong_sweep,
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
            db_path=db_path,
        )
        session = gateway_state_repository.issue_gateway_session(
            gateway_id="gateway-1",
            gateway_token=str(registration["gateway_token"]),
            ttl_seconds=300,
            db_path=db_path,
        )
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE gateway_sessions SET status = 'connected', updated_at = '2000-01-01T00:00:00+00:00' WHERE session_id = ?",
                (str(session["session_id"]),),
            )
            conn.commit()

        with pytest.raises(RuntimeError, match="unexpected_next_action:issue_gateway_session"):
            gateway_state_repository.sweep_stale_gateway_sessions(
                "gateway-1",
                stale_threshold_seconds=1,
                db_path=db_path,
            )

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT status FROM gateway_sessions WHERE session_id = ?",
            (str(session["session_id"]),),
        ).fetchone()
    assert row is not None
    assert row[0] == "connected"
