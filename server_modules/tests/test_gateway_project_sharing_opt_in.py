"""MAN-307-adjacent fix: 'Hardware attaches to its owner, never to the
project' (CLAUDE.md) was being violated — projects_repository.
set_project_default_gateway would point a project's agents at ANY gateway
that merely resolved to the workspace (fleet_tools.
gateway_resolves_in_workspace), with no check that the machine's owner ever
consented to sharing it. specialist_runtime_context.
resolve_specialist_runtime_context then handed that gateway to any agent in
the project as a silent tool-dispatch/brain-hosting fallback.

The fix adds a per-machine, owner-only opt-in flag
(gateway_state_repository.set_gateway_project_sharing_opt_in /
gateway_project_sharing_opted_in), stored in the SQLite gateway_registrations
row's `metadata.project_sharing_opt_in` — SQLite is the LIVE store for this
table (see gateway_state_repository.GATEWAY_STATE_DB_FILE and every read/
write in this module going through sqlite3); the Postgres `gateway_
registrations` table the channel-gateway hardening and MAN-307 document is a
stale, unrelated table that nothing in this module reads or writes.

These tests exercise the repository layer directly against a real temp
SQLite DB (same schema-init path production uses,
gateway_state_repository.init_gateway_state_db) — same convention
test_gateway_state_repository_prune.py already established for this module,
since a mock would happily let a scope-check bug slip past unnoticed.
"""

from __future__ import annotations

import importlib
import tempfile
import unittest
from pathlib import Path

from server_modules import gateway_state_repository


def _iso(offset_seconds: int = 0) -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) - timedelta(seconds=offset_seconds)).isoformat()


