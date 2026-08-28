"""Route-level tests for PATCH/GET .../group-policy (server_modules/
routes_personal_channels.py), added 2026-08-07 alongside
personal_channels_service.update_agent_group_policy_config /
_persist_agent_group_policy_config finally getting a real caller — see
the channel-gateway hardening §4/§5. Unit coverage of the gate itself already
lives in test_personal_channels_group_policy.py; this file proves the
HTTP surface: auth/ownership enforcement through a REAL FastAPI TestClient
(not just a code read), that a write actually persists, and that a fresh
(never-configured) binding reads back with the new safe defaults.

_FakeAgentInstallStore / _patch_agent_install_store are copied from
test_personal_channels_group_policy.py (same install_metadata contract,
same reason for copying rather than importing: keeping each test file's
fixtures self-contained and independently readable).

Channel key is `openclaw_signal` throughout, not telegram (repointed
2026-08-20, feat/seamless-telegram-setup): telegram is no longer an
OpenClaw-active personal-gateway channel — see openclaw_channel_registry.py's
own "CORRECTION, 2026-08-20" comment. This file is about the generic
group-policy ROUTE, which any live OpenClaw-transported channel exercises
identically.
"""

from __future__ import annotations

import importlib
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


class _FakeAgentInstallStore:
    def __init__(self) -> None:
        self.installs: Dict[str, Dict[str, Any]] = {}

    async def get_bundle(self, agent_id: str, *, tenant_id: str, workspace_id: str):
        if agent_id not in self.installs:
            return None
        meta = self.installs.get(agent_id, {})
        return {"id": agent_id, "install_metadata": dict(meta)}

    async def update(self, agent_id: str, *, tenant_id: str, workspace_id: str, metadata=None, **_kwargs):
        if agent_id not in self.installs:
            return None
        existing = self.installs.get(agent_id, {})
        merged = {**existing, **(metadata or {})}
        self.installs[agent_id] = merged
        return {"id": agent_id, "install_metadata": dict(merged)}


def _patch_agent_install_store(store: "_FakeAgentInstallStore"):
    return patch.multiple(
        "server_modules.agent_registry_repository",
        get_workspace_agent_install_bundle=AsyncMock(side_effect=store.get_bundle),
        update_workspace_agent_install=AsyncMock(side_effect=store.update),
    )


class RoutesPersonalChannelsGroupPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        global gateway_state_repository, routes_personal_channels, personal_channels_repository

        gateway_state_repository = importlib.import_module("server_modules.gateway_state_repository")
        routes_personal_channels = importlib.import_module("server_modules.routes_personal_channels")
        personal_channels_repository = importlib.import_module("server_modules.personal_channels_repository")

        self.tmpdir = tempfile.TemporaryDirectory()
        self.gateway_db_path = Path(self.tmpdir.name) / "gateway-state.sqlite3"
        gateway_state_repository.init_gateway_state_db(self.gateway_db_path)
        self.db_patcher = patch.object(gateway_state_repository, "GATEWAY_STATE_DB_FILE", self.gateway_db_path)
        self.db_patcher.start()

        self.store = _FakeAgentInstallStore()
        # The only agent this build's write path can actually reach:
        # telegram_personal/whatsapp_personal have a resolved per-agent
        # identity. "agent-owner-a" has an install bundle (so persist
        # succeeds); it starts with NO group_policy entry at all, matching
        # every real agent's actual state today (nothing could ever write
        # one before this build).
        self.store.installs["agent-owner-a"] = {}
        self.install_patcher = _patch_agent_install_store(self.store)
        self.install_patcher.start()

        self.app = FastAPI()
        self.app.include_router(routes_personal_channels.router, prefix="/api")
        self.app.dependency_overrides[routes_personal_channels.require_api_key] = self._current_user_override
        self.client = TestClient(self.app)

        self._active_user: Dict[str, Any] = self._owner_a_user()

        # Seed a gateway registration owned by user-a in workspace ws-a.
        pairing = gateway_state_repository.create_pairing_intent(
            tenant_id="tenant-a",
            workspace_id="ws-a",
            user_id="user-a",
            ttl_seconds=600,
            display_name="Owner A's Mac",
            platform="macos",
        )
        registration = gateway_state_repository.register_gateway_from_pairing(
            pairing_token=pairing["pairing_token"],
            device_id="device-a-1",
            display_name="Owner A's Mac",
            platform="macos-arm64",
            capabilities=[],
        )
        self.gateway_id = registration["gateway_id"]

    def tearDown(self) -> None:
        self.client.close()
        self.app.dependency_overrides.clear()
        self.install_patcher.stop()
        self.db_patcher.stop()
        self.tmpdir.cleanup()

    # -- current_user fixtures -------------------------------------------------

    def _current_user_override(self):
        return self._active_user

    @staticmethod
    def _owner_a_user() -> Dict[str, Any]:
        return {
            "auth_type": "bearer",
            "is_admin": False,
            "user_id": "user-a",
            "role": "owner",
            "workspace_roles": {"ws-a": "owner"},
            "workspace_access": {
                "ws-a": {
                    "workspace_id": "ws-a",
                    "tenant_id": "tenant-a",
                    "role": "owner",
                    "tenant_role": "owner",
                }
            },
        }

    @staticmethod
    def _attacker_user_b() -> Dict[str, Any]:
        """A real, authenticated user of a COMPLETELY DIFFERENT workspace
        and tenant — has zero entry for ws-a in their workspace_access map,
        so allowed_workspace_ids(...) resolves to {"ws-b"} only and
        enforce_workspace_access must reject "ws-a" outright."""
        return {
            "auth_type": "bearer",
            "is_admin": False,
            "user_id": "user-b",
            "role": "owner",
            "workspace_roles": {"ws-b": "owner"},
            "workspace_access": {
                "ws-b": {
                    "workspace_id": "ws-b",
                    "tenant_id": "tenant-b",
                    "role": "owner",
                    "tenant_role": "owner",
                }
            },
        }

    @staticmethod
    def _same_workspace_other_user() -> Dict[str, Any]:
        """A different person who DOES have member access to ws-a, but is
        not THIS gateway's paired owner (owner_user_id == "user-a") — the
        second, narrower check inside _require_accessible_gateway_registration."""
        return {
            "auth_type": "bearer",
            "is_admin": False,
            "user_id": "user-a-colleague",
            "role": "member",
            "workspace_roles": {"ws-a": "member"},
            "workspace_access": {
                "ws-a": {
                    "workspace_id": "ws-a",
                    "tenant_id": "tenant-a",
                    "role": "member",
                    "tenant_role": "member",
                }
            },
        }

    # -- tests -------------------------------------------------------------

    def test_patch_persists_and_get_reflects_it(self) -> None:
        response = self.client.patch(
            f"/api/personal-channels/openclaw_signal/gateways/{self.gateway_id}/group-policy",
            params={"agent_id": "agent-owner-a"},
            json={"mode": "allowlist", "allowlist": ["-100555", "-100777"], "require_mention": True},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["group_policy"]["mode"], "allowlist")
        self.assertEqual(sorted(body["group_policy"]["allowlist"]), ["-100555", "-100777"])
        self.assertTrue(body["group_policy"]["require_mention"])

        # Proves an ACTUAL write happened against the underlying store, not
        # just a 200 with a fabricated response body.
        persisted = self.store.installs["agent-owner-a"]["group_policy"]["openclaw_signal"]
        self.assertEqual(persisted["mode"], "allowlist")
        self.assertEqual(sorted(persisted["allowlist"]), ["-100555", "-100777"])

        get_response = self.client.get(
            f"/api/personal-channels/openclaw_signal/gateways/{self.gateway_id}/group-policy",
            params={"agent_id": "agent-owner-a"},
        )
        self.assertEqual(get_response.status_code, 200, get_response.text)
        self.assertEqual(get_response.json()["group_policy"], body["group_policy"])

    def test_unconfigured_new_binding_reads_back_the_new_safe_defaults(self) -> None:
        """No PATCH has ever been called for this agent+channel — proves
        the DEFAULT_GROUP_POLICY_MODE/DEFAULT_REQUIRE_MENTION flip is live
        end-to-end through the route, not just at the unit level."""
        response = self.client.get(
            f"/api/personal-channels/openclaw_signal/gateways/{self.gateway_id}/group-policy",
            params={"agent_id": "agent-owner-a"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        config = response.json()["group_policy"]
        self.assertEqual(config["mode"], "allowlist")
        self.assertTrue(config["require_mention"])
        self.assertEqual(config["allowlist"], [])

    def test_patch_rejects_an_invalid_mode_with_400_and_does_not_write_anything(self) -> None:
        response = self.client.patch(
            f"/api/personal-channels/openclaw_signal/gateways/{self.gateway_id}/group-policy",
            params={"agent_id": "agent-owner-a"},
            json={"mode": "totally-not-a-real-mode"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("group_policy", self.store.installs["agent-owner-a"])

    def test_patch_requires_agent_id(self) -> None:
        response = self.client.patch(
            f"/api/personal-channels/openclaw_signal/gateways/{self.gateway_id}/group-policy",
            json={"mode": "allowlist"},
        )
        self.assertEqual(response.status_code, 400)

    def test_patch_rejects_unknown_channel_key_with_404(self) -> None:
        response = self.client.patch(
            f"/api/personal-channels/not_a_real_channel/gateways/{self.gateway_id}/group-policy",
            params={"agent_id": "agent-owner-a"},
            json={"mode": "allowlist"},
        )
        self.assertEqual(response.status_code, 404)

    def test_local_bridge_channel_key_is_accepted_by_the_route_but_persist_fails_for_unresolved_identity(self) -> None:
        """The local-bridge channel keys ARE valid group_policy channel keys
        (GROUP_POLICY_CHANNEL_KEYS), but a write attempted with the
        LEGACY_UNSCOPED_AGENT_ID (empty string) must fail with a real error,
        never silently succeed.

        RETARGETED 2026-08-15: every URL in this file named a key commit
        6b2baf97e (the full OpenClaw cutover, 2026-08-14) deleted. This test
        was the only one that failed loudly (404 instead of 400) because its
        key left GROUP_POLICY_CHANNEL_KEYS; the `telegram_personal` ones went
        on passing because that key is STILL in GROUP_POLICY_CHANNEL_KEYS
        (personal_channels_service.py builds it as
        {WHATSAPP_PERSONAL_CHANNEL_KEY, TELEGRAM_PERSONAL_CHANNEL_KEY,
        *LOCAL_BRIDGE_PERSONAL_CHANNELS}) even though no handler, catalog
        entry or lane spec carries it any more — so the route happily accepted
        and persisted policy for a channel nothing routes. All keys here now
        come from the live registry."""
        response = self.client.patch(
            f"/api/personal-channels/openclaw_signal/gateways/{self.gateway_id}/group-policy",
            params={"agent_id": ""},
            json={"mode": "allowlist"},
        )
        self.assertEqual(response.status_code, 400)

    def test_cross_tenant_user_cannot_write_another_workspaces_gateway_config(self) -> None:
        """REAL cross-tenant check through the live route, not a code
        read: user-b belongs only to ws-b/tenant-b and has never been
        granted access to ws-a, which owns self.gateway_id."""
        self._active_user = self._attacker_user_b()
        response = self.client.patch(
            f"/api/personal-channels/openclaw_signal/gateways/{self.gateway_id}/group-policy",
            params={"agent_id": "agent-owner-a"},
            json={"mode": "open", "require_mention": False},
        )
        self.assertEqual(response.status_code, 403, response.text)
        # No write occurred — the attacker could not flip agent-owner-a's
        # config back to the dangerous open/no-mention-check state.
        self.assertNotIn("group_policy", self.store.installs["agent-owner-a"])

    def test_cross_tenant_user_cannot_read_another_workspaces_gateway_config(self) -> None:
        self._active_user = self._attacker_user_b()
        response = self.client.get(
            f"/api/personal-channels/openclaw_signal/gateways/{self.gateway_id}/group-policy",
            params={"agent_id": "agent-owner-a"},
        )
        self.assertEqual(response.status_code, 403, response.text)

    def test_same_workspace_member_who_is_not_the_paired_owner_is_rejected(self) -> None:
        """Second, narrower ownership check: even a legitimate ws-a member
        cannot reconfigure a gateway paired to a DIFFERENT person's
        account (owner_user_id == "user-a", not "user-a-colleague")."""
        self._active_user = self._same_workspace_other_user()
        response = self.client.patch(
            f"/api/personal-channels/openclaw_signal/gateways/{self.gateway_id}/group-policy",
            params={"agent_id": "agent-owner-a"},
            json={"mode": "open", "require_mention": False},
        )
        self.assertEqual(response.status_code, 403, response.text)
        self.assertNotIn("group_policy", self.store.installs["agent-owner-a"])

    def test_patch_against_an_unknown_gateway_id_is_404(self) -> None:
        response = self.client.patch(
            "/api/personal-channels/openclaw_signal/gateways/gateway_does_not_exist/group-policy",
            params={"agent_id": "agent-owner-a"},
            json={"mode": "allowlist"},
        )
        self.assertEqual(response.status_code, 404)

    def test_patch_against_agent_with_no_install_bundle_is_404(self) -> None:
        response = self.client.patch(
            f"/api/personal-channels/openclaw_signal/gateways/{self.gateway_id}/group-policy",
            params={"agent_id": "agent-that-does-not-exist-in-this-workspace"},
            json={"mode": "allowlist"},
        )
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
