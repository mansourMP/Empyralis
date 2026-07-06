import json
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from server_modules import gateway_registry_service, gateway_state_repository, rust_runtime_kernel_client


# The repo-wide conftest.py mocks run_runtime_kernel with a bare "allow" that
# has no next_action, which fails gateway_state_repository's stricter
# next_action-must-match-the-operation gate (this reproduces even on
# unrelated, pre-existing tests, e.g. test_gateway_routes.py's
# test_pairing_token_replay_is_rejected — not something introduced here).
# Use a mock that fills in the next_action the real kernel would return for
# each operation, so these tests exercise the actual dedupe/expiry logic.
_NEXT_ACTION_BY_OPERATION = {
    "create_pairing_intent": "create_pairing_intent",
    "expire_pairing_intent": "mark_pairing_intent_expired",
    "register_gateway": "consume_pairing_and_register_gateway",
    "revoke_registration": "revoke_gateway_registration",
}


def _mock_run_runtime_kernel(command: str, payload, timeout_seconds: int = 5):
    operation = str((payload or {}).get("operation") or "").strip()
    return {
        "ok": True,
        "decision": "allow",
        "command": command,
        "next_action": _NEXT_ACTION_BY_OPERATION.get(operation, operation),
        "decision_id": "rkd_test_mock",
        "reason": "mock allow (dedupe test)",
    }


class GatewayRegistrationDedupeTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch.object(
            rust_runtime_kernel_client, "run_runtime_kernel", side_effect=_mock_run_runtime_kernel
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.db_path = Path(self.tmpdir.name) / "gateway-state.sqlite3"
        gateway_state_repository.init_gateway_state_db(self.db_path)
        self.workspace_id = "ws-dedupe-test"

    def _register(self, *, vps_id: str = "", display_name: str = "Test Device") -> str:
        metadata = {"setup_source": "vps", "vps_id": vps_id, "provider": "digitalocean", "region": "sfo3"} if vps_id else {}
        pairing = gateway_state_repository.create_pairing_intent(
            tenant_id="tenant-1",
            workspace_id=self.workspace_id,
            user_id="user-1",
            ttl_seconds=3600,
            display_name=display_name,
            platform="linux",
            metadata=metadata,
            db_path=self.db_path,
        )
        registration = gateway_state_repository.register_gateway_from_pairing(
            pairing_token=pairing["pairing_token"],
            device_id=f"device-{uuid.uuid4().hex}",
            display_name=display_name,
            platform="linux",
            db_path=self.db_path,
        )
        return str(registration["gateway_id"])

    def _set_heartbeat(self, gateway_id: str, *, heartbeat_at: str) -> None:
        with gateway_state_repository._connect(self.db_path) as conn:
            conn.execute(
                "UPDATE gateway_registrations SET last_heartbeat_at = ?, updated_at = ? WHERE gateway_id = ?",
                (heartbeat_at, heartbeat_at, gateway_id),
            )
            conn.commit()

    def _backdate_created_at(self, gateway_id: str, *, created_at: str) -> None:
        with gateway_state_repository._connect(self.db_path) as conn:
            conn.execute(
                "UPDATE gateway_registrations SET created_at = ? WHERE gateway_id = ?",
                (created_at, gateway_id),
            )
            conn.commit()

    def _status(self, gateway_id: str) -> str:
        registration = gateway_state_repository.get_gateway_registration(gateway_id, db_path=self.db_path)
        return str((registration or {}).get("status") or "")

    def test_three_registrations_same_vps_id_collapse_to_one(self) -> None:
        # Mirrors the reported bug: three registration attempts for one physical
        # DigitalOcean box (same vps_id), only the freshest should survive.
        stale_1 = self._register(vps_id="vps_shared_box")
        stale_2 = self._register(vps_id="vps_shared_box")
        live = self._register(vps_id="vps_shared_box")
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        self._set_heartbeat(live, heartbeat_at=now_iso)

        result = gateway_state_repository.dedupe_and_expire_workspace_gateway_registrations(
            self.workspace_id, db_path=self.db_path
        )

        self.assertEqual(sorted(result["revoked_gateway_ids"]), sorted([stale_1, stale_2]))
        self.assertEqual(result["kept_by_vps_id"]["vps_shared_box"], live)
        self.assertEqual(self._status(live), "active")
        self.assertEqual(self._status(stale_1), "revoked")
        self.assertEqual(self._status(stale_2), "revoked")

        remaining = gateway_state_repository.list_workspace_gateway_registrations(
            self.workspace_id, include_revoked=False, db_path=self.db_path
        )
        self.assertEqual([str(item["gateway_id"]) for item in remaining], [live])

    def test_never_connected_registration_expires_past_grace_window(self) -> None:
        abandoned = self._register()
        old_created_at = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
        self._backdate_created_at(abandoned, created_at=old_created_at)

        gateway_state_repository.dedupe_and_expire_workspace_gateway_registrations(
            self.workspace_id, abandoned_grace_seconds=1800, db_path=self.db_path
        )

        self.assertEqual(self._status(abandoned), "revoked")

    def test_fresh_registration_without_heartbeat_is_not_prematurely_expired(self) -> None:
        # A box mid-install has no heartbeat yet but was created moments ago —
        # must not be revoked out from under an in-progress pairing.
        installing = self._register()

        gateway_state_repository.dedupe_and_expire_workspace_gateway_registrations(
            self.workspace_id, abandoned_grace_seconds=1800, db_path=self.db_path
        )

        self.assertEqual(self._status(installing), "active")

    def test_healthy_standalone_registration_is_untouched(self) -> None:
        healthy = self._register()
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        self._set_heartbeat(healthy, heartbeat_at=now_iso)

        result = gateway_state_repository.dedupe_and_expire_workspace_gateway_registrations(
            self.workspace_id, db_path=self.db_path
        )

        self.assertEqual(result["revoked_gateway_ids"], [])
        self.assertEqual(self._status(healthy), "active")


class GatewayHardwareLabelTests(unittest.TestCase):
    def test_cloud_vps_metadata_produces_provider_region_label(self) -> None:
        registration = {
            "metadata": {"setup_source": "vps", "provider": "digitalocean", "region": "sfo3", "vps_id": "vps_1"},
        }
        payload = gateway_registry_service._hardware_presentation(registration["metadata"])
        self.assertEqual(payload["hardware_kind"], "cloud_vps")
        self.assertEqual(payload["hardware_label"], "DigitalOcean · San Francisco 3")

    def test_personal_pairing_has_no_setup_source_and_labels_as_this_device(self) -> None:
        payload = gateway_registry_service._hardware_presentation({})
        self.assertEqual(payload["hardware_kind"], "personal_device")
        self.assertEqual(payload["hardware_label"], "This Device")


if __name__ == "__main__":
    unittest.main()