class GatewayProjectSharingOptInTests(unittest.TestCase):
    def setUp(self) -> None:
        global gateway_state_repository
        gateway_state_repository = importlib.import_module("server_modules.gateway_state_repository")
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "gateway-state.sqlite3"
        gateway_state_repository.init_gateway_state_db(self.db_path)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def _insert_registration(
        self,
        gateway_id: str,
        *,
        tenant_id: str = "t1",
        workspace_id: str = "ws-1",
        user_id: str = "owner-1",
        device_id: str = "dev-1",
        metadata: str = "{}",
    ) -> None:
        conn = gateway_state_repository._connect(self.db_path)
        try:
            ts = _iso()
            conn.execute(
                """
                INSERT INTO gateway_registrations (
                    gateway_id, device_id, tenant_id, workspace_id, user_id,
                    status, device_trust_state, display_name, platform,
                    gateway_token_hash, metadata, capabilities,
                    journal_cursor, checkpoint_cursor, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'active', 'verified', 'Test Box', 'darwin-arm64',
                          'hash', ?, '[]', 0, 0, ?, ?)
                """,
                (gateway_id, device_id, tenant_id, workspace_id, user_id, metadata, ts, ts),
            )
            conn.commit()
        finally:
            conn.close()

    # ---- default-off ----------------------------------------------------

    def test_fresh_registration_is_not_opted_in(self):
        """A registration with no metadata key at all — every registration
        that predates this feature, and every fresh pairing — must read as
        NOT opted in. Deny-by-default, not deny-until-a-migration-runs."""
        self._insert_registration("gw-1")
        self.assertFalse(
            gateway_state_repository.gateway_project_sharing_opted_in("gw-1", db_path=self.db_path)
        )

    def test_unknown_gateway_is_not_opted_in(self):
        self.assertFalse(
            gateway_state_repository.gateway_project_sharing_opted_in("gw-does-not-exist", db_path=self.db_path)
        )

    def test_truthy_but_not_true_value_is_not_opted_in(self):
        """Only the literal boolean True counts — a stray "1"/1/"true"
        string some other writer left behind must not be treated as
        consent."""
        self._insert_registration("gw-1", metadata='{"project_sharing_opt_in": "true"}')
        self.assertFalse(
            gateway_state_repository.gateway_project_sharing_opted_in("gw-1", db_path=self.db_path)
        )

    # ---- the setter: owner-only, scope-checked ---------------------------

    def test_owner_can_opt_in(self):
        self._insert_registration("gw-1", tenant_id="t1", workspace_id="ws-1", user_id="owner-1")
        result = gateway_state_repository.set_gateway_project_sharing_opt_in(
            gateway_id="gw-1", opted_in=True, tenant_id="t1", workspace_id="ws-1",
            user_id="owner-1", db_path=self.db_path,
        )
        self.assertIsNotNone(result)
        self.assertTrue(
            gateway_state_repository.gateway_project_sharing_opted_in("gw-1", db_path=self.db_path)
        )

    def test_owner_can_revoke_after_opting_in(self):
        self._insert_registration("gw-1", user_id="owner-1")
        gateway_state_repository.set_gateway_project_sharing_opt_in(
            gateway_id="gw-1", opted_in=True, tenant_id="t1", workspace_id="ws-1",
            user_id="owner-1", db_path=self.db_path,
        )
        gateway_state_repository.set_gateway_project_sharing_opt_in(
            gateway_id="gw-1", opted_in=False, tenant_id="t1", workspace_id="ws-1",
            user_id="owner-1", db_path=self.db_path,
        )
        self.assertFalse(
            gateway_state_repository.gateway_project_sharing_opted_in("gw-1", db_path=self.db_path)
        )

    def test_non_owner_user_in_same_workspace_cannot_opt_in_someone_elses_machine(self):
        """The law names the hardware's OWNER, not a workspace role, as the
        one who consents — a different user in the SAME tenant/workspace
        (e.g. a workspace admin who isn't this box's paired user) must be
        rejected, not silently succeed."""
        self._insert_registration("gw-1", tenant_id="t1", workspace_id="ws-1", user_id="owner-1")
        result = gateway_state_repository.set_gateway_project_sharing_opt_in(
            gateway_id="gw-1", opted_in=True, tenant_id="t1", workspace_id="ws-1",
            user_id="some-other-user", db_path=self.db_path,
        )
        self.assertIsNone(result)
        self.assertFalse(
            gateway_state_repository.gateway_project_sharing_opted_in("gw-1", db_path=self.db_path)
        )

    def test_wrong_workspace_cannot_opt_in(self):
        self._insert_registration("gw-1", tenant_id="t1", workspace_id="ws-1", user_id="owner-1")
        result = gateway_state_repository.set_gateway_project_sharing_opt_in(
            gateway_id="gw-1", opted_in=True, tenant_id="t1", workspace_id="ws-OTHER",
            user_id="owner-1", db_path=self.db_path,
        )
        self.assertIsNone(result)
        self.assertFalse(
            gateway_state_repository.gateway_project_sharing_opted_in("gw-1", db_path=self.db_path)
        )

    def test_wrong_tenant_cannot_opt_in(self):
        self._insert_registration("gw-1", tenant_id="t1", workspace_id="ws-1", user_id="owner-1")
        result = gateway_state_repository.set_gateway_project_sharing_opt_in(
            gateway_id="gw-1", opted_in=True, tenant_id="t-OTHER", workspace_id="ws-1",
            user_id="owner-1", db_path=self.db_path,
        )
        self.assertIsNone(result)

    def test_missing_user_id_is_rejected(self):
        self._insert_registration("gw-1", user_id="owner-1")
        result = gateway_state_repository.set_gateway_project_sharing_opt_in(
            gateway_id="gw-1", opted_in=True, tenant_id="t1", workspace_id="ws-1",
            user_id="", db_path=self.db_path,
        )
        self.assertIsNone(result)
        self.assertFalse(
            gateway_state_repository.gateway_project_sharing_opted_in("gw-1", db_path=self.db_path)
        )

    def test_unknown_gateway_setter_is_a_noop(self):
        result = gateway_state_repository.set_gateway_project_sharing_opt_in(
            gateway_id="gw-does-not-exist", opted_in=True, tenant_id="t1", workspace_id="ws-1",
            user_id="owner-1", db_path=self.db_path,
        )
        self.assertIsNone(result)

    def test_opt_in_preserves_other_metadata_keys(self):
        """Merge-not-replace: an existing metadata key (e.g. a display
        preference some other writer set) must survive the opt-in write
        untouched — same discipline projects_repository.
        set_project_default_gateway's own docstring calls out for its
        jsonb_set merge."""
        self._insert_registration("gw-1", user_id="owner-1", metadata='{"icon": "server"}')
        gateway_state_repository.set_gateway_project_sharing_opt_in(
            gateway_id="gw-1", opted_in=True, tenant_id="t1", workspace_id="ws-1",
            user_id="owner-1", db_path=self.db_path,
        )
        registration = gateway_state_repository.get_gateway_registration("gw-1", db_path=self.db_path)
        self.assertEqual(registration["metadata"].get("icon"), "server")
        self.assertTrue(registration["metadata"].get("project_sharing_opt_in") is True)


if __name__ == "__main__":
    unittest.main()
